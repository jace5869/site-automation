#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read SolarWinds once and keep it in the report database as one snapshot (1.96.0; plugins/module_utils/site_swis_db.py,
docs/REPORT_DATABASE.md "The SolarWinds snapshot"). SolarWinds is only read (SWQL queries); the database is written."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_swis_snapshot
short_description: Read SolarWinds (SWIS REST, queries only) and keep it as a snapshot in the report database
description:
  - Takes the writers' lock (GET_LOCK) so two runs never write together. Another run holding it - this one stops, said.
  - Makes the tables when they are not there (create).
  - Reads SolarWinds - the network map's read (nodes with every custom property, links, the links' interfaces, the
    version) and the nodes' poll, CPU and memory, their IPs, the links' interface errors, the active alerts, the events
    since the newest kept, SAM's applications and its components not up, the volumes, VIM's hosts and VMs. Each with
    only the columns this Orion has (Metadata.Property). A part this Orion lacks (no SAM, no VIM) is said, not an error.
  - Writes it in one transaction as one snapshot, then deletes the rows of the older snapshots (keep_snapshots), the
    snapshot rows older than keep_days and the events older than event_days.
  - SolarWinds not read - a snapshot row with ok 0 and the reason, and the module fails. The database not reached -
    fails. Check mode - reads SolarWinds and the database, writes nothing.
  - The SolarWinds account from SOLARWINDS_USERNAME and SOLARWINDS_PASSWORD (a "SolarWinds (read-only)" credential),
    the address from SOLARWINDS_URL or url.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  create: {description: Make the tables when they are not there., type: bool, default: true}
  url: {description: "SolarWinds' address (the credential's wins).", type: str, default: ''}
  validate_certs: {description: Check SolarWinds' certificate (the credential's SOLARWINDS_VERIFY wins)., type: bool, default: true}
  ca_path: {description: A CA file to check it with (the credential's SOLARWINDS_CA_PATH wins)., type: str, default: ''}
  timeout: {description: Seconds for one SolarWinds request (no answer - asked once more)., type: int, default: 60}
  properties: {description: "The node custom properties kept: all, or a list of names.", type: raw, default: all}
  skip_properties: {description: Node custom properties never kept., type: list, elements: str, default: []}
  parts: {description: "The other tables read (node, ip, iface, alert, event, app, component, volume, vim).", type: list, elements: str,
          default: [node, ip, iface, alert, event, app, component, volume, vim]}
  page: {description: Rows a request (paged)., type: int, default: 1000}
  chunk: {description: Ids a request., type: int, default: 500}
  max_nodes: {description: Nodes read at most., type: int, default: 20000}
  max_rows: {description: Rows of any other table at most., type: int, default: 50000}
  event_hours: {description: The first run's events - this many hours back., type: int, default: 24}
  keep_snapshots: {description: Whole snapshots whose rows are kept., type: int, default: 12}
  keep_days: {description: "Snapshot rows (the counts) kept this many days (0 = always).", type: int, default: 400}
  event_days: {description: "Events kept this many days (0 = always).", type: int, default: 400}
  job: {description: This job's ID (0 outside AAP)., type: int, default: 0}
author: site automation
'''

RETURN = r'''
ok: {description: The snapshot was written whole (in check mode - read)., type: bool}
snap_id: {description: The snapshot's id (0 in check mode)., type: int}
counts: {description: "nodes, nodes_down, nodes_warning, links, interfaces, alerts, events, apps, apps_down, components, volumes, vim_hosts, vim_vms.", type: dict}
not_read: {description: "What could not be read: [{what, why, absent}] (absent - this Orion has no such module).", type: list}
seconds: {description: The read's seconds., type: float}
queries: {description: SWQL queries sent., type: int}
pruned: {description: Rows deleted per table., type: dict}
created: {description: The tables were made this run., type: bool}
version: {description: Orion's version (platform, modules)., type: dict}
'''

import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_swis
from ansible.module_utils import site_swis_db as swdb


def shut(conn):
    try:
        if conn is not None:
            conn.close()
    except Exception:           # noqa: BLE001
        pass


def main():
    parts = list(swdb.PARTS)
    module = AnsibleModule(
        argument_spec=dict(db.ARGS, create=dict(type="bool", default=True), url=dict(type="str", default=""),
                           validate_certs=dict(type="bool", default=True), ca_path=dict(type="str", default=""),
                           timeout=dict(type="int", default=60), properties=dict(type="raw", default="all"),
                           skip_properties=dict(type="list", elements="str", default=[]),
                           parts=dict(type="list", elements="str", default=parts), page=dict(type="int", default=1000),
                           chunk=dict(type="int", default=500), max_nodes=dict(type="int", default=20000),
                           max_rows=dict(type="int", default=50000), event_hours=dict(type="int", default=24),
                           keep_snapshots=dict(type="int", default=12), keep_days=dict(type="int", default=400),
                           event_days=dict(type="int", default=400), job=dict(type="int", default=0)),
        supports_check_mode=True)
    p = module.params
    env = os.environ
    where = "%s:%s/%s" % (p["host"], p["port"], p["database"])
    res = {"changed": False, "ok": False, "snap_id": 0, "counts": {}, "not_read": [], "seconds": 0, "queries": 0, "pruned": {},
           "created": False, "version": {}, "where": where, "tls": ""}
    bad = [x for x in p["parts"] if x not in parts]
    if bad:
        module.fail_json(msg="solarwinds_snapshot_parts: %s is not one of %s" % (", ".join(bad), ", ".join(parts)), **res)
    props = p["properties"]
    if not (isinstance(props, list) or (isinstance(props, str) and props.strip().lower() in ("all", "*"))):
        module.fail_json(msg="solarwinds_snapshot_properties must be all or a list of node custom properties' names", **res)
    user, password = env.get("SOLARWINDS_USERNAME", ""), env.get("SOLARWINDS_PASSWORD", "")
    url = (env.get("SOLARWINDS_URL") or p["url"] or "").strip()
    if not user or not password:
        module.fail_json(msg='No SolarWinds account: attach a credential of type "SolarWinds (read-only)" to this job template '
                             '(aap/credential_types/solarwinds.yml).', **res)
    if not url:
        module.fail_json(msg="No SolarWinds address: put it in the SolarWinds (read-only) credential, or set solarwinds_url "
                             "(e.g. orion.yoursite.mil).", **res)
    verify = p["validate_certs"]
    if str(env.get("SOLARWINDS_VERIFY", "")).strip():
        verify = str(env["SOLARWINDS_VERIFY"]).strip().lower() not in ("0", "false", "no", "off")
    ca = (env.get("SOLARWINDS_CA_PATH") or p["ca_path"] or "").strip()
    if verify and ca and not os.path.isfile(ca):
        module.fail_json(msg="The CA file %s is not there where the job runs (solarwinds_ca_path, or the credential's CA file)" % ca, **res)
    try:
        client = site_swis.Client(url, user, password, verify=verify, ca_path=ca, timeout=p["timeout"])
    except site_swis.SwisError as e:
        module.fail_json(msg="solarwinds_url / the credential's address: %s" % e, **res)
    if not db.HAS_PYMYSQL:
        module.fail_json(msg="The report database %s: %s" % (where, db.NO_PYMYSQL), **res)
    conn = None
    try:
        conn, res["tls"] = db.connect(p)
    except Exception as e:      # noqa: BLE001 - the one thing this job is for: red, with what to change
        module.fail_json(msg="The report database %s could not be reached: %s" % (where, db.explain(e, p)), **res)
    try:
        if not swdb.lock(conn):
            shut(conn)
            module.warn("Another SolarWinds snapshot run is writing (the database lock %s is held): this run wrote nothing. "
                        "Turn off Concurrent jobs on the template." % swdb.LOCK)
            res["ok"] = True
            res["skipped_lock"] = True
            module.exit_json(**res)
        there, res["created"] = swdb.schema(conn, p["create"] and not module.check_mode)
        if not there:
            shut(conn)
            module.fail_json(msg="The SolarWinds tables are not in %s%s - make them once, docs/REPORT_DATABASE.md (The SolarWinds "
                                 "snapshot, By hand)" % (where, " (check mode: nothing made)" if module.check_mode and p["create"]
                                                         else " (solarwinds_db_create: false)"), **res)
        after = swdb.newest_event(conn)
    except Exception as e:      # noqa: BLE001
        shut(conn)
        module.fail_json(msg="The report database %s: %s" % (where, db.explain(e, p)), **res)
    snap = swdb.read_swis(client, {"properties": props, "skip": p["skip_properties"], "parts": p["parts"], "page": p["page"],
                                   "chunk": p["chunk"], "max_nodes": p["max_nodes"], "max_rows": p["max_rows"],
                                   "events_after": after, "event_hours": p["event_hours"]})
    raw = snap["raw"]
    res.update(seconds=snap.get("seconds", 0), queries=snap.get("queries", 0), version=raw.get("version") or {},
               not_read=(raw.get("not_read") or []) + (snap.get("not_read") or []))
    if module.check_mode:
        shut(conn)
        rows = swdb.to_rows(snap, 0) if raw.get("reachable") else None
        res["ok"] = bool(raw.get("reachable"))
        res["counts"] = swdb.counts(rows, snap) if rows else {}
        if not raw.get("reachable"):
            module.fail_json(msg="SolarWinds could not be read: %s" % raw.get("error"), **res)
        module.exit_json(**res)
    try:
        w = swdb.write(conn, snap, p["job"])
        res.update(snap_id=w["snap_id"], ok=w["ok"], counts=w["counts"], changed=True)
        if w["ok"]:
            res["pruned"] = swdb.prune(conn, p["keep_snapshots"], p["keep_days"], p["event_days"])
    except Exception as e:      # noqa: BLE001 - a read-only account, a full disk: nothing of this run is kept
        try:
            conn.rollback()
        except Exception:       # noqa: BLE001
            pass
        swdb.unlock(conn)
        shut(conn)
        module.fail_json(msg="The report database %s: this snapshot was not kept: %s" % (where, db.explain(e, p)), **res)
    swdb.unlock(conn)
    shut(conn)
    if not res["ok"]:
        module.fail_json(msg="SolarWinds could not be read: %s. The report database has a row saying so (sw_snapshot, ok 0); "
                             "the reports go on with the last whole snapshot until solarwinds_db_max_age." % raw.get("error"), **res)
    module.exit_json(**res)


if __name__ == "__main__":
    main()
