#!/usr/bin/env bash
# AAP - set up job templates, aap_templates_rename and aap_templates_match (1.79.19; library/site_aap_rename.py and
# site_aap_templates.py) against the stand-in AAP (tests/portal_setup/fake_aap_crud.py). Checks: Check writes nothing and
# lists every rename (old -> new) and field change (has -> should be), and why one is not renamed; a run renames by
# playbook (a playbook of two catalog templates by the Variables), a workflow by its steps, any name by
# aap_templates_rename_map; never onto a taken name (another organization's too); two templates of one catalog template:
# only the one the map names, the other listed; the Network area left out (aap_templates_rename_skip_areas); a template
# another job makes left; one portal_aap_names names: only by the map, with the portal_aap_names line to change; match
# brings the fields named to the catalog's (inventory, prompts, credentials only added - a Machine credential there kept,
# labels) and says what else differs; the description stays; a second run: already right, nothing written; a rename AAP
# refuses: FAILED, red, the others still renamed; aap_templates_rename off: nothing renamed; never a DELETE;
# aap_templates_create_missing: false - a catalog template not in AAP not made, only counted.
#   bash tests/portal_setup/run_aap_rename_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${AAP_RENAME_TEST_PORT:-18697}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -30 | sed 's/^/      /'; fails=$((fails + 1)); }

"$py" - "$repo/aap/job_templates.yml" "$work/state.json" <<'PYEND'
import json, sys, yaml
cat = yaml.safe_load(open(sys.argv[1]))
sw = next(w for w in cat["workflows"] if w["name"].startswith("Service watch"))
sn_var = next(e for e in cat["job_templates"] if e["playbook"] == "playbooks/servicenow_health.yml" and e.get("extra_vars"))
k, v = sorted(sn_var["extra_vars"].items())[0]
def cred(i, name, typ, tname):
    return {"id": i, "name": name, "organization": 1, "credential_type": typ, "inputs": {"password": "SECRET-PW-%d" % i},
            "summary_fields": {"credential_type": {"id": typ, "name": tname}}}
def jt(i, name, playbook, creds=(), inv=3, org=1, project=2, desc="", extra="", ask_limit=True):
    return {"id": i, "name": name, "organization": org, "project": project, "playbook": playbook, "inventory": inv, "description": desc,
            "extra_vars": extra, "ask_limit_on_launch": ask_limit, "ask_variables_on_launch": False, "ask_inventory_on_launch": False,
            "ask_job_type_on_launch": False, "execution_environment": 5, "credentials": list(creds), "labels": []}
tpls = [jt(100, "Daily health check", "playbooks/health_check.yml", [15], inv=4, ask_limit=False, desc="** Does: an older line of ours"),
        jt(101, "Investigate", "playbooks/troubleshoot.yml", [10]),
        jt(102, "Look at a host", "playbooks/troubleshoot.yml", [10]),
        jt(103, "SN health", "playbooks/servicenow_health.yml"),
        jt(104, "SN nodes", "playbooks/servicenow_health.yml", extra="---\n%s: %s\n" % (k, v)),
        jt(105, "Net backup", "playbooks/net_config_backup.yml", inv=4),
        jt(106, "Recheck STIG", "playbooks/stig_recheck.yml"),
        jt(107, "Cert report", "playbooks/cert_report.yml"),
        jt(108, "Certificate report", "playbooks/cert_report.yml", org=9, project=6),     # another organization has the name
        jt(109, "Patch", "playbooks/patch_hosts.yml", [10]),
        jt(110, "Windows health check", "playbooks/win_health_check.yml", [11]),
        jt(111, "Drift", "playbooks/stig_drift_report.yml", [15]),
        jt(112, "Our posture", "playbooks/poam_status.yml"),                       # a portal_actions_extra action runs it
        jt(113, "DB watch", "playbooks/database_health.yml"),                      # its catalog name is a workflow's elsewhere
        jt(114, "STIG Manager - import checklists - check", "playbooks/stigman_ckl_import.yml"),   # the portal setup made it
        jt(115, "Cert install check", "playbooks/cert_install_check.yml")]        # the map names it another name
cw = next(w for w in cat["workflows"] if w["name"] == "Certificate watch")          # "No approval" in its description
nodes = []
for i, pbk in enumerate(sw["playbooks"]):
    t = next((t for t in tpls if t["playbook"] == pbk and t["project"] == 2), None)
    if t is None:
        t = jt(120 + i, "SW step %d" % i, pbk)
        tpls.append(t)
    nodes.append({"id": 300 + i, "identifier": "n%d" % i, "wf": 200, "converge": False, "success": [], "failure": [], "always": [],
                  "unified_job_template": t["id"], "approval": None})
nodes.append({"id": 380, "identifier": "ok?", "wf": 200, "converge": False, "success": [], "failure": [], "always": [],
              "unified_job_template": None, "approval": 500})
for i, pbk in enumerate(cw["playbooks"]):
    t = next((t for t in tpls if t["playbook"] == pbk and t["project"] == 2), None)
    if t is None:
        t = jt(130 + i, "CW step %d" % i, pbk)
        tpls.append(t)
    for wf, nid in ((204, 400), (205, 420)):
        nodes.append({"id": nid + i, "identifier": "c%d" % i, "wf": wf, "converge": False, "success": [], "failure": [], "always": [],
                      "unified_job_template": t["id"], "approval": None})
nodes.append({"id": 440, "identifier": "nested", "wf": 205, "converge": False, "success": [], "failure": [], "always": [],
              "unified_job_template": None, "approval": None, "workflow": 201})
wfs = [{"id": 200, "name": "Our container watch", "organization": 1, "description": "kept as it is"},
       {"id": 201, "name": "My nightly", "organization": 1, "description": ""},
       {"id": 203, "name": "Database health - MariaDB / MySQL", "organization": 9, "description": ""},
       {"id": 204, "name": "Our cert watch", "organization": 1, "description": ""},
       {"id": 205, "name": "Our cert watch, and more", "organization": 1, "description": ""}]
s = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}, {"id": 9, "name": "Lab"}],
     "projects": [{"id": 2, "name": "site-automation", "organization": 1}, {"id": 6, "name": "Lab project", "organization": 9}],
     "inventories": [{"id": 3, "name": "Site hosts", "organization": 1}, {"id": 4, "name": "Old hosts", "organization": 1}],
     "execution_environments": [{"id": 5, "name": "All-in-one EE"}],
     "credentials": [cred(10, "Linux machine", 1, "Machine"), cred(11, "Windows machine", 1, "Machine"), cred(15, "Old machine", 1, "Machine"),
                     cred(13, "STIG Manager API", 30, "STIG Manager API")],
     "job_templates": tpls, "workflow_job_templates": wfs, "nodes": nodes,
     "approvals": [{"id": 500, "name": "Approve the fix", "description": "", "timeout": 0}],
     "schedules": [], "roles": [], "users": [], "labels": []}
json.dump(s, open(sys.argv[2], "w"), indent=1)
PYEND
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
export CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token CONTROLLER_VERIFY_SSL=false
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
cat > "$work/vars.json" <<'EOF'
{"aap_templates_rename": true, "aap_templates_inventory": "Site hosts", "aap_templates_ee": {"all": "All-in-one EE"},
 "aap_templates_credentials": {"machine": "Linux machine", "machine_windows": "Windows machine", "stigman_api": "STIG Manager API"},
 "aap_templates_rename_map": {"Look at a host": "Troubleshoot", "My nightly": "Nightly checks", "No such template": "Nightly two",
                              "Cert install check": "Our cert install check"},
 "aap_templates_match": ["inventory", "credentials", "prompts", "ee", "labels"],
 "portal_aap_names": {"disk_space_request": "Patch"},
 "portal_actions_extra": {"our_poam": {"title": "POA&M", "aap": {"type": "job", "name": "Our posture"}}},
 "aap_templates_only": ["Health check", "Troubleshoot", "ServiceNow health*", "Network config backup", "STIG recheck", "Certificate report",
                        "Patch hosts", "Windows health check", "STIG drift report", "Service watch*", "Nightly*", "POA&M status",
                        "Database health*", "Certificate watch", "Our cert watch*", "STIG Manager - import checklists*",
                        "Certificate install - check", "Our cert install check"]}
EOF
run() {   # run [ansible args...] -> $work/out.txt, rc in $rc
    ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 "$ap" -i "$work/inv.yml" "$repo/playbooks/aap_templates_setup.yml" \
        -e "@$work/vars.json" "$@" > "$work/out.txt" 2>&1
    rc=$?
    cat "$work/out.txt" >> "$work/all-out.txt"
}
field() { "$py" -c 'import json,sys; s=json.load(open(sys.argv[1])); r=next(r for r in s[sys.argv[2]] if r["id"] == int(sys.argv[3]))
v=r.get(sys.argv[4]); print(json.dumps(v) if not isinstance(v, str) else v, end="")' "$work/state.json" "$1" "$2" "$3"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
has() { grep -qF -- "$1" "$work/out.txt"; }
renames() { sed -n '/renamed to the catalog/,/made or checked/p' "$work/out.txt" | grep -v '^$'; }
: > "$work/all-out.txt"

# 1. Check: nothing written, every rename and field change listed, green; the descriptions shown under the new names
run --check -e aap_templates_describe=true
if [ "$rc" -eq 0 ] && [ "$(writes)" -eq 0 ] \
   && has "not renamed - job 'STIG Manager - import checklists - check' -> 'STIG Manager - import checklists' - AAP - set up portal actions made it" \
   && has "WOULD RENAME - job 'Cert install check' -> 'Our cert install check' - aap_templates_rename_map" \
   && has "WOULD DESCRIBE - job 'Health check'" && ! has "WOULD DESCRIBE - job 'Daily health check'" \
   && renames | grep -q "^changed: \[localhost\]" \
   && has "WOULD RENAME - job 'Daily health check' -> 'Health check' - by the playbook" \
   && has "WOULD RENAME - job 'SN health' -> 'ServiceNow health' - by the playbook" \
   && has "WOULD RENAME - job 'SN nodes' -> 'ServiceNow health - nodes' - by the playbook" \
   && has "WOULD RENAME - job 'Look at a host' -> 'Troubleshoot' - aap_templates_rename_map" \
   && has "NOT RENAMED (one of several) - job 'Investigate' -> 'Troubleshoot' - 2 job templates run playbooks/troubleshoot.yml" \
   && has "2 job templates run playbooks/troubleshoot.yml: 'Investigate', 'Look at a host' - aap_templates_rename_map renames 'Look at a host'; the other left" \
   && has "not renamed - job 'Net backup' -> 'Network config backup' - area Network (aap_templates_rename_skip_areas)" \
   && has "not renamed - job 'Recheck STIG' -> 'STIG recheck' - AAP - set up portal actions makes 'STIG recheck'" \
   && has "NOT RENAMED (name taken) - job 'Cert report' -> 'Certificate report' - AAP has a job template named 'Certificate report' already" \
   && has "not renamed - job 'Patch' -> 'Patch hosts' - a site portal action runs it by that name (portal_aap_names: disk_space_request)" \
   && has "not renamed - job 'Our posture' -> 'POA&M status' - a site portal action runs it by that name (portal_actions_extra: our_poam)" \
   && has "NOT RENAMED (name taken) - job 'DB watch' -> 'Database health - MariaDB / MySQL' - AAP has a workflow named" \
   && has "WOULD RENAME - workflow 'Our cert watch' -> 'Certificate watch' - by its steps" \
   && ! has "'Our cert watch, and more' ->" \
   && has "WOULD RENAME - workflow 'Our container watch' -> 'Service watch - approve or self-heal' - by its steps" \
   && has "WOULD RENAME - workflow 'My nightly' -> 'Nightly checks' - aap_templates_rename_map" \
   && has "not found - job 'No such template' -> 'Nightly two'" \
   && has "job already right: 1" \
   && has "WOULD FIX - 'Health check'" && has "inventory: has 'Old hosts', should be Site hosts" \
   && has "prompt for Limit: has no, should be yes" \
   && has "credential machine: Old machine kept (a Machine credential is there; aap_templates_overwrite: true puts Linux machine instead)" \
   && has "would label portal: portal, portal-area:Linux" \
   && has "WOULD FIX - 'STIG drift report'" && has "credential stigman_api: has none, should be STIG Manager API" \
   && ! has "WOULD CREATE - 'Health check'" && ! has "WOULD CREATE - 'Troubleshoot'"; then
    pass "Check: nothing written; renames (playbook, Variables, workflow steps, map) and field changes listed; clash, two of one, Network, portal's own, portal_aap_names, not found said; green"
else fail "Check" "$(cat "$work/out.txt")"; fi

# 2. the run
run
if [ "$rc" -eq 0 ] && [ "$(field job_templates 100 name)" = "Health check" ] && [ "$(field job_templates 103 name)" = "ServiceNow health" ] \
   && [ "$(field job_templates 104 name)" = "ServiceNow health - nodes" ] && [ "$(field job_templates 102 name)" = "Troubleshoot" ] \
   && [ "$(field job_templates 101 name)" = "Investigate" ] && [ "$(field job_templates 105 name)" = "Net backup" ] \
   && [ "$(field job_templates 106 name)" = "Recheck STIG" ] && [ "$(field job_templates 107 name)" = "Cert report" ] \
   && [ "$(field job_templates 109 name)" = "Patch" ] && [ "$(field workflow_job_templates 200 name)" = "Service watch - approve or self-heal" ] \
   && [ "$(field workflow_job_templates 201 name)" = "Nightly checks" ] && [ "$(field workflow_job_templates 204 name)" = "Certificate watch" ] \
   && [ "$(field workflow_job_templates 205 name)" = "Our cert watch, and more" ] && [ "$(field job_templates 112 name)" = "Our posture" ] \
   && [ "$(field job_templates 113 name)" = "DB watch" ] \
   && [ "$(field workflow_job_templates 200 description)" = "kept as it is" ] && [ "$(field job_templates 100 description)" = "** Does: an older line of ours" ]; then
    pass "a run: renamed by playbook, by Variables, by steps, by the map; the rest left; descriptions kept"
else fail "the run" "$(cat "$work/out.txt")"; fi
if [ "$(field job_templates 100 inventory)" = "3" ] && [ "$(field job_templates 100 ask_limit_on_launch)" = "true" ] \
   && [ "$(field job_templates 100 credentials)" = "[15]" ] && [ "$(field job_templates 111 credentials)" = "[15, 13]" ] \
   && [ "$(field job_templates 105 inventory)" = "4" ] \
   && "$py" -c 'import json,sys; s=json.load(open(sys.argv[1])); t=next(t for t in s["job_templates"] if t["id"]==100)
names=set(l["name"] for l in s["labels"] if l["id"] in t["labels"]); sys.exit(0 if {"portal","portal-area:Linux"} <= names else 1)' "$work/state.json"; then
    pass "match: inventory, prompts, the missing credential kind added (none taken off), the portal labels; the Network one not matched"
else fail "match" "$(cat "$work/out.txt")"; fi
if "$py" - "$work/writes.jsonl" <<'PYEND'
import json, sys
w = [json.loads(l) for l in open(sys.argv[1])]
bad = [x for x in w if x["method"] == "DELETE" or (x["method"] == "PATCH" and "name" in x["body"] and len(x["body"]) > 1)
       or (x["method"] == "POST" and x["path"].startswith("job_templates/") and x["path"].count("/") == 1)
       or x.get("body", {}).get("disassociate")]
sys.exit(1 if bad or not w else 0)
PYEND
then pass "only renames (the name alone), field PATCHes, credentials and labels added - never a template made, a credential taken off or a DELETE"
else fail "the writes" "$(cat "$work/writes.jsonl")"; fi

# 3. the portal_aap_names one by the map: renamed, with the line to change
run -e '{"aap_templates_rename_map": {"Look at a host": "Troubleshoot", "My nightly": "Nightly checks", "Cert install check": "Our cert install check",
                                      "Patch": "Patch hosts"}}'
if [ "$rc" -eq 0 ] && [ "$(field job_templates 109 name)" = "Patch hosts" ] \
   && has "portal_aap_names: disk_space_request - change 'Patch' to 'Patch hosts'"; then
    pass "a template portal_aap_names names: renamed by the map, with the portal_aap_names line to change"
else fail "portal_aap_names" "$(cat "$work/out.txt")"; fi

# 4. again: already right, nothing written
n="$(writes)"
run
if [ "$rc" -eq 0 ] && [ "$(writes)" -eq "$n" ] && has "job already right: " && ! has "WOULD RENAME" && ! grep -q '"renamed - ' "$work/out.txt" \
   && ! has "not found - job 'Look at a host'"; then
    if [ "$(field job_templates 115 name)" = "Our cert install check" ]; then
        pass "a second run: already right, nothing written; the map's rename kept (never renamed to the catalog's after it)"
    else fail "second run: the map's rename undone" "$(renames)"; fi
else fail "second run" "$(renames)"; fi

# 5. a rename AAP refuses: FAILED, red; the others still renamed
"$py" - "$work/state.json" <<'PYEND'
import json, sys
s = json.load(open(sys.argv[1]))
s["fail_paths"] = ["job_templates/103/"]
for t in s["job_templates"]:
    if t["id"] == 102:
        t["name"] = "Look at a host"
    if t["id"] == 103:
        t["name"] = "SN health"
json.dump(s, open(sys.argv[1], "w"))
PYEND
run
if [ "$rc" -ne 0 ] && has "FAILED - job 'SN health' -> 'ServiceNow health' - HTTP 500" && [ "$(field job_templates 103 name)" = "SN health" ] \
   && [ "$(field job_templates 102 name)" = "Troubleshoot" ] && has "job template 'SN health': not renamed to 'ServiceNow health': HTTP 500"; then
    pass "a rename AAP refuses: FAILED, the job red; the others still renamed"
else fail "a refused rename" "$(renames)"; fi

# 6. off by default: nothing renamed; never a secret in the output
"$py" - "$work/state.json" <<'PYEND'
import json, sys
s = json.load(open(sys.argv[1]))
s["fail_paths"] = []
for t in s["job_templates"]:
    if t["id"] == 103:
        t["name"] = "SN health"
json.dump(s, open(sys.argv[1], "w"))
PYEND
run -e aap_templates_rename=false -e '{"aap_templates_rename_map": {}}' --check
if [ "$rc" -eq 0 ] && ! has "WOULD RENAME" && ! has "already right: " && ! grep -q "SECRET-PW\|setup-token" "$work/all-out.txt"; then
    pass "aap_templates_rename off (and no map): nothing renamed; no secret in the output"
else fail "off" "$(renames)"; fi

# 6b. (Codex) the map naming one template twice ("Old" and " Old "): red before any rename
n="$(writes)"
run -e '{"aap_templates_rename_map": {"Investigate": "A one", " Investigate ": "A two"}}'
if [ "$rc" -ne 0 ] && [ "$(writes)" -eq "$n" ] && has "'Investigate' named more than once"; then
    pass "a map naming one template twice (spaces at the ends): red, nothing renamed"
else fail "map twice" "$(renames)"; fi

# 7. aap_templates_create_missing: false - a catalog template not in AAP not made, counted; true (the default) makes it
run --check -e aap_templates_create_missing=false -e '{"aap_templates_only": ["Windows troubleshoot"]}'
if [ "$rc" -eq 0 ] && ! has "WOULD CREATE" && has "missing: 1" && has "only catalog templates not in AAP, not made"; then
    run --check -e '{"aap_templates_only": ["Windows troubleshoot"]}'
    if [ "$rc" -eq 0 ] && has "WOULD CREATE - 'Windows troubleshoot'"; then
        pass "aap_templates_create_missing: false - not made, counted in short; true (the default) - made"
    else fail "create missing true" "$(cat "$work/out.txt")"; fi
else fail "create missing false" "$(cat "$work/out.txt")"; fi

[ "$fails" -eq 0 ] && echo "all AAP rename checks passed" || echo "$fails AAP rename check(s) FAILED"
[ "$fails" -eq 0 ]
