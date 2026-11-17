"""1.67.0: '!' leaves a STIG Manager collection out of the scorecard and of the checklist import's search."""
import importlib.util
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


import ansible.module_utils         # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(REPO, "plugins", "module_utils"))

def load(path, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, path))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


SC = load("roles/stigman_ckl_import/library/site_stig_scorecard.py", "site_stig_scorecard")
CK = load("roles/stigman_ckl_import/library/site_ckl_import.py", "site_ckl_import")
COLS = [{"collectionId": i, "name": n} for i, n in [(10, "Base"), (34, "Staging"), (48, "Big"), (50, "Linux"), (51, "Windows"),
                                                   (31, "Archived STIGs"), (60, "[PROD] Servers"), (61, "SITEB [DR]")]]


def names(cols):
    return [c["name"] for c in cols]


class Scorecard(unittest.TestCase):
    def test_all_but(self):
        out, missing = SC.wanted(COLS, ["*", "!48", "!archived*", "!34"])
        self.assertEqual(names(out), ["Base", "Linux", "Windows", "[PROD] Servers", "SITEB [DR]"])
        self.assertEqual(missing, [])

    def test_only_exclusions_means_all_but(self):
        self.assertEqual(names(SC.wanted(COLS, ["!48"])[0]), ["Base", "Staging", "Linux", "Windows", "Archived STIGs",
                                                             "[PROD] Servers", "SITEB [DR]"])

    def test_patterns_and_ids(self):
        self.assertEqual(names(SC.wanted(COLS, ["lin*", "51"])[0]), ["Linux", "Windows"])

    def test_a_typo_is_said(self):
        self.assertEqual(SC.wanted(COLS, ["*", "!99", "!Nope"])[1], ["!99", "!Nope"])

    def test_as_before(self):
        self.assertEqual(len(SC.wanted(COLS, [])[0]), 8)
        self.assertEqual(names(SC.wanted(COLS, ["Linux"])[0]), ["Linux"])


    def test_brackets_are_part_of_a_name(self):
        self.assertEqual(names(SC.wanted(COLS, ["[prod] servers"])[0]), ["[PROD] Servers"])
        self.assertEqual(names(SC.wanted(COLS, ["[PROD] Servers", "Linux"])[0]), ["[PROD] Servers", "Linux"])
        out = names(SC.wanted(COLS, ["*", "![PROD] Servers", "!SITEB [DR]"])[0])
        self.assertNotIn("[PROD] Servers", out)
        self.assertNotIn("SITEB [DR]", out)
        self.assertEqual(names(SC.wanted(COLS, ["SITEB*"])[0]), ["SITEB [DR]"])

    def test_a_pattern_that_matches_nothing_is_said(self):
        self.assertEqual(SC.wanted(COLS, ["Nope*"])[1], ["Nope*"])


class Import(unittest.TestCase):
    def test_all_but_and_staging_last(self):
        st, search = CK.pick_collections(COLS, ["*", "!48", "!31"], "Staging")
        self.assertEqual(st["name"], "Staging")
        self.assertEqual(names(search), ["[PROD] Servers", "Base", "Linux", "SITEB [DR]", "Windows", "Staging"])

    def test_staging_is_always_searched(self):
        _st, search = CK.pick_collections(COLS, ["*", "!34"], "Staging")
        self.assertEqual(names(search)[-1], "Staging")

    def test_only_exclusions_means_all_but(self):
        _st, search = CK.pick_collections(COLS, ["!48"], "Staging")
        self.assertNotIn("Big", names(search))
        self.assertIn("Base", names(search))

    def test_empty_is_staging_only(self):
        self.assertEqual(names(CK.pick_collections(COLS, [], "Staging")[1]), ["Staging"])

    def test_brackets_are_part_of_a_name(self):
        _st, search = CK.pick_collections(COLS, ["*", "![PROD] Servers"], "Staging")
        self.assertNotIn("[PROD] Servers", names(search))
        _st, search = CK.pick_collections(COLS, ["SITEB [DR]"], "Staging")
        self.assertEqual(names(search), ["SITEB [DR]", "Staging"])

    def test_leaving_out_an_unknown_collection_is_said_not_a_stop(self):
        del CK.PICK_NOTES[:]
        _st, search = CK.pick_collections(COLS, ["*", "!Nope", "!75"], "Staging")
        self.assertIn("Base", names(search))
        self.assertEqual(len(CK.PICK_NOTES), 2)
        self.assertIn("!75", CK.PICK_NOTES[1])

    def test_an_unknown_collection_to_search_still_stops(self):
        with self.assertRaises(CK.Fail):
            CK.pick_collections(COLS, ["Nope"], "Staging")


if __name__ == "__main__":
    unittest.main()
