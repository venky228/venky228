# -*- coding: utf-8 -*-
"""One stand-alone model per plant: the whole plant behind its POI, on an
infinite bus -- <name>.sav, <name>.raw and <name>.dyr -- for the client.

   WHAT EACH MODEL HOLDS
     every bus behind the Point of Interconnection: the existing units (EGF) and
     the surplus units (SGF) with their GSUs, the collector system, the main
     power transformers (MPT) and the gen-tie, exactly as the study's project
     case has them (the NEW_PLANT build of each project), plus
     the POI bus itself as the INFINITE BUS: swing bus, one machine 'IB' with
     MBASE 100000 MVA, and GENCLS H = 0 in the .dyr (H = 0 is PSS/E's infinite
     inertia), held at the POI voltage and angle of the full-case solution.
   Everything else in the case -- the grid -- is left out.

   WHERE IT COMES FROM (per project, from the panel of z7_main_f.py)
     power flow  {PROJ_FOLDER}\\<case>_BESS_<project>_<MW>MW_f_NEWPLANT.sav
                 (the project case the study built: EGF + SGF in service, the
                 Scenario 1 dispatch); the full case is written to RAW with
                 rawd_2 and the plant is cut out of that text
     dynamics    the same build's snapshot (.cnv + .snp) -- the models exactly
                 as the study ran them, deck changes included -- written out
                 with dyda; the records of the plant's buses are kept.
                 Fallback: the combined .dyr the build wrote
                 (*_with_BESS_<project>.dyr).

   CHECKS (VALIDATE = True)
     the cut-out case is solved and every plant unit's P / Q is compared with
     the full case; then it is converted, the .dyr loaded (with the study's
     add_library.idv for the user-model DLLs), initialised and run flat for
     FLAT_RUN_S seconds. The result of each step is in <name>_CHECK.txt.

   OUTPUT -- nothing existing is overwritten
     {root}\\PLANT_MODELS\\<date_time>\\<name>.sav / .raw / .dyr / _CHECK.txt

   Run with the PSS/E Python (3.4), from the folder z7_main_f.py is in:
       python z7_plant_models.py
"""
import os
import sys
import re
import time
import glob
import shutil
import collections

# ============================================================================
# SETTINGS
# ============================================================================
# poi = the bus that becomes the infinite bus; egf = the existing plant's unit
# buses (they find the plant side of the POI -- the SGF units, GSUs, collector
# and MPTs behind it come with it automatically)
PLANTS = collections.OrderedDict([
    ("SantaFe",       {"name": "Santa_Fe_BESS",       "poi": 765911, "egf": [765912, 765922, 765932, 765935]}),
    ("IronStar",      {"name": "Iron_Star_BESS",      "poi": 560080, "egf": [587313, 587317]}),
    ("EmpirePrairie", {"name": "Empire_Prairie_BESS", "poi": 761383, "egf": [761379, 761382, 761400, 761403]}),
    ("EastFork",      {"name": "East_Fork_BESS",      "poi": 531429, "egf": [531620, 531607]}),
])
PROJECTS = []                     # [] = every project above
CASE_BY_PROJECT = {}              # {"EastFork": r"C:\...\x.sav"} -- else found in PROJ_FOLDER
SNP_BY_PROJECT = {}               # {"EastFork": (r"...\x.cnv", r"...\x.snp")} -- else beside the case
DYR_BY_PROJECT = {}               # {"EastFork": r"...\x.dyr"} -- used when there is no snapshot
OUT_DIR = r"{root}\PLANT_MODELS"  # a <date_time> folder is made inside it
MAX_PLANT_BUSES = 400             # more buses than this behind the POI = it is not a plant: stop
IB_MBASE = 100000.0               # infinite-bus machine base (MVA)
IB_ID = "IB"
VALIDATE = True                   # solve, initialise and run flat in PSS/E
FLAT_RUN_S = 5.0
ADDLIB_IDV = "add_library.idv"    # in PROJ_FOLDER: loads the user-model DLLs, as the study does
DLLS = []                         # extra user-model DLLs to load (full paths), if any

HERE = os.path.dirname(os.path.abspath(__file__))


# ============================================================================
# SETTINGS READ FROM z7_main_f.py (text only -- nothing there is run)
# ============================================================================
def _main_setting(name, default):
    import ast
    for fn in ("z7_main_f.py", "z7_main.py"):
        p = os.path.join(HERE, fn)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                for ln in fh:
                    m = re.match(r"^%s\s*=\s*(.+)$" % re.escape(name), ln)
                    if not m:
                        continue
                    rhs = m.group(1)
                    for cut in range(len(rhs), 0, -1):
                        try:
                            return ast.literal_eval(rhs[:cut].strip())
                        except Exception:
                            continue
        except Exception:
            pass
    return default


ROOT = _main_setting("ROOT", "") or HERE


def _abs(p):
    p = str(p or "").replace("{root}", ROOT)
    if p and not (os.path.isabs(p) or p[:2] in ("\\\\", "//") or re.match(r"^[A-Za-z]:[\\/]", p)):
        p = os.path.join(ROOT, p)
    return p


PROJ_DIR = _abs(_main_setting("PROJ_FOLDER", "Projects"))


# ============================================================================
# RAW (v33 / v34 / v35) -- read, cut, write. Pure Python.
# ============================================================================
def split_fields(line):
    """Fields of one RAW / DYR line: commas or blanks outside quotes separate,
       text after '/' outside quotes is a comment. Quoted fields keep quotes."""
    out, cur, q, i = [], "", None, 0
    s = line.rstrip("\r\n")
    while i < len(s):
        c = s[i]
        if q:
            cur += c
            if c == q:
                q = None
        elif c in ("'", '"'):
            q = c
            cur += c
        elif c == "/":
            break
        elif c == "," or c.isspace():
            if c == ",":
                out.append(cur.strip())
                cur = ""
            elif cur.strip() and (i + 1 < len(s) and not s[i + 1].isspace() and s[i + 1] != ","):
                # blank-separated values (hand-written RAW) -- comma-separated is the norm
                rest = s[i + 1:].lstrip()
                if rest and rest[0] not in ",/":
                    out.append(cur.strip())
                    cur = ""
        else:
            cur += c
        i += 1
    if cur.strip() or (s.rstrip().endswith(",")):
        out.append(cur.strip())
    return out


def _int(x, default=0):
    try:
        return int(float(str(x).strip().strip("'\"")))
    except Exception:
        return default


SECTION_ORDER_34 = ["BUS", "LOAD", "FIXED SHUNT", "GENERATOR", "BRANCH", "TRANSFORMER", "AREA",
                    "TWO-TERMINAL DC", "VOLTAGE SOURCE CONVERTER", "IMPEDANCE CORRECTION",
                    "MULTI-TERMINAL DC", "MULTI-SECTION LINE", "ZONE", "INTER-AREA TRANSFER", "OWNER",
                    "FACTS DEVICE", "SWITCHED SHUNT", "GNE DEVICE", "INDUCTION MACHINE"]


def _is_end(line):
    t = line.strip()
    return t == "0" or t.startswith("0 ") or t.startswith("0/") or t.startswith("0,") and "END" in t.upper()


def read_raw(text):
    """{'head': [3 lines], 'rev': int, 'sections': OrderedDict(name -> [record lines]),
        'ends': {name: end line}, 'tail': [lines after the last section]}"""
    lines = text.splitlines()
    head = lines[:3]
    try:
        rev = _int(split_fields(lines[0])[2], 34)
    except Exception:
        rev = 34
    order = list(SECTION_ORDER_34)
    if rev >= 35:
        order = order[:5] + ["SYSTEM SWITCHING DEVICE"] + order[5:] + ["SUBSTATION"]
    secs, ends = collections.OrderedDict(), {}
    k, cur, i = 0, [], 3
    while i < len(lines):
        ln = lines[i]
        if ln.strip().upper().startswith("Q") and not ln.strip()[1:2].isalnum():
            break
        if _is_end(ln):
            name = order[k] if k < len(order) else "EXTRA%d" % k
            m = re.search(r"END OF (.+?) DATA", ln.upper())
            if m:
                name = m.group(1).strip()
            secs[name] = cur
            ends[name] = ln
            cur, k = [], k + 1
        else:
            cur.append(ln)
        i += 1
    return {"head": head, "rev": rev, "sections": secs, "ends": ends, "order": order,
            "tail": lines[i:] if i < len(lines) else ["Q"]}


def xfmr_records(lines):
    """Group transformer lines: 4 lines for two-winding (K = 0), 5 for three-winding."""
    out, i = [], 0
    while i < len(lines):
        f = split_fields(lines[i])
        k = _int(f[2]) if len(f) > 2 else 0
        n = 5 if k != 0 else 4
        out.append(lines[i:i + n])
        i += n
    return out


def net_from_raw(raw):
    """buses {n: fields}, adjacency {n: set}, machines {bus: [ids]}, branch / xfmr ends."""
    S = raw["sections"]
    buses = {}
    for ln in S.get("BUS", []):
        f = split_fields(ln)
        if f:
            buses[_int(f[0])] = f
    adj = collections.defaultdict(set)

    def link(a, b):
        if a and b and a != b:
            adj[a].add(b)
            adj[b].add(a)
    for ln in S.get("BRANCH", []):
        f = split_fields(ln)
        if len(f) > 13 and _int(f[13], 1) == 0:
            continue                                   # out of service
        link(abs(_int(f[0])), abs(_int(f[1])))
    for rec in xfmr_records(S.get("TRANSFORMER", [])):
        f = split_fields(rec[0])
        st = _int(f[11], 1) if len(f) > 11 else 1
        i, j, k = abs(_int(f[0])), abs(_int(f[1])), abs(_int(f[2]))
        if st == 0:
            continue
        if k:
            # three-winding status: 1 all in, 2 winding 2 out, 3 winding 3 out, 4 winding 1 out
            if st in (1, 2):
                link(i, k)
            if st in (1, 3):
                link(i, j)
            if st in (1, 4):
                link(j, k)
            if st == 1:
                pass
        else:
            link(i, j)
    mach = collections.defaultdict(list)
    for ln in S.get("GENERATOR", []):
        f = split_fields(ln)
        if f:
            mach[_int(f[0])].append(f[1].strip().strip("'\"").strip())
    return buses, adj, mach


def plant_side(adj, poi, egf, cap):
    """Buses behind `poi`: the union of the regions reached from POI neighbours
       without crossing the POI that hold at least one EGF unit bus."""
    keep = set()
    for n in sorted(adj.get(poi, ())):
        seen, todo = set([n]), [n]
        big = False
        while todo:
            u = todo.pop()
            for w in adj.get(u, ()):
                if w == poi or w in seen:
                    continue
                seen.add(w)
                todo.append(w)
            if len(seen) > cap:
                big = True
                break
        if not big and seen & set(egf):
            keep |= seen
    return keep


def cut_raw(raw, keep, poi, ib_p, ib_q, ib_v, ib_a):
    """RAW text of only `keep` + the POI as the infinite bus (lines kept verbatim
       except the fields that must change). Returns (text, notes)."""
    S, notes = raw["sections"], []
    allb = set(keep) | set([poi])
    out = list(raw["head"])
    out[1] = "PLANT MODEL ON AN INFINITE BUS AT %d" % poi if len(out) > 1 else out
    areas, zones, owners = set(), set(), set()

    def put(name, recs):
        for r in recs:
            out.extend(r if isinstance(r, list) else [r])
        out.append(raw["ends"].get(name, "0 / END OF %s DATA" % name))

    # BUS -- the POI becomes type 3 at the full-case voltage and angle
    recs = []
    for ln in S.get("BUS", []):
        f = split_fields(ln)
        n = _int(f[0])
        if n not in allb:
            continue
        areas.add(_int(f[4]))
        zones.add(_int(f[5]))
        owners.add(_int(f[6]))
        if n == poi:
            f[3] = "3"
            f[7] = "%.5f" % ib_v
            f[8] = "%.4f" % ib_a
            ln = ",".join(f)
        recs.append(ln)
    put("BUS", recs)
    # LOAD / FIXED SHUNT -- plant buses only (whatever sits at the POI belongs to the grid)
    for name in ("LOAD", "FIXED SHUNT"):
        recs = []
        for ln in S.get(name, []):
            f = split_fields(ln)
            n = _int(f[0])
            if n in keep:
                recs.append(ln)
                if name == "LOAD" and len(f) > 11:
                    owners.add(_int(f[11]))
            elif n == poi:
                notes.append("%s at the POI left out (grid side): %s" % (name.lower(), ln.strip()[:60]))
        put(name, recs)
    # GENERATOR -- plant units (remote regulation outside the plant -> local), plus the infinite source
    recs = []
    for ln in S.get("GENERATOR", []):
        f = split_fields(ln)
        n = _int(f[0])
        if n in keep:
            if len(f) > 7 and _int(f[7]) not in (0, n) and _int(f[7]) not in allb:
                notes.append("unit %s %s regulated bus %s is outside the plant -> regulates its own terminal"
                             % (n, f[1], f[7]))
                f[7] = "0"
                ln = ",".join(f)
            recs.append(ln)
        elif n == poi:
            notes.append("machine at the POI left out (grid side): %s" % ln.strip()[:60])
    recs.append("%d,'%s',%.3f,%.3f,%.1f,%.1f,%.5f,0,%.1f,0.0,0.0001,0.0,0.0,1.0,1,100.0,%.1f,%.1f,1,1.0"
                % (poi, IB_ID, ib_p, ib_q, 99999.0, -99999.0, ib_v, IB_MBASE, 99999.0, -99999.0))
    put("GENERATOR", recs)
    # BRANCH -- both ends kept
    recs = []
    for ln in S.get("BRANCH", []):
        f = split_fields(ln)
        if abs(_int(f[0])) in allb and abs(_int(f[1])) in allb:
            recs.append(ln)
    put("BRANCH", recs)
    if "SYSTEM SWITCHING DEVICE" in S:
        recs = []
        for ln in S.get("SYSTEM SWITCHING DEVICE", []):
            f = split_fields(ln)
            if abs(_int(f[0])) in allb and abs(_int(f[1])) in allb:
                recs.append(ln)
        put("SYSTEM SWITCHING DEVICE", recs)
    # TRANSFORMER -- all windings kept; a tap controlling a bus outside the plant -> fixed tap
    recs = []
    for rec in xfmr_records(S.get("TRANSFORMER", [])):
        f = split_fields(rec[0])
        ends = [abs(_int(f[0])), abs(_int(f[1]))] + ([abs(_int(f[2]))] if _int(f[2]) else [])
        if not all(e in allb for e in ends):
            continue
        rec = list(rec)
        for wl in range(2, len(rec)):
            w = split_fields(rec[wl])
            if len(w) > 7 and abs(_int(w[7])) not in (0,) and abs(_int(w[7])) not in allb:
                notes.append("transformer %s winding %d controlled bus %s is outside the plant -> fixed tap"
                             % ("-".join(str(e) for e in ends), wl - 1, w[7]))
                w[6] = "0"
                w[7] = "0"
                rec[wl] = ",".join(w)
        recs.append(rec)
    put("TRANSFORMER", recs)
    # AREA -- the areas of the kept buses; the POI's area swings on the POI
    poi_area = None
    for ln in S.get("BUS", []):
        f = split_fields(ln)
        if _int(f[0]) == poi:
            poi_area = _int(f[4])
    recs = []
    for ln in S.get("AREA", []):
        f = split_fields(ln)
        a = _int(f[0])
        if a in areas:
            f[1] = str(poi) if a == poi_area else "0"
            if len(f) > 2:
                f[2] = "0.0"
            recs.append(",".join(f))
    put("AREA", recs)
    for name in ("TWO-TERMINAL DC", "VOLTAGE SOURCE CONVERTER"):
        if S.get(name):
            notes.append("%s data left out (none inside a plant)" % name.lower())
        put(name, [])
    put("IMPEDANCE CORRECTION", S.get("IMPEDANCE CORRECTION", []))      # tables are referenced by number
    put("MULTI-TERMINAL DC", [])
    recs = []
    for ln in S.get("MULTI-SECTION LINE", []):
        f = split_fields(ln)
        bs = [abs(_int(x)) for x in f[:2] + f[3:] if str(x).strip().lstrip("-").isdigit()]
        if bs and all(b in allb for b in bs):
            recs.append(ln)
    put("MULTI-SECTION LINE", recs)
    put("ZONE", [ln for ln in S.get("ZONE", []) if _int(split_fields(ln)[0]) in zones])
    put("INTER-AREA TRANSFER", [])
    put("OWNER", [ln for ln in S.get("OWNER", []) if _int(split_fields(ln)[0]) in owners])
    if S.get("FACTS DEVICE"):
        notes.append("FACTS data left out")
    put("FACTS DEVICE", [])
    recs = []
    for ln in S.get("SWITCHED SHUNT", []):
        f = split_fields(ln)
        n = _int(f[0])
        if n in keep:
            if len(f) > 6 and _int(f[6]) not in (0, n) and _int(f[6]) not in allb:
                notes.append("switched shunt %s regulated bus %s is outside the plant -> local" % (n, f[6]))
                f[6] = "0"
                ln = ",".join(f)
            recs.append(ln)
        elif n == poi:
            notes.append("switched shunt at the POI left out (grid side)")
    put("SWITCHED SHUNT", recs)
    put("GNE DEVICE", [])
    put("INDUCTION MACHINE", [ln for ln in S.get("INDUCTION MACHINE", []) if _int(split_fields(ln)[0]) in keep])
    if "SUBSTATION" in S:
        put("SUBSTATION", [])
    out.append("Q")
    return "\n".join(out) + "\n", notes


# ============================================================================
# DYR -- records of the plant's buses, plus the infinite bus. Pure Python.
# ============================================================================
def dyr_records(text):
    """[(first bus, record text)] -- a record runs to the '/' outside quotes."""
    recs, cur, q = [], "", None
    for ch in text:
        cur += ch
        if q:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        elif ch == "/":
            body = cur
            cur = ""
            # the rest of that line is comment: keep it with the record
            recs.append(body)
    if cur.strip():
        recs.append(cur)
    out = []
    pending = ""
    for r in recs:
        r = pending + r
        pending = ""
        t = r.strip()
        if not t:
            continue
        # comments between records (lines starting with @ or !) are dropped
        lines = [ln for ln in t.splitlines() if not ln.strip().startswith(("@", "!", "//"))]
        t = "\n".join(lines).strip()
        if not t:
            continue
        f = split_fields(t.split("\n")[0])
        out.append((_int(f[0]) if f else 0, t))
    return out


def cut_dyr(text, keep, poi, all_buses):
    """Records of `keep`, then GENCLS H = 0 at the POI. Notes name any record that
       points at a bus outside the plant (a remote bus in its ICONs)."""
    kept, notes, models = [], [], collections.Counter()
    allb = set(keep) | set([poi])
    for b, r in dyr_records(text):
        if b not in keep:
            continue
        toks = re.findall(r"(?<![\w.'])(\d{4,7})(?![\w.'])", r.split("\n", 1)[-1] if "\n" in r else r)
        outside = sorted(set(int(t) for t in toks if int(t) in all_buses and int(t) not in allb))
        f = split_fields(r.split("\n")[0])
        mname = f[1].strip("'\"") if len(f) > 1 else "?"
        if mname.upper() == "USRMDL" and len(f) > 3:
            mname = f[3].strip("'\"")
        models[mname.upper()] += 1
        if outside:
            notes.append("record %s %s at bus %d names bus(es) outside the plant: %s -- check it"
                         % (mname, f[2] if len(f) > 2 else "", b, outside[:5]))
        kept.append(r if r.rstrip().endswith("/") else r + " /")
    kept.append("%d 'GENCLS' '%s' 0.0 0.0 /  infinite bus (H = 0)" % (poi, IB_ID))
    return "\n".join(kept) + "\n", notes, models


# ============================================================================
# PSS/E
# ============================================================================
psspy = None


def _bootstrap_psse():
    pyv = sys.version_info[:2]
    prefer = "PSSPY%d%d" % pyv
    roots = [os.environ.get("PSSE_ROOT", "")]
    for base in (r"C:\Program Files (x86)\PTI", r"C:\Program Files\PTI"):
        roots.extend(glob.glob(os.path.join(base, "PSSE3*")))

    def _add(d):
        if d and os.path.isdir(d):
            while d in sys.path:
                sys.path.remove(d)
            sys.path.insert(0, d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    for root in roots:
        if root and os.path.isdir(os.path.join(root, prefer)):
            _add(os.path.join(root, prefer))
            _add(os.path.join(root, "PSSBIN"))
            return
    for root in roots:
        if root and os.path.isdir(os.path.join(root, "PSSBIN")):
            _add(os.path.join(root, "PSSBIN"))
            return


def psse_start():
    global psspy
    if psspy is not None:
        return
    _bootstrap_psse()
    try:
        import psse34  # noqa: F401
    except Exception:
        pass
    import psspy as _p
    psspy = _p
    try:
        psspy.psseinit(150000)
    except Exception as e:
        print("[psse] psseinit: %s" % e)
    try:
        import redirect
        redirect.psse2py()
    except Exception:
        pass


def _ok(r):
    return r[0] if isinstance(r, (list, tuple)) else r


def write_raw(path):
    """The working case -> RAW, the call shapes the study uses elsewhere first."""
    for args in ((0, 1, [1, 1, 1, 0, 0, 0, 0], 0, path), (0, 1, [0, 1, 1, 0, 0, 0, 0], 0, path)):
        try:
            ie = _ok(psspy.rawd_2(*args))
        except Exception as e:
            ie = str(e)
        if ie in (0, None) and os.path.isfile(path) and os.path.getsize(path) > 0:
            return True
    return False


def dyda_dump(path):
    """The loaded dynamics -> .dyr text (the study's own call shapes)."""
    f = getattr(psspy, "dyda", None)
    if f is None:
        return False
    for args in ((0, 1, [1] * 9, 0, path), (0, 1, [1] * 6, 0, path),
                 (-1, 1, [1] * 9, 0, path), (0, 1, [1] * 10, 0, path)):
        try:
            f(*args)
        except Exception:
            continue
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return True
    return False


def solve():
    for opts in ([0, 0, 0, 1, 1, 0, 99, 0], [0, 0, 0, 1, 1, 1, 99, 0]):
        try:
            psspy.fnsl(opts)
        except Exception:
            pass
        try:
            s = _ok(psspy.solved())
        except Exception:
            s = None
        if s in (0, None):
            return True
    return False


def busv(b):
    v = psspy.busdat(b, "PU")
    a = psspy.busdat(b, "ANGLED")
    return (v[1] if _ok(v) == 0 else None), (a[1] if _ok(a) == 0 else None)


def machines_pq(buses):
    out = {}
    for b in buses:
        try:
            ie, ids = psspy.amachchar(-1, 4, ["ID"])
        except Exception:
            ids = None
        break
    ie, num = psspy.amachint(-1, 4, ["NUMBER"])
    ie2, ids = psspy.amachchar(-1, 4, ["ID"])
    ie3, pq = psspy.amachcplx(-1, 4, ["PQGEN"])
    if ie or ie2 or ie3:
        return out
    for n, i, s in zip(num[0], ids[0], pq[0]):
        if int(n) in buses:
            out[(int(n), str(i).strip())] = (complex(s).real, complex(s).imag)
    return out


def load_libraries(log):
    p = os.path.join(PROJ_DIR, ADDLIB_IDV)
    if os.path.isfile(p):
        try:
            psspy.runrspnsfile(p)
            log.append("user-model DLLs loaded with %s" % p)
        except Exception as e:
            log.append("*** %s failed: %s" % (p, e))
    for d in DLLS:
        try:
            psspy.addmodellibrary(d)
            log.append("DLL loaded: %s" % d)
        except Exception as e:
            log.append("*** DLL %s: %s" % (d, e))


def find_case(proj):
    if proj in CASE_BY_PROJECT:
        return _abs(CASE_BY_PROJECT[proj])
    c = [p for p in glob.glob(os.path.join(PROJ_DIR, "*_BESS_%s_*NEWPLANT.sav" % proj))
         if not re.search(r"egfoff|_cap\d|_poi\d|_mw\d", os.path.basename(p), re.I)]
    if not c:
        c = [p for p in glob.glob(os.path.join(PROJ_DIR, "*%s*.sav" % proj)) if "egfoff" not in p.lower()]
    return max(c, key=os.path.getmtime) if c else None


def one_project(proj, cfg, out_dir):
    name, poi, egf = cfg["name"], int(cfg["poi"]), [int(x) for x in cfg["egf"]]
    log = ["%s  --  %s  (POI %d)" % (name, proj, poi), "=" * 72]
    sav = find_case(proj)
    if not sav or not os.path.isfile(sav):
        print("[%s] *** no project case found in %s -- set CASE_BY_PROJECT" % (proj, PROJ_DIR))
        return False
    log.append("project case : %s" % sav)
    # ---- 1. the full case, solved, written to RAW
    if _ok(psspy.case(sav)) not in (0, None):
        print("[%s] *** case() failed: %s" % (proj, sav))
        return False
    solve()
    v_poi, a_poi = busv(poi)
    if v_poi is None:
        print("[%s] *** POI %d is not in %s" % (proj, poi, sav))
        return False
    tmp_full = os.path.join(out_dir, "_%s_full.raw" % name)
    if not write_raw(tmp_full):
        print("[%s] *** rawd_2 could not write the full case" % proj)
        return False
    with open(tmp_full, encoding="utf-8", errors="replace") as fh:
        raw = read_raw(fh.read())
    buses, adj, mach = net_from_raw(raw)
    keep = plant_side(adj, poi, egf, MAX_PLANT_BUSES)
    if not keep:
        print("[%s] *** nothing behind POI %d holds the EGF buses %s" % (proj, poi, egf))
        return False
    units = sorted((b, i) for b in keep for i in mach.get(b, ()))
    full_pq = machines_pq(keep)
    p_tot = sum(p for p, q in full_pq.values())
    q_tot = sum(q for p, q in full_pq.values())
    log.append("POI          : %d  %.5f pu  %.3f deg (full case)" % (poi, v_poi, a_poi))
    log.append("plant buses  : %d behind the POI" % len(keep))
    log.append("units        : %s" % ", ".join("%d '%s'" % u for u in units))
    log.append("plant output : %.1f MW, %.1f MVAr at the units (full case)" % (p_tot, q_tot))
    # ---- 2. the plant on an infinite bus
    text, notes = cut_raw(raw, keep, poi, -p_tot, -q_tot, v_poi, a_poi)
    tmp_cut = os.path.join(out_dir, "_%s_cut.raw" % name)
    with open(tmp_cut, "w", encoding="utf-8") as fh:
        fh.write(text)
    for n in notes:
        log.append("  note: " + n)
    if _ok(psspy.read(0, tmp_cut)) not in (0, None):
        print("[%s] *** read() rejected the plant RAW (%s)" % (proj, tmp_cut))
        return False
    ok_pf = solve()
    log.append("plant case solved: %s" % ("yes" if ok_pf else "NO"))
    cut_pq = machines_pq(keep)
    worst = 0.0
    for k, (p, q) in full_pq.items():
        if k in cut_pq:
            worst = max(worst, abs(cut_pq[k][0] - p), abs(cut_pq[k][1] - q))
            if abs(cut_pq[k][0] - p) > 0.5 or abs(cut_pq[k][1] - q) > 2.0:
                log.append("  unit %d '%s': P %.2f -> %.2f MW, Q %.2f -> %.2f MVAr"
                           % (k[0], k[1], p, cut_pq[k][0], q, cut_pq[k][1]))
    log.append("largest unit P/Q change against the full case: %.2f" % worst)
    sav_o = os.path.join(out_dir, name + ".sav")
    raw_o = os.path.join(out_dir, name + ".raw")
    psspy.save(sav_o)
    if not write_raw(raw_o):
        shutil.copy2(tmp_cut, raw_o)
        log.append("rawd_2 failed on the plant case -- the cut RAW is written as %s" % raw_o)
    log.append("written: %s, %s" % (sav_o, raw_o))
    # ---- 3. dynamics: the study's snapshot, else its combined .dyr
    stem = os.path.splitext(sav)[0]
    cnv, snp = SNP_BY_PROJECT.get(proj, (stem + ".cnv", stem + ".snp"))
    tmp_dyr = os.path.join(out_dir, "_%s_full.dyr" % name)
    got = False
    if os.path.isfile(cnv) and os.path.isfile(snp):
        load_libraries(log)
        if _ok(psspy.case(cnv)) in (0, None) and _ok(psspy.rstr(snp)) in (0, None):
            got = dyda_dump(tmp_dyr)
            log.append("dynamics from the snapshot %s: %s" % (snp, "yes" if got else "dyda FAILED"))
    if not got:
        dyr_src = DYR_BY_PROJECT.get(proj)
        if not dyr_src:
            c = glob.glob(os.path.join(PROJ_DIR, "*_with_BESS_%s*.dyr" % proj))
            dyr_src = max(c, key=os.path.getmtime) if c else None
        if dyr_src and os.path.isfile(dyr_src):
            shutil.copy2(dyr_src, tmp_dyr)
            got = True
            log.append("dynamics from the combined .dyr %s (deck changes made by .idv files are NOT in it)"
                       % dyr_src)
    if not got:
        print("[%s] *** no dynamics source (snapshot or combined .dyr)" % proj)
        return False
    with open(tmp_dyr, encoding="utf-8", errors="replace") as fh:
        dtext, dnotes, models = cut_dyr(fh.read(), keep, poi, set(buses))
    dyr_o = os.path.join(out_dir, name + ".dyr")
    with open(dyr_o, "w", encoding="utf-8") as fh:
        fh.write(dtext)
    log.append("written: %s (%d records + infinite bus)" % (dyr_o, sum(models.values())))
    log.append("models       : %s" % ", ".join("%s x%d" % (m, n) for m, n in sorted(models.items())))
    for n in dnotes:
        log.append("  note: " + n)
    # ---- 4. initialise and run flat
    if VALIDATE:
        log.extend(validate(sav_o, dyr_o, out_dir, name))
    with open(os.path.join(out_dir, name + "_CHECK.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    print("\n".join(log))
    return True


def validate(sav_o, dyr_o, out_dir, name):
    log = ["", "VALIDATION (PSS/E)"]
    try:
        psspy.case(sav_o)
        solve()
        psspy.cong(0)
        psspy.conl(0, 1, 1, [0, 0], [100.0, 0.0, 0.0, 100.0])
        psspy.conl(0, 1, 2, [0, 0], [100.0, 0.0, 0.0, 100.0])
        psspy.conl(0, 1, 3, [0, 0], [100.0, 0.0, 0.0, 100.0])
        psspy.ordr(0)
        psspy.fact()
        psspy.tysl(0)
        load_libraries(log)
        ie = _ok(psspy.dyre_new([1, 1, 1, 1], dyr_o, "", "", ""))
        log.append("dyre_new: %s" % ("ok" if ie in (0, None) else "ierr %s" % ie))
        out = os.path.join(out_dir, name + "_flat.out")
        prg = os.path.join(out_dir, name + "_init.txt")
        try:
            psspy.progress_output(2, prg, [0, 0])
        except Exception:
            pass
        psspy.machine_array_channel([-1, 2, 0], "", "")      # PELEC of every unit
        ie = _ok(psspy.strt_2([0, 1], out))
        log.append("strt_2: %s" % ("ok" if ie in (0, None) else "ierr %s" % ie))
        ie = _ok(psspy.run(0, FLAT_RUN_S, 99, 99, 0))
        log.append("flat run to %.1f s: %s" % (FLAT_RUN_S, "ok" if ie in (0, None) else "ierr %s" % ie))
        try:
            psspy.progress_output(1, "", [0, 0])
        except Exception:
            pass
        try:
            with open(prg, encoding="utf-8", errors="replace") as fh:
                t = fh.read()
            bad = [ln.strip() for ln in t.splitlines() if re.search(r"SUSPECT|NOT CONVERG|ERROR", ln, re.I)]
            log.append("initial-condition messages: %s" % ("none" if not bad else "%d -- see %s" % (len(bad), prg)))
            log.extend("  " + b for b in bad[:12])
        except Exception:
            pass
    except Exception as e:
        log.append("*** validation stopped: %s" % e)
    return log


def main():
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(_abs(OUT_DIR), stamp)
    os.makedirs(out_dir)
    print("[plant-models] output -> %s" % out_dir)
    psse_start()
    done = []
    for proj, cfg in PLANTS.items():
        if PROJECTS and proj not in PROJECTS:
            continue
        print("")
        print("[plant-models] %s ..." % proj)
        if one_project(proj, cfg, out_dir):
            done.append(cfg["name"])
    print("")
    print("[plant-models] done: %s" % (", ".join(done) or "nothing"))
    print("[plant-models] files in %s" % out_dir)


if __name__ == "__main__":
    main()
