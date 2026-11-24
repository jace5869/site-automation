#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Windows - schedule a restart: each server's SolarWinds node unmanaged for the restart window (plugins/module_utils/
site_win_restart_swis.py). On the AAP side, after the restart tasks are registered and verified, with a "SolarWinds (manage
nodes)" credential (aap/credential_types/solarwinds_manage.yml: SOLARWINDS_MANAGE_URL, _USERNAME, _PASSWORD, _VERIFY,
_CA_PATH; an account that may manage nodes). The read-only credential's SOLARWINDS_* are never used here, not even to read.
  items [{host, ip, earlier}] -> {result: {host: {text, level, done, times}}, window, window_text, times}: the nodes found by
  name only, then Unmanage only for a node with no unmanage window at all; never Remanage. Bounded: budget seconds in all;
  a restart less than a minute away: skipped. Check (or check: true): nothing invoked.
No credential: {skipped_why} - never a failure. A SolarWinds error is each server's warning, never the job's failure."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

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
        # no retry on a timeout: the restart's lead time is not spent waiting (the run is bounded - budget)
        return rs.Manager(url, user, password, verify=verify, ca_path=ca if ca and os.path.isfile(ca) else "", timeout=p["timeout"],
                          retries=0), ""
    except sw.SwisError as e:
        return None, "the credential's address: %s" % e
    except (OSError, ValueError) as e:
        return None, "the credential's CA file could not be used (%s)" % e.__class__.__name__


def main():
    m = AnsibleModule(argument_spec=dict(items=dict(type="list", elements="dict", default=[]),
                                         at_utc=dict(type="str", required=True), before=dict(type="int", default=5),
                                         after=dict(type="int", default=10), zone=dict(type="str", default=""),
                                         check=dict(type="bool", default=False), timeout=dict(type="int", default=20),
                                         budget=dict(type="int", default=120)),
                      supports_check_mode=True)
    p = m.params
    win = rs.window(p["at_utc"], p["before"], p["after"])
    c, why = client(p)
    tz = None
    if zoneinfo is not None and p["zone"].strip() and p["zone"].strip().upper() not in ("UTC", "Z", "GMT", "ETC/UTC"):
        try:
            tz = zoneinfo.ZoneInfo(p["zone"].strip())
        except Exception:  # noqa: BLE001 - the window shown in UTC then
            tz = None
    text = rs.shown(win, tz) if win else ""
    if c is None:
        m.exit_json(changed=False, skipped_why=why, result={}, window=win, window_text=text, times=[])
    items = [dict(it, window=win) for it in p["items"]]
    try:
        res = rs.run(c, items, p["at_utc"], p["check"] or m.check_mode, tz, p["budget"])
    except sw.SwisError as e:
        m.exit_json(changed=False, skipped_why="SolarWinds could not be read: %s" % e, result={}, window=win, window_text=text, times=[])
    times = sorted(set(r.get("times") for r in res.values() if r.get("times")))
    m.exit_json(changed=any(r["done"] for r in res.values()), result=res, window=win, window_text=text, times=times)


if __name__ == "__main__":
    main()
