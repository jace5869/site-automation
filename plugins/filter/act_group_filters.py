# -*- coding: utf-8 -*-
"""Hosts that found the same things, asked once (1.136.0): ACT reads the first host of each group; the others take its
answer (roles/site_act/tasks/run.yml at level explain, roles/fapolicyd/tasks/act.yml). The signatures are
plugins/module_utils/site_signature.py's, loaded by path."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import hashlib
import importlib.util
import json
import os

_spec = importlib.util.spec_from_file_location(
    "site_signature_for_groups", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_signature.py"))
_sig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sig)


def site_finding_set(findings, names=None):
    """A host's findings as one signature ('' for none): site_signature.finding_set."""
    return _sig.finding_set(findings, names)


def site_text_set(lines, names=None):
    """Lines of text (a job's own reading, as fapolicyd's denials) as one signature, their order and repeats left out
    ('' for none)."""
    sigs = sorted(set(_sig.signature(x, names) for x in (lines or []) if str(x or "").strip()))
    return hashlib.sha256(json.dumps(sigs).encode("utf-8")).hexdigest() if sigs else ""


def site_act_group(hostvars, hosts, me, var="_site_act_sig"):
    """{lead, same}: the hosts of this play with the same signature (`var` in each host's facts) as `me`, in the play's
    order - lead is the first of them, the one ACT reads. A host with no signature is its own group."""
    def sig(h):
        try:
            return str((hostvars[h] or {}).get(var) or "")
        except (KeyError, TypeError, AttributeError):
            return ""
    mine = sig(me)
    if not mine:
        return {"lead": me, "same": [me]}
    same = [str(h) for h in (hosts or []) if sig(h) == mine]
    if me not in same:
        same.append(me)
    return {"lead": same[0], "same": same}


def site_act_same_note(same, lead):
    """The line that tells a host's report it shares ACT's answer: '' for a host alone."""
    same = list(same or [])
    if len(same) < 2:
        return ""
    return "The same findings on %d hosts: %s (ACT read %s; the others take its answer)." % (len(same), ", ".join(same), lead)


class FilterModule(object):
    def filters(self):
        return {"site_finding_set": site_finding_set, "site_text_set": site_text_set, "site_act_group": site_act_group,
                "site_act_same_note": site_act_same_note}
