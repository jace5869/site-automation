#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read NetApp ONTAP clusters through their REST API - GET only, Python's standard library only
(no collection needed). For each cluster, run each query, page through the records, and keep
going when one fails: the report shows what could not be read and why."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ontap_collect
short_description: Read NetApp ONTAP clusters through the REST API (GET only)
description:
  - For each cluster, runs the given queries (path, fields, filters) with HTTP GET only and
    follows the pages (_links.next). This module cannot change anything on a cluster - it refuses
    any other HTTP method.
  - A query whose fields this ONTAP version does not know (HTTP 400) is asked again with
    ignore_unknown_fields=true (ONTAP 9.11+), then with its fallback_fields (or fields=*); the result
    is then marked reduced. Any other failure is recorded for that query and the others still run. A cluster that cannot be reached is recorded once; its other queries are skipped.
  - A request that gets no answer in time is asked once more. When the cluster has already answered,
    a query that still times out is recorded for that query alone and the others still run (a big
    cluster can be slow on one table); after two such in a row the cluster is recorded as unreachable.
  - The reason a cluster cannot be read says what to do (DNS, port, proxy, certificate, TLS).
  - Keys of /api/private/cli/ records (the CLI passthrough) are returned with underscores.
  - The account comes from ONTAP_USERNAME and ONTAP_PASSWORD (an AAP "NetApp ONTAP" credential).
    A read-only ONTAP role is enough.
options:
  clusters: {description: Cluster management addresses (names or IPs)., type: list, elements: str, required: true}
  queries:
    description: "Queries: key (name of the result), path (/api/...), fields (list), query (dict of filters), max (most records to read), fallback_fields and fallback_query (the last try, for older ONTAP)."
    type: list
    elements: dict
    required: true
  validate_certs: {description: Check the clusters' certificates., type: bool, default: true}
  ca_path: {description: A CA file to check them with (e.g. your own CA)., type: str}
  timeout: {description: Seconds for one request., type: int, default: 30}
  tls_legacy:
    description: "Connect to older ONTAP whose TLS the execution environment refuses by default (legacy renegotiation, small keys, SHA-1): OpenSSL 3's UNSAFE_LEGACY_RENEGOTIATION_DISABLED and the like."
    type: bool
    default: false
author: site automation
'''

RETURN = r'''
clusters:
  description: One entry per cluster.
  type: list
  returned: always
  sample: [{address: cluster1.example.mil, reachable: true, error: "",
            data: {nodes: {records: [{name: node1, state: up}], error: "", reduced: false, truncated: false}}}]
'''

import base64
import json
import os
import socket
import ssl
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.six.moves.urllib.request import Request, build_opener, HTTPSHandler
try:
    from urllib.request import getproxies, proxy_bypass
except ImportError:                      # Python 2
    from urllib import getproxies, proxy_bypass


class Unreachable(Exception):
    """The cluster itself cannot be reached (not just one query). kind: timeout, dns, refused, tls, auth, other."""

    def __init__(self, msg, kind="other"):
        Exception.__init__(self, msg)
        self.kind = kind


def kind_of(e):
    """What sort of network failure an exception from urlopen is."""
    reason = getattr(e, "reason", e)
    text = str(reason)
    if isinstance(reason, socket.timeout) or "timed out" in text.lower():
        return "timeout"
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "nodename nor servname" in text \
            or "Temporary failure in name resolution" in text:
        return "dns"
    if isinstance(reason, ssl.SSLError) or isinstance(reason, getattr(ssl, "CertificateError", ssl.SSLError)) or "SSL" in text \
            or "CERTIFICATE" in text:
        return "tls"
    if "refused" in text.lower() or "No route to host" in text or "unreachable" in text.lower():
        return "refused"
    return "other"


TLS_HINTS = (
    ("CERTIFICATE_VERIFY_FAILED", "the cluster's certificate is not trusted: set ontap_ca_path to its CA file, or "
                                  "ontap_validate_certs: false (docs/NETAPP.md). A curl with -k skips this check"),
    ("ostname mismatch", "the address is not one of the names on the cluster's certificate: list the cluster by the name its "
                         "certificate has, or ontap_validate_certs: false"),
    ("doesn't match", "the address is not one of the names on the cluster's certificate: list the cluster by the name its "
                      "certificate has, or ontap_validate_certs: false"),
    ("UNSAFE_LEGACY_RENEGOTIATION_DISABLED", "this ONTAP's TLS is older than the execution environment allows: "
                                             "ontap_tls_legacy: true (or update ONTAP)"),
    ("KEY_TOO_SMALL", "the cluster's key is smaller than the execution environment allows: ontap_tls_legacy: true, or a new "
                      "certificate on the cluster (security certificate create)"),
    ("MD_TOO_WEAK", "the cluster's certificate is signed with SHA-1: ontap_tls_legacy: true, or a new certificate on the cluster"),
    ("WRONG_VERSION_NUMBER", "that port does not speak HTTPS: give the cluster management address (port 443)"),
    ("UNSUPPORTED_PROTOCOL", "the cluster offers only TLS versions the execution environment refuses: ontap_tls_legacy: true, "
                             "or allow TLS 1.2 on the cluster (security config modify -supported-protocols)"),
    ("HANDSHAKE_FAILURE", "the cluster and the execution environment share no cipher: ontap_tls_legacy: true, or check the "
                          "cluster's security config (FIPS)"),
)


def proxy_for(url):
    """The proxy urllib sends this URL through (https_proxy in the environment, not in no_proxy), or ''."""
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0].rsplit("@", 1)[-1]
        scheme = url.split("://", 1)[0] if "://" in url else "https"
        proxy = getproxies().get(scheme, "")
        if proxy and not proxy_bypass(host.split(":")[0]):
            return proxy
    except Exception:  # pylint: disable=broad-except
        pass
    return ""


def explain(text, kind, address, timeout):
    """The reason a cluster could not be read, with what to do."""
    hint = ""
    if kind == "tls":
        hint = next((h for k, h in TLS_HINTS if k in text), "a TLS problem between the execution environment and the cluster")
    elif kind == "dns":
        hint = ("the name does not resolve where the job runs (the AAP execution node's container, not your shell): "
                "use the name DNS knows there, or the IP address")
    elif kind == "timeout":
        hint = ("no answer within %s s, asked twice: a firewall between the AAP execution node and the cluster's "
                "management address on port 443, or a slow cluster (ontap_timeout: 120)" % timeout)
    elif kind == "refused":
        hint = "nothing answered on port 443 from the AAP execution node: the cluster management address, a firewall"
    proxy = proxy_for(address if "://" in address else "https://" + address)
    if proxy and kind in ("timeout", "refused", "other", "tls"):
        hint += ("; it went through the proxy %s (https_proxy where the job runs): add the cluster to no_proxy"
                 % proxy.split("@")[-1])
    return text + (" - " + hint if hint else "")


def underscore_keys(v):
    """{"lag-time": 1} -> {"lag_time": 1}, all the way down (the CLI passthrough uses CLI names)."""
    if isinstance(v, dict):
        return {str(k).replace("-", "_"): underscore_keys(x) for k, x in v.items()}
    if isinstance(v, list):
        return [underscore_keys(x) for x in v]
    return v


def build_url(address, path, fields=None, query=None, page=None):
    q = dict(query or {})
    if fields:
        q["fields"] = ",".join(fields)
    if page:
        q["max_records"] = str(page)
    base = address if "://" in address else "https://" + address
    return base.rstrip("/") + path + ("?" + urlencode(q, safe="*|,.:=><!") if q else "")


def error_text(code, body):
    """An ONTAP error body ({"error": {"message", "code", "target"}}) as one line."""
    try:
        e = json.loads(body or "{}").get("error") or {}
        msg = e.get("message") or ""
        if e.get("target"):
            msg += " (%s)" % e["target"]
        return "HTTP %s: %s" % (code, msg) if msg else "HTTP %s" % code
    except (ValueError, AttributeError):
        return "HTTP %s" % code


class Client(object):
    """GET only. One instance per cluster."""

    def __init__(self, address, user, password, context, timeout, opener=None, retries=1):
        self.address, self.timeout, self.retries = address, timeout, retries
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")
        self.opener = opener or build_opener(HTTPSHandler(context=context))

    def get(self, url):
        """(status, body). Raises Unreachable when the cluster does not answer at all."""
        if not url.startswith("http"):
            url = (self.address if "://" in self.address else "https://" + self.address).rstrip("/") + url
        req = Request(url, headers={"Authorization": self.auth, "Accept": "application/json"})
        req.get_method = lambda: "GET"          # this module never sends anything but GET
        tries = 0
        while True:
            try:
                r = self.opener.open(req, timeout=self.timeout)
                return r.getcode(), r.read().decode("utf-8", "replace")
            except HTTPError as e:
                return e.code, e.read().decode("utf-8", "replace")
            except (URLError, ssl.SSLError, OSError) as e:
                kind = kind_of(e)
                if kind == "timeout" and tries < self.retries:
                    tries += 1
                    continue
                raise Unreachable(str(getattr(e, "reason", e)) or e.__class__.__name__, kind)


def attempts(q):
    """The ways to ask, in order: the fields as given; then the same with ignore_unknown_fields=true
    (ONTAP 9.11+ drops the fields it does not know); then the query's fallback_fields, or fields=*
    (never on the CLI passthrough, which refuses fields=*)."""
    fields = list(q.get("fields") or [])
    query = dict(q.get("query") or {})
    out = [(fields, query)]
    if fields:
        out.append((fields, dict(query, ignore_unknown_fields="true")))
        last = dict(q["fallback_query"]) if q.get("fallback_query") is not None else query
        if q.get("fallback_fields"):
            out.append((list(q["fallback_fields"]), last))
        elif not q["path"].startswith("/api/private/cli/"):
            out.append((["*"], last))
    return out


def run_query(client, q):
    """One query (pages followed) -> {records, error, reduced, truncated, at}. reduced: the first way
    was refused (HTTP 400) and a later one answered - some columns may be empty."""
    cap = int(q.get("max") or 10000)
    page = min(cap, 1000)
    out = {"records": [], "error": "", "reduced": "", "truncated": False, "at": time.time()}
    ways = attempts(q)
    n = 0
    url = build_url(client.address, q["path"], ways[0][0], ways[0][1], page)
    while url:
        code, body = client.get(url)
        if code == 400 and not out["records"] and n + 1 < len(ways):
            out["reduced"] = out["reduced"] or error_text(code, body)
            n += 1
            url = build_url(client.address, q["path"], ways[n][0], ways[n][1], page)
            continue
        if code == 401:
            raise Unreachable("the ONTAP account was refused (HTTP 401): check the NetApp ONTAP credential - the user exists "
                              "on this cluster, with the http application, unlocked", "auth")
        if code >= 300:
            out["error"] = error_text(code, body) + (" - the account's role may not allow this" if code == 403 else "")
            break
        try:
            data = json.loads(body or "{}")
        except ValueError:
            out["error"] = "not JSON: " + (body or "")[:200]
            break
        if not isinstance(data, dict):
            break
        recs = data["records"] if "records" in data else [data]
        if q["path"].startswith("/api/private/cli/"):
            recs = underscore_keys(recs)
        out["records"].extend(recs)
        nxt = ((data.get("_links") or {}).get("next") or {}).get("href")
        if len(out["records"]) >= cap:
            out["truncated"] = bool(nxt) or len(out["records"]) > cap
            out["records"] = out["records"][:cap]
            break
        url = nxt if recs else None          # ONTAP may send a next link with nothing after it
    if out["reduced"] and not out["error"]:
        out["reduced"] = "reduced detail - ONTAP refused a field (%s)" % out["reduced"]
    elif out["error"]:
        out["reduced"] = ""
    return out


def collect(client, queries):
    """Every query of one cluster -> {reachable, error, data}."""
    res = {"address": client.address, "reachable": True, "error": "", "data": {}}
    answered, slow = False, 0
    for q in queries:
        try:
            res["data"][q["key"]] = run_query(client, q)
            answered, slow = True, 0
        except Unreachable as e:
            # the cluster answered before: one slow table is that table's problem, not the cluster's
            if answered and e.kind == "timeout" and slow < 1:
                slow += 1
                res["data"][q["key"]] = {"records": [], "error": "no answer within %s s, asked twice - ontap_timeout: 120 "
                                         "gives it longer" % getattr(client, "timeout", "?"),
                                         "reduced": "", "truncated": False, "at": time.time()}
                continue
            res["reachable"] = False
            res["error"] = explain(str(e), e.kind, client.address, getattr(client, "timeout", "?"))
            break
    return res


def main():
    module = AnsibleModule(
        argument_spec=dict(clusters=dict(type="list", elements="str", required=True),
                           queries=dict(type="list", elements="dict", required=True),
                           validate_certs=dict(type="bool", default=True), ca_path=dict(type="str"),
                           timeout=dict(type="int", default=30), tls_legacy=dict(type="bool", default=False)),
        supports_check_mode=True,
    )
    p = module.params
    user, password = os.environ.get("ONTAP_USERNAME", ""), os.environ.get("ONTAP_PASSWORD", "")
    if not user or not password:
        module.fail_json(msg='No ONTAP account: attach a credential of type "NetApp ONTAP" to this job template '
                             '(aap/credential_types/netapp_ontap.yml).')
    if not p["clusters"]:
        module.fail_json(msg="ontap_clusters is empty: list the clusters' management addresses (docs/NETAPP.md).")
    for q in p["queries"]:
        if not str(q.get("path", "")).startswith("/api/") or not q.get("key"):
            module.fail_json(msg="a query needs a key and a path under /api/: %s" % q)
    if p["validate_certs"]:
        context = ssl.create_default_context(cafile=p["ca_path"] or None)
    else:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if p["tls_legacy"]:
        context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        try:
            context.set_ciphers("DEFAULT:@SECLEVEL=1")
        except ssl.SSLError:                 # FIPS: the policy decides
            pass
    started = time.time()
    out = []
    for address in p["clusters"]:
        res = collect(Client(address, user, password, context, p["timeout"]), p["queries"])
        out.append(res)
    module.exit_json(changed=False, clusters=out, seconds=round(time.time() - started, 1),
                     read_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)))


if __name__ == "__main__":
    main()
