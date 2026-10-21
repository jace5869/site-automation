#!/usr/bin/python
# -*- coding: utf-8 -*-
"""site_panos_api: a Palo Alto Networks firewall's (or Panorama's) configuration over its XML API - from the
controller, no collection or SDK needed."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
module: site_panos_api
short_description: Read, change or restore a PAN-OS configuration over the XML API
description:
  - C(export) - the running configuration (XML), as the web interface's Export named configuration snapshot
    gives it.
  - C(set) - changes (set, edit or delete at an xpath) to the candidate configuration, then validate and commit.
  - C(restore) - a saved configuration imported as a named snapshot, loaded as the candidate, validated and
    committed.
  - Before a change, the firewall must have no uncommitted changes (someone else's would be committed with
    ours): the module refuses (allow_pending overrides). A validation that fails reverts the candidate - nothing
    is committed. In check mode, set and restore read only - what is there now - and change nothing.
options:
  host: {description: The firewall's name or address., type: str, required: true}
  port: {description: Its HTTPS port., type: int, default: 443}
  api_key: {description: An API key (or username and password - a key is made from them)., type: str, no_log: true}
  username: {description: The API user., type: str}
  password: {description: Its password., type: str, no_log: true}
  action: {description: What to do., type: str, choices: [export, set, restore], default: export}
  entries:
    description: For set - a list of {xpath, element, action} (action set (default), edit or delete; element the XML
      to put at the xpath, without it for delete).
    type: list
    elements: dict
    default: []
  config: {description: For restore - the configuration (XML) to load., type: str}
  file_name: {description: For restore - its name on the firewall (a named configuration snapshot)., type: str, default: site-automation-restore.xml}
  description: {description: The commit's description., type: str, default: ''}
  allow_pending: {description: Change even with uncommitted changes on the firewall (they are committed too)., type: bool, default: false}
  commit: {description: Commit after validating (false - the candidate is left changed, for a look in the web interface)., type: bool, default: true}
  validate_certs: {description: Check the firewall's certificate., type: bool, default: true}
  ca_path: {description: A CA bundle (PEM) for the check., type: str}
  timeout: {description: Seconds per request., type: int, default: 60}
  job_timeout: {description: Seconds to wait for a validate or commit job., type: int, default: 900}
  scheme: {description: https (http only for a test stand-in)., type: str, choices: [https, http], default: https}
"""

RETURN = r"""
config: {description: The running configuration (export)., type: str, returned: action export}
current: {description: What is at each entry's xpath now (set)., type: list, returned: action set}
unchanged: {description: The entries were all there already - nothing was committed (set)., type: bool, returned: when so}
validate: {description: The validation job's result and details., type: dict, returned: when validated}
commit: {description: The commit job's result and details (or "no changes")., type: dict, returned: when committed}
"""

import json
import ssl
import time
import uuid
import xml.etree.ElementTree as ET

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError


class PanError(Exception):
    pass


class Pan(object):
    def __init__(self, host, port=443, api_key=None, username=None, password=None, validate_certs=True,
                 ca_path=None, timeout=60, scheme="https"):
        self.base = "%s://%s:%d/api/" % (scheme, host, int(port))
        self.timeout = timeout
        if scheme == "https":
            self.ctx = ssl.create_default_context(cafile=ca_path) if validate_certs else ssl._create_unverified_context()
        else:
            self.ctx = None
        self.key = api_key
        if not self.key:
            if not (username and password):
                raise PanError("no credential: an API key, or a username and password (the PAN-OS API credential)")
            r = self.call({"type": "keygen", "user": username, "password": password}, keyed=False)
            k = r.find("./result/key")
            if k is None or not (k.text or "").strip():
                raise PanError("the firewall gave no API key for this user")
            self.key = k.text.strip()

    def raw(self, params, body=None, content_type=None, keyed=True):
        url = self.base + "?" + urlencode(params)
        headers = {"X-PAN-KEY": self.key} if keyed else {}
        data = None
        if body is not None:
            data = body
            headers["Content-Type"] = content_type
        elif not keyed:
            # keygen: the user and password in the body, not the URL (no proxy or log keeps them)
            url = self.base
            data = urlencode(params).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(url, data=data, headers=headers)
        try:
            resp = urlopen(req, timeout=self.timeout, context=self.ctx) if self.ctx else urlopen(req, timeout=self.timeout)
            return resp.read().decode("utf-8", "replace")
        except HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            raise PanError("HTTP %d from the firewall: %s" % (e.code, msg_of(text) or e.reason))
        except URLError as e:
            raise PanError("cannot reach the firewall: %s" % e.reason)

    def call(self, params, keyed=True, body=None, content_type=None):
        text = self.raw(params, body=body, content_type=content_type, keyed=keyed)
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            raise PanError("not an XML API answer: %s" % text[:200])
        if root.get("status") != "success":
            raise PanError(msg_of(text) or "the firewall said: %s" % root.get("status"))
        return root

    def op(self, cmd):
        return self.call({"type": "op", "cmd": cmd})

    def export(self):
        text = self.raw({"type": "export", "category": "configuration"})
        if not text.lstrip().startswith("<config"):
            raise PanError(msg_of(text) or "the export gave no configuration")
        return text

    def pending(self):
        r = self.op("<check><pending-changes></pending-changes></check>")
        return (r.findtext("./result") or "").strip().lower() == "yes"

    def get(self, xpath):
        r = self.call({"type": "config", "action": "get", "xpath": xpath})
        res = r.find("./result")
        if res is None or len(res) == 0:
            return ""
        return "".join(ET.tostring(c, encoding="unicode") for c in res)

    def job(self, root, what, job_timeout):
        j = root.findtext("./result/job")
        if not j:
            return {"result": "OK", "details": [msg_of(ET.tostring(root, encoding="unicode")) or "no job (nothing to do)"], "job": None}
        deadline = time.time() + job_timeout
        while True:
            r = self.op("<show><jobs><id>%s</id></jobs></show>" % j.strip())
            st = r.findtext("./result/job/status") or ""
            if st == "FIN":
                res = r.findtext("./result/job/result") or ""
                det = [x.text or "" for x in r.findall("./result/job/details/line")] or \
                      [x.text or "" for x in r.findall(".//details//line")]
                return {"result": res, "details": [d.strip() for d in det if (d or "").strip()], "job": j.strip()}
            if time.time() > deadline:
                raise PanError("the %s job %s did not finish within %d seconds" % (what, j, job_timeout))
            time.sleep(2)

    def validate(self, job_timeout):
        return self.job(self.op("<validate><full></full></validate>"), "validate", job_timeout)

    def commit(self, description, job_timeout):
        cmd = "<commit>%s</commit>" % (("<description>%s</description>" % xml_escape(description)) if description else "")
        return self.job(self.call({"type": "commit", "cmd": cmd}), "commit", job_timeout)

    def revert(self):
        try:
            self.op("<revert><config></config></revert>")
            return True
        except PanError:
            return False

    def import_config(self, name, config):
        boundary = uuid.uuid4().hex
        body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: application/xml\r\n\r\n"
                % (boundary, name)).encode() + config.encode("utf-8") + ("\r\n--%s--\r\n" % boundary).encode()
        return self.call({"type": "import", "category": "configuration"}, body=body,
                         content_type="multipart/form-data; boundary=%s" % boundary)

    def load(self, name):
        return self.op("<load><config><from>%s</from></config></load>" % xml_escape(name))


def xml_escape(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def msg_of(text):
    """The message lines of an XML API answer (<msg>, <line>), joined."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ""
    lines = [(x.text or "").strip() for x in root.iter() if x.tag in ("line", "msg", "member") and (x.text or "").strip()]
    return "; ".join(lines)


def run(p, check_mode=False, scheme="https"):
    pan = Pan(p["host"], p.get("port") or 443, p.get("api_key"), p.get("username"), p.get("password"),
              p.get("validate_certs", True), p.get("ca_path"), p.get("timeout") or 60, scheme=scheme)
    action = p.get("action") or "export"
    jt = p.get("job_timeout") or 900
    if action == "export":
        return {"changed": False, "config": pan.export()}
    out = {"changed": False}
    if action == "set":
        entries = p.get("entries") or []
        for e in entries:
            if not e.get("xpath") or (e.get("action", "set") != "delete" and not e.get("element")):
                raise PanError("each entry needs an xpath, and an element unless its action is delete: %s" % json.dumps(e))
            if e.get("action", "set") not in ("set", "edit", "delete"):
                raise PanError("an entry's action is set, edit or delete, not %s" % e.get("action"))
        out["current"] = [{"xpath": e["xpath"], "now": pan.get(e["xpath"])} for e in entries]
    elif action == "restore":
        if not (p.get("config") or "").lstrip().startswith("<config"):
            raise PanError("the configuration to restore is not a PAN-OS configuration (it has no <config> root)")
    if check_mode:
        out["pending"] = pan.pending()
        return out
    if pan.pending() and not p.get("allow_pending"):
        raise PanError("the firewall has uncommitted changes (someone else's?) - they would be committed with these. "
                       "Commit or revert them first, or allow_pending")
    if action == "set":
        for e in p.get("entries") or []:
            params = {"type": "config", "action": e.get("action", "set"), "xpath": e["xpath"]}
            if e.get("action", "set") != "delete":
                params["element"] = e["element"]
            pan.call(params)
    else:
        name = p.get("file_name") or "site-automation-restore.xml"
        pan.import_config(name, p["config"])
        pan.load(name)
    if action == "set" and not pan.pending():
        out["unchanged"] = True         # every entry was there already: nothing to validate or commit
        return out
    out["changed"] = True
    out["validate"] = pan.validate(jt)
    if out["validate"]["result"] != "OK":
        out["reverted"] = pan.revert()
        raise PanError("validation failed - nothing committed%s: %s" % (
            ", the candidate reverted" if out["reverted"] else " (revert the candidate by hand)", "; ".join(out["validate"]["details"])))
    if p.get("commit", True):
        out["commit"] = pan.commit(p.get("description") or "", jt)
        if out["commit"]["result"] != "OK":
            raise PanError("the commit failed: %s" % "; ".join(out["commit"]["details"]))
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True), port=dict(type="int", default=443),
            api_key=dict(type="str", no_log=True), username=dict(type="str"), password=dict(type="str", no_log=True),
            action=dict(type="str", choices=["export", "set", "restore"], default="export"),
            entries=dict(type="list", elements="dict", default=[]), config=dict(type="str"),
            file_name=dict(type="str", default="site-automation-restore.xml"), description=dict(type="str", default=""),
            allow_pending=dict(type="bool", default=False), commit=dict(type="bool", default=True),
            validate_certs=dict(type="bool", default=True), ca_path=dict(type="str"),
            timeout=dict(type="int", default=60), job_timeout=dict(type="int", default=900),
            scheme=dict(type="str", choices=["https", "http"], default="https"),
        ),
        supports_check_mode=True,
    )
    try:
        out = run(module.params, module.check_mode, scheme=module.params["scheme"])
    except PanError as e:
        module.fail_json(msg=str(e))
    module.exit_json(**out)


if __name__ == "__main__":
    main()
