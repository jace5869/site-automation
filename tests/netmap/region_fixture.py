#!/usr/bin/env python3
"""1.102.0: a network map's page data with every site placed in one region - a hub (HUBA0001) with a WAN link to each site,
some links between sites, some down, warning, not read and one down while its twin is not read (the 1.95.1 review) - and
the AD layer (plugins/module_utils/site_ad_layer.py) from a made-up forest whose AD sites match by the DCs' nodes, the code,
the name, ad_site_map and one not at all. Placeholders only (SITA0101 ..., corp.example.mil, 192.0.2.x).
  region(n_sites, seed, ad=True, lon=(-121, -73), lat=(28, 47)) -> page data (sites, geo, nodes, links, ad)"""
import datetime
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "plugins", "module_utils"))
import site_ad_layer as al  # noqa: E402

NOW = datetime.datetime(2026, 11, 21, 9, 0)
SPEEDS = ["100 Mbps", "1 Gbps", "1 Gbps", "10 Gbps", "200 Mbps"]


def iso(h):
    return (NOW - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def region(n_sites=40, seed=5, ad=True, lon=(-121, -73), lat=(28, 47), hub_at=(38.9, -77.0)):
    r = random.Random(seed)
    letters = "ABCDEFGHJKLMNPRSTVWXYZ"
    names = ["HUBA0001"] + ["SIT%s%04d" % (letters[i % len(letters)], 101 + i // len(letters) * 100 + i % 7) for i in range(n_sites)]
    geo = {"HUBA0001": list(hub_at)}
    for s in names[1:]:
        geo[s] = [round(r.uniform(*lat), 1), round(r.uniform(*lon), 1)]
    nodes, links, nid = [], [], [1000]
    rt = {}

    def node(site, nm, role, status="Up"):
        nid[0] += 1
        nodes.append({"id": nid[0], "name": "%s-%s" % (site, nm), "site": site, "building": "B01", "role": role, "status": status,
                      "ip": "192.0.2.%d" % (nid[0] % 250 + 1)})
        return nid[0]
    for i, s in enumerate(names):
        rt[s] = node(s, "RT01", "router")
        n_dev = 30 if s == "HUBA0001" else r.choice([1, 2, 3, 5, 8, 14])
        for k in range(n_dev):
            st = "Up"
            if i % 9 == 4 and k == 0:
                st = "Down"
            elif i % 7 == 3 and k == 0:
                st = "Warning"
            node(s, "AS%02d" % (k + 1), "access", st)
        if i % 5 == 1 or s == "HUBA0001":
            node(s, "DC01", "server")                          # a DC that is a SolarWinds node (matched by the DCs' nodes)
    for i, s in enumerate(names[1:]):                         # each site to the hub
        st, sp = "Up", r.choice(SPEEDS)
        if i % 11 == 6:
            st = "Down"
        elif i % 13 == 9:
            st = "Warning"
        links.append({"a": rt[s], "b": rt["HUBA0001"], "status": st, "speed": sp, "ia": "Gi0/0/0", "ib": "Te1/0/%d" % (i + 1),
                      "in": r.randint(1, 96), "out": r.randint(1, 96), "wan": True})
    if len(names) < 9:
        return finish(names, geo, nodes, links, ad)
    s1, s2 = names[3], names[4]                               # one pair: a link down, its twin not read (the review's case)
    links.append({"a": rt[s1], "b": rt[s2], "status": "Down", "speed": "1 Gbps", "ia": "Gi0/0/1", "ib": "Gi0/0/1", "why": "the interface is down", "wan": True})
    links.append({"a": rt[s1], "b": rt[s2], "status": "Unknown", "speed": "1 Gbps", "ia": "Gi0/0/2", "ib": "Gi0/0/2", "wan": True})
    s3, s4 = names[6], names[7]                               # one pair not read at all
    links.append({"a": rt[s3], "b": rt[s4], "status": "Unknown", "speed": "100 Mbps", "ia": "Gi0/0/1", "ib": "Gi0/0/1", "wan": True})
    for i in range(8, min(len(names) - 1, 18), 2):                # a few more between sites
        links.append({"a": rt[names[i]], "b": rt[names[i + 1]], "status": "Up", "speed": "1 Gbps", "ia": "Gi0/0/3", "ib": "Gi0/0/3",
                      "in": r.randint(1, 60), "out": r.randint(1, 60), "wan": True})
    return finish(names, geo, nodes, links, ad)


def finish(names, geo, nodes, links, ad):
    data = {"run": "2026-11-21 10:00 CET", "source": "SolarWinds (test)", "view": "region", "sites": names, "geo": geo, "unplaced": [],
            "nodes": nodes, "links": links}
    if ad and len(names) > 6:
        data["ad"] = al.layer(forest(names), data, site_map={"Lab-Annex": names[-1]}, source="artifact", now=NOW + datetime.timedelta(hours=1))
    return data


def forest(names):
    """An AD forest's replication (compact form) over the region's sites: HQ (its DCs are the hub's nodes), a site by the
    same name, one by its code with a dash, one by the name in lower case, one set by ad_site_map, one not matched."""
    hq = names[0]
    dcs = [{"name": hq + "-DC01", "site": "HQ"}, {"name": "HQDC02", "site": "HQ"},
           {"name": names[1] + "-DC01", "site": "Branch-East"},           # names[1] has a DC node: matched by the DCs' nodes
           {"name": "SDC-" + names[2], "site": names[2]},                  # the same name
           {"name": "CDC-" + names[3], "site": names[3][:4] + "-" + names[3][4:]},   # the code
           {"name": "LDC-" + names[5], "site": names[5].lower()},          # the name, any case
           {"name": "LAB-DC1", "site": "Lab-Annex"},                       # ad_site_map
           {"name": "XDC-1", "site": "Branch-West"}]                       # nothing matches
    by = dict((d["name"], d["site"]) for d in dcs)
    ln = [("HQDC02", names[1] + "-DC01", 0.4, 0), (hq + "-DC01", "SDC-" + names[2], 9.1, 0), (hq + "-DC01", "CDC-" + names[3], 31, 3),
          (hq + "-DC01", "LDC-" + names[5], 4.6, 0), (hq + "-DC01", "LAB-DC1", 0.5, 0), ("SDC-" + names[2], "XDC-1", 2.0, 0),
          (names[1] + "-DC01", "SDC-" + names[2], 1.0, 0), (hq + "-DC01", "HQDC02", 0.1, 0)]
    links = [{"frm": a, "to": b, "nc": "corp.example.mil", "last_success": iso(h), "failures": f, "error": "8524" if f else ""} for a, b, h, f in ln]
    assert all(x["frm"] in by and x["to"] in by for x in links)
    return al.compact({"now": NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), "dcs": dcs, "links": links}, {"low_hours": 4, "warn_hours": 6, "crit_hours": 24})
