#!/usr/bin/env python3
"""1.122.0: Windows - local group members (playbooks/win_local_groups.yml, roles/win_local_groups,
plugins/filter/win_local_groups_filters.py): the script's output parsed; a group found by its SID on a German Windows
counts as the same group; an orphaned SID listed, never dropped; the usual members (80% of the servers that have the
group; fewer than 3 servers: none unusual; off); an enabled local account, no built-in one, in Administrators is a
warning, the built-in Administrator and a disabled one are not; the servers not read; the report (tiles, sections, the
Group and Member filters) rendered in the atlas look; the workbook's Group column; the PowerShell parses (and, with pwsh,
runs read only and prints its one line); the playbook's syntax.
  python3 tests/test_win_local_groups.py"""
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import site_filters as sf  # noqa: E402
import win_local_groups_filters as w  # noqa: E402

D = "S-1-5-21-1111111111-2222222222-3333333333"
RD = "S-1-5-32-555"


def mem(name, sid, typ="Group", src="Domain", **k):
    return dict(name=name, sid=sid, type=typ, source=src, **k)


def admins(comp, n, extra=(), name="Administrators", method="Get-LocalGroupMember"):
    local = "S-1-5-21-90%d-91%d-92%d" % (n, n, n)
    return {"asked": "Administrators", "sid": "S-1-5-32-544", "name": name, "found": True, "method": method, "error": "",
            "members": [mem("%s\\Administrator" % comp, local + "-500", "User", "Local", enabled=True, last_logon="2026-02-11"),
                        mem("CORP\\Domain Admins", D + "-512")] + list(extra)}


def rdp(members, name="Remote Desktop Users"):
    return {"asked": "Remote Desktop Users", "sid": RD, "name": name, "found": True, "method": "Get-LocalGroupMember", "members": members}


def result(comp, groups):
    return w.win_local_groups_parse(["x", w.MARK + json.dumps({"computer": comp, "domain": "corp.example.mil", "groups": groups})])


def fleet():
    """5 servers read (SRV-WEB01 a German Windows), 1 not reached, 1 whose script printed nothing."""
    hd = mem("CORP\\Helpdesk-RDP", D + "-4202")
    return [
        {"host": "SRV-APP01.corp.example.mil", "result": result("SRV-APP01", [admins("SRV-APP01", 1), rdp([hd])])},
        {"host": "SRV-APP02.corp.example.mil", "result": result("SRV-APP02", [admins("SRV-APP02", 2, [
            mem("SRV-APP02\\svc_deploy", "S-1-5-21-902-912-922-1001", "User", "Local", enabled=True, last_logon="2026-11-22")]), rdp([hd])])},
        {"host": "SRV-DB01.corp.example.mil", "result": result("SRV-DB01", [admins("SRV-DB01", 3, [mem("CORP\\jdoe", D + "-7781", "User")]), rdp([hd])])},
        {"host": "SRV-FS01.corp.example.mil", "result": result("SRV-FS01", [admins("SRV-FS01", 4, [
            mem("", D + "-9907", "Unknown", "Unresolved"),
            mem("SRV-FS01\\olduser", "S-1-5-21-904-914-924-1002", "User", "Local", enabled=False)], method="ADSI"), rdp([hd])])},
        {"host": "SRV-WEB01.corp.example.mil", "result": result("SRV-WEB01", [admins("SRV-WEB01", 5, name="Administratoren"),
                                                                            rdp([], name="Remotedesktopbenutzer")])},
        {"host": "SRV-WEB02.corp.example.mil", "result": {}, "unreached": "WinRM: the connection timed out"},
        {"host": "SRV-WEB03.corp.example.mil", "result": w.win_local_groups_parse(["no marker here"])},
    ]


def pair(model, name, group="Administrators"):
    return next(p for p in model["pairs"].values() if p["member"]["name"] == name and p["label"] == group)


class Parse(unittest.TestCase):
    def test_parse(self):
        self.assertIn("error", w.win_local_groups_parse([]))
        self.assertIn("error", w.win_local_groups_parse([w.MARK + "{not json"]))
        self.assertEqual(w.win_local_groups_parse(["a", w.MARK + '{"groups": []}'])["groups"], [])


class Model(unittest.TestCase):
    def setUp(self):
        self.m = w.win_local_groups_model(fleet(), "auto")

    def test_read_and_not_read(self):
        self.assertEqual(len(self.m["read"]), 5)
        why = dict((u["host"], u["why"]) for u in self.m["unread"])
        self.assertIn("not reached", why["SRV-WEB02.corp.example.mil"])
        self.assertIn("no result", why["SRV-WEB03.corp.example.mil"] + "no result")
        self.assertEqual(self.m["total"], 7)

    def test_german_names_by_sid(self):
        # Administratoren and Remotedesktopbenutzer are the same groups as on the English servers
        self.assertEqual([g["label"] for g in self.m["groups"]], ["Administrators", "Remote Desktop Users"])
        self.assertEqual(pair(self.m, "CORP\\Domain Admins")["hosts"].__len__(), 5)
        web01 = next(s for s in self.m["read"] if s["computer"] == "SRV-WEB01")
        self.assertEqual(web01["groups"][0]["local_name"], "Administratoren")

    def test_orphan_sid_kept(self):
        p = pair(self.m, "unresolved SID " + D + "-9907")
        self.assertEqual(p["member"]["source"], "Unresolved")
        self.assertEqual(p["hosts"], ["SRV-FS01.corp.example.mil"])

    def test_local_names_join(self):
        # SRV-APP01\Administrator and SRV-APP02\Administrator are one member, .\Administrator, on all 5
        p = pair(self.m, ".\\Administrator")
        self.assertEqual(len(p["hosts"]), 5)
        self.assertFalse(p["unusual"])
        self.assertTrue(p["member"]["builtin"])

    def test_unusual(self):
        self.assertTrue(pair(self.m, "CORP\\jdoe")["unusual"])
        self.assertFalse(pair(self.m, "CORP\\Domain Admins")["unusual"])
        # Helpdesk-RDP: on 4 of 5 servers that have Remote Desktop Users = 80%: usual
        hd = pair(self.m, "CORP\\Helpdesk-RDP", "Remote Desktop Users")
        self.assertEqual((len(hd["hosts"]), hd["of"]), (4, 5))
        self.assertFalse(hd["unusual"])
        strict = w.win_local_groups_model(fleet(), 90)
        self.assertTrue(pair(strict, "CORP\\Helpdesk-RDP", "Remote Desktop Users")["unusual"])
        off = w.win_local_groups_model(fleet(), "off")
        self.assertFalse(any(p["unusual"] for p in off["pairs"].values()))
        few = w.win_local_groups_model(fleet()[:2], "auto")          # 2 servers: nothing is unusual
        self.assertFalse(any(p["unusual"] for p in few["pairs"].values()))

    def test_model_is_plain_json(self):
        json.dumps(self.m)


class Findings(unittest.TestCase):
    def test_enabled_local_admin(self):
        m = w.win_local_groups_model(fleet(), "auto")
        f = w.win_local_groups_findings(m, "SRV-APP02.corp.example.mil")
        self.assertEqual([(x["severity"], x["summary"]) for x in f],
                         [("warning", ".\\svc_deploy is an enabled local account in Administrators")])
        # the built-in Administrator (enabled) and a disabled local account are no finding; unusual is the report's
        self.assertEqual(w.win_local_groups_findings(m, "SRV-APP01.corp.example.mil"), [])
        self.assertEqual(w.win_local_groups_findings(m, "SRV-FS01.corp.example.mil"), [])
        self.assertEqual(w.win_local_groups_findings(m, "SRV-WEB02.corp.example.mil"), [])

    def test_group_not_read(self):
        bad = fleet()[:1]
        bad[0]["result"]["groups"].append({"asked": "Backup Operators", "sid": "S-1-5-32-551", "name": "Backup Operators",
                                           "found": False, "error": "ADSI failed: access denied", "members": []})
        m = w.win_local_groups_model(bad, "auto")
        f = w.win_local_groups_findings(m, "SRV-APP01.corp.example.mil")
        self.assertEqual(f[0]["severity"], "warning")
        self.assertIn("access denied", f[0]["summary"])


class ReviewFixes(unittest.TestCase):
    """Codex review of 1.122.0: an account's state per server, groups with near names, servers with the same short name."""

    def test_enabled_on_one_server_only(self):
        hosts = fleet()[:3]
        for i, h in enumerate(hosts):
            h["result"]["groups"][0]["members"].append(mem("%s\\svc" % h["result"]["computer"], "S-1-5-21-7%d-1-1-1001" % i, "User", "Local",
                                                           enabled=(i == 0)))
        m = w.win_local_groups_model(hosts, "auto")
        p = pair(m, ".\\svc")
        self.assertEqual(len(p["hosts"]), 3)
        self.assertEqual(p["admin_hosts"], ["SRV-APP01.corp.example.mil"])
        self.assertEqual(len(w.win_local_groups_findings(m, "SRV-DB01.corp.example.mil")), 0)
        top = w.win_local_groups_sections(m, True)[0]
        row = next(r for r in top["rows"] if r[0] == ".\\svc")
        self.assertEqual(row[4], "SRV-APP01")
        sh = next(x for x in w.win_local_groups_sheets(m) if x["name"] == "By member")
        self.assertIn("SRV-DB01.corp.example.mil: S-1-5-21-72-1-1-1001", next(r for r in sh["rows"] if r[0] == ".\\svc")[3])

    def test_near_group_names_stay_apart(self):
        h = fleet()[:1]
        for n in ("Help Desk", "Help-Desk"):
            h[0]["result"]["groups"].append({"asked": n, "sid": "S-1-5-21-5-5-5-%d" % len(n), "name": n, "found": True,
                                             "members": [mem("CORP\\jdoe", D + "-7781", "User")]})
        m = w.win_local_groups_model(h, "auto")
        self.assertEqual(len([p for p in m["pairs"].values() if p["member"]["name"] == "CORP\\jdoe"]), 2)

    def test_same_short_name(self):
        h = fleet()[:2]
        h[1]["host"] = "SRV-APP01.west.example.mil"
        m = w.win_local_groups_model(h, "auto")
        self.assertEqual(sorted(m["shown"].values()), ["SRV-APP01.corp.example.mil", "SRV-APP01.west.example.mil"])


class Report(unittest.TestCase):
    def build(self, web):
        m = w.win_local_groups_model(fleet(), "auto")
        by = dict((s["host"], w.win_local_groups_findings(m, s["host"])) for s in m["read"])
        r = sf.findings_report(by, "Windows - local group members", [], ["local groups"], w.win_local_groups_sections(m, web))
        if web:
            r["facets"] = w.win_local_groups_facets(m)
        return m, r

    def test_tiles_and_sections(self):
        m, r = self.build(True)
        self.assertIn("1 enabled local account in Administrators", r["title"])
        self.assertIn("2 servers not read", r["title"])
        labels = [b["label"] for b in r["summary"]]
        self.assertEqual(labels, ["Servers read", "In Administrators", "Local accounts in Administrators", "Unusual members", "Servers not read"])
        self.assertEqual(r["summary"][0]["value"], "5 of 7")
        titles = [s["title"] for s in r["sections"]]
        self.assertEqual(titles[0], "Unusual members")
        for t in ("By server", "By member", "Not read"):
            self.assertIn(t, titles)
        top = r["sections"][0]
        self.assertEqual(top["rows"][0][0], ".\\svc_deploy")          # worst first
        self.assertEqual(top["row_status"][0], "warning")
        # every member of every server is a row of By server (none dropped), with its group's filter key and its name
        bs = next(s for s in r["sections"] if s["title"] == "By server")
        n = sum(len(g["members"]) for s in m["read"] for g in s["groups"]) + 1          # + Remotedesktopbenutzer (empty)
        self.assertEqual(len(bs["rows"]), n)
        self.assertTrue(all(t["pf"] for t in bs["row_tags"]))
        self.assertIn("unresolved sid", " ".join(t["h"] for t in bs["row_tags"]))

    def test_email_is_cut(self):
        _, r = self.build(False)
        bs = next(s for s in r["sections"] if s["title"] == "By server")
        self.assertNotIn("row_tags", bs)

    def test_rendered(self):
        from test_report_hover import render
        _, r = self.build(True)
        page = render("report_atlas.html.j2", r)
        self.assertIn('id="fl-pf">Group</span>', page)
        self.assertIn('for="fhost">Member</label>', page)
        self.assertIn('data-fv="gs-1-5-32-555"', page)
        self.assertIn('data-h="corp\\jdoe"', page)
        # a page with no facets labels still says Platform and Host (the STIG scorecard)
        r.pop("facets")
        r["facets"] = {"platforms": [{"key": "a", "name": "RHEL 9"}], "host": True}
        page = render("report_atlas.html.j2", r)
        self.assertIn('id="fl-pf">Platform</span>', page)
        self.assertIn('for="fhost">Host</label>', page)

    def test_sheets(self):
        m = w.win_local_groups_model(fleet(), "auto")
        sh = dict((s["name"], s) for s in w.win_local_groups_sheets(m))
        self.assertEqual(list(sh), ["Approved list", "By member", "By server", "Not read"])
        self.assertEqual(sh["By server"]["columns"][:8], ["Server", "Group", "Member", "Type", "Source", "SID", "Enabled", "Servers with this member"])
        self.assertEqual(sh["By member"]["columns"][1], "Group")
        self.assertEqual(len(sh["Not read"]["rows"]), 2)
        self.assertIn(["SRV-WEB01.corp.example.mil", "Administrators", ".\\Administrator"], [r[:3] for r in sh["By server"]["rows"]])
        self.assertIn("Administratoren", [r[10] for r in sh["By server"]["rows"]])


class Inventory(unittest.TestCase):
    """The complete export: every member of every group on every server, one row each, nothing filtered (1.122.0)."""

    def setUp(self):
        self.m = w.win_local_groups_model(fleet(), "auto")
        self.n = sum(len(g["members"]) or 1 for s in self.m["read"] for g in s["groups"])

    def test_rows_complete(self):
        rows = w.win_local_groups_rows(self.m)
        self.assertEqual(len(rows), self.n)
        # the unusual and the unresolved are there like every other member
        names = [r["member"] for r in rows]
        self.assertIn("CORP\\jdoe", names)
        self.assertIn("unresolved SID " + D + "-9907", names)
        adm = [r for r in rows if r["member"] == ".\\Administrator"]
        self.assertEqual(len(adm), 5)
        self.assertEqual(set(r["servers"] for r in adm), {5})
        self.assertEqual(next(r for r in rows if r["member"] == "CORP\\jdoe")["servers"], 1)

    def test_csv(self):
        import csv
        import io
        rows = list(csv.reader(io.StringIO(w.win_local_groups_csv(self.m))))
        self.assertEqual(rows[0][:8], ["server", "group", "member", "type", "source", "sid", "enabled", "servers"])
        self.assertEqual(len(rows) - 1, self.n)

    def test_json(self):
        d = json.loads(w.win_local_groups_json(self.m))
        self.assertEqual(len(d["rows"]), self.n)
        self.assertEqual(len(d["not_read"]), 2)
        for k in ("server", "group", "member", "type", "source", "sid", "enabled", "servers"):
            self.assertIn(k, d["rows"][0])

    def test_operators_by_sid(self):
        for sid, name in (("S-1-5-32-549", "Server Operators"), ("S-1-5-32-550", "Print Operators"), ("S-1-5-32-548", "Account Operators")):
            self.assertEqual(w.WELL_KNOWN[sid], name)
        with open(os.path.join(REPO, "roles", "win_local_groups", "defaults", "main.yml")) as fh:
            import yaml
            self.assertEqual(len(yaml.safe_load(fh)["win_local_groups"]), 7)


APPROVED = {"groups": {"Administrators": ["{computer}\\Administrator", "CORP\\Domain Admins", "CORP\\Break-Glass"],
                       "S-1-5-32-555": ["corp\\helpdesk-rdp"], "Account Operators": []}}


def approved_fleet():
    hosts = fleet()
    for h in hosts:
        r = h.get("result") or {}
        if "groups" in r:
            r["os"] = "Microsoft Windows Server 2022 Datacenter"
            r["domain_role"] = 5 if r["computer"] == "SRV-DB01" else 3
            if r["computer"] != "SRV-FS01":           # Account Operators read (empty) everywhere but SRV-FS01
                r["groups"].append({"asked": "Account Operators", "sid": "S-1-5-32-548", "name": "Account Operators",
                                    "found": True, "members": []})
    return hosts


class Review2(unittest.TestCase):
    """Codex review of 88cc21d8: formula cells in the CSV, a partial ADSI read, the workbook's note per server, approved
    group names per server, the rule's own status."""

    def test_csv_formula_cells(self):
        import csv
        import io
        h = fleet()[:1]
        h[0]["result"]["groups"].append({"asked": "@Ops", "sid": "S-1-5-21-5-5-5-1", "name": "=cmd|' /C calc'!A0", "found": True,
                                         "members": [mem("-CORP\\x", D + "-1", "User"), mem("+y", D + "-2", "User")]})
        m = w.win_local_groups_model(h, "auto")
        rows = list(csv.reader(io.StringIO(w.win_local_groups_csv(m))))
        cells = [c for r in rows for c in r]
        self.assertFalse([c for c in cells if c and c[0] in "=+-@"], cells)
        self.assertIn("'=cmd|' /C calc'!A0", cells)
        self.assertIn("'-CORP\\x", cells)
        # the JSON keeps the values as they are
        self.assertIn("=cmd|' /C calc'!A0", [r["group_name"] for r in json.loads(w.win_local_groups_json(m))["rows"]])

    def test_partial_read_keeps_members(self):
        h = fleet()[:1]
        g = h[0]["result"]["groups"][0]
        g.update(found=False, method="ADSI", error="ADSI failed: the RPC server is unavailable")
        m = w.win_local_groups_model(h)
        rows = [r for r in w.win_local_groups_rows(m) if r["group"] == "Administrators"]
        self.assertEqual([r["member"] for r in rows], [".\\Administrator", "CORP\\Domain Admins"])
        self.assertTrue(all(r["note"].startswith("partial read: ADSI failed") for r in rows))
        f = w.win_local_groups_findings(m, h[0]["host"])
        self.assertIn("partial read", f[0]["summary"])

    def test_workbook_note_per_server(self):
        hosts = fleet()[:3]
        for i, h in enumerate(hosts):
            h["result"]["groups"][0]["members"].append(mem("%s\\svc" % h["result"]["computer"], "S-1-5-21-7%d-1-1-1001" % i, "User",
                                                           "Local", enabled=(i == 0)))
        m = w.win_local_groups_model(hosts)
        bs = next(x for x in w.win_local_groups_sheets(m) if x["name"] == "By server")
        notes = dict((r[0].split(".")[0], r[11]) for r in bs["rows"] if r[2] == ".\\svc")
        self.assertIn("enabled local account", notes["SRV-APP01"])
        self.assertNotIn("enabled local account", notes["SRV-APP02"])
        self.assertNotIn("enabled local account", notes["SRV-DB01"])


class Review3(unittest.TestCase):
    """Codex review of 285b50b9..8f8d6cb6: a By server selection approves that server only; a By member one carries every
    server's SID; a name with a template mark never reaches a paste-ready variable."""

    def test_selection_identity(self):
        hosts = fleet()[:2]
        for i, h in enumerate(hosts):
            h["result"]["groups"][0]["members"].append(mem("%s\\svc" % h["result"]["computer"], "S-1-5-21-7%d-1-1-1001" % i, "User", "Local", enabled=False))
        m = w.win_local_groups_model(hosts, "auto")
        sec = dict((x["title"], x) for x in w.win_local_groups_sections(m, True))
        srv = [json.loads(t["data"]["sel"]) for t in sec["By server"]["row_tags"] if "data" in t]
        a = next(x for x in srv if x["m"] == ".\\svc" and x["s"].startswith("SRV-APP01"))
        self.assertEqual(a["e"], "SRV-APP01\\svc")                            # this server's account only
        mem_rows = [json.loads(t["data"]["sel"]) for t in sec["By member"]["row_tags"]]
        b = next(x for x in mem_rows if x["m"] == ".\\svc")
        self.assertEqual(b["e"], "{computer}\\svc")
        self.assertEqual(sorted(b["x"].values()), ["S-1-5-21-70-1-1-1001", "S-1-5-21-71-1-1-1001"])
        self.assertEqual(b["i"], "")

    def test_template_marks_never_pasted(self):
        import yaml
        h = fleet()
        for x in h[:5]:
            x["result"]["groups"][0]["members"].append(mem("CORP\\{{ lookup('env','HOME') }}", D + "-8888", "Group"))
        m = w.win_local_groups_model(h, "auto")
        text = w.win_local_groups_approved_yaml(m)
        self.assertNotIn("{{", text.replace("{{ lookup", "")[0:0] + "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
        self.assertIn("# - '%s-8888'" % D, text)                                   # its SID, to approve
        self.assertNotIn("{{", "".join(l.split("#")[0] for l in text.splitlines()))
        yaml.safe_load(text)


APPROVED = {"groups": {"Administrators": ["{computer}\\Administrator", "CORP\\Domain Admins", D + "-9907"],
                       "Remote Desktop Users": ["CORP\\Helpdesk-RDP"]}}


class NoJudging(unittest.TestCase):
    """1.122.0: the job inventories and exports; it is no STIG checker - no status anywhere, nothing approved for the person."""

    def test_no_status(self):
        m = w.win_local_groups_model(fleet())
        for k in ("approved", "stig"):
            self.assertNotIn(k, m)
        self.assertEqual(set(w.win_local_groups_rows(m)[0]), {"server", "computer", "group", "group_name", "member", "name", "sid", "type",
                                                               "source", "enabled", "last_logon", "servers", "note"})
        self.assertNotIn("status", w.CSV_COLUMNS)
        titles = [s["title"] for s in w.win_local_groups_sections(m, True)]
        self.assertNotIn("STIG check: approved members", titles)
        self.assertEqual(json.loads(w.win_local_groups_json(m)).keys(), {"kind", "not_read", "rows"})

    def test_nothing_prefilled(self):
        import yaml
        m = w.win_local_groups_model(fleet())
        self.assertIsNone(m["usual_pct"])
        text = w.win_local_groups_approved_yaml(m, "2026-11-23")
        g = yaml.safe_load(text)["win_local_groups_approved"]["groups"]
        self.assertTrue(all(v is None or v == [] for v in g.values()), g)
        self.assertIn("# - '{computer}\\Administrator'", text)                 # a local account on several servers
        self.assertIn("# - 'SRV-APP02\\svc_deploy'", text)                     # on one server: its own name
        ap = next(x for x in w.win_local_groups_sheets(m) if x["name"] == "Approved list")
        self.assertEqual(set(r[2] for r in ap["rows"]), {""})


def fake_domain(domain):
    return "function Get-CimInstance { param($ClassName, $ErrorAction) [PSCustomObject]@{ Domain = '%s' } }\n" % domain


def fake_members(members):
    """PowerShell that stands in for Get-LocalGroupMember (a function hides the cmdlet): these members, by name and SID."""
    items = ", ".join("[PSCustomObject]@{ Name = '%s'; SID = '%s' }" % (n.replace("'", "''"), sid) for n, sid in members)
    return "function Get-LocalGroupMember { param($SID, $ErrorAction) @(%s) }\n" % items


def run_ps(code):
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as fh:
        fh.write(code)
    try:
        r = subprocess.run(["pwsh", "-NoProfile", "-File", fh.name], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, COMPUTERNAME="SRV-APP01"))
        return r
    finally:
        os.unlink(fh.name)


class Checks(unittest.TestCase):
    """The checks made from the approved list: the answer files (the answer keys' block writer, parsed), the ValidationCode
    and check-local-admins.ps1 run under pwsh against stand-in members."""

    def setUp(self):
        self.m = w.win_local_groups_model(fleet())
        self.ents = w.approved_entries(self.m, APPROVED)

    def test_entries(self):
        self.assertEqual(self.ents, [("{computer}\\Administrator", "", ""), ("CORP\\Domain Admins", "", ""), (D + "-9907", "", "")])
        # the list's key in another language, a scope rule by name glob and by inventory group
        hosts = fleet()
        for h in hosts:
            if h["host"].startswith("SRV-DB01"):
                h["inv_groups"] = ["sql_servers"]
        m = w.win_local_groups_model(hosts)
        e = w.approved_entries(m, {"Administratoren": ["CORP\\Domain Admins"]},
                               [{"match": "SRV-WEB*", "groups": {"Administrators": ["CORP\\Web-Admins"]}},
                                {"group": "sql_servers", "Administrators": ["CORP\\SQL-Admins"]}])
        # Administratoren is the German server's name: there only, by its full name
        self.assertEqual(e, [("CORP\\Domain Admins", "", "SRV-WEB01.corp.example.mil"), ("CORP\\Web-Admins", "SRV-WEB*", ""),
                             ("CORP\\SQL-Admins", "", "SRV-DB01.corp.example.mil")])

    def test_files(self):
        files = w.win_local_groups_checks(self.m, APPROVED)
        self.assertEqual([f["name"] for f in files], ["answer-file-WinServer2019.xml", "answer-file-WinServer2022.xml",
                                                      "answer-file-WinServer2025.xml", "check-local-admins.ps1"])
        self.assertEqual(w.win_local_groups_checks(self.m, {}), [])

    def test_answer_file_as_the_answer_keys_write_it(self):
        import xml.etree.ElementTree as ET
        import stig_answer_filters as sa
        p = w.check_parts()
        st = p["stigs"][1]
        text = w.answer_file(self.ents, st, p)
        root = ET.fromstring(text.encode("utf-8"))
        self.assertEqual(root.tag, "STIGComments")
        self.assertEqual([v.get("ID") for v in root], ["V-254428", "V-254385"])
        ak = root[0][0]
        self.assertEqual([c.tag for c in ak], sa.DOCUMENTED)
        self.assertEqual(ak.find("ExpectedStatus").text, "Not_Reviewed")        # Evaluate-STIG's own status before the key runs
        self.assertEqual((ak.find("ValidTrueStatus").text, ak.find("ValidFalseStatus").text), ("NotAFinding", "Open"))
        self.assertEqual(sa.stig_answer_check_words(("NotAFinding", p["vc_true"]), ("Open", p["vc_false"])), "")
        self.assertEqual(ak.find("ValidationCode").text.rstrip(" ").strip("\n"), w.check_code(self.ents, p))
        # the block is the answer keys' own writer's, under <Vuln> as that work indents it
        block = sa.stig_answer_block("DEFAULT", "Not_Reviewed", w.check_code(self.ents, p), ("NotAFinding", p["vc_true"]), ("Open", p["vc_false"]))
        self.assertIn("\n".join(sa._indent_block(block)), text)
        self.assertNotIn("--", "".join(__import__("re").findall(r"<!--(.*?)-->", text)))

    def test_names_are_data(self):
        ents = [("CORP\\x\u2019; Remove-Item C:\\ -Recurse #", ""), ("\"'@\nWrite-Output pwned", "")]
        code = w.check_code(ents)
        for line in code.splitlines():
            self.assertFalse(line.startswith("'@") and line != "'@ | ConvertFrom-Csv", line)
        self.assertEqual(code.count("'@ | ConvertFrom-Csv"), 1)


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
class Review4(unittest.TestCase):
    """Codex reviews of ae7aec5e..f0f3d10a: a scope is the server's own identity (globs on its computer name, full names
    COMPUTERNAME.domain), a selector that is there but empty approves nothing, overrides, characters XML does not allow."""
    W = "SRV-WEB01.corp.example.mil"

    def hosts(self):
        hosts = fleet()
        for h in hosts:
            if h["host"].startswith("SRV-DB01"):
                h["inv_groups"] = ["sql_servers"]
        return hosts

    def test_language_key_only_where_that_name_is(self):
        m = w.win_local_groups_model(self.hosts())
        self.assertEqual(w.approved_entries(m, {"Administratoren": ["CORP\\x"]}), [("CORP\\x", "", self.W)])
        self.assertEqual(w.approved_entries(m, {"S-1-5-32-544": ["CORP\\y"]}), [("CORP\\y", "", "")])
        self.assertEqual(w.approved_entries(m, {"Ops": ["CORP\\z"]}), [])          # no server calls it that

    def test_same_short_name_other_domain(self):
        # another domain's SRV-WEB01 (English): the German server's approval is bound to its full name
        hosts = self.hosts()
        hosts[0]["result"]["computer"] = "SRV-WEB01"
        hosts[0]["result"]["domain"] = "other.example.mil"
        m = w.win_local_groups_model(hosts)
        self.assertEqual(w.approved_entries(m, {"Administratoren": ["CORP\\x"]}), [("CORP\\x", "", self.W)])

    def test_group_rule_by_full_name(self):
        m = w.win_local_groups_model(self.hosts())
        self.assertEqual(w.approved_entries(m, {}, [{"group": "sql_servers", "Administrators": ["CORP\\SQL-Admins"]}]),
                         [("CORP\\SQL-Admins", "", "SRV-DB01.corp.example.mil")])
        # match and group: the glob stays a condition on the server, the group its full names
        self.assertEqual(w.approved_entries(m, {}, [{"match": "SRV-*", "group": "sql_servers", "Administrators": ["CORP\\a"]}]),
                         [("CORP\\a", "SRV-*", "SRV-DB01.corp.example.mil")])

    def test_selectors_that_approve_nothing(self):
        m = w.win_local_groups_model(self.hosts())
        for rule in ({"match": "", "Administrators": ["CORP\\a"]}, {"match": ["", " "], "Administrators": ["CORP\\a"]},
                     {"match": "SRV-[!1]*", "Administrators": ["CORP\\a"]}, {"match": "SRV-F\u0130LE*", "Administrators": ["CORP\\a"]},
                     {"match": "*\x0b", "Administrators": ["CORP\\a"]}, {"match": "SRV-*", "group": "", "Administrators": ["CORP\\a"]},
                     {"match": "", "group": "sql_servers", "Administrators": ["CORP\\a"]}, {"group": "nobody", "Administrators": ["CORP\\a"]},
                     {"Administrators": ["CORP\\a"]}):
            self.assertEqual(w.approved_entries(m, {}, [rule]), [], rule)

    def test_full_name_twice(self):
        hosts = self.hosts()
        hosts[0]["result"]["computer"] = "SRV-DB01"                     # the same full name, outside sql_servers
        m = w.win_local_groups_model(hosts)
        self.assertEqual(w.approved_entries(m, {}, [{"group": "sql_servers", "Administrators": ["CORP\\a"]}]), [])
        hosts = self.hosts()
        hosts[0]["result"]["computer"] = "SRV-WEB01"                    # the same full name, English
        m = w.win_local_groups_model(hosts)
        self.assertEqual(w.approved_entries(m, {"Administratoren": ["CORP\\x"]}), [])
        self.assertEqual(w.approved_entries(m, {}, [{"match": "SQL;*", "Administrators": ["CORP\\a"]}]), [])

    def test_non_ascii_full_name_never_a_scope(self):
        hosts = self.hosts()
        for h in hosts:
            if h["host"].startswith("SRV-DB01"):
                h["result"]["domain"] = "corp.\u03c3.example"
        m = w.win_local_groups_model(hosts)
        self.assertEqual(w.approved_entries(m, {}, [{"group": "sql_servers", "Administrators": ["CORP\\a"]}]), [])

    def test_no_domain_no_full_name(self):
        hosts = self.hosts()
        for h in hosts:
            if "result" in h and h["result"].get("domain"):
                h["result"]["domain"] = "WORKGROUP"
        m = w.win_local_groups_model(hosts)
        self.assertEqual(w.approved_entries(m, {"Administratoren": ["CORP\\x"]}), [])

    def test_bad_rule_fields_never_offered(self):
        p = w.check_parts([{"stig": "WinServer2022", "vuln": "V-254428", "expected": "Not_Reviewed\x0b"},
                           {"stig": "WinServer2019", "vuln": "V-205746"}])
        self.assertEqual([st["name"] for st in p["stigs"]], ["WinServer2019"])

    def test_legacy_os_rules(self):
        p = w.check_parts([{"os": "2022", "role": "member", "rule": "WN22-MS-000010", "vuln": "V-254428"},
                           {"os": "2019", "role": "member", "rule": "WN19-MS-000010", "vuln": "V-205746"}, {"vuln": "V-1"}])
        self.assertEqual([st["name"] for st in p["stigs"]], ["WinServer2022", "WinServer2019"])

    def test_bad_characters_left_out(self):
        import xml.etree.ElementTree as ET
        m = w.win_local_groups_model(fleet())
        files = w.win_local_groups_checks(m, {"Administrators": ["CORP\\ok", "CORP\\bad\x0b"]})
        self.assertEqual(len(files), 4)
        for f in files[:3]:
            ET.fromstring(f["content"].encode("utf-8"))
            self.assertNotIn("bad", f["content"])

    def test_script_has_a_bom(self):
        self.assertTrue(w.check_script([("CORP\\Jos\u00e9", "")]).startswith("\ufeff"))


class ChecksRun(unittest.TestCase):
    ENTS = [("{computer}\\Administrator", "", ""), ("CORP\\domain admins", "", ""), (D + "-9907", "", ""), ("CORP\\SQL-Admins", "SRV-SQL*", "")]
    OK = [("SRV-APP01\\Administrator", "S-1-5-21-9-9-9-500"), ("CORP\\Domain Admins", D + "-512"), (D + "-9907", D + "-9907")]

    def vc(self, members, ents=None):
        r = run_ps(fake_members(members) + "$r = & {\n" + w.check_code(self.ENTS if ents is None else ents) + "\n}\n"
                   "'VALID=' + [string]$r.Valid\n'RESULTS=' + ($r.Results -replace \"`n\", ' | ')\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def test_approved_only_is_valid(self):
        out = self.vc(self.OK)
        self.assertIn("VALID=True", out)
        self.assertIn("Members of Administrators: SRV-APP01\\Administrator (S-1-5-21-9-9-9-500)", out)   # the ACTUAL members
        self.assertIn("unresolved SID " + D + "-9907", out)                    # approved by its SID

    def test_one_extra_is_not(self):
        out = self.vc(self.OK + [("CORP\\jdoe", D + "-7781")])
        self.assertIn("VALID=False", out)
        self.assertIn("Not approved: CORP\\jdoe (%s-7781)" % D, out)

    def test_sid_entry_never_matches_by_name_and_scope(self):
        # CORP\SQL-Admins is approved on SRV-SQL* only: here (SRV-APP01) it is not approved
        out = self.vc(self.OK + [("CORP\\SQL-Admins", D + "-4400")])
        self.assertIn("Not approved: CORP\\SQL-Admins", out)
        # an entry by SID never approves a member by its name
        out = self.vc([("S-1-5-21-0-0-0-1", D + "-1")], [("S-1-5-21-0-0-0-1", "")])
        self.assertIn("VALID=False", out)

    def test_other_computer_name(self):
        # {computer} is this server's own name: another server's Administrator is not approved here
        out = self.vc(self.OK + [("SRV-APP02\\Administrator", "S-1-5-21-8-8-8-500")])
        self.assertIn("Not approved: SRV-APP02\\Administrator", out)

    def test_ordinal_names(self):
        # a soft hyphen makes another name: never approved by the name without it
        out = self.vc(self.OK + [("CORP\\SQL\u00ad-Admins", D + "-4401")], self.ENTS[:3] + [("CORP\\SQL-Admins", "")])
        self.assertIn("VALID=False", out)
        # any case is the same name
        out = self.vc([("corp\\sql-admins", D + "-4400")], [("CORP\\SQL-Admins", "")])
        self.assertIn("VALID=True", out)

    def test_hosts_scope(self):
        ents = [("{computer}\\Administrator", "", ""), ("CORP\\x", "", "SRV-APP01.corp.example.mil")]
        ok = self.OK[:1] + [("CORP\\x", D + "-1")]
        out = run_ps(fake_domain("corp.example.mil") + fake_members(ok) + "$r = & {\n" + w.check_code(ents) + "\n}\n'VALID=' + [string]$r.Valid\n").stdout
        self.assertIn("VALID=True", out)
        # the same computer name in another domain: not this approval
        out = run_ps(fake_domain("other.example.mil") + fake_members(ok) + "$r = & {\n" + w.check_code(ents) + "\n}\n'VALID=' + [string]$r.Valid\n").stdout
        self.assertIn("VALID=False", out)
        # no domain read: a full-name scope never applies
        out = run_ps(fake_members(ok) + "$r = & {\n" + w.check_code(ents) + "\n}\n'VALID=' + [string]$r.Valid\n").stdout
        self.assertIn("VALID=False", out)

    def test_non_ascii_computer_name_never_matches_a_glob(self):
        ents = [("{computer}\\Administrator", "", ""), ("CORP\\x", "SRV01", "")]
        code = w.check_code(ents)
        r = run_ps(fake_members([("CORP\\x", D + "-1")]) + "$env:COMPUTERNAME = 'SRV\u00ad01'\n$r = & {\n" + code + "\n}\n'VALID=' + [string]$r.Valid\n")
        self.assertIn("VALID=False", r.stdout, r.stdout + r.stderr)
        r = run_ps(fake_members([("CORP\\x", D + "-1")]) + "$env:COMPUTERNAME = 'SRV01'\n$r = & {\n" + code + "\n}\n'VALID=' + [string]$r.Valid\n")
        self.assertIn("VALID=True", r.stdout, r.stdout + r.stderr)

    def test_long_s_computer_name(self):
        code = w.check_code([("{computer}\\Administrator", "", ""), ("CORP\\x", "SRV01", "")])
        r = run_ps(fake_members([("CORP\\x", D + "-1")]) + "$env:COMPUTERNAME = '\u017fRV01'\n$r = & {\n" + code + "\n}\n'VALID=' + [string]$r.Valid\n")
        self.assertIn("VALID=False", r.stdout, r.stdout + r.stderr)

    def test_long_s_is_another_name(self):
        out = self.vc([("CORP\\\u017fvc", D + "-2")], [("CORP\\svc", "", "")])
        self.assertIn("VALID=False", out)

    def test_hashtable_shape(self):
        r = run_ps(fake_members(self.OK) + "$r = & {\n" + w.check_code(self.ENTS) + "\n}\n'TYPE=' + $r.GetType().Name\n"
                   "'KEYS=' + ((@($r.Keys) | Sort-Object) -join ',')\n")
        self.assertIn("TYPE=Hashtable", r.stdout, r.stderr)
        self.assertIn("KEYS=Results,Valid", r.stdout)

    def test_nothing_read_is_not_valid(self):
        # no stand-in: off Windows neither reader works - never valid
        r = run_ps("$r = & {\n" + w.check_code(self.ENTS) + "\n}\n'VALID=' + [string]$r.Valid\n")
        self.assertIn("VALID=False", r.stdout, r.stdout + r.stderr)

    def test_empty_list_approves_nobody(self):
        out = self.vc(self.OK, [])
        self.assertIn("VALID=False", out)

    def test_script_exit_codes(self):
        s = w.check_script(self.ENTS)
        r = run_ps(fake_members(self.OK) + s)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Every member is approved.", r.stdout)
        r = run_ps(fake_members(self.OK + [("CORP\\jdoe", D + "-7781")]) + s)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Not approved: CORP\\jdoe", r.stdout)
        r = run_ps(s)
        self.assertEqual(r.returncode, 1)
        self.assertIn("could not be read", r.stdout)

    def test_parse_5_1_syntax(self):
        for text in (w.check_script(self.ENTS), w.check_code(self.ENTS)):
            with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False) as fh:
                fh.write(text)
            try:
                r = subprocess.run(["pwsh", "-NoProfile", "-Command",
                                    "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e);$e.Count" % fh.name],
                                   capture_output=True, text=True, timeout=120)
                self.assertEqual(r.stdout.strip(), "0", r.stdout + r.stderr)
            finally:
                os.unlink(fh.name)


PS = os.path.join(REPO, "roles", "win_local_groups", "files", "win_local_groups.ps1")


@unittest.skipUnless(shutil.which("pwsh"), "no pwsh")
class PowerShell(unittest.TestCase):
    def test_parses(self):
        r = subprocess.run(["pwsh", "-NoProfile", "-Command",
                            "$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile('%s',[ref]$null,[ref]$e);$e.Count" % PS],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.stdout.strip(), "0", r.stdout + r.stderr)

    def test_runs_and_prints_one_line(self):
        # off Windows there is no local group: every group says why, the script still prints its one line
        env = dict(os.environ, SITE_CHECK_CONFIG=json.dumps({"groups": ["Administrators", "Custom Group"]}))
        r = subprocess.run(["pwsh", "-NoProfile", "-File", PS], capture_output=True, text=True, timeout=120, env=env)
        res = w.win_local_groups_parse(r.stdout.splitlines())
        self.assertNotIn("error", res, r.stdout + r.stderr)
        self.assertEqual([g["asked"] for g in res["groups"]], ["Administrators", "Custom Group"])
        self.assertEqual(res["groups"][0]["sid"], "S-1-5-32-544")

    def test_read_only(self):
        with open(PS) as fh:
            text = fh.read()
        for verb in ("Add-LocalGroupMember", "Remove-LocalGroupMember", "Set-Local", "New-Local", "Disable-Local", "Enable-Local",
                     ".Add(", ".Remove(", "SetInfo", "Set-ItemProperty", "Remove-Item", "New-Item"):
            self.assertNotIn(verb, text)


@unittest.skipUnless(shutil.which("ansible-playbook"), "no ansible-playbook")
class Playbook(unittest.TestCase):
    def test_syntax(self):
        tmp = tempfile.mkdtemp()
        try:
            r = subprocess.run(["ansible-playbook", "--syntax-check", "-i", "localhost,", os.path.join(REPO, "playbooks", "win_local_groups.yml")],
                               capture_output=True, text=True, timeout=300, cwd=REPO, env=dict(os.environ, ANSIBLE_LOCAL_TEMP=tmp))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
