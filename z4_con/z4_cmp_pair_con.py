# -*- coding: utf-8 -*-
"""
z4_cmp_pair_con.py -- compare ANY two results folders and write the full
comparison report: the same 00_COMPARISON_REPORT_*.xlsx / .txt / .csv,
COMPARISON_SPP_TABLE and run-time comparison the panel writes, built by the
panel's own readers and writers, so the layout and the content are identical.

WHAT IT IS FOR
  The impact of a project under every scenario, each against the base:
      Base\results_base\SantaFe_spp            vs  Projects\results_proj\SantaFe_spp           (as studied)
      Base\results_base\SantaFe_spp            vs  Projects\results_proj\SantaFe_spp_poi502    (GIA capacity)
      Base\results_base\SantaFe_spp            vs  Projects\results_proj\SantaFe_spp_cap50     (50 % output)
      Base\results_base\SantaFe_spp            vs  Projects\results_proj\SantaFe_spp_dyr_Kqv2  (.dyr edit)
  or one project run against another (the reference need not be the base):
      Projects\results_proj\SantaFe_spp        vs  Projects\results_proj\SantaFe_spp_poi502
  Every classification in the report reads relative to the REFERENCE folder:
  NEW means "the test folder violates and the reference does not".

HOW TO USE
  1. Put this file beside z4_cmp_all_con.py (the panel), in C:\KV.
  2. Either fill in REFERENCE + SCENARIOS (or PAIRS) below and run it, or run
     it with nothing filled in and pick the two folders when asked, or give
     them on the command line:
         python z4_cmp_pair_con.py
         python z4_cmp_pair_con.py <reference folder> <test folder> [label]
  3. The reports go to OUT_DIR\<label>\  (OUT_DIR defaults to
     C:\KV\comparison_pairs, i.e. <panel folder>\comparison_pairs). One
     folder per pair, e.g.
         comparison_pairs\SantaFe_studied_vs_base\00_COMPARISON_REPORT_SantaFe_studied_vs_base.xlsx
         comparison_pairs\SantaFe_Sep21_full_gia_studied_vs_base\00_COMPARISON_REPORT_SantaFe_Sep21_full_gia_studied_vs_base.xlsx
     The label is <project>_<test tag>_vs_<reference tag>; a test folder that
     sits in a folder of its own (Sep21_full gia) carries that folder's name,
     and a label that repeats gets _2, _3. Set OUT_DIR to put them elsewhere,
     or give a label of your own as the third item of a PAIRS entry.

  Nothing is simulated and nothing in the results folders is changed, except
  that a folder whose reports are older than its parts\ is re-merged first
  (REMERGE_STALE), exactly as the panel does before it compares.

Python 3.4, standard library only.
"""
import os
import re
import sys
import glob
import time
import types
import subprocess

# ============================================================================
#  SETTINGS
# ============================================================================
REFERENCE = r"C:\KV\Base\results_base\SantaFe_spp"          # the folder every scenario is compared AGAINST, e.g. r"C:\KV\Base\results_base\SantaFe_spp"
SCENARIOS = [    r"C:\KV\Projects\results_proj\SantaFe_spp" ,
                 r"C:\KV\Projects\results_proj\Sep21_full gia\SantaFe_spp",       # the folders to compare against it, one report each, e.g.
    # r"C:\KV\Projects\results_proj\SantaFe_spp",
    # r"C:\KV\Projects\results_proj\SantaFe_spp_poi502",
]
PAIRS = [                # explicit pairs when the reference differs per pair:
    # (r"C:\KV\Base\results_base\IronStar_spp", r"C:\KV\Projects\results_proj\IronStar_spp_poi214", "IronStar_GIA"),
    # (r"C:\KV\Projects\results_proj\SantaFe_spp", r"C:\KV\Projects\results_proj\SantaFe_spp_poi502", "SantaFe_GIA_vs_studied"),
]
OUT_DIR = r""            # "" = <panel folder>\comparison_pairs
SIDE_BY_SIDE = True      # also one sheet with every scenario that shares a reference side by side: reference | as studied | GIA | ... per fault
REMERGE_STALE = True     # rebuild a folder's reports from parts\ when the parts are newer (same as the panel)
ASK_IF_EMPTY = True      # nothing above and nothing on the command line -> folder pickers
PANEL = "z4_cmp_all_con.py"
# ============================================================================


def _here():
    try:
        return os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
    except NameError:
        return os.getcwd()


def _load_panel():
    """The panel as a module, with PIPELINE forced to "compare" so its
       import-time checks (missing decks stop a simulating run) let a
       read-only comparison through. Nothing in the panel file is changed."""
    path = os.path.join(_here(), PANEL)
    if not os.path.isfile(path):
        raise SystemExit("%s must sit beside this script (looked in %s)" % (PANEL, _here()))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    src, n = re.subn(r'^(PIPELINE\s*=\s*)"[^"]*"', r'\1"compare"', src, count=1, flags=re.M)
    if not n:
        print("[pair] could not find the PIPELINE line in %s -- loading it as it is" % PANEL)
    mod = types.ModuleType("z4_cmp_all_con")
    mod.__file__ = path
    sys.modules["z4_cmp_all_con"] = mod
    code = compile(src, path, "exec")
    exec(code, mod.__dict__)
    return mod


def _norm(p):
    return os.path.normpath(os.path.abspath(str(p).strip().strip('"')))


def _base(p):
    """The last path component, whichever slash the path was typed with."""
    parts = [x for x in re.split(r"[\\/]+", str(p).strip().strip('"')) if x]
    return parts[-1] if parts else ""


def _parts(p):
    return [x.lower() for x in re.split(r"[\\/]+", str(p).strip().strip('"')) if x]


def _split_name(folder, modes):
    """('SantaFe', 'spp', '_poi502') from ...\\SantaFe_spp_poi502."""
    name = _base(folder)
    for m in list(modes or []) + ["spp", "con", "table", "custom", "manual"]:
        key = "_%s" % m
        i = name.find(key)
        while i >= 0:
            rest = name[i + len(key):]
            if rest == "" or rest.startswith("_"):
                return name[:i], m, rest
            i = name.find(key, i + 1)
    return name, (modes or ["spp"])[0], ""


def _case_root(folder):
    """(case folder holding the study script, 'b' | 'p') found by walking up
       from the results folder, or (None, None)."""
    d = _norm(folder)
    for _ in range(5):
        d = os.path.dirname(d)
        if not d or d == os.path.dirname(d):
            break
        for kind in ("p", "b"):
            if os.path.isfile(os.path.join(d, "z4_spp_%s_con.py" % kind)):
                return d, kind
    return None, None


def _make_case(key, folder):
    root, kind = _case_root(folder)
    return {"key": key,
            "dir": root or os.path.dirname(os.path.dirname(_norm(folder))),
            "script": ("z4_lch_%s_con.py" % kind) if kind else "",
            "label": _norm(folder),
            "_kind": kind}


def _tags_of(suffix):
    """SPP_CAP_TAG / SPP_RUN_TAG the study used for this folder, from its
       name: '_cap50_dyr_Kqv2' -> ('50', 'dyr_Kqv2'); '_poi502' -> ('', 'poi502')."""
    s = (suffix or "").strip("_")
    cap = ""
    m = re.match(r"^cap(\d+)(?:_(.*))?$", s)
    if m:
        cap, s = m.group(1), (m.group(2) or "")
    return cap, s


def _merge_folder(z4, case, rdir, suffix):
    """Rebuild one folder's reports from its parts\\ -- the panel's own merge
       step, with the folder's run tags so the study script finds THIS folder
       and not the plain one beside it."""
    parts = os.path.join(rdir, "parts")
    if not os.path.isdir(parts) or not glob.glob(os.path.join(parts, "*.csv")):
        return False
    # THE SETTINGS FOLLOW THE FOLDER'S OWN CASE, not its role in this pair: a
    # project folder used as the reference is still a project folder, and its
    # merge needs the project deck names and the project study script.
    mcase = dict(case)
    mcase["key"] = "PROJ" if case.get("_kind") == "p" else "BASE"
    mcase["script"] = "z4_lch_%s_con.py" % ("p" if case.get("_kind") == "p" else "b")
    spp = z4._study_script_for(mcase)
    if not spp:
        print("[pair]     no study script beside %s -- cannot re-merge, comparing "
              "the reports as they are" % rdir)
        return False
    env = dict(os.environ)
    try:
        z4._push_settings(env, mcase)
    except Exception as e:
        print("[pair]     could not pass the run settings to the merge (%s)" % e)
    env["SPP_MERGE_ONLY"] = "1"
    env["SPP_STUDY_DIR"] = mcase["dir"]
    for k in ("SPP_ONLY", "SPP_ONLY_FAULTS", "SPP_REPORT_FAULTS", "SPP_ONLY_EVENTS",
              "SPP_CAP_TAG", "SPP_RUN_TAG"):
        env.pop(k, None)
    proj, mode, _sfx = _split_name(rdir, z4.MODES)
    env["SPP_PROJECT"] = proj
    env["SPP_RUN_PROJECTS"] = proj
    env["SPP_FAULT_MODE"] = mode
    cap, run = _tags_of(suffix)
    if cap:
        env["SPP_CAP_TAG"] = cap
    if run:
        env["SPP_RUN_TAG"] = run
    print("[pair]     re-merging %s from its parts\\ ..." % rdir)
    try:
        rc = subprocess.call([sys.executable, os.path.abspath(spp)], cwd=rdir, env=env)
    except Exception as e:
        print("[pair]     could not start the merge (%s)" % e)
        return False
    if rc != 0:
        print("[pair]     the merge returned rc=%s -- its own output says why" % rc)
    return rc == 0


def _prepare(z4, case, folder, suffix):
    """Say what the folder holds; re-merge it first if its parts are newer
       than its reports."""
    proj, mode, _s = _split_name(folder, z4.MODES)
    try:
        outs, scored = z4._out_and_scored_sets(folder, proj)
    except Exception:
        outs, scored = set(), set()
    behind = 0.0
    try:
        behind = z4._report_behind_parts_by(folder, proj)
    except Exception:
        pass
    print("[pair]   %-5s %s" % (case["key"], folder))
    print("[pair]         %d .out file(s), %d with a verdict%s"
          % (len(outs), len(scored),
             ("  -- reports are %.0f s older than the parts" % behind)
             if behind > z4.STALE_REPORT_TOL_S else ""))
    if REMERGE_STALE and behind > z4.STALE_REPORT_TOL_S:
        if _merge_folder(z4, case, folder, suffix):
            try:
                outs, scored = z4._out_and_scored_sets(folder, proj)
                print("[pair]         after the merge: %d with a verdict" % len(scored))
            except Exception:
                pass


_STD_PARENTS = ("results_base", "results_proj", "results", "base", "projects", "kv")


def _folder_tag(folder):
    """What tells this folder apart: its own suffix ('poi502', 'cap50'), and
       the folder it sits in when that is not one of the usual results roots
       -- so ...\Sep21_full gia\SantaFe_spp is 'Sep21_full_gia' and not
       'studied', which is what ...\results_proj\SantaFe_spp is called."""
    _p, _m, sfx = _split_name(folder, None)
    bits = []
    parts = [x for x in re.split(r"[\\/]+", str(folder).strip().strip('"')) if x]
    parent = parts[-2] if len(parts) >= 2 else ""
    if parent and parent.lower() not in _STD_PARENTS and not re.match(r"^[A-Za-z]:$", parent):
        bits.append(parent)
    bits.append(sfx.strip("_") if sfx else "studied")
    return "_".join(bits)


def _label_for(ref, test):
    proj, _m, _s = _split_name(test, None)
    rk = "base" if "results_base" in _parts(ref) else _folder_tag(ref)
    tk = _folder_tag(test)
    lab = "%s_%s_vs_%s" % (proj, tk, rk)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", lab)


def compare_pair(z4, ref, test, label=None):
    ref, test = _norm(ref), _norm(test)
    for f in (ref, test):
        if not os.path.isdir(f):
            print("[pair] *** not a folder: %s ***" % f)
            return None
    if ref == test:
        print("[pair] *** the two folders are the same: %s ***" % ref)
        return None
    proj_t, mode, sfx_t = _split_name(test, z4.MODES)
    proj_r, mode_r, sfx_r = _split_name(ref, z4.MODES)
    if proj_r != proj_t:
        print("[pair] *** the folders belong to different projects (%s vs %s) -- "
              "the fault ids would not mean the same event. Skipped. ***" % (proj_r, proj_t))
        return None
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label or _label_for(ref, test))
    out_root = OUT_DIR or os.path.join(z4.STUDY_ROOT, "comparison_pairs")
    case_b = _make_case("BASE", ref)
    case_t = _make_case("PROJ", test)
    case_b["label"] = "REFERENCE  %s" % ref
    case_t["label"] = "TEST       %s" % test

    print("")
    print("=" * 100)
    print(" %s" % label)
    print("=" * 100)
    _prepare(z4, case_b, ref, sfx_r)
    _prepare(z4, case_t, test, sfx_t)

    # ---- point the panel at these two folders and nothing else ----------
    z4.CASE_BASE = case_b
    z4.CASE_TEST = case_t
    z4.PROJECTS = [proj_t]
    z4.PROJECTS_RUN = "each"
    z4.MODES = [mode]
    z4.ONLY_FAULTS = []
    z4.ONLY_EVENTS = []
    if hasattr(z4, "ONLY_IDS"):
        z4.ONLY_IDS = []
    if hasattr(z4, "ONLY_WORDS"):
        z4.ONLY_WORDS = []
    z4.COMPARE_BY_PROJECT = False
    z4.COMPARE_DIR = out_root
    z4.results_dir = lambda case, p, m: ref if str(case.get("key")) == "BASE" else test
    z4._res_root = lambda case: os.path.dirname(ref if str(case.get("key")) == "BASE" else test)
    z4.discover_projects = lambda m: ([proj_t], [], [])
    for name in ("_MEAS_CACHE", "_WANT_BUSES", "_WHERE"):
        d = getattr(z4, name, None)
        if isinstance(d, dict):
            d.clear()
    z4._RUN_OUTPUT[0] = label

    t0 = time.time()
    with z4._cmp_into(label):
        results, only_b, only_t = z4.compare_now(quiet=False)
        xl = z4.cmp_path("COMPARISON_REPORT", "xlsx")
        folder = os.path.dirname(xl)
    z4._RUN_OUTPUT[0] = ""
    if not results:
        print("[pair] *** nothing comparable: no fault has a verdict on both sides ***")
        return None
    res = results[0]
    rows = res.get("rows") or []
    n_new = sum(1 for r in rows if r.get("class") == z4.CLS_NEW or r.get("hidden_new"))
    n_pre = sum(1 for r in rows if r.get("class") == z4.CLS_PRE and not r.get("hidden_new"))
    print("[pair] %d fault(s) compared: %d NEW with the test folder, %d pre-existing "
          "-- %.0f s" % (len(rows), n_new, n_pre, time.time() - t0))
    print("[pair] -> %s" % folder)
    try:
        summ = z4._summary_rows([res])
    except Exception:
        summ = []
    return {"label": label, "ref": ref, "test": test, "folder": folder, "proj": proj_t,
            "faults": len(rows), "new": n_new, "pre": n_pre, "summary": summ,
            "tag": _folder_tag(test)}


_SBS_PER = [("verdict", "verdict_projects"), ("class", "classification"),
            ("worst_criterion", "worst_criterion"), ("ref_value", "base_value"),
            ("value", "project_value"), ("limit", "limit"), ("unit", "unit"),
            ("past_limit", "past_limit"), ("new_criteria", "new_criteria"),
            ("violating_buses", "violating_buses"), ("cause", "cause")]


def write_side_by_side(z4, ref, group):
    """ONE SHEET, EVERY SCENARIO. One row per fault: the reference's verdict,
       then for each scenario compared against that reference its verdict,
       classification, worst criterion, both values, limit and the buses --
       base | as studied | GIA | ... read across, instead of one workbook each.
       Built from the same summary rows the pair workbooks hold, so the two
       never disagree."""
    group = [g for g in group if g.get("summary")]
    if len(group) < 2:
        return None
    SC = dict((c, i) for i, c in enumerate(z4._SUMMARY_COLS))
    tags = []
    for g in group:
        t = g["tag"]
        n = 2
        while t in tags:
            t = "%s_%d" % (g["tag"], n)
            n += 1
        tags.append(t)
    per = {}                                   # fault -> {tag: summary row}
    order, head = [], {}
    for g, t in zip(group, tags):
        for r in g["summary"]:
            fid = str(r[SC["fault"]]).strip()
            if fid not in per:
                per[fid] = {}
                order.append(fid)
                head[fid] = r
            per[fid][t] = r
    proj = group[0]["proj"]
    rk = "base" if "results_base" in _parts(ref) else _folder_tag(ref)
    header = ["fault", "planning_event", "fault_source", "project", "reference", "verdict_" + rk]
    widths = [8, 12, 9, 12, 22, 12]
    for t in tags:
        for nm, _c in _SBS_PER:
            header.append("%s | %s" % (t, nm))
            widths.append({"verdict": 11, "class": 14, "worst_criterion": 24, "ref_value": 10,
                           "value": 10, "limit": 8, "unit": 6, "past_limit": 10,
                           "new_criteria": 18, "violating_buses": 40, "cause": 50}[nm])
    header += ["worst_across_scenarios", "description"]
    widths += [22, 60]
    rows = []
    _rank = {z4.CLS_NEW: 3}
    for fid in sorted(order, key=lambda x: (len(x), x)):
        h = head[fid]
        row = [fid, h[SC["planning_event"]], h[SC["fault_source"]], proj, _base(ref),
               h[SC["verdict_base"]]]
        worst, act, pre, allpass = "", False, False, True
        for t in tags:
            r = per[fid].get(t)
            if r is None:
                row += ["not compared"] + [""] * (len(_SBS_PER) - 1)
                allpass = False
                continue
            for _nm, c in _SBS_PER:
                row.append(r[SC[c]])
            cls = str(r[SC["classification"]])
            if str(r[SC["action"]]).startswith("ACT"):
                act = True
                worst = "%s: %s" % (t, cls)
            elif cls == z4.CLS_PRE:
                pre = True
                if not act:
                    worst = "%s: %s" % (t, cls)
            if str(r[SC["verdict_projects"]]).upper() != "PASS":
                allpass = False
        row += [worst or ("all PASS" if allpass else ""), h[SC["description"]]]
        rows.append(row)

    def _style(row):
        w = str(row[len(header) - 2])
        if w.endswith(": " + z4.CLS_NEW):
            return 2                            # a scenario introduced it
        if w.endswith(": " + z4.CLS_PRE):
            return 3                            # fails with and without
        if w == "all PASS":
            return 5
        return 0

    out_root = OUT_DIR or os.path.join(z4.STUDY_ROOT, "comparison_pairs")
    lab = re.sub(r"[^A-Za-z0-9_.-]+", "_", "%s_SIDE_BY_SIDE_vs_%s" % (proj, rk))
    d = os.path.join(out_root, lab)
    if not os.path.isdir(d):
        os.makedirs(d)
    xp = os.path.join(d, "00_SIDE_BY_SIDE_%s.xlsx" % lab)
    title = ["%s -- every scenario against %s, one row per fault" % (proj, ref)]
    for g, t in zip(group, tags):
        title.append("%s = %s" % (t, g["test"]))
    z4.write_xlsx(xp, header, rows, widths, _style, legend=z4._XL_LEGEND, title_rows=title)
    cp = os.path.join(d, "00_SIDE_BY_SIDE_%s.csv" % lab)
    try:
        import csv
        with open(cp, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for r in rows:
                w.writerow([("" if v is None else v) for v in r])
    except Exception as e:
        print("[pair] could not write %s: %s" % (cp, e))
    print("[pair] side by side (%s) -> %s" % (" | ".join([rk] + tags), xp))
    return xp


def _pick(title):
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        d = filedialog.askdirectory(title=title)
        root.destroy()
        return (d or "").strip()
    except Exception:
        try:
            return input("%s: " % title).strip().strip('"')
        except EOFError:
            return ""


def _pairs_from_settings(argv):
    pairs = []
    if len(argv) >= 3:
        pairs.append((argv[1], argv[2], argv[3] if len(argv) > 3 else None))
        return pairs
    for p in PAIRS:
        if len(p) >= 2:
            pairs.append((p[0], p[1], p[2] if len(p) > 2 else None))
    if REFERENCE and SCENARIOS:
        for s in SCENARIOS:
            pairs.append((REFERENCE, s, None))
    if pairs or not ASK_IF_EMPTY:
        return pairs
    print("[pair] no folders given -- pick them (reference first, then the one to test)")
    ref = _pick("REFERENCE results folder (e.g. Base\\results_base\\SantaFe_spp)")
    if not ref:
        return pairs
    while True:
        t = _pick("TEST results folder to compare against it (cancel to finish)")
        if not t:
            break
        pairs.append((ref, t, None))
    return pairs


def main(argv):
    pairs = _pairs_from_settings(argv)
    if not pairs:
        print("[pair] nothing to compare. Fill in REFERENCE + SCENARIOS or PAIRS, or give "
              "two folders on the command line.")
        return 1
    z4 = _load_panel()
    # ONE FOLDER PER PAIR, ALWAYS. Two test folders with the same name (the
    # as-studied run and yesterday's copy of it) used to get the same label,
    # and the second report overwrote the first.
    used, uniq = {}, []
    for ref, test, label in pairs:
        lab = re.sub(r"[^A-Za-z0-9_.-]+", "_", label or _label_for(ref, test))
        if lab in used:
            used[lab] += 1
            lab = "%s_%d" % (lab, used[lab])
        else:
            used[lab] = 1
        uniq.append((ref, test, lab))
    pairs = uniq
    done = []
    for ref, test, label in pairs:
        try:
            got = compare_pair(z4, ref, test, label)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("[pair] *** %s vs %s failed: %s ***" % (ref, test, e))
            got = None
        if got:
            done.append(got)
    if done and SIDE_BY_SIDE:
        groups = {}
        for g in done:
            groups.setdefault((g["ref"], g["proj"]), []).append(g)
        for (ref, _p), grp in sorted(groups.items()):
            try:
                write_side_by_side(z4, ref, grp)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("[pair] *** the side-by-side sheet failed: %s ***" % e)
    if done:
        out_root = OUT_DIR or os.path.join(z4.STUDY_ROOT, "comparison_pairs")
        idx = os.path.join(out_root, "PAIRS_INDEX.txt")
        try:
            with open(idx, "a") as fh:
                fh.write("%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
                for g in done:
                    fh.write("  %-40s faults %4d  NEW %4d  pre-existing %4d\n"
                             "      reference %s\n      test      %s\n      report    %s\n"
                             % (g["label"], g["faults"], g["new"], g["pre"],
                                g["ref"], g["test"], g["folder"]))
            print("[pair] index -> %s" % idx)
        except Exception:
            pass
    print("[pair] %d of %d pair(s) compared" % (len(done), len(pairs)))
    return 0 if len(done) == len(pairs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
