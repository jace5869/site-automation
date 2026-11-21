"""head_of.head(doc): the head a report page and its email read from the report (plugins/module_utils/report_head.py), for the tests of
the reports that set their own counts."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "module_utils"))
import report_head  # noqa: E402


def head(doc):
    return report_head.report_head(doc)
