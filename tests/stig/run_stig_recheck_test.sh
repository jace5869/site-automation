#!/usr/bin/env bash
# The STIG recheck (playbooks/stig_recheck.yml, roles/check_stig tasks/recheck.yml) on this box, as a plain user (no
# sudo), with a fake oscap that writes the results file tests/stig/make_results.py makes and the stand-in Keycloak +
# STIG Manager (tests/stig/stigman_mock.py, read-only, with the STIG revision's rules). In order: OpenSCAP - only the
# rules STIG Manager has Open or Not Reviewed checked (--rule), each sorted (fixed, answered, still open, by hand), report
# only, nothing kept with the drift report's scans, oscap's results kept as recheck/latest-results.xml; the email; Check
# mode keeps nothing; check_stig_recheck: [open]; nothing Open or Not Reviewed; the asset not there (a warning); STIG
# Manager not set; check_stig_recheck checked. Then Evaluate-STIG: with --SelectVuln (its checklist kept), one without
# it (run in full), Evaluate-STIG.ps1 with -SelectVuln as one array (pwsh, when there); only GET requests reached STIG
# Manager.
#   bash tests/stig/run_stig_recheck_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
smtp_pid="" sm_pid=""
cleanup() { [ -n "${STIG_KEEP:-}" ] && cp "$work/out.txt" "$STIG_KEEP"; [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0
port_sm="${STIG_RECHECK_SM_PORT:-12595}" port_smtp="${STIG_RECHECK_SMTP_PORT:-12596}"
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/bin" "$work/mail" "$work/content"
"$py" "$here/make_results.py" "$work/content/ssg-test-ds.xml" '{}'      # its rules are the datastream's
echo '{}' > "$work/results.json"
cat > "$work/bin/oscap" <<EOF
#!/bin/bash
# oscap xccdf eval --profile P --results R [--report H] [--rule ID ...] DS
echo "\$*" > "$work/oscap_args.txt"
res="" rep=""
while [ \$# -gt 0 ]; do case "\$1" in --results) res="\$2"; shift ;; --report) rep="\$2"; shift ;; esac; shift; done
"$py" "$here/make_results.py" "\$res" "\$(cat "$work/results.json")" || exit 1
[ -n "\$rep" ] && echo "<html><body>OSCAP report</body></html>" > "\$rep"
grep -q '"fail"' "$work/results.json" && exit 2 || exit 0
EOF
chmod +x "$work/bin/oscap"
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
check_stig_oscap: $work/bin/oscap
check_stig_datastream: $work/content/ssg-test-ds.xml
check_stig_dir: $work/kept
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port_smtp
report_email_security: none
report_email_from: aap@example.mil
EOF
# STIG Manager: the asset's reviews - two Open (V-257883, V-258176), two Not a Finding, one Not a Finding rejected (so Not
# Reviewed: V-257779) - and the revision's rules: the rest have no review (Not Reviewed: V-257835, V-258012, V-258013, V-258999)
sm_data() {  # sm_data ASSETS_JSON [REVIEW_RESULT_OF_THE_OPEN_ONES]
    local res="${2:-fail}"
    "$py" - "$1" "$res" > "$work/sm.json" <<'PY'
import json, sys
def rv(rule, result, status, ver, sev, title):
    return {"ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": status}, "ts": "2026-08-15T10:00:00Z",
            "rule": {"ruleId": rule, "version": ver, "severity": sev, "title": title}}
res = sys.argv[2]
reviews = [rv("SV-257883r1044925_rule", res, "saved", "RHEL-09-412035", "medium", "RHEL 9 must automatically exit interactive sessions after 10 minutes."),
           rv("SV-258176r1045388_rule", res, "accepted", "RHEL-09-654225", "medium", "RHEL 9 must audit all uses of privileged commands."),
           rv("SV-257985r1069364_rule", "pass", "accepted", "RHEL-09-255045", "medium", "RHEL 9 must not permit direct logons to the root account using remote access through SSH."),
           rv("SV-257821r1015077_rule", "pass", "submitted", "RHEL-09-214020", "high", "RHEL 9 must check the GPG signature of locally installed software packages."),
           rv("SV-257779r958390_rule", "pass", "rejected", "RHEL-09-211020", "medium", "RHEL 9 must display the Standard Mandatory DoD Notice and Consent Banner.")]
if res == "pass":   # nothing Open or Not Reviewed
    reviews = [dict(r, status={"label": "accepted"}) for r in reviews]
rules = [{"ruleId": r, "groupId": "V-" + r[3:9], "version": v, "severity": s, "title": t} for r, v, s, t in [
    ("SV-257883r1044925_rule", "RHEL-09-412035", "medium", "RHEL 9 must automatically exit interactive sessions after 10 minutes."),
    ("SV-258176r1045388_rule", "RHEL-09-654225", "medium", "RHEL 9 must audit all uses of privileged commands."),
    ("SV-257985r1069364_rule", "RHEL-09-255045", "medium", "RHEL 9 must not permit direct logons to the root account using remote access through SSH."),
    ("SV-257821r1015077_rule", "RHEL-09-214020", "high", "RHEL 9 must check the GPG signature of locally installed software packages."),
    ("SV-257779r958390_rule", "RHEL-09-211020", "medium", "RHEL 9 must display the Standard Mandatory DoD Notice and Consent Banner."),
    ("SV-257835r1044874_rule", "RHEL-09-215040", "high", "RHEL 9 must not have the telnet-server package installed."),
    ("SV-258012r1014855_rule", "RHEL-09-271010", "medium", "RHEL 9 must display the banner before granting access via the GUI."),
    ("SV-258013r1045082_rule", "RHEL-09-271015", "medium", "RHEL 9 must prevent a user from overriding the banner-message-enable setting."),
    ("SV-258999r1000001_rule", "RHEL-09-999990", "medium", "A manual check: a document, never scanned.")]]
if res == "pass":
    rules = rules[:5]
print(json.dumps({"secret": "s3cret", "client": "autostig", "collections": [{"collectionId": "1", "name": "RHEL Servers"}],
                  "assets": json.loads(sys.argv[1]), "reviews": {"11": reviews}, "rules": {"RHEL_9_STIG": rules}}))
PY
}
web01='{"1": [{"assetId": "11", "name": "localhost", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R7"}]}]}'
sm_data "$web01"

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/stig_recheck.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4715}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "finding(s)\|\[WARNING\]\|\[CRITICAL\]\|FIXED\|ANSWER\|localhost:\|STIG Manager\|fatal\|ERROR" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}
decoded() {
    "$py" - "$1" > "$work/mail.txt" <<'PY'
import email, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"))
print("Subject: " + str(m["Subject"]))
for p in m.walk():
    if p.get_content_type() in ("text/plain", "text/html"):
        print(p.get_payload(decode=True).decode("utf-8", "replace"))
PY
}

# OpenSCAP: the session timeout still fails, the audit rules pass now, the banner fails, the rest pass
echo '{"accounts_tmout": "fail", "banner_etc_issue": "fail"}' > "$work/results.json"
run "OpenSCAP: only the rules Open or Not Reviewed checked, each sorted, report only" ok -- \
    "HEALTHY. Checks run: stig" "7 rules Open or Not Reviewed in STIG Manager: V-257779, V-257835, V-257883, V-258012, V-258013, V-258176, V-258999" \
    "localhost: 2 Open, 5 Not Reviewed in STIG Manager - fixed 1, now n/a 0, answered 4, still open 1, by hand 1, left out 0; STIG Manager: RHEL Servers / localhost (V2R7); checked: only these rules (--rule)" \
    "FIXED  V-258176 (RHEL-09-654225) CAT II" "ANSWER V-257779 (RHEL-09-211020) CAT II" "- set Open" "ANSWER V-257835 (RHEL-09-215040) CAT I" "- set Not a Finding" \
    "checklist on the host: $work/kept/recheck/latest-results.xml" "!Traceback" "!is undefined"
if grep -q -- "--rule xccdf_org.ssgproject.content_rule_accounts_tmout" "$work/oscap_args.txt" && grep -q -- "--rule xccdf_org.ssgproject.content_rule_dconf_gnome_banner_enabled" "$work/oscap_args.txt" \
   && ! grep -q "sshd_disable_root_login\|ensure_gpgcheck_local_packages\|enable_authselect" "$work/oscap_args.txt"; then
    echo "ok   - oscap ran with a --rule for each SCAP rule of them, none for the rules STIG Manager has as Not a Finding"
else echo "FAIL - oscap's arguments"; cat "$work/oscap_args.txt"; fails=$((fails + 1)); fi
if [ -s "$work/kept/recheck/latest-results.xml" ] && ! ls "$work/kept"/*_summary.json >/dev/null 2>&1 && [ "$(stat -c %a "$work/kept/recheck")" = 700 ]; then
    echo "ok   - oscap's results kept as recheck/latest-results.xml (root-only), nothing kept with the drift report's scans"
else echo "FAIL - what was kept"; ls -laR "$work/kept"; fails=$((fails + 1)); fi
if [ "$(ls "$work/mail" | wc -l)" -ge 1 ]; then
    decoded "$(ls "$work/mail"/* | head -1)"; m="$work/mail.txt"
    if grep -q "Subject: .*STIG recheck" "$m" && grep -q "1. Open in STIG Manager - the host passes now: update the review" "$m" \
       && grep -q "2. Not Reviewed in STIG Manager - the scan has an answer" "$m" && grep -q "3. Still open - the scan agrees with STIG Manager" "$m" \
       && grep -q "By hand - the scan gave no answer" "$m" && grep -q "no SCAP check for it (a manual check)" "$m" \
       && grep -q "Open (accepted, 2026-08-15)" "$m" && grep -q "Not a Finding (rejected, 2026-08-15)" "$m" && grep -q ">no review<" "$m"
    then echo "ok   - the email: update the review (fixed), Not Reviewed answered (a rejected review counts as none), still open, by hand"
    else echo "FAIL - the email's content"; grep -i "subject\|open in\|not reviewed\|still open\|by hand" "$m" | head -8; fails=$((fails + 1)); fi
else echo "FAIL - no email arrived"; fails=$((fails + 1)); fi

rm -rf "$work/kept"
run "Check mode: checked and sorted, nothing kept" ok --check -- "fixed 1, now n/a 0, answered 4" "nothing kept on the host (not kept: dry run)"
[ ! -e "$work/kept" ] && echo "ok   - Check mode kept nothing on the host" || { echo "FAIL - Check mode kept something"; ls -R "$work/kept"; fails=$((fails + 1)); }

run "check_stig_recheck: [open] - only what STIG Manager has Open" ok -e '{"check_stig_recheck": ["open"]}' -- \
    "2 rules Open or Not Reviewed in STIG Manager: V-257883, V-258176" "localhost: 2 Open, 0 Not Reviewed in STIG Manager - fixed 1, now n/a 0, answered 0, still open 1, by hand 0"
run "check_stig_recheck must be open and / or not_reviewed" fail -e '{"check_stig_recheck": ["all"]}' -- \
    "check_stig_recheck must list open, not_reviewed or both"

sm_data "$web01" pass
rm -f "$work/oscap_args.txt"
run "nothing Open or Not Reviewed: nothing checked, healthy" ok -- \
    "HEALTHY" "localhost: nothing to recheck - STIG Manager has no rule Open or Not Reviewed"
[ ! -e "$work/oscap_args.txt" ] && echo "ok   - ... oscap was not run" || { echo "FAIL - oscap ran with nothing to check"; fails=$((fails + 1)); }

sm_data '{"1": []}'
run "the asset not in STIG Manager: not rechecked, a warning" fail -- \
    "[WARNING] stig: not rechecked - what to recheck is read from STIG Manager: no asset named localhost with RHEL_9_STIG" "NOT RECHECKED"
run "STIG Manager not set: the check could not run, said what to set" fail -e check_stig_stigman_api= -- \
    "The STIG recheck reads what to check from STIG Manager: set check_stig_stigman_api"
sm_data "$web01"

# ---- Evaluate-STIG (check_stig_scanner: evaluate_stig) ----
mkdir -p "$work/es" "$work/es_old"
echo '{"SV-257883": "open", "SV-257779": "open"}' > "$work/es_results.json"
for d in es es_old; do
    cat > "$work/$d/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# Evaluate-STIG_Bash.sh --Output CKLB --OutputPath DIR ...: DIR/HOST/Checklist/HOST_RHEL9.cklb
out=""; echo "\$*" > "$work/${d}_args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; esac; shift; done
mkdir -p "\$out/WEB01/Checklist" && "$py" "$here/make_cklb.py" "\$out/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb" "\$(cat "$work/es_results.json")" V2R10
echo "Done"
EOF
done
echo '# its options: --SelectSTIG, --SelectVuln, --ExcludeSTIG ...' >> "$work/es/Evaluate-STIG_Bash.sh"
es_on="{\"check_stig_scanner\": \"evaluate_stig\", \"check_stig_evaluate\": \"$work/es\", \"check_stig_evaluate_select\": {\"$major\": \"RHEL9\"}}"
run "Evaluate-STIG with --SelectVuln: only those rules, sorted, its checklist kept" ok -e "$es_on" -- \
    "HEALTHY" "localhost: 2 Open, 5 Not Reviewed in STIG Manager - fixed 1, now n/a 0, answered 4, still open 1, by hand 1" \
    "checked: only these rules (-SelectVuln)" "checklist on the host: $work/kept/recheck/latest-checklist.cklb"
if grep -q -- "--SelectSTIG RHEL9 --SelectVuln V-257779,V-257835,V-257883,V-258012,V-258013,V-258176,V-258999" "$work/es_args.txt" \
   && [ -s "$work/kept/recheck/latest-checklist.cklb" ] && ! ls "$work/kept"/*_summary.json >/dev/null 2>&1; then
    echo "ok   - Evaluate-STIG ran with --SelectVuln (the rules asked for); its checklist kept, nothing with the drift report's scans"
else echo "FAIL - Evaluate-STIG's arguments or what was kept"; cat "$work/es_args.txt"; ls -R "$work/kept"; fails=$((fails + 1)); fi
run "an Evaluate-STIG without --SelectVuln: run in full, the same answers" ok -e "$es_on" -e check_stig_evaluate="$work/es_old" -- \
    "fixed 1, now n/a 0, answered 4, still open 1, by hand 1" "checked: in full - this Evaluate-STIG has no -SelectVuln"
grep -q "SelectVuln" "$work/es_old_args.txt" && { echo "FAIL - --SelectVuln given to an Evaluate-STIG without it"; fails=$((fails + 1)); } \
    || echo "ok   - ... no --SelectVuln given to it"

if command -v pwsh >/dev/null; then
    mkdir -p "$work/es_ps"
    cat > "$work/es_ps/Evaluate-STIG.ps1" <<EOF
param([string[]] \$Output = @('CKLB'), [string] \$OutputPath = '', [string[]] \$SelectSTIG = @(), [string[]] \$SelectVuln = @())
Set-Content -LiteralPath '$work/es_ps_args.txt' -Value ('SelectVuln=' + \$SelectVuln.Count + ' ' + (\$SelectVuln -join '|'))
New-Item -ItemType Directory -Path (Join-Path \$OutputPath 'WEB01/Checklist') -Force | Out-Null
& '$py' '$here/make_cklb.py' (Join-Path \$OutputPath 'WEB01/Checklist/WEB01_RHEL9.cklb') (Get-Content -Raw '$work/es_results.json') V2R10
exit 0
EOF
    run "Evaluate-STIG.ps1 (pwsh): -SelectVuln given as one array" ok -e "$es_on" -e check_stig_evaluate="$work/es_ps" -- \
        "fixed 1, now n/a 0, answered 4, still open 1, by hand 1" "checked: only these rules (-SelectVuln)"
    grep -q "SelectVuln=7 V-257779|V-257835" "$work/es_ps_args.txt" && echo "ok   - ... 7 rules, each its own" \
        || { echo "FAIL - -SelectVuln as PowerShell read it"; cat "$work/es_ps_args.txt"; fails=$((fails + 1)); }
else
    echo "skip - Evaluate-STIG.ps1 with pwsh: pwsh is not installed"
fi

if grep -qvE '^(GET /api/|POST /token$)' "$work/sm.log"; then echo "FAIL - a request that is not a read reached STIG Manager"; grep -vE '^(GET /api/|POST /token$)' "$work/sm.log" | head -3; fails=$((fails + 1))
else echo "ok   - only GET requests reached STIG Manager ($(grep -c '^GET' "$work/sm.log") of them) and the token POSTs"; fi

echo
if [ $fails -eq 0 ]; then echo "all STIG recheck scenarios passed"; else echo "$fails STIG recheck scenario(s) FAILED"; exit 1; fi
