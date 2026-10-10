# -*- coding: utf-8 -*-
"""One-line diagrams (SLD) of the plants in PSS/E cases.

   It reads the NETWORK of each case -- a .sav or a .raw, nothing else: no
   snapshot, no .dyr, no dynamics -- traces every generator to the Point of
   Interconnection (POI) and draws each plant as a one-line diagram, in two
   vector, print-ready files: <case>_sld.svg and <case>_sld.pdf. Each unit's
   path to the POI is listed in <case>_paths.txt.

   WHICH BUS IS THE POI
     POI_BY_CASE, then --poi / POI_BUS when given (a --poi / POI_BUS that is no
     bus of a case is noted, and that case's POI is found as below). Otherwise:
       - a plant package (z7_plant_models.py): the swing bus that holds the
         infinite-bus machine 'IB' (or any machine of MBASE >= IB_MBASE_MIN);
         every other machine is a unit of the plant;
       - a small case (at most MAX_PLANT_BUSES buses): its swing bus;
       - a full-system case: the POI must be given (POI_BUS / --poi).
     The plant is every part of the network behind the POI (reached from a
     POI neighbour without passing through the POI) that holds a unit and
     has at most MAX_PLANT_BUSES buses, with any part hanging off it through
     out-of-service branches. Whatever else meets the POI -- the grid -- is
     drawn as labelled stubs on the POI ("to 531428 NAME 115 kV").

   HOW IT IS DRAWN
     - each unit's path to the POI is the shortest by number of branches
       (lines, zero-impedance ties, two-winding transformers, three-winding
       transformers through their star point), over in-service branches;
       ties are broken by the smaller |X|, then the smaller bus number;
     - the paths form a tree hanging from the POI: the POI on top, every unit
       on the bottom row, each unit's path straight up to the POI. Subtrees
       stand side by side, never overlapping, each as wide as its labels need
       (a tidy tree, Reingold-Tilford style, packed on a 6 pt skyline); a bus
       sits centred over the buses that lead to its units. A bus on no unit's
       path (auxiliary load, capacitor bank) hangs off its bus as a side
       branch; a tertiary sits beside its three-winding transformer; a bus
       section behind a zero-impedance tie, and the taps of a daisy-chained
       feeder (FEEDERS_ACROSS), run across;
     - parallel circuits stand side by side (two-winding ones on one
       connector each; a three-winding twin beside the first, its winding
       down to the bus both feed); a loop, or an out-of-service branch between
       two drawn buses, is a dashed connector routed under the drawing (all
       listed in <case>_paths.txt); out-of-service and de-energised equipment
       is grey and dashed;
     - coloured by voltage level (LEVELS), with a legend and a title block;
       labels are sized from Helvetica's own character widths, and the
       drawing is checked for overlapping labels (the log says so).

   PSS/E'S OWN DIAGRAM (.sld)
     PSS/E draws slider diagrams only in its GUI. Run from a command prompt,
     this script writes DRAW_SLD_IN_PSSE_GUI.py into the run folder: open
     PSS/E, File > Run Automation File > that file. It opens each case, draws
     its buses (growbus), moves each one to its place in this layout through
     PSS/E's sliderPy module -- every call written to a trace file on disk
     before it is made -- and saves <case>.sld in the run folder.
     Run inside the PSS/E GUI (File > Run Automation File > this script), it
     draws the .sld itself the same way. With no CASES and no arguments it
     draws the case open in the GUI; cases it is given are opened there,
     replacing the case open in the GUI (save your work first).

   OUTPUT -- nothing that exists is ever overwritten or deleted, and no case
   is changed: cases are only read (never solved, edited or saved)
     <folder of the first case>\\SLD_<yyyymmdd_hhmmss>\\  (OUT_DIR / --out: inside that folder)
         <case>_sld.svg, <case>_sld.pdf    the one-line diagram
         <case>_paths.txt                  each unit's path to the POI, dashed connections, stubs, notes
         SLD_LOG.txt                       what was read, found and written
         psse_progress.txt                 PSS/E's own progress messages (from a command prompt)
         DRAW_SLD_IN_PSSE_GUI.py           draws the .sld in the PSS/E GUI
         <case>.sld                        (when drawn in the PSS/E GUI, with slider_api.txt and
                                           slider_trace_<case>.txt)

   RUN (the PSS/E Python, 3.4 or 2.7 -- PowerShell or a command prompt)
       python z7_draw_sld.py C:\\cases\\East_Fork_BESS.sav
       python z7_draw_sld.py C:\\cases                 every .sav, and each .raw without a .sav
       python z7_draw_sld.py C:\\cases\\full.sav --poi 531429 --out C:\\SLD
   or set CASES below and run it with no arguments. In the PSS/E GUI:
       File > Run Automation File > z7_draw_sld.py
"""
from __future__ import print_function, division

import os
import io
import re
import sys
import glob
import math
import time
import zlib
import collections
import traceback

_PSSPY_PRELOADED = "psspy" in sys.modules       # inside the PSS/E GUI psspy is up before this script runs

# ============================================================================
# SETTINGS
# ============================================================================
CASES = []                 # .sav / .raw files or folders | [] = the command line; with neither, in the
                           # PSS/E GUI, the case open there
POI_BUS = None             # None = found from each case (plant package; small case: its swing bus) | 531429
POI_BY_CASE = {}           # {"East_Fork_BESS": 531429} -- by case file name (no extension); wins over the rest
GEN_BUSES = []             # [] = every machine behind the POI is a unit | [531620, 531607] = only these buses'
MAX_PLANT_BUSES = 400      # a part behind the POI larger than this is grid, never plant
OUT_DIR = None             # None = SLD_<date_time> beside the first case | r"C:\...\SLD" (SLD_<date_time> inside)
SHOW_FLOWS = True          # MW / Mvar on lines and two-winding transformers, as the case holds them
SHOW_IMPEDANCE = True      # R + jX of lines, X of transformers (pu, system base)
SHOW_VOLTAGES = True       # V (pu) and angle beside each bus
FEEDERS_ACROSS = True      # a daisy-chained feeder (one line on to the next tap, same kV) runs across, not down
WRITE_PDF = True           # <case>_sld.pdf beside the .svg
GUI_FILE = True            # from a command prompt: also DRAW_SLD_IN_PSSE_GUI.py, to draw the .sld in the GUI
IB_ID = "IB"               # the infinite-bus machine of a z7_plant_models.py package
IB_MBASE_MIN = 10000.0     # a machine at least this big (MVA) is an infinite bus / grid equivalent, not a unit
ZERO_X_PU = 0.0001         # a line with R = 0 and |X| at most this is a zero-impedance tie (PSS/E's THRSHZ)
FONT_PT = 7.0              # label text size (points); slots and rows follow the text
SLD_SCALE = 1.0            # PSS/E diagram (.sld): spread the layout out (1.5) or pack it tighter (0.75)
LEVELS = [                 # voltage levels: (lowest kV, name, colour)
    (300.0, "EHV", "#B03024"),
    (69.0, "HV", "#1F4E9A"),
    (1.0, "MV", "#1D7A3A"),
    (0.0, "LV", "#7B3FA0"),
]

GREY = "#8E8E8E"           # out of service / de-energised
INK = "#1A1A1A"            # text
SOFT = "#555555"           # secondary text
GUI_NAME = "DRAW_SLD_IN_PSSE_GUI.py"
LOG_NAME = "SLD_LOG.txt"

# ============================================================================
# SMALL HELPERS
# ============================================================================
try:
    _TEXT = unicode                                 # noqa: F821 -- Python 2
except NameError:
    _TEXT = str
DEG = u"\u00b0"
_CTRL = re.compile(u"[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f]")
psspy = None
GUI = False                                         # True: running inside the PSS/E GUI
PSSE_MAJOR = None


def _u(s):
    """Text, whatever PSS/E or Python 2 hands over (bytes are decoded)."""
    if s is None:
        return u""
    if isinstance(s, bytes):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                s = s.decode(enc)
                break
            except Exception:
                continue
    elif not isinstance(s, _TEXT):
        s = _TEXT(s)
    return _CTRL.sub(u"", s)


def _err(e):
    try:
        return _u(str(e))
    except Exception:
        return _u(repr(e))


def _say(msg):
    try:
        print(msg)
    except Exception:
        try:
            print(_u(msg).encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass


def _free(path):
    """`path`, or the first <stem>_2, _3 ... that is free -- never overwrite."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    k = 2
    while os.path.exists("%s_%d%s" % (stem, k, ext)):
        k += 1
    return "%s_%d%s" % (stem, k, ext)


def _write_new(path, data):
    """`data` written to a file that does not exist yet (never overwrite); the path written."""
    if not isinstance(data, bytes):
        data = _u(data).encode("utf-8")
    while True:
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
            break
        except OSError:
            if not os.path.exists(path):
                raise
            path = _free(path)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return path


def _mkdir_new(base, name):
    """A new folder `name` (or name_2, _3 ...) inside `base`."""
    if not os.path.isdir(base):
        os.makedirs(base)
    path = os.path.join(base, name)
    while True:
        path = _free(path)
        try:
            os.mkdir(path)
            return path
        except OSError:
            if not os.path.exists(path):
                raise


class Log(object):
    def __init__(self, quiet=False):
        self.lines = []
        self.quiet = quiet

    def __call__(self, msg=""):
        msg = _u(msg)
        if not self.quiet:
            _say(msg)
        self.lines.extend(msg.split("\n"))


def _ie(rc):
    return rc[0] if isinstance(rc, (list, tuple)) else rc


def _cols(fn, head, specs):
    """The columns of one subsystem-array call. specs: per column a name, or a
       tuple of names tried in turn (they differ between PSS/E versions). A
       column no name gives is None -- it never blanks the whole call."""
    specs = [(s,) if isinstance(s, (str, _TEXT)) else tuple(s) for s in specs]
    if fn is None:
        return [None] * len(specs)
    first = [s[0] for s in specs]
    try:
        ie, cols = fn(*(list(head) + [first]))
        if ie in (0, None) and cols is not None and len(cols) == len(first):
            return list(cols)
    except Exception:
        pass
    out = []
    for cand in specs:
        col = None
        for s in cand:
            try:
                ie, c = fn(*(list(head) + [[s]]))
            except Exception:
                continue
            if ie in (0, None) and c is not None and len(c) >= 1 and c[0] is not None:
                col = c[0]
                break
        out.append(col)
    return out


def _v(cols, n, k, default):
    try:
        c = cols[n]
        return default if c is None or c[k] is None else c[k]
    except Exception:
        return default


def _num(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def _cx(x):
    try:
        return complex(x)
    except Exception:
        return None


def _z(v):
    """No '-0.0' in labels."""
    return 0.0 if abs(v) < 0.05 else v


def _kv(v):
    """115 -> '115', 34.5 -> '34.5', 0.69 -> '0.69'."""
    try:
        return "%g" % round(float(v), 3)
    except Exception:
        return "?"


def _level(kv):
    for lo, name, col in LEVELS:
        if kv >= lo:
            return name, col
    return LEVELS[-1][1], LEVELS[-1][2]


def _nkey(n):
    """Sort key of a node of the traced network: buses first, by number; star points after."""
    return (0, n) if n > 0 else (1, -n)


def _sid(s):
    return _u(s).strip()


# ============================================================================
# TEXT WIDTHS -- Helvetica (and Arial, its metric twin), 1/1000 em, chars 32..126
# ============================================================================
_HELV = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
         556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
         1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
         667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
         333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
         556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584)
_HELVB = (278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
          556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611,
          975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778,
          667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556,
          333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611,
          611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584)


def tw(s, size, bold=False):
    """Width of `s` in points at `size`."""
    tab = _HELVB if bold else _HELV
    n = 0
    for ch in _u(s):
        o = ord(ch)
        n += tab[o - 32] if 32 <= o <= 126 else (400 if o == 0xB0 else 611)
    return n * size / 1000.0


# ============================================================================
# PSS/E
# ============================================================================
def _bootstrap_psse():
    """The PSSPY folder that matches this Python, plus PSSBIN -- as the study
       scripts find it."""
    prefer = "PSSPY%d%d" % sys.version_info[:2]
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        roots += sorted(d for d in glob.glob(os.path.join(base, "PSSE3*")) if os.path.isdir(d))

    def _add(d):
        if d and os.path.isdir(d):
            if d not in sys.path:
                sys.path.insert(0, d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    for root in roots:
        if root and os.path.isdir(os.path.join(root, prefer)):
            _add(os.path.join(root, prefer))
            _add(os.path.join(root, "PSSBIN"))
            return
    for root in roots:
        pbin = os.path.join(root or "", "PSSBIN")
        if root and (os.path.isfile(os.path.join(pbin, "psspy.pyd"))
                     or os.path.isfile(os.path.join(pbin, "psspy.py"))):
            _add(pbin)
            return


def _in_psse_exe():
    """True when this Python is embedded in another program (PSS/E's GUI), not a python executable."""
    exe = os.path.basename(getattr(sys, "executable", "") or "").lower()
    return exe == "" or ("python" not in exe and not exe.startswith("py"))


def _hosted():
    """True when PSS/E itself (its GUI) may be running this script: psspy was
       imported before this script was, or this is not a python executable."""
    return _PSSPY_PRELOADED or _in_psse_exe()


def _already_up(p):
    """Inside the PSS/E GUI PSS/E answers psseversion() / psestatus() before
       any psseinit -- and psseinit must not be called there."""
    if not _hosted():
        return False
    for name in ("psestatus", "psseversion"):
        f = getattr(p, name, None)
        if f is None:
            continue
        try:
            f()
        except Exception:
            continue
        return True
    return False


def psse_start(log):
    """psspy: started here (psseinit) when run from a command prompt; in the
       PSS/E GUI, the PSS/E that runs this script (no psseinit)."""
    global psspy, GUI, PSSE_MAJOR
    if psspy is not None:
        return
    try:
        import psspy as _p
    except ImportError:
        _bootstrap_psse()
        for mod in ("psse34", "psse35", "psse33"):
            try:
                __import__(mod)
                break
            except Exception:
                continue
        import psspy as _p
    psspy = _p
    GUI = _already_up(_p)
    if not GUI:
        try:
            import redirect
            redirect.psse2py()
        except Exception:
            pass
        psspy.psseinit(150000)
    PSSE_MAJOR = _psse_major()
    log("PSS/E          : %s%s" % ("version %s" % PSSE_MAJOR if PSSE_MAJOR else "version unknown",
                                   " -- inside the PSS/E GUI (no psseinit)" if GUI
                                   else " -- started here (psseinit), run from a command prompt"))


def _psse_major():
    try:
        v = psspy.psseversion()
        for x in (v[1:] if isinstance(v, (list, tuple)) else ()):
            try:
                return int(x)
            except Exception:
                continue
    except Exception:
        pass
    return None


def raw_rev(path):
    """The REV of a .raw file: the third field of its first data line (after
       any '@!' comment lines), or None."""
    try:
        with io.open(path, "r", encoding="latin-1", errors="replace") as fh:
            for k, ln in enumerate(fh):
                if k > 50:
                    break
                t = ln.strip()
                if not t or t.startswith("@!"):
                    continue
                t = t.split("/")[0]
                f = [x for x in re.split(r"[,\s]+", t) if x]
                if len(f) >= 3:
                    try:
                        return int(float(f[2]))
                    except Exception:
                        return None
                return None
    except Exception:
        return None
    return None


def open_case(path, log):
    """Load the case into PSS/E's working memory -- read only. True when read."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".sav":
        try:
            ie = _ie(psspy.case(path))
        except Exception as e:
            ie = _err(e)
        if ie not in (0, None):
            log("*** could not open the case: psspy.case gave %s" % ie)
            return False
        log("read           : psspy.case (saved case)")
        return True
    rev = raw_rev(path)
    calls = []
    if rev and PSSE_MAJOR and rev != PSSE_MAJOR and getattr(psspy, "readrawversion", None):
        for vern in (str(rev), rev):                # PSS/E takes the version as text; some as a number
            calls.append(("readrawversion", (0, vern, path), "readrawversion(0, %r, ...)" % (vern,)))
    if getattr(psspy, "read", None):
        calls.append(("read", (0, path), "read(0, ...)"))
    if getattr(psspy, "readrawx", None):
        calls.append(("readrawx", (0, path), "readrawx(0, ...)"))
    if not calls:
        log("*** this PSS/E has no read / readrawversion / readrawx -- the .raw cannot be read")
        return False
    tried = []
    for name, args, txt in calls:
        if tried and name != calls[0][0] and tried[-1][1] not in ("TypeError",):
            break                                   # a real refusal: do not go on to a reader of another version
        try:
            ie = _ie(getattr(psspy, name)(*args))
        except TypeError as e:
            tried.append((txt, "TypeError"))
            log("  (%s: TypeError %s)" % (txt, _err(e)))
            continue
        except Exception as e:
            tried.append((txt, _err(e)))
            continue
        if ie in (0, None):
            log("read           : psspy.%s -- RAW version %s, PSS/E %s" % (txt, rev or "?", PSSE_MAJOR or "?"))
            return True
        tried.append((txt, "ierr %s" % ie))
    log("*** could not read the .raw (RAW version %s, PSS/E %s): %s"
        % (rev or "not found in its first line", PSSE_MAJOR or "?",
           "; ".join("%s gave %s" % t for t in tried)))
    return False


def solution_state():
    """(converged?, text) -- of the solution the case holds; nothing is solved here."""
    try:
        ie = _ie(psspy.solved())
    except Exception:
        return False, "as the case holds them (solution state unknown)"
    if ie in (0, 7):
        return True, "as the case holds them -- a converged solution"
    return False, "as the case holds them -- not a converged solution (PSS/E solved() = %s)" % ie


def sysmva():
    try:
        v = psspy.sysmva()
        v = v[-1] if isinstance(v, (list, tuple)) else v
        v = float(v)
        return v if v > 0 else 100.0
    except Exception:
        return 100.0


def current_case_file():
    """The file name of the case open in PSS/E ('' if unnamed)."""
    try:
        r = psspy.sfiles()
    except Exception:
        return ""
    vals = r if isinstance(r, (list, tuple)) else [r]
    for v in vals:
        if isinstance(v, (list, tuple)):
            vals = list(vals) + list(v)
    for v in vals:
        if isinstance(v, (str, _TEXT, bytes)) and _u(v).strip():
            return _u(v).strip() if not isinstance(v, str) else v.strip()
    return ""


# ============================================================================
# THE NETWORK, AS PSS/E HOLDS IT
# ============================================================================
class Br(object):
    """One branch: a line ("LINE"), a two-winding ("2W") or a three-winding
       transformer ("3W": buses a, b, c are windings 1, 2, 3)."""

    def __init__(self, kind, a, b, c, ckt, st):
        self.kind, self.a, self.b, self.c, self.ckt, self.st = kind, a, b, c, ckt, st
        self.r = self.x = self.rate = self.sbase = self.p = self.q = None
        self.nomv = (None, None, None)
        self.x3 = (None, None, None)                # 3W: X1-2, X2-3, X3-1
        self.zero = False

    def wnd_on(self):
        if self.kind != "3W":
            return (self.st > 0, self.st > 0, False)
        return {1: (True, True, True), 2: (True, False, True), 3: (True, True, False),
                4: (False, True, True)}.get(self.st, (False, False, False))

    def ends(self):
        return [x for x in (self.a, self.b, self.c) if x]

    def label(self):
        if self.kind == "3W":
            return "3W %d-%d-%d '%s'" % (self.a, self.b, self.c, self.ckt)
        return "%s %d-%d '%s'" % ("2W" if self.kind == "2W" else ("tie" if self.zero else "line"),
                                  self.a, self.b, self.ckt)


def read_network(log):
    """{"buses", "machines", "branches", "loads", "fxsh", "swsh"} of the case in memory."""
    net = {"buses": {}, "machines": [], "branches": [], "loads": [], "fxsh": [], "swsh": []}
    bi = _cols(psspy.abusint, (-1, 2), ["NUMBER", "TYPE", "AREA", "ZONE"])
    bre = _cols(psspy.abusreal, (-1, 2), ["BASE", "PU", "ANGLED"])
    bc = _cols(psspy.abuschar, (-1, 2), ["NAME"])
    for k in range(len(bi[0] or [])):
        b = int(bi[0][k])
        net["buses"][b] = {"num": b, "type": int(_v(bi, 1, k, 1)), "area": int(_v(bi, 2, k, 0)),
                           "zone": int(_v(bi, 3, k, 0)), "kv": float(_v(bre, 0, k, 0.0)),
                           "vm": float(_v(bre, 1, k, 1.0)), "va": float(_v(bre, 2, k, 0.0)),
                           "name": _sid(_v(bc, 0, k, ""))}
    mi = _cols(psspy.amachint, (-1, 4), ["NUMBER", "STATUS"])
    mc = _cols(psspy.amachchar, (-1, 4), ["ID"])
    mr = _cols(psspy.amachreal, (-1, 4), ["PGEN", "QGEN", "MBASE", "PMAX", "PMIN", "QMAX", "QMIN"])
    for k in range(len(mi[0] or [])):
        net["machines"].append({"bus": int(mi[0][k]), "id": _sid(_v(mc, 0, k, "1")), "st": int(_v(mi, 1, k, 1)),
                                "p": float(_v(mr, 0, k, 0.0)), "q": float(_v(mr, 1, k, 0.0)),
                                "mbase": float(_v(mr, 2, k, 0.0)), "pmax": _num(_v(mr, 3, k, None)),
                                "pmin": _num(_v(mr, 4, k, None)), "qmax": _num(_v(mr, 5, k, None)),
                                "qmin": _num(_v(mr, 6, k, None))})
    head = (-1, 1, 1, 2, 1)
    li = _cols(psspy.abrnint, head, ["FROMNUMBER", "TONUMBER", "STATUS"])
    lc = _cols(psspy.abrnchar, head, ["ID"])
    lx = _cols(getattr(psspy, "abrncplx", None), head, ["RX"])
    lr = _cols(getattr(psspy, "abrnreal", None), head, [("RATEA", "RATE1"), "P", "Q"])
    for k in range(len(li[0] or [])):
        br = Br("LINE", int(li[0][k]), int(li[1][k]), 0, _sid(_v(lc, 0, k, "1")), int(_v(li, 2, k, 1)))
        z = _cx(_v(lx, 0, k, None))
        if z is not None:
            br.r, br.x = z.real, z.imag
            br.zero = abs(z.real) < 1e-12 and abs(z.imag) <= ZERO_X_PU
        br.rate, br.p, br.q = _num(_v(lr, 0, k, None)), _num(_v(lr, 1, k, None)), _num(_v(lr, 2, k, None))
        net["branches"].append(br)
    ti = _cols(psspy.atrnint, head, ["FROMNUMBER", "TONUMBER", "STATUS"])
    tc = _cols(psspy.atrnchar, head, ["ID"])
    tx = _cols(getattr(psspy, "atrncplx", None), head, [("RXACT", "RXNOM")])
    tr = _cols(getattr(psspy, "atrnreal", None), head, ["SBASE1", "NOMV1", "NOMV2", ("RATEA", "RATE1"), "P", "Q"])
    for k in range(len(ti[0] or [])):
        br = Br("2W", int(ti[0][k]), int(ti[1][k]), 0, _sid(_v(tc, 0, k, "1")), int(_v(ti, 2, k, 1)))
        z = _cx(_v(tx, 0, k, None))
        if z is not None:
            br.r, br.x = z.real, z.imag
        br.sbase = _num(_v(tr, 0, k, None))
        br.nomv = (_num(_v(tr, 1, k, None)), _num(_v(tr, 2, k, None)), None)
        br.rate, br.p, br.q = _num(_v(tr, 3, k, None)), _num(_v(tr, 4, k, None)), _num(_v(tr, 5, k, None))
        net["branches"].append(br)
    t3 = _cols(getattr(psspy, "atr3int", None), head, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER", "STATUS"])
    t3c = _cols(getattr(psspy, "atr3char", None), head, ["ID"])
    t3x = _cols(getattr(psspy, "atr3cplx", None), head,
                [("RX1-2ACT", "RX1-2NOM"), ("RX2-3ACT", "RX2-3NOM"), ("RX3-1ACT", "RX3-1NOM")])
    t3r = _cols(getattr(psspy, "atr3real", None), head, [("SBASE1-2",)])
    for k in range(len(t3[0] or [])):
        br = Br("3W", int(t3[0][k]), int(t3[1][k]), int(t3[2][k]), _sid(_v(t3c, 0, k, "1")), int(_v(t3, 3, k, 1)))
        xs = []
        for n in range(3):
            z = _cx(_v(t3x, n, k, None))
            xs.append(None if z is None else z.imag)
        br.x3 = tuple(xs)
        br.sbase = _num(_v(t3r, 0, k, None))
        net["branches"].append(br)
    order = {"LINE": 0, "2W": 1, "3W": 2}
    net["branches"].sort(key=lambda r: (order[r.kind], r.a, r.b, r.c, r.ckt))
    di = _cols(getattr(psspy, "aloadint", None), (-1, 4), ["NUMBER", "STATUS"])
    dc = _cols(getattr(psspy, "aloadchar", None), (-1, 4), ["ID"])
    dx = _cols(getattr(psspy, "aloadcplx", None), (-1, 4), [("TOTALACT", "MVAACT")])
    for k in range(len(di[0] or [])):
        s = _cx(_v(dx, 0, k, None))
        net["loads"].append({"bus": int(di[0][k]), "id": _sid(_v(dc, 0, k, "1")), "st": int(_v(di, 1, k, 1)),
                             "p": None if s is None else s.real, "q": None if s is None else s.imag})
    fi = _cols(getattr(psspy, "afxshuntint", None), (-1, 4), ["NUMBER", "STATUS"])
    fc = _cols(getattr(psspy, "afxshuntchar", None), (-1, 4), ["ID"])
    fx = _cols(getattr(psspy, "afxshuntcplx", None), (-1, 4), [("SHUNTNOM", "SHUNTACT")])
    for k in range(len(fi[0] or [])):
        s = _cx(_v(fx, 0, k, None))
        net["fxsh"].append({"bus": int(fi[0][k]), "id": _sid(_v(fc, 0, k, "1")), "st": int(_v(fi, 1, k, 1)),
                            "g": None if s is None else s.real, "b": None if s is None else s.imag})
    si = _cols(getattr(psspy, "aswshint", None), (-1, 4), ["NUMBER", "STATUS"])
    sc = _cols(getattr(psspy, "aswshchar", None), (-1, 4), ["ID"])
    sr = _cols(getattr(psspy, "aswshreal", None), (-1, 4), [("BSWNOM", "BSWACT"), "BSWMAX", "BSWMIN"])
    for k in range(len(si[0] or [])):
        net["swsh"].append({"bus": int(si[0][k]), "id": _sid(_v(sc, 0, k, "")), "st": int(_v(si, 1, k, 1)),
                            "b": _num(_v(sr, 0, k, None)), "bmax": _num(_v(sr, 1, k, None)),
                            "bmin": _num(_v(sr, 2, k, None))})
    for key in ("machines", "loads", "fxsh", "swsh"):
        net[key].sort(key=lambda r: (r["bus"], r["id"]))
    return net


def _ib_like(m):
    return m["id"].upper() == IB_ID.upper() or m["mbase"] >= IB_MBASE_MIN


# ============================================================================
# THE POI AND THE PLANT BEHIND IT (pure Python from here on)
# ============================================================================
def adjacency(net, everything=False):
    """{bus: set(neighbours)} over in-service branches between in-service buses
       -- or, everything=True, over every branch, whatever its status."""
    adj = collections.defaultdict(set)
    buses = net["buses"]

    def link(a, b):
        if a != b and a in buses and b in buses and (everything or (buses[a]["type"] != 4
                                                                    and buses[b]["type"] != 4)):
            adj[a].add(b)
            adj[b].add(a)
    for br in net["branches"]:
        if br.kind == "3W":
            on = br.wnd_on()
            ends = [x for x, o in zip((br.a, br.b, br.c), on) if o or everything]
            for i in range(len(ends)):
                for j in range(i + 1, len(ends)):
                    link(ends[i], ends[j])
        elif br.st > 0 or everything:
            link(br.a, br.b)
    return adj


def parts_behind(adj, poi, cap):
    """[(first bus, set of buses -- None when larger than cap)], one per part of
       the network reached from a POI neighbour without passing the POI."""
    out, done = [], set()
    for n in sorted(adj.get(poi, ())):
        if n in done:
            continue
        seen, todo, big = set([n]), [n], False
        while todo and not big:
            u = todo.pop()
            for w in sorted(adj.get(u, ())):
                if w == poi or w in seen:
                    continue
                seen.add(w)
                todo.append(w)
                if len(seen) > cap:
                    big = True
                    break
        done |= seen
        out.append((n, None if big else seen))
    return out


def _component(adj, start, stop, cap):
    """The buses reached from `start` without entering `stop`, or None past `cap`."""
    seen, todo = set([start]), [start]
    while todo:
        u = todo.pop()
        for w in sorted(adj.get(u, ())):
            if w in stop or w in seen:
                continue
            seen.add(w)
            todo.append(w)
            if len(seen) > cap:
                return None
    return seen


def find_poi(stem, net, poi_arg):
    """(POI bus, how it was found) or (None, why not)."""
    buses = net["buses"]
    given, src = None, ""
    for k, v in sorted(POI_BY_CASE.items()):
        if str(k).lower() == stem.lower():
            given, src = v, "POI_BY_CASE"
    if given is None and poi_arg is not None:
        given, src = poi_arg, "--poi"
    if given is None and POI_BUS is not None:
        given, src = POI_BUS, "POI_BUS"
    note = ""
    if given is not None:
        try:
            given = int(given)
        except Exception:
            return None, "%s %r is not a bus number" % (src, given)
        if given in buses and buses[given]["type"] == 4:
            return None, "%s %d is out of service (type 4) in this case" % (src, given)
        if given in buses:
            return given, "given (%s)" % src
        if src == "POI_BY_CASE":
            return None, "%s %d is not a bus of this case" % (src, given)
        note = "%s %d is not a bus of this case" % (src, given)     # meant for another case: found here instead
    poi, how = _auto_poi(net)
    if note:
        return poi, ("%s -- %s" % (how, note)) if poi is not None else ("%s; %s" % (note, how))
    return poi, how


def _auto_poi(net):
    buses = net["buses"]
    swings = sorted(b for b, d in buses.items() if d["type"] == 3)
    ib = [b for b in swings if any(m["bus"] == b and _ib_like(m) for m in net["machines"])]
    if ib:
        m = sorted((m for m in net["machines"] if m["bus"] == ib[0] and _ib_like(m)), key=lambda m: m["id"])[0]
        return ib[0], ("plant package: the swing bus holds the infinite-bus machine '%s' (MBASE %g MVA)"
                       % (m["id"], m["mbase"]))
    if len(buses) <= MAX_PLANT_BUSES:
        if swings:
            return swings[0], ("small case (%d buses): its swing bus%s"
                               % (len(buses), "" if len(swings) == 1 else
                                  " (the first of %d: %s)" % (len(swings), ", ".join(str(s) for s in swings))))
        return None, ("no POI: this case has no swing bus (and no IB machine) -- set POI_BUS / POI_BY_CASE "
                      "or give --poi")
    return None, ("full-system case (%d buses): set POI_BUS / POI_BY_CASE or give --poi <bus> -- the POI "
                  "cannot be guessed in a case this size" % len(buses))


class Edge(object):
    """A branch of the traced network between two nodes: buses, or a bus and
       the star point of a three-winding transformer (wnd = its winding)."""

    def __init__(self, u, v, br, wnd, order, on):
        self.u, self.v, self.br, self.wnd, self.order, self.on = u, v, br, wnd, order, on
        self.oos = not (br.wnd_on()[wnd - 1] if br.kind == "3W" else br.st > 0)   # switched out (else de-energised)
        if br.kind == "3W":
            x12, x23, x31 = br.x3
            if None in (x12, x23, x31):
                self.absx = float("inf")
            else:
                self.absx = abs({1: x12 + x31 - x23, 2: x12 + x23 - x31, 3: x23 + x31 - x12}[wnd] / 2.0)
        else:
            self.absx = abs(br.x) if br.x is not None else float("inf")

    def other(self, n):
        return self.v if n == self.u else self.u

    def key(self):
        return (not self.on, self.absx, self.br.ckt, self.order)

    def state(self):
        return "" if self.on else ("out of service" if self.oos else "de-energised")


class Plant(object):
    """The plant behind the POI of one case, traced from every unit to the POI."""

    def __init__(self, net, poi, how):
        self.net, self.poi, self.how = net, poi, how
        self.live, self.dead = set(), set()
        self.grid, self.idle = [], []
        self.units, self.left, self.stubs = [], [], []
        self.edges, self.nontree = [], []
        self.depth, self.parent, self.bundle, self.twins = {}, {}, {}, {}
        self.mach_at = collections.defaultdict(list)

    def bus(self, b):
        return self.net["buses"].get(b, {"name": "?", "kv": 0.0, "vm": 1.0, "va": 0.0, "type": 1})

    def bname(self, b):
        d = self.bus(b)
        return "%d %s %s kV" % (b, d["name"], _kv(d["kv"]))


def find_plant(net, poi, how):
    P = Plant(net, poi, how)
    gens = set(int(g) for g in GEN_BUSES)
    for m in net["machines"]:
        P.mach_at[m["bus"]].append(m)

    def is_unit(m):
        return m["bus"] != poi and not _ib_like(m) and (not gens or m["bus"] in gens)
    unit_buses = set(m["bus"] for m in net["machines"] if is_unit(m))
    adj = adjacency(net)
    for first, seen in parts_behind(adj, poi, MAX_PLANT_BUSES):
        if seen is None:
            P.grid.append(first)
        elif seen & unit_buses:
            P.live |= seen
        else:
            P.idle.append((first, seen))
    # parts reached only through out-of-service branches (a feeder switched out, say)
    anyg = adjacency(net, everything=True)
    stop = P.live | set([poi])
    checked = set()
    for u in sorted(stop):
        for w in sorted(anyg.get(u, ())):
            if w in stop or w in P.dead or w in checked:
                continue
            comp = _component(anyg, w, stop, MAX_PLANT_BUSES)
            if comp is None:
                checked.add(w)
                continue
            if u == poi and not (comp & unit_buses):
                checked |= comp
                continue
            P.dead |= comp
    plant = P.live | P.dead
    P.units = [m for m in net["machines"] if m["bus"] in plant and is_unit(m)]
    for m in net["machines"]:
        if is_unit(m) and m["bus"] not in plant:
            P.left.append("unit %d '%s' (%s): not connected to the POI, not even through out-of-service "
                          "branches -- not drawn" % (m["bus"], m["id"], P.bname(m["bus"])))
        elif m["bus"] in plant and not is_unit(m) and not _ib_like(m):
            P.left.append("machine %d '%s' is not a unit (GEN_BUSES) -- drawn, not traced" % (m["bus"], m["id"]))
    for first, seen in P.idle:
        P.left.append("%d bus(es) behind the POI via %s hold no unit -- drawn as a stub on the POI"
                      % (len(seen), P.bname(first)))
    for first in P.grid:
        P.left.append("the network behind the POI via %s is larger than %d buses (grid) -- drawn as a stub"
                      % (P.bname(first), MAX_PLANT_BUSES))
    for br in net["branches"]:
        ends = br.ends()
        if poi in ends:
            far = [x for x in ends if x != poi]
            if far and not any(x in plant for x in far):
                P.stubs.append((br, far))
            elif [x for x in far if x not in plant]:
                P.left.append("%s: its winding to %s is outside the plant -- not drawn"
                              % (br.label(), ", ".join(str(x) for x in far if x not in plant)))
        else:
            inside = [x for x in ends if x in plant]
            if inside and len(inside) < len(ends):
                P.left.append("%s joins the plant to %s outside it (%s) -- not drawn"
                              % (br.label(), ", ".join(str(x) for x in ends if x not in plant),
                                 "out of service" if not any(br.wnd_on()) else "in service"))
    P.stubs.sort(key=lambda s: (min(s[1]), s[0].kind, s[0].ckt))
    trace(P)
    return P


def trace(P):
    """Shortest paths (by number of branches) from the POI to every plant bus:
       first over in-service branches, then -- for parts reached only through
       out-of-service branches -- over any. Ties: the smaller |X|, then the
       smaller bus number."""
    net, poi, buses = P.net, P.poi, P.net["buses"]
    plant = P.live | P.dead | set([poi])
    edges = []
    for k, br in enumerate(net["branches"]):
        if br.kind == "3W":
            ends = [(w, b) for w, b in ((1, br.a), (2, br.b), (3, br.c)) if b in plant]
            if not ends or (len(ends) == 1 and ends[0][1] == poi):
                continue
            star = -(k + 1)
            on3 = br.wnd_on()
            for w, b in ends:
                edges.append(Edge(b, star, br, w, len(edges), on3[w - 1] and buses[b]["type"] != 4))
        elif br.a in plant and br.b in plant and br.a != br.b:
            edges.append(Edge(br.a, br.b, br, 0, len(edges),
                              br.st > 0 and buses[br.a]["type"] != 4 and buses[br.b]["type"] != 4))
    nb = collections.defaultdict(list)
    for e in edges:
        nb[e.u].append(e)
        nb[e.v].append(e)
    depth, parent, pedge = {poi: 0}, {}, {}

    def grow(frontier, use):
        frontier = sorted(frontier, key=_nkey)
        while frontier:
            nxt = set()
            for u in frontier:
                for e in nb[u]:
                    v = e.other(u)
                    if v not in depth and use(e):
                        nxt.add(v)
            for v in sorted(nxt, key=_nkey):
                best = None
                for e in nb[v]:
                    u = e.other(v)
                    if u in depth and use(e):
                        k = (depth[u], e.absx, _nkey(u), e.order)
                        if best is None or k < best[0]:
                            best = (k, u, e)
                parent[v], pedge[v] = best[1], best[2]
            for v in nxt:
                depth[v] = depth[parent[v]] + 1
            frontier = sorted(nxt, key=_nkey)
    grow([poi], lambda e: e.on)
    grow(list(depth), lambda e: True)
    P.edges, P.depth, P.parent = edges, depth, parent
    for v, u in parent.items():
        P.bundle[v] = sorted([e for e in nb[v] if e.other(v) == u], key=lambda e: e.key())
    P.nontree = [e for e in edges if parent.get(e.v) != e.u and parent.get(e.u) != e.v]
    # a three-winding transformer in parallel with another (same bus above, same bus below): a twin,
    # drawn beside the first with its winding down to that bus -- not a dashed connection
    for e in list(P.nontree):
        for s2, c in ((e.u, e.v), (e.v, e.u)):
            s1 = parent.get(c)
            if s2 < 0 < c and s1 is not None and s1 < 0 and s1 != s2 and parent.get(s1) == parent.get(s2) \
                    and s2 not in P.twins and e.on:
                P.twins[s2] = (s1, c, e)
                P.nontree.remove(e)
                break
    for b in sorted(plant - set(depth)):
        P.left.append("bus %s: in the plant but not reached -- not drawn" % P.bname(b))


# ============================================================================
# LAYOUT -- a tidy tree hanging from the POI, every unit on the bottom row
# ============================================================================
class Geo(object):
    """Sizes (points), from the label text size."""

    def __init__(self, font):
        f = float(font)
        self.k = f / 7.0                # title, legend and title block follow the label size
        self.f = f                      # label text
        self.fb = f + 0.6               # bus names
        self.lh = round(f * 1.24, 2)    # label line spacing
        self.gap = 12.0                 # between neighbouring subtrees
        self.pad = 10.0                 # a bar beyond its outermost connection
        self.half = 16.0                # half the shortest bar
        self.bar = 3.6                  # bar thickness
        self.r2 = 6.6                   # transformer winding circle
        self.rg = 10.0                  # generator circle
        self.lane = 7.0                 # between dashed-connection lanes
        self.elb = 19.0                 # three-winding transformer: elbow below the star point
        self.blk = 2 * self.lh + 6.0    # bus label (two lines) above its bar, with its gap
        self.pitch = max(78.0, round(self.bar / 2 + self.blk + 1.62 * self.r2 + (0.97 * f + 3 * self.lh) / 2 + 22, 1))
        self.head = 16.0 + 40.0 * self.k      # the title above the drawing
        self.top = self.head + self.pitch     # y of row 0 (the POI)
        self.chp = round(1.7 * self.lh, 1)    # between the channels of dashed connections


class Node(object):
    def __init__(self, key, kind):
        self.key, self.kind = key, kind             # bus number | star id (< 0) | (bus, n); "bus" | "star" | "hang"
        self.parent, self.kids, self.circ = None, [], []
        self.mode, self.path, self.live, self.unit = "below", False, True, False
        self.row, self.size = 0, 1
        self.item = None                            # hang: (kind, record)
        self.lbl = []                               # bus / star / hang label: [(text, bold, size)]
        self.clbl = []                              # per circuit: label lines
        self.cx = []                                # circuit x offsets from the anchor
        self.lanes = []                             # [side, x, loop number]
        self.twin = None                            # star point: (first star, bus fed by both, edge)
        self.bar = (0.0, 0.0)
        self.off = self.x = self.yb = 0.0           # yb: y of its bar (of its symbol, for a star point)
        self.prims = []
        self.contour = {}


def _preorder(root):
    out, st = [], [root]
    while st:
        n = st.pop()
        out.append(n)
        st.extend(reversed(n.kids))
    return out


def _merge(acc, c, off=0.0):
    out = dict((r, [v[0], v[1]]) for r, v in acc.items())
    for r, (lo, hi) in c.items():
        lo, hi = lo + off, hi + off
        if r in out:
            if lo < out[r][0]:
                out[r][0] = lo
            if hi > out[r][1]:
                out[r][1] = hi
        else:
            out[r] = [lo, hi]
    return out


def _fit(acc, c, gap):
    """The smallest offset that puts contour `c` right of `acc`, `gap` apart."""
    off = None
    for r, (lo, hi) in c.items():
        if r in acc:
            need = acc[r][1] + gap - lo
            off = need if off is None else max(off, need)
    if off is None:
        off = max(v[1] for v in acc.values()) + gap - min(v[0] for v in c.values())
    return off


class Sld(object):
    """The plant as a one-line diagram: tree, rows, tidy x placement, drawing."""

    def __init__(self, P, stem, case_path, state, mva, stamp):
        self.P, self.stem, self.case_path, self.state, self.mva, self.stamp = P, stem, case_path, state, mva, stamp
        self.g = Geo(FONT_PT)
        self.loops = []
        self.above = []
        self.build()
        self.measure()
        self.rows()
        self.prep_lanes()
        self.layout()
        self.place()

    # ---------------------------------------------------------------- tree
    def build(self):
        P = self.P
        N = {}
        for n in P.depth:
            N[n] = Node(n, "bus" if n > 0 else "star")
        self.N = N
        self.root = root = N[P.poi]
        for v in sorted(P.parent, key=_nkey):
            u = P.parent[v]
            N[v].parent = N[u]
            N[u].kids.append(N[v])
            N[v].circ = P.bundle[v]
        unit_buses = set(m["bus"] for m in P.units)
        for b in unit_buses:
            if b in N:
                N[b].unit = True
                n = N[b]
                while n is not None and not n.path:
                    n.path = True
                    n = n.parent
        for n in _preorder(root):
            if n is root:
                n.live = True
            elif n.kind == "bus":
                n.live = n.key in P.live and n.parent.live
            else:
                n.live = n.parent.live and any(e.on for e in n.circ)
        root.path = True
        hangs = collections.defaultdict(list)
        for m in P.net["machines"]:
            if m["bus"] != P.poi and m["bus"] in N:
                hangs[m["bus"]].append(("gen", m))
        for key, kind in (("loads", "load"), ("fxsh", "fxsh"), ("swsh", "swsh")):
            for r in P.net[key]:
                if r["bus"] in N:
                    hangs[r["bus"]].append((kind, r))
        for b in sorted(hangs):
            for k, it in enumerate(hangs[b]):
                h = Node((b, k), "hang")
                h.item, h.mode, h.parent = it, "hang", N[b]
                h.live = N[b].live and it[1].get("st", 1) > 0
                N[b].kids.append(h)
        for n in reversed(_preorder(root)):
            n.size = 1 + sum(k.size for k in n.kids if k.kind != "hang")
        order = {"gen": 0, "load": 1, "fxsh": 2, "swsh": 3}

        def first_bus(k):
            if k.kind == "bus":
                return k.key
            bs = [x.key for x in _preorder(k) if x.kind == "bus"]
            return min(bs) if bs else 10 ** 9

        def kid_key(k):
            if k.kind == "hang":
                return (2, order[k.item[0]], 0, k.key[1])
            return (0 if k.path else 1, -k.size, first_bus(k), 0)
        for n in _preorder(root):
            n.kids.sort(key=kid_key)
        for s2, (s1, c, e) in sorted(P.twins.items()):
            a, b, cn = N.get(s2), N.get(s1), N.get(c)
            if a is not None and b is not None and cn is not None and not [k for k in a.kids if k.path]:
                a.twin = (b, cn, e)
            else:
                P.nontree.append(e)                     # not drawable as a twin: a dashed connection after all
        for n in _preorder(root):
            k = self._beside_kid(n)
            if k is not None:
                k.mode = "beside"
                n.kids.remove(k)
                n.kids.append(k)
        last = {}
        for n in sorted((x for x in _preorder(root) if x.twin is not None), key=lambda x: _nkey(x.key)):
            b, cn, e = n.twin
            if [k for k in cn.kids if k.mode == "beside"]:
                n.twin = None                           # its bus runs on across: a dashed connection instead
                P.nontree.append(e)
                continue
            n.parent.kids.remove(n)                     # right after its first (and its first's other twins),
            n.parent.kids.insert(n.parent.kids.index(last.get(id(b), b)) + 1, n)   # so its winding reaches the bus
            last[id(b)] = n
        # what stands on the POI, above it: infinite bus / sources, then the grid stubs
        for m in sorted(P.mach_at.get(P.poi, []), key=lambda m: (not _ib_like(m), m["id"])):
            self.above.append({"kind": "ib" if _ib_like(m) else "src", "rec": m})
        for br, far in P.stubs:
            self.above.append({"kind": "stub", "rec": (br, far)})

    def _same_kv(self, a, b):
        kv1, kv2 = self.P.bus(a)["kv"], self.P.bus(b)["kv"]
        return abs(kv1 - kv2) <= 0.01 * max(kv1, kv2, 1e-6)

    def _beside_kid(self, n):
        """The one child drawn beside n, on its row, instead of below it -- or None:
           - a three-winding transformer's winding that feeds no unit (a tertiary),
             when another winding does: beside the transformer;
           - a bus section behind a zero-impedance tie, at the same kV;
           - FEEDERS_ACROSS: the next tap of a daisy-chained feeder (one
             in-service line, same kV), when n taps a unit itself."""
        kids = [k for k in n.kids if k.kind != "hang"]
        if n is self.root or not kids:
            return None
        if n.kind == "star":
            off = [k for k in kids if not k.path]
            return off[0] if len(off) == 1 and (len(kids) == 2 or (len(kids) == 1 and n.twin)) else None
        ties = [k for k in kids if k.kind == "bus" and not k.unit and len(k.circ) == 1 and k.circ[0].br.zero
                and k.circ[0].on and k.live and self._same_kv(n.key, k.key)]
        if len(ties) == 1:
            return ties[0]
        if not FEEDERS_ACROSS:
            return None
        cand = [k for k in kids if k.kind == "bus" and k.path and not k.unit and len(k.circ) == 1
                and k.circ[0].br.kind == "LINE" and k.circ[0].on and k.live and n.live
                and self._same_kv(n.key, k.key) and [x for x in k.kids if x.kind != "hang" and x.path]]
        taps = [k for k in kids if k not in cand and k.path and k.circ and k.circ[0].br.kind == "2W"]
        return cand[0] if len(cand) == 1 and (taps or n.unit) else None

    # ---------------------------------------------------------------- labels
    def bus_lines(self, n):
        P, g = self.P, self.g
        d = P.bus(n.key)
        poi = n is self.root
        l1 = "%s%d %s" % ("POI  " if poi else "", n.key, d["name"])
        if not n.live:
            l2 = "%s kV   %s" % (_kv(d["kv"]), "isolated" if d["type"] == 4 else "de-energised")
        elif SHOW_VOLTAGES:
            l2 = "%s kV   %.4f pu   %.2f%s" % (_kv(d["kv"]), d["vm"], d["va"], DEG)
        else:
            l2 = "%s kV" % _kv(d["kv"])
        return [(l1, True, g.fb + (1.0 if poi else 0.0)), (l2, False, g.f)]

    def _kv_of_end(self, br, bus):
        """The winding kV of a transformer at `bus` (its nominal, else the bus base)."""
        k = {br.a: 0, br.b: 1, br.c: 2}.get(bus)
        v = br.nomv[k] if k is not None else None
        return v if v else self.P.bus(bus)["kv"]

    def flow_dir(self, br, upper, lower):
        """(direction toward `upper` ?, P, Q) of a branch, P / Q in that direction."""
        if br.p is None:
            return None
        p, q = (br.p, br.q or 0.0) if br.a == lower else (-br.p, -(br.q or 0.0))
        return (p >= 0, abs(p), q if p >= 0 else -q)

    def circ_lines(self, e, upper, lower, horizontal=False):
        g, br = self.g, e.br
        out = []
        if br.kind == "LINE":
            t = ("Zero-Z tie '%s'" if br.zero else "Line '%s'") % br.ckt
            if br.rate:
                t += "   %g MVA" % br.rate
            out.append((t, True, g.f))
            if SHOW_IMPEDANCE and not br.zero and br.x is not None:
                out.append(("%.5f %s j%.5f pu" % (br.r or 0.0, "-" if br.x < 0 else "+", abs(br.x)), False, g.f))
        elif br.kind == "2W":
            t = "T '%s'" % br.ckt
            if br.sbase:
                t += "   %g MVA" % br.sbase
            out.append((t, True, g.f))
            out.append(("%s / %s kV" % (_kv(self._kv_of_end(br, upper)), _kv(self._kv_of_end(br, lower))), False, g.f))
            if SHOW_IMPEDANCE and br.x is not None:
                out.append(("X %.4f pu" % br.x, False, g.f))
        else:
            return out
        if SHOW_FLOWS and e.on:
            fd = self.flow_dir(br, upper, lower)
            if fd is not None:
                up, p, q = fd
                arrow = ("l" if up else "r") if horizontal else ("u" if up else "d")
                out.append(("@" + arrow + "%.1f MW   %.1f Mvar" % (_z(p), _z(q)), False, g.f))
        if not e.on:
            out.append((e.state(), False, g.f))
        return out

    def star_lines(self, n):
        g, br = self.g, n.circ[0].br if n.circ else None
        if br is None:
            return []
        t = "3W T '%s'" % br.ckt
        if br.sbase:
            t += "   %g MVA" % br.sbase
        kvs = " / ".join(_kv(self.P.bus(b)["kv"]) for b in (br.a, br.b, br.c))
        out = [(t, True, g.f), ("%s kV   (%d / %d / %d)" % (kvs, br.a, br.b, br.c), False, g.f)]
        if SHOW_IMPEDANCE and None not in br.x3:
            out.append(("X12 %.4f  X23 %.4f  X31 %.4f pu" % br.x3, False, g.f))
        if not any(br.wnd_on()):
            out.append(("out of service", False, g.f))
        elif not all(br.wnd_on()):
            out.append(("winding %d out of service" % ([1, 2, 3][list(br.wnd_on()).index(False)]), False, g.f))
        return out

    def hang_lines(self, n):
        g = self.g
        kind, r = n.item
        off = "" if r.get("st", 1) > 0 else "   OFF"
        if kind == "gen":
            out = [("Gen '%s'%s" % (r["id"], off), True, g.f),
                   ("%.1f MW   %.1f Mvar" % (_z(r["p"]), _z(r["q"])), False, g.f),
                   ("Mbase %g MVA" % r["mbase"], False, g.f)]
        elif kind == "load":
            out = [("Load '%s'%s" % (r["id"], off), True, g.f)]
            if r["p"] is not None:
                out.append(("%.1f MW   %.1f Mvar" % (_z(r["p"]), _z(r["q"])), False, g.f))
        elif kind == "fxsh":
            b = r["b"]
            out = [("%s '%s'%s" % ("Reactor" if (b or 0) < 0 else "Capacitor", r["id"], off), True, g.f)]
            if b is not None:
                out.append(("%.1f Mvar" % b, False, g.f))
        else:
            out = [("Sw shunt%s%s" % (" '%s'" % r["id"] if r["id"] else "", off), True, g.f)]
            if r["b"] is not None:
                out.append(("%.1f Mvar" % r["b"], False, g.f))
            if r["bmin"] is not None and r["bmax"] is not None:
                out.append(("%.1f to %.1f" % (r["bmin"], r["bmax"]), False, g.f))
        return out

    def above_lines(self, it):
        g, P = self.g, self.P
        if it["kind"] in ("ib", "src"):
            m = it["rec"]
            t = ("Infinite bus '%s'" % m["id"]) if it["kind"] == "ib" else ("Source '%s' (at the POI)" % m["id"])
            return [(t, True, g.f), ("Mbase %g MVA" % m["mbase"], False, g.f),
                    ("%.1f MW   %.1f Mvar" % (_z(m["p"]), _z(m["q"])), False, g.f)]
        br, far = it["rec"]
        b = far[0]
        d = P.bus(b)
        l1 = "to %d %s %s kV" % (b, d["name"], _kv(d["kv"]))
        kind = {"LINE": "tie" if br.zero else "line", "2W": "2W", "3W": "3W"}[br.kind]
        idle = [n for n, part in P.idle if b in part]
        l2 = "%s: %s '%s'" % ("no unit behind" if idle else "grid", kind, br.ckt)
        if len(far) > 1:
            l2 += " (+ %s)" % ", ".join("%d %s kV" % (x, _kv(P.bus(x)["kv"])) for x in far[1:])
        if not any(br.wnd_on()):
            l2 += "  out of service"
        return [(l1, True, g.f), (l2, False, g.f)]

    @staticmethod
    def block_w(lines):
        w = 0.0
        for t, bold, size in lines:
            if t.startswith("@"):
                w = max(w, 7.0 + tw(t[2:], size, bold))
            else:
                w = max(w, tw(t, size, bold))
        return w

    def circ_hw(self, e):
        if e.br.kind == "2W":
            return self.g.r2 + 0.5
        return 3.0

    def measure(self):
        g, root = self.g, self.root
        for n in _preorder(root):
            if n.kind == "bus":
                n.lbl = self.bus_lines(n)
                n.clbl = []
                if n is root or n.mode == "beside":
                    n.cx = []
                    if n.mode == "beside":
                        n.clbl = [self.circ_lines(n.circ[0], n.parent.key, n.key, True)]
                    continue
                if n.parent.kind == "star":
                    n.clbl = [[] for _e in n.circ]
                    n.cx = [0.0]
                    continue
                n.clbl = [self.circ_lines(e, n.parent.key, n.key) for e in n.circ]
                xs = [0.0]
                for j in range(1, len(n.circ)):
                    xs.append(xs[-1] + self.circ_hw(n.circ[j - 1]) + 4 + self.block_w(n.clbl[j - 1]) + g.gap
                              + self.circ_hw(n.circ[j]))
                mid = (xs[0] + xs[-1]) / 2.0
                n.cx = [x - mid for x in xs]
            elif n.kind == "star":
                n.lbl = self.star_lines(n)
                n.cx = [0.0]
            else:
                n.lbl = self.hang_lines(n)
                n.cx = [0.0]
        # the POI's own: infinite bus / sources at its anchor, grid stubs to the left
        x = 0.0
        for k, it in enumerate(self.above):
            it["lines"] = self.above_lines(it)
            it["hw"] = 10.0 if it["kind"] == "ib" else (g.rg if it["kind"] == "src" else 4.0)
            it["lw"] = self.block_w(it["lines"])
            if k:
                prev = self.above[k - 1]
                x -= prev["hw"] + g.gap + 4 + it["lw"] + it["hw"] + 2
            elif it["kind"] == "stub":                  # nothing feeds the POI at its anchor: stubs to the left
                x = -(it["hw"] + 6 + it["lw"] + g.gap)
            it["x"] = x
        need = g.bar / 2.0 + 2.5 + self.block_h(root.lbl) + 6.0
        for it in self.above:
            need = max(need, g.bar / 2.0 + 2.5 + self.block_h(root.lbl) + 6.0 + 2 * max(self.block_h(it["lines"]) / 2.0,
                                                                                         10.0) + 10.0)
        g.top = g.head + max(g.pitch, need + 10.0)

    # ---------------------------------------------------------------- rows
    def rows(self):
        pre = _preorder(self.root)
        self.pre = pre

        def assign(forced, bottom):
            for n in pre:
                if n is self.root:
                    n.row = 0
                elif id(n) in forced:
                    n.row = bottom
                else:
                    n.row = n.parent.row if n.mode == "beside" else n.parent.row + 1
        assign(set(), 0)
        leaves = [n for n in pre if n.kind == "bus" and n.unit
                  and not [k for k in n.kids if k.kind != "hang" and k.path]]
        self.bottom = max([n.row for n in leaves] or [0])
        assign(set(id(n) for n in leaves), self.bottom)
        self.leaves = leaves
        self.maxrow = max(n.row for n in pre)
        for n in pre:
            n.yb = self.Y(n.row)
            if n.mode == "beside" and n.parent.kind == "star":
                n.yb += 0.5 * self.g.r2                 # level with its winding's circle

    def Y(self, r):
        return self.g.top + r * self.g.pitch

    SLICE = 6.0                                     # contour resolution (points): a skyline, not one box per row

    def slices(self, y0, y1):
        """The contour slices a vertical extent covers."""
        s0 = int(math.floor((y0 + 0.01) / self.SLICE))
        s1 = int(math.floor((y1 - 0.01) / self.SLICE))
        return range(s0, max(s0, s1) + 1)

    # ---------------------------------------------------------------- dashed connections
    def prep_lanes(self):
        idx = dict((id(n), i) for i, n in enumerate(self.pre))
        for k, e in enumerate(sorted(self.P.nontree, key=lambda e: (_nkey(e.u), _nkey(e.v), e.order))):
            a, b = self.N.get(e.u), self.N.get(e.v)
            if a is None or b is None:
                continue
            if idx[id(a)] > idx[id(b)]:
                a, b = b, a
            x, desc = b, False
            while x is not None:
                if x is a:
                    desc = True
                    break
                x = x.parent
            la, lb = ["R", 0.0, k], ["R" if desc else "L", 0.0, k]
            a.lanes.append(la)
            b.lanes.append(lb)
            self.loops.append({"e": e, "a": a, "b": b, "la": la, "lb": lb, "tag": "L%d" % (k + 1)})

    # ---------------------------------------------------------------- tidy placement
    def layout(self):
        for n in reversed(self.pre):
            self.lay(n)

    def lay(self, n):
        g = self.g
        below = [k for k in n.kids if k.mode != "beside"]
        beside = [k for k in n.kids if k.mode == "beside"]
        offs, acc = [], {}
        for k in below:
            off = 0.0 if not acc else _fit(acc, k.contour, g.gap)
            offs.append(off)
            acc = _merge(acc, k.contour, off)
        pk = [i for i, k in enumerate(below) if k.kind != "hang" and k.path]
        if n.kind == "star" and len(pk) == 1:
            a = offs[pk[0]] + self.star_s()             # the one unit path straight under its winding
        elif pk:
            a = (offs[pk[0]] + offs[pk[-1]]) / 2.0
        elif below:
            a = (offs[0] + offs[-1]) / 2.0
        else:
            a = 0.0
        for i, k in enumerate(below):
            k.off = offs[i] - a
        acc = dict((r, [v[0] - a, v[1] - a]) for r, v in acc.items())
        # lanes of dashed connections: outside this subtree, below it
        below_bar = int(math.floor((n.yb + 6.0) / self.SLICE))
        sub = [v for r, v in acc.items() if r > below_bar]
        right = max([v[1] for v in sub] + [0.0]) + g.gap * 0.6
        left = min([v[0] for v in sub] + [0.0]) - g.gap * 0.6
        for ln in n.lanes:
            if ln[0] == "R":
                ln[1] = right
                right += g.lane
            else:
                ln[1] = left
                left -= g.lane
        n.prims = self.geom(n)
        own = {}
        for p in n.prims:
            x0, y0, x1, y1 = self.bbox(p)
            for r in self.slices(y0, y1):
                if r in own:
                    own[r][0] = min(own[r][0], x0)
                    own[r][1] = max(own[r][1], x1)
                else:
                    own[r] = [x0, x1]
        c = _merge(acc, own)
        for k in beside:
            k.off = _fit(c, k.contour, g.gap)
            c = _merge(c, k.contour, k.off)
        n.contour = c

    def place(self):
        root = self.root
        root.x = 0.0
        for n in self.pre:
            if n is not root:
                n.x = n.parent.x + n.off
        lo = min(v[0] for v in root.contour.values())
        hi = max(v[1] for v in root.contour.values())
        self.diag_w = hi - lo
        self.x0 = -lo
        for n in self.pre:                              # a twin's winding lands on the bus both feed: its bar reaches it
            if n.twin is not None:
                cn = n.twin[1]
                need = n.x - self.star_s() - cn.x + self.g.pad
                if need > cn.bar[1]:
                    cn.bar = (cn.bar[0], need)
                    b = list(cn.prims[0])
                    b[3] = need
                    cn.prims[0] = tuple(b)

    # ---------------------------------------------------------------- geometry
    def star_s(self):
        return self.g.r2 * 0.76

    def col(self, kv, live=True):
        return _level(kv)[1] if live else GREY

    def bbox(self, p):
        t = p[0]
        if t == "L":
            _t, x1, y1, x2, y2, col, lw, dash = p[:8]
            h = lw / 2.0
            return min(x1, x2) - h, min(y1, y2) - h, max(x1, x2) + h, max(y1, y2) + h
        if t == "C":
            cx, cy, r, lw = p[1], p[2], p[3], p[5]
            return cx - r - lw / 2, cy - r - lw / 2, cx + r + lw / 2, cy + r + lw / 2
        if t in ("P", "B"):
            pts = p[1]
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            return min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1
        if t == "T":
            _t, x, y, s, size, bold, col, anchor = p[:8]
            w = tw(s, size, bold)
            x0 = x if anchor == "start" else (x - w / 2.0 if anchor == "middle" else x - w)
            return x0, y - 0.75 * size, x0 + w, y + 0.22 * size
        if t in ("R", "S"):
            return p[1], p[2], p[3], p[4]
        raise ValueError(t)

    def text_block(self, out, x, ytop, lines, col, anchor="start", meta=""):
        """Label lines from `ytop` down; '@u' / '@d' / '@l' / '@r' lines start with a flow arrow."""
        y = ytop
        for t, bold, size in lines:
            y += 0.75 * size if y == ytop else self.g.lh
            if t.startswith("@"):
                d, s = t[1], t[2:]
                w = 7.0 + tw(s, size, bold)
                x0 = x if anchor == "start" else (x - w / 2.0 if anchor == "middle" else x - w)
                ax, ay = x0 + 2.6, y - 0.3 * size
                tri = {"u": [(ax - 2.4, ay + 2.0), (ax + 2.4, ay + 2.0), (ax, ay - 2.6)],
                       "d": [(ax - 2.4, ay - 2.0), (ax + 2.4, ay - 2.0), (ax, ay + 2.6)],
                       "l": [(ax + 2.0, ay - 2.4), (ax + 2.0, ay + 2.4), (ax - 2.6, ay)],
                       "r": [(ax - 2.0, ay - 2.4), (ax - 2.0, ay + 2.4), (ax + 2.6, ay)]}[d]
                out.append(("P", tri, col, 0.6, False, col, True, ""))
                out.append(("T", x0 + 7.0, y, s, size, bold, col, "start", meta))
            else:
                out.append(("T", x, y, t, size, bold, col, anchor, meta))
        return y

    def block_h(self, lines):
        if not lines:
            return 0.0
        return 0.75 * lines[0][2] + self.g.lh * (len(lines) - 1) + 0.22 * lines[-1][2]

    def geom(self, n):
        if n.kind == "bus":
            return self.geom_bus(n)
        if n.kind == "star":
            return self.geom_star(n)
        return self.geom_hang(n)

    def geom_bus(self, n):
        g, P = self.g, self.P
        out = []
        Y = n.yb
        d = P.bus(n.key)
        bcol = self.col(d["kv"], n.live)
        dash = not n.live
        pts = list(n.cx)
        if n is self.root:
            pts += [it["x"] for it in self.above]
        for k in n.kids:
            if k.mode == "beside":
                continue
            pts += [k.off + x for x in k.cx]
        pts += [ln[1] for ln in n.lanes]
        if not pts:
            pts = [0.0]
        lo, hi = min(pts) - g.pad, max(pts) + g.pad
        if hi - lo < 2 * g.half:
            mid = (lo + hi) / 2.0
            lo, hi = mid - g.half, mid + g.half
        n.bar = (lo, hi)
        meta = 'class="bar" data-bus="%d" data-row="%d" data-parent="%s" data-mode="%s"' % (
            n.key, n.row, "" if n.parent is None else n.parent.key, n.mode)
        out.append(("L", lo, Y, hi, Y, bcol, g.bar, dash, meta))
        # its connection up: circuits to the bus (or star) that feeds it
        if n.mode == "below" and n.parent is not None:
            p = n.parent
            ytop = (self.Y(p.row) + g.elb) if p.kind == "star" else (p.yb + g.bar / 2.0)
            ybot = Y - g.bar / 2.0
            hmax = max([self.block_h(x) for x in n.clbl] + [0.0])
            yc = ybot - 2.5 - self.block_h(n.lbl) - 4.0 - max(1.62 * g.r2, hmax / 2.0)
            for j, e in enumerate(n.circ):
                x = n.cx[j]
                on = e.on and n.live
                lcol = bcol if on else GREY
                wmeta = 'class="wire" data-from="%d" data-to="%s"' % (n.key, p.key)
                lines = n.clbl[j]
                if p.kind == "star":
                    out.append(("L", x, ytop, x, ybot, lcol, 1.2, not on, wmeta))
                    continue
                if e.br.kind == "2W":
                    r = g.r2
                    ucol = self.col(P.bus(p.key)["kv"], on)
                    out.append(("L", x, ytop, x, yc - 0.62 * r - r, ucol, 1.2, not on, wmeta))
                    out.append(("C", x, yc - 0.62 * r, r, ucol, 1.2, None, not on, 'class="xf"'))
                    out.append(("C", x, yc + 0.62 * r, r, lcol, 1.2, None, not on, 'class="xf"'))
                    out.append(("L", x, yc + 0.62 * r + r, x, ybot, lcol, 1.2, not on, wmeta))
                else:
                    out.append(("L", x, ytop, x, ybot, lcol, 1.2, not on, wmeta))
                    if e.br.zero:
                        out.append(("R", x - 2.6, yc - 2.6, x + 2.6, yc + 2.6, lcol, 0.8, lcol, 'class="tie"'))
                if lines:
                    h = self.block_h(lines)
                    self.text_block(out, x + self.circ_hw(e) + 4, yc - h / 2.0, lines, INK if on else GREY)
        elif n.mode == "beside":
            lines = n.clbl[0] if n.clbl else []
            h = self.block_h(lines)
            w = self.block_w(lines)
            reach = max(28.0, w + 12.0)
            out.append(("S", lo - reach, Y - h - 4.0, lo, Y + 1.0))
            if lines:
                self.text_block(out, lo - 6.0, Y - 3.5 - h, lines, INK, anchor="end")
        # its own label, above the bar, right of the connection up
        if n is self.root:
            lx = (self.above[0]["x"] if self.above and self.above[0]["kind"] != "stub" else 0.0) + 5.0
        elif n.mode == "beside":
            lx = lo + 4.0
        else:
            lx = max(n.cx) + 5.0
        lines = n.lbl
        h = self.block_h(lines)
        self.text_block(out, lx, Y - g.bar / 2.0 - 2.5 - h, lines, INK if n.live else GREY,
                        meta='data-label="bus"')
        if n is self.root:
            out += self.geom_above(n)
        out += self.geom_lanes(n, Y, lo, hi)
        return out

    def above_y(self, it):
        """The centre height of an item on the POI, above the POI's own label (y up = smaller)."""
        g = self.g
        base = self.Y(0) - g.bar / 2.0 - 2.5 - self.block_h(self.root.lbl) - 6.0
        h = self.block_h(it["lines"])
        if it["kind"] == "stub":
            return base - max(h / 2.0, 4.0) - 6.0
        return base - max(h / 2.0, 10.0 if it["kind"] == "ib" else g.rg)

    def geom_above(self, n):
        g, P = self.g, self.P
        out = []
        Y = self.Y(n.row) - g.bar / 2.0
        for it in self.above:
            x = it["x"]
            yc = self.above_y(it)
            h = self.block_h(it["lines"])
            if it["kind"] == "ib":
                col = self.col(P.bus(n.key)["kv"])
                out.append(("L", x, Y, x, yc + 10.0, col, 1.4, False, 'class="wire"'))
                x0, y0, x1, y1 = x - 10.0, yc - 10.0, x + 10.0, yc + 10.0
                out.append(("R", x0, y0, x1, y1, INK, 1.2, "#FFFFFF", 'class="ib"'))
                for c in (-14.0, -7.0, 0.0, 7.0, 14.0):    # hatching, drawn over the white square
                    pts = self._hatch(x0, y0, x1, y1, c)
                    if pts:
                        out.append(("P", pts, INK, 0.6, False, None, False, ""))
                self.text_block(out, x1 + 4.0, yc - h / 2.0, it["lines"], INK)
            elif it["kind"] == "src":
                col = self.col(P.bus(n.key)["kv"], it["rec"]["st"] > 0)
                out.append(("L", x, Y, x, yc + g.rg, col, 1.2, it["rec"]["st"] <= 0, 'class="wire"'))
                out += self.gen_symbol(x, yc, col, it["rec"]["st"] <= 0)
                self.text_block(out, x + g.rg + 4.0, yc - h / 2.0, it["lines"], INK)
            else:
                br, far = it["rec"]
                on = any(br.wnd_on())
                col = self.col(P.bus(far[0])["kv"], on)
                top = yc - 4.0
                out.append(("L", x, Y, x, top + 2.0, col, 1.2, not on, 'class="stub"'))
                out.append(("P", [(x - 3.4, top + 5.0), (x + 3.4, top + 5.0), (x, top - 4.0)], col, 0.8, False,
                            col, True, ""))
                self.text_block(out, x + 6.0, yc - h / 2.0, it["lines"], INK if on else GREY)
        return out

    @staticmethod
    def _hatch(x0, y0, x1, y1, c):
        """The part inside the square of the line through (x0 + c, y1) and up-right at 45 degrees."""
        # the line x - x0 = (y1 - y) + c, i.e. y = y1 + c - (x - x0)
        lo = max(x0, x0 + c)
        hi = min(x1, x0 + c + (y1 - y0))
        if hi - lo < 0.5:
            return None
        pts = [(lo, y1 + c - (lo - x0)), (hi, y1 + c - (hi - x0))]
        return pts

    def gen_symbol(self, x, cy, col, dash):
        r = self.g.rg
        out = [("C", x, cy, r, col, 1.3, "#FFFFFF", dash, 'class="gen"')]
        w, a = 0.62 * r, 0.42 * r
        x0 = x - w
        out.append(("B", [(x0, cy), (x0 + 0.36 * w, cy - 1.33 * a), (x0 + 0.64 * w, cy - 1.33 * a), (x, cy),
                          (x + 0.36 * w, cy + 1.33 * a), (x + 0.64 * w, cy + 1.33 * a), (x + w, cy)], col, 1.1, ""))
        return out

    def geom_star(self, n):
        g, P = self.g, self.P
        out = []
        Y = self.Y(n.row)
        r, s = g.r2, self.star_s()
        pcol = self.col(P.bus(n.parent.key)["kv"], n.live)
        ytop = n.parent.yb + g.bar / 2.0
        top_c = (0.0, Y - 0.62 * r)
        out.append(("L", 0.0, ytop, 0.0, top_c[1] - r, pcol, 1.2, not n.live, 'class="wire"'))
        kids = [k for k in n.kids if k.kind != "hang" and k.mode != "beside"]
        side_kid = [k for k in n.kids if k.mode == "beside"]
        sides = {}
        for k in kids:
            sides[id(k)] = -1.0 if (k.off < -0.01 or (len(kids) == 1 and k.off <= 0.01)) else 1.0
        if len(kids) == 2 and sides[id(kids[0])] == sides[id(kids[1])]:
            sides[id(kids[0])], sides[id(kids[1])] = -1.0, 1.0
        colors, dashed = {}, {}
        for k in kids:
            colors[sides[id(k)]], dashed[sides[id(k)]] = self.col(P.bus(k.key)["kv"], k.live), not k.live
        for k in side_kid:
            colors[1.0], dashed[1.0] = self.col(P.bus(k.key)["kv"], k.live), not k.live
        br = n.circ[0].br
        used = set([n.circ[0].wnd] + [k.circ[0].wnd for k in kids + side_kid if k.circ])
        rest = [w for w in (1, 2, 3) if w not in used]
        for sd in (-1.0, 1.0):                          # a winding drawn no child of its own: its own colour
            if sd not in colors:
                w = rest.pop(0) if rest else None
                on = w is not None and br.wnd_on()[w - 1] and n.live
                colors[sd] = self.col(P.bus((br.a, br.b, br.c)[w - 1])["kv"], on) if w else GREY
                dashed[sd] = not on
        out.append(("C", top_c[0], top_c[1], r, pcol, 1.2, None, not n.live,
                    'class="xf3" data-star="%d" data-parent="%d" data-y="%s"' % (n.key, n.parent.key, _n(Y))))
        for sd in (-1.0, 1.0):
            out.append(("C", sd * s, Y + 0.5 * r, r, colors[sd], 1.2, None, dashed[sd], 'class="xf3"'))
        ye = Y + g.elb
        for k in kids:
            sd = sides[id(k)]
            on = k.live
            kc = self.col(P.bus(k.key)["kv"], on)
            kx = k.off + (k.cx[0] if k.cx else 0.0)
            out.append(("L", sd * s, Y + 0.5 * r + r, sd * s, ye, kc, 1.2, not on, 'class="wire"'))
            if abs(kx - sd * s) > 0.05:
                out.append(("L", sd * s, ye, kx, ye, kc, 1.2, not on, 'class="wire"'))
        if n.twin is not None:                          # a twin: its winding straight down to the bus both feed
            b, cn, e = n.twin
            ccol = self.col(P.bus(cn.key)["kv"], e.on and cn.live)
            colors[-1.0] = ccol
            out = [q for q in out if not (q[0] == "C" and abs(q[1] + s) < 1e-6)]
            out.append(("C", -s, Y + 0.5 * r, r, ccol, 1.2, None, False, 'class="xf3"'))
            out.append(("L", -s, Y + 0.5 * r + r, -s, cn.yb - g.bar / 2.0, ccol, 1.2, not e.on,
                        'class="wire" data-from="%d" data-to="%d"' % (cn.key, n.key)))
        lines = n.lbl
        h = self.block_h(lines)
        ytop = min(Y - h / 2.0, Y + g.elb - 4.0 - h)    # a tall label rises: its foot stays above the elbows
        if side_kid:                                    # its tertiary is on the right: the label on the left
            self.text_block(out, -s - r - 4.0, ytop, lines, INK if n.live else GREY, anchor="end")
        else:
            self.text_block(out, s + r + 4.0, ytop, lines, INK if n.live else GREY)
        out += self.geom_lanes(n, Y, -s - r, s + r)
        return out

    def geom_hang(self, n):
        g, P = self.g, self.P
        out = []
        p = n.parent
        Yp = p.yb + g.bar / 2.0
        kind, r = n.item
        kv = P.bus(p.key)["kv"]
        on = n.live
        col = self.col(kv, on)
        dash = not on
        if kind == "gen":
            out.append(("L", 0.0, Yp, 0.0, Yp + 9.0, col, 1.2, dash, 'class="wire"'))
            cy = Yp + 9.0 + g.rg
            out += self.gen_symbol(0.0, cy, col, dash)
            out[-2] = out[-2][:8] + ('class="gen" data-bus="%d" data-id="%s" data-row="%d"' % (p.key, r["id"], p.row),)
            ytxt = cy + g.rg + 3.0
        elif kind == "load":
            out.append(("L", 0.0, Yp, 0.0, Yp + 11.0, col, 1.2, dash, 'class="wire"'))
            out.append(("P", [(-4.6, Yp + 11.0), (4.6, Yp + 11.0), (0.0, Yp + 20.0)], col, 0.8, False, col, True,
                        'class="load"'))
            ytxt = Yp + 23.0
        else:
            b = r["b"]
            if kind == "fxsh" and (b or 0) < 0:
                out.append(("L", 0.0, Yp, 0.0, Yp + 8.0, col, 1.2, dash, 'class="wire"'))
                y = Yp + 8.0
                pts = [(0.0, y)]
                for k in range(3):
                    y0 = y + k * 4.4
                    pts += [(3.6, y0), (3.6, y0 + 4.4), (0.0, y0 + 4.4)]
                out.append(("B", pts, col, 1.1, ""))
                yg = y + 13.2 + 2.0
                out.append(("L", 0.0, y + 13.2, 0.0, yg, col, 1.2, dash, ""))
            else:
                out.append(("L", 0.0, Yp, 0.0, Yp + 10.0, col, 1.2, dash, 'class="wire"'))
                out.append(("L", -7.0, Yp + 10.0, 7.0, Yp + 10.0, col, 1.8, dash, 'class="cap"'))
                out.append(("L", -7.0, Yp + 14.5, 7.0, Yp + 14.5, col, 1.8, dash, 'class="cap"'))
                yg = Yp + 20.0
                out.append(("L", 0.0, Yp + 14.5, 0.0, yg, col, 1.2, dash, ""))
                if kind == "swsh":
                    out.append(("L", -8.0, Yp + 19.0, 7.0, Yp + 6.0, col, 0.8, False, ""))
                    out.append(("P", [(7.9, Yp + 5.2), (4.3, Yp + 6.4), (6.3, Yp + 8.7)], col, 0.6, False, col,
                                True, ""))
            for k, hw in enumerate((6.0, 3.8, 1.6)):
                out.append(("L", -hw, yg + 2.4 * k, hw, yg + 2.4 * k, col, 1.0, False, ""))
            ytxt = yg + 8.0
        self.text_block(out, 0.0, ytxt, n.lbl, INK if on else GREY, anchor="middle")
        return out

    def geom_lanes(self, n, Y, lo, hi):
        """The lanes of n's dashed connections: from its bar (or its star) down
           past the bottom row (the channels below are drawn once placed)."""
        out = []
        yend = self.Y(self.maxrow) + 6.0
        for side, x, k in n.lanes:
            L = [q for q in self.loops if q["la"][2] == k][0]
            col = self._loop_col(L)
            if n.kind == "star":
                ex = hi if side == "R" else lo
                out.append(("L", ex, Y, x, Y, col, 1.0, True, 'class="loop"'))
            out.append(("L", x, Y, x, yend, col, 1.0, True, 'class="loop"'))
        return out

    def _loop_col(self, L):
        e = L["e"]
        if not e.on or not L["a"].live or not L["b"].live:
            return GREY
        b = e.u if e.u > 0 else e.v
        return self.col(self.P.bus(b)["kv"])

    # ---------------------------------------------------------------- drawing
    def size(self):
        g = self.g
        self.ybottom = self.Y(self.maxrow) + 6.0
        nl = len(self.loops)
        self.ych = self.ybottom + 8.0
        self.yfoot = self.ych + nl * g.chp + (14.0 if nl else 8.0)
        self.foot = self.footer_parts()
        fw = self.foot["w"]
        self.W = max(self.diag_w + 2 * 28.0, fw + 2 * 28.0, 420.0)
        self.H = self.yfoot + self.foot["h"] + 26.0
        self.dx = (self.W - self.diag_w) / 2.0 + self.x0

    def draw(self, cv):
        g = self.g
        W, H = self.W, self.H
        cv.rect(8, 8, W - 8, H - 8, INK, 1.4, None, check=False)
        cv.rect(12, 12, W - 12, H - 12, INK, 0.5, None, check=False)
        P = self.P
        d = P.bus(P.poi)
        k = g.k
        cv.text(24, 20 + 14 * k, "%s  --  one-line diagram" % self.stem, 13.0 * k, INK, bold=True)
        nu = len(P.units)
        cv.text(24, 20 + 27 * k, "POI %d %s %s kV   |   %d unit%s, %.1f MW   |   traced from every unit to the POI"
                % (P.poi, d["name"], _kv(d["kv"]), nu, "" if nu == 1 else "s", sum(m["p"] for m in P.units)),
                7.5 * k, SOFT)
        dx = self.dx
        for layer in ("L", "RCPB", "T"):              # connectors, then symbols over them, then text
            for n in self.pre:
                for p in n.prims:
                    if p[0] in layer:
                        self.emit(cv, p, n.x + dx)
            if layer == "L":
                self.draw_across(cv, dx)
                self.draw_channels(cv, dx)
        self.draw_footer(cv)

    def emit(self, cv, p, ox):
        t = p[0]
        if t == "L":
            _t, x1, y1, x2, y2, col, lw, dash = p[:8]
            meta = p[8] if len(p) > 8 else ""
            cv.line(x1 + ox, y1, x2 + ox, y2, col, lw, dash, meta=meta)
        elif t == "C":
            _t, cx, cy, r, col, lw, fill, dash = p[:8]
            cv.circle(cx + ox, cy, r, col, lw, fill, dash, meta=p[8] if len(p) > 8 else "")
        elif t == "P":
            _t, pts, col, lw, dash, fill, closed = p[:7]
            cv.poly([(x + ox, y) for x, y in pts], col, lw, dash, fill, closed, meta=p[7] if len(p) > 7 else "")
        elif t == "B":
            _t, pts, col, lw = p[:4]
            cv.curve([(x + ox, y) for x, y in pts], col, lw)
        elif t == "R":
            _t, x0, y0, x1, y1, col, lw, fill = p[:8]
            cv.rect(x0 + ox, y0, x1 + ox, y1, col, lw, fill, meta=p[8] if len(p) > 8 else "")
        elif t == "T":
            _t, x, y, s, size, bold, col, anchor = p[:8]
            cv.text(x + ox, y, s, size, col, bold, anchor, meta=p[8] if len(p) > 8 else "")

    def draw_across(self, cv, dx):
        """The connector of each feeder that runs across: from the end of the
           bar it continues to the start of the next bar."""
        g = self.g
        for n in self.pre:
            if n.mode != "beside":
                continue
            p = n.parent
            Y = n.yb
            x1 = p.x + dx + (self.star_s() + g.r2 if p.kind == "star" else p.bar[1])
            x2 = n.x + n.bar[0] + dx
            e = n.circ[0]
            col = self.col(self.P.bus(n.key)["kv"], e.on and n.live)
            cv.line(x1, Y, x2, Y, col, 1.2, not (e.on and n.live),
                    meta='class="wire" data-from="%d" data-to="%d"' % (n.key, p.key))
            if e.br.zero:                               # a bus tie: closed, as a filled square
                xm = (x1 + x2) / 2.0
                cv.rect(xm - 2.6, Y - 2.6, xm + 2.6, Y + 2.6, col, 0.8, col, meta='class="tie"')

    def draw_channels(self, cv, dx):
        g = self.g
        if not self.loops:
            return
        spans = []
        for L in self.loops:
            xa, xb = L["a"].x + L["la"][1] + dx, L["b"].x + L["lb"][1] + dx
            spans.append((abs(xb - xa), L["tag"], L, xa, xb))
        spans.sort(key=lambda s: (s[0], s[1]))
        lanes_x = [s[3] for s in spans] + [s[4] for s in spans]
        for lev, (_w, tag, L, xa, xb) in enumerate(spans):
            y = self.ych + lev * g.chp
            col = self._loop_col(L)
            m = 'class="loop"'
            cv.line(xa, self.ybottom, xa, y, col, 1.0, True, meta=m)
            cv.line(xb, self.ybottom, xb, y, col, 1.0, True, meta=m)
            cv.line(min(xa, xb), y, max(xa, xb), y, col, 1.0, True, meta=m)
            for x, end in ((min(xa, xb), "start"), (max(xa, xb), "end")):
                w = tw(tag, g.f, True)
                tx = x + 2.5 if end == "start" else x - 2.5
                x0, x1 = (tx, tx + w) if end == "start" else (tx - w, tx)
                if [v for v in lanes_x if x0 - 1.0 < v < x1 + 1.0]:
                    end = "end" if end == "start" else "start"
                    tx = x - 2.5 if end == "end" else x + 2.5
                cv.text(tx, y - 1.6 - 0.22 * g.f, tag, g.f, col if col != GREY else SOFT, True, end,
                        meta='data-label="loop"')
            L["level"] = lev

    # ---------------------------------------------------------------- footer
    def footer_parts(self):
        g, P = self.g, self.P
        present = set()
        kvs = collections.defaultdict(set)
        for n in self.pre:
            if n.kind == "bus":
                kv = P.bus(n.key)["kv"]
                kvs[_level(kv)[0]].add(kv)
                for e in n.circ:
                    if e.br.kind != "3W":
                        present.add({"LINE": "tie" if e.br.zero else "line", "2W": "2w"}[e.br.kind])
                    if not e.on:
                        present.add("oos")
                if not n.live:
                    present.add("oos")
            elif n.kind == "star":
                present.add("3w")
            else:
                kind, r = n.item
                present.add({"gen": "gen", "load": "load", "swsh": "swsh"}.get(kind) or
                            ("reactor" if (r["b"] or 0) < 0 else "cap"))
                if not n.live:
                    present.add("oos")
        for it in self.above:
            present.add(it["kind"])
            if it["kind"] == "stub" and not any(it["rec"][0].wnd_on()):
                present.add("oos")
        if self.loops:
            present.add("loop")
        self.present, self.kvs = present, kvs
        rows = [("VOLTAGE LEVELS", None)]
        for lo, name, col in LEVELS:
            if name in kvs:
                rows.append(("lvl", (name, col, ", ".join("%s kV" % _kv(v) for v in sorted(kvs[name], reverse=True)))))
        syms = [("gen", "Generating unit (P, Q, Mbase)"), ("2w", "Two-winding transformer"),
                ("3w", "Three-winding transformer (star point)"), ("line", "Line"),
                ("tie", "Zero-impedance tie"), ("load", "Load"), ("cap", "Capacitor (fixed shunt)"),
                ("reactor", "Reactor (fixed shunt)"), ("swsh", "Switched shunt"),
                ("ib", "Infinite bus / grid equivalent at the POI"), ("src", "Source at the POI"),
                ("stub", "Grid connection at the POI"), ("oos", "Out of service / de-energised"),
                ("loop", "Loop or out-of-service branch between drawn buses")]
        sym_rows = [("sym", (k, t)) for k, t in syms if k in present]
        notes = []
        if SHOW_FLOWS:
            notes.append("Flows: MW / Mvar of each branch as PSS/E holds them; the arrow shows the direction of the MW.")
        if SHOW_IMPEDANCE:
            notes.append("R + jX and X: pu on the system base (%g MVA). Bus V: pu and angle." % self.mva)
        notes.append("Voltages and flows: %s." % self.state)
        notes.append("Drawn from the network data only (no snapshot, no .dyr, no dynamics).")
        loops = []
        for L in self.loops:
            e = L["e"]
            if e.br.kind == "3W":
                what = "%s, winding %d to %s" % (e.br.label(), e.wnd, P.bname(e.u if e.u > 0 else e.v))
            else:
                what = e.br.label()
            loops.append("%s: %s%s" % (L["tag"], what, "" if e.on else " (out of service)"))
        lw = max([tw(t, g.f) for t in notes + loops] + [180.0 * g.k])
        sw = 0.0
        for k, r in rows + sym_rows:
            if k == "lvl":
                sw = max(sw, 36 * g.k + tw("%s  %s" % (r[0], r[2]), g.f))
            elif k == "sym":
                sw = max(sw, 36 * g.k + tw(r[1], g.f))
        tb = self.title_rows()
        tbw = max(tw(t, s, b) for t, s, b in tb) + 20.0
        left_h = 12 + self.leg_dy() * (len(rows) + len(sym_rows) + 1)
        right_h = 12 + g.lh * (len(notes) + (len(loops) + 1 if loops else 0)) + 8 + 11 * g.k
        tb_h = 10 + sum(s * 1.45 for t, s, b in tb) + 6
        h = max(left_h, right_h, tb_h) + 8
        w = sw + 24 + lw + 24 + tbw
        return {"rows": rows + sym_rows, "notes": notes, "loops": loops, "sw": sw, "lw": lw, "tbw": tbw,
                "tb": tb, "h": h, "w": w}

    def title_rows(self):
        P = self.P
        d = P.bus(P.poi)
        n_on = len([m for m in P.units if m["st"] > 0])
        mw = sum(m["p"] for m in P.units if m["st"] > 0)
        mva = sum(m["mbase"] for m in P.units)
        folder = _u(os.path.dirname(self.case_path or ""))
        if len(folder) > 70:
            folder = "..." + folder[-67:]
        k = self.g.k
        rows = [("PLANT ONE-LINE DIAGRAM", 10.5 * k, True),
                ("Case:  %s" % _u(os.path.basename(self.case_path or "(the case open in PSS/E)")), 8.0 * k, True),
                ("Folder:  %s" % (folder or "-"), 6.5 * k, False),
                ("POI:  %d %s  %s kV" % (P.poi, d["name"], _kv(d["kv"])), 8.0 * k, True),
                ("Found as:  %s" % P.how, 6.5 * k, False),
                ("Units:  %d (%d in service)   %.1f MW   Mbase %.0f MVA" % (len(P.units), n_on, mw, mva), 8.0 * k,
                 False),
                ("Drawn:  %s by z7_draw_sld.py   (PSS/E %s, Python %d.%d)"
                 % (self.stamp, PSSE_MAJOR or "?", sys.version_info[0], sys.version_info[1]), 6.5 * k, False)]
        return rows

    def leg_dy(self):
        return 11.0 * self.g.k

    def draw_footer(self, cv):
        g, F = self.g, self.foot
        y0 = self.yfoot
        x0 = 24.0
        k = g.k
        dy = self.leg_dy()
        cv.line(16, y0 - 8, self.W - 16, y0 - 8, INK, 0.5, False, check=False)
        y = y0 + 6
        for key, r in F["rows"]:
            y += dy
            if r is None:
                cv.text(x0, y, key, 7.5 * k, INK, True)
                continue
            if key == "lvl":
                name, col, kvs = r
                cv.line(x0, y - 0.3 * g.f, x0 + 26 * k, y - 0.3 * g.f, col, g.bar, False, check=False)
                cv.text(x0 + 34 * k, y, "%s  %s" % (name, kvs), g.f, INK)
            else:
                sym, t = r
                self.legend_symbol(cv, sym, x0 + 13 * k, y - 0.3 * g.f)
                cv.text(x0 + 34 * k, y, t, g.f, INK)
        xn = x0 + F["sw"] + 24
        y = y0 + 6 + dy
        cv.text(xn, y, "NOTES", 7.5 * k, INK, True)
        for t in F["notes"]:
            y += g.lh
            cv.text(xn, y, t, g.f, INK)
        if F["loops"]:
            y += g.lh + 3
            cv.text(xn, y, "DASHED CONNECTIONS (routed under the drawing)", 7.5 * k, INK, True)
            for t in F["loops"]:
                y += g.lh
                cv.text(xn, y, t, g.f, INK)
        # title block
        tbw = F["tbw"]
        xt, yt = self.W - 24 - tbw, y0 + 2
        hh = sum(s * 1.45 for t, s, b in F["tb"]) + 12
        cv.rect(xt, yt, xt + tbw, yt + hh, INK, 1.0, None, check=False)
        y = yt + 4
        for i, (t, s, b) in enumerate(F["tb"]):
            y += s * 1.45
            cv.text(xt + 10, y - 2, t, s, INK, b)
            if i in (0, 2, 4):
                cv.line(xt, y + 2.2, xt + tbw, y + 2.2, INK, 0.4, False, check=False)

    def legend_symbol(self, cv, key, x, y):
        c = LEVELS[2][2]
        if key == "gen":
            r = 5.0
            cv.circle(x, y, r, c, 1.0, "#FFFFFF", False, check=False)
            w, a = 0.62 * r, 0.42 * r
            cv.curve([(x - w, y), (x - w + 0.36 * w, y - 1.33 * a), (x - w + 0.64 * w, y - 1.33 * a), (x, y),
                      (x + 0.36 * w, y + 1.33 * a), (x + 0.64 * w, y + 1.33 * a), (x + w, y)], c, 0.8)
        elif key == "2w":
            cv.circle(x - 2.6, y, 3.6, LEVELS[1][2], 0.9, None, False)
            cv.circle(x + 2.6, y, 3.6, c, 0.9, None, False)
        elif key == "3w":
            cv.circle(x - 2.4, y - 1.6, 3.3, LEVELS[1][2], 0.9, None, False)
            cv.circle(x + 2.4, y - 1.6, 3.3, c, 0.9, None, False)
            cv.circle(x, y + 2.2, 3.3, LEVELS[3][2], 0.9, None, False)
        elif key == "line":
            cv.line(x - 12, y, x + 12, y, c, 1.2, False, check=False)
        elif key == "tie":
            cv.line(x - 12, y, x + 12, y, c, 1.2, False, check=False)
            cv.rect(x - 2.6, y - 2.6, x + 2.6, y + 2.6, c, 0.8, c)
        elif key == "load":
            cv.line(x - 8, y, x, y, c, 1.0, False, check=False)
            cv.poly([(x, y - 4), (x, y + 4), (x + 8, y)], c, 0.8, False, c, True)
        elif key in ("cap", "swsh"):
            cv.line(x - 10, y, x - 1.6, y, c, 1.0, False, check=False)
            cv.line(x - 1.6, y - 5, x - 1.6, y + 5, c, 1.6, False, check=False)
            cv.line(x + 1.6, y - 5, x + 1.6, y + 5, c, 1.6, False, check=False)
            cv.line(x + 1.6, y, x + 8, y, c, 1.0, False, check=False)
            if key == "swsh":
                cv.line(x - 6, y + 5, x + 6, y - 5, c, 0.7, False, check=False)
        elif key == "reactor":
            cv.line(x - 10, y, x - 6, y, c, 1.0, False, check=False)
            cv.curve([(x - 6, y), (x - 6, y - 4), (x - 2, y - 4), (x - 2, y), (x - 2, y - 4), (x + 2, y - 4),
                      (x + 2, y), (x + 2, y - 4), (x + 6, y - 4), (x + 6, y)], c, 0.9)
            cv.line(x + 6, y, x + 10, y, c, 1.0, False, check=False)
        elif key == "ib":
            cv.rect(x - 5, y - 5, x + 5, y + 5, INK, 0.9, "#FFFFFF")
            for cc in (-5.0, 0.0, 5.0):
                pts = self._hatch(x - 5, y - 5, x + 5, y + 5, cc)
                if pts:
                    cv.line(pts[0][0], pts[0][1], pts[1][0], pts[1][1], INK, 0.5, False, check=False)
        elif key == "src":
            self.legend_symbol(cv, "gen", x, y)
        elif key == "stub":
            cv.line(x - 10, y, x + 6, y, c, 1.0, False, check=False)
            cv.poly([(x + 3, y - 3), (x + 3, y + 3), (x + 10, y)], c, 0.6, False, c, True)
        elif key == "oos":
            cv.line(x - 12, y, x + 12, y, GREY, 1.2, True, check=False)
        elif key == "loop":
            cv.line(x - 12, y, x + 12, y, c, 1.0, True, check=False)

    # ---------------------------------------------------------------- for PSS/E
    def sld_buses(self):
        """[(bus, x, y)] in diagram inches, the POI first at (0, 0), y up."""
        root = self.root
        Y0 = self.Y(0)
        out = [(root.key, 0.0, 0.0)]
        for n in self.pre:
            if n.kind == "bus" and n is not root:
                out.append((n.key, round((n.x - root.x) / 72.0, 4), round(-(n.yb - Y0) / 72.0, 4)))
        return out


# ============================================================================
# CANVAS -- one drawing routine, two vector outputs (SVG, PDF)
# ============================================================================
def _n(v, nd=2):
    s = "%.*f" % (nd, v)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Canvas(object):
    """What the drawing needs: lines, polylines, circles, rectangles, curves
       and Helvetica text, in points, y down. Labels and connectors are kept
       for the overlap check."""

    def __init__(self, w, h):
        self.w, self.h = float(w), float(h)
        self.texts = []          # (x0, y0, x1, y1, text)
        self.segs = []           # (x1, y1, x2, y2, half width)
        self.shapes = []         # (x0, y0, x1, y1) of symbols

    def line(self, x1, y1, x2, y2, col, lw=1.0, dash=False, meta="", check=True):
        if check:
            self.segs.append((x1, y1, x2, y2, lw / 2.0))
        self._line(x1, y1, x2, y2, col, lw, dash, meta)

    def poly(self, pts, col, lw=1.0, dash=False, fill=None, closed=False, meta="", check=True):
        if check:
            self.shapes.append((min(p[0] for p in pts), min(p[1] for p in pts),
                                max(p[0] for p in pts), max(p[1] for p in pts)))
        self._poly(pts, col, lw, dash, fill, closed, meta)

    def circle(self, cx, cy, r, col, lw=1.0, fill=None, dash=False, meta="", check=True):
        if check:
            self.shapes.append((cx - r, cy - r, cx + r, cy + r))
        self._circle(cx, cy, r, col, lw, fill, dash, meta)

    def rect(self, x0, y0, x1, y1, col, lw=1.0, fill=None, meta="", check=True):
        if check:
            self.shapes.append((x0, y0, x1, y1))
        self._rect(x0, y0, x1, y1, col, lw, fill, meta)

    def curve(self, pts, col, lw=1.0):
        """pts: start point, then three points per cubic Bezier segment."""
        self._curve(pts, col, lw)

    def text(self, x, y, s, size, col=INK, bold=False, anchor="start", meta=""):
        s = _u(s)
        w = tw(s, size, bold)
        x0 = x if anchor == "start" else (x - w / 2.0 if anchor == "middle" else x - w)
        box = (x0, y - 0.75 * size, x0 + w, y + 0.22 * size)
        self.texts.append(box + (s,))
        self._text(x0, y, s, size, col, bold, box, meta)

    def overlaps(self):
        """[(what, text, other)] -- labels on labels or on connectors, or off the sheet."""
        out = []
        T = sorted(self.texts)
        for i in range(len(T)):
            a = T[i]
            if a[0] < 0 or a[1] < 0 or a[2] > self.w or a[3] > self.h:
                out.append(("off the sheet", a[4], ""))
            for j in range(i + 1, len(T)):
                b = T[j]
                if b[0] >= a[2] - 0.3:
                    break
                if b[1] < a[3] - 0.3 and a[1] < b[3] - 0.3 and b[0] < a[2] - 0.3:
                    out.append(("label on label", a[4], b[4]))
        for x0, y0, x1, y1, s in self.texts:
            for sx1, sy1, sx2, sy2, h in self.segs:
                if min(sx1, sx2) - h < x1 - 0.3 and max(sx1, sx2) + h > x0 + 0.3 and \
                        min(sy1, sy2) - h < y1 - 0.3 and max(sy1, sy2) + h > y0 + 0.3:
                    out.append(("label on a connector", s, "%.0f,%.0f-%.0f,%.0f" % (sx1, sy1, sx2, sy2)))
                    break
            for a0, b0, a1, b1 in self.shapes:
                if a0 < x1 - 0.3 and a1 > x0 + 0.3 and b0 < y1 - 0.3 and b1 > y0 + 0.3:
                    out.append(("label on a symbol", s, "%.0f,%.0f-%.0f,%.0f" % (a0, b0, a1, b1)))
                    break
        return out


def _esc(t):
    return _u(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


class SvgCanvas(Canvas):
    def __init__(self, w, h, title):
        Canvas.__init__(self, w, h)
        self.title = title
        self.out = []

    @staticmethod
    def _sty(col, lw, dash, fill=None):
        s = 'stroke="%s" stroke-width="%s"' % (col, _n(lw)) if col else 'stroke="none"'
        if dash:
            s += ' stroke-dasharray="4 2.6"'
        s += ' fill="%s"' % (fill or "none")
        return s

    def _line(self, x1, y1, x2, y2, col, lw, dash, meta):
        self.out.append('<line x1="%s" y1="%s" x2="%s" y2="%s" %s%s/>' % (
            _n(x1), _n(y1), _n(x2), _n(y2), self._sty(col, lw, dash), (" " + meta) if meta else ""))

    def _poly(self, pts, col, lw, dash, fill, closed, meta):
        tag = "polygon" if closed else "polyline"
        self.out.append('<%s points="%s" %s stroke-linejoin="round"%s/>' % (
            tag, " ".join("%s,%s" % (_n(x), _n(y)) for x, y in pts), self._sty(col, lw, dash, fill),
            (" " + meta) if meta else ""))

    def _circle(self, cx, cy, r, col, lw, fill, dash, meta):
        self.out.append('<circle cx="%s" cy="%s" r="%s" %s%s/>' % (
            _n(cx), _n(cy), _n(r), self._sty(col, lw, dash, fill), (" " + meta) if meta else ""))

    def _rect(self, x0, y0, x1, y1, col, lw, fill, meta):
        self.out.append('<rect x="%s" y="%s" width="%s" height="%s" %s%s/>' % (
            _n(x0), _n(y0), _n(x1 - x0), _n(y1 - y0), self._sty(col, lw, False, fill), (" " + meta) if meta else ""))

    def _curve(self, pts, col, lw):
        d = "M%s,%s" % (_n(pts[0][0]), _n(pts[0][1]))
        for k in range(1, len(pts) - 2, 3):
            d += " C%s,%s %s,%s %s,%s" % tuple(_n(v) for q in pts[k:k + 3] for v in q)
        self.out.append('<path d="%s" %s/>' % (d, self._sty(col, lw, False)))

    def _text(self, x0, y, s, size, col, bold, box, meta):
        self.out.append('<text xml:space="preserve" x="%s" y="%s" font-size="%s"%s fill="%s" data-box="%s"%s>%s</text>' % (
            _n(x0), _n(y), _n(size), ' font-weight="bold"' if bold else "", col,
            " ".join(_n(v, 1) for v in box), (" " + meta) if meta else "", _esc(s)))

    def render(self):
        head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="%spt" height="%spt" '
                'viewBox="0 0 %s %s" font-family="Helvetica, Arial, \'Liberation Sans\', sans-serif" '
                'xml:space="preserve" style="white-space:pre">\n<title>%s</title>\n'
                '<rect x="0" y="0" width="%s" height="%s" fill="#FFFFFF"/>\n'
                % (_n(self.w), _n(self.h), _n(self.w), _n(self.h), _esc(self.title), _n(self.w), _n(self.h)))
        return head + "\n".join(self.out) + "\n</svg>\n"


class PdfCanvas(Canvas):
    """A one-page PDF written by hand: page sized to the drawing, Helvetica."""
    KAPPA = 0.5522847498

    def __init__(self, w, h, title):
        Canvas.__init__(self, w, h)
        self.title = title
        self.ops = []
        self.st = {}

    def _y(self, y):
        return self.h - y

    @staticmethod
    def _c(col):
        col = col.lstrip("#")
        return " ".join(_n(int(col[i:i + 2], 16) / 255.0, 3) for i in (0, 2, 4))

    def _set(self, key, val, op):
        if self.st.get(key) != val:
            self.ops.append(op)
            self.st[key] = val

    def _pen(self, col, lw, dash):
        self._set("RG", col, self._c(col) + " RG")
        self._set("w", lw, _n(lw) + " w")
        d = "[4 2.6] 0 d" if dash else "[] 0 d"
        self._set("d", d, d)

    def _fill(self, col):
        self._set("rg", col, self._c(col) + " rg")

    def _p(self, x, y):
        return "%s %s" % (_n(x), _n(self._y(y)))

    def _line(self, x1, y1, x2, y2, col, lw, dash, meta):
        self._pen(col, lw, dash)
        self.ops.append("%s m %s l S" % (self._p(x1, y1), self._p(x2, y2)))

    def _poly(self, pts, col, lw, dash, fill, closed, meta):
        if col:
            self._pen(col, lw, dash)
        if fill:
            self._fill(fill)
        s = "%s m " % self._p(*pts[0]) + " ".join("%s l" % self._p(x, y) for x, y in pts[1:])
        if closed:
            s += " h"
        self.ops.append(s + (" B" if fill and col else (" f" if fill else " S")))

    def _circle(self, cx, cy, r, col, lw, fill, dash, meta):
        k = self.KAPPA * r
        y = self._y(cy)
        segs = [(cx + r, y + k, cx + k, y + r, cx, y + r), (cx - k, y + r, cx - r, y + k, cx - r, y),
                (cx - r, y - k, cx - k, y - r, cx, y - r), (cx + k, y - r, cx + r, y - k, cx + r, y)]
        s = "%s %s m " % (_n(cx + r), _n(y)) + " ".join("%s %s %s %s %s %s c" % tuple(_n(v) for v in q)
                                                      for q in segs)
        if col:
            self._pen(col, lw, dash)
        if fill:
            self._fill(fill)
        self.ops.append(s + (" h B" if fill and col else (" h f" if fill else " h S")))

    def _rect(self, x0, y0, x1, y1, col, lw, fill, meta):
        if col:
            self._pen(col, lw, False)
        if fill:
            self._fill(fill)
        self.ops.append("%s %s %s %s re %s" % (_n(x0), _n(self._y(y1)), _n(x1 - x0), _n(y1 - y0),
                                               "B" if fill and col else ("f" if fill else "S")))

    def _curve(self, pts, col, lw):
        self._pen(col, lw, False)
        s = "%s m" % self._p(*pts[0])
        for k in range(1, len(pts) - 2, 3):
            s += " %s %s %s c" % tuple(self._p(*q) for q in pts[k:k + 3])
        self.ops.append(s + " S")

    @staticmethod
    def _pdfstr(s):
        b = _u(s).encode("cp1252", "replace")
        out = []
        for ch in bytearray(b):
            if ch in (40, 41, 92):
                out.append("\\" + chr(ch))
            elif 32 <= ch <= 126:
                out.append(chr(ch))
            else:
                out.append("\\%03o" % ch)
        return "".join(out)

    def _text(self, x0, y, s, size, col, bold, box, meta):
        self._fill(col)
        self.ops.append("BT /%s %s Tf %s Td (%s) Tj ET" % ("F2" if bold else "F1", _n(size), self._p(x0, y),
                                                          self._pdfstr(s)))

    def render(self):
        w, h, k = self.w, self.h, 1.0
        if max(w, h) > 14400.0:                     # the largest page PDF readers take: scaled to fit
            k = 14400.0 / max(w, h)
        body = "\n".join(self.ops)
        if k != 1.0:
            body = "%s 0 0 %s 0 0 cm\n" % (_n(k, 5), _n(k, 5)) + body
        data = zlib.compress(body.encode("latin-1"), 9)

        def b(s):
            return s if isinstance(s, bytes) else s.encode("latin-1")
        stamp = time.strftime("D:%Y%m%d%H%M%S")
        objs = [b("<< /Type /Catalog /Pages 2 0 R >>"),
                b("<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
                b("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] /Resources << /Font << /F1 4 0 R "
                  "/F2 5 0 R >> /ProcSet [/PDF /Text] >> /Contents 6 0 R >>" % (_n(w * k), _n(h * k))),
                b("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
                b("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"),
                b("<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data)) + data + b("\nendstream"),
                b("<< /Title (%s) /Creator (z7_draw_sld.py) /Producer (z7_draw_sld.py) /CreationDate (%s) >>"
                  % (self._pdfstr(self.title), stamp))]
        out = [b("%PDF-1.4\n") + bytes(bytearray([37, 226, 227, 207, 211, 10]))]     # binary marker line
        pos = len(out[0])
        offs = []
        for i, o in enumerate(objs):
            offs.append(pos)
            chunk = b("%d 0 obj\n" % (i + 1)) + o + b("\nendobj\n")
            out.append(chunk)
            pos += len(chunk)
        xref = pos
        tail = "xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        tail += "".join("%010d 00000 n \n" % o for o in offs)
        tail += "trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objs) + 1, len(objs), xref)
        out.append(b(tail))
        return b("").join(out)


# ============================================================================
# PATHS REPORT
# ============================================================================
def hop_text(S, child, parent):
    """The branch(es) between a node and the one above it, as one line."""
    es = child.circ
    if not es:
        return "?"
    e = es[0]
    br = e.br
    if br.kind == "LINE":
        t = "%s '%s'" % ("zero-impedance tie" if br.zero else "line", br.ckt)
        if br.x is not None:
            t += "   R+jX %.5f%sj%.5f pu" % (br.r or 0.0, "-" if br.x < 0 else "+", abs(br.x))
    elif br.kind == "2W":
        t = "2W '%s'   X %s pu   %s MVA   %s/%s kV" % (
            br.ckt, "%.4f" % br.x if br.x is not None else "?", _kv(br.sbase) if br.sbase else "?",
            _kv(S._kv_of_end(br, parent.key if parent.kind == "bus" else child.key)),
            _kv(S._kv_of_end(br, child.key)))
    else:
        t = "3W '%s' (%d / %d / %d) winding %d" % (br.ckt, br.a, br.b, br.c, e.wnd)
    if len(es) > 1:
        t += "   (+ parallel: %s)" % ", ".join("%s '%s'%s" % (x.br.kind if x.br.kind != "LINE" else "line", x.br.ckt,
                                                          "" if x.on else " " + x.state()) for x in es[1:])
    if not e.on:
        t += "   " + e.state().upper()
    return t


def paths_text(S, case_path, how_read):
    P = S.P
    L = []
    d = P.bus(P.poi)
    L.append("z7_draw_sld.py -- every unit traced to the POI")
    L.append("case     : %s" % _u(case_path or "(the case open in PSS/E)"))
    L.append("read     : %s" % how_read)
    L.append("POI      : %s -- %s" % (P.bname(P.poi), P.how))
    n_on = len([m for m in P.units if m["st"] > 0])
    L.append("units    : %d (%d in service), %.1f MW in service" % (len(P.units), n_on,
                                                                   sum(m["p"] for m in P.units if m["st"] > 0)))
    L.append("plant    : %d bus(es) drawn (%d energised, %d de-energised) besides the POI"
             % (len(P.live) + len(P.dead), len(P.live), len(P.dead)))
    L.append("tracing  : shortest path by number of branches over in-service lines, ties, two- and three-winding")
    L.append("           transformers (a three-winding one through its star point); ties broken by the smaller |X|,")
    L.append("           then the smaller bus number. Parts reached only through out-of-service branches follow.")
    L.append("")
    for m in P.units:
        n = S.N.get(m["bus"])
        if n is None:
            continue
        chain = []
        x = n
        while x is not None:
            chain.append(x)
            x = x.parent
        hops = len([c for c in chain[:-1] if not (c.kind == "bus" and c.parent is not None and c.parent.kind == "star")])
        d = P.bus(m["bus"])
        L.append("UNIT %d '%s'  %s %s kV   P %.1f MW  Q %.1f Mvar  Mbase %g MVA%s  -- %d branch(es) to the POI%s"
                 % (m["bus"], m["id"], d["name"], _kv(d["kv"]), m["p"], m["q"], m["mbase"],
                    "" if m["st"] > 0 else "  (OUT OF SERVICE)", hops,
                    "" if n.row == S.bottom else "  (not on the bottom row: its bus feeds other units)"))
        for i, c in enumerate(chain):
            if c.kind == "bus":
                L.append("    %-8d %-14s %8s kV%s" % (c.key, P.bus(c.key)["name"], _kv(P.bus(c.key)["kv"]),
                                                     "   (POI)" if c is S.root else ""))
            if i + 1 < len(chain):
                up = chain[i + 1]
                if c.kind == "star":
                    continue
                if up.kind == "star":
                    br = up.circ[0].br
                    L.append("        |  3W '%s' (%d / %d / %d): winding %d -> star point -> winding %d%s"
                             % (br.ckt, br.a, br.b, br.c, c.circ[0].wnd, up.circ[0].wnd,
                                ("   X12 %.4f X23 %.4f X31 %.4f pu" % br.x3) if None not in br.x3 else ""))
                else:
                    L.append("        |  %s" % hop_text(S, c, up))
        L.append("")
    L.append("PARALLEL CIRCUITS (drawn side by side)")
    par = [n for n in S.pre if n.kind == "bus" and len(n.circ) > 1]
    for n in par:
        L.append("    %d - %d: %s" % (n.key, n.parent.key, ", ".join(
            "%s '%s'%s" % (e.br.kind if e.br.kind != "LINE" else "line", e.br.ckt, "" if e.on else " (out of service)")
            for e in n.circ)))
    for n in S.pre:
        if n.kind == "star" and n.twin is not None:
            b, cn, e = n.twin
            par.append(n)
            L.append("    %s and %s: in parallel, both feeding %s" % (b.circ[0].br.label(), n.circ[0].br.label(),
                                                                    P.bname(cn.key)))
    if not par:
        L.append("    none")
    L.append("")
    L.append("NON-TREE CONNECTIONS (dashed, routed under the drawing)")
    for Lp in S.loops:
        e = Lp["e"]
        L.append("    %s  %s%s" % (Lp["tag"], e.br.label() if e.br.kind != "3W" else "%s winding %d" % (e.br.label(), e.wnd),
                                   "   in service: a loop" if e.on else "   OUT OF SERVICE"))
    if not S.loops:
        L.append("    none")
    L.append("")
    L.append("ON THE POI")
    for it in S.above:
        if it["kind"] == "stub":
            br, far = it["rec"]
            L.append("    grid stub: %s to %s%s" % (br.label(), ", ".join(P.bname(x) for x in far),
                                                   "" if any(br.wnd_on()) else "   (out of service)"))
        else:
            m = it["rec"]
            L.append("    %s '%s'  Mbase %g MVA  P %.1f MW  Q %.1f Mvar" % (
                "infinite bus" if it["kind"] == "ib" else "source", m["id"], m["mbase"], m["p"], m["q"]))
    if not S.above:
        L.append("    nothing")
    L.append("")
    L.append("LEFT OUT / NOTES")
    for t in P.left:
        L.append("    " + t)
    if not P.left:
        L.append("    nothing")
    return "\n".join(L) + "\n"


# ============================================================================
# THE PSS/E GUI FILE (.sld) -- the crash-safe sliderPy pass (v3)
# ============================================================================
def _lit(s):
    """A Python literal of the text `s` that is plain ASCII (Python 2 and 3)."""
    s = _u(s)
    try:
        s.encode("ascii")
        return repr(str(s))
    except UnicodeError:
        return "u'" + s.encode("unicode_escape").decode("ascii").replace("'", "\\'") + "'"


def write_gui_file(out_root, items, stamp):
    """DRAW_SLD_IN_PSSE_GUI.py in the run folder; its path."""
    rows = []
    for it in items:
        rows.append('    {"name": %s,\n     "case": %s,\n     "rev": %s,\n     "buses": [\n%s]},'
                    % (_lit(it["name"]), "None" if it["case"] is None else _lit(it["case"]),
                       "None" if not it.get("rev") else int(it["rev"]),
                       ",\n".join("        (%d, %.4f, %.4f)" % (b, x, y) for b, x, y in it["buses"])))
    text = (_GUI_TEMPLATE.replace("@STAMP@", stamp).replace("@ROOT@", _lit(out_root))
            .replace("@SCALE@", repr(float(SLD_SCALE))).replace("@CASES@", "\n".join(rows)))
    return _write_new(os.path.join(out_root, GUI_NAME), text)


def run_gui_file(path, log):
    """Inside the PSS/E GUI: run that file here -- the same safe pass."""
    with open(path, "rb") as fh:
        code = fh.read()
    ns = {"__name__": "__z7_draw_sld_gui__", "__file__": path}
    try:
        exec(compile(code, path, "exec"), ns)
        return True
    except Exception:
        log("*** the .sld pass stopped: " + _u(traceback.format_exc()))
        return False


_GUI_TEMPLATE = r'''# -*- coding: utf-8 -*-
"""DRAW_SLD_IN_PSSE_GUI.py -- PSS/E's own one-line diagram (.sld) of each case
drawn by z7_draw_sld.py (run @STAMP@).

PSS/E draws slider diagrams only in its GUI, so this file runs THERE:
    open PSS/E  ->  File  ->  Run Automation File...  ->  this file
For each case it opens the case, draws every bus of the plant (growbus), then
moves each bus to its place in the layout of <case>_sld.svg -- traced from
every generator up to the POI: the generators on the bottom row, each one's
path straight up to the POI on top -- and saves <case>.sld in this folder.
growbus alone places the buses the way PSS/E's own auto-draw does (the
zigzag); the move goes through PSS/E's sliderPy module.

SAFETY. Before any diagram object is touched, the help text PSS/E gives for
its diagram calls is written to slider_api.txt. Every sliderPy call is then
written to slider_trace_<case>.txt -- and flushed to disk -- BEFORE it is
made, so if PSS/E closes on one, the last line of that file names the call.
Send both files back if that happens; MOVE_BUSES = False draws without moving.

A case that already has its .sld here is left as it is; nothing is ever
overwritten. Each case is opened in the GUI, replacing the case open there:
save your own work first. The last diagram is left open on the screen.

SCALE spreads the drawing out (1.5) or packs it tighter (0.75); FLIP_Y = True
turns it over if the POI comes out at the bottom.
"""
from __future__ import print_function
import os
import re
import sys
import time

import psspy

SCALE = @SCALE@
FLIP_Y = False
MOVE_BUSES = True           # False = growbus only (PSS/E's own placement)
SKIP_EXISTING = True        # True = a case that already has <case>.sld here is not drawn again
LEAVE_LAST_OPEN = True      # True = the last diagram stays open on the screen
ROOT = @ROOT@
CASES = [
@CASES@
]

LINES = []
_BUS_MS = re.compile(r"^\s*BU[A-Z]*\s+(\d+)\s*$", re.I)


def say(msg):
    print(msg)
    LINES.append(msg)


def _ie(rc):
    return rc[0] if isinstance(rc, (list, tuple)) else rc


def _here():
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _free(path):
    """`path`, or the first <stem>_2, _3 ... that is free -- never overwrite."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    k = 2
    while os.path.exists("%s_%d%s" % (stem, k, ext)):
        k += 1
    return "%s_%d%s" % (stem, k, ext)


def _out_dir():
    return ROOT if os.path.isdir(ROOT) else _here()


def _native(p):
    """A path as this Python's psspy takes it (Python 2: bytes)."""
    if sys.version_info[0] == 2 and not isinstance(p, str):
        try:
            return p.encode(sys.getfilesystemencoding() or "mbcs")
        except Exception:
            return p
    return p


class Trace(object):
    """One line per step, on disk before the step is made."""

    def __init__(self, path):
        self.path = path
        self.fh = open(path, "w")

    def __call__(self, msg):
        try:
            self.fh.write(msg + "\n")
            self.fh.flush()
            os.fsync(self.fh.fileno())
        except Exception:
            pass

    def close(self):
        try:
            self.fh.close()
        except Exception:
            pass


def _doc(obj):
    d = getattr(obj, "__doc__", None)
    return (d or "").strip()


def api_dump():
    """The help text of psspy's diagram calls and of sliderPy (modules and
       classes only -- no diagram object is touched)."""
    p = _free(os.path.join(_out_dir(), "slider_api.txt"))
    out = []
    for n in sorted(n for n in dir(psspy) if re.search(r"diag|grow|slid|sld", n, re.I)):
        out.append("==== psspy.%s\n%s\n" % (n, _doc(getattr(psspy, n, None))))
    try:
        import sliderPy
        out.append("==== sliderPy module\n%s\n" % _doc(sliderPy))
        for n in sorted(x for x in dir(sliderPy) if not x.startswith("_")):
            obj = getattr(sliderPy, n, None)
            out.append("---- sliderPy.%s (%s)\n%s" % (n, type(obj).__name__, _doc(obj)))
            if isinstance(obj, type):
                for m in sorted(x for x in dir(obj) if not x.startswith("_")):
                    out.append("      .%s: %s" % (m, " ".join(_doc(getattr(obj, m, None)).split())[:300]))
            out.append("")
    except Exception as e:
        out.append("no sliderPy here (%s)" % e)
    try:
        with open(p, "w") as fh:
            fh.write("\n".join(out) + "\n")
        say("help text of the diagram calls: %s" % p)
    except Exception:
        pass


def _xy(p):
    """(x, y) from whatever GetPosition gives, or None."""
    if p is None:
        return None
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        try:
            return float(p[0]), float(p[1])
        except Exception:
            return None
    for a, b in (("x", "y"), ("X", "Y")):
        if hasattr(p, a) and hasattr(p, b):
            try:
                return float(getattr(p, a)), float(getattr(p, b))
            except Exception:
                pass
    return None


def _nn(pts):
    """Median distance from each point to its nearest neighbour (0 if fewer than 2)."""
    ds = []
    for i, (x, y) in enumerate(pts):
        d = [((x - u) ** 2 + (y - v) ** 2) ** 0.5 for j, (u, v) in enumerate(pts) if j != i]
        d = [v for v in d if v > 1e-9]
        if d:
            ds.append(min(d))
    ds.sort()
    return ds[len(ds) // 2] if ds else 0.0


def tidy(pkg, tr):
    """Every bus of the active diagram moved to its place in the layout.
       "" when done, else what stopped it."""
    tr("import sliderPy")
    try:
        import sliderPy
    except Exception as e:
        tr("  no sliderPy: %s" % e)
        return "no sliderPy in this PSS/E"
    tr("sliderPy.GetActiveDocument()")
    doc = sliderPy.GetActiveDocument()
    tr("  -> %s" % type(doc).__name__)
    tr("document.GetDiagram()")
    diag = doc.GetDiagram()
    tr("  -> %s" % type(diag).__name__)
    tr("diagram.GetComponents()")
    comps = list(diag.GetComponents() or [])
    tr("  -> %d component(s); classes: %s" % (len(comps), ", ".join(sorted(set(type(c).__name__ for c in comps)))))
    buses = {}
    for i, c in enumerate(comps):
        if not hasattr(type(c), "GetMapString") and not hasattr(c, "GetMapString"):
            continue
        tr("component %d (%s).GetMapString()" % (i, type(c).__name__))
        ms = c.GetMapString()
        tr("  -> %r" % (ms,))
        m = _BUS_MS.match(str(ms or ""))
        if m:
            buses.setdefault(int(m.group(1)), c)
    want = dict((b, (x, y)) for b, x, y in pkg["buses"])
    found = [b for b in want if b in buses]
    tr("buses found by map string: %d of %d" % (len(found), len(want)))
    if not found:
        return "no bus found on the diagram by its map string"
    now = {}
    for b in found:
        tr("bus %d .GetPosition()" % b)
        p = buses[b].GetPosition()
        tr("  -> %r" % (p,))
        if _xy(p):
            now[b] = _xy(p)
    poi = pkg["buses"][0][0]
    if poi not in now:
        return "the POI %d has no position on the diagram" % poi
    s_auto = _nn(list(now.values()))
    s_ours = _nn([want[b] for b in now])
    k = (s_auto / s_ours if s_auto > 0 and s_ours > 0 else 1.0) * SCALE
    tr("spacing: PSS/E's %.4g, the layout's %.4g -> scale %.4g" % (s_auto, s_ours, k))
    x0, y0 = now[poi]
    px, py = want[poi]
    sgn = -1.0 if FLIP_Y else 1.0
    goal = dict((b, (x0 + k * (want[b][0] - px), y0 + sgn * k * (want[b][1] - py))) for b in now)
    tol = 1e-3 * max(1.0, s_auto)
    if all(abs(now[b][0] - goal[b][0]) <= tol and abs(now[b][1] - goal[b][1]) <= tol for b in now):
        tr("every bus is already where the layout wants it")
        return ""
    sample = buses[found[0]]
    if not hasattr(type(sample), "SetPosition") and not hasattr(sample, "SetPosition"):
        tr("no SetPosition on %s -- buses cannot be moved from Python here" % type(sample).__name__)
        return "sliderPy offers no SetPosition (see slider_api.txt)"
    moved, left = 0, []
    for b in [x for x in found if x in goal]:
        tx, ty = goal[b]
        tr("bus %d .SetPosition(%.4f, %.4f)" % (b, tx, ty))
        try:
            buses[b].SetPosition(tx, ty)
        except TypeError as e:
            tr("  TypeError: %s -- stopping (the signature is not (x, y))" % e)
            return "SetPosition takes other arguments (see the trace)"
        tr("bus %d .GetPosition() to check" % b)
        got = _xy(buses[b].GetPosition())
        tr("  -> %r" % (got,))
        if got and abs(got[0] - tx) <= tol and abs(got[1] - ty) <= tol:
            moved += 1
        else:
            left.append(b)
            if moved == 0 and len(left) >= 2:
                tr("SetPosition did not move the first buses -- stopping")
                break
    if moved and hasattr(psspy, "refreshdiagfile"):
        tr("psspy.refreshdiagfile()")
        psspy.refreshdiagfile()
    tr("moved %d of %d" % (moved, len(found)))
    return "" if moved == len(found) else ("%d of %d moved" % (moved, len(found)) if moved
                                           else "no move took (see the trace)")


def _psse_major():
    try:
        v = psspy.psseversion()
        for x in (v[1:] if isinstance(v, (list, tuple)) else ()):
            try:
                return int(x)
            except Exception:
                continue
    except Exception:
        pass
    return None


def _open(pkg, tr):
    """Open the case of `pkg`: "" when it is open, else why not."""
    path = pkg["case"]
    if not path:
        tr("the case open in PSS/E is drawn -- not opened again")
        return ""
    if not os.path.isfile(path):
        return "no such file: %s" % path
    if path.lower().endswith(".sav"):
        tr("psspy.case(%s)" % os.path.basename(path))
        ie = _ie(psspy.case(_native(path)))
        return "" if ie in (0, None) else "psspy.case ierr %s" % ie
    rev, cur = pkg.get("rev"), _psse_major()
    calls = []
    if rev and cur and rev != cur and hasattr(psspy, "readrawversion"):
        calls += [("readrawversion", (0, str(rev), _native(path))), ("readrawversion", (0, rev, _native(path)))]
    if hasattr(psspy, "read"):
        calls.append(("read", (0, _native(path))))
    if hasattr(psspy, "readrawx"):
        calls.append(("readrawx", (0, _native(path))))
    why = "this PSS/E has no read / readrawversion / readrawx"
    for k, (name, args) in enumerate(calls):
        if k and name != calls[0][0] and not why.startswith("TypeError"):
            break
        tr("psspy.%s(%r, %r, ...)" % (name, args[0], args[1]) if len(args) == 3 else "psspy.%s(0, ...)" % name)
        try:
            ie = _ie(getattr(psspy, name)(*args))
        except TypeError as e:
            why = "TypeError %s" % e
            continue
        if ie in (0, None):
            return ""
        why = "%s ierr %s" % (name, ie)
    return why


def draw(pkg, last):
    name = pkg["name"]
    sld = os.path.join(_out_dir(), name + ".sld")
    if SKIP_EXISTING and os.path.isfile(sld):
        say("%-28s %s.sld is already there -- left as it is" % (name, name))
        return True
    tr = Trace(_free(os.path.join(_out_dir(), "slider_trace_%s.txt" % name)))
    tr("%s -- %s" % (name, time.strftime("%Y-%m-%d %H:%M:%S")))
    try:
        why = _open(pkg, tr)
        if why:
            say("%-28s *** could not open the case: %s" % (name, why))
            return False
        tr("psspy.newdiagfile()")
        ie = _ie(psspy.newdiagfile())
        if ie not in (0, None):
            say("%-28s *** newdiagfile ierr %s" % (name, ie))
            return False
        bad = []
        for b, x, y in pkg["buses"]:
            tr("psspy.growbus(%d, %.4f, %.4f)" % (b, x * SCALE, y * SCALE))
            try:
                ie = _ie(psspy.growbus(b, x * SCALE, y * SCALE * (-1.0 if FLIP_Y else 1.0)))
            except Exception as e:
                ie = e
            if ie not in (0, None):
                bad.append("%d (%s)" % (b, ie))
        if MOVE_BUSES:
            try:
                note = tidy(pkg, tr)
            except Exception as e:
                note = "%s: %s" % (type(e).__name__, e)
                tr("  %s" % note)
        else:
            note = "MOVE_BUSES = False"
        out = _free(sld)
        tr("psspy.savediagfile(%s)" % os.path.basename(out))
        try:
            ie = _ie(psspy.savediagfile(_native(out)))
        except Exception as e:
            ie = e
        ok = os.path.isfile(out) and os.path.getsize(out) > 0
        if ok:
            say("%-28s written %s -- %d of %d buses drawn%s"
                % (name, out, len(pkg["buses"]) - len(bad), len(pkg["buses"]),
                   "; tidied" if not note else "; NOT tidied: " + note))
        else:
            say("%-28s *** %s not written (savediagfile gave %s)" % (name, os.path.basename(out), ie))
        if bad:
            say("    %d bus(es) not drawn: %s" % (len(bad), ", ".join(bad[:10])))
        if not (last and LEAVE_LAST_OPEN and ok):
            tr("psspy.closediagfile()")
            try:
                psspy.closediagfile()
            except Exception:
                pass
        tr("done")
        return ok and not bad
    finally:
        tr.close()


def main():
    say("One-line diagrams (.sld) of the cases drawn by z7_draw_sld.py -- %s" % time.strftime("%Y-%m-%d %H:%M"))
    api_dump()
    n = 0
    for k, pkg in enumerate(CASES):
        n += 1 if draw(pkg, k == len(CASES) - 1) else 0
    say("%d of %d diagram(s) in place" % (n, len(CASES)))
    try:
        p = _free(os.path.join(_out_dir(), "DRAW_SLD_IN_PSSE_GUI_log.txt"))
        with open(p, "w") as fh:
            fh.write("\n".join(LINES) + "\n")
    except Exception:
        pass


main()
'''


# ============================================================================
# ONE CASE
# ============================================================================
def one_case(path, poi_arg, out_root, stamp, log, gui_items):
    """Read, trace, lay out and draw one case. True when its drawing is written."""
    stem = os.path.splitext(os.path.basename(path))[0] if path else ""
    current = path is None                          # the case open in the PSS/E GUI: drawn as it is there
    log("")
    log("=" * 78)
    if path:
        log("%s   (%s)" % (_u(stem), _u(path)))
        if not open_case(path, log):
            return False
        how_read = log.lines[-1].split(":", 1)[-1].strip()
    else:
        cur = current_case_file()
        stem = os.path.splitext(os.path.basename(cur))[0] if cur else "case_in_PSSE"
        log("%s   (the case open in PSS/E%s)" % (_u(stem), (": " + _u(cur)) if cur else ", not saved under a name"))
        how_read = "the case open in the PSS/E GUI"
        path = cur or None
    net = read_network(log)
    nb = net["branches"]
    log("network        : %d buses, %d machines, %d branches (%d lines, %d two-winding, %d three-winding), "
        "%d loads, %d fixed and %d switched shunts"
        % (len(net["buses"]), len(net["machines"]), len(nb), len([b for b in nb if b.kind == "LINE"]),
           len([b for b in nb if b.kind == "2W"]), len([b for b in nb if b.kind == "3W"]), len(net["loads"]),
           len(net["fxsh"]), len(net["swsh"])))
    if not net["buses"]:
        log("*** no bus could be read from this case -- nothing to draw")
        return False
    poi, how = find_poi(stem, net, poi_arg)
    if poi is None:
        log("*** " + how)
        return False
    P = find_plant(net, poi, how)
    log("POI            : %s -- %s" % (P.bname(poi), how))
    if not P.units:
        log("*** no generating unit behind the POI %d%s -- nothing to draw"
            % (poi, " (GEN_BUSES %s)" % GEN_BUSES if GEN_BUSES else ""))
        for t in P.left[:6]:
            log("    " + t)
        return False
    solved, state = solution_state()
    n_on = len([m for m in P.units if m["st"] > 0])
    log("plant          : %d bus(es) behind the POI (%d de-energised), %d unit(s) (%d in service), %.1f MW"
        % (len(P.live) + len(P.dead), len(P.dead), len(P.units), n_on,
           sum(m["p"] for m in P.units if m["st"] > 0)))
    if P.stubs:
        log("grid at the POI: %d connection(s), drawn as stubs" % len(P.stubs))
    S = Sld(P, _u(stem), path, state, sysmva(), time.strftime("%Y-%m-%d %H:%M"))
    S.size()
    svg = SvgCanvas(S.W, S.H, "%s -- one-line diagram" % _u(stem))
    S.draw(svg)
    bad = svg.overlaps()
    off_row = [m for m in P.units if m["bus"] in S.N and S.N[m["bus"]].row != S.bottom]
    log("drawing        : %d row(s), %d unit(s) on the bottom row%s, %d dashed connection(s), %d parallel "
        "circuit group(s); %d labels, %s"
        % (S.bottom + 1, len(P.units) - len(off_row), (" (%d not: their bus feeds other units)" % len(off_row))
           if off_row else "", len(S.loops), len([n for n in S.pre if n.kind == "bus" and len(n.circ) > 1]),
           len(svg.texts), "none overlapping" if not bad else "REVIEW: %d overlap(s)" % len(bad)))
    for what, a, b in bad[:8]:
        log("    REVIEW: %s: %r %s" % (what, a, b))
    name, k = stem, 2                               # one name for all of this case's files (two cases of one name:
    while [x for x in ("_sld.svg", "_sld.pdf", "_paths.txt", ".sld")   # the second gets <name>_2)
           if os.path.exists(os.path.join(out_root, name + x))]:
        name, k = "%s_%d" % (stem, k), k + 1
    base = os.path.join(out_root, name)
    p_svg = _write_new(_free(base + "_sld.svg"), svg.render())
    done = [p_svg]
    if WRITE_PDF:
        pdf = PdfCanvas(S.W, S.H, "%s -- one-line diagram" % _u(stem))
        S.draw(pdf)
        done.append(_write_new(_free(base + "_sld.pdf"), pdf.render()))
    done.append(_write_new(_free(base + "_paths.txt"), paths_text(S, path, how_read)))
    if P.left:
        log("not drawn/notes: %d -- listed in %s" % (len(P.left), os.path.basename(done[-1])))
        for t in P.left[:4]:
            log("    " + t)
    for p in done:
        log("written        : %s" % _u(p))
    gui_items.append({"name": _u(os.path.basename(base)), "case": None if current else path,
                      "rev": raw_rev(path) if path and not current and not path.lower().endswith(".sav") else None,
                      "buses": S.sld_buses()})
    return True


# ============================================================================
def _args(argv):
    """(paths, --poi, --out, problems)."""
    paths, poi, out, bad = [], None, None, []
    k = 0
    while k < len(argv):
        a = argv[k]
        if a in ("-h", "--help", "/?"):
            bad.append("help")
        elif a.startswith("--poi"):
            v = a.split("=", 1)[1] if "=" in a else (argv[k + 1] if k + 1 < len(argv) else "")
            k += 0 if "=" in a else 1
            try:
                poi = int(v)
            except Exception:
                bad.append("--poi wants a bus number, not %r" % v)
        elif a.startswith("--out"):
            v = a.split("=", 1)[1] if "=" in a else (argv[k + 1] if k + 1 < len(argv) else "")
            k += 0 if "=" in a else 1
            out = v
        elif a.startswith("--"):
            bad.append("unknown option %s" % a)
        elif a.strip():
            paths.append(a)
        k += 1
    return paths, poi, out, bad


def expand(items):
    """Case files from files and folders: a folder gives every .sav in it, and
       each .raw that has no .sav of the same name. (files, problems)."""
    files, bad, seen = [], [], set()
    for it in items:
        p = os.path.abspath(os.path.expanduser(it))
        if os.path.isdir(p):
            names = sorted(os.listdir(p))
            savs = [x for x in names if x.lower().endswith(".sav")]
            stems = set(os.path.splitext(x)[0].lower() for x in savs)
            raws = [x for x in names if x.lower().endswith(".raw") and os.path.splitext(x)[0].lower() not in stems]
            got = sorted(savs + raws, key=lambda x: x.lower())
            if not got:
                bad.append("no .sav or .raw in %s" % p)
            for x in got:
                full = os.path.join(p, x)
                if os.path.normcase(full) not in seen:
                    seen.add(os.path.normcase(full))
                    files.append(full)
        elif os.path.isfile(p):
            if os.path.splitext(p)[1].lower() not in (".sav", ".raw"):
                bad.append("not a .sav or .raw: %s" % p)
            elif os.path.normcase(p) not in seen:
                seen.add(os.path.normcase(p))
                files.append(p)
        else:
            bad.append("not found: %s" % it)
    return files, bad


USAGE = """usage: python z7_draw_sld.py <case.sav | case.raw | folder> ... [--poi BUS] [--out FOLDER]
  a folder means every .sav in it, and each .raw without a .sav of the same name;
  --poi names the POI (needed for a full-system case); CASES / POI_BUS / POI_BY_CASE
  at the top of the script do the same. In the PSS/E GUI: File > Run Automation File."""


def main(argv=None):
    global OUT_DIR
    if argv is None:
        argv = list(getattr(sys, "argv", None) or [""])[1:]
    paths, poi_arg, out_arg, bad = _args(argv)
    if bad:
        _say(USAGE if "help" in bad else "%s\n\n%s" % ("\n".join(b for b in bad if b != "help"), USAGE))
        return 0 if bad == ["help"] else 2
    if out_arg:
        OUT_DIR = out_arg
    files, missing = expand(list(CASES) + paths)
    log = Log()
    for m in missing:
        log("*** %s" % m)
    if not files and not _hosted():
        _say(USAGE)
        return 2
    t0 = time.time()
    pre = Log(quiet=True)
    try:
        psse_start(pre)
    except ImportError as e:
        _say("*** psspy could not be imported (%s): run this script with the PSS/E Python, or set PSSE_ROOT "
             "to the PSS/E folder" % _err(e))
        return 2
    if not files and not GUI:
        _say(USAGE)
        return 2
    if files:
        first_dir = os.path.dirname(files[0])
    else:
        cur = current_case_file()
        first_dir = os.path.dirname(os.path.abspath(cur)) if cur else os.getcwd()
    try:
        out_root = _mkdir_new(OUT_DIR or first_dir, "SLD_" + time.strftime("%Y%m%d_%H%M%S"))
    except Exception as e:
        _say("*** cannot make the output folder in %s: %s" % (OUT_DIR or first_dir, _err(e)))
        return 2
    stamp = os.path.basename(out_root)[4:]
    log("z7_draw_sld.py -- one-line diagrams from PSS/E cases -- %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("Python         : %d.%d.%d" % tuple(sys.version_info[:3]))
    for ln in pre.lines:
        log(ln)
    log("output         : %s" % _u(out_root))
    items, n_ok = [], 0
    todo = files or [None]
    try:
        msgs = None
        if not GUI:                                 # PSS/E's progress messages into the run folder (alerts stay)
            try:
                msgs = _free(os.path.join(out_root, "psse_progress.txt"))
                psspy.progress_output(2, msgs, [0, 0])
            except Exception:
                msgs = None
        try:
            for path in todo:
                try:
                    n_ok += 1 if one_case(path, poi_arg, out_root, stamp, log, items) else 0
                except Exception:
                    log("*** stopped on this case: " + _u(traceback.format_exc()))
        finally:
            if msgs:
                try:
                    psspy.progress_output(1, "", [0, 0])
                except Exception:
                    pass
        log("")
        log("=" * 78)
        log("%d of %d case(s) drawn in %.1f s -- %s" % (n_ok, len(todo), time.time() - t0, _u(out_root)))
        if items and GUI:
            gp = write_gui_file(out_root, items, stamp)
            log(".sld           : drawn here, in the PSS/E GUI (growbus, then sliderPy -- every call traced to disk "
                "first): %s" % _u(gp))
            run_gui_file(gp, log)
            got = sorted(x for x in os.listdir(out_root) if x.lower().endswith(".sld"))
            log(".sld written   : %s" % (", ".join(got) if got else "none -- see DRAW_SLD_IN_PSSE_GUI_log.txt"))
        elif items and GUI_FILE:
            gp = write_gui_file(out_root, items, stamp)
            log(".sld           : not drawn here -- PSS/E draws slider diagrams only in its GUI (run from a command "
                "prompt, newdiagfile / growbus / savediagfile do nothing). Open PSS/E, then")
            log("                 File > Run Automation File > %s" % _u(gp))
    except Exception:
        log("*** stopped: " + _u(traceback.format_exc()))
        n_ok = -1
    finally:
        try:
            _write_new(_free(os.path.join(out_root, LOG_NAME)), "\n".join(log.lines) + "\n")
        except Exception as e:
            _say("*** log not written: %s" % _err(e))
    return 0 if n_ok == len(todo) else 1


# Run from a command prompt -- or inside PSS/E's GUI, whose Run Automation File may not
# name the script "__main__" (imported by another Python script, it waits for main()).
if __name__ == "__main__" or _in_psse_exe():
    _rc = main()
    if not GUI:
        sys.exit(_rc)
