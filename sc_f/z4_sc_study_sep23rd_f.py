# -*- coding: utf-8 -*-
"""
=============================================================================
 z4_sc_study.py -- SPP SHORT-CIRCUIT study, PSS/E 34 (Python 3.4, 32-bit)
 -----------------------------------------------------------------------------
 Methodology (SPP impact-study wording):
   "The short circuit analysis included applying a 3-phase fault on buses up to
    5 levels away from the POI bus. The PSS/E Automatic Sequence Fault
    Calculation (ASCC) module was used to calculate the fault current levels in
    the transmission system with and without the project generators online.
    The existing generating facilities at the POI were left online."

 What it does, for EVERY project in PROJECTS_SC:
   1. opens that project's .sav (the case WITH the project modelled),
   2. finds every bus up to HOPS levels from the POI (transformers included),
      keeps those at or above MIN_FAULT_KV,
   3. runs an ASCC 3-phase sweep over those buses
        - WITHOUT the project   (project machines out of service; with
          WITHOUT_SCOPE = "site", the existing units at the POI go out too)
        - WITH the project      at every capacity in CAPACITY_PCT (100 %, and
                                any partial capacities you list: the machine MVA
                                base is scaled, which is what sets a converter's
                                or generator's fault contribution)
      Everything else in the case, including existing units at the POI, is left
      exactly as saved.
   4. writes, per project:  SC_BUS_<project>.csv   every bus, every case, delta and %
                            SC_SUMMARY_<project>.txt
      and for all projects:  SC_REPORT_ALL.xlsx     (Summary, POI, one sheet per
                            project, Max by kV, Method)  and  SC_REPORT_ALL.txt
      plus plots (PDF) when matplotlib is available in this Python.

 Fault current: bolted 3-phase, flat 1.0 pu pre-fault (ANSI / SPP breaker-duty
 convention):   I = E / |Z1_thevenin|   in pu on the system base, converted to
 kA with  I_base = S_base / (sqrt(3) * kV).  Z1 comes straight from the ASCC
 result (pssarrays.ascc_currents), so the number is the module's own.
=============================================================================
"""
from __future__ import print_function
import os, sys, csv, math, time, glob, traceback, zipfile
from xml.sax.saxutils import escape

# =============================================================================
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>  SETTINGS  <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
# =============================================================================
# WHERE. None = the folder this script is in (put it beside the .sav files in
# C:\KV\Projects and nothing here needs editing).
STUDY_DIR   = None
RESULTS_DIR = None                                   # None = <STUDY_DIR>\shortcircuit
# THE CASE, found automatically:
#   1. a project row that names its own "sav" uses that;
#   2. else a .sav in STUDY_DIR whose name contains the project name (e.g. *_SantaFe.sav) is that project's;
#   3. else DEFAULT_SAV if set;
#   4. else the ONE .sav in STUDY_DIR -- or, with several, the newest, and the log says which.
# The built case from the dynamic study already holds the new plants and the
# existing units, which is what every project needs.
DEFAULT_SAV = None

# THE PROJECTS. Leave PROJECTS_SC empty ([]) to take them AUTOMATICALLY from the
# dynamic study's project table (BESS_PROJECTS in z4_spp_p*.py beside this
# script): name, POI and the existing units (its "feeders"). List them here only
# to override or to add sc_params.
PROJECTS_SC = [
    # name          : label used everywhere
    # sav           : OPTIONAL -- this project's own case (absolute, or relative to STUDY_DIR).
    #                 Leave it out to use DEFAULT_SAV for the project.
    # poi           : POI bus number
    # gens          : "NEW"  = the new-plant machines BEHIND THIS POI: machines on the new-plant bus
    #                 block (NEW_GEN_BUS_PREFIX) that reach the network only through this POI. With
    #                 every project built into one case, that picks this project's plant and leaves the
    #                 other projects' plants exactly as saved.
    #                 or an explicit list [{"bus": 999001, "id": "1", "mbase": 180.4, "r": 0.0, "xpp": 0.64}, ...]
    #                 mbase / r / xpp are optional: give them to apply the submitted short-circuit
    #                 source impedance (pu on mbase), omit them to use what the case holds.
    # keep_online   : existing units at the POI, left ONLINE in every case (documented, never toggled)
    # sc_params     : the SHORT-CIRCUIT MODEL PARAMETERS (report Table 5-1) written onto the project
    #                 machines before the sweeps -- Machine MVA base, R (pu) and X'' (pu on that base).
    #                 One dict for every project machine:   {"mbase": 180.4, "r": 0.0, "xpp": 0.64218}
    #                 or one per machine bus:                {999001: {"mbase": 180.4, "r": 0.0, "xpp": 0.64218}, 999002: {...}}
    #                 Leave out a key (or the whole entry) to keep what the case holds for it.
    # gia_mw : the GIA limit at this POI, MW. Case 2 dispatches the EGF to full
    #          capacity up to this number (see EGF_FULL_CAPACITY). Leave it out
    #          to dispatch to PMAX with no cap.
    # gia_mw  = the POI-injection target the case was BUILT to: the SGF at its
    #           rated MW plus the EGF picking up the remainder of the GIA.
    # sgf_mw  = the BESS rating, for the record -- the script reads the machines,
    #           it does not take the dispatch from this number.
    {"name": "SantaFe",       "poi": 765911, "gens": "NEW",
     "gia_mw": 984.2, "sgf_mw": 502,          # EGF remainder 482.2
     "keep_online": [765912, 765922, 765932, 765935]},
     # this project's own override, if it ever needs one -- uncomment and edit:
     # "sc_params": {"mbase": None, "r": 0.0, "xpp": 0.8}},
    {"name": "IronStar",      "poi": 560080, "gens": "NEW",
     "gia_mw": 290.0, "sgf_mw": 214,          # EGF remainder 76.0 (= POI_MW in the launcher)
     "keep_online": [587313, 587317]},
    {"name": "EmpirePrairie", "poi": 761383, "gens": "NEW",
     "gia_mw": 769.0, "sgf_mw": 604,          # EGF remainder 165
     "keep_online": [761379, 761382, 761400, 761403]},
    {"name": "EastFork",      "poi": 531429, "gens": "NEW",
     "gia_mw": 193.5, "sgf_mw": 112,          # EGF remainder 81.5
     "keep_online": [531620, 531607]},
]
# THE SHORT-CIRCUIT PARAMETERS USED FOR EVERY PROJECT that does not give its own
# sc_params (including projects read automatically from the dynamic study):
# R = 0, X = 0.8 pu on the machine MVA base, as in the SPP/MEPPI reports. The
# MVA base is left as the case holds it (mbase None). A project row's own
# sc_params overrides this, key by key.
# SPP Table 4-1 "Short-Circuit Model Parameters" -- the SUBMITTED data written
# onto the SGF machines, in pu on each machine's OWN MVA base.
# Applies to EVERY project unless overridden below. mbase: None = keep the MVA
# base the case holds.
DEFAULT_SC_PARAMS = {"mbase": None, "r": 0.0, "xpp": 0.8}

# ---- X'' PER PROJECT -- THE ONE PLACE TO CHANGE IT -------------------------
# Empty = every project uses DEFAULT_SC_PARAMS above (X'' = 0.8).
# Name a project to give it its own submitted value, either as a bare number:
#       SC_XPP_BY_PROJECT = {"IronStar": 0.962}
# or as a dict when MVA base or R differ too:
#       SC_XPP_BY_PROJECT = {"IronStar": {"mbase": 235.2, "r": 0.0, "xpp": 0.962}}
# A project's own "sc_params" row still wins over this, and a per-machine
# sc_params still wins over that -- most specific first.
#
# FOR REFERENCE, the value recent SPP surplus reports used in their Table 4-1:
#       GEN-2026-SR11, -SR16 : 0.893      GEN-2026-SR13, -SR14, -SR15 : 0.962
#       GEN-2025-SR16        : 0.8
# Whatever goes here, check it landed on the "SC model parameters" sheet:
# X'' written and X'' used by the fault calc must agree.
SC_XPP_BY_PROJECT = {}

# ---- X'' OF THE EXISTING UNITS (EGF) ----------------------------------------
# The fault calculation (ASCC) takes every unit's X'' from the SHORT CIRCUIT tab
# of its machine record (the sequence data: Subtransient X), not from the Power
# Flow tab's X Source. The X'' above goes on the SGF units only; the existing
# units are studied as the case holds them -- and EastFork's two units hold
# 9999 pu there (531607 also as X Source): an open circuit, so they feed NO
# fault current in any case, the POI current without the SGF is understated and
# the SGF's % change overstated.
# A project named here gets that X'' (pu on each unit's OWN MVA base) written
# on its keep_online units before the fault sweeps: the Subtransient X of the
# short-circuit data (the one ASCC uses; with GENXOP = 1 the Transient X too)
# and the X Source of the power-flow record. R, the MVA base and every other
# short-circuit value stay as the case holds them; the .sav is never changed
# (the saved SC cases carry the value, as they carry the SGF's).
#       EGF_XPP_BY_PROJECT = {"EastFork": 0.8}                          every EGF unit
#       EGF_XPP_BY_PROJECT = {"EastFork": {531620: 0.8, 531607: 0.8}}   per unit bus
# {} = every existing unit exactly as the case holds it (the old behaviour).
# Projects not named are not touched. The "SC model parameters" sheet lists
# every existing unit with the X'' written and the X'' the fault calc uses.
EGF_XPP_BY_PROJECT = {"EastFork": 0.8}

# ---- SAVE THE CASES THEMSELVES ---------------------------------------------
# True = each case is saved as a .sav the moment it is set up and solved, with
# the SGF's short-circuit model already written on it (MBASE / R / X'' on the
# machine record AND the sequence record), so it can be opened in PSS/E and the
# fault run by hand:
#     <CASES_DIR>\<project>_SC1_NOGEN_SGF_EGF_OUT.sav    SGF and EGF out of service
#     <CASES_DIR>\<project>_SC2_EGF_ONLY.sav             SGF out, EGF dispatched (EGF_DISPATCH)
#     <CASES_DIR>\<project>_SC3_SGF_EGF_<pct>PCT.sav      SGF + EGF in service, as built
# CASES_ONLY = True stops there: no fault sweep, no SC report -- just the cases
# and a CASES_<project>.txt saying what is on, what is off and at what MW.
SAVE_CASES = False
CASES_ONLY = False
CASES_DIR  = None                  # None = <RESULTS_DIR>\cases
NEW_GEN_BUS_PREFIX = "999"      # the bus block the new plants are built into ("NEW" above)
NEW_GEN_BUS_DIGITS = 6          # ...and how many digits those bus numbers have (999001, not 99905)
# Fallback reach when a plant's path to its POI leaves the bus block (a collector
# or GSU bus numbered outside 999xxx): block machines this many levels from the
# POI, and nearer to it than to any other project's POI, are taken as the plant.
NEW_GEN_MAX_HOPS = 5

HOPS         = 5                # fault every bus up to this many levels from the POI
MIN_FAULT_KV = 60.0             # fault only buses at/above this kV (the walk still crosses lower kV)
CAPACITY_PCT = [100]            # WITH-project cases: e.g. [100, 75, 50] -> one sweep per capacity
                                # (project machine MVA base scaled to pct %; 100 = as submitted)
APPLY_SC_PARAMS = True          # apply mbase / r / xpp given in "gens" (explicit lists only)
# WHOSE capacity a partial case reduces. Fault contribution follows the MVA in
# service (MBASE), not the dispatch, so "capacity" here means MVA base:
#   "new"   the project machines only ("gens"); the existing units stay as saved
#           -> the question "what does the new plant add at X % of its size"
#   "both"  the project machines AND the keep_online existing units, each to
#           X % of its own MVA base -> the whole site derated together
#   "site"  the SITE total held: existing units stay at 100 %, the project
#           machines scaled so that (existing + project) MVA = X % of the
#           full-site MVA (if X % is below the existing MVA alone, the project
#           goes to zero and the case says so)
CAPACITY_SCOPE = "new"

# WHAT THE "WITHOUT" CASE TURNS OFF. The comparison the report is built on is
# WITHOUT minus WITH, so this decides what the reported change is the change OF:
#   "new"   only the project machines ("gens") go out; the keep_online existing
#           units stay in service in BOTH cases -> the change is what the NEW
#           plant adds beside generation that is already there (the interconnection
#           question, and the default)
#   "site"  the WHOLE SITE goes out -- the project machines AND the keep_online
#           existing units -- so the WITHOUT case is the network with no
#           generation at this POI at all, and the change is the contribution of
#           the ENTIRE plant, new and existing together
# The WITH cases are unchanged either way: everything is back in service, at the
# capacity CAPACITY_SCOPE/CAPACITY_PCT set. Machines that were saved OUT OF
# SERVICE are left out of service -- "site" turns things off, never on.
WITHOUT_SCOPE = "new"

# ---- THE THREE CASES -------------------------------------------------------
# With RUN_NOGEN_CASE on, every project is studied in THREE states instead of
# two, and WITHOUT_SCOPE is ignored (case 1 below is the "whole site out" state
# that WITHOUT_SCOPE = "site" used to give):
#
#   CASE 1  NO GENERATION AT THE POI   SGF out, EGF out
#   CASE 2  EGF ONLY                   SGF out, EGF in     <- SPP's "EGF in service, SGF offline"
#   CASE 3  SGF + EGF                  SGF in,  EGF in     <- SPP's modified model
#
# which gives all three contributions per bus:
#   SGF  = case3 - case2      EGF = case2 - case1      whole site = case3 - case1
#
# SPP's own process is the middle and last rows -- "The first scenario was
# studied with both the SGF and EGF in service. In the second scenario the SGF
# was disconnected while the EGF was online to determine the impact of the SGF."
# Case 1 is an EXTRA that answers "what does the whole site contribute".
#
# LEAVING IT ON COSTS THE SPP TABLES NOTHING. Table 4-2 stays "EGF Only" vs
# "SGF & EGF" and Table 4-3 stays the SGF contribution by voltage, exactly as
# the reports print them -- case 1 only adds columns beside them. Set it False
# to skip the extra sweep.
RUN_NOGEN_CASE = True

# EGF DISPATCH IN CASE 2 -- five options:
#
#   "gia_poi" the EGF ALONE puts the project's full "gia_mw" INTO THE POI, so
#           case 2 injects the SAME MW at the POI as case 3 does with SGF+EGF
#           together -- like-for-like, and the only difference is WHICH machines
#           are producing it. The case is solved and the EGF rescaled for the
#           collector losses (which go with I^2 and are ~4x larger when the EGF
#           carries everything). No unit is pushed past its PMAX: an EGF that
#           cannot reach the GIA at the POI even at PMAX is dispatched to PMAX
#           and the shortfall is printed (SantaFe: ~980 of 984.2 MW).
#   "gia"   the same target at the machine TERMINALS -- lands short at the POI
#           by the losses (SantaFe 966 MW). Kept for comparison.
#   "pmax"  EVERY existing unit at its own PMAX, no cap -- the EGF's full
#           capacity regardless of the GIA (SantaFe 998 MW against a 984 GIA;
#           EmpirePrairie 800 against 769, OVER the GIA at the POI).
#   "full"  each unit to its own PMAX, scaled back only if the total would exceed
#           gia_mw.
#   "asis"  the dispatch is left exactly as the power-flow model holds it. This
#           is what the SPP reports describe -- their scenario 2 is the stability
#           Scenario 2 dispatch, and they state "No other changes were made to
#           the model".
#
# Whichever is chosen, the dispatch is restored before case 3 so that case 3
# keeps SPP's own proportional SGF+EGF sharing.
#
# READ THIS BEFORE EXPECTING IT TO MOVE A NUMBER. ASCC works from the machine
# SOURCE IMPEDANCE and the network, not from MW. Re-dispatching the SAME units
# from 487 to 984 MW leaves the Thevenin impedance untouched, so:
#   VOLTOP = 0  the fault current does not move at all;
#   VOLTOP = 1  it moves only through the solved prefault voltage -- a tenth
#               of a percent or so, and usually DOWN, since heavier loading
#               sags the POI voltage.
# What DOES move it by whole percents is CONNECTING units -- each EGF unit in
# service is a parallel source branch (about +3 % per 125 MW of EGF at a 345 kV
# POI in the GUI checks). That is EGF_ENERGIZE below, not this setting.
#
# WHY "asis" IS THE DEFAULT. SPP's scenario 2 is the EGF at ITS OWN share of the
# GIA with the SGF disconnected -- e.g. SantaFe 984.2 - 502 = 482.2 MW, which
# is exactly what the GUI one-line shows for that case. That IS like-for-like:
# the same GIA, split SGF+EGF in scenario 1 and carried by the EGF's share in
# scenario 2. "gia" pushes the EGF units to the whole GIA so the two cases
# quote the same POI MW; every project's EGF has the PMAX for it (SantaFe
# 4 x 249.6 = 998 MW against 984), and in the GUI it lowered the POI current
# 1.3 % (13.50 -> 13.33 kA) through the sagging voltage. Both are defensible;
# say which in the report.
EGF_DISPATCH = "gia_poi"

# EGF UNITS IN SERVICE. The fault contribution of the EGF is set by WHICH units
# are connected, not by their MW. With this on, every keep_online unit is put
# IN SERVICE for cases 2 and 3 (and the per-unit contribution runs), whatever
# status the .sav saved it with -- SPP's "EGF in service" means the whole EGF.
# Each unit switched in is named in the console and on the summary, and the
# saved status is put back when the project is done. Case 1 still takes them
# all out. Off, a unit saved out stays out, and its contribution is zero in
# every case.
EGF_ENERGIZE = True
# ...and in CASE 3 (SGF + EGF)? False = case 3 is the .sav EXACTLY as the
# dynamic study built it: a unit saved OUT is put back OUT before case 3, so the
# POI injection is the one the case was built to (EastFork: 531607 is saved out;
# switched in, its stale PGEN of ~16.6 MW pushed the POI from 193.5 to 210.1 MW).
# True = the old behaviour, every keep_online unit in service in case 3 too.
EGF_ENERGIZE_WITH = False

# CASE 3 -- HOW THE SGF AND EGF SHARE THE GIA. SPP's consultants word this two
# different ways, and they are NOT the same dispatch:
#
#   "gia"        "the EGF and SGF were dispatched PROPORTIONALLY to set the POI
#                injection to not exceed the Interconnection service amount."
#                Every machine at the POI is scaled by the same factor, so their
#                shares are preserved and only the total comes down. Downward
#                only -- a case already inside the limit is left alone.
#
#   "remainder"  "the SGF at 100% of the assumed dispatch while the EGF picked
#                up the REMAINING EGF GIA capacity." The SGF keeps its full
#                output and the EGF fills what is left: EGF = gia_mw - SGF.
#                (e.g. GIA 204.58 = SGF 101.38 + EGF 103.2.)
#
#   "asis"       trust the .sav to have been built that way already.
#
# Pick the one your study is written against -- they give different POI MW for
# the same GIA, though at VOLTOP = 0 neither changes a fault current.
#
# "asis" IS THE RIGHT DEFAULT WHEN THE .sav WAS ALREADY BUILT TO A POI TARGET.
# The study launcher dispatches to a POI-INJECTION target (POI_P_TARGET_MW),
# which accounts for the collector losses between the machines and the POI --
# EmpirePrairie solves to PGEN 609.3 + 166.0 = 775.3 MW at the machines but
# 769.2 MW INTO the POI, 6 MW of losses. Re-dispatching here works from machine
# PGEN and cannot see those losses, so it would land a few MW low. Better to
# trust the case and CHECK it: POI_GIA_CHECK below verifies the injection each
# case actually achieved against gia_mw, and says so when it is out.
POI_DISPATCH = "asis"

# Verify the POI injection against gia_mw and report any case that exceeds it.
POI_GIA_CHECK = True
POI_GIA_TOL_MW = 1.0            # allowance for rounding / losses, MW
# SAME POI MW IN EVERY PROJECT, EVERY EGF-IN CASE. gia_mw above must equal POI_MW
# in the dynamic-study launcher. True = a project whose SGF + EGF case is more
# than POI_GIA_TOL_MW off gia_mw (either way), or whose EGF-only case is OVER it,
# is STOPPED and written nowhere -- the comparison is only valid like-for-like.
# An EGF-only case that is UNDER because the EGF runs out of PMAX is reported,
# not stopped (that is the EGF's real capacity).
POI_GIA_STRICT = True

# ASCC options
#
# VOLTOP -- WHERE THE PREFAULT VOLTAGE COMES FROM. This is the ONE setting that
# decides whether the script matches the PSS/E GUI's ASCC dialog:
#
#   1  "Pre-fault bus voltage option: From power flow" -- the GUI's DEFAULT
#      preset and what the SPP consultants' consoles show ("SET PRE-FAULT
#      VOLTAGES AND PHASE SHIFT ANGLES TO POWER FLOW SOLUTION"). Every fault
#      current is driven by the SOLVED bus voltage (e.g. 1.0087 pu at a 345 kV
#      POI), so the currents run ~0.5-1 % above the flat figure, they depend on
#      the power flow having converged (REQUIRE_CONVERGENCE goes fatal), and
#      dispatch moves them -- by a tenth of a percent or so through the voltage.
#      The current reported is the module's own I''k (Ia1), as the GUI prints.
#
#   0  "FLAT Classical" -- 1.0 pu everywhere. Reproducible without a solved
#      case; the currents come from the Thevenin impedance alone and NO change
#      of MW can move them. The classical breaker-duty convention.
#
# Whichever you use, say so in the report; the Method sheet prints it. The two
# differ by the POI's solved voltage, ~0.65 % here -- never by more than the
# voltage profile, so a 6 % gap between runs is NEVER this setting.
#
# VOLTOP HERE IS THE STUDY'S CHOICE, NOT THE API CODE. The pssarrays `voltop`
# argument does not follow the dialog's numbering (see _probe_voltop), so with
# VOLTOP = 1 the script finds the code that reproduces the solved bus voltages
# and prints a probe table; ASCC_VOLTOP_CODE below the ASCC section forces one.
PREFAULT_VPU = 1.0              # the flat voltage used when VOLTOP = 0
VOLTOP  = 1                     # 0 = flat 1.0 pu pre-fault ; 1 = solved voltages (matches the GUI default)
GENXOP  = 0                     # 0 = subtransient X'' ; 1 = transient X'
# Any further ascc_currents keyword the installed build accepts (line charging,
# shunts, loads, taps ...), passed straight through. Empty = the module's
# defaults, which validated within 0.1 % of the GUI's "Leave unchanged". Only
# names help(pssarrays.ascc_currents) lists in YOUR PSS/E console go here; an
# unknown name makes every call form fail.
ASCC_EXTRA_OPTS = {}
SOLVE_PF = True                 # re-solve the power flow before each sweep
SOLVE_MAX_TRIES = 3             # decoupled + full-Newton passes per case before calling it unconverged
# WHAT A NON-CONVERGED CASE MEANS FOR THE RESULTS.
#   "auto"    abort the project when the fault currents depend on the solution
#             (VOLTOP = 1); with VOLTOP = 0 the currents come from a flat
#             prefault and the network impedance, so carry on but FLAG every
#             number that does read the solution (the POI MW).
#   "always"  abort the project if any case fails to converge, whatever VOLTOP is
#   "never"   report it and carry on regardless
REQUIRE_CONVERGENCE = "auto"
SBASE_FALLBACK = 100.0
MAKE_PLOTS = True
TOP_N = 25
CASE_LABEL = "25SP"             # the "CASE" name in the SPP-format tables (season / case tag)
HIGHLIGHT_KA = 40.0             # buses at/above this fault current are highlighted (breaker-duty watch), as in the SPP appendix
SPP_MAX_ROW = True              # add the "Max" row under the by-voltage table (some SPP reports print it, some do not)
# THE PLANT'S OWN LEAD IS NOT A "SYSTEM" BUS. Aneden footnotes Table 4-2/4-3 "For
# buses not on the generation interconnection line", and MEPPI's quoted maximum
# contribution equals the POI's -- neither counts the collector / HV tie bus of
# the plant being studied, where the contribution is trivially largest. With
# this on, new-plant-block buses (999xxx) are left out of the "maximum
# contribution" sentence, the by-voltage table and the Max-by-kV sheet. They
# stay in Appendix B and on the per-bus SC sheet, as the reports list them.
EXCLUDE_GEN_LEAD = True
WARN_NO_CONTRIBUTION_KA = 0.05  # if the LARGEST change anywhere is below this, say so loudly: a plant of real MVA that moves no bus is a modelling fault, not a result
WARN_FLAT_RATIO = 1.5           # ...and the POI should move at least this many times as much as the farthest bus faulted; if it does not, the "contribution" is numerical noise spread evenly
# PER-UNIT CONTRIBUTION. With every unit in service (the WITH-project state), each
# machine -- the existing units AND the project machines -- is switched out alone and
# the fault re-run; the drop in fault current is that unit's contribution (the
# report's ON-minus-OFF definition, applied unit by unit). One ASCC sweep per unit.
PER_MACHINE_CONTRIB = True
CONTRIB_FAULT_BUSES = ["POI"]   # where that fault is: ["POI"] (default), a list of bus numbers ("POI" allowed in the list), or "ALL" for every faulted bus
# =============================================================================

SQRT3 = math.sqrt(3.0)
_HERE = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))
STUDY_DIR = STUDY_DIR or _HERE
RESULTS_DIR = RESULTS_DIR or os.path.join(STUDY_DIR, "shortcircuit")
LOG_DIR = os.path.join(RESULTS_DIR, "logs")
for _d in (RESULTS_DIR, LOG_DIR):
    if not os.path.isdir(_d):
        os.makedirs(_d)
LOG_FILE = os.path.join(LOG_DIR, "SC_STUDY_%s.log" % time.strftime("%Y%m%d_%H%M%S"))


class _Tee(object):
    def __init__(self, fh, con):
        self.fh, self.con = fh, con

    def write(self, d):
        for s in (self.fh, self.con):
            try:
                s.write(d); s.flush()
            except Exception:
                pass

    def flush(self):
        for s in (self.fh, self.con):
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False


_LOG_FH = open(LOG_FILE, "w")
_ORIG_OUT, _ORIG_ERR = sys.stdout, sys.stderr
sys.stdout = _Tee(_LOG_FH, _ORIG_OUT)
sys.stderr = _Tee(_LOG_FH, _ORIG_ERR)

# ---- PSS/E 34 --------------------------------------------------------------
psspy = pssarrays = None


def _add_path(d):
    if d and os.path.isdir(d):
        while d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        return True
    return False


def bootstrap_psse():
    """Put THIS interpreter's PSSPY## folder and PSSBIN on the path -- the same
       discovery the dynamic study uses. PSSE_ROOT in the environment overrides."""
    pyv = sys.version_info[:2]
    prefer = "PSSPY%d%d" % pyv
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        for pat in ("PSSE34*", "PSSE3*", "PSSE*"):
            for d in glob.glob(os.path.join(base, pat)):
                if os.path.isdir(d) and d not in roots:
                    roots.append(d)
    avail = []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        avail += [p for p in glob.glob(os.path.join(root, "PSSPY*")) if os.path.isdir(p)]
        pdir, pbin = os.path.join(root, prefer), os.path.join(root, "PSSBIN")
        if os.path.isdir(pdir):
            _add_path(pdir); _add_path(pbin)
            print("[init] PSS/E: %s (+PSSBIN)  Python %d.%d" % (pdir, pyv[0], pyv[1]))
            return True
    for root in roots:
        pbin = os.path.join(root or "", "PSSBIN")
        if os.path.isfile(os.path.join(pbin, "psspy.pyd")) or os.path.isfile(os.path.join(pbin, "psspy.py")):
            _add_path(pbin)
            print("[init] PSS/E: %s (PSSBIN)  Python %d.%d" % (pbin, pyv[0], pyv[1]))
            return True
    print("[init] *** no %s folder for Python %d.%d under the PTI install. PSSPY folders present: %s\n"
          "        -> run with the Python that matches one of them (PSS/E 34.8 = C:\\Python34\\python.exe for PSSPY34),\n"
          "           or set the environment variable PSSE_ROOT to the PSS/E folder ***"
          % (prefer, pyv[0], pyv[1], ", ".join(avail) or "(none found)"))
    return False


def psse_init():
    global psspy, pssarrays, _i, _f, _s
    bootstrap_psse()
    for mod in ("psse34", "psse35"):
        try:
            __import__(mod)
            print("[init] %s" % mod)
            break
        except ImportError:
            continue
    import psspy as _psspy
    psspy = _psspy
    try:
        import redirect
        redirect.psse2py()
    except Exception as e:
        print("[init] redirect skipped: %s" % e)
    try:
        import pssarrays as _pa
        pssarrays = _pa
    except Exception as e:
        print("[init] pssarrays import failed: %s" % e)
    try:
        psspy.psseinit(150000)
    except Exception as e:
        print("[init] psseinit: %s" % e)
    _i, _f, _s = psspy.getdefaultint(), psspy.getdefaultreal(), psspy.getdefaultchar()


def _ts():
    return time.strftime("%H:%M:%S")


def status(msg):
    print("[%s] %s" % (_ts(), msg))
    try:
        psspy.progress("[%s] %s\n" % (_ts(), msg))
    except Exception:
        pass


def chk(ierr, what):
    code = ierr[0] if isinstance(ierr, (list, tuple)) and ierr else ierr
    print("  [%s] %-3s %s" % (_ts(), "ERR" if code not in (0, None) else "ok", what))
    return code


# ---- case helpers ----------------------------------------------------------
_BUSNAME, _BUSKV = {}, {}
_BUSAREA, _BUSZONE = {}, {}


def build_bus_index():
    global _BUSNAME, _BUSKV, _BUSAREA, _BUSZONE
    _BUSNAME, _BUSKV, _BUSAREA, _BUSZONE = {}, {}, {}, {}
    try:
        _, ni = psspy.abusint(-1, 2, ["NUMBER"])
        _, nm = psspy.abuschar(-1, 2, ["NAME"])
        _, kv = psspy.abusreal(-1, 2, ["BASE"])
        for num, name, base in zip(ni[0], nm[0], kv[0]):
            _BUSNAME[int(num)] = str(name).strip()
            _BUSKV[int(num)] = float(base or 0.0)
        print("  [busidx] %d buses indexed" % len(ni[0]))
    except Exception as e:
        print("  [busidx] failed: %s" % e)
    # AREA AND ZONE -- Appendix B (Table B-1) carries a column for each. Read
    # separately so that a build without them still gets names and kV above.
    try:
        _, az = psspy.abusint(-1, 2, ["NUMBER", "AREA", "ZONE"])
        for num, ar, zn in zip(az[0], az[1], az[2]):
            _BUSAREA[int(num)] = int(ar)
            _BUSZONE[int(num)] = int(zn)
    except Exception as e:
        print("  [busidx] area/zone unavailable (%s) -- those Appendix B columns "
              "will be blank" % e)


def bus_name(b):
    return _BUSNAME.get(int(b), "")


def bus_area(b):
    return _BUSAREA.get(int(b), "")


def bus_zone(b):
    return _BUSZONE.get(int(b), "")


def basekv(b):
    return _BUSKV.get(int(b), 0.0)


def sbase():
    try:
        s = psspy.sysmva()
        s = s[0] if isinstance(s, (list, tuple)) else s
        return float(s) if s and s > 0 else SBASE_FALLBACK
    except Exception:
        return SBASE_FALLBACK


_BR_COUNTS = {"lines": 0, "xfmr2": 0, "xfmr3": 0}


def branches():
    """(from, to) of every in-service branch AND transformer, two- and
       three-winding. abrnint(sid, owner, ties, flag, entry, string).

       THE THREE-WINDING CALL HAS ITS OWN FLAG. abrnint's FLAG 3 means "in
       service, transformers included"; atrnint's and atr3int's FLAG is only
       1 (in service) or 2 (all). Passing abrnint's 3 straight through made
       atr3int return an error that the guard below swallowed -- so no
       three-winding transformer was ever in the walk. For EastFork that cut
       MINGO 3 (115 kV) off from MINGO 7 (345 kV) one transformer above it,
       and the whole 345 kV side fell outside "5 levels of the POI". The
       counts are printed so a zero in the three-winding column is seen."""
    for owner, ties, flag in ((1, 3, 3), (1, 3, 2), (1, 1, 3), (2, 3, 3), (1, 3, 1)):
        try:
            ierr, a = psspy.abrnint(-1, owner, ties, flag, 1, ["FROMNUMBER", "TONUMBER"])
            if ierr == 0 and a and len(a) >= 2 and len(a[0]) > 0:
                out = list(zip(a[0], a[1]))
                _BR_COUNTS["lines"] = len(out)
                # transformers separately in case this flag excluded them --
                # with THEIR flag: 1 = in service, 2 = all
                _xf = 1 if flag in (1, 3) else 2
                n2 = n3 = 0
                for _t in (1, 2, 3):
                    try:
                        ie2, t = psspy.atrnint(-1, owner, _t, _xf, 1, ["FROMNUMBER", "TONUMBER"])
                        if ie2 == 0 and t and len(t) >= 2 and len(t[0]) > 0:
                            _pairs = list(zip(t[0], t[1]))
                            _have = set(out)
                            _new = [p for p in _pairs if p not in _have and (p[1], p[0]) not in _have]
                            out += _new
                            n2 = len(_pairs)
                            break
                    except Exception:
                        continue
                for _t in (1, 2, 3):
                    try:
                        ie3, t3 = psspy.atr3int(-1, owner, _t, _xf, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER"])
                        if ie3 == 0 and t3 and len(t3) >= 3 and len(t3[0]) > 0:
                            for x, y, z in zip(t3[0], t3[1], t3[2]):
                                out += [(x, y), (y, z), (x, z)]
                            n3 = len(t3[0])
                            break
                    except Exception:
                        continue
                _BR_COUNTS["xfmr2"], _BR_COUNTS["xfmr3"] = n2, n3
                print("  [branches] %d branch(es) incl. 2-winding transformers; %d 2-winding read separately; "
                      "%d THREE-winding transformer(s)%s"
                      % (len(a[0]), n2, n3,
                         "" if n3 else "  *** NONE -- if the case has any, the walk cannot cross them ***"))
                return out
        except Exception:
            continue
    return []


def hop_map(center, hops):
    """{bus: hops from center} out to `hops` levels."""
    adj = {}
    for x, y in branches():
        adj.setdefault(int(x), set()).add(int(y))
        adj.setdefault(int(y), set()).add(int(x))
    dist = {int(center): 0}
    fr = set([int(center)])
    for h in range(1, hops + 1):
        nx = set()
        for u in fr:
            for v in adj.get(u, ()):
                if v not in dist:
                    dist[v] = h
                    nx.add(v)
        fr = nx
        if not fr:
            break
    return dist


def machines_all():
    """[(bus, id, status, mbase)] for every machine in the case."""
    out = []
    try:
        _, nb = psspy.amachint(-1, 4, ["NUMBER", "STATUS"])
        _, ids = psspy.amachchar(-1, 4, ["ID"])
        _, mb = psspy.amachreal(-1, 4, ["MBASE"])
        for b, st, i, m in zip(nb[0], nb[1], ids[0], mb[0]):
            out.append((int(b), str(i).strip(), int(st), float(m or 0.0)))
    except Exception as e:
        print("  [machines] could not list machines: %s" % e)
    return out


def _is_block_bus(b):
    s = str(int(b))
    return s.startswith(NEW_GEN_BUS_PREFIX) and len(s) == int(NEW_GEN_BUS_DIGITS)


def _behind_poi(bus, poi, cap=300):
    """True when `bus` is THIS POI's new plant: the walk from it that never crosses
       the POI stays small and touches the POI.

       First the strict form -- every bus on the way in the new-plant block, the
       plant on its own MPT and tie. Then the SGF ON THE EXISTING MPTs (the _f
       study's connect = "egf_mpt"): each new feeder lands on an existing MPT low
       side, so the walk crosses the EGF's own collector / MPT buses before the
       POI. That plant pocket is still small and still ends at this POI, and no
       other project's POI is in it, so it is accepted -- said in the log."""
    if _behind_poi_strict(bus, poi, cap):
        return True
    pk = _pocket(bus, poi, cap)
    if not pk:
        return False
    if not any(int(poi) in _ADJ.get(u, ()) for u in pk):
        return False
    if any(int(q) in pk for q in _other_pois(poi)):
        return False
    _SHARED_PLANT.add(int(poi))
    return True


_SHARED_PLANT = set()     # POIs whose SGF shares the EGF's MPTs and ties (egf_mpt)


def _behind_poi_strict(bus, poi, cap=300):
    adj = _ADJ
    seen, q, touches = set([int(bus)]), [int(bus)], False
    while q:
        u = q.pop()
        for w in adj.get(u, ()):
            if w == int(poi):
                touches = True
                continue
            if w in seen:
                continue
            if not _is_block_bus(w):
                return False
            seen.add(w)
            q.append(w)
            if len(seen) > cap:
                return False
    return touches


_ADJ = {}


def _build_adj():
    global _ADJ
    _ADJ = {}
    for x, y in branches():
        _ADJ.setdefault(int(x), set()).add(int(y))
        _ADJ.setdefault(int(y), set()).add(int(x))


def _other_pois(poi):
    """Every OTHER project's POI bus, so a fallback pick cannot steal their plant."""
    out = []
    for p in (PROJECTS_SC or []):
        try:
            q = int(p.get("poi"))
        except Exception:
            continue
        if q != int(poi):
            out.append(q)
    return out


def _block_machines_near(allm, poi):
    """New-plant-block machines within NEW_GEN_MAX_HOPS of `poi` and nearer to it
       than to any other project's POI. The fallback for a plant whose path to
       the POI leaves the bus block -- see resolve_gens()."""
    try:
        mine = hop_map(int(poi), NEW_GEN_MAX_HOPS)
    except Exception as e:
        print("  [fallback] could not walk from POI %s: %s" % (poi, e))
        return []
    theirs = {}
    for q in _other_pois(poi):
        try:
            for b, h in hop_map(q, NEW_GEN_MAX_HOPS).items():
                if b not in theirs or h < theirs[b]:
                    theirs[b] = h
        except Exception:
            continue
    picked = []
    for b, i, st, m in allm:
        if not _is_block_bus(b) or b not in mine:
            continue
        if b in theirs and theirs[b] < mine[b]:
            continue                      # belongs to a nearer POI
        picked.append((b, i, st, m))
    return picked


def project_sc_defaults(name):
    """DEFAULT_SC_PARAMS with this project's SC_XPP_BY_PROJECT override applied.

       The entry may be a bare number (X'' alone) or a dict (mbase / r / xpp).
       A project's own "sc_params" is applied on top of this by resolve_gens, so
       precedence runs: per-machine sc_params > project sc_params >
       SC_XPP_BY_PROJECT > DEFAULT_SC_PARAMS."""
    base = dict(DEFAULT_SC_PARAMS or {})
    ov = (SC_XPP_BY_PROJECT or {}).get(name)
    if ov is None:
        return base, ""
    if isinstance(ov, dict):
        base.update(ov)
        src = "SC_XPP_BY_PROJECT[%r] = %s" % (name, ov)
    else:
        base["xpp"] = float(ov)
        src = "SC_XPP_BY_PROJECT[%r] -> X'' = %s" % (name, _n(float(ov)))
    return base, src


def resolve_gens(spec, sc_params=None, poi=None, defaults=None):
    """The project machines: [{"num","id","mbase0","mbase","r","xpp"}]. sc_params (per project
       or per bus) fills mbase / r / xpp for machines the spec did not give them for."""
    allm = machines_all()
    out = []

    def _sp(bus):
        base = dict(defaults if defaults is not None else (DEFAULT_SC_PARAMS or {}))
        if not sc_params:
            return base
        if all(isinstance(k, str) for k in sc_params):          # one dict for every machine
            base.update(sc_params)
            return base
        base.update(sc_params.get(bus) or sc_params.get(str(bus)) or {})
        return base
    if isinstance(spec, str) and spec.upper() == "NEW":
        others = []
        for b, i, st, m in allm:
            if not _is_block_bus(b):
                continue
            if poi is not None and not _behind_poi(b, poi):
                others.append("%d '%s'" % (b, i))
                continue
            sp = _sp(b)
            out.append({"num": b, "id": i, "mbase0": m, "mbase": sp.get("mbase"), "r": sp.get("r"), "xpp": sp.get("xpp")})
        print("  project machines (bus block %s*, behind POI %s): %s"
              % (NEW_GEN_BUS_PREFIX, poi, ["%d '%s'" % (g["num"], g["id"]) for g in out]))
        # NOTHING BEHIND THE POI, BUT BLOCK MACHINES DO EXIST IN THIS CASE.
        #
        # _behind_poi() insists that EVERY bus between the machine and the POI is
        # itself in the new-plant block. That is what keeps one project's plant
        # from being claimed by another project's POI -- but a plant whose
        # collector or GSU bus was numbered OUTSIDE the block fails it even
        # though it plainly belongs to this POI, and the project then dies with
        # "no project machines found" while its machines sit in the case.
        #
        # So fall back to distance: block machines within NEW_GEN_MAX_HOPS of
        # THIS POI, and nearer to it than to any other project's POI. Said
        # loudly, because it is a weaker test than the walk above.
        if not out and others and poi is not None:
            near = _block_machines_near(allm, poi)
            if near:
                print("  *** no machine passed the strict \"behind the POI\" walk, but %d "
                      "new-plant machine(s) sit within %d levels of POI %s and closer to it "
                      "than to any other project's POI: %s"
                      % (len(near), NEW_GEN_MAX_HOPS, poi,
                         ", ".join("%d '%s'" % (b, i) for b, i, _s, _m in near)))
                print("  *** Using them. This happens when the plant reaches the POI through a")
                print("  *** bus numbered outside the %s block (a collector or GSU bus)." % NEW_GEN_BUS_PREFIX)
                print("  *** CHECK THE LIST. If it is wrong, name the machines explicitly:")
                print("  ***     \"gens\": [{\"bus\": %s, \"id\": \"%s\"}]"
                      % (near[0][0], near[0][1]))
                for b, i, st, m in near:
                    sp = _sp(b)
                    out.append({"num": b, "id": i, "mbase0": m, "mbase": sp.get("mbase"),
                                "r": sp.get("r"), "xpp": sp.get("xpp")})
                others = [o for o in others
                          if o not in ["%d '%s'" % (b, i) for b, i, _s, _m in near]]
        if others:
            print("  other new-plant machines in this case, NOT behind this POI -- left as saved: %s" % ", ".join(others))
    else:
        have = dict(((b, i), m) for b, i, st, m in allm)
        for g in spec:
            b, i = int(g["bus"]), str(g.get("id", "1")).strip()
            if (b, i) not in have:
                print("  *** %d '%s' is not a machine in this case -- skipped ***" % (b, i))
                continue
            sp = _sp(b)
            out.append({"num": b, "id": i, "mbase0": have[(b, i)],
                        "mbase": g.get("mbase", sp.get("mbase")), "r": g.get("r", sp.get("r")),
                        "xpp": g.get("xpp", sp.get("xpp"))})
    if not out:
        raise RuntimeError("no project machines found -- check 'gens' for this project")
    return out


def _mchng(bus, mid, intgar, realar):
    """machine_chng_2 (PSS/E 34) takes INTGAR(6) = STAT, O1..O4, WMOD; the v35
       APIs take 7. Whatever length was passed, the call is retried at the other
       length when the API objects, and the value written is read back by the caller."""
    for nm in ("machine_chng_4", "machine_chng_3", "machine_chng_2"):
        fn = getattr(psspy, nm, None)
        if not fn:
            continue
        for n_int in (len(intgar), 6, 7):
            ig = list(intgar)[:n_int] + [_i] * max(0, n_int - len(intgar))
            try:
                ie = fn(bus, str(mid), ig, realar)
            except TypeError:
                continue
            return (ie[0] if isinstance(ie, (list, tuple)) else ie), nm
    raise RuntimeError("no machine_chng API accepted the call for %s '%s'" % (bus, mid))


_OK_MAC = (0, 4)      # macdat / macint: 4 = "machine off-line", the value is still returned


def _read_mbase(bus, mid):
    try:
        ierr, v = psspy.macdat(bus, str(mid), "MBASE")
        return v if ierr in _OK_MAC else None
    except Exception:
        return None


# REALAR index of MBASE / ZR / ZX per API. PSS/E 34 machine_chng_2:
#   PG QG QT QB PT PB MBASE ZR ZX RT XT GTAP F1 F2 F3 F4 WPF  -> MBASE 6, ZR 7, ZX 8
# The value is read back after every write, so a layout that does not take
# effect is caught rather than trusted.
_LAYOUTS = ({"mbase": 6, "r": 7, "xpp": 8}, {"mbase": 4, "r": 5, "xpp": 6})


def set_mbase(g, mbase, r=None, xpp=None):
    for lay in _LAYOUTS:
        realar = [_f] * 17
        realar[lay["mbase"]] = float(mbase)
        if r is not None:
            realar[lay["r"]] = float(r)
        if xpp is not None:
            realar[lay["xpp"]] = float(xpp)
        ie, nm = _mchng(g["num"], g["id"], [_i] * 7, realar)
        got = _read_mbase(g["num"], g["id"])
        if got is not None and abs(got - mbase) <= max(0.05, 0.005 * mbase):
            return got
    raise RuntimeError("could not set MBASE=%.2f for %d '%s' (read back %s)" % (mbase, g["num"], g["id"], got))


def _n(x):
    return "n/a" if x is None else ("%.5g" % x)


def _read_mac(bus, mid, what):
    """One real machine quantity ("P", "PMAX", ...), or None."""
    try:
        ie, v = psspy.macdat(int(bus), str(mid), what)
        return float(v) if ie in _OK_MAC and v is not None else None
    except Exception:
        return None


def set_pgen(g, mw):
    """Machine PGEN, in MW. PG is REALAR index 0 in BOTH machine_chng layouts,
       so unlike MBASE/ZSORCE there is no layout to guess -- but it is still
       read back, because a write that did not land must not be reported as a
       dispatch that did."""
    realar = [_f] * 17
    realar[0] = float(mw)
    _mchng(g["num"], g["id"], [_i] * 7, realar)
    return _read_mac(g["num"], g["id"], "P")


def snapshot_pgen(machines):
    """{(bus, id): PGEN} as the case holds it now, for restoring afterwards."""
    return dict(((g["num"], g["id"]), _read_mac(g["num"], g["id"], "P"))
                for g in (machines or []))


def restore_pgen(saved):
    for (b, i), mw in (saved or {}).items():
        if mw is not None:
            set_pgen({"num": b, "id": i}, mw)


def set_poi_to_gia(gens, existing, gia_mw):
    """CASE 3, SPP's scenario 2: "the EGF and SGF were dispatched PROPORTIONALLY
       to set the POI injection to NOT EXCEED the Interconnection service amount."

       Every in-service machine at this POI -- SGF and EGF alike -- is scaled by
       the SAME factor, which is what "proportionally" means: their shares of the
       injection are preserved, only the total is brought down. And only DOWN:
       "not exceed" is a ceiling, so a case already inside the limit is left
       exactly as the model holds it rather than being inflated to meet it.

       Scaling is applied to the machines' PGEN; the POI injection that results
       is then whatever the solved case gives (a little under the machine total,
       by the losses between them and the POI), which still satisfies the
       ceiling. The achieved figure is reported on the POI power sheet."""
    if not gia_mw:
        return "no gia_mw given for this project -- POI dispatch left as the model holds it"
    live = [g for g in (list(gens) + list(existing))
            if _mach_status(g["num"], g["id"]) == 1]
    if not live:
        return "no machine in service at this POI -- nothing to dispatch"
    cur = []
    for g in live:
        pg = _read_mac(g["num"], g["id"], "P")
        if pg is not None:
            cur.append((g, pg))
    if not cur:
        return "PGEN not readable -- POI dispatch left as the model holds it"
    tot = sum(x[1] for x in cur)
    lim = float(gia_mw)
    if tot <= lim + 0.05:
        return ("SGF+EGF total %.1f MW is already within the %.0f MW Interconnection "
                "Service amount -- dispatch left as the model holds it" % (tot, lim))
    scale = lim / tot if tot > 0 else 0.0
    got = 0.0
    for g, pg in cur:
        v = set_pgen(g, pg * scale)
        got += (v if v is not None else pg * scale)
    note = ("SGF+EGF scaled proportionally x%.4f: %.1f MW -> %.1f MW, to the %.0f MW "
            "Interconnection Service amount" % (scale, tot, got, lim))
    print("  [%s] POI dispatch: %s" % (_ts(), note))
    return note


def gia_check(name, powers, gia_mw, sgf_mw=None):
    """Did each case inject what it was supposed to at the POI?

       CHECKED, NOT IMPOSED. The .sav was dispatched to a POI-INJECTION target by
       the study launcher, which sees the collector losses between the machines
       and the POI; this script only sees machine PGEN, so re-dispatching here
       would quietly land a few MW low. Verifying instead keeps the case the
       authority and still catches a case that was never built to the target.

       Returns (lines, ok)."""
    L, ok = [], True
    if not (POI_GIA_CHECK and gia_mw):
        return L, ok
    lim, tol = float(gia_mw), float(POI_GIA_TOL_MW)
    for lbl, d in (powers or []):
        # Both EGF-in cases are held to the same POI MW: case 2 (EGF only,
        # "WITHOUT project") and case 3 (SGF + EGF, "WITH project 100%").
        # Case 1 (no generation) and partial-capacity cases are not.
        _u = str(lbl).upper()
        _egf_only = (lbl == _off_label())
        if not (_egf_only or _u == "WITH PROJECT 100%"):
            continue
        tot, prj, exi = d["total_mw"], d["pgen_proj"], d["pgen_exist"]
        # THE TIE FLOW IS THE MEASURE, but a build that cannot give it reports
        # 0.0 -- which would read as "984 MW below target" on a case that is
        # perfectly dispatched. Fall back to the machines' own PGEN and say so.
        _via = "at the POI"
        if tot <= 0.01 < (prj + exi):
            tot, _via = prj + exi, "at the machines (POI tie flow unavailable)"
        if tot > lim + tol:
            ok = False
            L.append("*** %s: injection %.1f MW %s EXCEEDS the %.1f MW Interconnection "
                     "Service amount by %.1f MW (SGF %.1f + EGF %.1f) ***"
                     % (lbl, tot, _via, lim, tot - lim, prj, exi))
        elif tot < lim - tol:
            if not _egf_only:
                ok = False
            L.append("%s%s: injection %.1f MW %s, %.1f MW BELOW the %.1f MW target "
                     "(SGF %.1f + EGF %.1f)%s"
                     % ("" if _egf_only else "*** ", lbl, tot, _via, lim - tot, lim, prj, exi,
                        " -- the EGF at PMAX cannot reach it" if _egf_only
                        else " -- the case was not built to the target ***"))
        else:
            L.append("%s: injection %.1f MW %s against the %.1f MW target -- OK "
                     "(SGF %.1f + EGF %.1f at the machines; any difference is collector "
                     "losses)" % (lbl, tot, _via, lim, prj, exi))
        if sgf_mw and not _egf_only:
            L.append("    SGF rated %.1f MW -> EGF remainder %.1f MW; the case has "
                     "SGF %.1f + EGF %.1f" % (float(sgf_mw), lim - float(sgf_mw), prj, exi))
    for ln in L:
        print("  [%s] %s" % (_ts(), ln))
    return L, ok


def set_poi_remainder(gens, existing, gia_mw):
    """CASE 3, the OTHER sharing rule SPP's consultants use:

        "Scenario 2 was comprised of the SGF at 100% of the assumed dispatch
         while the EGF generator picked up the remaining EGF GIA capacity."

    The SGF is left at its own dispatch and the EGF fills whatever is left of
    the GIA -- SGF first, EGF to the remainder. That is NOT the same as scaling
    both proportionally: here the SGF keeps its full output and only the EGF
    moves. Both rules appear in SPP surplus reports, so both are offered; see
    POI_DISPATCH.

    No EGF unit is pushed past its PMAX, and if the SGF alone already meets or
    exceeds the GIA the EGF goes to zero and that is said plainly."""
    if not gia_mw:
        return "no gia_mw given for this project -- POI dispatch left as the model holds it"
    lim = float(gia_mw)
    sgf = [g for g in (gens or []) if _mach_status(g["num"], g["id"]) == 1]
    egf = [g for g in (existing or []) if _mach_status(g["num"], g["id"]) == 1]
    sgf_tot = sum((_read_mac(g["num"], g["id"], "P") or 0.0) for g in sgf)
    want = max(0.0, lim - sgf_tot)
    tgt = []
    for g in egf:
        pmax = _read_mac(g["num"], g["id"], "PMAX")
        if pmax is not None:
            tgt.append((g, pmax))
    if not tgt:
        return ("SGF at %.1f MW; no existing unit with a readable PMAX -- EGF dispatch "
                "left as the model holds it" % sgf_tot)
    cap = sum(x[1] for x in tgt)
    short = ""
    if want > cap + 0.05:
        short = ("  *** the EGF cannot fill the remainder: %.1f MW left of the %.0f MW GIA "
                 "but its PMAX totals %.1f MW ***" % (want, lim, cap))
        want = cap
    scale = (want / cap) if cap > 0 else 0.0
    got = 0.0
    for g, pmax in tgt:
        v = set_pgen(g, pmax * scale)
        got += (v if v is not None else pmax * scale)
    note = ("SGF held at %.1f MW; EGF picked up the remaining %.1f MW of the %.0f MW GIA "
            "(%.0f%% of its %.1f MW PMAX) -- POI total %.1f MW"
            % (sgf_tot, got, lim, 100.0 * scale, cap, sgf_tot + got))
    if sgf_tot >= lim - 0.05:
        note = ("SGF alone is %.1f MW, at or above the %.0f MW GIA -- EGF dispatched to zero"
                % (sgf_tot, lim))
    print("  [%s] POI dispatch: %s" % (_ts(), note))
    if short:
        print("  [%s] %s" % (_ts(), short.strip()))
    return note + short


def set_egf_full(existing, gia_mw=None, mode="gia", poi=None, gens=None):
    """Dispatch the EXISTING units for the EGF-only case (case 2).

       mode "gia_poi" the EGF ALONE puts the full GIA INTO THE POI -- solved and
                    rescaled for the collector losses -- so case 2 injects the
                    SAME MW at the POI as case 3 does with SGF+EGF together.
                    Like-for-like: same injection, only WHICH machines differs.
       mode "gia"   the same, but GIA at the machine terminals (lands short at
                    the POI by the losses).
       mode "pmax"  every unit at its own PMAX, no cap.
       mode "full"  each unit to its own PMAX, scaled back only if the total
                    would exceed gia_mw.

       Units are shared in proportion to PMAX and NONE is ever pushed past its
       own PMAX -- so if the EGF cannot physically reach the target, it is
       dispatched to its ceiling and the shortfall is stated rather than papered
       over. Units out of service stay out; capacity is not invented here.

       With no gia_mw for the project the target falls back to the EGF's total
       PMAX, i.e. plain full capacity."""
    live = [g for g in (existing or []) if _mach_status(g["num"], g["id"]) == 1]
    if not live:
        return "no existing unit in service -- dispatch untouched"
    tgt = []
    for g in live:
        pmax = _read_mac(g["num"], g["id"], "PMAX")
        if pmax is not None:
            tgt.append((g, pmax))
    if not tgt:
        return "PMAX not readable -- dispatch left as the case holds it"
    cap = sum(x[1] for x in tgt)                      # the EGF's own ceiling
    _m = str(mode or "gia").strip().lower()
    want = float(gia_mw) if gia_mw else cap
    if _m.startswith("pmax"):
        want = cap                                    # every unit at its PMAX, no cap
    elif _m.startswith("full"):
        want = min(cap, want)                         # PMAX, merely capped at the GIA
    short = ""
    if want > cap + 0.05:
        short = ("  *** the EGF cannot reach it: its units total %.1f MW of PMAX, "
                 "%.1f MW short of the %.0f MW target. Dispatched to their ceiling. ***"
                 % (cap, want - cap, want))
        want = cap
    scale = (want / cap) if cap > 0 else 0.0

    def _apply(sc):
        tot = 0.0
        for g, pmax in tgt:
            v = set_pgen(g, pmax * sc)
            tot += (v if v is not None else pmax * sc)
        return tot

    got = _apply(scale)
    poi_note = ""
    # "gia_poi": THE GIA AT THE POI, NOT AT THE TERMINALS. The collector and GSU
    # losses between the machines and the POI go with I^2, so an EGF carrying
    # the whole GIA alone loses ~4x what it loses at its normal share (SantaFe
    # 18 MW instead of 4). Terminal PGEN = GIA therefore lands short at the POI.
    # Here the case is solved, the injection read at the POI, and PGEN rescaled
    # by target/measured -- three passes converge to well inside a MW. Still
    # capped at PMAX, and a project whose EGF cannot reach the GIA even at PMAX
    # (SantaFe: 998 MW PMAX -> ~980 at the POI vs 984.2) says so.
    if _m.startswith("gia_poi") and gia_mw and poi is not None:
        target = float(gia_mw)
        meas = None
        for it in range(1, 4):
            code, _n_ = solve_pf("EGF-only dispatch pass %d" % it)
            if code not in (0, None):
                poi_note = "; POI iteration stopped: power flow did not converge on pass %d" % it
                break
            pw = poi_power(poi, gens or [], existing)
            meas = pw["total_mw"]                     # project is out, so this is the EGF's injection
            if meas is None or meas <= 0.0:
                poi_note = "; POI iteration skipped: no tie flow measured at the POI"
                break
            if abs(target - meas) <= 0.5:
                break
            new_tot = got * target / meas
            if new_tot > cap + 0.05:
                short = ("  *** the EGF cannot put the %.0f MW GIA into the POI: at its full %.1f MW of PMAX "
                         "it delivers about %.1f MW there (losses %.1f MW). Dispatched to PMAX. ***"
                         % (target, cap, cap - (got - meas) * (cap / got) ** 2 if got > 0 else cap, (got - meas) * (cap / got) ** 2 if got > 0 else 0.0))
                new_tot = cap
            got = _apply(new_tot / cap if cap > 0 else 0.0)
            if new_tot >= cap - 0.05:
                break
        scale = (got / cap) if cap > 0 else 0.0
        if meas is not None and not poi_note:
            code, _n_ = solve_pf("EGF-only dispatch check")
            pw = poi_power(poi, gens or [], existing)
            meas = pw["total_mw"]
            poi_note = ("; %.1f MW at the POI against the %.0f MW GIA (%.1f MW losses)"
                        % (meas, target, got - meas))
    note = ("%d existing unit(s) -> %.1f MW total at the terminals (%.0f%% of their %.1f MW PMAX)%s%s"
            % (len(tgt), got, 100.0 * scale, cap,
               "; every unit at its PMAX (EGF_DISPATCH = pmax, no GIA cap)" if _m.startswith("pmax") else
               "; target = the %.0f MW GIA AT THE POI, so case 2 injects the same MW as case 3" % float(gia_mw)
               if (gia_mw and _m.startswith("gia_poi")) else
               ("; target = the %.0f MW GIA at the terminals" % float(gia_mw)) if (gia_mw and _m.startswith("gia"))
               else ("; capped at the %.0f MW GIA" % float(gia_mw)) if gia_mw else "",
               poi_note))
    print("  [%s] EGF dispatch: %s" % (_ts(), note))
    if short:
        print("  [%s] %s" % (_ts(), short.strip()))
    return note + short


def _read_seq_x(bus, mid):
    """The machine's POSITIVE-SEQUENCE subtransient reactance as the fault
       calculation will use it, or None if this build will not report it.

       ZSORCE IS NOT THE ONE THAT COUNTS. ASCC works from the sequence tables,
       and in a case that carries sequence data (every SPP model does) a machine
       created after that data was read has its own record -- so writing ZSORCE
       alone leaves the fault calculation using whatever that record holds. A
       machine built for a dynamic study carries a deliberately enormous source
       impedance there, which is why it can be in service, at its full MVA base,
       and still contribute nothing."""
    for fn in ("macdt2", "macdat"):
        f = getattr(psspy, fn, None)
        if not f:
            continue
        for what in ("ZPOS", "ZPOSITIVE", "XSUBTR", "ZXPPDV"):
            try:
                ie, z = f(int(bus), str(mid), what)
            except Exception:
                continue
            if ie in _OK_MAC and z is not None:
                try:
                    return float(z.imag)
                except AttributeError:
                    return float(z)
    return None


def read_sc_params(gens):
    """[(MBASE, R, X'' from ZSORCE, X'' the fault calc will use)] per machine."""
    out = []
    for g in gens:
        mb = _read_mbase(g["num"], g["id"])
        zr = zx = None
        for fn in ("macdt2", "macdat"):
            f = getattr(psspy, fn, None)
            if not f:
                continue
            try:
                ie, z = f(g["num"], str(g["id"]), "ZSORCE")
                if ie in _OK_MAC and z is not None:
                    zr, zx = float(z.real), float(z.imag)
                    break
            except Exception:
                continue
        out.append((mb, zr, zx, _read_seq_x(g["num"], g["id"])))
    return out


def set_seq_params(g, r, x):
    """Write the machine's SEQUENCE impedances too: R + jX'' as the positive-sequence
       subtransient / transient / synchronous values and the negative sequence.

       WHY THIS MATTERS. When a case carries sequence data (every SPP model does),
       ASCC takes a machine's fault source from its sequence record -- XSUBTR for
       GENXOP=0 -- NOT from the power-flow ZSORCE. A plant added to the case after
       the sequence data was read gets that record defaulted once, and changing
       ZSORCE afterwards does not update it. That is a machine whose ZSORCE reads
       0.8 pu while ASCC still sees the default: a 0.01 % change at the POI."""
    if r is None and x is None:
        return None
    rr = float(r) if r is not None else 0.0
    xx = float(x) if x is not None else None
    if xx is None:
        return None
    # seq_machine_data_4 (PSS/E 34): REALAR = ZRPOS, ZXPPDV, ZXPDV, ZXSDV, ZRNEG, ZXNEG, RZERO, XZERO, ...
    realar = [rr, xx, xx, xx, rr, xx, rr, xx]
    tried = []
    for nm, args in (("seq_machine_data_4", ([_i], realar + [_f] * 4)),
                     ("seq_machine_data_4", ([_i], realar)),
                     ("seq_machine_data_3", ([_i], realar)),
                     ("seq_machine_data_3", (realar,)),
                     ("seq_machine_data_2", (realar,))):
        f = getattr(psspy, nm, None)
        if not f:
            continue
        try:
            ie = f(g["num"], str(g["id"]), *args)
            ie = ie[0] if isinstance(ie, (list, tuple)) else ie
            if ie in (0, None):
                return nm
            tried.append("%s ierr=%s" % (nm, ie))
        except TypeError as e:
            tried.append("%s: %s" % (nm, str(e)[:60]))
    print("  [%s] *** sequence impedance NOT written for %d '%s' (%s) -- if the case holds sequence data, "
          "ASCC will not see the X'' set above ***" % (_ts(), g["num"], g["id"], "; ".join(tried) or "no API"))
    return None


def apply_sc_params(gens):
    if not APPLY_SC_PARAMS:
        return
    for g in gens:
        if g.get("mbase") is None and g.get("r") is None and g.get("xpp") is None:
            continue
        mb = g["mbase"] if g.get("mbase") is not None else g["mbase0"]
        got = set_mbase(g, mb, g.get("r"), g.get("xpp"))
        g["mbase0"] = got
        via = set_seq_params(g, g.get("r"), g.get("xpp"))
        print("  [%s] SC params %d '%s': MBASE=%.3f R=%s X''=%s (ZSORCE%s)"
              % (_ts(), g["num"], g["id"], got, g.get("r"), g.get("xpp"),
                 (" + sequence data via %s" % via) if via else ""))


def _egf_xpp_for(name, bus):
    """EGF_XPP_BY_PROJECT: the X'' (pu on the unit's own MVA base) to write on
       existing unit `bus` of project `name`, or None = as the case holds it."""
    ov = (EGF_XPP_BY_PROJECT or {}).get(name)
    if isinstance(ov, dict):
        ov = ov.get(int(bus), ov.get(str(bus)))
    return None if ov is None else float(ov)


def _read_zsorce(bus, mid):
    """(R, X) of the machine record's source impedance -- the Power Flow tab's
       R Source / X Source -- or (None, None)."""
    for fn in ("macdt2", "macdat"):
        f = getattr(psspy, fn, None)
        if not f:
            continue
        try:
            ie, z = f(int(bus), str(mid), "ZSORCE")
            if ie in _OK_MAC and z is not None:
                return float(z.real), float(z.imag)
        except Exception:
            continue
    return None, None


def apply_egf_xpp(name, existing):
    """EGF_XPP_BY_PROJECT on this project's existing units, then every existing
       unit as the fault calculation will see it, written or not:
       [(bus, id, MBASE, R Source, X Source, X'' used by ASCC, note)].

       What is written: the Subtransient X of the short-circuit (sequence) data --
       the reactance ASCC uses with GENXOP = 0 (with GENXOP = 1 the Transient X as
       well) -- and the X Source of the power-flow record, so both tabs read the
       same. Every other value goes in as the API default, which leaves it as the
       case holds it: R, MVA base, the other sequence reactances. Both are read
       back before anything is reported."""
    out = []
    for g in existing:
        x = _egf_xpp_for(name, g["num"])
        tried = []
        if x is not None:
            # power-flow record: X Source only (PSS/E 34 machine_chng_2 REALAR: ZX at 8,
            # the layout the SGF's X'' goes in with)
            realar = [_f] * 17
            realar[_LAYOUTS[0]["xpp"]] = x
            try:
                _mchng(g["num"], g["id"], [_i] * 7, realar)
            except RuntimeError as e:
                tried.append("X Source: %s" % e)
            # short-circuit data: the positive-sequence reactance(s) ASCC reads
            seq = [_f] * 8
            seq[1] = x                    # ZXPPDV  subtransient
            if GENXOP == 1:
                seq[2] = x                # ZXPDV   transient
            for nm, args in (("seq_machine_data_4", ([_i], seq + [_f] * 4)),
                             ("seq_machine_data_4", ([_i], seq)),
                             ("seq_machine_data_3", ([_i], seq)),
                             ("seq_machine_data_3", (seq,)),
                             ("seq_machine_data_2", (seq,))):
                f = getattr(psspy, nm, None)
                if not f:
                    continue
                try:
                    ie = f(g["num"], str(g["id"]), *args)
                    ie = ie[0] if isinstance(ie, (list, tuple)) else ie
                    if ie in (0, None):
                        break
                    tried.append("%s ierr=%s" % (nm, ie))
                except TypeError as e:
                    tried.append("%s: %s" % (nm, str(e)[:60]))
        mb = _read_mbase(g["num"], g["id"])
        zr, zx = _read_zsorce(g["num"], g["id"])
        sq = _read_seq_x(g["num"], g["id"])
        note = "existing unit (EGF): as in case"
        if x is not None:
            took = sq is not None and abs(sq - x) <= max(0.0005, 0.001 * x)
            if took:
                note = "existing unit (EGF): X'' %s written (EGF_XPP_BY_PROJECT)" % _n(x)
            else:
                note = ("existing unit (EGF): X'' %s asked (EGF_XPP_BY_PROJECT) but the fault calculation "
                        "still uses %s -- NOT taken" % (_n(x), _n(sq)))
            print("  [%s] EGF X'' %d '%s': %s pu -> X Source %s, X'' used by the fault calc %s%s"
                  % (_ts(), g["num"], g["id"], _n(x), _n(zx), _n(sq),
                     "" if took else "   *** NOT taken (%s) -- studied as the case holds it ***"
                     % ("; ".join(tried) or "no sequence-data API")))
        out.append((g["num"], g["id"], mb, zr, zx, sq, note))
    _EGF_PARAMS[name] = out
    return out


def _off_label():
    """What the WITHOUT case is called, everywhere it is named.

       One function so that the report, the summary and the power lookup cannot
       disagree about which case the numbers came from."""
    return ("WITHOUT project and existing units"
            if (WITHOUT_SCOPE or "new").lower() == "site" else "WITHOUT project")


def set_status(gens, on):
    for g in gens:
        intgar = [_i] * 7
        intgar[0] = 1 if on else 0
        ie, nm = _mchng(g["num"], g["id"], intgar, [_f] * 17)
        chk(ie, "machine %d '%s' -> %s" % (g["num"], g["id"], "IN" if on else "OUT"))


def restore_status(machines):
    """Put each machine back to the status the case was SAVED with.

       "site" turns the existing units off for the WITHOUT case, and they must
       come back exactly as they were -- a unit that was saved out of service is
       not put in service by a short-circuit study."""
    for g in machines:
        want = 1 if int(g.get("st0", 1)) == 1 else 0
        intgar = [_i] * 7
        intgar[0] = want
        ie, nm = _mchng(g["num"], g["id"], intgar, [_f] * 17)
        chk(ie, "machine %d '%s' -> %s (as saved)" % (g["num"], g["id"], "IN" if want else "OUT"))


def set_capacity(gens, pct, existing=None):
    """Put the case at pct % capacity, per CAPACITY_SCOPE. Returns a note for the report."""
    existing = existing or []
    scope = (CAPACITY_SCOPE or "new").lower()
    if scope == "both":
        for g in list(gens) + list(existing):
            got = set_mbase(g, g["mbase0"] * pct / 100.0)
            print("  [%s] %d '%s' MBASE %.2f -> %.2f (%d%%)" % (_ts(), g["num"], g["id"], g["mbase0"], got, pct))
        return "project and existing units each at %d%% of MVA base" % pct
    if scope == "site":
        ex = sum(g["mbase0"] for g in existing)
        nw = sum(g["mbase0"] for g in gens)
        tgt_new = max(0.0, (ex + nw) * pct / 100.0 - ex)
        f = (tgt_new / nw) if nw > 0 else 0.0
        for g in gens:
            got = set_mbase(g, max(0.01, g["mbase0"] * f))
            print("  [%s] %d '%s' MBASE %.2f -> %.2f (site %d%%: project at %.1f%%)" % (_ts(), g["num"], g["id"], g["mbase0"], got, pct, f * 100.0))
        return ("site total %d%% of %.0f MVA: existing %.0f MVA kept, project at %.1f%% (%.0f MVA)"
                % (pct, ex + nw, ex, f * 100.0, tgt_new))
    for g in gens:
        got = set_mbase(g, g["mbase0"] * pct / 100.0)
        print("  [%s] %d '%s' MBASE %.2f -> %.2f (%d%%)" % (_ts(), g["num"], g["id"], g["mbase0"], got, pct))
    return "project machines at %d%% of MVA base; existing units as saved" % pct


def _solved_code():
    """psspy.solved(): 0 = converged. None when the build will not say."""
    try:
        s = psspy.solved()
        return s[0] if isinstance(s, (list, tuple)) else s
    except Exception:
        return None


def solve_pf(tag=""):
    """Solve the power flow and SAY WHETHER IT CONVERGED. Returns (code, note);
       code 0 = converged, anything else = did not.

       Tries the decoupled solver first (robust on an ill-conditioned case) then
       full Newton to tighten it, repeating up to SOLVE_MAX_TRIES -- successive
       solves often pull in a case that one pass leaves short. The result is
       returned rather than printed and forgotten, because whether the case
       converged decides what the numbers downstream are worth: see
       _convergence_gate()."""
    if not SOLVE_PF:
        return None, "not attempted (SOLVE_PF = False)"
    seq = []
    for _try in range(max(1, int(SOLVE_MAX_TRIES))):
        for nm, opts in (("fdns", [0, 0, 0, 1, 1, 0, 0, 0]),
                         ("fnsl", [0, 0, 0, 1, 1, 0, 0, 0])):
            fn = getattr(psspy, nm, None)
            if not fn:
                continue
            try:
                fn(opts)
            except Exception as e:
                seq.append("%s raised %s" % (nm, str(e)[:40]))
                continue
            s = _solved_code()
            seq.append("%s=%s" % (nm, s))
            if s in (0, None):
                print("  [%s] power flow CONVERGED%s (%s)"
                      % (_ts(), (" -- " + tag) if tag else "", ", ".join(seq)))
                return 0, "converged (%s)" % ", ".join(seq)
    code = _solved_code()
    print("  [%s] *** POWER FLOW DID NOT CONVERGE%s (%s) ***"
          % (_ts(), (" -- " + tag) if tag else "", ", ".join(seq)))
    return (code if code is not None else 1), "NOT CONVERGED (%s)" % ", ".join(seq)


def _convergence_gate(name, cases):
    """Decide what a non-converged case means for THIS study, and act on it.

       `cases` is [(label, code, note)]. The rule is not "always abort": with
       VOLTOP = 0 the fault calculation uses a flat 1.0 pu prefault and the
       Thevenin impedance of the network, so it does NOT read the power-flow
       solution at all -- the kA stand whether or not the case converged. What
       does depend on it is the POI MW the report quotes, and with VOLTOP = 1
       the prefault voltages, and so every current.

       So: fatal when the currents depend on it, flagged when only the MW do.
       REQUIRE_CONVERGENCE = "always" / "never" overrides."""
    bad = [(l, c, n) for l, c, n in cases if c not in (0, None)]
    if not bad:
        return True, ""
    pol = str(REQUIRE_CONVERGENCE or "auto").strip().lower()
    fatal = (pol == "always") or (pol == "auto" and VOLTOP != 0)
    head = "%d of %d case(s) did not converge: %s" % (
        len(bad), len(cases), "; ".join("%s (%s)" % (l, c) for l, c, _n in bad))
    print("")
    print("  " + "*" * 70)
    print("  *** %s: POWER FLOW NOT CONVERGED ***" % name.upper())
    print("  *** %s" % head)
    print("  ***")
    if VOLTOP == 0:
        print("  *** VOLTOP = 0, so the FAULT CURRENTS do not use the power-flow")
        print("  *** solution: they come from the flat %.2f pu prefault and the" % PREFAULT_VPU)
        print("  *** network's Thevenin impedance. The kA are unaffected.")
        print("  *** What IS affected is the POI power (MW/MVAr) this report")
        print("  *** quotes -- those are read off the unconverged case.")
    else:
        print("  *** VOLTOP = %d, so the prefault voltages -- and therefore EVERY" % VOLTOP)
        print("  *** fault current -- are taken from this solution. The results")
        print("  *** would be built on a case that never solved.")
    print("  *** REQUIRE_CONVERGENCE = %r -> %s"
          % (REQUIRE_CONVERGENCE, "ABORTING this project" if fatal
             else "continuing, with every affected number flagged"))
    print("  " + "*" * 70)
    print("")
    if fatal:
        raise RuntimeError("power flow did not converge -- %s" % head)
    return False, head


_ENERGIZED_EGF = []     # keep_online units EGF_ENERGIZE switched in for the running project
_EGF_PARAMS = {}         # project -> apply_egf_xpp() rows: every existing unit as ASCC sees it


_PROJECT_GENS = []      # the running project's machines, so an abort can switch them back in


def _restore_energized():
    """Put back OUT of service every unit EGF_ENERGIZE switched in, and back IN
       every project machine a case had switched out -- called at the end of a
       project AND from the failure path, so an aborted project leaves the
       session's case as it was loaded."""
    if _PROJECT_GENS:
        gens = list(_PROJECT_GENS)
        del _PROJECT_GENS[:]
        try:
            _out = [g for g in gens if _mach_status(g["num"], g["id"]) == 0]
            if _out:
                set_status(_out, True)
                print("  [%s] %d project machine(s) switched back IN after the abort" % (_ts(), len(_out)))
        except Exception as e:
            print("  *** could not switch the project machines back in: %s ***" % e)
    if not _ENERGIZED_EGF:
        return
    units = list(_ENERGIZED_EGF)
    del _ENERGIZED_EGF[:]
    for g in units:
        g["st0"] = 0
    try:
        restore_status(units)
        print("  [%s] EGF_ENERGIZE: %d unit(s) put back OUT of service as saved" % (_ts(), len(units)))
    except Exception as e:
        print("  *** EGF_ENERGIZE: could not put %d unit(s) back out of service: %s ***" % (len(units), e))


def _gate_now(name, conv):
    """Stop at the FIRST unconverged case when the policy is fatal, instead of
       sweeping every remaining case on a solution that never happened and only
       then refusing the lot. With VOLTOP = 1 the very next ASCC would be built
       on that bad solution, so this runs right after each solve."""
    lbl, code, _note = conv[-1]
    if code in (0, None):
        return
    pol = str(REQUIRE_CONVERGENCE or "auto").strip().lower()
    if (pol == "always") or (pol == "auto" and VOLTOP != 0):
        _convergence_gate(name, conv)      # prints the banner and raises


# ---- POI POWER (from the solved power flow of each case) ---------------------
def _pocket(bus, poi, cap=300):
    """Buses reachable from `bus` without crossing the POI, or None if that walk
       reaches the wide system (more than `cap` buses)."""
    seen, q = set([int(bus)]), [int(bus)]
    while q:
        u = q.pop()
        for w in _ADJ.get(u, ()):
            if w == int(poi) or w in seen:
                continue
            seen.add(w)
            q.append(w)
            if len(seen) > cap:
                return None
    return seen


def poi_power(poi, gens, existing):
    """{"proj_mw","proj_mvar","exist_mw","exist_mvar","total_mw","total_mvar","pgen_proj","pgen_exist"}
       MW/MVAr flowing INTO the POI over the project ties and the existing units'
       ties, plus the machines' own PGEN, all from the case as solved."""
    poi = int(poi)
    side_p, side_e = set(), set()
    for g in gens:
        s = _pocket(g["num"], poi)
        if s:
            side_p |= s
    for g in existing:
        s = _pocket(g["num"], poi)
        if s:
            side_e |= s
    side_e -= side_p
    out = {"proj_mw": 0.0, "proj_mvar": 0.0, "exist_mw": 0.0, "exist_mvar": 0.0, "pgen_proj": 0.0, "pgen_exist": 0.0}
    seen = set()
    # READ AT THE POI END: what ARRIVES at the POI. Read at the plant end a line
    # tie counts its own losses as delivered (EmpirePrairie's gen-tie sends
    # 603.9 MW and lands 597.9 MW at 761383). Three-winding ties (EastFork) are
    # read with wnddt2 at the POI winding -- brnflo cannot read them at all.
    ties = [(x, y, ck, None) for x, y, ck in _all_branches_ck()] + list(_three_wind_ties())
    for x, y, ck, third in ties:
        other = y if x == poi else (x if y == poi else None)
        # ONE THREE-WINDING TRANSFORMER IS ONE TIE: its POI-winding flow is the
        # same whichever of the other two windings names it (EastFork's MPT is
        # listed via 531622 and via its tertiary 531624).
        _key = (other, ck) if third is None else (frozenset((int(x), int(y), int(third))), ck)
        if other is None or _key in seen:
            continue
        seen.add(_key)
        grp = "proj" if other in side_p else ("exist" if other in side_e else None)
        if grp is None:
            continue
        try:
            if third is None:
                ie, s = psspy.brnflo(poi, other, str(ck))  # MVA LEAVING the POI toward the plant
            else:
                ie, s = psspy.wnddt2(poi, other, int(third), str(ck), "FLOW")
            if ie == 0 and s is not None:
                out[grp + "_mw"] -= float(s.real)          # into the POI = minus what leaves it
                out[grp + "_mvar"] -= float(s.imag)
                out.setdefault("ties", []).append((int(other), str(ck), -float(s.real), -float(s.imag),
                                                   third is not None))
        except Exception:
            pass
    for key, lst in (("pgen_proj", gens), ("pgen_exist", existing)):
        for g in lst:
            try:
                ie, pg = psspy.macdat(g["num"], str(g["id"]), "P")
                if ie == 0 and pg is not None:               # off-line (ierr 4) contributes nothing
                    out[key] += float(pg)
            except Exception:
                pass
    out["total_mw"] = out["proj_mw"] + out["exist_mw"]
    out["total_mvar"] = out["proj_mvar"] + out["exist_mvar"]
    # SGF ON THE EXISTING MPTs: the ties carry the EGF and the SGF together, so
    # the walk files them all under the project. The POI total is exact; the
    # split between the two is given in proportion to the machines' own PGEN.
    out["split_how"] = "each plant on its own ties (measured)"
    if poi in _SHARED_PLANT:
        out["shared_ties"] = True
        _pp, _pe = out["pgen_proj"], out["pgen_exist"]
        if _pp + _pe <= 0.01:
            # nothing generating: what the ties carry is line / cable charging
            out["proj_mw"] = out["proj_mvar"] = 0.0
            out["exist_mw"] = out["exist_mvar"] = 0.0
            out["split_how"] = ("no generation in service -- the shared ties carry only the "
                                "plant's charging / losses (TOTAL column)")
        elif _pp <= 0.01:
            out["proj_mw"] = out["proj_mvar"] = 0.0
            out["exist_mw"], out["exist_mvar"] = out["total_mw"], out["total_mvar"]
            out["split_how"] = "SGF out of service -- everything on the shared ties is the EGF's"
        elif _pe <= 0.01:
            out["exist_mw"] = out["exist_mvar"] = 0.0
            out["proj_mw"], out["proj_mvar"] = out["total_mw"], out["total_mvar"]
            out["split_how"] = "EGF out of service -- everything on the shared ties is the SGF's"
        else:
            # THE SAME RULE AS THE DYNAMIC STUDY: the SGF's share at the POI is what
            # it puts into the existing MPT low-side buses, less its pro-rata part
            # of the shared MPT + tie losses (SGF into MPTs x total / MPT intake).
            sgf_in, mpt_in = _sgf_mpt_flows(gens, poi)
            if sgf_in and mpt_in and mpt_in > 1.0 and 0.8 <= out["total_mw"] / mpt_in <= 1.0 + 1e-6:
                _f = max(0.0, min(1.0, sgf_in / mpt_in))
                out["sgf_mpt_mw"], out["mpt_in_mw"] = sgf_in, mpt_in
                out["split_how"] = ("shared ties (SGF on the EGF's MPTs): SGF %.1f MW into the MPT low-side "
                                    "buses of %.1f MW total intake; MPT + tie losses shared pro rata"
                                    % (sgf_in, mpt_in))
            else:
                _f = _pp / (_pp + _pe)
                out["split_how"] = ("shared ties (SGF on the EGF's MPTs): split pro rata to PGEN "
                                    "(MPT intake could not be read)")
            out["proj_mw"], out["exist_mw"] = out["total_mw"] * _f, out["total_mw"] * (1.0 - _f)
            out["proj_mvar"], out["exist_mvar"] = out["total_mvar"] * _f, out["total_mvar"] * (1.0 - _f)
    out["losses_mw"] = (out["pgen_proj"] + out["pgen_exist"]) - out["total_mw"]
    return out


def _sgf_mpt_flows(gens, poi):
    """(SGF MW into the existing MPT low-side buses, total MW into those MPTs).
       The SGF's edge into the existing plant is a branch from a new-plant-block
       bus to a non-block bus in the plant pocket; the MPT intake is every
       transformer from those buses up to a higher voltage (>= 1.8 x kV)."""
    pk = set()
    for g in gens or []:
        s_ = _pocket(g["num"], poi)
        if s_:
            pk |= s_
    attach, sgf_in = set(), 0.0
    for u in pk:
        if not _is_block_bus(u):
            continue
        for w in _ADJ.get(u, ()):
            if w in pk and not _is_block_bus(w):
                try:
                    ie, f = psspy.brnflo(int(u), int(w), "1")
                    if ie == 0 and f is not None:
                        sgf_in += float(f.real)
                        attach.add(int(w))
                except Exception:
                    pass
    if not attach:
        return None, None
    mpt_in = 0.0
    for x, y, ck, third in [(a, b, k, None) for a, b, k in _all_branches_ck()] + list(_three_wind_ties()):
        for a, b in ((x, y), (y, x)):
            if a not in attach:
                continue
            try:
                ka, kb = basekv(a) or 0.0, basekv(b) or 0.0
            except Exception:
                continue
            if not (ka > 0 and kb >= 1.8 * ka):
                continue
            try:
                if third is None:
                    ie, f = psspy.brnflo(int(a), int(b), str(ck))
                else:
                    ie, f = psspy.wnddt2(int(a), int(b), int(third), str(ck), "FLOW")
                if ie == 0 and f is not None:
                    mpt_in += float(f.real)
            except Exception:
                pass
    return sgf_in, mpt_in


def _three_wind_ties():
    """(a, b, ckt, third) for every pair of windings of every in-service
       three-winding transformer -- both directions are matched by the caller."""
    out = []
    for _t in (1, 2, 3):
        try:
            ie, t3 = psspy.atr3int(-1, 1, _t, 1, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER"])
            je, c3 = psspy.atr3char(-1, 1, _t, 1, 1, ["ID"])
            if ie == 0 and t3 and len(t3) >= 3 and len(t3[0]) > 0:
                cks = c3[0] if (je == 0 and c3 and c3[0]) else ["1"] * len(t3[0])
                for x, y, z, k in zip(t3[0], t3[1], t3[2], cks):
                    x, y, z, k = int(x), int(y), int(z), str(k).strip()
                    out += [(x, y, k, z), (y, z, k, x), (x, z, k, y)]
                return out
        except Exception:
            continue
    return out


_BR_CK = None


def _all_branches_ck():
    """(from, to, ckt) for every branch and transformer, once per case."""
    global _BR_CK
    if _BR_CK is not None:
        return _BR_CK
    out = []
    for owner, ties, flag in ((1, 3, 3), (1, 3, 2), (1, 1, 3)):
        try:
            ie, a = psspy.abrnint(-1, owner, ties, flag, 1, ["FROMNUMBER", "TONUMBER"])
            je, c = psspy.abrnchar(-1, owner, ties, flag, 1, ["ID"])
            if ie == 0 and a and a[0]:
                cks = c[0] if (je == 0 and c and c[0]) else ["1"] * len(a[0])
                out += [(int(x), int(y), str(k).strip()) for x, y, k in zip(a[0], a[1], cks)]
                try:
                    ie2, tt = psspy.atrnint(-1, owner, ties, flag, 1, ["FROMNUMBER", "TONUMBER"])
                    je2, tc = psspy.atrnchar(-1, owner, ties, flag, 1, ["ID"])
                    if ie2 == 0 and tt and tt[0]:
                        tks = tc[0] if (je2 == 0 and tc and tc[0]) else ["1"] * len(tt[0])
                        out += [(int(x), int(y), str(k).strip()) for x, y, k in zip(tt[0], tt[1], tks)]
                except Exception:
                    pass
                break
        except Exception:
            continue
    _BR_CK = out
    return out


# ---- ASCC ------------------------------------------------------------------
def ascc_currents(sid, voltop=None):
    if pssarrays is None:
        raise RuntimeError("pssarrays is not available -- ASCC cannot be run from Python")
    kw = dict(flt3ph=1, fltlg=0, fltllg=0, fltll=0,
              voltop=(VOLTOP if voltop is None else int(voltop)), genxop=GENXOP)
    kw.update(ASCC_EXTRA_OPTS or {})
    tried = []
    for call in (lambda: pssarrays.ascc_currents(sid=sid, all=0, **kw),
                 lambda: pssarrays.ascc_currents(sid, 0, **kw),
                 lambda: pssarrays.ascc_currents(sid=sid, busall=0, **kw),
                 lambda: pssarrays.ascc_currents(sid, 0, 1, **kw)):
        try:
            return call()
        except TypeError as e:
            tried.append(str(e))
    raise RuntimeError("pssarrays.ascc_currents signature not matched: %s" % "; ".join(tried))


def _z1(rlst, i):
    z = rlst.thevzpu[i].z1
    try:
        return complex(z.real, z.imag)
    except Exception:
        return complex(z)


def _reported_ipu(rlst, i):
    """|Ia1| for the 3-phase fault as the module reports it, in pu on Sbase
       (the GUI's "Sym I''k rms" column), or None when the build has no such
       attribute. A build that returns amps is recognised by size."""
    try:
        ia = rlst.flt3ph[i].ia1
        mag = abs(complex(ia.real, ia.imag))
        return mag if mag < 1000.0 else None   # amps, not pu -> handled by the caller
    except Exception:
        return None


def _reported_ia1(rlst, i, ib):
    """Ia1 as a complex in pu on Sbase (the console's RE(I)/IM(I)), or None."""
    try:
        ia = rlst.flt3ph[i].ia1
        c = complex(ia.real, ia.imag)
        return c if abs(c) < 1000.0 else c / (ib * 1000.0)   # amps -> kA -> pu
    except Exception:
        return None


def _reported_ka(rlst, i, kv, sb):
    """The 3-phase current the module reports, in kA, when the attribute exists."""
    try:
        ia = rlst.flt3ph[i].ia1
        mag = abs(complex(ia.real, ia.imag))
        return mag * sb / (SQRT3 * kv) if mag < 1000.0 else mag / 1000.0   # pu on Sbase, or amps
    except Exception:
        return None


def solved_vpu(b):
    """The bus's solved voltage, pu, or None."""
    try:
        ie, v = psspy.busdat(int(b), "PU")
        return float(v) if ie == 0 and v else None
    except Exception:
        return None


# HOW "FROM POWER FLOW" IS ACTUALLY OBTAINED. The dialog's prefault presets and
# the pssarrays `voltop` codes are NOT the same numbering: on PSS/E 34.8
# voltop=1 drove the POI from 1.092 pu -- a voltage no bus in the case is at --
# and gave 15.58 kA where the GUI's "From power flow" gives 14.34. So the code
# is never assumed. Once per project the candidates are tried at the faulted
# buses and each one's implied prefault voltage (|Ia1| x |Z1|) is compared with
# the SOLVED bus voltage from busdat: the code that reproduces it is the GUI's
# "From power flow" and its Ia1 is used. If none does, the current is built as
# V_solved / |Z1| -- the same physics, missing only the loaded-machine EMF term
# (0.05-0.2 % in the GUI checks) -- and the log says so.
ASCC_VOLTOP_CANDIDATES = (0, 1, 2, 3)
ASCC_VOLTOP_CODE = None          # force a code (skip the probe) -- only if you have verified it
PROBE_TOL = 0.003                # 0.3 %: a code must match the solved voltage this closely
_ASCC_MODE = {"how": None, "code": None, "note": ""}


def _reset_ascc_mode():
    _ASCC_MODE.update({"how": None, "code": None, "note": ""})


def _probe_voltop(sid, buslist):
    """Find which voltop code means "From power flow"; sets _ASCC_MODE."""
    vs = dict((b, solved_vpu(b)) for b in buslist)
    if not any(vs.values()):
        _ASCC_MODE.update({"how": "thev_v", "code": None,
                           "note": "busdat gave no solved voltages -- cannot probe; using V_solved/|Z1| (flat where V unknown)"})
        print("  *** %s ***" % _ASCC_MODE["note"])
        return
    if ASCC_VOLTOP_CODE is not None:
        _ASCC_MODE.update({"how": "ia1", "code": int(ASCC_VOLTOP_CODE),
                           "note": "ASCC_VOLTOP_CODE = %d forced (not probed)" % int(ASCC_VOLTOP_CODE)})
        print("  [%s] prefault: %s" % (_ts(), _ASCC_MODE["note"]))
        return
    rows, best = [], None
    for code in ASCC_VOLTOP_CANDIDATES:
        try:
            rlst = ascc_currents(sid, code)
        except Exception as e:
            rows.append((code, None, None, "failed: %s" % str(e)[:50])); continue
        errs, vpfs = [], []
        for i in range(len(rlst.fltbus)):
            b = int(rlst.fltbus[i])
            z1 = _z1(rlst, i); ipu = _reported_ipu(rlst, i)
            if ipu is None or abs(z1) <= 0 or not vs.get(b):
                continue
            vpf = ipu * abs(z1)
            vpfs.append(vpf); errs.append(abs(vpf - vs[b]) / vs[b])
        if not errs:
            rows.append((code, None, None, "no Ia1 / no solved V to compare")); continue
        e = sum(errs) / len(errs); vm = sum(vpfs) / len(vpfs)
        rows.append((code, vm, e, ""))
        if best is None or e < best[1]:
            best = (code, e)
    vpoi = vs.get(buslist[0]) or 0.0
    print("  [%s] prefault-voltage probe at %d bus(es) (solved V at first bus %.4f pu):" % (_ts(), len(vs), vpoi))
    for code, vm, e, msg in rows:
        print("      voltop=%d  %s" % (code, msg or ("implied prefault V %.4f pu, %.2f%% from solved" % (vm, 100 * e))))
    if best and best[1] <= PROBE_TOL:
        _ASCC_MODE.update({"how": "ia1", "code": best[0],
                           "note": "voltop=%d reproduces the solved voltages (%.2f%%): Ia1 used, as the GUI's \"From power flow\""
                                   % (best[0], 100 * best[1])})
    else:
        _ASCC_MODE.update({"how": "thev_v", "code": None,
                           "note": "NO voltop code reproduces the solved voltages (best %s) -- using V_solved/|Z1|"
                                   % (("voltop=%d at %.2f%%" % (best[0], 100 * best[1])) if best else "none")})
    print("  [%s] prefault: %s" % (_ts(), _ASCC_MODE["note"]))


def _save_case(name, tag, label, gens, existing, poi):
    """Save the case as it stands, solved, into CASES_DIR. Returns the path or None."""
    if not SAVE_CASES:
        return None
    d = CASES_DIR or os.path.join(RESULTS_DIR, "cases")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    path = os.path.join(d, "%s_%s.sav" % (name, tag))
    try:
        ie = psspy.save(path)
        ok = (ie[0] if isinstance(ie, (list, tuple)) else ie) in (0, None)
    except Exception as e:
        ok = False
        print("  *** %s: could not save %s (%s) ***" % (name, path, e))
    if ok:
        print("  [%s] CASE SAVED: %s   (%s)" % (_ts(), path, label))
        _SAVED_CASES.append((name, tag, label, path))
        return path
    print("  *** %s: psspy.save refused %s ***" % (name, path))
    return None


_SAVED_CASES = []


def _write_cases_txt(name, poi, snaps, powers, params, gens):
    d = CASES_DIR or os.path.join(RESULTS_DIR, "cases")
    L = ["SHORT-CIRCUIT CASES -- %s   (POI %d)" % (name, poi), "=" * 78,
         "SGF short-circuit model written on every case (pu on the machine MVA base):"]
    for g, rec in zip(gens, params or []):
        L.append("  %d '%s'  MBASE %s  R %s  X'' (machine record) %s  X'' (sequence record, used by ASCC) %s"
                 % (g["num"], g["id"], _n(rec[0]), _n(rec[1]), _n(rec[2]),
                    _n(rec[3] if len(rec) > 3 else None)))
    for _rec in (_EGF_PARAMS.get(name) or []):
        L.append("  existing %d '%s'  MBASE %s  R %s  X'' (machine record) %s  X'' (sequence record, used by "
                 "ASCC) %s  -- %s" % (_rec[0], _rec[1], _n(_rec[2]), _n(_rec[3]), _n(_rec[4]), _n(_rec[5]),
                                     _rec[6].replace("existing unit (EGF): ", "")))
    L.append("")
    pw = dict(powers or [])
    for (lbl, sgf_on, sgf_off, egf_on, egf_off, sgf_mw, egf_mw) in _onoff_by_case(snaps):
        f = [x for x in _SAVED_CASES if x[0] == name and x[2] == lbl]
        L.append("%s" % lbl)
        L.append("  file   : %s" % (f[0][3] if f else "(not saved)"))
        L.append("  PGEN   : SGF %.1f MW, EGF %.1f MW, total %.1f MW" % (sgf_mw, egf_mw, sgf_mw + egf_mw))
        dd = pw.get(lbl)
        if dd:
            L.append("  POI    : %.1f MW / %.1f MVAr into POI %d (SGF %.1f MW, EGF %.1f MW)"
                     % (dd["total_mw"], dd["total_mvar"], poi, dd["proj_mw"], dd["exist_mw"]))
            L.append("  ties   : %s" % _ties_text(dd))
        L.append("  SGF ON : %s" % (", ".join(sgf_on) or "none"))
        L.append("  SGF OFF: %s" % (", ".join(sgf_off) or "none"))
        L.append("  EGF ON : %s" % (", ".join(egf_on) or "none"))
        L.append("  EGF OFF: %s" % (", ".join(egf_off) or "none"))
        L.append("")
    try:
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "CASES_%s.txt" % name), "w") as fh:
            fh.write("\n".join(L) + "\n")
        print("  [%s] -> %s" % (_ts(), os.path.join(d, "CASES_%s.txt" % name)))
    except Exception as e:
        print("  *** could not write CASES_%s.txt (%s) ***" % (name, e))


def run_ascc(buslist, label):
    if CASES_ONLY:
        return {}
    """Fault every bus in `buslist`; {bus: {kv, kA, xr, z1, vpf, kA_thev}}.

       WHICH CURRENT IS "THE" CURRENT.
         VOLTOP = 0   kA = PREFAULT_VPU / |Z1|          (FLAT classical)
         VOLTOP = 1   "From power flow", obtained either as the module's own Ia1
                      under the voltop code that _probe_voltop() verified, or as
                      V_solved(bus) / |Z1| when no code checks out.
       vpf is the prefault voltage the current was driven by, kept per bus so a
       reader can tie the kA to the one-line's bus voltage."""
    status("ASCC 3-phase sweep [%s] over %d bus(es)" % (label, len(buslist)))
    sid = 1
    psspy.bsys(sid, 0, [0.0, 0.0], 0, [], len(buslist), list(buslist), 0, [], 0, [])
    if VOLTOP != 0 and _ASCC_MODE["how"] is None:
        _probe_voltop(sid, list(buslist))
    how = _ASCC_MODE["how"] if VOLTOP != 0 else "flat"
    rlst = ascc_currents(sid, _ASCC_MODE["code"] if how == "ia1" else 0)
    sb = sbase()
    out = {}
    for i in range(len(rlst.fltbus)):
        b = int(rlst.fltbus[i])
        kv = basekv(b)
        if kv <= 0:
            continue
        z1 = _z1(rlst, i)
        if abs(z1) <= 0:
            continue
        ib = sb / (SQRT3 * kv)                       # kA per pu at this bus
        ka_thev = (PREFAULT_VPU / abs(z1)) * ib
        ipu = _reported_ipu(rlst, i)
        ka_rep = _reported_ka(rlst, i, kv, sb)
        if ipu is None and ka_rep is not None:
            ipu = ka_rep / ib                        # the build gave amps; back to pu
        if how == "ia1" and ipu:
            ka, vpf = ipu * ib, ipu * abs(z1)
        elif how == "thev_v":
            v = solved_vpu(b)
            ka, vpf = ((v if v else PREFAULT_VPU) / abs(z1)) * ib, (v if v else None)
        else:
            ka, vpf = ka_thev, (PREFAULT_VPU if how == "flat" else None)
        xr = (z1.imag / z1.real) if abs(z1.real) > 1e-9 else float("inf")
        # the console's RE(I)/IM(I): the module's own Ia1 under the code used, or,
        # when the current was built from V/|Z1|, that magnitude at the Z1 angle
        ia1 = _reported_ia1(rlst, i, ib) if how == "ia1" else None
        if ia1 is None:
            _m = ka / ib
            ia1 = complex(_m * (z1.real / abs(z1)), -_m * (z1.imag / abs(z1)))   # V / Z1, V real
        out[b] = {"kv": kv, "kA": ka, "xr": xr, "z1": z1, "vpf": vpf,
                  "kA_thev": ka_thev, "kA_rep": ka_rep, "ia1": ia1, "ipu": ka / ib}
    _v = [o["vpf"] for o in out.values() if o.get("vpf")]
    print("  [%s] %s: %d bus(es) computed (Sbase %.0f MVA; %s)"
          % (_ts(), label, len(out), sb,
             ("flat %.2f pu" % PREFAULT_VPU) if how == "flat" else
             ("prefault V %.4f-%.4f pu via %s" % (min(_v), max(_v),
                                                  "Ia1 (voltop=%s)" % _ASCC_MODE["code"] if how == "ia1" else "V_solved/|Z1|")
              if _v else "prefault V unknown")))
    return out


# ---- xlsx writer (no dependencies) -----------------------------------------
S_HEAD, S_RED, S_AMBER, S_GREEN, S_BOLD, S_GREY = 1, 2, 3, 4, 5, 6
_STYLES_XML = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.000"/></numFmts>'
               '<fonts count="3"><font><sz val="10"/><name val="Calibri"/></font>'
               '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
               '<font><b/><sz val="10"/><name val="Calibri"/></font></fonts>'
               '<fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FFF4C7C3"/></patternFill></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FFFFE699"/></patternFill></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FFC6EFCE"/></patternFill></fill>'
               '<fill><patternFill patternType="solid"><fgColor rgb="FFEDEDED"/></patternFill></fill></fills>'
               '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
               '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
               '<cellXfs count="7"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
               '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment wrapText="1" vertical="center"/></xf>'
               '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
               '<xf numFmtId="0" fontId="0" fillId="4" borderId="0" xfId="0" applyFill="1"/>'
               '<xf numFmtId="0" fontId="0" fillId="5" borderId="0" xfId="0" applyFill="1"/>'
               '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
               '<xf numFmtId="0" fontId="0" fillId="6" borderId="0" xfId="0" applyFill="1"/>'
               '</cellXfs></styleSheet>')


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_xml(header, rows, widths):
    o = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
         '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>']
    for i, w in enumerate(widths):
        o.append('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
    o.append('</cols><sheetData>')

    def cell(ref, v, st):
        if v is None or v == "":
            return '<c r="%s" s="%d"/>' % (ref, st) if st else ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                v = "inf" if v == v else ""
                return '<c r="%s" s="%d" t="inlineStr"><is><t>%s</t></is></c>' % (ref, st, v)
            return '<c r="%s" s="%d"><v>%r</v></c>' % (ref, st, v)
        return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, st, escape(str(v)))
    o.append('<row r="1">' + "".join(cell("%s1" % _col(i), h, S_HEAD) for i, h in enumerate(header)) + "</row>")
    for ri, r in enumerate(rows):
        cells = []
        for i, x in enumerate(r):
            v, st = (x if isinstance(x, tuple) else (x, 0))
            cells.append(cell("%s%d" % (_col(i), ri + 2), v, st))
        o.append('<row r="%d">%s</row>' % (ri + 2, "".join(cells)))
    o.append('</sheetData><autoFilter ref="A1:%s%d"/></worksheet>' % (_col(len(header) - 1), max(1, len(rows) + 1)))
    return "".join(o)


def write_xlsx(path, sheets):
    z = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
          '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            '<Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>']
    for i, (name, header, rows, widths) in enumerate(sheets):
        n = i + 1
        ct.append('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % n)
        wb.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(name[:31]), n, n))
        rels.append('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (n, n))
        z.writestr("xl/worksheets/sheet%d.xml" % n, _sheet_xml(header, rows, widths))
    ct.append("</Types>"); wb.append("</sheets></workbook>"); rels.append("</Relationships>")
    z.writestr("[Content_Types].xml", "".join(ct))
    z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
    z.writestr("xl/workbook.xml", "".join(wb))
    z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
    z.writestr("xl/styles.xml", _STYLES_XML)
    z.close()


# ---- automatic inputs --------------------------------------------------------
def find_case(project):
    """The .sav for `project` per the rules above DEFAULT_SAV. Returns (path, how)."""
    if project.get("sav"):
        s = project["sav"]
        return (s if os.path.isabs(s) else os.path.join(STUDY_DIR, s)), "named in PROJECTS_SC"
    savs = sorted(glob.glob(os.path.join(STUDY_DIR, "*.sav")), key=os.path.getmtime, reverse=True)
    key = str(project["name"]).lower().replace(" ", "")
    mine = [s for s in savs if key in os.path.basename(s).lower().replace(" ", "")]
    # THE AS-BUILT CASE, NOT A SCENARIO OF IT. The _f study saves the GIA case
    # (..._<project>_<MW>MW_f_NEWPLANT.sav) AND the surplus scenario built after
    # it (..._f_s1_egfoff_NEWPLANT.sav, every existing unit OUT). "The newest
    # name match" was that EGF-off case -- the wrong case for every SC step.
    def _scen(p):
        b = os.path.basename(p).lower()
        return ("egfoff" in b) or ("_s1_" in b) or ("_s2_" in b) or ("_s3_" in b)
    _keep = [s for s in mine if not _scen(s)]
    if _keep:
        _np = [s for s in _keep if "newplant" in os.path.basename(s).lower()]
        _pick = (_np or _keep)[0]
        if len(_keep) < len(mine):
            print("[case] %s: %d scenario case(s) (EGF off / surplus) left out of the pick: %s"
                  % (project["name"], len(mine) - len(_keep),
                     ", ".join(os.path.basename(x) for x in mine if _scen(x))))
        return _pick, "name match in %s (as-built case, scenarios excluded)" % STUDY_DIR
    if mine:
        return mine[0], "name match in %s" % STUDY_DIR
    if DEFAULT_SAV:
        s = DEFAULT_SAV
        return (s if os.path.isabs(s) else os.path.join(STUDY_DIR, s)), "DEFAULT_SAV"
    if len(savs) == 1:
        return savs[0], "the only .sav in %s" % STUDY_DIR
    if savs:
        return savs[0], "NEWEST of %d .sav files in %s (%s)" % (len(savs), STUDY_DIR, ", ".join(os.path.basename(x) for x in savs[:6]))
    raise RuntimeError("no .sav found in %s -- set DEFAULT_SAV or a 'sav' per project" % STUDY_DIR)


def projects_from_dynamic_study():
    """PROJECTS_SC built from BESS_PROJECTS in the dynamic study's project engine
       (z4_spp_p*.py beside this script): name, poi, feeders -> keep_online."""
    import ast
    cands = sorted(glob.glob(os.path.join(STUDY_DIR, "z4_spp_p*.py")) + glob.glob(os.path.join(_HERE, "z4_spp_p*.py")),
                   key=os.path.getmtime, reverse=True)
    for f in cands:
        try:
            src = open(f, "r", errors="ignore").read()
            tree = ast.parse(src)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(x, "id", "") == "BESS_PROJECTS" for x in node.targets):
                try:
                    rows = ast.literal_eval(node.value)
                except Exception:
                    continue
                out = []
                for r in rows:
                    if not isinstance(r, dict) or not r.get("name") or not r.get("poi"):
                        continue
                    out.append({"name": r["name"], "poi": int(r["poi"]), "gens": "NEW",
                                "keep_online": [int(b) for b in (r.get("feeders") or [])]})
                if out:
                    print("[auto] %d project(s) read from %s: %s" % (len(out), os.path.basename(f), ", ".join(p["name"] for p in out)))
                    return out
    raise RuntimeError("PROJECTS_SC is empty and no BESS_PROJECTS table was found in a z4_spp_p*.py beside this script")


# ---- one project -----------------------------------------------------------
def nominal_kv(kv):
    std = [13.8, 34.5, 46.0, 69.0, 115.0, 138.0, 161.0, 230.0, 345.0, 500.0, 765.0]
    return min(std, key=lambda s: abs(s - kv)) if kv else 0.0


def study_project(p):
    name = p["name"]
    sav, how = find_case(p)
    print("  case for %s: %s   [%s]" % (name, sav, how))
    print("\n" + "=" * 72 + "\n %s   %s\n" % (name, sav) + "=" * 72)
    if not os.path.isfile(sav):
        raise RuntimeError("case not found: %s" % sav)
    if psspy.case(sav) != 0:
        raise RuntimeError("case() failed: %s" % sav)
    build_bus_index()
    poi = int(p["poi"])
    if poi not in _BUSKV:
        raise RuntimeError("POI bus %d is not in %s" % (poi, os.path.basename(sav)))
    global _BR_CK
    _BR_CK = None
    _build_adj()
    _defaults, _ovsrc = project_sc_defaults(name)
    print("  SC model parameters for %s: R = %s, X'' = %s (pu on machine MVA base)%s"
          % (name, _n(_defaults.get("r")), _n(_defaults.get("xpp")),
             ("   <- %s" % _ovsrc) if _ovsrc
             else "   <- DEFAULT_SC_PARAMS (same for every project)"))
    gens = resolve_gens(p.get("gens", "NEW"), p.get("sc_params"), poi, _defaults)
    apply_sc_params(gens)
    params = read_sc_params(gens)                 # what the case holds NOW -- the report's Table 5-1
    for g, _rec in zip(gens, params):
        mb, zr, zx, sq = _rec[0], _rec[1], _rec[2], (_rec[3] if len(_rec) > 3 else None)
        print("  SC model %d '%s': MBASE %s  R %s  X'' %s (pu on MBASE)%s"
              % (g["num"], g["id"], _n(mb), _n(zr), _n(zx),
                 ("   *** the fault calculation will use X''=%s, NOT %s -- the "
                  "sequence record was not written ***" % (_n(sq), _n(zx)))
                 if (sq is not None and zx is not None
                     and abs(sq - zx) > max(0.01, 0.05 * abs(zx))) else ""))
    # THE NUMBER THAT DECIDES THE CONTRIBUTION. ASCC (GENXOP=0) uses ZSORCE = R + jX''
    # from the power-flow machine record. A plant built for dynamics often carries
    # the default or a very large X'' there, which makes its fault contribution
    # a few amps -- a 0.01 % change at the POI is that, not physics. The
    # submitted value (typically 0.5-1.0 pu on MBASE for an inverter plant,
    # ~0.15-0.25 for a synchronous unit) goes in sc_params.
    # THE REACTANCE THAT DECIDES THE ANSWER is the one the fault calculation
    # reads (the sequence record), and only ZSORCE when this build has no
    # sequence table -- so judge whichever of the two is actually in force.
    _bad = []
    for g, _rec in zip(gens, params):
        _zx = _rec[3] if (len(_rec) > 3 and _rec[3] is not None) else _rec[2]
        if _zx is None or _zx <= 0 or _zx > 5.0:
            _bad.append((g, _zx))
    if _bad:
        print("  *** WARNING: %d project machine(s) have no usable short-circuit X'' in this case (%s). "
              "Their fault contribution will be negligible. Give the submitted MBASE / R / X'' in "
              "sc_params for %s and run again. ***"
              % (len(_bad), ", ".join("%d: X''=%s" % (g["num"], _n(zx)) for g, zx in _bad), name))
    hops = hop_map(poi, HOPS)
    buslist = sorted(b for b, h in hops.items() if basekv(b) >= MIN_FAULT_KV)
    if poi not in buslist:
        buslist.append(poi)
    print("  POI %d %s (%.1f kV): %d bus(es) within %d levels, %d at/above %.0f kV faulted"
          % (poi, bus_name(poi), basekv(poi), len(hops), HOPS, len(buslist), MIN_FAULT_KV))
    print("  existing units left online in every case: %s" % (p.get("keep_online") or []))
    kept, existing = [], []
    for b in (p.get("keep_online") or []):
        for mb, mi, st, mm in machines_all():
            if mb == int(b):
                kept.append("%d '%s' %s" % (mb, mi, "IN" if st == 1 else "OUT"))
                existing.append({"num": mb, "id": mi, "mbase0": mm, "st0": st})
    if kept:
        print("  keep-online machines as saved: %s" % ", ".join(kept))
    # THE EXISTING UNITS' X'' (EGF_XPP_BY_PROJECT): written before anything reads
    # it, so the check below, every case, the per-unit contribution and the
    # saved SC cases all see the same value.
    egf_params = apply_egf_xpp(name, existing)
    # AN EGF UNIT THAT CANNOT FEED A FAULT. The existing units are studied as the
    # case holds them, and a unit carrying the dynamics default (9999) or no
    # usable X'' contributes nothing however it is dispatched -- EastFork's
    # 531607 showed +0.016 kA at its own POI for that reason. Said here so the
    # EGF column is not read as a result when it is a data gap.
    _egf_bad = []
    for g in existing:
        _zr, _zx = None, None
        for _fn in ("macdt2", "macdat"):
            _f_ = getattr(psspy, _fn, None)
            if not _f_:
                continue
            try:
                _ie, _z = _f_(g["num"], str(g["id"]), "ZSORCE")
                if _ie in _OK_MAC and _z is not None:
                    _zx = float(_z.imag); break
            except Exception:
                continue
        _sx = _read_seq_x(g["num"], g["id"])
        _use = _sx if _sx is not None else _zx
        if _use is None or _use <= 0 or _use > 5.0:
            _egf_bad.append((g, _use))
    if _egf_bad:
        print("  *** %d existing (EGF) unit(s) have no usable short-circuit X'' -- they will contribute"
              " nothing to any fault: %s. The EGF contribution for %s is understated until the"
              " submitted X'' is written on them: EGF_XPP_BY_PROJECT in the settings. ***"
              % (len(_egf_bad), ", ".join("%d '%s' X''=%s" % (g["num"], g["id"], _n(x)) for g, x in _egf_bad), name))
    # THE EGF IN SERVICE. What moves the EGF's fault contribution is which of
    # its units are CONNECTED -- each one is a parallel source branch -- not
    # what they are dispatched to. So for the EGF-in cases every keep_online
    # unit goes in service here, named, and its saved status is put back at the
    # end of the project (energized_egf holds the ones that were saved out).
    energized_egf = []
    del _ENERGIZED_EGF[:]
    _PROJECT_GENS[:] = list(gens)       # for the abort path only; cleared on normal completion
    _reset_ascc_mode()                  # the prefault probe is per case
    if EGF_ENERGIZE and existing:
        for g in existing:
            if int(g.get("st0", 1)) != 1:
                energized_egf.append(g)
        if energized_egf:
            set_status(energized_egf, True)
            _ENERGIZED_EGF.extend(energized_egf)   # so an abort can still put them back
            for g in energized_egf:
                g["st0"] = 1            # treated as in service for every restore below
            print("  [%s] EGF_ENERGIZE: %d existing unit(s) saved OUT of service switched IN for the"
                  " EGF-in cases: %s" % (_ts(), len(energized_egf),
                                         ", ".join("%d '%s'" % (g["num"], g["id"]) for g in energized_egf)))
        else:
            print("  EGF_ENERGIZE: every keep_online unit is already in service as saved")
    elif existing:
        _out_ = [g for g in existing if int(g.get("st0", 1)) != 1]
        if _out_:
            print("  *** EGF_ENERGIZE is off: %d keep_online unit(s) saved OUT stay out in EVERY case"
                  " and contribute nothing: %s ***"
                  % (len(_out_), ", ".join("%d '%s'" % (g["num"], g["id"]) for g in _out_)))
    print("  capacity scope: %s" % CAPACITY_SCOPE)

    cases, cap_notes, powers, snaps = [], [], [], []   # [(label, pct or None, {bus: rec})]
    conv = []                                          # [(label, solved code, note)]
    # WHAT GOES OUT FOR THE "WITHOUT" CASE -- the project alone, or the whole
    # site. Under "site" the existing units are switched out here and put back
    # exactly as the case saved them before the WITH cases run, so the two cases
    # differ by the plant and by nothing else.
    _site_off = (WITHOUT_SCOPE or "new").lower() == "site"
    if RUN_NOGEN_CASE:
        # Case 1 IS the whole-site-out state, run on its own below, so the
        # WITHOUT case here is always "EGF only" no matter what WITHOUT_SCOPE says.
        _site_off = False
    _n_cases = (1 if RUN_NOGEN_CASE else 0) + 1 + len(CAPACITY_PCT)
    _step = [0]

    def _next(lbl):
        _step[0] += 1
        status("%s %d/%d -- %s" % (name, _step[0], _n_cases, lbl))

    # ---- CASE 1 -- NO GENERATION AT THE POI (SGF and EGF both out) ----------
    nogen = None
    nogen_lbl = "NO GENERATION at the POI"
    if RUN_NOGEN_CASE:
        if not existing:
            print("  *** RUN_NOGEN_CASE: this project lists no keep_online units, so"
                  " case 1 is identical to case 2 (only the project machines can go"
                  " out). The EGF column will read 0. ***")
        _next(nogen_lbl + " (SGF + EGF out)")
        set_status(list(gens) + list(existing), False)
        conv.append((nogen_lbl,) + solve_pf("%s / %s" % (name, nogen_lbl)))
        _gate_now(name, conv)
        snaps.append((nogen_lbl, machine_snapshot(gens, existing)))
        powers.append((nogen_lbl, poi_power(poi, gens, existing)))
        _say_power(powers[-1])
        _save_case(name, "SC1_NOGEN_SGF_EGF_OUT", nogen_lbl, gens, existing, poi)
        nogen = run_ascc(buslist, "%s NO-GEN" % name)
        # EGF back exactly as the case saved them -- a unit saved out stays out.
        restore_status(existing)

    # ---- CASE 2 -- EGF ONLY (SGF out, EGF in) ------------------------------
    _off_now = list(gens) + (list(existing) if _site_off else [])
    off_lbl = _off_label()
    print("  WITHOUT case turns off: %s (WITHOUT_SCOPE = %s%s)"
          % ("the project machines AND the %d existing unit(s) at this site"
             % len(existing) if _site_off else "the project machines only",
             WITHOUT_SCOPE,
             ", overridden by RUN_NOGEN_CASE" if RUN_NOGEN_CASE else ""))
    if _site_off and not existing:
        print("  *** WITHOUT_SCOPE = \"site\" but this project lists no keep_online "
              "units, so there is nothing extra to switch out -- the WITHOUT case "
              "is the same as \"new\". ***")
    _next(off_lbl)
    set_status(_off_now, False)
    # THE EGF AT FULL CAPACITY, for this case only. Restored before case 3, which
    # keeps the case's own proportional SGF+EGF dispatch.
    egf_note, _pg_saved = "", None
    _egf_mode = str(EGF_DISPATCH or "asis").strip().lower()
    if _egf_mode[:3] in ("gia", "ful", "pma") and existing and not _site_off:
        _pg_saved = snapshot_pgen(existing)
        egf_note = set_egf_full(existing, p.get("gia_mw"), _egf_mode, poi=poi, gens=gens)
    else:
        egf_note = "dispatch as the power-flow model holds it (EGF_DISPATCH = %s)" % EGF_DISPATCH
        print("  [%s] EGF dispatch: %s" % (_ts(), egf_note))
    conv.append((off_lbl,) + solve_pf("%s / %s" % (name, off_lbl)))
    _gate_now(name, conv)
    snaps.append((off_lbl, machine_snapshot(gens, existing)))
    powers.append((off_lbl, poi_power(poi, gens, existing)))
    _say_power(powers[-1])
    _save_case(name, "SC2_EGF_ONLY", off_lbl, gens, existing, poi)
    cases.append((off_lbl, None, run_ascc(buslist, "%s WITHOUT" % name)))
    if _pg_saved:
        restore_pgen(_pg_saved)
    set_status(gens, True)
    if _site_off:
        restore_status(existing)
    # CASE 3 IS THE .sav AS BUILT: units EGF_ENERGIZE switched in for case 2 go
    # back OUT, so they add neither MW nor fault current the case never had.
    if energized_egf and not EGF_ENERGIZE_WITH:
        set_status(energized_egf, False)
        for g in energized_egf:
            g["st0"] = 0            # listed as saved out (zero contribution) from here on
        print("  [%s] EGF_ENERGIZE_WITH = False: %d unit(s) saved OUT put back OUT for the"
              " SGF + EGF case: %s" % (_ts(), len(energized_egf),
                                     ", ".join("%d '%s'" % (g["num"], g["id"]) for g in energized_egf)))
    for k, pct in enumerate(CAPACITY_PCT):
        _next("WITH project (SGF + EGF) at %d%%" % pct)
        note = set_capacity(gens, pct, existing)
        cap_notes.append((pct, note))
        # SPP scenario 2: hold the POI injection to the Interconnection Service
        # amount by scaling SGF and EGF proportionally. Only at 100%; a reduced
        # capacity case is already below the ceiling by construction.
        _pd = str(POI_DISPATCH or "asis").strip().lower()
        if pct == 100 and _pd[:3] in ("gia", "rem"):
            poi_note = (set_poi_remainder(gens, existing, p.get("gia_mw"))
                        if _pd.startswith("rem")
                        else set_poi_to_gia(gens, existing, p.get("gia_mw")))
            cap_notes.append((pct, poi_note))
        conv.append(("WITH project %d%%" % pct,)
                    + solve_pf("%s / WITH %d%%" % (name, pct)))
        _gate_now(name, conv)
        snaps.append(("WITH project %d%%" % pct, machine_snapshot(gens, existing)))
        powers.append(("WITH project %d%%" % pct, poi_power(poi, gens, existing)))
        _say_power(powers[-1])
        _save_case(name, "SC3_SGF_EGF_%dPCT" % pct, "WITH project %d%%" % pct, gens, existing, poi)
        cases.append(("WITH project %d%%" % pct, pct, run_ascc(buslist, "%s WITH %d%%" % (name, pct))))
    set_capacity(gens, 100, existing)             # leave the case as it was
    # ---- DID EVERY CASE SOLVE? ----------------------------------------------
    # Checked HERE -- after the sweeps, before anything is written -- so that a
    # project whose results cannot be trusted either stops, or carries the
    # reason with it into every file. Raises when the policy says fatal.
    conv_ok, conv_note = _convergence_gate(name, conv)
    gia_lines, gia_ok = gia_check(name, powers, p.get("gia_mw"), p.get("sgf_mw"))
    if SAVE_CASES:
        _write_cases_txt(name, poi, snaps, powers, params, gens)
    if CASES_ONLY:
        print("  [%s] CASES_ONLY: %s -- cases saved, no fault sweep" % (_ts(), name))
        return None
    if POI_GIA_STRICT and not gia_ok:
        raise RuntimeError("%s: POI injection is not the %.1f MW target in every EGF-in case "
                           "(see the lines above) -- results NOT written. Fix gia_mw to match "
                           "POI_MW in the launcher, or the case." % (name, float(p.get("gia_mw") or 0)))
    # ---- per-unit contribution (existing units AND project machines) ----------
    contrib = None
    if PER_MACHINE_CONTRIB:
        try:
            contrib = machine_contributions(gens, existing, poi, buslist)
        except Exception as _e:
            print("  *** per-unit contribution skipped: %s ***" % _e); traceback.print_exc()
            set_status(gens, True); restore_status(existing)

    # ---- rows ----------------------------------------------------------------
    off = cases[0][2]
    rows = []
    for b in buslist:
        if b not in off:
            continue
        r = {"bus": b, "name": bus_name(b), "kv": basekv(b), "nom_kv": nominal_kv(basekv(b)),
             "hops": hops.get(b, ""), "off": off[b]["kA"], "xr_off": off[b]["xr"], "with": [],
             # the prefault voltage each case's current was driven by (VOLTOP = 1),
             # so a reader can tie 14.34 kA to the 1.0087 pu on the one-line
             "vpf": dict((lbl, (res.get(b) or {}).get("vpf")) for lbl, _pct, res in cases),
             # the console's own quantities per case, for the "GUI pu" sheet:
             # |I''k| pu (SCMVA column), RE(I), IM(I), Z+ R, Z+ X, X/R, prefault V
             "gui": [(lbl, res.get(b)) for lbl, res in
                     (([(nogen_lbl, nogen)] if nogen else []) + [(l, rs) for l, _p, rs in cases])
                     if res.get(b)],
             # CASE 1, and the two contributions it makes possible. None when
             # RUN_NOGEN_CASE is off -- every writer checks before using it.
             "nogen": (nogen.get(b, {}) or {}).get("kA") if nogen else None,
             "egf": None}
        for lbl, pct, res in cases[1:]:
            on = res.get(b)
            if on is None:
                r["with"].append((pct, None, None, None, None))
                continue
            d = on["kA"] - r["off"]
            r["with"].append((pct, on["kA"], d, (d / r["off"] * 100.0) if r["off"] > 0 else 0.0, on["xr"]))
        # EGF contribution = case 2 - case 1. The SGF contribution is already the
        # change column (case 3 - case 2); the site total is case 3 - case 1.
        if r["nogen"] is not None:
            r["egf"] = r["off"] - r["nogen"]
        rows.append(r)
    rows.sort(key=lambda x: (-x["nom_kv"], x["hops"] if x["hops"] != "" else 99, -x["off"]))

    # ---- IS THIS A RESULT, OR A MODELLING FAULT? ----------------------------
    #
    # A plant of real MVA raises the fault current at its own POI by a visible
    # amount, and by steadily less the farther away the bus is. Two patterns say
    # the machines are not in the fault calculation at all:
    #
    #   * nothing moves anywhere -- the largest change in the whole radius is a
    #     few amps;
    #   * everything moves by the SAME few amps -- the POI, and a bus five hops
    #     away, change alike. Contribution falls off with impedance; a flat
    #     change is round-off in the solution, not current from the plant.
    #
    # The cause is almost always the sequence record: ASCC reads the sequence
    # machine table, and a machine created for a DYNAMIC study carries an
    # enormous source impedance there, so it can be in service, at its full MVA
    # base, and still contribute nothing. Saying it here means it is read before
    # the workbook is, rather than after the numbers have been circulated.
    try:
        _add_mva = sum((g.get("mbase0") or 0.0) for g in gens)
        _dk = [(r, max([abs(w[2]) for w in r["with"] if w[2] is not None] or [0.0])) for r in rows]
        _max_d = max([d for _r, d in _dk] or [0.0])
        _poi_d = next((d for _r, d in _dk if _r["bus"] == poi), 0.0)
        _far = [d for _r, d in _dk if _r["hops"] not in ("", None) and int(_r["hops"]) >= max(2, HOPS - 1)]
        _far_d = (sum(_far) / len(_far)) if _far else 0.0
        _flat = (_far_d > 0 and _poi_d < WARN_FLAT_RATIO * _far_d)
        if _add_mva > 10.0 and (_max_d < WARN_NO_CONTRIBUTION_KA or _flat):
            print("")
            print("  " + "*" * 68)
            if _max_d < WARN_NO_CONTRIBUTION_KA:
                print("  *** %s ADDS %.0f MVA AND MOVES NO BUS ***" % (name.upper(), _add_mva))
                print("  *** largest change anywhere: %.4f kA (POI: %.4f kA)" % (_max_d, _poi_d))
            else:
                print("  *** %s: THE CHANGE IS FLAT ACROSS THE RADIUS ***" % name.upper())
                print("  *** POI %.4f kA vs %.4f kA average %d hops out -- fault"
                      % (_poi_d, _far_d, max(2, HOPS - 1)))
                print("  *** contribution falls off with distance; this does not.")
            print("  ***")
            print("  *** The machines are almost certainly not in the fault")
            print("  *** calculation. ASCC reads the SEQUENCE machine record, not")
            print("  *** ZSORCE alone, and a plant built for a dynamic study carries")
            print("  *** a very large impedance there. Check the \"SC model")
            print("  *** parameters\" sheet: where X'' used by the fault calc differs")
            print("  *** from X'' written, that is the cause. APPLY_SC_PARAMS is %s"
                  % ("on" if APPLY_SC_PARAMS else "OFF -- turn it on"))
            print("  *** and sc_params for this project %s."
                  % ("is set" if p.get("sc_params") else "is NOT set: give the submitted MBASE / R / X''"))
            print("  " + "*" * 68)
            print("")
    except Exception as _e:
        print("  (contribution sanity check skipped: %s)" % _e)

    # ---- per-project csv -----------------------------------------------------
    pdir = os.path.join(RESULTS_DIR, name)
    if not os.path.isdir(pdir):
        os.makedirs(pdir)
    _three = bool(nogen)
    hdr = ["Bus", "Name", "Base kV (kV)", "Hops from POI"]
    if _three:
        hdr += ["1 NO GEN (kA)"]
    hdr += ["%s (kA)" % ("2 EGF only" if _three else _off_label()), "X/R without (ratio)"]
    for pct in CAPACITY_PCT:
        hdr += ["%s (kA)" % ("3 SGF+EGF" if _three else "WITH %d%%" % pct),
                "SGF contribution (kA)" if _three else "change %d%% (kA)" % pct,
                "SGF contribution (%)" if _three else "change %d%% (%%)" % pct,
                "X/R with %d%% (ratio)" % pct]
    if _three:
        hdr += ["EGF contribution (kA)", "Site contribution (kA)", "Site contribution (%)"]
    if VOLTOP != 0:
        hdr += ["Pre-fault V used, %s (pu)" % lbl for lbl, _pct, _res in cases]
    cp = os.path.join(pdir, "SC_BUS_%s.csv" % name)
    with open(cp, "w") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(hdr)
        for r in rows:
            line = [r["bus"], r["name"], "%.2f" % r["kv"], r["hops"]]
            if _three:
                line += ["" if r["nogen"] is None else "%.3f" % r["nogen"]]
            line += ["%.3f" % r["off"], _xr(r["xr_off"])]
            for pct, ka, d, pc, xr in r["with"]:
                line += ["" if ka is None else "%.3f" % ka, "" if d is None else "%.3f" % d,
                         "" if pc is None else "%.2f" % pc, _xr(xr)]
            if _three:
                _w0 = r["with"][0][1] if r["with"] else None
                _site = (_w0 - r["nogen"]) if (_w0 is not None and r["nogen"] is not None) else None
                line += ["" if r["egf"] is None else "%.3f" % r["egf"],
                         "" if _site is None else "%.3f" % _site,
                         "" if (_site is None or not r["nogen"]) else "%.2f" % (_site / r["nogen"] * 100.0)]
            if VOLTOP != 0:
                line += ["%.5f" % r["vpf"][lbl] if (r.get("vpf") or {}).get(lbl) else ""
                         for lbl, _pct, _res in cases]
            w.writerow(line)
    print("  -> %s" % cp)
    # ---- Appendix B (Table B-1), SPP column order, largest change first -------
    ap = os.path.join(pdir, "SC_APPENDIX_B_%s.csv" % name)
    with open(ap, "w") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Bus Number", "Bus Name", "Bus kV", "Area", "Zone",
                    "Gen ON (kA)", "GEN OFF (kA)", "Change (kA)", "% Change",
                    "Distance from POI Bus %d" % poi,
                    "Greater than %.0f kA Current" % HIGHLIGHT_KA])
        _pr = [r for r in rows if r["with"] and r["with"][0][1] is not None]
        for r in sorted(_pr, key=lambda x: -x["with"][0][2]):
            on, ch, pc = r["with"][0][1], r["with"][0][2], r["with"][0][3]
            w.writerow([r["bus"], r["name"], "%.2f" % r["kv"],
                        bus_area(r["bus"]), bus_zone(r["bus"]),
                        "%.4f" % on, "%.4f" % r["off"], "%.4f" % ch, "%.6f" % pc,
                        r["hops"], "TRUE" if on >= HIGHLIGHT_KA else "FALSE"])
    print("  -> %s" % ap)
    # ---- summary text --------------------------------------------------------
    poi_row = next((r for r in rows if r["bus"] == poi), None)
    sp = os.path.join(pdir, "SC_SUMMARY_%s.txt" % name)
    with open(sp, "w") as f:
        f.write(_summary_text(p, sav, gens, rows, poi_row, hops, cap_notes, params, powers, snaps,
                              contrib=contrib, conv=conv, gia_lines=gia_lines))
    print("  -> %s" % sp)
    # ---- per-unit contribution csv ----------------------------------------------
    if contrib:
        cpath = os.path.join(pdir, "SC_CONTRIB_%s.csv" % name)
        with open(cpath, "w") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["Fault bus", "Fault bus name", "Fault kV (kV)", "Unit bus", "Id", "Unit name", "Group",
                        "MVA base (MVA)", "Status", "All units in (kA)", "Unit out (kA)",
                        "Contribution (kA)", "Contribution (%)"])
            for fb, r, pb, is_grp in _contrib_iter(contrib, poi):
                nm, kv = contrib["fb_info"].get(fb, ("", 0.0))
                w.writerow([fb, nm, "%.2f" % (kv or 0.0), r["bus"], r["id"], r["name"], r["group"],
                            "" if r["mbase"] is None else "%.2f" % r["mbase"], r["status"],
                            "%.3f" % pb[0], "%.3f" % pb[1], "%.3f" % pb[2], "%.2f" % pb[3]])
        print("  -> %s" % cpath)
    # EGF units this run switched in go back to the status the case was saved with.
    # (The project machines are already back in service here -- set_capacity(100)
    # above ran on the WITH state -- so only the EGF restore does anything.)
    _restore_energized()
    return {"project": name, "sav": sav, "poi": poi, "gens": gens, "rows": rows, "poi_row": poi_row,
            "energized_egf": ["%d '%s'" % (g["num"], g["id"]) for g in energized_egf],
            "n_radius": len(hops), "kept": kept, "params": params, "egf_params": egf_params,
            "powers": powers, "snaps": snaps,
            "contrib": contrib, "three": bool(nogen), "egf_note": egf_note,
            "gia_mw": p.get("gia_mw"),
            "conv": conv, "conv_ok": conv_ok, "conv_note": conv_note,
            "gia_lines": gia_lines, "gia_ok": gia_ok, "sgf_mw": p.get("sgf_mw")}


def _mach_status(bus, mid):
    """Machine service status now: 1 in, 0 out, None unknown."""
    try:
        ie, st = psspy.macint(int(bus), str(mid), "STATUS")
        return int(st) if ie == 0 else (0 if ie == 4 else None)
    except Exception:
        return None


def _contrib_fault_buses(poi, buslist):
    """Which bus(es) the per-unit contribution is taken at, from CONTRIB_FAULT_BUSES."""
    spec = CONTRIB_FAULT_BUSES
    if isinstance(spec, str):
        return list(buslist) if spec.strip().upper() == "ALL" else [int(poi)]
    out = []
    for b in (spec or []):
        if isinstance(b, str) and b.strip().upper() == "POI":
            out.append(int(poi))
        else:
            try:
                out.append(int(b))
            except Exception:
                print("  *** CONTRIB_FAULT_BUSES entry %r ignored ***" % (b,))
    out = [b for b in out if b in _BUSKV]
    return sorted(set(out)) or [int(poi)]


def machine_contributions(gens, existing, poi, buslist):
    """Per-unit contribution to the fault current, by removal.

       Starting from the WITH-project state (every unit in service), take ONE
       machine out, fault the chosen bus(es) again, and the drop in fault current
       is that machine's contribution -- the report's own definition of
       "contribution" (ON minus OFF), applied unit by unit, for the existing
       units and the project machines alike. Group rows take all new / all
       existing units out together (the all-new row is the WITHOUT column).
       Sources feed a fault in parallel through the network, so the per-unit
       values do not add up to the group total exactly: each is the marginal
       effect of that unit alone. A unit saved out of service is never switched
       on -- it is listed with zero contribution."""
    cbus = _contrib_fault_buses(poi, buslist)
    mach = ([dict(g, group="New (project)") for g in gens]
            + [dict(g, group="Existing") for g in (existing or [])])
    if not mach:
        return None
    status("per-unit contribution: %d unit(s), fault at %s"
           % (len(mach), ", ".join("%d %s" % (b, bus_name(b)) for b in cbus)))
    base = run_ascc(cbus, "all units in")
    fb_info = dict((b, (bus_name(b), basekv(b))) for b in cbus)

    def _delta(res):
        d = {}
        for b in cbus:
            if b in base and b in res:
                ia, io = base[b]["kA"], res[b]["kA"]
                dk = ia - io
                d[b] = (ia, io, dk, (dk / ia * 100.0) if ia > 0 else 0.0)
        return d

    rows = []
    for m in mach:
        rec = {"bus": m["num"], "id": m["id"], "name": bus_name(m["num"]), "group": m["group"],
               "mbase": m.get("mbase0"), "status": "IN", "per_bus": {}}
        if _mach_status(m["num"], m["id"]) != 1:
            rec["status"] = "OUT (as saved)"
            rec["per_bus"] = dict((b, (base[b]["kA"], base[b]["kA"], 0.0, 0.0)) for b in cbus if b in base)
            rows.append(rec)
            continue
        set_status([m], False)
        if VOLTOP == 1:
            solve_pf()
        try:
            rec["per_bus"] = _delta(run_ascc(cbus, "%d '%s' out" % (m["num"], m["id"])))
        finally:
            set_status([m], True)
        rows.append(rec)
    groups = []
    for glbl, members in (("ALL new (project) machines", [m for m in mach if m["group"].startswith("New")]),
                          ("ALL existing units", [m for m in mach if m["group"] == "Existing"])):
        ins = [m for m in members if _mach_status(m["num"], m["id"]) == 1]
        if not ins:
            continue
        set_status(ins, False)
        if VOLTOP == 1:
            solve_pf()
        try:
            per = _delta(run_ascc(cbus, "%s out" % glbl))
        finally:
            set_status(ins, True)
        groups.append({"bus": "", "id": "", "name": glbl, "group": glbl,
                       "mbase": sum((m.get("mbase0") or 0.0) for m in ins), "status": "IN", "per_bus": per})
    for b in cbus:
        if b in base:
            print("  contribution at %d %s (all in %.3f kA): %s"
                  % (b, bus_name(b), base[b]["kA"],
                     "; ".join("%s %d '%s' %+.3f kA (%+.2f%%)"
                               % ("new" if r["group"].startswith("New") else "exist", r["bus"], r["id"],
                                  r["per_bus"][b][2], r["per_bus"][b][3])
                               for r in rows if b in r["per_bus"])))
    return {"fault_buses": cbus, "fb_info": fb_info, "base": base, "rows": rows, "groups": groups}


def _contrib_iter(contrib, poi):
    """(fault bus, record, (all-in kA, unit-out kA, contribution kA, %), is_group):
       POI first, new units before existing, largest contribution first, group totals last."""
    fbs = list(contrib["fault_buses"])
    if int(poi) in fbs:
        fbs.remove(int(poi))
        fbs.insert(0, int(poi))
    for fb in fbs:
        recs = [r for r in contrib["rows"] if fb in r["per_bus"]]
        recs.sort(key=lambda r: (0 if r["group"].startswith("New") else 1, -r["per_bus"][fb][2]))
        for r in recs:
            yield fb, r, r["per_bus"][fb], False
        for r in contrib["groups"]:
            if fb in r["per_bus"]:
                yield fb, r, r["per_bus"][fb], True


def machine_snapshot(gens, existing):
    """Every machine considered -- the project's and the existing units -- as the
       case stands NOW: status, MVA base, PGEN/QGEN, PMAX and the source X''."""
    rows = []
    for grp, lst in (("project (new)", gens), ("existing", existing)):
        for g in lst:
            b, i = g["num"], str(g["id"])
            def _md(s):
                try:
                    ie, v = psspy.macdat(b, i, s)
                    return float(v) if ie in _OK_MAC and v is not None else None
                except Exception:
                    return None
            st = None
            try:
                ie, st = psspy.macint(b, i, "STATUS")
                st = int(st) if ie == 0 else (0 if ie == 4 else None)
            except Exception:
                st = None
            zx = None
            for fnm in ("macdt2", "macdat"):
                f = getattr(psspy, fnm, None)
                if f:
                    try:
                        ie, z = f(b, i, "ZSORCE")
                        if ie in _OK_MAC and z is not None:
                            zx = float(z.imag)
                            break
                    except Exception:
                        pass
            mb, pg, qg, pmax = _md("MBASE"), _md("P"), _md("Q"), _md("PMAX")
            rows.append({"group": grp, "bus": b, "id": i, "name": bus_name(b), "kv": basekv(b),
                         "status": ("IN" if st == 1 else "OUT" if st == 0 else "?"),
                         "mbase": mb, "pgen": pg if st != 0 else 0.0, "qgen": qg if st != 0 else 0.0,
                         "pmax": pmax, "xpp": zx,
                         # the SEQUENCE record's X'' -- the one ASCC reads when the case
                         # carries sequence data. Shown beside ZSORCE so "as the model
                         # holds it" can be checked for both records, unit by unit.
                         "xpp_seq": _read_seq_x(b, i),
                         "loading_pct": (100.0 * pg / pmax) if (pg is not None and pmax) and st != 0 else (0.0 if st == 0 else None)})
    return rows


def _onoff_by_case(snaps):
    """[(case, SGF on, SGF off, EGF on, EGF off)] -- each a list of "bus 'id'" --
       read from the machine snapshot taken in that case."""
    out = []
    for lbl, rows_m in (snaps or []):
        d = {("project (new)", "IN"): [], ("project (new)", "OUT"): [],
             ("existing", "IN"): [], ("existing", "OUT"): []}
        tot = {"project (new)": 0.0, "existing": 0.0}
        for m in rows_m:
            st = "IN" if m.get("status") == "IN" else "OUT"
            if st == "IN":
                pg = m.get("pgen") or 0.0
                tot[m["group"]] = tot.get(m["group"], 0.0) + pg
                txt = "%d '%s' %.1f MW" % (m["bus"], m["id"], pg)
            else:
                txt = "%d '%s'" % (m["bus"], m["id"])
            d.setdefault((m["group"], st), []).append(txt)
        out.append((lbl, d[("project (new)", "IN")], d[("project (new)", "OUT")],
                    d[("existing", "IN")], d[("existing", "OUT")],
                    tot.get("project (new)", 0.0), tot.get("existing", 0.0)))
    return out


def _ties_text(d):
    return "; ".join("%d%s -> POI %.1f MW / %.1f MVAr" % (o, " (3-wdg)" if w3 else "", mw, mv)
                     for o, ck, mw, mv, w3 in (d.get("ties") or [])) or "none read"


def _say_power(pw):
    lbl, d = pw
    print("  [%s] POI ties %-18s %s" % (_ts(), lbl, _ties_text(d)))
    print("  [%s]          %-18s %s" % (_ts(), "", d.get("split_how", "")))
    print("  [%s] POI power %-18s project ties %.1f MW / %.1f MVAr, existing ties %.1f MW / %.1f MVAr, "
          "TOTAL into POI %.1f MW / %.1f MVAr  (PGEN project %.1f MW, existing %.1f MW)"
          % (_ts(), lbl, d["proj_mw"], d["proj_mvar"], d["exist_mw"], d["exist_mvar"], d["total_mw"], d["total_mvar"],
             d["pgen_proj"], d["pgen_exist"]))


def _xr(x):
    if x is None:
        return ""
    return "inf" if x == float("inf") else "%.2f" % x


def _summary_text(p, sav, gens, rows, poi_row, hops, cap_notes=(), params=(), powers=(), snaps=(),
                  contrib=None, conv=(), gia_lines=()):
    L = []
    L.append("SPP SHORT-CIRCUIT STUDY -- %s" % p["name"])
    L.append("Generated %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    L.append("Case: %s" % sav)
    L.append("Method: 3-phase fault (ASCC) on every bus within %d levels of POI %d %s, %d bus(es) at/above %.0f kV; "
             "%s, %s reactance" % (HOPS, p["poi"], bus_name(p["poi"]), len(rows), MIN_FAULT_KV,
                                   ("flat %.2f pu pre-fault" % PREFAULT_VPU) if VOLTOP == 0 else
                                   ("pre-fault from the solved power flow (%s)" % (_ASCC_MODE["note"] or "VOLTOP=%d" % VOLTOP)),
                                   "subtransient" if GENXOP == 0 else "transient"))
    L.append("Project machines toggled: %s" % ", ".join("%d '%s' (MBASE %.1f)" % (g["num"], g["id"], g["mbase0"]) for g in gens))
    L.append("Existing units left online: %s" % (p.get("keep_online") or []))
    if params:
        L.append("Short-circuit model parameters (pu on machine MVA base):")
        L.append("  %-18s %s" % ("Parameter", " ".join("%14s" % ("%d '%s'" % (g["num"], g["id"])) for g in gens)))
        L.append("  %-18s %s" % ("Machine MVA base", " ".join("%14s" % _n(r[0]) for r in params)))
        L.append("  %-18s %s" % ("R (pu)", " ".join("%14s" % _n(r[1]) for r in params)))
        L.append("  %-18s %s" % ("X'' written (pu)", " ".join("%14s" % _n(r[2]) for r in params)))
        # The one the fault calculation reads. Where it differs from the line
        # above, the submitted value never reached the calculation.
        L.append("  %-18s %s" % ("X'' used (pu)",
                                 " ".join("%14s" % _n(r[3] if len(r) > 3 else None) for r in params)))
    for _rec in (_EGF_PARAMS.get(p["name"]) or []):
        L.append("Existing unit %d '%s': MBASE %s, X'' written (X Source) %s, X'' used %s -- %s"
                 % (_rec[0], _rec[1], _n(_rec[2]), _n(_rec[4]), _n(_rec[5]),
                    _rec[6].replace("existing unit (EGF): ", "")))
    L.append("Capacities studied: %s   (CAPACITY_SCOPE = %s)" % (", ".join("%d%%" % x for x in CAPACITY_PCT), CAPACITY_SCOPE))
    L.append("WITHOUT case: %s   (WITHOUT_SCOPE = %s -- \"new\" = project machines only, "
             "\"site\" = project and existing units)" % (_off_label(), WITHOUT_SCOPE))
    for pct, note in cap_notes:
        L.append("    %d%%: %s" % (pct, note))
    _cv = dict((l, (c, n)) for l, c, n in (conv or []))
    for lbl, d in (powers or []):
        _c = _cv.get(lbl, (None, ""))[0]
        L.append("POI power %-18s TOTAL into POI %.1f MW / %.1f MVAr -- SGF %.1f MW, EGF %.1f MW at the POI "
                 "(PGEN SGF %.1f MW, EGF %.1f MW; losses machines -> POI %.1f MW)%s"
                 % (lbl, d["total_mw"], d["total_mvar"], d["proj_mw"], d["exist_mw"], d["pgen_proj"], d["pgen_exist"],
                    d.get("losses_mw", 0.0),
                    "" if _c in (0, None) else "   *** POWER FLOW NOT CONVERGED -- these MW are off an unsolved case ***"))
        L.append("          ties: %s" % _ties_text(d))
        L.append("          %s" % d.get("split_how", ""))
    for _ln in (gia_lines or []):
        L.append("POI vs GIA  %s" % _ln)
    if conv:
        _bad = [l for l, c, n in conv if c not in (0, None)]
        L.append("Power flow: %s"
                 % ("every case converged" if not _bad else
                    "*** %d of %d case(s) DID NOT CONVERGE: %s. With VOLTOP = %d the fault "
                    "currents do not use the power-flow solution (flat %.2f pu prefault, Thevenin "
                    "impedance) and are unaffected; the POI MW above are. ***"
                    % (len(_bad), len(conv), ", ".join(_bad), VOLTOP, PREFAULT_VPU)))
    _oo = _onoff_by_case(snaps)
    if _oo:
        L.append("GENERATORS IN EACH CASE (SGF = new plant, EGF = existing units):")
        for lbl, sgf_on, sgf_off, egf_on, egf_off, sgf_mw, egf_mw in _oo:
            L.append("  %s   (PGEN: SGF %.1f MW, EGF %.1f MW, total %.1f MW)" % (lbl, sgf_mw, egf_mw, sgf_mw + egf_mw))
            L.append("    SGF ON : %s" % (", ".join(sgf_on) or "none"))
            L.append("    SGF OFF: %s" % (", ".join(sgf_off) or "none"))
            L.append("    EGF ON : %s" % (", ".join(egf_on) or "none"))
            L.append("    EGF OFF: %s" % (", ".join(egf_off) or "none"))
        L.append("")
    for lbl, rows_m in (snaps or []):
        L.append("Machines considered -- %s:" % lbl)
        L.append("  %-14s %-8s %-3s %-12s %6s %-4s %9s %9s %9s %9s %8s %9s %9s"
                 % ("group", "bus", "id", "name", "kV", "stat", "MBASE", "PGEN MW", "QGEN", "PMAX", "load %",
                    "X''zsrc", "X''seq"))
        for m in rows_m:
            L.append("  %-14s %-8d %-3s %-12s %6.1f %-4s %9s %9s %9s %9s %8s %9s %9s"
                     % (m["group"], m["bus"], m["id"], m["name"][:12], m["kv"] or 0.0, m["status"], _n(m["mbase"]),
                        _n(m["pgen"]), _n(m["qgen"]), _n(m["pmax"]), _n(m["loading_pct"]), _n(m["xpp"]),
                        _n(m.get("xpp_seq"))))
        L.append("  (X''zsrc = power-flow ZSORCE; X''seq = sequence record, the one ASCC reads when the case has"
                 " sequence data. Existing units are taken exactly as the model holds them; only the project"
                 " machines are written.)")
    L.append("=" * 78)
    if poi_row and poi_row.get("nogen") is not None:
        _w0 = poi_row["with"][0][1] if poi_row["with"] else None
        L.append("POI %d %s (%.0f kV) -- the three cases:"
                 % (poi_row["bus"], poi_row["name"], poi_row["kv"]))
        L.append("   1  NO GEN at the POI (SGF + EGF out) : %8.3f kA" % poi_row["nogen"])
        L.append("   2  EGF only (SGF out)                : %8.3f kA    EGF contribution %+7.3f kA"
                 % (poi_row["off"], poi_row["egf"] if poi_row["egf"] is not None else 0.0))
        if _w0 is not None:
            L.append("   3  SGF + EGF                         : %8.3f kA    SGF contribution %+7.3f kA (%+.2f%%)"
                     % (_w0, poi_row["with"][0][2], poi_row["with"][0][3]))
            L.append("      whole site (case 3 - case 1)                        %+7.3f kA"
                     % (_w0 - poi_row["nogen"]))
    elif poi_row:
        L.append("POI %d %s (%.0f kV): WITHOUT %.2f kA" % (poi_row["bus"], poi_row["name"], poi_row["kv"], poi_row["off"]))
        for pct, ka, d, pc, xr in poi_row["with"]:
            if ka is not None:
                L.append("    WITH %3d%%: %.2f kA   change %+.2f kA (%+.1f%%)" % (pct, ka, d, pc))
    for k, pct in enumerate(CAPACITY_PCT):
        vals = [r for r in _sys_rows(rows) if r["with"][k][1] is not None]
        if not vals:
            continue
        mx = max(vals, key=lambda r: r["with"][k][1])
        md = max(vals, key=lambda r: r["with"][k][2])
        L.append("WITH %d%%: highest fault current %.2f kA at %d %s (%.0f kV); largest project contribution %+.2f kA (%+.1f%%) at %d %s%s"
                 % (pct, mx["with"][k][1], mx["bus"], mx["name"], mx["kv"], md["with"][k][2], md["with"][k][3], md["bus"], md["name"],
                    "  (system buses; the plant's own lead buses excluded, as the SPP reports do)" if EXCLUDE_GEN_LEAD else ""))
    L.append("")
    for s in spp_statements({"rows": rows, "poi_row": poi_row, "project": p["name"]}):
        L.append(s)
    L.append("")
    L.append("Top %d buses by project contribution at %d%%:" % (TOP_N, CAPACITY_PCT[0]))
    L.append("  %-8s %-12s %6s %4s %9s %9s %8s %7s" % ("Bus", "Name", "kV", "hops", "WITHOUT", "WITH", "dkA", "%"))
    for r in sorted([r for r in rows if r["with"][0][1] is not None], key=lambda r: -r["with"][0][2])[:TOP_N]:
        pct, ka, d, pc, xr = r["with"][0]
        L.append("  %-8d %-12s %6.1f %4s %9.2f %9.2f %+8.3f %+6.1f%%" % (r["bus"], r["name"][:12], r["kv"], r["hops"], r["off"], ka, d, pc))
    if contrib and contrib.get("fault_buses"):
        fb = int(p["poi"]) if int(p["poi"]) in contrib["fault_buses"] else contrib["fault_buses"][0]
        nm, kv = contrib["fb_info"].get(fb, ("", 0.0))
        ia = contrib["base"].get(fb, {}).get("kA")
        L.append("")
        L.append("Per-unit contribution to a 3-phase fault at %d %s (%.0f kV) -- all units in: %s kA"
                 % (fb, nm, kv or 0.0, _n(ia)))
        L.append("  (each unit switched out alone; contribution = all-in minus unit-out; group rows take the whole group out)")
        L.append("  %-26s %-8s %-3s %-12s %9s %-14s %11s %11s %8s"
                 % ("group", "bus", "id", "name", "MBASE", "status", "unit out kA", "contrib kA", "%"))
        for _fb, r, pb, is_grp in _contrib_iter(contrib, p["poi"]):
            if _fb != fb:
                continue
            L.append("  %-26s %-8s %-3s %-12s %9s %-14s %11.3f %+11.3f %+7.2f%%"
                     % (r["group"], r["bus"], r["id"], ("" if is_grp else (r["name"] or "")[:12]), _n(r["mbase"]),
                        r["status"], pb[1], pb[2], pb[3]))
        if len(contrib["fault_buses"]) > 1:
            L.append("  (the other %d fault bus(es) are in SC_CONTRIB_%s.csv and the 'Gen contribution' sheet)"
                     % (len(contrib["fault_buses"]) - 1, p["name"]))
    return "\n".join(L) + "\n"


# ---- the SPP report tables ---------------------------------------------------
def spp_tables(R):
    """The two tables SPP prints, per capacity case:
         Table 0-1  POI:   CASE | GEN-OFF CURRENT (KA) | GEN-ON CURRENT (KA) | MAX KA CHANGE | MAX %CHANGE
         Table 0-2  by kV: VOLTAGE (KV) | MAX. CURRENT (KA) | MAX KA CHANGE | MAX %CHANGE, then a Max row
                          (the Max row is the voltage level carrying the highest current, as SPP prints it)
       Returns (poi_rows, kv_blocks) where kv_blocks = [(case label, rows)]."""
    rows, pr = R["rows"], R["poi_row"]
    poi_rows, kv_blocks = [], []
    for k, pct in enumerate(CAPACITY_PCT):
        lbl = CASE_LABEL if len(CAPACITY_PCT) == 1 else "%s %d%%" % (CASE_LABEL, pct)
        if pr and pr["with"][k][1] is not None:
            poi_rows.append([lbl, round(pr["off"], 3), round(pr["with"][k][1], 3),
                             round(abs(pr["with"][k][2]), 3), "%.2f%%" % abs(pr["with"][k][3])])
        by = {}
        for r in _sys_rows(rows):
            if r["with"][k][1] is not None:
                by.setdefault(r["nom_kv"], []).append(r)
        krows = []
        for kv in sorted(by, reverse=True):              # highest voltage first, as SPP prints it
            grp = by[kv]
            mx = max(grp, key=lambda g: g["with"][k][1])
            krows.append([("%.0f" % kv) if kv == int(kv) else ("%.1f" % kv), round(mx["with"][k][1], 3),
                          round(max(abs(g["with"][k][2]) for g in grp), 3),
                          "%.2f%%" % max(abs(g["with"][k][3]) for g in grp)])
        if krows and SPP_MAX_ROW:
            top = max(krows, key=lambda x: x[1])
            krows.append(["Max", top[1], top[2], top[3]])
        kv_blocks.append((lbl, krows))
    return poi_rows, kv_blocks


def _sys_rows(rows):
    """The rows the SPP maxima are taken over: every faulted bus, minus the
       plant's own lead (new-plant-block buses) when EXCLUDE_GEN_LEAD is on."""
    if not EXCLUDE_GEN_LEAD:
        return list(rows)
    return [r for r in rows if not _is_block_bus(r["bus"])]


def spp_statements(R):
    """The two sentences the SPP reports state under the tables, per capacity case."""
    out = []
    rows, pr = R["rows"], R["poi_row"]
    for k, pct in enumerate(CAPACITY_PCT):
        vals = [r for r in _sys_rows(rows) if r["with"][k][1] is not None]
        if not vals:
            continue
        lbl = CASE_LABEL if len(CAPACITY_PCT) == 1 else "%s %d%%" % (CASE_LABEL, pct)
        mx = max(g["with"][k][1] for g in vals)
        md = max(vals, key=lambda g: abs(g["with"][k][2]))
        mp = max(vals, key=lambda g: abs(g["with"][k][3]))
        hi = [g for g in vals if g["with"][k][1] >= HIGHLIGHT_KA]
        # SPP's own sentence bands the highlighted buses: "over 40 kA but below
        # 60 kA", the upper figure rounded up to the next 10 kA above the worst
        # of them. With none over the threshold it says so instead.
        if hi:
            _band = int(math.ceil(max(g["with"][k][1] for g in hi) / 10.0) * 10)
            _hi_txt = (" There %s %d bus%s with a maximum three-phase fault current over %.0f kA "
                       "but below %d kA. %s highlighted in Appendix B."
                       % ("was" if len(hi) == 1 else "were", len(hi),
                          "" if len(hi) == 1 else "es", HIGHLIGHT_KA, _band,
                          "This bus is" if len(hi) == 1 else "These buses are"))
        else:
            _hi_txt = (" There were no buses with a maximum three-phase fault current over %.0f kA."
                       % HIGHLIGHT_KA)
        out.append("%s: the maximum fault current calculated within %d buses of the %s POI (including the POI bus) "
                   "was %.4f kA for the %s model.%s The maximum %s contribution to three-phase fault "
                   "current was about %.4f%% (bus %d) and %.4f kA (bus %d)."
                   % (lbl, HOPS, R["project"], mx, lbl, _hi_txt,
                      R["project"], abs(mp["with"][k][3]), mp["bus"], abs(md["with"][k][2]), md["bus"]))
        if pr and pr["with"][k][1] is not None:
            out.append("%s: the %s POI bus (%s %.0f kV - %d) fault current is %.4f kA with the project online, %.4f kA without."
                       % (lbl, R["project"], pr["name"], pr["kv"], pr["bus"], pr["with"][k][1], pr["off"]))
    return out


def write_spp_tables_csv(R):
    poi_rows, kv_blocks = spp_tables(R)
    p = os.path.join(RESULTS_DIR, R["project"], "SC_SPP_TABLES_%s.csv" % R["project"])
    with open(p, "w") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["Table 0-1: POI Short Circuit Results -- %s (POI %d %s)" % (R["project"], R["poi"], bus_name_of(R, R["poi"]))])
        w.writerow(["CASE", "GEN-OFF CURRENT (KA)", "GEN-ON CURRENT (KA)", "MAX KA CHANGE", "MAX %CHANGE"])
        w.writerows(poi_rows)
        for lbl, krows in kv_blocks:
            w.writerow([])
            w.writerow(["Table 0-2: %s Short Circuit Results -- %s" % (lbl, R["project"])])
            w.writerow(["VOLTAGE (KV)", "MAX. CURRENT (KA)", "MAX KA CHANGE", "MAX %CHANGE"])
            w.writerows(krows)
        w.writerow([])
        for s in spp_statements(R):
            w.writerow([s])
    print("  -> %s" % p)


# ---- the combined report ---------------------------------------------------
def write_report_all(results):
    xp = os.path.join(RESULTS_DIR, "SC_REPORT_ALL.xlsx")
    tp = os.path.join(RESULTS_DIR, "SC_REPORT_ALL.txt")
    # Summary: one row per project x capacity
    s_hdr = ["Project", "POI bus", "POI name", "POI kV (kV)", "Buses faulted (count)", "Case", "POI fault current (kA)",
             "POI change (kA)", "POI change (%)", "Highest fault current (kA)", "at bus", "Largest change (kA)", "at bus", "Largest change (%)",
             "POI SGF (MW)", "POI EGF (MW)", "POI total (MW)", "POI total (MVAr)"]
    s_rows, poi_rows, kv_rows, pw_rows = [], [], [], []
    sheets = []
    for R in results:
        rows, pr = R["rows"], R["poi_row"]
        pws = dict(R.get("powers") or [])
        def _pw(lbl):
            d = pws.get(lbl)
            return [round(d["proj_mw"], 1), round(d["exist_mw"], 1), round(d["total_mw"], 1), round(d["total_mvar"], 1)] if d else ["", "", "", ""]
        _cv = dict((l, (c, n)) for l, c, n in (R.get("conv") or []))
        for lbl, d in (R.get("powers") or []):
            _c, _cn = _cv.get(lbl, (None, "not recorded"))
            _okt = "CONVERGED" if _c in (0, None) else "NOT CONVERGED"
            pw_rows.append([R["project"], R["poi"], lbl, round(d["total_mw"], 2), round(d["total_mvar"], 2),
                            round(d["proj_mw"], 2), round(d["proj_mvar"], 2), round(d["exist_mw"], 2), round(d["exist_mvar"], 2),
                            round(d["pgen_proj"], 2), round(d["pgen_exist"], 2), round(d.get("losses_mw", 0.0), 2),
                            _ties_text(d), d.get("split_how", ""),
                            (_okt, S_GREEN if _c in (0, None) else S_RED), _cn])
        # pr["off"] IS case 2 (the WITHOUT / EGF-only sweep), so it is labelled
        # as such -- powers[0] is the NO-GEN case when that runs, and using its
        # label here printed case 2's current under "NO GENERATION at the POI".
        _olbl = _off_label()
        if pr and pr.get("nogen") is not None:
            s_rows.append([R["project"], R["poi"], bus_name_of(R, R["poi"]), pr["kv"], len(rows),
                           "NO GENERATION at the POI", round(pr["nogen"], 3), "", "",
                           round(max((r["nogen"] or 0.0) for r in rows), 3) if rows else "",
                           max(rows, key=lambda r: (r["nogen"] or 0.0))["bus"] if rows else "", "", "", ""]
                          + _pw("NO GENERATION at the POI"))
        s_rows.append([R["project"], R["poi"], bus_name_of(R, R["poi"]), (pr["kv"] if pr else ""), len(rows), _olbl,
                       round(pr["off"], 3) if pr else "", "", "", round(max(r["off"] for r in rows), 3) if rows else "",
                       max(rows, key=lambda r: r["off"])["bus"] if rows else "", "", "", ""] + _pw(_olbl))
        for k, pct in enumerate(CAPACITY_PCT):
            vals = [r for r in rows if r["with"][k][1] is not None]
            if not vals:
                continue
            mx = max(vals, key=lambda r: r["with"][k][1]); md = max(vals, key=lambda r: r["with"][k][2])
            pw = pr["with"][k] if pr else (pct, None, None, None, None)
            s_rows.append([R["project"], R["poi"], bus_name_of(R, R["poi"]), (pr["kv"] if pr else ""), len(rows), "WITH project %d%%" % pct,
                           round(pw[1], 3) if pw[1] is not None else "", round(pw[2], 3) if pw[2] is not None else "",
                           round(pw[3], 2) if pw[3] is not None else "",
                           round(mx["with"][k][1], 3), mx["bus"], round(md["with"][k][2], 3), md["bus"], round(md["with"][k][3], 2)]
                          + _pw("WITH project %d%%" % pct))
        # POI sheet
        if pr:
            line = [R["project"], pr["bus"], pr["name"], round(pr["kv"], 1), round(pr["off"], 3)]
            for pct, ka, d, pc, xr in pr["with"]:
                line += [round(ka, 3) if ka is not None else "", round(d, 3) if d is not None else "", round(pc, 2) if pc is not None else ""]
            poi_rows.append(line)
        # per-project sheet
        # THE THREE CASES ON THE SHEET PEOPLE ACTUALLY OPEN. The dedicated
        # "Three cases" sheet carries the same numbers across every project;
        # this is the per-project view, so it gets them too rather than sending
        # the reader somewhere else for the case they just asked about.
        # WHAT WAS DISPATCHED, in the heading. A column called "WITH 100% (kA)"
        # does not say what was in service or how much it was putting into the
        # POI, so the MW each case actually ran at is in the header itself.
        _pw = dict(R.get("powers") or [])
        _three_p = R.get("three")

        def _mw(lbl):
            # machine PGEN for the split (the tie-flow split fails where SGF and
            # EGF share a pocket), the measured tie flow for what reached the POI
            d = _pw.get(lbl)
            if not d:
                return ""
            _t = d["pgen_proj"] + d["pgen_exist"]
            _at = d.get("total_mw")
            if _at is None or abs(_at - _t) < 0.5:
                return "  [SGF %.0f + EGF %.0f = %.0f MW at POI]" % (d["pgen_proj"], d["pgen_exist"], _t)
            return ("  [SGF %.0f + EGF %.0f = %.0f MW at the machines -> %.0f MW at POI]"
                    % (d["pgen_proj"], d["pgen_exist"], _t, _at))

        # _off_label(), NOT powers[0]: with the no-generation case enabled the
        # FIRST power entry is that case, so indexing [0] labelled the EGF-only
        # column with the no-gen dispatch and printed 0 MW beside real currents.
        _off_lbl_txt = _off_label()
        hdr = ["Bus", "Name", "Base kV (kV)", "Hops from POI"]
        if _three_p:
            hdr += ["1 NO GEN: SGF+EGF out (kA)%s" % _mw("NO GENERATION at the POI")]
        hdr += ["%s (kA)%s" % ("2 EGF only: SGF out" if _three_p else "WITHOUT",
                               _mw(_off_lbl_txt)),
                "X/R (ratio)"]
        for pct in CAPACITY_PCT:
            _wl = "WITH project %d%%" % pct
            hdr += ["%s (kA)%s" % ("3 SGF+EGF in" if _three_p else "WITH %d%%" % pct, _mw(_wl)),
                    "SGF contribution (kA)" if _three_p else "change (kA)",
                    "SGF contribution (%)" if _three_p else "change (%)"]
        if _three_p:
            hdr += ["EGF contribution (kA)", "Site contribution (kA)"]
        # THE VOLTAGE THE CURRENT WAS DRIVEN BY. With VOLTOP = 1 this is the solved
        # bus voltage the module used (|Ia1| x |Z1|), the number to check against
        # the one-line; it is why the figure sits ~0.5-1 % above the flat result.
        _vpf_lbl = "WITH project %d%%" % CAPACITY_PCT[0]
        _show_vpf = VOLTOP != 0 and any((r.get("vpf") or {}).get(_vpf_lbl) for r in rows)
        if _show_vpf:
            hdr += ["Pre-fault V used, case 3 (pu)"]
        prow = []
        for r in rows:
            line = [r["bus"], r["name"], round(r["kv"], 2), r["hops"]]
            if _three_p:
                line += [round(r["nogen"], 3) if r.get("nogen") is not None else ""]
            line += [round(r["off"], 3), _xr(r["xr_off"])]
            for pct, ka, d, pc, xr in r["with"]:
                line += [(round(ka, 3), S_RED if ka >= HIGHLIGHT_KA else 0) if ka is not None else "",
                         (round(d, 3), S_AMBER if (d is not None and d > 0.5) else 0) if d is not None else "",
                         round(pc, 2) if pc is not None else ""]
            if _three_p:
                _w0 = r["with"][0][1] if r["with"] else None
                _site = (_w0 - r["nogen"]) if (_w0 is not None and r.get("nogen") is not None) else None
                line += [round(r["egf"], 3) if r.get("egf") is not None else "",
                         round(_site, 3) if _site is not None else ""]
            if _show_vpf:
                _v = (r.get("vpf") or {}).get(_vpf_lbl)
                line += [round(_v, 5) if _v else ""]
            prow.append(line)
        _w = [9, 14, 8, 8] + ([16] if _three_p else []) + [16, 7] \
            + [16, 16, 14] * len(CAPACITY_PCT) + ([16, 16] if _three_p else []) \
            + ([14] if _show_vpf else [])
        sheets.append(("SC " + R["project"], hdr, prow, _w))
        # THE CONSOLE'S OWN NUMBERS, per bus and case, so a row can be checked
        # against "ASCC SHORT CIRCUIT CURRENTS" in the GUI line for line:
        #   SCMVA  = |I''k| in pu on Sbase      RE(I), IM(I) = Ia1 components
        #   Z+     = the Thevenin impedance      X/R          = its ratio
        gui_hdr = ["Bus", "Name", "Base kV (kV)", "Hops from POI", "Case",
                   "SCMVA = |I''k| (pu)", "RE(I) (pu)", "IM(I) (pu)",
                   "Z+ R (pu)", "Z+ X (pu)", "X/R", "|Z+| (pu)", "Pre-fault V = |I| x |Z| (pu)",
                   "I''k (kA)", "I base (kA per pu)"]
        grows = []
        _ib_cache = {}
        for r in rows:
            for lbl, o in (r.get("gui") or []):
                z, ia = o["z1"], o.get("ia1")
                ib = _ib_cache.setdefault(r["kv"], 100.0 / (SQRT3 * r["kv"]))   # Sbase 100 assumed only for this column
                grows.append([r["bus"], r["name"], round(r["kv"], 2), r["hops"], lbl,
                              round(abs(ia), 4) if ia is not None else round(o["kA"] / ib, 4),
                              round(ia.real, 4) if ia is not None else "",
                              round(ia.imag, 4) if ia is not None else "",
                              round(z.real, 6), round(z.imag, 6),
                              round(z.imag / z.real, 5) if abs(z.real) > 1e-9 else "",
                              round(abs(z), 7),
                              round(o["vpf"], 5) if o.get("vpf") else "",
                              round(o["kA"], 4), round(ib, 7)])
        sheets.append(("GUI pu " + R["project"], gui_hdr, grows,
                       [9, 14, 8, 8, 26, 14, 11, 11, 11, 11, 9, 11, 16, 10, 12]))
        # max by kV (system buses -- the plant's own lead excluded, as the reports do)
        by = {}
        for r in _sys_rows(rows):
            by.setdefault(r["nom_kv"], []).append(r)
        for kv in sorted(by, reverse=True):
            grp = by[kv]
            line = [R["project"], kv, len(grp), round(max(g["off"] for g in grp), 3)]
            for k, pct in enumerate(CAPACITY_PCT):
                vals = [g for g in grp if g["with"][k][1] is not None]
                line += [round(max(g["with"][k][1] for g in vals), 3) if vals else "",
                         round(max(g["with"][k][2] for g in vals), 3) if vals else "",
                         round(max(g["with"][k][3] for g in vals), 2) if vals else ""]
            kv_rows.append(line)
    poi_hdr = ["Project", "POI bus", "POI name", "Base kV (kV)", "%s (kA)" % _off_label()]
    kv_hdr = ["Project", "Nominal kV (kV)", "Buses (count)", "Max WITHOUT (kA)"]
    for pct in CAPACITY_PCT:
        poi_hdr += ["WITH %d%% (kA)" % pct, "change (kA)", "change (%)"]
        kv_hdr += ["Max WITH %d%% (kA)" % pct, "Max change (kA)", "Max change (%)"]
    spp_rows = []
    for R_ in results:
        write_spp_tables_csv(R_)
        # A DIFFERENT poi_rows. spp_tables() returns the SPP appendix rows, and
        # binding them to `poi_rows` overwrote the POI SHEET's rows, which are
        # accumulated above and have a different shape -- so the POI sheet was
        # written with the appendix's 5 columns under its own 8 headings, and
        # every value sat three columns to the left of its title.
        _spp_poi, _spp_kv = spp_tables(R_)
        spp_rows.append([("%s -- Table 0-1: POI Short Circuit Results (POI %d %s)" % (R_["project"], R_["poi"], bus_name_of(R_, R_["poi"])), S_BOLD)])
        spp_rows.append([(x, S_HEAD) for x in ("CASE", "GEN-OFF CURRENT (KA)", "GEN-ON CURRENT (KA)", "MAX KA CHANGE", "MAX %CHANGE")])
        spp_rows.extend(_spp_poi)
        for lbl, krows in _spp_kv:
            spp_rows.append([])
            spp_rows.append([("%s -- Table 0-2: %s Short Circuit Results" % (R_["project"], lbl), S_BOLD)])
            spp_rows.append([(x, S_HEAD) for x in ("VOLTAGE (KV)", "MAX. CURRENT (KA)", "MAX KA CHANGE", "MAX %CHANGE")])
            for kr in krows:
                spp_rows.append([(x, S_BOLD) for x in kr] if kr[0] == "Max" else kr)
            if EXCLUDE_GEN_LEAD:
                spp_rows.append([("* For buses not on the generation interconnection line", S_GREY)])
        spp_rows.append([])
        for s in spp_statements(R_):
            spp_rows.append([s])
        spp_rows.append([]); spp_rows.append([])
    mach_rows = []
    for R_ in results:
        for lbl, rows_m in (R_.get("snaps") or []):
            for m in rows_m:
                mach_rows.append([R_["project"], lbl, m["group"], m["bus"], m["id"], m["name"], round(m["kv"] or 0.0, 1),
                                  (m["status"], S_GREEN if m["status"] == "IN" else S_GREY),
                                  round(m["mbase"], 2) if m["mbase"] is not None else "",
                                  round(m["pgen"], 2) if m["pgen"] is not None else "",
                                  round(m["qgen"], 2) if m["qgen"] is not None else "",
                                  round(m["pmax"], 2) if m["pmax"] is not None else "",
                                  round(m["loading_pct"], 1) if m["loading_pct"] is not None else "",
                                  round(m["xpp"], 4) if m["xpp"] is not None else "",
                                  round(m["xpp_seq"], 4) if m.get("xpp_seq") is not None else "n/a"])
    onoff_rows = []
    for R_ in results:
        for lbl, sgf_on, sgf_off, egf_on, egf_off, sgf_mw, egf_mw in _onoff_by_case(R_.get("snaps")):
            onoff_rows.append([R_["project"], lbl,
                               "%d of %d" % (len(sgf_on), len(sgf_on) + len(sgf_off)), round(sgf_mw, 2),
                               ", ".join(sgf_on) or "none", ", ".join(sgf_off) or "none",
                               "%d of %d" % (len(egf_on), len(egf_on) + len(egf_off)), round(egf_mw, 2),
                               ", ".join(egf_on) or "none", ", ".join(egf_off) or "none",
                               round(sgf_mw + egf_mw, 2)])
    prm_rows = []
    for R_ in results:
        for g, rec in zip(R_["gens"], R_.get("params") or []):
            mb, zr, zx = rec[0], rec[1], rec[2]
            _sq = rec[3] if len(rec) > 3 else None
            _note = ("submitted values applied"
                     if any(g.get(k) is not None for k in ("mbase", "r", "xpp"))
                     else "as in case")
            # THE ONE THAT DECIDES THE ANSWER. If the sequence reactance is not
            # the one submitted, the fault calculation is not using it -- see
            # _read_seq_x() -- and the project will look as though it adds
            # nothing.
            if _sq is not None and zx is not None and abs(_sq - zx) > max(0.01, 0.05 * abs(zx)):
                _note += ("  *** the FAULT CALCULATION uses X''=%.4g, not %.4g -- "
                          "the sequence record was not updated ***" % (_sq, zx))
            prm_rows.append([R_["project"], g["num"], g["id"], round(mb, 4) if mb is not None else "n/a",
                             round(zr, 6) if zr is not None else "n/a", round(zx, 6) if zx is not None else "n/a",
                             round(_sq, 6) if _sq is not None else "n/a", _note])
        # THE EXISTING UNITS as the fault calculation sees them (EGF_XPP_BY_PROJECT)
        for _num, _mid, _mb, _zr, _zx, _sx, _enote in (R_.get("egf_params") or []):
            prm_rows.append([R_["project"], _num, _mid, round(_mb, 4) if _mb is not None else "n/a",
                             round(_zr, 6) if _zr is not None else "n/a",
                             round(_zx, 6) if _zx is not None else "n/a",
                             round(_sx, 6) if _sx is not None else "n/a", _enote])
    contrib_rows = []
    for R_ in results:
        C = R_.get("contrib")
        if not C:
            continue
        for fb, r, pb, is_grp in _contrib_iter(C, R_["poi"]):
            nm, kv = C["fb_info"].get(fb, ("", 0.0))
            st = S_BOLD if is_grp else 0
            contrib_rows.append([(R_["project"], st), (fb, st), (nm, st), (round(kv or 0.0, 1), st),
                                 (r["bus"], st), (r["id"], st), (r["name"], st), (r["group"], st),
                                 (round(r["mbase"], 2) if r["mbase"] is not None else "", st),
                                 (r["status"], S_GREEN if r["status"] == "IN" else S_GREY),
                                 (round(pb[0], 3), st), (round(pb[1], 3), st),
                                 (round(pb[2], 3), S_AMBER if (not is_grp and pb[2] > 0.5) else st),
                                 (round(pb[3], 2), st)])
    # ---- APPENDIX B (Table B-1), exactly the SPP column order ----------------
    #
    #   Bus Number | Bus Name | Bus kV | Area | Zone |
    #   3 Phase Fault Current (kA): Gen ON | GEN OFF |
    #   Difference (ON-OFF): Change | % |
    #   Distance from GEN POI Bus <poi> | Greater than 40 kA Current
    #
    # Sorted by Change descending, which is how every SPP surplus appendix is
    # ordered -- the buses the SGF moves most, first.
    apx_rows = []
    for R_ in results:
        _pr = [r for r in R_["rows"] if r["with"] and r["with"][0][1] is not None]
        for r in sorted(_pr, key=lambda x: -x["with"][0][2]):
            on, ch, pc = r["with"][0][1], r["with"][0][2], r["with"][0][3]
            apx_rows.append([
                r["bus"], r["name"], round(r["kv"], 2),
                bus_area(r["bus"]), bus_zone(r["bus"]),
                (round(on, 4), S_RED if on >= HIGHLIGHT_KA else 0),
                round(r["off"], 4),
                (round(ch, 4), S_AMBER if ch > 0.5 else 0), round(pc, 6),
                r["hops"],
                "TRUE" if on >= HIGHLIGHT_KA else "FALSE",
                R_["project"]])
    apx_hdr = ["Bus Number", "Bus Name", "Bus kV", "Area", "Zone",
               "Gen ON (kA)", "GEN OFF (kA)", "Change (kA)", "% Change",
               "Distance from POI Bus", "Greater than %.0f kA Current" % HIGHLIGHT_KA,
               "Project"]

    # ---- THE THREE CASES, one row per bus per project ------------------------
    three_rows = []
    for R_ in results:
        if not R_.get("three"):
            continue
        for r in R_["rows"]:
            w0 = r["with"][0][1] if r["with"] else None
            if r.get("nogen") is None or w0 is None:
                continue
            site = w0 - r["nogen"]
            three_rows.append([
                R_["project"], r["bus"], r["name"], round(r["kv"], 2), r["hops"],
                round(r["nogen"], 3), round(r["off"], 3),
                (round(w0, 3), S_RED if w0 >= HIGHLIGHT_KA else 0),
                round(r["egf"], 3) if r["egf"] is not None else "",
                (round(r["with"][0][2], 3), S_AMBER if r["with"][0][2] > 0.5 else 0),
                round(r["with"][0][3], 2),
                round(site, 3),
                round(site / r["nogen"] * 100.0, 2) if r["nogen"] else ""])
    # POI MW PER CASE IN THE HEADINGS. With several projects on one sheet the
    # dispatch differs project to project, so the header states the RANGE across
    # the projects shown rather than one project's number.
    def _mw_range(lbl):
        vals = []
        for R_ in results:
            d = dict(R_.get("powers") or {}).get(lbl) if isinstance(R_.get("powers"), dict) \
                else dict(R_.get("powers") or []).get(lbl)
            if d:
                vals.append(d["total_mw"] if d.get("total_mw") is not None else d["pgen_proj"] + d["pgen_exist"])
        if not vals:
            return ""
        lo, hi = min(vals), max(vals)
        return "  [POI %.0f MW]" % lo if abs(hi - lo) < 0.5 else "  [POI %.0f-%.0f MW]" % (lo, hi)

    three_hdr = ["Project", "Bus", "Name", "Base kV (kV)", "Hops from POI",
                 "1 NO GEN: SGF+EGF out (kA)%s" % _mw_range("NO GENERATION at the POI"),
                 "2 EGF only: SGF out (kA)%s" % _mw_range(_off_label()),
                 "3 SGF+EGF in (kA)%s" % _mw_range("WITH project %d%%" % CAPACITY_PCT[0]),
                 "EGF contribution (kA)", "SGF contribution (kA)", "SGF contribution (%)",
                 "Site contribution (kA)", "Site contribution (%)"]

    method = [["Method", "3-phase bolted fault applied with the PSS/E Automatic Sequence Fault Calculation (ASCC) at every bus within %d levels of the POI (at/above %.0f kV), with and without the project generators online; %s." % (HOPS, MIN_FAULT_KV, "the existing units at the POI are taken out with the project, so the WITHOUT case has no generation at this POI at all" if (WITHOUT_SCOPE or "new").lower() == "site" else "existing units at the POI left online in both cases")],
              ["WITHOUT case", "%s (WITHOUT_SCOPE = %s): \"new\" takes out the project machines only, so the change is what the new plant adds beside the generation already there; \"site\" takes out the project machines and the existing units together, so the change is the contribution of the whole plant. Units saved out of service stay out." % (_off_label(), WITHOUT_SCOPE)],
              ["Pre-fault", ("%s (VOLTOP=%d); machine reactance: %s (GENXOP=%d)%s"
                             % ("solved power-flow bus voltages -- the ASCC dialog's default \"From power flow\"" if VOLTOP != 0
                                else "flat %.2f pu at every bus (\"FLAT Classical\")" % PREFAULT_VPU,
                                VOLTOP, "subtransient X''" if GENXOP == 0 else "transient X'", GENXOP,
                                ("; extra ASCC options: %s" % ASCC_EXTRA_OPTS) if ASCC_EXTRA_OPTS else ""))],
              ["Fault current", (("%s, in kA on the system MVA base and the bus base kV; the prefault voltage each current was "
                                  "driven by is printed per bus on the SC sheets. Probe result: %s"
                                  % ("|Ia1| as the ASCC module reports it (the GUI's Sym I''k rms)" if _ASCC_MODE["how"] == "ia1"
                                     else "V_solved(bus) / |Z1 thevenin| -- the solved bus voltage over the Thevenin impedance",
                                     _ASCC_MODE["note"] or "(no sweep run)"))
                                 if VOLTOP != 0 else
                                 "I = E / |Z1 thevenin| from the ASCC result, E = %.2f pu, in kA on the system MVA base and the bus base kV" % PREFAULT_VPU)],
              ["Maxima", ("EXCLUDE_GEN_LEAD = %s. %s" % (EXCLUDE_GEN_LEAD,
                          "The maximum-contribution sentence, the by-voltage table and the Max-by-kV sheet are taken over "
                          "system buses only -- the plant's own lead (new-plant-block buses) is left out, as the SPP reports "
                          "footnote (\"for buses not on the generation interconnection line\"). Appendix B and the per-bus SC "
                          "sheets list every faulted bus." if EXCLUDE_GEN_LEAD else
                          "Every faulted bus, including the plant's own collector / HV tie bus, is eligible for the maxima."))],
              ["EGF in service", ("EGF_ENERGIZE = %s. %s Units switched in this run: %s"
                                  % (EGF_ENERGIZE,
                                     "Every keep_online unit is put in service for the EGF-in cases (2 and 3) and the per-unit "
                                     "contribution, whatever status the case saved it with, and put back afterwards. The EGF's "
                                     "fault contribution is set by which units are CONNECTED -- each is a parallel source branch "
                                     "-- not by their MW, so an EGF unit left out contributes nothing however the rest are dispatched."
                                     if EGF_ENERGIZE else
                                     "A keep_online unit saved out of service stays out in every case and contributes nothing.",
                                     "; ".join("%s: %s" % (R["project"], ", ".join(R.get("energized_egf") or []) or "none")
                                               for R in results) or "(none)"))],
              ["EGF X''", ("EGF_XPP_BY_PROJECT = %r. %s"
                           % (EGF_XPP_BY_PROJECT or {},
                              "The X'' named there is written on that project's existing units before the sweeps -- "
                              "the short-circuit data's Subtransient X (the reactance ASCC uses) and the power-flow "
                              "X Source; R and MVA base unchanged. Every other existing unit is studied with the X'' "
                              "the case holds. Each unit is on the SC model parameters sheet."
                              if EGF_XPP_BY_PROJECT else
                              "Every existing unit is studied with the X'' the case holds (SC model parameters sheet)."))],
              ["Capacity cases", "WITH project at %s; CAPACITY_SCOPE = %s (new = project machines only; both = project and existing units each scaled; site = existing kept, project scaled so the site total meets the percentage). MVA base scaled; source impedance in pu on MBASE unchanged." % (", ".join("%d%%" % x for x in CAPACITY_PCT), CAPACITY_SCOPE)],
              ["Cases", "; ".join("%s: %s (POI %d)" % (R["project"], os.path.basename(R["sav"]), R["poi"]) for R in results)],
              ["Per-unit contribution", "With every unit in service (the WITH-project state) each machine -- existing units and project machines -- is switched out alone and the bus(es) in CONTRIB_FAULT_BUSES (%s) faulted again; the drop in fault current is that unit's contribution, the report's ON-minus-OFF definition applied unit by unit. Group rows take all new / all existing units out together (the all-new row equals the WITHOUT column). Sources feed a fault in parallel, so per-unit values do not sum exactly to the group total. A unit saved out of service is never switched on. %s" % (CONTRIB_FAULT_BUSES, "" if PER_MACHINE_CONTRIB else "(PER_MACHINE_CONTRIB is off -- not run)")],
              ["Three cases", ("RUN_NOGEN_CASE = %s. Each project is studied in three states: "
                               "(1) NO GENERATION at the POI -- SGF and EGF both out; "
                               "(2) EGF ONLY -- SGF out, existing units in (SPP's \"EGF in service, SGF offline\"); "
                               "(3) SGF + EGF -- everything in service at full MVA (SPP's modified model). "
                               "SGF contribution = case3 - case2, EGF contribution = case2 - case1, whole site = case3 - case1. "
                               "At the POI the sources are in parallel, so the SGF and EGF contributions add to the site total; "
                               "further out the network between makes that only approximate."
                               % RUN_NOGEN_CASE)],
              ["EGF dispatch", ("EGF_DISPATCH = %r. \"full\" dispatches the existing units to PMAX in case 2, scaled back "
                                "proportionally to each project's gia_mw where one is given, and restores the case's own "
                                "dispatch before case 3; \"asis\" leaves the power-flow dispatch alone. %s "
                                "GIA limits used: %s"
                                % (EGF_DISPATCH,
                                   ("With VOLTOP = %d the fault currents are driven by the solved bus voltages, so re-dispatching "
                                    "the same units moves them only through the voltage -- a tenth of a percent or so; ASCC works "
                                    "from source impedance, not MW. Which EGF units are CONNECTED is what moves them (EGF_ENERGIZE)."
                                    % VOLTOP) if VOLTOP != 0 else
                                   ("With VOLTOP = 0 (flat %.2f pu prefault) this does not change any fault current -- ASCC works "
                                    "from source impedance, not MW -- so it documents the operating point rather than altering the duty."
                                    % PREFAULT_VPU),
                                   ", ".join("%s %s MW" % (R["project"], R.get("gia_mw") if R.get("gia_mw") else "(none)")
                                             for R in results) or "(none)"))],
              ["POI vs GIA", ("POI_GIA_CHECK = %s, tolerance %.1f MW. Per project -- target (GIA), SGF rating, "
                              "EGF remainder, and what the SGF+EGF case actually injected: %s"
                              % (POI_GIA_CHECK, POI_GIA_TOL_MW,
                                 "; ".join(
                                     "%s: GIA %s, SGF %s, EGF remainder %s, achieved %s%s"
                                     % (R["project"],
                                        R.get("gia_mw") if R.get("gia_mw") else "(not set)",
                                        R.get("sgf_mw") if R.get("sgf_mw") else "(not set)",
                                        ("%.1f" % (float(R["gia_mw"]) - float(R["sgf_mw"])))
                                        if (R.get("gia_mw") and R.get("sgf_mw")) else "(n/a)",
                                        ("%.1f MW" % dict(R.get("powers") or []).get(
                                            "WITH project %d%%" % CAPACITY_PCT[0], {}).get("total_mw", float("nan"))
                                         if dict(R.get("powers") or []).get("WITH project %d%%" % CAPACITY_PCT[0])
                                         else "(n/a)"),
                                        "" if R.get("gia_ok", True) else "  *** EXCEEDS THE GIA ***")
                                     for R in results) or "(none)"))],
              ["POI injection (SPP scenario 2)",
               ("POI_DISPATCH = %r. SPP: \"the EGF and SGF were dispatched proportionally to set the POI "
                "injection to not exceed the Interconnection service amount.\" With \"gia\" every in-service "
                "machine at the POI is scaled by the SAME factor until their total meets the project's "
                "gia_mw -- downward only, so a case already inside the limit is untouched. GIA per project: "
                "%s. What each case actually injected is on the POI power sheet."
                % (POI_DISPATCH,
                   ", ".join("%s %s" % (R["project"], R.get("gia_mw") if R.get("gia_mw") else "(not set)")
                             for R in results) or "(none)"))],
              ["Convergence", ("REQUIRE_CONVERGENCE = %r, SOLVE_MAX_TRIES = %d. Each case is solved with the "
                               "decoupled solver then full Newton, repeated until it converges or the tries run out. %s "
                               "Per-case status is on the POI power sheet. THIS RUN: %s"
                               % (REQUIRE_CONVERGENCE, SOLVE_MAX_TRIES,
                                  ("With VOLTOP = %d EVERY fault current is driven by the solved prefault voltages, so a case "
                                   "that does not converge is fatal under \"auto\": the project stops at that case." % VOLTOP)
                                  if VOLTOP != 0 else
                                  ("With VOLTOP = 0 the fault currents are computed from a flat %.2f pu prefault and the "
                                   "network's Thevenin impedance, so they do NOT read the power-flow solution; the POI MW "
                                   "do. \"auto\" therefore aborts a project only when VOLTOP is not 0." % PREFAULT_VPU),
                                  ("; ".join("%s %s" % (R["project"],
                                                        "all cases converged" if R.get("conv_ok")
                                                        else "*** " + (R.get("conv_note") or "did not converge") + " ***")
                                             for R in results) or "no project reported")))],
              ["Written", time.strftime("%Y-%m-%d %H:%M:%S")]]
    _three_sheet = [("Three cases", three_hdr, three_rows,
                     [14, 9, 14, 8, 8, 13, 14, 14, 16, 16, 16, 16, 16])] if three_rows else []
    _apx_sheet = [("Appendix B", apx_hdr, apx_rows,
                   [11, 16, 8, 7, 7, 12, 13, 12, 11, 20, 24, 14])] if apx_rows else []
    write_xlsx(xp, [("SPP tables", ["", "", "", "", ""], spp_rows, [22, 22, 22, 16, 14])]
                   + _apx_sheet + _three_sheet + [
                    ("Summary", s_hdr, s_rows, [14, 9, 14, 7, 9, 18, 14, 12, 12, 14, 9, 14, 9, 8, 12, 12, 12, 12]),
                    ("Gens on-off by case", ["Project", "Case", "SGF in service", "SGF PGEN (MW)",
                                             "SGF ON (PGEN MW)", "SGF OFF",
                                             "EGF in service", "EGF PGEN (MW)", "EGF ON (PGEN MW)", "EGF OFF",
                                             "Total PGEN (MW)"], onoff_rows,
                     [14, 20, 10, 12, 52, 36, 10, 12, 52, 36, 12]),
                    ("Machines", ["Project", "Case", "Group", "Bus", "Id", "Bus name", "Base kV (kV)", "Status",
                                  "MVA base (MVA)", "PGEN (MW)", "QGEN (MVAr)",
                                  "PMAX (MW)", "Loading (% of PMAX)", "X'' ZSORCE (pu on MBASE)",
                                  "X'' sequence record (pu on MBASE)"], mach_rows,
                     [14, 18, 14, 9, 5, 14, 7, 8, 10, 10, 11, 10, 14, 16, 18]),
                    ("POI power", ["Project", "POI bus", "Case",
                                   "TOTAL into POI (MW)", "TOTAL into POI (MVAr)",
                                   "SGF at POI (MW)", "SGF at POI (MVAr)",
                                   "EGF at POI (MW)", "EGF at POI (MVAr)",
                                   "PGEN SGF (MW)", "PGEN EGF (MW)", "Losses machines to POI (MW)",
                                   "Each tie into the POI (MW / MVAr, read at the POI)",
                                   "How SGF / EGF are separated",
                                   "Power flow", "Solver"], pw_rows,
                     [14, 9, 18, 14, 14, 12, 12, 12, 12, 12, 12, 14, 60, 60, 14, 30]),
                    ("POI", poi_hdr, poi_rows, [14, 9, 14, 7, 12] + [12, 11, 10] * len(CAPACITY_PCT)),
                    ("Max by kV", kv_hdr, kv_rows, [14, 10, 7, 14] + [14, 14, 12] * len(CAPACITY_PCT)),
                    ("Gen contribution", ["Project", "Fault bus", "Fault bus name", "Fault kV (kV)", "Unit bus", "Id",
                                          "Unit name", "Group", "MVA base (MVA)", "Status", "All units in (kA)",
                                          "Unit out (kA)", "Contribution (kA)", "Contribution (%)"], contrib_rows,
                     [14, 9, 14, 8, 9, 5, 14, 24, 10, 14, 12, 12, 13, 12])]
               + sheets + [("SC model parameters", ["Project", "Machine bus", "Id", "Machine MVA base", "R (pu on MBASE)",
                                                   "X'' written (ZSORCE)", "X'' used by the fault calc", "Source"],
                            prm_rows, [14, 12, 5, 16, 16, 18, 22, 46]),
                           ("Method", ["Item", "Description"], method, [16, 150])])
    with open(tp, "w") as f:
        f.write("SPP SHORT-CIRCUIT STUDY -- ALL PROJECTS\n%s\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        f.write(method[0][1] + "\n" + method[1][1] + "\n\n")
        f.write("%-14s %-8s %-14s %6s %-18s %10s %10s %8s\n" % ("Project", "POI", "Name", "kV", "Case", "POI kA", "dkA", "%"))
        for r in s_rows:
            f.write("%-14s %-8s %-14s %6s %-18s %10s %10s %8s\n" % (r[0], r[1], r[2][:14], r[3], r[5], r[6], r[7], r[8]))
        for R in results:
            f.write("\n" + open(os.path.join(RESULTS_DIR, R["project"], "SC_SUMMARY_%s.txt" % R["project"])).read())
    print("\nReport -> %s\n       -> %s" % (xp, tp))


def bus_name_of(R, b):
    return R["poi_row"]["name"] if R.get("poi_row") and R["poi_row"]["bus"] == b else ""


def make_plots(results):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.backends.backend_pdf import PdfPages
    except Exception as e:
        print("  plots skipped (matplotlib not available: %s)" % e)
        return
    pp = os.path.join(RESULTS_DIR, "SC_REPORT_ALL_plots.pdf")
    with PdfPages(pp) as pdf:
        for R in results:
            rows = R["rows"]
            if not rows:
                continue
            by = {}
            for r in rows:
                by.setdefault(r["nom_kv"], []).append(r)
            kvs = sorted(by, reverse=True)
            fig, ax = plt.subplots(figsize=(10, 5))
            n = 1 + len(CAPACITY_PCT); wbar = 0.8 / n
            ax.bar([i - 0.4 + wbar / 2 for i in range(len(kvs))], [max(g["off"] for g in by[k]) for k in kvs], wbar, label=_off_label(), color="#888888")
            cols = ["#c0392b", "#e67e22", "#f1c40f", "#2980b9"]
            for j, pct in enumerate(CAPACITY_PCT):
                ax.bar([i - 0.4 + wbar * (j + 1.5) for i in range(len(kvs))],
                       [max([g["with"][j][1] or 0 for g in by[k]]) for k in kvs], wbar, label="WITH %d%%" % pct, color=cols[j % len(cols)])
            ax.set_xticks(range(len(kvs))); ax.set_xticklabels(["%.0f kV" % k for k in kvs])
            ax.set_ylabel("Maximum 3-phase fault current (kA)"); ax.set_title("%s -- maximum fault current by voltage level" % R["project"])
            ax.grid(alpha=.3, axis="y"); ax.legend(); fig.tight_layout(); pdf.savefig(fig); plt.close(fig)
            top = sorted([r for r in rows if r["with"][0][2] is not None], key=lambda r: -r["with"][0][2])[:TOP_N]
            if top:
                fig, ax = plt.subplots(figsize=(max(10, len(top) * 0.5), 5))
                ax.bar(range(len(top)), [r["with"][0][2] for r in top], color="#2980b9")
                ax.set_xticks(range(len(top))); ax.set_xticklabels(["%d\n%s" % (r["bus"], r["name"][:8]) for r in top], rotation=90, fontsize=6)
                ax.set_ylabel("Project contribution (kA)"); ax.set_title("%s -- top %d buses by contribution at %d%%" % (R["project"], len(top), CAPACITY_PCT[0]))
                ax.grid(alpha=.3, axis="y"); fig.tight_layout(); pdf.savefig(fig); plt.close(fig)
    print("  plots -> %s" % pp)


def main():
    projects = PROJECTS_SC or projects_from_dynamic_study()
    print("=" * 72 + "\n SPP SHORT-CIRCUIT STUDY  (%d project(s), %d levels, capacities %s)\n study dir %s\n results   %s\n" % (len(projects), HOPS, CAPACITY_PCT, STUDY_DIR, RESULTS_DIR) + "=" * 72)
    psse_init()
    results, failed = [], []
    for p in projects:
        try:
            _r = study_project(p)
            if _r is not None:
                results.append(_r)
        except Exception as e:
            failed.append((p["name"], str(e)))
            print("*** %s FAILED: %s ***" % (p["name"], e)); traceback.print_exc()
            _restore_energized()
    if _SAVED_CASES:
        print("[%s] %d case file(s) saved:" % (_ts(), len(_SAVED_CASES)))
        for _n_, _t, _l, _pth in _SAVED_CASES:
            print("   %-14s %-26s %s" % (_n_, _t, _pth))
    if results:
        write_report_all(results)
        if MAKE_PLOTS:
            make_plots(results)
    for n, e in failed:
        print("FAILED: %s -- %s" % (n, e))
    print("[%s] DONE  (%d ok, %d failed)   log: %s" % (_ts(), len(results), len(failed), LOG_FILE))
    return 0 if not failed else 1


if __name__ == "__main__":
    rc = 1
    try:
        rc = main()
    except Exception as e:
        print("RUN FAILED: %s" % e); traceback.print_exc()
    finally:
        try:
            sys.stdout, sys.stderr = _ORIG_OUT, _ORIG_ERR
            _LOG_FH.close()
        except Exception:
            pass
    sys.exit(rc)
