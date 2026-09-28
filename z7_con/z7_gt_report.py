# -*- coding: utf-8 -*-
"""Write the GEN TEST reports NOW, from the runs already on disk.

   Simulates nothing and touches no run folder, so it is safe to start while
   the gen test itself is running: it reads every finished run in
   <Base>\\results_base\\<project>\\gen_test\\...\\<project>_<mode>_gt_* and writes

       comparison_scenarios\\<project>\\BASE_CASE\\gen_test\\
           GEN_TEST_<project>.txt/.csv, GEN_TEST_BEST_<project>.txt,
           GEN_TEST_IMPACT_<project>.txt/.csv, GEN_TEST_NOT_RUN_<project>.txt

   with the settings of z7_main.py (same folder) -- every project in
   GEN_TEST_PROJECTS, or GEN_TEST_PROJECT alone. Runs still going or waiting
   show as not done. The running gen test rewrites the same files as each of
   its runs finishes, so this is only for reading them before that.

   Needs one PSS/E licence for about a minute: the plan (which machines,
   caps and lines) is found from the case, exactly as the gen test finds it.

       python z7_gt_report.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import z7_main as M

M.GEN_TEST_DRY_RUN = True          # list the plan, write the reports, simulate nothing
print("[gt-report] reports only -- nothing is simulated, no run folder is touched")
sys.exit(M.run_gen_tests())
