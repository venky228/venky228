# -*- coding: utf-8 -*-
"""z6_trip_why.py -- WHY DID THIS GENERATOR TRIP?

You give it one or more generator bus numbers. For every fault of the base
case, Scenario 1 and Scenario 2 it finds out whether that unit tripped and WHY,
from three sources:

    the .dyr files     the protection relays that act on the unit and their
                       settings (VTGTPAT / VTGDCAT / FRQTPAT / FRQDCAT, and the
                       older USRMDL VTGTPA / FRQTPA / VTGDCA / FRQDCA), and the
                       unit's own dynamic models
    the PSS/E logs     each fault's own log, <run>\\logs\\psse\\<fault>.txt
                       (kept when the panel has PSSE_FAULT_LOG = True): which
                       relay picked up, the voltage or frequency it saw, when
                       its breaker timer ran out, and PSS/E's
                       "MACHINE ... TRIPPED AT TIME" / "BUS ... DISCONNECTED" lines
    the study scorer   <run>\\reports\\SPP_MEASURE_MACHINES_*.csv, when present:
                       whether the study counted the unit as tripped, and on
                       what evidence (power to zero, terminal voltage collapsed)

and writes ONE TEXT FILE PER UNIT:  <ROOT>\\trip_reasons\\TRIP_REASON_<bus>.txt
(plus TRIP_REASONS.csv with every fault of every unit, for Excel).

Nothing is simulated and nothing in the results folders is changed: the logs,
.dyr files and CSVs are only read.

RUN (any Python 3.4 or later -- PSS/E is not needed):
    python z6_trip_why.py                      asks for the bus number(s)
    python z6_trip_why.py 584713 539114        or give them on the command line
    python z6_trip_why.py 999001:B             bus:machine-id for one machine only
Put the file in the study folder (the one holding Base\\ and Projects\\), or set
ROOT below.
"""
import os
import re
import sys
import csv
import time

# ============================================================================
#   SETTINGS
# ============================================================================
GEN_BUSES = []                 # e.g. [584713, 539114, 764984, 531601]; [] = asked when run
ROOT = ""                      # the study folder holding Base\ and Projects\ ("" = this file's folder)
PROJECTS = ["SantaFe", "IronStar", "EmpirePrairie", "EastFork"]   # [] = every project found
MODE = "spp"
FAULTS = []                    # [] = every fault with a PSS/E log | e.g. ["F134", "F10-F20"]

# The runs, as in z6_overlay.py: (label, results folder under ROOT, run-folder suffix).
# Run folder = <results folder>\<project>\<project>_<MODE><suffix>, or the same folder
# directly in the results folder. Nothing else is read (backup copies are never used).
# A results folder with "base" in its name is the base case.
CASES = [
    ("Base case",                    r"Base\results_base_f",     ""),
    ("Scenario 1 (SGF + EGF, GIA)",  r"Projects\results_proj_f", ""),
    ("Scenario 2 (SGF on, EGF off)", r"Projects\results_proj_f", "_s1_egfoff"),
]
ALL_RUNS = False               # True = also every other run folder of the project in those
                               #   results folders (capacity levels, cap bank, diagnostic runs ...)
DYR_FILES = []                 # [] = every .dyr directly in Base\ and Projects\ | or paths (under ROOT or full)
DYR_INCLUDE_LOADED = False     # True = also the LOADED_*.dyr dumps the builds leave beside them
OUT_DIR = "trip_reasons"       # under ROOT unless a full path
SHOW_PICKUPS = True            # also list the unit's relays that picked up but reset in time
SPIKE_PU = 2.0                 # a monitored voltage above this is a numerical spike, not a real overvoltage
SPIKE_HZ = 5.0                 # a monitored frequency this far from 60 Hz is a numerical spike
LINK_WINDOW_S = 0.05           # a trip is put down to a relay whose breaker timer ran out at most this long before it
# ============================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
BAR = "=" * 78
SUB = "-" * 78


def _root():
    return os.path.abspath(ROOT) if ROOT else HERE


def _abs(p):
    p = str(p).replace("\\", os.sep).replace("/", os.sep)
    return p if os.path.isabs(p) else os.path.join(_root(), p)


def _rel(p):
    try:
        r = os.path.relpath(p, _root())
        return p if r.startswith("..") else r
    except Exception:
        return p


def _fkey(fid):
    m = re.match(r"^([A-Za-z_]*)(\d+)(.*)$", fid)
    if m:
        return (m.group(1).upper(), int(m.group(2)), m.group(3))
    return (fid.upper(), -1, "")


def _num(t):
    try:
        v = float(t)
    except Exception:
        return None
    return None if v != v else v          # NaN -> None


def _g(x, nd=3):
    """A setting as written: 0.9 -> '0.90', 1.485 -> '1.485', 57 -> '57.0'."""
    if x is None:
        return "?"
    s = ("%." + str(nd) + "f") % x
    s = s.rstrip("0")
    if s.endswith("."):
        s += "0"
    if len(s.split(".")[1]) < 2 and abs(x) < 10:
        s += "0"
    return s


def _name(s):
    """'G19-054-GEN20.6000' -> 'G19-054-GEN2 0.6000' (PSS/E's 12-character name + kV)."""
    s = (s or "").strip()
    if len(s) > 12 and re.match(r"^\s*\d+\.\d+$", s[12:]):
        return "%s %s" % (s[:12].strip(), s[12:].strip())
    return re.sub(r"\s+", " ", s)


def _want_faults():
    if not FAULTS:
        return None
    out = set()
    for f in FAULTS:
        f = str(f).strip().upper()
        m = re.match(r"^([A-Z_]*)(\d+)\s*-\s*([A-Z_]*)(\d+)$", f)
        if m and (not m.group(3) or m.group(3) == m.group(1)):
            a, b = int(m.group(2)), int(m.group(4))
            for i in range(min(a, b), max(a, b) + 1):
                out.add((m.group(1), i))
        else:
            k = _fkey(f)
            out.add((k[0], k[1]) if k[1] >= 0 else (f, -1))
    return out


def _fault_wanted(fid, want):
    if want is None:
        return True
    k = _fkey(fid)
    return (k[0], k[1]) in want or (fid.upper(), -1) in want


# ---------------------------------------------------------------- the units --
def _units_from(args):
    """[(bus, machine id or None)] from '584713', '584713:1', '584713,539114'."""
    out = []
    for a in args:
        for tok in re.split(r"[\s,;]+", str(a).strip()):
            if not tok:
                continue
            m = re.match(r"^(\d+)(?::(.+))?$", tok)
            if not m:
                print("[trip-why] '%s' is not a bus number -- skipped" % tok)
                continue
            u = (m.group(1), (m.group(2) or "").strip() or None)
            if u not in out:
                out.append(u)
    return out


def get_units():
    if len(sys.argv) > 1:
        return _units_from(sys.argv[1:])
    if GEN_BUSES:
        return _units_from([str(b) for b in GEN_BUSES])
    try:
        ans = input("Generator bus number(s), e.g. 584713 539114 (bus:id for one machine): ")
    except (EOFError, KeyboardInterrupt):
        return []
    return _units_from([ans])


# -------------------------------------------------------------------- .dyr --
_TOK = re.compile(r"'[^']*'|\"[^\"]*\"|[^\s,'\"/]+")
RELAY_T = re.compile(r"^(VTG|FRQ)[A-Z]{3}T$")            # VTGTPAT VTGDCAT FRQTPAT FRQDCAT
RELAY_U = ("VTGTPA", "VTGDCA", "FRQTPA", "FRQDCA")       # the same relays as USRMDL records


def _cut_slash(line):
    """(the text before the first / outside quotes, True when there was one)."""
    if "/" not in line:
        return line, False
    if "'" not in line and '"' not in line:
        return line[:line.index("/")], True
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "/":
            return line[:i], True
    return line, False


def _unq(t):
    t = str(t)
    if len(t) >= 2 and t[0] in "'\"" and t[-1] == t[0]:
        return t[1:-1].strip()
    return t.strip()


def dyr_records(path, keep):
    """The records of one .dyr that name one of the buses in `keep` (as any field).
       A record ends at '/'; text after a '/' is a comment; '@!' lines are comments.
       -> [(tokens, line number)]"""
    out = []
    cur, start = [], 0
    with open(path, "r", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            s = line.lstrip()
            if not s or s.startswith("@!"):
                continue
            body, end = _cut_slash(line)
            toks = _TOK.findall(body)
            if toks and not cur:
                start = n
            cur.extend(toks)
            if end:
                if cur and any(_unq(t) in keep for t in cur[:16]):
                    out.append((cur, start))
                cur = []
    if cur and any(_unq(t) in keep for t in cur[:16]):
        out.append((cur, start))
    return out


def decode(toks):
    """One .dyr record -> {"kind": "relay", ...} | {"kind": "model", ...} | None."""
    if len(toks) < 3:
        return None
    m1 = _unq(toks[1]).upper()
    if RELAY_T.match(m1) and len(toks) >= 9:
        return {"kind": "relay", "model": m1, "inst": _unq(toks[0]), "mon": _unq(toks[2]),
                "gen": _unq(toks[3]), "id": _unq(toks[4]),
                "cons": [_num(x) for x in toks[5:9]]}
    if m1 == "USRMDL" and len(toks) >= 10:
        m3 = _unq(toks[3]).upper()
        if m3 in RELAY_U:
            try:
                ni, nc = int(toks[6]), int(toks[7])
            except Exception:
                return None
            icons = toks[10:10 + ni]
            cons = [_num(x) for x in toks[10 + ni:10 + ni + nc]]
            if len(icons) >= 3 and len(cons) >= 4:
                return {"kind": "relay", "model": m3, "inst": _unq(toks[0]), "mon": _unq(icons[0]),
                        "gen": _unq(icons[1]), "id": _unq(icons[2]), "cons": cons[:4]}
            return None
        return {"kind": "model", "bus": _unq(toks[0]), "model": m3, "id": _unq(toks[2])}
    return {"kind": "model", "bus": _unq(toks[0]), "model": m1, "id": _unq(toks[2])}


def find_dyr_files():
    if DYR_FILES:
        out = []
        for p in DYR_FILES:
            a = _abs(p)
            if os.path.isfile(a):
                out.append(a)
            else:
                print("[trip-why] .dyr not found: %s" % a)
        return out
    out = []
    for sub in ("Base", "Projects"):
        d = _abs(sub)
        if not os.path.isdir(d):
            continue
        for nm in sorted(os.listdir(d)):
            if not nm.lower().endswith(".dyr"):
                continue
            if nm.upper().startswith("LOADED_") and not DYR_INCLUDE_LOADED:
                continue
            p = os.path.join(d, nm)
            if os.path.isfile(p):
                out.append(p)
    return out


def read_dyr(units):
    """{bus: {"models": [(model, id, file)], "relays": [rec + "files"], "other": [(text, file)]}}"""
    keep = set(b for b, _i in units)
    info = dict((b, {"models": [], "relays": [], "other": []}) for b in keep)
    files = find_dyr_files()
    for p in files:
        t0 = time.time()
        try:
            recs = dyr_records(p, keep)
        except Exception as e:
            print("[trip-why] could not read %s (%s)" % (p, e))
            continue
        for toks, ln in recs:
            d = decode(toks)
            used = False
            if d and d["kind"] == "relay" and d["gen"] in keep:
                lst = info[d["gen"]]["relays"]
                for r in lst:
                    if (r["model"], r["inst"], r["mon"], r["id"], r["cons"]) == \
                            (d["model"], d["inst"], d["mon"], d["id"], d["cons"]):
                        r["files"].append(p)
                        break
                else:
                    d["files"] = [p]
                    d["line"] = ln
                    lst.append(d)
                used = True
            elif d and d["kind"] == "model" and d["bus"] in keep:
                row = (d["model"], d["id"])
                if row not in [(m, i) for m, i, _f in info[d["bus"]]["models"]]:
                    info[d["bus"]]["models"].append((d["model"], d["id"], p))
                used = True
            if not used:
                for b in keep:
                    if b in [_unq(t) for t in toks[:16]]:
                        txt = " ".join(toks)
                        txt = txt if len(txt) <= 110 else txt[:107] + "..."
                        if txt not in [x for x, _f in info[b]["other"]]:
                            info[b]["other"].append((txt, p))
        print("[trip-why] .dyr %s read (%.1f s)" % (_rel(p), time.time() - t0))
    for b in info:
        info[b]["relays"].sort(key=lambda r: (r["model"], r["inst"]))
    return info, files


# ---------------------------------------------------------- relay settings --
def _is_volt(model):
    return str(model).upper().startswith("VTG")


def _lo_on(rec):
    lo = rec["cons"][0] if rec and rec.get("cons") else None
    if lo is None:
        return False
    return lo > (0.0 if _is_volt(rec["model"]) else 1.0)       # VL <= 0 pu / FL <= 1 Hz (-100) = off


def _hi_on(rec):
    hi = rec["cons"][1] if rec and rec.get("cons") and len(rec["cons"]) > 1 else None
    if hi is None:
        return False
    return hi < (4.99 if _is_volt(rec["model"]) else 99.0)     # VU >= 5 pu / FU >= 99 Hz (100) = off


def _hi_far(rec):
    """An upper voltage limit set out of reach (5 or 10 pu) -- still crossed by numerical spikes."""
    hi = rec["cons"][1] if rec and rec.get("cons") and len(rec["cons"]) > 1 else None
    return hi is not None and _is_volt(rec["model"]) and 4.99 <= hi < 99.0


def setting_words(rec):
    """'V below 0.45 pu' / 'f below 57.0 Hz or above 63.0 Hz' / 'V below 0.60 pu (or above 5.0)'."""
    if not rec:
        return "(setting not found in the .dyr files read)"
    v = _is_volt(rec["model"])
    q, u = ("V", "pu") if v else ("f", "Hz")
    parts = []
    if _lo_on(rec):
        parts.append("%s below %s %s" % (q, _g(rec["cons"][0]), u))
    if _hi_on(rec):
        parts.append("%s above %s %s" % (q, _g(rec["cons"][1]), u))
    txt = " or ".join(parts)
    if _hi_far(rec):
        txt = ("%s (or above %s)" % (txt, _g(rec["cons"][1]))) if txt else \
            ("only above %s pu" % _g(rec["cons"][1]))
    return txt or ("both thresholds off (low %s, high %s)" % (_g(rec["cons"][0]), _g(rec["cons"][1])))


def _t(rec, i):
    try:
        x = rec["cons"][i]
        return 0.0 if x is None else float(x)
    except Exception:
        return None


def which_side(rec, model, val):
    """'UV' 'OV' 'UF' 'OF' (or 'UV/OV' when the log has no value and both are on)."""
    v = _is_volt(model)
    lo, hi = ("UV", "OV") if v else ("UF", "OF")
    tol = 0.006
    if rec and val is not None:
        if _lo_on(rec) and val <= rec["cons"][0] + tol:
            return lo
        if (_hi_on(rec) or _hi_far(rec)) and val >= rec["cons"][1] - tol:
            return hi
    if rec:
        if _lo_on(rec) and not _hi_on(rec):
            return lo
        if _hi_on(rec) and not _lo_on(rec):
            return hi
    if val is not None:
        if v:
            return hi if val > 1.0 else lo
        return hi if val > 60.0 else lo
    return lo + "/" + hi


WORDS = {"UV": "UNDER-VOLTAGE", "OV": "OVER-VOLTAGE", "UF": "UNDER-FREQUENCY",
         "OF": "OVER-FREQUENCY", "UV/OV": "VOLTAGE", "UF/OF": "FREQUENCY"}


# ------------------------------------------------------------- PSS/E logs --
R_RELAY_HDR = re.compile(r"^\s*Model\s+([A-Za-z0-9_]+)\s+Model Instance\s+(\d+)\s*:")
R_MODEL_HDR = re.compile(r"^\s*Model\s+([A-Za-z0-9_]+)\s+Bus\s+(\d+)\s*\[([^\]]*)\]\s*(?:Machine\s+\"([^\"]*)\")?")
R_ATBUS_HDR = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s+AT BUS\s+(\d+)\s*\[([^\]]*)\]\s*:\s*$")
R_PICK = re.compile(r"Pickup timer (started|reset) at TIME\s*=\s*(-?[\d.]+)\s*,\s*(Voltage|Frequency)"
                    r" at the monitored bus\s+(\d+)\s*\[([^\]]*)\]\s*=\s*(\S+)")
R_BRK = re.compile(r"Breaker timer (started|timed out) at TIME\s*=\s*(-?[\d.]+)\s+for Generator\s+"
                   r"\"([^\"]*)\"\s+connected at bus\s+(\d+)\s*\[([^\]]*)\]")
R_MTRIP = re.compile(r"^\s*MACHINE\s+(\S+)\s+AT BUS\s+(\d+)\s*\[([^\]]*)\]\s*TRIPPED AT TIME\s*=\s*(-?[\d.]+)")
R_BDISC = re.compile(r"^\s*BUS\s+(\d+)\s+DISCONNECTED AT TIME\s*=\s*(-?[\d.]+)")
R_IPICK = re.compile(r"PICKUP TIMER (STARTED|RESET) AT TIME\s*=\s*(-?[\d.]+)\s+(VOLTAGE|FREQUENCY)\s*=\s*(\S+)"
                     r"\s*\(RELAY\s*#?\s*(\d+)\)")
R_ITRIP = re.compile(r"\bTRIPPED\s*:\s*(.+?)\s*$")
R_TIME = re.compile(r"TIME\s*=\s*(-?\d+(?:\.\d+)?)")
R_OOS = re.compile(r"OUT OF STEP CONDITION AT TIME\s*=\s*(-?[\d.]+)")
R_BUSNO = re.compile(r"(?:^|\s)(\d{3,7})(?=\s)")
R_TOBUS = re.compile(r"(?<![\d.])(\d{3,7})(?![\d.])")


class LogData(object):
    def __init__(self):
        self.pick = {}       # inst -> [(t, "started"|"reset", qty, monbus, value)]
        self.brk = []        # (t, "started"|"timed out", model, inst, gid, genbus, pickup that led to it)
        self.mtrip = []      # (t, gid, bus, breaker events before it)
        self.bdisc = []      # (t, bus, breaker events before it)
        self.itrip = []      # (t, model, bus, gid, text)
        self.ipick = []      # (t, model, bus, state, qty, value, relay no)
        self.island = []     # (t, set of buses)
        self.oos = []        # (t, from bus, to bus)
        self.names = {}      # bus -> name as PSS/E prints it
        self.models = {}     # inst -> relay model name


def parse_log(path, wbus, winst, quick):
    """The events of one PSS/E fault log that concern the watched buses / relays.
       -> (LogData, None) or (None, why it could not be read)"""
    try:
        with open(path, "r", errors="replace") as fh:
            text = fh.read()
    except Exception as e:
        return None, "could not be read (%s)" % e
    ld = LogData()
    if not any(q in text for q in quick):
        return ld, None                                  # nothing about these units in it
    hdr = None
    last_t = None
    island = None
    oos = None
    last_start = {}
    for line in text.splitlines():
        if line.startswith("FLOW"):                      # 'FLOW1 BUS ... NOT FOUND' noise
            continue
        s = line.strip()
        if not s:
            hdr = None
            if island is not None:
                hit = set(island) & wbus
                if hit:
                    ld.island.append((last_t, set(island)))
                island = None
            if oos is not None and oos[0] == "rows":
                oos = None
            continue
        if island is not None:
            if "BUS#" not in line:
                island.extend(R_BUSNO.findall(line))
            continue
        if oos is not None:
            if oos[0] == "head":
                if "F R O M" in line or "BUS#" in line:
                    continue
                oos[0] = "rows"
            m0 = re.match(r"\s*(\d+)\s", line)
            m1 = R_TOBUS.search(line[28:]) if len(line) > 28 else None
            if m0 and m1:
                a, b = m0.group(1), m1.group(1)
                if a in wbus or b in wbus:
                    ld.oos.append((oos[1], a, b))
                continue
            oos = None
        if "TIME" in line:
            mt = R_TIME.search(line)
            if mt:
                last_t = _num(mt.group(1))
        if "Model Instance" in line:
            m = R_RELAY_HDR.match(line)
            if m:
                hdr = ("R", m.group(1).upper(), m.group(2))
                ld.models[m.group(2)] = m.group(1).upper()
                continue
        if "Pickup timer" in line:
            m = R_PICK.search(line)
            if m:
                inst = hdr[2] if hdr and hdr[0] == "R" else None
                st, tt, qty, mb, nm, val = m.groups()
                tt = _num(tt)
                if inst:
                    if st == "started":
                        last_start[inst] = (tt, qty, mb, val)
                    if inst in winst or mb in wbus:
                        ld.pick.setdefault(inst, []).append((tt, st, qty, mb, val))
                        ld.names.setdefault(mb, _name(nm))
                continue
        if "Breaker timer" in line:
            m = R_BRK.search(line)
            if m:
                st, tt, gid, gb, nm = m.groups()
                if gb in wbus:
                    inst = hdr[2] if hdr and hdr[0] == "R" else None
                    model = hdr[1] if hdr and hdr[0] == "R" else "?"
                    ld.brk.append((_num(tt), st, model, inst, gid.strip(), gb,
                                   last_start.get(inst) if inst else None))
                    ld.names.setdefault(gb, _name(nm))
                continue
        if "TRIPPED AT TIME" in line and "MACHINE" in line:
            m = R_MTRIP.match(line)
            if m and m.group(2) in wbus:
                ld.mtrip.append((_num(m.group(4)), m.group(1).strip(), m.group(2), len(ld.brk)))
                ld.names.setdefault(m.group(2), _name(m.group(3)))
            continue
        if "DISCONNECTED AT TIME" in line:
            m = R_BDISC.match(line)
            if m and m.group(1) in wbus:
                ld.bdisc.append((_num(m.group(2)), m.group(1), len(ld.brk)))
            continue
        if "following buses are disconnected" in line:
            island = []
            continue
        if "OUT OF STEP CONDITION" in line:
            m = R_OOS.search(line)
            oos = ["head", _num(m.group(1)) if m else last_t]
            continue
        if line.lstrip().startswith("Model ") and " Bus " in line:
            m = R_MODEL_HDR.match(line)
            if m:
                hdr = ("M", m.group(1).upper(), m.group(2), (m.group(4) or "").strip() or None)
                if m.group(2) in wbus:
                    ld.names.setdefault(m.group(2), _name(m.group(3)))
                continue
        if " AT BUS " in line and s.endswith(":"):
            m = R_ATBUS_HDR.match(line)
            if m:
                hdr = ("M", m.group(1).upper(), m.group(2), None)
                if m.group(2) in wbus:
                    ld.names.setdefault(m.group(2), _name(m.group(3)))
                continue
        if hdr and hdr[0] == "M" and hdr[2] in wbus:
            if "PICKUP TIMER" in line:
                m = R_IPICK.search(line)
                if m:
                    st, tt, qty, val, rn = m.groups()
                    ld.ipick.append((_num(tt), hdr[1], hdr[2], st.lower(), qty.title(), val, rn))
                continue
            if "TRIPPED" in line and ":" in line:
                m = R_ITRIP.search(line)
                if m:
                    ld.itrip.append((last_t, hdr[1], hdr[2], hdr[3], m.group(1)))
    return ld, None


# ---------------------------------------------------------------- analysis --
def _gid_ok(want, got):
    return want is None or str(want).strip().upper() == str(got).strip().upper()


def _val_txt(qty, val):
    v = _num(val)
    if v is None:
        if val and "*" in val:
            return "a value too large for PSS/E to print (*******)", None
        return "no value (NaN)", None
    return ("%.2f pu" % v if qty.lower().startswith("v") else "%.2f Hz" % v), v


def relay_reason(model, inst, rec, pick, t_bs, t_bt, act):
    """The sentence for a trip by a relay, and its short label."""
    qty = pick[1] if pick else ("Voltage" if _is_volt(model) else "Frequency")
    vtxt, val = _val_txt(qty, pick[3]) if pick else ("", None)
    side = which_side(rec, model, val)
    spike = False
    if val is not None:
        if _is_volt(model) and val > SPIKE_PU:
            spike = True
        if not _is_volt(model) and abs(val - 60.0) > SPIKE_HZ:
            spike = True
    mon = (pick[2] if pick else None) or (rec["mon"] if rec else "?")
    tp, tb = (_t(rec, 2), _t(rec, 3)) if rec else (None, None)
    instant = tp is not None and tp <= 0.02
    head = "%s relay %s %s (monitored bus %s, setting: %s, pickup %s s, breaker %s s)" % (
        WORDS.get(side, side), model, inst, mon, setting_words(rec),
        _g(tp) if tp is not None else "?", _g(tb) if tb is not None else "?")
    bits = [head + "."]
    if pick:
        what = "voltage" if qty.lower().startswith("v") else "frequency"
        if spike and instant:
            bits.append("Its pickup timer started at %.3f s on a %s of %s -- a NUMERICAL SPIKE at a "
                        "switching instant, not a real %s." % (pick[0], what, vtxt, what))
        elif spike:
            bits.append("Its pickup timer started at %.3f s on a %s of %s -- a numerically impossible "
                        "value at a switching instant -- and the %s stayed beyond the setting for "
                        "the whole pickup time (see the unit's plot: a non-converged solution can "
                        "hold such values)." % (pick[0], what, vtxt, what))
        else:
            bits.append("Its pickup timer started at %.3f s when the %s at bus %s was %s." % (
                pick[0], what, mon, vtxt))
        if t_bs is not None and pick[0] is not None:
            held = t_bs - pick[0]
            if tp is not None and tp <= 0.0005:
                bits.append("With a zero pickup time one time step was enough.")
            else:
                bits.append("The condition held for %.3f s (pickup timer %.3f -> %.3f s)." % (
                    held, pick[0], t_bs))
    else:
        bits.append("PSS/E did not print the value that started its pickup timer.")
    if t_bt is not None:
        bits.append("Breaker timer ran out at %.3f s and the relay %s." % (
            t_bt, "disconnected the unit's bus" if act == "disc" else "tripped the unit"))
    short = "%s%s %s" % (side, "-spike" if (spike and instant) else "", inst)
    key = "%s relay %s %s (%s)%s" % (side, model, inst, setting_words(rec),
                                    (", on a numerical spike" if instant else ", started on a numerical spike")
                                    if spike else "")
    return " ".join(bits), short, key


def _relay_rec(unit_relays, inst):
    for r in unit_relays:
        if r["inst"] == inst:
            return r
    return None


def explain(unit, gid, ld, unit_relays):
    """What happened to one unit in one fault log.
       -> {"events": [ {t, kind, gid, short, key, text} ], "pickups": [...], "notes": [...]}"""
    out = {"events": [], "pickups": [], "notes": []}
    used_brk = set()

    def brk_for(t, want_gid, n_before):
        best = None
        for i, b in enumerate(ld.brk[:n_before]):
            if b[1] != "timed out" or b[5] != unit or b[0] is None or t is None:
                continue
            if want_gid is not None and not _gid_ok(want_gid, b[4]):
                continue
            if b[0] <= t + 0.002 and t - b[0] <= LINK_WINDOW_S + 0.002:
                best = i
        return best

    def brk_started(inst, t_bt):
        ts = None
        for b in ld.brk:
            if b[3] == inst and b[1] == "started" and b[0] is not None and b[0] <= t_bt + 0.002:
                ts = b[0]
        return ts

    def pick_before(inst, t_ref, snap):
        """The pickup start that led to the breaker timer: the last one before it."""
        best = None
        for (tt, st, qty, mb, val) in ld.pick.get(inst, []):
            if st == "started" and tt is not None and (t_ref is None or tt <= t_ref + 0.002):
                best = (tt, qty, mb, val)
        return best or snap

    def by_relay(i, t, kind):
        b = ld.brk[i]
        used_brk.add(i)
        t_bt, model, inst, snap = b[0], b[2], b[3], b[6]
        t_bs = brk_started(inst, t_bt)
        rec = _relay_rec(unit_relays, inst)
        pick = pick_before(inst, t_bs if t_bs is not None else t_bt, snap)
        return relay_reason(model, inst, rec, pick, t_bs, t_bt, kind)

    # 1. MACHINE ... TRIPPED
    for (t, g, bus, nb) in ld.mtrip:
        if bus != unit or not _gid_ok(gid, g):
            continue
        i = brk_for(t, g, nb)
        if i is not None:
            text, short, key = by_relay(i, t, "trip")
        else:
            it = [x for x in ld.itrip if x[2] == unit and x[0] is not None and t is not None
                  and abs(x[0] - t) <= LINK_WINDOW_S]
            if it:
                text = "The unit model's own protection (%s): %s." % (it[0][1], it[0][4])
                short, key = "own prot. %s" % it[0][1], "own protection of %s: %s" % (it[0][1], it[0][4])
            else:
                text = ("PSS/E tripped it, but its log does not name the model that did "
                        "(no relay message for this unit just before).")
                short, key = "tripped (no model named)", "tripped, no model named in the log"
        out["events"].append({"t": t, "kind": "trip", "gid": g, "text": text, "short": short, "key": key})
    # 2. BUS ... DISCONNECTED
    for (t, bus, nb) in ld.bdisc:
        if bus != unit:
            continue
        i = brk_for(t, gid, nb)
        if i is not None and i not in used_brk:
            text, short, key = by_relay(i, t, "disc")
        else:
            text = ("PSS/E disconnected the unit's bus at %.3f s, and none of the unit's own relays "
                    "had acted: the switching of the event (or another element's protection) left "
                    "the bus without a connection." % t)
            short, key = "bus disconnected", "bus disconnected by the network, not its own relay"
        out["events"].append({"t": t, "kind": "disc", "gid": None, "text": text, "short": short, "key": key})
    # 3. the model's own trip with no MACHINE line
    for (t, model, bus, g, txt) in ld.itrip:
        if bus != unit or not _gid_ok(gid, g or gid):
            continue
        if any(e["t"] is not None and t is not None and abs(e["t"] - t) <= LINK_WINDOW_S
               for e in out["events"]):
            continue
        out["events"].append({"t": t, "kind": "trip", "gid": g,
                              "text": "The unit model's own protection (%s): %s." % (model, txt),
                              "short": "own prot. %s" % model,
                              "key": "own protection of %s: %s" % (model, txt)})
    # 4. isolated (PSS/E's list of disconnected buses) with no line above
    for (t, buses) in ld.island:
        if unit not in buses:
            continue
        if out["events"]:
            out["notes"].append("PSS/E also lists bus %s among the buses left disconnected (at about %s s)."
                                % (unit, "%.3f" % t if t is not None else "?"))
            continue
        out["events"].append({"t": t, "kind": "iso", "gid": None,
                              "text": ("PSS/E lists the unit's bus among the buses left disconnected "
                                       "(island with no connection) at about %s s: the event's "
                                       "switching isolated it; no relay of the unit acted."
                                       % ("%.3f" % t if t is not None else "?")),
                              "short": "isolated", "key": "isolated by the event's switching"})
    for (t, a, b) in ld.oos:
        if unit not in (a, b):
            continue
        out["notes"].append("PSS/E reported an OUT-OF-STEP condition at %.3f s on the branch %s - %s."
                            % (t if t is not None else -1, a, b))
    # 5. the unit's relays that picked up but did not trip it
    if SHOW_PICKUPS:
        tripped = set(ld.brk[i][3] for i in used_brk)
        t_off = min([e["t"] for e in out["events"] if e["t"] is not None] or [None]) \
            if out["events"] else None
        insts = [r["inst"] for r in unit_relays if _gid_ok(gid, r["id"])]
        for b in ld.brk:
            if b[5] == unit and b[3] and b[3] not in insts and _gid_ok(gid, b[4]):
                insts.append(b[3])
        for inst in insts:
            ev = ld.pick.get(inst, [])
            starts = [e for e in ev if e[1] == "started"]
            if not starts or inst in tripped:
                continue
            rec = _relay_rec(unit_relays, inst)
            model = rec["model"] if rec else ld.models.get(inst, "?")
            vtxt, _v = _val_txt(starts[0][2], starts[0][4])
            if rec and _v is not None:
                sd = which_side(rec, model, _v)
                vtxt += " (%s side)" % WORDS.get(sd, sd).lower()
            last = ev[-1]
            if last[1] == "reset":
                end = "reset at %.3f s" % last[0]
            elif t_off is not None:
                end = "had not reset when the unit went off at %.3f s" % t_off
            else:
                end = "still timing at the end of the log"
            bto = [b for b in ld.brk if b[3] == inst and b[1] == "timed out"]
            if bto:
                end += "; breaker timer ran out at %.3f s" % bto[-1][0]
            out["pickups"].append("%s %s (%s, pickup %s s): picked up %d time(s), first at %.3f s on %s, %s"
                                  % (model, inst, setting_words(rec),
                                     _g(_t(rec, 2)) if rec else "?", len(starts),
                                     starts[0][0], vtxt, end))
        for (t, model, bus, st, qty, val, rn) in ld.ipick:
            if bus == unit and st == "started":
                vtxt, _v = _val_txt(qty, val)
                out["pickups"].append("%s relay #%s of the unit model: picked up at %.3f s on %s %s"
                                      % (model, rn, t if t is not None else -1, qty.lower(), vtxt))
    out["events"].sort(key=lambda e: (e["t"] is None, e["t"]))
    return out


# ------------------------------------------------------------ run folders --
def _case_dir(res_root, proj, suffix):
    """ONLY <results>\<project>\<project>_<mode><suffix> or <results>\<project>_<mode><suffix>."""
    name = "%s_%s%s" % (proj, MODE, suffix)
    root = _abs(res_root)
    for d in (os.path.join(root, proj, name), os.path.join(root, name)):
        if os.path.isdir(d):
            return d
    return None


def find_projects():
    if PROJECTS:
        return list(PROJECTS)
    found = []
    for _l, res_root, _s in CASES:
        r = _abs(res_root)
        if not os.path.isdir(r):
            continue
        for nm in sorted(os.listdir(r)):
            m = re.match(r"^([A-Za-z0-9]+)_%s(_.*)?$" % re.escape(MODE), nm)
            p = m.group(1) if m else (nm if os.path.isdir(os.path.join(r, nm, "%s_%s" % (nm, MODE))) else None)
            if p and p not in found:
                found.append(p)
    return found


def find_runs(proj):
    """[(label, folder, is_base)] -- CASES first, then (ALL_RUNS) the project's other run folders."""
    runs, seen = [], set()
    for label, res_root, sfx in CASES:
        d = _case_dir(res_root, proj, sfx)
        if d is None:
            print("[trip-why] %s: no folder for '%s' (%s\\...\\%s_%s%s) -- skipped"
                  % (proj, label, res_root, proj, MODE, sfx))
            continue
        if os.path.normcase(d) in seen:
            continue
        seen.add(os.path.normcase(d))
        runs.append((label, d, "base" in res_root.lower()))
    if ALL_RUNS:
        roots = []
        for _l, res_root, _s in CASES:
            if res_root not in roots:
                roots.append(res_root)
        for res_root in roots:
            base = "base" in res_root.lower()
            r = _abs(res_root)
            for parent in (os.path.join(r, proj), r):
                if not os.path.isdir(parent):
                    continue
                for nm in sorted(os.listdir(parent)):
                    head = "%s_%s" % (proj, MODE)
                    if not (nm == head or nm.startswith(head + "_")):
                        continue
                    d = os.path.join(parent, nm)
                    if not os.path.isdir(d) or os.path.normcase(d) in seen:
                        continue
                    seen.add(os.path.normcase(d))
                    tag = nm[len(head):].lstrip("_") or "as studied"
                    runs.append(("%s %s" % ("Base" if base else "Project", tag), d, base))
    return runs


def run_logs(folder):
    """{fault: path} of the run's PSS/E fault logs."""
    d = os.path.join(folder, "logs", "psse")
    out = {}
    if os.path.isdir(d):
        for nm in os.listdir(d):
            if nm.lower().endswith(".txt"):
                out[nm[:-4]] = os.path.join(d, nm)
    return out


def run_scorer(folder, units):
    """{(fault, bus): [{"tripped": bool, "evidence": str, "signal": str, ...}]} from SPP_MEASURE_MACHINES."""
    paths = []
    for sub in ("reports", ""):
        d = os.path.join(folder, sub) if sub else folder
        if os.path.isdir(d):
            for nm in os.listdir(d):
                if nm.upper().startswith("SPP_MEASURE_MACHINES") and nm.lower().endswith(".csv"):
                    paths.append(os.path.join(d, nm))
    paths.sort(key=lambda p: os.path.getmtime(p))
    want = set(b for b, _i in units)
    out = {}
    for p in paths:
        try:
            with open(p, "r", errors="replace") as fh:
                for row in csv.DictReader(fh):
                    bus = (row.get("Bus") or "").strip()
                    if bus.endswith(".0"):
                        bus = bus[:-2]
                    sig = row.get("Signal") or ""
                    if bus not in want:
                        hit = [b for b in want if re.search(r"(?<!\d)%s(?!\d)" % b, sig)]
                        if not hit:
                            continue
                        bus = hit[0]
                    f = (row.get("Scenario") or "").strip()
                    rows = out.setdefault((f.upper(), bus), [])
                    rows[:] = [r for r in rows if r["signal"] != sig]
                    rows.append({"signal": sig,
                                 "tripped": (row.get("Tripped") or "").strip() in ("1", "True", "YES", "yes"),
                                 "evidence": (row.get("Evidence") or "").strip(),
                                 "p0": row.get("Pre-fault P (MW)", ""), "p1": row.get("Final P (MW)", ""),
                                 "e0": row.get("Pre-fault Eterm (pu)", ""), "e1": row.get("Final Eterm (pu)", "")})
        except Exception as e:
            print("[trip-why] could not read %s (%s)" % (p, e))
    return out, paths


# ------------------------------------------------------------------ report --
def _scorer_line(rows):
    if not rows:
        return None, None
    tr = [r for r in rows if r["tripped"]]
    r = tr[0] if tr else rows[0]
    ev = r["evidence"] if r["evidence"] and r["evidence"].lower() not in ("tripped", "connected") else ""
    bits = []
    if (r["p0"] or r["p1"]) and "MW" not in ev:
        bits.append("P %s -> %s MW" % (r["p0"] or "?", r["p1"] or "?"))
    if (r["e0"] or r["e1"]) and "ETERM" not in ev.upper():
        bits.append("terminal voltage %s -> %s pu" % (r["e0"] or "?", r["e1"] or "?"))
    txt = "%s%s%s" % ("TRIPPED" if tr else "not tripped",
                      (": %s" % ev) if ev else "", ("; " + ", ".join(bits)) if bits else "")
    return bool(tr), txt


def _no_trip_reason(r):
    """Why the study calls a unit tripped that PSS/E never tripped -- from the scorer's numbers."""
    rows = r.get("scorer_rows") or []
    tr = [x for x in rows if x["tripped"]] or rows
    e1 = _num(tr[0]["e1"]) if tr else None
    if e1 is not None and e1 < 0.2:
        return ("dead bus", "PSS/E never tripped this unit -- no relay acted and its log has no trip or "
                "disconnection line for it. The study counts it as tripped because its terminal "
                "voltage went to ZERO (%s pu at the end): the unit's bus lost its connection to the "
                "grid -- the event's switching left it isolated -- so the unit stopped with it." % _g(e1, 3))
    if e1 is not None:
        return ("power to zero", "PSS/E never tripped this unit -- no relay acted and its log has no "
                "trip or disconnection line for it. The study counts it as tripped because its "
                "power ended at zero while its terminal voltage stayed up (%s pu at the end): the "
                "machine model itself took its output to zero (for a converter: its low-voltage "
                "power logic or current limit; for a synchronous machine: loss of synchronism)."
                % _g(e1, 3))
    return ("study only", "PSS/E never tripped this unit -- no relay acted and its log has no trip "
            "or disconnection line for it. The study counts it as tripped from the .out (%s): its "
            "output fell to zero without a protection trip." % (r.get("scorer_ev") or
                                                                "power or terminal voltage went to zero"))


def _status(res):
    """The short side-by-side code of one fault in one run."""
    if res is None:
        return "not run"
    if res.get("nolog"):
        return "study: tripped, no log" if res.get("scorer_tripped") else "no PSS/E log"
    ev = res["exp"]["events"]
    if ev:
        e = ev[0]
        t = (" %.2fs" % e["t"]) if e["t"] is not None else ""
        return (e["short"] + t)[:26]
    if res.get("scorer_tripped"):
        return {"dead bus": "bus dead, no relay", "power to zero": "P to zero, no relay"}.get(
            _no_trip_reason(res)[0], "study only, no relay")
    return "-"


def _short(label):
    return label.split(" (")[0]


def _tripped(res):
    if res is None:
        return False
    if res.get("nolog"):
        return bool(res.get("scorer_tripped"))
    return bool(res["exp"]["events"]) or bool(res.get("scorer_tripped"))


def _wrap(txt, width=70, indent=""):
    words = txt.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        lines.append(cur)
    return ("\n" + indent).join(lines)


def write_unit(unit, gid, dinfo, dyr_files, results, nlogs, out_dir, csv_rows):
    tag = unit + ("_" + re.sub(r"\W", "", gid) if gid else "")
    path = os.path.join(out_dir, "TRIP_REASON_%s.txt" % tag)
    name = ""
    for proj in results:
        for (_lbl, _d, _b, faults) in results[proj]:
            for f, res in faults.items():
                if res and res.get("name"):
                    name = res["name"]
                    break
    relays = [r for r in dinfo["relays"] if _gid_ok(gid, r["id"])]
    L = []
    L.append(BAR)
    L.append(" WHY UNIT %s%s TRIPS" % (unit, (" (machine '%s')" % gid) if gid else ""))
    L.append(BAR)
    L.append(" Unit          bus %s  %s" % (unit, name))
    L.append(" Study folder  %s" % _root())
    L.append(" Runs read     %s" % ", ".join(c[0] for c in CASES) + (" + every other run folder" if ALL_RUNS else ""))
    L.append("               %d project(s), %d PSS/E fault log(s)" % (
        len([p for p in results if results[p]]), nlogs))
    L.append(" .dyr read     %s" % ("\n               ".join(_rel(p) for p in dyr_files) if dyr_files else
                                    "none found -- relay settings cannot be shown (set DYR_FILES)"))
    L.append(" Written       %s by %s" % (time.strftime("%Y-%m-%d %H:%M"), os.path.basename(__file__)))
    L.append("")
    # 1. in short
    L.append(SUB)
    L.append(" 1. IN SHORT")
    L.append(SUB)
    for proj in results:
        runs = results[proj]
        if not runs:
            continue
        L.append(" %s" % proj)
        base = [r for r in runs if r[2]]
        base_f = base[0][3] if base else None
        for (lbl, _d, is_base, faults) in runs:
            n_run = len([f for f, r in faults.items() if r and not r.get("nolog")])
            tr = [(f, r) for f, r in faults.items() if _tripped(r)]
            if not tr:
                L.append("   %-30s does not trip in any of the %d fault(s) with a log" % (lbl, n_run))
                continue
            keys = {}
            for f, r in tr:
                if r.get("nolog"):
                    k = "no PSS/E log (study says tripped)"
                elif r["exp"]["events"]:
                    k = r["exp"]["events"][0]["key"]
                else:
                    k = {"dead bus": "no relay -- its bus went dead (isolated by the event); "
                                     "PSS/E never tripped it",
                         "power to zero": "no relay -- its power went to zero with the terminal "
                                          "voltage up; PSS/E never tripped it"}.get(
                        _no_trip_reason(r)[0], "no relay -- the study counts it from the .out; "
                                               "PSS/E never tripped it")
                keys.setdefault(k, []).append(f)
            L.append("   %-30s trips in %d of %d fault(s) with a log:" % (lbl, len(tr), n_run))
            for k, fs in sorted(keys.items(), key=lambda kv: -len(kv[1])):
                fs = sorted(fs, key=_fkey)
                L.append("      %3d x %s" % (len(fs), _wrap(k, 62, "            ")))
                L.append("            %s" % _wrap(", ".join(fs), 62, "            "))
        if base_f is not None:
            only = []
            for (lbl, _d, is_base, faults) in runs:
                if is_base:
                    continue
                for f, r in faults.items():
                    if _tripped(r):
                        b = base_f.get(f)
                        if b is not None and not b.get("nolog") and not _tripped(b):
                            only.append((f, lbl))
            if only:
                agg = {}
                for f, lbl in only:
                    agg.setdefault(f, []).append(_short(lbl))
                L.append("   Trips with the SGF in service but NOT in the base case:")
                L.append("      %s" % _wrap("; ".join("%s (%s)" % (f, ", ".join(v)) for f, v in
                                              sorted(agg.items(), key=lambda kv: _fkey(kv[0]))),
                                              70, "      "))
        L.append("")
    # 2. the .dyr
    L.append(SUB)
    L.append(" 2. THE UNIT IN THE .dyr")
    L.append(SUB)
    if dinfo["models"]:
        L.append(" Models   %s" % _wrap(", ".join("%s '%s'" % (m, i) for m, i, _f in dinfo["models"]), 66,
                                         "          "))
    else:
        L.append(" Models   none found for bus %s in the .dyr files read" % unit)
    if relays:
        L.append(" Relays   %d record(s) act on this unit:" % len(relays))
        L.append("   %-10s %-8s %-9s %-7s %-34s %-8s %s" % ("instance", "model", "mon. bus", "machine",
                                                         "trips when", "pickup", "breaker"))
        for r in relays:
            L.append("   %-10s %-8s %-9s %-7s %-34s %-8s %s" % (
                r["inst"], r["model"], r["mon"], "'%s'" % r["id"], setting_words(r),
                _g(_t(r, 2)) + " s", _g(_t(r, 3)) + " s"))
        clash = {}
        for r in relays:
            clash.setdefault((r["model"], r["inst"]), []).append(r)
        clash = [v for v in clash.values() if len(v) > 1]
        if clash:
            L.append("   THE SAME INSTANCE HAS DIFFERENT SETTINGS IN DIFFERENT .dyr FILES:")
            for v in clash:
                for r in v:
                    L.append("     %s %s  %s, pickup %s s  in %s" % (
                        r["model"], r["inst"], setting_words(r), _g(_t(r, 2)),
                        ", ".join(_rel(p) for p in r["files"])))
    else:
        L.append(" Relays   NONE. No VTGTPAT / VTGDCAT / FRQTPAT / FRQDCAT record (nor a USRMDL")
        L.append("          VTGTPA / FRQTPA / VTGDCA / FRQDCA) acts on this unit, so PSS/E has no")
        L.append("          generic relay that can trip it. Where the study reports it tripped,")
        L.append("          PSS/E did not trip it: its bus was isolated, a model's own protection")
        L.append("          acted, or its output fell to zero on its own (see section 3).")
    if dinfo["other"]:
        L.append(" Other records that name bus %s (not decoded here):" % unit)
        for txt, p in dinfo["other"][:15]:
            L.append("   %s" % txt)
        if len(dinfo["other"]) > 15:
            L.append("   ... and %d more" % (len(dinfo["other"]) - 15))
    L.append("")
    # 3. fault by fault
    L.append(SUB)
    L.append(" 3. FAULT BY FAULT (only the faults in which the unit trips)")
    L.append(SUB)
    for proj in results:
        runs = results[proj]
        base = [r for r in runs if r[2]]
        base_f = base[0][3] if base else None
        for (lbl, d, is_base, faults) in runs:
            tr = sorted([f for f, r in faults.items() if _tripped(r)], key=_fkey)
            L.append(" %s -- %s" % (proj, lbl))
            L.append("   folder %s" % _rel(d))
            if not tr:
                L.append("   no trip in any fault")
                L.append("")
                continue
            for f in tr:
                r = faults[f]
                if r.get("nolog"):
                    L.append(" %-6s TRIPPED (the study says so), but there is no PSS/E log for this fault"
                             % f)
                    if r.get("scorer_txt"):
                        L.append("        Study: %s" % r["scorer_txt"])
                    L.append("")
                    continue
                ex = r["exp"]
                if ex["events"]:
                    e = ex["events"][0]
                    what = {"trip": "TRIPPED", "disc": "BUS DISCONNECTED", "iso": "ISOLATED"}.get(e["kind"], "TRIPPED")
                    L.append(" %-6s %s at %s%s" % (f, what, ("%.4f s" % e["t"]) if e["t"] is not None else "?",
                                                    (" (machine '%s')" % e["gid"]) if e["gid"] else ""))
                    L.append("        Reason: %s" % _wrap(e["text"], 62, "                "))
                    for e2 in ex["events"][1:]:
                        L.append("        Then at %s: %s" % (("%.4f s" % e2["t"]) if e2["t"] is not None else "?",
                                                          _wrap(e2["text"], 56, "                ")))
                else:
                    L.append(" %-6s TRIPPED in the study's results, but PSS/E did not trip it" % f)
                    L.append("        Reason: %s" % _wrap(_no_trip_reason(r)[1], 62, "                "))
                for n in ex["notes"]:
                    L.append("        Note: %s" % _wrap(n, 62, "              "))
                if ex["pickups"]:
                    L.append("        Also picked up (did not trip it):")
                    for pk in ex["pickups"][:8]:
                        L.append("          - %s" % _wrap(pk, 64, "            "))
                    if len(ex["pickups"]) > 8:
                        L.append("          ... and %d more" % (len(ex["pickups"]) - 8))
                if r.get("scorer_txt"):
                    L.append("        Study: %s" % _wrap(r["scorer_txt"], 63, "               "))
                if not is_base and base_f is not None:
                    b = base_f.get(f)
                    if b is None:
                        L.append("        Base case: this fault was not run")
                    elif b.get("nolog"):
                        L.append("        Base case: no PSS/E log%s" % (" (study: tripped)" if b.get("scorer_tripped") else ""))
                    elif _tripped(b):
                        L.append("        Base case: ALSO trips -- %s" % _status(b))
                    else:
                        pk = b["exp"]["pickups"]
                        if pk:
                            L.append("        Base case: does NOT trip. Its relays picked up and reset:")
                            for p in pk[:4]:
                                L.append("          - %s" % _wrap(p, 64, "            "))
                        else:
                            L.append("        Base case: does NOT trip, and none of its relays picked up")
                L.append("")
    # 4. side by side
    L.append(SUB)
    L.append(" 4. SIDE BY SIDE (faults in which the unit trips in at least one run)")
    L.append(SUB)
    for proj in results:
        runs = results[proj]
        allf = set()
        for (_l, _d, _b, faults) in runs:
            allf |= set(f for f, r in faults.items() if _tripped(r))
        if not allf:
            continue
        L.append(" %s" % proj)
        L.append("   %-7s" % "fault" + "".join(" %-26s" % _short(l)[:26] for (l, _d, _b, _f) in runs))
        for f in sorted(allf, key=_fkey):
            L.append("   %-7s" % f + "".join(" %-26s" % _status(fs.get(f)) for (_l, _d, _b, fs) in runs))
        L.append("")
    L.append(SUB)
    L.append(" HOW TO READ THIS")
    L.append(SUB)
    for t in (
        "UV / OV = under- / over-voltage relay, UF / OF = under- / over-frequency relay, then the "
        "relay instance and the time the unit tripped. '-spike' = the relay acted on a numerical "
        "spike (a voltage above %s pu or a frequency more than %s Hz from 60 Hz at a switching "
        "instant): no real voltage or frequency does that, and a relay with a zero pickup time "
        "trips on one time step of it." % (_g(SPIKE_PU), _g(SPIKE_HZ)),
        "A relay picks up when its voltage or frequency crosses the setting, must stay past it for "
        "the pickup time, and then trips the unit after the breaker time. VTGTPAT / FRQTPAT trip "
        "the machine; VTGDCAT / FRQDCAT disconnect its bus.",
        "'study only' = the study counts the unit as tripped from the .out (its power ended at "
        "zero, or its terminal voltage collapsed) but PSS/E never tripped it: no relay acted.",
        "Values are what PSS/E printed in the fault's log (logs\\psse\\<fault>.txt); settings are "
        "from the .dyr files listed at the top."):
        L.append(" " + _wrap(t, 76, " "))
        L.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    # the CSV rows
    for proj in results:
        for (lbl, d, is_base, faults) in results[proj]:
            for f in sorted(faults, key=_fkey):
                r = faults[f]
                if r is None:
                    continue
                ev = r["exp"]["events"] if not r.get("nolog") else []
                e = ev[0] if ev else None
                csv_rows.append([unit, gid or "", proj, lbl, f,
                                 "YES" if _tripped(r) else "no",
                                 ("%.4f" % e["t"]) if e and e["t"] is not None else "",
                                 e["short"] if e else ("no PSS/E log" if r.get("nolog") else
                                                       (_status(r) if r.get("scorer_tripped") else "")),
                                 e["text"] if e else (_no_trip_reason(r)[1] if (r.get("scorer_tripped")
                                                                               and not r.get("nolog")) else ""),
                                 r.get("scorer_txt") or "",
                                 " | ".join(r["exp"]["pickups"]) if not r.get("nolog") else ""])
    return path


# -------------------------------------------------------------------- main --
def main():
    units = get_units()
    if not units:
        print("[trip-why] no bus number given -- nothing to do")
        return 2
    print("[trip-why] units: %s" % ", ".join(b + ((":" + i) if i else "") for b, i in units))
    print("[trip-why] study folder: %s" % _root())
    dinfo, dyr_files = read_dyr(units)
    if not dyr_files:
        print("[trip-why] no .dyr found in Base\\ or Projects\\ -- relay settings will be missing")
    wbus = set(b for b, _i in units)
    winst = set()
    quick = set(wbus)
    for b in wbus:
        for r in dinfo[b]["relays"]:
            winst.add(r["inst"])
            quick.add(r["inst"])
            quick.add(r["mon"])
    want = _want_faults()
    projects = find_projects()
    if not projects:
        print("[trip-why] no project folders found under %s -- check ROOT and CASES" % _root())
        return 2
    # results[unit][proj] = [(label, folder, is_base, {fault: res})]
    results = dict((u, {}) for u in units)
    nlogs = 0
    t0 = time.time()
    for proj in projects:
        runs = find_runs(proj)
        for u in units:
            results[u][proj] = []
        for (lbl, d, is_base) in runs:
            logs = run_logs(d)
            score, spaths = run_scorer(d, units)
            faults = sorted(set([f for f in logs if _fault_wanted(f, want)] +
                                [f for (f, b) in score if b in wbus and _fault_wanted(f, want)
                                 and any(r["tripped"] for r in score[(f, b)])]), key=_fkey)
            print("[trip-why] %s -- %s: %d log(s)%s" % (proj, lbl, len([f for f in faults if f in logs]),
                                                      ", scorer %s" % ", ".join(os.path.basename(p) for p in spaths)
                                                      if spaths else ""))
            per = dict((u, {}) for u in units)
            for k, f in enumerate(faults):
                ld = None
                if f in logs:
                    ld, why = parse_log(logs[f], wbus, winst, quick)
                    nlogs += 1
                    if ld is None:
                        print("[trip-why]   %s: log %s" % (f, why))
                if (k + 1) % 25 == 0:
                    print("[trip-why]   %d/%d faults, %.0f s" % (k + 1, len(faults), time.time() - t0))
                for (b, gid) in units:
                    srows = score.get((f.upper(), b)) or []
                    s_tr, s_txt = _scorer_line(srows)
                    ev = ""
                    if srows:
                        trr = [r for r in srows if r["tripped"]]
                        ev = (trr[0]["evidence"] if trr else "") or ""
                        if ev.lower() in ("tripped", "connected"):
                            ev = ""
                    if ld is None:
                        per[(b, gid)][f] = {"nolog": True, "scorer_tripped": s_tr, "scorer_txt": s_txt,
                                            "scorer_ev": ev, "scorer_rows": srows}
                        continue
                    ex = explain(b, gid, ld, [r for r in dinfo[b]["relays"]])
                    per[(b, gid)][f] = {"exp": ex, "scorer_tripped": s_tr, "scorer_txt": s_txt,
                                        "scorer_ev": ev, "scorer_rows": srows,
                                        "name": ld.names.get(b, "")}
            for u in units:
                results[u][proj].append((lbl, d, is_base, per[u]))
    out_dir = _abs(OUT_DIR)
    try:
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
    except Exception as e:
        print("[trip-why] could not make %s (%s) -- writing beside this script" % (out_dir, e))
        out_dir = HERE
    csv_rows = []
    written = []
    for (b, gid) in units:
        written.append(write_unit(b, gid, dinfo[b], dyr_files, results[(b, gid)], nlogs, out_dir, csv_rows))
    cp = os.path.join(out_dir, "TRIP_REASONS.csv")
    try:
        with open(cp, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Bus", "Machine", "Project", "Run", "Fault", "Tripped", "Time (s)", "Reason (short)",
                        "Reason", "Study scorer", "Relays that picked up and reset"])
            for row in csv_rows:
                w.writerow(row)
        written.append(cp)
    except Exception as e:
        print("[trip-why] could not write %s (%s)" % (cp, e))
    print("")
    print("[trip-why] done in %.0f s:" % (time.time() - t0))
    for p in written:
        print("    %s" % p)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[trip-why] stopped")
        sys.exit(1)
