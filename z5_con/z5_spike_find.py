# -*- coding: utf-8 -*-
"""z5_spike_find.py -- WHERE DO THE >1.2 PU OVERVOLTAGES COME FROM?

Python 3.4, stdlib only. Put it in the study root (beside z5_cmp_all_con.py)
and run:

    C:\\Python34\\python.exe z5_spike_find.py

PART A  (no PSS/E needed, seconds)
    Reads every 02_VIOLATIONS_<KIND>_<project>.csv the scoring already wrote
    (Base\\results_base\\<proj>_spp\\ and/or Projects\\results_proj\\...) and
    ranks the overvoltage buses:
      * how many faults each bus fails on, worst pu, worst fault
      * SPIKE (<= 2 cycles above the limit, at the clearing instant) vs SWING
      * near the fault (0-1 hops) or remote
      * GROUPS of buses that fail on the same faults -- one group, one source
      * a likely-cause hint per bus

PART B  (READ_OUTS = True -- needs PSS/E's dyntools, i.e. the PSS/E Python)
    For the worst faults, opens the .out and finds WHO drives the spike:
      * clearing time from the faulted bus's own channel (FLT<bus> V)
      * each bad bus: peak, seconds after clearing, time above the limit
      * every machine with a Q channel: Mvar injected ABOVE its pre-fault
        value at the moment of the spike, and its own terminal voltage
      -> the top suspects. If no monitored machine is pushing vars, the
         spike is the NETWORK (capacitive: shunts / line charging / the
         clearing step) -- or a machine that has no Q channel.

PART C  (NEARBY = True -- needs PSS/E's psspy)
    Opens the case (.sav) and, for the top buses of ALL projects together,
    lists what is electrically near each one -- generators (IBR / synchronous
    / SVC, from the .dyr models), fixed and switched shunts (cap banks and
    reactors), FACTS, and lines with big charging -- closest first, with what
    to check on each. A machine Part B caught pushing vars is marked
    CONFIRMED.

Output, per results folder:   SPIKE_FINDER_<KIND>_<proj>.txt
                              SPIKE_BUSES_<KIND>_<proj>.csv
                              SPIKE_SUSPECTS_<KIND>_<proj>.csv   (Part B)
in the study root:             SPIKE_FINDER_ALL.txt  (every project + the buses of all
                                                     projects together)
                              SPIKE_NEARBY_<KIND>.txt / .csv       (Part C)

Nothing is re-run or re-scored; the results folders are only read (plus the
report files above).
"""
from __future__ import print_function
import os, sys, re, csv, glob, time

# =========================== SETTINGS ======================================
ROOT          = ""            # "" = the folder this file is in (the study root)
CASES         = ["base"]      # "base" and/or "proj"
PROJECTS      = []            # [] = every <proj>_spp folder found; or ["SantaFe"]
LIMIT_PU      = 1.20          # the SPP overshoot limit
SPIKE_S       = 2.0 / 60.0    # above the limit for <= this = SPIKE (same as the engine)
TOP_BUSES     = 40            # buses listed in the ranking (the CSV has all of them)
GROUP_JACCARD = 0.60          # buses whose fault sets overlap this much = one group

READ_OUTS     = True          # PART B -- needs PSS/E dyntools. False = Part A only
OUT_FAULTS    = "auto"        # "auto" = the worst OUT_FAULTS_N faults | ["F01", "F26"]
OUT_FAULTS_N  = 5             # dyntools is slow on big .outs: ~minutes per file
BUSES_PER_FAULT = 5           # worst buses traced per fault
SUSPECT_MVAR  = 10.0          # a machine pushing < this many Mvar extra is not a suspect
SUSPECT_TOP   = 10            # suspects listed per fault
SYS_MVA_BASE  = 100.0         # machine P/Q channels are pu on this base
MATCH_S       = 0.10          # a machine's Q peak within this of the bus peak = "in step"

NEARBY        = True          # PART C -- needs PSS/E (psspy): opens the case and lists what is
                              # electrically near each top bus: generators, cap banks, reactors,
                              # switched shunts, SVC/STATCOMs, high-charging lines
NEAR_BUSES_N  = 15            # top buses (all projects together) looked at
NEAR_HOPS     = 3             # search this many buses out from each bad bus
NEAR_TOP      = 15            # devices listed per bus, electrically closest first
LINE_CHG_MVAR = 20.0          # lines with at least this much charging are listed
CASE_SAV      = {}            # {} = read BASE_SAV / PROJ_SAV from z5_cmp_all_con.py, e.g.
                              # {"base": r"C:\KV\Base\DIS2201-25SP-G03-CQ_Mitigated.sav"}
CASE_DYR      = {}            # same, for the .dyr (machine model names: IBR / SYNC / SVC)
# ===========================================================================

KINDS = {"base": ("Base", ("results_base", "results"), "BASE"),
         "proj": ("Projects", ("results_proj", "results"), "PROJ")}


def _root():
    if ROOT:
        return ROOT
    try:
        return os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
    except NameError:
        return os.getcwd()


def _f(x, d=None):
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _bus(el):
    m = re.search(r"\d{4,}", str(el))
    return int(m.group(0)) if m else None


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])


def find_folders():
    """[(kind, proj, results_dir, violations_csv)]"""
    root, out = _root(), []
    for kind in CASES:
        if kind not in KINDS:
            print("[find] unknown case %r -- use 'base' or 'proj'" % kind)
            continue
        sub, rnames, tag = KINDS[kind]
        seen = set()
        for rn in rnames:
            for d in sorted(glob.glob(os.path.join(root, sub, rn, "*_spp*"))):
                if not os.path.isdir(d):
                    continue
                proj = re.sub(r"_spp.*$", "", os.path.basename(d))
                if PROJECTS and proj not in PROJECTS:
                    continue
                if proj in seen:
                    continue
                full = sorted(glob.glob(os.path.join(d, "02_VIOLATIONS_%s*.csv" % tag)))
                pick = [p for p in full if "_SELECTED" not in os.path.basename(p)] or full
                if not pick:
                    pick = sorted(glob.glob(os.path.join(d, "reports", "SPP_VIOLATIONS*_%s*.csv" % tag)))
                if not pick:
                    print("[find] %s: no 02_VIOLATIONS csv -- not scored yet?" % d)
                    continue
                seen.add(proj)
                out.append((kind, proj, d, pick[0]))
    return out


def read_overshoots(path):
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("violation") or "").strip().lower() != "overshoot":
                continue
            v = _f(r.get("value"))
            b = _bus(r.get("element"))
            if v is None or b is None:
                continue
            rows.append({"fault": (r.get("fault_id") or "").strip(),
                         "element": (r.get("element") or "").strip(),
                         "bus": b, "value": v, "time": _f(r.get("time_s")),
                         "area": (r.get("area") or "").strip(),
                         "area_name": (r.get("area_name") or "").strip(),
                         "hops": _f(r.get("hops_from_fault")),
                         "fault_bus": (r.get("fault_bus") or "").strip(),
                         "dur": _f(r.get("above_limit_s"))})
    return rows


# ----------------------------- PART A --------------------------------------
def rank_buses(rows, n_faults_total):
    by = {}
    for r in rows:
        b = by.setdefault(r["bus"], {"bus": r["bus"], "faults": set(), "worst": 0.0,
                                     "worst_fault": "", "durs": [], "hops": [],
                                     "area": r["area"], "area_name": r["area_name"],
                                     "label": r["element"]})
        b["faults"].add(r["fault"])
        if r["value"] > b["worst"]:
            b["worst"], b["worst_fault"] = r["value"], r["fault"]
        if r["dur"] is not None:
            b["durs"].append(r["dur"])
        if r["hops"] is not None:
            b["hops"].append(r["hops"])
        if len(r["element"]) > len(b["label"]):
            b["label"] = r["element"]
    out = []
    for b in by.values():
        n = len(b["faults"])
        spikes = sum(1 for d in b["durs"] if d <= SPIKE_S + 1e-9)
        swings = len(b["durs"]) - spikes
        if not b["durs"]:
            shape = "?"
        elif spikes >= 0.8 * len(b["durs"]):
            shape = "SPIKE"
        elif swings >= 0.8 * len(b["durs"]):
            shape = "SWING"
        else:
            shape = "MIXED"
        mh = _median(b["hops"])
        near = (mh is not None and mh <= 1)
        share = float(n) / max(1, n_faults_total)
        if shape == "SWING":
            hint = "controller swing -- a plant/exciter overshooting; Part B names it"
        elif near and share < 0.25:
            hint = "clearing step at/next to the faulted bus -- network jump, argue or check clearing"
        elif shape == "SPIKE" and share >= 0.25:
            hint = "fails on many faults -> a LOCAL device: shunt/cap/line charging or a fast IBR Q"
        elif shape == "SPIKE":
            hint = "clearing-instant spike, remote -- check shunts / IBR Q at clearing (Part B)"
        else:
            hint = "mixed -- look at Part B for the worst fault"
        b.update({"n": n, "share": share, "spikes": spikes, "swings": swings,
                  "shape": shape, "hops_med": mh, "near": near, "hint": hint,
                  "dur_med": _median(b["durs"])})
        out.append(b)
    out.sort(key=lambda b: (-b["n"], -b["worst"]))
    return out


def group_buses(buses):
    """Greedy: seed = the bus failing most often; members = buses whose fault
       set overlaps the seed's by >= GROUP_JACCARD. One group ~ one source."""
    left = list(buses)
    groups = []
    while left:
        seed = left.pop(0)
        mem, keep = [seed], []
        for b in left:
            inter = len(seed["faults"] & b["faults"])
            uni = len(seed["faults"] | b["faults"]) or 1
            (mem if float(inter) / uni >= GROUP_JACCARD else keep).append(b)
        left = keep
        common = set(seed["faults"])
        for b in mem[1:]:
            common &= b["faults"]
        groups.append((mem, common))
    groups.sort(key=lambda g: (-len(g[0]) * len(g[0][0]["faults"])))
    return groups


def rank_faults(rows):
    by = {}
    for r in rows:
        f = by.setdefault(r["fault"], {"fault": r["fault"], "buses": set(), "worst": 0.0,
                                       "worst_bus": "", "fault_bus": r["fault_bus"]})
        f["buses"].add(r["bus"])
        if r["value"] > f["worst"]:
            f["worst"], f["worst_bus"] = r["value"], r["element"]
    out = list(by.values())
    out.sort(key=lambda f: (-len(f["buses"]), -f["worst"]))
    return out


# ----------------------------- PART B --------------------------------------
_DYN = [None]


def _dyntools():
    if _DYN[0] is not None:
        return _DYN[0] or None
    try:
        import dyntools                               # already importable
        _DYN[0] = dyntools
        return dyntools
    except Exception:
        pass
    pyv = "PSSPY%d%d" % sys.version_info[:2]
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        roots += glob.glob(os.path.join(base, "PSSE3*"))
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        for d in (os.path.join(r, pyv), os.path.join(r, "PSSBIN")):
            if os.path.isdir(d) and d not in sys.path:
                sys.path.insert(0, d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            try:
                import psse34                         # noqa: F401  (sets PSS/E paths)
            except Exception:
                pass
            import dyntools
            _DYN[0] = dyntools
            print("[out] dyntools from %s" % r)
            return dyntools
        except Exception:
            continue
    _DYN[0] = False
    return None


_RX_VOLT = re.compile(r"^\s*VOLT\s*(\d{3,})", re.I)
_RX_FLT = re.compile(r"^\s*FLT\s*(\d{3,})\s*V\b", re.I)


def _chan_kind(title):
    T = str(title).upper()
    if _RX_FLT.match(T):
        return "FLT"
    if _RX_VOLT.match(T):
        return "VOLT"
    if "QELE" in T:
        return "Q"
    if "ETERM" in T or "ETRM" in T:
        return "ET"
    if "PELE" in T:
        return "P"
    return None


def _mach_key(title):
    """Bus + id for a machine channel, the same for its P, Q and ETERM."""
    T = str(title).strip()
    b = _bus(T)
    mid = ""
    m = re.search(r"\]\s*(\S{1,2})\s*$", T)           # chsb style: ...[NAME kV]1
    if m:
        mid = m.group(1)
    else:
        m = re.match(r"^\s*([A-Za-z]+)\d*(_[^_]+)?_(?:PELEC|QELEC|ETERM)", T, re.I)
        if m and m.group(2):
            mid = m.group(2).lstrip("_")
    pre = re.match(r"^\s*([A-Za-z]+)", T)
    pre = pre.group(1).upper() if pre else ""
    if pre in ("PELE", "QELE", "ETRM"):
        pre = "MACH"
    return (b if b is not None else T.split("_")[0], mid, pre)


def _idx(t, x):
    lo, hi = 0, len(t) - 1
    if hi < 0:
        return 0
    while lo < hi:
        mid = (lo + hi) // 2
        if t[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _clear_time(t, v):
    """(t_on, t_clear) from a faulted-bus voltage: the drop, then the biggest
       one-step rise within 1.5 s."""
    if not v or len(v) < 5:
        return None, None
    v0 = v[0]
    ion = None
    for i in range(1, len(v)):
        if v[i] == v[i] and v[i] < 0.8 * v0:
            ion = i
            break
    if ion is None:
        return None, None
    iend = _idx(t, t[ion] + 1.5)
    best, ic = 0.0, None
    for i in range(ion, max(ion + 1, iend)):
        if i + 1 >= len(v):
            break
        d = v[i + 1] - v[i]
        if d == d and d > best:
            best, ic = d, i + 1
    return t[ion], (t[ic] if ic is not None else None)


def _read_out(path, want_buses):
    dyn = _dyntools()
    if dyn is None:
        return None
    t0 = time.time()
    ch = dyn.CHNF(path)
    sel = None
    try:
        _sh, ids = ch.get_id()
        sel = []
        for k, title in ids.items():
            if k == "time":
                continue
            kd = _chan_kind(title)
            if kd in ("Q", "ET", "FLT") or (kd == "VOLT" and _bus(title) in want_buses):
                sel.append(k)
        if not sel:
            sel = None
    except Exception:
        sel = None
    try:
        _sh, cid, cd = ch.get_data(sel) if sel else ch.get_data()
    except Exception:
        _sh, cid, cd = ch.get_data()
    print("[out]   %s read in %.0f s (%d channels)"
          % (os.path.basename(path), time.time() - t0, len(cid) - 1))
    return cid, cd


def trace_fault(res_dir, fault, fault_bus, buses):
    """buses: [(bus, label)] the worst ones in this fault."""
    cands = [os.path.join(res_dir, "outs", "%s.out" % fault)]
    cands += sorted(glob.glob(os.path.join(res_dir, "outs", "%s*.out" % fault)))
    path = next((p for p in cands if os.path.isfile(p)), None)
    if not path:
        return {"fault": fault, "error": "no .out in outs\\ for %s" % fault}
    got = _read_out(path, set(b for b, _l in buses))
    if got is None:
        return {"fault": fault, "error": "dyntools not available -- run with the PSS/E Python"}
    cid, cd = got
    t = list(cd["time"])
    volt, flt, q, et = {}, None, {}, {}
    for k, title in cid.items():
        if k == "time":
            continue
        kd = _chan_kind(title)
        if kd == "VOLT":
            volt.setdefault(_bus(title), list(cd[k]))
        elif kd == "FLT":
            if flt is None or str(_bus(title)) == str(fault_bus):
                flt = list(cd[k])
        elif kd == "Q":
            q[_mach_key(title)] = (title, list(cd[k]))
        elif kd == "ET":
            et[_mach_key(title)] = (title, list(cd[k]))
    src = flt
    if src is None and fault_bus and _f(fault_bus) is not None:
        src = volt.get(int(_f(fault_bus)))
    t_on, t_clr = _clear_time(t, src) if src else (None, None)
    how = "FLT channel" if flt else ("fault-bus VOLT" if src else "")
    if t_clr is None:
        # the earliest overshoot time is at/near the clearing sample
        return {"fault": fault, "error": "could not find the clearing time (no FLT channel)"}
    ion, icl = _idx(t, t_on), _idx(t, t_clr)
    ipre = max(1, ion - 1)
    res = {"fault": fault, "t_on": t_on, "t_clr": t_clr, "how": how,
           "n_q": len(q), "buses": [], "suspects": []}
    for b, lbl in buses:
        v = volt.get(b)
        if not v:
            res["buses"].append({"bus": b, "label": lbl, "missing": True})
            continue
        if icl >= len(v):
            res["buses"].append({"bus": b, "label": lbl, "missing": True})
            continue
        ip = max(range(icl, len(v)), key=lambda i: v[i] if v[i] == v[i] else -1)
        # the swing peak: 2 cycles after clearing onwards
        isw = _idx(t, t_clr + SPIKE_S)
        isp = max(range(isw, len(v)), key=lambda i: v[i] if v[i] == v[i] else -1) if isw < len(v) else ip
        above, i = 0.0, ip
        while i < len(v) - 1 and v[i] > LIMIT_PU:
            above += t[i + 1] - t[i]
            i += 1
        i = ip - 1
        while i >= icl and v[i] > LIMIT_PU:
            above += t[i + 1] - t[i]
            i -= 1
        dt = t[ip] - t_clr
        res["buses"].append({"bus": b, "label": lbl, "pre": v[ipre], "peak": v[ip],
                             "t_peak": t[ip], "dt": dt, "above": above,
                             "swing_peak": v[isp], "swing_dt": t[isp] - t_clr,
                             "shape": "SPIKE" if above <= SPIKE_S + 1e-9 else "SWING"})
    iwin = _idx(t, t_clr + 1.0)
    mach = []
    for key, (title, v) in q.items():
        if len(v) <= iwin:
            continue
        seg = [x for x in v[icl:iwin] if x == x]
        if not seg:
            continue
        qmax = max(seg)
        e = et.get(key)
        eseg = [x for x in e[1][icl:iwin] if x == x] if e and len(e[1]) > iwin else []
        mach.append({"machine": title.strip(), "bus": key[0], "id": key[1], "v": v,
                     "q0": v[ipre] * SYS_MVA_BASE, "dq_max": (qmax - v[ipre]) * SYS_MVA_BASE,
                     "t_qmax_after_clr": t[icl + seg.index(qmax)] - t_clr,
                     "et": e[1] if e else None, "eterm_max": max(eseg) if eseg else None})

    def _at(m, i):
        dq = (m["v"][i] - m["v"][ipre]) * SYS_MVA_BASE
        e = m["et"][i] if m["et"] is not None and len(m["et"]) > i else None
        return dq, e
    # PER BUS: who is pushing vars at THAT bus's own peak. A clearing spike and
    # a swing 0.2 s later in the same fault usually have different causes.
    for b in res["buses"]:
        if b.get("missing"):
            continue
        ib = _idx(t, b["t_peak"])
        best = None
        for m in mach:
            dq, _e = _at(m, ib)
            if dq >= SUSPECT_MVAR and (best is None or dq > best[1]):
                best = (m, dq)
        if not mach:
            b["driver"] = "? no machine Q channels in this .out"
        elif best is None:
            b["driver"] = ("NETWORK -- no channelled machine +%.0f Mvar at this peak "
                           "(shunt / line charging / clearing step)" % SUSPECT_MVAR)
        else:
            b["driver"] = "%s  +%.0f Mvar at this peak" % (best[0]["machine"], best[1])
    # the suspects table: at the WORST bus's peak
    wb = [b for b in res["buses"] if not b.get("missing")]
    tp = max(wb, key=lambda b: b["peak"])["t_peak"] if wb else t_clr
    itp = _idx(t, tp)
    for m in mach:
        dq, e = _at(m, itp)
        res["suspects"].append({"machine": m["machine"], "bus": m["bus"], "id": m["id"],
                                "q0": m["q0"], "dq_at_spike": dq, "dq_max": m["dq_max"],
                                "t_qmax_after_clr": m["t_qmax_after_clr"],
                                "in_step": abs(t_clr + m["t_qmax_after_clr"] - tp) <= MATCH_S,
                                "eterm_at_spike": e, "eterm_max": m["eterm_max"]})
    res["suspects"].sort(key=lambda s: -max(s["dq_at_spike"], s["dq_max"]))
    top = [s for s in res["suspects"] if max(s["dq_at_spike"], s["dq_max"]) >= SUSPECT_MVAR]
    if not q:
        res["verdict"] = ("NO machine Q channels in this .out -- cannot name a plant. "
                          "Turn on GROUP3_AREA_IBR_PQV / cent_mach P/Q channels and re-run this fault.")
    elif not top:
        res["verdict"] = ("NETWORK: no monitored machine pushed >= %.0f Mvar extra within 1 s of "
                          "clearing. Look at shunts/capacitors and line charging near the buses, or a "
                          "machine with no Q channel (%d machines have one)." % (SUSPECT_MVAR, len(q)))
    else:
        s = top[0]
        res["verdict"] = ("biggest var push: %s %+.0f Mvar above pre-fault (%.3f s after clearing)%s"
                          " -- see each bus's driver above"
                          % (s["machine"], s["dq_max"], s["t_qmax_after_clr"],
                             (", own terminal up to %.3f pu" % s["eterm_max"]) if s["eterm_max"] else ""))
    return res


# ----------------------------- REPORT --------------------------------------
def run_folder(kind, proj, res_dir, vcsv):
    tag = KINDS[kind][2]
    rows = read_overshoots(vcsv)
    faults_all = set()
    with open(vcsv, newline="") as fh:
        for r in csv.DictReader(fh):
            faults_all.add((r.get("fault_id") or "").strip())
    buses = rank_buses(rows, len(faults_all))
    faults = rank_faults(rows)
    groups = group_buses(buses)
    stem = "%s_%s" % (tag, proj)
    txt = os.path.join(res_dir, "SPIKE_FINDER_%s.txt" % stem)
    bcsv = os.path.join(res_dir, "SPIKE_BUSES_%s.csv" % stem)
    scsv = os.path.join(res_dir, "SPIKE_SUSPECTS_%s.csv" % stem)

    with open(bcsv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bus", "element", "area", "area_name", "faults_failed", "share_of_faults",
                    "worst_pu", "worst_fault", "spikes", "swings", "shape",
                    "median_above_limit_s", "median_hops_from_fault", "hint", "faults"])
        for b in buses:
            w.writerow([b["bus"], b["label"], b["area"], b["area_name"], b["n"],
                        "%.2f" % b["share"], "%.3f" % b["worst"], b["worst_fault"],
                        b["spikes"], b["swings"], b["shape"],
                        "" if b["dur_med"] is None else "%.4f" % b["dur_med"],
                        "" if b["hops_med"] is None else "%g" % b["hops_med"],
                        b["hint"], " ".join(sorted(b["faults"]))])

    traced = []
    if READ_OUTS and rows:
        if OUT_FAULTS == "auto":
            pick = [f["fault"] for f in faults[:OUT_FAULTS_N]]
        else:
            pick = list(OUT_FAULTS)
        fb = dict((f["fault"], f["fault_bus"]) for f in faults)
        for fid in pick:
            top = sorted([r for r in rows if r["fault"] == fid], key=lambda r: -r["value"])
            seen, bl = set(), []
            for r in top:
                if r["bus"] not in seen:
                    seen.add(r["bus"])
                    bl.append((r["bus"], r["element"]))
                if len(bl) >= BUSES_PER_FAULT:
                    break
            if not bl:
                print("[out] %s %s: no overshoot rows -- skipped" % (stem, fid))
                continue
            print("[out] %s %s: tracing %d bus(es)" % (stem, fid, len(bl)))
            try:
                traced.append(trace_fault(res_dir, fid, fb.get(fid, ""), bl))
            except Exception as e:
                traced.append({"fault": fid, "error": "%s: %s" % (type(e).__name__, e)})
            if traced[-1].get("error") and "dyntools" in traced[-1]["error"]:
                print("[out] %s -- Part B stopped" % traced[-1]["error"])
                break
        with open(scsv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["fault", "t_clear", "machine", "bus", "id", "q_prefault_mvar",
                        "dq_at_spike_mvar", "dq_max_mvar", "t_qmax_after_clear_s", "in_step",
                        "eterm_at_spike", "eterm_max"])
            for tr in traced:
                for s in tr.get("suspects", []):
                    w.writerow([tr["fault"], "%.4f" % tr["t_clr"], s["machine"], s["bus"], s["id"],
                                "%.1f" % s["q0"], "%.1f" % s["dq_at_spike"], "%.1f" % s["dq_max"],
                                "%.4f" % s["t_qmax_after_clr"], int(s["in_step"]),
                                "" if s["eterm_at_spike"] is None else "%.3f" % s["eterm_at_spike"],
                                "" if s["eterm_max"] is None else "%.3f" % s["eterm_max"]])

    n_sp = sum(1 for b in buses if b["shape"] == "SPIKE")
    n_sw = sum(1 for b in buses if b["shape"] == "SWING")
    with open(txt, "w") as f:
        W = f.write
        W("SPIKE FINDER -- %s %s    (%s)\n" % (tag, proj, time.strftime("%Y-%m-%d %H:%M")))
        W("source: %s\n" % vcsv)
        W("limit %.2f pu; SPIKE = above it for <= %.3f s (2 cycles), longer = SWING\n" % (LIMIT_PU, SPIKE_S))
        W("=" * 100 + "\n")
        W(" %d distinct bus(es) over %.2f pu, in %d of %d fault(s).  SPIKE %d  SWING %d  other %d\n"
          % (len(buses), LIMIT_PU, len(faults), len(faults_all), n_sp, n_sw, len(buses) - n_sp - n_sw))
        W("\n 1. BUSES, most faults first (all of them in %s)\n" % os.path.basename(bcsv))
        W(" %-22s %-6s %6s %6s %-10s %-6s %8s %5s  %s\n"
          % ("bus", "area", "faults", "worst", "in", "shape", "abv_s", "hops", "likely cause"))
        W(" " + "-" * 98 + "\n")
        for b in buses[:TOP_BUSES]:
            W(" %-22s %-6s %6d %6.3f %-10s %-6s %8s %5s  %s\n"
              % (b["label"][:22], b["area"][:6], b["n"], b["worst"], b["worst_fault"][:10],
                 b["shape"], "-" if b["dur_med"] is None else "%.3f" % b["dur_med"],
                 "-" if b["hops_med"] is None else "%g" % b["hops_med"], b["hint"]))
        if len(buses) > TOP_BUSES:
            W(" ... %d more in the CSV\n" % (len(buses) - TOP_BUSES))

        W("\n 2. GROUPS -- buses that fail on the same faults (one group ~ one source)\n")
        for i, (mem, common) in enumerate(groups[:15], 1):
            if len(mem) < 2 and mem[0]["n"] < 3:
                continue
            W(" G%-2d %3d bus(es), seed %s fails on %d fault(s); %d shared by all: %s\n"
              % (i, len(mem), mem[0]["label"], mem[0]["n"], len(common),
                 " ".join(sorted(common)[:12]) + (" ..." if len(common) > 12 else "")))
            W("      %s\n" % ", ".join("%s(%.3f)" % (b["label"], b["worst"]) for b in mem[:12])
              + ("      ... +%d\n" % (len(mem) - 12) if len(mem) > 12 else ""))

        W("\n 3. BY AREA\n")
        ar = {}
        for b in buses:
            a = ar.setdefault((b["area"], b["area_name"]), [0, 0.0, 0])
            a[0] += 1
            a[1] = max(a[1], b["worst"])
            a[2] += b["n"]
        for (a, nm), v in sorted(ar.items(), key=lambda x: -x[1][0]):
            W(" %-6s %-20s %4d bus(es)  worst %.3f  bus-fault hits %d\n" % (a, nm[:20], v[0], v[1], v[2]))

        W("\n 4. FAULTS with the most overvoltage buses\n")
        for fr in faults[:20]:
            W(" %-12s %4d bus(es)  worst %.3f at %s  (fault bus %s)\n"
              % (fr["fault"], len(fr["buses"]), fr["worst"], fr["worst_bus"], fr["fault_bus"]))

        W("\n 5. WHO DRIVES IT (Part B, from the .out files)\n")
        if not READ_OUTS:
            W(" READ_OUTS = False -- not run\n")
        for tr in traced:
            W("\n ---- %s ----\n" % tr["fault"])
            if tr.get("error"):
                W(" %s\n" % tr["error"])
                continue
            W(" fault on %.3f s, cleared %.4f s (%s); %d machine(s) with a Q channel\n"
              % (tr["t_on"], tr["t_clr"], tr["how"], tr["n_q"]))
            for b in tr["buses"]:
                if b.get("missing"):
                    W("   %-20s no VOLT channel in this .out\n" % b["label"])
                    continue
                W("   %-20s pre %.3f  peak %.3f at +%.4f s  above %.3f s  -> %s ;"
                  " peak after 2 cycles %.3f (+%.3f s)\n"
                  % (b["label"][:20], b["pre"], b["peak"], b["dt"], b["above"], b["shape"],
                     b["swing_peak"], b["swing_dt"]))
                W("   %-20s   driver: %s\n" % ("", b.get("driver", "-")))
            W(" VERDICT: %s\n" % tr["verdict"])
            sus = [s for s in tr["suspects"] if max(s["dq_at_spike"], s["dq_max"]) >= SUSPECT_MVAR]
            if sus:
                W("   %-34s %8s %9s %9s %9s %7s %7s\n"
                  % ("machine", "Q0 Mvar", "dQ@spike", "dQ max", "t_qmax", "Et@spk", "Et max"))
                for s in sus[:SUSPECT_TOP]:
                    W("   %-34s %8.1f %9.1f %9.1f %9.3f %7s %7s%s\n"
                      % (s["machine"][:34], s["q0"], s["dq_at_spike"], s["dq_max"],
                         s["t_qmax_after_clr"],
                         "-" if s["eterm_at_spike"] is None else "%.3f" % s["eterm_at_spike"],
                         "-" if s["eterm_max"] is None else "%.3f" % s["eterm_max"],
                         "  <- in step" if s["in_step"] else ""))

        W("\n" + "=" * 100 + "\n HOW TO READ IT\n")
        W("  SPIKE near the fault, few faults  -> the clearing step itself. Nothing to tune; SPP may\n"
          "                                      accept it (V_OVERSHOOT_JUDGE_SWING) -- get it in writing.\n"
          "  SPIKE remote, MANY faults         -> something AT that bus holds voltage up the instant the\n"
          "                                      fault clears: a switched shunt/capacitor, a long lightly\n"
          "                                      loaded line, or an IBR whose Q does not ramp back.\n"
          "  SWING (> 2 cycles)                -> a controller: IBR voltage control / momentary cessation\n"
          "                                      exit, or an exciter. Part B's top suspect is the one.\n"
          "  VERDICT NETWORK                   -> no channelled machine explains it: look at shunts in\n"
          "                                      the group's buses, or add Q channels for that area.\n"
          "  Fix it in the model (DYRECHANGE_IDV / DYR_EDITS with DYR_APPLY_TO=\"both\"), then re-run\n"
          "  only the faults in section 4 with ONLY_FAULTS and run this again.\n")
    print("[spike] %s -> %s" % (stem, txt))
    top = buses[0] if buses else None
    return buses, traced, ("%-24s %4d bus(es) in %3d/%3d fault(s); SPIKE %d SWING %d; top %s (%d faults, %.3f pu)"
            % (stem, len(buses), len(faults), len(faults_all), n_sp, n_sw,
               top["label"] if top else "-", top["n"] if top else 0, top["worst"] if top else 0.0))


# ----------------------------- PART C --------------------------------------
_PSSPY = [None]


def _psspy():
    if _PSSPY[0] is not None:
        return _PSSPY[0] or None
    _dyntools()                                     # sets the PSS/E paths as a side effect
    try:
        try:
            import psse34                           # noqa: F401
        except Exception:
            pass
        import psspy
        psspy.psseinit(150000)
        for fn, a in (("progress_output", (6, "", [0, 0])), ("report_output", (6, "", [0, 0])),
                      ("alert_output", (6, "", [0, 0])), ("prompt_output", (6, "", [0, 0]))):
            try:
                getattr(psspy, fn)(*a)
            except Exception:
                pass
        _PSSPY[0] = psspy
        return psspy
    except Exception as e:
        print("[near] psspy not available (%s) -- run with the PSS/E Python" % e)
        _PSSPY[0] = False
        return None


def _panel_setting(name):
    p = os.path.join(_root(), "z5_cmp_all_con.py")
    if not os.path.isfile(p):
        return None
    rx = re.compile(r'^%s\s*=\s*r?["\']([^"\']*)["\']' % name)
    with open(p, encoding="latin-1") as fh:
        for ln in fh:
            m = rx.match(ln)
            if m:
                return m.group(1)
    return None


def case_files(kind):
    sub, _r, tag = KINDS[kind]
    folder = _panel_setting("%s_FOLDER" % ("BASE" if kind == "base" else "PROJ")) or sub
    d = folder if os.path.isabs(folder) else os.path.join(_root(), folder)
    sav = CASE_SAV.get(kind) or _panel_setting("%s_SAV" % tag)
    dyr = CASE_DYR.get(kind) or _panel_setting("%s_DYR" % tag)
    if sav and not os.path.isabs(sav):
        sav = os.path.join(d, sav)
    if dyr and not os.path.isabs(dyr):
        dyr = os.path.join(d, dyr)
    return sav, dyr


_SYNC_M = ("GENROU", "GENSAL", "GENCLS", "GENTPJ", "GENROE", "GENSAE", "GENTPF", "GENQEC", "CIMTR")
_SVC_M = ("CSVGN", "CSTCNT", "SVSMO", "CSTATT", "STCON", "ABBSVC", "SVC")
_IBR_M = ("REGC", "REEC", "REPC", "WT3G", "WT4G", "WT1G", "WT2G", "PVGU", "PVEU", "PVDG",
          "GEWT", "GEPV", "DER_A", "DERA", "IBR", "INV", "SMA", "SUNG", "VEST", "SIEM", "NORDEX")


def read_dyr_models(path):
    """{(bus, id): [model, ...]} from the .dyr -- names only."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, encoding="latin-1") as fh:
        txt = fh.read()
    txt = re.sub(r"(?m)//.*$", "", txt)
    for rec in txt.split("/"):
        tk = re.findall(r"'[^']*'|\"[^\"]*\"|[^\s,]+", rec.strip())
        if len(tk) < 3 or not re.match(r"^-?\d+$", tk[0]):
            continue
        model = tk[1].strip("'\"").upper()
        mid = tk[2].strip("'\"").strip()
        if model == "USRMDL" and len(tk) > 3:
            model = tk[3].strip("'\"").upper()
        out.setdefault((int(tk[0]), mid), []).append(model)
    return out


def _mtype(models, wmod):
    ms = " ".join(models or [])
    if any(k in ms for k in _SVC_M):
        return "SVC/STATCOM"
    if any(k in ms for k in _IBR_M):
        return "IBR"
    if any(k in ms for k in _SYNC_M):
        return "SYNC"
    if wmod and wmod > 0:
        return "IBR"
    return "GEN" if not models else "GEN(" + models[0] + ")"


def _arr(fn, sid, flag, names, *extra):
    """One psspy a* call per name, so an unknown name costs only that column."""
    out = {}
    for n in names:
        try:
            if extra:
                ierr, v = fn(sid, extra[0], extra[1], flag, extra[2], n)
            else:
                ierr, v = fn(sid, flag, n)
            if ierr == 0 and v:
                out[n] = v[0]
        except Exception:
            pass
    return out


def load_network(psspy, sav, dyr):
    ierr = psspy.case(sav)
    if ierr:
        raise RuntimeError("psspy.case(%s) ierr=%s" % (sav, ierr))
    net = {"bus": {}, "adj": {}, "dev": {}}
    b = _arr(psspy.abusint, -1, 2, ["NUMBER", "AREA"])
    br = _arr(psspy.abusreal, -1, 2, ["BASE", "PU"])
    bc = _arr(psspy.abuschar, -1, 2, ["NAME"])
    for i, n in enumerate(b.get("NUMBER", [])):
        net["bus"][n] = {"kv": br.get("BASE", [0] * (i + 1))[i], "pu": br.get("PU", [0] * (i + 1))[i],
                         "name": (bc.get("NAME", [""] * (i + 1))[i] or "").strip(),
                         "area": b.get("AREA", [""] * (i + 1))[i]}

    def link(a, c, z, what):
        net["adj"].setdefault(a, []).append((c, z, what))
        net["adj"].setdefault(c, []).append((a, z, what))

    def dev(bus, d):
        net["dev"].setdefault(bus, []).append(d)
    # branches + 2-winding transformers (flag 3 = in-service incl. transformers)
    bi = _arr(psspy.abrnint, -1, 3, ["FROMNUMBER", "TONUMBER"], 1, 1, 1)
    bz = {}
    try:
        ierr, v = psspy.abrncplx(-1, 1, 1, 3, 1, "RX")
        if ierr == 0:
            bz = v[0]
    except Exception:
        pass
    bch = _arr(psspy.abrnreal, -1, 3, ["CHARGING"], 1, 1, 1).get("CHARGING", [])
    bid = _arr(psspy.abrnchar, -1, 3, ["ID"], 1, 1, 1).get("ID", [])
    fr, to = bi.get("FROMNUMBER", []), bi.get("TONUMBER", [])
    for i in range(min(len(fr), len(to))):
        z = abs(bz[i]) if i < len(bz) else 0.05
        link(fr[i], to[i], max(z, 1e-4), "branch")
        if i < len(bch) and bch[i] * 100.0 >= LINE_CHG_MVAR:
            d = {"kind": "LINE CHARGING", "bus": fr[i], "to": to[i],
                 "id": (bid[i] if i < len(bid) else "").strip(),
                 "mvar": bch[i] * 100.0, "status": 1}
            dev(fr[i], d)
            dev(to[i], d)
    # 3-winding transformers: star them through winding 1
    t3 = _arr(psspy.atr3int, -1, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER"], 1, 1, 1)
    w1, w2, w3 = t3.get("WIND1NUMBER", []), t3.get("WIND2NUMBER", []), t3.get("WIND3NUMBER", [])
    for i in range(len(w1)):
        for w in ((w2[i] if i < len(w2) else 0), (w3[i] if i < len(w3) else 0)):
            if w:
                link(w1[i], w, 0.1, "3w-xfmr")
    # machines
    models = read_dyr_models(dyr)
    mi = _arr(psspy.amachint, -1, 4, ["NUMBER", "STATUS", "WMOD"])
    mr = _arr(psspy.amachreal, -1, 4, ["PGEN", "QGEN", "QMAX", "QMIN", "MBASE"])
    mc = _arr(psspy.amachchar, -1, 4, ["ID"])
    for i, n in enumerate(mi.get("NUMBER", [])):
        mid = (mc.get("ID", [""] * (i + 1))[i] or "").strip()
        md = models.get((n, mid), [])
        g = lambda k: mr.get(k, [None] * (i + 1))[i]
        dev(n, {"kind": _mtype(md, mi.get("WMOD", [0] * (i + 1))[i]), "bus": n, "id": mid,
                "status": mi.get("STATUS", [1] * (i + 1))[i], "mw": g("PGEN"), "mvar": g("QGEN"),
                "qmax": g("QMAX"), "qmin": g("QMIN"), "mbase": g("MBASE"),
                "models": [m for m in md if not m.startswith(("CMLD", "CLOD", "LDFR", "IEEL"))]})
    # fixed shunts (Mvar at 1 pu, + = capacitor)
    fi = _arr(psspy.afxshuntint, -1, 4, ["NUMBER", "STATUS"])
    fc = _arr(psspy.afxshuntchar, -1, 4, ["ID"])
    fq = []
    try:
        ierr, v = psspy.afxshuntcplx(-1, 4, "SHUNTNOM")
        if ierr == 0:
            fq = v[0]
    except Exception:
        pass
    for i, n in enumerate(fi.get("NUMBER", [])):
        q = fq[i].imag if i < len(fq) else None
        dev(n, {"kind": "FIXED CAP" if (q or 0) > 0 else "FIXED REACTOR", "bus": n,
                "id": (fc.get("ID", [""] * (i + 1))[i] or "").strip(),
                "status": fi.get("STATUS", [1] * (i + 1))[i], "mvar": q})
    # switched shunts
    si = _arr(psspy.aswshint, -1, 4, ["NUMBER", "STATUS", "MODE"])
    sr = _arr(psspy.aswshreal, -1, 4, ["BSWNOM", "BSWMAX", "BSWMIN", "VSWHI", "VSWLO", "BINIT"])
    for i, n in enumerate(si.get("NUMBER", [])):
        g = lambda k: sr[k][i] if k in sr and i < len(sr[k]) else None
        now = g("BSWNOM") if g("BSWNOM") is not None else g("BINIT")
        dev(n, {"kind": "SWITCHED SHUNT", "bus": n, "id": "",
                "status": si.get("STATUS", [1] * (i + 1))[i], "mode": si.get("MODE", [None] * (i + 1))[i],
                "mvar": now, "bmax": g("BSWMAX"), "bmin": g("BSWMIN"),
                "vhi": g("VSWHI"), "vlo": g("VSWLO")})
    # FACTS
    ff = _arr(psspy.afactsint, -1, 4, ["SENDNUMBER", "STATUS"])
    for i, n in enumerate(ff.get("SENDNUMBER", [])):
        dev(n, {"kind": "FACTS", "bus": n, "id": "", "status": ff.get("STATUS", [1] * (i + 1))[i]})
    print("[near] case: %d buses, %d with devices" % (len(net["bus"]), len(net["dev"])))
    return net


def nearby(net, bus):
    """[(bus, hops, zdist)] within NEAR_HOPS, Dijkstra on |Z| (pu)."""
    import heapq
    best = {bus: (0.0, 0)}
    pq = [(0.0, 0, bus)]
    while pq:
        z, h, n = heapq.heappop(pq)
        if best.get(n, (1e9, 0))[0] < z:
            continue
        if h >= NEAR_HOPS:
            continue
        for m, dz, _w in net["adj"].get(n, []):
            nz = z + dz
            if nz < best.get(m, (1e9, 0))[0]:
                best[m] = (nz, h + 1)
                heapq.heappush(pq, (nz, h + 1, m))
    return sorted(((n, h, z) for n, (z, h) in best.items()), key=lambda x: x[2])


def _advice(d, confirmed):
    k, st = d["kind"], d.get("status", 1)
    if st == 0:
        return "OUT of service -- no effect now"
    if k == "IBR":
        s = ("fast Q after clearing: check REEC Vdip/Vup, Kqv, Iqh/Iql, Thld/Iqfrz and the REPC "
             "voltage loop (Kc, Kp/Ki); a Q that does not ramp back holds V high")
        return ("CONFIRMED pushing vars (%s). " % confirmed + s) if confirmed else s
    if k == "SYNC":
        s = "exciter field forcing: check the AVR/exciter model and its limits; Q near QMAX pre-fault?"
        return ("CONFIRMED pushing vars (%s). " % confirmed + s) if confirmed else s
    if k == "SVC/STATCOM":
        return "voltage device: check its response (gain, slope, Vref) -- overshoot on recovery"
    if k == "FIXED CAP":
        return "holds V up the instant the fault clears: test it out of service / smaller in this dispatch"
    if k == "FIXED REACTOR":
        return "pulls V down -- already helping; putting a bigger one in / keeping it on helps"
    if k == "SWITCHED SHUNT":
        q = d.get("mvar")
        if q is not None and q > 0:
            s = "CAPACITIVE now (%.0f Mvar): test switching steps off" % q
        elif q is not None and q < 0:
            s = "inductive now (%.0f Mvar) -- helping" % q
        else:
            s = "check its switched-in Mvar"
        if d.get("mode") == 0:
            s += "; MODE 0 = locked (never switches in the run)"
        return s
    if k == "LINE CHARGING":
        return "long/lightly loaded line: its charging lifts V when load/flow drops after clearing"
    if k == "FACTS":
        return "FACTS device: check its voltage control response"
    return "generator with no recognised model -- check its .dyr records"


def run_nearby(kind, combined, traced_all):
    ps = _psspy()
    if ps is None:
        return None
    sav, dyr = case_files(kind)
    if not sav or not os.path.isfile(sav):
        print("[near] %s case not found: %s -- set CASE_SAV" % (kind, sav))
        return None
    print("[near] %s: loading %s" % (kind, sav))
    net = load_network(ps, sav, dyr)
    # machines Part B caught pushing vars, by bus
    conf = {}
    for proj, tr in traced_all:
        for s in tr.get("suspects", []):
            if max(s["dq_at_spike"], s["dq_max"]) >= SUSPECT_MVAR and isinstance(s["bus"], int):
                c = conf.setdefault(s["bus"], [])
                c.append("%s %s %+.0f Mvar" % (proj, tr["fault"], s["dq_max"]))
    tag = KINDS[kind][2]
    txt = os.path.join(_root(), "SPIKE_NEARBY_%s.txt" % tag)
    ncsv = os.path.join(_root(), "SPIKE_NEARBY_%s.csv" % tag)
    rows = []
    with open(txt, "w") as f:
        W = f.write
        W("NEARBY DEVICES -- %s case %s   (%s)\n" % (tag, os.path.basename(sav), time.strftime("%Y-%m-%d %H:%M")))
        W("dyr: %s\n" % (dyr or "-"))
        W("within %d buses of each bad bus, closest (by |Z| in pu) first\n" % NEAR_HOPS)
        W("=" * 100 + "\n")
        for cb in combined[:NEAR_BUSES_N]:
            bus = cb["bus"]
            bi = net["bus"].get(bus, {})
            W("\n BUS %s  %s %.0f kV  area %s   fails in %d fault(s) over %s, worst %.3f pu (%s)\n"
              % (bus, bi.get("name", "?"), bi.get("kv", 0) or 0, bi.get("area", "?"),
                 cb["n"], ", ".join(sorted(cb["projects"])), cb["worst"], cb["worst_where"]))
            if bus not in net["bus"]:
                W("   not in this case\n")
                continue
            found = []
            for n, h, z in nearby(net, bus):
                for d in net["dev"].get(n, []):
                    found.append((z, h, n, d))
            if not found:
                W("   no generator / shunt / FACTS / charging line within %d buses\n" % NEAR_HOPS)
                continue
            W("   %-15s %-8s %-18s %4s %6s %8s %8s  %s\n"
              % ("device", "bus", "name", "hops", "|Z|pu", "MW", "Mvar", "what to check"))
            seen = set()
            nshow = 0
            for z, h, n, d in found:
                key = (d["kind"], d["bus"], d.get("id"), d.get("to"))
                if key in seen:
                    continue
                seen.add(key)
                cf = "; ".join(conf.get(d["bus"], [])[:3]) if d["kind"] in ("IBR", "SYNC") else ""
                adv = _advice(d, cf)
                nm = net["bus"].get(n, {}).get("name", "")
                lbl = d["kind"] + ((" " + str(d["id"])) if d.get("id") else "")
                if d["kind"] == "LINE CHARGING":
                    lbl = "LINE %s-%s" % (d["bus"], d["to"])
                mw = d.get("mw")
                mv = d.get("mvar")
                if nshow < NEAR_TOP:
                    W("   %-15s %-8s %-18s %4d %6.3f %8s %8s  %s\n"
                      % (lbl[:15], n, nm[:18], h, z, "-" if mw is None else "%.0f" % mw,
                         "-" if mv is None else "%.0f" % mv, adv))
                    if d.get("models"):
                        W("   %-15s models: %s\n" % ("", " ".join(d["models"][:6])))
                nshow += 1
                rows.append([tag, bus, cb["n"], "%.3f" % cb["worst"], d["kind"], n, nm, d.get("id", ""),
                             h, "%.4f" % z, "" if mw is None else "%.1f" % mw,
                             "" if mv is None else "%.1f" % mv, d.get("status", ""),
                             " ".join(d.get("models", [])), cf, adv])
            if nshow > NEAR_TOP:
                W("   ... %d more in %s\n" % (nshow - NEAR_TOP, os.path.basename(ncsv)))
        W("\n" + "=" * 100 + "\n HOW TO USE IT\n"
          "  Start with the CONFIRMED machines and capacitive shunts closest to a bus that fails on\n"
          "  many faults. Test one change at a time on the worst faults (ONLY_FAULTS), in BOTH cases\n"
          "  (a base-case fix belongs in both: DYR_APPLY_TO=\"both\" / an idv in DYRECHANGE_IDV).\n")
    with open(ncsv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case", "bad_bus", "faults_failed", "worst_pu", "device", "device_bus", "name", "id",
                    "hops", "zdist_pu", "mw", "mvar", "status", "models", "confirmed_by_out", "what_to_check"])
        w.writerows(rows)
    print("[near] %s -> %s" % (kind, txt))
    return txt


def combine(per_proj):
    """Buses of every project together: the same base case fails the same buses."""
    by = {}
    for proj, buses in per_proj:
        for b in buses:
            c = by.setdefault(b["bus"], {"bus": b["bus"], "label": b["label"], "n": 0, "worst": 0.0,
                                         "worst_where": "", "projects": set(), "shapes": set(),
                                         "area": b["area"]})
            c["n"] += b["n"]
            c["projects"].add(proj)
            c["shapes"].add(b["shape"])
            if b["worst"] > c["worst"]:
                c["worst"], c["worst_where"] = b["worst"], "%s %s" % (proj, b["worst_fault"])
    out = list(by.values())
    out.sort(key=lambda c: (-len(c["projects"]), -c["n"], -c["worst"]))
    return out


def main():
    folders = find_folders()
    if not folders:
        print("[spike] no 02_VIOLATIONS csv found under %s (Base\\results_base\\<proj>_spp ...)" % _root())
        return 1
    lines, per_kind, traced_kind = [], {}, {}
    for kind, proj, d, v in folders:
        print("[spike] %s %s  <- %s" % (kind, proj, v))
        try:
            buses, traced, ln = run_folder(kind, proj, d, v)
            lines.append(ln)
            per_kind.setdefault(kind, []).append((proj, buses))
            traced_kind.setdefault(kind, []).extend((proj, t) for t in traced)
        except Exception as e:
            lines.append("%s %s FAILED: %s: %s" % (kind, proj, type(e).__name__, e))
            print("[spike] %s" % lines[-1])
    allp = os.path.join(_root(), "SPIKE_FINDER_ALL.txt")
    near = []
    with open(allp, "w") as f:
        f.write("SPIKE FINDER -- %s\n" % time.strftime("%Y-%m-%d %H:%M"))
        f.write("per project (full report in each results folder):\n")
        for ln in lines:
            f.write("  " + ln + "\n")
        for kind in per_kind:
            comb = combine(per_kind[kind])
            f.write("\n %s -- BUSES OF ALL PROJECTS TOGETHER (most projects, then most faults)\n"
                    % KINDS[kind][2])
            f.write(" %-22s %-6s %8s %7s %6s  %-24s %s\n"
                    % ("bus", "area", "projects", "faults", "worst", "worst in", "shape"))
            for c in comb[:TOP_BUSES]:
                f.write(" %-22s %-6s %8d %7d %6.3f  %-24s %s\n"
                        % (c["label"][:22], str(c["area"])[:6], len(c["projects"]), c["n"], c["worst"],
                           c["worst_where"][:24], "/".join(sorted(c["shapes"]))))
            if NEARBY and comb:
                try:
                    t = run_nearby(kind, comb, traced_kind.get(kind, []))
                except Exception as e:
                    t = None
                    print("[near] %s FAILED: %s: %s" % (kind, type(e).__name__, e))
                    f.write(" nearby devices FAILED: %s: %s\n" % (type(e).__name__, e))
                if t:
                    near.append(t)
                    f.write(" nearby devices for the top %d: %s\n" % (NEAR_BUSES_N, os.path.basename(t)))
    print("\n".join(lines))
    print("[spike] summary -> %s" % allp)
    for t in near:
        print("[spike] nearby  -> %s" % t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
