#!/usr/bin/env python3
"""Fake ServiceNow Table API for the site-automation ServiceNow playbooks.

Accounts (basic auth):  api / goodpw  = itil (read, create, update)
                        ro  / goodpw  = read only (create/update -> 403 ACL)
Tables: incident, sys_user_group (Linux Operations), sys_user (svc_aap), ecc_agent.
Journal fields (comments, work_notes): what is POSTed or PATCHed is kept as entries; with
sysparm_display_value=true they come back as ServiceNow shows them - newest first, each under a
header "<date time> - <user> (Additional comments)" / "(Work notes)"; without it, empty.
SNMOCK_NO_RESOLVE=1: resolving answers 403 "Data Policy Exception" (close code missing).
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
COUNTER = [10000]
LOG = os.environ.get("SNMOCK_LOG", "snmock.jsonl")
STATE_LABEL = {"1": "New", "2": "In Progress", "6": "Resolved", "7": "Closed"}


def matches(rec, query):
    """The encoded-query subset the playbooks use: a=b, aSTARTSWITHb, active=true, joined by ^."""
    for cond in [c for c in query.split("^") if c]:
        if "STARTSWITH" in cond:
            field, _, value = cond.partition("STARTSWITH")
            if not str(rec.get(field, "")).startswith(value):
                return False
        else:
            field, _, value = cond.partition("=")
            if field == "active":
                if (rec.get("state") not in ("6", "7")) != (value == "true"):
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


def show(rec, disp):
    """A record as the API returns it: journal fields as text (display) or empty; active from the state."""
    out = {k: v for k, v in rec.items() if not k.startswith("_") and k not in JOURNAL_LABEL}
    out["active"] = "false" if rec.get("state") in ("6", "7") else "true"
    for field, label in JOURNAL_LABEL.items():
        if disp:
            ents = [e for e in reversed(rec.get("_journal", [])) if e[0] == field]
            out[field] = "".join("%s (%s)\n%s\n\n" % (head, label, text) for _, head, text in ents)
        else:
            out[field] = ""
    if disp:
        out["state"] = STATE_LABEL.get(rec.get("state"), rec.get("state"))
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
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        url = urllib.parse.urlparse(self.path)
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
        if table != "incident":
            return 400, {"error": {"message": "Invalid table " + table}}
        if method == "GET" and not sys_id:
            rows = [i for i in INCIDENTS.values() if matches(i, q.get("sysparm_query", ""))]
            rows = rows[:int(q.get("sysparm_limit", "10000"))]
            disp = q.get("sysparm_display_value") == "true"
            return 200, {"result": [pick(show(i, disp), q.get("sysparm_fields")) for i in rows]}
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
            rec.update({"sys_id": sid, "number": "INC00%d" % COUNTER[0], "state": rec.get("state") or "1",
                        "sys_created_on": "2026-09-29 12:00:00", "work_notes_list": []})
            journal_add(rec, body, name)
            INCIDENTS[sid] = rec
            return 201, {"result": {"sys_id": sid, "number": rec["number"]}}
        rec = INCIDENTS.get(sys_id)
        if rec is None:
            return 404, {"error": {"message": "No Record found"}}
        if method == "GET":
            disp = q.get("sysparm_display_value") == "true"
            out = pick(rec, q.get("sysparm_fields"))
            if disp:
                out["state"] = STATE_LABEL.get(rec["state"], rec["state"])
                out["priority"] = "5 - Planning" if rec.get("urgency") == "3" else "3 - Moderate"
            return 200, {"result": out}
        if method == "PATCH":
            if not itil:
                return fail("Operation Failed", "ACL Exception Update Failed due to security constraints", 403)
            if "state" in body and os.environ.get("SNMOCK_NO_RESOLVE") == "1":
                return fail("Operation Failed", "Data Policy Exception: Resolution code is mandatory", 403)
            if "work_notes" in body:
                rec["work_notes_list"].append(body["work_notes"])
            journal_add(rec, body, name)
            for k in ("state", "close_code", "close_notes"):
                if k in body:
                    rec[k] = body[k]
            return 200, {"result": {"number": rec["number"], "state": rec["state"]}}
        return 405, {"error": {"message": "Method not allowed"}}


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
