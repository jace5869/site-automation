#!/usr/bin/python
# -*- coding: utf-8 -*-
"""vCenter's trusted root store and each ESXi host's trusted CA certificates, matched against a list of known
certificates (a CA whose key was compromised, a root the browsers dropped). Read-only, from the controller."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_trust
short_description: Which of vCenter's trusted roots and the ESXi hosts' trusted CAs are on a list of known certificates
description:
  - For every vCenter of the job (VMWARE_HOST and vcenters), its trusted root chains (the REST API
    certificate-management/vcenter/trusted-root-chains, vCenter 7 and later; /rest on 6.7) and, with esxi, every
    connected host's CA certificates (the host's certificate manager, through vCenter). Each certificate is matched
    by its SHA-256 and by its public key's. Changes nothing.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (a "VMware vCenter" credential).
  - With remove, the chains that hold a listed certificate (at one of the severities asked for) are deleted from
    vCenter's trusted root store, and each ESXi host's trusted CA list is replaced by the same list without them
    (its CRLs kept). Check mode shows what would go and changes nothing.
options:
  known: {description: "The certificates to look for: [{name, kind, sha256, spki_sha256, severity, why}].", type: list, elements: dict, default: []}
  vcenters: {description: More vCenters besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  esxi: {description: Read the ESXi hosts' trusted CAs too., type: bool, default: true}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
  timeout: {description: Seconds for one connection., type: int, default: 30}
  remove: {description: Delete the listed certificates from the stores that hold them., type: bool, default: false}
  severities: {description: With remove - only certificates at these severities., type: list, elements: str, default: [critical, warning]}
author: site automation
'''

RETURN = r'''
stores:
  description: One record per store read - vCenter's trusted roots, each ESXi host's trusted CAs.
  type: list
  returned: success
  sample: [{vcenter: vc01.example.mil, kind: vcenter, name: vc01.example.mil, count: 12, hits: [], blocked: []}]
not_read:
  description: What could not be read, with why.
  type: list
  returned: success
vcenters:
  description: One per vCenter - name, ok, error, note.
  type: list
  returned: success
removed:
  description: With remove - one per store changed (or that would be, in check mode) - vcenter, kind, name, names, action, done, error.
  type: list
  returned: success
'''

import base64
import json
import os
import ssl

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_trust import Known, pem_certs
from ansible.module_utils.site_vcenters import each_vcenter, host_port
from ansible.module_utils.six.moves.urllib import error as urlerror
from ansible.module_utils.six.moves.urllib import request as urlrequest

try:
    from pyVmomi import vim
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

CHAINS = "/vcenter/certificate-management/vcenter/trusted-root-chains"


def fault_text(e):
    pid = getattr(e, "privilegeId", None)
    if pid:
        return "the vCenter account lacks the privilege %s" % pid
    msg = " ".join(str(getattr(e, "msg", None) or e).split())
    name = type(e).__name__.split(".")[-1]
    return "%s: %s" % (name, msg) if msg and name not in msg else (msg or name)


class Api(object):
    """vCenter's REST API with the credential's account: /api (vCenter 7 and later), else /rest (6.7)."""

    def __init__(self, host, port, validate_certs, timeout):
        self.base = "https://%s:%d" % ("[%s]" % host if ":" in host else host, port)
        self.timeout = timeout
        self.ctx = ssl.create_default_context()
        if not validate_certs:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.token, self.old = "", False

    def call(self, method, path, auth=False):
        headers = {"Accept": "application/json"}
        if auth:
            pair = "%s:%s" % (os.environ.get("VMWARE_USER", ""), os.environ.get("VMWARE_PASSWORD", ""))
            headers["Authorization"] = "Basic " + base64.b64encode(pair.encode("utf-8")).decode("ascii")
        elif self.token:
            headers["vmware-api-session-id"] = self.token
        req = urlrequest.Request(self.base + path, headers=headers)
        req.get_method = lambda: method
        try:
            resp = urlrequest.urlopen(req, timeout=self.timeout, context=self.ctx)
            try:
                code, raw = resp.getcode(), resp.read()
            finally:
                resp.close()
        except urlerror.HTTPError as e:
            try:
                code, raw = e.code, e.read()
            finally:
                e.close()
        try:
            return code, json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return code, raw.decode("utf-8", "replace")[:300]

    def login(self, name):
        code, d = self.call("POST", "/api/session", auth=True)
        if code == 404:
            self.old = True
            code, d = self.call("POST", "/rest/com/vmware/cis/session", auth=True)
            d = d.get("value") if isinstance(d, dict) else d
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused the login to its API (HTTP %d)" % (name, code))
        if code not in (200, 201) or not isinstance(d, str) or not d:
            raise RuntimeError("vCenter %s: no API session (HTTP %s)" % (name, code))
        self.token = d

    def logout(self):
        if self.token:
            try:
                self.call("DELETE", "/rest/com/vmware/cis/session" if self.old else "/api/session")
            except Exception:                   # noqa: BLE001
                pass
            self.token = ""

    def get(self, path):
        code, d = self.call("GET", ("/rest" if self.old else "/api") + path)
        if self.old and isinstance(d, dict) and "value" in d:
            d = d["value"]
        return code, d

    def trusted_roots(self, name):
        """vCenter's trusted root chains: [(chain id, PEM text of its certificates)]."""
        code, d = self.get(CHAINS)
        if code == 404:
            raise RuntimeError("no certificate API on vCenter %s (HTTP 404: vCenter 6.7 Update 2 and later have it)" % name)
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused to list its trusted roots (HTTP %d): the account needs CertificateManagement.Manage" % (name, code))
        if code != 200 or not isinstance(d, list):
            raise RuntimeError("vCenter %s did not list its trusted roots (HTTP %s)" % (name, code))
        chains = []
        for item in d:
            cid = item.get("chain") if isinstance(item, dict) else item
            if not cid:
                continue
            code, c = self.get(CHAINS + "/" + str(cid))
            if code != 200 or not isinstance(c, dict):
                continue
            chain = (c.get("cert_chain") or {}).get("cert_chain") or []
            chains.append((str(cid), "\n".join(str(x) for x in chain)))
        return chains

    def delete_chain(self, name, cid):
        code, d = self.call("DELETE", ("/rest" if self.old else "/api") + CHAINS + "/" + cid)
        if code in (401, 403):
            raise RuntimeError("vCenter %s refused to delete the chain %s (HTTP %d): the account needs CertificateManagement.Manage" % (name, cid, code))
        if code not in (200, 204):
            raise RuntimeError("vCenter %s did not delete the chain %s (HTTP %s: %s)" % (name, cid, code, d))


def wanted(known, der, severities):
    m = known.match(der)
    return m if m is not None and str(m.get("severity", "warning")) in severities else None


def read_vcenter(known, name, esxi, validate_certs, timeout, stores, not_read, remove=None):
    """remove: None (read only) or (severities, check_mode, removed list)."""
    host, port = host_port(name)
    api = Api(host, port, validate_certs, timeout)
    try:
        api.login(name)
        try:
            chains = api.trusted_roots(name)
            r = known.scan(pem_certs("\n".join(text for _, text in chains)))
            stores.append(dict(r, vcenter=name, kind="vcenter", name=host))
            if remove is not None and r["hits"]:
                severities, check, removed = remove
                for cid, text in chains:
                    names = sorted(set(w["name"] for w in (wanted(known, d, severities) for d, _ in pem_certs(text)) if w))
                    if not names:
                        continue
                    rec = {"vcenter": name, "kind": "vcenter", "name": host, "names": names, "done": False, "planned": check, "error": "",
                           "action": "deleted the chain %s from vCenter's trusted roots (%d certificates)" % (cid, len(pem_certs(text)))}
                    try:
                        if not check:
                            api.delete_chain(name, cid)
                            rec["done"] = True
                    except Exception as e:      # noqa: BLE001
                        rec["error"] = str(e)
                    removed.append(rec)
                if any(x["done"] for x in removed if x["kind"] == "vcenter" and x["vcenter"] == name):
                    chains = api.trusted_roots(name)        # the store as it is now
                    stores[-1] = dict(known.scan(pem_certs("\n".join(text for _, text in chains))), vcenter=name, kind="vcenter", name=host)
        finally:
            api.logout()
    except Exception as e:                      # noqa: BLE001
        not_read.append({"vcenter": name, "kind": "vcenter", "name": host, "why": "its trusted roots could not be read: %s" % e})


def remove_from_host(known, h, name, pems, severities, check, removed):
    """The host's trusted CA list without the listed certificates (ReplaceCACertificatesAndCRLs takes the whole
    list; its CRLs are read and given back)."""
    keep, names = [], set()
    for p in pems:
        certs = pem_certs(p)
        w = wanted(known, certs[0][0], severities) if certs else None
        if w is None:
            keep.append(str(p))
        else:
            names.add(w["name"])
    if not names:
        return
    rec = {"vcenter": name, "kind": "esxi", "name": h.name, "names": sorted(names), "done": False, "planned": check, "error": "",
           "action": "replaced the host's trusted CA list without them (%d of %d certificates kept)" % (len(keep), len(pems))}
    try:
        if not check:
            cm = h.configManager.certificateManager
            crls = list(cm.ListCACertificateRevocationLists() or [])
            cm.ReplaceCACertificatesAndCRLs(caCert=keep, caCrl=crls)
            rec["done"] = True
    except Exception as e:                      # noqa: BLE001
        rec["error"] = fault_text(e)
    removed.append(rec)


def read_hosts(known, si, content, name, stores, not_read, remove=None):
    view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
    try:
        hosts = list(view.view)
    finally:
        view.Destroy()
    for h in sorted(hosts, key=lambda x: x.name):
        try:
            state = str(h.runtime.connectionState)
            if state != "connected":
                not_read.append({"vcenter": name, "kind": "esxi", "name": h.name, "why": "not connected (%s)" % state})
                continue
            cm = h.configManager.certificateManager
            if cm is None:
                not_read.append({"vcenter": name, "kind": "esxi", "name": h.name, "why": "the host has no certificate manager"})
                continue
            pems = list(cm.ListCACertificates() or [])
            r = known.scan(pem_certs("\n".join(str(p) for p in pems)))
            stores.append(dict(r, vcenter=name, kind="esxi", name=h.name))
            if remove is not None and r["hits"]:
                remove_from_host(known, h, name, pems, remove[0], remove[1], remove[2])
                if remove[2] and remove[2][-1]["done"] and remove[2][-1]["name"] == h.name:
                    pems = list(cm.ListCACertificates() or [])  # the store as it is now
                    stores[-1] = dict(known.scan(pem_certs("\n".join(str(p) for p in pems))), vcenter=name, kind="esxi", name=h.name)
        except Exception as e:                  # noqa: BLE001
            not_read.append({"vcenter": name, "kind": "esxi", "name": getattr(h, "name", "?"), "why": "its trusted CAs could not be read: %s" % fault_text(e)})


def main():
    module = AnsibleModule(argument_spec=dict(
        known=dict(type="list", elements="dict", default=[]),
        vcenters=dict(type="list", elements="str", default=[]),
        esxi=dict(type="bool", default=True),
        validate_certs=dict(type="bool", default=True),
        timeout=dict(type="int", default=30),
        remove=dict(type="bool", default=False),
        severities=dict(type="list", elements="str", default=["critical", "warning"]),
    ), supports_check_mode=True)
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in this execution environment (pip install pyvmomi)")
    p = module.params
    known = Known(p["known"])
    stores, not_read, removed = [], [], []
    remove = (set(p["severities"] or []), module.check_mode, removed) if p["remove"] else None

    def read(si, content, name):
        read_vcenter(known, name, p["esxi"], p["validate_certs"], p["timeout"], stores, not_read, remove)
        if p["esxi"]:
            read_hosts(known, si, content, name, stores, not_read, remove)
        return None

    _, statuses = each_vcenter(module, read, p["vcenters"], p["validate_certs"], what="trust stores", timeout=p["timeout"])
    module.exit_json(changed=any(r["done"] for r in removed), stores=stores, not_read=not_read, vcenters=statuses, removed=removed)


if __name__ == "__main__":
    main()
