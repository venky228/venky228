# -*- coding: utf-8 -*-
"""
z7_reclose_status.py -- STATUS OF EVERY FAULT AFTER THE RECLOSURE RUN.

READ-ONLY. Reads the status file (00_STATUS_*.txt) of each run folder --
BASE, GIA and SGF -- for every project, and lists every fault with its state
in each of the three. Nothing in the study is changed: no script, no result,
no marker. The only files written are the two lists below (new files).

A fault that GAVE UP with reclosure in any of the three is listed as
RUN WITHOUT RECLOSURE (SPP removes the reclose when reclosing an islanded
generator causes issues).

    BASE  Base\\results_base_f\\<project>\\<project>_spp
    GIA   Projects\\results_proj_f\\<project>\\<project>_spp
    SGF   Projects\\results_proj_f\\<project>\\<project>_spp_s1_egfoff

Put it in the study folder (C:\\KV\\ENGIE\\CQ_MIT_f) and run:

    python z7_reclose_status.py

It writes, in the study folder:
    RECLOSE_STATUS_LIST.txt    the list (also printed)
    RECLOSE_STATUS_LIST.csv    the same, one row per fault, for Excel

Python 3.4+, standard library only.
"""
from __future__ import print_function

import csv
import glob
import os
import re
import sys
import time

# ============================================================================
# SETTINGS
# ============================================================================
STUDY_ROOT = ""        # "" = the folder this script is in (C:\KV\ENGIE\CQ_MIT_f)
PROJECTS = ["SantaFe", "IronStar", "EmpirePrairie", "EastFork"]

# (label, results folder under the study folder, run folder name)
CASES = [
    ("BASE", r"Base\results_base_f",     "{project}_spp"),
    ("GIA",  r"Projects\results_proj_f", "{project}_spp"),
    ("SGF",  r"Projects\results_proj_f", "{project}_spp_s1_egfoff"),
]

# the fault list, only to show the fault bus and the element tripped ("" = skip)
FAULT_LIST = r"FAULT_LISTS_BPM\SPP_FAULTS_CON_{project}.csv"

MAX_ATTEMPTS = 3       # MAX_SCENARIO_ATTEMPTS of the panel
OUT_NAME = "RECLOSE_STATUS_LIST"   # written in the study folder (.txt and .csv)

# ALSO LOOK AT outs\ (read only). The status file is written by the launcher;
# a run that GAVE UP there but whose .out was complete is marked done later by
# the plot pass (outs\<fault>.done). Such a fault ran to the end with reclosure
# and is shown DONE* -- not as gave up.
CHECK_OUTS = True

SPP_RULE = ('SPP: "if reclosing would cause islanding of a generator, it is removed '
            'from the contingency when the resulting fault is observed to cause '
            'issues due to reclosing of an islanded generator."')

# ============================================================================

_ROW_RX = re.compile(
    r"^\s{1,4}(\S+)\s+(not started|re-queued|[A-Z][A-Z-]*)\s+(\d+)\s+(\S+)\s+"
    r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d|-)\s*(.*)$")
_HEAD_RX = re.compile(r"LIVE STATUS\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")

GAVE_UP = ("GAVE-UP",)
FAILED = ("ERROR", "FAILED")
NOT_YET = ("RUNNING", "QUEUED", "re-queued", "not started", "INCOMPLETE", "INTERRUPTED")


def _root():
    if STUDY_ROOT:
        return STUDY_ROOT
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _fkey(fid):
    m = re.match(r"^([A-Za-z_]*)(\d+)(.*)$", str(fid))
    if m:
        return (m.group(1).upper(), int(m.group(2)), m.group(3))
    return (str(fid).upper(), 0, "")


def _results_dir(root, rel):
    """The results folder, or its older name results_f if only that exists."""
    p = os.path.join(root, rel.replace("\\", os.sep))
    if os.path.isdir(p):
        return p
    alt = os.path.join(os.path.dirname(p), "results_f")
    return alt if os.path.isdir(alt) else p


def _rel(p, root):
    try:
        return os.path.relpath(p, root)
    except ValueError:            # another drive
        return p


def find_run_folder(root, rel, name):
    """<results>\\<project>\\<name> (one folder per project), or the flat
       <results>\\<name>. "" when neither exists."""
    res = _results_dir(root, rel)
    proj = name.split("_")[0]
    for p in (os.path.join(res, proj, name), os.path.join(res, name)):
        if os.path.isdir(p):
            return p
    return ""


def find_status_file(run):
    """The newest 00_STATUS*.txt (or older LIVE_STATUS*.txt) of a run folder."""
    got = []
    for pat in ("00_STATUS*.txt", "LIVE_STATUS*.txt"):
        for d in (run, os.path.join(run, "reports")):
            got += glob.glob(os.path.join(d, pat))
    got = [g for g in got if os.path.isfile(g)]
    if not got:
        return ""
    return max(got, key=os.path.getmtime)


def read_status(path):
    """({fault: (state, attempts, worker, time, note)}, written-at, problem)."""
    rows, when, problem = {}, "", ""
    try:
        with open(path, "r", errors="replace") as fh:
            lines = fh.read().splitlines()
    except Exception as e:
        return rows, when, "could not read (%s)" % e
    for ln in lines:
        if not when:
            m = _HEAD_RX.search(ln)
            if m:
                when = m.group(1)
        if "NO SCENARIO LIST" in ln:
            problem = "the status file says: NO SCENARIO LIST"
        m = _ROW_RX.match(ln)
        if not m or m.group(1).lower() == "scenario":
            continue
        try:
            att = int(m.group(3))
        except ValueError:
            att = 0
        rows[m.group(1)] = (m.group(2), att, m.group(4), m.group(5), m.group(6).strip())
    if not rows and not problem:
        problem = "no scenario rows in the status file"
    return rows, when, problem


def outs_state(run, fid):
    """What outs\\ holds for this fault: 'done', or (attempts) -- read only."""
    od = os.path.join(run, "outs")
    if (os.path.isfile(os.path.join(od, fid + ".done"))
            and os.path.isfile(os.path.join(od, fid + ".out"))):
        return "done", 0
    try:
        with open(os.path.join(od, fid + ".attempts")) as fh:
            return "", int((fh.read() or "0").split()[0])
    except Exception:
        return "", 0


def read_fault_list(root, proj):
    """{fault: (fault bus, element tripped)} from the fault list, if readable."""
    out = {}
    if not FAULT_LIST:
        return out
    p = os.path.join(root, FAULT_LIST.replace("{project}", proj).replace("\\", os.sep))
    try:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                fid = (r.get("fault_id") or "").strip()
                if fid:
                    out[fid] = ((r.get("fault_bus") or "").strip(),
                                (r.get("trip_elements") or "").strip())
    except Exception:
        pass
    return out


def cell_of(row, disk, why_none="-"):
    """(text for the table, kind) for one fault in one case.
       kind: done | gaveup | notyet | missing (no status for it)"""
    if row is None:
        if disk == "done":
            return "DONE*", "done"
        return why_none, "missing"
    st, att = row[0], row[1]
    if st == "DONE":
        return "DONE", "done"
    if disk == "done":
        return "DONE*", "done"            # finished after the status was written
    if st in GAVE_UP:
        return "GAVE-UP (%d)" % att, "gaveup"
    if st in FAILED:
        if att >= MAX_ATTEMPTS:
            return "%s (%d)" % (st, att), "gaveup"
        return "%s (%d)" % (st, att), "notyet"
    if st == "INCOMPLETE" and att >= MAX_ATTEMPTS:
        return "INCOMPLETE (%d)" % att, "gaveup"
    if st in NOT_YET:
        return (st if not att else "%s (%d)" % (st, att)), "notyet"
    return (st if not att else "%s (%d)" % (st, att)), "notyet"


def main():
    root = _root()
    labels = [c[0] for c in CASES]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    L = []
    L.append("=" * 100)
    L.append(" RECLOSURE RUN -- STATUS OF EVERY FAULT IN %s" % " / ".join(labels))
    L.append(" read from the status file of each run folder -- nothing in the study was changed")
    L.append(" study folder %s      written %s" % (root, stamp))
    L.append("=" * 100)

    status_lines, csv_rows = [], []
    summary = []           # (project, [gave-up faults], [not finished faults])
    starred = False
    blocks = []

    for proj in PROJECTS:
        runs = {}
        for lab, rel, name in CASES:
            run = find_run_folder(root, rel, name.replace("{project}", proj))
            sf = find_status_file(run) if run else ""
            rows, when, problem = (read_status(sf) if sf else ({}, "", ""))
            # A STATUS FILE OLDER THAN THE RECLOSURE RUN describes the run before
            # it (the rerun has not reached this folder yet): not used.
            why_none = "-" if sf else "no status"
            flag = os.path.join(run, "RECLOSE_RERUN_STARTED.txt") if run else ""
            if sf and flag and os.path.isfile(flag) and os.path.getmtime(sf) < os.path.getmtime(flag):
                problem = ("written BEFORE the reclosure run started (%s) -- not used"
                           % os.path.basename(flag))
                rows, why_none = {}, "old status"
            runs[lab] = (run, sf, rows, when, problem, why_none)
            if not run:
                status_lines.append("  %-14s %-4s  no run folder (%s\\...\\%s)"
                                    % (proj, lab, rel, name.replace("{project}", proj)))
            elif not sf:
                status_lines.append("  %-14s %-4s  %s -- NO status file"
                                    % (proj, lab, _rel(run, root)))
            else:
                status_lines.append("  %-14s %-4s  %s   (%s)%s"
                                    % (proj, lab, _rel(sf, root),
                                       when or "time not read",
                                       ("  *** %s ***" % problem) if problem else ""))
        ids = set()
        for lab in labels:
            ids.update(k for k in runs[lab][2] if not k.upper().startswith("FLAT"))
        if not ids:
            blocks.append(["", "=" * 100,
                           " %s -- no faults found in any status file" % proj.upper()])
            continue
        flist = read_fault_list(root, proj)
        ids = sorted(ids, key=_fkey)

        gave, notyet, ran = [], [], []
        table = []
        counts = dict((lab, {"done": 0, "gaveup": 0, "notyet": 0, "missing": 0, "nofolder": 0})
                      for lab in labels)
        for fid in ids:
            cells, kinds, cases_gave, cases_open = [], [], [], []
            for lab in labels:
                run, sf, rows, when, problem, why_none = runs[lab]
                disk = ""
                if CHECK_OUTS and run:
                    disk, _a = outs_state(run, fid)
                txt, kind = cell_of(rows.get(fid), disk, why_none)
                if not run:
                    txt, kind = "no folder", "nofolder"
                if txt.endswith("*"):
                    starred = True
                cells.append(txt)
                kinds.append(kind)
                counts[lab][kind] += 1
                if kind == "gaveup":
                    cases_gave.append(lab)
                elif kind in ("notyet", "missing"):
                    cases_open.append(lab)
            if cases_gave:
                decision = "NO -- gave up (%s) -> run WITHOUT reclosure" % ", ".join(cases_gave)
                gave.append(fid)
            elif cases_open:
                decision = "not finished yet (%s)" % ", ".join(cases_open)
                notyet.append(fid)
            elif "done" in kinds:
                decision = "yes -- ran with reclosure"
                ran.append(fid)
            else:
                decision = "?"
            fb, el = flist.get(fid, ("", ""))
            table.append("  %-6s %-8s %-24s " % (fid, fb, el[:24])
                         + " ".join("%-14s" % c for c in cells) + " " + decision)
            csv_rows.append([proj, fid, fb, el] + cells
                            + ["NO" if cases_gave else ("not finished" if fid in notyet else
                                                        ("yes" if fid in ran else "?")),
                               ", ".join(cases_gave)])
        b = []
        b.append("")
        b.append("=" * 100)
        b.append(" %s -- %d fault(s): %d ran with reclosure, %d GAVE UP (run without reclosure)%s"
                 % (proj.upper(), len(ids), len(ran), len(gave),
                    (", %d not finished yet" % len(notyet)) if notyet else ""))
        b.append("   " + "    ".join("%s: %s" % (lab, (
            "no folder" if counts[lab]["nofolder"] == len(ids) else
            "%d done, %d gave up%s%s" % (
                counts[lab]["done"], counts[lab]["gaveup"],
                (", %d not finished" % counts[lab]["notyet"]) if counts[lab]["notyet"] else "",
                (", %d no status" % counts[lab]["missing"]) if counts[lab]["missing"] else "")))
            for lab in labels))
        b.append("-" * 100)
        b.append("  %-6s %-8s %-24s " % ("Fault", "Bus", "Element tripped")
                 + " ".join("%-14s" % lab for lab in labels) + " With reclosure?")
        b.append("-" * 100)
        b.extend(table)
        blocks.append(b)
        summary.append((proj, gave, notyet))

    L.append("")
    L.append(" STATUS FILES READ")
    L.extend(status_lines)
    for b in blocks:
        L.extend(b)

    # ---- the list asked for --------------------------------------------------
    L.append("")
    L.append("=" * 100)
    L.append(" FAULTS THAT GAVE UP WITH RECLOSURE -- RUN WITHOUT RECLOSURE")
    L.append("=" * 100)
    L.append(" These faults gave up when run with reclosure in at least one of %s"
             % " / ".join(labels))
    L.append(" (PSS/E could not complete the run with the islanded unit(s) reclosed).")
    L.append(" " + SPP_RULE)
    L.append(" The reclose is therefore removed for them: they are RUN WITHOUT RECLOSURE")
    L.append(" (fault cleared, the line stays open, the islanded unit(s) trip with it), and")
    L.append(" their results are those of the run without reclosure.")
    L.append("")
    n_all = 0
    for proj, gave, notyet in summary:
        n_all += len(gave)
        L.append("  %-14s %3d : %s" % (proj, len(gave), ", ".join(gave) if gave else "none"))
    L.append("  %-14s %3d" % ("TOTAL", n_all))
    pend = [(p, n) for p, _g, n in summary if n]
    if pend:
        L.append("")
        L.append(" NOT FINISHED YET (still running, queued or stopped part way) -- this list is not")
        L.append(" final until these are DONE or GAVE-UP; run this script again when the run ends:")
        for proj, n in pend:
            L.append("  %-14s %3d : %s" % (proj, len(n), ", ".join(n)))
    if starred:
        L.append("")
        L.append(" * DONE* = the status file does not say DONE, but outs\\<fault>.done and .out are")
        L.append("   there: the run was finished after the status file was last written (the plot")
        L.append("   pass marks a complete .out done). It ran with reclosure.")
    L.append("")
    L.append(" Nothing in the study was changed by this script.")
    L.append("=" * 100)

    text = "\n".join(L)
    print(text)
    tp = os.path.join(root, OUT_NAME + ".txt")
    cp = os.path.join(root, OUT_NAME + ".csv")
    try:
        with open(tp, "w") as fh:
            fh.write(text + "\n")
        with open(cp, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["project", "fault", "fault_bus", "element_tripped"] + labels
                       + ["with_reclosure", "gave_up_in"])
            for r in csv_rows:
                w.writerow(r)
        print("")
        print("written: %s" % tp)
        print("         %s" % cp)
    except Exception as e:
        print("")
        print("*** could not write the list files (%s) -- the list is above ***" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
