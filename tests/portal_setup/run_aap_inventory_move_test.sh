#!/usr/bin/env bash
# AAP - move templates to another inventory (playbooks/aap_inventory_move.yml) against a stand-in AAP that keeps what it
# is given (tests/portal_setup/fake_aap_crud.py, its state seeded here). Checks: Check writes nothing and lists every
# move (a job template, a workflow, a workflow node, a schedule) with a total and the Limit warning; another
# organization's and another inventory's are never touched; aap_inventory_replace_only; a missing `to` and more moves
# than aap_inventory_replace_max stop before any write; a run moves only the inventory field; a failure part way lists
# what was moved; a second run has nothing to move; never a DELETE.
#   bash tests/portal_setup/run_aap_inventory_move_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${AAP_INVENTORY_MOVE_TEST_PORT:-18705}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

"$py" - "$work/state.json" <<'PYEND'
import json, sys
def jt(i, name, org, inv, limit=""):
    return {"id": i, "name": name, "organization": org, "project": 2, "playbook": "playbooks/x.yml", "inventory": inv, "limit": limit,
            "extra_vars": "---\nkeep: me\n", "credentials": [], "labels": []}
s = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}, {"id": 9, "name": "Lab"}],
     "projects": [{"id": 2, "name": "site-automation", "organization": 1}],
     "inventories": [{"id": 3, "name": "rhel-hosts-old", "organization": 1}, {"id": 4, "name": "linux-hosts-idm", "organization": 1},
                     {"id": 5, "name": "windows", "organization": 1}, {"id": 6, "name": "rhel-hosts-old", "organization": 9}],
     "groups": [{"id": 50, "name": "web", "inventory": 4}, {"id": 51, "name": "db", "inventory": 3}],
     "hosts": [{"id": 60, "name": "app01", "inventory": 4}],
     "credentials": [], "execution_environments": [],
     "job_templates": [jt(100, "Health check", 1, 3, "web:app01"), jt(101, "Patch hosts", 1, 3, "db"), jt(102, "Windows health", 1, 5),
                       jt(103, "Lab health", 9, 6), jt(104, "Troubleshoot", 1, 3)],
     "workflow_job_templates": [{"id": 200, "name": "Nightly", "organization": 1, "inventory": 3, "limit": ""},
                                {"id": 201, "name": "Windows nightly", "organization": 1, "inventory": 5, "limit": ""}],
     "nodes": [{"id": 300, "identifier": "n1", "wf": 201, "unified_job_template": 102, "inventory": 3, "converge": False,
                "success": [], "failure": [], "always": []},
               {"id": 301, "identifier": "n2", "wf": 201, "unified_job_template": 102, "inventory": None, "converge": False,
                "success": [], "failure": [], "always": []}],
     "schedules": [{"id": 400, "name": "Every night", "kind": "job_templates", "obj": 102, "inventory": 3, "rrule": "x"},
                   {"id": 401, "name": "Lab nightly", "kind": "job_templates", "obj": 103, "inventory": 6, "rrule": "x"}],
     "approvals": [], "roles": [], "users": [], "labels": []}
json.dump(s, open(sys.argv[1], "w"), indent=1)
PYEND
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
: > "$work/all-out.txt"
cat > "$work/vars.json" <<'EOF'
{"aap_inventory_replace": [{"from": "rhel-hosts-old", "to": "linux-hosts-idm"}]}
EOF
run() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token ANSIBLE_NOCOLOR=1 \
        "$ap" -i localhost, playbooks/aap_inventory_move.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
    local rc=$?
    cat "$work/out.txt" >> "$work/all-out.txt"
    return $rc
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
setst() { "$py" -c "import json; s=json.load(open('$work/state.json')); $1; json.dump(s, open('$work/state.json','w'))"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
inv() { st "[r for r in s['$1'] if r['id'] == $2][0]['inventory']"; }
has() { grep -qF -- "$1" "$work/out.txt"; }

# ---- Check: nothing written; every move listed, with the total and the Limit warning
run --check; rc=$?
if [ $rc = 0 ] && [ "$(writes)" = 0 ] && has "would move: job template 'Health check': rhel-hosts-old -> linux-hosts-idm" \
    && has "would move: job template 'Patch hosts': rhel-hosts-old -> linux-hosts-idm" \
    && has "would move: workflow 'Nightly': rhel-hosts-old -> linux-hosts-idm" \
    && has "would move: workflow node 'Windows health (in workflow Windows nightly)': rhel-hosts-old -> linux-hosts-idm" \
    && has "would move: schedule 'Every night (of Windows health)': rhel-hosts-old -> linux-hosts-idm" \
    && has "warning: its Limit names 'db': no group or host of that name in 'linux-hosts-idm'" \
    && has "6 would move (job template: 3, schedule: 1, workflow: 1, workflow node: 1), 1 with a Limit warning" \
    && ! has "Lab health" && ! has "Lab nightly" && ! has "'Windows nightly':"; then
    pass "Check: a job template, a workflow, a node and a schedule listed with the total; the Limit warning; nothing written"
else fail "Check" "rc $rc; $(writes) writes; $(grep -E "would move|warning|fatal|msg" "$work/out.txt" | head -14)"; fi
! has "warning: its Limit names 'web'" && ! has "'app01'" && pass "... a group and a host the new inventory has: no warning" \
    || fail "no warning for web/app01" "$(grep warning "$work/out.txt")"

# ---- a missing `to`, more moves than the cap: stopped before any write
run -e '{"aap_inventory_replace": [{"from": "rhel-hosts-old", "to": "linux-hosts-idx"}]}'; rc=$?
[ $rc != 0 ] && [ "$(writes)" = 0 ] && has "aap_inventory_replace to 'linux-hosts-idx': not found in organization 'Default'" \
    && pass "a 'to' not in the organization: stopped, nothing written" || fail "missing to" "rc $rc; $(grep -E "fatal|not found" "$work/out.txt" | head -3)"
run -e '{"aap_inventory_replace_max": 5}'; rc=$?
[ $rc != 0 ] && [ "$(writes)" = 0 ] && has "6 to move, more than aap_inventory_replace_max (5): nothing moved" \
    && pass "more moves than aap_inventory_replace_max: stopped, nothing written" || fail "max" "rc $rc; $(grep -E "fatal|more than" "$work/out.txt" | head -3)"

# ---- aap_inventory_replace_only: one template moved, only its inventory changed
run -e '{"aap_inventory_replace_only": ["health*"]}'; rc=$?
if [ $rc = 0 ] && [ "$(inv job_templates 100)" = 4 ] && [ "$(inv job_templates 101)" = 3 ] && [ "$(inv workflow_job_templates 200)" = 3 ] \
    && [ "$(st "[r for r in s['job_templates'] if r['id'] == 100][0]['extra_vars']")" = "$(printf -- '---\nkeep: me')" ] \
    && [ "$(st "[r for r in s['job_templates'] if r['id'] == 100][0]['limit']")" = "web:app01" ] \
    && [ "$(writes)" = 1 ] && has "moved: job template 'Health check': rhel-hosts-old -> linux-hosts-idm" && has "1 moved"; then
    pass "aap_inventory_replace_only: the one template moved - its inventory only (Variables, Limit kept)"
else fail "only" "rc $rc; $(writes) writes; $(grep -E "moved|fatal" "$work/out.txt" | head -4)"; fi

# ---- a failure part way: what was moved before it is listed
setst "s['fail_paths']=['workflow_job_templates/200/']"
run; rc=$?
setst "s.pop('fail_paths')"
[ $rc != 0 ] && has "moved: job template 'Patch hosts'" && has "FAILED: workflow 'Nightly'" && [ "$(inv job_templates 101)" = 4 ] \
    && [ "$(inv workflow_job_templates 200)" = 3 ] && has "stopped at workflow 'Nightly'" \
    && pass "a failure part way: the moves before it listed as moved, it said, the rest not tried" \
    || fail "failure part way" "rc $rc; $(grep -E "moved|FAILED|stopped" "$work/out.txt" | head -6)"

# ---- the run: the rest moved; another organization's and another inventory's left; a second run has nothing
run; rc=$?
if [ $rc = 0 ] && [ "$(inv workflow_job_templates 200)" = 4 ] && [ "$(st "[n for n in s['nodes'] if n['id'] == 300][0]['inventory']")" = 4 ] \
    && [ "$(st "[n for n in s['nodes'] if n['id'] == 301][0]['inventory']")" = None ] && [ "$(inv schedules 400)" = 4 ] \
    && [ "$(inv job_templates 103)" = 6 ] && [ "$(inv schedules 401)" = 6 ] && [ "$(inv job_templates 102)" = 5 ] \
    && [ "$(inv workflow_job_templates 201)" = 5 ] && [ "$(inv job_templates 104)" = 4 ]; then
    pass "a run: the workflow, the node and the schedule moved; another organization's and another inventory's left"
else fail "the run" "rc $rc; $(grep -E "moved|FAILED|fatal" "$work/out.txt" | head -8)"; fi
before="$(writes)"
run; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "nothing uses those inventories" && pass "a second run: nothing to move, nothing written" \
    || fail "second run" "rc $rc; $(writes) vs $before"

# ---- only PATCH of the inventory; never a DELETE
"$py" - "$work/writes.jsonl" <<'PYEND' && pass "every write a PATCH of the inventory field only, never a DELETE" || fail "the writes" "$(head -3 "$work/writes.jsonl")"
import json, sys
w = [json.loads(l) for l in open(sys.argv[1])]
sys.exit(0 if w and all(x["method"] == "PATCH" and set(x["body"]) == {"inventory"} for x in w) else 1)
PYEND

echo
if [ $fails = 0 ]; then echo "all AAP inventory move scenarios passed"; else echo "$fails AAP inventory move scenario(s) failed"; exit 1; fi
