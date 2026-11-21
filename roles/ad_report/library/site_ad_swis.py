#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.94.0: the AD health report's "From SolarWinds" (plugins/module_utils/site_ad_swis.py). Read only, on the AAP side.

- The SolarWinds snapshot in the report database (a "Report database" credential: REPORT_DB_*; 1.96.0's
  site_swis_db) when it is there and not older than max_age minutes;
- else SolarWinds itself (a "SolarWinds (read-only)" credential: SOLARWINDS_URL / _USERNAME / _PASSWORD), the DCs only;
- else nothing: the report says why. It never fails the job and never changes anything."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_ad_swis as ad
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_swis
try:                                                     # 1.96.0 (the SolarWinds snapshot): bundled when it is in the tree
    from ansible.module_utils import site_swis_db
except ImportError:
    site_swis_db = None


def main():
    module = AnsibleModule(
        argument_spec=dict(dcs=dict(type="list", elements="dict", default=[]), max_age=dict(type="int", default=60),
                           days=dict(type="int", default=30), now=dict(type="str", default=""), timeout=dict(type="int", default=30),
                           host=dict(type="str", default=""), port=dict(type="int", default=3309), database=dict(type="str", default="site_reports"),
                           user=dict(type="str", default=""), password=dict(type="str", default="", no_log=True),
                           tls=dict(type="bool", default=False), ca_file=dict(type="str", default=""), connect_timeout=dict(type="int", default=10),
                           url=dict(type="str", default=""), validate_certs=dict(type="bool", default=True), ca_path=dict(type="str", default="")),
        supports_check_mode=True)
    p = module.params
    opts = {"max_age": p["max_age"], "days": p["days"], "now": p["now"]}
    why, conn, client = [], None, None
    if p["host"]:
        if site_swis_db is None:
            why.append("the report database is attached, but this release has no SolarWinds snapshot reader (1.96.0)")
        elif not db.HAS_PYMYSQL:
            why.append("the report database: %s" % db.NO_PYMYSQL)
        else:
            try:
                conn, _tls = db.connect(p)
            except Exception as e:  # noqa: BLE001 - unreachable, refused: said, SolarWinds itself next
                why.append("the report database: %s" % ad._say(e))     # fixed words: never its host, user or message
    env = os.environ
    user, password = env.get("SOLARWINDS_USERNAME", ""), env.get("SOLARWINDS_PASSWORD", "")
    url = (env.get("SOLARWINDS_URL") or p["url"] or "").strip()
    if user and password and url:
        verify = p["validate_certs"]
        if str(env.get("SOLARWINDS_VERIFY", "")).strip():
            verify = str(env["SOLARWINDS_VERIFY"]).strip().lower() not in ("0", "false", "no", "off")
        ca = (env.get("SOLARWINDS_CA_PATH") or p["ca_path"] or "").strip()
        try:
            client = site_swis.Client(url, user, password, verify=verify, ca_path=ca if ca and os.path.isfile(ca) else "", timeout=p["timeout"])
        except site_swis.SwisError as e:
            why.append("the SolarWinds credential's address: %s" % e)
    res = ad.read([{"dc": str(d.get("dc") or ""), "ip": str(d.get("ip") or ""), "fqdn": str(d.get("fqdn") or "")} for d in p["dcs"]], opts, conn=conn,
                  swdb=site_swis_db, client=client)
    res["why"] = why + res["why"]
    res["asked"] = bool(conn is not None or client is not None or why)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    module.exit_json(changed=False, **res)


if __name__ == "__main__":
    main()
