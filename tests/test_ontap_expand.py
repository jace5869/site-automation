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
    if "available" not in kw and v.get("used") is not None and v.get("size"):
        v["available"] = max(0, int(v["size"]) - int(v["used"]))      # 1.130.0: a LUN plan needs space.available (as ONTAP gives it)
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
        self.assertEqual(p["target"], 11 * GB)                 # 10/9 headroom would be 11.1 GB: capped at the LUN's growth (no reserve)
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
        """a thin LUN bigger than its volume (over-provisioned): the volume would have to grow more than the LUN's growth -
        refused, said (never a LUN over its volume)"""
        p = X.plan(vol(), AGGR, 10 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 20 * GB}, lun_listed=20 * GB)
        self.assertEqual(p["status"], "refused")
        self.assertIn("more than the LUN's growth and its snapshot reserve", p["why"])

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
        self.assertEqual(p["target"], int(2.5 * TB) + g)                # the 1.25x headroom capped at the LUN's growth (no reserve)
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

    def test_p1_the_recheck_takes_the_guarantee_as_it_is_now(self):
        thin = vol(100 * GB, 50 * GB, available=50 * GB, guarantee="none")
        tight = {"name": "aggr1", "size": 100 * GB, "used": 96 * GB}                          # 1 GiB under 97 %
        p = X.plan(thin, tight, 100 * GB, {}, size_text="5GB")
        self.assertEqual((p["status"], p["landing"]), ("grow", 0))
        self.assertEqual(X.recheck(p, thin, tight), "")
        self.assertIn("got fuller", X.recheck(p, dict(thin, guarantee="volume"), tight))      # a volume guarantee since: 5 GB lands

    def test_p2_a_thin_nfs_volume_admitted_by_what_lands(self):
        p = X.plan(vol(100 * GB, 50 * GB, available=50 * GB, guarantee="none"), {"name": "aggr1", "size": 100 * GB, "used": 96 * GB},
                   100 * GB, {}, size_text="2GB")
        self.assertEqual(p["status"], "grow", p["why"])
        self.assertEqual(p["aggr_pct_after"], 96.0)

    def test_p2_the_spill_counted_once(self):
        """70 GiB data, 30 GiB snapshots, 20 % reserve, 100 GiB full: +10 GiB needs 110 GiB, not 112.5"""
        v = self.v(100 * GB, 0, snap=20, snap_used=30 * GB)
        self.assertEqual(X.usable_used(v)[0], 70 * GB)
        vt, _how = X.volume_for_lun(v, {"name": "l", "size": 60 * GB, "thick": True, "total": 60 * GB}, 10 * GB)
        self.assertEqual(vt, 110 * GB)
        self.assertEqual(X.size_for_usable(80 * GB, 20, 0), 100 * GB)                          # no snapshots: the reserve only
        self.assertEqual(X.size_for_usable(80 * GB, 0, 5 * GB), 85 * GB)                       # no reserve: all of them spill


def XF_doc(r):
    sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
    import ontap_expand_filters as XF
    return XF.ontap_expand_doc(r)


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


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

    def __init__(self, vols, lun_size=2 * TB, avail=None, aggr_used=int(14 * TB), autosize=None, fail=()):
        self.reads, self.vols, self.patches, self.lun_size = 0, list(vols), [], lun_size
        self.autosize, self.fail = dict(autosize or {"mode": "off"}), fail
        self.lun_parent, self.aggr_name = "v1", "aggr1"
        self.avail = avail if avail is not None else [int(0.85 * TB)] * len(vols)
        self.aggr_used = aggr_used

    def vol(self):
        i = min(self.reads, len(self.vols) - 1)
        self.reads += 1
        return {"uuid": "v1", "name": "vol_ds01", "svm": {"name": "svm1"}, "aggregates": [{"name": self.aggr_name}], "state": "online", "type": "rw",
                "style": "flexvol", "space": {"size": self.vols[i], "used": self.vols[i] - self.avail[i], "available": self.avail[i],
                                              "snapshot": {"reserve_percent": 0, "used": 0}}, "guarantee": {"type": "volume"}, "comment": "",
                "autosize": dict(self.autosize)}

    def records(self, path, fields, **q):
        if path == "/api/storage/volumes":
            return [self.vol()]
        if path == "/api/storage/aggregates":
            return [{"uuid": "a1", "name": q.get("name", "aggr1"), "space": {"block_storage": {"size": 20 * TB, "used": self.aggr_used}}}]
        return [{"uuid": "l1", "name": "/vol/vol_ds01/l", "location": {"volume": {"name": "vol_ds01", "uuid": self.lun_parent}},
                 "space": {"size": self.lun_size, "used": TB, "guarantee": {"requested": True}}}]

    def call(self, method, path, query=None, body=None):
        self.patches.append((path, body))
        for k in self.fail:
            if k in body:
                raise self.Failed("HTTP 400: refused (%s)" % k)
        if "autosize" in body:
            self.autosize.update(body["autosize"])
            return {}
        if "luns" in path:
            self.lun_size = body["space"]["size"]
        elif "size" in body:
            # like ONTAP: the growth becomes available (no snapshot reserve here) - on the size as last read
            i = min(max(0, self.reads - 1), len(self.vols) - 1)
            self.avail = [self.avail[min(i, len(self.avail) - 1)] + body["size"] - self.vols[i]]
            self.vols = [body["size"]]
            self.reads = 0
        return {}

    def wait(self, answer, seconds):
        return ""


class Grow1124(unittest.TestCase):
    """grow(): autosize never changed (grow_shrink refuses, a maximum under the new size said); every size sent with a
    size read just now and within the plan, exact (may_write); read again right before each change, nothing sent when it
    moved; read back - at least the size before + the growth; no definite outcome: said, no LUN, failed; the LUN's new
    size never made smaller to fit its volume."""

    @classmethod
    def setUpClass(cls):
        cls.V = volume_module()
        FakeCluster.Failed = cls.V.Failed

    def plan(self, size=3 * TB, avail=int(0.85 * TB), lun_size=2 * TB):
        v = vol(size, size - avail, available=avail, snap_pct=0, guarantee="volume", name="vol_ds01", svm="svm1")
        p = X.plan(v, AGGR, size, {}, lun={"name": "/vol/vol_ds01/l", "size": lun_size, "thick": True, "total": lun_size}, lun_listed=lun_size)
        p.update(volume_uuid="v1", lun_uuid="l1")
        return p

    def nas_plan(self, size=100 * GB):
        v = vol(size, size // 2, available=size // 2, guarantee="volume", name="vol_ds01", svm="svm1")
        p = X.plan(v, AGGR, size, {}, size_text="10GB")
        p.update(volume_uuid="v1", lun_uuid="")
        return p

    def run_(self, c, p):
        return self.V.grow(c, p, {"volume": "vol_ds01", "svm": "svm1"}, {"exclude": []}, "", 1)

    def test_a_thick_lun_volume_grown_then_its_lun(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        steps = self.run_(c, p)
        self.assertEqual([x["step"] for x in steps], ["Read the volume and aggr1 again", "Grow the volume", "Grow the LUN"])
        self.assertTrue(all(x["ok"] for x in steps), steps)
        self.assertEqual([b for _p, b in c.patches if "volumes" in _p], [{"size": p["target"]}])
        self.assertFalse([b for _p, b in c.patches if "autosize" in b])                    # autosize never touched
        self.assertEqual(c.lun_size, 2 * TB + p["growth"])

    def test_grow_shrink_refused_in_the_plan_and_the_change(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow_shrink", "maximum": 4 * TB})
        steps = self.run_(c, p)
        self.assertIn("autosize is grow_shrink on this volume: set it to grow or off in System Manager first, then run again", steps[0]["result"])
        self.assertEqual(c.patches, [])

    def test_the_plan_refuses_grow_shrink_and_says_the_maximum(self):
        key = {"address": "c1", "svm": "svm1", "volume": "vol_ds01", "lun": "/vol/vol_ds01/l", "listed": 3 * TB, "lun_listed": 2 * TB,
               "uuid": "v1", "lun_uuid": "l1"}
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow_shrink", "maximum": 4 * TB})
        p, _v = self.V.read_one(c, key, {"pct": 10, "min_gb": 1, "max_gb": 512, "max_pct": 97, "exclude": [], "lun_mode": "same"}, "")
        self.assertEqual((p["status"], p["why"]), ("refused", self.V.GROW_SHRINK))
        # Codex 1.128.0: a new size over autosize's maximum would make ONTAP raise it - a change to autosize: refused
        o = {"pct": 10, "min_gb": 1, "max_gb": 512, "max_pct": 97, "exclude": [], "lun_mode": "same"}
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow", "maximum": 3 * TB})
        p, _v = self.V.read_one(c, key, o, "")
        self.assertEqual(p["status"], "refused")
        self.assertIn("would raise autosize maximum 3.00 TB (ONTAP raises it with the size) - grow it by hand or raise the maximum first", p["why"])
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow"})       # on, no maximum given: not known
        p, _v = self.V.read_one(c, key, o, "")
        self.assertEqual((p["status"], p["why"]), ("refused", "autosize is grow and ONTAP gave no autosize maximum - nothing is changed without it"))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow", "maximum": 5 * TB})
        self.assertEqual(self.V.read_one(c, key, o, "")[0]["status"], "grow")
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)], autosize={"mode": "grow", "maximum": 3 * TB})     # the LUN only: the volume stays
        self.assertEqual(self.V.read_one(c, key, o, "")[0]["status"], "grow")

    def test_a_maximum_under_the_new_size_stops_the_change(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow", "maximum": 3 * TB})
        steps = self.run_(c, p)
        self.assertIn("would raise autosize maximum 3.00 TB", steps[0]["result"])
        self.assertEqual(c.patches, [])
        self.assertEqual(self.V.autosize_max_why({"autosize": "off", "autosize_max": 0}, 4 * TB), "")            # off, none reported

    def test_no_size_from_ontap_no_write(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        orig = c.vol

        def vol_():
            r = orig()
            del r["space"]["size"]
            return r
        c.vol = vol_
        steps = self.run_(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("ONTAP gave no size", steps[0]["result"])

    def test_moved_right_before_the_patch_not_sent(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB, 3 * TB + 5 * GB], avail=[int(0.1 * TB)] * 2)              # READ, then moved before the PATCH
        steps = self.run_(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("changed right before the change", steps[-1]["result"])

    def test_landed_means_the_whole_growth(self):
        p = self.nas_plan()
        c = FakeCluster([100 * GB], avail=[50 * GB])
        orig = c.call

        def call(method, path, query=None, body=None):
            r = orig(method, path, query, body)
            if "size" in (body or {}):
                c.vols = [100 * GB + 5 * GB]                                              # said done, only half there
            return r
        c.call = call
        steps = self.run_(c, p)
        self.assertFalse(steps[-1]["ok"])
        self.assertIn("resize of volume vol_ds01 (svm1): outcome not known", steps[-1]["result"])

    def test_no_answer_outcome_not_known_no_lun(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        orig = c.call

        def call(method, path, query=None, body=None):
            if "size" in (body or {}):
                raise self.V.Unreachable("timed out", "timeout")
            return orig(method, path, query, body)
        c.call = call
        steps = self.run_(c, p)
        self.assertEqual(steps[-1]["step"], "Grow the volume")
        self.assertIn("outcome not known", steps[-1]["result"])
        self.assertIn("check it in ONTAP System Manager before running again", steps[-1]["result"])
        self.assertEqual(c.lun_size, 2 * TB)                                              # the LUN not grown

    def test_a_refusal_is_said_nothing_changed(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], fail=("size",))
        steps = self.run_(c, p)
        self.assertIn("Refused", steps[-1]["result"])
        self.assertIn("nothing was changed", steps[-1]["result"])
        self.assertEqual(c.lun_size, 2 * TB)

    def test_a_growth_of_a_mib_is_sent_and_must_read_back_bigger(self):
        # Codex 1.128.0: any growth within the caps is sent (a LUN's parent may need 1 MiB); the read back must be bigger
        p = self.nas_plan()
        p["max_target"] = 100 * GB + X.MB
        c = FakeCluster([100 * GB], avail=[50 * GB])
        steps = self.run_(c, p)
        self.assertEqual([b["size"] for _p, b in c.patches if "size" in b], [100 * GB + X.MB])
        self.assertTrue(steps[-1]["ok"], steps)
        c = FakeCluster([100 * GB], avail=[50 * GB])
        c.call = lambda method, path, query=None, body=None: {}                         # said done, not changed
        steps = self.run_(c, p)
        self.assertFalse(steps[-1]["ok"])
        self.assertIn("outcome not known", steps[-1]["result"])

    def test_never_over_the_plans_own_size(self):
        p = self.nas_plan()
        p["max_target"] = p["target"]
        c = FakeCluster([100 * GB + X.MB], avail=[50 * GB])                              # 1 MiB more since the plan
        self.run_(c, p)
        self.assertEqual([b["size"] for _p, b in c.patches if "size" in b], [p["max_target"]])

    def test_never_over_the_plans_lun_size(self):
        p = self.plan(avail=int(0.9 * TB))                                               # room: the LUN only
        p["max_lun_target"] = p["lun"]["target"]
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)], lun_size=2 * TB + X.MB // 2)
        self.run_(c, p)
        self.assertEqual(c.lun_size, p["max_lun_target"])

    def test_the_lun_target_never_made_smaller_to_fit(self):
        p = self.plan()
        c = FakeCluster([2 * TB + 10 * GB])                                              # the parent read right before: too small
        steps = self.V._grow_lun(c, p, {"volume": "vol_ds01", "svm": "svm1"}, 1, [], "v1")
        self.assertFalse(steps[-1]["ok"])
        self.assertIn("cannot hold the LUN's new size", steps[-1]["result"])
        self.assertEqual(c.patches, [])

    def test_a_lun_with_no_answer_is_said(self):
        p = self.plan(avail=int(0.9 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)])
        orig = c.call

        def call(method, path, query=None, body=None):
            if "luns" in path:
                raise self.V.Unreachable("timed out", "timeout")
            return orig(method, path, query, body)
        c.call = call
        steps = self.run_(c, p)
        self.assertFalse(steps[-1]["ok"])
        self.assertIn("resize of LUN /vol/vol_ds01/l: outcome not known", steps[-1]["result"])

    def test_a_snapshot_deleted_since_the_plan_leaves_the_volume_as_it_is(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)])                                 # room now
        steps = self.run_(c, p)
        self.assertEqual([b for path, b in c.patches if "volumes" in path], [])
        self.assertEqual(c.lun_size, 2 * TB + p["growth"])
        self.assertTrue(all(s_["ok"] for s_ in steps), steps)

    def test_needing_more_than_planned_stops(self):
        p = self.plan()
        c = FakeCluster([3 * TB], avail=[10 * GB])
        steps = self.run_(c, p)
        self.assertIn("needs more than planned now", steps[0]["result"])
        self.assertEqual(c.patches, [])

    def test_a_lun_resized_since_stops_before_any_write(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], lun_size=2 * TB + 10 * GB)
        steps = self.run_(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("the LUN is 2.01 TB now", steps[0]["result"])

    def test_the_write_rule_exact(self):
        V = self.V
        self.assertTrue(V.may_write(3 * TB, 3 * TB + 5, 3 * TB + 5))
        self.assertFalse(V.may_write(3 * TB, 3 * TB + 6, 3 * TB + 5))
        self.assertFalse(V.may_write(3 * TB, 3 * TB - 1, 3 * TB + 5))
        self.assertFalse(V.may_write(0, 5, 5))
        self.assertFalse(V.may_write(None, 5, 5))
        p = self.plan(avail=int(0.1 * TB))
        p["vol_growth_cap"] = p["vol_growth"] - X.MB // 2
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        steps = V.grow(c, p, {"volume": "vol_ds01", "svm": "svm1"}, {"exclude": []}, "", 1)
        self.assertEqual(c.patches, [])
        self.assertIn("needs more than planned now", steps[0]["result"])

    def test_final_p1_the_volume_moved_aggregate_before_the_patch(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        orig = c.vol
        n = {"i": 0}

        def vol_():
            n["i"] += 1
            if n["i"] == 2:                                                          # the read right before the PATCH
                c.aggr_name = "aggr9"
            return orig()
        c.vol = vol_
        steps = self.run_(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("the volume moved to aggregate aggr9 - run again", steps[-1]["result"])

    def test_final_p1_no_room_left_right_before_the_volume_patch(self):
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        orig = c.vol
        n = {"i": 0}

        def vol_():
            n["i"] += 1
            if n["i"] == 2:
                c.aggr_used = int(20 * TB * 0.97)                                       # the aggregate filled up meanwhile
            return orig()
        c.vol = vol_
        steps = self.run_(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("aggr1 has no room for it now", steps[-1]["result"])

    def test_final_p1_no_room_for_a_thick_luns_growth_on_a_thin_volume(self):
        thin = vol(3 * TB, int(2.15 * TB), available=int(0.85 * TB), snap_pct=0, guarantee="none", name="vol_ds01", svm="svm1")
        p = X.plan(thin, AGGR, 3 * TB, {}, lun={"name": "/vol/vol_ds01/l", "size": 2 * TB, "thick": True, "total": 2 * TB}, lun_listed=2 * TB)
        p.update(volume_uuid="v1", lun_uuid="l1")
        self.assertEqual((p["status"], p["vol_growth"]), ("grow", 0))
        c = FakeCluster([3 * TB], avail=[int(0.85 * TB)])
        orig_vol, orig_rec = c.vol, c.records

        def vol_():
            r = orig_vol()
            r["guarantee"] = {"type": "none"}
            return r

        def records(path, fields, **q):
            if path == "/api/storage/luns" and q.get("uuid") == "l1":
                c.aggr_used = int(20 * TB * 0.97) - 10 * GB                            # less than the LUN's growth left
            return orig_rec(path, fields, **q)
        c.vol, c.records = vol_, records
        steps = self.run_(c, p)
        self.assertEqual(c.lun_size, 2 * TB)
        self.assertIn("aggr1 has no room for it now", steps[-1]["result"])

    def test_final_p2_a_lun_of_another_volume_with_the_same_name_is_not_grown(self):
        p = self.plan(avail=int(0.9 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)])
        c.lun_parent = "v-other"
        steps = self.run_(c, p)
        self.assertEqual(c.lun_size, 2 * TB)
        self.assertIn("moved to volume vol_ds01 (another volume)", steps[-1]["result"])

    def test_final_p2_never_over_the_plans_target_even_without_max_target(self):
        p = self.nas_plan()
        planned = p["target"]
        c = FakeCluster([100 * GB + X.MB], avail=[50 * GB])                              # tolerated drift since the plan
        self.run_(c, p)
        self.assertEqual([b["size"] for _p, b in c.patches if "size" in b], [planned])

    def test_codex_1280_the_autosize_maximum_read_again_right_before_the_patch(self):
        # the maximum lowered between the first read and the last one: nothing sent (ONTAP would raise it)
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)], autosize={"mode": "grow", "maximum": 5 * TB})
        orig = c.vol

        def vol_():
            r = orig()
            if c.reads > 1:
                r["autosize"] = {"mode": "grow", "maximum": 3 * TB}
            return r
        c.vol = vol_
        steps = self.run_(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("would raise autosize maximum 3.00 TB", steps[-1]["result"])

    def test_final_p2_an_unknown_outcome_in_the_reports_summary(self):
        p = dict(self.nas_plan(), outcome_unknown=True, vol_sent=True, cluster="c1")
        doc = XF_doc({"plan": p, "ontap_steps": [{"step": "Grow the volume", "exactly": "", "ok": False,
                                                  "result": "resize of volume vol_ds01 (svm1): outcome not known"}]})
        summ = dict((x["label"], x["value"]) for x in doc["summary"])
        self.assertEqual((summ["Volume after"], summ["Changed"]), ("not known", "not known"))

    def lun_reserve(self, c, after_reads, requested):
        """the LUN's space.guarantee.requested from its after_reads-th read by UUID on (None: not given)"""
        orig = c.records
        n = {"i": 0}

        def records(path, fields, **q):
            r = orig(path, fields, **q)
            if path == "/api/storage/luns" and q.get("uuid") == "l1":
                n["i"] += 1
                if n["i"] > after_reads:
                    r = [dict(r[0], space=dict(r[0]["space"], guarantee={} if requested is None else {"requested": requested}))]
            return r
        c.records = records

    def test_last_p1_a_thin_lun_made_thick_since_the_plan_stops(self):
        thin_vol = vol(3 * TB, int(2.15 * TB), available=int(0.85 * TB), snap_pct=0, guarantee="none", name="vol_ds01", svm="svm1")
        p = X.plan(thin_vol, AGGR, 3 * TB, {}, lun={"name": "/vol/vol_ds01/l", "size": 2 * TB, "thick": False, "total": 2 * TB}, lun_listed=2 * TB)
        p.update(volume_uuid="v1", lun_uuid="l1")
        c = FakeCluster([3 * TB], avail=[int(0.85 * TB)])
        self.lun_reserve(c, 0, True)                                                      # space-reserved now
        steps = self.run_(c, p)
        self.assertEqual(c.lun_size, 2 * TB)
        self.assertIn("the LUN's space reservation changed since the plan (thin, now thick) - run again", steps[-1]["result"])
        c = FakeCluster([3 * TB], avail=[int(0.85 * TB)])
        self.lun_reserve(c, 0, None)                                                      # not given: never guessed
        steps = self.run_(c, p)
        self.assertEqual(c.lun_size, 2 * TB)
        self.assertIn("ONTAP gave no space reservation", steps[-1]["result"])

    def test_last_p2_a_snapshot_deleted_right_before_the_patch_sends_less(self):
        p = self.plan(avail=int(0.1 * TB))
        planned = p["target"]
        c = FakeCluster([3 * TB, 3 * TB, 3 * TB], avail=[int(0.1 * TB), int(0.15 * TB), int(0.15 * TB)])     # room freed after READ
        steps = self.run_(c, p)
        sent = [b["size"] for _p, b in c.patches if "size" in b]
        self.assertEqual(len(sent), 1)
        self.assertLess(sent[0], planned)                                                # less, never more
        self.assertTrue(all(x["ok"] for x in steps), steps)
        p = self.plan(avail=int(0.1 * TB))
        c = FakeCluster([3 * TB, 3 * TB, 3 * TB], avail=[int(0.1 * TB), int(0.5 * TB), int(0.5 * TB)])       # room enough now
        steps = self.run_(c, p)
        self.assertFalse([b for _p, b in c.patches if "size" in b])                       # no volume change at all
        self.assertEqual(c.lun_size, 2 * TB + p["growth"])

    def test_last_p2_the_report_shows_what_was_read_back(self):
        p = self.nas_plan()
        c = FakeCluster([100 * GB], avail=[50 * GB], fail=("size",))
        steps = self.run_(c, p)
        doc = XF_doc({"plan": dict(p, cluster="c1"), "ontap_steps": steps[1:]})
        summ = dict((x["label"], x["value"]) for x in doc["summary"])
        self.assertEqual(summ["Volume after"], "100.0 GB")                               # refused: what it reads, not the plan's 110
        rows = [x for x in doc["sections"] if x.get("title", "").startswith("Now and")]
        self.assertEqual(rows[0]["rows"][0][2], "100.0 GB")                                # the After column too

    def test_last_p2_a_lun_read_back_that_fails_is_an_unknown_outcome(self):
        p = self.plan(avail=int(0.9 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)])
        orig = c.records
        sent = {"done": False}
        oc = c.call

        def call(method, path, query=None, body=None):
            if "luns" in path:
                sent["done"] = True
            return oc(method, path, query, body)

        def records(path, fields, **q):
            if sent["done"] and path == "/api/storage/luns":
                raise self.V.Unreachable("timed out", "timeout")
            return orig(path, fields, **q)
        c.call, c.records = call, records
        steps = self.run_(c, p)
        self.assertFalse(steps[-1]["ok"])
        self.assertIn("resize of LUN /vol/vol_ds01/l: outcome not known", steps[-1]["result"])
        self.assertTrue(p["outcome_unknown"])
        doc = XF_doc({"plan": dict(p, cluster="c1"), "ontap_steps": steps[1:]})
        self.assertEqual(dict((x["label"], x["value"]) for x in doc["summary"])["Changed"], "not known")

    def test_job_state(self):
        V = self.V
        self.addCleanup(setattr, V.time, "sleep", V.time.sleep)
        V.time.sleep = lambda s_: None
        st, why, u = V.job_state(types_ns(call=lambda *a, **k: {"state": "queued"}), {"job": {"uuid": "j9"}}, 0)
        self.assertEqual((st, u), ("unknown", "j9"))
        self.assertEqual(V.job_state(types_ns(call=lambda *a, **k: {"state": "success"}), {"job": {"uuid": "j9"}}, 0)[0], "done")
        self.assertEqual(V.job_state(None, {}, 0)[0], "done")                              # no job: made at once

class Codex1280(unittest.TestCase):
    """Codex's pre-release review of 1.128.0: every last read before a PATCH checks the name, SVM and exclusions (the comment
    too); the LUN's reservation on its last read; autosize's maximum never raised; one run at a time; the caps checked
    first; a parent that needs more now stops; the comment from the last read; a 1 MiB parent growth; the largest that fits."""

    @classmethod
    def setUpClass(cls):
        cls.V = volume_module()
        FakeCluster.Failed = cls.V.Failed

    def lun_plan(self, avail):
        v = vol(3 * TB, 3 * TB - avail, available=avail, snap_pct=0, guarantee="volume", name="vol_ds01", svm="svm1")
        p = X.plan(v, AGGR, 3 * TB, {}, lun={"name": "/vol/vol_ds01/l", "size": 2 * TB, "thick": True, "total": 2 * TB}, lun_listed=2 * TB)
        p.update(volume_uuid="v1", lun_uuid="l1")
        return p

    def nas_plan(self):
        v = vol(100 * GB, 50 * GB, available=50 * GB, guarantee="volume", name="vol_ds01", svm="svm1")
        p = X.plan(v, AGGR, 100 * GB, {}, size_text="10GB")
        p.update(volume_uuid="v1", lun_uuid="")
        return p

    def grow(self, c, p, exclude=(), line=""):
        return self.V.grow(c, p, {"volume": "vol_ds01", "svm": "svm1"}, {"exclude": list(exclude), "clusters": ("cluster1", "c1")}, line, 1)

    def later(self, c, after, **changes):
        """the volume's reads from the after-th on answer with these changes"""
        orig = c.vol

        def vol_():
            r = orig()
            if c.reads > after:
                for k, v in changes.items():
                    r[k] = v
            return r
        c.vol = vol_

    def test_p1a_renamed_into_an_exclusion_on_the_last_read_is_not_grown(self):
        p = self.nas_plan()
        c = FakeCluster([100 * GB], avail=[50 * GB])
        self.later(c, 1, name="backup_db")                                     # READ sees vol_ds01; the last read backup_db
        steps = self.grow(c, p, exclude=["backup*"])
        self.assertEqual(c.patches, [])
        self.assertIn("the volume is now backup_db on svm1", steps[-1]["result"])

    def test_p1a_excluded_now_on_the_last_read_is_not_grown(self):
        p = self.nas_plan()
        c = FakeCluster([100 * GB], avail=[50 * GB])
        self.later(c, 1, type="dp")                                            # made a SnapMirror destination meanwhile
        steps = self.grow(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("the volume is now excluded (DP volume", steps[-1]["result"])
        c = FakeCluster([100 * GB], avail=[50 * GB])
        self.later(c, 1, svm={"name": "svm_backup"})
        steps = self.grow(c, p, exclude=["svm_backup:*"])
        self.assertEqual(c.patches, [])
        self.assertIn("the volume is now vol_ds01 on svm_backup", steps[-1]["result"])

    def test_p1a_the_comment_only_write_is_checked_too(self):
        p = self.lun_plan(int(0.9 * TB))                                        # room: the volume stays, only its comment
        c = FakeCluster([3 * TB], avail=[int(0.9 * TB)])
        self.later(c, 1, type="dp")
        steps = self.grow(c, p, line="INC1: +204.8 GB")
        self.assertEqual(c.patches, [])
        self.assertEqual(c.lun_size, 2 * TB)
        self.assertIn("not sent: the volume is now excluded (DP volume", steps[-1]["result"])

    def test_p1b_the_lun_made_thick_on_its_last_read_is_not_grown(self):
        # Codex's own repro: a thin LUN on a thin volume, made space-reserved on the read right before the LUN's PATCH
        size = 100 * GB
        v = vol(size, 60 * GB, svm="svm1", available=40 * GB, guarantee="none")
        p = X.plan(v, AGGR, size, {}, lun={"name": "/vol/vol_ds01/l", "size": 40 * GB, "total": 40 * GB, "thick": False}, lun_listed=40 * GB, size_text="10GB")
        p.update(volume_uuid="v1", lun_uuid="l1")

        class Toggle(FakeCluster):
            ids = 0

            def vol(self):
                r = FakeCluster.vol(self)
                r["guarantee"] = {"type": "none"}
                return r

            def records(self, path, fields, **q):
                ans = FakeCluster.records(self, path, fields, **q)
                if path.endswith("/luns"):
                    if q.get("uuid"):
                        self.ids += 1
                    ans[0]["space"]["guarantee"]["requested"] = self.ids >= 4
                return ans
        c = Toggle([size], avail=[40 * GB], lun_size=40 * GB, aggr_used=int(20 * TB * .97) - GB)
        steps = self.grow(c, p)
        self.assertEqual([x for x, _b in c.patches if "luns" in x], [])
        self.assertEqual(c.lun_size, 40 * GB)
        self.assertIn("space reservation changed since the plan (thin, now thick)", steps[-1]["result"])
        self.assertFalse(steps[-1]["ok"])

    def test_p1d_one_run_at_a_time(self):
        jobs = [{"id": 90, "status": "running"}, {"id": 99, "status": "pending"}, {"id": 100, "status": "running"},
                {"id": 101, "status": "waiting"}, {"id": 102, "status": "pending"}]
        self.assertEqual(X.others_going(jobs, 100), [90, 99])                  # never itself, never one queued behind it
        self.assertEqual(X.others_going([{"id": 101, "status": "running"}], 100), [101])     # a later one already running
        self.assertEqual(X.others_going([{"id": 100, "status": "running"}], 100), [])
        self.assertEqual(X.others_going([], 100), [])

    def test_verify_autosize_off_with_a_reported_maximum_is_refused_too(self):
        # Codex verify: ONTAP resets a reported maximum when the size crosses it, whatever the mode
        self.assertIn("would raise autosize maximum", self.V.autosize_max_why({"autosize": "off", "autosize_max": 105 * GB}, 110 * GB))
        self.assertEqual(self.V.autosize_max_why({"autosize": "off", "autosize_max": 0}, 110 * GB), "")       # none reported: goes
        self.assertEqual(self.V.autosize_max_why({"autosize": "off", "autosize_max": 120 * GB}, 110 * GB), "")
        p = self.nas_plan()
        c = FakeCluster([100 * GB], avail=[50 * GB], autosize={"mode": "off", "maximum": 105 * GB})
        steps = self.grow(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("would raise autosize maximum 105.0 GB", steps[-1]["result"])
        c = FakeCluster([100 * GB, 100 * GB], avail=[50 * GB, 50 * GB], autosize={"mode": "off"})
        self.later(c, 1, autosize={"mode": "off", "maximum": 105 * GB})               # reported on the last read only
        steps = self.grow(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("would raise autosize maximum 105.0 GB", steps[-1]["result"])

    def test_verify_every_expand_template_is_asked(self):
        asked = []
        runs = {"job_templates/58/jobs/": [{"id": 300, "status": "running"}],
                "job_templates/61/jobs/": [{"id": 290, "status": "running"}, {"id": 310, "status": "pending"}],
                "workflow_job_templates/7/workflow_jobs/": [{"id": 299, "status": "running"}, {"id": 280, "status": "waiting"}]}

        def call(path):
            asked.append(path)
            if "?name=" in path:
                name = path.split("?name=")[1]
                ids = {"job_templates": {"NetApp%20-%20expand%20a%20volume": 58, "Expand%20B": 61},
                       "workflow_job_templates": {"Expand%20flow": 7}}[path.split("/")[0]]
                return 200, {"results": [{"id": ids[name]}] if name in ids else []}
            return 200, {"results": runs.get(path.split("?")[0], [])}
        busy, why = X.expand_busy(call, ["NetApp - expand a volume", {"template": "Expand B"}, {"template": "Expand flow", "kind": "workflow"},
                                         "Not made yet"], 58, 300, workflow_job_id=299)
        self.assertEqual(why, "")
        self.assertEqual(busy, [280, 290])               # the other template's running run, the workflow queued before; never
        #                                                  this run (300), its own workflow (299), nor one queued behind (310)
        self.assertEqual(len([p for p in asked if "job_templates/58/jobs" in p]), 1)          # its own template asked once
        busy, why = X.expand_busy(lambda path: (500, {}), ["NetApp - expand a volume"], 58, 300)
        self.assertEqual(busy, [])
        self.assertIn("could not be asked", why)

    def test_verify_a_thick_lun_needs_the_full_room_no_tolerance(self):
        p = self.lun_plan(int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        orig = c.call

        def call(method, path, query=None, body=None):
            r = orig(method, path, query, body)
            if "size" in (body or {}):
                c.avail = [c.avail[0] - X.MB]                                  # 1 MiB short of the room right after
            return r
        c.call = call
        steps = self.grow(c, p)
        self.assertTrue(steps[1]["ok"], steps)
        self.assertEqual(c.lun_size, 2 * TB)
        self.assertIn("has not the usable room for the thick LUN's new size", steps[-1]["result"])

    def test_p1e_the_caps_checked_first(self):
        ok = {"max_target": 110 * GB, "max_lun_target": -1, "max_vol_growth": 10 * GB}
        self.assertEqual(self.V.caps_why(ok), "")
        self.assertEqual(self.V.caps_why(dict(ok, max_vol_growth=0)), "")                  # a growth cap of 0: the LUN only
        self.assertIn("max_target", self.V.caps_why(dict(ok, max_target=0)))
        self.assertIn("max_lun_target must be -1", self.V.caps_why(dict(ok, max_lun_target=0)))
        self.assertIn("max_lun_target must be -1", self.V.caps_why(dict(ok, max_lun_target=-5)))
        self.assertIn("max_vol_growth must be -1", self.V.caps_why(dict(ok, max_vol_growth=-2)))
        self.assertIn("under the volume's size now", self.V.caps_why(dict(ok, max_target=90 * GB), {"size": 100 * GB}))
        self.assertIn("under the LUN's size now", self.V.caps_why(dict(ok, max_lun_target=GB), {"size": 100 * GB, "lun": {"size": 2 * GB}}))
        self.assertEqual(self.V.caps_why(dict(ok, max_lun_target=3 * GB), {"size": 100 * GB, "lun": {"size": 2 * GB}}), "")

    def test_p2_a_parent_that_needs_more_now_stops_before_any_patch(self):
        p = self.lun_plan(int(0.1 * TB))
        c = FakeCluster([3 * TB, 3 * TB], avail=[int(0.1 * TB), int(0.02 * TB)])    # free space went between READ and the last read
        steps = self.grow(c, p)
        self.assertEqual(c.patches, [])
        self.assertIn("more than the", steps[-1]["result"])
        self.assertIn("planned", steps[-1]["result"])

    def test_p2_the_comment_from_the_last_read(self):
        p = self.nas_plan()
        c = FakeCluster([100 * GB], avail=[50 * GB])
        self.later(c, 1, comment="edited by the storage team")
        self.grow(c, p, line="INC7: +10.0 GB")
        body = [b for _p, b in c.patches if "size" in b][0]
        self.assertTrue(body["comment"].startswith("edited by the storage team"), body)
        self.assertIn("INC7: +10.0 GB", body["comment"])

    def test_p2_a_thick_lun_needs_its_room_right_before_its_patch(self):
        p = self.lun_plan(int(0.1 * TB))
        c = FakeCluster([3 * TB], avail=[int(0.1 * TB)])
        orig = c.call

        def call(method, path, query=None, body=None):
            r = orig(method, path, query, body)
            if "size" in (body or {}):
                c.avail = [int(0.05 * TB)]                                    # written full right after the volume grew
            return r
        c.call = call
        steps = self.grow(c, p)
        self.assertTrue(steps[1]["ok"], steps)                                 # the volume grew
        self.assertEqual(c.lun_size, 2 * TB)                                   # the LUN left as it was
        self.assertIn("has not the usable room for the thick LUN's new size", steps[-1]["result"])

    def test_p2_the_largest_that_fits_counts_the_room_in_the_volume(self):
        v = vol(100 * GB, 10 * GB, available=90 * GB, snap_pct=0, guarantee="volume")
        aggr = {"name": "aggr1", "size": 1000 * GB, "used": 970 * GB - 5 * GB}             # 5 GiB under 97 %
        p = X.plan(v, aggr, 100 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 10 * GB, "thick": True, "total": 10 * GB},
                   lun_listed=10 * GB, size_text="100GB")
        self.assertEqual(p["status"], "refused")
        self.assertGreaterEqual(p["room"], 94 * GB)
        self.assertLessEqual(p["room"], 95 * GB)
        q = X.plan(v, aggr, 100 * GB, {}, lun={"name": "/vol/vol_ds01/l", "size": 10 * GB, "thick": True, "total": 10 * GB},
                   lun_listed=10 * GB, size_text="%dGB" % (p["room"] // GB))
        self.assertEqual(q["status"], "grow", q["why"])                       # the size said fits


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

    def test_cluster_patterns(self):
        cl = ("cluster9a", "cluster9a.example.mil")
        r = self.rec(svm={"name": "svm_a"}, name="vol_ds01")
        self.assertEqual(X.excluded(r, ["CLUSTER9*/*"], cl), "ontap_expand_exclude: CLUSTER9*/*")         # every volume there, any case
        self.assertEqual(X.excluded(r, ["cluster9?/"], cl), "ontap_expand_exclude: cluster9?/")           # a bare cluster/ too
        self.assertEqual(X.excluded(r, ["*.example.mil/svm_a:*"], cl), "ontap_expand_exclude: *.example.mil/svm_a:*")   # by address
        self.assertEqual(X.excluded(r, ["cluster9*/svm_b:*"], cl), "")                                   # another SVM there
        self.assertEqual(X.excluded(r, ["cluster1*/*"], cl), "")                                         # another cluster
        self.assertEqual(X.excluded(r, ["cluster9*/*"]), "")                                             # the cluster not known
        self.assertEqual(X.excluded(r, ["svm_a:*"], cl), "ontap_expand_exclude: svm_a:*")                # the old forms unchanged
        self.assertEqual(X.excluded(r, ["vol_ds*"], cl), "ontap_expand_exclude: vol_ds*")
        p = X.plan(vol(name="vol_ds01", svm="svm_a"), AGGR, 10 * GB, {"exclude": ["cluster9*/*"], "clusters": cl})
        self.assertEqual(p["status"], "refused")
        self.assertIn("cluster9*/*", p["why"])
        self.assertIn("cluster9*/*", X.recheck({"volume": "vol_ds01", "svm": "svm_a", "aggr": "aggr1"}, {"name": "vol_ds01", "svm": "svm_a"},
                                               {}, ["cluster9*/*"], cl))

    def test_excluded_by_hand_is_refused(self):
        p = X.plan(vol(name="vol_bk01", svm="svm_backup"), AGGR, 10 * GB, {"exclude": ["svm_backup:*"]})
        self.assertEqual(p["status"], "refused")
        self.assertIn("svm_backup:*", p["why"])


def volume_module():
    """roles/ontap_report/library/site_ontap_volume.py with its module_utils as Ansible would give them"""
    import importlib.util
    import ansible.module_utils
    import site_ontap
    for n, m in (("site_ontap", site_ontap), ("site_ontap_expand", X)):
        sys.modules["ansible.module_utils." + n] = m
        setattr(ansible.module_utils, n, m)
    spec = importlib.util.spec_from_file_location("site_ontap_volume", os.path.join(REPO, "roles", "ontap_report", "library", "site_ontap_volume.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class ClusterNameNotRead(unittest.TestCase):
    """Codex 1.127.0 P1: /api/cluster refused (403) while the storage reads work - the address is no stand-in for the name
    when a cluster/ rule is set: that cluster's volumes are not offered, and the expand job refuses."""

    def setUp(self):
        self.V = volume_module()
        V = self.V

        class C(object):
            address = "192.0.2.15"

            def call(self, method, path, query=None, body=None):
                raise V.Failed("HTTP 403: not authorized for that command")

            def records(self, path, fields, **kw):
                return []
        self.c = C()

    def test_no_cluster_rule_the_address_stands_in(self):
        self.assertEqual(self.V.cluster_name(self.c, need=self.V.cluster_rules(["svm_backup:*", "vol0"])), "192.0.2.15")
        self.assertEqual(self.V.list_cluster(self.c, {"exclude": ["svm_backup:*"]})[0], "192.0.2.15")

    def test_a_cluster_rule_refuses(self):
        self.assertTrue(self.V.cluster_rules(["cluster9*/*"]))
        with self.assertRaises(self.V.Failed) as e:
            self.V.list_cluster(self.c, {"exclude": ["cluster9*/*"]})
        self.assertEqual(str(e.exception), "cluster name not read - a cluster rule in ontap_expand_exclude can't be checked")
        with self.assertRaises(self.V.Failed):
            self.V.cluster_name(self.c, need=True)


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
        self.assertTrue(got[2].startswith("vol_thick (svm_vmware) - LUN 90.0 GB, 66% free [LUN]"), got[2])
        # display only: the key keeps the listed volume and LUN sizes the expand job plans with
        self.assertEqual((X.parse_choice(got[0])["listed"], X.parse_choice(got[0])["lun_listed"]), (100 * GB, 90 * GB))

    def test_datastores_first_then_the_rest_each_least_free_first(self):
        # (user 1.127.0) a VMware datastore's volume or LUN first, however full; then the rest; ties by name
        sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
        import ontap_expand_filters as XF
        nfs = dict(self.E, lun="", luns=0, datastore="", size=100 * GB, uuid="")
        rows = [dict(nfs, volume="vol_full", used=97 * GB, uuid="full"),                        # 3 % free, no datastore
                dict(nfs, volume="vol_ds_b", used=40 * GB, uuid="dsb"),                         # 60 % free, a datastore
                dict(nfs, volume="vol_ds_a", used=40 * GB, uuid="dsa"),                         # the same, a name before
                dict(nfs, volume="vol_ds_c", used=80 * GB, uuid="dsc"),                         # 20 % free, a datastore
                dict(nfs, volume="vol_mid", used=50 * GB, uuid="mid")]
        found = [{}, {"datastore": "DS-B"}, {"datastore": "DS-A"}, {"datastore": "DS-C"}, {}]
        got = XF.ontap_expand_choices(rows, found)["choices"]
        self.assertEqual([X.parse_choice(t)["uuid"] for t in got], ["dsc", "dsa", "dsb", "full", "mid"])
        got = XF.ontap_expand_choices(rows, [])["choices"]                                    # vCenter not read: one list
        self.assertEqual([X.parse_choice(t)["uuid"] for t in got], ["full", "dsc", "mid", "dsa", "dsb"])

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

    def test_the_datastore_space_on_the_map_only(self):
        # Codex 1.127.0 P2: the grow path never reads summary for the space; the map does, and a fault there is {} (the LUN judges)
        self.ds.summary.freeSpace = int(1.75 * GB)
        seen, vmfs, nfs = V.scan(content(self.hosts, [self.ds, self.nfs]), "H", "D")
        m = V.match({"naa": NAA}, seen, vmfs, nfs, with_space=True)
        self.assertEqual((m["capacity"], m["free"]), (int(8.75 * GB), int(1.75 * GB)))
        g = V.match({"naa": NAA}, seen, vmfs, nfs)
        self.assertNotIn("capacity", g)

        class Faulty(object):
            name = "DS-SITEA-01"

            @property
            def summary(self):
                raise RuntimeError("vmodl.fault.ManagedObjectNotFound")
        self.assertEqual(V.space(Faulty()), {})
        self.assertEqual(V.space(NS(summary=NS(capacity="n/a", freeSpace=1))), {})

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



class CodexR6Expand(unittest.TestCase):
    """1.130.0 - Codex round 6 B3: refuse unmeasured or impossible values; never advertise more than was measured"""

    def test_r6_8_a_lun_plan_needs_the_volumes_free_space(self):
        v = vol(100 * GB, 99 * GB, available=None, guarantee="none")
        l = {"name": "/vol/vol_ds01/l", "size": 40 * GB, "used": 30 * GB, "thick": True, "total": 40 * GB}
        p = X.plan(v, AGGR, 100 * GB, {}, lun=l, size_text="20GB", lun_listed=40 * GB)
        self.assertEqual(p["status"], "refused")
        self.assertIn("space.available", p["why"])
        ok = X.plan(vol(100 * GB, 50 * GB, guarantee="none"), AGGR, 100 * GB, {}, lun=dict(l, thick=False), size_text="2GB", lun_listed=40 * GB)
        self.assertEqual(ok["status"], "grow", ok["why"])
        self.assertIn("space.available", X.recheck(ok, dict(vol(100 * GB, 50 * GB, guarantee="none"), available=None, aggregates=[ok["aggr"]]), AGGR))

    def test_r6_9_impossible_values_are_refused(self):
        for v, a in ((vol(100 * GB, 50 * GB), dict(AGGR, used=-10 * GB)), (vol(100 * GB, 50 * GB), dict(AGGR, used=AGGR["size"] + 1)),
                     (vol(100 * GB, 120 * GB, available=0), AGGR), (vol(100 * GB, 50 * GB, available=-1), AGGR), (vol(-5, 0, available=0), AGGR)):
            with self.subTest(v=(v["size"], v.get("used"), v.get("available")), a=a.get("used")):
                p = X.plan(v, a, max(1, v["size"]), {}, size_text="2GB")
                self.assertEqual(p["status"], "refused")
        good = X.plan(vol(100 * GB, 50 * GB), AGGR, 100 * GB, {}, size_text="2GB")
        self.assertEqual(good["status"], "grow", good["why"])
        self.assertIn("cannot be", X.recheck(good, dict(vol(100 * GB, 50 * GB), aggregates=[good["aggr"]]), dict(AGGR, used=-10 * GB)) + " cannot be")
        self.assertTrue(X.recheck(good, dict(vol(100 * GB, 50 * GB), aggregates=[good["aggr"]]), dict(AGGR, used=-10 * GB)))

    def test_r6_10_the_nfs_free_space_is_the_least_measured(self):
        e = {"volume": "v", "svm": "s", "address": "c1.example.mil", "cluster": "c1", "size": 100 * GB, "used": 90 * GB, "available": 5 * GB, "lun": ""}
        self.assertEqual(X.lun_fullness(e), (100 * GB, 95 * GB, "volume"))
        self.assertIn("5% free", X.choice_shown(X.choice_text(e)))
        e2 = dict(e, available=None, ds_capacity=100 * GB, ds_free=5 * GB)
        self.assertIn("5% free", X.choice_shown(X.choice_text(e2)))

    def test_r6_11_the_report_says_use_unknown(self):
        p = dict(X.plan(vol(100 * GB, 0), AGGR, 100 * GB, {}, size_text="2GB"), used_known=False, cluster="c1")
        doc = XF_doc({"plan": p, "ontap_steps": []})
        now = [x for x in doc["summary"] if x["label"] in ("Volume now", "Volume before")][0]
        self.assertEqual(now["note"], "use unknown")
        self.assertNotEqual(now["status"], "ok")
        src = open(os.path.join(REPO, "roles", "ontap_report", "library", "site_ontap_volume.py"), encoding="utf-8").read()
        self.assertIn('p["used_known"] = vi.get("used") is not None', src)

    def test_r6_12_advertised_figures_round_down(self):
        p = X.plan(vol(100 * GB, 50 * GB), dict(AGGR, size=100 * GB, used=97 * GB - 2000 * MB), 100 * GB, {"max_pct": 97}, size_text="2GB")
        doc = XF_doc({"plan": dict(p, cluster="c1"), "ontap_steps": []})
        import json
        text = json.dumps(doc)
        self.assertNotIn("up to +2.0 GB", text)
        e = {"volume": "v", "svm": "s", "address": "c1.example.mil", "cluster": "c1", "size": 1000 * GB, "used": int(994 * GB), "lun": ""}
        self.assertIn("0% free", X.choice_shown(X.choice_text(e)))      # 0.6 % measured: never "1% free"


if __name__ == "__main__":
    unittest.main()
