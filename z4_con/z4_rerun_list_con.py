# -*- coding: utf-8 -*-
"""
z4_rerun_list_con.py -- WHICH FAULTS HAVE TO BE RUN AGAIN, per project, and why.

    C:\\Python34\\python.exe z4_rerun_list_con.py            # report only
    C:\\Python34\\python.exe z4_rerun_list_con.py --clear    # also clear their markers

Reads, for every project and both cases (Base\\results_base\\<proj>_spp and
Projects\\results_proj\\<proj>_spp):

  1. reports\\UNSWITCHED_BRANCHES_<KIND>_<proj>.txt  (or the per-scenario
     outs\\Fxx_UNSWITCHED.txt files) -- scenarios whose trip or reclose did NOT
     take. The study still simulated and scored them, so their .out is not the
     contingency described. Each one is sorted into:
        NOT IN CASE     the branch does not exist in this .sav  -> fix the list
        WRONG CKT       branch exists under another circuit id  -> fix the list
        3-WINDING       a 3-winding transformer named as 2-wdg  -> trip_3wind
        OTHER           anything else the study reported
     and, using flags\\BUS_MAP.csv, says whether the far bus itself is in the
     case (a queued-project tap SPP has and this case does not) or whether both
     buses exist and only the branch between them is missing.

  2. flags\\RUN_SUMMARY_<KIND>_<proj>.csv -- scenarios that CRASHED / GAVE UP.

Writes RERUN_UNSWITCHED.txt and RERUN_UNSWITCHED.csv in the study root, with a
ready-to-paste ONLY_FAULTS line per project.

--clear removes the .done / .attempts markers of the listed scenarios on BOTH
sides so a PIPELINE = "missing" run simulates them again. It does not touch any
.out, report or measurement. Do this only AFTER the fault list is corrected:
re-running an uncorrected definition reproduces the same non-event.

Stdlib only, Python 3.4.
"""
from __future__ import print_function
import os
import re
import sys
import csv
import glob
import time

# ---- where things are --------------------------------------------------------
ROOT = ""                                # "" = the folder this script is in
BASE_FOLDER = "Base"
PROJ_FOLDER = "Projects"
RESULTS_BASE = "results_base"
RESULTS_PROJ = "results_proj"
MODE = "spp"                             # results folder suffix: <proj>_spp
PROJECTS = []                            # [] = every <name>_spp folder found
FAULT_LIST = r"{root}\SPP_FAULTS_CON_{project}.csv"   # for con_id / description

OUT_TXT = "RERUN_UNSWITCHED.txt"
OUT_CSV = "RERUN_UNSWITCHED.csv"


def _root():
    if ROOT:
        return ROOT
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _fault_list_path(proj):
    """FAULT_LIST with {root}/{project} filled in, separators normalised so the
       Windows-style template also resolves when run elsewhere."""
    p = FAULT_LIST.replace("{root}", _root()).replace("{project}", proj)
    return os.path.normpath(p.replace("\\", os.sep).replace("/", os.sep))


def _fkey(fid):
    m = re.search(r"(\d+)", str(fid))
    return (int(m.group(1)) if m else 10 ** 9, str(fid))


# ---- readers ------------------------------------------------------------------
def read_unswitched_report(rdir, kind, proj):
    """{fid: [(step, branch, state), ...]} from the merged report."""
    out = {}
    cands = [os.path.join(rdir, "reports", "UNSWITCHED_BRANCHES_%s_%s.txt" % (kind, proj)),
             os.path.join(rdir, "UNSWITCHED_BRANCHES_%s_%s.txt" % (kind, proj)),
             os.path.join(rdir, "reports", "UNSWITCHED_BRANCHES.txt")]
    p = next((c for c in cands if os.path.isfile(c)), None)
    if not p:
        return out, ""
    cur = None
    with open(p, "r", errors="replace") as fh:
        for ln in fh:
            s = ln.rstrip("\r\n")
            if not s.strip():
                continue
            if s.startswith("=") or s.startswith("-"):
                cur = None
                continue
            if not s.startswith(" ") and re.match(r"^[A-Za-z]\w*\d+$", s.strip()):
                cur = s.strip()
                out.setdefault(cur, [])
                continue
            if cur and s.startswith(" "):
                parts = s.split()
                if not parts:
                    continue
                if parts[0] == "final" and len(parts) > 1:
                    step, rest = "final trip", parts[2:]
                else:
                    step, rest = parts[0], parts[1:]
                branch = " ".join(rest[:2]) if len(rest) >= 2 else " ".join(rest)
                state = " ".join(rest[3:]) if len(rest) > 3 else ""
                out[cur].append((step, branch, state))
    return out, p


def read_unswitched_perscenario(rdir):
    """Fallback: outs\\Fxx_UNSWITCHED.txt written by the workers."""
    out = {}
    for p in glob.glob(os.path.join(rdir, "outs", "*_UNSWITCHED.txt")):
        fid = os.path.basename(p)[:-len("_UNSWITCHED.txt")]
        body, on = [], False
        with open(p, "r", errors="replace") as fh:
            for ln in fh:
                s = ln.rstrip("\r\n")
                if s.startswith("-" * 10):
                    on = True
                    continue
                if on and s.startswith("=" * 10):
                    break
                if on and s.strip():
                    parts = s.split()
                    if parts[0] == "final" and len(parts) > 1:
                        step, rest = "final trip", parts[2:]
                    else:
                        step, rest = parts[0], parts[1:]
                    branch = " ".join(rest[:2]) if len(rest) >= 2 else " ".join(rest)
                    state = " ".join(rest[3:]) if len(rest) > 3 else ""
                    body.append((step, branch, state))
        out[fid] = body
    return out


def read_crashed(rdir, kind, proj):
    """{fid: status} for scenarios that did not complete, from RUN_SUMMARY csv."""
    out = {}
    cands = [os.path.join(rdir, "flags", "RUN_SUMMARY_%s_%s.csv" % (kind, proj)),
             os.path.join(rdir, "flags", "RUN_SUMMARY.csv")]
    p = next((c for c in cands if os.path.isfile(c)), None)
    if not p:
        return out
    with open(p, "r", newline="") as fh:
        for r in csv.DictReader(fh):
            sid = (r.get("scenario") or "").strip()
            st = (r.get("run_status") or "").strip().upper()
            if sid and st and st not in ("DONE", "COMPLETE", "OK") and "DONE" not in st:
                out[sid] = st
    return out


def read_bus_map(rdir):
    """(buses {num: (kv, area, name)}, links set of (a, b)) from flags\\BUS_MAP.csv."""
    buses, links = {}, set()
    p = os.path.join(rdir, "flags", "BUS_MAP.csv")
    if not os.path.isfile(p):
        return buses, links
    with open(p, "r", newline="", errors="replace") as fh:
        for r in csv.reader(fh):
            if not r:
                continue
            if r[0] == "B" and len(r) >= 2:
                try:
                    buses[int(r[1])] = (r[2] if len(r) > 2 else "", r[3] if len(r) > 3 else "",
                                        r[4] if len(r) > 4 else "")
                except ValueError:
                    pass
            elif r[0] == "L" and len(r) >= 3:
                try:
                    a, b = int(r[1]), int(r[2])
                    links.add((min(a, b), max(a, b)))
                except ValueError:
                    pass
    return buses, links


def read_fault_list(proj):
    """{fid: row dict} from the shared DISIS list, if present."""
    p = _fault_list_path(proj)
    out = {}
    if not os.path.isfile(p):
        return out
    with open(p, "r", newline="", errors="replace") as fh:
        for r in csv.DictReader(fh):
            fid = (r.get("fault_id") or "").strip()
            if fid:
                out[fid] = r
    return out


# ---- classification -----------------------------------------------------------
def classify(state):
    s = (state or "").lower()
    if "not in this case" in s:
        return "NOT IN CASE"
    if "under circuit id" in s:
        return "WRONG CKT"
    if "3-winding" in s:
        return "3-WINDING"
    if "already out of service" in s:
        return "ALREADY OPEN"
    return "OTHER"


def bus_diag(branch, buses, links, state=""):
    """'far bus 765035 not in case' / 'both buses in case, no branch between them' / ''."""
    if "3-winding" in (state or "").lower():
        return "a 3-winding transformer -- name it in trip_3wind, not as a branch"
    m = re.match(r"(\d+)-(\d+)", branch or "")
    if not m or not buses:
        return ""
    a, b = int(m.group(1)), int(m.group(2))
    missing = [x for x in (a, b) if x not in buses]
    if missing:
        return "bus %s not in this case" % ", ".join(str(x) for x in missing)
    if (min(a, b), max(a, b)) not in links:
        return "both buses in the case, but no branch between them"
    return "both buses and a branch exist -- see the circuit id"


# ---- main ---------------------------------------------------------------------
def main():
    clear = "--clear" in sys.argv[1:]
    root = _root()
    sides = [("BASE", os.path.join(root, BASE_FOLDER, RESULTS_BASE)),
             ("PROJ", os.path.join(root, PROJ_FOLDER, RESULTS_PROJ))]
    projects = list(PROJECTS)
    if not projects:
        seen = set()
        for _k, rroot in sides:
            for d in glob.glob(os.path.join(rroot, "*_%s" % MODE)):
                if os.path.isdir(d):
                    seen.add(os.path.basename(d)[:-(len(MODE) + 1)])
        projects = sorted(seen)
    if not projects:
        print("no <project>_%s results folders under %s" % (MODE, root))
        return 1

    L = []
    rows = []          # for the csv
    per_proj_ids = {}  # proj -> set of ids to re-run (unswitched + crashed)
    L.append("FAULTS TO RUN AGAIN -- %s" % time.strftime("%Y-%m-%d %H:%M"))
    L.append("root %s" % root)
    L.append("=" * 100)
    for proj in projects:
        flist = read_fault_list(proj)
        unsw = {}       # fid -> {kind: [(step, branch, state, diag)]}
        crashed = {}    # fid -> {kind: status}
        for kind, rroot in sides:
            rdir = os.path.join(rroot, "%s_%s" % (proj, MODE))
            if not os.path.isdir(rdir):
                continue
            buses, links = read_bus_map(rdir)
            u, src = read_unswitched_report(rdir, kind, proj)
            if not u:
                u = read_unswitched_perscenario(rdir)
            for fid, items in u.items():
                unsw.setdefault(fid, {})[kind] = [
                    (st, br, sta, bus_diag(br, buses, links, sta)) for (st, br, sta) in items]
            for fid, st in read_crashed(rdir, kind, proj).items():
                crashed.setdefault(fid, {})[kind] = st
        ids = set(unsw) | set(crashed)
        per_proj_ids[proj] = ids
        L.append("")
        L.append("%s   %d fault(s) to re-run: %d unswitched, %d crashed/gave-up"
                 % (proj, len(ids), len(unsw), len(crashed)))
        L.append("-" * 100)
        if unsw:
            L.append("  UNSWITCHED -- simulated and scored, but NOT the event described. FIX THE LIST FIRST.")
            by_cat = {}
            for fid in sorted(unsw, key=_fkey):
                cats = set()
                for kind in ("BASE", "PROJ"):
                    for (st, br, sta, dg) in unsw[fid].get(kind, []):
                        cats.add(classify(sta))
                cat = ("NOT IN CASE" if "NOT IN CASE" in cats else
                       "WRONG CKT" if "WRONG CKT" in cats else
                       "3-WINDING" if "3-WINDING" in cats else sorted(cats)[0] if cats else "OTHER")
                by_cat.setdefault(cat, []).append(fid)
                fr = flist.get(fid, {})
                head = " ".join(x for x in ((fr.get("con_id") or "").strip(),
                                            ("(%s)" % fr.get("planning_event").strip()) if fr.get("planning_event") else "",
                                            (fr.get("source") or "").strip()) if x)
                L.append("  %-6s %-12s %s" % (fid, cat, head))
                for kind in ("BASE", "PROJ"):
                    for (st, br, sta, dg) in unsw[fid].get(kind, []):
                        L.append("         %-4s %-11s %-22s %s%s"
                                 % (kind, st, br, sta, ("  [%s]" % dg) if dg else ""))
                        rows.append([proj, fid, "unswitched", cat, kind, st, br, sta, dg,
                                     (fr.get("con_id") or ""), (fr.get("planning_event") or "")])
            L.append("")
            L.append("  what to change in %s:" % _fault_list_path(proj))
            if by_cat.get("NOT IN CASE"):
                L.append("    NOT IN CASE  %s" % ", ".join(by_cat["NOT IN CASE"]))
                L.append("                 the branch (usually its far bus) is not in this .sav. Either map the")
                L.append("                 event to the in-case element SPP's case represents, or mark it not")
                L.append("                 simulable here and leave it out. Do not re-run as-is.")
            if by_cat.get("WRONG CKT"):
                L.append("    WRONG CKT    %s" % ", ".join(by_cat["WRONG CKT"]))
                L.append("                 the circuit id named does not exist here (a parallel circuit SPP's")
                L.append("                 case has). Decide: drop, or redefine on the circuit that exists")
                L.append("                 (that is a harsher event -- say so in the description).")
            if by_cat.get("3-WINDING"):
                L.append("    3-WINDING    %s" % ", ".join(by_cat["3-WINDING"]))
                L.append("                 move the element to the trip_3wind column (w1-w2-w3 ckt).")
            for c in sorted(by_cat):
                if c not in ("NOT IN CASE", "WRONG CKT", "3-WINDING"):
                    L.append("    %-12s %s" % (c, ", ".join(by_cat[c])))
        if crashed:
            L.append("")
            L.append("  CRASHED / GAVE UP -- no verdict; needs a fresh attempt budget (and, for the")
            L.append("  reclose-into-fault crashes, SIMULATE_RECLOSE = False or the model fixed).")
            for fid in sorted(crashed, key=_fkey):
                sides_txt = "  ".join("%s:%s" % (k, crashed[fid][k]) for k in ("BASE", "PROJ") if k in crashed[fid])
                L.append("  %-6s %s" % (fid, sides_txt))
                for k in crashed[fid]:
                    rows.append([proj, fid, "crashed", crashed[fid][k], k, "", "", "", "", "", ""])
        if ids:
            L.append("")
            L.append("  paste into z4_cmp_all_con.py (PROJECTS = [\"%s\"], PIPELINE = \"missing\"):" % proj)
            L.append("  ONLY_FAULTS = [%s]" % ", ".join('"%s"' % f for f in sorted(ids, key=_fkey)))

    L.append("")
    L.append("=" * 100)
    L.append("--clear removes the .done / .attempts markers of every id above on both sides so")
    L.append("PIPELINE = \"missing\" simulates them again. Fix the fault list BEFORE clearing the")
    L.append("unswitched ones." + ("   [markers cleared this run]" if clear else "   [not cleared: run with --clear]"))

    txt = os.path.join(root, OUT_TXT)
    with open(txt, "w") as fh:
        fh.write("\n".join(L) + "\n")
    with open(os.path.join(root, OUT_CSV), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["project", "fault_id", "kind", "category", "case", "step", "branch",
                    "state", "bus_diagnosis", "con_id", "planning_event"])
        w.writerows(rows)
    print("\n".join(L))
    print("\n-> %s\n-> %s" % (txt, os.path.join(root, OUT_CSV)))

    if clear:
        n = 0
        for proj, ids in per_proj_ids.items():
            for kind, rroot in sides:
                outs = os.path.join(rroot, "%s_%s" % (proj, MODE), "outs")
                for fid in ids:
                    for ext in (".done", ".attempts"):
                        p = os.path.join(outs, fid + ext)
                        if os.path.isfile(p):
                            try:
                                os.remove(p)
                                n += 1
                            except Exception as e:
                                print("could not remove %s (%s)" % (p, e))
        print("cleared %d marker file(s)" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
