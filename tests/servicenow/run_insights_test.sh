#!/usr/bin/env bash
# The ticket insights (roles/servicenow_insights, playbooks/servicenow_insights.yml) against the fake ServiceNow (snmock.py) with
# a year of a made-up site (insights_seed.py) and a fixed clock: six months to 2026-10-31 against the six before; without
# ACT (the issues by category, the fixes the most common close notes) and with a scripted model (fake_insights_model.py: the
# issues grouped, the fixes, a knowledge draft, the summaries - and no real name, phone number or address in what it was
# sent; the real ones back in the page); no ACT key (said, the report whole); the site's page and each team's page on the
# report website, linked to each other; one group named; a group not there; sys_audit or a table unreadable (said, green);
# Check; no credential; emailed with the workbook.
#   bash tests/servicenow/run_insights_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
port="${INSIGHTS_TEST_PORT:-18390}"
mport="${INSIGHTS_MODEL_PORT:-18391}"
smtp="${SMTP_TEST_PORT:-18039}"
pid=""
mpid=""
spid=""
fails=0
cleanup() { for p in "$pid" "$mpid" "$spid"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
INV
"$py" "$here/insights_seed.py" "$work/seed.json" 0.5 >/dev/null
NOW='"servicenow_insights_now": "2026-11-01T06:00:00", "servicenow_insights_until": "2026-10-31"'
NOACT='"servicenow_insights_act": false'
ACT="\"site_act_url\": \"http://127.0.0.1:$mport/v1/chat/completions\", \"site_act_env\": {\"ACT_STREAM\": \"0\"}, \"servicenow_insights_kb_min\": 40"

mock() {
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    : > "$work/log.jsonl"
    env SNMOCK_LOG="$work/log.jsonl" SNMOCK_SEED="$work/seed.json" "$@" "$py" "$here/snmock.py" "$port" &
    pid=$!
    for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
}

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output (and the mail) must contain each TEXT; !TEXT must not be there.
# INS_ENV: more environment. CHECK=1: --check.
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && env SN_HOST="http://127.0.0.1:$port" SN_USERNAME=api SN_PASSWORD=goodpw \
        ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles:vendor/act/ansible/roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 ${INS_ENV:-} \
        "$ap" -i "$work/inv.yml" playbooks/servicenow_insights.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal' "$work/out.txt" | head -8 | cut -c1-400; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            if grep -qF -- "${t:1}" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null; then
                echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1
            fi
        elif ! grep -qF -- "$t" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null; then
            echo "FAIL - $name: lacks: $t"; grep -E '"msg"|fatal' "$work/out.txt" | head -5 | cut -c1-400; fails=$((fails + 1)); return 1
        fi
    done
    echo "ok   - $name"
}

# check NAME FILE TEXT...: the file must contain each TEXT (!TEXT: must not)
check() {
    local name="$1" file="$2" t; shift 2
    if [ ! -f "$file" ]; then echo "FAIL - $name: no $file"; fails=$((fails + 1)); return 1; fi
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            if grep -qF -- "${t:1}" "$file"; then echo "FAIL - $name: $file has: ${t:1}"; fails=$((fails + 1)); return 1; fi
        elif ! grep -qF -- "$t" "$file"; then echo "FAIL - $name: $file lacks: $t"; fails=$((fails + 1)); return 1; fi
    done
    echo "ok   -   ... $name"
}

mock
# ---- without ACT: the site's page and the teams' ----------------------------------------------------------------
mkdir -p "$work/site"
run "six months against the six before, without ACT: by category, the most common close notes, every team its page" ok \
    "{$NOW, $NOACT, \"report_web_dest\": \"$work/site\"}" \
    'Ticket insights for All assignment groups:' 'Window: 2026-05-01 to 2026-10-31, compared with 2025-11-01 to 2026-04-30 (UTC)' \
    'Teams side by side' 'Ops - Service Desk' 'Top incident issues' 'Account / Password' 'Most requested services' \
    'Problem candidates' 'Changes failed or backed out' 'ACT: off (servicenow_insights_act)' 'Published: ' '"servicenow_insights":'
top="$work/site/servicenow-ticket-insights"
check "the site's page: every section, links to the teams, ticket links into ServiceNow" "$top/latest.html" \
    'ServiceNow ticket insights' 'id="teams"' 'id="keepingup"' 'id="severity"' 'id="issues"' 'id="problems"' 'id="handoffs"' \
    'id="requests"' 'id="changes"' 'id="notes"' 'id="allstars"' 'id="kb"' 'id="method"' 'href="ops-windows/latest.html"' \
    "http://127.0.0.1:$port/incident.do?sysparm_query=number=INC" 'grouped by their category' 'From the numbers' \
    'Reopen not used here' 'Came back' 'data-tab="p1"' 'No drafts: ACT (GenAI) writes them' '!cid:' '!<!--site-report-logo-->'
check "a team's page: that team, linked back to the site's" "$top/ops-windows/latest.html" \
    'Ops - Windows ·' '!id="teams"' 'href="../latest.html"' 'All-stars' 'Most resolved' 2>/dev/null || \
check "a team's page: that team, linked back to the site's" "$top/ops-windows/latest.html" 'href="../latest.html"' 'Most resolved' '!id="teams"'
n=$(find "$top" -name latest.html | wc -l)
[ "$n" = 6 ] && echo "ok   -   ... six latest pages (the site and five teams)" || { echo "FAIL - $n latest pages"; fails=$((fails + 1)); }
grep -q 'servicenow-ticket-insights/latest.html' "$work/site/index.json" && echo "ok   -   ... on the website's index" \
    || { echo "FAIL - not on the index"; fails=$((fails + 1)); }

# ---- with ACT (a scripted model) ---------------------------------------------------------------------------------
: > "$work/model.log"
env FAKE_MODEL_LOG="$work/model.log" "$py" "$here/fake_insights_model.py" "$mport" &
mpid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$mport") 2>/dev/null && break; sleep 0.1; done
mkdir -p "$work/site2"
INS_ENV="GENAI_KEY=test-key" run "with ACT: the issues grouped, the fixes written, a knowledge draft, the summaries" ok \
    "{$NOW, $ACT, \"report_web_dest\": \"$work/site2\", \"servicenow_insights_act_batch\": 15}" \
    'Account locked out / password reset' 'Printer offline / print queue stuck' 'ACT: ' 'questions answered' \
    'What the period says (written by ACT)' "Scope all: the model's first point."
p2="$work/site2/servicenow-ticket-insights/latest.html"
check "ACT's page: its issues and fixes (one suggested), a draft, its summary, the fix that is not settled" "$p2" \
    'grouped by ACT' 'Suggested by ACT' 'Fix: Account locked out / password reset' 'Written by ACT from the tables' \
    'Requests opened as incidents.' 'Software install opened as an incident' 'The close notes show different fixes' \
    'Self-service candidate' "Scope all: the model&#x27;s first point."
grep -qE '\b(HOST|PERSON|PHONE|IP|EMAIL)-[0-9]+\b' "$p2" && { echo "FAIL - a placeholder left in the page: $(grep -oE '\b(HOST|PERSON)-[0-9]+\b' "$p2" | head -3)"; fails=$((fails + 1)); } \
    || echo "ok   -   ... the replaced names put back (no placeholder in the page)"
leak=$(grep -oE 'Dana Whitfield|Whitfield|\(703\) 555-[0-9]{4}|198\.51\.100\.[0-9]+|wks-10[0-9][0-9]|prn01-fl[0-9]|Lee Anders' "$work/model.log" | sort -u | head -5)
[ -z "$leak" ] && [ -s "$work/model.log" ] && echo "ok   -   ... the model was sent no real name, phone number, address or host" \
    || { echo "FAIL - the model was sent: $leak"; fails=$((fails + 1)); }
grep -q 'PHONE-[0-9]' "$work/model.log" && echo "ok   -   ... it was sent the placeholders instead" || { echo "FAIL - no placeholder sent"; fails=$((fails + 1)); }

run "ACT on, no model key: said, the report by category" ok "{$NOW, \"report_web_dest\": \"\"}" \
    'ACT: did not run (This job has no API key for provider genai' 'Account / Password'

# ---- groups, what cannot be read, Check, no credential ---------------------------------------------------------------
run "one group named: its tickets only, its page" ok "{$NOW, $NOACT, \"servicenow_insights_groups\": \"Ops - Linux\"}" \
    'Ticket insights for All assignment groups' 'Ops - Linux' '!Ops - Service Desk  '
run "a group that is not there: said, the job fails" fail "{$NOW, $NOACT, \"servicenow_insights_groups\": [\"Linux Ops\"]}" \
    'none of the assignment groups is in ServiceNow: Linux Ops'
run "  ... servicenow_insights_fail false: a report only" ok "{$NOW, $NOACT, \"servicenow_insights_groups\": [\"Linux Ops\"], \"servicenow_insights_fail\": false}" \
    'none of the assignment groups is in ServiceNow: Linux Ops'
mock SNMOCK_NO_TABLES=sys_audit,problem
run "sys_audit and problem unreadable: said under Not read, the rest reported, green" ok "{$NOW, $NOACT}" \
    'sys_audit (the hand-off paths): GET api/now/table/sys_audit: HTTP 403' 'problem: GET api/now/table/problem: HTTP 403' 'Top incident issues'
mock
CHECK=1 run "Check: read and printed, nothing written" ok "{$NOW, $NOACT, \"report_web_dest\": \"$work/site3\"}" 'Ticket insights for All assignment groups'
[ ! -e "$work/site3" ] && echo "ok   -   ... nothing written in Check" || { echo "FAIL - Check wrote $work/site3"; fails=$((fails + 1)); }
INS_ENV="SN_HOST=" run "no credential" fail "{$NOW}" 'it sets SN_HOST, SN_USERNAME, SN_PASSWORD'
run "months 0: said" fail "{$NOW, $NOACT, \"servicenow_insights_months\": 0}" 'servicenow_insights_months must be 1 or more'
run "the last 60 days instead (servicenow_insights_days)" ok "{$NOW, $NOACT, \"servicenow_insights_days\": 60}" 'Window: 2026-09-02 to 2026-10-31'

# ---- emailed with the workbook -----------------------------------------------------------------------------------------
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done
run "the report emailed, the workbook attached" ok "{$NOW, $NOACT, \"report_email_to\": [\"ops@example.mil\"], \"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $smtp, \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\"}" \
    'emailed'
if ls "$work"/mail/*.eml >/dev/null 2>&1 && grep -q "Ticket insights for All assignment groups" "$work"/mail/*.eml && grep -q 'ticket-insights_.*\.xlsx\|servicenow-ticket-insights.*\.xlsx\|\.xlsx' "$work"/mail/*.eml; then
    echo "ok   -   ... the mail has the report and the workbook"
else
    echo "FAIL - no mail with the report and the workbook"; fails=$((fails + 1))
fi

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all ticket insights scenarios passed"
