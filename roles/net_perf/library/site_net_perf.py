#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Network performance, measured on a Linux host: ping (latency, jitter, loss) and timed TCP connects to the targets,
an HTTP POST of a ready-made OCSP request to each responder (the answer is judged on the AAP side), the interfaces'
drop and error counters against the last run, and the host's own history of runs (kept on the host) for the trend.
Standard library only; nothing is changed but the history files in dir (not in check mode)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_net_perf
short_description: Latency, loss, OCSP answers and interface drops from this host, with its own history
options:
  targets: {description: "[{name, host, port}]", type: list, elements: dict, default: []}
  ping_count: {description: Pings per target., type: int, default: 20}
  ping_interval: {description: Seconds between pings., type: float, default: 0.2}
  tcp_tries: {description: Timed TCP connects per target with a port., type: int, default: 5}
  timeout: {description: Seconds one ping or connect may wait., type: int, default: 5}
  ocsp: {description: "[{name, url, request (base64 DER, '' = a plain GET)}]", type: list, elements: dict, default: []}
  nics: {description: Read the interfaces' counters., type: bool, default: true}
  nic_exclude: {description: Interfaces left out (regular expression)., type: str, default: '^lo$'}
  sysfs: {description: Where the interfaces' counters are., type: str, default: /sys/class/net}
  dir: {description: Where the history is kept (root-only)., type: str, default: /var/lib/site-automation/netperf}
  keep: {description: Runs kept., type: int, default: 30}
  save: {description: Keep this run (false - a dry run - keeps nothing)., type: bool, default: true}
author: site automation
'''

RETURN = r'''
targets:
  description: "[{name, host, port, sent, received, loss_pct, rtt_min, rtt_avg, rtt_max, rtt_mdev, tcp_ok, tcp_fail, tcp_avg_ms, why}]"
  type: list
  returned: always
ocsp:
  description: "[{name, url, code, ms, body (base64), why}]"
  type: list
  returned: always
nics:
  description: "[{name, hours, rx_dropped, tx_dropped, errors, first (no last run to compare)}]: the counts since the last run"
  type: list
  returned: always
history:
  description: The kept runs before this one, oldest first ([{time, rtt: {target: avg ms}}]).
  type: list
  returned: always
'''

import base64
import glob
import json
import os
import re
import socket
import subprocess
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib import error as urlerror
from ansible.module_utils.six.moves.urllib import request as urlrequest

LOSS_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received.*?([\d.]+)% packet loss")
RTT_RE = re.compile(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms")
STATS = ("rx_dropped", "tx_dropped", "rx_errors", "tx_errors", "rx_crc_errors", "rx_frame_errors", "tx_carrier_errors")


def parse_ping(out):
    """ping's summary -> {sent, received, loss_pct, rtt_min, rtt_avg, rtt_max, rtt_mdev} (rtt None when nothing came back)."""
    r = {"sent": 0, "received": 0, "loss_pct": 100.0, "rtt_min": None, "rtt_avg": None, "rtt_max": None, "rtt_mdev": None}
    m = LOSS_RE.search(out.replace("\n", " "))
    if m:
        r["sent"], r["received"], r["loss_pct"] = int(m.group(1)), int(m.group(2)), float(m.group(3))
    m = RTT_RE.search(out)
    if m:
        r["rtt_min"], r["rtt_avg"], r["rtt_max"], r["rtt_mdev"] = (float(x) for x in m.groups())
    return r


def ping(host, count, interval, timeout):
    cmd = ["ping", "-n", "-q", "-c", str(count), "-i", str(interval), "-W", str(timeout), host]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=count * (interval + timeout) + 10)
    except FileNotFoundError:
        return dict(parse_ping(""), why="ping is not installed")
    except subprocess.TimeoutExpired:
        return dict(parse_ping(""), why="ping did not finish")
    out = p.stdout.decode("utf-8", "replace")
    r = parse_ping(out)
    if not r["sent"]:
        r["why"] = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["no answer"])[-1][:200]
    return r


def tcp(host, port, tries, timeout):
    ok, ms, why = 0, [], ""
    for _ in range(tries):
        t0 = time.time()
        try:
            s = socket.create_connection((host, int(port)), timeout=timeout)
            ms.append((time.time() - t0) * 1000.0)
            s.close()
            ok += 1
        except (OSError, socket.timeout) as e:
            why = str(e)[:200]
    return {"tcp_ok": ok, "tcp_fail": tries - ok, "tcp_avg_ms": round(sum(ms) / len(ms), 2) if ms else None, "tcp_why": why}


def ocsp_post(url, request_b64, timeout):
    rec = {"code": 0, "ms": 0, "body": "", "why": ""}
    t0 = time.time()
    try:
        if request_b64:
            req = urlrequest.Request(url, data=base64.b64decode(request_b64),
                                     headers={"Content-Type": "application/ocsp-request", "Accept": "application/ocsp-response"})
        else:
            req = urlrequest.Request(url)
        resp = urlrequest.urlopen(req, timeout=timeout)
        rec["code"], body = resp.getcode(), resp.read(65536)
        rec["body"] = base64.b64encode(body).decode("ascii") if request_b64 else ""
    except urlerror.HTTPError as e:
        rec["code"] = e.code
    except (urlerror.URLError, OSError) as e:
        rec["why"] = "no answer: %s" % getattr(e, "reason", e)
    rec["ms"] = int((time.time() - t0) * 1000)
    return rec


def read_counters(exclude, sysfs="/sys/class/net"):
    out = {}
    rx = re.compile(exclude) if exclude else None
    for d in sorted(glob.glob(os.path.join(sysfs, "*", "statistics"))):
        name = d.split("/")[-2]
        if rx and rx.search(name):
            continue
        c = {}
        for s in STATS:
            try:
                with open(os.path.join(d, s)) as f:
                    c[s] = int(f.read().strip() or 0)
            except (IOError, OSError, ValueError):
                pass
        out[name] = c
    return out


def nic_deltas(now_c, last, now_t):
    """The counts since the last run; a counter that went down (a reboot, a driver reload) starts over: no count."""
    out = []
    last_t = (last or {}).get("time")
    for name, c in sorted(now_c.items()):
        prev = ((last or {}).get("counters") or {}).get(name)
        if not prev or last_t is None:
            out.append({"name": name, "first": True, "hours": 0})
            continue
        d = {k: c.get(k, 0) - prev.get(k, 0) for k in c}
        if any(v < 0 for v in d.values()):
            out.append({"name": name, "first": True, "hours": 0, "reset": True})
            continue
        hours = max((now_t - last_t) / 3600.0, 1e-6)
        out.append({"name": name, "first": False, "hours": round(hours, 2), "rx_dropped": d.get("rx_dropped", 0),
                    "tx_dropped": d.get("tx_dropped", 0),
                    "errors": sum(d.get(k, 0) for k in ("rx_errors", "tx_errors")),
                    "detail": {k: v for k, v in d.items() if v}})
    return out


class Run(object):
    def __init__(self, module):
        self.m, self.p = module, module.params

    def go(self):
        p = self.p
        now_t = time.time()
        ts = []
        for t in p["targets"]:
            r = {"name": t.get("name") or t.get("host"), "host": t.get("host", ""), "port": t.get("port") or 0}
            r.update(ping(r["host"], p["ping_count"], p["ping_interval"], p["timeout"]))
            if r["port"]:
                r.update(tcp(r["host"], r["port"], p["tcp_tries"], p["timeout"]))
            ts.append(r)
        oc = []
        for x in p["ocsp"]:
            r = {"name": x.get("name") or x.get("url"), "url": x.get("url", ""), "mode": "request" if x.get("request") else "reach"}
            r.update(ocsp_post(r["url"], x.get("request") or "", p["timeout"] * 2))
            oc.append(r)
        d = p["dir"]
        last, hist = {}, []
        try:
            with open(os.path.join(d, "counters.json")) as f:
                last = json.load(f)
        except (IOError, OSError, ValueError):
            pass
        try:
            with open(os.path.join(d, "history.jsonl")) as f:
                hist = [json.loads(line) for line in f if line.strip()]
        except (IOError, OSError, ValueError):
            hist = []
        counters = read_counters(p["nic_exclude"], p["sysfs"]) if p["nics"] else {}
        nics = nic_deltas(counters, last, now_t) if p["nics"] else []
        if p["save"] and not self.m.check_mode:
            self.keep(d, now_t, counters, hist, ts)
        return dict(targets=ts, ocsp=oc, nics=nics, history=hist[-p["keep"]:], time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_t)))

    def keep(self, d, now_t, counters, hist, ts):
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        old = os.umask(0o077)
        try:
            if counters:
                with open(os.path.join(d, "counters.json.tmp"), "w") as f:
                    json.dump({"time": now_t, "counters": counters}, f)
                os.rename(os.path.join(d, "counters.json.tmp"), os.path.join(d, "counters.json"))
            rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_t)),
                   "rtt": {t["name"]: t["rtt_avg"] for t in ts if t.get("rtt_avg") is not None},
                   "loss": {t["name"]: t["loss_pct"] for t in ts}}
            lines = [json.dumps(h, sort_keys=True) for h in (hist + [rec])[-self.p["keep"]:]]
            with open(os.path.join(d, "history.jsonl.tmp"), "w") as f:
                f.write("\n".join(lines) + "\n")
            os.rename(os.path.join(d, "history.jsonl.tmp"), os.path.join(d, "history.jsonl"))
        finally:
            os.umask(old)


def main():
    module = AnsibleModule(argument_spec=dict(
        targets=dict(type="list", elements="dict", default=[]),
        ping_count=dict(type="int", default=20),
        ping_interval=dict(type="float", default=0.2),
        tcp_tries=dict(type="int", default=5),
        timeout=dict(type="int", default=5),
        ocsp=dict(type="list", elements="dict", default=[]),
        nics=dict(type="bool", default=True),
        nic_exclude=dict(type="str", default="^lo$"),
        sysfs=dict(type="str", default="/sys/class/net"),
        dir=dict(type="str", default="/var/lib/site-automation/netperf"),
        keep=dict(type="int", default=30),
        save=dict(type="bool", default=True),
    ), supports_check_mode=True)
    module.exit_json(changed=False, **Run(module).go())


if __name__ == "__main__":
    main()
