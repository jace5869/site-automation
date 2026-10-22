#!/usr/bin/env python3
"""Fake AAP (automation controller) API for playbooks/vm_deploy_poll.yml: one workflow job template,
"VM - deploy from ServiceNow ticket (workflow)" (id 42), found by name and started with extra_vars.

Login: a token (Authorization: Bearer goodtoken) or basic auth admin / goodpw.
API path: /api/controller/v2/ (AAP 2.5 and later); AAPMOCK_LEGACY=1: only /api/v2/ (older AAP).
AAPMOCK_NO_SURVEY=1: the workflow has no survey and no Prompt on launch (it takes no variables).
AAPMOCK_LAUNCH_FAIL=1: starting it answers 400 (e.g. an inventory or credential it needs is gone).
Usage: aapmock.py PORT   (each launch appended to $AAPMOCK_LOG as a JSON line)

Inventories (the deploy's vm_deploy_services; each host added or group joined appended to $AAPMOCK_INV_LOG):
  7 "Linux servers" (groups web, db; host old01 already in it, and in group web), 8 "Windows servers"
  (groups win_sql, win_dc), 9 "All servers" (constructed), 10 "Read only" (the user may not change it),
  11 and 12 both "Twice" (two organizations). AAPMOCK_OVERWRITE=1: inventory 7 has a source with Overwrite
  on. AAPMOCK_INV_FAIL=1: adding a host answers 403; AAPMOCK_INV_GARBLE=1: a page that is not JSON. State is
  kept while the fake runs.

Surveys (the pick lists, vm_pick_refresh.yml; each survey written appended to $AAPMOCK_SURVEY_LOG):
  job templates 51 "VM - delete picked snapshots" (vm_snapshot_picks: multiple select, its choices as text one
  per line - older AAP - and a default), 52 "VM - restart picked VMs" (vm_reboot_picks: single select, and
  vm_power_mode), 53 "Text question" (vm_snapshot_picks is a text box), 54 "No survey", 55 "Survey off" (a
  survey that is turned off), 56 "Both" (a workflow is named so too), 57 "Read only" (the user may not change
  it); workflow job templates 61 "VM - delete picked snapshots (workflow)" (vm_snapshot_picks: multiple select,
  a list) and 62 "Both". AAPMOCK_SURVEY_FAIL=1: writing a survey answers 400.

Jobs (the reports' strip of past runs, roles/site_email site_report_history): job template 71 "Snapshot cleanup"
has jobs 901-918 (successful, failed, one canceled, one still running; some with the artifact site_report_run,
the older ones without), 72 none. The list leaves the artifacts out, as AAP does: each job's own page has them.
AAPMOCK_JOBS_FORBID=1: the list answers 403 (the user has no Read role on the template).
"""
import base64
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WF = {"id": 42, "name": "VM - deploy from ServiceNow ticket (workflow)"}
LOG = os.environ.get("AAPMOCK_LOG", "aapmock.jsonl")
INV_LOG = os.environ.get("AAPMOCK_INV_LOG", "aapmock-inv.jsonl")
JOBS = [1000]


def _inv(i, name, kind="", edit=True, org="Default"):
    return {"id": i, "name": name, "kind": kind,
            "summary_fields": {"organization": {"name": org}, "user_capabilities": {"edit": edit}}}


INVS = [_inv(7, "Linux servers"), _inv(8, "Windows servers"), _inv(9, "All servers", kind="constructed"),
        _inv(10, "Read only", edit=False), _inv(11, "Twice", org="Org A"), _inv(12, "Twice", org="Org B")]
GROUPS = {7: [{"id": 70, "name": "web"}, {"id": 71, "name": "db"}], 8: [{"id": 80, "name": "win_sql"}, {"id": 81, "name": "win_dc"}]}
HOSTS = {7: [{"id": 500, "name": "old01", "variables": "{}"}]}
MEMBERS = {70: {500}}
NEXT_HOST = [600]
SURVEY_LOG = os.environ.get("AAPMOCK_SURVEY_LOG", "aapmock-survey.jsonl")


def _job(i, status, value=None, rstatus=None):
    j = {"id": i, "job_template": 71, "status": status, "finished": "2026-10-%02dT06:01:12.5Z" % (i - 900), "artifacts": {}}
    if rstatus:
        j["artifacts"] = {"site_report_run": {"status": rstatus, "value": value, "label": "Deleted"}, "other": 1}
    return j


JOBS_71 = ([_job(i, "successful") for i in range(901, 906)] + [_job(906, "failed")] + [_job(907, "canceled")]
           + [_job(i, "successful", i - 905, "ok") for i in range(908, 915)] + [_job(915, "successful", 0, "warning")]
           + [_job(916, "failed", 2, "critical"), _job(917, "successful", 5, "ok"), _job(918, "running")])


def _tpl(kind, i, name, enabled=True, edit=True):
    return {"id": i, "name": name, "type": "job_template" if kind == "job" else "workflow_job_template", "survey_enabled": enabled,
            "summary_fields": {"user_capabilities": {"edit": edit}}}


def _q(var, kind, choices=None, default="", required=False):
    q = {"question_name": var.replace("_", " "), "question_description": "Pick from the list", "variable": var, "type": kind,
         "required": required, "default": default, "min": None, "max": None}
    if choices is not None:
        q["choices"] = choices
    return q


TPLS = {"job": [_tpl("job", 51, "VM - delete picked snapshots"), _tpl("job", 52, "VM - restart picked VMs"),
                _tpl("job", 53, "Text question"), _tpl("job", 54, "No survey", enabled=False), _tpl("job", 55, "Survey off", enabled=False),
                _tpl("job", 56, "Both"), _tpl("job", 57, "Read only", edit=False)],
        "workflow": [_tpl("workflow", 61, "VM - delete picked snapshots (workflow)"), _tpl("workflow", 62, "Both")]}
SPECS = {("job", 51): {"name": "", "description": "", "spec": [_q("vm_snapshot_picks", "multiselect", "old one\nold two", "old two", True)]},
         ("job", 52): {"name": "", "description": "", "spec": [_q("vm_reboot_picks", "multiplechoice", ["x"], "x"),
                                                                _q("vm_power_mode", "multiplechoice", ["guest", "hard"], "guest")]},
         ("job", 53): {"name": "", "description": "", "spec": [_q("vm_snapshot_picks", "textarea")]},
         ("job", 54): {},
         ("job", 55): {"name": "", "description": "", "spec": [_q("vm_reboot_picks", "multiselect", [])]},
         ("job", 56): {"name": "", "description": "", "spec": [_q("vm_reboot_picks", "multiselect", [])]},
         ("job", 57): {"name": "", "description": "", "spec": [_q("vm_reboot_picks", "multiselect", [])]},
         ("workflow", 61): {"name": "", "description": "", "spec": [_q("vm_snapshot_picks", "multiselect", [], "")]},
         ("workflow", 62): {"name": "", "description": "", "spec": [_q("vm_reboot_picks", "multiselect", [])]}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        if code == 204:
            self.send_response(204)
            self.end_headers()
            return
        data = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        a = self.headers.get("Authorization", "")
        if a == "Bearer goodtoken":
            return True
        if a.startswith("Basic "):
            return base64.b64decode(a[6:]).decode() == "admin:goodpw"
        return False

    def _handle(self, method):
        n = int(self.headers.get("Content-Length", "0") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        base = "/api/v2/" if os.environ.get("AAPMOCK_LEGACY") == "1" else "/api/controller/v2/"
        if not url.path.startswith(base):
            return self._send(404, {"detail": "Not found."})
        if not self._authed():
            return self._send(401, {"detail": "Authentication credentials were not provided."})
        rest = url.path[len(base):].strip("/").split("/")
        no_survey = os.environ.get("AAPMOCK_NO_SURVEY") == "1"
        inv = self._inventories(method, rest, q, body)
        if inv is not None:
            return self._send(*inv)
        jb = self._jobs(method, rest, q)
        if jb is not None:
            return self._send(*jb)
        sv = self._surveys(method, rest, q, body)
        if sv is not None:
            return self._send(*sv)
        if rest == ["workflow_job_templates"] and method == "GET":
            found = [WF] if q.get("name") == WF["name"] else []
            return self._send(200, {"count": len(found), "results": found})
        if rest == ["workflow_job_templates", str(WF["id"]), "launch"]:
            if method == "GET":
                return self._send(200, {"survey_enabled": not no_survey, "ask_variables_on_launch": False,
                                        "variables_needed_to_start": [] if no_survey else ["vm_deploy_ticket"],
                                        "can_start_without_user_input": False})
            if os.environ.get("AAPMOCK_LAUNCH_FAIL") == "1":
                return self._send(400, {"resources_needed_to_start": ["Job Template 'VM - deploy' is missing or undefined."]})
            JOBS[0] += 1
            ev = body.get("extra_vars") or {}
            with open(LOG, "a") as f:
                f.write(json.dumps({"workflow": WF["id"], "extra_vars": ev, "job": JOBS[0], "api": base}) + "\n")
            out = {"workflow_job": JOBS[0], "id": JOBS[0], "url": "%sworkflow_jobs/%d/" % (base, JOBS[0])}
            if no_survey:
                out["ignored_fields"] = {"extra_vars": ev}
            return self._send(201, out)
        return self._send(404, {"detail": "Not found."})

    def _jobs(self, method, rest, q):
        """(status, answer) for jobs/ and jobs/<id>/, or None."""
        if method != "GET" or not rest or rest[0] != "jobs":
            return None
        if len(rest) == 2:
            found = [j for j in JOBS_71 if str(j["id"]) == rest[1]]
            return (200, found[0]) if found else (404, {"detail": "Not found."})
        if os.environ.get("AAPMOCK_JOBS_FORBID") == "1":
            return 403, {"detail": "You do not have permission to perform this action."}
        found = [j for j in JOBS_71 if str(j["job_template"]) == q.get("job_template")]
        if q.get("id__lt"):
            found = [j for j in found if j["id"] < int(q["id__lt"])]
        if q.get("status__in"):
            found = [j for j in found if j["status"] in q["status__in"].split(",")]
        found.sort(key=lambda j: -j["id"] if q.get("order_by") == "-id" else j["id"])
        found = found[:int(q.get("page_size", 25))]
        return 200, {"count": len(found), "results": [{k: v for k, v in j.items() if k != "artifacts"} for j in found]}

    def _surveys(self, method, rest, q, body):
        """(status, answer) for the templates' surveys, or None (the deploy's workflow 42 is answered below)."""
        kinds = {"job_templates": "job", "workflow_job_templates": "workflow"}
        if not rest or rest[0] not in kinds:
            return None
        kind = kinds[rest[0]]
        if rest[1:] == [] and method == "GET":
            if kind == "workflow" and q.get("name") == WF["name"]:
                return None
            found = [t for t in TPLS[kind] if t["name"] == q.get("name")]
            return 200, {"count": len(found), "results": found}
        if len(rest) < 2 or not rest[1].isdigit():
            return None
        n = int(rest[1])
        t = [x for x in TPLS[kind] if x["id"] == n]
        if not t:
            return None if (kind, n) == ("workflow", WF["id"]) else (404, {"detail": "Not found."})
        if rest[2:] == [] and method == "GET":
            return 200, t[0]
        if rest[2:] != ["survey_spec"]:
            return 404, {"detail": "Not found."}
        if method == "GET":
            return 200, SPECS[(kind, n)]
        if not t[0]["summary_fields"]["user_capabilities"]["edit"]:
            return 403, {"detail": "You do not have permission to perform this action."}
        if os.environ.get("AAPMOCK_SURVEY_FAIL") == "1":
            return 400, {"error": "Survey question 1 (vm_snapshot_picks): bad choices."}
        SPECS[(kind, n)] = body
        with open(SURVEY_LOG, "a") as f:
            f.write(json.dumps({"kind": kind, "id": n, "spec": body}) + "\n")
        return 200, {}

    def _inventories(self, method, rest, q, body):
        """(status, answer) for the inventory part of the API, or None."""
        def page(items):
            items = [x for x in items if "name" not in q or x["name"] == q["name"]]
            return 200, {"count": len(items), "results": items}

        def log(what):
            with open(INV_LOG, "a") as f:
                f.write(json.dumps(what) + "\n")
        if rest == ["me"] and method == "GET":
            return 200, {"count": 1, "results": [{"username": "admin"}]}
        if rest == ["inventories"] and method == "GET":
            return page(INVS)
        if len(rest) < 2 or rest[0] not in ("inventories", "groups") or not rest[1].isdigit():
            return None
        n = int(rest[1])
        if rest[0] == "inventories":
            if not any(i["id"] == n for i in INVS):
                return 404, {"detail": "Not found."}
            if rest[2:] == [] and method == "GET":
                return 200, [i for i in INVS if i["id"] == n][0]
            if rest[2:] == ["groups"] and method == "GET":
                return page(GROUPS.get(n, []))
            if rest[2:] == ["inventory_sources"] and method == "GET":
                srcs = [{"id": 900, "name": "core file", "overwrite": True}] if n == 7 and os.environ.get("AAPMOCK_OVERWRITE") == "1" else []
                return page(srcs)
            if rest[2:] == ["hosts"] and method == "GET":
                return page(HOSTS.get(n, []))
            if rest[2:] == ["hosts"] and method == "POST":
                if os.environ.get("AAPMOCK_INV_FAIL") == "1":
                    return 403, {"detail": "You do not have permission to perform this action."}
                if os.environ.get("AAPMOCK_INV_GARBLE") == "1":   # a proxy's login page in AAP's place
                    return 200, "<html>Please log in</html>"
                if any(h["name"] == body.get("name") for h in HOSTS.get(n, [])):
                    return 400, {"__all__": ["Host with this Name and Inventory already exists."]}
                NEXT_HOST[0] += 1
                h = {"id": NEXT_HOST[0], "name": body.get("name"), "description": body.get("description", ""),
                     "variables": body.get("variables", "")}
                HOSTS.setdefault(n, []).append(h)
                log({"added": h["name"], "inventory": n, "variables": json.loads(h["variables"] or "{}"),
                     "description": h["description"]})
                return 201, h
            return 404, {"detail": "Not found."}
        grp = [g for gs in GROUPS.values() for g in gs if g["id"] == n]
        if not grp or rest[2:] != ["hosts"]:
            return 404, {"detail": "Not found."}
        if method == "GET":
            return page([h for hs in HOSTS.values() for h in hs if h["id"] in MEMBERS.get(n, set())])
        hid = body.get("id")
        if not any(h["id"] == hid for hs in HOSTS.values() for h in hs):
            return 400, {"msg": "Host %s not found" % hid}
        MEMBERS.setdefault(n, set()).add(hid)
        log({"joined": grp[0]["name"], "host": hid})
        return 204, None

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
