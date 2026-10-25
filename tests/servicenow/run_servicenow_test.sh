#!/usr/bin/env bash
# Run playbooks/servicenow_test_ticket.yml against a fake ServiceNow Table API (snmock.py) and
# check what it reports: a good account, a wrong password, a read-only account, an unknown
# assignment group, a dry run, and an instance that refuses to resolve. Then the tickets step
# (servicenow_tickets.yml) with a finding: open, update on the next run, resolve once it clears.
# Then the VM request pickup (vm_deploy_poll.yml) against it and a fake AAP API (aapmock.py).
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

# an instance with more mandatory fields: servicenow_fields on the new ticket, servicenow_resolve_fields on resolving it
extra='{"servicenow_assignment_group": "Linux Operations", "servicenow_fields": {"location": "Building 100", "contact_type": "Self-service"}, "servicenow_resolve_fields": {"resolved_by": "svc_aap"}}'
scenario "more mandatory fields" 0 "$port" api goodpw -e "$extra"
expect "more fields: all six steps pass" 'PASS | 6 resolve | resolved INC'
last_body() { grep "\"method\": \"$1\"" "$work/requests-$port.jsonl" | grep -F "$2" | tail -1; }
if last_body POST '"location": "Building 100"' | grep -qF '"contact_type": "Self-service"'; then
    echo "ok   - more fields: the new ticket carries servicenow_fields"
else
    echo "FAIL - more fields: the POST lacks servicenow_fields"; fails=$((fails + 1))
fi
if last_body PATCH '"resolved_by": "svc_aap"' | grep -qF 'sysparm_input_display_value'; then
    echo "ok   - more fields: resolving sends servicenow_resolve_fields (names accepted)"
else
    echo "FAIL - more fields: the resolve PATCH lacks servicenow_resolve_fields"; fails=$((fails + 1))
fi

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
# the disk check could not run this time (its check-error finding): the /var incident has not cleared
errored='{"site_health": {"web01": {"findings": [{"check": "disk", "id": "disk:check-error", "severity": "warning", "summary": "the disk check could not run"}], "checks": ["disk"], "checks_failed": ["disk"]}}, "site_health_targets": ["web01"], "servicenow_resolve_cleared": true}'
PLAYBOOK=playbooks/servicenow_tickets.yml scenario "tickets: the check could not run" 2 $((port + 2)) api goodpw -e "$errored"
if grep -qF 'resolved: INC0010001' "$work/out.txt"; then echo "FAIL - tickets: a check that could not run resolved its old incident"; fails=$((fails + 1))
else echo "ok   - tickets: a check that could not run clears nothing it found before"; fi
PLAYBOOK=playbooks/servicenow_tickets.yml scenario "tickets: the problem cleared" 0 $((port + 2)) api goodpw -e "$clear"
expect "tickets: resolved - the old one and the check-error one, once the check runs again" '"resolved: INC0010001, INC0010002"'

# ---- VM request pickup (vm_deploy_poll.yml): ServiceNow -> the deploy workflow in a fake AAP ----------
start_mock $((port + 3))
snp=$((port + 3)) aapp=$((port + 4)) aapl=$((port + 5)) aapn=$((port + 6))
start_aap() {   # start_aap PORT [ENV=VALUE ...]
    local p="$1"; shift
    env "$@" AAPMOCK_LOG="$work/aap-$p.jsonl" python3 "$here/aapmock.py" "$p" >/dev/null 2>&1 &
    pids+=("$!")
    for _ in $(seq 1 50); do
        python3 -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$p)))" && return 0
        sleep 0.1
    done
    echo "fake AAP did not start on $p"; exit 1
}
start_aap "$aapp"
start_aap "$aapl" AAPMOCK_LEGACY=1
start_aap "$aapn" AAPMOCK_NO_SURVEY=1
aapf=$((port + 8))
start_aap "$aapf" AAPMOCK_LAUNCH_FAIL=1
newinc() {   # newinc PORT SHORT_DESCRIPTION [GROUP] -> the incident number
    python3 - "$1" "$2" "${3-Linux Operations}" <<'EOPY'
import base64, json, sys, urllib.request
body = {"short_description": sys.argv[2], "description": "vm_name: app01\ntemplate: win2022-pxe", "assignment_group": sys.argv[3]}
r = urllib.request.Request("http://127.0.0.1:%s/api/now/table/incident" % sys.argv[1], data=json.dumps(body).encode(), method="POST",
                           headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()})
print(json.load(urllib.request.urlopen(r))["result"]["number"])
EOPY
}
# poll NAME EXPECT_RC AAP_PORT [ansible-playbook args...]: SN_HOST is the pickup's fake ServiceNow; the AAP
# credential's variables come from AAP_ENV (default: a token, the CONTROLLER_* names)
poll() {
    local name="$1" want_rc="$2" ap_port="$3"; shift 3
    (cd "$repo" && env SN_HOST="http://127.0.0.1:$snp" SN_USERNAME=${SNU:-api} SN_PASSWORD=goodpw \
        ${AAP_ENV-CONTROLLER_HOST=http://127.0.0.1:$ap_port CONTROLLER_OAUTH_TOKEN=goodtoken CONTROLLER_VERIFY_SSL=False} \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        "$ap" playbooks/vm_deploy_poll.yml -e '{"vm_deploy_assignment_group": "Linux Operations", "awx_job_id": 4711}' "$@") > "$work/out.txt" 2>&1
    local rc=$?
    if [ "$rc" -ne "$want_rc" ]; then
        echo "FAIL - $name: exit $rc, expected $want_rc"; grep -E '"msg"|ERROR|fatal' -A2 "$work/out.txt" | head -12 | cut -c1-300; fails=$((fails + 1)); return
    fi
    echo "ok   - $name (exit $rc)"
}
launches() { cat "$work/aap-$1.jsonl" 2>/dev/null | wc -l; }
deny() {   # deny NAME TEXT: the last run did NOT print TEXT
    if grep -qF -- "$2" "$work/out.txt"; then echo "FAIL - $1: printed: $2"; fails=$((fails + 1)); else echo "ok   - $1"; fi
}
p1="$(newinc $snp 'VM request: app server')"
newinc $snp 'Printer broken' >/dev/null
p3="$(newinc $snp 'vm request - second one')"
newinc $snp 'VM request for another team' 'Network Operations' >/dev/null
snwrites() { grep -cE '"method": "(POST|PATCH)"' "$work/requests-$snp.jsonl"; }

w0="$(snwrites)"
poll "pickup: dry run (Check) lists, writes nothing, starts nothing" 0 "$aapp" --check
expect "pickup dry run: the two VM requests of the group, oldest first" "WOULD START: $p1 - VM request: app server"
expect "pickup dry run: ...and the second (the prefix in any case)" "WOULD START: $p3"
expect "pickup dry run: AAP's workflow checked (read only)" 'AAP: workflow \"VM - deploy from ServiceNow ticket (workflow)\" (id 42) can be started'
[ "$(snwrites)" = "$w0" ] && [ "$(launches $aapp)" = 0 ] && echo "ok   - pickup dry run: no note, no workflow" \
    || { echo "FAIL - pickup dry run wrote or started something"; fails=$((fails + 1)); }
poll "pickup: vm_deploy_poll_dry_run: true does the same" 0 "$aapp" -e vm_deploy_poll_dry_run=true
expect "pickup dry_run: says so" "DRY RUN - would start the workflow for 2 ticket(s)"

AAP_ENV="" poll "pickup: no AAP credential - a clear failure" 2 "$aapp"
expect "pickup no credential: names the credential type" 'attach a credential of type \"Red Hat Ansible Automation Platform\"'
[ "$(snwrites)" = "$w0" ] && echo "ok   - pickup no credential: nothing written on any ticket" || { echo "FAIL - wrote without AAP"; fails=$((fails + 1)); }

poll "pickup: empty assignment group - refused" 2 "$aapp" -e '{"vm_deploy_assignment_group": ""}'
expect "pickup empty group: says why" "vm_deploy_assignment_group is empty"
poll "pickup: no prefix and no query - refused (an ordinary ticket is never picked up)" 2 "$aapp" -e '{"vm_deploy_poll_prefix": ""}'
expect "pickup unmarked: says why" "nothing marks a VM request"
poll "pickup: a workflow that takes no ticket number - refused, nothing picked up" 2 "$aapn"
expect "pickup no survey: says what to fix" "give it the survey question vm_deploy_ticket"
AAP_ENV="CONTROLLER_HOST=http://127.0.0.1:$aapp CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=Wr0ngPass7" \
    poll "pickup: AAP refuses the login - nothing picked up" 2 "$aapp"
expect "pickup wrong AAP password: explained" "AAP refused the login (HTTP 401)"
deny "pickup wrong AAP password: the password is not printed" "Wr0ngPass7"
[ "$(snwrites)" = "$w0" ] && echo "ok   - pickup refusals: nothing written on any ticket" || { echo "FAIL - a refusal wrote a note"; fails=$((fails + 1)); }

poll "pickup: vm_deploy_poll_max 1 - one ticket started, the other waits" 0 "$aapp" -e vm_deploy_poll_max=1 -vvv
expect "pickup max 1: the oldest started" "STARTED: $p1 - workflow job 1001"
expect "pickup max 1: the other waits for the next run" "skipped $p3: over the limit of 1 per run"
deny "pickup -vvv: the AAP token is never printed" "goodtoken"
deny "pickup -vvv: the ServiceNow password is never printed" "goodpw"
python3 - "$work/aap-$aapp.jsonl" "$p1" <<'EOPY' && echo "ok   - pickup: the workflow got vm_deploy_ticket=$p1" || { echo "FAIL - launch extra_vars"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
assert [r["extra_vars"] for r in rows] == [{"vm_deploy_ticket": sys.argv[2]}], rows
EOPY
python3 - "$work/requests-$snp.jsonl" <<'EOPY' && echo "ok   - pickup: 'Picked up' on the ticket before the workflow, then its job and link" || { echo "FAIL - pickup notes"; fails=$((fails + 1)); }
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in open(sys.argv[1]) if '"PATCH"' in l]
assert notes[0].startswith("[AAP] Picked up by AAP job 4711"), notes
assert notes[1].startswith("[AAP] Workflow job 1001 started for this ticket: http://127.0.0.1:") and "/execution/jobs/workflow/1001/output" in notes[1], notes
EOPY
AAP_ENV="AAP_HOSTNAME=http://127.0.0.1:$aapl AAP_USERNAME=admin AAP_PASSWORD=goodpw AAP_VALIDATE_CERTS=false" \
    poll "pickup: older AAP (/api/v2/ only), the AAP_* variables and a password" 0 "$aapl"
expect "pickup older AAP: started the one left" "STARTED: $p3 - workflow job 1001"
expect "pickup older AAP: its link" "/#/jobs/workflow/1001/output"
expect "pickup: the first one is AAP's already" "skipped $p1: AAP has it already ([AAP] Workflow job 1001 started"
poll "pickup again: nothing new - green, nothing started" 0 "$aapp"
expect "pickup again: says so" "VM request pickup: nothing new"
[ "$(launches $aapp)" = 1 ] && echo "ok   - pickup again: no second workflow" || { echo "FAIL - started again"; fails=$((fails + 1)); }
p4="$(newinc $snp 'VM request: AAP will not start it')"
poll "pickup: AAP does not start the workflow - red, and the ticket says so" 2 "$aapf"
expect "pickup launch refused: the reason" "NOT STARTED: $p4 - AAP did not start it (HTTP 400"
tail -1 "$work/requests-$snp.jsonl" | grep -q 'Picked up, but the workflow was not started' && echo "ok   - pickup launch refused: noted on the ticket" \
    || { echo "FAIL - pickup launch refused: no note"; fails=$((fails + 1)); }
poll "pickup after a refused start: not picked up again (a person starts it by hand)" 0 "$aapp"
expect "pickup after refused: skipped" "skipped $p4: AAP has it already ([AAP] Picked up, but the workflow was not started"
poll "pickup: off (vm_deploy_poll_enabled false)" 0 "$aapp" -e vm_deploy_poll_enabled=false
expect "pickup off: says so" "Picking up VM request tickets is off"
# change tasks: picked up only once their change is approved and in Implement (a query that dot-walks to the change)
newrec() {   # newrec PORT TABLE JSON-BODY -> the new record's number
    python3 - "$1" "$2" "$3" <<'EOPY'
import base64, json, sys, urllib.request
r = urllib.request.Request("http://127.0.0.1:%s/api/now/table/%s" % (sys.argv[1], sys.argv[2]), data=sys.argv[3].encode(), method="POST",
                           headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()})
print(json.load(urllib.request.urlopen(r))["result"]["number"])
EOPY
}
cok="$(newrec $snp change_request '{"short_description": "New servers", "state": "-1", "approval": "approved"}')"
cwait="$(newrec $snp change_request '{"short_description": "New servers too", "state": "-4", "approval": "requested"}')"
tok="$(newrec $snp change_task "{\"short_description\": \"VM request: build\", \"change_request\": \"$cok\", \"assignment_group\": \"Linux Operations\"}")"
twait="$(newrec $snp change_task "{\"short_description\": \"VM request: build\", \"change_request\": \"$cwait\", \"assignment_group\": \"Linux Operations\"}")"
poll "pickup: change tasks, only those whose change is approved and in Implement (dry run)" 0 "$aapp" --check \
    -e '{"vm_deploy_poll_tables": ["change_task"], "vm_deploy_poll_query": "change_request.approval=approved^change_request.state=-1"}'
expect "pickup change tasks: the one whose change is ready" "WOULD START: $tok"
deny "pickup change tasks: not the one whose change waits for its approval" "WOULD START: $twait"

# an account that cannot read work notes: the note it writes does not show, so the next run would pick
# the ticket up again - the job stops before starting the workflow
start_mock $((port + 7)) SNMOCK_HIDE_JOURNAL=1
snp=$((port + 7))
p5="$(newinc $snp 'VM request: blind')"
newinc $snp 'VM request: blind 2' >/dev/null
poll "pickup: the account cannot see the work notes it writes - stopped, red" 2 "$aapp"
expect "pickup blind: says why" 'cannot see the \"Picked up\" note it wrote'
[ "$(launches $aapp)" = 1 ] && echo "ok   - pickup blind: no workflow started" || { echo "FAIL - blind started a workflow"; fails=$((fails + 1)); }
[ "$(grep -c '"PATCH"' "$work/requests-$snp.jsonl")" = 1 ] && echo "ok   - pickup blind: only the first ticket was touched" \
    || { echo "FAIL - blind touched more tickets"; fails=$((fails + 1)); }

# ---- ServiceNow health: the instance's MID Servers, and the MID Server hosts (a host that is not Windows, one not reached)
mids() {  # mids FILE JSON-OF-AGENTS-QUEUE-ISSUES (the "ago" of each agent: minutes since its heartbeat)
    python3 - "$1" "$2" <<'EOPY'
import datetime, json, sys
d = json.loads(sys.argv[2])
now = datetime.datetime.now(datetime.timezone.utc)
for a in d.get("agents", []):
    a["last_refreshed"] = (now - datetime.timedelta(minutes=a.pop("ago", 1))).strftime("%Y-%m-%d %H:%M:%S")
    a.setdefault("validated", "true"); a.setdefault("version", "xanadu-07-02-2024")
json.dump(d, open(sys.argv[1], "w"))
EOPY
}
mids "$work/mid-ok.json" '{"agents": [{"name": "mid01", "status": "Up", "host_name": "mid01.example.mil"},
                                      {"name": "mid02", "status": "Up", "host_name": "mid02.example.mil"}]}'
mids "$work/mid-bad.json" '{"agents": [{"name": "mid01", "status": "Down", "host_name": "mid01.example.mil", "ago": 90},
                                       {"name": "mid02", "status": "Up", "host_name": "mid02.example.mil", "ago": 45},
                                       {"name": "mid04", "status": "Up", "host_name": "mid04.example.mil", "version": "washingtondc-12-20-2023"}],
                            "queue": {"mid04": 7},
                            "issues": [{"mid_server": {"display_value": "mid04"}, "message": "MID Server certificate expires in 10 days", "state": "new"}]}'
start_mock $((port + 20)) SNMOCK_MID="$work/mid-ok.json"
start_mock $((port + 21)) SNMOCK_MID="$work/mid-bad.json"
start_mock $((port + 22)) SNMOCK_MID="$work/mid-ok.json" SNMOCK_MID_NO_QUEUE=1 SNMOCK_MID_NO_ISSUES=1
PLAYBOOK=playbooks/servicenow_health.yml scenario "health: the API and two MID Servers well" 0 $((port + 20)) api goodpw
expect "health well: the MID Servers' line" 'MID Servers: mid01 Up, mid02 Up'
PLAYBOOK=playbooks/servicenow_health.yml scenario "health: MID Servers down, silent, on another version, stuck, with issues, one missing" 2 \
    $((port + 21)) api goodpw -e '{"servicenow_mid_expected": ["mid01", "mid03"]}'
expect "health bad: down" 'MID Server mid01 on mid01.example.mil is Down'
expect "health bad: Up but silent" 'MID Server mid02 on mid02.example.mil says Up, but has not reported for 45 minutes'
expect "health bad: missing" 'MID Server mid03 (servicenow_mid_expected) is not in the instance'
expect "health bad: versions" 'the MID Servers run 2 different versions'
expect "health bad: stuck output" '7 output message(s) have waited more than 10 minutes for MID Server mid04'
expect "health bad: open issues" 'MID Server mid04 has 1 open MID Server Issue(s): MID Server certificate expires in 10 days'
PLAYBOOK=playbooks/servicenow_health.yml scenario "health: no read on ecc_queue, no ecc_agent_issue table - said, not a problem" 0 \
    $((port + 22)) api goodpw
expect "health unreadable: ecc_queue said" 'ecc_queue (stuck output): HTTP 403'
expect "health unreadable: ecc_agent_issue said" 'ecc_agent_issue (MID Server Issues): HTTP 400'
cat > "$work/mid-inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $(command -v python3)}
  children:
    servicenow_mid_hosts:
      hosts:
        mid01.example.mil: {ansible_connection: local, ansible_python_interpreter: $(command -v python3)}
        mid02.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh, ansible_ssh_timeout: 3}
EOI
PLAYBOOK=playbooks/servicenow_health.yml scenario "health with MID Server hosts: one not Windows, one not reached - in the same report" 2 \
    $((port + 20)) api goodpw -i "$work/mid-inv.yml"
expect "health hosts: not Windows" 'the MID Server check could not run on mid01.example.mil: not a Windows host'
expect "health hosts: not reached" 'MID Server host mid02.example.mil could not be reached over WinRM'
expect "health hosts: the line" 'MID Servers: mid01 Up, mid02 Up; hosts checked: 0 of 2'
PLAYBOOK=playbooks/servicenow_health.yml scenario "health with servicenow_check_mid_hosts: false - the instance's view only" 0 \
    $((port + 20)) api goodpw -i "$work/mid-inv.yml" -e servicenow_check_mid_hosts=false
expect "health no hosts: the line" 'MID Servers: mid01 Up, mid02 Up'

if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
echo "all ServiceNow test-ticket, tickets, VM request pickup and ServiceNow health checks passed"
