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
  2. Picks the plants. With PLANTS filled in (the POI DISTANCE list), each plant
     whose POI bus is within MAX_NODES of a project POI (PROJECT_POIS), counted
     in TARGET, is taken WHOLE: every bus SOURCE has behind that POI and TARGET
     does not -- the tie, the main transformer, the collector, the GSUs and the
     units -- found by walking SOURCE outward from the plant's POI bus. Without
     PLANTS: PLANT_BUSES (bus numbers / ranges), or every bus that is in SOURCE
     and not in TARGET.
  3. Checks before changing anything: every selected bus is new to TARGET, every
     branch / transformer it needs ends on a selected bus or on a bus TARGET
     already has, and every copied bus reaches the TARGET network through the
     copied elements (no island). Three-winding transformers (a 3-winding
     MPT) are copied: impedances on the system base and the three winding
     ratios, built from psspy's own help text and read back. A switched shunt
     on a plant stops that plant with the list, so a half-copied plant is never
     saved.
  4. ONE PLANT AT A TIME (ONE_AT_A_TIME): adds its buses, plant, machines,
     lines, two-winding transformers, loads and fixed shunts with SOURCE's data
     and solves; then scales the OTHER machines in that plant's area (not the
     copied ones, not the swing, not HOLD_EXCLUDE, and never the four project
     plants behind PROJECT_POIS -- PROTECT_PROJECTS) back to TARGET's original
     area MW (HOLD_TOL_MW), solving after each pass. The step is kept only when
     the case converged and the area balances; otherwise the case goes back to
     the end of the last good step and that plant is reported as skipped.
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
# >>> THE TWO CASES <<<
SOURCE_SAV = r"C:\KV\ENGIE\CQ_Cases\DIS23.sav"         # >>> the case that HAS the plants
TARGET_SAV = r"C:\KV\ENGIE\CQ_Cases\Base\DIS2201-25SP-G03-CQ_Mitigated.sav"   # >>> the case to add them to
OUT_SAV    = r""            # "" = <TARGET>_plus_<n>bus.sav beside TARGET_SAV

# >>> THE PROJECT POIs the distance is measured from (nodes, counted in TARGET)
PROJECT_POIS = {
    "EmpirePrairie": 761383,     # G17-183-TAP 345 kV
    "IronStar":      560080,     # G16-046-TAP 345 kV
    "EastFork":      531623,     # EASTFORK3 115 kV
    "SantaFe":       765911,     # G21-068-TAP 345 kV
}
MAX_NODES = 5               # a plant is copied when its POI is within this many
                            # nodes of ANY project POI (5 = NEAR in the list)
MAX_Z_PU  = None            # optional second test: summed |R+jX| pu (100 MVA) to
                            # the nearest project POI must be <= this; None = off

# >>> THE PLANTS (from the POI DISTANCE list): name, MW, type, POI bus.
# [] = do not select by plant; use PLANT_BUSES below instead.
PLANTS = [
    ("GEN-2023-099", 300, "Solar",   532766),   # Jeffery EC 345 kV
    ("GEN-2023-171", 150, "Battery", 548814),   # Sub M 161 kV
    ("GEN-2023-033", 200, "Battery", 541248),   # Liberty South 161 kV
    ("GEN-2023-170", 150, "Battery", 543062),   # Salisbury 161 kV
    ("GEN-2023-037", 200, "Battery", 546653),   # Nearman 161 kV
    ("GEN-2023-173", 100, "Wind",    531449),   # Holcomb 345 kV
    ("GEN-2023-172", 200, "Wind",    531449),   # Holcomb 345 kV
    ("GEN-2023-107", 300, "Wind",    531465),   # Setab 345 kV
    ("GEN-2023-034", 130, "Solar",   533073),   # Clear Water-Waco 138 kV
    ("GEN-2023-061", 100, "Battery", 505488),   # Carthage 161 kV
]
ONLY_PLANTS = []            # [] = every plant within MAX_NODES; or ["GEN-2023-107"]
                            # to copy just those (still reported with distances)

PLANT_BUSES = []            # [] = every bus in SOURCE that TARGET does not have.
                            # Or a list of buses / ranges: [999001, "999950-999954"]
COPY_MACHINES_ON_EXISTING_BUSES = False
                            # True = also copy machines that SOURCE has at a bus
                            # TARGET already has (and TARGET lacks that machine)

HOLD_AREA_MW = True         # True = the areas receiving machines keep their MW
HOLD_AREAS   = []           # [] = every area a copied machine is in; or [534, 541]
HOLD_EXCLUDE = []           # buses whose machines are never rescaled (e.g. an EGF)
PROTECT_PROJECTS = True     # True = the redispatch never touches the PROJECT_POIS
                            # plants: every machine radially behind each project
                            # POI (EGF + SGF -- tie, MPT, collector, units) keeps
                            # its MW; only the other machines of the area move
HOLD_TOL_MW  = 0.5          # stop when every held area is within this
HOLD_PASSES  = 6            # solve + rescale passes at most
ONE_AT_A_TIME = True        # True = one plant per step: add it, solve, redispatch
                            # its area, solve again, keep it only if the case
                            # converged and the area balances; else go back
REQUIRE_BALANCE = True      # True = a plant whose area cannot be brought back
                            # within HOLD_TOL_MW is taken out again (skipped)
RESPECT_LIMITS = True       # True = rescaled machines stay within PMIN..PMAX
COPIED_AT_PMAX = True       # True = every copied (extra) machine goes in at its
                            # PMAX, not at SOURCE's dispatch; the area is then
                            # balanced by the OTHER machines as usual. A machine
                            # whose PMAX is a placeholder (>= 9000 MW) or out of
                            # service keeps SOURCE's PGEN, and the log says so.

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
_BAD_STRINGS = set()


def _col(fn, *args):
    """One subsystem-array call -> list of columns.

       ONE BAD STRING MUST NOT BLANK THE WHOLE TABLE. PSS/E answers ierr for the
       whole call when any one of the strings asked for is not one this build
       knows (v34 amachreal), and every column came back empty. So the call is
       made once with all of them and, if that fails, once per string: a column
       this build does not have is None, and is said once in the log."""
    *head, strings = args
    try:
        ie, cols = fn(*args)
        if ie in (0, None) and cols is not None:
            return cols
    except Exception:
        pass
    if not isinstance(strings, (list, tuple)) or len(strings) < 2:
        return []
    out, got = [], False
    for s in strings:
        try:
            ie, c = fn(*(list(head) + [[s]]))
        except Exception:
            ie, c = 1, None
        if ie in (0, None) and c:
            out.append(c[0])
            got = True
        else:
            out.append(None)
            key = "%s %s" % (getattr(fn, "__name__", "?"), s)
            if key not in _BAD_STRINGS:
                _BAD_STRINGS.add(key)
                say("  (this PSS/E does not answer %s('%s') -- a default is used)" % (getattr(fn, "__name__", "?"), s))
    return out if got else []


def _v(cols, n, k, default):
    """cols[n][k], or default when that column was not available."""
    try:
        c = cols[n]
        return default if c is None else c[k]
    except Exception:
        return default


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
            "type": int(_v(ints, 1, k, 1)), "area": int(_v(ints, 2, k, 1)), "zone": int(_v(ints, 3, k, 1)),
            "owner": int(_v(ints, 4, k, 1)), "kv": float(_v(reals, 0, k, 0.0)), "vm": float(_v(reals, 1, k, 1.0)),
            "va": float(_v(reals, 2, k, 0.0)),
            "lim": [float(_v(reals, n, k, d)) for n, d in ((3, 1.1), (4, 0.9), (5, 1.1), (6, 0.9))],
            "name": _strip(_v(names, 0, k, ""))}
    mi = _col(psspy.amachint, -1, 4, ["NUMBER", "STATUS", "WMOD"])
    mr = _col(psspy.amachreal, -1, 4, ["PGEN", "QGEN", "QMAX", "QMIN", "PMAX", "PMIN", "MBASE",
                                       "RSOURCE", "XSOURCE", "RTRAN", "XTRAN", "GENTAP", "WPF"])
    mc = _col(psspy.amachchar, -1, 4, ["ID"])
    mz = _col(psspy.amachcplx, -1, 4, ["ZSORCE", "XTRAN"])
    for k in range(len(mi[0]) if mi else 0):
        key = (int(mi[0][k]), _strip(mc[0][k]))
        zs = complex(_v(mz, 0, k, 1j))
        zt = complex(_v(mz, 1, k, 0j))
        r = [float(_v(mr, 0, k, 0.0)), float(_v(mr, 1, k, 0.0)), float(_v(mr, 2, k, 9999.0)),
             float(_v(mr, 3, k, -9999.0)), float(_v(mr, 4, k, 9999.0)), float(_v(mr, 5, k, -9999.0)),
             float(_v(mr, 6, k, 100.0)), float(_v(mr, 7, k, zs.real)), float(_v(mr, 8, k, zs.imag)),
             float(_v(mr, 9, k, zt.real)), float(_v(mr, 10, k, zt.imag)), float(_v(mr, 11, k, 1.0)),
             float(_v(mr, 12, k, 1.0))]
        C["mach"][key] = {"st": int(_v(mi, 1, k, 1)), "wmod": int(_v(mi, 2, k, 0)), "r": r}
    gi = _col(psspy.agenbusint, -1, 4, ["NUMBER", "IREG"])
    gr = _col(psspy.agenbusreal, -1, 4, ["VSPU"])
    for k in range(len(gi[0]) if gi else 0):
        C["plant"][int(gi[0][k])] = {"ireg": int(_v(gi, 1, k, 0)), "vs": float(_v(gr, 0, k, 1.0))}
    # non-transformer branches (flag 2 = all, in or out of service)
    bi = _col(psspy.abrnint, -1, 1, 1, 2, 1, ["FROMNUMBER", "TONUMBER", "STATUS"])
    bc = _col(psspy.abrnchar, -1, 1, 1, 2, 1, ["ID"])
    bx = _col(psspy.abrncplx, -1, 1, 1, 2, 1, ["RX"])
    br = _col(psspy.abrnreal, -1, 1, 1, 2, 1, ["CHARGING", "RATEA", "RATEB", "RATEC", "LENGTH"])
    for k in range(len(bi[0]) if bi else 0):
        key = (int(bi[0][k]), int(bi[1][k]), _strip(bc[0][k]))
        C["line"][key] = {"st": int(_v(bi, 2, k, 1)), "rx": _v(bx, 0, k, None), "b": float(_v(br, 0, k, 0.0)),
                          "rate": [float(_v(br, n, k, 0.0)) for n in (1, 2, 3)], "len": float(_v(br, 4, k, 0.0))}
    # two-winding transformers (flag 2 = all)
    ti = _col(psspy.atrnint, -1, 1, 1, 2, 1, ["FROMNUMBER", "TONUMBER", "STATUS"])
    tc = _col(psspy.atrnchar, -1, 1, 1, 2, 1, ["ID"])
    tx = _col(psspy.atrncplx, -1, 1, 1, 2, 1, ["RXNOM"]) or _col(psspy.atrncplx, -1, 1, 1, 2, 1, ["RXACT"])
    tr = _col(psspy.atrnreal, -1, 1, 1, 2, 1, ["RATIO", "RATIO2", "ANGLE", "RATEA", "RATEB", "RATEC", "SBASE1"])
    for k in range(len(ti[0]) if ti else 0):
        key = (int(ti[0][k]), int(ti[1][k]), _strip(tc[0][k]))
        C["xf2"][key] = {"st": int(_v(ti, 2, k, 1)), "rx": _v(tx, 0, k, None),
                         "ratio": float(_v(tr, 0, k, 1.0)), "ratio2": float(_v(tr, 1, k, 1.0)),
                         "ang": float(_v(tr, 2, k, 0.0)),
                         "rate": [float(_v(tr, n, k, 0.0)) for n in (3, 4, 5)], "sbase": float(_v(tr, 6, k, 100.0))}
    t3 = _col(psspy.atr3int, -1, 1, 1, 2, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER"])
    t3c = _col(psspy.atr3char, -1, 1, 1, 2, 1, ["ID"])
    for k in range(len(t3[0]) if t3 else 0):
        C["xf3"].append((int(t3[0][k]), int(t3[1][k]), int(t3[2][k]), _strip(_v(t3c, 0, k, "1"))))
    li = _col(psspy.aloadint, -1, 4, ["NUMBER", "STATUS"])
    lc = _col(psspy.aloadchar, -1, 4, ["ID"])
    lx = _col(psspy.aloadcplx, -1, 4, ["MVANOM"])
    for k in range(len(li[0]) if li else 0):
        C["load"][(int(li[0][k]), _strip(_v(lc, 0, k, "1")))] = {"st": int(_v(li, 1, k, 1)), "s": _v(lx, 0, k, 0j)}
    si = _col(psspy.afxshuntint, -1, 4, ["NUMBER", "STATUS"])
    sc = _col(psspy.afxshuntchar, -1, 4, ["ID"])
    sx = _col(psspy.afxshuntcplx, -1, 4, ["SHUNTNOM"])
    for k in range(len(si[0]) if si else 0):
        C["fxsh"][(int(si[0][k]), _strip(_v(sc, 0, k, "1")))] = {"st": int(_v(si, 1, k, 1)), "s": _v(sx, 0, k, 0j)}
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


def select(S, T, want=None, extra_mach=None, added=()):
    new = set(S["bus"]) - set(T["bus"])
    if want is None:
        want = _expand(PLANT_BUSES) if PLANT_BUSES else set(new)
    errs = []
    for b in sorted(want):
        if b not in S["bus"]:
            errs.append("bus %d is not in SOURCE" % b)
        elif b in T["bus"]:
            errs.append("bus %d is already in TARGET -- not copied over it" % b)
    have = set(T["bus"]) | want | set(added)
    lines = {k: v for k, v in S["line"].items() if (k[0] in want or k[1] in want)}
    xf2 = {k: v for k, v in S["xf2"].items() if (k[0] in want or k[1] in want)}
    for k in list(lines) + list(xf2):
        if k[0] not in have or k[1] not in have:
            errs.append("%s %d-%d ck%s ends on bus %d, which is neither selected nor in TARGET"
                        % ("line" if k in lines else "transformer", k[0], k[1], k[2],
                           k[1] if k[0] in have else k[0]))
    xf3 = [k for k in S["xf3"] if any(b in want for b in k[:3])]
    for k in xf3:
        for b in k[:3]:
            if b not in have:
                errs.append("3-winding transformer %d-%d-%d ck%s ends on bus %d, which is neither "
                            "selected nor in TARGET" % (k + (b,)))
    for b in sorted(set(S["swsh"]) & want):
        errs.append("switched shunt at bus %d is not copied by this script -- add it by hand" % b)
    mach = {k: v for k, v in S["mach"].items() if k[0] in want}
    if COPY_MACHINES_ON_EXISTING_BUSES:
        mach.update({k: v for k, v in S["mach"].items()
                     if k[0] in T["bus"] and k not in T["mach"]})
    for k in extra_mach or ():
        mach[k] = S["mach"][k]
    # EVERY COPIED BUS MUST REACH TARGET'S NETWORK through the copied elements.
    adj = {}
    for k in list(lines) + list(xf2):
        adj.setdefault(k[0], set()).add(k[1])
        adj.setdefault(k[1], set()).add(k[0])
    for k in xf3:
        for a in k[:3]:
            for b in k[:3]:
                if a != b:
                    adj.setdefault(a, set()).add(b)
    seen = set(b for b in adj if b in T["bus"] or b in added)
    stack = list(seen)
    while stack:
        b = stack.pop()
        for c in adj.get(b, ()):
            if c not in seen:
                seen.add(c)
                stack.append(c)
    for b in sorted(want - seen):
        errs.append("bus %d would be an island: nothing selected connects it to TARGET" % b)
    return want, mach, dict(lines, **{}), dict(xf2, **{"_xf3": xf3}) if xf3 else xf2, errs


# ---------------------------------------------------------------- distance / plants
def graph(C):
    """{bus: {neighbour: |R+jX|}} over in-service lines and transformers."""
    g = {}

    def edge(a, b, z):
        if a == b:
            return
        g.setdefault(a, {})
        g.setdefault(b, {})
        if b not in g[a] or z < g[a][b]:
            g[a][b] = g[b][a] = z
    for k, v in list(C["line"].items()) + list(C["xf2"].items()):
        if v["st"] == 1:
            edge(k[0], k[1], abs(complex(v["rx"])) if v.get("rx") is not None else 0.0)
    for w1, w2, w3, _ck in C["xf3"]:
        edge(w1, w2, 0.0)
        edge(w2, w3, 0.0)
        edge(w1, w3, 0.0)
    return g


def distances(g, src):
    """({bus: nodes}, {bus: summed |Z| along the fewest-nodes path})."""
    hops, z = {src: 0}, {src: 0.0}
    frontier = [src]
    while frontier:
        nxt = []
        for a in frontier:
            for b, zz in g.get(a, {}).items():
                if b not in hops:
                    hops[b] = hops[a] + 1
                    z[b] = z[a] + zz
                    nxt.append(b)
                elif hops[b] == hops[a] + 1 and z[a] + zz < z[b]:
                    z[b] = z[a] + zz
        frontier = nxt
    return hops, z


def plant_pocket(S, T, poi):
    """The buses SOURCE has behind `poi` that TARGET does not: walk SOURCE out
       from the POI, stepping only onto buses TARGET lacks. That is the plant --
       tie, main transformer, collector, GSUs, units -- and nothing of the grid."""
    gs = graph(S)
    seen, stack = set(), [poi]
    while stack:
        a = stack.pop()
        for b in gs.get(a, {}):
            if b not in seen and b not in T["bus"]:
                seen.add(b)
                stack.append(b)
    # out-of-service elements still belong to the plant
    for k in list(S["line"]) + list(S["xf2"]):
        for a, b in ((k[0], k[1]), (k[1], k[0])):
            if a in seen and b not in seen and b not in T["bus"]:
                seen.add(b)
    return seen


def pick_plants(S, T):
    """Plants within MAX_NODES (and MAX_Z_PU) of a project POI, in TARGET."""
    gt = graph(T)
    dist = {}
    for pj, poi in sorted(PROJECT_POIS.items()):
        if poi not in T["bus"]:
            say("  *** project %s POI %d is not in TARGET -- left out of the distances ***" % (pj, poi))
            continue
        dist[pj] = distances(gt, int(poi))
    say("")
    say("PLANT DISTANCE FROM EACH PROJECT POI (nodes / |Z| pu), counted in TARGET")
    say("  %-14s %5s %-8s %-8s  %s   %s" % ("plant", "MW", "type", "POI bus",
        "  ".join("%-16s" % pj for pj in sorted(dist)), "copy?"))
    want, extra, chosen = set(), set(), []
    for name, mw, typ, poi in PLANTS:
        poi = int(poi)
        cells, near = [], []
        for pj in sorted(dist):
            h, z = dist[pj]
            if poi in h:
                cells.append("%3d / %-10.4f" % (h[poi], z[poi]))
                if h[poi] <= MAX_NODES and (MAX_Z_PU is None or z[poi] <= MAX_Z_PU):
                    near.append(pj)
            else:
                cells.append("%-16s" % ("POI not in case" if poi not in T["bus"] else "not connected"))
        take = bool(near) and (not ONLY_PLANTS or name in ONLY_PLANTS)
        note = ("YES (near %s)" % ", ".join(near)) if take else ("no" if not near else "near, not in ONLY_PLANTS")
        if take and poi not in T["bus"]:
            note = "*** POI %d not in TARGET -- cannot attach ***" % poi
            take = False
        pocket = set()
        if take:
            pocket = plant_pocket(S, T, poi)
            pm = [k for k in S["mach"] if k[0] in pocket or (k[0] == poi and k not in T["mach"])]
            if not pocket and not pm:
                note = "already in TARGET (nothing behind POI %d to add)" % poi
                take = False
            else:
                note += " -- %d bus(es), %d machine(s), %.1f MW in SOURCE" % (
                    len(pocket), len(pm), sum(S["mach"][k]["r"][0] for k in pm if S["mach"][k]["st"] == 1))
                extra |= set(k for k in pm if k[0] == poi)
        say("  %-14s %5s %-8s %-8d  %s   %s" % (name, mw, typ, poi, "  ".join(cells), note))
        if take:
            want |= pocket
            pex = set(k for k in pm if k[0] == poi)
            # TWO PLANTS ON ONE POI (Holcomb: GEN-2023-172 and -173) share what
            # is behind it -- one step, both names.
            same = [c for c in chosen if c[1] == poi]
            if same:
                c = same[0]
                chosen[chosen.index(c)] = (c[0] + " + " + name, poi, sorted(set(c[2]) | pocket), c[3] | pex)
            else:
                chosen.append((name, poi, sorted(pocket), pex))
    for name, poi, pocket, _x in chosen:
        say("  %s behind POI %d: %s" % (name, poi, ", ".join(str(b) for b in pocket) or "(machines at the POI bus only)"))
    return want, extra, chosen


# ---------------------------------------------------------------- 3-winding
# A THREE-WINDING TRANSFORMER IS BUILT FROM psspy's OWN HELP TEXT.
# The array layouts of three_wnd_imped_data_* and three_wnd_winding_data_* moved
# between PSS/E builds (STAT is INTGAR(9) in v34, not INTGAR(1)), and calling a
# Fortran API with a guessed layout has crashed PSS/E before. So the help text
# is read, every value is placed by its NAME (R1-2, X1-2, ..., STAT, CZ, CW,
# WINDV, ANG, RATA ...), and the transformer is read back afterwards. A call
# whose names are not all in the text is not made: the step fails, the plant is
# skipped, and the help text goes to the log.
def _doc_map(fn):
    """{ARRAY: (length, {NAME: 0-based index})} from psspy.<fn>.__doc__."""
    f = getattr(psspy, fn, None)
    doc = (getattr(f, "__doc__", None) or "") if f else ""
    out = {}
    for arr in ("INTGAR", "REALARI", "REALAR", "RATINGS", "RATING"):
        n = 0
        m = re.search(arr + r"\s*=?\s*\(?\w*\)?\s*(?:is\s+)?(?:an\s+)?array\s+of\s+(\d+)", doc, re.I)
        if m:
            n = int(m.group(1))
        names = {}
        for m in re.finditer(arr + r"\s*\(\s*(\d+)\s*\)\s*[=:]?\s*([^\n]*)", doc, re.I):
            idx = int(m.group(1))
            n = max(n, idx)
            tok = m.group(2).strip().split()
            if tok:
                names.setdefault(tok[0].upper().rstrip(",.;:"), idx - 1)
        if names:
            out[arr] = (n, names)
    return out, doc


def _pick(names, *keys):
    for k in keys:
        if k in names:
            return names[k]
    return None


def _say_doc(fn, doc):
    say("  ---- psspy.%s help text, as this PSS/E states it ----" % fn)
    for ln in (doc or "(none)").splitlines()[:80]:
        say("  | " + ln.rstrip()[:150])
    say("  ---- end ----")


def xf3_read(k):
    """{st, rx: {12, 23, 31}, wind: {1: {ratio, ang, rate}, ...}} for one
       3-winding transformer of the LOADED case, or (None, why)."""
    w1, w2, w3, ck = k
    d = {"rx": {}, "wind": {}}
    try:
        ie, st = psspy.tr3int(w1, w2, w3, ck, "STATUS")
        d["st"] = int(st) if ie in (0, None) else 1
    except Exception:
        d["st"] = 1
    for pair, names in (("12", ("RX1-2", "RX1-2NOM", "RX1-2ACT")),
                        ("23", ("RX2-3", "RX2-3NOM", "RX2-3ACT")),
                        ("31", ("RX3-1", "RX3-1NOM", "RX3-1ACT"))):
        for s in names:
            try:
                ie, v = psspy.tr3dt2(w1, w2, w3, ck, s)
            except Exception:
                continue
            if ie in (0, None) and v is not None:
                d["rx"][pair] = complex(v)
                break
        if pair not in d["rx"]:
            return None, "impedance %s not readable (tr3dt2)" % pair
    for n, (a, b, c) in ((1, (w1, w2, w3)), (2, (w2, w3, w1)), (3, (w3, w1, w2))):
        w = {}
        for key, names in (("ratio", ("RATIO",)), ("ang", ("ANGLE", "ANG")),
                           ("ra", ("RATEA", "RATE1")), ("rb", ("RATEB", "RATE2")), ("rc", ("RATEC", "RATE3"))):
            for s in names:
                try:
                    ie, v = psspy.wnddat(a, b, c, ck, s)
                except Exception:
                    continue
                if ie in (0, None) and v is not None:
                    w[key] = float(v)
                    break
        if "ratio" not in w:
            return None, "winding %d ratio not readable (wnddat)" % n
        d["wind"][n] = w
    return d, ""


def add_xf3(k, d):
    """Create one 3-winding transformer by name-placed arrays; '' on failure."""
    w1, w2, w3, ck = k
    made = ""
    for fn in ("three_wnd_imped_data_4", "three_wnd_imped_data_3"):
        if not hasattr(psspy, fn):
            continue
        dm, doc = _doc_map(fn)
        ig = dm.get("INTGAR")
        ra = dm.get("REALARI") or dm.get("REALAR")
        pos = {}
        if ra:
            for key, names in (("r12", ("R1-2",)), ("x12", ("X1-2",)), ("r23", ("R2-3",)),
                               ("x23", ("X2-3",)), ("r31", ("R3-1",)), ("x31", ("X3-1",))):
                pos[key] = _pick(ra[1], *names)
        ist = _pick(ig[1], "STAT", "STATUS") if ig else None
        icz = _pick(ig[1], "CZ") if ig else None
        icw = _pick(ig[1], "CW") if ig else None
        if not ra or not ig or None in pos.values() or ist is None:
            say("  *** %s: the help text does not name every value needed (STAT, R1-2 ... X3-1) "
                "-- not called" % fn)
            _say_doc(fn, doc)
            continue
        ia = [_i] * ig[0]
        ia[ist] = d["st"]
        if icz is not None:
            ia[icz] = 1          # impedances on the SYSTEM base, as read
        if icw is not None:
            ia[icw] = 1          # winding ratios in pu of the bus base kV, as read
        rr = [_f] * ra[0]
        for key, z in (("12", d["rx"]["12"]), ("23", d["rx"]["23"]), ("31", d["rx"]["31"])):
            rr[pos["r" + key]] = z.real
            rr[pos["x" + key]] = z.imag
        kw = {"intgar": ia, ("realari" if "REALARI" in dm else "realar"): rr}
        try:
            rc = getattr(psspy, fn)(w1, w2, w3, ck, **kw)
        except Exception as e:
            say("  *** %s raised: %s" % (fn, e))
            _say_doc(fn, doc)
            continue
        if not ok(rc):
            say("  *** %s answered ierr=%s" % (fn, rc))
            continue
        made = fn
        break
    if not made:
        return ""
    for n in (1, 2, 3):
        w = d["wind"][n]
        done = False
        for fn in ("three_wnd_winding_data_5", "three_wnd_winding_data_4", "three_wnd_winding_data_3"):
            if not hasattr(psspy, fn):
                continue
            dm, doc = _doc_map(fn)
            ra = dm.get("REALARI") or dm.get("REALAR")
            rt = dm.get("RATINGS") or dm.get("RATING")
            iv = _pick(ra[1], "WINDV", "WINDV1") if ra else None
            ian = _pick(ra[1], "ANG", "ANGLE") if ra else None
            if iv is None:
                say("  *** %s: the help text does not name WINDV -- not called" % fn)
                _say_doc(fn, doc)
                continue
            rr = [_f] * ra[0]
            rr[iv] = w["ratio"]
            if ian is not None and "ang" in w:
                rr[ian] = w["ang"]
            kw = {("realari" if "REALARI" in dm else "realar"): rr}
            rates = [w.get("ra"), w.get("rb"), w.get("rc")]
            if rt:
                rl = [_f] * rt[0]
                for j, key in enumerate(("RATE1", "RATE2", "RATE3")):
                    q = _pick(rt[1], key, ("RATA", "RATB", "RATC")[j])
                    if q is not None and rates[j] is not None:
                        rl[q] = rates[j]
                kw["ratings" if "RATINGS" in dm else "rating"] = rl
            else:
                for j, key in enumerate(("RATA", "RATB", "RATC")):
                    q = _pick(ra[1], key)
                    if q is not None and rates[j] is not None:
                        rr[q] = rates[j]
            try:
                rc = getattr(psspy, fn)(w1, w2, w3, ck, n, **kw)
            except TypeError:
                try:
                    rc = getattr(psspy, fn)(w1, w2, w3, ck, warg=n, **kw)
                except Exception as e:
                    say("  *** %s raised: %s" % (fn, e))
                    _say_doc(fn, doc)
                    continue
            except Exception as e:
                say("  *** %s raised: %s" % (fn, e))
                _say_doc(fn, doc)
                continue
            if ok(rc):
                done = True
                break
        if not done:
            say("  *** winding %d of %d-%d-%d ck%s: no winding call took" % (n, w1, w2, w3, ck))
            return ""
    # READ BACK: the copy must hold the impedances and ratios that were sent.
    got, why = xf3_read(k)
    if got is None:
        say("  *** %d-%d-%d ck%s created but cannot be read back: %s" % (k + (why,)))
        return ""
    for pair in ("12", "23", "31"):
        a, b = got["rx"][pair], d["rx"][pair]
        if abs(a.imag - b.imag) > max(1e-5, 0.02 * abs(b.imag)) or abs(a.real - b.real) > max(1e-5, 0.02 * abs(b.real) + 1e-5):
            say("  *** %d-%d-%d ck%s: X%s reads %.5f, sent %.5f -- NOT what was intended" % (w1, w2, w3, ck, pair, a.imag, b.imag))
            return ""
    for n in (1, 2, 3):
        if abs(got["wind"][n]["ratio"] - d["wind"][n]["ratio"]) > 0.002:
            say("  *** %d-%d-%d ck%s: winding %d ratio reads %.4f, sent %.4f" % (w1, w2, w3, ck, n,
                got["wind"][n]["ratio"], d["wind"][n]["ratio"]))
            return ""
    return made


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


def copied_mw(m):
    """The MW a copied machine is put in at."""
    pg, pmax = m["r"][0], m["r"][4]
    if COPIED_AT_PMAX and m["st"] == 1 and 0.0 < pmax < 9000.0:
        return pmax
    return pg


def add_machine(b, mid, m, plant):
    try:
        if b in plant:
            psspy.plant_data(b, plant[b]["ireg"], [plant[b]["vs"], _f])
        else:
            psspy.plant_data(b, _i, [_f, _f])
    except Exception:
        pass
    r = m["r"]   # PGEN QGEN QMAX QMIN PMAX PMIN MBASE RSOURCE XSOURCE RTRAN XTRAN GENTAP WPF
    realar = [copied_mw(m), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], r[11],
              _f, _f, _f, _f, r[12]]
    intgar = [m["st"], _i, _i, _i, _i, m["wmod"]]
    try:
        return ok(psspy.machine_data_2(b, mid, intgar, realar))
    except Exception:
        return False


def add_line(k, v):
    if v["rx"] is None:
        return ""
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


def project_machines(T):
    """{project: [(bus, id, MW)]} -- the machines radially behind each project
       POI in TARGET. Taking the POI out of the network, whatever is then cut
       off from the grid hangs on it alone: that is the plant, whatever kV its
       tie is at (Santa Fe's is 345 kV). A neighbour that still reaches the grid
       (more than PLANT_MAX_BUSES buses) is grid, not plant."""
    g = graph(T)
    out = {}
    for pj, poi in sorted(PROJECT_POIS.items()):
        poi = int(poi)
        if poi not in T["bus"]:
            continue
        plant = set([poi])
        for nb in g.get(poi, {}):
            if nb in plant:
                continue
            seen, stack, grid = set([nb]), [nb], False
            while stack:
                a = stack.pop()
                for b in g.get(a, {}):
                    if b == poi or b in seen:
                        continue
                    seen.add(b)
                    stack.append(b)
                if len(seen) > PLANT_MAX_BUSES:
                    grid = True
                    break
            if not grid:
                plant |= seen
        out[pj] = sorted((b, mid, m["r"][0]) for (b, mid), m in T["mach"].items()
                         if b in plant and m["st"] == 1)
    return out


PLANT_MAX_BUSES = 400       # a piece cut off behind a POI larger than this is grid


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
    if PLANTS:
        pw, pextra, chosen = pick_plants(S, T)
        if not chosen:
            say("no plant within %d node(s) of a project POI needs copying" % MAX_NODES)
    else:
        w0 = _expand(PLANT_BUSES) if PLANT_BUSES else set(S["bus"]) - set(T["bus"])
        chosen = [("selected buses", None, sorted(w0), set())]
    if not ONE_AT_A_TIME and len(chosen) > 1:
        chosen = [(" + ".join(c[0] for c in chosen), None,
                   sorted(set(b for c in chosen for b in c[2])), set(k for c in chosen for k in c[3]))]
    stem = os.path.splitext(TARGET_SAV)[0]
    n_bus = len(set(b for c in chosen for b in c[2]))
    out = OUT_SAV or "%s_plus_%dbus.sav" % (stem, n_bus)
    out_stem = os.path.splitext(out)[0]
    compare(S, T, out_stem + "_compare.csv")

    # ALL CHECKS FIRST, every step against what the steps before it add.
    steps, added, refused = [], set(), []
    for name, poi, pocket, pex in chosen:
        want, mach, lines, xf2, errs = select(S, T, set(pocket), pex, added)
        if errs:
            refused.append((name, errs))
            continue
        if not want and not mach:
            continue
        steps.append((name, poi, want, mach, lines, xf2))
        added |= want
    # THREE-WINDING TRANSFORMERS: their data read from SOURCE now
    if any("_xf3" in s[5] for s in steps):
        psspy.case(SOURCE_SAV)
        keep = []
        for st in steps:
            keys = st[5].get("_xf3")
            if keys:
                det, bad = {}, []
                for k in keys:
                    d, why = xf3_read(k)
                    if d is None:
                        bad.append("3-winding transformer %d-%d-%d ck%s: %s" % (k + (why,)))
                    else:
                        det[k] = d
                if bad:
                    refused.append((st[0], bad))
                    continue
                st[5]["_xf3"] = det
            keep.append(st)
        steps = keep
    for name, errs in refused:
        say("")
        say("*** %s NOT COPIED -- fix these first:" % name)
        for e in errs:
            say("  - " + e)
    if not steps:
        say("")
        say("nothing to copy" + (" (see above)" if refused else ": TARGET already has it all"))
        _write_log(out_stem + ".txt")
        raise SystemExit(1 if refused else 0)

    # TARGET as it is: the area MW every step goes back to
    psspy.case(TARGET_SAV)
    if not solve():
        say("*** TARGET itself does not solve -- nothing done ***")
        _write_log(out_stem + ".txt")
        raise SystemExit(1)
    base_mw = {}
    swing = set(b for b, d in T["bus"].items() if d["type"] == 3)
    excl = _expand(HOLD_EXCLUDE) | swing
    if PROTECT_PROJECTS and PROJECT_POIS:
        pm = project_machines(T)
        say("")
        say("PROJECT PLANTS -- never redispatched (PROTECT_PROJECTS)")
        for pj in sorted(pm):
            ms = pm[pj]
            say("  %-14s POI %-7d %3d machine(s) %8.1f MW: %s" % (
                pj, PROJECT_POIS[pj], len(ms), sum(m[2] for m in ms),
                ", ".join("%d '%s'" % (b, mid) for b, mid, _p in ms[:12]) + (" ..." if len(ms) > 12 else "")))
            if not ms:
                say("  *** %s: no machine found behind POI %d -- check PROJECT_POIS ***" % (pj, PROJECT_POIS[pj]))
            excl |= set(b for b, _mid, _p in ms)
        project_mw0 = dict((pj, sum(m[2] for m in ms)) for pj, ms in pm.items())
    else:
        pm, project_mw0 = {}, {}
    ckpt = out_stem + "_step.sav"
    psspy.save(ckpt)
    done, skipped, all_mach = [], [], {}
    for k, (name, poi, want, mach, lines, xf2) in enumerate(steps, 1):
        say("")
        say("=" * 92)
        say("STEP %d of %d: %s%s -- %d bus(es), %d machine(s), %.1f MW"
            % (k, len(steps), name, (" at POI %d" % poi) if poi else "", len(want), len(mach),
               sum(copied_mw(m) for m in mach.values() if m["st"] == 1)))
        say("=" * 92)
        areas = sorted(set(HOLD_AREAS) if HOLD_AREAS else
                       set(S["bus"][b]["area"] for (b, _m) in mach if b in S["bus"]))
        for a in areas:
            base_mw.setdefault(a, area_gen(T, a))     # TARGET's own MW, never a step's
        n_bad = add_step(S, want, mach, lines, xf2)
        why = ""
        if n_bad:
            why = "%d element(s) could not be added" % n_bad
        elif not solve():
            why = "the case does not converge with this plant added"
        else:
            say("  solved with the plant added")
            if HOLD_AREA_MW and areas:
                skip = excl | set(b for (b, _m) in list(all_mach) + list(mach))
                held = hold_areas(dict((a, base_mw[a]) for a in areas), skip)
                if not solve():
                    why = "the case does not converge after the redispatch"
                elif not held and REQUIRE_BALANCE:
                    why = "the area could not be brought back within %.2f MW" % HOLD_TOL_MW
            if not why:
                for a in areas:
                    now = sum(m[2] for m in live_area_machines(a, ()))
                    say("  area %-5d TARGET %.1f MW -> now %.1f MW (%+.2f), converged"
                        % (a, base_mw[a], now, now - base_mw[a]))
        if why:
            say("  *** %s -- %s is SKIPPED, the case goes back to the end of the last good step ***" % (why, name))
            psspy.case(ckpt)
            skipped.append((name, why))
            continue
        psspy.save(ckpt)
        done.append((name, poi, sorted(want)))
        all_mach.update(mach)
    # the last good step is the answer
    psspy.case(ckpt)
    solve()
    say("")
    say("AREA GENERATION (MW)        TARGET before    OUT after    difference")
    for a in sorted(base_mw):
        now = sum(m[2] for m in live_area_machines(a, ()))
        say("  area %-5d            %12.1f %12.1f %+12.1f" % (a, base_mw[a], now, now - base_mw[a]))
    if pm:
        say("")
        say("PROJECT PLANTS (MW)          TARGET before    OUT after")
        live = dict(((b, mid), p) for a in sorted(set(T["bus"][b]["area"] for ms in pm.values() for b, _m, _p in ms))
                    for b, mid, p, _x, _y, _z in live_area_machines(a, ()))
        for pj in sorted(pm):
            now = sum(live.get((b, mid), 0.0) for b, mid, _p in pm[pj])
            say("  %-14s              %12.1f %12.1f%s" % (pj, project_mw0[pj], now,
                "" if abs(now - project_mw0[pj]) < 0.5 else "   *** CHANGED ***"))
    say("  copied: %s" % (", ".join(d[0] for d in done) or "nothing"))
    for name, why in skipped:
        say("  SKIPPED %s: %s" % (name, why))
    for name, _e in refused:
        say("  NOT COPIED %s (see the checks above)" % name)
    if not done:
        say("*** no plant could be added -- NOT saved ***")
        _write_log(out_stem + ".txt")
        raise SystemExit(1)
    if not ok(psspy.save(out)):
        say("*** could not save %s ***" % out)
        raise SystemExit(1)
    try:
        os.remove(ckpt)
    except Exception:
        pass
    say("")
    say("SAVED %s" % out)
    if any(d[1] for d in done):
        # THE DISTANCES THE PLANTS WERE CHOSEN BY, read again on the saved case:
        # a plant hangs radially off its own POI, so nothing should have moved.
        O = read_case(out)
        g0, g1 = graph(T), graph(O)
        say("")
        say("POI DISTANCE CHECK (nodes, TARGET -> OUT)")
        for name, poi, _p in done:
            if not poi:
                continue
            cells = []
            for pj, ppoi in sorted(PROJECT_POIS.items()):
                if ppoi not in T["bus"]:
                    continue
                h0 = distances(g0, int(ppoi))[0].get(poi)
                h1 = distances(g1, int(ppoi))[0].get(poi)
                cells.append("%s %s->%s%s" % (pj, h0, h1, "" if h0 == h1 else " *** CHANGED ***"))
            say("  %-28s POI %-7d %s" % (name, poi, "   ".join(cells)))
    if SOURCE_DYR:
        copy_dyr(all_mach, OUT_DYR or out_stem + ".dyr")
    _write_log(out_stem + ".txt")


def add_step(S, want, mach, lines, xf2):
    """Add one plant's elements; the number that failed."""
    n_bad = 0
    for b in sorted(want):
        fn = add_bus(b, S["bus"][b])
        say("  bus  %-7d %-12s %7.1f kV area %-4d %s" % (b, S["bus"][b]["name"], S["bus"][b]["kv"],
                                                         S["bus"][b]["area"], fn or "*** FAILED ***"))
        n_bad += not fn
    for k, v in sorted(lines.items()):
        fn = add_line(k, v)
        say("  line %d-%d ck%s R=%.5f X=%.5f B=%.5f %s" % (k[0], k[1], k[2], complex(v["rx"] or 0).real,
                                                       complex(v["rx"] or 0).imag, v["b"], fn or "*** FAILED ***"))
        n_bad += not fn
    for k, v in sorted(xf2.get("_xf3", {}).items()):
        fn = add_xf3(k, v)
        say("  3W   %d-%d-%d ck%s X12=%.5f X23=%.5f X31=%.5f ratios %.4f/%.4f/%.4f %s"
            % (k + (v["rx"]["12"].imag, v["rx"]["23"].imag, v["rx"]["31"].imag,
                    v["wind"][1]["ratio"], v["wind"][2]["ratio"], v["wind"][3]["ratio"], fn or "*** FAILED ***")))
        n_bad += not fn
    for k, v in sorted((kk, vv) for kk, vv in xf2.items() if kk != "_xf3"):
        fn = add_xf2(k, v)
        say("  xfmr %d-%d ck%s R=%.5f X=%.5f (system base) ratio %.4f/%.4f, fixed tap %s"
            % (k[0], k[1], k[2], complex(v["rx"] or 0).real, complex(v["rx"] or 0).imag,
               v["ratio"], v["ratio2"], fn or "*** FAILED ***"))
        n_bad += not fn
    for (b, mid), m in sorted(mach.items()):
        good = add_machine(b, mid, m, S["plant"])
        p_in = copied_mw(m)
        note = ""
        if COPIED_AT_PMAX and m["st"] == 1 and p_in == m["r"][0] and not (0.0 < m["r"][4] < 9000.0):
            note = "  (PMAX %.0f is a placeholder -- kept SOURCE's PGEN)" % m["r"][4]
        elif p_in != m["r"][0]:
            note = "  (at PMAX; SOURCE had %.1f)" % m["r"][0]
        say("  gen  %d '%s' P=%.1f Q=%.1f Pmax=%.1f MBASE=%.1f%s %s" % (b, mid, p_in, m["r"][1], m["r"][4],
                                                                     m["r"][6], note, "" if good else "*** FAILED ***"))
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
    return n_bad


def _write_log(path):
    try:
        with open(path, "w") as fh:
            fh.write("\n".join(_LOG) + "\n")
        print("log: %s" % path)
    except Exception as e:
        print("could not write %s (%s)" % (path, e))


if __name__ == "__main__":
    main()
