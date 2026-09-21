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
     C:\KV\comparison_pairs). One folder per pair, each file named with the
     label, so any number of them open side by side in Excel.

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
REFERENCE = r""          # the folder every scenario is compared AGAINST, e.g. r"C:\KV\Base\results_base\SantaFe_spp"
SCENARIOS = [            # the folders to compare against it, one report each, e.g.
    # r"C:\KV\Projects\results_proj\SantaFe_spp",
    # r"C:\KV\Projects\results_proj\SantaFe_spp_poi502",
]
PAIRS = [                # explicit pairs when the reference differs per pair:
    # (r"C:\KV\Base\results_base\IronStar_spp", r"C:\KV\Projects\results_proj\IronStar_spp_poi214", "IronStar_GIA"),
    # (r"C:\KV\Projects\results_proj\SantaFe_spp", r"C:\KV\Projects\results_proj\SantaFe_spp_poi502", "SantaFe_GIA_vs_studied"),
]
OUT_DIR = r""            # "" = <panel folder>\comparison_pairs
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
    spp = z4._study_script_for(case)
    if not spp:
        print("[pair]     no study script beside %s -- cannot re-merge, comparing "
              "the reports as they are" % rdir)
        return False
    env = dict(os.environ)
    try:
        z4._push_settings(env, case)
    except Exception as e:
        print("[pair]     could not pass the run settings to the merge (%s)" % e)
    env["SPP_MERGE_ONLY"] = "1"
    env["SPP_STUDY_DIR"] = case["dir"]
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


def _label_for(ref, test):
    proj, _m, s_t = _split_name(test, None)
    _p, _m2, s_r = _split_name(ref, None)
    rk = "base" if "results_base" in _parts(ref) else \
        ("studied" if not s_r else s_r.strip("_"))
    tk = "studied" if not s_t else s_t.strip("_")
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
    return {"label": label, "ref": ref, "test": test, "folder": folder,
            "faults": len(rows), "new": n_new, "pre": n_pre}


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
