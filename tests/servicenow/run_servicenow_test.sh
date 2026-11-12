#!/usr/bin/env bash
# Run playbooks/servicenow_test_ticket.yml against a fake ServiceNow Table API (snmock.py) and
# check what it reports: a good account, a wrong password, a read-only account, an unknown
# assignment group, a dry run, and an instance that refuses to resolve. Then the tickets step
# (servicenow_tickets.yml) with a finding: open, update on the next run, resolve once it clears.
# Then the VM request pickup (vm_deploy_poll.yml) against it and a fake AAP API (aapmock.py).
#   bash tests/servicenow/run_servicenow_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
#   SN_TEST_PORT=28270 bash tests/servicenow/run_servicenow_test.sh   another port base: the test listens on the
#   base to base+43 (127.0.0.1; base+34 also on 127.0.0.2) and stops at once when one of them is in use.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
port="${SN_TEST_PORT:-18270}"
fails=0
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT

# every port the test listens on must be free before anything starts: a stand-in that cannot bind would leave the
# scenarios talking to whatever listens there
case "$port" in ''|*[!0-9]*) echo "ABORT - SN_TEST_PORT must be a port number (got: $port)"; exit 1;; esac
if [ "$port" -lt 1024 ] || [ "$port" -gt 65491 ]; then echo "ABORT - SN_TEST_PORT must be 1024 to 65491 (got: $port)"; exit 1; fi
busy="$(python3 - "$port" <<'PY'
import socket
import sys
base = int(sys.argv[1])
for p in range(base, base + 44):
    for addr in ("127.0.0.1",) + (("127.0.0.2",) if p == base + 34 else ()):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)     # as the stand-ins bind: a port just let go is free
        try:
            s.bind((addr, p))
        except OSError:
            print("%s:%d" % (addr, p))
        finally:
            s.close()
PY
)"
if [ -n "$busy" ]; then
    echo "ABORT - in use: $(echo $busy) - the test needs ports $port to $((port + 43)); free them or pick another base: SN_TEST_PORT=$((port + 1000 > 65491 ? 28270 : port + 1000))"
    exit 1
fi

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

# a password that is no word of the message: a newer ansible-core (2.22) masks the password wherever it shows ("is $REDACTED$")
scenario "wrong password" 2 "$port" api Wr0ngPw9 -e "$group"
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
deny "health without nodes: as before" 'Nodes: '

# ---- ServiceNow health, a self-hosted instance: the front-end nodes (a fake systemctl, rpm and ausearch, fake stats
# pages, fake localhost logs under a fake /glide), the cluster (sys_cluster_state) and the database host (tests/mariadb's
# fake podman, a fake /database). Each host's own fakes come from its interpreter: a small wrapper that sets them ----
nd="$here/node"
PY="$(command -v python3)"
start_stats() {   # start_stats PORT KIND [key=value ...]
    local p="$1"; shift
    python3 "$nd/statsmock.py" "$p" "$@" >/dev/null 2>&1 &
    pids+=("$!")
    for _ in $(seq 1 50); do
        python3 -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.1',$p)))" && return 0
        sleep 0.1
    done
    echo "fake stats page did not start on $p"; exit 1
}
make_home() {   # make_home DIR NODE_DIR PORT LOGKIND
    mkdir -p "$1/nodes/$2/conf"
    printf '# the node\nglide.servlet.port = %s\nglide.db.name=prod\n' "$3" > "$1/nodes/$2/conf/glide.properties"
    python3 "$nd/make_logs.py" "$1/nodes/$2/logs" "$4"
}
mkpy() {   # mkpy NAME [VAR=VALUE ...]: an interpreter that sets this host's fakes
    local f="$work/py-$1"; shift
    { echo '#!/bin/sh'; for kv in "$@"; do echo "export $kv"; done; echo "exec $PY \"\$@\""; } > "$f"
    chmod +x "$f"
}
start_stats $((port + 30)) xml
start_stats $((port + 31)) html
start_stats $((port + 32)) xml MEMUSED=3990 SEMQ=3 SCHEDQ=70 ACTIVE=40
start_stats $((port + 33)) 403
make_home "$work/glide01" prod_16000 $((port + 30)) well
make_home "$work/glide12" test_16001 $((port + 31)) well
make_home "$work/glide-bad" prod_16000 $((port + 32)) bad
make_home "$work/glide-403" prod_16000 $((port + 33)) well
mkdir -p "$work/glide03"
# the database host's data: /database/data (MariaDB's files, one level), InnoDB's system, undo and redo files
dd="$work/database/data"
mkdir -p "$dd/snprod" "$dd/snaudit"
head -c 4096 /dev/zero > "$dd/ibdata1"; head -c 2048 /dev/zero > "$dd/undo001"; head -c 1024 /dev/zero > "$dd/ib_logfile0"
head -c 30000 /dev/zero > "$dd/snprod/sys_audit.ibd"; head -c 20000 /dev/zero > "$dd/snprod/syslog.ibd"
head -c 5000 /dev/zero > "$dd/snaudit/old.ibd"
sed "s#/glide/nodes/prod_16000#$work/glide-bad/nodes/prod_16000#" "$nd/denials-node.txt" > "$work/denials-node.txt"
printf -- '----\ntype=PATH msg=audit(1) : item=0 name=%s/ibtmp1 inode=9\ntype=SYSCALL msg=audit(1) : arch=x86_64 syscall=openat success=no exe=/usr/sbin/mariadbd\ntype=FANOTIFY msg=audit(1) : resp=deny\n' "$dd" > "$work/denials-db.txt"
mkpy node01 SNNODE_HOME="$work/glide01" SNNODE_UNITS=snc_prod:active:enabled SNNODE_FAPOLICYD=active
mkpy node12 SNNODE_HOME="$work/glide12" SNNODE_UNITS=snc_test:active:enabled SNNODE_FAPOLICYD=none
mkpy node01bad SNNODE_HOME="$work/glide-bad" SNNODE_UNITS=snc_prod:active:enabled SNNODE_FAPOLICYD=active SNNODE_DENIALS="$work/denials-node.txt"
mkpy node03 SNNODE_HOME="$work/glide03" SNNODE_UNITS= SNNODE_FAPOLICYD=active
mkpy node01x SNNODE_HOME="$work/glide-403" SNNODE_UNITS=snc_prod:active:enabled SNNODE_FAPOLICYD=active
mkpy db SNNODE_FAPOLICYD=inactive FAKE_DATADIR="$dd/"
mkpy dbbad SNNODE_FAPOLICYD=active SNNODE_DENIALS="$work/denials-db.txt" FAKE_DATADIR="$dd/" FAKE_DENY=1
python3 - "$work" <<'EOP'
import datetime, json, os, sys, time
w = sys.argv[1]
now = datetime.datetime.now(datetime.timezone.utc)
t = lambda m: (now - datetime.timedelta(minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
row = lambda sid, st, m: {"system_id": sid, "node_id": sid.replace(":", "_") + "_id", "status": st, "most_recent_message": t(m), "schedulers": "any"}
two = [row("node01:prod_16000", "online", 1), row("node12:test_16001", "online", 1)]
bad = [row("node01:prod_16000", "online", 30), row("node03:prod_16000", "offline", 600), row("node12:test_16001", "online", 1),
       row("node09:prod_16000", "offline", 9000)]
json.dump({"cluster": two}, open(w + "/cluster-ok.json", "w"))
json.dump({"cluster": two[:1]}, open(w + "/cluster-one.json", "w"))
json.dump({"cluster": bad}, open(w + "/cluster-bad.json", "w"))
# the database's size history: 2 days ago 2 MiB smaller (well: +1 MB a day); bad: as if it grew by all the free space in 3 days
st = os.statvfs(w)
free = st.f_bavail * st.f_frsize
total = 48318382080 + 2147483648 + 4096 + 2048 + 1024
json.dump([{"t": int(time.time()) - 2 * 86400, "b": total - 2097152}], open(w + "/dbhist-well.json", "w"))
json.dump([{"t": int(time.time()) - 3 * 86400, "b": -free}], open(w + "/dbhist-bad.json", "w"))
EOP
start_mock $((port + 40)) SNMOCK_MID="$work/cluster-ok.json"
start_mock $((port + 41)) SNMOCK_MID="$work/cluster-bad.json"
start_mock $((port + 42)) SNMOCK_MID="$work/cluster-one.json" SNMOCK_NO_TABLES=sys_cluster_state
start_mock $((port + 43)) SNMOCK_MID="$work/cluster-one.json"
hostline() {   # hostline NAME PY_NAME [more vars]
    echo "        $1.yoursite.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $work/py-$2${3:+, $3}}"
}
nodeinv() {   # nodeinv FILE "node lines" "db lines"
    { echo "all:"; echo "  hosts:"; echo "    localhost: {ansible_connection: local, ansible_python_interpreter: $PY}"
      echo "  children:"; echo "    servicenow_nodes:"; echo "      hosts:"; printf '%s\n' "$2"
      if [ -n "$3" ]; then echo "    servicenow_db_hosts:"; echo "      hosts:"; printf '%s\n' "$3"; fi; } > "$1"
}
GONE12='        node12.yoursite.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh, ansible_ssh_timeout: 3}'
nodeinv "$work/inv-well.yml" "$(hostline node01 node01 "servicenow_node_home: $work/glide01")
$(hostline node12 node12 "servicenow_node_home: $work/glide12")" "$(hostline snowdb01 db 'check_mariadb_container: snowdb')"
nodeinv "$work/inv-bad.yml" "$(hostline node01 node01bad "servicenow_node_home: $work/glide-bad")
$(hostline node03 node03 "servicenow_node_home: $work/glide03")" "$(hostline snowdb01 dbbad 'check_mariadb_container: snowdb')"
nodeinv "$work/inv-403.yml" "$(hostline node01 node01x "servicenow_node_home: $work/glide-403")" ""
nodeinv "$work/inv-two.yml" "$(hostline node01 node01 "servicenow_node_home: $work/glide01")
$GONE12" "$(hostline snowdb01 db 'check_mariadb_container: snowdb')"
nodeinv "$work/inv-one.yml" "$(hostline node01 node01 "servicenow_node_home: $work/glide01")" ""
# no group at all: node01 and node12 are just hosts of the inventory (the Limit names them)
{ echo "all:"; echo "  hosts:"; echo "    localhost: {ansible_connection: local, ansible_python_interpreter: $PY}"
  hostline node01 node01 "servicenow_node_home: $work/glide01" | sed 's/^        /    /'
  hostline node12 node12 "servicenow_node_home: $work/glide12" | sed 's/^        /    /'; } > "$work/inv-nogroup.yml"
# the database: MariaDB in a container on the database host, through tests/mariadb's fake podman
mkdir -p "$work/dbbin"; cp "$here/../mariadb/fakepodman.py" "$work/dbbin/podman"; chmod +x "$work/dbbin/podman"
printf 'snowdb mariadb:11 running healthy\n' > "$work/containers.txt"
export FAKE_CONTAINERS="$work/containers.txt" FAKE_LOG="$work/podman.log" MARIADB_MONITOR_USER=monitor MARIADB_MONITOR_PASSWORD=monpw
export PATH="$nd/bin:$work/dbbin:$PATH"
# this machine's own disk and swap must not decide the result
HOSTOK="{\"servicenow_node_disk_warn_pct\": 101, \"servicenow_node_disk_critical_pct\": 101, \"servicenow_node_swap_warn_pct\": 101,
         \"servicenow_node_stats_interval\": 0.2, \"servicenow_check_mid\": false, \"servicenow_db_paths\": [\"$work/database\"],
         \"servicenow_db_history_file\": \"$work/dbhist-well.json\"}"
cp "$work/dbhist-well.json" "$work/dbhist-well.orig"
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: two nodes (snc_prod here, snc_test there), the cluster, the database host" 0 \
    $((port + 40)) api goodpw -i "$work/inv-well.yml" -e "$HOSTOK" --check
expect "self-hosted well (Check): the nodes' line" 'Nodes: node01 snc_prod running, node12 snc_test running; hosts read: 2 of 2; cluster: 2 of 2 online'
expect "self-hosted well (Check): the database, from information_schema" 'Database: snowdb01.yoursite.mil 47.0 GB (information_schema), '
expect "self-hosted well (Check): its growth" '+1 MB/day'
deny "self-hosted well: a unit not installed is no finding" 'no ServiceNow node unit found'
deny "self-hosted well: ...nor stopped" 'inactive (dead)'
deny "self-hosted well: an old log file is not read" 'in a file the check must not read'
if cmp -s "$work/dbhist-well.json" "$work/dbhist-well.orig"; then echo "ok   - self-hosted well: Check mode writes no history"
else echo "FAIL - self-hosted well: Check mode wrote the history"; fails=$((fails + 1)); fi
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: the same for real - the history written" 0 \
    $((port + 40)) api goodpw -i "$work/inv-well.yml" -e "$HOSTOK"
if [ "$(python3 -c "import json; print(len(json.load(open('$work/dbhist-well.json'))))")" = 2 ]; then echo "ok   - self-hosted well: the history has this run"
else echo "FAIL - self-hosted well: the history was not written"; fails=$((fails + 1)); fi
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: memory, semaphores, the scheduler, the log, no unit, fapolicyd, the cluster, the database full soon" 2 \
    $((port + 41)) api goodpw -i "$work/inv-bad.yml" -e "$HOSTOK" \
    -e "{\"servicenow_db_history_file\": \"$work/dbhist-bad.json\", \"servicenow_db_datadir\": \"$dd\"}"
expect "self-hosted bad: no unit of the three" 'no ServiceNow node unit found on node03.yoursite.mil (looked for snc_prod, snc_test, snc_dev)'
expect "self-hosted bad: JVM memory" 'uses 97.4% of its JVM memory (3990 of 4096 MB'
expect "self-hosted bad: semaphores queued in every read" 'has transactions waiting for a semaphore in every read (3, 3, 3; 2 in use)'
expect "self-hosted bad: the scheduler" 'has 70 scheduled job(s) waiting for its 8 worker(s)'
expect "self-hosted bad: OutOfMemory" 'ran out of memory 1 time(s) in the last 24 h'
expect "self-hosted bad: ...its line in the hint" '# java.lang.OutOfMemoryError: Java heap space'
expect "self-hosted bad: semaphore problems" 'logged 4 semaphore problem(s) in the last 24 h'
expect "self-hosted bad: SEVERE lines grouped" 'logged 144 SEVERE line(s) in the last 24 h (the most frequent 140 times)'
expect "self-hosted bad: ...the line in the hint, without who" '# Transaction cancelled: maximum execution time exceeded for /api/now/table/incident (user …)'
expect "self-hosted bad: fapolicyd denied the node's java" "[CRITICAL] servicenow_node: fapolicyd denied the ServiceNow node's java or install folder on node01.yoursite.mil 1 time(s) today"
expect "self-hosted bad: fapolicyd's other denials, grouped" '[WARNING] servicenow_node: fapolicyd denied 1 program(s) or file(s) on node01.yoursite.mil 2 time(s) today; the most: /usr/bin/bash -> /home/admin/tools/collect.sh (2x)'
expect "self-hosted bad: fapolicyd denied the database server" '[CRITICAL] servicenow_node: fapolicyd denied the database server or its data on snowdb01.yoursite.mil 1 time(s) today'
expect "self-hosted bad: the database's filesystem full soon" "[CRITICAL] servicenow_node: snowdb01.yoursite.mil: at the database's growth"
expect "self-hosted bad: the sizes from the data dir's files when the login is refused" "Database: snowdb01.yoursite.mil "
expect "self-hosted bad: ...said so" "(the data dir's files)"
expect "self-hosted bad: the database host's own check, in the same report" 'mariadb: snowdb01.yoursite.mil: MariaDB refused the login of monitor (the MariaDB monitor credential) in container'
expect "self-hosted bad: a stale heartbeat" 'the cluster node node01:prod_16000 says online, but its last heartbeat was 30 minutes ago'
expect "self-hosted bad: a node of the inventory offline" '[CRITICAL] servicenow_node: the cluster node node03:prod_16000 is offline'
expect "self-hosted bad: a retired node" 'the cluster node node09:prod_16000 is offline (last heartbeat'
expect "self-hosted bad: ...said as one" 'and it is not in the inventory group: a retired node'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: the stats page refused, sys_cluster_state refused - said" 2 \
    $((port + 42)) api goodpw -i "$work/inv-403.yml" -e "$HOSTOK"
expect "self-hosted refused: the stats page" 'the stats page of snc_prod.service on node01.yoursite.mil wants a login this account cannot give'
expect "self-hosted refused: ...and what the account needs" '(the API account needs a role that may read stats.do)'
expect "self-hosted refused: ...what was tried, in the hint" '# tried: http://127.0.0.1:'
expect "self-hosted refused: ...asked again with the login (refused: 403)" '(with the login) -> 403'
deny "self-hosted refused: the login's password is never shown" 'goodpw'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: -vvv - the login sent to the stats page is never printed" 2 \
    $((port + 42)) api goodpw -i "$work/inv-403.yml" -e "$HOSTOK" -vvv
deny "self-hosted -vvv: no password" 'goodpw'
expect "self-hosted refused: sys_cluster_state" 'sys_cluster_state (the cluster nodes): HTTP 403'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: a node host not reached - in the same report" 2 \
    $((port + 40)) api goodpw -i "$work/inv-two.yml" -e "$HOSTOK"
expect "self-hosted unreached: said" 'ServiceNow node host node12.yoursite.mil could not be reached'
expect "self-hosted unreached: the line" 'hosts read: 1 of 2'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: a Limit of one node - no localhost, the others not contacted" 0 \
    $((port + 40)) api goodpw -i "$work/inv-two.yml" -e "$HOSTOK" -l node01.yoursite.mil
expect "self-hosted Limit: the node read, the AAP side on it" 'Nodes: node01 snc_prod running; hosts read: 1 of 1'
expect "self-hosted Limit: the others said" "not in this run's Limit: node12.yoursite.mil, snowdb01.yoursite.mil"
deny "self-hosted Limit: node12 not contacted" 'node12.yoursite.mil could not be reached'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: servicenow_check_nodes false - the instance's view only" 0 \
    $((port + 43)) api goodpw -i "$work/inv-two.yml" -e "$HOSTOK" -e servicenow_check_nodes=false
deny "self-hosted off: no nodes line" 'Nodes: '
deny "self-hosted off: the cluster not read" 'cluster:'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: servicenow_health_part nodes - no MID Server read, under servicenow_node only" 0 \
    $((port + 43)) api goodpw -i "$work/inv-one.yml" -e "$HOSTOK" -e '{"servicenow_check_mid": true, "servicenow_health_part": "nodes"}'
expect "part nodes: the MID Servers not read" 'MID Servers: not read here (servicenow_health_part nodes)'
expect "part nodes: the checks run" 'HEALTHY. Checks run: servicenow_node"'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: servicenow_health_part mids - as before, no node read" 0 \
    $((port + 43)) api goodpw -i "$work/inv-two.yml" -e "$HOSTOK" -e servicenow_health_part=mids -e servicenow_check_mid_hosts=false
deny "part mids: no nodes line" 'Nodes: '
deny "part mids: node12 not contacted" 'node12.yoursite.mil could not be reached'
expect "part mids: the checks run" 'HEALTHY. Checks run: servicenow"'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: no group servicenow_nodes - the Limit's two hosts are the nodes" 0 \
    $((port + 40)) api goodpw -i "$work/inv-nogroup.yml" -e "$HOSTOK" -e servicenow_health_part=nodes -l node01.yoursite.mil,node12.yoursite.mil
expect "no group, a Limit: said where the nodes come from" '2 front-end node(s) (the Limit: no group servicenow_nodes in this inventory): node01.yoursite.mil, node12.yoursite.mil'
expect "no group, a Limit: both read" 'Nodes: node01 snc_prod running, node12 snc_test running; hosts read: 2 of 2; cluster: 2 of 2 online'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: no group and no Limit with part nodes - a clear failure, nothing read" 2 \
    $((port + 40)) api goodpw -i "$work/inv-nogroup.yml" -e "$HOSTOK" -e servicenow_health_part=nodes
expect "no group, no Limit: why" "servicenow_nodes_group 'servicenow_nodes' is not a group in this job's inventory, and there is no Limit naming the nodes: no front-end nodes, so the job would read nothing"
expect "no group, no Limit: the fix" 'Fix: add the group servicenow_nodes, set servicenow_nodes_group to one of those groups, put the nodes in the Limit (node01,node03), or list their stats pages in servicenow_node_stats_pages'
deny "no group, no Limit: no host read" 'Nodes: '
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: servicenow_nodes_group naming a group not in the inventory - a clear failure" 2 \
    $((port + 40)) api goodpw -i "$work/inv-well.yml" -e "$HOSTOK" -e servicenow_health_part=nodes -e servicenow_nodes_group=snow_app
expect "group not there: named" "servicenow_nodes_group 'snow_app' is not a group in this job's inventory"
expect "group not there: the groups listed" 'Groups in this inventory: servicenow_db_hosts, servicenow_nodes.'
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: servicenow_check_db true and no database group - a clear failure" 2 \
    $((port + 40)) api goodpw -i "$work/inv-nogroup.yml" -e "$HOSTOK" -e servicenow_health_part=nodes -e servicenow_check_db=true \
    -l node01.yoursite.mil,node12.yoursite.mil
expect "no database group: named" "servicenow_db_group 'servicenow_db_hosts' is not a group in this job's inventory, and servicenow_check_db is true"
PLAYBOOK=playbooks/servicenow_health.yml scenario "self-hosted: servicenow_health_part mids and no MID Server hosts' group - a clear failure" 2 \
    $((port + 40)) api goodpw -i "$work/inv-well.yml" -e "$HOSTOK" -e servicenow_health_part=mids
expect "no MID group: named" "servicenow_mid_hosts_group 'servicenow_mid_hosts' is not a group in this job's inventory, and servicenow_health_part is mids"

# ---- 1.32.0: the node's own address, a friendly name per node, and nodes read by their stats page only -------------
# The instance's address (glide.servlet.uri) is another address of this host here, the node's port on it answering with
# another node's numbers (memory nearly full): it must never be asked - the node's own page on 127.0.0.1 is read.
make_home "$work/glide-lb" prod_16000 $((port + 34)) well
printf 'glide.servlet.uri = http://127.0.0.2:%s/\n' $((port + 34)) >> "$work/glide-lb/nodes/prod_16000/conf/glide.properties"
python3 "$nd/statsmock.py" $((port + 34)) xml bind=127.0.0.2 MEMUSED=3990 >/dev/null 2>&1 &
pids+=("$!")
for _ in $(seq 1 50); do
    python3 -c "import socket,sys; s=socket.socket(); sys.exit(s.connect_ex(('127.0.0.2',$((port + 34)))))" && break
    sleep 0.1
done
start_stats $((port + 34)) xml
mkpy node01lb SNNODE_HOME="$work/glide-lb" SNNODE_UNITS=snc_prod:active:enabled SNNODE_FAPOLICYD=active
nodeinv "$work/inv-lb.yml" "$(hostline node01 node01lb "servicenow_node_home: $work/glide-lb, servicenow_node_label: Node 1")" ""
webrow() {   # webrow DIR: the Front-end nodes rows of the report written there (its first page), one per line, cells joined with " | "
    python3 - "$1" <<'EOPY'
import glob, json, os, sys
for f in sorted(glob.glob(os.path.join(sys.argv[1], "**", "*.json"), recursive=True)):
    if os.path.basename(f) == "index.json":
        continue
    for s in json.load(open(f)).get("report", {}).get("sections", []):
        if s.get("title") == "Front-end nodes":
            print("columns: " + " | ".join(str(c) for c in s["columns"]))
            for r in s["rows"]:
                print("row: " + " | ".join(str(c) for c in r))
            sys.exit(0)             # the page and its "latest" copy hold the same: one is enough
EOPY
}
mkdir -p "$work/web-lb" "$work/web-pages"
PLAYBOOK=playbooks/servicenow_health.yml scenario "own address: the instance's address answers with other numbers - never asked, the node's own page read" 0 \
    $((port + 43)) api goodpw -i "$work/inv-lb.yml" -e "$HOSTOK" -e "{\"report_web_dest\": \"$work/web-lb\"}"
deny "own address: the other node's memory is not this node's" 'JVM memory'
deny "own address: the instance's address in no line" "127.0.0.2:$((port + 34))"
webrow "$work/web-lb" > "$work/rows.txt"
if grep -qF "row: Node 1 (node01.yoursite.mil) | snc_prod :$((port + 34)) | 127.0.0.1:$((port + 34)) (http) | running, at boot" "$work/rows.txt" \
   && grep -qF "| Stats page answered at |" "$work/rows.txt"; then echo "ok   - own address: the report says which address answered, the friendly name beside the host"
else echo "FAIL - own address: the report's row"; cat "$work/rows.txt"; fails=$((fails + 1)); fi
# a friendly name: the host variable (above), or servicenow_node_labels by host - in the findings too
PLAYBOOK=playbooks/servicenow_health.yml scenario "friendly names: servicenow_node_labels, in the findings beside the host" 2 \
    $((port + 42)) api goodpw -i "$work/inv-403.yml" -e "$HOSTOK" -e '{"servicenow_node_labels": {"node01": "Node 1"}}'
expect "friendly names: the finding names both" 'the stats page of snc_prod.service on Node 1 (node01.yoursite.mil) wants a login this account cannot give'
# servicenow_node_stats_pages: nodes AAP does not log in to, each asked at its own listed address from here. node01 is
# also a node of the group: read once, over SSH (the entry's name is its friendly name).
dead=$((port + 39))
PAGES="{\"servicenow_node_stats_pages\": [
  {\"name\": \"Node 21\", \"url\": \"http://127.0.0.1:$((port + 30))/xmlstats.do\"},
  {\"name\": \"Node 22\", \"url\": \"http://127.0.0.1:$((port + 32))/xmlstats.do\"},
  {\"name\": \"Node 23\", \"url\": \"http://127.0.0.1:$dead/stats.do\"},
  {\"name\": \"First node\", \"url\": \"https://node01.yoursite.mil:$((port + 30))/stats.do\"}]}"
PLAYBOOK=playbooks/servicenow_health.yml scenario "stats pages: three nodes by their stats page only, one of the group over SSH" 2 \
    $((port + 43)) api goodpw -i "$work/inv-one.yml" -e "$HOSTOK" -e "$PAGES" -e servicenow_cluster_check=false -e "{\"report_web_dest\": \"$work/web-pages\"}"
expect "stats pages: said which are read from here, which over SSH" '3 node(s) read by the stats page only, from the AAP side: Node 21, Node 22, Node 23 - not these, read over SSH instead: First node'
expect "stats pages: the same thresholds - memory" 'servicenow_node: the ServiceNow node Node 22 (127.0.0.1) (its stats page only) uses '
expect "stats pages: an entry that does not answer is a finding that names it" "[CRITICAL] servicenow_node: the stats page of the ServiceNow node Node 23 (127.0.0.1) did not answer at http://127.0.0.1:$dead/stats.do"
expect "stats pages: the status line" 'hosts read: 3 of 4'
webrow "$work/web-pages" > "$work/rows.txt"
if [ "$(grep -c '^row: ' "$work/rows.txt")" = 4 ] && [ "$(grep -c '^row: First node (node01.yoursite.mil) | snc_prod ' "$work/rows.txt")" = 1 ] \
   && grep -qF "row: Node 21 (127.0.0.1) | stats page :$((port + 30)) | 127.0.0.1:$((port + 30)) (http) | stats page only" "$work/rows.txt" \
   && grep -qF "row: Node 23 (127.0.0.1) | stats page :$dead | - | NOT READ (stats page only)" "$work/rows.txt"; then
    echo "ok   - stats pages: four rows - node01 once (over SSH, named by its entry), the others say stats page only"
else echo "FAIL - stats pages: the report's rows"; cat "$work/rows.txt"; fails=$((fails + 1)); fi
# no node of the inventory at all: the listed pages are the nodes
{ echo "all:"; echo "  hosts:"; echo "    localhost: {ansible_connection: local, ansible_python_interpreter: $PY}"; } > "$work/inv-none.yml"
ONE="{\"servicenow_node_stats_pages\": [{\"name\": \"Node 21\", \"url\": \"http://127.0.0.1:$((port + 30))/xmlstats.do\"}]}"
PLAYBOOK=playbooks/servicenow_health.yml scenario "stats pages: no node in the inventory - the listed page is the node" 0 \
    $((port + 43)) api goodpw -i "$work/inv-none.yml" -e "$HOSTOK" -e "$ONE" -e servicenow_health_part=nodes
expect "stats pages only: the node's line" 'Nodes: Node 21 stats page read; hosts read: 1 of 1'
expect "stats pages only: the check ran" 'HEALTHY. Checks run: servicenow_node"'
NONE="{\"servicenow_node_stats_pages\": [{\"name\": \"Node 23\", \"url\": \"http://127.0.0.1:$dead/stats.do\"}]}"
PLAYBOOK=playbooks/servicenow_health.yml scenario "stats pages: the one listed page does not answer - never green having read nothing" 2 \
    $((port + 43)) api goodpw -i "$work/inv-none.yml" -e "$HOSTOK" -e "$NONE" -e servicenow_health_part=nodes
expect "stats pages unread: the finding" "the stats page of the ServiceNow node Node 23 (127.0.0.1) did not answer at http://127.0.0.1:$dead/stats.do"
expect "stats pages unread: the line" 'hosts read: 0 of 1'
PLAYBOOK=playbooks/servicenow_health.yml scenario "stats pages: an entry without a url - a clear failure, nothing read" 2 \
    $((port + 43)) api goodpw -i "$work/inv-none.yml" -e "$HOSTOK" -e '{"servicenow_node_stats_pages": [{"name": "Node 21"}]}' -e servicenow_health_part=nodes
expect "stats pages wrong: why" 'servicenow_node_stats_pages, entry 1: needs name and url'
deny "stats pages wrong: nothing read" 'Nodes: '

# servicenow_node_stats_auth form: nodes that answer stats.do only in a logged-in session (single sign-on in front). The
# node stats login's own credential (SN_STATS_USERNAME / SN_STATS_PASSWORD), one login per node, the first node alone
# first; refused there: the others (and the listed pages) send none. Each fake node writes its requests to a log.
start_stats $((port + 35)) html form=stats_reader:St4ts-pw9 log="$work/form35.log"
start_stats $((port + 36)) html form=stats_reader:St4ts-pw9 log="$work/form36.log"
start_stats $((port + 37)) html form=stats_reader:St4ts-pw9 log="$work/form37.log"
make_home "$work/glide-f1" prod_16000 $((port + 35)) well
make_home "$work/glide-f2" prod_16000 $((port + 36)) well
mkpy nodef1 SNNODE_HOME="$work/glide-f1" SNNODE_UNITS=snc_prod:active:enabled SNNODE_FAPOLICYD=active
mkpy nodef2 SNNODE_HOME="$work/glide-f2" SNNODE_UNITS=snc_prod:active:enabled SNNODE_FAPOLICYD=active
nodeinv "$work/inv-form.yml" "$(hostline node04 nodef1 "servicenow_node_home: $work/glide-f1")
$(hostline node05 nodef2 "servicenow_node_home: $work/glide-f2")" ""
FORM='{"servicenow_node_stats_auth": "form", "servicenow_cluster_check": false}'
posts() {   # posts LOG: the logins that node was sent
    if [ -f "$1" ]; then grep -c '^POST /login.do' "$1"; else echo 0; fi
}
formlogs() {   # formlogs NAME WANT35 WANT36: how many logins each of the two nodes was sent
    local got; got="$(posts "$work/form35.log") $(posts "$work/form36.log")"
    if [ "$got" = "$2 $3" ]; then echo "ok   - $1"; else echo "FAIL - $1: logins sent: $got, expected $2 $3"; fails=$((fails + 1)); fi
}
SN_STATS_USERNAME=stats_reader SN_STATS_PASSWORD=St4ts-pw9 PLAYBOOK=playbooks/servicenow_health.yml \
    scenario "form login: both nodes read in a session of the local login page" 0 \
    $((port + 43)) api goodpw -i "$work/inv-form.yml" -e "$HOSTOK" -e "$FORM"
expect "form login: the nodes' line" 'hosts read: 2 of 2'
deny "form login: no finding about the stats page" 'stats page of'
formlogs "form login: one login per node" 1 1
if [ "$(grep -c '^GET /logout.do session=yes' "$work/form35.log")" = 1 ] && [ "$(grep -c '^GET /logout.do session=yes' "$work/form36.log")" = 1 ] \
   && ! grep -q 'redirect' "$work/form35.log" "$work/form36.log" \
   && [ "$(sed -n 1p "$work/form36.log")" = "GET /side_door.do" ]; then
    echo "ok   - form login: each session closed (logout.do), no redirect followed"
else echo "FAIL - form login: the nodes' request logs"; cat "$work/form35.log" "$work/form36.log"; fails=$((fails + 1)); fi
deny "form login: the password is never shown" 'St4ts-pw9'
rm -f "$work"/form3?.log
SN_STATS_USERNAME=stats_reader SN_STATS_PASSWORD=St4ts-pw9 PLAYBOOK=playbooks/servicenow_health.yml \
    scenario "form login: -vvv - no password, no cookie, no sysparm_ck printed" 0 \
    $((port + 43)) api goodpw -i "$work/inv-form.yml" -e "$HOSTOK" -e "$FORM" -vvv
deny "form login -vvv: no password" 'St4ts-pw9'
deny "form login -vvv: no cookie" 'JSESSIONID'
deny "form login -vvv: no session store cookie" 'glide_session_store'
if grep -qE 'sysparm_ck[^a-z ]{1,6}[0-9a-f]{20}|(sess|pre)[0-9a-f]{16}' "$work/out.txt"; then
    echo "FAIL - form login -vvv: a sysparm_ck or a cookie value is printed"; fails=$((fails + 1))
else echo "ok   - form login -vvv: no sysparm_ck value, no cookie value"; fi
deny "form login -vvv: no query of a redirect target (a token there stays with the node)" 't0ken'
# two instances of one node behind one address (both units on one port): one session for the address, used by both units
rm -f "$work"/form3?.log
make_home "$work/glide-f3" prod_16000 $((port + 37)) well
make_home "$work/glide-f3" test_16001 $((port + 37)) well
mkpy nodef3 SNNODE_HOME="$work/glide-f3" SNNODE_UNITS=snc_prod:active:enabled,snc_test:active:enabled SNNODE_FAPOLICYD=active
nodeinv "$work/inv-form3.yml" "$(hostline node06 nodef3 "servicenow_node_home: $work/glide-f3")" ""
SN_STATS_USERNAME=stats_reader SN_STATS_PASSWORD=St4ts-pw9 PLAYBOOK=playbooks/servicenow_health.yml \
    scenario "form login: two units of one node on one address - both read" 0 \
    $((port + 43)) api goodpw -i "$work/inv-form3.yml" -e "$HOSTOK" -e "$FORM" -vvv
expect "form login, one address: the second unit says the session is used again" 'the session this run opened here is used (one login per node address)'
if [ "$(posts "$work/form37.log")" = 1 ] && [ "$(grep -c '^GET /logout.do session=yes' "$work/form37.log")" = 1 ] \
   && [ "$(grep -c '^GET /stats.do session=yes' "$work/form37.log")" = 6 ]; then
    echo "ok   - form login, one address: one login, both units read in it (3 reads each), one logout"
else echo "FAIL - form login, one address: the node's request log"; cat "$work/form37.log"; fails=$((fails + 1)); fi
deny "form login, one address: no password, -vvv too" 'St4ts-pw9'
deny "form login, one address: no query of a redirect target" 't0ken'
# a wrong password: one login on the first node, none on the second, none on a listed page; said once per node
rm -f "$work"/form3?.log
FPAGE="{\"servicenow_node_stats_pages\": [{\"name\": \"Node 27\", \"url\": \"https://127.0.0.1:$((port + 37))/stats.do\"}]}"
SN_STATS_USERNAME=stats_reader SN_STATS_PASSWORD=Wr0ng-pw9 PLAYBOOK=playbooks/servicenow_health.yml \
    scenario "form login: a wrong password - tried once on the first node, not on the others" 2 \
    $((port + 43)) api goodpw -i "$work/inv-form.yml" -e "$HOSTOK" -e "$FORM" -e "$FPAGE" -vvv
expect "form login refused: the finding, with what login.do answered" '[WARNING] servicenow_node: the node stats login was refused on snc_prod.service on node04.yoursite.mil (http://127.0.0.1:'"$((port + 35))"'/login.do: HTTP 302 to /login.do)'
expect "form login refused: the second node says why it was not read" 'the stats page of snc_prod.service on node05.yoursite.mil was not read: the node stats login was refused on node04.yoursite.mil'
expect "form login refused: a listed page too" 'the stats page of the ServiceNow node Node 27 (127.0.0.1) was not read: the node stats login was refused on node04.yoursite.mil'
formlogs "form login refused: one login in the whole run" 1 0
if [ ! -e "$work/form36.log" ] && [ ! -e "$work/form37.log" ]; then echo "ok   - form login refused: the other nodes were sent nothing at all"
else echo "FAIL - form login refused: the other nodes were asked"; cat "$work"/form36.log "$work"/form37.log 2>/dev/null; fails=$((fails + 1)); fi
deny "form login refused: the password is never shown, -vvv too" 'Wr0ng-pw9'
deny "form login refused: no query of a redirect target" 't0ken'
# no "ServiceNow node stats login" credential on the template: said, nothing sent - the API login is never used for it
rm -f "$work"/form3?.log
PLAYBOOK=playbooks/servicenow_health.yml scenario "form login: no node stats login credential - said, nothing sent" 2 \
    $((port + 43)) api goodpw -i "$work/inv-form.yml" -e "$HOSTOK" -e "$FORM"
expect "form login no credential: what it needs" 'servicenow_node_stats_auth form needs the '
expect "form login no credential: names the credential type" 'ServiceNow node stats login'
if [ ! -e "$work/form35.log" ] && [ ! -e "$work/form36.log" ]; then echo "ok   - form login no credential: no request to any node"
else echo "FAIL - form login no credential: a node was asked"; fails=$((fails + 1)); fi
# auto never picks form: with the credential there, the nodes' login page is not asked
SN_STATS_USERNAME=stats_reader SN_STATS_PASSWORD=St4ts-pw9 PLAYBOOK=playbooks/servicenow_health.yml \
    scenario "form login: auto never uses it" 2 \
    $((port + 43)) api goodpw -i "$work/inv-form.yml" -e "$HOSTOK" -e servicenow_cluster_check=false
formlogs "form login: auto sent no login to the login page" 0 0

if [ "$fails" -gt 0 ]; then echo "$fails check(s) failed"; exit 1; fi
echo "all ServiceNow test-ticket, tickets, VM request pickup and ServiceNow health checks (MID Servers, self-hosted nodes) passed"
