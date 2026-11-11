#!/usr/bin/env python3
"""A scripted GenAI model for the ticket insights test (run_insights_test.sh): answers the three questions the job asks -
grouping a batch of incident descriptions into issues (by keywords), the fixes of the top issues (one marked suggested,
a knowledge article draft where there are enough incidents, a placeholder from the evidence in its why), what each scope's
numbers say - in the BEGIN/END_ACT_ANALYSIS JSON. FAKE_MODEL_LEAKY_LABEL=1: the VPN issue named with a person, an IP
address and a host (the ACT cache must store it masked, 1.17.0). Every request's messages are appended to $FAKE_MODEL_LOG (the test looks
for real names there: there must be none).
    fake_insights_model.py PORT"""
import http.server
import json
import os
import re
import sys

ISSUES = [(r"lock|password", "Account locked out / password reset", "incident", True),
          (r"cac|piv|smart card", "CAC / PIV certificate not recognized", "incident", False),
          (r"horizon|vdi", "Horizon VDI session will not connect", "incident", False),
          (r"outlook", "Outlook will not open", "incident", False),
          (r"print", "Printer offline / print queue stuck", "incident", False),
          (r"share|mapped drive|access denied|folder", "Mapped drive / share access denied", "incident", False),
          (r"vpn", "VPN drops or will not connect", "incident", False),
          (r"install", "Software install opened as an incident", "request", False),
          (r"slow log|logon|login very slow", "Slow sign-in (Group Policy)", "incident", False),
          (r"full", "Filesystem full / service stopped", "incident", False),
          (r"network|port", "Network port down in a building", "incident", False),
          (r"teams", "Teams audio or video not working", "incident", False)]


def answer(text):
    if "Group IT service desk incidents" in text:
        known = set(re.findall(r"^- (.+?) \|", text, re.M))
        items, new = [], []
        for d, _, _, desc in re.findall(r"^(D\d+) \| (\d+) \| ([^|]*) \| (.*)$", text, re.M):
            hit = next((i for i in ISSUES if re.search(i[0], desc, re.I)), None)
            name = hit[1] if hit else "Other / unclear"
            if hit and hit[0] == r"vpn" and os.environ.get("FAKE_MODEL_LEAKY_LABEL"):      # 1.17.0: a name ACT should never give
                name = "VPN drops for Dana Whitfield at 198.51.100.9 on wks-1001"
            items.append({"id": d, "issue": name})
            if hit and name not in known and name not in [x["name"] for x in new]:
                new.append({"name": name, "about": "Users report: %s." % name.lower(), "kind": hit[2], "self_service": hit[3]})
        return {"new_issues": new, "items": items}
    if "write the fix action" in text:
        out = []
        for iid, name, count in re.findall(r"^ISSUE (I\d+): (.+?) - (\d+) incidents", text, re.M):
            ph = re.search(r"\b(HOST|PERSON)-\d+\b", text)
            sug = "Printer" in name
            block = text.split("ISSUE %s:" % iid, 1)[1].split("\nISSUE ", 1)[0]
            wn = "WORK NOTES" in block and "restarted the spooler" in block.lower()
            out.append({"id": iid, "steps": ["Check the %s first." % name.split(" /")[0].lower(), "Apply the usual fix and confirm with the user."],
                        "tickets": 23 if wn else 0, "workaround": "Restarting the Print Spooler, again and again (the work notes)." if wn else None,
                        "support": [0, 0] if sug else [9, 4], "source": "suggested" if sug else "tickets",
                        "about": "Users report: %s." % name.lower(),
                        "why": "From the close notes; most were on %s." % (ph.group(0) if ph else "one host"), "disagree": "VPN" in name,
                        "kb": None if sug or int(count) < 50 else {"title": "Fix: %s" % name, "symptoms": "The user sees %s." % name.lower(),
                                                                  "cause": "The usual cause.", "steps": ["Step one.", "Step two."]}})
        return {"items": out}
    if "Write what each scope" in text:
        return {"items": [{"id": s, "lines": ["Scope %s: the model's first point." % s, "Scope %s: the model's second point." % s]}
                          for s in re.findall(r"^SCOPE (\w+):", text, re.M)]}
    return {"items": []}


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8", "replace")
        try:
            msgs = json.loads(body).get("messages") or []
        except ValueError:
            msgs = []
        text = "\n".join(str(m.get("content")) for m in msgs)
        if os.environ.get("FAKE_MODEL_LOG"):
            with open(os.environ["FAKE_MODEL_LOG"], "a") as fh:
                fh.write(text + "\n=====\n")
        content = "Here is the analysis.\n\nBEGIN_ACT_ANALYSIS\n%s\nEND_ACT_ANALYSIS\n" % json.dumps(answer(text), indent=1)
        out = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Model).serve_forever()
