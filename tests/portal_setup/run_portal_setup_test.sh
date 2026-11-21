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
if [ $rc = 0 ] && [ "$(st 'len(s["job_templates"])')" = 20 ] && [ "$(st 'len(s["workflow_job_templates"])')" = 8 ]; then
    pass "a run: 20 job templates (the gate too) and 8 workflows made (40's check is skipped without a share credential, so is 40; 46 to 48 and their 4 templates are part D's, 49 to 51 and their 10 part E's)"
else fail "the run" "rc $rc; $(st 'sorted(x["name"] for x in s["job_templates"]+s["workflow_job_templates"])')
$(grep -E "fatal|FAILED|msg" "$work/out.txt" | head -10)"; fi
got="$(st '[(n["identifier"], [ [m["identifier"] for m in s["nodes"] if m["id"] in n["always"]] ]) for n in s["nodes"] if n["wf"] == [w for w in s["workflow_job_templates"] if w["name"] == "ACT - triage finding"][0]["id"]]')"
[ "$got" = "[('linux', [['windows']]), ('windows', [[]])]" ] && pass "workflow 44: linux -> always -> windows" || fail "workflow 44's nodes" "$got"
got="$(st 'sorted(c for j in s["job_templates"] if j["name"] == "Windows ACT - triage finding" for c in j["credentials"])')"
[ "$got" = "[11, 12, 13]" ] && pass "the Windows triage template: its three credentials" || fail "credentials" "$got"
got="$(st '[(j["inventory"], j["ask_variables_on_launch"], j["ask_limit_on_launch"], j["execution_environment"], j["extra_vars"]) for j in s["job_templates"] if j["name"] == "Windows ACT - triage finding"][0]')"
[ "$got" = "(4, True, True, 5, '{\"portal_triage_os\": \"windows\"}')" ] && pass "its inventory, prompts, EE and Variables" || fail "its fields" "$got"
got="$(st 'sorted(r["name"] for u in s["users"] for rid in u["roles"] for r in s["roles"] if r["id"] == rid)')"
[ "$got" = "['Approve', 'Approve', 'Approve', 'Approve', 'Approve', 'Approve', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute']" ] \
    && pass "the portal's account: Execute on workflows 44 to 51 and the two jobs (space request, open ticket), Approve on 46 to 51 (40 is not there)" || fail "grants" "$got"
# 1.67.0 part D: workflow 46 - the Linux and the Windows recheck each to its own gate, both gates to the approval; 47's chain
got="$(st '[(n["identifier"], [m["identifier"] for m in s["nodes"] if m["id"] in n["success"] + n["failure"]]) for n in s["nodes"] if n["wf"] == [w for w in s["workflow_job_templates"] if w["name"] == "STIG - recheck and update STIG Manager"][0]["id"]]')"
[ "$got" = "[('linux', ['gate_linux']), ('windows', ['gate_windows']), ('gate_linux', ['approve']), ('gate_windows', ['approve']), ('approve', ['update']), ('update', [])]" ] \
    && pass "workflow 46: each recheck to its own gate on fail, both gates to the approval" || fail "workflow 46's nodes" "$got"
got="$(st '[(n["identifier"], [m["identifier"] for m in s["nodes"] if m["id"] in n["success"] + n["failure"]]) for n in s["nodes"] if n["wf"] == [w for w in s["workflow_job_templates"] if w["name"] == "STIG - fix one rule"][0]["id"]]')"
[ "$got" = "[('check', ['gate']), ('gate', ['approve']), ('approve', ['apply']), ('apply', ['recheck']), ('recheck', ['gate_update']), ('gate_update', ['update']), ('update', [])]" ] \
    && pass "workflow 47: check -> gate -> approve -> apply -> recheck -> gate -> update" || fail "workflow 47's nodes" "$got"
got="$(st '[(x["name"], x["rrule"]) for x in s["schedules"]]')"
case "$got" in *"Recheck every 15 minutes"*"INTERVAL=15"*) pass "the schedule";; *) fail "the schedule" "$got";; esac
# 1.79.11: the catalog's five lines (aap/job_templates.yml); 1.93.0: nothing after them - no fingerprint text
got="$(st '[(x["name"], x["description"].startswith("** Does: "), "\n** Supports: Run constraint=" in x["description"], "fingerprint" in x["description"] or "[set up by AAP" in x["description"]) for x in s["workflow_job_templates"] + s["job_templates"] if x["name"] in ("STIG - recheck one host", "STIG recheck")]')"
[ "$got" = "[('STIG - recheck one host', True, True, False), ('STIG recheck', True, True, False)]" ] \
    && pass "the five lines on its workflows and templates (the catalog's), and no fingerprint text after them" || fail "the descriptions" "$got"
got="$(st 'sum(1 for x in s["workflow_job_templates"] + s["job_templates"] if "fingerprint" in x["description"])')"
[ "$got" = 0 ] && pass "no description of any template or workflow carries a fingerprint" || fail "fingerprint text somewhere" "$got"

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

# ---- 1.93.0: the fingerprints go on in a job artifact (aap_setup_fingerprints), never in a description
export ANSIBLE_SHOW_CUSTOM_STATS=1
cat > "$work/stats.py" <<'EOPY'
import json, sys
t = open(sys.argv[1]).read()
i = t.rfind("RUN: {")
m = json.JSONDecoder().raw_decode(t[i + 5:].lstrip())[0] if i >= 0 else {}
print(json.dumps(m.get("aap_setup_fingerprints", {})))
EOPY
stats() { "$py" "$work/stats.py" "$work/out.txt"; }
seed_job() {    # seed_job ID  - a successful run of template 7 that handed on the map of the last run
    "$py" - "$work/state.json" "$1" "$(stats)" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
s.setdefault("jobs", []).append({"id": int(sys.argv[2]), "job_template": 7, "status": "successful", "finished": "2026-11-21T06:00:00Z",
                                 "artifacts": {"aap_setup_fingerprints": json.loads(sys.argv[3])}})
json.dump(s, open(sys.argv[1], "w"))
EOPY
}
run_as() { setup -e "{\"awx_job_id\": $1, \"awx_job_template_id\": 7}" "${@:2}"; }
desc_of() { st '[j["description"] for j in s["job_templates"] if j["name"] == "STIG recheck"][0]'; }
run_as 100
fp="$(stats | "$py" -c 'import json,sys; m=json.load(sys.stdin); print(m.get("template/STIG recheck", ""))')"
if [ "${#fp}" = 12 ] && [ "$(stats | "$py" -c 'import json,sys; print(len(json.load(sys.stdin)))')" -ge 20 ] && ! desc_of | grep -q fingerprint; then
    pass "a first run (no earlier run in AAP): compared with the text it would write, all ours; the map goes on as the job artifact (a 12-character fingerprint each), the description has none"
else fail "first run's artifact" "fp=$fp; $(grep -n "CUSTOM STATS" -A4 "$work/out.txt" | head -8)"; fi
seed_job 90
n="$(writes)"
run_as 101
if [ "$(writes)" = "$n" ] && grep -q "template 'STIG recheck': as it should be" "$work/out.txt" && ! grep -qE "would be|: made|: changed|BY HAND|NOT MADE" "$work/out.txt"; then
    pass "a second run, with the last run's artifact: already right, nothing written"
else fail "second run with an artifact" "$(($(writes) - n)) writes; $(grep -E "STIG recheck|BY HAND" "$work/out.txt" | head -4)"; fi
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
for j in s["job_templates"]:
    if j["name"] == "STIG recheck":
        j["playbook"] = "playbooks/by_hand.yml"
json.dump(s, open(sys.argv[1], "w"))
EOPY
run_as 102
if grep -q "template 'STIG recheck': CHANGED BY HAND - left as it is - changed by hand since this job wrote it" "$work/out.txt" \
    && [ "$(st '[j["playbook"] for j in s["job_templates"] if j["name"] == "STIG recheck"][0]')" = "playbooks/by_hand.yml" ]; then
    pass "a hand edit, seen against the artifact's fingerprint: said, left as it is"
else fail "hand edit with an artifact" "$(grep "STIG recheck" "$work/out.txt" | head -3)"; fi
seed_job 91                      # this run kept the edited one's fingerprint, so the next still sees it
run_as 103 -e '{"portal_setup_overwrite": true}'
[ "$(st '[j["playbook"] for j in s["job_templates"] if j["name"] == "STIG recheck"][0]')" = "playbooks/stig_recheck.yml" ] \
    && pass "with the artifact and portal_setup_overwrite: written again" || fail "overwrite with an artifact" "$(grep "STIG recheck" "$work/out.txt" | head -3)"
seed_job 92
# the spec changed (not AAP): with the artifact AAP's copy is the one this job wrote, so it is brought up to date; with no earlier run it could only say "changed by hand"
n="$(writes)"
run_as 104 -e '{"portal_setup_describe": false}'
if grep -q "template 'STIG recheck': changed" "$work/out.txt" && [ "$(writes)" -gt "$n" ] && ! grep -q "BY HAND" "$work/out.txt"; then
    pass "what the job writes changed in workflows.yml and AAP still has what it wrote: brought up to date, not called hand-edited"
else fail "spec changed, artifact" "$(grep -E "STIG recheck|BY HAND" "$work/out.txt" | head -4)"; fi
setup -e '{"portal_setup_describe": true}' -e '{"portal_setup_overwrite": true}'      # back to the catalog's five lines (no artifact read)
# an old description mark is this job's: Check says it would take it off, the next Run does, the fingerprint stays in the map
setup --check
"$py" - "$work/state.json" "$(setup >/dev/null; stats | "$py" -c 'import json,sys; print(json.load(sys.stdin)["template/STIG recheck"])')" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
for j in s["job_templates"]:
    if j["name"] == "STIG recheck":
        j["description"] += " [set up by AAP - set up portal actions, fingerprint %s]" % sys.argv[2]
json.dump(s, open(sys.argv[1], "w"))
EOPY
n="$(writes)"
setup --check
if [ "$(writes)" = "$n" ] && grep -q "template 'STIG recheck': would be changed - would remove the old fingerprint text" "$work/out.txt" && desc_of | grep -q "fingerprint"; then
    pass "an old fingerprint mark, Check: says it would remove the old fingerprint text, writes nothing"
else fail "migration check" "$(($(writes) - n)) writes; $(grep "STIG recheck" "$work/out.txt" | head -3)"; fi
setup
if [ "$(desc_of | grep -c fingerprint)" = 0 ] && grep -q "template 'STIG recheck': changed - removed the old fingerprint text" "$work/out.txt" \
    && [ "$(stats | "$py" -c 'import json,sys; print(len(json.load(sys.stdin)["template/STIG recheck"]))')" = 12 ]; then
    pass "an old fingerprint mark, Run: taken off, the rest of the description kept, the fingerprint in the new map"
else fail "migration run" "$(desc_of | tail -2); $(grep "STIG recheck" "$work/out.txt" | head -3)"; fi
# an old mark whose fingerprint does not match what is there: changed by hand since - never touched
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
for j in s["job_templates"]:
    if j["name"] == "STIG recheck":
        j["playbook"] = "playbooks/by_hand.yml"
        j["description"] += " [set up by AAP - set up portal actions, fingerprint 0123456789ab]"
json.dump(s, open(sys.argv[1], "w"))
EOPY
n="$(writes)"
setup
if [ "$(writes)" = "$n" ] && grep -q "template 'STIG recheck': CHANGED BY HAND - left as it is" "$work/out.txt" && desc_of | grep -q "fingerprint 0123456789ab"; then
    pass "an old mark that no longer matches: changed by hand, the description (mark included) left as it is"
else fail "old mark, hand edit" "$(grep "STIG recheck" "$work/out.txt" | head -3)"; fi
setup -e '{"portal_setup_overwrite": true}'
unset ANSIBLE_SHOW_CUSTOM_STATS

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
if grep -q "template 'STIG Manager - import checklists - check': CHANGED BY HAND - left as it is - differs from what this job writes, and there is no earlier run to compare with" "$work/out.txt" \
    && grep -q "workflow 'STIG - recheck one host': CHANGED BY HAND - left as it is" "$work/out.txt" \
    && [ "$(st '[j["description"] for j in s["job_templates"] if j["name"] == "STIG Manager - import checklists - check"][0]')" = "made by a person" ]; then
    pass "one the job did not make (no earlier run to compare with: said so), and a workflow whose node was changed by hand: listed, left as they are"
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
[ "$got" = "[(\"Write this rule's re\", 28800), ('Run the commands ACT', 28800), ('Create the asset and', 28800), ('Approve the clean up', 86400), ('Restart this service', 86400), ('Allow this program?', 86400), ('Write the checklists', 28800)]" ] \
    && pass "the approval nodes: name and timeout (46 to 51's, and 40's)" || fail "approval node" "$got"
got="$(st 'sorted(r["name"] for u in s["users"] for rid in u["roles"] for r in s["roles"] if r["id"] == rid)')"
[ "$got" = "['Approve', 'Approve', 'Approve', 'Approve', 'Approve', 'Approve', 'Approve', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute', 'Execute']" ] \
    && pass "the portal's account: Execute and Approve on 40 as well" || fail "grants on 40" "$got"
# ---- 1.75.3: the Auditor grant (portal_setup_portal_auditor), and the portal's own templates and workflows
roles_of_user() { st 'sorted(r["name"] + ":" + r["kind"] for u in s["users"] for rid in u["roles"] for r in s["roles"] if r["id"] == rid and r["kind"] == "organizations")'; }
got="$(roles_of_user)"
[ "$got" = "[]" ] && pass "portal_setup_portal_auditor false (the default): no role on the organization" || fail "no auditor by default" "$got"
setup --check -e '{"portal_setup_portal_auditor": true}'
if grep -q "grant 'Default: Auditor': would be made" "$work/out.txt" && [ "$(roles_of_user)" = "[]" ]; then pass "Check first: says it would give Auditor on the organization, gives nothing"; else fail "check of the auditor" "$(grep -i "auditor\|fatal" "$work/out.txt" | head -5)"; fi
setup -e '{"portal_setup_portal_auditor": true}'
got="$(roles_of_user)"
[ "$got" = "['Auditor:organizations']" ] && pass "portal_setup_portal_auditor true: Auditor on the organization, and no other role of it (never a write role)" || fail "auditor grant" "$got"
setup -e '{"portal_setup_portal_auditor": true}'
[ "$(roles_of_user)" = "['Auditor:organizations']" ] && grep -q "grant 'Default: Auditor': as it should be" "$work/out.txt" && pass "again: nothing changes" || fail "auditor again" "$(grep -i auditor "$work/out.txt" | head -3)"
n_before="$(st 'len(s["job_templates"])')"
cat > "$work/own.json" <<EOF
{"portal_actions_extra": {"health_one_host": {"title": "Health check of one host", "summary": "Runs the Linux health check on one host.", "area": "Health",
   "aap": {"type": "job", "name": "Health check - Linux (portal)"}, "vars": {"target": {"kind": "inventory_host", "label": "Host"}}, "limit_from": "target", "approval": false, "fixes": false}},
 "portal_setup_templates_extra": [{"name": "Health check - Linux (portal)", "playbook": "playbooks/health_check.yml", "credentials": ["machine"],
   "prompts": {"variables": true, "limit": true}, "description": "The Linux health check on one host, for the portal"}]}
EOF
setup -e "@$work/own.json"
if [ "$(st 'len(s["job_templates"])')" = "$((n_before + 1))" ] && grep -q "template 'Health check - Linux (portal)': made" "$work/out.txt" \
    && grep -q "grant 'Health check - Linux (portal): Execute': made" "$work/out.txt"; then
    pass "portal_setup_templates_extra: your own template is made, and the portal's account gets Execute on it (portal_actions_extra names it)"
else fail "your own template" "$(grep -i "health check - linux\|fatal\|msg" "$work/out.txt" | head -6)"; fi
setup -e '{"portal_setup_templates_extra": [{"name": "ACT - triage finding", "playbook": "playbooks/act_triage_finding.yml"}]}'
if [ $? != 0 ] && grep -q "portal_setup_templates_extra: 'ACT - triage finding' is already one of the templates here" "$work/out.txt"; then pass "a name a built-in template has: an error naming the setting, nothing changed"; else fail "a clash" "$(tail -5 "$work/out.txt")"; fi
setup -e '{"portal_actions_extra": {"stig_recheck": {"title": "x", "aap": {"type": "job", "name": "n"}}}}'
if [ $? != 0 ] && grep -q "portal_actions_extra: 'stig_recheck' is already an action here" "$work/out.txt"; then pass "an action named like a built-in: an error naming the setting, nothing changed"; else fail "an action clash" "$(tail -5 "$work/out.txt")"; fi
# ---- 1.78.1: your own jobs on the portal by name pattern (the label + Execute), and off again (the label only)
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
for kind, names in (("job_templates", ["Site - health linux", "Site - health windows", "Other tool"]), ("workflow_job_templates", ["Site - cleanup flow"])):
    for nm in names:
        s["next"] += 1
        s[kind].append({"id": s["next"], "name": nm, "organization": 1, "credentials": [], "label_ids": []})
json.dump(s, open(sys.argv[1], "w"))
EOPY
pubvars='{"portal_publish_templates": ["site - HEALTH *", "Site - cleanup flow", "No such *"], "portal_publish_area": {"site - health *": "Health"}}'
labels_of() { st '"|".join(sorted(l["name"] for l in s.get("labels", []) if l["id"] in [j for t in s["job_templates"] + s["workflow_job_templates"] if t["name"] == "'"$1"'" for j in t.get("label_ids", [])]))'; }
execs() { st 'sorted(r["obj"] for u in s["users"] for rid in u["roles"] for r in s["roles"] if r["id"] == rid and r["name"] == "Execute" and r["obj"] in [t["id"] for t in s["job_templates"] + s["workflow_job_templates"] if t["name"].startswith("Site - ")])'; }
n="$(writes)"
setup --check -e "$pubvars"
if [ "$(writes)" = "$n" ] && grep -q "label 'Site - health linux: portal': would label" "$work/out.txt" \
    && grep -q "label 'Site - health linux: portal-area:Health': would label" "$work/out.txt" \
    && grep -q "publish grant 'Site - health linux: Execute': would grant" "$work/out.txt" \
    && grep -q "label 'Site - cleanup flow: portal': would label" "$work/out.txt" \
    && grep -q "publish 'No such \*': skipped - matches no job template or workflow" "$work/out.txt" \
    && ! grep -q "Other tool" "$work/out.txt" && [ "$(labels_of 'Site - health linux')" = "" ]; then
    pass "publish, Check first: would label / would grant, a pattern that matches nothing is said, an unmatched template untouched, nothing written"
else fail "publish check" "$(writes) writes; $(grep -E "label|publish|fatal" "$work/out.txt" | head -12)"; fi
setup -e "$pubvars"
if [ "$(labels_of 'Site - health linux')" = "portal|portal-area:Health" ] && [ "$(labels_of 'Site - health windows')" = "portal|portal-area:Health" ] \
    && [ "$(labels_of 'Site - cleanup flow')" = "portal" ] && [ "$(labels_of 'Other tool')" = "" ] && [ "$(execs | tr -d '[] ' | tr ',' '\n' | wc -l)" = 3 ]; then
    pass "publish: the label (and the area label) and Execute on the three matches; the one that does not match has nothing"
else fail "publish" "$(labels_of 'Site - health linux') / $(execs) / $(grep -E "label|grant|fatal" "$work/out.txt" | head -8)"; fi
n="$(writes)"
setup -e "$pubvars"
if [ "$(writes)" = "$n" ] && grep -q "label 'Site - health linux: portal': already done" "$work/out.txt" && grep -q "publish grant 'Site - health linux: Execute': already done" "$work/out.txt"; then
    pass "publish again: already done, nothing written"
else fail "publish again" "$(($(writes) - n)) writes; $(grep -E "label|grant" "$work/out.txt" | head -6)"; fi
n="$(writes)"
setup -e '{"portal_publish_templates": ["site - *"], "portal_publish_max": 2}'
if [ $? != 0 ] && [ "$(writes)" = "$n" ] && grep -q "more than portal_publish_max (2)" "$work/out.txt" && grep -q "Site - health linux" "$work/out.txt"; then
    pass "a pattern that matches more than portal_publish_max: stops with the list, nothing changed"
else fail "publish max" "$(grep -E "portal_publish|fatal" "$work/out.txt" | head -5)"; fi
setup -e '{"portal_publish_templates": ["Site - health *"], "portal_unpublish_templates": ["site - health windows"]}'
if [ $? != 0 ] && grep -q "both match" "$work/out.txt"; then pass "a template in both lists: an error, nothing changed"; else fail "both lists" "$(grep -E "both|fatal" "$work/out.txt" | head -3)"; fi
n="$(writes)"; before="$(execs)"
setup --check -e '{"portal_unpublish_templates": ["site - health windows", "Nothing like this"]}'
if [ "$(writes)" = "$n" ] && grep -q "label 'Site - health windows: portal': would remove the label" "$work/out.txt" && grep -q "unpublish 'Nothing like this': skipped - matches no" "$work/out.txt"; then
    pass "unpublish, Check first: says it would remove the label, writes nothing"
else fail "unpublish check" "$(grep -E "label|unpublish" "$work/out.txt" | head -5)"; fi
setup -e '{"portal_unpublish_templates": ["site - health windows"]}'
if [ "$(labels_of 'Site - health windows')" = "portal-area:Health" ] && [ "$(labels_of 'Site - health linux')" = "portal|portal-area:Health" ] && [ "$(execs)" = "$before" ]; then
    pass "unpublish: the label is gone from that one only; its area label and the roles stay"
else fail "unpublish" "$(labels_of 'Site - health windows') / $(execs) vs $before"; fi
setup -e '{"portal_unpublish_templates": ["site - health windows"]}'
if grep -q "label 'Site - health windows: portal': already done" "$work/out.txt"; then pass "unpublish again: already done"; else fail "unpublish again" "$(grep -E "label" "$work/out.txt" | head -3)"; fi
# ---- 1.78.1c: a whole organization on the portal with one setting
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
s["organizations"].append({"id": 7, "name": "platforms-org"})
for kind, names in (("job_templates", ["Platform - patch", "Platform - audit"]), ("workflow_job_templates", ["Platform - rollout"])):
    for nm in names:
        s["next"] += 1
        s[kind].append({"id": s["next"], "name": nm, "organization": 7, "credentials": [], "label_ids": []})
json.dump(s, open(sys.argv[1], "w"))
EOPY
orgvars='{"portal_publish_organization": "platforms-org", "portal_publish_templates": ["*"]}'
n="$(writes)"
setup --check -e "$orgvars"
if [ "$(writes)" = "$n" ] && grep -q "publish 'organization platforms-org': applies - only the job templates and workflows of this organization are matched (3 of them)" "$work/out.txt" \
    && grep -q "label 'Platform - patch: portal': would label" "$work/out.txt" && grep -q "label 'Platform - rollout: portal': would label" "$work/out.txt" \
    && grep -q "publish grant 'Platform - audit: Execute': would grant" "$work/out.txt" && ! grep -q "Other tool" "$work/out.txt"; then
    pass "a whole organization, Check first: the list says it is limited to the organization, only its three templates, nothing written"
else fail "organization check" "$(writes) writes; $(grep -E "label|publish|fatal" "$work/out.txt" | head -12)"; fi
setup -e "$orgvars"
if [ "$(labels_of 'Platform - patch')" = "portal" ] && [ "$(labels_of 'Platform - rollout')" = "portal" ] && [ "$(labels_of 'Other tool')" = "" ]; then
    pass "a whole organization: the label on every template and workflow of it, none elsewhere"
else fail "organization" "$(labels_of 'Platform - patch') / $(labels_of 'Other tool') / $(grep -E "label|fatal" "$work/out.txt" | head -6)"; fi
n="$(writes)"
setup -e '{"portal_publish_organization": "platforms-org", "portal_publish_templates": ["*"], "portal_publish_max": 2}'
if [ $? != 0 ] && [ "$(writes)" = "$n" ] && grep -q "more than portal_publish_max (2)" "$work/out.txt"; then pass "the organization's list over portal_publish_max: stops, nothing changed"
else fail "organization max" "$(grep -E "portal_publish|fatal" "$work/out.txt" | head -4)"; fi
setup -e '{"portal_publish_organization": "no-such-org", "portal_publish_templates": ["*"]}'
if [ $? != 0 ] && [ "$(writes)" = "$n" ] && grep -q "no-such-org" "$work/out.txt"; then pass "an organization that is not there: an error naming it, nothing changed"
else fail "organization missing" "$(grep -E "no-such|fatal" "$work/out.txt" | head -4)"; fi
if ! grep -q '"method": "DELETE", "path": "\(job_templates\|workflow_job_templates\)' "$work/writes.jsonl"; then
    pass "never a template or a workflow deleted"
else fail "a delete" "$(grep DELETE "$work/writes.jsonl" | head -3)"; fi

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
[ "$fails" = 0 ]
