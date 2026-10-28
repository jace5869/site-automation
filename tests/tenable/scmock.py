#!/usr/bin/env python3
"""Fake Tenable.sc (ACAS) REST API for tests/tenable/run_tenable_test.sh: three scan zones (East 192.0.2.0/25 and a range,
West 192.0.2.128/25, Lab 192.0.2.0/24 - wider than East, so East wins for 192.0.2.10), two scanners (West's cannot
be reached), two policies, one repository, one scan credential. Scans are made (POST /rest/scan), updated (PATCH),
launched (POST /rest/scan/ID/launch -> a scan result that is Queued, then Running, then Completed, one step per
look), and analysed (POST /rest/analysis: sumseverity and sumip - a host whose address ends in .10 has a critical).
Login: API keys (x-apikey: accesskey=AK123; secretkey=SK456) or a token (POST /rest/token svc-tenable / goodpw).
SCMOCK_FAIL_LAUNCH=<zone id>: a scan in that zone refuses to launch. Every call that changes something is appended
to $SCMOCK_LOG as a JSON line. State is kept while the fake runs. Usage: scmock.py PORT"""
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = os.environ.get("SCMOCK_LOG", "scmock.jsonl")
FAIL_LAUNCH = os.environ.get("SCMOCK_FAIL_LAUNCH", "")
KEYS = "accesskey=AK123; secretkey=SK456"
ZONES = [{"id": "1", "name": "Datacenter East", "description": "East", "ipList": "192.0.2.0/25,198.51.100.10-198.51.100.20",
          "scanners": [{"id": "1", "name": "scanner-east"}]},
         {"id": "2", "name": "Datacenter West", "description": "", "ipList": "192.0.2.128/25", "scanners": [{"id": "2", "name": "scanner-west"}]},
         {"id": "3", "name": "Lab", "description": "every lab address", "ipList": "192.0.2.0/24", "scanners": [{"id": "1", "name": "scanner-east"}]}]
SCANNERS = [{"id": "1", "name": "scanner-east", "status": "1", "zones": [{"id": "1"}, {"id": "3"}], "enabled": "true"},
            {"id": "2", "name": "scanner-west", "status": "16", "zones": [{"id": "2"}], "enabled": "true"}]
POLICIES = {"usable": [{"id": "7", "name": "Basic Network Scan", "description": "ports and services", "status": "0"},
                       {"id": "9", "name": "ACAS Best Practice", "description": "", "status": "0"}], "manageable": []}
REPOS = [{"id": "1", "name": "Main", "description": "", "type": "Local", "dataFormat": "IPv4"}]
CREDS = {"usable": [{"id": "3", "name": "Linux ssh", "type": "ssh"}], "manageable": []}
STATE = {"scans": [], "results": {}, "scan_id": 100, "result_id": 500, "tokens": set()}


def log(call, body=None):
    with open(LOG, "a") as f:
        f.write(json.dumps({"call": call, "body": body}) + "\n")


def envelope(data, code=0, msg=""):
    return {"type": "regular", "response": data, "error_code": code, "error_msg": msg, "warnings": [], "timestamp": int(time.time())}


def findings(ip):
    """What a scan finds on a host: a critical on an address ending in .10, else none; two high, three medium, one low."""
    return {"critical": 1 if ip.endswith(".10") else 0, "high": 2, "medium": 3, "low": 1, "info": 5}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):          # quiet
        pass

    def send(self, status, data, headers=None):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {}

    def authorized(self):
        if self.headers.get("x-apikey", "") == KEYS:
            return True
        tok = self.headers.get("X-SecurityCenter", "")
        return bool(tok) and tok in STATE["tokens"] and "TNS_SESSIONID=" in self.headers.get("Cookie", "")

    def handle_any(self, method):
        path = self.path.split("?")[0]
        body = self.body() if method in ("POST", "PATCH") else {}
        if method == "POST" and path == "/rest/token":
            if body.get("username") == "svc-tenable" and body.get("password") == "goodpw":
                tok = "tok-%d" % int(time.time() * 1000)
                STATE["tokens"].add(tok)
                log("POST /rest/token", {"username": body.get("username")})
                return self.send(200, envelope({"token": tok, "releaseID": "6.4.0"}), {"Set-Cookie": "TNS_SESSIONID=abc123; path=/; secure; HttpOnly"})
            return self.send(403, envelope("", 143, "Invalid login credentials."))
        if not self.authorized():
            return self.send(403, envelope("", 143, "Invalid credentials. Please try again."))
        if method == "DELETE" and path == "/rest/token":
            log("DELETE /rest/token")
            return self.send(200, envelope(""))
        if method == "GET":
            if path == "/rest/currentUser":
                return self.send(200, envelope({"username": "svc-tenable", "role": {"name": "Security Analyst"}, "organization": {"name": "Site"}}))
            if path == "/rest/scanner":
                return self.send(200, envelope(SCANNERS))
            if path == "/rest/zone":
                return self.send(200, envelope(ZONES))
            if path == "/rest/policy":
                return self.send(200, envelope(POLICIES))
            if path == "/rest/repository":
                return self.send(200, envelope(REPOS))
            if path == "/rest/credential":
                return self.send(200, envelope(CREDS))
            if path == "/rest/scan":
                return self.send(200, envelope({"usable": STATE["scans"], "manageable": []}))
            m = re.match(r"^/rest/scanResult/(\d+)$", path)
            if m:
                r = STATE["results"].get(m.group(1))
                if not r:
                    return self.send(403, envelope("", 2, "The scan result %s does not exist." % m.group(1)))
                r["looks"] += 1
                st = "Queued" if r["looks"] == 1 else ("Running" if r["looks"] == 2 else "Completed")
                done = st == "Completed"
                return self.send(200, envelope({"id": r["id"], "name": r["name"], "status": st, "running": "false" if done else "true",
                                                "totalIPs": str(len(r["ips"])), "scannedIPs": str(len(r["ips"]) if done else 0),
                                                "totalChecks": "100", "completedChecks": "100" if done else "40",
                                                "importStatus": "Finished" if done else "", "errorDetails": "",
                                                "startTime": str(int(time.time())), "finishTime": str(int(time.time())) if done else "-1"}))
            return self.send(404, envelope("", 1, "no such path"))
        if method == "POST" and path == "/rest/scan":
            STATE["scan_id"] += 1
            scan = dict(body, id=str(STATE["scan_id"]), owner={"username": "svc-tenable"})
            STATE["scans"].append(scan)
            log("POST /rest/scan", {"name": body.get("name"), "zone": (body.get("zone") or {}).get("id"), "ipList": body.get("ipList"),
                                    "policy": (body.get("policy") or {}).get("id"), "repository": (body.get("repository") or {}).get("id"),
                                    "credentials": [c.get("id") for c in body.get("credentials") or []], "schedule": (body.get("schedule") or {}).get("type")})
            return self.send(200, envelope(scan))
        m = re.match(r"^/rest/scan/(\d+)$", path)
        if m and method == "PATCH":
            for s in STATE["scans"]:
                if s["id"] == m.group(1):
                    s.update(body)
                    log("PATCH /rest/scan/%s" % m.group(1), {"name": body.get("name"), "ipList": body.get("ipList"), "zone": (body.get("zone") or {}).get("id")})
                    return self.send(200, envelope(s))
            return self.send(403, envelope("", 2, "The scan %s does not exist." % m.group(1)))
        m = re.match(r"^/rest/scan/(\d+)/launch$", path)
        if m and method == "POST":
            scan = next((s for s in STATE["scans"] if s["id"] == m.group(1)), None)
            if scan is None:
                return self.send(403, envelope("", 2, "The scan %s does not exist." % m.group(1)))
            if FAIL_LAUNCH and str((scan.get("zone") or {}).get("id")) == FAIL_LAUNCH:
                log("POST /rest/scan/%s/launch" % m.group(1), {"refused": True})
                return self.send(403, envelope("", 1, "Scan zone unavailable: no scanner of the zone can be reached."))
            STATE["result_id"] += 1
            rid = str(STATE["result_id"])
            STATE["results"][rid] = {"id": rid, "name": scan.get("name"), "looks": 0, "ips": [x for x in str(scan.get("ipList") or "").split(",") if x]}
            log("POST /rest/scan/%s/launch" % m.group(1), {"result": rid})
            return self.send(200, envelope({"scanResult": {"id": rid, "name": scan.get("name"), "status": "Queued"}}))
        if method == "POST" and path == "/rest/analysis":
            q = body.get("query") or {}
            r = STATE["results"].get(str(q.get("scanID") or body.get("scanID")))
            if r is None:
                return self.send(403, envelope("", 2, "no such scan result"))
            if q.get("tool") == "sumseverity":
                tot = {"4": 0, "3": 0, "2": 0, "1": 0, "0": 0}
                for ip in r["ips"]:
                    f = findings(ip)
                    for sid, k in (("4", "critical"), ("3", "high"), ("2", "medium"), ("1", "low"), ("0", "info")):
                        tot[sid] += f[k]
                names = {"4": "Critical", "3": "High", "2": "Medium", "1": "Low", "0": "Info"}
                return self.send(200, envelope({"totalRecords": "5", "returnedRecords": "5",
                                                "results": [{"severity": {"id": s, "name": names[s]}, "count": str(tot[s])} for s in ("4", "3", "2", "1", "0")]}))
            if q.get("tool") == "sumip":
                rows = []
                for ip in r["ips"]:
                    f = findings(ip)
                    rows.append({"ip": ip, "dnsName": "host-%s.example.mil" % ip.split(".")[-1], "severityCritical": str(f["critical"]),
                                 "severityHigh": str(f["high"]), "severityMedium": str(f["medium"]), "severityLow": str(f["low"]),
                                 "severityInfo": str(f["info"]), "total": str(sum(f.values())), "score": "10"})
                return self.send(200, envelope({"totalRecords": str(len(rows)), "returnedRecords": str(len(rows)), "results": rows}))
            return self.send(403, envelope("", 1, "unknown analysis tool"))
        return self.send(404, envelope("", 1, "no such path"))

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PATCH(self):
        self.handle_any("PATCH")

    def do_DELETE(self):
        self.handle_any("DELETE")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
