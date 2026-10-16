# -*- coding: utf-8 -*-
"""Plant Model Builder: SMIB and DETAILED plant models for PSS/E, PSLF, TSAT and PSCAD from one set of inputs.

   One plant -- any number of generators (PV, BESS, wind, synchronous), each
   with its own units, GSUs and collector system, on one or more main power
   transformers (MPT), a gen-tie and the Point of Interconnection (POI) -- is
   written as a Single Machine Infinite Bus (SMIB) model for both programs,
   from the same data and the same load-flow solution.

   THE INPUT: an Excel workbook (.xlsx)
       python smib_build.py --template SMIB_INPUT.xlsx
   writes one, filled in with an example (a hybrid plant: 100 MW PV and
   100 MW BESS on one MPT, one plant controller) and a README sheet that
   explains every column. Sheets:
     Plant          name, bases, POI, grid (SCR and X/R), gen-tie, the
                    operating point (CONTROL), the plant controller, PSCAD
                    run settings
     MPT            the main power transformers, 2 or 3 windings
     Generators     one row per generator: units, GSU, collector, Q control,
                    the electrical control model, the PSCAD model
     Feeders        the collector systems section by section (WECC)
     Cables         cable data per length
     Collectors     collector systems shared by more than one generator
     Loads_Shunts   auxiliary loads and shunts (capacitor banks, reactors)
     Machines       DETAILED: a machine's own Pgen, Pmax, Pmin, Qmax, Qmin, Q, MBASE, ID
     Bus_Numbers    bus numbers / names of your own (blank = automatic)
     Dyr_Params     any .dyr model parameter, by generator, model and name
     Dyr_Records    .dyr records of your own (vendor models)
     PSCAD_Params   the vendor PSCAD model's parameters, by generator

   THE MODEL -- Plant: Model type, the same for PSS/E and PSCAD
     SMIB (default):
     IB --[grid: SCR, X/R]-- POI --[gen-tie]-- HV --[MPT 1..n]-- collector bus
        --[each generator's own equivalent feeder]-- MV --[GSU]-- LV -- generator
     IB is the infinite bus (the swing bus; GENCLS with H = 0 in the .dyr).
     Each generator is N units aggregated (MBASE, P, Q = N x one unit) behind
     its GSUs in parallel (count x MVA, the same % impedance), on a collector
     equivalent of its own (WECC, each feeder weighed by the MW it carries)
     from its MPT winding; a generator's collector systems on one winding are
     one equivalent.
     Generators can share an MPT (its LV or tertiary winding), have MPTs of
     their own, share a collector system, or sit on any bus.
     DETAILED: every feeder section as a branch (identical feeder copies
     lumped); the units of a generator at each node a machine of their own
     (its generator's unit data and dynamic models, P in proportion) behind
     its own GSU (the generator's, scaled). Rows in Dyr_Params, Dyr_Records
     and PSCAD_Params that name a generator apply to all of its machines;
     Machines gives one machine values of its own. Scale up (GUI) or --scale:
     a test plant -- MPTs (copies of the first, with its generators), feeders
     per MPT, GSUs per feeder, inverters per GSU.

   WECC COLLECTOR EQUIVALENT
     Each collector system is given feeder by feeder, section by section from
     the collector bus out (cable, length, units tapped at the far end; a
     lateral names the section it hangs from). The equivalent branch is the
     WECC one (WECC Wind / PV Plant Power Flow Modeling Guides; Muljadi et al.,
     NREL):
         Zeq = sum(Zi * Wi^2) / W^2       Beq = sum(Bi)
     Wi = rated MW of the units downstream of section i (the unit count for
     equal units), W = all of it. The detailed feeders are solved too, every
     unit at full output, and their losses and voltages are set against the
     equivalent's (the WECC check in the report). An equivalent already worked
     out can be given instead.

   THE OPERATING POINT
     A load flow of this script's own (no PSS/E needed) sets the generators' P
     and Q and the infinite-bus voltage so that the POI meets the targets: the
     POI voltage and Q (or power factor) at the POI, or the POI voltage with
     the grid at a given voltage; P at the POI or each generator's dispatch.
     Generators with Q mode "share" carry the POI's Q in proportion to their
     Qmax, and regulate the POI in PSS/E with the same shares (RMPCT). The
     PSCAD sources are set from the same solution: both programs start from
     the same point.

   PSS/E
     <name>.raw   rev 33 (PSS/E 33, 34 and 35 read it) -- network and solution
     <name>.dyr   GENCLS (the infinite bus) and, per generator, WECC-approved
                  generic models only:
                    REGC_A   REGCA1 (REGCAU1: Plant, REGC_A / REPC_A records)
                    REEC     Generators, Electrical control:
                               REEC_A  REECA1    PV, WT4, WT3 (blank: these)
                               REEC_C  REECCU1   BESS (blank: BESS)
                               REEC_D  REECDU1   PV, BESS, WT4
                    REPC     Plant, Plant controller:
                               REPC_A  REPCA1 (REPCAU1) on each generator
                               REPC_B  one for the plant -- PLNTBU1 at the
                                       POI and REAX4BU1 on each generator
                                       (hybrid plants: PV + BESS)
                    WT3      + WTDTA1, WTARA1, WTPTA1, WTTQA1
                    SYNC     GENROU + SEXS + TGOV1 (Machine, Exciter, Governor
                             model: GENSAL; IEEET1, ESST1A, EXST1; HYGOV, GAST,
                             IEEEG1; NONE)
                  REECCU1, REECDU1, PLNTBU1, REAX4BU1, REGCAU1 and REPCAU1
                  are USRMDL / USRBUS records (PSS/E 34 / 35 layouts): the
                  WECC user-model DLL must be loaded (ADDLIB_IDV / DLLS).
                  Limits from the unit data, any parameter from Dyr_Params --
                  and your own records (Dyr_Records) with {BUS}, {ID}, {POI} ...
     With PSS/E found (PSSE_CHECK; PSSE_VERSION = 35: PSS/E 35): the .raw read
     back and solved against this script's load flow, <name>.sav and
     <name>_psse35.raw (PSS/E 35's own format) saved, the .dyr read
     (<name>.snp saved), initialised and run flat for FLAT_RUN_S seconds.
     PSS/E 35's psspy is for set Pythons (its PSSPY39, PSSPY310 ... folders):
     run from another (3.13 ...), the script runs the check in one of those,
     found by itself (the py launcher, PythonXY folders) or PSSE_PYTHON.

   PSCAD
     <name>.pscx             the PSCAD case: built right away (PSCAD_BUILD) when
                             this Python has PSCAD's automation library mhi.pscad
                             (pip install mhi.pscad) and PSCAD 5 is on this PC
     <name>_build_pscad.py   what builds it -- run it again any time with a Python
                             that has mhi.pscad. It builds <name>.pscx
                             from master-library parts: the infinite source at
                             the solved voltage, the grid impedance, gen-tie,
                             MPTs, the collectors (SMIB: one per generator;
                             DETAILED: every feeder section), the GSUs and, on
                             each GSU's LV side, the inverter: your vendor model
                             (UDM: PSCAD library / definition / AC port in
                             Generators, its parameters in PSCAD_Params with
                             {UNITS} {P_PU} {V_PU} ... filled in) or a stand-in
                             source at the solved terminal voltage and angle
                             (the transformers' phase shifts included) -- a
                             check that PSCAD's network matches PSS/E's; delete
                             it and connect a UDM to its node. SMIB: a UDM is
                             one unit, carried N times by PSCAD's current
                             scaler beside it (its scale = the generator's
                             units, as in vendors' test cases); DETAILED: no
                             scaler. A meter at each inverter (P, Q, V). A UDM
                             with its GSU inside: PSCAD inverter at MV. A timed
                             fault on the grid side of the POI (Plant: PSCAD
                             fault ...). One wired single-line diagram, nothing
                             overlapping, its page sized to fit. Every
                             parameter it set, and any it could not, is listed
                             in its log, <name>_build_pscad_<date_time>.txt.
     <name>_PSCAD_DATA.txt   every PSCAD value: ohm, H, uF, kV, MVA, pu on the
                             equipment's own base, vector groups, initial values

   CHECKS -- every input read, none dropped unsaid
     An input that cannot be used stops the build, naming its sheet, row and
     field (the GUI takes you there). One the model built does not use (a
     control mode's other targets, three-winding values on a two-winding MPT,
     collector R X B beside Feeders rows ...) is a NOTE saying so and why.
     What to look at -- a voltage outside 0.9 .. 1.1, a branch past its
     rating, kV that do not match, a value given twice that disagrees, a
     vendor model's AC port that is not 3-phase, a PSCAD drawing past its
     largest page -- is a CHECK or WARNING: in the report, and listed by the
     GUI's Check inputs.

   PLANT TYPE
     The GUI's Plant page, Plant type: the kinds of generator the plant has --
     PV only, BESS only, or a hybrid of any of PV, BESS, wind and synchronous
     machines. A kind ticked gets a generator (an inverter on the first MPT,
     its feeders like the plant's; a synchronous one at HV behind its own
     GSU); a kind unticked goes, with the rows naming it (asked first).
     Plant: Name blank = from the generators: PV100, Hybrid_PV100_BESS50 ...

   SIZES AND RATINGS
     GSUs: Units per GSU (the units on one GSU) sets the GSU count (Units /
     Units per GSU, rounded up); a GSU far above or below the units on it is
     CHECKed. An MPT's MVA blank is the units' MVA on it (Unit MVA x Units,
     through shared collectors too), following them; one far from it is
     CHECKed. A three-winding MPT's blanks: TV kV the LV's (a split LV winding),
     LV and TV MVA its MVA shared out by the units on each (none on the TV:
     MVA/2 on a split LV winding, MVA/3 a tertiary), Z HV-TV = Z HV-LV, Z LV-TV
     = 1.8 x Z HV-LV (a split LV winding's) -- the GUI shows each in brackets.
     The GUI's GSUs page (Size GSUs from the units) and MPT page
     (Size MPTs from the generators) suggest both and set the ones ticked.
     Feeders: an MPT's are those of the generators on it (and the shared
     collectors), each generator's its feeder x Copies -- the GUI shows them
     on the MPT and Feeders pages. Feeders on each MPT (either page) sets how
     many feeders each generator has, its units spread over them (an MPT's
     number shared out by MW); a generator's Units changed in the GUI: its
     feeders follow -- Copies of its feeder as drawn, the units left over on
     one more (a feeder's sections with no units at them or beyond left out).
     Lengths: a blank length unit is ft -- feeders, the gen-tie, an MPT's own
     tie (files saved before: km, written in and said). Unit Qmax blank: the
     MVA left at full P, sqrt(Unit MVA^2 - P^2), when Unit MVA is given (Unit
     PF given: from it; files saved before: Unit PF 0.95 written in); in the
     GUI a Unit Qmax on that circle follows a change of Unit MVA or P.
     Rate A, B, C (PSS/E RATEA/B/C, PSLF rate1/2/3; B blank = A, C blank =
     B): an MPT's (three windings: the HV winding's, the LV and TV windings'
     their MVA, B and C in proportion), one GSU's (x the GSUs), one cable's
     (x a section's parallel cables; DETAILED: x its feeder copies; a
     collector equivalent: its sections from the collector bus together).

   OUTPUT -- nothing that exists is ever overwritten
     <name> = the plant's name and its model type: Hybrid_PV100_BESS100_SMIB, ..._DETAILED
     Models\\Detailed_Models\\ (DETAILED) or Models\\Equivalent_Models\\ (SMIB),
     beside the workbook (OUT_DIR: Models elsewhere), then
     <name>_<date_time>\\   <name>_REPORT.txt, <name>_inputs.json, a copy of the
                             workbook, and what Plant: Build ... (or the GUI's
                             Build: boxes) chose -- any of them, or none:
         PSSE\\    <name>.raw, <name>.dyr; the check's .sav, .snp, <name>_PSSE_CHECK.txt
                  and _psse\\ (PSS/E's own output)
         PSCAD\\   <name>_build_pscad.py, <name>_PSCAD_DATA.txt, <name>.pscx, its log

   RUN (the PSS/E Python: 3.4 for PSS/E 34, 3.7+ for PSS/E 35 -- no PSS/E is
   needed to write the models; standard library only)
       python smib_build.py --template SMIB_INPUT.xlsx   an input workbook to fill in
       python smib_build.py SMIB_INPUT.xlsx              the models
       python smib_build.py SMIB_INPUT.xlsx --no-psse    the models, no PSS/E check
       python smib_build.py SMIB_INPUT.xlsx --no-pscad   the models, no PSCAD build
       python smib_build.py --scale 2 25 1 4 SMIB_INPUT.xlsx   a test plant: 2 MPTs, 25
           feeders on each, 1 GSU per feeder, 4 inverters per GSU (0 = as it
           is; a new workbook -- the GUI: Scale up). One per MPT: --scale 3
           12,8,10 1 4 ... (MPT1 12 feeders, MPT2 8, MPT3 10). The gen-tie:
           one per MPT (--tie per-mpt, the default) or --tie shared
       python smib_build.py                              (the Run button) the workbook
           in INPUT_XLSX, else the one beside this script; several or none: a
           file dialog -- none chosen: a new SMIB_INPUT.xlsx beside it to fill in
"""
from __future__ import print_function, division

import os
import io
import re
import sys
import json
import glob
import math
import cmath
import itertools
import time
import shutil
import subprocess
import zipfile
import posixpath
import collections
import traceback
import tempfile
from xml.sax.saxutils import escape as _xml_escape

# ============================================================================
# SETTINGS
# ============================================================================
GUI = True                        # run with no file: the input window (each sheet a tab) | False: the workbook
                                  # beside the script, as before
INPUT_XLSX = ""                   # "" = the command line's, else the one beside this script | r"C:\...\SMIB.xlsx"
OUT_DIR = ""                      # "" = Models beside the workbook: Detailed_Models / Equivalent_Models in it,
                                  # a new <name>_<date_time> folder in those
PSSE_CHECK = True                 # PSS/E found: the .raw read back and solved, the .sav saved, the .dyr run flat
PSSE_VERSION = 35                 # the PSS/E that checks the models: 35 | 34 | 36 ... | None = the newest installed
PSSE_PYTHON = ""                  # a Python PSS/E has a psspy for (its PSSPYxy folders), used when this one has none
                                  # (3.13 ...): "" = found (the py launcher, PythonXY folders) | r"C:\...\python.exe"
PSCAD_BUILD = True                # this Python has mhi.pscad (PSCAD 5's automation library): the .pscx built right away
FLAT_RUN_S = 10.0                 # flat run length (s)
FLAT_TOL = (0.5, 0.5, 0.001)      # MW, MVAr, pu: a larger drift in the flat run is listed for review
DYN_PARAMS = (60, 0.60, 0.0000095, 1.0 / 240.0, 0.033333)   # NITER, ACCEL, TOL, DELT, FREQFILT
ADDLIB_IDV = ""                   # an .idv that loads user-model libraries (the study's add_library.idv), run before
                                  # the .dyr is read: vendor models, and WECC models your PSS/E's own library lacks
DLLS = []                         # user-model DLLs (full paths), loaded before the .dyr -- instead of / with ADDLIB_IDV
SOLVE_OPTS = [0, 0, 0, 1, 0, 0, 0, 0]   # FNSL: taps, interchange, phase shift, switched shunts locked
TOL_MW = 0.5                      # PSS/E's solution against this script's: a larger difference is listed for review
TOL_MVAR = 1.0
TOL_V_PU = 0.001
IB_ID = "IB"                      # infinite-bus machine id
IB_MBASE = 100000.0               # infinite-bus machine base (MVA)
IB_XSOURCE = 0.01                 # pu on IB_MBASE (= 0.00001 pu on 100 MVA): the grid impedance is the branch POI-IB
IBR_ZSORCE_X = 9999.0             # inverter-based units: source reactance (pu on MBASE) unless Generators says
REGCB_XE = 0.15                   # REGC_B: the converter's coupling reactance Xe (ZSORCE X, pu on MBASE) unless given

# ============================================================================
# THE INPUT WORKBOOK -- its sheets and columns, and the example it is written with
# ============================================================================
LEN_UNITS = ["km", "mi", "kft", "m", "ft"]
FEEDER_UNITS = ["ft", "kft", "m", "km", "mi"]    # a length's unit, ft first: feeders, the gen-tie, an MPT's tie
FEEDER_UNIT = "ft"                               # what a blank length unit is (older files: km -- FORMAT)
FORMAT = 4       # what blanks mean in a project: 1 km lengths, Qmax from PF | 2 feeders ft | 3 ties ft, Qmax from MVA |
                 # 4 a three-winding MPT's LV / TV MVA from the units on each (before: its MVA)
SPLIT_LT = 1.8   # a three-winding MPT's Z LV-TV, blank: this x its Z HV-LV -- a split LV winding's, the two LV
                 # windings well apart (each about its HV-LV impedance from HV, a little of it shared)
LEN_KM = {"km": 1.0, "mi": 1.609344, "kft": 0.3048, "m": 0.001, "ft": 0.0003048}
GEN_TYPES = ["PV", "BESS", "WT4", "WT3", "SYNC", "USER"]
# the electrical control models (REEC) each inverter type takes; the first is its default
REEC_OK = {"PV": ("REEC_A", "REEC_D"), "WT4": ("REEC_A", "REEC_D"), "WT3": ("REEC_A",),
           "BESS": ("REEC_C", "REEC_D")}
REGC_OK = {"PV": ("REGC_A", "REGC_B"), "WT4": ("REGC_A", "REGC_B"), "BESS": ("REGC_A", "REGC_B"), "WT3": ("REGC_A",)}
# a synchronous machine's dynamic models (type SYNC), the first its default
SYNC_MACHINES = ["GENROU", "GENSAL"]
SYNC_EXCITERS = ["SEXS", "IEEET1", "ESST1A", "EXST1", "NONE"]
SYNC_GOVERNORS = ["TGOV1", "HYGOV", "GAST", "IEEEG1", "NONE"]
MODEL_CHOICE = {                  # what each choice is for: the GUI lists a generator's own (by its Type), the first
    "regc": (REGC_OK, collections.OrderedDict([       # its default
        ("REGC_A", "current source -- the standard"),
        ("REGC_B", "voltage source behind ZSORCE -- weak grids (low SCR)")])),
    "reec": (REEC_OK, collections.OrderedDict([
        ("REEC_A", "solar, wind -- WECC's standard"),
        ("REEC_C", "battery -- with its state of charge"),
        ("REEC_D", "solar, battery, wind -- the newest: voltage-dependent limits, momentary cessation")])),
    "sync_machine": ({"SYNC": tuple(SYNC_MACHINES)}, collections.OrderedDict([
        ("GENROU", "round rotor -- steam and gas turbine units"),
        ("GENSAL", "salient pole -- hydro units")])),
    "sync_exciter": ({"SYNC": tuple(SYNC_EXCITERS)}, collections.OrderedDict([
        ("SEXS", "simplified excitation system"),
        ("IEEET1", "IEEE type 1: a DC exciter (older units)"),
        ("ESST1A", "static, fed from its terminals (IEEE ST1A) -- most new units"),
        ("EXST1", "static, fed from its terminals (IEEE ST1, the older form)"),
        ("NONE", "none: its field voltage stays as it starts")])),
    "sync_governor": ({"SYNC": tuple(SYNC_GOVERNORS)}, collections.OrderedDict([
        ("TGOV1", "steam turbine"),
        ("HYGOV", "hydro turbine, with its penstock"),
        ("GAST", "gas turbine"),
        ("IEEEG1", "steam turbine, IEEE type 1: its stages (HP, IP, LP)"),
        ("NONE", "none: its mechanical power stays as it starts")])),
}

# Plant sheet (Item | Value | Unit | Notes): (key, item, unit, note, choices)
FAULT_TYPES = ["ABCG", "ABC", "AG", "BG", "CG", "ABG", "BCG", "ACG", "AB", "BC", "AC"]
PLANT_FIELDS = [
    ("name", "Name", "", "model name: file names and the PSCAD case (letters, digits, _; blank = from the "
     "generators: PV100, Hybrid_PV100_BESS50 ...)", None),
    ("model_type", "Model type", "", "SMIB: each generator one machine behind one GSU on its own equivalent feeder "
     "(WECC) from its MPT | DETAILED: every feeder section, the units at each node a machine of their own behind "
     "their own GSU -- PSS/E and PSCAD alike (blank = SMIB)", ["SMIB", "DETAILED"]),
    ("sbase_mva", "System base", "MVA", "PSS/E system base (blank = 100)", None),
    ("out_raw", "Build PSS/E .raw", "", "YES = the load-flow case (blank = YES)", ["YES", "NO"]),
    ("out_dyr", "Build PSS/E .dyr", "", "YES = the dynamic models (blank = YES)", ["YES", "NO"]),
    ("out_pscad", "Build PSCAD model", "", "YES = the PSCAD build script, its data and the .pscx (blank = YES)",
     ["YES", "NO"]),
    ("out_epc", "Build PSLF .epc", "", "YES = the load-flow case for GE PSLF (blank = YES)", ["YES", "NO"]),
    ("out_dyd", "Build PSLF .dyd", "", "YES = the dynamic models for GE PSLF (blank = YES)", ["YES", "NO"]),
    ("freq_hz", "Frequency", "Hz", "blank = 60", None),
    ("first_bus", "First bus number", "", "buses are numbered from here + 1 (Bus_Numbers sets any one)", None),
    ("poi_kv", "POI kV", "kV", "", None),
    ("poi_name", "POI name", "", "12 characters at most", None),
    ("grid_scr", "Grid SCR", "", "short-circuit ratio at the POI, on the SCR base", None),
    ("grid_x_r", "Grid X/R", "", "", None),
    ("scr_base_mw", "SCR base", "MW", "blank = the generators' Pmax", None),
    ("grid_sc_mva", "Grid short-circuit MVA", "MVA", "instead of the SCR (with Grid X/R)", None),
    ("grid_r_pu", "Grid R", "pu", "instead of the SCR: Thevenin R on the system base", None),
    ("grid_x_pu", "Grid X", "pu", "instead of the SCR: Thevenin X on the system base", None),
    ("tie_length", "Gen-tie length", "", "POI to the substation HV bus; blank = no gen-tie (the MPTs on the POI bus)",
     None),
    ("tie_unit", "Gen-tie length unit", "", "ft, kft, m, km, mi (blank = ft)", FEEDER_UNITS),
    ("tie_per_unit", "Gen-tie values per", "", "the length R, X, B, C below are per: km, mi, kft, m, ft (blank = "
     "km or mi, whichever matches a line of the POI kV)", LEN_UNITS),
    ("tie_r_per", "Gen-tie R per length", "ohm", "one circuit (blank = a typical overhead line of the POI kV)", None),
    ("tie_x_per", "Gen-tie X per length", "ohm", "one circuit (blank = typical)", None),
    ("tie_b_per", "Gen-tie B per length", "uS", "one circuit, total charging (blank = typical)", None),
    ("tie_c_per", "Gen-tie C per length", "uF", "instead of B", None),
    ("tie_r_ohm", "Gen-tie R total", "ohm", "instead of per length (one circuit)", None),
    ("tie_x_ohm", "Gen-tie X total", "ohm", "", None),
    ("tie_b_us", "Gen-tie B total", "uS", "", None),
    ("tie_parallel", "Gen-tie circuits", "", "identical circuits in parallel (blank = 1)", None),
    ("tie_rate_mva", "Gen-tie rating", "MVA", "one circuit (blank = typical for the POI kV)", None),
    ("control_mode", "Control mode", "", "Q: Q at the POI | PF: power factor at the POI | V: the plant holds the POI "
     "voltage with the grid at Grid voltage (blank = Q)", ["Q", "PF", "V"]),
    ("v_poi_pu", "POI voltage", "pu", "the POI voltage the plant holds (blank = 1.0)", None),
    ("q_poi_mvar", "Q at POI", "MVAr", "mode Q; + = into the grid (blank = 0)", None),
    ("pf_poi", "PF at POI", "", "mode PF; + lagging (Q into the grid), - leading", None),
    ("v_grid_pu", "Grid voltage", "pu", "mode V: the infinite bus (blank = 1.0)", None),
    ("p_poi_mw", "P at POI", "MW", "blank = each generator at its dispatch | a value = the dispatches scaled to "
     "deliver it", None),
    ("plant_ctrl", "Plant controller", "", "PSS/E .dyr -- REPC_A: a REPCA1 on each generator | REPC_B: one for the "
     "plant, PLNTBU1 at the POI and a REAX4BU1 on each generator (hybrid plants) (blank = REPC_A)",
     ["REPC_A", "REPC_B"]),
    ("regc_repc", "REGC_A / REPC_A records", "", "standard: REGCA1 / REPCA1 (PSS/E library) | USRMDL: REGCAU1 / "
     "REPCAU1 (the WECC user-model DLL) (blank = standard)", ["standard", "USRMDL"]),
    ("pscad_time_step_us", "PSCAD time step", "us", "blank = 10 (the vendor model's own requirement wins)", None),
    ("pscad_duration_s", "PSCAD duration", "s", "blank = 5", None),
    ("pscad_plot_step_us", "PSCAD plot step", "us", "blank = 100", None),
    ("pscad_meter", "PSCAD meter at POI", "", "YES = a multimeter between the grid and the POI (blank = YES)",
     ["YES", "NO"]),
    ("pscad_inv_meter", "PSCAD meter at each inverter", "", "YES = a multimeter between each inverter and its "
     "GSU: P_, Q_, V_<unit> in MW, MVAr, kV (blank = YES)", ["YES", "NO"]),
    ("pscad_lines", "PSCAD lines as", "", "PI = the gen-tie, each collector and each feeder section one Coupled PI "
     "Section (R, X, B in pu, its charging inside); RLC = an R and an L, the charging a C at each end (blank = PI)",
     ["PI", "RLC"]),
    ("pscad_fault", "PSCAD fault at POI", "", "YES = a fault on the grid side of the POI with its timed fault logic, "
     "as vendors' test cases have (blank = YES)", ["YES", "NO"]),
    ("pscad_fault_s", "PSCAD fault time", "s", "when the fault starts (blank = 100: none in the run)", None),
    ("pscad_fault_dur_s", "PSCAD fault duration", "s", "blank = 0.15", None),
    ("pscad_fault_type", "PSCAD fault type", "", "the phases faulted, G = to ground (blank = ABCG)", FAULT_TYPES),
    ("pscad_fault_ohm", "PSCAD fault resistance", "ohm", "blank = 0.01", None),
    ("pscad_libraries", "PSCAD libraries", "", "user-model libraries (.pslx) to load, separated by ;", None),
]

SWEEP = (("grid_scr", "SCR"), ("grid_x_r", "XR"), ("grid_sc_mva", "SC"), ("v_poi_pu", "V"), ("v_grid_pu", "VG"),
         ("q_poi_mvar", "Q"), ("pf_poi", "PF"), ("p_poi_mw", "P"), ("tie_length", "TIE"))   # Plant fields as lists
SWEEP_MAX = 50             # sets of models one build makes at most
PLANT_FIELDS = [(k, i, u, ((n + "; ") if n else "") + "several values (3, 5, 10): a set of models for each, "
                 "<name>_%s<value>" % dict(SWEEP)[k] if k in dict(SWEEP) else n, c) for k, i, u, n, c in PLANT_FIELDS]

# table sheets: (key, column, note, choices, width)
MPT_COLS = [
    ("name", "Name", "# MPT name. Its buses: <name> or <name>:LV (LV winding), <name>:TV (tertiary)", None, 11),
    ("windings", "Windings", "2 or 3 (blank = 3 when TV kV is given, else 2)", ["2", "3"], 7),
    ("hv_kv", "HV kV", "", None, 7),
    ("lv_kv", "LV kV", "", None, 7),
    ("tv_kv", "TV kV", "tertiary / 2nd LV winding (Windings 3; blank = the LV kV, a split LV winding)", None, 9),
    ("mva", "MVA", "the rating (3 windings: HV winding; blank = the MVA of the units on it, following them)", None,
     8),
    ("lv_mva", "LV MVA", "3 windings (blank = MVA x the LV units' share of the units on LV and TV -- none on TV: "
     "MVA)", None, 7),
    ("tv_mva", "TV MVA", "3 windings (blank = MVA x the TV units' share -- none on it: MVA/2 on a split LV winding "
     "(TV kV the LV's), MVA/3 a tertiary)", None, 7),
    ("rate_mva", "Rate A MVA", "normal rating: PSS/E RATEA, PSLF rate1 (blank = MVA; 3 windings: the HV winding's, "
     "the LV and TV windings' their MVA)", None, 8),
    ("rate_b_mva", "Rate B MVA", "emergency rating: RATEB, rate2 (blank = Rate A; 3 windings: the HV winding's, the "
     "LV and TV windings' in proportion)", None, 8),
    ("rate_c_mva", "Rate C MVA", "RATEC, rate3 (blank = Rate B; 3 windings: as Rate B)", None, 8),
    ("z_hl_pct", "Z HV-LV %", "on its base MVA", None, 8),
    ("z_ht_pct", "Z HV-TV %", "3 windings, on its base MVA (blank = Z HV-LV)", None, 8),
    ("z_lt_pct", "Z LV-TV %", "3 windings, on its base MVA (blank = 1.8 x Z HV-LV, a split LV winding's)", None, 8),
    ("base_hl_mva", "Z HV-LV base MVA", "blank = MVA", None, 9),
    ("base_ht_mva", "Z HV-TV base MVA", "blank = MVA", None, 9),
    ("base_lt_mva", "Z LV-TV base MVA", "blank = MVA", None, 9),
    ("x_r", "X/R", "each impedance", None, 6),
    ("load_loss_kw", "Load loss kW", "2 windings, at MVA: instead of X/R", None, 8),
    ("vector", "Vector group", "YNd1, YNd11, Dyn1, YNyn0, YNd1d1 ...", None, 9),
    ("tap", "HV tap", "HV winding ratio, 1.025 = +2.5 % (blank = 1)", None, 7),
    ("count", "Parallel units", "identical units (blank = 1)", None, 8),
    ("nll_pct", "No-load loss %", "of MVA (blank = 0)", None, 8),
    ("im_pct", "Magnetising current %", "of MVA (blank = 0)", None, 10),
    ("tie_length", "Tie length", "a line of its own from HV to this MPT (blank = none)", None, 9),
    ("tie_unit", "Tie length unit", "blank = ft", FEEDER_UNITS, 7),
    ("tie_per_unit", "Tie values per", "the length R, X, B are per (blank = km or mi, whichever matches a line of "
     "its kV)", LEN_UNITS, 7),
    ("tie_r_per", "Tie R per length", "ohm (blank = a typical overhead line of the HV kV)", None, 8),
    ("tie_x_per", "Tie X per length", "ohm (blank = typical)", None, 8),
    ("tie_b_per", "Tie B per length", "uS (blank = typical)", None, 8),
    ("tie_rate_mva", "Tie rating MVA", "blank = typical", None, 8),
]

GEN_COLS = [
    ("name", "Name", "# generator name. Its buses: <name>:MV (GSU HV), <name> or <name>:LV (terminal)", None, 11),
    ("type", "Type", "PV BESS WT4 WT3 SYNC USER (USER: Dyr_Records only)", GEN_TYPES, 7),
    ("id", "Machine ID", "blank = 1", None, 7),
    ("connect", "Connect to", "the bus its collector starts from: MPT1 (LV), MPT1:TV, a shared collector, HV, POI",
     None, 10),
    ("units", "Units", "number of units", None, 6),
    ("unit_mva", "Unit MVA", "one unit (blank = from P and Q)", None, 7),
    ("unit_pmax_mw", "Unit Pmax MW", "", None, 7),
    ("unit_pmin_mw", "Unit Pmin MW", "BESS charging: negative (blank = 0)", None, 8),
    ("unit_qmax_mvar", "Unit Qmax MVAr", "blank = the MVA left at full P: sqrt(Unit MVA^2 - Pmax^2), or from Unit "
     "PF", None, 8),
    ("unit_qmin_mvar", "Unit Qmin MVAr", "blank = -Qmax", None, 8),
    ("unit_pf", "Unit PF", "Q at Pmax when Qmax is blank, instead of from Unit MVA (blank = 0.95 where Unit MVA is "
     "blank too)", None, 7),
    ("kv", "Terminal kV", "blank = GSU LV kV", None, 8),
    ("dispatch_mw", "Dispatch MW", "this generator's total; blank = Pmax (BESS charging: negative)", None, 8),
    ("q_mode", "Q mode", "share: holds the POI with the other share generators | fixed: its own Q "
     "(blank = share)",
     ["share", "fixed"], 7),
    ("fixed_q_mvar", "Fixed Q MVAr", "Q mode fixed (blank = 0)", None, 8),
    ("fixed_pf", "Fixed PF", "Q mode fixed, instead of Fixed Q (+ lagging)", None, 7),
    ("gsu_units", "Units per GSU", "the units on one GSU (blank = Units / GSU count; both blank: 1)", None, 6),
    ("gsu_count", "GSU count", "blank = Units / Units per GSU, rounded up (that blank too: Units, a GSU each)", None,
     6),
    ("gsu_mva", "GSU MVA", "one GSU; blank = no GSU (units on the MV bus)", None, 7),
    ("gsu_rate_a_mva", "GSU Rate A MVA", "one GSU's normal rating: RATEA, rate1 (blank = GSU MVA)", None, 7),
    ("gsu_rate_b_mva", "GSU Rate B MVA", "one GSU's emergency rating: RATEB, rate2 (blank = Rate A)", None, 7),
    ("gsu_rate_c_mva", "GSU Rate C MVA", "one GSU's: RATEC, rate3 (blank = Rate B)", None, 7),
    ("gsu_hv_kv", "GSU HV kV", "", None, 7),
    ("gsu_lv_kv", "GSU LV kV", "", None, 7),
    ("gsu_z_pct", "GSU Z %", "on GSU MVA", None, 7),
    ("gsu_x_r", "GSU X/R", "", None, 6),
    ("gsu_vector", "GSU vector group", "", None, 8),
    ("gsu_tap", "GSU HV tap", "blank = 1", None, 6),
    ("collector", "Collector", "FEEDERS: WECC equivalent of its Feeders rows | EQUIVALENT: R X B here | NONE (blank = "
     "FEEDERS where Feeders has rows for it, else EQUIVALENT where Collector X is given, else NONE)",
     ["FEEDERS", "EQUIVALENT", "NONE"], 10),
    ("coll_r", "Collector R", "EQUIVALENT", None, 8),
    ("coll_x", "Collector X", "EQUIVALENT", None, 8),
    ("coll_b", "Collector B", "EQUIVALENT, total charging", None, 8),
    ("coll_units", "Collector units", "pu: system base at collector kV | ohm: R X in ohm, B in uS (blank = pu)",
     ["pu", "ohm"], 8),
    ("zsorce_x", "ZSORCE X pu", "on MBASE (blank: inverters 9999, SYNC X''d)", None, 8),
    ("wmod", "WMOD", "blank: inverters 1, SYNC 0", None, 6),
    ("regc", "Converter model", "REGC_A: a current source, the standard | REGC_B: a voltage source behind ZSORCE "
     "R + jX, for weak grids (ZSORCE X its Xe) -- PV, BESS, WT4; .dyr REGCA1 / REGCBU1, .dyd regc_a / regc_b "
     "(blank = REGC_A)",
     ["REGC_A", "REGC_B"], 9),
    ("reec", "Electrical control", "REEC_A: PV, WT | REEC_C: BESS (state of charge) | REEC_D: PV, BESS, WT4, the "
     "newest (voltage-dependent limits, momentary cessation) -- .dyr and .dyd alike (blank = REEC_A, BESS REEC_C)",
     ["REEC_A", "REEC_C", "REEC_D"], 9),
    ("sync_machine", "Machine model", "SYNC: GENROU round rotor (steam, gas) | GENSAL salient pole (hydro) (blank = "
     "GENROU)", SYNC_MACHINES, 9),
    ("sync_exciter", "Exciter model", "SYNC: SEXS simplified | IEEET1 DC (IEEE type 1) | ESST1A static, bus-fed | EXST1 "
     "static (older form) | NONE: constant field voltage (blank = SEXS)", SYNC_EXCITERS, 9),
    ("sync_governor", "Governor model", "SYNC: TGOV1 steam | HYGOV hydro | GAST gas | IEEEG1 steam, multi-stage | "
     "NONE: constant mechanical power (blank = TGOV1)", SYNC_GOVERNORS, 9),
    ("pscad_library", "PSCAD library", "vendor library .pslx (blank = none)", None, 18),
    ("pscad_definition", "PSCAD definition", "vendor model, library:name (blank = a source: network check)", None, 16),
    ("pscad_ac_port", "PSCAD AC port", "its AC port (blank = its 3-phase port)", None, 9),
    ("pscad_source", "PSCAD source case", "the OEM's PSCAD model (.pscx / .pslx) the UDM comes from: its PSCAD "
     "definition added to the case (GUI: Pick UDM from OEM model)", None, 18),
    ("pscad_component_id", "PSCAD component ID", "a UDM placed in the source case: copied with its settings "
     "instead (blank = the definition, its values from PSCAD_Params)", None, 10),
    ("pscad_inv_at", "PSCAD inverter at", "LV: the inverter (your UDM or the stand-in source) on the GSU's LV side, "
     "the GSU built | MV: on its HV side, for a UDM with its GSU inside (blank = LV)", ["LV", "MV"], 8),
]

FEEDER_COLS = [
    ("collector", "Collector", "# the generator (its own collector) or a shared collector", None, 11),
    ("feeder", "Feeder", "feeder name", None, 8),
    ("copies", "Copies", "identical feeders: how many of this feeder (blank = 1) -- an MPT's feeders are those of the "
     "generators on it (GUI: MPT or Feeders page, Feeders on each MPT)", None, 6),
    ("section", "Section", "1, 2, 3 ... from the collector bus out", None, 7),
    ("from_section", "From section", "blank = the one before (daisy chain) | 0 = the collector bus | n = a lateral "
     "off the end of section n", None, 9),
    ("cable", "Cable", "from Cables", None, 9),
    ("length", "Length", "", None, 7),
    ("unit", "Length unit", "blank = ft", FEEDER_UNITS, 7),
    ("units", "Units at end", "units tapped at the far end", None, 7),
    ("units_of", "Units of", "shared collectors: whose units (repeat the section for another generator's)", None, 10),
    ("parallel", "Parallel cables", "blank = 1", None, 7),
    ("r_ohm", "R ohm", "the section's R, its parallel cables together (blank = Cable x Length)", None, 8),
    ("x_ohm", "X ohm", "the section's X (blank = Cable x Length)", None, 8),
    ("b_us", "B uS", "the section's charging B, total (blank = Cable x Length)", None, 8),
]

CABLE_COLS = [
    ("cable", "Cable", "# cable type", None, 11),
    ("unit", "Length unit", "the values are per (blank = km)", LEN_UNITS, 7),
    ("r_per", "R per length", "ohm, at operating temperature", None, 9),
    ("x_per", "X per length", "ohm", None, 9),
    ("b_per", "B per length", "uS, the charging susceptance (GUI: Cables page, C to B)", None, 9),
    ("c_per", "C per length", "uF, instead of B (B = 2 pi f C)", None, 9),
    ("rate_a_mva", "Rate A MVA", "one cable's normal rating at its collector's kV (sqrt(3) x kV x amps / 1000): "
     "RATEA, rate1 -- a section's: x its parallel cables (blank = none)", None, 9),
    ("rate_b_mva", "Rate B MVA", "one cable's emergency rating: RATEB, rate2 (blank = Rate A)", None, 9),
    ("rate_c_mva", "Rate C MVA", "one cable's: RATEC, rate3 (blank = Rate B)", None, 9),
]

COLLECTOR_COLS = [
    ("name", "Name", "# shared collector: its far-end bus has this name", None, 11),
    ("connect", "Connect to", "the bus it starts from (MPT1, MPT1:TV ...)", None, 11),
]

LOAD_COLS = [
    ("kind", "Kind", "# LOAD: constant impedance | SHUNT: + capacitor, - reactor (blank = LOAD)", ["LOAD", "SHUNT"],
     8),
    ("at", "At bus", "POI, HV, MPT1, MPT1:TV, PV:MV, PV ...", None, 10),
    ("mw", "MW", "LOAD, at 1 pu", None, 7),
    ("mvar", "MVAr", "at 1 pu: LOAD + inductive | SHUNT + capacitive", None, 9),
    ("name", "Name", "", None, 10),
]

BUS_COLS = [
    ("bus", "Bus", "# its role: IB, POI, HV, MPT1:LV, PV:MV, PV:LV ...", None, 12),
    ("number", "Number", "blank = automatic", None, 9),
    ("name", "Name", "blank = automatic (12 characters at most)", None, 14),
]

MACHINE_COLS = [
    ("machine", "Machine", "# DETAILED model: a machine, <generator>_<feeder>_S<section> (the GUI lists them); its "
     "values in place of its share of its generator's", None, 16),
    ("id", "Machine ID", "blank = its generator's", None, 7),
    ("pgen_mw", "Pgen MW", "its dispatch (blank = its share of its generator's Dispatch MW)", None, 8),
    ("pmax_mw", "Pmax MW", "blank = its units x Unit Pmax", None, 8),
    ("pmin_mw", "Pmin MW", "blank = its units x Unit Pmin", None, 8),
    ("qmax_mvar", "Qmax MVAr", "blank = its units x Unit Qmax", None, 8),
    ("qmin_mvar", "Qmin MVAr", "blank = its units x Unit Qmin", None, 8),
    ("q_mvar", "Fixed Q MVAr", "a value: this Q, fixed (blank = as its generator's Q mode)", None, 8),
    ("mbase_mva", "MBASE MVA", "blank = its units x Unit MVA", None, 8),
]

DYRP_COLS = [
    ("generator", "Generator", "# IB: the infinite bus | PLANT: the plant controller (REPC_B)", None, 11),
    ("model", "Model", "REGCA1 (REGCAU1) | REECA1 REECCU1 REECDU1 | REPCA1 (REPCAU1) or REAX4BU1 | PLANT: PLNTBU1 | "
     "WT3: + WTDTA1 WTARA1 WTPTA1 WTTQA1 | SYNC: its machine (GENROU, GENSAL), exciter (SEXS, IEEET1, ESST1A, "
     "EXST1), governor (TGOV1, HYGOV, GAST, IEEEG1) | IB: GENCLS", None, 9),
    ("parameter", "Parameter", "PSS/E name (the report lists them all) or position: ICON2, CON14", None, 10),
    ("value", "Value", "", None, 9),
]

DYRR_COLS = [
    ("generator", "Generator", "# blank = the plant", None, 11),
    ("record", "Record", ".dyr text ending in / -- {BUS} {ID} {MBASE} {POI} {IB} {HV} {MV} {<bus role>} are filled "
     "in", None, 100),
]

PSCADP_COLS = [
    ("generator", "Generator", "#", None, 11),
    ("parameter", "Parameter", "the vendor model's parameter name", None, 16),
    ("value", "Value", "as PSCAD takes it (0.63 [kV], 40, {UNITS}, {P_PU} ...)", None, 16),
]

SIMPLE = {  # the GUI's simple view: these fields only (All fields: every one; a hidden field keeps its value)
    "Plant": ("name", "model_type", "freq_hz", "poi_kv", "grid_scr", "grid_x_r", "scr_base_mw", "tie_length",
              "tie_unit", "tie_per_unit", "tie_r_per", "tie_x_per", "tie_b_per", "control_mode", "v_poi_pu",
              "q_poi_mvar", "pf_poi", "v_grid_pu", "p_poi_mw", "plant_ctrl", "pscad_duration_s", "pscad_fault",
              "pscad_fault_s", "pscad_fault_dur_s", "pscad_fault_type", "pscad_fault_ohm"),
    "MPT": ("name", "windings", "hv_kv", "lv_kv", "tv_kv", "mva", "lv_mva", "tv_mva", "rate_mva", "rate_b_mva",
            "rate_c_mva", "count",
            "z_hl_pct", "z_ht_pct", "z_lt_pct", "x_r", "vector", "tap"),
    "Generators": ("name", "type", "connect", "units", "unit_mva", "unit_pmax_mw", "unit_pmin_mw", "unit_qmax_mvar",
                   "unit_qmin_mvar", "kv", "dispatch_mw", "collector", "regc", "reec", "pscad_definition",
                   "pscad_source"),
    "GSUs": ("name", "units", "gsu_units", "gsu_count", "gsu_mva", "gsu_rate_a_mva", "gsu_rate_b_mva", "gsu_rate_c_mva",
             "gsu_hv_kv", "gsu_lv_kv", "gsu_z_pct", "gsu_x_r", "gsu_vector", "gsu_tap"),
    "Feeders": ("collector", "feeder", "copies", "section", "cable", "length", "unit", "units", "units_of"),
    "Cables": ("cable", "unit", "r_per", "x_per", "b_per", "rate_a_mva", "rate_b_mva", "rate_c_mva"),
}
SIMPLE_TABS = ("Plant", "MPT", "Generators", "GSUs", "Feeders", "Cables", "Dyr_Params", "PSCAD_Params", "Output")
_is = lambda key, v: lambda r: (_txt(r.get(key)) or "").upper() == v
SHOW_WITH = {  # the simple view: a column it hides, shown where a row's other value calls for it
    ("Generators", "unit_pf"): lambda r: _txt(r.get("unit_qmax_mvar")) is None,      # it sets a blank Qmax
    ("Generators", "fixed_q_mvar"): _is("q_mode", "FIXED"), ("Generators", "fixed_pf"): _is("q_mode", "FIXED"),
    ("Generators", "coll_r"): _is("collector", "EQUIVALENT"), ("Generators", "coll_x"): _is("collector", "EQUIVALENT"),
    ("Generators", "coll_b"): _is("collector", "EQUIVALENT"),
    ("Generators", "coll_units"): _is("collector", "EQUIVALENT"),
    ("Generators", "sync_machine"): _is("type", "SYNC"), ("Generators", "sync_exciter"): _is("type", "SYNC"),
    ("Generators", "sync_governor"): _is("type", "SYNC"),
}
PAGE_COLS = {  # pages that show part of a sheet, in either view: the Generators sheet as its generators and its GSUs
    "Generators": tuple(c[0] for c in GEN_COLS if not c[0].startswith("gsu_")),
    "GSUs": ("name", "units") + tuple(c[0] for c in GEN_COLS if c[0].startswith("gsu_")),
}
NAV_GROUPS = (  # the GUI's pages, grouped on its left: (group, [(page, what it is called there)])
    ("Plant", (("Plant", "Plant and grid"),)),
    ("Generators", (("Generators", "Generators"), ("Machines", "DETAILED machines"))),
    ("Transformers", (("MPT", "Main transformers (MPT)"), ("GSUs", "Generator step-ups (GSU)"))),
    ("Collector system", (("Feeders", "Feeders"), ("Cables", "Cables"), ("Collectors", "Shared collectors"))),
    ("Dynamics: PSS/E, PSLF", (("Dyr_Params", "Model parameters"), ("Dyr_Records", "Records of your own"))),
    ("PSCAD", (("PSCAD_Params", "Vendor model (UDM)"),)),
    ("More inputs", (("Loads_Shunts", "Loads and shunts"), ("Bus_Numbers", "Bus numbers"))),
    ("Reference", (("Input_Reference", "Input reference"), ("Dynamic_Models", "Dynamic models"),
                   ("README", "Read me"))),
    ("Results", (("Output", "Output"),)),
)
_HV, _MV, _LV = ("69", "115", "138", "161", "230", "345", "500"), ("12.47", "13.8", "24.9", "34.5"), \
    ("0.48", "0.6", "0.63", "0.66", "0.69", "0.8")
_TAPS, _N = ("0.975", "0.9875", "1", "1.0125", "1.025"), ("1", "2", "3", "4")
SUGGEST = {  # the GUI's drop-down of typical values where a field has no list of its own (any value can be typed)
    "freq_hz": ("60", "50"), "sbase_mva": ("100",), "poi_kv": _HV, "grid_scr": ("3", "5", "10", "20", "3, 5, 10"),
    "grid_x_r": ("5", "10", "15", "20"), "v_poi_pu": ("0.95", "1.0", "1.05"), "v_grid_pu": ("0.95", "1.0", "1.05"),
    "pf_poi": ("0.95", "-0.95", "0.9", "-0.9", "1"), "tie_parallel": ("1", "2"),
    "pscad_time_step_us": ("1", "2", "5", "10", "20", "50"), "pscad_duration_s": ("2", "5", "10", "20"),
    "pscad_plot_step_us": ("50", "100", "250", "500"), "pscad_fault_s": ("1", "2", "3", "100"),
    "pscad_fault_dur_s": ("0.05", "0.1", "0.15", "0.2"), "pscad_fault_ohm": ("0.01", "1", "10"),
    "MPT.hv_kv": _HV, "MPT.lv_kv": _MV, "MPT.tv_kv": _MV, "MPT.vector": ("YNd1", "YNd11", "YNyn0", "YNyn0d1",
                                                                    "YNd1d1", "YNd11d11", "Dyn1", "Dyn11"),
    "MPT.z_hl_pct": ("8", "9", "10", "12"), "MPT.x_r": ("30", "40", "45", "50"), "MPT.tap": _TAPS,
    "MPT.count": _N, "Generators.id": ("1", "2", "3"), "Generators.units": _N, "Generators.kv": _LV,
    "Generators.unit_pf": ("0.9", "0.95", "1"), "Generators.fixed_pf": ("0.9", "0.95", "1", "-0.95"),
    "Generators.gsu_count": _N, "Generators.gsu_units": ("1", "2", "4", "8", "10", "16", "20"),
    "Generators.gsu_hv_kv": _MV, "Generators.gsu_lv_kv": _LV,
    "Generators.gsu_z_pct": ("5.75", "6", "6.5", "8"), "Generators.gsu_x_r": ("6", "8", "10"),
    "Generators.gsu_vector": ("Dyn1", "Dyn11", "YNd1", "YNd11", "Dy1", "Dy11", "YNyn0"), "Generators.gsu_tap": _TAPS,
    "Generators.wmod": ("0", "1"), "Feeders.copies": _N, "Feeders.parallel": _N, "Machines.id": ("1", "2", "3"),
}

COL_ALIASES = {  # a column's title before it was renamed: still read from older workbooks
    "MPT": (("Rating MVA", "rate_mva"),),
}

TABLES = [  # (sheet, input key, columns)
    ("MPT", "mpt", MPT_COLS),
    ("Generators", "generators", GEN_COLS),
    ("Feeders", "feeders", FEEDER_COLS),
    ("Cables", "cables", CABLE_COLS),
    ("Collectors", "collectors", COLLECTOR_COLS),
    ("Loads_Shunts", "loads_shunts", LOAD_COLS),
    ("Machines", "machines", MACHINE_COLS),
    ("Bus_Numbers", "bus_numbers", BUS_COLS),
    ("Dyr_Params", "dyr_params", DYRP_COLS),
    ("Dyr_Records", "dyr_records", DYRR_COLS),
    ("PSCAD_Params", "pscad_params", PSCADP_COLS),
]

_PV_FEEDER = [("1000AL", 5250, 0), ("500AL", 1300, 1), ("500AL", 1300, 1), ("4/0AL", 1300, 1),
              ("4/0AL", 1300, 1), ("1/0AL", 1300, 1)]                     # lengths in ft
_BESS_FEEDER = [("1000AL", 2600, 0), ("500AL", 500, 1), ("500AL", 500, 1), ("4/0AL", 500, 1),
                ("4/0AL", 500, 1), ("1/0AL", 500, 1)]

# the example: a hybrid plant -- 100 MW PV (25 x 4.0 MW) and 100 MW BESS (25 x 4.0 MW) on one 230/34.5 kV MPT,
# five identical feeders each, one plant controller (REPC_B: PLNTBU1 + REAX4BU1); PSCAD: the collectors section
# by section, a GSU and an inverter slot at every node
EXAMPLE = {
    "plant": {
        "name": "Hybrid_PV100_BESS100", "model_type": "SMIB", "sbase_mva": 100.0, "out_raw": "YES",
        "out_dyr": "YES", "out_pscad": "YES", "out_epc": "YES", "out_dyd": "YES", "freq_hz": 60.0,
        "first_bus": 1000,
        "poi_kv": 230.0, "poi_name": "POI", "grid_scr": 3.0, "grid_x_r": 10.0,
        "tie_length": 16400, "tie_unit": "ft", "tie_per_unit": "km", "tie_r_per": 0.0720, "tie_x_per": 0.4950,
        "tie_b_per": 3.35,
        "tie_parallel": 1, "tie_rate_mva": 400.0,
        "control_mode": "Q", "v_poi_pu": 1.0, "q_poi_mvar": 0.0, "pf_poi": 1.0, "v_grid_pu": 1.0,
        "plant_ctrl": "REPC_B", "regc_repc": "standard",
        "pscad_time_step_us": 10.0, "pscad_duration_s": 5.0, "pscad_plot_step_us": 100.0, "pscad_meter": "YES",
        "pscad_lines": "PI",
        "pscad_fault": "YES", "pscad_fault_dur_s": 0.15, "pscad_fault_type": "ABCG", "pscad_fault_ohm": 0.01,
    },
    "mpt": [
        {"name": "MPT1", "windings": "2", "hv_kv": 230.0, "lv_kv": 34.5, "mva": 225.0, "z_hl_pct": 10.0, "x_r": 45.0,
         "vector": "YNd1", "tap": 1.0, "count": 1, "rate_mva": 225.0},
    ],
    "generators": [
        {"name": "PV", "type": "PV", "id": "1", "connect": "MPT1", "units": 25, "unit_mva": 4.2,
         "unit_pmax_mw": 4.0, "unit_pmin_mw": 0.0, "unit_qmax_mvar": 1.28, "unit_qmin_mvar": -1.28, "kv": 0.63,
         "q_mode": "share", "gsu_mva": 4.2, "gsu_hv_kv": 34.5, "gsu_lv_kv": 0.63, "gsu_z_pct": 6.5,
         "gsu_x_r": 8.0, "gsu_vector": "YNd1", "collector": "FEEDERS", "reec": "REEC_A", "pscad_inv_at": "LV"},
        {"name": "BESS", "type": "BESS", "id": "1", "connect": "MPT1", "units": 25, "unit_mva": 4.4,
         "unit_pmax_mw": 4.0, "unit_pmin_mw": -4.0, "unit_qmax_mvar": 1.83, "unit_qmin_mvar": -1.83, "kv": 0.69,
         "q_mode": "share", "gsu_mva": 4.4, "gsu_hv_kv": 34.5, "gsu_lv_kv": 0.69, "gsu_z_pct": 6.0,
         "gsu_x_r": 7.0, "gsu_vector": "YNd1", "collector": "FEEDERS", "reec": "REEC_C", "pscad_inv_at": "LV"},
    ],
    "feeders": [{"collector": gen, "feeder": "F", "copies": 5, "section": k + 1, "cable": c, "length": ln,
                 "unit": "ft", "units": u}
                for gen, secs in (("PV", _PV_FEEDER), ("BESS", _BESS_FEEDER)) for k, (c, ln, u) in enumerate(secs)],
    "cables": [
        {"cable": "1000AL", "unit": "km", "r_per": 0.0460, "x_per": 0.1050, "b_per": 147.027},
        {"cable": "500AL", "unit": "km", "r_per": 0.0900, "x_per": 0.1180, "b_per": 113.097},
        {"cable": "4/0AL", "unit": "km", "r_per": 0.2010, "x_per": 0.1330, "b_per": 82.938},
        {"cable": "1/0AL", "unit": "km", "r_per": 0.4040, "x_per": 0.1480, "b_per": 64.0885},
    ],
    "collectors": [],
    "loads_shunts": [{"kind": "LOAD", "at": "MPT1", "mw": 1.0, "mvar": 0.3, "name": "AUX"}],
    "machines": [],
    "bus_numbers": [],
    "dyr_params": [{"generator": "PLANT", "model": "PLNTBU1", "parameter": "Kp", "value": 1.0},
                   {"generator": "BESS", "model": "REECCU1", "parameter": "SOCini", "value": 0.5}],
    "dyr_records": [{"generator": "#PV",
                     "record": "{BUS} 'USRMDL' {ID} 'MYMODEL' 101 0 1 3 2 1  1  0.02 0.5 1.0 /   <- a row "
                               "starting with # is skipped"}],
    "pscad_params": [{"generator": "#PV", "parameter": "Ninv", "value": "{UNITS}"},
                     {"generator": "#PV", "parameter": "Vt0", "value": "{V_PU} [pu]"}],
}

README = [
    ("SMIB INPUT", "Fill in the sheets, save, then run:   python smib_build.py <this workbook>.xlsx"),
    ("", "It writes the PSS/E model (.raw rev 33 and .dyr; with PSS/E found, a solved .sav and a flat-run check) "
         "and the PSCAD build script (run it where PSCAD 5 and its Python automation library mhi.pscad are: it "
         "builds the .pscx), with a report of every equivalent, the load flow and anything to check."),
    ("", "Blank cells take the default in the column note (row 2 of each sheet). Any row whose first cell starts "
         "with # is skipped (row 2 is one)."),
    ("BUSES", "Name a bus by its role: POI | HV (the gen-tie's end; the POI when there is no gen-tie) | <MPT> or "
              "<MPT>:LV | <MPT>:TV (3-winding) | <MPT>:HV (an MPT with a tie of its own) | <shared collector> (its "
              "far end) | <generator>:MV (GSU HV side) | <generator> or <generator>:LV (terminal) | IB (infinite "
              "bus). Numbers are automatic from First bus number; Bus_Numbers sets any one."),
    ("Plant", "Bases, the POI, the grid (SCR and X/R, or short-circuit MVA, or R/X), the gen-tie, the operating "
              "point, the plant controller and the PSCAD run settings."),
    ("MODEL TYPE", "Plant: Model type sets PSS/E and PSCAD alike. SMIB (default): each generator is one machine "
                   "behind one GSU on a collector equivalent of its own (WECC, each feeder weighed by its MW) from "
                   "its MPT winding. DETAILED: every feeder section is a branch (identical feeder "
                   "copies lumped), the units of a generator at each node a machine of their own behind their own "
                   "GSU; a generator's Dyr_Params, Dyr_Records and PSCAD_Params rows apply to all its machines."),
    ("", "Control mode Q: the plant holds the POI voltage with Q at POI into the grid; PF: with that power factor; "
         "V: the plant holds the POI voltage with the infinite bus at Grid voltage. The infinite-bus voltage (and "
         "the generators' Q) come out of the load flow."),
    ("", "Plant controller (PSS/E .dyr): REPC_A = a REPCA1 on each generator, each holding the POI; REPC_B = one "
         "controller for the whole plant (hybrid PV + BESS): PLNTBU1 at the POI, and on each generator a REAX4BU1 "
         "that takes its share of the plant's Q (Qmax / sum of Qmax) and P (Pmax / sum of Pmax). REGC_A / REPC_A "
         "records: standard = REGCA1 / REPCA1 from the PSS/E library; USRMDL = REGCAU1 / REPCAU1."),
    ("MPT", "One row per main power transformer; HV winding first. Three windings: TV kV (blank: the LV's, a split "
            "LV winding), the LV and TV MVA (blank: the MVA shared out by the units on each), the three impedances "
            "(Z HV-TV blank: Z HV-LV; Z LV-TV blank: 1.8 x Z HV-LV, a split LV winding's) and their bases. Parallel units: identical units in parallel. Tie: a line of its own from HV to it. "
            "MVA blank: the MVA of the units on it (Unit MVA x Units), following them -- the GUI's Size MPTs from "
            "the generators suggests a rating. Rate A, B, C: its normal and emergency ratings (PSS/E RATEA, RATEB, "
            "RATEC; PSLF rate1, rate2, rate3)."),
    ("Generators", "One row per generator (PV, BESS, wind, synchronous ...): the units (count and one unit's "
                   "data; the model is their aggregate), its GSU (one GSU's data, and Units per GSU or the GSU "
                   "count -- the GUI's GSUs page: Size GSUs from the units; Rate A, B, C one GSU's), its collector "
                   "(FEEDERS: the WECC equivalent of its rows in Feeders; EQUIVALENT: R X B given; NONE), how it "
                   "shares the POI's Q, its electrical control model in the .dyr (REEC_A: REECA1, PV and wind; "
                   "REEC_C: REECCU1, BESS; REEC_D: REECDU1, PV, BESS and WT4) and its PSCAD model (vendor library "
                   "and definition, or blank for a voltage source at the solved terminal voltage: a network check "
                   "against PSS/E) and where the inverter connects (LV or MV side of the GSU)."),
    ("Feeders", "The collector feeders section by section from the collector bus out: cable, length and the units "
                "tapped at the section's far end. A daisy chain needs no From section; a lateral names the section "
                "it leaves from (its far end); 0 = straight from the collector bus. Copies: identical feeders. "
                "The feeders on an MPT: those of the generators on it -- the GUI's Feeders on each MPT sets how many "
                "each has (its units spread over them), and they follow a generator's Units when it changes. "
                "WECC equivalent: Zeq = sum(Zi Wi^2) / W^2, Beq = sum(Bi), Wi = MW of the units downstream of "
                "section i. R ohm, X ohm, B uS: a section's own values (its parallel cables together) in place of "
                "Cable x Length -- the GUI's Fill R, X, B from cables shows them to change; Clear R, X, B goes "
                "back. A section with R and X needs no cable."),
    ("Cables", "Cable data per length (R, X in ohm; B in uS, or C in uF instead -- the GUI's C to B turns C into B) "
               "and the length unit they are per; Rate A, "
               "B, C one cable's ratings in MVA at its collector's kV (sqrt(3) x kV x amps / 1000): a section's x its "
               "parallel cables, a collector equivalent's its sections from the collector bus together."),
    ("Collectors", "Collector systems shared by more than one generator: name and the bus it starts from. Their "
                   "Feeders rows say whose units each section carries (Units of); the generators on it Connect to "
                   "its name."),
    ("Loads_Shunts", "Auxiliary loads (constant impedance, MW and MVAr at 1 pu) and shunts (MVAr at 1 pu: + "
                     "capacitor, - reactor) on any bus."),
    ("Machines", "DETAILED model only: a machine's own Pgen, Pmax, Pmin, Qmax, Qmin, fixed Q, MBASE and ID in place "
                 "of its share of its generator's (the GUI's 'List the DETAILED machines' fills in their names and "
                 "values). Scale up (GUI) or --scale: a test plant -- MPTs (copies of the first, with its generators), "
                 "feeders per MPT, GSUs per feeder and inverters per GSU."),
    ("Bus_Numbers", "Bus numbers or names of your own, by role (the report lists every role and its number)."),
    ("Dyr_Params", "Any parameter of the WECC-approved generic models, by generator, model and PSS/E name or "
                   "position (ICON2, CON14) -- IB for the infinite bus, PLANT for the plant controller. Inverters: "
                   "REGCA1 (REGC_A) + REECA1 / REECCU1 / REECDU1 (REEC_A / C / D) + REPCA1 (REPC_A) or REAX4BU1 "
                   "(with PLNTBU1, REPC_B); WT3: + WTDTA1 WTARA1 WTPTA1 WTTQA1; SYNC: its Machine, Exciter and "
                   "Governor models (Generators; blank: GENROU SEXS TGOV1). REECCU1, "
                   "REECDU1, PLNTBU1, REAX4BU1 (and REGCAU1, REPCAU1) are user models: the WECC user-model DLL must "
                   "be loaded for the PSS/E check (ADDLIB_IDV / DLLS in the script). The report lists every model's "
                   "parameters with the values used. In the GUI, the Dyr_Params tab shows every model's parameters "
                   "with the values the .dyr gets, a column per generator: double-click one to change it (it becomes "
                   "a row here), clear it to go back to the default."),
    ("Dyr_Records", "Records of your own (vendor or user models), one per row, ending in /. {BUS} {ID} {MBASE} "
                    "{POI} {IB} {HV} {MV} and {<bus role>} are filled in. Type USER generators have only these. "
                    "Models in user DLLs: list the DLLs in DLLS in the script for the PSS/E check."),
    ("PSCAD_Params", "Parameters of each generator's vendor PSCAD model (UDM), as PSCAD takes them. Filled in "
                     "for each inverter it stands for (DETAILED: each node): {UNITS} units, {MVA} their MVA, "
                     "{P_MW} {Q_MVAR} their P and Q where it connects, {P_PU} {Q_PU} on {MVA}, {UNIT_MVA} "
                     "{UNIT_P_MW} {UNIT_Q_MVAR} one unit's, {V_PU} {V_KV} {KV} {ANGLE} the voltage there, its base "
                     "and PSCAD angle, {P_INV_MW} {Q_INV_MVAR} {V_INV_PU} at the inverters' own terminals (a UDM on "
                     "the MV side), {FREQ} -- e.g. Ninv = {UNITS}, Vt0 = {V_PU}. In the GUI, the PSCAD_Params tab "
                     "shows each vendor model's parameters, read from its definition in its file, a column per "
                     "generator -- and, picked under Definition, those of the parts inside it (its controller ...) "
                     "and its signals set by a constant, slider or switch (FreqDroop ...): double-click one to "
                     "change it (a choice from its list), clear it to go back to the model's own. A part's "
                     "setting is Part/Setting (Controller4/UFLevel1), a signal's its source's Value "
                     "(var_switch#5/Value); they change a copy of the definition of the generator's own."),
    ("PSCAD", "The .pscx is built in the same run when PSCAD 5 and its Python library mhi.pscad (pip install "
              "mhi.pscad) are on the PC; otherwise run <name>_build_pscad.py where they are. "
              "The case holds the grid (source and Thevenin impedance), the gen-tie, the MPTs, each collector (the "
              "WECC equivalent as a pi section, or DETAILED: every feeder section, identical feeder copies lumped), "
              "the GSUs and, on each GSU's LV side, the inverter: your UDM (PSCAD library, definition and AC port "
              "in Generators) or a stand-in source at the solved voltage -- delete it and wire your UDM in its "
              "place (the report and the build log list every inverter node). Or take the UDM from the OEM's "
              "PSCAD model: Generators, Pick UDM from OEM model -- the model's definitions are listed (their ports, "
              "parameters and the IDs of the ones placed in it); the one picked is added to the case's definitions "
              "(with the ones it uses and the model's resources, .lib / .obj) and put at every GSU of that "
              "generator (every machine of the DETAILED model), PSCAD_Params rows ({UNITS}, {P_PU} ...) set on "
              "each; or, with a component ID, that placed one is copied instead, its settings and all. PSCAD "
              "inverter at MV: for a UDM "
              "with its GSU inside, the inverter on the GSU's HV side and no GSU drawn. It is drawn as a "
              "single-line diagram with wires, left to right from the grid; where PSCAD's R, L and C are "
              "single-phase, each is drawn three times between breakouts."),
]


# ============================================================================
# SMALL HELPERS
# ============================================================================
class InputError(Exception):
    """An input the script cannot use; the message names the sheet and row."""


REVIEW_MARKS = ("CHECK:", "WARNING", "***", "NOT FLAT", "SUSPECT")


class Log(object):
    def __init__(self, echo=True):
        self.lines = []
        self.echo = echo

    def __call__(self, msg=""):
        if self.echo:
            print(msg)
        self.lines.extend(str(msg).split("\n"))

    def review(self):
        return [ln for ln in self.lines if any(m in ln for m in REVIEW_MARKS)]


def _new(path):
    """`path`, after making sure nothing is there -- nothing is ever overwritten."""
    if os.path.exists(path):
        raise RuntimeError("will not overwrite %s" % path)
    return path


def _write_text(path, text):
    with io.open(_new(path), "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


def _txt(v):
    """A cell as text: None for blank; 1.0 -> '1'."""
    if v is None:
        return None
    if isinstance(v, float):
        if v != v:
            return None
        return ("%d" % v) if v == int(v) and abs(v) < 1e15 else repr(v)
    s = str(v).strip()
    return s or None


def _num(row, key, where, default=None, required=False, positive=False, label=None):
    v = row.get(key)
    if isinstance(v, str):
        v = v.strip() or None
    if v is None:
        if required:
            raise InputError("%s: %s is missing" % (where, label or _label(key, where)))
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InputError("%s: %s = %r is not a number" % (where, label or _label(key, where), v))
    if x != x or x in (float("inf"), float("-inf")):
        raise InputError("%s: %s = %r is not a number" % (where, label or _label(key, where), v))
    if positive and not x > 0:
        raise InputError("%s: %s must be above 0 (it is %s)" % (where, label or _label(key, where), v))
    return x


def _int(row, key, where, default=None, required=False, minimum=None):
    x = _num(row, key, where, None, required)
    if x is None:
        return default
    if abs(x - round(x)) > 1e-9:
        raise InputError("%s: %s = %s is not a whole number" % (where, _label(key, where), x))
    x = int(round(x))
    if minimum is not None and x < minimum:
        raise InputError("%s: %s = %d is below %d" % (where, _label(key, where), x, minimum))
    return x


def _abc(row, keys, where, a=None, notes=None):
    """Rate A, B and C (MVA) from a row's three fields -> (A, B, C): A blank = a, B blank = A, C blank = B; none
       below 0. notes: a B or C given below the one before it, or given with no Rate A, is CHECKed there."""
    got = [_num(row, k, where) for k in keys]
    for k, v in zip(keys, got):
        if v is not None and v < 0:
            raise InputError("%s: %s must be 0 or more (it is %.6g)" % (where, _label(k, where), v))
    ra = a if got[0] is None else got[0]
    rb = ra if got[1] is None else got[1]
    rc = rb if got[2] is None else got[2]
    if notes is not None:
        for (k0, v0), (k1, v1) in (((keys[0], ra), (keys[1], got[1])), ((keys[1], rb), (keys[2], got[2]))):
            if v1 and v0 and v1 < v0 - 1e-9:
                notes.append("CHECK: %s: %s %.6g is below %s %.6g -- B and C are the emergency ratings, at or above "
                             "A" % (where, _label(k1, where), v1, _label(k0, where), v0))
        if not ra and (got[1] or got[2]):
            notes.append("CHECK: %s: %s blank or 0, %s given -- no normal rating (PSS/E RATEA 0)" % (
                where, _label(keys[0], where), " and ".join(_label(k, where) for k, v in zip(keys[1:], got[1:])
                                                             if v)))
    return ra or 0.0, rb or 0.0, rc or 0.0


def _rabc(x):
    """A branch's (or a transformer's first winding's) Rate A, B, C: B blank = A, C blank = B."""
    a = x["rate"] or 0.0
    b = x.get("rate_b")
    b = a if b is None else b
    c = x.get("rate_c")
    return a, b, b if c is None else c


def _rabc3(t):
    """A three-winding transformer's Rate A, B, C for each winding: the HV winding's as given; the LV and TV
       windings' A their MVA, B and C in proportion (the HV winding's B / A, C / A)."""
    a, b, c = _rabc(t)
    out = [(a, b, c)]
    for k in ("mva2", "mva3"):
        m = t.get(k) or 0.0
        out.append((m, m * b / a, m * c / a) if a else (m, m, m))
    return out


def _choice(row, key, where, choices, default):
    s = _txt(row.get(key))
    if s is None:
        return default
    for c in choices:
        if s.upper() == c.upper():
            return c
    raise InputError("%s: %s = %r -- one of %s" % (where, _label(key, where), s, ", ".join(choices)))


_LABELS = {}
_SHEET_LABELS = {}                               # (sheet, key): the column's own title on that sheet
for _k, _lab, _u, _n, _c in PLANT_FIELDS:
    _LABELS[_k] = _lab
for _sh, _ik, _cols in TABLES:
    for _c in _cols:
        _LABELS.setdefault(_c[0], _c[1])
        _SHEET_LABELS[(_sh.upper(), _c[0])] = _c[1]


def _label(key, where=None):
    sheet = re.split(r"[\s(]", str(where or ""), maxsplit=1)[0].upper()
    return "'%s'" % _SHEET_LABELS.get((sheet, key), _LABELS.get(key, key))


def _unused(notes, row, keys, where, why, defaults=None):
    """Values given that the model built does not use: one NOTE (into notes: a list, or a function taking it)
       naming them, their values and why -- none is dropped unsaid. A value equal to its blank's (defaults: key ->
       what blank means) says nothing more: left out. -> the keys named."""
    got = []
    for k in keys:
        v = _txt(row.get(k))
        if v is None:
            continue
        dv = (defaults or {}).get(k)
        if dv is not None:
            try:
                if float(v) == float(dv):
                    continue
            except ValueError:
                if v.upper() == str(dv).upper():
                    continue
        got.append(k)
    if got:
        (notes.append if hasattr(notes, "append") else notes)("NOTE: %s: %s not used -- %s" % (where, ", ".join(
            "%s %s" % (_label(k, where), _txt(row.get(k))) for k in got), why))
    return got


def _g(x, nd=6):
    """A number short and exact enough for PSS/E's and PSCAD's free format."""
    if isinstance(x, bool):
        return "1" if x else "0"
    if isinstance(x, int):
        return "%d" % x
    if x == 0:
        return "0.0"
    s = "%.*g" % (nd, x)
    if "e" not in s and "." not in s:
        s += ".0"
    return s


def _key(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _safe_name(s, n=30):
    s = re.sub(r"[^A-Za-z0-9_]", "_", str(s or "SMIB")).strip("_") or "SMIB"
    if not s[0].isalpha():
        s = "M_" + s
    return s[:n]


# ============================================================================
# EXCEL -- read and write .xlsx with the standard library
# ============================================================================
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NSR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _col_letters(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def read_xlsx(path):
    """{sheet name: rows}; rows[k] is Excel row k + 1, a list of cell values (text, float or None)."""
    import xml.etree.ElementTree as ET
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        raise InputError("%s is not an .xlsx workbook (%s) -- save it from Excel as .xlsx" % (path, e))
    try:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_NS + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_NS + "t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")):
                rels[r.get("Id")] = r.get("Target") or ""
        out = collections.OrderedDict()
        for sh in ET.fromstring(z.read("xl/workbook.xml")).iter(_NS + "sheet"):
            target = rels.get(sh.get(_NSR + "id"), "")
            part = target.lstrip("/") if target.startswith("/") else posixpath.normpath("xl/" + target)
            if part in names:
                out[sh.get("name")] = _sheet_rows(ET.fromstring(z.read(part)), shared)
    except (KeyError, ET.ParseError, ValueError) as e:     # a zip, but not a workbook Excel can open
        raise InputError("%s is not an .xlsx workbook Excel wrote (%s) -- save it from Excel as .xlsx" % (path, e))
    finally:
        z.close()
    return out


def _sheet_rows(root, shared):
    rows = []
    for row in root.iter(_NS + "row"):
        try:
            rn = int(row.get("r"))
        except (TypeError, ValueError):
            rn = len(rows) + 1
        cells = {}
        for c in row.iter(_NS + "c"):
            m = re.match(r"([A-Z]+)", c.get("r") or "")
            col = _col_index(m.group(1)) if m else len(cells)
            t = c.get("t")
            v = c.find(_NS + "v")
            if t == "inlineStr":
                node = c.find(_NS + "is")
                val = "".join(x.text or "" for x in node.iter(_NS + "t")) if node is not None else None
            elif v is None or v.text is None:
                val = None
            elif t == "s":
                try:
                    val = shared[int(v.text)]
                except (ValueError, IndexError):
                    val = None
            elif t == "b":
                val = "YES" if v.text.strip() == "1" else "NO"
            elif t in ("str", "e"):
                val = v.text
            else:
                try:
                    val = float(v.text)
                except ValueError:
                    val = v.text
            if isinstance(val, str) and not val.strip():
                val = None
            cells[col] = val
        while len(rows) < rn - 1:
            rows.append([])
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def _find_sheet(book, name):
    for k in book:
        if _key(k) == _key(name):
            return book[k]
    return None


def _skip(row):
    first = next((x for x in row if x is not None), None)
    return first is None or (isinstance(first, str) and first.strip().startswith("#"))


def parse_workbook(book, path, keep_comments=False):
    """The workbook as {"plant": {key: value}, "<table>": [{key: value, "_where": ...}]}. keep_comments: a row of
       the user's starting with # (one set aside) is kept as it is -- the GUI shows it; the build skips it."""
    inp = {"plant": {}, "_source": path}
    rows = _find_sheet(book, "Plant")
    if rows is None:
        raise InputError("%s has no sheet 'Plant' -- start from: python smib_build.py --template x.xlsx" % path)
    items = dict((_key(lab), k) for k, lab, _u, _n, _c in PLANT_FIELDS)
    items.update((_key(k), k) for k, _l, _u, _n, _c in PLANT_FIELDS)
    unknown, fmt = [], FORMAT
    for k, r in enumerate(rows):
        if k == 0 or not r or _skip(r):
            continue
        item = _txt(r[0])
        key = items.get(_key(item or ""))
        if key == "tie_unit" and len(r) > 3 and _txt(r[3]) == "km":
            fmt = 2                                  # a workbook from when a blank gen-tie unit was km
        if key is None:
            if len(r) > 1 and _txt(r[1]) is not None:       # a section title has no value
                unknown.append(item)
            continue
        inp["plant"][key] = r[1] if len(r) > 1 else None
    inp["_unknown"] = ["Plant: '%s'" % u for u in unknown if u]
    for sheet, key, cols in TABLES:
        inp[key] = []
        rows = _find_sheet(book, sheet)
        if not rows:
            continue
        names = {}
        for c in cols:
            names[_key(c[1])] = c[0]
            names[_key(c[0])] = c[0]
        for old, k in COL_ALIASES.get(sheet, ()):
            names.setdefault(_key(old), k)
        head, hrow = None, 0
        for k, r in enumerate(rows):
            if r and any(x is not None for x in r):
                head, hrow = r, k
                break
        if head is None:
            continue
        colmap = {}
        for i, h in enumerate(head):
            if h is None:
                continue
            kk = names.get(_key(h))
            if kk is None:
                inp["_unknown"].append("%s: column '%s'" % (sheet, h))
            else:
                colmap[i] = kk
        if key == "mpt":                             # its notes row (the template's): no LV MVA default before 4
            notes = rows[hrow + 1] if hrow + 1 < len(rows) and rows[hrow + 1] and _skip(rows[hrow + 1]) else []
            said = [str(notes[i]) for i, kk in colmap.items() if kk == "lv_mva" and i < len(notes) and notes[i]]
            if said and "blank" not in said[0]:
                fmt = min(fmt, 3)
        if key == "feeders":                         # its notes row (the template's): what a blank unit meant
            notes = rows[hrow + 1] if hrow + 1 < len(rows) and rows[hrow + 1] and _skip(rows[hrow + 1]) else []
            said = [str(notes[i]) for i, kk in colmap.items() if kk == "unit" and i < len(notes) and notes[i]]
            if said and "blank = km" in said[0]:
                fmt = 1
        for k in range(hrow + 1, len(rows)):
            r = rows[k]
            if not r or _skip(r) and (not keep_comments or k == hrow + 1 or all(x is None for x in r)):
                continue                             # (right below the titles: the template's notes row)
            d = {"_where": "%s row %d" % (sheet, k + 1)}
            for i, kk in colmap.items():
                d[kk] = r[i] if i < len(r) else None
            if any(v is not None for kk, v in d.items() if kk != "_where"):
                inp[key].append(d)
    _old_blanks(inp, fmt)
    return inp


_XL_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="10"><font><sz val="10"/><name val="Calibri"/></font>'
    '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    '<font><i/><sz val="9"/><color rgb="FF595959"/><name val="Calibri"/></font>'
    '<font><b/><sz val="10"/><name val="Calibri"/></font>'
    '<font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    '<font><i/><sz val="10"/><color rgb="FFD9E1F2"/><name val="Calibri"/></font>'
    '<font><b/><sz val="11"/><color rgb="FF1F3864"/><name val="Calibri"/></font>'
    '<font><u/><sz val="10"/><color rgb="FF0563C1"/><name val="Calibri"/></font>'
    '<font><b/><sz val="10"/><color rgb="FFC00000"/><name val="Calibri"/></font>'
    '<font><sz val="9"/><name val="Consolas"/></font></fonts>'
    '<fills count="7"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDDEBF7"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F2"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFE2EFDA"/></patternFill></fill></fills>'
    '<borders count="3"><border><left/><right/><top/><bottom/><diagonal/></border>'
    '<border><left style="thin"><color rgb="FFBFBFBF"/></left><right style="thin"><color rgb="FFBFBFBF"/></right>'
    '<top style="thin"><color rgb="FFBFBFBF"/></top><bottom style="thin"><color rgb="FFBFBFBF"/></bottom>'
    '<diagonal/></border>'
    '<border><left/><right/><top/><bottom style="medium"><color rgb="FF1F3864"/></bottom><diagonal/></border>'
    '</borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="15">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
    '<alignment vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="4" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
    '<alignment vertical="center" indent="1"/></xf>'
    '<xf numFmtId="0" fontId="6" fillId="4" borderId="2" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment vertical="center"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="7" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" '
    'applyAlignment="1"><alignment vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="5" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
    '<alignment vertical="center" indent="1"/></xf>'
    '<xf numFmtId="0" fontId="8" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" '
    'applyAlignment="1"><alignment vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" '
    'applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="9" fillId="3" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" '
    'applyAlignment="1"><alignment vertical="top"/></xf>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')
(XS_HEAD, XS_NOTE, XS_INPUT, XS_BOLD, XS_WRAP, XS_TITLE, XS_SECTION, XS_CELL, XS_LINK, XS_SUB, XS_REQ, XS_ITEM,
 XS_TAG, XS_CODE) = range(1, 15)


def _xl_cell(ref, v, st):
    if v is None or v == "":
        return '<c r="%s" s="%d"/>' % (ref, st) if st else ""
    if isinstance(v, bool):
        v = "YES" if v else "NO"
    if isinstance(v, (int, float)):
        return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, st, repr(v) if isinstance(v, float) else v)
    return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, st, _xml_escape(str(v)))


def _xl_sheet(rows, widths, freeze=(0, 0), lists=(), merges=(), links=(), heights=None, tab=None, grid=True,
              landscape=True, fit=False):
    """rows: lists of values or (value, style); lists: [(range, [choices])] for drop-down cells; merges: ["A1:D1"];
       links: [(cell, sheet)] to another sheet; heights: {row (1-based): points}; tab: the tab's colour (RRGGBB)."""
    xs, ys = freeze
    heights = heights or {}
    o = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">']
    if tab or fit:
        o.append('<sheetPr>%s%s</sheetPr>' % (('<tabColor rgb="FF%s"/>' % tab) if tab else "",
                                             '<pageSetUpPr fitToPage="1"/>' if fit else ""))
    o.append('<sheetViews>')
    gl = '' if grid else ' showGridLines="0"'
    if xs or ys:
        tl = "%s%d" % (_col_letters(xs), ys + 1)
        pane = "bottomRight" if xs and ys else ("bottomLeft" if ys else "topRight")
        o.append('<sheetView%s workbookViewId="0"><pane %s%stopLeftCell="%s" activePane="%s" state="frozen"/>'
                 '</sheetView>' % (gl, ('xSplit="%d" ' % xs) if xs else "", ('ySplit="%d" ' % ys) if ys else "", tl,
                                   pane))
    else:
        o.append('<sheetView%s workbookViewId="0"/>' % gl)
    o.append('</sheetViews><sheetFormatPr defaultRowHeight="14"/><cols>')
    for i, w in enumerate(widths):
        o.append('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
    o.append('</cols><sheetData>')
    for ri, r in enumerate(rows):
        cells = []
        for ci, x in enumerate(r):
            v, st = x if isinstance(x, tuple) else (x, 0)
            cells.append(_xl_cell("%s%d" % (_col_letters(ci), ri + 1), v, st))
        h = heights.get(ri + 1)
        o.append('<row r="%d"%s>%s</row>' % (ri + 1, (' ht="%s" customHeight="1"' % h) if h else "", "".join(cells)))
    o.append('</sheetData>')
    if merges:
        o.append('<mergeCells count="%d">%s</mergeCells>' % (len(merges), "".join('<mergeCell ref="%s"/>' % m
                                                                                for m in merges)))
    if lists:
        o.append('<dataValidations count="%d">' % len(lists))
        for rng, choices in lists:
            o.append('<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="%s">'
                     '<formula1>"%s"</formula1></dataValidation>' % (rng, _xml_escape(",".join(choices))))
        o.append('</dataValidations>')
    if links:
        o.append('<hyperlinks>%s</hyperlinks>' % "".join(
            '<hyperlink ref="%s" location="%s" display="%s"/>' % (cell, _xml_escape("'%s'!A1" % sh), _xml_escape(sh))
            for cell, sh in links))
    o.append('<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5" header="0.3" footer="0.3"/>')
    o.append('<pageSetup orientation="%s"%s/>' % ("landscape" if landscape else "portrait",
                                                  ' fitToWidth="1" fitToHeight="0"' if fit else ""))
    o.append('</worksheet>')
    return "".join(o)


def write_xlsx(path, sheets):
    """sheets: [(name, xml)]."""
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/xl/workbook.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
          '<Override PartName="/xl/styles.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>']
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdS" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
            'Target="styles.xml"/>']
    z = zipfile.ZipFile(_new(path), "w", zipfile.ZIP_DEFLATED)
    try:
        for i, (name, xml) in enumerate(sheets):
            n = i + 1
            ct.append('<Override PartName="/xl/worksheets/sheet%d.xml" '
                      'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % n)
            wb.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (_xml_escape(name[:31]), n, n))
            rels.append('<Relationship Id="rId%d" '
                        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                        'Target="worksheets/sheet%d.xml"/>' % (n, n))
            z.writestr("xl/worksheets/sheet%d.xml" % n, xml)
        ct.append("</Types>")
        wb.append("</sheets></workbook>")
        rels.append("</Relationships>")
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        z.writestr("xl/styles.xml", _XL_STYLES)
    finally:
        z.close()


# ---- the workbook's reference sheets ----
PLANT_SECTIONS = {"name": "MODEL", "poi_kv": "POI AND GRID (the infinite bus behind its Thevenin impedance)",
                  "tie_length": "GEN-TIE (POI to the HV bus; blank = none)",
                  "control_mode": "OPERATING POINT (the load flow's targets at the POI)",
                  "plant_ctrl": "PLANT CONTROLLER (PSS/E .dyr)", "pscad_time_step_us": "PSCAD RUN",
                  "pscad_fault": "PSCAD FAULT AT THE POI", "pscad_libraries": "PSCAD LIBRARIES"}

MODEL_DOC = {
    "GENCLS": "infinite bus (IB): classical machine, H = 0",
    "REGCA1": "generator / converter interface, inverters (REGC_A)",
    "REGCAU1": "REGC_A as the WECC user model (Plant: REGC_A / REPC_A records USRMDL)",
    "REECA1": "electrical control REEC_A: PV, wind (WT3, WT4)",
    "REECCU1": "electrical control REEC_C: BESS, with state of charge",
    "REECDU1": "electrical control REEC_D: PV, BESS, WT4 (10-point VDL curves, current compensation)",
    "REPCA1": "plant controller REPC_A, one on each generator (Plant controller REPC_A)",
    "REPCAU1": "REPC_A as the WECC user model",
    "PLNTBU1": "plant controller REPC_B, one for the whole plant at the POI (Plant controller REPC_B)",
    "REAX4BU1": "REPC_B's auxiliary model on each generator: its share of the plant's P and Q",
    "WTDTA1": "WT3 drive train", "WTARA1": "WT3 aerodynamics", "WTPTA1": "WT3 pitch control",
    "WTTQA1": "WT3 torque control", "GENROU": "synchronous machine, round rotor (type SYNC; Machine model)",
    "SEXS": "simplified excitation system (type SYNC; Exciter model)",
    "TGOV1": "steam turbine-governor (type SYNC; Governor model)",
    "GENSAL": "synchronous machine, salient pole: hydro (type SYNC; Machine model)",
    "IEEET1": "IEEE type 1 excitation system: a DC exciter (type SYNC; Exciter model)",
    "ESST1A": "IEEE ST1A static excitation system, fed from the terminals (type SYNC; Exciter model)",
    "EXST1": "IEEE ST1 static excitation system, the older form (type SYNC; Exciter model)",
    "HYGOV": "hydro turbine-governor with its penstock (type SYNC; Governor model)",
    "GAST": "gas turbine-governor (type SYNC; Governor model)",
    "IEEEG1": "IEEE type 1 steam turbine-governor, its stages (type SYNC; Governor model)"}

AUTO_PARAMS = {"REECA1": ("QMax", "QMin", "PMAX", "PMIN", "PFLAG"), "REECCU1": ("QMax", "QMin", "PMAX", "PMIN"),
               "REECDU1": ("QMax", "QMin", "PMAX", "PMIN", "Ke"),
               "REPCA1": ("BUSR", "BUS1", "BUS2", "CKT", "Qmax", "Qmin", "Pmax", "Pmin", "Dup"),
               "REPCAU1": ("BUSR", "BUS1", "BUS2", "CKT", "Qmax", "Qmin", "Pmax", "Pmin", "Dup"),
               "PLNTBU1": ("BUSR", "BUS1", "BUS2", "CKT", "Qmax", "Qmin", "Pmax", "Pmin", "Dup", "MVAbase"),
               "REAX4BU1": ("PCBUS", "Kz", "Kw", "Qmax", "Qmin", "Pmax", "Pmin"), "WTTQA1": ("TRATE",),
               "TGOV1": ("VMAX",), "GAST": ("VMAX",), "IEEEG1": ("PMAX",), "HYGOV": ("GMAX",)}

PARAM_DOC = {
    "H": "inertia constant (s; 0 = infinite bus)", "D": "damping (pu)",
    "LVPLSW": "low-voltage power logic: 0 off, 1 on", "Tg": "converter current control time constant (s)",
    "Rrpwr": "active current recovery rate after a fault (pu/s)", "Brkpt": "LVPL breakpoint voltage (pu)",
    "Zerox": "LVPL zero-crossing voltage (pu)", "Lvpl1": "LVPL current limit at the breakpoint (pu)",
    "Volim": "high-voltage reactive current limit: voltage (pu)",
    "Lvpnt1": "low-voltage active current management: upper voltage (pu)",
    "Lvpnt0": "low-voltage active current management: lower voltage (pu)",
    "Iolim": "high-voltage clamp: current limit (pu, negative)",
    "Tfltr": "voltage (or Q) measurement filter time constant (s)", "Khv": "high-voltage clamp acceleration factor",
    "Iqrmax": "reactive current rate up (pu/s)", "Iqrmin": "reactive current rate down (pu/s)",
    "Accel": "acceleration factor of the converter's network solution",
    "BUSR": "regulated bus (0 = the terminal)", "PFFLAG": "1 constant power factor, 0 Q control",
    "VFLAG": "1 voltage loop (Kvp, Kvi) after the Q loop, 0 Q loop only",
    "QFLAG": "1 voltage / Q control, 0 constant power factor or Q", "PFLAG": "1 P reference times speed (wind)",
    "PQFLAG": "at the current limit: 0 Q priority, 1 P priority",
    "VCMPFLAG": "1 current compensation (Rc, Xc), 0 reactive droop (Kc)",
    "Vdip": "voltage below which the dip logic acts (pu)", "Vup": "voltage above which the dip logic acts (pu)",
    "Trv": "terminal voltage filter time constant (s)", "dbd1": "deadband, lower edge (pu, 0 or less)",
    "dbd2": "deadband, upper edge (pu, 0 or more)", "Kqv": "reactive current injection gain in a dip (pu/pu)",
    "Iqh1": "reactive current injection, upper limit (pu)", "Iql1": "reactive current injection, lower limit (pu)",
    "Vref0": "reference voltage for the injection (pu; 0 = the initial terminal voltage)",
    "Iqfrz": "reactive current held after a dip (pu)", "Thld": "time the injection is held after a dip (s)",
    "Thld2": "time the active current limit is held after a dip (s)",
    "Tp": "active power measurement filter time constant (s)", "QMax": "Q limit, upper (pu on MBASE)",
    "QMin": "Q limit, lower (pu on MBASE)", "VMAX": "voltage control limit, upper (pu)",
    "VMIN": "voltage control limit, lower (pu)", "Kqp": "Q loop proportional gain", "Kqi": "Q loop integral gain",
    "Kvp": "voltage loop proportional gain", "Kvi": "voltage loop integral gain",
    "Vref1": "voltage reference with VFLAG 0 (pu)", "Tiq": "reactive current command time constant (s)",
    "dPmax": "P reference ramp up (pu/s)", "dPmin": "P reference ramp down (pu/s)",
    "PMAX": "P limit, upper (pu on MBASE)", "PMIN": "P limit, lower (pu on MBASE; negative: charging)",
    "Imax": "converter current limit (pu on MBASE)", "Tpord": "P order filter time constant (s)",
    "T": "battery discharge time at rated power (s)", "SOCini": "initial state of charge (pu)",
    "SOCmax": "state of charge, upper limit (pu)", "SOCmin": "state of charge, lower limit (pu)",
    "Rc": "current / line drop compensation resistance (pu)", "Xc": "current / line drop compensation reactance (pu)",
    "Tr1": "compensated voltage filter time constant (s)", "Kc": "reactive droop gain",
    "Ke": "lower active current limit scaling: 0 no charging, -1 charging as discharging (storage)",
    "Vblkl": "voltage below which the inverter blocks (pu)", "Vblkh": "voltage above which the inverter blocks (pu)",
    "Tblk_delay": "blocking delay (s)", "BUS1": "P and Q measured on the branch from this bus",
    "BUS2": "... to this bus", "CKT": "... this circuit", "VCFLAG": "1 voltage control with line drop compensation, "
    "0 reactive droop", "REFFLAG": "1 voltage control, 0 Q control", "FFLAG": "1 frequency response on, 0 off",
    "Kp": "voltage / Q loop proportional gain", "Ki": "voltage / Q loop integral gain",
    "Tft": "lead time constant (s)", "Tfv": "lag time constant (s)",
    "Vfrz": "voltage below which the controller freezes (pu)", "emax": "error limit, upper (pu)",
    "emin": "error limit, lower (pu)", "Qmax": "Q command limit, upper (pu)", "Qmin": "Q command limit, lower (pu)",
    "Kpg": "frequency (P) loop proportional gain", "Kig": "frequency (P) loop integral gain",
    "fdbd1": "frequency deadband, lower edge (pu)", "fdbd2": "frequency deadband, upper edge (pu)",
    "femax": "frequency error limit, upper (pu)", "femin": "frequency error limit, lower (pu)",
    "Pmax": "P command limit, upper (pu)", "Pmin": "P command limit, lower (pu)",
    "Tlag": "P command lag time constant (s)", "Ddn": "droop gain, over-frequency (pu P / pu f)",
    "Dup": "droop gain, under-frequency (pu P / pu f)", "MVAbase": "the plant controller's MVA base",
    "PCBUS": "the plant controller's (PLNTBU1's) bus", "CON1": "reserved",
    "Kz": "its share of the plant's Q command", "Kw": "its share of the plant's P command",
    "DAMP": "drive train damping (pu)", "Htfrac": "turbine's share of the inertia",
    "Freq1": "first shaft torsional frequency (Hz)", "Dshaft": "shaft damping (pu)", "Ka": "aerodynamic gain",
    "Theta0": "initial pitch angle (deg)", "Kiw": "pitch control integral gain (speed)",
    "Kpw": "pitch control proportional gain (speed)", "Kic": "pitch compensation integral gain",
    "Kpc": "pitch compensation proportional gain", "Kcc": "pitch compensation cross gain",
    "TetaMax": "pitch angle, upper (deg)", "TetaMin": "pitch angle, lower (deg)",
    "RTetaMax": "pitch rate, upper (deg/s)", "RTetaMin": "pitch rate, lower (deg/s)",
    "TFLAG": "1 speed reference from power, 0 torque", "Kpp": "torque control proportional gain",
    "Kip": "torque control integral gain", "Twref": "speed reference time constant (s)",
    "Temax": "electrical torque, upper (pu)", "Temin": "electrical torque, lower (pu)",
    "TRATE": "turbine rating (MW)", "Tdop": "T'do (s)", "Tdopp": "T''do (s)", "Tqop": "T'qo (s)",
    "Tqopp": "T''qo (s)", "Xd": "Xd (pu)", "Xq": "Xq (pu)", "Xdp": "X'd (pu)", "Xqp": "X'q (pu)",
    "Xdpp": "X''d = X''q (pu)", "Xl": "leakage reactance (pu)", "S1": "saturation at 1.0 pu", "S12":
    "saturation at 1.2 pu", "TA_TB": "TA / TB", "TB": "TB (s)", "K": "exciter gain", "TE": "TE (s)",
    "EMIN": "field voltage, lower (pu)", "EMAX": "field voltage, upper (pu)", "R": "droop (pu)", "T1": "T1 (s)",
    "T2": "T2 (s)", "T3": "T3 (s)", "Dt": "turbine damping (pu)"}


_EXC = {"TR": "voltage measurement time constant (s)", "KA": "voltage regulator gain", "TA": "regulator time constant "
        "(s)", "VRMAX": "regulator output, upper (pu)", "VRMIN": "regulator output, lower (pu)", "KF": "rate feedback "
        "gain", "TF": "rate feedback time constant (s)", "TB": "lag time constant (s)", "TC": "lead time constant (s)",
        "VIMAX": "regulator input error, upper (pu)", "VIMIN": "regulator input error, lower (pu)",
        "KC": "rectifier loading factor (commutating reactance)"}
PARAM_DOC_OF = {  # a model's own meaning of a name other models use otherwise
    "IEEET1": dict(_EXC, KE="exciter constant (self-excited field)", TE="exciter time constant (s)",
                   Switch="0 (PSS/E's switch, not used)", E1="field voltage of the first saturation point (pu)",
                   SE1="saturation at E1", E2="field voltage of the second saturation point (pu)",
                   SE2="saturation at E2"),
    "ESST1A": dict(_EXC, UEL="where the underexcitation limiter acts: 1, 2 or 3", VOS="where the stabilizer acts: 1 "
                   "or 2", TC1="second lead time constant (s)", TB1="second lag time constant (s)",
                   VAMAX="amplifier output, upper (pu)", VAMIN="amplifier output, lower (pu)",
                   KLR="field current limiter gain", ILR="field current limit (pu)"),
    "EXST1": dict(_EXC),
    "HYGOV": {"R": "permanent droop (pu)", "r": "temporary droop (pu)", "Tr": "governor time constant (s)",
              "Tf": "filter time constant (s)", "Tg": "gate servo time constant (s)",
              "VELM": "gate velocity limit (pu/s)", "GMAX": "gate opening, upper (pu)",
              "GMIN": "gate opening, lower (pu)", "TW": "water inertia time constant (s)", "At": "turbine gain",
              "Dturb": "turbine damping (pu)", "qNL": "no-load flow (pu)"},
    "GAST": {"R": "droop (pu)", "T1": "fuel valve time constant (s)", "T2": "fuel system time constant (s)",
             "T3": "load limit time constant (s)", "AT": "load limit (ambient temperature) (pu)",
             "KT": "load limit feedback gain", "VMAX": "fuel valve, upper (pu)", "VMIN": "fuel valve, lower (pu)",
             "Dturb": "turbine damping (pu)"},
    "IEEEG1": dict([("IBUS", "a cross-compound unit's low-pressure machine bus (0 = none)"),
                    ("M", "its machine (0 = none)"), ("K", "governor gain, 1 / droop"),
                    ("T1", "governor lag time constant (s)"), ("T2", "governor lead time constant (s)"),
                    ("T3", "valve positioner time constant (s)"), ("Uo", "valve opening rate (pu/s)"),
                    ("Uc", "valve closing rate (pu/s, negative)"), ("PMAX", "valve opening, upper (pu)"),
                    ("PMIN", "valve opening, lower (pu)")] +
                   [("T%d" % k, "stage %d time constant (s)" % (k - 3)) for k in (4, 5, 6, 7)] +
                   [("K%d" % k, "stage %d's share of its power%s" % ((k + 1) // 2, " (high-pressure machine)" if k % 2
                                                                     else " (low-pressure machine)"))
                    for k in range(1, 9)]),
    "GENSAL": {"Tqopp": "T''qo (s)", "Xq": "Xq (pu)", "Xdpp": "X''d (pu)"},
}


def _param_doc(name, model=None):
    if name in PARAM_DOC_OF.get(model, {}):
        return PARAM_DOC_OF[model][name]
    m = re.match(r"^(V|I)(q|p)(\d+)$", name)
    if m:
        return "%s current limit curve (%s), point %s: %s" % (
            "reactive" if m.group(2) == "q" else "active", "VDL1" if m.group(2) == "q" else "VDL2", m.group(3),
            "voltage (pu)" if m.group(1) == "V" else "current limit (pu)")
    m = re.match(r"^(p|spd)(\d)$", name)
    if m:
        return "power-speed curve, point %s: %s" % (m.group(2), "power (pu)" if m.group(1) == "p" else "speed (pu)")
    return PARAM_DOC.get(name, "")


PSCAD_KEYS_DOC = [
    ("{UNITS}", "the units the inverter stands for"), ("{MVA}", "their MVA"),
    ("{P_MW}", "their P where the inverter connects (MW)"), ("{Q_MVAR}", "their Q there (MVAr)"),
    ("{P_PU}", "P on {MVA}"), ("{Q_PU}", "Q on {MVA}"), ("{UNIT_MVA}", "one unit's MVA"),
    ("{UNIT_P_MW}", "one unit's P (MW)"), ("{UNIT_Q_MVAR}", "one unit's Q (MVAr)"),
    ("{V_PU}", "the voltage where it connects (pu)"), ("{V_KV}", "the same in kV"), ("{KV}", "that bus's base kV"),
    ("{ANGLE}", "its PSCAD angle (deg, the transformers' phase shifts included)"), ("{FREQ}", "frequency (Hz)"),
    ("{P_INV_MW}", "P at the inverters' own terminals (MW; a UDM on the MV side)"),
    ("{Q_INV_MVAR}", "Q there (MVAr)"), ("{V_INV_PU}", "the voltage there (pu)")]


def _blank_means(note):
    m = re.search(r"blank\s*=\s*([^|;)]+)", note or "")
    return m.group(1).strip().rstrip(".,") if m else ""


def _says_more(note, v):
    """Whether a value says more than a blank would: it is given, and it is not the very default its note names
       (blank = 1, blank = YES ...) -- the GUI's simple view shows such a field even where it hides its kind."""
    v = _txt(v)
    if v is None:
        return False
    m = re.match(r"^([-+\w.]+)\s*(\(.*)?$", _blank_means(note))
    if not m:
        return True
    try:
        return float(m.group(1)) != float(v)
    except ValueError:
        return m.group(1).upper() != v.upper()


def _used_by(sheet, key):
    if re.match(r"^(gsu_|tie_)?rate(_[abc])?_mva$", key):            # ratings: the load-flow cases only
        return "PSS/E + PSLF"
    if sheet == "Dyr_Records" or key in ("regc_repc", "wmod"):
        return "PSS/E"
    if sheet == "Dyr_Params" or key in ("plant_ctrl", "regc", "reec", "zsorce_x"):
        return "PSS/E + PSLF"
    if sheet == "PSCAD_Params" or key.startswith("pscad"):
        return "PSCAD"
    if key in ("out_epc", "out_dyd"):
        return "PSLF"
    return "PSS/E + PSLF + PSCAD"


SHEET_INDEX = [
    ("Plant", "input", "model name and type, bases, POI and grid, gen-tie, operating point, plant controller, PSCAD"),
    ("MPT", "input", "main power transformers, 2 or 3 windings"),
    ("Generators", "input", "one row per generator: units, GSU, collector, Q control, dynamic and PSCAD models"),
    ("Feeders", "input", "the collector feeders, section by section"),
    ("Cables", "input", "cable data per length"),
    ("Collectors", "input", "collector systems shared by several generators"),
    ("Loads_Shunts", "input", "auxiliary loads, capacitor banks, reactors"),
    ("Machines", "input", "DETAILED: each machine's own Pgen, P and Q limits, MBASE and ID"),
    ("Bus_Numbers", "input", "bus numbers and names of your own (blank = automatic)"),
    ("Dyr_Params", "input", "any dynamic model parameter, by generator, model and name"),
    ("Dyr_Records", "input", ".dyr records of your own (vendor models)"),
    ("PSCAD_Params", "input", "the vendor PSCAD model's parameters, by generator"),
    ("Input_Reference", "reference", "every input: what it is, its unit, the default when blank, the choices, which "
     "model uses it"),
    ("Dynamic_Models", "reference", "every dynamic model's parameters: position, default and meaning")]
_TAB_IN, _TAB_REF, _TAB_HOME = "2E75B6", "70AD47", "1F3864"


def _rows_high(texts_widths, line=13.0, most=160.0):
    """A row's height (points) for wrapped text: [(text, column width)]."""
    n = 1
    for t, w in texts_widths:
        per = max(8, int(w * 1.15))
        n = max(n, sum(max(1, -(-len(part) // per)) for part in str(t or "").split("\n")))
    return min(most, line * n + 4)


def write_template(path, data=None, roles=None):
    """The input workbook, filled in with `data` (the example): a cover sheet, the input sheets, the references."""
    data = data or EXAMPLE
    stamp = time.strftime("%Y-%m-%d")
    sheets = []

    # cover
    rows, merges, links, hts = [], [], [], {}
    rows.append([("PLANT MODEL BUILDER  |  INPUT WORKBOOK", XS_TITLE), ("", XS_TITLE), ("", XS_TITLE)])
    rows.append([("PSS/E (.raw, .dyr) and PSCAD (.pscx) models of a plant from one workbook -- smib_build.py, "
                  + stamp, XS_SUB), ("", XS_SUB), ("", XS_SUB)])
    merges += ["A1:C1", "A2:C2"]
    hts.update({1: 34, 2: 20})

    def section(title):
        rows.append([])
        rows.append([(title, XS_SECTION), ("", XS_SECTION), ("", XS_SECTION)])
        merges.append("A%d:C%d" % (len(rows), len(rows)))
        hts[len(rows)] = 20

    section("HOW TO USE")
    for k, t in enumerate([
            "Fill in the yellow cells -- the example plant (100 MW PV + 100 MW BESS on one MPT) shows the format. "
            "A blank cell takes its default (Input_Reference lists every one).",
            "Plant: Model type -- SMIB: each generator one machine on its own equivalent feeder | DETAILED: every "
            "feeder section, the units at each node a machine of their own. PSS/E and PSCAD are built alike.",
            "Save, then run:   python smib_build.py <this workbook>.xlsx   (or keep the workbook next to the script "
            "and run the script on its own).",
            "Read <name>_REPORT.txt in the output folder: the equivalents, the load flow, the models and every "
            "CHECK line."]):
        rows.append([("Step %d" % (k + 1), XS_ITEM), (t, XS_CELL), ""])
        hts[len(rows)] = _rows_high([(t, 100)])
    section("SHEETS")
    for name, kind, what in SHEET_INDEX:
        rows.append([(name, XS_LINK), (what, XS_CELL), (kind, XS_TAG)])
        links.append(("A%d" % len(rows), name))
    section("COLOURS")
    for st, sample, what in ((XS_INPUT, "", "your inputs"), (XS_NOTE, "note", "what to enter, the default when "
                                                                              "blank"),
                             (XS_HEAD, "Column", "column titles"), (XS_SECTION, "SECTION", "a group of items"),
                             (XS_TAG, "PSS/E + PSLF + PSCAD", "which model an input goes into")):
        rows.append([(sample, st), (what, XS_CELL), ""])
    section("GUIDE")
    for topic, text in README:
        rows.append([(topic, XS_ITEM if topic else XS_CELL), (text, XS_CELL), ""])
        hts[len(rows)] = _rows_high([(text, 100)])
    sheets.append(("README", _xl_sheet(rows, [18, 100, 14], merges=merges, links=links, heights=hts, tab=_TAB_HOME,
                                       grid=False, fit=True)))

    # Plant: sections of items
    head = ["Item", "Value", "Unit", "Blank means", "Choices", "Description", "Used by"]
    widths = [26, 18, 7, 18, 22, 72, 13]
    rows, merges, lists, hts = [[(h, XS_HEAD) for h in head]], [], [], {1: 22}
    pl = data.get("plant", {})
    for key, item, unit, note, choices in PLANT_FIELDS:
        if key in PLANT_SECTIONS:
            rows.append([(PLANT_SECTIONS[key], XS_SECTION)] + [("", XS_SECTION)] * (len(head) - 1))
            merges.append("A%d:G%d" % (len(rows), len(rows)))
            hts[len(rows)] = 18
        blank = "required" if key == "poi_kv" else ("one of SCR, short-circuit MVA or R + X" if key == "grid_scr"
                                                    else _blank_means(note))
        rows.append([(item, XS_ITEM), (pl.get(key), XS_INPUT), (unit, XS_CELL),
                     (blank, XS_REQ if blank.startswith(("required", "one of")) else XS_CELL),
                     (" | ".join(choices) if choices else "", XS_CELL), (note, XS_NOTE),
                     (_used_by("Plant", key), XS_TAG)])
        hts[len(rows)] = _rows_high([(note, 72), (" | ".join(choices or []), 22), (blank, 18)])
        if choices:
            lists.append(("B%d" % len(rows), choices))
    sheets.append(("Plant", _xl_sheet(rows, widths, (1, 1), lists, merges=merges, heights=hts, tab=_TAB_IN, fit=True)))

    # the tables
    for sheet, key, cols in TABLES:
        widths = [max(c[4], 10) for c in cols]
        rows = [[(c[1], XS_HEAD) for c in cols], [(c[2] or "", XS_NOTE) for c in cols]]
        hts = {1: 32, 2: _rows_high([(c[2], w) for c, w in zip(cols, widths)], line=12.0, most=132.0)}
        recs = list(data.get(key, []))
        if key == "bus_numbers" and roles and not recs:
            recs = [{"bus": r, "number": None, "name": None} for r in roles]
        for rec in recs:
            rows.append([(rec.get(c[0]), XS_CODE if c[0] == "record" else XS_INPUT) for c in cols])
        while len(rows) < max(len(recs) + 2 + 12, 22):
            rows.append([(None, XS_CODE if c[0] == "record" else XS_INPUT) for c in cols])
        lists = [("%s3:%s500" % (_col_letters(i), _col_letters(i)), c[3]) for i, c in enumerate(cols) if c[3]]
        sheets.append((sheet, _xl_sheet(rows, widths, (1, 2), lists, heights=hts, tab=_TAB_IN, fit=len(cols) < 15)))

    # Input_Reference: every input
    head = ["Sheet", "Field", "Key", "Unit", "Blank means", "Choices", "Description", "Used by"]
    widths = [14, 26, 18, 7, 20, 24, 80, 13]
    rows = [[("INPUT REFERENCE  |  every input of every sheet", XS_TITLE)] + [("", XS_TITLE)] * 7,
            [("What each input is, its unit, what a blank cell means, the choices and which model it goes into. "
              "Bus names and the PSCAD placeholders are at the end.", XS_SUB)] + [("", XS_SUB)] * 7,
            [(h, XS_HEAD) for h in head]]
    merges, links, hts = ["A1:H1", "A2:H2"], [], {1: 30, 2: 18, 3: 20}

    def ref_section(title, sheet=None):
        rows.append([(title, XS_SECTION)] + [("", XS_SECTION)] * 7)
        merges.append("A%d:H%d" % (len(rows), len(rows)))
        hts[len(rows)] = 18
        if sheet:
            links.append(("A%d" % len(rows), sheet))

    ref_section("Plant", "Plant")
    for key, item, unit, note, choices in PLANT_FIELDS:
        rows.append([("Plant", XS_CELL), (item, XS_ITEM), (key, XS_CELL), (unit, XS_CELL),
                     ("required" if key == "poi_kv" else _blank_means(note), XS_CELL),
                     (" | ".join(choices) if choices else "", XS_CELL), (note, XS_CELL),
                     (_used_by("Plant", key), XS_TAG)])
        hts[len(rows)] = _rows_high([(note, 80), (" | ".join(choices or []), 24), (_blank_means(note), 20)])
    for sheet, key, cols in TABLES:
        ref_section(sheet, sheet)
        for c in cols:
            note = (c[2] or "").lstrip("# ").strip()
            rows.append([(sheet, XS_CELL), (c[1], XS_ITEM), (c[0], XS_CELL), ("", XS_CELL),
                         (_blank_means(note), XS_CELL), (" | ".join(c[3]) if c[3] else "", XS_CELL), (note, XS_CELL),
                         (_used_by(sheet, c[0]), XS_TAG)])
            hts[len(rows)] = _rows_high([(note, 80), (" | ".join(c[3] or []), 24), (_blank_means(note), 20)])
    ref_section("BUS NAMES -- Connect to, At, Bus_Numbers and {<bus>} in Dyr_Records take these")
    for nm, what in (("IB", "the infinite bus"), ("POI", "the point of interconnection"),
                     ("HV", "the gen-tie's plant end (the POI when there is no gen-tie)"),
                     ("<MPT> or <MPT>:LV", "an MPT's LV winding"), ("<MPT>:TV", "a 3-winding MPT's tertiary"),
                     ("<MPT>:HV", "an MPT with a tie line of its own: its HV end"),
                     ("<collector>", "a shared collector's far end"),
                     ("<generator>:MV", "a generator's GSU HV side"),
                     ("<generator> or <generator>:LV", "a generator's terminal"),
                     ("<collector>_<feeder>_S<n>", "DETAILED: the far end of feeder section n"),
                     ("<generator>_<feeder>_S<n>:LV", "DETAILED: the terminal of the units at that node")):
        rows.append([("", XS_CELL), (nm, XS_ITEM), ("", XS_CELL), ("", XS_CELL), ("", XS_CELL), ("", XS_CELL),
                     (what, XS_CELL), ("", XS_CELL)])
    ref_section("PSCAD_Params PLACEHOLDERS -- filled in for each inverter a vendor model stands for", "PSCAD_Params")
    for k, what in PSCAD_KEYS_DOC:
        rows.append([("PSCAD_Params", XS_CELL), (k, XS_ITEM), ("", XS_CELL), ("", XS_CELL), ("", XS_CELL),
                     ("", XS_CELL), (what, XS_CELL), ("PSCAD", XS_TAG)])
    sheets.append(("Input_Reference", _xl_sheet(rows, widths, (2, 3), merges=merges, links=links, heights=hts,
                                                tab=_TAB_REF, fit=True)))

    # Dynamic_Models: every parameter of every model
    head = ["Model", "Position", "Parameter", "Default", "Set by the script", "Description"]
    rows = [[("DYNAMIC MODELS  |  every parameter, its default and meaning", XS_TITLE)] + [("", XS_TITLE)] * 5,
            [("To change one: a row in Dyr_Params with the Generator (IB: the infinite bus, PLANT: the plant "
              "controller), the Model, the Parameter (or its position) and the Value. 'auto' values come from the "
              "plant data (limits on MBASE, buses, shares).", XS_SUB)] + [("", XS_SUB)] * 5,
            [(h, XS_HEAD) for h in head]]
    merges, hts = ["A1:F1", "A2:F2"], {1: 30, 2: 30, 3: 20}
    for model, (hdr, nic, names) in DYR_LIB.items():
        rows.append([("%s -- %s" % (model, MODEL_DOC.get(model, "")), XS_SECTION)] + [("", XS_SECTION)] * 5)
        merges.append("A%d:F%d" % (len(rows), len(rows)))
        hts[len(rows)] = 18
        for k, nm in enumerate(names):
            pos = ("ICON%d" % (k + 1)) if k < nic else ("CON%d" % (k - nic + 1))
            rows.append([(model, XS_CELL), (pos, XS_CELL), (nm, XS_ITEM), (DYN_DEFAULTS[model].get(nm), XS_CELL),
                         ("auto" if nm in AUTO_PARAMS.get(model, ()) else "", XS_TAG if nm in
                          AUTO_PARAMS.get(model, ()) else XS_CELL), (_param_doc(nm, model), XS_CELL)])
    sheets.append(("Dynamic_Models", _xl_sheet(rows, [11, 9, 13, 11, 14, 78], (0, 3), merges=merges, heights=hts,
                                               tab=_TAB_REF, fit=True)))
    write_xlsx(path, sheets)
    return path


# ============================================================================
# THE NETWORK -- buses by role; lines, transformers, machines, loads, shunts
# ============================================================================
class Net(object):
    def __init__(self, name, sb, f):
        self.name, self.sb, self.f = name, sb, f
        self.bus = collections.OrderedDict()     # role -> {"role", "kv", "name", "num", "vm", "va", "ide"}
        self.alias = {}                          # REFERENCE (upper case) -> role
        self.lines, self.tx2, self.tx3 = [], [], []
        self.gens, self.loads, self.shunts = [], [], []
        self.colls = collections.OrderedDict()   # collector name -> WECC result
        self.thev = {}
        self.ctl = {}
        self.op = {}                             # the operating point
        self.plant_ctrl = None                   # REPC_B: {model, gens, mbase} (the .dyr)
        self.model = "SMIB"                      # SMIB: one feeder per generator | DETAILED: every feeder section
        self.moved = {}                          # DETAILED: bus names that now stand for the collector's root
        self.merged = []                         # SMIB: a generator's collector systems made one feeder
        self.pscad_slots = []                    # PSCAD: the inverters' slots
        self.pscad_groups = []                   # PSCAD: the runs of R, L, C (single-phase R, L, C: drawn three times)
        self.notes = []

    def add_bus(self, role, kv, where, aliases=()):
        for a in (role,) + tuple(aliases):
            if a.upper() in self.alias:
                raise InputError("%s: the bus %s is defined twice (names must be unique)" % (where, a))
        self.bus[role] = {"role": role, "kv": float(kv), "name": None, "num": 0, "vm": 1.0, "va": 0.0, "ide": 1}
        for a in (role,) + tuple(aliases):
            self.alias[a.upper()] = role
        return role

    def add_alias(self, alias, role, where):
        role = self.alias.get(role.upper(), role)
        old = self.alias.get(alias.upper())
        if old is not None and old != role:
            raise InputError("%s: the name %s is used twice" % (where, alias))
        self.alias[alias.upper()] = role

    def find(self, ref, where):
        r = self.alias.get(str(ref or "").strip().upper())
        if r is None and not str(ref or "").strip():
            raise InputError("%s: no bus given -- buses: %s" % (where, ", ".join(sorted(self.alias))))
        if r is None:
            raise InputError("%s: no bus %r -- buses: %s%s" % (where, ref, ", ".join(sorted(self.alias)), (
                " (each generator has a collector of its own now: <generator>:MV is its far end)"
                if str(ref).strip().upper().endswith(":COLL") else "")))
        if str(ref).strip().upper() in self.moved and not (where or "").startswith("Loads_Shunts"):
            raise InputError("%s: %s is no bus of its own in the DETAILED model (each unit group has its own GSU "
                             "and terminal) -- give the row the generator's name to have each unit group's own"
                             % (where, ref))
        return r

    def kv(self, role):
        return self.bus[self.alias.get(role.upper(), role)]["kv"]


# typical overhead lines, one circuit, positive sequence, at 60 Hz -- what blank gen-tie values take:
# (kV class, R ohm/km at 75 C, X ohm/km, B uS/km, rating MVA, conductor)
OHL_TYPICAL = [
    (34.5, 0.14, 0.42, 3.9, 35.0, "477 kcmil ACSR"),
    (69.0, 0.21, 0.48, 3.4, 70.0, "336 kcmil ACSR"),
    (115.0, 0.14, 0.48, 3.5, 130.0, "477 kcmil ACSR"),
    (138.0, 0.085, 0.49, 3.4, 215.0, "795 kcmil ACSR"),
    (161.0, 0.085, 0.50, 3.3, 250.0, "795 kcmil ACSR"),
    (230.0, 0.072, 0.495, 3.35, 400.0, "795 kcmil ACSR"),
    (345.0, 0.034, 0.37, 4.5, 1200.0, "2 x 954 kcmil ACSR"),
    (500.0, 0.020, 0.33, 5.0, 2500.0, "3 x 954 kcmil ACSR"),
    (765.0, 0.011, 0.27, 6.2, 4000.0, "4 x 1351 kcmil ACSR"),
]


def ohl_typical(kv, f):
    """The typical overhead line nearest kv -> {kv, r, x, b (per km, at f), rate, cond}."""
    t = min(OHL_TYPICAL, key=lambda t: abs(math.log(kv / t[0])))
    return {"kv": t[0], "r": t[1], "x": t[2] * f / 60.0, "b": t[3] * f / 60.0, "rate": t[4], "cond": t[5]}


def line_data(d, prefix, kv, sb, f, where, unused=None):
    """A line from <prefix>length / _unit / _r_per ... (or _r_ohm ... totals): None when none is given. Each of
       R, X, B is its total where given, else its value per length times the length: per <prefix>per_unit (blank =
       km or mi, whichever brings them nearer a typical line of its kV); with no totals given, blank ones take a
       typical overhead line of its kV (OHL_TYPICAL), and ones far from it are CHECKed (a unit mixed up). A value
       given twice (a total and per length, B and C) that disagrees is CHECKed; unused (a list): a NOTE there of
       the values given for a line that is not there (no length, no totals) -- none is dropped unsaid.
       r, x, b in pu on sb at kv for one circuit; R, X (ohm), B (uS) one circuit; count circuits; notes."""
    g = lambda k: d.get(prefix + k)
    length = _num(d, prefix + "length", where)
    totals = any(g(k) not in (None, "") for k in ("r_ohm", "x_ohm"))
    if not totals and not length:
        if unused is not None:
            _unused(unused, d, [prefix + k for k in ("unit", "per_unit", "r_per", "x_per", "b_per", "c_per", "b_us",
                                                     "parallel", "rate_mva")], where,
                    "no length (and no R, X totals): no line here", {prefix + "unit": FEEDER_UNIT,
                                                                       prefix + "parallel": 1})
        return None
    unit = _choice(d, prefix + "unit", where, LEN_UNITS, FEEDER_UNIT)
    per_unit = _choice(d, prefix + "per_unit", where, LEN_UNITS, None)
    km = (length or 0.0) * LEN_KM[unit]
    typ, notes, used = ohl_typical(kv, f), [], []
    per = dict((k, _num(d, prefix + k, where)) for k in ("r_per", "x_per", "b_per", "c_per"))
    tot = dict((k, _num(d, prefix + k, where)) for k in ("r_ohm", "x_ohm", "b_us"))
    inferred = per_unit is None
    if inferred:                                      # per km or per mile: whichever is nearer a line of its kV
        given = [(v, typ[k]) for v, k in ((per["r_per"], "r"), (per["x_per"], "x"), (per["b_per"], "b")) if v]
        per_unit = min(("km", "mi"), key=lambda u: sum(abs(math.log(abs(v) / LEN_KM[u] / t)) for v, t in given))
    b_per = per["b_per"]
    if per["c_per"] is not None:                      # C (uF) instead of B (uS)
        b_c = 2.0 * math.pi * f * per["c_per"]
        if b_per is None:
            b_per = b_c
        elif abs(b_c - b_per) > 0.01 * max(abs(b_per), 1e-9):
            notes.append("CHECK: %s: B per length %g uS and C per length %g uF (= %.4g uS) disagree -- B is used"
                         % (where, b_per, per["c_per"], b_c))
    per_len, zero, unsaid, given_by = [], [], [], {}

    def one(t, p, typical, what, unit_txt, tkey=None, pkey=None):
        """One of R, X, B for one circuit: the total given, else per length x the length, else typical (or 0)."""
        if p is not None and km <= 0:
            unsaid.append(what)
            p = None
        given_by[what] = prefix + (tkey if t is not None else pkey if p is not None else "")
        if t is not None:
            if p is not None and abs(p / LEN_KM[per_unit] * km - t) > 0.01 * max(abs(t), 1e-9):
                notes.append("CHECK: %s: %s given as a total, %g %s, and per length, %g %s/%s x %.4g %s = %.4g %s "
                             "-- they disagree: the total is used" % (
                                 where, what, t, unit_txt, p, unit_txt, per_unit, length, unit,
                                 p / LEN_KM[per_unit] * km, unit_txt))
            return t
        if p is not None:
            per_len.append(what)
            return p / LEN_KM[per_unit] * km          # per <per_unit> -> per km, times the length
        if totals:                                    # totals given: what is not given is none
            zero.append(what)
            return 0.0
        used.append(what)
        return typical * km
    R = one(tot["r_ohm"], per["r_per"], typ["r"], "R", "ohm", "r_ohm", "r_per")
    X = one(tot["x_ohm"], per["x_per"], typ["x"], "X", "ohm", "x_ohm", "x_per")
    B = one(tot["b_us"], b_per, typ["b"], "B", "uS", "b_us", "b_per" if per["b_per"] is not None else "c_per")
    if inferred and per_len:
        notes.append("NOTE: %s: values per blank -- %s taken per %s (nearer a typical %g kV line than per %s); set "
                     "its 'values per' if they are per another length" % (
                         where, ", ".join(per_len), per_unit, typ["kv"], "mi" if per_unit == "km" else "km"))
    if unsaid:
        notes.append("CHECK: %s: %s per length not used -- no length is given (the totals are used)" % (
            where, ", ".join(unsaid)))
    if zero:
        notes.append("NOTE: %s: %s not given with the totals -- 0" % (where, ", ".join(zero)))
    if used:
        notes.append("NOTE: %s: %s per length not given -- a typical %g kV overhead line (%s, one circuit): R %g, "
                     "X %g ohm/km, B %g uS/km" % (where, ", ".join(used), typ["kv"], typ["cond"], typ["r"],
                                                  round(typ["x"], 4), round(typ["b"], 4)))
    if abs(complex(R, X)) < 1e-9:
        raise InputError("%s: the line has no impedance -- %s and %s (or %s and %s) are 0" % (
            where, _label(prefix + "r_per", where), _label(prefix + "x_per", where), _label(prefix + "r_ohm", where),
            _label(prefix + "x_ohm", where)))
    if km > 0:                                        # far from a line of its kV: a unit mixed up?
        for what, v, t in (("R", R / km, typ["r"]), ("X", X / km, typ["x"]), ("B", B / km, typ["b"])):
            if v > 0 and not 0.2 <= v / t <= 5.0:
                msg = "%s: %s %.4g %s/km%s is %s a typical %g kV line's %.4g -- %s" % (
                    where, what, v, "uS" if what == "B" else "ohm", (" (from %s)" % _label(given_by[what], where))
                    if given_by.get(what, prefix) != prefix else "", "%.0f times" % (v / t) if v > t else
                    "1/%.0f of" % (t / v), typ["kv"], t, "the R, X, B are read per %s: set 'values per' to the "
                    "length they are per (km, mi ...)" % per_unit if not totals else "check the totals and the length")
                if not 0.02 <= v / t <= 50.0:
                    raise InputError(msg)
                notes.append("CHECK: " + msg)
    rate = _num(d, prefix + "rate_mva", where, None)
    if rate is None:
        rate = typ["rate"]
        notes.append("NOTE: %s: rating not given -- %g MVA, a typical %g kV line's" % (where, rate, typ["kv"]))
    zb = kv * kv / sb
    return {"r": R / zb, "x": X / zb, "b": B * 1e-6 * zb, "R": R, "X": X, "B": B, "km": km,
            "count": _int(d, prefix + "parallel", where, 1, minimum=1), "rate": rate, "kv": kv, "notes": notes}


def tx_rx(z_pct, x_r, load_loss_kw, mva, where, what):
    """(r, x) pu on the transformer's own base from Z % and X/R, or Z % and the load loss."""
    if z_pct is None or not z_pct > 0:
        raise InputError("%s: %s impedance (Z %%) is missing" % (where, what))
    z = z_pct / 100.0
    if load_loss_kw is not None and load_loss_kw < 0:
        raise InputError("%s: %s load loss %g kW -- 0 or more" % (where, what, load_loss_kw))
    if load_loss_kw:
        r = load_loss_kw / 1000.0 / mva
        if r >= z:
            raise InputError("%s: %s load loss %.1f kW is more than Z allows" % (where, what, load_loss_kw))
        return r, math.sqrt(z * z - r * r)
    if x_r is None:
        raise InputError("%s: %s X/R (or the load loss) is missing" % (where, what))
    if x_r <= 0:
        return 0.0, z
    r = z / math.sqrt(1.0 + x_r * x_r)
    return r, r * x_r


_VG = re.compile(r"^(YN|Y|D|ZN|Z)((?:(?:yn|y|d|zn|z)\d{1,2}){1,2})$")


def vector_group(v, nw, where):
    """{"text", "w": ["Y"|"D", ...], "shift": [0, s2(, s3)]}: winding k's angle against winding 1 in degrees
       (the low-voltage winding lags by 30 x clock)."""
    s = (v or "").strip().replace(" ", "")
    if not s:
        s = "YNd1" if nw == 2 else "YNd1d1"
    m = _VG.match(s)
    if not m:
        raise InputError("%s: vector group %r is not understood (YNd1, Dyn11, YNyn0, YNd1d1 ...)" % (where, v))
    w = ["D" if m.group(1).startswith("D") else "Y"]
    shift = [0.0]
    for t, clock in re.findall(r"(yn|y|d|zn|z)(\d{1,2})", m.group(2)):
        ck = int(clock)
        if ck > 11:
            raise InputError("%s: vector group %r: clock %d" % (where, v, ck))
        w.append("D" if t == "d" else "Y")
        a = -30.0 * ck
        while a <= -180.0:
            a += 360.0
        shift.append(a)
    if len(w) != nw:
        raise InputError("%s: vector group %r has %d windings, the transformer %d" % (where, v, len(w), nw))
    for k in range(1, nw):
        if (w[0] == w[k] and shift[k] != 0.0) or (w[0] != w[k] and abs(shift[k]) != 30.0):
            raise InputError("%s: vector group %r -- the PSCAD classical transformer takes 0 deg (Yy, Dd) or "
                             "+-30 deg (Yd, Dy) only" % (where, v))
    return {"text": s, "w": w, "shift": shift}


def _mag(nll_pct, im_pct, mva, sb, kv_rated, kv_bus):
    """PSS/E MAG1, MAG2 (CM = 1: pu on the system base): no-load loss and magnetising current."""
    g = (nll_pct or 0.0) / 100.0
    y = (im_pct or 0.0) / 100.0
    b = math.sqrt(max(0.0, y * y - g * g))
    k = (mva / sb) * (kv_bus / kv_rated) ** 2
    return g * k, -b * k


KV_OFF = 0.15             # a winding this far off its bus voltage (or a GSU off its collector): an input error


def add_tx2(net, d):
    """A two-winding transformer: d = {name, kind, hv, lv, mva, z_pct, x_r, load_loss_kw, kv1, kv2, tap, vector,
       nll_pct, im_pct, rate, count, where}."""
    w = d["where"]
    r, x = tx_rx(d["z_pct"], d.get("x_r"), d.get("load_loss_kw"), d["mva"], w, d["kind"])
    kvb1, kvb2 = net.kv(d["hv"]), net.kv(d["lv"])
    k = (d["kv1"] / kvb1) ** 2                      # the impedance referred to winding 1 at its bus base (NOMV1)
    vg = vector_group(d.get("vector"), 2, w)
    t = dict(d)
    t.update({"r_own": r, "x_own": x, "r": r * k, "x": x * k, "windv1": d["kv1"] * d["tap"], "windv2": d["kv2"],
              "nomv1": kvb1, "nomv2": kvb2, "vg": vg,
              "rate": d["mva"] if d.get("rate") is None else d["rate"]})
    t["mag_g"], t["mag_b"] = _mag(d.get("nll_pct"), d.get("im_pct"), d["mva"], net.sb, d["kv1"], kvb1)
    for side, kvr, kvb in (("HV", d["kv1"], kvb1), ("LV", d["kv2"], kvb2)):
        if abs(kvr / kvb - 1.0) > KV_OFF:
            raise InputError("%s %s: %s winding %.4g kV on a %.4g kV bus -- the voltages do not match"
                             % (w, d["name"], side, kvr, kvb))
        if abs(kvr / kvb - 1.0) > 0.05:
            net.notes.append("CHECK: %s %s: %s winding %.4g kV on a %.4g kV bus -- an off-nominal ratio of %.3f"
                             % (w, d["name"], side, kvr, kvb, kvr / kvb))
    net.tx2.append(t)
    return t


def add_tx3(net, d):
    """A three-winding transformer: d = {name, hv, lv, tv, kv1..3, mva1..3, z12, z13, z23 (pct), base12, base13,
       base23, x_r, tap, vector, nll_pct, im_pct, count, where}."""
    w = d["where"]
    kvb = [net.kv(d["hv"]), net.kv(d["lv"]), net.kv(d["tv"])]
    kvr = [d["kv1"], d["kv2"], d["kv3"]]
    pairs = {}
    for p, (zk, bk, ref) in (("12", ("z12", "base12", 0)), ("23", ("z23", "base23", 1)), ("31", ("z13", "base13", 2))):
        r, x = tx_rx(d[zk], d.get("x_r"), None, d[bk], w, "%s Z %s" % (d["name"], {"12": "HV-LV", "23": "LV-TV",
                                                                                       "31": "HV-TV"}[p]))
        k = (kvr[ref] / kvb[ref]) ** 2
        pairs[p] = (r * k, x * k, d[bk], r, x)
    vg = vector_group(d.get("vector"), 3, w)
    t = dict(d)
    t.update({"r12": pairs["12"][0], "x12": pairs["12"][1], "s12": pairs["12"][2],
              "r23": pairs["23"][0], "x23": pairs["23"][1], "s23": pairs["23"][2],
              "r31": pairs["31"][0], "x31": pairs["31"][1], "s31": pairs["31"][2], "own": pairs,
              "windv": [kvr[0] * d["tap"], kvr[1], kvr[2]], "nomv": kvb, "vg": vg})
    t["mag_g"], t["mag_b"] = _mag(d.get("nll_pct"), d.get("im_pct"), d["mva1"], net.sb, kvr[0], kvb[0])
    for side, a, b in zip(("HV", "LV", "TV"), kvr, kvb):
        if abs(a / b - 1.0) > KV_OFF:
            raise InputError("%s %s: %s winding %.4g kV on a %.4g kV bus -- the voltages do not match"
                             % (w, d["name"], side, a, b))
        if abs(a / b - 1.0) > 0.05:
            net.notes.append("CHECK: %s %s: %s winding %.4g kV on a %.4g kV bus" % (w, d["name"], side, a, b))
    net.tx3.append(t)
    return t


def add_line(net, fr, to, ld, name, kind, where):
    t = dict(ld)
    t.update({"fr": fr, "to": to, "name": name, "kind": kind, "where": where})
    net.lines.append(t)
    return t


# ============================================================================
# WECC COLLECTOR EQUIVALENT
# ============================================================================
def read_cables(rows, log=None):
    out = {}
    for r in rows:
        w = r["_where"]
        name = _txt(r.get("cable"))
        if not name:
            raise InputError("%s: the cable has no name" % w)
        if name.upper() in out:
            raise InputError("%s: cable %s is already given in %s" % (w, name, out[name.upper()]["where"]))
        unit = _choice(r, "unit", w, LEN_UNITS, "km")
        b = _num(r, "b_per", w)
        c = _num(r, "c_per", w)
        if b is not None and c is not None and log:
            log("NOTE: %s: cable %s has both B and C -- B used" % (w, name))
        notes = []
        rates = _abc(r, ("rate_a_mva", "rate_b_mva", "rate_c_mva"), "%s (cable %s)" % (w, name), 0.0, notes)
        for m in notes if log else ():
            log(m)
        out[name.upper()] = {"name": name, "unit": unit, "r": _num(r, "r_per", w, required=True),
                             "x": _num(r, "x_per", w, required=True), "b": b, "c": c, "where": w, "rates": rates}
    return out


_OEM_READ = {}


def oem_model(path):
    """An OEM's PSCAD model (.pscx case or .pslx library) read as XML, without PSCAD -> (project name,
       [{name, desc, ports [(name, dim, kind)], params [(name, desc)], ids [component IDs placed]}]) -- read once
       while the file stays as it is."""
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime, st.st_size)
    except OSError:
        key = None
    if key in _OEM_READ:
        return _OEM_READ[key]
    got = _oem_model(path)
    if key:
        if len(_OEM_READ) > 8:
            _OEM_READ.clear()
        _OEM_READ[key] = got
    return got


def udm_ports_check(g, notes):
    """A generator's vendor model (UDM): its AC port against its definition's own ports, read from the OEM's
       file -- a port it has not, one not 3-phase, two 3-phase ones and none named: CHECKed (the PSCAD build would
       wire the wrong one, or none)."""
    ps, w = g["pscad"], g["where"]
    src = ps["source"] or ps["library"]
    if not src or not os.path.isfile(src) or not (ps["definition"] or ps["iid"]):
        return
    try:
        _scope, defs = oem_model(src)
    except Exception:
        return                                       # (a file PSCAD may still read: said when its form is read)
    want = (ps["definition"] or "").split(":")[-1].upper()
    d = next((x for x in defs if want and x["name"].upper() == want), None) or \
        next((x for x in defs if ps["iid"] and str(ps["iid"]) in x["ids"]), None)
    if d is None:
        if want:
            notes.append("CHECK: %s: %s %s is no definition of %s -- its definitions: %s" % (
                w, _label("pscad_definition"), ps["definition"], os.path.basename(src),
                ", ".join(x["name"] for x in defs[:20]) + (" ..." if len(defs) > 20 else "")))
        return
    el = [(n, dim) for n, dim, kind in d["ports"] if kind == "electrical"]
    say = ", ".join("%s (%d-phase)" % (n, dim) for n, dim in el) or "none"
    three = [n for n, dim in el if dim == 3]
    if ps["ac_port"]:
        hit = [(n, dim) for n, dim in el if n.upper() == ps["ac_port"].upper()]
        if not hit:
            notes.append("CHECK: %s: %s %s is no port of %s -- its electrical ports: %s" % (
                w, _label("pscad_ac_port"), ps["ac_port"], d["name"], say))
        elif hit[0][1] != 3:
            notes.append("CHECK: %s: %s %s of %s is %d-phase, the plant's node 3-phase -- its electrical ports: %s"
                         % (w, _label("pscad_ac_port"), ps["ac_port"], d["name"], hit[0][1], say))
    elif not three:
        notes.append("CHECK: %s: %s has no 3-phase port -- its electrical ports: %s; give %s" % (
            w, d["name"], say, _label("pscad_ac_port")))
    elif len(three) > 1:
        notes.append("CHECK: %s: %s has %d 3-phase ports (%s) -- %s is taken as its AC port: give %s if it is "
                     "another" % (w, d["name"], len(three), ", ".join(three), three[0], _label("pscad_ac_port")))


def _oem_model(path):
    import xml.etree.ElementTree as ET
    root = ET.parse(path).getroot()
    scope = root.get("name") or os.path.splitext(os.path.basename(path))[0]
    defs, placed = collections.OrderedDict(), collections.defaultdict(list)
    for el in root.iter():
        d = el.get("defn") or ""
        if ":" in d and el.get("id"):
            placed[d.split(":", 1)[1].upper()].append(el.get("id"))
        if el.tag.lower() != "definition" or not el.get("name") or el.get("name") in defs:
            continue
        desc = el.get("desc") or el.get("description") or ""
        for prm in el.iter():
            if prm.tag.lower() == "param" and (prm.get("name") or "").lower() == "description" and not desc:
                desc = prm.get("value") or ""
        ports, params = [], []
        for x in el.iter():
            t = x.tag.lower()
            prm = dict(((q.get("name") or "").lower(), q.get("value") or "") for q in x.iter("param"))
            if t == "port" and (x.get("name") or prm.get("name")):
                mode = (prm.get("mode") or x.get("mode") or "").upper()
                kind = "in" if mode in ("INPUT", "1") else "out" if mode in ("OUTPUT", "2") else "electrical"
                try:
                    dim = int(x.get("dim") or prm.get("dim") or 1)
                except ValueError:
                    dim = 1
                if (x.get("name") or prm["name"]) not in [q[0] for q in ports]:   # PSCAD 5: in svg and graphics
                    ports.append((x.get("name") or prm["name"], dim, kind))
            elif t == "parameter" and x.get("name"):
                params.append((x.get("name"), x.get("desc") or ""))
        defs[el.get("name")] = {"name": el.get("name"), "desc": desc, "ports": ports, "params": params}
    for d in defs.values():
        d["ids"] = placed.get(d["name"].upper(), [])
    return scope, list(defs.values())


def oem_symbol(path, name=None, iid=None):
    """The symbol of definition `name` -- or of the component with ID iid -- in an OEM's PSCAD file (.pscx /
       .pslx), from its XML: {"w", "h": its extent, "x0", "x1": its left and right, "ports": {name: (x, y)}
       electrical, "texts": [(x, y, width)] centred} in grid units from its origin, or None."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    if not name and iid:
        name = next((el.get("defn").split(":", 1)[1] for el in root.iter()
                     if el.get("id") == str(iid) and ":" in (el.get("defn") or "")), None)
    el = next((d for d in root.iter() if d.tag.lower() == "definition" and
               (d.get("name") or "").upper() == (name or "").upper()), None) if name else None
    g = el.find(".//graphics") if el is not None else None
    xs, ys, ports, texts = [], [], {}, []
    for x in (g.iter() if g is not None else []):
        t, prm = x.tag.lower(), dict(((q.get("name") or "").lower(), q.get("value") or "") for q in x.iter("param"))
        try:
            gx, gy = float(x.get("x") or 0) / 18.0, float(x.get("y") or 0) / 18.0
        except ValueError:
            continue
        if t == "port":
            if (prm.get("mode") or x.get("mode") or "ELECTRICAL").upper() in ("ELECTRICAL", "3"):
                ports[x.get("name") or prm.get("name") or "?"] = (gx, gy)
            pts = [(gx, gy)]
        elif t != "gfx":
            continue
        elif "text" in (x.get("classid") or "").lower():
            w = len(x.get("text") or prm.get("text") or "") * NAME_CH / 2.0
            pts = [(gx - w, gy), (gx + w, gy)]
            texts.append((gx, gy, 2.0 * w))
        else:
            pts = [(gx + float(v.get("x") or 0) / 18.0, gy + float(v.get("y") or 0) / 18.0) for v in x.iter("vertex")]
            if x.get("w") is not None:
                pts.append((gx + float(x.get("w")) / 18.0, gy + float(x.get("h") or 0) / 18.0))
            pts.append((gx, gy))
        xs += [a for a, _b in pts]
        ys += [b for _a, b in pts]
    if not xs:
        return None
    return {"w": max(xs) - min(xs), "h": max(ys) - min(ys), "x0": min(xs), "x1": max(xs), "ports": ports,
            "texts": texts}


def port_pad(sym, port=None):
    """How far the text beside a vendor symbol's AC port (its name, as a rule) reaches past the port, away from
       its box (grid units, whole): room its wire leaves for it."""
    ps = sym.get("ports") or {}
    p = (ps.get(port) or next(iter(ps.values()))) if ps else None
    if p is None:
        return 0
    out = 1.0 if p[0] > (sym["x0"] + sym["x1"]) / 2.0 else -1.0
    far = [(tx - p[0]) * out + tw / 2.0 for tx, ty, tw in sym.get("texts", []) if abs(ty - p[1]) <= 1.5 and
           (tx - p[0]) * out > -tw / 2.0]
    return int(math.ceil(max(far))) if far else 0


def udm_form(path, name=None, iid=None, library=False):
    """The parameters of definition `name` -- or of the component with ID iid, with the values it has there -- in
       an OEM's PSCAD file (.pscx / .pslx), from its XML -> {"defn": its name, "params": [{name, desc, type, unit,
       min, max, default, choices, cat, help}], "placed": {name: value} of the component iid, "parts": the parts
       inside it with settings of their own (udm_parts)}, or None."""
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return None
    placed = {}
    if iid:
        el = next((x for x in root.iter() if x.get("id") == str(iid) and ":" in (x.get("defn") or "")), None)
        if el is None:
            return None
        name = name or el.get("defn").split(":", 1)[1]
        for q in el.iter("param"):
            if q.get("name") and q.get("name") not in placed:
                placed[q.get("name")] = q.get("value") or ""
    name = (name or "").split(":")[-1]
    defs = dict(((d.get("name") or "").upper(), d) for d in root.iter() if d.tag.lower() == "definition" and
                d.get("name"))
    el = defs.get(name.upper()) if name else None
    out = _form_params(el)
    if out is None:
        return None
    scope = root.get("name") or os.path.splitext(os.path.basename(path))[0]
    return {"defn": el.get("name"), "params": out, "placed": placed,
            "parts": udm_parts(defs, scope, el.get("name"), library),
            "signals": udm_signals(defs, scope, el.get("name"))}


def _form_params(el):
    """A definition's form: its parameters [{name, desc, type, unit, min, max, default, choices, cat, help}], or
       None where it has no form."""
    form = el.find(".//form") if el is not None else None
    if form is None:
        return None
    out = []
    for cat in form.iter("category"):
        for x in cat.iter("parameter"):
            if not x.get("name") or any(q["name"] == x.get("name") for q in out):
                continue
            val = x.find("value")
            hlp = x.find("help")
            out.append({"name": x.get("name"), "desc": x.get("desc") or "", "type": x.get("type") or "",
                        "unit": x.get("unit") or "", "min": x.get("min"), "max": x.get("max"),
                        "default": (val.text or "").strip() if val is not None and val.text else "",
                        "choices": [(c.text or "").strip() for c in x.iter("choice")], "cat": cat.get("name") or "",
                        "help": (hlp.text or "").strip() if hlp is not None and hlp.text else ""})
    return out


def part_steps(el):
    """The parts placed in a definition's schematic, each with the name it goes by in a path: its definition's
       name -- name#k, k counting from 1, where it has more than one of them -> [(name, element)]."""
    sch = el.find("schematic") if el is not None else None
    users = list(sch.iter("User")) if sch is not None else []
    short = [(u.get("defn") or "").split(":")[-1] for u in users]
    many = collections.Counter(x.upper() for x in short)
    seen, out = collections.Counter(), []
    for u, sh in zip(users, short):
        seen[sh.upper()] += 1
        out.append((sh if many[sh.upper()] == 1 else "%s#%d" % (sh, seen[sh.upper()]), u))
    return out


def udm_signals(defs, scope, name, prefix="", depth=0):
    """The signals set inside definition `name` by a value of their own -- a constant, a slider or a switch wired
       to the signal's data label -- at any depth: [{signal, path (var_switch#2 ...; inside a part:
       Controller4/const#1), defn, value, min, max, label}] -- the value is the part's Value."""
    out, el = [], defs.get((name or "").upper())
    sch = el.find("schematic") if el is not None else None
    if sch is None or depth > 8:
        return out
    up = {}

    def top(a):
        up.setdefault(a, a)
        while up[a] != a:
            up[a] = up[up[a]]
            a = up[a]
        return a
    for w in sch.iter("Wire"):
        try:
            x0, y0 = int(w.get("x")), int(w.get("y"))
            vs = [(x0 + int(v.get("x")), y0 + int(v.get("y"))) for v in w.iter("vertex")]
        except (TypeError, ValueError):
            continue
        for a, b2 in zip(vs, vs[1:]):
            ra, rb = top(a), top(b2)
            if ra != rb:
                up[ra] = rb
    names = collections.defaultdict(list)
    for u in sch.iter("User"):
        if (u.get("defn") or "").lower() == "master:datalabel":
            nm = next((q.get("value") for q in u.iter("param") if q.get("name") == "Name"), None)
            try:
                if nm:
                    names[top((int(u.get("x")), int(u.get("y"))))].append(nm)
            except (TypeError, ValueError):
                pass
    for step, u in part_steps(el):
        d = (u.get("defn") or "").split(":", 1)
        prm = collections.OrderedDict()
        for q in u.iter("param"):
            if q.get("name") and q.get("name") not in prm:
                prm[q.get("name")] = q.get("value") or ""
        if len(d) == 2 and d[0].upper() == scope.upper() and d[1].upper() in defs:
            out += udm_signals(defs, scope, d[1], prefix + step + "/", depth + 1)
            continue
        if d[0].lower() != "master" or "Value" not in prm:
            continue
        try:
            x, y, o = int(u.get("x")), int(u.get("y")), int(u.get("orient") or 0)
        except (TypeError, ValueError):
            continue
        dx = -36 if o & 4 else 36                    # its output, two grid units out (as drawn: rotated, mirrored)
        tips = [(dx, 0)] if o % 4 == 0 else [(-dx, 0)] if o % 4 == 2 else [(0, 36), (0, -36)]
        got = []
        for tx, ty in tips:
            got += [n for n in names.get(top((x + tx, y + ty)), []) if n not in got]
        for sig in got:
            out.append({"signal": sig, "path": prefix + step, "defn": u.get("defn"), "value": prm["Value"],
                        "min": prm.get("Min"), "max": prm.get("Max"), "label": prm.get("Name") or ""})
    return out


def udm_parts(defs, scope, name, library=False, prefix="", depth=0):
    """The parts inside definition `name` with settings of their own, at any depth: [{path (Controller4; a part
       inside it Controller4/PWM#2), defn, params}] -- each made from one of the file's own definitions, with its
       form and the values that part has; with library, the master library's parts too, their values as they
       stand. defs: {NAME: definition element}."""
    out = []
    if depth > 8:
        return out
    for step, u in part_steps(defs.get((name or "").upper())):
        vals = collections.OrderedDict()
        for q in u.iter("param"):
            if q.get("name") and q.get("name") not in vals:
                vals[q.get("name")] = q.get("value") or ""
        d = (u.get("defn") or "").split(":", 1)
        mine = len(d) == 2 and d[0].upper() == scope.upper() and d[1].upper() in defs
        if mine:
            form = _form_params(defs[d[1].upper()]) or []
            if form:
                out.append({"path": prefix + step, "defn": d[1],
                            "params": [dict(q, default=vals.get(q["name"], q["default"])) for q in form]})
            out += udm_parts(defs, scope, d[1], library, prefix + step + "/", depth + 1)
        elif library and vals:
            out.append({"path": prefix + step, "defn": u.get("defn") or "", "params": [
                {"name": k, "desc": "", "type": "", "unit": "", "min": None, "max": None, "default": v, "choices": [],
                 "cat": "", "help": ""} for k, v in vals.items()]})
    return out


def udm_choice(p, v):
    """A choice parameter's value as its form shows it ("0 = 250 kW / 250 kVA") -> v itself where it is none."""
    code = str(v).split("=")[0].strip()
    return next((c for c in p["choices"] if c.split("=")[0].strip() == code), str(v))


def udm_doc(p):
    """A vendor model's parameter as its form has it: its type, unit, range or choices, its page and help."""
    def real(v):
        try:
            return abs(float(v)) < 1e9                # not one of the forms' "no limit" (1E+308, 2147483647)
        except (TypeError, ValueError):
            return False
    rng = ("%s .. %s" % (p["min"], p["max"])) if real(p["min"]) and real(p["max"]) and p["min"] != p["max"] else ""
    what = " | ".join(p["choices"]) if p["choices"] else " ".join(x for x in (p["type"], p["unit"], rng) if x)
    return "%s%s%s" % (what, ("   [%s]" % p["cat"]) if p["cat"] else "", ("   " + p["help"]) if p["help"] else "")


def udm_check(p, text, where, part=False):
    """A value typed for a vendor model's parameter p -> the value kept (a choice's code: 0, not 0 = 250 kW); an
       InputError where it cannot be: a choice it does not have, a number outside its range, a {KEY} that is
       not one -- or any {KEY} for a part inside it (part: its settings are in the definition, one for all the
       generator's inverters)."""
    text = str(text).strip()
    if part and "{" in text:
        raise InputError("%s: a part inside the vendor model takes a value, not %s -- its settings are in the "
                         "definition, one for all of the generator's inverters" % (where, text))
    if p["type"].upper() == "CHOICE":
        code = text.split("=")[0].strip()
        if p["choices"] and code not in [c.split("=")[0].strip() for c in p["choices"]]:
            raise InputError("%s: %s takes %s" % (where, p["name"], " | ".join(p["choices"])))
        return code
    _fill_pscad(text, dict((k, 1.0) for k in PSCAD_KEYS), where)
    try:
        x = float(text.split("[")[0])
    except ValueError:
        return text                                   # a {KEY}, a signal's name, a value with its unit
    lo, hi = [_num({"v": v}, "v", where) if v not in (None, "") else None for v in (p["min"], p["max"])]
    if lo is not None and hi is not None and hi > lo and not lo <= x <= hi:
        raise InputError("%s: %s = %s is outside %s .. %s" % (where, p["name"], text, _g(lo), _g(hi)))
    if p["type"].upper() == "INTEGER" and x != int(x):
        raise InputError("%s: %s takes a whole number" % (where, p["name"]))
    return text


PREFS = os.path.join(os.path.expanduser("~"), ".plant_model_builder.json")   # the GUI's remembered choices


def gui_prefs(update=None):
    """The GUI's remembered choices (which vendor model pages the PSCAD_Params page lists ...) -> dict; update:
       merged in and saved (where the file cannot be written, kept for this session)."""
    got = GUI_PREFS.setdefault("now", None)
    if got is None:
        try:
            with io.open(PREFS, encoding="utf-8") as fh:
                got = json.load(fh)
        except Exception:
            got = {}
        GUI_PREFS["now"] = got
    if update:
        got.update(update)
        try:
            _write_json(PREFS, got)
        except Exception:
            pass
    return got


GUI_PREFS = {}


def udm_default_shown(sec):
    """Whether the PSCAD_Params page lists a section (udm_table's) until chosen otherwise: a vendor model itself,
       its signals and the parts of its own placed in it -- not the parts inside those, nor the library's."""
    lab = sec["label"]
    return lab.endswith("(the UDM)") or lab.endswith(">  signals") or (
        lab.count("  >  ") == 1 and not str(sec.get("defn") or "").lower().startswith("master:"))


def udm_has_set(sec):
    """Whether a udm_table section holds a value of the user's (a PSCAD_Params row)."""
    return any(how == "set" for _k, e in sec["params"] for _v, how in e["cells"].values())


def udm_shown(secs):
    """The sections the PSCAD_Params page lists: as chosen (Choose..., remembered), else as udm_default_shown --
       and always one that holds a value of the user's (none is out of sight)."""
    got = gui_prefs().get("pscad_pages")
    got = got if isinstance(got, dict) else {}
    return [x["label"] for x in secs if got.get(x["label"], udm_default_shown(x)) or udm_has_set(x)]


def udm_table(data, library=False):
    """What the PSCAD_Params tab shows: each generator's vendor model (UDM) parameters, read from its definition,
       and those of the parts inside it (Controller4 ...), with the value its PSCAD model gets -> ([generators with
       one], [{label, defn, src, part, params: [(key, {p, key, cells: {generator: (value, how)}})]}], notes); key:
       the parameter (Pref1), a part's with its path (Controller4/UFLevel1); how: set (a PSCAD_Params row), case
       (the copied component's own), default (the definition's, or as the part has it)."""
    gens, secs, notes = [], collections.OrderedDict(), []
    rows = data.get("pscad_params") or []
    for r in data.get("generators") or []:
        nm = _txt(r.get("name"))
        dn, iid = _txt(r.get("pscad_definition")), _txt(r.get("pscad_component_id"))
        if not nm or not (dn or iid):
            continue
        src = _txt(r.get("pscad_source")) or _txt(r.get("pscad_library"))
        form = udm_form(src, dn, iid, library) if src and os.path.isfile(src) else None
        if form is None:
            notes.append("%s: %s cannot be read here -- type its parameters in the rows below" % (
                nm, src or "no PSCAD source case / library given"))
            continue
        gens.append(nm)
        mine = dict(((_txt(x.get("parameter")) or "").upper(), _txt(x.get("value")) or "") for x in rows
                    if (_txt(x.get("generator")) or "").upper() == nm.upper())
        where = os.path.normcase(os.path.abspath(src))
        known = set()
        sigs = [{"path": "", "defn": "signals", "signals": True, "params": [
            {"name": x["signal"], "key": x["path"] + "/Value", "desc": "%s (%s%s)" % (
                x["path"], x["defn"].split(":")[-1], (" " + x["label"]) if x["label"] else ""),
             "type": "Real", "unit": "", "min": x["min"], "max": x["max"], "default": x["value"], "choices": [],
             "cat": "", "help": ""} for x in form["signals"]]}] if form["signals"] else []
        for part in [{"path": "", "defn": form["defn"], "params": form["params"]}] + form["parts"] + sigs:
            sec = secs.setdefault((form["defn"].upper(), where, part["path"].upper(), part.get("signals", False)), {
                "label": form["defn"] + ("  >  signals" if part.get("signals") else "  >  " + part["path"].replace(
                    "/", "  >  ") if part["path"] else "  (the UDM)"),
                "defn": part["defn"], "src": src, "part": part["path"] or ("signals" if part.get("signals") else ""),
                "params": collections.OrderedDict()})
            for q in part["params"]:
                key = q.get("key") or (part["path"] + "/" if part["path"] else "") + q["name"]
                known.add(key.upper())
                ent = sec["params"].setdefault(key, {"p": q, "key": key, "cells": {}})
                if key.upper() in mine:
                    ent["cells"][nm] = (mine[key.upper()], "set")
                elif not part["path"] and q["name"] in form["placed"]:
                    ent["cells"][nm] = (form["placed"][q["name"]], "case")
                else:
                    ent["cells"][nm] = (q["default"], "default")
        parts = set(q["path"].upper() for q in form["parts"])
        for k in mine:
            if k not in known and ("/" not in k or library or k.rsplit("/", 1)[0] in parts):
                notes.append("%s: %s is not a parameter of %s -- the build lists it as a CHECK" % (
                    nm, k, form["defn"]))
    return gens, [dict(v, params=list(v["params"].items())) for v in secs.values()], notes


def section_rxb(r, cab, f, w):
    """A feeder section's R, X (ohm) and B (uS) from its cable's data, length and parallel cables -- what blank
       R ohm / X ohm / B uS cells take."""
    par = _int(r, "parallel", w, 1, minimum=1)
    unit = _choice(r, "unit", w, LEN_UNITS, FEEDER_UNIT)
    per = _num(r, "length", w, required=True, positive=True) * LEN_KM[unit] / LEN_KM[cab["unit"]]
    bper = cab["b"] if cab["b"] is not None else (2.0 * math.pi * f * cab["c"] if cab["c"] is not None else 0.0)
    return cab["r"] * per / par, cab["x"] * per / par, bper * per * par


def _sum_rates(rates):
    """Branches side by side: their Rate A, B, C added -- each 0 (none) where one of them has none."""
    return tuple(sum(x[i] for x in rates) if rates and all(x[i] > 0 for x in rates) else 0.0 for i in range(3))


def wecc_collector(cname, rows, cables, kv, sb, f, unit_mw, own_gen, log):
    """The WECC equivalent of collector `cname` from its Feeders rows.
       unit_mw: {GENERATOR (upper): MW of one unit}; own_gen: whose units a row without 'Units of' carries
       (None: a shared collector -- every row says)."""
    zb = kv * kv / sb
    feeders, spelt, fname = collections.OrderedDict(), {}, "F1"
    for r in rows:                                   # a blank Feeder: the row above's; names in any case alike
        fname = spelt.setdefault(_txt(r.get("feeder")).upper(), _txt(r.get("feeder"))) if _txt(
            r.get("feeder")) else fname
        feeders.setdefault(fname, []).append(r)
    out = []
    sum_zw2, sum_b, W = 0j, 0.0, 0.0
    counts = collections.OrderedDict()
    for fname, frows in feeders.items():
        where = "%s (collector %s, feeder %s)" % (frows[0]["_where"], cname, fname)
        given = sorted(set(_int(r, "copies", r["_where"], None, minimum=1) for r in frows) - set([None]))
        copies = given[-1] if given else 1
        if len(given) > 1:
            log("NOTE: %s: Copies %s on its rows -- %d used" % (where, " / ".join(str(k) for k in given), copies))
        secs, order, last = {}, [], 0
        for r in frows:
            w = r["_where"]
            num = _int(r, "section", w, None, minimum=1)
            cable = _txt(r.get("cable"))
            given = dict((k, _num(r, k, w)) for k in ("r_ohm", "x_ohm", "b_us"))
            own = given["r_ohm"] is not None and given["x_ohm"] is not None     # a section by its R and X alone
            if num is None:
                num = last + 1 if cable or own else last
            if num == 0:
                raise InputError("%s: units on no section" % w)
            s = secs.get(num)
            if s is None:
                if not cable and not own:
                    raise InputError("%s: section %d has no cable (or R ohm and X ohm)" % (w, num))
                cab = cables.get(cable.upper()) if cable else None
                if cable and cab is None:
                    raise InputError("%s: cable %r is not in Cables" % (w, cable))
                need = cab is not None and any(v is None for v in given.values())
                s = {"num": num, "from": _int(r, "from_section", w, None, minimum=0),
                     "cable": cab["name"] if cab else "(R X B)",
                     "length": _num(r, "length", w, required=need, positive=need) or 0.0,
                     "unit": _choice(r, "unit", w, LEN_UNITS, FEEDER_UNIT),
                     "parallel": _int(r, "parallel", w, 1, minimum=1), "units": collections.OrderedDict(),
                     "where": w}
                s["R"], s["X"], s["B"] = section_rxb(r, cab, f, w) if need else (0.0, 0.0, 0.0)
                s["rates"] = tuple(x * s["parallel"] for x in cab["rates"]) if cab else (0.0, 0.0, 0.0)
                km = s["length"] * LEN_KM[s["unit"]]
                if 0 < km < 0.003:                   # a few feet: a length meant in km or mi, its unit blank?
                    log("CHECK: %s: section %d of feeder %s is %s %s (%.2f ft) -- a few feet: is its Length unit "
                        "right (blank = %s)?" % (w, num, fname, _g(s["length"]), s["unit"], km / LEN_KM["ft"],
                                                 FEEDER_UNIT))
                s["given"] = ""
                for k, fld in (("r_ohm", "R"), ("x_ohm", "X"), ("b_us", "B")):
                    if given[k] is not None:
                        if given[k] < 0 or (fld == "X" and given[k] <= 0):
                            raise InputError("%s: %s must be %s (it is %s)" % (
                                w, _label(k, "Feeders"), "above 0" if fld == "X" else "0 or more", _g(given[k])))
                        s[fld] = given[k]
                        s["given"] += fld
                s["z"] = complex(s["R"], s["X"]) / zb
                s["b"] = s["B"] * 1e-6 * zb
                secs[num] = s
                order.append(num)
            elif cable:
                raise InputError("%s: section %d of feeder %s is given twice" % (w, num, fname))
            last = max(last, num)
            n = _int(r, "units", w, 0, minimum=0)
            if not n and _txt(r.get("units_of")):
                _unused(log, r, ("units_of",), w, "no units on this row")
            if n:
                who = (_txt(r.get("units_of")) or own_gen or "").upper()
                if not who:
                    raise InputError("%s: a shared collector's units need 'Units of'" % w)
                if who not in unit_mw:
                    raise InputError("%s: Units of %r -- no such generator" % (w, who))
                if own_gen and who != own_gen.upper():
                    raise InputError("%s: units of %s on %s's own collector -- share it through Collectors"
                                     % (w, who, own_gen))
                s["units"][who] = s["units"].get(who, 0) + n
        for num in order:                            # a daisy chain unless a section says where it starts
            s = secs[num]
            if s["from"] is None:
                s["from"] = max([n for n in order if n < num] or [0])
            elif s["from"] and s["from"] not in secs:
                raise InputError("%s: section %d starts from section %d, which feeder %s does not have"
                                 % (s["where"], num, s["from"], fname))
        kids = dict((n, []) for n in [0] + order)
        for num in order:
            kids[secs[num]["from"]].append(num)
        seen, todo, post = set(), [0], []
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            seen.add(n)
            post.append(n)
            todo.extend(kids[n])
        if len(seen) != len(order) + 1:
            bad = [n for n in order if n not in seen]
            raise InputError("%s (collector %s, feeder %s): sections %s do not lead back to the collector bus (a "
                             "loop? see their From section)" % (secs[bad[0]]["where"], cname, fname, bad))
        for n in reversed(post):                     # leaves first: what each section carries downstream
            if n == 0:
                continue
            s = secs[n]
            s["W"] = sum(c * unit_mw[g] for g, c in s["units"].items())
            s["N"] = collections.OrderedDict(s["units"])
            for c in kids[n]:
                s["W"] += secs[c]["W"]
                for g, k in secs[c]["N"].items():
                    s["N"][g] = s["N"].get(g, 0) + k
        wf = sum(secs[n]["W"] for n in kids[0])
        nf = collections.OrderedDict()
        for n in kids[0]:
            for g, k in secs[n]["N"].items():
                nf[g] = nf.get(g, 0) + k
        zf = sum((secs[n]["z"] * secs[n]["W"] ** 2 for n in order), 0j)
        bf = sum(secs[n]["b"] for n in order)
        sum_zw2 += copies * zf
        sum_b += copies * bf
        W += copies * wf
        for g, k in nf.items():
            counts[g] = counts.get(g, 0) + copies * k
        out.append({"name": fname, "copies": copies, "sections": [secs[n] for n in order], "kids": kids,
                    "W": wf, "N": nf, "km": sum(secs[n]["length"] * LEN_KM[secs[n]["unit"]] for n in order),
                    "z_own": (zf / wf ** 2) if wf else 0j, "b_own": bf, "where": where,
                    "rates": _sum_rates([secs[n]["rates"] for n in kids[0]])})
    if not W > 0:
        raise InputError("collector %s: its feeders carry no units" % cname)
    z = sum_zw2 / (W * W)
    res = {"name": cname, "kv": kv, "r": z.real, "x": z.imag, "b": sum_b, "R": z.real * zb, "X": z.imag * zb,
           "B": sum_b / zb * 1e6, "W": W, "N": counts, "feeders": out, "_mw": dict(unit_mw),
           "rates": _sum_rates([tuple(fd["copies"] * x for x in fd["rates"]) for fd in out])}
    try:
        res["check"] = wecc_check(res, sb)
    except (OverflowError, ZeroDivisionError, ValueError):     # feeders far too long for their load
        res["check"] = None
    return res


def _sweep(nodes, parent, z, bsh, sinj, tol=1e-12, itmax=100):
    """Radial load flow (backward / forward sweep), root 0 at 1.0 pu. nodes: in order from the root out;
       z[n]: series impedance into n; bsh[n]: shunt B at n; sinj[n]: power injected at n (pu).
       -> (V, I into each node's branch, converged)."""
    V = dict((n, 1.0 + 0j) for n in [0] + nodes)
    I = dict((n, 0j) for n in nodes)
    for _it in range(itmax):
        J = dict((n, 0j) for n in [0] + nodes)
        for n in reversed(nodes):
            J[n] += -(sinj.get(n, 0j) / V[n]).conjugate() + 1j * bsh.get(n, 0.0) * V[n]
            I[n] = J[n]
            J[parent[n]] += J[n]
        dv = 0.0
        for n in nodes:
            v = V[parent[n]] - z[n] * I[n]
            dv = max(dv, abs(v - V[n]))
            V[n] = v
        if dv < tol:
            return V, I, True
    return V, I, False


def wecc_check(res, sb):
    """The detailed feeders (every unit at full output, unity power factor) against the equivalent, with the
       collector bus at 1.0 pu: losses (P, and Q net of the cables' charging) and voltages."""
    det = {"p": 0.0, "q": 0.0, "vmax": 1.0, "vmin": 1.0, "ok": True}
    for fd in res["feeders"]:
        nodes, parent, z, bsh, sinj = [], {}, {}, {}, {}
        todo = list(fd["kids"][0])
        secs = dict((s["num"], s) for s in fd["sections"])
        while todo:
            n = todo.pop(0)
            nodes.append(n)
            todo.extend(fd["kids"][n])
        for n in nodes:
            s = secs[n]
            parent[n] = s["from"]
            z[n] = s["z"]
            bsh[n] = bsh.get(n, 0.0) + s["b"] / 2.0
            bsh[s["from"]] = bsh.get(s["from"], 0.0) + s["b"] / 2.0
            sinj[n] = complex(sum(c * res["_mw"][g] for g, c in s["units"].items()) / sb, 0.0)
        V, I, ok = _sweep(nodes, parent, z, bsh, sinj)
        det["ok"] = det["ok"] and ok
        loss = sum(z[n] * abs(I[n]) ** 2 for n in nodes) - 1j * sum(bsh.get(n, 0.0) * abs(V[n]) ** 2
                                                                        for n in [0] + nodes)
        det["p"] += fd["copies"] * loss.real * sb
        det["q"] += fd["copies"] * loss.imag * sb
        if nodes:
            det["vmax"] = max(det["vmax"], max(abs(V[n]) for n in nodes))
            det["vmin"] = min(det["vmin"], min(abs(V[n]) for n in nodes))
    zq = complex(res["r"], res["x"])
    bq = res["b"]
    V, I, ok = _sweep([1], {1: 0}, {1: zq}, {0: bq / 2.0, 1: bq / 2.0}, {1: complex(res["W"] / sb, 0.0)})
    loss = zq * abs(I[1]) ** 2 - 1j * (bq / 2.0) * (abs(V[0]) ** 2 + abs(V[1]) ** 2)
    eq = {"p": loss.real * sb, "q": loss.imag * sb, "v": abs(V[1]), "ok": ok}
    return {"detailed": det, "equivalent": eq}


# ============================================================================
# BUILDING THE MODEL FROM THE WORKBOOK
# ============================================================================
def build_network(inp, log):
    pl = inp["plant"]
    W = "Plant"
    name = _safe_name(_txt(pl.get("name")) or auto_name(inp))
    sb = _num(pl, "sbase_mva", W, 100.0, positive=True)
    f = _num(pl, "freq_hz", W, 60.0, positive=True)
    if not 40.0 <= f <= 70.0:
        raise InputError("Plant: 'Frequency' = %g Hz -- 50 or 60" % f)
    net = Net(name, sb, f)
    if f not in (50.0, 60.0):
        net.notes.append("CHECK: Plant: 'Frequency' %g Hz -- not 50 or 60" % f)
    if _txt(pl.get("name")) is None:
        net.notes.append("NOTE: Plant: Name blank -- %s, from the generators" % name)
    elif name != _txt(pl.get("name")):
        net.notes.append("NOTE: model name %r is used as %r (letters, digits and _ only; 30 at most)" % (
            pl.get("name"), name))
    poi_kv = _num(pl, "poi_kv", W, required=True, positive=True)
    net.add_bus("IB", poi_kv, W)
    net.add_bus("POI", poi_kv, W)
    poi_name = _txt(pl.get("poi_name"))
    if poi_name:
        net.bus["POI"]["name"] = poi_name[:12]

    tie = line_data(pl, "tie_", poi_kv, sb, f, "Plant (gen-tie)", net.notes)
    if tie:
        net.notes.extend(tie["notes"])
        net.add_bus("HV", poi_kv, W)
        add_line(net, "POI", "HV", tie, "GEN-TIE", "GENTIE", "Plant (gen-tie)")
    else:
        net.add_alias("HV", "POI", W)

    loads = mpt_loads(inp)
    for r in inp["mpt"]:
        build_mpt(net, r, f, loads)

    cables = read_cables(inp["cables"], net.notes.append)
    feeders = collections.OrderedDict()
    for r in inp["feeders"]:
        c = _txt(r.get("collector"))
        if not c:
            raise InputError("%s: Collector is missing" % r["_where"])
        feeders.setdefault(c.upper(), []).append(r)

    gens = []
    for r in inp["generators"]:
        gens.append(gen_data(r))
        u, s0 = gens[-1]["unit"], _num(r, "unit_mva", r["_where"], None, positive=True)
        if s0 and max(u["qmax"], -u["qmin"]) > s0 * 1.0001:
            pm = max(abs(u["pmax"]), abs(u["pmin"]))
            net.notes.append("CHECK: %s: Unit Qmax %g / Qmin %g MVAr, beyond its Unit MVA %g -- at full P (%g MW) it "
                             "has %.4g MVAr, sqrt(MVA^2 - P^2) (blank Qmax and Qmin: that)" % (
                                 gens[-1]["where"], u["qmax"], u["qmin"], s0, pm,
                                 math.sqrt(max(s0 * s0 - pm * pm, 0.0))))
    names = [g["name"].upper() for g in gens]
    if len(set(names)) != len(names):
        raise InputError("Generators: generator names must be unique")
    if not gens:
        raise InputError("Generators: no generator")
    unit_mw = dict((g["name"].upper(), g["unit"]["pmax"]) for g in gens)
    used = set()

    for r in inp["collectors"]:                  # shared collector systems
        w = r["_where"]
        cname = _txt(r.get("name"))
        if not cname:
            raise InputError("%s: Name is missing" % w)
        at = net.find(_txt(r.get("connect")), w)
        rows = feeders.get(cname.upper())
        if not rows:
            raise InputError("%s: collector %s has no rows in Feeders" % (w, cname))
        used.add(cname.upper())
        res = wecc_collector(cname, rows, cables, net.kv(at), sb, f, unit_mw, None, net.notes.append)
        res.update({"root": at, "own": None, "end": "C:" + cname})
        net.colls[cname] = res
        net.add_bus("C:" + cname, net.kv(at), w, aliases=(cname,))
        add_line(net, at, "C:" + cname, {"r": res["r"], "x": res["x"], "b": res["b"], "R": res["R"],
                                           "X": res["X"], "B": res["B"], "count": 1, "rate": res["rates"][0],
                                           "rate_b": res["rates"][1], "rate_c": res["rates"][2], "kv": net.kv(at),
                                           "km": 0.0, "w_mw": res["W"]}, cname, "COLLECTOR", w)

    for g in gens:
        build_generator(net, g, feeders, cables, unit_mw, used, log)
    if outputs(inp)["dyd"]:                          # said now (Check inputs), as the .dyd will say it
        for g in gens:
            for m in [m for m in g["sync"] if m in PSLF_NAME] if g["type"] == "SYNC" else ():
                net.notes.append("CHECK: PSLF .dyd: %s %s (PSS/E %s) not written -- add it by hand" % (
                    g["name"], PSLF_NAME[m], m))
    net.model = _choice(pl, "model_type", W, ["SMIB", "DETAILED"], "SMIB")
    net.stem = "%s_%s" % (net.name, net.model)          # the output files' names: SMIB and DETAILED apart
    for g in gens:                                       # Units against the units its feeders carry
        on = [(c, res["N"].get(g["name"].upper(), 0)) for c, res in net.colls.items()
              if res["N"].get(g["name"].upper())]
        n = sum(k for _c, k in on)
        if on and n != g["n"]:
            net.notes.append("CHECK: %s: the feeders carry %d of its units (%s), Units says %d -- %s; GUI: Feeders "
                             "on each MPT (MPT or Feeders page) puts its Units on its feeders" % (
                                 g["where"], n, ", ".join("%d on %s" % (k, c) for c, k in on), g["n"],
                                 "the equivalent weighs the feeders' units; the machine has %d" % g["n"]
                                 if net.model == "SMIB" else "DETAILED: its unit groups have the feeders' %d units (P "
                                 "and MVA in proportion)" % n))
        ends = [net.colls[c]["end"] for c, _k in on]
        if on and g["mv"] not in ends:
            net.notes.append("CHECK: %s: %s has units on %s, but its GSU is at %s -- connect it to its collector "
                             "(Connect to) or give its units to the generator on it" % (
                                 g["where"], g["name"], ", ".join(c for c, _k in on), g["mv"]))
    if net.model == "SMIB":
        one_feeder_per_generator(net)
    else:
        expand_collectors(net)
    machines(net, inp["machines"])

    taken, det_nodes = set(), {}
    if net.model == "SMIB":                          # the DETAILED model's nodes: here, its collector's equivalent
        for cname, res in net.colls.items():
            for fd in res.get("feeders", []):
                for sc in fd["sections"]:
                    nm = ("%s_%s_S%d" % (_node(cname), _node(fd["name"]), sc["num"])).upper()
                    det_nodes[nm] = det_nodes[nm + ":LV"] = res["end"]
    for r in inp["loads_shunts"]:
        w = r["_where"]
        kind = _choice(r, "kind", w, ["LOAD", "SHUNT"], "LOAD")
        if not _txt(r.get("at")):
            raise InputError("%s: At is missing -- the bus of this %s" % (w, kind.lower()))
        ref = _txt(r.get("at"))
        if ref.upper() not in net.alias and ref.upper() in det_nodes:
            at = net.find(det_nodes[ref.upper()], w + " At")
            net.notes.append("NOTE: %s: %s is a node of the DETAILED model -- in this SMIB model the %s is at %s, "
                             "the far end of its collector's equivalent" % (w, ref, kind.lower(), at))
        else:
            at = net.find(ref, w + " At")
        if _txt(r.get("at")).upper() in net.moved:
            net.notes.append("NOTE: %s: %s -- in the DETAILED model %s, the collector's root" % (
                w, _txt(r.get("at")), at))
        nm = _safe_name(_txt(r.get("name")) or kind)[:20]
        k = 1
        while nm.upper() in taken:                  # PSCAD tags and PSS/E ids must differ
            k += 1
            nm = "%s_%d" % (_safe_name(_txt(r.get("name")) or kind)[:17], k)
        taken.add(nm.upper())
        if kind == "LOAD":
            net.loads.append({"bus": at, "mw": _num(r, "mw", w, 0.0), "mvar": _num(r, "mvar", w, 0.0),
                              "name": nm, "where": w})
        else:
            _unused(net.notes, r, ("mw",), w, "a SHUNT is MVAr only (a LOAD takes MW)", {"mw": 0.0})
            mvar = _num(r, "mvar", w, required=True)
            if mvar == 0:
                net.notes.append("NOTE: %s: shunt %s of 0 MVAr -- not used" % (w, nm))
                continue
            net.shunts.append({"bus": at, "mvar": mvar, "name": nm, "where": w})

    left = [k for k in feeders if k not in used]
    if left:
        raise InputError("Feeders: rows for %s, which is no generator with Collector FEEDERS and no shared "
                         "collector" % ", ".join(sorted(left)))

    thevenin(net, pl, gens)
    control(net, pl)
    number_buses(net, pl, inp["bus_numbers"])
    net.notes.extend(inp.get("_said", []))
    for u in inp.get("_unknown", []):
        net.notes.append("NOTE: not used -- %s%s" % (u, " (Plant: Model type, SMIB or DETAILED, now sets PSS/E and "
                                                        "PSCAD alike)" if "PSCAD COLLECTOR" in u.upper() else ""))
    return net


def _windings(r, w):
    """An MPT's Windings: 2, 3 or None (blank: 3 when it has a TV kV)."""
    v = r.get("windings")
    if v is None or str(v).strip() == "":
        return None
    t = re.sub(r"[\s_-]", "", str(v).upper())
    for n, words in ((2, ("2", "2.0", "2W", "TWO", "2WINDING", "2WINDINGS", "TWOWINDING")),
                     (3, ("3", "3.0", "3W", "THREE", "3WINDING", "3WINDINGS", "THREEWINDING"))):
        if t in words:
            return n
    raise InputError("%s: Windings %r -- 2 or 3" % (w, v))


def build_mpt(net, r, f, loads=None):
    """An MPT row -> its transformer. loads (mpt_loads): the units' MVA on each MPT -- a blank MVA takes it, and
       an MVA far from it is CHECKed."""
    w = r["_where"]
    name = _txt(r.get("name"))
    if not name:
        raise InputError("%s: Name is missing" % w)
    w = "%s (%s)" % (w, name)
    kv1 = _num(r, "hv_kv", w, required=True, positive=True)
    kv2 = _num(r, "lv_kv", w, required=True, positive=True)
    kv3 = _num(r, "tv_kv", w, None, positive=True)
    count = _int(r, "count", w, 1, minimum=1)
    on = (loads or {}).get(name.upper()) or {"lv": 0.0, "tv": 0.0, "gens": []}
    units = on["lv"] + on["tv"]
    fm = lambda x: "%.6g" % x
    gens = " + ".join("%s %s%s" % (g, fm(x), " on TV" if wd == "TV" else "") for g, x, wd in on["gens"])
    mva = _num(r, "mva", w, None, positive=True)
    if mva is None:
        if not units > 0:
            raise InputError("%s: %s is missing -- blank takes the MVA of the units on it, and no generator is on %s"
                             % (w, _label("mva", w), name))
        mva = units / count
        net.notes.append("NOTE: %s: %s blank -- %s MVA, the units on it (%s)%s" % (
            w, _label("mva", w), fm(mva), gens, (", over its %d parallel units" % count) if count > 1 else ""))
    elif units > 0:                                  # the rating against what it carries
        cap = mva * count
        if units > cap * 1.02:
            net.notes.append("CHECK: %s: the units on it are %s MVA (%s), above its %s MVA" % (
                w, fm(units), gens, fm(cap)))
        elif cap > 2.0 * units:
            stiff = _txt(r.get("base_hl_mva")) is None
            net.notes.append("CHECK: %s: %s MVA for %s MVA of units on it (%s: %.0f %%) -- %sgive its nameplate MVA "
                             "(blank = the units' MVA; GUI: MPT page, Size MPTs from the generators)" % (
                                 w, fm(cap), fm(units), gens, 100.0 * units / cap,
                                 "its Z %% is on its MVA: %.3g times as stiff as an MPT sized for them; " % (
                                     cap / units) if stiff else ""))
    nw = _windings(r, w)
    vector = _txt(r.get("vector"))
    vm = _VG.match((vector or "").replace(" ", ""))
    turns = re.findall(r"(yn|y|d|zn|z)(\d{1,2})", vm.group(2)) if vm else []
    if nw == 2 and kv3 is not None:
        kv3 = None                                   # its TV kV and other three-winding values: said below
    if nw == 3 and kv3 is None:
        kv3 = kv2
        net.notes.append("NOTE: %s: Windings 3, no TV kV -- the TV winding at the LV's %g kV (a split LV winding)"
                         % (w, kv2))
    if kv3 is not None and len(turns) == 1:            # a two-winding group: the TV winding as the LV one
        vector = vector.replace(" ", "") + "".join(turns[0])
        net.notes.append("NOTE: %s: vector group %s for three windings -- %s (the TV winding as the LV one)"
                         % (w, _txt(r.get("vector")), vector))
    if kv3 is None and len(turns) == 2:
        if nw is None:
            raise InputError("%s: vector group %s has three windings -- set Windings 3 (and TV kV), or a "
                             "two-winding group (YNd1 ...)" % (w, vector))
        vector = vector.replace(" ", "")[:len(vector.replace(" ", "")) - len("".join(turns[1]))]
        net.notes.append("NOTE: %s: Windings 2 -- vector group %s for its two windings" % (w, vector))
    tap = _num(r, "tap", w, 1.0, positive=True)
    rates = _abc(r, ("rate_mva", "rate_b_mva", "rate_c_mva"), w, mva, net.notes)
    hv = net.find("HV", w)
    tie = line_data(r, "tie_", net.kv("HV"), net.sb, f, w + " tie", net.notes)
    if tie:
        net.notes.extend(tie["notes"])
        hv = net.add_bus(name + ":HV", net.kv("HV"), w)
        add_line(net, net.find("HV", w), hv, tie, name + "-TIE", "TIE", w)
    else:
        net.add_alias(name + ":HV", "HV", w)
    if abs(kv1 / net.kv("HV") - 1.0) > KV_OFF:
        raise InputError("%s: 'HV kV' %.4g kV, but the POI it connects to is %.4g kV (Plant: 'POI kV') -- the "
                         "voltages do not match: set one to the other" % (w, kv1, net.kv("HV")))
    if abs(kv1 / net.kv("HV") - 1.0) > 0.05:
        net.notes.append("CHECK: %s: HV %.4g kV on the %.4g kV HV bus" % (w, kv1, net.kv("HV")))
    lv = net.add_bus(name + ":LV", kv2, w, aliases=(name,))
    common = {"name": name, "kind": "MPT", "tap": tap, "count": count, "where": w, "vector": vector,
              "nll_pct": _num(r, "nll_pct", w, 0.0), "im_pct": _num(r, "im_pct", w, 0.0),
              "x_r": _num(r, "x_r", w)}
    if kv3 is None:
        _unused(net.notes, r, ("tv_kv", "lv_mva", "tv_mva", "z_ht_pct", "z_lt_pct", "base_ht_mva", "base_lt_mva"), w,
                "two windings: they are three-winding values")
        z = _num(r, "z_hl_pct", w, required=True, positive=True)
        zb = _num(r, "base_hl_mva", w, mva, positive=True)
        if abs(zb - mva) > 1e-9 * mva:               # Z on a base of its own (ONAN ...): on its MVA here
            net.notes.append("NOTE: %s: Z HV-LV %g %% on %g MVA = %.6g %% on its MVA %g" % (w, z, zb, z * mva / zb,
                                                                                         mva))
            z = z * mva / zb
        d = dict(common)
        d.update({"hv": hv, "lv": lv, "mva": mva, "z_pct": z,
                  "load_loss_kw": _num(r, "load_loss_kw", w), "kv1": kv1, "kv2": kv2,
                  "rate": rates[0], "rate_b": rates[1], "rate_c": rates[2]})
        add_tx2(net, d)
    else:
        _unused(net.notes, r, ("load_loss_kw",), w, "three windings: each R from its X/R (the load loss is for two)")
        tv = net.add_bus(name + ":TV", kv3, w)
        d = dict(common)
        split = abs(kv3 - kv2) <= 1e-9 * kv2
        dl, dt = mpt_windings(mva, on, split)
        mva2, mva3 = _num(r, "lv_mva", w, None, positive=True), _num(r, "tv_mva", w, None, positive=True)
        z12 = _num(r, "z_hl_pct", w, required=True, positive=True)
        b12, b13, b23 = [_num(r, k, w, mva, positive=True) for k in ("base_hl_mva", "base_ht_mva", "base_lt_mva")]
        z13, z23 = _num(r, "z_ht_pct", w, None, positive=True), _num(r, "z_lt_pct", w, None, positive=True)
        said = []
        if mva2 is None or mva3 is None:
            how = ("the MVA shared out by the units on each (%s)" % gens if on["lv"] > 0 and on["tv"] > 0 else
                   "nothing on %s: %s" % ("either" if on["lv"] + on["tv"] <= 0 else "TV" if on["tv"] <= 0 else "LV",
                                          "MVA/2, a split LV winding" if split else "MVA/3, a tertiary"))
            said.append("%s (%s)" % (" and ".join("%s MVA %s" % (wd, fm(x)) for wd, x, v in (
                ("LV", dl, mva2), ("TV", dt, mva3)) if v is None), how))
        if z13 is None:
            z13 = z12 * b13 / b12
            said.append("Z HV-TV %s %% (Z HV-LV's)" % fm(z13))
        if z23 is None:
            z23 = SPLIT_LT * z12 * b23 / b12
            said.append("Z LV-TV %s %% (%g x Z HV-LV, a split LV winding's)" % (fm(z23), SPLIT_LT))
        if said:
            net.notes.append("NOTE: %s: three windings, blank -- %s; give the nameplate's" % (w, "; ".join(said)))
        d.update({"hv": hv, "lv": lv, "tv": tv, "kv1": kv1, "kv2": kv2, "kv3": kv3, "mva1": mva,
                  "mva2": dl if mva2 is None else mva2, "mva3": dt if mva3 is None else mva3,
                  "z12": z12, "z13": z13, "z23": z23, "base12": b12, "base13": b13, "base23": b23,
                  "rate": rates[0], "rate_b": rates[1], "rate_c": rates[2]})
        for k, x, wd, v in (("mva2", on["lv"], "LV", mva2), ("mva3", on["tv"], "TV", mva3)):
            if v is not None and x > d[k] * count * 1.02:
                net.notes.append("CHECK: %s: the units on its %s winding are %s MVA, above its %s MVA" % (
                    w, wd, fm(x), fm(d[k] * count)))
        add_tx3(net, d)


def unit_rating(r, w):
    """A Generators row's units and one unit's rating -> (units, Pmax, Pmin, PF, Qmax, Qmin, MVA): Pmin blank 0,
       Qmax blank the MVA left at full P (sqrt(MVA^2 - P^2), P the larger of Pmax and -Pmin) -- from Unit PF where
       that is given or Unit MVA is blank (PF blank 0.95), Qmin blank -Qmax, MVA blank from P and Q."""
    n = _int(r, "units", w, required=True, minimum=1)
    pmax = _num(r, "unit_pmax_mw", w, required=True)
    pmin = _num(r, "unit_pmin_mw", w, 0.0)
    pf = _num(r, "unit_pf", w, 0.95)
    qmax = _num(r, "unit_qmax_mvar", w)
    s0 = _num(r, "unit_mva", w, None, positive=True)
    if qmax is None and s0 is not None and _txt(r.get("unit_pf")) is None:      # the MVA circle at full P
        pm = max(abs(pmax), abs(pmin))
        if pm > s0 + 1e-9:
            raise InputError("%s: %s %g is above %s %g -- Unit Qmax, blank, is the MVA left at full P" % (
                w, _label("unit_pmax_mw" if abs(pmax) >= abs(pmin) else "unit_pmin_mw", w),
                pmax if abs(pmax) >= abs(pmin) else pmin, _label("unit_mva", w), s0))
        qmax = math.sqrt(max(s0 * s0 - pm * pm, 0.0)) + 0.0
        pf = abs(pmax) / math.hypot(pmax, qmax) if pmax else 1.0
    if qmax is None:
        if not 0.0 < abs(pf) <= 1.0:
            raise InputError("%s: %s %g must be between 0 and 1 (it sets Unit Qmax, which is blank)" % (
                w, _label("unit_pf", w), pf))
        qmax = abs(pmax) * math.tan(math.acos(abs(pf)))
    qmin = _num(r, "unit_qmin_mvar", w, -qmax if qmax else 0.0)
    s = s0 or math.hypot(max(abs(pmax), abs(pmin)), max(qmax, -qmin))
    if pmin > pmax:
        raise InputError("%s: %s %g is above %s %g" % (w, _label("unit_pmin_mw", w), pmin, _label("unit_pmax_mw", w),
                                                      pmax))
    if qmin > qmax and _txt(r.get("unit_qmin_mvar")) is None:    # Qmin blank: -Qmax -- a Qmax below 0
        raise InputError("%s: %s %g is below 0 -- with %s blank (-Qmax, %g) the Q range is upside down: Qmax is the "
                         "most Q it gives (+), Qmin the most it takes (-)" % (
                             w, _label("unit_qmax_mvar", w), qmax, _label("unit_qmin_mvar", w), qmin))
    if qmin > qmax:
        raise InputError("%s: %s %g is above %s %g" % (w, _label("unit_qmin_mvar", w), qmin,
                                                      _label("unit_qmax_mvar", w), qmax))
    return n, pmax, pmin, pf, qmax, qmin, s


def _rows(data, key):
    """A table's rows as the build takes them: those starting with # left out (the GUI keeps them, set aside)."""
    cols = dict((t[1], t[2]) for t in TABLES)[key]
    return [r for r in data.get(key) or [] if not _skip([r.get(c[0]) for c in cols])]


KINDS = collections.OrderedDict([  # the kinds of generator a plant can have: (name, what, generator types)
    ("PV", ("PV", "PV", ("PV",))), ("BESS", ("BESS", "BESS", ("BESS",))),
    ("WT4", ("WIND", "wind, type 4", ("WT4",))), ("WT3", ("WIND", "wind, type 3", ("WT3",))),
    ("SYNC", ("SYNC", "synchronous (gas, steam, hydro)", ("SYNC",))), ("USER", ("GEN", "your own records", ("USER",)))])


def plant_kinds(data):
    """The kinds of generator the Generators rows have, in KINDS' order -> {kind: (generator names, MW: their
       Pmax)}."""
    out = collections.OrderedDict()
    for r in _rows(data, "generators"):
        t, nm = (_txt(r.get("type")) or "").upper(), _txt(r.get("name"))
        if t not in KINDS or not nm:
            continue
        e = out.setdefault(t, ([], 0.0))
        e[0].append(nm)
        out[t] = (e[0], e[1] + _cell_num(r, "units", 0.0) * _cell_num(r, "unit_pmax_mw", 0.0))
    return collections.OrderedDict((k, out[k]) for k in KINDS if k in out)


def plant_kind_text(data):
    """What the plant is, from its generators: PV only, BESS only, a hybrid of PV + BESS + SYNC ..."""
    ks = plant_kinds(data)
    if not ks:
        return "no generators yet"
    names = [KINDS[k][1] for k in ks]
    return ("%s only" % names[0]) if len(ks) == 1 else "a hybrid of %s" % " + ".join(names)


def auto_name(data):
    """A plant's name from its generators: their kinds and MW (Pmax) -- PV100, BESS50, Hybrid_PV100_BESS50,
       Hybrid_PV50_WIND30_SYNC20 (no generators: Plant)."""
    mw = collections.OrderedDict()
    for k, (_names, x) in plant_kinds(data).items():
        mw[KINDS[k][0]] = mw.get(KINDS[k][0], 0.0) + x
    if not mw:
        return "Plant"
    f = lambda x: ("%d" % round(x)) if x >= 10 or abs(x - round(x)) < 0.05 else ("%.1f" % x).replace(".", "p")
    return _safe_name(("Hybrid_" if len(mw) > 1 else "") + "_".join("%s%s" % (k, f(x)) for k, x in mw.items()))


def kind_rows(data, kind, name):
    """A new generator of a kind for this plant -> (its Generators row, its Feeders rows, the Cables rows it needs):
       an inverter's on the first MPT, its feeders like the plant's first feeder (else the example's), its units on
       them; a synchronous one at HV behind its own GSU."""
    mpts = _rows(data, "mpt")
    m = mpts[0] if mpts else {}
    mpt, lv, poi = _txt(m.get("name")) or "MPT1", _txt(m.get("lv_kv")) or "34.5", _txt((data.get("plant") or {}).get(
        "poi_kv")) or "230"
    ex = dict((r["type"], r) for r in EXAMPLE["generators"])
    if kind in ("PV", "BESS"):
        row = dict((k, str(v)) for k, v in ex[kind].items())
        row.update({"name": name, "connect": mpt, "gsu_hv_kv": lv})
    elif kind in ("WT4", "WT3"):
        row = {"name": name, "type": kind, "connect": mpt, "units": "20", "unit_mva": "3.6", "unit_pmax_mw": "3.3",
               "kv": "0.69", "q_mode": "share", "gsu_mva": "3.6", "gsu_hv_kv": lv, "gsu_lv_kv": "0.69",
               "gsu_z_pct": "6", "gsu_x_r": "8", "gsu_vector": "Dyn11", "collector": "FEEDERS"}
    elif kind == "SYNC":
        row = {"name": name, "type": "SYNC", "connect": "HV", "units": "1", "unit_mva": "50", "unit_pmax_mw": "45",
               "unit_pmin_mw": "10", "kv": "13.8", "q_mode": "share", "gsu_mva": "50", "gsu_hv_kv": poi,
               "gsu_lv_kv": "13.8", "gsu_z_pct": "10", "gsu_x_r": "40", "gsu_vector": "YNd1", "collector": "NONE"}
    else:
        row = {"name": name, "type": "USER", "connect": mpt, "units": "1", "unit_mva": "10", "unit_pmax_mw": "9",
               "kv": lv, "q_mode": "share", "collector": "NONE"}
    if row.get("collector") != "FEEDERS":
        return row, [], []
    lay = feeder_layout(data)
    src = next((g for g in (_txt(r.get("name")) for r in _rows(data, "generators")) if g and g.upper() in lay), None)
    cols = [c[0] for c in FEEDER_COLS]
    if src:
        mine = [r for r in _rows(data, "feeders") if (_txt(r.get("collector")) or "").upper() == src.upper()]
        fs = _feeders_of(mine)
        first = fs[max(fs, key=lambda f: (sum(_cell_num(r, "units") for r in fs[f]), len(fs[f])))]
        cabs = []
    else:
        first = [dict((k, str(v)) for k, v in r.items()) for r in EXAMPLE["feeders"] if r["collector"] == "PV"]
        have = set((_txt(c.get("cable")) or "").upper() for c in _rows(data, "cables"))
        cabs = [dict((k, str(v)) for k, v in c.items()) for c in EXAMPLE["cables"] if c["cable"].upper() not in have]
    feeders = [dict((k, r[k]) for k in cols if k in r and k not in ("collector", "feeder", "copies", "units_of")) for
               r in first]
    for f in feeders:
        f.update({"collector": name, "feeder": "F", "copies": "1"})
    return row, feeders, cabs


def set_kinds(data, kinds, name=None):
    """The plant made of these kinds of generator (KINDS) -> (the inputs as they would be, [what was done]): a kind
       it has keeps its generators; one it does not have gets one (kind_rows), its units on its feeders; one not
       wanted goes, with every row naming its generators (their feeders, parameters, records, bus numbers, loads
       on their buses). name: the plant's name (blank: from the generators)."""
    d = json.loads(json.dumps(data))
    have = plant_kinds(d)
    did = []
    for k in [k for k in have if k not in kinds]:
        gone = set(n.upper() for n in have[k][0])
        own = lambda v: (_txt(v) or "").upper().split(":")[0] in gone
        d["generators"] = [r for r in d.get("generators") or [] if not own(r.get("name"))]
        n0 = sum(len(d.get(t) or []) for t in ("feeders", "machines", "dyr_params", "dyr_records", "pscad_params",
                                                "bus_numbers", "loads_shunts"))
        d["feeders"] = [r for r in d.get("feeders") or [] if not own(r.get("collector")) and not own(r.get("units_of"))]
        d["machines"] = [r for r in d.get("machines") or [] if not any(
            (_txt(r.get("machine")) or "").upper().startswith(g) for g in gone)]
        for t in ("dyr_params", "dyr_records", "pscad_params"):
            d[t] = [r for r in d.get(t) or [] if not own((_txt(r.get("generator")) or "").lstrip("#"))]
        d["bus_numbers"] = [r for r in d.get("bus_numbers") or [] if not own(r.get("bus"))]
        d["loads_shunts"] = [r for r in d.get("loads_shunts") or [] if not own(r.get("at"))]
        n1 = sum(len(d.get(t) or []) for t in ("feeders", "machines", "dyr_params", "dyr_records", "pscad_params",
                                                "bus_numbers", "loads_shunts"))
        did.append("%s removed: %s%s" % (KINDS[k][1], ", ".join(have[k][0]),
                                         (" and %d row(s) naming them" % (n0 - n1)) if n0 != n1 else ""))
    for k in [k for k in kinds if k not in have]:
        taken = set((_txt(r.get("name")) or "").upper() for t in ("generators", "mpt", "collectors")
                    for r in d.get(t) or [])
        nm, i = KINDS[k][0], 2
        while nm.upper() in taken:
            nm, i = "%s%d" % (KINDS[k][0], i), i + 1
        row, feeders, cabs = kind_rows(d, k, nm)
        d.setdefault("generators", []).append(row)
        d.setdefault("feeders", []).extend(feeders)
        d.setdefault("cables", []).extend(cabs)
        said = ""
        if feeders:
            try:
                d["feeders"], said = set_feeders(d, nm)
            except InputError:
                d["feeders"], said = set_feeders(d, nm, 1)
            said = " -- " + said.split(" -- ", 1)[-1]
        did.append("%s added: %s, %s x %s MW on %s%s" % (KINDS[k][1], nm, row["units"], row["unit_pmax_mw"],
                                                         row["connect"], said))
    if name is not None:
        if _txt(name) is None or _txt(name) == auto_name(d):
            d.setdefault("plant", {}).pop("name", None)
        else:
            d.setdefault("plant", {})["name"] = _txt(name)
    return d, did


def plant_totals(data):
    """What the Generators rows add up to for the whole plant, and what each MPT carries -> (plant line, [MPT
       lines], {"pmax": MW, "dispatch": MW}) -- a generator whose row does not read yet is left out, named."""
    f = lambda x: "%.6g" % (x + 0.0)
    mva = pmax = pmin = qmax = qmin = disp = 0.0
    units, bad = 0, []
    for r in _rows(data, "generators"):
        if not any(v not in (None, "") for v in r.values()):
            continue
        nm = _txt(r.get("name")) or "?"
        try:
            n, p1, p0, _pf, q1, q0, s = unit_rating(r, nm)
            d = _num(r, "dispatch_mw", nm)
        except InputError:
            bad.append(nm)
            continue
        units += n
        mva, pmax, pmin, qmax, qmin = mva + n * s, pmax + n * p1, pmin + n * p0, qmax + n * q1, qmin + n * q0
        disp += n * p1 if d is None else d
    line = "Whole plant (from Generators): %s -- %s MVA, %d units  |  P %s to %s MW, dispatch %s MW  |  Q %s to %s " \
           "MVAr" % (plant_kind_text(data), f(mva), units, f(pmin), f(pmax), f(disp), f(qmin), f(qmax)) + (
        ("   (%s not counted: its row does not read yet)" % ", ".join(bad)) if bad else "")
    mpts, loads, fon = [], mpt_loads(data), feeders_on_mpts(data)
    for m in _rows(data, "mpt"):
        nm = _txt(m.get("name"))
        if not nm:
            continue
        try:
            rate, cnt = _num(m, "mva", nm, None, positive=True), _int(m, "count", nm, 1, minimum=1)
        except InputError:
            rate, cnt = None, 1
        e = loads.get(nm.upper()) or {"lv": 0.0, "tv": 0.0, "gens": []}
        tot = e["lv"] + e["tv"]
        gs = " + ".join("%s %s%s" % (g, f(x), " on TV" if wd == "TV" else "") for g, x, wd in e["gens"])
        size = ("%d x %s" % (cnt, f(rate)) if cnt > 1 else f(rate)) if rate else ""
        if not tot:
            mpts.append("%s%s: no generator on it%s" % (nm, (" %s MVA" % size) if rate else "",
                                                        "" if rate else " (MVA blank: give one)"))
        elif rate is None:
            mpts.append("%s: MVA blank = the units on it, %s MVA (%s)%s" % (
                nm, f(tot / cnt), gs, (", over %d parallel units" % cnt) if cnt > 1 else ""))
        else:
            mpts.append("%s %s MVA: carries %s = %s MVA (%.0f %% of its rating)%s" % (
                nm, size, gs, f(tot), 100.0 * tot / (rate * cnt),
                (" -- sized for them: %s MVA (Size MPTs from the generators)" % f(nice_mva(tot / cnt)))
                if tot > 1.02 * rate * cnt or rate * cnt > 2.0 * tot else ""))
        fd = fon.get(nm.upper())
        if fd:
            mpts[-1] += "; %d feeder%s (%s)" % (sum(k for _g, k, _u, _s in fd), "" if sum(
                k for _g, k, _u, _s in fd) == 1 else "s", ", ".join("%s %d" % (g, k) for g, k, _u, _s in fd))
    return line, mpts, {"pmax": pmax, "dispatch": disp}


def feeder_lines(data):
    """The Feeders page's live lines: each MPT's feeders, generator by generator -- its feeders (x copies), the
       units on each and on them all against its Units."""
    on, lay = mpt_of(data), feeder_layout(data)
    shared = set((_txt(c.get("name")) or "").upper() for c in _rows(data, "collectors"))
    units = {}
    for r in _rows(data, "generators"):
        nm = _txt(r.get("name"))
        if nm:
            try:
                units[nm.upper()] = _int(r, "units", nm)
            except InputError:
                units[nm.upper()] = None
    names = [_txt(r.get("name")) for r in _rows(data, "generators")] + [_txt(c.get("name"))
                                                                         for c in _rows(data, "collectors")]
    by = collections.OrderedDict((((_txt(m.get("name")) or "").upper()), []) for m in _rows(data, "mpt"))
    for nm in names:
        if not nm or nm.upper() not in lay or (nm.upper() in units and nm.upper() in shared):
            continue
        fs = lay[nm.upper()]
        tot = sum(k * u for _f, k, u in fs)
        u = units.get(nm.upper())
        of = (" = %d of its %d units" % (tot, u) + ("" if tot == u else " -- Units says %d" % u)) if u else ""
        txt = "%s%s %d (%s%s)" % (nm, " (shared)" if nm.upper() in shared else "", sum(k for _f, k, _u in fs),
                                  ", ".join("%s x %d: %d units each" % (f, k, n) for f, k, n in fs),
                                  "" if nm.upper() in shared else of)
        by.setdefault(on.get(nm.upper(), ("not on an MPT",))[0], []).append((sum(k for _f, k, _u in fs), txt))
    return ["   %s: %d feeder%s -- %s" % (m, sum(k for k, _t in v), "" if sum(k for k, _t in v) == 1 else "s",
                                         "; ".join(t for _k, t in v)) for m, v in by.items() if v]


def gen_blanks(r):
    """A Generators row's blank Unit MVA, Unit Qmax and Unit Qmin as the build takes them -> {key: value} (the GUI
       shows them in brackets); {} where the row does not read yet."""
    try:
        _n, _p1, _p0, _pf, q1, q0, s1 = unit_rating(r, "Generators")
    except (InputError, ValueError, TypeError, ZeroDivisionError):
        return {}
    return dict((k, v) for k, v in (("unit_mva", s1), ("unit_qmax_mvar", q1), ("unit_qmin_mvar", q0))
                if _txt(r.get(k)) is None)


def cable_b(r, data):
    """A Cables row's blank B per length as the build takes it from its C (2 pi f C) -> {"b_per": B} or {}."""
    try:
        c = _txt(r.get("c_per"))
        if c is None or _txt(r.get("b_per")) is not None:
            return {}
        return {"b_per": 2.0 * math.pi * (_cell_num((data or {}).get("plant") or {}, "freq_hz", 60.0) or 60.0) *
                float(c)}
    except (ValueError, TypeError):
        return {}


def mpt_blanks(r, loads):
    """What an MPT row's blank cells come to as the build takes them -> {key: value}: Windings, MVA (the units on
       it), Rate A / B / C and, three windings, TV kV, LV / TV MVA, Z HV-TV, Z LV-TV -- {} as far as it does not read
       yet (the GUI shows them in brackets)."""
    out, w = {}, "MPT"
    try:
        on = (loads or {}).get((_txt(r.get("name")) or "").upper()) or {"lv": 0.0, "tv": 0.0}
        count = _int(r, "count", w, 1, minimum=1)
        mva = _num(r, "mva", w, None, positive=True)
        if mva is None and on["lv"] + on["tv"] > 0:
            mva = out["mva"] = (on["lv"] + on["tv"]) / count
        nw = _windings(r, w)
        kv2, kv3 = _num(r, "lv_kv", w, None, positive=True), _num(r, "tv_kv", w, None, positive=True)
        three = nw == 3 or (nw is None and kv3 is not None)
        if nw is None:
            out["windings"] = "3" if three else "2"
        if mva:
            a, b, c = _abc(r, ("rate_mva", "rate_b_mva", "rate_c_mva"), w, mva)
            for k, x in (("rate_mva", a), ("rate_b_mva", b), ("rate_c_mva", c)):
                if _txt(r.get(k)) is None and x:
                    out[k] = x
        if not three:
            return out
        if kv3 is None and kv2:
            kv3 = out["tv_kv"] = kv2
        if mva and kv2 and kv3:
            dl, dt = mpt_windings(mva, on, abs(kv3 - kv2) <= 1e-9 * kv2)
            for k, x in (("lv_mva", dl), ("tv_mva", dt)):
                if _txt(r.get(k)) is None:
                    out[k] = x
        z12 = _num(r, "z_hl_pct", w, None, positive=True)
        if z12 and mva:
            b12, b13, b23 = [_num(r, k, w, mva, positive=True) for k in ("base_hl_mva", "base_ht_mva", "base_lt_mva")]
            if _txt(r.get("z_ht_pct")) is None:
                out["z_ht_pct"] = z12 * b13 / b12
            if _txt(r.get("z_lt_pct")) is None:
                out["z_lt_pct"] = SPLIT_LT * z12 * b23 / b12
    except (InputError, ValueError, TypeError, ZeroDivisionError):
        pass
    return out


def mpt_windings(mva, on, split):
    """A three-winding MPT's LV and TV winding MVA where blank: its MVA shared out by the units' MVA on each winding
       (on: mpt_loads'); a winding with none on it MVA/2 on a split LV winding (TV kV the LV's), MVA/3 a tertiary --
       the LV winding all of it when the TV has none (or neither has any)."""
    a, b = (on or {}).get("lv", 0.0), (on or {}).get("tv", 0.0)
    none = mva * (0.5 if split else 1.0 / 3.0)
    if b <= 0:
        return mva, none
    if a <= 0:
        return none, mva
    return mva * a / (a + b), mva * b / (a + b)


def nice_mva(x):
    """A transformer rating at or above x MVA: 0.05 MVA steps below 1, 0.25 to 10, 1 to 50, 5 to 200, then 10."""
    for top, step in ((1.0, 0.05), (10.0, 0.25), (50.0, 1.0), (200.0, 5.0), (float("inf"), 10.0)):
        if x <= top + 1e-9:
            return round(math.ceil(x / step - 1e-9) * step, 4)


def mpt_of(data):
    """The MPT each generator and shared collector is on: {name (upper): (MPT name (upper), "LV" or "TV")} --
       through shared collectors and other generators' buses to where it connects; one beside an MPT (MPT1:HV,
       HV, POI) left out."""
    mpts = set((_txt(m.get("name")) or "").upper() for m in _rows(data, "mpt"))
    via = {}
    for k in ("collectors", "generators"):
        for c in _rows(data, k):
            nm = (_txt(c.get("name")) or "").upper()
            if nm and nm not in mpts:
                via.setdefault(nm, (_txt(c.get("connect")) or "").upper())
    out = {}
    for nm, to in via.items():
        name, _c, wind = to.partition(":")
        seen = set([nm])
        while name not in mpts and name in via and name not in seen:
            seen.add(name)
            name, _c, wind = via[name].partition(":")
        if name in mpts and wind in ("", "LV", "TV"):          # MPT1:HV -- beside it, not through it
            out[nm] = (name, wind or "LV")
    return out


def mpt_loads(data):
    """The units' MVA on each MPT winding, from the Generators rows (through a shared collector, or another
       generator's bus, to where it connects) -> {MPT name (upper): {"lv": MVA, "tv": MVA, "gens": [(generator,
       its units' MVA, "LV" or "TV")]}} -- a generator whose row does not read yet, or on no MPT, is left out."""
    on, out = mpt_of(data), {}
    for r in _rows(data, "generators"):
        nm = _txt(r.get("name"))
        if not nm or nm.upper() not in on:
            continue
        try:
            n, _p1, _p0, _pf, _q1, _q0, s1 = unit_rating(r, nm)
        except InputError:
            continue
        name, wind = on[nm.upper()]
        e = out.setdefault(name, {"lv": 0.0, "tv": 0.0, "gens": []})
        e["tv" if wind == "TV" else "lv"] += n * s1
        e["gens"].append((nm, n * s1, wind))
    return out


def _feeders_of(rows):
    """One collector's Feeders rows -> {feeder name: its rows} in order (a blank Feeder: the row above's)."""
    out, spelt, fname = collections.OrderedDict(), {}, "F1"
    for r in rows:
        f = _txt(r.get("feeder"))
        if f:
            fname = spelt.setdefault(f.upper(), f)
        out.setdefault(fname, []).append(r)
    return out


def _cell_num(r, k, default=0.0):
    """A cell as a number, for what the GUI shows live: default where it is blank or does not read."""
    try:
        v = _num(r, k, "", None)
    except InputError:
        return default
    return default if v is None else v


def feeder_layout(data):
    """Each collector's feeders as the Feeders rows have them -> {collector (upper): [(feeder, copies, units on
       one)]} -- copies: the largest given on its rows (blank 1)."""
    rows = collections.OrderedDict()
    for r in _rows(data, "feeders"):
        c = _txt(r.get("collector"))
        if c:
            rows.setdefault(c.upper(), []).append(r)
    out = {}
    for c, rs in rows.items():
        out[c] = [(f, max([int(_cell_num(r, "copies", 1)) for r in frows] + [1]),
                   int(sum(_cell_num(r, "units") for r in frows))) for f, frows in _feeders_of(rs).items()]
    return out


def feeders_on_mpts(data):
    """The feeders on each MPT -> {MPT name (upper): [(generator or shared collector, its feeders, its units on
       them, shared)]} -- a generator's own collector and the shared collectors on it."""
    on, lay, out = mpt_of(data), feeder_layout(data), collections.OrderedDict()
    shared = set((_txt(c.get("name")) or "").upper() for c in _rows(data, "collectors"))
    names = [(_txt(r.get("name")), False) for r in _rows(data, "generators")] + \
        [(_txt(c.get("name")), True) for c in _rows(data, "collectors")]
    for nm, sh in names:
        if not nm or nm.upper() not in on or nm.upper() not in lay or (not sh and nm.upper() in shared):
            continue
        fs = lay[nm.upper()]
        out.setdefault(on[nm.upper()][0], []).append((nm, sum(k for _f, k, _u in fs), sum(k * u for _f, k, u in fs),
                                                      sh))
    return out


def feeders_needed(data, gen):
    """A generator's own feeders against its Units -> (feeders now, the units on them, the most on one, the
       feeders its Units need at that many on each) -- None: no feeders of its own, or its row does not read."""
    gr = next((r for r in _rows(data, "generators") if (_txt(r.get("name")) or "").upper() == gen.upper()), None)
    fs = feeder_layout(data).get(gen.upper())
    shared = set((_txt(c.get("name")) or "").upper() for c in _rows(data, "collectors"))
    if gr is None or not fs or gen.upper() in shared:
        return None
    try:
        units = _int(gr, "units", gen, required=True, minimum=1)
    except InputError:
        return None
    most = max(u for _f, _c, u in fs)
    return (sum(c for _f, c, _u in fs), sum(c * u for _f, c, u in fs), most,
            min(units, int(math.ceil(units / float(most) - 1e-9))) if most > 0 else min(units, 1))


def set_feeders(data, gen, n=None):
    """Generator gen's own collector reshaped, its units on it -> (the Feeders rows as they would be, what that is).
       Its feeders must be alike: each one's sections those of its fullest feeder (the design: the most units on
       one), the same cables and lengths -- a shorter one: its first sections.
         n given: n feeders, Units / n on each -- where it does not divide, one more on the first ones (a second
           group); on each feeder over the sections the design has units on, one more on the nearest.
         n None: its Units follow the design -- as many full feeders as it has units for, each as designed (only
           its Copies change), and the units left over on one more feeder; fewer units than one feeder has: an
           error (the design would be lost -- n given reshapes it).
       A feeder's sections with no units at them or beyond are not needed: left out, said."""
    gr = next((r for r in _rows(data, "generators") if (_txt(r.get("name")) or "").upper() == gen.upper()), None)
    if gr is None:
        raise InputError("Feeders: no generator %s" % gen)
    nm = _txt(gr.get("name"))
    units = _int(gr, "units", "Generators (%s)" % nm, required=True, minimum=1)
    rows = list(data.get("feeders") or [])
    cols = [c[0] for c in FEEDER_COLS]
    mine = [i for i, r in enumerate(rows) if (_txt(r.get("collector")) or "").upper() == nm.upper() and
            not _skip([r.get(c) for c in cols])]
    if not mine:
        raise InputError("%s: no Feeders rows -- give its first feeder's sections on the Feeders page" % nm)
    feeders = _feeders_of([rows[i] for i in mine])
    look = lambda r: tuple(_txt(r.get(k)) or "" for k in ("section", "from_section", "cable", "length", "unit",
                                                          "parallel", "r_ohm", "x_ohm", "b_us"))
    name = max(feeders, key=lambda f: (sum(_cell_num(r, "units") for r in feeders[f]), len(feeders[f])))
    first = feeders[name]
    if any(not set(look(r) for r in f) <= set(look(r) for r in first) for f in feeders.values()):
        raise InputError("%s: its feeders %s are not alike (their sections, cables or lengths differ) -- set their "
                         "Copies on the Feeders page" % (nm, ", ".join(feeders)))
    other = next((f for f in feeders if f != name), name + "_2")
    at = [j for j, r in enumerate(first) if _cell_num(r, "units") > 0] or [len(first) - 1]
    nums, up = [], {}                                # each section's: where it starts (its From section)
    for j, r in enumerate(first):
        num = int(_cell_num(r, "section", (nums[-1] if nums else 0) + 1))
        fr = _cell_num(r, "from_section", None)
        up[j] = int(fr) if fr is not None else max([x for x in nums if x < num] or [0])
        nums.append(num)
    spread = lambda u: dict((j, u // len(at) + (1 if q < u % len(at) else 0)) for q, j in enumerate(at))
    if n is None:                                    # the design kept: its full feeders, the rest on one more
        each = int(sum(_cell_num(r, "units") for r in first)) or units
        full, left = divmod(units, each)
        if not full:
            raise InputError("%s: Units %d is fewer than the %d units on one of its feeders -- its feeders stay as "
                             "they are: Feeders on each MPT (MPT or Feeders page) sets them" % (nm, units, each))
        groups = [(name, full, each, dict((j, int(_cell_num(r, "units"))) for j, r in enumerate(first)))] + \
            ([(other, 1, left, spread(left))] if left else [])
        n = full + (1 if left else 0)
    else:
        n = int(n)
        if not 1 <= n <= units:
            raise InputError("%s: %d feeders for its %d units -- 1 to %d" % (nm, n, units, units))
        per, extra = divmod(units, n)
        groups = [(name, extra, per + 1, spread(per + 1)), (other, n - extra, per, spread(per))] if extra else \
            [(name, n, per, spread(per))]
    new, said = [], []
    for fname, cps, u, on in groups:
        down = dict((j, on.get(j, 0)) for j in range(len(first)))      # the units at and beyond each section
        for j in reversed(range(len(first))):
            if up[j] in nums:
                down[nums.index(up[j])] += down[j]
        cut = [nums[j] for j in range(len(first)) if not down[j]]
        for j, r in enumerate(first):
            if not down[j]:                          # no units at it or beyond: no cable there
                continue
            d = dict(r, feeder=fname, copies=str(cps))
            if on.get(j):
                d["units"] = str(on[j])
            else:
                d.pop("units", None)
            new.append(d)
        said.append("%d x %s: %d units each (%s on its sections%s)" % (
            cps, fname, u, ", ".join(str(on[j]) for j in sorted(on) if on[j]),
            "; section%s %s not needed" % ("" if len(cut) == 1 else "s", ", ".join(str(x) for x in cut))
            if cut else ""))
    mine = set(mine)
    k = min(mine)
    out = [r for i, r in enumerate(rows[:k]) if i not in mine] + new + [r for i, r in enumerate(rows) if i > k and
                                                                      i not in mine]
    return out, "%s: %d feeder%s -- %s" % (nm, n, "" if n == 1 else "s", "; ".join(said))


def gsu_split(r, w, n):
    """A Generators row's GSUs for its n units -> (GSU count, the units on one GSU): GSU count blank = Units /
       Units per GSU, rounded up (that blank too: 1, a GSU per unit); a GSU count given: the units shared by its
       GSUs (Units per GSU only checks it)."""
    per = _int(r, "gsu_units", w, None, minimum=1)
    cnt = _int(r, "gsu_count", w, None, minimum=1)
    if cnt is None:
        per = min(per or 1, n)
        return int(math.ceil(n / float(per) - 1e-9)), per
    return cnt, n / float(cnt)


def gsu_sizing(r, per):
    """A Generators row's GSUs with `per` of its units on each -> (GSU count, the units' MVA on one GSU, a GSU
       rating for them: that MVA rounded up, or the GSU MVA given where it carries them without being twice their
       MVA)."""
    w = _txt(r.get("name")) or "a generator"
    n, _p1, _p0, _pf, _q1, _q0, s1 = unit_rating(r, w)
    per = max(1, min(int(per), n))
    load = per * s1
    have = _num(r, "gsu_mva", w, None, positive=True)
    keep = have if have and load <= have * 1.02 and have <= 2.0 * load else None
    return int(math.ceil(n / float(per) - 1e-9)), round(load, 6), keep or nice_mva(load)


def gsu_per_now(r):
    """The units on one GSU as a Generators row has them now (for the GUI to start from): Units per GSU, else
       Units / GSU count, else what one GSU's MVA carries, else 1."""
    w = _txt(r.get("name")) or "a generator"
    n, _p1, _p0, _pf, _q1, _q0, s1 = unit_rating(r, w)
    per = _int(r, "gsu_units", w, None, minimum=1)
    cnt = _int(r, "gsu_count", w, None, minimum=1)
    have = _num(r, "gsu_mva", w, None, positive=True)
    if per:
        return min(per, n)
    if cnt:
        return max(1, min(n, int(math.ceil(n / float(cnt) - 1e-9))))
    if have:
        return max(1, min(n, int(have / s1 + 1e-9)))
    return 1


def unit_totals(r):
    """What a Generators row's units add up to, as the build takes them: one line (the GUI shows it live)."""
    w = _txt(r.get("name")) or "a generator"
    try:
        n, pmax, pmin, _pf, qmax, qmin, s = unit_rating(r, w)
        d = _num(r, "dispatch_mw", w)
    except InputError as e:
        return "%s: %s" % (w, str(e).split(": ", 1)[-1])
    f = lambda x: "%.6g" % (x + 0.0)
    txt = "%s: %d x %s MVA = %s MVA, P %s to %s MW, Q %s to %s MVAr" % (
        w, n, f(s), f(n * s), f(n * pmin), f(n * pmax), f(n * qmin), f(n * qmax))
    try:
        gm = _num(r, "gsu_mva", w, None, positive=True)
        cnt, _per = gsu_split(r, w, n)
    except InputError:
        gm = None
    if gm:
        txt += ", GSU %d x %s = %s MVA" % (cnt, f(gm), f(cnt * gm))
    return txt + (", dispatch %s MW" % f(d) if d is not None else ", dispatch %s MW (Pmax)" % f(n * pmax))


def gen_data(r):
    w = r["_where"]
    name = _txt(r.get("name"))
    if not name:
        raise InputError("%s: Name is missing" % w)
    if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
        raise InputError("%s: generator name %r: letters, digits and _ only, starting with a letter" % (w, name))
    if name.upper() in ("IB", "PLANT"):
        raise InputError("%s: %s is a reserved name (IB: the infinite bus, PLANT: the plant controller)" % (w, name))
    w = "%s (%s)" % (w, name)
    typ = _choice(r, "type", w, GEN_TYPES, None)
    if typ is None:
        raise InputError("%s: Type is missing (%s)" % (w, ", ".join(GEN_TYPES)))
    reec = _choice(r, "reec", w, ["REEC_A", "REEC_C", "REEC_D"], None)
    if typ in REEC_OK:
        reec = reec or REEC_OK[typ][0]
        if reec not in REEC_OK[typ]:
            raise InputError("%s: Electrical control %s -- a %s generator takes %s" % (w, reec, typ,
                                                                                    " or ".join(REEC_OK[typ])))
    elif reec:
        raise InputError("%s: Electrical control %s -- only inverters take one (PV, BESS, WT4, WT3)" % (w, reec))
    sync = tuple(_choice(r, k, w, ok, None) for k, ok in (("sync_machine", SYNC_MACHINES),
                                                          ("sync_exciter", SYNC_EXCITERS),
                                                          ("sync_governor", SYNC_GOVERNORS)))
    if typ == "SYNC":
        sync = tuple(v or ok[0] for v, ok in zip(sync, (SYNC_MACHINES, SYNC_EXCITERS, SYNC_GOVERNORS)))
    elif any(sync):
        raise InputError("%s: %s -- only a synchronous machine (Type SYNC) takes one" % (w, ", ".join(
            "%s %s" % (_label(k, w), v) for k, v in zip(("sync_machine", "sync_exciter", "sync_governor"), sync) if v)))
    regc = _choice(r, "regc", w, ["REGC_A", "REGC_B"], None)
    if typ in REGC_OK:
        regc = regc or "REGC_A"
        if regc not in REGC_OK[typ]:
            raise InputError("%s: Converter model %s -- a %s generator takes %s" % (w, regc, typ,
                                                                                 " or ".join(REGC_OK[typ])))
    elif regc:
        raise InputError("%s: Converter model %s -- only inverters take one (PV, BESS, WT4, WT3)" % (w, regc))
    n, pmax, pmin, pf, qmax, qmin, s = unit_rating(r, w)
    g = {"name": name, "type": typ, "reec": reec, "regc": regc, "sync": sync, "id": (_txt(r.get("id")) or "1")[:2],
         "row": r,
         "where": w, "n": n,
         "unit": {"mva": s, "pmax": pmax, "pmin": pmin, "qmax": qmax, "qmin": qmin, "pf": pf},
         "mbase": n * s, "pmax": n * pmax, "pmin": n * pmin, "qmax": n * qmax, "qmin": n * qmin,
         "q_mode": _choice(r, "q_mode", w, ["share", "fixed"], "share"),
         "zsorce_x": _num(r, "zsorce_x", w), "wmod": _int(r, "wmod", w, None),
         "pscad": {"library": _txt(r.get("pscad_library")), "definition": _txt(r.get("pscad_definition")),
                   "ac_port": _txt(r.get("pscad_ac_port")), "at": _choice(r, "pscad_inv_at", w, ["LV", "MV"], "LV"),
                   "source": _txt(r.get("pscad_source")), "iid": _int(r, "pscad_component_id", w, None, minimum=1)}}
    ps = g["pscad"]
    if ps["source"] and not (ps["iid"] or ps["definition"]):
        raise InputError("%s: %s needs the UDM's %s (or %s) -- GUI: Generators, Pick UDM from OEM model" % (
            w, _label("pscad_source"), _label("pscad_definition"), _label("pscad_component_id")))
    if ps["iid"] and not ps["source"]:
        raise InputError("%s: %s needs the %s it is in" % (w, _label("pscad_component_id"), _label("pscad_source")))
    d = _num(r, "dispatch_mw", w)
    g["dispatch"] = g["pmax"] if d is None else d
    if g["dispatch"] > g["pmax"] + 1e-6 or g["dispatch"] < g["pmin"] - 1e-6:
        raise InputError("%s: Dispatch MW %.2f is outside Pmin..Pmax (%.2f .. %.2f MW)"
                         % (w, g["dispatch"], g["pmin"], g["pmax"]))
    if g["q_mode"] == "fixed":
        q = _num(r, "fixed_q_mvar", w)
        fpf = _num(r, "fixed_pf", w)
        if q is None and fpf is not None:
            if not 0.0 < abs(fpf) <= 1.0:
                raise InputError("%s: %s %g must be between 0 and 1 (+ lagging, - leading)" % (
                    w, _label("fixed_pf", w), fpf))
            q = ("pf", fpf)
        g["q_fixed"] = q if q is not None else 0.0
    if pmax > s + 1e-9:
        raise InputError("%s: %s %.3f MW is above %s %.3f" % (w, _label("unit_pmax_mw", w), pmax,
                                                              _label("unit_mva", w), s))
    return g


def build_generator(net, g, feeders, cables, unit_mw, used, log):
    r, w, name = g["row"], g["where"], g["name"]
    src = g["pscad"]["source"]
    if src and os.path.splitext(src)[1] and not os.path.isfile(src):
        net.notes.append("CHECK: %s: %s %s is not on this PC -- the PSCAD build looks for it there" % (
            w, _label("pscad_source"), src))
    udm_ports_check(g, net.notes)
    at = net.find(_txt(r.get("connect")), w + " Connect to")
    g["connect"] = at
    rows = feeders.get(name.upper())
    mode = _choice(r, "collector", w, ["FEEDERS", "EQUIVALENT", "NONE"], None)
    if mode is None:
        mode = "FEEDERS" if rows else ("EQUIVALENT" if _txt(r.get("coll_x")) or _txt(r.get("coll_r")) else "NONE")
    if mode != "EQUIVALENT":                         # what this row gives that its model does not use: said
        _unused(net.notes, r, ("coll_r", "coll_x", "coll_b", "coll_units"), w,
                "Collector %s (they are for EQUIVALENT)" % mode, {"coll_units": "pu"})
    if _txt(r.get("unit_qmax_mvar")) is not None:
        _unused(net.notes, r, ("unit_pf",), w, "'Unit Qmax' is given (Unit PF sets Qmax where it is blank)",
                {"unit_pf": 0.95})
    if g["type"] == "BESS" and _txt(r.get("unit_pmin_mw")) is None:
        net.notes.append("CHECK: %s: %s blank (0) -- this BESS does not charge: give its charging limit (-Pmax ...) "
                         "if it does" % (w, _label("unit_pmin_mw", w)))
    if _txt(r.get("unit_qmax_mvar")) is None and _txt(r.get("unit_pf")) is None and _txt(r.get("unit_mva")) and \
            g["unit"]["qmax"] < 0.05 * g["unit"]["mva"]:   # the MVA circle leaves (next to) no Q at full P
        net.notes.append("CHECK: %s: Unit MVA %s and P %s leave %.3g MVAr at full P (Unit Qmax blank: sqrt(MVA^2 - "
                         "P^2)) -- give Unit Qmax, a larger Unit MVA, or Unit PF" % (
                             w, _g(g["unit"]["mva"]), _g(max(abs(g["unit"]["pmax"]), abs(g["unit"]["pmin"]))),
                             g["unit"]["qmax"]))
    if g["unit"]["qmax"] < 0:                        # given so, with its Qmin below it: Q taken only
        net.notes.append("CHECK: %s: %s %g is below 0 -- this unit only takes Q (%g .. %g MVAr)" % (
            w, _label("unit_qmax_mvar", w), g["unit"]["qmax"], g["unit"]["qmin"], g["unit"]["qmax"]))
    if g["q_mode"] == "share":
        _unused(net.notes, r, ("fixed_q_mvar", "fixed_pf"), w, "Q mode share (they are for Q mode fixed)")
    elif _txt(r.get("fixed_q_mvar")) is not None:
        _unused(net.notes, r, ("fixed_pf",), w, "'Fixed Q MVAr' is given")
    if not (g["pscad"]["definition"] or g["pscad"]["iid"]):
        _unused(net.notes, r, ("pscad_ac_port", "pscad_library"), w,
                "no vendor model (UDM): 'PSCAD definition' is blank")
    kv = net.kv(at)
    mv = name + ":MV"
    if mode == "FEEDERS":
        if not rows:
            raise InputError("%s: Collector FEEDERS but Feeders has no rows for %s" % (w, name))
        used.add(name.upper())
        res = wecc_collector(name, rows, cables, kv, net.sb, net.f, unit_mw, name, net.notes.append)
        res.update({"root": at, "own": name, "end": mv})
        net.colls[name] = res
        line = {"r": res["r"], "x": res["x"], "b": res["b"], "R": res["R"], "X": res["X"], "B": res["B"],
                "count": 1, "rate": res["rates"][0], "rate_b": res["rates"][1], "rate_c": res["rates"][2], "kv": kv,
                "km": 0.0, "w_mw": res["W"]}
    elif mode == "EQUIVALENT":
        if rows:
            raise InputError("%s: Collector EQUIVALENT, but Feeders has rows for %s" % (w, name))
        units = _choice(r, "coll_units", w, ["pu", "ohm"], "pu")
        zb = kv * kv / net.sb
        rr, xx, bb = _num(r, "coll_r", w, 0.0), _num(r, "coll_x", w, required=True), _num(r, "coll_b", w, 0.0)
        if units == "pu":
            line = {"r": rr, "x": xx, "b": bb, "R": rr * zb, "X": xx * zb, "B": bb / zb * 1e6}
        else:
            line = {"r": rr / zb, "x": xx / zb, "b": bb * 1e-6 * zb, "R": rr, "X": xx, "B": bb}
        line.update({"count": 1, "rate": 0.0, "kv": kv, "km": 0.0, "w_mw": max(abs(g["pmax"]), abs(g["pmin"]))})
        if abs(complex(line["r"], line["x"])) < 1e-9:
            raise InputError("%s: the collector equivalent has no impedance" % w)
    else:
        if rows:
            raise InputError("%s: Collector NONE, but Feeders has rows for %s" % (w, name))
        line = None
    if line:
        net.add_bus(mv, kv, w)
        add_line(net, at, mv, line, name + "-COLL", "COLLECTOR", w)
    else:
        net.add_alias(mv, at, w)
    gsu_mva = _num(r, "gsu_mva", w, None, positive=True)
    if gsu_mva:
        kv1 = _num(r, "gsu_hv_kv", w, kv, positive=True)
        kv2 = _num(r, "gsu_lv_kv", w, None, positive=True) or _num(r, "kv", w, required=True, positive=True)
        if abs(kv1 / kv - 1.0) > KV_OFF:
            raise InputError("%s: 'GSU HV kV' %.4g kV, but the bus it connects to (%s) is %.4g kV -- the voltages do "
                             "not match" % (w, kv1, _txt(r.get("connect")) or "its collector", kv))
        kvt0 = _num(r, "kv", w, None, positive=True)
        if kvt0 and abs(kvt0 / kv2 - 1.0) > KV_OFF:
            raise InputError("%s: 'Terminal kV' %.4g kV, but 'GSU LV kV' is %.4g kV -- the voltages do not match"
                             % (w, kvt0, kv2))
        cnt, per = gsu_split(r, w, g["n"])
        x_r = _num(r, "gsu_x_r", w)
        if x_r is None:
            x_r = 8.0
            net.notes.append("CHECK: %s: GSU X/R not given -- 8 used" % w)
        lv = net.add_bus(name + ":LV", kv2, w, aliases=(name,))
        ra, rb, rc = _abc(r, ("gsu_rate_a_mva", "gsu_rate_b_mva", "gsu_rate_c_mva"), w, gsu_mva, net.notes)
        g["gsu"] = add_tx2(net, {"name": name + "-GSU", "kind": "GSU", "hv": net.alias[mv.upper()], "lv": lv,
                                  "mva": cnt * gsu_mva, "unit_mva": gsu_mva, "units": cnt,
                                  "z_pct": _num(r, "gsu_z_pct", w, required=True, positive=True), "x_r": x_r,
                                  "load_loss_kw": None, "kv1": kv1, "kv2": kv2,
                                  "tap": _num(r, "gsu_tap", w, 1.0, positive=True), "vector": _txt(r.get("gsu_vector")),
                                  "nll_pct": 0.0, "im_pct": 0.0, "rate": cnt * ra, "rate_b": cnt * rb,
                                  "rate_c": cnt * rc, "count": 1, "where": w})
        given = _int(r, "gsu_units", w, None, minimum=1)
        if given is not None and _txt(r.get("gsu_count")) is not None:
            need = int(math.ceil(g["n"] / float(min(given, g["n"])) - 1e-9))
            if need != cnt:
                net.notes.append("CHECK: %s: 'GSU count' %d, but 'Units per GSU' %d puts its %d units on %d GSUs -- "
                                 "the GSU count is used (give one of the two)" % (w, cnt, given, g["n"], need))
        fm = lambda x: "%.6g" % x
        each = per * g["unit"]["mva"]                  # the units' MVA on one GSU (a GSU count given: on average)
        on = ("one unit of %s MVA" % fm(each)) if per == 1 else "%s units of %s MVA = %s MVA%s" % (
            fm(per), fm(g["unit"]["mva"]), fm(each), "" if per == int(per) else " on average")
        everything = "%d GSUs, %s MVA, for units of %s MVA in all" % (cnt, fm(cnt * gsu_mva), fm(g["mbase"]))
        if each > gsu_mva * 1.02:
            net.notes.append("CHECK: %s: each GSU carries %s, above its %s MVA (%s)" % (w, on, fm(gsu_mva), everything))
        elif gsu_mva > 2.0 * each:                     # their impedance on their MVA: far too stiff a GSU
            blank = _txt(r.get("gsu_count")) is None and given is None
            net.notes.append("CHECK: %s: GSU %s MVA for %s on each (%s%s) -- its Z %% is on its MVA: give %s" % (
                w, fm(gsu_mva), on, everything, "; 'GSU count' and 'Units per GSU' blank: a GSU per unit" if blank
                else "", "'Units per GSU', the units on one GSU (GUI: GSUs page, Size GSUs from the units)" if blank
                else "the GSUs' count and MVA as they are"))
    else:
        _unused(net.notes, r, [c[0] for c in GEN_COLS if c[0].startswith("gsu_") and c[0] != "gsu_mva"], w,
                "no GSU: 'GSU MVA' is blank (its units on the MV bus)", {"gsu_tap": 1.0})
        g["gsu"] = None
        lv = net.alias[mv.upper()]
        net.add_alias(name + ":LV", lv, w)
        net.add_alias(name, lv, w)
    g["bus"] = net.alias[(name + ":LV").upper()]
    g["mv"] = net.alias[mv.upper()]
    kvt = _num(r, "kv", w, None, positive=True)
    if kvt and abs(kvt / net.kv(g["bus"]) - 1.0) > 0.02:
        net.notes.append("CHECK: %s: Terminal kV %.4g, its bus %.4g kV (the bus base wins)" % (w, kvt, net.kv(g["bus"])))
    if any(x["bus"] == g["bus"] for x in net.gens):
        raise InputError("%s: two generators on one terminal bus -- give each its own GSU" % w)
    net.gens.append(g)


def _drop_bus(net, role, to):
    """Bus `role` goes: every name that stood for it now stands for `to` -> those names."""
    names = [a for a, r in net.alias.items() if r == role]
    for a in names:
        net.alias[a] = to
    del net.bus[role]
    return names


def one_feeder_per_generator(net):
    """SMIB: each generator its own collector equivalent from its MPT winding. The equivalents that start at one
       bus and carry the same generators' units (two collector systems of one generator) become one: the WECC
       equivalent of them all, each weighed by the MW it carries -- Z = sum(Zc Wc^2) / W^2, B = sum(Bc) -- ending
       where the first one with a GSU on it ends."""
    terminals = set(g["bus"] for g in net.gens)
    gsu_at = set(g["mv"] for g in net.gens if g["mv"] != g["bus"])
    ends = dict((res["end"], tuple(sorted(G for G, k in res["N"].items() if k))) for res in net.colls.values())
    groups = collections.OrderedDict()
    for ln in net.lines:
        if ln["kind"] == "COLLECTOR" and ln["to"] not in terminals:
            who = ends.get(ln["to"]) or tuple(sorted(g["name"].upper() for g in net.gens if g["mv"] == ln["to"]))
            groups.setdefault((ln["fr"], who or (ln["name"],)), []).append(ln)
    for (root, who), lns in groups.items():
        W = sum(ln["w_mw"] for ln in lns)
        if len(lns) < 2 or not W > 0:
            continue
        end = next((ln["to"] for ln in lns if ln["to"] in gsu_at), lns[0]["to"])
        z = sum((complex(ln["r"], ln["x"]) * ln["w_mw"] ** 2 for ln in lns), 0j) / W ** 2
        Z = sum((complex(ln["R"], ln["X"]) * ln["w_mw"] ** 2 for ln in lns), 0j) / W ** 2
        gone = [ln["to"] for ln in lns if ln["to"] != end]
        for e in gone:
            _drop_bus(net, e, end)
        for t in net.tx2 + net.tx3:
            for k in ("hv", "lv", "tv"):
                if t.get(k) in gone:
                    t[k] = end
        for g in net.gens:
            if g["mv"] in gone:
                g["mv"] = end
        for res in net.colls.values():
            if res.get("end") in gone:
                res["end"] = end
        k = net.lines.index(lns[0])
        net.lines = [ln for ln in net.lines if not any(ln is x for x in lns)]
        real = dict((g.get("family", g["name"]).upper(), g.get("family", g["name"])) for g in net.gens)
        name = "_".join(real.get(n, n) for n in who) + "-COLL"
        if any(ln["name"].upper() == name.upper() for ln in net.lines):
            name = _node(root) + "_" + name
        ra, rb, rc = _sum_rates([_rabc(ln) for ln in lns])
        merged = {"r": z.real, "x": z.imag, "b": sum(ln["b"] for ln in lns), "R": Z.real, "X": Z.imag,
                  "B": sum(ln["B"] for ln in lns), "count": 1, "rate": ra, "rate_b": rb, "rate_c": rc,
                  "kv": net.kv(root), "km": 0.0, "w_mw": W,
                  "fr": root, "to": end, "name": name, "kind": "COLLECTOR", "where": "Plant (SMIB)",
                  "of": [(ln["name"], ln["w_mw"]) for ln in lns]}
        net.lines.insert(k, merged)
        net.merged.append(merged)


def expand_collectors(net):
    """DETAILED: every collector from Feeders, section by section (identical feeder copies lumped); the units of
       a generator at each node a machine of their own (its generator's unit data, P and Q in proportion) behind
       a GSU of its own (its generator's, scaled to them)."""
    gby = collections.OrderedDict((g["name"].upper(), g) for g in net.gens)
    carriers = collections.OrderedDict()                 # GENERATOR: the collectors with its units
    for cname, res in net.colls.items():
        for G, k in res["N"].items():
            if k and G in gby:
                carriers.setdefault(G, []).append(cname)
    line_of, why = {}, collections.OrderedDict()
    for cname, res in net.colls.items():
        line_of[cname] = next((ln for ln in net.lines if ln["kind"] == "COLLECTOR" and ln["to"] == res["end"]), None)
        hang = [c for c, r in net.colls.items() if r["root"] == res["end"]] + [
            g["name"] for g in net.gens if g["connect"] == res["end"] and g["name"].upper() not in res["N"]]
        if line_of[cname] is None:
            why[cname] = "no line"
        elif hang:
            why[cname] = "%s start%s at its far end" % (", ".join(hang), "s" if len(hang) == 1 else "")
        else:
            for G in carriers:
                g = gby[G]
                if cname in carriers[G] and not g["gsu"]:
                    why[cname] = "%s has no GSU (its units are its terminal)" % g["name"]
                elif cname in carriers[G] and g["mv"] not in [net.colls[c]["end"] for c in carriers[G]]:
                    why[cname] = "%s is not on it (its GSU is at %s)" % (g["name"], g["mv"])
    while True:                                          # a generator stays whole: all its collectors, or none
        more = [c for G, cs in carriers.items() if any(x in why for x in cs) for c in cs if c not in why]
        if not more:
            break
        for c in more:
            why[c] = "its generators' other collectors stay equivalents"
    for cname in why:
        net.notes.append("NOTE: DETAILED: collector %s stays its equivalent -- %s" % (cname, why[cname]))
    slots = collections.OrderedDict()
    for cname, res in list(net.colls.items()):
        if cname in why:
            continue
        end, root = res["end"], res["root"]
        line = line_of[cname]
        mine = [gby[G] for G in res["N"] if G in carriers and cname in carriers[G]]
        kv = res["kv"]
        w = "Feeders (collector %s)" % cname
        net.lines.remove(line)
        nodes, alike = {}, {}
        for fd in res["feeders"]:
            other = alike.setdefault(_node(fd["name"]).upper(), fd["name"])
            if other != fd["name"]:
                raise InputError("%s: feeders %r and %r give the same bus names (%s_%s_S1 ...) -- rename one"
                                 % (w, other, fd["name"], _node(cname), _node(fd["name"])))
            for sc in fd["sections"]:
                nodes[(fd["name"], sc["num"])] = net.add_bus("%s_%s_S%d" % (_node(cname), _node(fd["name"]),
                                                                            sc["num"]), kv, w)
        for g in mine:
            slots.setdefault(g["name"], [])
        for fd in res["feeders"]:
            n = fd["copies"]
            for sc in fd["sections"]:
                a = root if sc["from"] == 0 else nodes[(fd["name"], sc["from"])]
                b = nodes[(fd["name"], sc["num"])]
                add_line(net, a, b, {"r": sc["z"].real / n, "x": sc["z"].imag / n, "b": sc["b"] * n,
                                     "R": sc["R"] / n, "X": sc["X"] / n, "B": sc["B"] * n, "count": 1,
                                     "rate": sc["rates"][0] * n, "rate_b": sc["rates"][1] * n,
                                     "rate_c": sc["rates"][2] * n, "kv": kv, "km": sc["length"] * LEN_KM[sc["unit"]],
                                     "copies": n}, b, "SECTION", sc["where"])
                for G, cnt in sc["units"].items():
                    g = gby[G]
                    pre = _node(g["name"]) + ("_" + _node(cname) if len(carriers[G]) > 1 else "")
                    slots[g["name"]].append(_unit_group(net, g, "%s_%s_S%d" % (pre, _node(fd["name"]), sc["num"]),
                                                        cnt * n, b, sc["where"]))
        net.moved.update((a, "%s (collector %s)" % (root, cname)) for a in _drop_bus(net, end, root))
        res["end"] = None
    for name, groups in slots.items():                   # each generator: its unit groups in its place
        g = gby[name.upper()]
        root = net.colls[carriers[name.upper()][0]]["root"]
        net.tx2.remove(g["gsu"])
        net.moved.update((a, "%s (%s)" % (root, g["name"])) for a in _drop_bus(net, g["bus"], root))
        k = net.gens.index(g)
        net.gens[k:k + 1] = groups
    seen = set()
    for g in net.gens:
        if g["name"].upper() in seen:
            raise InputError("%s: DETAILED: two machines are named %s -- rename a generator or feeder"
                             % (g["where"], g["name"]))
        seen.add(g["name"].upper())


def machines(net, rows):
    """Machines: a DETAILED machine's own Pgen, Pmax, Pmin, Qmax, Qmin, fixed Q, MBASE and ID in place of its share
       of its generator's."""
    if not rows:
        return
    if net.model != "DETAILED":
        net.notes.append("NOTE: Machines: %d row(s) not used -- they are for the DETAILED model (Plant: Model type)"
                         % len(rows))
        return
    by = collections.OrderedDict((g["name"].upper(), g) for g in net.gens if g.get("family"))
    seen = set()
    for r in rows:
        w = r["_where"]
        nm = _txt(r.get("machine")) or ""
        g = by.get(nm.upper())
        if g is None:
            names = [x["name"] for x in by.values()]
            raise InputError("%s: no machine %s in the DETAILED model -- its machines: %s" % (
                w, nm, ", ".join(names[:12]) + (" ... (%d)" % len(names) if len(names) > 12 else "") or "none"))
        if nm.upper() in seen:
            raise InputError("%s: machine %s is in Machines twice" % (w, g["name"]))
        seen.add(nm.upper())
        done = []
        for key, fld, what in (("pmax_mw", "pmax", "Pmax"), ("pmin_mw", "pmin", "Pmin"), ("qmax_mvar", "qmax", "Qmax"),
                               ("qmin_mvar", "qmin", "Qmin"), ("pgen_mw", "dispatch", "Pgen")):
            v = _num(r, key, w)
            if v is not None:
                g[fld] = v
                done.append("%s %s" % (what, _g(v)))
        v = _num(r, "mbase_mva", w, None, positive=True)
        if v is not None:
            g["mbase"] = v
            done.append("MBASE %s" % _g(v))
        v = _num(r, "q_mvar", w)
        if v is not None:
            g["q_mode"], g["q_fixed"] = "fixed", v
            done.append("Q %s fixed" % _g(v))
        v = _txt(r.get("id"))
        if v:
            g["id"] = v[:2]
            done.append("ID %s" % g["id"])
        for lo, hi, what in (("pmin", "pmax", "P"), ("qmin", "qmax", "Q")):
            if g[lo] > g[hi] + 1e-9:
                blank = [x for x in (lo, hi) if _txt(r.get(x + ("_mw" if what == "P" else "_mvar"))) is None]
                raise InputError("%s: %s %smin %s is above its %smax %s%s" % (
                    w, g["name"], what, _g(g[lo]), what, _g(g[hi]), (" (%s blank: its units x its generator's "
                                                                     "Unit %s)" % (" and ".join(
                                                                         x.capitalize() for x in blank), ", Unit ".join(
                                                                         x.capitalize() for x in blank))) if blank
                    else ""))
        if g["dispatch"] > g["pmax"] + 1e-6 or g["dispatch"] < g["pmin"] - 1e-6:
            raise InputError("%s: %s Pgen %.3f MW is outside Pmin..Pmax (%.3f .. %.3f MW)"
                             % (w, g["name"], g["dispatch"], g["pmin"], g["pmax"]))
        if g["mbase"] < max(abs(g["pmax"]), abs(g["pmin"])) - 1e-6:
            net.notes.append("CHECK: %s: %s MBASE %s MVA is below its P range (%s .. %s MW)" % (
                w, g["name"], _g(g["mbase"]), _g(g["pmin"]), _g(g["pmax"])))
        if done:
            g["where"] += " (Machines: %s)" % ", ".join(done)
            net.notes.append("NOTE: %s: %s -- %s" % (w, g["name"], ", ".join(done)))


def _unit_group(net, g, name, units, at, where):
    """The units of generator g at one collector node: a machine of g's kind with g's unit data, P (and fixed Q)
       in proportion, behind its own GSU (g's, scaled to them)."""
    k = float(units) / g["n"]
    u = dict(g)
    u.update({"name": name, "family": g["name"], "n": units, "mbase": units * g["unit"]["mva"],
              "pmax": units * g["unit"]["pmax"], "pmin": units * g["unit"]["pmin"],
              "qmax": units * g["unit"]["qmax"], "qmin": units * g["unit"]["qmin"], "dispatch": g["dispatch"] * k,
              "connect": at, "mv": at, "where": "%s, %s" % (g["where"], name)})
    if g["q_mode"] == "fixed" and not isinstance(g["q_fixed"], tuple):
        u["q_fixed"] = g["q_fixed"] * k
    if g["gsu"]:
        lv = net.add_bus(name + ":LV", net.kv(g["bus"]), where)
        d = dict((key, g["gsu"].get(key)) for key in ("kind", "z_pct", "x_r", "load_loss_kw", "kv1", "kv2", "tap",
                                                       "vector", "nll_pct", "im_pct", "unit_mva"))
        d.update({"name": name + "-GSU", "hv": at, "lv": lv, "mva": g["gsu"]["mva"] * k,
                  "units": g["gsu"]["units"] * k, "rate": g["gsu"]["rate"] * k, "rate_b": g["gsu"]["rate_b"] * k,
                  "rate_c": g["gsu"]["rate_c"] * k, "count": 1, "where": where})
        u["gsu"], u["bus"] = add_tx2(net, d), lv
    else:
        u["gsu"], u["bus"] = None, at
    return u


def thevenin(net, pl, gens):
    W = "Plant"
    rated = _num(pl, "scr_base_mw", W, None, positive=True) or sum(max(g["pmax"], 0.0) for g in gens)
    x_r = _num(pl, "grid_x_r", W, None, positive=True)
    r_pu, x_pu = _num(pl, "grid_r_pu", W), _num(pl, "grid_x_pu", W, None, positive=True)
    if r_pu is not None and r_pu < 0:
        raise InputError("Plant: 'Grid R' must be 0 or more (it is %g)" % r_pu)
    sc = _num(pl, "grid_sc_mva", W, None, positive=True)
    scr = _num(pl, "grid_scr", W, None, positive=True)
    if x_pu is not None:
        _unused(net.notes, pl, ("grid_scr", "grid_sc_mva", "grid_x_r"), W, "the grid is set by 'Grid R' and 'Grid X'")
        r_pu = r_pu or 0.0
        how = "R and X given"
        z = math.hypot(r_pu, x_pu)
        sc = net.sb / z
        scr = sc / rated if rated else None
        x_r = x_pu / r_pu if r_pu else float("inf")
    else:
        if sc is None and scr is None:
            raise InputError("Plant: 'Grid SCR' (or 'Grid short-circuit MVA', or 'Grid R' / 'Grid X') is missing")
        _unused(net.notes, pl, ("grid_r_pu",), W, "'Grid R' goes with 'Grid X' (instead of the SCR), which is blank")
        if x_r is None:
            raise InputError("Plant: 'Grid X/R' is missing")
        if sc is not None:
            _unused(net.notes, pl, ("grid_scr",), W, "'Grid short-circuit MVA' %g sets the grid (an SCR of %.4g)" % (
                sc, sc / rated if rated else float("nan")))
        if sc is None:
            if not rated:
                raise InputError("Plant: the SCR needs an 'SCR base' (the generators have no Pmax)")
            sc = scr * rated
            how = "SCR given"
        else:
            scr = sc / rated if rated else None
            how = "short-circuit MVA given"
        z = net.sb / sc
        r_pu = z / math.sqrt(1.0 + x_r * x_r)
        x_pu = r_pu * x_r
    if not x_pu > 0:
        raise InputError("Plant: the grid reactance must be above 0")
    zb = net.kv("POI") ** 2 / net.sb
    net.thev = {"r": r_pu, "x": x_pu, "sc": sc, "scr": scr, "x_r": x_r, "rated": rated, "how": how,
                "R": r_pu * zb, "X": x_pu * zb}
    add_line(net, "POI", "IB", {"r": r_pu, "x": x_pu, "b": 0.0, "R": r_pu * zb, "X": x_pu * zb, "B": 0.0,
                                "count": 1, "rate": 0.0, "kv": net.kv("POI"), "km": 0.0}, "GRID", "GRID", W)
    if scr is not None and scr < 3.0:
        net.notes.append("NOTE: SCR %.2f -- a weak grid" % scr)


def control(net, pl):
    W = "Plant"
    c = {"mode": _choice(pl, "control_mode", W, ["Q", "PF", "V"], "Q"),
         "v_poi": _num(pl, "v_poi_pu", W, 1.0, positive=True),
         "q_poi": _num(pl, "q_poi_mvar", W, 0.0),
         "pf_poi": _num(pl, "pf_poi", W, 1.0),
         "v_grid": _num(pl, "v_grid_pu", W, 1.0, positive=True),
         "p_poi": _num(pl, "p_poi_mw", W, None),
         "plant_ctrl": _choice(pl, "plant_ctrl", W, ["REPC_A", "REPC_B"], "REPC_A"),
         "regc_repc": _choice(pl, "regc_repc", W, ["standard", "USRMDL"], "standard")}
    if c["mode"] == "PF" and not 0.0 < abs(c["pf_poi"]) <= 1.0:
        raise InputError("Plant: 'PF at POI' must be between 0 and 1 (+ lagging, - leading)")
    for k, key, used in (("v_poi", "v_poi_pu", True), ("v_grid", "v_grid_pu", c["mode"] == "V")):
        if used and not 0.5 <= c[k] <= 1.5:
            raise InputError("Plant: %s = %g pu -- a voltage in pu (0.9 .. 1.1), not in kV" % (_label(key), c[k]))
        if used and not 0.9 <= c[k] <= 1.1:
            net.notes.append("CHECK: Plant: %s %g pu -- outside 0.9 .. 1.1" % (_label(key), c[k]))
    _unused(net.notes, pl, {"Q": ("pf_poi", "v_grid_pu"), "PF": ("q_poi_mvar", "v_grid_pu"),
                            "V": ("q_poi_mvar", "pf_poi")}[c["mode"]], W, "Control mode %s" % c["mode"],
            {"pf_poi": 1.0, "v_grid_pu": 1.0, "q_poi_mvar": 0.0})
    c["pscad"] = {"time_step_us": _num(pl, "pscad_time_step_us", W, 10.0, positive=True),
                  "duration_s": _num(pl, "pscad_duration_s", W, 5.0, positive=True),
                  "plot_step_us": _num(pl, "pscad_plot_step_us", W, 100.0, positive=True),
                  "meter": _choice(pl, "pscad_meter", W, ["YES", "NO"], "YES") == "YES",
                  "inv_meter": _choice(pl, "pscad_inv_meter", W, ["YES", "NO"], "YES") == "YES",
                  "lines": _choice(pl, "pscad_lines", W, ["PI", "RLC"], "PI"),
                  "fault": None if _choice(pl, "pscad_fault", W, ["YES", "NO"], "YES") == "NO" else {
                      "t": _num(pl, "pscad_fault_s", W, None), "type": _choice(pl, "pscad_fault_type", W, FAULT_TYPES,
                                                                                "ABCG"),
                      "dur": _num(pl, "pscad_fault_dur_s", W, 0.15, positive=True),
                      "ohm": _num(pl, "pscad_fault_ohm", W, 0.01, positive=True)},
                  "libraries": [x.strip() for x in (_txt(pl.get("pscad_libraries")) or "").split(";") if x.strip()]}
    flt = c["pscad"]["fault"]
    if flt and flt["t"] is not None and flt["t"] < 0:
        raise InputError("Plant: %s must be 0 or more (it is %s)" % (_label("pscad_fault_s"), _g(flt["t"])))
    if flt and flt["t"] is not None and flt["t"] >= c["pscad"]["duration_s"]:
        net.notes.append("NOTE: Plant: PSCAD fault time %s s is past the run's end (%s s) -- no fault in the run"
                         % (_g(flt["t"]), _g(c["pscad"]["duration_s"])))
    net.ctl = c


def number_buses(net, pl, rows):
    start = _int(pl, "first_bus", "Plant", 1000, minimum=0)
    fixed, names = {}, {}
    for r in rows:
        w = r["_where"]
        ref = _txt(r.get("bus"))
        if not ref:
            continue
        num = _int(r, "number", w, None, minimum=1)
        nm = _txt(r.get("name"))
        role = net.alias.get(ref.upper())
        if ref.upper() in net.moved:              # DETAILED: each unit group has its own GSU and terminal
            if num is not None or nm:
                net.notes.append("NOTE: %s: %s is no bus of its own in the DETAILED model (each unit group has its "
                                 "own GSU and terminal) -- its number / name not used" % (w, ref))
            continue
        if role is None:                          # a bus of the other model type (SMIB / DETAILED), or a typo
            if num is not None or nm:
                net.notes.append("NOTE: %s: no bus %s in this %s model -- its number / name not used (buses: %s)"
                                 % (w, ref, net.model, ", ".join(sorted(net.alias))))
            continue
        if num is not None:
            if num > 999997:
                raise InputError("%s: bus number %d is above 999997" % (w, num))
            if num in fixed.values() and fixed.get(role) != num:
                raise InputError("%s: bus number %d is used twice" % (w, num))
            if role in fixed and fixed[role] != num:
                net.notes.append("NOTE: %s: %s is bus %s here, already numbered %d -- %d not used"
                                 % (w, ref, role, fixed[role], num))
            else:
                fixed[role] = num
        if nm:
            names.setdefault(role, nm[:12])
    nxt = start
    for role, b in net.bus.items():
        if role in fixed:
            b["num"] = fixed[role]
        else:
            nxt += 1
            while nxt in fixed.values():
                nxt += 1
            b["num"] = nxt
        if b["num"] > 999997:
            raise InputError("Plant: bus numbers run past 999997 -- a lower 'First bus number'")
        b["name"] = names.get(role) or b["name"] or role.replace("C:", "").replace(":", "_")[:12]
    seen = {}
    for role in names:                            # names given in Bus_Numbers stay as given
        key = net.bus[role]["name"].strip().upper()
        if key in seen:
            net.notes.append("NOTE: Bus_Numbers: buses %s and %s are both named %s" % (seen[key], role, names[role]))
        seen.setdefault(key, role)
    for role, b in net.bus.items():               # automatic names, cut to 12 characters: each its own
        key = b["name"].strip().upper()
        if role not in names:
            if key in seen:
                tail = "_%d" % b["num"]
                b["name"] = b["name"][:12 - len(tail)] + tail
                key = b["name"].upper()
            seen.setdefault(key, role)


# ============================================================================
# LOAD FLOW -- Newton-Raphson on the plant's own network (no PSS/E needed)
# ============================================================================
ZMIN = 1e-4                # the smallest series impedance the load flow takes (pu): PSS/E's and PSLF's jumper
                           # threshold -- a shorter branch (a few feet of gen-tie) is a jumper there


def _yser(z):
    """A series admittance 1/z, z at least ZMIN (its angle kept)."""
    a = abs(z)
    if a < ZMIN:
        z = ZMIN * (z / a if a > 0 else 1j)
    return 1.0 / z


def ybus(net, roles):
    """(index of each role, Y, size) over `roles`, with a star point for every three-winding transformer on them.
       PSS/E's models: lines as pi sections; a two-winding transformer as bus I -[t1:1]- Z -[1:t2]- bus J
       (t = WINDV / bus kV) with its magnetising admittance at bus I; a three-winding one as three windings to a
       star point."""
    idx = dict((r, k) for k, r in enumerate(roles))
    n = len(roles)
    stars = [t for t in net.tx3 if t["hv"] in idx and t["lv"] in idx and t["tv"] in idx]
    N = n + len(stars)
    Y = [[0j] * N for _k in range(N)]

    def branch(i, j, y11, y12, y21, y22):
        Y[i][i] += y11
        Y[i][j] += y12
        Y[j][i] += y21
        Y[j][j] += y22

    for ln in net.lines:
        if ln["fr"] in idx and ln["to"] in idx:
            y = _yser(complex(ln["r"], ln["x"]))
            hb = 0.5j * ln["b"]
            for _c in range(ln["count"]):
                branch(idx[ln["fr"]], idx[ln["to"]], y + hb, -y, -y, y + hb)
    for t in net.tx2:
        if t["hv"] in idx and t["lv"] in idx:
            y = _yser(complex(t["r"], t["x"]) * net.sb / t["mva"])
            t1 = t["windv1"] / net.kv(t["hv"])
            t2 = t["windv2"] / net.kv(t["lv"])
            ym = complex(t["mag_g"], t["mag_b"])
            for _c in range(t["count"]):
                branch(idx[t["hv"]], idx[t["lv"]], y / (t1 * t1) + ym, -y / (t1 * t2), -y / (t1 * t2), y / (t2 * t2))
    for s, t in enumerate(stars):
        zs = [complex(t["r12"], t["x12"]) * net.sb / t["s12"], complex(t["r23"], t["x23"]) * net.sb / t["s23"],
              complex(t["r31"], t["x31"]) * net.sb / t["s31"]]
        zk = [(zs[0] + zs[2] - zs[1]) / 2.0, (zs[0] + zs[1] - zs[2]) / 2.0, (zs[1] + zs[2] - zs[0]) / 2.0]
        k_star = n + s
        t["_star"] = k_star
        for c in range(t["count"]):
            for w, role in enumerate((t["hv"], t["lv"], t["tv"])):
                y = _yser(zk[w])
                tk = t["windv"][w] / net.kv(role)
                branch(idx[role], k_star, y / (tk * tk), -y / tk, -y / tk, y)
            Y[idx[t["hv"]]][idx[t["hv"]]] += complex(t["mag_g"], t["mag_b"])
    for ld in net.loads:
        if ld["bus"] in idx:
            Y[idx[ld["bus"]]][idx[ld["bus"]]] += complex(ld["mw"], -ld["mvar"]) / net.sb
    for sh in net.shunts:
        if sh["bus"] in idx:
            Y[idx[sh["bus"]]][idx[sh["bus"]]] += complex(0.0, sh["mvar"]) / net.sb
    return idx, Y, N


def _lin_solve(A, b):
    """A x = b by sparse Gaussian elimination: A's rows as lists or {column: value}; the next pivot in a column
       with the fewest entries left (little fill-in on a radial network), a row with a large enough entry in it
       and the fewest entries."""
    n = len(b)
    rows = [dict(r) if isinstance(r, dict) else dict((k, v) for k, v in enumerate(r) if v) for r in A]
    b = list(b)
    cols = [set() for _k in range(n)]
    for i, r in enumerate(rows):
        for k in r:
            cols[k].add(i)
    left, order = set(range(n)), []
    for _step in range(n):
        c = min(left, key=lambda k: len(cols[k]))
        cand = cols[c]
        big = max([abs(rows[i][c]) for i in cand] or [0.0])
        if big < 1e-14:
            raise ZeroDivisionError("singular matrix")
        p = min((i for i in cand if abs(rows[i][c]) >= 0.1 * big), key=lambda i: len(rows[i]))
        left.discard(c)
        prow = rows[p]
        for k in prow:
            cols[k].discard(p)
        piv = prow[c]
        for i in list(cand):
            r = rows[i]
            f = r.pop(c) / piv
            for k, v in prow.items():
                if k != c:
                    if k not in r:
                        cols[k].add(i)
                        r[k] = -f * v
                    else:
                        r[k] -= f * v
            b[i] -= f * b[p]
        cols[c] = set()
        order.append((p, c))
    x = [0.0] * n
    for p, c in reversed(order):
        r = rows[p]
        x[c] = (b[p] - sum(v * x[k] for k, v in r.items() if k != c)) / r[c]
    return x


def newton(Y, slack, vslack, sinj, v0=None, tol=1e-10, itmax=40):
    """Every bus but `slack` is a PQ bus injecting sinj[k] (pu). -> (V, converged, iterations)."""
    N = len(Y)
    V = list(v0) if v0 else [complex(abs(vslack), 0.0)] * N
    V[slack] = vslack
    pq = [k for k in range(N) if k != slack]
    m = len(pq)
    nz = [[k for k in range(N) if Y[i][k] != 0] for i in range(N)]
    for it in range(itmax + 1):
        S = [V[i] * sum(Y[i][k] * V[k] for k in nz[i]).conjugate() for i in range(N)]
        mis = [sinj[i].real - S[i].real for i in pq] + [sinj[i].imag - S[i].imag for i in pq]
        if max([abs(x) for x in mis] or [0.0]) < tol:
            return V, True, it
        if it == itmax:
            break
        pos = dict((k, a) for a, k in enumerate(pq))
        J = [{} for _r in range(2 * m)]
        vm = [abs(v) for v in V]
        va = [cmath.phase(v) for v in V]
        for a, i in enumerate(pq):
            Pi, Qi = S[i].real, S[i].imag
            Gii, Bii = Y[i][i].real, Y[i][i].imag
            for k in nz[i]:
                if k == slack:
                    continue
                b_ = pos[k]
                if k == i:
                    J[a][a] = -Qi - Bii * vm[i] ** 2
                    J[a][m + a] = Pi / vm[i] + Gii * vm[i]
                    J[m + a][a] = Pi - Gii * vm[i] ** 2
                    J[m + a][m + a] = Qi / vm[i] - Bii * vm[i]
                else:
                    G, B = Y[i][k].real, Y[i][k].imag
                    th = va[i] - va[k]
                    c, s = math.cos(th), math.sin(th)
                    J[a][b_] = vm[i] * vm[k] * (G * s - B * c)
                    J[a][m + b_] = vm[i] * (G * c + B * s)
                    J[m + a][b_] = -vm[i] * vm[k] * (G * c + B * s)
                    J[m + a][m + b_] = vm[i] * (G * s - B * c)
        try:
            dx = _lin_solve(J, mis)
        except ZeroDivisionError:                    # singular: no solution from here (said by the caller)
            return V, False, it
        for a, k in enumerate(pq):
            ang = va[k] + dx[a]
            mag = vm[k] + dx[m + a]
            if mag <= 0.05:
                mag = 0.05
            V[k] = cmath.rect(mag, ang)
    return V, False, itmax


class SolveError(Exception):
    pass


TARGET_FIELDS = {"P": "P at POI", "Q": "Q at POI", "PF": "PF at POI", "V": "POI voltage"}   # what each POI target is


def ramped(Y, slack, vslack, sinj, steps=10):
    """Newton from the flat start up to sinj in steps, each from the last: a heavy plant's load flow that a flat
       start misses -> (V or None, the largest share of sinj solved, its V)."""
    V, good, Vg = None, 0.0, None
    k_bad = None
    for n in range(1, steps + 1):
        k = float(n) / steps
        V2, ok, _it = newton(Y, slack, vslack, [x * k for x in sinj], V)
        if not ok:
            k_bad = k
            break
        V, good, Vg = V2, k, V2
    if k_bad is None:
        return V, 1.0, V
    for _i in range(5):                               # how far it goes: between the last solved and the first not
        k = (good + k_bad) / 2.0
        V2, ok, _it = newton(Y, slack, vslack, [x * k for x in sinj], Vg)
        if ok:
            good, Vg = k, V2
        else:
            k_bad = k
    return None, good, Vg


def bottleneck(net, roles, idx, V):
    """Why the plant's power does not get through, at a solved state V: the branches with the most angle and
       voltage drop across them, and the lowest voltage -> text."""
    sb, br = net.sb, []

    def add(name, what, i, j, z, ohm=None):
        vi, vj = V[i], V[j]
        ang = abs(math.degrees(cmath.phase(vi / vj))) if abs(vj) > 1e-9 else 180.0
        cur = (vi - vj) / z if abs(z) > 1e-12 else 0j
        br.append((ang + 100.0 * abs(abs(vi) - abs(vj)), ang, abs(abs(vi) - abs(vj)), name, what, z, ohm,
                   abs((vi * cur.conjugate()).real) * sb))
    for ln in net.lines:
        if ln["fr"] in idx and ln["to"] in idx:
            n = ln["count"]
            add(ln["name"], "line", idx[ln["fr"]], idx[ln["to"]], complex(ln["r"], ln["x"]) / n,
                complex(ln["R"], ln["X"]) / n)
    for t in net.tx2:
        if t["hv"] in idx and t["lv"] in idx:
            add(t["name"], "transformer", idx[t["hv"]], idx[t["lv"]],
                complex(t["r"], t["x"]) * sb / t["mva"] / t["count"])
    for t in net.tx3:
        if "_star" in t and t["hv"] in idx:
            zs = [complex(t["r%s" % k], t["x%s" % k]) * sb / t["s%s" % k] for k in ("12", "23", "31")]
            add(t["name"], "transformer, HV to its star point", idx[t["hv"]], t["_star"],
                (zs[0] + zs[2] - zs[1]) / 2.0 / t["count"])
    br.sort(reverse=True)
    low = min(range(len(roles)), key=lambda k: abs(V[k]))
    out = []
    for _key, ang, dv, name, what, z, ohm, mw in br[:2]:
        out.append("%s (%s) has %.0f deg and %.2f pu across it with %.0f MW through it -- %s%.4g + j%.4g pu on "
                   "%g MVA" % (name, what, ang, dv, mw, ("R %.4g + X %.4g ohm = " % (ohm.real, ohm.imag)) if ohm
                                                       else "", z.real, z.imag, sb))
    return "; ".join(out) + "; the lowest voltage %.3f pu at %s" % (abs(V[low]), roles[low])


def operating_point(net, log):
    """Generators' P and Q, every bus voltage, and the infinite bus -- POI targets met (see CONTROL)."""
    c = net.ctl
    sb = net.sb
    roles = [r for r in net.bus if r != "IB"]
    idx, Y, N = ybus(net, roles)
    poi = idx["POI"]
    zth = complex(net.thev["r"], net.thev["x"])
    share = [g for g in net.gens if g["q_mode"] == "share"]
    tot_qmax = sum(max(g["qmax"], 0.0) for g in share)
    for g in share:
        g["share"] = (max(g["qmax"], 0.0) / tot_qmax) if tot_qmax > 0 else 1.0 / len(share)
    state = {"V": None, "vpoi": c["v_poi"]}

    def gen_pq(alpha, beta):
        out = {}
        for g in net.gens:
            p = g["dispatch"] * alpha
            if g["q_mode"] == "share":
                q = beta * g["share"]
            else:
                q = g["q_fixed"]
                if isinstance(q, tuple):
                    pf = q[1]
                    q = abs(p) * math.tan(math.acos(abs(pf))) * (1.0 if pf > 0 else -1.0)
            out[g["name"]] = (p, q)
        return out

    def inner(alpha, beta):
        pq = gen_pq(alpha, beta)
        sinj = [0j] * N
        for g in net.gens:
            p, q = pq[g["name"]]
            sinj[idx[g["bus"]]] += complex(p, q) / sb
        vpoi = complex(state["vpoi"], 0.0)
        V, ok, it = newton(Y, poi, vpoi, sinj, state["V"])
        if not ok:
            V, ok, it = newton(Y, poi, vpoi, sinj, None)
        if not ok:                                    # a heavy plant: the dispatch in steps
            V, share_ok, Vg = ramped(Y, poi, vpoi, sinj)
            ok = V is not None
        if not ok:
            bad = [k for k, res in net.colls.items() if res.get("check") is None or not (
                res["check"]["detailed"]["ok"] and res["check"]["equivalent"]["ok"])]
            p_all = sum(x.real for x in sinj) * sb
            why = (" -- its network carries no more than about %.0f%% of it (%.0f of %.0f MW): there, %s. Check "
                   "that branch's data (a line's length and its unit, R and X; a transformer's Z %% and MVA)" % (
                       100.0 * share_ok, share_ok * p_all, p_all, bottleneck(net, roles, idx, Vg))
                   if Vg is not None else
                   " -- not even 10% of it solves: a branch's data is far off (a length and its unit, R and X, "
                   "a transformer's Z % and MVA)")
            raise SolveError("the plant's load flow does not converge (alpha %.3f, Q %.1f MVAr)%s%s" % (
                alpha, beta, why, (" -- %s %s %s not solve on %s own either: check the Feeders lengths and their "
                                   "unit (km, mi, kft, m, ft) and the cables' R and X" % (
                                       "collector" if len(bad) == 1 else "collectors", ", ".join(bad),
                                       "does" if len(bad) == 1 else "do", "its" if len(bad) == 1 else "their"))
                if bad else ""))
        state["V"] = V
        i_poi = sum(Y[poi][k] * V[k] for k in range(N))
        s_grid = -(V[poi] * i_poi.conjugate()) * sb                # MW + jMVAr from the POI into the grid
        e = V[poi] - zth * (s_grid / sb / V[poi]).conjugate()
        return V, s_grid, e, pq

    targets = []
    if c["p_poi"] is not None:
        if not any(abs(g["dispatch"]) > 0 for g in net.gens):
            raise InputError("Plant: P at POI is given but every generator's dispatch is 0")
        hi, lo = sum(g["pmax"] for g in net.gens), sum(g["pmin"] for g in net.gens)
        if not lo - 1e-6 <= c["p_poi"] <= hi + 1e-6:    # beyond the plant: no load flow can give it
            raise InputError("Plant: 'P at POI' %g MW is %s the generators' %s, %g MW in all -- the plant cannot "
                             "deliver it" % (c["p_poi"], "above" if c["p_poi"] > hi else "below",
                                             "Pmax" if c["p_poi"] > hi else "Pmin", hi if c["p_poi"] > hi else lo))
        targets.append("P")
    free_v = not share and c["mode"] == "V"           # nothing holds the POI: the grid sets its voltage
    if share or free_v:
        targets.append(c["mode"])
    if not share:
        net.notes.append("NOTE: no generator has Q mode share -- the POI's Q is what the fixed generators give%s"
                         % ("; the grid at %.4f pu sets the POI voltage" % c["v_grid"] if free_v else ""))

    def resid(x):
        a = x[0] if "P" in targets else 1.0
        b = x[-1] if share else 0.0
        if free_v:
            state["vpoi"] = x[-1]
        V, s, e, pq = inner(a, b)
        out = []
        for t in targets:
            if t == "P":
                out.append(s.real - c["p_poi"])
            elif t == "Q":
                out.append(s.imag - c["q_poi"])
            elif t == "PF":
                k = math.tan(math.acos(abs(c["pf_poi"]))) * (1.0 if c["pf_poi"] > 0 else -1.0)
                out.append(s.imag - k * s.real)
            else:
                out.append((abs(e) - c["v_grid"]) * sb)
        return out, (V, s, e, pq)

    x = ([1.0] if "P" in targets else []) + ([0.0] if share else ([c["v_grid"]] if free_v else []))
    if "P" in targets and c["p_poi"] is not None:
        tot = sum(g["dispatch"] for g in net.gens)
        if tot:
            x[0] = c["p_poi"] / tot
    sol = None
    for it in range(60):
        r, sol = resid(x)
        if all(abs(v) < 1e-7 for v in r):
            break
        J = []
        for j in range(len(x)):
            h = 1e-6 if (j == 0 and "P" in targets) or free_v else 1e-3
            xx = list(x)
            xx[j] += h
            rr, _s = resid(xx)
            J.append([(rr[i] - r[i]) / h for i in range(len(r))])
        Jt = [[J[j][i] for j in range(len(x))] for i in range(len(r))]
        try:
            dx = _lin_solve(Jt, [-v for v in r])
        except ZeroDivisionError:
            raise SolveError("Plant: the POI targets (%s) cannot be met -- no generator moves them: check 'P at POI' "
                             "against the generators' Dispatch MW, the Control mode and its target, and that a "
                             "generator has Q mode share" % ", ".join(
                                 TARGET_FIELDS[t]
                                 for t in targets))
        step = 1.0
        for _k in range(8):
            xn = [x[j] + step * dx[j] for j in range(len(x))]
            try:
                rn, _s = resid(xn)
                if max(abs(v) for v in rn) < max(abs(v) for v in r) or step < 0.02:
                    break
            except SolveError:
                pass
            step *= 0.5
        x = xn
    else:
        raise SolveError("Plant: the POI targets (%s) are not met after 60 iterations (still %s off) -- check them "
                         "against what the plant can give: 'P at POI' against the generators' Pmax, the Control "
                         "mode's target against their Q range" % (", ".join(
                             TARGET_FIELDS[t]
                             for t in targets), ", ".join("%.3g" % v for v in r)))
    V, s_grid, e, pq = sol
    rot = cmath.rect(1.0, -cmath.phase(e))                     # the infinite bus at 0 degrees
    for role, k in idx.items():
        v = V[k] * rot
        net.bus[role]["vm"], net.bus[role]["va"] = abs(v), math.degrees(cmath.phase(v))
    net.bus["IB"]["vm"], net.bus["IB"]["va"] = abs(e), 0.0
    for t in net.tx3:
        if "_star" in t:
            v = V[t["_star"]] * rot
            t["vstar"], t["astar"] = abs(v), math.degrees(cmath.phase(v))
    for g in net.gens:
        g["p"], g["q"] = pq[g["name"]]
    if "P" in targets:                                         # the scaled dispatch within each generator's range
        fam = collections.OrderedDict()
        for g in net.gens:
            f = fam.setdefault(g.get("family", g["name"]), [0.0, 0.0, 0.0])
            f[0] += g["p"]
            f[1] += g["pmin"]
            f[2] += g["pmax"]
        for nm, (pg, lo, hi) in fam.items():
            if pg > hi + 1e-3 or pg < lo - 1e-3:
                net.notes.append("CHECK: Plant: P at POI %.1f MW puts %s at %.3f MW, outside its Pmin .. Pmax %.3f .. "
                                 "%.3f MW -- a P at POI the plant can give, or the dispatches in Generators"
                                 % (c["p_poi"], nm, pg, lo, hi))
    net.op = {"s_grid": s_grid, "e": abs(e), "alpha": x[0] if "P" in targets else 1.0, "targets": targets}

    # the whole network once more, from the infinite bus: the POI must come out at its target
    roles2 = list(net.bus)
    idx2, Y2, N2 = ybus(net, roles2)
    sinj = [0j] * N2
    for g in net.gens:
        sinj[idx2[g["bus"]]] += complex(g["p"], g["q"]) / sb
    v0 = [cmath.rect(net.bus[r]["vm"], math.radians(net.bus[r]["va"])) for r in roles2] + \
        [1.0 + 0j] * (N2 - len(roles2))
    for t in net.tx3:
        if t.get("_star") is not None and t["_star"] < N2:
            v0[t["_star"]] = cmath.rect(t.get("vstar", 1.0), math.radians(t.get("astar", 0.0)))
    V2, ok, _it = newton(Y2, idx2["IB"], complex(abs(e), 0.0), sinj, v0)
    dv = abs(abs(V2[idx2["POI"]]) - state["vpoi"]) if ok else None
    net.op["check_dv"] = dv
    if not ok or dv > 1e-6:
        net.notes.append("WARNING: the load flow from the infinite bus does not reproduce the POI voltage (%s)" % dv)
    flows(net, idx2, V2, Y2)
    chg = collections.OrderedDict()                  # the cables' and lines' charging, by kind, at their kV
    for ln in net.lines:
        if ln["kind"] != "GRID":
            k = {"SECTION": "collectors", "COLLECTOR": "collectors", "GENTIE": "gen-tie", "TIE": "MPT ties"}.get(
                ln["kind"], ln["kind"].lower())
            chg[k] = chg.get(k, 0.0) + ln["B"] * 1e-6 * ln["kv"] ** 2 * ln["count"]
    for g in net.gens:
        if g["q_mode"] == "share" and not (g["qmin"] - 1e-3 <= g["q"] <= g["qmax"] + 1e-3):
            why = ""
            if g["q"] < g["qmin"] and sum(chg.values()) > 0.01:      # the charging is what it would have to take
                why = "; the plant's cables make %.2f MVAr of charging (%s), the generators take %.2f MVAr at " \
                      "most -- fewer or shorter feeders (Feeders on each MPT), their real cables, or another POI " \
                      "target" % (sum(chg.values()), ", ".join("%s %.2f" % kv for kv in chg.items()),
                                  -sum(x["qmin"] for x in net.gens if x["q_mode"] == "share"))
            net.notes.append("CHECK: %s needs Q %.2f MVAr, outside its %.2f .. %.2f MVAr -- the POI target is "
                             "beyond the plant (PSS/E will hold the limit)%s" % (g["name"], g["q"], g["qmin"],
                                                                                 g["qmax"], why))
        vt = net.bus[g["bus"]]["vm"]
        if not 0.9 <= vt <= 1.1:
            net.notes.append("WARNING: %s terminal voltage %.4f pu -- outside 0.9 .. 1.1: check the transformers' kV "
                             "and taps, and the POI voltage" % (g["name"], vt))
    terms = set(g["bus"] for g in net.gens)
    for role, b in net.bus.items():                  # every other bus of the plant too
        if role != "IB" and role not in terms and not 0.9 <= b.get("vm", 1.0) <= 1.1:
            net.notes.append("WARNING: bus %s at %.4f pu -- outside 0.9 .. 1.1: check the transformers' kV and taps, "
                             "and the POI voltage" % (role, b["vm"]))
    return net.op


def flows(net, idx, V, Y):
    """Branch flows from the solved voltages (MW + jMVAr leaving each end), losses, loading."""
    sb = net.sb
    out = []

    def s_ij(i, yii, yij, j):
        return V[i] * (yii * V[i] + yij * V[j]).conjugate() * sb

    for ln in net.lines:
        i, j = idx[ln["fr"]], idx[ln["to"]]
        y = _yser(complex(ln["r"], ln["x"]))
        hb = 0.5j * ln["b"]
        s1 = s_ij(i, y + hb, -y, j) * ln["count"]
        s2 = s_ij(j, y + hb, -y, i) * ln["count"]
        rate = ln["rate"] * ln["count"]
        out.append({"name": ln["name"], "kind": ln["kind"], "a": ln["fr"], "b": ln["to"], "s1": s1, "s2": s2,
                    "rate": rate, "load": (max(abs(s1), abs(s2)) / rate * 100.0) if rate else None})
    for t in net.tx2:
        i, j = idx[t["hv"]], idx[t["lv"]]
        y = _yser(complex(t["r"], t["x"]) * sb / t["mva"])
        t1, t2 = t["windv1"] / net.kv(t["hv"]), t["windv2"] / net.kv(t["lv"])
        ym = complex(t["mag_g"], t["mag_b"])
        s1 = s_ij(i, y / (t1 * t1) + ym, -y / (t1 * t2), j) * t["count"]
        s2 = s_ij(j, y / (t2 * t2), -y / (t1 * t2), i) * t["count"]
        rate = t["rate"] * t["count"]
        out.append({"name": t["name"], "kind": t["kind"], "a": t["hv"], "b": t["lv"], "s1": s1, "s2": s2,
                    "rate": rate, "load": (max(abs(s1), abs(s2)) / rate * 100.0) if rate else None})
    for t in net.tx3:
        k = t.get("_star")
        if k is None or k >= len(V):
            continue
        zs = [complex(t["r12"], t["x12"]) * sb / t["s12"], complex(t["r23"], t["x23"]) * sb / t["s23"],
              complex(t["r31"], t["x31"]) * sb / t["s31"]]
        zk = [(zs[0] + zs[2] - zs[1]) / 2.0, (zs[0] + zs[1] - zs[2]) / 2.0, (zs[1] + zs[2] - zs[0]) / 2.0]
        ss = []
        for w, role in enumerate((t["hv"], t["lv"], t["tv"])):
            y = _yser(zk[w])
            tk = t["windv"][w] / net.kv(role)
            i = idx[role]
            yy = y / (tk * tk) + (complex(t["mag_g"], t["mag_b"]) if w == 0 else 0)
            ss.append(V[i] * (yy * V[i] - (y / tk) * V[k]).conjugate() * sb * t["count"])
        rate = t["rate"] * t["count"]
        out.append({"name": t["name"], "kind": t["kind"], "a": t["hv"], "b": t["lv"] + "/" + t["tv"], "s1": ss[0],
                    "s2": ss[1] + ss[2], "s3": ss, "rate": rate,
                    "load": (abs(ss[0]) / rate * 100.0) if rate else None})
    gsu_of = dict((g["gsu"]["name"], g) for g in net.gens if g.get("gsu"))
    for f in out:
        if f["load"] is not None and f["load"] > 100.0:
            sx = max((f["s1"], f["s2"]), key=abs)
            g = gsu_of.get(f["name"])
            why = ""
            if g and g["q_mode"] == "share" and not (g["qmin"] - 1e-3 <= g["q"] <= g["qmax"] + 1e-3):
                ql = min(max(g["q"], g["qmin"]), g["qmax"])          # the Q it has: its limit
                why = " -- %s is asked for Q %.2f MVAr, beyond its %.2f .. %.2f (see 'needs Q'); at %.2f MVAr about " \
                      "%.0f %%" % (g["name"], g["q"], g["qmin"], g["qmax"], ql,
                                   abs(complex(g["p"], ql)) / f["rate"] * 100.0)
            net.notes.append("CHECK: %s loaded to %.1f %% of %.1f MVA: %.2f MVA through it, P %.2f MW and Q %.2f MVAr%s"
                             % (f["name"], f["load"], f["rate"], abs(sx), abs(sx.real), abs(sx.imag), why))
    for t in net.tx3:                                      # its LV and TV windings against their own MVA
        f = next((x for x in out if x["name"] == t["name"] and "s3" in x), None)
        for w, key in ((1, "mva2"), (2, "mva3")):
            mva = (t.get(key) or 0.0) * t["count"]
            if f and mva and abs(f["s3"][w]) > mva * 1.0001:
                net.notes.append("CHECK: %s %s winding loaded to %.1f %% of its %.1f MVA" % (
                    t["name"], "LV" if w == 1 else "TV", abs(f["s3"][w]) / mva * 100.0, mva))
    net.op["flows"] = out
    net.op["loss"] = sum(f["s1"] + f["s2"] for f in out if f["kind"] != "GRID")


# ============================================================================
# PSS/E -- the .raw (rev 33)
# ============================================================================
def _num_of(net, role):
    return net.bus[role]["num"]


def raw_gen(net, g, poi):
    """A generator's .raw values: (QT, QB, VS, IREG, RMPCT, ZX, WMOD) -- ZX, WMOD, IREG and VS kept on it for the
       report and the .dyr, with or without the .raw."""
    if g["q_mode"] == "share":
        qt, qb, vs, ireg, rmp = g["qmax"], g["qmin"], net.ctl["v_poi"], poi, 100.0 * g["share"]
    else:
        qt, qb, vs, ireg, rmp = g["q"], g["q"], net.bus[g["bus"]]["vm"], 0, 100.0
    zx = g["zsorce_x"]
    if zx is None and g.get("regc") == "REGC_B":
        zx = REGCB_XE
        net.notes.append("CHECK: %s: REGC_B with ZSORCE X blank -- Xe %g pu on MBASE used: set the converter's own "
                         "coupling reactance (Generators: ZSORCE X)" % (g.get("family", g["name"]), zx))
    elif zx is None:
        zx = DYN_DEFAULTS[(g.get("sync") or SYNC_DEFAULT)[0]]["Xdpp"] if g["type"] == "SYNC" else IBR_ZSORCE_X
    wmod = g["wmod"] if g["wmod"] is not None else (0 if g["type"] == "SYNC" else 1)
    g["zx"], g["wmod_used"], g["ireg"], g["vs"] = zx, wmod, ireg, vs
    return qt, qb, vs, ireg, rmp, zx, wmod


def write_raw(net, path):
    sb = net.sb
    L = ["0, %.2f, 33, 0, 0, %.2f     / PSS(R)E-33 RAW  %s %s -- smib_build.py  %s"
         % (sb, net.f, net.model, net.name, time.strftime("%Y-%m-%d %H:%M")),
         ("%s %s" % (net.model, net.name))[:60],
         ("POI %.1f kV  SCR %s  X/R %s" % (net.kv("POI"), _g(net.thev["scr"], 4) if net.thev["scr"] else "-",
                                           _g(net.thev["x_r"], 4)))[:60]]
    gen_buses = set(g["bus"] for g in net.gens)
    for role, b in net.bus.items():
        ide = 3 if role == "IB" else (2 if role in gen_buses else 1)
        b["ide"] = ide
        L.append("%7d,'%-12s',%10.4f,%d,   1,   1,   1,%10.6f,%11.5f, 1.10000, 0.90000, 1.10000, 0.90000"
                 % (b["num"], b["name"][:12], b["kv"], ide, b["vm"], b["va"]))
    L.append("0 / END OF BUS DATA, BEGIN LOAD DATA")
    ids = collections.defaultdict(int)
    for ld in net.loads:
        ids[ld["bus"]] += 1
        ld["id"] = str(ids[ld["bus"]])
        L.append("%7d,'%-2s',1,   1,   1,     0.000,     0.000,     0.000,     0.000,%10.4f,%10.4f,   1,1,0"
                 % (_num_of(net, ld["bus"]), ld["id"], ld["mw"], -ld["mvar"]))
    L.append("0 / END OF LOAD DATA, BEGIN FIXED SHUNT DATA")
    ids = collections.defaultdict(int)
    for sh in net.shunts:
        ids[sh["bus"]] += 1
        sh["id"] = str(ids[sh["bus"]])
        L.append("%7d,'%-2s',1,     0.000,%10.4f" % (_num_of(net, sh["bus"]), sh["id"], sh["mvar"]))
    L.append("0 / END OF FIXED SHUNT DATA, BEGIN GENERATOR DATA")
    s = next(f["s2"] for f in net.op["flows"] if f["kind"] == "GRID")      # what the infinite bus takes in
    L.append("%7d,'%-2s',%10.4f,%10.4f,%10.2f,%10.2f,%9.6f,%7d,%10.1f,%9.6f,%9.6f, 0.00000, 0.00000,1.00000,1,"
             "100.0,%10.2f,%10.2f,   1,1.0000,   0,1.0000,   0,1.0000,   0,1.0000,0, 1.0000"
             % (_num_of(net, "IB"), IB_ID, s.real, s.imag, 99999.0, -99999.0, net.bus["IB"]["vm"], 0, IB_MBASE,
                0.0, IB_XSOURCE, 99999.0, -99999.0))
    poi = _num_of(net, "POI")
    for g in net.gens:
        qt, qb, vs, ireg, rmp, zx, wmod = raw_gen(net, g, poi)
        L.append("%7d,'%-2s',%10.4f,%10.4f,%10.4f,%10.4f,%9.6f,%7d,%10.3f,%9.6f,%9.4f, 0.00000, 0.00000,1.00000,1,"
                 "%6.2f,%10.4f,%10.4f,   1,1.0000,   0,1.0000,   0,1.0000,   0,1.0000,%d, 1.0000"
                 % (_num_of(net, g["bus"]), g["id"], g["p"], g["q"], qt, qb, vs, ireg, g["mbase"], 0.0, zx, rmp,
                    g["pmax"], g["pmin"], wmod))
    L.append("0 / END OF GENERATOR DATA, BEGIN BRANCH DATA")
    for ln in net.lines:
        for c in range(ln["count"]):
            L.append("%7d,%7d,'%-2s',%12s,%12s,%12s,%9.2f,%9.2f,%9.2f, 0.00000, 0.00000, 0.00000, 0.00000,1,1,"
                     "%8.3f,   1,1.0000"
                     % ((_num_of(net, ln["fr"]), _num_of(net, ln["to"]), str(c + 1), _g(ln["r"], 7), _g(ln["x"], 7),
                         _g(ln["b"], 7)) + _rabc(ln) + (ln["km"],)))
    L.append("0 / END OF BRANCH DATA, BEGIN TRANSFORMER DATA")
    for t in net.tx2:
        for c in range(t["count"]):
            L.append("%7d,%7d,      0,'%-2s',1,2,1,%12s,%12s,2,'%-12s',1,   1,1.0000,   0,1.0000,   0,1.0000,   0,"
                     "1.0000,'%-12s'" % (_num_of(net, t["hv"]), _num_of(net, t["lv"]), str(c + 1), _g(t["mag_g"], 7),
                                         _g(t["mag_b"], 7), t["name"][:12], t["vg"]["text"][:12]))
            L.append("%12s,%12s,%10.3f" % (_g(t["r"], 7), _g(t["x"], 7), t["mva"]))
            w1 = t["windv1"] / t["nomv1"]
            L.append("%10.7f,%10.4f,   0.000,%9.2f,%9.2f,%9.2f, 0,      0, 1.10000, 0.90000,1.10000,0.90000,  33, 0, "
                     "0.00000, 0.00000,  0.000" % ((w1, t["nomv1"]) + _rabc(t)))
            L.append("%10.7f,%10.4f" % (t["windv2"] / t["nomv2"], t["nomv2"]))
    for t in net.tx3:
        for c in range(t["count"]):
            L.append("%7d,%7d,%7d,'%-2s',1,2,1,%12s,%12s,2,'%-12s',1,   1,1.0000,   0,1.0000,   0,1.0000,   0,"
                     "1.0000,'%-12s'" % (_num_of(net, t["hv"]), _num_of(net, t["lv"]), _num_of(net, t["tv"]),
                                         str(c + 1), _g(t["mag_g"], 7), _g(t["mag_b"], 7), t["name"][:12],
                                         t["vg"]["text"][:12]))
            L.append("%12s,%12s,%10.3f,%12s,%12s,%10.3f,%12s,%12s,%10.3f,%9.5f,%10.4f"
                     % (_g(t["r12"], 7), _g(t["x12"], 7), t["s12"], _g(t["r23"], 7), _g(t["x23"], 7), t["s23"],
                        _g(t["r31"], 7), _g(t["x31"], 7), t["s31"], t.get("vstar", 1.0), t.get("astar", 0.0)))
            rates = _rabc3(t)
            for w in range(3):
                L.append("%10.7f,%10.4f,   0.000,%9.2f,%9.2f,%9.2f, 0,      0, 1.10000, 0.90000,1.10000,0.90000,  33, 0, "
                         "0.00000, 0.00000,  0.000" % ((t["windv"][w] / t["nomv"][w], t["nomv"][w]) + rates[w]))
    L.append("0 / END OF TRANSFORMER DATA, BEGIN AREA DATA")
    L.append("   1,%7d,     0.000,    10.000,'SMIB        '" % _num_of(net, "IB"))
    for sec in ("AREA DATA, BEGIN TWO-TERMINAL DC DATA", "TWO-TERMINAL DC DATA, BEGIN VSC DC LINE DATA",
                "VSC DC LINE DATA, BEGIN IMPEDANCE CORRECTION DATA",
                "IMPEDANCE CORRECTION DATA, BEGIN MULTI-TERMINAL DC DATA",
                "MULTI-TERMINAL DC DATA, BEGIN MULTI-SECTION LINE DATA", "MULTI-SECTION LINE DATA, BEGIN ZONE DATA"):
        L.append("0 / END OF " + sec)
    L.append("   1,'SMIB        '")
    L.append("0 / END OF ZONE DATA, BEGIN INTER-AREA TRANSFER DATA")
    L.append("0 / END OF INTER-AREA TRANSFER DATA, BEGIN OWNER DATA")
    L.append("   1,'SMIB        '")
    for sec in ("OWNER DATA, BEGIN FACTS DEVICE DATA", "FACTS DEVICE DATA, BEGIN SWITCHED SHUNT DATA",
                "SWITCHED SHUNT DATA, BEGIN GNE DATA", "GNE DATA, BEGIN INDUCTION MACHINE DATA",
                "INDUCTION MACHINE DATA"):
        L.append("0 / END OF " + sec)
    L.append("Q")
    _write_text(path, "\n".join(L))


# ============================================================================
# PSLF -- the .epc, laid out as PSLF 23 writes it (each record's fields and its continuation lines as in a PSLF 23
# export; what this model does not use left at PSLF's own defaults)
# ============================================================================
EPC_DATES = "  400101   391231"              # in service from 1940 to 2039: PSLF's own 'always'


def _pq(text, n):
    """A PSLF string: in double quotes, n characters."""
    return '"%-*s"' % (n, (text or "")[:n].replace('"', "'"))


def _pref(net, role):
    """A bus as a branch, transformer or generator record names it: number, name, kV -- or none (0)."""
    if role is None:
        return '      0 "            "   0.00'
    b = net.bus[role]
    return "%7d %s %6.2f" % (b["num"], _pq(b["name"], 12), b["kv"])


def _pf(v, fmt="%.8f"):
    """A number for PSLF, with its decimal point."""
    return fmt % v


def _tx_lines(net, t, hv, lv, tv, ck, z, ratio, rates):
    """A transformer's six lines: z [(r, x)] for ps, pt, ts in pu on the system base -- its tbase (as PSLF's own
       cases have it: the same whichever base PSLF's units flags name); ratio (tap, ftaps, ftapt); rates (rate1,
       rate2, rate3 of each winding)."""
    (rps, xps), (rpt, xpt), (rts, xts) = z
    tap, ftaps, ftapt = ratio
    gmag, bmag = t["mag_g"], t["mag_b"]
    kvt = net.kv(tv) if tv else 0.0
    tbases = (net.sb, net.sb if tv else 0.0, net.sb if tv else 0.0)
    return [
        '%s     %s  %s   " " :   1  1 %s  0 %s %s    1    1 %12.6f %.6e %.6e %.6e %.6e %.6e %.6e /' % (
            _pref(net, hv), _pref(net, lv), _pq(ck, 2), _pref(net, None), _pref(net, None), _pref(net, tv),
            tbases[0], rps, xps, rpt, xpt, rts, xts),
        ' %f %f %f  0.000000 %.6e %.6e %8.1f %6.1f %6.1f    0.0 0.000  %f  %f  1.100000  0.900000  0.006250  %f  '
        '1.000000  %f  %f %s   0 1     0.0    0.0    0.0    0.0 /' % (
            (net.kv(hv), net.kv(lv), kvt, gmag, bmag) + tuple(rates[0]) + (max(1.1, tap), min(0.9, tap), tap, ftaps,
                                                                             ftapt, EPC_DATES)),
        '   1 1.000   0 1.000   0 1.000   0 1.000   0 0.000   0 0.000   0 0.000   0 0.000  0 /',
        '   %f   %f  0.000000  0.000000 %8.1f %6.1f %6.1f %8.1f %6.1f %6.1f 0.000 0.000  0  0  0 0.0000 0.0000 0  '
        '0  0   0.000000   0.000000  0.000000  0.000000  " "   0.000000 /' % (
            (tbases[1], tbases[2]) + tuple(rates[1]) + tuple(rates[2])),
        "   " + "    ".join(["0.00"] * 13) + "/",
        "   " + "    ".join(["0.00"] * 9)]


def epc_gen(net, g, poi):
    """A generator's .epc values: (regulated bus role, qrf, qmax, qmin, gen_x) as raw_gen sets its .raw values."""
    qt, qb, _vs, ireg, rmp, zx, _wmod = raw_gen(net, g, poi)
    return ("POI" if ireg else g["bus"]), rmp / 100.0, qt, qb, zx


def write_epc(net, path):
    sb = net.sb
    poi = _num_of(net, "POI")
    L = ["# history file date %s" % time.strftime("%a %b %d %H:%M:%S %Y"),
         "# present file date %s" % time.strftime("%a %b %d %H:%M:%S %Y"),
         "# Version 23.1.1", "# Exported to Version 23",
         "title", ("%s %s" % (net.model, net.name))[:80],
         ("POI %.1f kV  SCR %s  X/R %s -- smib_build.py %s" % (
             net.kv("POI"), _g(net.thev["scr"], 4) if net.thev["scr"] else "-", _g(net.thev["x_r"], 4),
             time.strftime("%Y-%m-%d %H:%M")))[:80],
         "!", "comments", "generic WECC models: placeholders until the vendor's (the .dyd)", "!",
         "solution parameters",
         "tap    1    tcul  enabled/disabled", "phas   0    ps    enabled/disabled",
         "area   0    area  enabled/disabled", "svd    1    svd   enabled/disabled",
         "dctap  1    dc    enabled/disabled", "gcd    0    gcd   enabled/disabled",
         "jump  0.000100    jumper threshold", "toler   0.1000    newton tolerance",
         "sbase %8.4f    system mva base" % sb, "!"]
    gens = [(g, epc_gen(net, g, poi)) for g in net.gens]
    held = set(e[0] for g, e in gens if e[0] != g["bus"])      # remote buses the generators hold
    L.append("bus data  [%5d]               ty  vsched   volt     angle    ar zone  vmax   vmin   date_in date_out "
             "pid L own st  latitude  longitude island    sdmon   vmax1      vmin1      dvmax subst" % len(net.bus))
    gen_buses = set(g["bus"] for g in net.gens)
    for role, b in net.bus.items():
        ty = 0 if role == "IB" else (2 if role in gen_buses else 1)
        vs = net.ctl["v_poi"] if role in held else b["vm"]
        L.append('%7d %s %9.4f " "  0  :  %d %.8f %.8f %.8f    1    1 1.1000 0.9000 %s   0 0   1 0   0.000000   '
                 '0.000000     0 0    1.10000    0.90000    0.00000 -1 " "     0 -1 " "  0  " " '
                 % (b["num"], _pq(b["name"], 12), b["kv"], ty, vs, b["vm"], b["va"], EPC_DATES))
    nbr = sum(ln["count"] for ln in net.lines)
    L.append("branch data  [%5d]                                          ck  se  ------------long_id_------------    "
             "st resist   react   charge   rate1  rate2  rate3  rate4 aloss  lngth " % nbr)
    for ln in net.lines:
        for c in range(ln["count"]):
            L.append('%s     %s  %s   1 " "  :  1 %s %s %s %7.2f %7.2f %7.2f    0.00 0.000    0.000 /' % (
                (_pref(net, ln["fr"]), _pref(net, ln["to"]), _pq(str(c + 1), 2), _pf(ln["r"]), _pf(ln["x"]),
                 _pf(ln["b"])) + _rabc(ln)))
            L.append('   1    1  0.000000  0.000000  0.000000 %s   0 1  0    0.00    0.00    0.00    0.00   1 1.000   '
                     '0 0.000   0 0.000   0 0.000   0 0.000   0 0.000   0 0.000   0 0.000  0 0  0  0  0  0.000   '
                     '0.000000  0.000000  " "     0.00    0.00    0.00    0.00 ' % EPC_DATES)
    ntx = sum(t["count"] for t in net.tx2) + sum(t["count"] for t in net.tx3)
    L.append("transformer data  [%4d]                                     ck   ------------long_id_------------     st "
             "ty --no---    reg_name          zt         int                           tert                      "
             "ar zone  tbase   ps_r    ps_x    pt_r    pt_x    ts_r    ts_x" % ntx)
    for t in net.tx2:
        t1, t2 = t["windv1"] / net.kv(t["hv"]), t["windv2"] / net.kv(t["lv"])
        k = sb / t["mva"] * t2 * t2                 # one tap, on the HV side: t1 / t2; the impedance t2^2 its own
        for c in range(t["count"]):
            L.extend(_tx_lines(net, t, t["hv"], t["lv"], None, str(c + 1),
                               [(t["r"] * k, t["x"] * k), (0.0, 0.0), (0.0, 0.0)], (t1 / t2, 1.0, 1.0),
                               (_rabc(t), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))))
    for t in net.tx3:
        tk = [t["windv"][w] / net.kv(role) for w, role in enumerate((t["hv"], t["lv"], t["tv"]))]
        z = [(t["r" + p] * sb / t["s" + p], t["x" + p] * sb / t["s" + p]) for p in ("12", "31", "23")]
        for c in range(t["count"]):
            L.extend(_tx_lines(net, t, t["hv"], t["lv"], t["tv"], str(c + 1), z, (tk[0], tk[1], tk[2]),
                               _rabc3(t)))
    L.append("generator data  [%4d]         id   ------------long_id_------------    st ---no--     reg_name           "
             "prf  qrf  ar zone   pgen   pmax   pmin   qgen   qmax   qmin   mbase  cmp_r cmp_x gen_r gen_x           "
             "hbus                         tbus                date_in date_out pid N" % (len(net.gens) + 1))
    s = next(f["s2"] for f in net.op["flows"] if f["kind"] == "GRID")      # what the infinite bus takes in
    rows = [("IB", IB_ID, "IB", 1.0, s.real, 99999.0, -99999.0, s.imag, 99999.0, -99999.0, IB_MBASE, IB_XSOURCE)]
    for g, (reg, qrf, qmax, qmin, zx) in gens:
        rows.append((g["bus"], g["id"], reg, qrf, g["p"], g["pmax"], g["pmin"], g["q"], qmax, qmin, g["mbase"], zx))
    for bus, gid, reg, qrf, p, pmax, pmin, q, qmax, qmin, mb, zx in rows:
        L.append('%s %s  " " :  1 %s  1.000000  %f   1    1 %f %f %f %f %f %f %.4f 0.000 0.000 0.000 %.3f %s %s %s '
                 '  0 1 /' % (_pref(net, bus), _pq(gid, 2), _pref(net, reg), qrf, p, pmax, pmin, q, qmax, qmin, mb, zx,
                             _pref(net, None), _pref(net, None), EPC_DATES))
        L.append(' 0.0000 0.0000 1.0000   1 1.000   0 0.000   0 0.000   0 0.000   0 0.000   0 0.000   0 0.000   0 '
                 '0.000    0   0   0   0   0   0   0    0.0 0 0  0.000000    0  " "  0  0   0   0   0   0   0.000000 /')
        L.append('  0   0  0.000000   0  0.000000   0.000000  " "     0     0   0.000000   0.000000 ')
    ids, loads = collections.defaultdict(int), []
    for x in net.loads + net.shunts:                     # a fixed shunt: a constant-impedance load, ids S1, S2 ...
        sh = "mw" not in x
        ids[(x["bus"], sh)] += 1
        loads.append((x["bus"], ("S%d" if sh else "%d") % ids[(x["bus"], sh)], 0.0 if sh else x["mw"],
                      -x["mvar"] if sh else x["mvar"]))
    L.append("load data  [%4d]             id   ------------long_id_------------     st      mw      mvar    mw_i    "
             "mvar_i  mw_z      mvar_z  ar zone  date_in date_out pid N own M nonc  thr_bus flg" % len(loads))
    for bus, lid, mw, mvar in loads:
        L.append('%s %s   " " :  1 0.000000 0.000000 0.000000 0.000000 %f %f   1    1 %s   0 1   1 0    0 %s   0 " "'
                 '    0  0 %s "  " 0.000000 0.000000 0 0 0.000000 0.000000 0 0  0.000000  " "  ' % (
                     _pref(net, bus), _pq(lid, 2), mw, mvar, EPC_DATES, _pref(net, None), _pref(net, None)) +
                 " ".join(["0.000000"] * 11) + " 0")
    L += ["zone data  [   1]                            pnet     qnet",
          '    1 %s    0.000    0.000 0 ' % _pq("SMIB", 32),
          "owner data  [    1]    owner name           s name       net_mw   net_mvar     sch_mw   sch_mvar  ar M",
          '       1 %s %s :      0.000      0.000      0.000      0.000   0 0 ' % (_pq("SMIB", 32), _pq("SMIB", 4)),
          "end"]
    _write_text(path, "\n".join(L) + "\n")


# PSLF .dyd: each PSS/E model of the .dyr as the PSLF model it stands for, its values in PSLF's parameter order
# (positional records, as PSLF 24 writes them): a name takes that PSS/E parameter's value, a number is PSLF's own
# setting. The orders are those of PSLF's dyd templates and of PSLF cases of plants with these models.
_PSLF_VDL = ["Vq1", "Iq1", "Vq2", "Iq2", "Vq3", "Iq3", "Vq4", "Iq4", "Vp1", "Ip1", "Vp2", "Ip2", "Vp3", "Ip3", "Vp4",
             "Ip4"]
_PSLF_VDL10 = ["%s%d" % (n, k) for vi in (("Vq", "Iq"), ("Vp", "Ip")) for k in range(1, 11) for n in vi]
_LHVRT = ["vref"] + ["dvtrp%d" % k for k in range(1, 11)] + ["dttrp%d" % k for k in range(1, 11)] + ["alarm"]
_LHFRT = ["fref"] + ["dftrp%d" % k for k in range(1, 11)] + ["dttrp%d" % k for k in range(1, 11)] + ["alarm"]
PSLF_DYD = {
    #           PSLF name  header  parameters
    "GENCLS": ("gencls", "mva", ["H", "D", 0.0, "_XS", 0.0, 0.0]),             # h d ra lppd rcomp xcomp
    "REGCA1": ("regc_a", "mva", ["LVPLSW", "Rrpwr", "Brkpt", "Zerox", "Lvpl1", "Volim", "Lvpnt1", "Lvpnt0", "Iolim",
                                 "Accel", "Tg", "Tfltr", "Iqrmax", "Iqrmin", 0.0]),
    "REECA1": ("reec_a", None, [0.0, "Vdip", "Vup", "Trv", "dbd1", "dbd2", "Kqv", "Iqh1", "Iql1", "Vref0", "Iqfrz",
                                "Thld", "Thld2", "Tp", "QMax", "QMin", "VMAX", "VMIN", "Kqp", "Kqi", "Kvp", "Kvi",
                                "Vref1", "Tiq", "dPmax", "dPmin", "PMAX", "PMIN", "Imax", "Tpord", "PFFLAG", "VFLAG",
                                "QFLAG", "PFLAG", "PQFLAG"] + _PSLF_VDL),
    "REECCU1": ("reec_c", None, [0.0, "Vdip", "Vup", "Trv", "dbd1", "dbd2", "Kqv", "Iqh1", "Iql1", "Vref0", "SOCini",
                                 "SOCmax", "SOCmin", "T", "Tp", "QMax", "QMin", "VMAX", "VMIN", "Kqp", "Kqi", "Kvp",
                                 "Kvi", "Tiq", "dPmax", "dPmin", "PMAX", "PMIN", "Imax", "Tpord", "PFFLAG", "VFLAG",
                                 "QFLAG", "PQFLAG"] + _PSLF_VDL),
    "REPCA1": ("repc_a", "branch", [0.0, "Tfltr", "Kp", "Ki", "Tft", "Tfv", "REFFLAG", "Vfrz", "Rc", "Xc", "Kc",
                                    "VCFLAG", "emax", "emin", "_DBD", "Qmax", "Qmin", "Kpg", "Kig", "Tp", "fdbd1",
                                    "fdbd2", "femax", "femin", "Pmax", "Pmin", "Tlag", "Ddn", "Dup", "FFLAG", 0.0,
                                    0.0]),                       # ... frqflg outflag puflag (WECC's base cases: 0 0)
    "GENROU": ("genrou", "mva", ["Tdop", "Tdopp", "Tqop", "Tqopp", "H", "D", "Xd", "Xq", "Xdp", "Xqp", "Xdpp", "Xl",
                                 "S1", "S12", 0.0, 0.0, 0.0, 0.0]),
    "SEXS": ("sexs", None, ["TA_TB", "TB", "K", "TE", "EMIN", "EMAX", 1.0, 0.0, -999.0, 999.0, 0.0]),
    "TGOV1": ("tgov1", "mwcap", ["R", "T1", "VMAX", "VMIN", "T2", "T3", "Dt"]),
    "GENSAL": ("gensal", "mva", ["Tdop", "Tdopp", "Tqopp", "H", "D", "Xd", "Xq", "Xdp", "Xdpp", "Xl", "S1", "S12", 0.0,
                                 0.0, 0.0, 0.0]),                            # .. ra rcomp xcomp accel
    "GAST": ("gast", "mwcap", ["R", "T1", "T2", "T3", "AT", "KT", "VMAX", "VMIN", "Dturb"]),       # AT: PSLF's lmax
    # PSLF's reec_d: its six flags (vcmpflag first) after tpord, then rc xc tr1 kc ke, vblkh before vblkl, tblkdelay,
    # the two ten-point VDL tables
    "REECDU1": ("reec_d", None, [0.0, "Vdip", "Vup", "Trv", "dbd1", "dbd2", "Kqv", "Iqh1", "Iql1", "Vref0", "Iqfrz",
                                 "Thld", "Thld2", "Tp", "QMax", "QMin", "VMAX", "VMIN", "Kqp", "Kqi", "Kvp", "Kvi",
                                 "Vref1", "Tiq", "dPmax", "dPmin", "PMAX", "PMIN", "Imax", "Tpord", "VCMPFLAG",
                                 "PFFLAG", "VFLAG", "QFLAG", "PFLAG", "PQFLAG", "Rc", "Xc", "Tr1", "Kc", "Ke", "Vblkh",
                                 "Vblkl", "Tblk_delay"] + _PSLF_VDL10),
    # PSLF's regc_b: re + j xe its source impedance -- the generator's ZSORCE, as in the .epc
    "REGCBU1": ("regc_b", "mva", ["Tfltr", "Tg", "Te", "Rrpwr", "_RE", "_XE", "Iqrmax", "Iqrmin", "RateFlag", "Imax",
                                  "PQFLAG"]),
    "LHVRT": ("lhvrt", "watch", _LHVRT),
    "LHFRT": ("lhfrt", "watch", _LHFRT),
    # type 3 wind (the orders GridDyn's PSLF reader takes): wtgt_a's turbine and generator inertias and shaft
    # stiffness from WTDTA1's H, Htfrac and Freq1
    "WTDTA1": ("wtgt_a", None, [0.0, lambda v: v["H"] * v["Htfrac"], lambda v: v["H"] * (1.0 - v["Htfrac"]), "Dshaft",
                                lambda v: 2.0 * v["Htfrac"] * (1.0 - v["Htfrac"]) * v["H"] * v["Freq1"] ** 2]),
    "WTARA1": ("wtga_a", None, [0.0, "Ka", "Theta0"]),
    "WTPTA1": ("wtgp_a", None, [0.0, "Kiw", "Kpw", "Kic", "Kpc", "Kcc", "Tp", "TetaMax", "TetaMin", "RTetaMax",
                                "RTetaMin"]),
    "WTTQA1": ("wtgq_a", None, [0.0, "Kip", "Kpp", "Tp", "Twref", "Temax", "Temin", "p1", "spd1", "p2", "spd2", "p3",
                                "spd3", "p4", "spd4", "TFLAG"]),
}
PSLF_NAME = {"IEEET1": "ieeet1", "ESST1A": "esst1a", "EXST1": "exst1", "HYGOV": "hygov",   # PSLF has them; their
             "IEEEG1": "ieeeg1"}                                          # parameter order not here yet
PSLF_DYD["REGCAU1"], PSLF_DYD["REPCAU1"] = PSLF_DYD["REGCA1"], PSLF_DYD["REPCA1"]


def _dv(v):
    """A .dyd value: short, its decimal point kept."""
    t = "%.6g" % float(v)
    return t if any(c in t for c in ".en") else t + ".0"


def _dbus(net, num):
    """A bus as a .dyd record names it: number "name" kV."""
    role = next((r for r, b in net.bus.items() if b["num"] == int(num)), None)
    b = net.bus[role]
    return "%7d %s %7.2f" % (b["num"], _pq(b["name"], 12), b["kv"])


def pslf_records(net, recs):
    """[(generator name | IB | PLANT, PSS/E model, its .dyd record, "" where it has none of its own, or None)]: None
       where PSLF has no model here. REPC_B (PLNTBU1 + a REAX4BU1 on each generator) goes as a repc_a on each of its
       generators -- PLNTBU1's settings, the generator's own limits (REAX4BU1's) -- holding the same bus through the
       same branch, as WECC cases have several inverters on one POI."""
    gen_of = dict((g["name"], g) for g in net.gens)
    plant = dict(next((v for n, m, v in recs if m == "PLNTBU1"), []))
    out = []
    for gname, model, vals in recs:
        if model == "PLNTBU1":
            out.append((gname, model, ""))
            continue
        if model == "REAX4BU1" and plant:
            vals = list(dict(plant, **dict((k, x) for k, x in vals if k in ("Qmax", "Qmin", "Pmax", "Pmin"))).items())
            model = "REPCA1"
        spec = PSLF_DYD.get(model)
        if spec is None:
            out.append((gname, model, None))
            continue
        name, head, order = spec
        v = dict(vals)
        if gname == "IB":
            bus, gid, mva = _num_of(net, "IB"), IB_ID, IB_MBASE
            v["H"] = v["H"] or 9999.0                  # PSS/E's H 0 (an infinite bus): a very large H for PSLF
            v["_XS"] = IB_XSOURCE
        else:
            g = gen_of[gname]
            bus, gid, mva = _num_of(net, g["bus"]), g["id"], g["mbase"]
            v["_RE"], v["_XE"] = 0.0, g.get("zx", IBR_ZSORCE_X)
        rec = "%-8s %s %s" % (name, _dbus(net, bus), '"%s"' % gid)
        if head == "branch":                          # the bus it holds, then the branch it measures
            rec += "  %s !  !  %s %s %s" % (_dbus(net, v["BUSR"] or bus), _dbus(net, v["BUS1"]), _dbus(net, v["BUS2"]),
                                             '"%s"' % v["CKT"])
            v["_DBD"] = max(abs(float(v["dbd1"])), abs(float(v["dbd2"])))
        if head == "watch":                           # the bus whose voltage / frequency it watches
            rec += "  %s" % _dbus(net, bus)
        rec += " : #9"
        if head == "mva":
            rec += " mva=%s" % _dv(mva)
        elif head == "mwcap":
            rec += " mwcap=%s" % _dv(mva)
        words = [_dv(x(v) if callable(x) else (v[x] if isinstance(x, str) else x)) for x in order]
        lines, cur = [], rec
        for k, w in enumerate(words):
            if k and len(cur) + len(w) > 118:
                lines.append(cur + " /")
                cur = " "
            cur += " " + w
        out.append((gname, model, "\n".join(lines + [cur])))
    return out


def write_dyd(net, recs, path):
    L = ["#   dynamics data written %s by smib_build.py" % time.strftime("%a %b %d %H:%M:%S %Y"),
         "#   %s %s: generic WECC models, placeholders until the vendor's" % (net.model, net.name), "#", "models"]
    for gname, model, rec in pslf_records(net, recs):
        if rec == "":
            continue
        if rec is None:
            pn = PSLF_NAME.get(model, model)
            L.append("# %s: %s (PSS/E %s) not written -- add it by hand" % (gname, pn, model))
            fam = next((g.get("family", g["name"]) for g in net.gens if g["name"] == gname), gname)
            note = "CHECK: PSLF .dyd: %s %s (PSS/E %s) not written -- add it by hand" % (fam, pn, model)
            if note not in net.notes:
                net.notes.append(note)
        else:
            L.append(rec)
    _write_text(path, "\n".join(L) + "\n")


# ============================================================================
# PSS/E -- the .dyr
# ============================================================================
# The models -- WECC-approved only (WECC Approved Dynamic Model List): REGC_A, REEC_A, REEC_C, REEC_D, REPC_A,
# REPC_B, the type 3 wind models (WTGT_A, WTGAR_A, WTGPT_A, WTGTRQ_A), GENROU, SEXS, TGOV1, GENCLS.
#   inverters  REGCA1 (REGC_A) + the generator's REEC + its part of the plant controller:
#                REEC_A  REECA1    PV, WT4, WT3     REPC_A  REPCA1 on the generator
#                REEC_C  REECCU1   BESS             REPC_B  REAX4BU1 on the generator and one PLNTBU1 (a USRBUS
#                REEC_D  REECDU1   PV, BESS, WT4            record) at the POI for the plant
#              REGCAU1 / REPCAU1 for REGCA1 / REPCA1 with Plant 'REGC_A / REPC_A records' USRMDL
#   WT3        + WTDTA1 WTARA1 WTPTA1 WTTQA1        SYNC  its Machine, Exciter and Governor models (blank:
#                                                         GENROU + SEXS + TGOV1; GENSAL, IEEET1, ESST1A, EXST1,
#                                                         HYGOV, GAST, IEEEG1, or NONE for exciter / governor)
# REECCU1, REECDU1, REAX4BU1, PLNTBU1, REGCAU1, REPCAU1 are user models (USRMDL / USRBUS): the WECC user-model DLL
# must be loaded (ADDLIB_IDV / DLLS).
# model: (USRMDL / USRBUS header IC IT NI NC NS NV, or None for a standard record; number of ICONs; names in record
# order). ICONs are written as integers ('CKT' quoted). REGCAU1 / REECCU1 / REPCAU1: PSS/E 34's layouts, as in the
# BESS template of the study engine (z7_spp_p.py). REECDU1, PLNTBU1 and REAX4BU1: the layouts of two PSS/E .dyr
# files of real plants (a REGC + REECDU1 + REPCA1 plant; a hybrid PV + BESS plant with PLNTBU1 + REAX4BU1), read so:
#   REECDU1   ICONs: the five REEC flags and VCMPFLAG. CONs 1-29 in REECA1's order (QMax QMin at 14-15 and PMAX PMIN
#             at 26-27 are the plant's limits in those files), 30-69 the ten-point VDL tables (Vq1 Iq1 .. Vq10 Iq10,
#             Vp1 Ip1 .. Vp10 Ip10), 70-77 REEC_D's own: Rc Xc Tr1 Kc Ke Vblkl Vblkh Tblk_delay (the files' BESS has
#             Ke -1 there and blocks below 0.5 / above 1.1 pu)
#   PLNTBU1   (REPC_B) ICONs and CONs 1-27 in REPCA1's order; CON 28 the MVA base of its limits: the sum of the
#             MBASEs it commands (its Qmax = sum of Qmax / that base ...)
#   REAX4BU1  ICON: the PLNTBU1 bus; CONs 2, 3: the generator's share of the plant's Q and P (its Qmax and Pmax over
#             the sums), 4-7: its Qmax Qmin Pmax Pmin (pu on MBASE); CON 1 (0 in both files) by position
# Names read from position -- check them against the PSS/E Model Library. Dyr_Params also takes ICONn / CONn.
_REGCA = ["LVPLSW", "Tg", "Rrpwr", "Brkpt", "Zerox", "Lvpl1", "Volim", "Lvpnt1", "Lvpnt0", "Iolim", "Tfltr", "Khv",
          "Iqrmax", "Iqrmin", "Accel"]
_REPCA = ["BUSR", "BUS1", "BUS2", "CKT", "VCFLAG", "REFFLAG", "FFLAG",
          "Tfltr", "Kp", "Ki", "Tft", "Tfv", "Vfrz", "Rc", "Xc", "Kc", "emax", "emin", "dbd1", "dbd2", "Qmax", "Qmin",
          "Kpg", "Kig", "Tp", "fdbd1", "fdbd2", "femax", "femin", "Pmax", "Pmin", "Tlag", "Ddn", "Dup"]
_REECA = ["Vdip", "Vup", "Trv", "dbd1", "dbd2", "Kqv", "Iqh1", "Iql1", "Vref0", "Iqfrz", "Thld", "Thld2", "Tp", "QMax",
          "QMin", "VMAX", "VMIN", "Kqp", "Kqi", "Kvp", "Kvi", "Vref1", "Tiq", "dPmax", "dPmin", "PMAX", "PMIN", "Imax",
          "Tpord"]
_VDL = ["Vq1", "Iq1", "Vq2", "Iq2", "Vq3", "Iq3", "Vq4", "Iq4", "Vp1", "Ip1", "Vp2", "Ip2", "Vp3", "Ip3", "Vp4", "Ip4"]
_VDL10 = ["%s%d" % (n, k) for vi in (("Vq", "Iq"), ("Vp", "Ip")) for k in range(1, 11) for n in vi]
DYR_LIB = collections.OrderedDict([
    ("GENCLS", (None, 0, ["H", "D"])),
    ("REGCA1", (None, 1, _REGCA)),
    ("REECA1", (None, 6, ["BUSR", "PFFLAG", "VFLAG", "QFLAG", "PFLAG", "PQFLAG"] + _REECA + _VDL)),
    ("REPCA1", (None, 7, _REPCA)),
    ("REGCAU1", ((101, 1, 1, 14, 3, 4), 1, _REGCA)),
    ("REGCBU1", ((101, 1, 2, 7, 5, 8), 2, ["RateFlag", "PQFLAG", "Tg", "Tfltr", "Iqrmax", "Iqrmin", "Rrpwr", "Te",
                                           "Imax"])),
    ("REECCU1", ((102, 0, 5, 45, 7, 6), 5, ["BUSR", "PFFLAG", "VFLAG", "QFLAG", "PQFLAG",
                                             "Vdip", "Vup", "Trv", "dbd1", "dbd2", "Kqv", "Iqh1", "Iql1", "Vref0", "Tp",
                                             "QMax", "QMin", "VMAX", "VMIN", "Kqp", "Kqi", "Kvp", "Kvi", "Tiq", "dPmax",
                                             "dPmin", "PMAX", "PMIN", "Imax", "Tpord"] + _VDL +
                                            ["T", "SOCini", "SOCmax", "SOCmin"])),
    ("REECDU1", ((102, 0, 6, 77, 7, 20), 6, ["PFFLAG", "VFLAG", "QFLAG", "PFLAG", "PQFLAG", "VCMPFLAG"] + _REECA +
                                             _VDL10 + ["Rc", "Xc", "Tr1", "Kc", "Ke", "Vblkl", "Vblkh", "Tblk_delay"])),
    ("REPCAU1", ((107, 0, 7, 27, 7, 9), 7, _REPCA)),
    ("REAX4BU1", ((107, 0, 1, 7, 2, 4), 1, ["PCBUS", "CON1", "Kz", "Kw", "Qmax", "Qmin", "Pmax", "Pmin"])),
    ("PLNTBU1", ((504, 0, 7, 28, 7, 15), 7, _REPCA + ["MVAbase"])),
    ("WTDTA1", (None, 0, ["H", "DAMP", "Htfrac", "Freq1", "Dshaft"])),
    ("WTARA1", (None, 0, ["Ka", "Theta0"])),
    ("WTPTA1", (None, 0, ["Kiw", "Kpw", "Kic", "Kpc", "Kcc", "Tp", "TetaMax", "TetaMin", "RTetaMax", "RTetaMin"])),
    ("WTTQA1", (None, 1, ["TFLAG", "Kpp", "Kip", "Tp", "Twref", "Temax", "Temin", "p1", "spd1", "p2", "spd2", "p3",
                          "spd3", "p4", "spd4", "TRATE"])),
    ("GENROU", (None, 0, ["Tdop", "Tdopp", "Tqop", "Tqopp", "H", "D", "Xd", "Xq", "Xdp", "Xqp", "Xdpp", "Xl", "S1",
                          "S12"])),
    ("SEXS", (None, 0, ["TA_TB", "TB", "K", "TE", "EMIN", "EMAX"])),
    ("TGOV1", (None, 0, ["R", "T1", "VMAX", "VMIN", "T2", "T3", "Dt"])),
    ("GENSAL", (None, 0, ["Tdop", "Tdopp", "Tqopp", "H", "D", "Xd", "Xq", "Xdp", "Xdpp", "Xl", "S1", "S12"])),
    ("IEEET1", (None, 0, ["TR", "KA", "TA", "VRMAX", "VRMIN", "KE", "TE", "KF", "TF", "Switch", "E1", "SE1", "E2",
                          "SE2"])),
    ("ESST1A", (None, 2, ["UEL", "VOS", "TR", "VIMAX", "VIMIN", "TC", "TB", "TC1", "TB1", "KA", "TA", "VAMAX",
                          "VAMIN", "VRMAX", "VRMIN", "KC", "KF", "TF", "KLR", "ILR"])),
    ("EXST1", (None, 0, ["TR", "VIMAX", "VIMIN", "TC", "TB", "KA", "TA", "VRMAX", "VRMIN", "KC", "KF", "TF"])),
    ("HYGOV", (None, 0, ["R", "r", "Tr", "Tf", "Tg", "VELM", "GMAX", "GMIN", "TW", "At", "Dturb", "qNL"])),
    ("GAST", (None, 0, ["R", "T1", "T2", "T3", "AT", "KT", "VMAX", "VMIN", "Dturb"])),
    ("IEEEG1", (None, 2, ["IBUS", "M", "K", "T1", "T2", "T3", "Uo", "Uc", "PMAX", "PMIN", "T4", "K1", "K2", "T5", "K3",
                          "K4", "T6", "K5", "K6", "T7", "K7", "K8"])),
    ("LHVRT", (None, 0, _LHVRT)),
    ("LHFRT", (None, 0, _LHFRT)),
])
# the ride-through relays, set as PSLF's lhvrt / lhfrt (a reference and up to ten trip points: a deviation from it
# and a time): in the .dyd as they are, in the .dyr as one VTGTPAT / FRQTPAT (a generator trip relay of one band)
# per trip point, its other side open
RELAYS = {"LHVRT": ("VTGTPAT", "vref", "dvtrp", 99.0), "LHFRT": ("FRQTPAT", "fref", "dftrp", 99.0)}
PSLF_ONLY = tuple(RELAYS)
DYR_USRBUS = ("PLNTBU1",)                  # bus-level user models: 'USRBUS' records, no machine
DYR_ALIAS = {"TG": "Tlag", "VBIAS": "Vref1", "IQHL": "Iqh1", "IQLL": "Iql1", "TA/TB": "TA_TB", "TATB": "TA_TB"}

# generic values -- placeholders until the vendor's (every one can be set in Dyr_Params). REPCA1 Kp 1 / Ki 5:
# settles after a POI fault at SCR 3 with two plant controllers on one POI; the WECC example's Kp 18 does not.
DYN_DEFAULTS = {
    "GENCLS": {"H": 0.0, "D": 0.0},
    "REGCA1": {"LVPLSW": 1, "Tg": 0.02, "Rrpwr": 10.0, "Brkpt": 0.9, "Zerox": 0.4, "Lvpl1": 1.22, "Volim": 1.2,
               "Lvpnt1": 0.8, "Lvpnt0": 0.4, "Iolim": -1.3, "Tfltr": 0.02, "Khv": 0.7, "Iqrmax": 99.0,
               "Iqrmin": -99.0, "Accel": 0.7},
    "REECA1": {"BUSR": 0, "PFFLAG": 0, "VFLAG": 0, "QFLAG": 0, "PFLAG": 0, "PQFLAG": 0,
               "Vdip": 0.9, "Vup": 1.1, "Trv": 0.02, "dbd1": -0.05, "dbd2": 0.05, "Kqv": 2.0, "Iqh1": 1.05,
               "Iql1": -1.05, "Vref0": 0.0, "Iqfrz": 0.0, "Thld": 0.0, "Thld2": 0.0, "Tp": 0.02, "QMax": 0.4,
               "QMin": -0.4, "VMAX": 1.1, "VMIN": 0.9, "Kqp": 0.0, "Kqi": 0.1, "Kvp": 0.0, "Kvi": 40.0,
               "Vref1": 0.0, "Tiq": 0.02, "dPmax": 99.0, "dPmin": -99.0, "PMAX": 1.0, "PMIN": 0.0, "Imax": 1.1,
               "Tpord": 0.02, "Vq1": 0.2, "Iq1": 1.1, "Vq2": 0.5, "Iq2": 1.1, "Vq3": 0.8, "Iq3": 1.1, "Vq4": 1.2,
               "Iq4": 1.1, "Vp1": 0.2, "Ip1": 1.1, "Vp2": 0.5, "Ip2": 1.1, "Vp3": 0.8, "Ip3": 1.1, "Vp4": 1.2,
               "Ip4": 1.1},
    "REPCA1": {"VCFLAG": 1, "REFFLAG": 1, "FFLAG": 1,
               "Tfltr": 0.02, "Kp": 1.0, "Ki": 5.0, "Tft": 0.0, "Tfv": 0.075, "Vfrz": 0.7, "Rc": 0.0, "Xc": 0.0,
               "Kc": 0.0, "emax": 0.1, "emin": -0.1, "dbd1": 0.0, "dbd2": 0.0, "Qmax": 0.4, "Qmin": -0.4,
               "Kpg": 0.1, "Kig": 0.05, "Tp": 0.25, "fdbd1": -0.0006, "fdbd2": 0.0006, "femax": 999.0,
               "femin": -999.0, "Pmax": 1.0, "Pmin": 0.0, "Tlag": 0.1, "Ddn": 20.0, "Dup": 0.0},
    "REECCU1": {"BUSR": 0, "PFFLAG": 0, "VFLAG": 0, "QFLAG": 0, "PQFLAG": 0,
                "Vdip": 0.9, "Vup": 1.1, "Trv": 0.02, "dbd1": -0.05, "dbd2": 0.05, "Kqv": 2.0, "Iqh1": 1.05,
                "Iql1": -1.05, "Vref0": 0.0, "Tp": 0.02, "QMax": 0.4, "QMin": -0.4, "VMAX": 1.1, "VMIN": 0.9,
                "Kqp": 0.0, "Kqi": 0.1, "Kvp": 0.0, "Kvi": 40.0, "Tiq": 0.02, "dPmax": 99.0, "dPmin": -99.0,
                "PMAX": 1.0, "PMIN": -1.0, "Imax": 1.1, "Tpord": 0.02, "Vq1": 0.2, "Iq1": 1.1, "Vq2": 0.5, "Iq2": 1.1,
                "Vq3": 0.8, "Iq3": 1.1, "Vq4": 1.2, "Iq4": 1.1, "Vp1": 0.2, "Ip1": 1.1, "Vp2": 0.5, "Ip2": 1.1,
                "Vp3": 0.8, "Ip3": 1.1, "Vp4": 1.2, "Ip4": 1.1, "T": 14400.0, "SOCini": 0.5, "SOCmax": 1.0,
                "SOCmin": 0.0},
    "REAX4BU1": {"CON1": 0.0, "Kz": 1.0, "Kw": 1.0, "Qmax": 0.4, "Qmin": -0.4, "Pmax": 1.0, "Pmin": 0.0},
    "WTDTA1": {"H": 3.0, "DAMP": 0.0, "Htfrac": 0.92, "Freq1": 1.8, "Dshaft": 1.5},
    "WTARA1": {"Ka": 0.007, "Theta0": 0.0},
    "WTPTA1": {"Kiw": 25.0, "Kpw": 150.0, "Kic": 30.0, "Kpc": 3.0, "Kcc": 0.0, "Tp": 0.3, "TetaMax": 27.0,
               "TetaMin": 0.0, "RTetaMax": 10.0, "RTetaMin": -10.0},
    "WTTQA1": {"TFLAG": 0, "Kpp": 3.0, "Kip": 0.6, "Tp": 0.05, "Twref": 60.0, "Temax": 1.2, "Temin": 0.0,
               "p1": 0.2, "spd1": 0.58, "p2": 0.4, "spd2": 0.72, "p3": 0.6, "spd3": 0.86, "p4": 0.8, "spd4": 1.0,
               "TRATE": 0.0},
    "GENROU": {"Tdop": 6.0, "Tdopp": 0.05, "Tqop": 0.75, "Tqopp": 0.07, "H": 4.0, "D": 0.0, "Xd": 1.8, "Xq": 1.75,
               "Xdp": 0.3, "Xqp": 0.55, "Xdpp": 0.25, "Xl": 0.15, "S1": 0.1, "S12": 0.3},
    "SEXS": {"TA_TB": 0.1, "TB": 10.0, "K": 100.0, "TE": 0.05, "EMIN": 0.0, "EMAX": 5.0},
    "TGOV1": {"R": 0.05, "T1": 0.5, "VMAX": 1.05, "VMIN": 0.0, "T2": 1.0, "T3": 1.0, "Dt": 0.0},
    "GENSAL": {"Tdop": 5.0, "Tdopp": 0.05, "Tqopp": 0.1, "H": 3.0, "D": 0.0, "Xd": 1.0, "Xq": 0.6, "Xdp": 0.3,
               "Xdpp": 0.2, "Xl": 0.15, "S1": 0.1, "S12": 0.3},
    "IEEET1": {"TR": 0.0, "KA": 400.0, "TA": 0.04, "VRMAX": 7.3, "VRMIN": -7.3, "KE": 1.0, "TE": 0.8, "KF": 0.03,
               "TF": 1.0, "Switch": 0, "E1": 3.0, "SE1": 0.1, "E2": 4.0, "SE2": 0.3},
    "ESST1A": {"UEL": 1, "VOS": 1, "TR": 0.02, "VIMAX": 999.0, "VIMIN": -999.0, "TC": 1.0, "TB": 1.0, "TC1": 0.0,
               "TB1": 0.0, "KA": 210.0, "TA": 0.0, "VAMAX": 999.0, "VAMIN": -999.0, "VRMAX": 6.43, "VRMIN": -6.0,
               "KC": 0.038, "KF": 0.0, "TF": 1.0, "KLR": 4.54, "ILR": 4.4},
    "EXST1": {"TR": 0.02, "VIMAX": 0.2, "VIMIN": -0.2, "TC": 1.0, "TB": 10.0, "KA": 200.0, "TA": 0.02, "VRMAX": 5.0,
              "VRMIN": -5.0, "KC": 0.05, "KF": 0.0, "TF": 1.0},
    "HYGOV": {"R": 0.05, "r": 0.3, "Tr": 5.0, "Tf": 0.05, "Tg": 0.5, "VELM": 0.2, "GMAX": 1.0, "GMIN": 0.0, "TW": 1.0,
              "At": 1.2, "Dturb": 0.5, "qNL": 0.08},
    "GAST": {"R": 0.05, "T1": 0.4, "T2": 0.1, "T3": 3.0, "AT": 1.0, "KT": 2.0, "VMAX": 1.0, "VMIN": 0.0, "Dturb": 0.0},
    "IEEEG1": {"IBUS": 0, "M": 0, "K": 20.0, "T1": 0.0, "T2": 0.0, "T3": 0.1, "Uo": 0.5, "Uc": -0.5, "PMAX": 1.0,
               "PMIN": 0.0, "T4": 0.3, "K1": 0.3, "K2": 0.0, "T5": 7.0, "K3": 0.4, "K4": 0.0, "T6": 0.6, "K5": 0.3,
               "K6": 0.0, "T7": 0.0, "K7": 0.0, "K8": 0.0},
}

DYN_DEFAULTS["REGCAU1"] = dict(DYN_DEFAULTS["REGCA1"])
# protection (PSLF lhvrt / lhfrt on each inverter): trips just past NERC PRC-024-2's no-trip boundaries -- voltage
# (pu from vref): HV 1.2 at once, 1.175 0.2 s, 1.15 0.5 s, 1.1 1 s; LV 0.45 0.15 s, 0.65 0.3 s, 0.75 2 s, 0.9 3 s;
# frequency (Hz from fref, the Western Interconnection's): 61.7 at once, 61.6 30 s, 60.6 180 s; 57.0 at once,
# 57.3 0.75 s, 57.8 7.5 s, 58.4 30 s, 59.4 180 s. A trip time of 0: PSLF takes two time steps.
for _m, _ref, _pts in (("LHVRT", ("vref", 1.0), [(0.2, 0.0), (0.175, 0.2), (0.15, 0.5), (0.1, 1.0), (-0.55, 0.15),
                                                 (-0.35, 0.3), (-0.25, 2.0), (-0.1, 3.0)]),
                       ("LHFRT", ("fref", 60.0), [(1.7, 0.0), (1.6, 30.0), (0.6, 180.0), (-3.0, 0.0), (-2.7, 0.75),
                                                  (-2.2, 7.5), (-1.6, 30.0), (-0.6, 180.0)])):
    _d = dict([_ref, ("alarm", 0)])
    for _k in range(10):
        _d[DYR_LIB[_m][2][1 + _k]], _d[DYR_LIB[_m][2][11 + _k]] = _pts[_k] if _k < len(_pts) else (0.0, 0.0)
    DYN_DEFAULTS[_m] = _d
# REGC_B (PSS/E 35's REGCBU1): its PQ flag and Imax follow its REEC's (set so in the .dyr unless Dyr_Params says)
DYN_DEFAULTS["REGCBU1"] = {"RateFlag": 0, "PQFLAG": 0, "Tg": 0.02, "Tfltr": 0.02, "Iqrmax": 99.0, "Iqrmin": -99.0,
                           "Rrpwr": 10.0, "Te": 0.02, "Imax": 1.1}
DYN_DEFAULTS["REPCAU1"] = dict(DYN_DEFAULTS["REPCA1"])
DYN_DEFAULTS["PLNTBU1"] = dict(DYN_DEFAULTS["REPCA1"], MVAbase=100.0)
# REEC_D: REEC_A's generic values and flags (all 0: the same whether the first ICON is a flag or a bus), flat
# ten-point VDL tables (the files' voltage points, Imax), no line-drop compensation and no blocking
DYN_DEFAULTS["REECDU1"] = dict(DYN_DEFAULTS["REECA1"], VCMPFLAG=0, Rc=0.0, Xc=0.0, Tr1=0.0, Kc=0.0, Ke=0.0,
                               Vblkl=0.0, Vblkh=1.5, Tblk_delay=0.0)
for _k, (_vq, _vp) in enumerate(zip((0.0, 0.3, 0.4, 0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.45),
                                     (0.0, 0.3, 0.4, 0.6, 0.7, 0.9, 1.0, 1.1, 1.2, 1.45))):
    DYN_DEFAULTS["REECDU1"].update({"Vq%d" % (_k + 1): _vq, "Iq%d" % (_k + 1): 1.1,
                                    "Vp%d" % (_k + 1): _vp, "Ip%d" % (_k + 1): 1.1})

REEC_MODEL = {"REEC_A": "REECA1", "REEC_C": "REECCU1", "REEC_D": "REECDU1"}
SYNC_DEFAULT = (SYNC_MACHINES[0], SYNC_EXCITERS[0], SYNC_GOVERNORS[0])
PLANT_CTRL_TYPES = ("PV", "BESS", "WT4")   # REPC_B's REAX4BU1 is for type 4 machines (WT3 keeps a REPCA1)


def on_plntbu1(g, ctl):
    """True: generator g takes its P and Q from the plant's PLNTBU1 (REPC_B) through a REAX4BU1."""
    return ctl.get("plant_ctrl") == "REPC_B" and g["type"] in PLANT_CTRL_TYPES and g["q_mode"] == "share"


def gen_chain(g, ctl):
    """Generator g's .dyr models: its type, Electrical control and the Plant's controller choices."""
    if g["type"] == "SYNC":                      # its machine, exciter and governor (NONE: left out)
        return [m for m in g.get("sync") or SYNC_DEFAULT if m != "NONE"]
    if g["type"] == "USER":
        return []
    usr = ctl.get("regc_repc") == "USRMDL"
    chain = ["REGCBU1" if g.get("regc") == "REGC_B" else ("REGCAU1" if usr else "REGCA1"), REEC_MODEL[g["reec"]]]
    if g["q_mode"] == "share":                   # fixed Q: no plant controller, its REEC holds its own Q
        chain.append("REAX4BU1" if on_plntbu1(g, ctl) else ("REPCAU1" if usr else "REPCA1"))
    if g["type"] == "WT3":
        chain += ["WTDTA1", "WTARA1", "WTPTA1", "WTTQA1"]
    return chain


def dyr_param_name(model, pname):
    """A Dyr_Params parameter as its model names it -- its name in any case, an alias, ICONn / CONn -- or None."""
    allp = DYR_LIB[model][2]
    names = dict((p.upper(), p) for p in allp)
    up = str(pname or "").strip().upper()
    if up not in names and DYR_ALIAS.get(up, "").upper() in names:
        up = DYR_ALIAS[up].upper()
    pos = re.match(r"^(I?CON)\s*\(?\s*(\d+)\s*\)?$", up)
    if up not in names and pos:
        nic = DYR_LIB[model][1]
        part = allp[:nic] if pos.group(1) == "ICON" else allp[nic:]
        if 1 <= int(pos.group(2)) <= len(part):
            return part[int(pos.group(2)) - 1]
    return names.get(up)


def dyr_table(inp):
    """Every .dyr parameter as written, by model and parameter, for each generator (DETAILED: its machines
       together), IB and PLANT -> (columns [name], rows [(model, param, position, {column: (text, how)})], notes);
       how: "set" (a Dyr_Params row), "default", "auto" (from the plant data), "varies" (by machine)."""
    log = Log(echo=False)
    net = build_network(inp, log)
    try:
        operating_point(net, log)
    except SolveError:
        for g in net.gens:
            g.setdefault("p", g["dispatch"])
            g.setdefault("q", 0.0)
    recs = dyr_models(net, inp)
    over = dyr_params(net, inp)
    fam = dict((g["name"], g.get("family", g["name"])) for g in net.gens)
    cols = []
    vals = collections.OrderedDict()                   # (model, param) -> {column: [(value, how) per machine]}
    for name, model, pv in recs:
        col = fam.get(name, name)
        if col not in cols:
            cols.append(col)
        for p, v in pv:
            how = "set" if p in over.get(name, {}).get(model, {}) else "default" if (
                DYN_DEFAULTS[model].get(p) == v or _same(DYN_DEFAULTS[model].get(p), v)) else "auto"
            vals.setdefault((model, p), collections.OrderedDict()).setdefault(col, []).append((v, how))
    cols = [c for c in cols if c not in ("IB", "PLANT")] + [c for c in ("IB", "PLANT") if c in cols]
    rows = []
    for model, (_hdr, nic, names) in DYR_LIB.items():
        for k, p in enumerate(names):
            if (model, p) not in vals:
                continue
            cells = {}
            for col, got in vals[(model, p)].items():
                hows = set(h for _v, h in got)
                how = "set" if "set" in hows else "auto" if "auto" in hows else "default"
                if model == "REAX4BU1" and p in ("Kz", "Kw") and len(got) > 1:
                    cells[col] = (_g(sum(float(v) for v, _h in got)), how)     # its share: the machines' sum
                elif len(set(str(v) for v, _h in got)) > 1:
                    nums = [float(v) for v, _h in got]
                    cells[col] = ("%s .. %s" % (_g(min(nums)), _g(max(nums))), "varies")
                else:
                    v = got[0][0]
                    cells[col] = (v if p == "CKT" else _g(float(v)) if k >= nic else "%d" % int(round(float(v))),
                                  how)
            rows.append((model, p, ("ICON%d" % (k + 1)) if k < nic else ("CON%d" % (k - nic + 1)), cells))
    return cols, rows, [n for n in net.notes if "Dyr_Params" in n]


def _same(a, b):
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    except (TypeError, ValueError):
        return False


def dyr_params(net, inp):
    """{GENERATOR, IB or PLANT: {MODEL: {param: value}}} from Dyr_Params; each name (or ICONn / CONn) checked
       against the model."""
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    gnames = members(net)
    gnames["IB"] = ["IB"]
    gnames["PLANT"] = ["PLANT"]
    for r in inp["dyr_params"]:
        w = r["_where"]
        gen = (_txt(r.get("generator")) or "").upper()
        if gen not in gnames:
            _no_gen(net, w, r.get("generator"), sorted(members(net, family_only=True)) + ["IB", "PLANT"])
            continue
        model = (_txt(r.get("model")) or "").upper()
        if model not in DYR_LIB:
            raise InputError("%s: Model %r -- one of %s" % (w, r.get("model"), ", ".join(DYR_LIB)))
        pname = _txt(r.get("parameter")) or ""
        allp = DYR_LIB[model][2]
        pn = dyr_param_name(model, pname)
        if pn is None:
            raise InputError("%s: %s has no parameter %r -- it has %s (or ICON1..%d, CON1..%d)"
                             % (w, model, pname, ", ".join(allp), DYR_LIB[model][1], len(allp) - DYR_LIB[model][1]))
        val = r.get("value")
        if pn == "CKT":
            val = _txt(val) or "1"
        else:
            val = _num(r, "value", w, required=True)
        many = gnames[gen]
        for gname in many:
            v = val
            if model == "REAX4BU1" and pn in ("Kz", "Kw") and len(many) > 1:   # DETAILED: the share split
                key = "qmax" if pn == "Kz" else "pmax"
                gs = [x for x in net.gens if x["name"] in many]
                tot = sum(x[key] for x in gs)
                v = val * next(x for x in gs if x["name"] == gname)[key] / tot if tot else val / len(many)
            out[gname][model][pn] = v
        if model == "REAX4BU1" and pn in ("Kz", "Kw") and len(many) > 1:
            net.notes.append("NOTE: %s: %s %s %s split over its %d unit groups by their %s" % (
                w, gen, pn, _g(val), len(many), "Qmax" if pn == "Kz" else "Pmax"))
    return out


def _reax_share(g, recs):
    """(Kz, Kw) of generator g's REAX4BU1 as written to the .dyr."""
    vals = dict(next((v for n, m, v in recs if n == g["name"] and m == "REAX4BU1"), []))
    return float(vals.get("Kz", g["pc_share"][0])), float(vals.get("Kw", g["pc_share"][1]))


def _no_gen(net, w, name, choices):
    """A Generator cell naming no generator: a unit group of the DETAILED model in an SMIB run is skipped with a
       NOTE; anything else stops the run."""
    nm = _txt(name)
    if not nm:
        raise InputError("%s: Generator is missing -- one of %s" % (w, ", ".join(choices)))
    fams = members(net, family_only=True)
    if net.model == "SMIB" and any(re.match(r"^%s_.+_S\d+$" % re.escape(f), nm.upper()) for f in fams):
        net.notes.append("NOTE: %s: %s is a unit group of the DETAILED model -- not used in this SMIB model" % (w, nm))
        return
    raise InputError("%s: Generator %r -- one of %s" % (w, nm, ", ".join(choices)))


def members(net, family_only=False):
    """{GENERATOR (upper): [machine names]}: a generator's own name stands for all its machines (DETAILED: one per
       collector node), a machine's name for itself."""
    out = collections.OrderedDict()
    for g in net.gens:
        out.setdefault(g.get("family", g["name"]).upper(), []).append(g["name"])
    if not family_only:
        for g in net.gens:
            out.setdefault(g["name"].upper(), [g["name"]])
    return out


def dyr_models(net, inp):
    """[(generator name | IB | PLANT, model, ordered [(param, value)])] -- the generic models with the plant's own
       limits; PLANT: REPC_B's plant controller (PLNTBU1, at the POI)."""
    over = dyr_params(net, inp)
    ctl = net.ctl
    poi, ib = _num_of(net, "POI"), _num_of(net, "IB")
    out = [("IB", "GENCLS", list(_merged("GENCLS", {}, over["IB"]["GENCLS"])))]
    pc = [g for g in net.gens if on_plntbu1(g, ctl)]
    smb = sum(g["mbase"] for g in pc)
    sq, sp = sum(g["qmax"] for g in pc), sum(g["pmax"] for g in pc)
    for g in net.gens:
        chain = gen_chain(g, ctl)
        mb = g["mbase"]
        qmax, qmin = g["qmax"] / mb, g["qmin"] / mb
        pmax, pmin = g["pmax"] / mb, g["pmin"] / mb
        lim = {"QMax": qmax, "QMin": qmin, "PMAX": pmax, "PMIN": pmin}
        g["pc_share"] = (g["qmax"] / sq if sq else 0.0, g["pmax"] / sp if sp else 0.0) if g in pc else (0.0, 0.0)
        auto = {
            "REECA1": dict(lim, PFLAG=1 if g["type"] == "WT3" else 0),
            "REECCU1": lim,
            "REECDU1": dict(lim, Ke=-1.0 if g["type"] == "BESS" else 0.0),
            "REPCA1": {"BUSR": poi, "BUS1": poi, "BUS2": ib, "CKT": "1", "Qmax": qmax, "Qmin": qmin, "Pmax": pmax,
                       "Pmin": pmin, "Dup": 20.0 if g["type"] == "BESS" else 0.0},
            "REAX4BU1": {"PCBUS": poi, "Kz": g["pc_share"][0], "Kw": g["pc_share"][1], "Qmax": qmax, "Qmin": qmin,
                         "Pmax": pmax, "Pmin": pmin},
            "WTTQA1": {"TRATE": g["pmax"]},
            "TGOV1": {"VMAX": max(1.05, g["p"] / mb + 0.05)},
            "GAST": {"VMAX": max(1.0, g["p"] / mb + 0.05)},
            "IEEEG1": {"PMAX": max(1.0, g["p"] / mb + 0.05)},
            "HYGOV": {"GMAX": max(1.0, g["p"] / mb / DYN_DEFAULTS["HYGOV"]["At"] + DYN_DEFAULTS["HYGOV"]["qNL"] + 0.05)},
        }
        auto["REPCAU1"] = auto["REPCA1"]
        prot = list(PSLF_ONLY) if g["type"] in REGC_OK else []
        for m in over.get(g["name"], {}):
            note = "NOTE: Dyr_Params for %s %s: %s is not in its model chain %s -- not used" % (
                g.get("family", g["name"]), m, m, "+".join(chain + prot) or "(none: type USER)")
            if m not in chain + prot and note not in net.notes:
                net.notes.append(note)
        if "REGCBU1" in chain:                     # the converter's current limit as its electrical control's
            rv = dict(_merged(chain[1], auto.get(chain[1], {}), over[g["name"]][chain[1]]))
            auto["REGCBU1"] = {"PQFLAG": rv["PQFLAG"], "Imax": rv["Imax"]}
        auto["LHFRT"] = {"fref": net.f}
        for m in chain + prot:
            out.append((g["name"], m, list(_merged(m, auto.get(m, {}), over[g["name"]][m]))))
        g["dyr"] = chain
    net.plant_ctrl = None
    if pc:
        auto = {"BUSR": poi, "BUS1": poi, "BUS2": ib, "CKT": "1", "Qmax": sq / smb,
                "Qmin": sum(g["qmin"] for g in pc) / smb, "Pmax": sp / smb, "Pmin": sum(g["pmin"] for g in pc) / smb,
                "Dup": 20.0 if any(g["type"] == "BESS" for g in pc) else 0.0, "MVAbase": smb}
        out.append(("PLANT", "PLNTBU1", list(_merged("PLNTBU1", auto, over["PLANT"]["PLNTBU1"]))))
        net.plant_ctrl = {"model": "PLNTBU1", "gens": [g["name"] for g in pc], "mbase": smb}
    elif ctl.get("plant_ctrl") == "REPC_B":
        net.notes.append("CHECK: Plant controller REPC_B, but no generator for it (PV, BESS or WT4 with Q mode "
                         "share) -- no PLNTBU1 written")
    for who in ("IB", "PLANT"):
        for m in over.get(who, {}):
            if (who, m) not in [(n, mm) for n, mm, _v in out]:
                net.notes.append("NOTE: Dyr_Params for %s %s: not in the .dyr -- not used" % (who, m))
    return out


def _merged(model, auto, user):
    vals = dict(DYN_DEFAULTS[model])
    vals.update(auto)
    vals.update(user)
    for p in DYR_LIB[model][2]:
        yield p, vals[p]


_PH = re.compile(r"\{([A-Za-z0-9_:\-]+)\}")


def fill_placeholders(net, text, g, where):
    """{BUS} {ID} {MBASE} {POI} {IB} {HV} {MV} {<bus role>} in a record of your own."""
    def rep(m):
        key = m.group(1).upper()
        if g is not None:
            if key == "BUS":
                return str(_num_of(net, g["bus"]))
            if key == "ID":
                return "'%s'" % g["id"]
            if key == "MBASE":
                return _g(g["mbase"])
            if key == "MV":
                return str(_num_of(net, g["mv"]))
            fam = g.get("family", "").upper()
            if fam and key in (fam, fam + ":LV"):        # DETAILED: this unit group's own terminal and GSU
                return str(_num_of(net, g["bus"]))
            if fam and key == fam + ":MV":
                return str(_num_of(net, g["mv"]))
        if key in ("BUS", "ID", "MBASE", "MV"):
            raise InputError("%s: {%s} needs a Generator" % (where, key))
        return str(_num_of(net, net.find(m.group(1), where)))
    return _PH.sub(rep, text)


def write_dyr(net, inp, path):
    recs = dyr_models(net, inp)
    L = ["/ %s %s -- written by smib_build.py on %s" % (net.model, net.name, time.strftime("%Y-%m-%d %H:%M")),
         "/ generic WECC models: placeholders until the vendor's models (parameters: Dyr_Params)"]
    gen_of = dict((g["name"], g) for g in net.gens)
    mins = [0]
    for gname, model, vals in recs:
        if model in RELAYS:
            L.extend(relay_records(net, gen_of[gname], model, dict(vals), mins))
            continue
        hdr, nic, _names = DYR_LIB[model]
        if gname == "IB":
            bus, mid = _num_of(net, "IB"), IB_ID
        elif gname == "PLANT":
            bus, mid = _num_of(net, "POI"), None
        else:
            bus, mid = _num_of(net, gen_of[gname]["bus"]), gen_of[gname]["id"]
        if model in DYR_USRBUS:
            head = "%d 'USRBUS' '%s' %s" % (bus, model, " ".join(str(x) for x in hdr))
        elif hdr:
            head = "%d 'USRMDL' '%s' '%s' %s" % (bus, mid, model, " ".join(str(x) for x in hdr))
        else:
            head = "%d '%s' '%s'" % (bus, model, mid)
        icons = []
        for p, v in vals[:nic]:
            icons.append("'%s'" % v if p == "CKT" else "%d" % int(round(float(v))))
        cons = [_g(float(v)) for p, v in vals[nic:]]
        L.append(head + ("  " + "  ".join(icons) if icons else ""))
        for k in range(0, len(cons), 6):
            L.append("     " + "  ".join(cons[k:k + 6]) + ("  /" if k + 6 >= len(cons) else ""))
        if not cons:
            L[-1] += "  /"
    L.extend(user_records(net, inp))
    _write_text(path, "\n".join(L))
    return recs


def relay_records(net, g, model, v, mins):
    """Generator g's ride-through relay as PSS/E has it: a VTGTPAT / FRQTPAT for each trip point (a deviation below
       the reference: the lower threshold; above it: the upper), each its own instance number (MINS)."""
    name, ref, dev, far = RELAYS[model]
    bus = _num_of(net, g["bus"])
    if float(v.get("alarm", 0)) > 0:                 # PSLF's alarm only: PSS/E's relays have no such mode
        note = "NOTE: %s %s alarm > 0: PSLF only reports its trips; no %s in the .dyr" % (
            g.get("family", g["name"]), model, name)
        if note not in net.notes:
            net.notes.append(note)
        return []
    out = []
    for k in range(1, 11):
        d, t = float(v["%s%d" % (dev, k)]), float(v["dttrp%d" % k])
        if d == 0.0 and t == 0.0:
            continue
        lo, hi = (float(v[ref]) + d, far) if d < 0 else (0.0, float(v[ref]) + d)
        mins[0] += 1
        out.append("%d '%s' %d %d '%s'  %s  %s  %s  0.0  /" % (mins[0], name, bus, bus, g["id"], _g(lo), _g(hi),
                                                                 _g(t)))
    return out


def user_records(net, inp):
    """Dyr_Records: each row's record, its placeholders filled (a generator's: one per machine), cut at its '/'."""
    out = []
    for r in inp["dyr_records"]:
        w = r["_where"]
        text = _txt(r.get("record"))
        if not text:
            continue
        gname = _txt(r.get("generator"))
        gs = [None]
        if gname:
            names = members(net).get(gname.upper())
            if not names:
                _no_gen(net, w, gname, list(members(net, family_only=True)) + [
                    "or blank for a record of the plant's own ({POI}, {IB} ... for its buses)"])
                continue
            gs = [x for x in net.gens if x["name"] in names]
        end = re.match(r"^((?:[^'/]|'[^']*')*/)", text)    # what follows the record's '/' is a remark
        for g in gs:
            rec = fill_placeholders(net, end.group(1) if end else text, g, w).strip()
            if not rec.endswith("/"):
                rec += " /"
            out.append(rec)
    return out


# ============================================================================
# PSCAD -- the same network, values in PSCAD's units, and the build script
# ============================================================================
def _node(role):
    return "GND" if role == "GND" else re.sub(r"[^A-Za-z0-9_]", "_", role.replace("C:", "C_"))


def _pp(what, names, value, choice=None):
    return {"what": what, "names": names, "value": value, "choice": choice}


def _rlc(tag, kind, value, a, b, x, y, note=""):
    defn, par, unit = {"R": ("master:resistor", "R", "ohm"), "L": ("master:inductor", "L", "H"),
                       "C": ("master:capacitor", "C", "uF")}[kind]
    return {"tag": tag, "kind": kind, "defn": [defn], "x": x, "y": y, "note": note, "value": value, "unit": unit,
            "params": [_pp(kind, [par], "%s [%s]" % (_g(value, 7), unit))],
            "ports": [{"node": _node(a), "pick": ["N1", "@first"]}, {"node": _node(b), "pick": ["N2", "@second"]}]}


def _fp(what, names, value, desc, cat=None, pick=None, choice=None, nope=None):
    """A parameter the build script finds in the component's form by its description (every word of desc in it,
       none of nope, every word of cat in its page's name) -- names: what it may be called where the form cannot
       be read; a choice set by its text (pick: lists of words, the first list that matches) or by choice (its
       key's words)."""
    return dict(_pp(what, names, value, choice), desc=desc, cat=cat, pick=pick, nope=nope)


def _meter(tag, a, b, kv, sig, net, note):
    """A multimeter between node a, where the power comes from, and node b: the flow a -> b reads positive -- it
       reads left to right, mirrored right to left (turned by where a and b lie: _turn). P, Q and RMS voltage as
       signals P_, Q_, V_<sig> in MW, MVAr and kV (base 1 MVA, base kV its bus's), the RMS voltage shown
       digitally, an animated display, 0.02 s smoothing -- the other measurements off."""
    cfg, yes, no = ["CONFIG"], [["YES"]], [["NO"]]
    return {"tag": tag, "kind": "METER", "defn": ["master:multimeter"], "x": 0, "y": 0, "orient": 4, "note": note,
            "params": [_fp("animated display", ["Dis", "Anim"], None, ["ANIMATED"], cfg, yes, ["YES"]),
                       _fp("instantaneous V", ["MeasV"], None, ["INSTANTANEOUS", "VOLTAGE"], cfg, no, ["NO"]),
                       _fp("instantaneous I", ["MeasI"], None, ["INSTANTANEOUS", "CURRENT"], cfg, no, ["NO"]),
                       _fp("measure P", ["MeasP"], None, ["ACTIVE", "POWER"], cfg, yes, ["YES"], nope=["REACTIVE"]),
                       _fp("measure Q", ["MeasQ"], None, ["REACTIVE", "POWER"], cfg, yes, ["YES"]),
                       _fp("measure Vrms", ["RMS", "MeasRMS", "MeasVrms"], None, ["RMS", "VOLTAGE"], cfg,
                           [["YES", "DIGITAL"], ["YES"]], ["YES"]),
                       _fp("measure Irms", ["MeasIrms"], None, ["RMS", "CURRENT"], cfg, no, ["NO"]),
                       _fp("phase angle", ["MeasPh"], None, ["PHASE", "ANGLE"], cfg, no, ["NO"]),
                       _fp("smoothing", ["Ts", "Tsmooth"], "0.02 [s]", ["SMOOTHING"], cfg),
                       _fp("frequency", ["Freq", "F"], "%s [Hz]" % _g(net.f), ["FREQUENCY"], cfg),
                       _fp("base MVA", ["BaseS", "S"], "1.0 [MVA]", ["BASE", "MVA"], cfg),
                       _fp("base kV", ["BaseV"], "%s [kV]" % _g(kv), ["BASE", "VOLTAGE"], cfg),
                       _fp("P signal", ["P"], "P_" + sig, ["ACTIVE", "POWER"], ["SIGNAL"], nope=["REACTIVE"]),
                       _fp("Q signal", ["Q"], "Q_" + sig, ["REACTIVE", "POWER"], ["SIGNAL"]),
                       _fp("Vrms signal", ["Vrms"], "V_" + sig, ["RMS", "VOLTAGE"], ["SIGNAL"])],
            "from_to": (a, b), "ports": _turn_ports(a, b, False)}


def _scaled(net, inp, g, sl):
    """The units a current scaler gives generator g's vendor model (UDM) of one unit: its slot's in the equivalent
       (SMIB) model -- 0 (none) in the DETAILED model, for a stand-in source (it holds them all), for one unit, and
       for a UDM whose PSCAD_Params give it {UNITS} (it counts them itself)."""
    ps = g["pscad"]
    if net.model != "SMIB" or not (ps["iid"] or ps["definition"]) or sl["units"] < 2:
        return 0
    mine = members(net)
    if any("{UNITS}" in (_txt(r.get("value")) or "").upper() for r in inp["pscad_params"]
           if g["name"] in mine.get((_txt(r.get("generator")) or "").upper(), [])):
        net.notes.append("NOTE: PSCAD: %s's vendor model is given {UNITS} (PSCAD_Params): it counts its units "
                         "itself -- no current scaler" % g["name"])
        return 0
    return int(round(sl["units"]))


def _scaler(tag, a, b, n, note):
    """PSCAD's current scaler between node a, one unit's (its UDM's), and node b, the network's: its network side
       carries n times the current of its unit's side -- the unit's side on its left, mirrored on its right
       (turned by where a and b lie: _turn)."""
    return {"tag": tag, "kind": "SCALER", "defn": ["master:current_scaler"], "x": 0, "y": 0, "orient": 4,
            "note": note, "params": [_pp("scale", ["G_CDCS"], "%d" % n), _pp("dimension", ["Dim"], "3")],
            "from_to": (a, b), "ports": _turn_ports(a, b, False)}


def _turn_ports(a, b, a_left):
    """The two ends of a part that runs from node a to node b, a's on its left or on its right."""
    left, right = (a, b) if a_left else (b, a)
    return [{"node": _node(left), "pick": ["@left", "@first"]}, {"node": _node(right), "pick": ["@right", "@second"]}]


def _turn(comps):
    """Each meter and current scaler turned by where its two sides lie in the drawing: from (the UDM's side, the
       plant's) on its left -- as drawn, left to right -- it is placed as it is; on its right, mirrored."""
    for cm in comps:
        if cm.get("from_to"):
            a, b = cm["from_to"]
            a_left = cm.get("in") == _node(a)
            cm["orient"], cm["ports"] = (0 if a_left else 4), _turn_ports(a, b, a_left)


def _pi_comp(ln, net):
    """A line, collector equivalent or feeder section as one Coupled PI Section: R, X and B in pu on 100 MVA and
       the line's kV, per metre of its length (1 m where it has none: the totals); zero sequence = positive
       sequence (no coupling between the phases: the same as an R, an L and C/2 at each end)."""
    n = ln["count"]
    kv = net.kv(ln["fr"])
    zb = kv * kv / PI_MVA
    m = ln["km"] * 1000.0 if ln.get("km") else 1.0
    r, x, b = ln["R"] / n / zb / m, ln["X"] / n / zb / m, ln["B"] * n * 1e-6 * zb / m
    rxb = ["R,X,B"]
    params = [_fp("name", ["Name"], _node(ln["name"]), ["NAME"]),
              _fp("data entered as", ["Estim", "DataType"], None, ["IMPEDANCE", "ADMITTANCE"],
                  pick=[["R,X,B", "PU"]], choice=["X_B", "PU"]),
              _fp("model", ["Model", "Coupled"], None, ["NOMINAL", "COUPLED"], pick=[["COUPLED"]], choice=["COUPLED"]),
              _fp("frequency", ["F", "Freq"], _g(net.f), ["FREQUENCY"]),
              _fp("length (m)", ["Len", "Length"], "%.10g" % m, ["LENGTH"]),
              _fp("zero sequence", ["Zseq"], None, ["0 SEQUENCE"], pick=[["ENTER"]], choice=["ENTER"]),
              _fp("graphics", ["View", "Disp"], None, ["GRAPHICS"], pick=[["COMPACT"], ["SINGLE"]], choice=["SINGLE"]),
              _fp("kV", ["Vrated", "V"], _g(kv, 8), ["RATED", "VOLTAGE"], rxb),
              _fp("MVA", ["MVA", "Sbase"], _g(PI_MVA), ["MVA"], rxb),
              _fp("R1 (pu/m)", ["R1"], "%.10g" % r, ["POSITIVE", "RESISTANCE"], rxb),
              _fp("X1 (pu/m)", ["X1"], "%.10g" % x, ["POSITIVE", "REACTANCE"], rxb),
              _fp("B1 (pu/m)", ["B1"], "%.10g" % b, ["POSITIVE", "SUSCEPTANCE"], rxb),
              _fp("R0 (pu/m)", ["R0"], "%.10g" % r, ["ZERO", "RESISTANCE"], rxb),
              _fp("X0 (pu/m)", ["X0"], "%.10g" % x, ["ZERO", "REACTANCE"], rxb),
              _fp("B0 (pu/m)", ["B0"], "%.10g" % b, ["ZERO", "SUSCEPTANCE"], rxb)]
    return {"tag": ln["name"], "kind": "PI", "defn": list(PI_DEFNS), "x": 0, "y": 0, "params": params,
            "note": "%s: R %s ohm, X %s ohm, B %s uS%s over %s m (%s kV, %s MVA base); zero sequence = positive" % (
                ln["name"], _g(ln["R"] / n), _g(ln["X"] / n), _g(ln["B"] * n), (" (%d circuits)" % n) if n > 1 else "",
                _g(m), _g(kv), _g(PI_MVA)),
            "ports": [{"node": _node(ln["fr"]), "pick": ["@left", "@first"]},
                      {"node": _node(ln["to"]), "pick": ["@right", "@second"]}]}


PI_MVA = 100.0        # the Coupled PI Sections' MVA base
PI_DEFNS = ("master:newpi", "master:pi_section", "master:coupled_pi", "master:cpi")   # tried after the one found


def _source(tag, name, kv, vpu, ang, f, x, y, node):
    vll = vpu * kv
    tiny = [_pp("source impedance %s" % n, [n], v) for n, v in (("R", "0.0001 [ohm]"), ("Rs", "0.0001 [ohm]"),
                                                                    ("Rp", "0.0001 [ohm]"), ("L", "1e-08 [H]"),
                                                                    ("Lp", "1e-08 [H]"), ("Z1", "0.0001 [ohm]"),
                                                                    ("Z0", "0.0001 [ohm]"))]
    return {"tag": tag, "kind": "SOURCE", "defn": ["master:source3"], "x": x, "y": y,
            "note": "%.6g kV (%.6f pu) at %.4f deg" % (vll, vpu, ang), "vll": vll, "vpu": vpu, "ang": ang,
            "params": [_pp("name", ["Name"], _node(name)), _pp("base frequency", ["F"], "%s [Hz]" % _g(f)),
                       _pp("frequency", ["f", "Freq"], "%s [Hz]" % _g(f)),
                       _pp("magnitude (kV L-L rms)", ["Vm", "Vac", "V"], "%s [kV]" % _g(vll, 8)),
                       _pp("base kV", ["Vbase"], "%s [kV]" % _g(kv, 8)),
                       _pp("magnitude (pu)", ["Vpu"], "%s [pu]" % _g(vpu, 8)),
                       _pp("phase (deg)", ["Ph", "PhT", "Phase"], "%s [deg]" % _g(ang, 8))] + tiny,
            "ports": [{"node": _node(node), "pick": ["@dim3", "@first"]},
                      {"node": "GND", "pick": ["@rest"], "optional": True}]}       # its neutral, where it has one


def _yd(w):
    return ["DELTA"] if w == "D" else ["Y"]


def _lead(vg, k):
    """PSCAD 'Delta lags or leads Y' for winding k against winding 1."""
    if vg["w"][0] == vg["w"][k]:
        return None
    d = vg["shift"][k] if vg["w"][k] == "D" else -vg["shift"][k]     # the delta winding's angle against the wye's
    return ["LAG"] if d < 0 else ["LEAD"]


def pscad_model(net, inp):
    """The PSCAD components: [{tag, kind, defn candidates, x, y, params [(what, names, value)], ports}]."""
    f = net.f
    w = 2.0 * math.pi * f
    comps = []
    shift = phase_shifts(net)
    c = net.ctl["pscad"]
    y0 = 6
    x = [4]

    def nx(step=12):
        x[0] += step
        return x[0]

    for t in net.tx3:
        if pscad_shifts(t["vg"]) != t["vg"]["shift"]:
            net.notes.append("NOTE: PSCAD: %s %s drawn with one lead / lag for both its delta windings (PSCAD's "
                             "3-winding transformer has one): its TV side's angles are %+.0f deg from the vector "
                             "group's; the power flows are the same" % (t["name"], t["vg"]["text"],
                                                                        pscad_shifts(t["vg"])[2] - t["vg"]["shift"][2]))
    th = net.thev
    comps.append(_source("IB_SRC", "IB", net.kv("IB"), net.bus["IB"]["vm"], 0.0, f, x[0], y0, "IB"))
    nxt = "IB"
    if th["R"] > 1e-9:
        comps.append(_rlc("GRID_R", "R", th["R"], "IB", "GRID_RL", nx(), y0, "grid Thevenin R"))
        nxt = "GRID_RL"
    poi_m = "POI_M" if c["meter"] else "POI"
    comps.append(_rlc("GRID_L", "L", th["X"] / w, nxt, poi_m, nx(), y0, "grid Thevenin L (X %.4f ohm)" % th["X"]))
    if c["meter"]:
        m = _meter("POI_METER", "POI", "POI_M", net.kv("POI"), "POI", net, "P, Q (MW, MVAr: the plant's export "
                   "positive), Vrms (kV) at the POI (grid side)")
        m["x"], m["y"] = nx(), y0
        comps.append(m)

    flt = c["fault"]
    if flt:
        t = flt["t"] if flt["t"] is not None else max(100.0, 10.0 * c["duration_s"])
        when = ("at %s s for %s s" % (_g(t), _g(flt["dur"]))) if flt["t"] is not None else (
            "none in the run (from %s s: set Plant: %s to apply one)" % (_g(t), _label("pscad_fault_s")))
        comps.append({"tag": "POI_FAULT", "kind": "FAULT", "defn": ["master:tpflt"], "x": 0, "y": 0, "room": 2,
                      "note": "%s fault at the POI%s through %s ohm, %s" % (
                          flt["type"], " (grid side of its meter)" if c["meter"] else "", _g(flt["ohm"]), when),
                      "params": [_pp("phase %s" % ph, [ph], None, ["YES"] if ph in flt["type"] else ["NO"])
                                 for ph in "ABCG"] + [_pp("fault resistance", ["RON"], "%s [ohm]" % _g(flt["ohm"]))],
                      "ports": [{"node": poi_m, "pick": ["@dim3", "@first"]}]})
        comps.append({"tag": "POI_FAULT_TIMER", "kind": "TIMER", "defn": ["master:tfaultn"], "x": 0, "y": 0,
                      "beside": "POI_FAULT", "drives": "POI_FAULT", "note": "the fault's timing: %s" % when,
                      "params": [_pp("fault time", ["TF"], "%s [s]" % _g(t)),
                                 _pp("fault duration", ["DF"], "%s [s]" % _g(flt["dur"]))], "ports": []})

    cap = collections.OrderedDict()                     # each node's line and cable charging: one C there

    def line(ln):
        n = ln["count"]
        a, b = ln["fr"], ln["to"]
        mid = ln["name"] + "_RL"
        out = []
        if ln["R"] > 1e-9:
            out.append(_rlc(ln["name"] + "_R", "R", ln["R"] / n, a, mid, 0, 0, "%s R" % ln["name"]))
        else:
            mid = a
        out.append(_rlc(ln["name"] + "_L", "L", ln["X"] / n / w, mid, b, 0, 0, "%s L" % ln["name"]))
        for nd in (a, b):
            if ln["B"] > 0:
                c0 = cap.setdefault(nd, [0.0, []])
                c0[0] += ln["B"] * n / w / 2.0               # uS / (rad/s) = uF: half at each end
                c0[1].append(ln["name"])
        return out

    for ln in net.lines:
        if ln["kind"] != "GRID":                         # the grid's Thevenin impedance: drawn above
            comps.extend([_pi_comp(ln, net)] if c["lines"] == "PI" else line(ln))
    slots = []
    inside = set(id(g["gsu"]) for g in net.gens if g["gsu"] and _gen_slot(net, g, shift)["at"] == "MV")
    for t in net.tx2:
        if t["kind"] == "MPT":
            comps.append(_tx2_comp(t, net, 0, 0))
    for t in net.tx3:
        comps.append(_tx3_comp(t, net, 0, 0))
    for g in net.gens:
        sl = _gen_slot(net, g, shift)
        first = len(comps)
        if g["gsu"] and id(g["gsu"]) not in inside:
            comps.append(_tx2_comp(g["gsu"], net, 0, 0))
        sl["inv"] = sl["node"]                           # the inverter's node: past its meter and its scaler
        if c["inv_meter"]:                               # its own P, Q, V: inverter to GSU positive
            sl["term"] = sl["name"] + "_TERM"
            comps.append(_meter(sl["name"] + "_PQ", sl["term"], sl["inv"], sl["kv"], sl["name"], net,
                                "P, Q (MW, MVAr: out of the inverter positive%s), Vrms (kV) of %s" % (
                                    ", all %s units" % _g(sl["units"]) if sl["units"] != 1 else "", sl["name"])))
            sl["inv"] = sl["term"]
        n = _scaled(net, inp, g, sl)
        if n:                                            # SMIB: one unit's UDM, its current n times over
            sl["one"] = sl["name"] + "_UNIT"
            comps.append(_scaler(sl["name"] + "_NX", sl["one"], sl["inv"], n, "%s units from one: the network "
                                 "side carries %d x the current of the UDM's side" % (sl["name"], n)))
            sl["inv"] = sl["one"]
        comps.append(_inverter_comp(g, sl, net, 0, 0))
        if n:
            sl["model"] += " -- one unit, x %d by %s_NX" % (n, sl["name"])
        comps[first]["hang"] = bool(g.get("family"))    # a DETAILED unit group: below its feeder node, always
        slots.append(sl)
    net.pscad_slots = slots
    for nd, (cuf, names) in cap.items():
        comps.append(_rlc(_node(nd) + "_CHG", "C", cuf, nd, "GND", 0, 0, "charging at %s: C/2 of %s" % (
            nd, ", ".join(names))))
    xx = yy = 0
    for ld in net.loads:
        v = net.kv(ld["bus"])
        if ld["mw"] > 0:
            comps.append(_rlc("%s_R" % ld["name"], "R", v * v / ld["mw"], ld["bus"], "GND", xx, yy,
                              "%s %.3f MW at 1 pu" % (ld["name"], ld["mw"])))
        if ld["mvar"] > 0:
            comps.append(_rlc("%s_L" % ld["name"], "L", v * v / ld["mvar"] / w, ld["bus"], "GND", xx + 8, yy,
                              "%s %.3f MVAr at 1 pu" % (ld["name"], ld["mvar"])))
        elif ld["mvar"] < 0:
            comps.append(_rlc("%s_C" % ld["name"], "C", -ld["mvar"] / (w * v * v) * 1e6, ld["bus"], "GND", xx + 8, yy,
                              "%s %.3f MVAr (capacitive) at 1 pu" % (ld["name"], -ld["mvar"])))
        xx += 20
    for sh in net.shunts:
        v = net.kv(sh["bus"])
        if sh["mvar"] > 0:
            comps.append(_rlc(sh["name"] + "_C", "C", sh["mvar"] / (w * v * v) * 1e6, sh["bus"], "GND", xx, yy,
                              "%s %.3f MVAr capacitor at 1 pu" % (sh["name"], sh["mvar"])))
        else:
            comps.append(_rlc(sh["name"] + "_L", "L", v * v / (-sh["mvar"]) / w, sh["bus"], "GND", xx, yy,
                              "%s %.3f MVAr reactor at 1 pu" % (sh["name"], -sh["mvar"])))
        xx += 12
    seen = set()
    for cm in comps:
        tag, k = cm["tag"], 1
        while tag.upper() in seen:                  # a load / shunt named like another part: the later one renamed
            k += 1
            tag = "%s_%d" % (cm["tag"], k)
        if tag != cm["tag"]:
            net.notes.append("NOTE: PSCAD: two components named %s -- the second is %s" % (cm["tag"], tag))
            cm["tag"] = tag
        seen.add(tag.upper())
    for c3 in pscad_layout(comps, cw=PSCAD_W3, ch=PSCAD_H3, pad=0)[0]:
        c3["x3"], c3["y3"] = c3["x"], c3["y"]          # where R, L and C are three-phase in this PSCAD
        c3["parts3"] = c3.pop("parts", {})
    comps, net.pscad_groups = pscad_layout(comps)
    _turn(comps)
    net.pscad_comps = comps
    w = max([max(c["x"], c.get("x3", c["x"])) for c in comps] or [0]) + 12      # the page it needs, as the
    h = max([max(c["y"], c.get("y3", c["y"])) for c in comps] or [0]) + 16      # build script sizes it
    if max(w, h) > PSCAD_PAGE and not any("PSCAD: the drawing" in n for n in net.notes):
        net.notes.append("CHECK: PSCAD: the drawing is %d x %d grid units -- past PSCAD's largest page (%d x %d): what "
                         "lies beyond it is not shown, to be moved by hand (the build log names it)%s" % (
                             w, h, PSCAD_PAGE, PSCAD_PAGE, "; the Equivalent (SMIB) model of the plant is far smaller"
                             if net.model == "DETAILED" else ""))
    return comps


PSCAD_W = 10          # a component's room along its row (grid units)
PSCAD_H = 14          # from a row to what hangs below it, and between those
PSCAD_GAP = 4         # from a node to the things hanging off it
PSCAD_PAD = 6         # each side of a run of R, L, C: room for its breakouts where PSCAD's R, L, C are single-phase
PSCAD_W3 = 8          # the same where PSCAD's R, L, C take the wire's three phases: no breakouts, closer together
PSCAD_H3 = 10
NAME_CH = 0.42        # a node name's width per character (grid units) at PSCAD's default zoom
PSCAD_TALL = 720       # a drawing this tall (grid units) goes on in another column: PSCAD's largest page is 800
PSCAD_PAGE = 800       # PSCAD's largest page (100 x 100 inches) in grid units
SYMBOL_HALF = {"XFMR2": 4, "XFMR3": 4, "PI": 3, "METER": 2, "SCALER": 2, "SOURCE": 3, "VENDOR": 3}   # half widths


def pscad_layout(comps, root="IB", cw=PSCAD_W, ch=PSCAD_H, gap=PSCAD_GAP, pad=PSCAD_PAD):
    """A single-line drawing, left to right from the infinite source: at each node its main branch carries on
       along the row, what hangs off it (shunts, loads, a GSU with its inverter) goes below it, its other branches
       below those. Sets every component's x, y (grid units) and its upstream node ("in"); returns the components
       in drawing order and the runs of R, L, C -- [{kind series | shunt, members [(tag, in, out)], a, b}] -- that
       the build script draws three times over, between breakouts, where PSCAD's R, L and C are single-phase."""
    tall = max([int(math.ceil(c["sym_h"] / 2.0)) + 2 for c in comps if c.get("sym_h")] or [0])
    ch = max(ch, 2 * tall)                       # rows far enough apart for the tallest symbol (a vendor model's)
    nodes = dict((id(c), [p["node"] for p in c["ports"] if p["node"] != "GND"]) for c in comps)
    at = collections.defaultdict(list)
    for c in comps:
        for n in nodes[id(c)]:
            at[n].append(c)

    def rlc(c):
        return c["kind"] in ("R", "L", "C")

    def mid(n):                                  # a node between two R / L / C in series and nothing else
        return len(at[n]) == 2 and all(rlc(c) and len(nodes[id(c)]) == 2 for c in at[n])

    def beyond(c, frm):
        out, seen_c, seen_n = [], set([id(c)]), set([frm])
        todo = [n for n in nodes[id(c)] if n != frm]
        while todo:
            n = todo.pop()
            if n in seen_n:
                continue
            seen_n.add(n)
            for c2 in at[n]:
                if id(c2) not in seen_c:
                    seen_c.add(id(c2))
                    out.append(c2)
                    todo.extend(m for m in nodes[id(c2)] if m not in seen_n)
        return out

    def small(c, frm):                           # a shunt, a source, or one component then a source or a shunt
        far = [n for n in nodes[id(c)] if n != frm]
        if not far:
            return True
        rest = beyond(c, frm)
        return len(far) == 1 and len(rest) <= 1 and all(len(nodes[id(x)]) == 1 for x in rest)

    placed, order = set(), []

    def put(c, x, y, frm):
        c["x"], c["y"], c["in"] = int(x), int(y), frm
        placed.add(id(c))
        order.append(c)

    def half(c):                                 # its symbol's half width (grid units)
        return SYMBOL_HALF.get(c["kind"], 3)

    def room(n):                                 # a wire long enough for node n's name
        return max(4, 2 * int(math.ceil(len(n) * NAME_CH / 2 + 0.75)))

    def chain(c, frm, x, y, edge=None):
        """c hanging off node frm (at x) on row y, then what lies beyond it -> (bottom, right). edge: the right
           edge of the symbol before it on its row: the wire between them long enough for frm's name."""
        x0 = x + (pad if rlc(c) and frm and not mid(frm) else 0) + c.get("lead", 0)
        if edge is not None and frm and not rlc(c):          # and the text by a vendor model's port clear of it
            x0 = max(x0, edge + room(frm) + 2 * c.get("pad_l", 0) + half(c) - cw // 2)
        put(c, x0 + cw // 2, y, frm)
        right, bottom = x0 + max(cw * c.get("room", 1), cw // 2 - half(c) + c.get("sym_w", 0) + 3), y
        far = [n for n in nodes[id(c)] if n != frm]
        if far:
            x1 = right + (pad if rlc(c) and not mid(far[0]) else 0)
            b, r = node(far[0], x1, y, None if rlc(c) else x0 + cw // 2 + half(c))
            bottom, right = max(bottom, b), max(right, r)
            for n2 in far[1:]:                   # a three-winding transformer's tertiary: below
                b, r = node(n2, x1, bottom + ch)
                bottom, right = max(bottom, b), max(right, r)
        return bottom, right

    def node(n, x, y, edge=None):
        kids = [c for c in at[n] if id(c) not in placed]
        chains = [c for c in kids if len(nodes[id(c)]) >= 2]
        big = [c for c in chains if not small(c, n)]
        main = big[0] if big else next((c for c in chains if not c.get("hang")), None)
        if main is None and not chains:
            main = next((c for c in kids if len(nodes[id(c)]) == 1 and not c.get("hang") and
                         not any(p["node"] == "GND" for p in c["ports"])), None)
        drops = [c for c in kids if c is not main and (len(nodes[id(c)]) == 1 or small(c, n))]
        others = [c for c in kids if c is not main and all(c is not d for d in drops)]
        bottom, right, yd, wide = y, x, y, 0
        for c in drops:
            yd += ch
            b, r = chain(c, n, x + gap, yd)
            bottom, right, wide = max(bottom, b, yd), max(right, r), max(wide, r - x)
        if main is not None:                     # its trunks clear of what hangs below this node
            b, r = chain(main, n, x + max(0, wide + 2 - cw // 2), y, edge)
            bottom, right = max(bottom, b), max(right, r)
        cx, cbot, part, k = x, bottom, 0, 0      # the column they go down in, its bottom, its number, how many
        for c in others:
            mark = len(order)
            b, r = chain(c, n, cx + gap, cbot + ch)
            if b > PSCAD_TALL and (k or cbot > y + ch):   # past the page below what is there already: a column
                for c2 in order[mark:]:          # of its own right of all so far, joined to the rest of n by n's
                    placed.discard(id(c2))       # name (a node label)
                    c2.pop("parts", None)
                del order[mark:]
                part, k, cx, cbot = part + 1, 0, right + room(n) + gap, y
                b, r = chain(c, n, cx + gap, cbot + ch)
            if part:
                c["parts"] = {n: part}
            k += 1
            cbot = max(cbot + ch, b)
            bottom, right = max(bottom, cbot), max(right, r)
        return bottom, right

    x0, y0 = 20, max(10, tall + 2)                # (a tall symbol on the first row: inside the page)
    for c in [c for c in at[root] if len(nodes[id(c)]) == 1]:
        if not any(p["node"] == "GND" for p in c["ports"]):
            put(c, x0 - cw // 2 - 6, y0, None)      # the infinite source, left of its bus: room for its name
    bottom, _r = node(root, x0, y0)
    for c in comps:                                     # in the room left beside the one it goes with (a timer)
        t = next((t for t in comps if t["tag"] == c.get("beside") and id(t) in placed), None)
        if t is not None and id(c) not in placed:
            put(c, t["x"] + cw, t["y"] + 2, None)
    xl = 20
    for c in comps:                                     # anything not reached from the source: a row of its own
        if id(c) not in placed:
            put(c, xl, bottom + 2 * ch, None)
            xl += cw
    groups, done = [], set()
    for c in order:
        if not rlc(c) or id(c) in done:
            continue
        ns = nodes[id(c)]
        if len(ns) == 1:
            groups.append({"kind": "shunt", "members": [[c["tag"], ns[0], "GND"]], "a": ns[0], "b": "GND"})
            done.add(id(c))
            continue
        frm = c["in"] if c["in"] in ns else ns[0]
        if mid(frm):
            continue
        run, n = [], frm
        while True:
            out = [m for m in nodes[id(c)] if m != n][0]
            run.append([c["tag"], n, out])
            done.add(id(c))
            nxt = [x for x in at[out] if id(x) not in done] if mid(out) else []
            if not nxt:
                break
            c, n = nxt[0], out
        groups.append({"kind": "series", "members": run, "a": frm, "b": run[-1][2]})
    return order, groups


def _tx2_comp(t, net, x, y):
    vg = t["vg"]
    tm = t["mva"] * t["count"]
    lead = _lead(vg, 1)
    params = [_pp("name", ["Name"], _node(t["name"])), _pp("MVA", ["Tmva"], "%s [MVA]" % _g(tm)),
              _pp("frequency", ["f", "F"], "%s [Hz]" % _g(net.f)),
              _pp("winding 1", ["YD1"], None, _yd(vg["w"][0])), _pp("winding 2", ["YD2"], None, _yd(vg["w"][1])),
              _pp("leakage X (pu)", ["Xl"], "%s [pu]" % _g(t["x_own"], 7)),
              _pp("copper losses (pu)", ["CuL"], "%s [pu]" % _g(t["r_own"], 7)),
              _pp("no-load losses (pu)", ["NLL"], "%s [pu]" % _g((t.get("nll_pct") or 0.0) / 100.0, 7)),
              _pp("winding 1 kV", ["V1"], "%s [kV]" % _g(t["windv1"], 8)),
              _pp("winding 2 kV", ["V2"], "%s [kV]" % _g(t["windv2"], 8)),
              _pp("view", ["View"], None, ["SINGLE"])]
    if lead:
        params.append(_pp("delta lags / leads Y", ["Lead"], None, lead))
    im = t.get("im_pct") or 0.0
    if im > 0:
        params += [_pp("ideal", ["Ideal"], None, ["NO"]), _pp("magnetising current (%)", ["Im1", "Im"], "%s [%%]"
                                                              % _g(im))]
    else:
        params.append(_pp("ideal", ["Ideal"], None, ["YES"]))
    return {"tag": t["name"], "kind": "XFMR2", "defn": ["master:xfmr-3p2w"], "x": x, "y": y, "params": params,
            "note": "%s %.1f MVA %.4g/%.4g kV Z %.3f%% X/R %s %s" % (t["kind"], tm, t["windv1"], t["windv2"],
                                                                     100.0 * math.hypot(t["r_own"], t["x_own"]),
                                                                     _g(t["x_own"] / t["r_own"], 4) if t["r_own"]
                                                                     else "inf", vg["text"]),
            "ports": [{"node": _node(t["hv"]), "pick": ["N1", "W1", "@left"]},
                      {"node": _node(t["lv"]), "pick": ["N2", "W2", "@right"]}]}


def _tx3_comp(t, net, x, y):
    vg = t["vg"]
    tm = t["mva1"] * t["count"]
    own = t["own"]
    xs = dict((p, own[p][4] * t["mva1"] / own[p][2]) for p in ("12", "23", "31"))     # on the HV rating
    rs = dict((p, own[p][3] * t["mva1"] / own[p][2]) for p in ("12", "23", "31"))
    params = [_pp("name", ["Name"], _node(t["name"])), _pp("MVA", ["Tmva"], "%s [MVA]" % _g(tm)),
              _pp("frequency", ["f", "F"], "%s [Hz]" % _g(net.f)),
              _pp("winding 1", ["YD1"], None, _yd(vg["w"][0])), _pp("winding 2", ["YD2"], None, _yd(vg["w"][1])),
              _pp("winding 3", ["YD3"], None, _yd(vg["w"][2])),
              _pp("X 1-2 (pu)", ["X12", "Xl12"], "%s [pu]" % _g(xs["12"], 7)),
              _pp("X 1-3 (pu)", ["X13", "Xl13"], "%s [pu]" % _g(xs["31"], 7)),
              _pp("X 2-3 (pu)", ["X23", "Xl23"], "%s [pu]" % _g(xs["23"], 7)),
              _pp("copper losses (pu)", ["CuL"], "%s [pu]" % _g(rs["12"], 7)),
              _pp("no-load losses (pu)", ["NLL"], "%s [pu]" % _g((t.get("nll_pct") or 0.0) / 100.0, 7)),
              _pp("winding 1 kV", ["V1"], "%s [kV]" % _g(t["windv"][0], 8)),
              _pp("winding 2 kV", ["V2"], "%s [kV]" % _g(t["windv"][1], 8)),
              _pp("winding 3 kV", ["V3"], "%s [kV]" % _g(t["windv"][2], 8)),
              _pp("view", ["View"], None, ["SINGLE"])]
    lead = _lead(vg, 1) or _lead(vg, 2)
    if lead:
        params.append(_pp("delta lags / leads Y", ["Lead"], None, lead))
    im = t.get("im_pct") or 0.0
    params.append(_pp("ideal", ["Ideal"], None, ["NO"] if im > 0 else ["YES"]))
    if im > 0:
        params.append(_pp("magnetising current (%)", ["Im1", "Im"], "%s [%%]" % _g(im)))
    return {"tag": t["name"], "kind": "XFMR3", "defn": ["master:xfmr-3p3w"], "x": x, "y": y, "params": params,
            "note": "MPT %.1f MVA %.4g/%.4g/%.4g kV X12 %.4f X13 %.4f X23 %.4f pu on %.1f MVA %s"
                    % (tm, t["windv"][0], t["windv"][1], t["windv"][2], xs["12"], xs["31"], xs["23"], t["mva1"],
                       vg["text"]),
            "ports": [{"node": _node(t["hv"]), "pick": ["N1", "W1", "@left"]},
                      {"node": _node(t["lv"]), "pick": ["N2", "W2", "@right"]},
                      {"node": _node(t["tv"]), "pick": ["N3", "W3", "@rest"]}]}


def _gen_slot(net, g, shift):
    """Where generator g's inverter connects in PSCAD (its collector drawn as the equivalent): its terminal (LV),
       or its GSU's HV side (MV: a UDM with its GSU inside -- no GSU drawn), with the load flow there."""
    at = g["pscad"]["at"] if g["gsu"] else "LV"
    node = g["bus"] if at == "LV" else g["mv"]
    b = net.bus[node]
    s = complex(g["p"], g["q"])
    if at == "MV":
        s = -next(f["s1"] for f in net.op["flows"] if f["name"] == g["gsu"]["name"])
    return {"name": g["name"], "gen": g["name"], "node": node, "kv": b["kv"], "vm": b["vm"],
            "ang": b["va"] + shift.get(node, 0.0), "units": g["n"], "mva": g["n"] * g["unit"]["mva"],
            "p": s.real, "q": s.imag, "at": at, "s_inv": complex(g["p"], g["q"]), "v_inv": net.bus[g["bus"]]["vm"]}


_SYMS = {}


def _sym_room(c, path, name, iid=None):
    """Vendor model c given its symbol's width and height (grid units) where the OEM's file can be read, so the
       layout leaves room for it -- and for the text beside its AC port, outside its wire's name -> c."""
    key = (path, name, iid)
    if key not in _SYMS:
        _SYMS[key] = oem_symbol(path, name, iid) if path and os.path.isfile(path) else None
    sym = _SYMS[key]
    if sym:
        c["sym_w"], c["sym_h"] = int(math.ceil(sym["w"])), int(math.ceil(sym["h"]))
        ac = next((k for k in c["ports"][0]["pick"] if not k.startswith("@")), None) if c["ports"] else None
        c["pad_l"] = port_pad(sym, ac)
    return c


def _inverter_comp(g, sl, net, x, y):
    """The inverter in its slot: the generator's vendor model (UDM), or a stand-in source holding the load flow."""
    ps = g["pscad"]
    node = _node(sl.get("inv", sl["node"]))            # its own node: past its meter and scaler, else its slot's
    what = "%s units, P %.3f MW, Q %.3f MVAr, V %.5f pu at %.4f deg (PSCAD angle)" % (
        _g(sl["units"]), sl["p"], sl["q"], sl["vm"], sl["ang"])
    src = ps["source"]
    case_file = bool(src) and os.path.splitext(src)[1].lower() != ".pslx"   # a case: its definition copied in
    if ps["iid"] or (src and case_file):                # the OEM's UDM: a placed one copied, or its definition
        sl["tag"] = sl["name"] + "_INV"
        sl["model"] = ("a copy of %s ID %d" % (os.path.basename(src), ps["iid"]) if ps["iid"] else
                       "%s from %s" % (ps["definition"], os.path.basename(src)))
        c = {"tag": sl["tag"], "kind": "VENDOR", "defn": [], "gen": g["name"], "slot": sl, "x": x, "y": y,
             "library": src if os.path.splitext(src)[1].lower() in (".pscx", ".pslx") else ps["library"],
             "params": [], "lead": 6, "half": SYMBOL_HALF["VENDOR"], "note": "inverter: %s%s -- %s" % (
                 sl["model"], ", its settings kept" if ps["iid"] else "", what),
             "ports": [{"node": node, "pick": ([ps["ac_port"]] if ps["ac_port"] else []) +
                        ["@dim3", "@first"]}]}
        if ps["iid"]:
            c["copy_of"] = {"source": src, "iid": ps["iid"]}
        if ps["definition"]:
            c["defn_from"] = {"source": src, "definition": ps["definition"].split(":")[-1]}
        _sym_room(c, src, ps["definition"].split(":")[-1] if ps["definition"] else None, ps["iid"])
        return c
    if src and ps["definition"]:                        # an OEM library: its definition used from it
        ps = dict(ps, library=src)
    if ps["definition"]:
        sl["tag"], sl["model"] = sl["name"] + "_INV", ps["definition"]
        return _sym_room({"tag": sl["tag"], "kind": "VENDOR", "defn": [ps["definition"]], "library": ps["library"],
                "gen": g["name"], "slot": sl, "x": x, "y": y, "params": [], "lead": 6, "half": SYMBOL_HALF["VENDOR"],
                "note": "inverter: vendor model %s -- %s" % (ps["definition"], what),
                "ports": [{"node": node, "pick": ([ps["ac_port"]] if ps["ac_port"] else []) +
                           ["@dim3", "@first"]}]}, ps["library"], ps["definition"].split(":")[-1])
    sl["tag"], sl["model"] = sl["name"] + "_SRC", "stand-in source"
    c = _source(sl["tag"], sl["name"], sl["kv"], sl["vm"], sl["ang"], net.f, x, y, node)
    c["lead"] = 6                                       # a wire of its own to its node: the node's name, a UDM's place
    c["orient"] = 4                                     # mirrored: its terminal faces the node (on its left)
    c["note"] += (" -- stand-in for the inverter (%s): holds the load flow; your UDM goes on node %s"
                  % (what, _node(sl["node"])))
    c["gen"], c["slot"] = g["name"], sl
    return c


def pscad_shifts(vg):
    """The angle of each winding against winding 1 in PSCAD's classical transformer (deg): 0 for the same
       connection, +-30 by its one 'delta lags / leads Y' -- for a 3-winding one, set by winding 2 (or 3 when
       winding 2 is like winding 1). The vector group's own angles where it can follow them."""
    lead = _lead(vg, 1) or (_lead(vg, 2) if len(vg["w"]) > 2 else None)
    d = -30.0 if lead == ["LAG"] else 30.0
    return [0.0] + [0.0 if w == vg["w"][0] else (d if w == "D" else -d) for w in vg["w"][1:]]


def phase_shifts(net):
    """The angle PSCAD's transformers add on the way from the infinite bus to every bus (deg); PSS/E has none."""
    out = {"IB": 0.0}
    edges = collections.defaultdict(list)
    for ln in net.lines:
        edges[ln["fr"]].append((ln["to"], 0.0))
        edges[ln["to"]].append((ln["fr"], 0.0))
    for t in net.tx2:
        s = pscad_shifts(t["vg"])[1]
        edges[t["hv"]].append((t["lv"], s))
        edges[t["lv"]].append((t["hv"], -s))
    for t in net.tx3:
        s = pscad_shifts(t["vg"])
        for a, b, d in ((t["hv"], t["lv"], s[1]), (t["hv"], t["tv"], s[2]), (t["lv"], t["tv"], s[2] - s[1])):
            edges[a].append((b, d))
            edges[b].append((a, -d))
    todo = ["IB"]
    while todo:
        a = todo.pop()
        for b, d in edges[a]:
            if b not in out:
                out[b] = out[a] + d
                todo.append(b)
    return out


PSCAD_KEYS = ("UNITS", "MVA", "P_MW", "Q_MVAR", "P_PU", "Q_PU", "UNIT_MVA", "UNIT_P_MW", "UNIT_Q_MVAR", "V_PU",
              "V_KV", "KV", "ANGLE", "FREQ", "P_INV_MW", "Q_INV_MVAR", "V_INV_PU")


def slot_values(sl, net):
    """The values PSCAD_Params can take for one inverter slot ({UNITS}, {P_PU} ...)."""
    u, mva = sl["units"], sl["mva"]
    return {"UNITS": u, "MVA": mva, "P_MW": sl["p"], "Q_MVAR": sl["q"], "P_PU": sl["p"] / mva if mva else 0.0,
            "Q_PU": sl["q"] / mva if mva else 0.0, "UNIT_MVA": mva / u if u else 0.0,
            "UNIT_P_MW": sl["p"] / u if u else 0.0, "UNIT_Q_MVAR": sl["q"] / u if u else 0.0, "V_PU": sl["vm"],
            "V_KV": sl["vm"] * sl["kv"], "KV": sl["kv"], "ANGLE": sl["ang"], "FREQ": net.f,
            "P_INV_MW": sl["s_inv"].real, "Q_INV_MVAR": sl["s_inv"].imag, "V_INV_PU": sl["v_inv"]}


def _fill_pscad(text, vals, where):
    def rep(m):
        key = m.group(1).upper()
        if key not in vals:
            raise InputError("%s: {%s} -- one of %s" % (where, m.group(1), " ".join("{%s}" % k for k in PSCAD_KEYS)))
        v = vals[key]
        return "%d" % v if key == "UNITS" and float(v) == int(v) else _g(float(v), 7)
    return re.sub(r"\{([A-Za-z_]+)\}", rep, text)


def udm_inner(net, cm, inner, where):
    """Settings of parts inside vendor model cm's definition (Controller4/UFLevel1 ...): the build script changes
       them in a copy of the definition of the generator's own (<definition>_<generator>) -- a UDM to be copied
       from a case is made from its definition instead, the copied component's own values set on it."""
    g = next(x for x in net.gens if x["name"] == cm["gen"])
    fam, ps = g.get("family", g["name"]), g["pscad"]
    for v, w in zip(inner.values(), where):
        if "{" in str(v):
            raise InputError("%s: %s -- a part inside the vendor model takes a value, not a {KEY}: its settings are "
                             "in the definition, one for all of %s's inverters" % (w, v, fam))
    src = ps["source"] or ps["library"]
    if cm.get("copy_of"):
        form = udm_form(src, None, ps["iid"]) if src and os.path.isfile(src) else None
        if form is None:
            raise InputError("%s: settings of parts inside its vendor model need its definition -- %s ID %s cannot "
                             "be read here" % (where[0], src, ps["iid"]))
        cm.pop("copy_of")
        cm["defn_from"] = {"source": src, "definition": form["defn"]}
        own = set(q["name"] for q in form["params"])
        cm["params"] = [_pp(k, [k], v) for k, v in form["placed"].items() if k in own and
                        not any(it["names"][0].upper() == k.upper() for it in cm["params"])] + cm["params"]
        note = "NOTE: PSCAD: %s: its UDM made from its definition %s (not copied: parts inside it changed), the " \
               "copied component's own values set on it" % (fam, form["defn"])
        if note not in net.notes:
            net.notes.append(note)
    if not cm.get("defn_from"):
        raise InputError("%s: settings of parts inside its vendor model need its definition in a file (Generators: "
                         "PSCAD source case or library, and definition)" % where[0])
    cm["defn_from"].update(inner=dict(inner), tag=_safe_name(fam, 12))


def pscad_spec(net, inp, comps):
    params = collections.defaultdict(list)
    for r in inp["pscad_params"]:
        gname = _txt(r.get("generator"))
        names = members(net).get((gname or "").upper())
        if not names:
            _no_gen(net, r["_where"], gname, list(members(net, family_only=True)))
            continue
        g = next(x for x in net.gens if x["name"] == names[0])
        if not g["pscad"]["definition"]:
            net.notes.append("NOTE: %s: PSCAD_Params for %s, which has no PSCAD definition -- not used"
                             % (r["_where"], gname))
        for nm in names:
            params[nm].append((_txt(r.get("parameter")), _txt(r.get("value")) or "", r["_where"]))
    for cm in comps:
        if cm["kind"] == "VENDOR":
            vals = slot_values(cm["slot"], net)
            mine = params.get(cm["gen"], [])
            cm["params"] = [_pp(p, [p], _fill_pscad(v, vals, w)) for p, v, w in mine if "/" not in p]
            inner = collections.OrderedDict((p, v) for p, v, w in mine if "/" in p)
            if inner:
                udm_inner(net, cm, inner, [w for p, v, w in mine if "/" in p])
    c = net.ctl["pscad"]
    libs = list(c["libraries"]) + [cm["library"] for cm in comps if cm.get("library")]
    return {"case": _safe_name(net.name, 29 - len(net.model)) + "_" + net.model,
            "written": time.strftime("%Y-%m-%d %H:%M"),
            "settings": {"time_duration": c["duration_s"], "time_step": c["time_step_us"],
                         "sample_step": c["plot_step_us"], "description": ("%s %s" % (net.model, net.name))[:60]},
            "libraries": [l for k, l in enumerate(libs) if l and l not in libs[:k]],
            "slots": [{"tag": sl["tag"], "node": _node(sl.get("inv", sl["node"])), "units": sl["units"],
                       "p": sl["p"], "q": sl["q"],
                       "vm": sl["vm"], "ang": sl["ang"], "model": sl["model"]} for sl in net.pscad_slots],
            "groups": [{"kind": gr["kind"], "a": _node(gr["a"]), "b": _node(gr["b"]),
                        "members": [[t, _node(i), _node(o)] for t, i, o in gr["members"]]} for gr in net.pscad_groups],
            "components": [dict((k, v) for k, v in cm.items() if k not in ("value", "unit", "slot", "gen", "from_to"))
                           for cm in comps]}


_BUILD_PSCAD = r'''# -*- coding: utf-8 -*-
"""Builds the PSCAD case of the SMIB model @NAME@ (written by smib_build.py on @DATE@).

   Run it with a Python that has PSCAD's automation library, mhi.pscad (PSCAD 5 installs it; or
   py -m pip install mhi.pscad):
       py -3 @FILE@
   It uses the PSCAD that is running (or starts one), loads the user-model libraries, creates the
   case in this folder under a free name (nothing is overwritten), places every component, sets its
   parameters, draws the single-line diagram with wires (R, L and C three times over, between breakouts,
   where they are single-phase in this PSCAD), names each node, sizes the page to the drawing and saves
   the case. Its log, @NAME@_build_pscad_<date_time>.txt, lists every parameter set (as PSCAD reads
   it back), everything it could not do (CHECK:) and each component type's own parameter names, so
   anything left can be set by hand. The values themselves are in @NAME@_PSCAD_DATA.txt.
"""
from __future__ import print_function

import os
import re
import sys
import json
import time
import traceback
import xml.etree.ElementTree as ET

LAUNCH_NEW = False        # True = start a PSCAD of its own even when one is running
PSCAD_VERSION = None      # None = the newest installed | "5.0.2", "5.1.0" ...
HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.loads(r"""@SPEC@""")


class Log(object):
    def __init__(self, path):
        self.fh = open(path, "w")
        self.checks = 0

    def __call__(self, msg=""):
        print(msg)
        if "CHECK:" in msg:
            self.checks += 1
        self.fh.write(msg + "\n")
        self.fh.flush()


def _free(stem, ext):
    """stem + ext, or stem_2 + ext ... -- whichever does not exist yet."""
    k, s = 1, stem
    while os.path.exists(os.path.join(HERE, s + ext)):
        k += 1
        s = "%s_%d" % (stem[:30 - len(str(k)) - 1], k)
    return s


def eports(cmp):
    """[(name, x, y, dim)] of the component's electrical ports (grid units, after rotation): every one, as PSCAD
       lists them -- ports() keeps one of two that share a name, as a meter's two ends can -- the second of a
       name as name#2."""
    try:
        raw = list(cmp._ports())
    except Exception:
        try:
            raw = list(cmp.ports().values())
        except Exception:
            return []
    out, names = [], set()
    for p in raw:
        try:
            if int(p[4]) not in (3, 4):               # electrical, or one end of a short (a meter's)
                continue
            name = str(p[2]).split(":")[0]
            nm, k = name, 1
            while nm in names:
                k += 1
                nm = "%s#%d" % (name, k)
            names.add(nm)
            out.append((nm, int(p[0]), int(p[1]), int(p[3])))
        except Exception:
            pass
    return out


def sports(cmp, kind):
    """[(name, x, y)] of the component's signal ports: kind 1 its inputs, 2 its outputs."""
    try:
        ports = cmp.ports()
    except Exception:
        return []
    out = []
    for name, p in ports.items():
        try:
            if int(p[4]) == kind:
                out.append((str(name), int(p[0]), int(p[1])))
        except Exception:
            pass
    return out


def link(canvas, src, dst, what, log):
    """A wire from src's output to dst's nearest input (a timer to what it drives)."""
    outs, ins = sports(src, 2), sports(dst, 1)
    if not outs or not ins:
        log("CHECK: %s: no %s found -- join them by hand" % (what, "output" if not outs else "input"))
        return
    o = outs[0]
    i = min(ins, key=lambda p: abs(p[1] - o[1]) + abs(p[2] - o[2]))
    mx = (o[1] + i[1]) // 2
    try:
        wire(canvas, (o[1], o[2]), (mx, o[2]), (mx, i[2]), (i[1], i[2]))
        log("%s: %s (%d, %d) -> %s (%d, %d)" % (what, o[0], o[1], o[2], i[0], i[1], i[2]))
    except Exception as e:
        log("CHECK: %s: no wire (%s) -- join them by hand" % (what, e))


def pick(ports, picks, taken):
    free = [p for p in ports if p[0] not in taken]
    allp = sorted(ports, key=lambda p: (p[1], p[2]))
    for k in picks:
        if not k:
            continue
        if not k.startswith("@"):
            for p in free:
                if p[0].split("#")[0].upper() == k.upper():
                    return p
            continue
        if not free:
            return None
        if k == "@first" and allp and allp[0] in free:
            return allp[0]
        if k == "@second" and len(allp) > 1 and allp[1] in free:
            return allp[1]
        if k == "@left":
            return min(free, key=lambda p: (p[1], p[2]))
        if k == "@right":
            return max(free, key=lambda p: (p[1], -p[2]))
        if k == "@top":
            return min(free, key=lambda p: (p[2], p[1]))
        if k == "@bottom":
            return max(free, key=lambda p: (p[2], -p[1]))
        if k == "@dim3":
            for p in free:
                if p[3] == 3:
                    return p
        if k == "@rest":
            return free[0]
    return None


def choice_value(cmp, name, words):
    """The legal value of a choice parameter whose name holds every word (Y, DELTA, LEAD, SINGLE ...)."""
    try:
        rng = cmp.range(name)
    except Exception:
        rng = None
    keys = [k for k in (list(rng) if isinstance(rng, (set, frozenset, tuple, list)) else []) if isinstance(k, str)]
    for k in keys:
        if all(w.upper() in k.upper() for w in words):
            return k
    if keys and words == ["Y"]:                       # "Y" is in "DELTA_Y ..." too: the shortest key with a Y
        ys = [k for k in keys if "Y" in k.upper() and "DELTA" not in k.upper()]
        if ys:
            return ys[0]
    if words in (["YES"], ["NO"]):
        return words == ["YES"]
    return None


def set_params(cmp, items, log, quiet=False, names=None, read_back=True):
    """Set the component's parameters -- in one call, one at a time where that is refused -- and log them (as
       PSCAD reads them back where read_back). names: its parameters' names, when known (saves a call)."""
    if names is None:
        try:
            names = list((cmp.parameters() or {}).keys())
        except Exception as e:
            log("    CHECK: its parameters cannot be read (%s)" % e)
            return
    have = set(names)
    up = dict((k.upper(), k) for k in names)
    todo = []
    for it in items:
        found = [n for n in it["names"] if n in have] or [up[n.upper()] for n in it["names"] if n.upper() in up]
        if not found:
            if it.get("need", True):
                log("    CHECK: %-26s no parameter %s -- set it by hand: %s"
                    % (it["what"], "/".join(it["names"]), it["value"] if it["value"] is not None
                       else "/".join(it["choice"] or [])))
            continue
        name = found[0]
        val = it["value"]
        if it.get("choice"):
            val = choice_value(cmp, name, it["choice"])
            if val is None:
                log("    CHECK: %-26s %s: no choice like %s (its choices: %s)"
                    % (it["what"], name, "/".join(it["choice"]), _range(cmp, name)))
                continue
        todo.append((it, name, val))
    if not todo:
        return
    refused = set()
    try:
        cmp.parameters(parameters=dict((name, val) for _it, name, val in todo))
    except Exception:
        for it, name, val in todo:                    # one at a time: which one is refused
            try:
                cmp.parameters(parameters={name: val})
            except Exception as e:
                refused.add(name)
                log("    CHECK: %-26s %s = %r refused: %s" % (it["what"], name, val, e))
    if quiet:
        return
    got = {}
    if read_back:
        try:
            got = cmp.parameters() or {}
        except Exception:
            read_back = False
    for it, name, val in todo:
        if name in refused:
            continue
        if read_back:
            log("    %-28s %-8s = %-22s read back: %s" % (it["what"], name, val, got.get(name)))
        else:
            log("    %-28s %-8s = %s" % (it["what"], name, val))


def _norm(text):
    return re.sub(r"\s+", "", str(text or "")).upper()


def definition(pscad, defn):
    """The definition object of "library:name" (None where this mhi.pscad has none)."""
    try:
        lib, name = defn.split(":", 1)
        return pscad.project(lib).definition(name)
    except Exception:
        return None


def form_of(d):
    """A definition's parameter form: [(page, name, description, [(code, choice text)])]; [] where it cannot be
       read."""
    try:
        x = getattr(d, "xml", None)
        x = x() if callable(x) else x
        if isinstance(x, (bytes, str)):
            x = ET.fromstring(x)
        form = x if x.tag == "form" else x.find(".//form")
        out = []
        for cat in form.iter("category"):
            page = cat.get("name") or cat.get("desc") or ""
            for p in cat.iter("parameter"):
                desc = p.get("desc") or p.get("description") or " ".join(
                    v for k, v in p.attrib.items() if k not in ("name", "type", "unit", "min", "max", "content_type"))
                ch = []
                for t in p.iter("choice"):
                    code, _eq, text = (t.text or "").partition("=")
                    ch.append((code.strip(), text.strip()))
                out.append((page, p.get("name"), desc, ch))
        return out
    except Exception:
        return []


def find_def(pscad, words, log):
    """The master library's definition whose form has a parameter described by every word: names with "pi" in
       them first, then the rest -> "master:name" or None."""
    try:
        master = pscad.project("master")
        names = [str(n) for n in master.definitions()]
    except Exception as e:
        log("(the master library's definitions cannot be listed: %s)" % e)
        return None
    t0 = time.time()
    for n in sorted(names, key=lambda n: "PI" not in n.upper()):
        if time.time() - t0 > 120:
            log("(stopped looking after 120 s)")
            break
        try:
            form = form_of(master.definition(n))
        except Exception:
            continue
        if any(all(_norm(w) in _norm(d) for w in words) for _pg, _n, d, _c in form):
            return n if ":" in n else "master:" + n
    return None


def form_pick(items, form, log):
    """Items found in the component's form by their descriptions: the form's name first, a choice set to the code
       of the one whose text has the words (pick); a CHECK where the form has no such parameter or choice."""
    out = []
    for it in items:
        it = dict(it)
        if form and it.get("desc"):
            hit = next((f for f in form if all(_norm(w) in _norm(f[0]) for w in it.get("cat") or []) and
                        all(_norm(w) in _norm(f[2]) for w in it["desc"]) and
                        not any(_norm(w) in _norm(f[2]) for w in it.get("nope") or [])), None)
            if hit is None:
                log("    CHECK: %-26s no parameter described as %r%s in its form" % (
                    it["what"], " ".join(it["desc"]), (" on page %s" % " ".join(it["cat"])) if it.get("cat") else ""))
            else:
                it["names"] = [hit[1]] + [n for n in it["names"] if n != hit[1]]
                code = None
                for words in it.get("pick") or []:
                    code = next((k for k, t in hit[3] if all(_norm(w) in _norm(t) for w in words)), None)
                    if code is not None:
                        break
                if code is not None:
                    it["value"], it["choice"] = code, None
                elif it.get("pick"):
                    log("    CHECK: %-26s %s: no choice like %s (its choices: %s)" % (
                        it["what"], hit[1], " / ".join(" ".join(w) for w in it["pick"]),
                        "; ".join("%s = %s" % c for c in hit[3]) or "none"))
        out.append(it)
    return out


def _range(cmp, name):
    try:
        return sorted(str(x) for x in cmp.range(name))
    except Exception:
        return "?"


DY = 4                     # single-phase R, L, C: the phases' rows, top to bottom (grid units)
TRACE = []                 # SMIB_PSCAD_TRACE=<file>: what was drawn, for smib_build's own tests
NO_BREAKOUT = []           # why this PSCAD has no breakout, once found
PAGES = (("85X11", 88, 68), ("11X17", 136, 88), ("17X22", 176, 136), ("22X34", 272, 176), ("34X44", 352, 272),
         ("100X100", 800, 800))  # PSCAD's page sizes, landscape: (size, width, height) in grid units
DRAWN = []                 # every component placed
REACH = [0, 0]             # how far right and down the drawing goes (grid units)
PAGE = [0, 0]              # the page set: width, height (grid units)
NEED = [0, 0]              # the drawing's size it was set for
NAMES = {}                 # each component type's parameter names (read once)
FORMS = {}                 # each component type's form, where its parameters are found by their descriptions
BREAKOUT_ORIENT = {}       # the breakout orientation that faced the phases, each side
READ_BACK = False          # True: log every value as PSCAD reads it back (slower); else the first of each type
T0 = time.time()


def _trace(kind, **kw):
    if os.environ.get("SMIB_PSCAD_TRACE"):
        kw["kind"] = kind
        TRACE.append(kw)


def _made(cmp, x, y):
    """A component placed at (x, y): counted in the drawing's reach (its symbol's own size added at the end)."""
    DRAWN.append(cmp)
    REACH[0], REACH[1] = max(REACH[0], int(x) + 4), max(REACH[1], int(y) + 4)
    return cmp


def page(canvas, w, h, log):
    """The smallest page -- landscape, else portrait -- that holds a drawing w x h grid units: PSCAD shows
       nothing past the page's edge (a new case starts on the smallest, 88 x 68)."""
    fits = [(s, o, a, b) for s, pw, ph in PAGES for o, a, b in (("LANDSCAPE", pw, ph), ("PORTRAIT", ph, pw))
            if a >= w and b >= h]
    fits += [(s, "LANDSCAPE", pw, ph) for s, pw, ph in reversed(PAGES) if (s, "LANDSCAPE", pw, ph) not in fits]
    err = None
    NEED[0], NEED[1] = w, h
    for size, orient, a, b in fits:
        try:
            canvas.parameters(size=size, orient=orient)
        except Exception as e:
            err = e
            continue
        PAGE[0], PAGE[1] = a, b
        try:
            got = canvas.parameters() or {}
            got = "%s %s" % (got.get("size", "?"), got.get("orient", "?"))
        except Exception:
            got = "?"
        log("page: %s %s, %d x %d grid units (read back: %s), for a drawing %d x %d" % (size, orient, a, b, got, w, h))
        if a < w or b < h:
            log("CHECK: the drawing (%d x %d grid units) is larger than the largest page this PSCAD takes: what lies "
                "past %d, %d is not shown -- move it onto the page by hand" % (w, h, a, b))
        return True
    log("CHECK: the page size cannot be set here (%s) -- set the canvas's size to %s, %s by hand (right-click an "
        "empty spot of the canvas): the drawing is %d x %d grid units and PSCAD shows nothing past the page's edge"
        % (err, fits[0][0], fits[0][1], w, h))
    return False


def reach(canvas):
    """How far right and down what was drawn goes (grid units): the symbols' own bounds where PSCAD gives them."""
    w, h = REACH
    try:
        rects = list(canvas.bounds(DRAWN))
    except Exception:
        rects = []
    for r in rects:
        try:
            w, h = max(w, int(r[2])), max(h, int(r[3]))
        except Exception:
            pass
    return w + 2, h + 2


def _move(cmp, dx, dy):
    """Shift a component by (dx, dy) grid units; False when this mhi.pscad cannot."""
    if not dx and not dy:
        return True
    try:
        x, y = cmp.location
        cmp.location = (x + dx, y + dy)
    except Exception:
        try:
            x, y = cmp.get_location()
            cmp.set_location(x + dx, y + dy)
        except Exception:
            return False
    return True


PI_HALF = 3                # a Coupled PI Section: its terminals this far either side of its middle (grid units)


def pi_ports(cmp, c, reported, log, first):
    """A Coupled PI Section's two ports where its symbol has them. PSCAD can report its ports where it does not
       draw them, so they come from its drawn extent (its bounds): the symbol moved first to sit centred on its
       place in the layout, its ports at its two ends on that row; PI_HALF either side of it where its bounds
       cannot be read."""
    got = None
    for attempt in (0, 1):
        try:
            b = [float(v) for v in tuple(cmp.bounds)]
        except Exception:
            b = None
        if not b or not (2 <= b[2] - b[0] <= 24 and 0 <= b[3] - b[1] <= 10):
            break
        xm, ym = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        dx, dy = int(round(c["x"] - xm)), int(round(c["y"] - ym))
        if attempt == 0 and (dx or dy) and _move(cmp, dx, dy):
            if first:
                log("    moved (%+d, %+d): its symbol centred on its place, (%d, %d)" % (dx, dy, c["x"], c["y"]))
            continue
        half = int(round((b[2] - b[0]) / 2.0))
        got = (int(round(xm)) - half, int(round(xm)) + half, int(round(ym)))
        break
    if got is None:
        try:
            x, y = [int(v) for v in cmp.location]
        except Exception:
            x, y = c["x"], c["y"]
        got = (x - PI_HALF, x + PI_HALF, y)
    names = [p[0] for p in sorted(reported, key=lambda p: (p[1], p[2]))]
    dim = max([p[3] for p in reported] or [3])
    out = [(names[0] if names else "N1", got[0], got[2], dim),
           (names[-1] if len(names) > 1 else "N2", got[1], got[2], dim)]
    if sorted((p[1], p[2]) for p in reported) != sorted((p[1], p[2]) for p in out):
        PI_NOTE.append("%s: PSCAD reports its ports at %s; drawn at (%d, %d), (%d, %d) -- wired there" % (
            c["tag"], ", ".join("(%d, %d)" % (p[1], p[2]) for p in reported) or "none", out[0][1], out[0][2],
            out[1][1], out[1][2]))
    return out


PI_NOTE = []               # the PI Sections whose ports PSCAD reports where it does not draw them
METER_NOTE = []            # the meters (and current scalers) PSCAD reports one end of


def two_ends(cmp, c, ports):
    """A meter's or a current scaler's two ends: where PSCAD reports one only, the other opposite it, about the
       middle of its symbol (of its place, where its extent cannot be read)."""
    if len(ports) != 1:
        return ports
    b = _box(cmp)
    try:
        mid = (b[0] + b[2]) / 2.0 if b else float(tuple(cmp.location)[0])
    except Exception:
        mid = c["x"]
    p = ports[0]
    x = int(round(2 * mid - p[1]))
    if x == p[1]:
        return ports
    q = (p[0] + "#2", x, p[2], p[3])
    METER_NOTE.append("%s: PSCAD reports one end, %s at (%d, %d) -- the other taken at (%d, %d)" % (
        c["tag"], p[0], p[1], p[2], q[1], q[2]))
    return sorted([p, q], key=lambda r: r[1])


def on_row(cmp, c, ports, log, first):
    """A two-ended part whose ends PSCAD puts off its place (the current scaler's: a grid unit above it, not
       about its middle) moved so that they lie on its row, about its place -> its ports."""
    if len(ports) != 2 or ports[0][2] != ports[1][2]:
        return ports
    dx = int(c["x"] - (ports[0][1] + ports[1][1]) // 2)
    dy = int(c["y"] - ports[0][2])
    if (dx or dy) and _move(cmp, dx, dy):
        if first:
            log("    moved (%+d, %+d): its ends on its row, about its place" % (dx, dy))
        return [(p[0], p[1] + dx, p[2] + dy, p[3]) for p in ports]
    return ports


PORT_NOTE = {}             # the vendor models whose AC port PSCAD reports where their symbol does not draw it
DEFN_XML = {}              # "case:name" -> its definition's XML (an OEM's, or read from PSCAD)
MIRRORED = set()           # the vendor models mirrored here: their symbol had the AC port on its right


def _box(cmp):
    """A component's drawn extent [x1, y1, x2, y2] (grid units), or None."""
    try:
        b = [float(v) for v in tuple(cmp.bounds)]
        return b if len(b) == 4 and b[2] > b[0] else None
    except Exception:
        return None


def _mirror(canvas, cmp, how):
    """Mirror a component left to right, one way (its own command, the canvas's, its orientation) -> whether
       this mhi.pscad took it."""
    try:
        if how == "component":
            return cmp.mirror() is not False
        if how == "canvas":
            return canvas.mirror(cmp) is not False
        cmp.orient = int(cmp.orient) ^ 4
        return True
    except Exception:
        return False


def drawn_ports(el):
    """A definition's electrical ports where its symbol draws them: {name: [(x, y)]}, grid units from its origin
       ({} where its XML has no symbol)."""
    out = {}
    g = el.find(".//graphics") if el is not None else None
    for x in (g.iter() if g is not None else []):
        if x.tag.lower() != "port":
            continue
        prm = dict(((q.get("name") or "").lower(), q.get("value") or "") for q in x.iter("param"))
        name = x.get("name") or prm.get("name")
        if name and (prm.get("mode") or x.get("mode") or "ELECTRICAL").upper() in ("ELECTRICAL", "3"):
            try:
                out.setdefault(name, []).append((float(x.get("x")) / 18.0, float(x.get("y")) / 18.0))
            except (TypeError, ValueError):
                pass
    return out


def _defn_el(pscad, used):
    if used not in DEFN_XML:
        el = None
        try:
            x = getattr(definition(pscad, used), "xml", None)
            x = x() if callable(x) else x
            el = ET.fromstring(x) if isinstance(x, (bytes, str)) else x
        except Exception:
            el = None
        DEFN_XML[used] = el
    return DEFN_XML[used]


def _on_edge(b, x, y):
    """Whether (x, y) lies on box b's outline, half a grid unit either way."""
    return b is not None and ((min(abs(x - b[0]), abs(x - b[2])) <= 0.5 and b[1] - 0.5 <= y <= b[3] + 0.5) or
                              (min(abs(y - b[1]), abs(y - b[3])) <= 0.5 and b[0] - 0.5 <= x <= b[2] + 0.5))


def udm_port(pscad, cmp, c, used, p):
    """Port p (name, x, y, dim) as PSCAD reports it -- or where the vendor model's symbol draws it, when PSCAD
       reports it off the symbol's outline and its definition draws it once, within the symbol."""
    b = _box(cmp)
    if c.get("copy_of") or b is None or _on_edge(b, p[1], p[2]):
        return p
    spots = sorted(set(drawn_ports(_defn_el(pscad, used)).get(p[0], [])))
    try:
        lx, ly = [float(v) for v in cmp.location]
    except Exception:
        return p
    if len(spots) != 1:
        return p
    sx, sy = spots[0]
    d = (int(round(lx + (-sx if c["tag"] in MIRRORED else sx))), int(round(ly + sy)))
    if d == (p[1], p[2]) or not (b[0] - 0.5 <= d[0] <= b[2] + 0.5 and b[1] - 0.5 <= d[1] <= b[3] + 0.5):
        return p
    PORT_NOTE[c["tag"]] = "%s: PSCAD reports its port %s at (%d, %d); its symbol draws it at (%d, %d) -- wired " \
        "there" % (c["tag"], p[0], p[1], p[2], d[0], d[1])
    return (p[0], d[0], d[1], p[3])


def udm_fit(pscad, canvas, cmp, c, used, ports, log, first):
    """A vendor model (UDM) in its slot: its AC port on the side facing its GSU -- mirrored when its symbol has
       the port on its right -- and at the end of its lead, the wire from its node -> its electrical ports."""
    p = pick(ports, c["ports"][0]["pick"], set()) if c["ports"] else None
    if p is None:
        return ports

    def now(name):                                            # its ports, and the port as PSCAD reports it
        got = eports(cmp)
        return got, next((x for x in got if x[0] == name), None)

    def x_at():
        try:
            return float(tuple(cmp.location)[0])
        except Exception:
            return None
    p0, x0 = p, x_at()
    p = udm_port(pscad, cmp, c, used, p)
    b = _box(cmp)
    if b is not None and p[1] > (b[0] + b[2]) / 2.0:          # on its right, away from its GSU: mirrored
        for how in ("component", "canvas", "orient"):
            if not _mirror(canvas, cmp, how):
                continue
            MIRRORED.symmetric_difference_update([c["tag"]])
            got, q0 = now(p[0])
            x1, b2 = x_at(), _box(cmp)
            if q0 is not None and None not in (x0, x1) and q0[1] != x1 and p0[1] != x0:
                turned = (q0[1] - x1) * (p0[1] - x0) < 0          # as reported: across its middle
            else:
                turned = q0 is not None and b2 is not None and q0[1] < (b2[0] + b2[2]) / 2.0
            q = udm_port(pscad, cmp, c, used, q0) if turned else None
            if q is not None and b2 is not None and q[1] < (b2[0] + b2[2]) / 2.0:
                ports, p, b = got, q, b2
                if first:
                    log("    its AC port %s is on its right: mirrored to face its GSU" % p[0])
                break
            _mirror(canvas, cmp, how)                         # not left to right that way: back as it was
            MIRRORED.symmetric_difference_update([c["tag"]])
        else:
            log("    CHECK: its AC port %s is on its right and it cannot be mirrored here -- the wire crosses it to "
                "the port: mirror it by hand (right-click > Mirror) and wire the port to node %s"
                % (p[0], c["ports"][0]["node"]))
    tx, ty = c["x"] - c.get("half", 3), c["y"]                 # the end of its lead
    dx = int(round(tx - (min(p[1], b[0]) if b is not None else p[1])))
    dy = int(round(ty - p[2]))
    if (dx or dy) and _move(cmp, dx, dy):
        got, q = now(p[0])
        if q is not None:
            ports, p = got, udm_port(pscad, cmp, c, used, q)
        if first:
            log("    moved (%+d, %+d): its AC port %s at the end of its lead, (%d, %d)" % (dx, dy, p[0], p[1], p[2]))
    return [p if x[0] == p[0] else x for x in ports]
COPIES = {}                # (source, ID) -> the first copy of it in this case: the rest are its clones
RESOURCES = set()          # the source cases whose resources (.lib, .obj ...) this case has been given


IMPORTED = {}              # (OEM file, definition) -> its name in this case


def oem_definitions(path):
    """The definitions in an OEM's PSCAD model (.pscx / .pslx) as XML -> (its project name, {name: element})."""
    root = ET.parse(path).getroot()
    scope = root.get("name") or os.path.splitext(os.path.basename(path))[0]
    defs = {}
    for el in root.iter():
        if el.tag.lower() == "definition" and el.get("name"):
            defs.setdefault(el.get("name"), el)
    return scope, defs


ADDED = set()              # (OEM file, definition) added to this case


def part_in(el, step):
    """The part `step` placed in definition el's schematic -- by its definition's name, name#k (k from 1) where it
       has more than one, as the GUI names them -- or None."""
    sch = el.find("schematic") if el is not None else None
    users = list(sch.iter("User")) if sch is not None else []
    nm, _h, k = step.partition("#")
    hits = [u for u in users if (u.get("defn") or "").split(":")[-1].upper() == nm.upper()]
    try:
        k = int(k) if k else (1 if len(hits) == 1 else 0)
    except ValueError:
        return None
    return hits[k - 1] if 0 < k <= len(hits) else None


def edit_parts(defs, scope, top, edits, tag, log):
    """Settings of parts inside definition `top` changed -- edits {"Controller4/UFLevel1": "57" ...}, a part inside
       a part as Controller4/PI#2/Kp -- in copies of the definitions they are in, named <name>_<tag>, each copy
       used in its place -> (the definitions with the copies, the name of top's copy)."""
    import copy as copies_of
    defs, copies = dict(defs), {}

    def own(n):
        if n not in copies:
            el = copies_of.deepcopy(defs[n])
            copies[n] = "%s_%s" % (n, tag)
            el.set("name", copies[n])
            defs[copies[n]] = el
        return copies[n]
    top2 = own(top)
    for path, value in edits.items():
        steps, cur, part = path.split("/"), top2, None
        for i, st in enumerate(steps[:-1]):
            part = part_in(defs[cur], st)
            if part is None:
                log("    CHECK: %s: no part %s inside %s -- not set" % (path, st, cur))
                break
            if i < len(steps) - 2:                    # a part inside it: in a copy of its definition
                d = (part.get("defn") or "").split(":", 1)
                if len(d) < 2 or d[0].upper() != scope.upper() or d[1] not in defs:
                    log("    CHECK: %s: %s is not one of the file's own definitions -- not set" % (path, st))
                    part = None
                    break
                cur = d[1] if d[1] in copies.values() else own(d[1])
                part.set("defn", "%s:%s" % (scope, cur))
        if part is None:
            continue
        q = next((x for x in part.iter("param") if (x.get("name") or "").upper() == steps[-1].upper()), None)
        if q is None:
            log("    CHECK: %s: %s has no setting %s -- not set" % (path, steps[-2], steps[-1]))
            continue
        log("    %s = %s (was %s)" % (path, value, q.get("value")))
        q.set("value", str(value))
    return defs, top2


def import_definition(pscad, prj, case, src, name, log, edits=None, tag=None):
    """The OEM's definition `name` -- and the definitions of its own it uses, those first -- added to this case's
       definitions from the OEM's file -> "case:name", or None. Its project's resources (.lib, .obj ...) come
       with it. edits: settings of parts inside it, in a copy of it of the generator's own, <name>_<tag>."""
    key = (os.path.normcase(os.path.abspath(src)), name.upper(), tag if edits else None)
    if key in IMPORTED:
        return IMPORTED[key]
    IMPORTED[key] = None
    try:
        scope, defs = oem_definitions(src)
    except Exception as e:
        log("CHECK: %s cannot be read (%s) -- the UDM's definition is not added" % (src, e))
        return None
    found = dict((n.upper(), n) for n in defs).get(name.upper())
    if found is None:
        log("CHECK: %s has no definition %s -- it has %s" % (os.path.basename(src), name, ", ".join(sorted(defs))))
        return None
    if edits:
        log("definition %s for %s: a copy of its own, %s_%s, with %d setting(s) of parts inside it changed:" % (
            found, tag, found, tag, len(edits)))
        defs, found = edit_parts(defs, scope, found, edits, tag, log)
    order, seen = [], set()

    def visit(n):
        if n in seen or n not in defs:
            return
        seen.add(n)
        for el in defs[n].iter():
            d = el.get("defn") or ""
            if d.upper().startswith(scope.upper() + ":"):
                visit(d.split(":", 1)[1])
        order.append(n)
    visit(found)
    pat = re.compile(r'((?:defn|name)=")%s:' % re.escape(scope), re.I)
    for n in order:
        if (key[0], n) in ADDED:                      # one another UDM from this file uses too: there already
            continue
        text = pat.sub(r"\g<1>%s:" % case, ET.tostring(defs[n], encoding="unicode"))
        try:
            prj.create_definition(text)
            ADDED.add((key[0], n))
            log("definition %s from %s added to this case%s" % (
                n, os.path.basename(src), "" if n == found else " (%s uses it)" % found))
        except Exception as e:
            log("CHECK: definition %s from %s not added: %s" % (n, os.path.basename(src), e))
            if n == found:
                return None
    try:                                              # its project's resources: this case gets them too
        if key[0] not in RESOURCES:
            RESOURCES.add(key[0])
            for r in pscad.project(scope).resources():
                try:
                    prj.create_resource(r.abspath)
                    log("resource from %s added to this case: %s" % (scope, r.abspath))
                except Exception as e:
                    log("CHECK: resource %s not added (%s) -- add it to this case by hand" % (r.abspath, e))
    except Exception:
        pass
    IMPORTED[key] = "%s:%s" % (case, found)
    DEFN_XML[IMPORTED[key]] = defs[found]
    return IMPORTED[key]


def _iid(cmp):
    try:
        return int(cmp.iid)
    except Exception:
        return id(cmp)


def _defn_of(cmp):
    try:
        d = cmp.defn_name
        return "%s:%s" % tuple(d) if isinstance(d, (tuple, list)) else str(d)
    except Exception:
        return "?"


def copy_of(pscad, canvas, prj, c, libs, log):
    """A component set up in another case (a vendor's test case, by its project and component ID): the first
       copied there and pasted here with its definition, with that case's resources (.lib, .obj ...) added to this
       one; the rest cloned from the first, its settings and all -> (component, its definition) or (None, None)."""
    src, iid = c["copy_of"]["source"], int(c["copy_of"]["iid"])
    key = (os.path.normcase(src), iid)
    first = COPIES.get(key)
    try:
        if first is not None:
            cmp = first.clone(c["x"], c["y"])
        else:
            name = (libs.get(os.path.normcase(os.path.abspath(src))) if os.path.splitext(src)[1] else None) or \
                os.path.splitext(os.path.basename(src))[0]
            sprj = pscad.project(name)
            orig = sprj.component(iid)
            if orig is None:
                raise ValueError("no component with ID %d in %s" % (iid, name))
            log("    %s: component %d is a %s -- copied here with its definition and settings" % (
                name, iid, _defn_of(orig)))
            here = set(_iid(x) for x in canvas.components())
            orig.copy()
            canvas.paste_transfer(c["x"] * 18, c["y"] * 18)
            new = [x for x in canvas.components() if _iid(x) not in here]
            if not new:
                raise ValueError("nothing was pasted")
            cmp = new[0]
            try:
                x, y = [int(v) for v in cmp.location]
                _move(cmp, c["x"] - x, c["y"] - y)
            except Exception:
                pass
            COPIES[key] = cmp
            if key[0] not in RESOURCES:
                RESOURCES.add(key[0])
                try:
                    res = [r.abspath for r in sprj.resources()]
                except Exception:
                    res = []
                for path in res:
                    try:
                        prj.create_resource(path)
                        log("    resource from %s added to this case: %s" % (name, path))
                    except Exception as e:
                        log("    CHECK: resource %s not added (%s) -- add it to this case by hand" % (path, e))
    except Exception as e:
        log("    CHECK: %s ID %d not copied: %s" % (src, iid, e))
        return None, None
    _made(cmp, c["x"], c["y"])
    return cmp, _defn_of(cmp)


def _delete(canvas, cmp):
    try:
        cmp.delete()
    except Exception:
        try:
            canvas.delete(cmp)
        except Exception:
            return False
    return True


def rlc_single(canvas, log):
    """Whether PSCAD's R, L and C are single-phase here (ports of dimension 1), from one resistor placed and
       removed; True (the roomy layout, right either way) when that cannot be told."""
    try:
        r = canvas.create_component("master:resistor", 2, 2)
    except Exception as e:
        log("(no resistor to look at: %s)" % e)
        return True
    dims = [p[3] for p in eports(r)]
    if not _delete(canvas, r):
        log("CHECK: a resistor placed to see its ports is left at (2, 2): delete it")
    return not dims or all(d == 1 for d in dims)


def wire(canvas, *pts):
    """An orthogonal wire through pts (the same point twice in a row is dropped)."""
    v = []
    for p in pts:
        p = (int(p[0]), int(p[1]))
        if not v or v[-1] != p:
            v.append(p)
    if len(v) > 1:
        canvas.create_wire(*v)
        _trace("wire", pts=v)
        REACH[0], REACH[1] = max([REACH[0]] + [p[0] for p in v]), max([REACH[1]] + [p[1] for p in v])


def ground(canvas, x, y, log):
    """A ground on the port at (x, y): below it, joined by a wire where its own port is not right there."""
    try:
        g = _made(canvas.create_component("master:ground", x, y + 2), x, y + 2)
        gp = eports(g)
        if gp and (gp[0][1], gp[0][2]) != (x, y):
            wire(canvas, (x, y), (x, gp[0][2]), (gp[0][1], gp[0][2]))
        _trace("comp", role="ground", ports=gp)
    except Exception as e:
        log("CHECK: no ground at (%d, %d): %s" % (x, y, e))


def label(canvas, x, y, name):
    """The node's name (a node label) on the port at (x, y)."""
    try:
        lb = _made(canvas.create_component("master:nodelabel", x, y, Name=name), x, y)
        if os.environ.get("SMIB_PSCAD_TRACE"):
            _trace("comp", role="label", name=name, ports=eports(lb))
        return True
    except Exception:
        return False


def breakout_for(canvas, targets, right, names, log):
    """A breakout whose three single-phase ports face `targets` (the phases' ports, top to bottom) -- from their
       left (right=True: the phases are right of it) or from their right -- named top to bottom as `names` when
       given; its top single port level with the top target, 5 grid units off. Every orientation is tried.
       -> (component, its three-phase port, its single ports top to bottom) or None."""
    tx, ty = targets[0][1], targets[0][2]
    for orient in sorted(range(8), key=lambda o: o != BREAKOUT_ORIENT.get(right)):
        fixed = False
        try:
            b = canvas.create_component("master:breakout", tx - 8 if right else tx + 8, ty, orient)
        except TypeError:                            # this mhi.pscad places it one way only
            b = canvas.create_component("master:breakout", tx - 8 if right else tx + 8, ty)
            fixed = True
        except Exception as e:
            NO_BREAKOUT.append(str(e))
            return None
        ps = eports(b)
        tri = [p for p in ps if p[3] == 3]
        one = sorted([p for p in ps if p[3] == 1], key=lambda p: p[2])
        ok = (len(tri) == 1 and len(one) == 3 and len(set(p[1] for p in one)) == 1 and
              len(set(p[2] for p in one)) == 3 and all(one[k][2] - one[0][2] <= DY * k for k in (1, 2)) and
              ((tri[0][1] < one[0][1]) if right else (tri[0][1] > one[0][1])) and
              (names is None or [p[0] for p in one] == names))
        if ok and not _move(b, (tx - 5 if right else tx + 5) - one[0][1], ty - one[0][2]):
            _delete(canvas, b)
            log("CHECK: a breakout cannot be moved with this mhi.pscad")
            return None
        if ok:
            BREAKOUT_ORIENT[right] = orient
            ps = eports(b)
            _made(b, tx, ty + 2 * DY)
            return b, [p for p in ps if p[3] == 3][0], sorted([p for p in ps if p[3] == 1], key=lambda p: p[2])
        _delete(canvas, b)
        if fixed:
            break
    log("CHECK: no orientation of the breakout faces the phases")
    return None


def phase_group(canvas, g, made, spec_of, points, log):
    """Group g -- a run of R / L / C in series, or one to ground -- drawn three times, phases top to bottom DY
       apart, a breakout at each three-phase end, joined by wires that never touch another phase's: each phase
       turns at its own column. The breakouts go first: when one cannot be placed nothing is added.
       -> the number of components added, or None (the group left as one phase)."""
    if NO_BREAKOUT:
        return None
    ends = []
    for tag in (g["members"][0][0], g["members"][-1][0]):
        ps = [p for p in eports(made[tag][0]) if p[3] == 1]
        if len(ps) != 2:
            log("CHECK: %s: %d single-phase ports" % (tag, len(ps)))
            return None
        ends.append(sorted(ps, key=lambda p: (p[1], p[2])))
    lf, rl = ends[0][0], ends[1][1]                  # the run's left end, its right end (phase a)
    got = breakout_for(canvas, [(lf[0], lf[1], lf[2] + DY * k, 1) for k in range(3)], True, None, log)
    if not got:
        return None
    bi, tri, one = got
    if g["kind"] == "series":
        got = breakout_for(canvas, [(rl[0], rl[1], rl[2] + DY * k, 1) for k in range(3)], False,
                           [p[0] for p in one], log)
        if not got:
            _delete(canvas, bi)
            return None
        _bo, tro, oneo = got
    rows, added = [], 2 if g["kind"] == "series" else 1
    for k in range(3):
        row = []
        for tag, _nin, _nout in g["members"]:
            cmp, used, _picked = made[tag]
            if k:
                c = spec_of[tag]
                cmp = _made(canvas.create_component(used, c["x"], c["y"] + DY * k), c["x"], c["y"] + DY * k)
                set_params(cmp, c["params"], log, quiet=True, names=NAMES.get(used))
                added += 1
            ports = sorted([p for p in eports(cmp) if p[3] == 1], key=lambda p: (p[1], p[2]))
            _trace("comp", role="phase", tag=tag, phase=k, ports=ports)
            row.append((tag, ports[0], ports[-1]))
        rows.append(row)
    for k in range(3):                               # along the run, phase by phase
        for i in range(len(rows[k]) - 1):
            a, b = rows[k][i][2], rows[k][i + 1][1]
            wire(canvas, (a[1], a[2]), (b[1], a[2]), (b[1], b[2]))
    ins, sx = [rows[k][0][1] for k in range(3)], one[0][1]
    for k in range(3):                               # fanning out: the bottom phase turns first
        wire(canvas, (sx, one[k][2]), (sx + 3 - k, one[k][2]), (sx + 3 - k, ins[k][2]), (ins[k][1], ins[k][2]))
    points.setdefault(g["a"], []).append((tri[1], tri[2], "breakout", tri[0],
                                          spec_of[g["members"][0][0]].get("parts", {}).get(g["a"], 0), tri[1] + 1))
    _trace("comp", role="breakout", group=g["members"][0][0], side="in", ports=[tri] + one)
    if g["kind"] == "series":
        outs, sx = [rows[k][-1][2] for k in range(3)], oneo[0][1]
        for k in range(3):                           # closing in: the top phase turns first
            wire(canvas, (outs[k][1], outs[k][2]), (sx - 3 + k, outs[k][2]), (sx - 3 + k, oneo[k][2]),
                 (sx, oneo[k][2]))
        points.setdefault(g["b"], []).append((tro[1], tro[2], "breakout", tro[0],
                                              spec_of[g["members"][-1][0]].get("parts", {}).get(g["b"], 0),
                                              tro[1] - 1))
        _trace("comp", role="breakout", group=g["members"][0][0], side="out", ports=[tro] + oneo)
    else:
        for k in range(3):
            r = rows[k][-1][2]
            points.setdefault("GND", []).append((r[1], r[2], g["members"][0][0], r[0]))
    return added


TRUNK_OFF = 2             # a trunk this far right of the part whose right end it leaves: clear of its symbol


def plan(points):
    """Each node's wires: from its leftmost port along its row to what carries on, and down a trunk to what hangs
       below -- the trunk TRUNK_OFF right of a part's right end, clear of its symbol -> {node: [(a, b) ...]}."""
    out = {}
    for node in sorted(points):
        pts = sorted(set((p[0], p[1]) for p in points[node]))
        if node == "GND" or len(pts) < 2:
            continue
        ax, ay = pts[0]
        off = [q for q in pts[1:] if q[1] != ay]
        row = [q for q in pts[1:] if q[1] == ay]
        tx = ax                                       # the trunk
        if off and all(q[0] > ax + TRUNK_OFF for q in pts[1:]) and \
                any(len(p) > 5 and p[5] < ax and (p[0], p[1]) == (ax, ay) for p in points[node]):
            tx = ax + TRUNK_OFF
            row = sorted(row + [(tx, ay)])
        segs, prev = [], (ax, ay)
        for q in row:
            segs.append((prev, q))
            prev = q
        if off:
            ys = sorted(set([ay] + [q[1] for q in off]))
            for y1, y2 in zip(ys, ys[1:]):
                segs.append(((tx, y1), (tx, y2)))
            for y in sorted(set(q[1] for q in off)):
                prev = (tx, y)
                for q in sorted(q for q in off if q[1] == y):
                    if q != prev:
                        segs.append((prev, q))
                    prev = q
        out[node] = segs
    return out


def _inside(p, a, b):
    return ((a[0] == b[0] == p[0] and min(a[1], b[1]) < p[1] < max(a[1], b[1])) or
            (a[1] == b[1] == p[1] and min(a[0], b[0]) < p[0] < max(a[0], b[0])))


def spot(node, wires, points):
    """Where the node's name goes: the middle of its longest wire (along a row before a trunk), clear of the
       symbols at its ends and of every other node's wires -> (wire index, point) or None (its first port)."""
    best = None
    for k, (a, b) in enumerate(wires[node]):
        n = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if n < 4:
            continue
        m = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        if any(_inside(m, c, d) or m in (c, d) for other, ws in wires.items() if other != node for c, d in ws):
            continue
        if any((p[0], p[1]) == m for other, ps in points.items() if other != node for p in ps):
            continue
        key = (a[1] == b[1] and n >= max(8, len(node) * 0.42 + 2), n)     # a row wire the name fits on, or the longest
        if best is None or key > best[0]:
            best = (key, k, m)
    return best[1:] if best else None


def join(canvas, points, log):
    """Wires: each node from its leftmost port, along its row to what carries on, and down a trunk to what hangs
       below; a ground on every ground port; the node's name in the middle of its longest wire."""
    for (x, y) in sorted(set((p[0], p[1]) for p in points.get("GND", []))):
        ground(canvas, x, y, log)
    split = {}                                        # a node in columns of its own: each part wired on its own,
    for node, pts in points.items():                  # joined to the rest by its name
        for p in pts:
            split.setdefault(node if node == "GND" or len(p) < 5 or not p[4] else "%s@%d" % (node, p[4]),
                             []).append(p)
    for key, pts in split.items():
        if "@" in key and len(set((p[0], p[1]) for p in pts)) == 1:     # one port: a wire of its own for the name
            n = key.split("@")[0]
            pts.append((pts[0][0] - max(4, int(len(n) * 0.42 + 2.5)), pts[0][1], "name", "", 0))
    points = split
    wires = plan(points)
    split_up = set(n.split("@")[0] for n in points if "@" in n)    # nodes in columns joined by their labels

    def name_it(node, x, y):
        if not label(canvas, x, y, node.split("@")[0]):
            log("CHECK: node %s: its node label could not be placed at (%d, %d)%s" % (
                node.split("@")[0], x, y, " -- its parts in other columns are not joined: join them by hand"
                if node.split("@")[0] in split_up else ""))
    for node in sorted(points):
        if node == "GND":
            continue
        if node not in wires:
            log("CHECK: node %s has one connection only" % node)
            continue
        at = spot(node, wires, points)
        try:
            for k, (a, b) in enumerate(wires[node]):
                if at and k == at[0]:
                    wire(canvas, a, at[1])
                    wire(canvas, at[1], b)
                else:
                    wire(canvas, a, b)
        except Exception as e:
            log("CHECK: node %s: no wires (%s) -- node labels instead" % (node, e))
            for (x, y) in sorted(set((p[0], p[1]) for p in points[node])):
                name_it(node, x, y)
            continue
        if not node.endswith("_RL"):
            x, y = at[1] if at else wires[node][0][0]
            name_it(node, x, y)


def main():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    log = Log(os.path.join(HERE, "%s_build_pscad_%s.txt" % (SPEC["case"], stamp)))
    log("SMIB %s -> PSCAD   (spec written %s)" % (SPEC["case"], SPEC["written"]))
    try:
        import mhi.pscad
    except ImportError:
        log("*** no mhi.pscad in %s -- run this with the Python PSCAD's automation library is installed in "
            "(py -m pip install mhi.pscad)" % sys.executable)
        return 2
    log("mhi.pscad %s" % getattr(mhi.pscad, "VERSION", getattr(mhi.pscad, "__version__", "?")))
    if LAUNCH_NEW or not hasattr(mhi.pscad, "application"):
        kw = {"version": PSCAD_VERSION} if PSCAD_VERSION else {}
        pscad = mhi.pscad.launch(**kw)
    else:
        pscad = mhi.pscad.application()
    if pscad is None:
        log("*** PSCAD could not be started")
        return 2
    libs = {}
    for lib in SPEC["libraries"]:
        if not os.path.isfile(lib):
            log("CHECK: library not found: %s" % lib)
            continue
        try:
            pscad.load(lib)
            libs[os.path.normcase(os.path.abspath(lib))] = os.path.splitext(os.path.basename(lib))[0]
            log("library loaded: %s" % lib)
        except Exception as e:
            log("CHECK: library %s not loaded: %s" % (lib, e))
    pis = [c for c in SPEC["components"] if c["kind"] == "PI"]
    if pis:
        found = find_def(pscad, ["Nominal PI", "Coupled PI"], log)
        if found:
            log("Coupled PI Section: %s" % found)
            for c in pis:
                c["defn"] = [found] + [d for d in c["defn"] if d != found]
        else:
            log("CHECK: no Coupled PI Section found in the master library (its form asks 'Nominal PI or Coupled PI "
                "Model') -- trying %s; or set Plant: PSCAD lines as RLC" % " / ".join(pis[0]["defn"]))
    names = set()
    for fn in ("project_names", "list_projects"):
        try:
            got = getattr(pscad, fn)()
            names |= set(str(x.get("name", x)) if isinstance(x, dict) else str(x) for x in got)
            break
        except Exception:
            continue
    case = _free(SPEC["case"], ".pscx")
    k = 1
    while case in names:
        k += 1
        case = _free("%s_%d" % (SPEC["case"][:27], k), ".pscx")
    prj = pscad.create_case(case + ".pscx", HERE)
    log("case: %s" % os.path.join(HERE, case + ".pscx"))
    try:
        prj.parameters(**SPEC["settings"])
        log("run settings: duration %(time_duration)s s, time step %(time_step)s us, plot step %(sample_step)s us"
            % SPEC["settings"])
    except Exception as e:
        log("CHECK: run settings not set (%s): %s" % (e, SPEC["settings"]))
    canvas = prj.canvas("Main") if hasattr(prj, "canvas") else prj.user_canvas("Main")
    if not rlc_single(canvas, log):
        for c in SPEC["components"]:
            c["x"], c["y"], c["parts"] = c.get("x3", c["x"]), c.get("y3", c["y"]), c.get("parts3", {})
        log("R, L and C take the wire's three phases here: drawn once each, closer together")
    page(canvas, max(c["x"] for c in SPEC["components"]) + 12, max(c["y"] for c in SPEC["components"]) + 16, log)
    for c in SPEC["components"]:                      # an OEM's UDM: its definition added to this case
        f = c.get("defn_from")
        if f:
            got = import_definition(pscad, prj, case, f["source"], f["definition"], log, f.get("inner"), f.get("tag"))
            if got:
                c["defn"] = [got] + [d for d in c["defn"] if d != got]
    seen_defn = {}
    made = {}                                         # tag -> (component, definition, [(node, port)])
    spec_of = dict((c["tag"], c) for c in SPEC["components"])
    for c in SPEC["components"]:
        log("")
        log("%s  (%s)  %s" % (c["tag"], c["kind"], c.get("note", "")))
        defns = list(c["defn"])
        if c.get("library") and defns and ":" not in defns[0]:
            lib = libs.get(os.path.normcase(os.path.abspath(c["library"])))
            if lib:
                defns.insert(0, "%s:%s" % (lib, defns[0]))
        cmp, used = None, None
        if c.get("copy_of"):
            cmp, used = copy_of(pscad, canvas, prj, c, libs, log)
        for d in (defns if cmp is None else []):
            try:
                try:
                    cmp = canvas.create_component(d, c["x"], c["y"], c.get("orient", 0))
                except TypeError:
                    cmp = canvas.create_component(d, c["x"], c["y"])
                    if c.get("orient"):
                        log("    CHECK: it could not be mirrored here -- its terminal is on the far side")
                used = d
                if cmp is not None:
                    _made(cmp, c["x"], c["y"])
                    break
            except Exception as e:
                log("    (%s: %s)" % (d, e))
        if cmp is None:
            log("    CHECK: not created (%s) -- place it by hand and join it to %s"
                % (" / ".join(defns + (["a copy of %s ID %s" % (c["copy_of"]["source"], c["copy_of"]["iid"])]
                                       if c.get("copy_of") else [])) or "nothing to make it from",
                   ", ".join(p["node"] for p in c["ports"])))
            continue
        log("    %s at (%d, %d)" % (used, c["x"], c["y"]))
        first = used not in seen_defn
        if first:
            try:
                have = cmp.parameters() or {}
                seen_defn[used] = sorted(have)
                log("    its parameters: %s" % ", ".join("%s=%s" % (k, have[k]) for k in sorted(have)))
            except Exception:
                seen_defn[used] = None
            try:
                log("    its bounds %s, location %s" % (tuple(cmp.bounds), tuple(cmp.location)))
            except Exception:
                pass
        NAMES[used] = seen_defn[used]
        if c["kind"] in ("PI", "METER"):              # its parameters found by their descriptions in its form
            if used not in FORMS:
                FORMS[used] = form_of(definition(pscad, used))
                if not FORMS[used]:
                    log("    (its form cannot be read here: its parameters go by name)")
            c["params"] = form_pick(c["params"], FORMS[used], log if first else (lambda *a: None))
        set_params(cmp, c["params"], log, names=seen_defn[used], read_back=first or READ_BACK)
        if len(made) % 10 == 9:
            print("  ... %d of %d components placed (%.0f s)" % (len(made) + 1, len(SPEC["components"]),
                                                              time.time() - T0))
            sys.stdout.flush()
        ports = eports(cmp)
        if c["kind"] == "PI":                         # where its symbol has them
            ports = pi_ports(cmp, c, ports, log, first)
        elif c["kind"] == "VENDOR":                   # its AC port facing its GSU, at the end of its lead
            ports = udm_fit(pscad, canvas, cmp, c, used, ports, log, first)
        elif c["kind"] == "METER":
            ports = two_ends(cmp, c, ports)
        elif c["kind"] == "SCALER":
            ports = on_row(cmp, c, two_ends(cmp, c, ports), log, first)
        if any(p["node"] for p in c["ports"]) and len(ports) > len(c["ports"]) and "View" in str(seen_defn[used]):
            log("    (%d electrical ports: %s)" % (len(ports), ", ".join(p[0] for p in ports)))
        taken, picked = set(), []
        nodes = [p["node"] for p in c["ports"]]
        if c["kind"] in ("R", "L", "C", "PI") and len(ports) == 2 and len(nodes) == 2:
            first = c.get("in") if c.get("in") in nodes else [n for n in nodes if n != "GND"][0]
            lr = sorted(ports, key=lambda p: (p[1], p[2]))          # its upstream node on its left port
            picked = [(first, lr[0]), (([n for n in nodes if n != first] or nodes)[0], lr[1])]
        else:
            for p in c["ports"]:
                got = pick(ports, p["pick"], taken)
                if got is None and p.get("optional"):
                    continue
                if got is None:
                    log("    CHECK: no port for node %s (ports: %s) -- join it by hand"
                        % (p["node"], ", ".join("%s@(%d,%d)" % (q[0], q[1], q[2]) for q in ports) or "none"))
                    continue
                taken.add(got[0])
                picked.append((p["node"], got))
        made[c["tag"]] = (cmp, used, picked)
        _trace("comp", role="spec", tag=c["tag"], ckind=c["kind"], ports=[q for _n, q in picked],
               nodes=[n for n, _q in picked])
        for node, got in picked:
            log("    port %-6s (%d, %d) dim %d -> %s" % (got[0], got[1], got[2], got[3], node))
    t_placed = time.time()
    single = any(c["kind"] in ("R", "L", "C") and made[c["tag"]][2] and
                 all(q[3] == 1 for _n, q in made[c["tag"]][2]) for c in SPEC["components"] if c["tag"] in made)
    points, grouped, extra = {}, set(), 0
    if single:
        log("")
        log("R, L and C are single-phase in this PSCAD: each three-phase one is drawn three times, phases a b c "
            "top to bottom, between breakouts")
        for g in SPEC.get("groups", []):
            tags = [t for t, _a, _b in g["members"]]
            if not all(t in made for t in tags):
                continue
            n = phase_group(canvas, g, made, spec_of, points, log)
            if n is None:
                if not NO_BREAKOUT:
                    log("CHECK: %s drawn as one phase only -- draw phases b and c by hand" % " + ".join(tags))
                continue
            extra += n
            grouped.update(tags)
        if NO_BREAKOUT:
            log("CHECK: no breakout here (master:breakout: %s) -- every R, L and C is drawn as one phase: add phases "
                "b and c, with breakouts, by hand" % NO_BREAKOUT[0])
    for c in SPEC["components"]:
        if c["tag"] in made and c["tag"] not in grouped:
            for node, q in made[c["tag"]][2]:
                points.setdefault(node, []).append((q[1], q[2], c["tag"], q[0], c.get("parts", {}).get(node, 0),
                                                    c["x"]))
    for c in SPEC["components"]:
        if c.get("drives") and c["tag"] in made and c["drives"] in made:
            link(canvas, made[c["tag"]][0], made[c["drives"]][0], "%s -> %s" % (c["tag"], c["drives"]), log)
    log("")
    t_phases = time.time()
    join(canvas, points, log)
    t_wired = time.time()
    made = len(made)
    log("nodes joined by wires: %d" % len([n for n in points if n != "GND"]))
    if PI_NOTE:
        log("PI Sections: %d whose ports PSCAD reports away from where it draws them -- wired to the drawn ends; %s"
            % (len(PI_NOTE), PI_NOTE[0]))
    if PORT_NOTE:
        log("vendor models: %d whose AC port PSCAD reports away from where their symbol draws it -- wired to the "
            "drawn port; %s" % (len(PORT_NOTE), PORT_NOTE[sorted(PORT_NOTE)[0]]))
    if MIRRORED:
        log("vendor models mirrored to face their GSU (their symbol has the AC port on its right): %d" % len(MIRRORED))
    if METER_NOTE:
        log("meters, current scalers: %d whose second end PSCAD does not report -- taken opposite the first, "
            "about the symbol's middle; %s" % (len(METER_NOTE), METER_NOTE[0]))
    w, h = reach(canvas)
    if PAGE[0] and (w > PAGE[0] or h > PAGE[1]) and (w > NEED[0] or h > NEED[1]):    # past what the layout said
        page(canvas, w, h, log)
    log("")
    log("inverters -- a vendor model (UDM) goes on these nodes; a stand-in source holds the load flow until then:")
    for s in SPEC.get("slots", []):
        log("    %-24s node %-24s %5s units  P %9.3f MW  Q %8.3f MVAr  V %.5f pu at %9.4f deg  %s"
            % (s["tag"], s["node"], s["units"], s["p"], s["q"], s["vm"], s["ang"], s["model"]))
    prj.save()
    log("time: components %.0f s, phases b and c %.0f s, wires and names %.0f s, saving %.0f s" % (
        t_placed - T0, t_phases - t_placed, t_wired - t_phases, time.time() - t_wired))
    if os.environ.get("SMIB_PSCAD_TRACE"):
        with open(os.environ["SMIB_PSCAD_TRACE"], "w") as fh:
            json.dump(TRACE, fh)
    log("")
    log("saved %s: %d of %d components%s, %d CHECK line(s) above"
        % (os.path.join(HERE, case + ".pscx"), made, len(SPEC["components"]),
           (" (+ %d for phases b and c, breakouts)" % extra) if extra else "", log.checks))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 1
    sys.exit(rc)
'''


def write_pscad(net, inp, out, log):
    comps = pscad_model(net, inp)
    spec = pscad_spec(net, inp, comps)
    optional = ("base kV", "magnitude (pu)", "name", "view", "no-load losses (pu)", "base frequency", "dimension")
    for cm in spec["components"]:
        for it in cm["params"]:                     # a missing optional one is not listed as a CHECK
            it["need"] = cm["kind"] == "VENDOR" or not (cm["kind"] == "METER" or it["what"] in optional or
                                                         it["what"].startswith("source impedance"))
    path = os.path.join(out, net.stem + "_build_pscad.py")
    text = (_BUILD_PSCAD.replace("@NAME@", net.name).replace("@DATE@", spec["written"])
            .replace("@FILE@", os.path.basename(path)).replace("@SPEC@", json.dumps(spec, indent=1)))
    _write_text(path, text)
    return comps, spec, path


def pscad_slot_lines(net, ind):
    """The inverters' slots (where a UDM goes) and the detailed collectors against their equivalents."""
    L = [ind + "Inverters -- a vendor model (UDM) goes on each node (Generators: PSCAD library / definition / AC "
               "port); until then a stand-in source holds the load flow there:",
         ind + "  %-24s %-24s %6s %10s %10s %9s %10s  %s" % ("component", "node", "units", "P MW", "Q MVAr", "V pu",
                                                              "angle", "model")]
    for sl in net.pscad_slots:
        L.append(ind + "  %-24s %-24s %6s %10.3f %10.3f %9.5f %10.4f  %s%s" % (
            sl["tag"], _node(sl.get("inv", sl["node"])), _g(sl["units"]), sl["p"], sl["q"], sl["vm"], sl["ang"],
            sl["model"], "  (GSU inside: on its HV side)" if sl["at"] == "MV" else ""))
    if any(sl.get("one") for sl in net.pscad_slots):
        L.append(ind + "  A vendor model is one unit: PSCAD's current scaler beside it (master:current_scaler, "
                       "<name>_NX, its scale the units above) carries them all, as in vendors' test cases.")
    elif net.model == "SMIB":
        L.append(ind + "  A vendor model (Generators: PSCAD definition) is one unit: it gets PSCAD's current scaler "
                       "beside it, its scale the units above, as in vendors' test cases.")
    else:
        L.append(ind + "  DETAILED: no current scaler -- a vendor model stands for its slot's units by itself "
                       "(PSCAD_Params {UNITS}, where it has a setting for them).")
    flt = next((c for c in getattr(net, "pscad_comps", []) if c["kind"] == "FAULT"), None)
    if flt:
        L.append(ind + "POI_FAULT: %s (its timing: POI_FAULT_TIMER)" % flt["note"])
    return L


def pscad_data_text(net, comps, spec):
    L = ["PSCAD DATA -- %s %s   (written by smib_build.py on %s)" % (net.model, net.name, spec["written"]),
         "=" * 100,
         "Every value the build script sets, in PSCAD's units. Transformers: pu on their own MVA and winding",
         "voltages (V1 is the tapped HV voltage). %s" % (
             "The gen-tie, collectors and feeder sections: one Coupled PI Section each, R, X, B in pu on" if
             net.ctl["pscad"]["lines"] == "PI" else "Lines, collectors and feeder sections: pi sections, R and L"),
         ("100 MVA and the line's kV, per metre (zero sequence = positive: no coupling between phases)." if
          net.ctl["pscad"]["lines"] == "PI" else
          "in series, the charging as C/2 at each end (one capacitor per node)."),
         "The drawing: a single-line diagram with wires (R, L, C three times over, between breakouts, where PSCAD's",
         "are single-phase), on a page sized to fit it; the node names below are the labels on the wires.",
         "Sources: kV line-to-line rms and degrees, the angle relative to the infinite source, the PSCAD",
         "transformers' phase shifts included (PSS/E has none). A node joins the components that name it.",
         "",
         "Run settings: duration %(time_duration)s s, time step %(time_step)s us, plot step %(sample_step)s us"
         % spec["settings"],
         "Libraries: %s" % (", ".join(spec["libraries"]) or "(none)"), ""]
    L += pscad_slot_lines(net, "")
    L.append("")
    for cm in comps:
        L.append("%-16s %-7s %s" % (cm["tag"], cm["kind"], " / ".join(cm["defn"])))
        if cm.get("note"):
            L.append("    %s" % cm["note"])
        L.append("    nodes: %s" % ", ".join(p["node"] for p in cm["ports"]))
        for it in cm["params"]:
            v = it["value"] if it["value"] is not None else "/".join(it["choice"] or [])
            L.append("    %-28s %-12s %s" % (it["what"], "/".join(it["names"]), v))
    return "\n".join(L)


# ============================================================================
# THE REPORT
# ============================================================================
def _c(s):
    return "%9.3f MW %9.3f MVAr" % (s.real, s.imag)


def report_text(net, inp, recs, comps, files, src):
    sb = net.sb
    th, op, c = net.thev, net.op, net.ctl
    shift = phase_shifts(net)
    L = ["%s MODEL  %s" % (net.model, net.name),
         "written %s by smib_build.py from %s" % (time.strftime("%Y-%m-%d %H:%M"), src), "=" * 100, ""]
    L.append("1. GRID, POI AND OPERATING POINT")
    L.append("   POI %.4g kV, bus %d '%s'" % (net.kv("POI"), net.bus["POI"]["num"], net.bus["POI"]["name"]))
    L.append("   grid: %s -- %.1f MVA short circuit, SCR %s on %.1f MW, X/R %s" % (
        th["how"], th["sc"], ("%.3f" % th["scr"]) if th["scr"] else "-", th["rated"], _g(th["x_r"], 5)))
    L.append("         R %.6f  X %.6f pu on %.0f MVA  =  R %.4f  X %.4f ohm at %.4g kV"
             % (th["r"], th["x"], sb, th["R"], th["X"], net.kv("POI")))
    mode = {"Q": "Q at the POI %.3f MVAr" % c["q_poi"], "PF": "power factor at the POI %.4f" % c["pf_poi"],
            "V": "the grid (infinite bus) at %.4f pu" % c["v_grid"]}[c["mode"]]
    held = any(g["q_mode"] == "share" for g in net.gens)
    L.append("   control: mode %s -- %s, %s; P: %s" % (
        c["mode"], ("the POI held at %.4f pu" % c["v_poi"]) if held else "no generator holds the POI (all fixed Q)",
        mode, ("%.3f MW at the POI" % c["p_poi"]) if c["p_poi"] is not None else "each generator at its dispatch"))
    pcx = net.plant_ctrl
    if pcx:
        L.append("   plant controller (.dyr): REPC_B -- %s at the POI (bus %d) on %.3f MVA commands %s through a "
                 "REAX4BU1 each" % (pcx["model"], net.bus["POI"]["num"], pcx["mbase"], ", ".join(pcx["gens"])))
    elif c.get("plant_ctrl") == "REPC_B":
        L.append("   plant controller (.dyr): REPC_B, but no generator for it (Q mode share) -- none written")
    elif held:
        L.append("   plant controller (.dyr): REPC_A -- a %s on each generator that holds the POI" % (
            "REPCAU1" if c.get("regc_repc") == "USRMDL" else "REPCA1"))
    else:
        L.append("   plant controller (.dyr): none -- no generator holds the POI")
    s = op["s_grid"]
    L.append("   solved: P at POI %.3f MW, Q at POI %.3f MVAr (into the grid), POI %.6f pu at %.4f deg"
             % (s.real, s.imag, net.bus["POI"]["vm"], net.bus["POI"]["va"]))
    L.append("           infinite bus %.6f pu at 0 deg%s" % (op["e"], (", dispatch scale %.5f" % op["alpha"])
                                                             if "P" in op["targets"] else ""))
    loss = op["loss"]
    L.append("           plant losses %.3f MW, %.3f MVAr (gen-tie to terminals, charging included)"
             % (loss.real, loss.imag))
    if op.get("check_dv") is not None:
        L.append("           check -- the whole network from the infinite bus: POI off its target by %.2e pu"
                 % op["check_dv"])
    L += ["", "2. BUSES", "   %7s  %-12s %9s  %-14s %10s %10s %11s" % ("number", "name", "kV", "role", "V pu",
                                                                       "angle", "PSCAD angle")]
    for role, b in net.bus.items():
        L.append("   %7d  %-12s %9.4f  %-14s %10.6f %10.4f %11.4f" % (b["num"], b["name"], b["kv"], role, b["vm"],
                                                                     b["va"], b["va"] + shift.get(role, 0.0)))
    L += ["", "3. EQUIPMENT (pu on the system base %.0f MVA unless said)" % sb]
    rates = lambda x: ("Rate A/B/C %s MVA" % "/".join("%.6g" % v for v in x)) if any(x) else "no rating"
    for ln in net.lines:
        L.append("   %-12s %-9s %s - %s  %d circuit(s)%s: R %.5f X %.5f ohm B %.3f uS each = r %.6f x %.6f b %.6f pu, "
                 "%s" % (ln["name"], ln["kind"], ln["fr"], ln["to"], ln["count"],
                         (", %.3f km" % ln["km"]) if ln.get("km") else "", ln["R"], ln["X"], ln["B"], ln["r"],
                         ln["x"], ln["b"], rates(_rabc(ln))))
    for t in net.tx2:
        L.append("   %-12s %-9s %s - %s  %s%.2f MVA %.4g/%.4g kV tap %.4f %s: Z %.4f%% X/R %s (own base) -> r %.6f "
                 "x %.6f pu on %.2f MVA" % (t["name"], t["kind"], t["hv"], t["lv"],
                                            ("%d x " % t["count"]) if t["count"] > 1 else
                                            ("%d units x %.3f = " % (t["units"], t["unit_mva"]) if t.get("units")
                                             else ""), t["mva"], t["kv1"], t["kv2"], t["tap"], t["vg"]["text"],
                                            100.0 * math.hypot(t["r_own"], t["x_own"]),
                                            _g(t["x_own"] / t["r_own"], 4) if t["r_own"] else "inf", t["r"], t["x"],
                                            t["mva"]) + ", " + rates(_rabc(t)))
    for t in net.tx3:
        L.append("   %-12s MPT(3W)  %s - %s - %s  %.4g/%.4g/%.4g kV tap %.4f %s  (HV, LV, TV: %s)" % (
            t["name"], t["hv"], t["lv"], t["tv"], t["kv1"], t["kv2"], t["kv3"], t["tap"], t["vg"]["text"],
            "; ".join(rates(x) for x in _rabc3(t))))
        for p, lab in (("12", "HV-LV"), ("31", "HV-TV"), ("23", "LV-TV")):
            r, x, base = t["own"][p][3], t["own"][p][4], t["own"][p][2]
            L.append("        %s Z %.4f%% on %.2f MVA (r %.6f x %.6f)" % (lab, 100.0 * math.hypot(r, x), base, r, x))
    for ld in net.loads:
        L.append("   load  %-10s at %-10s %.3f MW %.3f MVAr at 1 pu (constant impedance)" % (ld["name"], ld["bus"],
                                                                                         ld["mw"], ld["mvar"]))
    for sh in net.shunts:
        L.append("   shunt %-10s at %-10s %.3f MVAr at 1 pu" % (sh["name"], sh["bus"], sh["mvar"]))
    L += ["", "4. WECC COLLECTOR EQUIVALENTS  (Zeq = sum(Zi Wi^2) / W^2, Beq = sum(Bi); Wi = MW downstream)"]
    if net.model == "DETAILED":
        secs = [ln for ln in net.lines if ln["kind"] == "SECTION"]
        L.append("   MODEL TYPE DETAILED: every feeder section (identical feeder copies lumped) -- %d sections, %d "
                 "machines, each the units at one node behind its own GSU; the equivalents below are for reference"
                 % (len(secs), len(net.gens)))
    else:
        L.append("   MODEL TYPE SMIB: each generator one machine behind one GSU on its own equivalent feeder")
        for m in net.merged:
            L.append("   feeder %s (%s -> %s) = %s, weighed by MW: R %.5f X %.5f ohm, B %.3f uS = r %.7f x %.7f "
                     "b %.7f pu" % (m["name"], m["fr"], m["to"], " + ".join("%s %.1f MW" % (n, wmw) for n, wmw in
                                                                             m["of"]), m["R"], m["X"], m["B"],
                                    m["r"], m["x"], m["b"]))
    if not net.colls:
        L.append("   (none from feeders)")
    for cname, res in net.colls.items():
        zb = res["kv"] ** 2 / sb
        L.append("   %s: %.4g kV, %d feeder(s), %s, %.3f MW" % (
            cname, res["kv"], sum(fd["copies"] for fd in res["feeders"]),
            ", ".join("%d %s units" % (n, g) for g, n in res["N"].items()), res["W"]))
        for fd in res["feeders"]:
            L.append("     feeder %s%s: %d section%s, %.3f km, %s, %.3f MW; its own equivalent R %.5f X %.5f ohm "
                     "B %.3f uS" % (fd["name"], (" x %d" % fd["copies"]) if fd["copies"] > 1 else "",
                                    len(fd["sections"]), "s" if len(fd["sections"]) != 1 else "", fd["km"],
                                    ", ".join("%d %s" % (n, g) for g, n in
                                                                             fd["N"].items()), fd["W"],
                                    fd["z_own"].real * zb, fd["z_own"].imag * zb, fd["b_own"] / zb * 1e6))
            L.append("       %4s %4s  %-10s %8s %4s %9s %9s %10s %6s %9s" % ("sec", "from", "cable", "length", "",
                                                                         "R ohm", "X ohm", "B uS", "units",
                                                                         "MW down"))
            many = len(res["N"]) > 1
            for sct in fd["sections"]:
                L.append("       %4d %4d  %-10s %8.3f %-4s %9.5f %9.5f %10.3f %6s %9.3f" % (
                    sct["num"], sct["from"], sct["cable"] + ("*" if sct.get("given") else ""), sct["length"],
                    sct["unit"], sct["R"], sct["X"], sct["B"],
                    "+".join(("%d %s" % (n, g)) if many else str(n) for g, n in sct["units"].items()) or "0",
                    sct["W"]))
        L.append("     EQUIVALENT: R %.5f X %.5f ohm, B %.3f uS  =  r %.7f x %.7f b %.7f pu (%.0f MVA, %.4g kV)"
                 % (res["R"], res["X"], res["B"], res["r"], res["x"], res["b"], sb, res["kv"]))
        ck = res["check"]
        if ck is None or not (ck["detailed"]["ok"] and ck["equivalent"]["ok"]):
            L.append("     WECC check: the feeders' load flow (every unit at full output) does not solve")
            net.notes.append("CHECK: collector %s: its feeders' load flow does not solve at full output -- check the "
                             "Feeders lengths and their unit (km, mi, ft, m) and the cables' R and X" % cname)
            continue
        d, e = ck["detailed"], ck["equivalent"]
        dp = (e["p"] - d["p"]) / d["p"] * 100.0 if d["p"] else 0.0
        L.append("     WECC check, every unit at full output, collector bus 1.0 pu: losses detailed %.4f MW %.4f "
                 "MVAr, equivalent %.4f MW %.4f MVAr (P %+.2f %%)" % (d["p"], d["q"], e["p"], e["q"], dp))
        L.append("       detailed feeders %.5f .. %.5f pu; the equivalent's far end %.5f pu%s"
                 % (d["vmin"], d["vmax"], e["v"], ""))
        if abs(dp) > 5.0:
            net.notes.append("CHECK: collector %s: the equivalent's P loss is %+.1f %% off the detailed feeders'"
                             % (cname, dp))
    L += ["", "5. GENERATORS"]
    for g in net.gens:
        u = g["unit"]
        b = net.bus[g["bus"]]
        L.append("   %s (%s, id '%s') at %d '%s' %.4g kV: %d units x %.3f MVA = MBASE %.3f MVA" % (
            g["name"], g["type"], g["id"], b["num"], b["name"], b["kv"], g["n"], u["mva"], g["mbase"]))
        L.append("       P %.3f .. %.3f MW, Q %.3f .. %.3f MVAr (one unit: %.3f .. %.3f MW, %.3f .. %.3f MVAr)" % (
            g["pmin"], g["pmax"], g["qmin"], g["qmax"], u["pmin"], u["pmax"], u["qmin"], u["qmax"]))
        L.append("       solved P %.3f MW, Q %.3f MVAr, V %.6f pu at %.4f deg (PSCAD %.4f deg)" % (
            g["p"], g["q"], b["vm"], b["va"], b["va"] + shift.get(g["bus"], 0.0)))
        if g["q_mode"] == "share":
            L.append("       Q share %.1f %% (of Qmax); PSS/E: regulates the POI (%d) at %.4f pu, RMPCT %.1f%s" % (
                100.0 * g["share"], net.bus["POI"]["num"], c["v_poi"], 100.0 * g["share"],
                ("; REAX4BU1: %.4f of the plant's Q, %.4f of its P" % _reax_share(g, recs)) if "REAX4BU1" in
                g.get("dyr", []) else ""))
        else:
            L.append("       fixed Q (QT = QB = %.3f MVAr)" % g["q"])
        L.append("       PSS/E ZSORCE X %s pu, WMOD %d; .dyr: %s; PSCAD: %s" % (
            _g(g["zx"]), g["wmod_used"], " + ".join(g.get("dyr", [])) or "your Dyr_Records only",
            ("vendor model %s" % g["pscad"]["definition"]) if g["pscad"]["definition"] else
            "a source at the solved terminal voltage (network check)"))
    L += ["", "6. BRANCH FLOWS (MW + MVAr leaving each end)"]
    for fl in op["flows"]:
        L.append("   %-12s %-9s %-10s %s | %-10s %s | loss %8.4f MW %8.4f MVAr%s" % (
            fl["name"], fl["kind"], fl["a"], _c(fl["s1"]), fl["b"], _c(fl["s2"]), (fl["s1"] + fl["s2"]).real,
            (fl["s1"] + fl["s2"]).imag, ("  %.1f %% of %.1f MVA" % (fl["load"], fl["rate"])) if fl["load"] is not None
            else ""))
    L += ["", "7. DYNAMIC MODELS (.dyr): every parameter with its value -- generic WECC values unless Dyr_Params "
              "set them"]
    for gname, model, vals in recs:
        nic = DYR_LIB[model][1]
        L.append("   %-6s %-8s ICON: %s%s" % (gname, model, " ".join("%s=%s" % (p, v) for p, v in vals[:nic]) or "-",
                                             ("  (.dyd: %s; .dyr: a %s per trip point)" % (
                                                 model.lower(), RELAYS[model][0])) if model in RELAYS else ""))
        cons = ["%s=%s" % (p, _g(float(v))) for p, v in vals[nic:]]
        for k in range(0, len(cons), 8):
            L.append("   %-6s %-8s %s %s" % ("", "", "CON:" if k == 0 else "    ", "  ".join(cons[k:k + 8])))
    if inp["dyr_records"]:
        L.append("   + %d record(s) of your own (Dyr_Records)" % len([r for r in inp["dyr_records"]
                                                                       if _txt(r.get("record"))]))
    if files.get("pscad_build"):
        L += ["", "8. PSCAD: %d components -- the values are in PSCAD\\%s; build the case with PSCAD\\%s" % (
            len(comps), os.path.basename(files["pscad_data"]), os.path.basename(files["pscad_build"]))]
        L += pscad_slot_lines(net, "   ")
    else:
        L += ["", "8. PSCAD: not built (Plant: Build PSCAD model NO)"]
    L += ["", "9. FILES" + "".join(" -- no %s (Plant: Build ... NO)" % w for k, w in (
        ("raw", ".raw"), ("dyr", ".dyr"), ("epc", ".epc"), ("dyd", ".dyd")) if not files.get(k))]
    for k in ("raw", "dyr", "epc", "dyd", "pscad_build", "pscad_data", "json", "xlsx"):
        if files.get(k):
            L.append("   %s" % files[k])
    L += ["", "10. REVIEW"]
    L.extend("   " + n for n in net.notes)
    if not net.notes:
        L.append("   nothing to review")
    return "\n".join(L)


# ============================================================================
# PSS/E -- the check: the .raw read back and solved, the .dyr run flat
# ============================================================================
psspy = None
_i, _f, _s = -100000000, -1.0e20, "1"
_PSSE_DLL_HANDLES = []


def _vkey(path):
    """A sort key that puts 35.10 after 35.9."""
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", os.path.basename(path.rstrip("\\/")))]


def _psse_install_roots():
    """PSS/E install folders -- PSSE_VERSION's only, when set -- the newest first (PSSE_ROOT always first)."""
    bases = [r"C:\Program Files\PTI", r"C:\Program Files (x86)\PTI"]
    ver = "%d" % PSSE_VERSION if PSSE_VERSION else "3"
    out = [os.environ.get("PSSE_ROOT", "")]
    for b in bases:
        for d in sorted(glob.glob(os.path.join(b, "PSSE%s*" % ver)), key=_vkey, reverse=True):
            out += sorted(glob.glob(os.path.join(d, "%s*.*" % ver)), key=_vkey, reverse=True) + [d]
    return [r for r in out if r and os.path.isdir(r)]


def _psspy_versions(root):
    """[(3, 11), (3, 9) ...]: the Pythons a PSS/E install has a psspy for (its PSSPYxy folders), the newest first."""
    out = []
    for d in glob.glob(os.path.join(root, "PSSPY*")):
        m = re.match(r"^PSSPY(\d)(\d+)$", os.path.basename(d), re.I)
        if m and os.path.isdir(d):
            out.append((int(m.group(1)), int(m.group(2))))
    return sorted(set(out), reverse=True)


def _add_path(d):
    if d and os.path.isdir(d):
        while d in sys.path:
            sys.path.remove(d)
        sys.path.insert(0, d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
        try:
            if hasattr(os, "add_dll_directory"):                       # Python 3.8+ (PSS/E 35)
                _PSSE_DLL_HANDLES.append(os.add_dll_directory(d))      # novermin
        except Exception:
            pass


PSSE_RUNNING = None                 # (major, minor) of the PSS/E psspy came from


def psse_start(log):
    """psspy of PSSE_VERSION for this Python (its PSSPYxy folder); False when there is none."""
    global psspy, _i, _f, _s, PSSE_RUNNING
    if psspy is not None:
        return True
    try:
        import psspy as _p
    except ImportError:
        prefer = "PSSPY%d%d" % sys.version_info[:2]
        roots = _psse_install_roots()
        for root in roots:
            if os.path.isdir(os.path.join(root, prefer)):
                _add_path(os.path.join(root, "PSSBIN"))
                _add_path(os.path.join(root, prefer))
                log("PSS/E: %s" % os.path.join(root, prefer))
                break
            pbin = os.path.join(root, "PSSBIN")
            if os.path.isfile(os.path.join(pbin, "psspy.pyd")) or os.path.isfile(os.path.join(pbin, "psspy.py")):
                _add_path(pbin)
                log("PSS/E: %s" % pbin)
                break
        for m in (["psse%d" % PSSE_VERSION] if PSSE_VERSION else ["psse36", "psse35", "psse34"]):
            try:
                __import__(m)
                break
            except Exception:
                pass
        try:
            import psspy as _p
        except Exception as e:
            have = sorted(set(v for r in roots for v in _psspy_versions(r)))
            log("no PSS/E %sfor Python %d.%d (%s)%s" % (
                ("%s " % PSSE_VERSION) if PSSE_VERSION else "", sys.version_info[0], sys.version_info[1], e,
                ("; it has psspy for Python %s" % ", ".join("%d.%d" % v for v in have)) if have else
                ("; no PSS/E %s found under C:\\Program Files\\PTI (PSSE_ROOT: its folder)" % (PSSE_VERSION or ""))))
            return False
    psspy = _p
    try:
        import redirect
        redirect.psse2py()
    except Exception:
        pass
    psspy.psseinit(2000)
    try:
        _i, _f, _s = psspy.getdefaultint(), psspy.getdefaultreal(), psspy.getdefaultchar()
    except Exception:
        pass
    try:
        v = psspy.psseversion()
        PSSE_RUNNING = (int(v[1]), int(v[2]))
        log("PSS/E %d.%d, Python %d.%d" % (PSSE_RUNNING + sys.version_info[:2]))
        if PSSE_VERSION and PSSE_RUNNING[0] != PSSE_VERSION:
            log("CHECK: this is PSS/E %d, PSSE_VERSION says %d" % (PSSE_RUNNING[0], PSSE_VERSION))
    except Exception:
        pass
    return True


def _probe_python(cmd):
    """(major, minor, bits, executable) of the Python that `cmd` runs, or None."""
    code = ("import sys, struct; print('%d %d %d %s' % (sys.version_info[0], sys.version_info[1], "
            "struct.calcsize('P') * 8, sys.executable))")
    try:
        got = subprocess.check_output(cmd + ["-c", code], stderr=subprocess.STDOUT)
    except Exception:
        return None
    parts = got.decode("utf-8", "replace").strip().splitlines()[-1].split(" ", 3)
    try:
        return int(parts[0]), int(parts[1]), int(parts[2]), parts[3]
    except (IndexError, ValueError):
        return None


def psse_python(log):
    """A Python the PSS/E of PSSE_VERSION has a psspy for: PSSE_PYTHON, else one found (the py launcher, then the
       usual PythonXY folders) -- 64-bit for PSS/E 35 on, 32-bit before. None: none."""
    wanted = []
    for root in _psse_install_roots():
        for v in _psspy_versions(root):
            if v not in wanted:
                wanted.append(v)
    if not wanted:
        return None
    bits = 64 if (PSSE_RUNNING or (PSSE_VERSION or 35, 0))[0] >= 35 else 32
    names = ", ".join("%d.%d" % v for v in wanted)
    if PSSE_PYTHON:
        got = _probe_python([PSSE_PYTHON])
        if got and got[:2] in wanted:
            return got[3]
        log("CHECK: PSSE_PYTHON %s is %s -- PSS/E %s has psspy for Python %s" % (
            PSSE_PYTHON, ("Python %d.%d" % got[:2]) if got else "no Python", PSSE_VERSION or "", names))
        return None
    here = os.environ.get("LOCALAPPDATA", "")
    tried = set()
    for v in wanted:
        tag = "%d%d" % v
        cands = [["py", "-%d.%d-%d" % (v[0], v[1], bits)]]
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.path.join(here, "Programs", "Python") if here else "", "C:\\"):
            for name in ("Python%s" % tag, "Python%s-%d" % (tag, bits)):
                exe = os.path.join(base, name, "python.exe") if base else ""
                if exe and os.path.isfile(exe) and exe.lower() not in tried:
                    tried.add(exe.lower())
                    cands.append([exe])
        for cmd in cands:
            got = _probe_python(cmd)
            if got and got[:2] == v and got[2] == bits:
                return got[3]
    log("no %d-bit Python %s here for PSS/E %s's psspy -- install one (python.org), or set PSSE_PYTHON in the "
        "script; the check is skipped, the models are written" % (bits, " / ".join(names.split(", ")),
                                                                   PSSE_VERSION or ""))
    return None


class FileLog(Log):
    """A log on disk line by line -- a PSS/E crash still leaves the step it died in."""
    def __init__(self, path):
        Log.__init__(self)
        self.fh = io.open(_new(path), "w", encoding="utf-8")

    def __call__(self, msg=""):
        Log.__call__(self, msg)
        self.fh.write(u"%s\n" % msg)
        self.fh.flush()
        try:
            os.fsync(self.fh.fileno())
        except Exception:
            pass


def _ie(rc):
    return rc[0] if isinstance(rc, (list, tuple)) else rc


def psse_check(net, recs, inp, files, out, here_only=False):
    """PSS/E's check of the models in `out`: in this Python when it has PSS/E's psspy (PSSE_VERSION), else -- not
       here_only -- in a Python that has one: this script again, with --psse-check <out>."""
    pre = Log(echo=False)
    path = os.path.join(out, net.stem + "_PSSE_CHECK.txt")
    if not psse_start(pre) and not here_only:
        py = psse_python(pre)
        if py:
            print("[psse] %s -- the check runs in %s" % (pre.lines[-1] if pre.lines else "", py))
            rc = subprocess.call([py, os.path.abspath(__file__), "--psse-check", out])
            log = Log(echo=False)
            try:
                with io.open(path, encoding="utf-8", errors="replace") as fh:
                    log.lines = fh.read().splitlines()
            except (IOError, OSError):
                log("*** the PSS/E check in %s left no %s (exit %s)" % (py, os.path.basename(path), rc))
            for k, ext in (("sav", ".sav"), ("raw_psse", "_psse%d.raw" % (PSSE_VERSION or 0)), ("snp", ".snp")):
                if os.path.isfile(os.path.join(out, net.stem + ext)):
                    files[k] = os.path.join(out, net.stem + ext)
            return log
    log = FileLog(path)
    log("PSS/E CHECK -- %s %s   %s" % (net.model, net.name, time.strftime("%Y-%m-%d %H:%M")))
    for ln in pre.lines:
        log(ln)
    if psspy is None:
        return log
    work = os.path.join(out, "_psse")
    os.makedirs(work)
    cwd = os.getcwd()
    os.chdir(work)
    try:
        psspy.progress_output(2, os.path.join(work, "progress.txt"), [0, 0])
        psspy.alert_output(2, os.path.join(work, "alerts.txt"), [0, 0])
    except Exception:
        pass
    try:
        _psse_steps(net, recs, inp, files, out, work, log)
    except Exception:
        log("*** the check stopped: %s" % traceback.format_exc())
    finally:
        for fn in ("progress_output", "alert_output"):
            try:
                getattr(psspy, fn)(1, "", [0, 0])
            except Exception:
                pass
        os.chdir(cwd)
        txt = ""
        for f in ("progress.txt", "alerts.txt"):
            try:
                with io.open(os.path.join(work, f), encoding="utf-8", errors="replace") as fh:
                    txt += fh.read()
            except Exception:
                pass
        if re.search(r"INITIAL CONDITIONS SUSPECT", txt, re.I):
            log("*** initial conditions SUSPECT -- see _psse\\progress.txt")
        elif re.search(r"INITIAL CONDITIONS CHECK O\.?K", txt, re.I):
            log("initial conditions check o.k.")
        bad = [x.strip() for x in txt.splitlines()
               if re.search(r"error|not found|no model|not modeled|invalid|ignored", x, re.I)]
        for ln in bad[:20]:
            log("    | " + ln[:150])
        if any(re.search(r"not found|no model|not modeled|unknown", x, re.I) for x in bad):
            log("CHECK: PSS/E did not find a model of the .dyr (the lines above) -- set ADDLIB_IDV or DLLS in the "
                "script to the user-model library that has it")
    return log


def _psse_steps(net, recs, inp, files, out, work, log):
    raw = files["raw"]
    ie = _ie(psspy.read(0, raw))
    if ie not in (0, None):
        ie = _ie(psspy.readrawversion(0, "33", raw))
    log("read %s: %s" % (os.path.basename(raw), "ok" if ie in (0, None) else "*** ierr %s" % ie))
    if ie not in (0, None):
        return
    for _n in range(3):
        psspy.fnsl(list(SOLVE_OPTS))
        if _ie(psspy.solved()) == 0:
            break
    ok = _ie(psspy.solved()) == 0
    log("solved: %s" % ("yes" if ok else "*** NO (solved() = %s)" % _ie(psspy.solved())))
    num = lambda r: net.bus[r]["num"]
    worst = []
    for role, b in net.bus.items():
        ie1, v = psspy.busdat(b["num"], "PU")
        ie2, a = psspy.busdat(b["num"], "ANGLED")
        if ie1 == 0:
            dv, da = abs(v - b["vm"]), abs(a - b["va"])
            if dv > TOL_V_PU or da > 0.05:
                worst.append("CHECK: bus %d %s V %.5f pu at %.3f deg, this script %.5f at %.3f"
                             % (b["num"], role, v, a, b["vm"], b["va"]))
            log("  bus %7d %-12s V %.6f pu (script %.6f)  angle %9.4f (script %9.4f)" % (b["num"], role, v, b["vm"],
                                                                                       a, b["va"]))
    for g in net.gens:
        ie1, p = psspy.macdat(num(g["bus"]), g["id"], "P")
        ie2, q = psspy.macdat(num(g["bus"]), g["id"], "Q")
        if ie1 == 0 and ie2 == 0:
            log("  %-8s P %9.3f MW (script %9.3f)  Q %9.3f MVAr (script %9.3f)" % (g["name"], p, g["p"], q, g["q"]))
            if abs(p - g["p"]) > TOL_MW or abs(q - g["q"]) > TOL_MVAR:
                worst.append("CHECK: %s P/Q %.3f / %.3f, this script %.3f / %.3f" % (g["name"], p, q, g["p"], g["q"]))
    try:
        ie1, s = psspy.brnflo(num("POI"), num("IB"), "1")
        if ie1 == 0:
            s = complex(s)
            log("  POI -> grid %s (script %s)" % (_c(s), _c(net.op["s_grid"])))
            if abs(s.real - net.op["s_grid"].real) > TOL_MW or abs(s.imag - net.op["s_grid"].imag) > TOL_MVAR:
                worst.append("CHECK: P/Q at the POI %s, this script %s" % (_c(s), _c(net.op["s_grid"])))
    except Exception:
        pass
    for w in worst:
        log(w)
    if not worst and ok:
        log("PSS/E's solution is this script's (within %.2f MW, %.2f MVAr, %.4f pu)" % (TOL_MW, TOL_MVAR, TOL_V_PU))
    sav = os.path.join(out, net.stem + ".sav")
    ie = _ie(psspy.save(_new(sav)))
    log("saved %s: %s" % (os.path.basename(sav), "ok" if ie in (0, None) else "*** ierr %s" % ie))
    if ie in (0, None):
        files["sav"] = sav
    ver = (PSSE_RUNNING or (PSSE_VERSION or 0, 0))[0]
    raw2 = os.path.join(out, "%s_psse%d.raw" % (net.stem, ver))
    try:                                         # the case in this PSS/E's own .raw format
        ie = _ie(psspy.rawd_2(0, 1, [1, 1, 1, 0, 0, 0, 0], 0, _new(raw2)))
        log("wrote %s (PSS/E %d's own .raw): %s" % (os.path.basename(raw2), ver, "ok" if ie in (0, None)
                                                    else "*** ierr %s" % ie))
        if ie in (0, None):
            files["raw_psse"] = raw2
    except Exception as e:
        log("CHECK: PSS/E's own .raw not written: %s" % e)
    if not ok:
        return
    if not files.get("dyr"):
        log("no .dyr built (Plant: Build PSS/E .dyr NO): the dynamic check is skipped")
        return
    usr = sorted(set(m for _g_, m, _v in recs if DYR_LIB[m][0]))
    if any(re.search(r"'?USR(MDL|BUS)'?", (_txt(r.get("record")) or "").upper()) for r in inp["dyr_records"]):
        usr.append("your own")
    if usr and not (ADDLIB_IDV or DLLS):
        log("user models in the .dyr: %s -- read from PSS/E %d's own model library (one it lacks: set ADDLIB_IDV "
            "or DLLS in the script to the library that has it)" % (", ".join(usr), ver))
    psspy.cong(0)
    for opt in (1, 2, 3):
        psspy.conl(0, 1, opt, [0, 0], [100.0, 0.0, 0.0, 100.0])
    psspy.ordr(0)
    psspy.fact()
    psspy.tysl(0)
    if ADDLIB_IDV:
        here = os.getcwd()
        try:
            os.chdir(os.path.dirname(os.path.abspath(ADDLIB_IDV)))
            log("add-library file %s: ierr %s" % (ADDLIB_IDV, _ie(psspy.runrspnsfile(ADDLIB_IDV))))
        finally:
            os.chdir(here)
    for dll in DLLS:
        log("DLL %s: ierr %s" % (dll, _ie(psspy.addmodellibrary(dll)) if os.path.isfile(dll) else "*** not found"))
    ie = _ie(psspy.dyre_new([1, 1, 1, 1], files["dyr"], os.path.join(work, "conec.flx"),
                            os.path.join(work, "conet.flx"), os.path.join(work, "compile.bat")))
    log("dyre_new: %s" % ("ok" if ie in (0, None) else "*** ierr %s" % ie))
    if ie not in (0, None):
        return
    snp = os.path.join(out, net.stem + ".snp")
    try:                                         # the dynamics snapshot, with the .sav: the case to study from
        ie = _ie(psspy.snap([-1, -1, -1, -1, -1], _new(snp)))
        log("saved %s: %s" % (os.path.basename(snp), "ok" if ie in (0, None) else "*** ierr %s" % ie))
        if ie in (0, None):
            files["snp"] = snp
    except Exception as e:
        log("CHECK: snapshot not saved: %s" % e)
    n, a, t, d, ff = DYN_PARAMS
    psspy.dynamics_solution_param_2([n, _i, _i, _i, _i, _i, _i, _i], [a, t, d, ff, _f, _f, _f, _f])
    for g in net.gens:
        for code, q in ((2, "P"), (3, "Q"), (4, "V")):
            psspy.machine_array_channel([-1, code, num(g["bus"])], g["id"], "%s %s" % (q, g["name"]))
    outf = os.path.join(work, net.stem + "_flat.out")
    ie = _ie(psspy.strt_2([0, 1], outf))
    log("strt_2: %s" % ("ok" if ie in (0, None) else "*** ierr %s" % ie))
    if ie not in (0, None):
        return
    ie = _ie(psspy.run(0, FLAT_RUN_S, 0, 1, 0))
    log("run to %.1f s: %s" % (FLAT_RUN_S, "ok" if ie in (0, None) else "*** ierr %s" % ie))
    try:
        import dyntools
        _sh, ids, data = dyntools.CHNF(outf).get_data()
    except Exception as e:
        log("flat run: the .out could not be read (%s)" % e)
        return
    worst = {"P": (0.0, "-"), "Q": (0.0, "-"), "V": (0.0, "-")}
    for k in sorted(x for x in data if x != "time"):
        y = data[k]
        lab = str(ids.get(k, ""))
        q = lab.split()[0] if lab.split() else ""
        if y and q in worst:
            dd = max(abs(v - y[0]) for v in y) * (net.sb if q in ("P", "Q") else 1.0)
            if dd > worst[q][0]:
                worst[q] = (dd, lab)
    lim = dict(zip("PQV", FLAT_TOL))
    flat = all(worst[q][0] <= lim[q] for q in "PQV")
    log("flat run: %s -- largest drift P %.3f MW (%s), Q %.3f MVAr (%s), V %.5f pu (%s)" % (
        "flat" if flat else "NOT FLAT", worst["P"][0], worst["P"][1], worst["Q"][0], worst["Q"][1], worst["V"][0],
        worst["V"][1]))



# ============================================================================
# MAIN
# ============================================================================
def _old_blanks(inp, fmt):
    """What blanks meant in the file the inputs come from (fmt: its FORMAT), written in -- what differs from now
       said: before 2 a feeder's length unit km; before 3 the gen-tie's and MPT ties' km, and a blank Unit Qmax
       from Unit PF (0.95) though Unit MVA is given (now from MVA and Pmax)."""
    said = inp.setdefault("_said", [])
    fu, tu = ("ft" if fmt >= 2 else "km"), ("ft" if fmt >= 3 else "km")
    n = 0
    for r in inp.get("feeders") or []:
        if _txt(r.get("length")) is not None and _txt(r.get("unit")) is None:
            r["unit"] = fu
            n += 1
    if n and fu != FEEDER_UNIT:
        said.append("NOTE: Feeders: %d row(s) with no Length unit read as %s -- a blank meant %s when this file was "
                    "saved (now %s); they say %s from now on" % (n, fu, fu, FEEDER_UNIT, fu))
    ties = []
    pl = inp.get("plant") or {}
    if _txt(pl.get("tie_length")) is not None and _txt(pl.get("tie_unit")) is None:
        pl["tie_unit"] = tu
        ties.append("Plant: Gen-tie length unit")
    for r in inp.get("mpt") or []:
        if _txt(r.get("tie_length")) is not None and _txt(r.get("tie_unit")) is None:
            r["tie_unit"] = tu
            ties.append("MPT %s: Tie length unit" % (_txt(r.get("name")) or "?"))
    if ties and tu != FEEDER_UNIT:
        said.append("NOTE: %s blank, read as %s -- a blank meant %s when this file was saved (now %s); it says %s "
                    "from now on" % (", ".join(ties), tu, tu, FEEDER_UNIT, tu))
    if fmt < 3:
        q = []
        for r in inp.get("generators") or []:
            if _txt(r.get("unit_mva")) is not None and _txt(r.get("unit_qmax_mvar")) is None and \
                    _txt(r.get("unit_pf")) is None:
                r["unit_pf"] = 0.95
                q.append(_txt(r.get("name")) or "?")
        if q:
            said.append("NOTE: Generators %s: Unit PF 0.95 written in -- a blank Unit Qmax came from Unit PF (blank "
                        "0.95) when this file was saved; now, with Unit MVA given, from MVA and Pmax" % ", ".join(q))
    if fmt < 4:
        loads, done = None, []
        for r in inp.get("mpt") or []:
            try:
                nw = _windings(r, "MPT")
            except InputError:
                continue
            if nw != 3 and not (nw is None and _txt(r.get("tv_kv")) is not None):
                continue
            keys = [k for k in ("lv_mva", "tv_mva") if _txt(r.get(k)) is None]
            if not keys:
                continue
            try:
                m = _num(r, "mva", "MPT", None, positive=True)
                if m is None:
                    loads = mpt_loads(inp) if loads is None else loads
                    on = loads.get((_txt(r.get("name")) or "").upper()) or {"lv": 0.0, "tv": 0.0}
                    m = (on["lv"] + on["tv"]) / _int(r, "count", "MPT", 1, minimum=1) or None
            except InputError:
                m = None
            if m:
                for k in keys:
                    r[k] = round(m, 6)
                done.append("%s (%s MVA)" % (_txt(r.get("name")) or "?", "%.6g" % m))
        if done:
            said.append("NOTE: MPT %s: LV / TV MVA blank, written in as the MPT's MVA -- what a blank meant when this "
                        "file was saved (now: the MVA shared out by the units on each winding)" % ", ".join(done))


def _format_of(inp):
    """The FORMAT a project (.json) was saved in: its mark (1 if none -- before the marks)."""
    try:
        return int(inp.get("_format") or (2 if _txt(inp.get("_feeder_unit")) == "ft" else 1))
    except (TypeError, ValueError):
        return 1


def read_input(path, keep_comments=False):
    """A project (.json) or workbook read -> the inputs. keep_comments (the GUI): the rows starting with # (set
       aside) kept as they are, so a save keeps them -- the build skips them."""
    if path.lower().endswith(".json"):
        try:
            with io.open(path, encoding="utf-8") as fh:
                inp = json.load(fh)
        except ValueError as e:                      # not JSON (or not UTF-8): said plainly
            raise InputError("%s cannot be read as a project (.json): %s -- open a project this tool saved, or "
                             "a workbook (.xlsx)" % (path, e))
        if not isinstance(inp, dict) or not isinstance(inp.get("plant", {}), dict):
            raise InputError("%s is no project of this tool (no 'plant' in it)" % path)
        unknown, tables = [], dict((t[1], t) for t in TABLES)
        for k in inp:                                # what this version does not know: said, not dropped unseen
            if k != "plant" and k not in tables and not k.startswith("_") and inp[k] not in (None, [], {}, ""):
                unknown.append("'%s' (no sheet of this name)" % k)
        known = set(f[0] for f in PLANT_FIELDS)
        unknown += ["Plant: '%s'" % k for k, v in (inp.get("plant") or {}).items()
                    if k not in known and not k.startswith("_") and _txt(v) is not None]
        for key in ["plant"] + [t[1] for t in TABLES]:
            inp.setdefault(key, {} if key == "plant" else [])
        for sheet, key, cols in TABLES:             # a row whose first value starts with # is skipped, as in Excel
            if not isinstance(inp[key], list) or not all(isinstance(r, dict) for r in inp[key]):
                raise InputError("%s: '%s' is not a list of rows" % (path, key))
            inp[key] = [r for r in inp[key] if keep_comments and any(_txt(v) for v in r.values()) or
                        not _skip([r.get(c[0]) for c in cols])]
            names = set(c[0] for c in cols)
            for col in sorted(set(c for r in inp[key] for c, v in r.items()
                                  if c not in names and not c.startswith("_") and _txt(v) is not None)):
                unknown.append("%s: column '%s'" % (sheet, col))
            for k, r in enumerate(inp[key]):
                r.setdefault("_where", "%s row %d" % (sheet, k + 1))
        inp["_source"], inp["_unknown"] = path, unknown
        _old_blanks(inp, _format_of(inp))            # no mark: saved when blanks meant what they did then
        return inp
    return parse_workbook(read_xlsx(path), path, keep_comments)


def example_roles():
    log = Log()
    log.lines = []
    inp = json.loads(json.dumps(EXAMPLE))
    for sheet, key, _cols in TABLES:
        for k, r in enumerate(inp.get(key, [])):
            r["_where"] = "%s row %d" % (sheet, k + 3)
    inp["plant"] = inp.get("plant", {})
    inp["_unknown"] = []
    inp["dyr_records"] = [r for r in inp.get("dyr_records", []) if not str(r.get("generator", "")).startswith("#")]
    inp["pscad_params"] = [r for r in inp.get("pscad_params", []) if not str(r.get("generator", "")).startswith("#")]
    net = build_network(inp, lambda *a: None)
    return [r.replace("C:", "") for r in net.bus]


def pscad_build(files, out, log):
    """<name>_build_pscad.py run right away when this Python has mhi.pscad: PSCAD 5 builds <name>.pscx."""
    script = files["pscad_build"]
    try:
        import importlib.util
        have = importlib.util.find_spec("mhi.pscad") is not None
    except Exception:
        have = False
    if not have:
        log("[pscad] no mhi.pscad in this Python -- install it (%s -m pip install mhi.pscad) and run this again, or "
            "run %s with a Python that has it: PSCAD 5 builds the .pscx" % (os.path.basename(sys.executable),
                                                                         os.path.basename(script)))
        return
    log("[pscad] PSCAD builds the case: %s" % os.path.basename(script))
    rc = subprocess.call([sys.executable, script], cwd=out)
    pscx = sorted(glob.glob(os.path.join(out, "*.pscx")), key=os.path.getmtime)
    logs = sorted(glob.glob(os.path.join(out, "*_build_pscad_*.txt")), key=os.path.getmtime)
    last = ""
    if logs:
        with io.open(logs[-1], encoding="utf-8", errors="replace") as fh:
            last = ([ln.strip() for ln in fh if ln.strip()] or [""])[-1]
    if pscx:
        files["pscx"] = pscx[-1]
        log("[pscad] %s" % (last or pscx[-1]))
    else:
        log("[pscad] *** no .pscx (exit %s)%s" % (rc, (" -- see %s" % os.path.basename(logs[-1])) if logs else ""))


MODEL_DIRS = {"DETAILED": "Detailed_Models", "SMIB": "Equivalent_Models"}     # in Models (OUT_DIR)
OUT_KINDS = (("raw", "PSS/E .raw"), ("dyr", "PSS/E .dyr"), ("pscad", "PSCAD model"), ("epc", "PSLF .epc"),
             ("dyd", "PSLF .dyd"))                              # what Plant: Build ... can say YES / NO to


def plant_cases(inp):
    """Plant fields given as several values (Grid SCR 3, 5, 10; X/R 10, 15 ...: SWEEP) -> a set of inputs for each
       value -- each combination where several fields have them -- named <name>_SCR3_XR10 ... -> [(what it is,
       inputs)]; none -> [(None, inp)]."""
    lists = []
    for key, tag in SWEEP:
        v = inp["plant"].get(key)
        parts = [x for x in re.split(r"[,;\s]+", v.strip()) if x] if isinstance(v, str) else []
        if len(parts) < 2:
            continue
        for x in parts:
            try:
                float(x)
            except ValueError:
                raise InputError("Plant: %s = %r -- one value, or several separated by commas: 3, 5, 10" % (
                    _label(key), v))
        lists.append((key, tag, parts))
    if not lists:
        return [(None, inp)]
    n = 1
    for _k, _t, parts in lists:
        n *= len(parts)
    if n > SWEEP_MAX:
        raise InputError("Plant: %s make %d sets of models -- %d at most at a time" % (
            " x ".join("%d %s" % (len(pp), _label(k).strip("'")) for k, _t, pp in lists), n, SWEEP_MAX))
    out, name = [], _txt(inp["plant"].get("name")) or "Plant"
    for combo in itertools.product(*[pp for _k, _t, pp in lists]):
        d = json.loads(json.dumps(inp))
        for (key, _t, _p), x in zip(lists, combo):
            d["plant"][key] = float(x)
        d["plant"]["name"] = "%s_%s" % (name, "_".join("%s%s" % (t, x.replace("-", "m").replace(".", "p"))
                                                       for (_k, t, _p), x in zip(lists, combo)))
        out.append((", ".join("%s %s" % (_label(k).strip("'"), x) for (k, _t, _p), x in zip(lists, combo)), d))
    return out


def build(src, out_dir=None, check=True, pscad=True):
    """Everything from the workbook `src` -- a set of models for each value where Plant fields give several
       (plant_cases); returns (net, files) of the last, files["cases"] the output folder of each."""
    log = Log()
    log("[smib] input  %s" % src)
    cases = plant_cases(read_input(src))
    if len(cases) > 1:
        log("[smib] %d sets of models: %s" % (len(cases), " | ".join(x for x, _i in cases)))
    done = [_build_case(inp, src, out_dir, check, pscad, log) for _x, inp in cases]
    net, files = done[-1]
    if len(done) > 1:
        files["cases"] = [f["dir"] for _n, f in done]
    return net, files


def _build_case(inp, src, out_dir, check, pscad, log):
    net = build_network(inp, log)
    gens = collections.OrderedDict((g.get("family", g["name"]), g["type"]) for g in net.gens)
    log("[smib] %s model: %d buses, %d generators (%s) as %d machines, %s"
        % (net.model, len(net.bus), len(gens), ", ".join("%s %s" % kv for kv in gens.items()), len(net.gens),
           ("%d feeder sections" % len([ln for ln in net.lines if ln["kind"] == "SECTION"])) if net.model == "DETAILED"
           else "%d collector feeder(s)%s" % (len([ln for ln in net.lines if ln["kind"] == "COLLECTOR"]),
                                              " (%d merged per generator)" % sum(len(m["of"]) for m in net.merged)
                                              if net.merged else "")))
    operating_point(net, log)
    s = net.op["s_grid"]
    log("[smib] operating point: P at POI %.3f MW, Q %.3f MVAr, POI %.5f pu, infinite bus %.5f pu"
        % (s.real, s.imag, net.bus["POI"]["vm"], net.op["e"]))
    base = os.path.join(out_dir or OUT_DIR or os.path.join(os.path.dirname(os.path.abspath(src)), "Models"),
                        MODEL_DIRS[net.model])
    out = stem = os.path.join(base, "%s_%s" % (net.stem, time.strftime("%Y%m%d_%H%M%S")))
    k = 2
    while os.path.exists(out):                   # a second run within the same second: a folder of its own
        out = "%s_%d" % (stem, k)
        k += 1
    os.makedirs(out)
    want = outputs(inp)
    psse_dir, pscad_dir = os.path.join(out, "PSSE"), os.path.join(out, "PSCAD")
    files = {"json": os.path.join(out, net.stem + "_inputs.json"), "dir": out}
    try:
        if want["raw"] or want["dyr"]:
            os.makedirs(psse_dir)
            files["psse_dir"] = psse_dir
        if want["raw"]:
            files["raw"] = os.path.join(psse_dir, net.stem + ".raw")
            write_raw(net, files["raw"])
        else:
            for g in net.gens:
                raw_gen(net, g, _num_of(net, "POI"))
        if want["dyr"]:
            files["dyr"] = os.path.join(psse_dir, net.stem + ".dyr")
            recs = write_dyr(net, inp, files["dyr"])
        else:
            recs = dyr_models(net, inp)
        if want["epc"] or want["dyd"]:
            files["pslf_dir"] = os.path.join(out, "PSLF")
            os.makedirs(files["pslf_dir"])
        if want["epc"]:
            files["epc"] = os.path.join(files["pslf_dir"], net.stem + ".epc")
            write_epc(net, files["epc"])
        if want["dyd"]:
            files["dyd"] = os.path.join(files["pslf_dir"], net.stem + ".dyd")
            write_dyd(net, recs, files["dyd"])
        if want["pscad"]:
            os.makedirs(pscad_dir)
            files["pscad_dir"] = pscad_dir
            comps, spec, files["pscad_build"] = write_pscad(net, inp, pscad_dir, log)
            files["pscad_data"] = os.path.join(pscad_dir, net.stem + "_PSCAD_DATA.txt")
            _write_text(files["pscad_data"], pscad_data_text(net, comps, spec))
        else:
            comps = []
        clean = dict((k, v) for k, v in inp.items() if not k.startswith("_"))
        clean["_feeder_unit"], clean["_format"] = FEEDER_UNIT, FORMAT
        _write_text(files["json"], json.dumps(clean, indent=1, sort_keys=True, default=str))
        if src.lower().endswith((".xlsx", ".xlsm")):
            files["xlsx"] = os.path.join(out, os.path.basename(src))
            shutil.copy2(src, _new(files["xlsx"]))
        files["report"] = os.path.join(out, net.stem + "_REPORT.txt")
        net.notes[:] = list(collections.OrderedDict.fromkeys(net.notes))
        _write_text(files["report"], report_text(net, inp, recs, comps, files, src))
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)   # this run's own new folder: no half-written models left
        raise
    log("[smib] written to %s" % out)
    for k in ("report", "raw", "dyr", "epc", "dyd", "pscad_build", "pscad_data"):
        if files.get(k):
            log("         %s" % os.path.relpath(files[k], out))
    skip = [w for k, w in OUT_KINDS if not want[k]]
    if skip:
        log("[smib] not built (Plant: Build ...): %s" % ", ".join(skip))
    for n in net.notes:
        log("[smib] " + n)
    if check and PSSE_CHECK and want["raw"]:
        log("")
        clog = psse_check(net, recs, inp, files, psse_dir)
        for ln in clog.review():
            log("[psse] " + ln)
        made = [os.path.basename(files[k]) for k in ("sav", "raw_psse", "snp") if files.get(k)]
        if made:
            log("[psse] PSS/E wrote %s" % ", ".join(made))
    if pscad and PSCAD_BUILD and want["pscad"]:
        log("")
        pscad_build(files, pscad_dir, log)
    return net, files


def outputs(inp):
    """What Plant says to build: {"raw", "dyr", "pscad", "epc", "dyd": True / False} (blank = YES)."""
    pl = inp["plant"]
    return dict((k, _choice(pl, "out_" + k, "Plant", ["YES", "NO"], "YES") == "YES") for k, _w in OUT_KINDS)


def psse_check_only(out):
    """--psse-check <folder>: PSS/E's check of the models in <folder>, rebuilt from its _inputs.json -- how the
       check runs in a Python that PSS/E has a psspy for, when the one that wrote the models has none."""
    found = glob.glob(os.path.join(out, "*_inputs.json")) or glob.glob(os.path.join(os.path.dirname(out),
                                                                                   "*_inputs.json"))
    if len(found) != 1:
        print("[psse] *** %s: %s _inputs.json" % (out, "more than one" if found else "no"))
        return 2
    inp = read_input(found[0])
    quiet = Log(echo=False)
    net = build_network(inp, quiet)
    operating_point(net, quiet)
    recs = dyr_models(net, inp)
    files = dict((k, os.path.join(out, net.stem + ext)) for k, ext in (("raw", ".raw"), ("dyr", ".dyr"))
                 if os.path.isfile(os.path.join(out, net.stem + ext)))
    psse_check(net, recs, inp, files, out, here_only=True)
    return 0 if psspy is not None else 3


def find_inputs(dirs):
    """The SMIB input workbooks in `dirs` (a Plant and a Generators sheet), the newest first."""
    out, seen = [], set()
    for d in dirs:
        for p in glob.glob(os.path.join(d, "*.xls[xm]")):
            key = os.path.normcase(os.path.abspath(p))
            if key in seen or os.path.basename(p).startswith("~$"):
                continue
            seen.add(key)
            try:
                with zipfile.ZipFile(p) as z:
                    wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
            except Exception:
                continue
            names = set(n.lower() for n in re.findall(r'<sheet\b[^>]*\bname="([^"]*)"', wb))
            if "plant" in names and "generators" in names:
                out.append(os.path.abspath(p))
    return sorted(out, key=os.path.getmtime, reverse=True)


def _ask_workbook(here, found):
    """A file dialog for the input workbook; None: no dialog here, or cancelled."""
    try:
        import tkinter
        import tkinter.filedialog
    except ImportError:
        return None
    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = tkinter.filedialog.askopenfilename(
            parent=root, initialdir=here, filetypes=[("Excel workbook", "*.xlsx *.xlsm"), ("All files", "*.*")],
            title="SMIB input workbook -- Cancel: %s" % ("list them" if found else "a new one to fill in"))
        root.destroy()
    except Exception:
        return None
    return os.path.abspath(path) if path else None


def pick_input():
    """No workbook given: the one beside this script (or in the working folder); several or none: a file dialog;
       none chosen and none there: a new SMIB_INPUT.xlsx beside the script to fill in. None: nothing to run."""
    here = os.path.dirname(os.path.abspath(__file__))
    found = find_inputs([here, os.getcwd()])
    if len(found) == 1:
        print("[smib] input: %s (the SMIB workbook beside the script)" % found[0])
        return found[0]
    print("[smib] %s -- choose one in the file dialog" % ("%d SMIB workbooks beside the script" % len(found) if found
                                                         else "no input workbook given"))
    path = _ask_workbook(here, found)
    if path:
        return path
    if found:
        print("[smib] give one:  python %s <workbook>   (or set INPUT_XLSX at the top of the script)"
              % os.path.basename(__file__))
        for p in found:
            print("         %s" % p)
        return None
    path = os.path.join(here, "SMIB_INPUT.xlsx")
    try:
        roles = example_roles()
    except Exception:
        roles = None
    write_template(path, EXAMPLE, roles)
    print("[smib] no input workbook -- a new one to fill in: %s" % path)
    print("       it holds an example (100 MW PV + 100 MW BESS) and a README sheet; fill it in, save it and run "
          "this again (or: python %s --help)" % os.path.basename(__file__))
    return None


# ============================================================================
# THE GUI -- every input on a tab of its own, like the workbook's sheets (no Excel needed)
# ============================================================================
GUI_TEXT_KEYS = set(["name", "id", "vector", "gsu_vector", "cable", "feeder", "collector", "connect", "at", "bus",
                     "record", "parameter", "generator", "model", "units_of", "poi_name", "pscad_library",
                     "pscad_definition", "pscad_ac_port", "kind", "type", "pscad_libraries"])
GUI_PROJECT = "SMIB_PROJECT.json"           # the project the GUI opens when it is beside the script


def gui_data(inp):
    """The GUI's copy of an input (a workbook read, a project, EXAMPLE): every value as text, blanks dropped."""
    out = {"plant": dict((k, _txt(v)) for k, v in (inp.get("plant") or {}).items()
                         if not k.startswith("_") and _txt(v) is not None)}
    for _sheet, key, cols in TABLES:
        rows = []
        for r in inp.get(key) or []:
            row = dict((c[0], _txt(r.get(c[0]))) for c in cols if _txt(r.get(c[0])) is not None)
            if row:
                rows.append(row)
        out[key] = rows
    return out


def gui_value(key, s):
    """A value typed in the GUI as the workbook holds it: a number where it reads as one (not names, ids ...)."""
    if s is None or str(s).strip() == "":
        return None
    s = str(s).strip()
    if key in GUI_TEXT_KEYS:
        return s
    try:
        x = float(s)
    except ValueError:
        return s
    return int(x) if x == int(x) and "." not in s and "e" not in s.lower() and abs(x) < 1e15 else x


def _split(total, weights):
    """total in whole parts in proportion to weights, the largest remainders first."""
    tw = float(sum(weights)) or 1.0
    raw = [total * w / tw for w in weights]
    out = [int(math.floor(x)) for x in raw]
    for i in sorted(range(len(raw)), key=lambda i: -(raw[i] - out[i]))[:total - sum(out)]:
        out[i] += 1
    return out


def scale_arg(text):
    """A Scale up value: blank or 0 -> None, "12" -> 12, "12, 8, 10" -> [12, 8, 10] (one per MPT)."""
    parts = [x for x in re.split(r"[,;\s]+", str(text or "").strip()) if x]
    try:
        vals = [int(float(x)) for x in parts]
    except ValueError:
        raise InputError("Scale up: %r -- a whole number, or one per MPT: 12, 8, 10" % text)
    if any(float(x) != int(float(x)) for x in parts):
        raise InputError("Scale up: %r -- whole numbers, please" % text)
    return None if not vals else vals[0] or None if len(vals) == 1 else vals


def scale_plant(data, mpts=None, per_mpt=None, gsus=None, per_gsu=None, tie="PER_MPT"):
    """A test plant scaled from this one -- None or 0 keeps what the plant has:
         mpts      MPTs: copies of the first, each with a copy of the generators on it (PV, BESS -> PV_2, BESS_2 ...)
         per_mpt   feeders on each MPT, shared among its generators by their MW
         gsus      GSUs on each feeder: a section of its own each, the feeder's cables in order (the last repeated)
         per_gsu   inverters (units) behind each GSU
       per_mpt, gsus, per_gsu: one number for every MPT, or a list -- one per MPT in turn (MPT1, MPT2 ...; the last
       one for the rest): feeders [12, 8, 10]. Each generator's Units, GSU count, GSU MVA (its MVA per inverter
       kept) and Dispatch follow; each MPT's MVA follows its own MW.
         tie       the gen-tie with more than one MPT: PER_MPT -- each MPT a tie of its own to the POI, as the
                   plant's gen-tie was; SHARED -- the plant's one gen-tie, all the MPTs on it, as it was
       -> (inputs, [what was done])."""
    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def upper(v):
        return str(v or "").upper()

    def listed(v, what):
        vs = list(v) if isinstance(v, (list, tuple)) else [v]
        for x in vs:
            if x is not None and x < 0:
                raise InputError("Scale up: %s must be 1 or more (0 or blank: as it is)" % what)
        return [x or None for x in vs] or [None]

    d = json.loads(json.dumps(data, default=str))
    if not d.get("mpt"):
        raise InputError("Scale up: the plant has no MPT")
    if mpts is not None and mpts < 0:
        raise InputError("Scale up: MPTs must be 1 or more (0 or blank: as it is)")
    per_mpt, gsus, per_gsu = (listed(per_mpt, "feeders per MPT"), listed(gsus, "GSUs per feeder"),
                              listed(per_gsu, "inverters per GSU"))
    longest = max(len(per_mpt), len(gsus), len(per_gsu))
    if mpts and longest > mpts:
        raise InputError("Scale up: %d MPT(s), but %d values given one per MPT -- as many as the MPTs or fewer (the "
                         "last one goes on for the rest)" % (mpts, longest))
    mpts = int(mpts or (longest if longest > 1 else len(d["mpt"])) or 1)
    base = d["mpt"][0]
    bname = str(base.get("name") or "MPT1")
    names = set(upper(r.get("name")) for k in ("mpt", "generators", "collectors") for r in d.get(k, []))
    m = re.match(r"^(.*?)(\d+)$", bname)

    def fresh(nm):
        while upper(nm) in names:
            nm += "X"
        names.add(upper(nm))
        return nm

    def on_base(where):                                   # MPT1, MPT1:LV, MPT1:TV
        return upper(where) == upper(bname) or upper(where).startswith(upper(bname) + ":")

    shared = [c for c in d.get("collectors", []) if on_base(c.get("connect"))]
    shared_n = set(upper(c.get("name")) for c in shared)
    gens = [g for g in d.get("generators", []) if on_base(g.get("connect")) or upper(g.get("connect")) in shared_n]
    if not gens:
        raise InputError("Scale up: no generator is connected to %s" % bname)
    feeders = d.get("feeders", [])
    own = [g for g in gens if upper(g.get("collector") or "FEEDERS") == "FEEDERS" and
           upper(g.get("connect")) not in shared_n and any(upper(r.get("collector")) == upper(g["name"])
                                                           for r in feeders)]

    def mw(g):
        return (num(g.get("units")) or 0.0) * (num(g.get("unit_pmax_mw")) or 0.0)

    plan = []                                              # (generator, its first feeder's sections)
    nf_now = 0
    for g in own:
        rows = [r for r in feeders if upper(r.get("collector")) == upper(g["name"])]
        first = rows[0].get("feeder")
        plan.append((g, sorted([r for r in rows if r.get("feeder") == first],
                               key=lambda r: num(r.get("section")) or 0)))
        fc = collections.OrderedDict()                     # its feeders now: each with its copies
        for r in rows:
            fc.setdefault(r.get("feeder"), num(r.get("copies")) or 1)
        nf_now += int(sum(fc.values()))
    old_mw = sum(mw(g) for g in gens)

    def one_mpt(nf_all, n_gsu_in, inv_in):
        """One MPT's generators: {GEN: (row, feeder rows)}, [what]."""
        if own and nf_all < len(own):
            raise InputError("Scale up: %d feeders on an MPT are fewer than the %d generators with feeders on %s"
                             % (nf_all, len(own), bname))
        nfs = _split(nf_all, [max(mw(g), 1e-6) for g in own]) if own else []
        for i in range(len(nfs)):                          # every generator at least one feeder
            if nfs[i] == 0:
                j = max(range(len(nfs)), key=lambda j: nfs[j])
                nfs[i], nfs[j] = 1, nfs[j] - 1
        out, did = {}, []
        for (g, tpl), nf in zip(plan, nfs):
            us = [num(r.get("units")) or 0 for r in tpl]
            k0 = [i for i, u in enumerate(us) if u] or [len(tpl) - 1]
            head = tpl[:k0[0]]                             # the home run: the sections before the first units
            unit_secs = [tpl[i] for i in k0]
            n_gsu = int(n_gsu_in or len(unit_secs))
            units = num(g.get("units")) or 1.0
            per = num(g.get("gsu_units")) if not num(g.get("gsu_count")) else None
            cnt = num(g.get("gsu_count")) or (math.ceil(units / per - 1e-9) if per else units)
            inv = int(inv_in or max(1, int(round(units / cnt))))
            nu = nf * n_gsu * inv
            gg = dict(g, units=nu, gsu_count=nf * n_gsu if g.get("gsu_mva") not in (None, "") else g.get("gsu_count"))
            if num(g.get("gsu_units")):
                gg["gsu_units"] = inv
            if g.get("gsu_mva") not in (None, ""):          # its GSU MVA per inverter kept (Units per GSU: by design)
                gg["gsu_mva"] = round(num(g["gsu_mva"]) * (1.0 / per if per else cnt / units) * inv, 4)
                for k in ("gsu_rate_a_mva", "gsu_rate_b_mva", "gsu_rate_c_mva"):   # in step with the GSU's MVA
                    if num(g.get(k)) is not None:
                        gg[k] = round(num(g[k]) * gg["gsu_mva"] / num(g["gsu_mva"]), 4)
            if g.get("dispatch_mw") not in (None, ""):
                gg["dispatch_mw"] = round(num(g["dispatch_mw"]) * nu / units, 4)
            rows = []
            for j in range(nf):
                secs = head + [unit_secs[min(i, len(unit_secs) - 1)] for i in range(n_gsu)]
                for k, r in enumerate(secs):
                    rows.append(dict(r, feeder="F%d" % (j + 1), copies=1, section=k + 1, from_section=None,
                                     units=inv if k >= len(head) else None))
            out[upper(g["name"])] = (gg, rows)
            did.append("%d feeders x %d GSUs x %d inverters = %d units (%.1f MW), GSU %s MVA" % (
                nf, n_gsu, inv, nu, mw(gg), _g(num(gg.get("gsu_mva")) or 0)))
        for g in gens:
            if upper(g["name"]) not in out:
                out[upper(g["name"])] = (dict(g), [])
        return out, did

    pick = lambda vs, k: vs[min(k, len(vs) - 1)]
    sets = [one_mpt(int(pick(per_mpt, k) or nf_now or 0), pick(gsus, k), pick(per_gsu, k)) for k in range(mpts)]
    out_mpt, out_gen, out_col, out_f, gname, did = [], [], [], [], {}, []
    total = 0.0
    for k in range(1, mpts + 1):
        new_gen, dk = sets[k - 1]
        mw_k = sum(mw(gg) for gg, _r in new_gen.values())
        total += mw_k
        ratio = mw_k / old_mw if old_mw else 1.0
        mn = bname if k == 1 else fresh("%s%d" % (m.group(1), int(m.group(2)) + k - 1) if m else "%s_%d" % (bname, k))
        r = dict(base, name=mn)
        for key in ("mva", "lv_mva", "tv_mva", "rate_mva", "rate_b_mva", "rate_c_mva", "base_hl_mva", "base_ht_mva",
                    "base_lt_mva"):
            if num(r.get(key)):
                r[key] = round(num(r[key]) * ratio, 1)
        out_mpt.append(r)

        def moved(where):
            w = str(where or "")
            return mn + w[len(bname):] if on_base(w) else w
        for c in shared:
            nm = c["name"] if k == 1 else fresh("%s_%d" % (c["name"], k))
            gname[(upper(c["name"]), k)] = nm
            out_col.append(dict(c, name=nm, connect=moved(c.get("connect"))))
            out_f.extend(dict(fr, collector=nm) for fr in feeders if upper(fr.get("collector")) == upper(c["name"]))
        for g in gens:
            gg, rows = new_gen[upper(g["name"])]
            nm = g["name"] if k == 1 else fresh("%s_%d" % (g["name"], k))
            gname[(upper(g["name"]), k)] = nm
            con = g.get("connect")
            con = gname.get((upper(con), k), con) if upper(con) in shared_n else moved(con)
            out_gen.append(dict(gg, name=nm, connect=con))
            out_f.extend(dict(fr, collector=nm) for fr in rows)
        did.append("%s (%s MVA, %.1f MW): %s" % (mn, _g(num(r.get("mva")) or 0), mw_k, "; ".join(
            "%s %s" % (gname[(upper(g["name"]), k)], x) for (g, _t), x in zip(plan, dk)) or "its shared collectors"))
    gens_id = set(id(g) for g in gens)
    d["mpt"] = out_mpt + [r for r in d["mpt"] if r is not base]
    d["generators"] = out_gen + [g for g in d.get("generators", []) if id(g) not in gens_id]
    d["collectors"] = out_col + [c for c in d.get("collectors", []) if all(c is not x for x in shared)]
    moved_f = set(upper(g["name"]) for g in own) | shared_n
    d["feeders"] = out_f + [r for r in feeders if upper(r.get("collector")) not in moved_f]
    for key in ("dyr_params", "dyr_records", "pscad_params"):
        rows = []
        for r in d.get(key, []):
            rows.append(r)
            g = upper(r.get("generator"))
            rows.extend(dict(r, generator=gname[(g, k)]) for k in range(2, mpts + 1) if (g, k) in gname)
        d[key] = rows
    nfs = [int(pick(per_mpt, k) or nf_now or 0) for k in range(mpts)]
    did.insert(0, "%d MPT(s), copies of %s (which had %.1f MW), feeders on each: %s -- %.1f MW in all" % (
        mpts, bname, old_mw, ", ".join(str(x) for x in nfs) if len(set(nfs)) > 1 else nfs[0], total))
    pl = d.setdefault("plant", {})
    gt = dict((k, v) for k, v in pl.items() if k.startswith("tie_") and v not in (None, ""))
    if upper(tie) not in ("PER_MPT", "SHARED"):
        raise InputError("Scale up: gen-tie %r -- PER_MPT or SHARED" % tie)
    per_len = num(gt.get("tie_length")) and not any(num(gt.get(k)) for k in ("tie_r_ohm", "tie_x_ohm", "tie_b_us"))
    if mpts > 1 and upper(tie) == "PER_MPT" and gt and not num(base.get("tie_length")):
        if not per_len:
            raise InputError("Scale up: a gen-tie per MPT needs the plant's gen-tie as a length with R, X, B per "
                             "length (Plant: Gen-tie ...); it has totals -- or choose one shared gen-tie")
        b = num(gt.get("tie_b_per"))
        if b is None and num(gt.get("tie_c_per")):        # uF -> uS
            b = num(gt["tie_c_per"]) * 2.0 * math.pi * (num(pl.get("freq_hz")) or 60.0)
        own_tie = {"tie_length": gt.get("tie_length"), "tie_unit": gt.get("tie_unit"),
                   "tie_per_unit": gt.get("tie_per_unit"), "tie_r_per": gt.get("tie_r_per"),
                   "tie_x_per": gt.get("tie_x_per"), "tie_b_per": b, "tie_rate_mva": gt.get("tie_rate_mva")}
        for r in out_mpt:                                  # a gen-tie of each MPT's own, POI to its HV
            r.update(own_tie)
        for k in list(pl):
            if k.startswith("tie_"):
                pl[k] = None
        did.append("gen-tie: one per MPT, POI to each MPT, each as the plant's was (%s %s%s)%s" % (
            _g(num(gt["tie_length"])), gt.get("tie_unit") or FEEDER_UNIT,
            (", %s MVA" % _g(num(gt["tie_rate_mva"]))) if num(gt.get("tie_rate_mva")) else "",
            (" -- %s circuits were one each" % _g(num(gt["tie_parallel"]))) if (num(gt.get("tie_parallel")) or 1) > 1
            else ""))
    elif gt:
        if mpts > 1:
            did.append("gen-tie: the plant's one, shared by the %d MPTs (as it was)" % mpts)
        if num(gt.get("tie_rate_mva")):
            cap = num(gt["tie_rate_mva"]) * (num(gt.get("tie_parallel")) or 1)
            if total > cap:
                did.append("the gen-tie (%s MVA) is below the plant's %.1f MW: Plant: Gen-tie circuits or rating"
                           % (_g(cap), total))
    if shared:
        did.append("shared collector(s) %s copied as they are" % ", ".join(c["name"] for c in shared))
    for key, what in (("machines", "Machines"), ("bus_numbers", "Bus_Numbers")):
        if d.get(key):
            did.append("%s cleared (its names changed)" % what)
            d[key] = []
    left = [g["name"] for g in d["generators"][len(out_gen):]]
    if left:
        did.append("as they were (not on %s): %s" % (bname, ", ".join(left)))
    return d, did


RENAMES = {  # a name changed in the GUI -> where it is used: (table, column, bus: NAME:MV ... too, machine: NAME_...)
    ("generators", "name"): (("feeders", "collector", 0), ("feeders", "units_of", 0), ("dyr_params", "generator", 0),
                             ("dyr_records", "generator", 0), ("pscad_params", "generator", 0),
                             ("machines", "machine", 2), ("generators", "connect", 1), ("collectors", "connect", 1),
                             ("loads_shunts", "at", 1), ("bus_numbers", "bus", 1)),
    ("mpt", "name"): (("generators", "connect", 1), ("collectors", "connect", 1), ("loads_shunts", "at", 1),
                      ("bus_numbers", "bus", 1)),
    ("cables", "cable"): (("feeders", "cable", 0),),
    ("collectors", "name"): (("feeders", "collector", 0), ("generators", "connect", 1), ("collectors", "connect", 1),
                             ("loads_shunts", "at", 1), ("bus_numbers", "bus", 1)),
}


def rename_refs(data, table, col, old, new):
    """A generator, MPT, cable or collector renamed in the GUI: every place its old name is used takes the new --
       its own buses (NAME:MV ...) and DETAILED machines (NAME_F1_S2 ...) too -> how many cells changed."""
    old, new = str(old or "").strip(), str(new or "").strip()
    if not old or not new or old == new:
        return 0
    n = 0
    for tb, c, how in RENAMES.get((table, col), ()):
        for r in data.get(tb, []):
            v = str(r.get(c) or "")
            if v.upper() == old.upper():
                r[c] = new
            elif how == 1 and v.upper().startswith(old.upper() + ":"):
                r[c] = new + v[len(old):]
            elif how == 2 and v.upper().startswith(old.upper() + "_"):
                r[c] = new + v[len(old):]
            else:
                continue
            n += 1
    return n


def gui_save_data(data):
    """The project as written to its .json (and read back by read_input): typed values, empty rows left out."""
    out = {"plant": dict((k, gui_value(k, v)) for k, v in data["plant"].items() if gui_value(k, v) is not None)}
    for _sheet, key, cols in TABLES:
        rows = []
        for r in data.get(key, []):
            row = dict((c[0], gui_value(c[0], r.get(c[0]))) for c in cols if gui_value(c[0], r.get(c[0])) is not None)
            if row:
                rows.append(row)
        out[key] = rows
    out["_feeder_unit"] = FEEDER_UNIT                # what blanks mean in it (FORMAT; _feeder_unit: format 2's mark)
    out["_format"] = FORMAT
    return out


def _write_json(path, obj):
    with io.open(path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, indent=1))


def _replace_file(path, write):
    """write(tmp) then put it in place of `path` -- a save the user asked for (the project, an export)."""
    tmp = path + ".saving"
    if os.path.exists(tmp):
        os.remove(tmp)
    write(tmp)
    os.replace(tmp, path)


class _Queue(object):
    """sys.stdout while a build runs on its thread: its lines go to the GUI's Output tab."""
    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(s)
        return len(s or "")

    def flush(self):
        pass


def gui(start=None):
    """The input GUI: the workbook's sheets as tabs, the project saved as .json, models built from it."""
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
    except ImportError:
        print("[smib] *** this Python has no tkinter (the GUI's library) -- use a python.org Python, or a workbook: "
              "python %s --template SMIB_INPUT.xlsx" % os.path.basename(__file__))
        return 2
    import threading
    import queue
    import contextlib

    here = os.path.dirname(os.path.abspath(__file__))
    root = tk.Tk()
    root.title("Plant Model Builder")
    root.geometry("1280x800")
    root.minsize(900, 560)
    st = ttk.Style(root)
    for theme in ("vista", "clam"):
        if theme in st.theme_names():
            st.theme_use(theme)
            break
    navy, light, input_bg, grey = "#1F3864", "#DDEBF7", "#FFF2CC", "#595959"
    st.configure("Head.TLabel", background=navy, foreground="white", font=("Segoe UI", 13, "bold"), padding=(10, 6))
    st.configure("Sub.TLabel", background=navy, foreground="#D9E1F2", font=("Segoe UI", 9, "italic"),
                 padding=(10, 0, 10, 6))
    st.configure("Section.TLabel", background=light, foreground=navy, font=("Segoe UI", 10, "bold"), padding=(6, 3))
    st.configure("Item.TLabel", font=("Segoe UI", 9, "bold"))
    st.configure("Hint.TLabel", foreground=grey, font=("Segoe UI", 8))
    st.configure("Req.TLabel", foreground="#C00000", font=("Segoe UI", 8, "bold"))
    st.configure("Total.TLabel", foreground="#1F3864", font=("Segoe UI", 9, "bold"))
    st.configure("Tag.TLabel", foreground="#375623", font=("Segoe UI", 8))
    st.configure("Help.TLabel", background="#F2F2F2", foreground="#262626", font=("Segoe UI", 9), padding=(8, 5))
    st.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
    st.configure("TNotebook.Tab", padding=(5, 2), font=("Segoe UI", 9))
    st.configure("Treeview", rowheight=22)
    st.configure("Status.TLabel", background="#F2F2F2", padding=(8, 3))
    st.configure("Nav.Treeview", rowheight=24, font=("Segoe UI", 9))
    st.configure("Input.TCombobox", fieldbackground=input_bg)
    st.map("Input.TCombobox", fieldbackground=[("readonly", input_bg)])

    app = {"path": None, "dirty": False, "running": False, "out": None, "grids": {}, "vars": {}, "q": queue.Queue()}
    data = {"plant": {}}

    # ---------------------------------------------------------------- header, toolbar, tabs, status
    ttk.Label(root, text="PLANT MODEL BUILDER  |  PSS/E, PSLF, TSAT and PSCAD from one set of inputs",
              style="Head.TLabel").pack(fill="x")
    ttk.Label(root, text="Pick a page on the left. A blank field takes its default; the line at the bottom explains "
                         "the field you are on. The simple view shows the fields most plants need -- tick All fields "
                         "for every one.",
              style="Sub.TLabel").pack(fill="x")
    bar = ttk.Frame(root, padding=(6, 4))
    bar.pack(fill="x")
    bar2 = ttk.Frame(root, padding=(6, 0, 6, 4))
    bar2.pack(fill="x")
    body = ttk.Frame(root)
    body.pack(fill="both", expand=True, padx=4, pady=(0, 2))
    nav = ttk.Treeview(body, show="tree", selectmode="browse", style="Nav.Treeview")
    nav.column("#0", width=210, stretch=False)
    nav.pack(side="left", fill="y", padx=(0, 4))
    st.layout("Grouped.TNotebook.Tab", [])               # the pages are picked on the left
    nb = ttk.Notebook(body, style="Grouped.TNotebook")
    nb.pack(side="left", fill="both", expand=True)
    status = tk.StringVar()
    ttk.Label(root, textvariable=status, style="Status.TLabel", anchor="w").pack(fill="x", side="bottom")

    def set_status(msg):
        status.set("%s%s   |   %s" % (app["path"] or "(project not saved yet)", "  *" if app["dirty"] else "",
                                     " ".join(str(msg).split())))

    def dirty(*_a):
        if not app["dirty"]:
            app["dirty"] = True
            set_status("changed")

    # ---------------------------------------------------------------- helpers
    def scrolled(parent):
        """A frame that scrolls (the Plant form)."""
        outer = ttk.Frame(parent)
        cv = tk.Canvas(outer, highlightthickness=0, background="white")
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        inner = tk.Frame(cv, background="white")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        def wheel(e):
            cv.yview_scroll(-1 if (getattr(e, "num", 0) == 4 or e.delta > 0) else 1, "units")
        for w in (cv, inner):
            w.bind("<Enter>", lambda e: (cv.bind_all("<MouseWheel>", wheel), cv.bind_all("<Button-4>", wheel),
                                         cv.bind_all("<Button-5>", wheel)))
            w.bind("<Leave>", lambda e: (cv.unbind_all("<MouseWheel>"), cv.unbind_all("<Button-4>"),
                                         cv.unbind_all("<Button-5>")))
        return outer, inner

    def help_text(sheet, key, label, note, choices, unit=""):
        bits = ["%s  (%s)" % (label, key)]
        if unit:
            bits.append("unit: %s" % unit)
        b = "required" if key == "poi_kv" else _blank_means(note)
        if b:
            bits.append("blank = %s" % b)
        if choices:
            bits.append("choices: %s" % " | ".join(choices))
        bits.append(_used_by(sheet, key))
        return "   ".join(bits) + ("\n" + note.lstrip("# ") if note else "")

    def bus_names():
        """The bus names the current inputs give (for Connect to, At, Bus_Numbers)."""
        names = ["IB", "POI", "HV"]
        for r in data.get("mpt", []):
            n = r.get("name")
            if n:
                names += [n, n + ":LV"] + ([n + ":TV"] if r.get("tv_kv") else []) + [n + ":HV"]
        names += [r["name"] for r in data.get("collectors", []) if r.get("name")]
        for r in data.get("generators", []):
            if r.get("name"):
                names += [r["name"], r["name"] + ":MV", r["name"] + ":LV"]
        return names

    def gen_names():
        return [r["name"] for r in data.get("generators", []) if r.get("name")]

    def choices_for(sheet, key, row):
        """The drop-down list of a table cell: its column's choices, or names the inputs give."""
        cols = dict((c[0], c) for c in dict((t[1], t[2]) for t in TABLES)[TABLE_KEY[sheet]])
        c = cols[key]
        if sheet == "Generators" and key in MODEL_CHOICE:
            ok, doc = MODEL_CHOICE[key]
            return [""] + ["%s  -  %s" % (m, doc[m]) for m in ok.get((row.get("type") or "").upper(), ())], True
        if c[3]:
            return [""] + list(c[3]), True
        if (sheet, key) in (("Generators", "connect"), ("Collectors", "connect"), ("Loads_Shunts", "at"),
                            ("Bus_Numbers", "bus")):
            return bus_names(), False
        if (sheet, key) in (("Feeders", "units_of"),) or (key == "generator" and sheet != "Dyr_Params"):
            return gen_names(), False
        if sheet == "Dyr_Params" and key == "generator":
            return gen_names() + ["IB", "PLANT"], False
        if sheet == "Feeders" and key == "collector":
            return gen_names() + [r["name"] for r in data.get("collectors", []) if r.get("name")], False
        if sheet == "Feeders" and key == "cable":
            return [r["cable"] for r in data.get("cables", []) if r.get("cable")], False
        if sheet == "Dyr_Params" and key == "model":
            return list(DYR_LIB), False
        if sheet == "Dyr_Params" and key == "parameter":
            m = (row.get("model") or "").upper()
            return (list(DYR_LIB[m][2]) if m in DYR_LIB else []), False
        if sheet == "Feeders" and key in ("feeder", "section", "from_section"):
            k2 = "feeder" if key == "feeder" else "section"
            same = [r for r in data.get("feeders", []) if key == "feeder" or (
                (r.get("collector") or "").upper() == (row.get("collector") or "").upper() and
                (r.get("feeder") or "").upper() == (row.get("feeder") or "").upper())]
            got = sorted(set(str(r[k2]) for r in same if r.get(k2) not in (None, "")),
                         key=lambda v: (len(v), v))
            if key == "section":
                whole = []
                for v in got:
                    try:
                        whole.append(int(float(v)))
                    except ValueError:
                        pass
                got = got + [str(max(whole or [0]) + 1)]
            return got, False
        if sheet == "PSCAD_Params" and key in ("parameter", "value"):
            gens, secs, _notes = udm_table(data)
            mine = [(k, e) for sec in secs for k, e in sec["params"]
                    if (row.get("generator") or "").upper() in [g.upper() for g in e["cells"]]]
            if key == "parameter":
                return [k for k, _e in mine], False
            e = next((e for k, e in mine if k.upper() == (row.get("parameter") or "").upper()), None)
            if e is not None and e["p"]["choices"]:
                return [""] + e["p"]["choices"], True
            return ["{%s}" % k for k in PSCAD_KEYS] if e is None or "/" not in e["key"] else None, False
        got = SUGGEST.get("%s.%s" % (sheet, key)) or SUGGEST.get(key)
        if got:
            return [""] + list(got), False
        return None, False

    TABLE_KEY = dict((t[0], t[1]) for t in TABLES)

    # ---------------------------------------------------------------- Plant tab: a form in sections
    plant_tab = ttk.Frame(nb)
    nb.add(plant_tab, text="Plant")
    outer, form = scrolled(plant_tab)
    outer.pack(fill="both", expand=True)
    plant_help = tk.StringVar(value="Click a field to see what it is.")
    ttk.Label(plant_tab, textvariable=plant_help, style="Help.TLabel", wraplength=1200, justify="left").pack(
        fill="x", side="bottom")
    plant_total = tk.StringVar()                       # the whole plant, from Generators -- live
    top_bar = ttk.Frame(plant_tab)
    top_bar.pack(fill="x", padx=8, pady=(6, 2), before=outer)
    ttk.Button(top_bar, text="Plant type...", command=lambda: plant_type()).pack(side="right", padx=(8, 0))
    ttk.Label(top_bar, textvariable=plant_total, style="Total.TLabel", wraplength=1000, justify="left").pack(
        side="left", fill="x", expand=True)
    widgets, hints = {}, {}
    row_i = [0]
    field_w, sec_w, sec_of = {}, {}, {}           # each field's widgets, each section's title, each field's section
    for key, item, unit, note, choices in PLANT_FIELDS:
        if key in PLANT_SECTIONS:
            sec_w[key] = ttk.Label(form, text=PLANT_SECTIONS[key], style="Section.TLabel")
            sec_w[key].grid(row=row_i[0], column=0, columnspan=5, sticky="ew", pady=(8, 2))
            row_i[0] += 1
        sec_of[key] = list(sec_w)[-1] if sec_w else None
        var = tk.StringVar()
        app["vars"][key] = var
        lab = ttk.Label(form, text=item, style="Item.TLabel", background="white")
        lab.grid(row=row_i[0], column=0, sticky="w", padx=(8, 4), pady=1)
        field_w[key] = [lab]
        if choices:
            w = ttk.Combobox(form, textvariable=var, values=[""] + list(choices), state="readonly", width=26,
                             style="Input.TCombobox")
        elif key in SUGGEST:                          # typical values to pick, or any typed
            w = ttk.Combobox(form, textvariable=var, values=[""] + list(SUGGEST[key]), width=28,
                             style="Input.TCombobox")
        else:
            w = tk.Entry(form, textvariable=var, width=30 if key != "pscad_libraries" else 60, background=input_bg,
                         relief="solid", borderwidth=1)
        w.grid(row=row_i[0], column=1, sticky="w", padx=2, pady=1)
        widgets[key] = w
        ulab = ttk.Label(form, text=unit, background="white")
        ulab.grid(row=row_i[0], column=2, sticky="w", padx=2)
        blank = "required" if key == "poi_kv" else ("one of SCR, short-circuit MVA or R + X" if key == "grid_scr"
                                                    else _blank_means(note))
        hint = ttk.Label(form, text=("blank = " + blank) if blank and not blank.startswith(("required", "one of"))
                         else blank, style="Req.TLabel" if blank.startswith(("required", "one of")) else "Hint.TLabel",
                         background="white")
        hint.grid(row=row_i[0], column=3, sticky="w", padx=6)
        hints[key] = (hint, hint.cget("text"), hint.cget("style"))
        tag = ttk.Label(form, text=_used_by("Plant", key), style="Tag.TLabel", background="white")
        tag.grid(row=row_i[0], column=4, sticky="w", padx=6)
        field_w[key] += [w, ulab, hint, tag]
        txt = help_text("Plant", key, item, note, choices, unit)
        w.bind("<FocusIn>", lambda e, t=txt: plant_help.set(t))
        var.trace_add("write", dirty) if hasattr(var, "trace_add") else var.trace("w", dirty)
        row_i[0] += 1
    form.columnconfigure(3, weight=1)

    def tie_live(*_a):
        """The gen-tie as the build will take it, beside its total R, X, B -- kept up as its fields change."""
        for k in ("tie_per_unit", "tie_r_ohm", "tie_x_ohm", "tie_b_us"):
            h, txt, style = hints[k]
            h.configure(text=txt, style=style)
        d = dict((k, v.get()) for k, v in app["vars"].items())
        try:
            kv = float(d.get("poi_kv") or 0)
            if not kv > 0:
                return
            ln = line_data(d, "tie_", kv, float(d.get("sbase_mva") or 100.0), float(d.get("freq_hz") or 60.0),
                           "Gen-tie")
        except (InputError, ValueError) as e:
            hints["tie_r_ohm"][0].configure(text=str(e).split(": ", 1)[-1][:150], style="Req.TLabel")
            return
        if ln is None:
            return
        per = next((n for n in ln["notes"] if "values per blank" in n), "")
        if per:
            hints["tie_per_unit"][0].configure(text="blank = per %s here" % per.split("taken per ")[1].split()[0])
        if not d.get("tie_r_ohm") and not d.get("tie_x_ohm"):
            hints["tie_r_ohm"][0].configure(text="now %.5g ohm (%.3f km)  -> r %.6g x %.6g pu on %g MVA" % (
                ln["R"], ln["km"], ln["r"], ln["x"], float(d.get("sbase_mva") or 100.0)))
            hints["tie_x_ohm"][0].configure(text="now %.5g ohm" % ln["X"])
            hints["tie_b_us"][0].configure(text="now %.5g uS  -> b %.6g pu" % (ln["B"], ln["b"]))
    for k in [k for k in app["vars"] if k.startswith("tie_")] + ["poi_kv", "sbase_mva", "freq_hz"]:
        v = app["vars"][k]
        v.trace_add("write", tie_live) if hasattr(v, "trace_add") else v.trace("w", tie_live)

    # ---------------------------------------------------------------- table tabs: a grid each, edited in place
    class Grid(object):
        def __init__(self, sheet, key, cols, page=None, fixed=False):
            """A sheet's rows as a table -- page: shown as a page of its own (part of the sheet's columns, the GSUs
               of the Generators sheet); fixed: its rows are added and removed on the sheet's own page."""
            self.sheet, self.key, self.cols = sheet, key, cols
            self.page, self.fixed = page or sheet, fixed
            self.keys = [c[0] for c in cols]
            self.edit_w = None
            tab = ttk.Frame(nb)
            nb.add(tab, text=self.page)
            tools = ttk.Frame(tab, padding=(4, 4))
            tools.pack(fill="x")
            for txt, fn in (("Add row", self.add), ("Insert above", self.insert), ("Duplicate", self.duplicate),
                            ("Delete", self.delete), ("Move up", lambda: self.move(-1)),
                            ("Move down", lambda: self.move(1))) if not fixed else ():
                ttk.Button(tools, text=txt, command=fn).pack(side="left", padx=2)
            if fixed:
                ttk.Label(tools, text="One row per generator -- add, remove and rename them on the Generators page "
                                      "(Units: here or there, the same value).",
                          style="Item.TLabel").pack(side="left", padx=4, pady=4)
            acts = []                                    # this page's own actions: a row of their own
            if self.page == "GSUs":
                acts.append(("Size GSUs from the units...", self.size_gsus))
            if sheet == "MPT":
                acts.append(("Size MPTs from the generators...", self.size_mpts))
            if sheet in ("MPT", "Feeders"):
                acts.append(("Feeders on each MPT...", self.set_feeder_counts))
            if sheet == "Bus_Numbers":
                acts.append(("List this model's buses", self.fill_buses))
            if sheet == "Machines":
                acts.append(("List the DETAILED machines", self.fill_machines))
            if sheet == "Feeders":
                acts += [("Fill R, X, B from cables", self.fill_rxb), ("Clear R, X, B", self.clear_rxb),
                         ("Lengths in ft", self.lengths_in)]
            if sheet == "Cables":
                acts.append(("C to B", self.c_to_b))
            if sheet == "Generators" and not fixed:
                acts.append(("Pick UDM from OEM model...", self.pick_udm))
            if acts:
                bar = ttk.Frame(tab, padding=(4, 0, 4, 4))
                bar.pack(fill="x")
                for txt, fn in acts:
                    ttk.Button(bar, text=txt, command=fn).pack(side="left", padx=2)
            box = ttk.Frame(tab)
            box.pack(fill="both", expand=True)
            self.tab, self.tools, self.box = tab, tools, box
            self.vis = list(self.keys)                   # the columns shown (the simple view: some of them)
            self.tree = ttk.Treeview(box, columns=self.keys, show="headings", selectmode="browse")
            ys = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
            xs = ttk.Scrollbar(box, orient="horizontal", command=self.tree.xview)
            self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
            self.tree.grid(row=0, column=0, sticky="nsew")
            ys.grid(row=0, column=1, sticky="ns")
            xs.grid(row=1, column=0, sticky="ew")
            box.rowconfigure(0, weight=1)
            box.columnconfigure(0, weight=1)
            self.widths = []
            for c in cols:
                wpx = 700 if c[0] == "record" else max(70, int(c[4] * 8.5), len(c[1]) * 7 + 18)
                self.widths.append(wpx)
                self.tree.heading(c[0], text=c[1], command=lambda k=c[0]: self.show_help(k))
                self.tree.column(c[0], width=wpx, minwidth=50, stretch=False)
            self.tree.tag_configure("odd", background="#FFF9E6")
            self.tree.tag_configure("even", background=input_bg)
            self.help = tk.StringVar(value="Click a column title or a cell to see what the column is; double-click a "
                                           "cell (or press Enter) to edit it, Tab moves on.")
            wrap = lambda e: e.widget.configure(wraplength=max(300, e.width - 12))    # wrapped at the page's width
            hl = ttk.Label(tab, textvariable=self.help, style="Help.TLabel", wraplength=1200, justify="left")
            hl.pack(fill="x", side="bottom")
            hl.bind("<Configure>", wrap)
            self.total = tk.StringVar()                  # Generators, GSUs, MPT, Feeders: what they add up to, live
            if self.page in ("Generators", "GSUs", "MPT", "Feeders"):
                tl = ttk.Label(tab, textvariable=self.total, style="Total.TLabel", wraplength=1200, justify="left")
                tl.pack(fill="x", side="bottom", padx=8, pady=(4, 2))
                tl.bind("<Configure>", wrap)
            self.col = 0
            self.tree.bind("<Double-1>", self.on_double)
            self.tree.bind("<Button-1>", self.on_click, add="+")
            self.tree.bind("<Return>", lambda e: self.edit(self.current(), self.col))
            self.tree.bind("<F2>", lambda e: self.edit(self.current(), self.col))
            self.tree.bind("<Delete>", lambda e: self.clear_cell())
            self.tree.bind("<Left>", lambda e: self.step_col(-1))
            self.tree.bind("<Right>", lambda e: self.step_col(1))
            self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_help(self.keys[self.col]))

        @property
        def rows(self):
            return data.setdefault(self.key, [])

        def shown(self, r):
            """A row's cells as shown: a blank model choice as the one the build takes, in brackets -- an MPT's blank
               windings, MVA and three-winding values too."""
            out = []
            dflt = mpt_blanks(r, mpt_loads(data)) if self.sheet == "MPT" else cable_b(r, data) if \
                self.sheet == "Cables" else gen_blanks(r) if self.sheet == "Generators" else {}
            for k in self.keys:
                v = r.get(k) or ""
                if not v and self.sheet == "Generators" and k in MODEL_CHOICE:
                    ok = MODEL_CHOICE[k][0].get((r.get("type") or "").upper())
                    v = "(%s)" % ok[0] if ok else ""
                if not v and k in dflt:
                    v = "(%s)" % ("%.6g" % dflt[k] if isinstance(dflt[k], float) else dflt[k])
                out.append(v)
            return out

        def sums(self):
            if self.page == "GSUs":
                lines, f = [], lambda x: "%.6g" % x
                for r in _rows(data, "generators"):
                    nm = _txt(r.get("name"))
                    if not nm:
                        continue
                    try:
                        n, _p1, _p0, _pf, _q1, _q0, s1 = unit_rating(r, nm)
                        mva = _num(r, "gsu_mva", nm, None, positive=True)
                        cnt, per = gsu_split(r, nm, n)
                        z, hv, lv = _num(r, "gsu_z_pct", nm), _txt(r.get("gsu_hv_kv")), _txt(r.get("gsu_lv_kv"))
                    except InputError as e:
                        lines.append("   %s: %s" % (nm, str(e).split(": ", 1)[-1]))
                        continue
                    if not mva:
                        lines.append("   %s: no GSU (its units on the MV bus)" % nm)
                        continue
                    each = per * s1
                    lines.append("   %s: %d x %s MVA = %s MVA, %s / %s kV%s  |  on each: %s unit%s of %s MVA = %s MVA "
                                 "(%.0f %% of a GSU)%s" % (
                                     nm, cnt, f(mva), f(cnt * mva), hv or "?", lv or "the terminal's",
                                     (", Z %s %%" % f(z)) if z is not None else "", f(per), "" if per == 1 else "s",
                                     f(s1), f(each), 100.0 * each / mva,
                                     " -- above its rating" if each > mva * 1.02 else
                                     " -- far above them: Size GSUs from the units" if mva > 2.0 * each else ""))
                self.total.set(("GSUs -- each generator's, as the models take them:\n" + "\n".join(lines))
                               if lines else "")
                return
            if self.sheet == "Generators":
                rows = [r for r in self.rows if any(v not in (None, "") for v in r.values())]
                self.total.set("Totals -- units x one unit, as the models take them:\n" +
                               "\n".join("   " + unit_totals(r) for r in rows) if rows else "")
            if self.sheet in ("Generators", "MPT", "Collectors", "Feeders"):
                live_totals()

        def refresh(self, select=None):
            self.tree.delete(*self.tree.get_children())
            self.sums()
            for i, r in enumerate(self.rows):
                self.tree.insert("", "end", iid=str(i), values=self.shown(r),
                                 tags=("odd" if i % 2 else "even",))
            if select is not None and 0 <= select < len(self.rows):
                self.tree.selection_set(str(select))
                self.tree.focus(str(select))
                self.tree.see(str(select))
            if "vis_keys" in app:                        # a column now holding a value of the user's shows
                vis = app["vis_keys"](self)
                if vis != getattr(self, "vis", None):
                    self.set_vis(vis)

        def current(self):
            sel = self.tree.selection()
            return int(sel[0]) if sel else None

        def show_help(self, key):
            c = self.cols[self.keys.index(key)]
            self.help.set(help_text(self.sheet, key, c[1], c[2] or "", c[3]))

        def set_vis(self, keys):
            """Show these columns only (the simple view), the rest kept as they are."""
            self.close_edit(True)
            self.vis = [k for k in self.keys if k in keys] or list(self.keys)
            self.tree.configure(displaycolumns=self.vis)
            if self.keys[self.col] not in self.vis:
                self.col = self.keys.index(self.vis[0])

        def at_x(self, x):
            """The column (its index among all) under x."""
            n = int(self.tree.identify_column(x)[1:] or 1) - 1
            return self.keys.index(self.vis[max(0, min(n, len(self.vis) - 1))])

        def on_click(self, e):
            self.close_edit(True)
            if self.tree.identify_region(e.x, e.y) == "cell":
                self.col = self.at_x(e.x)
                self.show_help(self.keys[self.col])
                item = self.tree.identify_row(e.y)
                if item and self.sheet == "Generators" and self.keys[self.col] in MODEL_CHOICE:
                    self.tree.after_idle(lambda i=int(item), c=self.col: self.drop(i, c))

        def drop(self, i, ci):
            """A model choice's list, open: one click on its cell."""
            self.edit(i, ci)
            if self.edit_w and isinstance(self.edit_w[0], ttk.Combobox):
                try:
                    self.edit_w[0].tk.call("ttk::combobox::Post", self.edit_w[0])
                except tk.TclError:
                    pass

        def on_double(self, e):
            if self.tree.identify_region(e.x, e.y) != "cell":
                return
            item = self.tree.identify_row(e.y)
            self.col = self.at_x(e.x)
            if item:
                self.edit(int(item), self.col)

        def step_col(self, d):
            v = self.vis.index(self.keys[self.col]) if self.keys[self.col] in self.vis else 0
            self.col = self.keys.index(self.vis[max(0, min(len(self.vis) - 1, v + d))])
            self.show_help(self.keys[self.col])
            self.reveal(self.col)
            return "break"

        def reveal(self, ci):
            """Scroll so that column ci shows."""
            ws = [self.widths[self.keys.index(k)] for k in self.vis]
            k = self.vis.index(self.keys[ci]) if self.keys[ci] in self.vis else 0
            total, left = float(sum(ws)), sum(ws[:k])
            first, last = self.tree.xview()
            if left / total < first or (left + ws[k]) / total > last:
                self.tree.xview_moveto(max(0.0, left / total - 0.02))

        def edit(self, i, ci):
            if i is None or not (0 <= i < len(self.rows)):
                return
            if self.fixed and self.keys[ci] == "name":
                set_status("%s is set on the Generators page" % self.cols[ci][1])
                return
            self.close_edit(True)
            self.tree.see(str(i))
            self.reveal(ci)
            self.tree.update_idletasks()
            bbox = self.tree.bbox(str(i), self.keys[ci])
            if not bbox:
                return
            x, y, wd, ht = bbox
            key = self.keys[ci]
            row = self.rows[i]
            values, fixed = choices_for(self.sheet, key, row)
            var = tk.StringVar(value=next((x for x in values or [] if row.get(key) and x.split(" ")[0] == row.get(key)),
                                          row.get(key) or ""))
            if values is not None:
                w = ttk.Combobox(self.tree, textvariable=var, values=values, state="readonly" if fixed else "normal")
            else:
                w = tk.Entry(self.tree, textvariable=var, relief="solid", borderwidth=1, background="white",
                             font=("Consolas", 9) if key == "record" else None)
            width = max(wd, 120 if values is not None else wd)
            if values and self.sheet == "Generators" and key in MODEL_CHOICE:      # wide enough to read each one
                try:
                    import tkinter.font as tkfont
                    width = max(width, min(640, max(tkfont.nametofont("TkDefaultFont").measure(x) for x in values) +
                                           40))
                except (tk.TclError, ImportError):
                    pass
            w.place(x=x, y=y, width=width, height=ht)
            w.focus_set()
            if isinstance(w, tk.Entry):
                w.select_range(0, "end")
            self.edit_w = (w, var, i, ci)
            w.bind("<Return>", lambda e: (self.close_edit(True), self.move_cell(1, 0)))
            w.bind("<KP_Enter>", lambda e: (self.close_edit(True), self.move_cell(1, 0)))
            w.bind("<Tab>", lambda e: (self.close_edit(True), self.move_cell(0, 1), "break")[-1])
            w.bind("<ISO_Left_Tab>", lambda e: (self.close_edit(True), self.move_cell(0, -1), "break")[-1])
            w.bind("<Shift-Tab>", lambda e: (self.close_edit(True), self.move_cell(0, -1), "break")[-1])
            w.bind("<Escape>", lambda e: self.close_edit(False))
            w.bind("<FocusOut>", lambda e: self.close_edit(True) if not isinstance(w, ttk.Combobox) else None)
            if isinstance(w, ttk.Combobox):
                w.bind("<<ComboboxSelected>>", lambda e: self.close_edit(True))
            self.show_help(key)

        def close_edit(self, keep):
            if not self.edit_w:
                return
            w, var, i, ci = self.edit_w
            self.edit_w = None
            if keep and i < len(self.rows):
                key = self.keys[ci]
                v = var.get().strip()
                if self.sheet == "Generators" and key in MODEL_CHOICE:
                    v = v.split(" ")[0] if v else ""          # its code, not what it is for
                if self.sheet == "PSCAD_Params" and key == "value" and " = " in v:
                    v = v.split("=")[0].strip()
                if (self.rows[i].get(key) or "") != v:
                    was = self.rows[i].get(key)
                    if v:
                        self.rows[i][key] = v
                    else:
                        self.rows[i].pop(key, None)
                    for g in app["grids"].values():          # the other page of these rows (GSUs)
                        if g is not self and g.key == self.key:
                            g.refresh(i)
                    n = rename_refs(data, self.key, key, was, v) if v else 0
                    if n:                                 # a name: where it is used, the new one too
                        for g in app["grids"].values():
                            if g is not self:
                                g.refresh()
                        set_status("%s renamed %s: %d place(s) using it changed too" % (was, v, n))
                    said = None
                    if self.sheet == "Generators" and key == "units":      # its feeders follow its Units
                        said = feeders_follow(self.rows[i], was, v)
                    elif self.sheet == "Generators" and key in ("unit_mva", "unit_pmax_mw", "unit_pmin_mw"):
                        said = q_follow(self.rows[i], key, was)          # its Q follows its MVA and P
                    if self.sheet == "Generators" and key == "type":       # model choices its Type does not take
                        gone, lab = [], dict((c[0], c[1]) for c in self.cols)
                        for k, (ok, _doc) in MODEL_CHOICE.items():
                            if self.rows[i].get(k) and self.rows[i][k] not in ok.get(v.upper(), ()):
                                gone.append("'%s' %s" % (lab.get(k, k), self.rows[i].pop(k)))
                        if gone:                                  # said, not dropped unseen
                            set_status("Type %s: %s cleared -- not a choice for %s" % (v, ", ".join(gone), v))
                            out("[smib] %s row %d: Type %s -- %s cleared (not a choice for %s)\n" % (
                                self.sheet, i + 1, v, ", ".join(gone), v))
                    self.tree.item(str(i), values=self.shown(self.rows[i]))
                    self.sums()
                    dirty()
                    if said:
                        set_status(said)
            w.destroy()
            self.tree.focus_set()

        def move_cell(self, dr, dc):
            i = self.current()
            if i is None:
                return
            v = (self.vis.index(self.keys[self.col]) if self.keys[self.col] in self.vis else 0) + dc
            if v >= len(self.vis):
                v, dr = 0, 1
            if v < 0:
                v, dr = len(self.vis) - 1, -1
            ci = self.keys.index(self.vis[v])
            i += dr
            if i >= len(self.rows):
                return
            i = max(0, i)
            self.col = ci
            self.tree.selection_set(str(i))
            self.tree.focus(str(i))
            if dc:
                self.edit(i, ci)

        def clear_cell(self):
            i = self.current()
            if i is not None and self.rows[i].pop(self.keys[self.col], None) is not None:
                self.tree.set(str(i), self.keys[self.col], "")
                dirty()

        def add(self):
            self.close_edit(True)
            self.rows.append({})
            self.refresh(len(self.rows) - 1)
            dirty()
            self.col = 0
            self.edit(len(self.rows) - 1, 0)

        def insert(self):
            i = self.current()
            if i is None:
                return self.add()
            self.close_edit(True)
            self.rows.insert(i, {})
            self.refresh(i)
            dirty()

        def duplicate(self):
            i = self.current()
            if i is None:
                return
            self.close_edit(True)
            self.rows.insert(i + 1, dict(self.rows[i]))
            self.refresh(i + 1)
            dirty()

        def delete(self):
            i = self.current()
            if i is None:
                return
            self.close_edit(False)
            label = self.rows[i].get(self.keys[0]) or "row %d" % (i + 1)
            if not messagebox.askyesno("Delete row", "Delete %s from %s?" % (label, self.sheet), parent=root):
                return
            del self.rows[i]
            self.refresh(min(i, len(self.rows) - 1))
            dirty()

        def move(self, d):
            i = self.current()
            if i is None or not 0 <= i + d < len(self.rows):
                return
            self.close_edit(True)
            r = self.rows
            r[i], r[i + d] = r[i + d], r[i]
            self.refresh(i + d)
            dirty()

        def fill_rxb(self):
            """Blank R ohm / X ohm / B uS cells: the values the cables give (Cable x Length), to see and change.
               A value in a cell stays as it is when the cable or length changes; Clear R, X, B goes back."""
            try:
                inp = read_input(save_temp())
                cabs = read_cables(inp["cables"])
                f = _num(inp["plant"], "freq_hz", "Plant", 60.0, positive=True)
            except (InputError, SolveError) as e:
                messagebox.showerror("Feeders", "The inputs do not read yet:\n\n%s" % e, parent=root)
                return
            n = 0
            for i, r in enumerate(self.rows):
                cab = cabs.get(str(r.get("cable") or "").strip().upper())
                if cab is None or str(r.get("length") or "").strip() == "":
                    continue
                try:
                    vals = section_rxb(r, cab, f, "Feeders row %d" % (i + 1))
                except InputError:
                    continue
                for k, v in zip(("r_ohm", "x_ohm", "b_us"), vals):
                    if str(r.get(k) or "").strip() == "":
                        r[k] = _g(v, 6)
                        n += 1
            self.refresh(self.current())
            if n:
                dirty()
            set_status("%d R / X / B cell(s) filled from the cables -- change any; a filled value no longer follows "
                       "its cable and length (Clear R, X, B)" % n)

        def c_to_b(self):
            """Every cable's C per length as B per length (B = 2 pi f C, the Plant's frequency), C cleared; a row
               with both: its B kept, its C cleared."""
            self.close_edit(True)
            f = _cell_num(data.get("plant") or {}, "freq_hz", 60.0) or 60.0
            n, bad = 0, []
            for i, r in enumerate(self.rows):
                c = _txt(r.get("c_per"))
                if c is None:
                    continue
                try:
                    x = float(c)
                except ValueError:
                    bad.append(str(i + 1))
                    continue
                if _txt(r.get("b_per")) is None:
                    r["b_per"] = _g(2.0 * math.pi * f * x, 6)
                r.pop("c_per", None)
                n += 1
            self.refresh(self.current())
            if n:
                dirty()
            set_status("%d cable(s): C per length now B per length (B = 2 pi x %g Hz x C)%s" % (
                n, f, ("; rows %s: C is no number, left as it is" % ", ".join(bad)) if bad else ""))

        def lengths_in(self, unit="ft"):
            """Every Feeders row's length in `unit`, the same length: its value converted, its Length unit set."""
            self.close_edit(True)
            n, bad = 0, []
            for i, r in enumerate(self.rows):
                v, u = _txt(r.get("length")), (_txt(r.get("unit")) or FEEDER_UNIT).lower()
                if v is None:
                    continue
                try:
                    x = float(v)
                except ValueError:
                    bad.append(str(i + 1))
                    continue
                if u not in LEN_KM:
                    bad.append(str(i + 1))
                    continue
                if u == unit and _txt(r.get("unit")) == unit:
                    continue
                r["length"], r["unit"] = "%.6g" % (x * LEN_KM[u] / LEN_KM[unit]), unit
                n += 1
            self.refresh(self.current())
            if n:
                dirty()
            set_status("%d feeder length(s) now in %s -- the same lengths%s" % (
                n, unit, ("; rows %s left as they are (a length or unit that does not read)" % ", ".join(bad))
                if bad else ""))

        def clear_rxb(self):
            n = 0
            for r in self.rows:
                for k in ("r_ohm", "x_ohm", "b_us"):
                    if str(r.pop(k, "") or "").strip():
                        n += 1
            self.refresh(self.current())
            if n:
                dirty()
            set_status("%d R / X / B cell(s) cleared: each section from its cable and length again" % n)

        def pick_udm(self, path=None):
            """The selected generator's UDM from an OEM's PSCAD model: its definitions listed (ports, parameters,
               the IDs of the ones placed in it); the one picked is added to the case and put at every GSU."""
            i = self.current()
            if i is None:
                return messagebox.showinfo("Pick UDM", "Select a generator row first.", parent=root)
            gname = self.rows[i].get("name") or "the generator"
            path = path or filedialog.askopenfilename(
                parent=root, title="The OEM's PSCAD model for %s" % gname, initialdir=here,
                filetypes=[("PSCAD case or library", "*.pscx *.pslx"), ("All files", "*.*")])
            if not path:
                return
            try:
                scope, defs = oem_model(path)
            except Exception as e:
                return messagebox.showerror("Pick UDM", "%s cannot be read:\n\n%s" % (path, e), parent=root)
            defs = [d for d in defs if d["name"].upper() != "MAIN"]
            if not defs:
                return messagebox.showerror("Pick UDM", "%s has no definitions of its own." % path, parent=root)
            defs.sort(key=lambda d: (not d["ids"], not any(k == "electrical" for _n, _d, k in d["ports"]),
                                     d["name"].upper()))
            top = tk.Toplevel(root)
            top.title("Pick %s's UDM -- %s" % (gname, os.path.basename(path)))
            top.transient(root)
            ttk.Label(top, text="The definitions in %s (project %s). The one you pick is added to the PSCAD case "
                                "and put at every GSU of %s; a component ID copies that placed one instead, its "
                                "settings and all." % (os.path.basename(path), scope, gname), wraplength=760,
                      justify="left").pack(fill="x", padx=10, pady=(10, 4))
            box = ttk.Frame(top)
            box.pack(fill="both", expand=True, padx=10)
            cols = ("defn", "desc", "ports", "params", "ids")
            tv = ttk.Treeview(box, columns=cols, show="headings", height=12, selectmode="browse")
            for c_, t_, w_ in zip(cols, ("Definition", "Description", "Ports", "Parameters", "Placed (IDs)"),
                                  (150, 200, 220, 90, 140)):
                tv.heading(c_, text=t_)
                tv.column(c_, width=w_, stretch=(c_ == "desc"))
            ys = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
            tv.configure(yscrollcommand=ys.set)
            tv.pack(side="left", fill="both", expand=True)
            ys.pack(side="right", fill="y")
            for k, d in enumerate(defs):
                tv.insert("", "end", iid=str(k), values=(
                    d["name"], d["desc"], ", ".join("%s%s" % (n, " (3-ph)" if dm == 3 else "" if kd == "electrical"
                                                              else " (%s)" % kd) for n, dm, kd in d["ports"]),
                    len(d["params"]), ", ".join(d["ids"][:4]) + (" ..." if len(d["ids"]) > 4 else "")))
            form = ttk.Frame(top)
            form.pack(fill="x", padx=10, pady=6)
            ttk.Label(form, text="AC port").grid(row=0, column=0, sticky="w")
            port = tk.StringVar()
            pc = ttk.Combobox(form, textvariable=port, width=18)
            pc.grid(row=0, column=1, sticky="w", padx=6)
            ttk.Label(form, text="Copy the placed one").grid(row=0, column=2, sticky="w", padx=(18, 0))
            iid = tk.StringVar()
            ic = ttk.Combobox(form, textvariable=iid, width=26, state="readonly")
            ic.grid(row=0, column=3, sticky="w", padx=6)
            fresh = "no: the definition, values from PSCAD_Params"

            def chosen(*_a):
                sel = tv.selection()
                if not sel:
                    return
                d = defs[int(sel[0])]
                el = [n for n, dm, kd in d["ports"] if kd == "electrical"]
                three = [n for n, dm, kd in d["ports"] if kd == "electrical" and dm == 3]
                pc.configure(values=el)
                port.set((three or el or [""])[0])
                ic.configure(values=[fresh] + ["ID %s" % x for x in d["ids"]])
                iid.set(fresh)
            tv.bind("<<TreeviewSelect>>", chosen)
            tv.selection_set("0")
            chosen()
            got = {}

            def ok():
                sel = tv.selection()
                if sel:
                    got["v"] = (defs[int(sel[0])]["name"], port.get().strip(),
                                iid.get()[3:] if iid.get().startswith("ID ") else None)
                top.destroy()
            bb = ttk.Frame(top)
            bb.pack(pady=(0, 10))
            ttk.Button(bb, text="Use it for %s" % gname, command=ok).pack(side="left", padx=4)
            ttk.Button(bb, text="Cancel", command=top.destroy).pack(side="left", padx=4)
            app["udm_dialog"] = {"top": top, "tree": tv, "port": port, "iid": iid, "ok": ok}
            top.grab_set()
            root.wait_window(top)
            app["udm_dialog"] = None
            if "v" in got:
                self.use_udm(i, path, *got["v"])

        def use_udm(self, i, path, defn, port, iid=None):
            r = self.rows[i]
            r["pscad_source"], r["pscad_definition"] = path, defn
            for k, v in (("pscad_ac_port", port), ("pscad_component_id", iid)):
                if v:
                    r[k] = str(v)
                else:
                    r.pop(k, None)
            self.refresh(i)
            dirty()
            set_status("%s: UDM %s from %s%s -- put at every GSU when the PSCAD case is built" % (
                r.get("name") or "the generator", defn, os.path.basename(path),
                (", a copy of the placed one, ID %s" % iid) if iid else ""))

        def fill_machines(self):
            """The DETAILED model's machines (whatever Plant: Model type says) with the values they get now."""
            try:
                inp = read_input(save_temp())
                inp["plant"]["model_type"], inp["machines"] = "DETAILED", []
                net = build_network(inp, Log(echo=False))
            except (InputError, SolveError) as e:
                messagebox.showerror("Machines", "The inputs do not build yet:\n\n%s" % e, parent=root)
                return
            have = set((r.get("machine") or "").upper() for r in self.rows)
            for g in net.gens:
                if g.get("family") and g["name"].upper() not in have:
                    self.rows.append({"machine": g["name"], "id": g["id"], "pgen_mw": round(g["dispatch"], 4),
                                      "pmax_mw": round(g["pmax"], 4), "pmin_mw": round(g["pmin"], 4),
                                      "qmax_mvar": round(g["qmax"], 4), "qmin_mvar": round(g["qmin"], 4),
                                      "mbase_mva": round(g["mbase"], 4)})
            self.refresh()
            dirty()

        def fill_buses(self):
            try:
                inp = read_input(save_temp())
                net = build_network(inp, Log(echo=False))
            except (InputError, SolveError) as e:
                messagebox.showerror("Bus names", "The inputs do not build yet:\n\n%s" % e, parent=root)
                return
            have = set((r.get("bus") or "").upper() for r in self.rows)
            for role in net.bus:
                nm = role.replace("C:", "")
                if nm.upper() not in have:
                    self.rows.append({"bus": nm})
            self.refresh()
            dirty()

        def sizing_dialog(self, title, intro, heads):
            """A sizing dialog's frame: its title, what it does, a table of rows under these headings (filled by
               the caller) and its buttons -> (top, table, buttons frame)."""
            top = tk.Toplevel(root)
            top.title(title)
            top.transient(root)
            ttk.Label(top, text=intro, wraplength=900, justify="left").pack(fill="x", padx=10, pady=(10, 6))
            tab = ttk.Frame(top)
            tab.pack(fill="both", expand=True, padx=10)
            for c, h in enumerate(heads):
                ttk.Label(tab, text=h, style="Item.TLabel").grid(row=0, column=c, sticky="w", padx=4, pady=(0, 4))
            bb = ttk.Frame(top)
            bb.pack(pady=10)
            return top, tab, bb

        def size_gsus(self):
            """Each generator's GSUs from its units: how many share one GSU -> the GSU count (left blank: it
               follows Units) and a GSU MVA for them (the nameplate given where it carries them, else their MVA
               rounded up) -- shown first, set only for the generators ticked."""
            self.close_edit(True)
            f = lambda x: "%.6g" % x
            top, tab, bb = self.sizing_dialog(
                "Size the GSUs from the units",
                "For each generator: how many of its units share one GSU. The GSU count follows -- Units / units per "
                "GSU, rounded up ('GSU count' is left blank, so it keeps following Units). The GSU MVA: the one you "
                "gave where it carries those units (up to twice their MVA), else their MVA rounded up. Change any "
                "value; the ticked generators are set.",
                ("Generator", "Its units", "Units per GSU", "GSU MVA (one)", "Then"))
            rows = []
            for i, r in enumerate(self.rows):
                nm = _txt(r.get("name"))
                if not nm or nm.startswith("#"):
                    continue
                k = len(rows) + 1
                try:
                    n, _p1, _p0, _pf, _q1, _q0, s1 = unit_rating(r, nm)
                    per0 = gsu_per_now(r)
                    _cnt, _load, mva0 = gsu_sizing(r, per0)
                    have = _num(r, "gsu_mva", nm, None, positive=True)
                except InputError as e:
                    ttk.Label(tab, text=nm).grid(row=k, column=0, sticky="w", padx=4)
                    ttk.Label(tab, text="does not read yet: %s" % str(e).split(": ", 1)[-1], style="Req.TLabel",
                              wraplength=600).grid(row=k, column=1, columnspan=4, sticky="w", padx=4)
                    rows.append(None)
                    continue
                use = tk.BooleanVar(value=bool(have) and (have > 2.0 * per0 * s1 or per0 * s1 > have * 1.02 or
                                                          _txt(r.get("gsu_units")) is None))
                per, mva, then = tk.StringVar(value=str(per0)), tk.StringVar(value=f(mva0)), tk.StringVar()
                ttk.Checkbutton(tab, text=nm, variable=use).grid(row=k, column=0, sticky="w", padx=4, pady=2)
                ttk.Label(tab, text="%d x %s MVA = %s MVA" % (n, f(s1), f(n * s1))).grid(row=k, column=1, sticky="w",
                                                                                        padx=4)
                tk.Spinbox(tab, from_=1, to=n, textvariable=per, width=6).grid(row=k, column=2, sticky="w", padx=4)
                ttk.Entry(tab, textvariable=mva, width=9).grid(row=k, column=3, sticky="w", padx=4)
                ttk.Label(tab, textvariable=then, wraplength=520, justify="left").grid(row=k, column=4, sticky="w",
                                                                                      padx=4)
                row = {"i": i, "name": nm, "n": n, "s1": s1, "have": have, "use": use, "per": per, "mva": mva,
                       "then": then, "last": per0}

                def show(*_a, row=row):
                    try:
                        p_ = max(1, min(int(float(row["per"].get())), row["n"]))
                    except ValueError:
                        return row["then"].set("units per GSU: a whole number, 1 to %d" % row["n"])
                    if p_ != row["last"]:                 # a new split: its MVA suggested again
                        row["last"] = p_
                        row["mva"].set(f(gsu_sizing(self.rows[row["i"]], p_)[2]))
                    try:
                        m_ = float(row["mva"].get())
                    except ValueError:
                        return row["then"].set("GSU MVA: a number")
                    cnt = int(math.ceil(row["n"] / float(p_) - 1e-9))
                    last = row["n"] - (cnt - 1) * p_
                    load = p_ * row["s1"]
                    row["then"].set("%d GSU%s x %s MVA = %s MVA (%s unit%s on each%s): %s MVA on one, %.0f %%%s%s" % (
                        cnt, "" if cnt == 1 else "s", f(m_), f(cnt * m_), p_, "" if p_ == 1 else "s",
                        (", %d on the last" % last) if last != p_ else "", f(load), 100.0 * load / m_ if m_ else 0,
                        " -- above its MVA" if load > m_ * 1.02 else " -- far above them" if m_ > 2 * load else "",
                        "" if row["have"] else "  (no GSU now: it needs GSU kV and Z % too)"))
                for v in (per, mva):
                    v.trace_add("write", show) if hasattr(v, "trace_add") else v.trace("w", show)
                show()
                rows.append(row)

            def ok():
                done, bad = [], []
                for row in rows:
                    if row is None or not row["use"].get():
                        continue
                    try:
                        p_ = int(float(row["per"].get()))
                        m_ = float(row["mva"].get())
                        if not (1 <= p_ and m_ > 0):
                            raise ValueError
                    except ValueError:
                        bad.append(row["name"])
                        continue
                    r = self.rows[row["i"]]
                    r["gsu_units"], r["gsu_mva"] = str(min(p_, row["n"])), f(m_)
                    r.pop("gsu_count", None)               # blank: it follows Units / Units per GSU
                    cnt = int(math.ceil(row["n"] / float(min(p_, row["n"])) - 1e-9))
                    done.append("%s %d x %s MVA (%d units on each)" % (row["name"], cnt, f(m_), min(p_, row["n"])))
                if bad:
                    return messagebox.showerror("Size GSUs", "Units per GSU (1 or more) and GSU MVA (above 0) for "
                                                "%s" % ", ".join(bad), parent=top)
                top.destroy()
                if done:
                    for g in app["grids"].values():
                        if g.key == self.key:
                            g.refresh(self.current())
                    dirty()
                set_status(("GSUs set: %s -- 'GSU count' blank: it follows Units" % "; ".join(done)) if done else
                           "GSUs: nothing ticked, nothing changed")
            ttk.Button(bb, text="Set the ticked GSUs", command=ok).pack(side="left", padx=4)
            ttk.Button(bb, text="Cancel", command=top.destroy).pack(side="left", padx=4)
            app["size_dialog"] = {"top": top, "rows": rows, "ok": ok}
            top.grab_set()
            root.wait_window(top)
            app["size_dialog"] = None

        def set_feeder_counts(self):
            """How many identical feeders each generator has on its MPT -- its units spread over them, section by
               section as its first feeder has them (set_feeders); an MPT's number shared out to its generators by
               their MW. Only those changed (or whose feeders do not carry its Units) are set."""
            self.close_edit(True)
            on, lay = mpt_of(data), feeder_layout(data)
            shared = set((_txt(c.get("name")) or "").upper() for c in _rows(data, "collectors"))
            order = [(_txt(m.get("name")) or "").upper() for m in _rows(data, "mpt")]
            groups = collections.OrderedDict((m, []) for m in order if m)
            for r in _rows(data, "generators"):
                nm = _txt(r.get("name"))
                if nm:
                    groups.setdefault(on.get(nm.upper(), ("not on an MPT",))[0], []).append(r)
            top, tab, bb = self.sizing_dialog(
                "Feeders on each MPT",
                "How many identical feeders each generator has: its units are spread over them (Units / feeders on "
                "each, one more on the first feeders where it does not divide), section by section as its feeder "
                "has them -- its sections, cables and lengths kept; a section with no units at it or beyond is not "
                "needed. An MPT's number is shared out to its generators by their MW; change any generator's. Only "
                "the ones changed are set. A shared collector, or feeders not alike: their Copies on the Feeders "
                "page. (Change a generator's Units and its feeders follow by themselves: Copies of its feeder as "
                "drawn, the units left over on one more.)",
                ("MPT / generator", "Now", "Feeders", "Then"))
            rows, k, busy = [], 1, []
            for m, gl in groups.items():
                if not gl:
                    continue
                mv, mthen = tk.StringVar(), tk.StringVar()
                ttk.Label(tab, text=m, style="Item.TLabel").grid(row=k, column=0, sticky="w", padx=4, pady=(6, 2))
                ttk.Label(tab, textvariable=mthen, style="Hint.TLabel").grid(row=k, column=1, sticky="w", padx=4)
                msp = tk.Spinbox(tab, from_=1, to=9999, textvariable=mv, width=6)
                msp.grid(row=k, column=2, sticky="w", padx=4)
                ttk.Label(tab, text="shared out by MW", style="Hint.TLabel").grid(row=k, column=3, sticky="w", padx=4)
                k += 1
                mine = []
                for r in gl:
                    nm = _txt(r.get("name"))
                    fs = lay.get(nm.upper())
                    ttk.Label(tab, text="   " + nm).grid(row=k, column=0, sticky="w", padx=4)
                    try:
                        u = _int(r, "units", nm, required=True, minimum=1)
                        mw = u * max(abs(_num(r, "unit_pmax_mw", nm, 0.0)), 1e-6)
                    except InputError as x:
                        u, mw, fs = None, 0.0, None
                        why = "does not read yet: %s" % str(x).split(": ", 1)[-1]
                    else:
                        why = None
                    if fs and nm.upper() not in shared:
                        now = sum(c for _f, c, _n in fs)
                        carried = sum(c * n for _f, c, n in fs)
                        try:
                            set_feeders(data, nm, now)
                        except InputError as x:
                            why = str(x).split(": ", 1)[-1]
                    elif why is None:
                        why = "no feeders of its own (Collector EQUIVALENT or NONE, or a shared collector's)"
                    if why:
                        ttk.Label(tab, text=why, style="Hint.TLabel", wraplength=560, justify="left").grid(
                            row=k, column=1, columnspan=3, sticky="w", padx=4)
                        k += 1
                        continue
                    ttk.Label(tab, text="%s = %d of its %d units" % (", ".join("%s x %d: %d each" % (f, c, n_)
                                                                              for f, c, n_ in fs), carried, u)).grid(
                        row=k, column=1, sticky="w", padx=4)
                    need = feeders_needed(data, nm)
                    v, then = tk.StringVar(value=str(now if carried == u or not need else need[3])), tk.StringVar()
                    tk.Spinbox(tab, from_=1, to=u, textvariable=v, width=6).grid(row=k, column=2, sticky="w", padx=4)
                    ttk.Label(tab, textvariable=then, wraplength=560, justify="left").grid(row=k, column=3, sticky="w",
                                                                                          padx=4)
                    row = {"name": nm, "now": now, "ok": carried == u, "u": u, "mw": mw, "v": v, "then": then,
                           "mpt": mv}
                    mine.append(row)
                    rows.append(row)
                    k += 1

                def per_mpt(*_a, mine=mine, mv=mv, mthen=mthen):
                    if busy or not mine:
                        return
                    try:
                        n = int(float(mv.get()))
                    except ValueError:
                        return
                    n = max(n, len(mine))
                    busy.append(1)
                    try:
                        for row, x in zip(mine, _split(n, [r_["mw"] for r_ in mine])):
                            row["v"].set(str(max(1, min(x, row["u"]))))
                    finally:
                        busy.pop()
                    show_mpt(mine, mv, mthen)

                def show_mpt(mine, mv, mthen):
                    now = sum(r_["now"] for r_ in mine)
                    mthen.set("now %d feeder%s" % (now, "" if now == 1 else "s"))
                mv.trace_add("write", per_mpt) if hasattr(mv, "trace_add") else mv.trace("w", per_mpt)
                for row in mine:
                    def show(*_a, row=row, mine=mine, mv=mv):
                        try:
                            n = int(float(row["v"].get()))
                        except ValueError:
                            return row["then"].set("a whole number, 1 to %d" % row["u"])
                        if n == row["now"] and row["ok"]:
                            row["then"].set("as it is")
                        else:
                            try:
                                row["then"].set(set_feeders(data, row["name"], n)[1].split(" -- ", 1)[-1])
                            except InputError as x:
                                row["then"].set(str(x).split(": ", 1)[-1])
                        if not busy:
                            busy.append(1)
                            try:
                                tot = 0
                                for r_ in mine:
                                    try:
                                        tot += int(float(r_["v"].get()))
                                    except ValueError:
                                        pass
                                mv.set(str(tot))
                            finally:
                                busy.pop()
                    row["v"].trace_add("write", show) if hasattr(row["v"], "trace_add") else row["v"].trace("w", show)
                    show()
                show_mpt(mine, mv, mthen)
                if not mine:
                    mv.set("")
                    msp.configure(state="disabled")

            def ok():
                done, bad = [], []
                for row in rows:
                    try:
                        n = int(float(row["v"].get()))
                    except ValueError:
                        bad.append("%s: a whole number" % row["name"])
                        continue
                    if n == row["now"] and row["ok"]:
                        continue
                    try:
                        data["feeders"], said = set_feeders(data, row["name"], n)
                    except InputError as x:
                        bad.append(str(x))
                        continue
                    done.append(said)
                if bad:
                    return messagebox.showerror("Feeders on each MPT", "\n".join(bad), parent=top)
                top.destroy()
                if done:
                    for g in app["grids"].values():
                        if g.key == "feeders":
                            g.refresh()
                    live_totals()
                    dirty()
                set_status(("Feeders set: %s" % " | ".join(done)) if done else "Feeders: nothing changed")
            ttk.Button(bb, text="Set the feeders", command=ok).pack(side="left", padx=4)
            ttk.Button(bb, text="Cancel", command=top.destroy).pack(side="left", padx=4)
            app["size_dialog"] = {"top": top, "rows": rows, "ok": ok}
            top.grab_set()
            root.wait_window(top)
            app["size_dialog"] = None

        def size_mpts(self):
            """Each MPT's MVA from the units on it (Unit MVA x Units, through shared collectors too), rounded up;
               three windings: its LV and TV windings' too -- shown first, set only for the MPTs ticked. A Rate A
               or Z base given as the old MVA is cleared (blank: it follows the MVA)."""
            self.close_edit(True)
            f = lambda x: "%.6g" % x
            loads = mpt_loads(data)
            top, tab, bb = self.sizing_dialog(
                "Size the MPTs from the generators",
                "Each MPT's MVA from the units on it (their Unit MVA x Units; through shared collectors too), rounded "
                "up -- over its parallel units; three windings: its LV and TV windings' from the units on each. "
                "Change any value; the ticked MPTs are set. Or leave an MPT's MVA blank: the build takes the units' "
                "MVA, and it follows any change to the generators.",
                ("MPT", "The units on it", "MVA now", "MVA", "LV MVA", "TV MVA", "Then"))
            rows = []
            for i, r in enumerate(self.rows):
                nm = _txt(r.get("name"))
                if not nm or nm.startswith("#"):
                    continue
                k = len(rows) + 1
                e = loads.get(nm.upper()) or {"lv": 0.0, "tv": 0.0, "gens": []}
                try:
                    cnt = _int(r, "count", nm, 1, minimum=1)
                    now = _num(r, "mva", nm, None, positive=True)
                    three = _windings(r, nm) == 3 or (_windings(r, nm) is None and _txt(r.get("tv_kv")) is not None)
                except InputError as x:
                    ttk.Label(tab, text=nm).grid(row=k, column=0, sticky="w", padx=4)
                    ttk.Label(tab, text="does not read yet: %s" % str(x).split(": ", 1)[-1], style="Req.TLabel",
                              wraplength=600).grid(row=k, column=1, columnspan=6, sticky="w", padx=4)
                    rows.append(None)
                    continue
                tot = e["lv"] + e["tv"]
                use = tk.BooleanVar(value=bool(tot) and now is not None and (now * cnt > 2.0 * tot or
                                                                             tot > 1.02 * now * cnt))
                ttk.Checkbutton(tab, text=nm, variable=use).grid(row=k, column=0, sticky="w", padx=4, pady=2)
                ttk.Label(tab, text=("%s = %s MVA" % (" + ".join("%s %s%s" % (g, f(x), " (TV)" if wd == "TV" else "")
                                                              for g, x, wd in e["gens"]), f(tot))) if tot else
                          "no generator on it", wraplength=260, justify="left").grid(row=k, column=1, sticky="w",
                                                                                     padx=4)
                ttk.Label(tab, text=("%d x %s" % (cnt, f(now)) if cnt > 1 else f(now)) if now else "blank").grid(
                    row=k, column=2, sticky="w", padx=4)
                mva = tk.StringVar(value=f(nice_mva(tot / cnt)) if tot else (_txt(r.get("mva")) or ""))
                lv = tk.StringVar(value=(f(nice_mva(e["lv"] / cnt)) if e["lv"] else (_txt(r.get("lv_mva")) or ""))
                                  if three else "")
                tv = tk.StringVar(value=(f(nice_mva(e["tv"] / cnt)) if e["tv"] else (_txt(r.get("tv_mva")) or ""))
                                  if three else "")
                ttk.Entry(tab, textvariable=mva, width=9).grid(row=k, column=3, sticky="w", padx=4)
                for c, v in ((4, lv), (5, tv)):
                    ttk.Entry(tab, textvariable=v, width=8, state="normal" if three else "disabled").grid(
                        row=k, column=c, sticky="w", padx=4)
                then = tk.StringVar()
                ttk.Label(tab, textvariable=then, wraplength=380, justify="left").grid(row=k, column=6, sticky="w",
                                                                                      padx=4)
                row = {"i": i, "name": nm, "cnt": cnt, "now": now, "tot": tot, "three": three, "use": use,
                       "mva": mva, "lv": lv, "tv": tv, "then": then}

                def show(*_a, row=row):
                    try:
                        m_ = float(row["mva"].get())
                    except ValueError:
                        return row["then"].set("MVA: a number (or untick it)")
                    cap = m_ * row["cnt"]
                    r_ = self.rows[row["i"]]
                    follow = [lab for key, lab in (("rate_mva", "Rate A"), ("base_hl_mva", "Z HV-LV base"),
                                                   ("base_ht_mva", "Z HV-TV base"), ("base_lt_mva", "Z LV-TV base"))
                              if row["now"] and _txt(r_.get(key)) and _same(r_.get(key), row["now"])]
                    keep = [lab for key, lab in (("rate_b_mva", "Rate B"), ("rate_c_mva", "Rate C"))
                            if _txt(r_.get(key))]
                    row["then"].set("%s MVA%s%s%s%s" % (
                        f(cap), (" for %s MVA of units: %.0f %%" % (f(row["tot"]), 100.0 * row["tot"] / cap))
                        if row["tot"] and cap else "",
                        (" (%d x %s)" % (row["cnt"], f(m_))) if row["cnt"] > 1 else "",
                        ("; %s as the old MVA: cleared, it follows" % ", ".join(follow)) if follow else "",
                        ("; %s kept as given" % ", ".join(keep)) if keep else ""))
                mva.trace_add("write", show) if hasattr(mva, "trace_add") else mva.trace("w", show)
                show()
                rows.append(row)

            def ok():
                done, bad = [], []
                for row in rows:
                    if row is None or not row["use"].get():
                        continue
                    try:
                        vals = [float(row[k].get()) if row[k].get().strip() else None for k in ("mva", "lv", "tv")]
                        if vals[0] is None or any(v is not None and not v > 0 for v in vals):
                            raise ValueError
                    except ValueError:
                        bad.append(row["name"])
                        continue
                    r = self.rows[row["i"]]
                    for key in ("rate_mva", "base_hl_mva", "base_ht_mva", "base_lt_mva"):   # they follow the MVA
                        if row["now"] and _txt(r.get(key)) and _same(r.get(key), row["now"]):
                            r.pop(key)
                    r["mva"] = f(vals[0])
                    if row["three"]:
                        for key, v in (("lv_mva", vals[1]), ("tv_mva", vals[2])):
                            if v is None:
                                r.pop(key, None)
                            else:
                                r[key] = f(v)
                    done.append("%s %s MVA" % (row["name"], f(vals[0])))
                if bad:
                    return messagebox.showerror("Size MPTs", "MVA (above 0) for %s -- or untick it" % ", ".join(bad),
                                                parent=top)
                top.destroy()
                if done:
                    self.refresh(self.current())
                    dirty()
                set_status(("MPTs set: %s" % "; ".join(done)) if done else "MPTs: nothing ticked, nothing changed")
            ttk.Button(bb, text="Set the ticked MPTs", command=ok).pack(side="left", padx=4)
            ttk.Button(bb, text="Cancel", command=top.destroy).pack(side="left", padx=4)
            app["size_dialog"] = {"top": top, "rows": rows, "ok": ok}
            top.grab_set()
            root.wait_window(top)
            app["size_dialog"] = None

    for sheet, key, cols in TABLES:
        app["grids"][key] = Grid(sheet, key, cols)
    app["grids"]["gsus"] = Grid("Generators", "generators", GEN_COLS, page="GSUs", fixed=True)

    # ---------------------------------------------------------------- Dyr_Params: every value, editable
    def dyr_all(grid):
        """Above the Dyr_Params rows: every model's parameters as the .dyr gets them -- a column per generator (its
           machines together), IB and PLANT -- each value editable: a change is a Dyr_Params row, a cleared one
           goes back to the default."""
        top = ttk.Frame(grid.tab)
        top.pack(fill="both", expand=True, before=grid.tools)
        bar = ttk.Frame(top, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=lambda: fill()).pack(side="left", padx=2)
        ttk.Label(bar, text="Search:").pack(side="left", padx=(12, 2))
        find = tk.StringVar()
        ttk.Entry(bar, textvariable=find, width=24).pack(side="left")
        ttk.Label(bar, text="Every model's parameters as the .dyr gets them. Double-click a value to change it "
                            "(* = your value, kept in the rows below); clear it to go back to the default.",
                  style="Hint.TLabel").pack(side="left", padx=12)
        state = {"cols": [], "rows": [], "edit": None}
        msg = tk.StringVar()
        ttk.Label(top, textvariable=msg, style="Help.TLabel", wraplength=1200, justify="left").pack(fill="x")
        box = ttk.Frame(top)
        box.pack(fill="both", expand=True)
        tv = ttk.Treeview(box, show="headings", selectmode="browse")
        ys = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        xs = ttk.Scrollbar(box, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        tv.tag_configure("sec", background=light, font=("Segoe UI", 9, "bold"))
        tv.tag_configure("set", background="#FFF2CC")
        ttk.Label(grid.tab, text="Your values (saved in the project) -- the rows the table above writes:",
                  style="Item.TLabel").pack(fill="x", padx=4, pady=(6, 0), before=grid.tools)
        grid.box.pack_configure(expand=False, fill="x")
        grid.tree.configure(height=6)
        tv.bind("<<TreeviewOpen>>", lambda e: None)

        def fill(*_a):
            close(False)
            try:
                cols, rows, notes = dyr_table(read_input(save_temp()))
            except (InputError, SolveError) as e:
                msg.set("The inputs do not build yet, so the values cannot be shown: %s" % e)
                tv.delete(*tv.get_children())
                return
            state["cols"], state["rows"] = cols, rows
            heads = ["model", "param", "pos"] + ["g%d" % i for i in range(len(cols))] + ["doc"]
            tv.configure(columns=heads)
            for h, t, wd in zip(heads, ["Model", "Parameter", "Position"] + cols + ["Description"],
                                [80, 100, 70] + [110] * len(cols) + [700]):
                tv.heading(h, text=t)
                tv.column(h, width=wd, minwidth=50, stretch=(h == "doc"), anchor="w")
            tv.delete(*tv.get_children())
            f = find.get().strip().lower()
            last = None
            for model, p, pos, cells in rows:
                doc = _param_doc(p, model)
                if f and not any(f in str(x).lower() for x in [model, p, pos, doc] + [c[0] for c in cells.values()]):
                    continue
                if model != last:
                    tv.insert("", "end", iid="m|" + model, values=[model, MODEL_DOC.get(model, "")] + [""] * (
                        len(cols) + 2), tags=("sec",))
                    last = model
                vals = [cells[c][0] + (" *" if cells[c][1] == "set" else " ~" if cells[c][1] == "varies" else "")
                        if c in cells else "" for c in cols]
                tv.insert("", "end", iid="p|%s|%s" % (model, p), values=[model, p, pos] + vals + [doc],
                          tags=("set",) if any(c[1] == "set" for c in cells.values()) else ())
            msg.set("%d parameters of %d models for %s.%s%s" % (
                len(rows), len(set(r[0] for r in rows)), ", ".join(cols),
                "  ~ = differs between the DETAILED machines (Machines)." if any(
                    c[1] == "varies" for r in rows for c in r[3].values()) else "",
                ("  " + " ".join(notes)) if notes else ""))

        def close(keep):
            ed = state["edit"]
            if not ed:
                return
            state["edit"] = None
            w, var, model, p, col = ed
            text = var.get().strip()
            w.destroy()
            if keep:
                put(model, p, col, text)

        def put(model, p, col, text):
            row = next((r for r in state["rows"] if r[0] == model and r[1] == p), None)
            now = row[3].get(col) if row else None
            if now is None:
                return
            if text and p != "CKT":
                try:
                    float(text)
                except ValueError:
                    messagebox.showerror("Dyr_Params", "%s %s: %r is not a number" % (model, p, text), parent=root)
                    return
            rows = data.setdefault("dyr_params", [])
            same = [r for r in rows if str(r.get("generator") or "").strip().upper() == col.upper() and
                    str(r.get("model") or "").strip().upper() == model and
                    dyr_param_name(model, r.get("parameter")) == p]
            if not text:
                if not same:
                    return
                for r in same:
                    rows.remove(r)
            elif same:
                if same[0].get("value") == text and len(same) == 1:
                    return
                same[0]["value"] = text
                for r in same[1:]:
                    rows.remove(r)
            elif text == now[0] and now[1] != "set":
                return
            else:
                rows.append({"generator": col, "model": model, "parameter": p, "value": text})
            grid.refresh()
            dirty()
            fill()
            iid = "p|%s|%s" % (model, p)
            if tv.exists(iid):
                tv.selection_set(iid)
                tv.see(iid)
            set_status("%s %s %s: %s" % (col, model, p, text or "back to its default"))

        def edit(iid, colid):
            close(True)
            if not iid.startswith("p|"):
                return
            ci = int(colid.lstrip("#")) - 1 - 3
            if not 0 <= ci < len(state["cols"]):
                return
            _x, model, p = iid.split("|")
            col = state["cols"][ci]
            row = next((r for r in state["rows"] if r[0] == model and r[1] == p), None)
            if row is None:
                return
            if col not in row[3]:
                set_status("%s has no %s (not in its model chain)" % (col, model))
                return
            tv.see(iid)
            tv.update_idletasks()
            bbox = tv.bbox(iid, colid)
            if not bbox:
                return
            x, y, wd, ht = bbox
            text, how = row[3][col]
            var = tk.StringVar(value="" if how == "varies" else text)
            w = tk.Entry(tv, textvariable=var, relief="solid", borderwidth=1, background="white")
            w.place(x=x, y=y, width=max(wd, 90), height=ht)
            w.focus_set()
            w.select_range(0, "end")
            state["edit"] = (w, var, model, p, col)
            w.bind("<Return>", lambda e: close(True))
            w.bind("<KP_Enter>", lambda e: close(True))
            w.bind("<Escape>", lambda e: close(False))
            w.bind("<FocusOut>", lambda e: close(True))

        tv.bind("<Double-1>", lambda e: edit(tv.identify_row(e.y), tv.identify_column(e.x)))
        tv.bind("<Button-1>", lambda e: close(True), add="+")
        find.trace_add("write", fill) if hasattr(find, "trace_add") else find.trace("w", fill)

        def shown(_e=None):
            try:
                if nb.tab(nb.select(), "text") == grid.sheet:
                    fill()
            except tk.TclError:
                pass
        nb.bind("<<NotebookTabChanged>>", shown, add="+")
        app["dyr_all"] = {"fill": fill, "edit": edit, "close": close, "put": put, "tree": tv, "state": state,
                          "msg": msg}

    dyr_all(app["grids"]["dyr_params"])

    # ---------------------------------------------------------------- PSCAD_Params: each UDM's parameters, editable
    def pscad_all(grid):
        """Above the PSCAD_Params rows: each generator's vendor model (UDM) parameters, read from its definition,
           a column per generator -- each value editable (a choice from its list): a change is a PSCAD_Params row,
           a cleared one goes back to the model's own value."""
        top = ttk.Frame(grid.tab)
        top.pack(fill="both", expand=True, before=grid.tools)
        bar = ttk.Frame(top, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=lambda: fill()).pack(side="left", padx=2)
        ttk.Label(bar, text="Definition:").pack(side="left", padx=(12, 2))
        which = tk.StringVar()
        pick = ttk.Combobox(bar, textvariable=which, state="readonly", width=44)
        pick.pack(side="left")
        pick.bind("<<ComboboxSelected>>", lambda _e: fill())
        ttk.Button(bar, text="Choose...", command=lambda: choose()).pack(side="left", padx=(4, 0))
        lib = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="library parts too", variable=lib, command=lambda: fill()).pack(side="left", padx=8)
        ttk.Label(bar, text="Search:").pack(side="left", padx=(8, 2))
        find = tk.StringVar()
        ttk.Entry(bar, textvariable=find, width=18).pack(side="left")
        ttk.Label(bar, text="Double-click a value to change it (* = yours, kept in the rows below); clear it to go "
                            "back to the model's own.", style="Hint.TLabel").pack(side="left", padx=10)
        state = {"cols": [], "secs": [], "edit": None}
        msg = tk.StringVar()
        ttk.Label(top, textvariable=msg, style="Help.TLabel", wraplength=1200, justify="left").pack(fill="x")
        box = ttk.Frame(top)
        box.pack(fill="both", expand=True)
        tv = ttk.Treeview(box, show="headings", selectmode="browse")
        ys = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        xs = ttk.Scrollbar(box, orient="horizontal", command=tv.xview)
        tv.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tv.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        tv.tag_configure("sec", background=light, font=("Segoe UI", 9, "bold"))
        tv.tag_configure("set", background="#FFF2CC")
        ttk.Label(grid.tab, text="Your values (saved in the project) -- the rows the table above writes:",
                  style="Item.TLabel").pack(fill="x", padx=4, pady=(6, 0), before=grid.tools)
        grid.box.pack_configure(expand=False, fill="x")
        grid.tree.configure(height=6)

        def entry(iid):
            if not iid.startswith("p|"):
                return None, None
            _x, k, name = iid.split("|", 2)
            sec = state["secs"][int(k)]
            return sec, dict(sec["params"]).get(name)

        def fill(*_a):
            close(False)
            cols, secs, notes = udm_table(data, lib.get())
            state["cols"], state["secs"], state["all"] = cols, secs, [x["label"] for x in secs]
            labels = udm_shown(secs)
            state["labels"] = labels
            pick.configure(values=(["All"] + labels) if labels else [])
            if which.get() not in ["All"] + labels:
                which.set(labels[0] if labels else "")
            heads = ["param", "what"] + ["g%d" % i for i in range(len(cols))] + ["doc"]
            tv.configure(columns=heads)
            for h, t, wd in zip(heads, ["Parameter", "Description"] + cols + ["Type, range or choices"],
                                [150, 190] + [160] * len(cols) + [700]):
                tv.heading(h, text=t)
                tv.column(h, width=wd, minwidth=50, stretch=(h == "doc"), anchor="w")
            tv.delete(*tv.get_children())
            f = find.get().strip().lower()
            n = 0
            for k, sec in enumerate(secs):
                if sec["label"] not in labels or which.get() not in ("All", sec["label"]):
                    continue
                rows = [(nm, e) for nm, e in sec["params"] if not f or any(
                    f in str(x).lower() for x in [nm, e["p"]["desc"], udm_doc(e["p"])] +
                    [c[0] for c in e["cells"].values()])]
                if not rows:
                    continue
                users = [c for c in cols if any(c in e["cells"] for _nm, e in sec["params"])]
                tv.insert("", "end", iid="s|%d" % k, values=[sec["defn"], ""] + [""] * len(cols) + [
                    "%s -- from %s, for %s%s" % (sec["label"], sec["src"], ", ".join(users), (
                        "; a change gives a generator its own copy of the UDM's definition" if sec["part"] else ""))],
                    tags=("sec",))
                for nm, e in rows:
                    vals = []
                    for c in cols:
                        v, how = e["cells"].get(c, ("", None))
                        vals.append("" if how is None else (udm_choice(e["p"], v) if e["p"]["choices"] else v) +
                                    (" *" if how == "set" else ""))
                    tv.insert("", "end", iid="p|%d|%s" % (k, nm), values=[e["p"]["name"], e["p"]["desc"]] + vals +
                              [udm_doc(e["p"])], tags=("set",) if any(
                                  c[1] == "set" for c in e["cells"].values()) else ())
                    n += 1
            msg.set(("%d parameters shown -- %d definition(s) and parts, for %s." % (n, len(secs), ", ".join(cols))
                     if cols else
                     "No generator has a vendor model (UDM) yet: Generators, Pick UDM from OEM model... -- its "
                     "parameters show here.") + ("  " + "  ".join(notes) if notes else ""))

        def choose():
            """Which definitions and parts the Definition list shows -- ticked, remembered."""
            close(True)
            if not state.get("all"):
                set_status("No vendor model (UDM) yet: Generators, Pick UDM from OEM model...")
                return
            top = tk.Toplevel(root)
            top.title("Vendor model pages to list")
            top.transient(root)
            ttk.Label(top, text="Tick what the Definition list shows (a vendor model, the parts inside it, its "
                                "signals). Your choice is remembered.", wraplength=520, justify="left").pack(
                fill="x", padx=12, pady=(10, 6))
            box2 = ttk.Frame(top)
            box2.pack(fill="both", expand=True, padx=12)
            cv = tk.Canvas(box2, height=360, width=520, highlightthickness=0, background="white")
            sb2 = ttk.Scrollbar(box2, orient="vertical", command=cv.yview)
            inner = tk.Frame(cv, background="white")
            inner.bind("<Configure>", lambda _e: cv.configure(scrollregion=cv.bbox("all")))
            cv.create_window((0, 0), window=inner, anchor="nw")
            cv.configure(yscrollcommand=sb2.set)
            cv.pack(side="left", fill="both", expand=True)
            sb2.pack(side="left", fill="y")
            ticks = collections.OrderedDict()
            mine = set(x["label"] for x in state["secs"] if udm_has_set(x))
            for lab in state["all"]:
                v = tk.BooleanVar(value=lab in state["labels"])
                ticks[lab] = v
                depth = lab.count("  >  ")
                tk.Checkbutton(inner, text=(lab.split("  >  ")[-1] if depth else lab) + (
                    "   (holds values of yours: always listed)" if lab in mine else ""), variable=v, anchor="w",
                               background="white").pack(fill="x", padx=(6 + 22 * depth, 6))
            row = ttk.Frame(top)
            row.pack(fill="x", padx=12, pady=10)

            sec_of = dict((x["label"], x) for x in state["secs"])

            def setall(f):
                for lab, v in ticks.items():
                    v.set(f(sec_of[lab]))

            def done():
                was = gui_prefs().get("pscad_pages")
                now = dict(was) if isinstance(was, dict) else {}
                now.update((lab, bool(v.get())) for lab, v in ticks.items())
                gui_prefs({"pscad_pages": now})
                top.destroy()
                fill()
            for txt, f in (("Defaults", udm_default_shown), ("All", lambda _x: True), ("None", lambda _x: False)):
                ttk.Button(row, text=txt, command=lambda f=f: setall(f)).pack(side="left", padx=2)
            ttk.Button(row, text="OK", command=done).pack(side="right", padx=2)
            ttk.Button(row, text="Cancel", command=top.destroy).pack(side="right", padx=2)
            state["chooser"] = (top, ticks, done)

        def close(keep):
            ed = state["edit"]
            if not ed:
                return
            state["edit"] = None
            w, var, iid, col = ed
            text = var.get().strip()
            w.destroy()
            if keep:
                put(iid, col, text)

        def put(iid, col, text):
            sec, e = entry(iid)
            if e is None or col not in e["cells"]:
                return
            p, key = e["p"], e["key"]
            rows = data.setdefault("pscad_params", [])
            same = [r for r in rows if (_txt(r.get("generator")) or "").upper() == col.upper() and
                    (_txt(r.get("parameter")) or "").upper() == key.upper()]
            now, how = e["cells"][col]
            if text:
                try:
                    text = udm_check(p, text, "%s %s" % (col, key), part="/" in key)
                except InputError as ex:
                    messagebox.showerror("PSCAD_Params", str(ex), parent=root)
                    return
            if not text:
                if not same:
                    return
                for r in same:
                    rows.remove(r)
            elif same:
                if len(same) == 1 and _txt(same[0].get("value")) == text:
                    return
                same[0]["value"] = text
                for r in same[1:]:
                    rows.remove(r)
            elif text == now:
                return
            else:
                rows.append({"generator": col, "parameter": key, "value": text})
            grid.refresh()
            dirty()
            fill()
            if tv.exists(iid):
                tv.selection_set(iid)
                tv.see(iid)
            set_status("%s %s: %s" % (col, key, (udm_choice(p, text) if p["choices"] else text) if text else
                                      "back to the model's own"))

        def edit(iid, colid):
            close(True)
            sec, e = entry(iid)
            if e is None:
                return
            ci = int(colid.lstrip("#")) - 1 - 2
            if not 0 <= ci < len(state["cols"]):
                return
            col = state["cols"][ci]
            if col not in e["cells"]:
                set_status("%s's vendor model has no %s" % (col, e["p"]["name"]))
                return
            tv.see(iid)
            tv.update_idletasks()
            bbox = tv.bbox(iid, colid)
            if not bbox:
                return
            x, y, wd, ht = bbox
            v, _how = e["cells"][col]
            if e["p"]["choices"]:
                var = tk.StringVar(value=udm_choice(e["p"], v))
                w = ttk.Combobox(tv, textvariable=var, values=[""] + e["p"]["choices"], state="readonly")
                w.bind("<<ComboboxSelected>>", lambda _e: close(True))
            else:
                var = tk.StringVar(value=v)
                w = tk.Entry(tv, textvariable=var, relief="solid", borderwidth=1, background="white")
                w.select_range(0, "end")
            w.place(x=x, y=y, width=max(wd, 150 if e["p"]["choices"] else 90), height=ht)
            w.focus_set()
            state["edit"] = (w, var, iid, col)
            w.bind("<Return>", lambda _e: close(True))
            w.bind("<KP_Enter>", lambda _e: close(True))
            w.bind("<Escape>", lambda _e: close(False))
            if not e["p"]["choices"]:
                w.bind("<FocusOut>", lambda _e: close(True))

        tv.bind("<Double-1>", lambda ev: edit(tv.identify_row(ev.y), tv.identify_column(ev.x)))
        tv.bind("<Button-1>", lambda ev: close(True), add="+")
        find.trace_add("write", fill) if hasattr(find, "trace_add") else find.trace("w", fill)

        def shown(_e=None):
            try:
                if nb.tab(nb.select(), "text") == grid.sheet:
                    fill()
            except tk.TclError:
                pass
        nb.bind("<<NotebookTabChanged>>", shown, add="+")
        app["pscad_all"] = {"fill": fill, "edit": edit, "close": close, "put": put, "tree": tv, "state": state,
                            "msg": msg, "which": which, "pick": pick, "lib": lib, "choose": choose}

    pscad_all(app["grids"]["pscad_params"])

    # ---------------------------------------------------------------- reference tabs
    def ref_tab(title, heads, widths, rows, sections=False, extra=None):
        tab = ttk.Frame(nb)
        nb.add(tab, text=title)
        top = ttk.Frame(tab, padding=(4, 4))
        top.pack(fill="x")
        ttk.Label(top, text="Search:").pack(side="left")
        find = tk.StringVar()
        ttk.Entry(top, textvariable=find, width=40).pack(side="left", padx=4)
        if extra:
            extra(top)
        box = ttk.Frame(tab)
        box.pack(fill="both", expand=True)
        tv = ttk.Treeview(box, columns=heads, show="headings")
        ys = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=ys.set)
        tv.pack(side="left", fill="both", expand=True)
        ys.pack(side="right", fill="y")
        for h, wd in zip(heads, widths):
            tv.heading(h, text=h)
            tv.column(h, width=wd, stretch=(h == heads[-1]))
        tv.tag_configure("sec", background=light, font=("Segoe UI", 9, "bold"))

        def fill(*_a):
            tv.delete(*tv.get_children())
            f = find.get().strip().lower()
            for r in rows:
                if r[0] == "#":
                    if not f:
                        tv.insert("", "end", values=r[1:], tags=("sec",))
                    continue
                if not f or any(f in str(x).lower() for x in r):
                    tv.insert("", "end", values=r)
        find.trace_add("write", fill) if hasattr(find, "trace_add") else find.trace("w", fill)
        fill()
        return tv

    rrows = []
    for key, item, unit, note, choices in PLANT_FIELDS:
        rrows.append(["Plant", item, key, unit, "required" if key == "poi_kv" else _blank_means(note),
                      " | ".join(choices or []), note, _used_by("Plant", key)])
    for sheet, key, cols in TABLES:
        for c in cols:
            note = (c[2] or "").lstrip("# ")
            rrows.append([sheet, c[1], c[0], "", _blank_means(note), " | ".join(c[3] or []), note,
                          _used_by(sheet, c[0])])
    for k, what in PSCAD_KEYS_DOC:
        rrows.append(["PSCAD_Params", k, "", "", "", "", what, "PSCAD"])
    ref_tab("Input_Reference", ["Sheet", "Field", "Key", "Unit", "Blank means", "Choices", "Description", "Used by"],
            [95, 170, 120, 50, 140, 160, 520, 100], rrows)

    drows = []
    for model, (hdr, nic, names) in DYR_LIB.items():
        drows.append(["#", model, "", "", "", "", MODEL_DOC.get(model, "")])
        for k, nm in enumerate(names):
            drows.append([model, ("ICON%d" % (k + 1)) if k < nic else ("CON%d" % (k - nic + 1)), nm,
                          _txt(DYN_DEFAULTS[model].get(nm)) or "", "auto" if nm in AUTO_PARAMS.get(model, ()) else "",
                          _param_doc(nm, model)])

    def dm_extra(top):
        ttk.Button(top, text="Add the selected parameter to Dyr_Params", command=lambda: add_param()).pack(
            side="left", padx=12)
    dm_tv = ref_tab("Dynamic_Models", ["Model", "Position", "Parameter", "Default", "Set by the script",
                                       "Description"], [110, 80, 110, 80, 110, 600], drows, extra=dm_extra)

    def add_param():
        sel = dm_tv.selection()
        if not sel:
            return messagebox.showinfo("Dyr_Params", "Select a parameter first.", parent=root)
        v = dm_tv.item(sel[0], "values")
        if not v or v[0] not in DYR_LIB:
            return
        who = gen_names()[:1] or ["PLANT"]
        if v[0] == "PLNTBU1":
            who = ["PLANT"]
        elif v[0] == "GENCLS":
            who = ["IB"]
        g = app["grids"]["dyr_params"]
        g.rows.append({"generator": who[0], "model": v[0], "parameter": v[2], "value": v[3]})
        g.refresh(len(g.rows) - 1)
        dirty()
        nb.select(nb.tabs()[[nb.tab(t, "text") for t in nb.tabs()].index("Dyr_Params")])
        set_status("added %s %s to Dyr_Params -- set its Generator and Value" % (v[0], v[2]))

    guide = ttk.Frame(nb)
    nb.add(guide, text="README")
    gt = tk.Text(guide, wrap="word", font=("Segoe UI", 10), padx=12, pady=10, relief="flat")
    gs = ttk.Scrollbar(guide, orient="vertical", command=gt.yview)
    gt.configure(yscrollcommand=gs.set)
    gt.pack(side="left", fill="both", expand=True)
    gs.pack(side="right", fill="y")
    gt.tag_configure("h", font=("Segoe UI", 11, "bold"), foreground=navy, spacing1=8)
    gt.insert("end", "USING THIS WINDOW\n", "h")
    gt.insert("end", "Each tab is a sheet of the input workbook. Plant: one value per item. The other tabs: "
                     "one row per item -- Add row, then double-click a cell (or select it and press Enter) to "
                     "type; Tab moves to the next cell, Enter to the row below, Esc cancels. A blank cell takes "
                     "the default shown in the help line. Model type (toolbar or Plant) chooses SMIB or DETAILED "
                     "for PSS/E and PSCAD alike.\n"
                     "Save the project (.json), then Build models: the .raw, .dyr, report and PSCAD build script "
                     "go to Models beside the project: Detailed_Models or Equivalent_Models (SMIB) in it. "
                     "Excel is optional: Open a workbook you have (it is "
                     "imported), or export the project as one (File menu).\n")
    for topic, text in README:
        if topic:
            gt.insert("end", topic + "\n", "h")
        gt.insert("end", text + "\n")
    gt.configure(state="disabled")

    out_tab = ttk.Frame(nb)
    nb.add(out_tab, text="Output")
    ot = tk.Text(out_tab, wrap="none", font=("Consolas", 9), background="#FBFBFB")
    oy = ttk.Scrollbar(out_tab, orient="vertical", command=ot.yview)
    ox = ttk.Scrollbar(out_tab, orient="horizontal", command=ot.xview)
    ot.configure(yscrollcommand=oy.set, xscrollcommand=ox.set)
    ot.grid(row=0, column=0, sticky="nsew")
    oy.grid(row=0, column=1, sticky="ns")
    ox.grid(row=1, column=0, sticky="ew")
    out_tab.rowconfigure(0, weight=1)
    out_tab.columnconfigure(0, weight=1)
    ot.tag_configure("bad", foreground="#C00000")
    ot.tag_configure("note", foreground="#7F6000")

    def out(text):
        for ln in text.splitlines(True):
            tag = "bad" if ("***" in ln or "CHECK" in ln or "Error" in ln) else ("note" if "NOTE" in ln else "")
            ot.insert("end", ln, tag)
        ot.see("end")

    # ---------------------------------------------------------------- the data in and out of the widgets
    def to_widgets():
        for key, var in app["vars"].items():
            var.set(data["plant"].get(key) or ("YES" if key in ["out_" + k for k, _w in OUT_KINDS] else ""))
        for g in app["grids"].values():
            g.refresh(0 if g.rows else None)
        app["dirty"] = False

    def from_widgets():
        for g in app["grids"].values():
            g.close_edit(True)
        data["plant"] = dict((k, v.get().strip()) for k, v in app["vars"].items() if v.get().strip())

    def load(inp, path, what):
        new = gui_data(inp)
        data.clear()
        data.update(new)
        app["path"] = path
        to_widgets()
        root.title("Plant Model Builder -- %s" % (data["plant"].get("name") or auto_name(data)))
        apply_view()
        set_status(what)

    def save_temp():
        from_widgets()
        d = tempfile.gettempdir()
        p = os.path.join(d, "smib_check_inputs.json")
        _replace_file(p, lambda t: _write_json(t, gui_save_data(data)))
        return p

    def ask_save_changes():
        if not app["dirty"]:
            return True
        a = messagebox.askyesnocancel("Unsaved changes", "Save the changes to the project first?", parent=root)
        if a is None:
            return False
        return save() if a else True

    def save(as_new=False):
        from_widgets()
        path = app["path"] if app["path"] and app["path"].lower().endswith(".json") and not as_new else None
        if not path:
            path = filedialog.asksaveasfilename(parent=root, title="Save the project", defaultextension=".json",
                                                initialdir=os.path.dirname(app["path"] or "") or here,
                                                initialfile="%s.json" % (data["plant"].get("name") or "SMIB_PROJECT"),
                                                filetypes=[("SMIB project", "*.json")])
            if not path:
                return False
        _replace_file(path, lambda t: _write_json(t, gui_save_data(data)))
        app["path"], app["dirty"] = path, False
        set_status("saved")
        return True

    def open_any(path=None):
        if not ask_save_changes():
            return
        path = path or filedialog.askopenfilename(parent=root, title="Open a project or a workbook", initialdir=here,
                                                  filetypes=[("SMIB project or workbook", "*.json *.xlsx *.xlsm"),
                                                             ("SMIB project", "*.json"), ("Workbook", "*.xlsx *.xlsm")])
        if not path:
            return
        try:
            inp = read_input(path, keep_comments=True)
        except Exception as e:
            return messagebox.showerror("Open", "%s:\n\n%s" % (path, e), parent=root)
        load(inp, path if path.lower().endswith(".json") else None,
             "opened %s" % path if path.lower().endswith(".json")
             else "imported %s -- Save makes it a project (.json)" % path)
        for m in inp.get("_said", []):
            out("[smib] %s\n" % m)
            set_status(m)
        if inp.get("_unknown"):
            out("[smib] not used from %s: %s\n" % (path, "; ".join(inp["_unknown"])))
            messagebox.showwarning("Open", "%d item(s) in this file are not inputs of this version -- they are not "
                                   "used, and Save leaves them out:\n\n%s%s" % (
                                       len(inp["_unknown"]), "\n".join("- " + u for u in inp["_unknown"][:12]),
                                       "\n- ... (the Output tab lists them all)" if len(inp["_unknown"]) > 12
                                       else ""), parent=root)

    def new(example):
        if not ask_save_changes():
            return
        load(EXAMPLE if example else {"plant": {"model_type": "SMIB"}}, None,
             "a new project from the example plant" if example else "a new, empty project")

    def scale_up(*given):
        """A test plant from this one: MPTs, feeders per MPT, GSUs per feeder, inverters per GSU (asked for when not
           given; blank or 0 keeps what the plant has)."""
        from_widgets()
        src = gui_save_data(data)
        if not given:
            try:
                _d, now = scale_plant(src)
            except InputError as e:
                messagebox.showerror("Scale up", str(e), parent=root)
                return False
            top = tk.Toplevel(root)
            top.title("Scale up")
            top.transient(root)
            ttk.Label(top, text="A test plant from this one: copies of the first MPT with its generators. Blank keeps "
                                "what it has now:\n\n" + "\n".join(now[:3]), wraplength=520,
                      justify="left").grid(row=0, column=0, columnspan=2, padx=12, pady=(12, 8), sticky="w")
            vs = []
            for i, txt in enumerate(("Number of MPTs", "Feeders per MPT", "GSUs per feeder", "Inverters per GSU")):
                v = tk.StringVar()
                ttk.Label(top, text=txt).grid(row=i + 1, column=0, sticky="w", padx=12, pady=3)
                ttk.Entry(top, textvariable=v, width=16).grid(row=i + 1, column=1, sticky="w", padx=12)
                v.set("")
                vs.append(v)
            ttk.Label(top, text="Gen-tie (more than one MPT)").grid(row=5, column=0, sticky="w", padx=12, pady=3)
            vt = tk.StringVar(value="one per MPT")
            ttk.Combobox(top, textvariable=vt, values=["one per MPT", "one shared"], state="readonly",
                         width=14).grid(row=5, column=1, sticky="w", padx=12)
            ttk.Label(top, text="Feeders, GSUs and inverters: one number for every MPT, or one per MPT in turn -- "
                                "12, 8, 10 (the last one goes on for the rest). Gen-tie: each MPT a tie of its own "
                                "to the POI, or all of them on the plant's one gen-tie.", style="Hint.TLabel",
                      wraplength=520).grid(row=6, column=0, columnspan=2, padx=12, sticky="w")
            got = {}

            def ok():
                got["v"] = [v.get().strip() for v in vs]
                got["tie"] = "SHARED" if vt.get() == "one shared" else "PER_MPT"
                top.destroy()
            bb = ttk.Frame(top)
            bb.grid(row=7, column=0, columnspan=2, pady=12)
            ttk.Button(bb, text="Scale up", command=ok).pack(side="left", padx=4)
            ttk.Button(bb, text="Cancel", command=top.destroy).pack(side="left", padx=4)
            top.grab_set()
            root.wait_window(top)
            if "v" not in got:
                return False
            try:
                given = [scale_arg(x) for x in got["v"]] + [got["tie"]]
                if isinstance(given[0], list):
                    raise InputError("Scale up: one number of MPTs, please")
            except InputError as e:
                messagebox.showerror("Scale up", str(e), parent=root)
                return False
        try:
            d, did = scale_plant(src, *given)
        except InputError as e:
            messagebox.showerror("Scale up", str(e), parent=root)
            return False
        load(d, None, "scaled up -- not saved yet")
        app["dirty"] = True
        messagebox.showinfo("Scale up", "The inputs now have:\n\n" + "\n".join("- " + x for x in did) +
                            "\n\nSave makes them a new project (the one you had is not changed).", parent=root)
        return True

    def export_xlsx():
        from_widgets()
        path = filedialog.asksaveasfilename(parent=root, title="Export as a workbook", defaultextension=".xlsx",
                                            initialdir=here, initialfile="%s.xlsx" % (data["plant"].get("name") or
                                                                                      "SMIB_INPUT"),
                                            filetypes=[("Workbook", "*.xlsx")])
        if path:
            _replace_file(path, lambda t: write_template(t, gui_save_data(data)))
            set_status("exported %s" % path)

    # ---------------------------------------------------------------- check and build, on a thread
    def run(job, label):
        if app["running"]:
            return
        app["running"] = True
        for b in (b_check, b_build):
            b.state(["disabled"])
        nb.select(nb.tabs()[-1])
        out("\n==== %s  %s ====\n" % (label, time.strftime("%H:%M:%S")))

        def work():
            res = {"ok": False}
            try:
                with contextlib.redirect_stdout(_Queue(app["q"])):
                    res = job()
            except InputError as e:
                app["q"].put("[smib] *** input: %s\n" % e)
                res = {"ok": False, "error": str(e)}
            except SolveError as e:
                app["q"].put("[smib] *** load flow: %s\n" % e)
                res = {"ok": False, "error": str(e)}
            except Exception:
                app["q"].put(traceback.format_exc())
                res = {"ok": False, "error": "an error -- see the Output tab"}
            app["q"].put(("done", res))
        threading.Thread(target=work).start()

    def poll():
        try:
            while True:
                m = app["q"].get_nowait()
                if isinstance(m, tuple):
                    finish(m[1])
                else:
                    out(m)
        except queue.Empty:
            pass
        root.after(100, poll)

    def finish(res):
        app["running"] = False
        for b in (b_check, b_build):
            b.state(["!disabled"])
        if res.get("out"):
            app["out"] = res["out"]
            b_open.state(["!disabled"])
        if res.get("ok"):
            set_status(res.get("msg", "done"))
            if res.get("msg"):
                (messagebox.showwarning if res.get("warn") else messagebox.showinfo)(
                    "Plant Model Builder", res["msg"], parent=root)
        else:
            err = res.get("error", "")
            set_status("stopped: " + err[:120])
            goto(err)
            messagebox.showerror("Plant Model Builder", err or "stopped -- see the Output tab", parent=root)

    def goto(err):
        """Show the tab (and row) an input error names: 'Generators row 2 (BESS): ...', 'Plant: ...'."""
        m = re.match(r"^(\w+) row (\d+)", err or "")
        tabs = [nb.tab(t, "text") for t in nb.tabs()]
        g0 = app["grids"].get(TABLE_KEY.get(m.group(1))) if m else None
        if g0 is not None:                            # the page the column it names is on (a GSU's: GSUs)
            named = [c for c in g0.cols if c[1].lower() in err.lower()]
            best = max(named, key=lambda c: len(c[1]))[0] if named else None
            g0 = next((x for x in app["grids"].values() if x.key == g0.key and best in PAGE_COLS.get(x.page, ())),
                      g0) if best else g0
        page_of = g0.page if g0 is not None else (m.group(1) if m else None)
        if (m and m.group(1) in tabs and (not shown_view(page_of) or (g0 and any(
                c[1].lower() in err.lower() and not shown_view(g0.page, c[0]) and
                c[0] in PAGE_COLS.get(g0.page, g0.keys) for c in g0.cols)))) or \
                ((err or "").startswith("Plant") and any("'%s'" % f[1] in err and not shown_view("Plant", f[0])
                                                         for f in PLANT_FIELDS)):
            show_all()                                # the field it names is hidden: every field shown
        if m and m.group(1) in tabs:
            nb.select(g0.tab if g0 is not None else nb.tabs()[tabs.index(m.group(1))])
            g = g0
            if g:
                n = int(m.group(2)) - 1                   # the n-th row with something in it
                full = [i for i, r in enumerate(g.rows) if any(v for v in r.values()) and
                        not _skip([r.get(k) for k in g.keys])]          # as the build numbers them
                if 0 <= n < len(full):
                    g.refresh(full[n])
                named = [k for k, c in enumerate(g.cols) if c[1].lower() in err.lower()]
                g.col = max(named, key=lambda k: len(g.cols[k][1])) if named else 0
                g.tree.xview_moveto(0.0)
                g.reveal(g.col)
                g.show_help(g.keys[g.col])
        elif (err or "").startswith("Plant"):
            nb.select(nb.tabs()[0])
            hit = [f for f in PLANT_FIELDS if "'%s'" % f[1] in err] or \
                [f for f in PLANT_FIELDS if re.search(r"\b%s\b" % re.escape(f[1]), err.split(" -- ")[0])]
            if hit:                                   # the field it names (quoted, else the longest named)
                key = max(hit, key=lambda f: len(f[1]))[0]
                if not shown_view("Plant", key):
                    show_all()
                widgets[key].focus_set()
        else:                                         # 'Feeders: ...', 'Generators (collector ...)': its page
            m2 = re.match(r"^(\w+)\b", err or "")
            if m2 and m2.group(1) in tabs:
                if not shown_view(m2.group(1)):
                    show_all()
                nb.select(nb.tabs()[tabs.index(m2.group(1))])

    def check():
        p = save_temp()

        def job():
            msgs, notes, warns, unused = [], 0, [], []
            for x, inp in plant_cases(read_input(p)):        # each set where Plant fields give several values
                net = build_network(inp, Log())
                operating_point(net, Log())
                dyr_models(net, inp)                # Dyr_Params, Dyr_Records and PSCAD_Params checked too
                user_records(net, inp)
                pscad_spec(net, inp, pscad_model(net, inp))
                net.notes[:] = list(collections.OrderedDict.fromkeys(net.notes))
                s = net.op["s_grid"]
                for n in net.notes:
                    print("[smib] " + n)
                notes += len(net.notes)
                for n in net.notes:
                    if n.startswith(("WARNING", "CHECK")) and n not in warns:
                        warns.append(n)
                    elif "not used" in n and n not in unused:     # given, but not in this model: said here too
                        unused.append(n)
                msg = ("%sInputs read -- %s model: %d buses, %d machines; P at POI %.3f MW, Q %.3f MVAr, infinite bus "
                       "%.5f pu" % ("%s: " % x if x else "", net.model, len(net.bus), len(net.gens), s.real,
                                    s.imag, net.op["e"]))
                print("[smib] " + msg)
                msgs.append(msg)
            if warns or unused:                          # what to look at, in the dialog itself
                parts = [("%d thing(s) to check:" % len(warns), warns),
                         ("Given but not used (%d):" % len(unused), unused)]
                return {"ok": True, "warn": True, "msg": "\n\n".join("%s\n%s%s" % (
                    head, "\n".join("- " + w.split(": ", 1)[-1] for w in got[:8]),
                    "\n- ... %d more in the Output tab" % (len(got) - 8) if len(got) > 8 else "")
                    for head, got in parts if got) + "\n\n" + "\n".join(msgs)}
            return {"ok": True, "msg": "Inputs OK -- nothing to check.\n\n" + "\n".join(msgs) + (
                "\n%d note(s) in the Output tab" % notes if notes else "")}
        run(job, "CHECK")

    def build_models():
        if all(app["vars"]["out_" + k].get() == "NO" for k, _w in OUT_KINDS):
            messagebox.showinfo("Build models", "Nothing to build: tick %s or %s." % (
                ", ".join(w for _k, w in OUT_KINDS[:-1]), OUT_KINDS[-1][1]), parent=root)
            return
        if not save():
            return
        path = app["path"]

        def job():
            net, files = build(path, check=psse_var.get(), pscad=pscad_var.get())
            out_dir = files["dir"]
            made = [w for k, w in (("raw", "PSSE\\ .raw"), ("dyr", "PSSE\\ .dyr"), ("epc", "PSLF\\ .epc"),
                                   ("dyd", "PSLF\\ .dyd"), ("pscad_build", "PSCAD\\ model")) if files.get(k)]
            if files.get("cases"):                     # a set for each Grid SCR
                return {"ok": True, "out": os.path.dirname(out_dir), "msg": "Models written, %d sets:\n%s\n\n%s "
                        "model, %d machines: %s" % (len(files["cases"]), "\n".join(files["cases"]), net.model,
                                                     len(net.gens), ", ".join(made))}
            return {"ok": True, "out": out_dir, "msg": "Models written to\n%s\n\n%s model, %d machines: %s; the "
                    "report: %s" % (out_dir, net.model, len(net.gens), ", ".join(made),
                                    os.path.basename(files["report"]))}
        run(job, "BUILD")

    def open_out():
        p = app["out"]
        if not p:
            return
        if sys.platform.startswith("win"):
            os.startfile(p)                              # noqa
        else:
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", p])

    # ---------------------------------------------------------------- the whole plant, wherever it shows
    def live_totals():
        """The whole plant's MVA, P and Q (Plant tab), what each MPT carries (MPT tab), the Plant fields whose
           blank takes the generators' totals -- from the Generators rows as they are now."""
        line, mpts, tot = plant_totals(data)
        plant_total.set(line)
        g = app["grids"].get("mpt")
        if g is not None:
            g.total.set(("Generators on each MPT:   " + "   |   ".join(mpts)) if mpts else "")
        g = app["grids"].get("feeders")
        if g is not None:
            fl = feeder_lines(data)
            g.total.set(("Feeders on each MPT (Copies: identical feeders):\n" + "\n".join(fl)) if fl else "")
        for k, txt in (("scr_base_mw", "blank = %s MW, the generators' Pmax" % ("%.6g" % tot["pmax"])),
                       ("p_poi_mw", "blank = each generator at its dispatch: %s MW in all" % (
                           "%.6g" % tot["dispatch"])),
                       ("name", "blank = %s, from the generators" % auto_name(data))):
            if k in hints and not hints[k][2].startswith("Req"):
                hints[k][0].configure(text=txt)
        if "req_live" in app:                         # the SCR's short-circuit MVA: on the generators' Pmax
            app["req_live"]()

    def plant_type():
        """What kinds of generator the plant has -- PV only, BESS only, or a hybrid of any (set_kinds): ticked,
           a kind it does not have gets a generator; unticked, a kind goes with every row naming its generators
           (asked first). Its name: from the generators unless one of your own is typed."""
        from_widgets()
        have = plant_kinds(data)
        top = tk.Toplevel(root)
        top.title("Plant type")
        top.transient(root)
        ttk.Label(top, text="What kinds of generator the plant has -- one kind (PV only, BESS only ...) or a hybrid "
                            "of any of them. A kind ticked that it does not have gets a generator: an inverter on the "
                            "first MPT, its feeders like the plant's; a synchronous one at HV behind its own GSU -- "
                            "then set its units and data on the Generators page. A kind unticked goes, with every "
                            "row naming its generators (asked first).", wraplength=640, justify="left").pack(
            fill="x", padx=10, pady=(10, 6))
        pre = ttk.Frame(top)
        pre.pack(fill="x", padx=10)
        box = ttk.Frame(top)
        box.pack(fill="x", padx=10, pady=4)
        vs = collections.OrderedDict()
        for k, (short, what, _types) in KINDS.items():
            v = tk.BooleanVar(value=k in have)
            vs[k] = v
            ttk.Checkbutton(box, text=what, variable=v).grid(row=len(vs), column=0, sticky="w", pady=1)
            ttk.Label(box, text=("%s: %s MW" % (", ".join(have[k][0]), "%.6g" % have[k][1])) if k in have else
                      "none yet", style="Hint.TLabel").grid(row=len(vs), column=1, sticky="w", padx=12)
        for txt, ks in (("PV only", ("PV",)), ("BESS only", ("BESS",)), ("PV + BESS", ("PV", "BESS")),
                        ("PV + BESS + SYNC", ("PV", "BESS", "SYNC")), ("Wind + BESS", ("WT4", "BESS"))):
            ttk.Button(pre, text=txt, command=lambda ks=ks: [v.set(k in ks) for k, v in vs.items()]).pack(
                side="left", padx=2)
        nf = ttk.Frame(top)
        nf.pack(fill="x", padx=10, pady=4)
        ttk.Label(nf, text="Name").pack(side="left")
        name = tk.StringVar(value=_txt(data["plant"].get("name")) or auto_name(data))
        ttk.Entry(nf, textvariable=name, width=36).pack(side="left", padx=6)
        ttk.Label(nf, text="(left as the one from the generators, it follows them)", style="Hint.TLabel").pack(
            side="left")
        then = tk.StringVar()
        ttk.Label(top, textvariable=then, wraplength=640, justify="left").pack(fill="x", padx=10, pady=6)
        last = {"auto": auto_name(data)}

        def show(*_a):
            ks = [k for k, v in vs.items() if v.get()]
            d2, did = set_kinds(data, ks)
            a = auto_name(d2)
            if name.get().strip() in ("", last["auto"]):          # not one of the user's own: it follows
                name.set(a)
            last["auto"] = a
            then.set(("Then %s:\n" % plant_kind_text(d2) + "\n".join("- " + x for x in did)) if did else
                     "Nothing changes: %s." % plant_kind_text(d2))
        for v in vs.values():
            v.trace_add("write", show) if hasattr(v, "trace_add") else v.trace("w", show)
        show()
        got = {}

        def ok():
            ks = [k for k, v in vs.items() if v.get()]
            if not ks:
                return messagebox.showerror("Plant type", "Tick at least one kind of generator.", parent=top)
            d2, did = set_kinds(data, ks, name.get())
            gone = [x for x in did if " removed: " in x]
            if gone and not messagebox.askyesno("Plant type", "This removes:\n\n%s\n\nGo on?" % "\n".join(
                    "- " + x for x in gone), parent=top):
                return
            got["d"], got["did"] = d2, did
            top.destroy()
        bb = ttk.Frame(top)
        bb.pack(pady=(0, 10))
        ttk.Button(bb, text="Set the plant type", command=ok).pack(side="left", padx=4)
        ttk.Button(bb, text="Cancel", command=top.destroy).pack(side="left", padx=4)
        app["size_dialog"] = {"top": top, "kinds": vs, "name": name, "then": then, "ok": ok}
        top.grab_set()
        root.wait_window(top)
        app["size_dialog"] = None
        if "d" not in got:
            return
        load(got["d"], app["path"], "plant type: %s%s" % (plant_kind_text(got["d"]), (" -- " + "; ".join(
            got["did"])) if got["did"] else ""))
        app["dirty"] = True
        set_status("plant type: %s%s" % (plant_kind_text(data), (" -- " + "; ".join(got["did"])) if got["did"] else
                                         ""))

    def q_follow(r, key, was):
        """A unit's MVA or P changed: a Unit Qmax given on its MVA circle (sqrt(MVA^2 - P^2)) -- and a Unit Qmin
           at -Qmax -- follow it -> what was done, or None (a blank Qmax follows by itself; one given otherwise,
           or with Unit PF, stays)."""
        q = _cell_num(r, "unit_qmax_mvar", None)
        if q is None or _txt(r.get("unit_pf")) is not None:
            return None
        old = dict(r)
        if _txt(was) is None:
            old.pop(key, None)
        else:
            old[key] = was

        def circle(x):
            s0 = _cell_num(x, "unit_mva", None)
            pm = max(abs(_cell_num(x, "unit_pmax_mw", 0.0)), abs(_cell_num(x, "unit_pmin_mw", 0.0)))
            return math.sqrt(s0 * s0 - pm * pm) if s0 and s0 >= pm else None
        c0, c1 = circle(old), circle(r)
        s1, qmin = _cell_num(r, "unit_mva", None), _cell_num(r, "unit_qmin_mvar", None)
        on = c0 is not None and abs(q - c0) <= 0.005 * max(c0, 1e-3) + 0.005
        big = lambda x: bool(s1) and x is not None and abs(x) > s1 * 1.0001     # beyond the unit's MVA: not so
        if not (on or big(q) or big(qmin)):
            return None                              # not on its circle, and possible: as given
        nm = _txt(r.get("name")) or "the generator"
        if c1 is None:
            return ("%s: its P is above Unit MVA -- Unit Qmax %s stays (it sat on the MVA circle)" % (
                nm, _txt(r.get("unit_qmax_mvar")))) if on else None
        f = lambda x: "%.4g" % x if abs(x) > 1e-9 else "0"
        did, why = [], " (above the unit's %s MVA before)" % f(s1) if big(q) or big(qmin) else ""
        if on or big(q):
            r["unit_qmax_mvar"] = f(c1)
            did.append("Unit Qmax %s -> %s MVAr" % (f(q), f(c1)))
        if qmin is not None and (abs(qmin + q) <= 0.005 * max(q, 1e-3) + 0.005 or big(qmin)):
            r["unit_qmin_mvar"] = f(-c1)
            did.append("Qmin %s" % f(-c1) if did else "Unit Qmin %s -> %s MVAr" % (f(qmin), f(-c1)))
        return "%s: %s -- the MVA left at full P, sqrt(MVA^2 - P^2)%s" % (nm, ", ".join(did), why)

    def feeders_follow(r, was, v):
        """A generator's Units changed: its own feeders follow when they carried its Units before -- as many
           feeders as its units need at the units on each it had (the GSU count follows the same way) -> what
           was done, or None."""
        nm = _txt(r.get("name"))
        try:
            old, new = int(float(was)), int(float(v))
        except (TypeError, ValueError):
            return None
        need = feeders_needed(data, nm) if nm else None
        if not need or new < 1:
            return None
        if need[1] == new:
            return None
        if need[1] != old:
            return "%s: Units %d -- its feeders carry %d units: Feeders on each MPT (MPT or Feeders page) spreads " \
                   "its Units over them" % (nm, new, need[1])
        try:
            data["feeders"], said = set_feeders(data, nm)
        except InputError as e:
            return str(e)
        for g in app["grids"].values():
            if g.key == "feeders":
                g.refresh()
        return "Units %d -- its feeders follow: %s" % (new, said)

    def grid_says(v):
        """What the grid fields make of the POI, live: the short-circuit MVA of each SCR given (SCR x SCR base),
           or the SCR of the short-circuit MVA / R, X given."""
        f = lambda x: "%.4g" % x
        try:
            base = float(v["scr_base_mw"]) if v["scr_base_mw"] else plant_totals(data)[2]["pmax"]
            what = "%s MW (%s)" % (f(base), "SCR base" if v["scr_base_mw"] else "the generators' Pmax")
            if v["grid_x_pu"]:
                sc = float(v["sbase_mva"] or 100.0) / abs(complex(float(v["grid_r_pu"] or 0.0), float(v["grid_x_pu"])))
                return "Grid R, X: %s MVA short-circuit at the POI -- SCR %s on %s" % (
                    f(sc), f(sc / base) if base > 0 else "-", what)
            if v["grid_sc_mva"]:
                xs = [float(x) for x in re.split(r"[,;\s]+", v["grid_sc_mva"]) if x]
                return "short-circuit MVA given: SCR %s on %s" % (
                    ", ".join(f(x / base) for x in xs) if base > 0 else "-", what)
            xs = [float(x) for x in re.split(r"[,;\s]+", v["grid_scr"]) if x]
            if not base > 0:
                return "the SCR needs an SCR base (the generators have no Pmax yet)"
            return "short-circuit at the POI = SCR x %s: %s%s" % (what, ", ".join(
                "%s MVA" % f(x * base) for x in xs), ("  (SCR %s: a set of models for each)" % ", ".join(
                    f(x) for x in xs)) if len(xs) > 1 else "")
        except (ValueError, ZeroDivisionError):
            return "several (3, 5, 10): a set of models for each"

    def req_live(*_a):
        """A required field's red note only while it is blank (the grid's: while none of its three ways is given);
           given, the grid's says what it makes of the POI."""
        v = dict((k, app["vars"][k].get().strip()) for k in ("poi_kv", "grid_scr", "grid_sc_mva", "grid_r_pu",
                                                              "grid_x_pu", "scr_base_mw", "sbase_mva"))
        for k, ok, done in (("poi_kv", v["poi_kv"], ""),
                            ("grid_scr", v["grid_scr"] or v["grid_sc_mva"] or (v["grid_r_pu"] and v["grid_x_pu"]),
                             None)):
            h, txt, style = hints[k]
            h.configure(text=txt if not ok else (grid_says(v) if done is None else done),
                        style=style if not ok else "Hint.TLabel")
    app["req_live"] = req_live
    for k in ("poi_kv", "grid_scr", "grid_sc_mva", "grid_r_pu", "grid_x_pu", "scr_base_mw", "sbase_mva"):
        v = app["vars"][k]
        v.trace_add("write", req_live) if hasattr(v, "trace_add") else v.trace("w", req_live)

    # ---------------------------------------------------------------- the simple view, or every field
    allv = tk.BooleanVar(value=False)

    plant_note = dict((f[0], f[3]) for f in PLANT_FIELDS)

    def holds_data(sheet, key):
        """Whether a field the simple view would hide holds a value of the user's (not its blank's default) --
           then it shows: no value is out of sight (the Build: models are on the toolbar)."""
        if sheet == "Plant":
            return key in app["vars"] and not key.startswith("out_") and _says_more(plant_note.get(key),
                                                                                     app["vars"][key].get())
        g = next((x for x in app["grids"].values() if x.page == sheet), None)
        if g is None or key not in g.keys:
            return False
        note = g.cols[g.keys.index(key)][2]
        want = SHOW_WITH.get((g.sheet, key))
        return any(_says_more(note, r.get(key)) or (want is not None and any(v for v in r.values()) and want(r))
                   for r in g.rows)

    def shown_view(sheet, key=None):
        """Whether a tab (and a field of it) shows now: in the simple view, the fields most plants need and any
           holding a value of the user's."""
        if allv.get():
            return True
        g = next((x for x in app["grids"].values() if x.page == sheet), None)
        if key is None:                          # rows set aside (#) only: nothing of the model's hidden
            return sheet in SIMPLE_TABS or bool(g and any(any(v not in (None, "") for v in r.values()) and
                                                          not _skip([r.get(k) for k in g.keys]) for r in g.rows))
        return sheet not in SIMPLE or key in SIMPLE[sheet] or holds_data(sheet, key)

    def vis_keys(g):
        """The columns a page shows now."""
        return [k for k in g.keys if shown_view(g.page, k) and k in PAGE_COLS.get(g.page, g.keys)]
    app["vis_keys"] = vis_keys

    def apply_view(*_a):
        """The simple view -- the fields most plants need (SIMPLE), the tabs they are on and any tab with rows of
           its own -- or every field (All fields). A hidden field keeps its value."""
        for key, ws in field_w.items():
            for w in ws:
                w.grid() if shown_view("Plant", key) else w.grid_remove()
        for sk, lab in sec_w.items():
            on = any(shown_view("Plant", k) for k, s2 in sec_of.items() if s2 == sk)
            lab.grid() if on else lab.grid_remove()
        for g in app["grids"].values():
            g.set_vis(vis_keys(g))
        for t in nb.tabs():
            nb.tab(t, state="normal" if shown_view(nb.tab(t, "text")) else "hidden")
        if "nav_view" in app:
            app["nav_view"]()

    def show_all():
        if not allv.get():
            allv.set(True)
            apply_view()

    page = dict((nb.tab(t, "text"), t) for t in nb.tabs())
    for grp, items in NAV_GROUPS:
        nav.insert("", "end", iid="g|" + grp, text=grp, open=True, tags=("grp",))
        for sheet, label in items:
            if sheet in page:
                nav.insert("g|" + grp, "end", iid=sheet, text=label)
    nav.tag_configure("grp", font=("Segoe UI", 9, "bold"), foreground=navy)
    syncing = [False]

    def nav_pick(_e=None):
        if syncing[0]:
            return
        sel = nav.selection()
        if not sel:
            return
        it = sel[0]
        if it.startswith("g|"):                           # a group: its first page
            kids = nav.get_children(it)
            if not kids:
                return
            it = kids[0]
        if it in page:
            nb.select(page[it])

    def nav_follow(_e=None):
        try:
            name = nb.tab(nb.select(), "text")
        except tk.TclError:
            return
        if nav.exists(name) and nav.selection() != (name,):
            syncing[0] = True
            nav.selection_set(name)
            nav.see(name)
            root.after_idle(lambda: syncing.__setitem__(0, False))

    def nav_view():
        """The navigator shows the pages shown: a group with none left out."""
        for grp, items in NAV_GROUPS:
            g = "g|" + grp
            k = 0
            for sheet, _label in items:
                if not nav.exists(sheet):
                    continue
                if nb.tab(page[sheet], "state") == "hidden":
                    nav.detach(sheet)
                else:
                    nav.move(sheet, g, k)
                    k += 1
            if k:
                nav.move(g, "", [x[0] for x in NAV_GROUPS].index(grp))
            else:
                nav.detach(g)
    nav.bind("<<TreeviewSelect>>", nav_pick)
    nb.bind("<<NotebookTabChanged>>", nav_follow, add="+")
    app["nav_view"] = nav_view

    # ---------------------------------------------------------------- toolbar and menu
    model_var = app["vars"]["model_type"]
    psse_var, pscad_var = tk.BooleanVar(value=PSSE_CHECK), tk.BooleanVar(value=PSCAD_BUILD)
    for txt, fn in (("New", lambda: new(True)), ("Open...", open_any), ("Save", save), ("Scale up...", scale_up)):
        ttk.Button(bar, text=txt, command=fn).pack(side="left", padx=2)
    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
    ttk.Label(bar, text="Model type:", style="Item.TLabel").pack(side="left")
    ttk.Combobox(bar, textvariable=model_var, values=["SMIB", "DETAILED"], state="readonly", width=10).pack(
        side="left", padx=4)
    ttk.Checkbutton(bar, text="All fields", variable=allv, command=apply_view).pack(side="left", padx=(8, 2))
    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
    b_check = ttk.Button(bar, text="Check inputs", command=check)
    b_check.pack(side="left", padx=2)
    b_build = ttk.Button(bar, text="Build models", command=build_models)
    b_build.pack(side="left", padx=2)
    b_open = ttk.Button(bar, text="Open output folder", command=open_out)
    b_open.pack(side="left", padx=2)
    b_open.state(["disabled"])

    def pick_all(on):
        """Select all / Uncheck all: every model the Build: row lists (the Then: steps stay as they are --
           each runs only for a model that is built)."""
        for k, _w in OUT_KINDS:
            app["vars"]["out_" + k].set("YES" if on else "NO")
    ttk.Label(bar2, text="Build:", style="Item.TLabel").pack(side="left")
    ttk.Button(bar2, text="Select all", width=10, command=lambda: pick_all(True)).pack(side="left", padx=(4, 0))
    ttk.Button(bar2, text="Uncheck all", width=11, command=lambda: pick_all(False)).pack(side="left", padx=(2, 6))
    for key, txt in OUT_KINDS:
        ttk.Checkbutton(bar2, text=txt, variable=app["vars"]["out_" + key], onvalue="YES", offvalue="NO").pack(
            side="left", padx=4)
    ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y", padx=8)
    ttk.Label(bar2, text="Then:", style="Item.TLabel").pack(side="left")
    ttk.Checkbutton(bar2, text="PSS/E check (needs the .raw)", variable=psse_var).pack(side="left", padx=4)
    ttk.Checkbutton(bar2, text="build the .pscx in PSCAD", variable=pscad_var).pack(side="left", padx=4)

    menu = tk.Menu(root)
    fm = tk.Menu(menu, tearoff=0)
    fm.add_command(label="New from the example plant", command=lambda: new(True))
    fm.add_command(label="New, empty", command=lambda: new(False))
    fm.add_command(label="Open project or workbook...", command=open_any)
    fm.add_separator()
    fm.add_command(label="Save project", command=save, accelerator="Ctrl+S")
    fm.add_command(label="Save project as...", command=lambda: save(True))
    fm.add_command(label="Export as an Excel workbook...", command=export_xlsx)
    fm.add_separator()
    fm.add_command(label="Exit", command=lambda: close())
    menu.add_cascade(label="File", menu=fm)
    rm = tk.Menu(menu, tearoff=0)
    rm.add_command(label="Check inputs", command=check)
    rm.add_command(label="Build models", command=build_models)
    rm.add_command(label="Open output folder", command=open_out)
    menu.add_cascade(label="Run", menu=rm)
    root.config(menu=menu)
    root.bind("<Control-s>", lambda e: save())

    def close():
        if app["running"] and not messagebox.askyesno("SMIB", "A build is running. Close anyway?", parent=root):
            return
        if ask_save_changes():
            root.destroy()
    root.protocol("WM_DELETE_WINDOW", close)

    # ---------------------------------------------------------------- what it opens with
    start = start or (os.path.join(here, GUI_PROJECT) if os.path.isfile(os.path.join(here, GUI_PROJECT)) else None)
    if not start:
        found = find_inputs([here])
        start = found[0] if found else None
    if start:
        try:
            inp0 = read_input(start, keep_comments=True)
            load(inp0, start if start.lower().endswith(".json") else None,
                 "opened %s" % start if start.lower().endswith(".json") else
                 "imported %s -- Save makes it a project (.json)" % start)
            if inp0.get("_unknown"):
                out("[smib] not used from %s (Save leaves them out): %s\n" % (start, "; ".join(inp0["_unknown"])))
        except Exception as e:
            load(EXAMPLE, None, "%s could not be read (%s) -- the example plant" % (start, e))
    else:
        load(EXAMPLE, None, "the example plant -- change it, Save, then Build models")
    root.after(100, poll)
    root.smib = {"data": data, "app": app, "nb": nb, "save": save, "check": check, "build": build_models,
                 "open": open_any, "export": export_xlsx, "load": load, "vars": app["vars"], "grids": app["grids"],
                 "new": new, "scale": scale_up, "goto": goto, "from_widgets": from_widgets, "status": status,
                 "psse": psse_var, "hints": hints, "allv": allv, "view": apply_view, "widgets": widgets,
                 "plant_total": plant_total, "field_w": field_w, "plant_type": plant_type,
                 "pscad": pscad_var, "ot": ot}               # the window's parts, for smib_build's own tests
    root.mainloop()
    return 0


def _has_window():
    """Whether this Python can open a window: tkinter, and a screen (Windows, or a display elsewhere)."""
    try:
        __import__("tkinter")
    except ImportError:
        return False
    return sys.platform.startswith("win") or sys.platform == "darwin" or bool(os.environ.get("DISPLAY"))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--psse-check" in argv:
        k = argv.index("--psse-check")
        return psse_check_only(os.path.abspath(argv[k + 1])) if k + 1 < len(argv) else 2
    if "--template" in argv:
        k = argv.index("--template")
        path = os.path.abspath(argv[k + 1] if k + 1 < len(argv) else "SMIB_INPUT.xlsx")
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"
        try:
            roles = example_roles()
        except Exception:
            roles = None
        if os.path.exists(path):
            print("[smib] *** %s exists -- nothing is overwritten: give the template another name" % path)
            return 2
        write_template(path, EXAMPLE, roles)
        print("[smib] input workbook written: %s" % path)
        print("       fill it in, then: python %s \"%s\"" % (os.path.basename(__file__), path))
        return 0
    if "--scale" in argv:
        k = argv.index("--scale")
        try:
            nums, src = [scale_arg(x) for x in argv[k + 1:k + 5]], os.path.abspath(argv[k + 5])
            if len(nums) < 4 or isinstance(nums[0], list):
                raise IndexError
        except (IndexError, InputError):
            print("[smib] *** --scale MPTS FEEDERS_PER_MPT GSUS_PER_FEEDER INVERTERS_PER_GSU input.xlsx|.json "
                  "[output.xlsx]   (0 = as it is; feeders, GSUs, inverters: one number, or one per MPT: 12,8,10)")
            return 2
        dst = (os.path.abspath(argv[k + 6]) if len(argv) > k + 6 else
               os.path.splitext(src)[0] + "_scaled_%s.xlsx" % "_".join(argv[k + 1:k + 5]).replace(",", "-"))
        if os.path.exists(dst):
            print("[smib] *** %s exists -- nothing is overwritten: give it another name" % dst)
            return 2
        try:
            tie = argv[argv.index("--tie") + 1].upper().replace("-", "_") if "--tie" in argv[:-1] else "PER_MPT"
            d, did = scale_plant(gui_save_data(gui_data(read_input(src))), *nums, tie=tie)
        except InputError as e:
            print("[smib] *** %s" % e)
            return 2
        write_template(dst, d)
        print("[smib] scaled up: %s\n       %s" % (dst, "\n       ".join(did)))
        return 0
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    files = [a for a in argv if not a.startswith("-")]
    if "--gui" in argv or (GUI and not files and not INPUT_XLSX and _has_window()):
        return gui(os.path.abspath(files[0]) if files else None)
    src = files[0] if files else INPUT_XLSX
    if not src:
        src = pick_input()
        if not src:
            return 2
    if not os.path.isfile(src):
        print("[smib] *** no file %s" % src)
        return 2
    try:
        build(os.path.abspath(src), check="--no-psse" not in argv, pscad="--no-pscad" not in argv)
    except InputError as e:
        print("[smib] *** input: %s" % e)
        return 2
    except SolveError as e:
        print("[smib] *** load flow: %s" % e)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
