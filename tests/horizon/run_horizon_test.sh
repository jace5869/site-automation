#!/usr/bin/env bash
# The Horizon 8 jobs (roles/horizon, playbooks/horizon_*.yml) against a fake Horizon REST API (hzmock.py): the API
# test (versions found newest first, a part this release lacks, a refused login, a forbidden path, the settings,
# one user), the health and capacity reports (and an email), the broken desktops check -> approval -> fix (what
# is held back and why, the list handed on, read again after the approval, a refusal, a dry run, the cap), user
# help (look, log off, reset, recover refused on full clones, the ways to name a user), disconnected sessions,
# and the image push plan -> approval -> push. An expired token is refreshed; pages are followed.
#   bash tests/horizon/run_horizon_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
port="${HZ_TEST_PORT:-18443}"
smtp="${SMTP_TEST_PORT:-18027}"
pid=""
spid=""
fails=0
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; [ -n "$spid" ] && kill "$spid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
INV

# mock [VAR=value ...]: (re)start the fake Horizon with a fresh state and an empty action log
mock() {
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    : > "$work/log.jsonl"
    env HZMOCK_LOG="$work/log.jsonl" "$@" "$py" "$here/hzmock.py" "$port" &
    pid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
}

# run NAME EXPECT(ok|fail) PLAYBOOK EXTRA_VARS_JSON [TEXT...]: the output (and the mail) must contain each TEXT;
# a TEXT starting with ! must NOT be there. HZ_ENV: more environment (e.g. HORIZON_PASSWORD=bad).
run() {
    local name="$1" want="$2" pb="$3" vars="$4"; shift 4
    : > "$work/log.jsonl"                 # the actions of this run only
    (cd "$repo" && env HORIZON_URL="http://127.0.0.1:$port" HORIZON_DOMAIN=CORP HORIZON_USERNAME=svc-horizon HORIZON_PASSWORD=goodpw \
        ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 ${HZ_ENV:-} \
        "$ap" -i "$work/inv.yml" "playbooks/$pb" -e '{"horizon_protected_pools": ["IC-Prot*"]}' -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            if grep -qF -- "${t:1}" "$work/out.txt" "$work/log.jsonl" "$work"/mail/*.eml 2>/dev/null; then
                echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1
            fi
        elif ! grep -qF -- "$t" "$work/out.txt" "$work/log.jsonl" "$work"/mail/*.eml 2>/dev/null; then
            echo "FAIL - $name: lacks: $t"; grep -E '"msg"|fatal' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
        fi
    done
    echo "ok   - $name"
}

# the job's set_stats (ANSIBLE_SHOW_CUSTOM_STATS), as AAP hands them to the next workflow node: KEY -> extra vars JSON
stats() {
    "$py" - "$work/out.txt" "$1" <<'PY'
import json, sys
text = open(sys.argv[1]).read()
i = text.rindex("RUN: ")
obj, _ = json.JSONDecoder().raw_decode(text[i + 5:].lstrip())
print(json.dumps({sys.argv[2]: obj[sys.argv[2]]}))
PY
}
logged() { grep -c -- "$1" "$work/log.jsonl"; }

# ---- API test ------------------------------------------------------------------------------------
mock
run "API test: every path at the newest version the pod has (v2), one user found" ok horizon_api_test.yml '{"horizon_test_user": "jdoe"}' \
    'PASS 1 login: CORP\\svc-horizon at http://127.0.0.1:' 'PASS connection_servers: rest/monitor/v2/connection-servers - 2 record(s)' \
    'PASS machines: rest/inventory/v2/machines - 19 record(s)' 'PASS license: rest/config/v2/licenses' \
    'horizon_protected_pools: IC-Prot* matches IC-Protected' 'PASS 4 user: CORP\\jdoe (Jane Doe), S-1-5-21-1-2-3-1001' \
    'Session: desktop on w11-002 (pool IC-Win11), connected' 'Session: application on (an RDS host)' '"logout": true'
mock HZMOCK_NEWEST=4 HZMOCK_VERSIONS=machines=1,license=0
run "API test: newer releases (v4), an older path (v1), a part this release lacks" ok horizon_api_test.yml '{}' \
    'rest/monitor/v4/connection-servers' 'PASS machines: rest/inventory/v1/machines' \
    '----  license: not in this Horizon (HTTP 404 at every API version, v9 to v1)'
HZ_ENV="HORIZON_PASSWORD=bad" run "API test: a wrong password is said plainly" fail horizon_api_test.yml '{}' \
    'FAIL 1 login: Horizon refused the login of CORP\\svc-horizon (HTTP 401: Invalid user name, password or domain)'
HZ_ENV="HORIZON_URL=" run "no credential: what to attach" fail horizon_health_report.yml '{}' \
    'No Horizon connection: attach a credential of type \"Horizon API\"'
HZ_ENV="HORIZON_DOMAIN= HORIZON_USERNAME=CORP\\svc-horizon" run "API test: DOMAIN\\user in the user name, no domain field" ok horizon_api_test.yml '{}' \
    'PASS 1 login: CORP\\svc-horizon'
run "API test: settings that name no pool, a state and an action that do not exist, an ambiguous user" fail horizon_api_test.yml \
    '{"horizon_protected_pools": ["VIP-*"], "horizon_push_pools": ["IC-Nope"], "horizon_broken_states": ["AGENT_UNREACHABLE", "BROKN"],
      "horizon_fix_actions": {"ERROR": "reboot"}, "horizon_test_user": "sam"}' \
    'FAIL 3 settings' 'horizon_push_pools: IC-Nope matches no pool' 'horizon_protected_pools: VIP-* matches no pool' \
    'horizon_broken_states: BROKN is not a desktop state' 'horizon_fix_actions ERROR: reboot is not one of' \
    'FAIL 4 user: 2 users are sam (CORP\\sam, LAB\\sam): write it DOMAIN\\user'
mock HZMOCK_FORBID=sessions
run "API test: a path the account may not read" fail horizon_api_test.yml '{}' \
    'FAIL sessions: the account may not read rest/inventory/v2/sessions (HTTP 403)' '1 path(s) refused'

# ---- reports -------------------------------------------------------------------------------------
mock
run "health: a pool that stopped provisioning (red), the broken desktops (amber), the job green" ok horizon_health_report.yml '{}' \
    'Horizon health report: 1 critical, 1 warning' 'provisioning STOPPED: Insufficient disk space on datastore DS2' \
    '7 broken: 1 AGENT_ERR_NEED_REBOOT, 3 AGENT_UNREACHABLE, 1 ALREADY_USED, 1 ERROR, 1 PROVISIONING_ERROR' \
    'Read from http://127.0.0.1:' 'connection_servers: rest/monitor/v2/connection-servers' '!emailed'
mock HZMOCK_BAD=1 HZMOCK_EXPIRE=1 HZMOCK_VERSIONS=license=0
run "health: an unhealthy pod, an expired token refreshed, horizon_health_fail" fail horizon_health_report.yml '{"horizon_health_fail": true}' \
    'status ERROR' 'service VMware Horizon Security Gateway Component is DOWN' 'its certificate expires in 4 days' \
    'replication with cs02: ERROR RPC server unavailable' 'status PROBLEM' 'from Connection Server cs02: CANNOT_LOGIN' \
    'status CONNECTION_ERROR' 'not in this Horizon (HTTP 404 at every API version, v9 to v1)' '"refresh": true' 'critical finding(s) (the report is above)'
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done
mock
run "capacity: a full pool (red), every pool, pages of 4 followed, emailed" ok horizon_capacity_report.yml \
    '{"horizon_page_size": 4, "report_email_to": ["vdi-team@example.mil"], "report_email_smtp_host": "localhost", "report_email_smtp_port": '"$smtp"',
      "report_email_from": "aap@example.mil", "report_email_security": "none"}' \
    'Horizon capacity: 1 pool(s) full or nearly' 'full: no desktop left for the next user' '24 desktop(s) at most' \
    'IC-Win11      instant clone  10   10        2     2       5       1      3         20%' 'sessions on RDS hosts' 'X-Envelope-To: vdi-team@example.mil'
kill "$spid" 2>/dev/null; spid=""

# ---- broken desktops: check -> approval -> fix ----------------------------------------------------
mock
run "desktops check (report): what to fix, what is held back and why, green" ok horizon_desktops_check.yml '{}' \
    'Horizon desktops: 7 broken, 4 to fix' 'w11-008  IC-Win11  AGENT_ERR_NEED_REBOOT  restart' 'w11-004  IC-Win11  AGENT_UNREACHABLE      recover' \
    "w11-006   IC-Win11      AGENT_UNREACHABLE  a user's session is on it (disconnected)" \
    'recover is for instant clones, and pool FC-Legacy is full clone: fix it by hand' 'pool IC-Protected is protected' '!NEEDS APPROVAL:'
run "desktops check (approval): the list handed on, the job fails on purpose" fail horizon_desktops_check.yml '{"horizon_desktops_mode": "approval"}' \
    'NEEDS APPROVAL: 4 broken desktop(s) to fix - w11-008 (AGENT_ERR_NEED_REBOOT: restart), w11-004 (AGENT_UNREACHABLE: recover)' \
    '3 more held back for a person' '"horizon_desktops_fix": {'
fix="$(stats horizon_desktops_fix)"
CHECK=1 run "fix (dry run): what it would do, nothing sent" ok horizon_desktops_fix_approved.yml "$fix" \
    'w11-004: would recover (it was AGENT_UNREACHABLE) (a dry run)' 'w11-008: would restart' '!machines/action'
run "fix: exactly the approved desktops, one request per action" ok horizon_desktops_fix_approved.yml "$fix" \
    'Horizon desktops fixed: 4 of 4' 'w11-004: recover (it was AGENT_UNREACHABLE) requested - Horizon took it' \
    '"path": "inventory/machines/action/recover", "query": {}, "body": ["m-w04", "m-w05", "m-w07"]' \
    '"path": "inventory/machines/action/restart", "query": {}, "body": ["m-w08"]'
run "fix again: each desktop read again first - none broken now, nothing sent" ok horizon_desktops_fix_approved.yml "$fix" \
    'none - every approved desktop changed since the check' 'it is PROVISIONING now: it recovered by itself' '!machines/action'
run "fix on its own (no approved list): refused" fail horizon_desktops_fix_approved.yml '{}' 'Nothing to fix: no approved list (horizon_desktops_fix)'
mock HZMOCK_REFUSE=m-w04
run "fix: a desktop Horizon refuses fails the job, the others are done" fail horizon_desktops_fix_approved.yml "$fix" \
    'w11-004: REFUSED - recover refused (HTTP 400): the machine is busy' 'w11-005: recover (it was ALREADY_USED) requested' 'refused 1 action(s)'
mock
run "desktops check: horizon_fix_max, a desktop excluded, an action by state" ok horizon_desktops_check.yml \
    '{"horizon_fix_max": 2, "horizon_fix_exclude_machines": ["w11-00[5]"], "horizon_fix_actions": {"PROVISIONING_ERROR": "remove"}}' \
    'over horizon_fix_max (2 per run): the next run takes it' 'the desktop is excluded (horizon_fix_exclude_machines)' \
    'Horizon desktops: 7 broken, 2 to fix'

# ---- user help -----------------------------------------------------------------------------------
mock
run "user help (look): sessions and desktops, nothing changed" ok horizon_user_help.yml '{"horizon_user": "jdoe"}' \
    'CORP\\jdoe (Jane Doe), S-1-5-21-1-2-3-1001: 3 session(s), 2 desktop(s)' \
    'Session: desktop on fin-001 (pool IC-Finance), connected' 'Session: application on (an RDS host) (pool farm RDS-Farm), disconnected since' \
    'Desktop: w11-002 (pool IC-Win11) is CONNECTED' '!inventory/sessions/action'
run "user help: log off all of a user's sessions (forced)" ok horizon_user_help.yml '{"horizon_user": "CORP\\asmith", "horizon_user_action": "logoff"}' \
    'disconnected session on w11-003: logoff requested - Horizon took it' \
    '"path": "inventory/sessions/action/logoff", "query": {"forced_logoff": "true"}, "body": ["s2", "s6"]'
run "user help: recover refused on full clones, by user@domain" ok horizon_user_help.yml '{"horizon_user": "eve@corp.example.mil", "horizon_user_action": "recover"}' \
    'leg-002: left alone - recover is for instant clones; pool FC-Legacy is not' 'Desktop: leg-001 (pool FC-Legacy) is ERROR, assigned to them - BROKEN' \
    '!machines/action'
run "user help: reset a user's desktops, by SID" ok horizon_user_help.yml '{"horizon_user": "S-1-5-21-1-2-3-1001", "horizon_user_action": "reset"}' \
    '"path": "inventory/machines/action/reset", "query": {}, "body": ["m-w02", "m-f01"]'
mock HZMOCK_NO_LOGIN_FILTER=1
run "user help: an API that searches by name only" ok horizon_user_help.yml '{"horizon_user": "bwayne"}' 'CORP\\bwayne (Bo Wayne)'
run "user help: a user Horizon does not know" fail horizon_user_help.yml '{"horizon_user": "nobody"}' \
    'Horizon knows no user nobody (the AD users it can see), and no session carries that name. Nothing was changed.'
run "user help: a wrong action" fail horizon_user_help.yml '{"horizon_user": "jdoe", "horizon_user_action": "reboot"}' \
    "horizon_user_action is 'reboot': look, logoff, disconnect, reset, restart or recover."

# ---- disconnected sessions -----------------------------------------------------------------------
mock
CHECK=1 run "session cleanup (dry run): the old ones, the held ones, nothing logged off" ok horizon_session_cleanup.yml '{}' \
    'Horizon disconnected sessions: 3 to log off' 'CORP\\jdoe    (an RDS host)  farm RDS-Farm' 'CORP\\asmith  w11-003' \
    'pool IC-Protected is excluded' 'when it was disconnected is not known' 'CORP\\asmith on w11-003: would log off (a dry run)' '!sessions/action'
run "session cleanup: logged off oldest first, users excluded by name" ok horizon_session_cleanup.yml '{"horizon_session_cleanup_exclude_users": ["asmith"]}' \
    'Horizon disconnected sessions: 1 to log off' 'the user is excluded (horizon_session_cleanup_exclude_users)' \
    '"path": "inventory/sessions/action/logoff", "query": {"forced_logoff": "true"}, "body": ["s8"]'
mock
run "session cleanup: report only" ok horizon_session_cleanup.yml '{"horizon_session_cleanup_report_only": true, "horizon_session_max_disconnected_hours": 24}' \
    'Horizon disconnected sessions: 1 to log off' 'Nothing logged off: horizon_session_cleanup_report_only is true.' '!sessions/action'

# ---- image push: plan -> approval -> push ---------------------------------------------------------
mock
run "push plan: two pools, the golden image and its snapshot found, handed on" ok horizon_image_push_plan.yml \
    '{"horizon_push_pools": "IC-Win11, IC-Finance", "horizon_push_base_vm": "W11-Gold", "horizon_push_snapshot": "v2026-11"}' \
    'READY FOR APPROVAL: Push W11-Gold / /v2026-10/v2026-11 to IC-Win11, IC-Finance, starting as soon as the approved push job runs' \
    'each desktop is replaced when its user logs off' '"horizon_push_plan": {'
plan="$(stats horizon_push_plan)"
run "push (approved): scheduled on each pool, exactly the plan" ok horizon_image_push_approved.yml "$plan" \
    'Horizon image push: 2 of 2 pool(s) scheduled' 'IC-Win11: push the image requested - Horizon took it' \
    '"path": "inventory/desktop-pools/pool-ic1/action/schedule-push-image"' '"parent_vm_id": "bvm-1", "snapshot_id": "snap-2"' \
    '"logoff_policy": "WAIT_FOR_LOGOFF", "stop_on_first_error": true'
run "push again: the pools have a push pending now - nothing scheduled" fail horizon_image_push_approved.yml "$plan" \
    'a push is already scheduled or running on it (pending image pending)' 'every pool of the plan changed since the approval'
run "push on its own (no approved plan): refused" fail horizon_image_push_approved.yml '{}' 'Nothing to push: no approved plan (horizon_push_plan)'
mock
run "push plan: what stops it - not instant clones, a push pending, a protected pool, two snapshots named alike, a past start" fail \
    horizon_image_push_plan.yml \
    '{"horizon_push_pools": ["FC-Legacy", "IC-Lab", "IC-Protected", "IC-Win11"], "horizon_push_base_vm": "W11-Gold", "horizon_push_snapshot": "dup",
      "horizon_push_start": "2020-01-01 00:00"}' \
    'pool FC-Legacy is not instant clones (full clone)' 'pool IC-Lab already has a push scheduled or running' 'pool IC-Protected is protected' \
    '2 snapshots are named dup: give its path instead (/dup, /v2026-10/dup)' 'horizon_push_start 2020-01-01 00:00 UTC is in the past' \
    'Nothing was scheduled and nothing goes to the approval' '!horizon_push_plan": {'
run "push plan: a golden image VM that is not there" fail horizon_image_push_plan.yml \
    '{"horizon_push_pools": ["IC-Win11"], "horizon_push_base_vm": "W10-Gold", "horizon_push_snapshot": "x", "horizon_push_logoff_policy": "FORCE_LOGOFF"}' \
    'no golden image VM named W10-Gold (there are: Server-2022, W11-Gold, W11-Gold-Old)'

echo
if [ "$fails" -eq 0 ]; then echo "All Horizon tests passed."; else echo "$fails Horizon test(s) FAILED."; fi
[ "$fails" -eq 0 ]
