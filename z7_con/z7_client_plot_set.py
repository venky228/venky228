# -*- coding: utf-8 -*-
"""
z7_client_plot_set.py -- ONE COMPLETE SET OF RESULTS FOR THE CLIENT PLOTS.

After the reclosure rerun some faults finished WITH reclosure and some
crashed (gave up) with it. The report says the crashed ones were run WITHOUT
reclosure -- their results are the ones in the backup folders taken before the
rerun. This script puts together one study tree that holds, for every fault,
the results the report uses, so that z7_overlay_v2.py can plot all of them:

    fault finished with reclosure (BASE and GIA)  ->  the live run folders
    fault crashed with reclosure in ANY case      ->  the BACKUP folders, for
                                                      ALL three cases (base,
                                                      Scenario 1, Scenario 2),
                                                      so a plot never mixes a
                                                      run with reclosure and
                                                      one without
    fault not in the reclosure rerun              ->  the live run folders
    Empire Prairie F179 (no base case result)     ->  left out (EXCLUDE)

The new tree has the same layout as the study, so the overlay reads it as is:

    <OUT_ROOT>\\Base\\results_base_f\\<project>\\<project>_spp\\outs\\<fault>.out
    <OUT_ROOT>\\Projects\\results_proj_f\\<project>\\<project>_spp\\outs\\...
    <OUT_ROOT>\\Projects\\results_proj_f\\<project>\\<project>_spp_s1_egfoff\\outs\\...

with each case folder's OUT_LAYOUT*.txt (fast reader), flags\\BUS_MAP.csv and
violations list (02_VIOLATIONS_MERGED.csv: the rows of the run each fault
was taken from).

NOTHING IN THE STUDY IS CHANGED: the live and backup folders are only read.
Files are hard-linked into OUT_ROOT (instant, no extra disk space; the same
drive is needed) or copied (LINK_FILES = False, or when a link is refused).
A file already in OUT_ROOT that differs is moved to OUT_ROOT\\_replaced_<time>,
never deleted.

Then, in z7_overlay_v2.py, set
    ROOT = r"C:\\KV\\ENGIE\\CQ_MIT_f\\CLIENT_PLOT_SET"      (= OUT_ROOT here)
and run it: the PDFs go to <OUT_ROOT>\\overlay_v2_plots\\<project>\\.

RUN (Python 3.4+, standard library only):
    python z7_client_plot_set.py

Written in OUT_ROOT:
    CLIENT_PLOT_SET.txt   what was taken from where (also printed)
    CLIENT_PLOT_SET.csv   one row per fault: source and file of each case
"""
from __future__ import print_function

import csv
import glob
import io
import os
import re
import shutil
import sys
import time

# ============================================================================
# SETTINGS
# ============================================================================
STUDY_ROOT = r"C:\KV\ENGIE\CQ_MIT_f"
OUT_ROOT = r"C:\KV\ENGIE\CQ_MIT_f\CLIENT_PLOT_SET"   # NEW folder -> the overlay's ROOT
PROJECTS = ["SantaFe", "IronStar", "EmpirePrairie", "EastFork"]
MODE = "spp"

# (label, live results folder, BACKUP folder, run folder suffix) -- under STUDY_ROOT.
# The backup folder may hold <project>\<project>_spp<suffix>, <project>_spp<suffix>,
# or (base) the case folder itself as <project>\ -- all are searched.
CASES = [
    ("BASE", r"Base\results_base_f",     r"Base\results_base_f\Backup",          ""),
    ("GIA",  r"Projects\results_proj_f", r"Projects\results_proj_f\Backupkqv2",  ""),
    ("SGF",  r"Projects\results_proj_f", r"Projects\results_proj_f\Backupkqv2",  "_s1_egfoff"),
]
# A backup folder found automatically can be overridden here, e.g.
#   BACKUP_OVERRIDE = {("BASE", "SantaFe"): r"C:\...\Backup\SantaFe\SantaFe_spp"}
BACKUP_OVERRIDE = {}

EXCLUDE = {"EmpirePrairie": ["F179"]}     # left out of the set (no base case result)
INCLUDE_FLAT_RUN = True                   # FLAT_RUN.out too (the overlay draws it first)

# the faults of the reclosure rerun (only to know which faults were rerun; the
# status files say how each one ended)
RECLOSE_LIST = r"FAULT_LISTS_RECLOSE\SPP_FAULTS_CON_{project}.csv"
RERUN_MARKER = "RECLOSE_RERUN_STARTED.txt"   # in each live run folder
RECLOSE_START = ""          # "" = the marker's time | e.g. "2026-10-12 02:30" if there is no marker
MAX_ATTEMPTS = 3            # MAX_SCENARIO_ATTEMPTS of the panel

LINK_FILES = True           # True = hard links (same drive, no extra space) | False = copies
DRY_RUN = False             # True = write only the list (what WOULD be taken from where)

VIOLATION_FILES = ["02_VIOLATIONS*.csv", r"reports\SPP_VIOLATIONS*.csv"]   # as the overlay
# ============================================================================

TAG = "[client_plot_set]"
_ROW_RX = re.compile(
    r"^\s{1,4}(\S+)\s+(not started|re-queued|[A-Z][A-Z-]*)\s+(\d+)\s+(\S+)\s+"
    r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d|-)\s*(.*)$")


def _p(rel):
    rel = str(rel).replace("\\", os.sep).replace("/", os.sep)
    return rel if os.path.isabs(rel) else os.path.join(STUDY_ROOT, rel)


def _fkey(fid):
    m = re.match(r"^([A-Za-z_]*)(\d+)(.*)$", str(fid))
    return (m.group(1).upper(), int(m.group(2)), m.group(3)) if m else (str(fid).upper(), 0, "")


def _fnorm(f):
    s = str(f or "").strip().upper()
    m = re.match(r"^F0*(\d+)$", s)
    return ("F%s" % m.group(1)) if m else s


def _is_flat(fid):
    return str(fid).upper().startswith("FLAT")


def live_folder(rel, proj, suffix):
    name = "%s_%s%s" % (proj, MODE, suffix)
    res = _p(rel)
    for d in (os.path.join(res, proj, name), os.path.join(res, name)):
        if os.path.isdir(os.path.join(d, "outs")):
            return d
    return ""


def backup_folder(lab, rel, proj, suffix):
    if (lab, proj) in BACKUP_OVERRIDE:
        d = _p(BACKUP_OVERRIDE[(lab, proj)])
        return d if os.path.isdir(os.path.join(d, "outs")) else ""
    name = "%s_%s%s" % (proj, MODE, suffix)
    bk = _p(rel)
    for d in (os.path.join(bk, proj, name), os.path.join(bk, name)):
        if os.path.isdir(os.path.join(d, "outs")):
            return d
    top = os.path.join(bk, proj)
    if os.path.isdir(top):                  # deeper copy: ...\<project>\...\<name>\outs
        for dirpath, dirnames, _files in os.walk(top):
            if dirpath[len(top):].count(os.sep) >= 3:
                dirnames[:] = []
                continue
            if os.path.basename(dirpath) == name and "outs" in dirnames:
                return dirpath
        if not suffix and os.path.isdir(os.path.join(top, "outs")):
            return top                      # the base case folder copied as <project>\
    return ""


def outs_list(folder):
    if not folder:
        return set()
    return set(os.path.splitext(os.path.basename(p))[0]
               for p in glob.glob(os.path.join(folder, "outs", "*.out")))


def marker_time(run):
    if run:
        m = os.path.join(run, RERUN_MARKER)
        if os.path.isfile(m):
            return os.path.getmtime(m)
    if RECLOSE_START:
        return time.mktime(time.strptime(RECLOSE_START, "%Y-%m-%d %H:%M"))
    return None


def status_rows(run, t0):
    """{fault: (state, attempts)} from the newest status file of the run folder,
       only when it was written after the reclosure run started (else {})."""
    got = []
    for pat in ("00_STATUS*.txt", "LIVE_STATUS*.txt"):
        for d in (run, os.path.join(run, "reports")):
            got += [g for g in glob.glob(os.path.join(d, pat)) if os.path.isfile(g)]
    if not got:
        return {}, "no status file"
    sf = max(got, key=os.path.getmtime)
    if t0 is not None and os.path.getmtime(sf) < t0:
        return {}, "status written before the reclosure run -- this case was not rerun"
    rows = {}
    with open(sf, "r", errors="replace") as fh:
        for ln in fh.read().splitlines():
            m = _ROW_RX.match(ln)
            if m and m.group(1).lower() != "scenario":
                try:
                    att = int(m.group(3))
                except ValueError:
                    att = 0
                rows[m.group(1)] = (m.group(2), att)
    return rows, os.path.basename(sf)


def rerun_list(proj):
    p = _p(RECLOSE_LIST.replace("{project}", proj))
    out = set()
    try:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                fid = (r.get("fault_id") or "").strip()
                if fid:
                    out.add(fid)
    except Exception:
        pass
    return out


def done_after(run, fid, t0):
    """outs\\<fault>.out and .done exist and were written by the reclosure run."""
    od = os.path.join(run, "outs")
    o, d = os.path.join(od, fid + ".out"), os.path.join(od, fid + ".done")
    if not (os.path.isfile(o) and os.path.isfile(d)):
        return False
    return t0 is None or (os.path.getmtime(d) >= t0 and os.path.getmtime(o) >= t0)


_TYPICAL = {}


def complete(folder, fid):
    """A usable .out: a .done beside it, or (a backup copy without .done) a
       .out at least 90 % of the size of the case's typical .out."""
    if not folder:
        return False
    o = os.path.join(folder, "outs", fid + ".out")
    if not os.path.isfile(o):
        return False
    if os.path.isfile(os.path.join(folder, "outs", fid + ".done")):
        return True
    if folder not in _TYPICAL:
        sizes = sorted(os.path.getsize(p) for p in glob.glob(os.path.join(folder, "outs", "*.out")))
        _TYPICAL[folder] = sizes[len(sizes) // 2] if sizes else 0
    return os.path.getsize(o) >= 0.9 * _TYPICAL[folder]


def case_state(rows, run, fid, t0):
    """done | gaveup | notyet for a rerun fault in one case."""
    if run and done_after(run, fid, t0):
        return "done"
    st, att = rows.get(fid, ("", 0))
    if st == "GAVE-UP":
        return "gaveup"
    if st in ("ERROR", "FAILED", "INCOMPLETE") and att >= MAX_ATTEMPTS:
        return "gaveup"
    return "notyet"


# ---- writing ----------------------------------------------------------------
_STAMP = time.strftime("%Y%m%d_%H%M%S")
_N = {"linked": 0, "copied": 0, "same": 0, "moved": 0}


def _same(a, b):
    try:
        if os.path.samefile(a, b):
            return True
    except Exception:
        pass
    try:
        return (os.path.getsize(a) == os.path.getsize(b)
                and int(os.path.getmtime(a)) == int(os.path.getmtime(b)))
    except Exception:
        return False


def place(src, dst):
    if DRY_RUN:
        return
    if os.path.exists(dst):
        if _same(src, dst):
            _N["same"] += 1
            return
        keep = os.path.join(OUT_ROOT, "_replaced_%s" % _STAMP, os.path.relpath(dst, OUT_ROOT))
        if not os.path.isdir(os.path.dirname(keep)):
            os.makedirs(os.path.dirname(keep))
        shutil.move(dst, keep)               # never deleted: moved aside
        _N["moved"] += 1
    if not os.path.isdir(os.path.dirname(dst)):
        os.makedirs(os.path.dirname(dst))
    if LINK_FILES:
        try:
            os.link(src, dst)
            _N["linked"] += 1
            return
        except Exception:
            pass
    shutil.copy2(src, dst)
    _N["copied"] += 1


def viol_file(d):
    cands = []
    for pat in VIOLATION_FILES:
        cands.extend(glob.glob(os.path.join(d, pat.replace("\\", os.sep).replace("/", os.sep))))
    cands = sorted(set(c for c in cands if os.path.isfile(c)))
    full = [c for c in cands if "_SELECTED" not in os.path.basename(c).upper()]
    pool = full or cands
    return max(pool, key=os.path.getmtime) if pool else ""


def read_viol(d):
    p = viol_file(d) if d else ""
    if not p:
        return [], []
    with open(p, newline="", errors="replace") as fh:
        r = csv.DictReader(fh)
        return list(r.fieldnames or []), list(r)


def side_files(dst_case, live, backup, src_of):
    """OUT_LAYOUT*.txt, flags\\BUS_MAP.csv and the merged violations list."""
    seen = {}
    for tag, folder in (("", live), ("BACKUP", backup)):
        if not folder:
            continue
        for sub in ("outs", ""):
            for p in glob.glob(os.path.join(folder, sub, "OUT_LAYOUT*.txt")):
                name = os.path.basename(p)
                try:
                    body = open(p, "rb").read()
                except Exception:
                    continue
                if seen.get(name) == body:
                    continue
                if name in seen:                 # the backup's differs: keep both
                    name = name.replace("OUT_LAYOUT", "OUT_LAYOUT_" + tag, 1)
                seen[name] = body
                place(p, os.path.join(dst_case, sub, name))
    for folder in (live, backup):
        bm = os.path.join(folder, "flags", "BUS_MAP.csv") if folder else ""
        if bm and os.path.isfile(bm):
            place(bm, os.path.join(dst_case, "flags", "BUS_MAP.csv"))
            break
    lf, lr = read_viol(live)
    bf, br = read_viol(backup)
    fields = list(lf) + [f for f in bf if f not in lf]
    if not fields:
        return 0
    by_l, by_b = {}, {}
    for r in lr:
        by_l.setdefault(_fnorm(r.get("fault_id")), []).append(r)
    for r in br:
        by_b.setdefault(_fnorm(r.get("fault_id")), []).append(r)
    rows = []
    for f in sorted(src_of, key=_fkey):
        s = src_of[f]
        if s == "LIVE":                      # rerun with reclosure: the live list only
            rows += by_l.get(f, [])
        elif s == "BACKUP":                  # crashed with reclosure: the backup's list
            rows += by_b.get(f, [])
        else:                                # not rerun: same run in both -- the live list,
            rows += by_l.get(f) or by_b.get(f, [])   # or the backup's if the live one lacks it
    if not DRY_RUN:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        body = buf.getvalue()
        if not os.path.isdir(dst_case):
            os.makedirs(dst_case)
        out = os.path.join(dst_case, "02_VIOLATIONS_MERGED.csv")
        if os.path.exists(out):
            with open(out, "r", newline="") as fh:
                if fh.read() == body:
                    return len(rows)
            keep = os.path.join(OUT_ROOT, "_replaced_%s" % _STAMP, os.path.relpath(out, OUT_ROOT))
            if not os.path.isdir(os.path.dirname(keep)):
                os.makedirs(os.path.dirname(keep))
            shutil.move(out, keep)           # never deleted: moved aside
        with open(out, "w", newline="") as fh:
            fh.write(body)
    return len(rows)


def main():
    if os.path.abspath(OUT_ROOT).rstrip(os.sep).lower() == os.path.abspath(STUDY_ROOT).rstrip(os.sep).lower():
        print("%s OUT_ROOT must be a NEW folder, not the study folder" % TAG)
        return 1
    labels = [c[0] for c in CASES]
    L = ["=" * 100,
         " CLIENT PLOT SET -- one complete set of results for z7_overlay_v2.py",
         " study %s" % STUDY_ROOT,
         " set   %s      written %s%s" % (OUT_ROOT, time.strftime("%Y-%m-%d %H:%M"),
                                         "   (DRY RUN: nothing written)" if DRY_RUN else ""),
         "=" * 100, ""]
    rows_csv = []
    totals = []
    for proj in PROJECTS:
        live, back, rows, why, t0s = {}, {}, {}, {}, {}
        for lab, lrel, brel, suf in CASES:
            live[lab] = live_folder(lrel, proj, suf)
            back[lab] = backup_folder(lab, brel, proj, suf)
            t0s[lab] = marker_time(live[lab])
            if live[lab]:
                rows[lab], why[lab] = status_rows(live[lab], t0s[lab])
            else:
                rows[lab], why[lab] = {}, "no live run folder"
        L.append("-" * 100)
        L.append(" %s" % proj.upper())
        for lab in labels:
            L.append("   %-4s live   %s" % (lab, live[lab] or "*** NOT FOUND ***"))
            L.append("        backup %s" % (back[lab] or "*** NOT FOUND ***"))
            L.append("        status %s" % why[lab])
        rerun = rerun_list(proj)
        for lab in labels:
            rerun.update(k for k in rows[lab] if not _is_flat(k))
        faults = set()
        for lab in labels:
            faults |= outs_list(live[lab]) | outs_list(back[lab])
        excl = set(EXCLUDE.get(proj, []))
        faults = sorted((f for f in faults if f not in excl and (INCLUDE_FLAT_RUN or not _is_flat(f))),
                        key=_fkey)
        n = {"with": [], "without": [], "notrerun": 0, "pending": [], "missing": []}
        choice = {}
        for fid in faults:
            states = {}
            if fid in rerun:
                for lab in labels:
                    if rows[lab]:             # this case was part of the reclosure run
                        states[lab] = case_state(rows[lab], live[lab], fid, t0s[lab])
            gave = [lab for lab, s in states.items() if s == "gaveup"]
            open_ = [lab for lab, s in states.items() if s == "notyet"]
            if gave:
                src, what = "BACKUP", "WITHOUT reclosure (crashed with it in %s)" % ", ".join(sorted(gave))
                n["without"].append(fid)
            elif open_:
                src, what = "BACKUP", "reclosure run NOT FINISHED in %s -- backup used" % ", ".join(sorted(open_))
                n["pending"].append(fid)
            elif states:
                src, what = "LIVE", "WITH reclosure"
                n["with"].append(fid)
            else:
                src, what = "LIVE", "not in the reclosure rerun"
                n["notrerun"] += 1
            choice[("rerun", fid)] = bool(states)
            per = []
            for lab, lrel, brel, suf in CASES:
                want = src
                folder = live[lab] if want == "LIVE" else back[lab]
                note = ""
                if not complete(folder, fid):
                    alt = back[lab] if want == "LIVE" else ""
                    if alt and complete(alt, fid):
                        want, folder, note = "BACKUP", alt, "live .out missing or incomplete"
                    else:
                        want, folder = "MISSING", ""
                per.append((lab, want, folder, note))
                choice[(lab, fid)] = (want, folder)
            if any(w == "MISSING" for _l, w, _f, _n in per):
                n["missing"].append("%s(%s)" % (fid, ",".join(l for l, w, _f, _n in per if w == "MISSING")))
            rows_csv.append([proj, fid, what] + [x for _l, w, f, note in per
                                                for x in (w, os.path.join(f, "outs", fid + ".out") if f else "", note)])
        # ---- write the case folders ----------------------------------------
        for lab, lrel, brel, suf in CASES:
            name = "%s_%s%s" % (proj, MODE, suf)
            dst_case = os.path.join(OUT_ROOT, lrel.replace("\\", os.sep), proj, name)
            src_of = {}
            for fid in faults:
                want, folder = choice[(lab, fid)]
                if want == "MISSING":
                    continue
                src_of[_fnorm(fid)] = (want if want == "BACKUP" or choice[("rerun", fid)]
                                       else "LIVE_NOT_RERUN")
                place(os.path.join(folder, "outs", fid + ".out"), os.path.join(dst_case, "outs", fid + ".out"))
                done = os.path.join(folder, "outs", fid + ".done")
                if os.path.isfile(done):
                    place(done, os.path.join(dst_case, "outs", fid + ".done"))
            nv = side_files(dst_case, live[lab], back[lab], src_of)
            L.append("   %-4s -> %s  (%d .out, %d violation rows)"
                     % (lab, os.path.relpath(dst_case, OUT_ROOT), len(src_of), nv))
        nf = len([f for f in faults if not _is_flat(f)])
        L.append("")
        L.append("   %d faults%s: %d with reclosure, %d WITHOUT reclosure (from the backup), "
                 "%d not in the rerun%s"
                 % (nf, (" (left out: %s)" % ", ".join(sorted(excl))) if excl else "",
                    len(n["with"]), len(n["without"]), n["notrerun"],
                    (", %d PENDING" % len(n["pending"])) if n["pending"] else ""))
        if n["without"]:
            L.append("   from the backup (all three cases): %s" % ", ".join(n["without"]))
        if n["pending"]:
            L.append("   *** reclosure run not finished, backup used: %s" % ", ".join(n["pending"]))
        if n["missing"]:
            L.append("   *** NO usable .out (not in the set): %s" % ", ".join(n["missing"]))
        L.append("")
        totals.append((proj, nf, len(n["with"]), len(n["without"]), n["notrerun"],
                       len(n["pending"]), len(n["missing"])))
    L.append("=" * 100)
    L.append(" %-14s %7s %8s %9s %10s %8s %8s" % ("project", "faults", "with", "without",
                                                   "not rerun", "pending", "missing"))
    for t in totals:
        L.append(" %-14s %7d %8d %9d %10d %8d %8d" % t)
    L.append("")
    if not DRY_RUN:
        L.append(" files: %d hard-linked, %d copied, %d already there, %d moved aside%s"
                 % (_N["linked"], _N["copied"], _N["same"], _N["moved"],
                    (" (to _replaced_%s)" % _STAMP) if _N["moved"] else ""))
    L.append(" NEXT: in z7_overlay_v2.py set  ROOT = r\"%s\"  and run it;" % OUT_ROOT)
    L.append("       the PDFs go to %s" % os.path.join(OUT_ROOT, "overlay_v2_plots", "<project>"))
    L.append(" Nothing in the study folders was changed.")
    L.append("=" * 100)
    text = "\n".join(L)
    print(text)
    if not os.path.isdir(OUT_ROOT):
        os.makedirs(OUT_ROOT)
    with open(os.path.join(OUT_ROOT, "CLIENT_PLOT_SET.txt"), "w") as fh:
        fh.write(text + "\n")
    with open(os.path.join(OUT_ROOT, "CLIENT_PLOT_SET.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        hdr = ["project", "fault", "results used"]
        for lab in labels:
            hdr += [lab + " source", lab + " file", lab + " note"]
        w.writerow(hdr)
        for r in rows_csv:
            w.writerow(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
