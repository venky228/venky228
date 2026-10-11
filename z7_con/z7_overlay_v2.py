# -*- coding: utf-8 -*-
"""z7_overlay_v2.py -- BASE, SCENARIO 1 and SCENARIO 2 per fault:
VOLTAGES and ROTOR ANGLES overlaid, P and Q side by side.

    Base case                 the system without the surplus facility
    Scenario 1 (GIA)          SGF + EGF, POI held at the GIA amount
    Scenario 2                SGF on, EGF off

For every fault found in the case folders it reads the three .out files and
writes ONE PDF, in three parts:

    VOLTAGES       one panel per bus or unit, the three cases on the same axes:
                   faulted bus, POI, the plant's own buses, each SGF / EGF
                   unit's terminal voltage, and the other machines nearest the
                   fault.
    ROTOR ANGLES   the same way: swing machine, SGF / EGF units, the machines
                   nearest the fault.
    P AND Q        THREE PANELS SIDE BY SIDE -- Base case | Scenario 1 |
                   Scenario 2 -- one row for P and one for Q, for:
                     the POI (sum of the plant ties),
                     each SGF and EGF unit,
                     every machine the study FLAGGED for this fault (tripped
                       or undamped, in any of the three cases), wherever it is,
                     and the other machines within PQ_POI_HOPS (8) buses of
                       the POI -- nothing further away.

The flagged machines come from each case's own violations list, the
02_VIOLATIONS_*.csv the study writes beside its results, so the pages follow
the study's verdicts. A flagged machine's rotor angle and terminal voltage are
added to the overlays too (ANGLES_OF_VIOLATED), and its rows say which case
flagged it and for what.

The three cases are told apart by colour AND line style AND width, so the plot
still reads printed in black and white or by a colour-blind reader:

    Base         black       solid
    Scenario 1   blue        dashed
    Scenario 2   vermilion   dash-dot

RUN (PSS/E Python 3.4, needs dyntools and matplotlib):
    python z7_overlay_v2.py
Edit the SETTINGS block first. Nothing is simulated; the .out files and the
violations lists are only read, and nothing in the results folders is changed.
The PDFs go to OUT_DIR (overlay_v2_plots), apart from z7_overlay.py's.
"""
import os
import re
import sys
import csv
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
# all three the same width: the base case solid black underneath, scenario 1
# dashed blue on it, scenario 2 dash-dot orange on top -- where they coincide
# the gaps in the upper lines let the lower ones show, so all three stay visible.
# (label, results root, folder suffix, colour, line style, width, opacity)
CASES = [
    ("Base case",                          r"Base\results_base_f",     "",           "#000000", "-",  1.3, 1.0),
    ("Scenario 1: SGF + EGF (GIA)",        r"Projects\results_proj_f", "",           "#1F5AA6", "--", 1.3, 1.0),
    ("Scenario 2: SGF on, EGF off",        r"Projects\results_proj_f", "_s1_egfoff", "#E8590C", "-.", 1.3, 1.0),
]

PAGE_SUBTITLE = "Base Case vs Scenario 1 vs Scenario 2"   # right of the page title
FOOTER_TEXT = "SPP Dynamic Stability Study -- Surplus Interconnection"   # bottom left of every page
FAULT_LINE = True                        # thin red dotted line at the fault instant (no label); False = none
SHOW_END_VALUES = False                  # each panel's value at T_MAX per case, small, top right of the panel
PDF_NAME = "{project}_{fault}.pdf"       # file name per fault, e.g. SantaFe_F01.pdf ({project}, {fault}, {mode})
OUT_DIR = r"overlay_v2_plots"            # under ROOT unless absolute (apart from z7_overlay.py's overlay_plots)
ALIGN_TO_FAULT = False                   # False = simulation time, as the study plots | True = time from the fault (0 s = fault)
T_MIN, T_MAX = 0.0, 20.0                 # seconds shown; None = the whole record
MAX_POINTS = 800                         # points per trace; above this each block keeps its min AND max,
                                         #   so spikes survive (smaller PDFs; 4000 = finer, bigger files)

# ---- HOW EACH QUANTITY IS DRAWN ----------------------------------------------
OVERLAY_QUANTITIES = ["V", "A"]          # ONE panel, the three cases on the same axes:
                                         #   "V" voltage (bus and unit terminal, pu), "A" rotor angle (deg)
SIDE_BY_SIDE_QUANTITIES = ["P", "Q"]     # THREE panels in a row -- Base | Scenario 1 | Scenario 2:
                                         #   "P" active power (MW), "Q" reactive power (MVAr)
PLOT_GROUPS = ["FAULT", "POI", "PLANT", "SGF", "EGF", "GEN"]   # faulted bus, POI, the plant's own buses,
                                         #   SGF units, EGF units, other machines
PANELS_PER_PAGE = 4                      # overlay panels per page
PQ_ROWS_PER_PAGE = 4                     # side-by-side rows per page (a machine's P and Q stay on one page)
PQ_SAME_SCALE = True                     # the three panels of a row share one y scale, so they compare at a glance
PQ_MIN_SPAN = 2.0                        # smallest y range of a P / Q row (MW / MVAr): a flat trace is not blown up

# ---- WHICH MACHINES ------------------------------------------------------------
PQ_POI_HOPS = 8                          # P / Q of other machines: only those within this many buses of the POI ...
PQ_GEN_MAX = 0                           # ... at most this many of them, nearest the POI first (0 = all)
VIOLATED_ALWAYS = True                   # ... plus EVERY machine the study flagged for this fault (any of the
                                         #   three cases), wherever it is -- never cut by PQ_GEN_MAX
VIOLATION_KINDS = ["tripped", "undamped"]   # the flags that count: the 'violation' column of the study's
                                         #   02_VIOLATIONS csv ("tripped", "undamped", "review", ...)
ANGLES_OF_VIOLATED = True                # flagged machines' rotor angle and terminal voltage are overlaid too
GEN_NEAR_HOPS = 3                        # voltage / rotor-angle panels of other machines: those within this many
GEN_MAX = 8                              #   buses of the FAULT, at most this many, nearest first (0 = none)
INCLUDE_SGF_UNITS = True                 # per-unit SGF P / Q / terminal V / angle
INCLUDE_EGF_UNITS = True                 # per-unit EGF P / Q / terminal V / angle
BUS_ANGLES = False                       # bus voltage angles (POI, faulted bus, plant buses) -- not rotor angles
EXTRA_TITLE_REGEX = []                   # more channels by .out title, overlaid, e.g. [r"^GEN584713_ANGL$"]
VIOLATION_FILES = ["02_VIOLATIONS*.csv", r"reports\SPP_VIOLATIONS*.csv"]   # each case folder's own list
                                         #   (newest full one; a _SELECTED one only when there is no full one)

SBASE_MVA = 100.0                        # machine PELEC / QELEC are pu on the system base
FAST_READ = True                         # True = the study's fast packed reader where its verified layout fits (seconds, not minutes)
WORKERS = 1                              # faults drawn at once, each in its own process (1 = one at a time)
USE_CACHE = True                         # keep each .out's traces in <OUT_DIR>\cache: a re-run only reads new / changed .out files
STATUS_FILE = "OVERLAY_STATUS.txt"       # live progress, in OUT_DIR ("" = off)
SHOW_NODES = True                        # "N nodes from POI / fault bus, area" on each panel (from flags\BUS_MAP.csv)
SGF_UNIT_BUS_START = 999001              # SGF unit n is bus SGF_UNIT_BUS_START + n - 1 (NEW_PLANT "bus_start")
PSSE_DIRS = [r"C:\Program Files (x86)\PTI\PSSE34\PSSPY34",
             r"C:\Program Files (x86)\PTI\PSSE34\PSSBIN",
             r"C:\Program Files\PTI\PSSE34\PSSPY34",
             r"C:\Program Files\PTI\PSSE34\PSSBIN"]
# ============================================================================

TAG = "[overlay_v2]"


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
        print("%s dyntools could not be imported (%s) -- only files the fast reader "
              "can read will be drawn. Run this with the PSS/E Python, or add its PSSPY34 "
              "folder to PSSE_DIRS." % (TAG, e))
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


# ---- what is read ----------------------------------------------------------
# PSS/E stores channel titles in UPPER case, so every pattern ignores case. A
# leading channel index ("137 VOLT ...") and the " #2" a repeated name gets are
# taken off first, as the study does.
#
# A POI tie channel ends in P<plant bus> (metered at the POI: the flow LEAVING
# the POI, turned round here) or F<plant bus> (older runs: flow INTO the POI).
# A plant whose ties are three-winding is metered on the GRID side instead
# ("POI POWR <poi> MW GRID <bus>"): those branches carry the plant's power out
# of the POI, and their sum is the POI reading.
#
# The machines come in several title forms, all read here:
#   PROJ<n>_PELEC        the study's project units (SGF unit n; in the BASE
#                        case these are the existing units at the feeders)
#   XGEN<bus>[_<id>]_..  existing (EGF) units
#   NPGEN<bus>[_<id>]_.. the new plant's own units
#   GEN / AGEN / SYNC<bus>[_<id>]_..   other synchronous / asynchronous machines
#   POWR / VARS / ETRM / ANGL <bus>[NAME kV]<id>   PSS/E's own titles (chsb),
#                        which is how the central-area machines' P and Q are
#                        recorded
_RX_IDX = re.compile(r"^\s*\d+\s+(?=[A-Za-z])")
_RX_DUP = re.compile(r"\s*#\d+$")
_RX_FLT = re.compile(r"^FLT ?(\d+) (V|ANG)$", re.I)
_RX_POIV = re.compile(r"^POI ?(\d+) (V|ANG)$", re.I)
_RX_POIP = re.compile(r"^POI POWR (\d+) MW ([PF]) ?(\d+)", re.I)
_RX_POIQ = re.compile(r"^POI VARS (\d+) MVAR ([PF]) ?(\d+)", re.I)
_RX_POIG = re.compile(r"^POI (POWR|VARS) (\d+) (?:MW|MVAR) GR(?:ID|D\w*) ?(\d+)", re.I)
_RX_PBUS = re.compile(r"^PBUS ?(\d+) (V|ANG)$", re.I)
_RX_SGF = re.compile(r"^PROJ(\d+)_(PELEC|QELEC|ETERM|ANGL)$", re.I)
_RX_EGF = re.compile(r"^XGEN(\d+)(?:_(\w{1,2}))?_(PELEC|QELEC|ETERM|ANGL)$", re.I)
_RX_NPG = re.compile(r"^NPGEN(\d+)(?:_(\w{1,2}))?_(PELEC|QELEC|ETERM|ANGL)$", re.I)
_RX_GEN = re.compile(r"^(A?GEN|SYNC)(\d+)(?:_(\w{1,2}))?_(PELEC|QELEC|ETERM|ANGL)$", re.I)
_RX_PSSE = re.compile(r"^(ANGL|POWR|VARS|ETRM) *(\d+) *\[[^\[\]]*\] *([A-Za-z0-9]{1,2}) *$", re.I)
_RX_SWING = re.compile(r"^SWING ?(\d+)_ANGL$", re.I)
_QTY = {"PELEC": "P", "POWR": "P", "QELEC": "Q", "VARS": "Q", "ETERM": "V", "ETRM": "V", "ANGL": "A"}
_QTXT = {"P": "P (MW)", "Q": "Q (MVAr)", "V": "terminal V (pu)", "A": "angle (deg)"}
# preference when one machine quantity comes under two titles: the study's own
# named channel first, PSS/E's bulk title last
_PRIO = {"GEN": 0, "SYNC": 1, "AGEN": 2, "PSSE": 3}


def _core(title):
    return _RX_DUP.sub("", _RX_IDX.sub("", str(title))).strip()


def _mid(m):
    """Machine id as the study writes it: '' for id 1, else the id in capitals."""
    m = re.sub(r"[^A-Za-z0-9]", "", str(m or "")).upper()[:2]
    return "" if m in ("", "1") else m


def _unit(bus, mid):
    return "%s%s" % (bus, (" '%s'" % mid) if mid else "")


def _machine_of(core):
    """(family, bus, id, qty) for a machine channel of the OTHER machines, else None."""
    m = _RX_GEN.match(core)
    if m:
        return m.group(1).upper(), int(m.group(2)), _mid(m.group(3)), _QTY[m.group(4).upper()]
    m = _RX_PSSE.match(core)
    if m:
        return "PSSE", int(m.group(2)), _mid(m.group(3)), _QTY[m.group(1).upper()]
    return None


def _fixed(core):
    """True for the channels drawn whatever the fault: faulted bus, POI, ties,
       plant buses, SGF / EGF / new-plant units, swing machine."""
    m = _RX_FLT.match(core) or _RX_POIV.match(core) or _RX_PBUS.match(core)
    if m:
        return m.group(2).upper() == "V" or BUS_ANGLES
    if _RX_POIP.match(core) or _RX_POIQ.match(core) or _RX_POIG.match(core):
        return True
    if _RX_SWING.match(core):
        return True
    if INCLUDE_SGF_UNITS and (_RX_SGF.match(core) or _RX_NPG.match(core)):
        return True
    if INCLUDE_EGF_UNITS and _RX_EGF.match(core):
        return True
    return False


def _make_want(titles, bmap, vbuses):
    """The channels worth reading from one .out: the fixed ones, and of the other
       machines only those that can reach a page -- flagged by the study, within
       GEN_NEAR_HOPS of the fault bus or within PQ_POI_HOPS of the POI. The fault
       and POI buses are taken from the file's own titles, so the choice is made
       before any data is read (a .out can hold a thousand machine channels)."""
    fb, pois = None, set()
    for ttl in titles:
        c = _core(ttl)
        m = _RX_FLT.match(c)
        if m and fb is None:
            fb = int(m.group(1))
        m = _RX_POIV.match(c) or _RX_POIP.match(c) or _RX_POIQ.match(c)
        if m:
            pois.add(int(m.group(1)))
        m = _RX_POIG.match(c)
        if m:
            pois.add(int(m.group(2)))
    near = None
    if bmap is not None:
        near = set()
        if fb is not None:
            near.update(_hops_memo(bmap, fb, GEN_NEAR_HOPS))
        for p in pois:
            near.update(_hops_memo(bmap, p, PQ_POI_HOPS))
    vb = set(vbuses or ())
    extra = [re.compile(x) for x in EXTRA_TITLE_REGEX]

    def want(title):
        c = _core(title)
        if _fixed(c) or any(rx.search(c) for rx in extra):
            return True
        mm = _machine_of(c)
        if mm is None:
            return False
        if mm[1] in vb or near is None:          # no network map: keep, GEN_MAX trims later
            return True
        return mm[1] in near
    return want


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


def _fast_read(path, mk_want):
    """(t, {key: title}, {key: values}, want) for the wanted channels, or None."""
    import array
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
        want = mk_want([v for k, v in cid.items() if k != "time"])
        ids, data = {}, {}
        for k, title in cid.items():
            if k == "time" or not want(title):
                continue
            o = offs.get(k)
            if o is None or o + (ns - 1) * stride >= n:
                return None
            ids[k] = title
            data[k] = col(o, ns)
        return t, ids, data, want
    return None


def _read(dyntools, path, mk_want):
    """(t, {panel: values}) for one .out: the fast packed read when this
       folder's verified layout fits the file, else dyntools."""
    got = _fast_read(path, mk_want) if FAST_READ else None
    if got is not None:
        t, ids, data, want = got
        _STATS["fast"] += 1
    else:
        if dyntools is None:
            raise RuntimeError("no verified fast layout fits %s and dyntools is not available"
                               % os.path.basename(path))
        chnf = dyntools.CHNF(path)
        _sh, ids, data = chnf.get_data()
        t = list(data["time"])
        want = mk_want([v for k, v in ids.items() if k != "time"])
        _STATS["dyntools"] += 1
    return _extract(t, ids, data, want)


def _extract(t, ids, data, want):
    """{panel name: values}. Panel names:
         Faulted bus <b> voltage (pu)          POI <b> voltage (pu)
         POI <b> active / reactive power (MW / MVAr)   (and per tie, when several)
         Bus <b> voltage (pu)                  the plant's own buses (PBUS)
         SGF unit <n> P (MW) / Q (MVAr) / terminal V (pu) / angle (deg)
         EGF <bus>['id'] ...,  Gen <bus>['id'] ...,  Swing <b> rotor angle (deg)"""
    out, poip, poiq, grid = {}, [], [], {}
    gen_src = {}                  # Gen panel -> priority of the title it came from
    npg = {}                      # NPGEN panels, used only where PROJ<n> gave none
    extra = [re.compile(x) for x in EXTRA_TITLE_REGEX]
    for k, title in ids.items():
        if k == "time":
            continue
        ttl = str(title).strip()
        if want is not None and not want(ttl):
            continue
        v = data.get(k)
        if v is None:
            continue
        c = _core(ttl)
        if any(rx.search(c) for rx in extra):
            out.setdefault(c, list(v))          # EXTRA_TITLE_REGEX: overlaid under its own title, as recorded
            continue
        m = _RX_FLT.match(c)
        if m:
            if m.group(2).upper() == "V":
                out["Faulted bus %s voltage (pu)" % m.group(1)] = list(v)
            elif BUS_ANGLES:
                out["Faulted bus %s angle (deg)" % m.group(1)] = list(v)
            continue
        m = _RX_POIV.match(c)
        if m:
            if m.group(2).upper() == "V":
                out["POI %s voltage (pu)" % m.group(1)] = list(v)
            elif BUS_ANGLES:
                out["POI %s angle (deg)" % m.group(1)] = list(v)
            continue
        m = _RX_POIG.match(c)
        if m:
            grid.setdefault((m.group(1).upper(), m.group(2)), []).append(v)
            continue
        m = _RX_POIP.match(c)
        if m:
            poip.append((m.group(1), m.group(2).upper(), m.group(3), v))
            continue
        m = _RX_POIQ.match(c)
        if m:
            poiq.append((m.group(1), m.group(2).upper(), m.group(3), v))
            continue
        m = _RX_PBUS.match(c)
        if m:
            if m.group(2).upper() == "V":
                out["Bus %s voltage (pu)" % m.group(1)] = list(v)
            elif BUS_ANGLES:
                out["Bus %s angle (deg)" % m.group(1)] = list(v)
            continue
        m = _RX_SWING.match(c)
        if m:
            out["Swing %s rotor angle (deg)" % m.group(1)] = list(v)
            continue
        m = _RX_SGF.match(c)
        if m:
            if INCLUDE_SGF_UNITS:
                q = _QTY[m.group(2).upper()]
                sc = SBASE_MVA if q in ("P", "Q") else 1.0
                out["SGF unit %s %s" % (m.group(1), _QTXT[q])] = [x * sc for x in v]
            continue
        m = _RX_EGF.match(c)
        if m:
            if INCLUDE_EGF_UNITS:
                q = _QTY[m.group(3).upper()]
                sc = SBASE_MVA if q in ("P", "Q") else 1.0
                out["EGF %s %s" % (_unit(m.group(1), _mid(m.group(2))), _QTXT[q])] = [x * sc for x in v]
            continue
        m = _RX_NPG.match(c)
        if m:
            if INCLUDE_SGF_UNITS:
                q = _QTY[m.group(3).upper()]
                sc = SBASE_MVA if q in ("P", "Q") else 1.0
                npg[(int(m.group(1)), _mid(m.group(2)), q)] = [x * sc for x in v]
            continue
        mm = _machine_of(c)
        if mm is not None:
            fam, bus, mid, q = mm
            nm = "Gen %s %s" % (_unit(bus, mid), _QTXT[q])
            pr = _PRIO.get(fam, 9)
            if nm in gen_src and gen_src[nm] <= pr:
                continue
            gen_src[nm] = pr
            sc = SBASE_MVA if q in ("P", "Q") else 1.0
            out[nm] = [x * sc for x in v]
            continue
    # new-plant units named by bus: SGF unit n, where PROJ<n> did not give it
    for (bus, mid, q), vals in npg.items():
        n = bus - SGF_UNIT_BUS_START + 1
        nm = ("SGF unit %d %s" % (n, _QTXT[q])) if 1 <= n <= 999 else ("Gen %s %s" % (_unit(bus, mid), _QTXT[q]))
        if nm not in out:
            out[nm] = vals
    # Delivered INTO the POI: a 'P' tie (metered leaving the POI) is turned
    # round, an 'F' tie (metered into the POI) is taken as it is. With no plant
    # tie at all, the grid-side branches leaving the POI are the reading.
    n = len(t)
    for lst, gq, word, u in ((poip, "POWR", "active power", "MW"), (poiq, "VARS", "reactive power", "MVAr")):
        by_poi = {}
        for item in lst:
            by_poi.setdefault(item[0], []).append(item)
        for poi in sorted(set(by_poi) | set(p for (qq, p) in grid if qq == gq)):
            ties = by_poi.get(poi, [])
            tot = [0.0] * n
            if ties:
                for _b, d, _pl, v in ties:
                    sg = -1.0 if d == "P" else 1.0
                    for i in range(min(n, len(v))):
                        tot[i] += sg * v[i]
            else:
                for v in grid[(gq, poi)]:
                    for i in range(min(n, len(v))):
                        tot[i] += v[i]
            out["POI %s %s (%s)" % (poi, word, u)] = tot
            # every tie, even a lone one: another case may have two, and then
            # this one's single tie belongs on that tie's row (_choose drops the
            # tie rows when no case has more than one)
            for _b, d, _pl, v in ties:
                sg = -1.0 if d == "P" else 1.0
                out["POI %s tie from %s %s (%s)" % (poi, _pl, word, u)] = [sg * x for x in v[:n]]
    return t, out


def _read_cached(dyntools, path, mk_want, sel_key):
    """_read(), kept in <OUT_DIR>\cache keyed by the .out's size and time and by
       what was selected from it, so a second run reads nothing it already has."""
    if not USE_CACHE:
        return _read(dyntools, path, mk_want)
    import pickle
    st = os.stat(path)
    key = "ov2-1|%s|%d|%d|%s|%s|%s|%s|%s|%s|%s" % (
        os.path.abspath(path), st.st_size, int(st.st_mtime), INCLUDE_SGF_UNITS,
        INCLUDE_EGF_UNITS, BUS_ANGLES, SBASE_MVA, SGF_UNIT_BUS_START,
        "|".join(EXTRA_TITLE_REGEX), sel_key)
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
    t, sig = _read(dyntools, path, mk_want)
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
        if k.startswith("Faulted bus") and k.endswith("voltage (pu)") and v:
            v0 = v[0]
            for i, x in enumerate(v):
                if x < 0.9 * v0:
                    return t[max(0, i - 1)]
    return None


def _thin(t, v):
    """At most MAX_POINTS points, keeping each block's lowest and highest
       sample in time order -- a fault dip or a spike is never thinned away."""
    n = min(len(t), len(v))
    if not MAX_POINTS or n <= MAX_POINTS:
        return t[:n], v[:n]
    if T_MIN is not None or T_MAX is not None:      # only what is shown
        lo = 0
        hi = n
        while lo < n and T_MIN is not None and t[lo] < T_MIN:
            lo += 1
        while hi > lo and T_MAX is not None and t[hi - 1] > T_MAX:
            hi -= 1
        t, v, n = t[lo:hi], v[lo:hi], hi - lo
        if n <= MAX_POINTS:
            return t, v
    b = max(1, int(2 * n / MAX_POINTS) + 1)         # samples per block; 2 points out per block
    tt, vv = [], []
    for i0 in range(0, n, b):
        blk = range(i0, min(n, i0 + b))
        ok = [i for i in blk if v[i] == v[i]]
        if not ok:
            tt.append(t[i0]); vv.append(float("nan"))
            continue
        imin = min(ok, key=lambda i: v[i])
        imax = max(ok, key=lambda i: v[i])
        for i in sorted(set((imin, imax))):
            tt.append(t[i]); vv.append(v[i])
    return tt, vv


# ---- the network: distance from the fault bus and from the POI -------------------
_BMAPS = {}                   # per process: BUS_MAP path -> (adjacency, buses, areas)
_HOPS = {}                    # per process: (BUS_MAP path, source bus, limit) -> {bus: hops}


def _bus_map(dirs):
    """(adjacency, {bus: (kV, area, name)}, {area: name}, path) from the first
       flags\BUS_MAP.csv found -- Scenario 1 first, since it also holds the
       SGF buses. The branches are the PRE-fault network, as in the study PDFs."""
    for d in dirs[1:] + dirs[:1]:
        if not d:
            continue
        p = os.path.join(d, "flags", "BUS_MAP.csv")
        if not os.path.isfile(p):
            continue
        if p in _BMAPS:
            return _BMAPS[p]
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
        _BMAPS[p] = (adj, bus, area, p)
        return _BMAPS[p]
    return None


def _hops_memo(bmap, src, limit=40):
    """{bus: buses away} from src, out to limit buses."""
    key = (bmap[3], src, limit)
    if key in _HOPS:
        return _HOPS[key]
    adj = bmap[0]
    dist, frontier = {src: 0}, [src]
    for k in range(1, int(limit) + 1):
        nxt = []
        for x in frontier:
            for y in adj.get(x, ()):
                if y not in dist:
                    dist[y] = k
                    nxt.append(y)
        if not nxt:
            break
        frontier = nxt
    _HOPS[key] = dist
    return dist


# ---- the study's own verdicts: which machines it flagged ---------------------------
_VIOL = {}                    # per process: case folder -> (csv path, {fault: [rows]})


def _fnorm(f):
    """'F01', 'F1', 'f001' -> 'F1'; anything else upper-cased."""
    s = str(f or "").strip().upper()
    m = re.match(r"^F0*(\d+)$", s)
    return ("F%s" % m.group(1)) if m else s


def _elem_machine(el):
    """(bus, id) of a violations-list element: '584713', '531461-4',
       "PROJ 587313 'B'", 'SYNC 542963-2', 'NEW 999001' ..."""
    s = str(el or "").strip()
    m = re.search(r"(\d{4,})", s)
    if not m:
        return None
    rest = s[m.end():]
    mm = (re.match(r"^\s*[-_]\s*([A-Za-z0-9]{1,2})\b", rest)
          or re.match(r"^\s*'([A-Za-z0-9]{1,2})'", rest))
    return int(m.group(1)), _mid(mm.group(1) if mm else "")


def _viol_file(d):
    cands = []
    for pat in VIOLATION_FILES:
        cands.extend(glob.glob(os.path.join(d, str(pat).replace("\\", os.sep).replace("/", os.sep))))
    cands = sorted(set(cands))
    if not cands:
        return None
    full = [p for p in cands if "_SELECTED" not in os.path.basename(p).upper()]
    pool = full or cands
    return max(pool, key=lambda p: os.path.getmtime(p))


def _viol_table(d):
    """(csv path, {fault: [(bus, id, kind, value, unit, note)]}) for one case folder."""
    if d in _VIOL:
        return _VIOL[d]
    kinds = set(str(k).strip().lower() for k in (VIOLATION_KINDS or []))
    p = _viol_file(d) if d else None
    tab = {}
    if p:
        try:
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    kind = str(r.get("violation") or "").strip().lower()
                    if kind not in kinds:
                        continue
                    em = _elem_machine(r.get("element"))
                    if em is None:
                        continue
                    tab.setdefault(_fnorm(r.get("fault_id")), []).append(
                        (em[0], em[1], kind, str(r.get("value") or "").strip(),
                         str(r.get("unit") or "").strip(), str(r.get("note") or "").strip()))
        except Exception as e:
            print("%s could not read %s (%s) -- its flags are not used" % (TAG, p, e))
            tab = {}
    _VIOL[d] = (p, tab)
    return _VIOL[d]


def _violations(dirs, fault):
    """{(bus, id): [(case index, kind, value, unit, note)]} for one fault, all cases."""
    got = {}
    for ci, d in enumerate(dirs):
        if not d:
            continue
        _p, tab = _viol_table(d)
        for bus, mid, kind, val, unit, note in tab.get(_fnorm(fault), []):
            got.setdefault((bus, mid), []).append((ci, kind, val, unit, note))
    return got


def _flags_of(meta, viol):
    """The study's flags for the machine behind a panel, or []. An SGF unit
       panel matches on its bus alone (PROJ<n> carries no machine id)."""
    g, bus, mid, _q = meta
    if bus is None or g not in ("SGF", "EGF", "GEN"):
        return []
    out = []
    for (vb, vm), rows in viol.items():
        if vb == bus and (g == "SGF" or vm == mid):
            out.extend(rows)
    return out


def _flag_text(rows):
    """'tripped in Scenario 1 (4.27 MW, ended 0.00 MW); undamped 34.9 deg in Scenario 2'."""
    by = {}
    for ci, kind, val, unit, note in rows:
        by.setdefault(kind, []).append((ci, val, unit, note))
    parts = []
    for kind in sorted(by):
        cs = sorted(set(ci for ci, _v, _u, _n in by[kind]))
        ci0, val, unit, note = sorted(by[kind])[0]
        det = ""
        if kind == "tripped" and val:
            det = " (%s MW before%s)" % (val, (", " + note) if note else "")
        elif val and unit:
            det = " (%s %s)" % (val, unit)
        parts.append("%s%s in %s" % (kind, det, ", ".join(_short(CASES[c][0]) for c in cs)))
    return "; ".join(parts)


# ---- panel names ---------------------------------------------------------------
def _meta(name):
    """(group, bus, id, qty) of a panel. qty: V voltage, A rotor angle, P, Q,
       B bus angle, X anything else. For an SGF unit the id slot holds n; for a
       POI tie it holds the tie's plant bus."""
    m = re.match(r"^Faulted bus (\d+) (voltage|angle)", name)
    if m:
        return ("FAULT", int(m.group(1)), "", "V" if m.group(2) == "voltage" else "B")
    m = re.match(r"^POI (\d+) tie from (\d+) (active|reactive) power", name)
    if m:
        return ("POI", int(m.group(1)), int(m.group(2)), "P" if m.group(3) == "active" else "Q")
    m = re.match(r"^POI (\d+) (voltage|angle|active power|reactive power)", name)
    if m:
        return ("POI", int(m.group(1)), "", {"voltage": "V", "angle": "B", "active power": "P",
                                             "reactive power": "Q"}[m.group(2)])
    m = re.match(r"^Bus (\d+) (voltage|angle)", name)
    if m:
        return ("PLANT", int(m.group(1)), "", "V" if m.group(2) == "voltage" else "B")
    m = re.match(r"^Swing (\d+) rotor angle", name)
    if m:
        return ("GEN", int(m.group(1)), "", "A")
    m = re.match(r"^SGF unit (\d+) (P|Q|terminal V|angle) ", name)
    if m:
        n = int(m.group(1))
        return ("SGF", SGF_UNIT_BUS_START + n - 1, n,
                {"P": "P", "Q": "Q", "terminal V": "V", "angle": "A"}[m.group(2)])
    m = re.match(r"^(EGF|Gen) (\d+)(?: '(\w+)')? (P|Q|terminal V|angle) ", name)
    if m:
        return ("EGF" if m.group(1) == "EGF" else "GEN", int(m.group(2)), m.group(3) or "",
                {"P": "P", "Q": "Q", "terminal V": "V", "angle": "A"}[m.group(4)])
    return ("OTHER", None, "", "X")


def _normalise(traces):
    """One name per machine across the three cases.
       THE BASE CASE HAS NO SGF. Its "project machines" (PROJ1, PROJ2, ...) are
       the EXISTING units at the plant's feeders, in feeder order -- so they are
       drawn on the EGF panels (by the EGF buses the other cases name, ascending),
       never on the SGF ones. A machine that also came under PSS/E's own title
       (an EGF or SGF unit inside the central area) is drawn once, on its EGF /
       SGF panel."""
    egf = sorted(set(_meta(k)[1] for _c, _t, sg in traces for k in sg if _meta(k)[0] == "EGF"))
    egf_set = set(egf)
    for j, (ci, t, sg) in enumerate(traces):
        base = "base" in str(CASES[ci][1]).lower()
        new = {}
        if base:
            for k, v in sg.items():
                m = re.match(r"^SGF unit (\d+) (.*)$", k)
                if not m:
                    continue
                n = int(m.group(1))
                if INCLUDE_EGF_UNITS and 1 <= n <= len(egf):
                    new["EGF %d %s" % (egf[n - 1], m.group(2))] = v
        for k, v in sg.items():
            if base and k.startswith("SGF unit "):
                continue
            g, bus, mid, q = _meta(k)
            if g != "GEN" or k.startswith("Swing"):
                new.setdefault(k, v)
        for k, v in sg.items():
            g, bus, mid, q = _meta(k)
            if g != "GEN" or k.startswith("Swing"):
                continue
            nm = k
            n = bus - SGF_UNIT_BUS_START + 1
            if bus in egf_set:
                nm = "EGF %s %s" % (_unit(bus, mid), _QTXT[q])
                if not INCLUDE_EGF_UNITS:
                    continue
            elif 1 <= n <= 999 and not base:
                nm = "SGF unit %d %s" % (n, _QTXT[q])
                if not INCLUDE_SGF_UNITS:
                    continue
            if nm not in new:
                new[nm] = v
        traces[j] = (ci, t, new)
    return traces


def _where(meta, fbus, pois, bmap, dist_f, dist_p):
    """'2 nodes from POI 765911, 5 nodes from fault bus 539800, AREA 534 SUNC'."""
    g, b, _mid, _q = meta
    if b is None or bmap is None:
        return ""
    _adj, bus, area, _p = bmap
    parts = []
    if pois:
        if b in pois:
            parts.append("at the POI")
        elif b in dist_p:
            n = dist_p[b]
            parts.append("%d node%s from POI %d" % (n, "" if n == 1 else "s", min(pois)))
    if fbus is not None:
        if b == fbus:
            parts.append("at fault bus")
        elif b in dist_f:
            n = dist_f[b]
            parts.append("%d node%s from fault bus %d" % (n, "" if n == 1 else "s", fbus))
        else:
            parts.append("more than 40 nodes from fault bus %d" % fbus)
    txt = ", ".join(parts)
    if b in bus and bus[b][1]:
        an = bus[b][1]
        txt += (", " if txt else "") + ("AREA %s %s" % (an, area.get(an, ""))).rstrip()
    return txt


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


def _ylabel(name):
    """'Voltage (pu)', 'P (MW)', 'Q (MVAr)' -- from the unit the panel name ends with."""
    m = re.search(r"\(([^()]*)\)\s*$", name)
    u = (m.group(1) if m else "").strip()
    if u == "deg":
        return "Angle (deg)"
    if u == "pu":
        return "Voltage (pu)"
    if u == "MW":
        return "P (MW)"
    if u.upper() == "MVAR":
        return "Q (MVAr)"
    return u or ""


def _value_at_end(t, v):
    ve = v[-1]
    if T_MAX is not None:
        for k in range(min(len(t), len(v)) - 1, -1, -1):
            if t[k] <= T_MAX:
                ve = v[k]
                break
    return ve


def _style(ci):
    lab, col, ls, lw = CASES[ci][0], CASES[ci][3], CASES[ci][4], CASES[ci][5]
    al = CASES[ci][6] if len(CASES[ci]) > 6 else 1.0
    kw = ({"dashes": (6, 3)} if ls == "--" else ({"dashes": (8, 3, 2, 3)} if ls == "-." else {}))
    return lab, col, ls, lw, al, kw


def _dress(ax, xlabel=True):
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color("#888888")
        ax.spines[sp].set_linewidth(0.8)
    ax.grid(True, color="#E5E5E5", linewidth=0.6, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=8, colors="#333333", length=3)
    if xlabel:
        ax.set_xlabel("Time from fault (s)" if ALIGN_TO_FAULT else "Time (s)",
                      fontsize=8.5, color="#333333")
    if T_MIN is not None or T_MAX is not None:
        ax.set_xlim(left=T_MIN, right=T_MAX)


def _ylim(series):
    """Shared y limits of a side-by-side row over the time shown, with a minimum span."""
    lo = hi = None
    for t, v in series:
        for x, y in zip(t, v):
            if (T_MIN is not None and x < T_MIN) or (T_MAX is not None and x > T_MAX):
                continue
            if y != y or abs(y) > 1e30:
                continue
            if lo is None or y < lo:
                lo = y
            if hi is None or y > hi:
                hi = y
    if lo is None:
        return None
    need = max(float(PQ_MIN_SPAN or 0.0), 0.02 * max(abs(lo), abs(hi)))
    if hi - lo < need:
        mid = 0.5 * (lo + hi)
        lo, hi = mid - 0.5 * need, mid + 0.5 * need
    pad = 0.08 * (hi - lo)
    return lo - pad, hi + pad


# ---- choosing and ordering the panels ------------------------------------------
def _choose(traces, viol, bmap):
    """(overlay sections, side-by-side element groups, context) for one fault."""
    allk = set(k for _c, _t, sg in traces for k in sg)
    fbus, pois = None, set()
    for k in allk:
        g, b, mid, q = _meta(k)
        if g == "FAULT":
            fbus = b
        if g == "POI":
            pois.add(b)
    dist_f = _hops_memo(bmap, fbus, 40) if (bmap and fbus is not None) else {}
    dist_p = {}
    if bmap:
        for p in pois:
            for b, n in _hops_memo(bmap, p, 40).items():
                if b not in dist_p or n < dist_p[b]:
                    dist_p[b] = n
    groups = set(str(x).upper() for x in (PLOT_GROUPS or []))
    ovq = set(str(x).upper() for x in (OVERLAY_QUANTITIES or []))
    sbq = set(str(x).upper() for x in (SIDE_BY_SIDE_QUANTITIES or []))

    def flagged(meta):
        return bool(_flags_of(meta, viol))

    # OTHER MACHINES. For the overlays: the GEN_MAX nearest the fault within
    # GEN_NEAR_HOPS (as the study PDFs show them) plus the flagged ones. For P / Q:
    # every one within PQ_POI_HOPS of the POI plus the flagged ones.
    gens = sorted(set((_meta(k)[1], _meta(k)[2]) for k in allk
                      if _meta(k)[0] == "GEN" and not k.startswith("Swing")))
    far = 10 ** 6
    vio = set(gm for gm in gens if flagged(("GEN", gm[0], gm[1], "P")))
    if dist_f:
        near = sorted((dist_f[b], b, m) for b, m in gens if b in dist_f and dist_f[b] <= GEN_NEAR_HOPS)
        keep_va = set((b, m) for _d, b, m in near[:max(0, int(GEN_MAX or 0))])
    else:
        keep_va = set(gens[:max(0, int(GEN_MAX or 0))])
    if ANGLES_OF_VIOLATED:
        keep_va |= vio
    if dist_p:
        near = sorted((dist_p[b], b, m) for b, m in gens if b in dist_p and dist_p[b] <= PQ_POI_HOPS)
        near = [(b, m) for _d, b, m in near if (b, m) not in vio]
        if PQ_GEN_MAX:
            near = near[:int(PQ_GEN_MAX)]
        keep_pq = set(near)
    else:
        keep_pq = set()
    if VIOLATED_ALWAYS:
        keep_pq |= vio

    def on(k, meta):
        g = meta[0]
        if g == "GEN" and "GEN" not in groups:
            return False
        if g in ("FAULT", "POI", "PLANT", "SGF", "EGF") and g not in groups:
            return False
        return True

    # POI TIE ROWS only where some case has two or more ties into that POI --
    # with one tie the row would repeat the POI total
    multi = set()
    for _c, _t, sg in traces:
        nties = {}
        for k in sg:
            m = re.match(r"^POI (\d+) tie from (\d+) active power", k)
            if m:
                nties[m.group(1)] = nties.get(m.group(1), 0) + 1
        multi |= set(int(p) for p, cnt in nties.items() if cnt > 1)

    ov = {"V": [], "A": [], "B": [], "X": []}
    sb = {}
    for k in allk:
        meta = _meta(k)
        g, b, mid, q = meta
        if not on(k, meta):
            continue
        if g == "POI" and mid != "" and b not in multi:
            continue
        if g == "GEN" and not k.startswith("Swing"):
            if q in sbq and (b, mid) not in keep_pq:
                continue
            if q not in sbq and (b, mid) not in keep_va:
                continue
        if q in sbq:
            sb.setdefault((g, b, mid), []).append(k)
        elif q == "B":
            if BUS_ANGLES:
                ov["B"].append(k)
        elif q in ovq or q == "X":
            ov.setdefault(q, []).append(k)

    def ord_ov(k):
        meta = _meta(k)
        g, b, mid, q = meta
        f = 0 if flagged(meta) else 1
        d = dist_f.get(b, far) if b is not None else far
        if k.startswith("Swing"):
            return (0, 0, 0, 0, k)
        rank = {"FAULT": 1, "POI": 2, "PLANT": 3, "SGF": 5, "EGF": 6, "GEN": 7}.get(g, 9)
        if g == "GEN" and f == 0:
            rank = 4                                   # flagged machines lead the other machines
        return (rank, d if g == "GEN" else 0, b if b is not None else 0, str(mid), k)

    def ord_sb(key):
        g, b, mid = key
        meta = (g, b, mid, "P")
        f = flagged(meta)
        if g == "POI":
            return (0, 0 if mid == "" else 1, mid if mid != "" else 0, b)
        if f:
            return (1, dist_p.get(b, far), b, str(mid))
        rank = {"SGF": 2, "EGF": 3}.get(g, 4)
        return (rank, dist_p.get(b, far) if g == "GEN" else 0, b, str(mid))

    sections = []
    for q, title in (("V", "Voltages"), ("A", "Rotor angles"), ("P", "Active power"), ("Q", "Reactive power"),
                     ("B", "Bus angles"), ("X", "Other channels")):
        if ov.get(q):
            sections.append((title, sorted(ov[q], key=ord_ov)))
    elems = []
    for key in sorted(sb, key=ord_sb):
        rows = sorted(sb[key], key=lambda k: (0 if _meta(k)[3] == "P" else 1, k))
        elems.append(rows)
    # how many flagged machines have a panel, and which have none in any .out
    # (only their verdict is known -- nothing to draw)
    drawn = set()
    for k in allk:
        meta = _meta(k)
        if meta[0] in ("SGF", "EGF", "GEN") and not k.startswith("Swing"):
            for vb, vm in viol:
                if vb == meta[1] and (meta[0] == "SGF" or vm == meta[2]):
                    drawn.add((vb, vm))
    ctx = {"fbus": fbus, "pois": pois, "dist_f": dist_f, "dist_p": dist_p,
           "n_flagged": len(drawn),
           "flag_missing": [_unit(vb, vm) for (vb, vm) in viol if (vb, vm) not in drawn]}
    return sections, elems, ctx


# ---- drawing ---------------------------------------------------------------------
def _page_head(fig, proj, fault, section, pg, n_pages, stamp):
    fig.text(0.06, 0.965, "%s  |  %s  |  %s" % (proj, fault, section), fontsize=14,
             color="#1A1A1A", ha="left", va="center")
    fig.text(0.94, 0.965, PAGE_SUBTITLE, fontsize=9, color="#555555", ha="right", va="center")
    # the rule under the title -- fig.lines works on every matplotlib
    # (fig.add_artist only exists from 3.0; Python 3.4 has older ones)
    from matplotlib.lines import Line2D as _L2D
    fig.lines.append(_L2D([0.06, 0.94], [0.945, 0.945], transform=fig.transFigure,
                          figure=fig, color="#1F5AA6", linewidth=1.2))
    fig.text(0.06, 0.015, "%s  |  %s  |  %s" % (FOOTER_TEXT, proj, fault),
             fontsize=7, color="#888888", ha="left", va="bottom")
    fig.text(0.94, 0.015, "Page %d of %d   |   %s" % (pg, n_pages, stamp),
             fontsize=7, color="#888888", ha="right", va="bottom")


def _page_legend(fig, legend):
    if not legend:
        return
    order = sorted(legend)
    _lk = dict(loc="upper center", bbox_to_anchor=(0.5, 0.938), ncol=len(order),
               fontsize=9, frameon=True, fancybox=False,
               handlelength=4.0, columnspacing=2.5, borderpad=0.6)
    lg = fig.legend([legend[c] for c in order], [_cap(CASES[c][0]) for c in order], **_lk)
    try:                                  # set on the frame: works on old matplotlib too
        lg.get_frame().set_edgecolor("#CCCCCC")
        lg.get_frame().set_linewidth(0.8)
    except Exception:
        pass


def _legend_handles():
    """Proxy lines for the page legend (a page of side-by-side rows has every
       case even when one row lacks one)."""
    from matplotlib.lines import Line2D
    out = {}
    for ci in range(len(CASES)):
        lab, col, ls, lw, al, kw = _style(ci)
        out[ci] = Line2D([0, 1], [0, 0], color=col, linestyle=ls, linewidth=lw, alpha=al, **kw)
    return out


def _draw_overlay_page(plt, page, traces, fault_t, ctx, viol, bmap):
    fig, axes = plt.subplots(len(page), 1, figsize=(11.0, 8.5), squeeze=False)
    legend = {}
    for ax, name in zip(axes[:, 0], page):
        ends = []
        for ci, t, sig in traces:
            v = sig.get(name)
            if not v:
                continue
            lab, col, ls, lw, al, kw = _style(ci)
            tt, vv = _thin(t, v)
            ln, = ax.plot(tt, vv, color=col, linestyle=ls, linewidth=lw, alpha=al, zorder=2 + ci, **kw)
            legend.setdefault(ci, ln)
            ends.append((col, "%s: %s" % (_cap(_short(lab)), _fmt_end(_value_at_end(t, v)))))
        meta = _meta(name)
        try:
            ax.set_title(_cap(name), fontsize=9.5, loc="left", color="#1A1A1A", pad=14)
        except (TypeError, AttributeError):      # matplotlib before 2.0: no pad
            ax.set_title(_cap(name) + "\n", fontsize=9.5, loc="left", color="#1A1A1A")
        w = _where(meta, ctx["fbus"], ctx["pois"], bmap, ctx["dist_f"], ctx["dist_p"]) if SHOW_NODES else ""
        if w:
            ax.text(0.0, 1.015, _cap(w), transform=ax.transAxes, fontsize=7.5,
                    color="#666666", ha="left", va="bottom")
        fl = _flags_of(meta, viol)
        if SHOW_END_VALUES and ends:
            x = 1.0
            for col, txt in reversed(ends):
                ax.text(x, 1.015, txt, transform=ax.transAxes, fontsize=7.5,
                        color=col, ha="right", va="bottom")
                x -= 0.008 + 0.0068 * len(txt)
        elif fl:
            ax.text(1.0, 1.015, "Study flag: " + _flag_text(fl), transform=ax.transAxes,
                    fontsize=7.5, color="#C92A2A", ha="right", va="bottom")
        _dress(ax)
        ax.set_ylabel(_ylabel(name), fontsize=8.5, color="#333333")
        ax.margins(y=0.08)
        if FAULT_LINE and fault_t is not None:
            ax.axvline(fault_t, color="#C92A2A", linewidth=0.9, linestyle=":", zorder=1)
    fig.subplots_adjust(left=0.08, right=0.97, top=0.835, bottom=0.075, hspace=0.95)
    return fig, legend


def _above(ax, x, text, **kw):
    """Text a fixed 3 points above the top of the axes (whatever the panel's
       height), at x in axes fraction."""
    ax.annotate(text, xy=(x, 1.0), xycoords="axes fraction", xytext=(0, 3),
                textcoords="offset points", va="bottom", **kw)


def _missing_text(ci, name, have_case):
    if not have_case:
        return "no .out for this fault"
    if name.startswith("SGF unit") and "base" in str(CASES[ci][1]).lower():
        return "no SGF in the base case"
    if name.startswith("EGF") and "egfoff" in str(CASES[ci][2]).lower():
        return "EGF off in this case"
    return "not recorded in this case"


def _draw_pq_page(plt, rows, traces, fault_t, ctx, viol, bmap, present):
    """Rows of three panels -- one per case, in CASES order -- sharing one y scale."""
    from matplotlib.gridspec import GridSpec
    fig = plt.figure(figsize=(11.0, 8.5))
    nrow = max(1, int(PQ_ROWS_PER_PAGE or 1))
    top, bot, gap = 0.82, 0.075, 0.095       # the gap holds the row above's time axis and this row's title
    h = (top - bot - (nrow - 1) * gap) / float(nrow)
    gs = GridSpec(nrow, len(CASES), left=0.08, right=0.97, top=top, bottom=bot,
                  hspace=(gap / h) if nrow > 1 else 0.2, wspace=0.16)
    by_ci = dict((ci, (t, sig)) for ci, t, sig in traces)
    for r, name in enumerate(rows):
        meta = _meta(name)
        fl = _flags_of(meta, viol)
        fl_cases = set(ci for ci, _k, _v, _u, _n in fl)
        fl_kind = dict((ci, k) for ci, k, _v, _u, _n in fl)
        series = []
        axs = []
        for ci in range(len(CASES)):
            ax = fig.add_subplot(gs[r, ci], sharey=(axs[0] if (axs and PQ_SAME_SCALE) else None))
            axs.append(ax)
            lab, col, ls, lw, al, kw = _style(ci)
            got = by_ci.get(ci)
            v = got[1].get(name) if got else None
            if v:
                t = got[0]
                tt, vv = _thin(t, v)
                ax.plot(tt, vv, color=col, linestyle=ls, linewidth=lw, alpha=al, zorder=2, **kw)
                series.append((t, v))
                if SHOW_END_VALUES:
                    _above(ax, 1.0, _fmt_end(_value_at_end(t, v)), fontsize=7.5, color=col, ha="right")
            else:
                ax.text(0.5, 0.5, _missing_text(ci, name, ci in present), transform=ax.transAxes,
                        fontsize=8, color="#999999", ha="center", va="center")
            _above(ax, 0.0, _short(lab), fontsize=8.5, color=col if col != "#000000" else "#1A1A1A", ha="left")
            if ci in fl_cases and not SHOW_END_VALUES:
                _above(ax, 1.0, fl_kind[ci].upper(), fontsize=7.5, color="#C92A2A", ha="right",
                       fontweight="bold")
            _dress(ax, xlabel=(r == len(rows) - 1))
            if ci == 0:
                ax.set_ylabel(_ylabel(name), fontsize=8.5, color="#333333")
            if FAULT_LINE and fault_t is not None:
                ax.axvline(fault_t, color="#C92A2A", linewidth=0.9, linestyle=":", zorder=1)
        lim = _ylim(series) if PQ_SAME_SCALE else None
        if lim:
            axs[0].set_ylim(lim[0], lim[1])
        elif not PQ_SAME_SCALE:
            for ax in axs:
                ax.margins(y=0.08)
        # ---- the row's title: what it is in bold, where it is in grey, the flag in red
        pos = axs[0].get_position()
        fig.text(pos.x0, pos.y1 + 0.047, _cap(name), fontsize=9.5, color="#1A1A1A",
                 ha="left", va="bottom", fontweight="bold")
        w = _where(meta, ctx["fbus"], ctx["pois"], bmap, ctx["dist_f"], ctx["dist_p"]) if SHOW_NODES else ""
        if w:
            fig.text(pos.x0, pos.y1 + 0.029, _cap(w), fontsize=7.5, color="#666666",
                     ha="left", va="bottom")
        if fl:
            pr = axs[-1].get_position()
            fig.text(pr.x1, pos.y1 + 0.029, "Study flag: " + _flag_text(fl), fontsize=7.5,
                     color="#C92A2A", ha="right", va="bottom")
    return fig


def _paginate(sections, elems):
    """[(kind, section title, panels)] -- overlay pages of PANELS_PER_PAGE panels,
       then side-by-side pages of PQ_ROWS_PER_PAGE rows; a machine's P and Q rows
       are never split across two pages."""
    pages = []
    per = max(1, int(PANELS_PER_PAGE or 1))
    for title, names in sections:
        for i in range(0, len(names), per):
            pages.append(("overlay", title, names[i:i + per]))
    nrow = max(1, int(PQ_ROWS_PER_PAGE or 1))
    title = "P and Q, side by side"
    cur = []
    for rows in elems:
        if cur and len(cur) + len(rows) > nrow:
            pages.append(("pq", title, cur))
            cur = []
        while len(rows) > nrow:                      # only when PQ_ROWS_PER_PAGE is 1
            pages.append(("pq", title, rows[:nrow]))
            rows = rows[nrow:]
        cur = cur + list(rows)
    if cur:
        pages.append(("pq", title, cur))
    return pages


def draw_fault(proj, fault, dirs, dyntools, plt, PdfPages):
    """One fault's PDF. Returns (pdf path or None, note)."""
    bmap = _bus_map(dirs)
    viol = _violations(dirs, fault)
    vbuses = sorted(set(b for (b, _m) in viol))
    stamp_map = ("%s@%d" % (bmap[3], int(os.path.getmtime(bmap[3])))) if bmap else "nomap"
    sel_key = "%s|%s|%s|%s" % (GEN_NEAR_HOPS, PQ_POI_HOPS, ",".join(str(b) for b in vbuses), stamp_map)

    def mk_want(titles):
        return _make_want(titles, bmap, vbuses)
    traces = []                   # (case index, t, {panel: values})
    fault_t = None                # fault instant on the plotted time axis
    present = set()
    unreadable = 0
    for ci, d in enumerate(dirs):
        if not d:
            continue
        p = os.path.join(d, "outs", "%s.out" % fault)
        if not os.path.isfile(p):
            print("    %-30s %s: no .out" % (CASES[ci][0], fault))
            continue
        try:
            t, sig = _read_cached(dyntools, p, mk_want, sel_key)
        except Exception as e:
            print("    %-30s %s: could not read (%s)" % (CASES[ci][0], fault, e))
            unreadable += 1
            continue
        present.add(ci)
        tf = _fault_time(t, sig)
        if ALIGN_TO_FAULT and tf is not None:
            t = [x - tf for x in t]
            tf = 0.0
        traces.append((ci, t, sig))
        if fault_t is None and tf is not None:
            fault_t = tf
    if not traces:
        print("    %s: no case has a readable .out -- skipped" % fault)
        return None, ("no readable .out" if unreadable else "no .out")
    traces = _normalise(traces)
    sections, elems, ctx = _choose(traces, viol, bmap)
    pages = _paginate(sections, elems)
    if not pages:
        print("    %s: nothing left to draw with OVERLAY_QUANTITIES = %s, SIDE_BY_SIDE_QUANTITIES = %s, "
              "PLOT_GROUPS = %s" % (fault, OVERLAY_QUANTITIES, SIDE_BY_SIDE_QUANTITIES, PLOT_GROUPS))
        return None, "nothing to draw"
    if ctx["flag_missing"]:
        print("    %s: flagged by the study but not in any .out (no channel to draw): %s"
              % (fault, ", ".join(sorted(set(ctx["flag_missing"])))))
    out_dir = _abs(OUT_DIR)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    pdf_path = os.path.join(out_dir, PDF_NAME.format(project=proj, fault=fault, mode=MODE))
    stamp = time.strftime("%Y-%m-%d")
    n_ov = sum(len(p[2]) for p in pages if p[0] == "overlay")
    n_pq = sum(len(p[2]) for p in pages if p[0] == "pq")
    with PdfPages(pdf_path) as pdf:
        for pg, (kind, title, page) in enumerate(pages, start=1):
            if kind == "overlay":
                fig, legend = _draw_overlay_page(plt, page, traces, fault_t, ctx, viol, bmap)
            else:
                fig = _draw_pq_page(plt, page, traces, fault_t, ctx, viol, bmap, present)
                legend = _legend_handles()
            _page_head(fig, proj, fault, title, pg, len(pages), stamp)
            _page_legend(fig, legend)
            try:
                pdf.savefig(fig)
            finally:
                plt.close(fig)
    note = "%d overlay panel(s), %d P/Q row(s), %d flagged machine(s), %d page(s)" % (
        n_ov, n_pq, ctx["n_flagged"], len(pages))
    return pdf_path, note


_W = {}                       # per worker process: dyntools, plt, PdfPages


def _worker_init():
    import matplotlib
    matplotlib.use("Agg")
    try:                                    # smaller PDFs, same look
        matplotlib.rcParams["pdf.compression"] = 9
        matplotlib.rcParams["path.simplify"] = True
        matplotlib.rcParams["path.simplify_threshold"] = 0.5
    except Exception:
        pass
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
        p, what = draw_fault(proj, fault, dirs, _W["dyntools"], _W["plt"], _W["PdfPages"])
        note = "%s; fast %d / dyntools %d" % (what, _STATS["fast"] - f0, _STATS["dyntools"] - d0)
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
        print("%s %s" % (TAG, proj))
        for c, d in zip(CASES, dirs):
            print("    %-30s %s" % (c[0], d or "*** folder not found ***"))
        if not any(dirs):
            print("    no results folder for %s in any case -- skipped" % proj)
            continue
        for c, d in zip(CASES, dirs):
            if not d:
                continue
            p, tab = _viol_table(d)
            print("    %-30s flags: %s" % (_short(c[0]), ("%s (%d fault(s) with %s)"
                  % (os.path.basename(p), len(tab), "/".join(VIOLATION_KINDS))) if p else
                  "*** no violations list found -- flagged machines cannot be added ***"))
        bm = _bus_map(dirs)
        if bm is None:
            print("    *** no flags\\BUS_MAP.csv: other machines' P / Q are limited to the flagged ones ***")
        faults = _faults(dirs)
        print("    %d fault(s)" % len(faults))
        jobs.extend((proj, f, dirs) for f in faults)
    total = len(jobs)
    nw = max(1, min(int(WORKERS or 1), total or 1))
    print("")
    print("%s %d fault(s), %d worker(s)%s -- status: %s"
          % (TAG, total, nw, ", cache on" if USE_CACHE else "",
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
            line = ("%s [%-30s] %d/%d  %s %-8s %s  (%s%s)  elapsed %s  ETA %s"
                    % (TAG, bar, done, total, proj, fault, "OK" if pdf else "FAILED",
                       _hms(sec), (", " + err) if err else "", _hms(el), _hms(eta)))
            print(line)
            sys.stdout.flush()
            rows.append("%-14s %-8s %-7s %8s  %s" % (proj, fault, "OK" if pdf else "FAILED",
                                                    _hms(sec), pdf or err or ""))
            _status(["OVERLAY V2 PLOTS -- %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
                     "progress  %d / %d done (%d OK, %d failed), %d worker(s)"
                     % (done, total, ok, len(fails), nw),
                     "elapsed   %s   ETA %s" % (_hms(el), _hms(eta)),
                     "-" * 90] + rows)
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    print("")
    print("%s %d PDF(s) in %s  (%s)" % (TAG, ok, _abs(OUT_DIR), _hms(time.time() - t0)))
    for f in fails:
        print("%s   not drawn: %s" % (TAG, f))


if __name__ == "__main__":
    main()
