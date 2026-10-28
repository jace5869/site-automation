#!/usr/bin/env bash
# The Tenable.sc scan job (roles/tenable_scan, playbooks/tenable_scan.yml) against a fake Tenable.sc (scmock.py): nothing set
# (what Tenable.sc offers listed, nothing launched); targets without a policy (would launch, said); Check (the
# zones chosen - by address, the narrowest range, by the host's tenable_zone, by its group and by the code in its
# name - nothing launched); the scans launched, one per zone, a host no zone covers said; launched again (the
# scans updated, not made twice); waited for, the findings by severity and host; a refused launch (the job fails,
# or not with tenable_scan_fail false); a wrong key, no credential, a token login; a policy that is not there; too
# many hosts; a name that does not resolve; the report emailed.
#   bash tests/tenable/run_tenable_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
port="${TENABLE_TEST_PORT:-18444}"
smtp="${SMTP_TEST_PORT:-18028}"
pid=""
spid=""
fails=0
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; [ -n "$spid" ] && kill "$spid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
    web01.example.mil: {ansible_host: 192.0.2.10}
    web02.example.mil: {ansible_host: 192.0.2.130}
    web03.example.mil: {ansible_host: 203.0.113.9}
    db01.example.mil: {ansible_host: 192.0.2.11, tenable_zone: Datacenter West}
    xy2app01.example.mil: {ansible_host: 192.0.2.12}
  children:
    web:
      hosts: {web01.example.mil: {}, web02.example.mil: {}, web03.example.mil: {}, db01.example.mil: {}, xy2app01.example.mil: {}}
    dc_west:
      hosts: {xy2app01.example.mil: {}}
INV
LAUNCH='"tenable_scan_target": "web", "tenable_scan_policy": "Basic Network Scan", "tenable_scan_repository": "Main", "tenable_scan_credentials": ["Linux ssh"]'

# mock [VAR=value ...]: (re)start the fake Tenable.sc with a fresh state and an empty log
mock() {
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    : > "$work/log.jsonl"
    env SCMOCK_LOG="$work/log.jsonl" "$@" "$py" "$here/scmock.py" "$port" &
    pid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
}

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output, the fake's log and the mail must contain each TEXT;
# a TEXT starting with ! must NOT be there. TSC_ENV: more environment (e.g. TENABLE_SC_SECRET_KEY=bad). CHECK=1: --check.
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    : > "$work/log.jsonl"
    (cd "$repo" && env TENABLE_SC_URL="http://127.0.0.1:$port" TENABLE_SC_ACCESS_KEY=AK123 TENABLE_SC_SECRET_KEY=SK456 \
        ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 ${TSC_ENV:-} \
        "$ap" -i "$work/inv.yml" playbooks/tenable_scan.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
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
logged() { grep -c -- "$1" "$work/log.jsonl"; }

# ---- nothing set: what Tenable.sc offers -----------------------------------------------------------
mock
run "nothing set: the zones, scanners, policies, repositories and credentials listed, nothing launched" ok '{}' \
    'nothing to scan - 3 zone(s), 2 policies, 1 repositories listed; set tenable_scan_target' 'Datacenter East' '192.0.2.0/25, 198.51.100.10-198.51.100.20' \
    'scanner-east (working)' 'scanner-west (unable to connect)' 'Basic Network Scan (id 7) - ports and services' 'Main (id 1, Local IPv4)' \
    'Linux ssh (id 3, ssh)' 'as svc-tenable' '!"call": "POST /rest/scan"'
run "targets, no policy: the zones chosen, would launch, nothing launched - a host no zone covers said" ok '{"tenable_scan_target": "web"}' \
    'would launch 2 scan(s) in 2 zone(s) for 4 host(s), nothing launched: set tenable_scan_policy and tenable_scan_repository (listed below), 1 host(s) not scanned' \
    "web03.example.mil: no zone's ranges hold 203.0.113.9 (and no tenable_zone, site or default zone): not scanned" \
    'zone Datacenter East: 2 host(s) - web01.example.mil, xy2app01.example.mil' 'zone Datacenter West: 2 host(s) - web02.example.mil, db01.example.mil' \
    '!"call": "POST /rest/scan"'

# ---- Check: the zones chosen, nothing launched -----------------------------------------------------
CHECK=1 run "Check: the zone by address (the narrowest range), by tenable_zone (its address in another zone said), by the group, by the code in the name" ok \
    "{$LAUNCH, \"tenable_scan_sites\": {\"dc_west\": \"Datacenter West\"}}" \
    'Check: would launch 2 scan(s) in 2 zone(s) for 4 host(s)' 'site dc_west' 'tenable_zone' \
    'its address 192.0.2.11 is in the ranges of zone Datacenter East, not of Datacenter West: scanned from Datacenter West as told' \
    'Check: nothing launched' '!"call": "POST /rest/scan"'
CHECK=1 run "Check: the site code from the host's name" ok \
    "{$LAUNCH, \"tenable_scan_site_regex\": \"^([a-z]{2}[0-9])\", \"tenable_scan_sites\": {\"XY2\": \"Datacenter West\"}}" 'site XY2' '!"call": "POST'
CHECK=1 run "Check: tenable_scan_zone_default auto covers the host no zone has" ok "{$LAUNCH, \"tenable_scan_zone_default\": \"auto\"}" \
    'Check: would launch 3 scan(s) in 3 zone(s) for 5 host(s)' '(automatic distribution)' '!host(s) not scanned'

# ---- launched -------------------------------------------------------------------------------------
run "launched: one scan per zone, with the policy, repository and credential; the host no zone covers not scanned; green" ok "{$LAUNCH}" \
    'Tenable.sc scan: 2 scan(s) launched in 2 zone(s) for 4 host(s), 1 host(s) not scanned' '"name": "AAP web - Datacenter East"' '"zone": "1"' \
    '"ipList": "192.0.2.10,192.0.2.12"' '"policy": "7"' '"repository": "1"' '"credentials": ["3"]' '"schedule": "template"' \
    '"call": "POST /rest/scan/101/launch"' '"call": "POST /rest/scan/102/launch"' 'launched' '!Findings by host'
[ "$(logged '"call": "POST /rest/scan"')" = 2 ] && ok=1 || { echo "FAIL - two scans made (log: $(logged '"call": "POST /rest/scan"'))"; fails=$((fails + 1)); }
run "launched again: the scans of that name updated and launched again, not made twice" ok "{$LAUNCH}" \
    '"call": "PATCH /rest/scan/101"' '"call": "PATCH /rest/scan/102"' '"call": "POST /rest/scan/101/launch"' 'updated' '!"call": "POST /rest/scan"'
run "waited for: the scans completed, the findings by severity and by host, the job green, the report red (a critical)" ok \
    "{$LAUNCH, \"tenable_scan_wait\": true, \"tenable_scan_poll_seconds\": 1, \"tenable_scan_wait_minutes\": 2}" \
    '2 scan(s) launched in 2 zone(s) for 4 host(s), 1 critical, 8 high, 1 host(s) not scanned' 'Findings by host, the worst first' \
    'host-10.example.mil' '192.0.2.10' 'launched - completed'

# ---- what goes wrong ------------------------------------------------------------------------------
mock SCMOCK_FAIL_LAUNCH=2
run "a zone whose scan will not launch: said, the job fails (tenable_scan_fail)" fail "{$LAUNCH}" \
    '1 scan(s) launched in 1 zone(s) for 2 host(s), 1 not launched' 'Scan zone unavailable: no scanner of the zone can be reached' \
    'Datacenter West: the scan was not launched (the report says why)'
run "  ... tenable_scan_fail false: a report only" ok "{$LAUNCH, \"tenable_scan_fail\": false}" '1 not launched'
mock
run "a policy Tenable.sc does not offer: said, nothing launched" fail "{$LAUNCH, \"tenable_scan_policy\": \"Nope\"}" \
    'the policy Nope is not one Tenable.sc offers this account (Basic Network Scan, ACAS Best Practice)' '!"call": "POST /rest/scan"'
run "too many hosts: refused before Tenable.sc is asked" fail "{$LAUNCH, \"tenable_scan_max_targets\": 2}" \
    '5 hosts: more than tenable_scan_max_targets (2) - narrow tenable_scan_target, or raise it' '!"call": "POST'
run "a name that does not resolve from here: said, not scanned" ok '{"tenable_scan_targets": ["nores01.invalid", "192.0.2.20"]}' \
    'nores01.invalid: its address nores01.invalid does not resolve from the execution environment: set ansible_host to its IP, or tenable_zone' \
    'would launch 1 scan(s) in 1 zone(s) for 1 host(s)'
TSC_ENV="TENABLE_SC_SECRET_KEY=bad" run "a wrong API key: said plainly, the job fails" fail '{}' \
    'Tenable.sc refused GET rest/currentUser (HTTP 403): Invalid credentials. Please try again.'
TSC_ENV="TENABLE_SC_URL= TENABLE_SC_ACCESS_KEY=" run "no credential: what to attach" fail '{}' \
    'No Tenable.sc connection: attach a credential of type'
TSC_ENV="TENABLE_SC_ACCESS_KEY= TENABLE_SC_SECRET_KEY= TENABLE_SC_USERNAME=svc-tenable TENABLE_SC_PASSWORD=goodpw" run "a user name and password: a token login, logged out at the end" ok '{}' \
    '"call": "POST /rest/token"' 'as svc-tenable' '"call": "DELETE /rest/token"'
TSC_ENV="TENABLE_SC_ACCESS_KEY= TENABLE_SC_SECRET_KEY= TENABLE_SC_USERNAME=svc-tenable TENABLE_SC_PASSWORD=wrong" run "a wrong password" fail '{}' \
    'Tenable.sc refused the login of svc-tenable (HTTP 403): Invalid login credentials.'

# ---- emailed --------------------------------------------------------------------------------------
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done
run "the report emailed" ok "{$LAUNCH, \"report_email_to\": [\"scans@example.mil\"], \"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $smtp, \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\"}" \
    'Tenable.sc scan: 2 scan(s) launched' 'emailed'
ls "$work"/mail/*.eml >/dev/null 2>&1 && grep -q "Tenable.sc scan" "$work"/mail/*.eml && echo "ok   -   ... the mail has the report" || { echo "FAIL - no mail with the report"; fails=$((fails + 1)); }

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all Tenable.sc scan scenarios passed"
