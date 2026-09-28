# -*- coding: utf-8 -*-
"""Compare the scenarios in a violations_export folder (from z7_vio_export.py).

  python z7_vio_compare.py [EXPORT_DIR]

EXPORT_DIR defaults to violations_export next to this script.  Python 3.4,
standard library only.  Only faults scored in BOTH scenarios of a pair are
compared; the rest are counted as "not comparable".  'review' rows are
information only and are left out of the violation comparison.

Pairs (per project):
  GIA vs BASE                    -- project impact, EGF on
  GIA_s1_egfoff vs BASE_egfoff   -- project impact, EGF off
  BASE_egfoff vs BASE            -- EGF effect without the project
  GIA_s1_egfoff vs GIA           -- EGF effect with the project

Writes into EXPORT_DIR\\compare:
  COMPARE_SUMMARY.txt        one line per project/pair + the gaps
  COMPARE_FAULTS.csv         per fault: verdicts, failed criteria added/cleared
  COMPARE_VIOLATIONS.csv     per violation (kind, element): new / cleared / worse / better
"""
import csv
import os
import re
import sys
from collections import defaultdict

PAIRS = [("GIA", "BASE"), ("GIA_s1_egfoff", "BASE_egfoff"),
         ("BASE_egfoff", "BASE"), ("GIA_s1_egfoff", "GIA")]
SKIP_KINDS = ("review",)
# a value change smaller than this is "same" (pu, deg, MW all share it)
TOL = {"pu": 0.005, "deg": 0.5, "MW": 1.0}


def _open(p):
    return open(p, "r", newline="", encoding="utf-8-sig")


def _fnum(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _fkey(f):
    m = re.match(r"F(\d+)$", f)
    return (0, int(m.group(1)), f) if m else (1, 0, f)


def _ranges(ids):
    ids = sorted(set(ids), key=_fkey)
    out, run = [], []
    for f in ids:
        m = re.match(r"F(\d+)$", f)
        if m and run and re.match(r"F(\d+)$", run[-1]) and \
                int(re.match(r"F(\d+)$", run[-1]).group(1)) + 1 == int(m.group(1)):
            run.append(f)
            continue
        if run:
            out.append(run[0] if len(run) == 1 else run[0] + "-" + run[-1])
        run = [f]
    if run:
        out.append(run[0] if len(run) == 1 else run[0] + "-" + run[-1])
    return ", ".join(out)


def _worse(kind, unit, new, old):
    """+1 when new is worse than old, -1 better, 0 same."""
    if new is None or old is None:
        return 0
    tol = TOL.get(unit, 0.005)
    d = new - old
    if abs(d) < tol:
        return 0
    # low voltage (recovery / steady below 0.90) is worse when lower
    if kind == "recovery" or (kind == "steady" and new < 1.0 and old < 1.0):
        return 1 if d < 0 else -1
    # tripped: ended MW -- nothing to compare by size
    if kind == "tripped":
        return 0
    return 1 if d > 0 else -1


def load(d):
    verdict = {}          # (proj, scen, fault) -> PASS/FAIL
    crit = defaultdict(set)
    vio = defaultdict(dict)  # (proj, scen, fault) -> {(kind, elem): (value, unit, note)}
    with _open(os.path.join(d, "ALL_FAILED_CRITERIA.csv")) as fh:
        for r in csv.DictReader(fh):
            crit[(r["project"], r["scenario"], r["fault_id"])].add(r["criterion"])
    with _open(os.path.join(d, "ALL_VIOLATIONS.csv")) as fh:
        for r in csv.DictReader(fh):
            k = (r["project"], r["scenario"], r["fault_id"])
            if r["violation"] in SKIP_KINDS:
                continue
            key = (r["violation"], r["element"])
            v = _fnum(r["value"])
            old = vio[k].get(key)
            # keep the worst row per (kind, element)
            if old is None or _worse(r["violation"], r["unit"], v, old[0]) > 0:
                vio[k][key] = (v, r["unit"], r["note"])
    scen = defaultdict(set)
    for fn in sorted(os.listdir(d)):
        m = re.match(r"FAULT_VERDICTS_(.+)\.csv$", fn)
        if not m:
            continue
        proj = m.group(1)
        with _open(os.path.join(d, fn)) as fh:
            rd = csv.reader(fh)
            head = next(rd)
            for row in rd:
                for s, cell in zip(head[1:], row[1:]):
                    scen[proj].add(s)
                    cell = cell.strip()
                    if cell.startswith("PASS"):
                        verdict[(proj, s, row[0])] = "PASS"
                    elif cell.startswith("FAIL"):
                        verdict[(proj, s, row[0])] = "FAIL"
    return verdict, crit, vio, scen


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "violations_export")
    if not os.path.isfile(os.path.join(d, "ALL_VIOLATIONS.csv")):
        print("no ALL_VIOLATIONS.csv in %s -- run z7_vio_export.py first" % d)
        return 1
    verdict, crit, vio, scen = load(d)
    missing = defaultdict(list)
    mp = os.path.join(d, "MISSING.csv")
    if os.path.isfile(mp):
        with _open(mp) as fh:
            for r in csv.DictReader(fh):
                missing[(r["project"], r["scenario"])].append((r["missing"], r["fault_id"]))
    out = os.path.join(d, "compare")
    if not os.path.isdir(out):
        os.makedirs(out)
    frows, vrows, lines = [], [], []
    hdr = "%-14s %-30s %6s %6s %6s %6s %6s %6s %6s %6s %6s" % (
        "project", "pair", "both", "only1", "P->F", "F->P", "new", "cleared", "worse",
        "better", "crit+")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for proj in sorted(scen):
        for a, b in PAIRS:
            if a not in scen[proj] or b not in scen[proj]:
                continue
            fa = set(f for (p, s, f) in verdict if p == proj and s == a)
            fb = set(f for (p, s, f) in verdict if p == proj and s == b)
            both = sorted(fa & fb, key=_fkey)
            only = (fa ^ fb)
            n = {"pf": [], "fp": [], "new": 0, "clr": 0, "wor": 0, "bet": 0, "crit": 0}
            pair = "%s vs %s" % (a, b)
            for f in both:
                va, vb = verdict[(proj, a, f)], verdict[(proj, b, f)]
                ca, cb = crit[(proj, a, f)], crit[(proj, b, f)]
                add, clr = sorted(ca - cb), sorted(cb - ca)
                if vb == "PASS" and va == "FAIL":
                    n["pf"].append(f)
                elif vb == "FAIL" and va == "PASS":
                    n["fp"].append(f)
                n["crit"] += len(add)
                xa, xb = vio[(proj, a, f)], vio[(proj, b, f)]
                cnt = {"new": 0, "cleared": 0, "worse": 0, "better": 0, "same": 0}
                for key in sorted(set(xa) | set(xb)):
                    ra, rb = xa.get(key), xb.get(key)
                    if ra and not rb:
                        st = "new"
                    elif rb and not ra:
                        st = "cleared"
                    else:
                        w = _worse(key[0], ra[1], ra[0], rb[0])
                        st = "worse" if w > 0 else "better" if w < 0 else "same"
                    cnt[st] += 1
                    if st == "same":
                        continue
                    vrows.append([proj, pair, f, key[0], key[1], st,
                                  "" if not rb or rb[0] is None else rb[0],
                                  "" if not ra or ra[0] is None else ra[0],
                                  (ra or rb)[1],
                                  "" if not (ra and rb and ra[0] is not None and rb[0] is not None)
                                  else round(ra[0] - rb[0], 4),
                                  (ra or rb)[2]])
                n["new"] += cnt["new"]
                n["clr"] += cnt["cleared"]
                n["wor"] += cnt["worse"]
                n["bet"] += cnt["better"]
                frows.append([proj, pair, f, vb, va,
                              "PASS->FAIL" if (vb, va) == ("PASS", "FAIL") else
                              "FAIL->PASS" if (vb, va) == ("FAIL", "PASS") else "same",
                              "; ".join(add), "; ".join(clr),
                              cnt["new"], cnt["cleared"], cnt["worse"], cnt["better"]])
            for f in sorted(only, key=_fkey):
                frows.append([proj, pair, f, verdict.get((proj, b, f), "-"),
                              verdict.get((proj, a, f), "-"), "not comparable", "", "",
                              "", "", "", ""])
            lines.append("%-14s %-30s %6d %6d %6d %6d %6d %6d %6d %6d %6d" % (
                proj, pair, len(both), len(only), len(n["pf"]), len(n["fp"]),
                n["new"], n["clr"], n["wor"], n["bet"], n["crit"]))
            if n["pf"]:
                lines.append("%-14s %-30s   PASS->FAIL: %s" % ("", "", _ranges(n["pf"])))
            if n["fp"]:
                lines.append("%-14s %-30s   FAIL->PASS: %s" % ("", "", _ranges(n["fp"])))
    lines += ["",
              "both    = faults scored in both scenarios (only these are compared)",
              "only1   = faults scored in just one of the two -- not comparable",
              "P->F    = PASS in the 2nd scenario, FAIL in the 1st (F->P the reverse)",
              "new/cleared/worse/better = violations by (kind, element), 'review' left out",
              "crit+   = failed criteria that appear only in the 1st scenario", ""]
    gaps = [(k, v) for k, v in sorted(missing.items()) if v]
    if gaps:
        lines.append("GAPS (from MISSING.csv) -- rerun these for a full comparison:")
        for (p, s), v in gaps:
            by = defaultdict(list)
            for what, f in v:
                by[what].append(f)
            for what in sorted(by):
                lines.append("  %-14s %-16s %-24s %s" % (p, s, "%s (%d)" % (what, len(by[what])),
                                                        _ranges(by[what])))
    with open(os.path.join(out, "COMPARE_FAULTS.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["project", "pair", "fault_id", "verdict_2nd", "verdict_1st", "change",
                    "criteria_failed_only_in_1st", "criteria_failed_only_in_2nd",
                    "vio_new", "vio_cleared", "vio_worse", "vio_better"])
        w.writerows(frows)
    with open(os.path.join(out, "COMPARE_VIOLATIONS.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["project", "pair", "fault_id", "violation", "element", "change",
                    "value_2nd", "value_1st", "unit", "delta", "note"])
        w.writerows(vrows)
    with open(os.path.join(out, "COMPARE_SUMMARY.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print("written to %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
