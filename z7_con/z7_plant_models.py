# -*- coding: utf-8 -*-
"""Plant models on an infinite bus -- one package per project, for the client:
   <name>.sav, <name>.raw and <name>.dyr.

   WHAT EACH PACKAGE HOLDS
     Everything behind the project's Point of Interconnection (POI), exactly as
     the study's project case has it:
       - the Existing Generating Facility (EGF): units, GSUs, collector system,
         main power transformers (MPT) and gen-tie;
       - the Surplus Generating Facility (SGF): units, GSUs, collector system and
         its connection (its own MPT and tie, or onto the EGF's MPTs -- as built);
     and the POI bus itself as the INFINITE BUS: the swing bus, with one machine
     'IB' (MBASE 100 000 MVA) held at the POI voltage and angle of the full-case
     solution, and GENCLS with H = 0 in the .dyr (PSS/E's infinite inertia).
     Nothing of the grid is kept -- not even equipment that sits on the POI bus.

   WHERE IT COMES FROM (per project; the folders are read from z7_main_f.py)
     power flow : {PROJ_FOLDER}\\<deck>_BESS_<project>_<MW>MW[_tag]_NEWPLANT.sav,
                  the case the study built and ran (EGF + SGF in service).
                  Scenario cases (EGF off, SGF off, capacity or POI levels,
                  diagnostic runs) are never picked.
     dynamics   : that build's snapshot (.snp + .cnv beside the .sav) -- the
                  models as the study ran them, deck changes (DyreChanges, IRF)
                  included -- written out with dyda. A record PSS/E holds
                  unchanged keeps the deck's own text (the combined deck the
                  build wrote, *_with_BESS_<project>*.dyr), so no EGF value is
                  rounded; a record the study's decks changed is written as
                  PSS/E holds it, and listed in the check file.
                  No snapshot: the combined deck text is used, and the check
                  file says that deck changes made by .idv files are not in it.

   HOW THE PLANT IS CUT OUT
     1. the full case is solved; every unit's P / Q, every bus voltage and the
        POI voltage and angle are recorded;
     2. the plant = every part of the network behind the POI (reached from a
        POI neighbour without passing through the POI) that holds an EGF or an
        SGF unit -- at most MAX_PLANT_BUSES buses each, so the grid is never
        taken; anything else behind the POI is named in the check file;
     3. every other bus is purged with all its equipment (extr); whatever is
        connected to the POI bus itself (load, shunt, machine) goes too;
     4. the POI becomes the swing bus with the infinite source 'IB';
     5. a plant that regulated a bus no longer in the model (the POI, or a
        grid bus) regulates its own terminal at the voltage the full case gave
        it -- PSS/E would otherwise hold its terminal at the remote schedule;
     6. solved with taps and switched shunts locked, as the full-case solution
        holds them, and every unit and bus compared with the full case.

   CHECKS (VALIDATE = True), each package in its own work folder
     the package is loaded back from its own files: the .sav solved and
     converted, the .dyr read (dyre_new), initialised (strt) and run with no
     disturbance for FLAT_RUN_S seconds; the largest drift of every unit's
     P, Q and terminal voltage is reported.

   ONE-LINE DIAGRAM (SLD = True)
     <name>.sld, drawn by PSS/E itself (newdiagfile / growbus / savediagfile)
     with every bus placed by this script as a tidy tree: the POI and its
     infinite bus on top, each gen-tie straight down to its main transformer,
     the collector bus below, every feeder's GSU and unit straight below that
     -- EGF feeders on the left, SGF on the right, one column per unit, one row
     per level, so every connection runs straight. The same layout is written
     as _check\\<name>_layout.svg, to compare with what PSS/E drew.

   OUTPUT -- nothing that exists is ever overwritten or deleted
     {OUT_DIR}\\<date_time>\\<name>\\<name>.sav / .raw / .dyr / .sld   the package
     {OUT_DIR}\\<date_time>\\_check\\<name>_CHECK.txt, SUMMARY.txt
     {OUT_DIR}\\<date_time>\\_work\\<name>\\                     dumps, validation run

   Run with the PSS/E Python (3.4), from the folder z7_main_f.py is in:
       python z7_plant_models.py
"""
import os
import sys
import re
import time
import glob
import collections
import traceback

# ============================================================================
# SETTINGS
# ============================================================================
# poi = the bus that becomes the infinite bus; egf = the existing units' buses.
# The SGF is found from the case itself (the study's record of the new plant).
PLANTS = collections.OrderedDict([
    ("SantaFe",       {"name": "Santa_Fe_BESS",       "poi": 765911,
                       "egf": [765912, 765922, 765932, 765935]}),
    ("IronStar",      {"name": "Iron_Star_BESS",      "poi": 560080,
                       "egf": [587313, 587317]}),
    ("EmpirePrairie", {"name": "Empire_Prairie_BESS", "poi": 761383,
                       "egf": [761379, 761382, 761400, 761403]}),
    ("EastFork",      {"name": "East_Fork_BESS",      "poi": 531429,
                       "egf": [531620, 531607]}),
])
PROJECTS = []                     # [] = every project above | ["EastFork"]
CASE_DIR = ""                     # "" = PROJ_FOLDER of z7_main_f.py (the study's project cases)
CASE_BY_PROJECT = {}              # {"EastFork": r"C:\...\x_NEWPLANT.sav"} -- pins the case
MW_BY_PROJECT = {}                # {"EmpirePrairie": 769} -- else the smallest size found
SNP_BY_PROJECT = {}               # {"EastFork": (r"...\x.cnv", r"...\x.snp")} -- else beside the case
DECK_BY_PROJECT = {}              # {"EastFork": r"...\x.dyr"} -- else the build's combined deck
OUT_DIR = r"{root}\PLANT_MODELS"  # a new <date_time> folder is made inside it
MAX_PLANT_BUSES = 400             # a part behind the POI larger than this is grid, never plant
IB_ID = "IB"                      # infinite-bus machine id
IB_MBASE = 100000.0               # infinite-bus machine base (MVA)
IB_XSOURCE = 0.01                 # pu on IB_MBASE (= 0.00001 pu on 100 MVA)
SOLVE_OPTS = [0, 0, 0, 1, 0, 0, 0, 0]   # FNSL: taps, interchange, phase shift, switched shunts locked
TOL_MW = 0.5                      # unit P / Q / bus V against the full case: larger is listed for review
TOL_MVAR = 1.0
TOL_V_PU = 0.001
VALIDATE = True                   # load each package back, initialise it and run it flat
FLAT_RUN_S = 10.0
FLAT_TOL = (0.5, 0.5, 0.001)      # MW, MVAr, pu: a larger drift in the flat run is listed for review
ADDLIB_IDV = ""                   # "" = the study's own (ADDLIB_IDV of z7_spp_p_f.py, in the case folder)
DLLS = []                         # extra user-model DLLs (full paths), if any
DYN_PARAMS = (60, 0.60, 0.0000095, 1.0 / 240.0, 0.033333)   # NITER, ACCEL, TOL, DELT, FREQFILT (the study's)
SLD = True                        # also <name>.sld: the plant drawn as a tidy tree (POI on top, units at the bottom)
SLD_DX = 2.0                      # diagram column spacing (PSS/E diagram units -- inches)
SLD_DY = 1.6                      # diagram row spacing: one row per level below the POI
SLD_POI_ON_TOP = True             # True = POI and infinite bus on top, units at the bottom | False = upside down

HERE = os.path.dirname(os.path.abspath(__file__))
_BATCH = 4000                     # buses per extr call


# ============================================================================
# SETTINGS READ FROM THE STUDY'S FILES (as text -- nothing there is run)
# ============================================================================
def _file_setting(names, var, default):
    """The literal value of `var` in the first of `names` (beside this file)
       that assigns it on one line."""
    import ast
    for fn in names:
        p = os.path.join(HERE, fn)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for ln in fh:
                    m = re.match(r"^%s\s*=\s*(.+)$" % re.escape(var), ln)
                    if not m:
                        continue
                    rhs = m.group(1)
                    for cut in range(len(rhs), 0, -1):
                        try:
                            return ast.literal_eval(rhs[:cut].strip())
                        except Exception:
                            continue
        except Exception:
            pass
    return default


MAIN_FILES = ("z7_main_f.py", "z7_main.py")
ENGINE_FILES = ("z7_spp_p_f.py", "z7_spp_p.py")
ROOT = _file_setting(MAIN_FILES, "ROOT", "") or HERE


def _is_abs(p):
    return bool(p) and (os.path.isabs(p) or p[:2] in ("\\\\", "//") or bool(re.match(r"^[A-Za-z]:[\\/]", p)))


def _abs(p, base=None):
    p = str(p or "")
    p = re.sub(r"\{root\}[\\/]?", lambda m: ROOT.rstrip("\\/") + os.sep, p)
    if os.sep == "/":
        p = p.replace("\\", "/")
    if p and not _is_abs(p):
        p = os.path.join(base or ROOT, *[q for q in re.split(r"[\\/]+", p) if q])
    return p


def _case_dir():
    return _abs(CASE_DIR) if CASE_DIR else _abs(_file_setting(MAIN_FILES, "PROJ_FOLDER", "Projects"))


# ============================================================================
# SMALL HELPERS
# ============================================================================
psspy = None
_i, _f, _s = -100000000, -1.0e20, "1"
REVIEW_MARKS = ("REVIEW", "WARNING", "***", "NOT FLAT", "SUSPECT", "LEFT OUT", "CHECK:")


class Log(object):
    def __init__(self):
        self.lines = []

    def __call__(self, msg=""):
        print(msg)
        self.lines.extend(str(msg).split("\n"))

    def review(self):
        return [ln for ln in self.lines if any(m in ln for m in REVIEW_MARKS)]

    def write(self, path):
        with open(_new(path), "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.lines) + "\n")


def _new(path):
    """`path`, after making sure nothing is there -- nothing is ever overwritten."""
    if os.path.exists(path):
        raise RuntimeError("will not overwrite %s" % path)
    return path


def _ie(rc):
    return rc[0] if isinstance(rc, (list, tuple)) else rc


def _ok(rc):
    return _ie(rc) in (0, None)


def _col(fn, *args):
    """One subsystem-array call -> list of columns; a string this PSS/E does not
       know gives None for that column instead of blanking the whole call."""
    head, strings = list(args[:-1]), args[-1]
    try:
        ie, cols = fn(*args)
        if ie in (0, None) and cols is not None:
            return cols
    except Exception:
        pass
    out, got = [], False
    for s in strings:
        try:
            ie, c = fn(*(head + [[s]]))
        except Exception:
            ie, c = 1, None
        if ie in (0, None) and c:
            out.append(c[0])
            got = True
        else:
            out.append(None)
    return out if got else []


def _v(cols, n, k, default):
    try:
        c = cols[n]
        return default if c is None else c[k]
    except Exception:
        return default


def _sid(s):
    return str(s).strip()


def _when(p):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
    except Exception:
        return "?"


# ============================================================================
# PSS/E
# ============================================================================
def _bootstrap_psse():
    """The PSSPY folder that matches this Python, plus PSSBIN -- as the study
       scripts find it."""
    prefer = "PSSPY%d%d" % sys.version_info[:2]
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        roots += [d for d in glob.glob(os.path.join(base, "PSSE3*")) if os.path.isdir(d)]

    def _add(d):
        if d and os.path.isdir(d):
            if d not in sys.path:
                sys.path.insert(0, d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    for root in roots:
        if root and os.path.isdir(os.path.join(root, prefer)):
            _add(os.path.join(root, prefer))
            _add(os.path.join(root, "PSSBIN"))
            return
    for root in roots:
        pbin = os.path.join(root or "", "PSSBIN")
        if root and (os.path.isfile(os.path.join(pbin, "psspy.pyd"))
                     or os.path.isfile(os.path.join(pbin, "psspy.py"))):
            _add(pbin)
            return


def psse_start():
    global psspy, _i, _f, _s
    if psspy is not None:
        return
    try:
        import psspy as _p
    except ImportError:
        _bootstrap_psse()
        try:
            import psse34  # noqa: F401
        except Exception:
            pass
        import psspy as _p
    psspy = _p
    try:
        import redirect
        redirect.psse2py()
    except Exception:
        pass
    psspy.psseinit(150000)
    try:
        _i = psspy.getdefaultint()
        _f = psspy.getdefaultreal()
        _s = psspy.getdefaultchar()
    except Exception:
        pass


def outputs_to(prefix):
    """Progress and alert output into <prefix>_progress.txt / _alerts.txt."""
    psspy.progress_output(2, _new(prefix + "_progress.txt"), [0, 0])
    psspy.alert_output(2, _new(prefix + "_alerts.txt"), [0, 0])


def outputs_back():
    for fn in ("progress_output", "alert_output"):
        try:
            getattr(psspy, fn)(1, "", [0, 0])
        except Exception:
            pass


def read_outputs(prefix):
    text = ""
    for sfx in ("_progress.txt", "_alerts.txt"):
        try:
            with open(prefix + sfx, encoding="utf-8", errors="replace") as fh:
                text += fh.read() + "\n"
        except Exception:
            pass
    return text


def solve():
    """FNSL with SOLVE_OPTS; True when converged."""
    for _n in range(2):
        try:
            psspy.fnsl(list(SOLVE_OPTS))
        except Exception:
            pass
        try:
            if _ie(psspy.solved()) == 0:
                return True
        except Exception:
            pass
    return False


def busv(b):
    try:
        ie, v = psspy.busdat(b, "PU")
        ie2, a = psspy.busdat(b, "ANGLED")
        if ie == 0 and ie2 == 0:
            return float(v), float(a)
    except Exception:
        pass
    return None, None


def bus_table():
    """{bus: {"type", "area", "kv", "vm", "va", "name"}}"""
    ints = _col(psspy.abusint, -1, 2, ["NUMBER", "TYPE", "AREA"])
    reals = _col(psspy.abusreal, -1, 2, ["BASE", "PU", "ANGLED"])
    names = _col(psspy.abuschar, -1, 2, ["NAME"])
    out = {}
    for k in range(len(ints[0]) if ints else 0):
        out[int(ints[0][k])] = {"type": int(_v(ints, 1, k, 1)), "area": int(_v(ints, 2, k, 0)),
                                "kv": float(_v(reals, 0, k, 0.0)), "vm": float(_v(reals, 1, k, 1.0)),
                                "va": float(_v(reals, 2, k, 0.0)), "name": _sid(_v(names, 0, k, ""))}
    return out


def machine_table():
    """{(bus, id): {"st", "p", "q", "mbase"}}"""
    mi = _col(psspy.amachint, -1, 4, ["NUMBER", "STATUS"])
    mc = _col(psspy.amachchar, -1, 4, ["ID"])
    mr = _col(psspy.amachreal, -1, 4, ["PGEN", "QGEN", "MBASE"])
    out = collections.OrderedDict()
    for k in range(len(mi[0]) if mi else 0):
        out[(int(mi[0][k]), _sid(_v(mc, 0, k, "1")))] = {
            "st": int(_v(mi, 1, k, 1)), "p": float(_v(mr, 0, k, 0.0)),
            "q": float(_v(mr, 1, k, 0.0)), "mbase": float(_v(mr, 2, k, 0.0))}
    return out


def plant_table():
    """{gen bus: {"ireg", "vs"}}"""
    gi = _col(psspy.agenbusint, -1, 4, ["NUMBER", "IREG"])
    gr = _col(psspy.agenbusreal, -1, 4, ["VSPU"])
    out = {}
    for k in range(len(gi[0]) if gi else 0):
        out[int(gi[0][k])] = {"ireg": int(_v(gi, 1, k, 0) or 0), "vs": float(_v(gr, 0, k, 1.0))}
    return out


def branch_table():
    """[(kind, a, b, c, ckt, status)] -- lines and two-winding transformers
       (c = 0), three-winding transformers (kind "3W")."""
    out = []
    bi = _col(psspy.abrnint, -1, 1, 1, 2, 1, ["FROMNUMBER", "TONUMBER", "STATUS"])
    bc = _col(psspy.abrnchar, -1, 1, 1, 2, 1, ["ID"])
    for k in range(len(bi[0]) if bi else 0):
        out.append(("LINE", int(bi[0][k]), int(bi[1][k]), 0, _sid(_v(bc, 0, k, "1")), int(_v(bi, 2, k, 1))))
    ti = _col(psspy.atrnint, -1, 1, 1, 2, 1, ["FROMNUMBER", "TONUMBER", "STATUS"])
    tc = _col(psspy.atrnchar, -1, 1, 1, 2, 1, ["ID"])
    for k in range(len(ti[0]) if ti else 0):
        out.append(("2W", int(ti[0][k]), int(ti[1][k]), 0, _sid(_v(tc, 0, k, "1")), int(_v(ti, 2, k, 1))))
    t3 = _col(psspy.atr3int, -1, 1, 1, 2, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER", "STATUS"])
    t3c = _col(psspy.atr3char, -1, 1, 1, 2, 1, ["ID"])
    for k in range(len(t3[0]) if t3 else 0):
        out.append(("3W", int(t3[0][k]), int(t3[1][k]), int(t3[2][k]), _sid(_v(t3c, 0, k, "1")),
                    int(_v(t3, 3, k, 1))))
    return out


def equipment_at(bus):
    """[(kind, id)] of the loads, fixed shunts and switched shunts on `bus`."""
    out = []
    for kind, fi, fc in (("load", "aloadint", "aloadchar"), ("fixed shunt", "afxshuntint", "afxshuntchar")):
        try:
            ni = _col(getattr(psspy, fi), -1, 4, ["NUMBER"])
            nc = _col(getattr(psspy, fc), -1, 4, ["ID"])
            for k in range(len(ni[0]) if ni else 0):
                if int(ni[0][k]) == bus:
                    out.append((kind, _sid(_v(nc, 0, k, "1"))))
        except Exception:
            pass
    try:
        ws = _col(psspy.aswshint, -1, 4, ["NUMBER"])
        for k in range(len(ws[0]) if ws else 0):
            if int(ws[0][k]) == bus:
                out.append(("switched shunt", ""))
    except Exception:
        pass
    return out


def purge_at(bus, kind, eid):
    """Remove one load / fixed shunt / switched shunt from `bus`; True if gone."""
    names = {"load": ("purgload",), "fixed shunt": ("purgshnt", "purgshunt"),
             "switched shunt": ("purgsws",)}[kind]
    for fn in names:
        f = getattr(psspy, fn, None)
        if f is None:
            continue
        for args in ((bus,), (bus, eid)) if kind == "switched shunt" else ((bus, eid),):
            try:
                if _ok(f(*args)):
                    return True
            except Exception:
                continue
    return False


def branch_flow(a, b, ckt):
    """MW + jMVAr leaving bus a on the branch a-b ckt, or None."""
    try:
        ie, s = psspy.brnflo(a, b, ckt)
        if ie == 0 and s is not None:
            return complex(s)
    except Exception:
        pass
    return None


def set_bus_type(bus, ide, vm=None, va=None):
    """True when `bus` is type `ide` afterwards."""
    vm = _f if vm is None else vm
    va = _f if va is None else va
    for fn, args in (("bus_chng_4", (bus, 0, [ide, _i, _i, _i], [_f, vm, va, _f, _f, _f, _f], _s)),
                     ("bus_chng_3", (bus, [ide, _i, _i, _i], [_f, vm, va, _f, _f, _f, _f], _s))):
        f = getattr(psspy, fn, None)
        if f is None:
            continue
        try:
            if _ok(f(*args)):
                break
        except Exception:
            continue
    return bus_table().get(bus, {}).get("type") == ide


# ============================================================================
# NETWORK -- which buses are the plant (pure Python)
# ============================================================================
def adjacency(branches, buses):
    """{bus: set(neighbours)} over in-service branches between in-service buses."""
    adj = collections.defaultdict(set)

    def link(a, b):
        if a != b and a in buses and b in buses and buses[a]["type"] != 4 and buses[b]["type"] != 4:
            adj[a].add(b)
            adj[b].add(a)
    for kind, a, b, c, _ck, st in branches:
        if kind == "3W":
            # status: 1 all in, 2 winding 2 out, 3 winding 3 out, 4 winding 1 out
            pairs = {1: ((a, b), (b, c), (a, c)), 2: ((a, c),), 3: ((a, b),), 4: ((b, c),)}.get(st, ())
            for x, y in pairs:
                link(x, y)
        elif st == 1:
            link(a, b)
    return adj


def parts_behind(adj, poi, cap):
    """[(first bus, set of buses -- None when larger than cap)], one per part of
       the network reached from a POI neighbour without passing the POI."""
    out, done = [], set()
    for n in sorted(adj.get(poi, ())):
        if n in done:
            continue
        seen, todo, big = set([n]), [n], False
        while todo and not big:
            u = todo.pop()
            for w in adj.get(u, ()):
                if w == poi or w in seen:
                    continue
                seen.add(w)
                todo.append(w)
                if len(seen) > cap:
                    big = True
                    break
        done |= seen
        out.append((n, None if big else seen))
    return out


def read_newplant(path):
    """{role: [bus]} from the study's <case>_newplant_buses.txt, or {}."""
    out = collections.defaultdict(list)
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#"):
                    continue
                bits = ln.split()
                if len(bits) == 1:
                    out["MACH"].append(int(bits[0]))
                else:
                    out[bits[0].upper()].append(int(bits[1]))
    except Exception:
        return {}
    return dict(out)


# ============================================================================
# CASE FILES OF A PROJECT
# ============================================================================
_VARIANT = re.compile(r"^(s\d+|egfoff|sgfoff|cap\d*|poi\d*|d\d+|projoff|cbank|dyr|ppc|lvl\d*|off)$", re.I)


def _tags_ok(tags):
    bits = [t for t in tags.split("_") if t]
    return not any(_VARIANT.match(t) or "egfoff" in t.lower() or "sgfoff" in t.lower() for t in bits)


def find_case(proj, log):
    """(path, MW, tags) of the project's as-built NEWPLANT case, or (None, 0, "")."""
    if proj in CASE_BY_PROJECT:
        p = _abs(CASE_BY_PROJECT[proj])
        m = re.search(r"_(\d+)MW(.*)_NEWPLANT\.sav$", os.path.basename(p), re.I)
        log("case pinned by CASE_BY_PROJECT")
        return p, (int(m.group(1)) if m else 0), (m.group(2) if m else "")
    d = _case_dir()
    rx = re.compile(r"_BESS_%s_(\d+)MW(.*)_NEWPLANT\.sav$" % re.escape(proj), re.I)
    cands, left = [], []
    for p in sorted(glob.glob(os.path.join(d, "*.sav"))):
        m = rx.search(os.path.basename(p))
        if not m:
            continue
        if not _tags_ok(m.group(2)):
            left.append(p)
            continue
        cands.append((int(m.group(1)), os.path.getmtime(p), p, m.group(2)))
    if left:
        log("scenario cases not used: %s" % ", ".join(os.path.basename(x) for x in left))
    if not cands:
        log("*** no *_BESS_%s_<MW>MW*_NEWPLANT.sav in %s -- set CASE_DIR or CASE_BY_PROJECT" % (proj, d))
        return None, 0, ""
    sizes = sorted(set(c[0] for c in cands))
    want = MW_BY_PROJECT.get(proj)
    if want:
        cands = [c for c in cands if c[0] == int(want)]
        if not cands:
            log("*** no %d MW case for %s (sizes found: %s)" % (int(want), proj, sizes))
            return None, 0, ""
    elif len(sizes) > 1:
        log("NOTE: %s cases at %s MW -- the %d MW one is used (MW_BY_PROJECT picks another)"
            % (proj, " and ".join(str(s) for s in sizes), sizes[0]))
        cands = [c for c in cands if c[0] == sizes[0]]
    cands.sort(key=lambda c: -c[1])
    if len(cands) > 1:
        log("NOTE: %d as-built cases; the newest is used, not: %s"
            % (len(cands), ", ".join(os.path.basename(c[2]) for c in cands[1:])))
    return cands[0][2], cands[0][0], cands[0][3]


def find_deck(proj, tags):
    """The combined deck the build wrote for this case, or None."""
    if proj in DECK_BY_PROJECT:
        return _abs(DECK_BY_PROJECT[proj])
    d = _case_dir()
    exact = sorted(glob.glob(os.path.join(d, "*_with_BESS_%s%s.dyr" % (proj, tags))), key=os.path.getmtime)
    if exact:
        return exact[-1]
    rx = re.compile(r"_with_BESS_%s(.*)\.dyr$" % re.escape(proj), re.I)
    c = []
    for p in glob.glob(os.path.join(d, "*_with_BESS_%s*.dyr" % proj)):
        m = rx.search(os.path.basename(p))
        if m and _tags_ok(m.group(1)):
            c.append(p)
    return max(c, key=os.path.getmtime) if c else None


# ============================================================================
# USER-MODEL LIBRARIES
# ============================================================================
_LOADED = set()


def _key(p):
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def idv_dlls(path):
    """(DLL paths, does it hold other commands?) of an add-library response file."""
    dlls, other = [], False
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except Exception:
        return [], False
    base = os.path.dirname(path)
    for ln in text.splitlines():
        t = ln.strip()
        if not t or t.startswith("@!") or t.startswith("!") or t.startswith("/"):
            continue
        m = re.search(r"ADDMODELLIBRARY\s*[, ]\s*['\"]?([^'\";]+?)['\"]?\s*;?\s*$", t, re.I)
        if m:
            dlls.append(os.path.normpath(_abs(m.group(1).strip(), base)))
        elif not re.match(r"^(BAT_)?(END|ECHO)\b", t, re.I):
            other = True
    return dlls, other


def bess_dlls():
    """The study's BESS model library: BESS_MODEL_DLLS, else the DLL(s) in the
       case folder holding REGCAU1 / REECAU1 / REPCAU1 (the study's own search)."""
    d = _case_dir()
    named = _file_setting(ENGINE_FILES, "BESS_MODEL_DLLS", []) or []
    if named:
        return [x if _is_abs(x) else os.path.join(d, x) for x in named]
    hits = []
    for p in sorted(glob.glob(os.path.join(d, "*.dll"))):
        if os.path.basename(p).lower().startswith("dsusr"):
            continue
        try:
            with open(p, "rb") as fh:
                blob = fh.read()
        except Exception:
            continue
        if any(n in blob for n in (b"REGCAU1", b"REECAU1", b"REPCAU1")):
            hits.append(p)
    return hits


def addlib_path():
    return _abs(ADDLIB_IDV or _file_setting(ENGINE_FILES, "ADDLIB_IDV", "add_library.idv"), _case_dir())


def load_libraries(log, whole_idv):
    """The study's user-model DLLs, each loaded once. whole_idv: run the
       add-library file as the study does at initialisation (from its own
       folder) when it holds more than library loads; otherwise only the DLLs it
       names are loaded. dsusr.dll is never loaded -- it is built for the full
       deck's CONEC / CONET, not for a plant."""
    p = addlib_path()
    dlls, other = idv_dlls(p) if os.path.isfile(p) else ([], False)
    if not os.path.isfile(p):
        log("  no %s -- user-model DLLs from the case-folder search only" % p)
    elif whole_idv and other and _key(p) not in _LOADED:
        cwd = os.getcwd()
        try:
            os.chdir(os.path.dirname(p))
            ie = _ie(psspy.runrspnsfile(p))
        finally:
            os.chdir(cwd)
        _LOADED.add(_key(p))
        _LOADED.update(_key(x) for x in dlls)
        log("  %s run as the study runs it (ierr %s): it holds more than library loads" % (p, ie))
    for dll in list(dlls) + bess_dlls() + [_abs(x) for x in DLLS]:
        if _key(dll) in _LOADED or os.path.basename(dll).lower().startswith("dsusr"):
            continue
        _LOADED.add(_key(dll))
        if not os.path.isfile(dll):
            log("  *** user-model DLL not found: %s" % dll)
            continue
        try:
            log("  DLL %s (ierr %s)" % (dll, _ie(psspy.addmodellibrary(dll))))
        except Exception as e:
            log("  *** DLL %s: %s" % (dll, e))


# ============================================================================
# DYR -- records, the plant's records, the deck's text where PSS/E holds it
# ============================================================================
_TOK = re.compile(r"'[^']*'|\"[^\"]*\"|[^,\s]+")
_INT = re.compile(r"^[+-]?\d+$")
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")


def _end(line):
    """Index of the first '/' outside quotes, or -1."""
    q = None
    for k, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        elif ch == "/":
            return k
    return -1


def dyr_records(text):
    """[raw record text], as PSS/E reads a .dyr: a '/' outside quotes ends the
       record (also at the start of a line); a '/' line with no data pending is
       a comment; '@!' lines are labels, kept with the record they sit in."""
    out, cur, data = [], [], False
    for raw in text.replace("\r", "").split("\n"):
        st = raw.strip()
        if not st:
            continue
        if st.startswith("@!"):
            cur.append(raw.rstrip())
            continue
        k = _end(raw)
        part = raw[:k] if k >= 0 else raw
        if part.strip():
            data = True
        if data:
            cur.append(raw.rstrip())
        if k >= 0 and data:
            out.append("\n".join(cur))
            cur, data = [], False
    if data:
        out.append("\n".join(cur) + " /")       # unterminated at the end of the file
    return out


def _data(rec):
    """The record's values as one line: labels and comments left out."""
    keep = []
    for ln in rec.split("\n"):
        st = ln.strip()
        if not st or st.startswith("@!"):
            continue
        k = _end(ln)
        keep.append(ln[:k] if k >= 0 else ln)
    return " ".join(keep)


def _num(tok):
    t = tok.strip().strip("'\"").strip()
    if _NUM.match(t):
        try:
            return float(t.replace("D", "E").replace("d", "e"))
        except ValueError:
            pass
    return t.upper()


def _half_step(tok):
    """Half the last printed digit of a number token -- the rounding it holds."""
    t = tok.strip().strip("'\"").strip()
    m = re.match(r"^[+-]?(\d*)(?:\.(\d*))?(?:[eEdD]([+-]?\d+))?$", t)
    if not m:
        return 0.0
    return 0.5 * 10.0 ** (int(m.group(3) or 0) - len(m.group(2) or ""))


Rec = collections.namedtuple("Rec", "text model first first_bus refs vals toks anchor kind")

# Records numbered on their own (the first field is an instance number, not a
# bus): the relay family IREL 'VTGTPAT' IB JB ID ... and user "other" models.
# A first field above 999997 cannot be a PSS/E bus number either.
_RELAY = re.compile(r"^(VTG|FRQ)(TP|DC)A", re.I)

# where a user model's real name sits: IBUS 'USRMDL' ID 'NAME' IC IT NI NC NS NV ...
_USR_NAME_AT = {"USRMDL": 3, "USRLOD": 3, "USRMSC": 2, "USRBRN": 4, "USRAUX": 3, "USRDCL": 2, "USRFCT": 2}


def parse_rec(text, buses, floor):
    """Rec, or None when the record does not start with a number. `floor`: a
       whole number below it is a flag or an index, never a bus."""
    toks = _TOK.findall(_data(text))
    if len(toks) < 2 or not _INT.match(toks[0]):
        return None
    model = toks[1].strip("'\"").strip().upper()
    head = 2
    if model in _USR_NAME_AT and len(toks) > _USR_NAME_AT[model]:
        head = _USR_NAME_AT[model] + 7                 # the name, then IC IT NI NC NS NV
        model = toks[_USR_NAME_AT[model]].strip("'\"").strip().upper()
    first = int(toks[0])
    raw_model = toks[1].strip("'\"").strip().upper()
    if raw_model == "USRMSC" or _RELAY.match(raw_model) or first > 999997:
        kind = "relay" if _RELAY.match(raw_model) else "own"        # numbered on its own
    else:
        kind = "bus" if first in buses else "nobus"                 # nobus: its bus is not in this case
    first_bus = kind == "bus"
    refs = [abs(int(t)) for t in toks[head:] if _INT.match(t) and abs(int(t)) >= floor and abs(int(t)) in buses]
    anchor = first if first_bus else (refs[0] if refs else None)
    vals = (toks[:1] + toks[2:]) if first_bus else toks[2:]
    return Rec(text, model, first, first_bus, refs, [_num(t) for t in vals], vals, anchor, kind)


def _rid(tok):
    return tok.strip().strip("'\"").strip().upper()


def plant_records(text, keep, poi, buses, from_dyda, ids_at=None):
    """(the plant's [Rec], notes). A record is the plant's when its first field
       is a plant bus, or -- a relay or controller numbered on its own -- when
       it names a plant bus. A record that also names a bus outside the plant,
       or a relay whose machine (JB, ID) the plant does not have, cannot work in
       the model and is left out. From a dyda dump every record is one PSS/E
       accepted, so only that test applies. From deck text (no snapshot) a
       record at a plant bus whose id names no machine or load there (ids_at)
       is left out too: PSS/E would refuse it."""
    allb = set(keep) | set([poi])
    floor = min([1000] + list(allb))
    kept, notes, unread = [], [], 0
    for raw in dyr_records(text):
        r = parse_rec(raw, buses, floor)
        if r is None:
            unread += 1
            continue
        if r.kind == "nobus":
            if set(r.refs) & set(keep):
                notes.append("LEFT OUT: %s at %d -- bus %d is not in this case, though the record names plant "
                             "bus(es) %s" % (r.model, r.first, r.first, sorted(set(r.refs) & set(keep))[:6]))
            continue
        if r.first_bus:
            if r.first not in keep:
                continue
        elif not (set(r.refs) & set(keep)):
            continue
        if r.kind == "relay" and ids_at is not None:
            toks = _TOK.findall(_data(r.text))
            jb = abs(int(toks[3])) if len(toks) > 4 and _INT.match(toks[3]) else None
            if jb is None or _rid(toks[4]) not in ids_at.get(jb, ()):
                notes.append("LEFT OUT: %s %d trips machine %s '%s', which the plant does not have"
                             % (r.model, r.first, toks[3] if len(toks) > 3 else "?",
                                _rid(toks[4]) if len(toks) > 4 else "?"))
                continue
        if r.first_bus and not from_dyda and ids_at is not None:
            toks = _TOK.findall(_data(r.text))
            idt = toks[2] if len(toks) > 2 else ""
            branch = _INT.match(idt) and abs(int(idt)) in buses        # IBUS 'MODEL' JBUS ID: a branch model
            if not branch and _rid(idt) != "*" and _rid(idt) not in ids_at.get(r.first, ()):
                notes.append("LEFT OUT: %s at %d id '%s' -- no machine or load with that id there"
                             % (r.model, r.first, _rid(idt)))
                continue
        outside = sorted(set(b for b in r.refs if b not in allb))
        if outside:
            if from_dyda:
                notes.append("LEFT OUT: %s at %d names bus(es) outside the plant %s" % (r.model, r.first, outside[:6]))
                continue
            notes.append("CHECK: %s at %d names bus(es) outside the plant %s" % (r.model, r.first, outside[:6]))
        kept.append(r)
    if unread:
        notes.append("%d record(s) of the full set are keyed by a name, not a number (FACTS devices, dc lines, "
                     "or text the deck garbles) -- not plant models" % unread)
    return kept, notes


def _close(x, y, tx, ty):
    return abs(x - y) <= max(_half_step(tx), _half_step(ty)) * 1.0001 + 2e-7 * max(abs(x), abs(y))


def _same(a, b):
    """The two records hold the same values, to the digits each one prints."""
    if len(a.vals) != len(b.vals):
        return False
    for x, y, tx, ty in zip(a.vals, b.vals, a.toks, b.toks):
        if isinstance(x, float) and isinstance(y, float):
            if not _close(x, y, tx, ty):
                return False
        elif x != y:
            return False
    return True


def _diff_text(a, b, n=4):
    out = []
    for k, (x, y) in enumerate(zip(a.vals, b.vals)):
        same = (_close(x, y, a.toks[k], b.toks[k]) if isinstance(x, float) and isinstance(y, float) else x == y)
        if not same:
            out.append("value %d: %s in the deck, %s as run" % (k + 1, b.toks[k], a.toks[k]))
        if len(out) >= n:
            break
    return "; ".join(out) if out else "%d values in the deck, %d as run" % (len(b.vals), len(a.vals))


def _n_diff(a, b):
    return sum(1 for k, (x, y) in enumerate(zip(a.vals, b.vals))
               if not (_close(x, y, a.toks[k], b.toks[k]) if isinstance(x, float) and isinstance(y, float)
                       else x == y))


def merge_records(as_run, deck):
    """([record text] in the deck's order, notes, how many keep the deck's
       text): every record PSS/E holds; the deck's own text where PSS/E holds
       the same values, PSS/E's where they differ. Exact matches are paired
       first, so one changed record among many alike (a relay set on one bus)
       is never paired with the wrong one."""
    pool = collections.defaultdict(list)
    for k, r in enumerate(deck):
        pool[(r.model, r.anchor)].append([k, r, False])
    placed, notes, n_deck, left = [], [], 0, []
    for r in as_run:
        hit = next((c for c in pool.get((r.model, r.anchor), []) if not c[2] and _same(r, c[1])), None)
        if hit is None:
            left.append(r)
            continue
        hit[2] = True
        placed.append((hit[0], 0, hit[1].text))
        n_deck += 1
    for r in left:
        cands = pool.get((r.model, r.anchor), [])
        near = [c for c in cands if not c[2] and len(c[1].vals) == len(r.vals)]
        if near:
            c = min(near, key=lambda c: _n_diff(r, c[1]))
            c[2] = True
            notes.append("as run differs from the deck: %s at %s -- %s" % (r.model, r.anchor, _diff_text(r, c[1])))
            placed.append((c[0], 1, r.text))
            continue
        notes.append("as run, not in the deck: %s at %s" % (r.model, r.anchor))
        at = [c[0] for c in cands] or [c[0] for (m, a), lst in pool.items() if a == r.anchor for c in lst]
        placed.append((max(at) if at else len(deck), 2, r.text))
    for lst in pool.values():
        for k, r, used in lst:
            if not used:
                notes.append("in the deck, not in the case as run (removed by the study's decks, or not "
                             "accepted by PSS/E): %s at %s" % (r.model, r.anchor))
    placed.sort(key=lambda x: (x[0], x[1]))
    return [t for _k, _o, t in placed], notes, n_deck


# ============================================================================
# ONE-LINE DIAGRAM -- a tidy tree hanging from the POI
# ============================================================================
def tree_layout(adj, poi, keep, unit_buses, dx, dy, tri=()):
    """(positions {bus: (x, y)}, parent {bus: bus}, children {bus: [bus]}, order).

       The plant as a tree hanging from the POI: the POI at (0, 0), every bus
       one row (dy) below the bus that feeds it (first reached from the POI),
       its children side by side under it. Each leaf takes its own column (dx);
       a bus sits centred over its children, so every connection runs straight
       down. Children are ordered by the lowest unit number below them: the EGF
       feeders (their own numbers) come left of the SGF ones (999001..). The
       other windings of a three-winding transformer (tri: its three buses) stay
       side by side under the winding that feeds them, so PSS/E draws the
       transformer in one place."""
    nodes = set(keep) | set([poi])
    parent, depth, order, front = {poi: None}, {poi: 0}, [poi], [poi]
    while front:
        nxt = []
        for u in front:
            for w in sorted(adj.get(u, ())):
                if w in nodes and w not in depth:
                    depth[w] = depth[u] + 1
                    parent[w] = u
                    order.append(w)
                    nxt.append(w)
        front = nxt
    kids = collections.defaultdict(list)
    for b in order[1:]:
        kids[parent[b]].append(b)
    low = {}
    for b in reversed(order):                       # deepest first: the lowest unit number below b
        cand = [b] if b in unit_buses else []
        cand += [low[c] for c in kids[b] if low[c] is not None]
        low[b] = min(cand) if cand else None
    big = 10 ** 9

    def lo(c):
        return low[c] if low[c] is not None else big
    for b in list(kids):
        groups = collections.OrderedDict()
        for c in kids[b]:
            t = next((k for k, w in enumerate(tri) if b in w and c in w), None)
            groups.setdefault(("t", t) if t is not None else ("b", c), []).append(c)
        ordered = []
        for _g, members in sorted(groups.items(), key=lambda gm: (min(lo(m) for m in gm[1]), min(gm[1]))):
            ordered += sorted(members, key=lambda m: (lo(m), m))
        kids[b] = ordered
    col, slot = {}, [0]

    def place(b):
        if not kids[b]:
            col[b] = float(slot[0])
            slot[0] += 1
            return
        for c in kids[b]:
            place(c)
        col[b] = (col[kids[b][0]] + col[kids[b][-1]]) / 2.0
    place(poi)
    x0 = col[poi]
    pos = dict((b, (round((col[b] - x0) * dx, 4), round(-depth[b] * dy, 4))) for b in order)
    return pos, parent, kids, order


def layout_svg(path, pos, kids, kind_of, info, units, poi, title, dx, dy, tri=()):
    """The intended layout as an SVG drawing (bus bars, straight connections,
       transformer and machine symbols, labels) -- to compare with the .sld.
       Works either way up: symbols are drawn away from the bus that feeds them."""
    px = 64.0                                       # pixels per diagram inch
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    left, right = min(xs) - 0.9 * dx, max(xs) + 1.9 * dx
    top, bottom = max(ys) + 1.3 * dy, min(ys) - 1.3 * dy

    def X(x):
        return (x - left) * px

    def Y(y):
        return (top - y) * px

    w, h = (right - left) * px, (top - bottom) * px
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%.0f" height="%.0f" viewBox="0 0 %.0f %.0f" '
           'font-family="Arial" font-size="10">' % (w, h, w, h),
           '<rect width="100%" height="100%" fill="white"/>',
           '<text x="12" y="20" font-size="13" font-weight="bold">%s</text>' % _esc(title)]

    def pline(x1, y1, x2, y2):                      # pixel coordinates
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="#555" stroke-width="1.4"/>'
                   % (x1, y1, x2, y2))

    def circ(cx, cy, r, stroke="#333", fill="none", sw=1.2):
        out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" stroke-width="%.1f"/>'
                   % (cx, cy, r, fill, stroke, sw))
    par = {}
    for b, cs in kids.items():
        for c in cs:
            par[c] = b
    half = {}
    for b, (x, y) in pos.items():
        span = [pos[c][0] for c in kids.get(b, [])]
        half[b] = max(0.32 * dx, (max(span) - min(span)) / 2.0 + 0.18 * dx if span else 0.0)
    r = 0.11 * px
    for b, cs in kids.items():                      # three-winding transformers: one symbol each
        for wd in tri:
            if b not in wd:
                continue
            ms = sorted((c for c in cs if c in wd), key=lambda c: pos[c][0])
            if not ms:
                continue
            xm = X(sum(pos[c][0] for c in ms) / float(len(ms)))
            pb, pc = Y(pos[b][1]), Y(pos[ms[0]][1])
            d = 1.0 if pc > pb else -1.0           # +1: children drawn below their feeder
            ps, pj = pb + (pc - pb) * 0.32, pc - (pc - pb) * 0.14
            pline(xm, pb, xm, ps - d * r * 1.5)
            for ox, oy in ((0, -0.75), (-0.65, 0.45), (0.65, 0.45)):
                circ(xm + ox * r, ps + d * oy * r, r)
            pline(xm, ps + d * r * 1.2, xm, pj)
            pline(X(pos[ms[0]][0]), pj, X(pos[ms[-1]][0]), pj)
            for c in ms:
                pline(X(pos[c][0]), pj, X(pos[c][0]), Y(pos[c][1]))
    for b, cs in kids.items():                      # other connections, straight down
        for c in cs:
            if any(b in wd and c in wd for wd in tri):
                continue
            x, p1, p2 = X(pos[c][0]), Y(pos[b][1]), Y(pos[c][1])
            pline(x, p1, x, p2)
            if kind_of.get(frozenset((b, c)), "LINE") in ("2W", "3W"):
                pm = (p1 + p2) / 2.0
                circ(x, pm - r * 0.7, r)
                circ(x, pm + r * 0.7, r)
    for b, (x, y) in pos.items():                   # bus bars and labels on top
        kv = info.get(b, ("", 0.0))[1]
        colr = "#1F3864" if kv >= 100 else ("#2E75B6" if kv >= 10 else "#7F7F7F")
        out.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="4"/>'
                   % (X(x - half[b]), Y(y), X(x + half[b]), Y(y), colr))
        out.append('<text x="%.1f" y="%.1f">%d %s</text>' % (X(x + half[b]) + 4, Y(y) - 3, b,
                                                             _esc(info.get(b, ("", 0))[0])))
        out.append('<text x="%.1f" y="%.1f" fill="#555">%.1f kV</text>' % (X(x + half[b]) + 4, Y(y) + 10, kv))

    def away(b):                                    # +1: draw below the bus, -1: above
        if b in par:
            return 1.0 if Y(pos[b][1]) > Y(pos[par[b]][1]) else -1.0
        cs = kids.get(b, [])
        return -1.0 if cs and Y(pos[cs[0]][1]) > Y(pos[b][1]) else 1.0
    for b, mid in units:                            # machines on the free side of their bus
        if b not in pos:
            continue
        d, x, y = away(b), X(pos[b][0]), Y(pos[b][1])
        pline(x, y, x, y + d * 0.32 * dy * px)
        cy = y + d * (0.32 * dy * px + 0.15 * px)
        circ(x, cy, 0.15 * px, stroke="#C00000", fill="white", sw=1.5)
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" fill="#C00000">%s</text>' % (x, cy + 3.5, _esc(mid)))
    d, x, y = away(poi), X(pos[poi][0]), Y(pos[poi][1])  # the infinite bus on the free side of the POI
    pline(x, y, x, y + d * 0.45 * dy * px)
    cy = y + d * (0.45 * dy * px + 0.17 * px)
    circ(x, cy, 0.17 * px, stroke="#1F3864", fill="white", sw=1.8)
    out.append('<text x="%.1f" y="%.1f" font-weight="bold" fill="#1F3864">%s -- infinite bus</text>'
               % (x + 0.25 * px, cy + 3.5, _esc(IB_ID)))
    out.append("</svg>")
    with open(_new(path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_sld(sld_path, pos, order, work, log):
    """<name>.sld drawn by PSS/E: a new diagram, every bus placed with growbus
       at its layout position, saved. The help text of the diagram calls this
       PSS/E has is kept in the work folder. True when the file was written."""
    names = ("newdiagfile", "growbus", "growbuslevels", "savediagfile", "closediagfile", "opendiagfile")
    try:
        with open(_new(os.path.join(work, "diagram_api_help.txt")), "w", encoding="utf-8") as fh:
            for n in names:
                f = getattr(psspy, n, None)
                fh.write("==== psspy.%s: %s\n%s\n\n" % (n, "present" if f else "NOT in this PSS/E",
                                                         (getattr(f, "__doc__", "") or "") if f else ""))
    except Exception:
        pass
    if not all(getattr(psspy, n, None) for n in ("newdiagfile", "growbus", "savediagfile")):
        log("REVIEW: slider diagram not written -- this PSS/E has no newdiagfile / growbus / savediagfile")
        return False
    bad, ok = [], False
    try:
        ie = _ie(psspy.newdiagfile())
        if ie not in (0, None):
            log("REVIEW: slider diagram not written -- newdiagfile ierr %s" % ie)
            return False
        for b in order:
            x, y = pos[b]
            ie = _ie(psspy.growbus(b, x, y))
            if ie not in (0, None):
                bad.append("%d (ierr %s)" % (b, ie))
        ie = _ie(psspy.savediagfile(_new(sld_path)))
        ok = ie in (0, None) and os.path.isfile(sld_path) and os.path.getsize(sld_path) > 0
        if not ok:
            log("REVIEW: slider diagram not written -- savediagfile ierr %s" % ie)
    except Exception as e:
        log("REVIEW: slider diagram not written -- %s" % e)
    finally:
        try:
            psspy.closediagfile()
        except Exception:
            pass
    if bad:
        log("REVIEW: slider diagram: %d bus(es) not placed: %s" % (len(bad), ", ".join(bad[:10])))
    return ok


# ============================================================================
# ONE PROJECT
# ============================================================================
def one_project(proj, cfg, out_root, log):
    """Pass 1 -- the power flow: the plant cut out of the project case and put
       on an infinite bus, .sav and .raw written. Returns what pass 2 needs, or
       None. Every plant is cut before any dynamics are loaded, so no network is
       edited in a session that has run a simulation."""
    name, poi = cfg["name"], int(cfg["poi"])
    egf = [int(x) for x in cfg["egf"]]
    pkg = os.path.join(out_root, name)
    work = os.path.join(out_root, "_work", name)
    os.makedirs(work)
    log("%s  --  %s  (POI %d)" % (name, proj, poi))
    log("=" * 78)
    sav, mw, tags = find_case(proj, log)
    if not sav or not os.path.isfile(sav):
        log("*** no project case -- nothing written")
        return False
    stem = os.path.splitext(sav)[0]
    log("project case   : %s  (%s)" % (sav, _when(sav)))

    # ---- 1. the full case, solved -----------------------------------------------
    if not _ok(psspy.case(sav)):
        log("*** case() could not open it")
        return False
    if not solve():
        log("*** the full case does not solve with SOLVE_OPTS -- nothing written")
        return False
    buses = bus_table()
    if poi not in buses:
        log("*** POI %d is not in the case" % poi)
        return False
    v_poi, a_poi = busv(poi)
    if v_poi is None:
        log("*** no voltage for POI %d" % poi)
        return False
    mach_full = machine_table()
    plant_full = plant_table()
    loads_full = []
    try:
        li = _col(psspy.aloadint, -1, 4, ["NUMBER"])
        lc = _col(psspy.aloadchar, -1, 4, ["ID"])
        loads_full = [(int(li[0][k]), _sid(_v(lc, 0, k, "1"))) for k in range(len(li[0]) if li else 0)]
    except Exception:
        pass
    branches = branch_table()
    adj = adjacency(branches, buses)

    # ---- 2. the plant -------------------------------------------------------------
    rec = read_newplant(stem + "_newplant_buses.txt")
    sgf_rec = set(rec.get("MACH", []))
    np_lo = int((_file_setting(MAIN_FILES, "NEW_PLANT", {}) or {}).get("bus_start", 999001) or 999001)
    mach_buses = set(b for b, _m in mach_full)

    def is_sgf(b):
        return b in sgf_rec if sgf_rec else (np_lo <= b <= np_lo + 998 and b in mach_buses)
    keep, other_parts = set(), []
    for first, part in parts_behind(adj, poi, MAX_PLANT_BUSES):
        if part is None:
            continue
        if (part & set(egf)) or any(is_sgf(b) for b in part):
            keep |= part
        else:
            other_parts.append((first, part))
    missing = [b for b in egf if b not in keep]
    if missing:
        log("*** EGF bus(es) %s are not behind POI %d in this case -- check PLANTS" % (missing, poi))
        return False
    units = [(k, m) for k, m in mach_full.items() if k[0] in keep]
    egf_u = [(k, m) for k, m in units if k[0] in egf]
    sgf_u = [(k, m) for k, m in units if is_sgf(k[0])]
    oth_u = [(k, m) for k, m in units if k[0] not in egf and not is_sgf(k[0])]
    if not sgf_u:
        log("*** no SGF unit behind POI %d -- not a case with the new plant built" % poi)
        return False
    log("new-plant record: %s" % (("%s_newplant_buses.txt, %d unit bus(es)" % (os.path.basename(stem), len(sgf_rec)))
                                  if sgf_rec else "none -- SGF = the units on buses %d..%d" % (np_lo, np_lo + 998)))
    log("POI            : %d %s, %.1f kV -- %.5f pu, %.3f deg in the full case"
        % (poi, buses[poi]["name"], buses[poi]["kv"], v_poi, a_poi))
    log("plant          : %d buses behind the POI" % len(keep))

    def ulist(lst):
        return "; ".join("%d '%s' %s P %.1f Q %.1f MBASE %.1f" % (k[0], k[1], "in" if m["st"] == 1 else "OUT",
                                                                    m["p"], m["q"], m["mbase"]) for k, m in lst)
    log("EGF units      : %s" % ulist(egf_u))
    log("SGF units      : %s" % ulist(sgf_u))
    if oth_u:
        log("other units    : %s (in the plant's part of the network, kept)" % ulist(oth_u))
    for k, m in egf_u + sgf_u:
        if m["st"] != 1:
            log("WARNING: unit %d '%s' is OUT of service in the study case -- in the model, out of service" % k)
    for first, part in other_parts:
        log("left with the grid: %d bus(es) behind the POI via %d, without an EGF or SGF unit: %s"
            % (len(part), first, sorted(part)[:12]))
    p_units = sum(m["p"] for k, m in units if m["st"] == 1)
    q_units = sum(m["q"] for k, m in units if m["st"] == 1)
    log("units total    : %.1f MW, %.1f MVAr (full case)" % (p_units, q_units))
    ties = [(a if b == poi else b, ck) for kind, a, b, c, ck, st in branches
            if kind != "3W" and st == 1 and ((a == poi and b in keep) or (b == poi and a in keep))]
    tie3 = [x for x in branches if x[0] == "3W" and poi in x[1:4] and set(x[1:4]) & keep]

    def poi_inj():
        s = 0j
        for b, ck in ties:
            f = branch_flow(poi, b, ck)
            if f is None:
                return None
            s -= f
        return s
    inj_full = poi_inj()
    if inj_full is not None:
        log("into the POI   : %.1f MW, %.1f MVAr from the plant (full case, %d tie(s)%s)"
            % (inj_full.real, inj_full.imag, len(ties),
               "; a three-winding winding on the POI is not in this sum" if tie3 else ""))
    v_full = dict((b, buses[b]["vm"]) for b in keep)

    # ---- 3. everything else out ---------------------------------------------------
    allb = set(keep) | set([poi])
    drop = sorted(b for b in buses if b not in allb)
    for k in range(0, len(drop), _BATCH):
        part = drop[k:k + _BATCH]
        if not _ok(psspy.bsys(1, 0, [0.0, 0.0], 0, [], len(part), part, 0, [], 0, [])):
            log("*** bsys refused the bus list")
            return False
        ie = _ie(psspy.extr(1, 0, [0, 0]))
        if ie not in (0, None):
            log("*** extr ierr %s" % ie)
            return False
    cut = bus_table()
    if set(cut) != allb:
        log("*** after the purge the case holds %d buses, the plant and POI %d" % (len(cut), len(allb)))
        return False
    for b in sorted(keep):
        if cut[b]["type"] == 3:
            log("plant bus %d was a swing bus: now type 2 (%s)" % (b, "ok" if set_bus_type(b, 2) else "FAILED"))
    for k in [k for k in machine_table() if k[0] == poi]:
        log("machine %d '%s' on the POI bus removed (grid side): ierr %s" % (k[0], k[1], _ie(psspy.purgmac(poi, k[1]))))
    for kind, eid in equipment_at(poi):
        log("%s '%s' on the POI bus %s" % (kind, eid, "removed (grid side)" if purge_at(poi, kind, eid)
                                           else "could NOT be removed -- it changes only the IB's output"))

    # ---- 4. the infinite bus --------------------------------------------------------
    p0 = -(inj_full.real if inj_full is not None else p_units)
    q0 = -(inj_full.imag if inj_full is not None else q_units)
    ie = _ie(psspy.plant_data(poi, 0, [v_poi, _f]))
    if ie not in (0, None):
        log("*** plant_data at the POI: ierr %s" % ie)
        return False
    if not _ok(psspy.machine_data_2(poi, IB_ID, [1, _i, _i, _i, _i, 0],
                                    [p0, q0, 9999.0, -9999.0, 9999.0, -9999.0, IB_MBASE, 0.0, IB_XSOURCE,
                                     0.0, 0.0, 1.0, _f, _f, _f, _f, 1.0])):
        log("*** the infinite-bus machine could not be added")
        return False
    if not set_bus_type(poi, 3, v_poi, a_poi):
        log("*** the POI could not be made the swing bus")
        return False
    log("infinite bus   : POI %d is the swing bus; machine '%s', MBASE %.0f MVA, X source %.4g pu on it, "
        "held at %.5f pu, %.3f deg" % (poi, IB_ID, IB_MBASE, IB_XSOURCE, v_poi, a_poi))

    # ---- 5. remote regulation that left with the grid --------------------------------
    for b in sorted(set(k[0] for k, m in units)):
        p = plant_full.get(b)
        if not p or p["ireg"] in (0, b) or p["ireg"] in keep or b not in v_full:
            continue
        ie = _ie(psspy.plant_data(b, 0, [v_full[b], _f]))
        log("plant %d regulated bus %d (%s): it now regulates its own terminal at %.5f pu, its full-case "
            "voltage%s" % (b, p["ireg"], "the POI, now the infinite bus" if p["ireg"] == poi
                           else "grid, not in the model", v_full[b], "" if ie in (0, None) else
                           " -- *** plant_data ierr %s" % ie))

    # ---- 6. solve and compare ----------------------------------------------------------
    if not solve():
        log("*** the plant case does not solve -- saved for inspection only, no package")
        psspy.save(_new(os.path.join(work, name + "_UNSOLVED.sav")))
        return False
    mach_cut = machine_table()
    worst_p, worst_q = 0.0, 0.0
    for k, m in units:
        c = mach_cut.get(k)
        if c is None:
            log("REVIEW: unit %d '%s' is missing from the model" % k)
            continue
        dp, dq = c["p"] - m["p"], c["q"] - m["q"]
        worst_p, worst_q = max(worst_p, abs(dp)), max(worst_q, abs(dq))
        if abs(dp) > TOL_MW or abs(dq) > TOL_MVAR:
            log("REVIEW: unit %d '%s': P %.2f -> %.2f MW, Q %.2f -> %.2f MVAr against the full case"
                % (k[0], k[1], m["p"], c["p"], m["q"], c["q"]))
    bt = bus_table()
    dv = sorted(((abs(bt[b]["vm"] - v_full[b]), b) for b in keep if b in bt), reverse=True)
    worst_v = dv[0] if dv else (0.0, 0)
    ib = mach_cut.get((poi, IB_ID), {"p": 0.0, "q": 0.0})
    inj_cut = poi_inj()
    log("model solved   : yes -- against the full case the largest unit change is P %.3f MW, Q %.3f MVAr; "
        "the largest bus voltage change %.5f pu (bus %d)" % (worst_p, worst_q, worst_v[0], worst_v[1]))
    if worst_v[0] > TOL_V_PU:
        log("REVIEW: bus %d voltage moved %.5f pu against the full case" % (worst_v[1], worst_v[0]))
    log("infinite bus   : absorbs %.1f MW, %.1f MVAr" % (-ib["p"], -ib["q"]))
    if inj_full is not None and inj_cut is not None:
        log("into the POI   : %.1f MW, %.1f MVAr in the model (full case %.1f MW, %.1f MVAr)"
            % (inj_cut.real, inj_cut.imag, inj_full.real, inj_full.imag))
    os.makedirs(pkg)
    sav_o = os.path.join(pkg, name + ".sav")
    raw_o = os.path.join(pkg, name + ".raw")
    if not _ok(psspy.save(_new(sav_o))):
        log("*** save failed: %s" % sav_o)
        return False
    if not _ok(psspy.rawd_2(0, 1, [1, 1, 1, 0, 0, 0, 0], 0, _new(raw_o))) or not os.path.isfile(raw_o):
        log("*** rawd_2 could not write %s" % raw_o)
        return False
    log("written        : %s" % sav_o)
    log("written        : %s" % raw_o)
    if SLD:
        unit_buses = set(k[0] for k, m in units)
        tri = [set((a, b, c)) for kind, a, b, c, _ck, st in branches
               if kind == "3W" and a in allb and b in allb and c in allb]
        pos, _par, kids, order = tree_layout(adj, poi, keep, unit_buses, SLD_DX, SLD_DY, tri)
        if not SLD_POI_ON_TOP:
            pos = dict((b, (x, -y)) for b, (x, y) in pos.items())
        kind_of = {}
        for kind, a, b, c, _ck, st in branches:
            ends = [x for x in (a, b, c) if x]
            if all(x in allb for x in ends):
                for x in ends:
                    for y in ends:
                        if x != y:
                            kind_of[frozenset((x, y))] = kind
        try:
            layout_svg(os.path.join(out_root, "_check", name + "_layout.svg"), pos, kids, kind_of,
                       dict((b, (buses[b]["name"], buses[b]["kv"])) for b in allb),
                       sorted(set((k[0], k[1]) for k, m in units)), poi,
                       "%s -- one-line diagram layout (as placed in %s.sld)" % (name, name), SLD_DX, SLD_DY,
                       tri)
        except Exception as e:
            log("  (layout preview not written: %s)" % e)
        rows = max(abs(p[1]) for p in pos.values()) / SLD_DY + 1
        cols = max(p[0] for p in pos.values()) - min(p[0] for p in pos.values())
        if _ok(psspy.case(sav_o)) and write_sld(os.path.join(pkg, name + ".sld"), pos, order, work, log):
            log("written        : %s (%d buses placed: %d rows, %.0f wide, POI at the %s)"
                % (os.path.join(pkg, name + ".sld"), len(order), rows, cols,
                   "top" if SLD_POI_ON_TOP else "bottom"))
    return {"proj": proj, "name": name, "poi": poi, "stem": stem, "tags": tags, "work": work, "pkg": pkg,
            "sav_o": sav_o, "buses": buses, "keep": keep, "units": units, "mach_full": mach_full,
            "loads_full": loads_full}


def plant_dynamics(ctx, log):
    """Pass 2 -- the dynamics: the plant's .dyr from the study's snapshot, then
       the package loaded back from its files and run flat. True when the .dyr
       was written."""
    proj, name, poi, stem, tags = ctx["proj"], ctx["name"], ctx["poi"], ctx["stem"], ctx["tags"]
    work, pkg, sav_o, buses, keep = ctx["work"], ctx["pkg"], ctx["sav_o"], ctx["buses"], ctx["keep"]
    units, mach_full, loads_full = ctx["units"], ctx["mach_full"], ctx["loads_full"]
    log("")

    # ---- 7. dynamics ------------------------------------------------------------------
    cnv, snp = SNP_BY_PROJECT.get(proj, (stem + ".cnv", stem + ".snp"))
    cnv, snp = _abs(cnv), _abs(snp)
    deck = find_deck(proj, tags)
    as_run_txt = None
    if os.path.isfile(cnv) and os.path.isfile(snp):
        log("dynamics       : snapshot %s (%s) with %s" % (snp, _when(snp), os.path.basename(cnv)))
        dump = os.path.join(work, name + "_as_run_all.dyr")
        outputs_to(os.path.join(work, name + "_dyda"))
        try:
            if not _ok(psspy.rstr(snp)) or not _ok(psspy.case(cnv)):
                log("*** the snapshot could not be restored")
            else:
                psspy.fact()
                psspy.tysl(0)
                load_libraries(log, whole_idv=True)
                for args in ((0, 1, [1] * 9, 0, dump), (0, 1, [1] * 6, 0, dump),
                             (-1, 1, [1] * 9, 0, dump), (0, 1, [1] * 10, 0, dump)):
                    if os.path.isfile(dump) and os.path.getsize(dump) > 0:
                        break
                    try:
                        psspy.dyda(*args)
                    except Exception:
                        continue
                if os.path.isfile(dump) and os.path.getsize(dump) > 0:
                    with open(dump, encoding="utf-8", errors="replace") as fh:
                        as_run_txt = fh.read()
                else:
                    log("*** dyda wrote nothing")
        finally:
            outputs_back()
    else:
        log("dynamics       : no snapshot beside the case (%s)" % os.path.basename(snp))
    deck_txt = None
    if deck and os.path.isfile(deck):
        with open(deck, encoding="utf-8", errors="replace") as fh:
            deck_txt = fh.read()
        log("deck text      : %s (%s)" % (deck, _when(deck)))
    allbus = set(buses)
    ids_at = collections.defaultdict(set)
    for b, i in list(mach_full) + loads_full:
        if b in keep:
            ids_at[b].add(i.upper())
    if as_run_txt is not None:
        recs, dnotes = plant_records(as_run_txt, keep, poi, allbus, True, ids_at)
        if deck_txt is not None:
            drecs, _n = plant_records(deck_txt, keep, poi, allbus, False, ids_at)
            texts, mnotes, n_deck = merge_records(recs, drecs)
            log("records        : %d as run -- %d keep the deck's own text, %d are written as PSS/E holds them"
                % (len(recs), n_deck, len(recs) - n_deck))
            dnotes += mnotes
        else:
            texts = [r.text for r in recs]
            log("records        : %d as run, written as PSS/E holds them (no deck text found)" % len(recs))
        src = snp
    elif deck_txt is not None:
        recs, dnotes = plant_records(deck_txt, keep, poi, allbus, False, ids_at)
        texts = [r.text for r in recs]
        log("WARNING: records from the deck text only -- without the snapshot, model changes the study's "
            ".idv decks made (DyreChanges, IRF) are not in them")
        log("records        : %d" % len(recs))
        src = deck
    else:
        log("*** no dynamics source -- no snapshot and no deck (set SNP_BY_PROJECT or DECK_BY_PROJECT)")
        return False
    models = collections.Counter(r.model for r in recs)
    with_model = set(r.anchor for r in recs if r.first_bus)
    for k, m in units:
        if m["st"] == 1 and k[0] not in with_model:
            log("REVIEW: unit %d '%s' has no dynamic record" % k)
    dyr_o = os.path.join(pkg, name + ".dyr")
    head = ["/ %s -- dynamic models of the plant behind POI %d %s: EGF and SGF" % (name, poi, buses[poi]["name"]),
            "/ from %s" % os.path.basename(src),
            "/ the last record is the infinite bus at the POI: GENCLS with H = 0"]
    with open(_new(dyr_o), "w", encoding="utf-8") as fh:
        fh.write("\n".join(head + texts + ["%d 'GENCLS' '%s' 0.0 0.0 /" % (poi, IB_ID)]) + "\n")
    log("written        : %s (%d records + the infinite bus)" % (dyr_o, len(texts)))
    log("models         : %s" % ", ".join("%s x%d" % (m, n) for m, n in sorted(models.items())))
    usr = sorted(set(r.model for r in recs if re.search(r"'USR[A-Z]{3}'", _data(r.text), re.I)))
    if usr:
        log("user-written   : %s -- the client needs these models' DLLs" % ", ".join(usr))
    for n in dnotes:
        log("  " + n)

    # ---- 8. load it back and run it flat -----------------------------------------
    if VALIDATE:
        validate(name, sav_o, dyr_o, work, units, log)
    return True


def validate(name, sav_o, dyr_o, work, units, log):
    log("")
    log("VALIDATION -- the package loaded back from its own files, in %s" % work)
    cwd = os.getcwd()
    prefix = os.path.join(work, name + "_validate")
    out = os.path.join(work, name + "_flat.out")
    ran = False
    try:
        os.chdir(work)
        outputs_to(prefix)
        if not (_ok(psspy.case(sav_o)) and solve()):
            log("  *** the .sav does not open and solve")
            return
        psspy.cong(0)
        for opt in (1, 2, 3):
            psspy.conl(0, 1, opt, [0, 0], [100.0, 0.0, 0.0, 100.0])
        psspy.ordr(0)
        psspy.fact()
        psspy.tysl(0)
        load_libraries(log, whole_idv=False)
        ie = _ie(psspy.dyre_new([1, 1, 1, 1], dyr_o, os.path.join(work, "conec.flx"),
                                os.path.join(work, "conet.flx"), os.path.join(work, "compile.bat")))
        log("  dyre_new            : %s" % ("ok" if ie in (0, None) else "*** ierr %s" % ie))
        n, a, t, d, ff = DYN_PARAMS
        psspy.dynamics_solution_param_2([n, _i, _i, _i, _i, _i, _i, _i], [a, t, d, ff, _f, _f, _f, _f])
        try:
            psspy.delete_all_plot_channels()          # nothing of an earlier package's run
        except Exception:
            pass
        for k, m in units:
            if m["st"] == 1:
                for code, q in ((2, "P"), (3, "Q"), (4, "V")):
                    psspy.machine_array_channel([-1, code, k[0]], k[1], "%s %d %s" % (q, k[0], k[1]))
        ie = _ie(psspy.strt_2([0, 1], out))
        log("  strt_2              : %s" % ("ok" if ie in (0, None) else "*** ierr %s" % ie))
        if ie not in (0, None):
            return
        ie = _ie(psspy.run(0, FLAT_RUN_S, 0, 1, 0))
        log("  run to %.1f s        : %s" % (FLAT_RUN_S, "ok" if ie in (0, None) else "*** ierr %s" % ie))
        ran = ie in (0, None)
    except Exception as e:
        log("  *** validation stopped: %s" % e)
    finally:
        outputs_back()
        os.chdir(cwd)
        txt = read_outputs(prefix)
        if re.search(r"INITIAL CONDITIONS SUSPECT", txt, re.I):
            log("  initial conditions  : SUSPECT -- see %s_progress.txt" % prefix)
        elif re.search(r"INITIAL CONDITIONS CHECK O\.?K", txt, re.I):
            log("  initial conditions  : check o.k.")
        for ln in [x.strip() for x in txt.splitlines()
                   if re.search(r"error|not found|no model|not modeled|invalid|ignored", x, re.I)][:15]:
            log("    | " + ln[:150])
    if ran:
        flat_report(out, log)


def flat_report(out, log):
    try:
        import dyntools
        _sh, ids, data = dyntools.CHNF(out).get_data()
    except Exception as e:
        log("  flat run            : *** the .out could not be read (%s)" % e)
        return
    worst = {"P": (0.0, "-"), "Q": (0.0, "-"), "V": (0.0, "-")}
    for n in sorted(k for k in data if k != "time"):
        y = data[n]
        lab = str(ids.get(n, ""))
        q = lab.split()[0] if lab.split() else ""
        if not y or q not in worst:
            continue
        d = max(abs(v - y[0]) for v in y) * (100.0 if q in ("P", "Q") else 1.0)   # P, Q: pu on 100 MVA
        if d > worst[q][0]:
            worst[q] = (d, lab)
    lim = dict(zip("PQV", FLAT_TOL))
    flat = all(worst[q][0] <= lim[q] for q in "PQV")
    log("  flat run            : %s -- largest drift P %.3f MW (%s), Q %.3f MVAr (%s), V %.5f pu (%s)"
        % ("flat" if flat else "NOT FLAT", worst["P"][0], worst["P"][1], worst["Q"][0], worst["Q"][1],
           worst["V"][0], worst["V"][1]))


# ============================================================================
def main():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_root = os.path.join(_abs(OUT_DIR), stamp)
    os.makedirs(out_root)
    os.makedirs(os.path.join(out_root, "_check"))
    os.makedirs(os.path.join(out_root, "_work"))
    print("[plant-models] output        -> %s" % out_root)
    print("[plant-models] project cases <- %s" % _case_dir())
    cwd = os.getcwd()
    summary = []
    os.chdir(os.path.join(out_root, "_work"))     # whatever PSS/E writes on its own lands here
    try:
        psse_start()
        logs, ctxs = collections.OrderedDict(), {}
        for proj, cfg in PLANTS.items():                 # pass 1: every plant cut, .sav / .raw
            if PROJECTS and proj not in PROJECTS:
                continue
            print("")
            logs[proj] = log = Log()
            try:
                ctxs[proj] = one_project(proj, cfg, out_root, log) or None
            except Exception:
                ctxs[proj] = None
                log("*** stopped: " + traceback.format_exc())
        for proj, log in logs.items():                   # pass 2: .dyr, then loaded back and run flat
            cfg, done = PLANTS[proj], False
            if ctxs.get(proj):
                print("")
                print("[plant-models] %s: dynamics" % cfg["name"])
                try:
                    done = plant_dynamics(ctxs[proj], log)
                except Exception:
                    log("*** stopped: " + traceback.format_exc())
            rv = log.review()
            state = "written" if done else ("NOT complete: .sav and .raw written, no .dyr" if ctxs.get(proj)
                                            else "NOT written")
            summary.append("%-22s %s" % (cfg["name"], state + (" -- %d line(s) to review in _check\\%s_CHECK.txt"
                                                               % (len(rv), cfg["name"]) if rv else "")))
            log.write(os.path.join(out_root, "_check", cfg["name"] + "_CHECK.txt"))
    finally:
        os.chdir(cwd)
    with open(_new(os.path.join(out_root, "_check", "SUMMARY.txt")), "w", encoding="utf-8") as fh:
        fh.write("Plant models on an infinite bus -- %s\n\n%s\n" % (stamp, "\n".join(summary)))
    print("")
    for ln in summary:
        print("[plant-models] " + ln)
    print("[plant-models] packages in %s" % out_root)


if __name__ == "__main__":
    main()
