#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Is a service's API there right now, and how fast: each probe (a URL) asked a few times, a short pause between,
each answer timed. Read-only: GET only. Runs on the AAP side (localhost). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_http_probe
short_description: Ask a few URLs a few times each, read-only, and time every answer
description:
  - Each probe is asked C(attempts) times (GET), C(pause) seconds apart. An attempt is good when the answer's HTTP status
    is in the probe's C(expect) and - with C(json_key) - the JSON answer's value there is in C(json_expect).
  - Never fails: a probe that cannot be reached says why in its attempts.
options:
  probes:
    description: "[{name, url, group: service (the address people use), node (one server behind it)
      or dependency (what it needs, e.g. its login server) - roles/api_availability, auth: none / basic, user, password,
      expect: [200], json_key: 'currentState' (a top-level key of the JSON answer), json_expect: ['available']}]"
    type: list
    elements: dict
    required: true
  attempts: {description: Times each probe is asked., type: int, default: 3}
  pause: {description: Seconds between two attempts of one probe., type: float, default: 2}
  timeout: {description: Seconds an attempt may take., type: int, default: 10}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
author: site automation
'''

RETURN = r'''
probes:
  description: "Per probe, in order: {name, url, group, good (attempts that were good), attempts: [{ok, status (-1 = no answer),
                ms, value (json_key's value), why}], ms_median, ms_max (of the attempts that answered), why (the last bad
                attempt's reason, '' = none)}"
  type: list
  returned: always
'''

import base64
import json
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

from ansible.module_utils.basic import AnsibleModule


def context(validate, ca_path):
    if not validate:
        return ssl._create_unverified_context()   # noqa: S323 - the settings say so
    return ssl.create_default_context(cafile=ca_path) if ca_path else ssl.create_default_context()


def reason(e):
    """A failed request in plain words."""
    if isinstance(e, HTTPError):
        return "HTTP %d" % e.code
    r = getattr(e, "reason", e)
    if isinstance(r, ssl.SSLCertVerificationError):
        return "its certificate is not trusted (%s)" % (r.verify_message or r)
    if isinstance(r, (socket.timeout, TimeoutError)):
        return "no answer in time"
    if isinstance(r, ConnectionRefusedError):
        return "connection refused (nothing listens there)"
    if isinstance(r, socket.gaierror):
        return "the name does not resolve"
    return str(r)[:200]


def attempt(opener, probe, timeout):
    """{ok, status, ms, value, why} of one GET."""
    req = Request(probe["url"], headers={"Accept": "application/json"})
    if probe.get("auth") == "basic":
        pair = "%s:%s" % (probe.get("user") or "", probe.get("password") or "")
        req.add_header("Authorization", "Basic " + base64.b64encode(pair.encode()).decode())
    start = time.time()
    status, body, why = -1, b"", ""
    try:
        resp = opener.open(req, timeout=timeout)
        status, body = resp.status, resp.read(1048576)
    except HTTPError as e:
        status, why = e.code, reason(e)
        try:
            body = e.read(65536)
        except Exception:                                 # noqa: BLE001 - the status says enough
            body = b""
    except (URLError, OSError, ValueError) as e:
        why = reason(e)
    ms = int(round((time.time() - start) * 1000))
    value = None
    key = probe.get("json_key") or ""
    if key and body:
        try:
            data = json.loads(body.decode("utf-8", "replace"))
            value = data.get(key) if isinstance(data, dict) else None
        except ValueError:
            value = None
    ok = status in [int(s) for s in (probe.get("expect") or [200])]
    if ok and key:
        ok = value in (probe.get("json_expect") or [])
        if not ok:
            why = "%s is %s" % (key, json.dumps(value))
    elif not ok and status > 0 and key and value is not None:
        why = "%s (%s is %s)" % (why, key, json.dumps(value))
    return {"ok": ok, "status": status, "ms": ms, "value": value, "why": "" if ok else why}


def median(values):
    v = sorted(values)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else int(round((v[n // 2 - 1] + v[n // 2]) / 2.0))


def run(probes, attempts, pause, timeout, validate, ca_path, opener=None, sleep=time.sleep):
    opener = opener or build_opener(HTTPSHandler(context=context(validate, ca_path)))
    out = []
    for p in probes:
        tries = []
        for i in range(max(1, attempts)):
            if i:
                sleep(pause)
            tries.append(attempt(opener, p, timeout))
        answered = [t["ms"] for t in tries if t["status"] > 0]
        bad = [t for t in tries if not t["ok"]]
        out.append({"name": p.get("name") or p["url"], "url": p["url"], "group": p.get("group") or "service",
                    "good": len(tries) - len(bad), "attempts": tries, "ms_median": median(answered),
                    "ms_max": max(answered) if answered else None, "why": bad[-1]["why"] if bad else ""})
    return out


def main():
    module = AnsibleModule(argument_spec=dict(probes=dict(type="list", elements="dict", required=True),
                                              attempts=dict(type="int", default=3), pause=dict(type="float", default=2),
                                              timeout=dict(type="int", default=10), validate_certs=dict(type="bool", default=True),
                                              ca_path=dict(type="str", default="")),
                           supports_check_mode=True)
    p = module.params
    for probe in p["probes"]:
        if probe.get("password"):
            module.no_log_values.add(probe["password"])
    out = run(p["probes"], p["attempts"], p["pause"], p["timeout"], p["validate_certs"], p["ca_path"])
    module.exit_json(changed=False, probes=out,
                     msg="; ".join("%s %d/%d" % (o["name"], o["good"], len(o["attempts"])) for o in out))


if __name__ == "__main__":
    main()
