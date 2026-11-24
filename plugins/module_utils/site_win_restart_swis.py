# -*- coding: utf-8 -*-
"""Windows - schedule a restart / cancel a scheduled restart: the SolarWinds node of each server unmanaged for a window
around the restart, and SolarWinds manages it again on its own at the window's end (roles/win_restart; docs/WINDOWS.md
"Schedule a restart", SolarWinds).

The two calls, SWIS Invoke on Orion.Nodes (POST /SolarWinds/InformationService/v3/Json/Invoke/Orion.Nodes/<verb>, the
body a JSON array of the verb's arguments):
  Unmanage  ["N:<NodeID>", <from, UTC>, <until, UTC>, false]   - isRelative false: the two times are the window itself
  Remanage  ["N:<NodeID>"]
The argument order and UTC times as SolarWinds' own OrionSDK sample sends them (github.com/solarwinds/OrionSDK,
Samples/Go/UnmanageNode/main.go: "N:%d", nowUTC, laterUTC, false); Orion.Nodes' verbs Unmanage and Remanage and its
properties UnManaged (Boolean), UnManageFrom and UnManageUntil (DateTime) in the SWIS schema
(solarwinds.github.io/OrionSDK/schema/Orion.Nodes.html). Times are sent as YYYY-MM-DDTHH:MM:SSZ.

Read only otherwise: the nodes are found with one SWQL SELECT per server (Caption, DNS, IPAddress) and matched by name
only (plugins/module_utils/site_host_swis.py match without its IP stage: the full name, then a short name that does not
clash) - not found, more than one, or only an IP: never unmanaged.

Ours: only a window this job wrote. After its Unmanage the node is read back and what SWIS returned (both ends, as SWIS
gives them) is recorded with the registration id and the Orion server (restart-<id>.solarwinds.json beside the task's
settings). Ours later = the same node on the same server with both ends exactly that. Anything else is left as it is.
Writes take the "SolarWinds (manage nodes)" credential's own variables (SOLARWINDS_MANAGE_*), never the read-only
credential's SOLARWINDS_*. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import json
import re

try:
    from ansible.module_utils import site_swis as sw
    from ansible.module_utils import site_host_swis as hsw
except ImportError:                                      # the tests: plugins/module_utils on sys.path
    import site_swis as sw
    import site_host_swis as hsw

try:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request
except ImportError:                                      # Python 2: never run there
    HTTPError = URLError = Exception
    Request = None

U = datetime.timezone.utc
INVOKE_PATH = "/SolarWinds/InformationService/v3/Json/Invoke/Orion.Nodes/"
VERBS = ("Unmanage", "Remanage")
SAME_SECONDS = 90                                        # a window read back within this of ours is ours
FIELDS = "NodeID, Caption, DNS, IPAddress, UnManaged, UnManageFrom, UnManageUntil"


def stamp(dt):
    return dt.astimezone(U).strftime("%Y-%m-%dT%H:%M:%SZ")


ZONED = re.compile(r"(Z|[+-]\d\d:?\d\d)$")


def parse(text, tz=None):
    """A SWIS or our own time ('2026-11-24T20:55:00', '...Z', '...+01:00', '...0000000Z') -> an aware UTC datetime, or None.
    One with Z or an offset: exactly that. One without (naive): in tz when tz is given, else as UTC."""
    t = str(text or "").strip()
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?$", t)
    if not m:
        return None
    try:
        dt = datetime.datetime(*[int(x or 0) for x in m.groups()[:6]])
    except ValueError:
        return None
    z = m.group(7)
    if z and z != "Z":
        sign = 1 if z[0] == "+" else -1
        hh, mm = int(z[1:3]), int(z[-2:])
        return (dt - sign * datetime.timedelta(hours=hh, minutes=mm)).replace(tzinfo=U)
    if z == "Z" or tz is None:
        return dt.replace(tzinfo=U)
    return dt.replace(tzinfo=tz).astimezone(U)


def naive(text):
    """A time without Z or an offset (SWIS may return Orion's UnManageFrom / Until so)."""
    return bool(str(text or "").strip()) and not ZONED.search(str(text).strip())


def window(at_utc, before=5, after=10):
    """The restart moment -> {from, until} (UTC stamps): before minutes earlier to after minutes later."""
    at = parse(at_utc)
    if at is None:
        return {}
    return {"from": stamp(at - datetime.timedelta(minutes=int(before))), "until": stamp(at + datetime.timedelta(minutes=int(after)))}


def same(a, b, tz=None):
    """Is SolarWinds' window a the one we asked for, b (our UTC stamps)? -> '' (no), 'utc' or 'local' (the reading that
    matched, within SAME_SECONDS for both ends). A time with Z or an offset is taken exactly; a naive one is tried as UTC,
    then as the site's zone tz (what SWIS returns is not documented - either is accepted, and the report says which)."""
    try:
        ours = [parse(b[k]) for k in ("from", "until")]
        if None in ours:
            return ""
        readings = ["utc"] + (["local"] if tz is not None and any(naive(a.get(k)) for k in ("from", "until")) else [])
        for how in readings:
            got = [parse(a[k], tz if how == "local" else None) for k in ("from", "until")]
            if None not in got and all(abs((g - o).total_seconds()) <= SAME_SECONDS for g, o in zip(got, ours)):
                return how
    except (TypeError, KeyError):
        pass
    return ""


def shown(win, tz=None):
    """A window for people: '22:25-22:40 CET' in the site's zone (a date when it is not the same day)."""
    f, u = parse(win.get("from")), parse(win.get("until"))
    if f is None or u is None:
        return "?"
    if tz is not None:
        f, u = f.astimezone(tz), u.astimezone(tz)
    end = u.strftime("%H:%M") if u.date() == f.date() else u.strftime("%Y-%m-%d %H:%M")
    return ("%s-%s %s" % (f.strftime("%H:%M"), end, f.strftime("%Z"))).strip()


def shown_raw(win):
    """SolarWinds' own window as it gave it (its zone is not known when it carries none)."""
    return "%s to %s" % (str(win.get("from") or "?")[:16].replace("T", " "), str(win.get("until") or "?")[:16].replace("T", " "))


def node_window(node):
    """The node's own unmanage window as SWIS gave it, or {} when it has none."""
    if parse(node.get("until")) is None:
        return {}
    return {"from": str(node.get("from") or ""), "until": str(node.get("until") or "")}


def active(node, now, tz=None):
    """The node is unmanaged now or has a window still ahead (a naive end: ahead read either way - never taken as over
    when one reading says it is not)."""
    w = node_window(node)
    if node.get("unmanaged"):
        return True
    if not w:
        return False
    ends = [parse(w["until"])] + ([parse(w["until"], tz)] if tz is not None and naive(w["until"]) else [])
    return any(e is not None and e > now for e in ends)


class Manager(sw.Client):
    """site_swis's client, and the two Orion.Nodes verbs this job needs - nothing else is ever invoked."""

    def invoke(self, verb, args):
        if verb not in VERBS:
            raise sw.SwisError("refused: only Orion.Nodes Unmanage and Remanage are ever invoked", "query")
        body = json.dumps(list(args)).encode("utf-8")
        req = Request(self.url + INVOKE_PATH + verb, data=body, headers={"Content-Type": "application/json", "Accept": "application/json",
                                                                         "Authorization": self._auth})
        self.calls += 1
        try:
            r = self.opener.open(req, timeout=self.timeout)
            r.read(1024 * 1024)
        except HTTPError as e:
            e.close()
            if e.code == 401:
                raise sw.SwisError("SolarWinds refused the login (HTTP 401)", "auth")
            if e.code == 403:
                raise sw.SwisError("SolarWinds refused it (HTTP 403): the account may not manage nodes", "auth")
            raise sw.SwisError("SolarWinds could not %s the node (HTTP %d)" % (verb.lower(), e.code), "http")
        except (URLError, OSError) as e:
            kind = sw.kind_of(e)
            words = getattr(sw, "LOCAL")                 # site_swis's own words for a failed connection
            raise sw.SwisError(words.get(kind, "the connection failed (%s)" % e.__class__.__name__), kind)
        return True


def _node(row):
    return {"node_id": int(row.get("NodeID")), "name": str(row.get("Caption") or ""), "dns": str(row.get("DNS") or ""),
            "ip": str(row.get("IPAddress") or ""), "other_ips": [], "unmanaged": bool(row.get("UnManaged")),
            "from": str(row.get("UnManageFrom") or ""), "until": str(row.get("UnManageUntil") or "")}


def find(client, hosts):
    """hosts: [{host, fqdn, ip}] -> {host: {node: {...} or None, why}}. For a write, by NAME only: the full name, else a short
    name no other node or server shares (site_host_swis.match without its IP stage) - an IP is never enough (a cluster
    address can be secondary on the target and primary on another node). Only an IP matched: said, never unmanaged."""
    hs = hsw._hosts(hosts)
    nodes = {}
    by_ip = {}
    for h in hs:
        full, short, ip = h["fqdn"], h["short"], h["ip"]
        q = ("SELECT %s FROM Orion.Nodes WHERE Caption = @short OR DNS = @short OR Caption LIKE @shortdot OR DNS LIKE @shortdot"
             % FIELDS)
        params = {"short": short, "shortdot": short + ".%"}
        if full:
            q += " OR Caption = @full OR DNS = @full"
            params["full"] = full
        if ip:
            q += " OR IPAddress = @ip"
            params["ip"] = ip
        for row in client.query(q, params):
            n = _node(row)
            nodes[n["node_id"]] = n
            if ip and hsw._ip(n["ip"]) == hsw._ip(ip):
                by_ip.setdefault(h["host"], []).append(n)
    got = hsw.match(hs, list(nodes.values()), allow_ip=False)
    out = {}
    for i, h in enumerate(hs):
        node, how, why = got.get(i, (None, "", ""))
        if node is None and not why and by_ip.get(h["host"]):
            why = "matched by IP only (%s) - not unmanaged" % ", ".join("%s N:%d" % (n["name"], n["node_id"]) for n in by_ip[h["host"]])
        out[h["host"]] = {"node": node, "how": how, "why": why or ("no SolarWinds node named %s" % h["short"])}
    return out


def mine(node, record, orion):
    """Is the node's window the one this job wrote and read back (the record: node_id, orion, rid, from, until - SWIS's own
    values, as it returned them)? Both ends equal exactly, on the same Orion server; nothing else is ours."""
    if not record or not record.get("rid") or int(record.get("node_id") or 0) != int(node.get("node_id") or -1):
        return False
    if sw.base_url(str(record.get("orion") or "x")) != orion:
        return False
    return str(node.get("from") or "") == str(record.get("from") or "") and str(node.get("until") or "") == str(record.get("until") or "")


def plan(node, win, earlier, now, check, tz=None, why="", orion=""):
    """One server whose restart is scheduled: what is done to its node -> {op: unmanage | replace | none, text, level}.
    earlier: the record of the replaced registration (what our Unmanage wrote and read back) or {}."""
    if node is None:
        return {"op": "none", "level": "warning", "text": "SolarWinds: node not found (%s) - not unmanaged" % (why or "no match")
                if "matched by IP only" not in (why or "") else "SolarWinds: %s" % why}
    tag = "node %s (N:%d)" % (node["name"], node["node_id"])
    verb = "would be unmanaged" if check else "unmanaged"
    if active(node, now, tz):
        if not mine(node, earlier, orion):
            return {"op": "none", "level": "warning",
                    "text": "SolarWinds: %s is unmanaged by someone else (%s) - left as it is, not unmanaged by this job" % (tag, shown_raw(node_window(node)))}
        return {"op": "replace", "level": "ok",
                "text": "SolarWinds: %s %s %s (our earlier window %s replaced)" % (tag, verb, shown(win, tz), shown_raw(node_window(node)))}
    return {"op": "unmanage", "level": "ok", "text": "SolarWinds: %s %s %s" % (tag, verb, shown(win, tz))}


def plan_remanage(node, earlier, now, check, tz=None, orion=""):
    """A restart of ours taken away (cancel, or a skip): its node managed again only when its window is still exactly the
    one we wrote."""
    if not earlier or not earlier.get("node_id"):
        return {"op": "none", "level": "info", "text": ""}
    if node is None:
        return {"op": "none", "level": "warning", "text": "SolarWinds: node N:%s is not there any more - nothing to manage again" % earlier["node_id"]}
    tag = "node %s (N:%d)" % (node["name"], node["node_id"])
    if not active(node, now, tz):
        return {"op": "none", "level": "info", "text": "SolarWinds: %s is managed - nothing to do" % tag}
    if not mine(node, earlier, orion):
        return {"op": "none", "level": "warning",
                "text": "SolarWinds: %s has an unmanage window (%s) that is not the one this job wrote - left as it is" % (tag, shown_raw(node_window(node)))}
    return {"op": "remanage", "level": "ok", "text": "SolarWinds: %s %s" % (tag, "would be managed again" if check else "managed again")}


def by_id(client, nid):
    """One node, read now, or None."""
    for row in client.query("SELECT %s FROM Orion.Nodes WHERE NodeID = @id" % FIELDS, {"id": int(nid)}):
        return _node(row)
    return None


def run(client, items, now, check, tz=None):
    """items: [{host, op: schedule | remove, node_id, rid, why, window, earlier}] -> {host: {text, level, done, record, times}}.
    Each node is read right before its own call (never from a snapshot taken for all of them). An Unmanage is read back:
    what SWIS returned is the record that makes the window ours (with the registration id and the Orion server); SWIS's
    time convention (UTC or local) is told from that first read-back only. Each call's failure is that server's warning."""
    orion = sw.base_url(client.url)
    out = {}
    for it in items:
        res = {"text": "", "level": "info", "done": False, "record": {}, "times": ""}
        try:
            if it["op"] == "schedule":
                n = by_id(client, it["node_id"]) if it.get("node_id") else None
                p = plan(n, it["window"], it.get("earlier") or {}, now, check, tz, it.get("why", ""), orion)
                res.update(text=p["text"], level=p["level"])
                if not check and p["op"] in ("replace", "unmanage"):
                    if p["op"] == "replace":
                        n2 = by_id(client, n["node_id"])           # again, right before: changed meanwhile = not ours any more
                        if n2 is None or not mine(n2, it.get("earlier") or {}, orion):
                            raise sw.SwisError("its window changed while this job ran - left as it is", "changed")
                        client.invoke("Remanage", ["N:%d" % n["node_id"]])
                    client.invoke("Unmanage", ["N:%d" % n["node_id"], it["window"]["from"], it["window"]["until"], False])
                    back = by_id(client, n["node_id"]) or {}
                    how = same({"from": back.get("from"), "until": back.get("until")}, it["window"], tz)
                    if not how:
                        res.update(level="warning", text=res["text"] + " - but SolarWinds shows another window after it (%s): not recorded as ours"
                                   % shown_raw(back))
                    else:
                        res.update(done=True, times=how, record={"node_id": n["node_id"], "orion": orion, "rid": str(it.get("rid") or ""),
                                                                 "from": back["from"], "until": back["until"]})
            else:
                e = it.get("earlier") or {}
                n = by_id(client, e["node_id"]) if e.get("node_id") else None      # read right before its own call
                p = plan_remanage(n, e, now, check, tz, orion)
                res.update(text=p["text"], level=p["level"])
                if not check and p["op"] == "remanage":
                    client.invoke("Remanage", ["N:%d" % n["node_id"]])
                    res["done"] = True
        except sw.SwisError as e:
            res.update(level="warning", done=False, record={}, text="SolarWinds: %s failed (%s) - the restart stays as it is" % (
                "unmanage" if it["op"] == "schedule" else "manage again", e))
        out[it["host"]] = res
    return out
