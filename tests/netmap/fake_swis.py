#!/usr/bin/env python3
"""A fake SolarWinds SWIS REST API for the network map's tests (HTTP or HTTPS, standard library only).

  fake_swis.py PORT FIXTURE.json LOG [CERT KEY]

FIXTURE: tests/netmap/fixture.py. Like SWIS: POST /SolarWinds/InformationService/v3/Json/Query with {"query", "parameters"},
HTTP Basic login (401 with {"Message"} when wrong), the answer {"results": [...]}; a SWQL error is HTTP 400 with
{"Message": ...}. It understands the SELECTs site_swis.py sends: Metadata.Entity, Metadata.Property, Orion.InstalledModule, Orion.Engines,
Orion.Nodes N (named columns, N.CustomProperties.X, N.WorldMapPoint.Latitude/Longitude), Orion.TopologyConnections,
Orion.TopologyData, Orion.NPM.Interfaces WHERE InterfaceID IN @ids; TOP n; ORDER BY ... WITH ROWS a TO b. A custom
property the fixture has not: 400, "Cannot resolve property". Every request is a line in LOG: "POST <path> <entity>
<columns asked>"; anything else (another method or path) is logged as it came - the tests check only Query came, and
that no secret column (Community, RWCommunity, LicenseKey) or SELECT * was ever asked for.
  FAKE_SWIS_HIDE=Orion.TopologyConnections,Orion.WorldMap.Point  entities this Orion does not have
  FAKE_SWIS_EMPTY=Metadata.Property  entities that answer, with no rows (1.96.2)
  FAKE_SWIS_STATE=file.json  read on every query: {"down": [NodeID], "up": [NodeID], "ifdown": [InterfaceID]} (live changes),
                             "break": [entity] (its queries: HTTP 400), "huge": [entity] (an answer of 2 MB of padding)
  FAKE_SWIS_ECHO=1           every error's Message repeats what was sent: the Authorization header and the password (the
                             reader must never pass it on)
  FAKE_SWIS_DELAY=seconds    each answer that late; FAKE_SWIS_FAIL=file: while it exists, every query is HTTP 503
1.96.0 (the SolarWinds snapshot, tests/solarwinds/fixture_more.py): a fixture with "tables" ({entity: rows}) answers those
entities too - named columns only (another: 400), WHERE <col> IN @p, <col> <> n, <col> >= @p, <col> > @p, (a OR b) of those,
joined by AND; SELECT <key>, COUNT(<col>) AS <name> FROM <entity> [WHERE ...] GROUP BY <key>; SELECT MAX(<col>) AS <name>
FROM <entity>; 1.97.0: ORDER BY <col> DESC (Log Analyzer's Orion.OLM.LogEntry, newest first, with TOP n);
Metadata.Property (EntityName IN @names) lists each entity's columns, a navigation property among them, and
Orion.NodesCustomProperties' system columns and the fixture's properties. Orion.Nodes' columns may come without the alias."""
import base64
import json
import os
import re
import ssl
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port, fx, log = int(sys.argv[1]), json.load(open(sys.argv[2])), sys.argv[3]
cert = sys.argv[4:6] if len(sys.argv) > 5 else None
HIDE = set(x for x in os.environ.get("FAKE_SWIS_HIDE", "").split(",") if x)
EMPTY = set(x for x in os.environ.get("FAKE_SWIS_EMPTY", "").split(",") if x)
STATE, DELAY, FAIL = os.environ.get("FAKE_SWIS_STATE", ""), float(os.environ.get("FAKE_SWIS_DELAY", "0")), os.environ.get("FAKE_SWIS_FAIL", "")
PATH = "/SolarWinds/InformationService/v3/Json/Query"
ENT = {"Orion.Nodes", "Orion.NPM.Interfaces", "Orion.TopologyConnections", "Orion.TopologyData", "Orion.WorldMap.Point",
       "Orion.InstalledModule", "Orion.Engines", "Metadata.Entity", "Metadata.Property"}
MORE = fx.get("tables") or {}
ENT |= set(MORE) | ({"Orion.NodesCustomProperties"} if MORE else set())
SECRET = re.compile(r"\b(Community|RWCommunity|LicenseKey)\b|\*")
SQ = re.compile(r"^SELECT (?:TOP (\d+) )?(.*?) FROM (\S+)(?: (\w+))?(?: WHERE (.*?))?(?: ORDER BY (.*?))?(?: WITH ROWS (\d+) TO (\d+))?$", re.S)


class Bad(Exception):
    pass


def state():
    if STATE and os.path.exists(STATE):
        try:
            return json.load(open(STATE))
        except ValueError:
            return {}
    return {}


def node_rows(cols, alias):
    st = state()
    down, up = set(st.get("down") or []), set(st.get("up") or [])
    pts = dict((p["NodeID"], p) for p in fx["points"])
    out = []
    for n in sorted(fx["nodes"], key=lambda x: x["NodeID"]):
        r = {}
        for c in cols:
            m = re.match(r"^(?:%s\.)?(\w+)(?:\.(\w+))?(?: AS (\w+))?$" % alias, c.strip())
            if not m:
                raise Bad("Cannot parse column '%s'" % c)
            a, b, as_ = m.groups()
            if a == "CustomProperties":
                if b not in fx["properties"]:
                    raise Bad("Cannot resolve property %s of Orion.NodesCustomProperties" % b)
                r[as_ or b] = n["cp"].get(b)
            elif a == "WorldMapPoint":
                if "Orion.WorldMap.Point" in HIDE:
                    raise Bad("Cannot resolve property WorldMapPoint")
                p = pts.get(n["NodeID"])
                r[as_ or b] = p.get(b) if p else None
            elif a in n:
                v = n[a]
                if a == "Status":
                    v = 2 if n["NodeID"] in down else 1 if n["NodeID"] in up else v
                r[as_ or a] = v
            else:
                raise Bad("Cannot resolve property %s" % a)
        out.append(r)
    return out


def iface_rows(cols, where, params):
    st = state()
    ifd = set(st.get("ifdown") or [])
    m = re.match(r"^(InterfaceID|NodeID) IN @(\w+)$", where or "")     # 1.101.0: a node's interfaces too (the AD report)
    if not m:
        raise Bad("this fake knows only WHERE InterfaceID IN @ids or NodeID IN @ids")
    col, ids = m.group(1), set(params.get(m.group(2)) or [])
    out = []
    for i in fx["interfaces"]:
        if i.get(col) in ids:
            r = dict((c, i.get(c)) for c in cols)
            if i["InterfaceID"] in ifd:
                r.update(OperStatus=2, Status=2, InPercentUtil=0, OutPercentUtil=0)
            out.append(r)
    return out


def columns(ent):
    """An entity's columns, as Metadata.Property lists them."""
    if ent == "Orion.Nodes":
        return sorted(set(k for n in fx["nodes"] for k in n if k != "cp"))
    if ent == "Orion.NPM.Interfaces":
        return sorted(set(k for i in fx["interfaces"] for k in i))
    if ent == "Orion.NodesCustomProperties":
        return ["NodeID", "InstanceType", "Uri", "DisplayName", "Description"] + list(fx["properties"])
    return sorted(set(k for r in MORE.get(ent) or [] for k in r))


def where_ok(where, params):
    """A test for one row of a generic table: the WHERE forms the snapshot sends; 1.96.2: (a OR b) of those."""
    tests = []
    for part in re.split(r" AND ", where or ""):
        part = part.strip()
        if not part:
            continue
        if part.startswith("(") and part.endswith(")") and " OR " in part:
            alts = [where_ok(x.strip(), params) for x in part[1:-1].split(" OR ")]
            tests.append(lambda r, a=alts: any(f(r) for f in a))
            continue
        m = re.match(r"^(\w+) IN @(\w+)$", part)
        if m:
            vals = set(params.get(m.group(2)) or [])
            tests.append(lambda r, c=m.group(1), v=vals: r.get(c) in v)
            continue
        m = re.match(r"^(\w+) <> (\d+)$", part)
        if m:
            tests.append(lambda r, c=m.group(1), v=int(m.group(2)): r.get(c) != v)
            continue
        m = re.match(r"^(\w+) (>=|>) @(\w+)$", part)
        if m:
            v = params.get(m.group(3))
            if m.group(2) == ">=":
                tests.append(lambda r, c=m.group(1), v=v: r.get(c) is not None and r.get(c) >= v)
            else:
                tests.append(lambda r, c=m.group(1), v=v: r.get(c) is not None and r.get(c) > v)
            continue
        raise Bad("this fake does not know WHERE %s" % part)
    return lambda r: all(t(r) for t in tests)


def run(q, params):
    q = re.sub(r"\s+", " ", q.strip())
    g = re.match(r"^SELECT (\w+), COUNT\((\w+)\) AS (\w+) FROM (\S+)(?: WHERE (.*?))? GROUP BY (\w+)$", q)
    if g:                                       # 1.96.0: a count per key (SAM's components per application); 1.96.1: WHERE
        key, col, name, ent, where, by = g.groups()
        if key != by or ent not in MORE or ent in HIDE:
            raise Bad("Source entity [%s] not found in catalog" % ent)
        ok, n = where_ok(where, params), {}
        for r in MORE[ent]:
            if r.get(col) is not None and ok(r):
                n[r.get(key)] = n.get(r.get(key), 0) + 1
        return [{key: k, name: v} for k, v in sorted(n.items())]
    x = re.match(r"^SELECT MAX\((\w+)\) AS (\w+) FROM (\S+)$", q)
    if x:                                       # 1.96.2: Orion's newest EventID (did its EventIDs start again?)
        col, name, ent = x.groups()
        if ent not in MORE or ent in HIDE:
            raise Bad("Source entity [%s] not found in catalog" % ent)
        vals = [r.get(col) for r in MORE[ent] if r.get(col) is not None]
        return [{name: max(vals) if vals else None}]
    m = SQ.match(q)
    if not m:
        raise Bad("Incorrect syntax near '%s'" % q[:40])
    top, colstr, ent, alias, where, order, r0, r1 = m.groups()
    if ent not in ENT or ent in HIDE:
        raise Bad("Source entity [%s] not found in catalog" % ent)
    if ent in EMPTY:
        return []
    cols = [c.strip() for c in colstr.split(",")]
    if ent == "Metadata.Entity":
        names = params.get("names") or []
        rows = [{"FullName": n} for n in names if n in ENT and n not in HIDE]
    elif ent == "Metadata.Property" and "names" not in params:   # 1.95.0: the node custom properties' names (the discovery)
        if params.get("e") != "Orion.NodesCustomProperties" or cols != ["Name", "Type"]:
            raise Bad("Metadata.Property: an EntityName and Name, Type expected")
        rows = [{"Name": p, "Type": "System.String"} for p in fx["properties"]] + [{"Name": "Node", "Type": "Orion.Nodes"},
                                                                                     {"Name": "NodeID", "Type": "System.Int32"}]
    elif ent == "Orion.InstalledModule":
        rows = [dict((c, x.get(c)) for c in cols) for x in fx["modules"]]
    elif ent == "Orion.Engines":
        rows = [dict((c, x.get(c)) for c in cols) for x in fx["engines"]]
    elif ent == "Orion.Nodes":
        rows = node_rows(cols, alias or "Nodes")
    elif ent in ("Orion.TopologyConnections", "Orion.TopologyData"):
        for c in cols:
            if c not in ("SrcNodeID", "SrcInterfaceID", "DestNodeID", "DestInterfaceID") + (("LayerType",) if ent.endswith("Connections") else ()):
                raise Bad("Cannot resolve property %s" % c)
        rows = [dict((c, x.get(c)) for c in cols) for x in fx["connections"]]
    elif ent == "Orion.NPM.Interfaces":
        rows = iface_rows(cols, where, params)
    elif ent == "Metadata.Property":           # 1.96.0: the snapshot asks which columns each entity has (EntityName IN @names)
        names = [n for n in params.get("names") or [] if n in ENT and n not in HIDE]
        rows = []
        for n in names:
            rows.extend({"EntityName": n, "Name": c, "IsNavigable": False} for c in columns(n))
            rows.append({"EntityName": n, "Name": "Engine", "IsNavigable": True})     # a link to another entity: never a column
        rows = [dict((c, r.get(c)) for c in cols) for r in rows]
    elif ent in MORE:
        have = set(columns(ent))
        for c in cols:
            if c not in have:
                raise Bad("Cannot resolve property %s" % c)
        ok = where_ok(where, params)
        src = [r for r in MORE[ent] if ok(r)]
        first = (order or "").split(",")[0].strip().split(" ")
        key = first[0]
        if key:                                 # 1.96.1: ORDER BY its first column (the snapshot's keyset paging); 1.97.0: DESC
            src = sorted(src, key=lambda r: (r.get(key) is None, r.get(key)))
            if len(first) > 1 and first[1].upper() == "DESC":
                src = [r for r in src if r.get(key) is not None][::-1] + [r for r in src if r.get(key) is None]
        rows = [dict((c, r.get(c)) for c in cols) for r in src]
    else:
        rows = []
    if top:
        rows = rows[:int(top)]
    if r0:
        rows = rows[int(r0) - 1:int(r1)]
    return rows


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def note(self, line):
        with open(log, "a") as fh:
            fh.write(line + "\n")

    def other(self):
        self.note("%s %s" % (self.command, self.path))
        self.reply(405 if self.path.startswith("/SolarWinds") else 404, {"Message": "not here"})

    do_GET = do_PUT = do_DELETE = do_PATCH = other

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path != PATH:
            return self.other()
        want = "Basic " + base64.b64encode(("%s:%s" % (fx["user"], fx["password"])).encode()).decode()
        try:
            req = json.loads(body.decode())
        except ValueError:
            req = {}
        q = str(req.get("query") or "")
        m = SQ.match(re.sub(r"\s+", " ", q.strip()))
        self.note("POST %s %s %s" % (self.path, m.group(3) if m else "?", (m.group(2) if m else q)[:400]))
        echo = (" Authorization: %s password=%s" % (self.headers.get("Authorization"), fx["password"])) if os.environ.get("FAKE_SWIS_ECHO") else ""
        if self.headers.get("Authorization") != want:
            return self.reply(401, {"Message": "Access denied." + echo})
        st = state()
        if m and m.group(3) in (st.get("break") or []):
            return self.reply(400, {"Message": "Query failed." + echo})
        if m and m.group(3) in (st.get("huge") or []):
            return self.reply(200, {"results": [{"pad": "x" * 1024}] * 2048})
        if FAIL and os.path.exists(FAIL):
            return self.reply(503, {"Message": "Service unavailable"})
        if SECRET.search(m.group(2) if m else q):
            self.note("SECRET-COLUMN-ASKED")
        if DELAY:
            time.sleep(DELAY)
        try:
            return self.reply(200, {"results": run(q, req.get("parameters") or {})})
        except Bad as e:
            return self.reply(400, {"Message": str(e) + echo, "ExceptionType": "SolarWinds.Data.Query.ParserException"})


srv = ThreadingHTTPServer(("127.0.0.1", port), H)
if cert:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert[0], cert[1])
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
srv.serve_forever()
