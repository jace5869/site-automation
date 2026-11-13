#!/usr/bin/env python3
"""Tests for "AAP - template variables audit" (playbooks/aap_template_audit.yml, roles/aap_template_audit/library/
site_aap_template_audit.py, plugins/filter/template_audit_filters.py): what is flagged and where it should go, in
memory; the module against a fake AAP in this process (job templates over several pages, workflows, a survey, a
template whose Variables are not YAML, one of another project); the report; and the playbook itself against that fake,
with the report website and the email - no secret's value anywhere, nothing written to AAP.
Needs ansible-core: python3 tests/test_template_audit.py (ANSIBLE_PLAYBOOK=<2.16's ansible-playbook> for 2.16)"""
import copy
import glob
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "roles", "aap_template_audit", "library"))
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
_spec = importlib.util.spec_from_file_location("ansible.module_utils.site_aap", os.path.join(REPO, "plugins", "module_utils", "site_aap.py"))
_aap = importlib.util.module_from_spec(_spec)
sys.modules["ansible.module_utils.site_aap"] = _aap
_spec.loader.exec_module(_aap)
import site_aap_template_audit as m  # noqa: E402
import template_audit_filters as f  # noqa: E402

# made-up secrets: none of them may appear in any output
SECRET_PW = "Wq7!pZ2#rT9xLm4$vK8s"
SECRET_TOKEN = "Xa9fQ2mZ7pL4wR8tK3vB6nH1"
SITE_WIDE = ["report_email_from", "report_email_smtp_host", "report_web_dest", "site_fail_on", "act_provider"]
PER_TEMPLATE = ["report_email_to", "report_email_cc", "report_email_also", "site_fail_on", "target"]
OPTS = {"site_wide": SITE_WIDE, "per_template": PER_TEMPLATE, "repeat_min": 3}


def _jt(i, name, extra, project="Site automation", ask=False, survey=False, kind="job"):
    return {"id": i, "name": name, "extra_vars": extra, "ask_variables_on_launch": ask, "survey_enabled": survey,
            "summary_fields": {"project": {"name": project}} if kind == "job" else {}}


def records():
    """The same templates as the fake AAP has, as audit() takes them."""
    return [{"kind": "job" if r["id"] < 100 else "workflow", "id": r["id"], "name": r["name"],
             "project": (r["summary_fields"].get("project") or {}).get("name", ""), "extra_vars": r["extra_vars"],
             "ask_variables": r["ask_variables_on_launch"], "survey": SURVEYS.get(r["id"], [])} for r in JOBS + WORKFLOWS]


JOBS = [
    _jt(11, "Health check", "---\nreport_email_from: aap@yoursite.mil\nreport_email_to: [ops@yoursite.mil]\n"
                            "check_disk_warn_pct: 90\nansible_winrm_transport: ntlm\n"),
    _jt(12, "Disk report", json.dumps({"report_email_from": "aap@yoursite.mil", "check_disk_warn_pct": 90,
                                       "report_email_to": ["storage@yoursite.mil"]})),
    _jt(13, "Certificate report", "report_email_from: aap@yoursite.mil\ncheck_disk_warn_pct: 90\n"
                                  "vcenter_password: " + SECRET_PW + "\nvcenter_password_file: /etc/site/vc.txt\n"
                                  "check_accounts_pw_max_days: 60\n", ask=True),
    _jt(14, "VM - health report", "vmware_vcenter: vcsa01.yoursite.mil\nstigman_url: https://stigman.yoursite.mil/api\n"
                                  "ntp_servers: [192.0.2.10, 192.0.2.11]\nreport_logo_file: logo.png\n"
                                  "report_email_html_template: report.html.j2\ntarget: web01.yoursite.mil\n"),
    _jt(15, "Broken vars", "report_email_to: [ops@yoursite.mil\n  nope: : :"),
    _jt(16, "Patch hosts", "check_disk_warn_pct: 85\nhealth_checks: daily\n", survey=True),
    _jt(17, "Other project job", "ansible_user: admin\n", project="Other project"),
    _jt(18, "Token in a value", "some_setting: " + SECRET_TOKEN + "\nreport_email_to: [ops@yoursite.mil]\n"),
    _jt(19, "Empty", ""),
]
WORKFLOWS = [_jt(101, "Daily health", "---\nreport_email_from: aap@yoursite.mil\nansible_become: true\n", kind="workflow", survey=True)]
SURVEYS = {16: [{"variable": "health_checks", "type": "multiplechoice"}, {"variable": "db_password", "type": "text"},
                {"variable": "vault_password", "type": "password"}],
           101: [{"variable": "report_email_also", "type": "textarea"}, {"variable": "report_web_dest", "type": "text"}]}


class Judge(unittest.TestCase):
    def test_address(self):
        for s in ("vcsa01.yoursite.mil", "https://stigman.yoursite.mil/api", "192.0.2.10", "192.0.2.0/24", "192.0.2.5:5986",
                  "aap01.yoursite.mil:443", "2001:db8::1", "ldaps://dc01.corp"):
            self.assertTrue(m.is_address(s), s)
        for s in ("report.html.j2", "logo.png", "ops@yoursite.mil", "daily", "1.2", "Site automation", "{{ inventory_hostname }}",
                  "/etc/pki/tls/certs/ca.pem", "config.yml"):
            self.assertFalse(m.is_address(s), s)

    def test_secret(self):
        self.assertTrue(m.is_secret("vcenter_password", "x"))
        self.assertTrue(m.is_secret("aap_token", "abc"))
        self.assertTrue(m.is_secret("anything", SECRET_TOKEN))
        self.assertTrue(m.is_secret("anything", "$ANSIBLE_VAULT;1.1;AES256\n3132"))
        self.assertTrue(m.is_secret("creds", {"user": "svc", "api_key": "x", "secret": SECRET_PW}))
        self.assertFalse(m.is_secret("vcenter_password", ""))                  # no value: nothing to hide
        self.assertFalse(m.is_secret("vcenter_password_file", "/etc/site/vc.txt"))
        self.assertFalse(m.is_secret("check_accounts_pw_max_days", 60))
        self.assertFalse(m.is_secret("password_check", True))
        self.assertFalse(m.is_secret("report_web_smb_auth", "negotiate"))
        self.assertFalse(m.is_secret("token_url", "https://kc.yoursite.mil/realms/x"))
        for s in ("a_very_long_setting_name_here", "/var/lib/awx/projects/site_automation", "https://reports.yoursite.mil/x/y",
                  "ops-team@yoursite.mil", "All good, nothing to do here", "{{ lookup('env', 'X') }}"):
            self.assertFalse(m.looks_random(s), s)

    def test_classify(self):
        self.assertEqual(m.classify("ansible_winrm_transport", "ntlm"), (["connection"], "the windows group (group_vars/windows.yml)"))
        self.assertEqual(m.classify("ansible_user", "admin")[1], "the Machine credential")
        self.assertEqual(m.classify("ansible_password", "x"), (["connection", "secret"], "the Machine credential"))
        self.assertEqual(m.classify("ansible_port", 22)[1], "the group's or host's variables")
        self.assertEqual(m.classify("vmware_vcenter", "vcsa01.yoursite.mil"), (["address"], m.ENCLAVE))
        self.assertEqual(m.classify("report_email_from", "aap@yoursite.mil", SITE_WIDE), (["site_wide"], m.ALL_YML))
        self.assertEqual(m.classify("report_email_to", ["ops@yoursite.mil"], SITE_WIDE + ["report_email_to"], (), PER_TEMPLATE), ([], ""))
        self.assertEqual(m.classify("target", "web01.yoursite.mil", (), (), PER_TEMPLATE), ([], ""))
        self.assertEqual(m.classify("vcenter_password", "x"), (["secret"], m.SECRET))
        self.assertEqual(m.classify("dns_zone", "x", (), ["dns_zone"]), (["per_enclave"], m.ENCLAVE))

    def test_parse(self):
        self.assertEqual(m.parse_vars("---\na: 1\n"), ({"a": 1}, ""))
        self.assertEqual(m.parse_vars('{"a": 1}'), ({"a": 1}, ""))
        self.assertEqual(m.parse_vars(""), ({}, ""))
        self.assertEqual(m.parse_vars("---\n"), ({}, ""))
        self.assertEqual(m.parse_vars({"a": 1}), ({"a": 1}, ""))
        self.assertIn("not YAML or JSON", m.parse_vars("a: [1\n b: : :")[1])
        self.assertIn("not a list of settings", m.parse_vars("- a\n- b\n")[1])

    def test_audit(self):
        templates, settings = m.audit(records(), OPTS)
        t = dict((x["name"], x) for x in templates)
        flags = lambda name: dict((x["setting"], x) for x in t[name]["flags"])   # noqa: E731
        # connection, site-wide, and repeated: report_email_from in 4 templates, check_disk_warn_pct: 90 in 3
        self.assertEqual(flags("Health check")["ansible_winrm_transport"]["why"], ["connection"])
        self.assertEqual(flags("Health check")["report_email_from"]["why"], ["site_wide", "repeated"])
        self.assertEqual(flags("Health check")["report_email_from"]["repeats"], 4)
        self.assertEqual(flags("Health check")["check_disk_warn_pct"]["why"], ["repeated"])
        self.assertEqual(flags("Health check")["check_disk_warn_pct"]["suggestion"], m.ALL_YML)
        self.assertEqual(flags("Health check")["check_disk_warn_pct"]["note"], "also in Disk report, Certificate report")
        self.assertNotIn("report_email_to", flags("Health check"))              # per template, values differ
        self.assertNotIn("check_disk_warn_pct", flags("Patch hosts"))      # 85: not the same value
        # a secret: its name only; the file and the rule named like a password are no secrets
        c = flags("Certificate report")
        self.assertEqual(c["vcenter_password"]["why"], ["secret"])
        self.assertNotIn("vcenter_password_file", c)
        self.assertNotIn("check_accounts_pw_max_days", c)
        self.assertEqual(c["(Variables on launch)"]["why"], ["launch"])         # Prompt on launch, with flagged settings
        self.assertEqual(t["Certificate report"]["flags"][-1]["setting"], "(Variables on launch)")
        # addresses (not the per-template target, not file names)
        v = flags("VM - health report")
        self.assertEqual(sorted(v), ["ntp_servers", "stigman_url", "vmware_vcenter"])
        self.assertEqual(v["ntp_servers"]["suggestion"], m.ENCLAVE)
        self.assertIn("not YAML or JSON", t["Broken vars"]["error"])
        self.assertEqual(flags("Token in a value")["some_setting"]["why"], ["secret"])
        self.assertEqual(flags("Other project job")["ansible_user"]["suggestion"], "the Machine credential")
        # the survey: a text question for a password (a Password question is fine), a site-wide setting asked at launch
        n = flags("Patch hosts")
        self.assertEqual(sorted(n), ["db_password"])
        self.assertEqual(n["db_password"]["why"], ["secret", "launch"])
        self.assertIn("plain text (text, not Password)", n["db_password"]["note"])
        d = flags("Daily health")
        self.assertEqual(d["report_web_dest"]["why"], ["site_wide", "launch"])
        self.assertNotIn("report_email_also", d)
        self.assertEqual(d["ansible_become"]["why"], ["connection"])
        self.assertEqual(t["Empty"]["flags"], [])
        # each setting once, the most common first; a secret's value never kept
        s = dict((x["setting"], x) for x in settings)
        self.assertEqual(settings[0]["setting"], "report_email_from")
        self.assertEqual((s["report_email_from"]["templates"], s["report_email_from"]["same"], s["report_email_from"]["value"]),
                         (4, 4, "aap@yoursite.mil"))
        self.assertEqual(s["vcenter_password"]["value"], "")
        self.assertEqual(s["some_setting"]["value"], "")
        self.assertNotIn(SECRET_PW, json.dumps([templates, settings]))
        self.assertNotIn(SECRET_TOKEN, json.dumps([templates, settings]))

    def test_nested_and_numeric_secrets_are_never_shown(self):
        """A number under a secret's name, a short password in a dict (or a list of dicts), and a true/false under a
        secret's name: in 3 templates each, never a value in the module's result or in the report."""
        ev = ("db_password: 12345678\nstigman_db:\n  password: Summer2026!\n  user: svc_stig\n"
              "ldap_binds:\n- {host: ldap01.example.mil, bind_pw: Pine-4-Cone}\nsmtp_password: true\n"
              "check_list: [alpha, beta]\n")
        recs = [{"kind": "job", "id": i, "name": "Job %d" % i, "extra_vars": ev} for i in range(3)]
        self.assertTrue(m.is_secret("db_password", 12345678))
        self.assertTrue(m.is_secret("stigman_db", {"password": "Summer2026!", "user": "svc_stig"}))
        self.assertTrue(m.is_secret("ldap_binds", [{"host": "x", "bind_pw": 1234}]))
        self.assertFalse(m.is_secret("smtp_password", True))                   # a switch: not a secret ...
        self.assertTrue(m.hidden("smtp_password", True))                        # ... and still not shown
        templates, settings = m.audit(recs, {"repeat_min": 3})
        s = dict((x["setting"], x) for x in settings)
        for n in ("db_password", "stigman_db", "ldap_binds"):
            self.assertIn("secret", s[n]["why"], n)
            self.assertEqual(s[n]["value"], "", n)
        self.assertEqual(s["smtp_password"]["value"], "(same; not shown)")    # repeated, named like a secret
        self.assertEqual(s["check_list"]["value"], "(same; not shown)")       # a list: never its value
        out = json.dumps([templates, settings])
        r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []}, {"repeat_min": 3})
        out += json.dumps(r, default=str)
        for v in ("12345678", "Summer2026", "Pine-4-Cone", "svc_stig", "alpha"):
            self.assertNotIn(v, out, v)

    def test_odd_names_urls_and_texts(self):
        """The tester's list, each setting in 3 templates with the same value: a secret's value is never in the module's
        result or the report - its name a whole word (any case, - . _ or camelCase, a digit after it), a URL with a
        password, a text holding JSON or YAML (or name=value) with one in it. Words inside other words, a URL without a
        password and plain text are still shown."""
        hide = [  # (setting, value, what must never be seen)
            ("admin_pwd", "Hx-admin_pwd", "Hx-admin_pwd"), ("adminPwd", "Hx-adminPwd", "Hx-adminPwd"),
            ("dbPassword", "Hx-dbPassword", "Hx-dbPassword"), ("client-secret", "Hx-client-secret", "Hx-client-secret"),
            ("API-KEY", "Hx-API-KEY", "Hx-API-KEY"), ("db.password", "Hx-db.password", "Hx-db.password"),
            ("credential", "Hx-credential", "Hx-credential"), ("svc_credentials", "Hx-svc_creds", "Hx-svc_creds"),
            ("secret1", "Hx-secret1", "Hx-secret1"), ("pin", 4711, "4711"), ("vault_pin", "8812", "8812"),
            ("key_material", "Hx-key_material", "Hx-key_material"), ("apiKey", "Hx-apiKey", "Hx-apiKey"),
            ("conn_string", "mysql://svc:Tr0ub4dor@db01.example.mil:3306/app", "Tr0ub4dor"),
            ("app_json", '{"password": "Bl4ckC4t", "user": "svc"}', "Bl4ckC4t"),
            ("app_yaml", "password: Gr33nD0g\nuser: svc\n", "Gr33nD0g"),
            ("app_yaml_list", "- user: svc\n  password: R3dF0x\n", "R3dF0x"),
            ("odbc", "Server=db01;Uid=svc;Pwd=Wh1teOwl;", "Wh1teOwl"),
            # already fixed in 1.24.0: still hidden
            ("db_password", 12345678, "12345678"), ("PASSPHRASE", "Hx-PASSPHRASE", "Hx-PASSPHRASE"),
            ("ldap_bind_pw", "Hx-ldap_bind_pw", "Hx-ldap_bind_pw"), ("radius_shared_secret", "Hx-radius", "Hx-radius"),
            ("stigman_db", {"user": "svc", "password": "Hx-nested"}, "Hx-nested"),
            ("binds", [{"host": "ldap01.example.mil", "bindPw": "Hx-list"}], "Hx-list"),
        ]
        not_shown = [  # never a value, though no secret
            ("smtpPassword", True, "true"), ("check_list", ["alpha", "beta"], "alpha"),
            ("deep", {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"password": "Hx-deep"}}}}}}}}, "Hx-deep"),
            ("list_text", '["alpha", "beta"]', "alpha"), ("map_text", "colour: green", "green"),
        ]
        show = [  # (setting, value, the value shown)
            ("bypass_mode", "strict", "strict"), ("compass_heading", "north", "north"), ("keyboard_layout", "us", "us"),
            ("spin_count", 3, "3"), ("opinion", "fine", "fine"),
            ("report_url", "https://reports.example.mil:8443/x", "https://reports.example.mil:8443/x"),
            ("git_url", "ssh://git@git.example.mil/site.git", "ssh://git@git.example.mil/site.git"),
            ("note", "nightly patch window", "nightly patch window"),
        ]
        for n, v, expect in hide + not_shown + show:
            with self.subTest(setting=n):
                recs = [{"kind": "job", "id": i, "name": "Job %d" % i, "extra_vars": json.dumps({n: v})} for i in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                s = dict((x["setting"], x) for x in settings)[n]
                r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []}, {"repeat_min": 3})
                out = json.dumps([templates, settings]) + json.dumps(r, default=str)
                if (n, v, expect) in hide:
                    self.assertIn("secret", s["why"])
                    self.assertEqual(s["value"], "")
                    self.assertNotIn(expect, out)
                elif (n, v, expect) in not_shown:
                    self.assertNotIn("secret", s["why"])
                    self.assertEqual(s["value"], "(same; not shown)")
                    self.assertNotIn(expect, out)
                else:
                    self.assertNotIn("secret", s["why"])
                    self.assertEqual(s["value"], expect)
        for n in ("bypass", "compass", "keyboard", "spin", "opinion", "passthrough", "tokenizer", "pinned_version",
                  "vcenter_password_file", "dbPasswordFile", "api_key_id", "check_accounts_pw_max_days"):
            self.assertFalse(m.secret_name(n), n)
        self.assertEqual(m.name_words("APIKey-db.password_v2"), ["api", "key", "db", "password", "v", "2"])

    def test_joined_names_values_and_false_alarms(self):
        """1.24.0c, the re-pass: a name ending in a long secret word with no sign (PGPASSWORD, dbpassword) or in pass
        (adminpass); a secret in the value under a neutral name (an Authorization header, --password X, -p X, a PEM
        private key); and the false alarms - a credential's name or id, pin or pass with a count or a version, a sentence."""
        hide = [  # (setting, value, what must never be seen)
            ("PGPASSWORD", "Hx-pg1", "Hx-pg1"), ("dbpassword", "Hx-db2", "Hx-db2"), ("adminpass", "Hx-adm3", "Hx-adm3"),
            ("clientsecret", "Hx-cs4", "Hx-cs4"), ("vaultToken2", "Hx-vt5", "Hx-vt5"),
            ("auth_header", "Basic c3ZjOkh4LWJhc2lj", "c3ZjOkh4LWJhc2lj"),
            ("authorization", "Bearer eyJhbGciOiJIUzI1NiJ9.e30.Hx-bearer", "Hx-bearer"),
            ("curl_args", "-H 'Authorization: Token Hx0tok3n99' https://api.example.mil", "Hx0tok3n99"),
            ("cli_args", "--user svc --password Hx-cli6", "Hx-cli6"), ("mysql_args", "-u root -p Hx-cli7", "Hx-cli7"),
            ("env_line", "PGPASSWORD=Hx-env8 psql -h db01", "Hx-env8"),
            ("tls_key", "-----" "BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----", "MIIEvQ"),
            ("ssh_key", "-----" "BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n", "b3BlbnNz"),
            ("ldap_credential", "Hx9Summer2024", "Hx9Summer2024"), ("svc_creds", {"user": "svc", "pass": "Hx-c10"}, "Hx-c10"),
        ]
        show = [  # (setting, value, the value shown)
            ("incident_docs_draft_credential", "AAP - SOP draft", "AAP - SOP draft"),
            ("pin_version", "1.2.3", "1.2.3"), ("pass_count", 3, "3"),
            ("note", "Change the password: see the runbook", "Change the password: see the runbook"),
            ("ssh_args", "-p 2222 -o BatchMode=yes", "-p 2222 -o BatchMode=yes"),
            ("intro", "Basic information about the site", "Basic information about the site"),
        ]
        for n, v, expect in hide + show:
            with self.subTest(setting=n):
                recs = [{"kind": "job", "id": i, "name": "Job %d" % i, "extra_vars": json.dumps({n: v})} for i in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                s = dict((x["setting"], x) for x in settings)[n]
                r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []}, {"repeat_min": 3})
                out = json.dumps([templates, settings]) + json.dumps(r, default=str)
                if (n, v, expect) in hide:
                    self.assertIn("secret", s["why"])
                    self.assertNotIn(expect, out)
                else:
                    self.assertNotIn("secret", s["why"])
                    self.assertEqual(s["value"], expect)
        # the repo's own: a list of credential names or ids is no secret, and shown as a list is not (never a value)
        for v in (["SITEA - Linux SSH", "SITEA - Windows SMB"], [12, 15]):
            self.assertFalse(m.is_secret("tenable_scan_credentials", v), v)
        for n in ("PGPASSWORD", "dbpassword", "adminpass", "rootpass", "apitoken", "clientsecret", "sshprivatekey"):
            self.assertTrue(m.secret_name(n), n)
        for n in ("bypass", "compass", "multipass", "passenger_list", "secretary_email", "tokenizer", "token_url",
                  "password_max_days", "pin_version", "pass_count", "keyboard", "spin", "credential_name"):
            self.assertFalse(m.secret_name(n), n)

    def test_glued_p_user_colon_pw_and_community(self):
        """1.24.0d, the third pass: mysql's -pXXXX (and -pXXXX elsewhere but a few flag letters), -u / --user
        user:password, a name ending in pw (rootpw, bindpw), and community (SNMP) - and what must stay shown."""
        hide = [  # (setting, value, what must never be seen)
            ("db_cmd", "mysql -u root -pHx1glued db", "Hx1glued"), ("dump_cmd", "mysqldump -uroot -pHx2dump app", "Hx2dump"),
            ("maria_cmd", "mariadb-dump -pHx3maria app", "Hx3maria"), ("admin_cmd", "/usr/bin/mysqladmin -pabc status", "-pabc"),
            ("psql_cmd", "tool -pHx4!glued", "Hx4!glued"),
            ("curl_cmd", "curl -u svc:Hx5user https://api.example.mil/x", "Hx5user"),
            ("curl_long", "curl --user svc:Hx6user https://api.example.mil/x", "Hx6user"),
            ("curl_eq", "curl --user=svc:Hx7user https://api.example.mil/x", "Hx7user"),
            ("rootpw", "Hx8root", "Hx8root"), ("bindpw", "Hx9bind", "Hx9bind"), ("ldapBindpw", "Hx10bind", "Hx10bind"),
            ("snmp_community", "Hx11comm", "Hx11comm"), ("community", "Hx12comm", "Hx12comm"),
            ("snmpcommunity", "Hx13comm", "Hx13comm"),
        ]
        show = [  # (setting, value, the value shown)
            ("ssh_cmd", "ssh -p 2222 host01", "ssh -p 2222 host01"), ("run_cmd", "podman run -p 8080:80 web", "podman run -p 8080:80 web"),
            ("sync_cmd", "rsync -p /src /dst", "rsync -p /src /dst"), ("copy_cmd", "cp -pR /a /b", "cp -pR /a /b"),
            ("tar_cmd", "tar -pcf x.tar dir", "tar -pcf x.tar dir"), ("uid_cmd", "podman run -u 1000:1000 web", "podman run -u 1000:1000 web"),
            ("curl_plain", "curl -u svc https://api.example.mil/x", "curl -u svc https://api.example.mil/x"),
            ("max_tokens", 4096, "4096"), ("community_name", "SNMP v2c", "SNMP v2c"),
        ]
        for n, v, expect in hide + show:
            with self.subTest(setting=n):
                recs = [{"kind": "job", "id": i, "name": "Job %d" % i, "extra_vars": json.dumps({n: v})} for i in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                s = dict((x["setting"], x) for x in settings)[n]
                r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []}, {"repeat_min": 3})
                out = json.dumps([templates, settings]) + json.dumps(r, default=str)
                if (n, v, expect) in hide:
                    self.assertIn("secret", s["why"])
                    self.assertNotIn(expect, out)
                else:
                    self.assertNotIn("secret", s["why"])
                    self.assertEqual(s["value"], expect)
        self.assertFalse(m.is_secret("tenable_scan_credentials", ["ssh-linux", "smb-windows"]))   # names in a list
        self.assertTrue(m.is_secret("tenable_scan_credentials", "ssh-linux"))                      # one word alone: hidden
        for n in ("rootpw", "bindpw", "ldap_bindpw", "snmp_community", "snmpCommunity"):
            self.assertTrue(m.secret_name(n), n)
        for n in ("max_tokens", "min_password_length", "community_name"):
            self.assertFalse(m.secret_name(n), n)

    def test_command_password_options(self):
        """1.24.0e, the last pattern round: mysql's glued -p takes any value (its port is -P); -u user:password only
        after curl or wget; a glued -p elsewhere only with a digit or a sign and not $; each tool's own password option
        after that tool; a list under a credential's setting never shown."""
        hide = [  # (setting, value, what must never be seen)
            ("my1", "mysql -u root -p2024Hx1 db", "2024Hx1"), ("my2", "mysql -uroot -p9Hx2", "9Hx2"),
            ("my3", "/usr/bin/mariadb-dump -pHxthree app", "Hxthree"),
            ("cu1", "curl -usvc:Hx4 https://api.example.mil/x", "Hx4"), ("cu2", "wget --user svc:Hx5 https://api.example.mil/x", "Hx5"),
            ("sq1", "sqlcmd -S db01 -U sa -P Hx6 -Q go", "Hx6"), ("sq2", "C:\\tools\\sqlcmd.exe -PHx7", "Hx7"),
            ("ld1", "ldapsearch -x -D cn=svc -w Hx8 -b dc=example", "Hx8"), ("ld2", "ldapmodify -wHx9 -f x.ldif", "Hx9"),
            ("sm1", "smbclient //fs01/share -U svc%Hx10", "Hx10"), ("sm2", "net ads join -Usvc%Hx11", "Hx11"),
            ("ip1", "ipmitool -I lanplus -H bmc01 -U admin -P Hx12 power status", "Hx12"),
            ("rd1", "redis-cli -a Hx13 ping", "Hx13"), ("wr1", "winrs -r:host01 -u:admin -p:Hx14 ipconfig", "Hx14"),
            ("sp1", "sshpass -p Hx15 ssh host01", "Hx15"), ("gp1", "tool -pHx16!x", "Hx16"),
        ]
        show = [
            "date -u +%H:%M:%S", "podman exec -u apache:apache web ls", "docker run -u root:wheel web", "rsync -u host01:/src /dst",
            "ssh -p$SSH_PORT host01", "podman run -p${PORT}:80 web", "find . -print0", "find . -printf %p", "find . -prune",
            "find . -perm 644", "openssl rsa -pubout", "gcc -pthread x.c", "rsync -prltDvz a b", "netstat -plantu",
            "mysql -P 3306 app", "ldapsearch -W -x", "curl -u svc https://api.example.mil/x",
        ]
        for n, v, expect in hide:
            with self.subTest(setting=n):
                recs = [{"kind": "job", "id": i, "name": "Job %d" % i, "extra_vars": json.dumps({n: v})} for i in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                s = dict((x["setting"], x) for x in settings)[n]
                self.assertIn("secret", s["why"])
                self.assertNotIn(expect, json.dumps([templates, settings]))
        for v in show:
            with self.subTest(value=v):
                self.assertFalse(m.is_secret("cmd", v), v)
                self.assertFalse(m.hidden("cmd", v), v)
        # a list under a credential's setting: no secret, and its values never shown (pinned: the tester's check)
        v = ["SITEA - Linux SSH Hx17", "SITEA - Windows SMB Hx18"]
        recs = [{"kind": "job", "id": i, "name": "Job %d" % i, "extra_vars": json.dumps({"tenable_scan_credentials": v})} for i in range(3)]
        templates, settings = m.audit(recs, {"repeat_min": 3})
        s = dict((x["setting"], x) for x in settings)["tenable_scan_credentials"]
        r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []}, {"repeat_min": 3})
        out = json.dumps([templates, settings]) + json.dumps(r, default=str)
        self.assertNotIn("secret", s["why"])
        self.assertEqual(s["value"], "(same; not shown)")
        self.assertNotIn("Hx17", out)
        self.assertNotIn("Hx18", out)

    def test_shell_words(self):
        """1.24.0f: the command word wherever a shell starts a command - after $( ` ( ; | && and inside quotes - the
        command ends at a new line (not after a \\ at its end), and Windows tools' switches with / or - in any case."""
        hide = [  # (value, what must never be seen)
            ("N=$(mysql -uroot -p2024Hx1 -N)", "2024Hx1"), ("N=`mysql -uroot -pHx2`", "Hx2"), ("(mysql -uroot -p2024Hx3 db)", "2024Hx3"),
            ("ssh db01 'mysql -uroot -p9Hx4! -e status'", "9Hx4!"), ('ssh db01 "mysql -uroot -p9Hx5! -e status"', "9Hx5!"),
            ("bash -c 'mysql -uroot -p2024Hx6'", "2024Hx6"), ("cd /tmp;mysql -uroot -p2024Hx7 db", "2024Hx7"),
            ("true&&mysql -uroot -p2024Hx8", "2024Hx8"), ("echo 'select 1'|mysql -uroot -pHxnine", "Hxnine"),
            ("true|mysql -uroot -pHxten", "Hxten"), ("x=$(curl -usvc:Hx11 https://api.example.mil/x)", "Hx11"),
            ("mysqldump \\\n  -pHxtwelve app", "Hxtwelve"), ("mysql -e 'select 1' -pHxthirteen", "Hxthirteen"),
            ("mysql -p 'Hxfourteen'", "Hxfourteen"), ("winrs /p:Hx15 ipconfig", "Hx15"), ("WINRS -r:host01 -P:Hx16 ipconfig", "Hx16"),
            ("sqlcmd /P Hx17", "Hx17"), ("SQLCMD -S db01 /PHx18", "Hx18"), ("NET ads join /U svc%Hx19", "Hx19"),
        ]
        show = [
            "mysqldump app > app.sql\nrsync -prltDvz a b", "echo mysql\nls -pabc", "net use \\\\fs01\\share /user:svc",
            "mysql -P 3306 app", "date -u +%H:%M:%S", "podman exec -u apache:apache web ls", "ssh -p$SSH_PORT host01",
            "find . -print0", "rsync -prltDvz a b", "curl -u svc https://api.example.mil/x",
        ]
        for i, (v, expect) in enumerate(hide):
            with self.subTest(value=v):
                n = "cmd%d" % i
                recs = [{"kind": "job", "id": j, "name": "Job %d" % j, "extra_vars": json.dumps({n: v})} for j in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                self.assertIn("secret", dict((x["setting"], x) for x in settings)[n]["why"])
                self.assertNotIn(expect, json.dumps([templates, settings]))
        for v in show:
            with self.subTest(value=v):
                self.assertFalse(m.is_secret("cmd", v), v)
        self.assertEqual(m.shell_words("N=$(mysql -pX);ls 'a b'|wc"),           # 1.24.0g: a quoted string is one word
                         (["N=", "$(", "mysql", "-pX", ")", ";", "ls", "a b", "|", "wc"], ["a b"]))

    def test_quoted_strings(self):
        """1.24.0g: a quoted string is one word of the command around it - its ( ) | ; & never end that command - and a
        command line of its own; sqlcmd -p1 (statistics) is no password."""
        hide = [  # (value, what must never be seen)
            ('mysql -e "select count(*) from t" -p2024Hx1', "2024Hx1"), ("mysql -e 'select a|b' -pHxtwo", "Hxtwo"),
            ("ldapsearch -x -b dc=example '(uid=bob)' -D cn=admin -w Hx3", "Hx3"),
            ("ldapsearch -x '(&(uid=bob)(cn=x))' -w Hxfour", "Hxfour"), ("mysql -e 'use db; select 1' -p2024Hx5", "2024Hx5"),
            ("mysql -e 'select 1' -pHxsix", "Hxsix"), ("mysql -p 'Hxseven'", "Hxseven"), ('mysql -p"Hx eight"', "Hx eight"),
            ("curl -d '{\"a\":1}' -u svc:Hx9 https://api.example.mil/x", "Hx9"), ("ssh db01 'mysql -uroot -p9Hx10!'", "9Hx10!"),
            ("bash -c 'cd /tmp; mysql -uroot -p2024Hx11'", "2024Hx11"), ("mysql -e 'unclosed -p2024Hx12", "2024Hx12"),
        ]
        show = ["ldapsearch '(cn=-w x)'", "redis-cli 'get -a'", "sqlcmd -p", "sqlcmd -S db01 -p1 -Q go", "net use /persistent:yes",
                "net user /domain"]
        for i, (v, expect) in enumerate(hide):
            with self.subTest(value=v):
                n = "cmd%d" % i
                recs = [{"kind": "job", "id": j, "name": "Job %d" % j, "extra_vars": json.dumps({n: v})} for j in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                self.assertIn("secret", dict((x["setting"], x) for x in settings)[n]["why"])
                self.assertNotIn(expect, json.dumps([templates, settings]))
        for v in show:
            with self.subTest(value=v):
                self.assertFalse(m.is_secret("cmd", v), v)

    def test_backslashes_and_long_words(self):
        """1.24.0h: a backslash as POSIX has it (inside "..." \\" is a plain quote, the inside handed on without the
        backslash; inside '...' nothing is special), Windows paths kept; and a long word read in linear time."""
        hide = [  # (value, what must never be seen)
            ('ssh h "mysql -e \\"select 1\\" -p2024Hx1"', "2024Hx1"), ('bash -c "mysql -e \\"select 1\\" -p2024Hx2"', "2024Hx2"),
            ('ssh h "mysql -e \\"select 1\\" -p2024Hx3; echo done"', "2024Hx3"), ('mysql -e "select \\"x\\"" -p2024Hx4', "2024Hx4"),
            ("echo 'it'\\''s' ; mysql -pHxfive", "Hxfive"), ('"C:\\Program Files\\MySQL\\bin\\mysql.exe" -uroot -pHxsix', "Hxsix"),
            ("C:\\tools\\sqlcmd.exe -PHx7", "Hx7"), ("mysqldump \\\n  -pHxeight app", "Hxeight"),
        ]
        for i, (v, expect) in enumerate(hide):
            with self.subTest(value=v):
                n = "cmd%d" % i
                recs = [{"kind": "job", "id": j, "name": "Job %d" % j, "extra_vars": json.dumps({n: v})} for j in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                self.assertIn("secret", dict((x["setting"], x) for x in settings)[n]["why"])
                self.assertNotIn(expect, json.dumps([templates, settings]))
        self.assertEqual(m.shell_words('ssh h "mysql -e \\"select 1\\" -pX"'), (["ssh", "h", 'mysql -e "select 1" -pX'],
                                                                              ['mysql -e "select 1" -pX']))
        self.assertEqual(m.shell_words("echo 'it'\\''s'")[0], ["echo", "it's"])
        self.assertEqual(m.shell_words("echo 'a\\\\b'")[0], ["echo", "a\\\\b"])              # inside '...' nothing is special
        # 200 KB of one word: linear (it was quadratic from 1.24.0b on: 40 KB took 27 s)
        n = 200000                                            # 1.24.0i: the tester's worst inputs too
        for w in ("QUJD" * 50000, "A" * 200000, "node." * 40000, "a=b " * 50000, "x:" * 100000, "-a " * 66000,
                  "-" * n, "=" * n, ":" * n, "/" * n, "@" * n, "." * n, " " * n, "x: " + " " * n, " =" * 100000,
                  "-----BEGIN" * 20000, "-----BEGIN " + "A " * 99990, "://" * 66000, "a://b:" * 33000, "u:p@" * 50000,
                  "a" * n + "=1", "--" + "a" * n + " x"):
            t = time.time()
            self.assertFalse(m.is_secret("blob", w))
            self.assertLess(time.time() - t, 1.0, w[:12])
        t = time.time()
        recs = [{"kind": "job", "id": j, "name": "Job %d" % j,
                 "extra_vars": json.dumps({"blob1": "QUJD" * 5000, "blob2": "A" * 20000, "blob3": "node." * 4000})} for j in range(3)]
        m.audit(recs, {"repeat_min": 3})
        self.assertLess(time.time() - t, 2.0)

    def test_long_names_and_pem_labels(self):
        """1.24.0i: the bounds of h kept, the look-behinds gone - a name led by a digit, a name of 65 characters or more
        (read on its last 64), a URL scheme of 33 or more, a long option name, a PEM label of many words, and PGP's key
        block are secrets as in g."""
        long_name = "A" * 64 + "_PASSWORD"
        hide = [  # (value, what must never be seen)
            ("2FA_SECRET=Hx1", "Hx1"), ("1PASSWORD=Hx2", "Hx2"), ("export %s=Hx3" % long_name, "Hx3"),
            ("DB_HOST=db01 %s=Hx4" % long_name, "Hx4"), ("%s='Hx5 x'" % long_name, "Hx5"),
            ("a" * 40 + "+svc.scheme://svc:Hx6@db01.example.mil", "Hx6"), ("--" + "a" * 70 + "-password Hx7", "Hx7"),
            ("-----" "BEGIN ONE TWO THREE FOUR FIVE PRIVATE KEY-----\nHx8", "Hx8"),
            ("-----" "BEGIN ABCDEFGHIJKLMNOPQRS PRIVATE KEY-----\nHx9", "Hx9"), ("-----" "BEGIN PGP PRIVATE KEY BLOCK-----\nHx10", "Hx10"),
        ]
        for i, (v, expect) in enumerate(hide):
            with self.subTest(value=v[:40]):
                n = "blob%d" % i
                recs = [{"kind": "job", "id": j, "name": "Job %d" % j, "extra_vars": json.dumps({n: v})} for j in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                self.assertIn("secret", dict((x["setting"], x) for x in settings)[n]["why"])
                self.assertNotIn(expect, json.dumps([templates, settings]))
        self.assertEqual(list(m.text_names('a = b; "c": d, e:f=g')), ["a", "c", "e", "f"])

    def test_yaml_lists_as_text(self):
        """1.29.0: a text tried as YAML when it starts like YAML without a colon - a list item ("- ", "-" and a tab or a
        new line), the document marker (--- alone or with a comment), a comment line, a %YAML directive - its list
        then never shown; -----BEGIN, -a and plain texts as they were."""
        hide = ["---\n- --password\n- Hx1", "--- # args\n- --password\n- Hx2", "---\n- svc_ansible\n- Hx3!",
                "# args\n- --password\n- Hx4", "-\t--password\n-\tHx5", "%YAML 1.2\n---\n- --password\n- Hx6", "-\n  Hx7"]
        for i, v in enumerate(hide):
            with self.subTest(value=v):
                n = "app_args%d" % i
                self.assertIsInstance(m._value(v), list)
                recs = [{"kind": "job", "id": j, "name": "Job %d" % j, "extra_vars": json.dumps({n: v})} for j in range(3)]
                templates, settings = m.audit(recs, {"repeat_min": 3})
                s = dict((x["setting"], x) for x in settings)[n]
                self.assertEqual(s["value"], "(same; not shown)")
                self.assertNotIn("Hx%d" % (i + 1), json.dumps([templates, settings]))
        for v in ("-----BEGIN x", "-a -b", "# just a comment", "%d items", "---", "--- not a list"):
            self.assertIsInstance(m._value(v), str, v)

    def test_repeat_min(self):
        _, settings = m.audit(records(), dict(OPTS, repeat_min=0))
        self.assertNotIn("check_disk_warn_pct", [x["setting"] for x in settings])
        templates, _ = m.audit(records(), dict(OPTS, ignore=["report_email_from"]))
        self.assertFalse(any(x["setting"] == "report_email_from" for t in templates for x in t["flags"]))

    def test_report(self):
        templates, settings = m.audit(records(), OPTS)
        r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []}, {"repeat_min": 3, "aap": "aap01.yoursite.mil"})
        self.assertEqual(r["status"], "critical")                               # secrets
        self.assertRegex(r["title"], r"^AAP template variables audit: \d+ settings in 9 templates to move$")
        self.assertEqual(r["subtitle"], "9 job templates and 1 workflow read in AAP at aap01.yoursite.mil")
        summary = dict((x["label"], x["value"]) for x in r["summary"])
        self.assertEqual((summary["Job templates"], summary["Workflows"], summary["Secrets"]), (9, 1, 3))
        by = r["sections"][0]
        self.assertEqual(by["title"], "By setting")
        self.assertEqual(by["columns"], ["Setting", "Why", "Templates", "Same value", "Value", "Move it to"])
        rows = dict((x[0], x) for x in by["rows"])
        self.assertEqual(rows["vcenter_password"][4], "(not shown: a secret)")
        self.assertEqual(rows["report_email_from"][4], "aap@yoursite.mil")
        titles = [s["title"] for s in r["sections"]]
        self.assertIn("Job template: Broken vars (ID 15)", titles)
        self.assertIn("Workflow: Daily health (ID 101)", titles)
        self.assertEqual(titles[1], "Job template: Certificate report (ID 13)")  # a secret first
        broken = next(s for s in r["sections"] if s["title"] == "Job template: Broken vars (ID 15)")
        self.assertIn("Its Variables could not be read: not YAML or JSON", broken["text"])
        self.assertEqual(titles[-1], "Nothing to move (1)")
        self.assertNotIn(SECRET_PW, json.dumps(r))
        ok = f.template_audit_report({"templates": [{"kind": "job", "id": 1, "name": "A", "settings": 0, "flags": []}], "settings": []})
        self.assertEqual((ok["status"], ok["title"]), ("ok", "AAP template variables audit: nothing to move"))
        bad = f.template_audit_report({"problems": ["AAP refused the login (HTTP 401)"]})
        self.assertEqual((bad["status"], bad["sections"][0]["title"]), ("critical", "Not read"))
        st = f.template_audit_stats({"templates": templates, "settings": settings})
        self.assertNotIn(SECRET_PW, json.dumps(st))
        self.assertEqual(st["templates"], 10)


class ReviewFixes(unittest.TestCase):
    """1.47.0 (review): a part not read is never "nothing to move"; mixed-type keys never stop the audit; a nested password
    repeated, and a URL's user:password, never shown (0.99.0 showed them)."""
    CLEAN = {"kind": "job", "id": 1, "name": "Clean", "extra_vars": "check_disk_warn_pct: 90\n", "settings": 1}

    def test_incomplete_is_never_clean(self):
        templates, settings = m.audit([self.CLEAN], OPTS)
        part = ["the job templates: job_templates/ could not be read (HTTP 500): A server error occurred."]
        r = f.template_audit_report({"templates": templates, "settings": settings, "problems": part})
        self.assertEqual((r["status"], r["title"]),
                         ("warning", "AAP template variables audit (incomplete: 1 part not read): nothing to move in what was read"))
        self.assertEqual(dict((x["label"], (x["value"], x.get("status"))) for x in r["summary"])["Not read"], (1, "warning"))
        self.assertEqual(r["sections"][0]["title"], "Not read")
        # findings already made keep their severity
        templates, settings = m.audit(records(), OPTS)
        r = f.template_audit_report({"templates": templates, "settings": settings, "problems": part})
        self.assertEqual(r["status"], "critical")
        self.assertRegex(r["title"], r"^AAP template variables audit \(incomplete: 1 part not read\): \d+ settings in 9 templates to move$")
        # nothing read at all: as before
        bad = f.template_audit_report({"problems": part})
        self.assertEqual((bad["status"], bad["title"]), ("critical", "AAP template variables audit: AAP could not be read"))
        ok = f.template_audit_report({"templates": templates[-1:], "settings": [], "problems": []})
        self.assertNotIn("incomplete", ok["title"])

    def test_mixed_keys(self):
        mixed = {"kind": "job", "id": 2, "name": "Mixed", "extra_vars": "routes:\n  443: one\n  default: two\n"}
        templates, settings = m.audit([mixed, self.CLEAN], OPTS)
        self.assertEqual([(t["name"], t["error"], t["settings"]) for t in templates], [("Mixed", "", 1), ("Clean", "", 1)])
        a, b = {443: "one", "default": "two"}, {"default": "two", 443: "one"}
        self.assertEqual(m.fingerprint(a), m.fingerprint(b))                         # a mapping's order does not count
        self.assertNotEqual(m.fingerprint({443: "one"}), m.fingerprint({"443": "one"}))
        self.assertNotEqual(m.fingerprint(1), m.fingerprint(True))
        self.assertNotEqual(m.fingerprint(1), m.fingerprint("1"))
        self.assertEqual(m.shown({443: "a", "b": 1, "443": "x"}), '{443: "a", "443": "x", "b": 1}')
        # repeated in 3 templates: the same; a dict is still never shown, a secret inside still a secret
        three = [dict(mixed, id=i, name="M%d" % i) for i in range(3)]
        three += [{"kind": "job", "id": 9, "name": "S%d" % i, "extra_vars": "svc:\n  8443: a\n  password: Pine-4-Cone\n"} for i in range(3)]
        templates, settings = m.audit(three, OPTS)
        s = dict((x["setting"], x) for x in settings)
        self.assertEqual((s["routes"]["same"], s["routes"]["value"], s["svc"]["why"], s["svc"]["value"]),
                         (3, "(same; not shown)", ["secret"], ""))
        self.assertNotIn("Pine-4-Cone", json.dumps([templates, settings]))
        # a value that cannot be weighed: its template says so, the others are audited
        real = m.classify

        def boom(name, value, *a):
            if name == "routes":
                raise RecursionError("too deep")
            return real(name, value, *a)
        m.classify = boom
        try:
            templates, settings = m.audit([mixed, self.CLEAN, records()[2]], OPTS)
        finally:
            m.classify = real
        self.assertEqual(templates[0]["error"], "routes could not be checked (RecursionError)")
        self.assertIn("vcenter_password", [x["setting"] for x in settings])
        r = f.template_audit_report({"templates": templates, "settings": settings, "problems": []})
        self.assertIn("Its Variables could not be read: routes could not be checked", json.dumps(r))

    LEAK_PW, URL_PW = "Synth-Pw-7731!", "Synth-Url-Pw-55"
    LEAKY = [{"kind": "job", "id": 30 + i, "name": "Svc %d" % i,
              "extra_vars": "service:\n  password: Synth-Pw-7731!\n"
                            "service_url: https://svc_aap:Synth-Url-Pw-55@app01.example.mil/api\n"} for i in range(3)]

    def test_nested_password_and_url_password_never_shown(self):
        """0.99.0: three templates with the same nested service.password showed it in By setting's Value (fixed in 1.24.0,
        8584dff); a URL with user:password@host showed whole (fixed in 1.24.0, 91359fe). The module's result, the report
        and the artifacts hold neither."""
        templates, settings = m.audit(self.LEAKY, OPTS)
        s = dict((x["setting"], x) for x in settings)
        self.assertEqual(("secret" in s["service"]["why"], "secret" in s["service_url"]["why"], s["service"]["value"], s["service_url"]["value"]), (True, True, "", ""))
        out = json.dumps([templates, settings]) + json.dumps(f.template_audit_report({"templates": templates, "settings": settings,
                                                                                      "problems": []}), default=str)
        out += json.dumps(f.template_audit_stats({"templates": templates, "settings": settings}))
        for v in (self.LEAK_PW, self.URL_PW, "svc_aap:"):
            self.assertNotIn(v, out, v)


class FakeAap(object):
    """Job templates 11-19 over pages of 3, workflow 101, surveys of 16 and 101; any write is logged (there must be none)."""

    def __init__(self):
        self.jobs, self.workflows, self.writes, self.page_size = copy.deepcopy(JOBS), copy.deepcopy(WORKFLOWS), [], 3
        self.refuse_survey, self.fail_page = False, None
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj=None):
                data = json.dumps(obj if obj is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _do(self, method):
                url = urllib.parse.urlparse(self.path)
                qs = dict(urllib.parse.parse_qsl(url.query))
                base = "/api/controller/v2/"
                if not url.path.startswith(base):
                    return self._send(404, {"detail": "Not found."})
                if self.headers.get("Authorization") != "Bearer goodtoken":
                    return self._send(401, {"detail": "Authentication credentials were not provided."})
                if method != "GET":
                    fake.writes.append((method, url.path))
                    return self._send(405, {"detail": "Method not allowed."})
                rest = url.path[len(base):].strip("/").split("/")
                return self._send(*fake.answer(rest, qs, base + "/".join(rest) + "/"))

            def do_GET(self):
                self._do("GET")

            def do_PATCH(self):
                self._do("PATCH")

            def do_POST(self):
                self._do("POST")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.srv.server_port

    def page(self, items, qs, path):
        p = int(qs.get("page", "1"))
        if self.fail_page is not None and p >= self.fail_page:
            return 500, {"detail": "A server error occurred."}
        chunk = items[(p - 1) * self.page_size:p * self.page_size]
        nxt = ("%s?page=%d&page_size=%s" % (path, p + 1, qs.get("page_size", "25"))) if p * self.page_size < len(items) else None
        return 200, {"count": len(items), "next": nxt, "results": copy.deepcopy(chunk)}

    def answer(self, rest, qs, path):
        if rest == ["me"]:
            return 200, {"results": [{"username": "auditor"}]}
        lists = {"job_templates": self.jobs, "workflow_job_templates": self.workflows}
        if len(rest) == 1 and rest[0] in lists:
            return self.page(lists[rest[0]], qs, path)
        if len(rest) == 3 and rest[0] in lists and rest[2] == "survey_spec":
            if self.refuse_survey:
                return 403, {"detail": "You do not have permission to perform this action."}
            return 200, {"name": "", "description": "", "spec": SURVEYS.get(int(rest[1]), [])}
        return 404, {"detail": "Not found."}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class AgainstFakeAap(unittest.TestCase):
    def setUp(self):
        self.aap = FakeAap()
        self.api = m.Api(m.connection({"CONTROLLER_HOST": self.aap.url, "CONTROLLER_OAUTH_TOKEN": "goodtoken"}), no_credential=m.NO_CREDENTIAL)
        self.assertEqual(self.api.probe(), "")
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.aap.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read(self):
        recs, problems = m.read(self.api, {"exclude": [], "workflows": True, "project": ""})
        self.assertEqual(problems, [])
        self.assertEqual([r["id"] for r in recs], [11, 12, 13, 14, 15, 16, 17, 18, 19, 101])   # every page
        self.assertEqual(recs[5]["survey"][1], {"variable": "db_password", "type": "text"})
        self.assertEqual(recs[-1]["kind"], "workflow")
        recs, problems = m.read(self.api, {"exclude": ["Disk report", "13"], "workflows": False, "project": "Site automation"})
        self.assertEqual([r["id"] for r in recs], [11, 14, 15, 16, 18, 19])
        _, problems = m.read(self.api, {"exclude": [], "workflows": False, "project": "No such project"})
        self.assertIn("no job template of a project named No such project", problems[0])
        self.aap.refuse_survey = True
        recs, problems = m.read(self.api, {"exclude": [], "workflows": True, "project": ""})
        self.assertEqual(len(recs), 10)
        self.assertIn("the survey of job template Patch hosts (ID 16) could not be read (HTTP 403)", problems[0])
        self.assertEqual(self.aap.writes, [])

    def test_no_credential(self):
        self.assertIn("Organization Auditor is enough", m.Api(m.connection({}), no_credential=m.NO_CREDENTIAL).probe())

    def run_pb(self, extra, *a, token="goodtoken"):
        ap = os.environ.get("ANSIBLE_PLAYBOOK", "ansible-playbook")
        env = dict(os.environ, CONTROLLER_HOST=self.aap.url, CONTROLLER_OAUTH_TOKEN=token, ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCALHOST_WARNING="False", ANSIBLE_INVENTORY_UNPARSED_WARNING="False")
        return subprocess.run([ap, "playbooks/aap_template_audit.yml", "-i", "localhost,", "-e",
                               json.dumps(dict(extra, ansible_python_interpreter=sys.executable))] + list(a),
                              cwd=REPO, env=env, capture_output=True, text=True, timeout=300)

    def test_the_playbook(self):
        """playbooks/aap_template_audit.yml against the fake: the site-wide settings read from the roles, the report printed and
        on the website, the email; no secret anywhere; nothing written to AAP; Check reads too."""
        web = os.path.join(self.tmp, "web")
        os.makedirs(web)
        out = self.run_pb({"report_web_dest": web, "aap_template_audit_per_enclave": ["dns_zone"]})
        self.assertEqual(out.returncode, 0, out.stdout[-3000:] + out.stderr[-2000:])
        self.assertIn("AAP template variables audit:", out.stdout)
        self.assertIn("in 9 templates to move", out.stdout)
        self.assertIn("report_email_from", out.stdout)                             # site_email's defaults: site-wide
        pages = [p for p in glob.glob(os.path.join(web, "**", "*.html"), recursive=True) if "index" not in os.path.basename(p)]
        self.assertTrue(pages, out.stdout[-3000:])
        with open(sorted(pages)[0]) as fh:
            page = fh.read()
        self.assertIn("By setting", page)
        self.assertIn("Job template: Certificate report (ID 13)", page)
        self.assertIn("docs/ENCLAVES.md)", page)                         # one AAP per enclave (all.yml of each)
        everything = out.stdout + out.stderr + "".join(open(p, encoding="utf-8").read() for p in
                                                       glob.glob(os.path.join(web, "**", "*"), recursive=True) if os.path.isfile(p))
        for s in (SECRET_PW, SECRET_TOKEN):
            self.assertNotIn(s, everything)
        self.assertEqual(self.aap.writes, [])
        # Check: reads all the same; fail on a finding when asked
        out = self.run_pb({"aap_template_audit_fail": True}, "--check")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("see the report above", out.stdout)
        self.assertIn("Job template: Certificate report (ID 13)", out.stdout)
        # a login AAP refuses: the job fails and says so
        out = self.run_pb({}, token="bad")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("AAP refused the login (HTTP 401)", out.stdout)
        self.assertEqual(self.aap.writes, [])

    def test_a_later_page_that_fails(self):
        """1.47.0 (review): page 1 read, page 2 refused - the report says incomplete (never "nothing to move"), the job
        passes; aap_template_audit_fail fails it after the report."""
        self.aap.jobs = [_jt(41, "Clean one", "check_disk_warn_pct: 90\n"), _jt(42, "Never read", "ansible_user: admin\n")]
        self.aap.workflows, self.aap.page_size, self.aap.fail_page = [], 1, 2
        recs, problems = m.read(self.api, {"exclude": [], "workflows": True, "project": ""})
        self.assertEqual(([r["id"] for r in recs], len(problems)), ([41], 1))
        self.assertIn("could not be read (HTTP 500)", problems[0])
        web = os.path.join(self.tmp, "web")
        os.makedirs(web)
        title = "AAP template variables audit (incomplete: 1 part not read): nothing to move in what was read"
        out = self.run_pb({"report_web_dest": web})
        self.assertEqual(out.returncode, 0, out.stdout[-3000:])
        self.assertIn(title, out.stdout)
        page = "".join(open(p, encoding="utf-8").read() for p in glob.glob(os.path.join(web, "**", "*.html"), recursive=True))
        self.assertIn("incomplete: 1 part not read", page)
        self.assertIn("Not read", page)
        out = self.run_pb({"aap_template_audit_fail": True})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn(title, out.stdout)
        self.assertLess(out.stdout.index("Template audit | " + title), out.stdout.index("see the report above"))
        self.assertEqual(self.aap.writes, [])

    def test_mixed_keys_and_secrets_end_to_end(self):
        """1.47.0 (review): mixed-type keys next to a clean template, and 0.99.0's two disclosures (a nested password in 3
        templates, a URL's user:password) - the job output, the website and the email hold no secret."""
        self.aap.jobs = ([_jt(51, "Mixed keys", "routes:\n  443: one\n  default: two\n"), _jt(52, "Clean one", "check_disk_warn_pct: 90\n")]
                         + [_jt(r["id"], r["name"], r["extra_vars"]) for r in ReviewFixes.LEAKY])
        self.aap.workflows = []
        web = os.path.join(self.tmp, "web")
        os.makedirs(web)
        out, mtext = self.with_mail({"report_web_dest": web})
        self.assertEqual(out.returncode, 0, out.stdout[-3000:])
        self.assertIn("in 3 templates to move", out.stdout)
        self.assertIn("Clean one (ID 52)", out.stdout)
        self.assertNotIn("TypeError", out.stdout + out.stderr)
        everything = out.stdout + out.stderr + mtext + "".join(open(p, encoding="utf-8").read() for p in
                                                               glob.glob(os.path.join(web, "**", "*"), recursive=True) if os.path.isfile(p))
        self.assertIn("service_url", everything)
        for s in (ReviewFixes.LEAK_PW, ReviewFixes.URL_PW, "svc_aap:"):
            self.assertNotIn(s, everything, s)

    def with_mail(self, extra):
        smtp = os.path.join(REPO, "tests", "email", "fake_smtp.py")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        mail = os.path.join(self.tmp, "mail")
        p = subprocess.Popen([sys.executable, smtp, str(port), mail])
        try:
            time.sleep(1)
            out = self.run_pb(dict(extra, report_email_to=["ops@example.mil"], report_email_from="aap@example.mil",
                                   report_email_smtp_host="127.0.0.1", report_email_smtp_port=port, report_email_security="none"))
        finally:
            p.kill()
            p.wait()
        msgs = glob.glob(os.path.join(mail, "*.eml"))
        self.assertEqual(len(msgs), 1, out.stdout[-3000:])
        with open(msgs[0]) as fh:
            return out, fh.read()

    def test_email(self):
        smtp = os.path.join(REPO, "tests", "email", "fake_smtp.py")
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        mail = os.path.join(self.tmp, "mail")
        p = subprocess.Popen([sys.executable, smtp, str(port), mail])
        try:
            time.sleep(1)
            out = self.run_pb({"report_email_to": ["ops@example.mil"], "report_email_from": "aap@example.mil",
                               "report_email_smtp_host": "127.0.0.1", "report_email_smtp_port": port, "report_email_security": "none"})
        finally:
            p.kill()
            p.wait()
        self.assertEqual(out.returncode, 0, out.stdout[-3000:])
        msgs = glob.glob(os.path.join(mail, "*.eml"))
        self.assertEqual(len(msgs), 1, out.stdout[-3000:])
        with open(msgs[0]) as fh:
            mtext = fh.read()
        self.assertIn("AAP template variables audit:", mtext)
        for s in (SECRET_PW, SECRET_TOKEN):
            self.assertNotIn(s, mtext)


if __name__ == "__main__":
    unittest.main()
