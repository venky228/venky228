# -*- coding: utf-8 -*-
"""z7_why_unstable.py -- WHY DID A RUN CRASH, OR LOSE ITS SOLUTION?

For every fault of the base case, Scenario 1 and Scenario 2 it finds the runs
that went wrong and says why, as far as the study's own files can tell:

  CRASHED          the run stopped and has no result (no .done; the launcher
                   gave up after its attempts). Read: every attempt's run log
                   (logs\\RUN_<fault>_w<k>.log) -- the last step it finished and
                   the simulation time it reached --, the exit code the launcher
                   recorded for that worker (logs\\WORKER_EXIT_w<k>.txt: STACK
                   OVERFLOW, ACCESS VIOLATION ...), the PSS/E output of the
                   fault window (logs\\psse\\<fault>.txt), the init log
                   (logs\\FAULT_<fault>_strt-prog.txt) and the partial .out.
  STILL RUNNING    no result yet, but its .out or run log was written in the
                   last RUNNING_MIN minutes: not a crash. A run whose network
                   solution fails at every step crawls -- check again later.
  OFF-SCALE        a bus voltage above 5 pu ("Bus voltages within scale" FAIL).
  SOLUTION LOST    "System stability" FAIL: the run reached the end, but part
                   of the network lost its solution (a voltage off-scale, an
                   angle turning through more than 720 deg, or a divergence).

  NOT CONVERGED    the run FINISHED and was scored, but PSS/E's network solution
                   was still failing NONCONV_AFTER_S or more after the last
                   switching (SCAN_NONCONV): the values in that window are not a
                   solution, so the verdict of that run cannot be trusted.

For every one of them it then says WHERE IT STARTS and WHY, as a root cause:
the bus where the voltage runs away worst (PSS/E's own "outside of band"
lines, the study's off-scale list), how many buses that is from the faulted
bus, whether its ONLY path to the system runs through the faulted bus (a plant
radial on the faulted bus sees zero volts for the whole fault), which machines
sit there and which dynamic models they use (from the .dyr files), whether a
VSC / DC converter is involved, whether it starts at the reclose, which of the
cases fail and which run (the base case failing too = not caused by the SGF;
running with the EGF off = needs the SGF and the EGF together), and whether a
model PSS/E could not load ("NOT ACCESSIBLE" in the init log) sits nearby --
and WHAT TO TRY to confirm it (a smaller time step, the solver retry ladder,
switching the plant off as a test, running without the reclose).

For every run it also reads: which channels the study names (parts\\SCEN_<id>.csv),
whether the fault's own trips cut those buses off from the system (the fault
list + flags\\BUS_MAP.csv, the same test the study uses, and PSS/E's own
"following buses are disconnected"), what PSS/E reported in the fault window
(islands, out-of-step, network not converged, machine trips, voltages out of
band), and -- with READ_OUT -- WHEN each voltage ran away or each angle started
to turn, from the .out itself.

Every fault it looks at is also shown in the other cases, so a run that blows
up in one case only can be compared with the same fault where it did not.

Nothing is simulated and nothing in the results folders is changed: the files
are only read.

RUN (Python 3.4 or later, from the study folder that holds Base\\ and Projects\\):
    python z7_why_unstable.py                 every crashed or unstable run it finds
    python z7_why_unstable.py F124 F148       only these faults (in every case)
    python z7_why_unstable.py --quick         logs and score files only, no .out read (seconds)
    python z7_why_unstable.py --noscan        without the scan of the finished runs for non-convergence
READ_OUT = True reads the .out files with PSS/E's dyntools (the PSS/E Python
folders are looked for in the usual places, or set PSSE_DIRS) -- only the first
part of each file, up to OUT_WINDOW_S after the last switching, which is where a
blow-up shows; a crashed run's partial .out is never read. If the .out cannot be
read the rest of the report is still written.

The report opens with the MODELS PSS/E COULD NOT LOAD in each project (the
"NOT ACCESSIBLE" lines of the init logs), with the buses each one belongs to --
those models do not run in ANY fault.

Output: <ROOT>\\unstable_reasons\\WHY_UNSTABLE.txt and WHY_UNSTABLE.csv
"""
import os
import re
import sys
import csv
import glob
import time
import shutil
import tempfile
import datetime
import collections

# ============================================================================
#   SETTINGS
# ============================================================================
ROOT = ""                      # the study folder holding Base\ and Projects\ ("" = this file's folder)
PROJECTS = ["SantaFe", "IronStar", "EmpirePrairie", "EastFork"]   # [] = every project found
MODE = "spp"
# The runs: (label, results folder under ROOT, run-folder suffix), as in z7_trip_why.py.
CASES = [
    ("Base case",                    r"Base\results_base_f",     ""),
    ("Scenario 1 (SGF + EGF, GIA)",  r"Projects\results_proj_f", ""),
    ("Scenario 2 (SGF on, EGF off)", r"Projects\results_proj_f", "_s1_egfoff"),
    ("Scenario 1 at 40/60 (SGF 40 %, EGF 60 %)", r"Projects\results_proj_f", "_s1_sgf40"),   # skipped if not there
]
FAULTS = []                    # [] = every crashed / off-scale / solution-lost run found | e.g. ["F124", "F10-F20"]
COMPARE_OTHER_CASES = True     # show each fault in the other cases too
COMPARE_READ_OUT = False       # ...and read their .out files too (slower)
FAULT_LIST = r"FAULT_LISTS_BPM\SPP_FAULTS_CON_{project}.csv"   # under ROOT; the run's faults\SPP_FAULTS.csv is the fallback
READ_OUT = False               # True = also read the .out files (whole files -- dyntools cannot read part of one -- slow);
                               #   the PSS/E fault-window log already gives the times (bands, islands, non-convergence)
OUT_WINDOW_S = 4.0             # with READ_OUT: the timing is looked for up to this long after the last switching
ANGLE_WINDOW_S = 6.0           # ...and this long for a run flagged for a turning angle
READ_WORKERS = 4               # .out files read at the same time, one process each (~300 MB of memory each); 1 = one by one
PSSE_DIRS = []                 # [] = C:\Program Files (x86)\PTI\PSSE34 ... looked for | or e.g. [r"C:\Program Files (x86)\PTI\PSSE34"]
OFFSCALE_PU = 5.0              # the study's "within scale" limit
RUNAWAY_PU = 1.5               # "starts running away": the first time a voltage passes this after the fault
ANGLE_SPAN_DEG = 720.0         # the study's "lost its solution" angle span
ISLAND_MAX_BUSES = 40          # a pocket larger than this counts as still connected (the study's value)
# AC buses where a DC converter connects. The bus map holds AC branches only, so a DC
# tie is invisible to the island test: an island holding one of these buses is fed
# ONLY through that DC link. {bus: "what it is"}
DC_TERMINALS = {
    599950: "the Lamar back-to-back DC tie SPP_43_LAMAR (599950 LAMAR7 - 599951 LAMAR 6, area 999 WECC)",
}
EXIT_MATCH_MIN = 45            # a worker exit this many minutes after an attempt's last log line still belongs to it
RUNNING_MIN = 10               # no result, but the .out / run log written this recently = STILL RUNNING, not crashed
SCAN_NONCONV = True            # also every run that FINISHED but whose network solution was still failing ...
NONCONV_AFTER_S = 1.0          # ... this long (s) or more after the last switching (clearing / reclose) -> NOT CONVERGED
DYR_FILES = []                 # [] = every .dyr directly in Base\ and Projects\ | or paths (under ROOT or full)
DYR_INCLUDE_LOADED = False     # True = also the LOADED_*.dyr dumps the builds leave beside them
PLANT_HOPS = 3                 # the machines this close to where the voltage runs away are named, with their models
OUT_DIR = "unstable_reasons"   # under ROOT unless a full path
# ============================================================================

HERE = os.path.dirname(os.path.abspath(__file__))
BAR = "=" * 78
SUB = "-" * 78

# Windows exit codes a PSS/E worker dies with (NTSTATUS), for an exit the launcher did not name.
EXIT_CODES = {
    3221225477: "0xC0000005 ACCESS VIOLATION -- a model or PSS/E read or wrote memory it does not own",
    3221225725: "0xC00000FD STACK OVERFLOW -- runaway recursion inside a model",
    3221226505: "0xC0000409 STACK BUFFER OVERRUN / fail-fast -- the process stopped itself",
    3221226356: "0xC0000374 HEAP CORRUPTION",
    3221225495: "0xC0000017 OUT OF MEMORY",
    3221225620: "0xC0000094 INTEGER DIVIDE BY ZERO in a model",
    3221225614: "0xC000008E FLOATING-POINT DIVIDE BY ZERO in a model",
    3221225616: "0xC0000090 FLOATING-POINT INVALID OPERATION in a model",
    3221225617: "0xC0000091 FLOATING-POINT OVERFLOW in a model",
    3221225786: "0xC000013A STOPPED (Ctrl+C / window closed)",
    1: "rc=1 -- a Python error in the worker (its run log ends with the traceback)",
}


# ---------------------------------------------------------------- helpers --
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
    m = re.match(r"^([A-Za-z_]*)(\d+)(.*)$", str(fid))
    if m:
        return (m.group(1).upper(), int(m.group(2)), m.group(3))
    return (str(fid).upper(), -1, "")


def _num(t):
    try:
        v = float(t)
    except Exception:
        return None
    return None if v != v else v


def _read(p, limit=None):
    try:
        with open(p, "r", errors="replace") as fh:
            return fh.read(limit) if limit else fh.read()
    except Exception:
        return None


def _read_csv(p):
    try:
        with open(p, "r", errors="replace") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _mb(n):
    if n is None:
        return "?"
    return ("%.1f MB" % (n / 1048576.0)) if n >= 1048576 else ("%.0f KB" % (n / 1024.0))


def _short(s, n=150):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[:n - 3] + "..."


def _buses_in(text):
    """Bus numbers named in a label or a line (4 to 7 digits)."""
    return [int(x) for x in re.findall(r"(?<![\d.])(\d{4,7})(?![\d.])", str(text or ""))]


def _want_faults():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    spec = args or list(FAULTS or [])
    out = []
    for s in spec:
        s = str(s).strip().upper()
        m = re.match(r"^F(\d+)\s*-\s*F?(\d+)$", s)
        if m:
            out += ["F%d" % i for i in range(int(m.group(1)), int(m.group(2)) + 1)]
        elif s:
            out.append(s if s.startswith("F") or not s.isdigit() else "F" + s)
    return set(out)


# ---------------------------------------------------------------- folders --
def run_folders():
    """[(project, case label, folder)] for every project and case that exists."""
    projs = list(PROJECTS or [])
    if not projs:
        seen = set()
        for _lab, res, _suf in CASES:
            d = _abs(res)
            if os.path.isdir(d):
                for nm in os.listdir(d):
                    if os.path.isdir(os.path.join(d, nm)) and not nm.startswith(("_", ".")):
                        seen.add(nm)
        projs = sorted(seen)
    out = []
    for proj in projs:
        for lab, res, suf in CASES:
            name = "%s_%s%s" % (proj, MODE, suf)
            for cand in (os.path.join(_abs(res), proj, name), os.path.join(_abs(res), name)):
                if os.path.isdir(cand):
                    out.append((proj, lab, cand))
                    break
    return out


# ---------------------------------------------------------- study's files --
def read_scen(rdir, fid):
    """The per-scenario score: {'verdict', 'crit': {criterion: (result, detail)}} or None."""
    p = os.path.join(rdir, "parts", "SCEN_%s.csv" % fid)
    if not os.path.isfile(p):
        return None
    out = {"verdict": None, "crit": collections.OrderedDict(), "path": p}
    try:
        with open(p, "r", errors="replace") as fh:
            for r in csv.reader(fh):
                if not r:
                    continue
                if r[0] == "verdict" and len(r) > 2:
                    out["verdict"] = r[2]
                elif r[0] == "crit" and len(r) > 4:
                    out["crit"][r[2]] = (r[3], r[4])
    except Exception:
        return None
    return out


def _crit(scen, prefix):
    if not scen:
        return None
    for k, v in scen["crit"].items():
        if k.startswith(prefix):
            return v
    return None


def offscale_items(detail):
    """'... : 531601 94.1 pu, 531600 12.0 pu ...' -> [(label, peak)]."""
    tail = str(detail or "").split(":", 1)[-1]
    return [(m.group(1).strip(), float(m.group(2)))
            for m in re.finditer(r"([^,:]+?)\s+(\d+(?:\.\d+)?)\s*pu", tail)]


def stability_items(detail):
    """The reasons after 'LOST ITS SOLUTION:' (or the whole detail), one per entry."""
    d = str(detail or "")
    if "LOST ITS SOLUTION:" in d:
        d = d.split("LOST ITS SOLUTION:", 1)[1]
    return [x.strip() for x in re.split(r";\s+", d) if x.strip()]


def run_state(rdir, fid):
    od = os.path.join(rdir, "outs")
    q = os.path.join(od, fid + ".out")
    st = {"out": q if os.path.isfile(q) else None,
          "out_size": os.path.getsize(q) if os.path.isfile(q) else 0,
          "done": os.path.isfile(os.path.join(od, fid + ".done")),
          "partial": os.path.isfile(os.path.join(od, fid + ".partial")),
          "attempts": None, "t_clear": None, "t_end": None,
          "diverged": _read(os.path.join(od, fid + ".diverged"))}
    a = _read(os.path.join(od, fid + ".attempts"))
    if a is not None:
        st["attempts"] = int(_num(a.strip().split()[0]) or 0) if a.strip() else 0
    d = _read(os.path.join(od, fid + ".done")) or _read(os.path.join(od, fid + ".partial")) or ""
    if d:
        st["t_clear"] = _num(d.split("|")[0].strip())
        m = re.search(r"tend\s*=\s*([\d.]+)", d)
        st["t_end"] = _num(m.group(1)) if m else None
    return st


_FULL_SIZE = {}


def typical_out(rdir):
    """(median size of the finished .out files, their run length) -- to put a partial .out in time."""
    if rdir in _FULL_SIZE:
        return _FULL_SIZE[rdir]
    sizes, tend = [], None
    od = os.path.join(rdir, "outs")
    for dn in glob.glob(os.path.join(od, "*.done"))[:400]:
        q = dn[:-5] + ".out"
        if os.path.isfile(q):
            sizes.append(os.path.getsize(q))
            if tend is None:
                m = re.search(r"tend\s*=\s*([\d.]+)", _read(dn) or "")
                tend = _num(m.group(1)) if m else None
    sizes.sort()
    _FULL_SIZE[rdir] = ((sizes[len(sizes) // 2] if sizes else None), tend)
    return _FULL_SIZE[rdir]


# ------------------------------------------------------------ fault list --
_FLISTS = {}


def fault_list(proj, rdir):
    key = (proj, rdir)
    if key in _FLISTS:
        return _FLISTS[key]
    cands = []
    if FAULT_LIST:
        for base in ("", "Base", "Projects"):
            cands.append(_abs(os.path.join(base, FAULT_LIST.replace("{project}", proj))))
    cands.append(os.path.join(rdir, "faults", "SPP_FAULTS.csv"))
    out = {}
    for p in cands:
        rows = _read_csv(p) if os.path.isfile(p) else []
        for r in rows:
            f = (r.get("fault_id") or "").strip().upper()
            if f and f not in out:
                out[f] = r
        if out:
            break
    _FLISTS[key] = out
    return out


def fault_text(r):
    if not r:
        return "(not in the fault list)"
    s = "%s %s at bus %s (%s kV), cleared after %s cycles" % (
        (r.get("planning_event") or "").strip(), (r.get("fault_type") or "").strip(),
        (r.get("fault_bus") or "").split(".")[0], (r.get("fault_kv") or "").strip(),
        (r.get("clear_cycles") or "").strip())
    if (r.get("trip_elements") or "").strip():
        s += "; trips %s" % r["trip_elements"].strip()
    if (r.get("trip_3wind") or "").strip():
        s += "; 3-winding %s" % r["trip_3wind"].strip()
    if (r.get("drop_machines") or "").strip():
        s += "; drops machine(s) %s" % r["drop_machines"].strip()
    if (r.get("reclose") or "").strip() in ("1", "True", "true", "Y", "y"):
        s += "; recloses after %s cycles" % ((r.get("reclose_wait") or "").strip() or "?")
    return s


def fault_cuts(r):
    """The bus pairs the fault's own switching opens."""
    cut = set()
    if not r:
        return cut
    for el in re.split(r"[;|]", (r.get("trip_elements") or "") + ";" + (r.get("pre_outage") or "")):
        b = _buses_in(el.replace("-", " "))
        if len(b) >= 2:
            cut.add((b[0], b[1]))
            cut.add((b[1], b[0]))
    for el in re.split(r"[;|]", r.get("trip_3wind") or ""):
        b = _buses_in(el.replace("-", " "))[:3]
        for i in range(len(b)):
            for j in range(len(b)):
                if i != j:
                    cut.add((b[i], b[j]))
    return cut


def event_times(r, st, run_info):
    """{'fault': t, 'clear': t, 'reclose': t or None}."""
    ev = {"fault": None, "clear": st.get("t_clear"), "reclose": None}
    if run_info and run_info.get("t_fault") is not None:
        ev["fault"] = run_info["t_fault"]
    if ev["clear"] is None and r and ev["fault"] is not None:
        c = _num(r.get("clear_cycles"))
        if c:
            ev["clear"] = ev["fault"] + c / 60.0
    if r and (r.get("reclose") or "").strip() in ("1", "True", "true", "Y", "y") and ev["clear"] is not None:
        w = _num(r.get("reclose_wait")) or 20.0
        ev["reclose"] = ev["clear"] + w / 60.0
    return ev


# --------------------------------------------------------------- bus map --
_BMAPS = {}


def bus_map(rdir):
    if rdir in _BMAPS:
        return _BMAPS[rdir]
    p = os.path.join(rdir, "flags", "BUS_MAP.csv")
    bm = {"bus": {}, "adj": collections.defaultdict(set), "mach": collections.defaultdict(list), "ok": False}
    txt = _read(p)
    if txt:
        for ln in txt.splitlines():
            f = ln.split(",")
            try:
                if f[0] == "B" and len(f) >= 4:
                    bm["bus"][int(f[1])] = (float(f[2] or 0), f[3], ",".join(f[4:]).strip())
                elif f[0] == "L" and len(f) >= 3:
                    a, b = int(f[1]), int(f[2])
                    bm["adj"][a].add(b)
                    bm["adj"][b].add(a)
                elif f[0] == "M" and len(f) >= 3:
                    bm["mach"][int(f[1])].append(f[2].strip())
            except ValueError:
                continue
        bm["ok"] = bool(bm["adj"])
    _BMAPS[rdir] = bm
    return bm


def bus_name(bm, b):
    x = bm["bus"].get(int(b)) if bm else None
    if not x:
        return str(b)
    return "%s [%s %.1f kV]" % (b, x[2], x[0])


def event_pocket(r, bm):
    """Buses the fault's own trips leave with no path to the system (pockets of at
       most ISLAND_MAX_BUSES), as the study computes it. Empty when it cannot tell."""
    cut = fault_cuts(r)
    if not cut or not bm["ok"]:
        return set()
    ends = set(a for a, _b in cut)
    pocket, checked = set(), set()
    for st in ends:
        if st in checked or not bm["adj"].get(st):
            continue
        seen, fr, isl = set([st]), [st], True
        while fr:
            if len(seen) > ISLAND_MAX_BUSES:
                isl = False
                break
            nx = []
            for u in fr:
                for w in bm["adj"].get(u, ()):
                    if (u, w) in cut or w in seen:
                        continue
                    seen.add(w)
                    nx.append(w)
            fr = nx
        checked |= seen
        if isl:
            pocket |= seen
    return pocket


def near(bm, buses, hops=1):
    seen = set(buses)
    fr = set(buses)
    for _i in range(hops):
        nx = set()
        for u in fr:
            nx |= set(bm["adj"].get(u, ())) - seen
        seen |= nx
        fr = nx
    return seen

def hops_map(bm, src, limit=60):
    """{bus: buses from src} over the bus map."""
    dist, fr = {src: 0}, [src]
    for k in range(1, limit + 1):
        nx = []
        for u in fr:
            for w in bm["adj"].get(u, ()):
                if w not in dist:
                    dist[w] = k
                    nx.append(w)
        if not nx:
            break
        fr = nx
    return dist


def behind(bm, b, fbus, cut=()):
    """The buses b reaches WITHOUT passing through fbus (nor the fault's opened branches),
       when that is a small pocket (ISLAND_MAX_BUSES at most): b's only path to the system
       then runs through fbus. None when b reaches the system some other way (or no map).
       The bus map also lists branches that are out of service, so a radial connection can
       be missed -- never invented."""
    if not bm["ok"] or b is None or fbus is None or b == fbus or b not in bm["adj"]:
        return None
    seen, fr = set([b]), [b]
    while fr:
        nx = []
        for u in fr:
            for w in bm["adj"].get(u, ()):
                if w == fbus or w in seen or (u, w) in cut:
                    continue
                seen.add(w)
                nx.append(w)
                if len(seen) > ISLAND_MAX_BUSES:
                    return None
        fr = nx
    return seen


# -------------------------------------------------------------------- .dyr --
_TOK = re.compile(r"'[^']*'|\"[^\"]*\"|[^\s,'\"/]+")
_USR_AT_BUS = ("USRMDL", "USRLOD", "USRBUS", "USRBRN", "USRSHT", "USRAUX")
_DC_MODEL = re.compile(r"^(CDC|VSCDC|MTDC|CEEL|CHAAUT|CSTCNT|CSVGN|SVSMO)", re.I)
_CONVERTER = re.compile(r"^(REGC|REEC|REPC|WT[1-4][A-Z]|PVGU|PVEU|PVD|GEWT|GEPV|W[34]G|SWTG|DER_A|DERA|PVGEN|WTARA|WTDTA|"
                        r"WTPTA|WTTQA|IBR|GFM)", re.I)
_DYR = {"done": False, "files": [], "at": collections.defaultdict(list), "by_name": collections.defaultdict(list)}


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


def dyr_files():
    if DYR_FILES:
        return [_abs(p) for p in DYR_FILES if os.path.isfile(_abs(p))]
    out = []
    for sub in ("Base", "Projects"):
        d = _abs(sub)
        if not os.path.isdir(d):
            continue
        for nm in sorted(os.listdir(d)):
            if nm.lower().endswith(".dyr") and (DYR_INCLUDE_LOADED or not nm.upper().startswith("LOADED_")):
                if os.path.isfile(os.path.join(d, nm)):
                    out.append(os.path.join(d, nm))
    return out


def _dyr_rec(toks, path):
    if len(toks) < 3:
        return
    m1 = _unq(toks[1]).upper()
    ints = []
    for x in toks[3:60]:
        u = _unq(x)
        if re.match(r"^\d{4,7}$", u):
            ints.append(int(u))
    if m1 == "USRMSC":                       # 'id' 'USRMSC' 'NAME' ... -- a plant controller and the like
        name, bus = _unq(toks[2]).upper(), None
    elif m1 in _USR_AT_BUS and len(toks) >= 4:
        name, bus = _unq(toks[3]).upper(), _unq(toks[0])
    elif _DC_MODEL.match(m1):                # a DC line: named, not at a bus
        name, bus = m1, None
    else:
        name, bus = m1, _unq(toks[0])
    b = int(bus) if bus and bus.isdigit() else None
    if b is not None:
        _DYR["at"][b].append((name, _unq(toks[2]), path))
    _DYR["by_name"][name].append((b, ints, path, _unq(toks[0])))


def read_dyrs():
    """Every .dyr record, once: the models at each bus, and every record by model name."""
    if _DYR["done"]:
        return _DYR
    _DYR["done"] = True
    for p in dyr_files():
        t0, n = time.time(), 0
        try:
            cur = []
            with open(p, "r", errors="replace") as fh:
                for line in fh:
                    s = line.lstrip()
                    if not s or s.startswith("@!"):
                        continue
                    body, end = _cut_slash(line)
                    cur.extend(_TOK.findall(body))
                    if end:
                        if cur:
                            _dyr_rec(cur, p)
                            n += 1
                        cur = []
            if cur:
                _dyr_rec(cur, p)
        except Exception as e:
            print("[why] could not read %s (%s)" % (_rel(p), e))
            continue
        _DYR["files"].append(p)
        print("[why] .dyr %s: %d record(s) (%.1f s)" % (_rel(p), n, time.time() - t0))
    if not _DYR["files"]:
        print("[why] no .dyr found in Base\\ or Projects\\ -- the models at the buses cannot be named (DYR_FILES)")
    return _DYR


def _dyr_for(proj, path):
    """A .dyr whose name names another project is that project's own (its SGF buses 999001...)."""
    nm = os.path.basename(path).lower()
    hits = [p for p in (PROJECTS or []) if p.lower() in nm]
    return not hits or proj in hits


def models_at(proj, b):
    """['REGCAU1', 'REECAU1', ...] of the machine(s) at bus b (the project's own .dyr files)."""
    out = []
    for name, _mid, path in read_dyrs()["at"].get(int(b), []):
        if _dyr_for(proj, path) and name not in out:
            out.append(name)
    return out


def _models_txt(ms, n=6):
    return ", ".join(ms[:n]) + (" ..." if len(ms) > n else "")


_MISSING = {}


def missing_models(proj, rdirs):
    """[(model name, [record text])] of the models PSS/E could not load ("NOT ACCESSIBLE" in an
       init log of this project), with the buses their .dyr records name."""
    if proj in _MISSING:
        return _MISSING[proj]
    names = set()
    for rdir in rdirs:
        for q in sorted(glob.glob(os.path.join(rdir, "logs", "FAULT_*_strt-prog.txt")))[:3] + \
                glob.glob(os.path.join(rdir, "logs", "INIT*-prog.txt"))[:2]:
            names |= set(x.upper() for x in re.findall(r'MODEL\s+"([^"]+)"\s+NOT ACCESSIBLE', _read(q) or "", re.I))
        if names:
            break
    out = []
    for nm in sorted(names):
        recs = []
        for b, ints, path, first in read_dyrs()["by_name"].get(nm, []):
            if not _dyr_for(proj, path):
                continue
            recs.append((b, ints, path, first))
        out.append((nm, recs))
    _MISSING[proj] = out
    return out


def missing_lines(proj, rdirs, bm):
    L = []
    for nm, recs in missing_models(proj, rdirs):
        if not recs:
            L.append("   %-8s not in any .dyr read here" % nm)
            continue
        for b, ints, path, first in recs[:4]:
            named = [x for x in ints if x in bm["bus"]] if bm["ok"] else ints
            L.append("   %-8s %s%s in %s" % (
                nm, ("at bus %s" % bus_name(bm, b)) if b is not None else "record %s" % first,
                ("; names bus(es) %s" % ", ".join(bus_name(bm, x) for x in named[:4])) if named else "",
                os.path.basename(path)))
    return L



# ------------------------------------------------------------- run logs --
R_HDR = re.compile(r"^\s*(\S+)\s+--\s+attempt started\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d:\d\d)")
R_HMS = re.compile(r"^\s*\[(\d\d):(\d\d):(\d\d)\]")
R_SIMT = re.compile(r"\]\s+\S+\s+(\S[\w-]*)\s+sim t\s*=\s*([\d.]+)\s*/\s*([\d.]+)\s*s")
R_OK = re.compile(r"^\[(\d\d:\d\d:\d\d)\]\s+ok\s+(.+)$")
R_FAPP = re.compile(r"fault applied ~t=([\d.]+)s")
R_SOLVER = re.compile(r"\[solver\]\s+\S+:\s+(attempt\s+\d+\s+on\s+.+)$")
R_BAD = re.compile(r"\bnan\b|\binf\b|traceback|forrtl|not converged|diverg|stack overflow|access violation|"
                   r"exception|error|killed|watchdog|timed out|time-out|licen|codemeter|giving up|crash|abort|"
                   r"still solving", re.I)
R_NOISE = re.compile(r"^\s*(FLOW1\b|ACTIVITY\?|CCT TYPE USER DEFINED .*NOT ACCESSIBLE|Channel output file is|"
                     r"No power flow data changed|Messages for api|STRT or MSTR has not)", re.I)


def run_attempts(rdir, fid):
    """Every attempt found in logs\\RUN_<fid>_w*.log, oldest first."""
    atts = []
    for lp in sorted(glob.glob(os.path.join(rdir, "logs", "RUN_%s_w*.log" % fid)) +
                     glob.glob(os.path.join(rdir, "logs", "RUN_%s_*.log" % fid))):
        if any(a["log"] == lp for a in atts):
            continue
        m = re.search(r"_w(\d+)\.log$", lp)
        worker = int(m.group(1)) if m else None
        txt = _read(lp)
        if txt is None:
            continue
        has_hdr = any(R_HDR.match(x) for x in txt.splitlines()[:400]) or bool(
            re.search(r"--\s+attempt started\s+\d{4}-", txt))
        cur = None

        def new(date, hms):
            try:
                t0 = datetime.datetime.strptime(date + " " + hms, "%Y-%m-%d %H:%M:%S")
            except Exception:
                t0 = None
            return {"log": lp, "worker": worker, "start": t0, "last": t0, "solver": None,
                    "phase": None, "simt": None, "tend": None, "ok": None, "bad": [], "tail": collections.deque(maxlen=8),
                    "t_fault": None, "nan_fault": False}
        for ln in txt.splitlines():
            mh = R_HDR.match(ln)
            if mh:
                cur = new(mh.group(2), mh.group(3))
                atts.append(cur)
                continue
            if cur is None:
                if has_hdr:
                    continue                      # the banner above the first attempt
                cur = new("1970-01-01", "00:00:00")
                cur["start"] = None
                atts.append(cur)
            s = ln.strip()
            if not s or R_NOISE.match(s):
                continue
            mt = R_HMS.match(ln)
            if mt and cur["start"] is not None:
                t = cur["start"].replace(hour=int(mt.group(1)), minute=int(mt.group(2)), second=int(mt.group(3)))
                while t < cur["start"] - datetime.timedelta(minutes=5):
                    t += datetime.timedelta(days=1)
                cur["last"] = t
            ms = R_SOLVER.search(ln)
            if ms:
                cur["solver"] = ms.group(1)
            m2 = R_SIMT.search(ln)
            if m2:
                cur["phase"], cur["simt"], cur["tend"] = m2.group(1), float(m2.group(2)), float(m2.group(3))
            m3 = R_OK.match(s)
            if m3:
                cur["ok"] = (m3.group(1), m3.group(2).strip())
            m4 = R_FAPP.search(ln)
            if m4:
                cur["t_fault"] = float(m4.group(1))
            if "faulted-bus voltage during fault = nan" in ln:
                cur["nan_fault"] = True
            if R_BAD.search(s) and "no Inf overflow" not in s:
                if len(cur["bad"]) < 12 and s not in cur["bad"]:
                    cur["bad"].append(_short(s, 170))
            cur["tail"].append(_short(s, 170))
    atts.sort(key=lambda a: (a["start"] or datetime.datetime(1970, 1, 1)))
    return atts


R_EXIT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(.*)$")


def worker_exits(rdir):
    """{worker: [(time, text)]} from WORKER_EXIT_w<k>.txt (logs\\ or the run folder)."""
    out = collections.defaultdict(list)
    for p in glob.glob(os.path.join(rdir, "logs", "WORKER_EXIT_w*.txt")) + glob.glob(os.path.join(rdir, "WORKER_EXIT_w*.txt")):
        m = re.search(r"_w(\d+)\.txt$", p)
        if not m:
            continue
        for ln in (_read(p) or "").splitlines():
            mm = R_EXIT.match(ln.strip())
            if not mm:
                continue
            try:
                t = datetime.datetime.strptime(mm.group(1), "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            txt = mm.group(2).strip()
            rc = re.search(r"rc=(-?\d+)", txt)
            if rc and not re.search(r"0x[0-9A-Fa-f]{8}", txt):
                code = int(rc.group(1)) & 0xFFFFFFFF if int(rc.group(1)) < 0 else int(rc.group(1))
                if code in EXIT_CODES:
                    txt += " (%s)" % EXIT_CODES[code]
            out[int(m.group(1))].append((t, txt))
    return out


def _exit_words(txt):
    """'rc=3221225725 (0xC00000FD) STACK OVERFLOW -- ...' -> '0xC00000FD STACK OVERFLOW -- ...'."""
    t = re.sub(r"^rc=-?\d+\s*", "", str(txt)).strip()
    t = re.sub(r"^\((0x[0-9A-Fa-f]{8})\)\s*", r"\1 ", t)
    t = re.sub(r"^\((.*)\)$", r"\1", t)
    return t or str(txt)


def exit_for(att, exits):
    if att["worker"] is None or att["last"] is None:
        return None
    best = None
    for t, txt in exits.get(att["worker"], []):
        if att["start"] and t < att["start"]:
            continue
        if t - att["last"] > datetime.timedelta(minutes=EXIT_MATCH_MIN):
            continue
        if best is None or t < best[0]:
            best = (t, txt)
    return best


def status_rows(rdir, fid):
    rows = []
    for lp in glob.glob(os.path.join(rdir, "LIVE_STATUS*.txt")):
        for ln in (_read(lp) or "").splitlines():
            if re.match(r"^\s*%s\s+(GAVE-UP|ERROR|FAILED|INCOMPLETE|INTERRUPTED)\b" % re.escape(fid), ln):
                rows.append(_short(ln, 170))
    for lp in glob.glob(os.path.join(rdir, "logs", "DYN_STUDY_w*.log")):
        for ln in (_read(lp) or "").splitlines():
            if re.search(r"(?<![\w])%s(?![\w])" % re.escape(fid), ln) and re.search(r"GIVING UP|gave up|attempted \d+ time", ln):
                s = _short(ln, 170)
                if s not in rows:
                    rows.append(s)
    return rows[:8]


def init_check(rdir, fid):
    """The init log's 'NOT ACCESSIBLE' lines, and whether a finished run's init log has them too."""
    p = os.path.join(rdir, "logs", "FAULT_%s_strt-prog.txt" % fid)
    txt = _read(p)
    if txt is None:
        return {"have": False}
    names = sorted(set(re.findall(r'MODEL\s+"([^"]+)"\s+NOT ACCESSIBLE', txt, re.I)))
    n_nc = len(re.findall(r"NETWORK NOT CONVERGED", txt, re.I))
    other = None
    if names:
        for q in sorted(glob.glob(os.path.join(rdir, "logs", "FAULT_*_strt-prog.txt"))):
            oid = os.path.basename(q)[6:-len("_strt-prog.txt")]
            if oid == fid or not os.path.isfile(os.path.join(rdir, "outs", oid + ".done")):
                continue
            t2 = _read(q) or ""
            other = (oid, sorted(set(re.findall(r'MODEL\s+"([^"]+)"\s+NOT ACCESSIBLE', t2, re.I))))
            break
    return {"have": True, "names": names, "nc": n_nc, "other": other,
            "tail": [_short(x, 150) for x in txt.splitlines() if x.strip()][-2:]}


# -------------------------------------------------------------- PSS/E log --
R_TIME = re.compile(r"TIME\s*=\s*(-?\d+(?:\.\d+)?(?:[Ee][-+]?\d+)?)")
R_NC = re.compile(r"Network not converged at TIME\s*=\s*(-?[\d.Ee+-]+)", re.I)
R_MTRIP = re.compile(r"^\s*MACHINE\s+(\S+)\s+AT BUS\s+(\d+)\s*\[([^\]]*)\]\s*TRIPPED AT TIME\s*=\s*(-?[\d.]+)")
R_BDISC = re.compile(r"^\s*BUS\s+(\d+)\s+DISCONNECTED AT TIME\s*=\s*(-?[\d.]+)")
R_OOS = re.compile(r"OUT OF STEP CONDITION AT TIME\s*=\s*(-?[\d.]+)")
R_TOBUS = re.compile(r"(?<![\d.])(\d{3,7})(?![\d.])")
R_BUSNO = re.compile(r"(?:^|\s)(\d{3,7})(?=\s|$)")
R_BAND_HDR = re.compile(r"VOLTAGES OUTSIDE OF BAND\s+(-?[\d.]+)\s+TO\s+(-?[\d.]+)")
R_BAND_ROW = re.compile(r"(\d{3,7})\s*\[([^\]]*)\]\s*(\d+\.\d+|\*+)\s*(HI|LO)")
R_STATUS = re.compile(r"Status of circuit\s+\"([^\"]*)\"\s+from\s+(\d+)\s*\[[^\]]*\]\s+to\s+(\d+)\s*\[[^\]]*\]")
R_GENPWR = re.compile(r"Power unbalance\s*=\s*(-?[\d.]+)\s*;\s*Threshold\s*=\s*(-?[\d.]+)", re.I)
R_DC = re.compile(r"\bDC\b|CONVERTER|COMMUTAT|\bCDC\w*|\bVSC\b|\bBYPASS", re.I)
R_MODEL_LINE = re.compile(r"^Model\s+(\S+)\s+Bus\s+(\d+)\s*\[([^\]]*)\]")


def psse_log(rdir, fid):
    """What PSS/E printed in the fault window (logs\\psse\\<fid>.txt)."""
    p = os.path.join(rdir, "logs", "psse", fid + ".txt")
    txt = _read(p)
    if txt is None:
        return None
    L = {"path": p, "islands": [], "oos": [], "nc": [], "mtrip": [], "bdisc": [], "band_hi": [],
         "branch": [], "genpwr": [], "dc": [], "nan_models": [], "t_last": None,
         "tail": collections.deque(maxlen=10)}
    last_model = None
    last_t = None
    island = None
    oos = None
    band_t = None
    for line in txt.splitlines():
        if line.startswith("FLOW"):
            continue
        s = line.strip()
        if not s:
            if island is not None:
                L["islands"].append((last_t, sorted(set(int(x) for x in island))))
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
            m1 = (R_BUSNO.search(line[28:]) or R_TOBUS.search(line[28:])) if len(line) > 28 else None
            if m0 and m1:
                L["oos"].append((oos[1], int(m0.group(1)), int(m1.group(1))))
                continue
            oos = None
        if not R_NOISE.match(s):
            L["tail"].append(_short(s, 150))
        mm_ = R_MODEL_LINE.match(s)
        if mm_:
            last_model = (mm_.group(1), mm_.group(2), mm_.group(3).strip())
        elif last_model and "not converged" in s and "NaN" in s:
            if last_model not in L["nan_models"] and len(L["nan_models"]) < 8:
                L["nan_models"].append(last_model)
        if R_DC.search(s) and len(L["dc"]) < 12:
            _mt = R_TIME.search(s)
            L["dc"].append((_num(_mt.group(1)) if _mt else last_t, _short(s, 140)))
        mnc = R_NC.search(line)
        if mnc:
            t = _num(mnc.group(1))
            L["nc"].append(t)
            last_t = t if t is not None else last_t
            continue
        if "TIME" in line:
            mt = R_TIME.search(line)
            if mt:
                last_t = _num(mt.group(1))
            if "OUTSIDE OF BAND" in line:
                band_t = last_t
                continue
        if ("HI" in line) and "[" in line:
            for (bb, nm, val, side) in R_BAND_ROW.findall(line):
                if side == "HI":
                    v = _num(val)
                    if v is None or v > RUNAWAY_PU:
                        L["band_hi"].append((band_t, int(bb), v, nm.strip()))
            continue
        if "Power unbalance" in s:
            mg = R_GENPWR.search(s)
            if mg:
                L["genpwr"].append((last_t, _num(mg.group(1)), _num(mg.group(2))))
            continue
        if "TRIPPED AT TIME" in line and "MACHINE" in line:
            mm = R_MTRIP.match(line)
            if mm:
                L["mtrip"].append((_num(mm.group(4)), mm.group(1).strip(), int(mm.group(2)), mm.group(3).strip()))
            continue
        if "DISCONNECTED AT TIME" in line:
            mb = R_BDISC.match(line)
            if mb:
                L["bdisc"].append((_num(mb.group(2)), int(mb.group(1))))
            continue
        if "following buses are disconnected" in line:
            island = []
            continue
        if "OUT OF STEP CONDITION" in line:
            mo = R_OOS.search(line)
            oos = ["head", _num(mo.group(1)) if mo else last_t]
            continue
        if "Status of circuit" in line and "out-of-service" in line:
            ms = R_STATUS.search(line)
            if ms:
                L["branch"].append((last_t, int(ms.group(2)), int(ms.group(3)), ms.group(1).strip()))
            continue
    if island is not None:
        L["islands"].append((last_t, sorted(set(int(x) for x in island))))
    L["t_last"] = last_t
    return L


# -------------------------------------------------------------- .out read --
_DYN = {"mod": None, "tried": False, "why": None}


def _dyntools():
    if _DYN["tried"]:
        return _DYN["mod"]
    _DYN["tried"] = True
    try:
        import dyntools                                   # noqa -- already on the path
        _DYN["mod"] = dyntools
        return dyntools
    except Exception:
        pass
    cands = list(PSSE_DIRS or [])
    for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
               r"C:\Program Files (x86)", r"C:\Program Files"):
        if pf:
            for v in ("PSSE34", "PSSE35", "PSSE36", "PSSE33"):
                cands.append(os.path.join(pf, "PTI", v))
    tag = "PSSPY%d%d" % sys.version_info[:2]
    for d in cands:
        if not os.path.isdir(d):
            continue
        pys = [os.path.join(d, tag)] + sorted(glob.glob(os.path.join(d, "PSSPY*")), reverse=True)
        bn = os.path.join(d, "PSSBIN")
        for py in pys:
            if not os.path.isdir(py):
                continue
            for x in (bn, py):
                if x not in sys.path:
                    sys.path.insert(0, x)
            os.environ["PATH"] = bn + os.pathsep + py + os.pathsep + os.environ.get("PATH", "")
            try:
                import dyntools                           # noqa
                _DYN["mod"] = dyntools
                return dyntools
            except Exception as e:
                _DYN["why"] = "dyntools did not import from %s (%s)" % (py, e)
    if not _DYN["why"]:
        _DYN["why"] = "PSS/E's Python folder was not found -- set PSSE_DIRS"
    return None


def _cat(title):
    up = re.sub(r"\[[^\]]*\]", " ", str(title)).upper()
    if "ANG" in up:
        return "ANGLE"
    if "FREQ" in up:
        return "FREQ"
    if up.strip().startswith("VOLT") or "ETERM" in up or re.search(r"(^|[\s_])V(\s|$)", up):
        return "VOLT"
    return "OTHER"


def _fp_mask():
    """Keep floating-point exceptions masked on this thread (as the study does), so a NaN
       in a .out is a NaN, not a PSS/E 'Floating-Point Exception' box that kills the run."""
    if os.name != "nt":
        return
    try:
        import ctypes
        cur = ctypes.c_uint(0)
        for dll in ("msvcr100", "msvcr110", "msvcrt"):
            try:
                lib = ctypes.CDLL(dll + ".dll")
            except Exception:
                continue
            fn = getattr(lib, "_controlfp_s", None)
            if fn is not None and fn(ctypes.byref(cur), ctypes.c_uint(0x0008001F), ctypes.c_uint(0x0008001F)) == 0:
                return
    except Exception:
        pass


def read_out(path, t_upto=None, t_end=None):
    """(time list, {title: values}, why-not) for the voltage and angle channels -- of the
       first part of the file only (to t_upto), read from a copy of its head, as the
       study's own reader calibrates. PSS/E is never initialised here."""
    dyn = _dyntools()
    if dyn is None:
        return None, None, _DYN["why"]
    data = ids = None
    size = os.path.getsize(path)
    head = size                      # dyntools refuses a head copy of a .out -- the whole file is read
    src, tmpd, t0 = path, None, time.time()
    try:
        if head < size:
            tmpd = tempfile.mkdtemp(prefix="why_out_")
            src = os.path.join(tmpd, os.path.basename(path))
            with open(path, "rb") as fi, open(src, "wb") as fo:
                left = head
                while left > 0:
                    b = fi.read(min(left, 4194304))
                    if not b:
                        break
                    fo.write(b)
                    left -= len(b)
            print("[why]   reading %s -- the first %s of %s (to t=%.1f s) ..." % (_rel(path), _mb(head), _mb(size), t_upto))
        else:
            print("[why]   reading %s (%s) ..." % (_rel(path), _mb(size)))
        _fp_mask()
        try:
            _sh, ids, data = dyn.CHNF(src).get_data()
        except MemoryError:
            return None, None, "not enough memory to read %s in this Python" % os.path.basename(path)
        except Exception as e:
            return None, None, "dyntools could not read %s (%s)" % (os.path.basename(path), e)
    finally:
        if tmpd:
            shutil.rmtree(tmpd, ignore_errors=True)
    if not isinstance(data, dict) or not isinstance(ids, dict):
        return None, None, "dyntools returned nothing for %s" % os.path.basename(path)
    print("[why]     read in %.0f s" % (time.time() - t0))
    try:
        t = list(data["time"])
    except Exception:
        return None, None, "no time channel in %s" % os.path.basename(path)
    keep = {}
    for k, title in ids.items():
        if k == "time":
            continue
        c = _cat(title)
        if c in ("VOLT", "ANGLE"):
            keep["%s|%s" % (c, title)] = data[k]
    del data
    return t, keep, None


def _first(t, v, pred, t_from):
    for i, x in enumerate(v):
        if t[i] >= t_from and pred(x):
            return i
    return None


def series_facts(t, ch, t_from):
    """Voltages that pass OFFSCALE_PU (or go NaN) after t_from, and angles that turn more than ANGLE_SPAN_DEG."""
    volts, angs = [], []
    for key, v in ch.items():
        cat, title = key.split("|", 1)
        if not v:
            continue
        nan_i = _first(t, v, lambda x: x != x, t_from)
        if cat == "VOLT":
            fin = [x for x in v if x == x]
            mx = max(fin) if fin else None
            if (mx is not None and mx > OFFSCALE_PU) or nan_i is not None:
                i5 = _first(t, v, lambda x: x == x and x > OFFSCALE_PU, t_from)
                i15 = _first(t, v, lambda x: x == x and x > RUNAWAY_PU, t_from)
                ipk = max(range(len(v)), key=lambda i: v[i] if v[i] == v[i] else -1e99)
                i0 = _first(t, v, lambda x: True, t_from)
                pre = v[max(0, i0 - 1)] if i0 is not None else v[0]
                volts.append({"title": title, "pre": pre, "t15": t[i15] if i15 is not None else None,
                              "t5": t[i5] if i5 is not None else None, "peak": mx, "tpk": t[ipk],
                              "tnan": t[nan_i] if nan_i is not None else None, "end": v[-1]})
        else:
            i0 = _first(t, v, lambda x: True, t_from)
            seg = [x for x in v[i0 or 0:] if x == x]
            if len(seg) < 2:
                continue
            span = max(seg) - min(seg)
            if span > ANGLE_SPAN_DEG:
                base = seg[0]
                i360 = _first(t, v, lambda x: x == x and abs(x - base) > 360.0, t_from)
                n5 = max(2, int(len(t) * 5.0 / max(1e-6, t[-1] - t[0])))
                tail = [(t[i], v[i]) for i in range(max(0, len(v) - n5), len(v)) if v[i] == v[i]]
                rate = ((tail[-1][1] - tail[0][1]) / (tail[-1][0] - tail[0][0])
                        if len(tail) > 1 and tail[-1][0] > tail[0][0] else None)
                angs.append({"title": title, "span": span, "t360": t[i360] if i360 is not None else None,
                             "rate": rate, "start": base, "end": seg[-1]})
    volts.sort(key=lambda x: (x["t5"] if x["t5"] is not None else (x["tnan"] if x["tnan"] is not None else 1e9)))
    angs.sort(key=lambda x: -x["span"])
    return volts, angs


# ---------------------------------------------------------------- analysis --
def when_txt(t, ev):
    if t is None:
        return "?"
    s = "t=%.3f s" % t
    tags = []
    for k, lab in (("fault", "fault on"), ("clear", "clearing"), ("reclose", "reclose")):
        if ev.get(k) is not None and abs(t - ev[k]) <= 0.02:
            tags.append("at the %s" % lab)
    if not tags and ev.get("clear") is not None:
        if ev.get("fault") is not None and ev["fault"] <= t < ev["clear"]:
            tags.append("during the fault")
        elif t >= ev["clear"]:
            d = t - (ev["reclose"] if ev.get("reclose") is not None and t >= ev["reclose"] else ev["clear"])
            tags.append("%.3f s after the %s" % (d, "reclose" if ev.get("reclose") is not None and t >= ev["reclose"]
                                                  else "clearing"))
    return s + (" (%s)" % ", ".join(tags) if tags else "")


_ANALYSED = {}


def analyse_run(proj, case, rdir, fid):
    """Everything the logs and score files say about one run of one fault (once per run)."""
    key = (rdir, fid)
    if key not in _ANALYSED:
        _ANALYSED[key] = _analyse_run(proj, case, rdir, fid)
    return _ANALYSED[key]


def _last_write(rdir, fid, st):
    """Newest write time of this run's .out and run logs, or None."""
    ts = []
    for p in ([st["out"]] if st["out"] else []) + glob.glob(os.path.join(rdir, "logs", "RUN_%s_w*.log" % fid)):
        try:
            ts.append(os.path.getmtime(p))
        except OSError:
            pass
    return max(ts) if ts else None


def _ago(sec):
    sec = max(0.0, float(sec))
    return ("%.0f s" % sec) if sec < 120 else ("%.0f min" % (sec / 60.0))


def _analyse_run(proj, case, rdir, fid):
    fl = fault_list(proj, rdir)
    frow = fl.get(fid.upper())
    st = run_state(rdir, fid)
    scen = read_scen(rdir, fid)
    A = {"project": proj, "case": case, "rdir": rdir, "fault": fid, "frow": frow, "state": st, "scen": scen}
    off = _crit(scen, "Bus voltages within scale")
    stab = _crit(scen, "System stability")
    A["offscale"] = offscale_items(off[1]) if off and off[0] == "FAIL" else []
    A["stab"] = stability_items(stab[1]) if stab and stab[0] == "FAIL" else []
    A["stab_detail"] = stab[1] if stab else None
    crashed = bool(st["out"] or st["attempts"]) and not st["done"] and not st["partial"] and not (scen and scen["verdict"])
    # A run with no result whose files are still being written is not a crash: a run
    # failing to converge at every step crawls (60 iterations a step), and looks dead.
    A["last_write"] = _last_write(rdir, fid, st) if crashed else None
    running = bool(A["last_write"] and time.time() - A["last_write"] < RUNNING_MIN * 60.0)
    A["crashed"], A["running"] = crashed, running
    A["kind"] = ("STILL RUNNING" if running else
                 "CRASHED" if crashed else
                 "OFF-SCALE" if A["offscale"] else
                 "SOLUTION LOST" if A["stab"] else
                 ("ran, %s" % (scen["verdict"] if scen and scen["verdict"] else "no verdict")))
    A["atts"] = run_attempts(rdir, fid)
    run1 = A["atts"][-1] if A["atts"] else None
    A["ev"] = event_times(frow, st, run1)
    A["psse"] = psse_log(rdir, fid)
    A["late_nc"] = None
    if A["kind"].startswith("ran") and A["psse"] and A["psse"]["nc"]:
        ln = late_nc(A["psse"]["nc"], A["ev"])
        if ln:
            # it finished and was scored, but PSS/E was still failing to solve the network
            # well after the last switching: the values the verdict judged are not a solution
            A["kind"], A["late_nc"] = "NOT CONVERGED", ln
    bm = bus_map(rdir)
    A["bm"] = bm
    A["pocket"] = event_pocket(frow, bm)
    A["out_facts"] = None
    A["out_why"] = None
    if crashed:
        A["exits"] = worker_exits(rdir)
        A["status"] = status_rows(rdir, fid)
        A["init"] = init_check(rdir, fid)
        A["typical"] = typical_out(rdir)
    return A


def read_plan(A, window=None):
    """The .out read this run needs, or None. A CRASHED RUN'S PARTIAL .out IS NEVER
       READ: PSS/E died while writing it and dyntools cannot take it ("Error reading
       file") -- its size already says how far the run got."""
    st, ev = A["state"], A["ev"]
    if not READ_OUT or not st["out"] or A["crashed"]:
        return None
    last_sw = max([x for x in (ev["fault"], ev["clear"], ev["reclose"]) if x is not None] or [0.0])
    angle = window == "angle" or (A["stab"] and not A["offscale"])
    t_upto = (last_sw + (ANGLE_WINDOW_S if angle else OUT_WINDOW_S)) if last_sw else None
    t_end = st["t_end"] or typical_out(A["rdir"])[1]
    t_from = ev["fault"] if ev["fault"] is not None else ev["clear"]
    return ((A["rdir"], A["fault"]), st["out"], t_upto, t_end, t_from)


def _read_job(job):
    """One .out read -> (key, facts, last time read, why-not). Runs in a worker process."""
    key, path, t_upto, t_end, t_from = job
    try:
        t, ch, why = read_out(path, t_upto, t_end)
        if why or not t:
            return key, None, None, why or "nothing read from %s" % os.path.basename(path)
        facts = series_facts(t, ch, t_from if t_from is not None else t[0])
        return key, ("series",) + facts, t[-1], None
    except Exception as e:
        return key, None, None, "reading %s failed (%s)" % (os.path.basename(path), e)


def run_reads(jobs):
    """{key: (facts, last time, why-not)} -- READ_WORKERS files at a time."""
    out = {}
    if not jobs:
        return out
    if _dyntools() is None:
        for j in jobs:
            out[j[0]] = (None, None, _DYN["why"])
        return out
    n = max(1, min(int(READ_WORKERS or 1), len(jobs)))
    t0 = time.time()
    print("[why] reading %d .out file(s), %d at a time ..." % (len(jobs), n))
    if n > 1:
        try:
            import multiprocessing
            pool = multiprocessing.Pool(n)
            try:
                for key, facts, upto, why in pool.imap_unordered(_read_job, jobs):
                    out[key] = (facts, upto, why)
            finally:
                pool.close()
                pool.join()
            print("[why] .out files read in %.0f s" % (time.time() - t0))
            return out
        except Exception as e:
            print("[why] reading in parallel failed (%s) -- one at a time instead" % e)
    for j in jobs:
        key, facts, upto, why = _read_job(j)
        out[key] = (facts, upto, why)
    print("[why] .out files read in %.0f s" % (time.time() - t0))
    return out


def nc_window(nc, st=None, rdir=None):
    """(count, first t, last t, every step?, end t if it came back) for PSS/E's 'Network not
       converged' times. PSS/E does not stop on a failed network solution -- it carries on
       from the unconverged values -- so a run can fail for seconds and still reach its end.
       The PSS/E log covers the fault run to its end, so no message after the last one means
       every later step converged."""
    ts = sorted(set(round(t, 4) for t in nc if t is not None))
    if not ts:
        return len(nc), None, None, False, None
    a, b = ts[0], ts[-1]
    gaps = [y - x for x, y in zip(ts, ts[1:]) if y - x > 1e-4]
    every = bool(len(ts) >= 20 and gaps and len(ts) >= 0.9 * ((b - a) / min(gaps) + 1))
    back = None
    if st and st.get("done") and not st.get("diverged"):
        te = st.get("t_end") or (typical_out(rdir)[1] if rdir else None)
        if te and b < float(te) - 0.5:
            back = float(te)
    return len(nc), a, b, every, back


def nc_text(nc, st=None, rdir=None):
    """'N time(s) between t=a and b s (every time step in that window)', end t if it came back."""
    n, a, b, every, back = nc_window(nc, st, rdir)
    if a is None:
        return "%d time(s)" % n, None
    if b - a < 1e-6:
        return "%d time(s) at t=%.3f s" % (n, a), back
    return ("%d time(s) between t=%.3f and %.3f s%s" % (n, a, b, " (every time step in that window)" if every else ""),
            back)


def reason(A):
    """The most likely reason, in plain words, from what analyse_run found."""
    bm, ev = A["bm"], A["ev"]
    P = A["psse"] or {}
    if A["running"]:
        a = A["atts"][-1] if A["atts"] else {}
        parts = ["no result yet, but this run's files were written %s before this check%s: it is most likely "
                 "still running, not crashed -- run this script again when it finishes"
                 % (_ago(time.time() - A["last_write"]),
                    (" (sim t = %.2f of %.2f s)" % (a["simt"], a["tend"] or 0)) if a.get("simt") is not None else "")]
        if P.get("nc"):
            parts.append("PSS/E's network solution has failed %s so far -- every failed step takes the full "
                         "iteration limit, which is why it is slow" % nc_text(P["nc"])[0])
        return "; ".join(parts) + "."
    if A["crashed"]:
        atts = A["atts"]
        parts = []
        steps = [a["ok"][1] for a in atts if a.get("ok")]
        simts = [a["simt"] for a in atts if a.get("simt") is not None]
        exits = [exit_for(a, A["exits"]) for a in atts]
        names = [x[1] for x in exits if x]
        if names:
            common = collections.Counter(_exit_words(n) for n in names).most_common(1)[0][0]
            parts.append("PSS/E died %s: %s" % ("in every attempt" if len(names) == len(atts) and len(atts) > 1
                                                else "(%d of %d attempts)" % (len(names), len(atts)), common))
        if steps:
            same = len(set(steps)) == 1
            parts.append("%s after '%s'%s" % ("every attempt stopped" if same and len(steps) > 1 else "the last attempt stopped",
                                             steps[-1], (" (sim t = %.2f s)" % simts[-1]) if simts else ""))
        if any(a.get("nan_fault") for a in atts):
            parts.append("the faulted-bus voltage already read NaN during the fault (a run that completes reads "
                         "a few hundredths of a pu there), so the network solution was failing from the fault on")
        if P.get("nc"):
            parts.append("PSS/E reported 'network not converged' %s" % nc_text(P["nc"])[0])
        hi = sorted([x for x in P.get("band_hi", []) if x[2] is not None], key=lambda x: -x[2])[:2]
        if hi:
            parts.append("highest voltages PSS/E reported: %s" % ", ".join(
                "%s [%s] %.3g pu at t=%.3f s" % (b, _short(nm, 18), v, t or 0) for t, b, v, nm in hi))
        if P.get("genpwr"):
            parts.append("PSS/E's generator power-unbalance check (GENPWR) tripped %d machine(s)" % len(P["genpwr"]))
        if P.get("nan_models"):
            parts.append("then %s returned NaN, and PSS/E stopped -- the NaN is the last symptom, not the cause" % ", ".join(
                "%s at %s [%s]" % (m, b, _short(nm, 18)) for m, b, nm in P["nan_models"][:3]))
        if not parts:
            parts.append("no message survives: the process vanished without a log line -- see the attempts below")
        return "; ".join(parts) + "."
    if A["kind"] == "NOT CONVERGED" and A.get("late_nc"):
        n, tl, ls = A["late_nc"]
        s, _bk = nc_text(P.get("nc") or [])
        return ("Ran to the end and was scored (%s), but PSS/E's network solution kept failing: %s, %d of them more "
                "than %.1f s after the last switching (t=%.3f s), the last at t=%.3f s. PSS/E does not stop on a "
                "failed step -- it carries on from the unconverged values -- so the values in that window are not a "
                "solution, and this run's verdict cannot be trusted."
                % ((A["scen"] or {}).get("verdict") or "no verdict", s, n, NONCONV_AFTER_S, ls, tl))
    if A["kind"].startswith("ran"):
        return "Ran to the end: no off-scale voltage and no lost solution (%s)." % A["kind"][5:]
    # off-scale / solution lost
    named = set()
    for lab, _pk in A["offscale"]:
        named |= set(_buses_in(lab))
    for it in A["stab"]:
        named |= set(_buses_in(it.split(" angle")[0]))
    of = A.get("out_facts")
    if of and of[0] == "series":
        for v in of[1][:6]:
            named |= set(_buses_in(v["title"]))
    isl_psse = set()
    for _t, bl in P.get("islands", []):
        isl_psse |= set(bl)
    in_pocket = named & A["pocket"]
    in_psse = named & isl_psse
    out = []
    dc = [(b, DC_TERMINALS[b]) for b in sorted(A["pocket"] | isl_psse) if b in DC_TERMINALS]
    if dc and (in_pocket or in_psse or not named):
        bb = sorted(in_pocket | in_psse) or sorted(A["pocket"] | isl_psse)
        out.append("ISLANDED ONTO A DC TIE: the fault's own switching leaves %s connected to the rest of the "
                   "system only through %s. A line-commutated DC converter cannot hold the voltage of an AC "
                   "island: it keeps pushing its power into a few buses with no generator, and its filter "
                   "capacitors keep producing MVAr, so the island's voltage runs away. In reality the tie's "
                   "protection blocks the converter (and switches its filters out) within a few cycles; that "
                   "blocking is not in the simulation. This is not a real overvoltage"
                   % (", ".join(str(b) for b in bb[:6]), "; ".join(n for _b, n in dc)))
    elif in_pocket or in_psse:
        bb = sorted(in_pocket | in_psse)
        mach = sorted(set("%s-%s" % (b, i) for b in (A["pocket"] | isl_psse) for i in bm["mach"].get(b, [])))
        out.append("ISLANDED BY THE FAULT: %s %s cut off from the system by the fault's own switching%s. "
                   "An island with no grid connection has no valid network solution in a positive-sequence "
                   "simulation; the converter / machine models in it lose their reference and the voltage runs "
                   "away numerically. This is an artefact of the island, not a real overvoltage"
                   % (", ".join(str(b) for b in bb[:6]), "is" if len(bb) == 1 else "are",
                      (" (machines in the island: %s)" % ", ".join(mach[:6])) if mach else ""))
    oos = [x for x in P.get("oos", []) if x[1] in near(bm, named, 1) or x[2] in near(bm, named, 1)] if named else []
    if oos:
        out.append("OUT OF STEP: PSS/E reported an out-of-step condition on %s at t=%.3f s"
                   % (", ".join("%d-%d" % (a, b) for _t, a, b in oos[:3]), oos[0][0] or 0))
    if P.get("nc"):
        s, back = nc_text(P["nc"], A["state"], A["rdir"])
        _w = nc_window(P["nc"])
        s = ("NETWORK NOT CONVERGED: PSS/E's network solution failed %s; the values %s are not a solution"
             % (s, "at that step" if _w[1] is not None and _w[2] - _w[1] < 1e-6 else "in that window"))
        if back:
            s += ("; PSS/E does not stop on a failed step (it carries on from the unconverged values), and it "
                  "converged again after t=%.3f s through to the end (t=%.1f s), which is why the run still finished"
                  % (nc_window(P["nc"])[2], back))
        out.append(s)
    if A["stab"] and not A["offscale"] and not out:
        sgf = [b for b in named if str(b).startswith("999") or
               re.search(r"NEWGEN|PROJ|SGF", str(bm["bus"].get(b, ("", "", ""))[2]).upper())]
        if sgf:
            rate = None
            if of and of[0] == "series":
                for a in of[2]:
                    if set(_buses_in(a["title"])) & set(sgf) and a["rate"] is not None:
                        rate = a["rate"]
                        break
            out.append("ANGLE DRIFT AT THE SGF's OWN BUSES (%s): their voltage angle keeps turning relative to the "
                       "system%s, with no island and no out-of-step reported by PSS/E. That is the inverter model's "
                       "terminal angle (its PLL) running at a different frequency after the fault, not a "
                       "synchronous machine slipping poles. Check the SGF's P and Q in the plot: if they settle, it "
                       "is a measurement artefact of the inverter buses"
                       % (", ".join(str(b) for b in sorted(sgf)[:4]),
                          (" at about %.0f deg/s (%.3f Hz)" % (rate, rate / 360.0)) if rate is not None else ""))
        else:
            out.append("ANGLE TURNING: %s -- a machine or bus kept turning against the system (lost synchronism, or "
                       "no solution at that bus); PSS/E reported no island or out-of-step for it"
                       % "; ".join(A["stab"][:2]))
    if of and of[0] == "series" and of[1]:
        v = of[1][0]
        out.append("FIRST TO RUN AWAY: %s -- above %.1f pu from %s, above %.0f pu from %s, peak %.3g pu at t=%.3f s"
                   % (_short(v["title"], 40), RUNAWAY_PU, when_txt(v["t15"], ev), OFFSCALE_PU,
                      when_txt(v["t5"], ev), v["peak"] or 0, v["tpk"] or 0))
    if not out:
        out.append("NOT DETERMINED from the logs: no island, out-of-step or non-convergence reported -- look at "
                   "the plot of %s" % (", ".join(lab for lab, _p in A["offscale"][:2]) or "the named channel"))
    return ". ".join(out) + "."


# ------------------------------------------------------------ root cause --
_VERB = {"CRASHED": "crashes", "OFF-SCALE": "goes off-scale", "SOLUTION LOST": "loses its solution",
         "NOT CONVERGED": "does not converge"}


def _bad(X):
    return X["kind"] in _VERB


def _role(X):
    """'base' | 'egfoff' | 'proj' -- from the case's results folder and suffix in CASES."""
    for lab, res, suf in CASES:
        if lab == X["case"]:
            return "base" if "base" in res.lower() else "egfoff" if "egfoff" in suf.lower() else "proj"
    return "proj"


def _cases(xs):
    return ", ".join(X["case"].split(" (")[0] for X in xs)


def case_pattern(A, others):
    """Which cases of this fault fail and which run -- and what that says."""
    if not others:
        return ""
    allr = [A] + list(others)
    base = [X for X in allr if _role(X) == "base"]
    bad = [X for X in allr if _bad(X)]
    ok = [X for X in allr if X["kind"].startswith("ran")]
    okw = ""
    if ok:
        marg = [X for X in ok if X["psse"] and X["psse"]["nc"]]
        okw = "; it runs in %s" % _cases(ok)
        if marg:
            n, a, b, _e, _bk = nc_window(marg[0]["psse"]["nc"])
            okw += (" -- only just: PSS/E's network solution failed there too (%d time(s), to t=%.3f s)" % (n, b)
                    if b is not None else "")
    if base and _bad(base[0]):
        return ("the base case (no SGF) %s too, so it is an existing base case problem, not caused by the SGF%s"
                % (_VERB[base[0]["kind"]], okw))
    if base and bad:
        s = "it fails only with the SGF in service (%s); the base case runs" % _cases(bad)
        eo = [X for X in allr if _role(X) == "egfoff"]
        if eo and not _bad(eo[0]) and any(_role(X) == "proj" for X in bad):
            s += "; with the EGF off (Scenario 2) it runs too, so it takes the SGF and the EGF in service together"
        return s
    return ""


def last_switch(ev):
    xs = [x for x in (ev.get("clear"), ev.get("reclose")) if x is not None]
    return max(xs) if xs else ev.get("fault")


def late_nc(nc, ev):
    """(failed steps more than NONCONV_AFTER_S after the last switching, the last one, that switching) or None."""
    ls = last_switch(ev or {})
    if ls is None or not nc:
        return None
    after = [t for t in nc if t is not None and t > ls + NONCONV_AFTER_S]
    return (len(after), max(after), ls) if after else None


_RX_NCB = re.compile(br"Network not converged at TIME\s*=\s*(-?[\d.]+(?:[Ee][-+]?\d+)?)")


def nc_scan(path):
    """PSS/E's 'Network not converged' times in one fault log -- a fast pass, nothing else read."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception:
        return []
    out = []
    for x in _RX_NCB.findall(data):
        v = _num(x.decode("ascii", "replace"))
        if v is not None:
            out.append(v)
    return out


def runaway(A):
    """[(bus, peak pu -- 1e9 for PSS/E's '****' overflow --, first time above RUNAWAY_PU)], worst first:
       PSS/E's own 'outside of band' lines, the study's off-scale list, and the .out when read."""
    P = A["psse"] or {}
    pk, t1 = {}, {}

    def put(b, v, t):
        if b is None:
            return
        v = 1e9 if v is None else v
        if v > pk.get(b, -1.0):
            pk[b] = v
        if t is not None and (b not in t1 or t < t1[b]):
            t1[b] = t
    for t, b, v, _nm in P.get("band_hi", []):
        put(b, v, t)
    for lab, v in A.get("offscale") or []:
        bs = _buses_in(lab)
        if bs:
            put(bs[0], v, None)
    of = A.get("out_facts")
    if of and of[0] == "series":
        for v in of[1][:8]:
            bs = _buses_in(v["title"])
            if bs:
                put(bs[0], v["peak"], v["t15"])
    return sorted(((b, pk[b], t1.get(b)) for b in pk), key=lambda x: -x[1])


def _pk(v):
    return "overflow (****)" if v >= 1e8 else ("%.3g pu" % v)


def _nb(n):
    return "1 bus" if n == 1 else "%d buses" % n


def _nc_from(ncw, ev):
    if not ncw or ncw[1] is None:
        return ""
    a = ncw[1]
    if ev.get("fault") is not None and a <= ev["fault"] + 0.01:
        return "from the moment the fault is applied (t=%.3f s)" % a
    if ev.get("clear") is not None and a < ev["clear"] - 1e-3:
        return "during the fault (from t=%.3f s)" % a
    if ev.get("reclose") is not None and a >= ev["reclose"] - 1e-3:
        return "from the reclose (from t=%.3f s)" % a
    return "from t=%.3f s" % a


def explain(A, others):
    """(root cause, [where it starts], [what to try]) -- the logs, the bus map and the .dyr read
       together. ('', [], []) where nothing can be said beyond the evidence."""
    if A.get("running") or not (_bad(A)):
        return "", [], []
    key = "_explained"
    if key in A:
        return A[key]
    bm, ev, P = A["bm"], A["ev"], A["psse"] or {}
    fr = A["frow"] or {}
    try:
        fb = int(str(fr.get("fault_bus") or "").split(".")[0])
    except ValueError:
        fb = None
    cyc = (fr.get("clear_cycles") or "").strip()
    cut = fault_cuts(A["frow"])
    top = runaway(A)
    nc = P.get("nc") or []
    ncw = nc_window(nc) if nc else None
    ncf = _nc_from(ncw, ev)
    pat = case_pattern(A, others)
    W, T, head = [], [], ""
    mach, mods, vsc, pocket, h, src = [], [], [], None, None, None
    on_map = [x for x in top if x[0] in bm["adj"]] if bm["ok"] else []
    if on_map:
        src = on_map[0][0]               # the worst bus the map knows (a bus off the map cannot be placed)
        dist = hops_map(bm, fb, 40) if (fb is not None and fb in bm["adj"]) else {}
        h = dist.get(src)
        pocket = behind(bm, src, fb, cut)
        if pocket:
            area = sorted(pocket)
        else:
            nb = hops_map(bm, src, PLANT_HOPS)
            area = sorted(x for x in nb if x == src or (bm["bus"].get(x, (999.0,))[0] or 999.0) <= 35.0)
        mach = [(x, i) for x in area for i in bm["mach"].get(x, [])]
        for x, _i in mach:
            for m in models_at(A["project"], x):
                if m not in mods:
                    mods.append(m)
        vsc = [b for b, _p, _t in top[:8] if "VSC" in str(bm["bus"].get(b, ("", "", ""))[2]).upper()]
        W.append("worst: %s" % ", ".join("%s %s%s" % (bus_name(bm, b), _pk(p), (" (t=%.3f s)" % t) if t is not None else "")
                                         for b, p, t in top[:3]))
        W.append("%s is %s from the faulted bus %s" % (
            bus_name(bm, src), _nb(h) if h is not None else "more than 40 buses (or not on the map)",
            bus_name(bm, fb) if fb is not None else "?"))
        if pocket:
            W.append("RADIAL: its only path to the system runs through the faulted bus (%s behind it: %s)"
                     % (_nb(len(pocket)), ", ".join(str(b) for b in sorted(pocket)[:10])))
        if mach:
            W.append("machine(s) there: %s" % ", ".join("%s-%s [%s]" % (b, i, _short(bm["bus"].get(b, ("", "", ""))[2], 14))
                                                         for b, i in mach[:8]))
            W.append("their models (.dyr): %s" % (_models_txt(mods, 10) if mods else
                                                   "none found (no .dyr read, or not modelled there)"))
        if vsc:
            W.append("VSC HVDC converter bus(es) among them: %s" % ", ".join(bus_name(bm, b) for b in vsc[:4]))
    elif top:
        W.append("worst: %s (%s)" % (", ".join("%s %s" % (b, _pk(p)) for b, p, _t in top[:3]),
                                     "not on the bus map" if bm["ok"] else "no flags\\BUS_MAP.csv to place them"))
    dcl = [(t, ln) for t, ln in P.get("dc", []) if re.search(r"no longer controlling|block|commutat", ln, re.I)]
    if dcl:
        W.append("DC links reported (often a symptom of the collapse, not its start): %s" % "; ".join("t=%s %s" % (("%.3f" % t) if t is not None else "?", _short(ln, 70)) for t, ln in dcl[:2]))
    if ncw and ncw[1] is not None:
        W.append("network solution failed %s %s" % (nc_text(nc)[0], ("-- " + ncf) if ncf else ""))
    # did it start at the reclose?
    rec = ev.get("reclose")
    first = sorted([t for t, _a, _b in P.get("oos", []) if t is not None] +
                   [t for t, _b, v, _n in P.get("band_hi", []) if t is not None and (v is None or v > RUNAWAY_PU)])
    at_rec = bool(rec is not None and first and rec - 0.005 <= first[0] <= rec + 0.15)
    if at_rec:
        W.append("it starts at the reclose: first out-of-step / runaway at t=%.3f s, the reclose at t=%.3f s" % (first[0], rec))
    # a model PSS/E could not load, at or near it
    miss = []
    if src is not None and bm["ok"]:
        zone = set(hops_map(bm, src, PLANT_HOPS)) | set(pocket or ())
        for nm, recs in missing_models(A["project"], [A["rdir"]]):
            for b, ints, path, first_tok in recs:
                if (b is not None and b in zone) or (set(ints) & zone):
                    miss.append(nm)
                    break
        if miss:
            W.append("NOT LOADED there: %s (\"NOT ACCESSIBLE\" in the init log) -- that plant runs without it"
                     % ", ".join(miss))
    plant = ""
    if src is not None:
        nm0 = bm["bus"].get(src, ("", "", ""))[2] if bm["ok"] else ""
        plant = (("the %s plant (machine(s) %s; models %s)" % (
            _short(nm0, 16) if nm0 else src, ", ".join("%s-%s" % (b, i) for b, i in mach[:4]), _models_txt(mods, 5) or "?"))
            if mach else ("bus %s" % bus_name(bm, src)))
    in_island = bool(src is not None and src in (A.get("pocket") or set()))
    if src is None or in_island:
        head = ""                        # nothing placed, or the fault's own island explains it (evidence)
    elif at_rec:
        oos = [x for x in P.get("oos", [])][:2]
        head = ("it starts at the RECLOSE (t=%.3f s): %s, %s from the faulted bus, %s as the line recloses; "
                "PSS/E's network solution then fails%s" % (
                    rec, plant, _nb(h) if h is not None else "some buses",
                    ("goes out of step (%s)" % ", ".join("%d-%d" % (a, b) for _t, a, b in oos)) if oos else "runs away",
                    (" " + nc_text(nc)[0]) if nc else ""))
    elif vsc:
        head = ("the voltage runs away at the VSC HVDC converter buses (%s), %s from the faulted bus: the VSC "
                "converter model next to the fault does not hold its voltage after the fault and PSS/E's network "
                "solution fails around it %s -- a DC converter model problem, not a real overvoltage" % (
                    ", ".join(str(b) for b in vsc[:3]), _nb(h) if h is not None else "near", ncf))
    elif pocket:
        conv = any(_CONVERTER.match(m) for m in mods)
        head = ("%s is RADIAL on the faulted bus %s: its only path to the system runs through it. For the %s-cycle "
                "fault its terminal voltage is held near zero%s; PSS/E's network solution fails %s, and at clearing "
                "the unconverged solution runs away at the plant's buses (%s %s) -- a numerical failure of the "
                "plant's %smodel at a bolted fault on its only connection, not a real overvoltage" % (
                    plant[0].upper() + plant[1:], fb, cyc or "?",
                    (" and its converter controls go to their low-voltage limits" if conv else ""),
                    ncf or "around it", src, _pk(on_map[0][1]), "converter " if conv else ""))
    elif h == 0:
        head = ("the voltage runs away first at the faulted bus %s itself%s; PSS/E's network solution fails %s" % (
            bus_name(bm, src), (" -- next to %s" % plant) if mach else "", ncf or "around it"))
    else:
        head = ("the voltage runs away first at %s, %s from the faulted bus%s; PSS/E's network solution fails %s" % (
            bus_name(bm, src), _nb(h) if h is not None else "more than 40 buses",
            (": " + plant) if mach else "", ncf or "around it"))
    if head and miss:
        head += "; the plant's %s model is NOT loaded (NOT ACCESSIBLE) in this run" % ", ".join(miss)
    if pat:
        if head:
            head = head + ". " + pat[0].upper() + pat[1:]
        else:
            # nothing placed on the map (or the fault's own island explains it -- see the evidence):
            # the case pattern is still worth having, beside the evidence
            W.insert(0, "cases: " + pat)
    if head:
        head = "ROOT CAUSE: " + head
    # what to try
    if nc or A["crashed"]:
        T.append("rerun %s with DELT_CYCLES = 8 (1/8 cycle) in z7_main_f.py, or with SOLVER_RETRY_ON_NONCONV = True "
                 "(up to 600 iterations, acceleration down to 0.1): if it then finishes, the failure is numerical, "
                 "not a real instability" % A["fault"])
    if mach and not vsc:
        T.append("as a test, switch off machine(s) %s (or trip them at the fault): if the run then finishes, this "
                 "plant's model is the cause" % ", ".join("%s-%s" % (b, i) for b, i in mach[:6]))
    if vsc:
        T.append("check the VSC HVDC model's data and limits near bus %s; as a test, block the DC line at the fault" % vsc[0])
    if at_rec:
        T.append("as a check, run %s without the reclose" % A["fault"])
    if miss:
        T.append("get the %s model (DLL) loaded -- PSS/E prints NOT ACCESSIBLE when the model library is not "
                 "loaded with the case -- and rerun" % ", ".join(miss))
    if pat.startswith("the base case"):
        T.append("in the report: an existing base case issue -- the base case without the SGF fails the same way")
    A[key] = (head, W, T)
    return A[key]


# ---------------------------------------------------------------- report --
def block(A, others):
    L = []
    st, ev, P, bm = A["state"], A["ev"], A["psse"], A["bm"]
    L.append(BAR)
    L.append(" %s  %s  --  %s  --  %s" % (A["project"], A["fault"], A["case"], A["kind"]))
    L.append("   %s" % fault_text(A["frow"]))
    L.append("   folder: %s" % _rel(A["rdir"]))
    L.append(SUB)
    head, where, tries = explain(A, others)
    if head:
        L.append(" ROOT CAUSE")
        for ln in _wrap(head[len("ROOT CAUSE: "):], 74):
            L.append("   " + ln)
        L.append("")
    if where:
        L.append(" WHERE IT STARTS")
        for w in where:
            for i, ln in enumerate(_wrap(w, 72)):
                L.append(("   - " if i == 0 else "     ") + ln)
        L.append("")
    if tries:
        L.append(" WHAT TO TRY")
        for w in tries:
            for i, ln in enumerate(_wrap(w, 72)):
                L.append(("   - " if i == 0 else "     ") + ln)
        L.append("")
    L.append(" EVIDENCE FROM THE LOGS" if head else " MOST LIKELY REASON")
    for ln in _wrap(reason(A), 74):
        L.append("   " + ln)
    L.append("")
    if ev.get("fault") is not None or ev.get("clear") is not None:
        L.append(" EVENTS   fault on %s   cleared %s%s" % (
            ("t=%.3f s" % ev["fault"]) if ev.get("fault") is not None else "?",
            ("t=%.3f s" % ev["clear"]) if ev.get("clear") is not None else "?",
            ("   reclose t=%.3f s" % ev["reclose"]) if ev.get("reclose") is not None else ""))
    if A["crashed"]:
        full, tend = A["typical"]
        L.append(" MARKERS  %s attempt(s); no .done%s" % (
            st["attempts"] if st["attempts"] is not None else "?",
            ("; %s .out %s of a typical %s -> %s about t = %.1f s of %.1f s"
             % ("growing" if A["running"] else "partial", _mb(st["out_size"]), _mb(full),
                "so far at" if A["running"] else "it stopped at", tend * st["out_size"] / float(full), tend))
            if (st["out_size"] and full and tend) else ("; partial .out %s" % _mb(st["out_size"])) if st["out_size"] else
            "; no .out at all (it died before writing any output)"))
        for r in A["status"]:
            L.append("          %s" % r)
        if A["atts"]:
            L.append(" ATTEMPTS (logs\\RUN_%s_w*.log)" % A["fault"])
            for i, a in enumerate(A["atts"], 1):
                L.append("   %d. worker w%s  started %s%s" % (
                    i, a["worker"] if a["worker"] is not None else "?", a["start"] or "?",
                    ("   solver %s" % a["solver"]) if a["solver"] else ""))
                if a["ok"]:
                    L.append("      last step done : [%s] %s" % a["ok"])
                if a["simt"] is not None:
                    L.append("      last sim time  : %.2f of %.2f s (%s), last log line %s" % (
                        a["simt"], a["tend"] or 0, a["phase"], a["last"].strftime("%H:%M:%S") if a["last"] else "?"))
                if a["nan_fault"]:
                    L.append("      the faulted-bus voltage during the fault was NaN")
                x = exit_for(a, A["exits"])
                L.append("      worker exit    : %s" % ("%s  %s" % (x[0], x[1]) if x else
                                                       "none recorded within %d min of its last line" % EXIT_MATCH_MIN))
                for b in a["bad"][:5]:
                    L.append("      ! %s" % b)
                if not a["ok"] and a["tail"]:
                    L.append("      last lines     : %s" % " | ".join(list(a["tail"])[-3:]))
        else:
            L.append(" ATTEMPTS  no logs\\RUN_%s_w*.log found" % A["fault"])
        if A["pocket"]:
            L.append(" ISLAND (fault list + bus map): %d bus(es) left with no path to the system: %s" % (
                len(A["pocket"]), ", ".join(str(b) for b in sorted(A["pocket"])[:12])))
        ic = A["init"]
        if ic.get("have"):
            if ic["names"]:
                oth = ic["other"]
                L.append(" INIT LOG  models NOT ACCESSIBLE: %s%s" % (
                    ", ".join(ic["names"]),
                    (" -- the SAME lines are in the init log of %s, which finished, so they are NOT why this run "
                     "crashed" % oth[0]) if (oth and set(ic["names"]) <= set(oth[1])) else
                    (" -- a finished run (%s) has %s" % (oth[0], ", ".join(oth[1]) or "none")) if oth else ""))
            if ic["nc"]:
                L.append("          'network not converged' %d time(s) during init" % ic["nc"])
    else:
        if A["offscale"]:
            L.append(" OFF-SCALE (study): %s" % ", ".join("%s %.1f pu" % (lab, pk) for lab, pk in A["offscale"][:8]))
        if A["stab"]:
            L.append(" SOLUTION LOST (study):")
            for it in A["stab"][:6]:
                L.append("   %s" % _short(it, 150))
        if st.get("diverged"):
            L.append(" DIVERGED NOTE: %s" % _short(st["diverged"], 150))
        if A["pocket"]:
            mach = sorted(set("%s-%s" % (b, i) for b in A["pocket"] for i in bm["mach"].get(b, [])))
            dcs = [b for b in sorted(A["pocket"]) if b in DC_TERMINALS]
            L.append(" ISLAND (fault list + bus map): %d bus(es) left with no AC path to the system: %s%s%s" % (
                len(A["pocket"]), ", ".join(str(b) for b in sorted(A["pocket"])[:12]),
                ("; machines %s" % ", ".join(mach[:8])) if mach else "",
                ("; DC terminal %s -- %s" % (dcs[0], DC_TERMINALS[dcs[0]])) if dcs else ""))
        elif not A["frow"]:
            L.append(" ISLAND: not checked (%s is not in the fault list)" % A["fault"])
        elif bm["ok"]:
            L.append(" ISLAND (fault list + bus map): none -- the fault's switching leaves every bus connected")
        else:
            L.append(" ISLAND: not checked (no flags\\BUS_MAP.csv)")
        of = A.get("out_facts")
        if of and of[0] == "series":
            if of[1]:
                L.append(" FROM THE .out -- voltages above %.0f pu (or NaN), earliest first:" % OFFSCALE_PU)
                for v in of[1][:8]:
                    L.append("   %-34s pre %.3f pu; >%.1f pu %s; >%.0f pu %s; peak %.3g pu at t=%.3f s%s" % (
                        _short(v["title"], 34), v["pre"] if v["pre"] == v["pre"] else float("nan"), RUNAWAY_PU,
                        when_txt(v["t15"], ev), OFFSCALE_PU, when_txt(v["t5"], ev), v["peak"] or 0, v["tpk"] or 0,
                        ("; NaN from %s" % when_txt(v["tnan"], ev)) if v["tnan"] is not None else ""))
            if of[2]:
                L.append(" FROM THE .out -- angles turning through more than %.0f deg:" % ANGLE_SPAN_DEG)
                for a in of[2][:8]:
                    L.append("   %-34s span %.0f deg; past 360 deg %s; %s" % (
                        _short(a["title"], 34), a["span"], when_txt(a["t360"], ev),
                        ("still turning %.1f deg/s at t=%.1f s (%.3f Hz off the system)"
                         % (a["rate"], A.get("out_upto") or 0, a["rate"] / 360.0))
                        if a["rate"] is not None and abs(a["rate"]) > 1.0 else
                        "settled by t=%.1f s" % (A.get("out_upto") or 0)))
    if A.get("out_why"):
        L.append(" .out NOT READ: %s" % A["out_why"])
    if P:
        L.append(" PSS/E IN THE FAULT WINDOW (%s), last time %s" % (_rel(P["path"]),
                                                                   ("%.3f s" % P["t_last"]) if P["t_last"] is not None else "?"))
        for t, bl in P["islands"][:4]:
            mach = sorted(set("%s-%s" % (b, i) for b in bl for i in bm["mach"].get(b, [])))
            L.append("   t=%s  buses disconnected (island): %s%s" % (
                ("%.3f" % t) if t is not None else "?", ", ".join(str(b) for b in bl[:12]) + (" ..." if len(bl) > 12 else ""),
                ("; machines %s" % ", ".join(mach[:6])) if mach else ""))
        if P["oos"]:
            L.append("   out of step: %s" % ", ".join("t=%.3f %d-%d" % (t or 0, a, b) for t, a, b in P["oos"][:6]))
        if P["nc"]:
            s, back = nc_text(P["nc"], A["state"], A["rdir"])
            L.append("   network not converged %s%s" % (s, ("; converged at every step after that, through to the end "
                                                            "(t=%.1f s)" % back) if back else ""))
        for t, g, b, nm in P["mtrip"][:6]:
            L.append("   t=%.3f  machine %s at %s tripped" % (t or 0, g, bus_name(bm, b)))
        for t, b in P["bdisc"][:6]:
            L.append("   t=%.3f  bus %s disconnected" % (t or 0, bus_name(bm, b)))
        hi = sorted(P["band_hi"], key=lambda x: (x[0] or 0))[:6]
        for t, b, v, nm in hi:
            L.append("   t=%s  bus %s %s above band: %s" % (("%.3f" % t) if t is not None else "?", b, nm,
                                                         ("%.3g pu" % v) if v is not None else "overflow (****)"))
        if P["genpwr"]:
            L.append("   PSS/E power-unbalance trips (GENPWR): %d" % len(P["genpwr"]))
        for t, ln in P["dc"][:6]:
            L.append("   t=%s  DC: %s" % (("%.3f" % t) if t is not None else "?", ln))
        if A["crashed"] and P["tail"]:
            L.append("   last lines: %s" % " | ".join(list(P["tail"])[-4:]))
    elif A["crashed"] or A["offscale"] or A["stab"]:
        L.append(" PSS/E LOG: no logs\\psse\\%s.txt (PSSE_FAULT_LOG off in the panel?)" % A["fault"])
    if others:
        L.append(" THE SAME FAULT IN THE OTHER CASES")
        for O in others:
            of = O.get("out_facts")
            extra = []
            if O["kind"].startswith("ran") or O["kind"] == "NOT CONVERGED":
                if O["pocket"]:
                    extra.append("island %d bus(es)" % len(O["pocket"]))
                if O["psse"] and O["psse"]["islands"]:
                    extra.append("PSS/E island at t=%.3f" % (O["psse"]["islands"][0][0] or 0))
                if O["psse"] and O["psse"]["nc"]:
                    n, a, b, every, _bk = nc_window(O["psse"]["nc"], O["state"], O["rdir"])
                    extra.append("not converged %d%s" % (n, (" between t=%.3f and %.3f s%s" % (
                        a, b, ", every step" if every else "")) if a is not None else ""))
                if of and of[0] == "series":
                    if of[1]:
                        extra.append("max %.3g pu (%s)" % (of[1][0]["peak"] or 0, _short(of[1][0]["title"], 24)))
                    else:
                        extra.append("no voltage above %.0f pu" % OFFSCALE_PU)
                    if of[2]:
                        extra.append("%d angle(s) turning > %.0f deg (largest %.0f deg, %s)"
                                     % (len(of[2]), ANGLE_SPAN_DEG, of[2][0]["span"], _short(of[2][0]["title"], 20)))
                    else:
                        extra.append("no angle turning > %.0f deg" % ANGLE_SPAN_DEG)
            L.append("   %-30s %s%s" % (O["case"], O["kind"], ("; " + "; ".join(extra)) if extra else ""))
    L.append("")
    return L


def _wrap(s, n):
    words, out, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    return out


# ------------------------------------------------------------------ main --
def find_targets(folders, want):
    """{(project, fault): {case: kind}} for every crashed / off-scale / solution-lost run -- and, with
       SCAN_NONCONV, every finished run whose network solution was still failing NONCONV_AFTER_S after
       the last switching (its PSS/E fault log, read fast)."""
    hits = collections.defaultdict(dict)
    for proj, case, rdir in folders:
        n_scan = n_nc = 0
        t_scan = time.time()
        od = os.path.join(rdir, "outs")
        ids = set()
        for p in glob.glob(os.path.join(od, "*.out")) + glob.glob(os.path.join(od, "*.attempts")):
            ids.add(os.path.splitext(os.path.basename(p))[0])
        for fid in sorted(ids, key=_fkey):
            if fid.upper().startswith("FLAT"):
                continue
            if want and fid.upper() not in want:
                continue
            st = run_state(rdir, fid)
            scen = read_scen(rdir, fid)
            if want:
                hits[(proj, fid)][case] = rdir
                continue
            crashed = not st["done"] and not st["partial"] and not (scen and scen["verdict"])
            off = _crit(scen, "Bus voltages within scale")
            stab = _crit(scen, "System stability")
            if crashed or (off and off[0] == "FAIL") or (stab and stab[0] == "FAIL"):
                hits[(proj, fid)][case] = rdir
            elif SCAN_NONCONV and st["done"]:
                lp = os.path.join(rdir, "logs", "psse", fid + ".txt")
                if os.path.isfile(lp):
                    n_scan += 1
                    nc = nc_scan(lp)
                    if nc and late_nc(nc, event_times(fault_list(proj, rdir).get(fid.upper()), st, None)):
                        hits[(proj, fid)][case] = rdir
                        n_nc += 1
        if SCAN_NONCONV and n_scan:
            print("[why] %s %s: %d finished run(s) scanned for non-convergence, %d still failing %.1f s after the "
                  "last switching (%.0f s)" % (proj, case.split(" (")[0], n_scan, n_nc, NONCONV_AFTER_S,
                                              time.time() - t_scan))
    return hits


def main():
    global READ_OUT, SCAN_NONCONV
    t0 = time.time()
    if "--noscan" in sys.argv:
        SCAN_NONCONV = False
    if "--quick" in sys.argv:
        READ_OUT = False
        print("[why] --quick: logs and score files only, no .out read")
    want = _want_faults()
    folders = run_folders()
    if not folders:
        print("[why] no run folders found under %s -- check ROOT, PROJECTS and CASES" % _root())
        return 1
    print("[why] %d run folder(s): %s" % (len(folders), ", ".join("%s %s" % (p, c.split(" (")[0]) for p, c, _d in folders)))
    hits = find_targets(folders, want)
    if not hits:
        print("[why] nothing to explain: no crashed run, no off-scale voltage, no 'System stability' FAIL and no "
              "run still failing to converge%s"
              % (" among %s" % ", ".join(sorted(want)) if want else ""))
        return 0
    by_proj = collections.defaultdict(list)
    for (proj, fid), cases in hits.items():
        by_proj[proj].append(fid)
    print("[why] %d fault(s) to explain: %s" % (len(hits), "; ".join(
        "%s %s" % (p, ", ".join(sorted(fs, key=_fkey))) for p, fs in sorted(by_proj.items()))))
    if READ_OUT and _dyntools() is None:
        print("[why] .out files will not be read: %s" % _DYN["why"])
    lines, rows = [], []
    head = [BAR, " WHY DID THESE RUNS CRASH OR LOSE THEIR SOLUTION?   %s" % time.strftime("%Y-%m-%d %H:%M"),
            " study folder %s" % _root(), BAR, ""]
    # the models PSS/E could not load -- they do not run in ANY fault of these cases
    mm = []
    for proj in sorted(set(p for p, _c, _d in folders)):
        rd = [d for p, _c, d in folders if p == proj]
        ml = missing_lines(proj, rd, bus_map(rd[0]))
        if ml:
            mm.append("  %s" % proj)
            mm += ml
    if mm:
        head += [" MODELS PSS/E COULD NOT LOAD (\"NOT ACCESSIBLE\" in the init logs) -- they do not run in ANY fault:"]
        head += mm + [""]
    summary = []
    plan = []
    for proj in sorted(by_proj):
        fdirs = [(c, d) for p, c, d in folders if p == proj]
        for fid in sorted(by_proj[proj], key=_fkey):
            cases = hits[(proj, fid)]
            for case, rdir in fdirs:
                if case not in cases:
                    continue
                print("[why] %s %s %s ..." % (proj, fid, case.split(" (")[0]))
                A = analyse_run(proj, case, rdir, fid)
                others = []
                if COMPARE_OTHER_CASES:
                    others = [analyse_run(proj, c2, d2, fid) for c2, d2 in fdirs if c2 != case]
                plan.append((A, others))
    # THE .out READS, ALL AT ONCE: the runs to explain, and the other cases only
    # with COMPARE_READ_OUT. Each run is read once however often it is shown.
    want_read = collections.OrderedDict()
    for A, others in plan:
        win = "angle" if (A["stab"] and not A["offscale"]) else None
        want_read[(A["rdir"], A["fault"])] = (A, win)
    if COMPARE_READ_OUT:
        for A, others in plan:
            win = "angle" if (A["stab"] and not A["offscale"]) else None
            for O in others:
                want_read.setdefault((O["rdir"], O["fault"]), (O, win))
    jobs = [j for j in (read_plan(X, w) for X, w in want_read.values()) if j]
    for key, (facts, upto, why) in run_reads(jobs).items():
        X = want_read[key][0]
        X["out_facts"], X["out_upto"], X["out_why"] = facts, upto, why
    for A, others in plan:
        proj, fid, case = A["project"], A["fault"], A["case"]
        lines += block(A, others)
        rsn = reason(A)
        hd, wh, tr = explain(A, others)
        summary.append("  %-14s %-6s %-30s %-14s %s" % (proj, fid, case.split(" (")[0], A["kind"],
                                                       _short(hd[len("ROOT CAUSE: "):] if hd else rsn, 150)))
        rows.append([proj, fid, case, A["kind"], hd[len("ROOT CAUSE: "):] if hd else "", " | ".join(wh),
                     " | ".join(tr), rsn])
    od = _abs(OUT_DIR)
    try:
        os.makedirs(od)
    except Exception:
        pass
    txt = "\n".join(head + [" SUMMARY"] + summary + [""] + lines) + "\n"
    p1 = os.path.join(od, "WHY_UNSTABLE.txt")
    p2 = os.path.join(od, "WHY_UNSTABLE.csv")
    with open(p1, "w") as fh:
        fh.write(txt)
    with open(p2, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["project", "fault", "case", "what", "root cause", "where it starts", "what to try",
                    "evidence from the logs"])
        w.writerows(rows)
    print("")
    print("\n".join([" SUMMARY"] + summary))
    print("")
    print("[why] written %s and %s (%.0f s)" % (p1, p2, time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
