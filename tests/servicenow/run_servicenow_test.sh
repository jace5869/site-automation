#!/usr/bin/env bash
# Run playbooks/servicenow_test_ticket.yml against a fake ServiceNow Table API (snmock.py) and
# check what it reports: a good account, a wrong password, a read-only account, an unknown
# assignment group, a dry run, and an instance that refuses to resolve. Then the tickets step
# (servicenow_tickets.yml) with a finding: open, update on the next run, resolve once it clears.
#   bash tests/servicenow/run_servicenow_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
port=18270
fails=0
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT

start_mock() {   # start_mock PORT [ENV=VALUE ...]
    local p="$1"; shift
    env "$@" SNMOCK_LOG="$work/requests-$p.jsonl" python3 "$here/snmock.py" "$p" >/dev/null 2>&1 &
    pids+=("$!")
    for _ in $(seq 1 50); do
        python3 -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$p)))" && return 0
        sleep 0.1
    done
    echo "fake ServiceNow did not start on $p"; exit 1
}

# scenario NAME EXPECT_RC PORT USER PASSWORD [ansible-playbook args...]; then expect/deny lines
scenario() {
    local name="$1" want_rc="$2" p="$3" user="$4" pw="$5"; shift 5
    (cd "$repo" && SN_HOST="http://127.0.0.1:$p" SN_USERNAME="$user" SN_PASSWORD="$pw" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
        "$ap" "${PLAYBOOK:-playbooks/servicenow_test_ticket.yml}" "$@") > "$work/out.txt" 2>&1
    local rc=$?
    if [ "$rc" -ne "$want_rc" ]; then
        echo "FAIL - $name: exit $rc, expected $want_rc"; tail -20 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    echo "ok   - $name (exit $rc)"
}
expect() {   # expect NAME TEXT: the last run printed TEXT
    if grep -qF -- "$2" "$work/out.txt"; then echo "ok   - $1"; else echo "FAIL - $1: missing: $2"; fails=$((fails + 1)); fi
}

start_mock "$port"
start_mock $((port + 1)) SNMOCK_NO_RESOLVE=1
group='{"servicenow_assignment_group": "Linux Operations", "servicenow_caller": "svc_aap"}'

scenario "good account" 0 "$port" api goodpw -e "$group"
expect "good: all six steps pass" 'PASS | 6 resolve | resolved INC'
expect "good: the group and caller were checked" 'assignment group \"Linux Operations\" exists; caller \"svc_aap\" exists'
expect "good: a link to the ticket" 'nav_to.do?uri=incident.do?sys_id='
expect "good: read back shows the group" 'assignment group Linux Operations, caller svc_aap'

scenario "wrong password" 2 "$port" api wrong -e "$group"
expect "wrong password: explained" 'FAIL 1 connection: HTTP 401'
expect "wrong password: the likely causes" 'the user name or password is wrong'

scenario "read-only account" 2 "$port" ro goodpw -e "$group"
expect "read-only: refused at open" 'FAIL 3 open: HTTP 403: Operation Failed: ACL Exception Insert Failed'
expect "read-only: needs itil" 'it needs the itil role'

scenario "unknown group" 2 "$port" api goodpw -e '{"servicenow_assignment_group": "Linux Ops"}'
expect "unknown group: FAIL at settings" 'FAIL | 2 settings | assignment group \"Linux Ops\" does not exist'
expect "unknown group: read back shows it did not stick" 'did not stick'

writes() { grep -cE '"method": "(POST|PATCH)"' "$work/requests-$port.jsonl"; }
before="$(writes)"
scenario "dry run" 0 "$port" api goodpw -e "$group" --check
expect "dry run: says so" 'DRY RUN (Check mode): steps 1-2 ran (they only read)'
if [ "$(writes)" = "$before" ]; then
    echo "ok   - dry run: nothing created or changed (no POST or PATCH sent)"
else
    echo "FAIL - dry run: sent a POST or PATCH"; fails=$((fails + 1))
fi

scenario "resolve refused" 0 $((port + 1)) api goodpw -e "$group"
expect "resolve refused: a warning with the server's reason" 'WARN | 6 resolve | could not resolve INC0010001 (HTTP 403: Operation Failed: Data Policy Exception'

scenario "no credential" 2 "$port" "" "" -e "$group"
expect "no credential: says which credential" 'Attach the \"ServiceNow API\" credential'

# The tickets step itself, with one finding: open it, note it on the next run, resolve it once clear.
start_mock $((port + 2))
finding='{"site_health": {"web01": {"findings": [{"check": "disk", "id": "disk:/var", "severity": "warning", "summary": "/var is 91% full"}], "checks": ["disk"]}}, "site_health_targets": ["web01"]}'
clear='{"site_health": {"web01": {"findings": [], "checks": ["disk"]}}, "site_health_targets": ["web01"], "servicenow_resolve_cleared": true}'
PLAYBOOK=playbooks/servicenow_tickets.yml scenario "tickets: a finding opens an incident" 2 $((port + 2)) api goodpw -e "$finding"
expect "tickets: opened" '"opened: INC0010001"'
PLAYBOOK=playbooks/servicenow_tickets.yml scenario "tickets: the same finding again" 2 $((port + 2)) api goodpw -e "$finding"
expect "tickets: updated, not duplicated" '"updated: INC0010001"'
PLAYBOOK=playbooks/servicenow_tickets.yml scenario "tickets: the problem cleared" 0 $((port + 2)) api goodpw -e "$clear"
expect "tickets: resolved" '"resolved: INC0010001"'

if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
echo "all ServiceNow test-ticket checks passed"
