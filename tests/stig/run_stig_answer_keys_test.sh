#!/usr/bin/env bash
# STIG answer keys (1.75.0, playbooks/stig_answer_keys.yml) on this box as the trial host (the local connection, no
# sudo): a fake Evaluate-STIG_Bash.sh (writes the CKLB tests/stig/make_cklb.py makes; an Evaluate-STIG.ps1 with its
# version and an answer file template beside it), the STIG Manager stand-in (tests/stig/stigman_mock.py: any request that
# is not a GET - but the token's POST - FAILS the test), a fake ACT (tests/containers/fake_act.py) and pwsh. Checks: a
# pattern-made block (stat, true), a rule that needs a site value (min_release) - then given (false: "keeps it Open"),
# ACT's draft offered and marked, ACT's draft the read-only check refuses (iex), a rule Not a Finding in STIG Manager, a
# rule Evaluate-STIG passes; Evaluate-STIG given --SelectVuln, never --AFPath or the site's --AnswerKey; the template's
# element names, the documented ones; one host only; ACT off. 1.76.0: the trial in its read-only sandbox (systemd-run;
# tests/stig/sandbox_trial.py runs writes in it with the check left out), no sandbox - no trial.
#   bash tests/stig/run_stig_answer_keys_test.sh     (ANSIBLE_PLAYBOOK=... to pick one; AK_KEEP=DIR keeps the outputs)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
command -v pwsh >/dev/null || { echo "SKIP - pwsh is not here (the read-only check and the trial need PowerShell 7)"; exit 0; }
work="$(mktemp -d)"
# 1.76.0: the trial runs in a read-only sandbox with its own /tmp - a file the trial reads lives outside /tmp
mkdir -p "$HOME/.cache" && fx="$(mktemp -d "$HOME/.cache/site-ak-e2e-XXXXXX")"
smtp_pid="" sm_pid="" act_pid=""
cleanup() { [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; [ -n "$act_pid" ] && kill "$act_pid" 2>/dev/null; rm -rf "$work" "${fx:?}"; }
trap cleanup EXIT
fails=0
port_sm="${AK_SM_PORT:-12641}" port_smtp="${AK_SMTP_PORT:-12642}" port_act="${AK_ACT_PORT:-12643}"
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/es/AnswerFiles" "$work/mail" "$work/act" "$work/web"
echo '{"SV-258999": "not_reviewed", "SV-257835": "not_reviewed", "SV-257985": "open", "SV-257883": "open", "SV-257821": "open", "SV-257779": "open"}' > "$work/es_results.json"
cat > "$work/es/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# --SelectVuln (this Evaluate-STIG has it)
out=""; echo "\$*" >> "$work/es_args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; esac; shift; done
mkdir -p "\$out/WEB01/Checklist" && "$py" "$here/make_cklb.py" "\$out/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb" "\$(cat "$work/es_results.json")" V2R10
echo "Done"
EOF
chmod +x "$work/es/Evaluate-STIG_Bash.sh"
printf "@{ ModuleVersion = '1.2607.0' }\n" > "$work/es/Evaluate-STIG.psd1"
mkdir -p "$work/es/Modules/Answers"
cat > "$work/es/Modules/Answers/Answers.psm1" <<'EOF'
# where an answer is used (as Evaluate-STIG does): its ExpectedStatus is its own status, then its ValidationCode
if ($AnswerObject.ExpectedStatus -eq $Status) { $Valid = & ([scriptblock]::Create($AnswerObject.ValidationCode)) }
EOF
cat > "$work/es/AnswerFiles/AnswerFile_Template.xml" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<STIGComments Name="Template">
  <Vuln ID="V-000000">
    <AnswerKey Name="DEFAULT">
      <ExpectedStatus>Not_Reviewed</ExpectedStatus>
      <ValidationCode />
      <ValidTrueStatus />
      <ValidTrueComment />
      <ValidFalseStatus />
      <ValidFalseComment />
    </AnswerKey>
  </Vuln>
</STIGComments>
EOF
printf 'PermitRootLogin no\n' > "$fx/sshd_config"
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
# ACT's model URL: any HTTP answer shows the way is open (plugins/action/site_act_ask.py); the fake ACT answers itself
mkdir -p "$work/actweb"
"$py" -m http.server "$port_act" --bind 127.0.0.1 --directory "$work/actweb" >/dev/null 2>&1 & act_pid=$!
sleep 1
rv() {  # rv SV RESULT STATUS TITLE
    printf '{"ruleId": "%sr1_rule", "ruleIds": ["%sr1_rule"], "result": "%s", "status": {"label": "%s"}, "ts": "2026-10-01T10:00:00Z", "rule": {"ruleId": "%sr1_rule", "version": "RHEL-09-0", "severity": "medium", "title": "%s"}}' "$1" "$1" "$2" "$3" "$1" "$4"
}
cat > "$work/sm.json" <<EOF
{"secret": "s3cret", "client": "autostig",
 "collections": [{"collectionId": "1", "name": "RHEL Servers"}],
 "assets": {"1": [{"assetId": "11", "name": "localhost", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R7"}]}]},
 "reviews": {"11": [$(rv SV-257821 pass accepted "GPG check of local packages."), $(rv SV-257985 fail submitted "No root login over SSH."),
                    $(rv SV-257883 fail submitted "Sessions end after 10 minutes.")]},
 "rules": {"RHEL_9_STIG": [
   {"ruleId": "SV-258999r1_rule", "groupId": "V-258999", "version": "RHEL-09-999990", "severity": "medium", "title": "The host name file must be owned by root.",
    "check": "Verify the ownership of /etc/hostname with the following command:\\n\\n\$ sudo stat -c \"%U %n\" /etc/hostname\\n\\nroot /etc/hostname\\n\\nIf /etc/hostname does not have an owner of \"root\", this is a finding.", "fix": ""},
   {"ruleId": "SV-257835r1_rule", "groupId": "V-257835", "version": "RHEL-09-211010", "severity": "high", "title": "RHEL 9 must be a vendor-supported release.",
    "check": "Verify the version of RHEL 9 is vendor supported with the following command:\\n\\n\$ cat /etc/redhat-release\\n\\nRed Hat Enterprise Linux release 9.6 (Plow)\\n\\nIf the installed version of RHEL 9 is not supported, this is a finding.", "fix": ""},
   {"ruleId": "SV-257985r1_rule", "groupId": "V-257985", "version": "RHEL-09-255045", "severity": "medium", "title": "No root login over SSH.",
    "check": "Verify RHEL 9 does not allow a root login over SSH:\\n\\n\$ sudo grep -ir PermitRootLogin /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*\\n\\nPermitRootLogin no\\n\\nIf it is not no, this is a finding.", "fix": ""},
   {"ruleId": "SV-257883r1_rule", "groupId": "V-257883", "version": "RHEL-09-412035", "severity": "medium", "title": "Sessions end after 10 minutes.",
    "check": "Verify the session timeout:\\n\\n\$ grep -i tmout /etc/profile.d/*\\n\\nTMOUT=600\\n\\nIf it is not set, this is a finding.", "fix": ""},
   {"ruleId": "SV-257821r1_rule", "groupId": "V-257821", "version": "RHEL-09-214020", "severity": "high", "title": "GPG check of local packages.", "check": "Check it.", "fix": ""},
   {"ruleId": "SV-258176r1_rule", "groupId": "V-258176", "version": "RHEL-09-654225", "severity": "medium", "title": "Audit privileged commands.", "check": "Check it.", "fix": ""},
   {"ruleId": "SV-257779r1_rule", "groupId": "V-257779", "version": "RHEL-09-211020", "severity": "medium", "title": "The journal service must be active.",
    "check": "Verify the journal service is active with the following command:\\n\\n\$ systemctl is-active systemd-journald\\n\\nactive\\n\\nIf the systemd-journald service is not active, this is a finding.", "fix": ""}]}}
EOF
cat > "$work/settings.yml" <<EOF
check_stig_scanner: evaluate_stig
check_stig_evaluate: $work/es
check_stig_evaluate_select: {"$major": RHEL9}
check_stig_evaluate_args: ["--AnswerKey", "SITEA", "--Output", "STIGManager"]
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
check_stig_dir: $work/kept
answer_keys_cross_check: false          # 1.77.0: this test counts ACT's drafting calls; the cross-check: run_stig_answer_keys_hosts_test.sh
act_triage_script_src: $repo/tests/containers/fake_act.py
act_triage_poll: 1
site_act_url: http://127.0.0.1:$port_act/v1/chat/completions
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port_smtp
report_email_security: none
report_email_from: aap@example.mil
report_web_dest: $work/web
target: localhost
answer_keys_trial: true
EOF
all6='{"answer_keys_rules": ["V-258999", "SV-257835", "V-257985", "V-257883", "V-257821", "V-258176"]}'
act() { printf '%s' "$1" > "$work/act/act.json"; rm -f "$work/act/fake-act-calls.log"; }
ACT_OK='Here it is.\n```powershell\n$l = @(Get-Content -LiteralPath '"'"$fx/sshd_config"'"' | Where-Object { $_ -match '"'"'^\\s*PermitRootLogin\\b'"'"' })\nWrite-Verbose ('"'"'read: '"'"' + ($l -join '"'"' | '"'"'))\nreturn ($l.Count -eq 1 -and $l[0] -match '"'"'\\bno$'"'"')\n```\nIt checks PermitRootLogin.'
ACT_BAD='```powershell\niex '"'"'Get-Content /etc/profile'"'"'\nreturn $true\n```'
run() {  # run NAME ok|fail [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -f "$work/mail"/*
    (cd "$repo" && TMPDIR="$work/tmp" ANSIBLE_REMOTE_TMP="$work/rtmp" ANSIBLE_LOCAL_TEMP="$work/ltmp" \
        FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" GENAI_KEY=test-key \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "${AK_INV:-localhost,}" -c local playbooks/stig_answer_keys.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4900}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    [ -n "${AK_KEEP:-}" ] && { mkdir -p "$AK_KEEP"; cp "$work/out.txt" "$AK_KEEP/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-50).txt"; }
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "STIG answer keys:\|NOT DRAFTED\|ready\|ACT draft\|fatal\|ERROR\|msg" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}
# 1.100.0: a rule's block is a defer-detail block built when its row opens - the page unpacked as site-report.js does
unpacked() {  # unpacked FILE: the page with its deferred blocks built (tests/email/unpack_deferred.py), its path
    "$py" "$repo/tests/email/unpack_deferred.py" "$1" > "$work/unpacked.html" && echo "$work/unpacked.html"
}
pagetext() {  # pagetext FILE: the page's words as people see them (its deferred blocks built), its tags left out
    "$py" -c 'import html, re, sys; print(html.unescape(re.sub(r"<[^>]+>", "", open(sys.argv[1]).read())))' "$(unpacked "$1")" | tr -s ' \n' ' '
}
# the job's temporary folders in its own places (other jobs on this box use /tmp too): what is left there is the job's
mkdir -p "$work/tmp" "$work/rtmp" "$work/ltmp"
leftovers() { (cd "$work" && find tmp rtmp ltmp -mindepth 1 -maxdepth 1 2>/dev/null) | sort; }
before_left="$(leftovers)"
optact_before="$(stat -c '%Y %i' /opt/act 2>/dev/null; ls -la --time-style=+%s /opt/act 2>/dev/null | md5sum)"
untouched() {  # untouched NAME: the trial host as it was - no folder of the job left, no ACT installed or changed
    local now; sleep 3; now="$(leftovers)"      # (ACT's own async folder, on the AAP side - here the same box - may go a moment later)
    if [ "$now" = "$before_left" ] && [ ! -e "$work/actdir" ] && [ ! -e "$work/kept" ] \
       && [ "$(stat -c '%Y %i' /opt/act 2>/dev/null; ls -la --time-style=+%s /opt/act 2>/dev/null | md5sum)" = "$optact_before" ]
    then echo "ok   - $1: the trial host as it was (its temporary folders removed, no ACT installed or changed, nothing kept)"
    else echo "FAIL - $1: the trial host changed: $(comm -13 <(echo "$before_left") <(echo "$now") | head -3) $(ls -d "$work/actdir" "$work/kept" 2>/dev/null)"; fails=$((fails + 1)); fi
}
writes() {  # any request to STIG Manager but a GET (and the token's POST) fails the test
    if grep -qvE "^(GET |POST /token)" "$work/sm.log"; then echo "FAIL - $1: a request that is not a GET reached STIG Manager"; grep -vE "^(GET |POST /token)" "$work/sm.log" | head -3; fails=$((fails + 1))
    else echo "ok   - $1: only GET requests reached STIG Manager ($(wc -l < "$work/sm.log") requests)"; fi
}

# the rules are asked at the same time (site_act_concurrency): each answer goes to its rule by its V- number
act "{\"summaries_by\": {\"V-257985\": $(printf '%s' "$ACT_OK" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))'), \"V-257883\": $(printf '%s' "$ACT_BAD" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))')}}"
run "six rules: a pattern's block, ACT's block, one needs a value, ACT's refused, one Not a Finding, one Evaluate-STIG passes" ok -e "$all6" -- \
    "STIG answer keys: 2 ready to copy, 4 not drafted" \
    "V-258999 ready: A manual check: a document." "V-257985 ACT draft: RHEL 9 must not permit direct logons to the root account using remote access through SSH." \
    "V-257835 NOT DRAFTED: Not drafted - needs answer_keys_values.min_release (the RHEL 9 release the site supports, at least). Add to the template's Variables: answer_keys_values: {min_release:" \
    "V-257883 NOT DRAFTED: Not drafted - the read-only check refused the draft ACT wrote: it runs iex (not one of the allowed commands)" \
    "V-257821 NOT DRAFTED: Not a Finding in STIG Manager (accepted): answer keys are for Open and Not Reviewed rules only." \
    "V-258176 NOT DRAFTED: Evaluate-STIG's own check says Not a Finding on the trial host: no answer key is needed." \
    '<AnswerKey Name=\"SITEA\">' "<ExpectedStatus>Not_Reviewed</ExpectedStatus>" "<ExpectedStatus>Open</ExpectedStatus>" \
    "<!-- drafted by ACT - read it before you use it -->" "site-automation answer key (ACT draft): the ValidationCode returned true" \
    "site-automation answer key: /etc/hostname is owned by root (stat)" "!Traceback"
writes "ACT drafting included"
untouched "ACT drafting included"
args=$(cat "$work/es_args.txt")
case "$args" in *--SelectVuln*V-258999*) ok1=1 ;; *) ok1=0 ;; esac
if [ $ok1 = 1 ] && ! grep -q -- "--AFPath\|SITEA\|STIGManager" "$work/es_args.txt" && [ ! -e "$work/kept" -o -z "$(ls -A "$work/kept" 2>/dev/null)" ]
then echo "ok   - Evaluate-STIG: only these rules (--SelectVuln), no answer files, not the site's --AnswerKey or --Output; nothing kept"
else echo "FAIL - Evaluate-STIG's run: $args; kept: $(ls -A "$work/kept" 2>/dev/null)"; fails=$((fails + 1)); fi
[ "$(grep -c call "$work/act/fake-act-calls.log" 2>/dev/null)" = 2 ] && echo "ok   - ACT asked for the 2 rules no pattern fits, once each" \
    || { echo "FAIL - ACT calls: $(cat "$work/act/fake-act-calls.log" 2>/dev/null | wc -l)"; fails=$((fails + 1)); }
grep -q "DISA's check text:" "$work/act/fake-act-task.txt" && grep -q "Put the code alone in one \`\`\`powershell block" "$work/act/fake-act-task.txt" \
    && grep -q "DISA check text of V-25" "$work/act/fake-act-stdin.txt" \
    && echo "ok   - ACT was given DISA's check text and the rules of the code - evidence mode on the AAP side (it runs no command)" || { echo "FAIL - ACT's task"; fails=$((fails + 1)); }
page="$(command grep -rl 'Copy block' "$work/web" --include='*.html' | head -1)"
[ -n "$page" ] && grep -q 'class="defer-detail"' "$page" && page="$(unpacked "$page")"
if [ -n "$page" ] && grep -q 'data-copy="&lt;AnswerKey Name=&#34;SITEA&#34;&gt;\|data-copy="&lt;AnswerKey Name=&quot;SITEA&quot;&gt;' "$page" \
   && grep -q "drafted by ACT - read it before you use it" "$page" && grep -q "Evaluate-STIG compares an answer&#39;s ExpectedStatus\|Evaluate-STIG compares an answer's ExpectedStatus" "$page" \
   && grep -q "the template&#39;s element names\|the template's element names" "$page"
then echo "ok   - the website: the blocks with Copy block, ACT's marked, the template's names, ExpectedStatus compared (Modules/Answers/Answers.psm1:2)"
else echo "FAIL - the website's page"; ls -R "$work/web" | head; fails=$((fails + 1)); fi
m="$(ls "$work/mail"/* 2>/dev/null | head -1)"
if [ -n "$m" ] && "$py" - "$m" <<'PY'
import email, sys
msg = email.message_from_binary_file(open(sys.argv[1], "rb"))
text = "".join(p.get_payload(decode=True).decode("utf-8", "replace") for p in msg.walk() if p.get_content_type() in ("text/plain", "text/html"))
names = [p.get_filename() or "" for p in msg.walk()]
ok = "answer keys to copy (2)" in text and "not drafted (4)" in text and "<ValidationCode>" not in text and any(n.endswith(".xlsx") for n in names)
sys.exit(0 if ok else 1)
PY
then echo "ok   - the email: the two tables, the blocks only in the workbook (and on the website)"
else echo "FAIL - the email"; fails=$((fails + 1)); fi

# 1.137.0, model tiers (fake_act.py's by_model): the worker drafts each rule; its draft with no code (V-257985) is asked of the
# smart model once, whose block is offered; the worker's other draft (V-257883) stays the worker's. The report's last lines.
J() { printf '%s' "$1" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))'; }
act "{\"summary\": \"no model set\", \"by_model\": {\"worker-ak\": {\"summaries_by\": {\"V-257985\": \"I cannot write that.\", \"V-257883\": $(J "$ACT_BAD")}},
      \"smart-ak\": {\"summaries_by\": {\"V-257985\": $(J "$ACT_OK")}}}}"
rm -f "$work/act/fake-act-models.log"
run "model tiers: the worker drafts, a draft with no code goes to the smart model once" ok -e "$all6" \
    -e '{"site_act_models": {"genai": "smart-ak"}, "site_act_worker_models": {"genai": "worker-ak"}}' -- \
    "STIG answer keys: 2 ready to copy, 4 not drafted" "V-257985 ACT draft: RHEL 9 must not permit direct logons" \
    "Drafts: ACT models (site_act_worker_model): 1 item(s) answered by the worker model (worker-ak), 1 by the smart model (smart-ak), 1 of them re-asked after the worker (1 unreadable)." \
    "the read-only check refused the draft ACT wrote: it runs iex"
w="$(grep -c '"worker-ak"' "$work/act/fake-act-models.log" 2>/dev/null)"; s="$(grep -c '"smart-ak"' "$work/act/fake-act-models.log" 2>/dev/null)"
[ "$w" = 2 ] && [ "$s" = 1 ] && echo "ok   - ...the worker drafted 2 rules, the smart model 1 (the one with no code)" \
    || { echo "FAIL - ...calls by model: worker $w, smart $s"; fails=$((fails + 1)); }
untouched "model tiers"

act '{"summary": "no code"}'
# as a survey gives them: texts - the rules with commas and spaces, the values as a name: value line, ACT off as "false"
# 1.76.1c: the release rule's block reads /etc/redhat-release - on a host without it (CI's Ubuntu runner) its trial fails
# there, and the key is not drafted, said why
if [ -e /etc/redhat-release ]; then
    rel_want=("STIG answer keys: 2 ready to copy, 4 not drafted" "V-257835 ready: RHEL 9 must not have the telnet-server package installed." "the site's supported minimum")
else
    rel_want=("STIG answer keys: 1 ready to copy, 5 not drafted" "V-257835 NOT DRAFTED: Not drafted - the trial on the host failed: Cannot find path '/etc/redhat-release'")
fi
run "the site value given (a survey's text), ACT off: the release rule offered (false - it keeps it Open), the rest by hand" ok \
    -e '{"answer_keys_rules": "V-258999, SV-257835 V-257985,V-257883 V-257821 V-258176", "answer_keys_values": "min_release: 9.4", "answer_keys_act": "false"}' -- \
    "${rel_want[@]}" \
    "V-257985 NOT DRAFTED: Not drafted - no fixed pattern fits and ACT is off (answer_keys_act false): write this one by hand." \
    "!V-257985 ACT draft"
[ -e "$work/act/fake-act-calls.log" ] && { echo "FAIL - ACT was asked with answer_keys_act false"; fails=$((fails + 1)); } || echo "ok   - ACT not asked with answer_keys_act false"
page="$(command grep -rl 'Copy block' "$work/web" --include='*.html' | xargs -r ls -t | head -1)"
t="$(pagetext "$page")"
if [ -e /etc/redhat-release ]; then
    case "$t" in *"Open - the host fails it now: this key keeps it Open"*"answer_keys_values.min_release = 9.4"*) true ;; *) false ;; esac \
        && echo "ok   - the website: the false trial keeps it Open, the site value each block used" || { echo "FAIL - false trial / value on the page"; fails=$((fails + 1)); }
else
    case "$t" in *"V-257835"*"the trial on the host failed"*) true ;; *) false ;; esac \
        && echo "ok   - the website: no /etc/redhat-release here - the release rule's trial failed, said on the page" || { echo "FAIL - the failed trial on the page"; fails=$((fails + 1)); }
fi

# an Evaluate-STIG whose template lacks an element and whose code does not show the ExpectedStatus compare
sed -i '/ValidTrueComment/d' "$work/es/AnswerFiles/AnswerFile_Template.xml"
printf "@{ ModuleVersion = '1.2504.1' }\n" > "$work/es/Evaluate-STIG.psd1"; : > "$work/es/Modules/Answers/Answers.psm1"
act "{\"summaries\": [$(printf '%s' "$ACT_OK" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))')]}"
run "a template that lacks an element: the documented names, said; Open keys marked" ok -e '{"answer_keys_rules": ["V-257985"]}' -- \
    "STIG answer keys: 1 ready to copy, 0 not drafted" "<ValidTrueComment>"
page="$(command grep -rl 'Copy block' "$work/web" --include='*.html' | xargs -r ls -t | head -1)"
grep -q "the documented element names (the template lacks ValidTrueComment)" "$page" && grep -q "applies only if your Evaluate-STIG version runs ValidationCode for Open" "$page" \
    && grep -q "version 1.2504.1" "$page" && echo "ok   - the website: the documented names and why, the Open key marked, the version" \
    || { echo "FAIL - the documented names / the Open mark"; fails=$((fails + 1)); }
writes "every run"

# 1.75.0b: the picked rules or nothing - an Evaluate-STIG without -SelectVuln, or one that writes nothing with it: no key, never a
# full scan (it runs once at most, with -SelectVuln)
mkdir -p "$work/es_nosel"
grep -v -- '--SelectVuln' "$work/es/Evaluate-STIG_Bash.sh" > "$work/es_nosel/Evaluate-STIG_Bash.sh"; chmod +x "$work/es_nosel/Evaluate-STIG_Bash.sh"
: > "$work/es_args.txt"
run "an Evaluate-STIG without -SelectVuln: not run, no key drafted (never a full scan) - no host worked: the job fails, says why" fail -e "$all6" -e check_stig_evaluate="$work/es_nosel" -- \
    "STIG answer keys: 0 ready to copy, 0 not drafted - 0 STIGs, 0 hosts" \
    "HOST localhost: Evaluate-STIG could not run only the picked rules: this Evaluate-STIG has no -SelectVuln option (not run in full: selected_only)" \
    "STIG answer keys: no host worked"
[ ! -s "$work/es_args.txt" ] && echo "ok   - ... Evaluate-STIG not run at all" || { echo "FAIL - Evaluate-STIG ran: $(cat "$work/es_args.txt")"; fails=$((fails + 1)); }
mkdir -p "$work/es_empty"
cat > "$work/es_empty/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# --SelectVuln (it says it has it, and writes nothing with it)
echo "\$*" >> "$work/es_args.txt"; echo "nothing written"
EOF
chmod +x "$work/es_empty/Evaluate-STIG_Bash.sh"
: > "$work/es_args.txt"
run "an Evaluate-STIG that writes nothing with -SelectVuln: no key drafted, not run again in full" fail -e "$all6" -e check_stig_evaluate="$work/es_empty" -- \
    "STIG answer keys: 0 ready to copy, 0 not drafted" "with -SelectVuln it wrote no checklist" "not run in full (selected_only)"
[ "$(wc -l < "$work/es_args.txt")" -eq 1 ] && grep -q -- "--SelectVuln" "$work/es_args.txt" && echo "ok   - ... Evaluate-STIG run once, with --SelectVuln - no full run" \
    || { echo "FAIL - Evaluate-STIG runs: $(cat "$work/es_args.txt")"; fails=$((fails + 1)); }
untouched "every run"

# 1.76.4: the trial off (answer_keys_trial false, the default): the read-only check only - no systemd-run run at all (a
# stand-in that would leave a mark), the blocks offered marked not tried, the Trial tile "off"
mkdir -p "$work/sdoff"
printf '#!/bin/sh\ntouch "%s/sdoff/ran"\nexit 1\n' "$work" > "$work/sdoff/systemd-run"
chmod 755 "$work/sdoff/systemd-run"
# the rules are asked at the same time (site_act_concurrency): each answer goes to its rule by its V- number
act "{\"summaries_by\": {\"V-257985\": $(printf '%s' "$ACT_OK" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))'), \"V-257883\": $(printf '%s' "$ACT_BAD" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))')}}"
run "the trial off (the default): the check only, the block offered marked not tried, no systemd-run" ok \
    -e "$all6" -e answer_keys_trial=false -e _ak_sandbox="$work/sdoff/systemd-run" -- \
    "STIG answer keys: 2 ready to copy, 4 not drafted" "V-258999 ready (not tried): A manual check: a document." \
    "V-257985 ACT draft (not tried):" "!no sandbox"
[ ! -e "$work/sdoff/ran" ] && echo "ok   - ... systemd-run never ran" || { echo "FAIL - systemd-run ran with the trial off"; fails=$((fails + 1)); }
t="$(pagetext "$(command grep -rl 'Copy block' "$work/web" --include='*.html' | xargs -r ls -t | head -1)")"
case "$t" in *"not tried: the trial is off (answer_keys_trial) - read it before you use it, try it on a test host"*) \
    case "$t" in *"The trial is off (answer_keys_trial)"*) echo "ok   - ... the website marks each block not tried and says the trial is off" ;;
                 *) echo "FAIL - the website: the trial off not said"; fails=$((fails + 1)) ;; esac ;;
    *) echo "FAIL - the website: a block not marked not tried"; fails=$((fails + 1)) ;; esac
untouched "the trial off"

# 1.76.0: no sandbox (no systemd-run that keeps the host read-only): no trial, so no key - never a trial outside it
act "{\"summaries\": [$(printf '%s' "$ACT_OK" | "$py" -c 'import json,sys; print(json.dumps(sys.stdin.read().replace("\\n", "\n")))')]}"
run "no sandbox: not tried, no key" ok -e '{"answer_keys_rules": ["V-257985"]}' -e _ak_sandbox=/nonexistent/systemd-run -- \
    "STIG answer keys: 0 ready to copy, 1 not drafted" \
    "V-257985 NOT DRAFTED: Not drafted - not tried: no sandbox (systemd-run was not found): a trial runs only in a read-only sandbox."
untouched "no sandbox"

# 1.76.0: systemctl needs a daemon's socket - the sandbox has none: the exact command line the check allowed is run once
# outside it, its answer replayed inside (a stand-in check text on a rule the fake scan knows; the trial true where systemd-journald runs)
run "systemctl in a code: read once outside the sandbox, replayed inside" ok -e '{"answer_keys_rules": ["V-257779"], "answer_keys_act": false}' -- \
    "STIG answer keys: 1 ready to copy, 0 not drafted" "V-257779 ready: RHEL 9 must display the Standard Mandatory DoD Notice and Consent Banner."
page="$(command grep -rl 'Copy block' "$work/web" --include='*.html' | xargs -r ls -t | head -1)"
t="$(pagetext "$page")"; case "$t" in *"Replayed in the trial"*"/usr/bin/systemctl is-active systemd-journald - read once outside the sandbox"*) true ;; *) false ;; esac \
    && case "$t" in *"no socket of any kind, not root"*) true ;; *) false ;; esac \
    && echo "ok   - the website: the systemctl answer replayed (read once outside), the sandbox described" || { echo "FAIL - the replay on the page"; fails=$((fails + 1)); }

# 1.76.0: the sandbox itself - Codex's three writes and their variants run in it with the check left out (in the test):
# nothing on the host changes
"$py" "$here/sandbox_trial.py" > "$work/sandbox.txt" 2>&1; src=$?
grep -E "^(ok|FAIL) " "$work/sandbox.txt" | sed 's/^/    /'
[ $src -eq 0 ] && echo "ok   - the trial sandbox held: every write refused, nothing on the host changed" \
    || { echo "FAIL - the trial sandbox: $(tail -1 "$work/sandbox.txt")"; fails=$((fails + 1)); }

run "no rule asked and no STIG Manager: stops, says what to set" fail -e check_stig_stigman_api= -- "No rule asked and no STIG Manager to pick them from"
run "more hosts than answer_keys_max_hosts: stops, says so" fail -e "$all6" -e answer_keys_max_hosts=0 -- "at most 0 (answer_keys_max_hosts)"

# 1.76.0: many hosts - see run_stig_answer_keys_hosts_test.sh

# 1.75.1 / 1.76.0: no target - the Limit is used (one host or many); neither a target nor a Limit: the clear message
run "no target, the Limit is one host: it is the target" ok -e target= -l localhost -- "!No target: set target"
AK_INV="localhost,127.0.0.1," run "no target, the Limit is two hosts: both are used" ok -e target= -l localhost,127.0.0.1 -e "$all6" -- "!No target: set target"
run "no target and no Limit: the target message" fail -e target= -e "$all6" -- "No target: set target to the Linux host or group"

echo
if [ $fails -eq 0 ]; then echo "all STIG answer keys scenarios passed"; else echo "$fails STIG answer keys scenario(s) FAILED"; exit 1; fi
