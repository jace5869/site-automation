#!/usr/bin/env python3
"""Fake ServiceNow Table API for the site-automation ServiceNow playbooks.

Accounts (basic auth):  api / goodpw  = itil (read, create, update)
                        ro  / goodpw  = read only (create/update -> 403 ACL)
Tables: incident, sc_task, change_request, change_task (each numbered by its prefix; task = all of them,
with sys_class_name), sys_user_group (Linux Operations, ABC - Ops - IT - Linux), sys_user (svc_aap), ecc_agent.
Journal fields (comments, work_notes): what is POSTed or PATCHed is kept as entries; with
sysparm_display_value=true they come back as ServiceNow shows them - newest first, each under a
header "<date time> - <user> (Additional comments)" / "(Work notes)"; without it, empty.
Reference fields (assignment_group, location, caller_id) with sysparm_display_value=true come as
{"display_value", "link"}, as ServiceNow sends them, unless sysparm_exclude_reference_link=true.
Reference fields parent and change_request hold a ticket number; a query may dot-walk them
(change_request.approval=approved: the field of the ticket they hold). approval (a change's) comes back
as its label with sysparm_display_value=true (approved -> Approved).
SNMOCK_NO_RESOLVE=1: resolving answers 403 "Data Policy Exception" (close code missing).
A ticket whose short description has "[mock:no-close]": a PATCH of its state answers 403 (a data
policy); "[mock:keep-state]": answers 200 but keeps the state (a business rule). A PATCH with
sysparm_fields answers with those fields of the record.
SNMOCK_HIDE_JOURNAL=1: journal fields always come back empty (an account that cannot read them).
Attachments: POST /api/now/attachment/file?table_name&table_sys_id&file_name (the raw file as the
body, its Content-Type kept), GET /api/now/attachment?sysparm_query=table_sys_id=..^file_name=..
SNMOCK_NO_ATTACH=1: an attachment is refused (403, as an instance that does not allow the extension).
SNMOCK_ATTACH_TXT_ONLY=1: an attachment whose name does not end in .txt is refused (400 "Invalid file type:
text/plain", as an instance that checks the type against the name).
GET /api/now/attachment/<sys_id>: one attachment's record; GET /api/now/attachment/<sys_id>/file: the file itself, as it was
sent (binary too). An attachment may go to a ZZ_YY table (an attachment question's file) with any table_sys_id: nothing has
to exist there.
Service Catalog API: one item, "DoD PKI Certificate Request" (CATALOG_ID), with the questions of the PKI's form
(an attachment question for the CSR, references to sys_user / cmn_location, a select box).
GET /api/sn_sc/servicecatalog/items?sysparm_text=X (items whose name has X), GET .../items/<id> (its questions),
POST .../items/<id>/order_now {variables}: a mandatory question left empty -> 400; else a request (sc_request,
REQ...) and its requested item (sc_req_item, RITM..., request = the REQ's sys_id, its answers kept), and the file
an attachment question names moved to the RITM (ZZ_YYsc_req_item) - SNMOCK_SC_KEEP_ATTACHMENT=1: left where it
was uploaded (an instance that does not move it). SNMOCK_SC_REFUSE=1: every order refused (400).
SNMOCK_HIDE_ZZYY=1: an attachment search (GET /api/now/attachment?sysparm_query=...) leaves out the ZZ_YY ones.
cmn_location: Building 100 (loc1); sys_user also has jdoe (u2, "Jane Doe").
MID Servers (the ServiceNow health check): SNMOCK_MID=<file.json> {"agents": [ecc_agent rows], "queue": {name: stuck count},
"issues": [ecc_agent_issue rows]}; without it ecc_agent is empty. GET /api/now/stats/ecc_queue (grouped by agent) answers
the queue counts; SNMOCK_MID_NO_QUEUE=1: 403 (an account that cannot read ecc_queue); SNMOCK_MID_NO_ISSUES=1: ecc_agent_issue
is an unknown table (400, a release without it).
The weekly activity report: SNMOCK_SEED=<file.json> (records to start with, each with its _table and dates), problem_task and
problem tables, sysparm_display_value=all ({"value", "display_value"} per field), sysparm_offset, the query operators <=, <, >,
ISEMPTY, ISNOTEMPTY, !=, aINb,c on a reference (by name or sys_id) and ^OR (joins the condition before it); sys_user_group
nameINa,b and sys_user user_nameINa,b^ORnameINa,b; SNMOCK_NO_GROUPS=1: sys_user_group answers 403; SNMOCK_NO_TABLES=a,b:
reading those tables answers 403. task_sla: the seed's records with _table task_sla (task = the task's sys_id, sla = its name,
stage, has_breached, business_percentage, planned_end_time ...), read with taskINa,b and stage!=cancelled; not part of task.
Usage: snmock.py PORT   (requests appended to $SNMOCK_LOG as JSON lines)
"""
import base64
import datetime
import json
import re
import os
import sys
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERS = {"api": ("goodpw", True), "ro": ("goodpw", False)}
def mid_data():
    path = os.environ.get("SNMOCK_MID", "")
    if not path:
        return {}
    with open(path) as fh:
        return json.load(fh)


GROUPS = [{"sys_id": "g1", "name": "Linux Operations", "active": "true"}, {"sys_id": "g2", "name": "Windows Operations", "active": "true"},
          {"sys_id": "g3", "name": "ABC - Ops - IT - Linux", "active": "true"}]     # a name with " - " in it, as real ones have
SYSUSERS = [{"sys_id": "u1", "user_name": "svc_aap", "name": "AAP Service", "active": "true"},
            {"sys_id": "u2", "user_name": "jdoe", "name": "Jane Doe", "active": "true"},
            {"sys_id": "u3", "user_name": "bsmith", "name": "Bob Smith", "active": "true"}]
LOCATIONS = [{"sys_id": "loc1" + "0" * 28, "name": "Building 100"}]
CATALOG_ID = "c0ffee" + "0" * 26
CATALOG = {CATALOG_ID: {"sys_id": CATALOG_ID, "name": "DoD PKI Certificate Request",
                        "short_description": "DoD PKI Certificate request for Servers and Network devices",
                        "variables": [
                            {"name": "title_rank", "label": "Title/Rank:", "type": 6, "mandatory": False},
                            {"name": "requested_for", "label": "Requested For", "type": 31, "mandatory": True},
                            {"name": "email", "label": "Email:", "type": 6, "mandatory": False},
                            {"name": "dsn", "label": "DSN:", "type": 6, "mandatory": False},
                            {"name": "contact_method", "label": "Preferred method of contact", "type": 5, "mandatory": True,
                             "choices": [{"value": "", "label": "-- None --"}, {"value": "email", "label": "Email"},
                                         {"value": "phone", "label": "Phone"}]},
                            {"name": "location", "label": "Location:", "type": 8, "mandatory": False, "reference": "cmn_location"},
                            {"name": "note", "label": "Please provide the CSR ...", "type": 11, "mandatory": False},
                            {"name": "csr_file", "label": "Please add CSR", "type": 33, "mandatory": True},
                            {"name": "additional_sans", "label": "List any additional Subject Alternative Names (SAN)", "type": 2,
                             "mandatory": False}]}}
INCIDENTS = {}
ATTACHMENTS = []
COUNTER = [10000]
LOG = os.environ.get("SNMOCK_LOG", "snmock.jsonl")
STATE_LABEL = {"1": "New", "2": "In Progress", "6": "Resolved", "7": "Closed"}
TASK_LABEL = {"1": "Open", "2": "Work in Progress", "3": "Closed Complete", "4": "Closed Incomplete", "7": "Closed Skipped",
              "-5": "New", "-4": "Assess", "-3": "Authorize", "-2": "Scheduled", "-1": "Implement", "0": "Review"}
PREFIX = {"incident": "INC", "sc_task": "SCTASK", "change_request": "CHG", "change_task": "CTASK", "sc_request": "REQ",
          "sc_req_item": "RITM", "problem_task": "PTASK", "problem": "PRB"}


def seed():
    """SNMOCK_SEED=<file.json>: records to start with (the weekly activity report's week), each with its _table, number,
    sys_id, state and dates (sys_updated_on ...), as a JSON list - kept in the order given."""
    path = os.environ.get("SNMOCK_SEED", "")
    if not path:
        return
    with open(path) as fh:
        for rec in json.load(fh):
            rec = dict(rec)
            rec.setdefault("_table", "incident")
            rec.setdefault("sys_id", uuid.uuid4().hex)
            rec.setdefault("state", "1")
            rec.setdefault("work_notes_list", [])
            INCIDENTS[rec["sys_id"]] = rec
APPROVAL_LABEL = {"approved": "Approved", "requested": "Requested", "not requested": "Not Yet Requested", "rejected": "Rejected"}


def closed(rec):
    """An incident is closed when Resolved or Closed; the other tables when Closed (3, 4, 7)."""
    return rec.get("state") in (("6", "7") if rec.get("_table", "incident") == "incident" else ("3", "4", "7"))


def state_label(rec):
    return (STATE_LABEL if rec.get("_table", "incident") == "incident" else TASK_LABEL).get(rec.get("state"), rec.get("state"))


def _same(rec_value, value):
    """A field's value is the value asked for - as it is, or the value is the sys_id of what the record names (the mock
    keeps a group's name, a user's name and a ticket's number in its reference fields)."""
    rv = str(rec_value or "")
    if rv == value:
        return True
    if any(g["sys_id"] == value and g["name"] == rv for g in GROUPS):
        return True
    if any(u["sys_id"] == value and rv in (u["user_name"], u["name"]) for u in SYSUSERS):
        return True
    ref = next((x for x in INCIDENTS.values() if rv and x.get("number") == rv), {})
    return bool(value) and ref.get("sys_id") == value


def _cond(rec, cond):
    """One condition of an encoded query (see matches)."""
    if cond.startswith("ORDERBY"):
        return True
    if cond.endswith("ISEMPTY"):
        return not str(rec.get(cond[:-7], "") or "")
    if cond.endswith("ISNOTEMPTY"):
        return bool(str(rec.get(cond[:-10], "") or ""))
    for op in (">=", "<=", ">", "<"):
        if op in cond and not re.search(r"(LIKE|STARTSWITH|IN|!=)", cond):
            field, _, value = cond.partition(op)
            m = re.match(r"javascript:gs\.daysAgoStart\((\d+)\)$", value)
            if m:
                value = (datetime.datetime.utcnow() - datetime.timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d 00:00:00")
            have = str(rec.get(field, "") or "")
            if not have:
                return False
            return {">=": have >= value, "<=": have <= value, ">": have > value, "<": have < value}[op]
    if "LIKE" in cond and "=" not in cond.partition("LIKE")[0]:
        field, _, value = cond.partition("LIKE")
        return value.lower() in str(rec.get(field, "")).lower()
    if "STARTSWITH" in cond:
        field, _, value = cond.partition("STARTSWITH")
        return str(rec.get(field, "")).lower().startswith(value.lower())
    if "IN" in cond and "=" not in cond:
        field, _, value = cond.partition("IN")
        field = field[:-5] if field.endswith(".name") else field
        return any(_same(rec.get(field, ""), v) for v in value.split(","))
    if "!=" in cond:
        field, _, value = cond.partition("!=")
        return not _same(rec.get(field, ""), value)
    field, _, value = cond.partition("=")
    field = field[:-5] if field.endswith(".name") else field
    if "." in field:
        ref, _, sub = field.partition(".")
        target = next((x for x in INCIDENTS.values() if rec.get(ref) and x.get("number") == rec.get(ref)), {})
        return str(target.get(sub, "")) == value
    if field == "active":
        return (not closed(rec)) == (value == "true")
    return _same(rec.get(field, ""), value)


def matches(rec, query):
    """The encoded-query subset the playbooks use, joined by ^: a=b, a!=b, aSTARTSWITHb and aLIKEb (any case), aINb,c
    (a reference also by the sys_id of what it names), active=true, a.name=b / a.nameINb,c (a reference by its name: the
    mock keeps names), ref.field=b (parent / change_request: that field of the ticket the reference holds),
    a>=javascript:gs.daysAgoStart(N), a>=, a<=, a>, a<YYYY-MM-DD HH:MM:SS (a date; an empty date never compares),
    aISEMPTY, aISNOTEMPTY, ORDERBY... (the records are kept oldest first; ORDERBYDESC turns the result round, in the
    handler). ^OR joins a condition to the one before it, as ServiceNow does: a^ORb^c = (a or b) and c."""
    groups, cur = [], []
    for cond in [c for c in query.split("^") if c]:
        if cond.startswith("OR") and not cond.startswith("ORDERBY"):
            cur.append(cond[2:])
        else:
            if cur:
                groups.append(cur)
            cur = [cond]
    if cur:
        groups.append(cur)
    return all(any(_cond(rec, c) for c in g) for g in groups)


CLOCK = [0]
JOURNAL_LABEL = {"comments": "Additional comments", "work_notes": "Work notes"}


def journal_add(rec, body, user):
    for field in JOURNAL_LABEL:
        if body.get(field):
            CLOCK[0] += 1
            rec.setdefault("_journal", []).append((field, "2026-10-08 09:%02d:%02d - %s" % (CLOCK[0] // 60, CLOCK[0] % 60, user),
                                                   str(body[field])))


REFS = ("assignment_group", "location", "caller_id", "parent", "change_request")


def show_all(rec):
    """A record with sysparm_display_value=all: every field as {"value", "display_value"} - a state's label, a
    reference's name (its sys_id as the value when the mock knows it), the class's label."""
    plain = show(rec, False, False)
    shown = show(rec, True, False)
    out = {}
    for k, v in plain.items():
        value = v
        if k == "assignment_group":
            value = next((g["sys_id"] for g in GROUPS if g["name"] == v), v)
        elif k in ("assigned_to", "opened_by", "caller_id", "resolved_by"):
            value = next((u["sys_id"] for u in SYSUSERS if v in (u["user_name"], u["name"])), v)
        display = shown.get(k, v)
        if k in ("assigned_to", "opened_by", "caller_id", "resolved_by"):
            display = next((u["name"] for u in SYSUSERS if v in (u["user_name"], u["name"])), v)
        if k == "sys_class_name":
            display = {"incident": "Incident", "sc_task": "Catalog Task", "change_request": "Change Request", "change_task": "Change Task",
                       "problem_task": "Problem Task", "problem": "Problem", "sc_req_item": "Requested Item", "sc_request": "Request"}.get(v, v)
        out[k] = {"value": value, "display_value": display}
    return out


def show(rec, disp, links=True):
    """A record as the API returns it: journal fields as text (display) or empty; active from the state;
    reference fields as {display_value, link} (display, with links) or their name."""
    out = {k: v for k, v in rec.items() if not k.startswith("_") and k not in JOURNAL_LABEL}
    out["active"] = "false" if closed(rec) else "true"
    if disp and links:
        for f in REFS:
            if out.get(f):
                out[f] = {"display_value": out[f], "link": "https://snmock/api/now/table/%s/%s" % (f, abs(hash(out[f])) % 10000)}
    for field, label in JOURNAL_LABEL.items():
        if disp and os.environ.get("SNMOCK_HIDE_JOURNAL") != "1":
            ents = [e for e in reversed(rec.get("_journal", [])) if e[0] == field]
            out[field] = "".join("%s (%s)\n%s\n\n" % (head, label, text) for _, head, text in ents)
        else:
            out[field] = ""
    if disp:
        out["state"] = state_label(rec)
        if out.get("approval"):
            out["approval"] = APPROVAL_LABEL.get(out["approval"], out["approval"])
    return out


def pick(rec, fields):
    return {k: rec.get(k, "") for k in fields.split(",")} if fields else dict(rec)


def fail(msg, detail, code):
    return code, {"error": {"message": msg, "detail": detail}, "status": "failure"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _user(self):
        a = self.headers.get("Authorization", "")
        if not a.startswith("Basic "):
            return None
        u, _, p = base64.b64decode(a[6:]).decode().partition(":")
        rec = USERS.get(u)
        return (u, rec[1]) if rec and rec[0] == p else None

    def handle_any(self, method):
        n = int(self.headers.get("Content-Length", "0") or 0)
        url = urllib.parse.urlparse(self.path)
        raw = self.rfile.read(n) if n else b""
        self.raw = raw
        if url.path.startswith("/api/now/attachment"):
            body = {"raw": raw.decode("utf-8", "replace"), "content_type": self.headers.get("Content-Type", "")}
        else:
            body = json.loads(raw or b"{}") if n else {}
        q = dict(urllib.parse.parse_qsl(url.query))
        parts = url.path.strip("/").split("/")          # api now table <table> [sys_id]
        user = self._user()
        code, obj = self.route(method, parts, q, body, user)
        with open(LOG, "a") as f:
            f.write(json.dumps({"method": method, "path": url.path, "query": q, "body": body,
                                "user": user[0] if user else None, "status": code}) + "\n")
        if isinstance(obj, bytes):          # a file (GET /api/now/attachment/<sys_id>/file)
            self.send_response(code)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(obj)))
            self.end_headers()
            self.wfile.write(obj)
            return
        self._send(code, obj)

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PATCH(self):
        self.handle_any("PATCH")

    def route(self, method, parts, q, body, user):
        if user is None:
            return fail("User Not Authenticated", "Required to provide Auth information", 401)
        if parts[:3] == ["api", "now", "attachment"]:
            return self.attachment(method, parts, q, body, user)
        if parts[:4] == ["api", "sn_sc", "servicecatalog", "items"]:
            return self.catalog(method, parts[4:], q, body, user)
        if parts[:4] == ["api", "now", "stats", "ecc_queue"]:
            if os.environ.get("SNMOCK_MID_NO_QUEUE") == "1":
                return fail("Operation Failed", "ACL Exception: no read access to ecc_queue", 403)
            return 200, {"result": [{"groupby_fields": [{"field": "agent", "value": "mid.server." + k}], "stats": {"count": str(v)}}
                                    for k, v in sorted(mid_data().get("queue", {}).items())]}
        if parts[:3] != ["api", "now", "table"] or len(parts) < 4:
            return 404, {"error": {"message": "Requested URI does not represent any resource"}}
        table = parts[3]
        sys_id = parts[4] if len(parts) > 4 else None
        name, itil = user
        if table == "sys_user_group":
            if os.environ.get("SNMOCK_NO_GROUPS") == "1":
                return fail("Operation Failed", "ACL Exception: no read access to sys_user_group", 403)
            query = q.get("sysparm_query", "")
            if query.startswith("nameIN"):               # nameINa,b (the weekly activity report)
                want = query[6:].split(",")
                return 200, {"result": [g for g in GROUPS if g["name"] in want]}
            want = query.partition("=")[2]
            return 200, {"result": [g for g in GROUPS if g["name"] == want]}
        if table == "sys_user":
            query = q.get("sysparm_query", "")
            if query.startswith("user_nameIN"):          # user_nameINa,b^ORnameINa,b
                want = query.partition("^OR")[0][11:].split(",")
                return 200, {"result": [u for u in SYSUSERS if u["user_name"] in want or u["name"] in want]}
            if "^OR" in query:                          # user_name=x^ORname=x
                want = [c.partition("=")[2] for c in query.split("^OR")]
                return 200, {"result": [u for u in SYSUSERS if u["user_name"] == want[0] or u["name"] == want[1]]}
            want = query.partition("=")[2]
            return 200, {"result": [u for u in SYSUSERS if u["user_name"] == want]}
        if table == "cmn_location":
            want = q.get("sysparm_query", "").partition("=")[2]
            return 200, {"result": [x for x in LOCATIONS if x["name"] == want]}
        if table == "ecc_agent":
            return 200, {"result": mid_data().get("agents", [])}
        if table == "ecc_agent_issue":
            if os.environ.get("SNMOCK_MID_NO_ISSUES") == "1":
                return 400, {"error": {"message": "Invalid table ecc_agent_issue"}}
            return 200, {"result": mid_data().get("issues", [])}
        if table in os.environ.get("SNMOCK_NO_TABLES", "").split(",") and method == "GET":
            return fail("Operation Failed", "ACL Exception: no read access to " + table, 403)
        if table == "task_sla" and method == "GET" and not sys_id:
            rows = [i for i in INCIDENTS.values() if i.get("_table") == "task_sla" and matches(i, q.get("sysparm_query", ""))]
            num = dict((i["sys_id"], i.get("number", "")) for i in INCIDENTS.values())
            shown = [dict((k, {"value": v, "display_value": num.get(v, v) if k == "task" else v}) for k, v in i.items() if not k.startswith("_"))
                     for i in rows]
            return 200, {"result": [pick(x, q.get("sysparm_fields")) for x in shown]}
        if table == "task" and method == "GET" and not sys_id:
            # every ticket is a task: the parent table, with sys_class_name naming the real one
            if os.environ.get("SNMOCK_NO_TASK") == "1":
                return fail("Operation Failed", "ACL Exception: no read access to task", 403)
            rows = [dict(i, sys_class_name=i.get("_table", "incident")) for i in INCIDENTS.values()
                    if i.get("_table") != "task_sla" and matches(i, q.get("sysparm_query", ""))]
            offset = int(q.get("sysparm_offset", "0"))
            rows = rows[offset:offset + int(q.get("sysparm_limit", "10000"))]
            if q.get("sysparm_display_value") == "all":
                return 200, {"result": [pick(show_all(i), q.get("sysparm_fields")) for i in rows]}
            return 200, {"result": [pick(i, q.get("sysparm_fields")) for i in rows]}
        if table not in PREFIX:
            return 400, {"error": {"message": "Invalid table " + table}}
        if method == "GET" and not sys_id:
            rows = [i for i in INCIDENTS.values() if i.get("_table", "incident") == table and matches(i, q.get("sysparm_query", ""))]
            if "ORDERBYDESC" in q.get("sysparm_query", ""):
                rows.reverse()                                  # newest first
            offset = int(q.get("sysparm_offset", "0"))
            rows = rows[offset:offset + int(q.get("sysparm_limit", "10000"))]
            if q.get("sysparm_display_value") == "all":
                return 200, {"result": [pick(show_all(i), q.get("sysparm_fields")) for i in rows]}
            disp = q.get("sysparm_display_value") == "true"
            links = q.get("sysparm_exclude_reference_link") != "true"
            return 200, {"result": [pick(show(i, disp, links), q.get("sysparm_fields")) for i in rows]}
        if method == "POST":
            if not itil:
                return fail("Operation Failed", "ACL Exception Insert Failed due to security constraints", 403)
            sid = uuid.uuid4().hex
            COUNTER[0] += 1
            rec = dict(body)
            group = rec.get("assignment_group", "")
            if q.get("sysparm_input_display_value") == "true" and group and group not in [g["name"] for g in GROUPS]:
                rec["assignment_group"] = ""                    # an unknown display value is dropped
            for field in JOURNAL_LABEL:
                rec.pop(field, None)
            rec.update({"sys_id": sid, "number": body.get("number") or "%s00%d" % (PREFIX[table], COUNTER[0]), "state": rec.get("state") or "1",
                        "_table": table,
                        "sys_created_on": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "work_notes_list": []})
            journal_add(rec, body, name)
            INCIDENTS[sid] = rec
            return 201, {"result": {"sys_id": sid, "number": rec["number"]}}
        rec = INCIDENTS.get(sys_id)
        if rec is not None and rec.get("_table", "incident") != table:
            rec = None
        if rec is None:
            return 404, {"error": {"message": "No Record found"}}
        if method == "GET":
            disp = q.get("sysparm_display_value") == "true"
            out = pick(rec, q.get("sysparm_fields"))
            if disp:
                out["state"] = state_label(rec)
                out["priority"] = "5 - Planning" if rec.get("urgency") == "3" else "3 - Moderate"
                for field in JOURNAL_LABEL:          # journal fields asked for: as ServiceNow shows them
                    if field in out:
                        out[field] = show(rec, True)[field]
            return 200, {"result": out}
        if method == "PATCH":
            if not itil:
                return fail("Operation Failed", "ACL Exception Update Failed due to security constraints", 403)
            if "state" in body and os.environ.get("SNMOCK_NO_RESOLVE") == "1":
                return fail("Operation Failed", "Data Policy Exception: Resolution code is mandatory", 403)
            if "state" in body and "[mock:no-close]" in str(rec.get("short_description")):
                return fail("Operation Failed", "Data Policy Exception: Close notes is mandatory", 403)
            if "work_notes" in body:
                rec["work_notes_list"].append(body["work_notes"])
            journal_add(rec, body, name)
            for k, v in body.items():
                if k not in JOURNAL_LABEL and not (k == "state" and "[mock:keep-state]" in str(rec.get("short_description"))):
                    rec[k] = v
            if q.get("sysparm_fields"):
                return 200, {"result": pick({k: v for k, v in rec.items() if not k.startswith("_")}, q["sysparm_fields"])}
            return 200, {"result": {"number": rec["number"], "state": rec["state"]}}
        return 405, {"error": {"message": "Method not allowed"}}

    def catalog(self, method, rest, q, body, user):
        if method == "GET" and not rest:
            text = q.get("sysparm_text", "").lower()
            return 200, {"result": [{k: i[k] for k in ("sys_id", "name", "short_description")}
                                    for i in CATALOG.values() if text in i["name"].lower()]}
        item = CATALOG.get(rest[0]) if rest else None
        if item is None:
            return 404, {"error": {"message": "Catalog item not found", "detail": ""}}
        if method == "GET" and len(rest) == 1:
            return 200, {"result": item}
        if method == "POST" and rest[1:] == ["order_now"]:
            if not user[1]:
                return fail("Operation Failed", "ACL Exception: not allowed to order", 403)
            if os.environ.get("SNMOCK_SC_REFUSE") == "1":
                return 400, {"error": {"message": "Order could not be placed", "detail": "the item is not available"}}
            answers = body.get("variables") or {}
            missing = [v["name"] for v in item["variables"] if v.get("mandatory") and not answers.get(v["name"])]
            if missing:
                return 400, {"error": {"message": "Mandatory Variables are required", "detail": ", ".join(missing)}}
            COUNTER[0] += 1
            req = {"sys_id": uuid.uuid4().hex, "number": "REQ00%d" % COUNTER[0], "state": "1", "_table": "sc_request"}
            INCIDENTS[req["sys_id"]] = req
            COUNTER[0] += 1
            ritm = {"sys_id": uuid.uuid4().hex, "number": "RITM00%d" % COUNTER[0], "state": "1", "_table": "sc_req_item",
                    "request": req["sys_id"], "cat_item": item["sys_id"], "_variables": dict(answers),
                    "short_description": item["name"], "work_notes_list": []}
            INCIDENTS[ritm["sys_id"]] = ritm
            for v in item["variables"]:
                if v["type"] == 33 and answers.get(v["name"]) and os.environ.get("SNMOCK_SC_KEEP_ATTACHMENT") != "1":
                    for a in ATTACHMENTS:
                        if a["sys_id"] == answers[v["name"]]:
                            a.update(table_name="ZZ_YYsc_req_item", table_sys_id=ritm["sys_id"])
            return 200, {"result": {"sys_id": req["sys_id"], "number": req["number"], "request_number": req["number"],
                                    "request_id": req["sys_id"], "table": "sc_request"}}
        return 405, {"error": {"message": "Method not allowed"}}

    def attachment(self, method, parts, q, body, user):
        if method == "GET" and len(parts) == 5 and parts[4] == "file":
            a = next((x for x in ATTACHMENTS if x["sys_id"] == parts[3]), None)
            return (200, base64.b64decode(a["_data"])) if a else (404, {"error": {"message": "Record doesn't exist or ACL restricts the record retrieval"}})
        if method == "GET" and len(parts) == 4:
            a = next((x for x in ATTACHMENTS if x["sys_id"] == parts[3]), None)
            return (200, {"result": {k: v for k, v in a.items() if not k.startswith("_")}}) if a else (404, {"error": {"message": "Record doesn't exist or ACL restricts the record retrieval"}})
        if method == "GET" and len(parts) == 3:
            rows = [a for a in ATTACHMENTS if matches(a, q.get("sysparm_query", ""))
                    and not (os.environ.get("SNMOCK_HIDE_ZZYY") == "1" and a["table_name"].startswith("ZZ_YY"))]
            rows = rows[:int(q.get("sysparm_limit", "10000"))]
            return 200, {"result": [pick({k: v for k, v in a.items() if not k.startswith("_")}, q.get("sysparm_fields")) for a in rows]}
        if method == "POST" and parts[3:] == ["file"]:
            if not user[1]:
                return fail("Operation Failed", "ACL Exception Insert Failed due to security constraints", 403)
            if os.environ.get("SNMOCK_NO_ATTACH") == "1":
                return fail("Operation Failed", "File extension not allowed: %s" % q.get("file_name", "").rpartition(".")[2], 403)
            if os.environ.get("SNMOCK_ATTACH_TXT_ONLY") == "1" and not q.get("file_name", "").endswith(".txt"):
                return 400, {"error": {"message": "Invalid file type: %s" % body.get("content_type", "")}}
            rec = INCIDENTS.get(q.get("table_sys_id"))
            if not q.get("table_name", "").startswith("ZZ_YY") and (rec is None or rec.get("_table", "incident") != q.get("table_name")):
                return 400, {"error": {"message": "Invalid table or record"}}
            a = {"sys_id": uuid.uuid4().hex, "table_name": q["table_name"], "table_sys_id": q["table_sys_id"],
                 "file_name": q.get("file_name", ""), "content_type": body.get("content_type", ""),
                 "size_bytes": str(len(self.raw)), "_data": base64.b64encode(self.raw).decode()}
            ATTACHMENTS.append(a)
            return 201, {"result": {k: v for k, v in a.items() if not k.startswith("_")}}
        return 405, {"error": {"message": "Method not allowed"}}


if __name__ == "__main__":
    seed()
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
