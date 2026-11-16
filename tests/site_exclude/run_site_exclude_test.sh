#!/usr/bin/env bash
# Leave hosts out of every job (1.67.0 part J): site_exclude_hosts / site_exclude_groups in the inventory's all vars
# (as all.yml sets them). The hosts left out have addresses nothing answers on (192.0.2.x, RFC 5737): a job that tried
# them would say UNREACHABLE. Checks: a Linux job (Disk space report, on a host with a local connection) - the left-out
# hosts get no task and no connection, its report page ends with "Left out by site_exclude_hosts: ..."; under a Limit
# without localhost (AAP's Limit) the same; nothing set: no line, and every host is in the play; a Windows job
# (Windows connection test, WinRM) - the host left out is never tried, the other one is.
#   bash tests/site_exclude/run_site_exclude_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
cleanup() { rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

linux_inv() {   # $1: the exclude lines (all vars)
    cat > "$work/inv.yml" <<EOF
all:
  vars:
$1
    ansible_ssh_common_args: "-o ConnectTimeout=2"
  hosts:
    web01.example.mil: {ansible_connection: local, ansible_python_interpreter: "{{ ansible_playbook_python }}"}
    web02.example.mil: {ansible_host: 192.0.2.10}
    test1.lab.example.mil: {ansible_host: 192.0.2.11}
  children:
    retired:
      hosts:
        old01.example.mil: {ansible_host: 192.0.2.12}
EOF
}
job() {   # playbook, then ansible-playbook arguments; output in $work/out.txt
    local pb="$1"; shift
    (cd "$repo" && ANSIBLE_NOCOLOR=1 "$ap" -i "$work/inv.yml" "playbooks/$pb" "$@") > "$work/out.txt" 2>&1
}
touched() {   # the left-out hosts that got anything but their add_host line
    grep -E "web02|test1|old01|win02" "$work/out.txt" | grep -vE "\(item=|Left out by site_exclude_hosts" || true
}

# ---- a Linux job: three hosts left out (a short name, a pattern, a group)
linux_inv '    site_exclude_hosts: ["web02", "*.lab.example.mil"]
    site_exclude_groups: [retired]'
mkdir -p "$work/web"
job disk_report.yml -e "report_web_dest=$work/web"
rc=$?
page="$work/web/disk-space-report/latest.html"
if [ $rc = 0 ] && [ -z "$(touched)" ] && ! grep -q UNREACHABLE "$work/out.txt" && grep -q "ok: \[web01.example.mil\]" "$work/out.txt"; then
    pass "Linux job: the three hosts left out get no task and no connection; the other one runs"
else fail "Linux job (rc $rc)" "$(touched; grep -E "UNREACHABLE|fatal" "$work/out.txt" | head)"; fi
if grep -q "Left out by site_exclude_hosts: 3 - old01.example.mil, test1.lab.example.mil, web02.example.mil." "$page" 2>/dev/null; then
    pass "  ... its report says so in one line at its end"
else fail "the report line" "$(grep -o "AAP job[^<]*" "$page" 2>/dev/null | head -2)"; fi

# ---- AAP's Limit, without localhost
job disk_report.yml -l "web01.example.mil,web02.example.mil" -e report_web_dest=
if [ $? = 0 ] && [ -z "$(touched)" ] && grep -q "Left out by site_exclude_hosts: 3" "$work/out.txt"; then
    pass "a Limit without localhost: the exclusion still runs first; web02 in the Limit is still left out"
else fail "Limit without localhost" "$(touched; grep -E "skipping|fatal|Left out" "$work/out.txt" | head)"; fi

# ---- nothing set: every host is in the play, no line
linux_inv '    site_exclude_hosts: []'
job disk_report.yml --list-hosts
got="$(sed -n '/Disk space report/,$p' "$work/out.txt" | grep -cE "web01|web02|test1|old01")"
job disk_report.yml -l web01.example.mil -e report_web_dest=
if [ "$got" = 4 ] && ! grep -q "Left out by" "$work/out.txt"; then
    pass "nothing set: every host in the job's play, no line"
else fail "nothing set" "hosts listed: $got; $(grep "Left out" "$work/out.txt" | head -2)"; fi

# ---- a Windows job: win02 left out is never tried; win01 is
cat > "$work/inv.yml" <<'EOF'
all:
  vars:
    site_exclude_hosts: ["win02"]
  children:
    windows:
      vars: {ansible_connection: winrm, ansible_port: 5986, ansible_winrm_connection_timeout: 3, ansible_winrm_read_timeout_sec: 5,
             ansible_winrm_operation_timeout_sec: 4, ansible_user: svc_test, ansible_password: not-a-real-password}
      hosts:
        win01.example.mil: {ansible_host: 192.0.2.20}
        win02.example.mil: {ansible_host: 192.0.2.21}
EOF
job win_connection_test.yml -e report_web_dest=
if [ -z "$(touched)" ] && grep -qE "^win01.example.mil +: " "$work/out.txt" && ! grep -qE "^win02.example.mil +: " "$work/out.txt"; then
    pass "Windows job: win02 left out is never tried, win01 is"
else fail "Windows job" "$(touched; grep -E "^win0" "$work/out.txt")"; fi

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
