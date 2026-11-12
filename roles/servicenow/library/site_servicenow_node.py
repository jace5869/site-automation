#!/usr/bin/python
# -*- coding: utf-8 -*-
"""A self-hosted ServiceNow host, read on the host itself as root, read-only.
kind node - a front-end (application) node: its node units (snc_<env>.service - running, starting at boot; only those
installed), the install folder's disk and /'s, the memory and swap, the node's localhost log (SEVERE, OutOfMemory and
semaphore lines in the last hours, grouped by message) and the node's own stats page on 127.0.0.1 (xmlstats.do, else
stats.do: JVM memory, semaphores, scheduler workers and queue, active transactions, uptime), read a few times apart so
a semaphore queue that stays is told from one that passes.
kind db - the database host: the disk of its data paths (/database) and of the database's data dir, the database's
size (data and indexes from information_schema as the MariaDB check read them, else the data dir's files - one level,
stat only, never a recursive du - plus InnoDB's system, undo and redo files), the largest schemas and tables, and the
size kept in a small history file on the host (the only thing written; not in check mode) for the growth.
Both: fapolicyd - active? its denials today (ausearch -m FANOTIFY), grouped by program and path.
Standard library only (RHEL's platform Python is enough)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_servicenow_node
short_description: A self-hosted ServiceNow node's units, stats page, logs, disk and memory, read-only
description:
  - Never fails: what could not be read says why (a field of the stats page it cannot find is left out).
options:
  units: {description: "The node units (snc_prod, snc_prod.service; * and ?). [] = every snc_*.service on the host.", type: list, elements: str, default: []}
  home: {description: The install folder (its nodes, logs and glide.properties)., type: str, default: /glide}
  port: {description: "The stats page's port: a number for every unit, {unit or env: port}, or 0 = from glide.properties (glide.servlet.port) or the unit.", type: raw, default: 0}
  stats: {description: Read the stats page., type: bool, default: true}
  stats_pages: {description: "The pages to try, in order.", type: list, elements: str, default: [/xmlstats.do, /stats.do]}
  samples: {description: How many times the stats page is read (a semaphore queue in every one is sustained)., type: int, default: 3}
  interval: {description: Seconds between the reads., type: float, default: 5}
  log_hours: {description: How far back the log is read., type: float, default: 24}
  log_glob: {description: The node's log files in its logs folder., type: str, default: "localhost_log*"}
  log_max_mb: {description: At most this much of the end of each log file is read., type: int, default: 200}
  log_groups: {description: The most frequent messages kept., type: int, default: 10}
  timeout: {description: Seconds per request to the stats page., type: int, default: 10}
  host_name: {description: "The node's name in the inventory: with its FQDN, the addresses asked for the stats page before 127.0.0.1. The instance's address (glide.servlet.uri) is never asked - only sent as a Host header to 127.0.0.1.", type: str, default: ''}
  stats_url: {description: "The stats page's address, forced ({port} and {host} filled in; '' = try the ways in turn).", type: str, default: ''}
  sem_wait_ms: {description: "A slow transaction's semaphore wait from this many ms is a semaphore problem.", type: int, default: 1000}
  stats_auth: {description: "auto - the ServiceNow API login when every way without it is refused (401/403, a redirect, a login page); none; basic - from the start; form - a session of the instance's local login page (side_door.do, login.do) with form_user, closed with logout.do: one login per run, its cookies in memory only. Sent only over HTTPS to the node's own name, or to 127.0.0.1. A redirect is never followed.", type: str, default: auto}
  validate_certs: {description: "Check the stats page's HTTPS certificate and name (the host's trust store). false = not checked.", type: bool, default: true}
  stats_user: {description: The ServiceNow API account (SN_USERNAME)., type: str, default: ''}
  stats_password: {description: Its password (SN_PASSWORD); never in the result., type: str, default: '', no_log: true}
  form_user: {description: "stats_auth form: the node stats login's user name (SN_STATS_USERNAME) - a local account, never the API account.", type: str, default: ''}
  form_password: {description: Its password (SN_STATS_PASSWORD); never in the result., type: str, default: '', no_log: true}
  form_skip: {description: "stats_auth form: where that login was refused before in this run ('' = nowhere). Set: no login is sent here.", type: str, default: ''}
  kind: {description: "node (a front-end node), db (the database host) or pages (the stats pages of the option pages, read from where this runs).", type: str, default: node}
  pages: {description: "kind pages: [{name, url}] - each node's stats page by its own address, read without SSH to the node (the login only over HTTPS to that address's host; no redirect followed; a url with a login, a #, a backslash or a blank in it is not asked).", type: list, elements: dict, default: []}
  paths: {description: "More paths whose filesystems are reported (the database's: /database).", type: list, elements: str, default: []}
  fapolicyd: {description: Read fapolicyd's state and today's denials., type: bool, default: true}
  fapolicyd_since: {description: "ausearch -ts: from when the denials are read.", type: str, default: today}
  fapolicyd_critical: {description: "Programs or paths (prefixes, * and ?) whose denial is critical.", type: list, elements: str, default: []}
  db_datadir: {description: "db: the data dir on this host ('' = unknown).", type: str, default: ''}
  db_sizes: {description: "db: {schemas: [[name, bytes, tables]], tables: [[schema.table, bytes]]} as the MariaDB check read them.", type: dict, default: {}}
  db_top_schemas: {description: db - the largest schemas kept., type: int, default: 5}
  db_top_tables: {description: db - the largest tables kept., type: int, default: 10}
  db_history_file: {description: "db: the size history ('' = none).", type: str, default: ''}
  db_history_keep_days: {description: db - samples older than this are dropped., type: int, default: 60}
author: site automation
'''

RETURN = r'''
hostname: {description: The node's own name., type: str}
units:
  description: "[{unit, env, active, sub, enabled, since, node_dir, port, port_from, stats: {status, page, why, up, node_id,
                 answered, uptime, started, mem_used, mem_max, mem_pct, sem_in_use, sem_available, sem_queued, sem_queued_samples,
                 sem_sets, sched_workers, sched_queue, transactions, found}, log: {files, hours, severe, oom, semaphore,
                 groups: [{message, count, kinds, last}], why}}]"
  type: list
disk: {description: "[{path, size_gb, used_pct, free_gb}] - the install folder's filesystem and /'s (once when the same).", type: list}
memory: {description: "{total_mb, available_mb, available_pct, swap_total_mb, swap_used_pct}", type: dict}
pages: {description: "kind pages: [{name, url, host, port, stats}] - stats as a unit's.", type: list}
form_refused: {description: "stats_auth form: the node stats login was sent here and not accepted (the other nodes do not try it).", type: bool}
missing: {description: "The units asked for that are not installed here (not judged: a box runs one of them).", type: list}
looked_for: {description: The units looked for ([] = every snc_*.service)., type: list}
fapolicyd: {description: "{installed, active, enabled, since, denials, groups: [{exe, path, count, critical}], why}", type: dict}
db: {description: "kind db: {datadir, datadir_fs, datadir_under, source, total_bytes, data_bytes, system_bytes, system_read,
                   schemas: [[name, bytes, tables]], tables: [[name, bytes]], history: {samples, last_bytes, last_time, change_bytes,
                   per_day_bytes, days}, why}"
     type: dict}
'''

import base64
import fnmatch
import glob
import http.client
import json
import os
import re
import socket
import ssl
import subprocess
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from ansible.module_utils.basic import AnsibleModule

TS = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
# "2026-11-09 10:00:01 (123) glide.scheduler.worker.2 7F3A... SEVERE *** ERROR *** the message"
PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s+(?:\(\d+\)\s+)?(?:\S+\s+)??(?:[0-9A-Fa-f]{32}\s+)?"
                    r"(?:SEVERE|WARNING|INFO|DEBUG|FINE\w*|CONFIG)\b\s*")
LEVEL = re.compile(r"\b(SEVERE|WARNING)\b")
SEVERE = re.compile(r"\bSEVERE\b")
OOM = re.compile(r"OutOfMemory")
SEMAPHORE = re.compile(r"semaphore", re.I)
# a semaphore problem: a queue, a limit exceeded, a wait timed out - not a line that only mentions one
SEM_ERR = re.compile(r"semaphore.*\b(queue|exceeded|timed out|timeout|rejected|exhausted|no available)|"
                     r"\b(queue|exceeded|timed out|rejected)\b.*semaphore", re.I)
# "GlideServlet:Transaction waited excessive time to run: 3295ms, semaphore wait: 0ms, session ..." - a slow transaction
SLOW = re.compile(r"Transaction waited excessive time to run:?\s*(\d+)\s*ms", re.I)
SEM_WAIT = re.compile(r"semaphore wait:?\s*(\d+)\s*ms", re.I)
SESSION_WAIT = re.compile(r"session wait:?\s*(\d+)\s*ms", re.I)
# who did it: not needed, and not shown (the node's log is people's activity)
USER = re.compile(r"\buser\s*[:=]\s*[^,;)\]\n]+|\(user [^)]+\)", re.I)


def no_user(text):
    """A log message without the user name in it: "user: …"."""
    return USER.sub(lambda m: "(user …)" if m.group(0).startswith("(") else "user: …", text)
NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def run(argv):
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", str(e)


# ---- the units ------------------------------------------------------------------------------------------------

def unit_name(u):
    u = str(u).strip()
    return u if u.endswith(".service") or "*" in u or "?" in u else u + ".service"


def find_units(wanted):
    """The snc_*.service units the host has (loaded or only installed), and those of `wanted` it has not."""
    names = set()
    for argv in (["systemctl", "list-units", "--all", "--type=service", "--no-legend", "--plain", "--no-pager", "snc_*"],
                 ["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager", "snc_*"]):
        rc, out, _ = run(argv)
        for line in out.splitlines():
            w = line.split()
            if w and w[0].startswith("snc_") and w[0].endswith(".service") and not w[0].startswith("snc_mid"):
                names.add(w[0])
    if not wanted:
        return sorted(names), []
    got, missing = [], []
    for u in wanted:
        pat = unit_name(u)
        hit = sorted(n for n in names if fnmatch.fnmatchcase(n, pat))
        if hit:
            got += [h for h in hit if h not in got]
        else:
            missing.append(pat)
    return got, missing


def unit_show(u):
    rc, out, _ = run(["systemctl", "show", "--no-pager", "-p", "ActiveState", "-p", "SubState", "-p", "UnitFileState",
                      "-p", "ExecStart", "-p", "WorkingDirectory", "-p", "Environment", "-p", "ActiveEnterTimestamp", "-p", "MainPID", u])
    d = {}
    for line in out.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def node_dir(home, env, show):
    """The node's folder: a path under the install folder the unit names (ExecStart, WorkingDirectory, Environment)
    that holds conf/ or logs/, else <home>/nodes/<env>*, <home>/<env>*, else the install folder itself."""
    home = home.rstrip("/") or "/"
    text = " ".join(show.get(k, "") for k in ("ExecStart", "WorkingDirectory", "Environment"))
    for p in re.findall(r"(/[^\s;'\"=]+)", text):
        if not p.startswith(home + "/"):
            continue
        d = p
        while d.startswith(home + "/") and len(d) > len(home):
            if os.path.isdir(os.path.join(d, "conf")) or os.path.isdir(os.path.join(d, "logs")):
                return d
            d = os.path.dirname(d)
    for pat in (os.path.join(home, "nodes", env + "*"), os.path.join(home, env + "*")):
        hits = sorted(h for h in glob.glob(pat) if os.path.isdir(h))
        if hits:
            return hits[0]
    return home


def properties(path):
    d = {}
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith(("#", "!")) and "=" in line:
                    k, v = line.split("=", 1)
                    d[k.strip()] = v.strip()
    except OSError:
        pass
    return d


def port_for(unit, env, cfg, ndir, home, show):
    """(port, where it came from)."""
    if isinstance(cfg, dict):
        for k in (unit, unit[:-len(".service")], env):
            if cfg.get(k):
                return int(cfg[k]), "servicenow_node_port"
    elif str(cfg or "0").strip() not in ("", "0"):
        return int(cfg), "servicenow_node_port"
    for p in (os.path.join(ndir, "conf", "glide.properties"), os.path.join(home, "conf", "glide.properties"),
              os.path.join(ndir, "glide.properties")):
        v = properties(p).get("glide.servlet.port", "")
        if v.isdigit():
            return int(v), p
    text = " ".join(show.get(k, "") for k in ("ExecStart", "Environment"))
    m = (re.search(r"glide\.servlet\.port[= ](\d{2,5})\b", text)
         or re.search(r"(?:^|\s)--?port[= ](\d{2,5})\b", text) or re.search(r"_(\d{4,5})(?:\.service)?$", unit))
    if m:
        return int(m.group(1)), "the unit"
    p = listening_port(show.get("MainPID", ""))
    if p:
        return p, "the unit's java, listening"
    return 0, ""


def listening_port(pid):
    """The TCP port the unit's main process listens on (ss -ltnp), one in 16000-16999 first (ServiceNow's usual)."""
    if not str(pid).isdigit() or int(pid) == 0:
        return 0
    rc, out, _ = run(["ss", "-H", "-ltnp"])
    ports = []
    for line in out.splitlines():
        w = line.split()
        # LISTEN 0 100 *:16000 *:* users:(("java",pid=1234,fd=50))
        m = re.search(r":(\d{2,5})$", w[3]) if len(w) > 4 and "pid=%s," % pid in line else None
        if m:
            ports.append(int(m.group(1)))
    ports = sorted(set(ports), key=lambda x: (not 16000 <= x <= 16999, x))
    return ports[0] if ports else 0


# ---- the stats page -------------------------------------------------------------------------------------------

def _num(v):
    m = NUM.search(str(v or ""))
    if not m:
        return None
    n = float(m.group(0).replace(",", ""))
    u = str(v).lower()
    if re.search(r"\bgb\b|\bg\b", u):
        n *= 1024
    elif re.search(r"\bkb\b|\bk\b", u):
        n /= 1024.0
    elif re.search(r"\bbytes?\b", u):
        n /= 1048576.0
    return n


def _norm(s):
    return " ".join(re.sub(r"[._:/\-]+", " ", str(s)).split()).lower()


def stat_lines(text):
    """[(section, label, value)] from xmlstats.do (each element and attribute) or stats.do (its 'Label: value'
    lines under their headings)."""
    t = text.lstrip()
    out = []
    if t.startswith("<?xml") or t.startswith("<xmlstats") or (t.startswith("<") and not re.match(r"<(!doctype|html)", t, re.I)):
        try:
            root = ET.fromstring(t)
        except ET.ParseError:
            root = None
        if root is not None:
            def walk(e, path):
                name = e.attrib.get("name")
                here = path + [e.tag + (":" + name if name else "")]
                sec = " ".join(here[:-1])
                if e.text and e.text.strip() and len(e) == 0:
                    out.append((sec, e.tag, e.text.strip()))
                for k, v in e.attrib.items():
                    if k != "name":
                        out.append((" ".join(here), k, v))
                for c in e:
                    walk(c, here)
            walk(root, [])
            return out
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    h = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table|th)>", "\n", h)
    h = re.sub(r"(?i)</td>\s*<td[^>]*>", ": ", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = h.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    sec, sub = "", ""
    for raw in h.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() and len(k) <= 60:
                out.append(((sec + " " + sub).strip(), k.strip(), v.strip()))
                continue
        if len(line) <= 60:
            # a heading: a big one (with a known word) starts a section, any other a part of it (a semaphore set)
            if re.search(r"semaphore|scheduler|memory|servlet|statistics|transaction|cluster|database", line, re.I):
                sec, sub = line, ""
            else:
                sub = line
    return out


def parse_stats(text):
    """The numbers this check needs, found by their names (whatever the page's release calls them)."""
    r = {"node_id": "", "uptime": "", "started": "", "mem_used": None, "mem_max": None, "sem_in_use": None,
         "sem_available": None, "sem_queued": None, "sem_sets": [], "sched_workers": None, "sched_queue": None,
         "transactions": None}
    sets = {}
    for sec, label, val in stat_lines(text):
        full, lab = _norm(sec + " " + label), _norm(label)
        if "semaphore" in full:
            name = re.sub(r"(?i)^.*semaphores?(?: sets?)?\s*", "", sec).strip() or "Default"
            name = name.split(":")[-1].strip() or "Default"
            s = sets.setdefault(name, {"name": name, "in_use": None, "available": None, "queued": None})
            if re.search(r"\bin ?use\b", lab) and s["in_use"] is None:
                s["in_use"] = _num(val)
            elif re.search(r"\bavailable\b", lab) and s["available"] is None:
                s["available"] = _num(val)
            elif re.search(r"^(queue (depth|length|size)|queued?|queue)$", lab) and s["queued"] is None:
                s["queued"] = _num(val)
            continue
        if "scheduler" in full or "worker" in lab:
            if re.search(r"queue", lab) and r["sched_queue"] is None:
                r["sched_queue"] = _num(val)
            elif re.search(r"worker", lab) and r["sched_workers"] is None:
                r["sched_workers"] = _num(val)
            continue
        if "memory" in full:
            if re.search(r"\bmax", lab) and r["mem_max"] is None:
                r["mem_max"] = _num(val)
            elif re.search(r"\bin ?use\b|\bused\b", lab) and r["mem_used"] is None:
                r["mem_used"] = _num(val)
            continue
        if re.search(r"active|concurrent", full) and "transaction" in full and r["transactions"] is None:
            r["transactions"] = _num(val)
        elif re.search(r"uptime", lab) and not r["uptime"]:
            r["uptime"] = val
        elif re.search(r"started", lab) and not r["started"]:
            r["started"] = val
        elif re.search(r"cluster node|node id|node name|system id", full) and not r["node_id"]:
            r["node_id"] = val
    r["sem_sets"] = [s for s in sets.values() if any(s[k] is not None for k in ("in_use", "available", "queued"))]
    for k in ("in_use", "available", "queued"):
        vals = [s[k] for s in r["sem_sets"] if s[k] is not None]
        r["sem_" + k] = sum(vals) if vals else None
    if r["mem_used"] is not None and r["mem_max"]:
        r["mem_pct"] = round(100.0 * r["mem_used"] / r["mem_max"], 1)
    else:
        r["mem_pct"] = None
    r["found"] = [k for k in ("node_id", "uptime", "mem_used", "mem_max", "sem_in_use", "sem_queued", "sched_workers",
                              "sched_queue", "transactions") if r[k] not in (None, "")]
    return r


LOGIN = re.compile(r"login\.do|j_security_check|name=[\"']user_name[\"']|<title>\s*Log ?in", re.I)
# the instance's normal web page where the stats were asked for (an SSO-style login page names none of the above):
# with servicenow_node_stats_auth auto it counts as asking for a login too (1.32.0)
WEBPAGE = re.compile(r"^\s*(<!DOCTYPE\s+html|<html[\s>])", re.I)


def stats_bases(port, names, forced="", header_names=None):
    """The ways to ask the node, in order: [(url_base, Host header or '')]. The node's own names first - names: its
    inventory name and its FQDN (https, then http: the stats page may answer only there) - then 127.0.0.1 (http, https -
    a TLS connector answers plain HTTP with 400), then 127.0.0.1 with a name as the Host header: its own names, then
    header_names (the instance's name, glide.servlet.uri's host). The instance's name is never an address that is asked
    (1.32.0): behind a load balancer it reaches any node. forced (servicenow_node_stats_url: {fqdn}, {host}, {port}):
    that one only."""
    names = [n for n in dict.fromkeys(names or []) if n and n not in ("localhost", "127.0.0.1")]
    fqdn = next((n for n in names if "." in n), names[0] if names else "127.0.0.1")
    if forced:
        return [(forced.replace("{port}", str(port)).replace("{fqdn}", fqdn).replace("{host}", names[0] if names else fqdn), "")]
    out = []
    for n in names:
        out += [("https://%s:%d" % (n, port), ""), ("http://%s:%d" % (n, port), "")]
    out += [("http://127.0.0.1:%d" % port, ""), ("https://127.0.0.1:%d" % port, "")]
    for n in list(dict.fromkeys(names + [h for h in header_names or [] if h and h not in ("localhost", "127.0.0.1")])):
        out += [("http://127.0.0.1:%d" % port, n), ("https://127.0.0.1:%d" % port, n)]
    return out


def may_send_login(url, names, local=True):
    """The ServiceNow login goes only to this node: over HTTPS to one of names (its own names; a listed stats page's
    host), or to 127.0.0.1 (local false - a page read from the AAP side: never). Never to the instance's address."""
    host = _url_host(url)
    return (local and host == "127.0.0.1") or (url.lower().startswith("https://")
                                               and host.lower() in [str(n).lower() for n in names or [] if n])


class _NoRedirect(HTTPRedirectHandler):
    """Follows no redirect: the 3xx answer comes back as an HTTPError (urllib would send the Authorization header on)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def context(validate=True):
    """The HTTPS context: the certificate and the host name checked (the host's trust store), unless validate is false."""
    if not validate:
        return ssl._create_unverified_context()   # noqa: S323 - servicenow_node_validate_certs false says so
    return ssl.create_default_context()


class _NamedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPS connection to the URL's address (127.0.0.1) that says server_name in the TLS handshake (SNI) and checks
    the certificate against server_name, not against the address: the name sent as the Host header."""
    def __init__(self, host, server_name="", context=None, **kw):
        super().__init__(host, context=context, **kw)
        self._name_ctx, self._server_name = context, server_name

    def connect(self):
        http.client.HTTPConnection.connect(self)       # the TCP connection (and a proxy's tunnel), no TLS yet
        self.sock = self._name_ctx.wrap_socket(self.sock, server_hostname=self._server_name)


class _NamedHTTPSHandler(HTTPSHandler):
    """HTTPS through _NamedHTTPSConnection: the certificate checked against server_name."""
    def __init__(self, server_name, ctx):
        super().__init__(context=ctx)
        self._server_name, self._ctx = server_name, ctx

    def https_open(self, req):
        return self.do_open(_NamedHTTPSConnection, req, context=self._ctx, server_name=self._server_name)


def opener(validate=True, server_name=""):
    """An opener that follows no redirect, its HTTPS checked as context() says - against server_name when one is given
    (also the name sent in the handshake), else against the URL's host."""
    https = _NamedHTTPSHandler(server_name, context(validate)) if server_name else HTTPSHandler(context=context(validate))
    return build_opener(_NoRedirect(), https)


def _netloc(url):
    """The host[:port] of a URL as urlsplit() reads it - what the request goes to - without a login written in it
    ('' when it cannot be read). 1.42.0: 'https://a#@b/' goes to a, and a is what is shown."""
    try:
        return urlsplit(url if "://" in (url or "") else "//" + (url or "")).netloc.rsplit("@", 1)[-1]
    except ValueError:
        return ""


def _url_host(url):
    """The host of a URL, without its port (an IPv6 address without its brackets)."""
    rest = _netloc(url)
    if rest.startswith("["):
        return rest[1:].split("]")[0]
    return rest.rsplit(":", 1)[0] if ":" in rest else rest


# OpenSSL's verify codes: the certificate does not name the host / its issuer is not trusted
_NAME_MISMATCH = (62, 64)                 # X509_V_ERR_HOSTNAME_MISMATCH, X509_V_ERR_IP_ADDRESS_MISMATCH
_UNTRUSTED = (2, 18, 19, 20, 21)          # unable to get the issuer, self-signed (in the chain), no local issuer, ...


# ssl.SSLCertVerificationError is Python 3.7+; a node's platform Python can be 3.6 (RHEL 8), where a refused
# certificate is an ssl.SSLError "CERTIFICATE_VERIFY_FAILED", or an ssl.CertificateError for a name that does not match.
_CERT_ERRORS = tuple(x for x in (getattr(ssl, "SSLCertVerificationError", None), getattr(ssl, "CertificateError", None)) if x)


def is_cert_error(err):
    """True when err is a refused certificate, on any Python 3."""
    return isinstance(err, _CERT_ERRORS) or "CERTIFICATE_VERIFY_FAILED" in str(err)


def cert_message(err, name):
    """What a certificate the check refused means, and what to do (name: what it was checked against)."""
    why = getattr(err, "verify_message", None) or str(err)
    code = getattr(err, "verify_code", None)
    if code in _NAME_MISMATCH or "mismatch" in why.lower() or "doesn't match" in why.lower():
        return ("the certificate does not name %s: give the node one that does (its name, or glide.servlet.uri's host), "
                "or set servicenow_node_validate_certs false" % name)
    if code in _UNTRUSTED or (code is None and "CERTIFICATE_VERIFY_FAILED" in why):     # Python 3.6: no verify_code
        return ("its certificate is not trusted (%s): add its CA to the host's trust store, or set "
                "servicenow_node_validate_certs false" % why)
    return "its certificate is not accepted (%s): renew or replace it, or set servicenow_node_validate_certs false" % why


def ask(url, host, timeout, login=None, validate=True, cookies=None, data=None):
    """GET url - or POST data (a dict: a form) - with a Host header if given, basic auth with login (user, password),
    cookies ({name: value}) as the Cookie header; https checked unless validate is false -> (status, body, headers):
    status -1 when there was no answer (headers None). A redirect is not followed: its status, and as the body where
    it pointed. Over HTTPS with a Host header (127.0.0.1 with the node's name), the connection goes to the URL's
    address but the handshake says the Host name (SNI) and the certificate is checked against it, not the address."""
    headers = {"Host": host} if host else {}
    if login:
        headers["Authorization"] = "Basic " + base64.b64encode(("%s:%s" % login).encode("utf-8")).decode("ascii")
    if cookies:
        headers["Cookie"] = "; ".join("%s=%s" % kv for kv in cookies.items())
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urlencode(data).encode("utf-8")
    req = Request(url, headers=headers, data=data)
    server_name = ""
    if host and url.lower().startswith("https://"):
        server_name = _url_host("https://" + host)              # the Host header's name, without a port
    try:
        resp = opener(validate, server_name).open(req, timeout=timeout)
        return resp.getcode() or 200, resp.read(4 * 1048576).decode("utf-8", "replace"), resp.headers
    except HTTPError as e:
        if 300 <= e.code < 400:
            return e.code, "sent to %s (not followed)" % (_where(e.headers, True) or "no Location"), e.headers
        try:
            body = e.read(4096).decode("utf-8", "replace")
        except Exception:                   # noqa: BLE001 - the body of an error is only for the message
            body = ""
        return e.code, body, e.headers
    except (URLError, socket.timeout, OSError, ValueError, ssl.SSLError) as e:
        r = getattr(e, "reason", e)
        if is_cert_error(r):
            return -1, cert_message(r, server_name or _url_host(url)), None
        return -1, str(r), None


def fetch(url, host, timeout, login=None, validate=True):
    """ask(), a GET: -> (status, body)."""
    return ask(url, host, timeout, login, validate)[:2]


# ---- servicenow_node_stats_auth form (1.32.0): the instance's local login page, as a browser would -----------------
CK_INPUT = re.compile(r"<input[^>]*\bsysparm_ck\b[^>]*>", re.I)


def _origin(url):
    """scheme://host[:port] of an http(s) URL as urlsplit() reads it ('' when it is none)."""
    try:
        sp = urlsplit(url or "")
    except ValueError:
        return ""
    return "%s://%s" % (sp.scheme.lower(), _netloc(url)) if sp.scheme.lower() in ("http", "https") and _netloc(url) else ""


def keep_cookies(headers, jar):
    """Every Set-Cookie of an answer into jar {name: value} - kept in memory only, for this node's address only."""
    for c in (headers.get_all("Set-Cookie") or []) if headers is not None else []:
        nv = c.split(";", 1)[0]
        if "=" in nv:
            k, v = nv.split("=", 1)
            jar[k.strip()] = v.strip()


def _where(headers, host=False):
    """Where a redirect points, for a message: its path only - never its query or what follows a # (a query may carry
    a token: logout_redirect.do?sysparm_token=...), never a login written in it. host true: scheme://host[:port] in
    front, when the redirect names one. Every line that says where a redirect pointed is made here (1.42.0)."""
    loc = ((headers.get("Location") or "") if headers is not None else "").strip()
    path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/?#]*", "", loc, flags=re.I).split("?")[0].split("#")[0]
    m = re.match(r"^([a-z][a-z0-9+.-]*)://([^/?#]*)", loc, re.I) if host else None
    return ((("%s://%s" % (m.group(1), m.group(2).rsplit("@", 1)[-1])) if m else "") + path)[:200 if host else 120]


USER_INPUT = re.compile(r"<input[^>]*\bname\s*=\s*[\"']user_name[\"'][^>]*>", re.I)


def login_page(origin, page, host, timeout, validate, jar, tried):
    """GET one page that may show the login form (side_door.do, login.do) -> (kind, ck, said). kind: 'ck' (the form
    with its hidden sysparm_ck), 'form' (the user name form, no sysparm_ck on it), 'none' (it answered: a redirect -
    never followed - or a page with no login form), 'dead' (no answer). Its cookies go to jar, a line to tried."""
    code, body, hdrs = ask(origin + page, host, timeout, None, validate, cookies=jar or None)
    hs = (" (Host: %s)" % host) if host else ""
    if code != 200:
        said = body[:80] if code == -1 else "HTTP %s%s" % (code, (" to " + _where(hdrs)) if 300 <= code < 400 else "")
        tried.append("%s%s%s -> %s" % (origin, page, hs, said))
        return ("dead" if code == -1 else "none"), "", said
    keep_cookies(hdrs, jar)
    tag = CK_INPUT.search(body or "")
    m = re.search(r"\bvalue\s*=\s*[\"']([^\"']+)[\"']", tag.group(0)) if tag else None
    if m:
        tried.append("%s%s%s -> 200" % (origin, page, hs))
        return "ck", m.group(1), "HTTP 200"
    if USER_INPUT.search(body or ""):
        tried.append("%s%s%s -> 200, the login form with no sysparm_ck on it" % (origin, page, hs))
        return "form", "", "HTTP 200, the login form with no sysparm_ck"
    tried.append("%s%s%s -> 200, no login form on it" % (origin, page, hs))
    return "none", "", "HTTP 200, no login form on it"


def form_login(origin, host, timeout, creds, validate, tried):
    """One login through the local login page of origin (scheme://host:port): GET side_door.do for its sysparm_ck (a
    hidden input), POST login.do with the user name, the password and that sysparm_ck. When side_door.do does not
    show the form (1.42.0: on some instances it answers a redirect to navpage.do), login.do is read once for it; a
    login form with no sysparm_ck on it is posted without one. No redirect is followed: an answer 302 to
    login_redirect.do is the login accepted; anything else is refused (never asked twice, one POST at most).
    -> (state, said, jar): state 'in'; 'refused' (the POST was sent, and not accepted); 'none' (no login form here:
    nothing was sent). tried gets a line per request - never the password, the sysparm_ck or a cookie."""
    jar = {}
    hs = (" (Host: %s)" % host) if host else ""
    kind, ck, said = login_page(origin, "/side_door.do", host, timeout, validate, jar, tried)
    if kind == "dead":
        return "none", "%s/side_door.do%s: %s" % (origin, hs, said), jar
    if kind != "ck":
        kind2, ck, said2 = login_page(origin, "/login.do", host, timeout, validate, jar, tried)
        if kind2 not in ("ck", "form") and kind != "form":
            return "none", "%s%s shows no login form: side_door.do -> %s; login.do -> %s" % (origin, hs, said, said2), jar
    data = {"user_name": creds[0], "user_password": creds[1], "sys_action": "sysverb_login"}
    if ck:
        data["sysparm_ck"] = ck
    code, body, hdrs = ask(origin + "/login.do", host, timeout, None, validate, cookies=jar, data=data)
    keep_cookies(hdrs, jar)
    where = _where(hdrs) if 300 <= code < 400 else ""
    said = body[:80] if code == -1 else "HTTP %s%s" % (code, (" to " + where) if where else "")
    tried.append("%s/login.do%s (the node stats login%s) -> %s" % (origin, hs, "" if ck else ", no sysparm_ck", said))
    if 300 <= code < 400 and "login_redirect.do" in where:
        return "in", said, jar
    return "refused", said, jar


def form_logout(origin, host, timeout, validate, jar, tried):
    """GET logout.do with the session's cookies -> '' when it answered (200, or a redirect - not followed), else why."""
    code, body, hdrs = ask(origin + "/logout.do", host, timeout, None, validate, cookies=jar)
    said = body[:80] if code == -1 else "HTTP %s%s" % (code, (" to " + _where(hdrs)) if 300 <= code < 400 and _where(hdrs) else "")
    tried.append("%s/logout.do%s -> %s" % (origin, (" (Host: %s)" % host) if host else "", said))
    return "" if code == 200 or 300 <= code < 400 else said


def form_close(sessions, timeout, validate):
    """logout.do for every session of sessions ({(origin, Host header): {state, said, jar, sts}}), once each; the line
    it leaves and why a session was not closed (form_logout) go to every reading (sts) that used the session."""
    for (origin, host), rec in list(sessions.items()):
        sessions.pop((origin, host), None)
        lines = []
        out = form_logout(origin, host, timeout, validate, rec["jar"], lines)
        for st in rec["sts"]:
            st["tried"] += lines
            if out:
                st["form_logout"] = out


FORM_NEEDS = ('servicenow_node_stats_auth form needs the "ServiceNow node stats login" credential on the job template '
              "(SN_STATS_USERNAME, SN_STATS_PASSWORD)")


def read_stats_form(st, ways, names, local, samples, interval, timeout, creds, validate, skip="", sessions=None, lock=None):
    """servicenow_node_stats_auth form: the stats page read in a session of the instance's local login page. One login
    per node address (scheme://host:port, with its Host header) and run: sessions - given by a caller that reads more
    than one unit or page - keeps the session a call opened, a later call for the same address reads in it and sends
    no login (1.42.0), and the caller closes them all with form_close(). Without sessions: one login per call, closed
    here. Never a second try: the first of the node's own addresses (may_send_login: HTTPS to its own name, or
    127.0.0.1) whose side_door.do - or, when that shows none, login.do - shows a login form gets it; then the samples there with the session's cookies, and
    logout.do whatever happened. st.form: in, refused, no_numbers (logged in, no numbers), no_page (no login form on
    any address), not_asked (no credential, no address the login may go to), skipped (skip: where it was refused
    before - nothing is sent). st.form_logout: why the session was not closed. No redirect is followed."""
    st["form"] = "not_asked"
    if skip:
        st.update({"form": "skipped", "form_skip": skip,
                   "why": "not asked: the node stats login was refused on %s, and a refused login is not tried again in "
                          "the same run" % skip})
        return st
    if not (creds and creds[0] and creds[1]):
        st["why"] = "not asked: " + FORM_NEEDS
        return st
    groups = {}                                 # (origin, Host header) -> the stats pages' URLs there, in order
    for base, host, page in ways:
        o = _origin(base + page)
        if o and may_send_login(o, names, local):
            groups.setdefault((o, host), []).append(base + page)
    if not groups:
        st["why"] = ("not asked: with servicenow_node_stats_auth form the login is sent only over HTTPS to the node's own "
                     "name%s, and no address here is one" % (", or to 127.0.0.1 on it" if local else ""))
        return st
    st["form"] = "no_page"
    own = sessions is None
    sessions = {} if own else sessions
    try:
        for (origin, host), urls in groups.items():
            hs = (" (Host: %s)" % host) if host else ""
            if lock:
                lock.acquire()
            try:
                rec = sessions.get((origin, host))
                if rec is None:
                    state, said, jar = form_login(origin, host, timeout, creds, validate, st["tried"])
                    rec = {"state": state, "said": said, "jar": jar, "sts": []}
                    if state != "none":             # a login was sent: this address gets no second one in this run
                        sessions[(origin, host)] = rec
                else:
                    st["tried"].append("%s%s: the session this run opened here is used (one login per node address)" % (origin, hs))
            finally:
                if lock:
                    lock.release()
            state, said, jar = rec["state"], rec["said"], rec["jar"]
            if state == "none":                 # an address that answered (no form on it) says more than a dead one
                if not st["why"] or ("shows no login form" in said and "shows no login form" not in st["why"]):
                    st["why"] = said
                    m = re.search(r"HTTP (\d+)", said)
                    st["status"] = int(m.group(1)) if m else (st["status"] or -1)
                continue
            rec["sts"].append(st)
            if state != "in":
                st.update({"form": "refused", "status": 0, "why": "%s/login.do: %s" % (origin, said)})
                break
            st["form"] = "no_numbers"
            chosen = None
            for i in range(max(1, samples)):
                if i:
                    time.sleep(max(0.0, interval))
                got = None
                for url in ([chosen] if chosen else urls):
                    code, body, _h = ask(url, host, timeout, None, validate, cookies=jar)
                    said = body[:80] if code == -1 else ("%s %s" % (code, body)) if 300 <= code < 400 else code
                    st["tried"].append("%s%s (in the session) -> %s" % (url, hs, said))
                    if code == 200 and parse_stats(body)["found"]:
                        got, chosen = parse_stats(body), url
                        break
                    if not chosen:              # a later sample that failed keeps what the first ones read
                        st["status"] = code
                        st["why"] = "logged in at %s, but %s showed no numbers (%s)" % (
                            origin, url, ("HTTP %s" % code) if code != -1 else body[:120])
                if got is None:
                    break
                st.update(got)
                st.update({"page": chosen[len(origin):], "url": chosen, "host_header": host, "login": True, "why": "",
                           "up": True, "status": 200, "body": "", "answered": answered_at(origin, host), "form": "in"})
                st["sem_queued_samples"].append(got["sem_queued"])
            break
    finally:
        if own:
            form_close(sessions, timeout, validate)
    return st


def answered_at(base, host=""):
    """The address that answered, short, for the report's cell: 'node01.example.mil:16000', '127.0.0.1:16000', or
    '127.0.0.1:16000 as sn.example.mil' (a Host header). Plain HTTP is said."""
    m = re.match(r"^([a-z]+)://([^/]+)", base or "", re.I)
    if not m:
        return base or ""
    return m.group(2) + ((" as %s" % host) if host else "") + (" (http)" if m.group(1).lower() == "http" else "")


def read_stats(port, pages, samples, interval, timeout, host_names=None, forced="", auth="auto", login=None, validate=True,
               header_names=None, local=True, form=None, form_skip="", sessions=None, lock=None):
    """The stats page, read samples times: the first way (stats_bases) and page that answers 200 with numbers on it is
    used for every read. auth auto: when none answered with numbers and one asked for a login (401 / 403, a redirect,
    a login page), the ways are asked again with the ServiceNow API login (basic; only where may_send_login); basic: with it
    from the start; none: never. Not read: what was tried (never the login), the last status and the start of the
    last answer. A redirect is never followed (the login is not sent on): it is a failed try that says where it
    pointed. HTTPS is checked unless validate is false. header_names: names sent only as a Host header to 127.0.0.1
    (the instance's); local false (a listed page read from the AAP side): no login to 127.0.0.1. answered: the address
    that answered, short, for the report. auth form: read_stats_form() with form (user, password) - the node stats
    login, never the API login; form_skip: where that login was refused before (nothing is sent); sessions, lock: the
    sessions a caller shares between its readings (read_stats_form)."""
    st = {"status": 0, "page": "", "url": "", "why": "", "up": False, "sem_queued_samples": [], "tried": [], "body": "",
          "login": False, "answered": "", "login_refused": False}
    if not port and not forced:
        st["why"] = "no port: set servicenow_node_port, or glide.servlet.port in the node's glide.properties"
        return st
    names = [n for n in host_names or [] if n]
    ways = [(b, h, pg) for b, h in stats_bases(port, names, forced, header_names) for pg in ([""] if forced else pages)]
    login = login if login and login[0] and login[1] else None
    if auth == "form":
        return read_stats_form(st, ways, names, local, samples, interval, timeout, form, validate, form_skip, sessions, lock)

    def attempt(base, host, page, use_login):
        url = base + page
        lg = login if use_login and may_send_login(url, names, local) else None
        if use_login and not lg:
            return None
        code, body = fetch(url, host, timeout, lg, validate)
        said = body[:80] if code == -1 else ("%s %s" % (code, body)) if 300 <= code < 400 else code   # a redirect: where to
        st["tried"].append("%s%s%s -> %s" % (url, (" (Host: %s)" % host) if host else "", " (with the login)" if lg else "", said))
        return code, body

    chosen, last_http, needs_login = None, None, False
    rounds = ([True] if auth == "basic" and login else [False] + ([True] if auth == "auto" and login else []))
    for i in range(max(1, samples)):
        if i:
            time.sleep(max(0.0, interval))
        got = None
        for use_login in ([chosen[3]] if chosen else rounds):
            if use_login and not chosen and not needs_login and auth == "auto":
                break                          # no way asked for a login: none is sent
            for base, host, page in ([chosen[:3]] if chosen else ways):
                r = attempt(base, host, page, use_login)
                if r is None:
                    continue
                code, body = r
                if code != -1:
                    last_http = code
                if code == 200 and parse_stats(body)["found"]:
                    got, chosen = parse_stats(body), (base, host, page, use_login)
                    break
                # a redirect (to login.do, an SSO page) is asked for a login too: never followed
                wants = code in (401, 403) or 300 <= code < 400 or (code == 200 and LOGIN.search(body)) \
                    or (code == 200 and auth == "auto" and WEBPAGE.match(body or ""))
                if wants:
                    needs_login = True
                if use_login and (wants or (code == 200 and WEBPAGE.match(body or ""))):
                    st["login_refused"] = True     # asked with the login, and still no stats: this account may not read them
                st["status"] = code
                st["why"] = ("%s%s: %s" % (base + page, (" (Host: %s)" % host) if host else "",
                                           ("HTTP %s%s" % (code, (", " + body) if 300 <= code < 400 else ""))
                                           if code != -1 else body[:200])
                             if not (code == 200) else "%s answered, but nothing on it was recognised" % (base + page))
                st["body"] = re.sub(r"\s+", " ", body or "")[:200] if code != -1 and not 300 <= code < 400 else ""
            if got:
                break
        if got is None:
            st["status"] = last_http if last_http else -1       # -1: nothing answered at all (the node is down)
            if not st["tried"] and ways:                        # basic: the login may go to none of the addresses
                st["why"] = ("not asked: with servicenow_node_stats_auth basic the login is sent only over HTTPS to the "
                             "node's own name%s, and no address here is one" % (", or to 127.0.0.1 on it" if local else ""))
            break
        st.update(got)
        st.update({"page": chosen[2], "url": chosen[0] + chosen[2], "host_header": chosen[1], "login": chosen[3],
                   "why": "", "up": True, "status": 200, "body": "", "answered": answered_at(chosen[0], chosen[1]),
                   "login_refused": False})
        st["sem_queued_samples"].append(got["sem_queued"])
    return st


def own_names(*names):
    """The node's own names, in order, once each (not localhost): its inventory name, its FQDN."""
    return [n for n in dict.fromkeys(str(x or "").strip() for x in names) if n and n not in ("localhost", "127.0.0.1")]


def page_port(url):
    """The port of a URL as urlsplit() reads it (443 for https, 80 for http when none is written; 0 when it cannot be
    told)."""
    try:
        sp = urlsplit(url or "")
        port = sp.port
    except ValueError:
        return 0
    if sp.scheme.lower() not in ("http", "https") or not sp.netloc:
        return 0
    return port or (443 if sp.scheme.lower() == "https" else 80)


PAGE_NETLOC = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9._-]+)(:\d{1,5})?$")


def page_url_why(url):
    """Why a servicenow_node_stats_pages URL is not asked ('' when it is): it must be http(s)://host[:port]/path with
    nothing that makes the host shown differ from the host asked - no login in it, no '#', no '\\', no blank."""
    try:
        sp = urlsplit(url or "")
        sp.port
    except ValueError:
        return "its url cannot be read as an address"
    if sp.scheme.lower() not in ("http", "https") or not sp.netloc:
        return "its url must be the stats page's full address (https://node1.example.mil:16000/stats.do)"
    plain = "its url must be plain: https://host:port/page - no #, no backslash, no blank"
    if "#" in url or "\\" in url or re.search(r"\s", url):
        return plain
    if re.match(r"^https?://[^/]*@", url, re.I):
        return "its url carries a login: leave it out"
    if not PAGE_NETLOC.match(sp.netloc):
        return plain
    return ""


def read_pages(entries, samples, interval, timeout, auth="auto", login=None, validate=True, form=None, form_skip=""):
    """servicenow_node_stats_pages, read from where this runs (the AAP side), no SSH to the nodes: each entry {name,
    url} asked at its own URL only, the same way a node's page is (the same numbers, samples, no redirect followed);
    the login only over HTTPS and only to the entry's own host. The entries are read side by side (each waits between
    its samples). -> [{name, url, host, port, stats}] in the entries' order; an entry not read: stats.up false with
    what was tried. auth form: the first entry alone first - when its login is refused, the others are not asked (one
    refused login per run: an account locks after a few)."""
    import threading
    out = [None] * len(entries or [])
    skip = [form_skip]
    sessions, lock = {}, threading.Lock()       # auth form: two entries of one node address share one session

    def one(i, e):
        url = str((e or {}).get("url") or "").strip()
        host = _url_host(url)
        blank = {"status": -1, "up": False, "sem_queued_samples": [], "tried": [], "body": "", "url": "", "page": "",
                 "login": False, "answered": "", "login_refused": False}
        try:
            why = page_url_why(url)
            st = dict(blank, status=0, why="not asked: " + why) if why else \
                read_stats(page_port(url), [""], samples, interval, timeout, [host], url, auth, login, validate, local=False,
                           form=form, form_skip=skip[0], sessions=sessions, lock=lock)
        except Exception as err:            # noqa: BLE001 - one entry's failure is its own finding, the others are read
            st = dict(blank, why=str(err)[:200])
        out[i] = {"name": str((e or {}).get("name") or "").strip() or host, "url": url, "host": host, "port": page_port(url),
                  "stats": st}

    first = 0
    try:
        if auth == "form" and entries and not skip[0]:
            one(0, entries[0])
            first = 1
            if out[0]["stats"].get("form") == "refused":
                skip[0] = out[0]["name"]
        threads = [threading.Thread(target=one, args=(i, e)) for i, e in enumerate(entries or []) if i >= first]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        form_close(sessions, timeout, validate)
    return out


# ---- the log --------------------------------------------------------------------------------------------------

def message_of(line):
    """What a log record says: after its "*** WARNING ***" / "*** ERROR ***" marker, else after the time, thread,
    transaction ID and level."""
    parts = re.split(r"\*\*\* ?(?:ERROR|WARNING|SEVERE) ?\*\*\*\s*", line, maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        return parts[1].strip()
    m = PREFIX.sub("", line, count=1).strip()
    return m or line.strip()


def key_of(msg):
    """A message with what changes every time taken out: times, "(nnn)", thread numbers, txid, user names, IDs (in paths
    too: /api/now/ui/page_timing/<id> is /api/now/ui/page_timing), every number."""
    k = USER.sub("", msg)
    k = re.sub(r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*(?:\(\d+\)\s*)?", "", k)
    k = re.sub(r"\btxid=\S+", "", k)
    k = re.sub(r"(-thread)-\d+\b", r"\1", k)
    k = re.sub(r"/(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d+)(?=[/?\s,]|$)", "", k, flags=re.I)
    k = re.sub(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b|\b\d\d:\d\d:\d\d(?:[.,]\d+)?\b", "", k)
    k = re.sub(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "<id>", k, flags=re.I)
    k = re.sub(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{16,}\b", "<hex>", k, flags=re.I)
    k = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "<ip>", k)
    k = re.sub(r"(?<=/)\d+(?=/|$|\s|\?)", "N", k)
    k = re.sub(r"\d+", "N", k)            # counts, users, ports: the same message whatever its numbers
    return re.sub(r"\s+", " ", k).strip()


def tail_lines(path, max_bytes):
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - max_bytes))
        if size > max_bytes:
            f.readline()                     # a partial line
        for raw in f:
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


def read_log(logdir, pattern, hours, max_mb, top, now=None, sem_wait_ms=1000):
    """The node's log in the last hours: OutOfMemory, semaphore problems (a semaphore queue, exceeded, timed out; a
    slow transaction that waited sem_wait_ms or more for its semaphore), SEVERE lines, and slow transactions ("waited
    excessive time to run" - their own kind, with the longest run), grouped by message."""
    now = now or time.time()
    since = now - hours * 3600.0
    since_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(since))
    lg = {"dir": logdir, "files": [], "hours": hours, "severe": 0, "oom": 0, "semaphore": 0, "slow": 0, "slow_max_ms": 0,
          "slow_sem": 0, "sem_wait_ms": sem_wait_ms, "groups": [], "why": ""}
    files = sorted((p for p in glob.glob(os.path.join(logdir, pattern)) if os.path.isfile(p)), key=os.path.getmtime)
    files = [p for p in files if os.path.getmtime(p) >= since]
    if not os.path.isdir(logdir):
        lg["why"] = "no logs folder at %s" % logdir
        return lg
    if not files:
        lg["why"] = "no %s in %s written in the last %s h" % (pattern, logdir, hours)
        return lg
    groups = {}

    def close(rec):
        if not rec:
            return
        first, cont = rec[0], rec[1:6]
        kinds = []
        text = "\n".join([first] + cont)
        run_ms = session_ms = 0
        if OOM.search(text):
            kinds.append("OutOfMemory")
        slow = SLOW.search(first)
        if slow:
            # a slow transaction: waiting for a semaphore (the node's limit - a semaphore problem), waiting for the
            # session (one user's own requests queued behind each other: the session synchronizer), or slow itself
            run_ms = int(slow.group(1))
            sem = int((SEM_WAIT.search(first) or [0, 0])[1])
            session_ms = int((SESSION_WAIT.search(first) or [0, 0])[1])
            if sem >= sem_wait_ms:
                kinds.append("slow transaction (semaphore wait)")
                lg["slow_sem"] += 1
            elif run_ms and session_ms >= 0.8 * run_ms:
                kinds.append("slow transaction (session wait)")
            else:
                kinds.append("slow transaction")
            lg["slow"] += 1
            lg["slow_max_ms"] = max(lg["slow_max_ms"], run_ms)
        elif SEM_ERR.search(first) and LEVEL.search(first):
            kinds.append("semaphore")
        if SEVERE.search(first):
            kinds.append("SEVERE")
        if not kinds:
            return
        lg["oom"] += "OutOfMemory" in kinds
        lg["semaphore"] += "semaphore" in kinds or "slow transaction (semaphore wait)" in kinds
        lg["severe"] += "SEVERE" in kinds
        msg = message_of(first)
        if "OutOfMemory" in kinds and not OOM.search(first):
            msg = next((c.strip() for c in cont if OOM.search(c)), msg)
        msg = no_user(msg)
        k = "|".join(kinds) + "|" + key_of(msg)
        g = groups.setdefault(k, {"message": msg[:400], "count": 0, "kinds": [], "last": "", "max_ms": 0, "max_session_ms": 0})
        g["count"] += 1
        g["max_ms"] = max(g["max_ms"], run_ms)
        g["max_session_ms"] = max(g["max_session_ms"], session_ms)
        g["message"] = msg[:400]
        g["last"] = TS.match(first).group(0) if TS.match(first) else ""
        g["kinds"] = sorted(set(g["kinds"]) | set(kinds))

    for p in files:
        lg["files"].append(os.path.basename(p))
        rec = []
        try:
            for line in tail_lines(p, max_mb * 1048576):
                m = TS.match(line)
                if m:
                    close(rec)
                    rec = [line] if "%s %s" % (m.group(1), m.group(2)) >= since_s else []
                elif rec:
                    rec.append(line)
            close(rec)
        except OSError as e:
            lg["why"] = "%s: %s" % (p, e)
    order = {"OutOfMemory": 0, "semaphore": 1, "slow transaction (semaphore wait)": 1, "SEVERE": 2, "slow transaction": 3,
             "slow transaction (session wait)": 3}
    lg["groups"] = sorted(groups.values(), key=lambda g: (min(order[k] for k in g["kinds"]), -g["count"]))[:max(1, top)]
    return lg


# ---- the host -------------------------------------------------------------------------------------------------

def disks(paths):
    """[{path, size_gb, free_gb, used_pct}] - each path's filesystem, once."""
    out, seen = [], set()
    for p in paths:
        if not os.path.exists(p):
            continue
        try:
            st = os.statvfs(p)
            dev = os.stat(p).st_dev
        except OSError:
            continue
        if dev in seen:
            continue
        seen.add(dev)
        size = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = size - st.f_bfree * st.f_frsize
        out.append({"path": p, "size_gb": round(size / 1073741824.0, 1), "free_gb": round(free / 1073741824.0, 1),
                    "used_pct": round(100.0 * used / (used + free), 1) if used + free else 0.0})
    return out


def memory():
    m = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                m[k] = int(v.split()[0]) / 1024.0
    except (OSError, ValueError, IndexError):
        return {}
    tot, av = m.get("MemTotal", 0), m.get("MemAvailable", m.get("MemFree", 0))
    st, sf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    return {"total_mb": int(tot), "available_mb": int(av), "available_pct": round(100.0 * av / tot, 1) if tot else None,
            "swap_total_mb": int(st), "swap_used_pct": round(100.0 * (st - sf) / st, 1) if st else 0.0}


# ---- fapolicyd -----------------------------------------------------------------------------------------------

def matches(x, p):
    """A program or path x is p: * and ? patterns; a folder (x under it); a bare name (java: the program's name)."""
    if "*" in p or "?" in p:
        return fnmatch.fnmatchcase(x, p)
    if "/" not in p:
        return os.path.basename(x) == p or os.path.basename(x).startswith(p + "-")
    return x == p or x.startswith(p.rstrip("/") + "/")


def fapolicyd_groups(text, critical=None):
    """ausearch -i output -> [{exe, path, count, critical}] most frequent first: one per program and path denied."""
    pats = [str(p) for p in critical or [] if str(p).strip()]
    groups, exe, path = {}, "", ""

    def close():
        if exe or path:
            g = groups.setdefault((exe, path), {"exe": exe or "?", "path": path or "?", "count": 0, "critical": False})
            g["count"] += 1
            g["critical"] = g["critical"] or any(matches(x, p) for p in pats for x in (exe, path) if x)
    for line in text.splitlines():
        if line.startswith("----"):
            close()
            exe, path = "", ""
            continue
        if "type=SYSCALL" in line:
            m = re.search(r"\bexe=(\S+)", line)
            exe = m.group(1).strip('"') if m else exe
        elif "type=PATH" in line and not path:
            m = re.search(r"\bname=(\S+)", line)
            path = m.group(1).strip('"') if m else path
    close()
    return sorted(groups.values(), key=lambda g: (not g["critical"], -g["count"], g["exe"], g["path"]))


def fapolicyd(since, critical):
    fa = {"installed": False, "active": "", "enabled": "", "since": since, "denials": 0, "groups": [], "why": ""}
    rc, out, _ = run(["rpm", "-q", "fapolicyd"])
    fa["installed"] = rc == 0
    if not fa["installed"]:
        return fa
    fa["active"] = run(["systemctl", "is-active", "fapolicyd"])[1].strip() or "unknown"
    fa["enabled"] = run(["systemctl", "is-enabled", "fapolicyd"])[1].strip() or "unknown"
    rc, out, err = run(["ausearch", "-m", "FANOTIFY", "-ts", since, "-i"])
    if rc not in (0, 1):                 # 1: no matches
        fa["why"] = "ausearch: " + (err.strip().splitlines() or ["exit %d" % rc])[-1][:200]
    fa["groups"] = fapolicyd_groups(out, critical)
    fa["denials"] = sum(g["count"] for g in fa["groups"])
    return fa


# ---- the database host ----------------------------------------------------------------------------------------

SYSTEM_FILES = ("ibdata*", "undo*", "ib_logfile*", "#innodb_redo/*", "ibtmp*", "#innodb_temp/*", "aria_log*")


def fs_of(path):
    """The filesystem holding path: {path, mount, size_gb, used_gb, free_gb, used_pct, size_b, used_b, free_b}, or None."""
    if not path or not os.path.exists(path):
        return None
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    m = os.path.realpath(path)
    while not os.path.ismount(m):
        m = os.path.dirname(m)
    size, free, used = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize, (st.f_blocks - st.f_bfree) * st.f_frsize
    g = 1073741824.0
    return {"path": path, "mount": m, "size_b": size, "used_b": used, "free_b": free, "size_gb": round(size / g, 1),
            "used_gb": round(used / g, 1), "free_gb": round(free / g, 1), "used_pct": round(100.0 * used / (used + free), 1) if used + free else 0.0}


def files_sizes(datadir, top_schemas, top_tables):
    """The data dir's own files, one level down, stat only: (schemas [[name, bytes, files]], tables [[schema.table, bytes]],
    system bytes). A schema is a folder; its .ibd / .MYD / .MYI / .MAD / .MAI files are its tables."""
    schemas, tables, system = [], [], 0
    try:
        entries = list(os.scandir(datadir))
    except OSError:
        return [], [], 0
    for e in entries:
        if e.is_dir(follow_symlinks=False) and not e.name.startswith("#") and e.name not in ("mysql", "performance_schema", "sys"):
            total, n = 0, 0
            try:
                for f in os.scandir(e.path):
                    if f.is_file(follow_symlinks=False):
                        sz = f.stat(follow_symlinks=False).st_size
                        total += sz
                        base, ext = os.path.splitext(f.name)
                        if ext.lower() in (".ibd", ".myd", ".myi", ".mad", ".mai"):
                            n += 1
                            tables.append(["%s.%s" % (e.name, base), sz])
            except OSError:
                continue
            schemas.append([e.name, total, n])
    for pat in SYSTEM_FILES:
        for f in glob.glob(os.path.join(datadir, pat)):
            try:
                if os.path.isfile(f):
                    system += os.path.getsize(f)
            except OSError:
                pass
    merged = {}
    for name, size in tables:                      # .MYD and .MYI of one table: one table
        merged[name] = merged.get(name, 0) + size
    schemas.sort(key=lambda r: -r[1])
    return schemas[:top_schemas], sorted(([k, v] for k, v in merged.items()), key=lambda r: -r[1])[:top_tables], system


def system_bytes(datadir):
    total, read = 0, False
    for pat in SYSTEM_FILES:
        for f in glob.glob(os.path.join(datadir, pat)):
            try:
                if os.path.isfile(f):
                    total += os.path.getsize(f)
                    read = True
            except OSError:
                pass
    return total, read


def history(path, total, fs_used, keep_days, write, now=None):
    """The size history: read it, add this run (unless write is false), keep keep_days -> {samples, last_bytes, last_time,
    change_bytes, per_day_bytes, days}."""
    now = now or time.time()
    h = {"samples": 0, "last_bytes": None, "last_time": "", "change_bytes": None, "per_day_bytes": None, "days": None, "why": ""}
    if not path:
        return h
    data = []
    try:
        with open(path) as f:
            data = [x for x in json.load(f) if isinstance(x, dict) and "t" in x and "b" in x]
    except (OSError, ValueError):
        data = []
    data = [x for x in data if now - float(x["t"]) <= keep_days * 86400.0 and float(x["t"]) < now]
    if data:
        last = data[-1]
        h["last_bytes"], h["last_time"] = int(last["b"]), time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(float(last["t"])))
        h["change_bytes"] = int(total) - int(last["b"])
        first = data[0]
        days = (now - float(first["t"])) / 86400.0
        if days >= 1.0:
            h["days"] = round(days, 1)
            h["per_day_bytes"] = int((int(total) - int(first["b"])) / days)
    h["samples"] = len(data) + 1
    if write:
        data.append({"t": int(now), "b": int(total), "fs_used": int(fs_used or 0)})
        try:
            d = os.path.dirname(path)
            if d and not os.path.isdir(d):
                os.makedirs(d, 0o755)
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except OSError as e:
            h["why"] = "the history could not be written: %s" % e
    return h


def database(p, check_mode):
    datadir = (p["db_datadir"] or "").rstrip("/") or ""
    db = {"datadir": datadir, "datadir_fs": None, "datadir_under": None, "source": "", "total_bytes": None, "data_bytes": None,
          "system_bytes": None, "system_read": False, "schemas": [], "tables": [], "history": {}, "why": ""}
    if datadir and os.path.isdir(datadir):
        db["datadir_fs"] = fs_of(datadir)
        roots = [x.rstrip("/") for x in p["paths"] if str(x).strip()]
        db["datadir_under"] = any(datadir == r or datadir.startswith(r + "/") for r in roots) if roots else None
    elif datadir:
        db["why"] = "the data dir %s is not on this host (a container's? set servicenow_db_datadir to its path here)" % datadir
    sizes = p["db_sizes"] or {}
    schemas = [[str(r[0]), int(float(r[1] or 0)), int(float(r[2] if len(r) > 2 else 0))] for r in sizes.get("schemas") or [] if len(r) >= 2]
    tables = [[str(r[0]), int(float(r[1] or 0))] for r in sizes.get("tables") or [] if len(r) >= 2]
    if schemas:
        db["source"] = "information_schema"
        db["data_bytes"] = sum(r[1] for r in schemas)
        db["schemas"] = sorted(schemas, key=lambda r: -r[1])[:p["db_top_schemas"]]
        db["tables"] = sorted(tables, key=lambda r: -r[1])[:p["db_top_tables"]]
    elif db["datadir_fs"]:
        sc, tb, _ = files_sizes(datadir, 10 ** 6, p["db_top_tables"])
        if sc:
            db["source"] = "the data dir's files"
            db["data_bytes"] = sum(r[1] for r in sc)
            db["schemas"], db["tables"] = sc[:p["db_top_schemas"]], tb
    if db["datadir_fs"]:
        db["system_bytes"], db["system_read"] = system_bytes(datadir)
    if db["data_bytes"] is not None:
        db["total_bytes"] = db["data_bytes"] + (db["system_bytes"] or 0)
        fs = db["datadir_fs"] or {}
        db["history"] = history(p["db_history_file"], db["total_bytes"], fs.get("used_b"), p["db_history_keep_days"], not check_mode)
    elif not db["why"]:
        db["why"] = "the database's size was not read (no data dir, and the MariaDB check read no sizes)"
    return db


def main():
    module = AnsibleModule(argument_spec=dict(
        units=dict(type="list", elements="str", default=[]), home=dict(type="str", default="/glide"),
        port=dict(type="raw", default=0), stats=dict(type="bool", default=True),
        stats_pages=dict(type="list", elements="str", default=["/xmlstats.do", "/stats.do"]),
        samples=dict(type="int", default=3), interval=dict(type="float", default=5),
        log_hours=dict(type="float", default=24), log_glob=dict(type="str", default="localhost_log*"),
        log_max_mb=dict(type="int", default=200), log_groups=dict(type="int", default=10),
        timeout=dict(type="int", default=10), kind=dict(type="str", default="node", choices=["node", "db", "pages"]),
        pages=dict(type="list", elements="dict", default=[]),
        host_name=dict(type="str", default=""), stats_url=dict(type="str", default=""),
        stats_auth=dict(type="str", default="auto", choices=["auto", "none", "basic", "form"]),
        stats_user=dict(type="str", default=""), stats_password=dict(type="str", default="", no_log=True),
        form_user=dict(type="str", default=""), form_password=dict(type="str", default="", no_log=True),
        form_skip=dict(type="str", default=""),
        validate_certs=dict(type="bool", default=True),
        sem_wait_ms=dict(type="int", default=1000),
        paths=dict(type="list", elements="str", default=[]), fapolicyd=dict(type="bool", default=True),
        fapolicyd_since=dict(type="str", default="today"), fapolicyd_critical=dict(type="list", elements="str", default=[]),
        db_datadir=dict(type="str", default=""), db_sizes=dict(type="dict", default={}),
        db_top_schemas=dict(type="int", default=5), db_top_tables=dict(type="int", default=10),
        db_history_file=dict(type="str", default=""), db_history_keep_days=dict(type="int", default=60)),
        supports_check_mode=True)
    p = module.params
    if p["kind"] == "pages":
        got = read_pages(p["pages"], p["samples"], p["interval"], p["timeout"], p["stats_auth"],
                         (p["stats_user"], p["stats_password"]), p["validate_certs"],
                         form=(p["form_user"], p["form_password"]), form_skip=p["form_skip"])
        module.exit_json(changed=False, pages=got, form_refused=any(x["stats"].get("form") == "refused" for x in got), msg="; ".join("%s %s" % (x["name"], "read" if x["stats"]["up"] else "not read")
                                                                  for x in got) or "no stats page listed")
    home = p["home"].rstrip("/") or "/"
    fa = fapolicyd(p["fapolicyd_since"], p["fapolicyd_critical"]) if p["fapolicyd"] else {}
    if p["kind"] == "db":
        db = database(p, module.check_mode)
        paths = [x for x in p["paths"] if str(x).strip()] + ([db["datadir"]] if db["datadir_fs"] else []) + ["/"]
        module.exit_json(changed=False, hostname=socket.gethostname(), units=[], missing=[], looked_for=[], fapolicyd=fa, db=db,
                         disk=disks(paths), memory=memory(),
                         msg="database %s; fapolicyd %s" % (("%d bytes (%s)" % (db["total_bytes"], db["source"])) if db["total_bytes"]
                                                            is not None else "size not read", fa.get("active") or "not installed"))
    wanted = [u for u in p["units"] if str(u).strip()]
    names, missing = find_units(wanted)
    units = []
    form_skip = p["form_skip"]                 # the node stats login refused before (another node, a unit above): not again
    sessions = {}                              # the node stats login: units of one address (node:port) share a session
    try:
        for u in names:
            show = unit_show(u)
            env = re.sub(r"\.service$", "", u)[len("snc_"):]
            ndir = node_dir(home, env, show)
            port, port_from = port_for(u, env, p["port"], ndir, home, show)
            active = show.get("ActiveState", "") == "active"
            uri = properties(os.path.join(ndir, "conf", "glide.properties")).get("glide.servlet.uri", "")
            # asked at the node's own names only; the instance's name (behind a load balancer: any node) only as a Host
            # header on 127.0.0.1
            names = own_names(p["host_name"], socket.getfqdn())
            st = read_stats(port, p["stats_pages"], p["samples"], p["interval"], p["timeout"], names, p["stats_url"],
                            p["stats_auth"], (p["stats_user"], p["stats_password"]), p["validate_certs"],
                            header_names=[_url_host(uri)] if uri else [],
                            form=(p["form_user"], p["form_password"]), form_skip=form_skip, sessions=sessions) if p["stats"] and active else \
                {"status": 0, "why": "not read: the unit is not running" if p["stats"] else "not read (servicenow_node_stats false)",
                 "up": False, "sem_queued_samples": []}
            if st.get("form") == "refused" and not form_skip:
                form_skip = "%s on this node" % u.replace(".service", "")
            units.append({"unit": u, "env": env, "active": show.get("ActiveState", "unknown"), "sub": show.get("SubState", ""),
                          "enabled": show.get("UnitFileState", "unknown"), "since": show.get("ActiveEnterTimestamp", ""),
                          "node_dir": ndir, "port": port, "port_from": port_from, "stats": st,
                          "log": read_log(os.path.join(ndir, "logs"), p["log_glob"], p["log_hours"], p["log_max_mb"], p["log_groups"],
                                          sem_wait_ms=p["sem_wait_ms"])})
    finally:
        form_close(sessions, p["timeout"], p["validate_certs"])
    module.exit_json(changed=False, hostname=socket.gethostname(), units=units, missing=missing,
                     looked_for=[unit_name(u) for u in wanted], fapolicyd=fa,
                     form_refused=any((x["stats"] or {}).get("form") == "refused" for x in units),
                     disk=disks([home] + [x for x in p["paths"] if str(x).strip()] + ["/"]), memory=memory(),
                     msg="; ".join("%s %s%s" % (x["unit"], x["active"], (", stats " + ("up" if x["stats"]["up"] else "not read"))
                                                if p["stats"] else "") for x in units) or "no snc_*.service unit")


if __name__ == "__main__":
    main()
