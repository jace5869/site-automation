#!/usr/bin/env python3
"""1.123.1, the site portal's Run dialog: a survey choice that ends with a job's own key ([vol|...]) shows without it, the
value sent is the whole choice, and a long list gets the wide select. Standard library only: python3 tests/test_portal_choice_label.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "plugins", "filter"))
sys.path.insert(0, os.path.join(ROOT, "plugins", "module_utils"))
sys.path.insert(0, os.path.join(ROOT, "roles", "site_portal_stack", "files"))
import site_portal as P  # noqa: E402

KEYED = ("vol_ds03/lun_a (svm_vmware) - 2.00 TB, 21% free [LUN lun_a of 2: datastore DS-SITEA-03A] "
         "[vol|cluster1.example.mil|svm_vmware|vol_ds03|/vol/vol_ds03/lun_a|2199023255552|966367641600|v-ds03|l-a]")


class ChoiceLabel(unittest.TestCase):
    def test_key_left_off_only_at_the_end(self):
        self.assertEqual(P.choice_label(KEYED), "vol_ds03/lun_a (svm_vmware) - 2.00 TB, 21% free [LUN lun_a of 2: datastore DS-SITEA-03A]")
        self.assertEqual(P.choice_label("lines"), "lines")
        self.assertEqual(P.choice_label("[vol|x]"), "[vol|x]")                       # nothing left: shown whole
        self.assertEqual(P.choice_label("a [note] b"), "a [note] b")

    def test_css_widens_the_dialog(self):
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "files", "static", "portal.css")) as fh:
            css = fh.read()
        self.assertIn("dialog.dlg:has(select.wide)", css)
        with open(os.path.join(ROOT, "roles", "site_portal_stack", "files", "site_portal.py")) as fh:
            src = fh.read()
        self.assertIn('esc(choice_label(c))', src)
        self.assertIn('<option value="%s"%s>%s</option>\' % (esc(c)', src)              # the value sent is the whole choice


if __name__ == "__main__":
    unittest.main(verbosity=1)
