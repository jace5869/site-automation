"""1.83.0: check_stig_stigman_collection_routes as the checklist import's own routes (stig_collection_routes) - a host's own
collection first, then match (its names) and os (its STIG); a route that is not one stops the job, named.
1.103.0: stigs (the STIG it was scanned with), roles and tech_areas too; one collection per asset - the first route that
fits any of a host's STIGs takes the whole host; the plan names the route as the site wrote it."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "filter"))
import stig_ckl_filters as F  # noqa: E402
from ansible.errors import AnsibleFilterError  # noqa: E402
import ansible.module_utils  # noqa: E402 - the repository's module_utils as Ansible gives them (ansible.cfg)
ansible.module_utils.__path__.append(os.path.join(os.path.dirname(HERE), "plugins", "module_utils"))


class Routes(unittest.TestCase):
    ROUTES = [{"match": "*APP*", "collection": "App servers"}, {"os": "rhel", "collection": "Platforms"},
              {"os": "windows", "collection": "Platforms"}]

    def test_the_hosts_own_collection_first_then_the_routes_in_order(self):
        hosts = [{"name": "rhel02.example.mil", "asset": "", "collection": "Ops"}, {"name": "app01.example.mil", "asset": "", "collection": ""},
                 {"name": "web01.example.mil", "asset": "WEB-01", "collection": "Web"}]
        got = F.stig_collection_routes(hosts, self.ROUTES)
        own = "check_stig_stigman_collection"
        self.assertEqual(got, [{"collection": "Ops", "inventory_hosts": ["rhel02.example.mil"], "title": own},
                               {"collection": "Web", "inventory_hosts": ["web01.example.mil"], "title": own},
                               {"collection": "App servers", "names": ["*APP*"], "title": "route 1"},
                               {"collection": "Platforms", "os": ["rhel"], "title": "route 2"},
                               {"collection": "Platforms", "os": ["windows"], "title": "route 3"}])

    def test_a_hosts_own_collection_is_that_host_only(self):
        # 1.83.0 (Codex P1, final pass): db01's own collection (West) never takes db01.east by its short name
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "roles", "stigman_ckl_import", "library"))
        import site_ckl_import as ci
        rules = F.stig_collection_routes([{"name": "db01", "asset": "db-west", "collection": "West"},
                                          {"name": "db01.east.test", "asset": "db-east", "collection": ""}],
                                         [{"match": "db*", "collection": "Platforms"}])
        cols = [{"collectionId": "1", "name": "West"}, {"collectionId": "2", "name": "Platforms"}]
        routes = ci.read_routes(rules, cols)
        east = {"host": "db-east", "fqdn": "db01.east.test", "alias": "db01.east.test", "os": "rhel"}
        west = {"host": "db-west", "fqdn": "", "alias": "db01", "os": "rhel"}
        self.assertEqual(ci.route_of(routes, "db-east", [("RHEL_9_STIG", "RHEL 9", east)])[0]["want"], "Platforms")
        self.assertEqual(ci.route_of(routes, "db-west", [("RHEL_9_STIG", "RHEL 9", west)])[0]["want"], "West")
        self.assertEqual(ci.route_of(routes, "x", [("RHEL_9_STIG", "RHEL 9", dict(west, alias="DB01"))])[0]["want"], "West")   # any case
        self.assertEqual(ci.route_of(routes, "x", [("RHEL_9_STIG", "RHEL 9", dict(west, alias="db01.west.test"))])[0]["want"], "Platforms")

    def test_both_keys_both_fit_and_a_list_of_names(self):
        got = F.stig_collection_routes([], [{"match": ["*APP*", "*app*"], "os": "Windows", "collection": "E"}])
        self.assertEqual(got, [{"collection": "E", "names": ["*APP*", "*app*"], "os": ["windows"], "title": "route 1"}])

    def test_no_route_no_collection_nothing(self):
        self.assertEqual(F.stig_collection_routes([{"name": "h1", "asset": "", "collection": ""}], []), [])
        self.assertEqual(F.stig_collection_routes(None, None), [])

    def test_a_wrong_route_stops_the_job_named(self):
        for r, why in (({"match": "*APP*"}, "route 1: "), ({"os": "solaris", "collection": "X"}, "os is rhel or windows"),
                       ({"name": "x", "collection": "X"}, "name is not a route's key"), ({"match": "", "collection": "X"}, "match is empty"),
                       ("Platforms", "is not {collection"),
                       # 1.103.0
                       ({"stigs": 7, "collection": "X"}, "route 1: stigs is a list of names or patterns (or text with commas), not 7"),
                       ({"stigs": [], "collection": "X"}, "route 1: stigs is empty"),
                       ({"roles": " , ", "collection": "X"}, "route 1: roles is empty"),
                       ({"tech_areas": ["ok", 3], "collection": "X"}, "route 1: tech_areas is a list"),
                       ({"stig": "*AD*", "collection": "X"}, "stig is not a route's key (match, stigs, roles, tech_areas, os, collection)")):
            with self.subTest(route=r):
                with self.assertRaises(AnsibleFilterError) as e:
                    F.stig_collection_routes([], [r])
                self.assertIn(why, str(e.exception))



class ByStig(unittest.TestCase):
    """1.103.0: a new asset placed by the STIG it was scanned with - one collection per asset, the order decides."""
    DC = [("Windows_Server_2022_MS_STIG", "Windows Server 2022 Member Server STIG"),
          ("Active_Directory_Domain", "Active Directory Domain Security Technical Implementation Guide"),
          ("Active_Directory_Forest", "Active Directory Forest Security Technical Implementation Guide")]
    COLS = [{"collectionId": "1", "name": "Directory servers"}, {"collectionId": "2", "name": "Networks"},
            {"collectionId": "3", "name": "Platforms"}]

    def route(self, rules, stigs=None, target=None):
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "roles", "stigman_ckl_import", "library"))
        import site_ckl_import as ci
        t = target or {"host": "dc01", "fqdn": "dc01.example.mil", "alias": "dc01.example.mil", "os": "windows",
                       "role": "Domain Controller", "tech_area": "Windows OS"}
        routes = ci.read_routes(F.stig_collection_routes([], rules), self.COLS)
        first, also = ci.route_of(routes, t["host"], [(b, title, t) for b, title in (stigs or self.DC)])
        return ci, first, also

    def test_a_dc_with_three_stigs_goes_whole_by_the_ad_rule_placed_first(self):
        ci, first, also = self.route([{"stigs": "*Active_Directory_Domain*", "collection": "Directory servers"},
                                      {"os": "windows", "collection": "Platforms"}])
        self.assertEqual(first["want"], "Directory servers")
        self.assertEqual(ci.by_route(first), "route 1 (stigs *Active_Directory_Domain*)")
        self.assertEqual([(r["want"], b) for r, b in also], [("Platforms", "Windows_Server_2022_MS_STIG")])   # said, never split

    def test_the_same_rules_in_the_other_order_go_to_platforms(self):
        _ci, first, _also = self.route([{"os": "windows", "collection": "Platforms"},
                                        {"stigs": "*Active_Directory_Domain*", "collection": "Directory servers"}])
        self.assertEqual((first["want"], first["title"]), ("Platforms", "route 1"))

    def test_a_stigs_rule_that_fits_nothing_falls_to_the_next(self):
        ci, first, _also = self.route([{"stigs": ["*Cisco*", "*Palo_Alto*"], "collection": "Networks"},
                                       {"stigs": "*active directory forest*", "collection": "Directory servers"},
                                       {"os": "windows", "collection": "Platforms"}])
        self.assertEqual(first["want"], "Directory servers")               # by the title, any case
        self.assertEqual(ci.by_route(first), "route 2 (stigs *active directory forest*)")

    def test_a_network_device_by_its_stig(self):
        _ci, first, _also = self.route([{"stigs": "*Cisco*, *Palo_Alto*", "collection": "Networks"}, {"os": "rhel", "collection": "Platforms"}],
                                       [("Cisco_IOS-XE_Router_NDM_STIG", "Cisco IOS XE Router NDM STIG")],
                                       {"host": "rtr01", "fqdn": "", "alias": "rtr01", "os": ""})
        self.assertEqual(first["want"], "Networks")

    def test_roles_and_tech_areas_and_each_key_must_fit(self):
        _ci, first, _also = self.route([{"roles": "domain controller", "stigs": "*Forest*", "collection": "Directory servers"},
                                        {"tech_areas": "Windows OS", "collection": "Platforms"}])
        self.assertEqual(first["want"], "Directory servers")
        _ci, first, _also = self.route([{"roles": "Member Server", "stigs": "*Forest*", "collection": "Directory servers"},
                                        {"tech_areas": "Windows OS", "collection": "Platforms"}])
        self.assertEqual(first["want"], "Platforms")                         # the role does not fit: the next

    def test_by_stigs_and_or_naming_convention(self):
        # the user: "by stigs and/or naming convention" - match and stigs in one route both fit (AND), or either alone
        rules = [{"match": "*DC*", "stigs": ["*Active_Directory*"], "collection": "Directory servers"},
                 {"stigs": ["*Cisco*"], "collection": "Networks"}, {"match": "SITA*", "collection": "Platforms"}]
        ws = ("Windows_Server_2022_MS_STIG", "Windows Server 2022 Member Server STIG")
        cases = [("SITADC01", self.DC, "Directory servers", "route 1 (stigs *Active_Directory*; names *DC*)"),   # both fit
                 ("SITBDC01", self.DC, "Directory servers", None),
                 ("SITADC02", [ws], "Platforms", "route 3 (names SITA*)"),       # a DC name, no AD STIG: not route 1
                 ("SITAAPP01", self.DC, "Platforms", None),                      # AD STIGs, no DC in its name: not route 1
                 ("SITARTR01", [("Cisco_IOS-XE_Router_NDM_STIG", "Cisco IOS XE Router NDM STIG")], "Networks", "route 2 (stigs *Cisco*)"),
                 ("app01", self.DC, None, None)]                                 # no route fits: no collection
        for name, stigs, want, text in cases:
            with self.subTest(host=name):
                t = {"host": name, "fqdn": "", "alias": name, "os": "windows"}
                ci, first, _also = self.route(rules, stigs, t)
                self.assertEqual(first["want"] if first else None, want)
                if text:
                    self.assertEqual(ci.by_route(first), text)

    def test_any_combination_of_keys(self):
        keys = {"match": "SITA*", "os": "windows", "stigs": "*AD*", "roles": "Domain Controller", "tech_areas": "Windows OS"}
        import itertools
        for k in range(1, len(keys) + 1):
            for combo in itertools.combinations(sorted(keys), k):
                with self.subTest(keys=combo):
                    got = F.stig_collection_routes([], [dict(((c, keys[c]) for c in combo), collection="X")])
                    self.assertEqual(len(got), 1)
        self.assertEqual(F.stig_collection_routes([], [{"collection": "X"}]), [{"collection": "X", "title": "route 1"}])   # every host

    def test_a_hosts_own_collection_still_wins(self):
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "roles", "stigman_ckl_import", "library"))
        import site_ckl_import as ci
        rules = F.stig_collection_routes([{"name": "dc01.example.mil", "asset": "", "collection": "Platforms"}],
                                         [{"stigs": "*Active_Directory_Domain*", "collection": "Directory servers"}])
        routes = ci.read_routes(rules, self.COLS)
        t = {"host": "dc01", "fqdn": "dc01.example.mil", "alias": "dc01.example.mil", "os": "windows"}
        first, _also = ci.route_of(routes, "dc01", [(b, title, t) for b, title in self.DC])
        self.assertEqual((first["want"], first["title"]), ("Platforms", "check_stig_stigman_collection"))
        self.assertEqual(routes[1]["title"], "route 1")                      # the site's numbering, not the import's

    def test_the_imports_own_rules_keep_their_numbers_and_a_bad_title_is_refused(self):
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "roles", "stigman_ckl_import", "library"))
        import site_ckl_import as ci
        routes = ci.read_routes([{"collection": "Networks", "stigs": "*Cisco*"}], self.COLS)
        self.assertEqual(ci.by_route(routes[0]), "route 1 (stigs *Cisco*)")
        with self.assertRaises(ci.Fail) as e:
            ci.read_routes([{"collection": "Networks", "title": 3}], self.COLS)
        self.assertIn("rule 1: title is the rule's name in the plan (text), not 3", str(e.exception))


if __name__ == "__main__":
    unittest.main()
