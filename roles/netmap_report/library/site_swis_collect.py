#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read SolarWinds Orion through SWIS's REST API - queries only (plugins/module_utils/site_swis.py, the same reader the
site portal's live Network map uses) - and make the network map: sites and where they are, nodes, links, the WAN, what
changed since the kept copy of the last run. Standard library only - PyMySQL too for the report database (1.96.0).

1.96.0: with the report database's login (db_host ...), the read is the newest SolarWinds snapshot there when it is at
most db_max_age minutes old (plugins/module_utils/site_swis_db.py: the same dict collect() gives), else SolarWinds itself.
Which one it was is source (db-snapshot or swis-live), and why not the snapshot is why_live."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_swis_collect
short_description: Read SolarWinds (SWIS REST, queries only) and make the network map
description:
  - Asks SolarWinds Orion's SWIS REST API (port 17774) for its nodes, NPM's topology (Orion.TopologyConnections, else
    Orion.TopologyData) and the interfaces on those links, and which version it is. Every call is the Query endpoint
    with a SWQL SELECT naming its columns; nothing else is ever sent - it cannot change anything.
  - The account comes from SOLARWINDS_USERNAME and SOLARWINDS_PASSWORD (a "SolarWinds (read-only)" credential), the
    address from SOLARWINDS_URL (the credential) or url; the certificate check from SOLARWINDS_VERIFY and
    SOLARWINDS_CA_PATH (the credential) or validate_certs and ca_path.
  - What could not be read is said (not_read) and the rest still comes back; SolarWinds not reached at all is
    reachable false with the reason and what to do.
options:
  url: {description: "SolarWinds' address (orion.example.mil; https:// and :17774 are added) - the credential's wins.", type: str, default: ''}
  validate_certs: {description: Check SolarWinds' certificate (the credential's SOLARWINDS_VERIFY wins)., type: bool, default: true}
  ca_path: {description: A CA file to check it with (the credential's SOLARWINDS_CA_PATH wins)., type: str, default: ''}
  timeout: {description: Seconds for one request (no answer - asked once more)., type: int, default: 60}
  site_by: {description: "Which node field is its site: Location, or a custom property's name.", type: str, default: Location}
  site_split: {description: "The site is the text before this (Location \"Site A, building 1\" - Site A); '' = the whole value.", type: str, default: ","}
  sites: {description: "Sites' coordinates when SolarWinds has none: {site: {lat, lon}} (netmap_sites).", type: dict, default: {}}
  lat_property: {description: The custom property with a node's latitude ('' = none)., type: str, default: ''}
  lon_property: {description: The custom property with a node's longitude ('' = none)., type: str, default: ''}
  properties: {description: More custom properties shown with each node., type: list, elements: str, default: []}
  warn_util: {description: "A link this busy (%, in or out) is Warning; 0 = never.", type: int, default: 90}
  page: {description: Rows a request (paged)., type: int, default: 1000}
  chunk: {description: Interfaces a request., type: int, default: 500}
  max_nodes: {description: Nodes read at most (the rest - said)., type: int, default: 5000}
  snapshot: {description: "The kept copy of the last run (job artifact netmap_snapshot) to compare with; {} = none.", type: raw, default: {}}
  snapshot_links: {description: "Keep each link's interface names in the copy (false = the ids only, a smaller artifact).", type: bool, default: true}
  last: {description: When the kept copy was made (shown)., type: str, default: ''}
  run: {description: When this run read SolarWinds (shown)., type: str, default: ''}
  view: {description: "The world view's first look: globe or flat (netmap_view).", type: str, default: globe}
  no_site: {description: The site of nodes without one., type: str, default: No site}
  db_host: {description: "The report database's server ('' = SolarWinds itself, always).", type: str, default: ''}
  db_port: {description: Its port., type: int, default: 3309}
  db_database: {description: The database., type: str, default: site_reports}
  db_user: {description: The account (the reader is enough)., type: str, default: ''}
  db_password: {description: Its password., type: str, default: ''}
  db_tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  db_ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  db_timeout: {description: Seconds to connect., type: int, default: 10}
  db_max_age: {description: "Minutes a snapshot may be old (solarwinds_db_max_age); older - SolarWinds itself.", type: int, default: 20}
author: site automation
'''

RETURN = r'''
reachable: {description: SolarWinds answered the login and the node query., type: bool}
error: {description: Why it could not be read (with what to do)., type: str}
version: {description: "Orion's version: platform, npm, modules, engines.", type: dict}
not_read: {description: "What could not be read: [{what, why}].", type: list}
map: {description: "sites, nodes, links, unplaced, counts, hidden_links, cut, topology.", type: dict}
changes: {description: "Since the kept copy: [{kind, what, site, detail}] (none without one).", type: list}
compared: {description: There was a kept copy to compare with., type: bool}
data: {description: The page's data (the map's script reads it)., type: dict}
snapshot: {description: The copy to keep for the next run (job artifact netmap_snapshot)., type: dict}
source: {description: "Where the read came from: db-snapshot (the report database) or swis-live.", type: str}
snapshot_at: {description: When SolarWinds was read., type: str}
age_seconds: {description: The read's age when this run used it., type: int}
why_live: {description: Why it was not the database's snapshot (empty when it was)., type: list}
'''

import os
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_swis
from ansible.module_utils import site_swis_db
from ansible.module_utils import site_report_db


def main():
    module = AnsibleModule(
        argument_spec=dict(url=dict(type="str", default=""), validate_certs=dict(type="bool", default=True),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=60),
                           site_by=dict(type="str", default="Location"), site_split=dict(type="str", default=","),
                           sites=dict(type="dict", default={}), lat_property=dict(type="str", default=""),
                           lon_property=dict(type="str", default=""), properties=dict(type="list", elements="str", default=[]),
                           warn_util=dict(type="int", default=90), page=dict(type="int", default=1000),
                           chunk=dict(type="int", default=500), max_nodes=dict(type="int", default=5000),
                           snapshot=dict(type="raw", default={}), snapshot_links=dict(type="bool", default=True), last=dict(type="str", default=""),
                           run=dict(type="str", default=""), view=dict(type="str", default="globe"),
                           no_site=dict(type="str", default="Not placed"), site_pattern=dict(type="str", default=""),
                           hierarchy=dict(type="list", elements="raw", default=[]), roles=dict(type="dict", default={}),
                           discover=dict(type="bool", default=True),
                           db_host=dict(type="str", default=""), db_port=dict(type="int", default=3309),
                           db_database=dict(type="str", default="site_reports"), db_user=dict(type="str", default=""),
                           db_password=dict(type="str", default="", no_log=True), db_tls=dict(type="bool", default=False),
                           db_ca_file=dict(type="str", default=""), db_timeout=dict(type="int", default=10),
                           db_max_age=dict(type="int", default=20)),
        supports_check_mode=True,
    )
    p = module.params
    bad = site_swis.check_opts({"site_pattern": p["site_pattern"], "hierarchy": p["hierarchy"], "roles": p["roles"]})
    if bad:                                                # before anything is read: said, nothing asked of SolarWinds
        module.fail_json(msg="The network map's settings: " + "; ".join(bad))
    env = os.environ
    opts = {"site_by": p["site_by"] if str(p["site_by"]).lower() not in ("auto", "location") else "",
            "lat_property": p["lat_property"], "lon_property": p["lon_property"], "discover": p["discover"],
            "properties": p["properties"] + [h.get("value") for h in p["hierarchy"] if isinstance(h, dict) and h.get("from") == "property"],
            "page": p["page"], "chunk": p["chunk"], "max_nodes": p["max_nodes"]}
    why, raw = [], None
    url = (env.get("SOLARWINDS_URL") or p["url"] or "").strip()
    if p["db_host"].strip():               # 1.96.0: the report database's snapshot first
        if not site_report_db.HAS_PYMYSQL:
            why.append("the report database: " + site_report_db.NO_PYMYSQL)
        else:
            dbp = {"host": p["db_host"].strip(), "port": p["db_port"], "database": p["db_database"], "user": p["db_user"],
                   "password": p["db_password"], "tls": p["db_tls"], "ca_file": p["db_ca_file"], "connect_timeout": p["db_timeout"]}
            conn = None
            try:
                conn, _tls = site_report_db.connect(dbp)
                # 1.96.1 (review P1-2): only a snapshot of the SolarWinds this template reads (its address, when it has one)
                raw = site_swis_db.answer(opts, conn=conn, client=None, max_age=max(0, p["db_max_age"]) * 60, why=why, orion=url)
            except Exception as e:  # noqa: BLE001 - not reached, a login refused: said, then SolarWinds itself
                why.append("the report database could not be reached: %s" % site_swis_db.short_error(site_report_db.explain(e, dbp)))
            finally:
                try:
                    if conn is not None:
                        conn.close()
                except Exception:   # noqa: BLE001
                    pass
    else:
        why.append("the report database is not used (no \"Report database\" credential on this template, or netmap_from_db: false)")
    user, password = env.get("SOLARWINDS_USERNAME", ""), env.get("SOLARWINDS_PASSWORD", "")
    if raw is not None:
        client_url = url
    elif not user or not password:
        module.fail_json(msg='No SolarWinds account: attach a credential of type "SolarWinds (read-only)" to this job template '
                             '(aap/credential_types/solarwinds.yml, docs/NETWORK_MAP.md).%s'
                             % ((" The report database's snapshot was not used: %s." % "; ".join(why)) if p["db_host"].strip() else ""))
    elif not url:
        module.fail_json(msg="No SolarWinds address: put it in the SolarWinds (read-only) credential, or set netmap_solarwinds_url "
                             "(e.g. orion.yoursite.mil).")
    if raw is None:
        verify = p["validate_certs"]
        if str(env.get("SOLARWINDS_VERIFY", "")).strip():
            verify = str(env["SOLARWINDS_VERIFY"]).strip().lower() not in ("0", "false", "no", "off")
        ca = (env.get("SOLARWINDS_CA_PATH") or p["ca_path"] or "").strip()
        if verify and ca and not os.path.isfile(ca):
            module.fail_json(msg="The CA file %s is not there where the job runs (netmap_ca_path, or the credential's CA file): a path "
                                 "inside the project, e.g. {{ playbook_dir }}/files/ca/orion-ca.pem" % ca)
        try:
            client = site_swis.Client(url, user, password, verify=verify, ca_path=ca, timeout=p["timeout"])
        except site_swis.SwisError as e:                   # not https, or a login in the address: said, the address not repeated
            module.fail_json(msg="netmap_solarwinds_url / the credential's address: %s" % e)
        raw = site_swis_db.answer(opts, conn=None, client=client)
        client_url = client.url
    m = site_swis.model(raw, {"site_by": p["site_by"], "site_split": p["site_split"], "sites": p["sites"],
                              "lat_property": p["lat_property"], "lon_property": p["lon_property"], "properties": p["properties"],
                              "warn_util": p["warn_util"], "no_site": p["no_site"], "site_pattern": p["site_pattern"],
                              "hierarchy": p["hierarchy"], "roles": p["roles"],
                              "prev_fingerprint": p["snapshot"].get("h") if isinstance(p["snapshot"], dict) else ""})
    prev = p["snapshot"] if isinstance(p["snapshot"], dict) else {}
    snap = site_swis.snapshot(m, prev, p["snapshot_links"]) if raw["reachable"] else {}     # not read: nothing kept, the copy before stays the baseline
    compared = bool(raw["reachable"] and prev.get("v") == 2 and isinstance(prev.get("n"), dict))   # an empty inventory's copy is a baseline too
    changes = site_swis.compare(m, prev) if compared else []
    data = site_swis.page_data(m, last=p["last"] if compared else "", run=p["run"], view=p["view"],
                               source=site_swis_db.source_text(raw, (raw.get("version") or {}).get("platform") or ""))
    module.exit_json(changed=False, reachable=raw["reachable"], error=raw["error"], version=raw.get("version") or {},
                     not_read=raw.get("not_read") or [], map=m, changes=changes, compared=compared, data=data, snapshot=snap,
                     url=raw.get("url") or client_url, seconds=raw.get("seconds", 0), queries=raw.get("queries", 0),
                     read_at=raw.get("read_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     source=raw.get("source", site_swis_db.SWIS_LIVE), snapshot_at=raw.get("snapshot_at") or "",
                     age_seconds=raw.get("age_seconds") or 0, why_live=why if raw.get("source") != site_swis_db.DB_SNAPSHOT else [])


if __name__ == "__main__":
    main()
