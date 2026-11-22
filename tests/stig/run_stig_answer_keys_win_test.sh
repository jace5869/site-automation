#!/usr/bin/env bash
# STIG answer keys for Windows Server (1.105.0, playbooks/stig_answer_keys.yml) with no Windows: a stand-in host on this box (the local
# connection, ansible_shell_type: powershell, PowerShell 7 as "powershell", a CIM stand-in for the two classes win_os.ps1 reads - the same way
# tests/stig/run_win_stig_test.sh does it), the stand-in Evaluate-STIG (tests/windows/stig/Evaluate-STIG.ps1, -SelectVuln) with a Windows
# Server 2022 STIG of real DISA rules (tests/fixtures/stig_win/rules.json), the STIG Manager stand-in (tests/stig/stigman_mock.py: anything but
# a GET FAILS the test) and a stand-in auditpol.exe. Checks: the host is told apart (Windows Server 2022, its STIG by year; a 2016 is listed, no
# keys); Evaluate-STIG is run for the picked rules only (-SelectVuln) and nothing is kept; the blocks by fixed pattern (registry value, AuditPol),
# the rule no pattern fits is not drafted (ACT off); the trial ran in the restricted session (Constrained Language Mode, the proxies named in
# "Ran in"), What DISA checks, Matches Evaluate-STIG; the answer file of the STIG (STIGComments Name WinServer2022: Evaluate-STIG's own short
# name); the trial off marks the blocks not tried; nothing left behind on the host; only GET requests reached STIG Manager.
# The registry does not exist on Linux: the trial's registry reads fail, the codes say false - the pipeline is what is tested here (real Windows
# is needed for the values).
#   bash tests/stig/run_stig_answer_keys_win_test.sh      (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
pwsh=$(command -v pwsh) || { echo "SKIP - pwsh is not here"; exit 0; }
work="$(mktemp -d)"
smtp_pid="" sm_pid="" act_pid=""
cleanup() { [ -n "${AKW_KEEP:-}" ] && { mkdir -p "$AKW_KEEP"; cp "$work/sm.json" "$work/ids.json" "$work/es/Evaluate-STIG/spec.json" "$work/sm.log" "$AKW_KEEP/" 2>/dev/null; cp "$work/unpacked.html" "$AKW_KEEP/" 2>/dev/null; }
            [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; [ -n "$act_pid" ] && kill "$act_pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
port_sm="${AKW_SM_PORT:-12665}" port_smtp="${AKW_SMTP_PORT:-12666}" port_act="${AKW_ACT_PORT:-12667}"
mkdir -p "$work/act" "$work/actweb" "$work/bin" "$work/mail" "$work/web" "$work/es/Evaluate-STIG" "$work/tmp" "$work/kept"
cat > "$work/bin/powershell" <<SHIM
#!/bin/bash
exec "$pwsh" "\$@"
SHIM
chmod +x "$work/bin/powershell"; ln -s powershell "$work/bin/PowerShell"
cp "$here/win_ak_sim/bin/auditpol.exe" "$work/bin/"
mkdir -p "$work/repo" && (cd "$repo" && tar --exclude=.git -cf - .) | (cd "$work/repo" && tar -xf -)
cp "$repo/tests/windows/stig/Evaluate-STIG.ps1" "$work/es/Evaluate-STIG/"
# Simulate an installation with an existing answer file. The stand-in verifies -AFPath at scan time.
mkdir "$work/es/Evaluate-STIG/AnswerFiles"
echo '<STIGComments Name="WinServer2022"/>' > "$work/es/Evaluate-STIG/AnswerFiles/example.xml"
echo "'exampleReachMarker'" >> "$work/repo/roles/win_check_stig/files/win_os.ps1"
# the rules: real DISA text of Windows Server 2022 - two registry values, an AuditPol subcategory, one no pattern fits, one that passes
"$py" - "$work" "$repo" <<'PY'
import json, re, sys
work, repo = sys.argv[1:3]
sys.path.insert(0, repo + "/plugins/filter")
import stig_answer_filters as F
rules = [x for x in json.load(open(repo + "/tests/fixtures/stig_win/rules.json")) if x["stig"] == "WinServer2022"]
def has(x):
    d = F._draft_of({"v": x["v"], "sv": x["rule"], "check_text": x["check"], "title": x["title"], "severity": x["severity"]}, {}, True)
    return bool(d and d.get("kind") == "pattern")
reg = [x for x in rules if x["class"] == "registry value" and has(x)][:2]
aud = [x for x in rules if x["class"] == "AuditPol subcategory" and has(x)][:1]
oth = [x for x in rules if x["class"] == "other"][:1]
pas = [x for x in rules if x["class"] == "registry value" and has(x)][2:3]
picked = [(reg[0], "fail"), (reg[1], "nr"), (aud[0], "fail"), (oth[0], "fail"), (pas[0], "pass")]
sv = lambda x: re.match(r"(SV-\d+)", x["rule"]).group(1)
spec = {"require_empty_answers": True, "host": "WIN01", "exit": 0, "checklists": [{"file": "WIN01_WinServer2022_V2R11", "stig_id": "MS_Windows_Server_2022_STIG",
        "display_name": "Microsoft Windows Server 2022", "version": "2", "release": "Release: 11 Benchmark Date: 02 Jul 2026",
        "rules": [{"sv": sv(x), "v": x["v"], "stigid": "WN22-" + x["v"], "sev": x["severity"], "title": x["title"], "status": st,
                   "details": "client_secret=exampleChecklistMarker", "check": x["check"], "fix": x["fix"]} for x, st in picked]}]}
json.dump(spec, open(work + "/es/Evaluate-STIG/spec.json", "w"))
rule = lambda x: {"ruleId": sv(x) + "r1000_rule", "groupId": x["v"], "version": "WN22-" + x["v"], "severity": x["severity"], "title": x["title"],
                  "check": x["check"], "fix": x["fix"]}
rv = lambda x, res: {"ruleId": sv(x) + "r1000_rule", "ruleIds": [sv(x) + "r1000_rule"], "result": res, "status": {"label": "submitted"},
                     "ts": "2026-10-01T10:00:00Z", "rule": {"ruleId": sv(x) + "r1000_rule", "severity": x["severity"], "title": x["title"]}}
res = {"fail": "fail", "nr": "notchecked", "pass": "pass"}
sm = {"secret": "s3cret", "client": "autostig", "collections": [{"collectionId": "1", "name": "Windows Servers"}],
      "assets": {"1": [{"assetId": "21", "name": "win01", "fqdn": "", "stigs": [{"benchmarkId": "MS_Windows_Server_2022_STIG", "revisionStr": "V2R11"}]}]},
      "reviews": {"21": [dict(rv(x, res[st]), benchmarkId="MS_Windows_Server_2022_STIG") for x, st in picked]},
      "rules": {"MS_Windows_Server_2022_STIG": [rule(x) for x, st in picked]}}
json.dump(sm, open(work + "/sm.json", "w"))
json.dump({"v": [x["v"] for x, st in picked], "reg": [x["v"] for x in reg], "aud": aud[0]["v"], "oth": oth[0]["v"], "pas": pas[0]["v"]}, open(work + "/ids.json", "w"))
PY
ids() { "$py" -c "import json,sys; d=json.load(open('$work/ids.json')); print($1)"; }
cat > "$work/inv.yml" <<INV
all:
  hosts:
    win01:
      ansible_connection: local
      ansible_shell_type: powershell
      ansible_python_interpreter: $py
INV
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
"$py" -m http.server "$port_act" --bind 127.0.0.1 --directory "$work/actweb" >/dev/null 2>&1 & act_pid=$!   # ACT's address answers
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
answer_keys_act: false
answer_keys_trial: true
answer_keys_cross_check: false
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port_smtp
report_email_security: none
report_email_from: aap@example.mil
report_web_dest: $work/web
act_triage_script_src: $repo/tests/containers/fake_act.py
act_triage_poll: 1
site_act_url: http://127.0.0.1:$port_act/v1/chat/completions
EOF
run() {  # run NAME ok|fail [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -f "$work/mail"/* "$work/es/Evaluate-STIG/args.txt"
    (cd "$work/repo" && TMPDIR="$work/tmp" FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" GENAI_KEY=test-key PATH="$work/bin:$PATH" PSModulePath="$here/win_ak_sim/Modules:${PSModulePath:-}" ANSIBLE_ROLES_PATH=roles:vendor/act/ansible/roles \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        timeout 900 "$ap" -i "$work/inv.yml" playbooks/stig_answer_keys.yml -e "@$work/settings.yml" -e '{"awx_job_id": 4903}' "${args[@]}") > "$work/out.txt" 2>&1 </dev/null
    local rc=$?
    [ -n "${AKW_KEEP:-}" ] && { mkdir -p "$AKW_KEEP"; cp "$work/out.txt" "$AKW_KEEP/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-40).txt"; }
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "STIG answer keys:\|NOT DRAFTED\|ready\|HOST \|fatal\|ERROR\|msg" "$work/out.txt" | cut -c1-300 | head -40; fails=$((fails + 1)); fi
}
lastpage() { command grep -rl 'Copy block' "$work/web" --include='*.html' 2>/dev/null | xargs -r ls -t | head -1; }
unpacked() { "$py" "$repo/tests/email/unpack_deferred.py" "$1" > "$work/unpacked.html" && echo "$work/unpacked.html"; }
pagetext() { "$py" -c 'import html, re, sys; print(html.unescape(re.sub(r"<[^>]+>", " ", open(sys.argv[1]).read())))' "$(unpacked "$1")" | tr -s ' \n' ' '; }

# 1. the run: Windows Server 2022, the automatic pick (STIG Manager's Open and Not Reviewed), the trial in the restricted session
run "a Windows Server 2022 host: its STIG by year, the blocks by pattern, the trial, the job green" ok -v -e target=win01 -e answer_keys_pick=both -- \
    "STIG answer keys: 3 ready to copy, 1 not drafted - 1 STIG, 1 host" "Windows Server 2022 V2R11: 3 ready to copy, 1 not drafted (hosts: win01)" "!fatal: [win01]" "!exampleReachMarker" "!exampleChecklistMarker"
sel="$(grep -o 'SelectVuln=.*' "$work/es/Evaluate-STIG/args.txt" 2>/dev/null | head -1)"
want="$("$py" -c "import json; print(','.join(sorted(json.load(open('$work/ids.json'))['v'][:4])))")"
got="${sel#SelectVuln=}"; got="$(printf '%s' "$got" | tr ',' '\n' | sort | paste -sd, -)"
if [ "$got" = "$want" ]; then echo "ok   - Evaluate-STIG was run for the picked rules only (-SelectVuln: the four Open and Not Reviewed, not the one that passes)"
else echo "FAIL - -SelectVuln was '$got', wanted '$want'"; fails=$((fails + 1)); fi
baseline="$(sed -n 's/^OutputPath=//p' "$work/es/Evaluate-STIG/args.txt")"
answer_dir="$(sed -n 's/^AFPath=//p' "$work/es/Evaluate-STIG/args.txt")"
if [[ "$baseline" == */run-*/out && "$answer_dir" == "${baseline%/out}/AnswerFiles" && ! -e "${baseline%/out}" && -f "$work/es/Evaluate-STIG/AnswerFiles/example.xml" ]]; then
    echo "ok   - baseline used and cleaned up its own empty answer directory"
else echo "FAIL - baseline answer directory or cleanup"; fails=$((fails + 1)); fi
page="$(lastpage)"; t="$(pagetext "$page")"
for w in "Windows Server 2022 V2R11 - answer keys to copy (3)" "pattern - registry value" "pattern - AuditPol subcategory" "What DISA checks" "Ran in" \
         "Constrained Language Mode" "secedit.exe, Remove-Item, auditpol.exe" "Matches Evaluate-STIG" "Trial · matches Evaluate-STIG" \
         "Windows Server 2022 V2R11 - not drafted (1)" "no fixed pattern fits and ACT is off" "Copy the answer file for Windows Server 2022 V2R11" \
         "restricted PowerShell session"; do
    case "$t" in *"$w"*) true ;; *) echo "FAIL - the website: missing '$w'"; fails=$((fails + 1)) ;; esac
done
case "$t" in *"systemd-run"*) echo "FAIL - the website says systemd-run for a Windows STIG"; fails=$((fails + 1)) ;; *) echo "ok   - the website: the Windows pattern, DISA's check, the restricted session named with its proxies; no sandbox words of Linux" ;; esac
if "$py" - "$(unpacked "$page")" "$work/ids.json" <<'PY'
import html, json, re, sys
import xml.etree.ElementTree as ET
t = open(sys.argv[1]).read()
ids = json.load(open(sys.argv[2]))
files = [html.unescape(m) for m in re.findall(r'data-copy="([^"]*)"', t) if html.unescape(m).startswith("<?xml")]
root = ET.fromstring(files[0].encode())
vulns = [v.get("ID") for v in root.iter("Vuln")]
ok = root.get("Name") == "WinServer2022" and sorted(vulns) == sorted(ids["v"][:3]) and "ConstrainedLanguage" not in files[0]
print(root.get("Name"), vulns)
sys.exit(0 if ok else 1)
PY
then echo "ok   - the answer file of the STIG parses: STIGComments Name WinServer2022 (Evaluate-STIG's own short name), a <Vuln ID> for each block"
else echo "FAIL - the answer file"; fails=$((fails + 1)); fi
if [ -z "$(ls -A "$work/kept" 2>/dev/null)" ]; then echo "ok   - nothing was kept on the host (win_check_stig_dir is empty)"; else echo "FAIL - kept: $(ls "$work/kept")"; fails=$((fails + 1)); fi
left="$(find "$work/tmp" -mindepth 1 -name 'site-ak-*' 2>/dev/null | head -3)"
if [ -z "$left" ]; then echo "ok   - no work folder of the restricted session is left behind"; else echo "FAIL - left: $left"; fails=$((fails + 1)); fi
bad="$(grep -E '(PUT|PATCH|DELETE) ' "$work/sm.log" 2>/dev/null | head -3)"
if [ -z "$bad" ]; then echo "ok   - STIG Manager was only read (no PUT, PATCH or DELETE)"; else echo "FAIL - STIG Manager: $bad"; fails=$((fails + 1)); fi

# 2. the trial off: the blocks marked not tried
run "answer_keys_trial false: the read-only check only, the blocks marked not tried" ok -e target=win01 -e answer_keys_pick=both -e answer_keys_trial=false -- \
    "STIG answer keys: 3 ready to copy, 1 not drafted - 1 STIG, 1 host" "(not tried)"
second_out="$(sed -n 's/^OutputPath=//p' "$work/es/Evaluate-STIG/args.txt")"
if [[ "${baseline%/out}" =~ /run-[[:xdigit:]-]{36}$ && "$second_out" != "$baseline" ]]; then
    echo "ok   - each baseline gets its own run directory"
else echo "FAIL - expected distinct GUID run directories"; fails=$((fails + 1)); fi
# 4. the cross-check on Windows: the evidence is what each pattern read in the trial's restricted session. On this stand-in the registry does not exist, so
# every pattern's reading FAILS - and a failed reading is no evidence (review round): not read, ACT never asked (the positive path - ACT told it is a
# Windows Server host, given the reading and never the code - is in tests/test_stig_answer_win_host.py)
printf '%s' '{"summary": "Open\nThe registry value cannot be read.\nread: failed"}' > "$work/act/act.json"; rm -f "$work/act/fake-act-calls.log"
run "the cross-check on Windows: a failed reading is no evidence - not read, ACT not asked" ok -e target=win01 -e answer_keys_pick=both -e answer_keys_cross_check=true -- \
    "STIG answer keys: 3 ready to copy, 1 not drafted - 1 STIG, 1 host" "Cross-check: 0 agree, 0 need a look, 3 too little to compare"
n="$(grep -c call "$work/act/fake-act-calls.log" 2>/dev/null)"
if [ "${n:-0}" = 0 ]; then echo "ok   - ... ACT was not asked: no reading, no evidence"; else echo "FAIL - ACT was asked $n times"; fails=$((fails + 1)); fi
t="$(pagetext "$(lastpage)")"
for want in "Cross-check by STIG" "Cross-check — Windows Server 2022 V2R11 (3 rules)" "the pattern's reading failed on the trial host"; do
    case "$t" in *"$want"*) true ;; *) echo "FAIL - the cross-check page: missing '$want'"; fails=$((fails + 1)) ;; esac
done
# 5. review round: the scan's output is pinned to CKLB whatever win_check_stig_evaluate_output says (STIGManager would submit results)
run "win_check_stig_evaluate_output: STIGManager is not passed on: Evaluate-STIG is asked for CKLB" ok -e target=win01 -e answer_keys_pick=both -e win_check_stig_evaluate_output=STIGManager -- \
    "STIG answer keys: 3 ready to copy, 1 not drafted - 1 STIG, 1 host"
if grep -q '^Output=CKLB$' "$work/es/Evaluate-STIG/args.txt" 2>/dev/null; then echo "ok   - ... Evaluate-STIG got Output=CKLB"; else echo "FAIL - Evaluate-STIG's arguments: $(cat "$work/es/Evaluate-STIG/args.txt" 2>/dev/null | head -3)"; fails=$((fails + 1)); fi
# 6. a rule a person NAMED keeps the review protections: its review has RA words, so it is not drafted (and never scanned)
cp "$work/sm.json" "$work/sm.json.keep"
"$py" - "$work/sm.json" "$work/ids.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); ids = json.load(open(sys.argv[2]))
d["reviews"]["21"][0]["comment"] = "Accepted by the AO: RA 2026-04 in place"
json.dump(d, open(sys.argv[1], "w"))
PY
first="$("$py" -c "import json; print(json.load(open('$work/ids.json'))['v'][0])")"; second="$("$py" -c "import json; print(json.load(open('$work/ids.json'))['v'][1])")"
run "a named rule with an RA review is left out (the protections of the automatic pick), the other kept" ok -e target=win01 -e "{\"answer_keys_rules\": [\"$first\", \"$second\"]}" -- \
    "STIG answer keys: 1 ready to copy"
sel="$(grep -o 'SelectVuln=.*' "$work/es/Evaluate-STIG/args.txt" 2>/dev/null | head -1)"
case "$sel" in *"$first"*) echo "FAIL - the protected rule was scanned: $sel"; fails=$((fails + 1)) ;; *"$second"*) echo "ok   - ... Evaluate-STIG was run for the unprotected rule only ($sel)" ;; *) echo "FAIL - -SelectVuln: '$sel'"; fails=$((fails + 1)) ;; esac
cp "$work/sm.json.keep" "$work/sm.json"
"$py" - "$work/sm.json" <<'PYTHON'
import json, sys
d = json.load(open(sys.argv[1]))
d["reviews"]["21"][0]["metadata"] = {"artifacts": json.dumps([{"filename": "example.txt"}])}
json.dump(d, open(sys.argv[1], "w"))
PYTHON
# Distinguish the simulated Windows connection from the delegated task's "local" connection.
sed -i 's/ansible_connection: local/ansible_connection: ansible.builtin.lo''cal/' "$work/inv.yml"
cat > "$work/connection-test.yml" <<'YAML'
_ak_windows: >-
  {{ ansible_connection == 'ansible.builtin.' ~ 'local' }}
YAML
run "named attachment on the target with a delegated local lookup" ok -e target=win01 \
    -e "@$work/connection-test.yml" \
    -e "{\"answer_keys_rules\": [\"$first\", \"$second\"]}" -- "STIG answer keys: 1 ready to copy"
if grep -qx "SelectVuln=$second" "$work/es/Evaluate-STIG/args.txt"; then echo "ok   - attached named rule excluded"
else echo "FAIL - attached named rule selection"; fails=$((fails + 1)); fi
cp "$work/sm.json.keep" "$work/sm.json"
# 7. a Windows 11 (build 26100 is also Server 2025's): a client, no answer keys
FAKE_WIN_CAPTION="Microsoft Windows 11 Enterprise" FAKE_WIN_BUILD=26100 FAKE_WIN_PRODUCT_TYPE=1 run "a Windows 11 client alone: no answer keys" fail -e target=win01 -- \
    "no host worked - win01:"
# 8. an Evaluate-STIG without -SelectVuln is never run in full
mkdir -p "$work/es2/Evaluate-STIG" && sed 's/SelectVuln/SelVln/g' "$work/es/Evaluate-STIG/Evaluate-STIG.ps1" > "$work/es2/Evaluate-STIG/Evaluate-STIG.ps1" && cp "$work/es/Evaluate-STIG/spec.json" "$work/es2/Evaluate-STIG/"
run "an Evaluate-STIG with no -SelectVuln: not run in full, the host listed, the job stops" fail -e target=win01 -e answer_keys_pick=both -e "win_check_stig_evaluate=$work/es2/Evaluate-STIG" -- \
    "never run it in full"

# 3. a Windows that is not 2019, 2022 or 2025 gets no keys, said
FAKE_WIN_CAPTION="Microsoft Windows Server 2016 Standard" FAKE_WIN_BUILD=14393 run "a Windows Server 2016 alone: said, no answer keys, the job stops (no host worked)" fail -e target=win01 -e answer_keys_pick=both -- \
    "no host worked - win01: Microsoft Windows Server 2016 Standard is not a Windows Server 2019, 2022 or 2025 (build 14393): no answer keys for it"

# A WinRM scan that lost its JSON record can leave an unlabelled credential fragment in stderr.
cat > "$work/repo/roles/win_check_stig/files/win_stig.ps1" <<'PS'
[Console]::Error.WriteLine('exampleScannerTail')
exit 1
PS
run "scan failure omits raw stderr" fail -v -e target=win01 -e answer_keys_pick=both -- \
    "scan script gave no result" "!exampleScannerTail"

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
[ "$fails" = 0 ]
