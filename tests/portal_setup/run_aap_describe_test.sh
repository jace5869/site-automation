#!/usr/bin/env bash
# AAP - set up job templates, aap_templates_describe (1.79.11; library/site_aap_describe.py) against the stand-in AAP
# (tests/portal_setup/fake_aap_crud.py). Checks: Check writes nothing and lists every change (old -> new), green; a run
# writes the catalog's five lines - an empty description, one of ours ("** Does:", the "Made by" line), a template the
# site renamed (found by its playbook), each template of a playbook with two catalog entries (by its Variables), a
# template on ACT's own podman playbook (1.79.11d), two templates of one catalog template (both, and said in short); a
# hand-written one kept, then written with aap_templates_describe_overwrite; one AAP - set up portal actions wrote left
# to it; a template whose playbook the catalog does not know and a workflow it does not know: "no description known",
# untouched, green; a catalog template not in AAP: counted (listed with aap_templates_describe_list_missing), green; the service watch workflow found by its steps under
# the site's own name; the Contact line only from the settings; aap_templates_describe_only makes no template; a second
# run already right; a write AAP refuses: FAILED, the job red, the others still written; never a DELETE.
#   bash tests/portal_setup/run_aap_describe_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${AAP_DESCRIBE_TEST_PORT:-18698}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -25 | sed 's/^/      /'; fails=$((fails + 1)); }

# the seed: templates of the project (one renamed, two of one playbook, one of a playbook the catalog does not know, one the
# portal job wrote, one of another project), the service watch workflow under the site's own name, a workflow of the site's
"$py" - "$repo/aap/job_templates.yml" "$work/state.json" "$work/expect.json" <<'PYEND'
import json, sys, yaml
cat = yaml.safe_load(open(sys.argv[1]))
by = dict((e["name"], e) for e in cat["job_templates"])
sw = next(w for w in cat.get("workflows") or [] if w["name"].lower().startswith("service watch"))
def jt(i, name, playbook, desc="", project=2, extra=""):
    return {"id": i, "name": name, "organization": 1, "project": project, "playbook": playbook, "inventory": 3, "description": desc,
            "extra_vars": extra, "ask_limit_on_launch": True, "ask_variables_on_launch": False, "ask_inventory_on_launch": False,
            "ask_job_type_on_launch": False, "execution_environment": None, "credentials": [], "labels": []}
sn = [e for e in cat["job_templates"] if e["playbook"] == "playbooks/servicenow_health.yml"]
sn_plain = next(e for e in sn if not e.get("extra_vars"))
sn_var = next(e for e in sn if e.get("extra_vars"))
k, v = sorted(sn_var["extra_vars"].items())[0]
tpls = [jt(100, "Health check", "playbooks/health_check.yml"),
        jt(101, "My Linux look", by["Troubleshoot"]["playbook"], "** Does: an older line of ours"),
        jt(102, "STIG - drift report", by["STIG drift report"]["playbook"] if "STIG drift report" in by else "playbooks/stig_drift_report.yml",
           "Written by our team: runs Mondays"),
        jt(103, "SN health", "playbooks/servicenow_health.yml"),
        jt(104, "SN nodes", "playbooks/servicenow_health.yml", extra="---\n%s: %s\n" % (k, json.dumps(v) if not isinstance(v, str) else v)),
        jt(105, "Our own job", "playbooks/our_own.yml", "Ours, not the repository's"),
        jt(106, "Patching", "playbooks/patch_hosts.yml", "Made by AAP - set up job templates (aap/job_templates.yml)"),
        jt(107, "ACT - triage finding", "playbooks/act_triage_finding.yml",
           "Workflow 44's Linux step (fapolicyd findings), read only [set up by AAP - set up portal actions, fingerprint 0123456789ab]"),
        jt(108, "Health check (old project)", "playbooks/health_check.yml", "", project=6),
        # 1.79.11d: ACT's own playbook under the site's name; a second template for one catalog template
        jt(109, "Linux - podman health check", "vendor/act/ansible/playbooks/podman_app_health.yml"),
        jt(110, "Patching again", "playbooks/patch_hosts.yml"),
        jt(111, "Linux - podman health, stigman", "vendor/act/ansible/playbooks/podman_app_health.yml", extra="---\npodman_user: stig\n")]
nid, nodes = 300, []
for i, pbk in enumerate(sw["playbooks"]):
    t = next((t for t in tpls if t["playbook"] == pbk and t["project"] == 2), None)
    if t is None:
        t = jt(120 + i, "SW step %d" % i, pbk)
        tpls.append(t)
    nodes.append({"id": nid + i, "identifier": "n%d" % i, "wf": 200, "converge": False, "success": [], "failure": [], "always": [],
                  "unified_job_template": t["id"], "approval": None})
nodes.append({"id": 380, "identifier": "ok?", "wf": 200, "converge": False, "success": [], "failure": [], "always": [],
              "unified_job_template": None, "approval": 500})
# the same steps without the approval its description promises; a catalog name whose step is another project's template
for i, n in enumerate([x for x in nodes if x["wf"] == 200 and x["unified_job_template"]]):
    nodes.append(dict(n, id=350 + i, wf=202))
nodes.append({"id": 390, "identifier": "only", "wf": 201, "converge": False, "success": [], "failure": [], "always": [],
              "unified_job_template": 105, "approval": None})
nodes.append({"id": 391, "identifier": "elsewhere", "wf": 203, "converge": False, "success": [], "failure": [], "always": [],
              "unified_job_template": 108, "approval": None})
wfs = [{"id": 200, "name": "Our container watch", "organization": 1, "description": ""},
       {"id": 201, "name": "Our own flow", "organization": 1, "description": "by hand"},
       {"id": 202, "name": "Watch, no approval", "organization": 1, "description": ""},
       {"id": 203, "name": "Daily health", "organization": 1, "description": ""}]
approvals = [{"id": 500, "name": "Approve the fix", "description": "", "timeout": 0}]
s = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}], "projects": [{"id": 2, "name": "site-automation", "organization": 1},
     {"id": 6, "name": "Old project", "organization": 1}], "inventories": [{"id": 3, "name": "Site hosts", "organization": 1}],
     "execution_environments": [], "credentials": [], "job_templates": tpls, "workflow_job_templates": wfs, "nodes": nodes,
     "approvals": approvals, "schedules": [], "roles": [], "users": [], "labels": []}
json.dump(s, open(sys.argv[2], "w"), indent=1)
def text(d, contact=True):
    lines = [("** %s: %s" % (l, d[k]))[:160] for k, l in (("does", "Does"), ("targets", "Targets"), ("produces", "Produces"), ("supports", "Supports"))]
    return "\n".join(lines + (["** Contact: Owner=OPS-TEAM, Developer=DEV-TEAM"] if contact else []))
json.dump({"hc": text(by["Health check"]["description"]), "ts": text(by["Troubleshoot"]["description"]),
           "sn": text(sn_plain["description"]), "snv": text(sn_var["description"]), "sw": text(sw["description"]),
           "sw_name": sw["name"], "sn_var_name": sn_var["name"], "patch": text(by["Patch hosts"]["description"]),
           "hc_nocontact": text(by["Health check"]["description"], False),
           "podman": text(next(v for v in cat["vendored"] if v["playbook"].endswith("/podman_app_health.yml"))["description"])},
          open(sys.argv[3], "w"))
PYEND
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
export CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token CONTROLLER_VERIFY_SSL=false
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
run() {   # run NAME [ansible args...] -> $work/out.txt, rc in $rc
    ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 "$ap" -i "$work/inv.yml" "$repo/playbooks/aap_templates_setup.yml" \
        -e aap_templates_describe_only=true -e aap_templates_describe=true "$@" > "$work/out.txt" 2>&1
    rc=$?
    cat "$work/out.txt" >> "$work/all-out.txt"
}
desc() { "$py" -c 'import json,sys; s=json.load(open(sys.argv[1])); k=sys.argv[2]; i=int(sys.argv[3])
print(next(r for r in s[k] if r["id"] == i).get("description") or "", end="")' "$work/state.json" "$1" "$2"; }
want() { "$py" -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]], end="")' "$work/expect.json" "$1"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
: > "$work/all-out.txt"
OWN='{"aap_templates_description_owner": "OPS-TEAM", "aap_templates_description_contact": "DEV-TEAM"}'

# 1. Check: nothing written, every change listed, green
run --check -e "$OWN"
if [ "$rc" -eq 0 ] && [ "$(writes)" -eq 0 ] && grep -q "WOULD DESCRIBE - job 'Health check'" "$work/out.txt" \
   && grep -q "WOULD DESCRIBE - job 'My Linux look' (catalog: 'Troubleshoot')" "$work/out.txt" \
   && grep -q "WOULD DESCRIBE - job 'Patching' (catalog: 'Patch hosts')" "$work/out.txt" \
   && grep -q "KEPT (hand-written) - job 'STIG - drift report'" "$work/out.txt" \
   && grep -q "left to its own job - job 'ACT - triage finding'" "$work/out.txt" \
   && grep -q "no description known - job 'Our own job' - its playbook playbooks/our_own.yml is not in the catalog" "$work/out.txt" \
   && grep -q "no description known - workflow 'Our own flow'" "$work/out.txt" \
   && grep -q "no description known - workflow 'Watch, no approval'" "$work/out.txt" \
   && grep -q "no description known - workflow 'Daily health' - its steps run templates of another project" "$work/out.txt" \
   && grep -q "WOULD DESCRIBE - workflow 'Our container watch' (catalog: '$(want sw_name)') - would write it; matched by the playbooks of its steps" "$work/out.txt" \
   && ! grep -q "not in AAP - job " "$work/out.txt" && ! grep -q "not in AAP - workflow " "$work/out.txt" \
   && grep -Eq "job not in AAP: [0-9]+" "$work/out.txt" && grep -q "aap_templates_describe_list_missing: true lists each" "$work/out.txt" \
   && grep -q "WOULD DESCRIBE - job 'Linux - podman health check' (catalog: 'ACT - podman health (vendored)')" "$work/out.txt" \
   && grep -q "2 templates run playbooks/patch_hosts.yml: 'Patching', 'Patching again' - keep one" "$work/out.txt" \
   && grep -q "2 templates run vendor/act/ansible/playbooks/podman_app_health.yml: 'Linux - podman health check', 'Linux - podman health, stigman' - their Variables differ" "$work/out.txt" \
   && ! grep -q "not in AAP.*vendored" "$work/out.txt" \
   && grep -q "was: (empty)" "$work/out.txt" && grep -q "now: \*\* Does: " "$work/out.txt" \
   && ! grep -q "Health check (old project)" "$work/out.txt"; then
    pass "Check: nothing written; described, kept, left, no description known listed (old -> new); not in AAP counted only; ACT's podman playbook described; two of one playbook said; green"
else fail "Check" "$(cat "$work/out.txt")"; fi

# 2. the run: written; the renamed one by its playbook; each of the two of one playbook; the Contact line from the settings
run -e "$OWN"
if [ "$rc" -eq 0 ] && [ "$(desc job_templates 100)" = "$(want hc)" ] && [ "$(desc job_templates 101)" = "$(want ts)" ] \
   && [ "$(desc job_templates 103)" = "$(want sn)" ] && [ "$(desc job_templates 104)" = "$(want snv)" ] \
   && [ "$(desc job_templates 106)" = "$(want patch)" ] && [ "$(desc workflow_job_templates 200)" = "$(want sw)" ] \
   && [ "$(desc job_templates 102)" = "Written by our team: runs Mondays" ] && [ "$(desc job_templates 105)" = "Ours, not the repository's" ] \
   && [ "$(desc workflow_job_templates 201)" = "by hand" ] && [ "$(desc job_templates 108)" = "" ] \
   && [ "$(desc workflow_job_templates 202)" = "" ] && [ "$(desc workflow_job_templates 203)" = "" ] \
   && desc job_templates 107 | grep -q "fingerprint 0123456789ab" \
   && [ "$(desc job_templates 109)" = "$(want podman)" ] && [ "$(desc job_templates 110)" = "$(want patch)" ]; then
    pass "a run: the five lines written (renamed: by playbook; two of one playbook: by Variables; the workflow by its steps); the rest untouched"
else fail "the run" "$(cat "$work/out.txt")"; fi
if "$py" - "$work/writes.jsonl" <<'PYEND'
import json, sys
w = [json.loads(l) for l in open(sys.argv[1])]
sys.exit(0 if w and all(x["method"] == "PATCH" and list(x["body"]) == ["description"] for x in w) else 1)
PYEND
then pass "only PATCHes of the description - never another field, never a POST or a DELETE"
else fail "the writes" "$(cat "$work/writes.jsonl")"; fi

# 2b. 1.79.11d: aap_templates_describe_list_missing - each catalog template not in AAP listed again
run --check -e "$OWN" -e aap_templates_describe_list_missing=true
if [ "$rc" -eq 0 ] && grep -q "not in AAP - job 'Windows health check'" "$work/out.txt" \
   && ! grep -q "aap_templates_describe_list_missing: true lists each" "$work/out.txt" && ! grep -q "not in AAP.*vendored" "$work/out.txt"; then
    pass "aap_templates_describe_list_missing: each not in AAP listed (never ACT's own playbooks)"
else fail "list missing" "$(cat "$work/out.txt")"; fi

# 2c. (Codex) aap_templates_only on a template not in AAP alone: said so, not "nothing matches"; a project not found is
# its own count, not a catalog template missing
run --check -e "$OWN" -e '{"aap_templates_only": ["Windows health check"]}'
if [ "$rc" -eq 0 ] && grep -q "only templates or workflows not in AAP - counted in short" "$work/out.txt" \
   && ! grep -q "nothing matches aap_templates_only" "$work/out.txt" && grep -q "job not in AAP: 1" "$work/out.txt"; then
    pass "aap_templates_only on one not in AAP: counted, said - not 'nothing matches'"
else fail "only, not in AAP" "$(cat "$work/out.txt")"; fi
run --check -e "$OWN" -e aap_templates_project=no-such-project
if [ "$rc" -eq 0 ] && grep -q "not in AAP - job 'no-such-project'" "$work/out.txt" && grep -q "job organization/project not found: 1" "$work/out.txt" \
   && ! grep -q "job not in AAP" "$work/out.txt" && ! grep -q "lists each" "$work/out.txt"; then
    pass "a project not found: said, counted apart from catalog templates not in AAP"
else fail "project not found" "$(cat "$work/out.txt")"; fi

# 3. again: already right, nothing written
n="$(writes)"
run -e "$OWN"
if [ "$rc" -eq 0 ] && [ "$(writes)" -eq "$n" ] && grep -q "already right - job 'Health check'" "$work/out.txt"; then
    pass "a second run: already right, nothing written"
else fail "second run" "$(cat "$work/out.txt")"; fi

# 4. no owner or contact: no Contact line (ours: written again); overwrite takes the hand-written one
run -e aap_templates_describe_overwrite=true
if [ "$rc" -eq 0 ] && [ "$(desc job_templates 100)" = "$(want hc_nocontact)" ] && desc job_templates 102 | grep -q "^\*\* Does: " \
   && [ "$(desc job_templates 105)" = "Ours, not the repository's" ] && desc job_templates 107 | grep -q "fingerprint"; then
    pass "no owner/contact: no Contact line; aap_templates_describe_overwrite: the hand-written one written, the unknown and the portal's still left"
else fail "overwrite / contact" "$(cat "$work/out.txt")"; fi

# 5. aap_templates_only
"$py" - "$work/state.json" <<'PYEND'
import json, sys
s = json.load(open(sys.argv[1]))
for t in s["job_templates"]:
    if t["id"] in (100, 103):
        t["description"] = ""
json.dump(s, open(sys.argv[1], "w"))
PYEND
run -e "$OWN" -e '{"aap_templates_only": ["Health*"]}'
if [ "$rc" -eq 0 ] && [ "$(desc job_templates 100)" = "$(want hc)" ] && [ "$(desc job_templates 103)" = "" ] \
   && ! grep -q "'SN health'" "$work/out.txt"; then
    pass "aap_templates_only: only the matching ones"
else fail "only" "$(cat "$work/out.txt")"; fi

# 6. a write AAP refuses: FAILED, the job red - the others still written
"$py" - "$work/state.json" <<'PYEND'
import json, sys
s = json.load(open(sys.argv[1]))
s["fail_paths"] = ["job_templates/103/"]
for t in s["job_templates"]:
    if t["id"] in (100, 103):
        t["description"] = ""
json.dump(s, open(sys.argv[1], "w"))
PYEND
run -e "$OWN"
if [ "$rc" -ne 0 ] && grep -q "FAILED - job 'SN health'" "$work/out.txt" && grep -q "the description was not written: HTTP 500" "$work/out.txt" \
   && [ "$(desc job_templates 100)" = "$(want hc)" ]; then
    pass "a write AAP refuses: FAILED, the job red; the others still written"
else fail "a refused write" "$(cat "$work/out.txt")"; fi

# 7. describe only makes no template; never a secret or a DELETE
if ! grep -q '"method": "POST"' "$work/writes.jsonl" && ! grep -q '"method": "DELETE"' "$work/writes.jsonl" \
   && ! grep -q "setup-token" "$work/all-out.txt"; then
    pass "aap_templates_describe_only: no template made; no DELETE; no token in the output"
else fail "describe only / secrets"; fi

# 8. AAP not reached: a warning, nothing written, green (only a refused write is red - Codex)
n="$(writes)"
CONTROLLER_HOST="http://127.0.0.1:1" run -e "$OWN"
if [ "$rc" -eq 0 ] && [ "$(writes)" -eq "$n" ] && grep -q "WARNING, not read" "$work/out.txt"; then
    pass "AAP not reached: said as a warning, nothing written, the job green"
else fail "AAP not reached" "$(cat "$work/out.txt")"; fi

[ "$fails" -eq 0 ] && echo "all AAP describe checks passed" || echo "$fails AAP describe check(s) FAILED"
[ "$fails" -eq 0 ]
