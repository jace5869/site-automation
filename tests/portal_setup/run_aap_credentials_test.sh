#!/usr/bin/env bash
# AAP - set up credentials (playbooks/aap_credentials_setup.yml) against a stand-in AAP that keeps what it is given
# (tests/portal_setup/fake_aap_crud.py, its state a JSON file seeded here). Checks: Check first writes nothing and says
# what it would do; a rename; a rename onto a name that is there - an error, nothing written; a name in two
# organizations - an error unless aap_credential_organization; attach; a template with a credential of the same type -
# kept (said) unless aap_credential_replace_same_type, then replaced; detach; a second run - already done; a workflow
# named - said; a write that fails mid-run stops, what was done before it listed; never a secret in the output; never a
# DELETE.
#   bash tests/portal_setup/run_aap_credentials_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${AAP_CREDENTIALS_TEST_PORT:-18698}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

"$py" - "$work/state.json" <<'PYEND'
import json, sys
def cred(i, name, org, typ, secret):
    return {"id": i, "name": name, "organization": org, "credential_type": typ, "inputs": {"password": secret, "username": "svc"},
            "summary_fields": {"credential_type": {"id": typ, "name": {1: "Machine", 17: "Red Hat Ansible Automation Platform"}[typ]},
                               "organization": {"id": org, "name": {1: "Default", 2: "Second"}[org]}}}
s = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}, {"id": 2, "name": "Second"}],
     "projects": [], "inventories": [], "execution_environments": [], "users": [],
     "credentials": [cred(10, "Report website", 1, 1, "SECRET-PW-10"), cred(11, "AAP - site portal", 1, 17, "SECRET-PW-11"),
                     cred(12, "AAP reader", 1, 17, "SECRET-PW-12"), cred(13, "Shared name", 1, 1, "SECRET-PW-13"),
                     cred(14, "Shared name", 2, 1, "SECRET-PW-14"), cred(15, "Taken", 1, 1, "SECRET-PW-15"),
                     cred(16, "Only second", 2, 1, "SECRET-PW-16")],
     "job_templates": [{"id": 100, "name": "Site portal - deploy", "organization": 1, "credentials": [12]},
                       {"id": 101, "name": "Plain template", "organization": 1, "credentials": []}],
     "workflow_job_templates": [{"id": 200, "name": "A workflow", "organization": 1}],
     "nodes": [], "approvals": [], "schedules": [], "roles": []}
json.dump(s, open(sys.argv[1], "w"), indent=1)
PYEND
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
: > "$work/all-out.txt"
run() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token ANSIBLE_NOCOLOR=1 \
        "$ap" -i localhost, playbooks/aap_credentials_setup.yml "$@") > "$work/out.txt" 2>&1
    local rc=$?
    cat "$work/out.txt" >> "$work/all-out.txt"
    return $rc
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
creds_of() { st "sorted([j for j in s['job_templates'] if j['name'] == '$1'][0]['credentials'])"; }
V='{"aap_credential_renames": {"Report website": "Report website - nginx"},
    "aap_credential_attach": {"AAP - site portal": ["Site portal - deploy", "Plain template"]}}'

# ---- Check first: nothing written, says what it would do
run --check -e "$V"
if [ "$(writes)" = 0 ] && grep -q "would rename - 'Report website' to 'Report website - nginx'" "$work/out.txt" \
    && grep -q "would attach - 'AAP - site portal' on 'Plain template'" "$work/out.txt" \
    && grep -q "attach: NOT DONE - 'AAP - site portal' on 'Site portal - deploy' - would replace 'AAP reader' (Red Hat Ansible Automation Platform) - set aap_credential_replace_same_type: true" "$work/out.txt"; then
    pass "Check first: would rename, would attach, would replace (same type) said - nothing written"
    grep -qE "localhost +: ok=[0-9]+ +changed=1" "$work/out.txt" && pass "... and Check reports changed (what it would change)" \
        || fail "Check: changed" "$(grep -E "localhost +:" "$work/out.txt")"
else fail "Check first" "$(writes) writes; $(grep -E "would|NOT DONE|fatal|msg" "$work/out.txt" | head -10)"; fi

# ---- the run: renamed, attached; the same-type one kept, said
run -e "$V"; rc=$?
if [ $rc = 0 ] && [ "$(st "[c['name'] for c in s['credentials'] if c['id'] == 10][0]")" = "Report website - nginx" ] \
    && [ "$(creds_of 'Plain template')" = "[11]" ] && [ "$(creds_of 'Site portal - deploy')" = "[12]" ] \
    && grep -q "rename: done - 'Report website' to 'Report website - nginx'" "$work/out.txt"; then
    pass "a run: renamed, attached; the template with an AAP credential already keeps it (said)"
else fail "the run" "rc $rc; $(st "[(c['id'], c['name']) for c in s['credentials']]") $(creds_of 'Plain template') $(creds_of 'Site portal - deploy')
$(grep -E "done|NOT DONE|fatal|msg" "$work/out.txt" | head -10)"; fi

# ---- again: already done, nothing written
before="$(writes)"
run -e "$V"; rc=$?
if [ $rc = 0 ] && [ "$(writes)" = "$before" ] && grep -q "rename: already done - 'Report website' to 'Report website - nginx'" "$work/out.txt" \
    && grep -q "attach: already done - 'AAP - site portal' on 'Plain template'" "$work/out.txt"; then
    pass "a second run: already done, nothing written"
else fail "a second run" "rc $rc; $(writes) vs $before; $(grep -E "done|fatal" "$work/out.txt" | head -8)"; fi

# ---- the same type replaced when asked
run -e '{"aap_credential_attach": {"AAP - site portal": ["Site portal - deploy"]}, "aap_credential_replace_same_type": true}'; rc=$?
if [ $rc = 0 ] && [ "$(creds_of 'Site portal - deploy')" = "[11]" ] && grep -q "take off: done - 'AAP reader' from 'Site portal - deploy' - taken off to make room for 'AAP - site portal' (same type)" "$work/out.txt" \
    && grep -q "replace (same type): done - 'AAP - site portal' on 'Site portal - deploy'" "$work/out.txt"; then
    pass "aap_credential_replace_same_type: true - the other AAP credential taken off, the new one attached"
else fail "replace" "rc $rc; $(creds_of 'Site portal - deploy'); $(grep -E "done|fatal|msg" "$work/out.txt" | head -6)"; fi

# ---- a rename onto a name that is there: an error, nothing written (the other rename in the same run neither)
before="$(writes)"
run -e '{"aap_credential_renames": {"AAP reader": "Taken", "Shared name": "Renamed"}, "aap_credential_organization": "Default"}'; rc=$?
if [ $rc != 0 ] && [ "$(writes)" = "$before" ] && grep -q "rename 'AAP reader' to 'Taken': a credential of that name is there (id 15) - nothing is changed" "$work/out.txt"; then
    pass "a rename onto a name that is there: an error, nothing written - not even the other rename"
else fail "the clash" "rc $rc; $(writes) vs $before; $(grep -E "fatal|msg|done" "$work/out.txt" | head -6)"; fi

# ---- Codex: no organization set - a rename onto a name another organization has is a clash too
before="$(writes)"
run -e '{"aap_credential_renames": {"Report website - nginx": "Only second"}}'; rc=$?
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && grep -q "rename 'Report website - nginx' to 'Only second': a credential of that name is there (id 16)" "$work/out.txt" \
    && pass "no organization set: a rename onto a name of another organization - an error, nothing written" \
    || fail "a clash across organizations" "rc $rc; $(grep -E "fatal|msg" "$work/out.txt" | head -4)"
# ---- Codex: a self-rename of a name that is not there - an error, and the attach in the same run not written
before="$(writes)"
run -e '{"aap_credential_renames": {"Missing": "Missing"}, "aap_credential_attach": {"Taken": ["Plain template"]}}'; rc=$?
[ $rc != 0 ] && [ "$(writes)" = "$before" ] && grep -q "credential 'Missing': not found" "$work/out.txt" \
    && pass "a self-rename of a name not there: an error, the attach of the same run not written" \
    || fail "a self-rename" "rc $rc; $(writes) vs $before; $(grep -E "fatal|msg|done" "$work/out.txt" | head -4)"

# ---- a name in two organizations: an error unless aap_credential_organization
before="$(writes)"
run -e '{"aap_credential_attach": {"Shared name": ["Plain template"]}}'; rc=$?
if [ $rc != 0 ] && [ "$(writes)" = "$before" ] && grep -q "credential 'Shared name': 2 match (organizations: Default, Second) - set aap_credential_organization" "$work/out.txt"; then
    pass "a name in two organizations: an error, nothing written"
else fail "ambiguous" "rc $rc; $(grep -E "fatal|msg" "$work/out.txt" | head -6)"; fi
run -e '{"aap_credential_attach": {"Shared name": ["Plain template"]}, "aap_credential_organization": "Default"}'; rc=$?
[ $rc = 0 ] && [ "$(creds_of 'Plain template')" = "[11, 13]" ] && pass "... with aap_credential_organization: Default - the one of Default attached" \
    || fail "with the organization" "rc $rc; $(creds_of 'Plain template'); $(grep -E "fatal|msg" "$work/out.txt" | head -4)"

# ---- detach: only that one; again: already done
run -e '{"aap_credential_detach": {"Shared name": ["Plain template"]}, "aap_credential_organization": "Default"}'; rc=$?
if [ $rc = 0 ] && [ "$(creds_of 'Plain template')" = "[11]" ] && grep -q "take off: done - 'Shared name' from 'Plain template'" "$work/out.txt"; then
    pass "detach: only that credential taken off"
else fail "detach" "rc $rc; $(creds_of 'Plain template'); $(grep -E "done|fatal" "$work/out.txt" | head -4)"; fi
run -e '{"aap_credential_detach": {"Shared name": ["Plain template"]}, "aap_credential_organization": "Default"}'; rc=$?
[ $rc = 0 ] && grep -q "take off: already done - 'Shared name' from 'Plain template' - not attached" "$work/out.txt" && pass "... again: already done" \
    || fail "detach again" "rc $rc; $(grep -E "done|fatal" "$work/out.txt" | head -4)"

# ---- Codex: take one off and attach another of the same type in one run - planned on what the template will have
run -e '{"aap_credential_detach": {"AAP - site portal": ["Site portal - deploy"]}, "aap_credential_attach": {"AAP reader": ["Site portal - deploy"]}}'; rc=$?
[ $rc = 0 ] && [ "$(creds_of 'Site portal - deploy')" = "[12]" ] && grep -q "attach: done - 'AAP reader' on 'Site portal - deploy'" "$work/out.txt" \
    && pass "take off A and attach B of the same type in one run: B attached (not 'would replace')" \
    || fail "take off and attach" "rc $rc; $(creds_of 'Site portal - deploy'); $(grep -E "done|NOT DONE|fatal" "$work/out.txt" | head -4)"
# ---- Codex: a replace whose attach fails after the take-off - the take-off is listed
"$py" -c "import json; s=json.load(open('$work/state.json')); s['fail_attach_paths']=['job_templates/100/credentials/']; json.dump(s, open('$work/state.json','w'))"
run -e '{"aap_credential_attach": {"AAP - site portal": ["Site portal - deploy"]}, "aap_credential_replace_same_type": true}'; rc=$?
if [ $rc != 0 ] && [ "$(creds_of 'Site portal - deploy')" = "[]" ] && grep -q "take off: done - 'AAP reader' from 'Site portal - deploy'" "$work/out.txt" \
    && grep -q "replace (same type): FAILED - 'AAP - site portal' on 'Site portal - deploy' - POST job_templates/100/credentials/: HTTP 500 (fields: inputs)" "$work/out.txt"; then
    pass "a replace that fails after the take-off: the take-off listed (the template has neither now), the refusal by field names only"
else fail "a replace that fails" "rc $rc; $(creds_of 'Site portal - deploy'); $(grep -E "done|FAILED|fatal" "$work/out.txt" | head -5)"; fi
"$py" -c "import json; s=json.load(open('$work/state.json')); s.pop('fail_attach_paths'); json.dump(s, open('$work/state.json','w'))"

# ---- a workflow named: said, nothing attached
run -e '{"aap_credential_attach": {"Taken": ["A workflow"]}}'; rc=$?
[ $rc != 0 ] && grep -q "'A workflow' is a workflow: a workflow has no credentials of its own in AAP" "$work/out.txt" && pass "a workflow named: said, nothing attached" \
    || fail "a workflow" "rc $rc; $(grep -E "fatal|msg" "$work/out.txt" | head -4)"

# ---- a write that fails mid-run: stops; what was done before it listed
"$py" -c "import json; s=json.load(open('$work/state.json')); s['fail_paths']=['job_templates/101/credentials/']; json.dump(s, open('$work/state.json','w'))"
run -e '{"aap_credential_renames": {"Taken": "Taken - old"}, "aap_credential_attach": {"Taken - old": ["Plain template"]}}'; rc=$?
if [ $rc != 0 ] && grep -q "rename: done - 'Taken' to 'Taken - old'" "$work/out.txt" && grep -q "attach: FAILED - 'Taken' on 'Plain template'" "$work/out.txt" \
    && grep -q "stopped at attach 'Taken' -> 'Plain template'" "$work/out.txt"; then
    pass "a write that fails: the run stops, the rename done before it listed"
else fail "a failure mid-run" "rc $rc; $(grep -E "done|FAILED|stopped|fatal" "$work/out.txt" | head -6)"; fi

# ---- never a secret, never a DELETE, never a credential changed but its name
if grep -q "SECRET-PW" "$work/all-out.txt"; then fail "a secret in the output" "$(grep -n SECRET-PW "$work/all-out.txt" | head -3)"
else pass "no secret field in any output (inputs never read out)"; fi
only_renames() {   # every write to a credential is a PATCH of its name alone
    "$py" -c "
import json, sys
for l in open('$work/writes.jsonl'):
    w = json.loads(l)
    if w['path'].startswith('credentials/') and (w['method'] != 'PATCH' or set(w.get('body') or {}) != {'name'}):
        sys.exit(1)
"
}
if grep -q '"DELETE"' "$work/writes.jsonl"; then fail "a DELETE" "$(grep DELETE "$work/writes.jsonl" | head -3)"
elif ! only_renames; then fail "a credential write that was not a rename" "$(grep credentials/ "$work/writes.jsonl" | head -3)"
else pass "never a DELETE; a credential only ever renamed (its name, nothing else written)"; fi
grep -q "SECRET-PW" "$work/writes.jsonl" && fail "a secret written" || pass "no secret in what was written"

echo
if [ $fails = 0 ]; then echo "all AAP credentials scenarios passed"; else echo "$fails AAP credentials scenario(s) failed"; exit 1; fi
