#!/usr/bin/env python3
"""1.75.1: the answer keys' trial sandbox (roles/check_stig/library/site_stig_answer_host.py, systemd-run) holds even when
the read-only check is wrong. This test calls the module's own run_trial() directly - the check is left out HERE, never
in the module - with codes that write: the three Codex found (Attributes++, rpm -D/-E with a shell macro, sysctl -p with
an attached file), their variants and plain writes, /dev/shm and a network connection. Then it proves nothing changed:
the target folder (in the home folder), /dev/shm, the listener. 1.75.1 (Codex, round 2): the trial reads the host -
a world-writable file in the host's /tmp is found and the host's /tmp stays read-only, kernel.shmmax and getent are
the host's (replayed), the IPC files in /proc cannot be read inside. 1.75.1: a service of
its own survives systemctl stop (the real program) from inside, systemd's and D-Bus's sockets cannot be reached,
auditctl -D and -e 0 fail, net.ipv4.ip_forward inside is the host's, and the replay gives inside the answer read
outside (any other command line: not captured). Run as root (CI's throwaway runner, with sudo), it also proves the code
runs as a UID of its own, not root, sysctl -w (the key's own value), -p and --system fail inside, nothing is written in
/etc, auditctl -l replayed gives the host's rules and the audit rules are unchanged. A control run outside the sandbox
shows the same write works there.
  python3 tests/stig/sandbox_trial.py           (exit 0: all held; AK_SANDBOX=none: only says there is none here)"""
import importlib.util
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading

here = os.path.dirname(os.path.abspath(__file__))
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
if not pwsh:
    print("SKIP - pwsh is not here")
    sys.exit(0)
why = h.sandbox_probe(runner)
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
say(isinstance(r, dict) and "Read-only file system" in str(r.get("error")), "the write is refused by the kernel: %s" % (str((r or {}).get("error"))[:120] or err))
# 1.75.1 (Codex): no socket, no privilege. A service of this test's own, then the real systemctl and auditctl (no replay
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
# 1.75.1 (Codex, round 3): /tmp, /var/tmp and /dev/shm inside are the sandbox's own - never the host's. A host FIFO and
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
say("cannot show /tmp" in h.blind("$l = Get-Content -LiteralPath '%s'" % ww), "a code that reads /tmp is not tried")
fcall = ["/usr/bin/find", wtmp, "-type", "f", "-perm", "-0002", "-print"]
todo = h.replays_for([fcall])
binds = h.write_shims(sh_dir, dict((tuple(c), h.capture(c)) for c in todo))
r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/bin/find '%s' -type f -perm -0002 -print 2>&1); Write-Verbose ($o -join ' '); "
                                      "return ($LASTEXITCODE -eq 0 -and $o.Count -eq 1 -and $o[0] -eq '%s')" % (wtmp, ww), 30, binds)
say(todo == [fcall] and isinstance(r, dict) and r.get("values") == [True],
    "find over the host's /tmp is replayed: it finds the world-writable file (%s)" % ((r or {}).get("read") or err)[:100])
shutil.rmtree(wtmp, ignore_errors=True)
# a process of this host cannot be signalled from inside: a sleeper of the test's own user (here: a PID namespace of
# its own, systemd 257+), as root also one of nobody and one of another random UID (the trial's UID is its own)
sleepers = [("the test's own user", subprocess.Popen(["/bin/sleep", "600"]))]
if root:
    import pwd
    nobody = pwd.getpwnam("nobody")
    nob = subprocess.Popen(["/usr/bin/setpriv", "--reuid=%d" % nobody.pw_uid, "--regid=%d" % nobody.pw_gid, "--clear-groups", "/bin/sleep", "600"])
    other = h.UID_FIRST - 7
    oth = subprocess.Popen(["/usr/bin/setpriv", "--reuid=%d" % other, "--regid=%d" % other, "--clear-groups", "/bin/sleep", "600"])
    sleepers += [("nobody", nob), ("UID %d" % other, oth)]
for who, sp in sleepers:
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
r, err, _ = h.run_trial(pwsh, runner, "try { $null = Get-Content /proc/sys/kernel/shmmax -ErrorAction Stop; return $true } catch { return $false }", 30)
say(isinstance(r, dict) and r.get("values") == [False], "/proc/sys/kernel/shmmax cannot be read inside (it would be the sandbox's own)")
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
    say(isinstance(r, dict) and r.get("values") == [str(h.trial_uid())],
        "as root: the code runs as its own UID %d (in no passwd entry, owning no other process), not root: %s" % (h.trial_uid(), (r or {}).get("values") or err))
    say(not os.path.exists(etc_probe), "as root: nothing written in /etc")
    for name, args in (("sysctl -w (its own value)", "-w vm.swappiness=%s" % swap), ("sysctl -p FILE", "-p %s" % conf),
                       ("sysctl --system", "--system")):
        r, err, _ = h.run_trial(pwsh, runner, "$o = @(& /usr/sbin/sysctl %s 2>&1); Write-Verbose ($o -join ' '); "
                                              "return ($LASTEXITCODE -ne 0)" % args, 30)
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
