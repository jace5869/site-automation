# -*- coding: utf-8 -*-
"""The role's own names for the site_exclude filters, so they work in a run without this repository's ansible.cfg (a
playbook of ours imported from a playbook elsewhere): the one file, plugins/filter/site_exclude_filters.py, loaded by
its path - never a second copy, and names of their own (tests/test_filter_names.py: one file per name)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import importlib.util
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "plugins", "filter", "site_exclude_filters.py")
_SPEC = importlib.util.spec_from_file_location("site_exclude_filters_by_path", _PATH)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


class FilterModule(object):
    def filters(self):
        return {"site_exclude_role_match": _MOD.site_exclude_match, "site_exclude_role_line": _MOD.site_exclude_line}
