#!/usr/bin/env bash
# The Windows STIG drift report (playbooks/win_stig_drift_report.yml, roles/win_check_stig + roles/check_stig) with no
# Windows: the local connection with PowerShell 7 (pwsh) standing in for Windows PowerShell, as
# tests/windows/patch_sim does, the stand-in Evaluate-STIG (tests/windows/stig/Evaluate-STIG.ps1, run in the job's
# session: win_check_stig_run_as: direct) and the stand-in Keycloak + STIG Manager (tests/stig/stigman_mock.py,
# read-only). In order: the first scan, no STIG Manager (three checklists, kept per checklist on the server); a rule
# that passed fails (new, CAT I critical, named for its STIG); STIG Manager: drift, the web site's checklist matched to
# its own asset (disagree), a STIG the asset does not have (listed, no finding); the email; the asset not there (one
# finding for the server); Check mode keeps nothing; the checklists Evaluate-STIG already wrote read; Evaluate-STIG not
# found; only GET requests reached STIG Manager. Needs ansible-playbook, pwsh and python3.
#   bash tests/stig/run_win_stig_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
pwsh=$(command -v pwsh) || { echo "pwsh not found"; exit 1; }
work="$(mktemp -d)"
smtp_pid="" sm_pid=""
cleanup() { [ -n "${STIG_KEEP:-}" ] && cp "$work/out.txt" "$STIG_KEEP"; [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0
port_sm="${STIG_WIN_SM_PORT:-12593}" port_smtp="${STIG_WIN_SMTP_PORT:-12594}"
mkdir -p "$work/bin" "$work/mail" "$work/es/Evaluate-STIG" "$work/empty"
cat > "$work/bin/powershell" <<SHIM
#!/bin/bash
exec "$pwsh" "\$@"
SHIM
chmod +x "$work/bin/powershell"; ln -s powershell "$work/bin/PowerShell"
# a copy of the repository: the local PowerShell connection leaves temporary files where it runs
mkdir -p "$work/repo" && (cd "$repo" && tar --exclude=.git -cf - .) | (cd "$work/repo" && tar -xf -)
cp "$repo/tests/windows/stig/Evaluate-STIG.ps1" "$work/es/Evaluate-STIG/"
cat > "$work/inv.yml" <<INV
all:
  hosts:
    win01:
      ansible_connection: local
      ansible_shell_type: powershell
      ansible_python_interpreter: $py
INV
spec() {  # spec FIRST_RESULT: the server's checklists - Windows Server 2022, an IIS web site, Edge
    "$py" - "$1" > "$work/es/Evaluate-STIG/spec.json" <<'PY'
import json, sys
def r(sv, sev, status, details=""):
    return {"sv": sv, "v": "V-" + sv[3:], "stigid": "WN22-00-" + sv[3:], "sev": sev, "title": "Rule %s must be set." % sv, "status": status, "details": details}
print(json.dumps({"host": "WIN01", "exit": 0, "checklists": [
    {"file": "WIN01_WinServer2022_V2R4", "stig_id": "Windows_Server_2022_STIG", "display_name": "Microsoft Windows Server 2022", "version": "2",
     "release": "Release: 4 Benchmark Date: 02 Jul 2026",
     "rules": [r("SV-254238", "high", sys.argv[1], "Found: LmCompatibilityLevel is 3."), r("SV-254239", "medium", "fail", "Found: LegalNoticeText is empty."),
               r("SV-254240", "medium", "pass"), r("SV-254241", "low", "nr")]},
    {"file": "WIN01_IIS10Site_Default_Web_Site", "stig_id": "IIS_10-0_Site_STIG", "display_name": "Microsoft IIS 10.0 Site", "version": "2",
     "release": "Release: 9 Benchmark Date: 02 Jul 2026", "site": "Default Web Site", "instance": "",
     "rules": [r("SV-218735", "medium", "pass"), r("SV-218736", "medium", "fail", "Found: directory browsing is on.")]},
    {"file": "WIN01_MSEdge_V2R2", "stig_id": "MS_Edge_STIG", "display_name": "Microsoft Edge", "version": "2", "release": "Release: 2 Benchmark Date: 02 Jul 2026",
     "rules": [r("SV-235719", "medium", "fail", "Found: SmartScreen is off.")]}]}))
PY
}
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
win_check_stig_evaluate: $work/es/Evaluate-STIG
win_check_stig_run_as: direct
win_check_stig_dir: $work/kept
win_check_stig_timeout: 120
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port_smtp
report_email_security: none
report_email_from: aap@example.mil
EOF
sm_on="{\"check_stig_stigman_api\": \"http://127.0.0.1:$port_sm/api\", \"check_stig_stigman_token_url\": \"http://127.0.0.1:$port_sm/token\", \"check_stig_stigman_client_id\": \"autostig\", \"check_stig_stigman_client_secret\": \"s3cret\"}"
sm_data() {  # sm_data ASSETS_JSON: the server's asset (Windows Server 2022), its web site's asset (IIS site) - no Edge
    cat > "$work/sm.json" <<EOF
{"secret": "s3cret", "client": "autostig",
 "collections": [{"collectionId": "1", "name": "Windows Servers"}],
 "assets": $1,
 "reviews": {"21": [
   {"benchmarkId": "Windows_Server_2022_STIG", "ruleId": "SV-254238r1000_rule", "ruleIds": ["SV-254238r1000_rule"], "result": "pass", "status": {"label": "accepted"}, "ts": "2026-09-01T10:00:00Z",
    "rule": {"ruleId": "SV-254238r1000_rule", "version": "WN22-SO-000310", "severity": "high", "title": "Windows Server 2022 LAN Manager authentication level must be set."}},
   {"benchmarkId": "Windows_Server_2022_STIG", "ruleId": "SV-254239r1000_rule", "ruleIds": ["SV-254239r1000_rule"], "result": "fail", "status": {"label": "submitted"}, "ts": "2026-09-01T10:00:00Z",
    "rule": {"ruleId": "SV-254239r1000_rule", "version": "WN22-SO-000150", "severity": "medium", "title": "The legal notice must be configured."}},
   {"benchmarkId": "Windows_Server_2022_STIG", "ruleId": "SV-254241r1000_rule", "ruleIds": ["SV-254241r1000_rule"], "result": "notapplicable", "status": {"label": "accepted"}, "ts": "2026-09-01T10:00:00Z",
    "rule": {"ruleId": "SV-254241r1000_rule", "version": "WN22-00-000010", "severity": "low", "title": "A manual check."}}],
  "22": [
   {"benchmarkId": "IIS_10-0_Site_STIG", "ruleId": "SV-218736r1000_rule", "ruleIds": ["SV-218736r1000_rule"], "result": "pass", "status": {"label": "accepted"}, "ts": "2026-09-01T10:00:00Z",
    "rule": {"ruleId": "SV-218736r1000_rule", "version": "IIST-SI-000217", "severity": "medium", "title": "Directory browsing must be disabled."}}]}}
EOF
}
assets='{"1": [{"assetId": "21", "name": "win01", "fqdn": "", "stigs": [{"benchmarkId": "Windows_Server_2022_STIG", "revisionStr": "V2R3"}]},
              {"assetId": "22", "name": "win01-Default Web Site-NA", "fqdn": "", "stigs": [{"benchmarkId": "IIS_10-0_Site_STIG", "revisionStr": "V2R9"}]}]}'
sm_data "$assets"

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$work/repo" && PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH=roles ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False timeout 600 "$ap" -i "$work/inv.yml" playbooks/win_stig_drift_report.yml \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4714}' "${args[@]}") > "$work/out.txt" 2>&1 </dev/null
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "finding(s)\|\[WARNING\]\|\[CRITICAL\]\|DRIFT\|NEW \|STIG Manager\|fatal\|ERROR\|win01" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}
kept() { ls "$work/kept/$1"/*_summary.json 2>/dev/null | grep -vc latest; }
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

spec pass
run "the first scan, no STIG Manager: three checklists, nothing to compare, healthy" ok -- \
    "HEALTHY. Checks run: stig" "win01 - Microsoft Windows Server 2022: Microsoft Windows Server 2022 V2R4 - 2 pass, 1 fail; drifted 0, new 0" \
    "win01 - Microsoft IIS 10.0 Site - Default Web Site:" "win01 - Microsoft Edge:" "STIG Manager: not used" "!is undefined" "!Traceback"
if [ "$(kept Windows_Server_2022_STIG)" -eq 1 ] && [ "$(kept IIS_10-0_Site_STIG_Default_Web_Site)" -eq 1 ] && ls "$work/kept/MS_Edge_STIG"/*_checklist.cklb >/dev/null 2>&1; then
    echo "ok   - kept on the server: one folder per checklist, its summary and checklist"
else echo "FAIL - what was kept"; ls -R "$work/kept" | head -20; fails=$((fails + 1)); fi
sleep 1

spec fail
run "a rule that passed fails now: new, CAT I critical, named for its STIG" fail -- \
    "[CRITICAL] stig: Microsoft Windows Server 2022: 1 STIG rule passed in an earlier scan (last 20" "V-254238 CAT I" "!Microsoft Edge: 1 STIG"
sleep 1

rm -f "$work/mail"/*
run "STIG Manager: drift (CAT I), the web site's own asset (disagree), a STIG the asset does not have (no finding)" fail -e "$sm_on" -- \
    "[CRITICAL] stig: Microsoft Windows Server 2022: 1 STIG rule STIG Manager has as Not a Finding or Not Applicable fails now: V-254238 CAT I" \
    "STIG Manager: Windows Servers / win01 (V2R3)" "STIG Manager: Windows Servers / win01-Default Web Site-NA (V2R9); disagree 1" \
    "the asset win01 (Windows Servers) does not have MS_Edge_STIG assigned in STIG Manager" "!not compared with STIG Manager" "by hand 1"
if [ "$(ls "$work/mail" | wc -l)" -ge 1 ]; then
    decoded "$(ls "$work/mail"/* | head -1)"; m="$work/mail.txt"
    if grep -q "Subject: .*Windows STIG drift report" "$m" && grep -q "1. Drifted - the host fails what STIG Manager has as Not a Finding" "$m" \
       && grep -q "win01 - Microsoft Windows Server 2022" "$m" && grep -q "win01 - Microsoft IIS 10.0 Site - Default Web Site" "$m" \
       && grep -q "Start here" "$m" && grep -q "Found: LmCompatibilityLevel is 3." "$m" && grep -q "Found: directory browsing is on." "$m" \
       && grep -q "What Evaluate-STIG found - in full" "$m"
    then echo "ok   - the email: drifted with what Evaluate-STIG found, each checklist named for its server and STIG"
    else echo "FAIL - the email's content"; grep -i "subject\|drift\|win01\|start here\|found:" "$m" | head -10; fails=$((fails + 1)); fi
else echo "FAIL - no email"; fails=$((fails + 1)); fi

sm_data '{"1": []}'
run "the server's asset not in STIG Manager: one finding for the server, not one per checklist" fail -e "$sm_on" -- \
    "[WARNING] stig: not compared with STIG Manager: no asset named win01"
n=$(grep -c "\[WARNING\] stig: not compared with STIG Manager" "$work/out.txt")
[ "$n" -eq 1 ] && echo "ok   - ... once" || { echo "FAIL - the asset-not-there finding $n times"; fails=$((fails + 1)); }
sm_data "$assets"

before=$(kept Windows_Server_2022_STIG)
run "Check mode: scans and compares, keeps nothing on the server" fail --check -- "NEW   V-254238 (WN22-00-254238) CAT I"
[ "$(kept Windows_Server_2022_STIG)" -eq "$before" ] && echo "ok   - ... nothing kept" || { echo "FAIL - Check mode kept a run"; fails=$((fails + 1)); }

(cd "$work/es/Evaluate-STIG" && "$pwsh" -NoProfile -File Evaluate-STIG.ps1 -OutputPath "$work/compliance" >/dev/null)
run "win_check_stig_evaluate_read: the checklists Evaluate-STIG already wrote, read - not run (a first scan: healthy)" ok -e win_check_stig_evaluate=/nowhere \
    -e win_check_stig_evaluate_read="$work/compliance" -e win_check_stig_dir="$work/kept2" -- \
    "win01 - Microsoft Windows Server 2022: Microsoft Windows Server 2022 V2R4 - 1 pass, 2 fail" "its checklist of 20" "!NOT SCANNED"

run "Evaluate-STIG not found: not scanned, a warning that says what to set" fail -e win_check_stig_evaluate= \
    -e "{\"win_check_stig_search\": [\"$work/empty\"]}" -- \
    "[WARNING] stig: the STIG scan did not run: Evaluate-STIG was not found on the server" "win_check_stig_evaluate" "NOT SCANNED"

if grep -qvE "^(GET |POST /token)" "$work/sm.log"; then echo "FAIL - a request that is not a GET reached STIG Manager"; grep -vE "^(GET |POST /token)" "$work/sm.log" | head -3; fails=$((fails + 1))
else echo "ok   - only GET requests reached STIG Manager ($(wc -l < "$work/sm.log") requests)"; fi

echo
if [ $fails -eq 0 ]; then echo "all Windows STIG drift scenarios passed"; else echo "$fails Windows STIG drift scenario(s) FAILED"; exit 1; fi
