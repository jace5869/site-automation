#!/usr/bin/env python3
"""Fake ServiceNow Table API for the site-automation ServiceNow playbooks.

Accounts (basic auth):  api / goodpw  = itil (read, create, update)
                        ro  / goodpw  = read only (create/update -> 403 ACL)
Tables: incident, sc_task, change_request, change_task (each numbered by its prefix; task = all of them,
with sys_class_name), sys_user_group (Linux Operations), sys_user (svc_aap), ecc_agent.
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
Usage: snmock.py PORT   (requests appended to $SNMOCK_LOG as JSON lines)
"""
import base64
import json
import os
import sys
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

USERS = {"api": ("goodpw", True), "ro": ("goodpw", False)}
GROUPS = [{"sys_id": "g1", "name": "Linux Operations", "active": "true"}]
SYSUSERS = [{"sys_id": "u1", "user_name": "svc_aap", "active": "true"}]
INCIDENTS = {}
ATTACHMENTS = []
COUNTER = [10000]
LOG = os.environ.get("SNMOCK_LOG", "snmock.jsonl")
STATE_LABEL = {"1": "New", "2": "In Progress", "6": "Resolved", "7": "Closed"}
TASK_LABEL = {"1": "Open", "2": "Work in Progress", "3": "Closed Complete", "4": "Closed Incomplete", "7": "Closed Skipped",
              "-5": "New", "-4": "Assess", "-3": "Authorize", "-2": "Scheduled", "-1": "Implement", "0": "Review"}
PREFIX = {"incident": "INC", "sc_task": "SCTASK", "change_request": "CHG", "change_task": "CTASK"}
APPROVAL_LABEL = {"approved": "Approved", "requested": "Requested", "not requested": "Not Yet Requested", "rejected": "Rejected"}


def closed(rec):
    """An incident is closed when Resolved or Closed; the other tables when Closed (3, 4, 7)."""
    return rec.get("state") in (("6", "7") if rec.get("_table", "incident") == "incident" else ("3", "4", "7"))


def state_label(rec):
    return (STATE_LABEL if rec.get("_table", "incident") == "incident" else TASK_LABEL).get(rec.get("state"), rec.get("state"))


def matches(rec, query):
    """The encoded-query subset the playbooks use, joined by ^: a=b, aSTARTSWITHb (any case), aINb,c,
    active=true, a.name=b (a reference by its name: the mock keeps names), ref.field=b (parent /
    change_request: that field of the ticket the reference holds), ORDERBY... (ignored: the records are
    kept oldest first)."""
    for cond in [c for c in query.split("^") if c]:
        if cond.startswith("ORDERBY"):
            continue
        if "STARTSWITH" in cond:
            field, _, value = cond.partition("STARTSWITH")
            if not str(rec.get(field, "")).lower().startswith(value.lower()):
                return False
            continue
        if "IN" in cond and "=" not in cond:
            field, _, value = cond.partition("IN")
            if str(rec.get(field, "")) not in value.split(","):
                return False
            continue
        field, _, value = cond.partition("=")
        field = field[:-5] if field.endswith(".name") else field
        if "." in field:
            ref, _, sub = field.partition(".")
            target = next((x for x in INCIDENTS.values() if rec.get(ref) and x.get("number") == rec.get(ref)), {})
            if str(target.get(sub, "")) != value:
                return False
        elif field == "active":
            if (not closed(rec)) != (value == "true"):
                return False
        elif str(rec.get(field, "")) != value:
            return False
    return True


CLOCK = [0]
JOURNAL_LABEL = {"comments": "Additional comments", "work_notes": "Work notes"}


def journal_add(rec, body, user):
    for field in JOURNAL_LABEL:
        if body.get(field):
            CLOCK[0] += 1
            rec.setdefault("_journal", []).append((field, "2026-10-08 09:%02d:%02d - %s" % (CLOCK[0] // 60, CLOCK[0] % 60, user),
                                                   str(body[field])))


REFS = ("assignment_group", "location", "caller_id", "parent", "change_request")


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
        if parts[:3] != ["api", "now", "table"] or len(parts) < 4:
            return 404, {"error": {"message": "Requested URI does not represent any resource"}}
        table = parts[3]
        sys_id = parts[4] if len(parts) > 4 else None
        name, itil = user
        if table == "sys_user_group":
            want = q.get("sysparm_query", "").partition("=")[2]
            return 200, {"result": [g for g in GROUPS if g["name"] == want]}
        if table == "sys_user":
            want = q.get("sysparm_query", "").partition("=")[2]
            return 200, {"result": [u for u in SYSUSERS if u["user_name"] == want]}
        if table == "ecc_agent":
            return 200, {"result": []}
        if table == "task" and method == "GET" and not sys_id:
            # every ticket is a task: the parent table, with sys_class_name naming the real one
            if os.environ.get("SNMOCK_NO_TASK") == "1":
                return fail("Operation Failed", "ACL Exception: no read access to task", 403)
            rows = [i for i in INCIDENTS.values() if matches(i, q.get("sysparm_query", ""))]
            rows = rows[:int(q.get("sysparm_limit", "10000"))]
            return 200, {"result": [pick(dict(i, sys_class_name=i.get("_table", "incident")), q.get("sysparm_fields")) for i in rows]}
        if table not in PREFIX:
            return 400, {"error": {"message": "Invalid table " + table}}
        if method == "GET" and not sys_id:
            rows = [i for i in INCIDENTS.values() if i.get("_table", "incident") == table and matches(i, q.get("sysparm_query", ""))]
            rows = rows[:int(q.get("sysparm_limit", "10000"))]
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
                        "sys_created_on": "2026-09-29 12:00:00", "work_notes_list": []})
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

    def attachment(self, method, parts, q, body, user):
        if method == "GET" and len(parts) == 3:
            rows = [a for a in ATTACHMENTS if matches(a, q.get("sysparm_query", ""))]
            rows = rows[:int(q.get("sysparm_limit", "10000"))]
            return 200, {"result": [pick(a, q.get("sysparm_fields")) for a in rows]}
        if method == "POST" and parts[3:] == ["file"]:
            if not user[1]:
                return fail("Operation Failed", "ACL Exception Insert Failed due to security constraints", 403)
            if os.environ.get("SNMOCK_NO_ATTACH") == "1":
                return fail("Operation Failed", "File extension not allowed: %s" % q.get("file_name", "").rpartition(".")[2], 403)
            rec = INCIDENTS.get(q.get("table_sys_id"))
            if rec is None or rec.get("_table", "incident") != q.get("table_name"):
                return 400, {"error": {"message": "Invalid table or record"}}
            a = {"sys_id": uuid.uuid4().hex, "table_name": q["table_name"], "table_sys_id": q["table_sys_id"],
                 "file_name": q.get("file_name", ""), "content_type": body.get("content_type", ""),
                 "size_bytes": str(len(body.get("raw", "")))}
            ATTACHMENTS.append(a)
            return 201, {"result": a}
        return 405, {"error": {"message": "Method not allowed"}}


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
