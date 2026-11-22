#!/usr/bin/env python3
"""The fake SolarWinds Orion for tests/netmap/run_netmap_test.sh, tests/test_netmap.py and the portal's live map tests:
the entity and column names of SolarWinds' OrionSDK schema (Orion.Nodes, Orion.NPM.Interfaces, Orion.TopologyConnections,
Orion.WorldMap.Point, Orion.InstalledModule, Orion.Engines), the values made up - placeholders only.

  fixture.py OUT.json [NODES] [USER PASSWORD]

NODES (1500) nodes in nine sites, Site A to Site J (no Site I), plus a few nodes without a location:
  Site A, Site B, Site C   placed by SolarWinds' Worldwide Map (Orion.WorldMap.Point)
  Site D, Site E           by the custom properties Lat and Lon
  Site F, Site G, Site H   by netmap_sites (the test's settings)
  Site J                   nowhere: the Unplaced tray
Each site: two cores (the WAN), two firewalls, distribution switches, a wireless controller, access switches under the
distribution. Location is "Site X, building N, ..." (the site is the text before the first comma); the custom property
Site holds "SITE-X" too (netmap_site_by: Site). WAN links between the cores, one down (Site F - Site G), one busy (Site A -
Site H, 91 %). Some nodes down and warning, one unmanaged. Custom properties: Site, Lat, Lon, Region."""
import json
import os
import random
import sys

out = sys.argv[1]
total = int(sys.argv[2]) if len(sys.argv) > 2 else 1500
user, password = (sys.argv[3], sys.argv[4]) if len(sys.argv) > 4 else ("svc_swis_ro", os.environ.get("SWIS_FIXTURE_PASSWORD", 'ro&"<test>'))
rnd = random.Random(1850)
SITES = ["Site A", "Site B", "Site C", "Site D", "Site E", "Site F", "Site G", "Site H", "Site J"]
SHARE = [.2, .16, .13, .11, .1, .09, .08, .07, .06]
GEO = {"Site A": (39.4, -93.2), "Site B": (34.9, -112.8), "Site C": (49.6, 9.4), "Site D": (52.4, -1.1), "Site E": (36.3, 138.4),
       "Site F": (2.4, 102.3), "Site G": (-31.8, 147.6), "Site H": (-22.6, -47.8)}
MODELS = {"core": [("Cisco", "C9606R"), ("Juniper", "QFX10002")], "dist": [("Cisco", "C9500-48Y4C"), ("Aruba", "8360-48Y6C")],
          "fw": [("Palo Alto Networks", "PA-3430"), ("Fortinet", "FortiGate 600F")],
          "acc": [("Cisco", "C9300-48P"), ("Aruba", "6300M-48G"), ("Juniper", "EX4400-48P")],
          "wlc": [("Cisco", "C9800-40 Wireless Controller"), ("Aruba", "7240XM Mobility Controller")]}
nodes, ifaces, conns, points = [], [], [], []
nid, iid = [1000], [50000]


def node(site, role, i, location=True, status=1):
    nid[0] += 1
    v, m = rnd.choice(MODELS[role])
    code = site[-1].lower()
    name = {"core": "s%s-core%02d", "dist": "s%s-dist%02d", "fw": "s%s-fw%02d", "acc": "s%s-acc%03d", "wlc": "s%s-wlc%02d"}[role] % (code, i)
    n = {"NodeID": nid[0], "Caption": name, "DNS": name + ".corp.example.mil", "IPAddress": "198.18.%d.%d" % (SITES.index(site) * 16 + {"core": 0, "dist": 1, "fw": 2, "wlc": 3, "acc": 4}[role] + i // 250, i % 250 + 1),
         "Status": status, "StatusDescription": {1: "Node status is Up.", 2: "Node status is Down.", 3: "Node status is Warning.",
                                                 9: "Node is Unmanaged.", 14: "Node status is Critical."}.get(status, "Unknown"),
         "Vendor": v, "MachineType": m, "Location": ("%s, building %d, %s" % (site, 1 + i % 3, "core room" if role in ("core", "fw") else "floor %d IDF" % (1 + i % 4))) if location else "",
         "ResponseTime": rnd.randint(1, 30) if status not in (2, 9) else -1, "PercentLoss": 100.0 if status == 2 else 0.0, "UnManaged": status == 9,
         "Community": "never-read-public", "RWCommunity": "never-read-private",
         "cp": {"Site": site.upper().replace(" ", "-") if location else None, "Region": "Region %d" % (1 + SITES.index(site) // 3),
                "Lat": None, "Lon": None}}
    if site in ("Site D", "Site E") and location:
        n["cp"]["Lat"], n["cp"]["Lon"] = round(GEO[site][0] + rnd.uniform(-.05, .05), 4), round(GEO[site][1] + rnd.uniform(-.05, .05), 4)
    if site in ("Site A", "Site B", "Site C") and location and role in ("core", "dist"):
        points.append({"PointId": len(points) + 1, "Instance": "Orion.Nodes", "InstanceID": str(nid[0]), "NodeID": nid[0],
                       "Latitude": round(GEO[site][0] + rnd.uniform(-.02, .02), 5), "Longitude": round(GEO[site][1] + rnd.uniform(-.02, .02), 5)})
    nodes.append(n)
    return n


def iface(n, name, speed, pin=None, pout=None, oper=1, status=1):
    iid[0] += 1
    pin = rnd.uniform(1, 60) if pin is None else pin
    pout = rnd.uniform(1, 60) if pout is None else pout
    ifaces.append({"InterfaceID": iid[0], "NodeID": n["NodeID"], "Name": name, "Caption": name, "Speed": speed,
                   "InPercentUtil": round(pin, 2), "OutPercentUtil": round(pout, 2), "Inbps": round(speed * pin / 100.0),
                   "Outbps": round(speed * pout / 100.0), "Status": status, "OperStatus": oper})
    return iid[0]


def link(a, b, na, nb, speed, **kw):
    ia = iface(a, na, speed, kw.get("pin"), kw.get("pout"), kw.get("oper", 1), kw.get("st", 1))
    ib = iface(b, nb, speed, kw.get("pout"), kw.get("pin"), kw.get("oper", 1), kw.get("st", 1))
    conns.append({"SrcNodeID": a["NodeID"], "SrcInterfaceID": ia, "DestNodeID": b["NodeID"], "DestInterfaceID": ib, "LayerType": "L2"})
    if rnd.random() < .5:                  # NPM often lists a connection both ways: the map shows it once
        conns.append({"SrcNodeID": b["NodeID"], "SrcInterfaceID": ib, "DestNodeID": a["NodeID"], "DestInterfaceID": ia, "LayerType": "L2"})


G = 10 ** 9
cores = {}
for s, share in zip(SITES, SHARE):
    size = max(12, int(round(total * share)))
    c = [node(s, "core", i) for i in (1, 2)]
    cores[s] = c
    link(c[0], c[1], "Hu1/0/1", "Hu1/0/1", 100 * G)
    for k, f in enumerate([node(s, "fw", i) for i in (1, 2)]):
        link(c[k], f, "Te1/0/%d" % (10 + k), "ethernet1/%d" % (1 + k), 10 * G)
    nd = 2 + size // 60
    dist = [node(s, "dist", i) for i in range(1, nd + 1)]
    for i, d in enumerate(dist):
        link(c[0], d, "Hu1/0/%d" % (5 + i), "Hu1/1/1", 100 * G)
        link(c[1], d, "Hu1/0/%d" % (5 + i), "Hu1/1/2", 100 * G)
    w = node(s, "wlc", 1)
    link(dist[0], w, "Te1/0/40", "Te0/0/0", 10 * G)
    for i in range(1, size - 5 - nd):
        r = rnd.random()
        st = 2 if (r < .01 and s in ("Site C", "Site F")) else 3 if (r < .03 and s in ("Site C", "Site E", "Site H", "Site J")) else 14 if (r < .035 and s == "Site E") else 1
        d = dist[i % nd]
        a = node(s, "acc", i, status=st)
        down = st == 2
        link(d, a, "Te1/0/%d" % (1 + i // nd), "Te1/1/1", 10 * G, oper=2 if down else 1, st=2 if down else 1)
nodes[-1]["Status"], nodes[-1]["UnManaged"], nodes[-1]["StatusDescription"] = 9, True, "Node is Unmanaged."
for i in range(3):                         # nodes nobody gave a location
    node("Site J", "acc", 900 + i, location=False)
odd = node("Site J", "acc", 950)               # a location that is a word of JavaScript's objects: a site like any other
odd["Location"] = "constructor, building 1"
WAN = [("Site A", "Site B", 10 * G, 41, 36), ("Site A", "Site D", 10 * G, 73, 41), ("Site D", "Site C", 10 * G, 58, 62),
       ("Site A", "Site C", G, 88, 79), ("Site C", "Site F", G, 64, 22), ("Site F", "Site E", G, 35, 47),
       ("Site F", "Site G", 500 * 10 ** 6, 0, 0, 2), ("Site E", "Site B", G, 22, 18), ("Site A", "Site H", 500 * 10 ** 6, 91, 67),
       ("Site H", "Site D", 100 * 10 ** 6, 12, 9), ("Site J", "Site A", G, 30, 20)]
for i, w in enumerate(WAN):
    a, b, sp, pin, pout = w[:5]
    oper = w[5] if len(w) > 5 else 1
    link(cores[a][0], cores[b][1], "Te2/0/%d" % (i + 1), "Te2/0/%d" % (i + 21), sp, pin=pin, pout=pout, oper=oper, st=2 if oper == 2 else 1)
json.dump({"user": user, "password": password, "nodes": nodes, "interfaces": ifaces, "connections": conns, "points": points,
           "modules": [{"Name": "Orion", "DisplayName": "Orion Platform", "Version": "2025.4.1"},
                       {"Name": "NPM", "DisplayName": "Network Performance Monitor", "Version": "2025.4.1"}],
           "engines": [{"ServerType": "Primary", "EngineVersion": "2025.4.1.1234", "LicenseKey": "never-read-license"}],
           "properties": ["Site", "Lat", "Lon", "Region"], "sites": dict((k, {"lat": v[0], "lon": v[1]}) for k, v in GEO.items())},
          open(out, "w"))
print("nodes", len(nodes), "interfaces", len(ifaces), "connections", len(conns), "points", len(points))
