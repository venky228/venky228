# -*- coding: utf-8 -*-
"""
z6_cmp_multi.py -- compare ANY two results folders and write the full
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
  1. Put this file beside z6_main.py (the panel), in C:\KV.
  2. Either fill in REFERENCE + SCENARIOS (or PAIRS) below and run it, or run
     it with nothing filled in and pick the two folders when asked, or give
     them on the command line:
         python z6_cmp_multi.py
         python z6_cmp_multi.py <reference folder> <test folder> [label]
  3. The reports go to OUT_DIR\<label>\  (OUT_DIR defaults to
     C:\KV\comparison_pairs, i.e. <panel folder>\comparison_pairs). One
     folder per PROJECT, and inside it one folder per pair, e.g.
         comparison_pairs\SantaFe_studied_vs_base\00_COMPARISON_REPORT_SantaFe_studied_vs_base.xlsx
         comparison_pairs\SantaFe_Sep21_full_gia_studied_vs_base\00_COMPARISON_REPORT_SantaFe_Sep21_full_gia_studied_vs_base.xlsx
     The label is <project>_<test tag>_vs_<reference tag>; a test folder that
     sits in a folder of its own (Sep21_full gia) carries that folder's name,
     and a label that repeats gets _2, _3. Set OUT_DIR to put them elsewhere,
     or give a label of your own as the third item of a PAIRS entry.
  4. ONE OR TWO BASES: REFERENCE takes one folder or several. Every scenario
     is compared against every base, and each project gets ONE side-by-side
     workbook with old base | new base | studied | GIA in adjacent columns on
     every sheet, and each run's class / change once per base:
         comparison_pairs\SantaFe_SIDE_BY_SIDE_vs_BASE_CQ_F_base_and_base\

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
import json
import csv

# ============================================================================
#  SETTINGS
# ============================================================================
REFERENCE = [            # the base folder(s) every scenario is compared AGAINST -- ONE or SEVERAL (an old and a new
                         # base): a project's results folder (...\SantaFe_spp) OR a parent holding all of them
   r"C:\KV\Base\results_base\Base_CQ", r"C:\KV\Base\results_base\BASE_CQ_F",
    # r"C:\KV\Base\results_base",                             # a second base: both sit side by side in one workbook
]
SCENARIOS = [            # the folders to compare against it -- results folders, or parents holding one per project
                         # (matched by name: SantaFe_spp with SantaFe_spp, IronStar_spp with IronStar_spp ...)
    r"C:\KV\Projects\results_proj\Proj_SGF",
    r"C:\KV\Projects\results_proj\Sep21_full gia",
    # r"C:\KV\Projects\results_proj\SantaFe_spp_poi502",
    # r"C:\KV\Base\results_base_OLDBASE\SantaFe_spp",            # another base run works too
]
PAIRS = [                # explicit pairs when the reference differs per pair:
    # (r"C:\KV\Base\results_base\IronStar_spp", r"C:\KV\Projects\results_proj\IronStar_spp_poi214", "IronStar_GIA"),
    # (r"C:\KV\Projects\results_proj\SantaFe_spp", r"C:\KV\Projects\results_proj\SantaFe_spp_poi502", "SantaFe_GIA_vs_studied"),
]
OUT_DIR = r""            # "" = <panel folder>\comparison_pairs
SIDE_BY_SIDE = True      # also one workbook with every scenario that shares a reference side by side: reference | as studied | GIA | ... per fault
BASES_TOGETHER = True    # two bases for one project -> ONE workbook: old base | new base | studied | GIA on every sheet, and
                         # each run's class / change once per base ('studied vs base'). False: one workbook per base
AUTO_SCENARIOS = False   # OFF: only the folders listed above are compared. True + SCENARIOS empty -> every results folder of the reference's project:
                         #   Projects\results_proj\<proj>_<mode>*, Projects\results_proj\<anything>\<proj>_<mode>*
                         #   and every other Base\results_base*\<proj>_<mode> (another base run) -- .old / __run copies skipped
ALL_PROJECTS = False     # OFF. True + REFERENCE empty + AUTO_SCENARIOS -> every Base\results_base\<proj>_<mode> is a reference in turn
SCAN_ROOTS = [           # extra folders to look in for runs of the same project (each scanned one and two levels deep)
    # r"D:\archive\results_proj",
]
REMERGE_STALE = True     # rebuild a folder's reports from parts\ when the parts are newer (same as the panel)
ASK_IF_EMPTY = True      # nothing above and nothing on the command line -> folder pickers
PANEL = "z6_main.py"
FAST_COMPARE = True      # True = quick: no re-merge from parts\, no measurement reads (a value no report
                         # carries shows '-'), projects compared in parallel. False = the full way, as before
FAST_PARALLEL = 4        # FAST_COMPARE: projects compared at once, each in its own process (1 = one at a time)
# ============================================================================

import os as _os_env
# THE PANEL'S OWN SIDE-BY-SIDE (compare_three_way in z6_main.py) asks for the
# full comparison -- every measured value read -- whatever FAST_COMPARE says.
if (_os_env.environ.get("CMP_MULTI_FULL") or "").strip() == "1":
    FAST_COMPARE = False


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
    mod = types.ModuleType("z6_main")
    mod.__file__ = path
    sys.modules["z6_main"] = mod
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
            if os.path.isfile(os.path.join(d, "z6_spp_%s.py" % kind)):
                return d, kind
    return None, None


def _make_case(key, folder):
    root, kind = _case_root(folder)
    return {"key": key,
            "dir": root or os.path.dirname(os.path.dirname(_norm(folder))),
            "script": ("z6_lch_%s.py" % kind) if kind else "",
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
    mcase["script"] = "z6_lch_%s.py" % ("p" if case.get("_kind") == "p" else "b")
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


def _busy(folder, window=300.0):
    """Seconds since the study last wrote a claim, a part or a status file in
       this folder -- when that was within `window` -- else None. A folder the
       panel is scoring right now is not re-merged from here: two merges
       writing the same report files at once leave either a half-written
       report or a Windows 'file in use' failure."""
    newest = 0.0
    for pat in (os.path.join(folder, "outs", "*.claim"), os.path.join(folder, "outs", "*.rclaim"),
                os.path.join(folder, "parts", "*.csv"), os.path.join(folder, "logs", "REPORT_STATUS*.txt"),
                os.path.join(folder, "logs", "PROGRESS*.csv")):
        for p in glob.glob(pat):
            try:
                newest = max(newest, os.path.getmtime(p))
            except OSError:
                pass
    if not newest:
        return None
    age = time.time() - newest
    return age if age < window else None


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
    if outs and len(scored) < 0.5 * len(outs):
        print("[pair]         *** fewer than half of these .out files carry a verdict. Their")
        print("[pair]             faults will read 'not compared'. A merge cannot score them:")
        print("[pair]             run the panel (z6_main.py) once with PIPELINE = \"compare\"")
        print("[pair]             -- it restores retired .done markers and re-scores the")
        print("[pair]             report -- then run this script again. ***")
    if FAST_COMPARE:
        if behind > z4.STALE_REPORT_TOL_S:
            print("[pair]         FAST_COMPARE: not re-merged -- compared on its reports as "
                  "they stand (FAST_COMPARE = False to re-merge)")
        return
    _b = _busy(folder) if (REMERGE_STALE and behind > z4.STALE_REPORT_TOL_S) else None
    if _b is not None:
        print("[pair]         the study wrote to this folder %.0f s ago -- it is being scored" % _b)
        print("[pair]         right now, so it is NOT re-merged from here; its reports are")
        print("[pair]         compared as they stand. Run this again when the panel is done.")
    elif REMERGE_STALE and behind > z4.STALE_REPORT_TOL_S:
        if _merge_folder(z4, case, folder, suffix):
            try:
                outs, scored = z4._out_and_scored_sets(folder, proj)
                print("[pair]         after the merge: %d with a verdict" % len(scored))
            except Exception:
                pass


_STD_PARENTS = ("results_base", "results_proj", "results", "base", "projects", "kv")


def _given_tags():
    """CMP_MULTI_TAGS (a JSON file of {folder: column name}), normalised; {}
       when the variable is not set, as in every standalone run."""
    p = os.environ.get("CMP_MULTI_TAGS")
    if not p:
        return {}
    try:
        with open(p) as fh:
            return dict((_norm(k), re.sub(r"[^A-Za-z0-9_.-]+", "_", v))
                        for k, v in json.load(fh).items())
    except Exception:
        return {}


def _folder_tag(folder):
    """What tells this folder apart: its own suffix ('poi502', 'cap50'), and
       the folder it sits in when that is not one of the usual results roots
       -- so ...\Sep21_full gia\SantaFe_spp is 'Sep21_full_gia' and not
       'studied', which is what ...\results_proj\SantaFe_spp is called."""
    # NAMES GIVEN BY THE CALLER (z6_main's 4-scenario report): {folder: tag}.
    _given = _given_tags().get(_norm(folder))
    if _given:
        return _given
    _p, _m, sfx = _split_name(folder, None)
    bits = []
    parts = [x for x in re.split(r"[\\/]+", str(folder).strip().strip('"')) if x]
    parent = parts[-2] if len(parts) >= 2 else ""
    # results_base\SantaFe\SantaFe_spp: the project's own folder says nothing
    # the name does not -- look one level further up for a dated parent
    if _p and parent == _p:
        parts = parts[:-1]
        parent = parts[-2] if len(parts) >= 2 else ""
    if parent and parent.lower() not in _STD_PARENTS and not re.match(r"^[A-Za-z]:$", parent):
        bits.append(parent)
    # A BASE FOLDER IS A BASE. ...\results_base\BASE_CQ_F\SantaFe_spp is
    # 'BASE_CQ_F_base' and ...\results_base\SantaFe_spp is 'base', so an old
    # base and a new one compared side by side never share a column name.
    _low = [x.lower() for x in parts]
    _dflt = "base" if any(x == "base" or x.startswith("results_base") for x in _low) else "studied"
    bits.append(sfx.strip("_") if sfx else _dflt)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", "_".join(bits))


def _has_outs(d):
    try:
        return bool(glob.glob(os.path.join(d, "outs", "*.out")))
    except Exception:
        return False


def _skip_dir(d):
    n = _base(d).lower()
    # z6: the gen-test runs (<proj>_<mode>_gt_*) and the folders a gen-test
    # reset moved aside (*_prev_<time>) are not scenarios of the project
    return (n.endswith(".old") or "__run" in n or n.startswith("_")
            or "_gt_" in n or "_prev_" in n)


def _all_references(z4):
    """Every Base\results_base\<proj>_<mode> that holds .out files."""
    out = []
    root = os.path.join(z4.STUDY_ROOT, "Base", "results_base")
    for m in list(z4.MODES) or ["spp"]:
        for d in _both_layouts(root, "*_%s" % m):
            if os.path.isdir(d) and _has_outs(d) and not _skip_dir(d):
                out.append(_norm(d))
    return out


def _both_layouts(root, pat):
    """root\pat and root\<proj>\pat (one folder per project, names that
       start with that folder's '<proj>_'), sorted by folder name."""
    c = glob.glob(os.path.join(root, pat))
    c += [x for x in glob.glob(os.path.join(root, "*", pat))
          if _base(x).startswith(_base(os.path.dirname(x)) + "_")]
    return sorted(set(c), key=lambda x: (_base(x), x))


def _discover_scenarios(z4, ref):
    """Every OTHER results folder of the reference's project: the project
       runs (as studied, GIA, capacity, .dyr edits, dated sub-folders) and any
       other base run. Nothing is compared twice and the reference is never
       compared with itself."""
    proj, mode, _sfx = _split_name(ref, z4.MODES)
    pat = "%s_%s*" % (proj, mode)
    roots = [os.path.join(z4.STUDY_ROOT, "Projects", "results_proj")]
    for d in sorted(glob.glob(os.path.join(z4.STUDY_ROOT, "Base", "results_base*"))):
        # NOT THE _Q STUDY'S (results_base_q): the queue-project runs have their
        # own panel and side-by-side, and are not scenarios of this study.
        if os.path.isdir(d) and not os.path.basename(d).lower().endswith(("_q", "_f")):
            roots.append(d)
    roots += [r for r in SCAN_ROOTS if r]
    found, seen = [], set([_norm(ref)])
    for root in roots:
        if not os.path.isdir(root):
            continue
        cands = sorted(glob.glob(os.path.join(root, pat)))
        cands += sorted(glob.glob(os.path.join(root, "*", pat)))
        for d in cands:
            if not os.path.isdir(d) or _skip_dir(d) or _skip_dir(os.path.dirname(d)):
                continue
            p2, m2, _s2 = _split_name(d, z4.MODES)
            if p2 != proj or m2 != mode or not _has_outs(d):
                continue
            n = _norm(d)
            if n in seen:
                continue
            seen.add(n)
            found.append(n)
    return found


def _fault_ids_of(z4, proj):
    """Every fault id in the project's shared list (the one the study ran)."""
    import csv
    ids = []
    for p in (os.path.join(z4.STUDY_ROOT, "SPP_FAULTS_CON_%s.csv" % proj),):
        try:
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    fid = (r.get("fault_id") or "").strip()
                    if fid:
                        ids.append(fid)
        except Exception:
            continue
        if ids:
            break
    return ids


def _idsort(ids):
    return sorted(set(ids), key=lambda x: (len(x), x))


def _unrun(z4, folder, proj):
    """(never run, run but no verdict) for one results folder: ids from the
       fault list with no .out, and .out files that carry no verdict (crashed,
       non-finite, unscored)."""
    try:
        outs, scored = z4._out_and_scored_sets(folder, proj)
    except Exception:
        outs, scored = set(), set()
    outs = set(str(x) for x in outs)
    scored = set(str(x) for x in scored)
    listed = _fault_ids_of(z4, proj)
    never = [f for f in listed if f not in outs] if listed else []
    novote = [f for f in outs if f not in scored and not f.upper().startswith("FLAT")]
    # AN ID THE FAULT LIST DOES NOT HAVE CANNOT BE RE-RUN BY NAME (F02_previous
    # is a kept copy, not a fault): listed on its own line, kept out of the
    # line that is pasted into the panel.
    extra = []
    if listed:
        lset = set(listed)
        extra = [f for f in novote if f not in lset]
        novote = [f for f in novote if f in lset]
    return _idsort(never), _idsort(novote), _idsort(extra), bool(listed)


def _rerun_lines(z4, tag, folder, proj):
    never, novote, extra, have_list = _unrun(z4, folder, proj)
    _root, kind = _case_root(folder)
    side = "proj" if kind == "p" else ("base" if kind == "b" else "?")
    parent = _base(os.path.dirname(_norm(folder))).lower()
    if parent == str(proj or "").lower():          # results_base\<proj>\<folder>
        parent = _base(os.path.dirname(os.path.dirname(_norm(folder)))).lower()
    L = ["%s   %s" % (tag, folder)]
    if not have_list:
        L.append("   (fault list %s not found -- faults never run cannot be listed)"
                 % os.path.join(z4.STUDY_ROOT, "SPP_FAULTS_CON_%s.csv" % proj))
    else:
        L.append("   %d fault(s) with NO .out (never run) : %s" % (len(never), ", ".join(never) or "-"))
    L.append("   %d .out(s) with NO verdict (crashed / non-finite / unscored): %s"
             % (len(novote), ", ".join(novote) or "-"))
    if extra:
        L.append("   %d .out(s) not in the fault list -- cannot be re-run by id: %s"
                 % (len(extra), ", ".join(extra)))
    both = _idsort(never + novote)
    L.append("   RUN_CASES   = \"%s\"" % side)
    L.append("   ONLY_FAULTS = [%s]" % ", ".join('"%s"' % x for x in both))
    if parent not in ("results_base", "results_proj"):
        L.append("   NOTE: the panel writes to %s\\results_%s\\%s\\%s, not to this folder -- a "
                 "re-run lands there." % ("Base" if side == "base" else "Projects",
                                          "base" if side == "base" else "proj", proj, _base(folder)))
    return L, both


def write_rerun_list(z4, path, entries):
    """RERUN_*.txt: per folder, the faults with no result and the
       RUN_ONLY_FAULTS line to paste into z6_main.py to re-run them
       (RUN_CASES = "base" or "proj" for that side)."""
    L = ["FAULTS WITH NO RESULT -- what to re-run in z6_main.py",
         "generated %s" % time.strftime("%Y-%m-%d %H:%M"),
         "",
         "To re-run one folder's list, in z6_main.py set, for that run only:",
         "    PIPELINE             = \"all\"",
         "    RUN_CASES            = the value given below for that folder",
         "    ONLY_FAULTS          = the list given below for that folder",
         "    RUN_ONLY_MISSING_OUT = False   (a crashed fault HAS an .out; with True it is",
         "                                    skipped. The selected ids get their .done /",
         "                                    .attempts cleared and their .out replaced;",
         "                                    nothing else is touched.)",
         "Afterwards set ONLY_FAULTS = [] and RUN_ONLY_MISSING_OUT = True again and run",
         "PIPELINE = \"compare\" once, so the full reports include the new results.",
         ""]
    for tag, folder, proj in entries:
        lines, _b = _rerun_lines(z4, tag, folder, proj)
        L += lines + [""]
    try:
        with open(path, "w") as fh:
            fh.write("\n".join(L) + "\n")
        print("[pair] re-run list -> %s" % path)
    except Exception as e:
        print("[pair] could not write %s: %s" % (path, e))


def _label_for(ref, test):
    proj, _m, _s = _split_name(test, None)
    rk = _folder_tag(ref)
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
    z4.COMPARE_DIR = os.path.join(out_root, proj_t)   # one folder per project
    z4.results_dir = lambda case, p, m: ref if str(case.get("key")) == "BASE" else test
    z4._res_root = lambda case: os.path.dirname(ref if str(case.get("key")) == "BASE" else test)
    z4.discover_projects = lambda m: ([proj_t], [], [])
    for name in ("_MEAS_CACHE", "_WANT_BUSES", "_WHERE", "_SCEN_PART_CACHE"):
        d = getattr(z4, name, None)
        if isinstance(d, dict):
            d.clear()
    z4._RUN_OUTPUT[0] = label

    # THE MARKERS FIRST, THEN THE REPORTS. A base folder whose .done markers
    # were retired by the old size rule reads as "CRASHED (no .done)" on 180
    # faults, and every one of them lands on the Not compared sheet. The
    # panel's retire/restore step gives those markers back (judging each by
    # its own tend=, never by size) and back-dates the reports that were
    # written without them; _prepare then sees reports older than the parts
    # and re-merges, so the verdicts come back before anything is compared.
    try:
        # NOT IN z6_main's SIDE-BY-SIDE CHILD (CMP_MULTI_FULL): the panel ran
        # this over every folder moments earlier, and here it re-opened every
        # .done of both cases once per pair.
        if (hasattr(z4, "retire_truncated_done")
                and (os.environ.get("CMP_MULTI_FULL") or "").strip() != "1"):
            z4.retire_truncated_done(quiet=False)
    except Exception as e:
        print("[pair]   the .done marker check failed (%s) -- comparing as the folders are" % e)
    _prepare(z4, case_b, ref, sfx_r)
    _prepare(z4, case_t, test, sfx_t)
    for name in ("_MEAS_CACHE", "_WANT_BUSES", "_WHERE", "_OUT_SET_CACHE", "_SCEN_PART_CACHE"):
        d = getattr(z4, name, None)
        if isinstance(d, dict):
            d.clear()

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
    write_rerun_list(z4, os.path.join(folder, "RERUN_%s.txt" % label),
                     [("REFERENCE", ref, proj_t), ("TEST", test, proj_t)])
    # EVERY SHEET THE PANEL WRITES, kept for the side-by-side workbook -- the
    # same builders, the same rows, so the two never disagree.
    def _safe(fn, *a):
        try:
            return fn(*a) or []
        except Exception as e:
            print("[pair]   %s failed (%s) -- that sheet will be short" % (getattr(fn, "__name__", "?"), e))
            return []
    summ = _safe(z4._summary_rows, [res])
    detail = _safe(z4._report_rows, [res])
    notrun = _safe(z4._notrun_rows, [res])
    poi = _safe(z4._poi_power_rows, [res])
    new_el = _safe(lambda d: z4._compact_view(z4._project_caused_rows(d)), detail)
    pre_el = _safe(lambda d: z4._compact_view(z4._pre_existing_element_rows(d)), detail)
    try:
        outs_r, scored_r = z4._out_and_scored_sets(ref, proj_t)
        outs_t, scored_t = z4._out_and_scored_sets(test, proj_t)
    except Exception:
        outs_r = scored_r = outs_t = scored_t = set()
    return {"label": label, "ref": ref, "test": test, "folder": folder, "report": xl,
            "proj": proj_t, "faults": len(rows), "new": n_new, "pre": n_pre, "summary": summ,
            "detail": detail, "notrun": notrun, "poi": poi, "new_el": new_el,
            "pre_el": pre_el, "tag": _folder_tag(test),
            "n_out_ref": len(outs_r), "n_scored_ref": len(scored_r),
            "n_out": len(outs_t), "n_scored": len(scored_t),
            "only_ref": sorted(only_b or []), "only_test": sorted(only_t or [])}


_SBS_EL = [("value", "project_value"), ("state", "project_state"),
           ("class", "element_classification"), ("change", "change"),
           ("past_limit", "past_limit")]

# The criterion families a measurement file holds a NUMBER for. The others
# (terminal voltage, review, bus angle, yes/no statements) have none, and a
# cell for them says so instead of "not measured".
_MEASURABLE = ("recovery", "overshoot", "steady", "angle", "trip")
_KEY4 = ("fault", "criterion", "element", "bus_number")

# ONE ROW PER VIOLATION. Runs name the same element differently -- "765911"
# in one report, "POI 765911" or "PROJ 765912" in another, "763676 [converging,
# ...]" where the study added a note -- and each spelling made its own row.
# The key drops the prefix and the note; the row shows the fullest name.
_EL_PRE = re.compile(r"^(POI|PROJ)\s+", re.I)
_EL_NOTE = re.compile(r"\s*\[[^\]]*\]\s*$")


def _elkey(el):
    return _EL_PRE.sub("", _EL_NOTE.sub("", str(el).strip())).strip()


def _key4(vals):
    """(fault, criterion, element, bus) with the element's name normalised."""
    k = [str(v).strip() for v in vals]
    k[2] = _elkey(k[2])
    return tuple(k)


def _better_label(old, new):
    """The fuller of two names of one element: a POI/PROJ prefix, then a note."""
    def sc(s):
        s = str(s)
        return (1 if _EL_PRE.match(s) else 0, 1 if _EL_NOTE.search(s) else 0)
    return new if (old is None or sc(new) > sc(old)) else old


# The columns a reader compares, in this order, right after the key; all the
# others (event, source, area, hops, notes, run settings ...) go to the end.
# 'project' (the workbook is one project's) and 'unit' (the limit says it)
# are dropped.
_ORDER = {"limit": 0, "verdict_projects": 1, "worst_criterion": 2, "project_value": 3,
          "change": 4, "classification": 5, "element_classification": 5,
          "fault_classification": 5, "who_caused_it": 5, "action": 6, "past_limit": 7,
          "project_state": 8, "projects_state": 8}
_DROP = set(["project", "unit"])


def _rank_col(c, element=False):
    """Where column c goes. On an element sheet the element's own numbers come
       first; the fault's verdict (the same on every row of a fault) after."""
    if element and c in ("verdict_projects", "worst_criterion"):
        return 9
    if not element and c == "limit":
        return 2.5                                # a fault's limit: beside its worst value
    return _ORDER.get(c, 20)


def _empty(z4, v):
    """True for a cell that says nothing: blank, '-', or the panel's own n/a.
       The panel never writes a blank -- every empty cell is EMPTY_CELL -- so a
       test for "" alone never saw a missing value and never filled one."""
    if v is None:
        return True
    s = str(v).strip()
    return s in ("", "-", str(getattr(z4, "EMPTY_CELL", "n/a")))


def _free(z4, keep_want=False):
    """Drop everything the panel cached for the last pair. A 32-bit python has
       2 GB of address space, and three folders' measurements plus every pair's
       detail rows filled it: the comparison then failed to read its own parts
       ("MemoryError -- this 32-bit python ran out of its 2 GB address space")
       and the workbook could not be built."""
    import gc
    for name in ("_MEAS_CACHE", "_WANT_BUSES", "_WHERE", "_OUT_SET_CACHE",
                 "_SCEN_PART_CACHE", "_PART_LAY_CACHE", "_VIO_EXTRA", "_DIST_CACHE"):
        if keep_want and name == "_WANT_BUSES":
            continue               # the element sheet's bus list must survive the read
        d = getattr(z4, name, None)
        if isinstance(d, dict):
            d.clear()
    gc.collect()


def _sbs_tags(group, rk=None):
    """One column tag per run, never the same twice -- and never the same as
       the reference's, or 'value | base' would name two different folders."""
    tags, used = [], ([rk] if rk else [])
    for g in group:
        t = g["tag"]
        n = 2
        while t in tags or t in used:
            t = "%s_%d" % (g["tag"], n)
            n += 1
        tags.append(t)
    return tags


def _lay1(ref, group, tags, rk):
    """The layout of a workbook with ONE reference: each run's tag names its
       folder and its comparison both, exactly as it always did."""
    return {"refs": [rk], "ref_dir": {rk: ref}, "tests": list(tags),
            "test_dir": dict((t, g.get("test")) for g, t in zip(group, tags)),
            "rk_of": dict((t, rk) for t in tags), "tt_of": dict((t, t) for t in tags),
            "of_test": dict((t, [t]) for t in tags), "of_ref": {rk: list(tags)},
            "multi": False}


def _layout(group):
    """(comparisons, their tags, layout) of one project's side-by-side workbook.

       ONE reference: as before -- one tag per run.
       TWO OR MORE (an old and a new base): each FOLDER keeps one tag -- every
       base's values sit once per base, every run's measured values once per
       run -- and each COMPARISON is tagged '<run> vs <base>', because one
       violation can be pre-existing against the old base and introduced
       against the new one. The comparisons are ordered run by run: studied vs
       old, studied vs new, gia vs old, gia vs new."""
    refs = []
    for g in group:
        r = _norm(g["ref"])
        if r not in refs:
            refs.append(r)
    if len(refs) == 1:
        rk = _folder_tag(refs[0])
        tags = _sbs_tags(group, rk)
        return group, tags, _lay1(group[0]["ref"], group, tags, rk)
    used, rtag = [], {}
    for r in refs:
        t0 = _folder_tag(r)
        t, n = t0, 2
        while t in used:
            t, n = "%s_%d" % (t0, n), n + 1
        used.append(t)
        rtag[r] = t
    tests, ttag, grp, seen = [], {}, [], set()
    for g in group:
        r, s = _norm(g["ref"]), _norm(g["test"])
        if (r, s) in seen:
            continue                              # the same comparison listed twice
        seen.add((r, s))
        if s in refs:
            # A REFERENCE COMPARED AGAINST ANOTHER (base EGF off vs base): its
            # values already have their own column as a reference, so it is
            # not listed a second time as a run. Its pair report stands alone.
            continue
        grp.append(g)
        if s not in ttag:
            t0 = g["tag"]
            t, n = t0, 2
            while t in used:
                t, n = "%s_%d" % (t0, n), n + 1
            used.append(t)
            ttag[s] = t
            tests.append(s)
    so = dict((s, i) for i, s in enumerate(tests))
    ro = dict((r, i) for i, r in enumerate(refs))
    grp.sort(key=lambda g: (so[_norm(g["test"])], ro[_norm(g["ref"])]))
    tags = ["%s vs %s" % (ttag[_norm(g["test"])], rtag[_norm(g["ref"])]) for g in grp]
    lay = {"refs": [rtag[r] for r in refs], "ref_dir": dict((rtag[r], r) for r in refs),
           "tests": [ttag[s] for s in tests], "test_dir": dict((ttag[s], s) for s in tests),
           "rk_of": {}, "tt_of": {}, "of_test": {}, "of_ref": {}, "multi": True}
    for g, t in zip(grp, tags):
        rr, tt = rtag[_norm(g["ref"])], ttag[_norm(g["test"])]
        lay["rk_of"][t] = rr
        lay["tt_of"][t] = tt
        lay["of_test"].setdefault(tt, []).append(t)
        lay["of_ref"].setdefault(rr, []).append(t)
    return grp, tags, lay


def _per_test(z4, vals):
    """One run's cell from its comparisons against each base -- [(comparison,
       value or None, from its own report row)]. The value they agree on; each
       distinct one named by comparison when they do not; a report's own value
       before one filled from measurements. With one base there is one
       comparison and its value is returned as it is."""
    def _real(v):
        return v is not None and not _empty(z4, v) and str(v).strip() != "not in this run"
    got = [(t, v) for t, v, own in vals if own and _real(v)] or \
          [(t, v) for t, v, own in vals if _real(v)]
    if not got:
        for _t, v, _own in vals:
            if v is not None:
                return v
        return "not in this run"
    if len(set(str(v).strip() for _t, v in got)) == 1:
        return got[0][1]
    st = {}
    for t, v in got:
        _merge_add(st, "v", t, v, z4)
    return _merge_get(st, "v", z4)


def _num(v):
    """The number in a cell, whether it is a bare value or a limit phrase.

       The limit column carries the panel's own wording -- "max 1.20 pu",
       "0.95 - 1.05 pu", "max 16 deg" -- so float() on it always raised and
       every measured-only cell was called "within limit" whatever it read."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    txt = str(v).strip()
    if txt in ("", "-"):
        return None
    try:
        return float(txt)
    except (TypeError, ValueError):
        pass
    m = re.findall(r"-?\d+(?:\.\d+)?", txt)
    if not m:
        return None
    try:
        # a band ("0.95 - 1.05 pu") is judged on its upper bound, which is the
        # one an overvoltage passes; a "max"/"min" phrase has one number.
        return float(m[-1] if len(m) > 1 else m[0])
    except ValueError:
        return None


def _limits(txt):
    """(low, high) of a limit as the panel words it: 'min 0.70 pu' -> (0.7,
       None), 'max 1.20 pu' -> (None, 1.2), '0.90 - 1.10 pu' -> (0.9, 1.1).
       A steady-state band has TWO sides; judging it on its upper bound alone
       called 0.85 pu 'within limit'."""
    if txt is None:
        return None, None
    s = str(txt).strip().lower()
    try:
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", s)]
    except ValueError:
        return None, None
    if not nums:
        return None, None
    if s.startswith("min"):
        return nums[0], None
    if s.startswith("max"):
        return None, nums[0]
    if len(nums) >= 2:
        return min(nums[0], nums[1]), max(nums[0], nums[1])
    return None, nums[0]


def _fmt(v, fam):
    if v is None:
        return "-"
    try:
        return ("%.1f" % v) if fam == "angle" else ("%.3f" % v)
    except TypeError:
        return str(v)


def _meas_fill(z4, meas, fam, fid, element):
    """(value text, state text) for one element of one fault from ONE folder's
       own measurements -- what that bus or machine did in that run whether or
       not it broke anything there."""
    if fam == "trip":
        rec = None
        try:
            rec = z4.machine_state(meas, fid, element)
        except Exception:
            rec = None
        if rec is None:
            return "-", "not measured in this run"
        # THE PRE-FAULT MW, like the panel's own report rows. pend is what the
        # machine ended at; putting it in the same column as the report's p0
        # compared two different quantities side by side.
        p0 = rec.get("p0")
        return _fmt(p0, fam), (z4._trip_state_text(rec, None) or "measured in this run")
    try:
        v = z4.measured_value(meas, fam, fid, element)
    except Exception:
        v = None
    if v is None:
        return "-", "not measured in this run"
    return _fmt(v, fam), "measured in this run"


def _meas_cell(z4, meas, fam, fid, element):
    """(value, state, seconds above 1.20) of one element in one folder."""
    val, state = _meas_fill(z4, meas, fam, fid, element)
    above = "-"
    if fam == "overshoot" and val != "-":
        try:
            a = z4.measured_above(meas, fid, element)
            if a is not None:
                above = "%.4f" % float(a)
        except Exception:
            pass
    return val, state, above


def _measured_class(fam, val, state, limit):
    """How one MEASURED cell reads against its criterion's limit -- for a run
       whose own report has no row for this element."""
    if fam not in _MEASURABLE:
        return "no numeric value for this criterion"
    if str(val).strip() in ("", "-"):
        return "not measured in this run"
    if fam == "trip":
        return ("TRIPPED in this run (measured)" if str(state).strip().upper().startswith("TRIPPED")
                else "connected in this run (measured)")
    v = _num(val)
    if v is None:
        return "not measured in this run"
    lo, hi = _limits(limit)
    if lo is not None and v < lo and fam in ("recovery", "steady"):
        return "under the limit in this run (measured)"
    if hi is not None and v > hi:
        if fam == "angle":
            return "swing above %g deg in this run (measured; damping is judged by the study)" % hi
        if fam in ("overshoot", "steady"):
            return "over the limit in this run (measured)"
    return "within limit here (measured)"


# ---- merging one value that several pair workbooks each carry ---------------
# The reference's numbers are the same in every pair -- same folder, same
# measurements -- so they are shown ONCE, beside the runs' numbers. When two
# pairs disagree (the summary's base value belongs to each pair's WORST
# criterion, and those can differ) every distinct value is shown with the run
# it came from, instead of the first one standing for all.
def _merge_add(store, col, t, v, z4):
    if _empty(z4, v):
        return
    sv = str(v).strip()
    lst = store.setdefault(col, [])
    for ent in lst:
        if ent[0] == sv:
            if t not in ent[2]:
                ent[2].append(t)
            return
    lst.append([sv, v, [t]])


def _merge_get(store, col, z4, default=None):
    lst = store.get(col)
    if not lst:
        return z4.EMPTY_CELL if default is None else default
    if len(lst) == 1:
        return lst[0][1]
    return " / ".join("%s: %s" % (", ".join(ent[2]), ent[1]) for ent in lst)


def _merge_one(store, col):
    """The single value, or None when the pairs disagree or have none."""
    lst = store.get(col)
    return lst[0][1] if lst and len(lst) == 1 else None


def _poi_hops(z4, store):
    """Nodes from the POI to sort by: the nearest any run reports (runs can
       disagree by a node where the project adds buses); unknown sorts last."""
    return min([z4._poi_nodes_num(ent[1]) for ent in (store.get("hops_from_poi") or [])]
               or [10 ** 6])


def _sbs_context(z4, ref, group, tags, rk, lay=None):
    """What every side-by-side sheet needs besides the pair rows themselves:
       each run's detail and summary indexed by key, each reference's values
       merged across its pairs, and the MEASURED value of every element in
       every folder -- each base, each run -- where no report has a number.

       The measurements are read ONE FOLDER AT A TIME, the few cells needed
       are kept as short strings, and the folder is dropped before the next --
       holding three folders' measurements together is what ran a 32-bit
       python out of memory."""
    lay = lay or _lay1(ref, group, tags, rk)
    RC = dict((c, i) for i, c in enumerate(z4._REPORT_COLS))
    SC = dict((c, i) for i, c in enumerate(z4._SUMMARY_COLS))
    proj = group[0]["proj"]
    det, summ, order, seen, shared, label = {}, {}, [], set(), {}, {}
    base = dict((x, {}) for x in lay["refs"])     # base tag -> key -> its own values
    _scols = ("planning_event", "fault_source", "measured", "area", "hops_from_fault",
              "hops_from_poi", "limit", "unit", "description")
    _bcols = ("verdict_base", "base_value", "base_state", "base_value_note", "secs_above_1_20_base")

    def _score(r):
        return (0 if _empty(z4, r[RC["project_value"]]) else 2) + \
               (0 if _empty(z4, r[RC["base_value"]]) else 1)
    for g, t in zip(group, tags):
        bt = base[lay["rk_of"][t]]
        d, dk = {}, []
        # ONE ROW PER ELEMENT IN ONE RUN: of two spellings of one element the
        # row with numbers wins; the other adds nothing but its name.
        for r in (g.get("detail") or []):
            k = _key4(r[RC[c]] for c in _KEY4)
            label[k] = _better_label(label.get(k), str(r[RC["element"]]).strip())
            if k not in d:
                d[k] = r
                dk.append(k)
            elif _score(r) > _score(d[k]):
                d[k] = r
        for k in dk:
            r = d[k]
            if k not in seen:
                seen.add(k)
                order.append(k)
                shared[k] = {}
            b = bt.setdefault(k, {})
            for c in _scols:
                _merge_add(shared[k], c, t, r[RC[c]], z4)
            for c in _bcols:
                _merge_add(b, c, t, r[RC[c]], z4)
        det[t] = d
        s = {}
        for r in (g.get("summary") or []):
            s[str(r[SC["fault"]]).strip()] = r
        summ[t] = s
    # WHICH CELLS NEED A MEASUREMENT: a base where none of its pairs had a
    # number, and a run where one of its reports has no row or no number.
    todo = {}
    for k in order:
        fam = z4._criterion_family(k[1])
        if not k[1] or fam not in _MEASURABLE:
            continue
        el = k[2] if z4._bus_of_element(k[2]) is not None else k[3]
        for x in lay["refs"]:
            b = base[x].get(k) or {}
            if "base_value" not in b or "base_state" not in b:
                todo.setdefault(x, []).append((k, fam, el))
        for tt in lay["tests"]:
            for t in lay["of_test"][tt]:
                r = det[t].get(k)
                if r is None or _empty(z4, r[RC["project_value"]]) or _empty(z4, r[RC["project_state"]]):
                    todo.setdefault(tt, []).append((k, fam, el))
                    break
    fills = {}
    if todo and FAST_COMPARE:
        print("[pair]   FAST_COMPARE: %d value(s) no report carries are left '-' -- the "
              "measurements are not read (FAST_COMPARE = False to fill them)"
              % sum(len(v) for v in todo.values()))
        todo = {}
    if todo:
        for t, folder in ([(x, lay["ref_dir"][x]) for x in lay["refs"]] +
                          [(tt, lay["test_dir"][tt]) for tt in lay["tests"]]):
            if not todo.get(t):
                continue
            print("[pair]   reading the measurements of %s for %d element value(s) its report "
                  "does not carry ..." % (folder, len(todo[t])))
            try:
                _free(z4, keep_want=True)
                # ONLY THE BUSES THIS FOLDER'S CELLS NEED. The panel's reader
                # keeps every fault's row for a wanted bus; wanting every bus
                # on the sheet kept hundreds of thousands of rows per folder.
                want = z4._want_buses(proj)
                want.clear()
                for k, fam, el in todo[t]:
                    b = z4._bus_of_element(el)
                    if b is not None:
                        want.add(str(b))
                m = z4.read_measurements(folder, proj)
                for k, fam, el in todo[t]:
                    fills[(t,) + k] = _meas_cell(z4, m, fam, k[0], el)
                del m
            except Exception as e:
                print("[pair]   measurements of %s could not be read (%s) -- those cells "
                      "show '-'" % (folder, e))
            _free(z4, keep_want=True)
    # EACH REFERENCE'S OWN NUMBERS, FILLED WHERE NONE OF ITS PAIRS HAD ONE.
    for x in lay["refs"]:
        for k in order:
            f = fills.get((x,) + k)
            if not f:
                continue
            b = base[x].setdefault(k, {})
            if "base_value" not in b and f[0] != "-":
                _merge_add(b, "base_value", x, f[0], z4)
                if "base_value_note" not in b:
                    _merge_add(b, "base_value_note", x, "from the reference measurements", z4)
            if "base_state" not in b and f[1]:
                _merge_add(b, "base_state", x, f[1], z4)
            if "secs_above_1_20_base" not in b and f[2] != "-":
                _merge_add(b, "secs_above_1_20_base", x, f[2], z4)
    return {"RC": RC, "SC": SC, "det": det, "summ": summ, "order": order,
            "base": base, "shared": shared, "fills": fills, "rk": rk, "lay": lay,
            "label": label}


def _base_num(z4, ctx, k, t=None):
    """The reference value to subtract from run t's value: t's OWN pair's base
       value when it has one (it belongs to the same criterion), else the one
       value t's base agrees on across its pairs, else None."""
    RC = ctx["RC"]
    lay = ctx["lay"]
    if t is not None:
        r = ctx["det"][t].get(k)
        if r is not None and not _empty(z4, r[RC["base_value"]]):
            return _num(r[RC["base_value"]])
    refs = [lay["rk_of"][t]] if t is not None else lay["refs"]
    if len(refs) != 1:
        return None
    one = _merge_one(ctx["base"][refs[0]].get(k, {}), "base_value")
    return _num(one) if one is not None else None


def _detail_fallback(z4, ctx, t, k4):
    """A detail row for run t when its own report has none for this element:
       the fault-level cells from t's summary, the element's cells from t's
       measurements, judged against the limit."""
    r0 = ctx["det"][t].get(k4)
    if r0 is not None:
        return r0
    RC, SC = ctx["RC"], ctx["SC"]
    E = z4.EMPTY_CELL
    row = [E] * len(z4._REPORT_COLS)

    def put(c, v):
        if c in RC:
            row[RC[c]] = E if (v is None or str(v).strip() == "") else v
    fid, crit, el, bus = k4
    for c, v in zip(_KEY4, k4):
        put(c, v)
    s = ctx["summ"][t].get(fid)
    if s is not None:
        for c in ("run_setting", "dyr_edits", "project_output", "project",
                  "planning_event", "fault_source", "verdict_base", "description"):
            if c in SC:
                put(c, s[SC[c]])
        put("verdict_projects", s[SC["verdict_projects"]])
        put("fault_classification", s[SC["classification"]])
        put("fault_introduced_by_projects", "YES" if s[SC["classification"]] == z4.CLS_NEW else "no")
    else:
        put("verdict_projects", "fault not in this run")
    if crit:
        fam = z4._criterion_family(crit)
        limit = _merge_one(ctx["shared"].get(k4, {}), "limit")
        f = ctx["fills"].get((ctx["lay"]["tt_of"][t],) + k4)
        if f:
            val, state, above = f
        elif fam in _MEASURABLE:
            val, state, above = "-", "not measured in this run", "-"
        else:
            val, state, above = "-", "no numeric value for this criterion", "-"
        put("project_value", val)
        put("project_state", state)
        put("secs_above_1_20_project", above)
        put("element_classification", _measured_class(fam, val, state, limit))
        v, b0, lim = _num(val) if val != "-" else None, _base_num(z4, ctx, k4, t), _num(limit)
        put("change", _fmt(v - b0, fam) if (v is not None and b0 is not None and fam != "trip") else "-")
        put("past_limit", _fmt(v - lim, fam) if (v is not None and lim is not None) else "-")
        put("criterion_is_new", "-")
        put("limit", limit)
    return row


def _change(z4, ctx, k4, t, val, fam, default="-"):
    """run t's value minus ITS base's value, the panel's 'change', for a
       report row that had one of the two numbers missing -- the missing one
       now read from that folder's measurements. Tripping has none: the two
       numbers are pre-fault MW, their difference is dispatch (as the panel)."""
    if fam == "trip" or _empty(z4, val):
        return default
    v, b0 = _num(val), _base_num(z4, ctx, k4, t)
    return _fmt(v - b0, fam) if (v is not None and b0 is not None) else default


def _compact_fallback(z4, ctx, t, k4):
    try:
        return z4._compact_view([_detail_fallback(z4, ctx, t, k4)])[0]
    except Exception:
        return None


def _run_has_fault(ctx, t, fid):
    """Run/pair t's summary row for this fault, or None when it never ran it."""
    try:
        return ((ctx or {}).get("summ") or {}).get(t, {}).get(str(fid).strip())
    except Exception:
        return None


def _absent_cell(z4, ctx, lay, kind, t, fid, c):
    """WHAT A COLUMN SAYS WHERE ITS RUN HAS NO ROW FOR THIS LINE.

       'not in this run' was written whenever a run had no row of its own for a
       line -- and a run has none for every fault it COMPARED (sheet 7 lists the
       ones it did not) and for every element it did not list. So the EGF-off
       base, which ran all 180 SantaFe faults on 28 Sep, read 'not in this run'
       beside every fault the GIA pair could not compare. The run's own summary
       says whether it ran the fault: if it did, its state is given (its
       verdict) or the cell says the value was not recorded; 'not in this run'
       is left for a run that truly does not have the fault."""
    pairs = list(lay.get("of_ref", {}).get(t, [])) if kind == "ref" else [t]
    SC = (ctx or {}).get("SC") or {}
    for p in pairs:
        s = _run_has_fault(ctx, p, fid)
        if s is None:
            continue
        if c in ("base_state", "projects_state", "project_state") and SC:
            col = "verdict_base" if kind == "ref" else "verdict_projects"
            try:
                v = str(s[SC[col]]).strip().upper()
            except Exception:
                v = ""
            if v in ("PASS", "FAIL"):
                return ("scored %s" % v) if kind == "ref" else ("compared in this run: %s" % v)
        return "not recorded"
    return "not in this run"


def _notrun_fallback(z4, ctx, t, fid):
    """A fault run t DID compare: its not-compared cells say so, with the
       verdict, instead of 'not in this run'."""
    s = ctx["summ"][t].get(fid)
    if s is None:
        return None
    NC = dict((c, i) for i, c in enumerate(z4._NOTRUN_COLS))
    SC = ctx["SC"]
    row = ["-"] * len(z4._NOTRUN_COLS)
    for c in ("run_setting", "dyr_edits", "project_output", "project",
              "planning_event", "fault_source", "description"):
        if c in NC and c in SC:
            row[NC[c]] = s[SC[c]]
    row[NC["fault"]] = fid
    if "projects_state" in NC:
        row[NC["projects_state"]] = ("compared in this run: %s (%s)"
                                     % (s[SC["verdict_projects"]], s[SC["classification"]]))
    return row


def _sbs_faults(z4, ref, group, tags, rk, ctx=None, lay=None):
    """Sheet 1: one row per fault -- each reference's verdict, then each
       scenario's verdict, class, worst criterion, both values, limit, buses."""
    lay = lay or (ctx or {}).get("lay") or _lay1(ref, group, tags, rk)
    SC = dict((c, i) for i, c in enumerate(z4._SUMMARY_COLS))
    per, order, head, hb = {}, [], {}, {}
    for g, t in zip(group, tags):
        for r in g["summary"]:
            fid = str(r[SC["fault"]]).strip()
            if fid not in per:
                per[fid] = {}
                order.append(fid)
                head[fid] = {}
                hb[fid] = dict((x, {}) for x in lay["refs"])
            per[fid][t] = r
            for c in ("planning_event", "fault_source", "description"):
                _merge_add(head[fid], c, t, r[SC[c]], z4)
            _merge_add(hb[fid][lay["rk_of"][t]], "verdict_base", t, r[SC["verdict_base"]], z4)
    proj = group[0]["proj"]
    # ATTRIBUTE-MAJOR: verdict | base, studied, gia ... then class | studied,
    # gia ... -- the numbers to be compared sit in ADJACENT columns.
    _w = {"verdict": 11, "class": 14, "worst_criterion": 24, "value": 10, "limit": 8,
          "unit": 6, "past_limit": 10, "new_criteria": 18, "violating_buses": 40, "cause": 50}
    # THE NUMBERS FIRST: verdicts, class, worst criterion, values, limit; the
    # event, source and description last. project / reference / unit dropped
    # -- the folder names the project, the Key sheet the references, the
    # limit its unit.
    header = ["fault"]
    widths = [8]
    for x in lay["refs"]:
        header.append("verdict | %s" % x); widths.append(11)
    for tt in lay["tests"]:
        header.append("verdict | %s" % tt); widths.append(11)
    header += ["worst_across_scenarios"]; widths += [22]
    _attrs = [("class", "classification"), ("worst_criterion", "worst_criterion"),
              ("value", None), ("limit", "limit"), ("past_limit", "past_limit"),
              ("new_criteria", "new_criteria"), ("violating_buses", "violating_buses"),
              ("cause", "cause")]
    for nm, _c in _attrs:
        if nm == "value":
            for x in lay["refs"]:
                header.append("value | %s" % x); widths.append(_w[nm])
        for t in tags:
            header.append("%s | %s" % (nm, t)); widths.append(_w[nm])
    header += ["planning_event", "fault_source", "description"]; widths += [12, 9, 60]
    rows = []
    for fid in sorted(order, key=lambda x: (len(x), x)):
        h = head[fid]
        row = [fid]
        for x in lay["refs"]:
            if lay["multi"] and not any(t in per[fid] for t in lay["of_ref"].get(x, [])):
                row.append("not in this run")
            else:
                row.append(_merge_get(hb[fid][x], "verdict_base", z4))
        worst, act, allpass = "", False, True
        cells = {}
        for t in tags:
            r = per[fid].get(t)
            cells[t] = r
            if r is None:
                allpass = False
                continue
            cls = str(r[SC["classification"]])
            if str(r[SC["action"]]).startswith("ACT"):
                act = True
                worst = "%s: %s" % (t, cls)
            elif cls == z4.CLS_PRE and not act:
                worst = "%s: %s" % (t, cls)
            if str(r[SC["verdict_projects"]]).upper() != "PASS":
                allpass = False
        for tt in lay["tests"]:
            row.append(_per_test(z4, [(t, cells[t][SC["verdict_projects"]] if cells[t] is not None
                                       else None, True) for t in lay["of_test"][tt]]))
        row.append(worst or ("all PASS" if allpass else "mixed / see element sheet"))
        # EACH REFERENCE'S VALUE OF ITS RUNS' WORST CRITERION. One number when
        # the runs' worst criteria agree; every distinct one, named by run,
        # when they do not -- a pu value is not the base of an MW value.
        for nm, c in _attrs:
            if nm == "value":
                for x in lay["refs"]:
                    _bv = {}
                    for t in lay["of_ref"].get(x, []):
                        if cells[t] is not None:
                            _merge_add(_bv, "v", t, cells[t][SC["base_value"]], z4)
                    row.append(_merge_get(_bv, "v", z4, default="-"))
                for t in tags:
                    row.append(cells[t][SC["project_value"]] if cells[t] is not None else "not in this run")
                continue
            for t in tags:
                row.append(cells[t][SC[c]] if cells[t] is not None else "-")
        row += [_merge_get(h, "planning_event", z4), _merge_get(h, "fault_source", z4),
                _merge_get(h, "description", z4)]
        rows.append([("-" if (v is None or str(v).strip() == "") else v) for v in row])
    nw = header.index("worst_across_scenarios")

    def _style(row):
        w = str(row[nw])
        if w.endswith(": " + z4.CLS_NEW):
            return 2
        if w.endswith(": " + z4.CLS_PRE):
            return 3
        if w == "all PASS":
            return 5
        return 0
    return header, rows, widths, _style


def _el_rank(z4, cls):
    """How loud an element class is, for 'worst across scenarios'."""
    c = str(cls)
    if c == z4.CLS_NEW:
        return 3
    if c == z4.CLS_PRE:
        return 2
    if ("over the limit" in c or "under the limit" in c or c.startswith("TRIPPED")
            or c.startswith("swing above")):
        return 1
    if c in (z4.CLS_EL_OK, "", "within limit here (measured)", "not measured in this run",
             "connected in this run (measured)", "no numeric value for this criterion",
             getattr(z4, "CLS_EL_UNKNOWN_T", "\0"), z4.CLS_EL_UNKNOWN):
        return 0
    return 1


def _rounded3(res):
    """A sheet builder's (header, rows, widths, style) with every float cell
       rounded to 3 decimals; rows may be a generator and stay one."""
    header, rows, widths, style = res

    def _r(v):
        return round(v, 3) if isinstance(v, float) else v
    return header, ([_r(v) for v in row] for row in rows), widths, style


_ANG_CACHE = {}


def _angle_ratios(z4, folder, proj):
    """{(fault, bus): [(deviation, SPPR1, SPPR5)]} from ONE folder's
       SPP_MEASURE_ANGLES -- read once per folder, a few columns only."""
    key = (folder, proj)
    if key in _ANG_CACHE:
        return _ANG_CACHE[key]
    out = {}
    try:
        ap = z4.rfile(folder, "SPP_MEASURE_ANGLES", "csv", proj) if folder else None
        if ap:
            with z4.csv_open(ap) as fh:
                rd = csv.reader(fh)
                H = dict((h.strip(), i) for i, h in enumerate(next(rd, None) or []))
                i_sc, i_b, i_d = H.get("Scenario"), H.get("Bus"), H.get("Deviation (deg)")
                i_1, i_5 = H.get("SPPR1"), H.get("SPPR5")
                if None not in (i_sc, i_b, i_d):
                    for r in rd:
                        try:
                            k = (r[i_sc].strip(), r[i_b].strip().split(".")[0])
                            d = _num(r[i_d])
                            s1 = _num(r[i_1]) if i_1 is not None else None
                            s5 = _num(r[i_5]) if i_5 is not None else None
                        except IndexError:
                            continue
                        if d is not None:
                            out.setdefault(k, []).append((d, s1, s5))
    except Exception as e:
        print("[pair]   SPPR not read from %s (%s)" % (folder, e))
    _ANG_CACHE[key] = out
    return out


def _sppr_text(z4, folder, proj, fid, el, bus, value):
    """'SPPR1 0.803 / SPPR5 1.015' of the machine on this row in that folder --
       of the unit whose swing matches the value shown, when a bus has several."""
    b = _num(bus) if _num(bus) is not None else z4._bus_of_element(el)
    if b is None:
        return "-"
    lst = _angle_ratios(z4, folder, proj).get((str(fid).strip(), str(int(b))))
    if not lst:
        return "-"
    v = _num(value)
    d, s1, s5 = (min(lst, key=lambda x: abs(x[0] - v)) if v is not None
                 else max(lst, key=lambda x: x[0]))
    if s1 is None and s5 is None:
        return "-"
    f = lambda x: "-" if x is None else "%.3f" % x
    return "SPPR1 %s / SPPR5 %s" % (f(s1), f(s5))


def _band_gap(z4, value):
    """pu outside the steady-state band: negative below it, positive above,
       0 inside."""
    v = _num(value)
    if v is None:
        return "-"
    lo = float(getattr(z4, "V_SS_LOW", 0.90))
    hi = float(getattr(z4, "V_SS_HIGH", 1.10))
    return round(v - lo, 3) if v < lo else (round(v - hi, 3) if v > hi else 0.0)


def _beside(z4, fam, secs, value, folder, proj, k4):
    """The cell beside a value: cycles above 1.20 pu (overvoltage), SPPR1 /
       SPPR5 (rotor angle), pu outside 0.90-1.10 (steady state); '-' else."""
    if fam == "overshoot":
        return _cycles(fam, secs)
    if fam == "angle":
        return _sppr_text(z4, folder, proj, k4[0], k4[2], k4[3], value)
    if fam == "steady":
        return _band_gap(z4, value)
    return "-"


_BESIDE_HDR = "cycles / SPPR / pu out"


def _cycles(fam, secs):
    """Seconds above 1.20 pu as cycles (60 Hz), for an overvoltage row."""
    if fam != "overshoot":
        return "-"
    if isinstance(secs, str) and ":" in secs:
        return secs                     # comparisons disagree -- each named, as the value
    v = _num(secs)
    if v is None:
        return secs if str(secs).strip() not in ("", "None") else "-"
    return round(v * 60.0, 1)


def _pu3(fam, v):
    """A measured pu value to 3 decimals (1.073228359 -> 1.073); text as it is."""
    if fam in ("overshoot", "recovery", "steady") and isinstance(v, float):
        return round(v, 3)
    return v


def _sbs_elements(z4, ref, group, tags, rk, ctx=None, lay=None):
    """Sheet 2: one row per fault x criterion x ELEMENT (bus or machine) --
       the reference value and state, then each scenario's value, state and
       element classification. This is where 'what did bus 531605 do in the
       base, as studied and at SGF capacity' is answered on one line.

       NO BLANKS. A bus that violated in one scenario and not in another has
       no report row in the second; its value there is read from that folder's
       own measurements (the bus is added to the panel's want-list first, so
       the selective read keeps it). What was never measured says so."""
    if ctx is None:
        ctx = _sbs_context(z4, ref, group, tags, rk, lay)
    lay = ctx["lay"]
    RC = ctx["RC"]
    proj = group[0]["proj"]
    # ATTRIBUTE-MAJOR: value | old base, new base, studied, gia ... side by
    # side, then the states, then each comparison's class, change and
    # past-limit -- one glance per row.
    _w = {"value": 11, "state": 22, "class": 26, "change": 9, "past_limit": 10}
    # THE NUMBERS FIRST: key, limit, every value, then change / class /
    # past-limit per comparison, then states; the element's particulars last.
    header = ["fault", "criterion", "element", "bus_number", "limit"]
    widths = [8, 22, 18, 10, 12]
    # EACH VALUE WITH ITS TIME ABOVE 1.20 pu BESIDE IT, in cycles -- "1.24 pu,
    # 3 cycles" read in one glance per scenario (transient overvoltage rows;
    # '-' on every other criterion).
    for x in lay["refs"] + lay["tests"]:
        header.append("value | %s" % x); widths.append(_w["value"])
        header.append("%s | %s" % (_BESIDE_HDR, x)); widths.append(14)
    header.append("worst_across_scenarios"); widths.append(26)
    for nm in ("change", "class", "past_limit"):
        for t in tags:
            header.append("%s | %s" % (nm, t)); widths.append(_w[nm])
    for x in lay["refs"] + lay["tests"]:
        header.append("state | %s" % x); widths.append(_w["state"])
    header += ["measured", "planning_event", "fault_source", "area", "hops_from_fault",
               "hops_from_poi"]
    widths += [15, 12, 9, 6, 8, 8]
    for x in lay["refs"]:
        header.append("note | %s" % x); widths.append(30)
    header.append("description"); widths.append(40)
    rows = []

    # EACH FAULT'S VIOLATIONS PER CRITERION, NEAREST THE POI FIRST: node 1,
    # then node 2 ... then buses the map has no path to.
    def _fkey(k):
        f = k[0]
        m = re.match(r"^([A-Za-z]*)(\d+)$", f)
        hp = _poi_hops(z4, ctx["shared"].get(k, {}))
        return ((m.group(1), int(m.group(2))) if m else ("~", 0), f, k[1], hp, k[2], k[3])
    for k in sorted(ctx["order"], key=_fkey):
        fid, crit, el, bus = k
        s = ctx["shared"].get(k, {})
        bs = [ctx["base"][x].get(k) or {} for x in lay["refs"]]
        fam = z4._criterion_family(crit)
        limit = _merge_one(s, "limit")            # to judge with
        row = [fid, crit, ctx["label"].get(k, el), bus, _merge_get(s, "limit", z4)]
        got, own = {}, {}                         # comparison -> [value, state, class, change, past]
        above = {}                                # comparison -> seconds above 1.20 pu
        worst, rank = "", -1
        for t in tags:
            r = ctx["det"][t].get(k)
            f = ctx["fills"].get((lay["tt_of"][t],) + k)
            own[t] = r is not None
            a = r[RC["secs_above_1_20_project"]] if (r is not None and "secs_above_1_20_project" in RC) else None
            if (a is None or _empty(z4, a)) and f:
                a = f[2]
            above[t] = a
            if r is None and not crit:
                cells = ["-", "-", "-", "-", "-"]
                cls = "-"
            elif r is None:
                # NOT IN THIS SCENARIO'S REPORT: what that run measured.
                val, state = (f[0], f[1]) if f else (
                    "-", "not measured in this run" if fam in _MEASURABLE
                    else "no numeric value for this criterion")
                cls = _measured_class(fam, val, state, limit)
                v, b0, lim = (_num(val) if val != "-" else None), _base_num(z4, ctx, k, t), _num(limit)
                chg = _fmt(v - b0, fam) if (v is not None and b0 is not None and fam != "trip") else "-"
                past = _fmt(v - lim, fam) if (v is not None and lim is not None) else "-"
                cells = [val, state, cls, chg, past]
            else:
                cells = [r[RC[c]] for _nm, c in _SBS_EL]
                cls = str(r[RC["element_classification"]])
                if f:
                    if _empty(z4, cells[0]):
                        cells[0] = f[0]
                    if _empty(z4, cells[1]):
                        cells[1] = f[1]
                if _empty(z4, cells[3]):
                    cells[3] = _change(z4, ctx, k, t, cells[0], fam)
            got[t] = cells
            rk2 = _el_rank(z4, cls)
            if rk2 > rank:
                rank, worst = rk2, ("%s: %s" % (t, cls) if rk2 > 0 else cls)
        for x, b in zip(lay["refs"], bs):
            _v = _pu3(fam, _merge_get(b, "base_value", z4, default="-"))
            row.append(_v)
            row.append(_beside(z4, fam, _merge_get(b, "secs_above_1_20_base", z4, default="-"),
                               _v, lay["ref_dir"].get(x), proj, k))
        for tt in lay["tests"]:
            _v = _pu3(fam, _per_test(z4, [(t, got[t][0], own[t]) for t in lay["of_test"][tt]]))
            row.append(_v)
            row.append(_beside(z4, fam, _per_test(z4, [(t, above[t], own[t])
                                                       for t in lay["of_test"][tt]]),
                               _v, lay["test_dir"].get(tt), proj, k))
        row.append(worst or "-")
        for ix in (3, 2, 4):                      # change, class, past_limit
            for t in tags:
                row.append(got[t][ix])
        for b in bs:
            row.append(_merge_get(b, "base_state", z4, default="-"))
        for tt in lay["tests"]:
            row.append(_per_test(z4, [(t, got[t][1], own[t]) for t in lay["of_test"][tt]]))
        row += [_merge_get(s, "measured", z4), _merge_get(s, "planning_event", z4),
                _merge_get(s, "fault_source", z4), _merge_get(s, "area", z4),
                _merge_get(s, "hops_from_fault", z4), _merge_get(s, "hops_from_poi", z4)]
        for b in bs:
            row.append(_merge_get(b, "base_value_note", z4, default="-"))
        row.append(_merge_get(s, "description", z4))
        rows.append([("-" if (v is None or str(v).strip() == "") else v) for v in row])
    nw = header.index("worst_across_scenarios")

    def _style(row):
        w = str(row[nw])
        if w.endswith(": " + z4.CLS_NEW):
            return 2
        if w.endswith(": " + z4.CLS_PRE):
            return 3
        if w in (z4.CLS_EL_OK, "within limit here (measured)"):
            return 5
        return 0
    return header, rows, widths, _style


# Columns that describe the fault / element or the REFERENCE side -- the same
# in every run, so they appear once. Everything else is per run and is laid
# out attribute-major: attr | run1, attr | run2 ... in adjacent columns.
_SHARED = set(["project", "planning_event", "fault_source", "area", "hops_from_fault",
               "hops_from_poi", "limit", "unit", "description", "POI", "measured",
               "criterion", "element", "bus_number"])
_KEYS = {"summary": ["fault"], "notrun": ["fault"], "poi": ["fault"],
         "new_el": ["fault", "criterion", "element", "bus_number"],
         "pre_el": ["fault", "criterion", "element", "bus_number"],
         "detail": ["fault", "criterion", "element", "bus_number"]}
# per-run cells a run's measurements can fill when its report has no number
_RUN_FILL = {"project_value": 0, "project_state": 1,
             "secs_above_1_20_project": 2, "above_1.20_project_s": 2}


def _is_shared(c):
    c = str(c)
    return c in _SHARED or _is_base_col(c)


def _is_base_col(c):
    """A column that holds the REFERENCE's own value: one per base."""
    c = str(c)
    return c.startswith("base_") or c.startswith("base ") or c == "verdict_base" \
        or c.startswith("above_1.20_base") or c.startswith("secs_above_1_20_base")


# per-run columns that describe the RUN'S OWN FOLDER, not a comparison: with
# two bases they sit once per run, not once per run and base
_TEST_COLS = set(["run_setting", "dyr_edits", "project_output"])


def _class_style(z4, vals):
    """Red when any run's class is NEW / 'PROJECT introduced', amber when any
       is PRE-EXISTING / 'pre-existing (both cases)' -- the detail sheets and
       the compact sheets word the same class differently."""
    new = pre = False
    for v in vals:
        u = str(v).strip().upper()
        if u == str(z4.CLS_NEW).upper() or u.startswith("PROJECT INTRODUCED"):
            new = True
        elif u.startswith(str(z4.CLS_PRE).upper()):
            pre = True
    return 2 if new else (3 if pre else None)


def _wide_sheet(z4, group, tags, key, cols, rk, ctx=None, lay=None):
    """One panel sheet, every run side by side: the key and the shared /
       reference columns once, then each per-run column repeated per run,
       adjacent, with the reference's own column right before its runs'.

       NOTHING LEFT OUT. A violation listed for one run and not another is
       still answered for the other: sheets 5 and 6 show that element's class
       and values in the other run's own report; sheet 8 shows the run's
       measured value, judged against the limit; sheet 7 says the fault WAS
       compared there, with its verdict. 'not in this run' is left only where
       the run truly has nothing -- a fault it never ran.

       TWO BASES: every base column once per base, every value a run measured
       once per run, and every comparison column (class, change, who caused
       it ...) once per run and base."""
    lay = lay or (ctx or {}).get("lay") or _lay1(None, group, tags, rk)
    ix = dict((c, i) for i, c in enumerate(cols))
    keys = _KEYS.get(key, ["fault"])
    shared = [c for c in cols if c not in keys and _is_shared(c)]
    perrun = [c for c in cols if c not in keys and not _is_shared(c)]
    per, order, merged, mref, wl = {}, [], {}, {}, {}
    el4 = (len(keys) == 4 and "element" in ix)

    def _vs(r):
        return sum(1 for c in ("project_value", "base_value")
                   if c in ix and not _empty(z4, r[ix[c]]))
    for g, t in zip(group, tags):
        x = lay["rk_of"][t]
        cnt, spell = {}, {}
        for r in (g.get(key) or []):
            r = list(r) + [z4.EMPTY_CELL] * (len(cols) - len(r))
            if el4:
                # ONE ROW PER VIOLATION: another spelling ("POI 765911") of an
                # element this run already listed joins that row.
                k0 = _key4(r[ix[c]] for c in keys)
                lab = str(r[ix["element"]]).strip()
                wl[k0] = _better_label(wl.get(k0), lab)
                sp = spell.setdefault(k0, [])
                if sp and lab not in sp:
                    sp.append(lab)
                    k = k0 + (1,)
                    if _vs(r) > _vs(per[k][t]):
                        per[k][t] = r
                    for c in shared:
                        st = mref[k][x] if _is_base_col(c) else merged[k]
                        if c not in st:
                            _merge_add(st, c, t, r[ix[c]], z4)
                    continue
                sp.append(lab)
            else:
                k0 = tuple(str(r[ix[c]]).strip() for c in keys)
            # A KEY THAT REPEATS WITHIN ONE RUN keeps every row: the n-th row
            # of a run pairs with the n-th row of the others.
            n = cnt.get(k0, 0) + 1
            cnt[k0] = n
            k = k0 + (n,)
            if k not in per:
                per[k] = {}
                order.append(k)
                merged[k] = {}
                mref[k] = dict((y, {}) for y in lay["refs"])
            per[k][t] = r
            for c in shared:
                if _is_base_col(c):
                    _merge_add(mref[k][x], c, t, r[ix[c]], z4)
                else:
                    _merge_add(merged[k], c, t, r[ix[c]], z4)
    element_sheet = (len(keys) == 4 and ctx is not None)
    # EACH REFERENCE'S OWN NUMBERS WHERE NONE OF ITS PAIRS HAD ONE, from its
    # measurements.
    if element_sheet:
        _bmap = {"base_value": "base_value", "base_state": "base_state",
                 "base_value_note": "base_value_note",
                 "secs_above_1_20_base": "secs_above_1_20_base",
                 "above_1.20_base_s": "secs_above_1_20_base"}
        for k in order:
            for y in lay["refs"]:
                b = ctx["base"][y].get(k[:4])
                if not b:
                    continue
                for c, src in _bmap.items():
                    if c in ix and c not in mref[k][y] and src in b:
                        mref[k][y][c] = b[src]
    fallback = None
    if ctx is not None:
        if key == "detail":
            fallback = lambda t, k: _detail_fallback(z4, ctx, t, k[:4])
        elif key in ("new_el", "pre_el"):
            def fallback(t, k):
                r0 = ctx["det"][t].get(k[:4])
                if r0 is not None:
                    try:
                        return z4._compact_view([r0])[0]
                    except Exception:
                        return None
                return _compact_fallback(z4, ctx, t, k[:4])
        elif key == "notrun":
            fallback = lambda t, k: _notrun_fallback(z4, ctx, t, k[0])

    # THE BASE COLUMN SITS RIGHT BEFORE ITS OWN PROJECT COLUMNS: base_value |
    # base, project_value | studied, project_value | gia ... so the numbers to
    # be compared -- a bus voltage, a rotor angle, a machine's MW -- are read
    # across one stretch of cells. A base column with no project counterpart
    # (base_value_note) stays with the fault's own columns up front.
    def _base_of(c):
        for a, b in (("verdict_projects", "verdict_base"), ("projects_state", "base_state"),
                     ("project_state", "base_state"), ("project_value", "base_value"),
                     ("above_1.20_project_s", "above_1.20_base_s"),
                     ("secs_above_1_20_project", "secs_above_1_20_base")):
            if c == a and b in ix:
                return b
        if c.startswith("project ") and c.replace("project ", "base ", 1) in ix:
            return c.replace("project ", "base ", 1)
        return None
    paired = set(b for b in (_base_of(c) for c in perrun) if b)
    drop = set(c for c in _DROP if c != "unit" or "limit" in ix)
    front = [c for c in shared if c not in paired and c not in drop]
    # (rank, header, kind, column, tag): the compared numbers right after the
    # key, everything descriptive at the end -- see _ORDER.
    spec = []
    for c in front:
        if _is_base_col(c):
            for y in lay["refs"]:
                spec.append((_rank_col(c, el4), ("%s | %s" % (c, y)) if (lay["multi"] or c.startswith("base")
                                                                 or c == "verdict_base") else c,
                             "front", c, y))
        else:
            spec.append((_rank_col(c, el4), c, "front", c, None))
    for c in perrun:
        if c in drop:
            continue
        b = _base_of(c)
        if b:
            for y in lay["refs"]:
                spec.append((_rank_col(c, el4), "%s | %s" % (b, y), "ref", b, y))
        if b or c in _TEST_COLS:
            for tt in lay["tests"]:
                spec.append((_rank_col(c, el4), "%s | %s" % (c, tt), "test", c, tt))
        else:
            for t in tags:
                spec.append((_rank_col(c, el4), "%s | %s" % (c, t), "pair", c, t))
    spec = [sp[1:] for _i, sp in sorted(enumerate(spec), key=lambda e: (e[1][0], e[0]))]
    # THE TIME ABOVE 1.20 pu RIGHT AFTER ITS OWN VALUE, in cycles: base_value |
    # BASE, cycles_above_1.20 | BASE, base_value | BASE_EGF_OFF, cycles ... then
    # each project value with its cycles -- not seconds at the far end.
    _secs_of = {"base_value": ("secs_above_1_20_base", "above_1.20_base_s"),
                "project_value": ("secs_above_1_20_project", "above_1.20_project_s")}
    _is_secs = set(x for v in _secs_of.values() for x in v)
    cyc_pos = set()
    if el4 and "criterion" in ix:
        _rest = [sp for sp in spec if sp[2] not in _is_secs]
        _secs = [sp for sp in spec if sp[2] in _is_secs]
        spec = []
        for sp in _rest:
            spec.append(sp)
            for sc in _secs:
                if sc[2] in _secs_of.get(sp[2], ()) and sc[3] == sp[3]:
                    cyc_pos.add(len(spec))
                    spec.append(("%s | %s" % (_BESIDE_HDR, sp[3]) if sp[3] else
                                 _BESIDE_HDR, sc[1], sc[2], sc[3]))
        for sc in _secs:                          # a seconds column with no value beside it
            if not any(sp[2] == sc[2] and sp[3] == sc[3] for sp in spec):
                spec.append(sc)
    header = list(keys) + [h for h, _kd, _c, _t in spec]
    widths = [10] * len(keys) + [14] * len(spec)
    ix_crit = keys.index("criterion") if "criterion" in keys else 0
    _proj = (group[0].get("proj") if group else "") or ""
    cls_cols = [i for i, hh in enumerate(header)
                if hh.split(" | ")[0] in ("classification", "element_classification",
                                          "fault_classification", "who_caused_it")]

    def _k(k):
        m = re.match(r"^([A-Za-z]*)(\d+)$", k[0])
        fk = ((m.group(1), int(m.group(2))) if m else ("~", 0),)
        if el4 and "hops_from_poi" in ix:
            # per fault and criterion, nearest the POI first
            hp = _poi_hops(z4, merged[k])
            return fk + (k[0], k[1], hp) + tuple(k[2:])
        return fk + tuple(k)

    def _rows():
        for k in sorted(order, key=_k):
            mk, mr = merged[k], mref[k]
            got, own = {}, {}
            for t in tags:
                r = per[k].get(t)
                own[t] = r is not None
                if r is None and fallback is not None:
                    try:
                        r = fallback(t, k)
                    except Exception:
                        r = None
                    if r is not None:
                        r = list(r) + [z4.EMPTY_CELL] * (len(cols) - len(r))
                got[t] = r

            def _val(t, c):
                r = got[t]
                if r is None:
                    return None                   # no row at all: 'not in this run'
                v = r[ix[c]]
                if v is None:
                    v = ""                        # a row with an empty cell: n/a
                if element_sheet and c in _RUN_FILL and _empty(z4, v):
                    f = ctx["fills"].get((lay["tt_of"][t],) + tuple(k[:4]))
                    if f and not (f[_RUN_FILL[c]] in ("-", "")):
                        v = f[_RUN_FILL[c]]
                if element_sheet and c == "change" and _empty(z4, v) and "project_value" in ix:
                    v = _change(z4, ctx, tuple(k[:4]), t, _val(t, "project_value"),
                                z4._criterion_family(k[1]), default=v)
                return v
            row = list(k[:len(keys)])
            if el4:
                row[2] = wl.get(tuple(k[:4]), row[2])
            _fam = z4._criterion_family(str(k[ix_crit])) if cyc_pos else ""
            for _si, (_h, kind, c, t) in enumerate(spec):
                if kind == "front":
                    row.append(_merge_get(mk if t is None else mr[t], c, z4))
                elif kind == "ref":
                    if (lay["multi"] and c not in mr[t]
                            and not any(p in per[k] for p in lay["of_ref"].get(t, []))):
                        row.append(_absent_cell(z4, ctx, lay, "ref", t, k[0], c))
                    else:
                        row.append(_merge_get(mr[t], c, z4))
                elif kind == "pair":
                    v = _val(t, c)
                    row.append(_absent_cell(z4, ctx, lay, "pair", t, k[0], c) if v is None else v)
                else:
                    row.append(_per_test(z4, [(p, _val(p, c), own[p]) for p in lay["of_test"][t]]))
                if _si in cyc_pos:
                    _fold = (lay["ref_dir"].get(t) if kind == "ref" else
                             lay.get("test_dir", {}).get(t))
                    row[-1] = _beside(z4, _fam, row[-1], row[-2], _fold, _proj,
                                      tuple(k[:4]) if el4 else (k[0], "", "", ""))
            yield [(z4.EMPTY_CELL if v in ("", None) else v) for v in row]

    def _style(row):
        return _class_style(z4, [row[i] for i in cls_cols])
    return header, _rows(), widths, _style


def _sbs_poi(z4, ref, group, tags, rk, lay=None):
    """POI power per fault, wide, the reference's column right before the
       runs' for every quantity the reference has: total P0 | base, studied,
       gia; total end | base, studied, gia; ... then the runs' new-plant and
       existing MW, reactive power and post-clearing minimum. Two bases: one
       column per base, then one per run -- a run's POI power is its own,
       whichever base it is compared with."""
    lay = lay or _lay1(ref, group, tags, rk)
    PC = dict((c, i) for i, c in enumerate(z4._POI_COLS))
    per, order, head, hb = {}, [], {}, {}
    for g, t in zip(group, tags):
        for r in (g.get("poi") or []):
            fid = str(r[PC["fault"]]).strip()
            if fid not in per:
                per[fid] = {}
                order.append(fid)
                head[fid] = {}
                hb[fid] = dict((y, {}) for y in lay["refs"])
            per[fid][t] = r
            if "POI" in PC:
                _merge_add(head[fid], "POI", t, r[PC["POI"]], z4)
            for c in ("base total P0 (MW)", "base total end (MW)", "base total Q0 (MVAr)",
                      "base total Q end (MVAr)", "base min P after clearing (MW)"):
                if c in PC:
                    _merge_add(hb[fid][lay["rk_of"][t]], c, t, r[PC[c]], z4)
    proj = group[0]["proj"]
    blocks = [("total P0 (MW)", "base total P0 (MW)", "project total P0 (MW)", 14),
              ("total end (MW)", "base total end (MW)", "project total end (MW)", 14),
              ("new plant P0 (MW)", None, "project new plant P0 (MW)", 16),
              ("new plant end (MW)", None, "project new plant end (MW)", 16),
              ("existing P0 (MW)", None, "project existing P0 (MW)", 16),
              ("existing end (MW)", None, "project existing end (MW)", 16),
              ("total Q0 (MVAr)", "base total Q0 (MVAr)", "project total Q0 (MVAr)", 14),
              ("total Q end (MVAr)", "base total Q end (MVAr)", "project total Q end (MVAr)", 14),
              ("min P after clearing (MW)", "base min P after clearing (MW)",
               "project min P after clearing (MW)", 18),
              ("how measured", None, "how the project total was measured", 40),
              # THE OVER-DELIVERY CHECK (POI_P_END_OVER_MW), last so nothing moves
              ("end minus pre-fault (MW)", None, "project end minus pre-fault (MW)", 14),
              ("ends above pre-fault", None, "project ends above pre-fault", 34)]
    blocks = [b for b in blocks if b[2] in PC]
    header, widths = ["fault", "project", "POI"], [8, 12, 9]
    for name, bcol, _pcol, w in blocks:
        if bcol and bcol in PC:
            for y in lay["refs"]:
                header.append("%s | %s" % (name, y)); widths.append(16)
        for tt in lay["tests"]:
            header.append("%s | %s" % (name, tt)); widths.append(w)
    rows = []
    for fid in sorted(order, key=lambda x: (len(x), x)):
        h = head[fid]
        row = [fid, proj, _merge_get(h, "POI", z4, default="-")]
        for name, bcol, pcol, _w in blocks:
            if bcol and bcol in PC:
                for y in lay["refs"]:
                    row.append(_merge_get(hb[fid][y], bcol, z4, default="-"))
            for tt in lay["tests"]:
                vals = []
                for t in lay["of_test"][tt]:
                    r = per[fid].get(t)
                    v = None if r is None else ("" if r[PC[pcol]] is None else r[PC[pcol]])
                    vals.append((t, v, True))
                row.append(_per_test(z4, vals))
        rows.append([("-" if (v is None or str(v).strip() == "") else v) for v in row])
    _p0 = dict((t, header.index("total P0 (MW) | %s" % t)) for t in lay["tests"]
               if ("total P0 (MW) | %s" % t) in header)
    _pe = dict((t, header.index("total end (MW) | %s" % t)) for t in lay["tests"]
               if ("total end (MW) | %s" % t) in header)

    _ov = [header.index("ends above pre-fault | %s" % t) for t in lay["tests"]
           if ("ends above pre-fault | %s" % t) in header]

    def _style(row):
        # red when a scenario's POI total ENDS above pre-fault (POI_P_END_OVER_MW)
        for i in _ov:
            try:
                if "YES" in str(row[i]):
                    return 2
            except IndexError:
                pass
        # red when any scenario's POI total did not come back to 90 % of P0
        for t in lay["tests"]:
            try:
                p0, pe = float(row[_p0[t]]), float(row[_pe[t]])
            except (KeyError, TypeError, ValueError, IndexError):
                continue
            if p0 > 1.0 and pe < 0.9 * p0:
                return 2
        return None
    return header, rows, widths, _style


def _sbs_runs(z4, ref, group, tags, rk, lay=None):
    """One line per base and per comparison: where it is, how much it holds,
       how it compared."""
    lay = lay or _lay1(ref, group, tags, rk)
    header = ["tag", "role", "folder", ".out files", "with a verdict", "faults compared",
              "NEW", "pre-existing", "only in this run", "only in the reference",
              "pair report"]
    widths = [16, 10, 70, 10, 12, 14, 8, 12, 40, 40, 70]
    rows = []
    for y in lay["refs"]:
        g0 = [g for g, t in zip(group, tags) if lay["rk_of"][t] == y][0]
        rows.append([y, "reference", lay["ref_dir"][y], g0.get("n_out_ref", "-"),
                     g0.get("n_scored_ref", "-"), "-", "-", "-", "-", "-", "-"])
    for g, t in zip(group, tags):
        rows.append([t, ("scenario vs %s" % lay["rk_of"][t]) if lay["multi"] else "scenario",
                     g["test"], g.get("n_out", "-"), g.get("n_scored", "-"),
                     g["faults"], g["new"], g["pre"],
                     ", ".join(g.get("only_test") or []) or "-",
                     ", ".join(g.get("only_ref") or []) or "-",
                     g.get("report") or g.get("folder") or "-"])
    return header, rows, widths, None


# ---- the workbook, one sheet at a time, streamed ------------------------------
_NUMRE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def _cellval(v):
    """A number stays a number: '1.234' is written as 1.234, so a value column
       sorts and filters numerically. Ids, words and phrases stay text."""
    if isinstance(v, str):
        s = v.strip()
        if s and len(s) <= 15 and _NUMRE.match(s):
            try:
                return float(s) if "." in s else int(s)
            except ValueError:
                return v
    return v


def _stream_sheet(z4, fh, header, rows, widths, style_of, first):
    """One worksheet written straight to a file, row by row -- the panel's
       layout (coloured header, frozen top row, filter on every column) with
       no sheet ever held whole in memory. Returns the number of data rows."""
    ncol = len(header)
    fh.write(('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<sheetViews><sheetView workbookViewId="0"%s>'
              '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
              '</sheetView></sheetViews>' % (' tabSelected="1"' if first else "")).encode("utf-8"))
    if widths:
        fh.write(("<cols>" + "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>'
                                     % (i + 1, i + 1, w) for i, w in enumerate(widths[:ncol]))
                  + "</cols>").encode("utf-8"))
    fh.write(b"<sheetData>")
    fh.write(('<row r="1">' + "".join(z4._xl_cell(i, 1, h, 1) for i, h in enumerate(header))
              + "</row>").encode("utf-8"))
    n = 1
    for row in rows:
        n += 1
        st = (style_of(row) if style_of else 0) or 0
        fh.write(('<row r="%d">' % n
                  + "".join(z4._xl_cell(i, n, _cellval(row[i] if i < len(row) else ""), st)
                            for i in range(ncol))
                  + "</row>").encode("utf-8"))
    fh.write(b"</sheetData>")
    fh.write(('<autoFilter ref="A1:%s%d"/>' % (z4._xl_col(max(ncol, 1) - 1), n)).encode("utf-8"))
    fh.write(b"</worksheet>")
    return n - 1


def _write_workbook_streamed(z4, path, specs, csv_dir, csv_lab, legend=None, title_rows=None):
    """Every sheet is BUILT, written to the workbook and to its CSV, and
       dropped before the next one is built. specs: [(sheet name, csv stem,
       builder)] -- builder() returns (header, rows, widths, style_of), rows
       a list or a generator. Returns {csv stem: data rows written}."""
    import zipfile, tempfile, gc, csv
    names = [z4._xl_sheet_name(s[0]) for s in specs] + ["Key"]
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"',
          ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">',
          '<sheets>']
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for i, name in enumerate(names, start=1):
        ct.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType='
                  '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i)
        wb.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (z4._xl_esc(name), i, i))
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org'
                    '/officeDocument/2006/relationships/worksheet" Target="worksheets'
                    '/sheet%d.xml"/>' % (i, i))
    ct.append('<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
              'openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>')
    wb.append("</sheets></workbook>")
    rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org'
                '/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                % (len(names) + 1))
    rels.append("</Relationships>")
    counts = {}
    tmp_xlsx = path + ".tmp"
    z = zipfile.ZipFile(tmp_xlsx, "w", zipfile.ZIP_DEFLATED)
    try:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", z4._XL_RELS)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        z.writestr("xl/styles.xml", z4._XL_STYLES)
        for i, (name, stem, build) in enumerate(specs, start=1):
            try:
                header, rows, widths, style_of = build()
                header = z4._disp_hdr(header) if hasattr(z4, "_disp_hdr") else header
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("[pair] *** sheet '%s' could not be built: %s ***" % (name, e))
                header, rows, widths, style_of = (["error"], [["this sheet could not be built: %s" % e]],
                                                  [80], None)
            cp = os.path.join(csv_dir, "SIDE_BY_SIDE_%s_%s.csv" % (stem, csv_lab))
            try:
                cfh = open(cp, "w", newline="", errors="replace")
                cw = csv.writer(cfh)
                cw.writerow([{"hops_from_fault": "nodes_from_fault", "hops_from_poi": "nodes_from_poi"}.get(c, c) for c in header])
            except Exception as e:
                print("[pair] could not write %s: %s" % (cp, e))
                cfh, cw = None, None

            def _tee(rows=rows, cw=cw):
                for r in rows:
                    if cw is not None:
                        try:
                            cw.writerow([("" if v is None else v) for v in r])
                        except Exception:
                            pass
                    yield r
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
            try:
                counts[stem] = _stream_sheet(z4, tmp, header, _tee(), widths, style_of, first=(i == 1))
                tmp.close()
                z.write(tmp.name, "xl/worksheets/sheet%d.xml" % i)
            finally:
                try:
                    tmp.close()
                except Exception:
                    pass
                try:
                    os.remove(tmp.name)
                except Exception:
                    pass
                if cfh is not None:
                    cfh.close()
            del header, rows
            gc.collect()
        z.writestr("xl/worksheets/sheet%d.xml" % len(names),
                   z4._xl_legend_sheet(legend or [], title_rows or []))
    finally:
        z.close()
    # REPLACED IN ONE STEP: a workbook open in Excel cannot be overwritten, and
    # a half-written one must never take the good one's name.
    try:
        if os.path.isfile(path):
            os.remove(path)
        os.rename(tmp_xlsx, path)
    except Exception as e:
        print("[pair] *** could not replace %s (%s) -- is it open in Excel? The new "
              "workbook is at %s ***" % (path, e, tmp_xlsx))
        return counts, tmp_xlsx
    return counts, path


def write_side_by_side(z4, ref, group):
    """ONE WORKBOOK, EVERY SCENARIO THAT SHARES THIS REFERENCE, every sheet with
       the reference and every run in adjacent columns:
         1 Faults          one row per fault: base | studied | GIA verdicts, classes, values
         2 Elements        one row per fault x criterion x bus/machine: the VALUE in
                           the base, as studied, at GIA ... measured where a run's
                           report has no row
         3 POI power       base | runs for every MW / MVAr quantity at the POI
         4 Summary         the panel's summary sheet, wide
         5 Project introduces   the panel's element sheets, wide -- every
         6 Pre-existing         element any run lists, with every run's cells
         7 Not compared    the panel's sheet, wide
         8 All detail      every element row of every run, wide
         9 Runs            which folders, how many faults, where each pair report is
       Built from the same rows the pair workbooks hold, so they never disagree."""
    group = [g for g in group if g.get("summary")]
    if len(group) < 2:
        return None
    group, tags, lay = _layout(group)
    if len(group) < 2:
        return None
    proj = group[0]["proj"]
    rk = lay["refs"][0]
    ref = lay["ref_dir"][rk]
    out_root = OUT_DIR or os.path.join(z4.STUDY_ROOT, "comparison_pairs")
    lab = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                 (os.environ.get("CMP_MULTI_SBS_NAME") or "").replace("{proj}", proj)
                 or "%s_SIDE_BY_SIDE_vs_%s" % (proj, "_and_".join(lay["refs"])))
    d = os.path.join(out_root, proj, lab)          # beside that project's pair reports
    if not os.path.isdir(d):
        os.makedirs(d)
    xp = os.path.join(d, "00_SIDE_BY_SIDE_%s.xlsx" % lab)
    title = ["%s -- every scenario against %s" % (proj, " and ".join(lay["ref_dir"][y] for y in lay["refs"]))]
    for y in lay["refs"]:
        title.append("reference (%s) = %s" % (y, lay["ref_dir"][y]))
    for tt in lay["tests"]:
        title.append("%s = %s" % (tt, lay["test_dir"][tt]))
    if lay["multi"]:
        title.append("values: one column per base and per run.  class / change / who caused it: one column "
                     "per run AND base ('studied vs base') -- each run is judged against each base")
    title.append("generated %s" % time.strftime("%Y-%m-%d %H:%M"))
    ctx = _sbs_context(z4, ref, group, tags, rk, lay)
    specs = [("1 Faults", "FAULTS", lambda: _sbs_faults(z4, ref, group, tags, rk, ctx)),
             ("2 Elements", "ELEMENTS", lambda: _sbs_elements(z4, ref, group, tags, rk, ctx)),
             ("3 POI power", "POI", lambda: _sbs_poi(z4, ref, group, tags, rk, lay)),
             ("4 Summary", "SUMMARY",
              lambda: _wide_sheet(z4, group, tags, "summary", z4._SUMMARY_COLS, rk, ctx)),
             ("5 Project introduces", "INTRODUCES",
              lambda: _wide_sheet(z4, group, tags, "new_el", z4._COMPACT_COLS, rk, ctx)),
             ("6 Pre-existing", "PREEXISTING",
              lambda: _wide_sheet(z4, group, tags, "pre_el", z4._COMPACT_COLS, rk, ctx)),
             ("7 Not compared", "NOTCOMPARED",
              lambda: _wide_sheet(z4, group, tags, "notrun", z4._NOTRUN_COLS, rk, ctx)),
             ("8 All detail", "DETAIL",
              lambda: _wide_sheet(z4, group, tags, "detail", z4._REPORT_COLS, rk, ctx)),
             ("9 Runs", "RUNS", lambda: _sbs_runs(z4, ref, group, tags, rk, lay))]
    # EVERY DECIMAL TO 3 PLACES (1.07322835922 -> 1.073), on every sheet.
    specs = [(nm, key, (lambda fn=fn: _rounded3(fn()))) for nm, key, fn in specs]
    counts, xp = _write_workbook_streamed(z4, xp, specs, d, lab, legend=z4._XL_LEGEND,
                                          title_rows=title)
    write_rerun_list(z4, os.path.join(d, "RERUN_%s.txt" % proj),
                     [(y, lay["ref_dir"][y], proj) for y in lay["refs"]] +
                     [(tt, lay["test_dir"][tt], proj) for tt in lay["tests"]])
    print("[pair] side by side (%s): %s -- every run in adjacent columns -> %s"
          % (" | ".join(lay["refs"] + lay["tests"]),
             ", ".join("%d %s" % (counts.get(s, 0), s.lower())
                       for s in ("FAULTS", "ELEMENTS", "POI", "SUMMARY", "INTRODUCES",
                                 "PREEXISTING", "NOTCOMPARED", "DETAIL")),
             xp))
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


def _ref_list():
    """REFERENCE as a list: one folder as a string, or several in a list."""
    r = REFERENCE
    if isinstance(r, (list, tuple)):
        return [x for x in r if x and str(x).strip()]
    return [r] if r and str(r).strip() else []


def _pairs_from_settings(argv):
    pairs = []
    if len(argv) >= 3:
        pairs.append((argv[1], argv[2], argv[3] if len(argv) > 3 else None))
        return pairs
    for p in PAIRS:
        if len(p) >= 2:
            pairs.append((p[0], p[1], p[2] if len(p) > 2 else None))
    if SCENARIOS:
        for r in _ref_list():                     # every scenario against every base
            for s in SCENARIOS:
                pairs.append((r, s, None))
    if pairs or not ASK_IF_EMPTY:
        return pairs
    if AUTO_SCENARIOS:
        return None                      # resolved once the panel is loaded -- see main()
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


def _auto_pairs(z4):
    """REFERENCE + every run of its project (AUTO_SCENARIOS); with REFERENCE
       empty, every base folder is a reference in turn (ALL_PROJECTS)."""
    refs = ([_norm(r) for r in _ref_list()] or
            (_all_references(z4) if ALL_PROJECTS else []))
    pairs = []
    for ref in refs:
        if not os.path.isdir(ref):
            print("[pair] *** reference folder not found: %s ***" % ref)
            continue
        scen = _discover_scenarios(z4, ref)
        proj = _split_name(ref, z4.MODES)[0]
        if not scen:
            print("[pair] %-16s no other run of this project found beside %s" % (proj, ref))
            continue
        print("[pair] %-16s reference %s" % (proj, ref))
        for t in scen:
            print("[pair]                  vs %s" % t)
            pairs.append((ref, t, None))
    return pairs


def _project_folders(z4, parent):
    """{'SantaFe_spp': path, ...}: the results folders directly inside a
       parent folder (one per project), or {} when the folder is itself a
       results folder."""
    out = {}
    if _has_outs(parent):
        return out
    for m in list(z4.MODES) or ["spp"]:
        for d in _both_layouts(parent, "*_%s*" % m):
            if os.path.isdir(d) and _has_outs(d) and not _skip_dir(d):
                out[_base(d)] = _norm(d)
    return out


def _expand_parents(z4, pairs):
    """A PARENT folder on either side -- Base\results_base\BASE_CQ_F, or
       Projects\results_proj\Sep21_full gia, holding SantaFe_spp, IronStar_spp
       ... -- stands for every project inside it. The pair is expanded to one
       pair per project, matched by folder NAME on both sides, so all four
       projects are compared folder by folder in one run. A results folder
       given directly is used as it is."""
    out = []
    for ref, test, label in pairs:
        rp = _project_folders(z4, _norm(ref))
        tp = _project_folders(z4, _norm(test))
        if not rp and not tp:
            out.append((ref, test, label))
            continue
        if rp and tp:
            names = [n for n in rp if n in tp]
            # A SCENARIO RUN (SantaFe_spp_s1_egfoff, _egf, _poi502 ...) has no
            # folder of its own name on the reference side: it is compared
            # against its project's reference, SantaFe_spp.
            extra = []
            for n in sorted(set(tp) - set(rp)):
                _p, _m, _sfx = _split_name(n, z4.MODES)
                _k = "%s_%s" % (_p, _m) if (_p and _m) else None
                if _sfx and _k in rp:
                    extra.append((_k, n))
            _used = set(n for _k, n in extra)
            missing = sorted(set(rp) - set(tp)) + sorted(set(tp) - set(rp) - _used)
            if missing:
                print("[pair] %s vs %s: no match for %s -- skipped"
                      % (_base(ref), _base(test), ", ".join(missing)))
            for n in names:
                out.append((rp[n], tp[n], ("%s_%s" % (label, n.split("_")[0])) if label else None))
            for _k, n in extra:
                out.append((rp[_k], tp[n], ("%s_%s" % (label, n)) if label else None))
        elif rp:
            n = _base(test)
            if n in rp:
                out.append((rp[n], test, label))
            else:
                print("[pair] %s has no %s to compare with %s -- skipped" % (ref, n, test))
        else:
            n = _base(ref)
            if n in tp:
                out.append((ref, tp[n], label))
            else:
                print("[pair] %s has no %s to compare with %s -- skipped" % (test, n, ref))
        print("[pair] %s vs %s -> %d project folder pair(s) so far" % (_base(ref), _base(test), len(out)))
    return out


def main(argv):
    child = os.environ.get("CMP_MULTI_PAIRS")
    if child:
        # A FAST_COMPARE CHILD: its one project's pairs come from the parent,
        # never from the settings or the folder pickers again.
        with open(child) as fh:
            pairs = [tuple(x) for x in json.load(fh)]
    else:
        pairs = _pairs_from_settings(argv)
    z4 = _load_panel()
    if pairs is None:
        pairs = _auto_pairs(z4)
    if pairs:
        pairs = _expand_parents(z4, pairs)
    if not pairs:
        print("[pair] nothing to compare. Fill in REFERENCE + SCENARIOS or PAIRS, give "
              "two folders on the command line, or leave REFERENCE empty with "
              "ALL_PROJECTS = True to compare every project's runs against its base.")
        return 1
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
    # ONE PROJECT (or one reference) AT A TIME. Its pairs are compared, its
    # side-by-side workbook written, and everything is dropped before the
    # next: the rows of eight pairs held together are what ran a 32-bit
    # python out of memory part way through the third project. With
    # BASES_TOGETHER a project's comparisons against every base form one
    # group, so both bases land in one workbook.
    groups, gorder = {}, []
    for ref, test, label in pairs:
        if BASES_TOGETHER:
            k = tuple(_split_name(_norm(test), z4.MODES)[:2])
        else:
            k = _norm(ref)
        if k not in groups:
            groups[k] = []
            gorder.append(k)
        groups[k].append((ref, test, label))
    if (not child and FAST_COMPARE and int(FAST_PARALLEL or 1) > 1 and len(gorder) > 1):
        done = _fast_parallel(z4, [groups[k] for k in gorder])
        _finish(z4, done)
        print("[pair] %d of %d pair(s) compared" % (len(done), len(pairs)))
        return 0 if len(done) == len(pairs) else 1
    done = []
    t_all = time.time()
    for k in gorder:
        grp = []
        for ref, test, label in groups[k]:
            _free(z4)
            try:
                got = compare_pair(z4, ref, test, label)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("[pair] *** %s vs %s failed: %s ***" % (ref, test, e))
                got = None
            if got:
                grp.append(got)
        _free(z4)
        if len(grp) == 1 and SIDE_BY_SIDE:
            print("[pair] %s: one scenario against this reference -- its pair report IS the "
                  "side-by-side (base and project values adjacent on every sheet)" % grp[0]["proj"])
        if len(grp) > 1 and SIDE_BY_SIDE:
            try:
                write_side_by_side(z4, grp[0]["ref"], grp)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("[pair] *** the side-by-side sheet failed: %s ***" % e)
        # the index needs only the numbers -- the rows go
        for g in grp:
            done.append(dict((kk, g[kk]) for kk in ("label", "faults", "new", "pre", "ref", "test", "folder", "proj")))
        del grp
        _free(z4)
    if child:
        with open(os.environ["CMP_MULTI_DONE"], "w") as fh:
            json.dump(done, fh)
        print("[pair] %d of %d pair(s) compared -- %.0f s" % (len(done), len(pairs), time.time() - t_all))
        return 0 if len(done) == len(pairs) else 1
    _finish(z4, done)
    print("[pair] %d of %d pair(s) compared" % (len(done), len(pairs)))
    return 0 if len(done) == len(pairs) else 1


def _fast_parallel(z4, glist):
    """FAST_COMPARE: each project in its OWN process, FAST_PARALLEL at a time --
       each its own 2 GB of address space, and the projects no longer wait on
       one another. A child's console goes to <out>\<proj>\FAST_COMPARE_<proj>.log;
       what it compared comes back as a small JSON file."""
    import tempfile
    out_root = OUT_DIR or os.path.join(z4.STUDY_ROOT, "comparison_pairs")
    tmp = tempfile.mkdtemp(prefix="cmp_multi_")
    todo, running, done = list(enumerate(glist)), [], []
    n = max(1, int(FAST_PARALLEL or 1))
    t0 = time.time()
    print("[fast] FAST_COMPARE: %d project(s), %d at a time -- each in its own process"
          % (len(glist), n))
    while todo or running:
        while todo and len(running) < n:
            i, grp = todo.pop(0)
            proj = _split_name(_norm(grp[0][1]), z4.MODES)[0] or ("group%d" % i)
            pd = os.path.join(out_root, proj)
            if not os.path.isdir(pd):
                os.makedirs(pd)
            pj = os.path.join(tmp, "pairs_%d.json" % i)
            dj = os.path.join(tmp, "done_%d.json" % i)
            with open(pj, "w") as fh:
                json.dump([list(x) for x in grp], fh)
            env = dict(os.environ)
            env["CMP_MULTI_PAIRS"] = pj
            env["CMP_MULTI_DONE"] = dj
            logp = os.path.join(pd, "FAST_COMPARE_%s.log" % proj)
            lf = open(logp, "w")
            pr = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__)],
                                  env=env, stdout=lf, stderr=subprocess.STDOUT,
                                  cwd=os.path.dirname(os.path.abspath(__file__)))
            running.append((pr, lf, proj, dj, logp, time.time()))
            print("[fast]   started %-16s -> %s" % (proj, logp))
        time.sleep(2)
        for item in list(running):
            pr, lf, proj, dj, logp, ts = item
            if pr.poll() is None:
                continue
            running.remove(item)
            lf.close()
            got = []
            try:
                with open(dj) as fh:
                    got = json.load(fh)
            except Exception:
                pass
            done += got
            print("[fast]   %-16s %s -- %d pair(s), %.0f s%s"
                  % (proj, "done" if pr.returncode == 0 else "rc=%s" % pr.returncode,
                     len(got), time.time() - ts,
                     "" if pr.returncode == 0 else "  (see %s)" % logp))
    print("[fast] all projects: %.0f s" % (time.time() - t0))
    return done


def _finish(z4, done):
    """RERUN_ALL.txt, each project's RERUN list and the index, from what was
       compared."""
    if done:
        out_root = OUT_DIR or os.path.join(z4.STUDY_ROOT, "comparison_pairs")
        # EVERY FAULT TO RE-RUN, EVERY FOLDER COMPARED, IN ONE FILE: per project,
        # each base and each run once, with the RUN_CASES / ONLY_FAULTS lines to
        # paste into the panel.
        seen_f, entries = set(), []
        for g in sorted(done, key=lambda x: str(x.get("proj"))):
            for role, f in (("base", g["ref"]), ("run", g["test"])):
                if _norm(f) in seen_f:
                    continue
                seen_f.add(_norm(f))
                tag = _folder_tag(f)
                entries.append(("%s  %s (%s)" % (g["proj"], tag, role), f, g["proj"]))
        write_rerun_list(z4, os.path.join(out_root, "RERUN_ALL.txt"), entries)
        # ... and each project's own, in that project's folder
        for pr in sorted(set(e[2] for e in entries)):
            pd = os.path.join(out_root, pr)
            if not os.path.isdir(pd):
                os.makedirs(pd)
            write_rerun_list(z4, os.path.join(pd, "RERUN_%s_ALL.txt" % pr),
                             [e for e in entries if e[2] == pr])
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


if __name__ == "__main__":
    sys.exit(main(sys.argv))
