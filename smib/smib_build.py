# -*- coding: utf-8 -*-
"""SMIB plant models for PSS/E and PSCAD, built from an Excel input workbook.

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
     Bus_Numbers    bus numbers / names of your own (blank = automatic)
     Dyr_Params     any .dyr model parameter, by generator, model and name
     Dyr_Records    .dyr records of your own (vendor models)
     PSCAD_Params   the vendor PSCAD model's parameters, by generator

   THE MODEL
     IB --[grid: SCR, X/R]-- POI --[gen-tie]-- HV --[MPT 1..n]-- collector bus
        --[collector equivalent (WECC)]-- MV --[GSU equivalent]-- LV -- generator
     IB is the infinite bus (the swing bus; GENCLS with H = 0 in the .dyr).
     Each generator is N units aggregated (MBASE, P, Q = N x one unit) behind
     its GSUs in parallel (count x MVA, the same % impedance) and its collector
     equivalent. Generators can share an MPT (its LV or tertiary winding), have
     MPTs of their own, share a collector system, or sit on any bus.

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
                    SYNC     GENROU + SEXS + TGOV1
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
                             MPTs, the collectors (the WECC equivalent, or
                             DETAILED: every feeder section), the GSUs and, on
                             each GSU's LV side, the inverter: your vendor model
                             (UDM: PSCAD library / definition / AC port in
                             Generators, its parameters in PSCAD_Params with
                             {UNITS} {P_PU} {V_PU} ... filled in) or a stand-in
                             source at the solved terminal voltage and angle
                             (the transformers' phase shifts included) -- a
                             check that PSCAD's network matches PSS/E's; delete
                             it and connect a UDM to its node. A UDM with its
                             GSU inside: PSCAD inverter at MV. Every parameter
                             it set, and any it could not, is listed in its
                             log, <name>_build_pscad_<date_time>.txt.
     <name>_PSCAD_DATA.txt   every PSCAD value: ohm, H, uF, kV, MVA, pu on the
                             equipment's own base, vector groups, initial values

   OUTPUT -- nothing that exists is ever overwritten
     {OUT_DIR}\\<name>_<date_time>\\   <name>.raw / .dyr / .sav, <name>_REPORT.txt,
         <name>_PSCAD_DATA.txt, <name>_build_pscad.py, <name>_inputs.json, a copy
         of the workbook, _psse\\ (PSS/E's own output of the check run)

   RUN (the PSS/E Python: 3.4 for PSS/E 34, 3.7+ for PSS/E 35 -- no PSS/E is
   needed to write the models; standard library only)
       python smib_build.py --template SMIB_INPUT.xlsx   an input workbook to fill in
       python smib_build.py SMIB_INPUT.xlsx              the models
       python smib_build.py SMIB_INPUT.xlsx --no-psse    the models, no PSS/E check
       python smib_build.py SMIB_INPUT.xlsx --no-pscad   the models, no PSCAD build
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
import time
import shutil
import subprocess
import zipfile
import posixpath
import collections
import traceback
from xml.sax.saxutils import escape as _xml_escape

# ============================================================================
# SETTINGS
# ============================================================================
INPUT_XLSX = ""                   # "" = the command line's, else the one beside this script | r"C:\...\SMIB.xlsx"
OUT_DIR = ""                      # "" = SMIB_MODELS beside the workbook; a new <name>_<date_time> folder inside
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

# ============================================================================
# THE INPUT WORKBOOK -- its sheets and columns, and the example it is written with
# ============================================================================
LEN_UNITS = ["km", "mi", "kft", "m", "ft"]
LEN_KM = {"km": 1.0, "mi": 1.609344, "kft": 0.3048, "m": 0.001, "ft": 0.0003048}
GEN_TYPES = ["PV", "BESS", "WT4", "WT3", "SYNC", "USER"]
# the electrical control models (REEC) each inverter type takes; the first is its default
REEC_OK = {"PV": ("REEC_A", "REEC_D"), "WT4": ("REEC_A", "REEC_D"), "WT3": ("REEC_A",),
           "BESS": ("REEC_C", "REEC_D")}

# Plant sheet (Item | Value | Unit | Notes): (key, item, unit, note, choices)
PLANT_FIELDS = [
    ("name", "Name", "", "model name: file names and the PSCAD case (letters, digits, _)", None),
    ("sbase_mva", "System base", "MVA", "PSS/E system base (blank = 100)", None),
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
    ("tie_unit", "Gen-tie length unit", "", "km, mi, kft, m, ft (blank = km)", LEN_UNITS),
    ("tie_r_per", "Gen-tie R per length", "ohm", "one circuit, per length unit", None),
    ("tie_x_per", "Gen-tie X per length", "ohm", "one circuit, per length unit", None),
    ("tie_b_per", "Gen-tie B per length", "uS", "one circuit, per length unit (total charging)", None),
    ("tie_c_per", "Gen-tie C per length", "uF", "instead of B", None),
    ("tie_r_ohm", "Gen-tie R total", "ohm", "instead of per length (one circuit)", None),
    ("tie_x_ohm", "Gen-tie X total", "ohm", "", None),
    ("tie_b_us", "Gen-tie B total", "uS", "", None),
    ("tie_parallel", "Gen-tie circuits", "", "identical circuits in parallel (blank = 1)", None),
    ("tie_rate_mva", "Gen-tie rating", "MVA", "one circuit", None),
    ("control_mode", "Control mode", "", "Q: Q at the POI | PF: power factor at the POI | V: the plant holds the POI "
     "voltage with the grid at Grid voltage", ["Q", "PF", "V"]),
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
    ("pscad_meter", "PSCAD meter at POI", "", "YES = a multimeter between the grid and the POI", ["YES", "NO"]),
    ("pscad_libraries", "PSCAD libraries", "", "user-model libraries (.pslx) to load, separated by ;", None),
]

# table sheets: (key, column, note, choices, width)
MPT_COLS = [
    ("name", "Name", "# MPT name. Its buses: <name> or <name>:LV (LV winding), <name>:TV (tertiary)", None, 11),
    ("hv_kv", "HV kV", "", None, 7),
    ("lv_kv", "LV kV", "", None, 7),
    ("tv_kv", "TV kV", "tertiary / 2nd LV winding; blank = two windings", None, 9),
    ("mva", "MVA", "the rating (3 windings: HV winding)", None, 8),
    ("lv_mva", "LV MVA", "3 windings", None, 7),
    ("tv_mva", "TV MVA", "3 windings", None, 7),
    ("z_hl_pct", "Z HV-LV %", "on its base MVA", None, 8),
    ("z_ht_pct", "Z HV-TV %", "3 windings", None, 8),
    ("z_lt_pct", "Z LV-TV %", "3 windings", None, 8),
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
    ("rate_mva", "Rating MVA", "blank = MVA", None, 8),
    ("tie_length", "Tie length", "a line of its own from HV to this MPT (blank = none)", None, 9),
    ("tie_unit", "Tie length unit", "", LEN_UNITS, 7),
    ("tie_r_per", "Tie R per length", "ohm", None, 8),
    ("tie_x_per", "Tie X per length", "ohm", None, 8),
    ("tie_b_per", "Tie B per length", "uS", None, 8),
    ("tie_rate_mva", "Tie rating MVA", "", None, 8),
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
    ("unit_qmax_mvar", "Unit Qmax MVAr", "blank = from Unit PF", None, 8),
    ("unit_qmin_mvar", "Unit Qmin MVAr", "blank = -Qmax", None, 8),
    ("unit_pf", "Unit PF", "Q at Pmax when Qmax is blank (blank = 0.95)", None, 7),
    ("kv", "Terminal kV", "blank = GSU LV kV", None, 8),
    ("dispatch_mw", "Dispatch MW", "this generator's total; blank = Pmax (BESS charging: negative)", None, 8),
    ("q_mode", "Q mode", "share: holds the POI with the other share generators | fixed: its own Q",
     ["share", "fixed"], 7),
    ("fixed_q_mvar", "Fixed Q MVAr", "Q mode fixed (blank = 0)", None, 8),
    ("fixed_pf", "Fixed PF", "Q mode fixed, instead of Fixed Q (+ lagging)", None, 7),
    ("gsu_count", "GSU count", "blank = Units", None, 6),
    ("gsu_mva", "GSU MVA", "one GSU; blank = no GSU (units on the MV bus)", None, 7),
    ("gsu_hv_kv", "GSU HV kV", "", None, 7),
    ("gsu_lv_kv", "GSU LV kV", "", None, 7),
    ("gsu_z_pct", "GSU Z %", "on GSU MVA", None, 7),
    ("gsu_x_r", "GSU X/R", "", None, 6),
    ("gsu_vector", "GSU vector group", "", None, 8),
    ("gsu_tap", "GSU HV tap", "blank = 1", None, 6),
    ("collector", "Collector", "FEEDERS: WECC equivalent of its Feeders rows | EQUIVALENT: R X B here | NONE",
     ["FEEDERS", "EQUIVALENT", "NONE"], 10),
    ("coll_r", "Collector R", "EQUIVALENT", None, 8),
    ("coll_x", "Collector X", "EQUIVALENT", None, 8),
    ("coll_b", "Collector B", "EQUIVALENT, total charging", None, 8),
    ("coll_units", "Collector units", "pu: system base at collector kV | ohm: R X in ohm, B in uS", ["pu", "ohm"], 8),
    ("zsorce_x", "ZSORCE X pu", "on MBASE (blank: inverters 9999, SYNC X''d)", None, 8),
    ("wmod", "WMOD", "blank: inverters 1, SYNC 0", None, 6),
    ("reec", "Electrical control", "PSS/E .dyr -- REEC_A: PV, WT | REEC_C: BESS | REEC_D: PV, BESS, WT4 (blank = "
     "REEC_A, BESS REEC_C)", ["REEC_A", "REEC_C", "REEC_D"], 9),
    ("pscad_library", "PSCAD library", "vendor library .pslx (blank = none)", None, 18),
    ("pscad_definition", "PSCAD definition", "vendor model, library:name (blank = a source: network check)", None, 16),
    ("pscad_ac_port", "PSCAD AC port", "its AC port (blank = its 3-phase port)", None, 9),
    ("pscad_inv_at", "PSCAD inverter at", "LV: the inverter (your UDM or the stand-in source) on the GSU's LV side, "
     "the GSU built | MV: on its HV side, for a UDM with its GSU inside (blank = LV)", ["LV", "MV"], 8),
    ("pscad_collector", "PSCAD collector", "EQUIVALENT: one pi section, as PSS/E | DETAILED: every Feeders section, "
     "a GSU and an inverter at every node with units (blank = EQUIVALENT)", ["EQUIVALENT", "DETAILED"], 11),
]

FEEDER_COLS = [
    ("collector", "Collector", "# the generator (its own collector) or a shared collector", None, 11),
    ("feeder", "Feeder", "feeder name", None, 8),
    ("copies", "Copies", "identical feeders (blank = 1)", None, 6),
    ("section", "Section", "1, 2, 3 ... from the collector bus out", None, 7),
    ("from_section", "From section", "blank = the one before (daisy chain) | 0 = the collector bus | n = a lateral "
     "off the end of section n", None, 9),
    ("cable", "Cable", "from Cables", None, 9),
    ("length", "Length", "", None, 7),
    ("unit", "Length unit", "blank = km", LEN_UNITS, 7),
    ("units", "Units at end", "units tapped at the far end", None, 7),
    ("units_of", "Units of", "shared collectors: whose units (repeat the section for another generator's)", None, 10),
    ("parallel", "Parallel cables", "blank = 1", None, 7),
]

CABLE_COLS = [
    ("cable", "Cable", "# cable type", None, 11),
    ("unit", "Length unit", "the values are per", LEN_UNITS, 7),
    ("r_per", "R per length", "ohm, at operating temperature", None, 9),
    ("x_per", "X per length", "ohm", None, 9),
    ("c_per", "C per length", "uF", None, 9),
    ("b_per", "B per length", "uS, instead of C", None, 9),
]

COLLECTOR_COLS = [
    ("name", "Name", "# shared collector: its far-end bus has this name", None, 11),
    ("connect", "Connect to", "the bus it starts from (MPT1, MPT1:TV ...)", None, 11),
    ("pscad_collector", "PSCAD collector", "EQUIVALENT | DETAILED: every section, a GSU and an inverter per "
     "generator at every node (blank = EQUIVALENT)", ["EQUIVALENT", "DETAILED"], 11),
]

LOAD_COLS = [
    ("kind", "Kind", "# LOAD: constant impedance | SHUNT: + capacitor, - reactor", ["LOAD", "SHUNT"], 8),
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

DYRP_COLS = [
    ("generator", "Generator", "# IB: the infinite bus | PLANT: the plant controller (REPC_B)", None, 11),
    ("model", "Model", "REGCA1 (REGCAU1) | REECA1 REECCU1 REECDU1 | REPCA1 (REPCAU1) or REAX4BU1 | PLANT: PLNTBU1 | "
     "WT3: + WTDTA1 WTARA1 WTPTA1 WTTQA1 | SYNC: GENROU SEXS TGOV1 | IB: GENCLS", None, 9),
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

TABLES = [  # (sheet, input key, columns)
    ("MPT", "mpt", MPT_COLS),
    ("Generators", "generators", GEN_COLS),
    ("Feeders", "feeders", FEEDER_COLS),
    ("Cables", "cables", CABLE_COLS),
    ("Collectors", "collectors", COLLECTOR_COLS),
    ("Loads_Shunts", "loads_shunts", LOAD_COLS),
    ("Bus_Numbers", "bus_numbers", BUS_COLS),
    ("Dyr_Params", "dyr_params", DYRP_COLS),
    ("Dyr_Records", "dyr_records", DYRR_COLS),
    ("PSCAD_Params", "pscad_params", PSCADP_COLS),
]

_PV_FEEDER = [("1000AL", 1.60, 0), ("500AL", 0.40, 1), ("500AL", 0.40, 1), ("4/0AL", 0.40, 1),
              ("4/0AL", 0.40, 1), ("1/0AL", 0.40, 1)]
_BESS_FEEDER = [("1000AL", 0.80, 0), ("500AL", 0.15, 1), ("500AL", 0.15, 1), ("4/0AL", 0.15, 1),
                ("4/0AL", 0.15, 1), ("1/0AL", 0.15, 1)]

# the example: a hybrid plant -- 100 MW PV (25 x 4.0 MW) and 100 MW BESS (25 x 4.0 MW) on one 230/34.5 kV MPT,
# five identical feeders each, one plant controller (REPC_B: PLNTBU1 + REAX4BU1); PSCAD: the collectors section
# by section, a GSU and an inverter slot at every node
EXAMPLE = {
    "plant": {
        "name": "Hybrid_PV100_BESS100", "sbase_mva": 100.0, "freq_hz": 60.0, "first_bus": 1000,
        "poi_kv": 230.0, "poi_name": "POI", "grid_scr": 3.0, "grid_x_r": 10.0,
        "tie_length": 5.0, "tie_unit": "km", "tie_r_per": 0.0720, "tie_x_per": 0.4950, "tie_b_per": 3.35,
        "tie_parallel": 1, "tie_rate_mva": 400.0,
        "control_mode": "Q", "v_poi_pu": 1.0, "q_poi_mvar": 0.0, "pf_poi": 1.0, "v_grid_pu": 1.0,
        "plant_ctrl": "REPC_B", "regc_repc": "standard",
        "pscad_time_step_us": 10.0, "pscad_duration_s": 5.0, "pscad_plot_step_us": 100.0, "pscad_meter": "YES",
    },
    "mpt": [
        {"name": "MPT1", "hv_kv": 230.0, "lv_kv": 34.5, "mva": 225.0, "z_hl_pct": 10.0, "x_r": 45.0,
         "vector": "YNd1", "tap": 1.0, "count": 1, "rate_mva": 225.0},
    ],
    "generators": [
        {"name": "PV", "type": "PV", "id": "1", "connect": "MPT1", "units": 25, "unit_mva": 4.2,
         "unit_pmax_mw": 4.0, "unit_pmin_mw": 0.0, "unit_qmax_mvar": 1.28, "unit_qmin_mvar": -1.28, "kv": 0.63,
         "q_mode": "share", "gsu_mva": 4.2, "gsu_hv_kv": 34.5, "gsu_lv_kv": 0.63, "gsu_z_pct": 6.5,
         "gsu_x_r": 8.0, "gsu_vector": "YNd1", "collector": "FEEDERS", "reec": "REEC_A", "pscad_inv_at": "LV",
         "pscad_collector": "DETAILED"},
        {"name": "BESS", "type": "BESS", "id": "1", "connect": "MPT1", "units": 25, "unit_mva": 4.4,
         "unit_pmax_mw": 4.0, "unit_pmin_mw": -4.0, "unit_qmax_mvar": 1.83, "unit_qmin_mvar": -1.83, "kv": 0.69,
         "q_mode": "share", "gsu_mva": 4.4, "gsu_hv_kv": 34.5, "gsu_lv_kv": 0.69, "gsu_z_pct": 6.0,
         "gsu_x_r": 7.0, "gsu_vector": "YNd1", "collector": "FEEDERS", "reec": "REEC_C", "pscad_inv_at": "LV",
         "pscad_collector": "DETAILED"},
    ],
    "feeders": [{"collector": gen, "feeder": "F", "copies": 5, "section": k + 1, "cable": c, "length": ln,
                 "unit": "km", "units": u}
                for gen, secs in (("PV", _PV_FEEDER), ("BESS", _BESS_FEEDER)) for k, (c, ln, u) in enumerate(secs)],
    "cables": [
        {"cable": "1000AL", "unit": "km", "r_per": 0.0460, "x_per": 0.1050, "c_per": 0.390},
        {"cable": "500AL", "unit": "km", "r_per": 0.0900, "x_per": 0.1180, "c_per": 0.300},
        {"cable": "4/0AL", "unit": "km", "r_per": 0.2010, "x_per": 0.1330, "c_per": 0.220},
        {"cable": "1/0AL", "unit": "km", "r_per": 0.4040, "x_per": 0.1480, "c_per": 0.170},
    ],
    "collectors": [],
    "loads_shunts": [{"kind": "LOAD", "at": "MPT1", "mw": 1.0, "mvar": 0.3, "name": "AUX"}],
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
    ("", "Control mode Q: the plant holds the POI voltage with Q at POI into the grid; PF: with that power factor; "
         "V: the plant holds the POI voltage with the infinite bus at Grid voltage. The infinite-bus voltage (and "
         "the generators' Q) come out of the load flow."),
    ("", "Plant controller (PSS/E .dyr): REPC_A = a REPCA1 on each generator, each holding the POI; REPC_B = one "
         "controller for the whole plant (hybrid PV + BESS): PLNTBU1 at the POI, and on each generator a REAX4BU1 "
         "that takes its share of the plant's Q (Qmax / sum of Qmax) and P (Pmax / sum of Pmax). REGC_A / REPC_A "
         "records: standard = REGCA1 / REPCA1 from the PSS/E library; USRMDL = REGCAU1 / REPCAU1."),
    ("MPT", "One row per main power transformer; HV winding first. Three windings: TV kV, the three impedances "
            "and their bases. Parallel units: identical units in parallel. Tie: a line of its own from HV to it."),
    ("Generators", "One row per generator (PV, BESS, wind, synchronous ...): the units (count and one unit's "
                   "data; the model is their aggregate), its GSU (one GSU's data and the count), its collector "
                   "(FEEDERS: the WECC equivalent of its rows in Feeders; EQUIVALENT: R X B given; NONE), how it "
                   "shares the POI's Q, its electrical control model in the .dyr (REEC_A: REECA1, PV and wind; "
                   "REEC_C: REECCU1, BESS; REEC_D: REECDU1, PV, BESS and WT4) and its PSCAD model (vendor library "
                   "and definition, or blank for a voltage source at the solved terminal voltage: a network check "
                   "against PSS/E), where the inverter connects (LV or MV side of the GSU) and how the collector "
                   "is drawn in PSCAD (EQUIVALENT or DETAILED)."),
    ("Feeders", "The collector feeders section by section from the collector bus out: cable, length and the units "
                "tapped at the section's far end. A daisy chain needs no From section; a lateral names the section "
                "it leaves from (its far end); 0 = straight from the collector bus. Copies: identical feeders. "
                "WECC equivalent: Zeq = sum(Zi Wi^2) / W^2, Beq = sum(Bi), Wi = MW of the units downstream of "
                "section i."),
    ("Cables", "Cable data per length (R, X in ohm; C in uF or B in uS) and the length unit they are per."),
    ("Collectors", "Collector systems shared by more than one generator: name and the bus it starts from. Their "
                   "Feeders rows say whose units each section carries (Units of); the generators on it Connect to "
                   "its name."),
    ("Loads_Shunts", "Auxiliary loads (constant impedance, MW and MVAr at 1 pu) and shunts (MVAr at 1 pu: + "
                     "capacitor, - reactor) on any bus."),
    ("Bus_Numbers", "Bus numbers or names of your own, by role (the report lists every role and its number)."),
    ("Dyr_Params", "Any parameter of the WECC-approved generic models, by generator, model and PSS/E name or "
                   "position (ICON2, CON14) -- IB for the infinite bus, PLANT for the plant controller. Inverters: "
                   "REGCA1 (REGC_A) + REECA1 / REECCU1 / REECDU1 (REEC_A / C / D) + REPCA1 (REPC_A) or REAX4BU1 "
                   "(with PLNTBU1, REPC_B); WT3: + WTDTA1 WTARA1 WTPTA1 WTTQA1; SYNC: GENROU SEXS TGOV1. REECCU1, "
                   "REECDU1, PLNTBU1, REAX4BU1 (and REGCAU1, REPCAU1) are user models: the WECC user-model DLL must "
                   "be loaded for the PSS/E check (ADDLIB_IDV / DLLS in the script). The report lists every model's "
                   "parameters with the values used."),
    ("Dyr_Records", "Records of your own (vendor or user models), one per row, ending in /. {BUS} {ID} {MBASE} "
                    "{POI} {IB} {HV} {MV} and {<bus role>} are filled in. Type USER generators have only these. "
                    "Models in user DLLs: list the DLLs in DLLS in the script for the PSS/E check."),
    ("PSCAD_Params", "Parameters of each generator's vendor PSCAD model (UDM), as PSCAD takes them. Filled in "
                     "for each inverter it stands for (DETAILED: each node): {UNITS} units, {MVA} their MVA, "
                     "{P_MW} {Q_MVAR} their P and Q where it connects, {P_PU} {Q_PU} on {MVA}, {UNIT_MVA} "
                     "{UNIT_P_MW} {UNIT_Q_MVAR} one unit's, {V_PU} {V_KV} {KV} {ANGLE} the voltage there, its base "
                     "and PSCAD angle, {P_INV_MW} {Q_INV_MVAR} {V_INV_PU} at the inverters' own terminals (a UDM on "
                     "the MV side), {FREQ} -- e.g. Ninv = {UNITS}, Vt0 = {V_PU}."),
    ("PSCAD", "The .pscx is built in the same run when PSCAD 5 and its Python library mhi.pscad (pip install "
              "mhi.pscad) are on the PC; otherwise run <name>_build_pscad.py where they are. "
              "The case holds the grid (source and Thevenin impedance), the gen-tie, the MPTs, each collector (the "
              "WECC equivalent as a pi section, or DETAILED: every feeder section, identical feeder copies lumped), "
              "the GSUs and, on each GSU's LV side, the inverter: your UDM (PSCAD library, definition and AC port "
              "in Generators) or a stand-in source at the solved voltage -- delete it and wire your UDM in its "
              "place (the report and the build log list every inverter node). PSCAD inverter at MV: for a UDM "
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
            raise InputError("%s: %s is missing" % (where, label or _label(key)))
        return default
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise InputError("%s: %s = %r is not a number" % (where, label or _label(key), v))
    if x != x or x in (float("inf"), float("-inf")):
        raise InputError("%s: %s = %r is not a number" % (where, label or _label(key), v))
    if positive and not x > 0:
        raise InputError("%s: %s must be above 0 (it is %s)" % (where, label or _label(key), v))
    return x


def _int(row, key, where, default=None, required=False, minimum=None):
    x = _num(row, key, where, None, required)
    if x is None:
        return default
    if abs(x - round(x)) > 1e-9:
        raise InputError("%s: %s = %s is not a whole number" % (where, _label(key), x))
    x = int(round(x))
    if minimum is not None and x < minimum:
        raise InputError("%s: %s = %d is below %d" % (where, _label(key), x, minimum))
    return x


def _choice(row, key, where, choices, default):
    s = _txt(row.get(key))
    if s is None:
        return default
    for c in choices:
        if s.upper() == c.upper():
            return c
    raise InputError("%s: %s = %r -- one of %s" % (where, _label(key), s, ", ".join(choices)))


_LABELS = {}
for _k, _lab, _u, _n, _c in PLANT_FIELDS:
    _LABELS[_k] = _lab
for _sh, _ik, _cols in TABLES:
    for _c in _cols:
        _LABELS.setdefault(_c[0], _c[1])


def _label(key):
    return "'%s'" % _LABELS.get(key, key)


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


def parse_workbook(book, path):
    """The workbook as {"plant": {key: value}, "<table>": [{key: value, "_where": ...}]}."""
    inp = {"plant": {}, "_source": path}
    rows = _find_sheet(book, "Plant")
    if rows is None:
        raise InputError("%s has no sheet 'Plant' -- start from: python smib_build.py --template x.xlsx" % path)
    items = dict((_key(lab), k) for k, lab, _u, _n, _c in PLANT_FIELDS)
    items.update((_key(k), k) for k, _l, _u, _n, _c in PLANT_FIELDS)
    unknown = []
    for k, r in enumerate(rows):
        if k == 0 or not r or _skip(r):
            continue
        item = _txt(r[0])
        key = items.get(_key(item or ""))
        if key is None:
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
        for k in range(hrow + 1, len(rows)):
            r = rows[k]
            if not r or _skip(r):
                continue
            d = {"_where": "%s row %d" % (sheet, k + 1)}
            for i, kk in colmap.items():
                d[kk] = r[i] if i < len(r) else None
            if any(v is not None for kk, v in d.items() if kk != "_where"):
                inp[key].append(d)
    return inp


_XL_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="4"><font><sz val="10"/><name val="Calibri"/></font>'
    '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
    '<font><i/><sz val="9"/><color rgb="FF595959"/><name val="Calibri"/></font>'
    '<font><b/><sz val="10"/><name val="Calibri"/></font></fonts>'
    '<fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF1F3864"/></patternFill></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2CC"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1">'
    '<alignment wrapText="1" vertical="center"/></xf>'
    '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
    '<alignment wrapText="1" vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="3" borderId="0" xfId="0" applyFill="1"/>'
    '<xf numFmtId="0" fontId="3" fillId="0" borderId="0" xfId="0" applyFont="1" applyAlignment="1">'
    '<alignment vertical="top"/></xf>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment wrapText="1" vertical="top"/></xf>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>')
XS_HEAD, XS_NOTE, XS_INPUT, XS_BOLD, XS_WRAP = 1, 2, 3, 4, 5


def _xl_cell(ref, v, st):
    if v is None or v == "":
        return '<c r="%s" s="%d"/>' % (ref, st) if st else ""
    if isinstance(v, bool):
        v = "YES" if v else "NO"
    if isinstance(v, (int, float)):
        return '<c r="%s" s="%d"><v>%s</v></c>' % (ref, st, repr(v) if isinstance(v, float) else v)
    return '<c r="%s" s="%d" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, st, _xml_escape(str(v)))


def _xl_sheet(rows, widths, freeze=(0, 0), lists=()):
    """rows: lists of values or (value, style); lists: [(range, [choices])] for drop-down cells."""
    xs, ys = freeze
    o = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">', '<sheetViews>']
    if xs or ys:
        tl = "%s%d" % (_col_letters(xs), ys + 1)
        pane = "bottomRight" if xs and ys else ("bottomLeft" if ys else "topRight")
        o.append('<sheetView workbookViewId="0"><pane %s%stopLeftCell="%s" activePane="%s" state="frozen"/>'
                 '</sheetView>' % (('xSplit="%d" ' % xs) if xs else "", ('ySplit="%d" ' % ys) if ys else "", tl, pane))
    else:
        o.append('<sheetView workbookViewId="0"/>')
    o.append('</sheetViews><cols>')
    for i, w in enumerate(widths):
        o.append('<col min="%d" max="%d" width="%s" customWidth="1"/>' % (i + 1, i + 1, w))
    o.append('</cols><sheetData>')
    for ri, r in enumerate(rows):
        cells = []
        for ci, x in enumerate(r):
            v, st = x if isinstance(x, tuple) else (x, 0)
            cells.append(_xl_cell("%s%d" % (_col_letters(ci), ri + 1), v, st))
        o.append('<row r="%d">%s</row>' % (ri + 1, "".join(cells)))
    o.append('</sheetData>')
    if lists:
        o.append('<dataValidations count="%d">' % len(lists))
        for rng, choices in lists:
            o.append('<dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="%s">'
                     '<formula1>"%s"</formula1></dataValidation>' % (rng, _xml_escape(",".join(choices))))
        o.append('</dataValidations>')
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


def write_template(path, data=None, roles=None):
    """The input workbook, filled in with `data` (the example), README first."""
    data = data or EXAMPLE
    sheets = []
    rows = [[("SMIB plant model -- input workbook", XS_BOLD), ""]]
    for topic, text in README:
        rows.append([(topic, XS_BOLD), (text, XS_WRAP)])
    sheets.append(("README", _xl_sheet(rows, [14, 120])))
    rows = [[("Item", XS_HEAD), ("Value", XS_HEAD), ("Unit", XS_HEAD), ("Notes", XS_HEAD)]]
    lists = []
    pl = data.get("plant", {})
    for k, (key, item, unit, note, choices) in enumerate(PLANT_FIELDS):
        rows.append([(item, XS_BOLD), (pl.get(key), XS_INPUT), unit, (note, XS_NOTE)])
        if choices:
            lists.append(("B%d" % (k + 2), choices))
    sheets.append(("Plant", _xl_sheet(rows, [24, 18, 7, 90], (0, 1), lists)))
    for sheet, key, cols in TABLES:
        rows = [[(c[1], XS_HEAD) for c in cols], [(c[2] or "", XS_NOTE) for c in cols]]
        recs = list(data.get(key, []))
        if key == "bus_numbers" and roles and not recs:
            recs = [{"bus": r, "number": None, "name": None} for r in roles]
        for rec in recs:
            rows.append([(rec.get(c[0]), XS_INPUT if c[0] != "record" else 0) for c in cols])
        lists = [("%s3:%s500" % (_col_letters(i), _col_letters(i)), c[3]) for i, c in enumerate(cols) if c[3]]
        sheets.append((sheet, _xl_sheet(rows, [c[4] for c in cols], (1, 2), lists)))
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
        self.pscad_slots, self.pscad_det = [], []  # PSCAD: the inverters' slots, the detailed collectors
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
        if r is None:
            raise InputError("%s: no bus %r -- buses: %s" % (where, ref, ", ".join(sorted(self.alias))))
        return r

    def kv(self, role):
        return self.bus[self.alias.get(role.upper(), role)]["kv"]


def line_data(d, prefix, kv, sb, f, where):
    """A line from <prefix>length / _unit / _r_per ... (or _r_ohm ... totals): None when none is given.
       r, x, b in pu on sb at kv for one circuit; R, X (ohm), B (uS) one circuit; count circuits."""
    g = lambda k: d.get(prefix + k)
    length = _num(d, prefix + "length", where)
    totals = any(g(k) not in (None, "") for k in ("r_ohm", "x_ohm"))
    if not totals and not length:
        return None
    unit = _choice(d, prefix + "unit", where, LEN_UNITS, "km")
    if totals:
        R = _num(d, prefix + "r_ohm", where, 0.0)
        X = _num(d, prefix + "x_ohm", where, 0.0)
        B = _num(d, prefix + "b_us", where, 0.0)
    else:
        R = _num(d, prefix + "r_per", where, required=True) * length
        X = _num(d, prefix + "x_per", where, required=True) * length
        b = _num(d, prefix + "b_per", where)
        c = _num(d, prefix + "c_per", where)
        B = (b if b is not None else (2.0 * math.pi * f * c if c is not None else 0.0)) * length
    if abs(complex(R, X)) < 1e-9:
        raise InputError("%s: the line has no impedance" % where)
    zb = kv * kv / sb
    km = (length or 0.0) * LEN_KM[unit]
    return {"r": R / zb, "x": X / zb, "b": B * 1e-6 * zb, "R": R, "X": X, "B": B, "km": km,
            "count": _int(d, prefix + "parallel", where, 1, minimum=1), "rate": _num(d, prefix + "rate_mva", where, 0.0),
            "kv": kv}


def tx_rx(z_pct, x_r, load_loss_kw, mva, where, what):
    """(r, x) pu on the transformer's own base from Z % and X/R, or Z % and the load loss."""
    if z_pct is None or not z_pct > 0:
        raise InputError("%s: %s impedance (Z %%) is missing" % (where, what))
    z = z_pct / 100.0
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
              "nomv1": kvb1, "nomv2": kvb2, "vg": vg, "rate": d.get("rate") or d["mva"]})
    t["mag_g"], t["mag_b"] = _mag(d.get("nll_pct"), d.get("im_pct"), d["mva"], net.sb, d["kv1"], kvb1)
    for side, kvr, kvb in (("HV", d["kv1"], kvb1), ("LV", d["kv2"], kvb2)):
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
def read_cables(rows):
    out = {}
    for r in rows:
        w = r["_where"]
        name = _txt(r.get("cable"))
        if not name:
            raise InputError("%s: the cable has no name" % w)
        unit = _choice(r, "unit", w, LEN_UNITS, "km")
        b = _num(r, "b_per", w)
        c = _num(r, "c_per", w)
        out[name.upper()] = {"name": name, "unit": unit, "r": _num(r, "r_per", w, required=True),
                             "x": _num(r, "x_per", w, required=True), "b": b, "c": c, "where": w}
    return out


def wecc_collector(cname, rows, cables, kv, sb, f, unit_mw, own_gen, log):
    """The WECC equivalent of collector `cname` from its Feeders rows.
       unit_mw: {GENERATOR (upper): MW of one unit}; own_gen: whose units a row without 'Units of' carries
       (None: a shared collector -- every row says)."""
    zb = kv * kv / sb
    feeders = collections.OrderedDict()
    for r in rows:
        feeders.setdefault(_txt(r.get("feeder")) or "F1", []).append(r)
    out = []
    sum_zw2, sum_b, W = 0j, 0.0, 0.0
    counts = collections.OrderedDict()
    for fname, frows in feeders.items():
        where = "%s (collector %s, feeder %s)" % (frows[0]["_where"], cname, fname)
        copies = max(_int(r, "copies", r["_where"], 1, minimum=1) for r in frows)
        secs, order, last = {}, [], 0
        for r in frows:
            w = r["_where"]
            num = _int(r, "section", w, None, minimum=1)
            cable = _txt(r.get("cable"))
            if num is None:
                num = last + 1 if cable else last
            if num == 0:
                raise InputError("%s: units on no section" % w)
            s = secs.get(num)
            if s is None:
                if not cable:
                    raise InputError("%s: section %d has no cable" % (w, num))
                cab = cables.get(cable.upper())
                if cab is None:
                    raise InputError("%s: cable %r is not in Cables" % (w, cable))
                s = {"num": num, "from": _int(r, "from_section", w, None, minimum=0), "cable": cab["name"],
                     "length": _num(r, "length", w, required=True, positive=True),
                     "unit": _choice(r, "unit", w, LEN_UNITS, "km"),
                     "parallel": _int(r, "parallel", w, 1, minimum=1), "units": collections.OrderedDict(),
                     "where": w}
                per = s["length"] * LEN_KM[s["unit"]] / LEN_KM[cab["unit"]]
                bper = cab["b"] if cab["b"] is not None else (2.0 * math.pi * f * cab["c"] if cab["c"] is not None
                                                              else 0.0)
                s["R"] = cab["r"] * per / s["parallel"]
                s["X"] = cab["x"] * per / s["parallel"]
                s["B"] = bper * per * s["parallel"]
                s["z"] = complex(s["R"], s["X"]) / zb
                s["b"] = s["B"] * 1e-6 * zb
                secs[num] = s
                order.append(num)
            elif cable:
                raise InputError("%s: section %d of feeder %s is given twice" % (w, num, fname))
            last = max(last, num)
            n = _int(r, "units", w, 0, minimum=0)
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
        prev = 0
        for num in order:                            # a daisy chain unless a section says where it starts
            s = secs[num]
            if s["from"] is None:
                s["from"] = prev
            elif s["from"] and s["from"] not in secs:
                raise InputError("%s: section %d starts from section %d, which feeder %s does not have"
                                 % (s["where"], num, s["from"], fname))
            prev = num
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
            raise InputError("%s: sections %s do not lead back to the collector bus (a loop?)" % (where, bad))
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
                    "z_own": (zf / wf ** 2) if wf else 0j, "b_own": bf, "where": where})
    if not W > 0:
        raise InputError("collector %s: its feeders carry no units" % cname)
    z = sum_zw2 / (W * W)
    res = {"name": cname, "kv": kv, "r": z.real, "x": z.imag, "b": sum_b, "R": z.real * zb, "X": z.imag * zb,
           "B": sum_b / zb * 1e6, "W": W, "N": counts, "feeders": out, "_mw": dict(unit_mw)}
    res["check"] = wecc_check(res, sb)
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
    name = _safe_name(_txt(pl.get("name")) or "SMIB")
    if name != (_txt(pl.get("name")) or "SMIB"):
        log("NOTE: model name %r is used as %r (letters, digits and _ only; 30 at most)" % (pl.get("name"), name))
    sb = _num(pl, "sbase_mva", W, 100.0, positive=True)
    f = _num(pl, "freq_hz", W, 60.0, positive=True)
    net = Net(name, sb, f)
    poi_kv = _num(pl, "poi_kv", W, required=True, positive=True)
    net.add_bus("IB", poi_kv, W)
    net.add_bus("POI", poi_kv, W)
    poi_name = _txt(pl.get("poi_name"))
    if poi_name:
        net.bus["POI"]["name"] = poi_name[:12]

    tie = line_data(pl, "tie_", poi_kv, sb, f, "Plant (gen-tie)")
    if tie:
        net.add_bus("HV", poi_kv, W)
        add_line(net, "POI", "HV", tie, "GEN-TIE", "GENTIE", "Plant (gen-tie)")
    else:
        net.add_alias("HV", "POI", W)

    for r in inp["mpt"]:
        build_mpt(net, r, f)

    cables = read_cables(inp["cables"])
    feeders = collections.OrderedDict()
    for r in inp["feeders"]:
        c = _txt(r.get("collector"))
        if not c:
            raise InputError("%s: Collector is missing" % r["_where"])
        feeders.setdefault(c.upper(), []).append(r)

    gens = []
    for r in inp["generators"]:
        gens.append(gen_data(r))
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
        res = wecc_collector(cname, rows, cables, net.kv(at), sb, f, unit_mw, None, log)
        res.update({"root": at, "own": None,
                    "pscad": _choice(r, "pscad_collector", w, ["EQUIVALENT", "DETAILED"], "EQUIVALENT")})
        net.colls[cname] = res
        net.add_bus("C:" + cname, net.kv(at), w, aliases=(cname,))
        add_line(net, at, "C:" + cname, {"r": res["r"], "x": res["x"], "b": res["b"], "R": res["R"],
                                           "X": res["X"], "B": res["B"], "count": 1, "rate": 0.0, "kv": net.kv(at),
                                           "km": 0.0}, cname, "COLLECTOR", w)

    for g in gens:
        build_generator(net, g, feeders, cables, unit_mw, used, log)

    for r in inp["loads_shunts"]:
        w = r["_where"]
        kind = _choice(r, "kind", w, ["LOAD", "SHUNT"], "LOAD")
        at = net.find(_txt(r.get("at")), w)
        if kind == "LOAD":
            net.loads.append({"bus": at, "mw": _num(r, "mw", w, 0.0), "mvar": _num(r, "mvar", w, 0.0),
                              "name": _txt(r.get("name")) or "LOAD", "where": w})
        else:
            net.shunts.append({"bus": at, "mvar": _num(r, "mvar", w, required=True),
                               "name": _txt(r.get("name")) or "SHUNT", "where": w})

    left = [k for k in feeders if k not in used]
    if left:
        raise InputError("Feeders: rows for %s, which is no generator with Collector FEEDERS and no shared "
                         "collector" % ", ".join(sorted(left)))

    thevenin(net, pl, gens)
    control(net, pl)
    number_buses(net, pl, inp["bus_numbers"])
    for u in inp.get("_unknown", []):
        net.notes.append("NOTE: not used -- %s" % u)
    return net


def build_mpt(net, r, f):
    w = r["_where"]
    name = _txt(r.get("name"))
    if not name:
        raise InputError("%s: Name is missing" % w)
    w = "%s (%s)" % (w, name)
    kv1 = _num(r, "hv_kv", w, required=True, positive=True)
    kv2 = _num(r, "lv_kv", w, required=True, positive=True)
    kv3 = _num(r, "tv_kv", w, None, positive=True)
    mva = _num(r, "mva", w, required=True, positive=True)
    tap = _num(r, "tap", w, 1.0, positive=True)
    count = _int(r, "count", w, 1, minimum=1)
    hv = net.find("HV", w)
    tie = line_data(r, "tie_", net.kv("HV"), net.sb, f, w + " tie")
    if tie:
        hv = net.add_bus(name + ":HV", net.kv("HV"), w)
        add_line(net, net.find("HV", w), hv, tie, name + "-TIE", "TIE", w)
    else:
        net.add_alias(name + ":HV", "HV", w)
    if abs(kv1 / net.kv("HV") - 1.0) > 0.05:
        net.notes.append("CHECK: %s: HV %.4g kV on the %.4g kV HV bus" % (w, kv1, net.kv("HV")))
    lv = net.add_bus(name + ":LV", kv2, w, aliases=(name,))
    common = {"name": name, "kind": "MPT", "tap": tap, "count": count, "where": w, "vector": _txt(r.get("vector")),
              "nll_pct": _num(r, "nll_pct", w, 0.0), "im_pct": _num(r, "im_pct", w, 0.0),
              "x_r": _num(r, "x_r", w)}
    if kv3 is None:
        d = dict(common)
        d.update({"hv": hv, "lv": lv, "mva": mva, "z_pct": _num(r, "z_hl_pct", w, required=True, positive=True),
                  "load_loss_kw": _num(r, "load_loss_kw", w), "kv1": kv1, "kv2": kv2,
                  "rate": _num(r, "rate_mva", w, mva)})
        add_tx2(net, d)
    else:
        tv = net.add_bus(name + ":TV", kv3, w)
        d = dict(common)
        d.update({"hv": hv, "lv": lv, "tv": tv, "kv1": kv1, "kv2": kv2, "kv3": kv3, "mva1": mva,
                  "mva2": _num(r, "lv_mva", w, mva, positive=True), "mva3": _num(r, "tv_mva", w, mva, positive=True),
                  "z12": _num(r, "z_hl_pct", w, required=True, positive=True),
                  "z13": _num(r, "z_ht_pct", w, required=True, positive=True),
                  "z23": _num(r, "z_lt_pct", w, required=True, positive=True),
                  "base12": _num(r, "base_hl_mva", w, mva, positive=True),
                  "base13": _num(r, "base_ht_mva", w, mva, positive=True),
                  "base23": _num(r, "base_lt_mva", w, mva, positive=True),
                  "rate": _num(r, "rate_mva", w, mva)})
        add_tx3(net, d)


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
    n = _int(r, "units", w, required=True, minimum=1)
    pmax = _num(r, "unit_pmax_mw", w, required=True)
    pmin = _num(r, "unit_pmin_mw", w, 0.0)
    pf = _num(r, "unit_pf", w, 0.95)
    qmax = _num(r, "unit_qmax_mvar", w)
    if qmax is None:
        if not 0.0 < abs(pf) <= 1.0:
            raise InputError("%s: Unit PF must be between 0 and 1" % w)
        qmax = abs(pmax) * math.tan(math.acos(abs(pf)))
    qmin = _num(r, "unit_qmin_mvar", w, -qmax)
    s = _num(r, "unit_mva", w, None, positive=True) or math.hypot(max(abs(pmax), abs(pmin)), max(qmax, -qmin))
    if pmin > pmax:
        raise InputError("%s: Unit Pmin is above Unit Pmax" % w)
    if qmin > qmax:
        raise InputError("%s: Unit Qmin is above Unit Qmax" % w)
    g = {"name": name, "type": typ, "reec": reec, "id": (_txt(r.get("id")) or "1")[:2], "row": r, "where": w, "n": n,
         "unit": {"mva": s, "pmax": pmax, "pmin": pmin, "qmax": qmax, "qmin": qmin, "pf": pf},
         "mbase": n * s, "pmax": n * pmax, "pmin": n * pmin, "qmax": n * qmax, "qmin": n * qmin,
         "q_mode": _choice(r, "q_mode", w, ["share", "fixed"], "share"),
         "zsorce_x": _num(r, "zsorce_x", w), "wmod": _int(r, "wmod", w, None),
         "pscad": {"library": _txt(r.get("pscad_library")), "definition": _txt(r.get("pscad_definition")),
                   "ac_port": _txt(r.get("pscad_ac_port")), "at": _choice(r, "pscad_inv_at", w, ["LV", "MV"], "LV"),
                   "collector": _choice(r, "pscad_collector", w, ["EQUIVALENT", "DETAILED"], "EQUIVALENT")}}
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
                raise InputError("%s: Fixed PF must be between 0 and 1" % w)
            q = ("pf", fpf)
        g["q_fixed"] = q if q is not None else 0.0
    if pmax > s + 1e-9:
        raise InputError("%s: Unit Pmax %.3f MW is above Unit MVA %.3f" % (w, pmax, s))
    return g


def build_generator(net, g, feeders, cables, unit_mw, used, log):
    r, w, name = g["row"], g["where"], g["name"]
    at = net.find(_txt(r.get("connect")), w + " Connect to")
    g["connect"] = at
    rows = feeders.get(name.upper())
    mode = _choice(r, "collector", w, ["FEEDERS", "EQUIVALENT", "NONE"], None)
    if mode is None:
        mode = "FEEDERS" if rows else ("EQUIVALENT" if _txt(r.get("coll_x")) or _txt(r.get("coll_r")) else "NONE")
    kv = net.kv(at)
    mv = name + ":MV"
    if mode == "FEEDERS":
        if not rows:
            raise InputError("%s: Collector FEEDERS but Feeders has no rows for %s" % (w, name))
        used.add(name.upper())
        res = wecc_collector(name, rows, cables, kv, net.sb, net.f, unit_mw, name, log)
        res.update({"root": at, "own": name, "pscad": g["pscad"]["collector"]})
        net.colls[name] = res
        n = res["N"].get(name.upper(), 0)
        if n != g["n"]:
            net.notes.append("CHECK: %s: its feeders carry %d units, Units says %d -- the equivalent weighs the "
                             "feeders' units; the machine has %d" % (w, n, g["n"], g["n"]))
        line = {"r": res["r"], "x": res["x"], "b": res["b"], "R": res["R"], "X": res["X"], "B": res["B"],
                "count": 1, "rate": 0.0, "kv": kv, "km": 0.0}
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
        line.update({"count": 1, "rate": 0.0, "kv": kv, "km": 0.0})
        if abs(complex(line["r"], line["x"])) < 1e-9:
            raise InputError("%s: the collector equivalent has no impedance" % w)
    else:
        if rows:
            raise InputError("%s: Collector NONE, but Feeders has rows for %s" % (w, name))
        line = None
    if g["pscad"]["collector"] == "DETAILED" and mode != "FEEDERS":
        net.notes.append("NOTE: %s: PSCAD collector DETAILED needs Collector FEEDERS -- PSCAD draws the %s"
                         % (w, "equivalent" if line else "units on %s" % at))
    if line:
        net.add_bus(mv, kv, w)
        add_line(net, at, mv, line, name + "-COLL", "COLLECTOR", w)
    else:
        net.add_alias(mv, at, w)
    gsu_mva = _num(r, "gsu_mva", w, None, positive=True)
    if gsu_mva:
        kv1 = _num(r, "gsu_hv_kv", w, kv, positive=True)
        kv2 = _num(r, "gsu_lv_kv", w, None, positive=True) or _num(r, "kv", w, required=True, positive=True)
        cnt = _int(r, "gsu_count", w, g["n"], minimum=1)
        x_r = _num(r, "gsu_x_r", w)
        if x_r is None:
            x_r = 8.0
            net.notes.append("CHECK: %s: GSU X/R not given -- 8 used" % w)
        lv = net.add_bus(name + ":LV", kv2, w, aliases=(name,))
        g["gsu"] = add_tx2(net, {"name": name + "-GSU", "kind": "GSU", "hv": net.alias[mv.upper()], "lv": lv,
                                  "mva": cnt * gsu_mva, "unit_mva": gsu_mva, "units": cnt,
                                  "z_pct": _num(r, "gsu_z_pct", w, required=True, positive=True), "x_r": x_r,
                                  "load_loss_kw": None, "kv1": kv1, "kv2": kv2,
                                  "tap": _num(r, "gsu_tap", w, 1.0, positive=True), "vector": _txt(r.get("gsu_vector")),
                                  "nll_pct": 0.0, "im_pct": 0.0, "rate": cnt * gsu_mva, "count": 1, "where": w})
        if g["mbase"] > cnt * gsu_mva * 1.02:
            net.notes.append("CHECK: %s: units %.1f MVA behind GSUs of %.1f MVA" % (w, g["mbase"], cnt * gsu_mva))
    else:
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


def thevenin(net, pl, gens):
    W = "Plant"
    rated = _num(pl, "scr_base_mw", W, None, positive=True) or sum(max(g["pmax"], 0.0) for g in gens)
    x_r = _num(pl, "grid_x_r", W, None)
    r_pu, x_pu = _num(pl, "grid_r_pu", W), _num(pl, "grid_x_pu", W)
    sc = _num(pl, "grid_sc_mva", W, None, positive=True)
    scr = _num(pl, "grid_scr", W, None, positive=True)
    if x_pu is not None:
        r_pu = r_pu or 0.0
        how = "R and X given"
        z = math.hypot(r_pu, x_pu)
        sc = net.sb / z
        scr = sc / rated if rated else None
        x_r = x_pu / r_pu if r_pu else float("inf")
    else:
        if sc is None and scr is None:
            raise InputError("Plant: Grid SCR (or Grid short-circuit MVA, or Grid R / X) is missing")
        if x_r is None:
            raise InputError("Plant: Grid X/R is missing")
        if sc is None:
            if not rated:
                raise InputError("Plant: the SCR needs an SCR base (the generators have no Pmax)")
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
        raise InputError("Plant: PF at POI must be between 0 and 1 (+ lagging, - leading)")
    c["pscad"] = {"time_step_us": _num(pl, "pscad_time_step_us", W, 10.0, positive=True),
                  "duration_s": _num(pl, "pscad_duration_s", W, 5.0, positive=True),
                  "plot_step_us": _num(pl, "pscad_plot_step_us", W, 100.0, positive=True),
                  "meter": _choice(pl, "pscad_meter", W, ["YES", "NO"], "YES") == "YES",
                  "libraries": [x.strip() for x in (_txt(pl.get("pscad_libraries")) or "").split(";") if x.strip()]}
    net.ctl = c


def number_buses(net, pl, rows):
    start = _int(pl, "first_bus", "Plant", 1000, minimum=0)
    fixed, names = {}, {}
    for r in rows:
        w = r["_where"]
        ref = _txt(r.get("bus"))
        if not ref:
            continue
        role = net.find(ref, w)
        num = _int(r, "number", w, None, minimum=1)
        if num is not None:
            if num > 999997:
                raise InputError("%s: bus number %d is above 999997" % (w, num))
            if num in fixed.values() and fixed.get(role) != num:
                raise InputError("%s: bus number %d is used twice" % (w, num))
            fixed[role] = num
        nm = _txt(r.get("name"))
        if nm:
            names[role] = nm[:12]
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
            raise InputError("Plant: bus numbers run past 999997 -- a lower First bus number")
        b["name"] = names.get(role) or b["name"] or role.replace("C:", "").replace(":", "_")[:12]


# ============================================================================
# LOAD FLOW -- Newton-Raphson on the plant's own network (no PSS/E needed)
# ============================================================================
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
            y = 1.0 / complex(ln["r"], ln["x"])
            hb = 0.5j * ln["b"]
            for _c in range(ln["count"]):
                branch(idx[ln["fr"]], idx[ln["to"]], y + hb, -y, -y, y + hb)
    for t in net.tx2:
        if t["hv"] in idx and t["lv"] in idx:
            y = 1.0 / (complex(t["r"], t["x"]) * net.sb / t["mva"])
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
                z = zk[w] if abs(zk[w]) > 1e-9 else 1e-9j
                y = 1.0 / z
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
    """A x = b by Gaussian elimination with partial pivoting (A, b are changed)."""
    n = len(b)
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        if abs(A[p][c]) < 1e-14:
            raise ZeroDivisionError("singular matrix")
        if p != c:
            A[c], A[p] = A[p], A[c]
            b[c], b[p] = b[p], b[c]
        piv = A[c][c]
        rowc = A[c]
        for r in range(c + 1, n):
            f = A[r][c] / piv
            if f:
                rr = A[r]
                for k in range(c, n):
                    rr[k] -= f * rowc[k]
                b[r] -= f * b[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = b[r] - sum(A[r][k] * x[k] for k in range(r + 1, n))
        x[r] = s / A[r][r]
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
        J = [[0.0] * (2 * m) for _r in range(2 * m)]
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
        dx = _lin_solve(J, mis)
        for a, k in enumerate(pq):
            ang = va[k] + dx[a]
            mag = vm[k] + dx[m + a]
            if mag <= 0.05:
                mag = 0.05
            V[k] = cmath.rect(mag, ang)
    return V, False, itmax


class SolveError(Exception):
    pass


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
        if not ok:
            raise SolveError("the plant's load flow does not converge (alpha %.3f, Q %.1f MVAr)" % (alpha, beta))
        state["V"] = V
        i_poi = sum(Y[poi][k] * V[k] for k in range(N))
        s_grid = -(V[poi] * i_poi.conjugate()) * sb                # MW + jMVAr from the POI into the grid
        e = V[poi] - zth * (s_grid / sb / V[poi]).conjugate()
        return V, s_grid, e, pq

    targets = []
    if c["p_poi"] is not None:
        if not any(abs(g["dispatch"]) > 0 for g in net.gens):
            raise InputError("Plant: P at POI is given but every generator's dispatch is 0")
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
            raise SolveError("the POI targets cannot be met (no generator moves them)")
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
        raise SolveError("the POI targets are not met after 60 iterations: residuals %s" % r)
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
    for g in net.gens:
        if g["q_mode"] == "share" and not (g["qmin"] - 1e-3 <= g["q"] <= g["qmax"] + 1e-3):
            net.notes.append("CHECK: %s needs Q %.2f MVAr, outside its %.2f .. %.2f MVAr -- the POI target is "
                             "beyond the plant (PSS/E will hold the limit)" % (g["name"], g["q"], g["qmin"], g["qmax"]))
        vt = net.bus[g["bus"]]["vm"]
        if not 0.9 <= vt <= 1.1:
            net.notes.append("CHECK: %s terminal voltage %.4f pu" % (g["name"], vt))
    return net.op


def flows(net, idx, V, Y):
    """Branch flows from the solved voltages (MW + jMVAr leaving each end), losses, loading."""
    sb = net.sb
    out = []

    def s_ij(i, yii, yij, j):
        return V[i] * (yii * V[i] + yij * V[j]).conjugate() * sb

    for ln in net.lines:
        i, j = idx[ln["fr"]], idx[ln["to"]]
        y = 1.0 / complex(ln["r"], ln["x"])
        hb = 0.5j * ln["b"]
        s1 = s_ij(i, y + hb, -y, j) * ln["count"]
        s2 = s_ij(j, y + hb, -y, i) * ln["count"]
        rate = ln["rate"] * ln["count"]
        out.append({"name": ln["name"], "kind": ln["kind"], "a": ln["fr"], "b": ln["to"], "s1": s1, "s2": s2,
                    "rate": rate, "load": (max(abs(s1), abs(s2)) / rate * 100.0) if rate else None})
    for t in net.tx2:
        i, j = idx[t["hv"]], idx[t["lv"]]
        y = 1.0 / (complex(t["r"], t["x"]) * sb / t["mva"])
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
            y = 1.0 / (zk[w] if abs(zk[w]) > 1e-9 else 1e-9j)
            tk = t["windv"][w] / net.kv(role)
            i = idx[role]
            yy = y / (tk * tk) + (complex(t["mag_g"], t["mag_b"]) if w == 0 else 0)
            ss.append(V[i] * (yy * V[i] - (y / tk) * V[k]).conjugate() * sb * t["count"])
        rate = t["rate"] * t["count"]
        out.append({"name": t["name"], "kind": t["kind"], "a": t["hv"], "b": t["lv"] + "/" + t["tv"], "s1": ss[0],
                    "s2": ss[1] + ss[2], "s3": ss, "rate": rate,
                    "load": (abs(ss[0]) / rate * 100.0) if rate else None})
    for f in out:
        if f["load"] is not None and f["load"] > 100.0:
            net.notes.append("CHECK: %s loaded to %.1f %% of %.1f MVA" % (f["name"], f["load"], f["rate"]))
    net.op["flows"] = out
    net.op["loss"] = sum(f["s1"] + f["s2"] for f in out if f["kind"] != "GRID")


# ============================================================================
# PSS/E -- the .raw (rev 33)
# ============================================================================
def _num_of(net, role):
    return net.bus[role]["num"]


def write_raw(net, path):
    sb = net.sb
    L = ["0, %.2f, 33, 0, 0, %.2f     / PSS(R)E-33 RAW  SMIB %s -- smib_build.py  %s"
         % (sb, net.f, net.name, time.strftime("%Y-%m-%d %H:%M")),
         ("SMIB %s" % net.name)[:60],
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
        if g["q_mode"] == "share":
            qt, qb, vs, ireg, rmp = g["qmax"], g["qmin"], net.ctl["v_poi"], poi, 100.0 * g["share"]
        else:
            qt, qb, vs, ireg, rmp = g["q"], g["q"], net.bus[g["bus"]]["vm"], 0, 100.0
        zx = g["zsorce_x"]
        if zx is None:
            zx = DYN_DEFAULTS["GENROU"]["Xdpp"] if g["type"] == "SYNC" else IBR_ZSORCE_X
        wmod = g["wmod"] if g["wmod"] is not None else (0 if g["type"] == "SYNC" else 1)
        g["zx"], g["wmod_used"], g["ireg"], g["vs"] = zx, wmod, ireg, vs
        L.append("%7d,'%-2s',%10.4f,%10.4f,%10.4f,%10.4f,%9.6f,%7d,%10.3f,%9.6f,%9.4f, 0.00000, 0.00000,1.00000,1,"
                 "%6.2f,%10.4f,%10.4f,   1,1.0000,   0,1.0000,   0,1.0000,   0,1.0000,%d, 1.0000"
                 % (_num_of(net, g["bus"]), g["id"], g["p"], g["q"], qt, qb, vs, ireg, g["mbase"], 0.0, zx, rmp,
                    g["pmax"], g["pmin"], wmod))
    L.append("0 / END OF GENERATOR DATA, BEGIN BRANCH DATA")
    for ln in net.lines:
        for c in range(ln["count"]):
            L.append("%7d,%7d,'%-2s',%12s,%12s,%12s,%9.2f,%9.2f,%9.2f, 0.00000, 0.00000, 0.00000, 0.00000,1,1,"
                     "%8.3f,   1,1.0000"
                     % (_num_of(net, ln["fr"]), _num_of(net, ln["to"]), str(c + 1), _g(ln["r"], 7), _g(ln["x"], 7),
                        _g(ln["b"], 7), ln["rate"], ln["rate"], ln["rate"], ln["km"]))
    L.append("0 / END OF BRANCH DATA, BEGIN TRANSFORMER DATA")
    for t in net.tx2:
        for c in range(t["count"]):
            L.append("%7d,%7d,      0,'%-2s',1,2,1,%12s,%12s,2,'%-12s',1,   1,1.0000,   0,1.0000,   0,1.0000,   0,"
                     "1.0000,'%-12s'" % (_num_of(net, t["hv"]), _num_of(net, t["lv"]), str(c + 1), _g(t["mag_g"], 7),
                                         _g(t["mag_b"], 7), t["name"][:12], t["vg"]["text"][:12]))
            L.append("%12s,%12s,%10.3f" % (_g(t["r"], 7), _g(t["x"], 7), t["mva"]))
            w1 = t["windv1"] / t["nomv1"]
            L.append("%10.7f,%10.4f,   0.000,%9.2f,%9.2f,%9.2f, 0,      0, 1.10000, 0.90000,1.10000,0.90000,  33, 0, "
                     "0.00000, 0.00000,  0.000" % (w1, t["nomv1"], t["rate"], t["rate"], t["rate"]))
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
            rates = [t["rate"], t["mva2"], t["mva3"]]
            for w in range(3):
                L.append("%10.7f,%10.4f,   0.000,%9.2f,%9.2f,%9.2f, 0,      0, 1.10000, 0.90000,1.10000,0.90000,  33, 0, "
                         "0.00000, 0.00000,  0.000" % (t["windv"][w] / t["nomv"][w], t["nomv"][w], rates[w], rates[w],
                                                      rates[w]))
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
# PSS/E -- the .dyr
# ============================================================================
# The models -- WECC-approved only (WECC Approved Dynamic Model List): REGC_A, REEC_A, REEC_C, REEC_D, REPC_A,
# REPC_B, the type 3 wind models (WTGT_A, WTGAR_A, WTGPT_A, WTGTRQ_A), GENROU, SEXS, TGOV1, GENCLS.
#   inverters  REGCA1 (REGC_A) + the generator's REEC + its part of the plant controller:
#                REEC_A  REECA1    PV, WT4, WT3     REPC_A  REPCA1 on the generator
#                REEC_C  REECCU1   BESS             REPC_B  REAX4BU1 on the generator and one PLNTBU1 (a USRBUS
#                REEC_D  REECDU1   PV, BESS, WT4            record) at the POI for the plant
#              REGCAU1 / REPCAU1 for REGCA1 / REPCA1 with Plant 'REGC_A / REPC_A records' USRMDL
#   WT3        + WTDTA1 WTARA1 WTPTA1 WTTQA1        SYNC  GENROU + SEXS + TGOV1
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
])
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
}

DYN_DEFAULTS["REGCAU1"] = dict(DYN_DEFAULTS["REGCA1"])
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
PLANT_CTRL_TYPES = ("PV", "BESS", "WT4")   # REPC_B's REAX4BU1 is for type 4 machines (WT3 keeps a REPCA1)


def on_plntbu1(g, ctl):
    """True: generator g takes its P and Q from the plant's PLNTBU1 (REPC_B) through a REAX4BU1."""
    return ctl.get("plant_ctrl") == "REPC_B" and g["type"] in PLANT_CTRL_TYPES and g["q_mode"] == "share"


def gen_chain(g, ctl):
    """Generator g's .dyr models: its type, Electrical control and the Plant's controller choices."""
    if g["type"] == "SYNC":
        return ["GENROU", "SEXS", "TGOV1"]
    if g["type"] == "USER":
        return []
    usr = ctl.get("regc_repc") == "USRMDL"
    chain = ["REGCAU1" if usr else "REGCA1", REEC_MODEL[g["reec"]]]
    if g["q_mode"] == "share":                   # fixed Q: no plant controller, its REEC holds its own Q
        chain.append("REAX4BU1" if on_plntbu1(g, ctl) else ("REPCAU1" if usr else "REPCA1"))
    if g["type"] == "WT3":
        chain += ["WTDTA1", "WTARA1", "WTPTA1", "WTTQA1"]
    return chain


def dyr_params(net, inp):
    """{GENERATOR, IB or PLANT: {MODEL: {param: value}}} from Dyr_Params; each name (or ICONn / CONn) checked
       against the model."""
    out = collections.defaultdict(lambda: collections.defaultdict(dict))
    gnames = dict((g["name"].upper(), g["name"]) for g in net.gens)
    gnames["IB"] = "IB"
    gnames["PLANT"] = "PLANT"
    for r in inp["dyr_params"]:
        w = r["_where"]
        gen = (_txt(r.get("generator")) or "").upper()
        if gen not in gnames:
            raise InputError("%s: Generator %r -- one of %s" % (w, r.get("generator"), ", ".join(sorted(gnames))))
        model = (_txt(r.get("model")) or "").upper()
        if model not in DYR_LIB:
            raise InputError("%s: Model %r -- one of %s" % (w, r.get("model"), ", ".join(DYR_LIB)))
        pname = _txt(r.get("parameter")) or ""
        allp = DYR_LIB[model][2]
        names = dict((p.upper(), p) for p in allp)
        if pname.upper() not in names and DYR_ALIAS.get(pname.upper(), "").upper() in names:
            pname = DYR_ALIAS[pname.upper()]
        pos = re.match(r"^(I?CON)\s*\(?\s*(\d+)\s*\)?$", pname.upper())
        if pname.upper() not in names and pos:
            nic = DYR_LIB[model][1]
            part = allp[:nic] if pos.group(1) == "ICON" else allp[nic:]
            if 1 <= int(pos.group(2)) <= len(part):
                pname = part[int(pos.group(2)) - 1]
        if pname.upper() not in names:
            raise InputError("%s: %s has no parameter %r -- it has %s (or ICON1..%d, CON1..%d)"
                             % (w, model, pname, ", ".join(allp), DYR_LIB[model][1], len(allp) - DYR_LIB[model][1]))
        val = r.get("value")
        if names[pname.upper()] == "CKT":
            val = _txt(val) or "1"
        else:
            val = _num(r, "value", w, required=True)
        out[gnames[gen]][model][names[pname.upper()]] = val
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
        }
        auto["REPCAU1"] = auto["REPCA1"]
        for m in over.get(g["name"], {}):
            if m not in chain:
                net.notes.append("NOTE: Dyr_Params for %s %s: %s is not in its model chain %s -- not used"
                                 % (g["name"], m, m, "+".join(chain) or "(none: type USER)"))
        for m in chain:
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
        if key in ("BUS", "ID", "MBASE", "MV"):
            raise InputError("%s: {%s} needs a Generator" % (where, key))
        return str(_num_of(net, net.find(m.group(1), where)))
    return _PH.sub(rep, text)


def write_dyr(net, inp, path):
    recs = dyr_models(net, inp)
    L = ["/ SMIB %s -- written by smib_build.py on %s" % (net.name, time.strftime("%Y-%m-%d %H:%M")),
         "/ generic WECC models: placeholders until the vendor's models (parameters: Dyr_Params)"]
    gen_of = dict((g["name"], g) for g in net.gens)
    for gname, model, vals in recs:
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
    for r in inp["dyr_records"]:
        w = r["_where"]
        text = _txt(r.get("record"))
        if not text:
            continue
        gname = _txt(r.get("generator"))
        g = None
        if gname:
            g = next((x for x in net.gens if x["name"].upper() == gname.upper()), None)
            if g is None:
                raise InputError("%s: Generator %r -- no such generator" % (w, gname))
        rec = fill_placeholders(net, text, g, w).strip()
        if not rec.endswith("/"):
            rec += " /"
        L.append(rec)
    _write_text(path, "\n".join(L))
    return recs


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
            "ports": [{"node": _node(node), "pick": ["@dim3", "@first"]}]}


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

    th = net.thev
    comps.append(_source("IB_SRC", "IB", net.kv("IB"), net.bus["IB"]["vm"], 0.0, f, x[0], y0, "IB"))
    nxt = "IB"
    if th["R"] > 1e-9:
        comps.append(_rlc("GRID_R", "R", th["R"], "IB", "GRID_RL", nx(), y0, "grid Thevenin R"))
        nxt = "GRID_RL"
    poi_m = "POI_M" if c["meter"] else "POI"
    comps.append(_rlc("GRID_L", "L", th["X"] / w, nxt, poi_m, nx(), y0, "grid Thevenin L (X %.4f ohm)" % th["X"]))
    if c["meter"]:
        comps.append({"tag": "POI_METER", "kind": "METER", "defn": ["master:multimeter"], "x": nx(), "y": y0,
                      "note": "P, Q, Vrms at the POI (grid side)",
                      "params": [_pp("measure P", ["MeasP"], None, ["YES"]), _pp("P signal", ["P"], "P_POI"),
                                 _pp("measure Q", ["MeasQ"], None, ["YES"]), _pp("Q signal", ["Q"], "Q_POI"),
                                 _pp("measure Vrms", ["RMS", "MeasRMS", "MeasVrms"], None, ["YES"]),
                                 _pp("Vrms signal", ["Vrms"], "V_POI"),
                                 _pp("base kV", ["BaseV"], "%s [kV]" % _g(net.kv("POI")))],
                      "ports": [{"node": "POI_M", "pick": ["@left", "@first"]},
                                {"node": "POI", "pick": ["@right", "@second"]}]})

    def line(ln, xx, yy):
        n = ln["count"]
        a, b = ln["fr"], ln["to"]
        mid = ln["name"] + "_RL"
        out = []
        if ln["R"] > 1e-9:
            out.append(_rlc(ln["name"] + "_R", "R", ln["R"] / n, a, mid, xx, yy, "%s R" % ln["name"]))
        else:
            mid = a
        out.append(_rlc(ln["name"] + "_L", "L", ln["X"] / n / w, mid, b, xx + 10, yy, "%s L" % ln["name"]))
        if ln["B"] > 0:
            cc = ln["B"] * n / w / 2.0                          # uS / (rad/s) = uF; half at each end
            out.append(_rlc(ln["name"] + "_C1", "C", cc, a, "GND", xx, yy + 8, "%s C/2 (%s end)" % (ln["name"], a)))
            out.append(_rlc(ln["name"] + "_C2", "C", cc, b, "GND", xx + 10, yy + 8,
                            "%s C/2 (%s end)" % (ln["name"], b)))
        return out

    for ln in net.lines:
        if ln["kind"] in ("GENTIE", "TIE"):
            comps.extend(line(ln, nx(14), y0))
            nx(6)
    for t in net.tx2:
        if t["kind"] != "MPT":
            continue
        comps.append(_tx2_comp(t, net, nx(14), y0))
    for t in net.tx3:
        comps.append(_tx3_comp(t, net, nx(16), y0))
    yy = y0 + 22
    dets = detailed_collectors(net, shift)
    drawn = set(d["name"] for d in dets)
    covered = set(gn for d in dets for gn in d["gens"])
    for ln in net.lines:
        if ln["kind"] == "COLLECTOR" and ln["to"].startswith("C:") and ln["name"] not in drawn:
            comps.extend(line(ln, 20, yy))
            yy += 20
    slots = []
    for g in net.gens:
        if g["name"] in covered:
            continue
        xx = 20
        ln = next((l for l in net.lines if l["kind"] == "COLLECTOR" and l["to"] == g["mv"] and l["name"] ==
                   g["name"] + "-COLL"), None)
        if ln:
            comps.extend(line(ln, xx, yy))
        sl = _gen_slot(net, g, shift)
        if g["gsu"] and sl["at"] == "LV":
            comps.append(_tx2_comp(g["gsu"], net, xx + 26, yy))
        comps.append(_inverter_comp(g, sl, net, xx + 42, yy))
        slots.append(sl)
        yy += 20
    for d in dets:
        got, yy = _detailed_comps(net, d, 20, yy)
        comps.extend(got)
        slots.extend(d["slots"])
    net.pscad_slots, net.pscad_det = slots, dets
    xx = 20
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
        if cm["tag"] in seen:
            raise InputError("PSCAD: two components are named %s -- rename a load / shunt" % cm["tag"])
        seen.add(cm["tag"])
    comps, net.pscad_groups = pscad_layout(comps)
    return comps


PSCAD_W = 14          # a component's room along its row (grid units)
PSCAD_H = 14          # from a row to what hangs below it, and between those
PSCAD_GAP = 4         # from a node to the things hanging off it
PSCAD_PAD = 6         # each side of a run of R, L, C: room for its breakouts where PSCAD's R, L, C are single-phase


def pscad_layout(comps, root="IB"):
    """A single-line drawing, left to right from the infinite source: at each node its main branch carries on
       along the row, what hangs off it (shunts, loads, a GSU with its inverter) goes below it, its other branches
       below those. Sets every component's x, y (grid units) and its upstream node ("in"); returns the components
       in drawing order and the runs of R, L, C -- [{kind series | shunt, members [(tag, in, out)], a, b}] -- that
       the build script draws three times over, between breakouts, where PSCAD's R, L and C are single-phase."""
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

    def chain(c, frm, x, y):
        """c hanging off node frm (at x) on row y, then what lies beyond it -> (bottom, right)"""
        x0 = x + (PSCAD_PAD if rlc(c) and frm and not mid(frm) else 0)
        put(c, x0 + PSCAD_W // 2, y, frm)
        right, bottom = x0 + PSCAD_W, y
        far = [n for n in nodes[id(c)] if n != frm]
        if far:
            x1 = right + (PSCAD_PAD if rlc(c) and not mid(far[0]) else 0)
            b, r = node(far[0], x1, y)
            bottom, right = max(bottom, b), max(right, r)
            for n2 in far[1:]:                   # a three-winding transformer's tertiary: below
                b, r = node(n2, x1, bottom + PSCAD_H)
                bottom, right = max(bottom, b), max(right, r)
        return bottom, right

    def node(n, x, y):
        kids = [c for c in at[n] if id(c) not in placed]
        chains = [c for c in kids if len(nodes[id(c)]) >= 2]
        big = [c for c in chains if not small(c, n)]
        main = big[0] if big else (chains[0] if chains else None)
        if main is None:
            main = next((c for c in kids if len(nodes[id(c)]) == 1 and
                         not any(p["node"] == "GND" for p in c["ports"])), None)
        drops = [c for c in kids if c is not main and (len(nodes[id(c)]) == 1 or small(c, n))]
        others = [c for c in kids if c is not main and all(c is not d for d in drops)]
        bottom, right, yd, wide = y, x, y, 0
        for c in drops:
            yd += PSCAD_H
            b, r = chain(c, n, x + PSCAD_GAP, yd)
            bottom, right, wide = max(bottom, b, yd), max(right, r), max(wide, r - x)
        if main is not None:                     # its trunks clear of what hangs below this node
            b, r = chain(main, n, x + max(0, wide + 2 - PSCAD_W // 2), y)
            bottom, right = max(bottom, b), max(right, r)
        for c in others:
            yo = bottom + PSCAD_H
            b, r = chain(c, n, x + PSCAD_GAP, yo)
            bottom, right = max(bottom, b, yo), max(right, r)
        return bottom, right

    x0, y0 = 20, 10
    for c in [c for c in at[root] if len(nodes[id(c)]) == 1]:
        if not any(p["node"] == "GND" for p in c["ports"]):
            put(c, x0 - PSCAD_W // 2, y0, None)          # the infinite source, left of its bus
    bottom, _r = node(root, x0, y0)
    xl = 20
    for c in comps:                                     # anything not reached from the source: a row of its own
        if id(c) not in placed:
            put(c, xl, bottom + 2 * PSCAD_H, None)
            xl += PSCAD_W
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


def _inverter_comp(g, sl, net, x, y):
    """The inverter in its slot: the generator's vendor model (UDM), or a stand-in source holding the load flow."""
    ps = g["pscad"]
    what = "%s units, P %.3f MW, Q %.3f MVAr, V %.5f pu at %.4f deg (PSCAD angle)" % (
        _g(sl["units"]), sl["p"], sl["q"], sl["vm"], sl["ang"])
    if ps["definition"]:
        sl["tag"], sl["model"] = sl["name"] + "_INV", ps["definition"]
        return {"tag": sl["tag"], "kind": "VENDOR", "defn": [ps["definition"]], "library": ps["library"],
                "gen": g["name"], "slot": sl, "x": x, "y": y, "params": [],
                "note": "inverter: vendor model %s -- %s" % (ps["definition"], what),
                "ports": [{"node": _node(sl["node"]), "pick": ([ps["ac_port"]] if ps["ac_port"] else []) +
                           ["@dim3", "@first"]}]}
    sl["tag"], sl["model"] = sl["name"] + "_SRC", "stand-in source"
    c = _source(sl["tag"], sl["name"], sl["kv"], sl["vm"], sl["ang"], net.f, x, y, sl["node"])
    c["note"] += (" -- stand-in for the inverter (%s): holds the load flow; your UDM goes on node %s"
                  % (what, _node(sl["node"])))
    c["gen"], c["slot"] = g["name"], sl
    return c


def detailed_collectors(net, shift):
    """The collectors PSCAD draws section by section (PSCAD collector DETAILED), each one solved."""
    out = []
    for cname, res in net.colls.items():
        if res.get("pscad") != "DETAILED":
            continue
        gens = [g for g in net.gens if g["name"].upper() in res["N"]]
        gone = set()
        for g in gens:
            gone.update((g["mv"], g["bus"]))
        if res["own"] is None:
            gone.add("C:" + cname)
            bad = ["%s (no units on its feeders)" % g["name"] for g in net.gens
                   if g["connect"] == "C:" + cname and g not in gens]
            bad += ["%s (not straight on it: a collector of its own)" % g["name"] for g in gens
                    if g["mv"] != "C:" + cname]
            if bad:
                net.notes.append("NOTE: PSCAD collector %s DETAILED: %s -- PSCAD draws its equivalent"
                                 % (cname, ", ".join(bad)))
                continue
        on = sorted(set(x["bus"] for x in net.loads + net.shunts if x["bus"] in gone))
        if on:
            net.notes.append("NOTE: PSCAD collector %s DETAILED: a load or shunt on %s, which the detailed feeders "
                             "replace -- PSCAD draws the equivalent" % (cname, ", ".join(on)))
            continue
        out.append(detailed_collector(net, cname, res, gens, shift))
    return out


def detailed_collector(net, cname, res, gens, shift):
    """Collector `cname` section by section -- identical feeder copies lumped (R and X over the copies, B times
       them) -- with the units at each node behind a GSU of their own (their generator's, scaled to them), solved
       with its root at the load-flow voltage and every unit at its generator's P and Q."""
    w = "PSCAD collector %s" % cname
    sb = net.sb
    sub = Net(net.name, sb, net.f)
    root = res["root"]
    sub.add_bus(root, net.kv(root), w)
    gby = dict((g["name"].upper(), g) for g in gens)
    rows, slots, nodes = [], [], {}
    for k, fd in enumerate(res["feeders"]):
        for sc in fd["sections"]:
            nodes[(k, sc["num"])] = sub.add_bus("%s_%s_S%d" % (_node(cname), _node(fd["name"]), sc["num"]),
                                                res["kv"], w)
    for k, fd in enumerate(res["feeders"]):
        n = fd["copies"]
        row = []
        for sc in fd["sections"]:
            a = root if sc["from"] == 0 else nodes[(k, sc["from"])]
            b = nodes[(k, sc["num"])]
            ln = add_line(sub, a, b, {"r": sc["z"].real / n, "x": sc["z"].imag / n, "b": sc["b"] * n,
                                      "R": sc["R"] / n, "X": sc["X"] / n, "B": sc["B"] * n, "count": 1, "rate": 0.0,
                                      "kv": res["kv"], "km": 0.0}, b, "SECTION", w)
            here = []
            for G, cnt in sc["units"].items():
                g = gby[G]
                share = float(cnt * n) / res["N"][G]
                sl = {"name": "%s_%s_S%d" % (_node(g["name"]), _node(fd["name"]), sc["num"]), "gen": g["name"],
                      "units": cnt * n, "mva": cnt * n * g["unit"]["mva"], "mv": b, "copies": n,
                      "s": complex(g["p"], g["q"]) * share, "at": g["pscad"]["at"] if g["gsu"] else "LV"}
                if g["gsu"]:
                    lv = sub.add_bus(sl["name"] + "_LV", net.kv(g["bus"]), w)
                    d = dict((key, g["gsu"].get(key)) for key in ("kind", "z_pct", "x_r", "load_loss_kw", "kv1", "kv2",
                                                                  "tap", "vector", "nll_pct", "im_pct", "unit_mva"))
                    d.update({"name": sl["name"] + "_GSU", "hv": b, "lv": lv, "mva": g["gsu"]["mva"] * share,
                              "units": g["gsu"]["units"] * share, "rate": g["gsu"]["mva"] * share, "count": 1,
                              "where": w})
                    sl["gsu"], sl["lv"] = add_tx2(sub, d), lv
                else:
                    sl["gsu"], sl["lv"] = None, b
                here.append(sl)
            row.append((ln, here))
            slots.extend(here)
        rows.append((fd, row))
    roles = list(sub.bus)
    idx, Y, N = ybus(sub, roles)
    bs = net.bus[root]
    v0 = cmath.rect(bs["vm"], math.radians(bs["va"]))
    sinj = [0j] * N
    for sl in slots:
        sinj[idx[sl["lv"]]] += sl["s"] / sb
    V, ok, _it = newton(Y, idx[root], v0, sinj, [v0] * N)
    flows(sub, idx, V, Y)
    fl = dict((f["name"], f) for f in sub.op["flows"])
    for sl in slots:
        g = gby[sl["gen"].upper()]
        if sl["at"] == "MV" and sl["gsu"]:
            node, s_at = sl["mv"], -fl[sl["gsu"]["name"]]["s1"]
        else:
            node, s_at = sl["lv"], sl["s"]
        v = V[idx[node]]
        sl.update({"node": node, "kv": sub.kv(node), "vm": abs(v), "p": s_at.real, "q": s_at.imag,
                   "s_inv": sl["s"], "v_inv": abs(V[idx[sl["lv"]]]),
                   "ang": math.degrees(cmath.phase(v)) + shift.get(g["bus"] if node == sl["lv"] and sl["gsu"]
                                                                   else root, 0.0)})
    s_root = sum((f["s1"] for f in sub.op["flows"] if f["kind"] == "SECTION" and f["a"] == root), 0j)
    eq = next((f for f in net.op["flows"] if f["kind"] == "COLLECTOR" and f["a"] == root and
               f["name"] == (cname if res["own"] is None else cname + "-COLL")), None)
    if not ok:
        net.notes.append("CHECK: PSCAD collector %s DETAILED: its load flow did not converge -- the stand-in "
                         "sources' voltages are approximate" % cname)
    return {"name": cname, "root": root, "sub": sub, "rows": rows, "slots": slots, "gens": [g["name"] for g in gens],
            "s_root": s_root, "s_eq": eq["s1"] if eq else None, "ok": ok}


def _detailed_comps(net, d, x0, y0):
    """PSCAD components of a detailed collector: per feeder a row of sections (R, L; the charging as one
       capacitor per node), and at each node with units a GSU and the inverter per generator."""
    w = 2.0 * math.pi * net.f
    comps = []
    cap = collections.OrderedDict()
    for _fd, row in d["rows"]:
        for ln, _here in row:
            for nd in (ln["fr"], ln["to"]):
                cap[nd] = cap.get(nd, 0.0) + ln["B"] / w / 2.0          # uS / (rad/s) = uF; half at each end
    gby = dict((g["name"], g) for g in net.gens)
    if cap.get(d["root"], 0.0) > 0:
        comps.append(_rlc("%s_ROOT_C" % _node(d["name"]), "C", cap[d["root"]], d["root"], "GND", x0, y0 + 8,
                          "%s: the first sections' C/2 at %s" % (d["name"], d["root"])))
    y = y0
    for fd, row in d["rows"]:
        x = x0 + 8
        tall = 1
        for ln, here in row:
            a, b = ln["fr"], ln["to"]
            mid = b + "_RL"
            note = "feeder %s%s section %s" % (fd["name"], (" (x %d lumped)" % fd["copies"]) if fd["copies"] > 1
                                               else "", b.rsplit("_S", 1)[-1])
            if ln["R"] > 1e-9:
                comps.append(_rlc(b + "_R", "R", ln["R"], a, mid, x, y, note + " R"))
            else:
                mid = a
            comps.append(_rlc(b + "_L", "L", ln["X"] / w, mid, b, x + 10, y, note + " L"))
            if cap.get(b, 0.0) > 0:
                comps.append(_rlc(b + "_C", "C", cap[b], b, "GND", x + 20, y + 8, "C/2 of the sections at " + b))
            for j, sl in enumerate(here):
                g = gby[sl["gen"]]
                yj = y + 18 + 14 * j
                if sl["gsu"] and sl["at"] == "LV":
                    comps.append(_tx2_comp(sl["gsu"], net, x + 20, yj))
                comps.append(_inverter_comp(g, sl, net, x + 36, yj))
            tall = max(tall, len(here))
            x += 48
        y += 18 + 14 * tall + 8
    return comps, y


def phase_shifts(net):
    """The angle PSCAD's transformers add on the way from the infinite bus to every bus (deg); PSS/E has none."""
    out = {"IB": 0.0}
    edges = collections.defaultdict(list)
    for ln in net.lines:
        edges[ln["fr"]].append((ln["to"], 0.0))
        edges[ln["to"]].append((ln["fr"], 0.0))
    for t in net.tx2:
        s = t["vg"]["shift"][1]
        edges[t["hv"]].append((t["lv"], s))
        edges[t["lv"]].append((t["hv"], -s))
    for t in net.tx3:
        s = t["vg"]["shift"]
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


def pscad_spec(net, inp, comps):
    params = collections.defaultdict(list)
    for r in inp["pscad_params"]:
        gname = _txt(r.get("generator"))
        g = next((x for x in net.gens if x["name"].upper() == (gname or "").upper()), None)
        if g is None:
            raise InputError("%s: Generator %r -- no such generator" % (r["_where"], gname))
        if not g["pscad"]["definition"]:
            net.notes.append("NOTE: %s: PSCAD_Params for %s, which has no PSCAD definition -- not used"
                             % (r["_where"], g["name"]))
        params[g["name"]].append((_txt(r.get("parameter")), _txt(r.get("value")) or "", r["_where"]))
    for cm in comps:
        if cm["kind"] == "VENDOR":
            vals = slot_values(cm["slot"], net)
            cm["params"] = [_pp(p, [p], _fill_pscad(v, vals, w)) for p, v, w in params.get(cm["gen"], [])]
    c = net.ctl["pscad"]
    libs = list(c["libraries"]) + [cm["library"] for cm in comps if cm.get("library")]
    return {"case": _safe_name(net.name, 30), "written": time.strftime("%Y-%m-%d %H:%M"),
            "settings": {"time_duration": c["duration_s"], "time_step": c["time_step_us"],
                         "sample_step": c["plot_step_us"], "description": ("SMIB %s" % net.name)[:60]},
            "libraries": [l for k, l in enumerate(libs) if l and l not in libs[:k]],
            "slots": [{"tag": sl["tag"], "node": _node(sl["node"]), "units": sl["units"], "p": sl["p"], "q": sl["q"],
                       "vm": sl["vm"], "ang": sl["ang"], "model": sl["model"]} for sl in net.pscad_slots],
            "groups": [{"kind": gr["kind"], "a": _node(gr["a"]), "b": _node(gr["b"]),
                        "members": [[t, _node(i), _node(o)] for t, i, o in gr["members"]]} for gr in net.pscad_groups],
            "components": [dict((k, v) for k, v in cm.items() if k not in ("value", "unit", "slot", "gen"))
                           for cm in comps]}


_BUILD_PSCAD = r'''# -*- coding: utf-8 -*-
"""Builds the PSCAD case of the SMIB model @NAME@ (written by smib_build.py on @DATE@).

   Run it with a Python that has PSCAD's automation library, mhi.pscad (PSCAD 5 installs it; or
   py -m pip install mhi.pscad):
       py -3 @FILE@
   It uses the PSCAD that is running (or starts one), loads the user-model libraries, creates the
   case in this folder under a free name (nothing is overwritten), places every component, sets its
   parameters, draws the single-line diagram with wires (R, L and C three times over, between breakouts,
   where they are single-phase in this PSCAD), names each node and saves
   the case. Its log, @NAME@_build_pscad_<date_time>.txt, lists every parameter set (as PSCAD reads
   it back), everything it could not do (CHECK:) and each component type's own parameter names, so
   anything left can be set by hand. The values themselves are in @NAME@_PSCAD_DATA.txt.
"""
from __future__ import print_function

import os
import sys
import json
import time
import traceback

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
    """[(name, x, y, dim)] of the component's electrical ports (grid units, after rotation)."""
    try:
        ports = cmp.ports()
    except Exception:
        return []
    out = []
    for name, p in ports.items():
        try:
            if int(p[4]) == 3:
                out.append((str(name), int(p[0]), int(p[1]), int(p[3])))
        except Exception:
            pass
    return out


def pick(ports, picks, taken):
    free = [p for p in ports if p[0] not in taken]
    allp = sorted(ports, key=lambda p: (p[1], p[2]))
    for k in picks:
        if not k:
            continue
        if not k.startswith("@"):
            for p in free:
                if p[0].upper() == k.upper():
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


def set_params(cmp, items, log, quiet=False):
    try:
        have = cmp.parameters() or {}
    except Exception as e:
        log("    CHECK: its parameters cannot be read (%s)" % e)
        return
    up = dict((k.upper(), k) for k in have)
    for it in items:
        names = [n for n in it["names"] if n in have] or [up[n.upper()] for n in it["names"] if n.upper() in up]
        if not names:
            if it.get("need", True):
                log("    CHECK: %-26s no parameter %s -- set it by hand: %s"
                    % (it["what"], "/".join(it["names"]), it["value"] if it["value"] is not None
                       else "/".join(it["choice"] or [])))
            continue
        name = names[0]
        val = it["value"]
        if it.get("choice"):
            val = choice_value(cmp, name, it["choice"])
            if val is None:
                log("    CHECK: %-26s %s: no choice like %s (its choices: %s)"
                    % (it["what"], name, "/".join(it["choice"]), _range(cmp, name)))
                continue
        try:
            cmp.parameters(parameters={name: val})
            got = (cmp.parameters() or {}).get(name)
            if not quiet:
                log("    %-28s %-8s = %-22s read back: %s" % (it["what"], name, val, got))
        except Exception as e:
            log("    CHECK: %-26s %s = %r refused: %s" % (it["what"], name, val, e))


def _range(cmp, name):
    try:
        return sorted(str(x) for x in cmp.range(name))
    except Exception:
        return "?"


DY = 4                     # single-phase R, L, C: the phases' rows, top to bottom (grid units)
TRACE = []                 # SMIB_PSCAD_TRACE=<file>: what was drawn, for smib_build's own tests
NO_BREAKOUT = []           # why this PSCAD has no breakout, once found


def _trace(kind, **kw):
    if os.environ.get("SMIB_PSCAD_TRACE"):
        kw["kind"] = kind
        TRACE.append(kw)


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


def _delete(canvas, cmp):
    try:
        cmp.delete()
    except Exception:
        try:
            canvas.delete(cmp)
        except Exception:
            pass


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


def ground(canvas, x, y, log):
    """A ground on the port at (x, y): below it, joined by a wire where its own port is not right there."""
    try:
        g = canvas.create_component("master:ground", x, y + 2)
        gp = eports(g)
        if gp and (gp[0][1], gp[0][2]) != (x, y):
            wire(canvas, (x, y), (x, gp[0][2]), (gp[0][1], gp[0][2]))
        _trace("comp", role="ground", ports=gp)
    except Exception as e:
        log("CHECK: no ground at (%d, %d): %s" % (x, y, e))


def label(canvas, x, y, name):
    """The node's name (a node label) on the port at (x, y)."""
    try:
        lb = canvas.create_component("master:nodelabel", x, y, Name=name)
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
    for orient in range(8):
        try:
            b = canvas.create_component("master:breakout", tx - 8 if right else tx + 8, ty, orient)
        except TypeError:
            if orient:
                break
            b = canvas.create_component("master:breakout", tx - 8 if right else tx + 8, ty)
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
            ps = eports(b)
            return b, [p for p in ps if p[3] == 3][0], sorted([p for p in ps if p[3] == 1], key=lambda p: p[2])
        _delete(canvas, b)
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
                cmp = canvas.create_component(used, c["x"], c["y"] + DY * k)
                set_params(cmp, c["params"], log, quiet=True)
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
    points.setdefault(g["a"], []).append((tri[1], tri[2], "breakout", tri[0]))
    _trace("comp", role="breakout", group=g["members"][0][0], side="in", ports=[tri] + one)
    if g["kind"] == "series":
        outs, sx = [rows[k][-1][2] for k in range(3)], oneo[0][1]
        for k in range(3):                           # closing in: the top phase turns first
            wire(canvas, (outs[k][1], outs[k][2]), (sx - 3 + k, outs[k][2]), (sx - 3 + k, oneo[k][2]),
                 (sx, oneo[k][2]))
        points.setdefault(g["b"], []).append((tro[1], tro[2], "breakout", tro[0]))
        _trace("comp", role="breakout", group=g["members"][0][0], side="out", ports=[tro] + oneo)
    else:
        for k in range(3):
            r = rows[k][-1][2]
            points.setdefault("GND", []).append((r[1], r[2], g["members"][0][0], r[0]))
    return added


def join(canvas, points, log):
    """Wires: each node from its leftmost port, along its row to what carries on, and down a trunk to what hangs
       below; a ground on every ground port; the node's name on its leftmost port."""
    for node in sorted(points):
        pts = sorted(set((p[0], p[1]) for p in points[node]))
        if node == "GND":
            for (x, y) in pts:
                ground(canvas, x, y, log)
            continue
        if len(pts) < 2:
            log("CHECK: node %s has one connection only" % node)
            continue
        ax, ay = pts[0]
        try:
            prev = (ax, ay)
            for q in [q for q in pts[1:] if q[1] == ay]:
                wire(canvas, prev, q)
                prev = q
            off = [q for q in pts[1:] if q[1] != ay]
            if off:
                ys = sorted(set([ay] + [q[1] for q in off]))
                for y1, y2 in zip(ys, ys[1:]):
                    wire(canvas, (ax, y1), (ax, y2))
                for y in sorted(set(q[1] for q in off)):
                    prev = (ax, y)
                    for q in sorted(q for q in off if q[1] == y):
                        if q != prev:
                            wire(canvas, prev, q)
                        prev = q
        except Exception as e:
            log("CHECK: node %s: no wires (%s) -- node labels instead" % (node, e))
            for (x, y) in pts:
                label(canvas, x, y, node)
            continue
        if not node.endswith("_RL"):
            label(canvas, ax, ay, node)


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
    seen_defn = {}
    made = {}                                         # tag -> (component, definition, [(node, port)])
    spec_of = dict((c["tag"], c) for c in SPEC["components"])
    for c in SPEC["components"]:
        log("")
        log("%s  (%s)  %s" % (c["tag"], c["kind"], c.get("note", "")))
        defns = list(c["defn"])
        if c.get("library") and ":" not in defns[0]:
            lib = libs.get(os.path.normcase(os.path.abspath(c["library"])))
            if lib:
                defns.insert(0, "%s:%s" % (lib, defns[0]))
        cmp, used = None, None
        for d in defns:
            try:
                cmp = canvas.create_component(d, c["x"], c["y"])
                used = d
                if cmp is not None:
                    break
            except Exception as e:
                log("    (%s: %s)" % (d, e))
        if cmp is None:
            log("    CHECK: not created (%s) -- place it by hand and join it to %s"
                % (" / ".join(defns), ", ".join(p["node"] for p in c["ports"])))
            continue
        log("    %s at (%d, %d)" % (used, c["x"], c["y"]))
        if used not in seen_defn:
            try:
                have = cmp.parameters() or {}
                seen_defn[used] = sorted(have)
                log("    its parameters: %s" % ", ".join("%s=%s" % (k, have[k]) for k in sorted(have)))
            except Exception:
                seen_defn[used] = []
            try:
                log("    its bounds %s, location %s" % (tuple(cmp.bounds), tuple(cmp.location)))
            except Exception:
                pass
        set_params(cmp, c["params"], log)
        ports = eports(cmp)
        if any(p["node"] for p in c["ports"]) and len(ports) > len(c["ports"]) and "View" in str(seen_defn[used]):
            log("    (%d electrical ports: %s)" % (len(ports), ", ".join(p[0] for p in ports)))
        taken, picked = set(), []
        nodes = [p["node"] for p in c["ports"]]
        if c["kind"] in ("R", "L", "C") and len(ports) == 2 and len(nodes) == 2:
            first = c.get("in") if c.get("in") in nodes else [n for n in nodes if n != "GND"][0]
            lr = sorted(ports, key=lambda p: (p[1], p[2]))          # its upstream node on its left port
            picked = [(first, lr[0]), (([n for n in nodes if n != first] or nodes)[0], lr[1])]
        else:
            for p in c["ports"]:
                got = pick(ports, p["pick"], taken)
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
                points.setdefault(node, []).append((q[1], q[2], c["tag"], q[0]))
    log("")
    join(canvas, points, log)
    made = len(made)
    log("nodes joined by wires: %d" % len([n for n in points if n != "GND"]))
    log("")
    log("inverters -- a vendor model (UDM) goes on these nodes; a stand-in source holds the load flow until then:")
    for s in SPEC.get("slots", []):
        log("    %-24s node %-24s %5s units  P %9.3f MW  Q %8.3f MVAr  V %.5f pu at %9.4f deg  %s"
            % (s["tag"], s["node"], s["units"], s["p"], s["q"], s["vm"], s["ang"], s["model"]))
    prj.save()
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
    optional = ("base kV", "magnitude (pu)", "name", "view", "no-load losses (pu)", "base frequency")
    for cm in spec["components"]:
        for it in cm["params"]:                     # a missing optional one is not listed as a CHECK
            it["need"] = cm["kind"] == "VENDOR" or not (cm["kind"] == "METER" or it["what"] in optional or
                                                         it["what"].startswith("source impedance"))
    path = os.path.join(out, net.name + "_build_pscad.py")
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
            sl["tag"], _node(sl["node"]), _g(sl["units"]), sl["p"], sl["q"], sl["vm"], sl["ang"], sl["model"],
            "  (GSU inside: on its HV side)" if sl["at"] == "MV" else ""))
    for d in net.pscad_det:
        nsec = sum(len(row) for _fd, row in d["rows"])
        L.append(ind + "Collector %s DETAILED: %d feeder row(s) (identical copies lumped), %d sections, %d inverters; "
                       "at %s the feeders take %s" % (d["name"], len(d["rows"]), nsec, len(d["slots"]), d["root"],
                                                      _c(d["s_root"])))
        if d["s_eq"] is not None:
            ds = d["s_root"] - d["s_eq"]
            L.append(ind + "  the WECC equivalent takes %s there in the load flow (PSS/E): %+.4f MW, %+.4f MVAr apart"
                           % (_c(d["s_eq"]), ds.real, ds.imag))
    return L


def pscad_data_text(net, comps, spec):
    L = ["PSCAD DATA -- SMIB %s   (written by smib_build.py on %s)" % (net.name, spec["written"]),
         "=" * 100,
         "Every value the build script sets, in PSCAD's units. Transformers: pu on their own MVA and winding",
         "voltages (V1 is the tapped HV voltage). Lines and collector equivalents: pi sections (C/2 at each end);",
         "detailed collectors: each section's R and L, its charging as C/2 at each end (one capacitor per node).",
         "The drawing: a single-line diagram with wires (R, L, C three times over, between breakouts, where PSCAD's",
         "are single-phase); the node names below are the labels on the wires.",
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
    L = ["SMIB MODEL  %s" % net.name,
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
    else:
        L.append("   plant controller (.dyr): REPC_A -- a %s on each generator that holds the POI" % (
            "REPCAU1" if c.get("regc_repc") == "USRMDL" else "REPCA1"))
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
    for ln in net.lines:
        L.append("   %-12s %-9s %s - %s  %d circuit(s)%s: R %.5f X %.5f ohm B %.3f uS each = r %.6f x %.6f b %.6f pu"
                 % (ln["name"], ln["kind"], ln["fr"], ln["to"], ln["count"],
                    (", %.3f km" % ln["km"]) if ln.get("km") else "", ln["R"], ln["X"], ln["B"], ln["r"], ln["x"],
                    ln["b"]))
    for t in net.tx2:
        L.append("   %-12s %-9s %s - %s  %s%.2f MVA %.4g/%.4g kV tap %.4f %s: Z %.4f%% X/R %s (own base) -> r %.6f "
                 "x %.6f pu on %.2f MVA" % (t["name"], t["kind"], t["hv"], t["lv"],
                                            ("%d x " % t["count"]) if t["count"] > 1 else
                                            ("%d units x %.3f = " % (t["units"], t["unit_mva"]) if t.get("units")
                                             else ""), t["mva"], t["kv1"], t["kv2"], t["tap"], t["vg"]["text"],
                                            100.0 * math.hypot(t["r_own"], t["x_own"]),
                                            _g(t["x_own"] / t["r_own"], 4) if t["r_own"] else "inf", t["r"], t["x"],
                                            t["mva"]))
    for t in net.tx3:
        L.append("   %-12s MPT(3W)  %s - %s - %s  %.4g/%.4g/%.4g kV tap %.4f %s" % (
            t["name"], t["hv"], t["lv"], t["tv"], t["kv1"], t["kv2"], t["kv3"], t["tap"], t["vg"]["text"]))
        for p, lab in (("12", "HV-LV"), ("31", "HV-TV"), ("23", "LV-TV")):
            r, x, base = t["own"][p][3], t["own"][p][4], t["own"][p][2]
            L.append("        %s Z %.4f%% on %.2f MVA (r %.6f x %.6f)" % (lab, 100.0 * math.hypot(r, x), base, r, x))
    for ld in net.loads:
        L.append("   load  %-10s at %-10s %.3f MW %.3f MVAr at 1 pu (constant impedance)" % (ld["name"], ld["bus"],
                                                                                         ld["mw"], ld["mvar"]))
    for sh in net.shunts:
        L.append("   shunt %-10s at %-10s %.3f MVAr at 1 pu" % (sh["name"], sh["bus"], sh["mvar"]))
    L += ["", "4. WECC COLLECTOR EQUIVALENTS  (Zeq = sum(Zi Wi^2) / W^2, Beq = sum(Bi); Wi = MW downstream)"]
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
                    sct["num"], sct["from"], sct["cable"], sct["length"], sct["unit"], sct["R"], sct["X"], sct["B"],
                    "+".join(("%d %s" % (n, g)) if many else str(n) for g, n in sct["units"].items()) or "0",
                    sct["W"]))
        L.append("     EQUIVALENT: R %.5f X %.5f ohm, B %.3f uS  =  r %.7f x %.7f b %.7f pu (%.0f MVA, %.4g kV)"
                 % (res["R"], res["X"], res["B"], res["r"], res["x"], res["b"], sb, res["kv"]))
        ck = res["check"]
        d, e = ck["detailed"], ck["equivalent"]
        dp = (e["p"] - d["p"]) / d["p"] * 100.0 if d["p"] else 0.0
        L.append("     WECC check, every unit at full output, collector bus 1.0 pu: losses detailed %.4f MW %.4f "
                 "MVAr, equivalent %.4f MW %.4f MVAr (P %+.2f %%)" % (d["p"], d["q"], e["p"], e["q"], dp))
        L.append("       detailed feeders %.5f .. %.5f pu; the equivalent's far end %.5f pu%s"
                 % (d["vmin"], d["vmax"], e["v"], "" if d["ok"] and e["ok"] else "  *** a sweep did not converge"))
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
                ("; REAX4BU1: %.4f of the plant's Q, %.4f of its P" % g["pc_share"]) if "REAX4BU1" in g.get("dyr", [])
                else ""))
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
        L.append("   %-6s %-8s ICON: %s" % (gname, model, " ".join("%s=%s" % (p, v) for p, v in vals[:nic]) or "-"))
        cons = ["%s=%s" % (p, _g(float(v))) for p, v in vals[nic:]]
        for k in range(0, len(cons), 8):
            L.append("   %-6s %-8s %s %s" % ("", "", "CON:" if k == 0 else "    ", "  ".join(cons[k:k + 8])))
    if inp["dyr_records"]:
        L.append("   + %d record(s) of your own (Dyr_Records)" % len([r for r in inp["dyr_records"]
                                                                       if _txt(r.get("record"))]))
    L += ["", "8. PSCAD: %d components -- the values are in %s; build the case with %s" % (
        len(comps), os.path.basename(files["pscad_data"]), os.path.basename(files["pscad_build"]))]
    L += pscad_slot_lines(net, "   ")
    L += ["", "9. FILES"]
    for k in ("raw", "dyr", "pscad_build", "pscad_data", "json", "xlsx"):
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
    path = os.path.join(out, net.name + "_PSSE_CHECK.txt")
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
                if os.path.isfile(os.path.join(out, net.name + ext)):
                    files[k] = os.path.join(out, net.name + ext)
            return log
    log = FileLog(path)
    log("PSS/E CHECK -- SMIB %s   %s" % (net.name, time.strftime("%Y-%m-%d %H:%M")))
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
    sav = os.path.join(out, net.name + ".sav")
    ie = _ie(psspy.save(_new(sav)))
    log("saved %s: %s" % (os.path.basename(sav), "ok" if ie in (0, None) else "*** ierr %s" % ie))
    if ie in (0, None):
        files["sav"] = sav
    ver = (PSSE_RUNNING or (PSSE_VERSION or 0, 0))[0]
    raw2 = os.path.join(out, "%s_psse%d.raw" % (net.name, ver))
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
    snp = os.path.join(out, net.name + ".snp")
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
    outf = os.path.join(work, net.name + "_flat.out")
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
def read_input(path):
    if path.lower().endswith(".json"):
        with io.open(path, encoding="utf-8") as fh:
            inp = json.load(fh)
        for key in ["plant"] + [t[1] for t in TABLES]:
            inp.setdefault(key, {} if key == "plant" else [])
        for sheet, key, cols in TABLES:             # a row whose first value starts with # is skipped, as in Excel
            inp[key] = [r for r in inp[key] if not _skip([r.get(c[0]) for c in cols])]
            for k, r in enumerate(inp[key]):
                r.setdefault("_where", "%s row %d" % (sheet, k + 1))
        inp["_source"], inp["_unknown"] = path, []
        return inp
    return parse_workbook(read_xlsx(path), path)


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


def build(src, out_dir=None, check=True, pscad=True):
    """Everything from the workbook `src`; returns (net, files)."""
    log = Log()
    log("[smib] input  %s" % src)
    inp = read_input(src)
    net = build_network(inp, log)
    log("[smib] %d buses, %d generators (%s), %d collector equivalents from feeders"
        % (len(net.bus), len(net.gens), ", ".join("%s %s" % (g["name"], g["type"]) for g in net.gens),
           len(net.colls)))
    operating_point(net, log)
    s = net.op["s_grid"]
    log("[smib] operating point: P at POI %.3f MW, Q %.3f MVAr, POI %.5f pu, infinite bus %.5f pu"
        % (s.real, s.imag, net.bus["POI"]["vm"], net.op["e"]))
    base = out_dir or OUT_DIR or os.path.join(os.path.dirname(os.path.abspath(src)), "SMIB_MODELS")
    out = stem = os.path.join(base, "%s_%s" % (net.name, time.strftime("%Y%m%d_%H%M%S")))
    k = 2
    while os.path.exists(out):                   # a second run within the same second: a folder of its own
        out = "%s_%d" % (stem, k)
        k += 1
    os.makedirs(out)
    files = {"raw": os.path.join(out, net.name + ".raw"), "dyr": os.path.join(out, net.name + ".dyr"),
             "json": os.path.join(out, net.name + "_inputs.json")}
    write_raw(net, files["raw"])
    recs = write_dyr(net, inp, files["dyr"])
    comps, spec, files["pscad_build"] = write_pscad(net, inp, out, log)
    files["pscad_data"] = os.path.join(out, net.name + "_PSCAD_DATA.txt")
    _write_text(files["pscad_data"], pscad_data_text(net, comps, spec))
    clean = dict((k, v) for k, v in inp.items() if not k.startswith("_"))
    _write_text(files["json"], json.dumps(clean, indent=1, sort_keys=True, default=str))
    if src.lower().endswith((".xlsx", ".xlsm")):
        files["xlsx"] = os.path.join(out, os.path.basename(src))
        shutil.copy2(src, _new(files["xlsx"]))
    files["report"] = os.path.join(out, net.name + "_REPORT.txt")
    _write_text(files["report"], report_text(net, inp, recs, comps, files, src))
    log("[smib] written to %s" % out)
    for k in ("raw", "dyr", "report", "pscad_data", "pscad_build"):
        log("         %s" % os.path.basename(files[k]))
    for n in net.notes:
        log("[smib] " + n)
    if check and PSSE_CHECK:
        log("")
        clog = psse_check(net, recs, inp, files, out)
        for ln in clog.review():
            log("[psse] " + ln)
        made = [os.path.basename(files[k]) for k in ("sav", "raw_psse", "snp") if files.get(k)]
        if made:
            log("[psse] PSS/E wrote %s" % ", ".join(made))
    if pscad and PSCAD_BUILD:
        log("")
        pscad_build(files, out, log)
    return net, files


def psse_check_only(out):
    """--psse-check <folder>: PSS/E's check of the models in <folder>, rebuilt from its _inputs.json -- how the
       check runs in a Python that PSS/E has a psspy for, when the one that wrote the models has none."""
    found = glob.glob(os.path.join(out, "*_inputs.json"))
    if len(found) != 1:
        print("[psse] *** %s: %s _inputs.json" % (out, "more than one" if found else "no"))
        return 2
    inp = read_input(found[0])
    quiet = Log(echo=False)
    net = build_network(inp, quiet)
    operating_point(net, quiet)
    recs = dyr_models(net, inp)
    files = {"raw": os.path.join(out, net.name + ".raw"), "dyr": os.path.join(out, net.name + ".dyr")}
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
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    files = [a for a in argv if not a.startswith("-")]
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
