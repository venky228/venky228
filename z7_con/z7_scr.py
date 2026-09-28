# -*- coding: utf-8 -*-
"""z7_scr.py -- short-circuit ratio (SCR) at each project's POI, system intact
and with the elements each fault of the study trips taken out.

Run with the PSS/E Python, from the study root (beside z7_main.py):

    C:\\Python34\\python.exe z7_scr.py

Reads only -- the case is loaded, changed in memory and never saved.
Writes SCR_AT_POI.txt and SCR_AT_POI.csv in the study root.

WHAT IS CALCULATED
  SCMVA  = three-phase short-circuit MVA at the POI bus = SBASE / |Z1th|, from
           PSS/E's own short-circuit solution (ASCC, positive-sequence Thevenin
           impedance at the POI). Z1th is an impedance, so the value does not
           depend on the pre-fault voltage.
  SCR    = SCMVA / project MW (every study size of the project, when it has two).
  Two network states per project, both from the BASE case (no new plant in it):
    all in      every existing machine in service
    EGF off     the existing machines at the project's own feeder buses OUT --
                the project replaces them (disable_existing), so this is the
                SCR the new plant sees on its own
  and, for each state, the intact system and every OUTAGE SET taken from the
  project's fault list (trip_elements + trip_3wind + drop_machines +
  pre_outage of each fault), plus any lines listed in EXTRA_OUTAGES below.
  Faults that take out the same elements share one row.

  SCR < 3 is flagged WEAK and SCR < 1.5 VERY WEAK (common planning screens;
  set the limits below).
"""
from __future__ import print_function
import os, sys, re, ast, csv, io, time

# ---- SETTINGS -------------------------------------------------------------
PROJECTS = []          # [] = PROJECTS from z7_main.py
EXTRA_OUTAGES = []     # extra single outages, e.g. ["765911-531501-345-1"] (from-to-kV-ckt)
POI_N1 = True          # also every line / transformer AT the POI bus, one at a time
WEAK_SCR = 3.0         # SCR below this is flagged WEAK
VERY_WEAK_SCR = 1.5    # ... and below this VERY WEAK
MAX_FAULTS = 0         # 0 = every fault of the list; N = first N (a quick test)
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MAIN = os.path.join(HERE, "z7_main.py")


def _panel(name, default=None):
    """A top-level setting of z7_main.py, read without importing it."""
    try:
        src = io.open(MAIN, encoding="latin-1").read()
        for node in ast.parse(src).body:
            if isinstance(node, ast.Assign) and any(
                    getattr(t, "id", None) == name for t in node.targets):
                return ast.literal_eval(node.value)
    except Exception:
        pass
    return default


def _study_projects():
    """BESS_PROJECTS of the project study script: name -> {poi, mw[], feeders}."""
    folder = _panel("PROJ_FOLDER", "Projects")
    d = folder if os.path.isabs(folder) else os.path.join(HERE, folder)
    out = {}
    for p in (os.path.join(d, "z7_spp_p.py"), os.path.join(HERE, "z7_spp_p.py")):
        if not os.path.isfile(p):
            continue
        try:
            for node in ast.parse(io.open(p, encoding="latin-1").read()).body:
                if isinstance(node, ast.Assign) and any(
                        getattr(t, "id", None) == "BESS_PROJECTS" for t in node.targets):
                    for r in ast.literal_eval(node.value):
                        mw = r.get("mw")
                        mw = [float(x) for x in (mw if isinstance(mw, (list, tuple)) else [mw])]
                        out[r["name"]] = {"poi": int(r["poi"]), "mw": mw,
                                          "feeders": [int(b) for b in r.get("feeders") or []]}
            if out:
                return out
        except Exception as e:
            print("[scr] could not read BESS_PROJECTS from %s (%s)" % (p, e))
    return out


def _base_sav(proj):
    folder = _panel("BASE_FOLDER", "Base")
    d = folder if os.path.isabs(folder) else os.path.join(HERE, folder)
    sav = (_panel("BASE_SAV_BY_PROJECT", {}) or {}).get(proj) or _panel("BASE_SAV")
    if sav and not os.path.isabs(sav):
        sav = os.path.join(d, sav)
    return sav


def _fault_list(proj):
    t = _panel("SHARED_FAULTS_CSV") or r"{root}\FAULT_LISTS_BPM\SPP_FAULTS_CON_{project}.csv"
    p = t.replace("{root}", HERE).replace("{project}", proj).replace("\\", os.sep)
    if os.path.isfile(p):
        return p
    alt = os.path.join(HERE, "FAULT_LISTS_BPM", "SPP_FAULTS_CON_%s.csv" % proj)
    return alt if os.path.isfile(alt) else p


# ---- PSS/E ------------------------------------------------------------------
psspy = None
_i = _f = None


def _start_psse():
    global psspy, _i, _f
    import z7_spike_find as S                  # the same PSS/E path setup the tools use
    psspy = S._psspy()
    if psspy is None:
        return False
    _i, _f = psspy.getdefaultint(), psspy.getdefaultreal()
    return True


def _ie(r):
    return r[0] if isinstance(r, (tuple, list)) else r


def _load(sav):
    ie = psspy.case(sav)
    if ie:
        raise RuntimeError("psspy.case(%s) ierr=%s" % (sav, ie))
    for fn in ("short_circuit_units", "short_circuit_coordinates"):
        try:
            getattr(psspy, fn)(1)              # per unit, polar
        except Exception:
            pass


def scmva(bus):
    """Three-phase short-circuit MVA at one bus, or (None, reason)."""
    import pssarrays
    try:
        sbase = float(psspy.sysmva())
    except Exception:
        sbase = 100.0
    ie = psspy.bsys(1, 0, [0.0, 0.0], 0, [], 1, [int(bus)], 0, [], 0, [])
    if ie:
        return None, "bsys ierr=%s" % ie
    # THE KEYWORDS DIFFER BETWEEN PSS/E BUILDS. PSS/E 34 takes sid and all by
    # position and everything else only by keyword ("takes 2 positional
    # arguments"), and an option it does not know is a TypeError -- so try the
    # forms in turn, the fullest first.
    r, err = None, ""
    for kw in ({"flt3ph": 1, "fltlg": 0, "fltllg": 0, "fltll": 0},
               {"flt3ph": 1, "fltlg": 0},
               {"flt3ph": 1},
               {}):
        try:
            r = pssarrays.ascc_currents(1, 0, **kw)
            break
        except Exception as e:
            err = "%s" % e
    if r is None:
        return None, "ascc: %s" % err
    _dump_once(r)
    if getattr(r, "ierr", 0):
        return None, "ascc ierr=%s" % r.ierr
    # THEVENIN IMPEDANCE FIRST: SCMVA = SBASE / |Z1|, independent of the
    # pre-fault voltage. The fault current is the fallback (pu, 1.0 pu pre-fault).
    z1 = _first(r, ("thevzpu", "thevz", "zthev"), ("z1", "zpos", "z"))
    if z1 is not None:
        if abs(z1) > 1e-9:
            return sbase / abs(z1), ""
        return None, "POI Thevenin impedance is zero"
    i1 = _first(r, ("flt3ph", "fltcur3ph", "fltcur"), ("ia1", "i1", "ia"))
    if i1 is not None and abs(i1) > 0:
        # per unit when short_circuit_units(1) took; amps otherwise (large)
        if abs(i1) > 1000.0:
            try:
                kv = psspy.busdat(int(bus), "BASE")[1]
                return abs(i1) * float(kv) * 3 ** 0.5 / 1000.0, ""
            except Exception:
                return None, "fault current is in amps and the bus kV could not be read"
        return abs(i1) * sbase, ""
    return None, "no short-circuit result (POI isolated?)"


def _pick(x, names):
    for n in names:
        try:
            v = x[n] if isinstance(x, dict) else getattr(x, n)
            return v
        except Exception:
            continue
    return None


def _first(r, outer, inner):
    """A complex number r.<outer>[0].<inner> (or dict / keyed forms), else None."""
    o = _pick(r, outer)
    if o is None:
        return None
    for el in ([o[0]] if isinstance(o, (list, tuple)) and o else []) + [o]:
        try:
            if isinstance(el, dict) and el and not any(k in el for k in inner):
                el = list(el.values())[0]
        except Exception:
            pass
        v = _pick(el, inner)
        if isinstance(v, (list, tuple)) and v:
            v = v[0]
        if isinstance(v, (int, float, complex)):
            return complex(v)
    return None


_DUMPED = [False]


def _dump_once(r):
    """What this PSS/E build returns, printed ONCE -- so a result the reader
       above does not recognise can be read off the console."""
    if _DUMPED[0]:
        return
    _DUMPED[0] = True
    try:
        names = [n for n in dir(r) if not n.startswith("_")]
        print("[scr] ascc_currents returned: %s" % ", ".join(names))
        for n in ("thevzpu", "flt3ph", "fltbus"):
            if n in names:
                print(("[scr]   %s = %r" % (n, getattr(r, n)))[:400])
    except Exception:
        pass


# ---- ELEMENT STATUS ----------------------------------------------------------
_W3 = {}


def _three_wind_map():
    if _W3:
        return _W3
    try:
        ie, a = psspy.atr3int(-1, 1, 1, 1, 1, ["WIND1NUMBER", "WIND2NUMBER", "WIND3NUMBER"])
        ie2, c = psspy.atr3char(-1, 1, 1, 1, 1, ["ID"])
        for w1, w2, w3, ck in zip(a[0], a[1], a[2], c[0]):
            ck = str(ck).strip()
            t = (int(w1), int(w2), int(w3))
            for x, y in ((w1, w2), (w1, w3), (w2, w3)):
                _W3[(int(x), int(y), ck)] = t
                _W3[(int(y), int(x), ck)] = t
    except Exception:
        pass
    return _W3


def switch(a, b, ck, st, c=None):
    """Open (st=0) or close (st=1) a line, 2-winding or 3-winding transformer.
       True when a call accepted it."""
    a, b, ck = int(a), int(b), str(ck).strip()
    tries = []
    if c is None:
        tries += [lambda: psspy.branch_chng_3(a, b, ck, [st] + [_i] * 5, [_f] * 12, [_f] * 12, ""),
                  lambda: psspy.branch_chng(a, b, ck, [st] + [_i] * 5, [_f] * 12),
                  lambda: psspy.two_winding_chng_6(a, b, ck, [st] + [_i] * 15, [_f] * 26,
                                                   [_f] * 3, "", "")]
        w = _three_wind_map().get((a, b, ck))
    else:
        w = (a, b, int(c))
    if w:
        tries += [lambda: psspy.three_wnd_imped_chng_4(w[0], w[1], w[2], ck, [st] + [_i] * 11,
                                                       [_f] * 30, [_f] * 3, ""),
                  lambda: psspy.three_wnd_imped_chng_3(w[0], w[1], w[2], ck, [st] + [_i] * 11,
                                                       [_f] * 28, [_f] * 3, "")]
    for t in tries:
        try:
            if _ie(t()) in (0, None):
                return True
        except Exception:
            continue
    return False


def machine(bus, mid, st):
    try:
        return _ie(psspy.machine_chng_2(int(bus), str(mid), [st] + [_i] * 5, [_f] * 17)) in (0, None)
    except Exception:
        return False


def machines_at(buses):
    """In-service machines [(bus, id)] at the given buses."""
    out = []
    try:
        ie, n = psspy.amachint(-1, 1, ["NUMBER"])
        ie2, ids = psspy.amachchar(-1, 1, ["ID"])
        for b, i in zip(n[0], ids[0]):
            if int(b) in buses:
                out.append((int(b), str(i).strip()))
    except Exception:
        pass
    return out


def poi_branches(poi):
    """Every in-service line / 2-winding transformer at the POI, as 'from-to-kV-ckt'."""
    out = []
    try:
        ie, a = psspy.abrnint(-1, 1, 1, 3, 2, ["FROMNUMBER", "TONUMBER"])   # entry 2: both ends
        ie2, c = psspy.abrnchar(-1, 1, 1, 3, 2, ["ID"])
        for x, y, ck in zip(a[0], a[1], c[0]):
            e = "%d-%d-0-%s" % (x, y, str(ck).strip())
            if int(x) == poi and e not in out:
                out.append(e)
    except Exception:
        pass
    return out


# ---- OUTAGE SETS -------------------------------------------------------------
def _parse(txt, n):
    """'a-b-kv-ck;...' (n=4) or 'a-b-c-ck;...' (3-wind, n=4) or 'bus-id;...' (n=2)."""
    out = []
    for part in (txt or "").split(";"):
        part = part.strip()
        if not part:
            continue
        f = part.split("-")
        if len(f) < n:
            continue
        head, ck = f[:n - 1], "-".join(f[n - 1:])
        try:
            out.append(tuple(int(x) for x in head) + (ck.strip(),))
        except ValueError:
            continue
    return out


def outage_sets(proj, poi):
    """[(key, label, branches[(a,b,ck)], wind3[(a,b,c,ck)], machines[(bus,id)], fault ids)]"""
    sets, order = {}, []

    def add(fid, br, w3, mc, what):
        key = (tuple(sorted(br)), tuple(sorted(w3)), tuple(sorted(mc)))
        if not any(key):
            return
        if key not in sets:
            sets[key] = {"br": br, "w3": w3, "mc": mc, "ids": [], "what": what}
            order.append(key)
        if fid not in sets[key]["ids"]:
            sets[key]["ids"].append(fid)

    fl = _fault_list(proj)
    n = 0
    if os.path.isfile(fl):
        with io.open(fl, encoding="utf-8", errors="replace") as fh:
            for r in csv.DictReader(fh):
                fid = (r.get("fault_id") or "").strip()
                if not fid or fid.upper().startswith("FLAT"):
                    continue
                n += 1
                if MAX_FAULTS and n > MAX_FAULTS:
                    break
                br = [(a, b, ck) for a, b, _kv, ck in
                      _parse(r.get("trip_elements"), 4) + _parse(r.get("pre_outage"), 4)]
                w3 = _parse(r.get("trip_3wind"), 4)
                mc = [(b, str(i)) for b, i in _parse(r.get("drop_machines"), 2)]
                add(fid, br, w3, mc, "%s %s" % ((r.get("planning_event") or "").strip(),
                                                (r.get("subtype") or "").strip()))
    else:
        print("[scr] %s: no fault list at %s -- intact system and extra outages only" % (proj, fl))
    extra = list(EXTRA_OUTAGES) + (poi_branches(poi) if POI_N1 else [])
    for e in extra:
        p = _parse(e, 4)
        if p:
            a, b, _kv, ck = p[0]
            add("N-1 %d-%d ck %s" % (a, b, ck), [(a, b, ck)], [], [], "N-1 line/xfmr")
    return [(k, sets[k]["what"], sets[k]["br"], sets[k]["w3"], sets[k]["mc"], sets[k]["ids"])
            for k in order]


def _fmt_set(br, w3, mc):
    s = ["%d-%d(%s)" % x for x in br] + ["3w %d-%d-%d(%s)" % x for x in w3] + \
        ["gen %d '%s'" % x for x in mc]
    return "; ".join(s)


def _flag(scr):
    if scr is None:
        return ""
    return "VERY WEAK" if scr < VERY_WEAK_SCR else ("WEAK" if scr < WEAK_SCR else "")


# ---- ONE PROJECT -------------------------------------------------------------
def run_project(proj, info, rows, L):
    poi, mws, feeders = info["poi"], info["mw"], set(info["feeders"])
    sav = _base_sav(proj)
    L.append("")
    L.append("=" * 120)
    L.append(" %s   POI %d   project %s MW   case %s" % (
        proj, poi, " / ".join("%.0f" % m for m in mws), os.path.basename(sav or "?")))
    L.append("=" * 120)
    _load(sav)
    try:
        nm = psspy.notona(poi)[1].strip()
    except Exception:
        nm = ""
    egf = machines_at(feeders)
    L.append(" POI bus %d %s | existing machines at the feeders (EGF): %s"
             % (poi, nm, ", ".join("%d '%s'" % x for x in egf) or "none"))
    sets = outage_sets(proj, poi)
    print("[scr] %s: POI %d, %d outage set(s)" % (proj, poi, len(sets)))
    hdr = " %-38s %-14s" % ("outage", "state")
    for m in mws:
        hdr += " %9s %11s" % ("SCMVA", "SCR@%.0fMW" % m)
    L.append(hdr + "  flag   faults")
    L.append(" " + "-" * 118)
    for state, off in (("all in", []), ("EGF off", egf)):
        _load(sav)
        _W3.clear()
        for b, i in off:
            machine(b, i, 0)
        base, why = scmva(poi)
        cases = [("INTACT", "", [], [], [], [])] + [
            ("set", what, br, w3, mc, ids) for _k, what, br, w3, mc, ids in sets]
        for tag, what, br, w3, mc, ids in cases:
            opened, missing = [], []
            for a, b, ck in br:
                (opened if switch(a, b, ck, 0) else missing).append(("b", a, b, ck))
            for a, b, c, ck in w3:
                (opened if switch(a, b, ck, 0, c) else missing).append(("w", a, b, c, ck))
            for b, i in mc:
                if (b, i) in off:
                    continue
                (opened if machine(b, i, 0) else missing).append(("m", b, i))
            v, why2 = (base, why) if tag == "INTACT" else scmva(poi)
            # PUT IT BACK exactly as it was, so the next set starts from the same case.
            for o in opened:
                if o[0] == "b":
                    switch(o[1], o[2], o[3], 1)
                elif o[0] == "w":
                    switch(o[1], o[2], o[4], 1, o[3])
                else:
                    machine(o[1], o[2], 1)
            label = "INTACT system" if tag == "INTACT" else _fmt_set(br, w3, mc)
            line = " %-38s %-14s" % (label[:38], state)
            scrs = []
            for m in mws:
                scr = (v / m) if (v and m) else None
                scrs.append(scr)
                line += " %9s %11s" % ("%.0f" % v if v else "-", "%.2f" % scr if scr else "-")
            fl = _flag(min([s for s in scrs if s is not None] or [None]) if any(scrs) else None)
            note = why2 or ("not found: %s" % ", ".join(str(x[1:]) for x in missing) if missing else "")
            L.append(line + "  %-9s %s%s" % (fl, ", ".join(ids[:6]) + (" +%d" % (len(ids) - 6) if len(ids) > 6 else ""),
                                             ("   [%s]" % note) if note else ""))
            if len(label) > 38:
                L.append("   %s" % label)
            dl = (v - base) if (v and base and tag != "INTACT") else None
            rows.append([proj, poi, state, "INTACT" if tag == "INTACT" else what, label,
                         " ".join(ids), "%.1f" % v if v else ""] +
                        ["%.3f" % s if s else "" for s in scrs] +
                        ["%.1f" % dl if dl is not None else "", fl, note])
    # THE WORST, where a reader looks first.
    worst = [r for r in rows if r[0] == proj and r[6]]
    if worst:
        w = min(worst, key=lambda r: float(r[6]))
        L.append(" LOWEST SCMVA: %s MVA (%s, %s) -- faults %s" % (w[6], w[2], w[4] or "intact", w[5] or "-"))


def main():
    if not _start_psse():
        print("psspy NOT available -- run this with the PSS/E Python")
        return 1
    info = _study_projects()
    projs = PROJECTS or _panel("PROJECTS", []) or sorted(info)
    rows, L = [], []
    L.append(" SCR AT THE POI -- %s" % time.strftime("%Y-%m-%d %H:%M"))
    L.append(" SCMVA = SBASE/|Z1 Thevenin| at the POI (PSS/E ASCC, 3-phase); SCR = SCMVA / project MW")
    L.append(" all in = every existing machine in service; EGF off = the project's feeder machines out")
    L.append(" WEAK < %.1f, VERY WEAK < %.1f.  Base case (no new plant)." % (WEAK_SCR, VERY_WEAK_SCR))
    nmw = max([len(info[p]["mw"]) for p in projs if p in info] or [1])
    for p in projs:
        if p not in info:
            L.append("\n %s: not in BESS_PROJECTS -- skipped" % p)
            continue
        try:
            run_project(p, info[p], rows, L)
        except Exception as e:
            L.append("\n %s: FAILED -- %s" % (p, e))
            print("[scr] %s failed: %s" % (p, e))
    txt = "\n".join(L)
    print(txt)
    with io.open(os.path.join(HERE, "SCR_AT_POI.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    with open(os.path.join(HERE, "SCR_AT_POI.csv"), "w") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["project", "poi", "state", "event", "outage", "faults", "scmva"] +
                   ["scr_size%d" % (k + 1) for k in range(nmw)] +
                   ["delta_scmva_vs_intact", "flag", "note"])
        for r in rows:
            k = len(r) - 10          # size columns this project has
            w.writerow(r[:7] + r[7:7 + k] + [""] * (nmw - k) + r[7 + k:])
    print("\nwritten: SCR_AT_POI.txt / SCR_AT_POI.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
