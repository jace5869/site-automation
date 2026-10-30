#!/usr/bin/env bash
# The STIG drift report (roles/check_stig, playbooks/stig_drift_report.yml) on this box, as a plain user (no sudo),
# with a fake oscap that writes the results file tests/stig/make_results.py makes, and a stand-in Keycloak + STIG
# Manager (tests/stig/stigman_mock.py, read-only). In order: no oscap (not scanned); the first scan, no STIG Manager
# (nothing to compare: healthy, the scan kept on the host); a rule that passed fails now (new); STIG Manager with
# the asset: drift (CAT I critical), fixed but still Open, no review, the new fail that STIG Manager already has
# open is not new; the same fails on the next run: still drifted; the email; Check mode keeps nothing;
# check_stig_ignore_rules; a host whose first scan fails what STIG Manager has as Not a Finding (the scan and the
# checklist disagree: report only, or a finding with check_stig_disagree_severity); the asset in no collection, in two
# (several), the collection set; check_stig_stigman_asset; a wrong secret; only GET requests reached STIG Manager;
# keep 2.
#   bash tests/stig/run_stig_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
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
port_sm="${STIG_SM_PORT:-12581}" port_smtp="${STIG_SMTP_PORT:-12582}"
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/bin" "$work/mail" "$work/content"
: > "$work/content/ssg-test-ds.xml"
echo '{}' > "$work/results.json"
cat > "$work/bin/oscap" <<EOF
#!/bin/bash
# oscap xccdf eval --profile P --results R [--report H] [--tailoring-file T] DS
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
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port_smtp
report_email_security: none
report_email_from: aap@example.mil
EOF
sm_on="{\"check_stig_stigman_api\": \"http://127.0.0.1:$port_sm/api\", \"check_stig_stigman_token_url\": \"http://127.0.0.1:$port_sm/token\", \"check_stig_stigman_client_id\": \"autostig\", \"check_stig_stigman_client_secret\": \"s3cret\"}"
sm_data() {  # sm_data ASSETS_JSON: two collections, the reviews of asset 11
    cat > "$work/sm.json" <<EOF
{"secret": "s3cret", "client": "autostig",
 "collections": [{"collectionId": "1", "name": "RHEL Servers"}, {"collectionId": "2", "name": "Lab"}],
 "assets": $1,
 "reviews": {"11": [
   {"ruleId": "SV-257985r1045000_rule", "ruleIds": ["SV-257985r1045000_rule", "SV-257985r1069364_rule"], "result": "pass", "status": {"label": "accepted"}, "ts": "2026-09-01T10:00:00Z",
    "rule": {"ruleId": "SV-257985r1045000_rule", "version": "RHEL-09-255045", "severity": "medium", "title": "RHEL 9 must not permit direct logons to the root account using remote access through SSH."}},
   {"ruleId": "SV-257821r1015077_rule", "ruleIds": ["SV-257821r1015077_rule"], "result": "pass", "status": {"label": "submitted"}, "ts": "2026-09-01T10:00:00Z",
    "rule": {"ruleId": "SV-257821r1015077_rule", "version": "RHEL-09-214020", "severity": "high", "title": "RHEL 9 must check the GPG signature of locally installed software packages."}},
   {"ruleId": "SV-257883r1044925_rule", "ruleIds": ["SV-257883r1044925_rule"], "result": "fail", "status": {"label": "saved"}, "ts": "2026-08-15T10:00:00Z",
    "rule": {"ruleId": "SV-257883r1044925_rule", "version": "RHEL-09-412035", "severity": "medium", "title": "RHEL 9 must automatically exit interactive sessions after 10 minutes."}},
   {"ruleId": "SV-257779r958390_rule", "ruleIds": ["SV-257779r958390_rule"], "result": "pass", "status": {"label": "rejected"}, "ts": "2026-08-15T10:00:00Z",
    "rule": {"ruleId": "SV-257779r958390_rule", "version": "RHEL-09-211020", "severity": "medium", "title": "The banner."}},
   {"ruleId": "SV-258176r1045388_rule", "ruleIds": ["SV-258176r1045388_rule"], "result": "fail", "status": {"label": "accepted"}, "ts": "2026-08-15T10:00:00Z",
    "rule": {"ruleId": "SV-258176r1045388_rule", "version": "RHEL-09-654225", "severity": "medium", "title": "RHEL 9 must audit all uses of privileged commands."}},
   {"ruleId": "SV-258999r1000001_rule", "ruleIds": ["SV-258999r1000001_rule"], "result": "pass", "status": {"label": "accepted"}, "ts": "2026-08-15T10:00:00Z",
    "rule": {"ruleId": "SV-258999r1000001_rule", "version": "RHEL-09-999990", "severity": "medium", "title": "A manual check: a document, never scanned."}}]}}
EOF
}
web01='{"assetId": "11", "name": "localhost", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R7"}]}'
sm_data "{\"1\": [$web01], \"2\": []}"

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/stig_drift_report.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4713}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "finding(s)\|\[WARNING\]\|\[CRITICAL\]\|DRIFT\|NEW \|STIG Manager\|fatal\|ERROR" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}
kept() { ls "$work/kept"/*_summary.json 2>/dev/null | wc -l; }

run "no oscap: not scanned, a warning" fail -e "check_stig_oscap=$work/bin/none" -- \
    "[WARNING] stig: the STIG scan did not run: oscap is not installed (the package openscap-scanner" "NOT SCANNED" "!Traceback"

run "the first scan, no STIG Manager: nothing to compare, healthy, kept on the host" ok -- \
    "HEALTHY. Checks run: stig" "DISA STIG for Red Hat Enterprise Linux 9 V2R9 - 9 pass, 0 fail; drifted 0, new 0" "STIG Manager: not used" \
    "report on the host: $work/kept/" "!is undefined"
if [ "$(kept)" -eq 1 ] && [ -s "$work/kept/latest-summary.json" ] && [ -s "$work/kept/latest-report.html" ] && ls "$work/kept"/*_results.xml.gz >/dev/null 2>&1 \
   && [ "$(stat -c %a "$work/kept")" = 700 ] && [ "$(stat -c %a "$work/kept/latest-summary.json")" = 600 ]; then echo "ok   - kept on the host: summary, report, results; the folder and files root-only"
else echo "FAIL - what was kept"; ls -la "$work/kept"; fails=$((fails + 1)); fi
sleep 1

echo '{"accounts_tmout": "fail", "audit_rules_privileged_commands_su": "fail"}' > "$work/results.json"
run "a rule that passed fails now: new (CAT II: warning); two SCAP rules of one STIG rule count once" fail -- \
    "[WARNING] stig: 2 STIG rules passed in an earlier scan (last 20" "V-257883 CAT II" "V-258176 CAT II" \
    "7 pass, 2 fail" "NEW   V-258176 (RHEL-09-654225)" "!CRITICAL"
sleep 1

rm -f "$work/mail"/*
echo '{"accounts_tmout": "fail", "audit_rules_privileged_commands_su": "fail", "ensure_gpgcheck_local_packages": "fail", "sshd_disable_root_login": "fail", "banner_etc_issue": "fail", "package_telnet-server_removed": "fail"}' > "$work/results.json"
run "STIG Manager: drift (CAT I critical), the rejected review does not count, Open + now fail is not drift" fail -e "$sm_on" -- \
    "[CRITICAL] stig: 2 STIG rules STIG Manager has as Not a Finding or Not Applicable fail now: V-257821 CAT I, V-257985 CAT II" \
    "DRIFT V-257821 (RHEL-09-214020) CAT I" "STIG Manager: RHEL Servers / localhost" \
    "[CRITICAL] stig: 2 STIG rules passed in an earlier scan" "V-257835 CAT I, V-257779 CAT II" \
    "!DRIFT V-257779" "!DRIFT V-257883" "!DRIFT V-258176" "disagree 0, by hand 1, left out 0"
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
if [ "$(ls "$work/mail" | wc -l)" -ge 1 ]; then
    decoded "$(ls "$work/mail"/* | head -1)"; m="$work/mail.txt"
    if grep -q "1. Drifted - the host fails what STIG Manager has as Not a Finding" "$m" && grep -q "Every host - the scan, STIG Manager, the counts" "$m" \
       && grep -q "Fails STIG Manager has no review for" "$m" && grep -q "RHEL Servers / localhost (V2R7)" "$m" && grep -q "Subject: .*STIG drift report" "$m" \
       && grep -q "Not a Finding (submitted)" "$m" && grep -q "Last passed" "$m" && grep -q "Start here" "$m" \
       && grep -q "sudo oscap xccdf eval --profile xccdf_org.ssgproject.content_profile_stig --rule xccdf_org.ssgproject.content_rule_ensure_gpgcheck_local_packages" "$m"
    then echo "ok   - the email: Start here with the command to check the rule again, drifted (last passed), every host (collection / asset, revision), no review"
    else echo "FAIL - the email's content"; grep -i "subject\|drift\|every host\|start here\|oscap" "$m" | head -8; fails=$((fails + 1)); fi
else echo "FAIL - no email arrived"; fails=$((fails + 1)); fi
sleep 1

run "the same fails on the next run: still drifted (they passed in an earlier scan), still a finding" fail -e "$sm_on" -- \
    "[CRITICAL] stig: 2 STIG rules STIG Manager has as Not a Finding or Not Applicable fail now: V-257821 CAT I, V-257985 CAT II" \
    "DRIFT V-257821 (RHEL-09-214020) CAT I" "!DISAGREE"
sleep 1
run "check_stig_ignore_rules: a rule left out is never a finding, it is counted" fail -e "$sm_on" -e '{"check_stig_ignore_rules": ["V-257821", "RHEL-09-215040"]}' -- \
    "[WARNING] stig: 1 STIG rule STIG Manager has as Not a Finding or Not Applicable fails now: V-257985 CAT II" "left out 2" "!DRIFT V-257821" "!CRITICAL"
run "check_stig_disagree_severity must be none, warning or critical" fail -e check_stig_disagree_severity=high -- \
    "check_stig_disagree_severity must be none, warning or critical (it is high)"
sleep 1

echo '{"audit_rules_privileged_commands_su": "fail"}' > "$work/results.json"
rm -f "$work/mail"/*
run "fixed on the host, still Open in STIG Manager: report only" ok -e "$sm_on" -- "HEALTHY" "fixed but open 1"
decoded "$(ls "$work/mail"/* | head -1)"
grep -q "4. Fixed on the host, still Open in STIG Manager" "$work/mail.txt" && grep -q "Open (saved, 2026-08-15)" "$work/mail.txt" \
    && echo "ok   - the email: fixed, still Open, with the review's status and date" || { echo "FAIL - the fixed-open table"; fails=$((fails + 1)); }

n=$(kept)
echo '{"sshd_disable_root_login": "fail"}' > "$work/results.json"
run "Check mode: scanned and compared, nothing kept" fail --check -e "$sm_on" -- "[WARNING] stig: 1 STIG rule STIG Manager has as Not a Finding or Not Applicable fails now: V-257985 CAT II" "(not kept: dry run)"
[ "$(kept)" -eq "$n" ] && echo "ok   - Check mode kept nothing on the host" || { echo "FAIL - Check mode kept a scan"; fails=$((fails + 1)); }

echo '{"sshd_disable_root_login": "fail", "ensure_gpgcheck_local_packages": "fail"}' > "$work/results.json"
rm -f "$work/mail"/*
run "a host's first scan fails what STIG Manager has as Not a Finding: the scan and the checklist disagree - report only" ok \
    -e "$sm_on" -e check_stig_dir="$work/kept2" -- "HEALTHY" "drifted 0, new 0" "disagree 2, by hand 1, left out 0" \
    "DISAGREE V-257821 (RHEL-09-214020) CAT I" "the scan's check: ensure_gpgcheck_local_packages" "!DRIFT"
decoded "$(ls "$work/mail"/* | head -1)"
grep -q "3. The scan and the checklist disagree - look at each once" "$work/mail.txt" && grep -q "check_stig_disagree_severity: warning makes them findings" "$work/mail.txt" \
    && echo "ok   - the email: the scan and the checklist disagree, report only" || { echo "FAIL - the disagree table"; fails=$((fails + 1)); }
sleep 1
run "the next scan fails them again: still disagree (they never passed here); check_stig_disagree_severity: warning - a finding" fail \
    -e "$sm_on" -e check_stig_dir="$work/kept2" -e check_stig_disagree_severity=warning -- \
    "[WARNING] stig: 2 STIG rules STIG Manager has as Not a Finding or Not Applicable have failed in every scan of this host: V-257821 CAT I, V-257985 CAT II" \
    "disagree 2" "!DRIFT" "!CRITICAL"

echo '{}' > "$work/results.json"
sm_data '{"1": [], "2": []}'
run "the asset is in no collection: a warning, not compared" fail -e "$sm_on" -- \
    "[WARNING] stig: not compared with STIG Manager: no asset named localhost with RHEL_9_STIG in any collection the client can read (2)"
run "check_stig_stigman_missing_severity: none - listed only" ok -e "$sm_on" -e check_stig_stigman_missing_severity=none -- "HEALTHY" "no asset named localhost"
lab='{"assetId": "21", "name": "LOCALHOST.example.mil", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R9"}]}'
sm_data "{\"1\": [], \"2\": [$lab]}"
run "the asset by its short name (localhost = LOCALHOST.example.mil), in any collection" ok -e "$sm_on" -- "STIG Manager: Lab / LOCALHOST.example.mil (V2R9)"
sm_data "{\"1\": [$web01], \"2\": [$lab]}"
run "an exact name wins over a short one" ok -e "$sm_on" -- "STIG Manager: RHEL Servers / localhost"
lab2='{"assetId": "21", "name": "localhost", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R9"}]}'
sm_data "{\"1\": [$web01], \"2\": [$lab2]}"
run "the same name in two collections: several, not compared" fail -e "$sm_on" -- \
    "2 assets match: localhost in RHEL Servers, localhost in Lab - set check_stig_stigman_collection"
run "the collection set: found there" ok -e "$sm_on" -e '{"check_stig_stigman_collection": "Lab"}' -- "STIG Manager: Lab / localhost"
run "a collection that is not there" fail -e "$sm_on" -e '{"check_stig_stigman_collection": "Nope"}' -- "the collection Nope is not there, or the client has no grant in it"
sm_data "{\"1\": [$web01], \"2\": [$lab]}"
run "check_stig_stigman_asset: the asset's own name" ok -e "$sm_on" -e '{"check_stig_stigman_asset": "localhost.example.mil"}' -- "Lab / LOCALHOST.example.mil"
run "a wrong secret: said once, every host not compared" fail -e "$sm_on" -e check_stig_stigman_client_secret=nope -- \
    "STIG Manager: no token for the client autostig: HTTP 401" "!s3cret"
if grep -qvE '^(GET /api/|POST /token$)' "$work/sm.log"; then echo "FAIL - a request that is not a read reached STIG Manager"; grep -vE '^(GET /api/|POST /token$)' "$work/sm.log" | head -3; fails=$((fails + 1))
else echo "ok   - only GET requests reached STIG Manager ($(grep -c '^GET' "$work/sm.log") of them) and the token POSTs"; fi

run "keep 2: the oldest scans go" ok -e check_stig_keep=2 -- "HEALTHY"
[ "$(kept)" -eq 2 ] && [ "$(ls "$work/kept"/*_report.html | wc -l)" -eq 2 ] && echo "ok   - 2 scans kept" || { echo "FAIL - keep 2: $(kept) kept"; fails=$((fails + 1)); }

echo
if [ $fails -eq 0 ]; then echo "all STIG drift scenarios passed"; else echo "$fails STIG drift scenario(s) FAILED"; exit 1; fi
