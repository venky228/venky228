# -*- coding: utf-8 -*-
"""z4_plant_data_f.py -- PULLS THE PLANT DATA FOR THE REPORT, ONE SHEET PER PROJECT.

   For every project it opens the as-built GIA case (the _f study's
   ..._<project>_<MW>MW_f_NEWPLANT.sav, which holds the EGF AND the SGF), walks
   the plant from its POI and writes, for the EXISTING (EGF) and the SURPLUS (SGF)
   facility alike:

     * machines        : bus, id, name, kV, status, PGEN/QGEN, PMAX/PMIN, QMAX/QMIN,
                         MBASE, ZSORCE R / X'' (pu on MBASE), inverters (MBASE/4.4)
     * GSU transformers: the machine step-ups -- kV, winding MVA, R / X / Z % on the
                         winding base, X/R, rating, P/Q flow
     * collector       : the 34.5 kV equivalent lines -- R / X / B pu on 100 MVA,
                         rating, P/Q flow
     * MPTs            : every transformer from collector voltage up (2- and 3-winding),
                         each winding pair's R / X / Z % on its winding base
     * gen-tie         : the HV line(s) from the MPT to the POI -- R / X / B pu on
                         100 MVA, rating, length, P/Q at the POI end
     * POI             : bus, name, kV, area, zone, owner, voltage, MW / MVAr in

   Output (in <STUDY_DIR>\plantdata\):
     PLANT_DATA_ALL.xlsx   "Report fill" (one block per project, in the order of
                           the report's Table 2-x rows) + full detail sheets
     PLANT_DATA_<project>.txt  the same as ready-to-paste text

   Python 3.4, PSS/E 34. Put it beside the .sav files (C:\KV\ENGIE\CQ_MIT_f\SC) and run.
"""
from __future__ import print_function
import os, sys, csv, math, time, glob, traceback, zipfile
from xml.sax.saxutils import escape

# =============================================================================
# SETTINGS
# =============================================================================
STUDY_DIR = None                     # None = this script's folder
OUT_DIR   = None                     # None = <STUDY_DIR>\plantdata
# One row per project. poi = the bus the stability study meters the plant at.
# egf = the existing units (bus numbers). sav = optional, else the name match.
PROJECTS = [
    {"name": "SantaFe",       "poi": 765911, "egf": [765912, 765922, 765932, 765935]},
    {"name": "IronStar",      "poi": 560080, "egf": [587313, 587317]},
    {"name": "EmpirePrairie", "poi": 761383, "egf": [761379, 761382, 761400, 761403]},
    {"name": "EastFork",      "poi": 531429, "egf": [531620, 531607]},
]
NEW_GEN_BUS_PREFIX = "999"           # the SGF's bus block
INVERTER_MVA       = 4.4             # MVA per inverter, for the inverter count
COLLECTOR_KV_MAX   = 46.0            # at or below: collector voltage
POCKET_CAP         = 600             # largest plant the walk accepts (buses)
SBASE_FALLBACK     = 100.0

_HERE = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))
STUDY_DIR = STUDY_DIR or _HERE
OUT_DIR = OUT_DIR or os.path.join(STUDY_DIR, "plantdata")
if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)
LOG_FILE = os.path.join(OUT_DIR, "PLANT_DATA_%s.log" % time.strftime("%Y%m%d_%H%M%S"))


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


def _n(x, f="%.4f"):
    if x is None:
        return "n/a"
    try:
        return f % x
    except Exception:
        return str(x)


def _r(x, d=4):
    return round(x, d) if isinstance(x, (int, float)) else ("" if x is None else x)


# ---- array reads, defensive (one string at a time, so one unknown name does
#      not cost the rest) -------------------------------------------------------
def _arr(fn, sid, args, names):
    f = getattr(psspy, fn, None)
    out = {}
    if f is None:
        return out
    for nm in names:
        for a in args:
            try:
                ie, v = f(sid, *(list(a) + [[nm]]))
                if ie == 0 and v:
                    out[nm] = v[0]
                    break
            except Exception:
                continue
    return out


def read_case():
    """Every bus, machine, branch, 2- and 3-winding transformer of the case."""
    D = {}
    b_i = _arr("abusint", -1, [(2,)], ["NUMBER", "TYPE", "AREA", "ZONE", "OWNER"])
    b_r = _arr("abusreal", -1, [(2,)], ["BASE", "PU", "ANGLED"])
    b_c = _arr("abuschar", -1, [(2,)], ["NAME"])
    D["bus"] = {}
    for k, b in enumerate(b_i.get("NUMBER", [])):
        D["bus"][int(b)] = {"type": b_i.get("TYPE", [None] * (k + 1))[k], "area": b_i.get("AREA", [None] * (k + 1))[k],
                            "zone": b_i.get("ZONE", [None] * (k + 1))[k], "owner": b_i.get("OWNER", [None] * (k + 1))[k],
                            "kv": b_r.get("BASE", [0.0] * (k + 1))[k], "vpu": b_r.get("PU", [None] * (k + 1))[k],
                            "ang": b_r.get("ANGLED", [None] * (k + 1))[k],
                            "name": str(b_c.get("NAME", [""] * (k + 1))[k]).strip()}
    m_i = _arr("amachint", -1, [(4,), (2,)], ["NUMBER", "STATUS"])
    m_c = _arr("amachchar", -1, [(4,), (2,)], ["ID"])
    m_r = _arr("amachreal", -1, [(4,), (2,)], ["PGEN", "QGEN", "PMAX", "PMIN", "QMAX", "QMIN", "MBASE"])
    m_z = _arr("amachcplx", -1, [(4,), (2,)], ["ZSORCE"])
    D["mach"] = []
    for k, b in enumerate(m_i.get("NUMBER", [])):
        g = lambda d, n: (d.get(n) or [None] * (k + 1))[k] if len(d.get(n) or []) > k else None
        z = g(m_z, "ZSORCE")
        D["mach"].append({"bus": int(b), "id": str(g(m_c, "ID") or "").strip(), "status": g(m_i, "STATUS"),
                          "pgen": g(m_r, "PGEN"), "qgen": g(m_r, "QGEN"), "pmax": g(m_r, "PMAX"), "pmin": g(m_r, "PMIN"),
                          "qmax": g(m_r, "QMAX"), "qmin": g(m_r, "QMIN"), "mbase": g(m_r, "MBASE"),
                          "zr": (z.real if z is not None else None), "zx": (z.imag if z is not None else None)})
    # non-transformer branches, all of them
    br_i = _arr("abrnint", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["FROMNUMBER", "TONUMBER", "STATUS"])
    br_c = _arr("abrnchar", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["ID", "BRANCHNAME"])
    br_z = _arr("abrncplx", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["RX"])
    br_r = _arr("abrnreal", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["CHARGING", "RATEA", "RATEB", "RATEC", "RATE1", "RATE2", "RATE3", "LENGTH"])
    D["line"] = []
    for k, a in enumerate(br_i.get("FROMNUMBER", [])):
        g = lambda d, n: d[n][k] if (n in d and len(d[n]) > k) else None
        z = g(br_z, "RX")
        D["line"].append({"a": int(a), "b": int(g(br_i, "TONUMBER")), "ck": str(g(br_c, "ID") or "1").strip(),
                          "status": g(br_i, "STATUS"), "name": str(g(br_c, "BRANCHNAME") or "").strip(),
                          "r": (z.real if z is not None else None), "x": (z.imag if z is not None else None),
                          "b_ch": g(br_r, "CHARGING"),
                          "rate_a": g(br_r, "RATEA") if g(br_r, "RATEA") is not None else g(br_r, "RATE1"),
                          "rate_b": g(br_r, "RATEB") if g(br_r, "RATEB") is not None else g(br_r, "RATE2"),
                          "rate_c": g(br_r, "RATEC") if g(br_r, "RATEC") is not None else g(br_r, "RATE3"),
                          "length": g(br_r, "LENGTH")})
    # two-winding transformers, all
    t_i = _arr("atrnint", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["FROMNUMBER", "TONUMBER", "STATUS", "CZ", "CW"])
    t_c = _arr("atrnchar", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["ID", "XFRNAME"])
    t_z = _arr("atrncplx", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["RXACT", "RXNOM"])
    t_r = _arr("atrnreal", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["SBASE1", "NOMV1", "NOMV2", "RATIO", "RATIO2", "RATEA", "RATE1"])
    D["x2"] = []
    for k, a in enumerate(t_i.get("FROMNUMBER", [])):
        g = lambda d, n: d[n][k] if (n in d and len(d[n]) > k) else None
        z = g(t_z, "RXACT") if g(t_z, "RXACT") is not None else g(t_z, "RXNOM")
        D["x2"].append({"a": int(a), "b": int(g(t_i, "TONUMBER")), "ck": str(g(t_c, "ID") or "1").strip(),
                        "status": g(t_i, "STATUS"), "cz": g(t_i, "CZ"), "name": str(g(t_c, "XFRNAME") or "").strip(),
                        "zr": (z.real if z is not None else None), "zx": (z.imag if z is not None else None),
                        "sbase": g(t_r, "SBASE1"), "nomv1": g(t_r, "NOMV1"), "nomv2": g(t_r, "NOMV2"),
                        "ratio": g(t_r, "RATIO"), "ratio2": g(t_r, "RATIO2"),
                        "rate_a": g(t_r, "RATEA") if g(t_r, "RATEA") is not None else g(t_r, "RATE1")})
    # three-winding transformers, all
    w_i = _arr("atr3int", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER", "STATUS", "CZ"])
    w_c = _arr("atr3char", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["ID", "XFRNAME"])
    w_z = _arr("atr3cplx", -1, [(1, 1, 2, 1), (1, 3, 2, 1)],
               ["RX1-2ACT", "RX2-3ACT", "RX3-1ACT", "RX1-2NOM", "RX2-3NOM", "RX3-1NOM", "RX1-2", "RX2-3", "RX3-1"])
    w_r = _arr("atr3real", -1, [(1, 1, 2, 1), (1, 3, 2, 1)], ["SBS1-2", "SBS2-3", "SBS3-1", "SBASE1-2", "SBASE2-3", "SBASE3-1"])
    D["x3"] = []
    for k, a in enumerate(w_i.get("WIND1NUMBER", [])):
        g = lambda d, n: d[n][k] if (n in d and len(d[n]) > k) else None

        def _pair(p):
            for nm in ("RX%sACT" % p, "RX%sNOM" % p, "RX%s" % p):
                if g(w_z, nm) is not None:
                    return g(w_z, nm)
            return None

        def _sb(p, q):
            for nm in ("SBS%s" % p, "SBASE%s" % p):
                if g(w_r, nm) is not None:
                    return g(w_r, nm)
            return None
        D["x3"].append({"w1": int(a), "w2": int(g(w_i, "WIND2NUMBER")), "w3": int(g(w_i, "WIND3NUMBER")),
                        "ck": str(g(w_c, "ID") or "1").strip(), "status": g(w_i, "STATUS"), "cz": g(w_i, "CZ"),
                        "name": str(g(w_c, "XFRNAME") or "").strip(),
                        "z12": _pair("1-2"), "z23": _pair("2-3"), "z31": _pair("3-1"),
                        "s12": _sb("1-2", 0), "s23": _sb("2-3", 0), "s31": _sb("3-1", 0)})
    return D


def sbase():
    try:
        return float(psspy.sysmva())
    except Exception:
        return SBASE_FALLBACK


def _pct_on_winding(zr, zx, cz, sb_wind, sb_sys):
    """(R %, X %) on the winding MVA base.  RXACT / RXNOM (and the 3-winding
    RXn-nACT / NOM) come back in pu on the SYSTEM base whatever CZ the case
    uses, so always convert system base -> winding base.  With no winding base
    (None / 0) the value is returned on the system base."""
    if zr is None or zx is None:
        return None, None
    k = (float(sb_wind) / float(sb_sys)) if (sb_wind and sb_sys) else 1.0
    return 100.0 * zr * k, 100.0 * zx * k


def _flow(a, b, ck, third=None):
    try:
        if third is None:
            ie, s = psspy.brnflo(int(a), int(b), str(ck))
        else:
            ie, s = psspy.wnddt2(int(a), int(b), int(third), str(ck), "FLOW")
        if ie == 0 and s is not None:
            return float(s.real), float(s.imag)
    except Exception:
        pass
    return None, None


def _is_block(b):
    s = str(int(b))
    return s.startswith(NEW_GEN_BUS_PREFIX) and len(s) == 6


def plant_pocket(D, poi, seeds):
    adj = {}
    for l in D["line"]:
        adj.setdefault(l["a"], set()).add(l["b"]); adj.setdefault(l["b"], set()).add(l["a"])
    for t in D["x2"]:
        adj.setdefault(t["a"], set()).add(t["b"]); adj.setdefault(t["b"], set()).add(t["a"])
    for t in D["x3"]:
        for p, q in ((t["w1"], t["w2"]), (t["w2"], t["w3"]), (t["w1"], t["w3"])):
            adj.setdefault(p, set()).add(q); adj.setdefault(q, set()).add(p)
    seen = set()
    for s0 in seeds:
        if s0 in seen:
            continue
        q, loc = [s0], set([s0])
        while q:
            u = q.pop()
            for w in adj.get(u, ()):
                if w == poi or w in loc:
                    continue
                loc.add(w); q.append(w)
                if len(loc) > POCKET_CAP:
                    break
            if len(loc) > POCKET_CAP:
                break
        if len(loc) <= POCKET_CAP:
            seen |= loc
        else:
            print("  *** the walk from %d did not stay inside the plant (> %d buses) -- skipped ***" % (s0, POCKET_CAP))
    return seen


def project_data(p, D, sb):
    poi = int(p["poi"])
    egf_buses = set(int(b) for b in p.get("egf") or [])
    machs = [m for m in D["mach"] if m["bus"] in egf_buses or _is_block(m["bus"])]
    seeds = sorted(set(m["bus"] for m in machs))
    pk = plant_pocket(D, poi, seeds)
    sgf = [m for m in D["mach"] if _is_block(m["bus"]) and m["bus"] in pk]
    egf = [m for m in D["mach"] if m["bus"] in egf_buses]
    mbus = set(m["bus"] for m in D["mach"])
    kv = lambda b: (D["bus"].get(b) or {}).get("kv") or 0.0
    nm = lambda b: (D["bus"].get(b) or {}).get("name") or ""
    inplant = lambda a, b: (a in pk or a == poi) and (b in pk or b == poi) and (a in pk or b in pk)
    out = {"name": p["name"], "poi": poi, "egf": egf, "sgf": sgf, "gsu": [], "mpt": [], "col": [], "tie": [], "other": []}
    for t in D["x2"]:
        if not inplant(t["a"], t["b"]):
            continue
        rp, xp = _pct_on_winding(t["zr"], t["zx"], t["cz"], t["sbase"], sb)
        hi, lo = (t["a"], t["b"]) if kv(t["a"]) >= kv(t["b"]) else (t["b"], t["a"])
        pf, qf = _flow(hi, lo, t["ck"])
        rec = dict(t, hv=hi, lv=lo, kv_hv=kv(hi), kv_lv=kv(lo), r_pct=rp, x_pct=xp,
                   z_pct=(math.hypot(rp, xp) if rp is not None else None),
                   xr=((xp / rp) if (rp and xp is not None and rp > 0) else None),
                   p_hv=pf, q_hv=qf, sgf=(_is_block(t["a"]) or _is_block(t["b"])),
                   name_hv=nm(hi), name_lv=nm(lo))
        if lo in mbus:
            out["gsu"].append(rec)
        else:
            out["mpt"].append(rec)
    for t in D["x3"]:
        ws = [t["w1"], t["w2"], t["w3"]]
        if not any(w in pk for w in ws):
            continue
        rec = dict(t, kv=[kv(w) for w in ws], names=[nm(w) for w in ws], pairs=[])
        for lab, z, sbw in (("1-2", t["z12"], t["s12"]), ("2-3", t["z23"], t["s23"]), ("3-1", t["z31"], t["s31"])):
            rp, xp = _pct_on_winding(z.real if z is not None else None, z.imag if z is not None else None, t["cz"], sbw, sb)
            rec["pairs"].append((lab, sbw, rp, xp))
        rec["flows"] = []
        for k in range(3):
            o = [w for j, w in enumerate(ws) if j != k]
            pf, qf = _flow(ws[k], o[0], t["ck"], o[1])
            rec["flows"].append((ws[k], pf, qf))
        out["mpt"].append(dict(rec, three=True))
    for l in D["line"]:
        if not inplant(l["a"], l["b"]):
            continue
        if l["b"] == poi:
            a, b = l["b"], l["a"]
        elif l["a"] == poi:
            a, b = l["a"], l["b"]
        else:
            a, b = l["a"], l["b"]
        pf, qf = _flow(a, b, l["ck"])
        rec = dict(l, a=a, b=b, kv_a=kv(a), kv_b=kv(b), p_ab=pf, q_ab=qf, name_a=nm(a), name_b=nm(b),
                   sgf=(_is_block(l["a"]) or _is_block(l["b"])))
        if max(kv(a), kv(b)) <= COLLECTOR_KV_MAX:
            out["col"].append(rec)
        else:
            out["tie"].append(rec)
    # POI injection from the plant: every plant element ending at the POI, read at the POI
    tot_p = tot_q = 0.0
    for l in out["tie"] + out["col"]:
        if l["a"] == poi and l["p_ab"] is not None:
            tot_p -= l["p_ab"]; tot_q -= l["q_ab"]
    for t in out["mpt"]:
        if t.get("three"):
            for w, pf, qf in t["flows"]:
                if w == poi and pf is not None:
                    tot_p -= pf; tot_q -= qf
        elif t["hv"] == poi and t["p_hv"] is not None:
            tot_p -= t["p_hv"]; tot_q -= t["q_hv"]
    out["poi_p"], out["poi_q"] = tot_p, tot_q
    out["poi_bus"] = D["bus"].get(poi) or {}
    return out


def _neg(x):
    return (-x) if isinstance(x, (int, float)) else x


def _mach_rows(lst):
    return [{"bus": m["bus"], "id": m["id"], "status": "IN" if m["status"] == 1 else "OUT",
             "pgen": m["pgen"], "qgen": m["qgen"], "pmax": m["pmax"], "pmin": m["pmin"],
             "qmax": m["qmax"], "qmin": m["qmin"], "mbase": m["mbase"], "zr": m["zr"], "zx": m["zx"],
             "inv": (m["mbase"] / INVERTER_MVA) if (m["mbase"] and INVERTER_MVA) else None} for m in lst]


def report_text(P, D):
    """The report's Table 2-x rows, as text."""
    nm = lambda b: (D["bus"].get(b) or {}).get("name") or ""
    pb = P["poi_bus"]
    L = []
    L.append("=" * 90)
    L.append("%s   (POI %d %s, %.1f kV, area %s, zone %s, owner %s)"
             % (P["name"], P["poi"], pb.get("name", ""), pb.get("kv") or 0.0, pb.get("area"), pb.get("zone"), pb.get("owner")))
    L.append("=" * 90)
    for lab, lst in (("EGF (existing)", P["egf"]), ("SGF (surplus)", P["sgf"])):
        if not lst:
            L.append("%s: no machines found" % lab); continue
        tp = sum(m["pmax"] or 0.0 for m in lst); tm = sum(m["mbase"] or 0.0 for m in lst)
        tg = sum((m["pgen"] or 0.0) for m in lst if m["status"] == 1)
        L.append("%s: %d machine(s); PMAX total %.2f MW; MBASE total %.2f MVA; PGEN in case %.2f MW%s"
                 % (lab, len(lst), tp, tm, tg,
                    ("; %d x %.1f MVA inverters" % (round(tm / INVERTER_MVA), INVERTER_MVA)) if lab.startswith("SGF") else ""))
        for m in lst:
            L.append("   %d '%s' %-14s %s  PGEN %8.2f  QGEN %7.2f  PMAX %8.2f  PMIN %8.2f  QMAX %7.2f  QMIN %7.2f  "
                     "MBASE %7.2f  R %s  X'' %s"
                     % (m["bus"], m["id"], nm(m["bus"])[:14], "IN " if m["status"] == 1 else "OUT",
                        m["pgen"] or 0, m["qgen"] or 0, m["pmax"] or 0, m["pmin"] or 0, m["qmax"] or 0, m["qmin"] or 0,
                        m["mbase"] or 0, _n(m["zr"]), _n(m["zx"])))
    L.append("")
    L.append("Generation interconnection line(s) (HV lines in the plant, R/X/B pu on %.0f MVA):" % sbase())
    if not P["tie"]:
        L.append("   none -- the MPT high side IS the POI (or ties straight into it)")
    for l in P["tie"]:
        _into = (l["a"] == P["poi"] and l["p_ab"] is not None)
        L.append("   %d %s -- %d %s ck %s  %.1f kV  R %s  X %s  B %s  rate A/B/C %s/%s/%s MVA  length %s  %s"
                 % (l["b"], l["name_b"], l["a"], l["name_a"], l["ck"], max(l["kv_a"], l["kv_b"]), _n(l["r"], "%.6f"), _n(l["x"], "%.6f"),
                    _n(l["b_ch"], "%.6f"), _n(l["rate_a"], "%.0f"), _n(l["rate_b"], "%.0f"), _n(l["rate_c"], "%.0f"),
                    _n(l["length"], "%.2f"),
                    ("delivers %.1f MW / %.1f MVAr INTO the POI" % (-l["p_ab"], -l["q_ab"])) if _into else
                    ("flow %d -> %d %s MW / %s MVAr" % (l["a"], l["b"], _n(l["p_ab"], "%.1f"), _n(l["q_ab"], "%.1f")))))
    L.append("")
    L.append("Main power transformers (R / X / Z % on the winding MVA base):")
    for t in P["mpt"]:
        if t.get("three"):
            L.append("   3-winding %s ck %s: %d %s (%.1f kV) / %d %s (%.1f kV) / %d %s (%.1f kV)"
                     % (t["name"] or "", t["ck"], t["w1"], t["names"][0], t["kv"][0], t["w2"], t["names"][1], t["kv"][1],
                        t["w3"], t["names"][2], t["kv"][2]))
            for lab, sbw, rp, xp in t["pairs"]:
                L.append("      winding %s: base %s MVA  R %s %%  X %s %%  Z %s %%  X/R %s"
                         % (lab, _n(sbw, "%.1f"), _n(rp, "%.3f"), _n(xp, "%.3f"),
                            _n(math.hypot(rp, xp) if rp is not None else None, "%.3f"),
                            _n((xp / rp) if (rp and xp is not None and rp > 0) else None, "%.1f")))
            for w, pf, qf in t["flows"]:
                L.append("      winding at %d: %s MW / %s MVAr INTO the transformer (negative = out of it)" % (w, _n(pf, "%.1f"), _n(qf, "%.1f")))
        else:
            L.append("   %d %s (%.1f kV) / %d %s (%.1f kV) ck %s %s: base %s MVA  R %s %%  X %s %%  Z %s %%  X/R %s  "
                     "rate A %s MVA  out of the HV side %s MW / %s MVAr%s"
                     % (t["hv"], t["name_hv"], t["kv_hv"], t["lv"], t["name_lv"], t["kv_lv"], t["ck"], t["name"],
                        _n(t["sbase"], "%.1f"), _n(t["r_pct"], "%.3f"), _n(t["x_pct"], "%.3f"), _n(t["z_pct"], "%.3f"),
                        _n(t["xr"], "%.1f"), _n(t["rate_a"], "%.0f"), _n(_neg(t["p_hv"]), "%.1f"), _n(_neg(t["q_hv"]), "%.1f"),
                        "  (CZ=%s, raw %s + j%s)" % (t["cz"], _n(t["zr"], "%.5f"), _n(t["zx"], "%.5f")) if t["r_pct"] is None else ""))
    L.append("")
    for lab, flag in (("EGF", False), ("SGF", True)):
        L.append("%s machine step-up (GSU) transformers:" % lab)
        lst = [t for t in P["gsu"] if t["sgf"] == flag]
        if not lst:
            L.append("   none in the case (the unit sits on its collector bus, or the GSU is part of the equivalent)")
        for t in lst:
            L.append("   %d (%.2f kV) / %d (%.1f kV) ck %s: base %s MVA  R %s %%  X %s %%  Z %s %%  X/R %s  out of the HV side %s MW / %s MVAr"
                     % (t["lv"], t["kv_lv"], t["hv"], t["kv_hv"], t["ck"], _n(t["sbase"], "%.1f"), _n(t["r_pct"], "%.3f"),
                        _n(t["x_pct"], "%.3f"), _n(t["z_pct"], "%.3f"), _n(t["xr"], "%.1f"), _n(_neg(t["p_hv"]), "%.1f"), _n(_neg(t["q_hv"]), "%.1f")))
        L.append("%s collector equivalent lines (R/X/B pu on %.0f MVA):" % (lab, sbase()))
        lst = [l for l in P["col"] if l["sgf"] == flag]
        if not lst:
            L.append("   none")
        for l in lst:
            L.append("   %d %s -> %d %s ck %s  %.1f kV  R %s  X %s  B %s  rate A %s MVA  flow %s MW / %s MVAr"
                     % (l["a"], l["name_a"], l["b"], l["name_b"], l["ck"], max(l["kv_a"], l["kv_b"]), _n(l["r"], "%.6f"),
                        _n(l["x"], "%.6f"), _n(l["b_ch"], "%.6f"), _n(l["rate_a"], "%.0f"), _n(l["p_ab"], "%.1f"), _n(l["q_ab"], "%.1f")))
        L.append("")
    L.append("POI injection from the plant (read at the POI end of every plant element): %.1f MW / %.1f MVAr"
             % (P["poi_p"], P["poi_q"]))
    return L


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


def find_case(p):
    if p.get("sav"):
        s = p["sav"]
        return s if os.path.isabs(s) else os.path.join(STUDY_DIR, s)
    savs = sorted(glob.glob(os.path.join(STUDY_DIR, "*.sav")), key=os.path.getmtime, reverse=True)
    key = p["name"].lower()
    mine = [s for s in savs if key in os.path.basename(s).lower()]
    keep = [s for s in mine if not any(t in os.path.basename(s).lower() for t in ("egfoff", "_s1_", "_s2_", "_s3_"))]
    pick = ([s for s in keep if "newplant" in os.path.basename(s).lower()] or keep or mine or [None])[0]
    if pick is None:
        raise RuntimeError("no .sav for %s in %s" % (p["name"], STUDY_DIR))
    return pick


def main():
    psse_init()
    rows_sum, rows_m, rows_x, rows_l, rows_3 = [], [], [], [], []
    all_txt = []
    for p in PROJECTS:
        try:
            sav = find_case(p)
            print("[%s] %s: %s" % (_ts(), p["name"], sav))
            if psspy.case(sav) != 0:
                raise RuntimeError("case() failed")
            # flows are read off the case AS SAVED (it was saved solved) -- not re-solved here
            D = read_case()
            sb = sbase()
            P = project_data(p, D, sb)
            L = ["case: %s" % sav] + report_text(P, D)
            with open(os.path.join(OUT_DIR, "PLANT_DATA_%s.txt" % p["name"]), "w") as fh:
                fh.write("\n".join(L) + "\n")
            all_txt += L + [""]
            nm = lambda b: (D["bus"].get(b) or {}).get("name") or ""
            for lab, lst in (("EGF", P["egf"]), ("SGF", P["sgf"])):
                tp = sum(m["pmax"] or 0.0 for m in lst); tm = sum(m["mbase"] or 0.0 for m in lst)
                rows_sum.append([p["name"], lab, len(lst), _r(tp, 2), _r(tm, 2),
                                 _r(sum((m["pgen"] or 0.0) for m in lst if m["status"] == 1), 2),
                                 (round(tm / INVERTER_MVA) if lab == "SGF" and tm else ""),
                                 P["poi"], P["poi_bus"].get("name", ""), _r(P["poi_bus"].get("kv"), 1),
                                 P["poi_bus"].get("area"), _r(P["poi_p"], 2), _r(P["poi_q"], 2)])
                for m in _mach_rows(lst):
                    rows_m.append([p["name"], lab, m["bus"], m["id"], nm(m["bus"]), m["status"], _r(m["pgen"], 2), _r(m["qgen"], 2),
                                   _r(m["pmax"], 2), _r(m["pmin"], 2), _r(m["qmax"], 2), _r(m["qmin"], 2), _r(m["mbase"], 2),
                                   _r(m["zr"], 5), _r(m["zx"], 5), _r(m["inv"], 1)])
            for t in P["gsu"] + [x for x in P["mpt"] if not x.get("three")]:
                rows_x.append([p["name"], ("GSU" if t in P["gsu"] else "MPT"), ("SGF" if t["sgf"] else "EGF/shared"),
                               t["hv"], t["name_hv"], _r(t["kv_hv"], 2), t["lv"], t["name_lv"], _r(t["kv_lv"], 2), t["ck"],
                               t["name"], _r(t["sbase"], 2), _r(t["r_pct"], 4), _r(t["x_pct"], 4), _r(t["z_pct"], 4), _r(t["xr"], 2),
                               _r(t["rate_a"], 1), t["cz"], _r(_neg(t["p_hv"]), 2), _r(_neg(t["q_hv"]), 2)])
            for t in [x for x in P["mpt"] if x.get("three")]:
                for (lab, sbw, rp, xp) in t["pairs"]:
                    rows_3.append([p["name"], t["w1"], t["w2"], t["w3"], "/".join("%.1f" % k for k in t["kv"]), t["ck"], t["name"],
                                   lab, _r(sbw, 2), _r(rp, 4), _r(xp, 4),
                                   _r(math.hypot(rp, xp) if rp is not None else None, 4),
                                   _r((xp / rp) if (rp and xp is not None and rp > 0) else None, 2), t["cz"]])
            for l in P["tie"] + P["col"]:
                rows_l.append([p["name"], ("gen-tie" if l in P["tie"] else "collector"), ("SGF" if l["sgf"] else "EGF/shared"),
                               l["a"], l["name_a"], l["b"], l["name_b"], l["ck"], _r(max(l["kv_a"], l["kv_b"]), 2),
                               _r(l["r"], 6), _r(l["x"], 6), _r(l["b_ch"], 6), _r(l["rate_a"], 1), _r(l["rate_b"], 1),
                               _r(l["rate_c"], 1), _r(l["length"], 3), _r(l["p_ab"], 2), _r(l["q_ab"], 2),
                               (_r(_neg(l["p_ab"]), 2) if l["a"] == P["poi"] else ""),
                               (_r(_neg(l["q_ab"]), 2) if l["a"] == P["poi"] else "")])
        except Exception as e:
            print("*** %s FAILED: %s ***" % (p.get("name"), e)); traceback.print_exc()
    write_xlsx(os.path.join(OUT_DIR, "PLANT_DATA_ALL.xlsx"), [
        ("Summary", ["Project", "Facility", "Machines", "PMAX total (MW)", "MBASE total (MVA)", "PGEN in case (MW)",
                     "Inverters (4.4 MVA)", "POI bus", "POI name", "POI kV", "Area", "POI MW in", "POI MVAr in"], rows_sum,
         [14, 9, 9, 12, 12, 12, 10, 9, 16, 7, 6, 10, 10]),
        ("Machines", ["Project", "Facility", "Bus", "Id", "Name", "Status", "PGEN (MW)", "QGEN (MVAr)", "PMAX (MW)", "PMIN (MW)",
                      "QMAX (MVAr)", "QMIN (MVAr)", "MBASE (MVA)", "R source (pu MBASE)", "X'' source (pu MBASE)", "Inverters"], rows_m,
         [14, 8, 9, 4, 16, 6, 10, 10, 10, 10, 10, 10, 10, 12, 12, 9]),
        ("Transformers", ["Project", "Role", "Facility", "HV bus", "HV name", "HV kV", "LV bus", "LV name", "LV kV", "Ckt", "Name",
                          "Winding MVA", "R (% winding base)", "X (% winding base)", "Z (%)", "X/R", "Rate A (MVA)", "CZ",
                          "P out of HV side (MW)", "Q out of HV side (MVAr)"], rows_x,
         [14, 6, 10, 9, 14, 7, 9, 14, 7, 4, 12, 9, 10, 10, 9, 7, 9, 4, 10, 10]),
        ("3-winding", ["Project", "W1", "W2", "W3", "kV", "Ckt", "Name", "Pair", "Base (MVA)", "R (%)", "X (%)", "Z (%)", "X/R", "CZ"],
         rows_3, [14, 9, 9, 9, 16, 4, 12, 6, 9, 9, 9, 9, 7, 4]),
        ("Lines", ["Project", "Role", "Facility", "From", "From name", "To", "To name", "Ckt", "kV", "R (pu)", "X (pu)", "B (pu)",
                   "Rate A (MVA)", "Rate B (MVA)", "Rate C (MVA)", "Length", "P From->To (MW)", "Q From->To (MVAr)",
                   "P into POI (MW)", "Q into POI (MVAr)"], rows_l,
         [14, 9, 10, 9, 14, 9, 14, 4, 7, 10, 10, 10, 9, 9, 9, 8, 10, 10, 10, 10]),
        ("Report text", ["Line"], [[x] for x in all_txt], [180]),
    ])
    print("[%s] -> %s" % (_ts(), os.path.join(OUT_DIR, "PLANT_DATA_ALL.xlsx")))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try:
            sys.stdout, sys.stderr = _ORIG_OUT, _ORIG_ERR
            _LOG_FH.close()
        except Exception:
            pass
