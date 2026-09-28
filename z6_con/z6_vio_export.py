"""EVERY VIOLATION OF EVERY SCENARIO, IN A FEW SMALL FILES.

Reads what is already on disk -- nothing is simulated, rescored or changed --
and writes, for every results folder under Base\\results_base and
Projects\\results_proj (base, base EGF off, project GIA, surplus s1_egfoff,
project EGF off, sweeps, ...):

    violations_export\\ALL_VIOLATIONS.csv        every violation row, one scenario column
    violations_export\\ALL_FAILED_CRITERIA.csv   every criterion that FAILED, with its detail
    violations_export\\FAULT_VERDICTS_<proj>.csv  one row per fault, one column per scenario:
                                                 PASS / FAIL (n violations) / - (not run)
    violations_export\\MISSING.csv               every gap, one row per fault and scenario (see below)
    violations_export\\SUMMARY.txt               per scenario: faults, pass, fail, violations, source,
                                                 and WHAT IS MISSING, as fault ranges
    violations_export.zip                        all of the above, to hand over in one file

BACKUP FOLDERS ARE LEFT OUT: any folder (or folder above it) whose name has
backup / bak / old / prev / previous / archive / copy / before as a word, or
ends __run<n> (a previous run set aside), _prev_<time> or _before_fixed_<time>.
Each one skipped is listed on screen and in SUMMARY.txt. Add more words to
SKIP_WORDS below.

WHAT IS MISSING, per scenario (checked against that folder's own fault list
faults\\SPP_FAULTS.csv and every other scenario of the same project):
    not run            in a fault list, no .out
    not finished       .out but no .done (crashed, gave up, or still running)
    not scored         .out but no score anywhere (merged report or parts)
    not in merged report   scored in parts\\SCEN_<id>.csv only (merge unfinished)
    no PDF             .out but no plots\\<id>_plots.pdf
and whether the folder has no 02_VIOLATIONS / SPP_CRITERIA_REPORT file at all.

Where a folder's merged report is incomplete (a merge that did not finish),
the faults it lacks are read from parts\\SCEN_<id>.csv -- the worker's own
score -- exactly as the comparison does; the "source" column says which.

Run it with the same Python as the study (standard library only):
    C:\\Python34\\python.exe z6_vio_export.py                 (folder = this script's)
    C:\\Python34\\python.exe z6_vio_export.py C:\\KV\\ENGIE\\CQ_MIT
Optional second argument: only these projects, comma separated
    C:\\Python34\\python.exe z6_vio_export.py C:\\KV\\ENGIE\\CQ_MIT IronStar,SantaFe
"""
import os, sys, csv, glob, re, zipfile

try:
    csv.field_size_limit(min(sys.maxsize, 2 ** 31 - 1))
except Exception:
    pass

ROOT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] \
    else os.path.dirname(os.path.abspath(__file__))
ONLY = [p.strip() for p in (sys.argv[2] if len(sys.argv) > 2 else "").split(",") if p.strip()]
OUT = os.path.join(ROOT, "violations_export")

SKIP_WORDS = ("backup", "backups", "bak", "old", "prev", "previous", "archive",
              "archived", "copy", "before")
SKIPPED = []


def _is_backup(name):
    """EastFork_spp__run2, IronStar_spp_prev_0927_1015, backup, 'X - Copy' ..."""
    if re.search(r"__run\d+$", name):
        return True
    return any(w in SKIP_WORDS for w in re.split(r"[^a-z0-9]+", name.lower()))


VIO_COLS = ["fault_id", "fault_result", "violation", "element", "value", "unit",
            "time_s", "area", "area_name", "nodes_from_fault", "fault_bus", "note",
            "above_limit_s"]


def _ranges(ids):
    """F09, F10, F11, F22 -> 'F09-F11, F22'."""
    ids = sorted(set(ids), key=_fid_key)
    out, run = [], []
    for f in ids:
        k = _fid_key(f)
        if run and not k[2] and not _fid_key(run[-1])[2] and k[0] == _fid_key(run[-1])[0] \
                and k[1] == _fid_key(run[-1])[1] + 1:
            run.append(f)
            continue
        if run:
            out.append(run[0] if len(run) == 1 else "%s-%s" % (run[0], run[-1]))
        run = [f]
    if run:
        out.append(run[0] if len(run) == 1 else "%s-%s" % (run[0], run[-1]))
    return ", ".join(out)


def _stems(d, pat, suffix):
    return set(os.path.basename(p)[:-len(suffix)] for p in glob.glob(os.path.join(d, pat)))


def _planned(d):
    p = os.path.join(d, "faults", "SPP_FAULTS.csv")
    ids = set()
    if os.path.isfile(p):
        try:
            with _open_r(p) as fh:
                for r in csv.DictReader(fh):
                    f = (r.get("fault_id") or "").strip()
                    if f:
                        ids.add(f)
        except Exception as e:
            print("  could not read %s (%s)" % (p, e))
    return ids


def _open_r(p):
    return open(p, "r", newline="", encoding="utf-8", errors="replace")


def _open_w(p):
    return open(p, "w", newline="", encoding="utf-8")


def _fid_key(f):
    m = re.match(r"^([A-Za-z_]*?)(\d+)(.*)$", f or "")
    return (m.group(1), int(m.group(2)), m.group(3)) if m else (f, 0, "")


def run_folders():
    """(case, project, scenario label, folder) for every results folder."""
    found = []
    for case, sub in (("BASE", os.path.join("Base", "results_base")),
                      ("PROJ", os.path.join("Projects", "results_proj"))):
        top = os.path.join(ROOT, sub)
        if not os.path.isdir(top):
            continue
        for d, dirs, files in os.walk(top):
            dirs.sort()
            if os.path.relpath(d, top) != "." and _is_backup(os.path.basename(d)):
                SKIPPED.append(d)
                dirs[:] = []
                continue
            has_vio = any(f.upper().startswith("02_VIOLATIONS_") and f.lower().endswith(".csv")
                          for f in files)
            if not (has_vio or os.path.isdir(os.path.join(d, "parts"))
                    or os.path.isdir(os.path.join(d, "reports"))):
                continue
            rel = os.path.relpath(d, top)
            proj = rel.split(os.sep)[0]
            if ONLY and proj not in ONLY:
                dirs[:] = []
                continue
            name = os.path.basename(d)
            # EastFork_spp -> BASE / GIA; EastFork_spp_s1_egfoff -> BASE_s1_egfoff / GIA_s1_egfoff
            m = re.match(r"^%s_[A-Za-z0-9]+(?:_(.*))?$" % re.escape(proj), name)
            rest = (m.group(1) or "") if m else name
            label = ("BASE" if case == "BASE" else "GIA") + ("_" + rest if rest else "")
            if os.path.dirname(rel) not in ("", proj):
                label += "  [" + os.path.dirname(rel) + "]"
            found.append((case, proj, label, d))
            dirs[:] = [x for x in dirs if x not in ("parts", "reports", "outs", "plots",
                                                    "logs", "flags", "faults")]
    return found


def _pick(d, pattern):
    """The merged file, not its _SELECTED copy."""
    c = [p for p in glob.glob(os.path.join(d, pattern)) if "_SELECTED" not in os.path.basename(p).upper()]
    return sorted(c)[0] if c else None


def read_parts(d):
    """{fault: (verdict, [(criterion, result, detail)], [(kind, element, value)])}
       from parts\\SCEN_<id>.csv, skipping a part older than its re-run .out."""
    out = {}
    for p in glob.glob(os.path.join(d, "parts", "SCEN_*.csv")):
        nm = os.path.basename(p)
        if nm.upper().endswith("_MEAS.CSV") or ".tmp" in nm:
            continue
        sid = nm[len("SCEN_"):-len(".csv")]
        try:
            op = os.path.join(d, "outs", sid + ".out")
            if os.path.isfile(op) and os.path.getmtime(p) + 2.0 < os.path.getmtime(op):
                continue
        except Exception:
            pass
        verdict, rows, vio, case = None, [], [], None
        try:
            with _open_r(p) as fh:
                rd = csv.reader(fh)
                next(rd, None)
                for r in rd:
                    if len(r) < 3:
                        continue
                    kind = (r[0] or "").strip()
                    case = case or (r[1] or "").strip() or sid
                    if kind == "verdict":
                        verdict = (r[2] or "").strip().upper()
                    elif kind == "crit":
                        rows.append(((r[2] or "").strip(),
                                     (r[3] or "").strip().upper() if len(r) > 3 else "",
                                     (r[4] or "").strip() if len(r) > 4 else ""))
                    elif kind.startswith("vio:"):
                        vio.append((kind[4:], (r[2] or "").strip(),
                                    (r[3] or "").strip() if len(r) > 3 else ""))
        except Exception as e:
            print("  could not read %s (%s)" % (p, e))
            continue
        if not rows:
            continue
        if verdict not in ("PASS", "FAIL"):
            verdict = "PASS" if all(x[1] != "FAIL" for x in rows) else "FAIL"
        out[case or sid] = (verdict, rows, vio)
    return out


def main():
    folders = run_folders()
    if not folders:
        print("no results folders under %s\\Base\\results_base or %s\\Projects\\results_proj"
              % (ROOT, ROOT))
        return 2
    if not os.path.isdir(OUT):
        os.makedirs(OUT)
    fv = _open_w(os.path.join(OUT, "ALL_VIOLATIONS.csv"))
    wv = csv.writer(fv)
    wv.writerow(["project", "scenario", "case", "source"] + VIO_COLS + ["folder"])
    fc = _open_w(os.path.join(OUT, "ALL_FAILED_CRITERIA.csv"))
    wc = csv.writer(fc)
    wc.writerow(["project", "scenario", "case", "source", "fault_id", "criterion", "result",
                 "detail", "folder"])
    verdicts = {}          # proj -> {scenario: {fault: (verdict, n_vio)}}
    summary = []
    status = []            # (proj, label, d, planned, outs, done, scored, parts-only, pdfs, has_vp, has_cp)
    for case, proj, label, d in folders:
        print("%-14s %-28s %s" % (proj, label, d))
        crit = {}          # fault -> (verdict, [(criterion, result, detail)])
        cp = _pick(os.path.join(d, "reports"), "SPP_CRITERIA_REPORT_*.csv")
        if cp:
            try:
                with _open_r(cp) as fh:
                    for r in csv.DictReader(fh):
                        f = (r.get("Case") or "").strip()
                        if f:
                            crit.setdefault(f, ["", []])[1].append(
                                ((r.get("Criterion") or "").strip(),
                                 (r.get("Result") or "").strip().upper(),
                                 (r.get("Detail") or "").strip()))
            except Exception as e:
                print("  could not read %s (%s) -- using the parts" % (cp, e))
                crit = {}
        for f in crit:
            crit[f][0] = "PASS" if all(x[1] != "FAIL" for x in crit[f][1]) else "FAIL"
        parts = read_parts(d)
        from_parts = [f for f in parts if f not in crit]
        for f in from_parts:
            crit[f] = [parts[f][0], parts[f][1]]
        src_c = (os.path.basename(cp) if cp and len(crit) > len(from_parts) else "")
        # violations: the merged 02_VIOLATIONS file, then the parts for faults it lacks
        nvio = {}
        vp = _pick(d, "02_VIOLATIONS_*.csv")
        vio_faults = set()
        if vp:
            try:
                with _open_r(vp) as fh:
                    for r in csv.DictReader(fh):
                        f = (r.get("fault_id") or "").strip()
                        vio_faults.add(f)
                        nvio[f] = nvio.get(f, 0) + 1
                        wv.writerow([proj, label, case, os.path.basename(vp)]
                                    + [r.get(k, "") for k in VIO_COLS] + [d])
            except Exception as e:
                print("  could not read %s (%s)" % (vp, e))
        n_pv = 0
        for f in sorted(parts, key=_fid_key):
            if f in vio_faults or (vp and f not in from_parts):
                continue
            for kind, el, val in parts[f][2]:
                nvio[f] = nvio.get(f, 0) + 1
                n_pv += 1
                row = dict((k, "") for k in VIO_COLS)
                row.update({"fault_id": f, "fault_result": parts[f][0], "violation": kind,
                            "element": el, "value": val})
                wv.writerow([proj, label, case, "parts\\SCEN_%s.csv" % f]
                            + [row[k] for k in VIO_COLS] + [d])
        for f in sorted(crit, key=_fid_key):
            for c, res, det in crit[f][1]:
                if res == "FAIL":
                    wc.writerow([proj, label, case,
                                 "parts\\SCEN_%s.csv" % f if f in from_parts else src_c,
                                 f, c, res, det, d])
        vt = verdicts.setdefault(proj, {}).setdefault(label, {})
        for f in crit:
            vt[f] = (crit[f][0], nvio.get(f, 0))
        status.append((proj, label, d, _planned(d),
                       _stems(os.path.join(d, "outs"), "*.out", ".out"),
                       _stems(os.path.join(d, "outs"), "*.done", ".done"),
                       set(crit), set(from_parts),
                       _stems(os.path.join(d, "plots"), "*_plots.pdf", "_plots.pdf"),
                       bool(vp), bool(cp)))
        nf = sum(1 for f in crit if crit[f][0] == "FAIL")
        summary.append((proj, label, case, len(crit), len(crit) - nf, nf,
                        sum(nvio.values()), len(from_parts), n_pv, d))
    fv.close()
    fc.close()
    written = ["ALL_VIOLATIONS.csv", "ALL_FAILED_CRITERIA.csv", "MISSING.csv"]
    # ---- WHAT IS MISSING ----------------------------------------------------
    union = {}
    for st in status:
        union.setdefault(st[0], set()).update(st[3])
    gaps = []              # (proj, label, kind, [faults], d)
    for proj, label, d, planned, outs, done, scored, pponly, pdfs, has_vp, has_cp in status:
        want = (planned | union.get(proj, set())) - set(["FLAT_RUN"])
        g = [("not run", sorted(want - outs, key=_fid_key)),
             ("not finished", sorted(outs - done, key=_fid_key)),
             ("not scored", sorted(outs - scored, key=_fid_key)),
             ("not in merged report", sorted(pponly, key=_fid_key)),
             ("no PDF", sorted((outs & done) - pdfs - set(["FLAT_RUN"]), key=_fid_key))]
        for kind, ids in g:
            if ids:
                gaps.append((proj, label, kind, ids, d))
        if not has_vp:
            gaps.append((proj, label, "no 02_VIOLATIONS file", [], d))
        if not has_cp:
            gaps.append((proj, label, "no SPP_CRITERIA_REPORT file", [], d))
    with _open_w(os.path.join(OUT, "MISSING.csv")) as fh:
        w = csv.writer(fh)
        w.writerow(["project", "scenario", "missing", "fault_id", "folder"])
        for proj, label, kind, ids, d in gaps:
            for f in (ids or [""]):
                w.writerow([proj, label, kind, f, d])
    for proj in sorted(verdicts):
        scen = sorted(verdicts[proj], key=lambda s: (not s.startswith("BASE"), s))
        faults = sorted(set(f for s in scen for f in verdicts[proj][s]), key=_fid_key)
        nm = "FAULT_VERDICTS_%s.csv" % re.sub(r"[^A-Za-z0-9_.-]+", "_", proj)
        with _open_w(os.path.join(OUT, nm)) as fh:
            w = csv.writer(fh)
            w.writerow(["fault_id"] + scen)
            for f in faults:
                cells = []
                for s in scen:
                    v = verdicts[proj][s].get(f)
                    cells.append("-" if v is None else
                                 v[0] + (" (%d)" % v[1] if v[1] else ""))
                w.writerow([f] + cells)
        written.append(nm)
    with open(os.path.join(OUT, "SUMMARY.txt"), "w") as fh:
        fh.write("Violations export from %s\n\n" % ROOT)
        fh.write("%-14s %-30s %-5s %6s %6s %6s %10s %12s\n" % (
            "project", "scenario", "case", "faults", "pass", "fail", "violations",
            "from parts"))
        for s in summary:
            fh.write("%-14s %-30s %-5s %6d %6d %6d %10d %12s\n" % (
                s[0], s[1], s[2], s[3], s[4], s[5], s[6],
                ("%d fault(s)" % s[7]) if s[7] else "-"))
        fh.write("\nWHAT IS MISSING (details in MISSING.csv):\n")
        if not gaps:
            fh.write("  nothing -- every fault in every scenario is run, finished, scored,\n"
                     "  merged and plotted\n")
        for proj, label, kind, ids, d in gaps:
            fh.write("  %-14s %-28s %-24s %s\n" % (
                proj, label, kind + (" (%d)" % len(ids) if ids else ""), _ranges(ids)))
        fh.write("\n'from parts' = faults missing from that folder's merged report, read\n"
                 "from parts\\SCEN_<id>.csv (the worker's own score).\n\nFolders:\n")
        for s in summary:
            fh.write("  %-14s %-30s %s\n" % (s[0], s[1], s[9]))
        fh.write("\nBackup folders left out (%d):\n" % len(SKIPPED))
        for d in SKIPPED:
            fh.write("  %s\n" % d)
    written.append("SUMMARY.txt")
    zp = OUT + ".zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
        for nm in written:
            z.write(os.path.join(OUT, nm), os.path.join("violations_export", nm))
    print("")
    with open(os.path.join(OUT, "SUMMARY.txt")) as fh:
        print(fh.read())
    print("-> %s" % OUT)
    print("-> %s   (send this one)" % zp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
