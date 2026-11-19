#!/usr/bin/env python3
"""A stand-in of AAP's API for the site portal's tests (tests/test_site_portal.py, tests/site_portal/run_portal_test.sh):
the parts the portal uses, under api/controller/v2/ (AAP 2.5 and later). Not AAP: just enough to launch, follow and
approve.

  GET  me/                                         the token's account (Bearer FAKE_AAP_TOKEN)
  GET  workflow_job_templates/?name=  job_templates/?name=      by name
  GET  <kind>/<id>/launch/   <kind>/<id>/survey_spec/           what a launch takes
  POST <kind>/<id>/launch/                         a new job (its body kept in FAKE_AAP_LOG)
  GET  inventories/?name=   inventories/<id>/hosts/?name=
  GET  unified_jobs/?id=   jobs/<id>/   jobs/<id>/job_events/   jobs/<id>/job_host_summaries/
  GET  workflow_jobs/<id>/   workflow_jobs/<id>/workflow_nodes/
  GET  workflow_approvals/?status=pending   POST workflow_approvals/<id>/approve/ and deny/
  GET  settings/jobs/                              403 (an account that may not read settings), or FAKE_AAP_JINJA

A job runs for FAKE_AAP_RUN_SECONDS (4): running, its events appearing, then successful (failed when its limit is a
host named fail*). A workflow with an approval node waits there until approved (then its job runs) or denied (failed).
Standard library only."""
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = os.environ.get("FAKE_AAP_TOKEN", "fake-token")
LOG = os.environ.get("FAKE_AAP_LOG", "")
RUN = float(os.environ.get("FAKE_AAP_RUN_SECONDS", "4"))
BASE = "/api/controller/v2/"

LOCK = threading.Lock()
STATE = {
    "next": 100,
    "templates": {
        "job_templates": [
            {"id": 7, "name": "Linux - recheck one host", "ask_variables_on_launch": True, "ask_limit_on_launch": True,
             "survey_enabled": False},
            {"id": 8, "name": "No prompts template", "ask_variables_on_launch": False, "ask_limit_on_launch": False,
             "survey_enabled": False},
            {"id": 9, "name": "Disk - clean up one mount", "ask_variables_on_launch": True, "ask_limit_on_launch": True,
             "survey_enabled": False},
        ],
        "workflow_job_templates": [
            {"id": 40, "name": "Checklists imported into STIG Manager", "ask_variables_on_launch": False,
             "ask_limit_on_launch": False, "survey_enabled": True, "approval": True,
             "survey": ["stigman_ckl_import_mode", "portal_requested_by"]},
            {"id": 44, "name": "ACT - triage finding", "ask_variables_on_launch": True, "ask_limit_on_launch": True,
             "survey_enabled": False, "approval": False},
        ],
    },
    "inventories": [{"id": 3, "name": "Site hosts", "hosts": ["web01.example.mil", "web03.example.mil", "fail01.example.mil"],
                     "groups": {"web_servers": ["web01.example.mil", "web03.example.mil"], "failing": ["fail01.example.mil"]}}],
    "jobs": {},
    "approvals": {},
    "reports": [],                  # 1.75.3: finished report jobs the pick list reads: {id, template, playbook, status, finished (epoch), artifacts}
}
PAGE_CAP = int(os.environ.get("FAKE_AAP_PAGE_CAP", "200"))          # the biggest page the server gives
AUDITOR = os.environ.get("FAKE_AAP_AUDITOR", "1") != "0"          # the account's Auditor role of an organization (0: not given)


def add_template(kind, name, labels=(), spec=None, limit=False, execute=True, description="", tid=None, many_labels=0, needs_inventory=False, inventory=3):
    """1.78.0: a template the account may (execute=True) or may not run, with its labels and a survey spec ([{variable, type, ...}]) -> its id.
    many_labels: that many more labels (AAP lists only 10 of them in a row; the rest are at <kind>/<id>/labels/)."""
    tid = tid or new_id()
    t = {"id": tid, "name": name, "ask_variables_on_launch": False, "ask_limit_on_launch": bool(limit), "survey_enabled": spec is not None,
         "labels": ["extra-%d" % i for i in range(many_labels)] + list(labels), "execute": execute, "description": description,
         "spec": spec, "survey": [q["variable"] for q in (spec or [])], "needs_inventory": needs_inventory, "inventory": inventory}
    STATE["templates"][kind].append(t)
    return tid


def row_of(t):
    labs = [{"id": 500 + i, "name": n} for i, n in enumerate(t.get("labels", []))]
    return {"id": t["id"], "name": t["name"], "description": t.get("description", ""), "survey_enabled": t["survey_enabled"],
            "ask_limit_on_launch": t["ask_limit_on_launch"], "ask_variables_on_launch": t["ask_variables_on_launch"],
            "inventory": t.get("inventory", 3), "playbook": t.get("playbook", "a.yml"), "ask_inventory_on_launch": bool(t.get("ask_inventory")),
            "summary_fields": {"labels": {"count": len(labs), "results": labs[:10]}}}


def add_report(template, playbook, status, finished, findings, jid=None, path=None):
    """A finished report job with its site_findings artifact ({host: [entry]}) -> its id. AAP keeps a playbook as the path in its project:
    'playbooks/health_check.yml' (path=... to say another, e.g. 'health_check.yml' at the project's root) - never the bare name."""
    jid = jid or new_id()
    full = path or ("playbooks/" + playbook)
    tpl = STATE.setdefault("report_templates", {}).setdefault(template, {"id": new_id(), "playbook": full})
    STATE["reports"].append({"id": jid, "template": template, "template_id": tpl["id"], "playbook": full, "status": status, "finished": finished,
                             "artifacts": {"site_findings": findings} if findings is not None else {}})
    return jid


def report_view(r, with_artifacts=True):
    out = {"id": r["id"], "type": "job", "name": r["template"], "playbook": r["playbook"], "status": r["status"],
           "finished": iso(r["finished"]), "summary_fields": {"job_template": {"id": r["template_id"], "name": r["template"]}}}
    if with_artifacts:
        out["artifacts"] = r["artifacts"]
    return out


def log(rec):
    if LOG:
        with LOCK, open(LOG, "a") as fh:
            fh.write(json.dumps(dict({"at": time.time()}, **rec)) + "\n")


def new_id():
    with LOCK:
        STATE["next"] += 1
        return STATE["next"]


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime(t)) if t else None


def job_status(j):
    """A job's status, by the clock: running for RUN seconds after it may start, then done."""
    if j["type"] == "workflow_approval":
        return j["status"]
    if j["type"] == "workflow_job":
        nodes = [STATE["jobs"][n] for n in j["nodes"] if n in STATE["jobs"]]
        sts = [job_status(n) for n in nodes]
        if any(s in ("failed", "error") for s in sts):
            return "failed"
        if nodes and all(s == "successful" for s in sts):
            return "successful"
        return "running"
    if j.get("after"):                              # waits for an approval node
        ap = STATE["jobs"][j["after"]]
        if ap["status"] == "pending":
            return "pending"
        if ap["status"] != "successful":
            return "canceled"
        start = ap["decided"]
    else:
        start = j["created"]
    el = time.time() - start
    if el < 0.5:
        return "pending"
    if el < RUN:
        return "running"
    return "failed" if (j.get("limit") or "").startswith("fail") else "successful"


def view(j):
    st = job_status(j)
    start = j["created"] if not j.get("after") else STATE["jobs"][j["after"]].get("decided")
    done = st in ("successful", "failed", "error", "canceled")
    out = {"id": j["id"], "type": j["type"], "name": j["name"], "status": st, "failed": st in ("failed", "error"),
           "started": iso(start) if st != "pending" else None, "finished": iso((start or 0) + RUN) if done and start else None,
           "elapsed": round(min(time.time() - start, RUN), 3) if start else 0, "limit": j.get("limit", ""),
           "extra_vars": json.dumps(j.get("extra_vars", {}))}
    if j["type"] == "job":
        out["job_template"] = j.get("template_id")
        out["artifacts"] = {"site_report": {"url": "https://reports.example.mil/linux-health/2026-11-15_job-%d.html" % j["id"]},
                            "counts": {"critical": 0, "warning": 1}} if st == "successful" else {}
        ev = j.get("extra_vars") or {}
        if st in ("successful", "failed") and ev.get("finding_id"):          # part C: a fix's result
            out["artifacts"]["portal_result"] = {"finding_id": ev["finding_id"], "fixed": "true" if st == "successful" else "false",
                                                 "before": "93% full", "after": "71% full" if st == "successful" else "93% full",
                                                 "summary": "cleaned /var/tmp"}
        if st == "successful" and ev.get("next_action"):
            out["artifacts"]["act_triage_finding"] = {"next_action": ev["next_action"]}
    if j["type"] == "workflow_job":
        out["workflow_job_template"] = j["template"]
    return out


TASKS = ["Disk | Checking free space", "Services | Checking the services", "Time | Checking the clock", "Packages | Listing updates", "Logs | Reading the journal",
         "Net | Checking the links", "Users | Checking the accounts", "Disk | Checking inodes", "Cert | Checking certificates", "Audit | Reading audit rules",
         "Report | Writing the report", "Report | Saving the report"]


def events(j):
    """The job's events as AAP lists them (counter, event, event_data, host_name, stdout): a play, 12 tasks, each task's result on every host of the
    limit (comma list; a host named fail* fails its 3rd task, one named down* is unreachable from the 2nd), then the recap. Only what the clock has reached."""
    st = job_status(j)
    if st == "pending":
        return []
    start = j["created"] if not j.get("after") else STATE["jobs"][j["after"]]["decided"]
    hosts = [h for h in (j.get("limit") or "localhost").split(",") if h]
    out = []

    def add(event, host="", task="", res=None, stdout="", changed=False, failed=False):
        out.append({"id": len(out) + 1, "counter": len(out) + 1, "event": event, "host_name": host, "stdout": stdout, "changed": changed, "failed": failed,
                    "event_data": {"play": "Recheck", "task": task, "host": host, "res": res or {}}})
    add("playbook_on_play_start", stdout="PLAY [Recheck] *****************************************************************")
    dead = set()
    for i, task in enumerate(TASKS, 1):
        add("playbook_on_task_start", task=task, stdout="TASK [%s] ******************************************************************" % task)
        for h in hosts:
            if h in dead:
                continue
            if h.startswith("down") and i >= 2:
                add("runner_on_unreachable", h, task, {"msg": "Failed to connect to the host via ssh: Connection timed out"}, "fatal: [%s]: UNREACHABLE!" % h, failed=True)
                dead.add(h)
            elif h.startswith("fail") and i == 3:
                add("runner_on_failed", h, task, {"msg": "The clock is 14 minutes off (password=Hunter2-not-real)"}, "fatal: [%s]: FAILED!" % h, failed=True)
                dead.add(h)
            else:
                add("runner_on_ok", h, task, {"changed": i == 8}, "\x1b[0;32mok: [%s]\x1b[0m" % h, changed=i == 8)
    if st in ("successful", "failed"):
        add("playbook_on_stats", stdout="PLAY RECAP *********************************************************************\n%s" % "\n".join(
            "%s : ok=11 changed=0 unreachable=%d failed=%d skipped=0" % (h, 1 if h.startswith("down") else 0, 1 if h.startswith("fail") else 0) for h in hosts))
        return out
    frac = min(time.time() - start, RUN) / RUN
    return out[:max(3, int(frac * len(out)))]


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        raw = b"" if code == 204 else json.dumps(body if body is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer " + TOKEN:
            self._send(401, {"detail": "Authentication credentials were not provided."})
            return False
        return True

    def _paged_list(self, q, path, rows):
        size = min(int(q.get("page_size", "25")), PAGE_CAP)
        page = int(q.get("page", "1"))
        more = len(rows) > page * size
        nxt = (BASE + path + "?" + "&".join("%s=%s" % (k, v) for k, v in dict(q, page=str(page + 1)).items())) if more else None
        return self._send(200, {"count": len(rows), "next": nxt, "results": rows[(page - 1) * size:page * size]})

    def do_GET(self):
        u = urlsplit(self.path)
        if not u.path.startswith(BASE):
            return self._send(404, {"detail": "Not found."})
        p, q = u.path[len(BASE):], {k: v[0] for k, v in parse_qs(u.query).items()}
        if not self._auth():
            return None
        log({"method": "GET", "path": p, "query": q})
        if p == "me/":
            return self._send(200, {"results": [{"username": "svc_portal", "id": 77, "is_system_auditor": False}]})
        if p == "users/77/roles/":
            roles = [{"id": 5, "name": "Auditor", "summary_fields": {"resource_type": "organization", "resource_name": "Default"}}] if AUDITOR else []
            return self._send(200, {"count": len(roles), "results": roles})
        if p == "job_templates/" and (q.get("playbook__regex") or q.get("playbook__iregex")):       # the report job templates (an account that may not read them sees none)
            flags = 0 if q.get("playbook__regex") else re.I
            rx = q.get("playbook__regex") or q.get("playbook__iregex")
            rows = [{"id": t["id"], "name": n, "playbook": t["playbook"]} for n, t in sorted(STATE.get("report_templates", {}).items())
                    if AUDITOR and re.search(rx, t["playbook"], flags)]
            size = min(int(q.get("page_size", "25")), PAGE_CAP)                  # a server may cap the page size
            page = int(q.get("page", "1"))
            more = len(rows) > page * size
            nxt = (BASE + "job_templates/?" + "&".join("%s=%s" % (k, v) for k, v in dict(q, page=str(page + 1)).items())) if more else None
            return self._send(200, {"count": len(rows), "next": nxt, "results": rows[(page - 1) * size:page * size]})
        if p == "jobs/":                                            # the report jobs: filters playbook, playbook__startswith, status__in, finished__gt
            rows = list(STATE["reports"]) if AUDITOR else []
            if q.get("playbook"):                                   # AAP's playbook is the path in the project: only the whole path matches
                rows = [r for r in rows if r["playbook"] == q["playbook"]]
            if q.get("playbook__startswith"):
                rows = [r for r in rows if r["playbook"].startswith(q["playbook__startswith"])]
            if q.get("playbook__regex"):
                rows = [r for r in rows if re.search(q["playbook__regex"], r["playbook"])]
            if q.get("job_template"):
                rows = [r for r in rows if str(r["template_id"]) == q["job_template"]]
            if q.get("status__in"):
                rows = [r for r in rows if r["status"] in q["status__in"].split(",")]
            if q.get("finished__gt"):
                rows = [r for r in rows if iso(r["finished"]) > q["finished__gt"].replace("Z", ".000000Z")]
            rows.sort(key=lambda r: -r["finished"])
            if q.get("job_template") and not rows:                  # 1.78.0: jobs the portal started from a template, newest first
                mine = [view(j) for j in sorted(STATE["jobs"].values(), key=lambda x: -x["created"]) if j.get("template_id") == int(q["job_template"])]
                if q.get("status"):
                    mine = [v for v in mine if v["status"] == q["status"]]
                return self._send(200, {"count": len(mine), "results": mine[:int(q.get("page_size", "25"))]})
            size = int(q.get("page_size", "25"))
            noart = os.environ.get("FAKE_AAP_LIST_NO_ARTIFACTS") == "1"
            return self._send(200, {"count": len(rows), "results": [report_view(r, not noart) for r in rows[:size]]})
        if p == "settings/jobs/":
            j = os.environ.get("FAKE_AAP_JINJA")
            return self._send(200, {"ALLOW_JINJA_IN_EXTRA_VARS": j}) if j else self._send(403, {"detail": "You do not have permission."})
        m = re.match(r"^(job_templates|workflow_job_templates)/$", p)
        if m and q.get("role_level") == "execute_role":                 # 1.78.0: what the account may Execute, paged
            allr = [row_of(t) for t in STATE["templates"][m.group(1)] if t.get("execute", True) and t.get("labels") is not None]
            size = min(int(q.get("page_size", "25")), PAGE_CAP)
            page = int(q.get("page", "1"))
            more = len(allr) > page * size
            nxt = (BASE + m.group(1) + "/?" + "&".join("%s=%s" % (k, v) for k, v in dict(q, page=str(page + 1)).items())) if more else None
            return self._send(200, {"count": len(allr), "next": nxt, "results": allr[(page - 1) * size:page * size]})
        ml = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/labels/$", p)
        if ml:
            t = next((t for t in STATE["templates"][ml.group(1)] if t["id"] == int(ml.group(2))), None)
            labs = [{"id": 500 + i, "name": n} for i, n in enumerate((t or {}).get("labels", []))]
            return self._send(200, {"count": len(labs), "next": None, "results": labs})
        if m and not q.get("id__in"):
            rows = [{"id": t["id"], "name": t["name"]} for t in STATE["templates"][m.group(1)] if t["name"] == q.get("name")]
            return self._send(200, {"count": len(rows), "results": rows})
        mi = re.match(r"^(job_templates|workflow_job_templates)/$", p)
        if mi and q.get("id__in"):                                      # 1.78.0: the cards' last runs, one call per kind
            rows = []
            for t in STATE["templates"][mi.group(1)]:
                if str(t["id"]) not in q["id__in"].split(","):
                    continue
                mine = [j for j in STATE["jobs"].values() if (j.get("template_id") if mi.group(1) == "job_templates" else j.get("template")) == t["id"]
                        and j["type"] in ("job", "workflow_job")]
                last = sorted(mine, key=lambda j: -j["created"])[:1]
                lj = {"id": last[0]["id"], "status": job_status(last[0]), "finished": iso(last[0]["created"])} if last else {}
                rows.append({"id": t["id"], "name": t["name"], "summary_fields": {"last_job": lj} if lj else {}})
            return self._send(200, {"count": len(rows), "results": rows})
        if p == "workflow_jobs/" and q.get("workflow_job_template"):
            mine = [view(j) for j in sorted(STATE["jobs"].values(), key=lambda x: -x["created"]) if j["type"] == "workflow_job" and str(j.get("template")) == q["workflow_job_template"]]
            return self._send(200, {"count": len(mine), "results": mine[:int(q.get("page_size", "25"))]})
        mw = re.match(r"^workflow_job_templates/(\d+)/workflow_nodes/$", p)
        if mw:                                                           # 1.78.0: a workflow's steps (the portal fingerprints them)
            t = next((t for t in STATE["templates"]["workflow_job_templates"] if t["id"] == int(mw.group(1))), None)
            return self._paged_list(q, p, list((t or {}).get("nodes", [])))
        md = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/$", p)
        if md:                                                           # 1.78.0: one template (the portal asks again before it launches)
            t = next((t for t in STATE["templates"][md.group(1)] if t["id"] == int(md.group(2)) and t.get("labels") is not None), None)
            return self._send(200, row_of(t)) if t else self._send(404, {"detail": "Not found."})
        m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/(launch|survey_spec)/$", p)
        if m:
            t = next((t for t in STATE["templates"][m.group(1)] if t["id"] == int(m.group(2))), None)
            if not t:
                return self._send(404, {"detail": "Not found."})
            if m.group(3) == "launch" and t.get("no_launch_page"):
                return self._send(404, {"detail": "Not found."})
            if m.group(3) == "survey_spec":
                return self._send(200, {"spec": t["spec"] if t.get("spec") is not None else [{"variable": v} for v in t.get("survey", [])]})
            out = {k: t[k] for k in ("ask_variables_on_launch", "ask_limit_on_launch", "survey_enabled")}
            out["inventory_needed_to_start"] = bool(t.get("needs_inventory"))
            return self._send(200, out)
        if p == "inventories/":
            rows = [{"id": i["id"], "name": i["name"]} for i in STATE["inventories"] if i["name"] == q.get("name")]
            return self._send(200, {"count": len(rows), "results": rows})
        m = re.match(r"^inventories/(\d+)/hosts/$", p)
        if m:
            inv = next((i for i in STATE["inventories"] if i["id"] == int(m.group(1))), {"hosts": []})
            if q.get("name") or q.get("name__icontains"):
                rows = [{"name": h} for h in inv["hosts"] if h == q.get("name") or (q.get("name__icontains") and q["name__icontains"].lower() in h.lower())]
                return self._send(200, {"count": len(rows), "results": rows})
            return self._paged_list(q, "inventories/%s/hosts/" % m.group(1), [{"id": 1000 + n, "name": h} for n, h in enumerate(sorted(inv["hosts"]))])
        m = re.match(r"^inventories/(\d+)/groups/$", p)
        if m:
            inv = next((i for i in STATE["inventories"] if i["id"] == int(m.group(1))), {"groups": {}})
            rows = [{"id": 2000 + n, "name": g} for n, g in enumerate(sorted(inv.get("groups", {})))]
            if q.get("name"):
                rows = [r for r in rows if r["name"] == q["name"]]
            return self._paged_list(q, "inventories/%s/groups/" % m.group(1), rows)
        m = re.match(r"^groups/(\d+)/all_hosts/$", p)
        if m:
            inv = STATE["inventories"][0]
            names = sorted(inv.get("groups", {}))
            g = names[int(m.group(1)) - 2000] if 0 <= int(m.group(1)) - 2000 < len(names) else None
            return self._paged_list(q, p, [{"name": h} for h in sorted(inv["groups"].get(g, []))])
        if p == "unified_jobs/" and q.get("id__in"):                  # 1.78.0: the Jobs page's list, one call
            rows = [view(STATE["jobs"][int(i)]) for i in q["id__in"].split(",") if i.isdigit() and int(i) in STATE["jobs"]]
            return self._send(200, {"count": len(rows), "results": rows})
        if p == "unified_jobs/":
            j = STATE["jobs"].get(int(q.get("id", "0")))
            return self._send(200, {"count": 1 if j else 0, "results": [view(j)] if j else []})
        m = re.match(r"^(jobs|workflow_jobs)/(\d+)/$", p)
        if m and m.group(1) == "jobs" and AUDITOR and any(r["id"] == int(m.group(2)) for r in STATE["reports"]):
            return self._send(200, report_view(next(r for r in STATE["reports"] if r["id"] == int(m.group(2)))))
        if m:
            j = STATE["jobs"].get(int(m.group(2)))
            return self._send(200, view(j)) if j else self._send(404, {"detail": "Not found."})
        m = re.match(r"^jobs/(\d+)/job_events/$", p)
        if m:
            j = STATE["jobs"].get(int(m.group(1)))
            ev = events(j) if j else []
            if q.get("counter__gt"):
                ev = [e for e in ev if e["counter"] > int(q["counter__gt"])]
            if q.get("event"):
                ev = [e for e in ev if e["event"] == q["event"]]
            if q.get("order_by") == "-counter":
                ev = sorted(ev, key=lambda e: -e["counter"])
            size = min(int(q.get("page_size", "25")), PAGE_CAP)
            page = int(q.get("page", "1"))
            more = len(ev) > page * size
            nxt = (BASE + p + "?" + "&".join("%s=%s" % (k, v) for k, v in dict(q, page=str(page + 1)).items())) if more else None
            return self._send(200, {"count": len(ev), "next": nxt, "results": ev[(page - 1) * size:page * size]})
        m = re.match(r"^jobs/(\d+)/stdout/$", p)
        if m:
            j = STATE["jobs"].get(int(m.group(1)))
            text = "\n".join(e["stdout"] for e in (events(j) if j else []) if e["stdout"])
            raw = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return None
        m = re.match(r"^jobs/(\d+)/job_host_summaries/$", p)
        if m:
            j = STATE["jobs"].get(int(m.group(1)))
            st = job_status(j) if j else ""
            rows = [{"host_name": j.get("limit") or "localhost", "ok": 11, "changed": 0, "failures": 1 if st == "failed" else 0,
                     "dark": 0, "skipped": 0}] if st in ("successful", "failed") else []
            return self._send(200, {"count": len(rows), "results": rows})
        m = re.match(r"^workflow_jobs/(\d+)/workflow_nodes/$", p)
        if m:
            j = STATE["jobs"].get(int(m.group(1)))
            if not j:
                return self._send(404, {"detail": "Not found."})
            rows = []
            for i, nid in enumerate(j["nodes"]):
                n = STATE["jobs"][nid]
                rows.append({"id": 900 + i, "do_not_run": False, "summary_fields": {
                    "job": {"id": n["id"], "name": n["name"], "type": n["type"], "status": job_status(n), "failed": job_status(n) == "failed"},
                    "unified_job_template": {"name": n["name"], "unified_job_type": n["type"]}}})
            return self._send(200, {"count": len(rows), "results": rows})
        if p == "workflow_approvals/":
            rows = []
            for a in sorted(STATE["approvals"].values(), key=lambda x: x["created"]):
                if q.get("status") and a["status"] != q["status"]:
                    continue
                wf = STATE["jobs"][a["workflow"]]
                rows.append({"id": a["id"], "name": a["name"], "status": a["status"], "created": iso(a["created"]),
                             "approval_expiration": None,
                             "summary_fields": {"source_workflow_job": {"id": wf["id"], "name": wf["name"], "status": job_status(wf)}}})
            return self._send(200, {"count": len(rows), "results": rows})
        return self._send(404, {"detail": "Not found."})

    def do_POST(self):
        u = urlsplit(self.path)
        if not u.path.startswith(BASE):
            return self._send(404, {"detail": "Not found."})
        p = u.path[len(BASE):]
        if not self._auth():
            return None
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        log({"method": "POST", "path": p, "body": body})
        m = re.match(r"^(job_templates|workflow_job_templates)/(\d+)/launch/$", p)
        if m:
            t = next((t for t in STATE["templates"][m.group(1)] if t["id"] == int(m.group(2))), None)
            if not t:
                return self._send(404, {"detail": "Not found."})
            ev, limit = body.get("extra_vars") or {}, body.get("limit") or ""
            ignored = {}
            if not t["ask_variables_on_launch"]:
                if t.get("survey_enabled"):
                    extra = sorted(k for k in ev if k not in t.get("survey", []))
                    if extra:
                        ignored["extra_vars"] = {k: ev[k] for k in extra}
                elif ev:
                    ignored["extra_vars"] = ev
            if limit and not t["ask_limit_on_launch"]:
                ignored["limit"] = limit
            now = time.time()
            if m.group(1) == "job_templates":
                jid = new_id()
                STATE["jobs"][jid] = {"id": jid, "type": "job", "name": t["name"], "created": now, "limit": limit, "extra_vars": ev, "template_id": t["id"]}
                return self._send(201, {"job": jid, "id": jid, "ignored_fields": ignored})
            wid = new_id()
            nodes = []
            after = None
            if t.get("approval"):
                aid = new_id()
                STATE["jobs"][aid] = {"id": aid, "type": "workflow_approval", "name": "Approve the import", "created": now,
                                      "status": "pending"}
                STATE["approvals"][aid] = {"id": aid, "name": "Approve the import", "status": "pending", "created": now, "workflow": wid}
                nodes.append(aid)
                after = aid
            cid = new_id()
            STATE["jobs"][cid] = {"id": cid, "type": "job", "name": t["name"] + " - step", "created": now, "limit": limit,
                                  "extra_vars": ev, "after": after}
            nodes.append(cid)
            STATE["jobs"][wid] = {"id": wid, "type": "workflow_job", "name": t["name"], "created": now, "nodes": nodes,
                                  "template": t["id"], "extra_vars": ev, "limit": limit}
            return self._send(201, {"workflow_job": wid, "id": wid, "ignored_fields": ignored})
        m = re.match(r"^workflow_approvals/(\d+)/(approve|deny)/$", p)
        if m:
            a = STATE["approvals"].get(int(m.group(1)))
            if not a or a["status"] != "pending":
                return self._send(400, {"detail": "This approval is not pending."})
            a["status"] = "successful" if m.group(2) == "approve" else "failed"
            STATE["jobs"][a["id"]]["status"] = a["status"]
            STATE["jobs"][a["id"]]["decided"] = time.time()
            return self._send(204)
        return self._send(404, {"detail": "Not found."})


def serve(port, stop=None):
    httpd = ThreadingHTTPServer(("0.0.0.0", port), H)
    httpd.daemon_threads = True
    if stop is not None:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    httpd.serve_forever()


def seed_reports(now=None):
    """The report jobs of the pick list's tests (FAKE_AAP_SEED_REPORTS=1): ids 9001 (Health check - Linux, failed: it found something) and
    9002 (Disk report, successful), findings of web01.example.mil and web03.example.mil."""
    now = now or time.time()
    e = lambda kind, host, key, title, sev: {"id": "%s:%s:%s" % (kind, host, key), "kind": kind, "host": host, "title": title,            # noqa: E731
                                              "severity": sev, "data": {"os": "linux"}}
    add_report("Health check - Linux", "health_check.yml", "failed", now - 3600, {
        "web01.example.mil": [e("disk_full", "web01.example.mil", "/var", "/var is 96% full", "critical"),
                              e("disk_full", "web01.example.mil", "/home", "/home is 91% full", "warning")],
        "web03.example.mil": [e("disk_full", "web03.example.mil", "/srv", "/srv is 90% full", "warning")]}, jid=9001)
    add_report("Disk report", "disk_report.yml", "successful", now - 90000, {
        "web01.example.mil": [e("disk_full", "web01.example.mil", "/var", "/var is 96% full", "critical")]}, jid=9002)


def seed_found():
    """1.78.0 (FAKE_AAP_SEED_FOUND=1): templates for the portal to find - 'Quick check (found)' (labelled, a survey with a number and a
    password, a Limit), one the account may run without the label, and one with a survey type the portal does not handle."""
    spec = [{"variable": "rounds", "question_name": "Rounds", "type": "integer", "required": True, "min": 1, "max": 5, "default": 2},
            {"variable": "secret", "question_name": "Secret", "type": "password", "required": True, "max": 40},
            {"variable": "portal_requested_by", "question_name": "Started by", "type": "text", "required": False}]
    add_template("job_templates", "Quick check (found)", ["portal", "portal-area:Health"], spec, limit=True, description="A quick check, found in AAP.")
    add_template("job_templates", "Run but not labelled", ["other"])
    add_template("job_templates", "Odd survey (found)", ["portal"], [{"variable": "f", "type": "file"}])


if os.environ.get("FAKE_AAP_SEED_FOUND") == "1":
    seed_found()
if os.environ.get("FAKE_AAP_SEED_REPORTS") == "1":
    seed_reports()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8000)
