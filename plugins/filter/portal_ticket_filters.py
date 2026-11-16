# -*- coding: utf-8 -*-
"""The site portal's "Open ticket" button follows the setting portal_ticket_mode (1.67.0; roles/portal_ticket).

An action of actions.yml with `modes: {dry_run: <label>, live: <label>, off: null}` is offered under the label of the
mode set, and not at all in a mode whose label is null (or not listed). The report jobs pass portal_ticket_mode through
this filter once, where they load the actions; the portal itself (the confirm page) has no modes - the job refuses to
run when tickets are off.
"""
from __future__ import absolute_import, division, print_function
__metaclass__ = type


def portal_action_modes(actions, mode="dry_run"):
    """actions (name -> action) with each action that has `modes` reduced to its button under this mode: an action whose
    mode has no label is left out; the others get button = that label. An action without `modes` is as it was."""
    mode = "off" if mode is False else str(mode or "dry_run")      # a bare off in YAML is false
    out = {}
    for name, a in (actions or {}).items():
        if isinstance(a, dict) and isinstance(a.get("modes"), dict):
            label = a["modes"].get(mode)
            if not label:
                continue
            a = dict(a, button=str(label))
        out[name] = a
    return out


class FilterModule(object):
    def filters(self):
        return {"portal_action_modes": portal_action_modes}
