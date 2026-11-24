#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Windows - schedule a restart / cancel a scheduled restart: each server's SolarWinds node (plugins/module_utils/
site_win_restart_swis.py). On the AAP side, with a "SolarWinds (manage nodes)" credential (aap/credential_types/solarwinds_manage.yml:
SOLARWINDS_MANAGE_URL, _USERNAME, _PASSWORD, _VERIFY, _CA_PATH; an account that may manage nodes). The read-only
credential's SOLARWINDS_* are never used here, not even to read.
  action find: the node of each host (read only) -> {found: {host: {node, how, why}}, window}
  action run:  items [{host, op: schedule | remove, node_id, rid, why, window, earlier}] -> {result: {host: {text, level, done,
               record, times}}} - record: what makes the window ours (written beside the task's settings by the next step)
               - Check (or check: true): what it would do, nothing invoked
No credential: {skipped: why} - never a failure. A SolarWinds error is each server's warning, never the job's failure."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_swis as sw
from ansible.module_utils import site_win_restart_swis as rs

try:
    import zoneinfo
except ImportError:
    zoneinfo = None


def client(p):
    env = os.environ
    user, password = env.get("SOLARWINDS_MANAGE_USERNAME", ""), env.get("SOLARWINDS_MANAGE_PASSWORD", "")
    url = (env.get("SOLARWINDS_MANAGE_URL") or "").strip()
    if not (user and password and url):
        return None, "no credential"
    verify = str(env.get("SOLARWINDS_MANAGE_VERIFY", "true")).strip().lower() not in ("0", "false", "no", "off")
    ca = (env.get("SOLARWINDS_MANAGE_CA_PATH") or "").strip()
    try:
        return rs.Manager(url, user, password, verify=verify, ca_path=ca if ca and os.path.isfile(ca) else "", timeout=p["timeout"]), ""
    except sw.SwisError as e:
        return None, "the credential's address: %s" % e
    except (OSError, ValueError) as e:
        return None, "the credential's CA file could not be used (%s)" % e.__class__.__name__


def main():
    m = AnsibleModule(argument_spec=dict(action=dict(type="str", required=True, choices=["find", "run"]),
                                         hosts=dict(type="list", elements="dict", default=[]),
                                         items=dict(type="list", elements="dict", default=[]),
                                         at_utc=dict(type="str", default=""), before=dict(type="int", default=5),
                                         after=dict(type="int", default=10), zone=dict(type="str", default=""),
                                         check=dict(type="bool", default=False), timeout=dict(type="int", default=30)),
                      supports_check_mode=True)
    p = m.params
    c, why = client(p)
    if c is None:
        m.exit_json(changed=False, skipped_why=why, found={}, result={}, window={})
    tz = None
    if zoneinfo is not None and p["zone"].strip() and p["zone"].strip().upper() not in ("UTC", "Z", "GMT", "ETC/UTC"):
        try:
            tz = zoneinfo.ZoneInfo(p["zone"].strip())
        except Exception:  # noqa: BLE001 - the window shown in UTC then
            tz = None
    win = rs.window(p["at_utc"], p["before"], p["after"]) if p["at_utc"] else {}
    try:
        if p["action"] == "find":
            found = rs.find(c, p["hosts"])
            m.exit_json(changed=False, found=found, window=win, window_text=rs.shown(win, tz) if win else "")
        res = rs.run(c, p["items"], datetime.datetime.now(datetime.timezone.utc), p["check"] or m.check_mode, tz)
        times = sorted(set(r.get("times") for r in res.values() if r.get("times")))
        m.exit_json(changed=any(r["done"] for r in res.values()), result=res, window=win, window_text=rs.shown(win, tz) if win else "",
                    times=times)
    except sw.SwisError as e:
        m.exit_json(changed=False, skipped_why="SolarWinds could not be read: %s" % e, found={}, result={}, window=win)


if __name__ == "__main__":
    main()
