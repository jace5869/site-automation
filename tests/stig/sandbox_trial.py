#!/usr/bin/env python3
"""1.76.0: the answer keys' trial sandbox (roles/check_stig/library/site_stig_answer_host.py, systemd-run) holds even when
the read-only check is wrong. This test calls the module's own run_trial() directly - the check is left out HERE, never
in the module - with codes that write: the three Codex found (Attributes++, rpm -D/-E with a shell macro, sysctl -p with
an attached file), their variants and plain writes, /dev/shm and a network connection. Then it proves nothing changed:
the target folder (in the home folder), /dev/shm, the listener. 1.76.0 (Codex, round 2): the trial reads the host -
a world-writable file in the host's /tmp is found and the host's /tmp stays read-only, kernel.shmmax and getent are
the host's (replayed), the IPC files in /proc cannot be read inside. 1.76.0: a service of
its own survives systemctl stop (the real program) from inside, systemd's and D-Bus's sockets cannot be reached,
auditctl -D and -e 0 fail, net.ipv4.ip_forward inside is the host's, and the replay gives inside the answer read
outside (any other command line: not captured). Run as root (CI's throwaway runner, with sudo), it also proves the code
runs as a UID of its own, not root, sysctl -w (the key's own value), -p and --system fail inside, nothing is written in
/etc, auditctl -l replayed gives the host's rules and the audit rules are unchanged. A control run outside the sandbox
shows the same write works there.
  python3 tests/stig/sandbox_trial.py           (exit 0: all held; AK_SANDBOX=none: only says there is none here)"""
import importlib.util
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time

here = os.path.dirname(os.path.abspath(__file__))
import ansible.module_utils  # noqa: E402
# as ansible.cfg gives them (1.77.0: the module imports site_redact)
ansible.module_utils.__path__.append(os.path.join(here, "..", "..", "plugins", "module_utils"))
spec = importlib.util.spec_from_file_location(
    "site_stig_answer_host", os.path.join(here, "..", "..", "roles", "check_stig", "library", "site_stig_answer_host.py"))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

fails = 0


def say(ok, text):
    global fails
    print(("ok   - " if ok else "FAIL - ") + text)
    fails += 0 if ok else 1


pwsh = h.find_pwsh("")
runner = h.find_sandbox("")
# 1.76.1: AK_PROFILE=239 - the systemd 239 profile (RHEL 8) on this systemd too (it holds on a newer one as well)
if os.environ.get("AK_PROFILE") == "239":
    h.FORCED_VERSION = 239
PROFILE = h.profile_of(h.FORCED_VERSION if h.FORCED_VERSION is not None else h.systemd_version(runner))
print("profile: %s (systemd %s)" % (PROFILE, h.systemd_version(runner)))
if not pwsh:
    print("SKIP - pwsh is not here (%s)" % ("; ".join(h.PWSH_WHY) or "not on the PATH"))
    sys.exit(0)
why = h.sandbox_probe(runner)
VER = h.FORCED_VERSION if h.FORCED_VERSION is not None else h.systemd_version(runner)
if os.geteuid() != 0 and VER < 257 and os.environ.get("AK_SANDBOX") != "none":
    # 1.76.1c: as a user other than root, below systemd 257 (no PID namespace of its own) the code could signal this
    # user's other processes - the module refuses the sandbox; the root run (the job's own way) is the one that tries it
    say("needs systemd 257" in why, "as a user other than root on systemd %s the module refuses the sandbox: %s" % (VER, why or "it did not"))
    print("the writes, signals and replays are tried by the root run (sudo) on this systemd")
    sys.exit(1 if fails else 0)
if os.geteuid() != 0 and why and os.environ.get("AK_USER_MAY_REFUSE") == "1":
    # 1.76.1e: a CI runner whose user manager gets no namespace (systemd then skips the sandbox's mounts): the probe
    # refuses the sandbox - fail-closed, the trial is "not tried". The root run (the job's own way) must hold.
    say(True, "as a user other than root the module refuses the sandbox here: %s" % why)
    print("the writes, signals and replays are tried by the root run (sudo) on this systemd")
    sys.exit(0)
if os.environ.get("AK_SANDBOX") == "none":
    say(bool(why), "no sandbox here, and the module says so: %s" % (why or "it found one"))
    sys.exit(1 if fails else 0)
say(not why, "the sandbox works here and keeps the host read-only (%s)" % (why or "systemd-run%s" % ("" if os.geteuid() == 0 else " --user")))
if why:
    sys.exit(1)

base = os.path.expanduser("~/.cache")
os.makedirs(base, exist_ok=True)
d = tempfile.mkdtemp(prefix="site-ak-sandbox-", dir=base)
victim = os.path.join(d, "victim")
with open(victim, "w") as f:
    f.write("x\n")
os.chmod(victim, 0o644)
shm = "site-ak-sandbox-%d" % os.getpid()
conf = os.path.join(d, "s.conf")
swap = open("/proc/sys/vm/swappiness").read().strip()
with open(conf, "w") as f:
    f.write("vm.swappiness = %s\n" % swap)      # its own value: even a write that got through changes nothing
lis = socket.socket()
lis.bind(("127.0.0.1", 0))
lis.listen(5)
lis.settimeout(0.5)
port = lis.getsockname()[1]
calls = []
stop = threading.Event()


def accept():
    while not stop.is_set():
        try:
            c, _ = lis.accept()
            calls.append(1)
            c.close()
        except OSError:
            pass


threading.Thread(target=accept, daemon=True).start()


def snap():
    st = os.stat(victim)
    return sorted(os.listdir(d)), open(victim).read(), stat.S_IMODE(st.st_mode), st.st_mtime


before = snap()
q = "'" + victim + "'"
codes = [
    ("Codex 1: (Get-Item x).Attributes++", "(Get-Item %s).Attributes++; return $true" % q),
    ("Attributes += on a variable", "$f = Get-Item %s; $f.Attributes += 1; return $true" % q),
    ("Attributes set", "(Get-Item %s).Attributes = 'ReadOnly'; return $true" % q),
    ("Codex 2: rpm -q -D with a shell macro, -E", "& /usr/bin/rpm -q -D 'p %%(touch %s/rpm-d)' -E '%%p' 2>&1 | Out-Null; return $true" % d),
    ("rpm --eval with a shell macro", "& /usr/bin/rpm --eval '%%(touch %s/rpm-eval)' 2>&1 | Out-Null; return $true" % d),
    ("rpm --define then --eval", "& /usr/bin/rpm --define 'p %%(touch %s/rpm-define)' --eval '%%p' 2>&1 | Out-Null; return $true" % d),
    ("Codex 3: sysctl -n -p<file> (attached)", "& /usr/sbin/sysctl -n -p%s 2>&1 | Out-Null; return $true" % conf),
    ("sysctl -f, --load, --system", "& /usr/sbin/sysctl -f %s 2>&1 | Out-Null; & /usr/sbin/sysctl --load %s 2>&1 | Out-Null; "
                                    "& /usr/sbin/sysctl --system 2>&1 | Out-Null; return $true" % (conf, conf)),
    ("Set-Content", "Set-Content -LiteralPath %s -Value y; return $true" % q),
    ("New-Item beside it", "New-Item -ItemType File -Path '%s/new' | Out-Null; return $true" % d),
    ("[IO.File]::WriteAllText", "[IO.File]::WriteAllText(%s, 'y'); return $true" % q),
    ("Remove-Item", "Remove-Item -LiteralPath %s; return $true" % q),
    ("chmod", "& /usr/bin/chmod 777 %s 2>&1 | Out-Null; return $true" % q),
    ("a file in /dev/shm", "Set-Content -LiteralPath '/dev/shm/%s' -Value y; return $true" % shm),
    ("a network connection", "$c = [Net.Sockets.TcpClient]::new(); $c.Connect('127.0.0.1', %d); return $true" % port),
]
etc_probe = "/etc/site-ak-sandbox-%d" % os.getpid()
if os.geteuid() == 0:
    codes.append(("as root: a file in /etc", "Set-Content -LiteralPath '%s' -Value y; return $true" % etc_probe))
ran = 0
for name, code in codes:
    r, err, secs = h.run_trial(pwsh, runner, code, 30)
    ran += 1 if isinstance(r, dict) else 0
    after = snap()
    if after != before:
        say(False, "%s: it CHANGED the host - %s -> %s" % (name, before, after))
        before = after
    elif os.path.exists("/dev/shm/" + shm):
        say(False, "%s: it wrote the host's /dev/shm" % name)
    elif calls:
        say(False, "%s: it reached the network (127.0.0.1:%d)" % (name, port))
    else:
        say(isinstance(r, dict), "%s: ran in the sandbox (%s s), nothing changed%s" % (
            name, secs, "" if isinstance(r, dict) else " - but it gave no answer: %s" % err))
# the code did run: it reads the host's file in there, and the write it tries is refused by the kernel
r, err, _ = h.run_trial(pwsh, runner, "return ((Get-Content -LiteralPath %s) -eq 'x')" % q, 30)
say(isinstance(r, dict) and r.get("values") == [True], "the code runs in there and reads the host's files (it read the file: %s)" % (r or err))
r, err, _ = h.run_trial(pwsh, runner, "Set-Content -LiteralPath %s -Value y; return $true" % q, 30)
# (as root the code is another user than the file's owner: the kernel says permission denied before read-only)
say(isinstance(r, dict) and re.search(r"Read-only file system|denied", str(r.get("error"))) is not None and open(victim).read() == "x\n",
    "the write is refused by the kernel: %s" % (str((r or {}).get("error"))[:120] or err))
# 1.76.0 (Codex): no socket, no privilege. A service of this test's own, then the real systemctl and auditctl (no replay
# shim here: the test leaves it out) and systemd's and D-Bus's own sockets, tried from inside: nothing reaches them
root = os.geteuid() == 0
uid = os.geteuid()
sc = ["/usr/bin/systemctl"] + ([] if root else ["--user"])
unit = "site-ak-dummy-%d" % os.getpid()
subprocess.run(["/usr/bin/systemd-run"] + ([] if root else ["--user"]) + ["--unit", unit, "--collect", "--quiet", "/bin/sleep", "600"],
               env=h.sandbox_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)


def active():
    return subprocess.run(sc + ["is-active", unit], env=h.sandbox_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          timeout=60).stdout.decode().strip()


say(active() == "active", "a service of the test's own runs (%s)" % unit)
scope = "" if root else " --user"
r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/bin/systemctl%s stop %s 2>&1); Write-Verbose ($o -join ' '); "
                                      "return ($LASTEXITCODE -ne 0)" % (scope, unit), 30)
say(isinstance(r, dict) and r.get("values") == [True] and active() == "active",
    "systemctl%s stop (the real program, no shim) fails inside: the service still runs (%s)" % (scope, ((r or {}).get("read") or err)[:100]))
for sockp in ["/run/systemd/private", "/run/dbus/system_bus_socket"] + ([] if root else ["/run/user/%d/systemd/private" % uid, "/run/user/%d/bus" % uid]):
    r, err, _ = h.run_trial(pwsh, runner, "try { $s = [System.Net.Sockets.Socket]::new('Unix', 'Stream', 'Unspecified'); "
                                          "$s.Connect([System.Net.Sockets.UnixDomainSocketEndPoint]::new('%s')); return $true } "
                                          "catch { Write-Verbose ([string]$_); return $false }" % sockp, 30)
    say(isinstance(r, dict) and r.get("values") == [False], "%s cannot be reached from inside (%s)" % (sockp, ((r or {}).get("read") or err)[:80]))
say(active() == "active", "the service still runs after every try")
# the kernel's network settings read inside are the host's (no network namespace of its own)
host_fw = open("/proc/sys/net/ipv4/ip_forward").read().strip()
r, err, _ = h.run_trial(pwsh, runner, "return [string](& /usr/sbin/sysctl -n net.ipv4.ip_forward)", 30)
say(isinstance(r, dict) and r.get("values") == [host_fw], "net.ipv4.ip_forward inside is the host's: %s (a network namespace of its own would say 0)" % (
    (r or {}).get("values") or err))
# the replay: an answer read outside (systemctl is-active, the exact command line) given inside; any other: not captured
sh_dir = tempfile.mkdtemp(prefix="site-ak-shims-")
argv = ("/usr/bin/systemctl", "is-active", "systemd-journald")
answers = {argv: h.capture(list(argv))}
want = answers[argv][0].decode().strip()
binds = h.write_shims(sh_dir, answers)
r, err, _ = h.run_trial(pwsh, runner, "$a = & /usr/bin/systemctl is-active systemd-journald; $null = @(& /usr/bin/systemctl stop %s 2>&1); "
                                      "$rc = $LASTEXITCODE; Write-Verbose ($a + ' ' + $rc); return ($a -eq '%s' -and $rc -eq 2)" % (unit, want), 30, binds)
say(isinstance(r, dict) and r.get("values") == [True] and active() == "active",
    "the replay: systemctl is-active systemd-journald inside gives the answer read outside (%s); stop: not captured (exit 2)" % want)
# 1.76.0 (Codex, round 3): /tmp, /var/tmp and /dev/shm inside are the sandbox's own - never the host's. A host FIFO and
# a host file in /tmp cannot be reached; a write to /tmp inside does not reach the host; a code that reads /tmp is not
# tried; find over /tmp is replayed - outside, it finds the host's world-writable file
wtmp = tempfile.mkdtemp(prefix="site-ak-ww-", dir="/tmp")
ww, fifo = os.path.join(wtmp, "ww"), os.path.join(wtmp, "fifo")
with open(ww, "w") as f:
    f.write("x\n")
os.chmod(ww, 0o666)
os.mkfifo(fifo, 0o666)
rd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)          # a reader of the host's FIFO: whatever is written, it gets
r, err, _ = h.run_trial(pwsh, runner, "$a = Test-Path -LiteralPath '%s'; $b = Test-Path -LiteralPath '%s'; "
                                      "try { [IO.File]::WriteAllText('%s', 'from the sandbox') } catch { }; "
                                      "Set-Content -LiteralPath '/tmp/site-ak-inside' -Value y; return ($a -or $b)" % (fifo, ww, fifo), 30)
try:
    got = os.read(rd, 100)
except BlockingIOError:
    got = b""
os.close(rd)
say(isinstance(r, dict) and r.get("values") == [False] and got == b"", "a host FIFO and file in /tmp cannot be reached from inside (nothing reached the FIFO's reader)")
say(not os.path.exists("/tmp/site-ak-inside"), "a write to /tmp inside does not reach the host's /tmp")
say("cannot show /tmp" in h.blind("", [], [{"cmd": "Get-Content", "path": ww, "literal": True}]), "a code that reads /tmp is not tried")
# 1.76.1f: every mount point of this host read-only inside - not a fixed list (a STIG'd host has /var, /var/log,
# /var/log/audit, /home, /opt ... of their own; /run/lock is a tmpfs of its own); an automount is mounted before the
# trial (fired outside) and read-only in it, and no mount reaches the sandbox after it starts (MountFlags=private)
mps = h.probe_mounts()
# 1.76.1h: writable = a write that works (a new file of a random name, removed) - never test -w: the Rust coreutils of
# Ubuntu 25.10+ read permission bits (a 777 folder on a read-only mount: "writable")
WTRY = ("function Test-Write($p) { if (-not [IO.Directory]::Exists($p)) { return $false }; "
        "$t = [IO.Path]::Combine($p, '.site-ak-w-' + [guid]::NewGuid().ToString('N')); "
        "try { [IO.File]::Open($t, 'CreateNew', 'Write').Dispose(); [IO.File]::Delete($t); return $true } catch { return $false } }; ")
r, err, _ = h.run_trial(pwsh, runner, WTRY + "$w = @(foreach ($p in @(%s)) { if (Test-Write $p) { $p } }); "
                        "Write-Verbose ($w -join ' '); return ($w.Count -eq 0)" % ", ".join("'%s'" % m.replace("'", "''") for m in mps), 60)
say(isinstance(r, dict) and r.get("values") == [True], "every mount point of this host (%d: %s) is read-only inside%s" % (
    len(mps), ", ".join(mps[:12]) + (" ..." if len(mps) > 12 else ""), "" if isinstance(r, dict) and r.get("values") == [True] else
    " - writable: %s" % ((r or {}).get("read") or err)[:200]))
# 1.76.1f (CI's runner: /opt 777 on the root file system stayed writable as root with systemd 259): as root, a folder
# everyone may write, on the root file system - the probe finds it and holds, and a write into it fails inside
if root:
    wwroot = "/site-ak-wwroot-%d" % os.getpid()
    os.mkdir(wwroot)
    os.chmod(wwroot, 0o777)
    try:
        say(not h.sandbox_probe(runner), "a folder everyone may write on the root file system (%s, 777): the probe holds - "
            "the root file system is read-only in it (the mount table inside)" % wwroot)
        r, err, _ = h.run_trial(pwsh, runner, "try { Set-Content -LiteralPath '%s/x' -Value y -ErrorAction Stop; return $true } "
                                              "catch { return $false }" % wwroot, 30)
        say(isinstance(r, dict) and r.get("values") == [False] and not os.path.exists(wwroot + "/x"),
            "... a write into it fails inside, nothing there on the host")
    finally:
        shutil.rmtree(wwroot, ignore_errors=True)
autos = [m for m, fs, _ in h.mounts() if fs == "autofs" and not re.search(r"['\u2018\u2019\u201a\u201b]", m)]
for am in autos[:3]:
    h.fire_automounts()
    if am in h.AUTOFS_HIDDEN:
        say("cannot show" in h.blind("", [], [{"cmd": "Get-ChildItem", "path": am + "/x", "literal": True}]),
            "the automount %s: not mounted (an indirect map, or no answer) - a code that reads below it is not tried" % am)
        continue
    try:
        host_n = len(os.listdir(am))
    except OSError:
        host_n = -1
    r, err, _ = h.run_trial(pwsh, runner, WTRY + "$w = Test-Write '%s'; "
                                          "$n = @(Get-ChildItem -LiteralPath '%s' -Force -ErrorAction SilentlyContinue).Count; "
                                          "return ([string]$n + ' ' + [string]$w)" % (am, am), 60)
    say(isinstance(r, dict) and r.get("values") == ["%d False" % host_n],
        "the automount %s: mounted before the trial, read inside as on the host (%d entries), not writable (%s)" % (
            am, host_n, (r or {}).get("values") if isinstance(r, dict) else err))
if not autos:
    print("SKIP - no automount on this host (an automounted /opt is how 1.76.1f was found)")
fcall = ["/usr/bin/find", wtmp, "-type", "f", "-perm", "-0002", "-print"]
todo = h.replays_for([fcall])
binds = h.write_shims(sh_dir, dict((tuple(c), h.capture(c)) for c in todo))
r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/bin/find '%s' -type f -perm -0002 -print 2>&1); Write-Verbose ($o -join ' '); "
                                      "return ($LASTEXITCODE -eq 0 -and $o.Count -eq 1 -and $o[0] -eq '%s')" % (wtmp, ww), 30, binds)
say(todo == [fcall] and isinstance(r, dict) and r.get("values") == [True],
    "find over the host's /tmp is replayed: it finds the world-writable file (%s)" % ((r or {}).get("read") or err)[:100])
shutil.rmtree(wtmp, ignore_errors=True)
# 1.76.1d (Codex): what the sandbox's own settings hide - /run/user, the dynamic user's private folders, the kernel's
# modules, the host's devices: a code that reads one there is not tried; stat of one is replayed (the host's answer),
# never "missing" because the sandbox hides it
def _a_dev():
    for e in sorted(os.listdir("/dev")):
        q = os.path.join("/dev", e)
        try:
            if stat.S_ISBLK(os.stat(q).st_mode):
                return q
        except OSError:
            continue
    return ""


ru = sorted(os.listdir("/run/user")) if os.path.isdir("/run/user") else []
hidden = [("/run/user", "/run/user/" + ru[0] if ru else ""), ("/var/lib/private", "/var/lib/private"),
          ("/var/cache/private", "/var/cache/private"), ("/var/log/private", "/var/log/private"),
          ("/usr/lib/modules", "/usr/lib/modules/" + os.uname().release), ("/dev", _a_dev())]
for root_, hp in hidden:
    if not hp or not os.path.exists(hp):
        print("SKIP - %s: nothing of it on this host" % root_)
        continue
    r, err, _ = h.run_trial(pwsh, runner, "return (Test-Path -LiteralPath '%s')" % hp, 30)
    inside = (r or {}).get("values") if isinstance(r, dict) else err
    say("hidden in the sandbox" in h.blind("", [], [{"cmd": "Test-Path", "path": hp, "literal": True}]) or
        "the sandbox has its own" in h.blind("", [], [{"cmd": "Test-Path", "path": hp, "literal": True}]),
        "%s: Test-Path of %s is not tried (inside it says %s; on the host it is there)" % (root_, hp, inside))
    sc = ["/usr/bin/stat", "-c", "%a %F", hp]
    todo = h.replays_for([sc])
    ans = dict((tuple(c), h.capture(c)) for c in todo)
    want = h.capture(sc)[0].decode().strip()
    r, err, _ = h.run_trial(pwsh, runner, "return [string](& /usr/bin/stat -c '%%a %%F' '%s')" % hp, 30, h.write_shims(sh_dir, ans))
    say(todo == [sc] and isinstance(r, dict) and r.get("values") == [want],
        "%s: stat of %s is replayed - inside it is the host's (%s)" % (root_, hp, ((r or {}).get("values") or [err])[0] if isinstance(r, dict) else err))
# a process of this host cannot be signalled from inside: a sleeper of the test's own user (here: a PID namespace of
# its own, systemd 257+), as root also one of nobody and one of another random UID (the trial's UID is its own)
sleepers = [("the test's own user", subprocess.Popen(["/bin/sleep", "600"]))]
if root:
    import pwd
    nobody = pwd.getpwnam("nobody")
    nob = subprocess.Popen(["/usr/bin/setpriv", "--reuid=%d" % nobody.pw_uid, "--regid=%d" % nobody.pw_gid, "--clear-groups", "/bin/sleep", "600"])
    other = 54321          # another UID that owns nothing (inside a rootless container's mapped range too)
    oth = subprocess.Popen(["/usr/bin/setpriv", "--reuid=%d" % other, "--regid=%d" % other, "--clear-groups", "/bin/sleep", "600"])
    sleepers += [("nobody", nob), ("UID %d" % other, oth)]
time.sleep(1)
for who, sp in sleepers:
    if sp.poll() is not None:
        print("SKIP - a process of %s did not start here (setpriv exited %s)" % (who, sp.returncode))
        continue
    r, err, _ = h.run_trial(pwsh, runner, "try { [System.Diagnostics.Process]::GetProcessById(%d).Kill() } catch { }; "
                                          "$null = @(& /usr/bin/kill -TERM %d 2>&1); return $true" % (sp.pid, sp.pid), 30)
    say(sp.poll() is None, "a process of %s (pid %d) cannot be signalled from inside: it still runs" % (who, sp.pid))
    sp.kill()
    sp.wait()
# the host's IPC settings: kernel.shmmax through sysctl is replayed (the sandbox's own IPC namespace would answer
# otherwise); read straight from /proc inside, it is hidden - never the sandbox's own value
host_shm = open("/proc/sys/kernel/shmmax").read().strip()
calls = [["/usr/sbin/sysctl", "-n", "kernel.shmmax"], ["/usr/bin/getent", "passwd", "root"]]
todo = h.replays_for(calls)
say(todo == calls, "the IPC setting and getent are replayed (%s)" % todo)
answers = dict((tuple(c), h.capture(c)) for c in todo)
binds = h.write_shims(sh_dir, answers)
want_pw = answers[("/usr/bin/getent", "passwd", "root")][0].decode().strip()
r, err, _ = h.run_trial(pwsh, runner, "$v = [string](& /usr/sbin/sysctl -n kernel.shmmax); Write-Verbose $v; return ($v -eq '%s')" % host_shm, 30, binds)
say(isinstance(r, dict) and r.get("values") == [True], "kernel.shmmax inside is the host's %s (replayed)" % host_shm)
# 1.76.1c: no IPC namespace of its own with any profile - the file is the host's, read as it is
r, err, _ = h.run_trial(pwsh, runner, "return [string](Get-Content /proc/sys/kernel/shmmax)", 30)
say(isinstance(r, dict) and r.get("values") == [host_shm], "/proc/sys/kernel/shmmax inside is the host's (%s)" % host_shm)
# 1.76.1c: no SysV IPC object can be made inside, with every profile (the calls refused - PrivateIPC may be skipped)
r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/bin/ipcmk -M 4096 2>&1); Write-Verbose ($o -join ' '); return ($LASTEXITCODE -ne 0)", 30)
say(isinstance(r, dict) and r.get("values") == [True], "no SysV IPC object can be made inside (ipcmk: %s)" % ((r or {}).get("read") or err)[:80])
r, err, _ = h.run_trial(pwsh, runner, "$v = [string](& /usr/bin/getent passwd root); return ($v -eq '%s')" % want_pw.replace("'", "''"), 30, binds)
say(isinstance(r, dict) and r.get("values") == [True], "getent passwd root inside gives the answer read outside (replayed): %s" % want_pw.split(":")[0])
say("mount table" in h.blind("Get-Content /proc/self/mountinfo") and "user or group" in h.blind("(Get-Item /etc/x).User"),
    "a code that reads the mount table or a name through PowerShell is not tried")
audit_before = None
if os.path.exists("/usr/sbin/auditctl") and root:
    audit_before = (h.capture(["/usr/sbin/auditctl", "-l"])[0], h.capture(["/usr/sbin/auditctl", "-s"])[0])
if os.path.exists("/usr/sbin/auditctl"):
    r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/sbin/auditctl -D 2>&1); $d = $LASTEXITCODE; $o += @(& /usr/sbin/auditctl -e 0 2>&1); "
                                          "Write-Verbose ($o -join ' | '); return ($d -ne 0 -and $LASTEXITCODE -ne 0)", 30)
    say(isinstance(r, dict) and r.get("values") == [True], "auditctl -D and auditctl -e 0 (the real program) fail inside (%s)" % (
        ((r or {}).get("read") or err)[:100]))
    if root:
        # auditctl -l replayed: the rules read outside, the same inside - no CAP_AUDIT_CONTROL in the sandbox, ever
        a2 = {("/usr/sbin/auditctl", "-l"): h.capture(["/usr/sbin/auditctl", "-l"])}
        r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/sbin/auditctl -l 2>&1); return (($o -join \"`n\") -eq @'\n%s\n'@)"
                                % a2[("/usr/sbin/auditctl", "-l")][0].decode().rstrip("\n").replace("'@", "' @"), 30, h.write_shims(sh_dir, a2))
        say(isinstance(r, dict) and r.get("values") == [True], "as root: auditctl -l inside gives the rules read outside (replayed)")
        after = (h.capture(["/usr/sbin/auditctl", "-l"])[0], h.capture(["/usr/sbin/auditctl", "-s"])[0])
        say(after[0] == audit_before[0] and after[1].split(b"\n")[0] == audit_before[1].split(b"\n")[0],
            "as root: the audit rules and 'enabled' are as they were")
else:
    print("SKIP - no /usr/sbin/auditctl here (install auditd to try auditctl -D / -e 0 / -l)")
subprocess.run(sc + ["stop", unit], env=h.sandbox_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
shutil.rmtree(sh_dir, ignore_errors=True)
if root:
    # as root (CI's throwaway runner: sudo): it runs as a UID of its own that keeps only the right to read; the kernel's
    # settings stay as they are - the write fails inside, even of the key's own value; /etc stays as it is
    r, err, _ = h.run_trial(pwsh, runner, "return [string](& /usr/bin/id -u)", 30)
    # 1.76.1c: a dynamic user on every systemd (a numeric User= with no passwd entry: exit 217 before systemd 257)
    u = ((r or {}).get("values") or [""])[0]
    say(isinstance(r, dict) and u.isdigit() and h.DYNAMIC_FIRST <= int(u) <= h.DYNAMIC_LAST and not h._uid_procs(int(u)),
        "as root (systemd %s): the code runs as a dynamic user of its own (UID %s), not root, owning no process after" % (VER, u or err))
    say(not os.path.exists(etc_probe), "as root: nothing written in /etc")
    for name, args in (("sysctl -w (its own value)", "-w vm.swappiness=%s" % swap), ("sysctl -p FILE", "-p %s" % conf),
                       ("sysctl --system", "--system")):
        # refused: a non-zero exit, or (procps-ng 4.0.4 - Ubuntu 24.04 - exits 0 on a refused write, "ignoring") the
        # refusal said; the value itself is checked after
        r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/sbin/sysctl %s 2>&1); Write-Verbose ($o -join ' '); "
                                              "return ($LASTEXITCODE -ne 0 -or (($o -join ' ') -match 'Read-only file system|Permission denied|Operation not permitted'))" % args, 30)
        say(isinstance(r, dict) and r.get("values") == [True], "as root: %s fails inside the sandbox (%s)" % (
            name, ((r or {}).get("read") or err)[:120]))
    say(open("/proc/sys/vm/swappiness").read().strip() == swap, "as root: vm.swappiness still %s" % swap)
say(ran == len(codes), "every code ran in the sandbox (%d of %d) - the check was left out, the sandbox held" % (ran, len(codes)))
# the control: the same write outside the sandbox works - the test can see a write
ctl = os.path.join(d, "control")
with open(ctl, "w") as f:
    f.write("x\n")
subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", "Set-Content -LiteralPath '%s' -Value y" % ctl],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
say(open(ctl).read().strip() == "y", "the control: the same Set-Content outside the sandbox writes")
stop.set()
lis.close()
for left in ("/dev/shm/" + shm, etc_probe):
    if os.path.exists(left):
        os.remove(left)
shutil.rmtree(d, ignore_errors=True)
print("sandbox: %s" % ("all held" if not fails else "%d FAILED" % fails))
sys.exit(1 if fails else 0)
