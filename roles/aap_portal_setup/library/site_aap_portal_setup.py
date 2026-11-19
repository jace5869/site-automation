#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - set up portal actions (1.67.0 part C, playbooks/aap_portal_setup.yml): the job templates and workflows the site
portal's actions launch, made or brought up to date through AAP's own API from roles/site_portal_stack/files/workflows.yml
(and actions.yml for who may run what). Never deletes a template or a workflow. One made or changed by hand since this job
last wrote it (its description carries the fingerprint of what this job wrote) is listed and left as it is, unless
overwrite. The portal's AAP account gets Execute on the actions' templates and workflows and Approve on the workflows
with an approval node - nothing more. In check mode it only reads and says, field by field, what it would change.
The address and login come from the "Red Hat Ansible Automation Platform" credential (plugins/module_utils/site_aap.py).
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_portal_setup
short_description: Make or update the site portal's job templates and workflows in AAP (never deletes)
description:
  - Each job template of spec.templates and each workflow of spec.workflows is made when AAP has none of that name, or
    brought up to date field by field. Its description ends with a mark - the fingerprint of what this module wrote - so a
    later change by hand is seen; such a template or workflow, or one of that name this module did not make, is listed
    and left as it is unless overwrite.
  - A workflow's nodes (job templates and approval nodes, their success, failure and always links) are made again when
    they differ from the spec.
  - The portal's account (portal_user) gets Execute on the template or workflow of each action, and Approve on each such
    workflow with an approval node. No other role is given - except, when portal_auditor is true (1.75.3), Auditor on the organization.
    Never a write role.
  - schedules adds or updates schedules (every N minutes) on templates or workflows of the spec.
  - 1.68.1, scorecard: a job template of its own for the STIG scorecard (the check template of workflow 40 copied - its project,
    inventory, execution environment, credentials and Variables - with stigman_ckl_import_mode scorecard), a weekly schedule on
    it, and (split) stigman_ckl_import_mode import on workflow 40's check template when that is not set or is "import and
    scorecard". Each is made once; one that is there is left as it is.
  - In check mode nothing is written; the result says what would change.
options:
  spec: {description: "workflows.yml: {templates: [...], workflows: [...]}.", type: dict, required: true}
  actions: {description: "actions.yml's actions (for the grants).", type: dict, default: {}}
  organization: {description: The organization the templates and workflows are made in (its name)., type: str, required: true}
  project: {description: The project the job templates run from (its name)., type: str, required: true}
  execution_environment: {description: The execution environment of the job templates (its name; "" = the project's default)., type: str, default: ''}
  inventories: {description: "The inventories by key: {default: name, linux: name, windows: name}.", type: dict, required: true}
  credentials: {description: "The credentials by kind: {machine: name, aap: name, ...}; a list of names for several.", type: dict, default: {}}
  portal_user: {description: "The portal's AAP account (its user name); '' = no grants.", type: str, default: ''}
  portal_auditor: {description: "Also give portal_user the Auditor role of the organization (1.75.3: to read the report jobs' findings). Never a write role.", type: bool, default: false}
  overwrite: {description: Also bring up to date what was made or changed by hand., type: bool, default: false}
  schedules: {description: "[{name, target (a template or workflow of the spec), every_minutes, extra_data}].", type: list, elements: dict, default: []}
  scorecard: {description: "{name, schedule ('weekly Mon 06:00', 'daily 06:00' or ''), schedule_name, time_zone, split}; {} = none.", type: dict, default: {}}
  publish: {description: "1.78.1: patterns (case-insensitive, * and ?) of the names of job templates and workflows to put on the portal: each gets the label (publish_label) and Execute for portal_user. Nothing is removed.", type: list, elements: str, default: []}
  publish_area: {description: "1.78.1: {pattern: area} - a published template whose name matches gets the label <area_label><area> (the first pattern wins; an area label already there stays).", type: dict, default: {}}
  unpublish: {description: "1.78.1: patterns of the names to take off the portal: the label (publish_label) is removed, never a role.", type: list, elements: str, default: []}
  publish_label: {description: The label that marks a template for the portal (portal_label)., type: str, default: portal}
  area_label: {description: The start of an area label (portal_label_area)., type: str, default: "portal-area:"}
  publish_max: {description: "1.78.1: the most templates one pattern may match; more, and nothing is changed (the list is said).", type: int, default: 50}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
results: {description: "Each object: {kind (template|workflow|grant|schedule), name, action (create|update|unchanged|hand-edited|
  not ours|skipped|failed), diff ({field: [now, wanted]}), why}.", type: list}
problems: {description: What could not be done (a project, inventory or credential not found, AAP not reached)., type: list}
'''

import fnmatch
import hashlib
import json
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

MARK = re.compile(r"\s*\[set up by AAP - set up portal actions, fingerprint ([0-9a-f]{12})\]\s*$")
MARK_TEXT = " [set up by AAP - set up portal actions, fingerprint %s]"
NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user that may make job templates and workflows in the organization, and give roles on them)')


def fingerprint(fields):
    """The fingerprint of what this module wrote: its fields as AAP gives them back alike (JSON text as its value, None as '')."""
    norm = dict((k, _norm(v)) for k, v in fields.items())
    return hashlib.sha256(json.dumps(norm, sort_keys=True, default=str).encode()).hexdigest()[:12]


def split_desc(desc):
    """(the description without the mark, the fingerprint in it or '')."""
    d = str(desc or "")
    m = MARK.search(d)
    return (d[:m.start()], m.group(1)) if m else (d, "")


class Setup(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.results, self.problems = [], []
        self.cache = {}

    # ---- reading
    def all(self, path):
        out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
        for _ in range(200):
            code, d = self.api.call("GET", nxt)
            if code != 200:
                raise RuntimeError("%s could not be read (HTTP %d): %s" % (path.split("?")[0], code, error_text(d)))
            out += d.get("results") or []
            n = d.get("next")
            if not n:
                return out
            nxt = n.split(self.api.base, 1)[1] if self.api.base in n else n.lstrip("/")
        return out

    def by_name(self, kind, name, extra=""):
        key = (kind, name, extra)
        if key not in self.cache:
            field = "username" if kind == "users" else "name"          # a user is found by its user name
            rows = [r for r in self.all("%s/?%s=%s%s" % (kind, field, _q(name), extra)) if r.get(field) == name]
            self.cache[key] = rows
        return self.cache[key]

    def one_id(self, kind, name, what):
        rows = self.by_name(kind, name)
        if len(rows) != 1:
            self.problems.append("%s %r %s in AAP" % (what, name, "is not there" if not rows else "is there %d times" % len(rows)))
            return None
        return int(rows[0]["id"])

    # ---- writing
    def write(self, method, path, body=None):
        if self.check:
            return 200, {}
        code, d = self.api.call(method, path, body)
        if code not in (200, 201, 202, 204):
            raise RuntimeError("%s %s: HTTP %d: %s" % (method, path.split("?")[0], code, error_text(d)))
        return code, d

    def note(self, kind, name, action, diff=None, why=""):
        self.results.append({"kind": kind, "name": name, "action": action, "diff": diff or {}, "why": why})

    # ---- a job template or a workflow: made, brought up to date, or left (hand-edited / not ours)
    def reconcile(self, kind, api_kind, name, want, extra_read=None, extra_write=None):
        """want: the fields this module sets (description without the mark). extra_read(obj) -> the rest of what it owns
        as AAP has it now (credentials, nodes); extra_write(id, created) writes that rest. -> (id, action)."""
        rows = self.by_name(api_kind, name)
        if len(rows) > 1:
            self.note(kind, name, "skipped", why="AAP has %d of that name: rename one" % len(rows))
            return None, "skipped"
        desc = want.get("description", "")
        if not rows:
            body = dict(want, description=desc + MARK_TEXT % fingerprint(dict(want, extra=extra_write("wanted", True) if extra_write else None)))
            code, d = self.write("POST", "%s/" % api_kind, body)
            oid = int(d.get("id")) if d.get("id") else (-1 if self.check else None)      # check mode: one that would be made
            if extra_write and oid and oid > 0:
                extra_write(oid, True)
            self.note(kind, name, "create", dict((k, [None, v]) for k, v in want.items()))
            return oid, "create"
        obj = rows[0]
        oid = int(obj["id"])
        now_desc, mark = split_desc(obj.get("description"))
        now = dict((k, obj.get(k)) for k in want if k != "description")
        now["description"] = now_desc
        now_extra = extra_read(oid) if extra_read else None
        want_extra = extra_write("wanted", False) if extra_write else None
        diff = dict((k, [now.get(k), v]) for k, v in want.items() if _norm(now.get(k)) != _norm(v))
        if extra_read and _norm(now_extra) != _norm(want_extra):
            diff["(its parts)"] = [now_extra, want_extra]
        recorded_ok = mark and mark == fingerprint(dict(now, extra=now_extra))
        if not mark and not self.p["overwrite"]:
            self.note(kind, name, "not ours", diff, "AAP has one of that name this job did not make: left as it is (portal_setup_overwrite: true takes it over)")
            return oid, "not ours"
        if mark and not recorded_ok and not self.p["overwrite"]:
            self.note(kind, name, "hand-edited", diff, "changed by hand since this job wrote it: left as it is (portal_setup_overwrite: true writes it again)")
            return oid, "hand-edited"
        if not diff and recorded_ok:
            self.note(kind, name, "unchanged")
            return oid, "unchanged"
        body = dict((k, want[k]) for k in want)
        body["description"] = desc + MARK_TEXT % fingerprint(dict(want, extra=want_extra))
        self.write("PATCH", "%s/%d/" % (api_kind, oid), body)
        if extra_write and "(its parts)" in diff:
            extra_write(oid, False)
        self.note(kind, name, "update", diff)
        return oid, "update"

    # ---- job templates
    def template(self, t, refs, copy=None, with_action=False):
        """copy: a template as AAP has it - its project, inventory, execution environment and credentials are taken as they are."""
        if copy:
            cred_ids, why = sorted(int(c["id"]) for c in self.all("job_templates/%d/credentials/" % int(copy["id"]))), ""
            inv = copy.get("inventory")
            # its execution environment as it is: none (null) = inherited from the project or organization, kept so
            ee = copy.get("execution_environment") if "execution_environment" in copy else refs.get("ee")
            refs = dict(refs, project=copy.get("project") or refs["project"], ee=ee)
        else:
            cred_ids, why = self.cred_ids(t.get("credentials") or [])
            inv = refs["inventories"].get(t.get("inventory") or "default") or refs["inventories"].get("default")
        if why or not inv:
            self.note("template", t["name"], "skipped", why=why or "no inventory %r (portal_setup_inventories)" % (t.get("inventory") or "default"))
            return (None, "skipped") if with_action else None
        prompts = t.get("prompts") or {}
        want = {"name": t["name"], "description": str(t.get("description") or ""), "job_type": "run", "project": refs["project"],
                "playbook": t["playbook"], "inventory": inv, "ask_variables_on_launch": bool(prompts.get("variables")),
                "ask_limit_on_launch": bool(prompts.get("limit")), "become_enabled": bool(t.get("become")),
                "extra_vars": json.dumps(t.get("extra_vars") or {}, sort_keys=True) if t.get("extra_vars") else ""}
        if refs.get("ee"):
            want["execution_environment"] = refs["ee"]

        def read(oid):
            return sorted(int(c["id"]) for c in self.all("job_templates/%d/credentials/" % oid))

        def write(oid, created):
            if oid == "wanted":
                return sorted(cred_ids)
            have = [] if created else read(oid)
            for cid in sorted(set(have) - set(cred_ids)):
                self.write("POST", "job_templates/%d/credentials/" % oid, {"id": cid, "disassociate": True})
            for cid in sorted(set(cred_ids) - set(have)):
                self.write("POST", "job_templates/%d/credentials/" % oid, {"id": cid})
            return None
        oid, action = self.reconcile("template", "job_templates", t["name"], want, read, write)
        return (oid, action) if with_action else oid

    def cred_ids(self, kinds):
        out = []
        for k in kinds:
            names = self.p["credentials"].get(k)
            if not names:
                return [], "no credential of kind %s (portal_setup_credentials: {%s: <its name in AAP>})" % (k, k)
            for n in names if isinstance(names, list) else [names]:
                rows = self.by_name("credentials", n)
                if len(rows) != 1:
                    return [], "credential %r %s" % (n, "is not there" if not rows else "is there %d times" % len(rows))
                out.append(int(rows[0]["id"]))
        return out, ""

    # ---- 1.68.1: the scorecard's own template and schedule; workflow 40's check on import
    def split_vars(self, t, final=True):
        """Workflow 40's check template with stigman_ckl_import_mode import - unless the mode there is another one.
        final=False (the first pass, before the scorecard is in place): the mode as it is now, never a new one."""
        rows = self.all("job_templates/?name=%s" % _q(t["name"]))
        rows = [r for r in rows if r.get("name") == t["name"]]
        want = dict(t.get("extra_vars") or {})
        if len(rows) == 1:
            live = parse_vars(rows[0].get("extra_vars"))
            if live is not None and live.get(MODE) not in (None, "", "import", "import and scorecard"):
                want[MODE] = live[MODE]                 # a person chose it: kept
                return dict(t, extra_vars=want)
        if final or (len(rows) == 1 and (parse_vars(rows[0].get("extra_vars")) or {}).get(MODE) == "import"):
            want[MODE] = "import"
        return dict(t, extra_vars=want)

    def apply_split(self, src_spec, action, refs, oid):
        """After the scorecard's template and schedule are in place: workflow 40's check imports only."""
        t = self.split_vars(src_spec)
        if action in ("hand-edited", "not ours"):
            self.split_by_hand(t["name"])
        elif action in ("create", "update", "unchanged"):
            if oid and oid < 0:                             # Check: made by this run, so the mode would be set then
                self.note("setting", "%s on %s" % (MODE, t["name"]), "create", {MODE: [None, "import"]})
                return
            self.cache.pop(("job_templates", t["name"], ""), None)
            n = len(self.results)
            self.template(t, refs, with_action=True)
            if len(self.results) == n + 1 and self.results[-1]["action"] == "unchanged":
                self.results.pop()                          # the first pass said so already

    def split_by_hand(self, name):
        """The check template was changed by hand (or not made by this job): only its mode is set, nothing else."""
        rows = [r for r in self.all("job_templates/?name=%s" % _q(name)) if r.get("name") == name]
        if len(rows) != 1:
            return
        live = parse_vars(rows[0].get("extra_vars"))
        what = "%s on %s" % (MODE, name)
        if live is None:
            self.note("setting", what, "skipped", why="its Variables could not be read: set %s: import there" % MODE)
            return
        now = live.get(MODE)
        if now in ("import",):
            self.note("setting", what, "unchanged")
        elif now in (None, "", "import and scorecard"):
            live[MODE] = "import"
            self.write("PATCH", "job_templates/%d/" % int(rows[0]["id"]), {"extra_vars": json.dumps(live, sort_keys=True)})
            self.note("setting", what, "update", {MODE: [now or "(not set)", "import"]})
        else:
            self.note("setting", what, "unchanged", why="it is %r: left as it is (the scorecard has its own template)" % now)

    def scorecard(self, sc, src, refs, ids, src_oid, src_action=""):
        """-> True when the scorecard's template and (if wanted) its schedule are in place."""
        name = sc.get("name") or ""
        if not name:
            return False
        live = [r for r in self.all("job_templates/?name=%s" % _q(src["name"])) if r.get("name") == src["name"]]
        if len(live) == 1:
            vars_ = parse_vars(live[0].get("extra_vars"))
            if vars_ is None:
                self.note("template", name, "skipped", why="the Variables of '%s' could not be read" % src["name"])
                return False
            copy = live[0]
        elif src_oid and src_oid < 0:                   # Check: the source would be made by this run
            vars_, copy = dict(src.get("extra_vars") or {}), None
        else:
            self.note("template", name, "skipped", why=("'%s' was not set up (see above)" if src_action == "skipped" else "'%s' is not in AAP: run again once it is") % src["name"])
            return False
        vars_.pop("stigman_ckl_import_workflow", None)  # the check's "fail when something waits for approval"
        vars_[MODE] = "scorecard"
        t = dict(src, name=name, extra_vars=vars_, credentials=src.get("credentials") or [],
                 description="The weekly STIG scorecard: STIG Manager's collections read, the report and its email. No approval.")
        oid, action = self.template(t, refs, copy, with_action=True)
        if not oid or action == "skipped":
            return False
        sname = sc.get("schedule_name") or "Weekly - %s" % name
        if action in ("not ours", "hand-edited"):        # one that was there: only when it is the scorecard, by what it runs
            row = next((r for r in self.all("job_templates/?name=%s" % _q(name)) if r.get("name") == name), {})
            mode = (parse_vars(row.get("extra_vars")) or {}).get(MODE)
            if row.get("playbook") != src["playbook"] or mode != "scorecard":
                self.note("schedule", sname, "skipped", why="'%s' is there and is not the scorecard (its playbook or %s differs): no schedule, nothing else changed" % (name, MODE))
                return False
        ids[name] = ("job_templates", oid)
        when = sc.get("schedule") or ""
        if not when:
            return True
        rrule = rrule_of(when, sc.get("time_zone") or "")      # checked before any change (run)
        if oid < 0:
            self.note("schedule", sname, "create", {"rrule": [None, rrule]})
            return True
        rows = [r for r in self.all("job_templates/%d/schedules/" % oid) if r.get("name") == sname]
        if rows:
            self.note("schedule", sname, "unchanged", why="" if rows[0].get("rrule") == rrule else "its time is not the setting: left as it is")
            return True
        try:
            self.write("POST", "job_templates/%d/schedules/" % oid, {"name": sname, "rrule": rrule, "extra_data": {}, "enabled": True})
        except RuntimeError as e:
            self.note("schedule", sname, "failed", why=str(e)[:160])
            return False
        self.note("schedule", sname, "create", {"rrule": [None, rrule]})
        return True

    # ---- workflows
    def workflow(self, w, tpl_ids, refs):
        prompts = w.get("prompts") or {}
        want = {"name": w["name"], "description": str(w.get("description") or ""), "organization": refs["org"],
                "ask_variables_on_launch": bool(prompts.get("variables")), "ask_limit_on_launch": bool(prompts.get("limit")),
                "extra_vars": json.dumps(w.get("extra_vars") or {}, sort_keys=True) if w.get("extra_vars") else ""}
        nodes = w.get("nodes") or []
        for n in nodes:
            if n.get("template") and n["template"] not in tpl_ids:
                rows = self.by_name("job_templates", n["template"])
                if len(rows) == 1:
                    tpl_ids[n["template"]] = int(rows[0]["id"])
        missing = [n["template"] for n in nodes if n.get("template") and not tpl_ids.get(n["template"])]
        if missing:
            self.note("workflow", w["name"], "skipped", why="its template(s) %s are not in AAP" % ", ".join(sorted(set(missing))))
            return None

        def shape(nodes_spec):
            out = []
            for n in nodes_spec:
                a = n.get("approval") or {}
                out.append({"id": n["id"], "template": n.get("template") or "",
                            "approval": {"name": a.get("name", ""), "timeout": int(a.get("timeout") or 0),
                                         "description": a.get("description", "")} if n.get("approval") else None,
                            "success": sorted(n.get("success") or []), "failure": sorted(n.get("failure") or []),
                            "always": sorted(n.get("always") or []), "converge": bool(n.get("all_parents_must_converge")),
                            "extra_data": dict(n.get("extra_data") or {})})       # 1.67.0: e.g. the gate's workflow_gate_var
            return sorted(out, key=lambda x: x["id"])

        def read(oid):
            got = self.all("workflow_job_template_nodes/?workflow_job_template=%d" % oid)
            ident = dict((int(g["id"]), g.get("identifier") or str(g["id"])) for g in got)
            out = []
            for g in got:
                sf = g.get("summary_fields") or {}
                ujt = sf.get("unified_job_template") or {}
                appr = ujt.get("unified_job_type") == "workflow_approval"
                out.append({"id": ident[int(g["id"])], "template": "" if appr else ujt.get("name", ""),
                            "approval": {"name": ujt.get("name", ""), "timeout": int(ujt.get("timeout") or 0),
                                         "description": ujt.get("description", "")} if appr else None,
                            "success": sorted(ident.get(int(x), str(x)) for x in g.get("success_nodes") or []),
                            "failure": sorted(ident.get(int(x), str(x)) for x in g.get("failure_nodes") or []),
                            "always": sorted(ident.get(int(x), str(x)) for x in g.get("always_nodes") or []),
                            "converge": bool(g.get("all_parents_must_converge")),
                            "extra_data": dict(_norm(g.get("extra_data")) or {}) if not appr else {}})
            return sorted(out, key=lambda x: x["id"])

        def write(oid, created):
            if oid == "wanted":
                return shape(nodes)
            if not created:                 # the workflow's own nodes, made again (never a template or workflow)
                for g in self.all("workflow_job_template_nodes/?workflow_job_template=%d" % oid):
                    self.write("DELETE", "workflow_job_template_nodes/%d/" % int(g["id"]))
            made = {}
            for n in nodes:
                body = {"identifier": n["id"], "all_parents_must_converge": bool(n.get("all_parents_must_converge"))}
                if n.get("template"):
                    body["unified_job_template"] = tpl_ids[n["template"]]
                if n.get("extra_data"):              # the template must ask for Variables on launch (prompts: variables)
                    body["extra_data"] = dict(n["extra_data"])
                code, d = self.write("POST", "workflow_job_templates/%d/workflow_nodes/" % oid, body)
                nid = int(d.get("id") or 0)
                made[n["id"]] = nid
                if n.get("approval"):
                    a = n["approval"]
                    self.write("POST", "workflow_job_template_nodes/%d/create_approval_template/" % nid,
                               {"name": a.get("name", n["id"]), "description": a.get("description", ""), "timeout": int(a.get("timeout") or 0)})
            for n in nodes:
                for link in ("success", "failure", "always"):
                    for child in n.get(link) or []:
                        self.write("POST", "workflow_job_template_nodes/%d/%s_nodes/" % (made[n["id"]], link), {"id": made[child]})
            return None
        oid, _ = self.reconcile("workflow", "workflow_job_templates", w["name"], want, read, write)
        return oid

    # ---- grants: Execute (and Approve on a workflow with an approval node) for the portal's account
    def grant(self, api_kind, oid, name, roles, uid, kind="grant"):
        if oid < 0:                                       # check mode: made by this run, so none of its roles is given yet
            for role in roles:
                self.note(kind, "%s: %s" % (name, role), "create")
            return
        have = set(int(r["id"]) for r in self.all("users/%d/roles/" % uid))
        avail = dict((r.get("name"), int(r["id"])) for r in self.all("%s/%d/object_roles/" % (api_kind, oid)))
        for role in roles:
            rid = avail.get(role)
            if rid is None:
                self.note(kind, "%s: %s" % (name, role), "failed", why="AAP has no %s role on it" % role)
            elif rid in have:
                self.note(kind, "%s: %s" % (name, role), "unchanged")
            else:
                self.write("POST", "users/%d/roles/" % uid, {"id": rid})
                self.note(kind, "%s: %s" % (name, role), "create")

    # ---- 1.78.1: put your own jobs on the portal by name patterns (the label + Execute), take them off again (the label only)
    @staticmethod
    def _matches(pattern, name):
        return fnmatch.fnmatchcase(str(name).lower(), str(pattern).strip().lower())

    def publish_plan(self):
        """Read-only, before any change -> {publish: [(api_kind, row, [areas])], unpublish: [(api_kind, row)]} or None when a setting is wrong
        (a problem is said and nothing is changed): a pattern that is not text, one that matches more than publish_max, a template in both lists."""
        p = self.p
        pubs, unpubs, areas = list(p.get("publish") or []), list(p.get("unpublish") or []), dict(p.get("publish_area") or {})
        if not pubs and not unpubs:
            return {"publish": [], "unpublish": [], "pats": {}}
        for label, pats in (("portal_publish_templates", pubs), ("portal_unpublish_templates", unpubs)):
            if not all(isinstance(x, str) and x.strip() for x in pats):
                self.problems.append("%s must be a list of name patterns (text)" % label)
                return None
        for pat, area in areas.items():
            if not isinstance(area, str) or not area.strip() or len(area) > 60 or "\n" in area:
                self.problems.append("portal_publish_area: the area of %r must be short text" % pat)
                return None
        rows = []
        for api_kind in ("job_templates", "workflow_job_templates"):
            rows += [(api_kind, r) for r in self.all(api_kind + "/")]
        mx = max(1, int(p.get("publish_max") or 50))
        found = {}
        for label, pats in (("portal_publish_templates", pubs), ("portal_unpublish_templates", unpubs)):
            for pat in pats:
                hit = [(k, r) for k, r in rows if self._matches(pat, r.get("name"))]
                if len(hit) > mx:
                    self.problems.append("%s: %r matches %d templates and workflows, more than portal_publish_max (%d): %s - narrow the pattern; nothing was changed" % (
                        label, pat, len(hit), mx, ", ".join(sorted(r["name"] for _k, r in hit)[:20]) + (", ..." if len(hit) > 20 else "")))
                    return None
                found[(label, pat)] = hit
        both = set((k, r["id"]) for pat in pubs for k, r in found[("portal_publish_templates", pat)]) & \
            set((k, r["id"]) for pat in unpubs for k, r in found[("portal_unpublish_templates", pat)])
        if both:
            names = sorted(r["name"] for k, r in rows if (k, r["id"]) in both)
            self.problems.append("portal_publish_templates and portal_unpublish_templates both match: %s (nothing was changed)" % ", ".join(names[:10]))
            return None
        pub, unpub, seen_p, seen_u = [], [], set(), set()
        for pat in pubs:
            hit = found[("portal_publish_templates", pat)]
            if not hit:
                self.note("publish", pat, "skipped", why="matches no job template or workflow")
            for k, r in hit:
                if (k, r["id"]) not in seen_p:
                    seen_p.add((k, r["id"]))
                    pub.append((k, r))
        for pat in unpubs:
            hit = found[("portal_unpublish_templates", pat)]
            if not hit:
                self.note("unpublish", pat, "skipped", why="matches no job template or workflow")
            for k, r in hit:
                if (k, r["id"]) not in seen_u:
                    seen_u.add((k, r["id"]))
                    unpub.append((k, r))
        return {"publish": pub, "unpublish": unpub, "areas": areas}

    def labels_on(self, api_kind, row):
        """[(label id, name)] of a template or workflow (all of them: AAP lists ten in the row)."""
        return [(int(x["id"]), str(x.get("name") or "")) for x in self.all("%s/%d/labels/" % (api_kind, int(row["id"])))]

    def publish_apply(self, plan):
        p = self.p
        label, prefix = p.get("publish_label") or "portal", p.get("area_label") or "portal-area:"
        uid = None
        if plan["publish"]:
            if p["portal_user"]:
                uid = self.one_id("users", p["portal_user"], "User")
            for api_kind, row in plan["publish"]:
                name, oid = str(row.get("name")), int(row["id"])
                org = row.get("organization") or ((row.get("summary_fields") or {}).get("organization") or {}).get("id")
                have = [n for _i, n in self.labels_on(api_kind, row)]
                if label in have:
                    self.note("label", "%s: %s" % (name, label), "unchanged", why="already done")
                elif not org:
                    self.note("label", "%s: %s" % (name, label), "skipped", why="it has no organization to hold the label")
                else:
                    self.write("POST", "%s/%d/labels/" % (api_kind, oid), {"name": label, "organization": int(org)})
                    self.note("label", "%s: %s" % (name, label), "create")
                area = next((a for pat, a in plan["areas"].items() if self._matches(pat, name)), None)
                if area:
                    want = prefix + area.strip()
                    has_area = [n for n in have if n.startswith(prefix)]
                    if want in have:
                        self.note("label", "%s: %s" % (name, want), "unchanged", why="already done")
                    elif has_area:
                        self.note("label", "%s: %s" % (name, want), "skipped", why="it is already in the area %r (an area label is never changed here)" % has_area[0][len(prefix):])
                    elif org:
                        self.write("POST", "%s/%d/labels/" % (api_kind, oid), {"name": want, "organization": int(org)})
                        self.note("label", "%s: %s" % (name, want), "create")
                if uid:
                    self.grant(api_kind, oid, name, ["Execute"], uid, kind="publish grant")
                elif not p["portal_user"]:
                    self.note("publish grant", "%s: Execute" % name, "skipped", why="portal_setup_portal_user is not set: no role is given")
        for api_kind, row in plan["unpublish"]:
            name, oid = str(row.get("name")), int(row["id"])
            mine = [i for i, n in self.labels_on(api_kind, row) if n == label]
            if not mine:
                self.note("label", "%s: %s" % (name, label), "unchanged", why="already done")
            else:
                self.write("POST", "%s/%d/labels/" % (api_kind, oid), {"id": mine[0], "disassociate": True})
                self.note("label", "%s: %s" % (name, label), "remove")

    # ---- schedules
    def schedule(self, s, ids):
        target = s.get("target", "")
        kind, oid = ids.get(target, (None, None))
        if not oid:
            self.note("schedule", s.get("name", ""), "skipped", why="its target %r is not a template or workflow of this setup" % target)
            return
        every = int(s.get("every_minutes") or 0)
        if every < 1:
            self.note("schedule", s.get("name", ""), "skipped", why="every_minutes must be 1 or more")
            return
        want = {"name": s["name"], "rrule": "DTSTART;TZID=UTC:20260101T000000 RRULE:FREQ=MINUTELY;INTERVAL=%d" % every,
                "extra_data": s.get("extra_data") or {}, "enabled": True}
        if oid < 0:                                       # check mode: on one this run would make
            self.note("schedule", s["name"], "create", dict((k, [None, v]) for k, v in want.items()))
            return
        rows = [r for r in self.all("%s/%d/schedules/" % (kind, oid)) if r.get("name") == s["name"]]
        if not rows:
            self.write("POST", "%s/%d/schedules/" % (kind, oid), want)
            self.note("schedule", s["name"], "create", dict((k, [None, v]) for k, v in want.items()))
            return
        r = rows[0]
        diff = dict((k, [r.get(k), v]) for k, v in want.items() if _norm(r.get(k)) != _norm(v))
        if diff:
            self.write("PATCH", "schedules/%d/" % int(r["id"]), want)
        self.note("schedule", s["name"], "update" if diff else "unchanged", diff)

    def run(self):
        p = self.p
        sc = p["scorecard"] or {}
        if sc.get("name"):                               # before any change: a name of the spec is never the scorecard's
            taken = [t["name"] for t in p["spec"].get("templates") or []]
            if sc["name"] in taken:
                self.problems.append("portal_setup_scorecard_template %r is the name of a job template of workflows.yml: "
                                     "choose another name (nothing was changed)" % sc["name"])
                return
            if sc.get("schedule"):
                try:
                    rrule_of(sc["schedule"], sc.get("time_zone") or "")
                except ValueError as e:
                    self.problems.append("portal_setup_scorecard_schedule: %s (nothing was changed)" % e)
                    return
        plan = self.publish_plan()                       # read-only, before any change: a pattern that matches too many stops everything
        if plan is None:
            return
        org = self.one_id("organizations", p["organization"], "Organization")
        proj = self.one_id("projects", p["project"], "Project")
        ee = self.one_id("execution_environments", p["execution_environment"], "Execution environment") if p["execution_environment"] else None
        invs = {}
        for key, name in (p["inventories"] or {}).items():
            iid = self.one_id("inventories", name, "Inventory")
            if iid:
                invs[key] = iid
        if not org or not proj or "default" not in invs:
            if "default" not in invs and not any("Inventory" in x for x in self.problems):
                self.problems.append("no default inventory (portal_setup_inventories: {default: <its name>})")
            return
        refs = {"org": org, "project": proj, "ee": ee, "inventories": invs}
        tpl_ids, ids = {}, {}
        src = source_template(p["spec"]) if sc else None
        src_oid, src_spec, src_action = None, None, ""
        for t in p["spec"].get("templates") or []:
            is_src = bool(src) and t["name"] == src
            if is_src:
                src_spec = t
                if sc.get("split"):
                    t = self.split_vars(t, final=False)     # the mode as it is: set after the scorecard is in place
            oid, action = self.template(t, refs, with_action=True)
            if is_src:
                src_oid, src_action = oid, action
            if oid:
                tpl_ids[t["name"]] = oid
                ids[t["name"]] = ("job_templates", oid)
        if sc and src_spec:
            in_place = self.scorecard(sc, src_spec, refs, ids, src_oid, src_action)
            if sc.get("split") and src_oid and src_action != "skipped":
                if in_place:
                    self.apply_split(src_spec, src_action, refs, src_oid)
                else:
                    self.note("setting", "%s on %s" % (MODE, src_spec["name"]), "unchanged",
                              why="the scorecard's template or schedule is not in place: this check keeps its mode, so no scorecard is lost")
        wf_ids = {}
        for w in p["spec"].get("workflows") or []:
            oid = self.workflow(w, tpl_ids, refs)
            if oid:
                wf_ids[w["name"]] = (oid, any(n.get("approval") for n in w.get("nodes") or []))
                ids[w["name"]] = ("workflow_job_templates", oid)
        if p["portal_user"]:
            uid = self.one_id("users", p["portal_user"], "User")
            for name, a in sorted((p["actions"] or {}).items()):
                target = (a or {}).get("aap") or {}
                tn = target.get("name", "")
                if target.get("type") == "workflow":
                    oid, appr = wf_ids.get(tn, (None, False))
                    if oid is None:
                        rows = self.by_name("workflow_job_templates", tn)
                        oid = int(rows[0]["id"]) if len(rows) == 1 else None
                        appr = bool((a or {}).get("approval"))
                    if uid and oid:
                        self.grant("workflow_job_templates", oid, tn, ["Execute"] + (["Approve"] if appr else []), uid)
                    elif uid:
                        self.note("grant", tn, "skipped", why="workflow %r is not in AAP" % tn)
                elif target.get("type") == "job":
                    oid = tpl_ids.get(tn)
                    if oid is None:
                        rows = self.by_name("job_templates", tn)
                        oid = int(rows[0]["id"]) if len(rows) == 1 else None
                    if uid and oid:
                        self.grant("job_templates", oid, tn, ["Execute"], uid)
                    elif uid:
                        self.note("grant", tn, "skipped", why="job template %r is not in AAP" % tn)
        if p["portal_user"] and p.get("portal_auditor"):        # 1.75.3: Auditor of the organization: it reads the report jobs' artifacts
            uid = self.one_id("users", p["portal_user"], "User")
            if uid and org:
                self.grant("organizations", org, p["organization"], ["Auditor"], uid)
        for s in p["schedules"] or []:
            self.schedule(s, ids)
        self.publish_apply(plan)


def check_spec(spec):
    """What is wrong with workflows.yml ([] = nothing): names, playbooks, node ids, links, approval nodes."""
    out = []
    if not isinstance(spec, dict):
        return ["workflows.yml must be a mapping with templates and workflows"]
    names = set()
    for t in spec.get("templates") or []:
        if not isinstance(t, dict) or not str(t.get("name") or "").strip() or not re.match(r"^playbooks/[A-Za-z0-9_./-]+\.ya?ml$", str(t.get("playbook") or "")):
            out.append("a template needs name and playbook (playbooks/<file>.yml): %r" % (t,))
            continue
        if t["name"] in names:
            out.append("template %r is there twice" % t["name"])
        names.add(t["name"])
        if not isinstance(t.get("credentials") or [], list):
            out.append("template %r: credentials must be a list of kinds" % t["name"])
    numbers, wnames = set(), set()
    for w in spec.get("workflows") or []:
        if not isinstance(w, dict) or not str(w.get("name") or "").strip():
            out.append("a workflow needs a name: %r" % (w,))
            continue
        where = "workflow %r" % w["name"]
        if w["name"] in wnames:
            out.append("%s is there twice" % where)
        wnames.add(w["name"])
        if w.get("number") in numbers:
            out.append("%s: number %s is taken" % (where, w.get("number")))
        numbers.add(w.get("number"))
        ids = [n.get("id") for n in w.get("nodes") or [] if isinstance(n, dict)]
        if not ids or len(set(ids)) != len(ids) or not all(isinstance(i, str) and re.match(r"^[a-z][a-z0-9_]{0,40}$", i) for i in ids):
            out.append("%s: each node needs an id of its own (lower case letters, digits, _)" % where)
            continue
        for n in w["nodes"]:
            if bool(n.get("template")) == bool(n.get("approval")):
                out.append("%s, node %s: either template or approval" % (where, n["id"]))
            if n.get("extra_data") is not None and (not isinstance(n.get("extra_data"), dict) or not n.get("template")):
                out.append("%s, node %s: extra_data is a mapping, on a template's node" % (where, n["id"]))
            if n.get("approval") and (not str(n["approval"].get("name") or "").strip() or not isinstance(n["approval"].get("timeout", 0), int)):
                out.append("%s, node %s: an approval needs a name (and a timeout in seconds)" % (where, n["id"]))
            for link in ("success", "failure", "always"):
                for child in n.get(link) or []:
                    if child not in ids or child == n["id"]:
                        out.append("%s, node %s: %s link to %r, not another node of it" % (where, n["id"], link, child))
    return out


MODE = "stigman_ckl_import_mode"
DAYS = {"mon": "MO", "tue": "TU", "wed": "WE", "thu": "TH", "fri": "FR", "sat": "SA", "sun": "SU"}


def parse_vars(text):
    """A template's Variables (JSON or YAML text) as a dict; None when they cannot be read."""
    if isinstance(text, dict):
        return dict(text)
    t = str(text or "").strip()
    if not t:
        return {}
    try:
        d = json.loads(t)
    except ValueError:
        try:
            import yaml
            d = yaml.safe_load(t)
        except Exception:
            return None
    return d if isinstance(d, dict) else (None if t not in ("---", "{}") else {})


def rrule_of(when, tz):
    """AAP's schedule rule for 'weekly Mon 06:00' or 'daily 06:00' (in time zone tz, "" = UTC)."""
    w = str(when).strip().lower().split()
    m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", w[-1]) if w else None
    if not m or not (len(w) == 3 and w[0] == "weekly" and w[1][:3] in DAYS or len(w) == 2 and w[0] == "daily"):
        raise ValueError("%r is not 'weekly Mon 06:00' or 'daily 06:00'" % when)
    head = "DTSTART;TZID=%s:20260105T%02d%s00" % (tz or "UTC", int(m.group(1)), m.group(2))
    return head + " RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=%s" % DAYS[w[1][:3]] if w[0] == "weekly" else head + " RRULE:FREQ=DAILY;INTERVAL=1"


def source_template(spec):
    """The check template of workflow 40 (its node 'check')."""
    for w in spec.get("workflows") or []:
        if w.get("number") == 40:
            for n in w.get("nodes") or []:
                if n.get("id") == "check" and n.get("template"):
                    return n["template"]
    return ""


def _q(name):
    try:
        from urllib.parse import quote
    except ImportError:                                  # pragma: no cover
        from urllib import quote
    return quote(str(name))


def _norm(v):
    """Compare AAP's answer and the spec alike: JSON text as its value, None as ''."""
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return "" if v is None else v


def main():
    module = AnsibleModule(
        argument_spec=dict(spec=dict(type="dict", required=True), actions=dict(type="dict", default={}),
                           organization=dict(type="str", required=True), project=dict(type="str", required=True),
                           execution_environment=dict(type="str", default=""), inventories=dict(type="dict", required=True),
                           credentials=dict(type="dict", default={}), portal_user=dict(type="str", default=""), portal_auditor=dict(type="bool", default=False),
                           overwrite=dict(type="bool", default=False), schedules=dict(type="list", elements="dict", default=[]),
                           scorecard=dict(type="dict", default={}),
                           publish=dict(type="list", elements="str", default=[]), publish_area=dict(type="dict", default={}),
                           unpublish=dict(type="list", elements="str", default=[]), publish_label=dict(type="str", default="portal"),
                           area_label=dict(type="str", default="portal-area:"), publish_max=dict(type="int", default=50),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="portal_setup_aap_ca_path", no_credential=NO_CREDENTIAL)
    s = Setup(api, p, module.check_mode)
    bad = check_spec(p["spec"])
    if bad:
        module.exit_json(changed=False, results=[], problems=["workflows.yml: " + x for x in bad], msg="workflows.yml is not right")
    why = api.probe()
    if why:
        module.exit_json(changed=False, results=[], problems=[why], msg=why)
    try:
        s.run()
    except RuntimeError as e:
        s.problems.append(str(e))
    changed = any(r["action"] in ("create", "update", "remove") for r in s.results) and not module.check_mode
    module.exit_json(changed=changed, results=s.results, problems=s.problems, api=api.base)


if __name__ == "__main__":
    main()
