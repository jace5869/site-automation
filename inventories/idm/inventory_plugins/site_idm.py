# -*- coding: utf-8 -*-
# The hosts enrolled in Red Hat IdM (FreeIPA) and its host groups, as an Ansible inventory: AAP's inventory source
# "Sourced from a Project" with inventories/idm/idm.yml (docs/IDM_INVENTORY.md). Here, next to idm.yml, because AAP
# runs the sync with --playbook-dir inventories/idm (ansible.cfg's inventory_plugins points here too). Only reads IdM
# (host_find, hostgroup_find over its JSON-RPC API, as a user that may read hosts - any IdM user can).
from __future__ import annotations

DOCUMENTATION = r"""
name: site_idm
short_description: The hosts enrolled in Red Hat IdM and its host groups
description:
  - Reads the hosts and host groups of Red Hat Identity Management (IdM, FreeIPA) over its JSON-RPC API (host_find,
    hostgroup_find - only reads) and makes each enrolled host an inventory host, named by its FQDN.
  - Each IdM host group becomes a group of the same name (characters other than letters, digits and _ made _),
    nested as in IdM; I(group_map) puts the hosts of an IdM host group in groups of other names as well (the
    runbooks' aap_hosts, rhel9_all ...).
  - Every option can come from an environment variable (IDM_...) - in AAP from the credential (server, user, password,
    CA) and from the inventory source's Source variables box (the rest); a value written in the inventory file wins.
  - A sync that would be wrong stops instead (AAP then keeps the inventory as it was) - IdM not reached, the login
    refused, a list cut short by IdM's search limit, fewer hosts than I(min_hosts), a group of I(require_groups) empty.
extends_documentation_fragment:
  - constructed
options:
  plugin:
    description: This file is for this plugin.
    required: true
    choices: [site_idm]
  server:
    description: An IdM server's FQDN (idm1.example.mil), host:port, or https://host[:port].
    type: str
    env: [{name: IDM_SERVER}]
  user:
    description: The IdM user that reads (a plain user is enough - every IdM user may read hosts and host groups).
    type: str
    env: [{name: IDM_USER}]
  password:
    description: Its password.
    type: str
    env: [{name: IDM_PASSWORD}]
  ca_path:
    description: IdM's CA certificate (PEM, /etc/ipa/ca.crt on any IdM client) - else the system's trust.
    type: str
    default: ''
    env: [{name: IDM_CA_PATH}]
  validate_certs:
    description: Check IdM's certificate.
    type: bool
    default: true
    env: [{name: IDM_VALIDATE_CERTS}]
  timeout:
    description: Seconds for each request.
    type: int
    default: 60
    env: [{name: IDM_TIMEOUT}]
  enrolled_only:
    description: Only hosts enrolled in IdM (they have a keytab - ipa-client-install ran); a host entry made by hand
      for a name only (a service, a load balancer) is left out.
    type: bool
    default: true
    env: [{name: IDM_ENROLLED_ONLY}]
  include_hostgroups:
    description: Only the hosts in one of these IdM host groups (directly or through a nested group); names or
      patterns (* and ?). Empty, every host.
    type: raw
    default: []
    env: [{name: IDM_INCLUDE_HOSTGROUPS}]
  exclude_hostgroups:
    description: Leave out the hosts in one of these IdM host groups (directly or nested); names or patterns.
    type: raw
    default: []
    env: [{name: IDM_EXCLUDE_HOSTGROUPS}]
  include_hosts:
    description: Only these hosts (FQDN; patterns * and ?). Empty, every host.
    type: raw
    default: []
    env: [{name: IDM_INCLUDE_HOSTS}]
  exclude_hosts:
    description: Leave out these hosts (FQDN; patterns).
    type: raw
    default: []
    env: [{name: IDM_EXCLUDE_HOSTS}]
  group_map:
    description:
      - "IdM host group (name or pattern) to the group(s) its hosts (directly or nested) also go in:
        {'rhel9-servers': 'rhel9_all', 'aap-servers': ['aap_hosts', 'no_patch']}."
    type: raw
    default: {}
    env: [{name: IDM_GROUP_MAP}]
  idm_groups:
    description: Also make each IdM host group a group of its own name. False, only the groups of I(group_map) (and
      of I(groups), I(keyed_groups)).
    type: bool
    default: true
    env: [{name: IDM_GROUPS}]
  group_prefix:
    description: Put before the name of each group made from an IdM host group (not before I(group_map)'s).
    type: str
    default: ''
    env: [{name: IDM_GROUP_PREFIX}]
  hostname:
    description: The inventory host's name - fqdn, or short (the first label; then ansible_host is the FQDN).
    type: str
    default: fqdn
    choices: [fqdn, short]
    env: [{name: IDM_HOSTNAME}]
  min_hosts:
    description: Fewer hosts than this, the sync stops (a wrong filter or a user that sees nothing would else empty
      the inventory).
    type: int
    default: 1
    env: [{name: IDM_MIN_HOSTS}]
  require_groups:
    description: These groups must have a host, else the sync stops (the safety groups - aap_hosts - never lost to a
      renamed IdM host group).
    type: raw
    default: []
    env: [{name: IDM_REQUIRE_GROUPS}]
"""

EXAMPLES = r"""
# inventories/idm/idm.yml - everything else from the credential and the Source variables
plugin: site_idm
"""

import ast
import fnmatch
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from ansible.errors import AnsibleError
from ansible.module_utils.common.text.converters import to_native
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable
from ansible.utils.display import Display
from ansible.utils.unsafe_proxy import wrap_var

try:
    import yaml
except ImportError:                     # always there with ansible-core
    yaml = None

display = Display()

# what each host gets: inventory variable <- IdM attribute
HOST_VARS = (("idm_description", "description"), ("idm_locality", "l"), ("idm_location", "nshostlocation"),
             ("idm_platform", "nshardwareplatform"), ("idm_os", "nsosversion"))
RESERVED = ("all", "ungrouped")


class IdmError(Exception):
    pass


def as_value(v, want):
    """An option as written in the file, or from an environment variable as AAP's Source variables box passes it
    (str() of a list or dict: Python's quotes - read as YAML or a Python literal), or a comma-separated string."""
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return want()
        for load in (ast.literal_eval, getattr(yaml, "safe_load", None), json.loads):
            if load is None:
                continue
            try:
                got = load(s)
            except Exception:
                continue
            if isinstance(got, (list, dict)):
                v = got
                break
        else:
            v = [x.strip() for x in s.split(",") if x.strip()] if want is list else v
    if v is None:
        return want()
    if want is list:
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, (list, tuple)):
            raise IdmError("not a list: %r" % (v,))
        return [str(x) for x in v if str(x).strip()]
    if not isinstance(v, dict):
        raise IdmError("not a dictionary ({idm host group: group}): %r" % (v,))
    out = {}
    for k, val in v.items():
        out[str(k)] = [str(x) for x in (val if isinstance(val, (list, tuple)) else [val]) if str(x).strip()]
    return out


def safe_group(name, prefix=""):
    g = re.sub(r"[^A-Za-z0-9_]", "_", prefix + name)
    if g[:1].isdigit() or g in RESERVED:
        g = "idm_" + g
    return g


def matches(name, patterns):
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p.lower()) for p in patterns)


def matches_any(pattern, names):
    return any(fnmatch.fnmatchcase(n.lower(), pattern.lower()) for n in names)


def first(entry, attr, default=None):
    v = entry.get(attr)
    if isinstance(v, list):
        return v[0] if v else default
    return default if v is None else v


def base_url(server):
    s = (server or "").strip().rstrip("/")
    if not s:
        raise IdmError("no IdM server - set server (IDM_SERVER, the credential's IdM server)")
    if "://" not in s:
        s = "https://" + s
    u = urllib.parse.urlsplit(s)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise IdmError("server %r is not an IdM server's name (idm1.example.mil)" % server)
    path = u.path if u.path.endswith("/ipa") else u.path + "/ipa"
    url = "%s://%s%s" % (u.scheme, u.netloc, path)
    # IdM refuses a request whose Referer is not https://<its own name>/ipa - the name without a port
    return url, "https://%s/ipa" % u.hostname


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


class Idm:
    def __init__(self, server, user, password, ca_path="", validate_certs=True, timeout=60):
        self.url, self.referer = base_url(server)
        self.user, self.password, self.timeout = user, password, timeout
        if ca_path and not os.path.isfile(ca_path):
            raise IdmError("ca_path %s: no such file" % ca_path)
        if ca_path and not open(ca_path, "rb").read().strip():
            ca_path = ""                # the credential's CA left empty: AAP still writes the (empty) file
        try:
            ctx = ssl.create_default_context(cafile=ca_path or None)
        except (ssl.SSLError, OSError) as e:
            raise IdmError("ca_path %s is not a CA certificate in PEM: %s" % (ca_path, e))
        if not validate_certs:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), NoRedirect())
        self.cookie = ""

    def _post(self, path, body, ctype):
        req = urllib.request.Request(self.url + path, data=body, method="POST")
        req.add_header("Referer", self.referer)
        req.add_header("Content-Type", ctype)
        req.add_header("Accept", "application/json" if "json" in ctype else "text/plain")
        if self.cookie:
            req.add_header("Cookie", self.cookie)
        try:
            return self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if 300 <= e.code < 400:     # IdM's web server sends a name other than its own to its own name
                raise IdmError("IdM at %s answered with a redirect to %s - set server to the IdM server's own FQDN"
                               " (not an alias, an address or a port forward)" % (self.url, e.headers.get("Location")))
            raise

    def login(self):
        if not self.user or not self.password:
            raise IdmError("no IdM user or password - attach the IdM inventory credential (IDM_USER, IDM_PASSWORD)")
        body = urllib.parse.urlencode({"user": self.user, "password": self.password}).encode()
        try:
            r = self._post("/session/login_password", body, "application/x-www-form-urlencoded")
        except urllib.error.HTTPError as e:
            why = e.headers.get("X-IPA-Rejection-Reason", "") if e.headers else ""
            hint = {"password-expired": " - the password has expired (a new IdM user's first password is expired"
                                        " too): set a new one (kinit %s) and put it in the credential" % self.user,
                    "invalid-password": " - the user name or password is wrong",
                    "denied": " - the user may not log in (disabled, or locked out)"}.get(why, "")
            raise IdmError("IdM refused the login of %s (HTTP %s%s)%s" % (
                self.user, e.code, ", " + why if why else "", hint))
        except (urllib.error.URLError, OSError) as e:
            raise IdmError("IdM at %s not reached: %s" % (self.url, to_native(getattr(e, "reason", e))))
        for h in r.headers.get_all("Set-Cookie") or []:
            if h.startswith("ipa_session="):
                self.cookie = h.split(";", 1)[0]
        if not self.cookie:
            raise IdmError("IdM at %s answered the login without a session" % self.url)

    def call(self, method, options):
        body = json.dumps({"method": method, "params": [[], options], "id": 0}).encode()
        try:
            r = self._post("/session/json", body, "application/json")
            data = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise IdmError("IdM's %s: HTTP %s %s" % (method, e.code, e.reason))
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise IdmError("IdM's %s: %s" % (method, to_native(getattr(e, "reason", e))))
        if not isinstance(data, dict):                  # a proxy's or WAF's page, not IdM's JSON-RPC answer
            raise IdmError("IdM's %s: the answer is not IdM's (not a JSON object) - is the URL IdM's own address?" % method)
        err = data.get("error")
        if err:
            raise IdmError("IdM's %s: %s" % (method, err.get("message") if isinstance(err, dict) else err))
        res = data.get("result") or {}
        if res.get("truncated"):
            raise IdmError("IdM's %s returned only %s - a limit of IdM's directory cut the list short (this user's"
                           " nsSizeLimit or nsTimeLimit, or the server's nsslapd-sizelimit): ask the IdM admin to"
                           " raise it for this user; the inventory left as it was" % (method, res.get("count")))
        return res.get("result") or []


def build(hosts, hostgroups, o):
    """IdM's host_find and hostgroup_find -> {"hosts": {name: vars}, "groups": {group: {"hosts": [], "children": []}},
    "left": {why: [fqdn]}, "unused": [pattern]}. o: the options, as read (as_value)."""
    parents = {}                                    # IdM host group -> its IdM parent groups
    for g in hostgroups:
        cn = first(g, "cn", "")
        for child in g.get("member_hostgroup") or []:
            parents.setdefault(child, set()).add(cn)

    def above(groups):
        seen, todo = set(), list(groups)
        while todo:
            g = todo.pop()
            if g not in seen:
                seen.add(g)
                todo.extend(parents.get(g, ()))
        return seen

    out = {"hosts": {}, "groups": {}, "left": {}, "unused": []}
    used_map = set()

    def group(name):
        return out["groups"].setdefault(name, {"hosts": set(), "children": set()})

    def leave(why, fqdn):
        out["left"].setdefault(why, []).append(fqdn)

    for h in sorted(hosts, key=lambda x: first(x, "fqdn", "")):
        fqdn = first(h, "fqdn", "")
        if not fqdn:
            continue
        direct = list(h.get("memberof_hostgroup") or [])
        mine = above(direct + list(h.get("memberofindirect_hostgroup") or []))
        enrolled = bool(h.get("has_keytab") or h.get("krblastpwdchange"))
        if o["enrolled_only"] and not enrolled:
            leave("not enrolled (no keytab)", fqdn)
            continue
        if o["include_hosts"] and not matches(fqdn, o["include_hosts"]):
            leave("not in include_hosts", fqdn)
            continue
        if matches(fqdn, o["exclude_hosts"]):
            leave("exclude_hosts", fqdn)
            continue
        if o["include_hostgroups"] and not any(matches(g, o["include_hostgroups"]) for g in mine):
            leave("not in include_hostgroups", fqdn)
            continue
        if any(matches(g, o["exclude_hostgroups"]) for g in mine):
            leave("exclude_hostgroups", fqdn)
            continue
        name = fqdn.split(".")[0] if o["hostname"] == "short" else fqdn
        if name in out["hosts"]:
            leave("a second host named %s (hostname: short)" % name, fqdn)
            continue
        hv = {"idm_fqdn": fqdn, "idm_hostgroups": sorted(mine), "idm_enrolled": enrolled}
        for var, attr in HOST_VARS:
            hv[var] = first(h, attr, "")
        if o["hostname"] == "short":
            hv["ansible_host"] = fqdn
        out["hosts"][name] = hv
        if o["idm_groups"]:
            for g in direct:
                group(safe_group(g, o["group_prefix"]))["hosts"].add(name)
        for pat, targets in o["group_map"].items():
            if matches_any(pat, mine):
                used_map.add(pat)
                for t in targets:
                    group(t)["hosts"].add(name)
    if o["idm_groups"]:                             # IdM's nesting, for the groups with a host below them
        grown = True
        while grown:
            grown = False
            for child, ps in parents.items():
                c = safe_group(child, o["group_prefix"])
                if c in out["groups"]:
                    for p in ps:
                        pg = safe_group(p, o["group_prefix"])
                        # two IdM names that become one Ansible name (web-servers, web_servers): never a group inside itself
                        if c != pg and c not in group(pg)["children"]:
                            group(pg)["children"].add(c)
                            grown = True
    known = [first(g, "cn", "") for g in hostgroups]
    out["unused"] = ["group_map %r matches no IdM host group with a host here" % p for p in o["group_map"] if p not in used_map]
    out["unused"] += ["include_hostgroups %r matches no IdM host group" % p for p in o["include_hostgroups"]
                      if not matches_any(p, known)]
    return out


def check(out, o, server):
    n = len(out["hosts"])
    if n < o["min_hosts"]:
        left = "; ".join("%s: %d" % (k, len(v)) for k, v in sorted(out["left"].items()))
        raise IdmError("only %d host(s) from IdM at %s, min_hosts is %d (left out - %s) - check the filters and that the"
                       " user may read hosts; the inventory left as it was" % (n, server, o["min_hosts"], left or "none"))


class InventoryModule(BaseInventoryPlugin, Constructable):
    NAME = "site_idm"

    def verify_file(self, path):
        # inventories/idm/idm.yml, or any file whose name ends in idm.yml
        return super().verify_file(path) and path.endswith(("idm.yml", "idm.yaml"))

    def parse(self, inventory, loader, path, cache=False):
        super().parse(inventory, loader, path, cache)
        self._read_config_data(path)
        try:
            o = {
                "enrolled_only": self.get_option("enrolled_only"),
                "include_hostgroups": as_value(self.get_option("include_hostgroups"), list),
                "exclude_hostgroups": as_value(self.get_option("exclude_hostgroups"), list),
                "include_hosts": as_value(self.get_option("include_hosts"), list),
                "exclude_hosts": as_value(self.get_option("exclude_hosts"), list),
                "group_map": as_value(self.get_option("group_map"), dict),
                "idm_groups": self.get_option("idm_groups"),
                "group_prefix": self.get_option("group_prefix") or "",
                "hostname": self.get_option("hostname"),
                "min_hosts": self.get_option("min_hosts"),
                "require_groups": as_value(self.get_option("require_groups"), list),
            }
            bad = [t for ts in o["group_map"].values() for t in ts if safe_group(t) != t]
            if bad:
                raise IdmError("group_map: %s - a group name is letters, digits and _, not starting with a digit"
                               % ", ".join(bad))
            idm = Idm(self.get_option("server"), self.get_option("user"), self.get_option("password"),
                      self.get_option("ca_path"), self.get_option("validate_certs"), self.get_option("timeout"))
            idm.login()
            # all: krblastpwdchange (the keytab's date - enrolled; has_keytab is not in the API's answer);
            # no_members False: memberof_hostgroup and memberofindirect_hostgroup, which IdM leaves out else
            # sizelimit, timelimit 0: no limit (IdM's own are 100 entries and 2 seconds)
            every = {"sizelimit": 0, "timelimit": 0, "no_members": False}
            hosts = idm.call("host_find", dict(every, all=True))
            hostgroups = idm.call("hostgroup_find", every)
            out = build(hosts, hostgroups, o)
            check(out, o, idm.url)
        except IdmError as e:
            raise AnsibleError("site_idm: %s" % e)
        for msg in out["unused"]:
            display.warning("site_idm: %s" % msg)
        for gname in sorted(out["groups"]):
            self.inventory.add_group(gname)
        for gname, g in sorted(out["groups"].items()):
            for c in sorted(g["children"]):
                self.inventory.add_child(gname, c)
        strict = self.get_option("strict")
        for name, hv in out["hosts"].items():
            self.inventory.add_host(name)
            for k, v in hv.items():         # what IdM holds is never run as a template (a description with {{ }})
                self.inventory.set_variable(name, k, wrap_var(v))
            for gname, g in out["groups"].items():
                if name in g["hosts"]:
                    self.inventory.add_child(gname, name)
            self._set_composite_vars(self.get_option("compose"), hv, name, strict=strict)
            self._add_host_to_composed_groups(self.get_option("groups"), hv, name, strict=strict)
            self._add_host_to_keyed_groups(self.get_option("keyed_groups"), hv, name, strict=strict)
        empty = [g for g in o["require_groups"]
                 if g not in self.inventory.groups or not self.inventory.groups[g].get_hosts()]
        if empty:
            raise AnsibleError("site_idm: no host in %s (require_groups) - an IdM host group renamed or emptied, or"
                               " group_map changed; the inventory left as it was" % ", ".join(empty))
