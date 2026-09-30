"""
=============================================================================
 run_study_parallel_PQ.py  --  MULTI-PROCESS parallel launcher for
                               SPP_WORKING_parallel_PQ.py  (DIS2301-1-25SP-G03-PQ)

 WHY: PSS/E has ONE global engine per Python process -- you CANNOT run two
 simulations at once inside a single process (one case in memory, one channel
 set, one .out at a time). So "parallel" means several PROCESSES, each its own
 PSS/E session, each running a DIFFERENT slice of the fault set. This launcher
 orchestrates that in three phases:

   1) BUILD   : ONE process builds the snapshot (.cnv/.snp), runs the flat/QC
                check, and writes the SPP fault-definition files.  (SPP_ROLE=build)
   2) WORK    : N worker processes run CONCURRENTLY, worker i taking faults
                i, i+N, i+2N, ...  Each reuses the snapshot built in phase 1 and
                plots each fault immediately.  (SPP_ROLE=work)
   3) REPORT  : ONE process reads EVERY finished .out and writes the single
                merged SPP_CRITERIA_REPORT.  (SPP_ROLE=report)

 Each phase is crash-resilient: a process that dies is relaunched (up to
 MAX_LAUNCHES_PER), and the study's own .done markers make it RESUME instead of
 restart. Worker slices are disjoint, so workers never collide.

 IMPORTANT -- before relying on this:
   * LICENSE: each worker calls psseinit() = a separate concurrent PSS/E
     session. A network/multi-token license allows this; a single-seat license
     may reject worker 2+ at init. If a worker dies immediately with a license
     error, lower N_WORKERS to 1 or get more tokens.
   * RAM: each worker holds a FULL copy of the ~150k-bus case. N workers use
     ~N x the memory. Start with 2-3 and watch memory before going higher.

 HOW TO RUN (same Python that has PSS/E on its path):
     c:\\Python34\\python.exe  C:\\study1\\run_study_parallel_PQ.py

 ---------------------------------------------------------------------------
 SETTINGS AS SHIPPED -- all 4 projects, 3-bus radius, 3PH + SLG, SPP criteria
 ---------------------------------------------------------------------------
 HERE                              IN run_SPP2.py
   RUN_PROJECTS = all four           CUSTOM_HOPS        = 3
   FAULT_MODES  = ["custom"]         CUSTOM_TYPES       = ["3PH", "SLG"]
   N_WORKERS        = 6              CUSTOM_CYCLES      = [9]
   MAX_LAUNCHES_PER = 100             CUSTOM_KV_MIN      = 100.0
   RUN_ONLY_FAULTS  = []             CUSTOM_MAX_BUSES   = 40
   REGEN_FAULTS = "if-missing"       CUSTOM_INCLUDE_POI = True
   FRESH_START  = True               MAX_SCENARIO_ATTEMPTS = 2

 That is 4 studies, run one after another, each into its own folder:

     results\\IronStar_custom\\   results\\EastFork_custom\\
     results\\SantaFe_custom\\    results\\EmpirePrairie_custom\\

 each with its own faults\\, outs\\, plots\\, RUN_SUMMARY and
 SPP_CRITERIA_REPORT. Up to 40 buses x 2 types x 1 clearing time = 80
 scenarios per project, 320 in total, ~13 per worker per project.

 WHY MAX_LAUNCHES_PER = 35: a worker needs one launch to work its slice, and
 one more per crash. Crashes are capped at
     scenarios_in_slice x MAX_SCENARIO_ATTEMPTS = 13 x 2 = 26,
 so 27 is the worst honest case and 35 is that plus margin. Set it too low and
 the last scenarios in a slice never get their second attempt; too high and a
 genuinely stuck worker keeps relaunching for hours. Nothing is re-run on a
 relaunch -- .done markers make it resume.

 6 WORKERS = 6 CONCURRENT PSS/E SESSIONS. Confirm the license allows it and
 that RAM holds 6 copies of the case before starting a 320-scenario run; if
 worker 2+ dies instantly with a license error, that is the cause. Start it,
 watch the first LIVE_STATUS table (60 s), and stop it there if not.
=============================================================================
"""
import os, sys, re, subprocess, time, glob, csv, threading

# NO WINDOWS ERROR BOXES. A crashed process (an access violation in PSS/E, a
# floating-point trap) otherwise waits on "python.exe has stopped working" --
# alive, holding its slot, until someone clicks. With these flags it exits with
# its crash code and the launcher relaunches it. Child processes inherit the
# mode, so every launcher, worker, shard and plotter started from here has it.
if os.name == "nt":
    try:
        import ctypes as _ct_em
        _ct_em.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    except Exception:
        pass

# --- EDIT THESE ------------------------------------------------------------
def _script_dir():
    """The folder THIS FILE is in. Used when the study is run on its own; when
       z7_main.py drives it, SPP_STUDY_DIR wins and points at the same place."""
    try:
        return os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
    except NameError:
        return os.getcwd()


# NO ABSOLUTE PATH. The case folder is wherever THIS FILE sits, unless
# z7_main.py says otherwise -- so the whole study moves between machines
# by copying the folder, with nothing to edit.
STUDY_DIR        = os.environ.get("SPP_STUDY_DIR") or _script_dir()
# >>> POINT THIS AT THE STUDY SCRIPT YOU ARE ACTUALLY EDITING.
# It said run_SPP2.py while the study script had been renamed run_SPP3.py, and
# the old run_SPP2.py was still sitting in the folder -- so the launcher would
# have run the OLD file without a word. That is the version mismatch that made
# REPORT_FAULTS look broken and the progress line stick on "starting".
# check_study_version() below now also checks whatever this points at.
STUDY_SCRIPT     = os.path.join(STUDY_DIR, "z7_spp_p.py")
N_WORKERS        = 4         # <<< number of PARALLEL PSS/E worker processes (mind license + RAM)

# >>> OVERRIDABLE FROM THE ENVIRONMENT, so z7_main.py can size BOTH
# cases from one place. It has to be one place: with RUN_IN_PARALLEL the two
# launchers run at the same time, so the machine sees 2 x this many PSS/E
# sessions, and a number chosen for one case alone is twice what was intended.
#     set SPP_LAUNCH_WORKERS=3
_envw = (os.environ.get("SPP_LAUNCH_WORKERS") or "").strip()
if _envw.isdigit() and int(_envw) > 0:
    N_WORKERS = int(_envw)
    print("[parallel] N_WORKERS from the environment: %d" % N_WORKERS)
                             # 3 because z7_main.py runs BOTH studies at
                             # once (RUN_IN_PARALLEL): 3 here x 2 studies = 6
                             # concurrent PSS/E sessions, which is what 6 in a
                             # single-study run used to cost. Raising this
                             # without lowering the other side, or turning
                             # RUN_IN_PARALLEL off, doubles the licence count.
# Crash-relaunch cap PER phase/worker. A worker needs 1 launch to do its slice
# cleanly, and one MORE for every crash; crashes are bounded by
#     scenarios_in_the_slice x MAX_SCENARIO_ATTEMPTS   (2, in the study script)
# so the cap has to cover that or the last few scenarios in a slice never get
# their second attempt. For the 4-project / 3-hop / 3PH+SLG run below that is
# 80 scenarios per project over 6 workers = 14 each, so 1 + 14x2 = 29 worst
# case; 35 leaves margin without letting a truly stuck worker run forever.
# Resume means a relaunch re-does nothing that already has a .done marker.
MAX_LAUNCHES_PER =  1000      # crash-relaunch cap PER phase/worker (resume skips finished work)

# --- BUILD / REPORT PHASES: their own caps AND their own timeout -------------
# THIS IS WHY ONLY IRONSTAR RAN.
#
# The WORK phase has a hang watchdog (HANG_TIMEOUT_S) because a crashed PSS/E
# can freeze instead of exiting -- a Windows error dialog, a Fortran pause, a
# blocked write. Phases 1 and 3 had NO such protection: they used a plain
# subprocess.call(), which blocks FOREVER on exactly the same freeze. IronStar's
# workers finished at 05:52 and the launcher wrote its pre-report summary, then
# the REPORT process stopped responding and the launcher waited on it -- so
# EastFork, SantaFe and EmpirePrairie never started. One stuck report held four
# projects hostage.
#
# Now every phase is launched the same way the workers are (piped, tagged,
# activity-stamped) and KILLED if it goes silent for PHASE_TIMEOUT_S. The pass
# then continues, the project is recorded as report-failed, and the queue moves
# on to the next project.
#
# The caps are separate and SMALL on purpose. Build and report failures are
# usually DETERMINISTIC -- a missing model, an unreadable .out -- so they fail
# identically every launch. Retrying such a failure 35 times is 35 x a full case
# load for a result that cannot change. Three attempts is enough to ride out a
# genuine transient.

# --- RUN THE REPORT IN THE BACKGROUND AND START THE NEXT PROJECT -------------
# The report phase reads every .out and scores the criteria. Those files are
# ~27 MB with 5665 channels each, so the phase takes a long time -- and while it
# ran, the launcher sat there and the next project did not start. Reading .out
# files is pure post-processing: it needs nothing from the simulations that are
# about to run, and the next project writes to a different folder, so there is
# nothing to serialise for.
#
# True  = spawn the report, move straight on to the next project, and collect
#         every outstanding report at the end before the final roll-up.
# False = the old behaviour, one project fully finished before the next starts.
#
# COUNT YOUR PSS/E SESSIONS. A background report is one more concurrent session
# on top of the workers: peak = N_WORKERS + (reports still running). With 6
# workers and 2 reports that is 8. If the licence or the RAM will not carry
# that, lower REPORT_MAX_BG or N_WORKERS rather than turning this off -- the
# launcher waits for a slot instead of overrunning it.
# --- REPORT ONLY: score .out files that are ALREADY on disk -----------------
# True = skip the build and the workers entirely and run ONLY the report phase,
#        over whatever .out files are already in each selected project's outs\
#        folder. No simulations. Use it when the runs finished but the report
#        died, took too long, or you changed the criteria and want the existing
#        results re-scored.
#
# It obeys RUN_PROJECTS and FAULT_MODES, so it reads exactly the folders those
# name -- results\<project>_<mode>\ -- and writes that project's
# SPP_CRITERIA_REPORT / SPP_COMPLIANCE_TABLE / SPP_VIOLATIONS, then the
# all-projects roll-up.
#
# FRESH_START IS IGNORED WHILE THIS IS ON, deliberately. FRESH_START deletes the
# .done markers, and the report uses those markers to decide which .out files
# finished properly. Clearing them before a report-only run would exclude every
# scenario and produce an empty report from a complete set of results.
REPORT_ONLY = False


# >>> OVERRIDABLE FROM THE ENVIRONMENT, so run_compare.py can ask for a
# report-only pass over results that already exist without editing this file.
#     set SPP_REPORT_ONLY=1
_envr = (os.environ.get("SPP_REPORT_ONLY") or "").strip().lower()
if _envr in ("1", "true", "yes", "on"):
    REPORT_ONLY = True
    print("[parallel] REPORT_ONLY forced on from the environment")
elif _envr in ("0", "false", "no", "off"):
    REPORT_ONLY = False

# >>> SIMULATE EVERY PROJECT FIRST, SCORE AFTERWARDS.
# The report phase ran right after each project's simulation, in the
# foreground: a relaunch that had nothing left to simulate for SantaFe still
# spent its first twenty minutes re-reading SantaFe's scores before IronStar's
# first fault started. With this on, the launcher stops after the WORK phase
# of each project and z7_main.py scores every project of both cases at
# the end, in one pass, from the parts the workers wrote as they ran.
#     set SPP_DEFER_REPORTS=1       (the panel sends it: REPORTS_AFTER_ALL_PROJECTS)
DEFER_REPORTS = False
_envd = (os.environ.get("SPP_DEFER_REPORTS") or "").strip().lower()
if _envd in ("1", "true", "yes", "on"):
    DEFER_REPORTS = True
elif _envd in ("0", "false", "no", "off"):
    DEFER_REPORTS = False

# --- SCORE ONLY SOME SCENARIOS ----------------------------------------------
# Empty = score every .out in the folder.
# Non-empty = score ONLY these, and write the result to SPP_CRITERIA_REPORT_
#             SELECTED.txt rather than over the full report.
#
# Ids, ranges and the usual keywords all work:
#     REPORT_FAULTS = ["F03", "F07", "F19"]
#     REPORT_FAULTS = ["F01-F20"]
#     REPORT_FAULTS = ["FAIL"]        the ones that failed last time
#     REPORT_FAULTS = ["NOTDONE"]
#
# Worth having because a scenario costs MINUTES to read: checking six faults
# should not mean waiting for a hundred and forty.
REPORT_FAULTS = []

# >>> OVERRIDABLE FROM THE ENVIRONMENT (same reason as RUN_ONLY_FAULTS below).
#     set SPP_REPORT_FAULTS=F03,F07
_envrf = (os.environ.get("SPP_REPORT_FAULTS") or "").strip()
if _envrf:
    REPORT_FAULTS = [x.strip() for x in _envrf.split(",") if x.strip()]
    print("[parallel] REPORT_FAULTS from the environment: %s"
          % ", ".join(REPORT_FAULTS))

# --- CLEAR .badout MARKERS BEFORE SCORING -----------------------------------
# A .badout marker next to a .out means "do not hand this file to dyntools".
# It is meant to be written only when a read ACTUALLY died.
#
# An earlier build also wrote one whenever a byte scan saw a single non-finite
# float anywhere in the file. That scan is channel-blind -- one machine in a
# far-off area that tripped is enough -- so it condemned ~141 perfectly good
# scenarios in one pass, and every later report skipped everything and wrote
# nothing. The study script no longer does that, but the markers it already
# wrote are still on disk and would keep the folder dead.
#
# True  = delete results\<project>_<mode>\outs\*.badout before each report,
#         printing how many went. Files that genuinely crash the reader are
#         re-marked the moment they crash again, so nothing is lost by clearing
#         -- at worst one scenario is retried in a child and re-condemned.
# False = leave the markers alone.
CLEAR_BADOUT = True

# --- HOW MANY FILES THE REPORTS LEAVE BEHIND --------------------------------
# Each project writes four reports, and each has had a .csv companion: eight
# files per project, thirty-two across four projects, in folders you go to when
# you want to read one thing.
#
# WRITE_CSV = False turns the companions off. The .txt is ALWAYS written -- it is
# the report you read and the one this launcher parses -- so it is never
# optional. Keep the CSVs on while you are pivoting violations in Excel; turn
# them off when you only want to read results.
#
# This setting must MATCH the one in the study script, which writes the
# per-project files. The launcher checks and says so if they disagree.
WRITE_CSV = False

# --- HOW MANY PROCESSES SCORE THE REPORT ------------------------------------
# The report reads every .out and judges it. That is per-file work on
# independent files, so it shards exactly like the WORK phase does.
#
# It matters more than it sounds: a 5665-channel .out takes MINUTES for dyntools
# to parse, so 142 of them serially is most of a working day. Six processes turn
# ~9 hours into ~1.5.
#
#   1 = one process, as before
#   N = N processes score files[i::N], then ONE short merge process reads their
#       parts and writes the single report. The merge opens no .out and takes
#       seconds.
#
# Each shard is a PSS/E session, so this adds to the concurrent-session count
# the same way N_WORKERS does. With REPORT_MAX_BG background reports running,
# peak sessions = REPORT_WORKERS x (reports running).
REPORT_WORKERS = 3
_envrw = (os.environ.get("SPP_LAUNCH_REPORT_WORKERS") or "").strip()
if _envrw.isdigit() and int(_envrw) > 0:
    REPORT_WORKERS = int(_envrw)
    print("[parallel] REPORT_WORKERS from the environment: %d" % REPORT_WORKERS)

REPORT_IN_BACKGROUND = True
# z7_main.py turns this OFF when CORES_MAX_INCLUDES_REPORTS is set: a report
# left running while the next project's workers start is exactly how a ceiling
# of 8 sessions became 10 on the machine. Local setting when nothing is driving
# this launcher.
_envbg = (os.environ.get("SPP_LAUNCH_REPORT_BG") or "").strip()
if _envbg in ("0", "1"):
    REPORT_IN_BACKGROUND = (_envbg == "1")
    print("[parallel] REPORT_IN_BACKGROUND from the environment: %s"
          % ("on" if REPORT_IN_BACKGROUND else "off -- the report runs between "
             "studies, inside the session budget"))
REPORT_MAX_BG        = 2     # most reports allowed to run at once
REPORT_BG_TIMEOUT_S  = 14400 # kill a background report still going after this long (4 h)
SHARD_STALL_S        = 3600  # kill an individual report SHARD that has printed nothing
                             # for this long (60 min). Judged PER SHARD and it kills
                             # only that one -- a shard stuck on a bad .out must not
                             # take down the five that are working. The shard is then
                             # relaunched, condemns the file that stalled it, and
                             # resumes from its part file.
                             #
                             # WAS 20 MINUTES, AND THAT WAS THE BUG. Scoring one .out
                             # is a single blocking call, and under load it ran past
                             # 20 min -- so a shard doing honest work was killed,
                             # relaunched, killed again, and gave up after three
                             # launches. 19 of 142 scenarios got scored. The study
                             # script now prints a [alive] heartbeat every
                             # HEARTBEAT_S (120 s) while it works, so silence really
                             # does mean stuck; this is the backstop behind it.
REPORT_SILENT_S      = 3000  # ...or after this long with NO OUTPUT AT ALL (50 min).
                             # Was 1800. Nothing may be killed inside KILL_GRACE_S
                             # of silence anyway, so a shorter value here could
                             # never have fired -- it now says what it means.
                             # Total runtime and silence are different questions: a report
                             # working through 141 .out files legitimately takes a long
                             # time, but it prints as it goes. One that has said nothing
                             # for half an hour is stuck, and waiting the full 4 h to find
                             # out wastes the difference.

MAX_LAUNCHES_BUILD  = 3      # relaunch cap for phase 1 (was MAX_LAUNCHES_PER = 35)
MAX_LAUNCHES_REPORT = 40     # relaunch cap for phase 3 (was 3). A report shard RESUMES
                             # from its part file on relaunch (it skips every scenario
                             # already scored) and condemns only the one .out that killed
                             # it, so each extra launch is pure forward progress. On a
                             # 32-bit build the heavy diverged .out files (F101/F102/F150
                             # etc.) can exhaust the 2 GB address space on the read and
                             # take the shard down; at a cap of 3 a run stalled having
                             # scored ~a third of the study. A high cap lets ONE run
                             # grind through them -- crash, resume, skip, carry on -- to
                             # full coverage instead of needing the report re-run by hand.
PHASE_TIMEOUT_S     = 3600   # kill a build/report that has printed NOTHING for this long.
                             # Not total runtime -- SILENCE. A working build prints
                             # continuously, so an hour of silence is a freeze, not slow
                             # progress. Raise it if a single legitimate step really can be
                             # silent longer than this.
FRESH_START      = False     # clear .done/.attempts + every ALL_DONE*.flag once, up front
                             # DEFAULT IS "RESUME". This is the most destructive setting in
                             # the toolchain -- it deletes every marker in the folder -- and a
                             # default of True meant any child started WITHOUT SPP_FRESH_START
                             # in its environment did exactly that. The panel states the real
                             # value on every run it starts, so nothing that should start over
                             # loses the ability to; what changes is that silence now means
                             # "keep what is on disk" instead of "throw it away".

# >>> OVERRIDABLE FROM THE ENVIRONMENT, so z7_main.py can start both
# cases the same way without either launcher being edited. One case starting
# fresh while the other resumes is not a comparison of two cases -- it is a
# comparison of two different amounts of work.
#     set SPP_FRESH_START=0        resume
#     set SPP_FRESH_START=1        start over
_envfs = (os.environ.get("SPP_FRESH_START") or "").strip().lower()
if _envfs in ("1", "true", "yes", "on"):
    FRESH_START = True
    print("[parallel] FRESH_START forced ON from the environment")
elif _envfs in ("0", "false", "no", "off"):
    FRESH_START = False
    print("[parallel] FRESH_START forced OFF from the environment -- resuming")
# A LAUNCH DRIVEN BY z7_main.py STARTS OVER ONLY WHEN THE PANEL SAYS SO.
# The panel sends SPP_STUDY_DIR with every launch, and SPP_FRESH_START=1 only
# when its own FRESH_START is on. A True in THIS file cannot override that: the
# base case once started over because of it while the project case resumed, and
# that is a comparison of two different amounts of work.
if (FRESH_START and (os.environ.get("SPP_STUDY_DIR") or "").strip()
        and _envfs not in ("1", "true", "yes", "on")):
    FRESH_START = False
    print("[parallel] FRESH_START is on in this file, but the launch is driven by")
    print("[parallel] z7_main.py and it did not ask for a fresh start -- resuming;")
    print("[parallel] the .done/.attempts markers are kept")
# ---- NEVER KILL A WORKER THAT IS STILL RUNNING -----------------------------------
# A 30 s simulation with ~7,800 channels can take 30-45 minutes of wall clock, and
# longer on a loaded machine or a machine with fewer cores. Every watchdog below
# judges SILENCE, and a worker deep inside one blocking psspy call is silent by
# definition -- so on a slow PC the watchdogs were killing workers that were doing
# honest work, and the study lost the whole scenario.
#
# With this True, NOTHING here kills a live process. A worker ends when it exits by
# itself: sentinel written, crash, or PSS/E gone. The watchdogs still WATCH -- they
# print how long a process has been quiet so a genuine freeze is visible -- they
# just do not act on it. The cost is that a truly frozen worker (a Windows error
# dialog nobody clicks) holds its slot until you stop the run yourself.
#
# Set False to restore the old kill-on-silence behaviour.
#
# IT IS FALSE NOW. The cost above -- "a truly frozen worker holds its slot
# until you stop the run yourself" -- was paid in full on 2026-09-16: IronStar
# F166 held work5 for 18h 33m behind a CodeMeter dialog while the other six
# workers had retired, and the campaign made no progress for a day. The
# watchdogs below act again. KILL_GRACE_S still floors every one of them, so
# a long solve is safe; a frozen one is not left standing.
NEVER_KILL_WORKERS = False
_envnk = (os.environ.get("SPP_NEVER_KILL") or "").strip().lower()
if _envnk in ("1", "true", "yes", "on"):
    NEVER_KILL_WORKERS = True
elif _envnk in ("0", "false", "no", "off"):
    NEVER_KILL_WORKERS = False

# ---- NOTHING MAY BE KILLED INSIDE THIS MANY SECONDS OF SILENCE ------------------
# A FLOOR UNDER EVERY WATCHDOG, not another watchdog. Each one below carries its own
# limit -- HANG_TIMEOUT_S, PHASE_TIMEOUT_S, SHARD_STALL_S, REPORT_SILENT_S -- and any
# of them being set too low is enough to kill a worker that is simply deep inside one
# blocking psspy call. This is checked FIRST and overrides all of them: under 50
# minutes of silence, no watchdog may act, whatever its own setting says.
#
# So the guarantee holds even if NEVER_KILL_WORKERS is turned off later, or a limit
# below is edited down, or a new watchdog is added that forgets the rule.
KILL_GRACE_S = 1800.0                       # 30 minutes. Slowest scenario ever
                                            # measured here: 24m 02s, mean 5m 14s.
try:
    KILL_GRACE_S = float((os.environ.get("SPP_KILL_GRACE_S") or "").strip()
                         or KILL_GRACE_S)
except Exception:
    pass

_QUIET_WARNED = {}

def _exit_reason(rc):
    """Turn a worker's exit code into the sentence that identifies the cause.

       Windows returns a fatal exception's code AS the exit code, and those codes
       are the whole diagnosis: 0xC0000005 is a bad pointer inside a model,
       0xC0000017 is 32-bit PSS/E hitting its 2 GB ceiling, and a small number is
       the script exiting on its own. 'crash/hang' covers all three and
       distinguishes none of them."""
    if rc is None:
        return "still running"
    if rc == EXIT_LICENCE_BUSY:
        return ("rc=%d -- PSS/E COULD NOT START: the CodeMeter licence runtime was busy "
                "(relaunched after a pause; no scenario attempt charged)" % rc)
    NAMED = {
        0xC0000005: "ACCESS VIOLATION -- a model read or wrote memory it does not own",
        0xC0000017: "OUT OF MEMORY -- this PSS/E is 32-bit, so one session gets ~2 GB",
        0xC00000FD: "STACK OVERFLOW -- runaway recursion inside a model",
        0xC000013A: "Ctrl-C / console closed",
        0xC0000094: "INTEGER DIVIDE BY ZERO inside a model",
        0xC0000090: "FLOATING-POINT EXCEPTION inside a model",
        0xC0000374: "HEAP CORRUPTION -- a model wrote past the end of an array",
        0xC0000409: "STACK BUFFER OVERRUN",
    }
    u = rc & 0xFFFFFFFF                      # Python reports these as negative
    if u in NAMED:
        return "rc=%d (0x%08X) %s" % (rc, u, NAMED[u])
    if u >= 0xC0000000:
        return "rc=%d (0x%08X) -- a Windows fatal exception" % (rc, u)
    return "rc=%s -- exited on its own, no crash code" % rc


def _snapshot_psse_sink(i, why=""):
    """Keep PSS/E's own output from the moment a worker died.

       The engine sends PSS/E's console to logs\\_psse_sink_w<i>.txt, and the
       next launch of that worker slot OVERWRITES it. So the one file that
       holds PSS/E's last words about a crash -- "Network not converged",
       a model's message, a floating-point trap -- was gone by the time anyone
       looked: F15's three deaths on 2026-09-20 left a sink holding F22.

       Copied here, on every death, to logs\\CRASH_<scenario>_w<i>_<hhmmss>.txt
       (the last 4000 lines). The scenario is the one the study's PROGRESS
       rows show RUNNING on this worker."""
    try:
        src = os.path.join(LOGS_DIR, "_psse_sink_w%s.txt" % i)
        if not LOGS_DIR or not os.path.isfile(src) or os.path.getsize(src) == 0:
            return ""
        sid = ""
        try:
            sid, _age = _worker_scenario_age(int(i))
        except Exception:
            sid = ""
        dst = os.path.join(LOGS_DIR, "CRASH_%s_w%s_%s.txt"
                           % (sid or "unknown", i, time.strftime("%H%M%S")))
        with open(src, "r", errors="replace") as fh:
            lines = fh.readlines()
        with open(dst, "w") as fh:
            fh.write("PSS/E output of worker %s at the time it died -- %s\n" % (i, time.strftime("%Y-%m-%d %H:%M:%S")))
            fh.write("scenario: %s\nreason:   %s\n" % (sid or "(no RUNNING row for this worker)", why))
            fh.write("source:   %s (last %d of %d lines)\n" % (src, min(4000, len(lines)), len(lines)))
            fh.write("=" * 96 + "\n")
            fh.writelines(lines[-4000:])
        print("[parallel] worker %s: PSS/E output at the crash kept -> %s" % (i, dst))
        return dst
    except Exception as e:
        print("[parallel] worker %s: could not keep the PSS/E sink (%s)" % (i, e))
        return ""


def _worker_exit_note(i, text=""):
    """Remember, or recall, HOW a worker last died.

       The reason is printed on the console at the moment it happens --
       "rc=-1073741819 (0xC0000005) ACCESS VIOLATION" -- and the console is the
       one place a person does not still have hours later. The STATUS table and
       the run summary are what get read, and all they could say was

           GAVE-UP   4   attempted 4 time(s) >= MAX_SCENARIO_ATTEMPTS

       which names the counter and not one thing about the cause. A scenario
       given up after four identical crashes and one that ran out of retries on
       a slow machine are the same row, and the fix for them is nothing alike.

       Written to flags\ with the other bookkeeping, one file per worker, so it
       survives the launcher and can be read by the summary written afterwards."""
    p = os.path.join(_flags_dir(), "WORKER_EXIT_w%s.txt" % i)
    if text:
        try:
            with open(p, "w") as fh:
                fh.write("%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
        except Exception:
            pass
        _snapshot_psse_sink(i, text)
        return text
    try:
        with open(p) as fh:
            return (fh.read() or "").strip().split("\t")[-1]
    except Exception:
        return ""


def _quiet_watch(key, label, idle, limit, never_kill=None):
    """A watchdog fired. Kill, or report and let it work?

       True  = caller should kill (the old behaviour).
       False = caller must leave it alone; the notice has been printed here.

       never_kill = False OVERRIDES NEVER_KILL_WORKERS for this caller. It is
       for the phases that are a SINGLE process -- the build, and a report that
       is not sharded.

       WHY THAT DISTINCTION EXISTS, and what it cost before it did.
       NEVER_KILL_WORKERS is documented as being about worker slots: "a truly
       frozen worker holds its slot until you stop the run yourself" -- one slot
       out of N, and the other N-1 carry on. For a single-process phase there is
       no other slot. The launcher waits on `while p.poll() is None`, the poll
       loop takes its `continue` every time, and the pass never ends -- so every
       REMAINING PROJECT IN THE QUEUE never starts.
       That is the exact failure this file's own header describes ("THIS IS WHY
       ONLY IRONSTAR RAN ... One stuck report held four projects hostage") and
       the exact thing _run_phase's docstring promises not to do ("A phase that
       cannot finish must cost one timeout, not the whole run"). The promise was
       unreachable as shipped: the kill branch under it was dead code.
       The KILL_GRACE_S floor below still applies, so nothing dies early."""
    # THE FLOOR COMES FIRST. Before any setting is consulted, before
    # NEVER_KILL_WORKERS is even looked at: nothing dies inside KILL_GRACE_S of
    # silence. A watchdog whose own limit is shorter than the floor is simply
    # early, and being early is what killed healthy work.
    if idle < KILL_GRACE_S:
        return False
    if not (NEVER_KILL_WORKERS if never_kill is None else never_kill):
        return True
    # Say it once when it first goes past the limit, then once an hour after, so
    # a long quiet stretch stays visible without filling the console. Keyed on
    # WHEN IT WAS LAST SAID, not on a count: the watchdog is polled every few
    # seconds, so counting ticks says nothing about elapsed time.
    now = time.time()
    last = _QUIET_WARNED.get(key)
    if last is not None and (now - last) < 3600.0:
        return False
    _QUIET_WARNED[key] = now
    print("[parallel] %s has printed nothing for %s (past %ds) -- NOT killing it. "
          "NEVER_KILL_WORKERS is on: a long silent solve is normal on this machine, "
          "and it keeps its scenario until it finishes or exits on its own."
          % (label, _fmt_hms(idle), int(limit)))
    return False

PAUSE_BETWEEN    = 3         # seconds between a crash and a relaunch

# ---- PSS/E START FAILURES: THE CodeMeter LICENCE RUNTIME -------------------
# PSS/E takes its licence from the CodeMeter runtime service when psseinit()
# runs. Seven workers (plus the other case's seven) starting in the same second
# ask it fourteen times at once, and when it cannot keep up pssenng.dll shows a
# MODAL box -- "Start Error: CodeMeter runtime system is currently busy. Please
# try again later!" -- and waits for a click that never comes. The worker is
# alive, prints nothing, holds no scenario, and with NEVER_KILL_WORKERS on the
# launcher waited for it for ever: "running workers: [3]  idle(s): {3: 2053}"
# with 0 running scenarios. Clicking OK made it worse: the worker died, was
# relaunched PAUSE_BETWEEN seconds later, asked the same busy runtime again and
# showed the same box -- "occurring at all times".
#
# Four things fix that, and none of them touches a worker that holds a scenario:
#   LAUNCH_STAGGER_S   the first launch of worker i waits i x this many seconds
#                      before importing PSS/E, so the licence requests arrive
#                      one at a time instead of all at once.
#   CLOSE_PSSE_DIALOGS a sweeper thread closes any modal box that belongs to a
#                      process THIS launcher started (never anyone else's). A
#                      licence box means that PSS/E is not going to run: the
#                      worker is killed and relaunched after a pause.
#   LICENCE_BACKOFF_S  the pause before relaunching a worker whose PSS/E could
#                      not start, doubling on each repeat up to
#                      LICENCE_BACKOFF_MAX_S, so a busy runtime is left alone to
#                      recover instead of being hammered every 3 s.
#   STARTUP_SILENT_S   a worker that has printed nothing for this long AND holds
#                      no scenario claim is killed and relaunched. It is not
#                      running anything, so nothing is lost -- this is the one
#                      case where NEVER_KILL_WORKERS and KILL_GRACE_S do not
#                      apply, because their whole reason ("a long silent solve
#                      is normal") needs a scenario to be solving.
# The engine exits with rc=86 (EXIT_LICENCE_BUSY) when psseinit() itself reports
# the licence problem, which takes the same backoff path. None of these deaths
# charge a scenario attempt: the worker never reached a scenario.
LAUNCH_STAGGER_S      = 20.0
CLOSE_PSSE_DIALOGS    = True
LICENCE_BACKOFF_S     = 60.0
LICENCE_BACKOFF_MAX_S = 900.0
MAX_LICENCE_FAILS     = 30      # per worker; after this it gives up with a note on what to do
START_FAIL_MAX        = 5       # deaths at start with no licence evidence, in a row, before a slot gives up
STARTUP_SILENT_S      = 900.0   # 15 min silent with NO claim = stuck at start (dialog / dead licence)
OUT_MIN_BYTES         = 1048576  # an .out under this holds no samples (a header at most): not a finished run, whatever its marker says
KEEP_PARTIAL_RUNS     = str(os.environ.get("SPP_KEEP_PARTIAL", "1")).strip().lower() \
                        not in ("0", "false", "no", "off")
                                # a scenario with a scorable .partial run is kept, not simulated over
ONLY_MISSING_OUT      = str(os.environ.get("SPP_ONLY_MISSING_OUT", "0")).strip().lower() \
                        in ("1", "true", "yes", "on")
                                # simulate only the faults with no .out file at all
MAX_GROW_WORKERS      = 32      # hard cap on workers a handover may grow to (guards a stale/edited .spp_slots_*.txt)
STARTUP_DEAD_S        = 600.0   # died within this many s of launch with no claim = a start failure
                                # (was 180: the psseng.dll licence timeout is ~180 s, so
                                #  a licence death landed just outside it and was filed as a crash)
EXIT_LICENCE_BUSY     = 86
for _nm, _ev in (("LAUNCH_STAGGER_S", "SPP_LAUNCH_STAGGER_S"),
                 ("LICENCE_BACKOFF_S", "SPP_LICENCE_BACKOFF_S"),
                 ("LICENCE_BACKOFF_MAX_S", "SPP_LICENCE_BACKOFF_MAX_S"),
                 ("STARTUP_SILENT_S", "SPP_STARTUP_SILENT_S"),
                 ("MAX_LICENCE_FAILS", "SPP_MAX_LICENCE_FAILS")):
    try:
        _v = (os.environ.get(_ev) or "").strip()
        if _v:
            globals()[_nm] = float(_v)
    except Exception:
        pass
_envcd = (os.environ.get("SPP_CLOSE_DIALOGS") or "").strip().lower()
if _envcd in ("1", "true", "yes", "on"):
    CLOSE_PSSE_DIALOGS = True
elif _envcd in ("0", "false", "no", "off"):
    CLOSE_PSSE_DIALOGS = False

# ---- A SCENARIO MAY NOT RUN FOREVER ------------------------------------------
# HANG_TIMEOUT_S watches SILENCE, which is a proxy for stuck and not the thing
# itself. IronStar F166 sat on work5 for 18h 33m while six other workers had
# already retired, and the run made no progress for a whole day. Whatever it was
# doing, it was not a 24-minute solve: the slowest scenario ever measured on this
# machine is 24m 02s and the mean is 5m 14s.
#
# This is the hard ceiling, read from the study's own PROGRESS rows: a scenario
# that has been RUNNING longer than this is killed and requeued, whether or not
# its worker is printing. 4500 s is three times the slowest scenario ever seen,
# so nothing legitimate can reach it.
SCENARIO_MAX_S = 4500.0
try:
    SCENARIO_MAX_S = float((os.environ.get("SPP_SCENARIO_MAX_S") or "").strip()
                           or SCENARIO_MAX_S)
except Exception:
    pass
SCENARIO_SCAN_EVERY_S = 30.0    # how often the PROGRESS rows are re-read for this

# ---- A STRAGGLER THAT HAS RUN FAR LONGER THAN ANYTHING ELSE HERE -----------------
# SCENARIO_MAX_S and HANG_TIMEOUT_S are fixed numbers sized for the slowest
# scenario ever seen on the machine, so a hung PSS/E costs 45-75 min per attempt.
# On 2026-09-27 BASE F124 hung three times running on EastFork -- 2h 20m with
# one worker alive and 21 cores idle -- while the other 23 of its faults took
# 3-8 min each (the PROJECT side ran F124 in 17 min).
#
# This one is measured, not fixed: a scenario that has been running AND silent
# for longer than STRAGGLER_FACTOR x the slowest finished scenario in THIS
# results folder (never under STRAGGLER_MIN_S) is killed and requeued. It needs
# STRAGGLER_MIN_DONE finished scenarios before it acts, so a new folder with no
# timings falls back to the fixed watchdogs. 0 turns it off.
STRAGGLER_FACTOR   = 3.0
STRAGGLER_MIN_S    = 1200.0     # 20 min floor, whatever the timings say
STRAGGLER_MIN_DONE = 5
# ---- A HANG IS NOT A CRASH: IT COUNTS DOUBLE -------------------------------------
# A scenario the watchdog had to KILL (silent, overtime or straggler) is charged
# this many attempts instead of one. A crash is often the worker's luck (licence,
# memory); a hang on the same fault in the same case usually repeats -- F124
# hung identically three times. With 2 and MAX_SCENARIO_ATTEMPTS = 3 a hung
# scenario gets two tries, not three. 1 = the old behaviour.
HANG_ATTEMPTS = 2
for _nm, _ev in (("STRAGGLER_FACTOR", "SPP_STRAGGLER_FACTOR"),
                 ("STRAGGLER_MIN_S", "SPP_STRAGGLER_MIN_S"),
                 ("STRAGGLER_MIN_DONE", "SPP_STRAGGLER_MIN_DONE"),
                 ("HANG_ATTEMPTS", "SPP_HANG_ATTEMPTS")):
    try:
        _v = (os.environ.get(_ev) or "").strip()
        if _v:
            globals()[_nm] = float(_v)
    except Exception:
        pass
_STRAG = {"t": 0.0, "lim": 0.0}


def _straggler_limit():
    """Seconds a scenario may run silent before it is a straggler; 0 = no limit
       yet (too few finished scenarios to measure, or switched off)."""
    if not STRAGGLER_FACTOR or STRAGGLER_FACTOR <= 0 or not OUT_DIR:
        return 0.0
    now = time.time()
    if now - _STRAG["t"] < 60.0:
        return _STRAG["lim"]
    _STRAG["t"] = now
    try:
        ids = [os.path.splitext(os.path.basename(p))[0]
               for p in glob.glob(os.path.join(OUT_DIR, "*.secs"))]
        secs = _scenario_secs(ids)
    except Exception:
        secs = []
    if not secs or len(secs) < max(1, int(STRAGGLER_MIN_DONE)):
        _STRAG["lim"] = 0.0
    else:
        _STRAG["lim"] = max(float(STRAGGLER_MIN_S), float(STRAGGLER_FACTOR) * max(secs))
    return _STRAG["lim"]


def _charge_hang(sid):
    """A killed hang costs HANG_ATTEMPTS attempts. The worker already counted
       one when it claimed the scenario; add the rest to <sid>.attempts."""
    extra = int(HANG_ATTEMPTS) - 1
    if not sid or extra <= 0 or not OUT_DIR:
        return
    p = os.path.join(OUT_DIR, "%s.attempts" % sid)
    try:
        try:
            with open(p) as fh:
                n = int((fh.read() or "0").strip() or "0")
        except Exception:
            n = 0
        with open(p, "w") as fh:
            fh.write(str(n + extra))
        try:
            with open(os.path.join(OUT_DIR, "%s.hangs" % sid), "a") as fh:
                fh.write("%s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        except Exception:
            pass
        print("[parallel] %s: the kill counts as %d attempt(s) (HANG_ATTEMPTS) -- now %d"
              % (sid, int(HANG_ATTEMPTS), n + extra))
    except Exception as e:
        print("[parallel] %s: could not charge the hang (%s)" % (sid, e))


# ---- SHARING THE MACHINE WITH SCORING THAT RUNS WHILE THIS SIMULATES --------
# z7_main.py scores a project as soon as both cases have finished simulating
# it, on the cores the workers are not using (SCORE_WHILE_SIMULATING) -- so a
# straggler holding one worker no longer leaves 21 cores idle. Three files
# beside the cases keep the two from overrunning CORES_MAX:
#   SPP_ALIVE_FILE        this launcher writes how many workers are alive
#   SPP_SCORING_BUSY_FILE the panel writes how many scoring shards it runs
#   SPP_CORES_CEILING     the total both may use together
# A new project starts, and a handover grows, only into what is left. When the
# scoring ends the workers grow back (see _maybe_grow). No file = no limit.
_ALIVE_FILE   = (os.environ.get("SPP_ALIVE_FILE") or "").strip()
_BUSY_FILE    = (os.environ.get("SPP_SCORING_BUSY_FILE") or "").strip()
try:
    _CORES_CEILING = int(float((os.environ.get("SPP_CORES_CEILING") or "0").strip() or "0"))
except Exception:
    _CORES_CEILING = 0
_SHARE_FRESH_S = 150.0          # a file not rewritten for this long is ignored
_ALIVE_LAST = [0.0, -1]


def _write_alive(k, force=False):
    """Tell the panel how many workers this launcher has alive."""
    if not _ALIVE_FILE:
        return
    now = time.time()
    if not force and k == _ALIVE_LAST[1] and now - _ALIVE_LAST[0] < 20.0:
        return
    try:
        with open(_ALIVE_FILE, "w") as fh:
            fh.write("%d\n" % int(k))
        _ALIVE_LAST[0], _ALIVE_LAST[1] = now, k
    except Exception:
        pass


def _read_count(p):
    """An int from a share file that is still being kept up; 0 otherwise."""
    try:
        if p and os.path.isfile(p) and time.time() - os.path.getmtime(p) < _SHARE_FRESH_S:
            with open(p) as fh:
                return max(0, int((fh.read() or "0").strip() or "0"))
    except Exception:
        pass
    return 0


def _sim_allowed():
    """How many workers this launcher may have alive now; None = no limit."""
    if not _CORES_CEILING or not _BUSY_FILE:
        return None
    busy = _read_count(_BUSY_FILE)
    other = 0
    if _ALIVE_FILE:
        for p in glob.glob(os.path.join(os.path.dirname(_ALIVE_FILE), ".spp_alive_*.txt")):
            if os.path.normcase(os.path.abspath(p)) != os.path.normcase(os.path.abspath(_ALIVE_FILE)):
                other += _read_count(p)
    return max(1, _CORES_CEILING - busy - other)

# ---- A LICENCE OUTAGE MAY NOT RETIRE A WORKER SLOT ---------------------------
# _schedule() used to do done.add(i) once a worker had passed MAX_LICENCE_FAILS,
# which abandons that worker's share of the queue for the rest of the launch. On
# 2026-09-16 the CodeMeter runtime started answering
#   "A network error occurred, Error 100"
# and six of the seven workers walked through their 30 failures and retired --
# permanently, while the licence server came back minutes later. Nothing was
# left to notice.
#
# A licence outage is a condition of the machine, not a verdict on the slot. With
# LICENCE_RETIRE False the worker is parked for LICENCE_COOLDOWN_S, its failure
# count is cleared, and it is launched again. It can do that forever, because the
# alternative -- a dead slot -- is strictly worse.
LICENCE_RETIRE     = False
LICENCE_COOLDOWN_S = 600.0      # 10 min parked, then the slot tries again
try:
    LICENCE_COOLDOWN_S = float((os.environ.get("SPP_LICENCE_COOLDOWN_S") or "").strip()
                               or LICENCE_COOLDOWN_S)
except Exception:
    pass

# ---- HOW OFTEN PSS/E MAY BE ASKED FOR A LICENCE ------------------------------
# Every worker relaunch is a fresh psseinit(), and a fresh psseinit() is a
# CodeMeter checkout. The 2026-09-16 run had used W0=308, W1=307, W2=305, W3=307,
# W4=311, W6=305 relaunches for ~110 finished scenarios -- about 2.8 process
# starts per scenario -- and the BASE and PROJECT launchers were both doing it at
# 7 workers each. That is roughly 170 checkouts an hour against one network
# licence server, which is what Error 100 is: not a missing licence, a flooded
# one.
#
# This throttles process STARTS across BOTH launchers through a lock directory
# beside the cases, so base and project share one budget instead of each keeping
# its own. It costs nothing when starts are rare and only bites during a relaunch
# storm, which is exactly when the server is drowning.
#
# 0 disables the gate.
LICENCE_STARTS_PER_MIN  = 6
LICENCE_GATE_MAX_WAIT_S = 300.0   # never hold a launch longer than this
try:
    LICENCE_STARTS_PER_MIN = int((os.environ.get("SPP_LICENCE_STARTS_PER_MIN") or "").strip()
                                 or LICENCE_STARTS_PER_MIN)
except Exception:
    pass
_LIC_GATE_DIR = os.path.join(os.path.dirname(os.path.normpath(STUDY_DIR)),
                             ".spp_licence_gate")


def _licence_gate(tag):
    """Wait until this process may start a PSS/E child, across BOTH launchers.

       A token bucket of LICENCE_STARTS_PER_MIN starts per rolling minute, kept
       in a file guarded by an atomically-created lock DIRECTORY (os.mkdir is
       atomic on Windows and POSIX alike; Python 3.4 on Windows has no fcntl).

       EVERY FAILURE PATH FALLS THROUGH AND LETS THE LAUNCH PROCEED. A gate that
       can stop the run is worse than no gate: this may only ever slow starts
       down, never block them."""
    if LICENCE_STARTS_PER_MIN <= 0:
        return
    lock = os.path.join(_LIC_GATE_DIR, "lock")
    stamps_p = os.path.join(_LIC_GATE_DIR, "starts.txt")
    try:
        if not os.path.isdir(_LIC_GATE_DIR):
            os.makedirs(_LIC_GATE_DIR)
    except Exception:
        return
    deadline = time.time() + LICENCE_GATE_MAX_WAIT_S
    said = False
    while True:
        got = False
        for _ in range(240):                       # up to ~60 s for the mutex
            try:
                os.mkdir(lock); got = True; break
            except Exception:
                # A LAUNCHER THAT DIED HOLDING THE MUTEX MUST NOT WEDGE THE REST.
                try:
                    if time.time() - os.path.getmtime(lock) > 120.0:
                        os.rmdir(lock); continue
                except Exception:
                    pass
                time.sleep(0.25)
        if not got:
            return                                  # cannot coordinate -- go
        try:
            now = time.time()
            try:
                with open(stamps_p) as fh:
                    stamps = [float(x) for x in fh.read().split() if x.strip()]
            except Exception:
                stamps = []
            stamps = [t for t in stamps if 0 <= now - t < 60.0]
            if len(stamps) < LICENCE_STARTS_PER_MIN:
                stamps.append(now)
                try:
                    with open(stamps_p, "w") as fh:
                        fh.write("\n".join("%.3f" % t for t in stamps))
                except Exception:
                    pass
                return
            wait = 60.0 - (now - min(stamps)) + 0.5
        finally:
            try: os.rmdir(lock)
            except Exception: pass
        if time.time() >= deadline:
            return
        if not said:
            said = True
            print("[parallel] %s: holding this launch -- %d PSS/E start(s) already in the "
                  "last minute across both cases (LICENCE_STARTS_PER_MIN=%d)"
                  % (tag, LICENCE_STARTS_PER_MIN, LICENCE_STARTS_PER_MIN))
        time.sleep(max(1.0, min(wait, 20.0)))


# ---- A SECOND CHANCE FOR SCENARIOS THAT GAVE UP -----------------------------
# A scenario is GAVE-UP when MAX_SCENARIO_ATTEMPTS workers died while holding
# it. On this machine most of those deaths were the WORKER's -- an access
# violation after the .out, a licence that went away, a 2 GB ceiling -- not
# the scenario's, and the six that gave up at 03:37 had simply been unlucky
# four times. With RETRY_GAVE_UP_ROUNDS > 0 the launcher, after the workers
# finish, resets the attempts of every scenario that gave up and runs them once
# more with RETRY_WORKERS workers and RETRY_ATTEMPTS attempts each. On a resumed
# launch the scenarios that gave up LAST time get the same reset up front.
# 0 restores the old behaviour (they stay written off).
RETRY_GAVE_UP_ROUNDS = 1
RETRY_WORKERS        = 2
RETRY_ATTEMPTS       = 2
try:
    RETRY_GAVE_UP_ROUNDS = int((os.environ.get("SPP_RETRY_GAVE_UP_ROUNDS") or "").strip()
                               or RETRY_GAVE_UP_ROUNDS)
except Exception:
    pass
POLL_SECS        = 5         # how often to poll the running workers
SLOTS_CHECK_S    = 30        # how often to look for a HANDOVER target (SPP_SLOTS_FILE) from the panel
_LAUNCHER_T0     = time.time()   # a handover file older than this process is stale and ignored
# THE CATCH-UP PLOT PASS HAS A CLOCK. It is one child process the launcher waits
# for between the last worker and the report, and it had no limit at all: a
# plotter that wedged -- a modal dialog, a reader stuck in a 2 GB address space
# -- held the whole queue. One did, for five and a half hours, and the two
# projects behind it were never started. Nothing the pass does is needed to go
# on: the report phase scores from the .out files itself, and the panel's plot
# pass redraws whatever is missing. So it is killed when it goes quiet, or when
# it has simply had long enough, and the queue continues.
PLOT_CATCHUP_STALL_S = 900   # kill the catch-up plotter if it prints NOTHING for this long (15 min)
PLOT_CATCHUP_MAX_S   = 3600  # ...or when it has run this long in total, whatever it is printing
HANG_TIMEOUT_S   = 2700      # if a worker prints NOTHING for this many seconds it is treated as
                             # HUNG (a PSS/E crash that popped a Windows error dialog / Fortran
                             # pause and never exited -- the "worker still open, no summary" case).
                             # The launcher then KILLS it and relaunches/gives up, so the run ALWAYS
                             # finishes and RUN_SUMMARY is written. Raise if a single long fault can
                             # legitimately be silent this long; lower to catch hangs sooner.
PYTHON           = sys.executable

# --- RUN ONLY SELECTED FAULTS ----------------------------------------------
# Empty list = normal full study, exactly as before.
# Non-empty  = run ONLY these faults, taken from the fault definition file
#              results\dynamics\faults\SPP_FAULTS.csv. Ranges expand.
#
#     RUN_ONLY_FAULTS = ["F01"]                     one fault
#     RUN_ONLY_FAULTS = ["F01", "F07", "F19"]       several
#     RUN_ONLY_FAULTS = ["F19-F24"]                 a range
#     RUN_ONLY_FAULTS = ["FLAT_RUN"]                the flat case
#
# It also understands these KEYWORDS, resolved from RUN_SUMMARY.csv and the
# .done/.attempts markers of the previous run -- so you can re-run exactly what
# went wrong without typing ids:
#     RUN_ONLY_FAULTS = ["CRASHED"]     gave up after MAX_SCENARIO_ATTEMPTS
#     RUN_ONLY_FAULTS = ["NOTDONE"]     every scenario without a .done marker
#     RUN_ONLY_FAULTS = ["NONCONV"]     needed the solver-retry ladder
#     RUN_ONLY_FAULTS = ["FAIL"]        scored FAIL against the SPP criteria
#     RUN_ONLY_FAULTS = ["NONCONV", "F07"]        keywords and ids mix freely
#
# When this is set the launcher:
#   * does NOT clear every .done/.attempts marker, even with FRESH_START = True
#     -- it clears the markers for the SELECTED ids only, so the runs you already
#     finished are left alone
#   * SKIPS the build phase when the snapshot and the fault file already exist,
#     so a selective run starts in seconds instead of rebuilding
#   * splits the selected ids across the workers and passes each its own slice NOTDONE
#
# >>> IDS ARE PER FAULT MODE. The SPP set is F01, F02, ...; the CUSTOM set is
#     C01_3PH_560080_9cy, C02_SLG_560080_9cy, ... A leftover ["F01-F50"] with
#     FAULT_MODES = ["custom"] therefore selects NOTHING, and the launcher stops
#     the whole run rather than quietly running all of it. Leave this EMPTY for a
#     full custom run; use it afterwards, with the ids the run actually produced,
#     to re-run the handful that failed.
RUN_ONLY_FAULTS  = []            # <<< empty = run all; non-empty = run only these ids/ranges/keywords

# --- RUN / SCORE ONLY SOME PLANNING EVENTS ----------------------------------
# [] = every event. Non-empty = only the faults whose planning event matches,
# applied to BOTH the run selection and the report selection:
#     ONLY_EVENTS = ["P1.2"]              only the 3ph line faults
#     ONLY_EVENTS = ["P1.2", "P1.3"]      lines and transformers
#     ONLY_EVENTS = ["P1"]                every P1 sub-event
#     ONLY_EVENTS = ["P6"]
#
# NOT the same setting as SPP_EVENTS in the study script. SPP_EVENTS decides
# what the generator PUTS IN the fault list; this decides which of what is
# already in it gets run. Switching P4.2 off in SPP_EVENTS and regenerating
# throws the P4.2 results away -- selecting P1.2 here leaves them on disk.
#
# Combines with RUN_ONLY_FAULTS by INTERSECTION: ids F01-F40 plus events P1.3
# means the P1.3 faults among F01-F40.
ONLY_EVENTS = []
_enve = (os.environ.get("SPP_ONLY_EVENTS") or "").strip()
if _enve:
    ONLY_EVENTS = [x.strip() for x in _enve.split(",") if x.strip()]
    print("[parallel] ONLY_EVENTS from the environment: %s" % ", ".join(ONLY_EVENTS))


# --- RUN ONLY WHAT HAS NOT RUN YET ------------------------------------------
# True  = before starting, drop every scenario that already has a .done marker,
#         so a re-run continues where the last one stopped instead of repeating
#         hours of finished work. Combines with ONLY_EVENTS and RUN_ONLY_FAULTS
#         by intersection: "the P1.2 faults that have not run yet".
# False = the old behaviour. The .done markers still make individual scenarios
#         skip themselves, so nothing is re-simulated either way; this decides
#         whether they are EXCLUDED UP FRONT, which is what makes the worker
#         deal even -- otherwise one worker can be handed a share that is
#         entirely finished and exit immediately while another has 20 to run.
#
# Scenarios that GAVE UP have no .done marker, so they are selected again. They
# still carry their .attempts count, so they are skipped by the attempt cap
# unless RETRY_GAVE_UP is on -- the count of them is printed either way.
SKIP_DONE = True
_envs = (os.environ.get("SPP_SKIP_DONE") or "").strip().lower()
if _envs in ("1", "true", "yes", "on"):
    SKIP_DONE = True
    print("[parallel] SKIP_DONE forced ON from the environment")
elif _envs in ("0", "false", "no", "off"):
    SKIP_DONE = False
    print("[parallel] SKIP_DONE forced OFF from the environment")


# >>> OVERRIDABLE FROM THE ENVIRONMENT, so z7_main.py can restrict BOTH
# cases to the same handful of faults without either launcher being edited.
# Editing one launcher to run three faults and forgetting to edit the other is
# how the two cases end up having run different sets -- and the comparison would
# then report the difference as "the projects introduced it".
#     set SPP_ONLY_FAULTS=F03,F07
#     set SPP_ONLY_FAULTS=F19-F24
#     set SPP_ONLY_FAULTS=                cleared -- the list above is used
_envo = (os.environ.get("SPP_ONLY_FAULTS") or "").strip()
if _envo:
    RUN_ONLY_FAULTS = [x.strip() for x in _envo.split(",") if x.strip()]
    print("[parallel] RUN_ONLY_FAULTS from the environment: %s"
          % ", ".join(RUN_ONLY_FAULTS))

# --- LIVE STATUS WHILE THE RUN IS GOING -------------------------------------
# RUN_SUMMARY.txt is written only by the REPORT phase, i.e. after every worker has
# finished. This prints a rolling table every LIVE_STATUS_EVERY seconds -- what is
# running, what finished, what crashed, attempts used, relaunches spent -- and
# writes the same table to LIVE_STATUS.txt so you can open it from another window
# mid-run. 0 disables it.
LIVE_STATUS_EVERY = 60      # seconds between live status tables (0 = off)
LIVE_STATUS_ROWS  = 0       # 0 = list every scenario; N = only the first N rows
# The CONSOLE table lists only scenarios that have started -- running, re-queued,
# done, crashed -- and counts the rest on one line. LIVE_STATUS.txt still holds
# every row. False prints the full list to the console too.
LIVE_STATUS_QUIET_ROWS = True

# What to do about the fault definition file (SPP_FAULTS.csv):
#   "if-missing" regenerate it only when it is absent          (default)
#   "always"     regenerate it every run
#   "never"      only the build phase ever writes it           (old behaviour)
#
# "if-missing" is the right setting for a multi-project run: each project gets a
# NEW results folder, so its fault file does not exist yet. "never" would leave
# the workers with no fault definitions to read and RUN_SUMMARY with no rows.
REGEN_FAULTS     = "if-missing"  # <<< "if-missing" | "always" | "never"
# AND THE PANEL CAN SAY OTHERWISE.
#
# z7_main.py has a REGEN_FAULTS setting and pushes it as SPP_REGEN_FAULTS,
# and this file then wrote its OWN constant into the environment of every build,
# work and report process it spawned -- so the panel's value could never reach
# the study. RUN_PROJECTS and FAULT_MODES, declared a few lines from here, both
# take the override; this one was simply missed.
#
# The default was also "never", against the advice of the comment directly above
# it: with a new results folder per project the fault file does not exist yet,
# and "never" leaves the workers with no fault definitions to read and
# RUN_SUMMARY with no rows.
_envrf = (os.environ.get("SPP_REGEN_FAULTS") or "").strip().lower()
if _envrf in ("if-missing", "always", "never"):
    REGEN_FAULTS = _envrf
    print("[parallel] REGEN_FAULTS from the environment: %s" % REGEN_FAULTS)
elif _envrf:
    print("[parallel] SPP_REGEN_FAULTS = %r is not one of if-missing/always/never "
          "-- keeping %r" % (_envrf, REGEN_FAULTS))
# ---------------------------------------------------------------------------

# --- WHERE THE STUDY WRITES ITS RESULTS -------------------------------------
# The study script puts results in  results\dynamics  normally, but in
#   results\BESS_<project>_<MW>MW   when ENABLE_BESS = True.
# The launcher MUST look in the same place or it will never see the workers'
# ALL_DONE sentinels and will report a successful phase as a failure.
#   "auto"  read ENABLE_BESS / ACTIVE_PROJECT / the project's MW out of the study
#           script and build the same folder name it does        (recommended)
#   "dynamics" or an explicit "BESS_IronStar_214MW" to pin it by hand
RESULTS_SUBDIR = "auto"

# ===========================================================================
# >>> WHICH PROJECT(S), AND WHICH FAULT SET
# ---------------------------------------------------------------------------
# RUN_PROJECTS names one or more entries from BESS_PROJECTS in the study
# script. Each is run as a COMPLETE study of its own -- build, workers, report
# -- one after another, into its own results folder:
#
#     results\IronStar_spp\      results\EastFork_custom\
#
# Separate folders rather than one merged run, because each project's SPP
# report is a separate deliverable and a shared folder would have the second
# project's .out files overwrite the first's.
#
# [] = the old behaviour: run once, with whatever RUN_PROJECT the study script
# has set, into results\dynamics.
RUN_PROJECTS = ["EastFork", "SantaFe", "IronStar", "EmpirePrairie"]   # EastFork first; the panel passes the same order to both launchers
# A PROJECT WHOSE PASS ENDS BADLY STOPS THE LAUNCHER. Before, "a failure in one
# does not stop the rest": a pass that died in its first minute (a build error)
# was followed at once by the next project, so a project launcher that failed
# on EastFork was seen "moving to SantaFe" while the base launcher was still on
# EastFork. Off = the old behaviour.
STOP_ON_FAILED_PROJECT = True

# >>> OVERRIDABLE FROM THE ENVIRONMENT.
# run_compare.py works out which projects are missing from which case and needs
# to run exactly those, without editing this file for each attempt -- editing a
# launcher to run one project, then editing it back, is how the two studies end
# up having run different sets.
#     set SPP_RUN_PROJECTS=EastFork,IronStar
# Unset or empty leaves the list above exactly as written.
_envp = (os.environ.get("SPP_RUN_PROJECTS") or "").strip()
if _envp:
    RUN_PROJECTS = [x.strip() for x in _envp.split(",") if x.strip()]
    print("[parallel] RUN_PROJECTS from the environment: %s" % ", ".join(RUN_PROJECTS))


#["IronStar", "EastFork", "SantaFe", "EmpirePrairie"]

# "spp"    the SPP BP-7250 set generated around each project's POI
# "custom" 3PH/SLG at the buses near the POI, per CUSTOM_* in the study script
# "manual" the hand-written FAULTS list in the study script
# A list runs each mode in turn for every project, each into its own folder:
#     FAULT_MODES = ["spp", "custom"]   ->  4 studies for 2 projects
FAULT_MODES = ["spp"]
_envm = (os.environ.get("SPP_RUN_MODES") or "").strip()
if _envm:
    FAULT_MODES = [x.strip() for x in _envm.split(",") if x.strip()]
    print("[parallel] FAULT_MODES from the environment: %s" % ", ".join(FAULT_MODES))


# Set for the duration of one project/mode pass, and read by _env() and by
# _study_results_subdir() so the launcher and the study script agree on where
# the results go. They MUST agree: the launcher watches for the workers'
# sentinels in that folder, and a mismatch makes a successful run look failed.
#
# These live HERE, above the two functions that read them, because
# _set_paths() is called at import time -- further down, but still before the
# old position of these two lines. That ordering is what raised
#     NameError: name '_CUR_PROJECT' is not defined
# on the very first import. A module-level name has to be bound before the
# module-level code that reads it runs, not merely before the function that
# reads it is called.
_CUR_PROJECT = ""
_CUR_MODE    = ""


def _study_results_subdir():
    """The study script's results sub-folder, WORKED OUT THE SAME WAY IT DOES.

       INCLUDING THE RUN SUFFIX. This returned "<project>_<mode>" and nothing
       else, while the study appends SPP_CAP_TAG / SPP_RUN_TAG -- so during a
       capacity or .dyr sweep the study wrote into

           results\SantaFe_custom_dyr_Kqv0\

       and this launcher went on watching

           results\SantaFe_custom\

       Everything the launcher does with that path then referred to the wrong
       run: its console log went to the untagged folder (which is why the swept
       folders had none), the fault file it read was the previous run's, and --
       the damaging one -- the ALL_DONE_w*.flag sentinels left there by the main
       run said the WORK PHASE WAS ALREADY FINISHED. So each swept value built
       its snapshot, ran the flat run, skipped every fault, and scored a report
       containing FLAT_RUN and nothing else. That is exactly the column of "?"
       the sweep table showed."""
    sub = _study_results_subdir_base()
    cap = (os.environ.get("SPP_CAP_TAG") or "").strip()
    run = (os.environ.get("SPP_RUN_TAG") or "").strip()
    if cap:
        sub += "_cap%s" % cap
    if run:
        sub += "_%s" % run
    return sub


def _study_results_subdir_base():
    """Work out the study script's results sub-folder the same way it does."""
    if RESULTS_SUBDIR != "auto":
        return RESULTS_SUBDIR
    # Same rule as the study script: a named project puts the results in
    # "<project>_<mode>". Keep the two in step -- the launcher looks for the
    # workers' sentinels here.
    if _CUR_PROJECT:
        return "%s_%s" % (_CUR_PROJECT, _CUR_MODE or "spp")
    try:
        txt = open(STUDY_SCRIPT, errors="replace").read()
    except Exception as e:
        print("[parallel] could not read %s (%s) -- assuming 'dynamics'" % (STUDY_SCRIPT, e))
        return "dynamics"

    def val(name):
        m = re.search(r"(?m)^%s\s*=\s*(.+?)\s*(?:#.*)?$" % name, txt)
        return m.group(1).strip() if m else None

    if (val("ENABLE_BESS") or "").lower() != "true":
        return "dynamics"
    proj = (val("ACTIVE_PROJECT") or "").strip("\"'")
    if not proj:
        print("[parallel] ENABLE_BESS is on but ACTIVE_PROJECT is unreadable -- using 'dynamics'")
        return "dynamics"
    m = re.search(r'"name"\s*:\s*"%s"[^}]*"mw"\s*:\s*(\[[^\]]*\]|[0-9.]+)' % re.escape(proj), txt)
    if not m:
        print("[parallel] could not find %s's MW in BESS_PROJECTS -- using 'dynamics'" % proj)
        return "dynamics"
    raw = m.group(1)
    if raw.startswith("["):
        sizes = [float(x) for x in re.findall(r"[0-9.]+", raw)]
        req = val("ACTIVE_MW")
        try:
            req = float(req)
        except (TypeError, ValueError):
            req = None
        mw = req if (req in sizes) else (sizes[0] if sizes else 0)
    else:
        mw = float(raw)
    return "BESS_%s_%dMW" % (proj, int(mw))


# ---- WHICH CASE'S RESULTS FOLDER --------------------------------------------
# Base and Projects each hold a "results" folder, so two windows open side by
# side look identical and a path pasted into a message says nothing about which
# case it came from. With this on, the folder is named for its case:
#
#     Base\results_base\SantaFe_spp\      Projects\results_proj\SantaFe_spp\
#
# BACKWARD COMPATIBLE ON PURPOSE. A study already on disk lives in "results",
# and renaming it out from under a half-finished run would orphan every .out,
# part and report in it. So: use results_<kind> when it exists, or when there is
# no plain "results" to use; otherwise keep using the one that is there. To
# adopt the new names on an existing study, rename the folder once --
# Base\results -> Base\results_base, Projects\results -> Projects\results_proj
# -- and everything picks it up with no other change.
RESULTS_FOLDER_BY_CASE = True
_RES_KIND = "proj"


def _results_root():
    """<study dir>\results_base | results_proj, or the plain "results" already
       on disk. One rule, used by every path in this file."""
    # THE LITERAL NAME, NOT _results_root(), on both of these lines. They are
    # inside the helper: calling it here is calling itself, and the run dies
    # with "maximum recursion depth exceeded" before it has read anything.
    _plain = os.path.join(STUDY_DIR, "results")
    if not RESULTS_FOLDER_BY_CASE:
        return _plain
    _new = os.path.join(STUDY_DIR, "results_%s" % _RES_KIND)
    if os.path.isdir(_new) or not os.path.isdir(_plain):
        return _new
    return _plain


# ---- ONE FOLDER PER PROJECT (the same rule as z7_main.py and the study) ----
# A project's run folders sit in <results root>\<project>\ under their own
# names -- used when that folder exists, or when the project has no run
# folder loose in the results root yet; a project still laid out flat stays
# flat until z7_main.py's TIDY_RESULTS moves it.
def _is_proj_box(d):
    """A project's folder, not a run folder: no outs\ of its own and at
       least one '<name>_...' run folder inside."""
    if not os.path.isdir(d) or os.path.isdir(os.path.join(d, "outs")):
        return False
    nm = os.path.basename(os.path.normpath(d))
    return os.path.isdir(os.path.join(d, "gen_test")) or \
        any(os.path.isdir(x) for x in glob.glob(os.path.join(d, glob.escape(nm) + "_*")))


def _proj_root(root, proj):
    """Where this project's run folders are: root\<proj>, or root itself for
       a project still laid out flat."""
    if not proj:
        return root
    box = os.path.join(root, proj)
    if os.path.isdir(box):
        return box
    if any(os.path.isdir(d) and not _is_proj_box(d)
           for d in glob.glob(os.path.join(root, glob.escape(proj) + "_*"))):
        return root
    return box


def _run_glob(root, pat):
    """Run folders matching pat in BOTH layouts: root\pat (project folders
       left out) and root\<proj>\pat (names starting '<proj>_')."""
    out = [d for d in glob.glob(os.path.join(root, pat)) if not _is_proj_box(d)]
    for d in glob.glob(os.path.join(root, "*", pat)):
        par = os.path.basename(os.path.dirname(d))
        if os.path.basename(d).startswith(par + "_") and _is_proj_box(os.path.dirname(d)):
            out.append(d)
    # sorted gen-test runs: root\<proj>\gen_test\<side>\<kind>\<run>
    for d in glob.glob(os.path.join(root, "*", "gen_test", "*", "*", pat)):
        box = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(d))))
        if os.path.basename(d).startswith(os.path.basename(box) + "_") and os.path.isdir(d):
            out.append(d)
    return sorted(out)


# ---- GEN-TEST RUNS SORTED BY KIND (the same rule as z7_main.py) -------------
# <project>\gen_test\POI_ON|POI_OFF\<kind>\<run>, decided from the run
# folder's unchanged name; a run still at the top of the project's folder is
# used there until z7_main.py's TIDY_RESULTS moves it.
GT_SORT_DIR = "gen_test"
_GT_NAME_RX = re.compile(r"^.+?_gt_(?P<sc>.+?)(?P<poi>_poioff)?"
                         r"(?:_off(?P<bus>\d+)_(?P<id>[A-Za-z0-9]+))?$")
_GT_ASIDE_RX = re.compile(r"(_prev_\d.*|_before_fixed_.*|__run\d+|\.[^.]*\.old)$")


def _run_group(name):
    m = _GT_NAME_RX.match(_GT_ASIDE_RX.sub("", str(name)))
    if not m:
        return ""
    i = m.group("id") or ""
    if not i:
        return os.path.join(GT_SORT_DIR, "POI_ON", "00_REFERENCE")
    if i == "POIALL" and not m.group("poi"):
        return os.path.join(GT_SORT_DIR, "POI_OFF", "00_POIGENOFF")
    side = "POI_OFF" if m.group("poi") else "POI_ON"
    kind = ("MODEL_EDIT" if i.startswith("EGF") else
            "CAPS_OFF" if i.startswith("CAP") else
            "LINE_OFF" if re.match(r"BR\d+(T\d+)?C", i) else
            "GEN_OFF")
    return os.path.join(GT_SORT_DIR, side, kind)


def _run_path(root, proj, name):
    """The run folder: <project folder>\\[<group>\\]name, or root\\name for a
       project still laid out flat."""
    base = _proj_root(root, proj)
    if not proj or os.path.normcase(os.path.abspath(base)) == os.path.normcase(os.path.abspath(root)):
        return os.path.join(base, name)
    grp = _run_group(name)
    if not grp:
        return os.path.join(base, name)
    new, old = os.path.join(base, grp, name), os.path.join(base, name)
    # a path that would leave under ~90 characters for the files inside
    # (Windows allows 260) stays unsorted -- same answer in every script
    if len(os.path.abspath(new)) + 90 > 250:
        return old
    return old if (os.path.isdir(old) and not os.path.isdir(new)) else new


def _set_paths():
    """Recompute the results paths for the project/mode now being run.

       They were module constants fixed at import, which is fine for one study
       and wrong for several: every pass would write into the first project's
       folder. RESULTS and friends stay module-level names so nothing else in
       this file changes."""
    global RESULTS, OUT_DIR, LOGS_DIR, CONSOLE_LOG


    RESULTS  = (_run_path(_results_root(), _CUR_PROJECT, _study_results_subdir())
                if (_CUR_PROJECT and RESULTS_SUBDIR == "auto") else
                os.path.join(_results_root(), _study_results_subdir()))
    OUT_DIR  = os.path.join(RESULTS, "outs")
    LOGS_DIR = os.path.join(RESULTS, "logs")
    CONSOLE_LOG = os.path.join(LOGS_DIR, "parallel_console.log")
    return RESULTS


RESULTS = OUT_DIR = LOGS_DIR = CONSOLE_LOG = ""
_set_paths()


def _banner(msg):
    print("\n" + "=" * 72); print(" [parallel] " + msg); print("=" * 72)


_PHASES = []          # [name, started, finished] -- wall clock per phase of this launch


def _phase(name=None):
    """Close the open phase; open `name` (None = just close)."""
    now = time.time()
    if _PHASES and _PHASES[-1][2] is None:
        _PHASES[-1][2] = now
    if name:
        _PHASES.append([name, now, None])


def _phase_report(where=""):
    """WHERE THE TIME WENT: one line per phase, printed and appended to
       RESULTS\PHASE_TIMES.txt. Simulation, the plot/score catch-up, the
       report shards and the merge are different problems with different
       fixes, and one total hides which of them a launch spent its hours on."""
    _phase(None)
    if not _PHASES:
        return
    lines = [" PHASE TIMES%s" % ((" -- " + where) if where else "")]
    tot = 0.0
    for _n, _a, _b in _PHASES:
        _d = (_b or time.time()) - _a
        tot += _d
        lines.append("   %-28s %10s   (%s -> %s)"
                     % (_n, _fmt_hms(_d), time.strftime("%H:%M:%S", time.localtime(_a)),
                        time.strftime("%H:%M:%S", time.localtime(_b or time.time()))))
    lines.append("   %-28s %10s" % ("total", _fmt_hms(tot)))
    print("")
    for _l in lines:
        print("[parallel]" + _l)
    print("")
    try:
        with open(os.path.join(RESULTS, "PHASE_TIMES.txt"), "a") as fh:
            fh.write("%s\n%s\n\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), "\n".join(lines)))
    except Exception:
        pass


def _phase_line():
    """One line for the live output: every phase so far and how long it took,
       the open one with 'so far'. Printed with each progress tick so the split
       between simulating, plotting and reporting is visible DURING the run."""
    if not _PHASES:
        return ""
    parts = []
    for _n, _a, _b in _PHASES:
        if _b is None:
            parts.append("%s %s so far" % (_n, _fmt_hms(time.time() - _a)))
        else:
            parts.append("%s %s" % (_n, _fmt_hms(_b - _a)))
    return "phases: " + " | ".join(parts)


def _fmt_hms(sec):
    sec = int(round(sec)); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    if h: return "%dh %02dm %02ds" % (h, m, s)
    if m: return "%dm %02ds" % (m, s)
    return "%ds" % s


FLAGS_SUBDIR   = "flags"
REPORTS_SUBDIR = "reports"

# WHICH CASE THIS LAUNCHER RUNS, in every filename it writes. A base result and
# a project result in two folders under identical names are two files whose
# provenance you have to remember, and remembering it wrongly once is a
# comparison that says the project fixed something the base never had.
RUN_KIND = "PROJ"


def _root_report(head, ext="txt"):
    """A file that stays in the ROOT of the results folder, numbered.

       Only the two that get read WHILE a study is running live here --
       00_STATUS and 01_PLAN -- with the study's own 02_VIOLATIONS. Everything
       else is a result to read afterwards and goes to reports\."""
    return os.path.join(RESULTS, "%s_%s%s.%s"
                        % (head, RUN_KIND,
                            ("_%s" % _CUR_PROJECT) if _CUR_PROJECT else "", ext))


def _flags_dir():
    d = os.path.join(RESULTS, FLAGS_SUBDIR)
    try:
        os.makedirs(d)
    except Exception:
        pass
    return d


def _sentinel(suffix):
    """Where this run's ALL_DONE marker goes: flags\, with the rest of the
       markers, so the root of a results folder holds reports and not
       bookkeeping."""
    return os.path.join(_flags_dir(), "ALL_DONE%s.flag" % suffix)


def _sentinels():
    """Every ALL_DONE flag, in flags\ AND in the root.

       BOTH PLACES, ALWAYS. A study that was already running when the layout
       changed wrote its sentinel to the root, and a launcher that looked only
       in flags\ would wait for a marker that was already there -- forever, and
       with no way to tell that from a worker that had died."""
    return (glob.glob(os.path.join(RESULTS, FLAGS_SUBDIR, "ALL_DONE*.flag"))
            + glob.glob(os.path.join(RESULTS, "ALL_DONE*.flag")))


_ROOT_HEADS = {"LIVE_STATUS": "00_STATUS",
               "RUN_PLAN": "01_PLAN",
               "SPP_VIOLATIONS": "02_VIOLATIONS",
               "SPP_MEASUREMENTS": "03_MEASUREMENTS",
               "RUN_SUMMARY": "04_RUN_SUMMARY"}


def _rfile(rdir, stem, ext="txt", proj=None):
    """Path of one report file in rdir, project-suffixed name preferred.

       The study writes SPP_CRITERIA_REPORT_EastFork.txt when
       NAME_FILES_BY_PROJECT is on and SPP_CRITERIA_REPORT.txt when it is not,
       and older result folders hold the un-suffixed name. The launcher reads
       these files in a dozen places; every one of them has to accept both, so
       the choice lives here rather than at each call site. If neither exists the
       suffixed name comes back, because that is what a fresh run would write.
       A glob catches folders written for a project this launcher was not told
       about."""
    import glob as _g
    who = proj if proj is not None else _CUR_PROJECT
    names = []
    if who:
        names.append("%s_%s_%s.%s" % (stem, RUN_KIND, who, ext))
        names.append("%s_%s.%s" % (stem, who, ext))
    names.append("%s_%s.%s" % (stem, RUN_KIND, ext))
    names.append("%s.%s" % (stem, ext))
    # ...AND THE _SELECTED NAMES A PARTIAL RUN WRITES.
    #
    # With ONLY_FAULTS set, the study tags every report _SELECTED so a report
    # over fifty faults cannot be mistaken for one over a hundred and fifty.
    # This list did not know that name, so the all-projects summary looked for
    # SPP_CRITERIA_REPORT_BASE_SantaFe.txt, found nothing, and printed
    #
    #     SantaFe  spp  0  0  0  0  *** MISSING ***
    #     FAIL: none
    #
    # for a study that had just reported 0 PASS / 50 FAIL. "FAIL: none" over
    # fifty failures is the most dangerous line this tool can print.
    #
    # Tried AFTER the untagged names, so a complete report still wins where
    # both exist and the partial one is only used when it is all there is.
    if who:
        names.append("%s_SELECTED_%s_%s.%s" % (stem, RUN_KIND, who, ext))
        names.append("%s_SELECTED_%s.%s" % (stem, who, ext))
    names.append("%s_SELECTED_%s.%s" % (stem, RUN_KIND, ext))
    names.append("%s_SELECTED.%s" % (stem, ext))
    # THE NUMBERED ROOT REPORTS. The study writes the few that are read while
    # or straight after a run to the ROOT under a numbered name --
    # 02_VIOLATIONS, 03_MEASUREMENTS, 04_RUN_SUMMARY -- so a launcher looking
    # for "RUN_SUMMARY_PROJ_<proj>.txt" would not find the file that is there
    # and would write a SECOND summary beside it. That is most of what used to
    # fill the results root.
    _head = _ROOT_HEADS.get(stem)
    if _head:
        _pre = list(names)
        names = []
        for n in _pre:
            names.append(_head + n[len(stem):])
        names += _pre
    # reports\ FIRST, then the root. The study writes them to the subfolder now
    # and wrote them to the root before, and a launcher that reads only one of
    # the two places reports a finished study as one that never wrote anything.
    for d in (os.path.join(rdir, REPORTS_SUBDIR), rdir):
        for n in names:
            f = os.path.join(d, n)
            if os.path.isfile(f):
                return f
    hits = sorted(_g.glob(os.path.join(rdir, REPORTS_SUBDIR,
                                       "%s_*.%s" % (stem, ext)))
                  + _g.glob(os.path.join(rdir, "%s_*.%s" % (stem, ext))))
    # A _SELECTED file covers only the scenarios a partial report was asked for.
    # It must never stand in for the full one -- reading a 12-fault selection as
    # if it were the whole 142-fault study is a wrong answer with no symptom.
    hits = [h for h in hits if "_SELECTED" not in os.path.basename(h)]
    if hits:
        return hits[0]
    return os.path.join(rdir, names[0])


# ---- DYNAMIC WORK SHARING --------------------------------------------------
# Set by z7_main.py. When on, the workers share ONE queue instead of being
# dealt fixed slices, so this launcher hands every worker the FULL list and lets
# them claim from it. Handing them pre-cut slices here would put the static deal
# back underneath the shared queue and defeat it.
DYNAMIC_WORK = (os.environ.get("SPP_DYNAMIC_WORK") or "1").strip().lower() \
    in ("1", "true", "yes", "on")

def _env(role, widx=0, n=1, only=None, start_delay=0.0, max_attempts=None):
    e = dict(os.environ)
    # STAGGER: the engine sleeps this long before it imports PSS/E, so N
    # workers do not ask the licence runtime in the same second.
    if start_delay and start_delay > 0:
        e["SPP_START_DELAY_S"] = "%.0f" % start_delay
    else:
        e.pop("SPP_START_DELAY_S", None)
    if max_attempts:
        e["SPP_MAX_ATTEMPTS"] = str(int(max_attempts))
    if _CUR_PROJECT:
        e["SPP_PROJECT"] = _CUR_PROJECT
    if _CUR_MODE:
        e["SPP_FAULT_MODE"] = _CUR_MODE
        # The study script has its own STUDY_DIR literal for standalone
        # use; this makes it follow the launcher, which follows
        # z7_main.py. One setting moves the whole study.
        e["SPP_STUDY_DIR"] = STUDY_DIR
    e["SPP_ROLE"]      = role
    e["SPP_N_WORKERS"] = str(n)
    e["SPP_WORKER"]    = str(widx)
    e["SPP_REGEN_FAULTS"] = REGEN_FAULTS
    # SPP_ONLY carries this process's slice of the selected ids. Absent/empty means
    # "run everything", which is the normal full study.
    #
    # SPP_ONLY_PRESLICED says the list is ALREADY this worker's disjoint share,
    # so the study must not partition it a second time. Without it the study
    # cannot tell this apart from a hand-run where the same full selection is
    # given to every worker and each one does need to take its own share --
    # and it sliced both, so two thirds of every worker's assignment was
    # silently dropped and the run "finished" with most scenarios never started.
    if only:
        e["SPP_ONLY"] = ",".join(only)
        # PRESLICED says "this list is already yours alone". Under DYNAMIC_WORK
        # it is not -- every worker gets the same full list and they divide it
        # by claiming -- so saying so would make each one treat the shared list
        # as its private share and run everything N times.
        if DYNAMIC_WORK:
            e.pop("SPP_ONLY_PRESLICED", None)
        else:
            e["SPP_ONLY_PRESLICED"] = "1"
    else:
        e.pop("SPP_ONLY", None)
        e.pop("SPP_ONLY_PRESLICED", None)
    # ONE NAME FOR THIS RUN'S REPORTS, decided here so every process agrees.
    # _report_tag() is the whole-run question ("is this run restricted?"), not
    # the per-process one ("does THIS process have a selection?") -- the merge
    # and the shards would otherwise answer differently and both sets of files
    # would be written.
    e["SPP_REPORT_TAG"] = _report_tag()
    return e


_LOCKED = []          # files a previous PSS/E process still has open


def _clear(path):
    """Delete one marker/sentinel. Returns True if it is gone afterwards.

       A failure used to be swallowed silently, which is worse than it sounds on
       Windows: a file still held open by a PSS/E process that has not exited
       CANNOT be deleted, so FRESH_START would report 'cleared 40 file(s)' while
       the stale markers were all still there -- and the run would then skip
       every scenario as already done. The file is now named, and the count at
       the end is of files that really were removed."""
    try:
        if os.path.isfile(path):
            os.remove(path)
        return True
    except Exception as e:
        _LOCKED.append((path, str(e)))
        return False


def _report_locked(what):
    """Say plainly that files could not be deleted, and why that happens.

       EMPTIES THE LIST ON BOTH EXITS. It used to clear only when it printed,
       so a caller that found nothing left the list primed for the next one."""
    if not _LOCKED:
        return False
    print("")
    print("[parallel] *** %d file(s) could NOT be deleted -- something still has them "
          "OPEN ***" % len(_LOCKED))
    for p, e in _LOCKED[:12]:
        print("[parallel]     %s" % p)
        print("[parallel]        %s" % e)
    if len(_LOCKED) > 12:
        print("[parallel]     ... and %d more" % (len(_LOCKED) - 12))
    print("[parallel]  On Windows a file held open by a running process cannot be removed.")
    print("[parallel]  That means a PSS/E process from an EARLIER run is still alive --")
    print("[parallel]  usually a build or report phase that froze rather than exiting. It")
    print("[parallel]  keeps its .out and its DYN_STUDY_*.log open, which is why the log")
    print("[parallel]  keeps changing long after the simulations finished.")
    print("[parallel]  Close it before re-running:")
    print("[parallel]     Task Manager -> Details -> end every python.exe / psse*.exe")
    print("[parallel]     that is not this window, then delete the folder again.")
    print("[parallel]  To find the exact owner: Resource Monitor -> CPU -> Associated")
    print("[parallel]  Handles, and search for the file name.")
    print("[parallel]  %s" % what)
    print("")
    del _LOCKED[:]
    return True


_PRINT_LOCK    = threading.Lock()
_CONSOLE_FH    = None                 # combined console-log file handle (opened in main)
_LAST_ACTIVITY = {}                   # worker index -> time of its last printed line (hang watchdog)

def _tag_print(tag, line):
    """Thread-safe: print one child line prefixed with its worker tag ('[W1] ...') to the
       terminal AND to the combined console log, so all workers' output is in one file too.

       NOTHING HERE MAY RAISE. The caller is the thread draining that worker's
       pipe; if it dies the pipe fills and the worker blocks for ever. A case
       comment with an en dash in it is enough -- a Windows console is cp437 and
       cannot encode one."""
    text = "%-6s %s" % (tag, line if line.endswith("\n") else line + "\n")
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    with _PRINT_LOCK:
        try:
            sys.stdout.write(text)
        except UnicodeEncodeError:
            try:
                sys.stdout.write(text.encode(enc, "replace").decode(enc, "replace"))
            except Exception:
                pass
        except Exception:
            pass
        try:
            sys.stdout.flush()
        except Exception:
            pass
        if _CONSOLE_FH is not None:
            try: _CONSOLE_FH.write(text); _CONSOLE_FH.flush()
            except Exception: pass

# ---- PSS/E CHATTER THAT DROWNS THE CONSOLE ---------------------------------
# PSS/E re-echoes its RUN-activity preamble EVERY TIME psspy.run is called, and
# SHOW_SIM_PROGRESS calls it once per PROGRESS_STEP_S of simulated time. With a
# 30 s scenario and a 0.5 s step that is sixty repeats, and the preamble on this
# deck is ~120 lines of
#
#     FLOW1 BUS   1999 NOT FOUND--CIRCUIT 1  FROM   1801 TO   1999
#     FLOW1 BRANCH NOT FOUND: CIRCUIT 1  FROM 768441 TO 543028
#     Channel output file is "...\outs\F113.out"
#
# -- seven thousand lines per scenario, per worker, ten workers deep. The live
# status table is printed into the middle of that and cannot be found.
#
# The warnings themselves are old news: they name branches in the monitored-flow
# list that this case does not contain, they are identical every time, and the
# FIRST occurrence is kept so they are still on the record. What is dropped is
# the ninety-ninth copy of it.
QUIET_PSSE_NOISE = True
# ...and do not announce it either. "(110 repeated PSS/E preamble line(s)
# suppressed)" printed every seven seconds by ten workers is the same problem
# one size smaller: two lines of bookkeeping around every one line of progress.
# The distinct warnings were each printed once when they first appeared, which
# is the record; the count adds nothing to it. True puts the counter back.
QUIET_PSSE_NOISE_REPORT = False
# Workers emit a bare blank line after each PSS/E block. Harmless alone, but at
# ten workers it doubles the height of the console for no content.
QUIET_BLANK_LINES = True

_NOISE_RE = re.compile(
    r"^\s*(FLOW1\s+(BUS|BRANCH)\b"
    r"|Channel output file is\b"
    r"|CCT TYPE USER DEFINED .*NOT ACCESSIBLE)", re.I)

_NOISE_SEEN = {}          # idx -> set of lines already shown once
_NOISE_HELD = {}          # idx -> how many have been swallowed since the last report


# ---- MODAL BOXES THAT BELONG TO OUR OWN CHILDREN -----------------------------
# A headless worker has nobody to click OK. The sweeper closes, every few
# seconds, any modal dialog (window class #32770) whose owning PROCESS is one
# this launcher started -- and only those, so a person's own PSS/E GUI or a
# dialog from another run is never touched. What it closed is recorded per
# child so the worker loop can tell a licence failure from a crash box.
_CHILD_PIDS  = {}          # pid -> key ("w3", "phase:build", "shard2", ...)
_DIALOG_HITS = {}          # key -> [(time, title, text)]
_DIALOG_SEEN = {}          # hwnd -> when it was last closed. A box that is STILL
                           # showing 30 s later is closed again: the Abort/Retry/
                           # Ignore box that psseng.dll raises on a CodeMeter
                           # network error comes back under the same handle when
                           # the first click lands on Retry, and a set never
                           # looked at it twice. That is how F166 stood for 18 h
                           # with the sweeper on.
_DIALOG_RECLOSE_S = 30.0
_DIALOG_LOCK = threading.Lock()
_LICENCE_RE  = re.compile(r"codemeter|licen[cs]e|start error|pssenng|psseng|dll load failed|initialization routine failed|network error|error 100|wibu", re.I)
_DLL_INIT_RE = re.compile(r"DLL load failed|initialization routine failed", re.I)


def _key_of_idx(idx):
    """The _DIALOG_HITS key the pump index maps to: workers are w<i>, the
       build is -1 and the merge -2 (their own keys)."""
    if idx == -1:
        return "build"
    if idx == -2:
        return "report-merge"
    if not isinstance(idx, int):
        return str(idx)           # report shards / background report / plotter: their own key
    return "w%d" % idx


def _register_child(key, proc):
    try:
        with _DIALOG_LOCK:
            _CHILD_PIDS[int(proc.pid)] = key
    except Exception:
        pass


def _child_key(pid):
    with _DIALOG_LOCK:
        return _CHILD_PIDS.get(int(pid))


def _modal_boxes_of_children():
    """[(hwnd, pid, key, title, text)] -- every visible #32770 dialog owned by a
       registered child process. Windows only; [] anywhere else."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    out = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    buf = ctypes.create_unicode_buffer(512)

    def _txt(h):
        buf.value = ""
        u.GetWindowTextW(h, buf, 512)
        return buf.value

    def _cls(h):
        buf.value = ""
        u.GetClassNameW(h, buf, 512)
        return buf.value

    def _statics(h):
        parts = []

        def _cb(ch, _lp):
            try:
                if _cls(ch).lower() == "static":
                    t = _txt(ch).strip()
                    if t:
                        parts.append(t)
            except Exception:
                pass
            return True
        try:
            u.EnumChildWindows(h, WNDENUMPROC(_cb), 0)
        except Exception:
            pass
        return " | ".join(parts)[:300]

    def _top(h, _lp):
        try:
            if not u.IsWindowVisible(h):
                return True
            if _cls(h) != "#32770":
                return True
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            key = _child_key(pid.value)
            if key is None:
                return True
            out.append((int(h), int(pid.value), key, _txt(h), _statics(h)))
        except Exception:
            pass
        return True
    try:
        u.EnumWindows(WNDENUMPROC(_top), 0)
    except Exception:
        pass
    return out


def _close_box(hwnd):
    """Press the box's button, then ask it to close. Both are posted, never sent,
       so the sweeper can not itself block on a window that is going away."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    BM_CLICK, WM_CLOSE, WM_COMMAND, IDOK, IDCANCEL = 0x00F5, 0x0010, 0x0111, 1, 2
    IDABORT, IDRETRY, IDIGNORE = 3, 4, 5
    # THE psseng.dll LICENCE BOX HAS THREE BUTTONS: Abort / Retry / Ignore, no
    # close box (its X is greyed), and no OK or Cancel. Clicking "the first
    # Button" lands on whichever one Windows created first, and Retry asks the
    # same dead licence server the same question and shows the same box again.
    # Ignore lets psseng carry on without the licence -- the psspy call fails,
    # the study records it, and the worker exits or moves on. Abort ends the
    # process outright. Either ends the hang; Retry never does. So Ignore, then
    # Abort, are posted BY ID, before the generic click, and Retry never is.
    for wp in (IDIGNORE, IDABORT):
        try:
            u.PostMessageW(wintypes.HWND(hwnd), WM_COMMAND, wp, 0)
        except Exception:
            pass
    try:
        btn = u.FindWindowExW(wintypes.HWND(hwnd), None, "Button", None)
        while btn:
            try:
                _n = ctypes.create_unicode_buffer(64)
                u.GetWindowTextW(btn, _n, 64)
                _lab = (_n.value or "").replace("&", "").strip().lower()
            except Exception:
                _lab = ""
            if _lab != "retry":
                u.PostMessageW(btn, BM_CLICK, 0, 0)
            btn = u.FindWindowExW(wintypes.HWND(hwnd), btn, "Button", None)
    except Exception:
        pass
    for wp in (IDOK, IDCANCEL):
        try:
            u.PostMessageW(wintypes.HWND(hwnd), WM_COMMAND, wp, 0)
        except Exception:
            pass
    try:
        u.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0)
    except Exception:
        pass


def _dialog_sweep_once():
    for hwnd, pid, key, title, text in _modal_boxes_of_children():
        _last = _DIALOG_SEEN.get(hwnd)
        if _last is not None and (time.time() - _last) < _DIALOG_RECLOSE_S:
            continue
        _again = _last is not None
        _DIALOG_SEEN[hwnd] = time.time()
        kind = "LICENCE" if _LICENCE_RE.search("%s %s" % (title, text)) else "modal"
        with _PRINT_LOCK:
            print("[dialog] %s (pid %d) is showing a %s box  '%s': %s  -- closing it%s"
                  % (key, pid, kind, title, text or "(no text)",
                     "  (STILL SHOWING -- closing it again)" if _again else ""))
            if kind == "LICENCE":
                print("[dialog]   PSS/E in that process could not take a licence from the "
                      "CodeMeter runtime. The process is relaunched after a pause.")
        with _DIALOG_LOCK:
            _DIALOG_HITS.setdefault(key, []).append((time.time(), title, text))
        _close_box(hwnd)


def _dialog_sweeper():
    while True:
        try:
            _dialog_sweep_once()
        except Exception as e:
            with _PRINT_LOCK:
                print("[dialog] sweep failed: %s" % e)
        time.sleep(5)


_SWEEPER_ON = [False]


def _dialog_sweeper_start():
    if not CLOSE_PSSE_DIALOGS or os.name != "nt" or _SWEEPER_ON[0]:
        return
    _SWEEPER_ON[0] = True             # one sweeper for the launch, not one per pass
    th = threading.Thread(target=_dialog_sweeper)
    th.daemon = True
    th.start()
    print("[parallel] dialog sweeper on: modal PSS/E boxes owned by this launcher's own "
          "processes are closed automatically (CLOSE_PSSE_DIALOGS)")


def _licence_hit(key, since):
    """(title, text) of a LICENCE box closed for this child after `since`, or None."""
    with _DIALOG_LOCK:
        hits = list(_DIALOG_HITS.get(key) or [])
    for t, title, text in hits:
        if t >= since and _LICENCE_RE.search("%s %s" % (title, text)):
            return (title, text)
    return None


def _pid_holds_claim(pid):
    """Does any live scenario claim in outs\ name this process? The claim text
       is 'w<i> pid<pid> host=...'."""
    try:
        pat = re.compile(r"\bpid%d\b" % int(pid))
    except Exception:
        return False
    for p in glob.glob(os.path.join(OUT_DIR, "*.claim")):
        try:
            with open(p, "r", errors="replace") as fh:
                if pat.search(fh.read(300) or ""):
                    return True
        except Exception:
            continue
    return False



_SCEN_SCAN = {"t": 0.0, "rows": {}}


def _worker_scenario_age(i, since=0.0):
    """(scenario_id, seconds_it_has_been_RUNNING) for the scenario worker i holds,
       or (None, 0). Read from the study's own PROGRESS rows -- the same source the
       live table uses -- and cached, because this is asked every POLL_SECS.

       THIS IS THE ONE WATCHDOG THAT DOES NOT MEASURE SILENCE. A worker blocked on
       a modal Windows dialog can still have a pump thread and a last-activity time
       that look recent; what it cannot do is finish the scenario."""
    now = time.time()
    if now - _SCEN_SCAN["t"] > SCENARIO_SCAN_EVERY_S:
        try:
            _SCEN_SCAN["rows"] = _progress_rows(since=_RUN_STARTED) or {}
        except Exception:
            _SCEN_SCAN["rows"] = {}
        _SCEN_SCAN["t"] = now
    want = "work%d" % i
    best = (None, 0.0)
    for sid, row in (_SCEN_SCAN["rows"] or {}).items():
        try:
            st, _att, wk, tm, _note = row
        except Exception:
            continue
        if (st or "").strip() != "RUNNING" or (wk or "").strip() != want or not tm:
            continue
        try:
            ts = time.mktime(time.strptime(tm, "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        # A ROW WRITTEN BEFORE THIS LAUNCH OF THE WORKER is the killed one's:
        # read as the new process's, it killed the relaunch at once, for ever
        if since and ts < since - 2.0:
            continue
        age = now - ts
        if age > best[1]:
            best = (sid, age)
    return best


def _gave_up_ids(ids=None):
    """Scenarios that GAVE UP (or errored) and have no finished .out."""
    prog = _progress_rows()
    out = []
    for sid in (ids or _fault_ids() or []):
        if (os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid))
                and os.path.isfile(os.path.join(OUT_DIR, "%s.out" % sid))):
            continue
        st = (prog.get(sid) or ("",))[0]
        if st in ("GAVE-UP", "ERROR", "FAILED") or _read_attempts(sid) >= _max_attempts_now():
            out.append(sid)
    return out


def _max_attempts_now():
    try:
        return int((os.environ.get("SPP_MAX_ATTEMPTS") or "").strip() or 4)
    except Exception:
        return 4


def _requeued_at(sid):
    """When this scenario's attempt budget was last reset, or 0.

       THE PROGRESS FILE IS NOT REWRITTEN BY THE RESET. A scenario that gave up
       keeps its GAVE-UP row until a worker actually claims it again and writes
       a new one, so for the first minutes of a retry round the table shows a
       queue of GAVE-UP rows -- with the OLD note, "attempted 4 time(s)", beside
       an attempts column the reset has just put back to 0. Every one of them is
       queued and will be run. This marker is how the table knows that."""
    try:
        return os.path.getmtime(os.path.join(OUT_DIR, "%s.requeued" % sid))
    except Exception:
        return 0


def _row_is_stale(sid, tm):
    """True if this scenario's progress row was written BEFORE its budget was
       reset -- i.e. the row describes the round that ended, not this one."""
    r = _requeued_at(sid)
    if not r:
        return False
    if not tm:
        return True
    try:
        return time.mktime(time.strptime(tm, "%Y-%m-%d %H:%M:%S")) < r
    except Exception:
        return False


def _reset_attempts(ids, why):
    """Give these scenarios a fresh attempt budget: drop .attempts and the kept
       claim, so a worker can take them again."""
    n = 0
    for sid in ids:
        for ext in ("attempts", "claim", "hangs"):
            p = os.path.join(OUT_DIR, "%s.%s" % (sid, ext))
            if os.path.isfile(p) and _clear(p):
                n += 1
        try:
            with open(os.path.join(OUT_DIR, "%s.requeued" % sid), "w") as _fh:
                _fh.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), why))
        except Exception:
            pass
    print("[parallel] %s: attempt budget reset for %d scenario(s) (%s%s) -- %d marker(s) removed"
          % (why, len(ids), ", ".join(ids[:10]), " ..." if len(ids) > 10 else "", n))


def _pump_lines(proc):
    """proc's output line by line, READ AS BYTES and decoded with 'replace'. A
       byte the console codec cannot decode used to raise inside the text
       reader and end the reading: the pipe then filled and the child blocked
       until the hang watchdog killed it (one attempt lost)."""
    enc = getattr(proc.stdout, "encoding", None) or "latin-1"
    raw = getattr(proc.stdout, "buffer", None)
    while True:
        if raw is not None:
            b = raw.readline()
            line = b.decode(enc, "replace").replace("\r\n", "\n").replace("\r", "\n") if b else ""
        else:
            line = proc.stdout.readline()
        if not line:
            return
        yield line


def _pump(idx, tag, proc, key=None):
    """Read a child's stdout line-by-line and echo each line prefixed with its tag. Also stamp
       this worker's 'last activity' time so the hang watchdog can spot a frozen worker.

       Repeated PSS/E preamble is folded into a one-line count (QUIET_PSSE_NOISE)
       so the progress lines and the status table stay findable."""
    _NOISE_SEEN.setdefault(idx, set())
    _NOISE_HELD.setdefault(idx, 0)
    try:
        for line in _pump_lines(proc):
            _LAST_ACTIVITY[idx] = time.time()
            txt = line.rstrip("\n")
            # "import psspy" DYING IS THE LICENCE, NOT THE SCRIPT. psseng.dll
            # takes its CodeMeter licence inside its DLL initialisation, and
            # when the server does not answer the import raises
            #   ImportError: DLL load failed: A dynamic link library (DLL)
            #   initialization routine failed.
            # about three minutes in -- CodeMeter's network timeout. No box,
            # no rc=86, and it lands just past STARTUP_DEAD_S, so it used to
            # be filed as an ordinary crash and relaunched three seconds
            # later, forever. It is recorded as a licence hit for this child
            # so the exit takes the backoff + cooldown path instead.
            if _DLL_INIT_RE.search(txt):
                with _DIALOG_LOCK:
                    _DIALOG_HITS.setdefault(key or _key_of_idx(idx), []).append(
                        (time.time(), "psspy import", txt.strip()))
            if QUIET_PSSE_NOISE and _NOISE_RE.match(txt):
                nkey = txt.strip()
                if nkey in _NOISE_SEEN[idx]:
                    _NOISE_HELD[idx] += 1          # seen it -- say so by counting
                    continue
                _NOISE_SEEN[idx].add(nkey)         # first time: print it in full
            elif QUIET_BLANK_LINES and not txt.strip():
                continue
            elif _NOISE_HELD[idx]:
                n, _NOISE_HELD[idx] = _NOISE_HELD[idx], 0
                if QUIET_PSSE_NOISE_REPORT:
                    _tag_print(tag, "   (%d repeated PSS/E preamble line(s) "
                                    "suppressed -- QUIET_PSSE_NOISE)" % n)
            _tag_print(tag, txt)
    except Exception:
        pass


def _expand_ids(items):
    """['F01','F19-F21'] -> ['F01','F19','F20','F21']. Same expansion the study
       script does, so both ends agree on what an id list means."""
    out = []
    for raw in items:
        for tok in str(raw).replace(";", ",").split(","):
            tok = tok.strip().upper()
            if not tok:
                continue
            if "-" in tok and tok.count("-") == 1:
                lo, hi = [t.strip() for t in tok.split("-")]
                pre = "".join(c for c in lo if not c.isdigit())
                lo_n = lo[len(pre):]
                hi_n = hi[len(pre):] if hi.startswith(pre) else hi
                if pre and lo_n.isdigit() and hi_n.isdigit():
                    w = len(lo_n)
                    for n in range(int(lo_n), int(hi_n) + 1):
                        out.append("%s%0*d" % (pre, w, n))
                    continue
            out.append(tok)
    seen, uniq = set(), []
    for s in out:
        if s not in seen:
            seen.add(s); uniq.append(s)
    return uniq


_SELECT_KEYWORDS = ("CRASHED", "NOTDONE", "NONCONV", "FAIL", "INCOMPLETE", "ALL")

# ---- A BATCH OF THE FAULT SET, NOT ALL OF IT -------------------------------
# A full SPP set is hours of simulation before ANY of it can be looked at, and
# the answer to "is the model behaving" does not need all of it. Two keywords
# take an argument:
#
#   RUN_ONLY_FAULTS = ["EVERY:10"]   every 10th fault -- 15 of 142
#   RUN_ONLY_FAULTS = ["FIRST:20"]   the first 20
#
# EVERY:N IS THE ONE TO REACH FOR. The list is built by walking out from the
# POI, so it is ORDERED BY LOCATION -- the first twenty faults are twenty events
# around the same few buses, and passing all of them says nothing about the rest
# of the network. Every tenth spreads the sample over the whole radius for the
# same cost.
#
# The ids keep their numbers either way (F01, F11, F21 ...), so a later run of
# the rest drops in beside these with SKIP_DONE = True and FRESH_START = False.
_SELECT_ARG_KEYWORDS = ("EVERY", "FIRST")


def _is_select_keyword(tok):
    t = str(tok).strip().upper()
    if t in _SELECT_KEYWORDS:
        return True
    head = t.split(":", 1)[0].strip()
    return ":" in t and head in _SELECT_ARG_KEYWORDS


def _run_summary_csv():
    """The run summary's .csv -- flags\\ now, with a fallback to where it used
       to be written. It is what CRASHED / FAIL / INCOMPLETE resolve against,
       so a folder from before the move must still answer them."""
    import glob as _g
    cand = []
    tag = _report_tag()
    who = _CUR_PROJECT
    for d in (os.path.join(RESULTS, FLAGS_SUBDIR),
              os.path.join(RESULTS, REPORTS_SUBDIR), RESULTS):
        if who:
            cand.append(os.path.join(d, "RUN_SUMMARY%s_%s_%s.csv" % (tag, RUN_KIND, who)))
            cand.append(os.path.join(d, "RUN_SUMMARY%s_%s.csv" % (tag, who)))
        cand.append(os.path.join(d, "RUN_SUMMARY%s_%s.csv" % (tag, RUN_KIND)))
        cand.append(os.path.join(d, "RUN_SUMMARY%s.csv" % tag))
    for f in cand:
        if os.path.isfile(f):
            return f
    hits = sorted(_g.glob(os.path.join(RESULTS, FLAGS_SUBDIR, "RUN_SUMMARY*.csv")))
    return hits[0] if hits else cand[0]


def _summary_rows():
    """[(scenario, run_status, attempts, spp_verdict, solver_fix)] from the previous
       run's RUN_SUMMARY.csv. Empty list if it has not been written yet."""
    p = _run_summary_csv()
    rows = []
    try:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                rows.append(((r.get("scenario") or "").strip(),
                             (r.get("run_status") or "").strip().upper(),
                             (r.get("attempts") or "0").strip(),
                             (r.get("spp_verdict") or "").strip().upper(),
                             (r.get("solver_fix") or "").strip()))
    except Exception:
        pass
    return rows


def _resolve_keyword(word):
    """Turn one selection KEYWORD into a list of scenario ids."""
    word = word.upper().strip()
    if ":" in word and word.split(":", 1)[0].strip() in _SELECT_ARG_KEYWORDS:
        head, arg = [x.strip() for x in word.split(":", 1)]
        # A FRESH FOLDER HAS NO LOCAL SPP_FAULTS.csv YET -- the BUILD writes it.
        # Fall back to the SHARED list so FIRST:N / EVERY:N still select.
        ids = _fault_ids() or _shared_fault_ids()
        try:
            n = int(arg)
        except ValueError:
            print("[parallel] *** '%s' -- %r is not a number, so nothing is selected ***"
                  % (word, arg))
            return []
        if n <= 0:
            print("[parallel] *** '%s' -- N must be 1 or more ***" % word)
            return []
        if head == "FIRST":
            return ids[:n]
        # EVERY:N -- and it always includes the FIRST id, so a sample and a
        # "first" selection of the same list overlap rather than interleave.
        return ids[::n]
    rows = _summary_rows()
    if word == "ALL":
        # local list, else the shared one (fresh folder -- see FIRST/EVERY above)
        return [s for s in (_fault_ids() or _shared_fault_ids())]
    if word == "NOTDONE":
        # markers are authoritative and exist even without a RUN_SUMMARY
        out = []
        for sid in (_fault_ids() or _shared_fault_ids()):
            if not os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid)):
                out.append(sid)
        return out
    if not rows:
        print("[parallel] *** '%s' needs %s from a previous run -- none found ***"
              % (word, _rfile_tagged(RESULTS, "RUN_SUMMARY", "csv")))
        return []
    if word == "CRASHED":
        return [s for s, st, a, v, f in rows if "CRASH" in st or "GAVE" in st]
    if word == "INCOMPLETE":
        return [s for s, st, a, v, f in rows if st in ("INCOMPLETE", "NOT RUN")]
    if word == "NONCONV":
        return [s for s, st, a, v, f in rows if f]
    if word == "FAIL":
        return [s for s, st, a, v, f in rows if v == "FAIL"]
    return []


SKIP_DONE_SELECTED = False    # set by _apply_skip_done; reported, not used for naming


def _fault_row_sig(r):
    """A short fingerprint of ONE fault list row: bus, type, clearing time, the
       elements tripped (direction-free, circuit upper-cased), three-winding,
       drops, prior outage and the con_id. Written into <id>.done when a
       scenario completes and checked before a .done is trusted, so a list
       that was renumbered -- F27 on disk is not F27 in the new list -- is
       never resumed as if it were the same faults."""
    import hashlib
    def _els(s):
        out = set()
        for e in (s or "").split(";"):
            b = [x.strip() for x in e.split("-")]
            if len(b) >= 2 and b[0] and b[1]:
                try:
                    a_, c_ = int(b[0]), int(b[1])
                except ValueError:
                    continue
                out.add("%d-%d-%s" % (min(a_, c_), max(a_, c_), (b[3] if len(b) > 3 and b[3] else "1").upper()))
        return ";".join(sorted(out))
    def _tri(s):
        out = set()
        for e in (s or "").split(";"):
            b = [x.strip() for x in e.split("-")]
            if len(b) >= 3 and b[0]:
                try:
                    out.add("%s-%s" % ("-".join(str(x) for x in sorted(int(y) for y in b[:3])),
                                       (b[3] if len(b) > 3 and b[3] else "1").upper()))
                except ValueError:
                    continue
        return ";".join(sorted(out))
    def _pairs(s):
        return ";".join(sorted(x.strip().upper() for x in (s or "").split(";") if x.strip()))
    try:
        cyc = "%.2f" % float(r.get("clear_cycles") or 0)
    except ValueError:
        cyc = str(r.get("clear_cycles") or "")
    parts = [str(r.get("fault_bus") or "").strip(), str(r.get("fault_type") or "3PH").strip().upper(), cyc,
             _els(r.get("trip_elements")), _tri(r.get("trip_3wind")),
             _pairs(r.get("drop_machines")), _pairs(r.get("drop_loads")), _pairs(r.get("drop_shunts")),
             _els(r.get("pre_outage")), str(r.get("con_id") or "").strip().upper()]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def _done_sig(path):
    """The sig= line of a .done marker, or "" for a marker written before
       markers carried one."""
    try:
        with open(path) as fh:
            for ln in fh:
                if ln.startswith("sig="):
                    return ln[4:].strip()
    except Exception:
        pass
    return ""


def _timing_changed(out_dir, sid):
    """Why <sid>'s result on disk was run with DIFFERENT TIMES than this launch
       (PRE_FAULT_S / SIM_END_S from z7_main.py), or "".

       Its marker records the clearing instant (first line) and the last
       simulated second (tend=). A result from an 8 s run with the fault at
       3 s, kept under RUN_ONLY_MISSING_OUT beside 25.2 s runs with the fault
       at 5 s, would be scored against windows that belong to the other
       timing -- and compared as if it were the same study. Never deletes:
       the caller moves it aside and re-runs it."""
    def _f(k):
        try:
            v = float(os.environ.get(k) or "")
            return v if v == v and v > 0 else None
        except ValueError:
            return None
    if str(sid).upper().startswith("FLAT"):
        # the no-fault run has its own length, FLAT_RUN_S, and no fault
        fl = _f("SPP_FLAT_RUN_S")
        mp = os.path.join(out_dir, "%s.done" % sid)
        if fl is not None and os.path.isfile(mp):
            try:
                for ln in open(mp).read().splitlines():
                    if ln.startswith("tend=") and abs(float(ln[5:]) - fl) > 0.11:
                        return "ran to %.2f s -- FLAT_RUN_S is now %.2f s" % (float(ln[5:]), fl)
            except Exception:
                pass
        return ""
    pre, end = _f("SPP_PRE_FAULT_S"), _f("SPP_SIM_END_S")
    if pre is None and end is None:
        return ""
    for ext in ("done", "partial"):
        mp = os.path.join(out_dir, "%s.%s" % (sid, ext))
        if not os.path.isfile(mp):
            continue
        tclear = tend = None
        try:
            with open(mp) as fh:
                lines = fh.read().splitlines()
            if lines and lines[0].strip():
                try:
                    tclear = float(lines[0].strip())
                except ValueError:
                    tclear = None
            for ln in lines:
                if ln.startswith("tend="):
                    tend = float(ln[5:].strip())
        except Exception:
            continue
        if pre is not None and tclear is not None and not (pre < tclear <= pre + 1.0):
            return ("cleared at %.3f s -- the fault is now applied at %.2f s (PRE_FAULT_S)"
                    % (tclear, pre))
        if ext == "done" and end is not None and tend is not None and abs(tend - end) > 0.11:
            return "ran to %.2f s -- SIM_END_S is now %.2f s" % (tend, end)
    return ""


def _stale_aside(out_dir, sid, why):
    """Move <sid>.out / .done aside as *.stale_<stamp> -- never deleted."""
    import time as _t
    stamp = _t.strftime("%Y%m%d_%H%M%S")
    moved = 0
    # THE ATTEMPT COUNT GOES TOO. Left behind, a scenario moved aside because
    # the fault list was renumbered comes back carrying the previous list's
    # attempts -- at the cap it is refused on sight and reported GAVE-UP
    # without ever being simulated.
    for ext in ("out", "done", "attempts", "hangs", "plotted", "readfail", "badout", "partial"):
        p = os.path.join(out_dir, "%s.%s" % (sid, ext))
        if os.path.isfile(p):
            try:
                os.rename(p, "%s.stale_%s" % (p, stamp))
                moved += 1
            except Exception:
                pass
    return moved


def _apply_skip_done(selected):
    """Drop scenarios that already finished. Returns the ids still to run.

       Called after the id/event selection so it narrows THAT, never widens it."""
    global SKIP_DONE_SELECTED
    SKIP_DONE_SELECTED = False     # this pass's own answer, not the last project's
    # AN EMPTY SELECTION STAYS EMPTY. A keyword or id list that matched nothing
    # must not fall through to `selected or _fault_ids()` below and come back as
    # every unfinished id -- that would run the whole list when you asked for
    # none of it.
    if (RUN_ONLY_FAULTS or ONLY_EVENTS) and not selected:
        return selected
    # RUN_ONLY_MISSING_OUT IS A SKIP TOO. "Simulate only faults with no .out"
    # was honoured only with SKIP_DONE on: with SKIP_DONE = False every selected
    # fault had its markers cleared and was simulated again over the .out
    # already on disk -- hours of repeated work the panel said would not happen.
    if not SKIP_DONE and not ONLY_MISSING_OUT:
        return selected
    # FRESH_START MEANS START OVER, AND IT HAS TO WIN HERE.
    #
    # THIS IS WHY "FRESH_START = True" DID NOTHING. This function runs BEFORE
    # the FRESH_START clear, so it read the markers that were still on disk and
    # returned the not-yet-finished ids -- a non-empty list. Back in main() the
    # clear is guarded by `if FRESH_START and not selected:`, and `selected` was
    # now truthy, so the wholesale delete of every .done / .attempts / sentinel
    # NEVER RAN. Control fell into the `elif selected:` branch, which clears
    # markers only for the scenarios that had none.
    #
    # Two consoles promised otherwise on the way past -- confirm_case() said
    # "the N .done marker(s) above will be CLEARED, so every scenario runs again
    # from the start", and _run_one_study said "Every scenario will be simulated
    # again" -- and neither happened. The finished .out files kept their markers
    # and were scored, so a report could mix scenarios run against two different
    # dynamics decks under one project name.
    #
    # It also skipped the BUILD: `_skip_build = bool(selected) and ...` was true
    # for the same reason, so even the scenarios that did re-run used the old
    # snapshot.
    #
    # A start-over run has nothing to skip, by definition. Returning here says
    # so, and leaves `selected` empty so the clear in main() fires.
    if FRESH_START:
        return selected
    ids = selected or _fault_ids()
    if not ids:
        return selected
    # DONE means the same thing here as in the live status table: a .done marker
    # AND a .out on disk. A marker with no .out is not a finished scenario, and
    # skipping it would quietly drop it from the run for good.
    prog = _progress_rows()
    # THE SAME ID IS NOT THE SAME FAULT. The list on disk may have been
    # renumbered since those .done files were written (a new DISIS list, a
    # merged list): F27 then is not F27 now. Each marker carries the
    # fingerprint of the row it was run for; a marker whose fingerprint is
    # not this list's row -- or that has none -- is moved aside and re-run.
    sigs = {}
    for _lp in (_shared_fault_list_path(), os.path.join(RESULTS, "faults", "SPP_FAULTS.csv")):
        if not _lp or not os.path.isfile(_lp):
            continue
        try:
            with open(_lp, newline="") as _fh:
                for _r in csv.DictReader(_fh):
                    _fid = (_r.get("fault_id") or "").strip()
                    if _fid and _fid not in sigs:
                        sigs[_fid] = _fault_row_sig(_r)
        except Exception:
            continue
        if sigs:
            break
    todo, done, gave_up, stale, empty, partial, have_out = [], [], [], [], [], [], []
    retimed = []
    for sid in ids:
        _op = os.path.join(OUT_DIR, "%s.out" % sid)
        # RUN WITH OTHER TIMES: set aside and run again, even under
        # RUN_ONLY_MISSING_OUT -- see _timing_changed.
        _why_t = _timing_changed(OUT_DIR, sid)
        if _why_t:
            _stale_aside(OUT_DIR, sid, "timing")
            retimed.append((sid, _why_t))
        # A SCORABLE PARTIAL RUN IS KEPT, NOT RE-RUN. Re-running writes over
        # the only copy of a run that reached most of SIM_END_S and is already
        # scored and compared, and the attempts left to it are the ones that
        # already failed. KEEP_PARTIAL_RUNS = False in the panel to try again.
        # ONLY THE FAULTS WITH NO RESULT AT ALL.
        #
        # A result on disk is a result: complete, partial, or stopped early.
        # Simulating over it spends hours to replace a record that is already
        # there -- and an attempt that dies at init replaces it with nothing.
        # With this on, the sweep fills the GAPS and the scoring pass decides
        # what the existing files are worth.
        if ONLY_MISSING_OUT and os.path.isfile(_op):
            try:
                _has_data = os.path.getsize(_op) >= OUT_MIN_BYTES
            except Exception:
                _has_data = False
            if _has_data:
                have_out.append(sid)
                done.append(sid)
                continue
        if (KEEP_PARTIAL_RUNS
                and os.path.isfile(os.path.join(OUT_DIR, "%s.partial" % sid))
                and os.path.isfile(_op)
                and not os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid))):
            partial.append(sid)
            done.append(sid)
            continue
        if (os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid))
                and os.path.isfile(_op)):
            # AN EMPTY .out IS NOT A RESULT. IronStar came back with 231 .out
            # files of 0 MB, every one carrying a .done marker: the writer
            # produced nothing (a full disk, or a failed channel file) and the
            # run of the day marked them done because the file existed. This is
            # not a size rule -- no run of any length fits in under 1 MB, that
            # is at most a header -- it is "the file has no data in it".
            try:
                _nodata = os.path.getsize(_op) < OUT_MIN_BYTES
            except Exception:
                _nodata = False
            want = sigs.get(sid)
            if _nodata:
                _stale_aside(OUT_DIR, sid, "empty")
                empty.append(sid)
            elif want and _done_sig(os.path.join(OUT_DIR, "%s.done" % sid)) != want:
                _stale_aside(OUT_DIR, sid, "sig")
                stale.append(sid)
            else:
                done.append(sid)
                continue
        todo.append(sid)
        st = (prog.get(sid) or ("",))[0]
        if st in ("GAVE-UP", "ERROR", "FAILED"):
            gave_up.append(sid)
    if retimed:
        print("[parallel] %d scenario(s) on disk were run with DIFFERENT TIMES than this launch -- "
              "moved aside as .stale and re-run: %s%s"
              % (len(retimed), ", ".join("%s (%s)" % x for x in retimed[:4]),
                 " ..." if len(retimed) > 4 else ""))
    if empty:
        print("[parallel] SKIP_DONE: %d scenario(s) carried a .done marker but their .out holds NO DATA"
              " (under %d KB) -- the run wrote nothing; moved aside as .stale and re-run: %s%s"
              % (len(empty), OUT_MIN_BYTES // 1024, ", ".join(empty[:8]), " ..." if len(empty) > 8 else ""))
    if have_out:
        print("[parallel] RUN_ONLY_MISSING_OUT: %d scenario(s) already have an .out file"
              " -- not simulated again; the scoring pass judges what is there: %s%s"
              % (len(have_out), ", ".join(have_out[:8]), " ..." if len(have_out) > 8 else ""))
    if partial:
        print("[parallel] SKIP_DONE: %d scenario(s) hold a PARTIAL run that is scored and compared"
              " -- kept as they are, NOT re-run (KEEP_PARTIAL_RUNS): %s%s"
              % (len(partial), ", ".join(partial[:8]), " ..." if len(partial) > 8 else ""))
    if stale:
        print("[parallel] SKIP_DONE: %d finished scenario(s) on disk were run for a DIFFERENT fault list"
              " (renumbered, or markers without a fingerprint) -- moved aside as .stale and re-run: %s%s"
              % (len(stale), ", ".join(stale[:8]), " ..." if len(stale) > 8 else ""))
    if not done:
        print("[parallel] SKIP_DONE: nothing has finished yet -- running all %d"
              % len(todo))
        return selected
    if not todo:
        # EVERY SELECTED SCENARIO IS FINISHED. Returning an empty list would mean
        # "no selection", i.e. run everything -- the opposite of what is true.
        print("[parallel] SKIP_DONE: all %d selected scenario(s) are already done."
              % len(done))
        print("[parallel] Nothing to run. Set %s to run them again."
              % ("RUN_ONLY_MISSING_OUT = False and SKIP_DONE = False" if ONLY_MISSING_OUT
                 else "SKIP_DONE = False"))
        # THIS RETURN IS A SKIP-DONE SELECTION TOO. It did not say so, so the
        # marker-clearing branch downstream treated the sentinel as an explicit
        # choice -- the one case the flag exists to tell apart.
        SKIP_DONE_SELECTED = True
        return ["__ALL_ALREADY_DONE__"]
    print("[parallel] SKIP_DONE: %d already finished, %d still to run"
          % (len(done), len(todo)))
    if gave_up:
        if RETRY_GAVE_UP_ROUNDS > 0:
            print("[parallel]   %d of those gave up before (%s%s) -- RETRY_GAVE_UP_ROUNDS=%d: "
                  "their attempt budget is reset so this launch tries them again"
                  % (len(gave_up), ", ".join(gave_up[:8]),
                     " ..." if len(gave_up) > 8 else "", RETRY_GAVE_UP_ROUNDS))
            _reset_attempts(gave_up, "resume")
        else:
            print("[parallel]   %d of those gave up before (%s%s) -- they are selected "
                  "again but the attempt cap still applies"
                  % (len(gave_up), ", ".join(gave_up[:8]),
                     " ..." if len(gave_up) > 8 else ""))
    SKIP_DONE_SELECTED = True
    return todo


def _rfile_tagged(rdir, stem, ext="txt", proj=None):
    """_rfile() for THIS RUN's report names.

       A restricted run writes SPP_CRITERIA_REPORT_SELECTED_<proj>.txt. The
       launcher was looking for the untagged name, not finding it, and then
       writing its OWN untagged RUN_SUMMARY beside the study's tagged one --
       two summaries of the same scenarios, which is most of what filled the
       results folder. Reading and writing through the same tag keeps one run
       to one set of files, and leaves a previous full report untouched."""
    return _rfile(rdir, stem + _report_tag(), ext, proj)


def _report_tag():
    """"_SELECTED" when this RUN covers only part of the study, else "".

       Restricting a run and writing the full report's name over it is how a
       six-scenario report comes to be read as a complete one. Restricting it
       and writing BOTH names is how the results folder ends up with two of
       everything. One function, asked once, answers both."""
    # WHAT THE REPORT COVERS, not what the run covers. RUN_ONLY_FAULTS and
    # SKIP_DONE restrict which scenarios are SIMULATED; the report phase then
    # scores every .out in the folder, so its coverage is still complete and
    # naming it _SELECTED would understate it. Only REPORT_FAULTS and
    # ONLY_EVENTS restrict the scoring itself.
    return "_SELECTED" if (REPORT_FAULTS or ONLY_EVENTS) else ""


SHARED_FAULTS_CSV = r"{root}\FAULT_LISTS_BPM\SPP_FAULTS_CON_{project}.csv"  # as z7_main.py / z7_fault_list.py
                                             #   (old: r"{root}\SPP_FAULTS_CON_{project}.csv")


def _shared_fault_list_path():
    """This project's shared fault list, beside the case folders. "" if unknown.

       The path z7_main.py sends as SPP_FAULTS_CSV (its SHARED_FAULTS_CSV), else
       SHARED_FAULTS_CSV above -- the same file the study engine reads.
       {root} = the folder the case folders sit in, {project} = this project."""
    try:
        root = os.path.dirname(os.path.normpath(STUDY_DIR))
        if not (root and _CUR_PROJECT):
            return ""
        t = (os.environ.get("SPP_FAULTS_CSV") or SHARED_FAULTS_CSV).strip()
        return os.path.normpath(t.replace("{root}", root).replace("{project}", _CUR_PROJECT))
    except Exception:
        return ""


def _events_to_ids(events):
    """The fault ids whose planning event matches, from SPP_FAULTS.csv.

       Read from the file the study will actually run, not rebuilt here: the
       generator renumbers, and an id list derived from a second guess at the
       topology would select the wrong faults while looking right."""
    # THIS RUN'S OWN RECORD FIRST, THE SHARED LIST SECOND.
    #
    # results\faults\SPP_FAULTS.csv is written by the BUILD phase -- and the
    # selection is resolved BEFORE the build runs. On a first launch, or after
    # NEW_FAULT_LIST retired the previous results, that file does not exist yet,
    # so ONLY_EVENTS could not be resolved at the one moment it is needed.
    #
    # The shared SPP_FAULTS_<project>.csv beside the cases is the same list --
    # phase 0 writes it and every study copies it into its own folder -- and it
    # is there before any launcher starts. Reading it is what lets a P1 run be a
    # P1 run from the first scenario instead of stopping or running all 142.
    p = os.path.join(RESULTS, "faults", "SPP_FAULTS.csv")
    if not os.path.isfile(p):
        _sh = _shared_fault_list_path()
        if _sh and os.path.isfile(_sh):
            print("[parallel] ONLY_EVENTS: this run has no fault file yet -- reading the")
            print("[parallel]   shared list %s" % _sh)
            p = _sh
    toks = [str(x).strip().upper() for x in events if str(x).strip()]
    ids, seen = [], set()
    try:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                pe = (r.get("planning_event") or "").strip().upper()
                fid = (r.get("fault_id") or "").strip().upper()
                if pe:
                    seen.add(pe)
                if fid and any(pe == t or pe.startswith(t + ".") for t in toks):
                    ids.append(fid)
    except Exception as e:
        print("[parallel] could not read %s (%s) -- ONLY_EVENTS ignored" % (p, e))
        return None
    if not ids:
        print("[parallel] *** ONLY_EVENTS %s matched NO fault in %s"
              % (", ".join(toks), p))
        print("[parallel]     the list holds: %s" % (", ".join(sorted(seen)) or "(none)"))
    return ids


def _resolve_selection(items):
    """RUN_ONLY_FAULTS and ONLY_EVENTS, resolved to one list of ids."""
    sel = _resolve_selection_ids(items)
    if not ONLY_EVENTS:
        return sel
    ev = _events_to_ids(ONLY_EVENTS)
    if ev is None:
        # UNREADABLE FAULT FILE + AN EVENT SELECTION = RUN EVERYTHING. That is
        # what this used to do, and it is how one case ran 119 scenarios and the
        # other 142 in the same launch: ONLY_EVENTS = ["P1"] resolved against the
        # base case's fault file and silently did not against the project's, so
        # the project also ran the 23 P4.2 events the selection excludes.
        #
        # A selection that cannot be applied is not a selection to ignore. The
        # planning_event column lives in this case's own results\faults\
        # SPP_FAULTS.csv, written by its build; if it is not readable yet, the
        # answer is to wait for the build, not to simulate 23 extra scenarios
        # per case and discover it from a scenario count hours later.
        print("")
        print("[parallel] *** ONLY_EVENTS = %s CANNOT BE APPLIED ***"
              % ", ".join(str(x) for x in ONLY_EVENTS))
        print("[parallel]     %s" % os.path.join(RESULTS, "faults", "SPP_FAULTS.csv"))
        print("[parallel]     could not be read, so which faults are %s is not known."
              % ", ".join(str(x) for x in ONLY_EVENTS))
        print("[parallel]     Running the WHOLE list instead would simulate events you")
        print("[parallel]     deselected -- and the other case, whose file IS readable,")
        print("[parallel]     would run the selection. Two different studies, one launch.")
        print("[parallel]     Not applied: the build writes that file (a first launch builds")
        print("[parallel]     it now and tries again); otherwise the launch stops here.")
        print("")
        return ["__EVENT_LIST_UNREADABLE__"]
    if not ev:
        return ["__NO_SUCH_EVENT__"]     # matched nothing -- select nothing, not all
    if not sel:
        print("[parallel] ONLY_EVENTS %s -> %d scenario(s)"
              % (", ".join(ONLY_EVENTS), len(ev)))
        return ev
    keep = [x for x in sel if x in set(ev)]
    print("[parallel] ONLY_EVENTS %s + the id selection -> %d scenario(s)"
          % (", ".join(ONLY_EVENTS), len(keep)))
    return keep


def _resolve_selection_ids(items):
    """Expand RUN_ONLY_FAULTS: ids and ranges as before, plus the keywords above."""
    plain, out = [], []
    for raw in items:
        for tok in str(raw).replace(";", ",").split(","):
            tok = tok.strip()
            if not tok:
                continue
            if _is_select_keyword(tok):
                got = _resolve_keyword(tok)
                print("[parallel] selection '%s' -> %d scenario(s)%s"
                      % (tok.upper(), len(got),
                         (": " + ", ".join(got[:12]) + (" ..." if len(got) > 12 else ""))
                         if got else ""))
                out.extend(got)
            else:
                plain.append(tok)
    out.extend(_expand_ids(plain))
    seen, uniq = set(), []
    for s in out:
        u = s.strip().upper()
        if u and u not in seen:
            seen.add(u); uniq.append(u)
    return uniq


_RUN_STARTED = ""          # "YYYY-mm-dd HH:MM:SS" of this launcher run


def _progress_rows(since=None):
    """Latest status per scenario from the study script's PROGRESS*.csv files.
       Returns {scenario: (status, attempts, worker, time, note)}.

       `since` DROPS ROWS OLDER THAN THIS RUN. PROGRESS*.csv is appended to and
       never cleared -- FRESH_START removes .done/.attempts and the sentinels,
       not these -- so rows from earlier runs were shown as current, and a
       scenario not yet reached inherited an old verdict."""
    latest = {}
    for p in sorted(glob.glob(os.path.join(LOGS_DIR, "PROGRESS*.csv"))):
        try:
            with open(p, newline="") as fh:
                for r in csv.DictReader(fh):
                    sid = (r.get("scenario") or "").strip()
                    tm = (r.get("time") or "").strip()
                    if since and tm and tm < since:
                        continue                    # a previous run's row
                    # THE NEWEST ROW WINS, not the last file read: PROGRESS_w10
                    # sorts before PROGRESS_w2, and an old RUNNING row of one
                    # worker hid the DONE another wrote later
                    if sid and (sid not in latest or not tm or tm >= latest[sid][3]):
                        latest[sid] = ((r.get("status") or "").strip(),
                                       (r.get("attempts") or "").strip(),
                                       (r.get("worker") or "").strip(),
                                       tm,
                                       (r.get("note") or "").strip())
        except Exception:
            continue
    return latest


def _read_attempts(sid):
    try:
        with open(os.path.join(OUT_DIR, "%s.attempts" % sid)) as fh:
            return int((fh.read() or "0").strip() or "0")
    except Exception:
        return 0


_LIVE_STATUS_TOLD = [False]


def _announce_work(selected, n_work):
    """State, in one block, what this run resolved to before it starts.

       "The base case is not running" is a sentence about a folder that is not
       changing, and every cause looks the same from outside: an empty
       selection, a fault file the launcher read differently from the other
       case, every scenario already done, workers that start and exit. Each of
       those is known HERE, seconds in, and none of them was printed anywhere
       a person would find it.

       Printed AND written next to the results, because the console of a run
       launched by z7_main.py belongs to that script, not to this one."""
    L = []
    L.append("=" * 96)
    L.append(" WHAT THIS RUN WILL DO   %s   project %s   mode %s"
             % (time.strftime("%Y-%m-%d %H:%M:%S"), _CUR_PROJECT or "?",
                _CUR_MODE or "?"))
    L.append("=" * 96)
    p = _faults_csv()
    all_ids = _fault_ids()
    L.append(" fault list      %s" % p)
    L.append("                 %s"
             % ("%d scenario(s)" % len(all_ids) if all_ids
                else "*** UNREADABLE OR EMPTY -- nothing can run ***"))
    if ONLY_EVENTS:
        L.append(" ONLY_EVENTS     %s" % ", ".join(ONLY_EVENTS))
    if RUN_ONLY_FAULTS:
        L.append(" RUN_ONLY_FAULTS %s" % ", ".join(str(x) for x in RUN_ONLY_FAULTS))
    L.append(" FRESH_START     %s          SKIP_DONE  %s" % (FRESH_START, SKIP_DONE))

    # THE SENTINELS ARE NOT FAULT IDS. _apply_skip_done and the event selection
    # return a placeholder when they resolve to nothing, because an empty list
    # means "run everything" everywhere else. Printed as-is it would read as a
    # scenario called __ALL_ALREADY_DONE__ being dealt to worker 0.
    _SENTINELS = {"__ALL_ALREADY_DONE__": "every selected scenario is already finished",
                  "__NO_SUCH_EVENT__": "the event selection matched nothing in the fault list"}
    _why_none = ""
    if selected and len(selected) == 1 and selected[0] in _SENTINELS:
        _why_none = _SENTINELS[selected[0]]
        selected = []
        todo = []
    else:
        todo = selected if selected else all_ids
    n_done = sum(1 for s in (all_ids or [])
                 if os.path.isfile(os.path.join(OUT_DIR, "%s.done" % s))
                 and os.path.isfile(os.path.join(OUT_DIR, "%s.out" % s)))
    L.append("")
    L.append(" already finished %d" % n_done)
    L.append(" to run           %d" % len(todo or []))
    if not todo:
        # THE CASE THIS EXISTS FOR. Nothing to run is a legitimate outcome and
        # an indistinguishable-from-broken one, so it is said in words.
        L.append("")
        L.append(" *** THERE IS NOTHING FOR THIS CASE TO RUN ***")
        if _why_none:
            L.append("     %s" % _why_none)
        L.append("     The workers will start PSS/E and exit, and this folder will")
        L.append("     not change again. If that is not what you meant, the cause is")
        L.append("     one of the three lines above.")
    else:
        for i in range(n_work):
            mine = todo[i::n_work]
            L.append("   worker %d  %3d  %s%s"
                     % (i, len(mine), ", ".join(mine[:10]),
                        " ..." if len(mine) > 10 else ""))
    L.append("=" * 96)
    text = "\n".join(L)
    with _PRINT_LOCK:
        print(text)
    try:
        with open(_root_report("01_PLAN"), "w") as fh:
            fh.write(text + "\n")
    except Exception as e:
        print("[parallel] (could not write RUN_PLAN: %s)" % e)


def _live_status_path():
    return _root_report("00_STATUS")


# ---- ONE FILE, BOTH CASES -------------------------------------------------
# The per-study LIVE_STATUS file stays exactly where it was. This adds a single
# combined one in the study ROOT, because "how is it going" is a question about
# the LAUNCH, and answering it meant opening two files in two folders and
# reading them against each other -- while both were being rewritten.
#
# Two processes cannot safely append to one file, so neither does: each writes
# its own part beside it and then rebuilds the combined file from every part it
# finds. A rebuild that loses a race is at most one tick stale -- the next one,
# LIVE_STATUS_EVERY seconds later, has both -- and no process ever waits on
# another to write its own status.
LIVE_STATUS_ALL = (os.environ.get("SPP_LIVE_STATUS_ALL") or "").strip()
LIVE_STATUS_KEY = (os.environ.get("SPP_LIVE_STATUS_KEY") or "").strip() or "STUDY"


# WHICH RUN THIS IS, IN WORDS -------------------------------------------------
# The same project is studied several times over in one launch -- at full
# output, at a capacity level, with its machines off, once per swept .dyr value
# -- and every one of them prints the same project name and the same fault ids.
# Watching the console or the live table, there was nothing to say which.
#
# The tags are already in the environment (z7_main.py sends them, and the
# results folder is named from them); this only turns them into the phrase a
# reader wants. Folder-safe is not readable: "dyr_Kqv0p5" is a path, "Kqv=0.5"
# is a heading.
def _run_kind():
    """"capacity 50%", "project OFF", "Kqv=0.5", or "" for the study as-is."""
    cap = (os.environ.get("SPP_CAP_TAG") or "").strip()
    tag = (os.environ.get("SPP_RUN_TAG") or "").strip()
    bits = []
    if cap:
        bits.append("capacity %s%%" % cap)
    if tag == "proj_off":
        bits.append("project OFF")
    elif tag.startswith("dyr_"):
        for bit in tag[len("dyr_"):].split("_"):
            # The value's minus is written as a leading "m" and its point as a
            # "p", so the name has to stop at the first digit -- a greedy name
            # turns Kqvm0p5 into "Kqvm=0.5" instead of "Kqv=-0.5".
            m = re.match(r"^([A-Za-z]+?)(m?[0-9].*)$", bit)
            if m:
                v = m.group(2).replace("p", ".")
                if v.startswith("m"):
                    v = "-" + v[1:]
                bits.append("%s=%s" % (m.group(1), v))
            else:
                bits.append(bit)
    elif tag:
        bits.append(tag)
    return ", ".join(bits)


def _live_status_part():
    return "%s.%s.part" % (LIVE_STATUS_ALL, LIVE_STATUS_KEY)


def _side_by_side(blocks, gap="   |   "):
    """Lay text blocks in columns rather than one after another.

       Two tables stacked vertically are read by scrolling between them and
       holding the first in your head. Side by side, the same two lines -- the
       scenario counts, the same fault in each case -- are on one row, which is
       the comparison you were making anyway."""
    cols = [b.split("\n") for b in blocks if b is not None]
    if not cols:
        return ""
    if len(cols) == 1:
        return "\n".join(cols[0])
    widths = [max([len(l) for l in c] or [0]) for c in cols]
    height = max(len(c) for c in cols)
    out = []
    for i in range(height):
        row = []
        for c, w in zip(cols, widths):
            row.append((c[i] if i < len(c) else "").ljust(w))
        # Trailing blank columns are padding, not content: strip the line end
        # so a narrow window does not scroll sideways over nothing.
        out.append(gap.join(row).rstrip())
    return "\n".join(out)


# HOW OLD A CASE'S BLOCK MAY BE IN THE COMBINED FILE BEFORE IT IS CALLED FROZEN.
# Both cases rewrite their part every LIVE_STATUS_EVERY seconds, so anything
# past a few multiples of that has stopped, not slowed down.
_PART_STALE_S = 240.0


def _write_live_status_all(text):
    """This case's part, then the combined file. Never raises: the run does not
       depend on it, and the per-study file above is written either way."""
    if not LIVE_STATUS_ALL:
        return
    try:
        # WHICH RUN THIS IS, not just which case. A .dyr sweep runs the same
        # project several times over, and "PROJ SantaFe" three times in a row
        # says nothing about which value is on the machine right now.
        _kind = _run_kind()
        head = "%s  %s%s  (%s)" % (LIVE_STATUS_KEY,
                                   _CUR_PROJECT or "-",
                                   ("  [%s]" % _kind) if _kind else "",
                                   time.strftime("%Y-%m-%d %H:%M:%S"))
        with open(_live_status_part(), "w") as fh:
            fh.write(head + "\n" + text + "\n")
    except Exception:
        return
    try:
        import glob as _glob
        # BASE ON THE LEFT, PROJECT ON THE RIGHT, always. Sorted by name the
        # columns would swap the day a case is renamed, and a table whose
        # columns move is worse than one that is merely tall.
        order = {"BASE": 0, "PROJ": 1}
        parts = _glob.glob("%s.*.part" % LIVE_STATUS_ALL)
        parts.sort(key=lambda q: (order.get(q.split(".")[-2].upper(), 9), q))
        body = []
        for q in parts:
            try:
                with open(q) as fh:
                    txt = fh.read().rstrip("\n")
            except Exception:
                continue
            # A CASE THAT HAS STOPPED WRITING STILL HAS A COLUMN HERE.
            #
            # Each case writes its own part and the combined file is rebuilt
            # from whatever parts exist -- so a launcher that exited three
            # minutes ago leaves its last table sitting beside a live one,
            # looking like a study in progress. The header carries its own
            # timestamp, and nobody reads two timestamps against each other.
            # Say it in the column instead.
            try:
                m = re.search(r"\((\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\)",
                              txt.split("\n", 1)[0])
                if m:
                    age = time.time() - time.mktime(
                        time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                    if age > _PART_STALE_S:
                        lines = txt.split("\n")
                        lines.insert(1, "  *** THIS CASE STOPPED WRITING %s AGO"
                                        " -- the table below is frozen ***"
                                        % _fmt_hms(age))
                        txt = "\n".join(lines)
            except Exception:
                pass
            body.append(txt)
        txt = _side_by_side(body) if len(body) > 1 else ("\n".join(body))
        with open(LIVE_STATUS_ALL, "w") as fh:
            fh.write((txt + "\n") if txt else "")
    except Exception:
        pass


def _write_live_status_file(text):
    """Write LIVE_STATUS.txt, and SAY SO IF IT CANNOT BE WRITTEN.

       This was `except Exception: pass`. The table still printed to a console
       that scrolls, so a failure to write the one file you open from another
       window -- wrong folder, no permission, a stale handle -- looked exactly
       like a study that had not started. Reported ONCE: a message repeating
       every sixty seconds for a five-hour run is its own kind of silence."""
    p = _live_status_path()
    _write_live_status_all(text)
    try:
        with open(p, "w") as fh:
            fh.write(text + "\n")
        if _LIVE_STATUS_TOLD[0]:
            _LIVE_STATUS_TOLD[0] = False
            print("[parallel] live status is being written again -> %s" % p)
    except Exception as e:
        if not _LIVE_STATUS_TOLD[0]:
            _LIVE_STATUS_TOLD[0] = True
            print("")
            print("[parallel] *** could NOT write the live status file ***")
            print("[parallel]     %s" % p)
            print("[parallel]     %s" % e)
            print("[parallel]     The run is unaffected -- but nothing in that")
            print("[parallel]     folder will show progress, so watch this console.")
            print("")


# ---- IS THE WORKER THAT CLAIMED THIS SCENARIO STILL ALIVE? ------------------
# The same question the study's queue asks, asked the same way, so the table and
# the queue can never disagree about who is working on what. A claim file records
# its owner's PID and host; ask the operating system about that PID.
try:
    import socket as _socket
    _HOSTNAME = (_socket.gethostname() or "?").strip().replace(" ", "_")
except Exception:
    _HOSTNAME = "?"


_K32 = [None, False]        # [handle to kernel32, tried yet]

def _kernel32():
    """kernel32 with the three calls we use properly declared, or None.

       Built once. use_last_error=True is what makes ctypes.get_last_error()
       report the error from OUR call instead of whatever happened since."""
    if _K32[1]:
        return _K32[0]
    _K32[1] = True
    try:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.OpenProcess.restype = ctypes.c_void_p          # HANDLE, not int
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.WaitForSingleObject.restype = ctypes.c_uint32
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CloseHandle.restype = ctypes.c_int
        _K32[0] = k
    except Exception:
        _K32[0] = None
    return _K32[0]


def _pid_alive(pid):
    """Is that process still running? Unknown counts as alive.

       A lock is only worth waiting for while the process that took it exists,
       and a claimed scenario is only off-limits while its worker exists. The
       run killed at 17:23 left its snapshot lock behind, and the next launch
       sat on it -- correctly, by the rules it had, and uselessly, because
       nothing was ever going to release it.

       os.kill(pid, 0) is NOT used on Windows: CPython maps os.kill to
       TerminateProcess for any signal other than the console-control ones, so
       the "harmless liveness probe" of the Unix idiom would kill the very
       process it asked about.

       THE ARGUMENT AND RETURN TYPES ARE DECLARED, and that is not decoration. A
       HANDLE is pointer-sized; ctypes defaults a return value to C int, which on
       64-bit Windows keeps the low half of the handle and throws the rest away.
       The truncated value is still non-zero, so the code walks on and calls
       WaitForSingleObject on a handle that does not exist -- which returns
       WAIT_FAILED, which is not zero, which reads as "still running". Every
       process would look alive forever, and no crashed worker's scenario would
       ever be picked up again."""
    try:
        pid = int(pid)
    except Exception:
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        try:
            k = _kernel32()
            if k is None:
                return True
            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                              0, pid)
            if not h:
                # 87 ERROR_INVALID_PARAMETER: no process with that id.
                # 5  ERROR_ACCESS_DENIED:     it exists and belongs to someone else.
                import ctypes
                return ctypes.get_last_error() != 87
            try:
                # 0 = WAIT_OBJECT_0, the process handle is signalled: it exited.
                return k.WaitForSingleObject(h, 0) != 0
            finally:
                try:
                    k.CloseHandle(h)
                except Exception:
                    pass
        except Exception:
            return True                      # cannot tell -> wait, do not steal
    try:
        os.kill(pid, 0)
        return True
    except OSError as _e:
        import errno
        # EPERM: it exists, it is simply not ours to signal.
        return getattr(_e, "errno", None) == errno.EPERM
    except Exception:
        return True

def _claim_holder(path):
    """"alive" | "dead" | "unknown" -- who holds this claim.

       "alive" is the answer whenever the truth cannot be established, because
       the only thing this decides is whether to stop calling a scenario RUNNING,
       and a slow worker must never be written off."""
    try:
        with open(path, "r") as fh:
            txt = fh.read(200)
    except Exception:
        return "unknown"                   # no claim file at all
    if not (txt or "").strip():
        return "unknown"
    pid = host = None
    for tok in txt.split():
        if tok.startswith("pid"):
            pid = tok[3:]
        elif tok.startswith("host="):
            host = tok[5:]
    if pid is None:
        return "unknown"
    if host is not None and host != _HOSTNAME:
        return "alive"
    return "alive" if _pid_alive(pid) else "dead"


# THE FALLBACK ONLY. Whether a scenario is still being worked on is decided by
# asking the operating system about the process that claimed it -- see
# _claim_holder() below. This number is used ONLY when there is no claim to ask:
# no claim file, an unreadable one, or one written by another machine.
#
# ONE HOUR PER SCENARIO. Longer than anything that has ever run here, so a slow
# solve is never mistaken for a dead one.
#
# Measured against the CLAIM FILE, which the worker's keeper thread touches every
# 30 s for as long as it holds the scenario -- not against the row's own
# timestamp, which is written once at the start and only tells you how long the
# scenario has been running. Those are different questions, and confusing them is
# what made a healthy 45-minute solve show up as "stale".
#
# This is its OWN setting, not CLAIM_STALE_S. The queue's number answers "may I
# take this scenario away from its owner"; this one answers "should the table
# still show it as running". Deliberately generous: 30 minutes of no keeper beat
# (60 missed beats) is a dead process, not a slow one.
_STALE_ROW_S = 3600.0
try:
    _STALE_ROW_S = float((os.environ.get("SPP_STALE_ROW_S") or "").strip()
                         or _STALE_ROW_S)
except Exception:
    pass

# ---- HOW LONG IS LEFT ------------------------------------------------------
# "142 scenario(s): 0 done" answers how far along it is and nothing about when
# it ends, which is the question actually being asked at hour two. Every
# finished scenario leaves a <FID>.secs beside its .out holding the wall clock
# it cost, so after two or three of them the rate is measured rather than
# guessed -- and the estimate is stated with the sample size it rests on, so a
# figure from three runs is not read as a promise.
def _scenario_secs(ids):
    """[wall seconds] for every scenario that has finished and recorded one."""
    out = []
    for sid in ids:
        if str(sid).upper().startswith("FLAT"):
            continue          # 3 s, no disturbance -- not a fault scenario's cost
        try:
            p = os.path.join(OUT_DIR, "%s.secs" % sid)
            # ONLY A SCENARIO THAT ACTUALLY FINISHED. A .secs left behind by an
            # earlier launch would otherwise put a confident rate beside "0 done"
            # -- an estimate built from runs this campaign never made.
            if not (os.path.isfile(p)
                    and os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid))):
                continue
            with open(p) as fh:
                v = float((fh.read() or "0").strip().split()[0])
            if v > 0:
                out.append(v)
        except Exception:
            continue
    return out


def _eta_lines(ids, n_done, n_left, n_live, elapsed):
    """The progress bar, the measured rate, and the finish time -- or an honest
       'not yet' while there is nothing to measure."""
    total = len(ids) or 1
    frac = float(n_done) / total
    bar = "#" * int(round(frac * 40))
    out = ["  progress    [%-40s] %d/%d done  (%.1f%%)"
           % (bar, n_done, total, 100.0 * frac)]

    secs = _scenario_secs(ids)
    n_live = max(1, int(n_live or 1))
    if secs:
        mean = sum(secs) / len(secs)
        # SLOWEST AND FASTEST, because a mean over three runs hides the spread
        # and the spread here is the whole story: a pre-fault chunk costs
        # seconds and a post-fault chunk that will not converge costs minutes.
        out.append("  measured    %d scenario(s) timed: mean %s each "
                   "(fastest %s, slowest %s)"
                   % (len(secs), _fmt_hms(mean), _fmt_hms(min(secs)),
                      _fmt_hms(max(secs))))
        per = mean / float(n_live)                 # wall seconds per scenario, N at a time
        rate = 3600.0 / per if per > 0 else 0.0
        out.append("  throughput  %.1f scenario(s)/hour across %d live worker(s)"
                   % (rate, n_live))
        eta = n_left * per
        out.append("  REMAINING   %d scenario(s)  ->  about %s left, finishing near %s"
                   % (n_left, _fmt_hms(eta),
                      time.strftime("%Y-%m-%d %H:%M",
                                    time.localtime(time.time() + eta))))
        if len(secs) < 3:
            out.append("              (from %d finished run(s) -- treat as a first "
                       "guess until 3 or more)" % len(secs))
    elif n_done:
        per = elapsed / float(n_done)
        eta = n_left * per
        out.append("  REMAINING   %d scenario(s)  ->  about %s left, finishing near %s"
                   % (n_left, _fmt_hms(eta),
                      time.strftime("%Y-%m-%d %H:%M",
                                    time.localtime(time.time() + eta))))
        out.append("              (from elapsed time only -- no .secs files found)")
    else:
        out.append("  REMAINING   %d scenario(s)  ->  no finished run yet, so no "
                   "estimate can be made." % n_left)
        out.append("              The first completed scenario sets the rate; "
                   "the third makes it worth quoting.")
        if elapsed > 3600:
            out.append("              *** %s elapsed with nothing finished -- the "
                       "workers are not"
                       % _fmt_hms(elapsed))
            out.append("              completing scenarios. Check the attempt "
                       "counts below: a column of")
            out.append("              'attempt 3/4' means each one is dying and "
                       "restarting from zero. ***")
    return out





# ---- IS THIS CASE RUNNING THE WHOLE LIST? ----------------------------------
# The two cases showed 119 and 142 scenarios in the same launch, which reads as
# two different fault sets -- and it is not. The base's ids were F01, F02, F05,
# ... F142: the SAME numbering with 23 entries absent. One 142-event list,
# 23 rows this case's own copy does not carry.
#
# Which is worth saying out loud on every tick, because a subset is only visible
# by reading a hundred and forty ids and noticing the gaps.
def _shared_fault_ids():
    """The ids in the SHARED list both cases are meant to run, or []."""
    try:
        p = _shared_fault_list_path()
        if not (p and os.path.isfile(p)):
            return []
        ids = []
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                fid = (r.get("fault_id") or "").strip()
                if fid:
                    ids.append(fid)
        return ids
    except Exception:
        return []


def _coverage_lines(ids):
    """One or two lines naming what this case is NOT running, and from where.

       SILENT WHEN YOU ASKED FOR A SUBSET. With ONLY_EVENTS = ["P1"] the run is
       a P1 study and its totals, its progress bar and its estimate are all over
       the P1 set -- that is the point of asking. Measuring it against the full
       142 and calling the difference missing would turn a deliberate selection
       into a warning printed every minute."""
    if ONLY_EVENTS or RUN_ONLY_FAULTS or SKIP_DONE_SELECTED:
        what = (", ".join(str(x) for x in ONLY_EVENTS) if ONLY_EVENTS
                else "the ids you selected")
        return ["  selection   %d scenario(s) -- %s. Totals, progress and the"
                % (len(ids), what),
                "              estimate below cover THIS selection, not the whole list."]
    shared = _shared_fault_ids()
    if not shared:
        return []
    have = set(s.strip().upper() for s in ids)
    missing = [s for s in shared if s.strip().upper() not in have]
    if not missing:
        return ["  fault set   all %d event(s) of the shared list -- like for like"
                % len(shared)]
    return ["  *** this case runs %d of the %d event(s) in the shared list ***"
            % (len(ids), len(shared)),
            "      absent here: %s%s"
            % (", ".join(missing[:18]), " ..." if len(missing) > 18 else ""),
            "      Same numbering on both sides, so F57 is F57 either way -- but the",
            "      comparison has nothing to put beside these. They are usually events",
            "      naming elements only one case contains.",
            ]


def _live_status(launches, done, t0, selected=None):
    """Build the live status table, print it, and write it to LIVE_STATUS.txt.
       Everything comes from files on disk, so it is accurate even for a worker
       that has just been killed."""
    ids = selected if selected else _fault_ids()
    if not ids:
        # NO ID LIST -- AND THE ABSENCE OF THE FILE IS THE SYMPTOM. Returning
        # here wrote nothing at all, so a case whose fault list is missing or
        # unreadable looked from the folder exactly like a case that had not
        # started, while the console happily printed "running workers". The one
        # place a person looks to find out what is happening has to say
        # something, and what it has to say is which file it could not read.
        _p = _faults_csv()
        _why = ("does not exist" if not os.path.isfile(_p)
                else "exists but no fault_id column could be read from it")
        _text = "\n".join([
            "=" * 96,
            " LIVE STATUS  %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "=" * 96,
            " *** NO SCENARIO LIST -- this launcher does not know what it is running ***",
            "",
            " %s" % _p,
            "   %s" % _why,
            "",
            " Until that file is readable the workers have nothing to be measured",
            " against: no progress table, and any selection (ONLY_EVENTS,",
            " RUN_ONLY_FAULTS, SKIP_DONE) resolves to nothing.",
            "",
            " Usually one of:",
            "   * the BUILD phase has not finished, or was skipped while the fault",
            "     file was missing -- check for ALL_DONE_build.flag in this folder",
            "   * REGEN_FAULTS = \"never\" with no fault file ever written here",
            "   * the other case wrote its fault list and this one did not",
            "=" * 96,
        ])
        with _PRINT_LOCK:
            print(_text)
        _write_live_status_file(_text)
        return
    prog = _progress_rows(since=_RUN_STARTED)
    rows, n_done = [], 0
    n_run = n_crash = n_pend = n_requeue = 0
    for sid in ids:
        out_ok = os.path.isfile(os.path.join(OUT_DIR, "%s.out" % sid))
        att = _read_attempts(sid)
        st, _a, wk, tm, note = prog.get(sid, ("", "", "", "", ""))
        if os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid)) and out_ok:
            state = "DONE"; n_done += 1
        elif st == "RUNNING":
            # RUNNING IS THE LAST THING THAT SCENARIO WROTE, NOT PROOF OF LIFE.
            #
            # A worker that dies mid-scenario never gets to write anything else,
            # so its RUNNING line stands for the rest of the study. The table
            # then reads "7 running" while the console shows one live worker,
            # and the two look like a contradiction when they are answering
            # different questions.
            #
            # Two things settle it, both already on hand: the worker that wrote
            # the line has since finished, or the line has not been touched for
            # longer than a claim survives (after which the queue has already
            # given the scenario to somebody else).
            # IS ANYONE ACTUALLY ON IT? Ask the operating system, not the clock.
            #
            # The row is written ONCE, when the scenario starts, so its age is
            # how long the scenario has been running -- never evidence of a
            # problem. A 45-minute solve is normal here. What decides is whether
            # the process that claimed it still exists: the claim file carries
            # its PID, and the worker's keeper thread touches that file every
            # 30 s for as long as it holds the scenario.
            _gone = False
            try:
                _wi = int(str(wk).replace("work", "").strip())
                _gone = _wi in (done or set())
            except Exception:
                pass
            if not _gone:
                _who = _claim_holder(os.path.join(OUT_DIR, "%s.claim" % sid))
                if _who == "dead":
                    _gone = True
                elif _who == "unknown" and tm:
                    # No claim to ask -- fall back to the clock, with an hour of
                    # rope. Nothing here runs longer than that.
                    try:
                        _age = time.time() - time.mktime(
                            time.strptime(tm, "%Y-%m-%d %H:%M:%S"))
                        _gone = _age > _STALE_ROW_S
                    except Exception:
                        pass
            if _gone:
                state = "re-queued"; n_requeue += 1
                note = "worker exited -- back in the queue"
            else:
                state = "RUNNING"; n_run += 1
                # HOW LONG IT HAS BEEN ON THIS ONE. "RUNNING" and a start time
                # is two numbers to subtract; a scenario 29 minutes in and one
                # 30 seconds in are the difference between slow and stuck, and
                # that is the question being asked of this table.
                if tm:
                    try:
                        _on = time.time() - time.mktime(
                            time.strptime(tm, "%Y-%m-%d %H:%M:%S"))
                        if _on > 0:
                            # THE STUDY'S OWN NOTE STAYS. It carries the solver
                            # settings of the attempt in flight ("att 1/2: 200 it,
                            # acc 0.50, dt 2.08 ms"); the elapsed time used to
                            # replace it, so the table could not say what a
                            # scenario was being solved on.
                            _own = (note or "").strip()
                            note = "%s on this scenario%s" % (
                                _fmt_hms(_on),
                                ("  |  " + _own) if (_own.startswith("att ") or _own.startswith("retry ")) else "")
                    except Exception:
                        pass
        elif st in ("GAVE-UP", "ERROR", "FAILED") and _row_is_stale(sid, tm):
            # ITS BUDGET HAS BEEN RESET AND IT IS WAITING FOR A WORKER. The row
            # below is the previous round's; showing it as GAVE-UP reads as a
            # scenario that has been refused, which is the opposite of the truth.
            state = "QUEUED"; n_requeue += 1
            note = "attempts reset -- waiting for a worker (was: %s)" % (st or "GAVE-UP")
        elif st in ("GAVE-UP", "ERROR", "FAILED"):
            state = st; n_crash += 1
        elif att > 0:
            state = "INCOMPLETE"; n_crash += 1
        else:
            state = "not started"; n_pend += 1
        rows.append((sid, state, att, wk, tm, note))

    # ---- ONE WORKER RUNS ONE SCENARIO -------------------------------------
    # A worker is a single PSS/E process running one case at a time, so two
    # rows saying RUNNING on work0 cannot both be true. The table said exactly
    # that: F02 and F03 both RUNNING on work0, and "6 running" beside five live
    # workers -- because RUNNING is read from the last line each scenario wrote,
    # and a scenario abandoned mid-way never gets to write another.
    #
    # The staleness rule alone cannot catch it: both lines were minutes old, far
    # inside the half-hour a claim survives. What settles it is the invariant.
    # Per worker, the NEWEST claim is the one it is actually on; every older
    # claim of that worker's belongs to a scenario it walked away from, so it
    # goes back to the queue where the study has already put it.
    _newest = {}
    for i, (sid, state, att, wk, tm, note) in enumerate(rows):
        if state != "RUNNING" or not wk:
            continue
        prev = _newest.get(wk)
        if prev is None or (tm or "") > (rows[prev][4] or ""):
            _newest[wk] = i
    _keep = set(_newest.values())
    for i, r in enumerate(rows):
        sid, state, att, wk, tm, note = r
        if state == "RUNNING" and wk and i not in _keep:
            # THE REASON REPLACES THE NOTE, it does not defer to it. The note
            # already there is "attempt 1/4", which is what the scenario was
            # doing -- not why the table stopped believing it, which is the
            # only thing this row has left to say.
            rows[i] = (sid, "re-queued", att, wk, tm,
                       "%s restarted -- back in the queue" % wk)
            n_run -= 1
            n_requeue += 1

    el = time.time() - t0
    lines = []
    lines.append("=" * 96)
    lines.append(" LIVE STATUS  %s   elapsed %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), _fmt_hms(el)))
    if _phase_line():
        lines.append("  " + _phase_line())
    # WHAT IS BEING RUN, not just how far along it is. Which project, which
    # fault mode, and which of that project's runs -- the numbers below mean
    # something different for each.
    lines.append("  %s%s%s" % (_CUR_PROJECT or "(default)",
                               ("  %s faults" % _CUR_MODE) if _CUR_MODE else "",
                               ("   ***  %s  ***" % _run_kind()) if _run_kind()
                               else "   (project as studied, full output)"))
    lines.append("=" * 96)
    lines.append("  %d scenario(s): %d done, %d running, %d crashed/incomplete, "
                 "%d not started%s"
                 % (len(ids), n_done, n_run, n_crash, n_pend,
                    (", %d re-queued" % n_requeue) if n_requeue else ""))
    if n_requeue:
        lines.append("  're-queued' / 'QUEUED' = nobody is on it right now -- the worker "
                     "that started")
        lines.append("  it restarted, or a retry round reset its attempts. It is in the "
                     "queue and WILL")
        lines.append("  be run: nothing has been skipped. Only two run at once in a retry "
                     "round, so")
        lines.append("  the rest wait their turn; the [W0]/[W1] lines above show what is "
                     "moving.")
    wtxt = ", ".join("W%d=%d/%d" % (i, launches.get(i, 0), MAX_LAUNCHES_PER)
                     for i in sorted(launches))
    lines.append("  relaunches used: %s   (workers finished: %s)"
                 % (wtxt or "n/a", sorted(done) if done else "none yet"))
    try:
        lines.extend(_coverage_lines(ids))
    except Exception:
        pass
    lines.append("-" * 96)
    # HOW FAR ALONG, AT WHAT RATE, AND WHEN IT ENDS.
    try:
        lines.extend(_eta_lines(ids, n_done, len(ids) - n_done,
                                max(1, len(launches) - len(done or set())), el))
    except Exception as _e:
        lines.append("  (could not estimate the remaining time: %s)" % _e)
    lines.append("-" * 96)
    hdr = ("  %-11s %-12s %-4s %-7s %-20s %s"
           % ("scenario", "state", "att", "worker", "last update", "note"))

    def _rowtxt(r):
        sid, state, att, wk, tm, note = r
        return ("  %-11s %-12s %-4d %-7s %-20s %s"
                % (sid, state, att, wk or "-", tm or "-", note[:72]))

    # THE FILE GETS EVERY SCENARIO. THE CONSOLE GETS THE ONES DOING SOMETHING.
    #
    # A hundred and thirty lines of "not started" printed every sixty seconds,
    # twice over for two cases, is how the seven lines that matter get lost --
    # and they are the whole reason to look. LIVE_STATUS.txt is a file you open
    # deliberately and scroll, so it keeps the full list; the console shows what
    # has moved and counts the rest.
    full = list(lines)
    full.append(hdr)
    shown = rows if not LIVE_STATUS_ROWS else rows[:LIVE_STATUS_ROWS]
    for r in shown:
        full.append(_rowtxt(r))
    if len(rows) > len(shown):
        full.append("  ... %d more (set LIVE_STATUS_ROWS = 0 to list every one)"
                    % (len(rows) - len(shown)))
    full.append("=" * 96)
    text = "\n".join(full)

    if LIVE_STATUS_QUIET_ROWS:
        live = [r for r in rows if r[1] != "not started"]
        con = list(lines)
        con.append(hdr)
        for r in live:
            con.append(_rowtxt(r))
        if not live:
            con.append("  (nothing has started yet)")
        n_hidden = len(rows) - len(live)
        if n_hidden:
            con.append("  ... %d not started (every scenario is listed in %s)"
                       % (n_hidden, os.path.basename(_live_status_path())))
        con.append("=" * 96)
        console = "\n".join(con)
    else:
        console = text

    with _PRINT_LOCK:
        print(console)
    _write_live_status_file(text)


def _faults_csv():
    return os.path.join(RESULTS, "faults", "SPP_FAULTS.csv")


def _have_fault_file():
    p = _faults_csv()
    try:
        return os.path.isfile(p) and os.path.getsize(p) > 0
    except Exception:
        return False


def _check_selection(sel):
    """Warn about ids that are not in the fault definition file, and say what is."""
    known = [s.upper() for s in _fault_ids()]
    if not known:
        print("[parallel] fault file not readable yet -- the build phase will create it")
        return sel
    bad = [s for s in sel if s != "FLAT_RUN" and s not in known]
    if bad:
        print("[parallel] *** not in %s: %s ***" % (_faults_csv(), ", ".join(bad)))
        print("[parallel]     the file holds %d id(s), %s ... %s"
              % (len(known), ", ".join(known[:5]), known[-1]))
    return sel


def _fault_ids():
    """Read the fault IDs (in order) the BUILD phase wrote to SPP_FAULTS.csv, so the launcher
       can show the worker->fault assignment up front."""
    p = os.path.join(RESULTS, "faults", "SPP_FAULTS.csv")
    ids = []
    try:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                fid = (r.get("fault_id") or "").strip()
                if fid: ids.append(fid)
    except Exception:
        pass
    return ids

def _print_assignment(n):
    """Print which worker will run which fault IDs (round-robin faults[i::n])."""
    ids = _fault_ids()
    if not ids:
        print("[parallel] (fault list not readable yet -- each worker will print its own slice)")
        return
    print("[parallel] %d fault(s) total, split round-robin across %d worker(s):" % (len(ids), n))
    for i in range(n):
        mine = ids[i::n]
        print("   worker %d (%d): %s" % (i, len(mine), ", ".join(mine)))


def _run_phase(role, suffix, n=1, widx=0):
    """Run a single-process phase (build or report) with crash-relaunch until its
       sentinel appears (build) or it exits cleanly (report writes no sentinel).

       Launched exactly like a worker -- piped, tagged, activity-stamped -- and
       KILLED if it goes silent for PHASE_TIMEOUT_S.

       It used to be a bare subprocess.call(), which waits forever. When a report
       process froze instead of exiting, the launcher waited on it and the three
       remaining projects in the queue never started. A phase that cannot finish
       must cost one timeout, not the whole run."""
    cap = MAX_LAUNCHES_BUILD if role == "build" else (
          MAX_LAUNCHES_REPORT if role == "report" else MAX_LAUNCHES_PER)
    _clear(_sentinel(suffix))
    tag = "[%s]" % role[:4].upper()
    att, lic_fails = 0, 0
    while att < cap:
        att += 1
        _banner("%s phase: launch %d/%d  (killed if silent for %ds)"
                % (role.upper(), att, cap, PHASE_TIMEOUT_S))
        idx = -1                                   # its own _LAST_ACTIVITY slot, not a worker's
        _LAST_ACTIVITY[idx] = time.time()
        _t_launch = time.time()
        p = subprocess.Popen([PYTHON, "-u", STUDY_SCRIPT], cwd=STUDY_DIR,
                             env=_env(role, widx, n),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, bufsize=1)
        _register_child("phase:%s" % role, p)
        th = threading.Thread(target=_pump, args=(idx, tag, p, "phase:%s" % role)); th.daemon = True; th.start()
        killed = False
        licence = None
        while p.poll() is None:
            time.sleep(POLL_SECS)
            # A LICENCE BOX MEANS THIS PSS/E IS NOT GOING TO RUN. Kill it now
            # (it holds nothing) and come back after a pause.
            licence = _licence_hit("phase:%s" % role, _t_launch)
            if licence:
                _banner("%s phase: PSS/E could not take a licence ('%s') -- stopping this "
                        "launch, will retry after a pause" % (role.upper(), licence[0]))
                try: p.kill()
                except Exception: pass
                killed = True
                break
            idle = time.time() - _LAST_ACTIVITY.get(idx, time.time())
            if idle > PHASE_TIMEOUT_S:
                # never_kill=False: this phase is ONE process. NEVER_KILL_WORKERS
                # is about worker slots, and a frozen slot costs one of N. A
                # frozen build or report costs every project still in the queue.
                if not _quiet_watch("phase:%s" % role, "the %s phase" % role,
                                    idle, PHASE_TIMEOUT_S, never_kill=False):
                    continue
                _banner("%s phase HUNG (no output for %ds > %ds) -- killing it so the "
                        "rest of the queue can continue" % (role.upper(), int(idle), PHASE_TIMEOUT_S))
                try: p.kill()
                except Exception: pass
                killed = True
                break
        th.join(timeout=5)                         # drain whatever it printed last
        rc = p.poll()
        print("[parallel] %s exited rc=%s%s" % (role, rc, "  (KILLED -- hung)" if killed else ""))
        if licence or rc == EXIT_LICENCE_BUSY:
            # NOT COUNTED AGAINST THE CAP: nothing was tried. Wait, doubling
            # each time, so the licence runtime is left alone to recover.
            lic_fails += 1
            att -= 1
            if lic_fails > MAX_LICENCE_FAILS:
                _banner("%s phase: PSS/E failed to take a licence %d times in a row -- giving up. "
                        "Restart the 'CodeMeter Runtime Server' service (services.msc, or CodeMeter "
                        "Control Center > Process > Restart CodeMeter Service) and launch again; "
                        "finished work is kept." % (role.upper(), lic_fails))
                return False
            _wait = min(LICENCE_BACKOFF_S * (2 ** (lic_fails - 1)), LICENCE_BACKOFF_MAX_S)
            print("[parallel] %s: licence failure %d/%d -- relaunching in %s"
                  % (role, lic_fails, int(MAX_LICENCE_FAILS), _fmt_hms(_wait)))
            time.sleep(_wait)
            continue
        if not killed:
            if role == "report":
                if rc == 0:
                    return True                    # report writes no sentinel; clean exit == done
            elif os.path.isfile(_sentinel(suffix)):
                return True
        if att < cap:
            time.sleep(PAUSE_BETWEEN)
    print("[parallel] *** %s phase did not finish after %d launch(es) -- continuing anyway ***"
          % (role, cap))
    return False


_REPORT_BG = []      # [{proj, mode, dir, proc, thread, t0}] reports still running


def _start_report_bg(proj, mode, rdir):
    """Spawn the report for one project and RETURN IMMEDIATELY.

       Post-processing only: it reads .out files that are already written and
       touches nothing the next project needs. Blocking on it just idled the
       machine -- with 6 workers finished and one report grinding, five cores sat
       doing nothing while three projects waited their turn."""
    _wait_for_report_slot()
    clear_badout(os.path.join(rdir, "outs"))
    key = "rep:%s/%s" % (proj or "default", mode)
    _LAST_ACTIVITY[key] = time.time()
    p = subprocess.Popen([PYTHON, "-u", STUDY_SCRIPT], cwd=STUDY_DIR,
                         env=_env("report", 0, 1),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, bufsize=1)
    _register_child("report-bg", p)
    th = threading.Thread(target=_pump, args=(key, "[R:%s]" % (proj or "def")[:8], p, "report-bg"))
    th.daemon = True; th.start()
    _REPORT_BG.append({"proj": proj, "mode": mode, "dir": rdir,
                       "proc": p, "thread": th, "t0": time.time(), "key": key})
    print("[parallel] REPORT for %s/%s started IN THE BACKGROUND -- moving on to the "
          "next project now (its lines are tagged [R:%s])"
          % (proj or "default", mode, (proj or "def")[:8]))
    return True


def _reap_reports(block=False, quiet=False):
    """Collect finished background reports. block=True waits for all of them.

       A report that overruns REPORT_BG_TIMEOUT_S is KILLED. Its scenarios keep
       their .out files and their plots -- only the merged criteria report for
       that project is missing, and the roll-up says so by name rather than
       leaving a blank."""
    while _REPORT_BG:
        still = []
        for r in _REPORT_BG:
            rc = r["proc"].poll()
            el = time.time() - r["t0"]
            # SILENCE, not just total runtime. Background reports had no idle
            # watchdog at all, so a frozen one held the launcher for the full
            # REPORT_BG_TIMEOUT_S. The workers have had this check all along.
            _idle = time.time() - _LAST_ACTIVITY.get(r["key"], r["t0"])
            if rc is None and _idle > REPORT_SILENT_S:
                if _quiet_watch("rep:%s/%s" % (r["proj"], r["mode"]),
                                "the background report for %s/%s" % (r["proj"], r["mode"]),
                                _idle, REPORT_SILENT_S, never_kill=False):
                    print("[parallel] *** background report for %s/%s has printed NOTHING for "
                          "%s (> REPORT_SILENT_S=%ds) -- treating it as hung and killing it ***"
                          % (r["proj"], r["mode"], _fmt_hms(_idle), REPORT_SILENT_S))
                    try: r["proc"].kill()
                    except Exception: pass
                    rc = r["proc"].poll()
            if rc is None and el > REPORT_BG_TIMEOUT_S and not NEVER_KILL_WORKERS:
                print("[parallel] *** background report for %s/%s has run %s "
                      "(> REPORT_BG_TIMEOUT_S=%ds) -- killing it ***"
                      % (r["proj"], r["mode"], _fmt_hms(el), REPORT_BG_TIMEOUT_S))
                try: r["proc"].kill()
                except Exception: pass
                rc = r["proc"].poll()
            if rc is None:
                still.append(r); continue
            r["thread"].join(timeout=5)
            ok = os.path.isfile(_rfile(r["dir"], "SPP_CRITERIA_REPORT", "txt"))
            print("[parallel] background report for %s/%s finished rc=%s after %s -- %s"
                  % (r["proj"], r["mode"], rc, _fmt_hms(el),
                     "SPP_CRITERIA_REPORT.txt written"
                     if ok else "*** NO SPP_CRITERIA_REPORT.txt ***"))
        del _REPORT_BG[:]
        _REPORT_BG.extend(still)
        if not block or not _REPORT_BG:
            return
        if not quiet:
            # How long it has been RUNNING and how long since it last printed. The
            # bare "waiting for 1 background report" repeated every ten seconds
            # said nothing about whether it was progressing.
            _now = time.time()
            print("[parallel] waiting for %d background report(s): %s"
                  % (len(_REPORT_BG),
                     ", ".join("%s/%s running %s, silent %s"
                               % (r["proj"], r["mode"], _fmt_hms(_now - r["t0"]),
                                  _fmt_hms(_now - _LAST_ACTIVITY.get(r["key"], r["t0"])))
                               for r in _REPORT_BG)))
        time.sleep(POLL_SECS * 2)


def _wait_for_report_slot():
    """Block until fewer than REPORT_MAX_BG reports are running.

       This is what keeps the concurrent PSS/E session count bounded. Without it
       four projects could have four reports running beside six workers."""
    while True:
        _reap_reports(block=False, quiet=True)
        if len(_REPORT_BG) < max(1, REPORT_MAX_BG):
            return
        print("[parallel] %d background report(s) already running (REPORT_MAX_BG=%d) "
              "-- waiting for a slot before starting another"
              % (len(_REPORT_BG), REPORT_MAX_BG))
        time.sleep(POLL_SECS * 2)


# Markers the launcher needs the STUDY SCRIPT to have. Each is a literal that
# exists only in the version implementing that feature.
_STUDY_FEATURES = [
    ("REPORT_STATUS",       "per-shard status files (the progress line reads these)"),
    ("SPP_ONLY selects",    "report-side id filter (REPORT_FAULTS depends on it)"),
    ("_report_w%d",         "a separate log per report shard"),
    ("SCORE_AT_RUN_TIME",   "workers score as they run"),
    ("ISOLATE_OUT_READS",   "isolated .out reads (survives a NaN crash)"),
    ("LIVE_REPORT",         "report kept current during the run"),
    ("_balanced_slice",     "even fault-type split across workers"),
    ("_rstat(",             "startup stages reported (else the line reads 'starting')"),
    ('op + ".badout"',      "no full byte-scan of every .out before scoring starts"),
    ("_heartbeat_start",    "[alive] heartbeat while scoring (else working shards get killed)"),
    ("_outs[WORKER_INDEX::N_WORKERS]", "shards partition the RAW file list (no overlap/gaps)"),
    ("SPP_REPORT_SHARDS",   "merge reads only this run's parts"),
    ("def report_path",     "project-named report files + WRITE_CSV switch"),
]


def check_study_version():
    """Warn when the study script is older than this launcher expects.

       These two files are edited together and shipped together, and running a
       new launcher against an older study script fails in ways that look like
       bugs rather than a mismatch: REPORT_FAULTS is silently ignored and every
       scenario is scored anyway; the progress line reads "starting" for ever
       because nothing is writing the status files. Both were reported as faults
       when the real answer was that the pair had drifted apart."""
    try:
        txt = open(STUDY_SCRIPT, errors="replace").read()
    except Exception as e:
        print("[parallel] could not read %s to check its version (%s)" % (STUDY_SCRIPT, e))
        return True
    # WRITE_CSV lives in both files and means the same thing in each. They are
    # edited separately, so they drift; a launcher writing no roll-up CSV beside
    # a study writing every per-project CSV looks like a bug in one of them.
    _m = re.search(r"^WRITE_CSV\s*=\s*(True|False)", txt, re.M)
    if _m and (_m.group(1) == "True") != bool(WRITE_CSV):
        print("[parallel] NOTE: WRITE_CSV is %s here and %s in %s -- set them the same"
              % (WRITE_CSV, _m.group(1), os.path.basename(STUDY_SCRIPT)))
    missing = [(m, d) for m, d in _STUDY_FEATURES if m not in txt]
    if not missing:
        return True
    print("")
    print("[parallel] *** %s IS OLDER THAN THIS LAUNCHER ***" % STUDY_SCRIPT)
    print("[parallel]     It is missing %d feature(s) this launcher relies on:" % len(missing))
    for m, d in missing:
        print("[parallel]       - %s" % d)
    print("[parallel]     Consequences you will actually see:")
    if any(m == "SPP_ONLY selects" for m, _d in missing):
        print("[parallel]       * REPORT_FAULTS is IGNORED -- every scenario is scored")
    if any(m == "REPORT_STATUS" for m, _d in missing):
        print("[parallel]       * the progress line stays on 'starting' -- watch the [S#]")
        print("[parallel]         lines instead; the shards ARE working")
    print("[parallel]     Use the %s that came with this launcher."
          % os.path.basename(STUDY_SCRIPT))
    print("")
    return False


def _report_selection():
    """The ids REPORT_FAULTS resolves to, or None for 'score everything'."""
    if not REPORT_FAULTS and not ONLY_EVENTS:
        return None
    sel = _resolve_selection(REPORT_FAULTS)
    if not sel:
        print("[parallel] *** REPORT_FAULTS resolved to NOTHING -- scoring nothing rather "
              "than silently scoring everything ***")
        return []
    print("[parallel] REPORT_FAULTS -> %d scenario(s): %s"
          % (len(sel), ", ".join(sel[:16]) + (" ..." if len(sel) > 16 else "")))
    return sel


def clear_badout(outs_dir):
    """Remove .badout markers so previously-condemned scenarios can be scored.

       See CLEAR_BADOUT. A marker only ever means "the last attempt to read this
       file went wrong"; it is not evidence about the file itself, and an
       earlier build wrote them wholesale off a channel-blind byte scan. Any
       file that really does crash the reader is re-marked the moment it does,
       so clearing costs at most one retry per genuinely bad file."""
    if not CLEAR_BADOUT or not os.path.isdir(outs_dir):
        return 0
    gone = 0
    for f in glob.glob(os.path.join(outs_dir, "*.badout")):
        try:
            os.remove(f); gone += 1
        except Exception as e:
            print("[parallel] could not remove %s: %s" % (os.path.basename(f), e))
    if gone:
        print("[parallel] cleared %d .badout marker(s) in %s" % (gone, outs_dir))
        print("[parallel]     those scenarios were being SKIPPED unscored; they are now")
        print("[parallel]     back in the report. Anything that truly crashes the reader")
        print("[parallel]     will be marked again as soon as it does.")
    return gone


def _run_report_sharded(n, selected=None):
    """Score the report with n processes, then merge. Returns True if the merge
       wrote a report.

       The shards are launched together and all must finish before the merge
       runs -- unlike the fault workers, a partial merge would silently produce
       a report covering some of the study while looking complete."""
    _banner("REPORT: %d shard(s) scoring in parallel, then one merge" % n)
    _phase("report shards (%d)" % n)
    clear_badout(OUT_DIR)
    # WHEN THIS SCORING PASS BEGAN. Every shard and every relaunch gets the
    # same stamp (SPP_RESCORE_T0): under FORCE_RESCORE a per-scenario part
    # newer than it was written by THIS pass under the current rules, so a
    # shard that reaches a scenario another shard has already re-scored takes
    # the rows instead of reading the .out a second time.
    _t0_pass = time.time()
    # WIPE THE OLD STATUS FILES FIRST. They are rewritten by each shard as it
    # starts a scenario, and they are NOT cleared between runs -- so a new run
    # displayed the previous run's positions until each shard happened to write
    # its first one. That is how a status line could read "S3 17/24  ~94 scored"
    # while every shard had just printed "[score] 1/24": the counts were hours
    # old and belonged to a different run.
    for _f in glob.glob(os.path.join(LOGS_DIR, "REPORT_STATUS_report_w*.txt")):
        try: os.remove(_f)
        except Exception: pass
    # Say EXACTLY what each shard is being given, before launching. If the
    # selection did not take, the numbers here show it immediately instead of
    # after an hour of scoring the wrong set.
    if selected:
        print("[parallel] %d selected scenario(s) split across %d shard(s):"
              % (len(selected), n))
        if DYNAMIC_WORK:
            print("   shared queue -- each shard takes the next unscored one, so no")
            print("   shard waits on another. %d scenario(s), %d shard(s)." % (len(selected), n))
        else:
            for i in range(n):
                _m = selected[i::n]
                print("   shard %d (%d): %s" % (i, len(_m), ", ".join(_m)))
    else:
        print("[parallel] no selection -- every .out in the folder will be scored, "
              "split across %d shard(s)" % n)
    procs, threads, launches = {}, {}, {}
    t_launch  = {}                    # i -> time of its current launch
    lic_fails = {i: 0 for i in range(n)}
    pending   = {}                    # i -> time at which to relaunch it (licence backoff)

    def _start(i):
        launches[i] = launches.get(i, 0) + 1
        pending.pop(i, None)
        key = "rep%d" % i
        _LAST_ACTIVITY[key] = time.time()
        # Each shard gets its OWN slice of the selection, so the shards divide
        # the selected scenarios rather than every one of them scoring all of
        # the selected set.
        _mine = None if not selected else (
            list(selected) if DYNAMIC_WORK else selected[i::n])
        _e = _env("report", i, n, _mine)
        # FORCE_RESCORE ON THE FIRST LAUNCH ONLY. A shard that starts under it
        # discards its saved part file and scores from nothing -- which is the
        # point. A RELAUNCH of that shard after a crash must not: it would
        # discard the scenarios the first launch had already finished, score
        # from nothing again, crash again, and after three launches leave
        # parts\ empty. That happened -- six shards, 8 minutes, zero parts,
        # "no report parts -- nothing to merge". The rescore was asked for
        # once; the relaunch is a resume.
        # ...BUT NOT BY TURNING FORCE_RESCORE OFF. That was the previous fix,
        # and it swapped one wrong report for another: with the flag off the
        # relaunched shard took every remaining scenario "from the worker's
        # score" -- the OLD rule's verdicts -- so one crash left half a
        # project rescored and half not, in the same workbook. Now the flag
        # stays on and SPP_RESCORE_RESUME=1 tells the shard to KEEP its part
        # (the scenarios it already re-scored) while still reading every
        # other .out afresh.
        # The launch's own stamp when z7_main sent one (every pass of the
        # launch shares it); this pass's start otherwise.
        _e["SPP_RESCORE_T0"] = _e.get("SPP_RESCORE_T0") or repr(_t0_pass)
        if launches[i] > 1 and _e.get("SPP_FORCE_RESCORE", "0") not in ("", "0", "false", "no", "off"):
            _e["SPP_RESCORE_RESUME"] = "1"
            print("[parallel] shard %d relaunch: FORCE_RESCORE stays ON -- it keeps the "
                  "scenarios it already re-scored and reads the rest from the .out" % i)
        # THROUGH THE LICENCE GATE, LIKE EVERY OTHER PSS/E START. A shard takes
        # a PSS/E session too; six of them started in the same second were
        # refused by the licence runtime and burned their MAX_LAUNCHES_REPORT.
        _licence_gate("shard %d" % i)
        t_launch[i] = time.time()
        procs[i] = subprocess.Popen([PYTHON, "-u", STUDY_SCRIPT], cwd=STUDY_DIR,
                                    env=_e,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, bufsize=1)
        _register_child("shard%d" % i, procs[i])
        th = threading.Thread(target=_pump, args=(key, "[S%d]" % i, procs[i], "shard%d" % i))
        th.daemon = True; th.start(); threads[i] = th

    for i in range(n):
        _start(i)
    t0 = time.time()

    # ONE SUPERVISORY LOOP, PER SHARD.
    #
    # This used to take the MAXIMUM idle time across all shards and, when that
    # exceeded the limit, kill EVERY shard still running. So one shard stuck on
    # a bad .out destroyed five that were working perfectly well -- the opposite
    # of what a watchdog is for. Each shard is now judged on its OWN silence,
    # killed alone, and relaunched alone while the others carry on. A relaunched
    # shard condemns the file that stalled it and resumes from its part file.
    done = set()
    while len(done) < n:
        time.sleep(POLL_SECS)
        now = time.time()
        # --- relaunches that were put off by the licence backoff
        for i in list(pending):
            if i not in done and now >= pending[i]:
                _start(i)
        now = time.time()
        for i in range(n):
            if i in done or i in pending:
                continue
            rc = procs[i].poll()
            if rc is None:
                idle = now - _LAST_ACTIVITY.get("rep%d" % i, t0)
                if idle > SHARD_STALL_S:
                    # A report shard holds a PSS/E session but no scenario, and
                    # killing one leaves the others scoring. Not a worker slot.
                    if _quiet_watch("shard%d" % i, "report shard %d" % i, idle,
                                    SHARD_STALL_S, never_kill=False):
                        print("[parallel] *** shard %d has printed nothing for %s "
                              "(> SHARD_STALL_S=%ds) -- killing THAT shard only; the others "
                              "keep going ***" % (i, _fmt_hms(idle), SHARD_STALL_S))
                        try: procs[i].kill()
                        except Exception: pass
                continue
            # it has exited
            if threads.get(i):
                threads[i].join(timeout=3)
            if rc == 0:
                done.add(i)
                print("[parallel] shard %d finished cleanly" % i)
                continue
            # A LICENCE REFUSAL IS NOT A SHARD FAILURE. PSS/E never started, so
            # nothing was scored: the launch is not charged against
            # MAX_LAUNCHES_REPORT and the shard is relaunched after the same
            # backoff _run_workers uses (LICENCE_BACKOFF_S doubling, capped at
            # LICENCE_BACKOFF_MAX_S). Past MAX_LICENCE_FAILS in a row it is
            # treated as an ordinary failure below.
            # (a licence box only counts while the shard was still starting --
            # _DIALOG_HITS is never pruned, same guard as _run_workers)
            _lic = (rc == EXIT_LICENCE_BUSY
                    or ((now - t_launch.get(i, now)) < STARTUP_DEAD_S
                        and _licence_hit("shard%d" % i, t_launch.get(i, 0))))
            if _lic and lic_fails[i] < MAX_LICENCE_FAILS:
                lic_fails[i] += 1
                launches[i] -= 1
                _w = min(LICENCE_BACKOFF_S * (2 ** (lic_fails[i] - 1)), LICENCE_BACKOFF_MAX_S)
                pending[i] = time.time() + _w
                print("[parallel] shard %d: PSS/E could not take a licence (rc=%s) -- start "
                      "failure %d/%d, relaunching in %s (no launch charged)"
                      % (i, rc, lic_fails[i], int(MAX_LICENCE_FAILS), _fmt_hms(_w)))
                continue
            if not _lic:
                lic_fails[i] = 0
            if launches[i] < MAX_LAUNCHES_REPORT:
                print("[parallel] shard %d exited rc=%s -- relaunch %d/%d (it will skip "
                      "whatever killed it and resume from its part file)"
                      % (i, rc, launches[i] + 1, MAX_LAUNCHES_REPORT))
                time.sleep(PAUSE_BETWEEN)
                _start(i)
            else:
                done.add(i)
                print("[parallel] *** shard %d gave up after %d launch(es); the scenarios "
                      "it never reached will show as unscored ***" % (i, launches[i]))
        # progress line, from the status files each shard writes
        _st, _tot = [], 0
        for i in range(n):
            _txt = ""
            try:
                with open(os.path.join(LOGS_DIR, "REPORT_STATUS_report_w%d.txt" % i)) as _fh:
                    _txt = _fh.read().strip()
            except Exception:
                pass
            _m = re.search(r"\s(\d+)/(\d+)\s", _txt)
            if i in done:
                _st.append("S%d done" % i)
            elif "FINISHED" in _txt:
                _st.append("S%d wrapping" % i)
            elif _m:
                _st.append("S%d %s/%s" % (i, _m.group(1), _m.group(2)))
            else:
                _st.append("S%d starting" % i)
        # HOW MANY SCENARIOS ARE ACTUALLY SCORED -- COUNTED, NOT ADDED UP.
        #
        # This summed the four shard counters, and every shard counts the whole
        # set it RESUMED from parts\ as well as the ones it scored itself. All
        # four resume the same ~40 cases, so the sum quadruple-counted them and
        # the line read "~83 scored" over a selection of 50 -- a number that
        # cannot happen, and one that looks like the shards are redoing each
        # other's work. The verdict parts hold one row per scenario, so the
        # distinct count is there to be read; they are small CSVs and this runs
        # once every five seconds.
        _tot = 0
        try:
            _cases = set()
            for _vp in glob.glob(os.path.join(RESULTS, "parts", "VERDICTS_w*.csv")):
                try:
                    with open(_vp) as _vf:
                        for _ln in _vf.read().splitlines()[1:]:
                            _c = _ln.split(",")[0].strip()
                            if _c:
                                _cases.add(_c)
                except Exception:
                    continue
            _tot = len(_cases)
        except Exception:
            _tot = 0
        print("[parallel] report %s   elapsed %s%s"
              % ("  ".join(_st), _fmt_hms(time.time() - t0),
                 ("   %d scenario(s) scored" % _tot) if _tot else ""))
        _pl = _phase_line()
        if _pl:
            print("[parallel] " + _pl)

    _phase("report merge")
    _banner("REPORT: merging %d shard(s)" % n)
    # THE MERGE MUST KNOW IT IS A FILTERED REPORT. Without the selection in its
    # environment it writes the untagged SPP_CRITERIA_REPORT.txt -- so scoring
    # six chosen scenarios would REPLACE a complete 142-scenario report with one
    # that looks complete and is not. Passing the ids makes it write
    # SPP_CRITERIA_REPORT_SELECTED.txt instead.
    # ONE SHARD HAS NOTHING TO MERGE.
    #
    # With n == 1 the shard is launched with SPP_N_WORKERS=1, takes the
    # un-sharded branch, and writes the complete report itself -- leaving no
    # part file. Running the merge afterwards then rebuilt that report from
    # whatever CRITERIA_w0.csv an earlier run had left in parts\\, replacing a
    # correct report with an older one, and the violations, measurements and run
    # summary with it. There is nothing here that needs merging.
    if n <= 1:
        print("[parallel] one shard -- it wrote the report itself, so there is "
              "nothing to merge")
        return True
    e = _env("report", 0, 1, selected)
    e["SPP_REPORT_MERGE"] = "1"
    # HOW MANY SHARDS THIS RUN HAD. parts\\ survives between runs on purpose -- a
    # killed shard resumes from its part file -- so a 2-shard run after a 6-shard
    # one would otherwise merge CRITERIA_w0..w5 and fold four parts of an older,
    # unrelated report into this one. The merge reads only w0..w(n-1).
    e["SPP_REPORT_SHARDS"] = str(n)
    # NOT A BARE subprocess.call(). That is what this was, and it is the one
    # spawn in this file the dialog sweeper could not see and no watchdog
    # timed. The merge imports psspy like every other child, so it can meet the
    # CodeMeter "runtime busy" box like every other child -- and when it did,
    # unregistered, the box stayed up and the launcher waited on it. Five and
    # a half hours, with two projects behind it in the queue that never ran.
    # Same treatment as the build and the shards: registered (so the sweeper
    # closes the box), piped (so silence is measurable), killed after
    # PHASE_TIMEOUT_S, and retried after a licence hit.
    rc = None
    _tries, _lic = 0, 0
    while _tries < 3:
        _tries += 1
        _t_launch = time.time()
        _LAST_ACTIVITY[-2] = _t_launch
        p = subprocess.Popen([PYTHON, "-u", STUDY_SCRIPT], cwd=STUDY_DIR, env=e,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, bufsize=1)
        _register_child("report-merge", p)
        th = threading.Thread(target=_pump, args=(-2, "[MERG]", p)); th.daemon = True; th.start()
        killed, licence = False, None
        while p.poll() is None:
            time.sleep(POLL_SECS)
            licence = _licence_hit("report-merge", _t_launch)
            if licence:
                _banner("REPORT merge: PSS/E could not take a licence ('%s') -- stopping it, "
                        "retrying after a pause" % licence[0])
                try: p.kill()
                except Exception: pass
                killed = True
                break
            idle = time.time() - _LAST_ACTIVITY.get(-2, time.time())
            if idle > PHASE_TIMEOUT_S:
                _banner("REPORT merge HUNG (no output for %ds > %ds) -- killing it so the "
                        "rest of the queue can continue" % (int(idle), PHASE_TIMEOUT_S))
                try: p.kill()
                except Exception: pass
                killed = True
                break
        th.join(timeout=5)
        rc = p.poll()
        print("[parallel] merge exited rc=%s%s" % (rc, "  (KILLED)" if killed else ""))
        if licence or rc == EXIT_LICENCE_BUSY:
            _lic += 1
            _tries -= 1
            if _lic > MAX_LICENCE_FAILS:
                print("[parallel] merge: licence refused %d times -- giving up on the merge; "
                      "the parts are on disk and MERGE_ONLY rebuilds the report later" % _lic)
                break
            _w = min(LICENCE_BACKOFF_S * (2 ** (_lic - 1)), LICENCE_BACKOFF_MAX_S)
            print("[parallel] merge: waiting %ds for the licence runtime" % _w)
            time.sleep(_w)
            continue
        break
    _phase_report(os.path.basename(RESULTS or ""))
    return rc == 0


_WANT_N = [0]                   # the workers this project was sized for


def _worker_slice(i, n, selected):
    """The ids worker i should run. None = the full fault set (the study decides);
       a list = what this worker is given.

       With DYNAMIC_WORK every worker is given the WHOLE selection and takes what
       is free, which is the point: a fixed slice is exactly what leaves three
       cores idle while the fourth finishes its share."""
    if not selected:
        return None
    if DYNAMIC_WORK:
        return list(selected)
    return selected[i::n]


def _run_workers(n, selected=None, _round=0, _attempts=None):
    """Launch N workers CONCURRENTLY; relaunch any that die before writing their sentinel.
       Worker i handles faults[i::N]; slices are disjoint so there is no collision. Every
       line a worker prints is echoed to THIS terminal prefixed with its tag ([W0]/[W1]/...)
       so you can see, live, which worker is running which scenario.

       _round / _attempts: the RETRY_GAVE_UP_ROUNDS pass calls this again for the
       scenarios that gave up, with fewer workers and a smaller attempt budget."""
    for i in range(n):
        _clear(_sentinel("_w%d" % i))
    procs, threads, launches, done = {}, {}, {i: 0 for i in range(n)}, set()
    launched_at = {}                  # i -> time of its current launch
    lic_fails   = {i: 0 for i in range(n)}
    start_fail  = {i: 0 for i in range(n)}   # deaths at start with NO licence evidence
    pending     = {}                  # i -> time at which to relaunch it (licence backoff)

    def start(i, first=False):
        launches[i] += 1
        _LAST_ACTIVITY[i] = time.time()
        launched_at[i] = time.time()
        pending.pop(i, None)
        # ONE PACER, NOT TWO. The stagger (worker i waits i x LAUNCH_STAGGER_S
        # before psseinit) predates the licence gate, which now spaces every
        # PSS/E start at 60 / LICENCE_STARTS_PER_MIN seconds. Both together
        # put worker 15 on the machine at 10 x 15 + 20 x 15 = 450 s: sixteen
        # sessions took seven and a half minutes to fill on every project.
        # With the gate on, the gate alone paces the starts; the stagger is
        # only used when the gate is switched off (LICENCE_STARTS_PER_MIN = 0).
        _delay = ((i * LAUNCH_STAGGER_S)
                  if (first and LAUNCH_STAGGER_S > 0 and LICENCE_STARTS_PER_MIN <= 0)
                  else 0.0)
        _banner("WORK: worker %d launch %d/%d  (tag [W%d])%s"
                % (i, launches[i], MAX_LAUNCHES_PER, i,
                   ("  -- starts PSS/E after %ds (LAUNCH_STAGGER_S)" % _delay) if _delay else ""))
        # -u = unbuffered child stdout so its lines stream to us live (not block-buffered
        # because it's a pipe); stderr merged into stdout so everything is tagged.
        _licence_gate("worker %d" % i)
        procs[i] = subprocess.Popen([PYTHON, "-u", STUDY_SCRIPT], cwd=STUDY_DIR,
                                    env=_env("work", i, n, _worker_slice(i, n, selected),
                                             start_delay=_delay, max_attempts=_attempts),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, bufsize=1)
        _register_child("w%d" % i, procs[i])
        th = threading.Thread(target=_pump, args=(i, "[W%d]" % i, procs[i])); th.daemon = True
        th.start(); threads[i] = th

    def kill(i):
        try: procs[i].kill()
        except Exception: pass

    def _holds(i):
        try:
            return _pid_holds_claim(procs[i].pid)
        except Exception:
            return True                                    # unknown = assume it is working

    def _schedule(i, why):
        """Relaunch i after the licence backoff instead of in PAUSE_BETWEEN seconds."""
        lic_fails[i] += 1
        if lic_fails[i] > MAX_LICENCE_FAILS:
            if LICENCE_RETIRE:
                _banner("worker %d: PSS/E failed to start %d times in a row (%s) -- giving up on this "
                        "slot. Restart the 'CodeMeter Runtime Server' service (services.msc, or "
                        "CodeMeter Control Center > Process > Restart CodeMeter Service) and launch "
                        "again; finished scenarios are kept and the rest resume." % (i, lic_fails[i], why))
                done.add(i)
                return
            # PARK IT, DO NOT BURY IT. The slot's share of the queue is still
            # there; the licence server usually is not, for a while. Clear the
            # count so the backoff restarts from LICENCE_BACKOFF_S when it wakes.
            _banner("worker %d: PSS/E failed to start %d times in a row (%s). The licence "
                    "runtime is not answering -- parking this slot for %s, then trying again. "
                    "Nothing is given up. If this repeats, restart the 'CodeMeter Runtime "
                    "Server' service (services.msc)." % (i, lic_fails[i], why,
                                                        _fmt_hms(LICENCE_COOLDOWN_S)))
            lic_fails[i] = 0
            pending[i] = time.time() + LICENCE_COOLDOWN_S
            return
        _wait = min(LICENCE_BACKOFF_S * (2 ** (lic_fails[i] - 1)), LICENCE_BACKOFF_MAX_S)
        pending[i] = time.time() + _wait
        _banner("worker %d: %s -- start failure %d/%d, relaunching in %s (no scenario attempt "
                "charged)" % (i, why, lic_fails[i], int(MAX_LICENCE_FAILS), _fmt_hms(_wait)))

    # CLAIMS LEFT BY AN EARLIER LAUNCH ARE CLEARED BEFORE ANY WORKER STARTS.
    # No worker of this launch exists yet, so every .claim older than this
    # launcher belongs to a run that has ended. Its PID is not a safe test:
    # Windows reuses PIDs, and a reused one read as "alive" kept F10 and F70
    # unclaimable while 20 of 22 workers ran out of work and stopped.
    # Given-up scenarios stay given up through their .attempts count.
    if not _round:
        _n_stale = 0
        for _cp in glob.glob(os.path.join(OUT_DIR, "*.claim")):
            try:
                if os.path.getmtime(_cp) < _LAUNCHER_T0 and _clear(_cp):
                    _n_stale += 1
            except Exception:
                pass
        if _n_stale:
            print("[parallel] cleared %d scenario claim(s) left by an earlier launch" % _n_stale)

    _banner("PHASE 2/3: launching %d worker(s) IN PARALLEL -- each line below is tagged [W#]%s"
            % (n, ("  [GAVE-UP RETRY ROUND %d]" % _round) if _round else ""))
    for i in range(n):
        start(i, first=True)

    t_start = time.time()
    _phase("simulate (%d worker(s))%s" % (n, (" retry round %d" % _round) if _round else ""))
    t_next_status = t_start + LIVE_STATUS_EVERY
    # TAKE OVER THE OTHER CASE'S SESSIONS WHEN IT FINISHES. z7_main.py
    # splits the machine between the base and the project launcher; the base
    # side finishes hours earlier and its sessions used to die with it, so
    # the project ran the tail of the sweep on half the cores. When a case
    # finishes, the panel writes the new session count for the other case to
    # SPP_SLOTS_FILE. Every SLOTS_CHECK_S this loop reads it; a target above
    # n adds workers n..target-1, each through the licence gate, and with
    # DYNAMIC_WORK they take whatever scenarios are still free. Only a file
    # written AFTER this launcher process started counts, so a stale one from
    # last week cannot double the session count on a machine that is full;
    # the panel also deletes both files when it starts a parallel sweep.
    _slots_file = (os.environ.get("SPP_SLOTS_FILE") or "").strip()
    _slots_next = [0.0]            # look at once: a handover written between two projects still counts

    def _maybe_grow():
        nonlocal n
        if _round or not DYNAMIC_WORK:
            return
        now = time.time()
        if now < _slots_next[0]:
            return
        _slots_next[0] = now + SLOTS_CHECK_S
        target = 0
        try:
            if (_slots_file and os.path.isfile(_slots_file)
                    and os.path.getmtime(_slots_file) >= _LAUNCHER_T0):
                with open(_slots_file) as fh:
                    target = int((fh.read() or "0").strip() or "0")
        except Exception:
            target = 0
        _why = "the other case has finished"
        # WORKERS HELD BACK FOR SCORING COME BACK when it is done.
        if _WANT_N[0] > max(target, n):
            target = _WANT_N[0]
            _why = "the scoring of another project has freed its cores"
        # AND NEVER GROW INTO CORES THE SCORING IS USING.
        _cap = _sim_allowed()
        if _cap is not None:
            _alive_now = len([k for k in range(n) if k not in done and k not in pending
                              and k in procs and procs[k].poll() is None])
            target = min(target, n + max(0, _cap - _alive_now))
        if target <= n:
            return
        _banner("HANDOVER: %s -- growing from %d to %d worker(s)" % (_why, n, target))
        # CAPPED, AND ONE AT A TIME.
        #
        # The file is written by the panel, but a stale or hand-edited value
        # would start that many PSS/E sessions on top of the running ones, so
        # it is bounded by the machine. And the starts go through the licence
        # gate, which BLOCKS: growing 4 -> 16 in this loop held the supervisor
        # for minutes, during which no worker was polled, no hang watchdog ran
        # and a worker that died was not relaunched. One per poll costs a few
        # seconds of ramp and keeps every watchdog live.
        target = min(int(target), MAX_GROW_WORKERS)
        if selected:
            target = min(target, max(n, len(selected)))
        if target <= n:
            return
        _old = n
        _next_i = n
        launches[_next_i] = 0
        lic_fails[_next_i] = 0
        start_fail[_next_i] = 0          # a grown worker's first early death must not KeyError
        _clear(_sentinel("_w%d" % _next_i))
        n = _next_i + 1
        start(_next_i, first=(LICENCE_STARTS_PER_MIN <= 0))
        _slots_next[0] = time.time() + max(2.0, min(SLOTS_CHECK_S, LAUNCH_STAGGER_S or 5.0))
        _phase("simulate (%d of %d worker(s)) after handover" % (n, target))

    while len(done) < n:
        time.sleep(POLL_SECS)
        now = time.time()
        _maybe_grow()
        if LIVE_STATUS_EVERY and now >= t_next_status:
            t_next_status = now + LIVE_STATUS_EVERY
            try: _live_status(launches, done, t_start, selected)
            except Exception as e: print("[parallel] live status failed: %s" % e)
        # --- relaunches that were put off by the licence backoff
        for i in list(pending):
            if i not in done and now >= pending[i]:
                start(i)
        # --- hang watchdog: KILL any alive worker that has printed nothing for HANG_TIMEOUT_S.
        #     A PSS/E crash can freeze the process (error dialog / Fortran pause) instead of
        #     exiting; without this the launcher would wait forever and never write the summary.
        for i in range(n):
            if i in done or i in pending or procs[i].poll() is not None:
                continue
            # A LICENCE BOX WAS CLOSED FOR THIS WORKER: its PSS/E never started.
            # Kill it (it holds no scenario) and relaunch after the backoff.
            # This does not consult NEVER_KILL_WORKERS -- there is nothing
            # running in that process to protect.
            # ...AND ONLY WHILE IT IS STILL STARTING. _DIALOG_HITS is never
            # pruned, so a box closed 3 s after launch -- which PSS/E then
            # retried past -- matched for the rest of the run and killed a
            # worker in the middle of a solve, with a scenario claimed.
            _lic = (None if _holds(i) or (now - launched_at.get(i, now)) >= STARTUP_DEAD_S
                    else _licence_hit("w%d" % i, launched_at.get(i, 0)))
            if _lic:
                kill(i)
                _schedule(i, "PSS/E licence box '%s'" % _lic[0])
                continue
            idle = now - _LAST_ACTIVITY.get(i, now)
            # SILENT AND HOLDING NOTHING = STUCK AT START. A worker with a
            # scenario may be quiet for an hour inside a solve, and the rules
            # below leave it alone; one with no claim has nothing to be quiet
            # about. This is exactly the "running workers: [3]  idle(s):
            # {3: 2053}  ...  0 running" state.
            if idle > STARTUP_SILENT_S and not _holds(i):
                _banner("worker %d has printed nothing for %s and holds NO scenario -- it is stuck "
                        "before its first scenario (a PSS/E start box, or a dead licence). Killing "
                        "it; nothing is lost." % (i, _fmt_hms(idle)))
                kill(i)
                _schedule(i, "silent %s with no scenario" % _fmt_hms(idle))
                continue
            if idle > HANG_TIMEOUT_S:
                if _quiet_watch("w%d" % i, "worker %d" % i, idle, HANG_TIMEOUT_S):
                    _banner("worker %d HUNG (no output for %ds > %ds) -- killing it"
                            % (i, int(idle), HANG_TIMEOUT_S))
                    try:
                        _charge_hang(_worker_scenario_age(i, launched_at.get(i, 0.0))[0])
                    except Exception:
                        pass
                    kill(i)
                    continue
            # --- A SCENARIO THAT HAS SIMPLY BEEN RUNNING TOO LONG. Not silence:
            #     elapsed. This is what would have ended F166 at 75 minutes
            #     instead of 18 hours. The kill goes through _quiet_watch so
            #     KILL_GRACE_S still applies to it.
            if SCENARIO_MAX_S > 0:
                _sid, _on = _worker_scenario_age(i, launched_at.get(i, 0.0))
                if _sid and _on > SCENARIO_MAX_S and _on > KILL_GRACE_S:
                    _banner("worker %d has been on %s for %s > SCENARIO_MAX_S=%s -- the slowest "
                            "scenario ever measured here took 24 min. Killing the worker; %s goes "
                            "back in the queue and this counts as one of its attempts."
                            % (i, _sid, _fmt_hms(_on), _fmt_hms(SCENARIO_MAX_S), _sid))
                    _charge_hang(_sid)
                    kill(i)
                    continue
            # --- A STRAGGLER: silent and running far past this folder's slowest.
            if not NEVER_KILL_WORKERS:
                _lim = _straggler_limit()
                if _lim and idle > _lim:
                    _sid, _on = _worker_scenario_age(i, launched_at.get(i, 0.0))
                    if _sid and _on > _lim:
                        _banner("worker %d: %s has run %s with no output -- past %s (%.0fx the "
                                "slowest finished scenario here, STRAGGLER_FACTOR). It is hung, "
                                "not slow: killing the worker; %s goes back in the queue."
                                % (i, _sid, _fmt_hms(_on), _fmt_hms(_lim), STRAGGLER_FACTOR, _sid))
                        _charge_hang(_sid)
                        kill(i)
                        continue
        alive = [i for i in range(n) if i not in done and i not in pending and procs[i].poll() is None]
        _write_alive(len(alive) + len([i for i in pending if i not in done]))
        with _PRINT_LOCK:
            idles = {i: int(now - _LAST_ACTIVITY.get(i, now)) for i in alive}
            _pend = {i: int(pending[i] - now) for i in pending if i not in done}
            print("[parallel] running workers: %s   idle(s): %s   (done: %s)%s"
                  % (sorted(alive), idles, sorted(done),
                     ("   relaunch in (s): %s" % _pend) if _pend else ""))
            _pl = _phase_line()
            if _pl:
                print("[parallel] " + _pl)
        for i in range(n):
            if i in done or i in pending:
                continue
            p = procs.get(i)
            if p is None or p.poll() is None:
                continue                                   # still running
            if threads.get(i):
                threads[i].join(timeout=2)                 # drain the last of its output
            rc = p.poll()
            if os.path.isfile(_sentinel("_w%d" % i)):
                _banner("worker %d COMPLETE (sentinel present)" % i); done.add(i)
            elif (rc == EXIT_LICENCE_BUSY
                  or ((now - launched_at.get(i, now)) < STARTUP_DEAD_S
                      and _licence_hit("w%d" % i, launched_at.get(i, 0)))):
                _worker_exit_note(i, _exit_reason(EXIT_LICENCE_BUSY))
                _schedule(i, _exit_reason(rc))
            elif (now - launched_at.get(i, now)) < STARTUP_DEAD_S and not _holds(i) and rc != 0:
                # DIED AT START, BEFORE TAKING ANYTHING. Whatever the code says,
                # this is a start failure -- it goes through the backoff so a
                # busy licence runtime is not asked again 3 s later.
                _why = _exit_reason(rc)
                _worker_exit_note(i, _why + " (died %ds after launch, before any scenario)"
                                  % int(now - launched_at.get(i, now)))
                # A START FAILURE THAT IS NOT THE LICENCE IS USUALLY THE SAME
                # EVERY TIME (a case that cannot be built, a missing machine):
                # parking and relaunching it for ever kept the launcher, and
                # z7_main behind it, from ever finishing. The licence branch
                # above still waits for as long as it takes.
                start_fail[i] += 1
                if start_fail[i] > START_FAIL_MAX:
                    _banner("worker %d died at start %d times in a row (%s) with no licence "
                            "box -- giving up on this slot; its faults will show as not run. "
                            "See this worker's log for the error." % (i, start_fail[i], _why))
                    done.add(i)
                else:
                    _schedule(i, "died at start: %s" % _why)
            elif launches[i] >= MAX_LAUNCHES_PER:
                _banner("worker %d GAVE UP after %d launch(es) -- moving on (its faults will show "
                        "as CRASHED/INCOMPLETE in RUN_SUMMARY)" % (i, launches[i])); done.add(i)
            else:
                # SAY HOW IT DIED. "crash/hang" names the symptom and hides the
                # one fact that identifies the cause: Windows returns the
                # exception code as the exit code, and they are not
                # interchangeable. An access violation is a bad pointer in a
                # model; out-of-memory is 32-bit PSS/E hitting its 2 GB ceiling;
                # a normal small code is the script itself exiting. Without this
                # every death looked the same and the fix was guesswork.
                lic_fails[i] = 0                           # it got as far as real work
                start_fail[i] = 0
                _why = _exit_reason(rc)
                _worker_exit_note(i, _why)
                _banner("worker %d exited without sentinel -- %s -- relaunching"
                        % (i, _why))
                time.sleep(PAUSE_BETWEEN); start(i)
    if LIVE_STATUS_EVERY:
        try: _live_status(launches, done, t_start, selected)   # final table for this phase
        except Exception as e: print("[parallel] live status failed: %s" % e)
    # ---- SECOND CHANCE FOR WHAT GAVE UP -------------------------------------
    # Every scenario whose attempts ran out while workers were dying under it
    # gets one more round: budget reset, fewer workers (so the machine is quiet
    # and the licence runtime is not busy), RETRY_ATTEMPTS attempts each.
    if _round < RETRY_GAVE_UP_ROUNDS:
        try:
            _gu = _gave_up_ids(selected if selected and selected != ["__ALL_ALREADY_DONE__"] else None)
        except Exception as e:
            print("[parallel] gave-up scan failed: %s" % e); _gu = []
        if _gu:
            _banner("GAVE-UP RETRY ROUND %d: %d scenario(s) gave up (%s%s) -- resetting their "
                    "attempts and running them again with %d worker(s), %d attempt(s) each"
                    % (_round + 1, len(_gu), ", ".join(_gu[:10]), " ..." if len(_gu) > 10 else "",
                       max(1, min(n, int(RETRY_WORKERS))), int(RETRY_ATTEMPTS)))
            _reset_attempts(_gu, "retry round %d" % (_round + 1))
            _run_workers(max(1, min(n, int(RETRY_WORKERS))), _gu, _round + 1, int(RETRY_ATTEMPTS))
            _phase("simulate (%d worker(s))" % n)
    # ---- CATCH-UP PLOT/SCORE PASS ------------------------------------------
    # The workers never open their own .out files any more -- loading a
    # ~7,800-channel file inside 32-bit PSS/E is what was killing them right
    # after "-> F##.out (OK)". A separate plotter process draws and scores as
    # the run goes; this synchronous pass sweeps up whatever was still pending
    # when the last worker finished, so the report phase starts complete.
    if _round:
        # THE OUTER ROUND RUNS THIS PASS THE MOMENT WE RETURN. A retry round is
        # called from inside _run_workers(), so the pass ran here and then again
        # one line later in the caller -- "plot/score catch-up 1m 46s | simulate
        # 0s | plot/score catch-up 1m 46s" in the phase line, the same files
        # read twice. Once is enough, and the outer round is the one that does it.
        _phase(None)
        return all(os.path.isfile(_sentinel("_w%d" % i)) for i in range(n))
    _phase("plot/score catch-up")
    try:
        _banner("PLOT/SCORE catch-up: finishing any .out still missing its .done/PDF/score "
                "(silent %ds or %ds total = killed, the queue goes on)"
                % (PLOT_CATCHUP_STALL_S, PLOT_CATCHUP_MAX_S))
        _pe = _env("work", 9, 1)
        _pe["SPP_PLOT_MISSING"] = "1"
        # PER-FILE .pclaim CLAIMS ON. The during-run plotter may still be
        # drawing when this pass starts; without the claim both drew the same
        # scenario. With it, a file the other plotter holds is skipped.
        _pe["SPP_PLOT_CLAIMS"] = "1"
        _pe.pop("SPP_ONLY", None)
        _pp = subprocess.Popen([PYTHON, "-u", STUDY_SCRIPT], cwd=STUDY_DIR, env=_pe,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, bufsize=1)
        _register_child("plot-catchup", _pp)
        _LAST_ACTIVITY["plot"] = time.time()
        _pt = threading.Thread(target=_pump, args=("plot", "[PLOT]", _pp, "plot-catchup"))
        _pt.daemon = True; _pt.start()
        _t0c = time.time()
        _killed = ""
        while _pp.poll() is None:
            time.sleep(5)
            _now = time.time()
            _quiet = _now - _LAST_ACTIVITY.get("plot", _t0c)
            if PLOT_CATCHUP_STALL_S and _quiet > PLOT_CATCHUP_STALL_S:
                _killed = "printed nothing for %s (PLOT_CATCHUP_STALL_S=%d)" % (_fmt_hms(_quiet), PLOT_CATCHUP_STALL_S)
            elif PLOT_CATCHUP_MAX_S and (_now - _t0c) > PLOT_CATCHUP_MAX_S:
                _killed = "ran %s in total (PLOT_CATCHUP_MAX_S=%d)" % (_fmt_hms(_now - _t0c), PLOT_CATCHUP_MAX_S)
            if _killed:
                _banner("CATCH-UP PLOTTER KILLED -- it %s. Whatever it left undrawn is "
                        "picked up by the panel's plot pass; the report phase does not "
                        "need it. The queue continues." % _killed)
                try:
                    _pp.kill()
                except Exception:
                    pass
                break
        _pt.join(timeout=5)
        if not _killed:
            print("[parallel] catch-up plot pass done (rc=%s) in %s"
                  % (_pp.poll(), _fmt_hms(time.time() - _t0c)))
    except Exception as e:
        print("[parallel] catch-up plot pass failed: %s" % e)
    _phase(None)
    return all(os.path.isfile(_sentinel("_w%d" % i)) for i in range(n))


def _plot_pdf(sid):
    """Path of this scenario's plot PDF if it was written, else ''."""
    p = os.path.join(RESULTS, "plots", "%s_plots.pdf" % sid)
    return p if os.path.isfile(p) else ""


_NOT_PLOTTED = {}
_NOT_PLOTTED_READ = [False]


def _no_pdf_reason(sid):
    """Why this scenario has no PDF, from plots\\NOT_PLOTTED.txt, or "".

       A "pdf: no" column with nothing beside it is a dead end: it does not say
       whether the file was refused, whether the pass never reached it, or
       whether plotting was off for this run -- and those have three different
       fixes. The plot pass records its reason per scenario; this puts it on the
       same row as the "no"."""
    if not _NOT_PLOTTED_READ[0]:
        _NOT_PLOTTED_READ[0] = True
        try:
            with open(os.path.join(RESULTS, "plots", "NOT_PLOTTED.txt"),
                      errors="replace") as fh:
                for ln in fh:
                    b = ln.rstrip("\n").split("\t")
                    if len(b) >= 2 and b[0].strip():
                        _NOT_PLOTTED[b[0].strip()] = b[1].strip()
        except Exception:
            pass
    return _NOT_PLOTTED.get(str(sid).strip(), "")


def _study_summary_exists():
    """True if the STUDY's own report phase wrote its (richer) RUN_SUMMARY.txt."""
    p = _rfile_tagged(RESULTS, "RUN_SUMMARY", "txt")
    try:
        return os.path.isfile(p) and os.path.getsize(p) > 0
    except Exception:
        return False


def _scenario_state(sid, prog):
    """(state, attempts, worker, last_update, note) for one scenario, read from disk.

       Exactly the classification the live status table uses, so the summary and the
       last LIVE STATUS you watched cannot disagree."""
    out_ok = os.path.isfile(os.path.join(OUT_DIR, "%s.out" % sid))
    att = _read_attempts(sid)
    st, _a, wk, tm, note = prog.get(sid, ("", "", "", "", ""))
    if os.path.isfile(os.path.join(OUT_DIR, "%s.done" % sid)) and out_ok:
        state = "DONE"
    elif st in ("GAVE-UP", "ERROR", "FAILED") and _row_is_stale(sid, tm):
        state = "INCOMPLETE"           # its budget was reset; the GAVE-UP row is stale
    elif st in ("GAVE-UP", "ERROR", "FAILED"):
        state = st
    elif st == "RUNNING":
        state = "INTERRUPTED"          # the run ended while this was still marked running
    elif att > 0:
        state = "INCOMPLETE"
    else:
        state = "NOT RUN"
    # HOW THAT WORKER DIED, on the row of the scenario it was holding.
    #
    # "attempted 4 time(s) >= MAX_SCENARIO_ATTEMPTS" is the counter, not the
    # cause. The cause was printed on the console four times and is nowhere a
    # reader of this table can reach it.
    if state in ("GAVE-UP", "ERROR", "FAILED", "INCOMPLETE", "INTERRUPTED"):
        _w = _worker_exit_note(re.sub(r"\D", "", str(wk)) or wk)
        if _w:
            note = ("%s; worker died: %s" % (note, _w)) if note \
                else ("worker died: %s" % _w)
    return state, att, wk, tm, note


def _write_run_summary(t0, launches=None, report_ok=None, why=""):
    """Write RUN_SUMMARY.txt + RUN_SUMMARY.csv from what is ON DISK. Never raises.

       This exists because the REPORT phase can die mid-way -- a Fortran-level
       "Invalid Floating-Point number." abort while reading a NaN .out kills the
       process outright AND still returns rc=0 -- leaving a three-hour run with no
       summary at all. Everything below comes from PROGRESS*.csv and the
       .done/.attempts/.out/_plots.pdf files, so it is correct whatever the report did.

       If the study's own report DID write RUN_SUMMARY.txt, that richer version (it
       carries the SPP verdicts) is left alone and this one goes to
       RUN_SUMMARY_LAUNCHER.txt instead."""
    try:
        ids = _fault_ids()
        if not ids:
            # no fault file: fall back to whatever scenarios left markers behind
            ids = sorted(set(os.path.basename(p).rsplit(".", 1)[0]
                             for p in glob.glob(os.path.join(OUT_DIR, "*.attempts")) +
                                      glob.glob(os.path.join(OUT_DIR, "*.done"))))
        if not ids:
            print("[parallel] no scenarios found on disk -- no summary to write")
            return
        prog = _progress_rows(since=_RUN_STARTED)
        rows = []
        for sid in ids:
            state, att, wk, tm, note = _scenario_state(sid, prog)
            # WHY THERE IS NO PDF, on the row that says there is none.
            if not _plot_pdf(sid):
                _why = _no_pdf_reason(sid)
                if _why:
                    note = ("%s; no PDF: %s" % (note, _why)) if note \
                        else ("no PDF: %s" % _why)
            rows.append((sid, state, att, wk, tm, note,
                         "yes" if os.path.isfile(os.path.join(OUT_DIR, "%s.out" % sid)) else "no",
                         "yes" if _plot_pdf(sid) else "no"))

        n_done = sum(1 for r in rows if r[1] == "DONE")
        unfinished = [r[0] for r in rows if r[1] != "DONE"]
        el = time.time() - t0

        L = []
        L.append("=" * 96)
        L.append(" RUN SUMMARY  --  written by the launcher at %s"
                 % time.strftime("%Y-%m-%d %H:%M:%S"))
        if why:
            L.append(" (%s)" % why)
        L.append("=" * 96)
        L.append(" results   : %s" % RESULTS)
        L.append(" study     : %s" % STUDY_SCRIPT)
        L.append(" elapsed   : %s   workers: %d" % (_fmt_hms(el), N_WORKERS))
        if launches:
            L.append(" relaunches: %s"
                     % ", ".join("W%d=%d/%d" % (i, launches.get(i, 0), MAX_LAUNCHES_PER)
                                 for i in sorted(launches)))
        if report_ok is not None:
            L.append(" merged SPP criteria report: %s"
                     % ("written -> SPP_CRITERIA_REPORT.txt" if report_ok else
                        "*** NOT PRODUCED -- the report phase died before writing it ***"))
        L.append("-" * 96)
        L.append(" %d scenario(s): %d DONE, %d did not finish" % (len(rows), n_done, len(unfinished)))
        by_state = {}
        for r in rows:
            by_state[r[1]] = by_state.get(r[1], 0) + 1
        L.append("   " + "   ".join("%s=%d" % (k, by_state[k]) for k in sorted(by_state)))
        L.append("-" * 96)
        L.append(" %-11s %-12s %-4s %-7s %-20s %-4s %-4s %s"
                 % ("scenario", "state", "att", "worker", "last update", ".out", "pdf", "note"))
        for sid, state, att, wk, tm, note, has_out, has_pdf in rows:
            L.append(" %-11s %-12s %-4d %-7s %-20s %-4s %-4s %s"
                     % (sid, state, att, wk or "-", tm or "-", has_out, has_pdf, note[:36]))
        L.append("=" * 96)
        if unfinished:
            L.append(" TO RE-RUN WHAT DID NOT FINISH, put this in the launcher:")
            L.append("     RUN_ONLY_FAULTS = [%s]"
                     % ", ".join('"%s"' % s for s in unfinished))
            L.append(" (or RUN_ONLY_FAULTS = [\"NOTDONE\"], which resolves to the same set)")
        else:
            L.append(" Every scenario finished.")
        L.append("=" * 96)
        text = "\n".join(L)

        # --- the .txt: ONE run summary, not two ---
        #
        # This used to write RUN_SUMMARY_LAUNCHER_<proj>.txt whenever the study
        # had written its own -- two files describing the same scenarios, in the
        # same folder, differing only in that one has the SPP verdicts. Which is
        # the summary was then a question, and the answer was "read both".
        #
        # The launcher's view is worth keeping (it comes from the markers on
        # disk, so it is right even when the report phase died part way), so it
        # is APPENDED to the study's summary under its own heading instead of
        # being filed separately. When the study wrote nothing, this becomes the
        # summary, under the study's own name.
        txt_path = _rfile_tagged(RESULTS, "RUN_SUMMARY", "txt")
        _MARK = " LAUNCHER'S VIEW -- from the markers on disk, independent of the report phase"
        if _study_summary_exists():
            # REPLACE the previous launcher block, never stack another one.
            # _write_run_summary() is called after the work phase, after the
            # report phase, and again on Ctrl-C or an exception, so a plain
            # append would leave four launcher views in one file, three of them
            # out of date and none of them marked as such.
            try:
                with open(txt_path, errors="replace") as fh:
                    prev = fh.read()
            except Exception:
                prev = ""
            cut = prev.find(_MARK)
            if cut > 0:
                cut = prev.rfind("\n" + "=" * 96, 0, cut)
                prev = prev[:cut] if cut > 0 else prev
            with open(txt_path, "w") as fh:
                fh.write(prev.rstrip("\n") + "\n")
                fh.write("\n" + "=" * 96 + "\n")
                fh.write(_MARK + "\n")
                fh.write("=" * 96 + "\n")
                fh.write(text + "\n")
        else:
            with open(txt_path, "w") as fh:
                fh.write(text + "\n")

        # THE COMBINED DOCUMENT TOO. The study concatenates every report into
        # 00_ALL_RESULTS_<kind>_<project>.txt when its report phase ends -- which
        # is BEFORE this runs, so without this the one file that is supposed to
        # hold everything would be the one place the launcher's view is missing.
        # Same replace-don't-stack rule as above.
        try:
            import glob as _g2
            for _ar in _g2.glob(os.path.join(RESULTS, "00_ALL_RESULTS_*.txt")):
                try:
                    with open(_ar, errors="replace") as fh:
                        prev = fh.read()
                except Exception:
                    continue
                cut = prev.find(_MARK)
                if cut > 0:
                    cut = prev.rfind("\n" + "=" * 96, 0, cut)
                    prev = prev[:cut] if cut > 0 else prev
                with open(_ar, "w") as fh:
                    fh.write(prev.rstrip("\n") + "\n")
                    fh.write("\n" + "=" * 96 + "\n")
                    fh.write(_MARK + "\n")
                    fh.write("=" * 96 + "\n")
                    fh.write(text + "\n")
        except Exception:
            pass

        # --- the .csv: this is what the CRASHED / NOTDONE keywords read next run ---
        csv_path = _run_summary_csv()
        write_csv = True
        if os.path.isfile(csv_path):
            try:                                  # keep the study's own, it has verdicts
                write_csv = os.path.getsize(csv_path) == 0
            except Exception:
                write_csv = True
        if write_csv:
            try:
                os.makedirs(os.path.dirname(csv_path))
            except Exception:
                pass
            with open(csv_path, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["scenario", "run_status", "attempts", "worker", "last_update",
                            "out_file", "plot_pdf", "spp_verdict", "solver_fix", "note"])
                for sid, state, att, wk, tm, note, has_out, has_pdf in rows:
                    w.writerow([sid, state, att, wk, tm, has_out, has_pdf, "", "", note])
            print("[parallel] summary -> %s" % csv_path)
        print("[parallel] summary -> %s" % txt_path)
    except Exception as e:
        print("[parallel] could not write RUN_SUMMARY: %s" % e)


def _echo_run_summary():
    """Re-print the study's own RUN_SUMMARY at the very end of the parallel run.

       The REPORT phase writes it, but by then thousands of tagged worker lines have
       scrolled past, so the one thing you actually want to see -- how many scenarios
       crashed -- is buried. This puts it back on screen as the last thing printed."""
    path = _rfile_tagged(RESULTS, "RUN_SUMMARY", "txt")
    if not os.path.isfile(path):
        # A folder written before the launcher stopped filing its own copy.
        alt = os.path.join(RESULTS, "RUN_SUMMARY_LAUNCHER%s%s.txt"
                           % (_report_tag(),
                              ("_%s" % _CUR_PROJECT) if _CUR_PROJECT else ""))
        if os.path.isfile(alt):
            path = alt
        else:
            print("[parallel] no run summary yet at %s" % path)
            return
    try:
        with open(path, errors="replace") as fh:
            text = fh.read()
    except Exception as e:
        print("[parallel] could not read RUN_SUMMARY.txt: %s" % e)
        return
    _banner("RUN SUMMARY  (%s)" % path)
    print(text.rstrip())


def _project_results_dir(proj, mode):
    """<proj>_<mode> WITH the SPP_CAP_TAG / SPP_RUN_TAG suffix, as the study
       names it (see _study_results_subdir) -- a sweep's reports were read from
       the untagged folder of another run."""
    sub = ("%s_%s" % (proj, mode)) if proj else "dynamics"
    if proj:
        cap = (os.environ.get("SPP_CAP_TAG") or "").strip()
        run = (os.environ.get("SPP_RUN_TAG") or "").strip()
        if cap:
            sub += "_cap%s" % cap
        if run:
            sub += "_%s" % run
    return _run_path(_results_root(), proj, sub)


def _read_project_verdicts(rdir, proj=None):
    """[(case, verdict)] from one project's SPP_CRITERIA_REPORT.txt.

       Parsed out of the text rather than the CSV because the per-case verdict
       only exists in the text ("CASE: <id>   RESULT: PASS"); the CSV carries the
       individual criterion rows. Reading the file the study already writes means
       the roll-up cannot disagree with the per-project report."""
    out = []
    p = _rfile(rdir, "SPP_CRITERIA_REPORT", "txt", proj)
    try:
        with open(p, errors="replace") as fh:
            for line in fh:
                m = re.match(r"\s*CASE:\s+(\S+)\s+RESULT:\s+(\S+)", line)
                if m:
                    out.append((m.group(1), m.group(2).upper()))
    except Exception:
        pass
    return out


def _read_project_criteria(rdir, proj=None):
    """{case: [(criterion, result, detail)]} from one project's
       SPP_CRITERIA_REPORT.csv -- the per-criterion rows the study writes.

       This is what turns "C27 FAILED" into "C27 failed voltage recovery, POI
       539800 held 0.42 pu". The verdict alone says a fault is a problem; only
       the criterion and its detail say WHY, and that is the part you act on."""
    out = {}
    csvp = _rfile(rdir, "SPP_CRITERIA_REPORT", "csv", proj)
    try:
        with open(csvp, newline="") as fh:
            for r in csv.DictReader(fh):
                case = (r.get("Case") or "").strip()
                if case:
                    out.setdefault(case, []).append(
                        ((r.get("Criterion") or "").strip(),
                         (r.get("Result") or "").strip().upper(),
                         (r.get("Detail") or "").strip()))
    except Exception:
        pass
    if out:
        return out
    # ---- THE .txt SAYS THE SAME THING, AND IT IS ALWAYS THERE --------------
    #
    # THIS IS WHY EVERY SCENARIO READ "per-criterion rows not readable".
    # The rows were only ever looked for in SPP_CRITERIA_REPORT.csv, and that
    # file is written only when the study's WRITE_CSV is on -- it is off, so
    # the file does not exist, the read failed into a bare `except: pass`, and
    # the roll-up printed the fallback line for all five scenarios of every
    # project. Nothing said a file was missing; the roll-up simply had no
    # criteria in it, which is most of what it exists to carry.
    #
    # The .txt is written unconditionally and holds the same rows:
    #
    #     CASE: C01_3PH_765911_6cy               RESULT: FAIL
    #       [FAIL] Voltage recovery                     : POI 765911 held 0.42 pu
    #
    # so it is parsed when the csv is not there. Same three fields, same
    # meaning; only the shape differs.
    txtp = _rfile(rdir, "SPP_CRITERIA_REPORT", "txt", proj)
    case = ""
    try:
        with open(txtp, errors="replace") as fh:
            for ln in fh:
                s = ln.rstrip("\n")
                st = s.strip()
                if st.startswith("CASE:"):
                    # "CASE: <name>   RESULT: <verdict>"
                    body = st[len("CASE:"):]
                    case = body.split("RESULT:")[0].strip()
                    continue
                if not case or not st.startswith("["):
                    continue
                # "[FAIL] <criterion> : <detail>"
                try:
                    res, rest = st[1:].split("]", 1)
                except ValueError:
                    continue
                crit, _sep, detail = rest.partition(":")
                out.setdefault(case, []).append(
                    (crit.strip(), res.strip().upper(), detail.strip()))
    except Exception:
        pass
    return out


def _read_swing_ref(rdir, proj=None):
    """The rotor-angle reference line out of a project's criteria report, so the
       all-projects roll-up states which machine each project's angles were
       measured against. Different projects legitimately get different
       references -- the rule picks the swing machine nearest each POI -- so a
       combined report that does not say which is missing something a reviewer
       needs."""
    try:
        with open(_rfile(rdir, "SPP_CRITERIA_REPORT", "txt", proj), errors="replace") as fh:
            for line in fh:
                if line.strip().startswith("Rotor-angle reference:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def _read_project_states(rdir, proj=None):
    """{scenario: run_state} for one project, from its RUN_SUMMARY.csv."""
    st = {}
    for name in (_rfile(rdir, "RUN_SUMMARY", "csv", proj),):
        try:
            with open(name, newline="") as fh:
                for r in csv.DictReader(fh):
                    sid = (r.get("scenario") or "").strip()
                    if sid:
                        st[sid] = (r.get("run_status") or "").strip().upper()
        except Exception:
            pass
    return st


def _write_all_projects_report(passes, done_so_far):
    """The one file that answers 'how did every project do' -- written to
           results\\ALL_PROJECTS_SPP_SUMMARY.txt / .csv

       Rewritten after EVERY project finishes, not only at the very end, so the
       roll-up for the projects that are done exists even if a later one hangs or
       the run is stopped. That is the same reasoning that puts RUN_SUMMARY before
       the report phase: a long run must not be able to end with nothing to read.

       CRASHED AND GAVE-UP SCENARIOS ARE NOT SCORED. The study's report phase
       excludes any scenario without a .done marker, and any whose PROJECT/POI
       channels are NaN, before the criteria are applied -- so a scenario that
       never converged cannot appear as a PASS or a FAIL. They are counted here as
       'not scored' and listed by id, because a criteria report that quietly drops
       9 of 52 scenarios without saying so is worse than one that fails."""
    lines, rows = [], []
    lines.append("=" * 100)
    lines.append(" SPP CRITERIA -- ALL PROJECTS")
    lines.append(" written %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("=" * 100)
    lines.append(" Criteria are applied to every scenario that COMPLETED, whatever fault set")
    lines.append(" produced it -- spp, custom or manual. Scenarios that crashed, gave up or")
    lines.append(" returned NaN are NOT scored (their values are not valid for scoring); they are")
    lines.append(" listed per project below so that none is omitted.")
    lines.append("=" * 100)
    lines.append(" %-18s %-8s %-6s %-6s %-6s %-10s %s"
                 % ("project", "mode", "PASS", "FAIL", "scen", "crash+wait", "report"))
    lines.append("-" * 100)

    # EVERY PROJECT ON DISK, not just this launcher's.
    #
    # This used to iterate `passes` alone -- the projects THIS run was told to do.
    # One file, results\ALL_PROJECTS_SPP_SUMMARY.txt, rewritten by whichever
    # launcher finished last: a report-only run on EastFork replaced the table
    # with a single EastFork row, and the SantaFe launcher then replaced that
    # with a single SantaFe row. Neither was wrong about its own project and both
    # destroyed the other's. Running two launchers side by side is normal here, so
    # the roll-up has to be built from what is ON DISK rather than from what one
    # process happens to know about.
    #
    # Each project's numbers come from its OWN results folder, so a project this
    # run never touched carries its previous results through unchanged.
    _seen_pm = set(passes)
    _extra = []
    _extra_dir = {}
    # this run's own folders (with a run tag they are <proj>_<mode>_<tag>, which
    # the name split below would read as another project)
    _own = set(os.path.normcase(os.path.normpath(_project_results_dir(p, m))) for p, m in passes)
    try:
        for _d in _run_glob(_results_root(), "*_*"):
            if not os.path.isdir(_d):
                continue
            if os.path.normcase(os.path.normpath(_d)) in _own:
                continue
            _nm = os.path.basename(_d)
            if "_gt_" in _nm or "_before_fixed_" in _nm:
                continue          # gen-test runs / results moved aside by FIXED_SOLVER
            _pj, _sep, _md = _nm.rpartition("_")
            if not _sep or not _pj:
                continue
            if (_pj, _md) in _seen_pm:
                continue
            # Only if it actually holds results -- an empty or half-made folder
            # in the table is noise.
            if not (os.path.isfile(_rfile(_d, "SPP_CRITERIA_REPORT", "txt", _pj))
                    or os.path.isfile(_rfile(_d, "RUN_SUMMARY", "csv", _pj))):
                continue
            _seen_pm.add((_pj, _md))
            _extra.append((_pj, _md))
            _extra_dir[(_pj, _md)] = _d        # read from THIS folder, never re-derived
    except Exception as e:
        print("[parallel] could not scan results\\ for other projects: %s" % e)
    if _extra:
        print("[parallel] roll-up also covers %d project(s) this run did not touch: %s"
              % (len(_extra), ", ".join("%s/%s" % (p, m) for p, m in _extra)))

    detail = []
    for proj, mode in list(passes) + _extra:
        rdir = _extra_dir.get((proj, mode)) or _project_results_dir(proj, mode)
        key = "%s/%s" % (proj or "default", mode)
        # A project from the disk scan was not part of this run, so it has no
        # "done" key -- read it anyway; its folder is the record.
        if (proj, mode) in _extra:
            key = None
        if key is not None and key not in done_so_far:
            lines.append(" %-18s %-8s %s"
                         % (proj or "(default)", mode, "-- not run yet --"))
            continue
        verdicts = _read_project_verdicts(rdir, proj)
        states   = _read_project_states(rdir, proj)
        crit     = _read_project_criteria(rdir, proj)
        npass = sum(1 for _c, v in verdicts if v == "PASS")
        nfail = sum(1 for _c, v in verdicts if v == "FAIL")
        scored = set(c for c, _v in verdicts)
        # UNSCORED SPLITS TWO WAYS, and conflating them misreports the run.
        #   * a scenario that CRASHED has no usable .out, so it can never be
        #     scored -- that is a result;
        #   * a scenario that is DONE but absent from the criteria report simply
        #     has not been scored YET, because the report is still running or
        #     died. Calling those "crashed, gave up or NaN" -- as this did -- says
        #     140 finished runs failed when every one of them completed.
        not_scored = sorted(s for s, st in states.items() if s not in scored)
        crashed_ns = [s for s in not_scored
                      if (states.get(s, "") or "").upper() not in ("DONE", "")]
        pending_ns = [s for s in not_scored if s not in crashed_ns]
        have_report = os.path.isfile(_rfile(rdir, "SPP_CRITERIA_REPORT", "txt", proj))
        # A report still being produced in the background is not a missing one.
        running = any(r["proj"] == proj and r["mode"] == mode for r in _REPORT_BG)
        lines.append(" %-18s %-8s %-6d %-6d %-6d %-10s %s"
                     % (proj or "(default)", mode, npass, nfail, len(states),
                        ("%d+%d" % (len(crashed_ns), len(pending_ns)))
                        if pending_ns else str(len(crashed_ns)),
                        "yes" if have_report else
                        ("still running" if running else "*** MISSING ***")))
        detail.append((proj, mode, rdir, verdicts, states, not_scored, crit,
                       _read_swing_ref(rdir, proj), crashed_ns, pending_ns, running))
        # One CSV row PER CRITERION for a failed case, so the spreadsheet says
        # which check failed and what the numbers were -- not just that the case
        # did. A passing case stays one row; there is nothing to itemise.
        for case, v in verdicts:
            bad = [(c, d) for c, r, d in crit.get(case, []) if r == "FAIL"]
            if v == "FAIL" and bad:
                for c, d in bad:
                    rows.append([proj or "(default)", mode, case, v,
                                 states.get(case, ""), c, d, rdir])
            else:
                rows.append([proj or "(default)", mode, case, v,
                             states.get(case, ""), "", "", rdir])
        for sid in not_scored:
            rows.append([proj or "(default)", mode, sid, "NOT SCORED",
                         states.get(sid, ""), "",
                         "excluded before scoring -- crashed, gave up or NaN", rdir])

    lines.append("=" * 100)
    for (proj, mode, rdir, verdicts, states, not_scored, crit, swing,
         crashed_ns, pending_ns, running) in detail:
        lines.append("")
        lines.append("-" * 100)
        lines.append(" %s  (%s faults)   %s" % (proj or "(default)", mode, rdir))
        if swing:
            lines.append("   rotor-angle reference: %s" % swing)
        lines.append("-" * 100)
        fails = [c for c, v in verdicts if v == "FAIL"]
        if fails:
            lines.append("   FAIL (%d) -- which criterion, and the measured values:"
                         % len(fails))
            for c in fails:
                lines.append("")
                lines.append("      %s" % c)
                bad = [(k, d) for k, r, d in crit.get(c, []) if r == "FAIL"]
                if not bad:
                    # SAY WHICH FILE, AND WHETHER IT IS THERE. "not readable"
                    # sent the reader to a report that was fine; the rows were
                    # being looked for in a .csv that WRITE_CSV never wrote.
                    _cr = _rfile(rdir, "SPP_CRITERIA_REPORT", "txt", proj)
                    if not crit:
                        lines.append("         (no per-criterion rows could be read "
                                     "from %s -- %s)"
                                     % (os.path.basename(_cr),
                                        "the file is not there"
                                        if not os.path.isfile(_cr)
                                        else "it holds no [RESULT] lines"))
                    else:
                        lines.append("         (this case has no FAIL row of its own "
                                     "-- its verdict came from elsewhere in %s)"
                                     % os.path.basename(_cr))
                for k, d in bad:
                    lines.append("         FAIL  %s" % k)
                    # The detail names the offending buses/machines and their
                    # values, which is the whole point of quoting it here.
                    lines.append("               %s" % (d if len(d) <= 88 else d[:88] + " ..."))
            # A quick count of WHICH criterion is doing the failing across the
            # project -- one bus failing 30 faults and 30 buses failing once each
            # are very different problems, and the tally separates them.
            tally = {}
            for c in fails:
                for k, r, _d in crit.get(c, []):
                    if r == "FAIL":
                        tally[k] = tally.get(k, 0) + 1
            if tally:
                lines.append("")
                lines.append("   failing criterion, by count:")
                for k in sorted(tally, key=lambda x: -tally[x]):
                    lines.append("      %-4d %s" % (tally[k], k))
        else:
            lines.append("   FAIL: none")
        if pending_ns:
            # These RAN. They are only unscored because the criteria report has
            # not covered them yet.
            lines.append("   AWAITING SCORING (%d) -- these scenarios COMPLETED; the criteria"
                         % len(pending_ns))
            lines.append("   report %s, so no verdict exists for them yet."
                         % ("is still running" if running else
                            "is missing or did not finish"))
            lines.append("      %s%s"
                         % (", ".join(pending_ns[:14]),
                            " ... and %d more" % (len(pending_ns) - 14)
                            if len(pending_ns) > 14 else ""))
            if not running:
                lines.append("   Re-run the report for this project alone -- no simulations"
                             " needed:")
                lines.append("      set SPP_ROLE=report & set SPP_PROJECT=%s & set "
                             "SPP_FAULT_MODE=%s" % (proj or "", mode))
                lines.append("      then run %s" % os.path.basename(STUDY_SCRIPT))
        if crashed_ns:
            lines.append("   NOT SCORED (%d) -- crashed, gave up or NaN, so not judged:"
                         % len(crashed_ns))
            for sid in crashed_ns:
                lines.append("      %-26s %s" % (sid, states.get(sid, "")))
            lines.append("   To finish these and have them scored, run the launcher again with")
            lines.append("      RUN_PROJECTS = [\"%s\"]" % (proj or ""))
            lines.append("      RUN_ONLY_FAULTS = [\"NOTDONE\"]")
        if not crashed_ns and not pending_ns:
            lines.append("   NOT SCORED: none -- every scenario was judged")
    lines.append("")
    lines.append("=" * 100)

    text = "\n".join(lines)
    base = _results_root()
    try:
        if not os.path.isdir(base):
            os.makedirs(base)
        with open(os.path.join(base, "ALL_PROJECTS_SPP_SUMMARY.txt"), "w") as fh:
            fh.write(text + "\n")
        if WRITE_CSV:
            with open(os.path.join(base, "ALL_PROJECTS_SPP_SUMMARY.csv"), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["project", "fault_mode", "scenario", "spp_verdict",
                            "run_status", "failed_criterion", "detail", "results_dir"])
                w.writerows(rows)
        print("[parallel] all-projects summary -> %s"
              % os.path.join(base, "ALL_PROJECTS_SPP_SUMMARY.txt"))
    except Exception as e:
        print("[parallel] could not write the all-projects summary: %s" % e)
    return text


def confirm_case():
    """State WHICH CASE this launcher is about to write into, before it does.

       Two launchers now sit side by side -- z7_lch_b.py for the base
       case and z7_lch_p.py for the projects -- and they are nearly
       identical files pointed at different folders. The failure that costs a
       day is running one thinking it is the other: the results it overwrites
       are the ones you were about to compare against, and nothing in the output
       would have told you which case produced them.

       So say it, at the top, in the terms that distinguish them: the folder, the
       case name, the .dyr, and how much finished work is already sitting there."""
    print("")
    print("#" * 78)
    print("#  %s" % os.path.basename(sys.argv[0] or "launcher"))
    print("#  study folder : %s" % STUDY_DIR)
    print("#  study script : %s" % STUDY_SCRIPT)
    print("#  results root : %s" % _results_root())
    # WHAT IS ACTUALLY BEING READ, not what the study script says by default.
    #
    # These two lines were parsed out of the study script's SOURCE TEXT, so
    # when z7_main.py points the run at a different deck through
    # SPP_SOURCE_CASE / SPP_DYR_FILE the banner went on naming the file in the
    # script -- and the banner is the thing anyone reads to check which case
    # they are running. It said DIS2201-25SP-G03-CQ while the study loaded
    # DIS2201-25SP-G03-CQ_BESS_SantaFe_502MW.sav. The environment wins here as
    # it wins in the study, and where it is set the banner says so.
    _base, _dyr, _how = "", "", ""
    try:
        _txt = open(STUDY_SCRIPT, errors="replace").read() if sys.version_info[0] >= 3 \
               else open(STUDY_SCRIPT).read()
        _m = re.search(r'^COMMON_BASE\s*=\s*"([^"]+)"', _txt, re.M)
        if _m:
            _base = _m.group(1)
        _m = re.search(r'^DYR_FILE\s*=.*?"([^"]+\.dyr)"', _txt, re.M)
        if _m:
            _dyr = _m.group(1)
    except Exception:
        pass
    _env_sav = (os.environ.get("SPP_SOURCE_CASE") or "").strip()
    _env_dyr = (os.environ.get("SPP_DYR_FILE") or "").strip()
    if _env_sav:
        _base, _how = _env_sav, "  <- from z7_main.py"
    if _env_dyr:
        _dyr = _env_dyr
    if _dyr:
        print("#  dynamics    : %s%s" % (_dyr, "  <- from z7_main.py" if _env_dyr else ""))
    if _base:
        print("#  power flow  : %s%s" % (_base, _how))
    # How much finished work is here already -- the thing FRESH_START discards.
    try:
        _outs = [f for d in _run_glob(_results_root(), "*")
                 for f in glob.glob(os.path.join(d, "outs", "*.out"))]
        _done = [f for d in _run_glob(_results_root(), "*")
                 for f in glob.glob(os.path.join(d, "outs", "*.done"))]
    except Exception:
        _outs = _done = []
    if _outs:
        print("#  already here: %d .out file(s), %d finished scenario(s)"
              % (len(_outs), len(_done)))
        if FRESH_START and not REPORT_ONLY:
            print("#")
            print("#  *** FRESH_START = True: the %d .done marker(s) above will be" % len(_done))
            print("#      CLEARED, so every scenario runs again from the start. The .out")
            print("#      files stay, but they are overwritten as each one re-runs. Set")
            print("#      FRESH_START = False to continue this study instead. ***")
    else:
        print("#  already here: nothing -- this is a first run for this folder")
    print("#" * 78)
    sys.stdout.flush()


def preflight_python():
    """Refuse to launch if PYTHON has no matching PSSPY## folder.

       PSS/E 34 ships its Python API as version-locked .pyc: PSSPY27 is importable
       only from Python 2.7, PSSPY37 only from 3.7, and so on. Launch with anything
       else and every process dies on `import psspy` -- which the launcher reads as
       a crash, so it relaunches, and the relaunch dies the same way. Three identical
       tracebacks and a spent relaunch budget for one wrong interpreter.

       PYTHON defaults to sys.executable, so the interpreter that runs THIS file is
       the one the workers get: `python run_parallel3.py` from a PATH where `python`
       is 3.9 gives 3.9 shards. Check it here, before anything is spawned, and name
       an interpreter that would work."""
    import glob as _g
    if PYTHON == sys.executable:
        pyv = sys.version_info[:2]
    else:
        try:
            out = subprocess.check_output(
                [PYTHON, "-c", "import sys;print('%d %d' % sys.version_info[:2])"],
                stderr=subprocess.STDOUT)
            pyv = tuple(int(x) for x in out.decode("ascii", "replace").split()[:2])
        except Exception as e:
            print("[parallel] could not ask %s for its version (%s) -- skipping the check"
                  % (PYTHON, e))
            return True
    want = "PSSPY%d%d" % pyv
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        roots += [d for d in _g.glob(os.path.join(base, "PSSE3*")) if os.path.isdir(d)]
    found = {}
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for p in _g.glob(os.path.join(root, "PSSPY*")):
            if os.path.isdir(p):
                found[os.path.basename(p)] = p
    if not found:
        print("[parallel] no PSSPY## folder found at all -- letting the study script")
        print("[parallel]     look for itself (set PSSE_ROOT if it is installed elsewhere).")
        return True
    if want in found:
        return True
    print("")
    print("[parallel] *** WRONG PYTHON -- nothing would import psspy ***")
    print("[parallel]     running: %s  (Python %d.%d)" % (PYTHON, pyv[0], pyv[1]))
    print("[parallel]     PSS/E needs %s, and that folder does not exist." % want)
    print("[parallel]     PSSPY folders present:")
    for k in sorted(found):
        print("[parallel]       %s" % found[k])
    # Name an interpreter that WOULD work, rather than leaving it as an exercise.
    cands = []
    for k in sorted(found):
        m = re.match(r"PSSPY(\d)(\d)$", k)
        if not m:
            continue
        vv = "%s.%s" % (m.group(1), m.group(2))
        for c in (r"C:\Python%s%s\python.exe" % (m.group(1), m.group(2)),
                  os.path.expandvars(r"%%LOCALAPPDATA%%\Programs\Python\Python%s%s\python.exe"
                                     % (m.group(1), m.group(2))),
                  r"C:\Program Files (x86)\PTI\PSSE34\PSSPYTHON%s%s\python.exe"
                  % (m.group(1), m.group(2))):
            if os.path.isfile(c):
                cands.append((vv, c))
    print("[parallel]     Launch it with one of these instead:")
    if cands:
        for vv, c in cands:
            print("[parallel]       \"%s\" %s" % (c, os.path.basename(sys.argv[0])))
    else:
        print("[parallel]       py -3.7 %s        (if the launcher is installed)"
              % os.path.basename(sys.argv[0]))
        print("[parallel]       or the full path to a Python %s"
              % " / ".join(sorted("%s.%s" % (k[5], k[6:]) for k in found if len(k) > 6)))
    print("[parallel]     Use the SAME interpreter the other terminal is running --")
    print("[parallel]     that one works, so it is already the right version.")
    print("")
    return False


def main():
    """Run every selected project x fault mode, each as a complete study."""
    global _RUN_STARTED
    _RUN_STARTED = time.strftime("%Y-%m-%d %H:%M:%S")   # rows older than this are stale
    if not os.path.isfile(STUDY_SCRIPT):
        print("[parallel] STUDY_SCRIPT not found: %s" % STUDY_SCRIPT); return 2
    if not preflight_python():
        return 2
    confirm_case()
    global _CUR_PROJECT, _CUR_MODE
    passes = [(p, m) for p in (RUN_PROJECTS or [""]) for m in (FAULT_MODES or ["spp"])]
    if len(passes) > 1:
        _banner("%d STUDIES QUEUED -- %d project(s) x %d fault mode(s)"
                % (len(passes), len(RUN_PROJECTS or [""]), len(FAULT_MODES or ["spp"])))
        for i, (p, m) in enumerate(passes, 1):
            print("   %d. %-16s %-8s -> results\\%s"
                  % (i, p or "(as set in the study script)", m,
                     ("%s_%s" % (p, m)) if p else "dynamics"))
        if REPORT_ONLY:
            print("   REPORT ONLY: no build, no workers, no simulations. Each pass scores")
            print("   the .out files already in that project's outs\\ folder.")
        else:
            print("   Each is a COMPLETE study -- build, workers, report -- in its own")
            print("   folder. A failure in one does not stop the rest; the tally at the")
            print("   end says which succeeded.")
    results, finished = [], set()
    for i, (proj, mode) in enumerate(passes, 1):
        # EVERY per-pass step is inside the try, including _set_paths(). It was
        # outside, so anything it raised would have escaped the loop and taken the
        # remaining projects with it -- the one failure mode this loop exists to
        # prevent. A pass may fail; the QUEUE may not.
        try:
            _CUR_PROJECT, _CUR_MODE = proj, mode
            _set_paths()
            # THE BUILD AND THE START HOLD THIS CASE'S CORES TOO: say so, or the
            # panel reads the 0 left by the last pass and scores into them.
            try:
                _write_alive(int(N_WORKERS), force=True)
            except Exception:
                pass
            if len(passes) > 1:
                _banner("STUDY %d of %d -- project %s, %s faults%s   (started %s)"
                        % (i, len(passes), proj or "(default)", mode,
                           ("  --  %s" % _run_kind()) if _run_kind() else "",
                           time.strftime("%H:%M:%S")))
            rc = _run_one_study()
        except KeyboardInterrupt:
            raise
        except Exception:
            import traceback; traceback.print_exc()
            rc = 1
        except BaseException as _be:
            # SystemExit AND EVERYTHING ELSE THAT IS NOT AN Exception.
            #
            # `except Exception` does not catch SystemExit, and one sys.exit()
            # anywhere under _run_one_study() therefore ended the whole launcher
            # in the middle of the queue -- three projects done, the fourth never
            # started, and no error printed because SystemExit prints nothing.
            # A pass may fail; the QUEUE may not.
            import traceback; traceback.print_exc()
            print("[parallel] *** study %d of %d (%s / %s) raised %s -- that pass is "
                  "abandoned, the queue continues ***"
                  % (i, len(passes), proj or "default", mode, type(_be).__name__))
            rc = 1
        results.append((proj, mode, rc))
        # THE PANEL MAY SCORE THIS PROJECT NOW. Its simulation is over for this
        # case; z7_main.py waits for this stamp from BOTH cases, then scores it
        # on the idle cores while this launcher goes on to the next project.
        if i >= len(passes):
            _write_alive(0, force=True)
        if not REPORT_ONLY:
            try:
                with open(os.path.join(_flags_dir(), "SIM_FINISHED.txt"), "w") as fh:
                    fh.write("%s\trc=%s\tpid=%d\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                     rc, os.getpid()))
            except Exception:
                pass
        if STOP_ON_FAILED_PROJECT and rc not in (0, None) and i < len(passes):
            print("")
            print("[parallel] *** project %s ended with rc=%s -- STOP_ON_FAILED_PROJECT: the remaining"
                  " %d pass(es) are NOT started. Read this project's logs (results\\%s_%s\\logs) first. ***"
                  % (proj, rc, len(passes) - i, proj, mode))
            break
        finished.add("%s/%s" % (proj or "default", mode))
        if len(passes) > 1:
            print("[parallel] study %d of %d (%s / %s) finished rc=%s at %s"
                  % (i, len(passes), proj or "default", mode, rc, time.strftime("%H:%M:%S")))
        # Roll the criteria up NOW, over every project finished so far, rather than
        # only after the last one. If the run is stopped -- or a later project hangs
        # -- the summary for what IS done is already on disk.
        try:
            _write_all_projects_report(passes, finished)
        except Exception as e:
            print("[parallel] all-projects summary failed (non-fatal): %s" % e)
        if len(passes) > 1 and i < len(passes):
            print("[parallel] --> starting study %d of %d next: %s / %s"
                  % (i + 1, len(passes), passes[i][0] or "default", passes[i][1]))
    _write_alive(0, force=True)       # this launcher simulates nothing more
    # Every simulation is done; now wait for the criteria reports that were still
    # being produced in the background. This is the ONLY place the launcher blocks
    # on them, and by now they have had the whole of the following projects' run
    # time to work in -- so most will already be finished.
    if _REPORT_BG:
        _banner("ALL SIMULATIONS FINISHED -- waiting for %d background criteria "
                "report(s)" % len(_REPORT_BG))
        _reap_reports(block=True)

    if len(passes) > 1:
        _banner("ALL %d STUDIES FINISHED" % len(passes))
        print("  %-18s %-8s %-6s %s" % ("project", "mode", "rc", "results"))
        for proj, mode, rc in results:
            print("  %-18s %-8s %-6s %s"
                  % (proj or "(default)", mode, rc,
                     os.path.join(_proj_root(_results_root(), proj),
                                  ("%s_%s" % (proj, mode)) if proj else "dynamics")))
        # ANY PASS THE QUEUE NEVER REACHED. Without this a launcher that ended
        # early listed three studies and said nothing about the fourth.
        _missing = [(p, m) for (p, m) in passes
                    if not any(p == rp and m == rm for rp, rm, _rc in results)]
        if _missing:
            print("")
            print("  *** %d of %d pass(es) were NEVER RUN: %s ***"
                  % (len(_missing), len(passes),
                     ", ".join("%s/%s" % (p or "default", m) for p, m in _missing)))
            print("      The queue ended before reaching them. Run again: finished")
            print("      scenarios are skipped, so only the missing work is done.")
        bad = [r for r in results if r[2] not in (0, None)]
        if bad:
            print("")
            print("  *** %d of %d did not finish cleanly: %s ***"
                  % (len(bad), len(results),
                     ", ".join("%s/%s" % (p or "default", m) for p, m, _r in bad)))
        # The combined criteria table, on screen, as the last thing printed.
        try:
            _banner("SPP CRITERIA -- ALL PROJECTS")
            print(_write_all_projects_report(passes, finished))
        except Exception as e:
            print("[parallel] all-projects summary failed: %s" % e)
        return 0 if not bad else 1
    # Single pass: the roll-up written inside the loop was produced while the
    # report was still running, so it would have said the report was missing.
    # Rewrite it now that the report has been collected.
    try:
        _banner("SPP CRITERIA")
        print(_write_all_projects_report(passes, finished))
    except Exception as e:
        print("[parallel] summary failed: %s" % e)
    return results[0][2] if results else 1


def _run_one_study():
    global _CONSOLE_FH
    t0 = time.time()
    # THE UNDELETABLE-FILE LIST BELONGS TO THIS PASS, NOT TO THE LAUNCH.
    #
    # _LOCKED is a module-level accumulator that _clear() appends to whenever a
    # delete fails, and only _report_locked() empties it -- and only when it
    # actually prints. _clear() is also called from _run_phase() and
    # _run_workers(), and neither of those ever calls _report_locked(). So one
    # locked sentinel in project N's cleanup stayed in the list, and project
    # N+1's check at the top of its run found it, returned 1, and that project
    # ABORTED BEFORE IT BUILT ANYTHING -- naming files in the previous
    # project's results folder, which is what made it unreadable.
    del _LOCKED[:]
    try:
        if not os.path.isdir(LOGS_DIR): os.makedirs(LOGS_DIR)
        if _CONSOLE_FH is not None:
            # the previous project's log: closed, not left open (and locked) per pass
            with _PRINT_LOCK:
                try:
                    _CONSOLE_FH.close()
                except Exception:
                    pass
                _CONSOLE_FH = None
        _CONSOLE_FH = open(CONSOLE_LOG, "w", buffering=1)   # combined tagged output of all workers
        print("[parallel] combined worker console -> %s" % CONSOLE_LOG)
    except Exception as e:
        print("[parallel] could not open combined console log (%s) -- continuing" % e)

    if REPORT_ONLY:
        _banner("REPORT ONLY  --  scoring existing .out files, no simulations")
    else:
        _banner("PARALLEL LAUNCHER  --  N_WORKERS = %d   (%s)"
                % (N_WORKERS,
                   "TRUE PARALLEL (multiple PSS/E processes)" if N_WORKERS > 1
                   else "N_WORKERS=1 -> effectively SEQUENTIAL; raise N_WORKERS for parallelism"))
    print("[parallel] script = %s" % STUDY_SCRIPT)
    print("[parallel] results = %s" % RESULTS)
    if not os.path.isdir(RESULTS):
        print("[parallel] *** that results folder does not exist yet ***")
        alt = sorted(glob.glob(os.path.join(_results_root(), "*")))
        if alt:
            print("[parallel]     folders present under results\\ :")
            for a in alt:
                print("[parallel]       %s" % os.path.basename(a))
            print("[parallel]     If the study writes to one of those, set RESULTS_SUBDIR to it.")
    if REPORT_ONLY:
        # ---- REPORT ONLY -------------------------------------------------------
        # Score what is already on disk. No build, no workers, no simulations.
        _outs = glob.glob(os.path.join(OUT_DIR, "*.out"))
        _done = glob.glob(os.path.join(OUT_DIR, "*.done"))
        _banner("REPORT ONLY -- scoring the .out files already in %s" % OUT_DIR)
        if not os.path.isdir(OUT_DIR):
            print("[parallel] *** %s does not exist -- nothing to report on ***" % OUT_DIR)
            print("[parallel]     RUN_PROJECTS / FAULT_MODES decide which folder is read:")
            print("[parallel]     results\\<project>_<mode>\\outs")
            return 1
        print("[parallel] %d .out file(s), %d with a .done marker" % (len(_outs), len(_done)))
        if not _outs:
            print("[parallel] *** no .out files here -- nothing to score. ***")
            return 1
        if not _done:
            print("[parallel] *** .out files are present but NONE has a .done marker. ***")
            print("[parallel]     REPORT_STRICT only scores scenarios that finished, so this")
            print("[parallel]     would produce an empty report. Either those runs never")
            print("[parallel]     completed, or the markers were deleted -- FRESH_START does")
            print("[parallel]     exactly that. Set REPORT_STRICT = False in the study script")
            print("[parallel]     to score them anyway, knowing they may not have converged.")
        # FRESH_START is deliberately NOT applied here: it deletes the .done
        # markers the report needs, which would empty the report of a complete
        # set of results.
        if FRESH_START:
            print("[parallel] (FRESH_START is on but IGNORED in REPORT_ONLY -- it would delete")
            print("[parallel]  the .done markers this report reads.)")
        check_study_version()
        clear_badout(OUT_DIR)
        _sel = _report_selection()
        if _sel == []:
            return 1
        if REPORT_WORKERS > 1:
            _run_report_sharded(min(REPORT_WORKERS, max(1, len(_sel or _outs))), _sel)
            _rep = _rfile_tagged(RESULTS, "SPP_CRITERIA_REPORT", "txt")
            if not (os.path.isfile(_rep) and os.path.getsize(_rep) > 0):
                print("[parallel] *** the report did not produce %s ***" % _rep)
        elif REPORT_IN_BACKGROUND:
            _start_report_bg(_CUR_PROJECT, _CUR_MODE, RESULTS)
        else:
            _run_phase("report", "_report")
            _rep = _rfile_tagged(RESULTS, "SPP_CRITERIA_REPORT", "txt")
            if not (os.path.isfile(_rep) and os.path.getsize(_rep) > 0):
                print("[parallel] *** the report did not produce %s ***" % _rep)
        _write_run_summary(t0, launches=None,
                           why="REPORT ONLY -- scored the .out files already on disk; "
                               "no simulations were run")
        return 0

    print("[parallel] phases: 1) BUILD (single process) -> 2) WORK (%d in parallel) -> 3) REPORT" % N_WORKERS)

    check_study_version()
    selected = _resolve_selection(RUN_ONLY_FAULTS)
    if selected == ["__EVENT_LIST_UNREADABLE__"] and not _have_fault_file():
        # FIRST LAUNCH: the fault list ONLY_EVENTS needs is written by the build.
        # Build now, then apply the selection -- it used to go on and start
        # workers with an id that matches nothing, simulate nothing, and end rc 0
        _dialog_sweeper_start()
        _banner("PHASE 1/3: BUILD first -- ONLY_EVENTS needs the fault list it writes")
        if not _run_phase("build", "_build"):
            print("[parallel] BUILD failed -- aborting."); return 1
        selected = _resolve_selection(RUN_ONLY_FAULTS)
    if selected == ["__EVENT_LIST_UNREADABLE__"]:
        print("[parallel] *** ONLY_EVENTS cannot be applied -- stopped, nothing simulated ***")
        return 1

    # FRESH_START AND SKIP_DONE ASK FOR OPPOSITE THINGS. FRESH_START deletes the
    # .done markers; SKIP_DONE reads them to decide what has already run. With
    # both on there is nothing left to read, so the run starts from scratch --
    # which is FRESH_START's job, and is what you want when the case or the
    # fault list changed. It is worth stating plainly, because "resume" is what
    # SKIP_DONE looks like it promises and hours of finished work are at stake.
    if FRESH_START and SKIP_DONE:
        print("")
        print("[parallel] *** FRESH_START is ON, so SKIP_DONE has nothing to skip ***")
        print("[parallel]     FRESH_START deletes the .done markers; SKIP_DONE reads")
        print("[parallel]     them. Every scenario will be simulated again.")
        print("[parallel]     To RESUME instead, set FRESH_START = False.")
        print("")
    selected = _apply_skip_done(selected)
    if (RUN_ONLY_FAULTS or ONLY_EVENTS) and not selected:
        # NOTHING MATCHES -- NOTHING TO RUN. Not a failure: the selection simply
        # names no scenario in this case's list, so exit 0 and let a queue with
        # STOP_ON_FAILED_PROJECT carry on to the next project. (Finished ids come
        # back as __ALL_ALREADY_DONE__, not empty, so this is only a no-match.)
        print("[parallel] *** RUN_ONLY_FAULTS/ONLY_EVENTS resolved to NOTHING -- nothing "
              "matches, nothing to run (the whole study is NOT run by accident). ***")
        return 0
    if selected:
        _banner("SELECTIVE RUN -- %d scenario(s): %s" % (len(selected), ", ".join(selected)))
        selected = _check_selection(selected)

    _dialog_sweeper_start()

    if FRESH_START and not selected:
        nrm = 0
        # THE SYSTEM-ADJUSTMENTS RECORD GOES WITH THEM. Every worker APPENDS one
        # line per change it makes -- they are separate processes and none can
        # see the others' memory, so a shared list would report a fraction of
        # them. Appending means the file outlives the run that wrote it, and a
        # run that started over would report the PREVIOUS run's adjustments as
        # its own. Cleared here, where "start over" is already being carried
        # out, and nowhere else: with FRESH_START off the run is resuming, the
        # deck and the settings are the same, and the earlier lines are still
        # the truth about it.
        for p in (glob.glob(os.path.join(OUT_DIR, "*.done")) +
                  glob.glob(os.path.join(OUT_DIR, "*.attempts")) +
                  glob.glob(os.path.join(OUT_DIR, "*.hangs")) +
                  glob.glob(os.path.join(OUT_DIR, "*.requeued")) +
                  glob.glob(os.path.join(RESULTS, "SYSTEM_ADJUSTMENTS.txt")) +
                  _sentinels()):
            if _clear(p):
                nrm += 1
        print("[parallel] FRESH_START: cleared %d marker/sentinel file(s)" % nrm)
        # A .done that could not be deleted makes the study skip that scenario as
        # already finished, so a "fresh" run silently reruns nothing. Worth stopping
        # for rather than discovering three hours later.
        if _report_locked("FRESH_START could not do its job: any .done left behind "
                          "makes that scenario be SKIPPED as already complete."):
            print("[parallel] *** stopping. Kill the stale process, then start again. ***")
            return 1
    elif selected:
        # Clear the markers for the SELECTED ids only. Wiping them all would force
        # every finished scenario to run again, which is the opposite of the point.
        nrm = 0
        # WHICH MARKERS MAY BE CLEARED DEPENDS ON WHERE THE SELECTION CAME FROM.
        #
        # This branch was written for RUN_ONLY_FAULTS -- ids named on purpose,
        # to be run again from scratch. But `selected` also carries the output
        # of _apply_skip_done(), which is every scenario that has NOT finished
        # -- precisely the ones that have burned attempts. Deleting their
        # .attempts reset the per-scenario poison guard on every launch, so
        # MAX_SCENARIO_ATTEMPTS counted attempts within a launch instead of
        # against the scenario, and a scenario that crashes every time was
        # retried for ever across launches.
        #
        # SKIP_DONE_SELECTED already records which kind of selection this is.
        _exts = ("done",) if SKIP_DONE_SELECTED else ("done", "attempts", "hangs")
        for sid in selected:
            for ext in _exts:
                p = os.path.join(OUT_DIR, "%s.%s" % (sid, ext))
                if os.path.isfile(p) and _clear(p):
                    nrm += 1
        for p in _sentinels():
            if _clear(p):
                nrm += 1
        print("[parallel] selective run: cleared %d marker(s) for the selected id(s) only "
              "-- finished scenarios are untouched" % nrm)
        _report_locked("The selected scenarios keep their old .done markers, so they "
                       "will be SKIPPED rather than re-run.")

    # Phase 1 -- BUILD (snapshot + flat + fault definitions). MUST finish before workers.
    #  NOTE: this phase is a SINGLE process -- during it you will (correctly) see only ONE
    #  PSS/E running. The N parallel workers start only AFTER the build+flat finishes.
    # A selective run reuses the snapshot the earlier build already made, so the build
    # phase is skipped when the flat run is done AND the fault file exists. If either is
    # missing the build still runs -- that is what regenerates the fault definition file.
    _why_f = _timing_changed(OUT_DIR, "FLAT_RUN")
    if _why_f:
        _stale_aside(OUT_DIR, "FLAT_RUN", "timing")
        print("[parallel] the no-fault run on disk %s -- moved aside and run again" % _why_f)
    _flat_done = os.path.isfile(os.path.join(OUT_DIR, "FLAT_RUN.done"))
    _skip_build = bool(selected) and _flat_done and _have_fault_file()
    if _skip_build:
        _banner("PHASE 1/3: BUILD SKIPPED -- selective run, snapshot and fault file "
                "already exist (%s)" % _faults_csv())
    else:
        if selected and not _have_fault_file():
            print("[parallel] fault file missing -- the build phase will regenerate it")
        _banner("PHASE 1/3: BUILD (single process) -- snapshot + flat + fault list. "
                "Only ONE PSS/E runs here; workers start after this.")
        if not _run_phase("build", "_build"):
            print("[parallel] BUILD failed -- aborting."); return 1

    # Show which worker will run which faults BEFORE launching them.
    if selected:
        _n = max(1, min(N_WORKERS, len(selected)))
        print("[parallel] %d selected scenario(s) split across %d worker(s):"
              % (len(selected), _n))
        for i in range(_n):
            print("   worker %d (%d): %s"
                  % (i, len(selected[i::_n]), ", ".join(selected[i::_n])))
    else:
        _print_assignment(N_WORKERS)

    # Phase 2 -- WORK (N concurrent workers, disjoint fault slices).
    # With a selection, never start more workers than there are scenarios to run --
    # a worker with an empty slice would just start PSS/E and exit.
    n_work = min(N_WORKERS, len(selected)) if selected else N_WORKERS
    n_work = max(1, n_work)
    if selected and n_work != N_WORKERS:
        print("[parallel] %d selected scenario(s) -> using %d worker(s) instead of %d"
              % (len(selected), n_work, N_WORKERS))
    _WANT_N[0] = n_work
    # ANNOUNCE FIRST, THEN LOOK. The panel's scorer writes its shard count and
    # then re-reads this file; this side writes its count and then reads the
    # scorer's. Whichever order they land in, at least one of them sees the
    # other, so the two can never both take the same cores.
    _write_alive(n_work, force=True)
    _cap = _sim_allowed()
    if _cap is not None and _cap < n_work:
        print("[parallel] %d core(s) are scoring other projects -- starting %d worker(s) "
              "instead of %d; the rest join as the scoring finishes" % (
                  _read_count(_BUSY_FILE), _cap, n_work))
        n_work = _cap
        _write_alive(n_work, force=True)
    _announce_work(selected, n_work)
    # EVERY .out MARKED, not only every scenario counted done. With
    # ONLY_MISSING_OUT a finished .out counts as done without a .done; the
    # catch-up plotter is what writes that marker (and .partial), and without
    # it the report would leave those scenarios out.
    _unmarked = []
    if selected == ["__ALL_ALREADY_DONE__"]:
        for _o in glob.glob(os.path.join(OUT_DIR, "*.out")):
            _b = _o[:-4]
            if os.path.isfile(_b + ".done") or os.path.isfile(_b + ".partial"):
                continue
            # ALREADY JUDGED: a .plotted / .badout marker at least as new as the
            # .out means the catch-up pass has looked at it since it was run
            # (a non-converged run, refused) and would do nothing new.
            try:
                _om = os.path.getmtime(_o)
                if any(os.path.isfile(_b + _x) and os.path.getmtime(_b + _x) + 1.0 >= _om
                       for _x in (".plotted", ".badout")):
                    continue
            except Exception:
                pass
            _unmarked.append(os.path.basename(_b))
        if _unmarked:
            print("[parallel] every scenario is simulated, but %d .out file(s) have no "
                  ".done/.partial marker yet (%s) -- running the catch-up pass that "
                  "writes them" % (len(_unmarked), ", ".join(sorted(_unmarked)[:6])))
    if selected == ["__ALL_ALREADY_DONE__"] and not _unmarked:
        # NOTHING TO SIMULATE: NO WORKER AND NO CATCH-UP PLOTTER. A worker here
        # started PSS/E -- a licence checkout, often a snapshot rebuild -- to run
        # nothing, and the catch-up plotter behind it drew the folder one file
        # at a time for up to PLOT_CATCHUP_MAX_S before any scoring began. The
        # report phase scores the folder and the panel's plot pass draws it.
        print("[parallel] nothing to simulate -- no PSS/E worker and no catch-up "
              "plotter for this folder; it goes straight to the report")
        _write_alive(0, force=True)
        ok = True
    else:
        ok = _run_workers(n_work, selected)
    if not ok:
        print("[parallel] WARNING: one or more workers did not fully finish -- the merged "
              "report will cover whatever completed.")

    # >>> WRITE THE SUMMARY NOW, BEFORE THE REPORT PHASE RUNS.
    #     The report reads every .out, and a NaN .out from a gave-up scenario has been
    #     seen to abort the process at the Fortran level ("Invalid Floating-Point
    #     number.") -- which kills it outright AND still returns rc=0. Writing the
    #     summary here means three hours of work can no longer be lost to that.
    _write_run_summary(t0, launches=None, why="written after the WORK phase, "
                       "before the report -- so a report crash cannot lose it")

    # Phase 3 -- REPORT (merge every finished .out into one SPP criteria report).
    # In the background by default, so the next project's build starts NOW rather
    # than after the .out files have all been read. Everything below that judges
    # the report is skipped in that case -- there is nothing to judge yet, and the
    # collection happens in main() once every project has run.
    if DEFER_REPORTS and not REPORT_ONLY:
        el = time.time() - t0
        _banner("RUNS COMPLETE for %s -- %s (report DEFERRED: z7_main.py scores "
                "every project once all of them have simulated)"
                % (_CUR_PROJECT or "this project", _fmt_hms(el)))
        _write_run_summary(t0, launches=None,
                           why="written after the WORK phase; the criteria report is "
                               "deferred to the panel's scoring pass over every project")
        return 0
    if REPORT_WORKERS > 1:
        # Sharded reports run HERE rather than in the background: they occupy
        # REPORT_WORKERS sessions at once, and letting several projects do that
        # concurrently would multiply the PSS/E session count without bound.
        _rsel = _report_selection()
        if _rsel == []:
            print("[parallel] REPORT_FAULTS resolves to no scenario in this study -- "
                  "scoring NOTHING rather than silently scoring everything.")
        else:
            _run_report_sharded(REPORT_WORKERS, _rsel)
    elif REPORT_IN_BACKGROUND:
        _start_report_bg(_CUR_PROJECT, _CUR_MODE, RESULTS)
        el = time.time() - t0
        _banner("RUNS COMPLETE for %s -- %s (report still running in the background)"
                % (_CUR_PROJECT or "this project", _fmt_hms(el)))
        _write_run_summary(t0, launches=None,
                           why="written after the WORK phase; the criteria report for "
                               "this project is still being produced in the background")
        return 0
    else:
        _run_phase("report", "_report")
    # Judge the report on what it PRODUCED, not on its exit code: the crash above
    # exits 0, so rc alone would call a failed report a success.
    _rep = _rfile_tagged(RESULTS, "SPP_CRITERIA_REPORT", "txt")
    _report_ok = os.path.isfile(_rep) and os.path.getsize(_rep) > 0
    if not _report_ok:
        print("[parallel] *** the REPORT phase did not produce %s ***" % _rep)
        print("[parallel]     Usually a NaN/partial .out from a gave-up scenario. The "
              "RUN_SUMMARY below is written from the markers on disk and is unaffected.")

    el = time.time() - t0
    _banner("PARALLEL STUDY COMPLETE -- total wall clock: %s (%.1f s) with %d worker(s)"
            % (_fmt_hms(el), el, N_WORKERS))
    print("[parallel] merged report -> %s"
          % _rfile_tagged(RESULTS, "SPP_CRITERIA_REPORT", "txt"))
    _write_run_summary(t0, launches=None, report_ok=_report_ok,
                       why="final summary, written after every phase")
    _echo_run_summary()
    return 0


if __name__ == "__main__":
    # The summary is written from a finally: as well, so it exists even if the run is
    # interrupted with Ctrl-C or the launcher itself raises. _write_run_summary never
    # raises, and re-writing it is harmless.
    _t_start = time.time()
    try:
        _rc = main()
    except KeyboardInterrupt:
        print("\n[parallel] INTERRUPTED -- writing the summary for what finished so far")
        try: _write_run_summary(_t_start, why="run INTERRUPTED by the user (Ctrl-C)")
        except Exception: pass
        _rc = 130
    except Exception:
        import traceback; traceback.print_exc()
        print("[parallel] launcher failed -- writing the summary for what finished so far")
        try: _write_run_summary(_t_start, why="the launcher raised before finishing")
        except Exception: pass
        _rc = 1
    sys.exit(_rc)
