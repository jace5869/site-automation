#!/usr/bin/env bash
# AAP - set up portal actions (1.67.0 part C, playbooks/aap_portal_setup.yml) against a stand-in AAP that keeps what it is
# given (tests/portal_setup/fake_aap_crud.py, its state a JSON file the test reads and changes by hand). Checks: Check
# first writes nothing and says what it would make; a run makes every job template and workflow of workflows.yml (the
# nodes, the approval node, the links, the credentials) and gives the portal's account Execute (Approve on the
# workflow with an approval node) and nothing more; a second run changes nothing; a template changed by hand is listed
# and left as it is, written again with portal_setup_overwrite; one of the same name the job did not make is left
# alone; a credential kind not named skips its template, said; a schedule made once; never a DELETE of a template or a
# workflow.
#   bash tests/portal_setup/run_portal_setup_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${PORTAL_SETUP_TEST_PORT:-18697}"
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
cat > "$work/vars.json" <<EOF
{"portal_setup_project": "Site automation", "portal_setup_execution_environment": "Site EE",
 "portal_setup_inventories": {"default": "Site hosts", "windows": "Windows hosts"},
 "portal_setup_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "aap": "AAP reader",
                              "act_model_key": "ACT model key", "stigman_api": "STIG Manager API", "stigman_api_writer": "STIG Manager API"},
 "portal_setup_portal_user": "svc_site_portal"}
EOF
setup() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token ANSIBLE_NOCOLOR=1 \
        "$ap" -i localhost, playbooks/aap_portal_setup.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }

# ---- Check first: nothing written
setup --check
if [ "$(writes)" = 0 ] && grep -q "workflow 'ACT - triage finding': would be made" "$work/out.txt" \
    && grep -q "template 'STIG Manager - import checklists - check': skipped - no credential of kind windows_share" "$work/out.txt"; then
    pass "Check first: says what it would make (and skip), writes nothing"
else fail "Check first" "$(writes) writes; $(grep -E "would be|skipped|fatal" "$work/out.txt" | head -12)"; fi

# ---- the run: everything made
setup -e '{"portal_setup_schedules": [{"name": "Recheck every 15 minutes", "target": "STIG - recheck one host", "every_minutes": 15}]}'
rc=$?
if [ $rc = 0 ] && [ "$(st 'len(s["job_templates"])')" = 6 ] && [ "$(st 'len(s["workflow_job_templates"])')" = 2 ]; then
    pass "a run: 6 job templates (the gate too) and 2 workflows made (40's check is skipped without a share credential, so is 40)"
else fail "the run" "rc $rc; $(st 'sorted(x["name"] for x in s["job_templates"]+s["workflow_job_templates"])')
$(grep -E "fatal|FAILED|msg" "$work/out.txt" | head -10)"; fi
got="$(st '[(n["identifier"], [ [m["identifier"] for m in s["nodes"] if m["id"] in n["always"]] ]) for n in s["nodes"] if n["wf"] == [w for w in s["workflow_job_templates"] if w["name"] == "ACT - triage finding"][0]["id"]]')"
[ "$got" = "[('linux', [['windows']]), ('windows', [[]])]" ] && pass "workflow 44: linux -> always -> windows" || fail "workflow 44's nodes" "$got"
got="$(st 'sorted(c for j in s["job_templates"] if j["name"] == "Windows ACT - triage finding" for c in j["credentials"])')"
[ "$got" = "[11, 12, 13]" ] && pass "the Windows triage template: its three credentials" || fail "credentials" "$got"
got="$(st '[(j["inventory"], j["ask_variables_on_launch"], j["ask_limit_on_launch"], j["execution_environment"], j["extra_vars"]) for j in s["job_templates"] if j["name"] == "Windows ACT - triage finding"][0]')"
[ "$got" = "(4, True, True, 5, '{\"portal_triage_os\": \"windows\"}')" ] && pass "its inventory, prompts, EE and Variables" || fail "its fields" "$got"
got="$(st 'sorted(r["name"] for u in s["users"] for rid in u["roles"] for r in s["roles"] if r["id"] == rid)')"
[ "$got" = "['Execute', 'Execute']" ] && pass "the portal's account: Execute on workflows 44 and 45 only (40 is not there)" || fail "grants" "$got"
got="$(st '[(x["name"], x["rrule"]) for x in s["schedules"]]')"
case "$got" in *"Recheck every 15 minutes"*"INTERVAL=15"*) pass "the schedule";; *) fail "the schedule" "$got";; esac

# ---- a second run changes nothing
n="$(writes)"
setup -e '{"portal_setup_schedules": [{"name": "Recheck every 15 minutes", "target": "STIG - recheck one host", "every_minutes": 15}]}'
if [ "$(writes)" = "$n" ] && grep -q "changed=0 " "$work/out.txt" && ! grep -qE "would be|: made|: changed" "$work/out.txt"; then
    pass "a second run changes nothing"
else fail "a second run changes nothing" "$(($(writes) - n)) writes; $(grep -E ": made|: changed|changed=" "$work/out.txt" | head)"; fi

# ---- a hand edit: listed, left alone; Check shows the field; overwrite writes it again
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
for j in s["job_templates"]:
    if j["name"] == "STIG recheck":
        j["playbook"] = "playbooks/by_hand.yml"
json.dump(s, open(sys.argv[1], "w"))
EOPY
n="$(writes)"
setup --check -e '{"portal_setup_overwrite": true}'
if [ "$(writes)" = "$n" ] && grep -q "template 'STIG recheck': would be changed" "$work/out.txt" \
    && grep -q "playbook: playbooks/by_hand.yml -> playbooks/stig_recheck.yml" "$work/out.txt"; then
    pass "Check with overwrite: the field it would change, nothing written"
else fail "Check shows the hand edit" "$(grep -A3 "STIG recheck" "$work/out.txt" | head)"; fi
setup
if [ "$(st '[j["playbook"] for j in s["job_templates"] if j["name"] == "STIG recheck"][0]')" = "playbooks/by_hand.yml" ] \
    && grep -q "template 'STIG recheck': CHANGED BY HAND - left as it is" "$work/out.txt"; then
    pass "a template changed by hand: listed, left as it is"
else fail "hand edit left alone" "$(grep "STIG recheck" "$work/out.txt" | head -3)"; fi
setup -e '{"portal_setup_overwrite": true}'
if [ "$(st '[j["playbook"] for j in s["job_templates"] if j["name"] == "STIG recheck"][0]')" = "playbooks/stig_recheck.yml" ]; then
    pass "portal_setup_overwrite: written again"
else fail "overwrite" "$(grep "STIG recheck" "$work/out.txt" | head -3)"; fi

# ---- one of that name the job did not make: left alone; a node changed by hand on its workflow: listed
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
s["next"] += 1
s["job_templates"].append({"id": s["next"], "name": "STIG Manager - import checklists - check", "description": "made by a person",
                           "playbook": "playbooks/stigman_ckl_import.yml", "credentials": []})
for w in s["workflow_job_templates"]:
    if w["name"] == "STIG - recheck one host":
        for n in s["nodes"]:
            if n["wf"] == w["id"] and n["identifier"] == "linux":
                n["always"] = []
json.dump(s, open(sys.argv[1], "w"))
EOPY
setup -e '{"portal_setup_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "aap": "AAP reader",
            "act_model_key": "ACT model key", "stigman_api": "STIG Manager API", "stigman_api_writer": "STIG Manager API",
            "windows_share": "STIG Manager API"}}'
if grep -q "template 'STIG Manager - import checklists - check': NOT MADE BY THIS JOB - left as it is" "$work/out.txt" \
    && grep -q "workflow 'STIG - recheck one host': CHANGED BY HAND - left as it is" "$work/out.txt" \
    && [ "$(st '[j["description"] for j in s["job_templates"] if j["name"] == "STIG Manager - import checklists - check"][0]')" = "made by a person" ]; then
    pass "one the job did not make, and a workflow whose node was changed by hand: listed, left as they are"
else fail "not ours / node changed" "$(grep -E "import checklists - check|recheck one host" "$work/out.txt" | head)"; fi
if grep -q "workflow 'Checklists imported into STIG Manager': made" "$work/out.txt"; then
    pass "workflow 40 made, on the template that was there (its approval node too)"
else fail "workflow 40" "$(grep "Checklists" "$work/out.txt" | head -3)"; fi
got="$(st '[(n["identifier"], [m["identifier"] for m in s["nodes"] if m["id"] in n["failure"]], [m["identifier"] for m in s["nodes"] if m["id"] in n["success"]], n["extra_data"]) for n in s["nodes"] if n["wf"] == [w for w in s["workflow_job_templates"] if w["name"] == "Checklists imported into STIG Manager"][0]["id"]]')"
[ "$got" = "[('check', ['gate'], [], {}), ('gate', [], ['approve'], {'workflow_gate_var': 'stigman_update_plan'}), ('approve', [], ['update'], {}), ('update', [], [], {})]" ] \
    && pass "workflow 40: check -> on failure -> the gate (workflow_gate_var) -> on success -> the approval -> update" || fail "40's gate" "$got"
got="$(st '[(j["playbook"], j["ask_variables_on_launch"], j["credentials"]) for j in s["job_templates"] if j["name"] == "Workflow gate - has a plan"][0]')"
[ "$got" = "('playbooks/workflow_gate.yml', True, [])" ] && pass "the gate template: its playbook, Variables on launch, no credential" || fail "gate template" "$got"
got="$(st '[(a["name"][:20], a["timeout"]) for a in s["approvals"]]')"
[ "$got" = "[('Write the checklists', 28800)]" ] && pass "its approval node: name and timeout" || fail "approval node" "$got"
got="$(st 'sorted(r["name"] for u in s["users"] for rid in u["roles"] for r in s["roles"] if r["id"] == rid)')"
[ "$got" = "['Approve', 'Execute', 'Execute', 'Execute']" ] && pass "the portal's account: Execute and Approve on 40" || fail "grants on 40" "$got"
if ! grep -q '"method": "DELETE", "path": "\(job_templates\|workflow_job_templates\)' "$work/writes.jsonl"; then
    pass "never a template or a workflow deleted"
else fail "a delete" "$(grep DELETE "$work/writes.jsonl" | head -3)"; fi

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
[ "$fails" = 0 ]
