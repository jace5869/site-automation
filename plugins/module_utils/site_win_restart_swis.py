# -*- coding: utf-8 -*-
"""Windows - schedule a restart: the SolarWinds node of each server unmanaged for a window around the restart; SolarWinds
manages it again on its own at the window's end (roles/win_restart; docs/WINDOWS.md "Schedule a restart", SolarWinds).

One write, never another: SWIS Invoke Orion.Nodes Unmanage (POST /SolarWinds/InformationService/v3/Json/Invoke/
Orion.Nodes/Unmanage, the body a JSON array) with ["N:<NodeID>", <from, UTC>, <until, UTC>, false] - isRelative false: the
two times are the window itself. The argument order and UTC times as SolarWinds' own OrionSDK sample sends them
(github.com/solarwinds/OrionSDK, Samples/Go/UnmanageNode/main.go: "N:%d", nowUTC, laterUTC, false); Orion.Nodes' verb
Unmanage and its properties UnManaged (Boolean), UnManageFrom and UnManageUntil (DateTime) in the SWIS schema
(solarwinds.github.io/OrionSDK/schema/Orion.Nodes.html). Times are sent as YYYY-MM-DDTHH:MM:SSZ. Remanage is never called:
a window ends by itself (a reschedule or a cancel leaves the earlier one to expire).

Only a node with no unmanage window at all is unmanaged (UnManaged false, UnManageUntil empty or past); any window -
the job's own from an earlier run, or a person's - is left as it is. The node is matched by name only: a full name, else a
short name exactly one node has (an ambiguous name is refused before anything else; an IP never decides); right before
the write the node is read again by its ID and its name must still be the one matched. After the write one read-back
says the window and whether SWIS returns times as UTC or local time (reported only).
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
VERBS = ("Unmanage",)                                    # the one verb ever invoked
SAME_SECONDS = 90                                        # the read-back: our window, within this (to tell UTC from local)

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
    """The read-back after our Unmanage: is SolarWinds' window a the one we sent, b? -> '' (no), 'utc' or 'local' (the reading
    that matched, within SAME_SECONDS for both ends) - to report SWIS's time convention, never to decide anything. A time
    with Z or an offset is taken exactly; a naive one is tried as UTC, then as the site's zone tz."""
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
    if not str(node.get("until") or "").strip():
        return {}
    return {"from": str(node.get("from") or ""), "until": str(node.get("until") or "")}


def ends(text, tz=None):
    """Every moment an end could be: one with Z or an offset exactly; a naive one as UTC and in the site's zone tz, both
    folds when the clocks go back (01:45 twice on that night)."""
    out = [parse(text)]
    if tz is not None and naive(text):
        loc = parse(text, None)
        if loc is not None:
            wall = loc.replace(tzinfo=None)
            out += [wall.replace(tzinfo=tz, fold=f).astimezone(U) for f in (0, 1)]
    return [e for e in out if e is not None]


def has_window(node, now, tz=None):
    """The node has an unmanage window now: unmanaged; or an end any reading of which is still ahead (past only when past
    every way); or an end that cannot be read at all (a window is there - it is left)."""
    if node.get("unmanaged"):
        return True
    until = str(node.get("until") or "").strip()
    if not until:
        return False
    got = ends(until, tz)
    if not got:
        return True
    return any(e > now for e in got)


class Manager(sw.Client):
    """site_swis's client, and the one Orion.Nodes verb this job needs - nothing else is ever invoked."""

    def invoke(self, verb, args):
        if verb not in VERBS:
            raise sw.SwisError("refused: only Orion.Nodes Unmanage is ever invoked", "query")
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


def _canon(x):
    return hsw.canon(x)


def _node(row):
    return {"node_id": int(row.get("NodeID")), "name": str(row.get("Caption") or ""), "dns": str(row.get("DNS") or ""),
            "ip": str(row.get("IPAddress") or ""), "unmanaged": bool(row.get("UnManaged")),
            "from": str(row.get("UnManageFrom") or ""), "until": str(row.get("UnManageUntil") or "")}


def _names(n):
    return [x for x in (_canon(n.get("name")), _canon(n.get("dns"))) if x and not hsw._ip(x)]


def find(client, hosts, deadline=None, clock=None):
    """hosts: [{host, fqdn, ip}] -> {host: {node, name, why}}: name the node is matched by (checked again before the write).
    1. Full name (the inventory name, or ansible_host, with a domain): Caption or DNS equal to it. More than one: refused.
    2. Else the short name: the nodes whose Caption or DNS is that name, or starts with it and a dot. More than one:
       refused - before anything else, and never decided by an IP. One: with a domain of its own, it must be the
       server's domain when the server has one (a conflict otherwise); a server without a domain takes it.
    A node two servers of this job match: neither gets it. Only an IP matches: said, never unmanaged."""
    hs = hsw._hosts(hosts)
    cand, late = {}, set()
    for h in hs:
        if deadline is not None and (clock or _clock)() > deadline:
            late.add(h["host"])                          # out of time: not looked up, never unmanaged
            cand[h["host"]] = []
            continue
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
        cand[h["host"]] = [_node(r) for r in client.query(q, params)]
    picked, out = {}, {}
    for h in hs:
        nodes = dict((n["node_id"], n) for n in cand[h["host"]])
        full, short, ip = h["fqdn"], h["short"], hsw._ip(h["ip"])
        node, name, why = None, "", ""
        byfull = [n for n in nodes.values() if full and full in _names(n)]
        if len(byfull) > 1:
            why = "%d SolarWinds nodes are named %s" % (len(byfull), full)
        elif byfull:
            node, name = byfull[0], full
        else:
            byshort = [n for n in nodes.values() if any(x.split(".", 1)[0] == short for x in _names(n))]
            if len(byshort) > 1:
                why = "%d SolarWinds nodes are named %s" % (len(byshort), short)
            elif byshort:
                n = byshort[0]
                doms = [x for x in _names(n) if "." in x]
                if full and doms and full not in doms:
                    why = "a SolarWinds node named %s is %s" % (short, min(doms))
                else:
                    node, name = n, next(x for x in _names(n) if x.split(".", 1)[0] == short)
            elif ip and any(hsw._ip(n["ip"]) == ip for n in nodes.values()):
                why = "matched by IP only (%s) - not unmanaged" % ", ".join(
                    "%s N:%d" % (n["name"], n["node_id"]) for n in nodes.values() if hsw._ip(n["ip"]) == ip)
        if node is not None:
            picked.setdefault(node["node_id"], []).append(h["host"])
        if h["host"] in late:
            node, why = None, "SolarWinds took too long - not looked up"
        out[h["host"]] = {"node": node, "name": name, "fqdn": full, "why": why or ("no SolarWinds node named %s" % short)}
    for nid, owners in picked.items():
        if len(owners) > 1:
            for host in owners:
                out[host] = {"node": None, "name": "", "why": "%d servers of this job could be the SolarWinds node N:%d" % (len(owners), nid)}
    return out


def by_id(client, nid):
    """One node, read now, or None."""
    for row in client.query("SELECT %s FROM Orion.Nodes WHERE NodeID = @id" % FIELDS, {"id": int(nid)}):
        return _node(row)
    return None


def plan(node, win, now, check, tz=None, why="", earlier=None):
    """One server whose restart is scheduled -> {op: unmanage | none, text, level}. earlier: the window of the replaced
    restart of ours ({from, until}, from its settings) - only to say the window there is likely that one."""
    if node is None:
        return {"op": "none", "level": "warning", "text": "SolarWinds: %s" % why if "matched by IP only" in (why or "")
                else "SolarWinds: node not found (%s) - not unmanaged" % (why or "no match")}
    tag = "node %s (N:%d)" % (node["name"], node["node_id"])
    if has_window(node, now, tz):
        likely = " - likely the replaced restart's (it ends by itself)" if earlier and same(node_window(node), earlier, tz) else ""
        return {"op": "none", "level": "warning",
                "text": "SolarWinds: %s already has an unmanage window (%s)%s - left as it is" % (tag, shown_raw(node_window(node)), likely)}
    return {"op": "unmanage", "level": "ok", "text": "SolarWinds: %s %s %s" % (tag, "would be unmanaged" if check else "unmanaged", shown(win, tz))}


def _clock():
    return datetime.datetime.now(U)


def same_node(n, name, fqdn):
    """The node read again just before the write is still the one matched: it carries the matched name, and - when the
    server's full name is known - no domain name of another domain (a DNS name it gained meanwhile)."""
    names = _names(n)
    if name and name not in names and not any(x.split(".", 1)[0] == name for x in names):
        return "node N:%d is not named %s any more" % (n["node_id"], name)
    doms = [x for x in names if "." in x]
    if fqdn and doms and fqdn not in doms:
        return "node N:%d is now named %s, not %s" % (n["node_id"], min(doms), fqdn)
    return ""


def run(client, items, at_utc, check, tz=None, budget=120, min_lead=60, clock=None):
    """items: [{host, ip, fqdn, window, earlier}] - servers whose restart task is registered and verified (or, in Check,
    would be) -> {host: {text, level, done, times}}. Bounded: past budget seconds in all, or with the restart less than
    min_lead seconds away, a server is skipped (a warning). The nodes are found first (by name only); then each one is read
    by its ID right before its own write - its name (and the server's domain) checked again, and it must have no unmanage
    window. After the write one read-back: the window as SolarWinds shows it and its time convention (a failed read-back
    does not undo the write: done, said). A failure is that server's warning."""
    clock = clock or _clock
    start = clock()
    deadline = start + datetime.timedelta(seconds=int(budget))
    at = parse(at_utc)
    found = find(client, [{"host": it["host"], "ip": it.get("ip", ""), "fqdn": it.get("fqdn", "")} for it in items], deadline, clock)
    out = {}
    for it in items:
        res = {"text": "", "level": "info", "done": False, "times": ""}
        f = found.get(it["host"]) or {}
        now = clock()
        if now > deadline:
            res.update(level="warning", text="SolarWinds: took longer than %ds in all - not unmanaged" % int(budget))
            out[it["host"]] = res
            continue
        if at is not None and (at - now).total_seconds() < int(min_lead):
            res.update(level="warning", text="SolarWinds: the restart is less than a minute away - not unmanaged")
            out[it["host"]] = res
            continue
        try:
            n = by_id(client, f["node"]["node_id"]) if f.get("node") else None
            if n is not None:
                why = same_node(n, f.get("name"), f.get("fqdn"))
                if why:
                    raise sw.SwisError(why + " - not unmanaged", "changed")
            p = plan(n, it["window"], now, check, tz, f.get("why", ""), it.get("earlier"))
            res.update(text=p["text"], level=p["level"])
            if not check and p["op"] == "unmanage":
                client.invoke("Unmanage", ["N:%d" % n["node_id"], it["window"]["from"], it["window"]["until"], False])
                res["done"] = True
                try:
                    back = by_id(client, n["node_id"]) or {}
                    how = same({"from": back.get("from"), "until": back.get("until")}, it["window"], tz)
                    res["times"] = how
                    if not how:
                        res.update(level="warning", text=res["text"] + " - but SolarWinds shows %s after it" % shown_raw(back))
                except sw.SwisError as e:
                    res.update(level="warning", text=res["text"] + "; the read-back failed (%s)" % e)
        except sw.SwisError as e:
            res.update(level="warning", done=False, text="SolarWinds: unmanage failed (%s) - the restart stays as it is" % e
                       if getattr(e, "kind", "") != "changed" else "SolarWinds: %s" % e)
        out[it["host"]] = res
    return out
