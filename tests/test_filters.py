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

    def test_ds_row(self):
        d = {"name": "ds1", "datacenter": "DC1", "cluster": "", "type": "VMFS", "capacity_gb": 100.0, "free_gb": 5.0,
             "used_pct": 95.0, "provisioned_pct": 120.0, "accessible": True, "maintenance": "normal", "hosts": 3, "vms": 9}
        self.assertEqual(f.vm_ds_row(d), ["ds1", "DC1", "", "VMFS", 100.0, 5.0, 95.0, 120.0, 3, 9, "ok"])
        self.assertEqual(f.vm_ds_row(dict(d, accessible=False))[-1], "inaccessible")
        self.assertEqual(f.vm_ds_row(dict(d, maintenance="inMaintenance"))[-1], "maintenance")

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
          "hostname": "aap01.example.mil", "ip": "10.0.0.5"},
         {"vm": "odd", "moid": "vm-8", "id": 1, "name": "s", "description": "", "age_days": 10.0, "size_gb": None}]
    RX = "(?i)keep|do.?not.?delete"

    def test_split(self):
        r = f.vm_snapshot_split(self.S, 3, self.RX, ["dc*", "vcsa01"], ["aap01", "10.0.0.5"], 1024)
        self.assertEqual([x["vm"] for x in r["all"]][:3], ["db01", "files01", "DC01"])
        self.assertEqual([(x["vm"], x["id"]) for x in r["old"]], [("web01", 1), ("app02", 3)])
        self.assertEqual({x["vm"]: x["reason"] for x in r["held"]}, {
            "db01": "its name or description says keep", "files01": "bigger than 1.00 TB: delete it by hand, at a quiet time",
            "DC01": "the VM is on the exclusion list", "aapvm": "the VM is the AAP server", "odd": "its size could not be read",
            "app01": "its name or description says keep"})
        # without the size limit an unknown size does not hold a snapshot back
        self.assertIn("odd", [x["vm"] for x in f.vm_snapshot_split(self.S, 3, self.RX)["old"]])

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

    def test_name_matches(self):
        self.assertTrue(f.name_matches("DC01", ["dc*"]))
        self.assertTrue(f.name_matches("vcsa01", ["VCSA01"]))
        self.assertFalse(f.name_matches("adc01", ["dc*"]))
        self.assertFalse(f.name_matches("x", ["", " "]))


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
                     "category": "info", "message": "Cannot login svc_scan@10.9.9.9", "user": "", "login_user": "svc_scan", "ip": "10.9.9.9",
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
                         [("svc_scan", "10.9.9.9", "esx01", 3, "critical", "01", "03")])
        self.assertEqual([(c["host"], c["state"], c["events"], c["severity"]) for c in g["connection"]], [("esx02", "notResponding", 1, "critical")])
        self.assertEqual([e["vm"] for e in g["vm"]], ["db01", "app01"])                        # newest first
        self.assertEqual([(x["type"], x["count"], x["message"]) for x in g["other"]], [("esx.problem.storage.latency", 2, "latency 101 ms")])
        self.assertEqual(va.vm_alarm_groups(self.D)["logins"][0]["severity"], "warning")       # 3 < 10

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


def _t(iso):
    return iso[11:13]


if __name__ == "__main__":
    unittest.main()
