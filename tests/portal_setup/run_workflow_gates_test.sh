#!/usr/bin/env bash
# AAP - add workflow gates (1.67.0 part I, playbooks/aap_workflow_gates.yml) against the stand-in AAP
# (tests/portal_setup/fake_aap_crud.py), seeded with workflows a person built by hand, with names of their own:
# fapolicyd (check -> approval -> approved, and an always link to tickets), STIG Manager with two check -> approval pairs
# (Linux and Windows), checklists already gated by a gate template of another name, a check of the site's own (unknown),
# ACT's fix (no gate) and a workflow with no approval. Checks: Check writes nothing and lists what it would do; a run
# makes the gate template and puts a gate into each known pair, removing only the direct failure link; every other node
# and link stays; a second run changes nothing; a half-done gate is finished; a name that matches nothing fails, said;
# never a DELETE.
#   bash tests/portal_setup/run_workflow_gates_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${WORKFLOW_GATES_TEST_PORT:-18698}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

# ---- the site's workflows, built by hand
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = {"next": 5000, "organizations": [{"id": 1, "name": "Default"}], "projects": [{"id": 2, "name": "Site automation"}],
     "inventories": [{"id": 3, "name": "Site hosts"}], "execution_environments": [{"id": 5, "name": "Site EE"}],
     "credentials": [], "users": [], "approvals": [], "schedules": [], "roles": [], "job_templates": [], "workflow_job_templates": [], "nodes": []}
def jt(i, name, pb, ask=False):
    s["job_templates"].append({"id": i, "name": name, "playbook": "playbooks/" + pb, "ask_variables_on_launch": ask, "credentials": []})
for i, n, p in ((101, "Linux - fapolicyd - check (wf)", "fapolicyd.yml"), (102, "Linux - fapolicyd - approved (wf)", "fapolicyd.yml"),
                (103, "RHEL STIG - check (wf)", "stig_recheck.yml"), (104, "Windows STIG - check (wf)", "win_stig_recheck.yml"),
                (105, "STIG Manager - write (wf)", "stigman_update.yml"), (106, "Checklists - check (wf)", "stigman_ckl_import.yml"),
                (107, "Our own check", "our_check.yml"), (108, "Our own fix", "our_fix.yml"), (109, "Daily health", "health_check.yml"),
                (110, "Apply ACT fix", "act_fix_approved.yml"), (111, "Tickets", "servicenow_tickets.yml")):
    jt(i, n, p)
jt(112, "Old gate (by hand)", "workflow_gate.yml", True)
nid = [200]
def wf(i, name):
    s["workflow_job_templates"].append({"id": i, "name": name, "description": "", "organization": 1})
def node(w, tpl=None, appr=None, ident=None, extra=None):
    nid[0] += 1
    n = {"id": nid[0], "wf": w, "identifier": ident or "n%d" % nid[0], "converge": False, "unified_job_template": tpl,
         "approval": None, "success": [], "failure": [], "always": [], "extra_data": extra or {}}
    if appr:
        s["approvals"].append({"id": nid[0] + 9000, "name": appr, "description": "", "timeout": 28800})
        n["approval"] = nid[0] + 9000
    s["nodes"].append(n)
    return n
wf(301, "Linux - fapolicyd (approval)")
c, a, ap_, t = node(301, 101), node(301, appr="Make the fapolicyd changes the check listed?"), node(301, 102), node(301, 111)
c["failure"], c["always"], a["success"] = [a["id"]], [t["id"]], [ap_["id"]]
wf(302, "STIG Manager updated - Linux and Windows")
c1, c2, a1, a2, u1, u2 = node(302, 103), node(302, 104), node(302, appr="Write the Linux reviews?"), node(302, appr="Write the Windows reviews?"), node(302, 105), node(302, 105)
c1["failure"], c2["failure"], a1["success"], a2["success"] = [a1["id"]], [a2["id"]], [u1["id"]], [u2["id"]]
wf(303, "Checklists into STIG Manager")
c, g, a, u = node(303, 106), node(303, 112, extra={"workflow_gate_var": "stigman_update_plan"}), node(303, appr="Write the checklists?"), node(303, 105)
c["failure"], g["success"], a["success"] = [g["id"]], [a["id"]], [u["id"]]
wf(304, "Our own fix")
c, a, f = node(304, 107), node(304, appr="Run our fix?"), node(304, 108)
c["failure"], a["success"] = [a["id"]], [f["id"]]
wf(305, "Fix with approval (ACT)")
c, a, f = node(305, 109), node(305, appr="Apply the fix ACT proposed?"), node(305, 110)
c["failure"], a["success"] = [a["id"]], [f["id"]]
wf(306, "Daily health and tickets")
c, t = node(306, 109), node(306, 111)
c["always"] = [t["id"]]
json.dump(s, open(sys.argv[1], "w"), indent=1)
EOPY
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=gates-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
echo '{"aap_gates_project": "Site automation", "aap_gates_inventory": "Site hosts", "aap_gates_execution_environment": "Site EE"}' > "$work/vars.json"
gates() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=gates-token ANSIBLE_NOCOLOR=1 \
        "$ap" -i localhost, playbooks/aap_workflow_gates.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
# a workflow's links by the nodes' template names (approval: A:its name; a gate: {its variable}): sorted [(from, link, to)]
st_links() {
    "$py" - "$work/state.json" "$1" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1])); w = int(sys.argv[2])
def nm(n):
    if n.get("approval"):
        return "A:" + next(a["name"] for a in s["approvals"] if a["id"] == n["approval"])
    j = next(j for j in s["job_templates"] if j["id"] == n["unified_job_template"])
    return j["name"] + ("{%s}" % n["extra_data"]["workflow_gate_var"] if n.get("extra_data") else "")
print(sorted((nm(n), k, nm(m)) for n in s["nodes"] if n["wf"] == w for k in ("success", "failure", "always") for m in s["nodes"] if m["id"] in n[k]))
EOPY
}
before_303="$(st_links 303)"; before_304="$(st_links 304)"; before_305="$(st_links 305)"; before_306="$(st_links 306)"

# ---- Check first: nothing written, everything said
gates --check
if [ "$(writes)" = 0 ] && grep -q "Gate template 'Workflow gate - has a plan': would be made" "$work/out.txt" \
    && grep -q "WOULD GATE - Linux - fapolicyd (approval)" "$work/out.txt" \
    && grep -q "Linux - fapolicyd - check (wf) (fapolicyd.yml) -> Make the fapolicyd changes the check listed?: would gate - workflow_gate_var: fapolicyd_workflow_plan" "$work/out.txt" \
    && grep -q "ALREADY GATED - Checklists into STIG Manager" "$work/out.txt" \
    && grep -q "UNKNOWN CHECK - Our own fix" "$work/out.txt" && grep -q "our_check.yml is not a check this job knows" "$work/out.txt" \
    && grep -q "NO GATE (ACT'S FIX) - Fix with approval (ACT)" "$work/out.txt" \
    && grep -q "NOT A CHECK->APPROVAL - Daily health and tickets" "$work/out.txt"; then
    pass "Check: writes nothing; would gate, already gated, unknown check, ACT's fix, not a check->approval - each said"
else fail "Check" "$(writes) writes; $(grep -E "GATE|CHECK|Gate|fatal|msg" "$work/out.txt" | head -20)"; fi

[ -n "${GATES_SHOW:-}" ] && cp "$work/out.txt" "$GATES_SHOW.check.txt"     # the job's table, to look at

# ---- the run
gates
rc=$?
[ -n "${GATES_SHOW:-}" ] && cp "$work/out.txt" "$GATES_SHOW.run.txt"
got="$(st_links 301)"
want="[('A:Make the fapolicyd changes the check listed?', 'success', 'Linux - fapolicyd - approved (wf)'), ('Linux - fapolicyd - check (wf)', 'always', 'Tickets'), ('Linux - fapolicyd - check (wf)', 'failure', 'Workflow gate - has a plan{fapolicyd_workflow_plan}'), ('Workflow gate - has a plan{fapolicyd_workflow_plan}', 'success', 'A:Make the fapolicyd changes the check listed?')]"
[ $rc = 0 ] && [ "$got" = "$want" ] && pass "the user's fapolicyd workflow: check -> on failure -> gate -> on success -> approval; the tickets link stays" \
    || fail "fapolicyd workflow (rc $rc)" "$got
$(grep -E "fatal|msg|GATE" "$work/out.txt" | head)"
got="$(st_links 302)"
case "$got" in
  *"('RHEL STIG - check (wf)', 'failure', 'Workflow gate - has a plan{stigman_update_plan}')"*"('Windows STIG - check (wf)', 'failure', 'Workflow gate - has a plan{stigman_update_plan}')"*)
    if ! printf '%s' "$got" | grep -q "STIG - check (wf)', 'failure', 'A:"; then pass "two check -> approval pairs in one workflow: both gated"
    else fail "two pairs: a direct failure link left" "$got"; fi;;
  *) fail "two pairs" "$got";;
esac
[ "$(st_links 303)" = "$before_303" ] && [ "$(st_links 304)" = "$before_304" ] && [ "$(st_links 305)" = "$before_305" ] \
    && [ "$(st_links 306)" = "$before_306" ] && pass "already gated, unknown, ACT's fix, no approval: not one link changed" \
    || fail "untouched workflows" "$(st_links 303) $(st_links 304)"
got="$(st '[(j["playbook"], j["ask_variables_on_launch"], j["project"], j["inventory"], j["execution_environment"], "fingerprint" in j["description"]) for j in s["job_templates"] if j["name"] == "Workflow gate - has a plan"]')"
[ "$got" = "[('playbooks/workflow_gate.yml', True, 2, 3, 5, True)]" ] && pass "the gate template made once: its playbook, Variables on launch, the setup job's mark" \
    || fail "gate template" "$got"
grep -q "Gate template 'Workflow gate - has a plan': made" "$work/out.txt" && grep -q "GATED NOW - STIG Manager updated - Linux and Windows" "$work/out.txt" \
    && pass "the table: gated now" || fail "the table" "$(grep -E "GATE|Gate" "$work/out.txt" | head)"
got="$("$py" -c "
import json
w = [json.loads(x) for x in open('$work/writes.jsonl')]
print(sum(1 for x in w if x['method'] == 'DELETE'), sum(1 for x in w if x['body'].get('disassociate')), sum(1 for x in w if x['path'].endswith('/workflow_nodes/')))")"
[ "$got" = "0 3 3" ] && pass "no DELETE; exactly 3 direct failure links removed, 3 gate nodes added" || fail "the writes" "$got"

# ---- a second run: nothing to do
n="$(writes)"
gates
if [ "$(writes)" = "$n" ] && grep -q "ALREADY GATED - Linux - fapolicyd (approval)" "$work/out.txt" && grep -q "changed=0 " "$work/out.txt"; then
    pass "a second run changes nothing (already gated)"
else fail "second run" "$(($(writes) - n)) writes; $(grep -E "GATE|changed=" "$work/out.txt" | head)"; fi

# ---- a half-done gate (the direct link back, by hand): finished, no second gate
"$py" - "$work/state.json" <<'EOPY'
import json, sys
s = json.load(open(sys.argv[1]))
c = next(n for n in s["nodes"] if n["wf"] == 301 and n["unified_job_template"] == 101)
a = next(n for n in s["nodes"] if n["wf"] == 301 and n.get("approval"))
c["failure"].append(a["id"])
json.dump(s, open(sys.argv[1], "w"))
EOPY
gates
if [ "$(st_links 301)" = "$want" ] && grep -q "the gate was there; the direct failure link removed" "$work/out.txt"; then
    pass "a half-done gate: the direct link removed, no second gate node"
else fail "half-done" "$(st_links 301)"; fi

# ---- a name that matches nothing: said, the job fails; only the named workflows read
gates -e '{"aap_gates_workflows": ["Linux - fapolicyd*", "No such workflow"]}'
rc=$?
if [ $rc != 0 ] && grep -q "'No such workflow' matches no workflow" "$work/out.txt" && ! grep -q "Our own fix" "$work/out.txt"; then
    pass "aap_gates_workflows: only those read; a name that matches nothing fails, said"
else fail "selection" "rc $rc; $(grep -E "matches|GATE" "$work/out.txt" | head)"; fi

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
