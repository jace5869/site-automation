#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read SolarWinds Orion through SWIS's REST API - queries only (plugins/module_utils/site_swis.py, the same reader the
site portal's live Network map uses) - and make the network map: sites and where they are, nodes, links, the WAN, what
changed since the kept copy of the last run. Standard library only."""
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
'''

import os
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_swis


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
                           no_site=dict(type="str", default="No site")),
        supports_check_mode=True,
    )
    p = module.params
    env = os.environ
    user, password = env.get("SOLARWINDS_USERNAME", ""), env.get("SOLARWINDS_PASSWORD", "")
    url = (env.get("SOLARWINDS_URL") or p["url"] or "").strip()
    if not user or not password:
        module.fail_json(msg='No SolarWinds account: attach a credential of type "SolarWinds (read-only)" to this job template '
                             '(aap/credential_types/solarwinds.yml, docs/NETWORK_MAP.md).')
    if not url:
        module.fail_json(msg="No SolarWinds address: put it in the SolarWinds (read-only) credential, or set netmap_solarwinds_url "
                             "(e.g. orion.yoursite.mil).")
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
    raw = site_swis.collect(client, {"site_by": p["site_by"], "lat_property": p["lat_property"], "lon_property": p["lon_property"],
                                     "properties": p["properties"], "page": p["page"], "chunk": p["chunk"], "max_nodes": p["max_nodes"]})
    m = site_swis.model(raw, {"site_by": p["site_by"], "site_split": p["site_split"], "sites": p["sites"],
                              "lat_property": p["lat_property"], "lon_property": p["lon_property"], "properties": p["properties"],
                              "warn_util": p["warn_util"], "no_site": p["no_site"]})
    prev = p["snapshot"] if isinstance(p["snapshot"], dict) else {}
    snap = site_swis.snapshot(m, prev, p["snapshot_links"]) if raw["reachable"] else {}     # not read: nothing kept, the copy before stays the baseline
    compared = bool(raw["reachable"] and prev.get("v") == 2 and isinstance(prev.get("n"), dict))   # an empty inventory's copy is a baseline too
    changes = site_swis.compare(m, prev) if compared else []
    data = site_swis.page_data(m, last=p["last"] if compared else "", run=p["run"], view=p["view"],
                               source="SolarWinds%s (SWIS, read-only)" % ((" " + raw["version"]["platform"]) if raw.get("version", {}).get("platform") else ""))
    module.exit_json(changed=False, reachable=raw["reachable"], error=raw["error"], version=raw.get("version") or {},
                     not_read=raw.get("not_read") or [], map=m, changes=changes, compared=compared, data=data, snapshot=snap,
                     url=client.url, seconds=raw.get("seconds", 0), queries=raw.get("queries", 0),
                     read_at=raw.get("read_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


if __name__ == "__main__":
    main()
