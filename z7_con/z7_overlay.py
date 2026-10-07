# -*- coding: utf-8 -*-
"""z7_overlay.py -- BASE, SCENARIO 1 and SCENARIO 2 on the same axes, per fault.

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
    python z7_overlay.py
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
CASES = [
    ("Base case",                          r"Base\results_base_f",     "",           "#000000", "--", 1.6),
    ("Scenario 1: SGF + EGF (GIA)",        r"Projects\results_proj_f", "",           "#0072B2", "-",  1.4),
    ("Scenario 2: SGF on, EGF off",        r"Projects\results_proj_f", "_s1_egfoff", "#D55E00", "-.", 1.4),
]

OUT_DIR = r"overlay_plots"               # under ROOT unless absolute
ALIGN_TO_FAULT = False                   # False = simulation time, as the study plots | True = time from the fault (0 s = fault)
T_MIN, T_MAX = 0.0, 20.0                 # seconds shown; None = the whole record
PANELS_PER_PAGE = 4
MAX_POINTS = 4000                        # points per trace (thinned evenly above this)
INCLUDE_SGF_UNITS = True                 # per-unit SGF P / Q / ETERM
INCLUDE_EGF_UNITS = True                 # per-unit EGF P / Q / ETERM
EXTRA_TITLE_REGEX = []                   # more channels by .out title, e.g. [r"^GEN584713_PELEC$"]
SBASE_MVA = 100.0                        # machine PELEC / QELEC are pu on the system base
WORKERS = 4                              # faults drawn at once, each in its own process (1 = one at a time)
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
        raise SystemExit("dyntools could not be imported (%s). Run this with the PSS/E "
                         "Python, or add its PSSPY34 folder to PSSE_DIRS." % e)


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
_RX_FLT = re.compile(r"^FLT(\d+) V$")
_RX_POIV = re.compile(r"^POI ?(\d+) V$")
_RX_POIP = re.compile(r"^POI POWR (\d+) MW p(\d+)")
_RX_POIQ = re.compile(r"^POI VARS (\d+) MVAR p(\d+)")
_RX_SGF = re.compile(r"^PROJ(\d+)_(PELEC|QELEC|ETERM)$")
_RX_EGF = re.compile(r"^XGEN(\d+)(?:_(\w{1,2}))?_(PELEC|QELEC|ETERM)$")
_QTY = {"PELEC": ("P", "MW"), "QELEC": ("Q", "MVAr"), "ETERM": ("terminal V", "pu")}


def _read(dyntools, path):
    """{title: (t list, value list)} for the channels this script draws, plus
       the summed POI P / Q, read once from one .out."""
    chnf = dyntools.CHNF(path)
    _sh, ids, data = chnf.get_data()
    t = list(data["time"])
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
        if m and "GRID" not in ttl and "GRD" not in ttl:
            poip.append((m.group(1), v))
            continue
        m = _RX_POIQ.match(ttl)
        if m and "GRID" not in ttl and "GRD" not in ttl:
            poiq.append((m.group(1), v))
            continue
        m = _RX_SGF.match(ttl)
        if m and INCLUDE_SGF_UNITS:
            q, u = _QTY[m.group(2)]
            sc = SBASE_MVA if u != "pu" else 1.0
            out["SGF unit %s %s (%s)" % (m.group(1), q, u)] = [x * sc for x in v]
            continue
        m = _RX_EGF.match(ttl)
        if m and INCLUDE_EGF_UNITS:
            q, u = _QTY[m.group(3)]
            sc = SBASE_MVA if u != "pu" else 1.0
            unit = m.group(1) + (" '%s'" % m.group(2) if m.group(2) else "")
            out["EGF %s %s (%s)" % (unit, q, u)] = [x * sc for x in v]
            continue
        if any(rx.search(ttl) for rx in extra):
            out[ttl] = list(v)
    # The tie channels record the flow LEAVING the POI towards the plant, so the
    # power the plant delivers is minus their sum.
    for lst, nm, u in ((poip, "P", "MW"), (poiq, "Q", "MVAr")):
        if lst:
            poi = lst[0][0]
            n = len(t)
            tot = [0.0] * n
            for _b, v in lst:
                for i in range(min(n, len(v))):
                    tot[i] -= v[i]
            out["POI %s %s delivered, sum of %d tie(s) (%s)" % (poi, nm, len(lst), u)] = tot
    return t, out


def _read_cached(dyntools, path, ci):
    """_read(), kept in <OUT_DIR>\cache keyed by the .out's size and time, so a
       second run with other plot settings reads nothing it already has."""
    if not USE_CACHE:
        return _read(dyntools, path)
    import pickle
    st = os.stat(path)
    key = "%s|%d|%d|%s|%s|%s" % (os.path.abspath(path), st.st_size, int(st.st_mtime),
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
        if ALIGN_TO_FAULT:
            tf = _fault_time(t, sig)
            if tf is not None:
                t = [x - tf for x in t]
        traces.append((ci, t, sig))
    if not traces:
        print("    %s: no case has an .out -- skipped" % fault)
        return None
    panels = sorted(set(k for _c, _t, s in traces for k in s), key=_order)
    bmap, fbus, dist = (_bus_map(dirs) if SHOW_NODES else None), None, {}
    for k in panels:
        m = re.match(r"^Faulted bus (\d+)", k)
        if m:
            fbus = int(m.group(1))
    if bmap and fbus is not None:
        dist = _hops(bmap[0], fbus)
    out_dir = _abs(OUT_DIR)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    pdf_path = os.path.join(out_dir, "%s_%s_%s_overlay.pdf" % (proj, MODE, fault))
    have = ", ".join(CASES[c][0].split(":")[0] for c, _t, _s in traces)
    with PdfPages(pdf_path) as pdf:
        for p0 in range(0, len(panels), PANELS_PER_PAGE):
            page = panels[p0:p0 + PANELS_PER_PAGE]
            fig, axes = plt.subplots(len(page), 1, figsize=(11.0, 8.5), squeeze=False)
            fig.suptitle("%s  %s  --  base vs scenario 1 vs scenario 2   (%s)"
                         % (proj, fault, have), fontsize=11, fontweight="bold")
            for ax, name in zip(axes[:, 0], page):
                for ci, t, sig in traces:
                    v = sig.get(name)
                    if not v:
                        continue
                    lab, col, ls, lw = CASES[ci][0], CASES[ci][3], CASES[ci][4], CASES[ci][5]
                    tt, vv = _thin(t, v)
                    # "end" = the last value shown (at T_MAX), not the end of the file
                    _ve = v[-1]
                    if T_MAX is not None:
                        for _k in range(min(len(t), len(v)) - 1, -1, -1):
                            if t[_k] <= T_MAX:
                                _ve = v[_k]
                                break
                    ax.plot(tt, vv, color=col, linestyle=ls, linewidth=lw,
                            label="%s  (end %.3g)" % (lab, _ve))
                _w = _where(name, fbus, bmap, dist) if SHOW_NODES else ""
                ax.set_title(name + (("   --  " + _w) if _w else ""), fontsize=9, loc="left")
                ax.grid(True, color="#d9d9d9", linewidth=0.6)
                ax.tick_params(labelsize=8)
                ax.set_ylabel(_ylabel(name), fontsize=8)
                ax.set_xlabel("Time from fault (s)" if ALIGN_TO_FAULT else "Time (s)", fontsize=8)
                if T_MIN is not None or T_MAX is not None:
                    ax.set_xlim(left=T_MIN, right=T_MAX)
                if ALIGN_TO_FAULT:
                    ax.axvline(0.0, color="#999999", linewidth=0.8, linestyle=":")
                ax.legend(fontsize=7, loc="best", frameon=False)
            fig.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.07, hspace=0.75)
            pdf.savefig(fig)
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
        p = draw_fault(proj, fault, dirs, _W["dyntools"], _W["plt"], _W["PdfPages"])
        return proj, fault, p, time.time() - t0, ""
    except Exception as e:
        return proj, fault, None, time.time() - t0, str(e)


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
            line = ("[overlay] [%-30s] %d/%d  %s %-8s %s  (%s)  elapsed %s  ETA %s"
                    % (bar, done, total, proj, fault, "OK" if pdf else "FAILED",
                       _hms(sec), _hms(el), _hms(eta)))
            print(line)
            sys.stdout.flush()
            rows.append("%-14s %-8s %-7s %8s  %s" % (proj, fault, "OK" if pdf else "FAILED",
                                                    _hms(sec), pdf or err or ""))
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
