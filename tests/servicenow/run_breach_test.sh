#!/usr/bin/env bash
# About to breach (1.59.0, roles/servicenow_insights): the fake ServiceNow (snmock.py) with breach_seed.py's made-up site and a
# fixed clock (2026-11-13 07:00 UTC). servicenow_insights_mode breach: only the running SLAs and the breaches read, its own
# page ({template}/about-to-breach/latest.html, off the index), its email to servicenow_breach_email_to with the workbook,
# the queue least time left first (one row per ticket; paused, closed, outside the window and cancelled left out), the tiles,
# the weeks 13 / 16 / 21. full: the section first on the All page, a team's page only that team's tickets. task_sla
# refused: said, the full report whole; breach mode fails (servicenow_insights_fail). Check: read and printed, nothing written.
#   bash tests/servicenow/run_breach_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
port="${BREACH_TEST_PORT:-18395}"
smtp="${BREACH_SMTP_PORT:-18396}"
pid=""
spid=""
fails=0
cleanup() { for p in "$pid" "$spid"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; [ -n "${BREACH_KEEP:-}" ] && cp -r "$work" "$BREACH_KEEP"; rm -rf "$work"; }
trap cleanup EXIT
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
INV
"$py" "$here/breach_seed.py" "$work/seed.json" >/dev/null
BASE='"servicenow_insights_now": "2026-11-13T07:00:00", "servicenow_insights_days": 30, "servicenow_insights_act": false,
 "servicenow_insights_hierarchy_source": "names", "servicenow_insights_team_min": 1,
 "servicenow_insights_ticket_prefixes": {"XINC": "incident", "XRITM": "sc_req_item"},
 "servicenow_insights_vip_fields": [{"label": "VIP", "column": "vip"}]'
MAILV="\"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $smtp, \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\""

mock() {
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    : > "$work/log.jsonl"
    env SNMOCK_LOG="$work/log.jsonl" SNMOCK_SEED="$work/seed.json" SNMOCK_VIP='{"vip": ["Pat Doe"]}' "$@" "$py" "$here/snmock.py" "$port" &
    pid=$!
    for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
}

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output (and the mail) must contain each TEXT; !TEXT must not be there.
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && env SN_HOST="http://127.0.0.1:$port" SN_USERNAME=api SN_PASSWORD=goodpw \
        ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles:vendor/act/ansible/roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 \
        "$ap" -i "$work/inv.yml" playbooks/servicenow_insights.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal' "$work/out.txt" | head -8 | cut -c1-400; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            if grep -qF -- "${t:1}" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null; then echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1; fi
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
mkdir -p "$work/site" "$work/full" "$work/off" "$work/deny" "$work/check"     # the website's folder is there already, as on a share
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done

# ---- breach mode: the quick read, its own page, its email ---------------------------------------------------------------------
run "breach mode: the queue, the tiles, the weeks; its page and email" ok \
    "{$BASE, $MAILV, \"servicenow_insights_mode\": \"breach\", \"report_web_dest\": \"$work/site\", \"report_email_to\": [\"all@example.mil\"],
      \"servicenow_breach_email_to\": [\"shift@example.mil\"], \"servicenow_insights_xlsx\": true}" \
    'About to breach: 3 tickets breach within the hour; breaches down from 16 to 13 this week' \
    'Breach within 1 h: 3 (1 not assigned to anyone)' 'Within 4 h: 5 (incl. the 3 above)' 'Within 8 h: 6' 'Breached today: 2' \
    'Last 7 days (7-13 Nov)  13' '31 Oct - 6 Nov' '24-30 Oct' 'Response SLAs: ' \
    '"servicenow_insights_breach":' '!XINC0041990' '!XINC0041995' '!XINC0041998' '!Ticket insights for'
page="$work/site/servicenow-ticket-insights/about-to-breach/latest.html"
check "its page: the queue least time left first, nobody flagged, VIP, links into ServiceNow, the hook" "$page" \
    'ServiceNow - about to breach' 'id="breach"' '3 tickets breach within the hour; breaches down from 16 to 13 this week' \
    '<b class="c-crit nw">24 min</b>' '<b class="c-crit nw">42 min</b>' '<b class="c-crit nw">54 min</b>' '<b class="c-warn nw">1 h 36 min</b>' \
    '<b class="c-warn nw">2 h 18 min</b>' '<b class="c-info nw">7 h 06 min</b>' '<b class="c-warn">nobody</b>' '<span class="chip info">VIP</span>' \
    "http://127.0.0.1:$port/nav_to.do?uri=incident.do%3Fsysparm_query%3Dnumber%3DXINC0041872" \
    "http://127.0.0.1:$port/nav_to.do?uri=sc_req_item.do%3Fsysparm_query%3Dnumber%3DXRITM0019220" \
    'class="breach-trend" data-points=' "Every day's number (30 days)" 'P2 Incident Response' \
    'href="../latest.html">ticket insights</a>' '!XINC0041990' '!XINC0041995' '!XINC0041998'
"$py" - "$page" <<'PYQ' && echo "ok   -   ... the rows in order: least time left first, one per ticket" || { echo "FAIL - the queue's order"; fails=$((fails + 1)); }
import re, sys
html = open(sys.argv[1]).read()
nums = re.findall(r'number%3D(X[A-Z]+\d+)"', html.split('id="breach"')[1].split("Breaches, last")[0])
assert nums == ["XINC0041872", "XINC0041901", "XRITM0019220", "XINC0041915", "XINC0041930", "XINC0041977"], nums
PYQ
[ ! -e "$work/site/index.html" ] || ! grep -q 'about-to-breach' "$work/site/index.html" \
    && echo "ok   -   ... not on the website's index" || { echo "FAIL - on the index"; fails=$((fails + 1)); }
mails=$(ls "$work"/mail/*.eml 2>/dev/null | wc -l)
if [ "$mails" = 1 ] && grep -q '^To: shift@example.mil' "$work"/mail/*.eml && ! grep -q 'all@example.mil' "$work"/mail/*.eml \
    && grep -q 'about-to-breach.*\.xlsx' "$work"/mail/*.eml && ! grep -q 'VPN drops every' "$work"/mail/*.eml; then
    echo "ok   -   ... one email to servicenow_breach_email_to only, the workbook attached, no description in it"
else echo "FAIL - the breach email ($mails)"; fails=$((fails + 1)); fi
"$py" - "$work/mail" <<'PYX' && echo "ok   -   ... the workbook: every ticket, every day, every breached SLA, the days' trend line" || { echo "FAIL - the workbook"; fails=$((fails + 1)); }
import email, glob, io, sys, zipfile, re
msg = email.message_from_binary_file(open(glob.glob(sys.argv[1] + "/*.eml")[0], "rb"))
x = [p for p in msg.walk() if (p.get_filename() or "").endswith(".xlsx")][0]
z = zipfile.ZipFile(io.BytesIO(x.get_payload(decode=True)))
wb = z.read("xl/workbook.xml").decode()
names = re.findall(r'<sheet name="([^"]+)"', wb)
assert names == ["About to breach", "Breaches by day", "Breached SLAs"], names
shared = z.read("xl/sharedStrings.xml").decode() if "xl/sharedStrings.xml" in z.namelist() else "".join(z.read(n).decode() for n in z.namelist() if n.startswith("xl/worksheets/"))
for t in ("XINC0041872", "XINC0041977", "VPN drops every 20 minutes"):
    assert t in shared, t
assert "XINC0040998" not in shared and "XINC0040999" not in shared
# 1.62.0: Breaches by day's 30 days as one Excel sparkline, in the first day's Trend cell
day = z.read("xl/worksheets/sheet2.xml").decode()
rows = len(re.findall(r"<row ", day)) - 1
assert re.findall(r"<xm:f>([^<]+)</xm:f><xm:sqref>([^<]+)</xm:sqref>", day) == [("'Breaches by day'!B2:B%d" % (rows + 1), "F2")], day[-600:]
PYX
rm -f "$work"/mail/*.eml

run "breach mode, servicenow_breach_email_to empty: the page only" ok \
    "{$BASE, $MAILV, \"servicenow_insights_mode\": \"breach\", \"report_web_dest\": \"$work/site\", \"report_email_to\": [\"all@example.mil\"]}" \
    'About to breach: 3 tickets'
[ -z "$(ls "$work"/mail/*.eml 2>/dev/null)" ] && echo "ok   -   ... no email" || { echo "FAIL - an email went out"; fails=$((fails + 1)); }

run "breach mode, a 2-hour window: the bands 1 h and 2 h" ok \
    "{$BASE, \"servicenow_insights_mode\": \"breach\", \"servicenow_breach_window_hours\": 2, \"report_web_dest\": \"\"}" \
    'Breach within 1 h: 3' 'Within 2 h: 4' '!Within 4 h' '!Within 8 h'

run "a mode that is not one: stopped, said" fail "{$BASE, \"servicenow_insights_mode\": \"hourly\"}" \
    'servicenow_insights_mode is full (the whole report) or breach (only About to breach), not hourly'

# ---- full mode: the section first, each page its own groups ---------------------------------------------------------------------
run "full: About to breach at the top of the report" ok "{$BASE, $MAILV, \"report_web_dest\": \"$work/full\", \"report_email_to\": [\"ops@example.mil\"]}" \
    'Ticket insights for All assignment groups' 'About to breach' '3 tickets breach within the hour; breaches down from 16 to 13 this week'
top="$work/full/servicenow-ticket-insights"
check "the All page: the section first, before At a glance" "$top/latest.html" \
    'id="breach"' '<a href="#breach">About to breach <span class="c">6</span></a>' '<b class="c-crit nw">24 min</b>' 'XINC0041977'
"$py" - "$top/latest.html" <<'PYF' && echo "ok   -   ... before the Custom view and At a glance" || { echo "FAIL - the section's place"; fails=$((fails + 1)); }
import sys
h = open(sys.argv[1]).read()
main = h.split("<main>")[1]
assert main.index('id="breach"') < main.index('id="glance"'), "order"
PYF
team=$(grep -rl 'Ops\|Network Operations ·' "$top" --include=latest.html | xargs grep -l '<h1>ServiceNow ticket insights</h1>' | xargs grep -l 'Network Operations ·' | head -1)
if [ -n "$team" ]; then
    check "a team's page: only its tickets" "$team" 'id="breach"' 'XINC0041872' 'XINC0041915' '!XINC0041901' '!XRITM0019220'
else echo "FAIL - no Network Operations page"; fails=$((fails + 1)); fi
grep -q 'About to breach, least time left first' "$work"/mail/*.eml && grep -q 'Breaches, last 30 days' "$work"/mail/*.eml \
    && ! grep -q 'VPN drops every' "$work"/mail/*.eml \
    && echo "ok   -   ... the email: the section first, no description" || { echo "FAIL - the full email"; fails=$((fails + 1)); }
rm -f "$work"/mail/*.eml

run "full, servicenow_insights_breach false: no section, no task_sla read for it" ok "{$BASE, \"servicenow_insights_breach\": false, \"report_web_dest\": \"$work/off\"}" \
    'Ticket insights for All assignment groups' '!About to breach, least time left first'
check "  no section" "$work/off/servicenow-ticket-insights/latest.html" '!id="breach"'

# ---- task_sla refused ------------------------------------------------------------------------------------------------------------
mock SNMOCK_NO_TABLES=task_sla
run "full, task_sla refused: said in the section, the report whole" ok "{$BASE, \"report_web_dest\": \"$work/deny\"}" \
    'Ticket insights for All assignment groups' 'About to breach' 'The running SLAs were not read: GET api/now/table/task_sla: HTTP 403'
run "breach mode, task_sla refused: the job fails, says why" fail "{$BASE, \"servicenow_insights_mode\": \"breach\", \"report_web_dest\": \"$work/deny\"}" \
    'the account needs read on task_sla'
mock

CHECK=1 run "Check: read and printed, nothing written" ok "{$BASE, \"servicenow_insights_mode\": \"breach\", \"report_web_dest\": \"$work/check\"}" \
    'About to breach: 3 tickets breach within the hour'
[ ! -e "$work/check/servicenow-ticket-insights/about-to-breach/latest.html" ] && echo "ok   -   ... nothing written" || { echo "FAIL - Check wrote"; fails=$((fails + 1)); }

"$py" - "$work/log.jsonl" <<'PYG' && echo "ok   - every request a GET" || { echo "FAIL - a request that is not a GET"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
assert rows and all((r.get("method") or "GET") == "GET" for r in rows), [r for r in rows if r.get("method") not in (None, "GET")][:2]
PYG

[ $fails -eq 0 ] && echo "all about to breach scenarios passed" || { echo "$fails failed"; exit 1; }
