#!/usr/bin/env bash
# Linux - auditd tuning for containers (1.79.13; playbooks/auditd_tuning.yml) on two UBI containers over podman: the real
# tasks on real /etc/audit files, with stand-in auditctl, augenrules, grubby, service, systemctl (state in /fake). One
# host has immutable rules (-e 2, enabled 2), one not. Checks: a read-only run lists what is too small and the changes to
# approve, writes nothing; Check with approved changes writes nothing; the approved changes - the rules file (-D and -f
# kept), auditd.conf, every kernel's argument - each file backed up, auditd restarted, augenrules --load only where not
# immutable ("applies at the next reboot"), and the next read says "takes effect at the next reboot - immutable"; a
# failed restart puts auditd.conf back, restarts again, the job red; the optional containers line (ISSO) only when asked;
# the workflow's check: NEEDS APPROVAL, nothing written; never a reboot; a value above its minimum kept; rules that do not
# load: red.
#   bash tests/auditd/run_auditd_tuning_test.sh     (ANSIBLE_PLAYBOOK=..., AUDITD_TEST_COLLECTIONS=<a folder with containers.podman>)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
engine="${CONTAINER_ENGINE:-$(command -v podman)}"
image="registry.access.redhat.com/ubi9/ubi:latest"
imm="sa-audtune-imm-$$" mut="sa-audtune-mut-$$"
work="$(mktemp -d)"
cleanup() { "$engine" rm -f -t 0 "$imm" "$mut" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -30 | sed 's/^/      /'; fails=$((fails + 1)); }

cols="${AUDITD_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$repo/tests/patch/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi

# the stand-ins: the kernel's audit state in /fake (enabled, backlog_limit, lost, the kernels' arguments), a log of calls
cat > "$work/stubs.sh" <<'STUBS'
set -e
mkdir -p /fake /usr/local/bin /etc/audit/rules.d /var/lib/containers/storage/overlay /home/svc/.local/share/containers/storage/overlay
cat > /usr/local/bin/auditctl <<'X'
#!/bin/sh
[ "$1" = "-s" ] || exit 1
echo "AUDITCTL -s" >> /fake/log
printf 'enabled %s\nfailure 1\npid 812\nrate_limit 0\nbacklog_limit %s\nlost %s\nbacklog 3\nbacklog_wait_time 60000\n' \
  "$(cat /fake/enabled)" "$(cat /fake/backlog_limit)" "$(cat /fake/lost)"
X
cat > /usr/local/bin/augenrules <<'X'
#!/bin/sh
echo "AUGENRULES $*" >> /fake/log
if [ "$(cat /fake/enabled)" = 2 ]; then echo "Audit system is in immutable mode - exiting with no changes"; exit 0; fi
if [ -e /fake/load_fail ]; then echo "There was an error in line 4 of /etc/audit/audit.rules" >&2; exit 1; fi
b=$(awk '$1 == "-b" {v = $2} END {print v}' /etc/audit/rules.d/10-base-config.rules)
[ -n "$b" ] && echo "$b" > /fake/backlog_limit
exit 0
X
cat > /usr/local/bin/grubby <<'X'
#!/bin/sh
echo "GRUBBY $*" >> /fake/log
case "$1" in
  --info=ALL)
    i=0; for k in 5.14.0-427 5.14.0-503; do
      printf 'index=%s\nkernel="/boot/vmlinuz-%s"\nargs="ro quiet %s"\nroot="/dev/mapper/rhel-root"\n' "$i" "$k" "$(cat /fake/kargs.$k)"; i=$((i + 1)); done ;;
  --update-kernel=*)
    a="${2#--args=}"; which="${1#--update-kernel=}"
    for k in 5.14.0-427 5.14.0-503; do
      [ "$which" = ALL ] || [ "$which" = "/boot/vmlinuz-$k" ] || continue
      printf '%s' "$(sed 's/audit_backlog_limit=[0-9]*//' /fake/kargs.$k) $a" | sed 's/  */ /g; s/^ *//' > /fake/kargs.$k
    done ;;
esac
X
cat > /usr/local/bin/service <<'X'
#!/bin/sh
echo "SERVICE $*" >> /fake/log
if [ -e /fake/restart_fail ] && grep -q '^q_depth = 8192' /etc/audit/auditd.conf; then echo "Job for auditd.service failed" >&2; exit 1; fi
exit 0
X
cat > /usr/local/bin/systemctl <<'X'
#!/bin/sh
echo "SYSTEMCTL $*" >> /fake/log
[ "$1" = is-active ] && { echo active; exit 0; }
exit 0
X
for r in reboot shutdown; do printf '#!/bin/sh\necho "REBOOT %s $*" >> /fake/log\n' "$r" > /usr/local/bin/$r; done
chmod +x /usr/local/bin/*
STUBS

prepare() {   # prepare CONTAINER ENABLED(1|2): a fresh audit configuration and kernel state
    "$engine" exec -i "$1" sh -s "$2" <<'PREP'
set -e
rm -f /fake/log /fake/restart_fail /fake/load_fail /etc/audit/*.site-bak-* /etc/audit/rules.d/*.site-bak-* /etc/audit/rules.d/05-containers.rules
echo "$1" > /fake/enabled; echo 320 > /fake/backlog_limit; echo 17 > /fake/lost
echo "ro quiet crashkernel=1G" > /fake/cmdline
echo "crashkernel=1G" > /fake/kargs.5.14.0-427; echo "crashkernel=1G audit_backlog_limit=32768" > /fake/kargs.5.14.0-503
printf '## First rule - delete all\n-D\n\n## Increase the buffers\n-b 320\n\n## failure mode\n-f 2\n' > /etc/audit/rules.d/10-base-config.rules
printf -- '-a always,exit -F arch=b64 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=unset -k delete\n' > /etc/audit/rules.d/30-stig.rules
if [ "$1" = 2 ]; then printf -- '-e 2\n' > /etc/audit/rules.d/99-finalize.rules; else rm -f /etc/audit/rules.d/99-finalize.rules; fi
printf 'log_file = /var/log/audit/audit.log\nflush = INCREMENTAL_ASYNC\nfreq = 50\nq_depth = 1200\noverflow_action = IGNORE\n' > /etc/audit/auditd.conf
PREP
}
sums() { for c in "$imm" "$mut"; do "$engine" exec "$c" sh -c 'cat /etc/audit/auditd.conf /etc/audit/rules.d/*.rules /fake/kargs.* /fake/backlog_limit | md5sum'; done; }
on() { "$engine" exec "$1" sh -c "$2"; }

for c in "$imm" "$mut"; do
    "$engine" run -d --name "$c" "$image" sleep infinity >/dev/null || { echo "ABORT - could not start $image"; exit 1; }
    "$engine" exec -i "$c" sh < "$work/stubs.sh" || { echo "ABORT - the stand-ins"; exit 1; }
done
prepare "$imm" 2; prepare "$mut" 1
c="ansible_connection: containers.podman.podman, ansible_python_interpreter: /usr/bin/python3, ansible_podman_executable: $engine, ansible_become: false"
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    imm01.example.mil: {ansible_host: $imm, $c}
    mut01.example.mil: {ansible_host: $mut, $c}
EOI
run() {   # run [ansible args...] -> $work/out.txt, $rc
    (cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/inv.yml" playbooks/auditd_tuning.yml -e auditd_tuning_cmdline=/fake/cmdline "$@") > "$work/out.txt" 2>&1
    rc=$?
    nrun=$(( ${nrun:-0} + 1 ))
    [ -n "${AUDITD_TEST_KEEP:-}" ] && { mkdir -p "$AUDITD_TEST_KEEP"; cp "$work/out.txt" "$AUDITD_TEST_KEEP/run-$nrun.txt"; }
}
has() { grep -qF -- "$1" "$work/out.txt"; }

# 1. read only: what is too small, the changes to approve; nothing written
before="$(sums)"
run
if [ "$rc" -eq 0 ] && [ "$(sums)" = "$before" ] && has "the kernel's audit backlog_limit is 320 - too small for containers (want 8192)" \
   && has "to approve: backlog: -b 8192 and --backlog_wait_time 60000 in /etc/audit/rules.d/10-base-config.rules (now 320 and not set)" \
   && has "to approve: queue: q_depth 8192 and overflow_action SYSLOG in /etc/audit/auditd.conf, auditd restarted (now 1200 and IGNORE)" \
   && has "to approve: kernel: audit_backlog_limit=8192 on 1 of 2 kernel(s) without it or lower (grubby; at the next reboot)" \
   && has "17 audit record(s) LOST since boot" && ! has "containers:"; then
    pass "read only: too small, lost, the three changes to approve (no containers line unless asked); nothing written"
else fail "read only" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi

# 2. Check with every change approved: nothing written, what it would make said
run --check -e '{"auditd_tuning_approved": ["backlog", "queue", "kernel"]}'
if [ "$rc" -eq 0 ] && [ "$(sums)" = "$before" ] && has "would make: backlog: -b 8192" && has "would make: kernel: audit_backlog_limit=8192 on 1 of 2"; then
    pass "Check with approved changes: nothing written; what it would make is said"
else fail "Check" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi

# 3. the workflow's check: NEEDS APPROVAL, nothing written
run -e auditd_tuning_workflow=true
if [ "$rc" -ne 0 ] && [ "$(sums)" = "$before" ] && has "NEEDS APPROVAL - 6 change(s) on 2 host(s)"; then
    pass "the workflow's check: NEEDS APPROVAL (6 changes on 2 hosts), nothing written"
else fail "workflow check" "$(grep -E 'msg|fatal|NEEDS' "$work/out.txt" | head -20)"; fi

# 4. approved (one host by name for the kernel line): the files, the backups, the restart; augenrules only where not immutable
run -e '{"auditd_tuning_approved": ["backlog", "queue", "mut01.example.mil: kernel", "imm01.example.mil: kernel"]}'
ok=1
for h in "$imm" "$mut"; do
    on "$h" 'cat /etc/audit/rules.d/10-base-config.rules' | tr '\n' '|' | grep -q '^## First rule - delete all|-D||## Increase the buffers|-b 8192|--backlog_wait_time 60000||## failure mode|-f 2|$' || ok=0
    on "$h" 'grep -qx -- "-f 2" /etc/audit/rules.d/10-base-config.rules && grep -qx "q_depth = 8192" /etc/audit/auditd.conf && grep -qx "overflow_action = SYSLOG" /etc/audit/auditd.conf' || ok=0
    on "$h" 'ls /etc/audit/auditd.conf.site-bak-* /etc/audit/rules.d/10-base-config.rules.site-bak-* >/dev/null && grep -q "^-b 320" /etc/audit/rules.d/10-base-config.rules.site-bak-*' || ok=0
    on "$h" 'grep -q "audit_backlog_limit=8192" /fake/kargs.5.14.0-427 && grep -q "audit_backlog_limit=32768" /fake/kargs.5.14.0-503 && grep -q "^SERVICE auditd restart" /fake/log && ! grep -q REBOOT /fake/log' || ok=0
    on "$h" '! grep -q "GRUBBY --update-kernel=ALL" /fake/log && grep -q "GRUBBY --update-kernel=/boot/vmlinuz-5.14.0-427" /fake/log' || ok=0
    on "$h" 'grep -qx -- "-a always,exit -F arch=b64 -S unlink,unlinkat,rename,renameat -F auid>=1000 -F auid!=unset -k delete" /etc/audit/rules.d/30-stig.rules' || ok=0
done
on "$mut" 'grep -q "^AUGENRULES --load" /fake/log && [ "$(cat /fake/backlog_limit)" = 8192 ]' || ok=0
on "$imm" '! grep -q "^AUGENRULES" /fake/log && [ "$(cat /fake/backlog_limit)" = 320 ] && grep -qx -- "-e 2" /etc/audit/rules.d/99-finalize.rules' || ok=0
if [ "$rc" -eq 0 ] && [ "$ok" = 1 ] && has "applies at the next reboot (immutable, -e 2)" && has "on /boot/vmlinuz-5.14.0-427 - at the next reboot (never rebooted here"; then
    pass "approved: -b/--backlog_wait_time (-D, -f, the STIG rules kept), q_depth/SYSLOG, only the low kernel (32768 kept); backups; auditd restarted; loaded only where not immutable; no reboot"
else fail "approved ($ok)" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi

# 5. the next read: the immutable host waits for its reboot, the other is right
run
if [ "$rc" -eq 0 ] && has "backlog_limit 8192 / backlog_wait_time 60000 in the rules file are set; take effect at the next reboot - immutable (-e 2): now backlog_limit 320" \
   && ! has "to approve: backlog" && ! has "to approve: queue" && ! has "to approve: kernel"; then
    pass "read again: the immutable host 'set; takes effect at the next reboot - immutable', nothing left to approve"
else fail "read again" "$(grep -E 'msg|fatal' "$work/out.txt" | head -30)"; fi

# 6. a restart that fails: auditd.conf put back, restarted again, the job red
prepare "$mut" 1
on "$mut" 'touch /fake/restart_fail'
run --limit mut01.example.mil -e '{"auditd_tuning_approved": ["queue"]}'
if [ "$rc" -ne 0 ] && on "$mut" 'grep -qx "q_depth = 1200" /etc/audit/auditd.conf && grep -qx "overflow_action = IGNORE" /etc/audit/auditd.conf && [ "$(grep -c "^SERVICE auditd restart" /fake/log)" = 2 ]' \
   && has "rolled back" && has "put back, restarted again"; then
    pass "a failed restart: auditd.conf put back, restarted again, the job red (rolled back)"
else fail "failed restart" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi
on "$mut" 'rm -f /fake/restart_fail'

# 7. the containers line (OFF by default): asked for, its own line, ISSO; approved - only the missing exclusions added to
#    05-containers.rules, a site rule there kept (Codex); one excluded already is not asked again
prepare "$mut" 1
on "$mut" 'printf -- "-w /etc/containers -p wa -k containers_conf\n-a never,exit -F dir=/var/lib/containers/storage/overlay\n" > /etc/audit/rules.d/05-containers.rules'
run --limit mut01.example.mil -e '{"auditd_container_exclude": true, "auditd_container_users": ["svc", "nobody-here"]}'
has "to approve: containers: -a never,exit for /home/svc/.local/share/containers/storage/overlay in 05-containers.rules - ISSO approval needed" && c1=1 || c1=0
run --limit mut01.example.mil -e '{"auditd_container_exclude": true, "auditd_container_users": ["svc"], "auditd_tuning_approved": ["containers"]}'
if [ "$c1" = 1 ] && [ "$rc" -eq 0 ] && on "$mut" 'grep -qx -- "-w /etc/containers -p wa -k containers_conf" /etc/audit/rules.d/05-containers.rules && [ "$(grep -c "storage/overlay" /etc/audit/rules.d/05-containers.rules)" = 2 ] && grep -qx -- "-a never,exit -F dir=/home/svc/.local/share/containers/storage/overlay" /etc/audit/rules.d/05-containers.rules && ls /etc/audit/rules.d/05-containers.rules.site-bak-* >/dev/null && grep -q "^-b 320" /etc/audit/rules.d/10-base-config.rules'; then
    pass "the containers line: only when asked (ISSO), its own approval; only the missing exclusion added, the site rule kept, backed up"
else fail "containers" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi

# 8. a deeper queue is kept when only the overflow action is wrong (Codex); auditd.conf names in any case
prepare "$mut" 1
on "$mut" 'printf "Q_DEPTH = 100\nq_depth = 32768\noverflow_action = IGNORE\n" > /etc/audit/auditd.conf'
run --limit mut01.example.mil -e '{"auditd_tuning_approved": ["queue"]}'
if [ "$rc" -eq 0 ] && has "to approve: queue: q_depth 32768 and overflow_action SYSLOG" && on "$mut" '[ "$(grep -ci "^q_depth" /etc/audit/auditd.conf)" = 2 ] && grep -qx "q_depth = 32768" /etc/audit/auditd.conf && grep -qx "overflow_action = SYSLOG" /etc/audit/auditd.conf'; then
    pass "the queue: the effective (last, any case) q_depth 32768 read and kept; only the overflow action changed"
else fail "deeper queue" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi

# 9. the rules that do not load: the change failed, said, the job red (Codex)
prepare "$mut" 1
on "$mut" 'touch /fake/load_fail'
run --limit mut01.example.mil -e '{"auditd_tuning_approved": ["backlog"]}'
if [ "$rc" -ne 0 ] && has "augenrules --load FAILED (rc 1): There was an error in line 4" && on "$mut" 'ls /etc/audit/rules.d/10-base-config.rules.site-bak-* >/dev/null'; then
    pass "rules that do not load: the change failed, augenrules' words said, the backup there, the job red"
else fail "load failure" "$(grep -E 'msg|fatal|FAILED' "$work/out.txt" | head -30)"; fi

[ "$fails" -eq 0 ] && echo "all auditd tuning checks passed" || echo "$fails auditd tuning check(s) FAILED"
[ "$fails" -eq 0 ]
