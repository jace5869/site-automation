"""Unit tests for plugins/filter/site_filters.py (standard library only: python3 tests/test_filters.py)."""
import datetime
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import site_filters as f  # noqa: E402
import vmware_alarm_filters as va  # noqa: E402
import esxi_filters as ef  # noqa: E402
import vmware_health_filters as vh  # noqa: E402
import vmware_cert_filters as vcf  # noqa: E402


class FromCsv(unittest.TestCase):
    def test_rows_headers_bom_blank(self):
        text = "﻿POAM ID , Status\n1, Ongoing\n,\n2,\"Completed\"\n"
        self.assertEqual(f.from_csv(text), [{"POAM ID": "1", "Status": "Ongoing"},
                                            {"POAM ID": "2", "Status": "Completed"}])

    def test_quoted_commas(self):
        rows = f.from_csv('ID,Vulnerability IDs\n7,"V-1, V-2"\n')
        self.assertEqual(rows[0]["Vulnerability IDs"], "V-1, V-2")

    def test_empty(self):
        self.assertEqual(f.from_csv(""), [])
        self.assertEqual(f.from_csv(None), [])


class DaysUntil(unittest.TestCase):
    def test_formats(self):
        today = datetime.date.today()
        fmts = ["%Y-%m-%d", "%m/%d/%Y"]
        self.assertEqual(f.days_until((today + datetime.timedelta(days=3)).isoformat(), fmts), 3)
        self.assertEqual(f.days_until((today - datetime.timedelta(days=2)).strftime("%m/%d/%Y"), fmts), -2)

    def test_not_a_date(self):
        self.assertIsNone(f.days_until("TBD", ["%Y-%m-%d"]))
        self.assertIsNone(f.days_until("", ["%Y-%m-%d"]))



class SiteResult(unittest.TestCase):
    def test_last_marker_line_wins(self):
        lines = ["noise", '###SITE-JSON### {"a": 1}', "more", '###SITE-JSON### {"a": 2}']
        self.assertEqual(f.site_result(lines), {"a": 2})

    def test_string_and_defaults(self):
        self.assertEqual(f.site_result('x\n###SITE-JSON### {"b": [1]}\n'), {"b": [1]})
        self.assertEqual(f.site_result([]), {})
        self.assertEqual(f.site_result(["###SITE-JSON### null"], {"x": 0}), {"x": 0})
        self.assertEqual(f.site_result(["###SITE-JSON### {not json"], {"x": 0}), {"x": 0})
        self.assertEqual(f.site_result(None), {})



# ---- podman discovery ------------------------------------------------------------------------
NOW = 1790000000


def ps_entry(name, state="running", exit_code=0, exited_at=-62135596800, labels=None, cid=None, status="", infra=False):
    cid = cid or (name.encode().hex() * 8)[:64]
    return {"Id": cid, "Names": [name], "Image": "registry.example.com/%s:1" % name, "State": state,
            "ExitCode": exit_code, "ExitedAt": exited_at, "Labels": labels, "Status": status, "IsInfra": infra}


def inspect_entry(p, podman=4, health="", oom=False, policy="", restarts=0):
    state = {"Status": p["State"], "Running": p["State"] == "running", "OOMKilled": oom, "ExitCode": p["ExitCode"]}
    if health:
        state["Health" if podman >= 4 else "Healthcheck"] = {"Status": health, "FailingStreak": 1}
    return {"Id": p["Id"], "Name": p["Names"][0], "State": state, "RestartCount": restarts,
            "HostConfig": {"RestartPolicy": {"Name": policy, "MaximumRetryCount": 0}}}


def owner_output(ps, inspect=None, quadlets=None, unitfiles=None, show=None, ps_rc=0, show_rc=0):
    out = ["", "@@@ ps %d" % ps_rc]
    out += json.dumps(ps, indent=4).splitlines() if ps_rc == 0 else ["Error: cannot connect to podman"]
    if ps_rc != 0:
        return "\n".join(out)
    out.append("@@@ list 0")
    for p in ps:
        lab = (p.get("Labels") or {}).get("PODMAN_SYSTEMD_UNIT", "")
        out.append("%s|%s|%s" % (p["Id"], p["Names"][0], lab))
    if ps:
        out += ["@@@ inspect 0"] + json.dumps(inspect or [], indent=4).splitlines()
    for path, lines in (quadlets or {}).items():
        out += ["@@@ quadlet " + path] + lines
    for path in unitfiles or []:
        out.append("@@@ unitfile " + path)
    if show is not None:
        out.append("@@@ show %d" % show_rc)
        for unit, props in show.items():
            out += ["Id=" + unit] + ["%s=%s" % kv for kv in props.items()] + [""]
    out.append("@@@ end")
    return "\n".join(out)


def result(owner, uid, stdout):
    return {"_pd_owner": {"name": owner, "uid": uid, "home": "/home/" + owner, "linger": 1},
            "ansible_loop_var": "_pd_owner", "stdout": stdout, "rc": 0}


def by_key(containers):
    return dict((c["key"], c) for c in containers)


class PodmanDiscovery(unittest.TestCase):
    def test_rootful_plain_restart_policy_and_exit0(self):
        a = ps_entry("db")
        b = ps_entry("job", state="exited", exit_code=0, exited_at=NOW - 60)
        out = owner_output([a, b], [inspect_entry(a, policy="always"), inspect_entry(b)], show={})
        got = by_key(f.podman_discovery([result("root", 0, out)])["containers"])
        self.assertTrue(got["root/db"]["should_run"])
        self.assertIn("restart policy is always", got["root/db"]["why"])
        self.assertEqual(got["root/db"]["run_as"], "")
        self.assertFalse(got["root/job"]["should_run"])            # exit 0, no unit, no policy: on purpose
        self.assertEqual(got["root/job"]["exited_at"], NOW - 60)
        self.assertEqual(got["root/db"]["exited_at"], 0)             # Go zero time -> never exited

    def test_same_name_three_owners(self):
        res = []
        for owner, uid in (("root", 0), ("alice", 1001), ("bob", 1002)):
            p = ps_entry("nginx", cid=("%d" % uid) * 64)
            res.append(result(owner, uid, owner_output([p], [inspect_entry(p, policy="always")], show={})))
        got = f.podman_discovery(res)["containers"]
        self.assertEqual(sorted(c["key"] for c in got), ["alice/nginx", "bob/nginx", "root/nginx"])
        alice = by_key(got)["alice/nginx"]
        self.assertEqual(alice["run_as"], "runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 ")
        self.assertEqual(alice["uid"], 1001)

    def test_health_podman3_and_podman4(self):
        for podman in (3, 4):
            p = ps_entry("web")
            out = owner_output([p], [inspect_entry(p, podman=podman, health="unhealthy")], show={})
            got = f.podman_discovery([result("root", 0, out)])["containers"][0]
            self.assertEqual(got["health"], "unhealthy", "podman %d" % podman)

    def test_health_from_ps_status_when_inspect_missing(self):
        p = ps_entry("web", status="Up 2 hours (healthy)")
        out = owner_output([p], [], show={})
        self.assertEqual(f.podman_discovery([result("root", 0, out)])["containers"][0]["health"], "healthy")

    def test_oom_restarts_infra(self):
        p = ps_entry("big", state="exited", exit_code=137, exited_at=NOW - 30)
        infra = ps_entry("abc-infra", infra=True)
        out = owner_output([p, infra], [inspect_entry(p, oom=True, restarts=7)], show={})
        got = f.podman_discovery([result("root", 0, out)])["containers"]
        self.assertEqual([c["name"] for c in got], ["big"])            # a pod's infra container is left out
        self.assertTrue(got[0]["oom_killed"])
        self.assertEqual(got[0]["restart_count"], 7)

    def test_quadlet_running_and_missing(self):
        run = ps_entry("systemd-api", labels={"PODMAN_SYSTEMD_UNIT": "api.service"})
        quads = {"/home/alice/.config/containers/systemd/api.container":
                 ["[Container]", "Image=registry.example.com/api:1", "[Install]", "WantedBy=default.target"],
                 "/home/alice/.config/containers/systemd/worker.container":
                 ["[Container]", "ContainerName=worker", "Image=registry.example.com/worker:1", "[Install]", "WantedBy=default.target"],
                 "/home/alice/.config/containers/systemd/manual.container": ["[Container]", "Image=x"]}
        show = {"api.service": {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "UnitFileState": "generated"},
                "worker.service": {"LoadState": "loaded", "ActiveState": "failed", "SubState": "failed", "Result": "exit-code",
                                   "UnitFileState": "generated", "NRestarts": "5"},
                "manual.service": {"LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead", "UnitFileState": "generated"}}
        out = owner_output([run], [inspect_entry(run)], quadlets=quads, show=show)
        got = by_key(f.podman_discovery([result("alice", 1001, out)])["containers"])
        self.assertEqual(got["alice/systemd-api"]["how"], "quadlet")
        self.assertTrue(got["alice/systemd-api"]["should_run"])
        w = got["alice/worker"]                                         # Quadlet: its container is gone while the unit is down
        self.assertEqual((w["state"], w["running"], w["unit"], w["unit_scope"]), ("missing", False, "worker.service", "user"))
        self.assertTrue(w["should_run"])
        self.assertIn("[Install] WantedBy=default.target", w["why"])
        self.assertEqual((w["unit_active"], w["unit_result"], w["unit_restarts"]), ("failed", "exit-code", 5))
        m = got["alice/systemd-manual"]
        self.assertFalse(m["should_run"])
        self.assertIn("no [Install] section", m["why"])

    def test_quadlet_not_loaded_still_should_run(self):
        quads = {"/etc/containers/systemd/db.container": ["[Container]", "Image=x", "[Install]", "WantedBy=multi-user.target"]}
        out = owner_output([], quadlets=quads, show={"db.service": {"LoadState": "not-found", "ActiveState": "inactive"}})
        c = f.podman_discovery([result("root", 0, out)])["containers"][0]
        self.assertTrue(c["should_run"])
        self.assertIn("daemon-reload", c["why"])

    def test_generated_units(self):
        # podman generate systemd (no --new): the container stays; unit container-NAME.service, enabled
        keep = ps_entry("app", state="exited", exit_code=0, exited_at=NOW - 100)
        # podman generate systemd --new: a unit file whose container is gone
        show = {"app.service": {"LoadState": "not-found"},
                "container-app.service": {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "enabled",
                                          "ExecStart": "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman start app ; }"},
                "myweb.service": {"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "disabled",
                                  "WantedBy": "multi-user.target",
                                  "ExecStart": "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman run --cidfile=/run/x --rm -d --name web2 img ; }"}}
        out = owner_output([keep], [inspect_entry(keep)], unitfiles=["/etc/systemd/system/myweb.service"], show=show)
        got = by_key(f.podman_discovery([result("root", 0, out)])["containers"])
        self.assertEqual((got["root/app"]["unit"], got["root/app"]["how"]), ("container-app.service", "unit-name"))
        self.assertTrue(got["root/app"]["should_run"])
        self.assertIn("is enabled", got["root/app"]["why"])
        w = got["root/web2"]
        self.assertEqual((w["state"], w["unit"], w["how"]), ("missing", "myweb.service", "unit-file"))
        self.assertTrue(w["should_run"])                                  # wanted by multi-user.target
        self.assertIn("wanted by multi-user.target", w["why"])

    def test_kube_quadlet_and_units_without_a_named_container(self):
        ex = "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman %s ; }"
        api = ps_entry("api-pod-api", labels={"PODMAN_SYSTEMD_UNIT": "api.service"})
        stray = ps_entry("shop", state="exited", exit_code=0, exited_at=NOW - 60)     # not the .kube's
        quads = {"/etc/containers/systemd/shop.kube": ["[Kube]", "Yaml=/etc/containers/shop.yaml", "[Install]", "WantedBy=multi-user.target"],
                 "/etc/containers/systemd/api.kube": ["[Kube]", "Yaml=/etc/containers/api.yaml", "[Install]", "WantedBy=multi-user.target"]}
        show = {"shop.service": {"LoadState": "loaded", "ActiveState": "failed", "Result": "exit-code", "UnitFileState": "generated"},
                "api.service": {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "generated"},
                "podman-kube@-srv-blog.yaml.service": {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "enabled",
                                                       "ExecStart": ex % "kube play --replace /srv/blog.yaml"},
                "stack.service": {"LoadState": "loaded", "ActiveState": "inactive", "Type": "oneshot", "Result": "success",
                                  "InactiveExitTimestampMonotonic": "5", "UnitFileState": "enabled",
                                  "ExecStart": "{ path=/usr/bin/podman-compose ; argv[]=/usr/bin/podman-compose up -d ; }"},
                "never.service": {"LoadState": "loaded", "ActiveState": "inactive", "Type": "oneshot", "Result": "success",
                                  "InactiveExitTimestampMonotonic": "0", "UnitFileState": "enabled", "ExecStart": ex % "compose up -d"},
                "mypod.service": {"LoadState": "loaded", "ActiveState": "active", "UnitFileState": "enabled", "ExecStart": ex % "pod start mypod"},
                "anon.service": {"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "enabled", "ExecStart": ex % "run --rm img"},
                "restart.service": {"LoadState": "loaded", "ActiveState": "inactive", "UnitFileState": "enabled",
                                    "ExecStart": ex % "start --all --filter restart-policy=always"},
                "upd.service": {"LoadState": "loaded", "ActiveState": "failed", "UnitFileState": "static", "ExecStart": ex % "auto-update"}}
        units = ["/etc/systemd/system/multi-user.target.wants/podman-kube@-srv-blog.yaml.service"] + [
            "/etc/systemd/system/%s.service" % u for u in ("stack", "never", "mypod", "anon", "restart", "upd")]
        out = owner_output([api, stray], [inspect_entry(api), inspect_entry(stray)], quadlets=quads, unitfiles=units, show=show)
        got = by_key(f.podman_discovery([result("root", 0, out)])["containers"])
        self.assertEqual(sorted(got), ["root/anon", "root/api-pod-api", "root/mypod", "root/never",
                                       "root/podman-kube@-srv-blog.yaml", "root/shop", "root/shop.service", "root/stack"])
        a = got["root/api-pod-api"]                         # the running .kube's container: its unit and [Install]
        self.assertEqual((a["unit"], a["how"], a["kind"], a["should_run"]), ("api.service", "quadlet", "container", True))
        self.assertEqual((got["root/shop"]["kind"], got["root/shop"]["unit"]), ("container", ""))   # the stray one
        shop = got["root/shop.service"]
        self.assertEqual((shop["kind"], shop["what"], shop["state"], shop["running"], shop["should_run"]),
                         ("kube", "pod (podman kube play)", "failed", False, True))
        self.assertEqual(shop["image"], "/etc/containers/shop.yaml")
        blog = got["root/podman-kube@-srv-blog.yaml"]
        self.assertEqual((blog["kind"], blog["running"], blog["should_run"]), ("kube", False, True))
        self.assertEqual((got["root/stack"]["kind"], got["root/stack"]["state"], got["root/stack"]["running"]), ("compose", "finished", True))
        self.assertEqual((got["root/never"]["state"], got["root/never"]["running"]), ("inactive", False))   # oneshot never started
        self.assertEqual((got["root/mypod"]["kind"], got["root/mypod"]["running"]), ("pod", True))
        self.assertEqual(got["root/anon"]["kind"], "unit")

    def test_exec_kind_and_name(self):
        ex = "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman %s ; }"
        self.assertEqual(f._exec_name(ex % "start --all --filter restart-policy=always"), "")
        self.assertEqual(f._exec_name(ex % "container run -d --name=web img"), "web")
        for args, kind in (("kube play x.yaml", "kube"), ("play kube x.yaml", "kube"), ("pod start p", "pod"), ("compose up", "compose"),
                           ("run --rm img", "unit"), ("auto-update", ""), ("system service", ""), ("start -a", ""), ("pull img", "")):
            self.assertEqual(f._exec_kind(ex % args), kind, args)
        self.assertEqual(f._exec_kind("{ path=/usr/local/bin/docker-compose ; argv[]=/usr/local/bin/docker-compose up ; }"), "compose")
        self.assertEqual(f._exec_kind("{ path=/usr/bin/sleep ; argv[]=/usr/bin/sleep 1 ; }"), "")

    def test_service_watch_leaves_out_what_is_not_a_container(self):
        disc_ = [dict(disc("root", 0, "shop", "shop.service"), kind="kube"), disc("root", 0, "web", "web.service")]
        self.assertEqual([t["key"] for t in f.watch_targets([], disc_)], ["root/web"])

    def test_ignore_full_match_name_or_owner(self):
        res = []
        for owner, uid in (("root", 0), ("alice", 1001)):
            ps = [ps_entry("web", cid=("%d1" % uid) * 32), ps_entry("webapp", cid=("%d2" % uid) * 32)]
            res.append(result(owner, uid, owner_output(ps, [], show={})))
        got = by_key(f.podman_discovery(res, ignore=["web", "alice/.*"])["containers"])
        self.assertTrue(got["root/web"]["ignored"])
        self.assertFalse(got["root/webapp"]["ignored"])                  # whole-name match only
        self.assertTrue(got["alice/webapp"]["ignored"])

    def test_errors(self):
        bad = result("alice", 1001, owner_output([], ps_rc=125))
        show_bad = result("bob", 1002, owner_output([], show={}, show_rc=1))
        nothing = {"_pd_owner": {"name": "carol", "uid": 1003}, "ansible_loop_var": "_pd_owner", "stdout": "",
                   "stderr": "runuser: user carol does not exist", "rc": 1}
        out = f.podman_discovery([bad, show_bad, nothing, {"skipped": True}])
        self.assertEqual(out["containers"], [])
        whats = [(e["owner"], e["what"]) for e in out["errors"]]
        self.assertEqual(whats, [("alice", "podman ps"), ("bob", "systemctl show"), ("carol", "the discovery script")])
        self.assertIn("runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 podman ps -a", out["errors"][0]["command"])

    def test_empty_ps_null_and_inspect_race(self):
        out = "\n@@@ ps 0\nnull\n@@@ end\n"
        self.assertEqual(f.podman_discovery([result("root", 0, out)]), {"containers": [], "errors": []})
        p = ps_entry("gone")
        text = owner_output([p], [], show={}).replace("@@@ inspect 0", "@@@ inspect 125")
        self.assertEqual(f.podman_discovery([result("root", 0, text)])["containers"][0]["name"], "gone")


# ---- service watch -----------------------------------------------------------------------------
def disc(owner, uid, name, unit="", should_run=True, how="", quadlet=""):
    return {"owner": owner, "uid": uid, "key": owner + "/" + name, "name": name, "unit": unit, "how": how,
            "quadlet_file": quadlet, "should_run": should_run, "ignored": False}


def watched(owner, uid, name, unit="", container=None):
    return {"key": owner + "/" + name, "owner": owner, "uid": uid, "name": name, "container": container or name,
            "unit": unit, "label": name}


ALICE = "runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 "
BOB = "runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002 "


class WatchTargets(unittest.TestCase):
    def test_static_first_then_discovered_and_dedupe(self):
        found = [disc("root", 0, "systemd-stigman", "stigman.service", how="quadlet"),
                 disc("root", 0, "nginx"), disc("alice", 1001, "nginx", "nginx.service"),
                 disc("bob", 1002, "nginx"), disc("root", 0, "tmp", should_run=False)]
        got = f.watch_targets([{"name": "stigman", "url": "https://127.0.0.1/"}, {"name": "nginx"}], found)
        self.assertEqual([t["key"] for t in got], ["root/stigman", "root/nginx", "alice/nginx", "bob/nginx"])
        self.assertEqual((got[0]["container"], got[0]["unit"], got[0]["url"]), ("systemd-stigman", "stigman.service", "https://127.0.0.1/"))
        self.assertEqual(got[2]["uid"], 1001)

    def test_static_user_entry_and_discovery_off(self):
        found = [disc("alice", 1001, "web", "web.service")]
        got = f.watch_targets([{"name": "web", "user": "alice"}, {"name": "web"}], found, False)
        self.assertEqual([(t["key"], t["unit"], t["discovered"]) for t in got],
                         [("alice/web", "web.service", True), ("root/web", "", False)])
        self.assertEqual(f.watch_targets([], found, False), [])


    def test_skipped_user_with_boot_units_is_watched_as_a_whole(self):
        skipped = [{"user": "carol", "uid": 1003, "wants": 2, "ignored": False},
                   {"user": "dave", "uid": 1004, "wants": 0, "ignored": False},      # storage only: not watched
                   {"user": "erin", "uid": 1005, "wants": 1, "ignored": True},       # podman_discover_ignore: erin/.*
                   {"user": "ghost", "uid": -1, "wants": 0, "ignored": False}]
        got = f.watch_targets([], [], True, skipped)
        self.assertEqual([(t["key"], t.get("whole_user"), t["wants"]) for t in got], [("carol/*", True, 2)])
        self.assertEqual(f.watch_allow_patterns(got), [])                       # nothing to start there
        self.assertEqual(f.watch_targets([], [], False, skipped), [])            # discovery off: not added
        # a static entry of that user already covers them
        self.assertEqual([t["key"] for t in f.watch_targets([{"name": "web", "user": "carol"}], [], True, skipped)], ["carol/web"])


class WatchFixCommands(unittest.TestCase):
    W = [watched("root", 0, "db", "db.service"), watched("root", 0, "nginx"),
         watched("alice", 1001, "nginx", "nginx.service"), watched("bob", 1002, "nginx")]

    def plan(self, proposals, standard=None, known=None):
        return f.watch_fix_commands(proposals, self.W, standard or {}, known)

    def test_rootful_podman_start_of_unit_container_becomes_systemctl(self):
        self.assertEqual(self.plan(["podman start db"])["commands"], ["systemctl start db.service"])
        self.assertEqual(self.plan(["sudo podman container restart db nginx"])["commands"],
                         ["systemctl restart db.service", "podman restart nginx"])

    def test_owner_from_runuser_sudo_and_machine(self):
        self.assertEqual(self.plan([ALICE + "podman start nginx"])["commands"], [ALICE + "systemctl --user start nginx.service"])
        self.assertEqual(self.plan(["sudo -u alice XDG_RUNTIME_DIR=/run/user/1001 podman start nginx"])["commands"],
                         [ALICE + "systemctl --user start nginx.service"])
        self.assertEqual(self.plan(["systemctl --user -M alice@ start nginx.service"])["commands"],
                         [ALICE + "systemctl --user start nginx.service"])
        self.assertEqual(self.plan(["sudo -u bob podman start nginx"])["commands"], [BOB + "podman start nginx"])

    def test_same_name_root_wins_when_owner_not_said(self):
        self.assertEqual(self.plan(["podman start nginx"])["commands"], ["podman start nginx"])

    def test_owner_left_out_maps_to_the_only_user_that_has_it(self):
        w = [watched("alice", 1001, "api", "api.service")]
        got = f.watch_fix_commands(["podman start api"], w, {}, [])
        self.assertEqual(got["commands"], [ALICE + "systemctl --user start api.service"])
        # ...but not when root has a container of that name (watched or not)
        got = f.watch_fix_commands(["podman start api"], w, {}, [{"owner": "root", "name": "api", "unit": ""}])
        self.assertEqual(got["commands"], ["podman start api"])

    def test_root_start_of_a_container_root_does_not_have_is_dropped(self):
        known = [{"owner": "alice", "name": "nginx", "unit": "nginx.service"}, {"owner": "bob", "name": "nginx", "unit": ""}]
        std = {"alice/nginx": [ALICE + "systemctl --user start nginx.service"], "bob/nginx": [BOB + "podman start nginx"]}
        w = [e for e in self.W if e["owner"] != "root"]
        got = f.watch_fix_commands(["podman start nginx"], w, std, known)   # which user's? root has none: it could only fail
        self.assertEqual(got["commands"], [ALICE + "systemctl --user start nginx.service", BOB + "podman start nginx"])
        self.assertEqual(got["dropped"], ["podman start nginx (root has no container nginx)"])
        # without discovery (nothing known) it is kept
        self.assertEqual(f.watch_fix_commands(["podman start nginx"], w, {}, [])["commands"], ["podman start nginx"])

    def test_podman_run_of_unit_container(self):
        self.assertEqual(self.plan([ALICE + "podman run -d --name nginx img"])["commands"], [ALICE + "systemctl --user start nginx.service"])

    def test_standard_fix_added_per_owner_and_ordered(self):
        std = {"root/db": ["systemctl start db.service"], "alice/nginx": [ALICE + "systemctl --user start nginx.service"],
               "bob/nginx": [BOB + "podman start nginx"]}
        got = self.plan(["systemctl start nginx.service"], std)       # root has no unit nginx: covers nothing watched
        self.assertEqual(got["added"], ["root/db", "alice/nginx", "bob/nginx"])
        got = self.plan([BOB + "podman start nginx", "systemctl daemon-reload", "podman start db"], std)
        self.assertEqual(got["commands"], ["systemctl daemon-reload", "systemctl start db.service",
                                           ALICE + "systemctl --user start nginx.service", BOB + "podman start nginx"])
        self.assertEqual(got["added"], ["alice/nginx"])

    def test_root_proposal_does_not_cover_a_user_container(self):
        std = {"alice/nginx": [ALICE + "systemctl --user start nginx.service"]}
        got = self.plan(["podman rm -f nginx"], std)
        self.assertEqual(got["added"], ["alice/nginx"])

    def test_unknown_user_command_is_kept(self):
        self.assertEqual(self.plan(["sudo -u carol podman start x"])["commands"], ["sudo -u carol podman start x"])


class WatchAllowPatterns(unittest.TestCase):
    CHARS = re.compile(r"\A[A-Za-z0-9 _./:=@%+,-]+\Z")          # what ACT pre-approves at all

    def test_patterns_match_exactly_the_right_commands(self):
        w = [watched("root", 0, "db", "db.service"), watched("root", 0, "cache"),
             watched("alice", 1001, "nginx", "nginx.service"), watched("bob", 1002, "nginx")]
        pats = [re.compile(r"\A(?:" + p + r")\Z") for p in f.watch_allow_patterns(w)]
        ok = ["systemctl start db.service", "systemctl restart db", "podman start cache",
              ALICE + "systemctl --user start nginx.service", ALICE + "systemctl --user reset-failed nginx.service",
              BOB + "podman restart nginx"]
        bad = ["podman start nginx", "systemctl start nginx.service", ALICE + "podman start nginx",
               BOB + "systemctl --user start nginx.service", "runuser -u alice -- podman start nginx",
               ALICE + "systemctl --user start nginx.service; reboot", ALICE + "systemctl --user stop nginx.service"]
        for c in ok:
            self.assertTrue(self.CHARS.match(c), c)
            self.assertTrue(any(p.match(c) for p in pats), c)
        for c in bad:
            self.assertFalse(any(p.match(c) for p in pats), c)


class ActBatches(unittest.TestCase):
    """The alarms ACT analysis asked vm_alarm_act_batch problems at a time (roles/vmware_vm/tasks/alarm_act.yml)."""

    @staticmethod
    def batch(ids, ran=True, overall="", reason="", error=""):
        by = {i: {"cause": "c" + i, "evidence": "", "fix": "", "confidence": 80} for i in ids} if ran and not error else {}
        return {"ids": ids, "ran": ran, "reason": reason, "raw": "",
                "parsed": {"parsed": ran and not error, "overall": overall, "by_id": by, "error": error}}

    def test_every_batch_answered(self):
        bs = [self.batch(["P1", "P2", "P3", "P4", "P5"], overall="Fix the power supply first."), self.batch(["P6", "P7"], overall="Then the logins.")]
        m = va.act_batches_merge(bs)
        self.assertEqual((m["parsed"], sorted(m["by_id"], key=lambda x: int(x[1:]))), (True, ["P%d" % i for i in range(1, 8)]))
        self.assertEqual(m["overall"], "P1-P5: Fix the power supply first. P6, P7: Then the logins.")
        self.assertEqual(va.act_batches_reason(bs), "")

    def test_one_batch_failed_the_others_kept(self):
        bs = [self.batch(["P1", "P2", "P3"]), self.batch(["P4", "P5", "P6"], ran=False, reason="ACT failed: the reply was cut off."),
              self.batch(["P7"], error="ACT's answer has no readable JSON block")]
        m = va.act_batches_merge(bs)
        self.assertEqual((m["parsed"], sorted(m["by_id"])), (True, ["P1", "P2", "P3"]))
        self.assertIn("P4-P6 not analyzed: ACT failed: the reply was cut off.", m["overall"])
        self.assertIn("P7: ACT's answer could not be read (ACT's answer has no readable JSON block).", m["overall"])
        self.assertEqual(va.act_batches_reason(bs), "P4-P6 not analyzed: ACT failed: the reply was cut off.")

    def test_one_batch_as_before(self):
        one = self.batch(["P1"], ran=False, reason="This job has no API key for provider genai.")
        self.assertEqual(va.act_batches_merge([one]), one["parsed"])
        self.assertEqual(va.act_batches_reason([one]), "This job has no API key for provider genai.")
        self.assertEqual(va.act_batches_reason([self.batch(["P1"], error="no JSON")]), "")    # answered: the report shows the answer


class VmFacts(unittest.TestCase):
    VM = {"name": "Web01.example.mil", "runtime": {"powerState": "poweredOn"},
          "config": {"template": False, "firmware": "efi", "annotation": "owner: ops",
                     "bootOptions": {"efiSecureBootEnabled": True},
                     "hardware": {"device": [
                         {"_vimtype": "vim.vm.device.VirtualDisk", "key": 2000},
                         {"_vimtype": "vim.vm.device.VirtualVmxnet3", "macAddress": "00:50:56:aa:bb:01",
                          "deviceInfo": {"label": "Network adapter 1", "summary": "DVSwitch: 50 2a"},
                          "backing": {"_vimtype": "vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo",
                                      "port": {"portgroupKey": "dvportgroup-12", "switchUuid": "50 2a"}}},
                         {"_vimtype": "vim.vm.device.VirtualE1000e", "macAddress": "00:50:56:aa:bb:02",
                          "deviceInfo": {"label": "Network adapter 2", "summary": "VM Network"},
                          "backing": {"_vimtype": "vim.vm.device.VirtualEthernetCard.NetworkBackingInfo",
                                      "deviceName": "VM Network", "network": "vim.Network:network-7"}}]}},
          "guest": {"hostName": "WEB01.example.mil", "ipAddress": "192.0.2.3", "toolsRunningStatus": "guestToolsRunning",
                    "net": [{"ipAddress": ["192.0.2.3", "fe80::1"]}]}}

    def test_record(self):
        r = f.vm_facts(self.VM, {"moid": "vm-42", "hw_folder": "/DC1/vm/web", "hw_esxi_host": "esx01"})
        self.assertEqual((r["moid"], r["datacenter"], r["folder"], r["esxi_host"], r["ip"]),
                         ("vm-42", "DC1", "/DC1/vm/web", "esx01", "192.0.2.3"))
        self.assertEqual(f.vm_facts(self.VM, {"hw_folder": "/Sites/East/DC2/vm"})["datacenter"], "DC2")
        self.assertIsNone(f.vm_facts(self.VM)["datacenter"])
        self.assertEqual((r["name"], r["power"], r["firmware"], r["secure_boot"], r["template"]),
                         ("Web01.example.mil", "poweredOn", "efi", True, False))
        self.assertEqual(r["ids"], ["192.0.2.3", "fe80::1", "web01"])
        self.assertEqual([(n["index"], n["adapter_type"], n["network"], n["kind"]) for n in r["nics"]],
                         [(1, "vmxnet3", "dvportgroup-12", "dvs"), (2, "e1000e", "network-7", "standard")])

    def test_empty_and_bios(self):
        r = f.vm_facts({"name": "x", "config": {"firmware": "bios", "bootOptions": {"efiSecureBootEnabled": None}}})
        self.assertEqual((r["secure_boot"], r["nics"], r["ids"], r["annotation"]), (False, [], ["x"], ""))
        self.assertEqual(f.vm_facts(None)["ids"], [])

    def test_where(self):
        r = f.vm_facts(self.VM, {"hw_folder": "/DC1/vm/web", "hw_esxi_host": "esx01"})
        self.assertEqual(f.vm_where(r), "Web01.example.mil  folder /DC1/vm/web  on esx01  guest WEB01.example.mil 192.0.2.3  (poweredOn, )".replace("(poweredOn, )", "(poweredOn, ?)"))
        self.assertEqual(f.vm_where({"name": "x"}), "x  (?, ?)")

    def test_ds_row(self):
        d = {"name": "ds1", "datacenter": "DC1", "cluster": "", "type": "VMFS", "capacity_gb": 100.0, "free_gb": 5.0,
             "used_pct": 95.0, "provisioned_pct": 120.0, "accessible": True, "maintenance": "normal", "hosts": 3, "vms": 9}
        self.assertEqual(f.vm_ds_row(d), ["ds1", "DC1", "", "VMFS", 100.0, 5.0, 95.0, 120.0, 3, 9, "ok"])
        self.assertEqual(f.vm_ds_row(dict(d, accessible=False))[-1], "inaccessible")
        self.assertEqual(f.vm_ds_row(dict(d, maintenance="inMaintenance"))[-1], "maintenance")

    def test_host_ids(self):
        hv = {"aap01.example.mil": {"ansible_host": "192.0.2.5"}, "aap02": {}}
        self.assertEqual(f.host_ids(["aap01.example.mil", "aap02"], hv), ["192.0.2.5", "aap01", "aap02"])
        self.assertEqual(f.host_ids([], hv), [])
        self.assertEqual(f.host_ids(["AAP03.example.mil"]), ["aap03"])


class ReportText(unittest.TestCase):
    def test_tables_lines_empty(self):
        r = {"title": "Secure boot", "subtitle": "141 VMs", "summary": [{"label": "VMs", "value": 141}],
             "sections": [{"title": "BIOS", "text": "Fix: convert.", "columns": ["VM", "Folder", "Power"],
                           "rows": [["DEBIAN_11_64", "/DC1/vm/A", "poweredOn"], ["x", None, "poweredOff"], ["short"]]},
                          {"title": "Unknown", "columns": ["VM"], "rows": []},
                          {"title": "Notes", "lines": ["one", " ", "two\nlines"]}],
             "footer": "vCenter v1"}
        t = f.report_text(r, "AAP job 1")
        self.assertEqual(t.splitlines(), [
            "Secure boot", "===========", "141 VMs", "", "VMs: 141", "", "",
            "BIOS (3)", "--------", "Fix: convert.", "",
            "VM            Folder     Power", "------------  ---------  ----------",
            "DEBIAN_11_64  /DC1/vm/A  poweredOn", "x" + " " * 24 + "poweredOff", "short",
            "", "", "Unknown (0)", "-----------", "None.",
            "", "", "Notes", "-----", "- one", "- two", "lines",
            "", "--", "vCenter v1", "AAP job 1"])

    def test_cells_on_one_line(self):
        t = f.report_text({"title": "T", "sections": [{"title": "S", "columns": ["A"], "rows": [["a\nb   c"]]}]})
        self.assertIn("a b c", t)


class FindingsReport(unittest.TestCase):
    W = {"check": "disk", "id": "disk:/var", "severity": "warning", "summary": "/var 86% full", "hint": "df -h /var"}
    C = {"check": "services", "id": "svc:x", "severity": "critical", "summary": "x is down", "hint": "systemctl status x"}

    def test_fleet(self):
        r = f.findings_report({"web02": [self.W], "web01": [self.W, self.C], "db01": []}, "Health check",
                              ["web09"], ["disk", "services", "disk"], [{"title": "Extra", "lines": ["e"]}])
        self.assertEqual(r["title"], "Health check: 3 finding(s) on 2 of 3 host(s), 1 host(s) did not report")
        self.assertEqual(r["status"], "critical")
        self.assertEqual([(b["label"], b["value"], b.get("status")) for b in r["summary"]],
                         [("Hosts checked", 3, None), ("Healthy", 1, "ok"), ("With findings", 2, "warning"),
                          ("Critical", 1, "critical"), ("Warning", 2, "warning"), ("Did not report", 1, "warning")])
        self.assertEqual([s["title"] for s in r["sections"]], ["Findings", "Did not report", "Extra", "Healthy (1)"])
        self.assertEqual(r["sections"][0]["rows"][0], ["web01", "CRITICAL", "services", "x is down", "systemctl status x"])
        self.assertEqual([x[0] for x in r["sections"][0]["rows"][1:]], ["web01", "web02"])
        self.assertEqual(r["sections"][3]["text"], "db01")
        self.assertEqual(r["footer"], "Checks run: disk, services")

    def test_healthy_and_single(self):
        r = f.findings_report({"a": [], "b": []}, "Health check")
        self.assertEqual((r["title"], r["status"]), ("Health check: no findings - all 2 host(s) healthy", "ok"))
        r = f.findings_report({"localhost": [self.W]}, "POA&M status")
        self.assertEqual(r["title"], "POA&M status: 1 finding(s)")
        self.assertEqual(r["sections"][0]["columns"], ["Severity", "Check", "Finding", "Look further"])
        self.assertEqual([b["label"] for b in r["summary"]], ["Critical", "Warning"])
        self.assertEqual(f.findings_report({"localhost": []}, "X")["sections"], [{"title": "Result", "lines": ["No findings."]}])


class Snapshots(unittest.TestCase):
    S = [{"vm": "web01", "moid": "vm-1", "id": 1, "name": "before-patch", "description": "", "age_days": 9.0, "size_gb": 4.0},
         {"vm": "web01", "moid": "vm-1", "id": 2, "name": "after", "description": "", "age_days": 1.5, "size_gb": 1.0},
         {"vm": "db01", "moid": "vm-2", "id": 7, "name": "baseline", "description": "KEEP for audit", "age_days": 40.0, "size_gb": None},
         {"vm": "app01", "moid": "vm-3", "id": 1, "name": "app_Keep", "description": "", "age_days": 5.0, "size_gb": 2.0},
         {"vm": "app02", "moid": "vm-4", "id": 3, "name": "x", "description": "", "age_days": 3.0, "size_gb": 2.0},
         {"vm": "DC01", "moid": "vm-5", "id": 1, "name": "pre", "description": "", "age_days": 20.0, "size_gb": 1.0},
         {"vm": "files01", "moid": "vm-6", "id": 1, "name": "big", "description": "", "age_days": 30.0, "size_gb": 1500.0},
         {"vm": "aapvm", "moid": "vm-7", "id": 1, "name": "s", "description": "", "age_days": 10.0, "size_gb": 1.0,
          "hostname": "aap01.example.mil", "ip": "192.0.2.5"},
         {"vm": "odd", "moid": "vm-8", "id": 1, "name": "s", "description": "", "age_days": 10.0, "size_gb": None}]
    RX = "(?i)keep|do.?not.?delete"

    def test_split(self):
        r = f.vm_snapshot_split(self.S, 3, self.RX, ["dc*", "vcsa01"], ["aap01", "192.0.2.5"], 1024)
        self.assertEqual([x["vm"] for x in r["all"]][:3], ["db01", "files01", "DC01"])
        self.assertEqual([(x["vm"], x["id"]) for x in r["old"]], [("web01", 1), ("app02", 3)])
        self.assertEqual({x["vm"]: x["reason"] for x in r["held"]}, {
            "db01": "its name or description says keep", "files01": "bigger than 1.00 TB: delete it by hand, at a quiet time",
            "DC01": "the VM is on the exclusion list", "aapvm": "the VM is the AAP server", "odd": "its size could not be read",
            "app01": "its name or description says keep"})
        # without the size limit an unknown size does not hold a snapshot back
        self.assertIn("odd", [x["vm"] for x in f.vm_snapshot_split(self.S, 3, self.RX)["old"]])

    def test_split_datacenter_and_cluster(self):
        s = [{"vm": "web01", "moid": "vm-1", "id": 1, "name": "a", "age_days": 9.0, "size_gb": 1.0, "datacenter": "DR-DC", "cluster": "CL1"},
             {"vm": "sql01", "moid": "vm-2", "id": 1, "name": "a", "age_days": 9.0, "size_gb": 1.0, "datacenter": "DC1", "cluster": "PROD-SQL"},
             {"vm": "esx", "moid": "vm-3", "id": 1, "name": "a", "age_days": 9.0, "size_gb": 1.0, "datacenter": "DC1", "cluster": ""},
             {"vm": "app01", "moid": "vm-4", "id": 1, "name": "a", "age_days": 9.0, "size_gb": 1.0, "datacenter": "DC1", "cluster": "CL1"}]
        r = f.vm_snapshot_split(s, 3, self.RX, [], [], 1024, ["dr-*"], ["*sql*"])
        self.assertEqual([x["vm"] for x in r["old"]], ["app01", "esx"])   # a standalone host's VM has no cluster to match
        self.assertEqual({x["vm"]: x["reason"] for x in r["held"]}, {
            "web01": "its datacenter DR-DC is on the exclusion list", "sql01": "its cluster PROD-SQL is on the exclusion list"})
        # the excluded VM's own reason comes first; without the lists nothing is held back
        self.assertEqual(f.vm_snapshot_split(s, 3, self.RX, ["web*"], [], 1024, ["dr-*"])["held"][0]["reason"],
                         "the VM is on the exclusion list")
        self.assertEqual(len(f.vm_snapshot_split(s, 3, self.RX, [], [], 1024)["old"]), 4)

    def test_name_words(self):
        S = [dict(self.S[0], name="Pre-Patching 10/14"), dict(self.S[4], name="PATCHES oct"), dict(self.S[5], vm="dc02", name="patch"),
             dict(self.S[1], name="patch-new"), dict(self.S[3], name="Patch - keep"), dict(self.S[7], vm="web02", name="before upgrade")]
        r = f.vm_snapshot_split(S, 3, self.RX, [], [], 1024, name_words=["Patching", "Patches", "Patch"])
        # any case, anywhere in the name; too young is not old at all; keep and the size limit still win
        self.assertEqual([x["name"] for x in r["old"]], ["patch", "Pre-Patching 10/14", "PATCHES oct"])
        self.assertEqual({x["name"]: x["reason"] for x in r["held"]}, {
            "Patch - keep": "its name or description says keep", "before upgrade": "its name has none of: Patching, Patches, Patch"})
        # survey text: one per line, or separated by commas; a phrase keeps its spaces
        self.assertEqual(f.snapshot_name_words("Patching, Patches\nPatch Tuesday\n"), ["Patching", "Patches", "Patch Tuesday"])
        self.assertEqual(f.vm_snapshot_split(S, 3, self.RX, [], [], 1024, name_words="patching")["old"], [S[0]])
        # [] (the default), "" or None: any name, as before
        for none in ([], "", None, [" "]):
            self.assertEqual(len(f.vm_snapshot_split(S, 3, self.RX, [], [], 1024, name_words=none)["old"]), 4)

    def test_plan(self):
        old = f.vm_snapshot_split(self.S, 3, self.RX, [], [], 1024)["old"]
        self.assertEqual(len(f.vm_snapshot_plan(old)["todo"]), 4)
        # approved: web01#1 (still old), app01#1 (held: keep), gone#9; files01#1 approved but too big now
        plan = f.vm_snapshot_plan(old, [{"moid": "vm-1", "id": "1"}, {"moid": "vm-3", "id": 1}, {"moid": "vm-9", "id": 9},
                                        {"moid": "vm-6", "id": 1}])
        self.assertEqual([(x["vm"], x["id"]) for x in plan["todo"]], [("web01", 1)])
        self.assertEqual([x["moid"] for x in plan["skipped"]], ["vm-3", "vm-9", "vm-6"])
        self.assertEqual(f.vm_snapshot_plan(old, [])["todo"], [])

    def test_row_and_sizes(self):
        self.assertEqual(f.vm_snap_row(dict(self.S[2], created="2026-08-27T10:00:00Z", folder="/DC1/vm")),
                         ["db01", "baseline", "2026-08-27 10:00:00", "unknown", 40.0, "?", "/DC1/vm", "KEEP for audit"])
        self.assertEqual(f.vm_snap_row(dict(self.S[2], taken_by="CORP\\jdoe"))[3], "CORP\\jdoe")
        self.assertEqual(f.vm_snap_row(dict(self.S[0], reason="r"))[-1], "r")
        self.assertEqual([f.human_size(x) for x in (0.4, 0.95, 1.0, 12.44, 1023.9, 1024, 1536, None)],
                         ["410 MB", "973 MB", "1.0 GB", "12.4 GB", "1023.9 GB", "1.00 TB", "1.50 TB", "?"])

    def test_two_vcenters(self):
        """Two vCenters both have a vm-1 with snapshot 1: the approval names the vCenter, and only that
        vCenter's snapshot is deleted (a moid alone would pick the other VM)."""
        a = dict(self.S[0], vcenter="vc01.example.mil")
        b = dict(self.S[0], vm="other", vcenter="vc02.example.mil")
        plan = f.vm_snapshot_plan([a, b], [{"vcenter": "vc02.example.mil", "moid": "vm-1", "id": 1}])
        self.assertEqual([(x["vm"], x["vcenter"]) for x in plan["todo"]], [("other", "vc02.example.mil")])
        # an approval without a vCenter (from an older report, which read only the credential's vCenter)
        # means the credential's vCenter - never the other one
        plan = f.vm_snapshot_plan([a, b], [{"moid": "vm-1", "id": 1}], "vc01.example.mil")
        self.assertEqual([x["vcenter"] for x in plan["todo"]], ["vc01.example.mil"])
        plan = f.vm_snapshot_plan([b], [{"moid": "vm-1", "id": 1}], "vc01.example.mil")
        self.assertEqual((plan["todo"], len(plan["skipped"])), ([], 1))
        self.assertNotEqual(f.vm_snap_vm_key(a), f.vm_snap_vm_key(b))
        self.assertEqual(f.vm_snap_row(dict(b, folder="/DC1/vm"), True)[6], "vc02: /DC1/vm")
        self.assertEqual(f.vm_snap_row(dict(b, folder="/DC1/vm"))[6], "/DC1/vm")

    def test_cert_report_section(self):
        certs = [{"days": 45, "expires": "2026-11-22", "host": "web01", "subject": "CN=web01", "where": "/etc/pki/tls/certs/web01.crt"},
                 {"days": -3, "expires": "2026-10-05", "host": "db01", "subject": "CN=db01", "where": "tcp 443"},
                 {"days": 200, "expires": "2027-04-26", "host": "app01", "subject": "CN=app01", "where": "/etc/app.crt"},
                 {"days": 30, "expires": "2026-11-07", "host": "app02", "subject": "CN=app02", "where": "/etc/app2.crt"},
                 {"days": 60, "expires": "2026-12-07", "host": "app03", "subject": "CN=app03", "where": "/etc/app3.crt"}]
        s = f.cert_report_section(certs, 30, 60)
        self.assertEqual([r[0] for r in s["rows"]], [-3, 30, 45, 60, 200])                    # soonest first
        self.assertEqual(s["row_status"], ["critical", "warning", "info", "info", ""])
        self.assertEqual([c[0] for c in s["cell_status"]], ["critical", "warning", "info", "info", ""])
        self.assertEqual([r[5] for r in s["rows"]], ["EXPIRED", "expires within 30 days", "expires within 60 days",
                                                      "expires within 60 days", "ok"])
        self.assertEqual(s["status"], "critical")
        self.assertNotIn("status", f.cert_report_section(certs[2:3]))
        self.assertEqual(f.cert_report_section([])["rows"], [])
        html = f.report_text({"title": "t", "sections": [s]})
        self.assertIn("EXPIRED", html)

    def test_act_chat_reply(self):
        for t in ("I am ready. What task or command would you like me to assist you with?", "How can I help you today?",
                  "Hello! What would you like me to do?", "  I'm ready to assist."):
            self.assertTrue(f.act_is_chat_reply(t), t)
        for t in ("", "The container kcmysql was removed and no unit runs it: recreate it, then podman generate systemd.",
                  "What happened: chronyd stopped at 03:12 after the NTP server became unreachable."):
            self.assertFalse(f.act_is_chat_reply(t), t)

    def test_vcenter_helpers(self):
        st = [{"name": "vc01.example.mil", "ok": True, "error": "", "note": ""},
              {"name": "VC01", "ok": True, "error": "", "note": "the same vCenter as vc01.example.mil: read once"},
              {"name": "vc02.example.mil:8443", "ok": True, "error": "", "note": "vCenter vc02 has no datacenter named DC9"},
              {"name": "192.0.2.3", "ok": False, "error": "Could not connect to vCenter 192.0.2.3: timed out", "note": ""}]
        self.assertEqual(f.vm_vcenters_read(st), ["vc01.example.mil", "vc02.example.mil:8443"])
        self.assertEqual([f.vc_short(x) for x in ("vc01.example.mil", "vc02.example.mil:8443", "192.0.2.3")], ["vc01", "vc02:8443", "192.0.2.3"])
        self.assertEqual(f.vm_dc_label({"vcenter": "vc02.example.mil", "datacenter": "DC1"}, True), "vc02 / DC1")
        self.assertEqual(f.vm_dc_label({"vcenter": "vc02.example.mil", "datacenter": "DC1"}), "DC1")
        doc = f.vm_vcenter_report({"title": "VMware datastore report: all 4 below 85% used", "status": "ok", "sections": [{"title": "All"}]}, st)
        self.assertEqual((doc["title"], doc["status"]), ("VMware datastore report: all 4 below 85% used - 1 vCenter(s) not read", "critical"))
        self.assertEqual(doc["sections"][0]["rows"], [["192.0.2.3", "Could not connect to vCenter 192.0.2.3: timed out"]])
        self.assertEqual(len(doc["sections"]), 2)
        self.assertEqual(f.vm_vcenter_report({"title": "x"}, st[:3]), {"title": "x"})
        self.assertEqual(f.vm_ds_row({"name": "ds1", "vcenter": "vc02.example.mil", "datacenter": "DC1"}, True)[1], "vc02 / DC1")

    def test_name_matches(self):
        self.assertTrue(f.name_matches("DC01", ["dc*"]))
        self.assertTrue(f.name_matches("vcsa01", ["VCSA01"]))
        self.assertFalse(f.name_matches("adc01", ["dc*"]))
        self.assertFalse(f.name_matches("x", ["", " "]))


    def test_age_compared_unrounded(self):
        # 6.96 days shows as 7.0 but is not 7 days old yet; 7.0001 is
        snaps = [{"vm": "a", "moid": "vm-1", "id": 1, "name": "x", "description": "", "age_days": 7.0, "age_days_exact": 6.96, "size_gb": 1.0},
                 {"vm": "b", "moid": "vm-2", "id": 1, "name": "y", "description": "", "age_days": 7.0, "age_days_exact": 7.0001, "size_gb": 1.0},
                 {"vm": "c", "moid": "vm-3", "id": 1, "name": "z", "description": "", "age_days": 7.0, "size_gb": 1.0}]   # an older module: age_days
        self.assertEqual([x["vm"] for x in f.vm_snapshot_split(snaps, 7)["old"]], ["b", "c"])


class NameLists(unittest.TestCase):
    """vmware_protected_vms and the other name settings given as text (a survey, -e x=AAP01) must never be read as
    the letters of one name: the protection would match nothing."""

    def test_name_list(self):
        self.assertEqual(f.name_list("AAP01"), ["AAP01"])
        self.assertEqual(f.name_list("AAP01, VCSA01\nDC*"), ["AAP01", "VCSA01", "DC*"])
        self.assertEqual(f.name_list(["AAP01", " ", "x"]), ["AAP01", "x"])
        self.assertEqual(f.name_list(None), [])
        with self.assertRaises(ValueError):
            f.name_list({"AAP01": 1})

    def test_name_matches_text(self):
        self.assertTrue(f.name_matches("aap01", "AAP01"))
        self.assertTrue(f.name_matches("dc02", "AAP01, DC*"))
        self.assertFalse(f.name_matches("A", "AAP01"))            # not a letter of it
        self.assertTrue(f.name_matches("vcsa01", ["AAP01", "vcsa*"]))
        self.assertFalse(f.name_matches("web01", ""))

    def test_protected_text_in_the_snapshot_split(self):
        snaps = [{"vm": "AAP01", "moid": "vm-1", "id": 1, "name": "x", "description": "", "age_days": 9.0, "size_gb": 1.0}]
        r = f.vm_snapshot_split(snaps, 3, "", "AAP01")
        self.assertEqual((r["old"], [x["reason"] for x in r["held"]]), ([], ["the VM is on the exclusion list"]))


class PickLists(unittest.TestCase):
    """The pick lists: survey choices that end with the ID token, read back from the survey's answer, and
    each pick found again live (plugins/filter/site_filters.py, vm_pick_*)."""
    SNAP = {"vm": "web01", "moid": "vm-42", "vcenter": "VC01.example.mil", "datacenter": "DC1", "name": "before (patch)",
            "id": 7, "age_days": 6.2, "size_gb": 12.4, "created": "2026-10-14T08:12:00Z"}
    VM = {"name": "web01", "moid": "vm-42", "vcenter": "vc01.example.mil", "datacenter": "DC1", "cluster": "PROD-CL01",
          "folder": "/DC1/vm/Linux", "template": False, "power": "poweredOn", "hostname": "web01.example.mil", "ip": "192.0.2.20",
          "ips": ["192.0.2.20", "192.0.2.21"], "tools": "toolsOk", "tools_running": "guestToolsRunning", "heartbeat": "green",
          "up_minutes": 3000}

    def vm(self, **kw):
        return dict(self.VM, **kw)

    def test_snapshot_choice_round_trip(self):
        c = f.vm_pick_snapshot_choices([self.SNAP], multi=True)["choices"][0]
        self.assertEqual(c, 'web01: "before (patch)" (12.4 GB, 6.2 days old, taken 2026-10-14 08:12 UTC, VC01) [vc01.example.mil/vm-42/7]')
        p = f.vm_pick_parse([c, c], "snapshot")                         # the same pick twice: once
        self.assertEqual(p["picks"], [{"vcenter": "vc01.example.mil", "moid": "vm-42", "id": "7", "vm": "web01", "name": "before (patch)",
                                       "text": c}])
        self.assertEqual(f.vm_pick_snapshot_choices([self.SNAP] * 3, max_choices=2)["more"], 1)

    def test_parse_text_bad_and_empty(self):
        text = ("(nothing to pick: no snapshot may be deleted - list of 2026-10-20 10:00 UTC)\n\nweb01 typed by hand\n"
                "web01 (x) [vc01/vm-42]\n")
        p = f.vm_pick_parse(text, "snapshot")
        self.assertEqual((p["picks"], p["ignored"]), ([], 1))
        self.assertEqual(p["bad"], ["web01 typed by hand", "web01 (x) [vc01/vm-42]"])   # a VM's token is no snapshot's
        p = f.vm_pick_parse(["db (01) (VMware Tools running, up 2.0 days) [vc01/vm-7]"], "vm")
        self.assertEqual((p["picks"][0]["vm"], p["picks"][0]["moid"]), ("db (01)", "vm-7"))      # brackets in the name stay
        plan = [{"vm": "web01", "vcenter": "VC01", "moid": "vm-42", "id": 7, "name": "x"}, {"vm": "nope"}]
        p = f.vm_pick_parse(plan, "snapshot")
        self.assertEqual(([(x["vcenter"], x["id"]) for x in p["picks"]], len(p["bad"])), ([("vc01", "7")], 1))

    def test_vms_offered_and_held(self):
        vms = [self.vm(), self.vm(name="aap01", moid="vm-1", hostname="aap01.example.mil", ip="192.0.2.5", ips=[]),
               self.vm(name="SQL01", moid="vm-2"), self.vm(name="off01", moid="vm-3", power="poweredOff"),
               self.vm(name="tmpl", moid="vm-4", template=True), self.vm(name="other", moid="vm-5", cluster="DEV")]
        r = f.vm_pick_vms(vms, {"protected_ids": ["192.0.2.5"], "exclude": ["sql*"], "clusters": ["prod-*"]})
        self.assertEqual([v["name"] for v in r["offered"]], ["web01"])
        self.assertEqual(r["offered"][0]["choice"],
                         "web01 (VMware Tools running, up 2.1 days, DC1 / PROD-CL01, guest web01.example.mil 192.0.2.20) [vc01.example.mil/vm-42]")
        self.assertEqual(sorted((v["name"], v["reason"].split(" (")[0]) for v in r["held"]),
                         [("SQL01", "on the exclusion list"), ("aap01", "the AAP server or a protected VM")])
        self.assertEqual((r["off"], r["out_of_scope"]), (2, 1))
        self.assertEqual(f.vm_pick_reasons(r["held"]), "1 on the exclusion list (vmware_protected_vms, vm_pick_reboot_exclude_vms), "
                                                       "1 the AAP server or a protected VM (vmware_protected_groups)")
        self.assertEqual(f.vm_pick_ids(self.VM), ["192.0.2.20", "192.0.2.21", "web01"])

    def test_only_hung(self):
        vms = [self.vm(), self.vm(name="hb", moid="vm-6", heartbeat="red", tools_running="guestToolsRunning"),
               self.vm(name="nt", moid="vm-7", heartbeat="gray", tools="toolsNotRunning", tools_running="guestToolsNotRunning"),
               self.vm(name="none", moid="vm-8", heartbeat="gray", tools="toolsNotInstalled", tools_running="guestToolsNotRunning"),
               self.vm(name="booting", moid="vm-9", heartbeat="red", up_minutes=3),
               self.vm(name="unknown", moid="vm-10", heartbeat="red", up_minutes=None)]
        r = f.vm_pick_vms(vms, {"only_hung": True, "hung_min_minutes": 15})
        self.assertEqual([(v["name"], v["hung"]) for v in r["offered"]],
                         [("hb", "no heartbeat"), ("nt", "VMware Tools not running"), ("unknown", "no heartbeat")])
        self.assertTrue(r["offered"][0]["choice"].startswith("hb (no heartbeat, up 2.1 days,"))
        self.assertEqual(sorted(v["name"] for v in r["held"]), ["booting", "none", "web01"])

    def test_snapshot_plan(self):
        live = dict(self.SNAP, vcenter="vc01.example.mil")
        held = dict(live, id=8, name="keep me", reason="its name or description says keep")
        young = dict(live, id=9, name="new")
        split = {"all": [live, held, young], "old": [live], "held": [held]}
        tok = lambda i, name="before (patch)", vm="web01": '%s: "%s" (1 GB, 1 days old, taken x) [vc01.example.mil/vm-42/%s]' % (vm, name, i)
        picks = f.vm_pick_parse([tok(7), tok(8, "keep me"), tok(9, "new"), tok(5),
                                 '%s: "x" (y) [vc02.example.mil/vm-42/7]' % "web01"], "snapshot")["picks"]
        plan = f.vm_pick_snapshot_plan(split, picks, ["VC01.example.mil"])
        self.assertEqual([x["id"] for x in plan["todo"]], [7])
        self.assertEqual([x["reason"].split(":")[0].split(" (")[0] for x in plan["skipped"]],
                         ["held back now", "not old enough to be offered", "no longer there", "its vCenter vc02.example.mil was not read by this job"])
        renamed = f.vm_pick_parse([tok(7, "old name")], "snapshot")["picks"]
        self.assertIn('the snapshot was renamed since the list was made (was "old name", now "before (patch)")',
                      f.vm_pick_snapshot_plan(split, renamed, ["vc01.example.mil"])["skipped"][0]["reason"])
        moved = f.vm_pick_parse([tok(7, vm="web02")], "snapshot")["picks"]
        self.assertIn('the VM was renamed', f.vm_pick_snapshot_plan(split, moved, ["vc01.example.mil"])["skipped"][0]["reason"])

    def test_reboot_plan(self):
        vms = [self.vm(), self.vm(name="nt", moid="vm-7", tools_running="guestToolsNotRunning"), self.vm(name="SQL01", moid="vm-2")]
        picked = f.vm_pick_vms(vms, {"exclude": ["SQL*"]})
        texts = [v["choice"] for v in picked["offered"]] + ["SQL01 (x) [vc01.example.mil/vm-2]", "gone (x) [vc01.example.mil/vm-99]"]
        picks = f.vm_pick_parse(texts, "vm")["picks"]
        plan = f.vm_pick_reboot_plan(picked, picks, ["vc01.example.mil"], "guest")
        self.assertEqual([x["name"] for x in plan["todo"]], ["web01"])
        self.assertEqual((plan["todo"][0]["tools"], plan["todo"][0]["tools_status"]), ("guestToolsRunning", "toolsOk"))
        self.assertEqual(plan["todo"][0]["ids"], ["192.0.2.20", "192.0.2.21", "web01"])
        self.assertEqual([x["reason"].split(":")[0].split(",")[0] for x in plan["skipped"]],
                         ["VMware Tools is not running in it", "held back now", "not offered now"])
        hard = f.vm_pick_reboot_plan(picked, picks, ["vc01.example.mil"], "hard")
        self.assertEqual([x["name"] for x in hard["todo"]], ["nt", "web01"])
        renamed = f.vm_pick_parse(["web02 (x) [vc01.example.mil/vm-42]"], "vm")["picks"]
        self.assertIn('was "web02", now "web01"', f.vm_pick_reboot_plan(picked, renamed, ["vc01.example.mil"])["skipped"][0]["reason"])


class VmwareAlarms(unittest.TestCase):
    D = {"vcenter": "vc01", "read_at": "2026-10-07T12:00:00Z", "window_hours": 24.0, "events_capped": False,
         "alarms": [{"entity_type": "host", "entity": "esx01", "moid": "host-1", "cluster": "CL1", "datacenter": "DC1",
                     "alarm": "Host hardware power status", "alarm_description": "PSU", "status": "red", "severity": "critical",
                     "time": "2026-10-07T03:00:00Z", "acknowledged": False, "acknowledged_by": "", "acknowledged_time": ""},
                    {"entity_type": "cluster", "entity": "CL1", "moid": "domain-c1", "cluster": "CL1", "datacenter": "DC1",
                     "alarm": "vSphere HA failover resources", "alarm_description": "", "status": "yellow", "severity": "warning",
                     "time": "2026-10-06T03:00:00Z", "acknowledged": True, "acknowledged_by": "CORP\\op", "acknowledged_time": "2026-10-06T04:00:00Z"}],
         "config_issues": [{"entity_type": "host", "entity": "esx02", "moid": "host-2", "cluster": "CL1", "datacenter": "DC1",
                            "message": "SSH for the host has been enabled", "time": "", "type": "LocalTSMEnabledEvent"}],
         "hosts": [{"name": "esx01", "moid": "host-1", "cluster": "CL1", "datacenter": "DC1", "connection": "connected", "power": "poweredOn",
                    "maintenance": False, "version": "8.0.2", "build": "1", "vendor": "Dell", "model": "R750", "status": "red"},
                   {"name": "esx02", "moid": "host-2", "cluster": "CL1", "datacenter": "DC1", "connection": "notResponding", "power": "unknown",
                    "maintenance": False, "version": "", "build": "", "vendor": "", "model": "", "status": "gray"},
                   {"name": "esx03", "moid": "host-3", "cluster": "CL1", "datacenter": "DC1", "connection": "connected", "power": "poweredOn",
                    "maintenance": True, "version": "8.0.2", "build": "1", "vendor": "Dell", "model": "R750", "status": "green"}],
         "events": [{"key": i, "time": "2026-10-07T0%d:00:00Z" % i, "type": "BadUsernameSessionEvent", "group": "login", "severity": "warning",
                     "category": "info", "message": "Cannot login svc_scan@192.0.2.99", "user": "", "login_user": "svc_scan", "ip": "192.0.2.99",
                     "host": "esx01", "vm": "", "cluster": "CL1", "datacenter": "DC1"} for i in range(1, 4)]
                   + [{"key": 10, "time": "2026-10-07T05:00:00Z", "type": "HostNotRespondingEvent", "group": "connection", "severity": "critical",
                       "category": "error", "message": "Host esx02 is not responding", "user": "", "login_user": "", "ip": "", "host": "esx02",
                       "vm": "", "cluster": "CL1", "datacenter": "DC1"},
                      {"key": 11, "time": "2026-10-07T06:00:00Z", "type": "VmGuestShutdownEvent", "group": "vm", "severity": "info",
                       "category": "info", "message": "Guest OS shut down for app01", "user": "CORP\\admin", "login_user": "", "ip": "",
                       "host": "esx01", "vm": "app01", "cluster": "CL1", "datacenter": "DC1"},
                      {"key": 12, "time": "2026-10-07T07:00:00Z", "type": "com.vmware.vc.ha.VmRestartedByHAEvent", "group": "vm", "severity": "warning",
                       "category": "warning", "message": "vSphere HA restarted db01", "user": "", "login_user": "", "ip": "",
                       "host": "esx03", "vm": "db01", "cluster": "CL1", "datacenter": "DC1"}]
                   + [{"key": 20 + i, "time": "2026-10-07T1%d:00:00Z" % i, "type": "esx.problem.storage.latency", "group": "other",
                       "severity": "warning", "category": "warning", "message": "latency %d ms" % (100 + i), "user": "", "login_user": "",
                       "ip": "", "host": "esx01", "vm": "", "cluster": "CL1", "datacenter": "DC1"} for i in range(2)]}

    def test_groups(self):
        g = va.vm_alarm_groups(self.D, {"login_critical": 3})
        self.assertEqual([(x["user"], x["ip"], x["where"], x["count"], x["severity"], _t(x["first"]), _t(x["last"])) for x in g["logins"]],
                         [("svc_scan", "192.0.2.99", "esx01", 3, "critical", "01", "03")])
        self.assertEqual([(c["host"], c["state"], c["events"], c["severity"]) for c in g["connection"]], [("esx02", "notResponding", 1, "critical")])
        self.assertEqual([e["vm"] for e in g["vm"]], ["db01", "app01"])                        # newest first
        self.assertEqual([(x["type"], x["count"], x["message"]) for x in g["other"]], [("esx.problem.storage.latency", 2, "latency 101 ms")])
        self.assertEqual(va.vm_alarm_groups(self.D)["logins"][0]["severity"], "warning")       # 3 < 10

    def test_two_vcenters(self):
        """vc02 also has a host-1 (esx01b, no alarm): vc01's critical host alarm must not colour it, the
        datacenter cells name the vCenter, and a failed login on vc02 itself is a vCenter-level problem."""
        import copy
        d = copy.deepcopy(self.D)
        for k in ("alarms", "config_issues", "hosts", "events"):
            for x in d[k]:
                x["vcenter"] = "vc01.example.mil"
        d["vcenter"] = "vc01.example.mil, vc02.example.mil"
        d["vcenters"] = [{"name": "vc01.example.mil", "ok": True, "error": "", "note": ""},
                         {"name": "vc02.example.mil", "ok": True, "error": "", "note": ""}]
        d["hosts"].append({"name": "esx01b", "moid": "host-1", "cluster": "CL9", "datacenter": "DC9", "connection": "connected",
                           "power": "poweredOn", "maintenance": False, "version": "8.0.2", "build": "1", "vendor": "", "model": "",
                           "status": "green", "vcenter": "vc02.example.mil"})
        d["events"].append({"key": 1, "time": "2026-10-07T09:00:00Z", "type": "BadUsernameSessionEvent", "group": "login", "severity": "warning",
                            "category": "info", "message": "Cannot login", "user": "", "login_user": "bob", "ip": "203.0.113.1", "host": "", "vm": "",
                            "cluster": "", "datacenter": "", "vcenter": "vc02.example.mil"})
        r = va.vm_alarm_report(d, {"login_critical": 10})
        self.assertIn("vCenters vc01.example.mil, vc02.example.mil", r["subtitle"])
        hosts = [x for x in r["sections"] if x["title"] == "All hosts"][0]
        i = [row[0] for row in hosts["rows"]].index("esx01b")
        self.assertEqual((hosts["rows"][i][2], hosts["rows"][i][7], hosts["row_status"][i]), ("vc02 / DC9", 0, ""))
        j = [row[0] for row in hosts["rows"]].index("esx01")
        self.assertEqual((hosts["rows"][j][2], hosts["rows"][j][7], hosts["row_status"][j]), ("vc01 / DC1", 1, "critical"))
        logins = va.vm_alarm_groups(d)["logins"]
        self.assertEqual([(x["where"], x["on_vcenter"]) for x in logins if x["user"] == "bob"], [("vc02.example.mil", True)])
        probs = va.vm_alarm_problems(d)["problems"]
        self.assertEqual([p["object_type"] for p in probs if "bob" in p["problem"]], ["vcenter"])
        self.assertEqual([p["datacenter"] for p in probs if p["object"] == "esx01"][0], "vc01 / DC1")
        self.assertIn("vc02", va.vm_alarm_names(d))

    def test_report_colours_and_counts(self):
        r = va.vm_alarm_report(self.D, {"login_critical": 10})
        self.assertEqual(r["status"], "critical")
        self.assertIn("1 critical, 1 warning alarm(s)", r["title"])
        self.assertIn("1 host(s) not connected", r["title"])
        self.assertIn("3 failed login(s)", r["title"])
        self.assertIn("1 VM event(s) to check", r["title"])
        sec = {s["title"]: s for s in r["sections"]}
        al = sec["Triggered alarms"]
        self.assertEqual([row[0] for row in al["rows"]], ["CRITICAL", "WARNING"])
        self.assertEqual(al["row_status"], ["critical", "warning"])
        self.assertEqual([c[0] for c in al["cell_status"]], ["critical", "warning"])
        self.assertTrue(al["rows"][1][5].startswith("yes - CORP\\op"))
        self.assertEqual(sec["Host connection"]["rows"][0][:3], ["CRITICAL", "esx02", "notResponding"])
        self.assertEqual(sec["Virtual machine events"]["row_status"], ["warning", "info"])
        hosts = sec["All hosts"]
        self.assertEqual(hosts["row_status"], ["critical", "critical", "info"])      # critical alarm, not connected, maintenance
        self.assertEqual(hosts["cell_status"][1][3], "critical")                     # its Connection cell
        self.assertEqual(hosts["rows"][0][7:], [1, 5])                               # 1 alarm, 5 warning/critical events
        self.assertEqual(sec["Configuration issues"]["rows"][0][3], "SSH for the host has been enabled")
        text = f.report_text(r)
        self.assertRegex(text, r"\nCRITICAL +Host +esx01 +Host hardware power status")

    def test_report_quiet_and_no_events(self):
        quiet = dict(self.D, alarms=[], config_issues=[], events=[], hosts=[dict(self.D["hosts"][0])])
        r = va.vm_alarm_report(quiet)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["title"].startswith("VMware alarms report: no triggered alarms (events: last 24 h)"))
        sec = {s["title"]: s for s in r["sections"]}
        self.assertNotIn("columns", sec["Triggered alarms"])                        # no empty table, the text says it
        self.assertEqual(sec["Host connection"]["text"], "Every host is connected, and none lost its connection in the last 24 h.")
        r = va.vm_alarm_report(dict(quiet, window_hours=0))
        self.assertNotIn("Failed logins", [s["title"] for s in r["sections"]])
        self.assertNotIn("events: last", r["title"])

    def test_problems_order_ids_and_cap(self):
        p = va.vm_alarm_problems(self.D, {"login_critical": 10})
        kinds = [(x["id"], x["kind"], x["severity"]) for x in p["problems"]]
        self.assertEqual(kinds[:2], [("P1", "alarm", "critical"), ("P2", "connection", "critical")])
        self.assertEqual({k for _, k, _ in kinds}, {"alarm", "connection", "login", "vm", "event", "config"})
        self.assertNotIn("app01", [x["object"] for x in p["problems"]])             # an info VM event is not a problem
        capped = va.vm_alarm_problems(self.D, {"max_items": 2, "config_issues": False})
        self.assertEqual((len(capped["problems"]), len(capped["not_analyzed"])), (2, 4))
        self.assertNotIn("config", [x["kind"] for x in capped["problems"] + capped["not_analyzed"]])

    def test_evidence_task_and_names(self):
        p = va.vm_alarm_problems(self.D)["problems"]
        ev = va.vm_alarm_evidence(self.D, p)
        self.assertIn("P1 [CRITICAL] Host esx01 (CL1 / DC1) - alarm \"Host hardware power status\"", ev)
        self.assertIn("esx02: cluster CL1, datacenter DC1, connection notResponding", ev)
        self.assertIn("[esx01]", ev)
        self.assertIn("BadUsernameSessionEvent", ev)
        self.assertTrue(va.vm_alarm_evidence(self.D, p, {"max_chars": 100}).endswith("[... evidence cut at 100 characters ...]"))
        task = va.vm_alarm_act_task(p, {"hours": 24, "extra": "Site note."})
        self.assertIn("BEGIN_ACT_ANALYSIS", task)
        self.assertIn("P1, P2", task)
        self.assertIn("You cannot run any commands", task)
        self.assertTrue(task.endswith("Site note."))
        names = va.vm_alarm_names(self.D)
        for n in ("vc01", "esx01", "CL1", "DC1", "app01", "db01"):
            self.assertIn(n, names)

    def test_parse(self):
        p = [{"id": "P1"}, {"id": "P2"}, {"id": "P3"}, {"id": "P4"}]
        good = ('Some text.\nBEGIN_ACT_ANALYSIS\n{"overall": "Fix  esx02 first.", "items": ['
                '{"id": "P1", "cause": "A PSU failed.", "evidence": "alarm", "fix": "Replace it.", "confidence": 92},'
                '{"id": "p2", "cause": "c", "confidence": "55%"}, {"id": 3, "likely_cause": "x", "confidence": "low"},'
                '{"id": "P4", "cause": "y", "confidence": 0.9}, {"id": "P9", "cause": "unknown id"},]}\nEND_ACT_ANALYSIS')
        r = va.act_analysis_parse(good, p)
        self.assertTrue(r["parsed"])
        self.assertEqual(r["overall"], "Fix esx02 first.")
        self.assertEqual({k: v["confidence"] for k, v in r["by_id"].items()}, {"P1": 92, "P2": 55, "P3": 30, "P4": 90})
        self.assertEqual(r["by_id"]["P3"]["cause"], "x")
        fenced = va.act_analysis_parse('Answer:\n```json\n[{"id": "P1", "cause": "c", "confidence": 101}]\n```', p)
        self.assertEqual(fenced["by_id"]["P1"]["confidence"], 100)
        inner = 'BEGIN_ACT_ANALYSIS\n{"overall": "o", "items": [{"id": "P2", "cause": "c", "confidence": 70}]}\nEND_ACT_ANALYSIS'
        wrapped = json.dumps({"action": "plan", "next_action": {"action": "finish", "message": inner}})
        w = va.act_analysis_parse(wrapped, p)                       # inside another JSON reply
        self.assertEqual((w["parsed"], w["overall"], w["by_id"]["P2"]["confidence"]), (True, "o", 70))
        bad = va.act_analysis_parse("ROOT CAUSE: something. CONFIDENCE: high", p)
        self.assertEqual((bad["parsed"], bad["error"]), (False, "ACT's answer has no readable JSON block"))
        self.assertEqual(va.act_analysis_parse("", p)["error"], "ACT gave no answer")

    def test_act_report(self):
        p = va.vm_alarm_problems(self.D)["problems"]
        parsed = va.act_analysis_parse('{"overall": "o", "items": [{"id": "P1", "cause": "PSU", "evidence": "e", "fix": "f", "confidence": 90},'
                                       '{"id": "P2", "cause": "net", "confidence": 60}, {"id": "P3", "cause": "?", "confidence": 20}]}', p)
        r = va.vm_alarm_act_report(p, parsed, {"ran": True, "vcenter": "vc01", "provider": "genai", "model": "m", "hours": 24})
        self.assertEqual(r["title"], "VMware alarms ACT analysis: likely causes and fixes for %d problem(s)" % len(p))
        t = [s for s in r["sections"] if s["title"] == "Likely causes and fixes"][0]
        self.assertEqual([row[7] for row in t["rows"][:3]], ["90% high", "60% medium", "20% low"])
        self.assertEqual([c[7] for c in t["cell_status"][:3]], ["ok", "warning", "critical"])
        self.assertEqual(t["cell_status"][0][1], "critical")
        self.assertEqual(t["rows"][3][4], "ACT gave no answer for this one")
        self.assertEqual(r["sections"][0], {"title": "What to do first", "status": "critical", "lines": ["o"]})
        nr = va.vm_alarm_act_report(p, {}, {"ran": False, "reason": "no key"})
        self.assertEqual((nr["status"], nr["sections"][0]["lines"][0]), ("critical", "no key"))
        self.assertIn("ACT did not run", nr["title"])
        raw = va.vm_alarm_act_report(p, va.act_analysis_parse("just text", p), {"ran": True, "raw": "line one\n\nline two"})
        self.assertEqual([s for s in raw["sections"] if s["title"] == "ACT's answer"][0]["lines"], ["line one", "line two"])


class EsxiSecurityReport(unittest.TestCase):
    H = [{"name": "esx01", "cluster": "C", "datacenter": "D", "status": "changed", "before": {"SSH": "running, starts with the host", "lockdown": "disabled"},
          "changes": ["SSH stopped and set to start manually", "lockdown disabled -> normal"], "notes": [], "errors": []},
         {"name": "esx02", "cluster": "C", "datacenter": "D", "status": "failed", "before": {"SSH": "stopped, starts manually"},
          "changes": [], "notes": [], "errors": ["lockdown: the vCenter account lacks the privilege Host.Config.Settings on this host"]},
         {"name": "esx03", "cluster": "C", "datacenter": "D", "status": "ok", "before": {"SSH": "stopped, starts manually", "lockdown": "normal"},
          "changes": [], "notes": [], "errors": []},
         {"name": "esx04", "cluster": "C", "datacenter": "D", "status": "skipped", "before": {}, "changes": [],
          "notes": ["not connected to vCenter (notResponding)"], "errors": []}]
    O = {"ssh": "disabled", "settings": {"UserVars.ESXiShellTimeOut": 600}, "lockdown": "normal"}

    def test_report(self):
        r = ef.esxi_security_report(self.H, self.O)
        self.assertEqual(r["title"], "ESXi security settings: 1 of 4 host(s) changed, 1 failed, 1 skipped (not connected)")
        self.assertEqual(r["status"], "critical")
        self.assertEqual(r["subtitle"], "Wanted: SSH disabled; UserVars.ESXiShellTimeOut = 600; lockdown normal")
        sec = {s["title"]: s for s in r["sections"]}
        self.assertIn("Host.Config.Settings", sec["Failed"]["rows"][0][2])
        self.assertEqual(sec["Changed"]["rows"][0][2], "SSH stopped and set to start manually; lockdown disabled -> normal")
        allh = sec["All hosts"]
        self.assertEqual(allh["columns"], ["Host", "Cluster", "Datacenter", "Result", "SSH (before)", "lockdown (before)", "Notes"])
        self.assertEqual(allh["row_status"], ["warning", "critical", "", "unknown"])
        self.assertEqual([c[3] for c in allh["cell_status"]], ["warning", "critical", "ok", "unknown"])
        self.assertIn("CHANGED", f.report_text(r))

    def test_dry_run_and_all_set(self):
        dry = ef.esxi_security_report(self.H[:1], dict(self.O, check_mode=True))
        self.assertEqual(dry["title"], "ESXi security settings (dry run): 1 of 1 host(s) would change")
        self.assertTrue(dry["subtitle"].endswith("DRY RUN: nothing was changed"))
        self.assertIn("Would change", [s["title"] for s in dry["sections"]])
        ok = ef.esxi_security_report([self.H[2]], self.O)
        self.assertEqual((ok["title"], ok["status"]), ("ESXi security settings: all 1 host(s) already set", "ok"))


def _hl_host(name, cluster, dc="DC1", vc="vc01.example.mil", **kw):
    h = {"vcenter": vc, "datacenter": dc, "cluster": cluster, "standalone": not cluster, "name": name, "moid": "host-" + name,
         "connection": "connected", "power": "poweredOn", "maintenance": False, "standby": "none", "status": "green",
         "ha_state": "connectedToMaster", "cpu_used_mhz": 10000, "cpu_mhz": 100000, "mem_used_mb": 200000, "mem_mb": 524288,
         "uptime_days": 40.5, "reboot_required": False, "version": "8.0.3", "build": "24022510", "vendor": "Dell Inc.",
         "model": "PowerEdge R760", "sensors_bad": [], "config_issues": [], "alarms": []}
    h.update(kw)
    return h


def _hl_cluster(name, dc="DC1", vc="vc01.example.mil", ha_on=True, drs_on=True, **kw):
    ha, drs = ha_on, drs_on
    c = {"vcenter": vc, "datacenter": dc, "name": name, "moid": "domain-" + name, "status": "green",
         "ha": {"enabled": ha, "host_monitoring": "enabled" if ha else "", "admission": "hosts" if ha else "", "admission_hosts": 1 if ha else 0,
                "admission_cpu_pct": None, "admission_mem_pct": None, "failover_level_now": 1 if ha else None,
                "cpu_left_pct": None, "mem_left_pct": None},
         "drs": {"enabled": drs, "behavior": "fullyAutomated" if drs else "", "recommendations": 0, "faults": 0, "score": 97 if drs else None},
         "vcls_retreat": False, "evc": "", "hosts": 2, "effective_hosts": 2, "config_issues": [], "alarms": []}
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k] = dict(c[k], **v)
        else:
            c[k] = v
    return c


class VmHealth(unittest.TestCase):
    VC = "vc01.example.mil"

    def data(self):
        return {
            "vcenter": self.VC, "vcenters": [{"name": self.VC, "ok": True, "error": "", "note": ""}], "read_at": "2026-10-13T06:00:00Z",
            "vcenter_info": [{"vcenter": self.VC, "version": "8.0.3", "build": "1", "name": "VMware vCenter Server 8.0.3", "alarms": [],
                              "vcls_readable": True}],
            "datacenters": [{"vcenter": self.VC, "name": "DC1", "moid": "datacenter-1", "status": "green", "config_issues": [], "alarms": []},
                            {"vcenter": self.VC, "name": "DC2", "moid": "datacenter-2", "status": "green", "config_issues": [], "alarms": []}],
            "clusters": [
                # HA reserves 1 host but survives 0 now; 2 DRS recommendations waiting
                _hl_cluster("CL1", ha={"failover_level_now": 0}, drs={"recommendations": 2}, hosts=3),
                # HA and DRS off on 2 hosts
                _hl_cluster("CL2", ha_on=False, drs_on=False),
                # retreat mode with DRS on
                _hl_cluster("CL3", dc="DC2", vcls_retreat=True, hosts=1),
                # one host: HA and DRS off are fine
                _hl_cluster("CL4", dc="DC2", ha_on=False, drs_on=False, hosts=1),
            ],
            "hosts": [
                _hl_host("esx01", "CL1", ha_state="networkIsolated"),
                _hl_host("esx02", "CL1", ha_state="master", mem_used_mb=500000),
                _hl_host("esx03", "CL1", maintenance=True, ha_state="uninitialized"),
                _hl_host("esx04", "CL2", ha_state="", reboot_required=True,
                         sensors_bad=[{"name": "Power Supply 2", "state": "red", "type": "power"},
                                      {"name": "Fan 3", "state": "yellow", "type": "fan"}]),
                _hl_host("esx05", "CL2", ha_state="", connection="notResponding", cpu_used_mhz=None, mem_used_mb=None, uptime_days=None),
                _hl_host("esx06", "CL3", dc="DC2", ha_state="master"),
                _hl_host("esx07", "CL4", dc="DC2", ha_state=""),
                _hl_host("esx08", "", dc="DC2", ha_state="", config_issues=["SSH for the host has been enabled"],
                         alarms=[{"alarm": "Host memory usage", "status": "yellow", "time": "2026-10-13T05:00:00Z", "acknowledged": False}]),
            ],
        }

    def problems(self, d=None, **o):
        return [(p["severity"], p["object"], p["problem"]) for p in vh.vm_health_problems(d or self.data(), o)]

    def test_problems(self):
        ps = self.problems()
        crit = {(o, pr) for s, o, pr in ps if s == "critical"}
        warn = {(o, pr) for s, o, pr in ps if s == "warning"}
        info = {(o, pr) for s, o, pr in ps if s == "info"}
        self.assertIn(("esx01", "vSphere HA agent: network isolated"), crit)
        self.assertIn(("esx05", "Not responding to vCenter"), crit)
        self.assertIn(("esx04", "Hardware fault: Power Supply 2"), crit)
        self.assertIn(("CL3", "vSphere Cluster Services (vCLS) in retreat mode: DRS is not working and HA works less well"), crit)
        self.assertIn(("CL1", "HA can survive 0 host failure(s) now; admission control reserves 1"), warn)
        self.assertIn(("CL1", "2 DRS recommendation(s) waiting"), warn)
        self.assertIn(("CL2", "vSphere HA is off: the VMs of a failed host are not restarted"), warn)
        self.assertIn(("CL2", "DRS is off: VMs are not balanced, and maintenance mode does not move them"), warn)
        self.assertIn(("esx04", "Hardware warning: Fan 3"), warn)
        self.assertIn(("esx04", "Needs a restart (an update or a setting waits for it)"), warn)
        self.assertIn(("esx08", "SSH for the host has been enabled"), warn)
        self.assertIn(("esx02", "Memory 95% used"), crit)
        self.assertTrue(any(o == "esx08" and pr.startswith("Alarm: Host memory usage") for o, pr in warn))
        self.assertIn(("esx03", "In maintenance mode"), info)
        # a host in maintenance: its HA agent is not configured, as expected - not a problem
        self.assertFalse(any(o == "esx03" and "HA" in pr for s, o, pr in ps))
        # one-host cluster: HA and DRS off are fine; a host of a cluster without HA has no HA problem
        self.assertFalse(any(o in ("CL4", "esx07", "esx06") for s, o, pr in ps))
        # most severe first
        self.assertEqual([p[0] for p in ps], sorted([p[0] for p in ps], key=lambda x: vh.RANK[x]))

    def test_settings(self):
        ps = self.problems(require_ha=False, require_drs=False, mem_crit_pct=99, mem_warn_pct=0)
        self.assertFalse(any(o == "CL2" for s, o, pr in ps))
        self.assertFalse(any(o == "esx02" and pr.startswith("Memory") for s, o, pr in ps))
        ps = self.problems(cpu_warn_pct=5, cpu_crit_pct=0)
        self.assertIn(("warning", "esx06", "CPU 10% used"), ps)

    def test_admission_percent_and_drs_faults(self):
        c = _hl_cluster("CLP", ha={"admission": "percent", "admission_cpu_pct": 25, "admission_mem_pct": 25, "cpu_left_pct": 40,
                                   "mem_left_pct": 10, "failover_level_now": None},
                        drs={"faults": 3, "behavior": "manual"})
        ps = [(s, pr) for s, pr, _ in vh.cluster_problems(c, [_hl_host("a", "CLP"), _hl_host("b", "CLP")], {})]
        self.assertIn(("warning", "HA failover capacity below what is reserved: memory 10% left, 25% reserved"), ps)
        self.assertIn(("warning", "DRS could not apply 3 recommendation(s)"), ps)
        self.assertIn(("info", "DRS is manual: its recommendations wait for someone to apply them"), ps)
        off = _hl_cluster("CLX", ha={"admission": "off", "host_monitoring": "disabled"})
        ps = [(s, pr) for s, pr, _ in vh.cluster_problems(off, [_hl_host("a", "CLX"), _hl_host("b", "CLX")], {})]
        self.assertIn(("warning", "vSphere HA host monitoring is off: a failed host is not noticed"), ps)
        self.assertTrue(any(s == "info" and pr.startswith("HA admission control is off") for s, pr in ps))
        # retreat mode without DRS is a warning; a red cluster with nothing to explain it says so
        r = _hl_cluster("CLR", drs_on=False, vcls_retreat=True, status="red", hosts=1)
        ps = [(s, pr) for s, pr, _ in vh.cluster_problems(r, [_hl_host("a", "CLR")], {})]
        self.assertIn(("warning", "vSphere Cluster Services (vCLS) in retreat mode"), ps)
        self.assertIn(("critical", "vCenter shows the cluster red"), ps)

    def test_report(self):
        r = vh.vm_health_report(self.data(), {})
        n = {s: len([p for p in vh.vm_health_problems(self.data(), {}) if p["severity"] == s]) for s in ("critical", "warning")}
        self.assertEqual(r["title"], "VMware health report: %d critical, %d warning(s) - 2 datacenter(s), 4 cluster(s), 8 host(s)"
                         % (n["critical"], n["warning"]))
        self.assertEqual(r["status"], "critical")
        summ = {x["label"]: (x["value"], x["status"]) for x in r["summary"]}
        self.assertEqual(summ["Hosts not connected"], (1, "critical"))
        self.assertEqual(summ["Hosts with hardware faults"], (1, "critical"))
        self.assertEqual(summ["Triggered alarms"], (1, "warning"))
        self.assertEqual(summ["Datacenters"], (2, "critical"))
        sec = {s["title"]: s for s in r["sections"]}
        self.assertEqual(list(sec), ["Needs attention", "Datacenters", "Clusters", "Hosts", "vCenter"])
        need = sec["Needs attention"]
        self.assertEqual(need["columns"], ["Severity", "Object", "Problem", "What to do", "Where"])
        self.assertEqual(need["rows"][0][0], "CRITICAL")
        self.assertIn(["CRITICAL", "Host esx01", "vSphere HA agent: network isolated"], [row[:3] for row in need["rows"]])
        self.assertIn("DC1 / CL1", [row[4] for row in need["rows"] if row[1] == "Host esx01"])
        cl = {row[1]: (row, sec["Clusters"]["cell_status"][i]) for i, row in enumerate(sec["Clusters"]["rows"])}
        self.assertEqual(cl["CL2"][0][4:7], ["OFF", "OFF", "normal"])
        self.assertEqual(cl["CL2"][1][4:6], ["warning", "warning"])
        self.assertEqual(cl["CL2"][0][3], "1 of 2 (1 not connected)")
        self.assertEqual(cl["CL2"][1][3], "critical")
        self.assertEqual((cl["CL3"][0][6], cl["CL3"][1][6]), ("RETREAT MODE", "critical"))
        self.assertEqual(cl["CL4"][0][0], "OK")
        self.assertTrue(cl["CL1"][0][4].startswith("on, reserves 1 host(s); 1 host agent(s) not OK"))
        self.assertEqual(cl["CL1"][0][5], "on (fully automated), score 97%, 2 waiting")
        hs = {row[1]: (row, sec["Hosts"]["cell_status"][i]) for i, row in enumerate(sec["Hosts"]["rows"])}
        self.assertEqual((hs["esx05"][0][4], hs["esx05"][1][4]), ("not responding", "critical"))
        self.assertEqual((hs["esx01"][0][6], hs["esx01"][1][6]), ("network isolated", "critical"))
        self.assertEqual((hs["esx04"][0][7], hs["esx04"][1][7]), ("1 red, 1 yellow", "critical"))
        self.assertEqual((hs["esx02"][0][9], hs["esx02"][1][9]), ("95%", "critical"))
        self.assertEqual(hs["esx08"][0][2], "(standalone)")
        self.assertEqual(hs["esx03"][0][0], "INFO")
        dcs = {row[1]: row for row in sec["Datacenters"]["rows"]}
        self.assertEqual(dcs["DC1"][:8], ["CRITICAL", "DC1", 2, 5, 3, 1, 1, 0])
        self.assertIn("CRITICAL", f.report_text(r))

    def test_healthy_and_several_vcenters(self):
        d = self.data()
        d["clusters"] = [_hl_cluster("CL1")]
        d["hosts"] = [_hl_host("esx01", "CL1", ha_state="master"), _hl_host("esx02", "CL1")]
        r = vh.vm_health_report(d, {})
        self.assertEqual((r["title"], r["status"]), ("VMware health report: healthy - 2 datacenter(s), 1 cluster(s), 2 host(s)", "ok"))
        self.assertTrue(r["sections"][0]["text"].startswith("Nothing needs attention"))
        # two vCenters, each with a DC1 and a CL1: nothing mixed up, the vCenter in front
        vc2 = "vc02.example.mil"
        d2 = self.data()
        d2["vcenters"] = [{"name": self.VC, "ok": True}, {"name": vc2, "ok": True}]
        d2["clusters"] = [_hl_cluster("CL1"), _hl_cluster("CL1", vc=vc2, ha_on=False)]
        d2["hosts"] = [_hl_host("esx01", "CL1", ha_state="master"), _hl_host("esx02", "CL1"),
                       _hl_host("esx01", "CL1", vc=vc2, ha_state=""), _hl_host("esx02", "CL1", vc=vc2, ha_state="")]
        ps = vh.vm_health_problems(d2, {})
        self.assertEqual([(p["vcenter"], p["object"], p["problem"]) for p in ps],
                         [(vc2, "CL1", "vSphere HA is off: the VMs of a failed host are not restarted")])
        r = vh.vm_health_report(d2, {})
        self.assertEqual(r["sections"][0]["rows"][0][4], "vc02 / DC1")
        self.assertIn("vc02 / DC1", [row[2] for row in [s for s in r["sections"] if s["title"] == "Clusters"][0]["rows"]])

    def test_vcls_unreadable(self):
        d = self.data()
        d["vcenter_info"][0]["vcls_readable"] = False
        ps = self.problems(d)
        self.assertTrue(any(s == "info" and o == self.VC and pr.startswith("Could not read vCLS retreat mode") for s, o, pr in ps))


class VmAlarmHistory(unittest.TestCase):
    def test_fired_and_cleared(self):
        vc = "vc01.example.mil"
        ch = lambda t, to, moid="host-1", alarm="Host connection and power state", v=vc: {
            "vcenter": v, "time": "2026-10-13T0%d:00:00Z" % t, "alarm": alarm, "entity_type": "host", "entity": "esx01", "moid": moid,
            "from": "green", "to": to, "cluster": "CL1", "datacenter": "DC1"}
        d = {"vcenter": vc, "window_hours": 24, "alarms": [
                {"vcenter": vc, "moid": "host-2", "alarm": "Host CPU usage", "severity": "warning", "status": "yellow", "entity_type": "host",
                 "entity": "esx02"}],
             "alarm_changes": [ch(1, "red"), ch(2, "green"), ch(3, "red"), ch(4, "green"),
                               ch(5, "yellow", "host-2", "Host CPU usage"), ch(6, "green", "host-3", "Host CPU usage"),
                               ch(7, "red", v="vc02.example.mil")]}
        h = va.vm_alarm_history(d)
        got = [(x["vcenter"], x["moid"], x["alarm"], x["red"], x["yellow"], x["now"], x["severity"]) for x in h]
        self.assertEqual(got, [
            (vc, "host-2", "Host CPU usage", 0, 1, "yellow", "warning"),              # still on: first
            ("vc02.example.mil", "host-1", "Host connection and power state", 1, 0, "cleared", "info"),
            (vc, "host-1", "Host connection and power state", 2, 0, "cleared", "info")])   # went green-only host-3: left out
        self.assertEqual((h[2]["first"], h[2]["last"]), ("2026-10-13T01:00:00Z", "2026-10-13T04:00:00Z"))
        r = va.vm_alarm_report(d)
        self.assertIn("2 alarm(s) fired and cleared", r["title"])
        sec = {s["title"]: s for s in r["sections"]}["Alarms that fired in the last 24 h"]
        self.assertEqual(sec["rows"][0][8], "still yellow")
        self.assertEqual(sec["row_status"], ["warning", "info", "info"])
        self.assertEqual({x["label"]: x["value"] for x in r["summary"]}["Alarms fired (24 h)"], 3)
        # no events read: no such section
        quiet = va.vm_alarm_report(dict(d, window_hours=0))
        self.assertNotIn("Alarms that fired in the last 24 h", [s["title"] for s in quiet["sections"]])


def _t(iso):
    return iso[11:13]


class VmwareCerts(unittest.TestCase):
    """The VMware certificate renewal: which certificates are due, their findings, their CSR status."""
    VMCA = "CN=CA, DC=vsphere, DC=local, C=US, ST=California, O=vc01.example.mil, OU=VMware Engineering"
    PKI = "CN=Example Issuing CA 1, OU=PKI, O=Example, C=US"

    def certs(self):
        return [{"vcenter": "vc01.example.mil", "kind": "vcenter", "name": "vc01.example.mil", "port": 443, "subject": "CN=vc01.example.mil,O=Example",
                 "issuer": self.PKI, "sans": ["vc01.example.mil"], "not_after": "2026-11-01 00:00 UTC", "expires": "2026-11-01", "days": 12,
                 "self_signed": False},
                {"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx01.example.mil", "subject": "CN=esx01.example.mil", "issuer": self.VMCA,
                 "not_after": "2026-10-20 00:00 UTC", "expires": "2026-10-20", "days": 1, "self_signed": False},
                {"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx02.example.mil", "subject": "CN=esx02.example.mil", "issuer": self.PKI,
                 "not_after": "2029-01-01 00:00 UTC", "expires": "2029-01-01", "days": 805, "self_signed": False},
                {"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx03.example.mil", "subject": "CN=esx03", "issuer": "CN=esx03",
                 "not_after": "2026-09-01 00:00 UTC", "expires": "2026-09-01", "days": -48, "self_signed": True}]

    def test_vmca(self):
        self.assertTrue(vcf.vmware_cert_vmca(self.VMCA))
        self.assertTrue(vcf.vmware_cert_vmca("CN=CA,DC=corp,DC=local,C=US"))        # another SSO domain
        self.assertFalse(vcf.vmware_cert_vmca(self.PKI))
        self.assertFalse(vcf.vmware_cert_vmca("CN=Corp-Issuing-CA, DC=corp, DC=example"))   # an AD CS CA has DCs too, not CN=CA
        self.assertTrue(vcf.vmware_cert_vmca("CN=Our VMCA, O=vc01.example.mil", ["*o=vc01.example.mil*"]))

    def test_plan(self):
        p = vcf.vmware_cert_plan(self.certs(), {"warn_days": 30, "crit_days": 7, "job": 4711})
        self.assertEqual([c["name"] for c in p["due"]], ["esx03.example.mil", "esx01.example.mil", "vc01.example.mil"])
        by = dict((c["name"], c) for c in p["certs"])
        self.assertEqual(by["vc01.example.mil"]["renew"], "csr")
        self.assertEqual(by["esx01.example.mil"]["renew"], "vmca")
        self.assertEqual(by["esx03.example.mil"]["renew"], "csr")           # self-signed: a CSR, to get a real one
        self.assertEqual(by["esx02.example.mil"]["renew"], "")
        self.assertEqual(by["esx01.example.mil"]["severity"], "critical")
        self.assertEqual(by["vc01.example.mil"]["severity"], "warning")
        self.assertEqual(by["vc01.example.mil"]["id"], "vmware-cert:vcenter:vc01.example.mil:2026-11-01")   # a renewed certificate: a new id
        # every name read is in the tickets step's input - also esx02, with no finding - so a renewed certificate's ticket clears
        self.assertEqual(sorted(p["health"]), ["esx01.example.mil", "esx02.example.mil", "esx03.example.mil", "vc01.example.mil"])
        h = p["health"]["esx02.example.mil"]
        self.assertEqual((h["findings"], h["checks"], h["reach"]), ([], ["vmware_certs"], False))
        f3 = p["health"]["esx03.example.mil"]["findings"][0]
        self.assertIn("EXPIRED 48 days ago", f3["summary"])
        self.assertIn("vmware_cert_warn_days (30 days)", f3["note"])
        self.assertIn("Renew", p["health"]["esx01.example.mil"]["findings"][0]["note"])
        self.assertIn("openssl s_client -connect vc01.example.mil:443", p["health"]["vc01.example.mil"]["findings"][0]["hint"])

    def test_plan_test_days_and_only(self):
        p = vcf.vmware_cert_plan(self.certs(), {"warn_days": 3650, "only": ["ESX02"]})
        self.assertEqual([c["name"] for c in p["due"]], ["esx02.example.mil"])
        self.assertEqual(list(p["health"]), ["esx02.example.mil"])
        self.assertEqual(sorted(p["left_out"]), ["esx01.example.mil", "esx03.example.mil", "vc01.example.mil"])
        self.assertIn("(3650 days)", p["due"][0]["finding"]["note"])
        p = vcf.vmware_cert_plan(self.certs(), {"warn_days": 30, "vmca_csr": True})
        self.assertEqual([c["renew"] for c in p["due"]], ["csr", "csr", "csr"])

    def test_csr_result(self):
        due = vcf.vmware_cert_plan(self.certs(), {"warn_days": 30})["due"]
        made = [{"vcenter": "vc01.example.mil", "kind": "vcenter", "name": "vc01.example.mil", "csr": "-----BEGIN CERTIFICATE REQUEST-----\nx\n",
                 "csr_subject": "CN=vc01.example.mil", "csr_names": ["vc01.example.mil"], "error": ""},
                {"vcenter": "vc01.example.mil", "kind": "esxi", "name": "esx03.example.mil", "csr": "", "error": "the vCenter account lacks the privilege Certificate.Manage"}]
        r = vcf.vmware_cert_csr_result(due, made, {"connected": True, "ext": "csr"})
        self.assertEqual((r["check"], r["content_type"], r["retry_txt"]), ("vmware_certs", "text/plain", True))
        by = dict((c["host"], c) for c in r["certs"])
        self.assertEqual(by["vc01.example.mil"]["status"], "made")
        self.assertRegex(by["vc01.example.mil"]["file_name"], r"^vc01\.example\.mil-machine-ssl-[0-9a-f]{8}\.csr$")
        self.assertTrue(by["vc01.example.mil"]["sans_same"])
        self.assertIn("VECS", by["vc01.example.mil"]["key_note"])
        self.assertIn("Machine SSL Certificate > Import and Replace Certificate", by["vc01.example.mil"]["install"])
        self.assertEqual((by["esx03.example.mil"]["status"], by["esx03.example.mil"]["reason"]), ("failed", made[1]["error"]))
        self.assertEqual(by["esx01.example.mil"]["status"], "skipped")
        self.assertIn("VMCA", by["esx01.example.mil"]["reason"])
        # its CSR on the ticket already: never a second one
        r = vcf.vmware_cert_csr_result(due, [], {"connected": True, "on_ticket": {due[2]["id"]: "INC0010001 (attached: vc01.example.mil-machine-ssl-1a2b3c4d.csr)"}})
        vc = [c for c in r["certs"] if c["host"] == "vc01.example.mil"][0]
        self.assertEqual((vc["status"], vc["file_name"]), ("exists", "vc01.example.mil-machine-ssl-1a2b3c4d.csr"))
        self.assertNotIn("csr", vc)
        # no ServiceNow: no CSR (nothing would record it), unless asked; incidents only; a dry run
        r = vcf.vmware_cert_csr_result(due, [], {"connected": False})
        self.assertTrue(all(c["status"] == "skipped" for c in r["certs"]))
        self.assertIn("no ServiceNow API credential", [c for c in r["certs"] if c["host"] == "vc01.example.mil"][0]["reason"])
        r = vcf.vmware_cert_csr_result(due, [], {"connected": True, "make_csr": False})
        self.assertIn("vmware_cert_make_csr: false", [c for c in r["certs"] if c["host"] == "vc01.example.mil"][0]["reason"])
        r = vcf.vmware_cert_csr_result(due, [], {"connected": True, "check_mode": True})
        self.assertEqual([c["status"] for c in r["certs"]], ["would make", "skipped", "would make"])

    def test_report(self):
        p = vcf.vmware_cert_plan(self.certs(), {"warn_days": 30})
        r = vcf.vmware_cert_csr_result(p["due"], [], {"connected": True, "check_mode": True})
        doc = vcf.vmware_cert_report(p, r, [{"vcenter": "vc01.example.mil", "name": "esx09.example.mil", "why": "not connected (notResponding)"}],
                                     {"title": "All certificates", "rows": []}, {"warn_days": 30, "check_mode": True,
                                                                                  "tickets": {p["due"][0]["id"]: "INC0010009"}})
        self.assertEqual(doc["title"], "VMware certificates (dry run): 3 due within 30 days (1 expired), of 4, 1 not checked")
        self.assertEqual(doc["status"], "critical")
        due = doc["sections"][0]
        self.assertEqual(due["rows"][0][4], "INC0010009")
        self.assertEqual(due["rows"][0][5], "would make a CSR (dry run)")
        self.assertEqual(doc["sections"][-1]["rows"], [["esx09.example.mil", "vc01.example.mil", "not connected (notResponding)"]])

    def test_report_only(self):
        p = vcf.vmware_cert_plan(self.certs(), {"warn_days": 30})
        r = vcf.vmware_cert_csr_result(p["due"], [], {"report_only": True, "connected": True})
        self.assertEqual(set(c["status"] for c in r["certs"]), {"skipped"})
        self.assertIn("the report only", r["certs"][0]["reason"])
        doc = vcf.vmware_cert_report(p, r, [], None, {"warn_days": 30, "report_only": True})
        self.assertEqual(doc["title"], "VMware certificate report: 3 due within 30 days (1 expired), of 4")
        due = doc["sections"][0]
        self.assertEqual(due["columns"][-1], "VMware - certificate renewal would")
        self.assertEqual([row[-1] for row in due["rows"]], ["open an incident, and make a CSR on the host",
                                                           "open an incident: renew it in vCenter (VMCA), no CSR",
                                                           "open an incident, and make a CSR on vCenter"])
        self.assertNotIn("CSRs made", [s["label"] for s in doc["summary"]])



class EmailRecipients(unittest.TestCase):
    """report_email_to plus report_email_also (the "Also email the report to" box asked at launch)."""

    def test_also_is_added_never_in_place(self):
        self.assertEqual(f.email_recipients("a@example.mil, b@example.mil", "c@example.mil"), ["a@example.mil", "b@example.mil", "c@example.mil"])
        self.assertEqual(f.email_recipients([], "c@example.mil"), ["c@example.mil"])
        self.assertEqual(f.email_recipients(["a@example.mil"], ""), ["a@example.mil"])
        self.assertEqual(f.email_recipients(["a@example.mil"], None), ["a@example.mil"])
        self.assertEqual(f.email_recipients([], " "), [])

    def test_each_once_whatever_the_case(self):
        self.assertEqual(f.email_recipients("a@example.mil", ["A@EXAMPLE.mil", "b@example.mil", "B@example.mil"]),
                         ["a@example.mil", "b@example.mil"])

    def test_report_email_to_read_as_before(self):
        # report_email_to is not checked: a relay may take a local name (root); spaces, commas, semicolons
        self.assertEqual(f.email_recipients("root; ops@example.mil  x@example.mil"), ["root", "ops@example.mil", "x@example.mil"])
        self.assertEqual(f.email_recipients([" a@example.mil ", ""]), ["a@example.mil"])

    def test_outlook_and_text_forms(self):
        text = "Doe, Jane CIV USN (US) <jane.doe@example.mil>; Roe, Rick <rick.roe@example.mil>\nx@example.mil, y@example.mil z@example.mil"
        self.assertEqual(f.email_recipients([], text),
                         ["jane.doe@example.mil", "rick.roe@example.mil", "x@example.mil", "y@example.mil", "z@example.mil"])
        self.assertEqual(f.email_also_refused(text), [])

    def test_refused_with_why(self):
        r = f.email_also_refused("jane at example; c@example.com, @example.mil; a@b; ok@sub.example.mil", ["example.mil"])
        self.assertEqual(r, ["jane at example (no email address in it)", "c@example.com (not in report_email_also_domains: example.mil)",
                             "@example.mil (not an email address)", "a@b (not an email address)"])
        self.assertEqual(f.email_recipients([], "c@example.com, ok@sub.example.mil", "example.mil, @example.org"), ["ok@sub.example.mil"])
        # a domain is matched whole: example.mil is not in the domain "ample" + ".mil" (in two parts for tests/check_public.py)
        self.assertEqual(f.email_recipients([], "x@example.mil", ["ample" + ".mil"]), [])
        self.assertEqual(f.email_also_refused(None), [])


if __name__ == "__main__":
    unittest.main()
