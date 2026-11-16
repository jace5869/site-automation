#!/usr/bin/env bash
# The site portal's STIG actions (1.67.0, workflows 46-48) on this box as a Linux host (a plain user, no sudo): a fake
# AAP (the report job with its site_findings artifact), a fake ACT (tests/containers/fake_act.py), a fake oscap
# (tests/stig/make_results.py) and the stand-in Keycloak + STIG Manager (tests/stig/stigman_mock.py, with the rules'
# check and fix text). Each workflow is run step by step as AAP would, the artifacts of one step the next one's extra
# variables. Checks: 47 - ACT's proposal and NEEDS APPROVAL; refused with no ACT asked: CAT I, a rule about audit, a
# fix that restarts; a proposed command about PAM refused; the approval's dry run lists the commands and changes nothing;
# the fix runs exactly them; a plan for another finding is refused; the recheck of that rule alone (1.68.0: also with an
# Evaluate-STIG run in full, which answers every rule - only the rule asked is planned) - passes: its review
# planned and written (fixed), still fails: nothing planned (still there). 46 - passes now, still Open: planned and
# written. 48 - a host with no asset: planned with the scan's answers. Only the approved review reaches STIG Manager.
#   bash tests/portal/run_portal_stig_test.sh            (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
stig="$repo/tests/stig"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${PORTAL_STIG_TEST_PORT:-18740}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/bin" "$work/act" "$work/content"
"$py" "$stig/make_results.py" "$work/content/ssg-test-ds.xml" '{}'
echo '{}' > "$work/results.json"
cat > "$work/bin/oscap" <<EOF
#!/bin/bash
echo "\$*" > "$work/oscap_args.txt"
res=""
while [ \$# -gt 0 ]; do case "\$1" in --results) res="\$2"; shift ;; esac; shift; done
"$py" "$stig/make_results.py" "\$res" "\$(cat "$work/results.json")" || exit 1
grep -q '"fail"' "$work/results.json" && exit 2 || exit 0
EOF
chmod +x "$work/bin/oscap"

# ---- STIG Manager: the asset localhost; SV-257883 (the session timeout, CAT II) and SV-258176 (audit, CAT II) Open,
# SV-257835 (telnet, CAT I) Open; the rules' check and fix text
sm_data() {  # sm_data ASSETS_JSON
    "$py" - "$1" > "$work/sm.json" <<'PY'
import json, sys
def rv(rule, result, ver, sev, title):
    return {"ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": "submitted"}, "ts": "2026-11-10T10:00:00Z",
            "rule": {"ruleId": rule, "version": ver, "severity": sev, "title": title}}
T = {"SV-257883r1044925_rule": ("RHEL-09-412035", "medium", "RHEL 9 must automatically exit interactive command shell user sessions after 10 minutes of inactivity.",
                                "$ sudo grep -i tmout /etc/profile /etc/profile.d/*.sh", "Add to /etc/profile.d/tmout.sh:\n\nTMOUT=600\nreadonly TMOUT\nexport TMOUT"),
     "SV-258176r1045388_rule": ("RHEL-09-654225", "medium", "RHEL 9 must audit all uses of privileged commands.",
                                "$ sudo auditctl -l | grep privileged", "Add to /etc/audit/rules.d/audit.rules: -a always,exit -F perm=x ..."),
     "SV-257835r1044874_rule": ("RHEL-09-215040", "high", "RHEL 9 must not have the telnet-server package installed.",
                                "$ sudo dnf list --installed telnet-server", "$ sudo dnf remove telnet-server")}
reviews = [rv(r, "fail", v, s, t) for r, (v, s, t, c, f) in T.items()]
rules = [{"ruleId": r, "groupId": "V-" + r[3:9], "version": v, "severity": s, "title": t, "check": c, "fix": f} for r, (v, s, t, c, f) in T.items()]
print(json.dumps({"secret": "s3cret", "client": "autostig", "writers": ["autostig"], "managers": ["autostig"],
                  "collections": [{"collectionId": "1", "name": "RHEL Servers"}], "assets": json.loads(sys.argv[1]),
                  "reviews": {"11": reviews}, "rules": {"RHEL_9_STIG": rules}}))
PY
}
with_asset='{"1": [{"assetId": "11", "name": "localhost", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R7"}]}]}'
sm_data "$with_asset"
"$py" "$stig/stigman_mock.py" "$((port + 1))" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & pids+=($!)

# ---- AAP: job 4711, a STIG drift report run, its site_findings for localhost
cat > "$work/aap.py" <<'EOPY'
import http.server, json, sys
port = int(sys.argv[1])
def f(kind, rule, cat, title, extra=None):
    d = {"rule": rule, "stigid": "", "cat": cat, "title": title, "stig": "RHEL_9_STIG", "group": "drift", "stigman": "Open",
         "collection": "RHEL Servers", "asset": "localhost", "os": "linux"}
    d.update(extra or {})
    return {"id": "%s:localhost:%s" % (kind, rule), "kind": kind, "host": "localhost", "title": title, "severity": "warning", "data": d}
FIND = [f("stig_rule_fail", "SV-257883r1044925_rule", "CAT II", "RHEL 9 must automatically exit interactive sessions after 10 minutes."),
        f("stig_rule_fail", "SV-258176r1045388_rule", "CAT II", "RHEL 9 must audit all uses of privileged commands."),
        f("stig_rule_fail", "SV-257835r1044874_rule", "CAT I", "RHEL 9 must not have the telnet-server package installed."),
        f("stig_fixed_open", "SV-257883r1044925_rule", "CAT II", "RHEL 9 must automatically exit interactive sessions after 10 minutes."),
        {"id": "stig_no_asset:localhost:RHEL_9_STIG", "kind": "stig_no_asset", "host": "localhost", "title": "localhost is not in STIG Manager",
         "severity": "warning", "data": {"collection": "RHEL Servers", "asset": "localhost", "stig": "RHEL_9_STIG", "why": "no asset", "os": "linux"}}]
JOB = {"id": 4711, "name": "STIG drift report", "status": "successful", "playbook": "playbooks/stig_drift_report.yml", "finished": "2026-11-16T06:00:00Z",
       "artifacts": {"site_findings": {"localhost": FIND}}}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def answer(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path.endswith("/me/"):
            return self.answer(200, {"results": [{"username": "portal-reader"}]})
        if self.path.endswith("/jobs/4711/"):
            return self.answer(200, JOB)
        return self.answer(404, {"detail": "Not found."})
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
EOPY
"$py" "$work/aap.py" "$port" & pids+=($!)
sleep 1

cat > "$work/settings.yml" <<EOF
check_stig_oscap: $work/bin/oscap
check_stig_datastream: $work/content/ssg-test-ds.xml
check_stig_dir: $work/kept
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_stigman_api: http://127.0.0.1:$((port + 1))/api
check_stig_stigman_token_url: http://127.0.0.1:$((port + 1))/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
act_triage_script_src: $repo/tests/containers/fake_act.py
act_triage_poll: 1
act_triage_dest_dir: $work/actdir
site_act_url: http://127.0.0.1:$port/v1/chat/completions
portal_requested_by: CN=Pat Example
report_job: "4711"
target: localhost
EOF
act() { printf '%s' "$1" > "$work/act/act.json"; rm -f "$work/act/fake-act-calls.log"; }

# run NAME ok|fail PLAYBOOK [args...] -- TEXT...   (TEXT starting with ! must not be there); the run's custom stats in $work/stats.json
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" GENAI_KEY=test-key \
        CONTROLLER_HOST="http://127.0.0.1:$port" CONTROLLER_OAUTH_TOKEN=made-up-token ANSIBLE_SHOW_CUSTOM_STATS=1 \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local -l localhost "playbooks/$pb" -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4800}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    [ -n "${PORTAL_STIG_KEEP:-}" ] && { mkdir -p "$PORTAL_STIG_KEEP"; cp "$work/out.txt" "$PORTAL_STIG_KEEP/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-60).txt"; }
    "$py" - "$work/out.txt" > "$work/stats.json" <<'PY'
import json, sys
for line in open(sys.argv[1]):
    if line.strip().startswith("RUN: "):
        print(line.strip()[5:]); break
else:
    print("{}")
PY
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -30 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "fatal\|ERROR\|msg" "$work/out.txt" | cut -c1-300 | head -20; fails=$((fails + 1)); fi
}
stat() { "$py" -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(eval(sys.argv[2], {"d": d})))' "$work/stats.json" "$1"; }
check() { if [ "$(stat "$1")" = "$2" ]; then echo "ok   - $3"; else echo "FAIL - $3: $(stat "$1") (wanted $2)"; fails=$((fails + 1)); fi; }

F_TMOUT=stig_rule_fail:localhost:SV-257883r1044925_rule
fix="printf 'TMOUT=600\\\\n' > $work/tmout.sh"

# ---- 47 step 1: the proposal
act "{\"propose\": [\"$fix\"], \"summary\": \"TMOUT is not set. Fix it, then check with: grep TMOUT $work/tmout.sh\"}"
run "47 check: ACT proposes the commands, NEEDS APPROVAL" fail stig_fix_rule.yml -e finding_id=$F_TMOUT -- \
    "NEEDS APPROVAL - fix SV-257883r1044925_rule (II" "ACT proposes: $fix" "!Traceback"
check 'd.get("stig_fix_plan", {}).get("commands")' "[\"$fix\"]" "47 check: the plan handed on is ACT's command"
cp "$work/stats.json" "$work/approved.json"
grep -q "TMOUT=600" "$work/act/fake-act-task.txt" && grep -q "readonly TMOUT" "$work/act/fake-act-task.txt" \
  && echo "ok   - 47 check: ACT was given DISA's fix text from STIG Manager" || { echo "FAIL - 47 check: the fix text did not reach ACT"; fails=$((fails + 1)); }

# ---- 47 step 1: refused, ACT not asked
act '{"propose": ["true"]}'
run "47 check: a CAT I rule is refused" ok stig_fix_rule.yml -e finding_id=stig_rule_fail:localhost:SV-257835r1044874_rule -- \
    "it is CAT I: only CAT II and III rules are fixed this way" "Nothing was changed"
run "47 check: a rule about audit is refused" ok stig_fix_rule.yml -e finding_id=stig_rule_fail:localhost:SV-258176r1045388_rule -- \
    "it is about audit configuration, which a person changes by hand"
[ -e "$work/act/fake-act-calls.log" ] && { echo "FAIL - ACT was asked for a refused rule"; fails=$((fails + 1)); } || echo "ok   - ACT not asked for a refused rule"
check 'd.get("portal_result", {}).get("fixed")' '"unknown"' "a refusal: the job page says not checked, nothing changed"
act '{"propose": ["sed -i s/x/y/ /etc/pam.d/system-auth"]}'
run "47 check: a proposed command about PAM is refused" ok stig_fix_rule.yml -e finding_id=$F_TMOUT -- \
    "a proposed command is about PAM" "!NEEDS APPROVAL - "
act '{"propose": ["true"]}'
run "47 check: ACT off (site_act_enabled false) - no proposal, green, nothing asked" ok stig_fix_rule.yml -e finding_id=$F_TMOUT \
    -e site_act_enabled=false -- "ACT proposes no command" "ACT is off" "!NEEDS APPROVAL - "
[ -e "$work/act/fake-act-calls.log" ] && { echo "FAIL - ACT was asked with ACT off"; fails=$((fails + 1)); } || echo "ok   - ACT not asked with ACT off"
run "47 check: asking for self-heal is refused" fail stig_fix_rule.yml -e finding_id=$F_TMOUT -e site_act_level=self-heal -- \
    "This step only proposes"

# ---- 47 step 3: the approved fix
approved="$(cat "$work/approved.json")"
run "47 apply, dry run: lists the commands, changes nothing" ok stig_fix_apply.yml -e finding_id=$F_TMOUT -e "$approved" --check -- \
    "DRY RUN on localhost: approved for SV-257883r1044925_rule, and would run: $fix"
[ -e "$work/tmout.sh" ] && { echo "FAIL - the dry run changed the host"; fails=$((fails + 1)); } || echo "ok   - the dry run changed nothing"
run "47 apply: a plan for another finding is refused" fail stig_fix_apply.yml -e finding_id=stig_rule_fail:localhost:SV-258176r1045388_rule -e "$approved" -- \
    "Nothing to apply"
run "47 apply: exactly the approved command runs" ok stig_fix_apply.yml -e finding_id=$F_TMOUT -e "$approved" -- "### exit 0"
grep -q "TMOUT=600" "$work/tmout.sh" 2>/dev/null && echo "ok   - 47 apply: the fix is on the host" || { echo "FAIL - 47 apply: no fix on the host"; fails=$((fails + 1)); }

# ---- 47 step 4: the recheck of that rule alone
echo '{"accounts_tmout": "fail"}' > "$work/results.json"
run "47 recheck: still fails - nothing planned (still there)" ok stig_finding_recheck.yml -e finding_id=$F_TMOUT -- "!NEEDS APPROVAL - "
check 'd.get("portal_result", {}).get("fixed")' '"false"' "47 recheck: the job page says still there"
check '"stigman_update_plan" in d' 'false' "47 recheck: no review planned when it still fails"
echo '{}' > "$work/results.json"
run "47 recheck: passes now - its review planned (fixed)" fail stig_finding_recheck.yml -e finding_id=$F_TMOUT -- \
    "NEEDS APPROVAL - 1 review(s)"
check 'd.get("portal_result", {}).get("fixed")' '"true"' "47 recheck: the job page says fixed"
check '[p["ruleId"] for p in d["stigman_update_plan"]]' '["SV-257883r1044925_rule"]' "47 recheck: only that rule is planned"
grep -q -- "--rule xccdf_org.ssgproject.content_rule_accounts_tmout" "$work/oscap_args.txt" && ! grep -q "audit_rules" "$work/oscap_args.txt" \
  && echo "ok   - 47 recheck: oscap checked that rule alone" || { echo "FAIL - 47 recheck: other rules were checked"; fails=$((fails + 1)); }
plan="$(cat "$work/stats.json")"
# 1.68.0 (Codex P1): the same recheck with an Evaluate-STIG that has no --SelectVuln - it scans in full, and its checklist
# answers every rule: SV-258176 and SV-257835, Open in STIG Manager, pass in it too. Still only the rule asked is
# compared and planned - never another rule's review on this approval
mkdir -p "$work/es_full"
cat > "$work/es_full/Evaluate-STIG_Bash.sh" <<ESEOF
#!/bin/bash
# Evaluate-STIG_Bash.sh --Output CKLB --OutputPath DIR ... (an old one: no option to pick rules): every rule, DIR/HOST/Checklist/HOST_RHEL9.cklb
out=""; echo "\$*" > "$work/es_full_args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; esac; shift; done
mkdir -p "\$out/LOCALHOST/Checklist" && "$py" "$stig/make_cklb.py" "\$out/LOCALHOST/Checklist/LOCALHOST_RHEL9_V2R7.cklb" '{}' V2R7
echo "Done"
ESEOF
chmod +x "$work/es_full/Evaluate-STIG_Bash.sh"
es_full="{\"check_stig_scanner\": \"evaluate_stig\", \"check_stig_evaluate\": \"$work/es_full\", \"check_stig_evaluate_select\": {\"$major\": \"RHEL9\"}}"
run "47 recheck, Evaluate-STIG in full (no --SelectVuln, every rule passes): only the rule asked is planned" fail stig_finding_recheck.yml \
    -e finding_id=$F_TMOUT -e "$es_full" -- "NEEDS APPROVAL - 1 review(s)" "checked: in full - this Evaluate-STIG has no -SelectVuln"
check '[p["ruleId"] for p in d["stigman_update_plan"]]' '["SV-257883r1044925_rule"]' "47 recheck in full: SV-258176 and SV-257835 pass in the scan, and are not planned"
grep -q "SelectVuln" "$work/es_full_args.txt" && { echo "FAIL - 47 recheck in full: --SelectVuln given to an Evaluate-STIG without it"; fails=$((fails + 1)); }
: > "$work/sm.log.writes"
run "47 update: the review is written (the same approval)" ok stigman_update.yml -e finding_id=$F_TMOUT -e "$plan" -- "!Traceback"
grep -q "SV-257883r1044925_rule" "$work/sm.log.writes" && ! grep -q "SV-258176\|SV-257835" "$work/sm.log.writes" \
  && echo "ok   - 47 update: only that review reached STIG Manager" || { echo "FAIL - 47 update: $(cat "$work/sm.log.writes" | head -c 300)"; fails=$((fails + 1)); }

# ---- 46: passes now, still Open
sm_data "$with_asset"
run "46 recheck: passes now and still Open - planned" fail stig_finding_recheck.yml -e finding_id=stig_fixed_open:localhost:SV-257883r1044925_rule -- \
    "NEEDS APPROVAL - 1 review(s)"
check 'd.get("portal_result", {}).get("fixed")' '"true"' "46: the job page says fixed (passes now)"

# ---- 48: no asset
sm_data '{"1": []}'
run "48 recheck: no asset - the asset and the scan's answers planned" fail stig_finding_recheck.yml -e finding_id=stig_no_asset:localhost:RHEL_9_STIG -- \
    "NEW ASSET localhost in RHEL Servers"
check 'd.get("portal_result", {}).get("after")' '"planned"' "48: the job page says planned"

# 1.68.0 (Codex P1): "fix one rule" (47) on a host whose asset is gone, with asset creation on for the site
# (check_stig_update_create_assets in all.yml - host facts win): never an asset made, nor every rule scanned and planned
# on that approval - not rechecked (a warning, as any host without its asset), nothing planned
run "47 recheck, the asset gone, check_stig_update_create_assets on for the site: not rechecked, nothing planned" fail stig_finding_recheck.yml \
    -e finding_id=$F_TMOUT -e '{"check_stig_update_create_assets": true, "check_stig_stigman_collection": "RHEL Servers"}' -- \
    "!NEEDS APPROVAL - " "!NEW ASSET" "not rechecked - what to recheck is read from STIG Manager: no asset named localhost"
check '"stigman_update_plan" in d' 'false' "47 recheck, the asset gone: no plan (no asset made, no other rule's review)"

run "a finding of another kind is refused" fail stig_fix_rule.yml -e finding_id=stig_fixed_open:localhost:SV-257883r1044925_rule -- "!NEEDS APPROVAL - "

echo
if [ "$fails" -eq 0 ]; then echo "ok   - all STIG action scenarios passed"; exit 0; fi
echo "FAIL - $fails STIG action scenario(s) failed"; exit 1
