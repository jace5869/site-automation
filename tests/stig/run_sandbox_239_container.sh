#!/usr/bin/env bash
# 1.76.1: the answer keys' trial sandbox on a real systemd 239 - RHEL 8's - in a container whose PID 1 is systemd: a UBI 8
# init image (else a Rocky 8 one: IMAGE=...), with Python 3.11 and ansible-core (the module's own imports), pwsh and the
# programs the test uses; tests/stig/sandbox_trial.py then runs as root inside - the profile picked by systemd's version
# (239: no PrivateIPC, ProtectProc, ProtectClock ... - the same properties another way, the probe proving them).
# What it proves on the real 239: a numeric User=/Group= with no passwd entry, the empty RestrictAddressFamilies list, pwsh
# under the system call filter, and every write, signal and socket refused.
#   bash tests/stig/run_sandbox_239_container.sh        (ENGINE=docker (CI) | podman; IMAGE=...; KEEP=1 leaves the container)
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
engine="${ENGINE:-docker}"
image="${IMAGE:-registry.access.redhat.com/ubi8/ubi-init}"
name="site-ak-239-$$"
if [ "$engine" = docker ]; then
    run_args=(--privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw)
else
    run_args=(--privileged --systemd=always)
fi
case "$image" in *rocky*) init=(/usr/sbin/init) ;; *) init=() ;; esac
cleanup() { [ -n "${KEEP:-}" ] || "$engine" rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
"$engine" run -d --name "$name" "${run_args[@]}" "$image" "${init[@]}" >/dev/null
for _ in $(seq 1 90); do
    st="$("$engine" exec "$name" systemctl is-system-running 2>/dev/null || true)"
    case "$st" in running|degraded) break ;; esac
    sleep 1
done
echo "the container's systemd: $("$engine" exec "$name" systemctl --version | head -1) ($st)"
"$engine" exec "$name" bash -c '
  set -e
  dnf -y -q install python3.11 python3.11-pip util-linux procps-ng findutils rpm curl >/dev/null
  curl -fsSL https://packages.microsoft.com/config/rhel/8/prod.repo -o /etc/yum.repos.d/microsoft-prod.repo
  dnf -y -q install powershell >/dev/null
  python3.11 -m pip -q install "ansible-core==2.16.*" >/dev/null
  mkdir -p /repo/roles/check_stig/library /repo/tests/stig /repo/plugins/module_utils'
"$engine" cp "$repo/roles/check_stig/library/site_stig_answer_host.py" "$name:/repo/roles/check_stig/library/"
"$engine" cp "$repo/tests/stig/sandbox_trial.py" "$name:/repo/tests/stig/"
# 1.77.0: the module imports site_redact (ansible.module_utils.site_redact; sandbox_trial.py adds the folder)
"$engine" cp "$repo/plugins/module_utils/site_redact.py" "$name:/repo/plugins/module_utils/"
"$engine" exec "$name" bash -c 'cd /repo && python3.11 tests/stig/sandbox_trial.py'
