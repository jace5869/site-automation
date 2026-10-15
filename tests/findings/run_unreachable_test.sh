#!/usr/bin/env bash
# A host that cannot be reached (roles/site_findings/tasks/reach.yml): the check and report playbooks list
# it as NOT REACHED, go on with the others and end green; it still counts as targeted (its "no result"
# ticket). site_unreachable_fails: true fails the job for it, and so does no host reached at all (a wrong
# machine credential looks like that). down01 is ssh to a closed port on 127.0.0.1: connection refused.
#   bash tests/findings/run_unreachable_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${UNREACHABLE_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
# a port nothing listens on
port=$("$py" -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
cat > "$work/inv.yml" <<EOF
all:
  hosts:
    web01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
    down01.example.mil: {ansible_connection: ssh, ansible_host: 127.0.0.1, ansible_port: $port, ansible_user: nobody,
                         ansible_ssh_common_args: '-o ConnectTimeout=5 -o BatchMode=yes', ansible_python_interpreter: $py}
EOF
vars='{"check_certs_discover": false, "cert_report_discover": false, "site_fail_on": [], "cert_renew_dir": "'"$work"'/renew"}'
run() {  # run OUTFILE [ansible-playbook args...]
    local out="$1"; shift
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_HOST_KEY_CHECKING=False \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 "$ap" -i "$work/inv.yml" -e "$vars" "$@") > "$work/$out" 2>&1
}
has() {  # has NAME FILE TEXT...
    local name="$1" f="$work/$2"; shift 2
    local t
    for t in "$@"; do grep -qF -- "$t" "$f" || { bad "$name: lacks: $t"; tail -25 "$f"; return; }; done
    ok "$name"
}

# ---- 1. the certificate report: down01 listed, web01 checked, the job green ----
run report.txt playbooks/cert_report.yml; rc=$?
[ $rc -eq 0 ] && ok "certificate report, one host not reached: exit 0" || { bad "certificate report: exit $rc"; tail -30 "$work/report.txt"; }
has "certificate report: down01 NOT REACHED, with the reason" report.txt "down01.example.mil: NOT REACHED - " "Connection refused"
"$py" - "$work/report.txt" <<'EOPY' && ok "certificate report: down01 still targeted (its 'no result' ticket), counted ignored, not unreachable" || bad "certificate report: targets / recap"
import re, sys
t = open(sys.argv[1]).read()
m = re.search(r'"site_health_targets":\s*\[([^\]]*)\]', t)
recap = re.search(r'down01\.example\.mil\s*:.*unreachable=(\d+).*ignored=(\d+)', t)
web = re.search(r'web01\.example\.mil\s*:.*failed=(\d+).*', t)
sys.exit(0 if m and "down01" in m.group(1) and "web01" in m.group(1) and recap and recap.group(1) == "0" and recap.group(2) != "0"
         and web and web.group(1) == "0" else 1)
EOPY

# ---- 2. the certificate renewal: green, down01 named among the not renewed ----
run renew.txt playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "certificate renewal, one host not reached: exit 0" || { bad "certificate renewal: exit $rc"; tail -30 "$work/renew.txt"; }
has "certificate renewal: down01 listed as not reached" renew.txt "NOT REACHED | down01.example.mil | every certificate"

# ---- 3. a playbook with a "Target check" play on all hosts: that play never connects ----
run db.txt playbooks/database_health.yml -e target=all -e check_mariadb_enabled=false; rc=$?
[ $rc -eq 0 ] && ok "database health (Target check play first), one host not reached: exit 0" || { bad "database health: exit $rc"; tail -30 "$work/db.txt"; }

# ---- 4. site_unreachable_fails: true - the job fails for it, as before ----
run fails.txt playbooks/cert_report.yml -e site_unreachable_fails=true; rc=$?
[ $rc -ne 0 ] && grep -q "down01.example.mil: NOT REACHED" "$work/fails.txt" && ok "site_unreachable_fails true: the job fails (exit $rc)" \
    || bad "site_unreachable_fails true: exit $rc"

# ---- 5. no host reached at all (a wrong credential looks like this): the job fails ----
run none.txt playbooks/cert_report.yml --limit down01.example.mil; rc=$?
[ $rc -ne 0 ] && grep -q "No host could be reached" "$work/none.txt" && ok "no host reached: the job fails, the credential named (exit $rc)" \
    || { bad "no host reached: exit $rc"; tail -20 "$work/none.txt"; }

if [ "$fails" -eq 0 ]; then echo "ok   - all unreachable-host scenarios passed"; else echo "$fails FAILED"; exit 1; fi
