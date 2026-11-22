#!/usr/bin/python
# -*- coding: utf-8 -*-
"""1.101.2: the deploy's look at the report database login it gives the portal (the secret site-portal-reportdb): it logs
in, reads its own grants and whether the AD job's runs are there yet. Read only, never fails the deploy: what it finds is
said (a login that can write: a warning - the portal only reads, the read-only account report_reader is enough).
Runs where the deploy runs (the execution environment: PyMySQL)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_portal_db_check
short_description: Look at the report database login the portal is given (read only)
options:
  host: {description: The database server., type: str, required: true}
  port: {description: Its port., type: int, default: 3309}
  database: {description: The database., type: str, required: true}
  user: {description: The account., type: str, required: true}
  password: {description: Its password., type: str, required: true}
  tls: {description: Use TLS and check the server's certificate., type: bool, default: false}
  ca_file: {description: The CA file the certificate is checked with., type: str, default: ''}
  connect_timeout: {description: Seconds to wait for the connection., type: int, default: 10}
author: site automation
'''

RETURN = r'''
ok: {description: The login worked., type: bool}
error: {description: Why not., type: str}
writes: {description: "The login has more than SELECT and SHOW VIEW (INSERT, UPDATE, DELETE, CREATE, ALL ...).", type: bool}
privileges: {description: The privileges its grants name., type: list}
ad_runs: {description: "The AD job's runs in ad_run (-1: the table is not there yet).", type: int}
'''

import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils import site_report_db as db

READ = {"SELECT", "SHOW VIEW", "USAGE"}


def privileges(grants):
    """SHOW GRANTS lines -> the privileges they name (GRANT SELECT, SHOW VIEW ON `x`.* TO ... -> SELECT, SHOW VIEW)."""
    out = set()
    for g in grants:
        m = re.match(r"^GRANT (.+?) ON ", str(g or ""), re.I)
        if m:
            out |= set(p.strip().upper() for p in m.group(1).split(",") if p.strip())
    return sorted(out)


def main():
    module = AnsibleModule(argument_spec=dict(db.ARGS), supports_check_mode=True)
    p = module.params
    res = {"changed": False, "ok": False, "error": "", "writes": False, "privileges": [], "ad_runs": -1}
    if not db.HAS_PYMYSQL:
        res["error"] = db.NO_PYMYSQL
        module.exit_json(**res)
    conn = None
    try:
        conn, _tls = db.connect(p)
        cur = conn.cursor()
        cur.execute("SHOW GRANTS FOR CURRENT_USER()")
        res["privileges"] = privileges([r[0] for r in cur.fetchall()])
        res["writes"] = any(x not in READ for x in res["privileges"])
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'ad_run'")
        if cur.fetchone() is not None:
            cur.execute("SELECT COUNT(*) FROM ad_run")
            res["ad_runs"] = int(cur.fetchone()[0])
        res["ok"] = True
    except Exception as e:      # noqa: BLE001 - said, never the deploy's failure
        res["error"] = db.explain(e, p)
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:       # noqa: BLE001
            pass
    module.exit_json(**res)


if __name__ == "__main__":
    main()
