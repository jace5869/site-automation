#!/usr/bin/env bash
# The Windows STIG recheck (playbooks/win_stig_recheck.yml, roles/win_check_stig tasks/recheck.yml + roles/check_stig) with
# no Windows: the local connection with PowerShell 7 (pwsh) standing in for Windows PowerShell, as
# tests/stig/run_win_stig_test.sh does, the stand-in Evaluate-STIG (tests/windows/stig/Evaluate-STIG.ps1, with
# -SelectVuln) and the stand-in Keycloak + STIG Manager (tests/stig/stigman_mock.py, read-only, with the STIGs' rules). In
# order: every STIG of the server's asset and of its web site's asset (not another server's, not an asset named like it),
# only their rules Open or Not Reviewed checked (-SelectVuln), each checklist sorted, a STIG STIG Manager does not have
# left out, report only, nothing kept with the drift report's scans; the email; an Evaluate-STIG without -SelectVuln (run
# in full); Check mode keeps nothing; the server's asset not there (a warning); only GET requests reached STIG Manager.
# Needs ansible-playbook, pwsh and python3.
#   bash tests/stig/run_win_stig_recheck_test.sh    (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
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
port_sm="${STIG_WIN_RECHECK_SM_PORT:-12597}" port_smtp="${STIG_WIN_RECHECK_SMTP_PORT:-12598}"
mkdir -p "$work/bin" "$work/mail" "$work/es/Evaluate-STIG" "$work/es_old/Evaluate-STIG"
cat > "$work/bin/powershell" <<SHIM
#!/bin/bash
exec "$pwsh" "\$@"
SHIM
chmod +x "$work/bin/powershell"; ln -s powershell "$work/bin/PowerShell"
# a copy of the repository: the local PowerShell connection leaves temporary files where it runs
mkdir -p "$work/repo" && (cd "$repo" && tar --exclude=.git -cf - .) | (cd "$work/repo" && tar -xf -)
cp "$repo/tests/windows/stig/Evaluate-STIG.ps1" "$work/es/Evaluate-STIG/"
# an Evaluate-STIG from before -SelectVuln
sed 's/SelectVuln/PickVuln/g' "$repo/tests/windows/stig/Evaluate-STIG.ps1" > "$work/es_old/Evaluate-STIG/Evaluate-STIG.ps1"
cat > "$work/inv.yml" <<INV
all:
  hosts:
    win01:
      ansible_connection: local
      ansible_shell_type: powershell
      ansible_python_interpreter: $py
INV
"$py" - > "$work/spec.json" <<'PY'
import json
def r(sv, sev, status, details=""):
    return {"sv": sv, "v": "V-" + sv[3:], "stigid": "WN22-00-" + sv[3:], "sev": sev, "title": "Rule %s must be set." % sv, "status": status, "details": details}
print(json.dumps({"host": "WIN01", "exit": 0, "checklists": [
    {"file": "WIN01_WinServer2022_V2R4", "stig_id": "Windows_Server_2022_STIG", "display_name": "Microsoft Windows Server 2022", "version": "2",
     "release": "Release: 4 Benchmark Date: 02 Jul 2026",
     "rules": [r("SV-254238", "high", "pass"), r("SV-254239", "medium", "fail", "Found: LegalNoticeText is empty."),
               r("SV-254240", "medium", "pass"), r("SV-254241", "low", "nr")]},
    {"file": "WIN01_IIS10Site_Default_Web_Site", "stig_id": "IIS_10-0_Site_STIG", "display_name": "Microsoft IIS 10.0 Site", "version": "2",
     "release": "Release: 9 Benchmark Date: 02 Jul 2026", "site": "Default Web Site", "instance": "",
     "rules": [r("SV-218735", "medium", "pass"), r("SV-218736", "medium", "pass")]},
    {"file": "WIN01_MSEdge_V2R2", "stig_id": "MS_Edge_STIG", "display_name": "Microsoft Edge", "version": "2", "release": "Release: 2 Benchmark Date: 02 Jul 2026",
     "rules": [r("SV-235719", "medium", "fail", "Found: SmartScreen is off.")]}]}))
PY
cp "$work/spec.json" "$work/es/Evaluate-STIG/"; cp "$work/spec.json" "$work/es_old/Evaluate-STIG/"
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
win_check_stig_evaluate: $work/es/Evaluate-STIG
win_check_stig_run_as: direct
win_check_stig_dir: $work/kept
win_check_stig_timeout: 120
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
sm_data() {  # sm_data ASSETS_JSON: the server's asset (Windows Server 2022), its web site's (IIS site) - no Edge
    "$py" - "$1" > "$work/sm.json" <<'PY'
import json, sys
def rv(b, sv, result, status, sev, title):
    rule = sv + "r1000_rule"
    return {"benchmarkId": b, "ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": status}, "ts": "2026-09-01T10:00:00Z",
            "rule": {"ruleId": rule, "version": "X-" + sv[3:], "severity": sev, "title": title}}
def rl(sv, sev, title):
    return {"ruleId": sv + "r1000_rule", "groupId": "V-" + sv[3:], "version": "X-" + sv[3:], "severity": sev, "title": title}
W, I = "Windows_Server_2022_STIG", "IIS_10-0_Site_STIG"
print(json.dumps({"secret": "s3cret", "client": "autostig", "collections": [{"collectionId": "1", "name": "Windows Servers"}],
    "assets": json.loads(sys.argv[1]),
    "reviews": {"21": [rv(W, "SV-254238", "fail", "accepted", "high", "LAN Manager authentication level must be set."),
                       rv(W, "SV-254239", "fail", "submitted", "medium", "The legal notice must be configured."),
                       rv(W, "SV-254240", "pass", "accepted", "medium", "Rule SV-254240.")],
                "22": [rv(I, "SV-218736", "fail", "accepted", "medium", "Directory browsing must be disabled.")],
                "23": [rv(I, "SV-218735", "fail", "accepted", "medium", "Another server's site.")]},
    "rules": {W: [rl("SV-254238", "high", "LAN Manager authentication level must be set."), rl("SV-254239", "medium", "The legal notice must be configured."),
                  rl("SV-254240", "medium", "Rule SV-254240."), rl("SV-254241", "low", "A manual check.")],
              I: [rl("SV-218735", "medium", "Logging must be on."), rl("SV-218736", "medium", "Directory browsing must be disabled.")]}}))
PY
}
assets='{"1": [{"assetId": "21", "name": "win01", "fqdn": "", "stigs": [{"benchmarkId": "Windows_Server_2022_STIG", "revisionStr": "V2R3"}]},
              {"assetId": "22", "name": "win01-Default Web Site-NA", "fqdn": "", "stigs": [{"benchmarkId": "IIS_10-0_Site_STIG", "revisionStr": "V2R9"}]},
              {"assetId": "23", "name": "win010-Default Web Site-NA", "fqdn": "", "stigs": [{"benchmarkId": "IIS_10-0_Site_STIG", "revisionStr": "V2R9"}]},
              {"assetId": "24", "name": "win01-old", "fqdn": "", "stigs": [{"benchmarkId": "IIS_10-0_Site_STIG", "revisionStr": "V2R9"}]}]}'
sm_data "$assets"

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$work/repo" && PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH=roles ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False timeout 600 "$ap" -i "$work/inv.yml" playbooks/win_stig_recheck.yml \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4716}' "${args[@]}") > "$work/out.txt" 2>&1 </dev/null
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "finding(s)\|\[WARNING\]\|FIXED\|ANSWER\|win01\|STIG Manager\|fatal\|ERROR" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
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

run "every STIG of the server's asset and its web site's: only their Open and Not Reviewed rules checked, sorted" ok -- \
    "HEALTHY. Checks run: stig" "5 rules Open or Not Reviewed in STIG Manager: V-218735, V-218736, V-254238, V-254239, V-254241" \
    "win01 - Microsoft Windows Server 2022: 2 Open, 1 Not Reviewed in STIG Manager - fixed 1, now n/a 0, answered 0, still open 1, by hand 1, left out 0; STIG Manager: Windows Servers / win01 (V2R3); checked: only these rules (-SelectVuln)" \
    "win01 - Microsoft IIS 10.0 Site - Default Web Site: 1 Open, 1 Not Reviewed in STIG Manager - fixed 1, now n/a 0, answered 1, still open 0, by hand 0, left out 0; STIG Manager: Windows Servers / win01-Default Web Site-NA (V2R9)" \
    "FIXED  V-254238 (X-254238) CAT I" "ANSWER V-218735 (X-218735) CAT II" "!Microsoft Edge" "!win010" "!win01-old" "!Traceback" "!is undefined"
if grep -q "SelectVuln=V-218735,V-218736,V-254238,V-254239,V-254241" "$work/es/Evaluate-STIG/args.txt" \
   && [ -s "$work/kept/recheck/Windows_Server_2022_STIG.cklb" ] && [ -s "$work/kept/recheck/IIS_10-0_Site_STIG_Default_Web_Site.cklb" ] \
   && [ ! -e "$work/kept/Windows_Server_2022_STIG" ]; then
    echo "ok   - Evaluate-STIG ran with -SelectVuln (the rules asked for); each checklist kept in recheck, nothing with the drift report's scans"
else echo "FAIL - Evaluate-STIG's arguments or what was kept"; cat "$work/es/Evaluate-STIG/args.txt"; ls -R "$work/kept" | head; fails=$((fails + 1)); fi
if [ "$(ls "$work/mail" | wc -l)" -ge 1 ]; then
    decoded "$(ls "$work/mail"/* | head -1)"; m="$work/mail.txt"
    if grep -q "Subject: .*Windows STIG recheck" "$m" && grep -q "1. Open in STIG Manager - the host passes now: update the review" "$m" \
       && grep -q "win01 - Microsoft IIS 10.0 Site - Default Web Site" "$m" && grep -q "Found: LegalNoticeText is empty." "$m"
    then echo "ok   - the email: what to update, each checklist named for its server and STIG, what Evaluate-STIG found for the still open"
    else echo "FAIL - the email's content"; grep -i "subject\|open in\|win01\|found:" "$m" | head -8; fails=$((fails + 1)); fi
else echo "FAIL - no email"; fails=$((fails + 1)); fi

run "an Evaluate-STIG without -SelectVuln: run in full, the same answers" ok -e win_check_stig_evaluate="$work/es_old/Evaluate-STIG" -- \
    "fixed 1, now n/a 0, answered 1, still open 0" "checked: in full - this Evaluate-STIG has no -SelectVuln"
grep -q "PickVuln=$" "$work/es_old/Evaluate-STIG/args.txt" && echo "ok   - ... no -SelectVuln given to it" \
    || { echo "FAIL - -SelectVuln given to an Evaluate-STIG without it"; cat "$work/es_old/Evaluate-STIG/args.txt"; fails=$((fails + 1)); }

rm -rf "$work/kept"
run "Check mode: checks and sorts, keeps nothing on the server" ok --check -- "fixed 1, now n/a 0, answered 0, still open 1" "nothing kept on the host (not kept: dry run)"
[ ! -e "$work/kept/recheck" ] && echo "ok   - ... nothing kept" || { echo "FAIL - Check mode kept a checklist"; fails=$((fails + 1)); }

sm_data '{"1": []}'
run "the server's asset not in STIG Manager: not rechecked, one warning" fail -- \
    "[WARNING] stig: not rechecked - what to recheck is read from STIG Manager: no asset named win01" "NOT RECHECKED"

if grep -qvE "^(GET |POST /token)" "$work/sm.log"; then echo "FAIL - a request that is not a GET reached STIG Manager"; grep -vE "^(GET |POST /token)" "$work/sm.log" | head -3; fails=$((fails + 1))
else echo "ok   - only GET requests reached STIG Manager ($(wc -l < "$work/sm.log") requests)"; fi

echo
if [ $fails -eq 0 ]; then echo "all Windows STIG recheck scenarios passed"; else echo "$fails Windows STIG recheck scenario(s) FAILED"; exit 1; fi
