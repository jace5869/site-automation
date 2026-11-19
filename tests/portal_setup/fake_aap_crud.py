#!/usr/bin/env python3
"""A stand-in of AAP's API for tests/portal_setup/run_portal_setup_test.sh (1.67.0 part C, "AAP - set up portal actions"):
the objects that job reads and writes, kept in memory and in a JSON file (FAKE_AAP_STATE) so the test can look and change
one by hand. Under api/controller/v2/, Bearer FAKE_AAP_TOKEN. Not AAP: just enough for the setup job.
  organizations, projects, inventories, credentials, execution_environments, users        GET ?name=
  job_templates, workflow_job_templates     GET ?name= / <id>/, POST, PATCH <id>/
  job_templates/<id>/credentials/           GET, POST {id} or {id, disassociate}
  workflow_job_templates/<id>/workflow_nodes/   POST;  workflow_job_template_nodes/?workflow_job_template=  GET
  workflow_job_template_nodes/<id>/         DELETE;  <id>/create_approval_template/, <id>/{success,failure,always}_nodes/ POST
                                            ({id}, or {id, disassociate: true}: the link removed)
  <kind>/<id>/object_roles/                 GET (Execute, Approve, Admin)
  users/<id>/roles/                         GET, POST {id}
  <kind>/<id>/schedules/ GET, POST;  schedules/<id>/  PATCH
  credentials/<id>/                         PATCH (a rename: 400 when the organization has that name - AAP Credentials setup)
A write to a path in the state's "fail_paths" answers 500 (a failure mid-run). Every write is a line in FAKE_AAP_LOG.  fake_aap_crud.py PORT"""
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = os.environ.get("FAKE_AAP_TOKEN", "setup-token")
STATE = os.environ.get("FAKE_AAP_STATE", "")
LOG = os.environ.get("FAKE_AAP_LOG", "")
BASE = "/api/controller/v2/"
LOCK = threading.Lock()
SEED = {"next": 1000,
        "organizations": [{"id": 1, "name": "Default"}],
        "projects": [{"id": 2, "name": "Site automation"}],
        "inventories": [{"id": 3, "name": "Site hosts"}, {"id": 4, "name": "Windows hosts"}],
        "execution_environments": [{"id": 5, "name": "Site EE"}],
        "credentials": [{"id": 10, "name": "Linux machine"}, {"id": 11, "name": "Windows machine"}, {"id": 12, "name": "AAP reader"},
                        {"id": 13, "name": "ACT model key"}, {"id": 14, "name": "STIG Manager API"}],
        "users": [{"id": 20, "name": "svc_site_portal", "username": "svc_site_portal", "roles": []}],
        "job_templates": [], "workflow_job_templates": [], "nodes": [], "approvals": [], "schedules": [], "roles": []}


def load():
    if STATE and os.path.exists(STATE):
        with open(STATE) as fh:
            return json.load(fh)
    return json.loads(json.dumps(SEED))


def save(s):
    if STATE:
        with open(STATE + ".new", "w") as fh:
            json.dump(s, fh, indent=1)
        os.replace(STATE + ".new", STATE)


def log(rec):
    if LOG:
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec) + "\n")


def new_id(s):
    s["next"] += 1
    return s["next"]


def roles_of(s, kind, oid):
    """The object's roles, made on first ask: Admin, Execute (and Approve on a workflow)."""
    names = ["Admin", "Execute"] + (["Approve"] if kind == "workflow_job_templates" else [])
    if kind == "organizations":                                   # 1.75.3: an organization's roles
        names = ["Admin", "Member", "Auditor", "Execute", "Read"]
    out = []
    for n in names:
        r = next((r for r in s["roles"] if r["kind"] == kind and r["obj"] == oid and r["name"] == n), None)
        if r is None:
            r = {"id": new_id(s), "kind": kind, "obj": oid, "name": n}
            s["roles"].append(r)
        out.append(r)
    return out


def node_view(s, n):
    if n.get("approval"):
        a = next(a for a in s["approvals"] if a["id"] == n["approval"])
        ujt = {"id": a["id"], "name": a["name"], "description": a["description"], "timeout": a["timeout"], "unified_job_type": "workflow_approval"}
    elif n.get("unified_job_template"):
        jt = next(j for j in s["job_templates"] if j["id"] == n["unified_job_template"])
        ujt = {"id": jt["id"], "name": jt["name"], "unified_job_type": "job"}
    else:
        ujt = {}
    return {"id": n["id"], "identifier": n["identifier"], "workflow_job_template": n["wf"], "all_parents_must_converge": n["converge"],
            "success_nodes": n["success"], "failure_nodes": n["failure"], "always_nodes": n["always"],
            "extra_data": n.get("extra_data") or {}, "summary_fields": {"unified_job_template": ujt}}


TPL_FIELDS = ("name", "description", "job_type", "project", "playbook", "inventory", "ask_variables_on_launch", "ask_limit_on_launch",
              "become_enabled", "extra_vars", "execution_environment")
WF_FIELDS = ("name", "description", "organization", "ask_variables_on_launch", "ask_limit_on_launch", "extra_vars")


def rrule_problem(rr):
    """What a real controller's schedule validation refuses: no DTSTART, no INTERVAL, a rule that is not RRULE:FREQ=..."""
    rr = str(rr or "")
    if not re.match(r"^DTSTART(;TZID=[A-Za-z0-9_/+-]+:|:)\d{8}T\d{6}Z? RRULE:", rr):
        return "Valid DTSTART required in rrule. Value should start with: DTSTART:YYYYMMDDTHHMMSSZ or DTSTART;TZID=zone:YYYYMMDDTHHMMSS"
    if "INTERVAL=" not in rr:
        return "INTERVAL required in rrule: %s" % rr
    return ""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=None):
        raw = b"" if code == 204 else json.dumps(body if body is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def ok(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            self.send(401, {"detail": "Authentication credentials were not provided."})
            return False
        return True

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self.ok():
            return None
        u = urlsplit(self.path)
        p, q = u.path[len(BASE):], dict((k, v[0]) for k, v in parse_qs(u.query).items())
        with LOCK:
            s = load()
            if p == "me/":
                return self.send(200, {"results": [{"username": "setup-admin"}]})
            m = re.match(r"^(organizations|projects|inventories|credentials|execution_environments|users|job_templates|workflow_job_templates)/$", p)
            if m:
                want = q.get("name", q.get("username"))
                rows = [dict((k, v) for k, v in r.items() if k != "roles") for r in s[m.group(1)]
                        if want is None or r.get("name") == want or r.get("username") == want]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/$", p)
            if m:
                r = next((r for r in s[m.group(1)] if r["id"] == int(m.group(2))), None)
                return self.send(200, r) if r else self.send(404, {"detail": "Not found."})
            m = re.match(r"^job_templates/(\d+)/credentials/$", p)
            if m:
                jt = next(r for r in s["job_templates"] if r["id"] == int(m.group(1)))
                rows = [c for c in s["credentials"] if c["id"] in jt.get("credentials", [])]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            if p == "workflow_job_template_nodes/":
                rows = [node_view(s, n) for n in s["nodes"] if str(n["wf"]) == q.get("workflow_job_template")]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            m = re.match(r"^(job_templates|workflow_job_templates|organizations)/(\d+)/object_roles/$", p)
            if m:
                rows = [{"id": r["id"], "name": r["name"]} for r in roles_of(s, m.group(1), int(m.group(2)))]
                save(s)
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            m = re.match(r"^users/(\d+)/roles/$", p)
            if m:
                usr = next(x for x in s["users"] if x["id"] == int(m.group(1)))
                rows = [{"id": rid} for rid in usr["roles"]]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/schedules/$", p)
            if m:
                rows = [x for x in s["schedules"] if x["kind"] == m.group(1) and x["obj"] == int(m.group(2))]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
        return self.send(404, {"detail": "Not found."})

    def do_POST(self):
        if not self.ok():
            return None
        p = urlsplit(self.path).path[len(BASE):]
        b = self.body()
        log({"method": "POST", "path": p, "body": b})
        with LOCK:
            s = load()
            if p in (s.get("fail_paths") or []) or (p in (s.get("fail_attach_paths") or []) and not b.get("disassociate")):
                # a refusal that echoes a value back (as a validation answer may): what reads it must not print it
                return self.send(500, {"detail": "a failure the test asked for", "inputs": {"password": "SECRET-PW-ECHO"}})
            try:
                m = re.match(r"^(job_templates|workflow_job_templates)/$", p)
                if m:
                    if any(r["name"] == b.get("name") for r in s[m.group(1)]):
                        return self.send(400, {"name": ["already exists"]})
                    fields = TPL_FIELDS if m.group(1) == "job_templates" else WF_FIELDS
                    r = dict((k, b.get(k)) for k in fields)
                    r["id"] = new_id(s)
                    if m.group(1) == "job_templates":
                        r["credentials"] = []
                    s[m.group(1)].append(r)
                    return self.send(201, r)
                m = re.match(r"^job_templates/(\d+)/credentials/$", p)
                if m:
                    jt = next(r for r in s["job_templates"] if r["id"] == int(m.group(1)))
                    if b.get("disassociate"):
                        jt["credentials"] = [c for c in jt["credentials"] if c != b["id"]]
                    elif b["id"] not in jt["credentials"]:
                        jt["credentials"].append(b["id"])
                    return self.send(204)
                m = re.match(r"^workflow_job_templates/(\d+)/workflow_nodes/$", p)
                if m:
                    n = {"id": new_id(s), "wf": int(m.group(1)), "identifier": b.get("identifier") or "", "converge": bool(b.get("all_parents_must_converge")),
                         "unified_job_template": b.get("unified_job_template"), "approval": None, "success": [], "failure": [], "always": [],
                         "extra_data": b.get("extra_data") or {}}
                    jt = next((j for j in s["job_templates"] if j["id"] == n["unified_job_template"]), None)
                    if n["extra_data"] and jt and not jt.get("ask_variables_on_launch"):     # as AAP: only a prompted field
                        return self.send(400, {"extra_data": ["Variables are not allowed on launch for this template."]})
                    s["nodes"].append(n)
                    return self.send(201, {"id": n["id"]})
                m = re.match(r"^workflow_job_template_nodes/(\d+)/create_approval_template/$", p)
                if m:
                    n = next(x for x in s["nodes"] if x["id"] == int(m.group(1)))
                    a = {"id": new_id(s), "name": b.get("name"), "description": b.get("description", ""), "timeout": int(b.get("timeout") or 0)}
                    s["approvals"].append(a)
                    n["approval"] = a["id"]
                    return self.send(201, a)
                m = re.match(r"^workflow_job_template_nodes/(\d+)/(success|failure|always)_nodes/$", p)
                if m:
                    n = next(x for x in s["nodes"] if x["id"] == int(m.group(1)))
                    if b.get("disassociate"):                   # a link removed (the node stays)
                        n[m.group(2)] = [x for x in n[m.group(2)] if x != int(b["id"])]
                    elif int(b["id"]) not in n[m.group(2)]:
                        n[m.group(2)].append(int(b["id"]))
                    return self.send(204)
                m = re.match(r"^users/(\d+)/roles/$", p)
                if m:
                    usr = next(x for x in s["users"] if x["id"] == int(m.group(1)))
                    if b["id"] not in usr["roles"]:
                        usr["roles"].append(b["id"])
                    return self.send(204)
                m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/schedules/$", p)
                if m:
                    bad = "Schedules are refused (the state says so)" if s.get("reject_schedules") else rrule_problem(b.get("rrule"))
                    if bad:
                        return self.send(400, {"rrule": [bad]})
                    sc = dict(b, id=new_id(s), kind=m.group(1), obj=int(m.group(2)))
                    s["schedules"].append(sc)
                    return self.send(201, sc)
            finally:
                save(s)
        return self.send(404, {"detail": "Not found."})

    def do_PATCH(self):
        if not self.ok():
            return None
        p = urlsplit(self.path).path[len(BASE):]
        b = self.body()
        log({"method": "PATCH", "path": p, "body": b})
        with LOCK:
            s = load()
            if p in (s.get("fail_paths") or []):
                return self.send(500, {"detail": "a failure the test asked for"})
            m = re.match(r"^(job_templates|workflow_job_templates|schedules|credentials)/(\d+)/$", p)
            if m:
                r = next((r for r in s[m.group(1)] if r["id"] == int(m.group(2))), None)
                if r is None:
                    return self.send(404, {"detail": "Not found."})
                if m.group(1) == "credentials" and any(c["name"] == b.get("name") and c.get("organization") == r.get("organization")
                                                       and c["id"] != r["id"] for c in s["credentials"]):
                    return self.send(400, {"__all__": ["Credential with this Name and Organization already exists."]})
                if m.group(1) == "schedules" and "rrule" in b and rrule_problem(b["rrule"]):
                    return self.send(400, {"rrule": [rrule_problem(b["rrule"])]})
                r.update(b)
                save(s)
                return self.send(200, r)
        return self.send(404, {"detail": "Not found."})

    def do_DELETE(self):
        if not self.ok():
            return None
        p = urlsplit(self.path).path[len(BASE):]
        log({"method": "DELETE", "path": p})
        with LOCK:
            s = load()
            m = re.match(r"^workflow_job_template_nodes/(\d+)/$", p)
            if m:
                nid = int(m.group(1))
                s["nodes"] = [n for n in s["nodes"] if n["id"] != nid]
                for n in s["nodes"]:
                    for k in ("success", "failure", "always"):
                        n[k] = [x for x in n[k] if x != nid]
                save(s)
                return self.send(204)
        return self.send(404, {"detail": "Not found."})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H)
    srv.daemon_threads = True
    srv.serve_forever()
