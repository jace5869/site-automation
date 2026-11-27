#!/usr/bin/env python3
"""1.136.0: the wall's Ops monitor demo - a made-up SolarWinds (nodes, groups, a custom property ServiceGroup) and the
VMs vCenter has of the same names, for tests/test_wall_fleet.py, the page tests and the screenshots. Placeholders only (names
SITEA..., addresses from the documentation and benchmark ranges).

  fixture(scale)         the fake SWIS's fixture (tests/netmap/fake_swis.py reads it: nodes, properties, tables)
  vms(fx)                the VMs a vCenter read has for it (wall_live.shape's VM shape)
  view(now, fx)          tests/wall/demo.py's collector view with those VMs in vc01
  FakeReader(fx)         a wall_fleet.Reader stand-in: snapshot() as if SolarWinds had just been read

  python3 tests/wall/fleet_demo.py OUT.json [SCALE] [USER PASSWORD]     writes the fake SWIS's fixture"""
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# the mockup's services: name, how many nodes, the caption's prefix, digits, a VM (in vCenter) or not
GROUPS = [("Active Directory", 6, "SITEADIRDCV", 2, True), ("Certificate Services", 6, "SITEACERTV", 2, True),
          ("ServiceNow MID", 4, "SITESNOWCVM5", 1, True), ("VMware infrastructure", 26, "SITEAESX", 2, False),
          ("Storage", 24, "SITEANAS", 2, False), ("Databases", 18, "SITEASQLV", 2, True), ("File & print", 22, "SITEAFSV", 2, True),
          ("Network access", 96, "SITEASWACC", 3, False)]
WORKLOADS, VC_ONLY_N = 640, 37
PW = 'ro&"<test>9'


def ip(n):
    return "192.0.2.%d" % (n % 250 + 2) if n < 250 else "198.18.%d.%d" % (n // 250, n % 250 + 2)


def fixture(scale=1.0, user="svc_swis_ro", password=PW, now=None):
    """-> the fake SWIS's fixture: ~860 nodes (scale 1; 3.5 for ~3,000), 8 groups, ServiceGroup, the mockup's problems"""
    now = now or time.time()
    rnd = random.Random(1380)
    nodes, members, k, nid = [], [], 0, 2000
    sync = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(now - 40))

    def node(name, group, vm, gi):
        nonlocal k, nid
        nid += 1
        k += 1
        n = {"NodeID": nid, "Caption": name, "DNS": name.lower() + ".corp.example.mil", "IPAddress": ip(k), "Status": 1, "StatusDescription": "Node status is Up.",
             "Vendor": "VMware Inc." if vm else ("Cisco" if "SW" in name else "NetApp" if "NAS" in name else "Dell Inc."),
             "MachineType": "VMware Virtual Platform" if vm else ("Catalyst 9300" if "SW" in name else "AFF A400" if "NAS" in name else "PowerEdge R750"),
             "ResponseTime": rnd.randint(1, 9), "PercentLoss": 0, "UnManaged": False, "LastSync": sync, "Location": "SITEA",
             "cp": {"ServiceGroup": group}}
        nodes.append(n)
        if gi is not None:
            members.append({"ContainerID": 100 + gi, "MemberPrimaryID": nid, "MemberEntityType": "Orion.Nodes", "Name": name})
        return n
    by = {}
    for gi, (g, n, pre, w, vm) in enumerate(GROUPS):
        cnt = int(round(n * scale)) if n > 30 else n
        for i in range(cnt):
            name = pre + str(i if pre.endswith("5") else i + 1).zfill(w)
            by[name] = node(name, g, vm, gi)
    for i in range(int(round(WORKLOADS * scale))):
        name = "SITEAAPPV" + str(i + 1).zfill(4 if scale > 1.5 else 3)
        by[name] = node(name, "", True, None)

    def st(name, status, desc, **kw):
        by[name].update(Status=status, StatusDescription=desc, **kw)
    st("SITEADIRDCV01", 2, "Node status is Down.", PercentLoss=100, ResponseTime=-1)
    st("SITESNOWCVM50", 2, "Node status is Down.", PercentLoss=100, ResponseTime=-1)
    st("SITEASWACC007", 2, "Node status is Down.", PercentLoss=100, ResponseTime=-1)
    st("SITEASQLV05", 2, "Node status is Down.", PercentLoss=100, ResponseTime=-1)
    st("SITEACERTV02", 3, "Node status is Warning.", PercentLoss=8)
    st("SITEAFSV09", 3, "Node status is Warning.", ResponseTime=190)
    st("SITEANAS03", 3, "Node status is Warning.", ResponseTime=280)
    st("SITEASWACC031", 3, "Node status is Warning.", PercentLoss=22)
    apps = sorted(n for n in by if n.startswith("SITEAAPPV"))
    st(apps[3], 3, "Node status is Warning.", PercentLoss=12)
    st(apps[120], 3, "Node status is Warning.", ResponseTime=310)
    st(apps[300], 3, "Node status is Warning.", ResponseTime=420)
    for j in range(9):                       # down in SolarWinds, powered off in vCenter: "Powered off", never down
        st(apps[30 + j * 61], 2, "Node status is Down.", PercentLoss=100, ResponseTime=-1)
    for j in range(6):
        st(apps[600 + j * 5], 9, "Node is Unmanaged.", UnManaged=True)
    # 5 SolarWinds VMs not in vCenter (decommissioned, still polled); a caption with markup (the page escapes it)
    for j in range(5):
        node("SITEAOLDV%02d" % (j + 1), "", True, None)
    node('<img src=x onerror="alert(1)">SITEAXSS01', "Databases", True, 5)
    groups = [{"ContainerID": 100 + gi, "Name": g[0], "Status": 1} for gi, g in enumerate(GROUPS)]
    groups.append({"ContainerID": 199, "Name": "Empty group", "Status": 0})
    for g in groups:
        if any(m["ContainerID"] == g["ContainerID"] and by.get(m["Name"], {}).get("Status") == 2 for m in members):
            g["Status"] = 14
    return {"user": user, "password": password, "nodes": nodes, "interfaces": [], "connections": [], "points": [],
            "modules": [{"Name": "Orion", "DisplayName": "Orion Platform", "Version": "2025.4.1"}],
            "engines": [{"ServerType": "Primary", "EngineVersion": "2025.4.1.1234"}], "properties": ["ServiceGroup"],
            "tables": {"Orion.Container": groups, "Orion.ContainerMembers": members}}


def vms(fx):
    """the VMs vCenter has: every VM-type node but the five old ones (some by FQDN, some lower case), and 37 VMs SolarWinds
    does not watch; SITEASQLV05 running with its heartbeat green (SolarWinds: down - Disagree); nine workloads powered off"""
    out = []
    downs = set(n["Caption"] for n in fx["nodes"] if n["Status"] == 2)
    for i, n in enumerate(fx["nodes"]):
        c = n["Caption"]
        if n["Vendor"] != "VMware Inc." or c.startswith("SITEAOLDV") or c.startswith("<"):
            continue
        name = c.lower() + ".corp.example.mil" if i % 3 == 0 else c.lower() if i % 3 == 1 else c
        v = {"id": "vm-%d" % n["NodeID"], "name": name, "host": "sitea-esx%02d.corp.example.mil" % (1 + i % 18), "power": "poweredOn",
             "heartbeat": "green", "tools": "guestToolsRunning", "tools_version": "guestToolsCurrent", "tools_seen": True, "template": False,
             "vcls": False, "cpu_pct": 20.0, "mem_pct": 30.0, "ip": n["IPAddress"], "guest": c.lower() + ".corp.example.mil"}
        if c == "SITEADIRDCV01":
            v.update(heartbeat="gray", tools="guestToolsNotRunning")
        elif c == "SITESNOWCVM50":
            v.update(heartbeat="red")
        elif c in downs and c.startswith("SITEAAPPV"):
            v.update(power="poweredOff", heartbeat=None, tools="guestToolsNotRunning", cpu_pct=None, mem_pct=None)
        out.append(v)
    for j in range(VC_ONLY_N):
        out.append({"id": "vm-x%d" % j, "name": "SITEAVMX%02d" % (j + 1), "host": "sitea-esx%02d.corp.example.mil" % (1 + j % 18), "power": "poweredOn",
                    "heartbeat": "red" if j == 4 else "gray" if j == 7 else "green", "tools": "guestToolsRunning", "tools_version": "guestToolsCurrent",
                    "tools_seen": True, "template": False, "vcls": False, "cpu_pct": 10.0, "mem_pct": 20.0, "ip": "203.0.113.%d" % (j + 2), "guest": ""})
    out.append({"id": "vm-t", "name": "SITEATEMPLATE01", "host": "", "power": "poweredOff", "template": True})              # never a workload
    out.append({"id": "vm-c", "name": "vCLS-0001", "host": "", "power": "poweredOn", "template": False, "vcls": True})
    return out


def view(now=None, fx=None, vc_age=24, vc_state="ok"):
    """tests/wall/demo.py's view, with the fleet's VMs in vc01 (read vc_age s ago)"""
    import demo
    now = now or time.time()
    v = demo.view(now=now, vc2_state="ok")
    s = v["sources"]["vcenter:" + demo.VC1]
    s["snap"]["vms"] = list(s["snap"]["vms"]) + vms(fx or fixture())
    s["at"] = s["tried"] = now - vc_age
    s["state"] = vc_state
    return v


def rows_of(fx, prop=False):
    """the rows wall_fleet.read_nodes would get from the fake SWIS"""
    cols = ["NodeID", "Caption", "DNS", "IPAddress", "Status", "StatusDescription", "Vendor", "MachineType", "ResponseTime", "PercentLoss",
            "UnManaged", "LastSync"]
    out = []
    for n in sorted(fx["nodes"], key=lambda x: x["NodeID"]):
        r = dict((c, n.get(c)) for c in cols)
        if prop:
            r["cp_group"] = n["cp"].get("ServiceGroup")
        out.append(r)
    return out


def groups_of(fx):
    by = {}
    for m in fx["tables"]["Orion.ContainerMembers"]:
        by.setdefault(str(m["MemberPrimaryID"]), []).append(m["ContainerID"])
    return {"list": [[g["ContainerID"], g["Name"], g["Status"]] for g in fx["tables"]["Orion.Container"]], "members": by, "cut": False}


class FakeReader(object):
    """wall_fleet.Reader's two calls, from the fixture: read age_s ago, state as given"""

    def __init__(self, fx=None, age_s=35, state="ok", prop=False, moving=False):
        self.fx, self.age_s, self.state, self.prop, self.moving = fx or fixture(), age_s, state, prop, moving
        self.t0 = time.time()
        self.reads = 1

    def viewed(self):
        pass

    def rows(self, at):
        """the fixture's rows, Orion's own poll 40 s before the read (as a live Orion)"""
        sync = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at - 40))
        return [dict(r, LastSync=sync, _read_at=at) for r in rows_of(self.fx, self.prop)]

    def snapshot(self):
        now = time.time()
        at = (self.t0 + (now - self.t0) // 60 * 60 if self.moving else self.t0) - self.age_s     # moving: read again every minute
        return {"state": self.state, "why": "" if self.state == "ok" else "SolarWinds refused the login (HTTP 401)", "at": at, "tried": at,
                "fail_since": None, "next_try": at + 60, "nodes": self.rows(at), "cut": False, "notes": [],
                "groups": None if self.prop else groups_of(self.fx), "groups_at": at, "groups_why": "", "reads": self.reads}


if __name__ == "__main__":
    fx = fixture(float(sys.argv[2]) if len(sys.argv) > 2 else 1.0, *(sys.argv[3:5] if len(sys.argv) > 4 else ("svc_swis_ro", PW)))
    with open(sys.argv[1], "w") as fh:
        json.dump(fx, fh)
    print("nodes", len(fx["nodes"]))
