#!/usr/bin/env python3
"""Unit tests for what leaves a host in podman discovery (1.79.18, roles/podman_discover/tasks/main.yml): the program that cuts podman's and systemd's
output down ON THE HOST is taken out of the role and run for real. The listing carries each container's Command (its arguments: passwords), the inspect
output its environment and healthcheck output, systemd's ExecStart the whole command line; none of it may come out. And that what discovery reads
of an ExecStart (the kind of unit, the container it runs) is the same after the cut as before. Placeholder data only.
    python3 tests/test_podman_discover_host.py"""
import json
import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "plugins", "filter"))
import site_filters as F  # noqa: E402

YML = open(os.path.join(HERE, "..", "roles", "podman_discover", "tasks", "main.yml")).read()
m = re.search(r"prog=\$\(cat <<'PY'\n(.*?)\n\s+PY\n", YML, re.S)
PROG = "\n".join(l[10:] if l.startswith("          ") else l for l in m.group(1).splitlines())


KNOWN_NAMES = "web1 web2 web3 web4 app web9 w"                  # the containers podman listed (PD_NAMES): a name from ExecStart must be one of them


def host(mode, text, names=KNOWN_NAMES):
    r = subprocess.run([sys.executable, "-c", PROG, mode], input=text, capture_output=True, text=True, env=dict(os.environ, PD_NAMES=names))
    return r.returncode, r.stdout


EXEC = [
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman run --rm -d -e DB_PASSWORD=exec-secret --name web2 -p 8080:80 registry.example.com/web2:1 ; ignore_errors=no ; start_time=[n/a] ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman run --name=web3 --env TOKEN=exec-secret registry.example.com/web3:1 --token exec-secret ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman container run --name web4 -e X=exec-secret img ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman start app ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman start --attach app ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman start --all ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman start -a ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman kube play --replace /etc/k/app.yaml --creds user:exec-secret ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman play kube /etc/k/app.yaml ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman pod start mypod ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman pod create --name p ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman auto-update ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman system prune -f ; ignore_errors=no ; }",
    "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman login -u u -p exec-secret reg ; ignore_errors=no ; }",
    "{ path=/usr/local/bin/podman-compose ; argv[]=/usr/local/bin/podman-compose -f /srv/c.yml up -d ; ignore_errors=no ; }",
    "{ path=/usr/bin/sleep ; argv[]=/usr/bin/sleep 10 ; ignore_errors=no ; }",
    "",
]


class Host(unittest.TestCase):
    def test_an_execstart_keeps_what_discovery_reads_and_no_argument(self):
        for ex in EXEC:
            rc, out = host("show", "Id=x.service\nExecStart=%s\nActiveState=active\n" % ex)
            self.assertEqual(rc, 0, ex)
            self.assertNotIn("exec-secret", out, ex)
            line = [l for l in out.splitlines() if l.startswith("ExecStart=")][0][len("ExecStart="):]
            self.assertEqual(F._exec_kind(line), F._exec_kind(ex), ex)                       # the kind of unit
            self.assertEqual(F._exec_name(line), F._exec_name(ex), ex)                       # the container it runs
            self.assertEqual("podman" in line, "podman" in ex, ex)                           # the unit-name guess
            before, after = F._exec_args(ex), F._exec_args(line)
            self.assertEqual(after[:1], before[:1], ex)                                      # the subcommand (run, start, kube, pod, auto-update ...)
            if before[:1] in (["kube"], ["play"], ["pod"]):
                self.assertEqual(after[:2], before[:2], ex)
            self.assertIn("ActiveState=active", out)                                         # the other lines pass through

    def test_the_listing_keeps_the_fields_read_and_no_command_or_label(self):
        ps = [{"Id": "a" * 64, "Names": ["web"], "Image": "r/web:1", "State": "running", "ExitCode": 0, "ExitedAt": -62135596800, "Status": "Up 1 hour (healthy)",
               "IsInfra": False, "Restarts": 2, "Command": ["/entrypoint.sh", "--password", "cmd-secret"], "Labels": {"PODMAN_SYSTEMD_UNIT": "web.service", "vendor.token": "label-secret"},
               "Mounts": ["/srv/x"], "Ports": [{"host_port": 1}]}]
        rc, out = host("ps", json.dumps(ps))
        self.assertEqual(rc, 0)
        for bad in ("cmd-secret", "label-secret", "entrypoint", "Mounts", "Ports"):
            self.assertNotIn(bad, out)
        got = json.loads(out)[0]
        self.assertEqual((got["Id"], got["Names"], got["State"], got["Restarts"], got["Labels"]), ("a" * 64, ["web"], "running", 2, {"PODMAN_SYSTEMD_UNIT": "web.service"}))
        self.assertEqual(host("ps", "null"), (0, "[]"))
        self.assertEqual(host("ps", "")[1], "[]")
        self.assertEqual(host("ps", "[not json")[0], 1)                                       # a bad read is an error, never an empty list

    def test_the_inspect_keeps_the_state_and_no_environment(self):
        c = {"Id": "b" * 64, "ImageName": "r/x:1", "RestartCount": 3, "Config": {"Env": ["DB_PASSWORD=hunter2"]}, "HostConfig": {"RestartPolicy": {"Name": "always"}, "Binds": ["/s:/x"]},
             "State": {"Status": "running", "ExitCode": 0, "OOMKilled": False, "Health": {"Status": "unhealthy", "Log": [{"Output": "token=hc-secret"}]}}}
        rc, out = host("inspect", json.dumps([c]))
        self.assertEqual(rc, 0)
        for bad in ("hunter2", "hc-secret", "Binds", "Env"):
            self.assertNotIn(bad, out)
        got = json.loads(out)[0]
        self.assertEqual((got["State"]["Health"], got["HostConfig"]["RestartPolicy"]["Name"], got["RestartCount"]), ({"Status": "unhealthy"}, "always", 3))
        c["State"] = {"Status": "running", "Healthcheck": {"Status": "healthy", "Log": [{"Output": "token=hc-secret"}]}}                     # podman 3
        self.assertEqual(json.loads(host("inspect", json.dumps([c]))[1])[0]["State"], {"Status": "running", "Healthcheck": {"Status": "healthy"}})

    def test_a_global_option_before_the_verb_leaves_nothing(self):
        """`podman --env=HTTPS_PROXY=http://user:exec-secret@proxy run ...`: a global option holds a credential; only a recognized verb leaves the host."""
        for ex in ("{ path=/usr/bin/podman ; argv[]=/usr/bin/podman --env=HTTPS_PROXY=http://u:exec-secret@p run --name web1 img ; }",
                   "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman --root /srv/exec-secret run --name web1 img ; }",
                   "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman exec-secret-verb run --name web1 img ; }",
                   "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman kube exec-secret /x.yaml ; }",
                   "{ path=/usr/bin/podman ; argv[]=/usr/bin/podman pod exec-secret mypod ; }"):
            rc, out = host("show", "ExecStart=%s\n" % ex)
            self.assertEqual(rc, 0)
            self.assertNotIn("exec-secret", out, ex)
        _, out = host("show", "ExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/podman --log-level=debug run --name web1 img ; }\n")
        self.assertEqual(F._exec_name(out.split("ExecStart=", 1)[1]), "web1")                  # a global option with its value inline is passed over
        _, out = host("show", "ExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/podman --root /srv/x run --name web1 img ; }\n")
        self.assertEqual(F._exec_name(out.split("ExecStart=", 1)[1]), "")                      # one that may take a separate value: not known, nothing said

    def test_a_name_option_of_the_application_is_not_the_containers_name(self):
        """`podman run IMAGE --name TOKEN`: after the image the arguments are the application's."""
        for ex, want in (("podman run --rm img --name exec-secret-token", ""),
                         ("podman run --rm --name web9 img --name exec-secret-token", "web9"),
                         ("podman run --name=web9 img", "web9"),
                         ("podman run -e A=B img --name=exec-secret-token", ""),
                         ("podman run --weird exec-secret-token --name web9 img", ""),                  # an option the table does not know: where the options end is unknown
                         ("podman run -p 80:80 -v /a:/b --name web9 img", "web9"),
                         ("podman run --name 'x;touch' img", ""),                                      # no container name
                         ("podman run --name $(id) img", "")):
            rc, out = host("show", "ExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/%s ; }\n" % ex)
            self.assertEqual(rc, 0)
            self.assertNotIn("exec-secret", out, ex)
            self.assertEqual(F._exec_name(out.split("ExecStart=", 1)[1]), want, ex)

    def test_systemctl_show_gives_only_what_discovery_reads(self):
        text = ("Id=web.service\nLoadState=loaded\nActiveState=active\nEnvironment=DB_PASSWORD=exec-secret\nExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/podman run -e P=exec-secret --name w img ; }\n"
                "FragmentPath=/etc/systemd/system/web.service\nSomethingNew=exec-secret\n\nId=b.service\nUnit b.service could not be found.\n")
        rc, out = host("show", text)
        self.assertEqual(rc, 0)
        self.assertNotIn("exec-secret", out)
        for kept in ("Id=web.service", "LoadState=loaded", "ActiveState=active", "FragmentPath=/etc/systemd/system/web.service", "Id=b.service"):
            self.assertIn(kept, out)
        self.assertNotIn("could not be found", out)                                         # 1.79.18: a line that is no allowed property is dropped

    def test_a_name_must_be_a_listed_container_or_the_units_own(self):
        """1.79.18 (Codex): ExecStart text is space-joined, so `-e MESSAGE=use --name private-token` reads like a --name option; and a
        multi-line argument continues on lines with no property name. Neither may leave the host."""
        text = ("Id=web9.service\nExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/podman run --rm -e MESSAGE=use --name private-token img ; }\n"
                "a continuation line with exec-secret\n\n"
                "Id=container-web7.service\nExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/podman run --name web7 img ; }\n")
        rc, out = host("show", text, names="")
        self.assertEqual(rc, 0)
        self.assertNotIn("private-token", out)
        self.assertNotIn("exec-secret", out)
        self.assertIn("--name web7", out)                                                   # the unit's own name: container-web7.service
        rc, out = host("show", text, names="private-token")
        self.assertIn("--name private-token", out)                                          # only when podman listed such a container

    def test_the_unit_names_are_filtered_and_come_after_two_dashes(self):
        self.assertIn("-- $units", YML)                                                      # after the end of the options
        m2 = re.search(r"grep -E '(\^\[A-Za-z0-9_.@:\]\[A-Za-z0-9:_.@-\]\*\$)'", YML)
        self.assertTrue(m2)
        rx = re.compile(m2.group(1))
        for ok in ("web.service", "container-web.service", "podman-kube@x.service", "a:b.service"):
            self.assertTrue(rx.match(ok), ok)
        for bad in ("-p", "--now", "bad;touch", "a b", "$(id)", "x`id`", "*", "a/b.service", ""):
            self.assertFalse(rx.match(bad), bad)

    def test_the_evidence_heading_is_data(self):
        """The heading holds a label a user's Quadlet chose: one quoted argument of a fixed printf, never shell source."""
        import jinja2
        ev = open(os.path.join(HERE, "..", "roles", "service_watch", "tasks", "evidence.yml")).read()
        line = [l.strip() for l in ev.splitlines() if "===== container" in l][0]
        env = jinja2.Environment()
        import shlex
        env.filters["quote"] = shlex.quote
        for label in ("web", "ev$(touch /tmp/pwn-heading)", "a`id`b", "x; id", "q'uote"):
            cmd = env.from_string(line).render(_watch_m={"label": label}, service_watch={"states": {label: "down"}})
            r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(r.stdout, "===== container %s: down =====\n" % label, label)
            self.assertEqual(r.stderr, "")

    def test_the_owner_is_quoted_in_the_prefix(self):
        self.assertEqual(F.podman_run_as("root"), "")
        self.assertEqual(F.podman_run_as("alice", 1001), "runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 ")
        self.assertEqual(F.podman_run_as("a b; reboot", 5), "runuser -u 'a b; reboot' -- env XDG_RUNTIME_DIR=/run/user/5 ")
        self.assertEqual(F.podman_run_as("x", "1001; id"), "runuser -u x -- env XDG_RUNTIME_DIR=/run/user/'1001; id' ")
        self.assertEqual(F.podman_run_as("x", "UID"), "runuser -u x -- env XDG_RUNTIME_DIR=/run/user/UID ")           # an owner that was not found


if __name__ == "__main__":
    unittest.main()
