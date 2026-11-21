#!/usr/bin/env python3
"""The fake SolarWinds for the snapshot's tests (1.96.0): the network map's fixture (tests/netmap/fixture.py) plus what
the snapshot reads besides - the nodes' poll, CPU and memory, their other IPs, the links' interface errors, active alerts,
events, SAM applications and components, volumes, VIM's clusters, hosts and VMs. Entity and column names from
SolarWinds' OrionSDK schema, the values made up (placeholders only).

  fixture_more.py IN.json OUT.json [NOW]

NOW (2026-11-21T09:00:00Z; default: the time now): the events are the 48 hours before it, one every 12 minutes.
Three ESXi hosts esx01..esx03.example.mil are nodes too (no links), and VIM knows them in the cluster cluster1."""
import datetime
import json
import random
import sys

src, out = sys.argv[1], sys.argv[2]
now = (datetime.datetime.strptime(sys.argv[3], "%Y-%m-%dT%H:%M:%SZ") if len(sys.argv) > 3
       else datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None))
fx = json.load(open(src))
rnd = random.Random(1960)
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")
nodes = fx["nodes"]
nid = max(n["NodeID"] for n in nodes)
for i in (1, 2, 3):                      # the ESXi hosts, monitored as nodes (no links)
    nid += 1
    nodes.append({"NodeID": nid, "Caption": "esx%02d" % i, "DNS": "esx%02d.example.mil" % i, "IPAddress": "192.0.2.%d" % (10 + i),
                  "Status": 2 if i == 2 else 1, "StatusDescription": "Node status is Down." if i == 2 else "Node status is Up.",
                  "Vendor": "VMware Inc.", "MachineType": "VMware ESXi 8.0.3", "Location": "Site A, building 1, server room",
                  "ResponseTime": -1 if i == 2 else 2, "PercentLoss": 100.0 if i == 2 else 0.0, "UnManaged": False,
                  "Community": "never-read-public", "RWCommunity": "never-read-private",
                  "cp": {"Site": "SITE-A", "Region": "Region 1", "Lat": None, "Lon": None}})
for n in nodes:
    up = n["Status"] not in (2, 9)
    n.update(CPULoad=rnd.randint(1, 95) if up else -2, PercentMemoryUsed=rnd.randint(10, 92) if up else -2,
             LastSystemUpTimePollUtc=iso(now - datetime.timedelta(minutes=rnd.randint(1, 9))) if up else iso(now - datetime.timedelta(hours=3)),
             LastBoot=iso(now - datetime.timedelta(days=rnd.randint(2, 300))), ObjectSubType="SNMP" if "esx" not in n["Caption"] else "ICMP",
             IOSVersion="17.9.4a" if n["Vendor"] == "Cisco" else "")
for i in fx["interfaces"]:
    i.update(AdminStatus=1, InErrorsThisHour=0, OutErrorsThisHour=0, InDiscardsThisHour=0, OutDiscardsThisHour=0,
             InErrorsToday=0, OutErrorsToday=0)
for i in fx["interfaces"][::97]:         # a few with errors
    i.update(InErrorsThisHour=rnd.randint(1, 400), OutDiscardsThisHour=rnd.randint(1, 90), InErrorsToday=rnd.randint(400, 9000))
by = dict((n["NodeID"], n) for n in nodes)
esx = [n for n in nodes if n["Caption"].startswith("esx")]
down = [n for n in nodes if n["Status"] == 2 and not n["Caption"].startswith("esx")]
t = {}
t["Orion.NodeIPAddresses"] = sorted([{"NodeID": n["NodeID"], "IPAddress": n["IPAddress"], "IPAddressType": "IPv4"} for n in nodes] +
                                    [{"NodeID": n["NodeID"], "IPAddress": "198.51.100.%d" % (k + 1), "IPAddressType": "IPv4"}
                                     for k, n in enumerate(nodes[:40:4])], key=lambda r: (r["NodeID"], r["IPAddress"]))
conf = [(101, "Node is down", 2), (102, "High CPU load", 1), (103, "Interface errors", 1), (104, "Volume almost full", 3),
        (105, "Application down", 2)]
t["Orion.AlertConfigurations"] = [{"AlertID": a, "Name": nm, "Severity": sv, "Enabled": True} for a, nm, sv in conf]
objs, act = [], []
on = [(down[0], 101), (down[1] if len(down) > 1 else down[0], 101), (esx[1], 101), (esx[0], 102), (nodes[3], 103), (nodes[5], 104), (esx[2], 105)]
for k, (n, aid) in enumerate(on):
    objs.append({"AlertObjectID": 7000 + k, "AlertID": aid, "EntityType": "Orion.Nodes", "EntityCaption": n["Caption"],
                 "RelatedNodeId": n["NodeID"], "RelatedNodeCaption": n["Caption"], "EntityUri": "swis://orion/Orion/Orion.Nodes/NodeID=%d" % n["NodeID"]})
    act.append({"AlertActiveID": 9000 + k, "AlertObjectID": 7000 + k, "TriggeredDateTime": iso(now - datetime.timedelta(minutes=30 + 17 * k)),
                "TriggeredMessage": "%s on %s" % (dict((c[0], c[1]) for c in conf)[aid], n["Caption"]), "Acknowledged": k == 3,
                "AcknowledgedBy": "never-read-a-person"})
t["Orion.AlertObjects"], t["Orion.AlertActive"] = objs, act
kinds = [(1, "Node Down"), (5, "Node Up"), (10, "Interface Down"), (11, "Interface Up"), (51, "Alert Triggered"), (52, "Alert Reset")]
t["Orion.EventTypes"] = [{"EventType": k, "Name": nm} for k, nm in kinds]
ev = []
for k in range(240):                    # 48 hours, one every 12 minutes, oldest first
    n = rnd.choice(nodes)
    et = rnd.choice(kinds)[0]
    ev.append({"EventID": 500000 + k, "EventTime": iso(now - datetime.timedelta(minutes=12 * (240 - k))), "NetworkNode": n["NodeID"],
               "NetObjectType": "I" if et in (10, 11) else "N", "NetObjectID": n["NodeID"], "EventType": et,
               "Message": "%s %s" % (n["Caption"], dict(kinds)[et].lower()), "Acknowledged": False})
t["Orion.Events"] = ev
apps, comps = [], []
servers = esx + nodes[:17]
for k, n in enumerate(servers):
    st = 2 if k in (2, 9) else 3 if k == 5 else 1
    apps.append({"ApplicationID": 300 + k, "Name": ["Web server", "Database", "Directory", "Hypervisor"][k % 4], "NodeID": n["NodeID"],
                 "Status": st, "StatusDescription": {1: "Up", 2: "Down", 3: "Warning"}[st], "Unmanaged": False})
    for j in range(9 if k == 9 else 3):     # application 309: 9 components, 2 of them down
        cs = st if j == 0 or (k == 9 and j == 4) else 1
        comps.append({"ComponentID": 3000 + 10 * k + j, "ApplicationID": 300 + k, "Name": "Component %d" % (j + 1),
                      "ComponentType": 8 if j else 3, "Status": cs, "StatusDescription": {1: "Up", 2: "Down", 3: "Warning"}[cs],
                      "StatisticData": float(10 * j), "ComponentMessage": "" if cs == 1 else "Port 443 refused the connection"})
t["Orion.APM.Application"], t["Orion.APM.Component"] = apps, comps
vols = []
for k, n in enumerate(nodes[:120]):
    for j, cap in enumerate(["/", "/var"]):
        size = float(rnd.choice([50, 100, 200]) * 2 ** 30)
        pct = 96.0 if (k, j) == (5, 1) else round(rnd.uniform(5, 80), 1)
        vols.append({"VolumeID": 40000 + 10 * k + j, "NodeID": n["NodeID"], "Caption": cap, "VolumeType": "Fixed Disk",
                     "VolumeSize": size, "VolumeSpaceUsed": round(size * pct / 100.0), "VolumePercentUsed": pct, "Status": 1})
t["Orion.Volumes"] = vols
t["Orion.VIM.Clusters"] = [{"ClusterID": 1, "Name": "cluster1"}]
t["Orion.VIM.Hosts"] = [{"HostID": 60 + k, "HostName": n["DNS"], "NodeID": n["NodeID"], "ClusterID": 1, "Status": n["Status"],
                         "ConnectionState": "notResponding" if n["Status"] == 2 else "connected", "PowerState": "poweredOn",
                         "CpuLoad": 0 if n["Status"] == 2 else rnd.randint(10, 70), "MemUsage": 0 if n["Status"] == 2 else rnd.randint(30, 80),
                         "VmCount": 12, "VmRunningCount": 0 if n["Status"] == 2 else 11, "VMwareProductName": "VMware ESXi"}
                        for k, n in enumerate(esx)]
t["Orion.VIM.VirtualMachines"] = [{"VirtualMachineID": 800 + k, "HostID": 60 + k % 3, "NodeID": None, "Name": "vm-app%02d" % k,
                                   "PowerState": "poweredOff" if k % 3 == 1 else "poweredOn", "Status": 1, "CPULoad": rnd.randint(0, 60),
                                   "MemUsage": rnd.randint(5, 70), "IPAddress": "192.0.2.%d" % (100 + k), "GuestState": "running"}
                                  for k in range(36)]
fx["tables"] = t
fx["modules"] = fx["modules"] + [{"Name": "APM", "DisplayName": "Server & Application Monitor", "Version": "2026.1"},
                                 {"Name": "VIM", "DisplayName": "Virtualization Manager", "Version": "2026.1"}]
fx["now"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
json.dump(fx, open(out, "w"))
print("nodes", len(nodes), "alerts", len(act), "events", len(ev), "apps", len(apps), "volumes", len(vols))
