"""tests/test_ad_map.py's map data: a realistic forest (made-up, placeholders only) - 25 locations, 40 AD sites with 2-4 DCs
each, 5 locations holding 2-6 AD sites (the main one the busiest), 51 connections; and every AD site in one place."""
import datetime
import random

now = datetime.datetime(2026, 10, 15, 9, 30)


def iso(h):
    return (now - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")


def realistic():
    """s3: ~25 locations, every AD site 2-4 DCs, 5 locations with 2-6 AD sites each (the main one the busiest)."""
    r = random.Random(11)
    locs = ["HUBA0001"] + ["SIT%s%04d" % (c, 101 + k) for k, c in enumerate("BCDEFGHJKLMNPRSTUVWXYZAB")][:24]
    places = {"HUBA0001": [38.9, -77.0]}
    for l in locs[1:]:
        places[l] = [round(r.uniform(29, 47), 1), round(r.uniform(-121, -72), 1)]
    shared = {"HUBA0001": 6, locs[3]: 4, locs[7]: 3, locs[12]: 2, locs[18]: 5}
    dcs, swdcs, node_site, nid, ad_sites = [], [], {}, 100, []
    for l in locs:
        n = shared.get(l, 1)
        for k in range(n):
            ad = ("HQ-%s" % ["MAIN", "B02", "B03", "LAB", "DMZ", "OPS"][k]) if l == "HUBA0001" else (l if n == 1 else "%s-%s" % (l[:4] + "-" + l[4:], "AB"[k % 2] + str(k + 1)))
            ad_sites.append((ad, l))
            for i in range(r.choice([2, 2, 3, 4]) if l != "HUBA0001" or k else 4):
                nid += 1
                name_ = "%s-DC%d" % (ad.replace("-", "").upper(), i + 1)
                dcs.append({"name": name_, "site": ad, "read": True}); swdcs.append({"dc": name_, "node_id": nid}); node_site[str(nid)] = l
    first = {}
    for d in dcs:
        first.setdefault(d["site"], [])
        first[d["site"]].append(d["name"])
    links = []
    def add(a, b, h):
        f = "1722 (the RPC server is unavailable)" if h > 24 else ""
        for nc in ("corp.test", "Configuration"):
            links.append({"to": first[b][0], "frm": first[a][0], "nc": nc, "last_success": iso(h), "failures": 2 if f else 0, "error": f,
                          "last_failure": iso(.1) if f else ""})
    main = [a for a, l in ad_sites if l == "HUBA0001"]
    for k, (a, l) in enumerate(ad_sites):
        if l == "HUBA0001":
            continue
        add("HQ-MAIN", a, r.choice([.2, .3, .5, .4, 5.0, 8.5, .3, .2, 30][k % 9:k % 9 + 1] or [.3]))
    for i in range(len(main)):
        add(main[i], main[(i + 1) % len(main)], [.1, .2, 6.5, .1, .3, .2][i])
    for l, n in shared.items():
        if l == "HUBA0001":
            continue
        mem = [a for a, x in ad_sites if x == l]
        for i in range(len(mem) - 1):
            add(mem[i], mem[i + 1], .2 if i else 4.6)
    for a, b in [(ad_sites[8][0], ad_sites[9][0]), (ad_sites[14][0], ad_sites[20][0])]:
        add(a, b, .6)
    wan = [{"a": "HUBA0001", "b": l, "n": 1, "bps": r.choice([1e7, 5e7, 1e8, 1e9]), "util": r.randint(3, 92), "status": "Up",
            "ifs": ["HUBA0001-RT01 Gi0/%d - %s-RT01 Gi0/0" % (k, l)]} for k, l in enumerate(locs[1:])]
    swis = {"dcs": swdcs, "netmap": {"ok": True, "source": "db-snapshot", "names": sorted(places), "places": places,
                                     "node_site": node_site, "wan": wan, "no_site": "Not placed"}}
    return {"dcs": dcs, "links": links, "now": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, swis




def one_place(n=8):
    """n AD sites, all on HUBA0001 (one place: the ring)."""
    dcs, swdcs, node_site, links = [], [], {}, []
    for i in range(n):
        s, dc = "HQ-B%02d" % (i + 1), "HQB%02d-DC1" % (i + 1)
        dcs.append({"name": dc, "site": s, "read": True}); swdcs.append({"dc": dc, "node_id": 200 + i}); node_site[str(200 + i)] = "HUBA0001"
    for i in range(n):
        links.append({"to": "HQB%02d-DC1" % (i + 1), "frm": "HQB%02d-DC1" % ((i + 1) % n + 1), "nc": "corp.test", "last_success": iso(.3), "failures": 0})
    swis = {"dcs": swdcs, "netmap": {"ok": True, "source": "db-snapshot", "names": ["HUBA0001"], "places": {"HUBA0001": [38.9, -77.0]},
                                     "node_site": node_site, "wan": [], "no_site": "Not placed"}}
    return {"dcs": dcs, "links": links, "now": now.strftime("%Y-%m-%dT%H:%M:%SZ")}, swis
