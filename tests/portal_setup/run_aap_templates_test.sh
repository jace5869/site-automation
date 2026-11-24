#!/usr/bin/env bash
# AAP - set up job templates (playbooks/aap_templates_setup.yml) against a stand-in AAP that keeps what it is given
# (tests/portal_setup/fake_aap_crud.py, its state seeded here). Checks: Check writes nothing and says what it would make,
# fix and label; a run makes the missing templates (credentials by kind, prompts, the EE, the Variables all switched
# off); one that differs is left (said) unless aap_templates_overwrite, then brought right - a credential of another type
# kept; the same playbook under another name is not made twice; a kind not mapped, a name of another organization: skipped;
# one the portal setup makes: left to it; aap_templates_only; the portal labels (portal-actions, all); a second run
# already right; never a DELETE; never a secret in the output.
#   bash tests/portal_setup/run_aap_templates_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${AAP_TEMPLATES_TEST_PORT:-18699}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

"$py" - "$work/state.json" <<'PYEND'
import json, sys
def cred(i, name, typ, tname):
    return {"id": i, "name": name, "organization": 1, "credential_type": typ, "inputs": {"password": "SECRET-PW-%d" % i},
            "summary_fields": {"credential_type": {"id": typ, "name": tname}}}
def jt(i, name, org, project, playbook, creds, ee=5):
    return {"id": i, "name": name, "organization": org, "project": project, "playbook": playbook, "inventory": 3, "ask_limit_on_launch": True,
            "ask_variables_on_launch": False, "ask_inventory_on_launch": False, "ask_job_type_on_launch": False, "execution_environment": ee, "credentials": creds, "labels": []}
s = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}, {"id": 9, "name": "Lab"}],
     "projects": [{"id": 2, "name": "site-automation", "organization": 1}, {"id": 6, "name": "Old project", "organization": 1}],
     "inventories": [{"id": 3, "name": "Site hosts", "organization": 1}],
     "execution_environments": [{"id": 5, "name": "All-in-one EE"}, {"id": 7, "name": "Windows EE"}],
     "credentials": [cred(10, "Linux machine", 1, "Machine"), cred(11, "Windows machine", 1, "Machine"),
                     cred(12, "AAP - jobs", 17, "Red Hat Ansible Automation Platform"), cred(13, "STIG Manager API", 30, "STIG Manager API"),
                     cred(14, "ACT model key", 31, "ACT model key"), cred(15, "Old machine", 1, "Machine"), cred(16, "Teams hook", 40, "Teams webhook"),
                     cred(18, "ServiceNow API", 32, "ServiceNow API")],
     "job_templates": [jt(100, "Health check", 1, 6, "playbooks/health_check.yml", [15, 16]),
                       jt(101, "STIG - drift report", 1, 2, "playbooks/stig_drift_report.yml", [10, 13]),
                       jt(102, "Troubleshoot", 1, 2, "playbooks/troubleshoot.yml", [10]),
                       jt(103, "Windows health check", 9, 2, "playbooks/win_health_check.yml", []),
                       jt(104, "Restart a service", 1, 2, "playbooks/service_restart.yml", [10, 12]),
                       jt(105, "Ask for disk space", 1, 2, "playbooks/disk_space_request.yml", [10, 12])],
     "workflow_job_templates": [], "nodes": [], "approvals": [], "schedules": [], "roles": [], "users": [], "labels": []}
json.dump(s, open(sys.argv[1], "w"), indent=1)
PYEND
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
: > "$work/all-out.txt"
cat > "$work/vars.json" <<'EOF'
{"aap_templates_inventory": "Site hosts", "aap_templates_ee": {"all": "All-in-one EE", "windows": "Windows EE"},
 "aap_templates_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "aap": "AAP - jobs",
                               "stigman_api": "STIG Manager API", "act_model_key": "ACT model key", "servicenow": "ServiceNow API"},
 "aap_templates_only": ["Health check", "Troubleshoot", "STIG drift report", "Windows health check", "VM - restart", "ACT - triage finding",
                        "Database health*", "poa&m STATUS", "ServiceNow health - nodes", "Windows troubleshoot", "Service - restart",
                        "Disk - space request"]}
EOF
run() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token ANSIBLE_NOCOLOR=1 \
        "$ap" -i localhost, playbooks/aap_templates_setup.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
    local rc=$?
    cat "$work/out.txt" >> "$work/all-out.txt"
    return $rc
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
tpl() { st "[j for j in s['job_templates'] if j['name'] == '$1' and j['organization'] == 1][0]$2"; }
has() { grep -qF -- "$1" "$work/out.txt"; }

# ---- Check: nothing written; what it would do
run --check -e '{"aap_templates_label_portal": "all"}'
if [ "$(writes)" = 0 ] && has "DIFFERS - 'Health check' - aap_templates_overwrite: true brings it right" \
    && has "project: has 'Old project', should be site-automation" && has "credential machine: has Old machine, should be Linux machine" \
    && has "already right - 'Troubleshoot'" && has "WOULD CREATE - 'POA&M status'" && has "WOULD CREATE - 'Database health - MariaDB / MySQL'" \
    && has "same playbook, other name - 'STIG drift report' - 'STIG - drift report' runs its playbook - not made twice" \
    && has "left to another job - 'ACT - triage finding' - NOT IN AAP - AAP - set up portal actions makes it, with the workflow that runs it: run that job" \
    && has "left to another job - 'Disk - space request' - NOT IN AAP - AAP - set up portal actions makes it, with the workflow that runs it: run that job; 'Ask for disk space' runs its playbook" \
    && has "# or: portal_aap_names: {disk_space_request: 'Ask for disk space'} (1.78.1+)" \
    && has "same playbook, other name - 'Service - restart' - 'Restart a service' runs its playbook - not made twice; aap_templates_create_alias: true makes it under this name too" \
    && has "SKIPPED - 'Windows health check' - the name is used by a template of another organization" \
    && has "SKIPPED - 'VM - restart' - credential kind vmware not mapped (aap_templates_credentials)" \
    && has "would label portal: portal, portal-area:STIG" && grep -qE "would label portal: [0-9]+" "$work/out.txt"; then
    pass "Check: would create, differs (field by field), right, other name, left, skipped, would label - nothing written"
else fail "Check" "$(writes) writes; $(grep -E "WOULD|DIFFERS|already|SKIPPED|other name|left|label|fatal|msg" "$work/out.txt" | head -20)"; fi

# ---- the run: made; the one that differs left as it is
run; rc=$?
nt="$(st "len([j for j in s['job_templates'] if j['organization'] == 1])")"
if [ $rc = 0 ] && [ "$nt" = 9 ] && [ "$(tpl 'POA&M status' "['credentials']")" = "[13]" ] && [ "$(tpl 'Health check' "['project']")" = 6 ] \
    && [ "$(tpl 'Windows troubleshoot' "['execution_environment']")" = 7 ] && [ "$(tpl 'Windows troubleshoot' "['credentials']")" = "[11]" ]; then
    pass "a run: the missing ones made (credentials by kind, the Windows EE), the one that differs left as it is"
else fail "the run" "rc $rc; $nt templates; $(grep -E "created|DIFFERS|fatal|msg" "$work/out.txt" | head -10)"; fi
got="$(tpl 'Database health - MariaDB / MySQL' "['ask_limit_on_launch'], s['job_templates'][-1]['inventory']")"
ev="$(tpl 'Database health - MariaDB / MySQL' "['extra_vars']")"
if printf '%s' "$ev" | "$py" -c "
import sys
t = sys.stdin.read()
live = [l for l in t.splitlines() if l.strip() and not l.lstrip().startswith('#') and l.strip() != '---']
sys.exit(1 if live or 'database_health' not in t else 0)"; then pass "... its Variables: the file, every setting switched off"
else fail "the Variables" "$(printf '%s' "$ev" | head -8)"; fi
ev="$(tpl 'ServiceNow health - nodes' "['extra_vars']")"
printf '%s' "$ev" | grep -q '^servicenow_health_part: "nodes"' && pass "... the second template of a playbook: its one fixed setting on" \
    || fail "the fixed setting" "$(printf '%s' "$ev" | head -4)"

# ---- overwrite: brought right; the credential of another type kept
run -e '{"aap_templates_overwrite": true}'; rc=$?
if [ $rc = 0 ] && [ "$(tpl 'Health check' "['project']")" = 2 ] \
    && [ "$(st "sorted([j for j in s['job_templates'] if j['name'] == 'Health check'][0]['credentials'])")" = "[10, 14, 16]" ] \
    && has "fixed - 'Health check'"; then
    pass "aap_templates_overwrite: brought right - the project, the machine credential replaced, the ACT key added, the Teams one kept"
else fail "overwrite" "rc $rc; $(st "[j for j in s['job_templates'] if j['name'] == 'Health check'][0]")"; fi

# ---- again: right, nothing written
before="$(writes)"
run -e '{"aap_templates_overwrite": true}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "already right - 'Health check'" && ! has "created" && pass "a second run: already right, nothing written" \
    || fail "a second run" "rc $rc; $(writes) vs $before; $(grep -E "created|fixed|fatal" "$work/out.txt" | head -5)"

# ---- the portal labels: portal-actions labels only what the portal launches; all labels the rest; never twice
run -e '{"aap_templates_label_portal": "portal-actions", "aap_templates_only": ["Health check", "ACT - triage finding"]}'; rc=$?
labels_of() { st "sorted(l['name'] for l in s['labels'] if l['id'] in [j for j in s['job_templates'] if j['name'] == '$1'][0].get('labels', []))"; }
[ $rc = 0 ] && [ "$(labels_of 'Health check')" = "[]" ] && pass "portal-actions: a template the portal does not launch is not labelled" \
    || fail "portal-actions" "rc $rc; $(labels_of 'Health check')"
run -e '{"aap_templates_label_portal": "all", "aap_templates_only": ["Health check", "POA&M status"]}'; rc=$?
if [ $rc = 0 ] && [ "$(labels_of 'Health check')" = "['portal', 'portal-area:Linux']" ] && [ "$(labels_of 'POA&M status')" = "['portal', 'portal-area:STIG']" ] \
    && [ "$(st "len([l for l in s['labels'] if l['name'] == 'portal'])")" = 1 ]; then
    pass "all: the portal's label and its area on each, one label made once"
else fail "all" "rc $rc; $(labels_of 'Health check') $(labels_of 'POA&M status')"; fi
before="$(writes)"
run -e '{"aap_templates_label_portal": "all", "aap_templates_only": ["Health check", "POA&M status"]}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && ! has "labelled portal" && pass "... again: nothing written" || fail "labels again" "rc $rc"
# an old catalog area's label (portal-area:Reports, before one section per platform) is named, never taken off; a label of the
# site's own area (portal-area:Custom) is not named
"$py" -c "import json; s=json.load(open('$work/state.json')); s['labels'] += [{'id': 900, 'name': 'portal-area:Reports', 'organization': 1}, {'id': 901, 'name': 'portal-area:Custom', 'organization': 1}]; [j for j in s['job_templates'] if j['name'] == 'Health check'][0]['labels'] += [900, 901]; json.dump(s, open('$work/state.json','w'))"
before="$(writes)"
run -e '{"aap_templates_label_portal": "all", "aap_templates_only": ["Health check"]}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "stale portal-area label portal-area:Reports on Health check - remove it by hand" \
    && ! has "portal-area:Custom on" && [ "$(labels_of 'Health check')" = "['portal', 'portal-area:Custom', 'portal-area:Linux', 'portal-area:Reports']" ] \
    && pass "an old catalog area label: named to remove by hand, never taken off" \
    || fail "stale area label" "rc $rc; $(labels_of 'Health check'); $(grep -F "stale" "$work/out.txt" | head -3)"
"$py" -c "import json; s=json.load(open('$work/state.json')); j=[j for j in s['job_templates'] if j['name'] == 'Health check'][0]; j['labels'] = [x for x in j['labels'] if x not in (900, 901)]; json.dump(s, open('$work/state.json','w'))"

# ---- aap_templates_create_alias: the catalog's name made too (a workflow may name it), the site's own kept
run -e '{"aap_templates_create_alias": true, "aap_templates_only": ["Service - restart"]}'; rc=$?
[ $rc = 0 ] && [ "$(tpl 'Service - restart' "['playbook']")" = "playbooks/service_restart.yml" ] && [ "$(tpl 'Restart a service' "['id']")" = 104 ] \
    && has "created - 'Service - restart'" && pass "aap_templates_create_alias: the portal's name made beside the site's own" \
    || fail "create_alias" "rc $rc; $(grep -E "created|other name|fatal" "$work/out.txt" | head -4)"

# ---- a failure part way: what was done before it is said; the run after brings it right
setst() { "$py" -c "import json; s=json.load(open('$work/state.json')); $1; json.dump(s, open('$work/state.json','w'))"; }
nx="$(st "s['next'] + 1")"
setst "s['fail_attach_paths']=['job_templates/$nx/credentials/']"
run -e '{"aap_templates_only": ["Log analysis"]}'; rc=$?
setst "s.pop('fail_attach_paths')"
[ $rc != 0 ] && has "FAILED - 'Log analysis'" && has "done before it: made it" && [ "$(tpl 'Log analysis' "['id']")" = "$nx" ] \
    && pass "a failure part way: the template made before it is said" \
    || fail "failure part way" "rc $rc; $(grep -E "FAILED|done before|fatal" "$work/out.txt" | head -4)"
# ---- an optional credential of a type the template has (the Windows Machine beside the Linux one): left off, said
run -e '{"aap_templates_overwrite": true, "aap_templates_only": ["Log analysis"]}'; rc=$?
[ $rc = 0 ] && [ "$(tpl 'Log analysis' "['credentials']")" = "[10, 14]" ] && has "fixed - 'Log analysis'" \
    && has "not attached: Windows machine (machine_windows) - the template has a Machine credential already, and takes one" \
    && pass "an optional Windows Machine credential beside the Linux one: left off, said" \
    || fail "optional left off" "rc $rc; $(tpl 'Log analysis' "['credentials']"); $(grep -E "fixed|attached|fatal" "$work/out.txt" | head -4)"

# ---- two templates of one name in the organization: a problem before any write (never one of them picked)
setst "s['job_templates'].append(dict([j for j in s['job_templates'] if j['name'] == 'Troubleshoot'][0], id=190))"
before="$(writes)"
run -e '{"aap_templates_label_portal": "all", "aap_templates_only": ["Troubleshoot", "Windows*"]}'; rc=$?
setst "s['job_templates']=[j for j in s['job_templates'] if j['id'] != 190]"
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && has "AAP has 2 job templates named 'Troubleshoot' in the organization: rename one" \
    && pass "two templates of one name: stopped before any write" || fail "two of a name" "rc $rc; $(writes) vs $before"

# ---- no inventory: a run that makes nothing still checks; a project of another organization is never used
run -e '{"aap_templates_inventory": "", "aap_templates_only": ["Troubleshoot"]}'; rc=$?
[ $rc = 0 ] && has "already right - 'Troubleshoot'" && pass "no inventory set: the templates there still checked" \
    || fail "no inventory" "rc $rc; $(grep -E "fatal|msg" "$work/out.txt" | head -3)"
# ---- 1.79.11c: the inventory of another organization (aap_templates_inventory_organization)
setst "s['inventories'].append({'id': 33, 'name': 'Lab hosts', 'organization': 9})"
run --check -e '{"aap_templates_inventory": "Lab hosts", "aap_templates_only": ["Troubleshoot"]}'; rc=$?
[ $rc != 0 ] && has "inventory (aap_templates_inventory) 'Lab hosts': not found in organization 'Default'" \
    && pass "an inventory of another organization, its organization not named: not found, stopped" || fail "other org inv, unnamed" "rc $rc"
run --check -e '{"aap_templates_inventory": "Lab hosts", "aap_templates_inventory_organization": "Lab", "aap_templates_only": ["Troubleshoot"]}'; rc=$?
[ $rc = 0 ] && ! has "inventory (aap_templates_inventory)" && pass "aap_templates_inventory_organization: the other organization's inventory found (Check)" \
    || fail "other org inv" "rc $rc; $(grep -E "fatal|msg" "$work/out.txt" | head -3)"
run --check -e '{"aap_templates_inventory": "Lab hosts", "aap_templates_inventory_organization": "Nope", "aap_templates_only": ["Troubleshoot"]}'; rc=$?
[ $rc != 0 ] && has "organization 'Nope' (aap_templates_inventory_organization) not found" && pass "  ... an organization that is not there: said, stopped" \
    || fail "other org missing" "rc $rc"
setst "s['projects'].append({'id': 20, 'name': 'Lab project', 'organization': 9})"
before="$(writes)"
run -e '{"aap_templates_project": "Lab project"}'; rc=$?
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && has "project (aap_templates_project) 'Lab project': not found in organization 'Default'" \
    && pass "a project of another organization: not found, nothing written" || fail "other organization" "rc $rc; $(grep -E "fatal|not found" "$work/out.txt" | head -3)"

# ---- Codex final round: a personal credential (no organization) is never used; a Variables file that cannot be read and
# a playbook the project does not have (as AAP has it synced) stop the job before any write
setst "s['credentials'].append({'id': 70, 'name': 'Personal key', 'organization': None, 'credential_type': 1, 'summary_fields': {'credential_type': {'id': 1, 'name': 'Machine'}}})"
before="$(writes)"
run -e '{"aap_templates_credentials": {"machine": "Personal key"}, "aap_templates_only": ["Troubleshoot"]}'; rc=$?
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && has "credential machine (aap_templates_credentials) 'Personal key': not found in organization 'Default'" \
    && pass "a personal credential (no organization): not used, nothing written" || fail "personal credential" "rc $rc; $(grep -E "fatal|not found" "$work/out.txt" | head -3)"
"$py" - "$repo/aap/job_templates.yml" "$work/catalog.yml" <<'PYEND2'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1]))["job_templates"]
one = [dict(e, name="Made first") for e in c if e["name"] == "Troubleshoot"][0]
two = dict(one, name="Bad Variables", variables_file="aap/extra_vars/not-there.yml")
yaml.safe_dump({"job_templates": [one, two]}, open(sys.argv[2], "w"))
PYEND2
run -e "{\"aap_templates_catalog\": \"$work/catalog.yml\", \"aap_templates_only\": [], \"aap_templates_create_alias\": true}"; rc=$?
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && has "'Bad Variables': its Variables file aap/extra_vars/not-there.yml could not be read" \
    && ! has "created - 'Made first'" && pass "a Variables file that cannot be read: stopped before the first write" \
    || fail "Variables file" "rc $rc; $(writes) vs $before; $(grep -E "fatal|Variables|created" "$work/out.txt" | head -3)"
setst "s['project_playbooks']={'2': ['playbooks/troubleshoot.yml']}"
run --check -e '{"aap_templates_only": ["Patch hosts"]}'; rc=$?
setst "s.pop('project_playbooks')"
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && has "not in project 'site-automation' as AAP has it: playbooks/patch_hosts.yml - sync the project" \
    && pass "a playbook the project lacks: said in Check, before any write" || fail "playbook" "rc $rc; $(grep -E "fatal|not in project" "$work/out.txt" | head -3)"

# ---- never a DELETE, never a secret
# ---- 1.83.0: the STIG jobs - the two workflows with every node and link, their steps (the gate and STIG Manager - update
# too, when AAP has none), the portal's label on the workflows only, the old names said - never deleted
setst "s['job_templates'].append(dict(s['job_templates'][1], id=120, name='STIG answer keys', playbook='playbooks/stig_answer_keys.yml'));
s['workflow_job_templates'].append({'id': 121, 'name': 'STIG Manager updated, with an approval', 'organization': 1, 'description': '', 'labels': []})"
cat > "$work/stig.json" <<'EOF2'
{"aap_templates_description_owner": "Example setup", "aap_templates_only": ["STIG - *", "Workflow gate - has a plan", "STIG Manager - update"],
 "aap_templates_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "stigman_api": "STIG Manager API",
                               "stigman_api_writer": "STIG Manager API", "act_model_key": "ACT model key"}}
EOF2
before="$(writes)"
export ANSIBLE_SHOW_CUSTOM_STATS=1
run --check -e "@$work/stig.json"; rc=$?
if [ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "WOULD CREATE - 'STIG - full scan - Linux check'" && has "WOULD CREATE - 'STIG Manager - update'" \
    && has "WOULD CREATE - 'Workflow gate - has a plan'" && has "WOULD CREATE - workflow 'STIG - full scan and upload'" \
    && ! has '"aap_setup_fingerprints":' && has "WOULD CREATE - workflow 'STIG - drift scan and upload'" \
    && has "node linux: 'STIG - full scan - Linux check'; on failure -> gate_linux" && has "node windows: 'STIG - full scan - Windows check'; on failure -> gate_windows" \
    && has "node gate_linux: 'Workflow gate - has a plan'; on success -> approve; its Variables: workflow_gate_var: stigman_update_plan" \
    && has "node gate_windows: 'Workflow gate - has a plan'; on success -> approve; its Variables: workflow_gate_var: stigman_update_plan_windows" \
    && has "node approve: approval 'Write the full scan's reviews (and the new assets) to STIG Manager?'; on success -> update" \
    && has "node update: 'STIG Manager - update'" && has "label - workflow 'STIG - full scan and upload' - would label it: portal, portal-area:STIG" \
    && has "RENAME - 'STIG - answer file generator' - rename 'STIG answer keys' to 'STIG - answer file generator' when ready" \
    && ! has "WOULD CREATE - 'STIG - answer file generator'" && ! has "OLD - old 'STIG answer keys'" \
    && has "OLD - old 'STIG Manager updated, with an approval' - workflow replaced by 'STIG - drift scan and upload' - delete it when ready"; then
    pass "STIG jobs, Check: the templates, both workflows node by node and link by link, the old names - nothing written"
else echo "      STIG jobs, Check: rc $rc, $(writes) writes vs $before"; fail "STIG jobs, Check" "$(grep -E "WOULD|node|OLD|label|SKIPPED|other name|fatal|msg" "$work/out.txt" | head -40)"; fi
cp "$work/out.txt" "$work/stig-check.txt"
# 1.83.1 (P2-5): what replaces an old name is skipped (no Windows credential mapped) - the old one is kept, never
# "replaced ... delete it when ready"
before="$(writes)"
run --check -e "@$work/stig.json" -e '{"aap_templates_credentials": {"machine": "Linux machine", "stigman_api": "STIG Manager API", "stigman_api_writer": "STIG Manager API"}}'; rc=$?
if [ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "SKIPPED - 'STIG - drift scan - Windows check' - credential kind machine_windows not mapped" \
    && has "SKIPPED - workflow 'STIG - drift scan and upload'" \
    && has "KEEP - old 'STIG Manager updated, with an approval' - workflow - keep it: 'STIG - drift scan and upload', which replaces it, is not in AAP and would not be made (see above)" \
    && ! has "OLD - old 'STIG Manager updated, with an approval'"; then
    pass "STIG jobs, Check: the new workflow skipped - the old one it replaces is kept (KEEP), never 'delete it when ready'"
else fail "old name kept when its replacement is skipped" "rc $rc; $(grep -E "SKIPPED|OLD|KEEP|fatal|msg" "$work/out.txt" | head -12)"; fi
# the site's own update and gate, under other names (the director: never a second one) - used for the steps
setst "s['job_templates'].append(dict(s['job_templates'][1], id=122, name='STIG update', playbook='playbooks/stigman_update.yml', credentials=[13], ask_limit_on_launch=False));
s['job_templates'].append(dict(s['job_templates'][1], id=123, name='Has a plan', playbook='playbooks/workflow_gate.yml', credentials=[], ask_variables_on_launch=True, ask_limit_on_launch=False))"
before="$(writes)"
run --check -e "@$work/stig.json"; rc=$?
if [ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "USES - 'STIG Manager - update' - uses 'STIG update' (playbooks/stigman_update.yml) for the update step" \
    && has "USES - 'Workflow gate - has a plan' - uses 'Has a plan' (playbooks/workflow_gate.yml) for the gate step" \
    && has "node update: 'STIG update'" && has "node gate_linux: 'Has a plan'; on success -> approve" && ! has "WOULD CREATE - 'STIG Manager - update'" \
    && ! has "WOULD CREATE - 'Workflow gate - has a plan'"; then
    pass "STIG jobs, Check: the site's own update and gate (other names) used for the steps, never made again"
else fail "STIG jobs, Check with the site's own update" "$(grep -E "USES|WOULD|node|SKIPPED|fatal|msg" "$work/out.txt" | head -30)"; fi
[ -n "${AAP_TEMPLATES_KEEP:-}" ] && cp "$work/out.txt" "$AAP_TEMPLATES_KEEP"        # the STIG jobs' Check output, to read
run -e "@$work/stig.json"; rc=$?
got="$("$py" - "$work/state.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
out = []
for wname in ("STIG - full scan and upload", "STIG - drift scan and upload"):
    w = [x for x in s["workflow_job_templates"] if x["name"] == wname]
    if len(w) != 1:
        print("no workflow %s" % wname); sys.exit(0)
    w = w[0]
    nodes = [n for n in s["nodes"] if n["wf"] == w["id"]]
    by = dict((n["id"], n["identifier"]) for n in nodes)
    tname = dict((j["id"], j["name"]) for j in s["job_templates"])
    aname = dict((a["id"], a["name"]) for a in s["approvals"])
    for n in sorted(nodes, key=lambda n: n["identifier"]):
        what = tname.get(n["unified_job_template"]) if n.get("unified_job_template") else "approval:" + aname.get(n["approval"], "?")
        links = ";".join("%s>%s" % (k, ",".join(sorted(by[x] for x in n[k]))) for k in ("success", "failure", "always") if n[k])
        out.append("%s|%s|%s|%s|%s" % (wname.split(" - ")[1].split()[0], n["identifier"], what, links, json.dumps(n.get("extra_data") or {}, sort_keys=True)))
    labels = sorted(l["name"] for l in s.get("labels", []) if l["id"] in w.get("labels", []))
    out.append("%s labels %s limit %s" % (wname.split(" - ")[1].split()[0], ",".join(labels), w.get("ask_limit_on_launch")))
upd = [j for j in s["job_templates"] if j["name"] == "STIG Manager - update"]
out.append("update labels %s" % ((upd[0].get("labels") or []) if upd else "none"))
gate = [j for j in s["job_templates"] if j["name"] == "Workflow gate - has a plan"]
out.append("gate asks variables %s" % (gate[0].get("ask_variables_on_launch") if gate else "none"))
mine = [j for j in s["job_templates"] if j["name"] == "STIG update"]
out.append("STIG update labels %s" % (mine[0].get("labels") or []))
print("\n".join(out))
PY
)"
want="full|approve|approval:Write the full scan's reviews (and the new assets) to STIG Manager?|success>update|{}
full|gate_linux|Has a plan|success>approve|{\"workflow_gate_var\": \"stigman_update_plan\"}
full|gate_windows|Has a plan|success>approve|{\"workflow_gate_var\": \"stigman_update_plan_windows\"}
full|linux|STIG - full scan - Linux check|failure>gate_linux|{}
full|update|STIG update||{}
full|windows|STIG - full scan - Windows check|failure>gate_windows|{}
full labels portal,portal-area:STIG limit True
drift|approve|approval:Write the drift scan's reviews (and the new assets) to STIG Manager?|success>update|{}
drift|gate_linux|Has a plan|success>approve|{\"workflow_gate_var\": \"stigman_update_plan\"}
drift|gate_windows|Has a plan|success>approve|{\"workflow_gate_var\": \"stigman_update_plan_windows\"}
drift|linux|STIG - drift scan - Linux check|failure>gate_linux|{}
drift|update|STIG update||{}
drift|windows|STIG - drift scan - Windows check|failure>gate_windows|{}
drift labels portal,portal-area:STIG limit True
update labels none
gate asks variables none
STIG update labels []"
[ $rc = 0 ] && [ "$got" = "$want" ] && pass "STIG jobs, a run: both workflows made with the site's own update and gate - each node, link, approval, the gate's Variables; labelled; not the update" \
    || fail "STIG jobs, a run" "rc $rc; got:
$got
$(grep -E "created|SKIPPED|FAILED|fatal|msg" "$work/out.txt" | head -10)"
# The workflow map is a Run artifact. Seed it as AAP history for the next launch.
if "$py" - "$work/out.txt" "$work/state.json" <<'PYEND'
import json, re, sys
from pathlib import Path
output, state = map(Path, sys.argv[1:])
stats, _ = json.JSONDecoder().raw_decode(output.read_text().rsplit("RUN: ", 1)[1].lstrip())
fps = stats["aap_setup_fingerprints"]
s = json.loads(state.read_text())
for scan in ("full", "drift"):
    name = "STIG - %s scan and upload" % scan
    desc = next(w["description"] for w in s["workflow_job_templates"] if w["name"] == name)
    assert desc.startswith("** Does: ") and "[set up by" not in desc, name
    assert re.fullmatch("[0-9a-f]{12}", fps["1/workflow/" + name]), name
# Older success, baseline, failed Run, successful Check. Only the baseline should be used.
s["jobs"] = [{"id": i, "job_template": 7, "status": status, "job_type": "check" if i == 92 else "run", "artifacts": artifacts}
             for i, status, artifacts in [(89, "successful", {"aap_setup_fingerprints": {}}),
                                          (90, "successful", {"aap_setup_fingerprints": fps}),
                                          (91, "failed", {"aap_setup_fingerprints": {}}),
                                          (92, "successful", {})]]
state.write_text(json.dumps(s))
PYEND
then pass "both workflows have clean descriptions and a fingerprint in the Run artifact"
else fail "workflow descriptions and artifact"; fi
unset ANSIBLE_SHOW_CUSTOM_STATS
ev="$(tpl 'STIG - drift scan - Windows check' "['extra_vars']")"
case "$ev" in *"check_stig_update_new_by_import: \"true\""*"check_stig_update_plan_var: \"stigman_update_plan_windows\""*) pass "... the Windows drift check hands its plan on as stigman_update_plan_windows and makes new assets by the import's planner" ;;
    *) fail "the drift check's fixed Variables" "$ev" ;; esac
before="$(writes)"
run -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "already right - workflow 'STIG - full scan and upload'" && has "already right - workflow 'STIG - drift scan and upload'" \
    && pass "STIG jobs, a second run: already right, nothing written" || fail "STIG jobs, second run" "rc $rc; $(writes) vs $before; $(grep -E "workflow|fatal" "$work/out.txt" | head -6)"
[ "$(st "len([w for w in s['workflow_job_templates'] if w['name'] == 'STIG Manager updated, with an approval'])")" = 1 ] \
    && pass "... the old workflow is still there (named, never deleted)" || fail "the old workflow was deleted"
# A changed catalog description is safe to apply when AAP still matches the previous Run.
before="$(writes)"
run --check -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7, "aap_templates_description_owner": "Example team", "report_history": false}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "WOULD UPDATE - workflow 'STIG - full scan and upload'" \
    && has "WOULD UPDATE - workflow 'STIG - drift scan and upload'" && pass "Check reads the previous Run's workflow fingerprints" \
    || fail "workflow history lookup" "$(grep -E 'workflow|fatal' "$work/out.txt" | tail -12)"
# Edit just the Contact line after migration. Neither a full setup nor describe-only may undo it.
setst "w = next(w for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload'); w['description'] = w['description'].replace('** Contact: Owner=Example setup', '** Contact: Owner=Example editor')"
for only in false true; do
    run -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7, "aap_templates_describe": true, "aap_templates_only": ["STIG - full scan and upload"]}' -e "aap_templates_describe_only=$only"
    if [ "$?" = 0 ] && [ "$(writes)" = "$before" ] && has "left to its own job - workflow 'STIG - full scan and upload'" \
        && st "next(w for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload')['description']" | grep -q 'Owner=Example editor'; then
        pass "Contact edit survives describe (describe-only $only)"
    else fail "Contact edit (describe-only $only)" "$(grep -E 'STIG - full scan and upload|fatal' "$work/out.txt" | tail -8)"; fi
done
setst "w = next(w for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload'); w['description'] = w['description'].replace('** Contact: Owner=Example editor', '** Contact: Owner=Example setup')"
# 1.83.1 (P2-1): a step whose fixed Variables lack the plan var differs - never "already right"; overwrite sets
# only those keys, a person's own settings kept
setst "[j.update(extra_vars=j['extra_vars'].replace('check_stig_update_plan_var: \"stigman_update_plan_windows\"\\n', '') + 'site_note: mine\\n') for j in s['job_templates'] if j['name'] == 'STIG - drift scan - Windows check' and j['organization'] == 1]"
nl=$'\n'
case "$nl$(tpl 'STIG - drift scan - Windows check' "['extra_vars']")" in *"${nl}check_stig_update_plan_var:"*) fail "the test did not take the plan var off" ;; esac
before="$(writes)"
run --check -e "@$work/stig.json"; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "DIFFERS - 'STIG - drift scan - Windows check' - aap_templates_overwrite: true brings it right" \
    && has "variable check_stig_update_plan_var: has none, should be stigman_update_plan_windows" && ! has "already right - 'STIG - drift scan - Windows check'" \
    && pass "STIG jobs: a Windows check without the plan var - DIFFERS, the variable said, never already right" \
    || fail "a step's fixed Variables changed" "rc $rc; $(grep -E "DIFFERS|variable|already right - 'STIG|fatal|msg" "$work/out.txt" | head -10)"
run -e "@$work/stig.json" -e aap_templates_overwrite=true; rc=$?
ev="$(tpl 'STIG - drift scan - Windows check' "['extra_vars']")"
case "$rc|$ev" in 0\|*"check_stig_update_plan_var: \"stigman_update_plan_windows\""*"site_note: mine"*) pass "... aap_templates_overwrite: the plan var set again, the person's own setting kept" ;;
    *) fail "overwrite of the fixed Variables" "rc $rc; $ev; $(grep -E "brought|FAILED|fatal|msg" "$work/out.txt" | head -6)" ;; esac
before="$(writes)"
run --check -e "@$work/stig.json"; rc=$?
[ $rc = 0 ] && has "already right - 'STIG - drift scan - Windows check'" && pass "... then already right" \
    || fail "already right after the overwrite" "rc $rc; $(grep -E "DIFFERS|variable|fatal" "$work/out.txt" | head -6)"
# two of the site's templates run the update's playbook: which one is not guessed - aap_templates_step_names names it
setst "s['job_templates'].append(dict(s['job_templates'][1], id=124, name='STIG update 2', playbook='playbooks/stigman_update.yml', credentials=[13], ask_limit_on_launch=False))"
run --check -e "@$work/stig.json"; rc=$?
[ $rc = 0 ] && has "SKIPPED - 'STIG Manager - update' - several templates run playbooks/stigman_update.yml: 'STIG update', 'STIG update 2' - name the one for the update step in aap_templates_step_names" \
    && pass "STIG jobs: two of the site's update templates - not guessed, said with the setting" || fail "two update templates" "$(grep -E "SKIPPED|USES|workflow|fatal" "$work/out.txt" | head -8)"
run --check -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7, "aap_templates_step_names": {"STIG Manager - update": "STIG update"}}'; rc=$?
[ $rc = 0 ] && has "USES - 'STIG Manager - update' - uses 'STIG update' (playbooks/stigman_update.yml) for the update step" \
    && has "already right - workflow 'STIG - full scan and upload'" && pass "... aap_templates_step_names: that one used, the workflows already right" \
    || fail "step_names" "$(grep -E "SKIPPED|USES|workflow|fatal" "$work/out.txt" | head -8)"
# Codex (1.83.0): a workflow of that name in another organization is never this one's
setst "s['workflow_job_templates'].append(dict([w for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload'][0], id=977, organization=9))"
run --check -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7, "aap_templates_step_names": {"STIG Manager - update": "STIG update"}}'; rc=$?
[ $rc = 0 ] && has "already right - workflow 'STIG - full scan and upload'" && ! has "of that name: rename one" \
    && pass "... a workflow of that name in another organization: never this one's" || fail "another organization's workflow" "$(grep -E "SKIPPED|workflow|fatal" "$work/out.txt" | head -8)"
# Codex (1.83.0): the step named must run its playbook; a reused step that asks for a Limit would do nothing under the workflow's
run --check -e "@$work/stig.json" -e '{"aap_templates_step_names": {"STIG Manager - update": "Health check"}}'; rc=$?
[ $rc = 0 ] && has "SKIPPED - 'STIG Manager - update' - aap_templates_step_names names 'Health check' for it, but it runs playbooks/" \
    && ! has "WOULD CREATE - workflow 'STIG - full scan and upload'" && pass "... a step named that runs another playbook: refused, said" \
    || fail "step_names to another playbook" "$(grep -E "SKIPPED|USES|workflow|fatal" "$work/out.txt" | head -8)"
setst "[j.update(ask_limit_on_launch=True) for j in s['job_templates'] if j['name'] == 'STIG update']"
run --check -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7, "aap_templates_step_names": {"STIG Manager - update": "STIG update"}}'; rc=$?
[ $rc = 0 ] && has "SKIPPED - 'STIG Manager - update' - 'STIG update' runs playbooks/stigman_update.yml but asks for a Limit on launch" \
    && pass "... a reused update that asks for a Limit: refused, said (the workflow's Limit would reach it)" \
    || fail "reused update with a Limit prompt" "$(grep -E "SKIPPED|USES|workflow|fatal" "$work/out.txt" | head -8)"

# Codex P1 (1.83.0, final pass): a template under the gate's own name that runs the update - refused, never wired
setst "[j.update(ask_limit_on_launch=False) for j in s['job_templates'] if j['name'] == 'STIG update'];
s['job_templates'].append(dict(s['job_templates'][1], id=125, name='Workflow gate - has a plan', playbook='playbooks/stigman_update.yml', credentials=[13], ask_variables_on_launch=True, ask_limit_on_launch=False))"
before="$(writes)"
run --check -e "@$work/stig.json" -e '{"awx_job_id": 93, "awx_job_template_id": 7, "aap_templates_step_names": {"STIG Manager - update": "STIG update"}}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] \
    && has "SKIPPED - 'Workflow gate - has a plan' - 'Workflow gate - has a plan' runs playbooks/stigman_update.yml - the step must run playbooks/workflow_gate.yml from this project; the workflows that need it are not made" \
    && has "SKIPPED - workflow 'STIG - full scan and upload' - its step(s) 'Workflow gate - has a plan' refused above - not made or changed" \
    && has "SKIPPED - workflow 'STIG - drift scan and upload' - its step(s) 'Workflow gate - has a plan' refused above" \
    && pass "a template under the gate's name that runs the update: refused, its workflows not wired to it (Check)" \
    || fail "a gate that runs the update" "rc $rc; $(grep -E "SKIPPED|USES|workflow|fatal" "$work/out.txt" | head -10)"
run -e "@$work/stig.json" -e '{"aap_templates_step_names": {"STIG Manager - update": "STIG update"}}'; rc=$?
[ "$(st "len([n for n in s['nodes'] if n.get('unified_job_template') == 125])")" = 0 ] \
    && pass "... a run: no node of any workflow runs it" || fail "the gate that runs the update was wired" "rc $rc"
# the director's check (1.83.0): aap_templates_only naming the workflow alone - the template job plans no step, the workflow
# job checks each step's template itself
cat > "$work/stig-wf.json" <<'EOF2'
{"aap_templates_only": ["STIG - full scan and upload"],
 "aap_templates_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "stigman_api": "STIG Manager API",
                               "stigman_api_writer": "STIG Manager API", "act_model_key": "ACT model key"}}
EOF2
# the update there under its own name, right: the gate is the only step that is wrong
setst "s['job_templates'].append(dict(s['job_templates'][1], id=126, name='STIG Manager - update', playbook='playbooks/stigman_update.yml', credentials=[13], ask_limit_on_launch=False))"
before="$(writes)"
run --check -e "@$work/stig-wf.json"; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && ! has "SKIPPED - 'Workflow gate - has a plan'" \
    && has "SKIPPED - workflow 'STIG - full scan and upload' - 'Workflow gate - has a plan' runs playbooks/stigman_update.yml - the step must run playbooks/workflow_gate.yml from this project - not made or changed" \
    && ! has "workflow 'STIG - drift scan and upload'" \
    && pass "aap_templates_only naming the workflow alone: its gate that runs the update is still refused (Check)" \
    || fail "only the workflow: a gate that runs the update" "rc $rc; $(grep -E "SKIPPED|USES|workflow|fatal|msg" "$work/out.txt" | head -10)"
run -e "@$work/stig-wf.json"; rc=$?
[ "$(st "len([n for n in s['nodes'] if n.get('unified_job_template') in (125, 126)])")" = 0 ] \
    && has "SKIPPED - workflow 'STIG - full scan and upload' - 'Workflow gate - has a plan' runs playbooks/stigman_update.yml" \
    && pass "... a run: never wired" || fail "only the workflow: the gate that runs the update was wired" "rc $rc; $(grep -E "SKIPPED|workflow|fatal" "$work/out.txt" | head -6)"
setst "[j.update(ask_limit_on_launch=True) for j in s['job_templates'] if j['id'] == 125]; [j.update(playbook='playbooks/workflow_gate.yml') for j in s['job_templates'] if j['id'] == 125]"
run --check -e "@$work/stig-wf.json"; rc=$?
[ $rc = 0 ] && has "SKIPPED - workflow 'STIG - full scan and upload' - 'Workflow gate - has a plan' asks for a Limit on launch" \
    && pass "... a gate of the right playbook that asks for a Limit: refused too" \
    || fail "only the workflow: a gate with a Limit prompt" "rc $rc; $(grep -E "SKIPPED|workflow|fatal" "$work/out.txt" | head -6)"
setst "s['job_templates'] = [j for j in s['job_templates'] if j['id'] not in (125, 126)]"

# Codex P1 (1.83.0, final pass): the update is never a root node with its template - a link that fails leaves it running nothing
setst "ws = [x['id'] for x in s['workflow_job_templates'] if x['name'] == 'STIG - full scan and upload'];
s['nodes'] = [n for n in s['nodes'] if n['wf'] not in ws]; s['workflow_job_templates'] = [x for x in s['workflow_job_templates'] if x['id'] not in ws];
s['fail_links_from'] = ['approve']"
run -e "@$work/stig.json" -e '{"aap_templates_step_names": {"STIG Manager - update": "STIG update"}}'; rc=$?
roots="$(st "sorted(n['identifier'] for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload' for n in s['nodes'] if n['wf'] == w['id'] and n.get('unified_job_template') and not any(n['id'] in p[k] for p in s['nodes'] for k in ('success', 'failure', 'always')))")"
upd="$(st "[str(n.get('unified_job_template')) for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload' for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'update']")"
[ $rc != 0 ] && has "FAILED - workflow 'STIG - full scan and upload'" && [ "$roots" = "['linux', 'windows']" ] && [ "$upd" = "['None']" ] \
    && pass "the approval's link fails: the job stops, the update node has no template - never a root node that writes" \
    || fail "a failed link left the update runnable" "rc $rc; roots $roots; update $upd; $(grep -E "FAILED|fatal" "$work/out.txt" | head -4)"
setst "s.pop('fail_links_from')"
run -e "@$work/stig.json" -e '{"aap_templates_step_names": {"STIG Manager - update": "STIG update"}}' -e aap_templates_overwrite=true; rc=$?
upd="$(st "[(n.get('unified_job_template'), [p['identifier'] for p in s['nodes'] if n['id'] in p['success']]) for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload' for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'update']")"
[ $rc = 0 ] && [ "$upd" = "[(122, ['approve'])]" ] \
    && pass "... made again (aap_templates_overwrite): the update attached after its approval link" \
    || fail "the workflow made again" "rc $rc; update $upd; $(grep -E "FAILED|fatal|workflow" "$work/out.txt" | head -6)"
# a workflow changed by hand, made again: its old nodes deleted leaves first - the update before its approval, never a root
setst "w = [x for x in s['workflow_job_templates'] if x['name'] == 'STIG - full scan and upload'][0];
[n.update(extra_data={'workflow_gate_var': 'changed'}) for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'gate_linux']"
set -- $(st "' '.join(str(n['id']) for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload' for i in ('update', 'approve') for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == i)")
n0="$(writes)"
run -e "@$work/stig.json" -e '{"aap_templates_step_names": {"STIG Manager - update": "STIG update"}}' -e aap_templates_overwrite=true; rc=$?
order="$(tail -n +$((n0 + 1)) "$work/writes.jsonl" | "$py" -c "
import json, sys
d = [x['path'] for x in map(json.loads, sys.stdin) if x['method'] == 'DELETE']
print('workflow_job_template_nodes/$1/' in d and 'workflow_job_template_nodes/$2/' in d and d.index('workflow_job_template_nodes/$1/') < d.index('workflow_job_template_nodes/$2/'))")"
upd="$(st "[(n.get('unified_job_template'), [p['identifier'] for p in s['nodes'] if n['id'] in p['success']]) for w in s['workflow_job_templates'] if w['name'] == 'STIG - full scan and upload' for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'update']")"
[ $rc = 0 ] && [ "$order" = True ] && [ "$upd" = "[(122, ['approve'])]" ] \
    && pass "... a hand-changed workflow made again: the update deleted before its approval, then attached after its link" \
    || fail "deleted leaves first" "rc $rc; order $order ($1 $2); update $upd; $(grep -E "FAILED|fatal|workflow" "$work/out.txt" | head -6)"

grep '"DELETE"' "$work/writes.jsonl" | grep -qv 'workflow_job_template_nodes/' && fail "a DELETE" "$(grep DELETE "$work/writes.jsonl" | grep -v workflow_job_template_nodes/ | head -2)" \
    || pass "never a DELETE of a template or workflow (only a workflow's own nodes, made again)"
grep -q "SECRET-PW" "$work/all-out.txt" "$work/writes.jsonl" && fail "a secret" "$(grep -n SECRET-PW "$work/all-out.txt" | head -2)" \
    || pass "no secret in any output or write"

echo
if [ $fails = 0 ]; then echo "all AAP job templates scenarios passed"; else echo "$fails AAP job templates scenario(s) failed"; exit 1; fi
