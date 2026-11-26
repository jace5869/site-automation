#!/usr/bin/env python3
"""The wall's made-up demo: a collector view (two vCenters, a host not responding, a VM and a host high for longer than the
sustained window, a datastore over the warning line, a vCenter whose login failed) and the NetApp page's test run in SQLite,
for the tests (tests/test_wall.py) and the screenshots. Placeholders only.

  python3 tests/wall/demo.py [port]      a local portal with the demo on http://127.0.0.1:<port>/wall (the card headers added
                                          by a small front, as nginx would); Ctrl-C stops it"""
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
ROOT = os.path.dirname(TESTS)
FILES = os.path.join(ROOT, "roles", "site_portal_stack", "files")
for p in (FILES, os.path.join(ROOT, "plugins", "module_utils"), os.path.join(ROOT, "plugins", "filter"), TESTS, os.path.join(TESTS, "netapp"),
          os.path.join(TESTS, "site_portal")):
    if p not in sys.path:
        sys.path.insert(0, p)

VC1, VC2 = "vc01.corp.example.mil", "vc02.corp.example.mil"
TB = 1024 ** 4
CN = "DOE.JANE.Q.1234567890"
DN = "CN=%s,OU=USA,OU=PKI,OU=DoD,O=U.S. Government,C=US" % CN


def host(n, cl, cpu, mem, vms, ds, state="connected", maint=False):
    return {"name": n, "cluster": cl, "state": state, "maint": maint, "hardware": "green", "power": "poweredOn" if state == "connected" else "unknown",
            "cpu_pct": None if state != "connected" else cpu, "mem_pct": None if state != "connected" else mem, "vms": vms, "datastores": ds}


def vcenter_snap():
    """vc01: two clusters at SITA01, one at SITB02; sita01-esx03 not responding with 14 VMs on it"""
    hosts, vms = [], []
    ds_a = ["DS-SITEA-01", "DS-SITEA-03", "DS-SITEA-04", "DS-SITEA-07"]
    ds_b = ["DS-SITEA-05", "DS-SITEA-06", "DS-SITEA-08"]
    loads = {"SITA01-CL01": [(58, 76), (66, 81), (0, 0), (61, 79), (55, 74), (70, 85), (52, 71), (63, 80)],
             "SITA01-CL02": [(44, 70), (52, 93), (49, 72), (57, 77), (41, 69), (60, 80)],
             "SITB02-CL01": [(38, 66), (45, 70), (51, 73), (47, 69)]}
    k, seq = 0, {}
    for cl, rows in loads.items():
        site = cl.split("-")[0].lower()
        for cpu, mem in rows:
            seq[site] = i = seq.get(site, 0) + 1
            n = "%s-esx%02d.corp.example.mil" % (site, i)
            names = ["%s-app%02d" % (site, k + j) for j in range(1, (14 if n.startswith("sita01-esx03") else 6) + 1)]
            k += len(names)
            down = n.startswith("sita01-esx03")
            hosts.append(host(n, cl, cpu, mem, names, ds_a if cl.startswith("SITA01") else ds_b, "notResponding" if down else "connected"))
            for v in names:
                vms.append({"name": v, "host": n, "power": "poweredOn", "cpu_pct": 22.0, "mem_pct": 35.0, "heartbeat": "gray" if down else "green"})
    vms.append({"name": "sitb02-app07", "host": "sitb02-esx02.corp.example.mil", "power": "poweredOn", "cpu_pct": 97.0, "mem_pct": 60.0, "heartbeat": "green"})
    vms.append({"name": "sita01-db02", "host": "sita01-esx05.corp.example.mil", "power": "poweredOn", "cpu_pct": 93.0, "mem_pct": 50.0, "heartbeat": "green"})
    dss = [("DS-SITEA-07", 15.6, 1.1, "NFS"), ("DS-SITEA-01", 16.0, 2.6, "VMFS"), ("DS-SITEA-03", 16.5, 3.4, "VMFS"), ("DS-SITEA-05", 16.5, 4.8, "VMFS"),
           ("DS-SITEA-04", 12.0, 4.1, "VMFS"), ("DS-SITEA-06", 10.0, 4.4, "NFS"), ("DS-SITEA-08", 8.0, 5.9, "VMFS"), ("DS-SITEB-LOCAL", 2.0, 1.6, "VMFS")]
    for vm in vms:
        vm.update(template=False, tools_seen=True)
    datastores = [{"name": n, "shared": n != "DS-SITEB-LOCAL", "type": t, "capacity": int(c * TB), "free": int(f * TB), "accessible": True,
                   "hosts": [h["name"] for h in hosts if n in h["datastores"]], "vms": 12 + i} for i, (n, c, f, t) in enumerate(dss)]
    return {"hosts": hosts, "clusters": [{"name": c, "hosts": [h["name"] for h in hosts if h["cluster"] == c]} for c in loads],
            "datastores": datastores, "vms": vms}


def series(now, minutes, level, before=55.0):
    """one sample a minute for the last hour: `before` until `minutes` ago, then `level`"""
    return [(now - 60 * m, level if m < minutes else before) for m in range(60, -1, -1)]


NA1, NA2 = "sita01-nas.corp.example.mil", "sitb02-nas.corp.example.mil"


def ontap_snap(name, aggr_used=(0.62, 0.91), broken=False):
    """one live ONTAP cluster's read (wall_live.OntapSource.poll's shape): two aggregates, two nodes up, volumes and LUNs online"""
    cl = name.split(".")[0]
    return {"cluster": cl, "version": "NetApp Release 9.14.1", "failed": {},
            "nodes": [{"name": cl + "-01", "state": "up", "takeover": True}, {"name": cl + "-02", "state": "up", "takeover": True}],
            "aggrs": [{"name": "%s_aggr%d" % (cl.replace("-", "_"), i + 1), "node": cl + "-0%d" % (i + 1), "size": int(40 * TB), "used": int(u * 40 * TB)}
                      for i, u in enumerate(aggr_used)],
            "volumes": [{"name": "vol_ds%02d" % i, "svm": "svm_vmware", "state": "online", "size": int(2 * TB), "used": int(1.2 * TB), "aggr": "",
                         "autosize": False, "snap_reserve": 0, "snap_used": 0} for i in range(1, 4)],
            "luns": [{"path": "/vol/vol_ds%02d/lun0" % i, "name": "lun0", "svm": "svm_vmware", "volume": "vol_ds%02d" % i, "size": int(2 * TB),
                      "used": int(1.1 * TB), "state": "online"} for i in range(1, 4)],
            "snapmirror": [{"src": "%s:vol_ds01" % cl, "dst": "dr:vol_ds01_dr", "state": "broken_off" if broken else "snapmirrored",
                            "healthy": not broken, "lag_s": 600, "why": "Transfer failed." if broken else ""}]}


def view(now=None, vc2_state="auth", ontap=True):
    now = now or time.time()
    snap = vcenter_snap()
    key1, key2 = "vcenter:" + VC1, "vcenter:" + VC2
    hist = {}
    for h in snap["hosts"]:
        if h["cpu_pct"] is not None:
            hist[(key1, "host", h["name"], "cpu")] = series(now, 0, h["cpu_pct"], h["cpu_pct"])
            hist[(key1, "host", h["name"], "mem")] = series(now, 0, h["mem_pct"], h["mem_pct"])
    hist[(key1, "host", "sita01-esx02.corp.example.mil", "mem")] = series(now, 18, 93.0, 74.0)        # high for 18 min: on the wall
    hist[(key1, "vm", "sitb02-app07", "cpu")] = series(now, 42, 97.0, 58.0)                           # high for 42 min: on the wall
    hist[(key1, "vm", "sita01-db02", "cpu")] = series(now, 4, 93.0, 40.0)                             # 4 min: a blip, not yet
    srcs = {key1: {"kind": "vcenter", "name": VC1, "state": "ok", "why": "", "at": now - 24, "tried": now - 24, "next_try": None, "skipped": 0,
                   "snap": snap, "fail_since": None},
            key2: {"kind": "vcenter", "name": VC2, "state": vc2_state, "why": "login failed - check the credential" if vc2_state == "auth" else "",
                   "at": None if vc2_state == "auth" else now - 30, "tried": now - 300, "next_try": now + 600, "skipped": 0,
                   "snap": None if vc2_state == "auth" else {"hosts": [], "clusters": [], "datastores": [], "vms": []},
                   "fail_since": now - 300 if vc2_state == "auth" else None}}
    if ontap:                                        # the wall's Storage is read live only (Codex verify 4): two clusters
        for name, used, broken in ((NA1, (0.62, 0.91), True), (NA2, (0.40, 0.55), False)):
            srcs["ontap:" + name] = {"kind": "ontap", "name": name, "state": "ok", "why": "", "at": now - 20, "tried": now - 20, "next_try": None,
                                     "snap": ontap_snap(name, used, broken), "fail_since": None}
    return {"sources": srcs, "history": hist}


class FakeCollector(object):
    def __init__(self, fn):
        self.fn = fn

    def view(self):
        return self.fn()


OPTS = {"site_places": {"SITA01": "DE", "SITB02": "US"}, "maintenance_expected": []}


def netapp_db(minutes_old=11):
    """the NetApp page's test run (tests/test_netapp_live.py's fill), its newest run minutes_old minutes ago"""
    import datetime
    import test_netapp_live as T
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0) - datetime.timedelta(minutes=minutes_old)
    return T.fill(now=now)


def portal(port=0, vc2_state="auth", netapp_minutes_old=11, opts=None, collector=None, netapp=True, health=True, fleet=None, env_more=None):
    """-> (base URL behind the header front, stop()) - a portal with the demo wall, as the tests and the screenshots use it.
    1.136.0: fleet - a wall_fleet.Reader stand-in (tests/wall/fleet_demo.py FakeReader): the Ops monitor area on"""
    import json
    import shutil
    import tempfile
    import site_portal as sp
    import netapp_live as nl
    tmp = tempfile.mkdtemp(prefix="wall-demo-")
    T = __import__("test_netapp_live")
    env = {"PORTAL_WALL_OPTS": json.dumps(dict(OPTS, **(opts or {}))), "PORTAL_WALL_TITLE": "Ops center", "PORTAL_WALL_ENCLAVE": "SITEA-UNCLASS",
           "PORTAL_TIME_ZONE": "Europe/Berlin", "PORTAL_APPROVERS": json.dumps([CN]),
           "PORTAL_WALL_TEST": "1"}   # the test harness: ?freeze= and ?pull= honoured
    if fleet is not None:
        env.update({"PORTAL_WALL_MONITOR": "1", "PORTAL_SWIS_URL": "https://swis.example.invalid", "PORTAL_SWIS_LOGIN": json.dumps({"user": "svc_swis_ro", "password": "x"})})
    env.update(env_more or {})
    cfg = T.cfg_for(tmp, **env)
    p = sp.Portal(cfg)
    p.aap.ready = ""
    nal = None
    if netapp:                                       # netapp=False: a portal with no NetApp read at all
        db = netapp_db(netapp_minutes_old)
        if not health:                               # ontap_live_health false: the run has no SnapMirror and no node parts
            db.db.execute("DELETE FROM ontap_live_part WHERE part IN ('snapmirror', 'nodes', 'clusters')")
        nal = sp.NetappLive(cfg, p.log, p, db=nl.Database(lambda: db, mark="?"))
        nal.refresh()
    cfg.netapp_tab = True
    wall = sp.WallLive(cfg, p.log, nal, collector=collector or FakeCollector(lambda: view(vc2_state=vc2_state)), fleet=fleet)
    if collector is None:
        wall.model(time.time() - 3 * 3600)              # a first read 3 h ago: the tracker's starts, so durations read like a running wall
    srv = sp.PortalServer(("127.0.0.1", 0), sp.make_handler(cfg, p, os.path.join(FILES, "static"), wall=wall))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    inner = "http://127.0.0.1:%d" % srv.server_address[1]

    class Front(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            path = self.path[len("/portal"):] if self.path.startswith("/portal/") else self.path
            req = urllib.request.Request(inner + path, headers={"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": DN,
                                                                "If-None-Match": self.headers.get("If-None-Match", "")})
            try:
                with urllib.request.urlopen(req, timeout=30) as f:
                    code, body, hd = f.status, f.read(), f.headers
            except urllib.error.HTTPError as e:
                code, body, hd = e.code, e.read(), e.headers
            self.send_response(code)
            for k in ("Content-Type", "Content-Security-Policy", "ETag", "Cache-Control"):
                if hd.get(k):
                    self.send_header(k, hd[k])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_POST(self):
            # as nginx over TLS would: the page's own origin (https) and its token go through
            path = self.path[len("/portal"):] if self.path.startswith("/portal/") else self.path
            n = int(self.headers.get("Content-Length") or 0)
            host = inner.split("://", 1)[1]
            req = urllib.request.Request(inner + path, data=self.rfile.read(n), method="POST", headers={
                "X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": DN, "Host": host, "Origin": "https://" + host,
                "Content-Type": self.headers.get("Content-Type", ""), "X-Portal-Token": self.headers.get("X-Portal-Token", "")})
            try:
                with urllib.request.urlopen(req, timeout=30) as f:
                    code, body = f.status, f.read()
            except urllib.error.HTTPError as e:
                code, body = e.code, e.read()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    front = ThreadingHTTPServer(("127.0.0.1", port), Front)
    threading.Thread(target=front.serve_forever, daemon=True).start()

    def stop():
        for s in (front, srv):
            s.shutdown()
            s.server_close()
        shutil.rmtree(tmp, True)
    return "http://127.0.0.1:%d/portal/" % front.server_address[1], stop


if __name__ == "__main__":
    url, stop = portal(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
    print(url + "wall", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop()
