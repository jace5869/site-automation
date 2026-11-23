#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.116.1: read other sources for a VM outage review with this job's report reader and SolarWinds credentials."""
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_host_swis as hs
from ansible.module_utils import site_outage_context as ctx
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_swis_db
from ansible.module_utils import site_time
from ansible.module_utils.site_redact import redact


def review(p):
    def live(orion, ids, window):
        why = []
        client = hs.client_of(p, why, retries=0)
        if not client:
            raise ctx.NoData("the SolarWinds credential could not be used" if why else "no SolarWinds credential")
        if site_swis_db.orion_key(client.url) != orion:
            raise ctx.NoData("the SolarWinds credential is for a different Orion than the snapshot")
        result = hs.syslog_live(client, ids, dict(window=window, max_rows=ctx.LOG_LIMIT, redact=lambda value: ctx.clean_source(value, redact)))
        if not result["ok"] and result["why"].startswith("Log Analyzer could not be read:"):
            # The driver diagnostic may have been cut in the middle of a credential.
            raise ctx.NoData("Log Analyzer could not be read")
        return result

    try:
        conn, _tls = db.connect(dict(p, read_timeout=p["timeout"]))
        try:
            return ctx.read(conn, p["scope"], p["minutes"], p["storage_nodes"], live if p["syslog"] else None,
                            site_time.zone(p["time_zone"])[0])
        finally:
            conn.close()
    except Exception:  # A driver (including login, TLS and close) can quote the password in its exception.
        return ctx.unavailable(ctx.DB_ERROR)


def main():
    module = AnsibleModule(argument_spec=dict(db.ARGS, scope=dict(type="dict", required=True), minutes=dict(type="int", default=15),
                           storage_nodes=dict(type="list", elements="str", default=[]), syslog=dict(type="bool", default=True),
                           time_zone=dict(type="str", default=""), url=dict(type="str", default=""), validate_certs=dict(type="bool", default=True),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)), supports_check_mode=True)
    module.exit_json(changed=False, context=review(module.params))


if __name__ == "__main__":
    main()
