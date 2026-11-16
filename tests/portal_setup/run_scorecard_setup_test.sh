#!/usr/bin/env bash
# AAP - set up portal actions, 1.68.1: the STIG scorecard's job template and weekly schedule made through AAP's API (the
# stand-in tests/portal_setup/fake_aap_crud.py), so nobody makes them by hand. Checks: Check first writes nothing and says
# what it would make; a run makes "STIG - weekly scorecard" as workflow 40's check template copied (project, inventory,
# execution environment, credentials, the Variables a person added) with stigman_ckl_import_mode: scorecard, and a weekly
# schedule in report_time_zone; the check template gets stigman_ckl_import_mode: import; a second run changes nothing;
# a template or a schedule that is there is left as it is, no second schedule; a mode a person chose on the check
# template stays, said; portal_setup_split_scorecard: false leaves the check template alone; an empty schedule makes none;
# never a DELETE.
#   bash tests/portal_setup/run_scorecard_setup_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${PORTAL_SCORECARD_TEST_PORT:-18698}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
cat > "$work/vars.json" <<EOT
{"portal_setup_project": "Site automation", "portal_setup_execution_environment": "Site EE",
 "portal_setup_inventories": {"default": "Site hosts", "windows": "Windows hosts"},
 "portal_setup_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "aap": "AAP reader",
                              "act_model_key": "ACT model key", "stigman_api": "STIG Manager API", "stigman_api_writer": "STIG Manager API",
                              "windows_share": "Share account"},
 "portal_setup_portal_user": "svc_site_portal", "report_time_zone": "Europe/Berlin"}
EOT
setup() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token ANSIBLE_NOCOLOR=1 \
        "$ap" -i localhost, playbooks/aap_portal_setup.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
# the stand-in, fresh: one more credential (the share account), and a check template made by hand when $1 is "hand"
fresh() {
    rm -f "$work/state.json" "$work/writes.jsonl"
    "$py" - "$work/state.json" "${1:-none}" "${2:-}" <<'EOPY'
import json, sys
sys.path.insert(0, "tests/portal_setup")
SEED = json.loads(json.dumps(__import__("fake_aap_crud").SEED))
SEED["credentials"].append({"id": 15, "name": "Share account"})
if sys.argv[2] == "hand":
    SEED["next"] += 1
    SEED["job_templates"].append({"id": SEED["next"], "name": "STIG Manager - import checklists - check", "description": "made by a person",
        "job_type": "run", "project": 2, "playbook": "playbooks/stigman_ckl_import.yml", "inventory": 4, "execution_environment": 5,
        "ask_variables_on_launch": True, "ask_limit_on_launch": False, "become_enabled": False, "credentials": [14],
        "extra_vars": json.dumps({"stigman_ckl_import_workflow": True, "stigman_scorecard_collections": ["RHEL Servers"],
                                  "stigman_ckl_import_mode": sys.argv[3] or "import and scorecard",
                                  "report_email_to": "team@example.mil"})})
json.dump(SEED, open(sys.argv[1], "w"))
EOPY
}
cd "$repo" || exit 1

# ---- Check first: nothing written, says what it would make
fresh
setup --check
if [ "$(writes)" = 0 ] && grep -q "template 'STIG - weekly scorecard': would be made" "$work/out.txt" \
    && grep -q "schedule 'Weekly - STIG scorecard': would be made" "$work/out.txt" \
    && grep -q "template 'STIG Manager - import checklists - check': would be made" "$work/out.txt"; then
    pass "Check first: the scorecard template and its schedule would be made, nothing written"
else fail "Check first" "$(writes) writes; $(grep -E "scorecard|Weekly|fatal" "$work/out.txt" | head -8)"; fi

# ---- the run
setup
rc=$?
got="$(st '[(j["playbook"], j["project"], j["inventory"], j["execution_environment"], sorted(j["credentials"]), json.loads(j["extra_vars"])) for j in s["job_templates"] if j["name"] == "STIG - weekly scorecard"]')"
[ $rc = 0 ] && [ "$got" = "[('playbooks/stigman_ckl_import.yml', 2, 3, 5, [14, 15], {'stigman_ckl_import_mode': 'scorecard'})]" ] \
    && pass "a run makes STIG - weekly scorecard: the check's playbook, project, inventory, EE, credentials; mode scorecard, no workflow flag" \
    || fail "the scorecard template" "rc $rc; $got
$(grep -E "scorecard|fatal|msg" "$work/out.txt" | head -8)"
got="$(st '[json.loads(j["extra_vars"]) for j in s["job_templates"] if j["name"] == "STIG Manager - import checklists - check"]')"
[ "$got" = "[{'stigman_ckl_import_mode': 'import', 'stigman_ckl_import_workflow': True}]" ] \
    && pass "workflow 40's check template: stigman_ckl_import_mode import" || fail "check template mode" "$got"
got="$(st '[(x["name"], x["rrule"], x["enabled"]) for x in s["schedules"]]')"
[ "$got" = "[('Weekly - STIG scorecard', 'DTSTART;TZID=Europe/Berlin:20260105T060000 RRULE:FREQ=WEEKLY;BYDAY=MO', True)]" ] \
    && pass "the schedule: weekly Monday 06:00 in report_time_zone" || fail "the schedule" "$got"

# ---- a second run changes nothing
n="$(writes)"
setup
if [ "$(writes)" = "$n" ] && grep -q "changed=0 " "$work/out.txt" && ! grep -qE "would be|: made|: changed" "$work/out.txt"; then
    pass "a second run changes nothing"
else fail "a second run" "$(($(writes) - n)) writes; $(grep -E ": made|: changed" "$work/out.txt" | head)"; fi

# ---- the schedule and the template as a person changed them: left as they are, no second schedule
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
for x in s["schedules"]:
    x["rrule"] = "DTSTART;TZID=Europe/Berlin:20260105T070000 RRULE:FREQ=WEEKLY;BYDAY=TU"
for j in s["job_templates"]:
    if j["name"] == "STIG - weekly scorecard":
        j["extra_vars"] = json.dumps({"stigman_ckl_import_mode": "scorecard", "stigman_scorecard_collections": ["Mine"]})
json.dump(s, open(sys.argv[1], "w"))
EOPY
n="$(writes)"
setup
if [ "$(writes)" = "$n" ] && [ "$(st 'len(s["schedules"])')" = 1 ] && grep -q "its time is not the setting: left as it is" "$work/out.txt" \
    && grep -q "template 'STIG - weekly scorecard': CHANGED BY HAND - left as it is" "$work/out.txt"; then
    pass "a schedule at another time and a template with Variables added by hand: left as they are, one schedule"
else fail "left alone" "$(($(writes) - n)) writes; $(st 'len(s["schedules"])') schedules; $(grep -E "scorecard|Weekly" "$work/out.txt" | head -5)"; fi

# ---- a check template a person made, with their Variables: copied; its mode "import and scorecard" becomes import
fresh hand
setup
got="$(st '[json.loads(j["extra_vars"]) for j in s["job_templates"] if j["name"] == "STIG - weekly scorecard"]')"
[ "$got" = "[{'report_email_to': 'team@example.mil', 'stigman_ckl_import_mode': 'scorecard', 'stigman_scorecard_collections': ['RHEL Servers']}]" ] \
    && pass "the person's check template copied: their Variables kept, mode scorecard, no workflow flag" || fail "copied Variables" "$got
$(grep -E "scorecard|fatal" "$work/out.txt" | head -6)"
got="$(st '[(j["description"], json.loads(j["extra_vars"])) for j in s["job_templates"] if j["name"] == "STIG Manager - import checklists - check"]')"
[ "$got" = "[('made by a person', {'report_email_to': 'team@example.mil', 'stigman_ckl_import_mode': 'import', 'stigman_ckl_import_workflow': True, 'stigman_scorecard_collections': ['RHEL Servers']})]" ] \
    && grep -q "setting 'stigman_ckl_import_mode on STIG Manager - import checklists - check': changed" "$work/out.txt" \
    && pass "their check template: only the mode set to import, everything else as it was" || fail "their check template" "$got"
n="$(writes)"
setup
[ "$(writes)" = "$n" ] && pass "and a second run changes nothing" || fail "second run on theirs" "$(($(writes) - n)) writes; $(grep -E ": made|: changed" "$work/out.txt" | head)"

# ---- a mode a person chose stays, said
fresh hand scorecard
setup
got="$(st '[json.loads(j["extra_vars"])["stigman_ckl_import_mode"] for j in s["job_templates"] if j["name"] == "STIG Manager - import checklists - check"]')"
[ "$got" = "['scorecard']" ] && grep -q "is 'scorecard': left as it is" "$work/out.txt" \
    && pass "a check template whose mode is scorecard: left as it is, said" || fail "other mode" "$got; $(grep -E "stigman_ckl_import_mode" "$work/out.txt" | head -3)"

# ---- portal_setup_split_scorecard false, and no schedule
fresh hand
setup -e '{"portal_setup_split_scorecard": false, "portal_setup_scorecard_schedule": ""}'
got="$(st '[json.loads(j["extra_vars"])["stigman_ckl_import_mode"] for j in s["job_templates"] if j["name"] == "STIG Manager - import checklists - check"]')"
[ "$got" = "['import and scorecard']" ] && [ "$(st 'len(s["schedules"])')" = 0 ] \
    && [ "$(st 'len([j for j in s["job_templates"] if j["name"] == "STIG - weekly scorecard"])')" = 1 ] \
    && pass "portal_setup_split_scorecard: false leaves the check template's mode; an empty schedule makes none" || fail "split false / no schedule" "$got"

# ---- a wrong schedule text is said, nothing half-made
fresh
setup -e '{"portal_setup_scorecard_schedule": "sometimes"}'
if [ $? != 0 ] && grep -q "portal_setup_scorecard_schedule" "$work/out.txt"; then pass "a schedule text that is not understood: said, the run fails"
else fail "bad schedule text" "$(grep -E "schedule|fatal" "$work/out.txt" | head -4)"; fi

if ! grep -q '"method": "DELETE", "path": "\(job_templates\|workflow_job_templates\|schedules\)' "$work/writes.jsonl"; then
    pass "never a template, workflow or schedule deleted"
else fail "a delete" "$(grep DELETE "$work/writes.jsonl" | head -3)"; fi

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
[ "$fails" = 0 ]
