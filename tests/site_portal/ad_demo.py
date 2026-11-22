#!/usr/bin/env python3
"""1.101.2: the portal's Active Directory page on the real service, for looking at it (screenshots): a report database (SQLite,
filled by the AD job's own writer site_ad_db with 7 days of runs every 30 min, DC01-DC06) and SolarWinds stood in. Test data
only. The card's headers (nginx's job) come from the browser: X-SSL-Client-Verify: SUCCESS, X-SSL-Client-S-DN.
  python tests/site_portal/ad_demo.py LOOK PORT [state]     prints READY when it listens
  state: ok (default) | nodb | stale | norun | old"""
import datetime
import json
import os
import sys
import tempfile

wt = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
look, port = sys.argv[1], int(sys.argv[2])
state = sys.argv[3] if len(sys.argv) > 3 else "ok"
sys.path[:0] = [os.path.join(wt, "plugins", "filter"), os.path.join(wt, "plugins", "module_utils"), os.path.join(wt, "roles/site_portal_stack/files"),
                os.path.join(wt, "roles", "ad_report", "library"), os.path.join(wt, "tests")]
import site_report_db  # noqa: E402
import ansible.module_utils  # noqa: E402
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db)
ansible.module_utils.site_report_db = site_report_db
import site_portal as sp  # noqa: E402
import site_ad_db as adb  # noqa: E402
import ad_db_filters as dbf  # noqa: E402
from test_portal_ad import Lite  # noqa: E402

NOW = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
NOW = NOW - datetime.timedelta(minutes=NOW.minute % 30)
DCS = [("DC01", "SITA0101"), ("DC02", "SITA0101"), ("DC03", "SITA0101"), ("DC04", "SITA0101"), ("DC05", "SITB0201"), ("DC06", "SITC0301")]


def state_of(dc, k):
    """k runs back: DC03 critical for the last 6, DC05 warning on and off, DC06 not read twice."""
    if dc == "DC03" and k < 6:
        return "critical", "dcdiag: Replications failed (8524, DNS lookup failure)"
    if dc == "DC05" and k in (1, 2, 9, 10, 11, 30):
        return "warning", "Time: 2.4 s off the PDC emulator"
    if dc == "DC06" and k in (20, 21):
        return None, "WinRM did not answer"
    if dc == "DC02" and 150 < k < 154:
        return "critical", "DNS: the SRV records are missing"
    return "ok", ""


def fill(lite, back=0):
    for k in range(335, -1, -1):
        t = NOW - datetime.timedelta(minutes=30 * (k + back))
        iso = lambda h: (t - datetime.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
        probs, dcs = [], []
        for dc, _site in DCS:
            st, text = state_of(dc, k)
            dcs.append({"dc": dc, "domain": "corp.test", "read": st is not None, "error": text if st is None else ""})
            if st not in (None, "ok"):
                probs.append({"domain": "corp.test", "object": dc, "severity": st, "text": text})
        crit = sum(1 for p in probs if p["severity"] == "critical") + sum(1 for d in dcs if not d["read"])
        ad = {"report": {"status": "critical" if crit else ("warning" if probs else "ok")}, "critical": crit, "warning": len(probs) - crit,
              "problems": probs, "dcs": dcs,
              "repl": {"now": iso(0), "dcs": [{"name": dc, "site": s} for dc, s in DCS],
                       "links": [{"frm": "DC01", "to": "DC06", "nc": "corp.test", "last_success": iso(31.5) if k < 63 else iso(0.2),
                                  "failures": 3 if k < 63 else 0, "error": "8524 (DNS lookup failure)" if k < 63 else "", "last_failure": iso(0.1)},
                                 {"frm": "DC02", "to": "DC06", "nc": "Configuration", "last_success": iso(9.2), "failures": 0},
                                 {"frm": "DC05", "to": "DC02", "nc": "DomainDnsZones", "last_success": iso(4.6), "failures": 0},
                                 {"frm": "DC02", "to": "DC01", "nc": "corp.test", "last_success": iso(0.1), "failures": 0}]}}
        live = {"kind": "ad", "dcs": [{"dc": dc, "ip": ""} for dc, _s in DCS], "problems": probs, "opts": {"cpu_warn": 90, "mem_warn": 90, "days": 30}}
        adb.write(lite, t, 9400 + (335 - k), 12, dbf.ad_db_payload(ad, live, {}))
    lite.commit()


def swis(live):
    def d(name):
        down = name == "DC04"
        return {"dc": name, "ip": "", "matched": "name", "status": "Down" if down else "Up", "status_text": "", "cpu": None if down else 14,
                "mem": None if down else 41, "alerts": [], "apps": {}, "last_down": None}
    return {"ok": True, "source": "swis-live", "read_at": NOW.strftime("%Y-%m-%d %H:%M"), "age_min": 0, "why": [], "sam": False,
            "sam_why": "SAM's applications are not on this Orion", "events": False, "dcs": [d(x["dc"]) for x in live["dcs"]]}


lite = Lite()
if state == "old":
    fill(lite, back=6)
elif state != "norun":
    fill(lite)
tmp = tempfile.mkdtemp()
reports = os.path.join(tmp, "reports")
os.makedirs(os.path.join(reports, "ad-health"))
with open(os.path.join(reports, "index.json"), "w") as fh:
    json.dump({"reports": [{"key": "ad-health/latest.html", "latest": "ad-health/latest.html", "when": NOW.strftime("%Y-%m-%d %H:%M UTC"),
                            "job": "9735", "live": "ad"}]}, fh)
with open(os.path.join(tmp, "actions.json"), "w") as fh:
    json.dump({"actions": {}}, fh)
cfg = sp.Config({"PORTAL_DATA": tmp, "PORTAL_REPORTS": reports, "PORTAL_ACTIONS": os.path.join(tmp, "actions.json"), "PORTAL_AAP_URL": "http://127.0.0.1:9",
                 "PORTAL_AAP_TOKEN": "x", "PORTAL_THEME": look, "PORTAL_TITLE": "Example site portal", "PORTAL_TIME_ZONE": "UTC"})
cfg.ad_swis_why = ""
calls = []


def connect():
    calls.append(1)
    if state == "stale" and len(calls) > 1:
        raise RuntimeError("(2003, \"Can't connect to MySQL server on 'db.example.mil' (timed out)\")")
    return lite


real = sp.AdLive.__init__


def init(self, c, log, aap=None, source=None, db=None):
    real(self, c, log, aap, source=swis, db=None if state == "nodb" else sp.AdDb(c, connect=connect, mark="?"))
    self.sched = {"next": (NOW + datetime.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")}
    self.sched_at = 1e12


sp.AdLive.__init__ = init
sp.AD_POLL = 15 if state == "stale" else 60
portal = sp.Portal(cfg)
portal.aap.ready = ""
sys.stdout.flush()
H = sp.make_handler(cfg, portal, os.path.join(wt, "roles", "site_portal_stack", "files", "static"))


class Front(H):
    """nginx's part: /portal/... to the service as /... (the card's headers come from the browser)."""

    def do_GET(self):
        if self.path.startswith("/portal/"):
            self.path = self.path[len("/portal"):]
        return H.do_GET(self)


srv = sp.PortalServer(("127.0.0.1", port), Front)
print("READY", flush=True)
sp.sys.stdout = open(os.devnull, "w")
srv.serve_forever()
