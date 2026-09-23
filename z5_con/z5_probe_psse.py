# -*- coding: utf-8 -*-
"""z5_probe_psse.py -- checks the PSS/E calls z5_spike_find.py relies on.

Run with the PSS/E Python, from the study root:

    C:\\Python34\\python.exe z5_probe_psse.py

Writes PROBE_PSSE.txt in the same folder -- send that file back.
Reads only; changes nothing. Takes a few minutes (it opens one .out).
"""
from __future__ import print_function
import os, sys, glob, time

# ---- SETTINGS -------------------------------------------------------------
BUS = 539667          # a bus that fails often -- the probe prints what is around it
OUT = ""              # "" = the first .out in Base\results_base\*_spp\outs\ ; or a full path
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import z5_spike_find as S                                  # same PSS/E path setup + dyr parser

LOG = []


def P(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    LOG.append(s)


def first(v, n=3):
    try:
        return list(v)[:n]
    except Exception:
        return v


def try_call(label, fn, *args):
    try:
        r = fn(*args)
        ierr = r[0] if isinstance(r, tuple) else r
        val = r[1] if isinstance(r, tuple) and len(r) > 1 else None
        if val is not None:
            try:
                val = [first(x) for x in val]
            except Exception:
                pass
        P("  %-46s ierr=%s  %s" % (label, ierr, val))
    except Exception as e:
        P("  %-46s RAISED %s: %s" % (label, type(e).__name__, e))


def main():
    P("PROBE -- %s   Python %s" % (time.strftime("%Y-%m-%d %H:%M"), sys.version.split()[0]))
    ps = S._psspy()
    if ps is None:
        P("psspy NOT available -- run this with the PSS/E Python")
    else:
        sav, dyr = S.case_files("base")
        P("case: %s  exists=%s" % (sav, os.path.isfile(sav or "")))
        P("dyr : %s  exists=%s" % (dyr, os.path.isfile(dyr or "")))
        P("case() ierr=%s" % ps.case(sav))
        P("\n-- array calls (ierr 0 = the name works) --")
        for n in ("NUMBER", "AREA"):
            try_call("abusint %s" % n, ps.abusint, -1, 2, n)
        for n in ("BASE", "PU"):
            try_call("abusreal %s" % n, ps.abusreal, -1, 2, n)
        try_call("abuschar NAME", ps.abuschar, -1, 2, "NAME")
        for fl in (1, 3):
            try_call("abrnint flag=%d FROMNUMBER" % fl, ps.abrnint, -1, 1, 1, fl, 1, "FROMNUMBER")
        try_call("abrnint flag=3 count", lambda *a: (0, [[len(ps.abrnint(*a)[1][0])]]), -1, 1, 1, 3, 1, "FROMNUMBER")
        try_call("abrnint flag=1 count", lambda *a: (0, [[len(ps.abrnint(*a)[1][0])]]), -1, 1, 1, 1, 1, "FROMNUMBER")
        try_call("atrnint flag=1 count (2-winding)", lambda *a: (0, [[len(ps.atrnint(*a)[1][0])]]), -1, 1, 1, 1, 1, "FROMNUMBER")
        try_call("abrncplx RX", ps.abrncplx, -1, 1, 1, 3, 1, "RX")
        for n in ("CHARGING", "CHARGINGZERO", "LENGTH"):
            try_call("abrnreal %s" % n, ps.abrnreal, -1, 1, 1, 3, 1, n)
        try_call("abrnchar ID", ps.abrnchar, -1, 1, 1, 3, 1, "ID")
        try_call("atr3int WIND1NUMBER", ps.atr3int, -1, 1, 1, 1, 1, "WIND1NUMBER")
        for n in ("NUMBER", "STATUS", "WMOD"):
            try_call("amachint %s" % n, ps.amachint, -1, 4, n)
        for n in ("PGEN", "QGEN", "QMAX", "QMIN", "MBASE"):
            try_call("amachreal %s" % n, ps.amachreal, -1, 4, n)
        try_call("amachchar ID", ps.amachchar, -1, 4, "ID")
        for n in ("NUMBER", "STATUS"):
            try_call("afxshuntint %s" % n, ps.afxshuntint, -1, 4, n)
        for n in ("SHUNTNOM", "SHUNTACT"):
            try_call("afxshuntcplx %s" % n, ps.afxshuntcplx, -1, 4, n)
        for n in ("NUMBER", "STATUS", "MODE"):
            try_call("aswshint %s" % n, ps.aswshint, -1, 4, n)
        for n in ("BSWNOM", "BSWACT", "BSWMAX", "BSWMIN", "BINIT", "VSWHI", "VSWLO"):
            try_call("aswshreal %s" % n, ps.aswshreal, -1, 4, n)
        for n in ("SENDNUMBER", "STATUS"):
            try_call("afactsint %s" % n, ps.afactsint, -1, 4, n)

        P("\n-- what z5_spike_find sees around bus %s --" % BUS)
        try:
            net = S.load_network(ps, sav, dyr)
            P("  bus record: %s" % net["bus"].get(BUS))
            P("  neighbours: %s" % [(m, round(z, 4), w) for m, z, w in net["adj"].get(BUS, [])][:12])
            reach = S.nearby(net, BUS, S.GEN_HOPS)
            n = 0
            for b, h, z in reach:
                for d in net["dev"].get(b, []):
                    if n < 40:
                        P("  hops %d |Z| %.4f  %s" % (h, z, dict((k, d[k]) for k in d if k != "models")))
                        if d.get("models"):
                            P("        models %s" % d["models"])
                    n += 1
            P("  %d device(s) within %d buses" % (n, S.GEN_HOPS))
        except Exception as e:
            P("  load_network RAISED %s: %s" % (type(e).__name__, e))
        # fixed-shunt sign check at a known capacitor, if any
        try:
            ierr, (num,) = ps.afxshuntint(-1, 4, "NUMBER")
            ierr2, (nom,) = ps.afxshuntcplx(-1, 4, "SHUNTNOM")
            P("\n  first 5 fixed shunts (bus, SHUNTNOM): %s" % list(zip(num, nom))[:5])
            P("  -> compare with the case: a CAPACITOR should show + Mvar here")
        except Exception as e:
            P("  fixed-shunt check RAISED %s: %s" % (type(e).__name__, e))

    P("\n-- one .out --")
    path = OUT
    if not path:
        c = sorted(glob.glob(os.path.join(HERE, "Base", "results_base", "*_spp", "outs", "*.out")))
        c += sorted(glob.glob(os.path.join(HERE, "Base", "results", "*_spp", "outs", "*.out")))
        path = c[0] if c else ""
    P("out: %s" % path)
    dyn = S._dyntools()
    if not dyn or not path:
        P("  dyntools or .out not available")
    else:
        t0 = time.time()
        try:
            ch = dyn.CHNF(path)
            P("  CHNF() %.1f s" % (time.time() - t0))
            sh, ids = ch.get_id()
            P("  get_id() %.1f s, %d channels" % (time.time() - t0, len(ids) - 1))
            kinds = {}
            for k, ttl in ids.items():
                if k == "time":
                    continue
                kd = S._chan_kind(ttl) or "other"
                kinds.setdefault(kd, []).append(ttl)
            for kd in sorted(kinds):
                P("  %-6s %5d  e.g. %s" % (kd, len(kinds[kd]), [str(x).strip() for x in kinds[kd][:4]]))
            q = kinds.get("Q", [])[:3]
            P("  machine keys: %s" % [(str(x).strip(), S._mach_key(x)) for x in q])
            sel = [k for k, ttl in ids.items() if k != "time" and S._chan_kind(ttl) in ("FLT",)][:2]
            sel += [k for k, ttl in ids.items() if k != "time" and S._chan_kind(ttl) == "Q"][:2]
            t1 = time.time()
            try:
                r = ch.get_data(sel)
                P("  get_data(subset of %d) OK in %.1f s, %d samples" % (len(sel), time.time() - t1, len(r[2]["time"])))
                for k in sel:
                    v = r[2][k]
                    P("    %s: first %.4f  min %.4f  max %.4f" % (str(r[1][k]).strip(), v[0], min(v), max(v)))
            except Exception as e:
                P("  get_data(subset) RAISED %s: %s -- the script falls back to reading everything" % (type(e).__name__, e))
        except Exception as e:
            P("  .out read RAISED %s: %s" % (type(e).__name__, e))

    out = os.path.join(HERE, "PROBE_PSSE.txt")
    with open(out, "w") as f:
        f.write("\n".join(LOG) + "\n")
    print("\nwritten: %s -- send this file" % out)


if __name__ == "__main__":
    main()
