# -*- coding: utf-8 -*-
"""SPP BPM fault list for every project -- from the power-flow case AND SPP's
DISIS sheet, one radius for both, no duplicates, ordered outward from the POI.

   SPP BPM (Generator Interconnection):
     "Fault events will include P1 events involving each network circuit
      segment connected within four levels of each Current-Study Request's POI
      as well as P4 and P6 events involving each network circuit connected
      within three levels ... A network circuit is comprised of each segment
      of sectionalized single (or double) circuits from substations or buses
      to accommodate generation and radial load. Each level includes all
      substations on the remote end of all network circuits. (i.e., 0 levels
      away from a line tap POI includes substations with at least 3 connected
      circuits on either end of the tapped circuit) ... Each event should
      remove from service all elements that are expected to automatically
      disconnect for each event."

   HOW THE LEVELS ARE COUNTED (from the case, >= KV_MIN):
     substation   buses joined by a transformer or a bus tie (|X| < JUMPER_X_PU)
     radial       a substation hanging off the network by one path only
                  (load / generation stub) -- not a network circuit
     level node   a substation with >= HUB_MIN_CIRCUITS network circuits
     level 0      the POI substation if it is a level node, else the level
                  nodes at either end of the tapped circuit
     level k+1    the substations at the remote end of the circuits out of
                  level k (a substation between two level nodes counts itself)
     an event's level = the level of its FAULTED bus, so "faulted bus <= 3"
     is "circuit within four levels". Below KV_MIN: +1 per line crossed.

   WHAT IS IN THE LIST
     DISIS  SPP's sheet, faulted bus within SPP_LEVELS_BY_EVENT; "remove
            bus" events become every element at that bus plus its loads /
            shunts / units; loads, shunts and units the sheet names are dropped
     SCRIPT built from the case, faulted bus within CASE_LEVELS_BY_EVENT, only
            where the sheet has nothing the same:
            P1.2  each network circuit segment (a tapped line out whole),
                  faulted at the end nearer the POI, 20-cycle reclose
            P1.3  each 2- and 3-winding network transformer (no reclose)
            P4    SPP's proxy at each bus the sheet has no P4 for:
                  (a) the whole busbar  (b) the two highest-loaded branches
            P4 is limited as SPP's reports do: P4_MAX_PER_BUS most severe at
            a bus, P4_MAX_TOTAL per project, nearest the POI first
            IMPACT_SCREEN: a P1 at level >= SCREEN_FROM_LEVEL is left out when
            the POI stays >= SCREEN_POI_V_PU during it and nothing it trips
            carries SCREEN_DF_MIN of the project's MW
            P6    GENERATE_P6: a line out beforehand, 3PH on another element
     Not in the list: anything faulted in the plant or switching a plant gen
     tie; a P4 that leaves the plant no path to the grid (e.g. every branch
     at the POI out). Duplicates (same elements out) are kept once -- SPP's
     (DISIS) over the script's, then the one faulted nearest the POI; a
     script P1 on a circuit SPP already has a P1 for is not made. Order: all
     P1 outward from the POI, then all P4, then P6.
     Every event is checked the way the study opens elements (EXECUTION CHECK
     in the report); a 3-winding twin is written by a winding pair that
     names it alone.

   OUTPUT (nothing in use is overwritten unless INSTALL = True):
     OUT_DIR\\SPP_FAULTS_CON_<project>.csv        same columns the study reads
     OUT_DIR\\FAULT_LIST_BPM_<project>.txt        what was kept, dropped and why
     OUT_DIR\\FAULT_LIST_LEVELS_<project>.csv     every bus: substation, level

   Run with the PSS/E Python (3.4), from the folder z6_main.py is in:
       python z6_fault_list.py
"""
import os
import sys
import re
import csv
import time
import shutil

# ============================================================================
# SETTINGS
# ============================================================================
PROJECTS = ["SantaFe", "IronStar", "EastFork", "EmpirePrairie"]
# POI and plant feeder buses (as z6_spp_b.py BESS_PROJECTS)
PROJECT_BUSES = {
    "SantaFe":       {"poi": 765911, "feeders": [765912, 765922, 765932, 765935]},
    "IronStar":      {"poi": 560080, "feeders": [587313, 587317]},
    # MINGO 3 is SPP's POI; 531623 EASTFORK3 is the existing plant's own bus
    # behind it, so EASTFORK3-MINGO is the plant's gen tie (as z4_disis_con.py)
    "EastFork":      {"poi": 531429, "feeders": [531623, 531620, 531607]},
    "EmpirePrairie": {"poi": 761383, "feeders": [761379, 761382, 761400, 761403]},
}
DISIS_SHEET = r"{root}\DISIS_CENTRAL_FAULTS.xlsx"     # SPP's DISIS event sheet (.xlsx / .csv)
CASE = None                                  # None = the case z6_main.py builds the list from
                                             #   (FAULT_LIST_FROM, BASE_SAV / BASE_SAV_BY_PROJECT)
OUT_DIR = r"{root}\FAULT_LISTS_BPM"          # new lists + reports go here
INSTALL = False                              # True = also copy each list over SHARED_FAULTS_CSV
                                             #   (the old one kept as .bak_<time>)
KEEP_OLD_IDS = False                         # False = F01.. numbered outward from the POI (runs again)
                                             # True  = an event identical to one in the old list keeps its
                                             #   F-number and con_id, so its finished run is used as is
# LEVEL OF THE FAULTED BUS: substations from the POI to it, the bus itself
# included, the POI excluded (minus one when the POI is a tap, so the tapped
# circuit's ends are 0). Measured the same way for both sources.
# Every substation is a step (HUB_MIN_CIRCUITS = 2, as SPP's Aneden reports
# GEN-2026-SR1/SR11 count). P1 <= 3 / P4 <= 1 with the P4 limits and the
# impact screen below: ~70-180 events per project (~90-270 without the
# screen); P1 <= 2 ~55-150. Each level more roughly doubles the list.
SPP_LEVELS_BY_EVENT = {"P1": 3, "P4": 1, "P6": 1}     # events from the DISIS sheet
CASE_LEVELS_BY_EVENT = {"P1": 3, "P4": 1, "P6": 1}    # events built from the power-flow case
KV_MIN = 100.0                               # network circuits at / above this kV
SUBT_KV_MIN = 69                             # also P1 on lines down to this kV (None = off) ...
SUBT_P1_LEVEL = 1                            # ... faulted at a bus within this level (SPP 115 kV POI reports)
XFMR_LV_KV_MIN = 60.0                        # P1.3: transformer second winding at / above this kV
HUB_MIN_CIRCUITS = 2                         # a substation is a level node with this many circuits:
                                             #   2 = every substation is a step (SPP's Aneden reports)
                                             #   3 = only switching stations (BPM text, MEPPI reports)
JUMPER_X_PU = 0.0005                         # |X| below this = bus tie (same substation)
EVENTS = {"P1.2": True, "P1.3": True, "P4": True}   # built from the case (False = that kind not built)
INCLUDE_RADIAL_P1 = True                     # True = P1 also on radial lines (other plants' gen ties,
                                             #   radial loads) -- their units dropped, no reclose (SPP reports)
P4_PROXY = "proxy"                           # P4 where the DISIS sheet has none: "proxy" = SPP's two
                                             #   (whole busbar + two highest-loaded branches) | "pairs" =
                                             #   whole busbar + every pair of elements at the bus
GENERATE_P6 = False                          # True = also build P6 (prior outage + 3PH) from the case
P6_MAX_PER_BUS = 2                           # P6 pairs per bus (highest-loaded first)
P4_SKIP_IF_PLANT_ISLANDED = True             # no P4 that cuts the plant off the grid
MAX_EVENTS = 400                             # cap per project (None = no cap): whole levels are kept
                                             #   outward from the POI; the level that overflows is filled
                                             #   DISIS first, then nearest the POI, then highest kV
# P4 AS SPP'S REPORTS DO IT: at the POI substation and its level-1 neighbours
# (P4 <= 1 above), a few stuck-breaker events per bus (SR1 15 at 5 buses, SR11
# 22 at 7). Kept per bus: SPP's own first, then the most elements / MW out.
P4_MAX_PER_BUS = 3                           # None = every P4 at the bus
P4_LABEL = "P4"                              # planning_event written for every P4 ("P4" -- no
                                             #   P4.2 / P4.3 / P4.5); None = keep the sub-category
P4_MAX_TOTAL = 50                            # P4 per project (SPP reports: 4-22); nearest the POI kept
# IMPACT SCREEN -- P1 only; every P4 stays as SPP defines it. A P1 faulted at
# or beyond SCREEN_FROM_LEVEL is left out when BOTH hold: the POI keeps at
# least SCREEN_POI_V_PU during the fault (bolted 3PH, case impedances and
# machine source impedances), and no element it trips carries SCREEN_DF_MIN of
# the project's MW (DC flow, POI to swing). A P1 that drops units is kept.
# Every event left out is listed in the report with both numbers.
IMPACT_SCREEN = True
SCREEN_FROM_LEVEL = 3                        # levels 0-2 always kept (the depth SPP reports cover)
SCREEN_POI_V_PU = 0.85
SCREEN_DF_MIN = 0.03
NORMAL_CLEAR = [(345.0, 6), (0.0, 7)]        # cycles by kV (SPP: 6 at 345 kV, 7 below)
STUCK_CYCLES = 16                            # P4 stuck-breaker clearing (SPP 16)
RECLOSE_WAIT = 20                            # P1.2 / P6 reclose wait (cycles)
SLG_RETAIN_VPU = 0.60                        # generated P4 SLG retained voltage
ISLAND_SEARCH_MAX = 3000                     # a pocket bigger than this counts as still on the grid

HERE = os.path.dirname(os.path.abspath(__file__))


# ============================================================================
# SETTINGS READ FROM z6_main.py (text only -- nothing there is run)
# ============================================================================
def _main_setting(name, default):
    import ast
    p = os.path.join(HERE, "z6_main.py")
    try:
        with open(p) as fh:
            for ln in fh:
                m = re.match(r"^%s\s*=\s*(.+)$" % re.escape(name), ln)
                if not m:
                    continue
                rhs = m.group(1)
                for cut in range(len(rhs), 0, -1):       # strip a trailing # comment
                    try:
                        return ast.literal_eval(rhs[:cut].strip())
                    except Exception:
                        continue
    except Exception:
        pass
    return default


ROOT = _main_setting("ROOT", "") or HERE


def _abs(p):
    p = str(p or "").replace("{root}", ROOT)
    if p and not (os.path.isabs(p) or p[:2] in ("\\\\", "//") or re.match(r"^[A-Za-z]:[\\/]", p)):
        p = os.path.join(ROOT, p)
    return p


def _case_for(proj):
    if CASE:
        return _abs(CASE)
    frm = str(_main_setting("FAULT_LIST_FROM", "BASE") or "BASE").upper()
    if frm == "TEST":
        folder = _main_setting("PROJ_FOLDER", "Projects")
        sav = (_main_setting("PROJ_SAV_BY_PROJECT", {}) or {}).get(proj) \
            or _main_setting("PROJ_SAV", "")
    else:
        folder = _main_setting("BASE_FOLDER", "Base")
        sav = (_main_setting("BASE_SAV_BY_PROJECT", {}) or {}).get(proj) \
            or _main_setting("BASE_SAV", "")
    shared = _main_setting("SHARED_DECK", "")
    if shared:
        sav = _main_setting("SHARED_DECK_SAV", sav)
    if os.path.isabs(str(sav)) or re.match(r"^[A-Za-z]:[\\/]", str(sav)):
        return sav
    return os.path.join(_abs(folder), sav)


def _shared_csv(proj):
    t = _main_setting("SHARED_FAULTS_CSV", r"{root}\SPP_FAULTS_CON_{project}.csv")
    return _abs(str(t).replace("{project}", proj))


# ============================================================================
# PSS/E
# ============================================================================
def _bootstrap_psse():
    import glob as _glob
    pyv = sys.version_info[:2]
    prefer = "PSSPY%d%d" % pyv
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        for d in _glob.glob(os.path.join(base, "PSSE3*")):
            roots.append(d)

    def _add(d):
        if d and os.path.isdir(d):
            while d in sys.path:
                sys.path.remove(d)
            sys.path.insert(0, d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

    for root in roots:
        if root and os.path.isdir(os.path.join(root, prefer)):
            _add(os.path.join(root, prefer))
            _add(os.path.join(root, "PSSBIN"))
            return
    for root in roots:
        if root and os.path.isdir(os.path.join(root, "PSSBIN")):
            _add(os.path.join(root, "PSSBIN"))
            return


psspy = None


def _psse_start():
    global psspy
    if psspy is not None:
        return
    _bootstrap_psse()
    try:
        import psse34  # noqa: F401
    except Exception:
        pass
    import psspy as _p
    psspy = _p
    try:
        psspy.psseinit(150000)
    except Exception as e:
        print("[psse] psseinit: %s" % e)
    try:
        import redirect
        redirect.psse2py()
    except Exception:
        pass


def _ok(r):
    return r if not isinstance(r, (list, tuple)) else r[0]


def _arr(fn, *args):
    """psspy a* call -> list of columns, [] on any error."""
    try:
        r = fn(*args)
        if isinstance(r, (list, tuple)) and len(r) == 2 and r[0] == 0 and r[1]:
            return r[1]
    except Exception:
        pass
    return []


def _ck(x):
    return str(x).strip().upper() or "1"


class Net(object):
    """Everything this script reads from the case, in plain dicts."""

    def __init__(self):
        self.kv, self.name, self.btype = {}, {}, {}
        self.elem = {}                    # key -> {"kind","buses","ck","mva"}
        self.at = {}                      # bus -> [element key]
        self.mach, self.load, self.fsh, self.ssh = {}, {}, {}, {}
        self.swing = []
        self.t3_all = []                  # [((w1, w2, w3), ckt)] incl. out of service
        self._t3_first = None
        self.z = {}                       # key -> R+jX (pu, system base); a 3W -> star (z1, z2, z3)
        self.zsrc = []                    # [(bus, R+jX)] machine source impedances (system base)
        self.zdefault = {}                # what had no impedance in the case -> count
        self._screen = None

    def study_resolves(self, a, b, ck):
        """The element the study (z6_spp_b _switch_branch) opens for "a-b ck":
           a line, else a two-winding, else the FIRST three-winding in the case
           with both buses on that circuit. None = nothing."""
        a, b, ck = int(a), int(b), _ck(ck)
        for k in (("L", min(a, b), max(a, b), ck), ("X", min(a, b), max(a, b), ck)):
            if k in self.elem:
                return k
        if self._t3_first is None:
            m = {}
            for w, c in self.t3_all:
                for x in w:
                    for y in w:
                        if x != y:
                            m.setdefault((x, y, c), ("T",) + tuple(sorted(w)) + (c,))
            self._t3_first = m
        return self._t3_first.get((a, b, ck))

    def add_elem(self, key, kind, buses, ck, mva=0.0, x=None):
        if key in self.elem:
            return
        self.elem[key] = {"kind": kind, "buses": tuple(buses), "ck": ck,
                          "mva": float(mva or 0.0), "x": x}
        for b in set(buses):
            self.at.setdefault(b, []).append(key)

    def nbrs(self, b, skip_el=None, skip_bus=None):
        for k in self.at.get(b, ()):
            if skip_el and k in skip_el:
                continue
            for o in self.elem[k]["buses"]:
                if o != b and not (skip_bus and o in skip_bus):
                    yield o


def load_net(case_path):
    _psse_start()
    ie = _ok(psspy.case(case_path))
    if ie not in (0, None):
        raise RuntimeError("psspy.case(%s) returned %s" % (case_path, ie))
    try:        # a solved case gives the flows the P4 / P6 ranking uses
        psspy.fnsl([0, 0, 0, 1, 1, 0, 99, 0])
    except Exception:
        pass
    n = Net()
    bi = _arr(psspy.abusint, -1, 2, ["NUMBER", "TYPE"])
    bk = _arr(psspy.abusreal, -1, 2, ["BASE"])
    bn = _arr(psspy.abuschar, -1, 2, ["NAME"])
    for i, b in enumerate(bi[0] if bi else []):
        t = int(bi[1][i])
        if t == 4:
            continue
        n.kv[int(b)] = float(bk[0][i]) if bk else 0.0
        n.name[int(b)] = str(bn[0][i]).strip() if bn else ""
        n.btype[int(b)] = t
        if t == 3:
            n.swing.append(int(b))
    # lines (in service, non-transformer)
    li = _arr(psspy.abrnint, -1, 1, 1, 1, 1, ["FROMNUMBER", "TONUMBER"])
    lc = _arr(psspy.abrnchar, -1, 1, 1, 1, 1, ["ID"])
    lx = _arr(psspy.abrncplx, -1, 1, 1, 1, 1, ["RX"])
    lm = _arr(psspy.abrnreal, -1, 1, 1, 1, 1, ["MVA"])
    for i, a in enumerate(li[0] if li else []):
        a, b = int(a), int(li[1][i])
        if a not in n.kv or b not in n.kv:
            continue
        ck = _ck(lc[0][i]) if lc else "1"
        x = abs(complex(lx[0][i]).imag) if lx else None
        n.add_elem(("L", min(a, b), max(a, b), ck), "line", (a, b), ck,
                   lm[0][i] if lm else 0.0, x)
        if lx:
            n.z.setdefault(("L", min(a, b), max(a, b), ck), complex(lx[0][i]))
    # two-winding transformers
    ti = _arr(psspy.atrnint, -1, 1, 1, 1, 1, ["FROMNUMBER", "TONUMBER"])
    tc = _arr(psspy.atrnchar, -1, 1, 1, 1, 1, ["ID"])
    tm = _arr(psspy.atrnreal, -1, 1, 1, 1, 1, ["MVA"])
    tz = _arr(getattr(psspy, "atrncplx", None), -1, 1, 1, 1, 1, ["RXACT"]) \
        or _arr(getattr(psspy, "atrncplx", None), -1, 1, 1, 1, 1, ["RXNOM"])
    for i, a in enumerate(ti[0] if ti else []):
        a, b = int(a), int(ti[1][i])
        if a not in n.kv or b not in n.kv:
            continue
        ck = _ck(tc[0][i]) if tc else "1"
        n.add_elem(("X", min(a, b), max(a, b), ck), "xf2", (a, b), ck,
                   tm[0][i] if tm else 0.0)
        if tz:
            n.z.setdefault(("X", min(a, b), max(a, b), ck), complex(tz[0][i]))
    # three-winding transformers (status 0 = all windings out)
    wi = _arr(psspy.atr3int, -1, 1, 1, 2, 1,
              ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER", "STATUS"])
    wc = _arr(psspy.atr3char, -1, 1, 1, 2, 1, ["ID"])
    wz = []
    for names in (["RX1-2ACT", "RX2-3ACT", "RX3-1ACT"], ["RX1-2NOM", "RX2-3NOM", "RX3-1NOM"]):
        wz = _arr(getattr(psspy, "atr3cplx", None), -1, 1, 1, 2, 1, names)
        if len(wz) == 3:
            break
    for i, a in enumerate(wi[0] if wi else []):
        w = (int(a), int(wi[1][i]), int(wi[2][i]))
        ck = _ck(wc[0][i]) if wc else "1"
        n.t3_all.append((w, ck))          # every one, in the case's order: the study's lookup
        if int(wi[3][i]) == 0 or any(x not in n.kv for x in w):
            continue
        k3 = ("T",) + tuple(sorted(w)) + (ck,)
        n.add_elem(k3, "xf3", w, ck, 0.0)
        if len(wz) == 3 and k3 not in n.z:
            z12, z23, z31 = complex(wz[0][i]), complex(wz[1][i]), complex(wz[2][i])
            n.z[k3] = ((z12 + z31 - z23) / 2, (z12 + z23 - z31) / 2, (z23 + z31 - z12) / 2)
    for (fi, fc, dst) in ((psspy.amachint, psspy.amachchar, n.mach),
                          (psspy.aloadint, psspy.aloadchar, n.load),
                          (psspy.afxshuntint, psspy.afxshuntchar, n.fsh)):
        bb = _arr(fi, -1, 1, ["NUMBER"])
        cc = _arr(fc, -1, 1, ["ID"])
        for i, b in enumerate(bb[0] if bb else []):
            dst.setdefault(int(b), []).append(str(cc[0][i]).strip() if cc else "1")
    # machine source impedances (ZSORCE on MBASE) for the impact screen
    try:
        sbase = float(psspy.sysmva())
    except Exception:
        sbase = 100.0
    mb = _arr(psspy.amachint, -1, 1, ["NUMBER"])
    mz = _arr(getattr(psspy, "amachcplx", None), -1, 1, ["ZSORCE"])
    mm = _arr(getattr(psspy, "amachreal", None), -1, 1, ["MBASE"])
    for i, b in enumerate(mb[0] if mb else []):
        base = float(mm[0][i]) if mm and float(mm[0][i]) > 0 else sbase
        z = complex(mz[0][i]) if mz else 0j
        if abs(z) < 1e-4:
            z = complex(0.0, 0.25)
            n.zdefault["machine source impedance"] = n.zdefault.get("machine source impedance", 0) + 1
        if abs(z) < 50.0:                 # 9999 = no source (inverter), left out
            n.zsrc.append((int(b), z * sbase / base))
    bb = _arr(psspy.aswshint, -1, 1, ["NUMBER"])
    cc = _arr(getattr(psspy, "aswshchar", lambda *a: None), -1, 1, ["ID"])
    for i, b in enumerate(bb[0] if bb else []):
        n.ssh.setdefault(int(b), []).append(str(cc[0][i]).strip() if cc else "1")
    print("[case] %s: %d buses, %d branches, %d machines, swing %s"
          % (os.path.basename(case_path), len(n.kv), len(n.elem),
             sum(len(v) for v in n.mach.values()), n.swing[:3]))
    return n


# ============================================================================
# LEVELS (SPP BPM) AROUND ONE POI
# ============================================================================
class Levels(object):
    def __init__(self, net, poi, feeders):
        self.net, self.poi = net, int(poi)
        kv = net.kv
        # ---- the plant: whatever is cut off from the grid when the POI bus is
        #      out (the project's feeders, collector, units -- existing and new)
        #      -- only the pieces that hold a feeder (or a unit when no feeder
        #      is in the case), so a radial load off the POI is not "plant"
        self.plant = set()
        fset = set(f for f in feeders if f in kv and f != self.poi)
        done = set([self.poi])
        for s0 in list(net.nbrs(self.poi)):
            if s0 in done:
                continue
            comp, todo, grid = set([s0]), [s0], False
            while todo:
                b = todo.pop()
                if b in net.swing or len(comp) > ISLAND_SEARCH_MAX:
                    grid = True
                    break
                for o in net.nbrs(b):
                    if o != self.poi and o not in comp:
                        comp.add(o)
                        todo.append(o)
            done |= comp
            if grid:
                continue
            if (comp & fset) or (not fset and any(net.mach.get(b) for b in comp)):
                self.plant |= comp
        self.plant |= fset                   # a feeder named but meshed: still plant
        units = sorted(b for b in self.plant if net.mach.get(b))
        self.plant_nodes = units or [f for f in feeders if f in kv and f != self.poi] \
            or [self.poi]
        # ---- network buses and substations
        N = set(b for b in kv if kv[b] >= KV_MIN and b not in self.plant)
        N.add(self.poi)
        self.N = N
        par = dict((b, b) for b in N)

        def find(b):
            while par[b] != b:
                par[b] = par[par[b]]
                b = par[b]
            return b

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                par[max(ra, rb)] = min(ra, rb)

        for k, e in net.elem.items():
            bs = [b for b in e["buses"] if b in N]
            if e["kind"] in ("xf2", "xf3") and len(bs) >= 2:
                for b in bs[1:]:
                    union(bs[0], b)
            elif e["kind"] == "line" and len(bs) == 2 and e["x"] is not None \
                    and e["x"] < JUMPER_X_PU:
                union(bs[0], bs[1])
        self.st = dict((b, find(b)) for b in N)
        # ---- substation graph (network lines only)
        nb = {}
        for k, e in net.elem.items():
            if e["kind"] != "line":
                continue
            a, b = e["buses"]
            if a in N and b in N and self.st[a] != self.st[b]:
                nb.setdefault(self.st[a], set()).add(self.st[b])
                nb.setdefault(self.st[b], set()).add(self.st[a])
        poi_st = self.st[self.poi]
        # ---- radial substations: pruned leaf by leaf
        deg = dict((s, len(v)) for s, v in nb.items())
        rparent, removed = {}, set()
        todo = [s for s in nb if deg[s] <= 1 and s != poi_st]
        while todo:
            s = todo.pop()
            if s in removed or s == poi_st:
                continue
            removed.add(s)
            live = [t for t in nb[s] if t not in removed]
            if live:
                rparent[s] = live[0]
                t = live[0]
                deg[t] -= 1
                if deg[t] <= 1 and t != poi_st:
                    todo.append(t)
        core = dict((s, set(t for t in v if t not in removed))
                    for s, v in nb.items() if s not in removed)
        self.hub = set(s for s, v in core.items() if len(v) >= HUB_MIN_CIRCUITS)
        # ---- level 0 (BPM: a POI on a tapped line -- 2 network circuits -- is
        #      not a level of its own; the substations at the tapped circuit's
        #      ends are 0, whatever HUB_MIN_CIRCUITS says)
        if len(core.get(poi_st, ())) >= 3 and poi_st in self.hub:
            l0 = set([poi_st])
        else:
            l0, seen, todo = set(), set([poi_st]), [poi_st]
            while todo:
                s = todo.pop()
                for t in core.get(s, ()):
                    if t in seen:
                        continue
                    seen.add(t)
                    if t in self.hub:
                        l0.add(t)
                    else:
                        todo.append(t)
            l0 = l0 or set([poi_st])
        # ---- non-hub components of the core, and the hubs on their border
        comp_of, comps = {}, []
        for s in core:
            if s in self.hub or s in comp_of:
                continue
            ci, mem, bord, todo = len(comps), set([s]), set(), [s]
            comp_of[s] = ci
            while todo:
                x = todo.pop()
                for t in core[x]:
                    if t in self.hub:
                        bord.add(t)
                    elif t not in comp_of:
                        comp_of[t] = ci
                        mem.add(t)
                        todo.append(t)
            comps.append((mem, bord))
        hub_nb = dict((h, set()) for h in self.hub)
        for h in self.hub:
            for t in core[h]:
                if t in self.hub:
                    hub_nb[h].add(t)
                else:
                    hub_nb[h] |= comps[comp_of[t]][1] - set([h])
        # ---- level BFS over the level nodes
        L = dict((h, 0) for h in l0)
        front = list(l0)
        while front:
            nxt = []
            for h in front:
                for g in hub_nb.get(h, ()):
                    if g not in L:
                        L[g] = L[h] + 1
                        nxt.append(g)
            front = nxt
        BIG = 99
        slev = {}
        for h in self.hub:
            slev[h] = L.get(h, BIG)
        for mem, bord in comps:
            lv = min([L.get(h, BIG) for h in bord] or [BIG]) + 1
            for s in mem:
                slev[s] = min(lv, BIG)
        slev[poi_st] = 0

        def rlev(s, depth=0):
            if s in slev:
                return slev[s]
            p = rparent.get(s)
            v = BIG if (p is None or depth > 200) else min(BIG, rlev(p, depth + 1) + 1)
            slev[s] = v
            return v

        for s in removed:
            rlev(s)
        for s in set(self.st.values()):
            slev.setdefault(s, BIG)
        self.slev, self.L, self.l0 = slev, L, l0
        self.radial = removed
        self.BIG = BIG
        # ---- bus level: network buses from their substation; a bus below
        #      KV_MIN from the nearest network bus, +1 per line crossed below
        #      KV_MIN (a transformer stays in the same substation: +0)
        import heapq
        blev = dict((b, slev[self.st[b]]) for b in N)
        for b in self.plant:
            blev[b] = 0
        pq = [(v, b) for b, v in blev.items() if v < BIG]
        heapq.heapify(pq)
        while pq:
            v, b = heapq.heappop(pq)
            if v > blev.get(b, BIG) or v >= BIG:
                continue
            for k in net.at.get(b, ()):
                e = net.elem[k]
                step = 1 if e["kind"] == "line" else 0
                for o in e["buses"]:
                    if o == b or o in N or o in self.plant:
                        continue
                    if v + step < blev.get(o, BIG):
                        blev[o] = v + step
                        heapq.heappush(pq, (v + step, o))
        self.blev = blev
        # ---- node distance from the POI (all in-service branches)
        hop = {self.poi: 0}
        front = [self.poi]
        while front:
            nxt = []
            for b in front:
                for o in net.nbrs(b):
                    if o not in hop:
                        hop[o] = hop[b] + 1
                        nxt.append(o)
            front = nxt
            if front and hop[front[0]] > 40:
                break
        self.hop = hop

    def bus_level(self, b):
        return self.blev.get(int(b), self.BIG)

    def elem_level(self, key):
        e = self.net.elem.get(key)
        if e is None:
            return self.BIG
        bs = e["buses"]
        if any(b in self.plant for b in bs):
            return 0
        if e["kind"] == "line" and all(b in self.N for b in bs):
            s1, s2 = self.st[bs[0]], self.st[bs[1]]
            if s1 == s2:
                return self.slev[s1]
            if s1 in self.hub and s2 in self.hub:
                return min(self.BIG, min(self.slev[s1], self.slev[s2]) + 1)
            return max(self.slev[s1], self.slev[s2])
        if e["kind"] in ("xf2", "xf3"):
            hv = max(bs, key=lambda b: self.net.kv.get(b, 0.0))
            if hv in self.N:
                return self.slev[self.st[hv]]
        return max(self.bus_level(b) for b in bs)


# ============================================================================
# SPP's DISIS SHEET
# ============================================================================
def _xlsx_rows(path):
    import zipfile
    import xml.etree.ElementTree as ET
    NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(path)
    try:
        shared = []
        try:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")):
                shared.append("".join(t.text or "" for t in si.iter(NS + "t")))
        except KeyError:
            pass
        name = "xl/worksheets/sheet1.xml"
        if name not in z.namelist():
            name = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        rows = []
        for row in ET.fromstring(z.read(name)).iter(NS + "row"):
            cells = {}
            for c in row.iter(NS + "c"):
                col = re.match(r"[A-Z]+", c.get("r") or "")
                if not col:
                    continue
                v = c.find(NS + "v")
                if v is None:
                    ins = c.find(NS + "is")
                    val = "".join(t.text or "" for t in ins.iter(NS + "t")) if ins is not None else ""
                elif c.get("t") == "s":
                    try:
                        val = shared[int(v.text)]
                    except (ValueError, IndexError):
                        val = v.text or ""
                else:
                    val = v.text or ""
                cells[col.group(0)] = val
            if any((x or "").strip() for x in cells.values()):
                rows.append(cells)
    finally:
        z.close()
    if not rows:
        return []
    head = rows[0]
    return [dict((head.get(k, k), v) for k, v in r.items()) for r in rows[1:]]


def read_disis(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        rows = _xlsx_rows(path)
    else:
        with open(path) as fh:
            rows = list(csv.DictReader(fh))
    out, seen = [], set()
    for r in rows:
        d = dict((re.sub(r"[^a-z]", "", str(k).lower()), v) for k, v in r.items())
        ev = {"id": (d.get("eventid") or "").strip(),
              "text": (d.get("eventdescription") or "").strip(),
              "cat": (d.get("eventcategory") or "").strip().upper()}
        if not ev["text"]:
            continue
        key = (ev["id"], ev["text"])
        if key in seen:                     # the sheet repeats each event per study
            continue
        seen.add(key)
        out.append(ev)
    return out


_T_TOK = re.compile(r"\((\d+)\)\s*([\d.]+)\s*kV", re.I)
_T_CKT = re.compile(r"#\s*(\S*)\s*$")
_T_HEAD = re.compile(r"(?i)^\s*(3\s*Phase\s+fault|Single\s+Phase\s+Fault)")
_T_CLEAR = re.compile(r"(?i)^\s*b\.\s*(?:Clear\s+fault\s+after|Run\s+for)\s+([\d.]+)\s+cycles")
_T_WAIT = re.compile(r"(?i)^\s*c\.\s*Wait\s+([\d.]+)\s+cycles")
_T_ITEM = re.compile(r"(?i)^\s*b\.\s*\d+\.\s*(.+?)\s*$")
_T_STEP = re.compile(r"(?i)^\s*[a-e]\.")


def parse_disis(text):
    """-> {type, fbus, fkv, cycles, wait, items:[(kind, buses, ck, raw)]} or (None, why)."""
    lines = []
    for ln in str(text).replace("\r", "").split("\n"):
        s = ln.strip()
        if not s:
            continue
        if lines and not _T_STEP.match(s) and not _T_HEAD.match(s):
            lines[-1] += " " + s
        else:
            lines.append(s)
    head = None
    for s in lines:
        m = _T_HEAD.match(s)
        if m and _T_TOK.search(s):
            head = (m.group(1).lower(), _T_TOK.findall(s)[0])
            break
    if not head:
        return None, "no fault-bus line"
    p = {"type": "SLG" if head[0].startswith("single") else "3PH",
         "fbus": int(head[1][0]), "fkv": float(head[1][1]),
         "cycles": None, "wait": None, "items": []}
    for s in lines:
        m = _T_CLEAR.match(s)
        if m:
            p["cycles"] = float(m.group(1))
            continue
        m = _T_WAIT.match(s)
        if m:
            p["wait"] = float(m.group(1))
            continue
        m = _T_ITEM.match(s)
        if not m:
            continue
        it = m.group(1)
        up = it.upper()
        tok = [int(b) for b, _kv in _T_TOK.findall(it)]
        mc = _T_CKT.search(it)
        ck = _ck(mc.group(1)) if (mc and mc.group(1)) else ""
        if not tok:
            p["items"].append(("?", [], ck, it))
        elif "REMOVE BUS" in up:
            p["items"].append(("bus", tok[:1], ck, it))
        elif re.search(r"\bLOAD\b", up):
            p["items"].append(("load", tok[:1], ck or "1", it))
        elif "GENERATOR" in up or re.match(r"(?i)^UNIT\b", it):
            p["items"].append(("mach", tok[:1], ck or "1", it))
        elif "SWITCHED SHUNT" in up:
            p["items"].append(("ssh", tok[:1], ck, it))
        elif "SHUNT" in up:
            p["items"].append(("fsh", tok[:1], ck, it))
        elif len(tok) >= 3:
            p["items"].append(("br3", tok[:3], ck or "1", it))
        elif len(tok) == 2:
            p["items"].append(("br", tok, ck or "1", it))
        else:
            p["items"].append(("?", tok, ck, it))
    if p["cycles"] is None:
        p["cycles"] = float(clear_cycles(p["fkv"]))
    return p, ""


def find_branch(net, buses, ck):
    """The case element an SPP item names, or None."""
    if len(buses) >= 3:
        k = ("T",) + tuple(sorted(buses[:3])) + (ck,)
        if k in net.elem:
            return k
    a, b = buses[0], buses[1]
    for k in (("L", min(a, b), max(a, b), ck), ("X", min(a, b), max(a, b), ck)):
        if k in net.elem:
            return k
    for k in net.at.get(a, ()):          # a three-winding named by two of its buses
        e = net.elem[k]
        if e["kind"] == "xf3" and b in e["buses"] and e["ck"] == ck:
            return k
    return None


# ============================================================================
# EVENTS
# ============================================================================
def lim(cls, src="CASE"):
    """Level limit of the faulted bus for an event class (P1 / P4 / P6), for
       events from the DISIS sheet or built from the case."""
    t = SPP_LEVELS_BY_EVENT if src == "DISIS" else CASE_LEVELS_BY_EVENT
    return int(t.get(cls, t.get("P1", 3)))


def clear_cycles(kv):
    for thr, c in NORMAL_CLEAR:
        if float(kv or 0) >= thr:
            return c
    return NORMAL_CLEAR[-1][1]


def new_event(src, cid, ev, ftype, fbus, cycles):
    return {"src": src, "con_id": cid, "ev": ev, "type": ftype, "fbus": int(fbus),
            "cycles": cycles, "trips": [], "pre": [], "drop_m": [], "drop_l": [],
            "drop_s": [], "rm_bus": [], "reclose": False, "wait": "",
            "retained_v": "", "primary": "", "notes": [], "sub": ""}


def event_from_disis(net, d):
    p, why = parse_disis(d["text"])
    if p is None:
        return None, why
    cat = d["cat"] or "P1"
    e = new_event("DISIS", d["id"], cat, p["type"], p["fbus"], p["cycles"])
    e["fkv"] = p["fkv"]
    if p["wait"] is not None:
        e["reclose"], e["wait"] = True, p["wait"]
    unknown, missing = [], []
    for kind, bs, ck, raw in p["items"]:
        if kind == "?":
            unknown.append(raw)
        elif kind in ("br", "br3"):
            k = find_branch(net, bs, ck)
            if k:
                if k not in e["trips"]:
                    e["trips"].append(k)
            else:
                missing.append(raw)
        elif kind == "bus":
            b = bs[0]
            if b not in net.kv:
                missing.append(raw)
                continue
            e["rm_bus"].append(b)
            for k in net.at.get(b, ()):
                if k not in e["trips"]:
                    e["trips"].append(k)
            for m in net.mach.get(b, ()):
                e["drop_m"].append((b, m))
            for m in net.load.get(b, ()):
                e["drop_l"].append((b, m))
            for m in list(net.fsh.get(b, ())) + list(net.ssh.get(b, ())):
                e["drop_s"].append((b, m))
        elif kind == "load":
            (e["drop_l"] if bs[0] in net.load else missing).append(
                (bs[0], ck) if bs[0] in net.load else raw)
        elif kind == "mach":
            if bs[0] in net.mach:
                ids = net.mach[bs[0]] if ck not in [_ck(x) for x in net.mach[bs[0]]] else [ck]
                for m in ids:
                    e["drop_m"].append((bs[0], m))
            else:
                missing.append(raw)
        elif kind in ("fsh", "ssh"):
            have = net.fsh.get(bs[0], []) if kind == "fsh" else net.ssh.get(bs[0], [])
            if not have:
                missing.append(raw)
            for m in have:
                if not ck or _ck(m) == ck:
                    e["drop_s"].append((bs[0], m))
    for _k in ("drop_m", "drop_l", "drop_s"):          # a bus removed AND its load named
        _u = []
        for x in e[_k]:
            if (x[0], _ck(x[1])) not in [(y[0], _ck(y[1])) for y in _u]:
                _u.append(x)
        e[_k] = _u
    if unknown:
        return None, "not understood: %s" % unknown[0][:70]
    if not (e["trips"] or e["drop_m"] or e["drop_l"] or e["drop_s"]):
        return None, ("elements not in this case: %s" % missing[0][:70]) if missing \
            else "nothing tripped"
    if missing:
        e["notes"].append("not in case (not switched): " + " | ".join(m[:60] for m in missing))
    # ---- the TPL-001 category
    kinds = set(net.elem[k]["kind"] for k in e["trips"])
    if cat.startswith("P4"):
        e["ev"] = "P4.5" if e["rm_bus"] else ("P4.3" if kinds and kinds <= set(["xf2", "xf3"])
                                              else "P4.2")
        e["sub"] = "slg"
    elif cat.startswith("P1"):
        if e["trips"] and kinds <= set(["xf2", "xf3"]):
            e["ev"], e["sub"] = "P1.3", "transformer"
        elif e["trips"]:
            e["ev"], e["sub"] = "P1.2", "line"
        elif e["drop_s"] and not (e["drop_m"] or e["drop_l"]):
            e["ev"], e["sub"] = "P1.4", "shunt"
        elif e["drop_m"]:
            e["ev"], e["sub"] = "P1.1", "gen"
        else:
            e["ev"], e["sub"] = "P1.2", "line"
    else:
        e["sub"] = "line"
    return e, ""


def elem_buses(net, keys):
    s = set()
    for k in keys:
        s |= set(net.elem[k]["buses"])
    return s


def islands(net, cut, rm_bus=()):
    """Buses left without a path to a swing bus once `cut` elements and the
       `rm_bus` buses are out."""
    cut = set(cut)
    rm = set(rm_bus)
    start = elem_buses(net, cut) | set(o for b in rm for o in net.nbrs(b))
    start -= rm
    swing = set(net.swing)
    out, done = set(), set()
    for s in start:
        if s in done:
            continue
        seen, todo, grid = set([s]), [s], False
        while todo:
            b = todo.pop()
            if b in swing:
                grid = True
                break
            for o in net.nbrs(b, cut, rm):
                if o not in seen:
                    seen.add(o)
                    todo.append(o)
            if len(seen) > ISLAND_SEARCH_MAX:
                grid = True
                break
        done |= seen
        if not grid:
            out |= seen
    return out | rm


def plant_islanded(lev, e):
    isl = islands(lev.net, e["trips"] + e["pre"], e["rm_bus"])
    return all(b in isl for b in lev.plant_nodes), isl


# ---- tapped lines go out whole ---------------------------------------------
def pure_tap(net, b):
    ks = net.at.get(b, [])
    return (len(ks) == 2 and all(net.elem[k]["kind"] == "line" for k in ks)
            and not (net.mach.get(b) or net.load.get(b) or net.fsh.get(b) or net.ssh.get(b)))


def segment(net, key):
    """All the line pieces one breaker clears: through buses that are only a
       junction of two lines."""
    seg, ends = [key], []
    for b0 in net.elem[key]["buses"]:
        prev, b = key, b0
        guard = 0
        while pure_tap(net, b) and guard < 50:
            nxt = [k for k in net.at[b] if k != prev]
            if not nxt or nxt[0] in seg:
                break
            prev = nxt[0]
            seg.append(prev)
            b = [x for x in net.elem[prev]["buses"] if x != b][0]
            guard += 1
        ends.append(b)
    return seg, ends


def build_generated(lev, disis_kept):
    net = lev.net
    out = []
    p1_sets = [set(e["trips"]) for e in disis_kept if e["ev"].startswith("P1")]
    p4_bus = set(e["fbus"] for e in disis_kept if e["ev"].startswith("P4"))

    p1_all = set()
    for t in p1_sets:
        p1_all |= t

    def covered(trips):
        """SPP already has a P1 on this circuit -- on any piece of it (SPP
           may fault a tapped line piece by piece, or only the pieces within
           its own levels). SPP's definition of the circuit stands."""
        return bool(set(trips) & p1_all)

    def near_end(bs):
        return min(bs, key=lambda b: (lev.hop.get(b, 999), -net.kv.get(b, 0), b))

    def is_net(k):
        """A network element: not the plant, and not on a radial stub (a load
           or generation tap is not a network circuit)."""
        e = net.elem[k]
        if any(b in lev.plant for b in e["buses"]):
            return False
        return not any(b in lev.N and lev.st[b] in lev.radial for b in e["buses"])

    def finish(e):
        isl = islands(net, e["trips"] + e["pre"], e["rm_bus"])
        mm = [(b, m) for b in sorted(isl) if b not in lev.plant for m in net.mach.get(b, ())]
        for x in mm:
            if x not in e["drop_m"]:
                e["drop_m"].append(x)
        if mm and e["reclose"]:
            e["reclose"], e["wait"] = False, ""
            e["notes"].append("no reclose: it would island a generator")
        return e

    # ---- P1.2 every network circuit segment
    done = set()
    segs = []
    for k, el in sorted(net.elem.items()):
        if el["kind"] != "line" or k in done:
            continue
        if not (is_net(k) or (INCLUDE_RADIAL_P1 and not any(b in lev.plant
                                                              for b in el["buses"]))):
            continue
        if not all(b in lev.N for b in el["buses"]):
            continue
        sg, ends = segment(net, k)
        done |= set(sg)
        segs.append((sg, ends))
    # sub-transmission lines (SUBT_KV_MIN .. KV_MIN) close to the POI
    if SUBT_KV_MIN:
        for k, el in sorted(net.elem.items()):
            if el["kind"] != "line" or k in done:
                continue
            bs = el["buses"]
            if any(b in lev.plant for b in bs) or all(b in lev.N for b in bs):
                continue
            if min(net.kv.get(b, 0) for b in bs) < SUBT_KV_MIN:
                continue
            sg, ends = segment(net, k)
            done |= set(sg)
            if min(lev.bus_level(b) for b in ends) <= SUBT_P1_LEVEL:
                segs.append((sg, ends))
    if EVENTS.get("P1.2"):
        for sg, ends in segs:
            fb = near_end(ends)
            if lev.bus_level(fb) > lim("P1") or covered(sg):
                continue
            e = new_event("SCRIPT", "", "P1.2", "3PH", fb, clear_cycles(net.kv.get(fb)))
            e["trips"], e["sub"] = list(sg), "line"
            e["reclose"], e["wait"] = True, RECLOSE_WAIT
            out.append(finish(e))
    # ---- P1.3 every transformer
    if EVENTS.get("P1.3"):
        for k, el in sorted(net.elem.items()):
            if el["kind"] not in ("xf2", "xf3"):
                continue
            if not (is_net(k) or (INCLUDE_RADIAL_P1 and not any(b in lev.plant
                                                                for b in el["buses"]))):
                continue
            kvs = sorted([net.kv.get(b, 0) for b in el["buses"]], reverse=True)
            if kvs[0] < KV_MIN or kvs[1] < XFMR_LV_KV_MIN:
                continue
            hv = max(el["buses"], key=lambda b: net.kv.get(b, 0))
            if any(net.mach.get(b) for b in el["buses"] if b != hv and net.kv.get(b, 0) < KV_MIN):
                continue                          # generator step-up: a unit, not the network
            if lev.bus_level(hv) > lim("P1") or covered([k]):
                continue
            e = new_event("SCRIPT", "", "P1.3", "3PH", hv, clear_cycles(net.kv.get(hv)))
            e["trips"], e["sub"] = [k], "transformer"
            out.append(finish(e))

    # ---- what a breaker at bus b clears for each element there
    def groups_at(b):
        g = []
        for k in net.at.get(b, ()):
            if net.elem[k]["kind"] == "line":
                sg = segment(net, k)[0]
            else:
                sg = [k]
            if all(x not in sum(g, []) for x in sg):
                g.append(sg)
        return g

    def mva(sg):
        return max([abs(net.elem[k]["mva"]) for k in sg] or [0.0])

    # ---- P4 proxies where SPP has no P4 definition
    if EVENTS.get("P4"):
        for b in sorted(lev.N):
            if b in p4_bus or pure_tap(net, b) or lev.st[b] in lev.radial or b in lev.plant:
                continue
            grp = groups_at(b)
            if len(grp) < 2:
                continue
            netg = [g for g in grp if all(is_net(k) for k in g)]
            if lev.bus_level(b) > lim("P4"):
                continue
            fkv = net.kv.get(b, 0)
            whole = new_event("SCRIPT", "", "P4.5", "SLG", b, STUCK_CYCLES)
            whole["trips"] = [k for g in grp for k in g]
            whole["rm_bus"] = [b]
            whole["drop_m"] = [(b, m) for m in net.mach.get(b, ())]
            whole["drop_l"] = [(b, m) for m in net.load.get(b, ())]
            whole["drop_s"] = [(b, m) for m in net.fsh.get(b, []) + net.ssh.get(b, [])]
            cands = [whole]
            if (P4_PROXY or "proxy").lower() == "pairs":
                # every pair of elements at the bus -- one per breaker pair, the
                # shape SPP's consultants write (ring / breaker-and-a-half)
                pairs = [(netg[i], netg[j]) for i in range(len(netg))
                         for j in range(i + 1, len(netg))]
            else:
                top2 = sorted(netg, key=lambda g: -mva(g))[:2]
                pairs = [tuple(top2)] if len(top2) == 2 else []
            for g1, g2 in pairs:
                if len(grp) <= 2:
                    break                        # the pair IS the whole bus
                two = new_event("SCRIPT", "", "P4.2", "SLG", b, STUCK_CYCLES)
                two["trips"] = list(g1) + list(g2)
                if all(net.elem[k]["kind"] != "line" for k in two["trips"]):
                    two["ev"] = "P4.3"
                cands.append(two)
            for e in cands:
                e["sub"], e["retained_v"] = "slg", SLG_RETAIN_VPU
                e["primary"] = clear_cycles(fkv)
                e["gen_p4"] = True
                out.append(finish(e))
    # ---- P6 prior outage + 3PH on another element at the same bus
    if GENERATE_P6 and P6_MAX_PER_BUS > 0:
        for b in sorted(lev.N):
            if pure_tap(net, b) or lev.bus_level(b) > lim("P6") \
                    or lev.st[b] in lev.radial or b in lev.plant:
                continue
            grp = [g for g in groups_at(b) if all(is_net(k) for k in g)]
            if len(grp) < 2:
                continue
            # the study opens a prior outage with branch_chng_3 -- lines only --
            # so the element out beforehand is a line; the faulted one may be
            # a line or a transformer
            pairs = [(x, y) for x in grp for y in grp if x is not y
                     and all(net.elem[k]["kind"] == "line" for k in x)]
            pairs.sort(key=lambda xy: -(mva(xy[0]) + mva(xy[1])))
            for x, y in pairs[:P6_MAX_PER_BUS]:
                e = new_event("SCRIPT", "", "P6", "3PH", b, clear_cycles(net.kv.get(b)))
                e["pre"], e["trips"], e["sub"] = list(x), list(y), "prior_outage"
                if net.elem[y[0]]["kind"] == "line":
                    e["reclose"], e["wait"] = True, RECLOSE_WAIT
                out.append(finish(e))
    return out


# ============================================================================
# ONE LIST
# ============================================================================
def ev_class(ev):
    return "P1" if ev.startswith("P1") else ("P4" if ev.startswith("P4") else ev[:2])


def _pairs(xs):
    return tuple(sorted(set("%s-%s" % (int(b), _ck(i)) for b, i in xs)))


def dedupe_key(e):
    return (ev_class(e["ev"]), tuple(sorted(e["trips"])), tuple(sorted(e["pre"])),
            _pairs(e["drop_m"]), _pairs(e["drop_l"]), _pairs(e["drop_s"]))


def rank(lev, e):
    """Smaller = kept: SPP's own (DISIS) over the script's, then nearest the
       POI, then the longer clearing."""
    return (0 if e["src"] == "DISIS" else 1, e["level"], lev.hop.get(e["fbus"], 999),
            -float(e["cycles"] or 0), e["con_id"])


def enc_elem(net, k, near=None):
    """(from, to, kV, ckt) as the fault list writes it. A three-winding goes by
       its two highest-kV windings -- unless another element answers to that
       pair and circuit (twin transformers differing only in the tertiary), when
       the first winding pair that names THIS transformer alone is used, so the
       study opens the right one."""
    e = net.elem[k]
    bs = list(e["buses"])
    if e["kind"] == "xf3":
        w = sorted(bs, key=lambda b: -net.kv.get(b, 0))
        bs = w[:2]
        for x, y in ((w[0], w[1]), (w[0], w[2]), (w[1], w[2])):
            if net.study_resolves(x, y, e["ck"]) == k:
                bs = [x, y]
                break
    elif near in bs and bs[0] != near:
        bs = bs[::-1]
    kv = net.kv.get(bs[0], 0)
    return (bs[0], bs[1], kv, e["ck"])


def exec_check(net, e):
    """Everything the study will switch for this event, resolved the way the
       study resolves it. [] = runs as written."""
    bad = []
    if e["fbus"] not in net.kv:
        bad.append("fault bus %d not in case" % e["fbus"])
    for what, keys in (("trip", e["trips"]), ("prior outage", e["pre"])):
        for k in keys:
            a, b, _kv, ck = enc_elem(net, k, e["fbus"])
            got = net.study_resolves(a, b, ck)
            if got != k:
                bad.append("%s %s-%s ck%s opens %s, not %s" % (
                    what, a, b, ck, _desc(net, got) if got else "nothing", _desc(net, k)))
    for k in e["pre"]:
        if net.elem[k]["kind"] != "line":
            bad.append("prior outage %s is a transformer: the study opens prior outages "
                       "as lines only" % _desc(net, k))
    for lab, lst, have in (("unit", e["drop_m"], net.mach), ("load", e["drop_l"], net.load)):
        for b, i in lst:
            if _ck(i) not in [_ck(x) for x in have.get(b, [])]:
                bad.append("%s %s-%s not in case" % (lab, b, i))
    for b, i in e["drop_s"]:
        if _ck(i) not in [_ck(x) for x in list(net.fsh.get(b, [])) + list(net.ssh.get(b, []))]:
            bad.append("shunt %s-%s not in case" % (b, i))
    if not (e["trips"] or e["drop_m"] or e["drop_l"] or e["drop_s"]):
        bad.append("switches nothing")
    return bad


def enc_3w(net, keys):
    out = []
    for k in keys:
        e = net.elem[k]
        if e["kind"] == "xf3":
            out.append("%d-%d-%d-%s" % (e["buses"][0], e["buses"][1], e["buses"][2], e["ck"]))
    return ";".join(out)


def _els(tl):
    return ";".join("%s-%s-%s-%s" % (a, b, ("%.0f" % kv) if kv else "", ck) for a, b, kv, ck in tl)


# "source" FIRST (SCRIPT = built from the case, DISIS = SPP's sheet). Every
# reader in the study takes the columns by name, so the order is free.
HEADER = ["source", "fault_id", "fault_bus", "fault_kv", "fault_type", "clear_cycles", "trip_from",
          "trip_to", "trip_kv", "trip_ckt", "planning_event", "trip_elements", "pre_outage",
          "reclose", "reclose_wait", "no_fault", "retained_v", "primary_cycles", "gen_id",
          "hops_from_poi", "con_id", "subtype", "trip_3wind", "drop_machines", "drop_loads",
          "drop_shunts", "spp_level"]


def to_row(lev, e):
    net = lev.net
    tl = [enc_elem(net, k, e["fbus"]) for k in e["trips"]]
    first = tl[0] if tl else ("", "", 0, "1")
    fkv = net.kv.get(e["fbus"]) or e.get("fkv") or 0
    cyc = e["cycles"]
    cyc = int(cyc) if float(cyc) == int(float(cyc)) else cyc
    return {"fault_id": e["id"], "fault_bus": e["fbus"], "fault_kv": "%.0f" % fkv,
            "fault_type": e["type"], "clear_cycles": cyc,
            "trip_from": first[0], "trip_to": first[1],
            "trip_kv": ("%.0f" % first[2]) if first[2] else "", "trip_ckt": first[3],
            "planning_event": (P4_LABEL if P4_LABEL and ev_class(e["ev"]) == "P4" else e["ev"]),
            "trip_elements": _els(tl),
            "pre_outage": _els([enc_elem(net, k) for k in e["pre"]]),
            "reclose": "1" if e["reclose"] else "0",
            "reclose_wait": (("%g" % float(e["wait"])) if e["reclose"] and e["wait"] != "" else ""),
            "no_fault": "0", "retained_v": e["retained_v"], "primary_cycles": e["primary"],
            "gen_id": "", "hops_from_poi": lev.hop.get(e["fbus"], ""),
            "con_id": e["con_id"], "subtype": e["sub"],
            "trip_3wind": enc_3w(net, e["trips"]),
            "drop_machines": ";".join("%s-%s" % x for x in e["drop_m"]),
            "drop_loads": ";".join("%s-%s" % x for x in e["drop_l"]),
            "drop_shunts": ";".join("%s-%s" % x for x in e["drop_s"]),
            "spp_level": e["level"], "source": "DISIS" if e["src"] == "DISIS" else "SCRIPT"}


def phys_sig(r):
    """What the study's run marker checks, without the con_id (z6_spp_b _fault_row_sig)."""
    def _el(s):
        out = set()
        for x in (s or "").split(";"):
            b = [y.strip() for y in x.split("-")]
            if len(b) >= 2 and b[0].isdigit() and b[1].isdigit():
                a_, c_ = int(b[0]), int(b[1])
                out.add("%d-%d-%s" % (min(a_, c_), max(a_, c_),
                                      (b[3] if len(b) > 3 and b[3] else "1").upper()))
        return ";".join(sorted(out))

    def _tri(s):
        out = set()
        for x in (s or "").split(";"):
            b = [y.strip() for y in x.split("-")]
            if len(b) >= 3 and all(y.isdigit() for y in b[:3]):
                out.add("%s-%s" % ("-".join(str(y) for y in sorted(int(z) for z in b[:3])),
                                   (b[3] if len(b) > 3 and b[3] else "1").upper()))
        return ";".join(sorted(out))

    def _pr(s):
        return ";".join(sorted(x.strip().upper() for x in (s or "").split(";") if x.strip()))
    try:
        cyc = "%.2f" % float(r.get("clear_cycles") or 0)
    except ValueError:
        cyc = str(r.get("clear_cycles"))
    return "|".join([str(r.get("fault_bus") or "").strip(),
                     str(r.get("fault_type") or "3PH").strip().upper(), cyc,
                     _el(r.get("trip_elements")), _tri(r.get("trip_3wind")),
                     _pr(r.get("drop_machines")), _pr(r.get("drop_loads")),
                     _pr(r.get("drop_shunts")), _el(r.get("pre_outage"))])


# ============================================================================
# IMPACT SCREEN (P1): POI voltage during the fault, project share on the trips
# ============================================================================
def _ldl(n, adj, dg):
    """Sparse LDL' of a symmetric matrix (nodes 0..n-1, off-diagonals in adj,
       diagonal in dg), minimum-degree order. Returns (L, D, order, pos)."""
    import heapq
    hp = [(len(adj[v]), v) for v in range(n)]
    heapq.heapify(hp)
    alive = [True] * n
    L, D, order = {}, {}, []
    while hp:
        deg, v = heapq.heappop(hp)
        if not alive[v]:
            continue
        if deg != len(adj[v]):
            heapq.heappush(hp, (len(adj[v]), v))
            continue
        d = dg[v]
        if abs(d) < 1e-12:
            d = 1e-12
        row = adj[v]
        adj[v] = {}
        alive[v] = False
        items = list(row.items())
        for u, _a in items:
            del adj[u][v]
        for i, (u, au) in enumerate(items):
            lu = au / d
            dg[u] -= lu * au
            Au = adj[u]
            for w, aw in items[i + 1:]:
                t = lu * aw
                if w in Au:
                    Au[w] -= t
                    adj[w][u] -= t
                else:
                    Au[w] = -t
                    adj[w][u] = -t
        for u, _a in items:
            heapq.heappush(hp, (len(adj[u]), u))
        L[v] = dict((u, au / d) for u, au in items)
        D[v] = d
        order.append(v)
    pos = [0] * n
    for i, v in enumerate(order):
        pos[v] = i
    return L, D, order, pos


def _ldl_solve(F, b):
    L, D, order, _pos = F
    y = dict(b)
    for v in order:
        yv = y.get(v)
        if yv:
            for u, l in L[v].items():
                y[u] = y.get(u, 0) - l * yv
    x = {}
    for v in reversed(order):
        s = y.get(v, 0) / D[v]
        for u, l in L[v].items():
            xu = x.get(u)
            if xu:
                s -= l * xu
        if s:
            x[v] = s
    return x


def _ldl_diag(F, k):
    """(A^-1)[k, k] from one sparse forward pass: y = L^-1 e_k, sum y^2 / D."""
    import heapq
    L, D, order, pos = F
    y, hp, seen, s = {k: 1.0}, [pos[k]], set([k]), 0
    while hp:
        v = order[heapq.heappop(hp)]
        yv = y[v]
        s += yv * yv / D[v]
        for u, l in L[v].items():
            y[u] = y.get(u, 0) - l * yv
            if u not in seen:
                seen.add(u)
                heapq.heappush(hp, pos[u])
    return s


class Screen(object):
    """Factored once per case: the positive-sequence network (branches +
       machine source impedances) for fault voltages, and the DC network (X
       only, swing grounded) for the project's flow share."""
    ZMIN = 1e-4

    def __init__(self, net):
        t0 = time.time()
        self.net = net
        idx = {}
        for b in sorted(net.kv):
            idx[b] = len(idx)
        branches = []                    # (i, j, z) incl. 3W star legs
        self.legs = {}                   # element key -> [(i, j, z)]
        miss = 0
        for k, el in sorted(net.elem.items()):
            z = net.z.get(k)
            if el["kind"] == "xf3":
                if z is None:
                    miss += 1
                    z = (complex(0, 0.05),) * 3
                s = len(idx)
                idx[("S",) + k] = s
                lg = [(idx[b], s, zz) for b, zz in zip(el["buses"], z)]
            else:
                if z is None:
                    miss += 1
                    z = complex(0, 0.02 if el["kind"] == "line" else 0.1)
                lg = [(idx[el["buses"][0]], idx[el["buses"][1]], z)]
            self.legs[k] = lg
            branches.extend(lg)
        if miss:
            net.zdefault["branch impedance"] = net.zdefault.get("branch impedance", 0) + miss
        if miss > 0.10 * max(1, len(net.elem)):
            raise RuntimeError("%d of %d branch impedances not readable from the case"
                               % (miss, len(net.elem)))
        n = len(idx)
        self.idx = idx
        # ---- positive sequence Y
        adj = [dict() for _ in range(n)]
        dg = [complex(1e-6, 0)] * n
        for i, j, z in branches:
            if i == j:
                continue
            if abs(z) < self.ZMIN:
                z = complex(0, self.ZMIN)
            y = 1.0 / z
            adj[i][j] = adj[i].get(j, 0) - y
            adj[j][i] = adj[j].get(i, 0) - y
            dg[i] += y
            dg[j] += y
        for b, z in net.zsrc:
            if b in idx:
                dg[idx[b]] += 1.0 / (z if abs(z) >= self.ZMIN else complex(0, self.ZMIN))
        self.fy = _ldl(n, adj, dg)
        # ---- DC B (swing grounded)
        adj = [dict() for _ in range(n)]
        dg = [1e-6] * n
        for i, j, z in branches:
            if i == j:
                continue
            y = 1.0 / max(abs(z.imag), self.ZMIN)
            adj[i][j] = adj[i].get(j, 0) - y
            adj[j][i] = adj[j].get(i, 0) - y
            dg[i] += y
            dg[j] += y
        for b in net.swing:
            if b in idx:
                dg[idx[b]] += 1e6
        self.fb = _ldl(n, adj, dg)
        self._poi = None
        print("[screen] network factored: %d nodes in %.0f s" % (n, time.time() - t0))

    def set_poi(self, poi):
        if poi == self._poi:
            return
        p = self.idx[poi]
        self.zp = _ldl_solve(self.fy, {p: complex(1, 0)})    # Z[k, poi]
        self.th = _ldl_solve(self.fb, {p: 1.0})              # angles for 1 pu at the POI
        self._poi = poi
        self._vcache = {}

    def poi_v(self, bus):
        """|V| at the POI (pu) with a bolted 3PH fault at bus."""
        if bus == self._poi:
            return 0.0
        if bus not in self._vcache:
            k = self.idx.get(bus)
            if k is None:
                return 0.0
            zkk = _ldl_diag(self.fy, k)
            self._vcache[bus] = abs(1 - self.zp.get(k, 0) / zkk) if abs(zkk) > 0 else 1.0
        return self._vcache[bus]

    def share(self, keys):
        """Largest share of the project's MW on any element in keys (DC)."""
        best = 0.0
        for k in keys:
            for i, j, z in self.legs.get(k, ()):
                f = abs(self.th.get(i, 0) - self.th.get(j, 0)) / max(abs(z.imag), self.ZMIN)
                best = max(best, f)
        return best


def make_project(proj, net, disis, rep):
    pb = PROJECT_BUSES[proj]
    poi = int(pb["poi"])
    if poi not in net.kv:
        raise RuntimeError("%s: POI %d is not in the case" % (proj, poi))
    lev = Levels(net, poi, [int(x) for x in pb.get("feeders", [])])
    W = rep.append
    W("=" * 78)
    W("%s   POI %d %s %.0f kV" % (proj, poi, net.name.get(poi, ""), net.kv.get(poi, 0)))
    W("=" * 78)
    W("Faulted-bus level limits: DISIS P1 <= %d, P4 <= %d, P6 <= %d | case P1 <= %d, P4 <= %d, "
      "P6 <= %d; network >= %.0f kV; a level node has >= %d circuits."
      % (lim("P1", "DISIS"), lim("P4", "DISIS"), lim("P6", "DISIS"), lim("P1"), lim("P4"),
         lim("P6"), KV_MIN, HUB_MIN_CIRCUITS))
    W("POI substation is %s. Level 0: %s"
      % ("a level node" if lev.st[poi] in lev.l0 else "a tap (not a level node)",
         ", ".join("%d %s" % (s, net.name.get(s, "")) for s in sorted(lev.l0))))
    W("Plant (excluded from events, protected in P4): %d bus(es)" % len(lev.plant))
    for k in range(0, max(list(SPP_LEVELS_BY_EVENT.values()) + list(CASE_LEVELS_BY_EVENT.values())) + 1):
        hs = sorted(h for h in lev.hub if lev.L.get(h) == k)
        W("  level %d: %d substation(s)  %s" % (k, len(hs), ", ".join(
            "%d %s" % (h, net.name.get(h, "")) for h in hs[:14]) + (" ..." if len(hs) > 14 else "")))
    # ---- DISIS
    kept, drop = [], {}
    near_drops = []
    for d in disis:
        e, why = event_from_disis(net, d)
        if e is None:
            drop[why.split(":")[0]] = drop.get(why.split(":")[0], 0) + 1
            if d["id"] and _T_TOK.search(d["text"]):
                b = int(_T_TOK.search(d["text"]).group(1))
                if lev.bus_level(b) <= lim("P1", "DISIS"):
                    near_drops.append((d["id"], why))
            continue
        cls = ev_class(e["ev"])
        e["level"] = lev.bus_level(e["fbus"])
        if e["level"] > lim(cls, "DISIS"):
            _w = "%s faulted bus beyond level %d" % (cls, lim(cls, "DISIS"))
            drop[_w] = drop.get(_w, 0) + 1
            continue
        kept.append(e)
    W("")
    W("DISIS sheet: %d event(s) in the sheet, %d within the levels" % (len(disis), len(kept)))
    for why in sorted(drop, key=lambda k: -drop[k]):
        W("  not used %5d  %s" % (drop[why], why))
    for cid, why in near_drops:
        W("  NEAR THE POI, NOT USED: %-30s %s" % (cid, why))
    gen = build_generated(lev, kept)
    for e in gen:
        cls = ev_class(e["ev"])
        e["level"] = lev.bus_level(e["fbus"])
    allev = kept + gen
    # ---- no plant gen ties: an event faulted in the plant, or switching any
    #      plant element (its ties to the POI included), is not in the list
    tie_cut, keep = [], []
    for e in allev:
        pb = set([e["fbus"]]) | elem_buses(net, e["trips"] + e["pre"]) \
            | set(b for b, _m in e["drop_m"] + e["drop_l"] + e["drop_s"])
        if pb & lev.plant:
            tie_cut.append(e)
        else:
            keep.append(e)
    allev = keep
    # ---- P4 that cuts the plant off the grid
    p4_cut = []
    if P4_SKIP_IF_PLANT_ISLANDED:
        keep = []
        for e in allev:
            if ev_class(e["ev"]) == "P4" and plant_islanded(lev, e)[0]:
                p4_cut.append(e)
                continue
            keep.append(e)
        allev = keep
    # ---- duplicates: the same elements out, kept once (nearest the POI)
    best, dups = {}, []
    for e in allev:
        k = dedupe_key(e)
        if k not in best or rank(lev, e) < rank(lev, best[k]):
            if k in best:
                dups.append((best[k], e))
            best[k] = e
        else:
            dups.append((e, best[k]))
    # ---- the same stuck breaker written twice: same bus, same elements out,
    #      same clearing -- only the units / loads dropped differ. The one
    #      that drops the most is kept (a unit on a cleared bus trips anyway).
    pool = list(best.values())
    same_brk = []
    grp = {}
    for e in pool:
        if ev_class(e["ev"]) == "P4":
            k = (e["fbus"], tuple(sorted(set(e["trips"]))), float(e["cycles"] or 0))
            grp.setdefault(k, []).append(e)
    cut_ids = set()
    for k, es in grp.items():
        if len(es) > 1:
            es.sort(key=lambda e: (-(len(e["drop_m"]) + len(e["drop_l"]) + len(e["drop_s"])),
                                   0 if e["src"] == "DISIS" else 1, e["con_id"]))
            for e in es[1:]:
                same_brk.append((e, es[0]))
                cut_ids.add(id(e))
    pool = [e for e in pool if id(e) not in cut_ids]
    # ---- P4 per bus as SPP's reports: the few most severe at each bus
    p4_trim = []
    if P4_MAX_PER_BUS:
        byb = {}
        for e in pool:
            if ev_class(e["ev"]) == "P4":
                byb.setdefault(e["fbus"], []).append(e)
        for b, es in byb.items():
            es.sort(key=lambda e: (0 if e["src"] == "DISIS" else 1, -len(e["trips"]),
                                   -sum(abs(net.elem[k]["mva"]) for k in e["trips"] if k in net.elem),
                                   e["con_id"], str(dedupe_key(e))))
            p4_trim.extend(es[P4_MAX_PER_BUS:])
        cut_ids = set(id(e) for e in p4_trim)
        pool = [e for e in pool if id(e) not in cut_ids]
    if P4_MAX_TOTAL:
        p4s = sorted([e for e in pool if ev_class(e["ev"]) == "P4"], key=lambda e: (
            e["level"], lev.hop.get(e["fbus"], 999), 0 if e["src"] == "DISIS" else 1,
            -net.kv.get(e["fbus"], 0), e["fbus"], -len(e["trips"]), e["con_id"], str(dedupe_key(e))))
        extra = p4s[P4_MAX_TOTAL:]
        p4_trim.extend(extra)
        cut_ids = set(id(e) for e in extra)
        pool = [e for e in pool if id(e) not in cut_ids]
    # ---- impact screen: a remote P1 the project cannot see is left out
    screened, scr_note, n_cand = [], "", 0
    if IMPACT_SCREEN:
        try:
            if net._screen is None:
                net._screen = Screen(net)
            sc = net._screen
            sc.set_poi(poi)
            keep = []
            for e in pool:
                if ev_class(e["ev"]) == "P1" and e["level"] >= SCREEN_FROM_LEVEL \
                        and not e["drop_m"]:
                    n_cand += 1
                    e["scr"] = (sc.poi_v(e["fbus"]), sc.share(e["trips"]))
                    if e["scr"][0] >= SCREEN_POI_V_PU and e["scr"][1] < SCREEN_DF_MIN:
                        screened.append(e)
                        continue
                keep.append(e)
            pool = keep
        except Exception as ex:
            scr_note = "NOT APPLIED -- %s: %s (every event kept)" % (type(ex).__name__, ex)
            print("[screen] %s: %s" % (proj, scr_note))
    # ---- cap: level by level outward; the level that overflows is filled
    #      SPP's own (DISIS) first, then nearest the POI, then highest kV
    capped = []
    if MAX_EVENTS and len(pool) > MAX_EVENTS:
        pool.sort(key=lambda e: (e["level"], 0 if e["src"] == "DISIS" else 1,
                                 lev.hop.get(e["fbus"], 999), -lev.net.kv.get(e["fbus"], 0),
                                 {"P1": 0, "P4": 1, "P6": 2}.get(ev_class(e["ev"]), 3),
                                 e["fbus"], e["con_id"], str(dedupe_key(e))))
        pool, capped = pool[:MAX_EVENTS], pool[MAX_EVENTS:]
    # every P1 first, nearest the POI first; then every P4; then P6
    final = sorted(pool, key=lambda e: (
        {"P1": 0, "P4": 1, "P6": 2}.get(ev_class(e["ev"]), 3),
        lev.hop.get(e["fbus"], 999), e["level"], -lev.net.kv.get(e["fbus"], 0), e["fbus"], e["ev"],
        0 if e["src"] == "DISIS" else 1, e["con_id"], str(dedupe_key(e))))
    # ---- ids
    old_rows = []
    oldp = _shared_csv(proj)
    if KEEP_OLD_IDS and os.path.isfile(oldp):
        with open(oldp) as fh:
            old_rows = list(csv.DictReader(fh))
    old_by = {}
    for r in old_rows:
        old_by.setdefault(phys_sig(r), r)
    used = set(r.get("fault_id") for r in old_rows)
    nxt = 1 + max([int(m.group(1)) for m in (re.match(r"^F(\d+)$", r.get("fault_id") or "")
                                              for r in old_rows) if m] or [0])
    xbad = []
    for e in final:
        b_ = exec_check(net, e)
        if b_:
            xbad.append((e, b_))
            e["notes"].append("CHECK: " + "; ".join(b_))
    rows, reused = [], 0
    for i, e in enumerate(final, 1):
        e["id"] = "F%02d" % i
        r = to_row(lev, e)
        o = old_by.pop(phys_sig(r), None) if KEEP_OLD_IDS else None
        if o is not None:
            # identical to an event already run: its F-number and con_id, so
            # the study's run marker still matches and it is not run again
            r["fault_id"], r["con_id"] = o["fault_id"], o["con_id"]
            reused += 1
        elif KEEP_OLD_IDS:
            while ("F%02d" % nxt) in used:
                nxt += 1
            r["fault_id"] = "F%02d" % nxt
            used.add(r["fault_id"])
            nxt += 1
        if not r["con_id"]:
            r["con_id"] = "SCRIPT_%s" % r["fault_id"]      # generated: named by its F-number
        e["id"], e["con_id"] = r["fault_id"], r["con_id"]
        rows.append(r)
    # ---- report
    cnt = {}
    for e in final:
        k = (e["ev"], e["src"])
        cnt[k] = cnt.get(k, 0) + 1
    W("")
    W("FINAL LIST: %d event(s)%s" % (len(final), " (capped at MAX_EVENTS; %d left out, listed "
                                              "below)" % len(capped) if capped else ""))
    for k in sorted(cnt):
        W("  %-5s %-6s %4d" % (k[0], k[1], cnt[k]))
    if KEEP_OLD_IDS:
        W("  %d event(s) identical to the old list keep their F-number (their runs are used)"
          % reused)
    W("")
    odd = [e for e in final if ev_class(e["ev"]) == "P4" and float(e["cycles"] or 0) != STUCK_CYCLES]
    if odd:
        W("P4 CLEARING OTHER THAN %d CYCLES -- as SPP's sheet writes it (a utility's own breaker-"
          "failure time), kept:" % STUCK_CYCLES)
        for e in odd:
            W("  %-30s bus %-7d %s cycles" % (e["con_id"], e["fbus"], e["cycles"]))
        W("")
    W("EXECUTION CHECK -- every element resolved the way the study opens it: %s"
      % ("all %d event(s) OK" % len(final) if not xbad else "%d event(s) NEED A LOOK" % len(xbad)))
    for e, b_ in xbad:
        W("  %-6s %-30s bus %-7d %s" % (e["id"], e["con_id"], e["fbus"], "; ".join(b_)))
    W("")
    W("NOT USED -- faulted in the plant or switching a plant gen tie (%d):" % len(tie_cut))
    for e in tie_cut:
        W("  %-6s %-5s %-30s bus %-7d %s" % (e["src"], e["ev"], e["con_id"] or "(generated)",
                                            e["fbus"], ", ".join(_desc(net, k) for k in e["trips"][:4])))
    W("")
    W("P4 NOT USED -- the plant would have no path to the grid (%d):" % len(p4_cut))
    for e in p4_cut:
        W("  %-6s %-30s bus %-7d %s" % (e["src"], e["con_id"] or "(generated)", e["fbus"],
                                       ", ".join(_desc(net, k) for k in e["trips"][:4])))
    W("")
    if same_brk:
        W("P4 SAME BREAKER TWICE (%d) -- same bus, elements and clearing; the one dropping the "
          "most units / loads kept:" % len(same_brk))
        for lost, won in same_brk:
            W("  %-6s %-30s @%-7d -> kept %-30s" % (lost["src"], lost["con_id"] or "(generated)",
                                                  lost["fbus"], won["con_id"] or "(generated)"))
        W("")
    if p4_trim:
        W("P4 OVER P4_MAX_PER_BUS = %s at a bus / P4_MAX_TOTAL = %s (%d left out; at each bus "
          "the most severe kept, then the nearest the POI):"
          % (P4_MAX_PER_BUS, P4_MAX_TOTAL, len(p4_trim)))
        for e in sorted(p4_trim, key=lambda e: (e["level"], e["fbus"])):
            W("  %-6s %-5s L%d %-30s bus %-7d %d element(s)"
              % (e["src"], e["ev"], e["level"], e["con_id"] or "(generated)", e["fbus"],
                 len(e["trips"])))
        W("")
    if IMPACT_SCREEN:
        W("IMPACT SCREEN (P1 faulted at level >= %d; P4 not screened): %s"
          % (SCREEN_FROM_LEVEL, scr_note or "%d of %d left out -- POI stays >= %.2f pu during "
             "the fault AND no tripped element carries >= %.0f %% of the project's MW"
             % (len(screened), n_cand, SCREEN_POI_V_PU, 100 * SCREEN_DF_MIN)))
        if net.zdefault:
            W("  not in the case, a typical value used: %s" % ", ".join(
                "%s x%d" % (k, v) for k, v in sorted(net.zdefault.items())))
        for e in sorted(screened, key=lambda e: (e["level"], -e["scr"][0], e["fbus"])):
            W("  %-6s %-5s L%d %-30s bus %-7d POI %.2f pu  share %4.1f %%  %s"
              % (e["src"], e["ev"], e["level"], e["con_id"] or "(generated)", e["fbus"],
                 e["scr"][0], 100 * e["scr"][1], ", ".join(_desc(net, k) for k in e["trips"][:3])))
        W("")
    if capped:
        cut = min(c["level"] for c in capped)
        W("OVER MAX_EVENTS = %d -- %d event(s) left out, farthest first to go (levels 0..%d "
          "complete; level %d partly kept):" % (MAX_EVENTS, len(capped), cut - 1, cut))
        for e in capped:
            W("  %-6s %-5s L%d %-30s bus %-7d %s"
              % (e["src"], e["ev"], e["level"], e["con_id"] or "(generated)", e["fbus"],
                 ", ".join(_desc(net, k) for k in e["trips"][:4])))
        W("")
    W("DUPLICATES REMOVED (%d) -- same elements out; DISIS kept over script, then the one "
      "nearer the POI:" % len(dups))
    for lost, won in dups:
        W("  %-6s %-30s @%-7d -> kept %-6s %-30s @%d"
          % (lost["src"], lost["con_id"] or "(generated %s)" % lost["ev"], lost["fbus"],
             won["src"], won["con_id"] or "(generated %s)" % won["ev"], won["fbus"]))
    W("")
    W("%-5s %-6s %-5s %-4s %-5s %-30s %-8s %s" % ("id", "event", "level", "node", "src",
                                                  "con_id", "bus", "removes"))
    for r, e in zip(rows, final):
        W("%-5s %-6s %-5s %-4s %-5s %-30s %-8s %s%s%s"
          % (r["fault_id"], e["ev"], e["level"], r["hops_from_poi"], r["source"][:5],
             r["con_id"][:30], e["fbus"],
             ", ".join(_desc(net, k) for k in e["trips"]) or "-",
             ("  | prior out: " + ", ".join(_desc(net, k) for k in e["pre"])) if e["pre"] else "",
             ("  | " + "; ".join(e["notes"])) if e["notes"] else ""))
    return lev, rows


def _desc(net, k):
    e = net.elem[k]
    return "%s ck%s" % ("-".join(str(b) for b in e["buses"]), e["ck"])


def write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def write_levels(path, lev):
    net = lev.net
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bus", "name", "kv", "substation", "level_node", "radial", "level",
                    "nodes_from_poi"])
        for b in sorted(lev.N, key=lambda b: (lev.bus_level(b), lev.hop.get(b, 999), b)):
            if lev.bus_level(b) > max(list(SPP_LEVELS_BY_EVENT.values()) + list(CASE_LEVELS_BY_EVENT.values())) + 1:
                continue
            s = lev.st[b]
            w.writerow([b, net.name.get(b, ""), "%.1f" % net.kv.get(b, 0), s,
                        1 if s in lev.hub else 0, 1 if s in lev.radial else 0,
                        lev.bus_level(b), lev.hop.get(b, "")])


def main():
    t0 = time.time()
    sheet = _abs(DISIS_SHEET)
    if not os.path.isfile(sheet):
        print("[faults] DISIS sheet not found: %s" % sheet)
        return 2
    disis = read_disis(sheet)
    print("[faults] %s: %d distinct event(s)" % (os.path.basename(sheet), len(disis)))
    out = _abs(OUT_DIR)
    if not os.path.isdir(out):
        os.makedirs(out)
    nets = {}
    rc = 0
    for proj in PROJECTS:
        if proj not in PROJECT_BUSES:
            print("[faults] %s: no POI in PROJECT_BUSES -- skipped" % proj)
            rc = 1
            continue
        case = _case_for(proj)
        try:
            if case not in nets:
                if not os.path.isfile(case):
                    raise RuntimeError("case not found: %s" % case)
                nets[case] = load_net(case)
            rep = ["SPP BPM fault list -- %s" % time.strftime("%Y-%m-%d %H:%M"),
                   "case  : %s" % case, "DISIS : %s" % sheet, ""]
            lev, rows = make_project(proj, nets[case], disis, rep)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[faults] %s FAILED: %s" % (proj, e))
            rc = 1
            continue
        p_csv = os.path.join(out, "SPP_FAULTS_CON_%s.csv" % proj)
        write_csv(p_csv, rows)
        write_levels(os.path.join(out, "FAULT_LIST_LEVELS_%s.csv" % proj), lev)
        with open(os.path.join(out, "FAULT_LIST_BPM_%s.txt" % proj), "w") as fh:
            fh.write("\n".join(rep) + "\n")
        print("[faults] %-14s %4d event(s) -> %s" % (proj, len(rows), p_csv))
        if INSTALL and os.path.normcase(os.path.abspath(_shared_csv(proj))) \
                == os.path.normcase(os.path.abspath(p_csv)):
            print("[faults]   the study already reads this file (z6_main SHARED_FAULTS_CSV)")
        elif INSTALL:
            dst = _shared_csv(proj)
            if os.path.isfile(dst):
                shutil.copy2(dst, dst + ".bak_%s" % time.strftime("%Y%m%d_%H%M%S"))
            shutil.copy2(p_csv, dst)
            print("[faults]   installed -> %s" % dst)
    print("[faults] done in %.0f s" % (time.time() - t0))
    return rc


if __name__ == "__main__":
    sys.exit(main())
