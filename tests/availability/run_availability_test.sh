#!/usr/bin/env bash
# Run playbooks/stigman_availability.yml, playbooks/servicenow_availability.yml and playbooks/stigman_health.yml (the
# API part: stigman_health_hosts false) against fake STIG Manager nodes, a fake Keycloak and a fake ServiceNow
# (api_mock.py), and check what they say: up (one line, green), one node down (warning, green), every node down or the
# address down (red, reported), slow, the login page down, a wrong password; the health check's state, login, database
# numbers, versions and schemas, a node that refuses the token, the database restarted, the nodes from the inventory.
#   bash tests/availability/run_availability_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
port=18631                     # 18631 front, 18632 node a, 18633 node b, 18634 Keycloak, 18635 ServiceNow
fails=0
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT

start_mock() {   # start_mock PORT KIND
    python3 "$here/api_mock.py" "$1" "$2" "$work/state-$1.json" >/dev/null 2>&1 &
    pids+=("$!")
    for _ in $(seq 1 50); do
        python3 -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$1)))" && return 0
        sleep 0.1
    done
    echo "fake $2 did not start on $1"; exit 1
}
state() { printf '%s\n' "$2" > "$work/state-$1.json"; }   # state PORT JSON

F=$port; A=$((port + 1)); B=$((port + 2)); K=$((port + 3)); S=$((port + 4))
node='{"state": "available", "db": true, "oidc": true}'
for p in $F $A $B; do state $p "$node"; start_mock $p stigman; done
state $K '{"secret": "s3cret"}'; start_mock $K keycloak
state $S '{"user": "api", "password": "pw"}'; start_mock $S servicenow

cat > "$work/sm.yml" <<EOF
stigman_health_api: http://127.0.0.1:$F/api
stigman_health_nodes: [{name: stig-a, url: "http://127.0.0.1:$A/api"}, {name: stig-b, url: "http://127.0.0.1:$B/api"}]
stigman_health_hosts: false
availability_attempts: 2
availability_pause_seconds: 0.1
availability_timeout: 3
availability_slow_ms: 1000
stigman_health_slow_ms: 1000
EOF
cat > "$work/inv.yml" <<EOF
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: "$(command -v python3)"}
  children:
    stigman:
      hosts:
        stig01: {stigman_health_node_url: "http://127.0.0.1:$A/api"}
        stig02: {stigman_health_node_url: "http://127.0.0.1:$B/api"}
    stigman_db:
      hosts:
        db01: {}
EOF

# run NAME EXPECT_RC PLAYBOOK [args...]
run() {
    local name="$1" want_rc="$2" pb="$3"; shift 3
    (cd "$repo" && STIGMAN_TOKEN_URL="http://127.0.0.1:$K/realms/stigman/protocol/openid-connect/token" \
        STIGMAN_CLIENT_ID=healthcheck STIGMAN_CLIENT_SECRET="${SECRET:-s3cret}" \
        SN_HOST="http://127.0.0.1:$S" SN_USERNAME=api SN_PASSWORD="${SNPW:-pw}" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
        "$ap" -i "$work/inv.yml" "playbooks/$pb" -e "@$work/sm.yml" "$@") > "$work/out.txt" 2>&1
    local rc=$?
    if [ "$rc" -ne "$want_rc" ]; then
        echo "FAIL - $name: exit $rc, expected $want_rc"; tail -25 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    echo "ok   - $name (exit $rc)"
}
expect() { if grep -qF -- "$2" "$work/out.txt"; then echo "ok   -   $1"; else echo "FAIL -   $1: missing: $2"; fails=$((fails + 1)); fi; }
deny() { if grep -qF -- "$2" "$work/out.txt"; then echo "FAIL -   $1: printed: $2"; fails=$((fails + 1)); else echo "ok   -   $1"; fi; }

# ---- STIG Manager availability ----
run "STIG Manager up: one line, green" 0 stigman_availability.yml
expect "the line" 'STIG Manager UP: API 2/2'
expect "each node and Keycloak asked" 'stig-b 2/2'
expect "Keycloak's realm asked" 'Keycloak 2/2'
deny "no report when up" 'Report | email the report'

state $B '{"state": "unavailable", "db": false, "oidc": true}'
run "one node's database connection lost: warning, still green" 0 stigman_availability.yml
expect "degraded" 'STIG Manager DEGRADED'
expect "why" 'currentState is'
run "  ...with availability_report_on [critical, warning]: reported, green" 0 stigman_availability.yml -e '{"availability_report_on": ["critical", "warning"]}'
expect "reported" 'STIG Manager node stig-b does not answer'
run "  ...with availability_node_down critical: red" 2 stigman_availability.yml -e availability_node_down=critical

state $F '{"down": true}'; state $A '{"down": true}'
run "the address down: red, reported" 2 stigman_availability.yml
expect "down" 'STIG Manager DOWN'
expect "the address's finding" 'STIG Manager (API) does not answer: HTTP 502'

run "no address, every node down: red" 2 stigman_availability.yml -e stigman_health_api=
expect "every node" 'does not answer on any of its 2 nodes'
state $F "$node"; state $A "$node"; state $B "$node"

state $A '{"state": "available", "slow_ms": 1500}'
run "a slow node: warning, green" 0 stigman_availability.yml
expect "slow" 'node stig-a is slow'
state $A "$node"

run "the nodes from the inventory group (stigman_health_nodes empty)" 0 stigman_availability.yml -e '{"stigman_health_nodes": []}'
expect "stig01" 'stig01 2/2'
expect "stig02" 'stig02 2/2'

# ---- ServiceNow availability ----
run "ServiceNow up" 0 servicenow_availability.yml
expect "the line" 'ServiceNow UP: API 2/2'
expect "the login page" 'Login page 2/2'
SNPW=wrong run "ServiceNow: a wrong password is a down API" 2 servicenow_availability.yml
expect "why" 'does not answer: HTTP 401'
state $S '{"user": "api", "password": "pw", "login": 500}'
run "ServiceNow: the login page down" 2 servicenow_availability.yml
expect "which" 'ServiceNow (Login page) does not answer: HTTP 500'
run "  ...servicenow_availability_ui false: only the API" 0 servicenow_availability.yml -e servicenow_availability_ui=false
state $S '{"user": "api", "password": "pw"}'

# ---- STIG Manager health (the API part) ----
run "health: all well" 0 stigman_health.yml
expect "each node's state and version" 'stig-a available 1.6.18'
expect "the token" 'token OK'
expect "healthy" 'HEALTHY'

state $B '{"state": "available", "version": "1.6.17", "migration": 41, "mysql": {"uptime": 600}, "node_uptime": 300}'
run "health: one node older, another database, restarted" 2 stigman_health.yml
expect "versions differ" 'different STIG Manager versions'
expect "schemas differ" 'different database schemas'
expect "another database" 'do not seem to use the same database'
expect "restarted" 'node stig-b restarted 5 min ago'
state $B "$node"

state $A '{"state": "available", "user": 401}'
run "health: a node refuses the token" 2 stigman_health.yml
expect "token refused" "node stig-a refuses Keycloak's token"
state $A '{"state": "available", "mysql": {"threads": 148, "max": 151}}'
run "health: the database's connections nearly gone" 2 stigman_health.yml
expect "connections" 'the database has 148 of its 151 connections in use'
state $A "$node"

for p in $F $A $B; do state $p '{"state": "available", "appinfo": 403}'; done
run "health: not an admin client - appinfo not read, still healthy" 0 stigman_health.yml
expect "said" 'Not read: HTTP 403'
for p in $F $A $B; do state $p "$node"; done

SECRET=wrong run "health: Keycloak refuses the client" 2 stigman_health.yml
expect "token" 'Keycloak gave no token'

state $B '{"down": true}'
run "health: a node gone" 2 stigman_health.yml
expect "down" 'node stig-b does not answer: HTTP 502'
state $B "$node"

run "health: the nodes' hosts not read are said (inventory, stigman_health_hosts true, no SSH)" 2 stigman_health.yml \
    -e '{"stigman_health_hosts": true, "stigman_health_nodes": []}' -l localhost
expect "said" 'stig01: its host was not read'

# the hosts' part for real on this machine: a node host reached locally (no sudo), its containers and disk checked
cat > "$work/inv2.yml" <<EOF
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: "$(command -v python3)"}
  children:
    stigman:
      hosts:
        node-local: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: "$(command -v python3)"}
EOF
(cd "$repo" && STIGMAN_TOKEN_URL="http://127.0.0.1:$K/realms/stigman/protocol/openid-connect/token" \
    STIGMAN_CLIENT_ID=healthcheck STIGMAN_CLIENT_SECRET=s3cret ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
    "$ap" -i "$work/inv2.yml" playbooks/stigman_health.yml -e "@$work/sm.yml" \
    -e '{"stigman_health_hosts": true, "check_disk_warn_pct": 101, "check_disk_crit_pct": 101, "check_disk_inode_warn_pct": 101,
         "check_disk_inode_crit_pct": 101, "check_disk_lvm": false}') > "$work/out.txt" 2>&1
echo "ok   - health with a node host reached (exit $?)"
expect "the host's checks ran and were added" 'Checks run: stigman, containers, disk'
deny "the host was read" 'node-local: its host was not read'

echo
[ "$fails" -eq 0 ] && echo "all availability and STIG Manager health scenarios passed" || { echo "$fails check(s) failed"; exit 1; }
