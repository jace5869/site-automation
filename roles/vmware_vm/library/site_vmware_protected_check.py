#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The protected names checked against vCenter (1.42.0): every VM's name read once per vCenter (one property-collector
call each, no call per VM), and each protected or excluded name - or pattern - that matches none of them said. A typo
in vmware_protected_vms protects nothing: this says so. Reads only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_protected_check
short_description: Which protected VM names match no VM in vCenter (read-only)
description:
  - Reads the name of every VM (templates too) of each vCenter - the credential's and C(vcenters) - in one
    property-collector call per vCenter.
  - For each name of C(lists) - a VM name, or a pattern with * and ? - that matches none of them (in any case; a name
    matches itself before it is read as a pattern), one entry in C(unmatched).
  - Reads only. A vCenter that could not be read is in C(vcenters) with its error; when none could, C(error) says so
    and nothing is unmatched (nothing could be checked).
options:
  lists:
    description: "[{setting (its name, for the line), names (as name_list read them)}]."
    type: list
    elements: dict
    required: true
  vcenters: {description: More vCenters to read besides the credential's (same account; name or name:port)., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
author: site automation
'''

RETURN = r'''
unmatched:
  description: "Each name that matches no VM: {setting, name}."
  type: list
  returned: always
vms:
  description: How many VM names were read.
  type: int
  returned: always
vcenters:
  description: "Each vCenter: {name, ok, error, note}."
  type: list
  returned: always
error:
  description: "Why nothing could be checked ('' = checked)."
  type: str
  returned: always
'''

import fnmatch

from ansible.module_utils.basic import AnsibleModule

try:
    from ansible.module_utils.site_vcenters import each_vcenter, vm_properties
    HAS = True
except ImportError:
    HAS = False


def unmatched(lists, names):
    """[{setting, names}] and every VM name read -> [{setting, name}] of the names and patterns that match none."""
    have = set(str(n).lower() for n in names if n)
    out, seen = [], set()
    for ls in lists or []:
        for p in ls.get("names") or []:
            pat = str(p).strip().lower()
            if not pat or (ls.get("setting"), pat) in seen:
                continue
            seen.add((ls.get("setting"), pat))
            if pat in have or any(fnmatch.fnmatchcase(n, pat) for n in have):
                continue
            # "VCSA01;AAP01" is read as both names and, whole, as one (a VM's name may have a ";"): not said when its parts are
            # in the list - they are checked themselves
            names = set(str(x).strip().lower() for x in ls.get("names") or [])
            if ";" in pat and all(x.strip() in names for x in pat.split(";") if x.strip()):
                continue
            out.append({"setting": ls.get("setting") or "", "name": str(p).strip()})
    return out


def main():
    module = AnsibleModule(argument_spec=dict(
        lists=dict(type="list", elements="dict", required=True),
        vcenters=dict(type="list", elements="str", default=[]),
        validate_certs=dict(type="bool", default=True),
    ), supports_check_mode=True)
    p = module.params
    if not any(ls.get("names") for ls in p["lists"]):
        module.exit_json(changed=False, unmatched=[], vms=0, vcenters=[], error="")
    if not HAS:
        module.exit_json(changed=False, unmatched=[], vms=0, vcenters=[], error="this execution environment has no pyVmomi")

    def read(si, content, name):
        return [str(pr.get("name") or "") for _vm, pr in vm_properties(content, ["name"])]
    # not one vCenter read: each_vcenter fails the module with why - the task takes that as "could not check" (never
    # a failure of the job)
    done, statuses = each_vcenter(module, read, p["vcenters"], p["validate_certs"], what="the VMs' names")
    names = [n for _name, got in done for n in got]
    if not any(s.get("ok") for s in statuses):
        module.exit_json(changed=False, unmatched=[], vms=0, vcenters=statuses,
                         error="; ".join(s.get("error") or "" for s in statuses if s.get("error")) or "no vCenter read")
    module.exit_json(changed=False, unmatched=unmatched(p["lists"], names), vms=len(names), vcenters=statuses, error="")


if __name__ == "__main__":
    main()
