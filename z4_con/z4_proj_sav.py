# -*- coding: utf-8 -*-
"""
z4_proj_sav.py -- one PROJECT .sav per project from a BASE .sav.

For each project it builds the project power flow EXACTLY as the dynamic
study does, because it is the study script (z4_spp_p_con.py) doing it, in its
SAV_ONLY mode:

  1. the input base .sav is loaded, and the project area's generation is read
     BEFORE anything is added -- that is the MW the area is held to
  2. the new plant is built at the POI (NEW_PLANT in z4_cmp_all_con.py)
  3. the SGF (the new machines) go to their rating, and the EGF (the existing
     machines at the plant) make up the rest of the POI total, split by
     POI_P_SHARE -- the same EGF / SGF sharing the studies use
  4. the POI total is metered and grossed up for losses, the other machines in
     the area are rescaled so the area comes back to its pre-project MW, and
     the case is solved
  5. that solved case is saved as  OUT_DIR\\<input>_<project>_POI<MW>.sav
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
    stem = os.path.splitext(os.path.basename(base_sav))[0]
    out = os.path.join(out_dir, "%s_%s_POI%sMW%s.sav"
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
    if os.path.isfile(out):
        os.remove(out)                            # a stale file must not pass as this build
    log = os.path.splitext(out)[0] + ".log"
    print("[sav] %-14s POI %8.1f MW  -> %s" % (proj, mw, out))
    t0 = time.time()
    with open(log, "w") as fh:
        p = subprocess.Popen([sys.executable, "-u", script], cwd=study_dir, env=env,
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
    for proj in projects:
        lv = _levels(z4, proj)
        if not lv:
            print("[sav] %-14s no POI MW (POI_MW here or POI_P_TARGET_MW in the panel) -- skipped" % proj)
            failed.append(proj)
            continue
        for mw in lv:
            got = build_one(z4, proj, mw, base_sav, out_dir)
            (done if got else failed).append(got or "%s @ %g MW" % (proj, mw))
    print("")
    print("[sav] %d case(s) written, %d failed" % (len(done), len(failed)))
    for d in done:
        print("      %s" % d)
    for f in failed:
        print("      FAILED: %s" % f)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
