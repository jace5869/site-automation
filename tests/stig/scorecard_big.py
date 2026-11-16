#!/usr/bin/env python3
"""A big made-up site for the STIG scorecard (1.68.0): 8 collections, 400 assets, the platforms of the defaults, as
STIG Manager's metrics answer (one row per asset and STIG) and as its findings by rule answer (each rule's hosts).
Seeded: the same numbers every run. tests/test_stig_scorecard.py and the report fit documents use it.

  scorecard_big.py read OUT.json [NOW]     what site_stig_scorecard gives (metrics and rules), NOW 2026-11-13T06:00:00Z"""
import json
import random
import sys

NOW = "2026-11-13T06:00:00Z"
# collection: (its id, its STIGs and how many assets of each, a host name prefix)
SITE = [
    ("11", "SITEA - RHEL 9", [("RHEL_9_STIG", 110), ("Apache_Server_2-4_UNIX_Server_STIG", 18)], "rhel"),
    ("12", "SITEA - RHEL 8", [("RHEL_8_STIG", 54)], "r8app"),
    ("13", "SITEA - Windows Server", [("MS_Windows_Server_2022_STIG", 85), ("MS_SQL_Server_2022_Instance_STIG", 12)], "win"),
    ("14", "SITEA - Windows 11", [("MS_Windows_11_STIG", 64), ("Microsoft_Edge_STIG", 64)], "wks"),
    ("15", "SITEA - VMware", [("VMW_vSphere_8-0_ESXi_STIG", 24), ("VMW_vSphere_8-0_vCenter_STIG", 2)], "esx"),
    ("16", "SITEA - Network", [("Cisco_IOS-XE_Router_NDM_STIG", 22), ("Juniper_SRX_SG_NDM_STIG", 8)], "net"),
    ("17", "SITEB - Storage", [("NetApp_ONTAP_DSC_9-x_STIG", 6)], "nas"),
    ("18", "SITEB - Lab", [("RHEL_9_STIG", 35), ("Zebra_Printer_Generic_STIG", 4)], "lab"),
]
TITLES = {"RHEL": "RHEL %s must %s.", "MS_W": "Windows must %s.", "MS_S": "SQL Server must %s.", "Micr": "Edge must %s.",
          "VMW_": "The ESXi host must %s.", "Cisc": "The router must %s.", "Juni": "The SRX must %s.", "NetA": "ONTAP must %s.",
          "Apac": "Apache must %s.", "Zebr": "The printer must %s."}
WHAT = ["use a FIPS-validated module", "lock an account after three failed logons", "audit privileged commands",
        "set a session timeout of ten minutes", "show the DoD banner before logon", "disable unused services",
        "keep its packages current", "restrict the permissions of its logs", "use NTP with authenticated sources",
        "deny root logins over SSH", "require a password of 15 characters", "remove the default accounts"]


def build(seed=1680):
    rnd = random.Random(seed)
    cols = []
    for cid, name, stigs, pre in SITE:
        hosts = []
        n_hosts = max(n for b, n in stigs)
        for i in range(n_hosts):
            hosts.append("%s%03d" % (pre, i + 1))
        rows, rules = [], {}
        for bench, n in stigs:
            # each STIG has 60 rules: CAT I about 10%, CAT II 75%, CAT III 15%; a host fails a few of them
            base = 200000 + (sum(map(ord, bench)) * 37 % 50000)
            catalog = []
            for k in range(60):
                cat = 1 if k % 10 == 0 else (3 if k % 7 == 0 else 2)
                sv = "SV-%d" % (base + k * 3)
                rid = "%sr%d_rule" % (sv, 900000 + k)
                pfx = TITLES.get(bench[:4], "It must %s.")
                title = pfx % ((bench.split("_")[1], WHAT[k % len(WHAT)]) if pfx.count("%s") == 2 else WHAT[k % len(WHAT)])
                catalog.append((rid, sv, cat, "%s-%05d" % (bench.split("_")[0][:4].upper(), k * 10 + 10), title))
            badness = rnd.random()
            for h in hosts[:n]:
                fails = [r for r in catalog if rnd.random() < (0.02 + 0.10 * badness) * (0.5 if r[2] == 1 else 1.0)]
                c = {1: 0, 2: 0, 3: 0}
                for r in fails:
                    c[r[2]] += 1
                    rec = rules.setdefault(r[0], {"rule": r[0], "sv": r[1], "cat": r[2], "version": r[3], "title": r[4],
                                                  "assets": [], "count": 0, "stig": bench})
                    rec["assets"].append(h)
                    rec["count"] += 1
                rows.append({"asset": h, "stig": bench, "cat1": c[1], "cat2": c[2], "cat3": c[3],
                             "min_ts": "2025-%02d-%02dT10:00:00Z" % (rnd.randint(1, 12), rnd.randint(1, 28)), "max_ts": "2026-11-0%dT10:00:00Z" % rnd.randint(1, 9)})
        rl = sorted(rules.values(), key=lambda r: (r["cat"], -r["count"], r["sv"]))
        tot = dict((k, sum(r["cat%d" % k] for r in rows)) for k in (1, 2, 3))
        cols.append({"id": cid, "name": name, "assets": len(hosts), "cat1": tot[1], "cat2": tot[2], "cat3": tot[3], "rules": rl,
                     "checklists": rows, "asset_stigs": dict((h, sorted(set(r["stig"] for r in rows if r["asset"] == h))) for h in hosts),
                     "hosts": "listed", "error": "", "counts_from": "metrics", "timing": {"metrics": 0.4, "rules": 3.1}})
    return {"ok": True, "error": "", "read_at": NOW, "collections": cols, "missing": []}


if __name__ == "__main__":
    if sys.argv[1] == "read":
        d = build()
        if len(sys.argv) > 3:
            d["read_at"] = sys.argv[3]
        json.dump(d, open(sys.argv[2], "w"))
