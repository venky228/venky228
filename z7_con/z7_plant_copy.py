# -*- coding: utf-8 -*-
"""
z7_plant_copy.py -- compare two power-flow cases and copy plants from one into
the other, holding each affected area's generation at its original MW.

    SOURCE case  the case that HAS the plant(s)      (e.g. the project .sav)
    TARGET case  the case to add them to            (e.g. the base .sav)
    OUT case     TARGET + the copied plant(s), solved, area MW held

WHAT IT DOES
  1. Loads both cases and writes a COMPARISON: buses, machines, branches and
     transformers that are only in one of them, and generation per area.
  2. Picks the plant: PLANT_BUSES (bus numbers / ranges), or [] = every bus that
     is in SOURCE and not in TARGET.
  3. Checks before changing anything: every selected bus is new to TARGET, every
     branch / transformer it needs ends on a selected bus or on a bus TARGET
     already has, and every copied bus reaches the TARGET network through the
     copied elements (no island). Anything it cannot copy faithfully (a
     three-winding transformer, a switched shunt) stops the script with the
     list, so a half-copied plant is never saved.
  4. Records TARGET's generation in each area that receives a machine, adds the
     buses, plants, machines, lines, two-winding transformers, loads and fixed
     shunts with SOURCE's data, solves, and then scales the OTHER machines in
     each such area (not the copied ones, not the swing, not HOLD_EXCLUDE) until
     the area is back at its original MW (HOLD_TOL_MW), solving after each pass.
  5. Saves OUT_SAV and writes <OUT>.txt (what was copied, area MW before/after,
     every change made) and <OUT>_compare.csv. With SOURCE_DYR set, the dynamic
     records of the copied machines go to OUT_DYR (appended to TARGET_DYR when
     that is set).

Nothing is simulated. SOURCE_SAV and TARGET_SAV are only read.

HOW TO USE
  Fill in SETTINGS and run   py -3.4 z7_plant_copy.py
  or                         py -3.4 z7_plant_copy.py <source.sav> <target.sav>

Python 3.4, standard library and psspy only. One PSS/E licence.
"""
import os
import re
import sys
import csv
import time

# ============================================================================
#  SETTINGS
# ============================================================================
SOURCE_SAV = r"C:\KV\Projects\DIS2201-25SP-G03-CQ_PROJ.sav"   # has the plant(s)
TARGET_SAV = r"C:\KV\Base\DIS2201-25SP-G03-CQ.sav"            # gets them added
OUT_SAV    = r""            # "" = <TARGET>_plus_<n>bus.sav beside TARGET_SAV

PLANT_BUSES = []            # [] = every bus in SOURCE that TARGET does not have.
                            # Or a list of buses / ranges: [999001, "999950-999954"]
COPY_MACHINES_ON_EXISTING_BUSES = False
                            # True = also copy machines that SOURCE has at a bus
                            # TARGET already has (and TARGET lacks that machine)

HOLD_AREA_MW = True         # True = the areas receiving machines keep their MW
HOLD_AREAS   = []           # [] = every area a copied machine is in; or [534, 541]
HOLD_EXCLUDE = []           # buses whose machines are never rescaled (e.g. an EGF)
HOLD_TOL_MW  = 0.5          # stop when every held area is within this
HOLD_PASSES  = 6            # solve + rescale passes at most
RESPECT_LIMITS = True       # True = rescaled machines stay within PMIN..PMAX

SOURCE_DYR = r""            # optional: dynamic data of SOURCE (.dyr)
TARGET_DYR = r""            # optional: dynamic data of TARGET (.dyr); the copied
                            # records are appended to a copy of it
OUT_DYR    = r""            # "" = <OUT>.dyr

SOLVE_OPTS = [0, 0, 0, 1, 1, 0, 0, 0]   # FDNS / FNSL options (the study's own)
# ============================================================================


# ---------------------------------------------------------------- PSS/E start
def _add_path(d):
    if d and os.path.isdir(d):
        if d not in sys.path:
            sys.path.insert(0, d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        return True
    return False


def _bootstrap_psse():
    """The PSSPY folder that matches this Python, plus PSSBIN -- as the study
       scripts find it."""
    import glob
    pyv = sys.version_info[:2]
    prefer = "PSSPY%d%d" % pyv
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        roots += [d for d in glob.glob(os.path.join(base, "PSSE3*")) if os.path.isdir(d)]
    for root in roots:
        if root and os.path.isdir(os.path.join(root, prefer)):
            _add_path(os.path.join(root, prefer))
            _add_path(os.path.join(root, "PSSBIN"))
            return True
    for root in roots:
        pbin = os.path.join(root or "", "PSSBIN")
        if root and (os.path.isfile(os.path.join(pbin, "psspy.pyd"))
                     or os.path.isfile(os.path.join(pbin, "psspy.py"))):
            _add_path(pbin)
            return True
    return False


try:
    import psspy
except ImportError:
    if not _bootstrap_psse():
        raise SystemExit("PSS/E not found for Python %d.%d -- set PSSE_ROOT" % sys.version_info[:2])
    try:
        import psse34          # noqa: F401  (optional shim)
    except Exception:
        pass
    import psspy

# PSS/E's "leave as it is" values, from psspy itself
try:
    _i = psspy.getdefaultint()
    _f = psspy.getdefaultreal()
except Exception:
    _i = getattr(psspy, "_i", -100000000)
    _f = getattr(psspy, "_f", -1.0e20)

_LOG = []


def say(msg=""):
    print(msg)
    _LOG.append(msg)


def ok(rc):
    rc = rc[0] if isinstance(rc, (list, tuple)) else rc
    return rc in (0, None)


# ---------------------------------------------------------------- case reading
def _col(fn, *args):
    """One subsystem-array call -> list of columns, [] on any error."""
    try:
        ie, cols = fn(*args)
    except Exception:
        return []
    if ie not in (0, None) or cols is None:
        return []
    return cols


def _strip(s):
    return str(s).strip()


def read_case(path):
    """Everything this script copies or compares, keyed so two cases line up."""
    if not ok(psspy.case(path)):
        raise SystemExit("could not open %s" % path)
    C = {"path": path, "bus": {}, "mach": {}, "plant": {}, "line": {}, "xf2": {},
         "xf3": [], "load": {}, "fxsh": {}, "swsh": []}
    ints = _col(psspy.abusint, -1, 2, ["NUMBER", "TYPE", "AREA", "ZONE", "OWNER"])
    reals = _col(psspy.abusreal, -1, 2, ["BASE", "PU", "ANGLED", "NVLMHI", "NVLMLO", "EVLMHI", "EVLMLO"])
    names = _col(psspy.abuschar, -1, 2, ["NAME"])
    for k in range(len(ints[0]) if ints else 0):
        C["bus"][int(ints[0][k])] = {
            "type": int(ints[1][k]), "area": int(ints[2][k]), "zone": int(ints[3][k]),
            "owner": int(ints[4][k]), "kv": float(reals[0][k]), "vm": float(reals[1][k]),
            "va": float(reals[2][k]), "lim": [float(reals[n][k]) for n in (3, 4, 5, 6)],
            "name": _strip(names[0][k]) if names else ""}
    mi = _col(psspy.amachint, -1, 4, ["NUMBER", "STATUS", "WMOD"])
    mr = _col(psspy.amachreal, -1, 4, ["PGEN", "QGEN", "QMAX", "QMIN", "PMAX", "PMIN", "MBASE",
                                       "RSOURCE", "XSOURCE", "RTRAN", "XTRAN", "GENTAP", "WPF"])
    mc = _col(psspy.amachchar, -1, 4, ["ID"])
    for k in range(len(mi[0]) if mi else 0):
        key = (int(mi[0][k]), _strip(mc[0][k]))
        C["mach"][key] = {"st": int(mi[1][k]), "wmod": int(mi[2][k]),
                          "r": [float(mr[n][k]) for n in range(len(mr))]}
    gi = _col(psspy.agenbusint, -1, 4, ["NUMBER", "IREG"])
    gr = _col(psspy.agenbusreal, -1, 4, ["VSPU"])
    for k in range(len(gi[0]) if gi else 0):
        C["plant"][int(gi[0][k])] = {"ireg": int(gi[1][k]), "vs": float(gr[0][k]) if gr else 1.0}
    # non-transformer branches (flag 2 = all, in or out of service)
    bi = _col(psspy.abrnint, -1, 1, 1, 2, 1, ["FROMNUMBER", "TONUMBER", "STATUS"])
    bc = _col(psspy.abrnchar, -1, 1, 1, 2, 1, ["ID"])
    bx = _col(psspy.abrncplx, -1, 1, 1, 2, 1, ["RX"])
    br = _col(psspy.abrnreal, -1, 1, 1, 2, 1, ["CHARGING", "RATEA", "RATEB", "RATEC", "LENGTH"])
    for k in range(len(bi[0]) if bi else 0):
        key = (int(bi[0][k]), int(bi[1][k]), _strip(bc[0][k]))
        C["line"][key] = {"st": int(bi[2][k]), "rx": bx[0][k], "b": float(br[0][k]),
                          "rate": [float(br[n][k]) for n in (1, 2, 3)], "len": float(br[4][k])}
    # two-winding transformers (flag 2 = all)
    ti = _col(psspy.atrnint, -1, 1, 1, 2, 1, ["FROMNUMBER", "TONUMBER", "STATUS"])
    tc = _col(psspy.atrnchar, -1, 1, 1, 2, 1, ["ID"])
    tx = _col(psspy.atrncplx, -1, 1, 1, 2, 1, ["RXNOM"]) or _col(psspy.atrncplx, -1, 1, 1, 2, 1, ["RXACT"])
    tr = _col(psspy.atrnreal, -1, 1, 1, 2, 1, ["RATIO", "RATIO2", "ANGLE", "RATEA", "RATEB", "RATEC", "SBASE1"])
    for k in range(len(ti[0]) if ti else 0):
        key = (int(ti[0][k]), int(ti[1][k]), _strip(tc[0][k]))
        C["xf2"][key] = {"st": int(ti[2][k]), "rx": tx[0][k] if tx else None,
                         "ratio": float(tr[0][k]), "ratio2": float(tr[1][k]), "ang": float(tr[2][k]),
                         "rate": [float(tr[n][k]) for n in (3, 4, 5)], "sbase": float(tr[6][k])}
    t3 = _col(psspy.atr3int, -1, 1, 1, 2, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER"])
    t3c = _col(psspy.atr3char, -1, 1, 1, 2, 1, ["ID"])
    for k in range(len(t3[0]) if t3 else 0):
        C["xf3"].append((int(t3[0][k]), int(t3[1][k]), int(t3[2][k]), _strip(t3c[0][k]) if t3c else "1"))
    li = _col(psspy.aloadint, -1, 4, ["NUMBER", "STATUS"])
    lc = _col(psspy.aloadchar, -1, 4, ["ID"])
    lx = _col(psspy.aloadcplx, -1, 4, ["MVANOM"])
    for k in range(len(li[0]) if li else 0):
        C["load"][(int(li[0][k]), _strip(lc[0][k]))] = {"st": int(li[1][k]), "s": lx[0][k] if lx else 0j}
    si = _col(psspy.afxshuntint, -1, 4, ["NUMBER", "STATUS"])
    sc = _col(psspy.afxshuntchar, -1, 4, ["ID"])
    sx = _col(psspy.afxshuntcplx, -1, 4, ["SHUNTNOM"])
    for k in range(len(si[0]) if si else 0):
        C["fxsh"][(int(si[0][k]), _strip(sc[0][k]))] = {"st": int(si[1][k]), "s": sx[0][k] if sx else 0j}
    ws = _col(psspy.aswshint, -1, 4, ["NUMBER"])
    C["swsh"] = [int(b) for b in (ws[0] if ws else [])]
    return C


def area_gen(C, area, skip=()):
    return sum(m["r"][0] for (b, _id), m in C["mach"].items()
               if m["st"] == 1 and C["bus"].get(b, {}).get("area") == area and b not in skip)


# ---------------------------------------------------------------- comparison
def compare(S, T, csv_path):
    rows = []
    for kind, a, b in (("bus", S["bus"], T["bus"]), ("machine", S["mach"], T["mach"]),
                       ("line", S["line"], T["line"]), ("2W transformer", S["xf2"], T["xf2"])):
        for k in sorted(set(a) - set(b), key=str):
            rows.append((kind, "only in SOURCE", k))
        for k in sorted(set(b) - set(a), key=str):
            rows.append((kind, "only in TARGET", k))
    s3, t3 = set(S["xf3"]), set(T["xf3"])
    rows += [("3W transformer", "only in SOURCE", k) for k in sorted(s3 - t3)]
    rows += [("3W transformer", "only in TARGET", k) for k in sorted(t3 - s3)]
    areas = sorted(set(x["area"] for x in S["bus"].values()) | set(x["area"] for x in T["bus"].values()))
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["element", "where", "key"])
        for r in rows:
            w.writerow([r[0], r[1], " ".join(str(x) for x in (r[2] if isinstance(r[2], tuple) else (r[2],)))])
        w.writerow([])
        w.writerow(["area", "SOURCE gen MW", "TARGET gen MW", "difference"])
        for a in areas:
            sa, ta = area_gen(S, a), area_gen(T, a)
            w.writerow([a, "%.1f" % sa, "%.1f" % ta, "%+.1f" % (sa - ta)])
    say("COMPARISON (full list in %s)" % csv_path)
    for kind in ("bus", "machine", "line", "2W transformer", "3W transformer"):
        n1 = sum(1 for r in rows if r[0] == kind and r[1] == "only in SOURCE")
        n2 = sum(1 for r in rows if r[0] == kind and r[1] == "only in TARGET")
        say("  %-15s only in SOURCE %5d   only in TARGET %5d" % (kind, n1, n2))
    say("  generation by area where the two cases differ (MW):")
    for a in areas:
        sa, ta = area_gen(S, a), area_gen(T, a)
        if abs(sa - ta) > 0.05:
            say("    area %-5d SOURCE %9.1f   TARGET %9.1f   %+8.1f" % (a, sa, ta, sa - ta))


# ---------------------------------------------------------------- selection
def _expand(spec):
    out = set()
    for x in spec or []:
        s = str(x).strip()
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", s)
        if m:
            out |= set(range(int(m.group(1)), int(m.group(2)) + 1))
        elif s:
            out.add(int(s))
    return out


def select(S, T):
    new = set(S["bus"]) - set(T["bus"])
    want = _expand(PLANT_BUSES) if PLANT_BUSES else set(new)
    errs = []
    for b in sorted(want):
        if b not in S["bus"]:
            errs.append("bus %d is not in SOURCE" % b)
        elif b in T["bus"]:
            errs.append("bus %d is already in TARGET -- not copied over it" % b)
    have = set(T["bus"]) | want
    lines = {k: v for k, v in S["line"].items() if (k[0] in want or k[1] in want)}
    xf2 = {k: v for k, v in S["xf2"].items() if (k[0] in want or k[1] in want)}
    for k in list(lines) + list(xf2):
        if k[0] not in have or k[1] not in have:
            errs.append("%s %d-%d ck%s ends on bus %d, which is neither selected nor in TARGET"
                        % ("line" if k in lines else "transformer", k[0], k[1], k[2],
                           k[1] if k[0] in have else k[0]))
    xf3 = [k for k in S["xf3"] if any(b in want for b in k[:3])]
    for k in xf3:
        errs.append("3-winding transformer %d-%d-%d ck%s touches the plant: it is not copied "
                    "by this script -- add it by hand (or a RAW append) and rerun with its "
                    "buses excluded" % k)
    for b in sorted(set(S["swsh"]) & want):
        errs.append("switched shunt at bus %d is not copied by this script -- add it by hand" % b)
    mach = {k: v for k, v in S["mach"].items() if k[0] in want}
    if COPY_MACHINES_ON_EXISTING_BUSES:
        mach.update({k: v for k, v in S["mach"].items()
                     if k[0] in T["bus"] and k not in T["mach"]})
    # EVERY COPIED BUS MUST REACH TARGET'S NETWORK through the copied elements.
    adj = {}
    for k in list(lines) + list(xf2):
        adj.setdefault(k[0], set()).add(k[1])
        adj.setdefault(k[1], set()).add(k[0])
    seen = set(b for b in adj if b in T["bus"])
    stack = list(seen)
    while stack:
        b = stack.pop()
        for c in adj.get(b, ()):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    for b in sorted(want - seen):
        errs.append("bus %d would be an island: nothing selected connects it to TARGET" % b)
    return want, mach, lines, xf2, errs


# ---------------------------------------------------------------- adding
def add_bus(b, d):
    intgar = [d["type"] if d["type"] != 3 else 2, d["area"], d["zone"], d["owner"]]
    realar = [d["kv"], d["vm"], d["va"]] + d["lim"]
    for fn, args in (("bus_data_4", (b, 0, intgar, realar, d["name"][:12])),
                     ("bus_data_3", (b, intgar, realar, d["name"][:12])),
                     ("bus_data_2", (b, intgar, realar[:3], d["name"][:12]))):
        if hasattr(psspy, fn):
            try:
                if ok(getattr(psspy, fn)(*args)):
                    return fn
            except Exception:
                continue
    return ""


def add_machine(b, mid, m, plant):
    try:
        if b in plant:
            psspy.plant_data(b, plant[b]["ireg"], [plant[b]["vs"], _f])
        else:
            psspy.plant_data(b, _i, [_f, _f])
    except Exception:
        pass
    r = m["r"]   # PGEN QGEN QMAX QMIN PMAX PMIN MBASE RSOURCE XSOURCE RTRAN XTRAN GENTAP WPF
    realar = [r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11],
              _f, _f, _f, _f, r[12]]
    intgar = [m["st"], _i, _i, _i, _i, m["wmod"]]
    try:
        return ok(psspy.machine_data_2(b, mid, intgar, realar))
    except Exception:
        return False


def add_line(k, v):
    rx = complex(v["rx"])
    ra = [rx.real, rx.imag, v["b"]] + [_f] * 9
    ra[7] = v["len"] if v["len"] > 0 else _f
    rates = (v["rate"] + [_f] * 12)[:12]
    for fn, args in (("branch_data_3", (k[0], k[1], k[2], [v["st"], _i, _i, _i, _i, _i], ra, rates, "")),
                     ("branch_data", (k[0], k[1], k[2], [v["st"], _i, _i, _i, _i, _i],
                                      [rx.real, rx.imag, v["b"]] + v["rate"] + [_f] * 9))):
        if hasattr(psspy, fn):
            try:
                if ok(getattr(psspy, fn)(*args)):
                    return fn
            except Exception:
                continue
    return ""


def add_xf2(k, v):
    """R and X on the SYSTEM base (CZ = 1, the default) and the turns ratios in
       pu of the bus base kV (CW = 1, the default), as SOURCE solved them: the
       copy has the same impedance and the same effective ratio. Tap control is
       not copied -- the ratio is fixed at SOURCE's value (said in the log)."""
    if v["rx"] is None:
        return ""
    rx = complex(v["rx"])
    intgar = [_i] * 15
    intgar[0] = v["st"]
    realari = [_f] * 21
    realari[0], realari[1], realari[2] = rx.real, rx.imag, v["sbase"] if v["sbase"] > 0 else _f
    realari[3] = v["ratio"]          # WINDV1
    realari[5] = v["ang"]            # ANG1
    realari[6] = v["ratio2"]         # WINDV2
    rates = (v["rate"] + [_f] * 12)[:12]
    for fn in ("two_winding_data_6", "two_winding_data_5"):
        if hasattr(psspy, fn):
            try:
                if ok(getattr(psspy, fn)(k[0], k[1], k[2], intgar, realari, rates, "", "")):
                    return fn
            except Exception:
                continue
    return ""


def add_load(k, v):
    s = complex(v["s"])
    try:
        return ok(psspy.load_data_5(k[0], k[1], [v["st"], _i, _i, _i, _i, _i, _i],
                                    [s.real, s.imag, _f, _f, _f, _f, _f, _f], ""))
    except Exception:
        return False


def add_fxsh(k, v):
    s = complex(v["s"])
    try:
        return ok(psspy.shunt_data(k[0], k[1], v["st"], [s.real, s.imag]))
    except Exception:
        return False


def solve():
    psspy.fdns(SOLVE_OPTS)
    s = psspy.solved()
    s = s[0] if isinstance(s, (list, tuple)) else s
    if s not in (0, None):
        psspy.fnsl(SOLVE_OPTS)
        s = psspy.solved()
        s = s[0] if isinstance(s, (list, tuple)) else s
    return s in (0, None)


# ---------------------------------------------------------------- area hold
def live_area_machines(area, skip):
    out = []
    mi = _col(psspy.amachint, -1, 1, ["NUMBER"])
    mc = _col(psspy.amachchar, -1, 1, ["ID"])
    mr = _col(psspy.amachreal, -1, 1, ["PGEN", "PMAX", "PMIN"])
    for k in range(len(mi[0]) if mi else 0):
        b = int(mi[0][k])
        ie, ar = psspy.busint(b, "AREA")
        if ie not in (0, None) or int(ar) != int(area):
            continue
        out.append((b, _strip(mc[0][k]), float(mr[0][k]), float(mr[1][k]), float(mr[2][k]), b in skip))
    return out


def hold_areas(before, skip):
    for k in range(1, int(HOLD_PASSES) + 1):
        worst = 0.0
        for area, want in sorted(before.items()):
            ms = live_area_machines(area, skip)
            now = sum(m[2] for m in ms)
            diff = now - want
            worst = max(worst, abs(diff))
            if abs(diff) <= HOLD_TOL_MW:
                continue
            free = [m for m in ms if not m[5]]
            tot = sum(m[2] for m in free)
            if tot <= 0 or tot - diff <= 0:
                say("  [hold] area %d is %+.1f MW out and has nothing left to scale" % (area, diff))
                continue
            kf = (tot - diff) / tot
            say("  [hold] pass %d: area %d at %.1f MW, target %.1f (%+.1f) -- scaling %d machine(s) by %.5f"
                % (k, area, now, want, diff, len(free), kf))
            for b, mid, p, pmax, pmin, _sk in free:
                p1 = p * kf
                if RESPECT_LIMITS:
                    p1 = max(min(pmin, pmax), min(max(pmin, pmax), p1))
                psspy.machine_chng_2(b, mid, [_i] * 6, [p1] + [_f] * 16)
        if worst <= HOLD_TOL_MW:
            return True
        if not solve():
            say("  [hold] *** the case did not solve after pass %d ***" % k)
            return False
    return False


# ---------------------------------------------------------------- .dyr
def dyr_records(path):
    """[(bus, model, id, text)] -- PSS/E .dyr grammar, a record ends at '/'."""
    with open(path, "r", errors="replace") as fh:
        txt = "\n".join(ln for ln in fh.read().splitlines() if not ln.strip().startswith("@!"))
    out = []
    for chunk in txt.split("/")[:-1]:
        raw = chunk.strip()
        if not raw:
            continue
        tok = raw.replace(",", " ").split()
        try:
            bus = int(tok[0])
        except Exception:
            continue
        model = tok[1].strip("'\"").upper() if len(tok) > 1 else ""
        mid = tok[2].strip("'\"") if len(tok) > 2 else ""
        out.append((bus, model, mid, raw + " /"))
    return out


def copy_dyr(mach, out_dyr):
    recs = dyr_records(SOURCE_DYR)
    keys = set((b, _strip(mid)) for (b, mid) in mach)
    pick = [r for r in recs if (r[0], _strip(r[2])) in keys]
    lines = []
    if TARGET_DYR:
        with open(TARGET_DYR, "r", errors="replace") as fh:
            lines.append(fh.read().rstrip() + "\n")
    lines.append("@! ---- copied from %s by z7_plant_copy.py, %s\n"
                 % (os.path.basename(SOURCE_DYR), time.strftime("%Y-%m-%d %H:%M")))
    lines += [r[3] + "\n" for r in pick]
    with open(out_dyr, "w") as fh:
        fh.writelines(lines)
    missing = sorted(k for k in keys if not any((r[0], _strip(r[2])) == k for r in pick))
    say("DYR: %d record(s) for %d copied machine(s) -> %s" % (len(pick), len(keys), out_dyr))
    for b, mid in missing:
        say("  *** no record in SOURCE_DYR for machine %d '%s' ***" % (b, mid))


# ---------------------------------------------------------------- main
def main():
    global SOURCE_SAV, TARGET_SAV
    if len(sys.argv) >= 3:
        SOURCE_SAV, TARGET_SAV = sys.argv[1], sys.argv[2]
    for p in (SOURCE_SAV, TARGET_SAV):
        if not os.path.isfile(p):
            raise SystemExit("not found: %s" % p)
    psspy.psseinit(200000)
    try:
        psspy.report_output(6, "", [0, 0])
        psspy.progress_output(6, "", [0, 0])
    except Exception:
        pass
    say("z7_plant_copy  %s" % time.strftime("%Y-%m-%d %H:%M"))
    say("SOURCE %s" % SOURCE_SAV)
    say("TARGET %s" % TARGET_SAV)
    S = read_case(SOURCE_SAV)
    T = read_case(TARGET_SAV)
    want, mach, lines, xf2, errs = select(S, T)
    stem = os.path.splitext(TARGET_SAV)[0]
    out = OUT_SAV or "%s_plus_%dbus.sav" % (stem, len(want))
    out_stem = os.path.splitext(out)[0]
    compare(S, T, out_stem + "_compare.csv")
    say("")
    say("SELECTED: %d bus(es), %d machine(s), %d line(s), %d two-winding transformer(s)"
        % (len(want), len(mach), len(lines), len(xf2)))
    if want:
        say("  buses: %s" % ", ".join(str(b) for b in sorted(want)))
    if errs:
        say("")
        say("*** NOT COPIED -- fix these first (nothing was changed or saved):")
        for e in errs:
            say("  - " + e)
        _write_log(out_stem + ".txt")
        raise SystemExit(1)
    if not want and not mach:
        say("nothing to copy: TARGET already has every bus and machine of SOURCE")
        _write_log(out_stem + ".txt")
        return
    # TARGET as it is: its area MW is what the held areas go back to
    psspy.case(TARGET_SAV)
    areas = sorted(set(HOLD_AREAS) if HOLD_AREAS else
                   set(S["bus"][b]["area"] for (b, _m) in mach if b in S["bus"]))
    before = dict((a, area_gen(T, a)) for a in areas)
    say("")
    say("ADDING to TARGET")
    n_bad = 0
    for b in sorted(want):
        fn = add_bus(b, S["bus"][b])
        say("  bus  %-7d %-12s %7.1f kV area %-4d %s" % (b, S["bus"][b]["name"], S["bus"][b]["kv"],
                                                         S["bus"][b]["area"], fn or "*** FAILED ***"))
        n_bad += not fn
    for k, v in sorted(lines.items()):
        fn = add_line(k, v)
        say("  line %d-%d ck%s R=%.5f X=%.5f B=%.5f %s" % (k[0], k[1], k[2], complex(v["rx"]).real,
                                                       complex(v["rx"]).imag, v["b"], fn or "*** FAILED ***"))
        n_bad += not fn
    for k, v in sorted(xf2.items()):
        fn = add_xf2(k, v)
        say("  xfmr %d-%d ck%s R=%.5f X=%.5f (system base) ratio %.4f/%.4f, fixed tap %s"
            % (k[0], k[1], k[2], complex(v["rx"] or 0).real, complex(v["rx"] or 0).imag,
               v["ratio"], v["ratio2"], fn or "*** FAILED ***"))
        n_bad += not fn
    for (b, mid), m in sorted(mach.items()):
        good = add_machine(b, mid, m, S["plant"])
        say("  gen  %d '%s' P=%.1f Q=%.1f Pmax=%.1f MBASE=%.1f %s" % (b, mid, m["r"][0], m["r"][1], m["r"][4],
                                                                   m["r"][6], "" if good else "*** FAILED ***"))
        n_bad += not good
    for k, v in sorted(S["load"].items()):
        if k[0] in want:
            good = add_load(k, v)
            say("  load %d '%s' %.1f + j%.1f %s" % (k[0], k[1], complex(v["s"]).real, complex(v["s"]).imag,
                                                    "" if good else "*** FAILED ***"))
            n_bad += not good
    for k, v in sorted(S["fxsh"].items()):
        if k[0] in want:
            good = add_fxsh(k, v)
            say("  shunt %d '%s' %.1f + j%.1f MVA %s" % (k[0], k[1], complex(v["s"]).real, complex(v["s"]).imag,
                                                         "" if good else "*** FAILED ***"))
            n_bad += not good
    if n_bad:
        say("*** %d element(s) could not be added -- NOT saved ***" % n_bad)
        _write_log(out_stem + ".txt")
        raise SystemExit(1)
    say("")
    if not solve():
        say("*** the case with the plant added does not solve -- NOT saved ***")
        _write_log(out_stem + ".txt")
        raise SystemExit(1)
    held = True
    if HOLD_AREA_MW and before:
        skip = set(b for (b, _m) in mach) | _expand(HOLD_EXCLUDE) | \
            set(b for b, d in T["bus"].items() if d["type"] == 3)
        held = hold_areas(before, skip)
    say("")
    say("AREA GENERATION (MW)        TARGET before    OUT after    difference")
    for a in areas:
        now = sum(m[2] for m in live_area_machines(a, ()))
        say("  area %-5d            %12.1f %12.1f %+12.1f" % (a, before[a], now, now - before[a]))
    copied = sum(m["r"][0] for m in mach.values() if m["st"] == 1)
    say("  copied machines carry %.1f MW; the other machines in the held area(s) were scaled down by that" % copied)
    if not held:
        say("  *** an area is still outside %.2f MW after %d passes -- see the [hold] lines ***"
            % (HOLD_TOL_MW, HOLD_PASSES))
    if not ok(psspy.save(out)):
        say("*** could not save %s ***" % out)
        raise SystemExit(1)
    say("")
    say("SAVED %s" % out)
    if SOURCE_DYR:
        copy_dyr(mach, OUT_DYR or out_stem + ".dyr")
    _write_log(out_stem + ".txt")


def _write_log(path):
    try:
        with open(path, "w") as fh:
            fh.write("\n".join(_LOG) + "\n")
        print("log: %s" % path)
    except Exception as e:
        print("could not write %s (%s)" % (path, e))


if __name__ == "__main__":
    main()
