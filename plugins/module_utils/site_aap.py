# -*- coding: utf-8 -*-
"""AAP's own API, for the site_aap_* modules (the deploy's inventory, the pick lists' surveys) and the
reports' past runs (roles/site_email: site_report_history): the address and login come from the "Red Hat
Ansible Automation Platform" credential's variables. The API path is found by asking - api/controller/v2/
(AAP 2.5 and later), else api/v2/. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import json
import os
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# the credential's variables, by their NAMES: CONTROLLER_* (AAP 2.4 and later), AAP_* (2.5 and later), TOWER_* (older)
VARS = {"host": ("CONTROLLER_HOST", "AAP_HOSTNAME", "TOWER_HOST"),
        "token": ("CONTROLLER_OAUTH_TOKEN", "AAP_TOKEN", "TOWER_OAUTH_TOKEN"),
        "user": ("CONTROLLER_USERNAME", "AAP_USERNAME", "TOWER_USERNAME"),
        "password": ("CONTROLLER_PASSWORD", "AAP_PASSWORD", "TOWER_PASSWORD"),
        "verify": ("CONTROLLER_VERIFY_SSL", "AAP_VALIDATE_CERTS", "TOWER_VERIFY_SSL")}
NO_CREDENTIAL = 'No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template'


def connection(env=None):
    """AAP's address and login from the credential's variables (the first name set of each)."""
    env = os.environ if env is None else env
    c = {}
    for k, names in VARS.items():
        c[k] = next((str(env.get(n)).strip() for n in names if str(env.get(n) or "").strip()), "")
    h = c["host"]
    c["host"] = ((h if h.startswith("http") else "https://" + h).rstrip("/")) if h else ""
    c["verify"] = c["verify"].lower() not in ("0", "false", "no", "off") if c["verify"] else True
    c["login"] = "token" if c["token"] else ("password" if c["user"] and c["password"] else "")
    return c


def error_text(data):
    """AAP's reason, from its JSON answer: {"detail": ...} or {"field": ["reason"]}."""
    if not isinstance(data, dict):
        return str(data or "")[:200]
    if data.get("detail"):
        return str(data["detail"])
    parts = []
    for k, v in sorted(data.items()):
        v = v[0] if isinstance(v, list) and v else v
        parts.append("%s: %s" % (k, v))
    return "; ".join(parts)[:300]


class Api(object):
    """ca_setting: the setting that names the CA file, for the messages; no_credential: what to say
    when the job has no AAP credential (which role its user needs, on what)."""

    def __init__(self, conn, ca_path="", timeout=30, ca_setting="the CA path", no_credential=NO_CREDENTIAL):
        self.conn, self.ca_path, self.timeout, self.base, self.ca_error = conn, ca_path, timeout, "", ""
        self.user = ""                                     # the login's user name, once probe() has asked
        self.ca_setting, self.no_credential = ca_setting, no_credential
        if ca_path and conn["verify"] and not os.path.isfile(ca_path):
            self.ca_error = "the CA file %s (%s) is not there" % (ca_path, ca_setting)
            ca_path = ""
        if not conn["verify"]:
            self.ctx = ssl._create_unverified_context()   # noqa: S323 - the credential says so
        elif ca_path:
            self.ctx = ssl.create_default_context(cafile=ca_path)
        else:
            self.ctx = ssl.create_default_context()

    def call(self, method, path, body=None):
        """(HTTP status, JSON answer); status -1 = no answer (the address, a firewall, the certificate)."""
        req = Request(self.conn["host"] + "/" + self.base + path, data=json.dumps(body).encode() if body is not None else None)
        req.get_method = lambda: method
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.conn["login"] == "token":
            req.add_header("Authorization", "Bearer " + self.conn["token"])
        else:
            pair = ("%s:%s" % (self.conn["user"], self.conn["password"])).encode()
            req.add_header("Authorization", "Basic " + base64.b64encode(pair).decode())
        try:
            r = urlopen(req, timeout=self.timeout, context=self.ctx if self.conn["host"].startswith("https") else None)
            raw = r.read()
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                return -1, {"detail": "the answer to %s %s is not AAP's (HTTP %d, not JSON): the credential's URL must be AAP's own address"
                                      % (method, path.split("?")[0], r.getcode())}
            return r.getcode(), data if isinstance(data, dict) else {"results": data}
        except HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"detail": "HTTP %d" % e.code}
            finally:
                e.close()
        except (URLError, socket.timeout, ssl.SSLError, OSError) as e:
            return -1, {"detail": str(getattr(e, "reason", e))}

    def probe(self):
        """Find the API path (api/controller/v2/ first) and check the login: '' or why not."""
        if not self.conn["host"] or not self.conn["login"]:
            return self.no_credential
        if self.ca_error:
            return self.ca_error
        for base in ("api/controller/v2/", "api/v2/"):
            self.base = base
            code, data = self.call("GET", "me/")
            if code != 404:
                break
        if code == 200:
            me = (data.get("results") or [{}])[0] if isinstance(data.get("results"), list) else data
            self.user = str((me if isinstance(me, dict) else {}).get("username") or "")
            return ""
        if code < 0:
            return ("AAP did not answer at %s: %s (the address, a firewall, or its certificate - %s)"
                    % (self.conn["host"], error_text(data), self.ca_setting))
        if code in (401, 403):
            return "AAP refused the login (HTTP %d): the credential's user name and password, or its token, are wrong or expired" % code
        if code == 404:
            return "AAP has no API at %s/api/controller/v2/ or /api/v2/ (HTTP 404): the credential's URL must be AAP's own address" % self.conn["host"]
        return "AAP answered HTTP %d: %s" % (code, error_text(data))
