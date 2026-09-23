# -*- coding: utf-8 -*-
"""
z4_proj_sav.py -- one PROJECT .sav per project from a BASE .sav.

For each project it builds the project power flow EXACTLY as the dynamic
study does, because it is the study script (z4_spp_p_con.py) doing it. That
file is NOT changed: this script reads it, adds a "save the solved case and
stop" step IN MEMORY, and runs that copy -- your study scripts on disk stay
exactly as they are:

  1. the input base .sav is loaded, and the project area's generation is read
     BEFORE anything is added -- that is the MW the area is held to
  2. the new plant is built at the POI (NEW_PLANT in z4_cmp_all_con.py)
  3. the SGF (the new machines) go to their rating, and the EGF (the existing
     machines at the plant) make up the rest of the POI total, split by
     POI_P_SHARE -- the same EGF / SGF sharing the studies use
  4. the POI total is metered and grossed up for losses, the other machines in
     the area are rescaled so the area comes back to its pre-project MW, and
     the case is solved
  5. that solved case is saved as  OUT_DIR\\<project>\\<input>_<project>_POI<MW>.sav
     with a .txt beside it saying what was done and the area MW before/after

Nothing is simulated, no .cnv / .snp / .dyr is written, and nothing in the
study folders is changed -- it is safe beside a running study. Every setting
not given below comes from z4_cmp_all_con.py, as for a study run.

Each project uses one PSS/E licence while it builds (one project at a time).

HOW TO USE
  Put this file beside z4_cmp_all_con.py in C:\\KV, fill in SETTINGS, run
      py -3.4 z4_proj_sav.py
  or  py -3.4 z4_proj_sav.py <base .sav>

Python 3.4, standard library only.
"""
import os
import re
import sys
import time
import types
import subprocess

# ============================================================================
#  SETTINGS
# ============================================================================
BASE_SAV = r"C:\KV\Base\DIS2201-25SP-G03-CQ.sav"   # the input base case (no project in it)
OUT_DIR  = r""               # "" = <panel folder>\project_savs
PROJECTS = []                # [] = the panel's PROJECTS; or ["SantaFe", "IronStar"]
POI_MW = {                   # MW at the POI per project. A project not listed takes the
    # "SantaFe": 984.2,      # panel's POI_P_TARGET_MW; a LIST makes one .sav per level:
    # "IronStar": [216, 290.5],
}
POI_PCT = []                 # [] = off. [100, 80] = those percent of each project's POI MW
HOLD_AREA = True             # True = the project area's MW stays at its base-case value
EGF_OFF = False              # True = SPP BP-7250 7.6 scenario 1: SGF at 100 %, EGF out of service
SHARE = None                 # None = the panel's POI_P_SHARE ("capacity" | "present" | "equal")
PANEL = "z4_cmp_all_con.py"
STUDY_SCRIPT = "z4_spp_p_con.py"
# ============================================================================


def _here():
    try:
        return os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
    except NameError:
        return os.getcwd()


def _load_panel():
    """The panel as a module, PIPELINE forced to "compare" so its import-time
       checks do not stop; nothing in the panel file is changed."""
    path = os.path.join(_here(), PANEL)
    if not os.path.isfile(path):
        raise SystemExit("%s must sit beside this script (looked in %s)" % (PANEL, _here()))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    src = re.sub(r'^(PIPELINE\s*=\s*)"[^"]*"', r'\1"compare"', src, count=1, flags=re.M)
    mod = types.ModuleType("z4_cmp_all_con")
    mod.__file__ = path
    sys.modules["z4_cmp_all_con"] = mod
    exec(compile(src, path, "exec"), mod.__dict__)
    return mod


def _levels(z4, proj):
    """The POI MW values to build for one project."""
    v = POI_MW.get(proj)
    if v is None:
        t = getattr(z4, "POI_P_TARGET_MW", None)
        v = t.get(proj) if isinstance(t, dict) else t
    if v is None:
        return []
    base = list(v) if isinstance(v, (list, tuple)) else [v]
    out = [float(x) for x in base]
    for p in POI_PCT or []:
        p = float(p)
        p = p / 100.0 if p > 1.0 else p
        out += [round(float(base[0]) * p, 1)]
    seen, res = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            res.append(x)
    return res


def _mw_txt(mw):
    return ("%g" % mw).replace(".", "p")


def build_one(z4, proj, mw, base_sav, out_dir):
    study_dir = z4.CASE_TEST["dir"]
    script = os.path.join(study_dir, STUDY_SCRIPT)
    if not os.path.isfile(script):
        print("[sav] *** %s not found -- cannot build %s ***" % (script, proj))
        return None
    patched_engine_source(script)                 # stop now if the study script does not fit
    stem = os.path.splitext(os.path.basename(base_sav))[0]
    pdir = os.path.join(out_dir, proj)            # one folder per project
    if not os.path.isdir(pdir):
        os.makedirs(pdir)
    out = os.path.join(pdir, "%s_%s_POI%sMW%s.sav"
                       % (stem, proj, _mw_txt(mw), "_EGFoff" if EGF_OFF else ""))
    env = dict(os.environ)
    z4._push_settings(env, z4.CASE_TEST)          # every panel setting, as for a study run
    env["SPP_SOURCE_CASE"] = base_sav             # ... but THIS base case
    for k in ("SPP_SOURCE_CASE_BY_PROJECT", "SPP_CAP_SCALE", "SPP_CAP_TAG",
              "SPP_MERGE_ONLY", "SPP_REPORT_ONLY", "SPP_PLOT_MISSING"):
        env.pop(k, None)
    env["SPP_STUDY_DIR"] = study_dir
    env["SPP_PROJECT"] = proj
    env["SPP_RUN_PROJECTS"] = proj
    env["SPP_RUN_TAG"] = "savbuild"               # its log folder, not the study's results
    env["SPP_POI_P_TARGET"] = repr(float(mw))
    env["SPP_POI_HOLD_AREA"] = "1" if HOLD_AREA else "0"
    env["SPP_EGF_OFF"] = "1" if EGF_OFF else "0"
    if SHARE:
        env["SPP_POI_P_SHARE"] = str(SHARE)
    env["SPP_SAV_ONLY"] = out
    env["SPP_SAV_ENGINE"] = script
    if os.path.isfile(out):
        os.remove(out)                            # a stale file must not pass as this build
    log = os.path.splitext(out)[0] + ".log"
    print("[sav] %-14s POI %8.1f MW  -> %s" % (proj, mw, out))
    t0 = time.time()
    with open(log, "w") as fh:
        p = subprocess.Popen([sys.executable, "-u", os.path.abspath(__file__), "--engine"],
                             cwd=study_dir, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True)
        for line in p.stdout:
            fh.write(line)
            if ("[poi-p]" in line or "[sav-only]" in line or "***" in line
                    or "FAILED" in line or "Traceback" in line):
                sys.stdout.write("      " + line)
        rc = p.wait()
    ok = rc == 0 and os.path.isfile(out)
    print("[sav] %-14s %s in %.0f s  (full log: %s)"
          % (proj, "DONE" if ok else "*** FAILED rc=%s ***" % rc, time.time() - t0, log))
    return out if ok else None


# ---- THE STUDY SCRIPT, WITH A SAVE-AND-STOP STEP ADDED IN MEMORY ----------
# Each patch is (text that must be found once in z4_spp_p_con.py, what it
# becomes). If the study script has changed so a text is not found, nothing
# runs -- a build that silently skipped a step would save the wrong case.
_PATCHES = [
    ('MERGE_ONLY = _env_bool("SPP_MERGE_ONLY", MERGE_ONLY)\n',
     'MERGE_ONLY = _env_bool("SPP_MERGE_ONLY", MERGE_ONLY)\n'
     'SAV_ONLY = (os.environ.get("SPP_SAV_ONLY") or "").strip()\n'),
    ('def _np_buses_write(buses):\n'
     '    """Record the new plant\'s machine buses beside the snapshot."""\n',
     'def _np_buses_write(buses):\n'
     '    """Record the new plant\'s machine buses beside the snapshot."""\n'
     '    if SAV_ONLY:\n'
     '        print("  [newplant] SAV_ONLY: plant buses not written to the study folder")\n'
     '        return\n'),
    ('        # 3b) SURPLUS BESS: save the MODIFIED power-flow case BEFORE the GNET/CONL\n',
     '        if SAV_ONLY:\n'
     '            _sav_only_save(_mm)\n'
     '            return\n'
     '        # 3b) SURPLUS BESS: save the MODIFIED power-flow case BEFORE the GNET/CONL\n'),
    ('def build_case(outages=None, cnv=CNV_CASE, snp=SNP_FILE, tag="BUILD"):\n',
     '__SAV_ONLY_SAVE__\n'
     'def build_case(outages=None, cnv=CNV_CASE, snp=SNP_FILE, tag="BUILD"):\n'),
    ('if __name__ == "__main__":\n    rc = 0\n',
     'if __name__ == "__main__" and SAV_ONLY:\n'
     '    try:\n'
     '        if not os.path.isdir(LOG_DIR):\n'
     '            os.makedirs(LOG_DIR)\n'
     '        build_case(outages=None, tag="SAV_ONLY")\n'
     '        _rc = 0 if os.path.isfile(SAV_ONLY) else 1\n'
     '    except SystemExit:\n'
     '        raise\n'
     '    except Exception as _e:\n'
     '        traceback.print_exc()\n'
     '        print("[sav-only] FAILED: %s" % _e)\n'
     '        _rc = 1\n'
     '    sys.exit(_rc)\n'
     'if __name__ == "__main__":\n    rc = 0\n'),
]
_SAVE_FN = '''
def _sav_only_save(members):
    """Save the solved project case and a one-page note beside it."""
    d = os.path.dirname(os.path.abspath(SAV_ONLY))
    if d and not os.path.isdir(d):
        os.makedirs(d)
    ok = _pf_solved_code() in (0, None)
    chk(psspy.save(SAV_ONLY), "save project case (%s)" % os.path.basename(SAV_ONLY))
    lines = ["project case built by z4_proj_sav.py",
             "written   %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
             "input     %s" % SOURCE_CASE,
             "saved     %s" % SAV_ONLY,
             "solved    %s" % ("yes" if ok else "NO -- solved code %s" % _pf_solved_code()),
             "POI total %s MW  (measured: %s, share of the rest: %s, project machines: %s)"
             % (POI_P_TARGET_MW, POI_P_MEASURE, POI_P_SHARE, POI_P_PROJECT_AT),
             "EGF off   %s" % bool(POI_P_EXISTING_OFF),
             "area hold %s" % bool(POI_HOLD_AREA_MW)]
    for a, mw in sorted(_AREA_MW_BEFORE.items()):
        now = _area_gen_mw(a)
        lines.append("area %-5s before the project %.1f MW, now %s MW"
                     % (a, mw, ("%.1f" % now) if now is not None else "?"))
    for mr, mmw in members or []:
        lines.append("plant %-14s POI %s  machines %s"
                     % (mr.get("name"), mr.get("poi"),
                        ", ".join("%s '%s'" % (b, m) for b, m in _member_gens(mr)) or "-"))
    try:
        with open(os.path.splitext(SAV_ONLY)[0] + ".txt", "w") as fh:
            fh.write("\\n".join(lines) + "\\n")
    except Exception as e:
        print("  [sav-only] could not write the note (%s)" % e)
    for ln in lines:
        print("  [sav-only] " + ln)

'''


def patched_engine_source(path):
    """z4_spp_p_con.py's text with the save-and-stop step added."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    missing = []
    for old, new in _PATCHES:
        if src.count(old) != 1:
            missing.append(old.strip().splitlines()[0])
            continue
        src = src.replace(old, new)
    if missing:
        raise SystemExit("[sav] *** %s does not have the lines this script expects -- "
                         "nothing was built:\n      %s" % (path, "\n      ".join(missing)))
    return src.replace("__SAV_ONLY_SAVE__\n", _SAVE_FN)


def run_engine():
    """The child process: run the patched study script as if it were started."""
    path = os.environ["SPP_SAV_ENGINE"]
    src = patched_engine_source(path)
    sys.argv = [path]
    g = {"__name__": "__main__", "__file__": path, "__builtins__": __builtins__}
    exec(compile(src, path, "exec"), g)


def main(argv):
    base_sav = os.path.abspath(argv[1] if len(argv) > 1 else BASE_SAV)
    if not os.path.isfile(base_sav):
        print("[sav] *** base case not found: %s ***" % base_sav)
        return 1
    z4 = _load_panel()
    out_dir = OUT_DIR or os.path.join(z4.STUDY_ROOT, "project_savs")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    projects = list(PROJECTS or z4.PROJECTS)
    print("[sav] input base case : %s" % base_sav)
    print("[sav] output folder   : %s" % out_dir)
    print("[sav] area held       : %s    EGF off: %s    share: %s"
          % (HOLD_AREA, EGF_OFF, SHARE or getattr(z4, "POI_P_SHARE", "capacity")))
    done, failed = [], []
    res = {}                                      # project -> [(mw, path or None, why)]
    for proj in projects:
        lv = _levels(z4, proj)
        if not lv:
            print("[sav] %-14s no POI MW (POI_MW here or POI_P_TARGET_MW in the panel) -- skipped" % proj)
            failed.append(proj)
            res.setdefault(proj, []).append((None, None, "no POI MW given"))
            continue
        for mw in lv:
            got = build_one(z4, proj, mw, base_sav, out_dir)
            (done if got else failed).append(got or "%s @ %g MW" % (proj, mw))
            res.setdefault(proj, []).append((mw, got, "" if got else "build failed -- see its .log"))
    print("")
    print("[sav] %d case(s) written, %d failed" % (len(done), len(failed)))
    for d in done:
        print("      %s" % d)
    for f in failed:
        print("      FAILED: %s" % f)
    write_rerun(os.path.join(out_dir, "RERUN_ALL.txt"), res, base_sav)
    for proj in res:
        write_rerun(os.path.join(out_dir, proj, "RERUN_%s.txt" % proj), {proj: res[proj]}, base_sav)
    return 0 if not failed else 1


def write_rerun(path, res, base_sav):
    """What was built, what failed, and the lines to paste into SETTINGS to
       build only the failed ones again."""
    L = ["PROJECT .sav BUILDS -- what to re-run in z4_proj_sav.py",
         "generated %s" % time.strftime("%Y-%m-%d %H:%M"),
         "input     %s" % base_sav, ""]
    redo = {}
    for proj in sorted(res):
        L.append(proj)
        for mw, got, why in res[proj]:
            if got:
                L.append("   OK      POI %g MW  -> %s" % (mw, got))
            else:
                L.append("   FAILED  %s%s" % (("POI %g MW  -- " % mw) if mw is not None else "", why))
                if mw is not None:
                    redo.setdefault(proj, []).append(mw)
        L.append("")
    if redo:
        L += ["To build only the failed ones again, set in z4_proj_sav.py:",
              "    PROJECTS = [%s]" % ", ".join('"%s"' % p for p in sorted(redo)),
              "    POI_MW   = {%s}" % ", ".join('"%s": [%s]' % (p, ", ".join("%g" % m for m in redo[p]))
                                            for p in sorted(redo)),
              "    POI_PCT  = []",
              "and run it again. The .log beside each failed .sav says why it failed."]
    else:
        L.append("Nothing to re-run: every build was written.")
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(path, "w") as fh:
            fh.write("\n".join(L) + "\n")
        print("[sav] re-run list -> %s" % path)
    except Exception as e:
        print("[sav] could not write %s: %s" % (path, e))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--engine":
        run_engine()
        sys.exit(0)
    sys.exit(main(sys.argv))
