# -*- coding: utf-8 -*-
"""Move the results folders of EVERY project into the new layout, now.

       results_base\\<project>\\<project>_spp\\                      normal study
       results_base\\<project>\\gen_test\\POI_ON\\00_REFERENCE\\       all in service
                                     \\POI_ON\\GEN_OFF|LINE_OFF|CAPS_OFF|MODEL_EDIT\\
                                     \\POI_OFF\\00_POIGENOFF\\         POI plants off
                                     \\POI_OFF\\GEN_OFF|LINE_OFF|CAPS_OFF\\
   (and the same in results_proj\\)

   Run folder NAMES are not changed, nothing is simulated or scored again:
   the next launch finds every run in its new place. It is the same move a
   launch makes at its start (TIDY_RESULTS in z6_main.py), done on its own.

   Close every launch, plotter and Explorer window on the results first -- a
   folder with a file open in it cannot be moved; it is named and left where
   it is (still found and used there), and the script can simply be run again.

       python z6_tidy_results.py
"""
import os
import sys
import glob

# ---- setting ---------------------------------------------------------------
DRY_RUN = False        # True = only list what would move, move nothing

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import z6_main as M

if not all(hasattr(M, a) for a in ("_run_group", "_is_proj_box", "tidy_results")):
    print("")
    print("[tidy] *** %s is an OLDER z6_main.py -- it does not know the new layout." % M.__file__)
    print("[tidy]     Replace it (and z6_lch_b/p, z6_spp_b/p, z6_gt_report) with the new")
    print("[tidy]     versions, then run this again. Nothing was moved. ***")
    sys.exit(2)


def _plan():
    """[(from, to)] for every run folder not yet where the new layout puts it."""
    names = set(M._panel_projects() or []) | set(M.PROJECTS or []) | set(M.GEN_TEST_PROJECTS or [])
    if M.GEN_TEST_PROJECT:
        names.add(M.GEN_TEST_PROJECT)
    names = sorted((n for n in names if n), key=len, reverse=True)
    out = []
    for case in (M.CASE_BASE, M.CASE_TEST):
        root = M._res_root(case)
        if not os.path.isdir(root):
            continue
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            if not os.path.isdir(d) or M._is_proj_box(d):
                continue
            nm = os.path.basename(d)
            proj = next((p for p in names if nm.startswith(p + "_")), None)
            if proj:
                out.append((d, os.path.join(root, proj, M._run_group(nm), nm)))
        for box in sorted(glob.glob(os.path.join(root, "*"))):
            if not M._is_proj_box(box):
                continue
            for d in sorted(glob.glob(os.path.join(box, "*"))):
                grp = M._run_group(os.path.basename(d))
                if grp and os.path.isdir(d):
                    out.append((d, os.path.join(box, grp, os.path.basename(d))))
    return out


def main():
    plan = _plan()
    print("")
    print("[tidy] %d run folder(s) to move%s" % (len(plan), " (DRY RUN -- nothing moved)" if DRY_RUN else ""))
    for a, b in plan:
        root = os.path.dirname(os.path.dirname(a)) if M._is_proj_box(os.path.dirname(a)) else os.path.dirname(a)
        print("  %s\n      -> %s" % (os.path.relpath(a, root), os.path.relpath(b, root)))
    if DRY_RUN or not plan:
        return 0
    rc = M.tidy_results()
    left = _plan()
    print("")
    if left:
        print("[tidy] %d folder(s) still in the old place (open, already there, or the path "
              "would be too long for Windows) -- they are still used where they are:" % len(left))
        for a, _b in left:
            print("    %s" % a)
    else:
        print("[tidy] done -- every run folder is in the new layout")
    return rc


if __name__ == "__main__":
    sys.exit(main())
