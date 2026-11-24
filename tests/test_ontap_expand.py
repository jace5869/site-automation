#!/usr/bin/env python3
"""1.123.0: NetApp - expand a volume - the arithmetic and the rules (plugins/module_utils/site_ontap_expand.py): the default
growth (10 %, at least 1 GB, at most 512 GB), the aggregate's limit (97 %) and the largest size that fits, the Size box,
the exclusions, the NetApp NAA id of a LUN, the survey choice and its key, the volume comment, the re-check.
    python3 tests/test_ontap_expand.py"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path[:0] = [os.path.join(REPO, "plugins", "module_utils")]
import site_ontap_expand as X  # noqa: E402

GB, TB, MB = X.GB, X.TB, X.MB
AGGR = {"name": "aggr1", "size": 20 * TB, "used": int(14.24 * TB)}


def vol(size=10 * GB, used=int(8.8 * GB), **kw):
    v = {"name": "vol_ds01", "svm": "svm_vmware", "size": size, "used": used, "state": "online", "type": "rw", "style": "flexvol"}
    v.update(kw)
    return v


class Sizes(unittest.TestCase):
    def test_default_growth(self):
        self.assertEqual(X.default_growth(10 * GB), 1 * GB)                 # 10 % of 10 GB = 1 GB
        self.assertEqual(X.default_growth(5 * GB), 1 * GB)                  # 0.5 GB -> at least 1 GB
        self.assertEqual(X.default_growth(2 * TB), int(204.8 * GB) // MB * MB + MB)   # 204.8 GB, whole MB up
        self.assertEqual(X.default_growth(8 * TB), 512 * GB)                # 819 GB -> at most 512 GB
        self.assertEqual(X.default_growth(100 * GB, 20, 1, 512), 20 * GB)

    def test_size_box(self):
        self.assertEqual(X.parse_size("150GB"), 150 * GB)
        self.assertEqual(X.parse_size(" 150 gb "), 150 * GB)
        self.assertEqual(X.parse_size("1TB"), TB)
        self.assertEqual(X.parse_size("1.5 TiB"), int(1.5 * TB))
        self.assertEqual(X.parse_size("200"), 200 * GB)
        for bad in ("", "0", "0GB", "-5GB", "150MB", "lots", "1e3GB"):
            with self.assertRaises(ValueError, msg=bad):
                X.parse_size(bad)

    def test_room_and_human(self):
        self.assertEqual(X.room(100 * GB, 90 * GB, 97), 7 * GB)
        self.assertEqual(X.room(100 * GB, 98 * GB, 97), 0)
        self.assertEqual(X.human(10 * GB), "10.0 GB")
        self.assertEqual(X.human(2 * TB), "2.00 TB")


class Plans(unittest.TestCase):
    def test_lun_volume_default(self):
        """thin LUN: the default growth is the LUN's (10 % of 9 GB -> 1 GB at least); the volume keeps its headroom (10/9)"""
        p = X.plan(vol(), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/lun_ds01", "size": 9 * GB}, lun_listed=9 * GB)
        self.assertEqual(p["status"], "grow", p["why"])
        self.assertEqual((p["growth"], p["lun"]["target"]), (GB, 10 * GB))
        self.assertEqual(p["target"], X._ceil_mb(10 * GB * 10 / 9.0))
        self.assertIn("thin LUN, the volume keeps 1.11x its LUN", p["vol_how"])
        self.assertEqual(p["by"], "default")

    def test_default_over_the_limit_says_what_fits(self):
        aggr2 = {"name": "aggr2", "size": 30 * TB, "used": int(30 * TB * 0.97) - 180 * GB}
        p = X.plan(vol(2 * TB, int(1.82 * TB), name="vol_sql01", svm="svm1"), aggr2, 2 * TB, {})
        self.assertEqual(p["status"], "refused")
        self.assertIn("Default 204.8 GB would put aggr2 at 97.", p["why"])
        self.assertIn("up to 180.0 GB fits", p["why"])
        self.assertEqual(p["room"], 180 * GB)
        ok = X.plan(vol(2 * TB, int(1.82 * TB), name="vol_sql01", svm="svm1"), aggr2, 2 * TB, {}, size_text="180GB")
        self.assertEqual(ok["status"], "grow", ok["why"])                  # exactly the largest that fits
        big = X.plan(vol(2 * TB, int(1.82 * TB), name="vol_sql01", svm="svm1"), aggr2, 2 * TB, {}, size_text="181GB")
        self.assertTrue(big["why"].startswith("181.0 GB would put aggr2 at"), big["why"])

    def test_the_limit_is_a_setting(self):
        self.assertEqual(X.plan(vol(), {"name": "aggr1", "size": 100 * GB, "used": 96 * GB}, 10 * GB, {})["status"], "grow")   # 97 % exactly: at the limit
        aggr = {"name": "aggr1", "size": 100 * GB, "used": int(96.5 * GB)}
        self.assertEqual(X.plan(vol(), aggr, 10 * GB, {})["status"], "refused")                   # 97.5 % after 1 GB: over 97
        self.assertEqual(X.plan(vol(), aggr, 10 * GB, {"max_pct": 99})["status"], "grow")

    def test_nothing_fits(self):
        p = X.plan(vol(), {"name": "aggr1", "size": 100 * GB, "used": 98 * GB}, 10 * GB, {})
        self.assertIn("nothing fits", p["why"])

    def test_size_box_beats_max_but_not_the_aggregate(self):
        p = X.plan(vol(2 * TB), AGGR, 2 * TB, {}, size_text="1TB")        # over ontap_expand_max_gb: the box wins
        self.assertEqual((p["status"], p["growth"], p["by"]), ("grow", TB, "size"))

    def test_bad_size_box(self):
        p = X.plan(vol(), AGGR, 10 * GB, {}, size_text="ten gigs")
        self.assertEqual(p["status"], "refused")
        self.assertIn("not understood", p["why"])

    def test_again_is_nothing_to_do(self):
        p = X.plan(vol(11 * GB), AGGR, 10 * GB, {})                         # the second run of the same choice
        self.assertEqual(p["status"], "nothing")
        self.assertIn("nothing to do", p["why"])
        self.assertEqual(p["growth"], 0)

    def test_changed_since_the_list(self):
        p = X.plan(vol(12 * GB), AGGR, 10 * GB, {}, size_text="5GB")
        self.assertEqual(p["status"], "refused")
        self.assertIn("changed since the list", p["why"])
        p = X.plan(vol(9 * GB), AGGR, 10 * GB, {})                          # smaller now: never grown to a stale target
        self.assertIn("changed since the list", p["why"])

    def test_lun_never_bigger_than_its_volume(self):
        """a thin LUN bigger than its volume (over-provisioned): the volume still grows to at least the LUN's new size"""
        p = X.plan(vol(), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 20 * GB}, lun_listed=20 * GB)
        self.assertEqual(p["status"], "grow", p["why"])
        self.assertGreaterEqual(p["target"], p["lun"]["target"])

    def test_recheck(self):
        p = X.plan(vol(), AGGR, 10 * GB, {})
        self.assertEqual(X.recheck(p, vol(), AGGR), "")
        self.assertIn("changed between the plan", X.recheck(p, vol(10 * GB + 2 * MB), AGGR))
        full = dict(AGGR, used=int(20 * TB * 0.97) - 512 * MB)
        self.assertIn("got fuller", X.recheck(p, vol(), full))


class Review1123(unittest.TestCase):
    """Codex's review: the aggregate's space not read, the live size within the list's rounding, a LUN left behind, a LUN
    changed since the list, a volume moved or excluded between the plan and the change, a size to type that fits."""

    def test_aggregate_space_not_read(self):
        p = X.plan(vol(), {"name": "aggr1", "size": 20 * TB, "used": None}, 10 * GB, {})
        self.assertEqual(p["status"], "refused")
        self.assertIn("could not be read", p["why"])

    def test_the_change_is_what_was_checked(self):
        p = X.plan(vol(10 * GB - MB // 2), AGGR, 10 * GB, {}, size_text="1GB")
        self.assertEqual(p["target"] - p["size"], GB)                       # never more than the aggregate was checked for

    def test_a_lun_left_behind_is_not_nothing_to_do(self):
        """a LUN volume is done when its LUN is: a volume grown by hand with the LUN left as it was is not 'nothing to do'"""
        lun = {"name": "/vol/vol_ds01/lun_ds01", "size": 9 * GB}
        p = X.plan(vol(11 * GB), AGGR, 10 * GB, {}, lun=lun, lun_listed=9 * GB)
        self.assertEqual(p["status"], "refused")
        self.assertIn("the volume changed since the list", p["why"])
        done = X.plan(vol(11 * GB), AGGR, 10 * GB, {}, lun=dict(lun, size=10 * GB), lun_listed=9 * GB)
        self.assertEqual(done["status"], "nothing")
        self.assertIn("LUN lun_ds01 already 10.0 GB", done["why"])

    def test_a_lun_changed_since_the_list(self):
        p = X.plan(vol(), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": int(9.5 * GB)}, lun_listed=9 * GB)
        self.assertIn("the LUN changed since the list", p["why"])

    def test_moved_or_excluded_before_the_change(self):
        p = X.plan(vol(), AGGR, 10 * GB, {})
        self.assertIn("moved to aggregate aggr9", X.recheck(p, dict(vol(), aggregates=["aggr9"]), AGGR))
        self.assertIn("now excluded", X.recheck(p, dict(vol(), name="old_root", aggregates=["aggr1"]), AGGR, ["*_root"]))
        self.assertIn("could not be read again", X.recheck(p, dict(vol(), aggregates=["aggr1"]), dict(AGGR, used=None)))
        self.assertEqual(X.recheck(p, dict(vol(), aggregates=["aggr1"]), AGGR), "")

    def test_pass2(self):
        """the second pass: a size below 0.1 GB, a size that could not be read, a datastore already over the whole LUN"""
        with self.assertRaises(ValueError):
            X.parse_size("0.0009765625GB")
        self.assertEqual(X.parse_size("0.1GB"), int(X.math.ceil(GB / 10 / MB) * MB))
        self.assertIn("could not be read", X.plan(vol(size=0), AGGR, 10 * GB, {})["why"])
        tight = {"name": "aggr1", "size": 100 * GB, "used": int(97 * GB) - 64 * MB}
        self.assertIn("nothing fits", X.plan(vol(), tight, 10 * GB, {})["why"])                      # 64 MB: nothing to type
        again = X.plan(vol(11 * GB), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 10 * GB}, lun_listed=9 * GB)
        self.assertEqual((again["status"], again["lun"]["target"]), ("nothing", 10 * GB))           # the datastore's step runs again
        self.assertTrue(again["retry_ok"])
        more = X.plan(vol(111 * GB), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 110 * GB}, lun_listed=9 * GB)
        self.assertEqual((more["status"], more["lun"], more["retry_ok"]), ("nothing", None, False))   # grown more by someone else: vCenter left alone
        vol_more = X.plan(vol(130 * GB), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 10 * GB}, lun_listed=9 * GB)
        self.assertTrue(vol_more["retry_ok"])                                      # a LUN volume is judged by its LUN: the LUN is as planned
        self.assertIn("did not have the size of LUN", X.plan(vol(), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 9 * GB})["why"])
        self.assertIn("could not be read", X.plan(vol(), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 0}, lun_listed=9 * GB)["why"])

    def test_a_size_to_type_fits(self):
        self.assertEqual(X.human(1023 * MB, floor=True), "0.9 GB")
        self.assertEqual(X.human(int(1.999 * TB), floor=True), "1.99 TB")


class LunPlans(unittest.TestCase):
    """A LUN volume is judged and grown by its LUN: the growth is the LUN's, the volume grows only as much as the bigger LUN
    needs - thick: its used space + the growth, thin: its headroom over its LUNs kept; the snapshot reserve on top; not at all
    when it has the room. The aggregate's limit is checked against the volume's growth."""
    LUN = {"name": "/vol/vol_ds01/lun0", "size": 2 * TB, "used": int(1.64 * TB), "thick": True, "total": 2 * TB}

    def v(self, size, available, snap=5):
        return vol(size, size - available, available=available, snap_pct=snap, guarantee="volume")

    def test_thick_with_snapshot_reserve(self):
        size = int(2.11 * TB)
        avail = int(size * 0.95) - 2 * TB                          # the thick LUN is all the usable space takes
        p = X.plan(self.v(size, avail), AGGR, size, {}, lun=self.LUN, lun_listed=2 * TB)
        self.assertEqual(p["status"], "grow", p["why"])
        g = X.default_growth(2 * TB)                               # 10 % of the LUN: 204.8 GB
        self.assertEqual((p["growth"], p["lun"]["target"]), (g, 2 * TB + g))
        self.assertEqual(p["target"], X._ceil_mb((2 * TB + g) / 0.95))
        self.assertEqual(p["vol_growth"], p["target"] - size)
        self.assertEqual(p["vol_how"], "thick LUN + 5 % snapshot reserve")
        self.assertIn("of the LUN's 2.00 TB", p["default_text"])

    def test_thin_keeps_the_headroom(self):
        lun = dict(self.LUN, thick=False)
        p = X.plan(self.v(int(2.5 * TB), int(0.4 * TB), snap=0), AGGR, int(2.5 * TB), {}, lun=lun, lun_listed=2 * TB)
        g = X.default_growth(2 * TB)
        self.assertEqual(p["target"], X._ceil_mb(2.5 * TB * (2 * TB + g) / (2 * TB)))
        self.assertTrue(p["vol_how"].startswith("thin LUN, the volume keeps 1.25x its LUN"), p["vol_how"])

    def test_enough_room_already(self):
        p = X.plan(self.v(3 * TB, int(0.85 * TB), snap=0), AGGR, 3 * TB, {}, lun=self.LUN, lun_listed=2 * TB)
        self.assertEqual(p["status"], "grow", p["why"])
        self.assertEqual((p["vol_growth"], p["target"]), (0, 3 * TB))
        self.assertTrue(p["vol_how"].startswith("it has room for the LUN's growth already"), p["vol_how"])
        self.assertEqual(p["aggr_pct_after"], p["aggr_pct"])       # nothing of the aggregate

    def test_aggregate_limit_is_against_the_volume_growth(self):
        size = int(2.11 * TB)
        avail = int(size * 0.95) - 2 * TB
        aggr = {"name": "aggr1", "size": 10 * TB, "used": int(9.7 * TB) - 100 * GB}       # 100 GB under 97 %
        p = X.plan(self.v(size, avail), aggr, size, {}, lun=self.LUN, lun_listed=2 * TB)
        self.assertEqual(p["status"], "refused")
        self.assertIn("on the LUN takes ", p["why"])
        self.assertIn(" from aggr1 (the volume +", p["why"])
        self.assertGreater(p["room"], 0)                                                   # the largest LUN growth that fits
        self.assertLessEqual(X.volume_for_lun(self.v(size, avail), self.LUN, p["room"])[0] - size, X.room(10 * TB, aggr["used"], 97))
        self.assertGreater(X.volume_for_lun(self.v(size, avail), self.LUN, p["room"] + X.MB)[0] - size, X.room(10 * TB, aggr["used"], 97))
        ok = X.plan(self.v(size, avail), aggr, size, {}, lun=self.LUN, lun_listed=2 * TB, size_text="%dGB" % (p["room"] // GB))
        self.assertEqual(ok["status"], "grow", ok["why"])
        room_vol = X.plan(self.v(3 * TB, int(0.85 * TB), snap=0), aggr, 3 * TB, {}, lun=self.LUN, lun_listed=2 * TB)
        self.assertEqual(room_vol["status"], "grow")                                      # no volume growth: the aggregate is not asked

    def test_recheck_uses_the_volume_growth(self):
        p = X.plan(self.v(3 * TB, int(0.85 * TB), snap=0), AGGR, 3 * TB, {}, lun=self.LUN, lun_listed=2 * TB)
        full = dict(AGGR, used=int(20 * TB * 0.97))
        self.assertEqual(X.recheck(p, self.v(3 * TB, int(0.85 * TB), snap=0), full), "")    # the LUN grows inside the volume

    def test_fullness(self):
        e = {"lun": "/vol/v/l", "lun_size": 100 * GB, "lun_used": 82 * GB, "size": 110 * GB, "used": 108 * GB}
        self.assertEqual(X.lun_fullness(e), (100 * GB, 82 * GB, "LUN"))
        self.assertEqual(X.fullness_text(e), "LUN 82% written")
        e2 = dict(e, ds_capacity=99 * GB, ds_free=int(19.8 * GB))
        self.assertEqual(X.fullness_text(e2), "datastore 80% used")
        self.assertEqual(X.fullness_text({"size": 100 * GB, "used": 50 * GB}), "volume 50% used")


class Review1124(unittest.TestCase):
    """Codex's review of the LUN rule: the aggregate counted by what really lands on it (over the limit already: refused,
    whatever the growth); the snapshots' spill counted once; the change re-read right before (autosize, a snapshot deleted)."""

    def v(self, size, available, snap=0, snap_used=0, guarantee="volume"):
        return vol(size, size - available, available=available, snap_pct=snap, snap_used=snap_used, guarantee=guarantee)

    LUN = {"name": "/vol/v/l", "size": 2 * TB, "used": int(1.5 * TB), "thick": True, "total": 2 * TB}

    def test_p1_an_aggregate_over_the_limit_refuses_even_with_room_in_the_volume(self):
        over = {"name": "aggr1", "size": 10 * TB, "used": int(9.8 * TB)}                       # 98 %
        p = X.plan(self.v(3 * TB, int(0.85 * TB)), over, 3 * TB, {}, lun=self.LUN, lun_listed=2 * TB)
        self.assertEqual(p["vol_growth"], 0)
        self.assertEqual(p["status"], "refused", p["why"])
        self.assertIn("nothing fits", p["why"])
        ok = X.plan(self.v(3 * TB, int(0.85 * TB)), AGGR, 3 * TB, {}, lun=self.LUN, lun_listed=2 * TB)
        self.assertEqual(ok["status"], "grow")
        self.assertIn("got fuller", X.recheck(ok, self.v(3 * TB, int(0.85 * TB)), over))     # the re-check too, with nothing to take
        nas = X.plan(vol(), {"name": "aggr1", "size": 100 * GB, "used": 98 * GB}, 10 * GB, {})
        self.assertEqual(nas["status"], "refused")

    def test_p1_a_thin_volume_counts_the_thick_luns_growth(self):
        aggr = {"name": "aggr1", "size": 10 * TB, "used": int(9.7 * TB) - 100 * GB}            # 100 GB under 97 %
        thin_vol = self.v(3 * TB, int(0.85 * TB), guarantee="none")
        p = X.plan(thin_vol, aggr, 3 * TB, {}, lun=self.LUN, lun_listed=2 * TB)               # no volume growth, 204.8 GB reserved
        self.assertEqual((p["vol_growth"], p["landing"]), (0, X.default_growth(2 * TB)))
        self.assertEqual(p["status"], "refused")
        thin_lun = X.plan(thin_vol, aggr, 3 * TB, {}, lun=dict(self.LUN, thick=False), lun_listed=2 * TB)
        self.assertEqual(thin_lun["landing"], 0)
        self.assertEqual(thin_lun["status"], "grow", thin_lun["why"])
        self.assertEqual(X.landing({"guarantee": ""}, self.LUN, 5, 7), 12)                    # not read: the most it can be

    def test_p2_the_spill_counted_once(self):
        """70 GiB data, 30 GiB snapshots, 20 % reserve, 100 GiB full: +10 GiB needs 110 GiB, not 112.5"""
        v = self.v(100 * GB, 0, snap=20, snap_used=30 * GB)
        self.assertEqual(X.usable_used(v)[0], 70 * GB)
        vt, _how = X.volume_for_lun(v, {"name": "l", "size": 60 * GB, "thick": True, "total": 60 * GB}, 10 * GB)
        self.assertEqual(vt, 110 * GB)
        self.assertEqual(X.size_for_usable(80 * GB, 20, 0), 100 * GB)                          # no snapshots: the reserve only
        self.assertEqual(X.size_for_usable(80 * GB, 0, 5 * GB), 85 * GB)                       # no reserve: all of them spill


def volume_module():
    """roles/ontap_report/library/site_ontap_volume.py with its module_utils (ansible-core's, the repo's)"""
    import importlib.util
    import ansible.module_utils
    import site_ontap
    for n, m in (("site_ontap", site_ontap), ("site_ontap_expand", X)):
        sys.modules["ansible.module_utils." + n] = m
        setattr(ansible.module_utils, n, m)
    spec = importlib.util.spec_from_file_location("site_ontap_volume_t", os.path.join(REPO, "roles", "ontap_report", "library", "site_ontap_volume.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCluster(object):
    """A cluster for grow(): its volume (sizes that move on the reads given), its aggregate, its LUN; every PATCH kept."""

    def __init__(self, vols, lun_size=2 * TB, avail=None, aggr_used=int(14 * TB)):
        self.reads, self.vols, self.patches, self.lun_size = 0, list(vols), [], lun_size
        self.avail = avail if avail is not None else [int(0.85 * TB)] * len(vols)
        self.aggr_used = aggr_used

    def vol(self):
        i = min(self.reads, len(self.vols) - 1)
        self.reads += 1
        return {"uuid": "v1", "name": "vol_ds01", "svm": {"name": "svm1"}, "aggregates": [{"name": "aggr1"}], "state": "online", "type": "rw",
                "style": "flexvol", "space": {"size": self.vols[i], "used": self.vols[i] - self.avail[i], "available": self.avail[i],
                                              "snapshot": {"reserve_percent": 0, "used": 0}}, "guarantee": {"type": "volume"}, "comment": ""}

    def records(self, path, fields, **q):
        if path == "/api/storage/volumes":
            return [self.vol()]
        if path == "/api/storage/aggregates":
            return [{"uuid": "a1", "name": "aggr1", "space": {"block_storage": {"size": 20 * TB, "used": self.aggr_used}}}]
        return [{"uuid": "l1", "name": "/vol/vol_ds01/l", "location": {"volume": {"name": "vol_ds01"}},
                 "space": {"size": self.lun_size, "used": TB, "guarantee": {"requested": True}}}]

    def call(self, method, path, query=None, body=None):
        self.patches.append((path, body))
        if "luns" in path:
            self.lun_size = body["space"]["size"]
        elif "size" in body:
            self.vols = [body["size"]]
            self.reads = 0
        return {}

    def wait(self, answer, seconds):
        return ""


class Grow1124(unittest.TestCase):
    """grow(): never a size under the volume's own (autosize grew it after the last read), the volume's growth worked out again
    from what is there before the first write (a snapshot deleted made the room), the LUN's new size within its volume."""

    @classmethod
    def setUpClass(cls):
        cls.V = volume_module()

    def plan(self, size=3 * TB, avail=int(0.85 * TB), lun_size=2 * TB):
        v = vol(size, size - avail, available=avail, snap_pct=0, guarantee="volume", name="vol_ds01", svm="svm1")
        p = X.plan(v, AGGR, size, {}, lun={"name": "/vol/vol_ds01/l", "size": lun_size, "thick": True, "total": lun_size}, lun_listed=lun_size)
        p.update(volume_uuid="v1", lun_uuid="l1")
        return p

    def test_p1_autosize_grew_it_after_the_last_read(self):
        p = self.plan(avail=int(0.1 * TB))                       # thick: the volume must grow (+0.1 TB more than free)
        self.assertGreater(p["vol_growth"], 0)
        bigger = p["target"] + 50 * GB
        c = FakeCluster([3 * TB, bigger], avail=[int(0.1 * TB), int(0.1 * TB) + 50 * GB + p["target"] - 3 * TB])
        steps = self.V.grow(c, p, {"volume": "vol_ds01", "svm": "svm1"}, {"exclude": []}, "", 1)
        sizes = [b.get("size") for path, b in c.patches if "volumes" in path and b.get("size")]
        self.assertEqual(sizes, [])                                                  # never a size under its own
        self.assertTrue(all(s_["ok"] for s_ in steps), steps)
        self.assertIn("its size is not sent", steps[1]["result"])
        self.assertEqual(c.lun_size, 2 * TB + p["growth"])

    def test_p2_a_snapshot_deleted_since_the_plan_leaves_the_volume_as_it_is(self):
        p = self.plan(avail=int(0.1 * TB))
        self.assertGreater(p["vol_growth"], 0)
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)])                             # room now
        steps = self.V.grow(c, p, {"volume": "vol_ds01", "svm": "svm1"}, {"exclude": []}, "", 1)
        self.assertEqual([b for path, b in c.patches if "volumes" in path], [])
        self.assertEqual(c.lun_size, 2 * TB + p["growth"])
        self.assertTrue(all(s_["ok"] for s_ in steps), steps)

    def test_p2_needing_more_than_planned_stops(self):
        p = self.plan()
        self.assertEqual(p["vol_growth"], 0)
        c = FakeCluster([3 * TB], avail=[10 * GB])                                   # the room went: more than planned
        steps = self.V.grow(c, p, {"volume": "vol_ds01", "svm": "svm1"}, {"exclude": []}, "", 1)
        self.assertFalse(steps[0]["ok"])
        self.assertIn("needs more than planned now", steps[0]["result"])
        self.assertEqual(c.patches, [])

    def test_p2_the_luns_size_within_its_volume(self):
        p = self.plan()
        c = FakeCluster([3 * TB])
        steps = self.V._grow_lun(c, p, {"volume": "vol_ds01"}, 1, [], vol_size=2 * TB)
        self.assertFalse(steps[-1]["ok"])
        self.assertIn("would be over its volume", steps[-1]["result"])
        self.assertEqual(c.patches, [])


class Exclusions(unittest.TestCase):
    def rec(self, **kw):
        r = {"name": "vol_a", "svm": {"name": "svm1"}, "state": "online", "type": "rw", "style": "flexvol"}
        r.update(kw)
        return r

    def test_always(self):
        self.assertIn("SVM root", X.excluded(self.rec(is_svm_root=True)))
        self.assertIn("SVM root", X.excluded(self.rec(name="svm1_root")))
        self.assertIn("node", X.excluded(self.rec(name="vol0")))
        self.assertIn("node", X.excluded(self.rec(svm={})))
        self.assertIn("SnapMirror", X.excluded(self.rec(type="dp")))
        self.assertIn("LS", X.excluded(self.rec(type="ls")))
        self.assertIn("offline", X.excluded(self.rec(state="offline")))
        self.assertIn("restricted", X.excluded(self.rec(state="restricted")))
        self.assertIn("FlexGroup", X.excluded(self.rec(style="flexgroup")))
        self.assertEqual(X.excluded(self.rec()), "")

    def test_patterns(self):
        pats = ["*_root", "vol0", "svm_backup:*", "SVM2:Logs_?"]
        self.assertEqual(X.excluded(self.rec(name="app_root"), pats), "ontap_expand_exclude: *_root")
        self.assertEqual(X.excluded(self.rec(svm={"name": "svm_backup"}, name="vol_bk01"), pats), "ontap_expand_exclude: svm_backup:*")
        self.assertEqual(X.excluded(self.rec(svm={"name": "svm2"}, name="logs_1"), pats), "ontap_expand_exclude: SVM2:Logs_?")
        self.assertEqual(X.excluded(self.rec(svm={"name": "svm3"}, name="logs_1"), pats), "")
        self.assertEqual(X.excluded(self.rec(name="vol_ok"), pats), "")

    def test_excluded_by_hand_is_refused(self):
        p = X.plan(vol(name="vol_bk01", svm="svm_backup"), AGGR, 10 * GB, {"exclude": ["svm_backup:*"]})
        self.assertEqual(p["status"], "refused")
        self.assertIn("svm_backup:*", p["why"])


class Naa(unittest.TestCase):
    def test_naa(self):
        self.assertEqual(X.naa("80Ab1DmT5zXq"), "naa.600a09803830416231446d54357a5871")
        self.assertEqual(X.serial_of("naa.600a09803830416231446d54357a5871"), "80Ab1DmT5zXq")
        self.assertEqual(X.serial_of("NAA.600A09803830416231446D54357A5871"), "80Ab1DmT5zXq")
        self.assertEqual(X.serial_of("naa.6000c29a1b2c3d4e5f60718293a4b5c6"), "")     # not NetApp
        self.assertEqual(X.naa(""), "")


class Choices(unittest.TestCase):
    E = {"cluster": "cluster1", "address": "cluster1.example.mil", "svm": "svm_vmware", "volume": "vol_ds03", "lun": "/vol/vol_ds03/lun_a",
         "luns": 2, "size": 2 * TB, "used": int(1.58 * TB), "aggr": "aggr1", "aggr_free": int(5.76 * TB), "datastore": "DS-SITEA-03A",
         "lun_size": 900 * GB, "lun_used": 711 * GB, "uuid": "v-ds03", "lun_uuid": "l-a"}

    def test_text_and_key(self):
        t = X.choice_text(self.E)
        self.assertTrue(t.startswith("vol_ds03/lun_a (svm_vmware) - LUN 900.0 GB, 21% free "
                                     "[VMware datastore DS-SITEA-03A, LUN lun_a of 2] [vol|cluster1.example.mil|svm_vmware|vol_ds03|"), t)
        self.assertEqual(X.parse_choice(t), {"address": "cluster1.example.mil", "svm": "svm_vmware", "volume": "vol_ds03",
                                             "lun": "/vol/vol_ds03/lun_a", "listed": 2 * TB, "lun_listed": 900 * GB, "uuid": "v-ds03", "lun_uuid": "l-a"})

    def test_same_names_on_two_clusters_show_the_cluster(self):
        sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
        import ontap_expand_filters as XF
        a = dict(self.E, lun="", luns=0, datastore="")
        b = dict(a, cluster="cluster2", address="cluster2.example.mil", uuid="v-other")
        c = dict(a, volume="vol_other", uuid="v-x")
        got = XF.ontap_expand_choices([a, b, c])["choices"]
        self.assertTrue(got[0].startswith("vol_ds03 (svm_vmware, cluster1) - "), got[0])
        self.assertTrue(got[1].startswith("vol_ds03 (svm_vmware, cluster2) - "), got[1])
        self.assertTrue(got[2].startswith("vol_other (svm_vmware) - "), got[2])
        self.assertEqual(X.parse_choice(got[1])["address"], "cluster2.example.mil")

    def test_fullest_first(self):
        sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
        import ontap_expand_filters as XF
        a = dict(self.E, lun="", luns=0, datastore="", volume="vol_a", size=100 * GB, used=10 * GB, uuid="a")
        b = dict(a, volume="vol_b", used=95 * GB, uuid="b")
        c = dict(a, volume="vol_c", used=50 * GB, uuid="c")
        got = XF.ontap_expand_choices([a, b, c], [{}, {"datastore": "DS-B"}, {}])["choices"]
        self.assertEqual([X.parse_choice(t)["uuid"] for t in got], ["b", "c", "a"])
        self.assertIn("[VMware datastore DS-B, NFS]", got[0])                    # its datastore follows it in the sort
        self.assertEqual(XF.ontap_expand_choices([a, b, c], [], 2)["more"], 1)

    def test_fullest_first_by_the_lun_or_its_datastore(self):
        sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
        import ontap_expand_filters as XF
        nas = dict(self.E, lun="", luns=0, datastore="", volume="vol_nas", size=100 * GB, used=85 * GB, uuid="n")
        thick = dict(self.E, luns=1, volume="vol_thick", size=100 * GB, used=99 * GB, lun_size=90 * GB, lun_used=30 * GB, uuid="t")
        ds = dict(thick, volume="vol_ds", uuid="d")
        got = XF.ontap_expand_choices([thick, nas, ds], [{}, {}, {"datastore": "DS9", "capacity": 89 * GB, "free": 4 * GB}])["choices"]
        self.assertEqual([X.parse_choice(t)["uuid"] for t in got], ["d", "n", "t"])        # the volume's own 99 % counts for nothing
        self.assertTrue(got[0].startswith("vol_ds (svm_vmware) - LUN 89.0 GB, 4% free [VMware datastore DS9, LUN]"), got[0])
        self.assertTrue(got[2].startswith("vol_thick (svm_vmware) - LUN 90.0 GB, 67% free [LUN]"), got[2])

    def test_plain_and_nfs(self):
        e = dict(self.E, lun="", luns=0, volume="vol_nfs02", size=500 * GB, used=470 * GB, datastore="NFS-DS02")
        t = X.choice_text(e)
        self.assertIn("[VMware datastore NFS-DS02, NFS]", t)
        self.assertEqual(X.parse_choice(t)["lun"], "")
        self.assertNotIn("[LUN", X.choice_text(dict(e, datastore="")))
        self.assertIsNone(X.parse_choice("vol_nfs02 (svm1, cluster1)"))     # typed by hand without the key: refused by the job

    def test_comment(self):
        self.assertEqual(X.comment_with("", "XINC0001234: +1.0 GB"), "XINC0001234: +1.0 GB")
        self.assertEqual(X.comment_with("app team", "XINC0001234: +1.0 GB"), "app team | XINC0001234: +1.0 GB")
        c = X.comment_with("x" * 1020, "XINC0001234: +1.0 GB")
        self.assertEqual(len(c), 1023)
        self.assertTrue(c.endswith("XINC0001234: +1.0 GB"))


# ---------------------------------------------------------------- the vCenter side, with fake pyVmomi objects (vcsim v0.52.0 has
# no NetApp LUN, no VMFS extent and answers MethodNotFound to QueryVmfsDatastoreExpandOptions and RefreshDatastoreStorageInfo)
import importlib.util  # noqa: E402
import types  # noqa: E402

for _n in ("site_ontap_expand",):
    _s = importlib.util.spec_from_file_location("ansible.module_utils." + _n, os.path.join(REPO, "plugins", "module_utils", _n + ".py"))
    _m = importlib.util.module_from_spec(_s)
    sys.modules["ansible.module_utils." + _n] = _m
    _s.loader.exec_module(_m)
sys.path.insert(0, os.path.join(REPO, "roles", "vmware_vm", "library"))
import site_vmware_netapp_ds as V  # noqa: E402

NAA = X.naa("80Ab1DmT5zXq")
NS = types.SimpleNamespace


class FakeLun(NS):
    pass


def lun(naa, gb):
    return FakeLun(canonicalName=naa, capacity=NS(block=int(gb * GB / 512), blockSize=512))


class FakeHost(object):
    def __init__(self, name, luns, after=None, fail=None):
        self.name, self.luns, self.after, self.fail, self.calls = name, luns, after, fail, []
        ss = NS(RescanAllHba=self.rescan_hba, RescanVmfs=lambda: self.calls.append("vmfs"), storageDeviceInfo=NS(scsiLun=luns))
        self.configManager = NS(storageSystem=ss, datastoreSystem=None)
        self.config = NS(storageDevice=NS(scsiLun=luns))

    def rescan_hba(self):
        if self.fail:
            raise RuntimeError(self.fail)
        self.calls.append("hba")
        if self.after is not None:
            for x in self.luns:
                x.capacity = NS(block=int(self.after * GB / 512), blockSize=512)


class FakeDs(object):
    def __init__(self, name, cap_gb, vmfs=None, nas=None, hosts=()):
        self.name = name
        self.summary = NS(capacity=int(cap_gb * GB))
        self.info = NS(vmfs=NS(extent=[NS(diskName=vmfs)]) if vmfs else None, nas=nas)
        self.host = [NS(key=h) for h in hosts]
        self.refreshed = False

    def RefreshDatastore(self):
        pass

    def RefreshDatastoreStorageInfo(self):
        self.refreshed = True
        self.summary.capacity = int(550 * GB)


class FakeDss(object):
    def __init__(self, ds, opts=True):
        self.ds, self.opts, self.expanded = ds, opts, False

    def QueryVmfsDatastoreExpandOptions(self, datastore):
        # another extent's free space first: only the option on the grown LUN may be taken (a multi-extent datastore)
        other = NS(spec=NS(extent=NS(diskName="naa.6000c29aaaaaaaaaaaaaaaaaaaaaaaa"), name="spec-other"))
        return [other, NS(spec=NS(extent=NS(diskName=NAA), name="spec-1"))] if self.opts else [other]

    def ExpandVmfsDatastore(self, datastore, spec):
        self.expanded = spec
        datastore.summary.capacity = int(9.75 * GB)


def content(hosts, dss):
    def view(_root, kinds, _rec):
        return NS(view=hosts if kinds == ["H"] else dss, Destroy=lambda: None)
    return NS(rootFolder=None, viewManager=NS(CreateContainerView=view))


class Vcenter(unittest.TestCase):
    def setUp(self):
        self.hosts = [FakeHost("esx0%d.corp.example.mil" % i, [lun(NAA, 9), lun("naa.6000c29aaaaaaaaaaaaaaaaaaaaaaaa", 50)], after=10)
                      for i in range(1, 5)]
        self.ds = FakeDs("DS-SITEA-01", 8.75, vmfs=NAA, hosts=self.hosts)
        self.nfs = FakeDs("NFS-DS02", 500, nas=NS(remoteHost="192.0.2.21", remotePath="/vol_nfs02/", remoteHostNames=None), hosts=self.hosts[:2])
        self.nfs_other = FakeDs("NFS-OTHER", 500, nas=NS(remoteHost="192.0.2.99", remotePath="/vol_nfs02", remoteHostNames=None))
        self.c = content(self.hosts, [self.ds, self.nfs])

    def found(self, key, dss=None):
        seen, vmfs, nfs = V.scan(content(self.hosts, dss or [self.ds, self.nfs]), "H", "D")
        f = V.match(key, seen, vmfs, nfs)
        if f:
            f["seen"] = seen.get(str(key.get("naa") or "").lower(), [])
        return f

    def test_vmfs_by_naa(self):
        f = self.found({"naa": NAA})
        self.assertEqual((f["datastore"], f["kind"], len(f["hosts"]), f["serial"]), ("DS-SITEA-01", "VMFS", 4, "80Ab1DmT5zXq"))
        self.assertEqual(self.found({"naa": X.naa("XXXXXXXXXXXX")}), {})

    def test_nfs_by_path_and_address(self):
        ip = ["192.0.2.21"]
        self.assertEqual(self.found({"nfs_path": "/vol_nfs02", "nfs_ips": ip})["datastore"], "NFS-DS02")   # a trailing / is the same path
        self.assertEqual(self.found({"nfs_path": "/vol_nfs02"}), {})               # the SVM's addresses unknown: never a guess
        self.assertEqual(self.found({"nfs_path": "/vol_nfs02"}, [self.nfs, self.nfs_other]), {})  # two, no address: none
        f = self.found({"nfs_path": "/vol_nfs02", "nfs_ips": ["192.0.2.21"]}, [self.nfs, self.nfs_other])
        self.assertEqual(f["datastore"], "NFS-DS02")
        # the same path, mounted from another SVM only: never taken for this one
        seen, vmfs, nfs = V.scan(content(self.hosts, [self.nfs_other]), "H", "D")
        self.assertEqual(V.match({"nfs_path": "/vol_nfs02", "nfs_ips": ["192.0.2.21"]}, seen, vmfs, nfs, resolve=set), {})
        # the datastore's server given by name: its address counts
        named = FakeDs("NFS-NAMED", 500, nas=NS(remoteHost="svm-vmware.corp.example.mil", remotePath="/vol_nfs02", remoteHostNames=None))
        seen, vmfs, nfs = V.scan(content(self.hosts, [named]), "H", "D")
        f = V.match({"nfs_path": "/vol_nfs02", "nfs_ips": ["192.0.2.21"]}, seen, vmfs, nfs,
                    resolve=lambda names: set(names) | {"192.0.2.21"})
        self.assertEqual(f["datastore"], "NFS-NAMED")
        self.assertEqual(self.found({"nfs_path": "/", "nfs_ips": ip}), {})

    def test_grow_vmfs(self):
        dss = FakeDss(self.ds)
        for h in self.hosts:
            h.configManager.datastoreSystem = dss
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB)
        self.assertEqual([s["ok"] for s in steps], [True, True], steps)
        self.assertEqual(steps[0]["result"], "Done - 4 of 4 hosts see 10.0 GB")
        self.assertEqual(steps[1]["result"], "Done - DS-SITEA-01 is 9.8 GB (was 8.8 GB)")
        self.assertEqual(dss.expanded.name, "spec-1")
        self.assertTrue(all(h.calls == ["hba", "vmfs"] for h in self.hosts))

    def test_a_host_that_still_sees_it_small_stops_the_expand(self):
        self.hosts[2].after = None                   # its rescan finds nothing new
        dss = FakeDss(self.ds)
        for h in self.hosts:
            h.configManager.datastoreSystem = dss
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB)
        self.assertEqual(len(steps), 1)
        self.assertIn("esx03.corp.example.mil sees 9.0 GB", steps[0]["result"])
        self.assertFalse(dss.expanded)

    def test_no_expand_option_and_a_failed_rescan(self):
        dss = FakeDss(self.ds, opts=False)
        for h in self.hosts:
            h.configManager.datastoreSystem = dss
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB)
        self.assertFalse(steps[1]["ok"])
        self.assertIn("offers no way to expand", steps[1]["result"])
        self.hosts[0].fail = "no permission Host.Config.Storage"
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB)
        self.assertIn("no permission", steps[0]["result"])

    def test_no_host_sees_it_and_a_second_vcenter_refreshes(self):
        f = self.found({"naa": NAA})
        f["seen"] = []
        steps = V.grow_vmfs(f, NAA, 10 * GB)
        self.assertEqual((len(steps), steps[0]["ok"]), (1, False))
        self.assertIn("no connected host sees the LUN", steps[0]["result"])
        refreshed = []
        self.ds.RefreshDatastore = lambda: refreshed.append(1)
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB, expand=False, where=" (vCenter vc02)")
        self.assertEqual([s["step"] for s in steps], ["Rescan the hosts that see the LUN", "Refresh the VMFS datastore"])
        self.assertTrue(refreshed and all(s["ok"] for s in steps))

    def test_a_lun_grown_more_since_stops_the_expand(self):
        for h in self.hosts:
            h.after = 30                             # someone grew it to 30 GB since this run's 10 GB
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB)
        self.assertEqual(len(steps), 1)
        self.assertIn("more than this run made it", steps[0]["result"])

    def test_two_vcenters_check_all_before_the_expand(self):
        """Codex pass 5: vCenter A sees the LUN bigger than this run made it, B has the datastore - B must not expand it"""
        def run(a_after):
            vc_a = content([FakeHost("esx-a", [lun(NAA, 9)], after=a_after)], [])
            h = FakeHost("esx-b", [lun(NAA, 9)], after=10)
            ds = FakeDs("DS", 9, vmfs=NAA, hosts=[h])
            h.configManager.datastoreSystem = FakeDss(ds)
            vc_b = content([h], [ds])
            out = {}
            saved = (V.HAS, V.vim, getattr(V, "each_vcenter", None), V.AnsibleModule)
            V.HAS, V.vim = True, NS(HostSystem="H", Datastore="D")
            V.each_vcenter = lambda m, read, **kw: ([(n, read(None, c, n)) for n, c in (("vc-a", vc_a), ("vc-b", vc_b))], [])
            V.AnsibleModule = lambda **kw: NS(params=dict(action="grow", keys=[{"naa": NAA}], lun_bytes=10 * GB, vcenters=[],
                                                          validate_certs=True), check_mode=False, exit_json=lambda **k: out.update(k))
            try:
                V.main()
            finally:
                V.HAS, V.vim, V.each_vcenter, V.AnsibleModule = saved
            return out, h.configManager.datastoreSystem
        out, dss = run(12)
        self.assertFalse(dss.expanded)
        self.assertIn("more than this run made it", out["steps"][0]["result"])
        out, dss = run(10)
        self.assertEqual(dss.expanded.name, "spec-1")
        self.assertEqual([s["step"] for s in out["steps"]], ["Rescan the hosts that see the LUN", "Rescan the hosts that see the LUN",
                                                              "Expand the VMFS datastore"])      # both vCenters checked, then the expand

    def test_already_expanded_is_fine(self):
        dss = FakeDss(self.ds, opts=False)
        for h in self.hosts:
            h.configManager.datastoreSystem = dss
        self.ds.summary.capacity = int(9.9 * GB)                   # VMFS over the whole 10 GB LUN, less its own space
        steps = V.grow_vmfs(self.found({"naa": NAA}), NAA, 10 * GB)
        self.assertTrue(steps[1]["ok"], steps)
        self.assertIn("Nothing to do", steps[1]["result"])

    def test_nfs_refresh(self):
        steps = V.grow_nfs(self.found({"nfs_path": "/vol_nfs02", "nfs_ips": ["192.0.2.21"]}))
        self.assertTrue(steps[0]["ok"])
        self.assertTrue(self.nfs.refreshed)
        self.assertEqual(steps[0]["result"], "Done - NFS-DS02 shows 550.0 GB")


if __name__ == "__main__":
    unittest.main()
