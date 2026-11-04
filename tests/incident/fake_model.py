#!/usr/bin/env python3
"""A scripted GenAI model for tests/incident/run_incident_test.sh (standard library only).

  fake_model.py PORT LOG MODE

Answers an OpenAI-style chat completion. It reads the typed boxes ACT pipes to it (the JSON with "fields") and answers
as the outage AAR / SOP prompt asks - MODE good: each box tidied ("teh" -> "the", "recieved" -> "received") and two
suggestions, and the SOP's steps written from the AAR when asked; drop: the first box's text loses its numbers (the job
must keep that box as typed) and the SOP's steps name an address the AAR does not have (not used); junk: no JSON at all.
Each request's body is appended to LOG, one JSON per line."""
import http.server
import json
import re
import sys

port, log, mode = int(sys.argv[1]), sys.argv[2], sys.argv[3]


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        with open(log, "a") as f:
            f.write(body.replace("\n", " ") + "\n")
        sent = json.loads(body)
        text = "\n".join(str(m.get("content")) for m in sent["messages"])
        fields = {}
        at = text.find('"context"')         # the evidence: {"context": {...}, "fields": {...}}
        if at >= 0:
            start = text.rfind("{", 0, at)
            try:
                fields = json.JSONDecoder().raw_decode(text[start:])[0].get("fields") or {}
            except ValueError:
                fields = {}
        if mode == "junk":
            answer = "I cannot help with that."
        else:
            tidy = {}
            for i, (k, v) in enumerate(sorted(fields.items())):
                t = v["text"].replace("teh", "the").replace("recieved", "received")
                if mode == "drop" and i == 0:
                    t = re.sub(r"\d", "", t)
                tidy[k] = t
            sugg = [{"for": "improvements", "text": "Add a rollback plan to each patch change.", "why": "the rollback fixed it"},
                    {"for": "sop_verify", "text": "Confirm a user can log in from outside.", "why": "remote users were hit"}]
            out = {}
            if "TIDY:" in text:
                out["tidy"] = tidy
            if "SUGGEST:" in text:
                out["suggestions"] = sugg
            if "SOP: the SOP is made from this outage" in text:
                out["sop"] = {"sop_troubleshoot": ["Open a bridge with the on-call teams.",
                                                   "Fail over to 192.0.2.%s." % ("99" if mode == "drop" else "30")],
                              "sop_fix": ["Roll the PHP package back."], "sop_verify": ["Log in to the portal from outside."]}
            answer = "Done.\n\nBEGIN_ACT_ANALYSIS\n%s\nEND_ACT_ANALYSIS\n" % json.dumps(out, indent=1)
        reply = json.dumps({"choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


http.server.ThreadingHTTPServer(("127.0.0.1", port), Model).serve_forever()
