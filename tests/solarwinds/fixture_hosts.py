#!/usr/bin/env python3
"""The fake SolarWinds for 1.97.0's reports (tests/solarwinds/run_swreport_test.sh): tests/solarwinds/fixture_more.py's
fixture plus the servers a health check and troubleshoot run see - web01, db01, app02, app03 (example.mil), web01 linked
to an access switch's port with errors, their alerts, SAM applications, volumes and events, the port's events, and Log
Analyzer's entries (Orion.OLM.LogEntry). Placeholders only.

  fixture_hosts.py IN.json OUT.json

The times are around the fixture's now (fixture_more.py's)."""
import copy
import datetime
import json
import sys

src, out = sys.argv[1], sys.argv[2]
fx = json.load(open(src))
now = datetime.datetime.strptime(fx["now"], "%Y-%m-%dT%H:%M:%SZ")
iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")       # noqa: E731
ago = lambda m: iso(now - datetime.timedelta(minutes=m))        # noqa: E731
t = fx["tables"]
nodes = fx["nodes"]
tpl = next(n for n in nodes if n["Caption"].startswith("esx"))
switch = next(n for n in nodes if "acc" in n["Caption"])
servers = {}
for k, (name, ip, st) in enumerate((("web01", "192.0.2.201", 1), ("db01", "192.0.2.202", 1), ("app02", "192.0.2.203", 3),
                                    ("app03", "192.0.2.204", 1))):
    n = copy.deepcopy(tpl)
    n.update(NodeID=9001 + k, Caption=name, DNS="%s.example.mil" % name, IPAddress=ip, Status=st,
             StatusDescription={1: "Node status is Up.", 3: "Node status is Warning."}[st], Vendor="Dell Inc.", MachineType="PowerEdge R750",
             ResponseTime=2, PercentLoss=0.0, CPULoad=20 + k, PercentMemoryUsed=40 + k, LastSystemUpTimePollUtc=ago(2),
             ObjectSubType="SNMP", IOSVersion="")
    nodes.append(n)
    servers[name] = n
web = servers["web01"]
# web01's link: the switch's port Gi1/0/14 (errors this hour) and the server's own card
fx["interfaces"] += [
    {"InterfaceID": 990001, "NodeID": switch["NodeID"], "Name": "Gi1/0/14", "Caption": "Gi1/0/14", "Speed": 1000000000, "InPercentUtil": 71.0,
     "OutPercentUtil": 12.0, "Inbps": 710000000, "Outbps": 120000000, "Status": 1, "OperStatus": 1, "AdminStatus": 1, "InErrorsThisHour": 412,
     "OutErrorsThisHour": 0, "InDiscardsThisHour": 0, "OutDiscardsThisHour": 0, "InErrorsToday": 2300, "OutErrorsToday": 0},
    {"InterfaceID": 990002, "NodeID": web["NodeID"], "Name": "eno1", "Caption": "eno1", "Speed": 1000000000, "InPercentUtil": 12.0,
     "OutPercentUtil": 71.0, "Inbps": 120000000, "Outbps": 710000000, "Status": 1, "OperStatus": 1, "AdminStatus": 1, "InErrorsThisHour": 0,
     "OutErrorsThisHour": 0, "InDiscardsThisHour": 0, "OutDiscardsThisHour": 0, "InErrorsToday": 0, "OutErrorsToday": 0}]
for a, b, ia, ib in ((switch["NodeID"], web["NodeID"], 990001, 990002), (web["NodeID"], switch["NodeID"], 990002, 990001)):
    fx["connections"].append({"SrcNodeID": a, "SrcInterfaceID": ia, "DestNodeID": b, "DestInterfaceID": ib, "LayerType": "L2"})
# alerts: web01 (a volume, an application), app02 (response time)
t["Orion.AlertConfigurations"] += [{"AlertID": 106, "Name": "Volume /var over 95 %", "Severity": 2, "Enabled": True},
                                   {"AlertID": 107, "Name": "Application down: Apache HTTP Server", "Severity": 1, "Enabled": True},
                                   {"AlertID": 108, "Name": "High response time: HTTPS 443 over 2 s", "Severity": 1, "Enabled": True}]
for k, (n, aid, m) in enumerate(((web, 106, 38), (web, 107, 42), (servers["app02"], 108, 205))):
    t["Orion.AlertObjects"].append({"AlertObjectID": 7100 + k, "AlertID": aid, "EntityType": "Orion.Nodes", "EntityCaption": n["Caption"],
                                    "RelatedNodeId": n["NodeID"], "RelatedNodeCaption": n["Caption"],
                                    "EntityUri": "swis://orion/Orion/Orion.Nodes/NodeID=%d" % n["NodeID"]})
    t["Orion.AlertActive"].append({"AlertActiveID": 9100 + k, "AlertObjectID": 7100 + k, "TriggeredDateTime": ago(m),
                                   "TriggeredMessage": "alert on %s" % n["Caption"], "Acknowledged": False, "AcknowledgedBy": "never-read-a-person"})
# SAM: web01's Apache down (2 of 3 components), db01's MariaDB up
for k, (n, name, st, down) in enumerate(((web, "Apache HTTP Server", 2, 2), (web, "SSH", 1, 0), (servers["db01"], "MariaDB", 1, 0),
                                         (servers["app02"], "Tomcat", 1, 0), (servers["app03"], "Tomcat", 1, 0))):
    t["Orion.APM.Application"].append({"ApplicationID": 600 + k, "Name": name, "NodeID": n["NodeID"], "Status": st,
                                       "StatusDescription": {1: "Up", 2: "Down"}[st], "Unmanaged": False})
    for j in range(3):
        cs = 2 if j < down else 1
        t["Orion.APM.Component"].append({"ComponentID": 6000 + 10 * k + j, "ApplicationID": 600 + k,
                                         "Name": ["httpd process", "HTTPS port 443", "Response time"][j], "ComponentType": 8, "Status": cs,
                                         "StatusDescription": {1: "Up", 2: "Down"}[cs], "StatisticData": 0.0,
                                         "ComponentMessage": "" if cs == 1 else "not running"})
# volumes: web01's /var 96 %
for k, (n, cap, pct) in enumerate(((web, "/var", 96.0), (web, "/", 41.0), (servers["db01"], "/", 52.0))):
    size = 50.0 * 2 ** 30
    t["Orion.Volumes"].append({"VolumeID": 69000 + k, "NodeID": n["NodeID"], "Caption": cap, "VolumeType": "Fixed Disk", "VolumeSize": size,
                               "VolumeSpaceUsed": round(size * pct / 100.0), "VolumePercentUsed": pct, "Status": 1})
# events: web01's own, its port's (and another port's of the same switch, never shown)
ev = [(44, web, "N", web["NodeID"], 51, "Volume /var over 95 % on web01"), (48, web, "N", web["NodeID"], 51, "Apache down on web01"),
      (50, switch, "I", 990001, 10, "Gi1/0/14 is down"), (49, switch, "I", 990001, 11, "Gi1/0/14 is up"),
      (47, switch, "I", 50001, 10, "another port is down"), (300, web, "N", web["NodeID"], 1, "web01 is not responding"),
      (296, web, "N", web["NodeID"], 5, "web01 is up"), (60 * 30, web, "N", web["NodeID"], 1, "an event of yesterday")]
for k, (m, n, typ, oid, et, msg) in enumerate(ev):
    t["Orion.Events"].append({"EventID": 800000 + k, "EventTime": ago(m), "NetworkNode": n["NodeID"], "NetObjectType": typ, "NetObjectID": oid,
                              "EventType": et, "Message": msg, "Acknowledged": False})
# Log Analyzer: web01 41 errors and worse in 24 h (3 kinds), db01 3; a warning and an older error never counted
olm = []
for k in range(28):
    olm.append((web, 10 + k, "Error", "httpd[%d]: AH00052: child pid %d exit signal Segmentation fault (11)" % (4000 + k, 4000 + k)))
for k in range(9):
    olm.append((web, 30 + k, "Critical", "kernel: EXT4-fs warning (device dm-3): ext4_dx_add_entry: Directory index full"))
for k in range(4):
    olm.append((web, 50 + k, "Error", "systemd[1]: httpd.service: Main process exited, code=killed, status=11/SEGV"))
for k in range(3):
    olm.append((servers["db01"], 120 + k, "Error", "mariadbd[811]: Aborted connection %d to db: 'reports'" % (812 + k)))
olm += [(web, 5, "Warning", "a warning, never counted"), (web, 60 * 30, "Error", "an error of yesterday, never counted")]
t["Orion.OLM.LogEntry"] = [{"LogEntryID": 1 + k, "NodeID": n["NodeID"], "DateTime": ago(m), "Level": lv, "LogEntryLevelID": 3,
                            "Message": msg} for k, (n, m, lv, msg) in enumerate(olm)]
fx["modules"].append({"Name": "OLM", "DisplayName": "Log Analyzer", "Version": "2026.1"})
json.dump(fx, open(out, "w"))
print("servers", len(servers), "log entries", len(olm), "switch", switch["Caption"])
