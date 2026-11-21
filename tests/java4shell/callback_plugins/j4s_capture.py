# tests/test_java4shell.py: each task's result (by its name) as one JSON document at the end - ansible-core only (the json callback
# is ansible.posix's). Test only.
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: j4s_capture
type: stdout
short_description: each task's result as JSON (tests/test_java4shell.py)
description: Test only.
"""


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "stdout"
    CALLBACK_NAME = "j4s_capture"

    def __init__(self):
        super(CallbackModule, self).__init__()
        self.tasks = {}

    def _keep(self, result, state):
        r = dict(result._result)
        r["_state"] = state
        self.tasks[result._task.get_name()] = r

    def v2_runner_on_ok(self, result):
        self._keep(result, "ok")

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self._keep(result, "failed")

    def v2_runner_on_skipped(self, result):
        self._keep(result, "skipped")

    def v2_playbook_on_stats(self, stats):
        h = stats.summarize("localhost")
        self._display.display(json.dumps({"tasks": self.tasks, "stats": h}, default=str))
