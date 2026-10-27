# -*- coding: utf-8 -*-
"""The ESXi hosts' VMkernel networks (roles/vmware_vm/library/site_vmware_hostnet.py) -> the ESXi network check report
(playbooks/esxi_network_check.yml): esxi_net_doc.

  a full-size ping (don't fragment) that does not get through while a small one does     critical (an MTU on the way)
  no answer at all between two hosts' vMotion (or other) adapters                         critical
  a ping round trip from opts.rtt_crit_ms (150: vMotion's limit)                          critical
  a datastore's average read or write latency from opts.latency_crit_ms (50)              critical
  some pings lost; a round trip from opts.rtt_warn_ms (5); latency from opts.latency_warn_ms (20)   warning
  the hosts of a cluster on different MTUs for one service; a vmk's MTU above its switch's       warning
  a host of a cluster without the service the others have; a cluster with none                   warning
  a ping that could not run (esxcli through vCenter: a privilege)                                 warning
Read-only: what to change is in each problem's fix."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

RANK = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
LABEL = {"vmotion": "vMotion", "vsan": "vSAN", "management": "management", "vSphereProvisioning": "provisioning",
         "faultToleranceLogging": "FT logging", "vSphereReplication": "replication", "target": "target"}


def _o(opts):
    o = dict({"rtt_warn_ms": 5, "rtt_crit_ms": 150, "latency_warn_ms": 20, "latency_crit_ms": 50, "services": ["vmotion"],
              "max_rows": 1000}, **(opts or {}))
    for k in ("rtt_warn_ms", "rtt_crit_ms", "latency_warn_ms", "latency_crit_ms"):
        o[k] = float(o[k])
    o["max_rows"] = int(o["max_rows"])
    return o


def _svc(s):
    return LABEL.get(s, s)


def _short(name):
    n = str(name or "")
    return n if n.replace(".", "").isdigit() else n.split(".")[0]


def _net(ip, mask):
    try:
        a = [int(x) for x in str(ip).split(".")]
        m = [int(x) for x in str(mask).split(".")]
        if len(a) != 4 or len(m) != 4:
            return ""
        return ".".join(str(x & y) for x, y in zip(a, m))
    except ValueError:
        return ""


def _ms(v):
    return "-" if v is None else "%g" % round(float(v), 2)


def ping_status(q, o):
    """(status, what) for one ping."""
    if q.get("error"):
        return "warning", "could not ping: %s" % q["error"]
    got, sent = q.get("received") or 0, q.get("sent") or 0
    small = q.get("small") or {}
    if not got:
        if q.get("df") and small.get("received"):
            return "critical", "full-size pings do not get through, small ones do: an MTU smaller than the adapter's on the way"
        return "critical", "no answer"
    avg = q.get("rtt_avg_ms")
    if avg is not None and avg >= o["rtt_crit_ms"]:
        return "critical", "round trip %s ms (from %g ms is too slow)" % (_ms(avg), o["rtt_crit_ms"])
    if got < sent:
        return "warning", "%d of %d pings lost" % (sent - got, sent)
    if avg is not None and avg >= o["rtt_warn_ms"]:
        return "warning", "round trip %s ms (from %g ms: slow for vMotion within a site)" % (_ms(avg), o["rtt_warn_ms"])
    return "ok", ""


def _ping_fix(q, what):
    if "MTU" in what:
        return ("Find the hop with the smaller MTU: the vSwitch or distributed switch, the uplinks, the physical switch ports and "
                "any router between - all must take %d-byte frames." % (int(q.get("size", 1472)) + 28))
    if what == "no answer":
        return ("Check the address, VLAN and port group of both adapters, the physical uplinks, and the network stack (%s) - a "
                "routed network needs its gateway." % (q.get("netstack") or "default"))
    if what.startswith("could not ping"):
        return ("The account needs Host > CIM > CIM interaction for esxcli through vCenter." if "privilege" in what
                or "permission" in what else "The host's own answer is in the table below; vCenter must be able to reach the host.")
    if "lost" in what:
        return "Errors or drops on the uplinks or switch ports (the VM health report's network drops), a busy link."
    return "A long or busy path: vMotion within a site expects well under a millisecond."


def _problems(read, o):
    """[(severity, vcenter, where, problem, fix)]"""
    out = []
    for r in read or []:
        vc = r.get("vcenter", "")
        if r.get("note"):
            out.append(("warning", vc, "vCenter " + _short(vc), r["note"],
                        "Give the VMware vCenter credential's account Host > CIM > CIM interaction on the hosts (a role with it, on "
                        "the datacenter or the clusters): esxcli runs through vCenter with it." if "privilege" in r["note"]
                        or "permission" in r["note"] else "The job's log has the whole message."))
        hosts = r.get("hosts") or []
        up = [h for h in hosts if h.get("state") == "ok"]
        for h in hosts:
            if h.get("error"):
                out.append(("warning", vc, h["name"], h["error"], "The account needs Read-only on the host at least."))
        for svc in o["services"]:
            for cl in sorted(set(h.get("cluster") for h in up if h.get("cluster"))):
                members = [h for h in up if h.get("cluster") == cl]
                have = [(h, v) for h in members for v in h.get("vmks") or [] if svc in (v.get("services") or [])]
                with_svc = sorted(set(h["name"] for h, _ in have))
                if len(members) < 2:
                    continue
                if not have:
                    out.append(("warning", vc, "cluster " + cl, "no host has a VMkernel adapter with %s on" % _svc(svc),
                                "vSphere Client > each host > Configure > VMkernel adapters > the adapter > Edit: tick %s (or add an "
                                "adapter for it); with none, %s between these hosts cannot run." % (_svc(svc), _svc(svc))))
                    continue
                for h in members:
                    if h["name"] not in with_svc:
                        out.append(("warning", vc, h["name"], "no VMkernel adapter with %s on, while %d other host(s) of cluster %s "
                                    "have one" % (_svc(svc), len(with_svc), cl),
                                    "Tick %s on its adapter for that network, as on the others." % _svc(svc)))
                mtus = {}
                for h, v in have:
                    mtus.setdefault(v.get("mtu"), []).append("%s %s" % (_short(h["name"]), v["device"]))
                if len(mtus) > 1:
                    out.append(("warning", vc, "cluster " + cl, "the %s adapters are on different MTUs: %s" % (
                        _svc(svc), "; ".join("%s on %s" % (m, ", ".join(sorted(x))) for m, x in sorted(mtus.items(), key=lambda i: str(i[0])))),
                        "Set one MTU on all of them (and on their switches, uplinks and the physical switch ports): 9000 for "
                        "jumbo frames end to end, else 1500."))
                nets = set(_net(v.get("ip"), v.get("mask")) for _, v in have if v.get("ip"))
                if len(nets) > 1:
                    out.append(("info", vc, "cluster " + cl, "the %s adapters are on %d different subnets (%s)" % (
                        _svc(svc), len(nets), ", ".join(sorted(nets))),
                        "Fine when the network is routed on purpose: then their network stack (vMotion's) needs its gateway. "
                        "Otherwise an address or mask is wrong."))
        for h in up:
            for v in h.get("vmks") or []:
                if v.get("switch_mtu") and v.get("mtu") and int(v["mtu"]) > int(v["switch_mtu"]):
                    out.append(("warning", vc, h["name"], "%s has MTU %s, its switch %s only %s" % (
                        v["device"], v["mtu"], v.get("switch"), v["switch_mtu"]),
                        "Raise the switch's MTU to the adapter's (vSphere Client > the host > Configure > Virtual switches > Edit), "
                        "or lower the adapter's."))
        # one row per kind of failure: a host in every failed ping of a cluster's service is named once (it is the one)
        groups = {}
        for q in r.get("pings") or []:
            st, what = ping_status(q, o)
            if st == "ok":
                continue
            kind = re.sub(r"[\d.]+ ms", "", what) if "round trip" in what or "lost" in what else what
            groups.setdefault((st, q.get("cluster", ""), q.get("service"), kind), []).append((q, what))
        for (st, cl, svc, kind), qs in groups.items():
            q0, what0 = qs[0]
            fix = _ping_fix(q0, what0)
            label = "%s ping (%s bytes%s)" % (_svc(svc), q0.get("size"), ", don't fragment" if q0.get("df") else "")
            if svc == "target":
                tos = sorted(set(q.get("to") for q, _ in qs))
                for to in tos:
                    mine = [q for q, _ in qs if q.get("to") == to]
                    out.append((st, vc, "%s from %d host(s): %s" % (to, len(mine), ", ".join(_short(q.get("from")) for q in mine)),
                                "%s: %s" % (label, what0 if len(mine) == 1 else kind), fix))
                continue
            every = [h for h in set(x for q, _ in qs for x in (q.get("from"), q.get("to")))
                     if all(h in (q.get("from"), q.get("to")) for q, _ in qs)]
            if len(qs) > 1 and len(every) == 1:
                h = every[0]
                peers = sorted(set(_short(x) for q, _ in qs for x in (q.get("from"), q.get("to")) if x != h))
                out.append((st, vc, h, "%s to and from it fail with the other hosts of %s (%s; %d pings): %s" % (
                    label, cl, ", ".join(peers), len(qs), kind), fix))
            else:
                for q, what in qs:
                    out.append((st, vc, "%s %s -> %s (%s)" % (q.get("from"), q.get("vmk"), q.get("to"), q.get("to_ip")),
                                "%s: %s" % (label, what), fix))
        # a datastore slow from several hosts: one row (the array, or its paths); from one host: that host
        slow = {}
        for d in r.get("latency") or []:
            worst = max([x for x in (d.get("read_avg_ms"), d.get("write_avg_ms")) if x is not None] or [0])
            if worst >= o["latency_warn_ms"]:
                slow.setdefault(d.get("datastore"), []).append((worst, d))
        for ds, ds_rows in sorted(slow.items()):
            worst = max(w for w, _ in ds_rows)
            st = "critical" if worst >= o["latency_crit_ms"] else "warning"
            if len(ds_rows) == 1:
                d = ds_rows[0][1]
                out.append((st, vc, "%s / %s" % (d.get("host"), ds),
                            "storage latency %s ms average (read %s, write %s; at most %s / %s)" % (
                                _ms(worst), _ms(d.get("read_avg_ms")), _ms(d.get("write_avg_ms")), _ms(d.get("read_max_ms")),
                                _ms(d.get("write_max_ms"))),
                            "Only this host sees it here: its HBA or uplinks, its paths to the array (all active?), its queue depth."))
            else:
                lo = min(w for w, _ in ds_rows)
                out.append((st, vc, "%s, from %d host(s)" % (ds, len(ds_rows)),
                            "storage latency %s to %s ms average (%s)" % (_ms(lo), _ms(worst), ", ".join(
                                sorted(_short(d.get("host")) for _, d in ds_rows))),
                            "Every host sees it: the array's own latency and load, or the shared paths and switches to it."))
    out.sort(key=lambda p: (RANK.get(p[0], 9), p[1], p[2]))
    return out


def esxi_net_doc(net, opts=None):
    """The report: {title, status, subtitle, summary, sections}."""
    o = _o(opts)
    read = (net or {}).get("read") or []
    ps = _problems(read, o)
    bad = [s for s in (net or {}).get("vcenters") or [] if s.get("error")]
    multi = len(read) > 1
    sections = []
    if bad:
        sections.append({"title": "vCenters not read", "status": "critical", "lines": [s.get("error") for s in bad]})
    real = [p for p in ps if p[0] in ("critical", "warning")]
    if ps:
        sections.append({"title": "Needs attention" if real else "Notes", "status": "critical" if any(p[0] == "critical" for p in ps)
                         else ("warning" if real else "info"),
                         "columns": ["Status", "Where"] + (["vCenter"] if multi else []) + ["Problem", "Fix"],
                         "rows": [[p[0].upper(), p[2]] + ([_short(p[1])] if multi else []) + [p[3], p[4]] for p in ps],
                         "row_status": [p[0] for p in ps]})
    rows, rs = [], []
    hosts_ok = 0
    for r in read:
        for h in r.get("hosts") or []:
            if h.get("state") != "ok":
                continue
            hosts_ok += 1
            for v in h.get("vmks") or []:
                st = "warning" if v.get("switch_mtu") and v.get("mtu") and int(v["mtu"]) > int(v["switch_mtu"]) else "ok"
                rows.append([_short(h["name"]), h.get("cluster") or "-", v["device"], ", ".join(_svc(s) for s in v.get("services") or []) or "-",
                             v.get("ip") or "-", v.get("mtu") or "-", "%s (%s)" % (v.get("switch") or "-", v.get("switch_mtu") or "?")
                             if v.get("switch") else "-", v.get("netstack") or "-"])
                rs.append(st)
    if rows:
        sections.append({"title": "VMkernel adapters", "columns": ["Host", "Cluster", "vmk", "Services", "IP address", "MTU",
                                                                   "Switch (MTU)", "Network stack"],
                         "rows": rows[:o["max_rows"]], "row_status": rs[:o["max_rows"]]})
    pings = [q for r in read for q in r.get("pings") or []]
    by = {}
    for q in pings:
        by.setdefault("target" if q.get("service") == "target" else q.get("service"), []).append(q)
    for svc in [s for s in o["services"] if s in by] + (["target"] if "target" in by else []):
        rows, rs = [], []
        for q in sorted(by[svc], key=lambda q: (RANK.get(ping_status(q, o)[0], 9), q.get("cluster", ""), q.get("from", ""), q.get("to", ""))):
            st, what = ping_status(q, o)
            small = q.get("small") or {}
            rows.append([st.upper(), "%s %s" % (_short(q.get("from")), q.get("vmk")),
                         q.get("to") if svc == "target" else "%s (%s)" % (_short(q.get("to")), q.get("to_ip")),
                         "%s%s" % (q.get("size"), " DF" if q.get("df") else ""),
                         "-" if q.get("error") else "%s/%s" % (q.get("received"), q.get("sent")), _ms(q.get("rtt_avg_ms")),
                         _ms(q.get("rtt_max_ms")), ("small: %s/%s" % (small.get("received"), small.get("sent")) if small else "") or what or "-"])
            rs.append(st)
        title = "Pings to other targets" if svc == "target" else "%s pings between the hosts of each cluster" % _svc(svc)
        sections.append({"title": title, "columns": ["Status", "From", "To", "Bytes", "Answered", "Avg ms", "Max ms", "Note"],
                         "rows": rows[:o["max_rows"]], "row_status": rs[:o["max_rows"]],
                         "text": ("Full size (the adapter's MTU less 28 bytes) with don't fragment; when that fails, a small ping too - "
                                  "it tells a broken link from an MTU too small on the way.") if svc != "target" else ""})
    for svc in o["services"]:
        if svc not in by and any(r.get("hosts") for r in read):
            sections.append({"title": "%s pings between the hosts of each cluster" % _svc(svc),
                             "lines": ["None: no two connected hosts of a cluster have an adapter with %s on (Needs attention says "
                                       "which)." % _svc(svc)]})
    cut = sum(r.get("cut", 0) for r in read)
    if cut:
        sections.append({"title": "Not pinged", "status": "info",
                         "lines": ["%d ping(s) not run: more than vm_hostnet_max_pairs per vCenter - narrow it with vm_hostnet_clusters, "
                                   "or raise it." % cut]})
    lat = sorted([d for r in read for d in r.get("latency") or []],
                 key=lambda d: -max([x for x in (d.get("read_avg_ms"), d.get("write_avg_ms")) if x is not None] or [0]))
    if lat:
        rows, rs = [], []
        for d in lat[:o["max_rows"]]:
            worst = max([x for x in (d.get("read_avg_ms"), d.get("write_avg_ms")) if x is not None] or [0])
            st = "critical" if worst >= o["latency_crit_ms"] else ("warning" if worst >= o["latency_warn_ms"] else "ok")
            rows.append([st.upper(), _short(d.get("host")), d.get("datastore"), _ms(d.get("read_avg_ms")), _ms(d.get("read_max_ms")),
                         _ms(d.get("write_avg_ms")), _ms(d.get("write_max_ms"))])
            rs.append(st)
        sections.append({"title": "Storage latency, the worst first", "columns": ["Status", "Host", "Datastore", "Read avg ms",
                                                                                   "Read max ms", "Write avg ms", "Write max ms"],
                         "rows": rows, "row_status": rs,
                         "text": "From the hosts' real-time statistics: the average and the highest 20-second sample. A warning from "
                                 "%g ms average, critical from %g." % (o["latency_warn_ms"], o["latency_crit_ms"])})
    skipped = [(r.get("vcenter"), h) for r in read for h in r.get("hosts") or [] if h.get("state") != "ok"]
    if skipped:
        sections.append({"title": "Hosts not checked", "status": "info",
                         "lines": ["%s (%s): %s" % (h["name"], h.get("cluster") or "standalone", h["state"]) for _, h in skipped]})
    n_fail = len([q for q in pings if ping_status(q, o)[0] != "ok"])
    slow = len(set(d.get("datastore") for d in lat
                   if max([x for x in (d.get("read_avg_ms"), d.get("write_avg_ms")) if x is not None] or [0]) >= o["latency_warn_ms"]))
    crit = len([p for p in ps if p[0] == "critical"])
    warn = len([p for p in ps if p[0] == "warning"])
    status = "critical" if crit or bad else ("warning" if warn else "ok")
    avgs = [q["rtt_avg_ms"] for q in pings if q.get("rtt_avg_ms") is not None and q.get("service") != "target"]
    if crit or warn:
        title = "ESXi network check: %d problem(s)%s" % (crit + warn, " (%d critical)" % crit if crit else "")
    else:
        title = "ESXi network check: %s" % ("all %d pings through" % len(pings) if pings else "no pings run")
    return {"title": title, "status": status,
            "subtitle": "%d host(s): their VMkernel adapters, vmkping between the hosts of each cluster (%s), storage latency" % (
                hosts_ok, ", ".join(_svc(s) for s in o["services"])),
            "summary": [{"label": "Hosts checked", "value": hosts_ok}, {"label": "Pings", "value": len(pings)},
                        {"label": "Pings with a problem", "value": n_fail, "status": "critical" if any(
                            ping_status(q, o)[0] == "critical" for q in pings) else ("warning" if n_fail else "ok")},
                        {"label": "Worst round trip (ms)", "value": _ms(max(avgs)) if avgs else "-"},
                        {"label": "Slow datastores", "value": slow, "status": "warning" if slow else "ok"}],
            "sections": sections}


class FilterModule(object):
    def filters(self):
        return {"esxi_net_doc": esxi_net_doc}
