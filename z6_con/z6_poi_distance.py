# -*- coding: utf-8 -*-
"""z6_poi_distance.py -- how far each new IA build's POI is from each study POI.

Run with the PSS/E Python, from the study root (beside z6_main.py):

    C:\\Python34\\python.exe z6_poi_distance.py

Writes POI_DISTANCE.txt and POI_DISTANCE.csv in the same folder.
Reads only; changes nothing.

Two measures per pair:
  nodes  = fewest buses crossed (in-service lines + transformers; a 3-winding
           transformer counts as one step, as in the gen test's NODES group)
  |Z| pu = smallest summed branch |R+jX| along any path (100 MVA base) -- the
           ELECTRICAL distance; a short |Z| means strong interaction even when
           the node count is high.
"""
from __future__ import print_function
import os, sys, time, heapq
from collections import deque

# ---- SETTINGS -------------------------------------------------------------
CASE = "base"          # "base" | "proj" -- which .sav z6_main.py points at
STUDY_POIS = {         # as GEN_TEST_BY_PROJECT in z6_main.py
    "SantaFe": 765911,
    "IronStar": 560080,
    "EastFork": 531623,
    "EmpirePrairie": 761383,
}
# Table 5 IA builds: GEN number -> (MW, type, substation, [POI bus(es)]); a
# line tap lists every candidate bus and the nearest one is reported
NEW_BUILDS = [
    ("GEN-2023-173", 100, "Wind", "Holcomb 345 kV", [531449]),
    ("GEN-2023-172", 200, "Wind", "Holcomb 345 kV", [531449]),
    ("GEN-2023-171", 150, "Battery", "Sub M 161 kV", [548814]),
    ("GEN-2023-170", 150, "Battery", "Salisbury 161 kV", [543062]),
    ("GEN-2023-107", 300, "Wind", "Setab 345 kV", [531465]),
    ("GEN-2023-099", 300, "Solar", "Jeffery EC 345 kV", [532766]),
    ("GEN-2023-061", 100, "Battery", "Carthage 161 kV", [505488]),
    ("GEN-2023-037", 200, "Battery", "Nearman 161 kV", [546653]),
    ("GEN-2023-034", 130, "Solar", "Clear Water-Waco 138 kV", [533036, 533071, 533072, 533073]),
    ("GEN-2023-033", 200, "Battery", "Liberty South 161 kV", [541248]),
]
NEAR_NODES = 6         # flagged NEAR when within this many nodes (GEN_TEST_HOPS = 5)
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import z6_spike_find as S                                  # same PSS/E setup + network loader


def hops_from(net, src):
    """Fewest nodes from src to every reachable bus (BFS)."""
    d = {src: 0}
    q = deque([src])
    while q:
        n = q.popleft()
        for m, _z, _w in net["adj"].get(n, []):
            if m not in d:
                d[m] = d[n] + 1
                q.append(m)
    return d


def z_from(net, src):
    """Smallest summed |Z| (pu) from src to every reachable bus (Dijkstra)."""
    best = {src: 0.0}
    pq = [(0.0, src)]
    while pq:
        z, n = heapq.heappop(pq)
        if z > best.get(n, 1e18):
            continue
        for m, dz, _w in net["adj"].get(n, []):
            nz = z + dz
            if nz < best.get(m, 1e18):
                best[m] = nz
                heapq.heappush(pq, (nz, m))
    return best


def main():
    ps = S._psspy()
    if ps is None:
        print("psspy NOT available -- run this with the PSS/E Python")
        return 1
    sav, dyr = S.case_files(CASE)
    print("case: %s" % sav)
    net = S.load_network(ps, sav, dyr)
    B = net["bus"]

    def bname(b):
        i = B.get(b)
        return ("%s %.0f kV area %s" % (i["name"], i["kv"], i["area"])) if i else "NOT IN CASE"

    rows, lines = [], []
    lines.append("POI DISTANCE -- %s   case %s" % (time.strftime("%Y-%m-%d %H:%M"), os.path.basename(sav or "")))
    lines.append("nodes = fewest buses crossed | |Z| = smallest summed branch |R+jX| pu (100 MVA)")
    lines.append("NEAR = within %d nodes (the gen test's NODES reach)" % NEAR_NODES)
    for proj, poi in STUDY_POIS.items():
        lines.append("")
        lines.append("=" * 100)
        lines.append("%s  POI %d  %s" % (proj, poi, bname(poi)))
        lines.append("=" * 100)
        if poi not in B:
            lines.append("  POI bus not in this case -- try CASE = \"proj\"")
            continue
        H, Z = hops_from(net, poi), z_from(net, poi)
        res = []
        for gen, mw, typ, sub, buses in NEW_BUILDS:
            cand = [(H.get(b), Z.get(b), b) for b in buses if b in B]
            cand = [c for c in cand if c[0] is not None]
            if not cand:
                miss = [b for b in buses if b not in B]
                res.append((1e9, 1e9, gen, mw, typ, sub, buses[0],
                            "NOT IN CASE" if miss else "NOT CONNECTED"))
                continue
            h, z, b = min(cand, key=lambda c: (c[1], c[0]))
            res.append((h, z, gen, mw, typ, sub, b, "NEAR" if h <= NEAR_NODES else ""))
        res.sort(key=lambda r: (r[1], r[0]))
        lines.append("  %-13s %5s  %-8s %-24s %-8s %-34s %6s %9s  %s"
                     % ("GEN", "MW", "type", "substation", "POI bus", "bus name", "nodes", "|Z| pu", ""))
        for h, z, gen, mw, typ, sub, b, flag in res:
            hs = "-" if h >= 1e9 else "%d" % h
            zs = "-" if z >= 1e9 else "%.4f" % z
            lines.append("  %-13s %5d  %-8s %-24s %-8d %-34s %6s %9s  %s"
                         % (gen, mw, typ, sub[:24], b, bname(b)[:34], hs, zs, flag))
            rows.append((proj, poi, gen, mw, typ, sub, b, bname(b), hs, zs, flag))
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(HERE, "POI_DISTANCE.txt"), "w") as fh:
        fh.write(txt + "\n")
    with open(os.path.join(HERE, "POI_DISTANCE.csv"), "w") as fh:
        fh.write("project,study_poi,gen,mw,type,substation,poi_bus,bus_name,nodes,z_pu,flag\n")
        for r in rows:
            fh.write(",".join('"%s"' % x if "," in str(x) else str(x) for x in r) + "\n")
    print("\nwritten: POI_DISTANCE.txt / POI_DISTANCE.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
