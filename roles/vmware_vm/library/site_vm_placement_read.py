#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.116.6: the outage review's read of the VM placement history (report database, read only): the VMs on each lost
datastore or host as of the time the outage began (plugins/module_utils/site_vm_placement.py). Never fails: a database
that cannot be read is one warning, and the review reads vCenter now instead (or says the VMs were not read)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vm_placement_read
short_description: The VMs on datastores and hosts as of a time, from the report database's placement history
description:
  - For each item {vcenter, kind (datastore or host), name, at}, the VMs on it as of at - the newest whole snapshot at or
    before at, with the changes after it. Reads only.
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account (the reader is enough)., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
  items: {description: "What to look up: [{vcenter, kind, name, at}].", type: list, elements: dict, default: []}
author: site automation
'''

RETURN = r'''
ok: {description: The database was read., type: bool}
error: {description: Why not., type: str}
placements: {description: "Each item with as_of ('' = no snapshot that old), source snapshot and vms [{name, moid, host, cluster, power, datastores}].", type: list}
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db
from ansible.module_utils import site_vm_placement as vp


def main():
    module = AnsibleModule(argument_spec=dict(db.ARGS, items=dict(type="list", elements="dict", default=[])), supports_check_mode=True)
    p = module.params
    res = dict(changed=False, ok=False, error="", placements=[])
    if not p["items"]:
        res["ok"] = True
        module.exit_json(**res)
    if not db.HAS_PYMYSQL:
        res["error"] = db.NO_PYMYSQL
    else:
        conn = None
        try:
            conn, _ = db.connect(p)
            cur = conn.cursor()
            if vp.schema(cur, False)[0]:
                res["placements"] = vp.lookup(cur, p["items"])
                res["ok"] = True
            else:
                res["error"] = "no VM placement history yet (schedule VMware - VM placement snapshot)"
        except Exception as e:      # noqa: BLE001
            res["error"] = db.explain(e, p)
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:       # noqa: BLE001
                pass
    if res["error"]:
        module.warn("The VM placement history: %s - the review reads vCenter now for the VMs instead" % res["error"])
    module.exit_json(**res)


if __name__ == "__main__":
    main()
