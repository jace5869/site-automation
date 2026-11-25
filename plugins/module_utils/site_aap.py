# -*- coding: utf-8 -*-
"""AAP's own API, for the site_aap_* modules (the deploy's inventory, the pick lists' surveys) and the
reports' past runs (roles/site_email: site_report_history): the address and login come from the "Red Hat
Ansible Automation Platform" credential's variables. The API path is found by asking - api/controller/v2/
(AAP 2.5 and later), else api/v2/. Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import base64
import http.client
import json
import os
import socket
import ssl
import time
from concurrent.futures import Future, TimeoutError as FutureTimeout
from threading import Lock, Thread, local
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, Request, build_opener

# the credential's variables, by their NAMES: CONTROLLER_* (AAP 2.4 and later), AAP_* (2.5 and later), TOWER_* (older)
VARS = {"host": ("CONTROLLER_HOST", "AAP_HOSTNAME", "TOWER_HOST"),
        "token": ("CONTROLLER_OAUTH_TOKEN", "AAP_TOKEN", "TOWER_OAUTH_TOKEN"),
        "user": ("CONTROLLER_USERNAME", "AAP_USERNAME", "TOWER_USERNAME"),
        "password": ("CONTROLLER_PASSWORD", "AAP_PASSWORD", "TOWER_PASSWORD"),
        "verify": ("CONTROLLER_VERIFY_SSL", "AAP_VALIDATE_CERTS", "TOWER_VERIFY_SSL")}
NO_CREDENTIAL = 'No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template'


class _NoRedirect(HTTPRedirectHandler):
    """Follows no redirect: the 3xx answer comes back as an HTTPError (urllib would send the Authorization header on)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_read_context = local()


class ReadDeadline:
    """One wall-clock budget for a background inventory lookup, HTTP response and parsing."""

    def __init__(self, seconds):
        self.end = time.monotonic() + seconds
        self.lock = Lock()
        self.sock = None

    def remaining(self):
        left = self.end - time.monotonic()
        if left <= 0:
            raise TimeoutError("inventory read deadline exceeded")
        return left

    def watch(self, sock):
        with self.lock:
            self.remaining()
            self.sock = sock

    def run(self, read, *args):
        # Only the caller publishes the result. A late DNS lookup or parser cannot replace a retry.
        result = Future()

        def worker():
            _read_context.deadline = self
            try:
                result.set_result(read(*args))
            except Exception as exc:
                result.set_exception(exc)
            finally:
                del _read_context.deadline

        Thread(target=worker, daemon=True).start()
        try:
            value = result.result(timeout=self.remaining())
        except FutureTimeout:
            if time.monotonic() < self.end:
                raise  # A shorter socket timeout from the worker.
            with self.lock:
                if self.sock is not None:
                    # Interrupt even a peer that trickles headers or body bytes inside every socket timeout.
                    try:
                        self.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            raise TimeoutError("inventory read deadline exceeded") from None
        self.remaining()
        return value


class _DeadlineHandler(HTTPHandler, HTTPSHandler):
    """Capture the connected socket before urllib starts reading the response headers."""
    def __init__(self, deadline, context):
        HTTPSHandler.__init__(self, context=context)
        self.deadline = deadline

    def do_open(self, http_class, req, **kwargs):
        deadline = self.deadline

        class Connection(http_class):
            def getresponse(self):
                deadline.watch(self.sock)
                return super().getresponse()

        return super().do_open(Connection, req, **kwargs)


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

    def call(self, method, path, body=None, base=None):
        """(HTTP status, JSON answer); status -1 = no answer (the address, a firewall, the certificate)."""
        req = Request(self.conn["host"] + "/" + (self.base if base is None else base) + path, data=json.dumps(body).encode() if body is not None else None)
        req.get_method = lambda: method
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if self.conn["login"] == "token":
            req.add_header("Authorization", "Bearer " + self.conn["token"])
        else:
            pair = ("%s:%s" % (self.conn["user"], self.conn["password"])).encode()
            req.add_header("Authorization", "Basic " + base64.b64encode(pair).decode())
        # a redirect is an answer, never a path to follow: followed, the Authorization header would go wherever it
        # points (a proxy's login page, another host, plain http)
        deadline = getattr(_read_context, "deadline", None)
        handlers = [_NoRedirect()]
        if deadline:
            handlers.append(_DeadlineHandler(deadline, self.ctx))
        elif self.conn["host"].startswith("https"):
            handlers.append(HTTPSHandler(context=self.ctx))
        opener = build_opener(*handlers)
        try:
            timeout = min(self.timeout, deadline.remaining()) if deadline else self.timeout
            with opener.open(req, timeout=timeout) as r:
                raw, status = r.read(), r.getcode()
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                return -1, {"detail": "the answer to %s %s is not AAP's (HTTP %d, not JSON): the credential's URL must be AAP's own address"
                                      % (method, path.split("?")[0], status)}
            return status, data if isinstance(data, dict) else {"results": data}
        except HTTPError as e:
            try:
                if 300 <= e.code < 400:
                    return e.code, {"detail": "HTTP %d: AAP's address answered a redirect to %s, not followed (the login would go there). "
                                              "The credential's URL must be AAP's own address" % (e.code, e.headers.get("Location", "?"))}
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {"detail": "HTTP %d" % e.code}
            finally:
                e.close()
        except (URLError, socket.timeout, ssl.SSLError, OSError) as e:
            return -1, {"detail": str(getattr(e, "reason", e))}
        except http.client.HTTPException as e:          # IncompleteRead: the answer cut off on its way (a large job's artifacts)
            return -1, {"detail": "the answer was cut off: %s" % (e.__class__.__name__ if isinstance(e, http.client.IncompleteRead) else e)}

    def probe(self):
        """Find the API path (api/controller/v2/ first) and check the login: '' or why not."""
        self.probe_status = None
        if not self.conn["host"] or not self.conn["login"]:
            self.base = ""
            return self.no_credential
        if self.ca_error:
            self.base = ""
            return self.ca_error
        for base in ("api/controller/v2/", "api/v2/"):
            code, data = self.call("GET", "me/", base=base)
            if code != 404:
                break
        self.probe_status = code
        if code == 200:
            me = (data.get("results") or [{}])[0] if isinstance(data.get("results"), list) else data
            self.user = str((me if isinstance(me, dict) else {}).get("username") or "")
            self.base = base
            return ""
        self.base = ""
        if code < 0:
            return ("AAP did not answer at %s: %s (the address, a firewall, or its certificate - %s)"
                    % (self.conn["host"], error_text(data), self.ca_setting))
        if code in (401, 403):
            return "AAP refused the login (HTTP %d): the credential's user name and password, or its token, are wrong or expired" % code
        if code == 404:
            return "AAP has no API at %s/api/controller/v2/ or /api/v2/ (HTTP 404): the credential's URL must be AAP's own address" % self.conn["host"]
        return "AAP answered HTTP %d: %s" % (code, error_text(data))


def portal_inventory_warnings(read_all, username, targets, users=None):
    """Check (template name or None for all Windows templates, inventory ID, inventory name) targets."""
    if not targets:
        return []
    if not username:
        return ["Portal inventory access was not checked because the portal account name is unset."]
    if users is None:
        try:
            users = read_all("users/?username=%s" % quote(username, safe=""))
        except RuntimeError as exc:
            return ["Portal inventory access was not checked for %s: %s" % (username, exc)]
    matches = [u for u in users if u.get("username") == username]
    if len(matches) != 1:
        return ["Portal inventory access was not checked because AAP could not identify the portal account %s." % username]
    user = matches[0]
    if user.get("is_superuser") or user.get("is_system_auditor"):
        return []

    warnings = []
    by_inventory = {}
    for template, iid, name in targets:
        by_inventory.setdefault(iid, []).append((template, name))
    for iid, cards in by_inventory.items():
        name = next((name for _, name in cards if name), None)
        try:
            # AAP lists users with effective access, including grants inherited from teams and the inventory's organization.
            access = read_all("inventories/%d/access_list/?id=%d" % (iid, user["id"]))
            if any(row.get("id") == user["id"] for row in access):
                continue
        except RuntimeError as exc:
            warnings.append("Portal inventory access was not checked for %s on inventory %s: %s" % (username, name or iid, exc))
            continue
        if not name:
            try:
                rows = read_all("inventories/?id=%d" % iid)
                name = next((r.get("name") for r in rows if r.get("id") == iid), None)
            except RuntimeError:
                pass  # Access is known even if the setup account cannot resolve the display name.
            name = name or "ID %d" % iid
        for template, _ in cards:
            card = "its card stays hidden" if template else "their cards stay hidden"
            warnings.append("%s: the portal's account %s cannot read inventory %s - %s; give it Read on that inventory"
                            % (template or "Windows templates", username, name, card))
    return warnings


# 1.79.11: a template's or workflow's description in five short labeled lines (aap/job_templates.yml's description, and the
# Contact line from the site's settings only) - written by AAP - set up job templates (aap_templates_describe) and by
# AAP - set up portal actions on its own templates and workflows
DESCRIBED_LABELS = (("does", "Does"), ("targets", "Targets"), ("produces", "Produces"), ("supports", "Supports"))
DESCRIBED_LINE_MAX = 160


def described_text(desc, owner="", contact=""):
    """The lines ("** Does: ..."), each at most 160 characters; the Contact line only with an owner or a contact; '' when
    desc has no Does."""
    def one(v):
        return " ".join(str(v or "").split())
    if not isinstance(desc, dict) or not one(desc.get("does")):
        return ""
    lines = [("** %s: %s" % (label, one(desc.get(key))))[:DESCRIBED_LINE_MAX] for key, label in DESCRIBED_LABELS if one(desc.get(key))]
    who = ", ".join(x for x in (("Owner=" + one(owner)) if one(owner) else "", ("Developer=" + one(contact)) if one(contact) else "") if x)
    if who:
        lines.append(("** Contact: " + who)[:DESCRIBED_LINE_MAX])
    return "\n".join(lines)


def setup_description(describe, kind, spec):
    """The catalog description when enabled and available, otherwise the workflow spec's exact text."""
    settings = describe or {}
    entry = (settings.get(kind) or {}).get(spec["name"])
    return described_text(entry, settings.get("owner"), settings.get("contact")) or str(spec.get("description") or "")


# 1.79.19: the catalog matcher - what AAP - set up job templates' descriptions (site_aap_describe, 1.79.11) and its renames
# (site_aap_rename) both go by: a job template of the project by its playbook, a workflow by its name, else by the
# playbooks of its steps
WORKFLOW_GATE = "playbooks/workflow_gate.yml"            # in many workflows: never what tells them apart


def vars_of(text):
    """A template's Variables (JSON or YAML text) as a dict - {} when they cannot be read."""
    t = str(text or "").strip()
    if not t:
        return {}
    try:
        d = json.loads(t)
    except ValueError:
        try:
            import yaml
            d = yaml.safe_load(t)
        except Exception:                               # noqa: BLE001 - not readable: no Variables of its own
            return {}
    return d if isinstance(d, dict) else {}


def vars_have(text, want):
    """A template's Variables carry each of want's keys with its value (as set: a commented line is not set)."""
    have = vars_of(text)
    return all(k in have and str(have[k]).lower() == str(v).lower() for k, v in (want or {}).items())


def catalog_entry(t, entries):
    """The catalog entry a job template is, of the entries of its playbook: the one of its name, else (a playbook of
    several) the one whose Variables it has, else the one with none of its own - None when there is none."""
    entries = list(entries or [])
    if not entries:
        return None
    e = next((x for x in entries if x.get("name") == t.get("name")), None)
    if e is None and len(entries) > 1:
        e = next((x for x in entries if x.get("extra_vars") and vars_have(t.get("extra_vars"), x["extra_vars"])), None) \
            or next((x for x in entries if not x.get("extra_vars")), entries[0])
    return e or entries[0]


def workflow_steps(nodes, pb_of):
    """A workflow's nodes -> (Counter of its steps' playbooks - the gate aside, the number of steps of a template not of the
    project (pb_of: {template id: playbook}), whether it has an approval)."""
    import collections
    steps, foreign, approval = collections.Counter(), 0, False
    for n in nodes or []:
        uj = (n.get("summary_fields") or {}).get("unified_job_template") or {}
        if uj.get("unified_job_type") == "workflow_approval":
            approval = True
        elif uj.get("unified_job_type") == "job" and uj.get("id") is not None:
            pb = pb_of.get(int(uj["id"]))
            if pb is None:
                foreign += 1                         # a template of another project: not the site-automation workflow
            else:
                steps[pb] += 1
    steps.pop(WORKFLOW_GATE, None)
    return steps, foreign, approval


def workflow_fits(steps, approval, workflows, wants_approval=None):
    """The catalog workflows with these steps (the gate aside) - one that promises an approval only on one that has one.
    wants_approval(entry): whether it promises one (the descriptions: "approv" in its Supports line)."""
    import collections
    if wants_approval is None:
        def wants_approval(c):
            return "approv" in str((c.get("description") or {}).get("supports") or "").lower()
    fits = [c for c in workflows or [] if steps == collections.Counter(x for x in c.get("playbooks") or [] if x != WORKFLOW_GATE)]
    return [c for c in fits if approval or not wants_approval(c)]
