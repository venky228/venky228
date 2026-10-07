# -*- coding: utf-8 -*-
"""z6_overlay.py -- BASE, SCENARIO 1 and SCENARIO 2 on the same axes, per fault.

    Base case                 the system without the surplus facility
    Scenario 1 (GIA)          SGF + EGF, POI held at the GIA amount
    Scenario 2                SGF on, EGF off

For every fault found in the case folders it reads the three .out files and
writes ONE PDF in which each panel carries all three traces:

    faulted-bus voltage, POI voltage, POI P and Q (sum of the plant ties),
    each SGF unit's P / Q / terminal voltage (Scenario 1 and 2 only), and
    each EGF unit's P / Q / terminal voltage.

The three cases are told apart by colour AND line style AND width, so the plot
still reads printed in black and white or by a colour-blind reader:

    Base         black       dashed      ----
    Scenario 1   blue        solid       ____
    Scenario 2   vermilion   dash-dot    -.-.

Time is measured from the fault (0 s = fault applied), so runs with different
pre-fault times line up. Each legend entry carries that case's final value.

RUN (PSS/E Python 3.4, needs dyntools and matplotlib):
    python z6_overlay.py
Edit the SETTINGS block first. Nothing is simulated; the .out files are only
read, and nothing in the results folders is changed.
"""
import os
import re
import sys
import glob
import time

# ============================================================================
#   SETTINGS
# ============================================================================
ROOT = r"C:\KV\ENGIE\CQ_MIT_f"          # the study folder holding Base\ and Projects\
PROJECTS = ["SantaFe", "IronStar", "EmpirePrairie", "EastFork"]   # all 4 (remove any you do not want)
MODE = "spp"
FAULTS = []                              # [] = every fault with an .out in Scenario 1 | e.g. ["F134", "F01"]

# The three runs: label, results root (under ROOT), folder suffix, colour, style, width.
# Folder = <results root>\<project>\<project>_<mode><suffix>  (or without the
# <project>\ level -- both layouts are searched).
# Styles are chosen to stay readable when the traces lie on top of each other:
# the base case is a wide grey band underneath, scenario 1 a solid blue line on
# it, scenario 2 an orange dashed line on top -- all three remain visible.
# (label, results root, folder suffix, colour, line style, width, opacity)
CASES = [
    ("Base case",                          r"Base\results_base_f",     "",           "#8C8C8C", "-",  4.0, 0.55),
    ("Scenario 1: SGF + EGF (GIA)",        r"Projects\results_proj_f", "",           "#1F5AA6", "-",  1.5, 1.0),
    ("Scenario 2: SGF on, EGF off",        r"Projects\results_proj_f", "_s1_egfoff", "#E8590C", "--", 1.5, 1.0),
]

PAGE_SUBTITLE = "Base Case vs Scenario 1 vs Scenario 2"   # right of the page title
FOOTER_TEXT = "SPP Dynamic Stability Study -- Surplus Interconnection"   # bottom left of every page
SHOW_END_VALUES = True                   # each panel's value at T_MAX per case, small, top right of the panel
PDF_NAME = "{project}_{fault}.pdf"        # file name per fault, e.g. SantaFe_F01.pdf ({project}, {fault}, {mode})
OUT_DIR = r"overlay_plots"               # under ROOT unless absolute
ALIGN_TO_FAULT = False                   # False = simulation time, as the study plots | True = time from the fault (0 s = fault)
T_MIN, T_MAX = 0.0, 20.0                 # seconds shown; None = the whole record
PANELS_PER_PAGE = 4
MAX_POINTS = 4000                        # points per trace (thinned evenly above this)
PLOT_QUANTITIES = ["V", "P", "Q"]        # which quantities: "V" voltage (pu), "P" (MW), "Q" (MVAr) -- e.g. ["V"] or ["P", "Q"]
PLOT_GROUPS = ["FAULT", "POI", "SGF", "EGF"]   # which panels: faulted bus, POI, SGF units, EGF units -- e.g. ["POI"]
INCLUDE_SGF_UNITS = True                 # per-unit SGF P / Q / ETERM
INCLUDE_EGF_UNITS = True                 # per-unit EGF P / Q / ETERM
EXTRA_TITLE_REGEX = []                   # more channels by .out title, e.g. [r"^GEN584713_PELEC$"]
SBASE_MVA = 100.0                        # machine PELEC / QELEC are pu on the system base
FAST_READ = True                         # True = the study's fast packed reader where its verified layout fits (seconds, not minutes)
WORKERS = 1                              # faults drawn at once, each in its own process (1 = one at a time)
USE_CACHE = True                         # keep each .out's traces in <OUT_DIR>\cache: a re-run only reads new / changed .out files
STATUS_FILE = "OVERLAY_STATUS.txt"       # live progress, in OUT_DIR ("" = off)
SHOW_NODES = True                        # "N nodes from fault bus, area" on each panel (from flags\BUS_MAP.csv)
SGF_UNIT_BUS_START = 999001              # SGF unit n is bus SGF_UNIT_BUS_START + n - 1 (NEW_PLANT "bus_start")
PSSE_DIRS = [r"C:\Program Files (x86)\PTI\PSSE34\PSSPY34",
             r"C:\Program Files (x86)\PTI\PSSE34\PSSBIN",
             r"C:\Program Files\PTI\PSSE34\PSSPY34",
             r"C:\Program Files\PTI\PSSE34\PSSBIN"]
# ============================================================================


def _psse_path():
    for d in PSSE_DIRS:
        if os.path.isdir(d):
            if d not in sys.path:
                sys.path.insert(0, d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def _import_dyntools():
    _psse_path()
    try:
        import psse34  # noqa: F401  (sets the PSS/E 34 paths when it exists)
    except Exception:
        pass
    try:
        import dyntools
        return dyntools
    except Exception as e:
        print("[overlay] dyntools could not be imported (%s) -- only files the fast reader "
              "can read will be drawn. Run this with the PSS/E Python, or add its PSSPY34 "
              "folder to PSSE_DIRS." % e)
        return None


def _abs(p):
    p = str(p).replace("\\", os.sep).replace("/", os.sep)     # \ or / both work
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def _case_dir(res_root, proj, suffix):
    """ONLY <results root>\<project>\<project>_<mode><suffix>, or the same folder
       directly under the results root. Nothing else is searched: a backup or
       any other folder beside the project's (results_base_f\backup\..., an
       old copy, another project) is never read."""
    name = "%s_%s%s" % (proj, MODE, suffix)
    root = _abs(res_root)
    for d in (os.path.join(root, proj, name), os.path.join(root, name)):
        if not os.path.isdir(d):
            continue
        rel = os.path.relpath(d, root).split(os.sep)
        if rel not in ([proj, name], [name]):
            continue
        return d
    return None


def _faults(dirs):
    """Faults to draw: FAULTS, else every .out in Scenario 1 (or the first case found)."""
    if FAULTS:
        return list(FAULTS)
    for d in dirs[1:] + dirs[:1]:
        if d:
            got = sorted(os.path.splitext(os.path.basename(p))[0]
                         for p in glob.glob(os.path.join(d, "outs", "*.out")))
            got = [f for f in got if not f.upper().startswith("FLAT")]
            if got:
                return got
    return []


# ---- what is drawn ---------------------------------------------------------
# PSS/E stores channel titles in UPPER case, so every pattern ignores case.
# A POI tie channel ends in P<plant bus> (metered at the POI: the flow LEAVING
# the POI, turned round here) or F<plant bus> (older runs: flow INTO the POI).
_RX_FLT = re.compile(r"^FLT(\d+) V$", re.I)
_RX_POIV = re.compile(r"^POI ?(\d+) V$", re.I)
_RX_POIP = re.compile(r"^POI POWR (\d+) MW ([PF])(\d+)", re.I)
_RX_POIQ = re.compile(r"^POI VARS (\d+) MVAR ([PF])(\d+)", re.I)
_RX_SGF = re.compile(r"^PROJ(\d+)_(PELEC|QELEC|ETERM)$", re.I)
_RX_EGF = re.compile(r"^XGEN(\d+)(?:_(\w{1,2}))?_(PELEC|QELEC|ETERM)$", re.I)
_QTY = {"PELEC": ("P", "MW"), "QELEC": ("Q", "MVAr"), "ETERM": ("terminal V", "pu")}


# ---- the fast .out reader ---------------------------------------------------------
# The study's own packed reader, cut down to what this script needs. A .out is
# read as raw 32-bit words using a layout the STUDY has already verified against
# dyntools and saved beside the files (outs\OUT_LAYOUT_<channels>.txt). A file is
# read this way only when (1) its header titles match a saved layout's to 90 %,
# (2) its time column starts near 0 and rises at a constant step, and (3) the
# regular time grid runs to the end of the file. Anything else -> dyntools.
_STATS = {"fast": 0, "dyntools": 0}
_LAYOUTS = {}


def _load_layouts(folder):
    if folder in _LAYOUTS:
        return _LAYOUTS[folder]
    lays = []
    for d in (os.path.join(folder, "outs"), folder):
        for p in sorted(glob.glob(os.path.join(d, "OUT_LAYOUT*.txt"))):
            try:
                txt = open(p, "r").read()
            except Exception:
                continue
            if not re.search(r"^verified\s*=\s*yes", txt, re.M):
                continue
            m = re.search(r"^stride\s*=\s*(\d+)", txt, re.M)
            b = re.search(r"^base\s*=\s*(\d+)", txt, re.M)
            tw = re.search(r"^twidth\s*=\s*(\d+)", txt, re.M)
            tr = re.search(r"^trailer\s*=\s*(\d+)", txt, re.M)
            if not (m and b and tw):
                continue
            offs, tits = {}, {}
            for line in txt.splitlines():
                mm = re.match(r"^off\s+(\S+)\s*=\s*(\d+)$", line.strip())
                if mm:
                    k = mm.group(1)
                    offs["time" if k == "time" else (int(k) if k.isdigit() else k)] = int(mm.group(2))
                    continue
                mt = re.match(r"^tit\s+(\S+)\s+(\d+)\s(.*)$", line.rstrip("\r\n"))
                if mt:
                    k = mt.group(1)
                    tits[int(k) if k.isdigit() else k] = (int(mt.group(2)), " ".join(mt.group(3).split()))
            if offs.get("time") is not None and tits:
                lays.append((int(b.group(1)), int(m.group(1)), offs, int(tw.group(1)),
                             int(tr.group(1)) if tr else (1 << 16), tits))
    _LAYOUTS[folder] = lays
    return lays


def _fast_read(path):
    """(t, {key: title}, {key: values}) for the wanted channels, or None."""
    import array
    import struct
    lays = _load_layouts(os.path.dirname(os.path.dirname(path)))
    if not lays:
        return None
    try:
        size = os.path.getsize(path)
    except Exception:
        return None
    for base, stride, offs, width, trailer, tits in lays:
        need = max(p for p, _t in tits.values()) + width
        if need > base * 4 or stride <= 0:
            continue
        try:
            with open(path, "rb") as fh:
                hdr = fh.read(need)
        except Exception:
            return None
        cid, same, ok = {}, 0, True
        for k, (p, ref) in tits.items():
            try:
                tx = " ".join(hdr[p:p + width].rstrip(b" \0").decode("ascii").split())
            except Exception:
                ok = False
                break
            if not tx or not all(32 <= ord(c) < 127 for c in tx):
                ok = False
                break
            cid[k] = tx
            same += (tx == ref)
        if not ok or same < 0.9 * len(tits):
            continue
        n = size // 4
        raw = array.array("I")
        try:
            with open(path, "rb") as fh:
                raw.fromfile(fh, n)
        except EOFError:
            pass
        except Exception:
            return None
        n = len(raw)

        def col(o, cnt):
            u = raw[o: o + (cnt - 1) * stride + 1: stride]
            f = array.array("f")
            f.frombytes(u.tobytes())
            return [x if x == x and abs(x) < 3e38 else float("nan") for x in f]
        t_off = offs["time"]
        n_max = (n - 1 - t_off) // stride + 1
        if n_max < 8:
            continue
        tall = col(t_off, n_max)
        a, b2 = tall[0], tall[1]
        step = b2 - a
        if not (-1.0 <= a <= 1.0 and 1e-6 < step < 10.0):
            continue
        ns, jump = 1, max(10.0 * step, 1.0)
        while ns < n_max:
            d = tall[ns] - tall[ns - 1]
            if d != d or d < 0 or d > jump:
                break
            ns += 1
        if ns < 8:
            continue
        leftover = n - (t_off + (ns - 1) * stride + 1)
        if leftover < 0 or leftover > trailer + stride:
            return None               # the grid breaks before the end: dyntools reads it properly
        t = tall[:ns]
        ids, data = {}, {}
        for k, title in cid.items():
            if k == "time" or not _wanted(title):
                continue
            o = offs.get(k)
            if o is None or o + (ns - 1) * stride >= n:
                return None
            ids[k] = title
            data[k] = col(o, ns)
        return t, ids, data
    return None


def _wanted(title):
    """True for a channel title this script draws."""
    ttl = str(title).strip()
    if (_RX_FLT.match(ttl) or _RX_POIV.match(ttl) or _RX_POIP.match(ttl)
            or _RX_POIQ.match(ttl)):
        return True
    if INCLUDE_SGF_UNITS and _RX_SGF.match(ttl):
        return True
    if INCLUDE_EGF_UNITS and _RX_EGF.match(ttl):
        return True
    return any(re.search(x, ttl) for x in EXTRA_TITLE_REGEX)


def _read(dyntools, path):
    """(t, {panel: values}) for one .out: the fast packed read when this
       folder's verified layout fits the file, else dyntools."""
    got = _fast_read(path) if FAST_READ else None
    if got is not None:
        t, ids, data = got
        _STATS["fast"] += 1
    else:
        if dyntools is None:
            raise RuntimeError("no verified fast layout fits %s and dyntools is not available"
                               % os.path.basename(path))
        chnf = dyntools.CHNF(path)
        _sh, ids, data = chnf.get_data()
        t = list(data["time"])
        _STATS["dyntools"] += 1
    return _extract(t, ids, data)


def _extract(t, ids, data):
    out, poip, poiq = {}, [], []
    extra = [re.compile(x) for x in EXTRA_TITLE_REGEX]
    for k, title in ids.items():
        if k == "time":
            continue
        ttl = str(title).strip()
        v = data.get(k)
        if v is None:
            continue
        m = _RX_FLT.match(ttl)
        if m:
            out["Faulted bus %s voltage (pu)" % m.group(1)] = list(v)
            continue
        m = _RX_POIV.match(ttl)
        if m:
            out["POI %s voltage (pu)" % m.group(1)] = list(v)
            continue
        m = _RX_POIP.match(ttl)
        if m and "GRID" not in ttl.upper() and "GRD" not in ttl.upper():
            poip.append((m.group(1), m.group(2).upper(), v))
            continue
        m = _RX_POIQ.match(ttl)
        if m and "GRID" not in ttl.upper() and "GRD" not in ttl.upper():
            poiq.append((m.group(1), m.group(2).upper(), v))
            continue
        m = _RX_SGF.match(ttl)
        if m and INCLUDE_SGF_UNITS:
            q, u = _QTY[m.group(2).upper()]
            sc = SBASE_MVA if u != "pu" else 1.0
            out["SGF unit %s %s (%s)" % (m.group(1), q, u)] = [x * sc for x in v]
            continue
        m = _RX_EGF.match(ttl)
        if m and INCLUDE_EGF_UNITS:
            q, u = _QTY[m.group(3).upper()]
            sc = SBASE_MVA if u != "pu" else 1.0
            unit = m.group(1) + (" '%s'" % m.group(2) if m.group(2) else "")
            out["EGF %s %s (%s)" % (unit, q, u)] = [x * sc for x in v]
            continue
        if any(rx.search(ttl) for rx in extra):
            out[ttl] = list(v)
    # Delivered INTO the POI: a 'P' tie (metered leaving the POI) is turned
    # round, an 'F' tie (metered into the POI) is taken as it is.
    for lst, nm, u in ((poip, "P", "MW"), (poiq, "Q", "MVAr")):
        if lst:
            poi = lst[0][0]
            n = len(t)
            tot = [0.0] * n
            for _b, d, v in lst:
                sg = -1.0 if d == "P" else 1.0
                for i in range(min(n, len(v))):
                    tot[i] += sg * v[i]
            out["POI %s %s delivered, sum of %d tie(s) (%s)" % (poi, nm, len(lst), u)] = tot
    return t, out


def _read_cached(dyntools, path, ci):
    """_read(), kept in <OUT_DIR>\cache keyed by the .out's size and time, so a
       second run with other plot settings reads nothing it already has."""
    if not USE_CACHE:
        return _read(dyntools, path)
    import pickle
    st = os.stat(path)
    key = "v2|%s|%d|%d|%s|%s|%s" % (os.path.abspath(path), st.st_size, int(st.st_mtime),
                                 INCLUDE_SGF_UNITS, INCLUDE_EGF_UNITS, "|".join(EXTRA_TITLE_REGEX))
    cdir = os.path.join(_abs(OUT_DIR), "cache")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.relpath(os.path.abspath(path), ROOT)) + ".pkl"
    cp = os.path.join(cdir, name)
    try:
        with open(cp, "rb") as fh:
            k, t, sig = pickle.load(fh)
        if k == key:
            return t, sig
    except Exception:
        pass
    t, sig = _read(dyntools, path)
    try:
        if not os.path.isdir(cdir):
            os.makedirs(cdir)
        with open(cp + ".tmp", "wb") as fh:
            pickle.dump((key, t, sig), fh, protocol=2)
        if os.path.exists(cp):
            os.remove(cp)
        os.rename(cp + ".tmp", cp)
    except Exception:
        pass
    return t, sig


def _fault_time(t, sig):
    """First instant the faulted-bus voltage drops below 90 % of its start."""
    for k, v in sig.items():
        if k.startswith("Faulted bus") and v:
            v0 = v[0]
            for i, x in enumerate(v):
                if x < 0.9 * v0:
                    return t[max(0, i - 1)]
    return None


def _thin(t, v):
    n = min(len(t), len(v))
    if MAX_POINTS and n > MAX_POINTS:
        s = int(n / MAX_POINTS) + 1
        return t[:n:s], v[:n:s]
    return t[:n], v[:n]


# ---- distance from the fault bus ------------------------------------------------
def _bus_map(dirs):
    """(adjacency, {bus: (kV, area, name)}, {area: name}) from the first
       flags\BUS_MAP.csv found -- Scenario 1 first, since it also holds the
       SGF buses. The branches are the PRE-fault network, as in the study PDFs."""
    for d in dirs[1:] + dirs[:1]:
        if not d:
            continue
        p = os.path.join(d, "flags", "BUS_MAP.csv")
        if not os.path.isfile(p):
            continue
        adj, bus, area = {}, {}, {}
        with open(p) as fh:
            for ln in fh:
                f = ln.rstrip("\r\n").split(",")
                try:
                    if f[0] == "B" and len(f) >= 4:
                        bus[int(f[1])] = (float(f[2]), f[3], ",".join(f[4:]).strip())
                    elif f[0] == "A" and len(f) >= 3:
                        area[f[1]] = ",".join(f[2:]).strip()
                    elif f[0] == "L" and len(f) >= 3:
                        a, b = int(f[1]), int(f[2])
                        adj.setdefault(a, set()).add(b)
                        adj.setdefault(b, set()).add(a)
                except ValueError:
                    continue
        return adj, bus, area
    return None


def _hops(adj, src, limit=40):
    dist, frontier = {src: 0}, [src]
    for k in range(1, limit + 1):
        nxt = []
        for x in frontier:
            for y in adj.get(x, ()):
                if y not in dist:
                    dist[y] = k
                    nxt.append(y)
        if not nxt:
            break
        frontier = nxt
    return dist


def _panel_bus(name):
    m = re.match(r"^(?:Faulted bus|POI) (\d+)", name)
    if m:
        return int(m.group(1))
    m = re.match(r"^SGF unit (\d+) ", name)
    if m:
        return SGF_UNIT_BUS_START + int(m.group(1)) - 1
    m = re.match(r"^EGF (\d+)", name)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{4,6})", name)
    return int(m.group(1)) if m else None


def _where(name, fbus, bmap, dist):
    """'1 node from fault bus 531469, AREA 534 SUNC' -- as the study PDFs say it."""
    b = _panel_bus(name)
    if b is None or bmap is None:
        return ""
    _adj, bus, area = bmap
    ar = ""
    if b in bus:
        an = bus[b][1]
        ar = ", AREA %s %s" % (an, area.get(an, "")) if an else ""
        ar = ar.rstrip()
    if fbus is None:
        return ar.lstrip(", ")
    if b == fbus:
        return "at fault bus%s" % ar
    if b in dist:
        n = dist[b]
        return "%d node%s from fault bus %d%s" % (n, "" if n == 1 else "s", fbus, ar)
    return "more than 40 nodes from fault bus %d%s" % (fbus, ar)


def _cap(text):
    """First letter of every word in capitals; units in brackets left alone:
       'POI 765911 voltage (pu)' -> 'POI 765911 Voltage (pu)'."""
    out = []
    for w in str(text).split(" "):
        if w and not w.startswith("(") and w[0].islower():
            w = w[0].upper() + w[1:]
        out.append(w)
    return " ".join(out)


def _fmt_end(v):
    """An end value to a sensible number of digits for its size."""
    try:
        a = abs(float(v))
    except Exception:
        return str(v)
    if a != a:
        return "n/a"
    return ("%.0f" % v) if a >= 100 else (("%.1f" % v) if a >= 10 else "%.3f" % v)


def _short(label):
    """'Scenario 1: SGF + EGF (GIA)' -> 'Scenario 1'; 'Base case' stays."""
    return str(label).split(":")[0].strip()


def _panel_on(name):
    """PLOT_QUANTITIES / PLOT_GROUPS: is this panel wanted? The faulted-bus
       voltage is still READ (it finds the fault time) even when not drawn."""
    u = _ylabel(name)
    qty = "V" if u.startswith("Voltage") else ("P" if u.startswith("P ") else ("Q" if u.startswith("Q ") else "OTHER"))
    if qty != "OTHER" and qty not in [str(x).upper() for x in (PLOT_QUANTITIES or [])]:
        return False
    grp = ("FAULT" if name.startswith("Faulted bus") else "POI" if name.startswith("POI")
           else "SGF" if name.startswith("SGF") else "EGF" if name.startswith("EGF") else "OTHER")
    return grp == "OTHER" or grp in [str(x).upper() for x in (PLOT_GROUPS or [])]


def _ylabel(name):
    """'Voltage (pu)', 'P (MW)', 'Q (MVAr)' -- from the unit the panel name ends with."""
    m = re.search(r"\(([^()]*)\)\s*$", name)
    u = (m.group(1) if m else "").strip()
    if u == "pu":
        return "Voltage (pu)"
    if u == "MW":
        return "P (MW)"
    if u.upper() == "MVAR":
        return "Q (MVAr)"
    return u or ""


def _order(name):
    """Faulted bus, POI V, POI P, POI Q, then the SGF units, then the EGF units."""
    if name.startswith("POI"):
        return (1, 0 if "voltage" in name else (1 if " P " in name else 2), name)
    for i, p in enumerate(("Faulted bus", "", "SGF", "EGF")):
        if p and name.startswith(p):
            return (i, 0, name)
    return (9, 0, name)


def draw_fault(proj, fault, dirs, dyntools, plt, PdfPages):
    traces = []                   # (case index, t, {panel: values})
    fault_t = [None]              # fault instant on the plotted time axis
    for ci, d in enumerate(dirs):
        if not d:
            continue
        p = os.path.join(d, "outs", "%s.out" % fault)
        if not os.path.isfile(p):
            print("    %-30s %s: no .out" % (CASES[ci][0], fault))
            continue
        try:
            t, sig = _read_cached(dyntools, p, ci)
        except Exception as e:
            print("    %-30s %s: could not read (%s)" % (CASES[ci][0], fault, e))
            continue
        tf = _fault_time(t, sig)
        if ALIGN_TO_FAULT and tf is not None:
            t = [x - tf for x in t]
            tf = 0.0
        traces.append((ci, t, sig))
        if fault_t[0] is None and tf is not None:
            fault_t[0] = tf
    if not traces:
        print("    %s: no case has an .out -- skipped" % fault)
        return None
    # THE BASE CASE HAS NO SGF. Its "project machines" (PROJ1, PROJ2, ...) are
    # the EXISTING units at the plant's feeders, in feeder order -- so they are
    # drawn on the EGF panels (by the EGF buses the other cases name, ascending),
    # never on the SGF ones.
    egf_buses = sorted(set(int(m.group(1)) for _c, _t, sg in traces for k in sg
                           for m in [re.match(r"^EGF (\d+)", k)] if m))
    for j, (ci, t, sg) in enumerate(traces):
        if "base" not in str(CASES[ci][1]).lower():
            continue
        new = {}
        for k, v in sg.items():
            m = re.match(r"^SGF unit (\d+) (.*)$", k)
            if not m:
                new[k] = v
                continue
            n = int(m.group(1))
            if INCLUDE_EGF_UNITS and 1 <= n <= len(egf_buses):
                new["EGF %d %s" % (egf_buses[n - 1], m.group(2))] = v
        traces[j] = (ci, t, new)
    panels = sorted(set(k for _c, _t, s in traces for k in s), key=_order)
    panels = [k for k in panels if _panel_on(k)]
    if not panels:
        print("    %s: nothing left to draw with PLOT_QUANTITIES = %s, PLOT_GROUPS = %s"
              % (fault, PLOT_QUANTITIES, PLOT_GROUPS))
        return None
    bmap, fbus, dist = (_bus_map(dirs) if SHOW_NODES else None), None, {}
    for k in set(k for _c, _t, sg in traces for k in sg):      # before PLOT_GROUPS filtered it out
        m = re.match(r"^Faulted bus (\d+)", k)
        if m:
            fbus = int(m.group(1))
    if bmap and fbus is not None:
        dist = _hops(bmap[0], fbus)
    out_dir = _abs(OUT_DIR)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    pdf_path = os.path.join(out_dir, PDF_NAME.format(project=proj, fault=fault, mode=MODE))
    have = ", ".join(CASES[c][0].split(":")[0] for c, _t, _s in traces)
    n_pages = (len(panels) + PANELS_PER_PAGE - 1) // PANELS_PER_PAGE
    stamp = time.strftime("%Y-%m-%d")
    with PdfPages(pdf_path) as pdf:
        for pg, p0 in enumerate(range(0, len(panels), PANELS_PER_PAGE), start=1):
            page = panels[p0:p0 + PANELS_PER_PAGE]
            fig, axes = plt.subplots(len(page), 1, figsize=(11.0, 8.5), squeeze=False)
            # ---- title bar ----
            fig.text(0.06, 0.965, "%s  |  %s" % (proj, fault), fontsize=14, fontweight="bold",
                     color="#1A1A1A", ha="left", va="center")
            fig.text(0.94, 0.965, PAGE_SUBTITLE, fontsize=9, color="#555555", ha="right", va="center")
            # the rule under the title -- fig.lines works on every matplotlib
            # (fig.add_artist only exists from 3.0; Python 3.4 has older ones)
            from matplotlib.lines import Line2D as _L2D
            fig.lines.append(_L2D([0.06, 0.94], [0.945, 0.945], transform=fig.transFigure,
                                  figure=fig, color="#1F5AA6", linewidth=1.2))
            legend = {}
            for ax, name in zip(axes[:, 0], page):
                ends = []
                for ci, t, sig in traces:
                    v = sig.get(name)
                    if not v:
                        continue
                    lab, col, ls, lw = CASES[ci][0], CASES[ci][3], CASES[ci][4], CASES[ci][5]
                    al = CASES[ci][6] if len(CASES[ci]) > 6 else 1.0
                    tt, vv = _thin(t, v)
                    _ve = v[-1]
                    if T_MAX is not None:
                        for _k in range(min(len(t), len(v)) - 1, -1, -1):
                            if t[_k] <= T_MAX:
                                _ve = v[_k]
                                break
                    _kw = {"dashes": (6, 3)} if ls == "--" else {}
                    ln, = ax.plot(tt, vv, color=col, linestyle=ls, linewidth=lw, alpha=al,
                                  zorder=2 + ci, solid_capstyle="round", **_kw)
                    legend.setdefault(ci, ln)
                    ends.append((col, "%s: %s" % (_cap(_short(lab)), _fmt_end(_ve))))
                # ---- panel title: quantity in bold, where it is in grey ----
                _w = _where(name, fbus, bmap, dist) if SHOW_NODES else ""
                try:
                    ax.set_title(_cap(name), fontsize=9.5, fontweight="bold", loc="left",
                                 color="#1A1A1A", pad=14)
                except (TypeError, AttributeError):      # matplotlib before 2.0: no pad
                    ax.set_title(_cap(name) + "\n", fontsize=9.5, fontweight="bold",
                                 loc="left", color="#1A1A1A")
                if _w:
                    ax.text(0.0, 1.015, _cap(_w), transform=ax.transAxes, fontsize=7.5,
                            color="#666666", ha="left", va="bottom")
                if SHOW_END_VALUES and ends:
                    x = 1.0
                    for col, txt in reversed(ends):
                        t_ = ax.text(x, 1.015, txt, transform=ax.transAxes, fontsize=7.5,
                                     color=col if col != "#8C8C8C" else "#555555",
                                     ha="right", va="bottom", fontweight="bold")
                        x -= 0.008 + 0.0068 * len(txt)
                # ---- axes ----
                for sp in ("top", "right"):
                    ax.spines[sp].set_visible(False)
                for sp in ("left", "bottom"):
                    ax.spines[sp].set_color("#888888")
                    ax.spines[sp].set_linewidth(0.8)
                ax.grid(True, color="#E5E5E5", linewidth=0.6, zorder=0)
                ax.set_axisbelow(True)
                ax.tick_params(labelsize=8, colors="#333333", length=3)
                ax.set_ylabel(_ylabel(name), fontsize=8.5, color="#333333")
                ax.set_xlabel("Time from fault (s)" if ALIGN_TO_FAULT else "Time (s)",
                              fontsize=8.5, color="#333333")
                if T_MIN is not None or T_MAX is not None:
                    ax.set_xlim(left=T_MIN, right=T_MAX)
                ax.margins(y=0.08)
                if fault_t[0] is not None:
                    ax.axvline(fault_t[0], color="#C92A2A", linewidth=0.9, linestyle=":", zorder=1)
                    ax.text(fault_t[0], 1.0, " fault", transform=ax.get_xaxis_transform(),
                            fontsize=7, color="#C92A2A", ha="left", va="top")
            # ---- one legend for the page ----
            if legend:
                order = sorted(legend)
                _lk = dict(loc="upper center", bbox_to_anchor=(0.5, 0.94), ncol=len(order),
                           fontsize=9, frameon=True, fancybox=False,
                           handlelength=4.0, columnspacing=2.5, borderpad=0.6)
                lg = fig.legend([legend[c] for c in order], [_cap(CASES[c][0]) for c in order], **_lk)
                try:                                  # set on the frame: works on old matplotlib too
                    lg.get_frame().set_edgecolor("#CCCCCC")
                    lg.get_frame().set_linewidth(0.8)
                except Exception:
                    pass
            # ---- footer ----
            fig.text(0.06, 0.015, "%s  |  %s  |  %s" % (FOOTER_TEXT, proj, fault),
                     fontsize=7, color="#888888", ha="left", va="bottom")
            fig.text(0.94, 0.015, "Page %d of %d   |   %s" % (pg, n_pages, stamp),
                     fontsize=7, color="#888888", ha="right", va="bottom")
            fig.subplots_adjust(left=0.08, right=0.97, top=0.865, bottom=0.075, hspace=0.95)
            try:
                pdf.savefig(fig)
            finally:
                plt.close(fig)
    return pdf_path


_W = {}                       # per worker process: dyntools, plt, PdfPages


def _worker_init():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    _W["plt"], _W["PdfPages"] = plt, PdfPages
    _W["dyntools"] = _import_dyntools()


def _job(args):
    """One fault: read its three .out files and write its PDF. Runs in a worker."""
    proj, fault, dirs = args
    t0 = time.time()
    try:
        if not _W:
            _worker_init()
        f0, d0 = _STATS["fast"], _STATS["dyntools"]
        p = draw_fault(proj, fault, dirs, _W["dyntools"], _W["plt"], _W["PdfPages"])
        note = "fast %d / dyntools %d" % (_STATS["fast"] - f0, _STATS["dyntools"] - d0)
        return proj, fault, p, time.time() - t0, note
    except Exception as e:
        if not _W.get("tb_shown"):                # the first failure in full, once
            _W["tb_shown"] = True
            import traceback
            traceback.print_exc()
        return proj, fault, None, time.time() - t0, "%s: %s" % (type(e).__name__, e)


def _hms(sec):
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return ("%dh %02dm" % (h, m)) if h else (("%dm %02ds" % (m, s)) if m else "%ds" % s)


def _status(lines):
    if not STATUS_FILE:
        return
    try:
        d = _abs(OUT_DIR)
        if not os.path.isdir(d):
            os.makedirs(d)
        with open(os.path.join(d, STATUS_FILE), "w") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


def main():
    try:
        import matplotlib  # noqa: F401
    except Exception as e:
        raise SystemExit("matplotlib is needed (%s):  \"%s\" -m pip install matplotlib"
                         % (e, sys.executable))
    _import_dyntools()            # fail here, once, rather than in every worker
    t0 = time.time()
    jobs = []
    for proj in PROJECTS:
        dirs = [_case_dir(c[1], proj, c[2]) for c in CASES]
        print("")
        print("[overlay] %s" % proj)
        for c, d in zip(CASES, dirs):
            print("    %-30s %s" % (c[0], d or "*** folder not found ***"))
        if not any(dirs):
            print("    no results folder for %s in any case -- skipped" % proj)
            continue
        faults = _faults(dirs)
        print("    %d fault(s)" % len(faults))
        jobs.extend((proj, f, dirs) for f in faults)
    total = len(jobs)
    nw = max(1, min(int(WORKERS or 1), total or 1))
    print("")
    print("[overlay] %d fault(s), %d worker(s)%s -- status: %s"
          % (total, nw, ", cache on" if USE_CACHE else "",
             os.path.join(_abs(OUT_DIR), STATUS_FILE) if STATUS_FILE else "console only"))
    done, ok, fails, rows = 0, 0, [], []

    if nw == 1:
        it = (_job(j) for j in jobs)
        pool = None
    else:
        import multiprocessing
        pool = multiprocessing.Pool(nw, initializer=_worker_init)
        it = pool.imap_unordered(_job, jobs)
    try:
        for proj, fault, pdf, sec, err in it:
            done += 1
            if pdf:
                ok += 1
            else:
                fails.append("%s %s%s" % (proj, fault, (": " + err) if err else ""))
            el = time.time() - t0
            eta = el / done * (total - done)
            bar = "#" * int(30 * done / max(1, total))
            line = ("[overlay] [%-30s] %d/%d  %s %-8s %s  (%s%s)  elapsed %s  ETA %s"
                    % (bar, done, total, proj, fault, "OK" if pdf else "FAILED",
                       _hms(sec), (", " + err) if err else "", _hms(el), _hms(eta)))
            print(line)
            sys.stdout.flush()
            rows.append("%-14s %-8s %-7s %8s  %-28s %s" % (proj, fault, "OK" if pdf else "FAILED",
                                                          _hms(sec), err if pdf else "", pdf or err or ""))
            _status(["OVERLAY PLOTS -- %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                     "progress  %d / %d done (%d OK, %d failed), %d worker(s)"
                     % (done, total, ok, len(fails), nw),
                     "elapsed   %s   ETA %s" % (_hms(el), _hms(eta)),
                     "-" * 90] + rows)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    print("")
    print("[overlay] %d PDF(s) in %s  (%s)" % (ok, _abs(OUT_DIR), _hms(time.time() - t0)))
    for f in fails:
        print("[overlay]   not drawn: %s" % f)


if __name__ == "__main__":
    main()
