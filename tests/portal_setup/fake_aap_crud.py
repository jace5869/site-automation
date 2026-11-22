#!/usr/bin/env python3
"""A stand-in of AAP's API for tests/portal_setup/run_portal_setup_test.sh (1.67.0 part C, "AAP - set up portal actions"):
the objects that job reads and writes, kept in memory and in a JSON file (FAKE_AAP_STATE) so the test can look and change
one by hand. Under api/controller/v2/, Bearer FAKE_AAP_TOKEN. Not AAP: just enough for the setup job.
  organizations, projects, inventories, credentials, execution_environments, users        GET ?name=
  job_templates, workflow_job_templates     GET ?name= / <id>/, POST, PATCH <id>/
  job_templates/<id>/credentials/           GET, POST {id} or {id, disassociate}
  workflow_job_templates/<id>/workflow_nodes/   POST;  workflow_job_template_nodes/?workflow_job_template=  GET
  workflow_job_template_nodes/<id>/         DELETE; PATCH (inventory, limit, unified_job_template, extra_data);  <id>/create_approval_template/, <id>/{success,failure,always}_nodes/ POST
                                            ({id}, or {id, disassociate: true}: the link removed)
  <kind>/<id>/labels/                       GET;  POST {name, organization} (made if new, then added) or {id, disassociate: true}  (1.78.1)
  <kind>/<id>/object_roles/                 GET (Execute, Approve, Admin)
  users/<id>/roles/                         GET, POST {id}
  <kind>/<id>/schedules/ GET, POST;  schedules/<id>/  PATCH
  credentials/<id>/                         PATCH (a rename: 400 when the organization has that name - AAP Credentials setup)
  a list GET filters on any other field too (?inventory=3);  schedules/ GET;  workflow_job_template_nodes/<id>/ PATCH;
  inventories/<id>/groups/, inventories/<id>/hosts/   GET ?name= (the state's groups and hosts, each with its inventory)
  <kind>/<id>/survey_spec/                  GET, POST (1.107.0: the state's "surveys" {"<kind>/<id>": spec}); a node's job_type and
                                            diff_mode (POST, PATCH) only on a template that prompts for them, its extra_data only on
                                            one that prompts for Variables or has those questions in its survey (on), as AAP
A read of a path in "fail_get_paths" answers 404 (1.107.0). A write to a path in the state's "fail_paths" answers 500 (a failure mid-run); a link from a node whose identifier is in
"fail_links_from" too. Every write is a line in FAKE_AAP_LOG.  fake_aap_crud.py PORT"""
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
        "projects": [{"id": 2, "name": "Site automation", "organization": 1}],
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
    elif n.get("workflow"):                                       # 1.79.19: a nested workflow's node
        w = next(x for x in s["workflow_job_templates"] if x["id"] == n["workflow"])
        ujt = {"id": w["id"], "name": w["name"], "unified_job_type": "workflow_job"}
    elif n.get("unified_job_template"):
        jt = next(j for j in s["job_templates"] if j["id"] == n["unified_job_template"])
        ujt = {"id": jt["id"], "name": jt["name"], "unified_job_type": "job"}
    else:
        ujt = {}
    return {"id": n["id"], "identifier": n["identifier"], "workflow_job_template": n["wf"], "all_parents_must_converge": n["converge"],
            "success_nodes": n["success"], "failure_nodes": n["failure"], "always_nodes": n["always"],
            "extra_data": n.get("extra_data") or {}, "summary_fields": {"unified_job_template": ujt},
            "job_type": n.get("job_type"), "diff_mode": n.get("diff_mode"),
            "inventory": n.get("inventory"), "limit": n.get("limit") or ""}


def schedule_view(s, x):
    par = next((r for r in s[x["kind"]] if r["id"] == x["obj"]), {})
    return dict(x, unified_job_template=x["obj"], summary_fields={"unified_job_template": {
        "id": x["obj"], "name": par.get("name"), "unified_job_type": "job" if x["kind"] == "job_templates" else "workflow_job"}})


def by_fields(rows, q):
    """The other query fields as filters (?inventory=3), as AAP's list views do."""
    for k, v in q.items():
        if k not in ("name", "username", "page_size", "page", "workflow_job_template"):
            rows = [r for r in rows if str(r.get(k)) == v]
    return rows


TPL_FIELDS = ("name", "description", "job_type", "project", "playbook", "inventory", "ask_variables_on_launch", "ask_limit_on_launch",
              "become_enabled", "extra_vars", "execution_environment", "organization", "ask_inventory_on_launch",
              "ask_job_type_on_launch", "ask_diff_mode_on_launch", "survey_enabled", "diff_mode", "limit")
WF_FIELDS = ("name", "description", "organization", "ask_variables_on_launch", "ask_limit_on_launch", "extra_vars", "survey_enabled")


def node_refused(s, jt, b):
    """As AAP: what a node may give its template - Variables only when it prompts for them or its survey (on) has the
    question, Job type / Show changes only when it prompts for them. -> the 400 answer, or None."""
    if jt is None:
        return None
    if b.get("extra_data") and not jt.get("ask_variables_on_launch"):
        qs = [q.get("variable") for q in ((s.get("surveys") or {}).get("job_templates/%d" % jt["id"]) or {}).get("spec") or []] \
            if jt.get("survey_enabled") else []
        if any(k not in qs for k in b["extra_data"]):
            return {"extra_data": ["Variables are not allowed on launch for this template."]}
    if b.get("job_type") and not jt.get("ask_job_type_on_launch"):
        return {"job_type": ["Field is not configured to prompt on launch."]}
    if b.get("diff_mode") is not None and not jt.get("ask_diff_mode_on_launch"):
        return {"diff_mode": ["Field is not configured to prompt on launch."]}
    return None


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
            if p in (s.get("fail_get_paths") or []):          # 1.107.0: a read that fails (the test's fail_get_paths)
                return self.send(404, {"detail": "Not found."})
            if p == "me/":
                return self.send(200, {"results": [{"username": "setup-admin"}]})
            m = re.match(r"^(organizations|projects|inventories|credentials|execution_environments|users|job_templates|workflow_job_templates|labels)/$", p)
            if m:
                want = q.get("name", q.get("username"))
                rows = [dict((k, v) for k, v in r.items() if k != "roles") for r in s.get(m.group(1), [])
                        if want is None or r.get("name") == want or r.get("username") == want]
                rows = by_fields(rows, q)
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            if p == "jobs/":                                # 1.93.0: this template's earlier runs (state "jobs": id, job_template, status, artifacts)
                rows = [dict((k, v) for k, v in j.items() if k != "artifacts") for j in s.get("jobs", [])
                        if str(j.get("job_template")) == q.get("job_template", str(j.get("job_template")))
                        and j["id"] < int(q.get("id__lt", "999999999"))
                        and j.get("status") == q.get("status", j.get("status"))
                        and j.get("job_type", "run") == q.get("job_type", j.get("job_type", "run"))
                        and j.get("status") in q.get("status__in", j.get("status")).split(",")]
                rows.sort(key=lambda j: -j["id"])
                size = int(q.get("page_size", "25"))
                return self.send(200, {"count": len(rows), "results": rows[:size], "next": self.path if len(rows) > size else None})
            m = re.match(r"^jobs/(\d+)/$", p)
            if m:
                j = next((j for j in s.get("jobs", []) if j["id"] == int(m.group(1))), None)
                return self.send(200, j) if j else self.send(404, {"detail": "Not found."})
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/$", p)
            if m:
                r = next((r for r in s[m.group(1)] if r["id"] == int(m.group(2))), None)
                return self.send(200, r) if r else self.send(404, {"detail": "Not found."})
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/labels/$", p)
            if m:                                                                       # 1.78.1 and the job-templates job: a template's labels
                r = next((r for r in s[m.group(1)] if r["id"] == int(m.group(2))), None)
                if r is None:
                    return self.send(404, {"detail": "Not found."})
                ids = set(r.get("label_ids", [])) | set(x for x in r.get("labels", []) if isinstance(x, int))
                rows = [x for x in s.get("labels", []) if x["id"] in ids]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/survey_spec/$", p)
            if m:                                            # 1.107.0: {} when it has none, as AAP
                return self.send(200, (s.get("surveys") or {}).get("%s/%s" % (m.group(1), m.group(2))) or {})
            m = re.match(r"^job_templates/(\d+)/credentials/$", p)
            if m:
                jt = next(r for r in s["job_templates"] if r["id"] == int(m.group(1)))
                rows = [c for c in s["credentials"] if c["id"] in jt.get("credentials", [])]
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            if p == "workflow_job_template_nodes/":
                rows = by_fields([node_view(s, n) for n in s["nodes"]
                                  if "workflow_job_template" not in q or str(n["wf"]) == q.get("workflow_job_template")], q)
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            if p == "schedules/":
                rows = by_fields([schedule_view(s, x) for x in s["schedules"]], q)
                return self.send(200, {"count": len(rows), "results": rows, "next": None})
            m = re.match(r"^projects/(\d+)/playbooks/$", p)
            if m:                                           # as AAP: a bare list; the repository's own unless the state says
                pbs = (s.get("project_playbooks") or {}).get(m.group(1))
                if pbs is None:
                    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
                    pbs = sorted("playbooks/" + f for f in os.listdir(os.path.join(root, "playbooks")) if f.endswith(".yml"))
                return self.send(200, pbs)
            m = re.match(r"^inventories/(\d+)/(groups|hosts)/$", p)
            if m:
                rows = [r for r in s.get(m.group(2), []) if r.get("inventory") == int(m.group(1))
                        and ("name" not in q or r.get("name") == q["name"])]
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
                    fields = TPL_FIELDS if m.group(1) == "job_templates" else WF_FIELDS
                    r = dict((k, b.get(k)) for k in fields)
                    if m.group(1) == "job_templates" and not r["organization"]:
                        r["organization"] = next((p.get("organization") for p in s["projects"] if p["id"] == r["project"]), None)
                    if any(x["name"] == r["name"] and x.get("organization") == r["organization"] for x in s[m.group(1)]):
                        return self.send(400, {"name": ["already exists"]})
                    r["id"] = new_id(s)
                    if m.group(1) == "job_templates":
                        r["credentials"] = []
                        if not r.get("inventory") and not r.get("ask_inventory_on_launch"):     # as AAP
                            return self.send(400, {"inventory": ["Job Template must provide 'inventory' or allow prompting for it."]})
                    s[m.group(1)].append(r)
                    return self.send(201, r)
                if p == "labels/":                              # AAP - set up job templates: a label of the organization
                    lb = {"id": new_id(s), "name": b.get("name"), "organization": b.get("organization")}
                    s.setdefault("labels", []).append(lb)
                    return self.send(201, lb)
                m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/labels/$", p)
                if m:                                                                   # a label added by id or by name (made if new), or taken off (never deleted)
                    r = next((r for r in s[m.group(1)] if r["id"] == int(m.group(2))), None)
                    if r is None:
                        return self.send(404, {"detail": "Not found."})
                    labels = s.setdefault("labels", [])
                    ids = r.setdefault("label_ids", [])
                    plain = r.setdefault("labels", [])
                    if b.get("disassociate"):
                        r["label_ids"] = [i for i in ids if i != b.get("id")]
                        r["labels"] = [i for i in plain if i != b.get("id")]
                    else:
                        if b.get("name") is not None:
                            lab = next((x for x in labels if x["name"] == b.get("name") and x.get("organization") == b.get("organization")), None)
                            if lab is None:
                                lab = {"id": new_id(s), "name": b.get("name"), "organization": b.get("organization")}
                                labels.append(lab)
                            lid = lab["id"]
                        else:
                            lid = b["id"]
                        if lid not in ids:
                            ids.append(lid)
                        if lid not in plain:
                            plain.append(lid)
                    return self.send(204)
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
                         "extra_data": b.get("extra_data") or {}, "job_type": b.get("job_type"), "diff_mode": b.get("diff_mode")}
                    jt = next((j for j in s["job_templates"] if j["id"] == n["unified_job_template"]), None)
                    bad = node_refused(s, jt, b)                 # as AAP: only a prompted field
                    if bad:
                        return self.send(400, bad)
                    s["nodes"].append(n)
                    return self.send(201, {"id": n["id"]})
                m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/survey_spec/$", p)
                if m:
                    # 1.107.0 (review): what AAP's survey validation asks of every question - its name, variable, type,
                    # required, default and description keys; a choice question's choices (a list, or text one per line)
                    if "name" not in b or "description" not in b or not isinstance(b.get("spec"), list):
                        return self.send(400, {"error": "'name', 'description' and 'spec' are required"})
                    for q in b["spec"]:
                        miss = [k for k in ("question_name", "question_description", "variable", "type", "required", "default") if k not in q]
                        if miss:
                            return self.send(400, {"error": "'%s' missing from survey question %s." % (miss[0], q.get("variable"))})
                        if q["type"] in ("multiplechoice", "multiselect") and not (
                                (isinstance(q.get("choices"), list) and q["choices"]) or (isinstance(q.get("choices"), str) and q["choices"].strip())):
                            return self.send(400, {"error": "Survey question %s needs choices." % q["variable"]})
                    s.setdefault("surveys", {})["%s/%s" % (m.group(1), m.group(2))] = b
                    return self.send(200, b)
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
                    if n["identifier"] in (s.get("fail_links_from") or []) and not b.get("disassociate"):
                        return self.send(500, {"detail": "a link refused (the test's fail_links_from)"})
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
                # 1.79.19: a rename onto a name the organization has (AAP: unique per organization and kind)
                if m.group(1) in ("job_templates", "workflow_job_templates") and "name" in b and any(
                        x["name"] == b["name"] and x.get("organization") == r.get("organization") and x["id"] != r["id"] for x in s[m.group(1)]):
                    return self.send(400, {"__all__": ["%s with this Name and Organization already exists." % (
                        "Job template" if m.group(1) == "job_templates" else "Workflow job template")]})
                if m.group(1) == "schedules" and "rrule" in b and rrule_problem(b["rrule"]):
                    return self.send(400, {"rrule": [rrule_problem(b["rrule"])]})
                r.update(b)
                save(s)
                return self.send(200, r)
            m = re.match(r"^workflow_job_template_nodes/(\d+)/$", p)
            if m:
                n = next((x for x in s["nodes"] if x["id"] == int(m.group(1))), None)
                if n is None:
                    return self.send(404, {"detail": "Not found."})
                jt = next((j for j in s["job_templates"] if j["id"] == b.get("unified_job_template", n.get("unified_job_template"))), None)
                bad = node_refused(s, jt, b)             # as AAP: Variables, Job type only for a template that asks for them
                if bad:
                    return self.send(400, bad)
                n.update(dict((k, v) for k, v in b.items() if k in ("inventory", "limit", "unified_job_template", "extra_data", "job_type", "diff_mode")))
                save(s)
                return self.send(200, node_view(s, n))
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
