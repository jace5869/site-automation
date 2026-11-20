#!/usr/bin/env python3
"""Stand-ins for podman, systemctl, runuser, ps, journalctl, id, getent and loginctl, for
tests/containers/run_containers_test.sh. One file; what it does depends on the name it is called by
(the test writes bin/podman, bin/systemctl ... wrappers that call it with --tool NAME). Not a podman: only what the roles need.

    python3 fakes.py setup FIXTURE.json WORKDIR     make WORKDIR: bin/ links, state.json, files

The host it pretends to be is WORKDIR/state.json (from a fixture in tests/containers/fixtures/):
  users:  {name: {uid, home (relative to WORKDIR), linger, runtime, storage, subuid, conmon}}
  owners: {owner: {containers: [...], units: {...}}} - owner is root or a user name
    container: name, state (running | exited | created), exit, exited_ago (seconds), unit (its
               PODMAN_SYSTEMD_UNIT label), health, podman (3 = State.Healthcheck, 4 = State.Health),
               oom, policy, restarts, image
    unit:      LoadState, ActiveState, SubState, Result, UnitFileState, NRestarts, ExecStart, WantedBy,
               creates (the container a start creates, as a unit-run container does)
  files:  {path relative to WORKDIR: content} (Quadlet files, unit files)
Every call is logged to WORKDIR/calls.log as "OWNER XDG_RUNTIME_DIR argv...". A start / restart
changes state.json, so a re-check sees the result. $FAKE_PODMAN_FAIL=OWNER makes that owner's
podman fail; $FAKE_USERNS_OFF=1 makes every user's podman fail as with user.max_user_namespaces = 0;
$FAKE_NOW is "now" (seconds since 1970)."""
import json
import re
import os
import sys

WORK = os.environ.get("FAKE_DIR", "")


def load():
    with open(os.path.join(WORK, "state.json")) as fh:
        return json.load(fh)


def save(st):
    with open(os.path.join(WORK, "state.json"), "w") as fh:
        json.dump(st, fh, indent=1)


def log(owner, argv):
    with open(os.path.join(WORK, "calls.log"), "a") as fh:
        fh.write("%s %s %s\n" % (owner, os.environ.get("XDG_RUNTIME_DIR", "-"), " ".join(argv)))


def now():
    return int(os.environ.get("FAKE_NOW", "1790000000"))


def cid(owner, name):
    return (("%s-%s" % (owner, name)).encode().hex() * 8)[:64]


def ps_json(owner, c):
    exited_at = now() - int(c["exited_ago"]) if c.get("exited_ago") is not None else -62135596800
    labels = dict({"PODMAN_SYSTEMD_UNIT": c["unit"]} if c.get("unit") else {}, **{"vendor.token": "label-secret-" + c["name"]})
    status = "Up 1 hour" + ((" (%s)" % c["health"]) if c.get("health") else "") if c.get("state") == "running" \
        else "Exited (%s) 1 hour ago" % c.get("exit", 0)
    return {"Id": cid(owner, c["name"]), "Names": [c["name"]], "Image": c.get("image", "registry.example.com/%s:1" % c["name"]),
            "State": c.get("state", "running"), "ExitCode": int(c.get("exit", 0)), "Exited": c.get("state") == "exited",
            "ExitedAt": exited_at, "Labels": labels, "Status": status, "IsInfra": False, "Restarts": int(c.get("restarts", 0)),
            "Command": ["/entrypoint.sh", "--password", "cmd-secret-" + c["name"]]}


def inspect_json(owner, c):
    state = {"Status": c.get("state", "running"), "Running": c.get("state") == "running", "OOMKilled": bool(c.get("oom")),
             "Pid": 0 if c.get("gone") else int(c.get("pid", 0)),
             "ExitCode": int(c.get("exit", 0)), "FinishedAt": "2026-09-30T10:00:00.000000000Z"}
    if c.get("health"):
        state["Healthcheck" if int(c.get("podman", 4)) < 4 else "Health"] = {"Status": c["health"], "FailingStreak": 3,
                                                                              "Log": [{"Output": "token=hc-secret-" + c["name"]}]}
    if c.get("up_ago") is not None:                           # 1.84.0: StartedAt, for the uptime of the NetApp Console profile
        import datetime
        state["StartedAt"] = datetime.datetime.fromtimestamp(now() - int(c["up_ago"]), tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    return {"Id": cid(owner, c["name"]), "Name": c["name"], "State": state, "RestartCount": int(c.get("restarts", 0)),
            "Mounts": [{"Type": "bind", "Source": m, "Destination": "/x"} for m in c.get("mounts", [])],
            "ImageName": c.get("image", ""), "HostConfig": {"RestartPolicy": {"Name": c.get("policy", ""), "MaximumRetryCount": 0}},
            "Config": {"Labels": {"PODMAN_SYSTEMD_UNIT": c["unit"]} if c.get("unit") else {},
                       "Env": ["DB_PASSWORD=hunter2-" + c["name"], "JWT_SECRET=s3cr3t-" + c["name"]]}}


def find(o, ref):
    for c in o.get("containers", []):
        if ref in (c["name"], cid_of(o, c)) or cid_of(o, c).startswith(ref):
            return c
    return None


def cid_of(o, c):
    return cid(o["_owner"], c["name"])


def owner_state(st, owner):
    o = st["owners"].setdefault(owner, {"containers": [], "units": {}})
    o["_owner"] = owner
    return o


def podman(argv):
    owner = os.environ.get("FAKE_OWNER", "root")
    log(owner, ["podman"] + argv)
    if argv[:1] == ["--version"]:
        print("podman version " + os.environ.get("FAKE_PODMAN_VERSION", "4.9.4"))
        return 0
    if os.environ.get("FAKE_PODMAN_FAIL") == owner:
        sys.stderr.write("Error: creating runtime static files: mkdir /run/user/x: permission denied\n")
        return 125
    if os.environ.get("FAKE_USERNS_OFF") and owner != "root":     # user.max_user_namespaces = 0 (a STIG setting)
        sys.stderr.write("cannot clone: No space left on device\nuser namespaces are not enabled in "
                         "/proc/sys/user/max_user_namespaces\nError: cannot re-exec process\n")
        return 125
    st = load()
    o = owner_state(st, owner)
    args = argv[1:] if argv[:1] == ["container"] else argv
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    if cmd == "ps":
        fmt = rest[rest.index("--format") + 1] if "--format" in rest else ""
        items = o.get("containers", [])
        if "--filter" in rest:
            want = rest[rest.index("--filter") + 1].split("=", 2)
            items = [c for c in items if want[0] == "label" and c.get("unit") == want[2]]
        if fmt == "json":
            print(json.dumps([ps_json(owner, c) for c in items], indent=4))
        elif fmt.startswith("{{.ID}}|"):
            for c in items:
                print("%s|%s|%s" % (cid(owner, c["name"]), c["name"], c.get("unit", "")))
        elif fmt == "{{.Names}}":
            for c in items:
                print(c["name"])
        return 0
    if cmd == "exists":
        return 0 if find(o, rest[0]) else 1
    if cmd == "inspect":
        fmt = rest[rest.index("--format") + 1] if "--format" in rest else ""
        refs = [r for r in rest if r != "--format" and r != fmt]
        found = [find(o, r) for r in refs]
        if "range .Mounts" in fmt:                              # 1.84.0: the sources of the mounts of all of them, one a line
            for c in found:
                for m in (c or {}).get("mounts", []):
                    print(m)
            return 0
        if "State.Pid" in fmt:                                  # 1.84.0: the main process id (0 when it is gone)
            c = found[0] if found else None
            print(0 if (c or {}).get("gone") else int((c or {}).get("pid", 4000)))
            return 0 if c else 125
        if "NetworkSettings.Networks" in fmt:                   # 1.84.0: the container's address
            print("127.0.0.1 ")
            return 0
        if fmt:
            c = found[0] if found else None
            if not c:
                sys.stderr.write('Error: no such object: "%s"\n' % (refs[0] if refs else ""))
                return 125
            if "{{.State.Status}}|" in fmt:                     # the Service watch's state read: status|exit code|oom|finished
                stt = inspect_json(owner, c)["State"]
                print("%s|%s|%s|%s" % (stt["Status"], stt["ExitCode"], "true" if stt["OOMKilled"] else "false", stt["FinishedAt"]))
                return 0
            for field, other in (("State.Health.Status", "Healthcheck"), ("State.Healthcheck.Status", "Health")):
                if "{{.%s}}" % field in fmt:                    # podman 3 has no .State.Health, podman 4 no .State.Healthcheck: the template fails
                    stt = inspect_json(owner, c)["State"]
                    if other in stt:
                        sys.stderr.write("Error: template: can't evaluate field %s\n" % field.split(".")[1])
                        return 125
                    print((stt.get(field.split(".")[1]) or {}).get("Status", ""))
                    return 0
            if "PODMAN_SYSTEMD_UNIT" in fmt:
                print(c.get("unit") or "<no value>")
            elif "json .State" in fmt:
                print(json.dumps(inspect_json(owner, c)["State"]))
            return 0
        fail = os.path.join(WORK, "inspect_fail")                           # a test: the Console's read of its containers fails (a timeout) or is cut short
        if os.path.exists(fail) and any(r.startswith("ds_") for r in refs):
            if open(fail).read().strip() == "partial":
                print(json.dumps([inspect_json(owner, c) for c in found if c][:1], indent=4))
            sys.stderr.write("Error: timed out\n")
            return 125
        print(json.dumps([inspect_json(owner, c) for c in found if c], indent=4))
        if os.path.exists(os.path.join(WORK, "inspect_partial")) and not any(r.startswith("ds_") for r in refs):    # a container went away meanwhile
            sys.stderr.write("Error: no such container\n")
            return 125
        if not all(found):
            sys.stderr.write("Error: no such container\n")
            return 125
        return 0
    if cmd == "volume" and rest[:1] == ["inspect"]:                    # 1.84.0: the certificate volume's Mountpoint
        mp = st.get("console", {}).get("volume_mountpoint")
        if not mp:
            sys.stderr.write("Error: no such volume %s\n" % rest[1])
            return 125
        print(mp.replace("{WORK}", WORK))
        return 0
    if cmd == "exec":                                                  # podman exec NAME printenv VAR
        c = find(o, rest[0])
        if not c or rest[1:2] != ["printenv"]:
            return 125
        v = st.get("console", {}).get("env", {}).get(rest[2])
        if v is None:
            return 1
        print(v)
        return 0
    if cmd == "info":
        print(st.get("console", {}).get("graphroot", "/var/lib/containers/storage"))
        return 0
    if cmd == "logs":
        c = find(o, rest[-1])
        if not c:
            sys.stderr.write("Error: no container with name or ID %s found\n" % rest[-1])
            return 125
        if c["name"] in st.get("console", {}).get("logs", {}):
            print(st["console"]["logs"][c["name"]])
            if os.path.exists(os.path.join(WORK, "more_logs")):          # a test: one more error line between two passes
                print("2026-09-30T10:20:00Z ERROR jwt token validation failed: signature is invalid")
            return 0
        print("fake log line of %s (owner %s): database connection refused" % (c["name"], owner))
        return 0
    if cmd in ("start", "restart"):
        rc = 0
        for n in rest:
            c = find(o, n)
            if not c:
                sys.stderr.write("Error: no container with name or ID %s found\n" % n)
                rc = 125
                continue
            c["state"], c["exit"], c["health"] = "running", 0, ("healthy" if c.get("health") else "")
        save(st)
        return rc
    sys.stderr.write("fake podman: not supported: %s\n" % " ".join(argv))
    return 125


def systemctl(argv):
    user = "--user" in argv
    owner = os.environ.get("FAKE_OWNER", "root") if user else "root"
    log(owner, ["systemctl"] + argv)
    st = load()
    if user and st.get("users", {}).get(owner, {}).get("systemd_fail"):
        sys.stderr.write("Failed to connect to bus: No medium found\n")
        return 1
    o = owner_state(st, owner)
    args = [a for a in argv if a not in ("--user", "--no-pager", "-l", "--plain", "--no-legend")]
    verb, rest = args[0], args[1:]
    if verb == "show" and "--value" in argv and "LoadState" in " ".join(argv):          # 1.84.0: is the unit there
        svc = st.get("console", {}).get("services", {}).get(rest[0].replace(".service", "")) if rest else None
        print("loaded" if svc is not None else "not-found")
        return 0
    if verb in ("is-active", "is-enabled"):                    # 1.84.0: fapolicyd, from the fixture
        svc = st.get("console", {}).get("services", {}).get(rest[0].replace(".service", ""))
        if svc is None:
            print("inactive" if verb == "is-active" else "not-found")
            return 3 if verb == "is-active" else 1
        print(svc["active" if verb == "is-active" else "enabled"])
        return 0 if svc["active" if verb == "is-active" else "enabled"] in ("active", "enabled") else 3
    if verb == "--failed":
        for u, info in sorted(o.get("units", {}).items()):
            if info.get("ActiveState") == "failed":
                print("%s loaded failed failed fake unit" % u)
        return 0
    if verb == "show":
        props, units, i = [], [], 0
        while i < len(rest):
            if rest[i] == "-p":
                props += rest[i + 1].split(",")
                i += 2
            elif rest[i] == "--":
                i += 1
            else:
                units.append(rest[i])
                i += 1
        blocks = []
        for u in units:
            info = dict(o.get("units", {}).get(u) or {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead"})
            info.setdefault("Result", "success")
            info.setdefault("NRestarts", "0")
            if "ExecStart" in info and "FragmentPath" not in info:
                # 1.79.18: a real unit has a file; discovery reads its ExecStart there for the true argument boundaries
                m = re.search(r"argv\[\]=([^;]*)", info["ExecStart"])
                d = os.path.join(WORK, "unitfiles", owner)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, u), "w") as fh:
                    fh.write("[Service]\nExecStart=%s\n" % (m.group(1).strip() if m else ""))
                info["FragmentPath"] = os.path.join(d, u)
            lines = ["Id=" + u] if (not props or "Id" in props) else []
            for p in props or info:
                if p != "Id" and p in info and p != "creates":
                    lines.append("%s=%s" % (p, info[p]))
            blocks.append("\n".join(lines))
        print("\n\n".join(blocks))
        return 0
    if verb == "status":
        info = o.get("units", {}).get(rest[0])
        print("* %s - fake unit\n   Active: %s (%s)" % (rest[0], (info or {}).get("ActiveState", "inactive"), (info or {}).get("SubState", "dead")))
        return 0 if info and info.get("ActiveState") == "active" else 3
    if verb in ("start", "restart"):
        info = o.get("units", {}).get(rest[0])
        if not info or info.get("LoadState") == "not-found":
            sys.stderr.write("Failed to start %s: Unit %s not found.\n" % (rest[0], rest[0]))
            return 5
        info.update({"ActiveState": "active", "SubState": "running", "Result": "success"})
        name = info.get("creates")
        if name:
            c = next((c for c in o["containers"] if c["name"] == name), None)
            if c is None:
                c = {"name": name, "unit": rest[0]}
                o["containers"].append(c)
            c.update({"state": "running", "exit": 0, "exited_ago": None})
        save(st)
        return 0
    if verb in ("reset-failed", "daemon-reload"):
        return 0
    sys.stderr.write("fake systemctl: not supported: %s\n" % " ".join(argv))
    return 1


def runuser(argv):
    """runuser -u USER -- [env K=V ...] CMD ...: run CMD as USER (here: with FAKE_OWNER=USER)."""
    if argv[:1] != ["-u"] or len(argv) < 4 or argv[2] != "--":
        sys.stderr.write("fake runuser: only runuser -u USER -- CMD\n")
        return 1
    user, rest = argv[1], argv[3:]
    if user not in load().get("users", {}):
        sys.stderr.write("runuser: user %s does not exist\n" % user)
        return 1
    env = dict(os.environ, FAKE_OWNER=user)
    if rest[:1] == ["env"]:
        rest = rest[1:]
        while rest and "=" in rest[0] and not rest[0].startswith("-"):
            k, v = rest[0].split("=", 1)
            env[k] = v
            rest = rest[1:]
    os.execvpe(rest[0], rest, env)


def loginctl(argv):
    log(os.environ.get("FAKE_OWNER", "root"), ["loginctl"] + argv)
    st = load()
    if argv[:1] == ["enable-linger"] and len(argv) == 2:
        u = st.get("users", {}).get(argv[1])
        if not u:
            sys.stderr.write("Could not enable linger: No such user %s\n" % argv[1])
            return 1
        u["linger"], u["runtime"] = 1, 1          # logind starts the user's systemd: /run/user/UID appears
        open(os.path.join(WORK, "linger", argv[1]), "w").close()
        os.makedirs(os.path.join(WORK, "run/user", str(u["uid"])), exist_ok=True)
        save(st)
        return 0
    if argv[:1] == ["show-user"]:
        print("Linger=no")
        return 0
    sys.stderr.write("fake loginctl: not supported: %s\n" % " ".join(argv))
    return 1


def ps(argv):
    for name, u in sorted(load().get("users", {}).items()):
        if u.get("conmon"):
            print("%5d conmon" % u["uid"])
    print("    0 conmon")
    print("    0 systemd")
    return 0


def journalctl(argv):
    log(os.environ.get("FAKE_OWNER", "root"), ["journalctl"] + argv)
    if "-u" in argv and argv[argv.index("-u") + 1] == "fapolicyd":
        for line in load().get("console", {}).get("fapolicyd_denials", []):
            print(line)
        return 0
    print("Sep 30 10:00:00 host01 systemd[1]: fake journal line")
    return 0


def console_tool(name, argv):
    """1.84.0: chronyc, auditctl, getenforce, curl, openssl, df - what the NetApp Console profile reads, from the fixture's "console" part."""
    log("root", [name] + argv)
    c = load().get("console", {})
    if name == "chronyc":
        print(c.get("chrony", ""), end="")
    elif name == "auditctl":
        print(c.get("auditctl", ""), end="")
    elif name == "getenforce":
        print(c.get("getenforce", ""))
    elif name == "curl":
        print(c.get("curl", "000 0.0"), end="")
    elif name == "openssl":
        if argv[:1] == ["x509"] and "-in" in argv:                      # the certificate file of the volume
            if c.get("cert_file_end"):
                print("notAfter=" + c["cert_file_end"])
        elif argv[:1] == ["x509"] and c.get("ssl_end"):
            print("notAfter=" + c["ssl_end"])
        elif argv[:1] == ["s_client"]:
            print("CONNECTED(00000003)")
    elif name == "df":
        row = c.get("df", {}).get(argv[-1])
        if not row:
            sys.stderr.write("df: %s: No such file or directory\n" % argv[-1])
            return 1
        print("Filesystem 1024-blocks Used Available Capacity Mounted on")
        print("/dev/fake %d %d %d %d%% %s" % (row["blocks"], row["blocks"] - row["avail"], row["avail"], row["pct"], row["mount"]))
    return 0


def passwd_line(name_or_uid):
    for name, u in load().get("users", {}).items():
        if name_or_uid in (name, str(u["uid"])):
            return "%s:x:%d:%d::%s:/bin/bash" % (name, u["uid"], u["uid"], os.path.join(WORK, u["home"]))
    return None


def real(tool, argv):
    mine = os.path.realpath(os.path.join(WORK, "bin"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, tool)
        if os.path.realpath(d) != mine and os.access(p, os.X_OK):
            os.execv(p, [tool] + argv)
    return 127


def getent(argv):
    if argv[:1] == ["passwd"] and len(argv) == 2:
        line = passwd_line(argv[1])
        if line:
            print(line)
            return 0
        if argv[1] in load().get("fake_only", []):
            return 2
    return real("getent", argv)


def id_(argv):
    if argv[:1] == ["-u"] and len(argv) == 2:
        line = passwd_line(argv[1])
        if line:
            print(line.split(":")[2])
            return 0
    return real("id", argv)


def setup(fixture, work):
    with open(fixture) as fh:
        st = json.load(fh)
    os.makedirs(os.path.join(work, "bin"), exist_ok=True)
    for tool in TOOLS:                   # small wrappers, executable whatever the repo file's mode is
        path = os.path.join(work, "bin", tool)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\nexec %s %s --tool %s \"$@\"\n" % (sys.executable, os.path.abspath(__file__), tool))
        os.chmod(path, 0o755)
    for d in ("linger", "run/user", "etc/containers/systemd/users", "usr/share/containers/systemd", "etc/systemd/system"):
        os.makedirs(os.path.join(work, d), exist_ok=True)
    subuid = []
    for name, u in st.get("users", {}).items():
        home = os.path.join(work, u["home"])
        os.makedirs(home, exist_ok=True)
        if u.get("linger"):
            open(os.path.join(work, "linger", name), "w").close()
        if u.get("runtime"):
            os.makedirs(os.path.join(work, "run/user", str(u["uid"])), exist_ok=True)
        if u.get("storage"):
            os.makedirs(os.path.join(home, ".local/share/containers/storage"), exist_ok=True)
        if u.get("subuid"):
            subuid.append("%s:%d:65536" % (name, 100000 + 65536 * len(subuid)))
    with open(os.path.join(work, "subuid"), "w") as fh:
        fh.write("\n".join(subuid) + "\n")
    for o in st.get("owners", {}).values():                  # 1.84.0: /proc/PID of every container that is not "gone" (the role's _con_proc points here)
        for c in o.get("containers", []):
            if c.get("pid") and not c.get("gone"):
                os.makedirs(os.path.join(work, "proc", str(c["pid"])), exist_ok=True)
    mp = st.get("console", {}).get("volume_mountpoint")
    if mp:
        os.makedirs(mp.replace("{WORK}", work), exist_ok=True)
    for rel, content in st.get("files", {}).items():
        path = os.path.join(work, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(content)
    for name in ("state.json",):
        with open(os.path.join(work, name), "w") as fh:
            json.dump(st, fh, indent=1)
    open(os.path.join(work, "calls.log"), "w").close()
    return 0


TOOLS = ("podman", "systemctl", "runuser", "ps", "journalctl", "id", "getent", "loginctl", "chronyc", "auditctl", "getenforce", "curl", "openssl", "df")


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["setup"]:
        return setup(argv[1], argv[2])
    if argv[:1] != ["--tool"] or len(argv) < 2 or argv[1] not in TOOLS:
        sys.stderr.write("usage: fakes.py setup FIXTURE WORKDIR | fakes.py --tool TOOL ARGS\n")
        return 2
    tool, argv = argv[1], argv[2:]
    if argv == ["--is-fake"]:           # the test runner checks this before it runs anything
        print("FAKE " + tool)
        return 0
    return {"podman": podman, "systemctl": systemctl, "runuser": runuser, "ps": ps, "journalctl": journalctl,
            "id": id_, "getent": getent, "loginctl": loginctl}.get(tool, lambda a: console_tool(tool, a))(argv)


if __name__ == "__main__":
    sys.exit(main())
