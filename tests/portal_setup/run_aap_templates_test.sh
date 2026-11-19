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
            "ask_variables_on_launch": False, "ask_inventory_on_launch": False, "execution_environment": ee, "credentials": creds, "labels": []}
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
                       jt(103, "Windows health check", 9, 2, "playbooks/win_health_check.yml", [])],
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
                        "Database health*", "poa&m STATUS", "ServiceNow health - nodes", "Windows troubleshoot"]}
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
    && has "left to another job - 'ACT - triage finding'" && has "SKIPPED - 'Windows health check' - the name is used by a template of another organization" \
    && has "SKIPPED - 'VM - restart' - credential kind vmware not mapped (aap_templates_credentials)" \
    && has "would label portal: portal, portal-area:STIG" && grep -qE "would label portal: [0-9]+" "$work/out.txt"; then
    pass "Check: would create, differs (field by field), right, other name, left, skipped, would label - nothing written"
else fail "Check" "$(writes) writes; $(grep -E "WOULD|DIFFERS|already|SKIPPED|other name|left|label|fatal|msg" "$work/out.txt" | head -20)"; fi

# ---- the run: made; the one that differs left as it is
run; rc=$?
nt="$(st "len([j for j in s['job_templates'] if j['organization'] == 1])")"
if [ $rc = 0 ] && [ "$nt" = 7 ] && [ "$(tpl 'POA&M status' "['credentials']")" = "[13]" ] && [ "$(tpl 'Health check' "['project']")" = 6 ] \
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

# ---- never a DELETE, never a secret
grep -q '"DELETE"' "$work/writes.jsonl" && fail "a DELETE" "$(grep DELETE "$work/writes.jsonl" | head -2)" || pass "never a DELETE"
grep -q "SECRET-PW" "$work/all-out.txt" "$work/writes.jsonl" && fail "a secret" "$(grep -n SECRET-PW "$work/all-out.txt" | head -2)" \
    || pass "no secret in any output or write"

echo
if [ $fails = 0 ]; then echo "all AAP job templates scenarios passed"; else echo "$fails AAP job templates scenario(s) failed"; exit 1; fi
