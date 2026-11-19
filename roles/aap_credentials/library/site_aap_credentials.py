#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up credentials (playbooks/aap_credentials_setup.yml): rename credentials, and attach them to job templates
or take them off, through AAP's own API. Never deletes a credential; never reads, prints or logs a secret field - only a
credential's id, name, type and organization are used. Everything is read and checked first: a name that matches
nothing, more than one credential, or a rename onto a name that is there, and nothing is written. In check mode it only
reads and says what it would do. The address and login come from the "Red Hat Ansible Automation Platform" credential
(plugins/module_utils/site_aap.py). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_credentials
short_description: Rename AAP credentials and attach them to job templates or take them off (never deletes)
description:
  - >-
    renames ({old name: new name}) renames a credential. A new name another credential has - in the organization, or in
    any organization when none is given - is an error, and nothing is changed.
  - >-
    attach ({credential: [job template, ...]}) attaches it. A template that has a credential of the same type already
    keeps it and is listed ("would replace ...") unless replace_same_type, which takes that one off and attaches the new one.
  - >-
    detach ({credential: [job template, ...]}) takes only that credential off those templates.
  - >-
    A credential or template name is looked up in organization when it is given, else in every organization - and one
    that matches more than one is an error. A workflow has no credentials of its own in AAP (its templates do): named, it
    is said, and nothing is attached to it.
  - >-
    Everything is read and checked before anything is written; a problem found then and nothing is written. A write that
    fails stops the run; what was done before it is listed.
  - >-
    Only a credential's id, name, type and organization are kept (AAP sends secret fields as $encrypted$). Its inputs are
    never returned, printed or logged.
options:
  renames: {description: "{old name: new name}.", type: dict, default: {}}
  attach: {description: "{credential name: [job template name, ...]}.", type: dict, default: {}}
  detach: {description: "{credential name: [job template name, ...]}.", type: dict, default: {}}
  organization: {description: "The organization the names are looked up in ('' = any - a name in two is an error).", type: str, default: ''}
  replace_same_type: {description: "Take off a template's credential of the same type and attach the new one.", type: bool, default: false}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each: {action (rename|attach|replace|detach), credential, target, state (done|would do|already
  done|not done|failed), why}.", type: list}
problems: {description: "What stopped the job: a name that matches nothing or more than one, a clash, AAP not reached.", type: list}
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user with Admin on the organization - it renames credentials and changes job templates)')
# what is ever kept of a credential: never its inputs
KEEP = ("id", "name", "credential_type", "organization")


def _why(code, d):
    """Codex: what AAP refused, by its field names only - never a value it sent back (it might echo an input)."""
    keys = sorted(str(k) for k in (d or {}) if k != "detail") if isinstance(d, dict) else []
    return "HTTP %d%s" % (code, (" (fields: %s)" % ", ".join(keys)) if keys else "")


def _q(s):
    from urllib.parse import quote
    return quote(str(s), safe="")


def _slim(c, types=None):
    """A credential as this module keeps it - its id, name, type (and the type's name) and organization; nothing else."""
    out = dict((k, c.get(k)) for k in KEEP)
    sf = c.get("summary_fields") or {}
    tname = (sf.get("credential_type") or {}).get("name") or (types or {}).get(c.get("credential_type")) or ""
    out["type_name"] = str(tname or "type %s" % c.get("credential_type"))
    out["org_name"] = str((sf.get("organization") or {}).get("name") or "")
    return out


class Creds(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.results, self.problems = [], []
        self.org_id = None

    def all(self, path):
        out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
        for _ in range(200):
            code, d = self.api.call("GET", nxt)
            if code != 200:
                raise RuntimeError("%s could not be read: %s" % (path.split("?")[0], _why(code, d)))
            out += d.get("results") or []
            n = d.get("next")
            if not n:
                return out
            nxt = n.split(self.api.base, 1)[1] if self.api.base in n else n.lstrip("/")
        return out

    def note(self, action, cred, target, state, why=""):
        self.results.append({"action": action, "credential": cred, "target": target, "state": state, "why": why})

    # ---- looking up
    def credentials_named(self, name):
        rows = [_slim(c) for c in self.all("credentials/?name=%s" % _q(name)) if c.get("name") == name]
        if self.org_id is not None:
            rows = [c for c in rows if c.get("organization") == self.org_id]
        return rows

    def one(self, rows, what, name):
        if len(rows) == 1:
            return rows[0]
        if not rows:
            self.problems.append("%s %r: not found%s" % (what, name, self.in_org()))
        else:
            orgs = sorted(set(str(r.get("org_name") or r.get("organization") or "none") for r in rows))
            self.problems.append("%s %r: %d match (organizations: %s) - set aap_credential_organization" % (what, name, len(rows), ", ".join(orgs))
                                 if self.org_id is None else "%s %r: %d match in the organization - rename one in AAP" % (what, name, len(rows)))
        return None

    def in_org(self):
        return (" in organization %r" % self.p["organization"]) if self.p["organization"] else ""

    def template(self, name):
        rows = [t for t in self.all("job_templates/?name=%s" % _q(name)) if t.get("name") == name]
        if self.org_id is not None:
            rows = [t for t in rows if t.get("organization") == self.org_id]
        if not rows:
            wf = [w for w in self.all("workflow_job_templates/?name=%s" % _q(name)) if w.get("name") == name]
            if wf:
                self.problems.append("%r is a workflow: a workflow has no credentials of its own in AAP - attach it to the job "
                                     "templates the workflow runs" % name)
                return None
        t = self.one(rows, "job template", name)
        return {"id": int(t["id"]), "name": name} if t else None

    def attached(self, tid):
        return [_slim(c) for c in self.all("job_templates/%d/credentials/" % tid)]

    # ---- the plan: everything read and checked first
    def plan(self):
        p = self.p
        if p["organization"]:
            orgs = [o for o in self.all("organizations/?name=%s" % _q(p["organization"])) if o.get("name") == p["organization"]]
            if len(orgs) != 1:
                self.problems.append("organization %r: %s" % (p["organization"], "not found" if not orgs else "%d match" % len(orgs)))
                return []
            self.org_id = int(orgs[0]["id"])
        steps, by_name = [], {}
        # renames: the credential found by its old name; its new name free in its organization
        news = {}
        for old, new in sorted((p["renames"] or {}).items()):
            old, new = str(old), str(new)
            if not new.strip():
                self.problems.append("rename %r: the new name is empty" % old)
                continue
            if new in news:
                self.problems.append("rename %r and %r: both to %r" % (news[new], old, new))
                continue
            news[new] = old
            olds = self.credentials_named(old)
            if old == new:                                  # Codex: still one credential of that name, or a problem
                self.one(olds, "credential", old)
                continue
            if not olds and self.credentials_named(new):
                # renamed before: the old name is not there, the new one is. Said - and the new name is NOT taken to be
                # that credential for this run's attaches (Codex): it is looked up by its name like any other
                self.note("rename", old, new, "already done", "%r is not there and %r is" % (old, new))
                continue
            c = self.one(olds, "credential", old)
            if not c:
                continue
            # Codex: the new name free where names are looked up - the organization, or every organization when none is set
            clash = [x for x in self.all("credentials/?name=%s" % _q(new)) if x.get("name") == new and int(x["id"]) != int(c["id"])
                     and (self.org_id is None or x.get("organization") == c.get("organization"))]
            if clash:
                self.problems.append("rename %r to %r: a credential of that name is there (id %s) - nothing is changed"
                                     % (old, new, ", ".join(str(x["id"]) for x in clash)))
                continue
            steps.append(("rename", c, new))
            by_name[new] = c
            by_name[old] = c

        def cred(name):
            if name in by_name:
                return by_name[name]
            c = self.one(self.credentials_named(name), "credential", name)
            by_name[name] = c
            return c
        member = {}                                 # Codex: each template's credentials as the queued steps leave them

        def now_of(t):
            if t["id"] not in member:
                member[t["id"]] = self.attached(t["id"])
            return member[t["id"]]
        for action in ("detach", "attach"):
            for cname, tnames in sorted((p[action] or {}).items()):
                tnames = [tnames] if isinstance(tnames, str) else list(tnames or [])
                c = cred(str(cname))
                for tname in tnames:
                    t = self.template(str(tname))
                    if not c or not t:
                        continue
                    now = now_of(t)
                    has = any(int(x["id"]) == int(c["id"]) for x in now)
                    if action == "detach":
                        if has:
                            steps.append(("detach", c, t))
                            member[t["id"]] = [x for x in now if int(x["id"]) != int(c["id"])]
                        else:
                            self.note("detach", str(cname), t["name"], "already done", "not attached")
                        continue
                    if has:
                        self.note("attach", str(cname), t["name"], "already done")
                        continue
                    same = [x for x in now if x.get("credential_type") == c.get("credential_type")]
                    if same and not p["replace_same_type"]:
                        self.note("attach", str(cname), t["name"], "not done",
                                  "would replace %s (%s) - set aap_credential_replace_same_type: true"
                                  % (", ".join(repr(x["name"]) for x in same), c["type_name"]))
                        continue
                    steps.append(("replace" if same else "attach", c, t, same))
                    member[t["id"]] = [x for x in now if x not in same] + [c]
        return steps

    # ---- doing it
    def write(self, method, path, body):
        code, d = self.api.call(method, path, body)
        if code not in (200, 201, 202, 204):
            raise RuntimeError("%s %s: %s" % (method, path.split("?")[0], _why(code, d)))

    def run(self):
        steps = self.plan()
        if self.problems:
            return                                   # a problem found while reading: nothing is written
        for st in steps:
            kind, c = st[0], st[1]
            if kind == "rename":
                target = st[2]
                what = c["name"]
            else:
                target = st[2]["name"]
                what = c["name"]
            if self.check:
                why = ""
                if kind == "replace":
                    why = "takes off %s" % ", ".join(repr(x["name"]) for x in st[3])
                self.note(kind, what, target, "would do", why)
                continue
            try:
                if kind == "rename":
                    self.write("PATCH", "credentials/%d/" % int(c["id"]), {"name": target})
                elif kind == "detach":
                    self.write("POST", "job_templates/%d/credentials/" % st[2]["id"], {"id": int(c["id"]), "disassociate": True})
                else:
                    for x in (st[3] if kind == "replace" else []):
                        self.write("POST", "job_templates/%d/credentials/" % st[2]["id"], {"id": int(x["id"]), "disassociate": True})
                        # Codex: said as it happens - a failure after it leaves the template without it, and the list says so
                        self.note("detach", x["name"], target, "done", "taken off to make room for %r (same type)" % what)
                    self.write("POST", "job_templates/%d/credentials/" % st[2]["id"], {"id": int(c["id"])})
            except RuntimeError as e:
                self.note(kind, what, target, "failed", str(e))
                self.problems.append("stopped at %s %r -> %r: %s (what was done before it is listed)" % (kind, what, target, e))
                return
            self.note(kind, what, target, "done")


def main():
    module = AnsibleModule(
        argument_spec=dict(renames=dict(type="dict", default={}), attach=dict(type="dict", default={}), detach=dict(type="dict", default={}),
                           organization=dict(type="str", default=""), replace_same_type=dict(type="bool", default=False),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_credential_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, results=[], problems=[why])
    s = Creds(api, p, module.check_mode)
    try:
        s.run()
    except RuntimeError as e:
        s.problems.append(str(e))
    changed = any(r["state"] in ("done", "would do") for r in s.results)        # Codex: check mode says what it would change
    module.exit_json(changed=changed, results=s.results, problems=s.problems)


if __name__ == "__main__":
    main()
