#!/usr/bin/env python3
"""Every filter name is given by one file in plugins/filter or a role's filter_plugins. Two files with the same
name: Ansible keeps the one it loads last, and which that is differs between machines (0.85.0: hzi_report in both the internal VMs and the golden
image filters broke the internal VMs report on one machine and not on another).
    python3 tests/test_filter_names.py"""
import glob
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
FILTERS = os.path.join(HERE, "..", "plugins", "filter")
sys.path.insert(0, FILTERS)


class FilterNames(unittest.TestCase):
    def test_each_filter_name_in_one_file(self):
        seen = {}
        paths = sorted(glob.glob(os.path.join(FILTERS, "*.py")) +
                       glob.glob(os.path.join(HERE, "..", "roles", "*", "filter_plugins", "*.py")))
        for i, path in enumerate(paths):
            fn = os.path.relpath(path, os.path.join(HERE, ".."))
            if os.path.basename(fn).startswith("_"):
                continue
            spec = importlib.util.spec_from_file_location("_fn_%d" % i, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not hasattr(mod, "FilterModule"):
                continue
            for name in mod.FilterModule().filters():
                seen.setdefault(name, []).append(fn)
        twice = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(twice, {}, "filter names given by more than one file")
        self.assertGreater(len(seen), 50)


if __name__ == "__main__":
    unittest.main()
