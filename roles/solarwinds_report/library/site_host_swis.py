#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.97.0: the hosts of a health check, a troubleshoot run or a VMware alarms report as SolarWinds sees them
(plugins/module_utils/site_host_swis.py). Read only, on the AAP side.

- The SolarWinds snapshot in the report database (a "Report database" credential: REPORT_DB_*; 1.96.0's site_swis_db)
  when it is there and not older than max_age minutes; else nothing, and why says why.
- syslog: Log Analyzer's entries of the matched nodes, one capped SWQL query (a "SolarWinds (read-only)" credential:
  SOLARWINDS_URL / _USERNAME / _PASSWORD).
It never fails the job and never changes anything."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os

from ansible.module_utils.site_redact import redact
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_host_swis as hs
from ansible.module_utils import site_ad_swis as ad
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_swis
from ansible.module_utils import site_swis_db


def client_of(p, why):
    env = os.environ
    user, password = env.get("SOLARWINDS_USERNAME", ""), env.get("SOLARWINDS_PASSWORD", "")
    url = (env.get("SOLARWINDS_URL") or p["url"] or "").strip()
    if not (user and password and url):
        return None
    verify = p["validate_certs"]
    if str(env.get("SOLARWINDS_VERIFY", "")).strip():
        verify = str(env["SOLARWINDS_VERIFY"]).strip().lower() not in ("0", "false", "no", "off")
    ca = (env.get("SOLARWINDS_CA_PATH") or p["ca_path"] or "").strip()
    try:
        return site_swis.Client(url, user, password, verify=verify, ca_path=ca if ca and os.path.isfile(ca) else "", timeout=p["timeout"])
    except site_swis.SwisError as e:
        why.append("the SolarWinds credential's address: %s" % e)
    except (OSError, ValueError) as e:                  # the CA file: fixed words, never its text
        why.append("the SolarWinds credential's CA file could not be used (%s)" % e.__class__.__name__)
    return None


def main():
    module = AnsibleModule(
        argument_spec=dict(hosts=dict(type="list", elements="dict", default=[]), mode=dict(type="str", default="health",
                                                                                          choices=["health", "troubleshoot", "vcenter"]),
                           max_age=dict(type="int", default=20), event_hours=dict(type="int", default=24), now=dict(type="str", default=""),
                           syslog=dict(type="bool", default=False), syslog_hours=dict(type="int", default=24),
                           syslog_levels=dict(type="list", elements="str", default=list(hs.LEVELS)),
                           syslog_max_rows=dict(type="int", default=5000), timeout=dict(type="int", default=60),
                           host=dict(type="str", default=""), port=dict(type="int", default=3309), database=dict(type="str", default="site_reports"),
                           user=dict(type="str", default=""), password=dict(type="str", default="", no_log=True),
                           tls=dict(type="bool", default=False), ca_file=dict(type="str", default=""), connect_timeout=dict(type="int", default=10),
                           url=dict(type="str", default=""), validate_certs=dict(type="bool", default=True), ca_path=dict(type="str", default="")),
        supports_check_mode=True)
    p = module.params
    opts = {"max_age": p["max_age"], "mode": p["mode"], "event_hours": p["event_hours"], "now": p["now"],
            "orion": (os.environ.get("SOLARWINDS_URL") or p["url"] or "").strip()}
    why, conn = [], None
    if not p["host"]:
        why.append('this job has no "Report database" credential (the SolarWinds snapshot is read from it)')
    elif not db.HAS_PYMYSQL:
        why.append("the report database: %s" % db.NO_PYMYSQL)
    else:
        try:
            conn, _tls = db.connect(p)
        except Exception as e:  # noqa: BLE001 - unreachable, refused: said in fixed words
            why.append("the report database: %s" % ad._say(e))
    res = hs.read(p["hosts"], opts, conn, site_swis_db)
    res["why"] = why + res["why"]
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    res["syslog"] = None
    if p["syslog"] and res["ok"]:
        cw = []
        client = client_of(p, cw)
        if client is None:
            res["syslog"] = {"ok": False, "why": cw[0] if cw else 'this job has no "SolarWinds (read-only)" credential', "by_node": {}}
        else:
            ids = [h["node"]["node_id"] for h in res["hosts"] if h.get("node")]
            res["syslog"] = hs.syslog_live(client, ids, {"hours": p["syslog_hours"], "levels": p["syslog_levels"],
                                                         "max_rows": p["syslog_max_rows"], "now": p["now"], "redact": redact})
        # the module's answer travels as JSON: node ids as text
        res["syslog"]["by_node"] = dict((str(k), v) for k, v in (res["syslog"].get("by_node") or {}).items())
        res["syslog"]["queried"] = [str(k) for k in res["syslog"].get("queried") or []]
    module.exit_json(changed=False, **hs.redact_read(res, redact))


if __name__ == "__main__":
    main()
