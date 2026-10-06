"""Unit tests for plugins/filter/site_filters.py (standard library only: python3 tests/test_filters.py)."""
import datetime
import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "filter"))
import site_filters as f  # noqa: E402


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
          "guest": {"hostName": "WEB01.example.mil", "ipAddress": "10.1.2.3", "toolsRunningStatus": "guestToolsRunning",
                    "net": [{"ipAddress": ["10.1.2.3", "fe80::1"]}]}}

    def test_record(self):
        r = f.vm_facts(self.VM, {"moid": "vm-42", "hw_folder": "/DC1/vm/web", "hw_esxi_host": "esx01"})
        self.assertEqual((r["moid"], r["datacenter"], r["folder"], r["esxi_host"], r["ip"]),
                         ("vm-42", "DC1", "/DC1/vm/web", "esx01", "10.1.2.3"))
        self.assertEqual(f.vm_facts(self.VM, {"hw_folder": "/Sites/East/DC2/vm"})["datacenter"], "DC2")
        self.assertIsNone(f.vm_facts(self.VM)["datacenter"])
        self.assertEqual((r["name"], r["power"], r["firmware"], r["secure_boot"], r["template"]),
                         ("Web01.example.mil", "poweredOn", "efi", True, False))
        self.assertEqual(r["ids"], ["10.1.2.3", "fe80::1", "web01"])
        self.assertEqual([(n["index"], n["adapter_type"], n["network"], n["kind"]) for n in r["nics"]],
                         [(1, "vmxnet3", "dvportgroup-12", "dvs"), (2, "e1000e", "network-7", "standard")])

    def test_empty_and_bios(self):
        r = f.vm_facts({"name": "x", "config": {"firmware": "bios", "bootOptions": {"efiSecureBootEnabled": None}}})
        self.assertEqual((r["secure_boot"], r["nics"], r["ids"], r["annotation"]), (False, [], ["x"], ""))
        self.assertEqual(f.vm_facts(None)["ids"], [])

    def test_where(self):
        r = f.vm_facts(self.VM, {"hw_folder": "/DC1/vm/web", "hw_esxi_host": "esx01"})
        self.assertEqual(f.vm_where(r), "Web01.example.mil  folder /DC1/vm/web  on esx01  guest WEB01.example.mil 10.1.2.3  (poweredOn, )".replace("(poweredOn, )", "(poweredOn, ?)"))
        self.assertEqual(f.vm_where({"name": "x"}), "x  (?, ?)")

    def test_host_ids(self):
        hv = {"aap01.example.mil": {"ansible_host": "10.0.0.5"}, "aap02": {}}
        self.assertEqual(f.host_ids(["aap01.example.mil", "aap02"], hv), ["10.0.0.5", "aap01", "aap02"])
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


if __name__ == "__main__":
    unittest.main()
