"""1.83.0: check_stig_stigman_collection_routes as the checklist import's own routes (stig_collection_routes) - a host's own
collection first, then match (its names) and os (its STIG); a route that is not one stops the job, named."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugins", "filter"))
import stig_ckl_filters as F  # noqa: E402
from ansible.errors import AnsibleFilterError  # noqa: E402


class Routes(unittest.TestCase):
    ROUTES = [{"match": "*APP*", "collection": "App servers"}, {"os": "rhel", "collection": "Platforms"},
              {"os": "windows", "collection": "Platforms"}]

    def test_the_hosts_own_collection_first_then_the_routes_in_order(self):
        hosts = [{"name": "rhel02.example.mil", "asset": "", "collection": "Ops"}, {"name": "app01.example.mil", "asset": "", "collection": ""},
                 {"name": "web01.example.mil", "asset": "WEB-01", "collection": "Web"}]
        got = F.stig_collection_routes(hosts, self.ROUTES)
        self.assertEqual(got, [{"collection": "Ops", "inventory_hosts": ["rhel02.example.mil"]},
                               {"collection": "Web", "inventory_hosts": ["web01.example.mil"]},
                               {"collection": "App servers", "names": ["*APP*"]},
                               {"collection": "Platforms", "os": ["rhel"]},
                               {"collection": "Platforms", "os": ["windows"]}])

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
        self.assertEqual(got, [{"collection": "E", "names": ["*APP*", "*app*"], "os": ["windows"]}])

    def test_no_route_no_collection_nothing(self):
        self.assertEqual(F.stig_collection_routes([{"name": "h1", "asset": "", "collection": ""}], []), [])
        self.assertEqual(F.stig_collection_routes(None, None), [])

    def test_a_wrong_route_stops_the_job_named(self):
        for r, why in (({"match": "*APP*"}, "route 1: "), ({"os": "solaris", "collection": "X"}, "os is rhel or windows"),
                       ({"name": "x", "collection": "X"}, "name is not a route's key"), ({"match": "", "collection": "X"}, "match is empty"),
                       ("Platforms", "is not {match")):
            with self.subTest(route=r):
                with self.assertRaises(AnsibleFilterError) as e:
                    F.stig_collection_routes([], [r])
                self.assertIn(why, str(e.exception))


if __name__ == "__main__":
    unittest.main()
