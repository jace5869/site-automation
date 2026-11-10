#!/usr/bin/env bash
# playbooks/incident_aar.yml and playbooks/incident_sop.yml, run for real against a fake mail relay (tests/email/fake_smtp.py)
# and a scripted GenAI model (tests/incident/fake_model.py): the AAR and the SOP attached as Word documents (every part
# well-formed XML, the logo and banner in them), sent only to the addresses typed - not to the reports' report_email_to -
# and never written to the report website even with report_web_dest given at launch; the SOP made from the AAR when its
# step boxes are empty (Actions, Recovery as written; or written by ACT, a step with an address the AAR does not have
# not used); no SOP when there are no steps at all (said in the email); the SOP draft put in the SOP job's form of a
# fake AAP (tests/incident/fake_aap.py) and emptied by the SOP job launched from it - no AAP credential: said; ACT tidying and suggesting, a tidied box that dropped a number kept as typed, an answer with no
# JSON (the typed text, said), no API key (said); the SOP job; no address, no start, no steps (refused); a dry run.
#   bash tests/incident/run_incident_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
port="${INCIDENT_TEST_PORT:-18640}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
# the reports' usual recipients, as all.yml would set them: the AAR must not go there
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n  vars:\n    report_email_to: [everyone@example.mil]\n    report_email_cc: [boss@example.mil]\n' "$py" > "$work/inv.yml"
"$py" - "$work/logo.png" <<'PY'
import struct, sys, zlib
w, h = 90, 30
raw = b"".join(b"\x00" + b"\x30\x50\x90" * w for _ in range(h))
c = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + c(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + c(b"IDAT", zlib.compress(raw)) + c(b"IEND", b""))
PY
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$port" "$work/mail" 2>/dev/null & pids+=($!)
"$py" "$here/fake_model.py" $((port + 1)) "$work/good.log" good & pids+=($!)
"$py" "$here/fake_model.py" $((port + 2)) "$work/drop.log" drop & pids+=($!)
"$py" "$here/fake_model.py" $((port + 3)) "$work/junk.log" junk & pids+=($!)
"$py" "$here/fake_aap.py" "$work/aap.url" & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 2)); wait_port $((port + 3))
for _ in $(seq 1 50); do [ -s "$work/aap.url" ] && break; sleep 0.1; done
aap_url="$(cat "$work/aap.url" 2>/dev/null)"
# the SOP job's survey in the fake AAP: VARIABLE -> its default answer
draft() { "$py" - "$aap_url" "$1" <<'PY'
import json, sys, urllib.request
req = urllib.request.Request(sys.argv[1] + "/api/controller/v2/job_templates/2/survey_spec/", headers={"Authorization": "Bearer goodtoken"})
spec = json.load(urllib.request.urlopen(req))
print([q for q in spec["spec"] if q["variable"] == sys.argv[2]][0]["default"])
PY
}
model() { echo "{\"site_act_urls\": {\"genai\": \"http://127.0.0.1:$1/v1/chat/completions\"}}"; }

cat > "$work/aar.yml" <<YML
incident_name: Web portal outage
incident_start: 10/27/2026 10:28 AM
incident_impact_level: MAJOR
incident_impact: it is affecting users enterprise-wide
incident_detection: "Monitoring alert recieved at 10:30\r\nCustomer reports"
incident_actions: "- Opened a bridge\n- Failed over to 192.0.2.30\n- Bell\u0007 pasted"
incident_root_cause: A PHP patch (CVE-2026-0001) broke sessions.
incident_recovery: Rolled teh package back.
incident_make_sop: "Yes"
sop_title: Web portal returns 503
sop_troubleshoot: "1. Check the status page\n2. Read the error log"
sop_fix: "1. Restart the app pool"
report_email_also: "ops@example.mil; not-an-address"
report_email_from: aap@example.mil
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $port
report_email_security: none
report_logo_file: $work/logo.png
YML

# run NAME EXPECT(ok|fail) PLAYBOOK [ansible-playbook args...] -- TEXT...: the texts in the output
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -f "$work/mail/"*.eml
    (cd "$repo" && GENAI_KEY="${GENAI_KEY-test-only}" ANSIBLE_NOCOLOR=1 "$ap" -i "$work/inv.yml" "playbooks/$pb" "${args[@]}" > "$work/out.txt" 2>&1)
    local rc=$? bad=""
    if [ "$want" = ok ] && [ $rc -ne 0 ]; then bad="exit $rc"; fi
    if [ "$want" = fail ] && [ $rc -eq 0 ]; then bad="it should have failed"; fi
    for t in "${texts[@]}"; do grep -qF -- "$t" "$work/out.txt" || bad="$bad; missing: $t"; done
    if [ -z "$bad" ]; then echo "ok   - $tname"; else echo "FAIL - $tname: $bad"; tail -30 "$work/out.txt"; fails=$((fails + 1)); fi
}
# mail NAME CHECKS...: the one email sent, read by tests/incident/read_mail.py (recipients, attachments, their text)
mail() {
    local tname="$1"; shift
    if out=$("$py" "$here/read_mail.py" "$work/mail" "$@" 2>&1); then echo "ok   -   $tname"; else echo "FAIL -   $tname: $out"; fails=$((fails + 1)); fi
}

run "AAR and SOP, attached; website not written even when given at launch" ok incident_aar.yml -e @"$work/aar.yml" \
    -e "{\"report_web_dest\": \"$work/web\"}" -- "Made: AAR-Web-portal-outage.docx, SOP-Web-portal-returns-503.docx" "To: ops@example.mil" "not-an-address"
mail "to the address typed only; both Word documents; logo and banner; typed text as typed" to=ops@example.mil \
    attach=AAR-Web-portal-outage.docx,SOP-Web-portal-returns-503.docx "AAR-Web-portal-outage.docx~Rolled teh package back." \
    "AAR-Web-portal-outage.docx~Failed over to 192.0.2.30" "AAR-Web-portal-outage.docx~Bell pasted" logo=AAR-Web-portal-outage.docx \
    "banner=CONTROLLED UNCLASSIFIED INFORMATION (CUI)" "SOP-Web-portal-returns-503.docx~Restart the app pool" "subject=Outage AAR: Web portal outage"
if [ -e "$work/web" ]; then echo "FAIL -   the report website was written"; fails=$((fails + 1)); else echo "ok   -   nothing on the report website"; fi

# 1.3.0: incident_docs_banner auto follows the reports' banner (classified: SECRET); your own text still wins
run "classified (site_enclave): the documents say SECRET without incident_docs_banner" ok incident_aar.yml -e @"$work/aar.yml" \
    -e site_enclave=classified -- "Made: AAR-Web-portal-outage.docx"
mail "the AAR and SOP banner SECRET, the email's subject (SECRET)" "banner=SECRET" "subject=(SECRET) [AAP] Outage AAR: Web portal outage"
run "classified, incident_docs_banner set: that wins" ok incident_aar.yml -e @"$work/aar.yml" -e site_enclave=classified \
    -e '{"incident_docs_banner": "SECRET//REL TO EXAMPLE"}' -- "Made: AAR-Web-portal-outage.docx"
mail "its own marking" "banner=SECRET//REL TO EXAMPLE"

run "AAR only" ok incident_aar.yml -e @"$work/aar.yml" -e incident_make_sop=No -- "Made: AAR-Web-portal-outage.docx\""
mail "one attachment" attach=AAR-Web-portal-outage.docx

run "SOP from the AAR: its step boxes empty" ok incident_aar.yml -e @"$work/aar.yml" -e '{"sop_troubleshoot": "", "sop_fix": ""}' \
    -- "Made: AAR-Web-portal-outage.docx, SOP-Web-portal-returns-503.docx"
mail "the AAR's Actions and Recovery as the SOP's steps, said" attach=AAR-Web-portal-outage.docx,SOP-Web-portal-returns-503.docx \
    "SOP-Web-portal-returns-503.docx~Failed over to 192.0.2.30" "SOP-Web-portal-returns-503.docx~Rolled teh package back." \
    "SOP-Web-portal-returns-503.docx~Taken from the AAR's Actions as written" "SOP-Web-portal-returns-503.docx~Signs it is happening" \
    "body=steps from the AAR's Actions and Recovery as written"

run "ACT writes the SOP's steps from the AAR" ok incident_aar.yml -e @"$work/aar.yml" -e '{"sop_troubleshoot": "", "sop_fix": ""}' \
    -e incident_act_sop=Yes -e "$(model $((port + 1)))" -- "SOP steps written by ACT from the AAR: troubleshoot, fix, verify"
mail "ACT's steps, marked for review; the AAR as typed" "SOP-Web-portal-returns-503.docx~Roll the PHP package back." \
    "SOP-Web-portal-returns-503.docx~Log in to the portal from outside." "SOP-Web-portal-returns-503.docx~Written by GenAI (ACT) from the AAR" \
    "AAR-Web-portal-outage.docx~Rolled teh package back." "body=steps written by GenAI (ACT) from the AAR"

run "ACT's steps name an address the AAR does not have: not used" ok incident_aar.yml -e @"$work/aar.yml" \
    -e '{"sop_troubleshoot": "", "sop_fix": ""}' -e incident_act_sop=Yes -e "$(model $((port + 2)))" -- "192.0.2.99, not in the AAR"
mail "the AAR's Actions as written instead" "SOP-Web-portal-returns-503.docx~Failed over to 192.0.2.30" \
    "SOP-Web-portal-returns-503.docx~Roll the PHP package back."

run "SOP asked for with no steps at all: AAR only, said" ok incident_aar.yml -e @"$work/aar.yml" \
    -e '{"sop_troubleshoot": "", "sop_fix": "", "incident_actions": "", "incident_recovery": ""}' -- "No SOP:"
mail "the email says why" attach=AAR-Web-portal-outage.docx "body=No SOP:"

run "SOP draft asked, no AAP credential: the AAR sent, said" ok incident_aar.yml -e @"$work/aar.yml" -e incident_make_sop=No \
    -e incident_sop_draft=Yes -- "Made: AAR-Web-portal-outage.docx\"" "No SOP draft: No AAP connection"
mail "said in the email" attach=AAR-Web-portal-outage.docx "body=No SOP draft"

export CONTROLLER_HOST="$aap_url" CONTROLLER_OAUTH_TOKEN=goodtoken
run "SOP draft: the SOP job's form filled in from the AAR" ok incident_aar.yml -e @"$work/aar.yml" -e incident_make_sop=No \
    -e incident_sop_draft=Yes -e '{"sop_troubleshoot": "", "sop_fix": "", "sop_title": ""}' -- "The form of 'SOP - Word document' is filled in"
mail "the email says so" attach=AAR-Web-portal-outage.docx "body=The form of 'SOP - Word document' is filled in"
if [ "$(draft sop_troubleshoot)" = "$(printf 'Opened a bridge\nFailed over to 192.0.2.30\nBell pasted')" ] \
   && [ "$(draft sop_title)" = "Troubleshoot and fix: Web portal outage" ] && [ "$(draft incident_name)" = "Web portal outage" ]; then
    echo "ok   -   the form's answers: the AAR's Actions as the steps, its title, the outage"
else echo "FAIL -   the form's answers: $(draft sop_troubleshoot) / $(draft sop_title)"; fails=$((fails + 1)); fi
run "the SOP job launched from another outage: the draft stays" ok incident_sop.yml -e @"$work/aar.yml" -e incident_name=Other \
    -- "is not 'Other'"
[ "$(draft sop_title)" = "Troubleshoot and fix: Web portal outage" ] && echo "ok   -   the draft is still there" \
    || { echo "FAIL -   the draft was emptied"; fails=$((fails + 1)); }
run "the SOP job launched from the draft: made, the draft emptied" ok incident_sop.yml -e @"$work/aar.yml" -- "default answer(s) written"
[ -z "$(draft sop_troubleshoot)$(draft incident_name)" ] && echo "ok   -   the form is empty again" \
    || { echo "FAIL -   the form still has the draft"; fails=$((fails + 1)); }
unset CONTROLLER_HOST CONTROLLER_OAUTH_TOKEN

run "ACT tidies and suggests" ok incident_aar.yml -e @"$work/aar.yml" -e incident_act_tidy=Yes -e incident_act_suggest=Yes -e "$(model $((port + 1)))" \
    -- "Wording tidied:" "2 suggestion(s) from ACT"
mail "tidied text and the From ACT sections" "AAR-Web-portal-outage.docx~Rolled the package back." "AAR-Web-portal-outage.docx~received at 10:30" \
    "AAR-Web-portal-outage.docx~From ACT - suggestions to review" "SOP-Web-portal-returns-503.docx~Confirm a user can log in from outside." \
    "AAR-Web-portal-outage.docx~tidied by GenAI" "body=Wording tidied:"
grep -q "TIDY:" "$work/good.log" && grep -q "SUGGEST:" "$work/good.log" && echo "ok   -   the model was asked to tidy and suggest" \
    || { echo "FAIL -   the model's request"; fails=$((fails + 1)); }

run "ACT drops a number: that box kept as typed" ok incident_aar.yml -e @"$work/aar.yml" -e incident_act_tidy=Yes -e "$(model $((port + 2)))" \
    -- "kept as typed"
mail "the box with the number as typed" "AAR-Web-portal-outage.docx~Failed over to 192.0.2.30"

run "ACT answers with no JSON: the text as typed, said" ok incident_aar.yml -e @"$work/aar.yml" -e incident_act_suggest=Yes -e "$(model $((port + 3)))" \
    -- "ACT was not used:"
mail "said in the email" "body=ACT was not used:" "AAR-Web-portal-outage.docx~Rolled teh package back."

GENAI_KEY="" run "ACT asked, no API key: said, documents made" ok incident_aar.yml -e @"$work/aar.yml" -e incident_act_tidy=Yes \
    -- "ACT was not used:" "no API key"

run "the SOP job" ok incident_sop.yml -e @"$work/aar.yml" -e incident_name= -- "Made: SOP-Web-portal-returns-503.docx\""
mail "only the SOP" attach=SOP-Web-portal-returns-503.docx "subject=SOP: Web portal returns 503"

run "the SOP job with no steps: refused" fail incident_sop.yml -e @"$work/aar.yml" -e '{"sop_troubleshoot": "", "sop_fix": ""}' -- "the SOP has no steps"
run "no address: refused" fail incident_aar.yml -e @"$work/aar.yml" -e report_email_also= -- "Email the documents to"
run "no start: refused" fail incident_aar.yml -e @"$work/aar.yml" -e incident_start= -- "start date and time"
run "a dry run (Check): made, not sent" ok incident_aar.yml -e @"$work/aar.yml" --check -- "(a dry run: not sent)"
if ls "$work/mail/"*.eml >/dev/null 2>&1; then echo "FAIL -   a dry run sent an email"; fails=$((fails + 1)); else echo "ok   -   nothing sent"; fi
run "no logo file: said, documents made" ok incident_aar.yml -e @"$work/aar.yml" -e report_logo_file=/nonexistent.png -- "is not there, or not a PNG"

if [ $fails -eq 0 ]; then echo "all outage AAR / SOP scenarios passed"; else echo "$fails FAILED"; exit 1; fi
