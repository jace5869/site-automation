#!/usr/bin/python
# -*- coding: utf-8 -*-
"""NetApp - expand a volume: another run of this job template still going? (AAP API, read only; Codex 1.128.0.) Two runs at
once could both find room on one aggregate and both grow into it; the template is made with Concurrent jobs off
(allow_simultaneous: false, aap/job_templates.yml), and this checks it right before the change - with a "Red Hat Ansible
Automation Platform" credential (CONTROLLER_HOST, ...). Without one: checked=false, said; the template's setting is the
guard. Every template of ontap_expand_templates is asked (two templates are two AAP queues; a workflow there: its workflow
jobs), and this run's own. A run counts while running, or pending/waiting with a lower job id
(site_ontap_expand.expand_busy, others_going); the workflow job this run is a node of never counts.
  template_id, job_id, workflow_job_id (this run: awx_job_template_id, awx_job_id, awx_workflow_job_id), templates
  (ontap_expand_templates) -> {checked, busy: [job ids], why}"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection
from ansible.module_utils import site_ontap_expand as X


def main():
    m = AnsibleModule(argument_spec=dict(template_id=dict(type="int", default=0), job_id=dict(type="int", default=0),
                                         workflow_job_id=dict(type="int", default=0), templates=dict(type="raw", default=[]),
                                         ca_path=dict(type="str", default=""), timeout=dict(type="int", default=20)),
                      supports_check_mode=True)
    p = m.params
    conn = connection()
    if not p["template_id"] or not conn["host"] or not conn["login"]:
        m.exit_json(changed=False, checked=False, busy=[], why="no AAP credential (or not run from AAP): Concurrent jobs off on the "
                                                                "template is the guard against two runs at once")
    api = Api(conn, p["ca_path"], p["timeout"])
    why = api.probe()
    if why:
        m.exit_json(changed=False, checked=False, busy=[], why="AAP could not be asked: %s" % why)
    busy, why = X.expand_busy(lambda path: api.call("GET", path), p["templates"], p["template_id"], p["job_id"], p["workflow_job_id"])
    if why:
        m.exit_json(changed=False, checked=False, busy=[], why=why)
    m.exit_json(changed=False, checked=True, busy=busy, why="")


if __name__ == "__main__":
    main()
