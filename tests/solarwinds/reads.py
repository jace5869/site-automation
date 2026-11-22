# -*- coding: utf-8 -*-
"""1.97.0: what roles/solarwinds_report/library/site_host_swis.py gives back, for the filters' tests and the pages
(tests/test_solarwinds_reports.py): a health check's six hosts, a troubleshoot run's one host, a vCenter's hosts and VMs.
The shapes of site_host_swis.read() (and syslog_live() under "syslog"). Placeholders only."""
import copy

READ_AT = "2026-11-21 10:40"


def node(nid, name, status="Up", ip="", vendor="", model="", os_version="", last_poll="2026-11-21 10:38"):
    return {"node_id": nid, "name": name, "status": status, "status_code": {"Up": 1, "Down": 2, "Warning": 3}.get(status, 0),
            "status_text": "Node status is %s." % status, "ip": ip, "last_poll": last_poll, "cpu": 12.0, "mem": 40.0,
            "vendor": vendor, "model": model, "os_version": os_version}


def host(name, nd=None, **kw):
    h = {"host": name, "short": name.split(".")[0], "fqdn": name if "." in name else "", "ip": "", "kind": kw.pop("kind", ""),
         "matched": "name" if nd else "", "unmatched_why": kw.pop("why", ""), "node": nd, "alerts": [], "apps": [], "volumes": [],
         "ports": [], "events": []}
    h.update(kw)
    return h


def alert(name, sev, since, on=""):
    return {"name": name, "severity": sev, "since": since, "message": name, "on": on, "entity_type": "Orion.Nodes", "acknowledged": False}


def sam_app(name, status, comps=()):
    return {"app_id": sum(map(ord, name)) % 1000, "app": name, "status": status, "status_code": {"Up": 1, "Down": 2, "Warning": 3}[status],
            "status_text": status, "unmanaged": False, "components": 3,
            "components_not_up": [{"component_id": i, "name": c, "kind": "", "status": "Down", "status_code": 2, "status_text": "Down",
                                   "value": None, "message": ""} for i, c in enumerate(comps)]}


def vol(caption, pct, size_gb=50):
    size = size_gb * 2 ** 30
    return {"volume_id": sum(map(ord, caption)) % 1000, "caption": caption, "kind": "Fixed Disk", "size_bytes": size, "used_bytes": size * pct / 100.0,
            "used_pct": pct, "status": "Up"}


def health():
    """A health check's read: six hosts, two of them not in SolarWinds, Log Analyzer read."""
    hs = [
        host("web01.example.mil", node(101, "web01", ip="192.0.2.21"),
             alerts=[alert("Volume /var over 95 %", 2, "2026-11-21 10:02", "web01 /var"),
                     alert("Application down: Apache HTTP Server", 1, "2026-11-21 09:58", "web01")],
             apps=[sam_app("Apache HTTP Server", "Down", ["httpd process", "HTTPS port 443"]), sam_app("Linux CPU and memory", "Up"),
                   sam_app("SSH", "Up")],
             volumes=[vol("/var", 96.0), vol("/", 41.0)]),
        host("db01.example.mil", node(102, "db01", ip="192.0.2.22"), apps=[sam_app("MariaDB", "Up"), sam_app("SSH", "Up")], volumes=[vol("/", 52.0)]),
        host("app02.example.mil", node(103, "app02", status="Warning", ip="192.0.2.23"),
             alerts=[alert("High response time: HTTPS 443 over 2 s", 1, "2026-11-21 07:15", "app02")],
             apps=[sam_app("Tomcat", "Up"), sam_app("SSH", "Up")]),
        host("app03.example.mil", node(104, "app03", ip="192.0.2.24"), apps=[sam_app("Tomcat", "Up"), sam_app("SSH", "Up")]),
        host("mon01.example.mil"),
        host("lab07.example.mil"),
    ]
    syslog = {"ok": True, "why": "", "capped": False, "max_rows": 5000, "hours": 24, "levels": ["Emergency", "Alert", "Critical", "Error"],
              "by_node": {"101": {"count": 41, "top": [
                  {"message": "httpd[4121]: AH00052: child pid 4121 exit signal Segmentation fault (11)", "count": 28, "last": "2026-11-21 10:31"},
                  {"message": "kernel: EXT4-fs warning (device dm-3): ext4_dx_add_entry: Directory index full", "count": 9, "last": "2026-11-21 10:12"},
                  {"message": "systemd[1]: httpd.service: Main process exited, code=killed, status=11/SEGV", "count": 4, "last": "2026-11-21 09:58"}]},
                  "102": {"count": 3, "top": [{"message": "mariadbd[811]: [Warning] Aborted connection 812 to db: 'reports' "
                                                          "(Got timeout reading communication packets)", "count": 3, "last": "2026-11-21 08:44"}]}}}
    return {"ok": True, "read_at": READ_AT, "age_min": 6, "age_text": "6 min", "orion": "orion.example.mil:17774", "why": [],
            "not_read": [], "sam": True, "volumes": True, "events": False, "hosts": hs, "syslog": syslog}


def findings():
    F = lambda sev, check, summary, hint: {"severity": sev, "check": check, "summary": summary, "hint": hint, "id": check + ":x"}
    return {"web01.example.mil": [F("critical", "disk", "/var is 96 % full (2.1 GB free), full in about 3 days at this rate",
                                    "du -xh --max-depth=2 /var | sort -h | tail"),
                                  F("warning", "services", "httpd restarted 4 times in the last hour", "journalctl -u httpd --since -1h")],
            "db01.example.mil": [F("warning", "performance", "I/O wait 31 % (over 20 %) for the last 15 minutes", "iostat -x 5 3")],
            "app02.example.mil": [], "app03.example.mil": [], "mon01.example.mil": [], "lab07.example.mil": []}


def port(switch="sw-acc-02", name="Gi1/0/14", status="Up", errors=412, discards=0, sid=501, iid=7014):
    return {"switch": switch, "switch_id": sid, "interface_id": iid, "port": name, "status": status, "status_code": 1 if status == "Up" else 2,
            "speed": 1e9, "in_pct": 71.0, "out_pct": 12.0, "errors": errors, "discards": discards}


def troubleshoot(events=12):
    ev = [("10:02", "Alert Triggered", "Volume /var over 95 %", "node"), ("09:58", "Alert Triggered", "Application down: Apache HTTP Server", "node"),
          ("09:57", "Interface Up", "Gi1/0/14 is up", "port"), ("09:56", "Interface Down", "Gi1/0/14 is down", "port"),
          ("08:44", "Alert Reset", "High CPU load", "node"), ("08:21", "Alert Triggered", "High CPU load", "node"),
          ("06:10", "Node Rebooted", "web01 was rebooted", "node"), ("06:06", "Node Up", "web01 is up", "node"),
          ("06:04", "Node Down", "web01 is not responding", "node"), ("02:30", "Alert Reset", "Volume /var over 90 %", "node"),
          ("01:12", "Alert Triggered", "Volume /var over 90 %", "node"), ("00:15", "Node Changed", "Custom property Owner changed", "node")]
    while len(ev) < events:
        ev.append(("00:0%d" % (len(ev) % 10), "Node Changed", "Custom property changed", "node"))
    rows = [{"time": "2026-11-21 " + t, "kind": k, "event_type": 0, "message": m, "on": on, "event_id": 900 - i,
             "switch": "sw-acc-02" if on == "port" else ""}
            for i, (t, k, m, on) in enumerate(ev[:events])]
    h = host("web01.example.mil", node(101, "web01", ip="192.0.2.21", vendor="Dell", model="R750", os_version="Red Hat Enterprise Linux 9"),
             alerts=[alert("Application down: Apache HTTP Server", 1, "2026-11-21 09:58", "web01"),
                     alert("Volume /var over 95 %", 2, "2026-11-21 10:02", "web01 /var")],
             ports=[port()], events=rows)
    return {"ok": True, "read_at": READ_AT, "age_min": 6, "age_text": "6 min", "orion": "orion.example.mil:17774", "why": [],
            "not_read": [], "sam": True, "volumes": True, "events": True, "hosts": [h], "syslog": None}


def vcenter():
    """The alarms report's read: three hosts and a VM, two with SolarWinds alerts on hosts, one on the VM."""
    hs = [host("esx01", node(201, "esx01"), kind="host",
               alerts=[alert("Hardware: power supply 2 failed", 2, "2026-10-07 03:01", "esx01")]),
          host("esx02", node(202, "esx02", status="Down"), kind="host",
               alerts=[alert("Node down", 2, "2026-10-07 04:58", "esx02")],
               ports=[port("sw-acc-04", "Gi1/0/3", status="Down", errors=0, sid=504, iid=7203)],
               events=[{"time": "2026-10-07 04:57", "kind": "Interface Down", "event_type": 10, "message": "Gi1/0/3 is down", "on": "port",
                        "switch": "sw-acc-04", "event_id": 1}]),
          host("esx03", node(203, "esx03"), kind="host"),
          host("db01", node(204, "db01", status="Warning"), kind="vm",
               alerts=[alert("High CPU load over 90 % for 15 min", 1, "2026-10-07 06:55", "db01")])]
    return {"ok": True, "read_at": "2026-10-07 10:40", "age_min": 6, "age_text": "6 min", "orion": "orion.example.mil:17774", "why": [],
            "not_read": [], "sam": True, "volumes": True, "events": True, "hosts": hs, "syslog": None,
            "vim": {"esx01": {"kind": "host", "name": "esx01", "status": "Warning", "status_code": 14},
                    "esx02": {"kind": "host", "name": "esx02", "status": "Unknown", "status_code": 0},
                    "esx03": {"kind": "host", "name": "esx03", "status": "Up", "status_code": 1},
                    "db01": {"kind": "vm", "name": "db01", "status": "Warning", "status_code": 3}}}


def stale():
    return {"ok": False, "read_at": "", "age_min": None, "orion": "", "sam": False, "volumes": False, "events": False, "hosts": [],
            "why": ["the SolarWinds snapshot is 45 min old (of 2026-11-21 09:55 UTC), older than solarwinds_db_max_age (20 min)"]}


def get(name):
    return copy.deepcopy({"health": health, "troubleshoot": troubleshoot, "vcenter": vcenter, "stale": stale}[name]())
