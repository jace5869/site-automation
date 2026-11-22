#!/usr/bin/env bash
# 1.107.0: AAP - set up job templates makes every workflow of the catalog (aap/job_templates.yml), against a stand-in AAP
# that keeps what it is given (tests/portal_setup/fake_aap_crud.py). Checks: Check writes nothing and says what it would
# make; a workflow whose step's node gives Variables its template does not take (no survey question, no Prompt on launch)
# is skipped, said; a run makes every other workflow with each node, link, approval, Variables, Job type and Show changes
# as the catalog has it, its survey and its Variables, no portal label (the STIG jobs only); the site portal's own
# workflows are left to their job; with the templates' survey questions there every workflow is made; a second run is
# already right; a workflow changed by hand, or made by hand under the name, is said node by node and changed only with
# aap_templates_overwrite; a survey there is never changed; the site's own workflow with the same steps under another
# name is never made twice (aap_templates_create_alias makes it); aap_templates_create_missing: false makes none; a step
# that does not prompt for the Job type its node sets: skipped, said; never a DELETE but a workflow's own nodes.
#   bash tests/portal_setup/run_aap_catalog_workflows_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${AAP_CATALOG_WF_TEST_PORT:-18757}"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; [ -n "${2:-}" ] && printf '%s\n' "$2" | tail -30 | sed 's/^/      /'; fails=$((fails + 1)); }

"$py" - "$work/state.json" <<'PYEND'
import json, sys
kinds = [("machine", "Linux machine", "Machine"), ("machine_windows", "Windows machine", "Machine (Windows)"),
         ("aap", "AAP - jobs", "Red Hat Ansible Automation Platform"), ("stigman_api", "STIG Manager API", "STIG Manager API"),
         ("stigman_api_writer", "STIG Manager writer", "STIG Manager API (writer)"), ("stigman_db", "STIG Manager database", "STIG Manager database"),
         ("act_model_key", "ACT model key", "ACT model key"), ("servicenow", "ServiceNow API", "ServiceNow API"),
         ("vmware", "vCenter", "VMware vCenter"), ("horizon", "Horizon API", "Horizon API"), ("panos", "PAN-OS API", "Palo Alto PAN-OS API"),
         ("tls_certificate", "TLS certificate", "TLS certificate")]
creds = [{"id": 10 + i, "name": n, "organization": 1, "credential_type": 100 + i, "inputs": {"password": "SECRET-PW-%d" % i},
          "summary_fields": {"credential_type": {"id": 100 + i, "name": t}}} for i, (k, n, t) in enumerate(kinds)]
s = {"next": 1000, "organizations": [{"id": 1, "name": "Default"}],
     "projects": [{"id": 2, "name": "site-automation", "organization": 1}],
     "inventories": [{"id": 3, "name": "Site hosts", "organization": 1}],
     "execution_environments": [{"id": 5, "name": "All-in-one EE"}, {"id": 7, "name": "Windows EE"}],
     "credentials": creds, "workflow_job_templates": [], "nodes": [], "approvals": [], "schedules": [],
     "roles": [], "users": [], "labels": [],
     # the site's own Health check, with its own survey: one question, its default weekly (kept as it is)
     "job_templates": [{"id": 100, "name": "Health check", "organization": 1, "project": 2, "playbook": "playbooks/health_check.yml",
                        "inventory": 3, "ask_limit_on_launch": True, "ask_variables_on_launch": False, "ask_inventory_on_launch": False,
                        "ask_job_type_on_launch": False, "execution_environment": 5, "credentials": [10], "labels": [], "survey_enabled": True,
                        "description": "", "extra_vars": ""}],
     "surveys": {"job_templates/100": {"name": "", "description": "", "spec": [
         {"question_name": "Which checks?", "question_description": "", "variable": "health_checks", "type": "multiselect",
          "choices": ["daily", "weekly", "all", "disk", "mounts", "services", "selinux", "fapolicyd", "auditd", "accounts", "certs"],
          "default": "weekly", "required": True}]}}}
json.dump(s, open(sys.argv[1], "w"), indent=1)
json.dump({"aap_templates_inventory": "Site hosts", "aap_templates_ee": {"all": "All-in-one EE", "windows": "Windows EE"},
           "aap_templates_credentials": dict((k, n) for k, n, t in kinds), "aap_templates_description_owner": "Example setup"},
          open(sys.argv[1].replace("state.json", "vars.json"), "w"))
PYEND
FAKE_AAP_STATE="$work/state.json" FAKE_AAP_LOG="$work/writes.jsonl" FAKE_AAP_TOKEN=setup-token "$py" "$here/fake_aap_crud.py" "$port" &
pid=$!
sleep 1
: > "$work/all-out.txt"
run() {
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=setup-token ANSIBLE_NOCOLOR=1 ANSIBLE_SHOW_CUSTOM_STATS=1 \
        "$ap" -i localhost, playbooks/aap_templates_setup.yml -e "@$work/vars.json" "$@") > "$work/out.txt" 2>&1
    local rc=$?
    cat "$work/out.txt" >> "$work/all-out.txt"
    return $rc
}
st() { "$py" -c "import json,sys; s=json.load(open('$work/state.json')); print($1)"; }
setst() { "$py" -c "import json; s=json.load(open('$work/state.json')); $1; json.dump(s, open('$work/state.json','w'))"; }
writes() { [ -f "$work/writes.jsonl" ] && wc -l < "$work/writes.jsonl" || echo 0; }
has() { grep -qF -- "$1" "$work/out.txt"; }
# the Run's fingerprints (its artifact) as AAP's job history, for the next launch (job id $1)
seed() {
    "$py" - "$work/out.txt" "$work/state.json" "$1" <<'PYEND'
import json, sys
from pathlib import Path
out, state, jid = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
stats, _ = json.JSONDecoder().raw_decode(out.read_text().rsplit("RUN: ", 1)[1].lstrip())
s = json.loads(state.read_text())
s.setdefault("jobs", []).append({"id": jid, "job_template": 7, "status": "successful", "job_type": "run",
                                 "artifacts": {"aap_setup_fingerprints": stats["aap_setup_fingerprints"]}})
state.write_text(json.dumps(s))
PYEND
}
# every catalog workflow AAP has, node by node, against the catalog: [] when all are as the catalog says
compare() {
    "$py" - "$repo/aap/job_templates.yml" "$work/state.json" <<'PYEND'
import json, sys, yaml
cat = yaml.safe_load(open(sys.argv[1]))
s = json.load(open(sys.argv[2]))
jt = dict((j["id"], j["name"]) for j in s["job_templates"])
ap = dict((a["id"], a) for a in s["approvals"])
bad, made = [], 0
for w in cat["workflows"]:
    have = [x for x in s["workflow_job_templates"] if x["name"] == w["name"]]
    if not w.get("nodes") or not have:
        continue
    made += 1
    x = have[0]
    nodes = [n for n in s["nodes"] if n["wf"] == x["id"]]
    ident = dict((n["id"], n["identifier"]) for n in nodes)
    def norm(n):
        a = ap.get(n.get("approval"))
        return {"id": n["identifier"], "template": jt.get(n.get("unified_job_template"), ""),
                "approval": {"name": a["name"], "timeout": a["timeout"], "description": a["description"]} if a else None,
                "success": sorted(ident[i] for i in n["success"]), "failure": sorted(ident[i] for i in n["failure"]),
                "always": sorted(ident[i] for i in n["always"]), "extra_data": n.get("extra_data") or {},
                "job_type": n.get("job_type") or "", "diff_mode": bool(n.get("diff_mode"))}
    def want(n):
        return {"id": n["id"], "template": n.get("template", ""), "approval": n.get("approval"),
                "success": sorted(n.get("success") or []), "failure": sorted(n.get("failure") or []),
                "always": sorted(n.get("always") or []), "extra_data": n.get("extra_data") or {},
                "job_type": n.get("job_type") or "", "diff_mode": bool(n.get("diff_mode"))}
    g = sorted((norm(n) for n in nodes), key=lambda d: d["id"])
    c = sorted((want(n) for n in w["nodes"]), key=lambda d: d["id"])
    if g != c:
        bad.append("%s: nodes %s" % (w["name"], [a for a in g if a not in c][:2]))
    p = w.get("prompts") or {}
    if bool(x.get("ask_limit_on_launch")) != bool(p.get("limit")) or bool(x.get("ask_variables_on_launch")) != bool(p.get("variables")):
        bad.append("%s: prompts" % w["name"])
    ev = json.loads(x.get("extra_vars") or "{}")
    if ev != (w.get("extra_vars") or {}):
        bad.append("%s: Variables %s" % (w["name"], ev))
    sv = (s.get("surveys") or {}).get("workflow_job_templates/%d" % x["id"])
    # the questions (their choices may be the refresh's since)
    strip = lambda qs: [(q["question_name"], q["variable"], q["type"], bool(q.get("required"))) for q in qs or []]
    if sv and any(k not in q for q in sv.get("spec") or [] for k in ("question_name", "question_description", "default", "choices")):
        bad.append("%s: a survey question without the keys AAP needs" % w["name"])
    if w.get("survey") and (not x.get("survey_enabled") or strip((sv or {}).get("spec")) != strip(w["survey"])):
        bad.append("%s: survey" % w["name"])
    if not w.get("survey") and (sv or x.get("survey_enabled")):
        bad.append("%s: a survey it should not have" % w["name"])
    labels = [l["name"] for l in s.get("labels", []) if l["id"] in (x.get("label_ids") or [])]
    if bool(labels) != bool(w.get("portal")):
        bad.append("%s: labels %s" % (w["name"], labels))
print("%d made; %s" % (made, bad))
PYEND
}

# ---- Check: nothing written, what it would make; the survey questions the steps need, said - added to the site's own
# Health check (its question kept as it is), all of them on the templates it would make
before="$(writes)"
run --check; rc=$?
if [ $rc = 0 ] && [ "$(writes)" = "$before" ] \
    && has "WOULD CREATE - workflow 'VM snapshot cleanup (with approval)'" \
    && has "would add its survey: vm_snapshot_max_age_days, report_email_to" \
    && has "WOULD CREATE - workflow 'Network config load (workflow)'" \
    && has "would add its survey: net_config_change, net_config_load_mode, net_config_only, net_config_lines, net_config_restore_ref" \
    && has "node dry_run: 'Network config load (dry run)'; on success -> approve" \
    && has "node dry_run: 'ACT for Windows - install (dry run)'; on success -> approve" \
    && has "WOULD CREATE - 'Patch hosts (dry run)'" \
    && has "WOULD CREATE - workflow 'fapolicyd, with an approval'" \
    && has "node gate: 'Workflow gate - has a plan'; on success -> approve; its Variables: workflow_gate_var: fapolicyd_workflow_plan" \
    && has "would add survey question(s): use_act, site_act_level" \
    && has "would add its survey questions: health_checks" \
    && has "left to another job - workflow 'VM - deploy from ServiceNow ticket (workflow)' - left by hand for now" \
    && has "WOULD CREATE - workflow 'Daily health'" && has "WOULD CREATE - workflow 'Fix with approval (ACT)'" \
    && has "WOULD CREATE - workflow 'Windows patch with a dry run first'" \
    && ! has "SKIPPED - workflow" \
    && has "left to another job - workflow 'Disk - clean up (approved)' - AAP - set up portal actions makes it" \
    && ! has "WOULD CREATE - workflow 'STIG Manager updated, with an approval'" \
    && [ "$(grep -c "would label it" "$work/out.txt")" = 2 ] && has "label - workflow 'STIG - drift scan and upload' - would label it" ; then
    pass "Check: what it would make, node by node, the workflows' surveys, the template survey questions it would add; nothing skipped; nothing written"
else fail "Check" "rc $rc; $(writes) vs $before; $(grep -E "WOULD CREATE - workflow|SKIPPED - workflow|survey|left to another|fatal" "$work/out.txt" | head -30)"; fi
[ -n "${AAP_CATALOG_WF_KEEP:-}" ] && cp "$work/out.txt" "$AAP_CATALOG_WF_KEEP"

# ---- a run (the first: no history to read): every workflow, the missing survey questions added - none changed
run; rc=$?
got="$(compare)"
case "$rc|$got" in
    "0|40 made; []") pass "a run: all 40 workflows made, each node, link, approval, Variables, Job type, Show changes, prompt, survey as the catalog; no portal label but the STIG jobs'" ;;
    *) fail "the run" "rc $rc; $got; $(grep -E "SKIPPED - workflow|FAILED|fatal|survey" "$work/out.txt" | head -20)" ;;
esac
[ $rc = 0 ] && seed 51
hcs="$(st "[(q['variable'], q['default']) for k, v in s['surveys'].items() if k == 'job_templates/100' for q in v['spec']]")"
[ "$hcs" = "[('health_checks', 'weekly'), ('use_act', 'no'), ('site_act_level', 'explain')]" ] && [ "$(st "[j['survey_enabled'] for j in s['job_templates'] if j['id'] == 100]")" = "[True]" ] \
    && has "survey question(s) added: use_act, site_act_level" \
    && pass "... the site's Health check: its own question kept (default weekly), the two missing ones added, the survey on" \
    || fail "template survey add-only" "$hcs"
vmd="$(st "[j['ask_variables_on_launch'] for j in s['job_templates'] if j['name'] == 'VM - deploy from ServiceNow ticket']")"
[ "$vmd" = "[False]" ] \
    && pass "... VM - deploy from ServiceNow ticket: no Prompt on launch for Variables" || fail "VM deploy prompts" "$vmd"
dry="$(st "sorted((j['name'], j.get('job_type'), bool(j.get('diff_mode'))) for j in s['job_templates'] if j['name'].endswith('(dry run)'))")"
[ "$dry" = "[('ACT for Windows - install (dry run)', 'check', False), ('Network config load (dry run)', 'check', False), ('Patch hosts (dry run)', 'check', False), ('STIG Manager - deploy (dry run)', 'check', True), ('Windows patch (dry run)', 'check', False)]" ] \
    && pass "review: each dry run before an approval is a Check template itself (STIG Manager's with Show changes)" || fail "dry-run templates" "$dry"
ncw="$(st "[w.get('ask_variables_on_launch') for w in s['workflow_job_templates'] if w['name'] == 'Network config load (workflow)']")"
[ "$ncw" = "[False]" ] && pass "... Network config load (workflow): its survey, never Prompt on launch for Variables" || fail "net config prompts" "$ncw"
before="$(writes)"
run -e '{"awx_job_id": 52, "awx_job_template_id": 7}'; rc=$?
n_right="$(grep -c "already right - workflow '" "$work/out.txt")"
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && [ "$n_right" = 40 ] && pass "a second run: all 40 already right, nothing written" \
    || fail "second run" "rc $rc; $(writes) vs $before; $n_right right; $(grep -E "WOULD|updated|CHANGED|SKIPPED - workflow|fatal" "$work/out.txt" | head -10)"
[ $rc = 0 ] && seed 52

# ---- a survey there is the site's: never changed (the pick lists' refresh writes its choices)
setst "w = [x for x in s['workflow_job_templates'] if x['name'] == 'VM - delete picked snapshots (workflow)'][0];
s['surveys']['workflow_job_templates/%d' % w['id']]['spec'][0]['choices'] = ['snap1 [vc/vm-1/2]', 'snap2 [vc/vm-1/3]']"
before="$(writes)"
run -e '{"awx_job_id": 53, "awx_job_template_id": 7}'; rc=$?
ch="$(st "[x['spec'][0]['choices'] for k, x in s['surveys'].items() if k == 'workflow_job_templates/%d' % [w['id'] for w in s['workflow_job_templates'] if w['name'] == 'VM - delete picked snapshots (workflow)'][0]]")"
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "already right - workflow 'VM - delete picked snapshots (workflow)'" \
    && [ "$ch" = "[['snap1 [vc/vm-1/2]', 'snap2 [vc/vm-1/3]']]" ] && pass "a survey there (the refresh's choices): kept, the workflow already right" \
    || fail "survey kept" "rc $rc; $ch; $(grep -E "picked snapshots|fatal" "$work/out.txt" | head -5)"
[ $rc = 0 ] && seed 53

# ---- a survey taken off by hand: said, never put back without aap_templates_overwrite
setst "w = [x for x in s['workflow_job_templates'] if x['name'] == 'Horizon - image push (workflow)'][0]; w['survey_enabled'] = False;
s['surveys'].pop('workflow_job_templates/%d' % w['id'])"
before="$(writes)"
run -e '{"awx_job_id": 53, "awx_job_template_id": 7}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "it has no survey (taken off by hand?): left as it is - aap_templates_overwrite: true writes it" \
    && pass "a survey taken off by hand: said, not put back, nothing written" || fail "survey taken off" "rc $rc; $(writes) vs $before; $(grep -A3 "image push" "$work/out.txt" | head -6)"
run -e '{"awx_job_id": 53, "awx_job_template_id": 7, "aap_templates_overwrite": true}'; rc=$?
[ $rc = 0 ] && has "survey added: horizon_push_pools" && [ "$(st "[x.get('survey_enabled') for x in s['workflow_job_templates'] if x['name'] == 'Horizon - image push (workflow)']")" = "[True]" ] \
    && pass "... aap_templates_overwrite: true writes it again" || fail "survey overwrite" "rc $rc; $(grep -A3 "image push" "$work/out.txt" | head -6)"

# ---- changed by hand: said node by node; brought right only with aap_templates_overwrite
setst "w = [x for x in s['workflow_job_templates'] if x['name'] == 'Patch with a dry run first'][0];
[n.update(job_type='run') for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'dry_run']"
before="$(writes)"
run --check -e '{"awx_job_id": 54, "awx_job_template_id": 7}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "CHANGED BY HAND - workflow 'Patch with a dry run first'" \
    && has "node missing in AAP: 'Patch hosts (dry run)'; on success -> approval 'Install what the dry run listed?'" \
    && has "node in AAP, not the catalog's: 'Patch hosts (dry run)' as Run; on success -> approval 'Install what the dry run listed?'" \
    && pass "a workflow changed by hand: CHANGED BY HAND, node by node (the dry run made a Run), nothing written" \
    || fail "hand-edited" "rc $rc; $(grep -A6 "Patch with a dry run first'" "$work/out.txt" | head -12)"
run -e '{"awx_job_id": 54, "awx_job_template_id": 7}'; rc=$?
jt_now="$(st "[n.get('job_type') for w in s['workflow_job_templates'] if w['name'] == 'Patch with a dry run first' for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'dry_run']")"
[ $rc = 0 ] && [ "$jt_now" = "['run']" ] && pass "... a Run without aap_templates_overwrite: left as it is" || fail "left as it is" "rc $rc; $jt_now"
run -e '{"awx_job_id": 55, "awx_job_template_id": 7, "aap_templates_overwrite": true}'; rc=$?
jt_now="$(st "[n.get('job_type') for w in s['workflow_job_templates'] if w['name'] == 'Patch with a dry run first' for n in s['nodes'] if n['wf'] == w['id'] and n['identifier'] == 'dry_run']")"
got="$(compare)"
[ $rc = 0 ] && [ "$jt_now" = "[None]" ] && [ "$got" = "40 made; []" ] && pass "... aap_templates_overwrite: its nodes made again, as the catalog" \
    || fail "overwrite" "rc $rc; $jt_now; $got; $(grep -E "Patch with a dry run|FAILED|fatal" "$work/out.txt" | head -5)"
[ $rc = 0 ] && seed 55

# ---- made by hand under the catalog's name (no fingerprint): checked, said field by field, never changed without overwrite
setst "[j['artifacts']['aap_setup_fingerprints'].pop('1/workflow/Daily health', None) for j in s['jobs']]; s['workflow_job_templates'] = [x for x in s['workflow_job_templates'] if x['name'] != 'Daily health'];
s['workflow_job_templates'].append({'id': 900, 'name': 'Daily health', 'organization': 1, 'description': 'mine', 'ask_limit_on_launch': True,
    'ask_variables_on_launch': False, 'extra_vars': ''});
hc = [j['id'] for j in s['job_templates'] if j['name'] == 'Health check'][0]
tk = [j['id'] for j in s['job_templates'] if j['name'] == 'ServiceNow tickets'][0]
s['nodes'] += [{'id': 901, 'wf': 900, 'identifier': 'a1b2', 'converge': False, 'unified_job_template': hc, 'approval': None, 'success': [902],
                'failure': [], 'always': [], 'extra_data': {'health_checks': ['daily']}},
               {'id': 902, 'wf': 900, 'identifier': 'c3d4', 'converge': False, 'unified_job_template': tk, 'approval': None, 'success': [],
                'failure': [], 'always': [], 'extra_data': {}}]"
before="$(writes)"
run -e '{"awx_job_id": 56, "awx_job_template_id": 7}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "NOT MADE BY THIS JOB - workflow 'Daily health'" \
    && has "prompt for Limit: has true, should be false" \
    && has "node missing in AAP: 'Health check' (its Variables: health_checks: [daily]); always -> 'ServiceNow tickets'" \
    && has "node in AAP, not the catalog's: 'Health check' (its Variables: health_checks: [daily]); on success -> 'ServiceNow tickets'" \
    && [ "$(st "len([n for n in s['nodes'] if n['wf'] == 900])")" = 2 ] \
    && pass "a workflow made by hand under the name: NOT MADE BY THIS JOB, field by field and node by node, nothing written" \
    || { fail "hand-built" "rc $rc; $(writes) vs $before; $(grep -A8 "workflow 'Daily health'" "$work/out.txt" | head -12)"; }
[ $rc = 0 ] && seed 56
run -e '{"awx_job_id": 57, "awx_job_template_id": 7, "aap_templates_overwrite": true}'; rc=$?
got="$(compare)"
[ $rc = 0 ] && [ "$got" = "40 made; []" ] && has "updated - workflow 'Daily health'" \
    && pass "... aap_templates_overwrite: true takes it over - its nodes as the catalog's" || fail "take over" "rc $rc; $got"
[ $rc = 0 ] && seed 57

# ---- the site's own workflow with the same steps under another name: never made twice
setst "[x.update(name='Nightly health') for x in s['workflow_job_templates'] if x['id'] == 900]"
before="$(writes)"
run --check -e '{"awx_job_id": 58, "awx_job_template_id": 7}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] && has "same steps, other name - workflow 'Daily health' - 'Nightly health' has its steps - not made twice" \
    && ! has "WOULD CREATE - workflow 'Daily health'" && pass "the site's own workflow of the same steps (another name): not made twice, said" \
    || fail "same steps" "rc $rc; $(grep -E "Daily health" "$work/out.txt" | head -4)"
run --check -e '{"awx_job_id": 58, "awx_job_template_id": 7, "aap_templates_create_alias": true}'; rc=$?
[ $rc = 0 ] && has "WOULD CREATE - workflow 'Daily health'" && pass "... aap_templates_create_alias: true makes it beside it" \
    || fail "create_alias" "rc $rc; $(grep -E "Daily health" "$work/out.txt" | head -4)"
run --check -e '{"awx_job_id": 58, "awx_job_template_id": 7, "aap_templates_create_missing": false}'; rc=$?
[ $rc = 0 ] && has "not made - workflow 'Daily health' - not made (aap_templates_create_missing: false)" \
    && pass "aap_templates_create_missing: false - a missing workflow not made, said" || fail "create_missing" "$(grep -E "Daily health" "$work/out.txt" | head -4)"

# ---- review: a dry run before the approval that is not a Check template: the workflow skipped, said, never wired
setst "[j.update(job_type='run') for j in s['job_templates'] if j['name'] == 'Windows patch (dry run)'];
s['workflow_job_templates'] = [x for x in s['workflow_job_templates'] if x['name'] != 'Windows patch with a dry run first']"
before="$(writes)"
run -e '{"awx_job_id": 59, "awx_job_template_id": 7, "aap_templates_only": ["Windows patch with a dry run first"]}'; rc=$?
[ $rc = 0 ] && [ "$(writes)" = "$before" ] \
    && has "SKIPPED - workflow 'Windows patch with a dry run first' - 'Windows patch (dry run)' is a Run template: the dry run before the approval must be Job type Check on the template itself" \
    && pass "review: a dry run that is not a Check template - the workflow skipped (it would send before the approval), nothing written" \
    || fail "job type refused" "rc $rc; $(writes) vs $before; $(grep -E "Windows patch" "$work/out.txt" | head -4)"

# ---- review of 1.107.0 (Neon): each finding, against the stand-in AAP
del_wf() { setst "names = [$1]; ids = [w['id'] for w in s['workflow_job_templates'] if w['name'] in names];
s['workflow_job_templates'] = [w for w in s['workflow_job_templates'] if w['id'] not in ids]; s['nodes'] = [n for n in s['nodes'] if n['wf'] not in ids]"; }
# P1: a survey AAP refuses - said for that workflow (made, its survey not written), the next workflows still made, the
# fingerprints handed on, the job red
del_wf "'Certificate watch', 'STIG drift'"
nx="$(st "s['next'] + 1")"
setst "s['fail_paths'] = ['workflow_job_templates/$nx/survey_spec/']"
run -e '{"awx_job_id": 60, "awx_job_template_id": 7}'; rc=$?
setst "s.pop('fail_paths')"
[ $rc != 0 ] && has "FAILED - survey 'Certificate watch' - the workflow is there, its survey NOT written" && has "created - workflow 'STIG drift'" \
    && grep -q '"1/workflow/STIG drift"' "$work/out.txt" \
    && pass "review P1: a survey AAP refuses - said for that workflow; the next made; the fingerprints handed on; the job red" \
    || fail "survey refused" "rc $rc; $(grep -E "Certificate watch|STIG drift|fatal" "$work/out.txt" | head -6)"
[ $rc != 0 ] && seed 60
# P1: a step without the fixed Variables its node relies on (Horizon - desktops check in report mode) - skipped, said
del_wf "'Horizon - fix broken desktops (workflow)'"
setst "[j.update(extra_vars='horizon_desktops_mode: report\n') for j in s['job_templates'] if j['name'] == 'Horizon - desktops check']"
before="$(writes)"
run -e '{"awx_job_id": 61, "awx_job_template_id": 7, "aap_templates_only": ["Horizon - fix broken desktops (workflow)"]}'; rc=$?
[ "$(writes)" = "$before" ] && has "SKIPPED - workflow 'Horizon - fix broken desktops (workflow)' - 'Horizon - desktops check' lacks its fixed Variables horizon_desktops_mode: approval" \
    && pass "review P1: a step without its fixed Variables (the check in report mode) - the workflow skipped, said, nothing written" \
    || fail "fixed Variables" "rc $rc; $(grep -E "Horizon - fix|fatal" "$work/out.txt" | head -4)"
setst "[j.update(extra_vars='horizon_desktops_mode: approval\n') for j in s['job_templates'] if j['name'] == 'Horizon - desktops check']"
# P1: a read that fails for one workflow (its step's survey) - that one FAILED, the others go on
del_wf "'Security posture', 'VM - outage watch (workflow)'"
hc="$(st "[j['id'] for j in s['job_templates'] if j['name'] == 'Health check'][0]")"
setst "s['fail_get_paths'] = ['job_templates/$hc/survey_spec/']"
run -e '{"awx_job_id": 62, "awx_job_template_id": 7}'; rc=$?
setst "s.pop('fail_get_paths')"
[ $rc != 0 ] && has "FAILED - workflow 'Security posture'" && has "created - workflow 'VM - outage watch (workflow)'" \
    && grep -q '"1/workflow/VM - outage watch (workflow)"' "$work/out.txt" && has "survey added: check_certs_warn_days" \
    && pass "review P1: a read that fails for one workflow - it is FAILED, the others made, their fingerprints handed on; the survey refused before written now" \
    || fail "read error" "rc $rc; $(grep -E "Security posture|outage|fatal" "$work/out.txt" | head -6)"
[ $rc != 0 ] && seed 62
run -e '{"awx_job_id": 63, "awx_job_template_id": 7}'; rc=$?
got="$(compare)"
[ $rc = 0 ] && [ "${got#* made; }" = "[]" ] && has "already right - workflow 'VM - outage watch (workflow)'" \
    && has "created - workflow 'Security posture'" && has "already right - workflow 'Certificate watch'" \
    && pass "... the next run: the failed one made, the others already right (this job's)" || fail "after the read error" "rc $rc; $got; $(grep -E "Security posture|Certificate watch" "$work/out.txt" | head -6)"
[ $rc = 0 ] && seed 63
# P2: two catalog workflows of the same steps told apart by their Variables (mail relay add / remove)
del_wf "'Mail relay - add an IP', 'Mail relay - remove an IP'"
setst "s['workflow_job_templates'].append({'id': 950, 'name': 'Our relay add', 'organization': 1, 'description': '', 'extra_vars': 'mail_relay_action: add'});
t = dict((j['name'], j['id']) for j in s['job_templates'])
s['nodes'] += [{'id': 951, 'wf': 950, 'identifier': 'p', 'converge': False, 'unified_job_template': t['Mail relay - whitelist plan'], 'approval': None,
                'success': [952], 'failure': [], 'always': [], 'extra_data': {}},
               {'id': 952, 'wf': 950, 'identifier': 'a', 'converge': False, 'unified_job_template': None, 'approval': 953, 'success': [954],
                'failure': [], 'always': [], 'extra_data': {}},
               {'id': 954, 'wf': 950, 'identifier': 'x', 'converge': False, 'unified_job_template': t['Mail relay - apply the approved whitelist change'],
                'approval': None, 'success': [], 'failure': [], 'always': [], 'extra_data': {}}]
s['approvals'].append({'id': 953, 'name': 'ok?', 'description': '', 'timeout': 60})"
run --check -e '{"awx_job_id": 64, "awx_job_template_id": 7}'; rc=$?
[ $rc = 0 ] && has "same steps, other name - workflow 'Mail relay - add an IP' - 'Our relay add' has its steps" \
    && has "WOULD CREATE - workflow 'Mail relay - remove an IP'" \
    && pass "review P2: the site's add workflow blocks only the catalog's add, never the remove (their Variables tell them apart)" \
    || fail "same steps by Variables" "rc $rc; $(grep -E "Mail relay" "$work/out.txt" | head -4)"

# ---- Codex's last pass (each finding)
# a gate reused under its own name whose fixed Limit leaves out localhost: refused, its workflows never wired
del_wf "'Auditd tuning, with an approval'"
setst "[j.update(limit='all') for j in s['job_templates'] if j['name'] == 'Workflow gate - has a plan']"
before="$(writes)"
run -e '{"awx_job_id": 70, "awx_job_template_id": 7, "aap_templates_only": ["Auditd tuning, with an approval", "Workflow gate - has a plan"]}'; rc=$?
[ "$(writes)" = "$before" ] && has "'Workflow gate - has a plan' has its own Limit 'all', which leaves out localhost" \
    && ! has "created - workflow 'Auditd tuning, with an approval'" \
    && pass "codex: a gate whose own Limit leaves out localhost - refused, the workflow not made" \
    || fail "gate limit" "rc $rc; $(grep -E "Auditd|gate|fatal" "$work/out.txt" | head -5)"
setst "[j.update(limit='') for j in s['job_templates'] if j['name'] == 'Workflow gate - has a plan']"
# a node's answer the template's own question does not offer (Health check without 'mounts'): skipped, the question kept
del_wf "'Patch with checks'"
setst "s['surveys']['job_templates/100']['spec'][0]['choices'] = ['daily', 'weekly', 'all', 'disk', 'services', 'selinux', 'fapolicyd', 'auditd', 'accounts', 'certs']"
before="$(writes)"
run -e '{"awx_job_id": 71, "awx_job_template_id": 7, "aap_templates_only": ["Patch with checks"]}'; rc=$?
[ "$(writes)" = "$before" ] && has "SKIPPED - workflow 'Patch with checks' - 'Health check''s survey question health_checks does not offer mounts" \
    && [ "$(st "'mounts' in s['surveys']['job_templates/100']['spec'][0]['choices']")" = False ] \
    && pass "codex: a node's answer its template's question does not offer - skipped, said, the question never changed" \
    || fail "answer choices" "rc $rc; $(grep -E "Patch with checks|fatal" "$work/out.txt" | head -4)"
# a template's survey its owner switched off (every question there): left off without overwrite, its workflows said
del_wf "'Investigate a host'"
setst "[j.update(survey_enabled=False) for j in s['job_templates'] if j['name'] == 'Troubleshoot']"
before="$(writes)"
run -e '{"awx_job_id": 72, "awx_job_template_id": 7, "aap_templates_only": ["Troubleshoot", "Investigate a host"]}'; rc=$?
[ "$(writes)" = "$before" ] && [ "$(st "[j['survey_enabled'] for j in s['job_templates'] if j['name'] == 'Troubleshoot']")" = "[False]" ] \
    && has "its survey is off: left off" && has "SKIPPED - workflow 'Investigate a host'" \
    && pass "codex: a survey its owner switched off - left off without overwrite, said; its workflow skipped" \
    || fail "survey left off" "rc $rc; $(grep -E "Troubleshoot|Investigate|survey|fatal" "$work/out.txt" | head -6)"
# a template of the catalog's name from another project: refused as a step, and no catalog survey questions on it
setst "s['projects'].append({'id': 6, 'name': 'Old project', 'organization': 1});
[j.update(project=6, survey_enabled=False) for j in s['job_templates'] if j['name'] == 'Troubleshoot'];
tid = [j['id'] for j in s['job_templates'] if j['name'] == 'Troubleshoot'][0]; s['surveys'].pop('job_templates/%d' % tid, None)"
before="$(writes)"
run -e '{"awx_job_id": 73, "awx_job_template_id": 7, "aap_templates_only": ["Troubleshoot", "Investigate a host"]}'; rc=$?
tsv="$(st "[s.get('surveys', {}).get('job_templates/%d' % j['id']) for j in s['job_templates'] if j['name'] == 'Troubleshoot']")"
[ "$(writes)" = "$before" ] && [ "$tsv" = "[None]" ] && has "no survey questions written: it runs playbooks/troubleshoot.yml from another project" \
    && pass "codex: a template of the catalog's name from another project - no catalog survey questions put on it" \
    || fail "refused template survey" "rc $rc; $tsv; $(grep -E "Troubleshoot|survey|fatal" "$work/out.txt" | head -6)"
setst "[j.update(project=2) for j in s['job_templates'] if j['name'] == 'Troubleshoot']"
# a workflow there whose survey would override what its steps fix: never wired, its survey never edited
setst "w = [x for x in s['workflow_job_templates'] if x['name'] == 'fapolicyd, with an approval'][0];
w['survey_enabled'] = True;
s['surveys']['workflow_job_templates/%d' % w['id']] = {'name': '', 'description': '', 'spec': [
    {'question_name': 'Gate variable', 'question_description': '', 'variable': 'workflow_gate_var',
     'type': 'text', 'required': False, 'default': '', 'choices': ''}]}"
before="$(writes)"
run -e '{"awx_job_id": 74, "awx_job_template_id": 7, "aap_templates_only": ["fapolicyd, with an approval"], "aap_templates_overwrite": true}'; rc=$?
[ "$(writes)" = "$before" ] && has "SKIPPED - workflow 'fapolicyd, with an approval' - its survey asks workflow_gate_var, and a workflow's answer would override what its steps fix" \
    && pass "codex: a workflow survey that would override a step's fixed gate variable - not wired even with overwrite, its survey never edited" \
    || fail "survey collision" "rc $rc; $(grep -E "fapolicyd|fatal" "$work/out.txt" | head -4)"

# ---- Codex's narrow check of c1973d1f
# the workflow's own fixed Variables (mail_relay_action: add) are protected from its survey too
setst "s['workflow_job_templates'] = [x for x in s['workflow_job_templates'] if x['name'] != 'Our relay add']"
run -e '{"awx_job_id": 79, "awx_job_template_id": 7, "aap_templates_only": ["Mail relay - remove an IP"]}' >/dev/null; seed 79
setst "w = [x for x in s['workflow_job_templates'] if x['name'] == 'Mail relay - remove an IP'][0];
s['surveys']['workflow_job_templates/%d' % w['id']]['spec'].append(
    {'question_name': 'Action', 'question_description': '', 'variable': 'mail_relay_action', 'type': 'text', 'required': False, 'default': '', 'choices': ''})"
before="$(writes)"
run -e '{"awx_job_id": 80, "awx_job_template_id": 7, "aap_templates_only": ["Mail relay - remove an IP"], "aap_templates_overwrite": true}'; rc=$?
[ "$(writes)" = "$before" ] && has "SKIPPED - workflow 'Mail relay - remove an IP' - its survey asks mail_relay_action, and a workflow's answer would override what its steps fix" \
    && pass "codex: a workflow survey asking the workflow's own fixed Variable (mail_relay_action) - not wired, even with overwrite" \
    || fail "workflow vars collision" "rc $rc; $(grep -E "Mail relay - remove an IP|fatal" "$work/out.txt" | head -4)"
# a node's list answer to a single-choice question; choices checked with Prompt on launch for Variables too
del_wf "'Weekly compliance'"
setst "q = s['surveys']['job_templates/100']['spec'][0]; q['type'] = 'multiplechoice'; q['choices'] = ['daily', 'weekly'];
[j.update(ask_variables_on_launch=True) for j in s['job_templates'] if j['id'] == 100]"
before="$(writes)"
run -e '{"awx_job_id": 81, "awx_job_template_id": 7, "aap_templates_only": ["Weekly compliance"]}'; rc=$?
[ "$(writes)" = "$before" ] && has "SKIPPED - workflow 'Weekly compliance' - 'Health check''s survey question health_checks takes one choice" \
    && pass "codex: a list answer to a single-choice question - skipped before any write, Variables prompted or not" \
    || fail "answer type" "rc $rc; $(grep -E "Weekly compliance|fatal" "$work/out.txt" | head -4)"
setst "q = s['surveys']['job_templates/100']['spec'][0]; q['type'] = 'multiselect';
q['choices'] = ['daily', 'weekly', 'all', 'disk', 'mounts', 'services', 'selinux', 'fapolicyd', 'auditd', 'accounts', 'certs'];
[j.update(ask_variables_on_launch=False) for j in s['job_templates'] if j['id'] == 100]"
# the step after the approval is a Check template: skipped (it would apply nothing and end green)
del_wf "'Patch with a dry run first'"
setst "[j.update(job_type='check') for j in s['job_templates'] if j['name'] == 'Patch hosts']"
before="$(writes)"
run -e '{"awx_job_id": 82, "awx_job_template_id": 7, "aap_templates_only": ["Patch with a dry run first"]}'; rc=$?
[ "$(writes)" = "$before" ] && has "SKIPPED - workflow 'Patch with a dry run first' - 'Patch hosts' is a Check template: the step must be Job type Run" \
    && pass "codex: the step after the approval is a Check template - skipped, said" \
    || fail "apply step Check" "rc $rc; $(grep -E "Patch with a dry run|fatal" "$work/out.txt" | head -4)"
setst "[j.update(job_type='run') for j in s['job_templates'] if j['name'] == 'Patch hosts']"
# a foreign same-name template partly matched (its prompts): still no catalog survey questions on it
setst "[j.update(project=6, survey_enabled=False, ask_limit_on_launch=False) for j in s['job_templates'] if j['name'] == 'Troubleshoot'];
tid = [j['id'] for j in s['job_templates'] if j['name'] == 'Troubleshoot'][0]; s['surveys'].pop('job_templates/%d' % tid, None)"
before="$(writes)"
run -e '{"awx_job_id": 83, "awx_job_template_id": 7, "aap_templates_only": ["Troubleshoot"], "aap_templates_match": ["prompts"]}'; rc=$?
tsv="$(st "[s.get('surveys', {}).get('job_templates/%d' % j['id']) for j in s['job_templates'] if j['name'] == 'Troubleshoot']")"
[ "$tsv" = "[None]" ] && has "no survey questions written: it runs playbooks/troubleshoot.yml from another project" \
    && pass "codex: a foreign same-name template whose prompts were matched - still no catalog survey questions on it" \
    || fail "partial match identity" "rc $rc; $tsv; $(grep -E "Troubleshoot|fatal" "$work/out.txt" | head -6)"
setst "[j.update(project=2) for j in s['job_templates'] if j['name'] == 'Troubleshoot']"

grep '"DELETE"' "$work/writes.jsonl" | grep -qv 'workflow_job_template_nodes/' && fail "a DELETE" "$(grep DELETE "$work/writes.jsonl" | grep -v workflow_job_template_nodes/ | head -2)" \
    || pass "never a DELETE of a template or workflow (only a workflow's own nodes, made again)"
grep -q "SECRET-PW" "$work/all-out.txt" "$work/writes.jsonl" && fail "a secret" "$(grep -n SECRET-PW "$work/all-out.txt" | head -2)" \
    || pass "no secret in any output or write"

echo
if [ $fails = 0 ]; then echo "all AAP catalog workflow scenarios passed"; else echo "$fails AAP catalog workflow scenario(s) failed"; exit 1; fi
