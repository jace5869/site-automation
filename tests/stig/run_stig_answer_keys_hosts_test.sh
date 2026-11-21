#!/usr/bin/env bash
# STIG answer keys across many hosts (1.76.0, playbooks/stig_answer_keys.yml): four stand-in hosts on this box (the local
# connection, no sudo) - a RHEL 8, two RHEL 9 (one Open, one Not Reviewed for the same rule) and a RHEL 10 - each with a
# fake Evaluate-STIG_Bash.sh of its own STIG (tests/stig/make_cklb_stig.py, only the rules asked of it), and one host
# that cannot be reached. The STIG Manager stand-in (tests/stig/stigman_mock.py: anything but a GET - and the token's
# POST - FAILS the test). Checks: one list of RHEL 8, 9 and 10 ids, each host taking its own; one block per rule per STIG,
# grouped; the ExpectedStatus of the majority, the others said; Copy all per STIG; a workbook sheet per STIG; the
# unreachable host listed, the job green; a rule in none of the hosts' STIGs. The automatic pick (no rule asked): STIG
# Manager's Open and Not Reviewed - never Not a Finding, a review with RA words or attachments, a CAT left out;
# answer_keys_pick open; the cap (answer_keys_max_rules); the rules already in the answer files left out. 1.77.0, the
# cross-check (a fake ACT, tests/containers/fake_act.py): DISA's command read on the trial host and on the host a person
# reviewed after the scan, one ACT call for the same evidence, never the code; a disagreement named; the cap.
#   bash tests/stig/run_stig_answer_keys_hosts_test.sh     (ANSIBLE_PLAYBOOK=... to pick one; AK_KEEP=DIR keeps the outputs)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
command -v pwsh >/dev/null || { echo "SKIP - pwsh is not here (the read-only check needs PowerShell 7)"; exit 0; }
work="$(mktemp -d)"
smtp_pid="" sm_pid="" act_pid=""
cleanup() { for p in "$smtp_pid" "$sm_pid" "$act_pid"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
port_sm="${AKH_SM_PORT:-12645}" port_smtp="${AKH_SMTP_PORT:-12646}" port_act="${AKH_ACT_PORT:-12647}"
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/mail" "$work/web" "$work/tmp" "$work/rtmp" "$work/ltmp" "$work/answers" "$work/act" "$work/actweb"
CHK='Verify the owner of /etc/hostname:\n\n$ sudo stat -c \"%U %n\" /etc/hostname\n\nroot /etc/hostname\n\nIf it is not root, this is a finding.'
# fake Evaluate-STIGs: es8 (RHEL 8 V2R9), es9a and es9b (RHEL 9 V2R10), es10 (RHEL 10 V1R3)
fake_es() {  # fake_es DIR STIG VERSION RULES_JSON
    mkdir -p "$1/AnswerFiles" "$1/Modules/Answers"
    printf '%s' "$4" > "$1/rules.json"
    cat > "$1/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# --SelectVuln (this Evaluate-STIG has it)
out=""; sel=""; echo "\$*" >> "$1/args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; --SelectVuln) sel="\$2"; shift ;; esac; shift; done
printf '%s' "\$sel" > "$1/select.txt"
mkdir -p "\$out/HOST/Checklist" && "$py" "$here/make_cklb_stig.py" "\$out/HOST/Checklist/HOST_$2_$3.cklb" "$2" "$3" "\$(cat "$1/rules.json")" "$1/select.txt"
echo "Done"
EOF
    chmod +x "$1/Evaluate-STIG_Bash.sh"
    printf "@{ ModuleVersion = '1.2610.1' }\n" > "$1/Evaluate-STIG.psd1"
    printf '# where an answer is used\nif ($AnswerObject.ExpectedStatus -eq $Status) { $Valid = 1 }\n' > "$1/Modules/Answers/Answers.psm1"
    printf '<?xml version="1.0"?>\n<STIGComments Name="Template"><Vuln ID="V-000000"><AnswerKey Name="DEFAULT"><ExpectedStatus>Not_Reviewed</ExpectedStatus><ValidationCode /><ValidTrueStatus /><ValidTrueComment /><ValidFalseStatus /><ValidFalseComment /></AnswerKey></Vuln></STIGComments>\n' > "$1/AnswerFiles/AnswerFile_Template.xml"
}
r() { printf '{"sv": "%s", "status": "%s", "severity": "%s"}' "$1" "$2" "${3:-medium}"; }
fake_es "$work/es8" RHEL_8_STIG V2R9 "[$(r SV-230221 open), $(r SV-230222 not_a_finding)]"
fake_es "$work/es9a" RHEL_9_STIG V2R10 "[$(r SV-257777 open), $(r SV-258999 not_reviewed), $(r SV-257801 open), $(r SV-257802 open), $(r SV-257803 open low), $(r SV-257804 open)]"
fake_es "$work/es9b" RHEL_9_STIG V2R10 "[$(r SV-257777 not_reviewed), $(r SV-258999 not_reviewed)]"
fake_es "$work/es10" RHEL_10_STIG V1R3 "[$(r SV-281111 open)]"
# 1.76.0b: an older RHEL 9 revision (V2R7), whose V-257777 has another check text (/etc/hosts): its own block
fake_es "$work/es9old" RHEL_9_STIG V2R7 "[$(r SV-257777 open)]"
CHK7='Verify the owner of /etc/hosts:\n\n$ sudo stat -c \"%U %n\" /etc/hosts\n\nroot /etc/hosts\n\nIf it is not root, this is a finding.'
"$py" "$repo/tests/email/fake_smtp.py" "$port_smtp" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
"$py" -m http.server "$port_act" --bind 127.0.0.1 --directory "$work/actweb" >/dev/null 2>&1 & act_pid=$!   # ACT's address answers
sleep 1
rv() {  # rv SV RESULT [EXTRA_JSON]
    printf '{"ruleId": "%sr1_rule", "ruleIds": ["%sr1_rule"], "result": "%s", "status": {"label": "submitted"}, "ts": "2026-10-01T10:00:00Z", "rule": {"ruleId": "%sr1_rule", "severity": "medium", "title": "x"}%s}' "$1" "$1" "$2" "$1" "${3:-}"
}
rule() {  # rule SV SEVERITY TITLE [CHECK]
    printf '{"ruleId": "%sr1_rule", "groupId": "V-%s", "version": "X-%s", "severity": "%s", "title": "%s", "check": "%s", "fix": ""}' "$1" "${1#SV-}" "${1#SV-}" "$2" "$3" "${4:-$CHK}"
}
asset() { printf '{"assetId": "%s", "name": "%s", "fqdn": "", "stigs": [{"benchmarkId": "%s", "revisionStr": "%s"}]}' "$1" "$2" "$3" "$4"; }
cat > "$work/sm.json" <<EOF
{"secret": "s3cret", "client": "autostig",
 "collections": [{"collectionId": "1", "name": "RHEL Servers"}],
 "assets": {"1": [$(asset 11 h8 RHEL_8_STIG V2R9), $(asset 12 h9a RHEL_9_STIG V2R10), $(asset 13 h9b RHEL_9_STIG V2R10), $(asset 14 h10 RHEL_10_STIG V1R3), $(asset 15 h9old RHEL_9_STIG V2R7)]},
 "reviews": {"11": [$(rv SV-230221 fail), $(rv SV-230222 pass)],
             "12": [$(rv SV-257777 fail), $(rv SV-257800 pass), $(rv SV-257801 fail ', "comment": "Accepted by the AO: RA 2026-04 in place"'),
                    $(rv SV-257802 fail ', "metadata": {"artifacts": "[{\"filename\": \"evidence.png\"}]"}'), $(rv SV-257803 fail), $(rv SV-257804 fail)],
             "13": [$(rv SV-257777 notchecked)], "14": [$(rv SV-281111 fail)], "15": [$(rv SV-257777 fail)]},
 "rules_rev": {"RHEL_9_STIG/V2R7": [$(rule SV-257777 medium "RHEL 9 V2R7 hosts file owner." "$CHK7")]},
 "rules": {"RHEL_8_STIG": [$(rule SV-230221 medium "RHEL 8 hostname file owner."), $(rule SV-230222 medium "RHEL 8 passes.")],
           "RHEL_9_STIG": [$(rule SV-257777 medium "RHEL 9 hostname file owner."), $(rule SV-258999 medium "RHEL 9 a manual check."),
                           $(rule SV-257800 medium "Passes."), $(rule SV-257801 medium "Risk accepted."), $(rule SV-257802 medium "Has evidence attached."),
                           $(rule SV-257803 low "A CAT III rule."), $(rule SV-257804 high "A CAT I rule.")],
           "RHEL_10_STIG": [$(rule SV-281111 medium "RHEL 10 hostname file owner.")]}}
EOF
hv() {  # hv NAME ES STIG SELECT
    printf '    %s:\n      ansible_connection: local\n      check_stig_evaluate: %s\n      check_stig_stigman_benchmarks: {"%s": %s}\n      check_stig_evaluate_select: {"%s": %s}\n' "$1" "$2" "$major" "$3" "$major" "$4"
}
{
    echo "all:"; echo "  children:"; echo "    rhel:"; echo "      hosts:"
    for x in h8 h9a h9b h10 h9old hdead; do echo "        $x:"; done
    echo "  hosts:"
    hv h8 "$work/es8" RHEL_8_STIG RHEL8; hv h9a "$work/es9a" RHEL_9_STIG RHEL9; hv h9b "$work/es9b" RHEL_9_STIG RHEL9; hv h10 "$work/es10" RHEL_10_STIG RHEL10; hv h9old "$work/es9old" RHEL_9_STIG RHEL9
    printf '    hdead:\n      ansible_connection: ssh\n      ansible_host: 127.0.0.1\n      ansible_port: 9\n      ansible_ssh_common_args: -o ConnectTimeout=3 -o BatchMode=yes\n'
} > "$work/inv.yml"
cat > "$work/settings.yml" <<EOF
check_stig_scanner: evaluate_stig
check_stig_evaluate_args: ["--AnswerKey", "SITEA", "--Output", "STIGManager"]
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
check_stig_dir: $work/kept
answer_keys_act: false
answer_keys_trial: true
answer_keys_cross_check: false          # 1.77.0: on in its own scenario below (with the fake ACT)
act_triage_script_src: $repo/tests/containers/fake_act.py
act_triage_poll: 1
site_act_url: http://127.0.0.1:$port_act/v1/chat/completions
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port_smtp
report_email_security: none
report_email_from: aap@example.mil
report_web_dest: $work/web
EOF
lastpage() { command grep -rl 'Copy block' "$work/web" --include='*.html' 2>/dev/null | xargs -r ls -t | head -1; }
run() {  # run NAME ok|fail [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -f "$work/mail"/* "$work"/es*/args.txt
    (cd "$repo" && TMPDIR="$work/tmp" ANSIBLE_REMOTE_TMP="$work/rtmp" ANSIBLE_LOCAL_TEMP="$work/ltmp" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_HOST_KEY_CHECKING=False FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" GENAI_KEY=test-key "$ap" -i "${AKH_INV:-$work/inv.yml}" playbooks/stig_answer_keys.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4901, "awx_user_name": "aap.admin"}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    [ -n "${AK_KEEP:-}" ] && { mkdir -p "$AK_KEEP"; cp "$work/out.txt" "$AK_KEEP/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-50).txt"
                               p="$(lastpage)"; [ -n "$p" ] && cp "$p" "$AK_KEEP/$(printf '%s' "$tname" | tr -c 'A-Za-z0-9' '_' | cut -c1-50).html"; }
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "STIG answer keys:\|NOT DRAFTED\|ready\|DID NOT\|HOST \|fatal\|ERROR\|msg" "$work/out.txt" | cut -c1-300 | head -40; fails=$((fails + 1)); fi
}
# 1.100.0: rows past the first 100 and each rule's opened detail are deferred blocks built when the reader opens them
unpacked() {  # unpacked FILE: the page with its deferred blocks built as site-report.js does (tests/email/unpack_deferred.py), its path
    "$py" "$repo/tests/email/unpack_deferred.py" "$1" > "$work/unpacked.html" && echo "$work/unpacked.html"
}
pagetext() { "$py" -c 'import html, re, sys; print(html.unescape(re.sub(r"<[^>]+>", " ", open(sys.argv[1]).read())))' "$(unpacked "$1")" | tr -s ' \n' ' '; }
leftovers() { (cd "$work" && find tmp rtmp ltmp -mindepth 1 -maxdepth 1 2>/dev/null) | sort; }
before_left="$(leftovers)"

# 1. one list of RHEL 8, 9 and 10 ids across five hosts - one unreachable
mixed='{"answer_keys_rules": ["V-230221", "V-257777", "V-258999", "V-281111", "V-999999"]}'
run "six hosts, one list of RHEL 8, 9 and 10 ids: grouped by STIG and release, the dead host listed, the job green" ok -e target=rhel -e "$mixed" -- \
    "STIG answer keys: 5 ready to copy, 1 not drafted - 4 STIGs, 5 hosts" "RHEL 9 V2R7: 1 ready to copy, 0 not drafted (hosts: h9old)" \
    "RHEL 8 V2R9: 1 ready to copy, 0 not drafted (hosts: h8)" "RHEL 9 V2R10: 2 ready to copy, 0 not drafted (hosts: h9a, h9b)" \
    "RHEL 10 V1R3: 1 ready to copy, 0 not drafted (hosts: h10)" "HOST hdead: not reached" \
    "V-999999 NOT DRAFTED: in none of the hosts' STIGs" "!fatal: [h8]" "!fatal: [h9a]"
for h in es8 es9a es9b es10; do
    sel="$(cat "$work/$h/select.txt" 2>/dev/null)"
    case "$h:$sel" in es8:V-230221,V-257777,V-258999,V-281111,V-999999|es9a:V-230221,V-257777,V-258999,V-281111,V-999999|es9b:*V-257777*|es10:*V-281111*) echo "ok   - $h: Evaluate-STIG run for the picked rules only (--SelectVuln $sel)" ;;
        *) echo "FAIL - $h: Evaluate-STIG's --SelectVuln: '$sel'"; fails=$((fails + 1)) ;; esac
done
page="$(lastpage)"; t="$(pagetext "$page")"
for want in "RHEL 9 V2R10 - answer keys to copy (2)" "RHEL 8 V2R9 - answer keys to copy (1)" "RHEL 10 V1R3 - answer keys to copy (1)" \
            "Copy the answer file for RHEL 9 V2R10" "RHEL 9 V2R10 - the answer file (2 blocks)" "Not Reviewed on 1 host, Open on 1 host - the key applies where Evaluate-STIG says Open" \
            "Found on (2) h9a, h9b" "Hosts that did not work (1)" "In none of the hosts' STIGs (1)"; do
    case "$t" in *"$want"*) true ;; *) echo "FAIL - the website: missing '$want'"; fails=$((fails + 1)) ;; esac
done
if "$py" - "$(unpacked "$page")" <<'PY'
import html, re, sys
t = open(sys.argv[1]).read()
import xml.etree.ElementTree as ET
# 1.79.4: each STIG's whole answer file to copy - it parses, its Name, every block in its own <Vuln ID>
c = dict((re.search(r"site-automation STIG answer keys: (RHEL [0-9]+ V[0-9]+R[0-9]+)", m).group(1), m)
         for m in (html.unescape(x) for x in re.findall(r'data-copy="([^"]*)"', t)) if m.startswith("<?xml"))
names = dict((k, ET.fromstring(v.encode()).get("Name")) for k, v in c.items())
v7, v10 = c.get("RHEL 9 V2R7", ""), c.get("RHEL 9 V2R10", "")
ok = '<Vuln ID="V-257777">' in v7 and "'/etc/hosts'" in v7 and "/etc/hostname" not in v7 and "'/etc/hostname'" in v10 and "/etc/hosts'" not in v10
print(sorted(c), names)
sys.exit(0 if ok and len(c) == 4 and names == {"RHEL 8 V2R9": "RHEL8", "RHEL 9 V2R7": "RHEL9", "RHEL 9 V2R10": "RHEL9", "RHEL 10 V1R3": "RHEL10"} else 1)
PY
then echo "ok   - the website: each STIG's whole answer file (it parses; its Name from check_stig_evaluate_select); RHEL 9 V2R7 and V2R10 each their own (/etc/hosts, /etc/hostname)"
else echo "FAIL - the answer file / a file per revision"; fails=$((fails + 1)); fi
if "$py" - "$work/out.txt" <<'PY'
import sys
t = open(sys.argv[1]).read()
sys.exit(0 if all(n in t for n in ("answer-keys-RHEL_8_STIG-V2R9.xml", "answer-keys-RHEL_9_STIG-V2R7.xml", "answer-keys-RHEL_9_STIG-V2R10.xml",
                                   "answer-keys-RHEL_10_STIG-V1R3.xml")) else 1)
PY
then echo "ok   - the job's output names the four answer files"; else echo "FAIL - the job's output: the answer files"; fails=$((fails + 1)); fi
# 1.79.8: the website hosts the four answer files (and the workbook) beside the page: each parses, its Download link
lp="$(lastpage)"
if "$py" - "$lp" "$(unpacked "$lp")" <<'PY'
import os, re, sys
import xml.etree.ElementTree as ET
page = sys.argv[1]                                  # the run's page or its latest.html: both link the run's folder
t = open(sys.argv[2]).read()                        # its rows as people see them (each block's link built from Files)
want = ("answer-keys-RHEL_8_STIG-V2R9.xml", "answer-keys-RHEL_9_STIG-V2R7.xml", "answer-keys-RHEL_9_STIG-V2R10.xml", "answer-keys-RHEL_10_STIG-V1R3.xml")
links = re.findall(r'<a class="dl" href="([^"]+)" download="([^"]+)">Download ', t)
folders = set(os.path.dirname(h) for h, _ in links)
folder = os.path.join(os.path.dirname(page), folders.pop()) if len(folders) == 1 else ""
there = sorted(os.listdir(folder)) if folder and os.path.isdir(folder) else []
print("hosted:", folder, there)
names = dict((n, ET.parse(os.path.join(folder, n)).getroot().get("Name")) for n in want if n in there)
sys.exit(0 if len(names) == 4 and folder.endswith("-files") and any(x.endswith(".xlsx") for x in there)
         and sorted(n for _, n in links) == sorted(want) and t.count('class="card sfiles"') == 1 and "site-report-file:" not in t else 1)
PY
then echo "ok   - the website: the four answer files and the workbook hosted beside the page (each parses), a Download link each, the Files line"
else echo "FAIL - the website: the hosted answer files / their Download links"; fails=$((fails + 1)); fi
m="$(ls "$work/mail"/* 2>/dev/null | head -1)"
if [ -n "$m" ] && "$py" - "$m" <<'PY'
import email, io, re, sys, zipfile
msg = email.message_from_binary_file(open(sys.argv[1], "rb"))
import xml.etree.ElementTree as ET
x = [p for p in msg.walk() if (p.get_filename() or "").endswith(".xlsx")]
names = re.findall(r'<sheet [^>]*name="([^"]+)"', zipfile.ZipFile(io.BytesIO(x[0].get_payload(decode=True))).read("xl/workbook.xml").decode()) if x else []
print("sheets:", names)
# 1.79.4: an answer file per STIG attached (.xml, no zip), each parses, with its Name
xs = dict((p.get_filename(), ET.fromstring(p.get_payload(decode=True)).get("Name")) for p in msg.walk() if (p.get_filename() or "").endswith(".xml"))
print("xml:", xs)
zips = [p.get_filename() for p in msg.walk() if (p.get_filename() or "").lower().endswith(".zip")]
body = "".join(p.get_payload(decode=True).decode("utf-8", "replace") for p in msg.walk() if p.get_content_type() == "text/html")
sys.exit(0 if all(n in names for n in ("RHEL 8 V2R9", "RHEL 9 V2R10", "RHEL 9 V2R7", "RHEL 10 V1R3", "Not drafted")) and not zips and
         xs == {"answer-keys-RHEL_8_STIG-V2R9.xml": "RHEL8", "answer-keys-RHEL_9_STIG-V2R7.xml": "RHEL9",
                "answer-keys-RHEL_9_STIG-V2R10.xml": "RHEL9", "answer-keys-RHEL_10_STIG-V1R3.xml": "RHEL10"} and
         "Answer files attached (4)" in body else 1)
PY
then echo "ok   - the email: a sheet per STIG in the workbook; an answer file per STIG attached (.xml, it parses, its Name; no zip), listed"
else echo "FAIL - the email: the workbook's sheets / the answer files attached"; fails=$((fails + 1)); fi
grep -qvE "^(GET |POST /token)" "$work/sm.log" && { echo "FAIL - a request that is not a GET reached STIG Manager"; fails=$((fails + 1)); } || echo "ok   - only GET requests reached STIG Manager"

# 1.76.0b: a trial host that fails - its STIG's blocks not tried, the other STIG and the report go on, the job green
sed 's|^    h10:$|    h10:\n      _ak_sandbox: /bin/true|' "$work/inv.yml" > "$work/inv2.yml"
AKH_INV="$work/inv2.yml" run "a trial host that fails: its STIG not tried, the other STIG and the report go on, the job green" ok -e target=h8,h10 -e "$mixed" -- \
    "RHEL 8 V2R9: 1 ready to copy, 0 not drafted (hosts: h8)" "RHEL 10 V1R3: 0 ready to copy, 1 not drafted (hosts: h10)" \
    "V-281111 NOT DRAFTED: Not drafted - not tried: the trial host failed (sandbox /bin/true is not systemd-run: no trial with it)."

# 2. the automatic pick: no rule asked - STIG Manager's Open and Not Reviewed for the host
run "no rule asked, the default (answer_keys_pick not_reviewed): STIG Manager's Not Reviewed only" ok -e target=h9a -- \
    "RHEL 9 V2R10: 1 ready to copy" "V-258999 ready" "!V-257777 ready" "!V-257804 ready"
run "no rule asked, answer_keys_pick both: STIG Manager's Open and Not Reviewed (CAT I and II) - not the passed, RA, attached or CAT III" ok -e target=h9a -e answer_keys_pick=both -e '{"answer_keys_cat": [1, 2]}' -- \
    "RHEL 9 V2R10: 3 ready to copy" "V-257804 ready" "V-257777 ready" "V-258999 ready" "!V-257800" "!V-257801" "!V-257802" "!V-257803"
sel="$(cat "$work/es9a/select.txt")"
[ "$sel" = "V-257804,V-257777,V-258999" ] && echo "ok   - ... Evaluate-STIG run for those three only, CAT I first ($sel)" || { echo "FAIL - the automatic pick's --SelectVuln: '$sel'"; fails=$((fails + 1)); }
t="$(pagetext "$(lastpage)")"; case "$t" in *"Picked: automatically from STIG Manager"*"answer_keys_pick both"*) echo "ok   - the website says how they were picked" ;; *) echo "FAIL - how picked"; fails=$((fails + 1)) ;; esac
case "$t" in *"2 of these are rules Evaluate-STIG decides itself"*"Evaluate-STIG already checks this rule and found it Open - this key changes the result only where your site"*) echo "ok   - the website: the Open ones are rules Evaluate-STIG decides itself - said at the top and in each" ;;
    *) echo "FAIL - the decided note"; fails=$((fails + 1)) ;; esac
run "the automatic pick, answer_keys_pick open" ok -e target=h9a -e answer_keys_pick=open -e '{"answer_keys_cat": "1, 2"}' -- \
    "RHEL 9 V2R10: 2 ready to copy" "V-257804 ready" "V-257777 ready" "!V-258999 ready"
run "the automatic pick, at most answer_keys_max_rules: the rest left out, said" ok -e target=h9a -e answer_keys_pick=both -e answer_keys_max_rules=1 -- \
    "RHEL 9 V2R10: 1 ready to copy" "V-257804 ready" "!V-257777 ready"
t="$(pagetext "$(lastpage)")"; case "$t" in *"Left out: more than answer_keys_max_rules"*) echo "ok   - ... the left out said" ;; *) echo "FAIL - left out"; fails=$((fails + 1)) ;; esac

# 3. the rules already in the site's answer files (an AnswerKey of this Name): left out, listed
printf '<?xml version="1.0"?>\n<STIGComments Name="RHEL9"><Vuln ID="V-257777"><AnswerKey Name="SITEA"><ExpectedStatus>Open</ExpectedStatus><ValidationCode>return $true</ValidationCode></AnswerKey></Vuln></STIGComments>\n' > "$work/answers/RHEL9_SITEA.xml"
run "the rules already in your answer file: left out, listed" ok -e target=h9a -e answer_keys_pick=both -e '{"answer_keys_cat": [1, 2]}' -e check_stig_evaluate_answers="$work/answers" -- \
    "RHEL 9 V2R10: 2 ready to copy" "!V-257777 ready"
t="$(pagetext "$(lastpage)")"; case "$t" in *"Already in your answer file (1)"*"V-257777"*) echo "ok   - ... listed: already in your answer file" ;; *) echo "FAIL - already answered"; fails=$((fails + 1)) ;; esac
# 1.79.4: the answer file's Name from the site's own answer file for that STIG (not check_stig_evaluate_select's RHEL9)
sed -i 's/<STIGComments Name="RHEL9">/<STIGComments Name="RHEL9-site">/' "$work/answers/RHEL9_SITEA.xml"
run "the answer file's Name from your own answer file for that STIG" ok -e target=h9a -e answer_keys_pick=both -e '{"answer_keys_cat": [1, 2]}' -e check_stig_evaluate_answers="$work/answers" -- \
    "answer file answer-keys-RHEL_9_STIG-V2R10.xml: STIGComments Name RHEL9-site (your answer file RHEL9_SITEA.xml)"
m="$(ls "$work/mail"/* 2>/dev/null | head -1)"
if [ -n "$m" ] && "$py" - "$m" <<'PY'
import email, sys
import xml.etree.ElementTree as ET
msg = email.message_from_binary_file(open(sys.argv[1], "rb"))
x = [ET.fromstring(p.get_payload(decode=True)) for p in msg.walk() if p.get_filename() == "answer-keys-RHEL_9_STIG-V2R10.xml"]
sys.exit(0 if x and x[0].get("Name") == "RHEL9-site" and [v.get("ID") for v in x[0]] == ["V-257804", "V-258999"] else 1)
PY
then echo "ok   - ... the attached file: Name RHEL9-site, the two new blocks (not the one already answered)"; else echo "FAIL - the Name from the site's file"; fails=$((fails + 1)); fi

# 4. 1.77.0, the cross-check (1.82.0: its need a look counted in the head, the subject): h9b's review of SV-258999 is a person's (no result engine), newer than its last scan import
# (SV-257777's, by Evaluate-STIG); the code is tried on h9a (the trial host). Both hosts are this box: the same evidence.
cp "$work/sm.json" "$work/sm.json.keep"
"$py" - "$work/sm.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
eng = {"type": "script", "product": "Evaluate-STIG", "version": "1.2610.1"}
d["reviews"]["13"] = [dict(d["reviews"]["13"][0], resultEngine=eng, ts="2026-10-01T10:00:00Z"),
                      {"ruleId": "SV-258999r1_rule", "ruleIds": ["SV-258999r1_rule"], "result": "fail", "status": {"label": "submitted"},
                       "ts": "2026-11-10T10:00:00Z", "resultEngine": None, "rule": {"ruleId": "SV-258999r1_rule", "severity": "medium", "title": "x"}}]
json.dump(d, open(sys.argv[1], "w"))
PY
xc='{"answer_keys_rules": ["V-258999"]}'
printf '%s' '{"summary": "NF\nroot owns /etc/hostname, as the check text wants.\nroot /etc/hostname"}' > "$work/act/act.json"; rm -f "$work/act/fake-act-calls.log"
run "the cross-check: code NF and ACT NF on h9a, a person's Open in STIG Manager and ACT NF on h9b - need a look" ok \
    -e target=h9a,h9b -e "$xc" -e answer_keys_cross_check=true -- \
    "STIG answer keys: 1 ready to copy, 0 not drafted - 1 STIG, 2 hosts" 'emailed "[AAP] STIG answer keys: 1 need a look, 1 ready to copy"' \
    "Cross-check: 0 agree, 1 need a look, 0 too little to compare" "V-258999 CROSS-CHECK: STIG Manager Open, ACT NF on h9b"
[ "$(grep -c call "$work/act/fake-act-calls.log" 2>/dev/null)" = 1 ] && echo "ok   - ... one ACT call: the two hosts returned the same evidence" \
    || { echo "FAIL - cross-check ACT calls: $(cat "$work/act/fake-act-calls.log" 2>/dev/null | wc -l)"; fails=$((fails + 1)); }
tk="$(cat "$work/act/fake-act-task.txt" 2>/dev/null)"
case "$tk" in *"\$ /usr/bin/stat -c '%U %n' /etc/hostname   (exit 0)"*"root /etc/hostname"*) echo "ok   - ... ACT got DISA's command and what the host returned" ;;
    *) echo "FAIL - what ACT got: $(printf '%s' "$tk" | head -c 600)"; fails=$((fails + 1)) ;; esac
case "$tk" in *ValidationCode*|*powershell*|*AnswerKey*|*Get-Content*|*"Not Reviewed"*) echo "FAIL - ACT saw the code or Evaluate-STIG's status"; fails=$((fails + 1)) ;;
    *) echo "ok   - ... never the code, its result or Evaluate-STIG's status" ;; esac
t="$(pagetext "$(lastpage)")"; xmiss=0
for want in "Cross-check by STIG" "Cross-check — RHEL 9 V2R10 (1 rule, 1 need a look)" "⚠ Need a look: STIG Manager Open, ACT NF on h9b" \
            "Open (a person," "Ran on h9a, h9b: /usr/bin/stat -c '%U %n' /etc/hostname" "answer_keys_cross_check_act_max"; do
    case "$t" in *"$want"*) true ;; *) echo "FAIL - the cross-check page: missing '$want'"; xmiss=1 ;; esac
done
[ $xmiss -eq 0 ] && echo "ok   - ... the website: the cross-check sections, the disagreement, every command and the host it ran on" || fails=$((fails + 1))
# 1.88.0: the opened rule, host by host - each verdict line, the code's read line, ACT's evidence as state and counts with
# the line it named marked, Evaluate-STIG and STIG Manager
p="$(lastpage)"; t="$(pagetext "$p")"; xmiss=0
for want in "Agree: the code and ACT say Not a Finding" "A person's review differs: STIG Manager says Open, ACT says Not a Finding" \
            "/usr/bin/stat -c '%U %n' /etc/hostname (exit 0, 1 line)" "(trial on h9a)" "returned True" "the trial ran on h9a" \
            "a person, " "· counted" "shown, not counted"; do
    case "$t" in *"$want"*) true ;; *) echo "FAIL - the opened rule: missing '$want'"; xmiss=1 ;; esac
done
grep -qF 'class="wdl" title="The line ACT named as deciding it">→ root /etc/hostname</span>' "$(unpacked "$p")" || { echo "FAIL - the deciding line not marked"; xmiss=1; }
case "$t" in *"What ACT was given"*) echo "FAIL - the opened rule still shows the evidence's text"; xmiss=1 ;; esac
[ $xmiss -eq 0 ] && echo "ok   - ... the opened rule: each host's verdict, the four sources, the deciding line marked (state and counts only)" || fails=$((fails + 1))
rm -f "$work/act/fake-act-calls.log"
run "the cross-check, answer_keys_cross_check_act_max 0: ACT not asked, said with the setting" ok \
    -e target=h9a,h9b -e "$xc" -e answer_keys_cross_check=true -e answer_keys_cross_check_act_max=0 -- \
    "Cross-check: 0 agree, 0 need a look, 1 too little to compare"
[ -e "$work/act/fake-act-calls.log" ] && { echo "FAIL - ACT asked past the cap"; fails=$((fails + 1)); } || echo "ok   - ... no ACT call"
t="$(pagetext "$(lastpage)")"; case "$t" in *"not asked (the cap)"*) echo "ok   - ... the website: not asked (the cap)" ;; *) echo "FAIL - the cap not said"; fails=$((fails + 1)) ;; esac
cp "$work/sm.json.keep" "$work/sm.json"

grep -qvE "^(GET |POST /token)" "$work/sm.log" && { echo "FAIL - a request that is not a GET reached STIG Manager"; fails=$((fails + 1)); } || echo "ok   - every run: only GET requests reached STIG Manager"
now="$(leftovers)"; [ "$now" = "$before_left" ] && [ ! -e "$work/kept" ] && echo "ok   - every run: the hosts as they were (no folder of the job left, nothing kept)" \
    || { echo "FAIL - the hosts changed: $now $(ls -d "$work/kept" 2>/dev/null)"; fails=$((fails + 1)); }

echo
if [ $fails -eq 0 ]; then echo "all STIG answer keys (many hosts) scenarios passed"; else echo "$fails STIG answer keys (many hosts) scenario(s) FAILED"; exit 1; fi
