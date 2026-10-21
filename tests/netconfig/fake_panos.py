#!/usr/bin/env python3
"""A stand-in Palo Alto Networks firewall for tests/netconfig: the XML API (http on 127.0.0.1) as the network
configuration jobs use it - keygen (admin / admin -> key TESTKEY), export of the running configuration, the
candidate (config get / set / edit / delete at an xpath), check pending-changes, validate and commit (jobs),
import of a named configuration, load config from it, revert config. Each request is logged (LOGFILE).
  python3 fake_panos.py PORT LOGFILE [HOSTNAME]
A candidate with an element named "invalid" fails validation (as a reference to a missing object would)."""
import json
import sys
import threading
import xml.etree.ElementTree as ET
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT, LOG = int(sys.argv[1]), sys.argv[2]
HOST = sys.argv[3] if len(sys.argv) > 3 else "fw01"
RUNNING = """<config version="10.2.0" urldb="paloaltonetworks" detail-version="10.2.4">
  <devices>
    <entry name="localhost.localdomain">
      <deviceconfig>
        <system>
          <hostname>%s</hostname>
          <ntp-servers>
            <primary-ntp-server>
              <ntp-server-address>192.0.2.123</ntp-server-address>
            </primary-ntp-server>
          </ntp-servers>
          <login-banner>Authorized use only</login-banner>
        </system>
      </deviceconfig>
    </entry>
  </devices>
  <mgt-config>
    <users>
      <entry name="admin">
        <phash>$5$fakehash$0123456789abcdef</phash>
      </entry>
    </users>
  </mgt-config>
</config>
""" % HOST
LOCK = threading.Lock()
STATE = {"running": RUNNING, "candidate": ET.fromstring(RUNNING), "files": {}, "jobs": {}, "next_job": [1]}


def log(entry):
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def ok(inner=""):
    return '<response status="success">%s</response>' % inner


def err(msg, code="400"):
    return '<response status="error" code="%s"><msg><line>%s</line></msg></response>' % (code, msg)


def text_of(el):
    return ET.tostring(el, encoding="unicode")


def same(a, b):
    """Two configurations alike, whitespace aside."""
    strip = lambda e: [(x.tag, sorted(x.attrib.items()), (x.text or "").strip()) for x in e.iter()]
    return strip(a) == strip(b)


def steps(xpath):
    if not xpath.startswith("/config"):
        raise ValueError("xpath must start with /config")
    out = []
    for s in [x for x in xpath[len("/config"):].split("/") if x]:
        if "[@name=" in s:
            tag, rest = s.split("[@name=", 1)
            out.append((tag, rest.strip("]").strip("'\"")))
        else:
            out.append((s, None))
    return out


def find(root, xpath, create=False):
    node = root
    for tag, name in steps(xpath):
        nxt = None
        for c in node:
            if c.tag == tag and (name is None or c.get("name") == name):
                nxt = c
                break
        if nxt is None:
            if not create:
                return None, None
            nxt = ET.SubElement(node, tag, {"name": name} if name else {})
        node, parent = nxt, node
    return node, (parent if steps(xpath) else None)


def parent_of(root, el):
    for p in root.iter():
        for c in p:
            if c is el:
                return p
    return None


def new_job(kind):
    with LOCK:
        j = str(STATE["next_job"][0])
        STATE["next_job"][0] += 1
    cand = STATE["candidate"]
    if kind == "validate":
        bad = [x for x in cand.iter() if x.tag == "invalid"]
        STATE["jobs"][j] = ("FAIL", ["Validation Error:", "invalid is not a valid reference"]) if bad else ("OK", ["Configuration is valid"])
    else:
        STATE["running"] = text_of(cand)
        STATE["jobs"][j] = ("OK", ["Configuration committed successfully"])
    return j


def pending():
    return not same(STATE["candidate"], ET.fromstring(STATE["running"]))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def answer(self, body, ctype="application/xml", status=200):
        b = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self.handle_api({})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        ctype = self.headers.get("Content-Type") or ""
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + raw)
            part = [x for x in msg.iter_parts() if x.get_param("name", header="content-disposition") == "file"][0]
            self.handle_api({"_file": (part.get_filename(), part.get_payload(decode=True).decode())})
        else:
            self.handle_api({k: v[0] for k, v in parse_qs(raw.decode()).items()})

    def handle_api(self, form):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        q.update({k: v for k, v in form.items() if not k.startswith("_")})
        t = q.get("type")
        key = self.headers.get("X-PAN-KEY") or q.get("key")
        log({"method": self.command, "type": t, "action": q.get("action"), "cmd": q.get("cmd"), "xpath": q.get("xpath"),
             "keyed": bool(key), "user_in_url": "user" in parse_qs(u.query), "file": (form.get("_file") or [None])[0]})
        if u.path != "/api/":
            return self.answer(err("not found", "404"), status=404)
        if t == "keygen":
            if (q.get("user"), q.get("password")) == ("admin", "admin"):
                return self.answer(ok("<result><key>TESTKEY</key></result>"))
            return self.answer(err("Invalid Credential", "403"), status=403)
        if key != "TESTKEY":
            return self.answer(err("Invalid Credential", "403"), status=403)
        if t == "export" and q.get("category") == "configuration":
            return self.answer(STATE["running"])
        if t == "import" and q.get("category") == "configuration":
            name, content = form["_file"]
            STATE["files"][name] = content
            return self.answer(ok("<msg><line>%s saved</line></msg>" % name))
        if t == "config":
            return self.config(q)
        if t == "commit":
            if not pending():
                return self.answer(ok("<msg>There are no changes to commit.</msg>"))
            return self.answer(ok("<result><msg><line>Commit job enqueued with jobid %s</line></msg><job>%s</job></result>" % ((new_job("commit"),) * 2)))
        if t == "op":
            return self.op(q.get("cmd") or "")
        return self.answer(err("unknown request"))

    def config(self, q):
        act, xp = q.get("action"), q.get("xpath") or ""
        cand = STATE["candidate"]
        try:
            if act in ("get", "show"):
                root = cand if act == "get" else ET.fromstring(STATE["running"])
                node, _ = find(root, xp)
                return self.answer(ok("<result>%s</result>" % (text_of(node) if node is not None else "")))
            if act == "set":
                node, _ = find(cand, xp, create=True)
                for c in ET.fromstring("<x>%s</x>" % q["element"]):
                    old = [o for o in node if o.tag == c.tag and o.get("name") == c.get("name")]
                    for o in old:
                        node.remove(o)
                    node.append(c)
                return self.answer(ok("<msg>command succeeded</msg>"))
            if act == "edit":
                node, _ = find(cand, xp, create=True)
                new = ET.fromstring(q["element"])
                p = parent_of(cand, node)
                i = list(p).index(node)
                p.remove(node)
                p.insert(i, new)
                return self.answer(ok("<msg>command succeeded</msg>"))
            if act == "delete":
                node, _ = find(cand, xp)
                if node is not None:
                    parent_of(cand, node).remove(node)
                return self.answer(ok("<msg>command succeeded</msg>"))
        except (ValueError, ET.ParseError, KeyError) as e:
            return self.answer(err(str(e)))
        return self.answer(err("unknown config action"))

    def op(self, cmd):
        if cmd == "<check><pending-changes></pending-changes></check>":
            return self.answer(ok("<result>%s</result>" % ("yes" if pending() else "no")))
        if cmd == "<validate><full></full></validate>":
            j = new_job("validate")
            return self.answer(ok("<result><msg><line>Validate job enqueued with jobid %s</line></msg><job>%s</job></result>" % (j, j)))
        if cmd.startswith("<show><jobs><id>"):
            j = cmd[len("<show><jobs><id>"):].split("<")[0]
            if j not in STATE["jobs"]:
                return self.answer(err("job %s not found" % j))
            res, det = STATE["jobs"][j]
            lines = "".join("<line>%s</line>" % d for d in det)
            return self.answer(ok("<result><job><id>%s</id><status>FIN</status><result>%s</result><details>%s</details></job></result>" % (j, res, lines)))
        if cmd.startswith("<load><config><from>"):
            name = cmd[len("<load><config><from>"):].split("<")[0]
            if name not in STATE["files"]:
                return self.answer(err("%s does not exist" % name))
            STATE["candidate"] = ET.fromstring(STATE["files"][name])
            return self.answer(ok("<msg><line>Config loaded from %s</line></msg>" % name))
        if cmd == "<revert><config></config></revert>":
            STATE["candidate"] = ET.fromstring(STATE["running"])
            return self.answer(ok("<msg>Candidate configuration reverted</msg>"))
        return self.answer(err("unknown op %s" % cmd))


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
