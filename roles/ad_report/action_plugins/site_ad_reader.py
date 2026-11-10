# -*- coding: utf-8 -*-
"""The module site_ad_reader (roles/ad_report/library) with the job's own credential: AAP passes a Machine credential
as -u and --ask-pass, which reach only the connection (the play context), never a variable. The reader binds to AD
with them when user and password are not given (ad_reader_user, ad_reader_password)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    _supports_check_mode = True

    def run(self, tmp=None, task_vars=None):
        result = super(ActionModule, self).run(tmp, task_vars)
        args = dict(self._task.args)
        if not args.get("user"):
            args["user"] = self._play_context.remote_user or ""
            if not args.get("password"):
                args["password"] = self._play_context.password or ""
        result.update(self._execute_module(module_name="site_ad_reader", module_args=args, task_vars=task_vars))
        return result
