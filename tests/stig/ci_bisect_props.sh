#!/usr/bin/env bash
# Not for release - a CI debug aid (1.76.1 sandbox): which sandbox property, on this host's systemd, leaves a folder on the
# root file system writable inside. Run as root from the repo root:  sudo bash tests/stig/ci_bisect_props.sh [python]
# It takes the exact property list the module uses (sandbox_cmd), then runs systemd-run with:
#   1. the full list;   2. the full list less one property at a time (every per-mount ReadOnlyPaths=- as one item);
#   3. a base (DynamicUser, ProtectSystem=strict, ReadOnlyPaths=/) plus one property at a time.
# Each run tries a write into /opt and into a 777 folder it makes on / (removed at the end), and prints the root
# mount's options as the sandbox sees them. Lines: "<run>: opt=<ro|WRITABLE> www=<ro|WRITABLE> root=<opts>".
set -u
PY="${1:-python3}"
[ "$(id -u)" = 0 ] || { echo "run as root"; exit 2; }
WW="/site-ak-bisect-$$"
mkdir -p "$WW" && chmod 777 "$WW"
trap 'rm -rf "${WW:?}"' EXIT
export AK_BISECT_WW="$WW"
"$PY" - <<'PYEND'
import importlib.util, os, re, subprocess
spec = importlib.util.spec_from_file_location("h", "roles/check_stig/library/site_stig_answer_host.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
run = "/usr/bin/systemd-run"
out = subprocess.run(["systemctl", "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout
ver = int(re.search(r"systemd (\d+)", out).group(1))
cmd = h.sandbox_cmd(run, 30, version=ver)
props = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-p"]
ww = os.environ["AK_BISECT_WW"]
probe = ("for d in /opt %s; do t=\"$d/.ak-bisect-$$\"; if ( : > \"$t\" ) 2>/dev/null; then rm -f \"$t\"; echo WRITABLE; "
         "else echo ro; fi; done | tr '\\n' ' '; awk '$5==\"/\"{o=$6} END {print o}' /proc/self/mountinfo" % ww)


def trial(ps):
    r = subprocess.run([run, "--pipe", "--wait", "--collect", "--quiet"] + sum([["-p", p] for p in ps], []) + ["sh", "-c", probe],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    w = r.stdout.strip().split()
    if r.returncode != 0 or len(w) < 3:
        return "did not run (exit %s): %s" % (r.returncode, r.stdout.strip()[-200:].replace("\n", " | "))
    return "opt=%s www=%s root=%s" % (w[0], w[1], w[2])


per = [p for p in props if p.startswith("ReadOnlyPaths=-")]
items = [p for p in props if not p.startswith("ReadOnlyPaths=-")] + (["<per-mount ReadOnlyPaths=- (%d)>" % len(per)] if per else [])
print("systemd %d, %d properties (%d per-mount); %s" % (ver, len(props), len(per), os.uname().release))
print("host /opt: %s" % subprocess.run(["stat", "-c", "%A %U %m", "/opt"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip())
print("full: " + trial(props))
for it in items:
    rest = [p for p in props if (p not in per if it.startswith("<") else p != it)]
    print("full less %s: %s" % (it, trial(rest)))
base = ["DynamicUser=yes", "ProtectSystem=strict", "ReadOnlyPaths=/"]
print("base: " + trial(base))
for it in items:
    add = per if it.startswith("<") else [it]
    if all(a in base for a in add):
        continue
    print("base plus %s: %s" % (it, trial(base + add)))
PYEND
