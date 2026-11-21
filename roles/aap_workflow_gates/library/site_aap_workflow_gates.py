#!/usr/bin/python
# -*- coding: utf-8 -*-
"""AAP - add workflow gates (1.67.0 part I, playbooks/aap_workflow_gates.yml): the gate step ("Workflow gate - has a plan")
put into workflows a person built by hand. In each workflow it finds a job node that links ON FAILURE straight to an
approval node, knows the check by its job template's PLAYBOOK (never by a name) and puts the gate between them:
check --failure--> gate (extra_data workflow_gate_var) --success--> approval. It removes only that one failure link;
every other node and link stays. Never deletes a node. Already gated: left alone. An unknown playbook before an approval
is listed, not touched. In check mode it only reads and says what it would change.
The address and login come from the "Red Hat Ansible Automation Platform" credential (plugins/module_utils/site_aap.py).
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_workflow_gates
short_description: Put the gate between each check and its approval in workflows built by hand (never deletes)
description:
  - Reads the workflows (all, or those named), and in each the pairs check --on failure--> approval.
  - A check is known by its job template's playbook (checks; the built-in table covers this repository's checks). Known:
    a gate node is added (its extra_data workflow_gate_var: the check's plan), linked check --failure--> gate --success-->
    approval, and then the one direct failure link is removed. Nothing else changes; no node is deleted.
  - A check that already goes through a gate to its approval is left alone. A playbook not in the table is listed.
  - The gate template is made when AAP has none of that name (as AAP - set up portal actions makes it).
  - In check mode nothing is written; the result says what would change.
options:
  workflows: {description: "The workflows: names, ids or patterns (* and ?, any case); [] = every workflow.", type: list, elements: raw, default: []}
  checks: {description: "More checks, {playbook file name: its plan's variable} - added to the built-in table (or replacing an entry).", type: dict, default: {}}
  gate: {description: "The gate template's spec from workflows.yml: {name, playbook, description, prompts}.", type: dict, required: true}
  describe: {description: The catalog descriptions and Contact settings used by portal setup., type: dict, default: {}}
  project: {description: "The project the gate template runs from, when it must be made (its name).", type: str, default: ''}
  inventory: {description: "The inventory of the gate template, when it must be made (its name).", type: str, default: ''}
  execution_environment: {description: "Its execution environment ('' = the project's default).", type: str, default: ''}
  ca_path: {description: A CA file for AAP's HTTPS certificate., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
workflows: {description: "Each workflow read: {name, id, status (gated now | would gate | already gated | not a check->approval | unknown check |
  failed), pairs: [{check, playbook, approval, var, action (gated | would gate | already gated | unknown check | no gate), why}]}.", type: list}
gate_template: {description: "The gate template: {name, id, action (there | made | would be made)}.", type: dict}
problems: {description: What could not be done., type: list}
'''

import fnmatch
import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text, setup_description

# The checks of this repository whose approval goes behind the gate, by playbook - docs/WORKFLOWS_AND_SCHEDULES.md, "The gate"
CHECKS = {
    "fapolicyd.yml": "fapolicyd_workflow_plan",
    "stigman_ckl_import.yml": "stigman_update_plan",
    "stig_recheck.yml": "stigman_update_plan",
    "win_stig_recheck.yml": "stigman_update_plan",
    "cert_install_check.yml": "cert_install_plan",
    "win_cert_install_check.yml": "cert_install_plan",
    "vmware_cert_install_check.yml": "vmware_cert_install_plan",
    "horizon_desktops_check.yml": "horizon_desktops_fix",
    "service_watch.yml": "service_watch_fix",
}
# Before an approval, but no gate: ACT's fix (workflow 5) - the check hands on ACT's analysis for every host, a plan or not
NO_GATE = {
    "health_check.yml": "ACT's fix (workflow 5): its check hands on an analysis for every host, so a gate cannot tell; "
                        "its apply step skips hosts with nothing approved",
    "win_health_check.yml": "ACT's fix: its check hands on an analysis for every host; the apply step skips what was not approved",
    "troubleshoot.yml": "ACT's fix: its check hands on an analysis for every host; the apply step skips what was not approved",
}
GATE_PLAYBOOK = "workflow_gate.yml"
NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(an AAP user that may change the workflows: Admin on them, and Use on the project and inventory if the gate '
                 'template must be made)')


def playbook_file(p):
    return os.path.basename(str(p or "")).strip()


def wanted(sel, wf):
    """Is workflow wf ({id, name}) one of sel (names, ids, patterns; [] = all)?"""
    if not sel:
        return True
    for s in sel:
        if isinstance(s, int) or (isinstance(s, str) and s.isdigit()):
            if int(s) == int(wf["id"]):
                return True
        elif fnmatch.fnmatchcase(str(wf.get("name") or "").lower(), str(s).lower()):
            return True
    return False


class Gates(object):
    def __init__(self, api, p, check):
        self.api, self.p, self.check = api, p, check
        self.problems, self.out = [], []
        self.checks = dict(CHECKS, **dict((playbook_file(k), str(v)) for k, v in (p["checks"] or {}).items()))
        self.templates = {}
        self.gate = {"name": p["gate"].get("name", ""), "id": None, "action": ""}

    def all(self, path):
        out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
        for _ in range(500):
            code, d = self.api.call("GET", nxt)
            if code != 200:
                raise RuntimeError("%s could not be read (HTTP %d): %s" % (path.split("?")[0], code, error_text(d)))
            out += d.get("results") or []
            n = d.get("next")
            if not n:
                return out
            nxt = n.split(self.api.base, 1)[1] if self.api.base in n else n.lstrip("/")
        return out

    def write(self, method, path, body=None):
        code, d = self.api.call(method, path, body)
        if code not in (200, 201, 202, 204):
            raise RuntimeError("%s %s: HTTP %d: %s" % (method, path.split("?")[0], code, error_text(d)))
        return d

    def template(self, tid):
        if tid not in self.templates:
            code, d = self.api.call("GET", "job_templates/%d/" % tid)
            self.templates[tid] = d if code == 200 else {}
        return self.templates[tid]

    def one(self, kind, name, what):
        rows = [r for r in self.all("%s/?name=%s" % (kind, _q(name))) if r.get("name") == name]
        if len(rows) != 1:
            self.problems.append("%s %r %s in AAP" % (what, name, "is not there" if not rows else "is there %d times" % len(rows)))
            return None
        return rows[0]

    # ---- the gate template: there, or made as AAP - set up portal actions makes it
    def gate_template(self):
        g = self.p["gate"]
        rows = [r for r in self.all("job_templates/?name=%s" % _q(g["name"])) if r.get("name") == g["name"]]
        if len(rows) > 1:
            self.problems.append("job template %r is there %d times: rename one" % (g["name"], len(rows)))
            return None
        if rows:
            t = rows[0]
            if playbook_file(t.get("playbook")) != GATE_PLAYBOOK:
                self.problems.append("job template %r runs %s, not playbooks/%s" % (g["name"], t.get("playbook"), GATE_PLAYBOOK))
                return None
            if not t.get("ask_variables_on_launch"):
                self.problems.append("job template %r: tick Prompt on launch for Variables (the gate node's workflow_gate_var "
                                     "needs it)" % g["name"])
                return None
            self.gate.update(id=int(t["id"]), action="there")
            return int(t["id"])
        if not self.p["project"] or not self.p["inventory"]:
            self.problems.append("no job template %r in AAP: set aap_gates_project and aap_gates_inventory to make it, or run "
                                 "AAP - set up portal actions first" % g["name"])
            return None
        proj = self.one("projects", self.p["project"], "Project")
        inv = self.one("inventories", self.p["inventory"], "Inventory")
        ee = self.one("execution_environments", self.p["execution_environment"], "Execution environment") if self.p["execution_environment"] else None
        if not proj or not inv or (self.p["execution_environment"] and not ee):
            return None
        prompts = g.get("prompts") or {}
        want = {"name": g["name"], "description": setup_description(self.p.get("describe"), "job", g), "job_type": "run", "project": int(proj["id"]),
                "playbook": g.get("playbook") or "playbooks/" + GATE_PLAYBOOK, "inventory": int(inv["id"]),
                "ask_variables_on_launch": bool(prompts.get("variables", True)), "ask_limit_on_launch": bool(prompts.get("limit")),
                "become_enabled": False, "extra_vars": ""}
        if ee:
            want["execution_environment"] = int(ee["id"])
        if self.check:
            self.gate.update(id=-1, action="would be made")
            return -1
        body = dict(want)
        d = self.write("POST", "job_templates/", body)
        self.gate.update(id=int(d["id"]), action="made")
        return int(d["id"])

    # ---- one workflow
    def workflow(self, wf, gate_id, act=True):
        rec = {"name": wf.get("name", ""), "id": int(wf["id"]), "status": "", "pairs": []}
        nodes = self.all("workflow_job_template_nodes/?workflow_job_template=%d" % int(wf["id"]))
        by_id = dict((int(n["id"]), n) for n in nodes)

        def kind(n):
            return ((n.get("summary_fields") or {}).get("unified_job_template") or {}).get("unified_job_type") or ""

        def label(n):
            u = (n.get("summary_fields") or {}).get("unified_job_template") or {}
            return u.get("name") or n.get("identifier") or str(n.get("id"))

        def pb(n):
            u = (n.get("summary_fields") or {}).get("unified_job_template") or {}
            return playbook_file(self.template(int(u["id"])).get("playbook")) if kind(n) == "job" and u.get("id") else ""

        gates = set(int(n["id"]) for n in nodes if kind(n) == "job" and pb(n) == GATE_PLAYBOOK)
        work = []
        for n in nodes:
            if kind(n) != "job" or int(n["id"]) in gates:
                continue
            for c in n.get("failure_nodes") or []:
                child = by_id.get(int(c))
                if child is not None and kind(child) == "workflow_approval":
                    work.append((n, child, False))
                elif int(c) in gates:                       # check --failure--> gate --success--> approval: done before
                    for a in by_id[int(c)].get("success_nodes") or []:
                        if a in by_id and kind(by_id[int(a)]) == "workflow_approval":
                            work.append((n, by_id[int(a)], True))
        gated = set((int(n["id"]), int(a["id"])) for n, a, done in work if done)
        for n, a, done in work:
            if done and any(not d and int(x["id"]) == int(n["id"]) and int(y["id"]) == int(a["id"]) for x, y, d in work):
                continue                                    # a half-done earlier run: the direct link's row says it
            playbook = pb(n)
            row = {"check": label(n), "playbook": playbook, "approval": label(a), "var": self.checks.get(playbook, ""),
                   "action": "", "why": ""}
            rec["pairs"].append(row)
            half = (int(n["id"]), int(a["id"])) in gated
            if done:
                row["action"] = "already gated"
            elif half and self.check:
                row.update(action="would gate", why="the gate is there; the direct failure link would be removed")
            elif half and act:
                try:
                    self.write("POST", "workflow_job_template_nodes/%d/failure_nodes/" % int(n["id"]), {"id": int(a["id"]), "disassociate": True})
                    row.update(action="gated", why="the gate was there; the direct failure link removed")
                except RuntimeError as e:
                    row.update(action="failed", why=str(e))
            elif playbook in NO_GATE and playbook not in self.checks:
                row.update(action="no gate", why=NO_GATE[playbook])
            elif not row["var"]:
                row.update(action="unknown check", why="%s is not a check this job knows (aap_gates_checks: {%s: <its plan's variable>}) - "
                                                       "left as it is" % (playbook or "not a job template", playbook or "?"))
            elif not act:
                row["action"] = "to gate"
            elif gate_id is None:
                row.update(action="failed", why="no gate template (see the problems)")
            elif self.check:
                row["action"] = "would gate"
            else:
                try:
                    self.insert(int(wf["id"]), n, a, row["var"], gate_id)
                    row["action"] = "gated"
                except RuntimeError as e:
                    row.update(action="failed", why=str(e))
        acts = [r["action"] for r in rec["pairs"]]
        rec["status"] = ("failed" if "failed" in acts else "gated now" if "gated" in acts else "would gate" if "would gate" in acts
                         else "unknown check" if "unknown check" in acts else "already gated" if "already gated" in acts
                         else "no gate (ACT's fix)" if "no gate" in acts else "not a check->approval")
        return rec

    def insert(self, wid, check, appr, var, gate_id):
        """The gate node and its two links first, then the one direct link removed: never a moment without a way on."""
        ident = ("gate-%s" % (check.get("identifier") or check["id"]))[:500]
        d = self.write("POST", "workflow_job_templates/%d/workflow_nodes/" % wid,
                       {"unified_job_template": gate_id, "extra_data": {"workflow_gate_var": var}, "identifier": ident})
        gid = int(d["id"])
        self.write("POST", "workflow_job_template_nodes/%d/failure_nodes/" % int(check["id"]), {"id": gid})
        self.write("POST", "workflow_job_template_nodes/%d/success_nodes/" % gid, {"id": int(appr["id"])})
        self.write("POST", "workflow_job_template_nodes/%d/failure_nodes/" % int(check["id"]), {"id": int(appr["id"]), "disassociate": True})

    def run(self):
        wfs = sorted((w for w in self.all("workflow_job_templates/") if wanted(self.p["workflows"], w)),
                     key=lambda x: str(x.get("name") or "").lower())
        for s in self.p["workflows"] or []:
            if not any(wanted([s], w) for w in wfs):
                self.problems.append("aap_gates_workflows: %r matches no workflow" % (s,))
        # first only read: the gate template is looked for (or made) only when a workflow needs it
        first = [self.workflow(w, None, act=False) for w in wfs]
        gate_id = None
        if any(r["action"] in ("to gate", "gated", "would gate") for rec in first for r in rec["pairs"]):
            gate_id = self.gate_template()
        self.out = [self.workflow(w, gate_id) for w in wfs]


def _q(name):
    try:
        from urllib.parse import quote
    except ImportError:                                  # pragma: no cover
        from urllib import quote
    return quote(str(name))


def main():
    module = AnsibleModule(
        argument_spec=dict(workflows=dict(type="list", elements="raw", default=[]), checks=dict(type="dict", default={}),
                           gate=dict(type="dict", required=True), describe=dict(type="dict", default={}), project=dict(type="str", default=""),
                           inventory=dict(type="str", default=""), execution_environment=dict(type="str", default=""),
                           ca_path=dict(type="str", default=""), timeout=dict(type="int", default=30)),
        supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_gates_aap_ca_path", no_credential=NO_CREDENTIAL)
    g = Gates(api, p, module.check_mode)
    why = api.probe()
    if why:
        module.exit_json(changed=False, workflows=[], gate_template=g.gate, problems=[why], msg=why)
    try:
        g.run()
    except RuntimeError as e:
        g.problems.append(str(e))
    changed = not module.check_mode and (any(r["status"] == "gated now" for r in g.out) or g.gate["action"] == "made")
    module.exit_json(changed=changed, workflows=g.out, gate_template=g.gate, problems=g.problems, api=api.base)


if __name__ == "__main__":
    main()
