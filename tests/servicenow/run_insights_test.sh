#!/usr/bin/env bash
# The ticket insights (roles/servicenow_insights, playbooks/servicenow_insights.yml) against the fake ServiceNow (snmock.py) with
# a year of a made-up site (insights_seed.py) and a fixed clock: six months to 2026-10-31 against the six before; without
# ACT (the issues by category, the fixes the most common close notes) and with a scripted model (fake_insights_model.py: the
# issues grouped, the fixes, a knowledge draft, the summaries - and no real name, phone number or address in what it was
# sent; the real ones back in the page); no ACT key (said, the report whole); the site's page and each team's page on the
# report website, linked to each other; one group named; a group not there; sys_audit or a table unreadable (said, green);
# Check; no credential; emailed with the workbook. 0.96.0: the team picker and the side bar's filter; ACT reads the top issues'
# work notes (sys_journal_field, GET only, masked - what only they hold too), the page says what each fix was read from; the
# work notes refused (403): said, the fixes from the close notes; off: not read. 1.2.0: the change tasks (change_task, GET
# only) - the teams' columns, Held up by, the leaderboards' own column; refused (403): said, the rest whole, green.
# 1.7.0: task_sla read (Response and Resolution met, No SLO, SLA at risk now, act on first, how incidents ended, the
# categories, the surveys); task_sla refused: the targets in business hours, a response from the metric Assigned to, said.
# 1.9.0: the time in each state (the state metric), the backlog's age and flow, quality and hot spots, after hours, workload.
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
NOW='"servicenow_insights_now": "2026-11-01T06:00:00", "servicenow_insights_until": "2026-10-31", "servicenow_insights_hierarchy_source": "names"'
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
mkdir -p "$work/site/kept"
# the index before: a team line an older run put there (dropped), a report whose folder was deleted by hand (pruned), one
# that is still there (kept)
echo '<html>kept</html>' > "$work/site/kept/latest.html"
cat > "$work/site/index.json" <<'EOJ'
{"generated": "2026-10-01 05:00 UTC", "reports": [
 {"key": "servicenow-ticket-insights/ops-windows/latest.html", "latest": "servicenow-ticket-insights/ops-windows/latest.html", "name": "ServiceNow - ticket insights", "status": "warning", "when": "2026-10-01 05:00 UTC"},
 {"key": "gone/latest.html", "latest": "gone/latest.html", "name": "Gone - report", "status": "ok", "when": "2026-10-01 05:00 UTC"},
 {"key": "kept/latest.html", "latest": "kept/latest.html", "name": "Kept - report", "status": "ok", "when": "2026-10-01 05:00 UTC"}]}
EOJ
run "six months against the six before, without ACT: by category, the most common close notes, every team its page" ok \
    "{$NOW, $NOACT, \"report_web_dest\": \"$work/site\"}" \
    'Ticket insights for All assignment groups:' 'Window: 2026-05-01 to 2026-10-31, compared with 2025-11-01 to 2026-04-30 (UTC)' \
    'Teams side by side' 'Ops - Service Desk' 'Top incident issues' 'Account / Password' 'Most requested services' \
    'Problem candidates' 'Changes failed or backed out' 'ACT: off (servicenow_insights_act)' 'Published: ' '"servicenow_insights":' \
    'Change tasks closed' 'Held up by: the team whose task a change waited on last' 'Changes closed with tasks: '
top="$work/site/servicenow-ticket-insights"
"$py" - "$work/log.jsonl" <<'PYJ' && echo "ok   -   ... change_task read with GET only" || { echo "FAIL - the change tasks' requests"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
c = [r for r in rows if "/change_task" in json.dumps(r)]
assert c and all((r.get("method") or "GET") == "GET" for r in c), c[:2]
assert all("planned_end_date" in json.dumps(r) for r in c), c[:1]
PYJ
check "the change tasks on the site's page: the teams' columns, Held up by, the leaderboards' column" "$top/latest.html" \
    'Change tasks closed' 'Change tasks open' '<h3 class="subh">Change tasks</h3>' 'Held up by' 'Tasks per change' 'Open change tasks by age' \
    'Change tasks = how many of them were change tasks' '<th class="n">Closed</th><th class="n">Change tasks</th>'
check "the site's page: every section, links to the teams, ticket links into ServiceNow" "$top/latest.html" \
    'ServiceNow ticket insights' 'id="teams"' 'id="keepingup"' 'id="severity"' 'id="issues"' 'id="problems"' 'id="handoffs"' \
    'id="requests"' 'id="changes"' 'id="notes"' 'id="allstars"' 'id="kb"' 'id="method"' 'href="ops/other/windows/latest.html"' 'Teams side by side' \
    "http://127.0.0.1:$port/incident.do?sysparm_query=number=INC" 'grouped by their category' 'From the numbers' \
    'Reopen not used here' 'Came back' 'Response met' 'Resolution met' 'No SLO' 'P4 Incident Response' 'Act on first' 'SLA at risk now'
    'How incidents ended' 'Priority set by hand' 'id="categories"' 'id="satisfaction"' 'Time in each state' 'The numbers as a table'
    'Backlog age and flow' 'Ping-pong' 'id="afterhours"' 'id="workload"' 'data-tab="p1"' 'No drafts: ACT (GenAI) writes them' '!cid:' '!<!--site-report-logo-->'
check "a team's page: that team, linked back to the site's" "$top/ops/other/windows/latest.html" \
    'Ops - Windows ·' '!id="teams"' 'href="../../../latest.html"' 'All-stars' 'Most closed' 2>/dev/null || \
check "a team's page: that team, linked back to the site's" "$top/ops/other/windows/latest.html" 'href="../../../latest.html"' 'Most closed' '!id="teams"'
# 0.88.0: the page's script as a file beside the index (a web server that blocks inline scripts runs it), the teams as plain links,
# the leaderboard = the group's members (a quiet member on it, the desk's closer not), who sends the team its tickets, short group names
check "the script loaded from site-insights.js, relative to each page; the pages as links without it" "$top/ops/other/windows/latest.html" \
    '<script src="../../../../site-insights.js?v=' 'data-file="site-insights.js"' '<details class="picker" id="picker">' \
    '<a class="team lv3" href="../linux/latest.html" data-n="ops - linux linux" data-c="ops other" title="Ops - Linux">Linux</a>' '<input class="tf js" id="teamq"' \
    '<span class="pk-v">Other · Windows</span>' '!class="teamnav"' '<nav class="crumbs" aria-label="Where this page is"><a href="../../../latest.html">All</a>' \
    'Riley Chen' '!Dana Whitfield</td><td><b>' 'Who sends this team its tickets' "members, from ServiceNow's group membership" \
    '<a class="hop glink" href="../service-desk/latest.html" title="Ops - Service Desk">Service Desk</a>' '<title>Week of '
[ -s "$work/site/site-insights.js" ] && grep -q 'window.siteInsights' "$work/site/site-insights.js" && ! grep -q 'site-insights.js' "$work/site/index.html" \
    && echo "ok   -   ... site-insights.js at the top of the website, the index does not load it" \
    || { echo "FAIL - site-insights.js not written at the top (or the index loads it)"; fails=$((fails + 1)); }
n=$(find "$top" -name latest.html | wc -l)
[ "$n" = 8 ] && echo "ok   -   ... eight latest pages (All, the site Ops, its directorate Other, five teams)" || { echo "FAIL - $n latest pages"; fails=$((fails + 1)); }
grep -q 'servicenow-ticket-insights/latest.html' "$work/site/index.json" && echo "ok   -   ... on the website's index" \
    || { echo "FAIL - not on the index"; fails=$((fails + 1)); }
check "the page loads its script from the website (a CSP that blocks inline script runs it); hover text without it" "$top/latest.html" \
    'src="../site-insights.js?v=' 'id="site-report-js" data-file="site-insights.js"' '<title>Week of' 'Busiest week: week of'
check "  ... a team's page too" "$top/ops/other/windows/latest.html" 'src="../../../../site-insights.js?v='
[ -s "$work/site/site-insights.js" ] && grep -q 'siteInsights' "$work/site/site-insights.js" && ! LC_ALL=C grep -qP '[^\x00-\x7f]' "$work/site/site-insights.js" \
    && echo "ok   -   ... site-insights.js beside the index (ASCII)" || { echo "FAIL - site-insights.js"; fails=$((fails + 1)); }
keys=$("$py" -c "import json,sys; print(' '.join(sorted(r['key'] for r in json.load(open(sys.argv[1]))['reports'])))" "$work/site/index.json")
[ "$keys" = "kept/latest.html servicenow-ticket-insights/latest.html" ] \
    && echo "ok   -   ... one line on the index (no team lines; the old team line and the deleted report's line taken off)" \
    || { echo "FAIL - the index has: $keys"; fails=$((fails + 1)); }
grep -qF 'taken off the index: ' "$work/out.txt" && echo "ok   -   ... the job says what it took off" \
    || { echo "FAIL - the job does not say what it took off the index"; fails=$((fails + 1)); }

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
# 0.96.0: the work notes - read (GET only), sent masked (what only they hold too), each fix says what it was read from
grep -q 'WORK NOTES - what was tried' "$work/model.log" && echo "ok   -   ... the top issues' work notes sent to ACT" \
    || { echo "FAIL - no work notes in what ACT was sent"; fails=$((fails + 1)); }
leak=$(grep -oE 'vpn-help\.wn@example\.mil|wn-gw-07|\(571\) 555-[0-9]{4}' "$work/model.log" | sort -u | head -3)
[ -z "$leak" ] && echo "ok   -   ... the work notes masked (an address, a gateway, a phone number only they hold)" \
    || { echo "FAIL - the work notes sent as they are: $leak"; fails=$((fails + 1)); }
"$py" - "$work/log.jsonl" <<'PYJ' && echo "ok   -   ... sys_journal_field read with GET only, element=work_notes" || { echo "FAIL - the work notes' requests"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
j = [r for r in rows if "sys_journal_field" in json.dumps(r)]
assert j and all((r.get("method") or "GET") == "GET" for r in j), j[:2]
assert all("element=work_notes" in json.dumps(r) for r in j)
assert all((r.get("method") or "GET") == "GET" for r in rows), [r for r in rows if r.get("method") != "GET"][:2]
PYJ
check "  ... the page: what each fix was read from, the printers' repeated workaround (a KB candidate)" "$p2" \
    ' work notes of ' '<b>Repeated workaround:</b> Restarting the Print Spooler, again and again (the work notes).' '!Work notes were not read'
mock SNMOCK_NO_TABLES=sys_journal_field
mkdir -p "$work/site4"
INS_ENV="GENAI_KEY=test-key" run "  ... the work notes refused (403): said, the fixes from the close notes, green" ok \
    "{$NOW, $ACT, \"report_web_dest\": \"$work/site4\", \"servicenow_insights_act_batch\": 15}" \
    "sys_journal_field (the incidents' work notes, for ACT's fixes): GET api/now/table/sys_journal_field: HTTP 403" 'the account needs read on sys_journal_field'
check "  ... said on the page" "$work/site4/servicenow-ticket-insights/latest.html" 'Work notes were not read (GET api/now/table/sys_journal_field: HTTP 403' \
    'the fixes come from the close notes only' '!Repeated workaround'
mock
INS_ENV="GENAI_KEY=test-key" run "  ... servicenow_insights_act_work_notes false: not read" ok \
    "{$NOW, $ACT, \"report_web_dest\": \"\", \"servicenow_insights_act_batch\": 15, \"servicenow_insights_act_work_notes\": false}" 'questions answered'
grep -q 'sys_journal_field' "$work/log.jsonl" && { echo "FAIL - the work notes read though off"; fails=$((fails + 1)); } || echo "ok   -   ... sys_journal_field not asked"

run "ACT on, no model key: said, the report by category" ok "{$NOW, \"report_web_dest\": \"\"}" \
    'ACT: did not run (This job has no API key for provider genai' 'Account / Password'

# ---- sites, directorates and teams (0.97.0): the same records, the groups named as three made-up sites ----------------
"$py" "$here/insights_seed.py" "$work/seed3.json" 0.5 --sites >/dev/null
mock SNMOCK_SEED="$work/seed3.json"
mkdir -p "$work/site5"
run "three sites from ServiceNow's group tree (parents): a page for each site, directorate and team, and a split" ok \
    "{$NOW, $NOACT, \"servicenow_insights_hierarchy_source\": \"parents\", \"report_web_dest\": \"$work/site5\", \"servicenow_insights_team_min\": 5,
      \"servicenow_insights_split_label\": \"BLUE\", \"servicenow_insights_split_pattern\": \"*- nova\"}" \
    'Sites side by side' 'SITEA' 'SITEB'
t5="$work/site5/servicenow-ticket-insights"
for f in latest.html sitea/latest.html sitea/j6-staff-division/latest.html sitea/j6-staff-division/j6-service-desk/latest.html \
         sitea/site-direct/j63-windows/latest.html siteb/site-direct/ops-vdi-nova/latest.html siteb/client-support/latest.html \
         unknown/unknown/site-c-codex-linux/latest.html latest-blue.html latest-non-blue.html sitea/latest-non-blue.html \
         siteb/site-direct/ops-vdi-nova/latest-blue.html; do
    [ -s "$t5/$f" ] || { echo "FAIL - no $f"; fails=$((fails + 1)); }
done
n=$(find "$t5" -name latest.html | wc -l)
[ "$n" = 13 ] && echo "ok   -   ... 13 pages: All, 3 sites (Unknown one), 5 directorates, 4 teams (the inactive group none)" \
    || { echo "FAIL - $n pages"; fails=$((fails + 1)); }
n=$(find "$t5" -name 'latest-*blue.html' | wc -l)
[ "$n" = 26 ] && echo "ok   -   ... each page twice more: BLUE and non-BLUE (26)" || { echo "FAIL - $n split pages"; fails=$((fails + 1)); }
[ ! -e "$t5/siteb/client-support/network/latest.html" ] && echo "ok   -   ... no page for the inactive group" || { echo "FAIL - an inactive group's page"; fails=$((fails + 1)); }
check "  ... All: the sites side by side, the tree, the switch" "$t5/latest.html" 'Sites side by side' '<a class="teamlink" href="sitea/latest.html" title="SITEA">SITEA</a>' \
    '<li class="lv1"><a href="sitea/latest.html"' '<li class="lv2"><a href="siteb/client-support/latest.html"' \
    '<nav class="split" aria-label="Which groups"><a href="latest.html" aria-current="page">All</a><a href="latest-blue.html">BLUE</a><a href="latest-non-blue.html">non-BLUE</a></nav>' \
    '<b>Sites, directorates and teams</b>' 'by their ServiceNow parent' '<dt>No page</dt><dd>SITEB - Network<br><span class="mut">Inactive in ServiceNow. Its tickets are in SITEB / Client Support.</span>' \
    '<dt>Unknown</dt><dd>SITE-C - CODEX - Linux ('
check "  ... a directorate: its breadcrumbs and teams" "$t5/sitea/j6-staff-division/latest.html" 'Teams side by side' \
    '<nav class="crumbs" aria-label="Where this page is"><a href="../../latest.html">All</a><span class="sep" aria-hidden="true">›</span><a href="../latest.html">SITEA</a>' \
    '<span class="pk-v">SITEA · J6 Staff Division</span>' 'href="j6-service-desk/latest.html"'
check "  ... a BLUE page links BLUE pages" "$t5/sitea/latest-blue.html" 'href="../latest-blue.html">All</a>' 'aria-current="page">BLUE</a>' \
    'href="j6-staff-division/latest-blue.html"'
"$py" - "$t5" <<'PYJ' && echo "ok   -   ... the rollup: each site adds up to All, BLUE and non-BLUE to the whole (a ticket once at every level)" || { echo "FAIL - the rollup"; fails=$((fails + 1)); }
import re, sys
def inc(f):
    return int(re.search(r'<span class="eyebrow">Tickets</span><span class="v">([\d,]+)', open(sys.argv[1] + "/" + f).read()).group(1).replace(",", ""))
assert inc("latest.html") == inc("sitea/latest.html") + inc("siteb/latest.html") + inc("unknown/latest.html")
assert inc("sitea/latest.html") == inc("sitea/j6-staff-division/latest.html") + inc("sitea/site-direct/latest.html")
assert inc("latest.html") == inc("latest-blue.html") + inc("latest-non-blue.html")
assert inc("siteb/latest.html") == inc("siteb/latest-blue.html") + inc("siteb/latest-non-blue.html")
PYJ
keys=$("$py" -c "import json,sys; print(' '.join(sorted(r['key'] for r in json.load(open(sys.argv[1]))['reports'])))" "$work/site5/index.json")
[ "$keys" = "servicenow-ticket-insights/latest.html" ] && echo "ok   -   ... one line on the index" || { echo "FAIL - the index: $keys"; fails=$((fails + 1)); }
"$py" - "$work/log.jsonl" <<'PYJ' && echo "ok   -   ... sys_user_group read with GET only, no person's field" || { echo "FAIL - the groups' requests"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
g = [r for r in rows if r["path"].endswith("/sys_user_group") and r["query"].get("sysparm_query", "").startswith("sys_idIN")]
assert g and all(r["method"] == "GET" for r in g)
assert all(r["query"]["sysparm_fields"] == "sys_id,name,parent,active,type" for r in g), g[0]["query"]
PYJ
run "  ... again with servicenow_insights_split_teams false: the teams' split pages gone, the rest kept" ok \
    "{$NOW, $NOACT, \"servicenow_insights_hierarchy_source\": \"parents\", \"report_web_dest\": \"$work/site5\", \"servicenow_insights_team_min\": 5,
      \"servicenow_insights_split_label\": \"BLUE\", \"servicenow_insights_split_pattern\": \"*- nova\", \"servicenow_insights_split_teams\": false}" 'Sites side by side'
n=$(find "$t5" -name 'latest-*blue.html' | wc -l)
[ "$n" = 18 ] && [ ! -e "$t5/siteb/site-direct/ops-vdi-nova/latest-blue.html" ] && [ -s "$t5/siteb/latest-blue.html" ] \
    && echo "ok   -   ... 18 split pages: All, the sites, the directorates - the teams' 8 deleted" || { echo "FAIL - $n split pages after split_teams false"; fails=$((fails + 1)); }
check "  ... a team page: no switch" "$t5/siteb/site-direct/ops-vdi-nova/latest.html" '!class="split"'
check "  ... a directorate's BLUE page links its teams' own pages" "$t5/siteb/site-direct/latest-blue.html" 'href="ops-vdi-nova/latest.html"'
run "  ... by the names (hierarchy_source names): NOVA, the staff codes, a named directorate" ok \
    "{$NOW, $NOACT, \"report_web_dest\": \"\", \"servicenow_insights_directorates\": [\"CODEX\"], \"servicenow_insights_suffix_directorates\": [\"NOVA\"],
      \"servicenow_insights_team_min\": 5}" 'Sites side by side' 'SITE-C' 'J63' 'NOVA' 'CODEX'
mock SNMOCK_SEED="$work/seed3.json" SNMOCK_NO_GROUPS=1
run "  ... sys_user_group refused (403): said, the sites by the names, green" ok \
    "{$NOW, $NOACT, \"servicenow_insights_hierarchy_source\": \"parents\", \"report_web_dest\": \"\", \"servicenow_insights_team_min\": 5}" \
    "sys_user_group (the groups' parents, for the sites and directorates): GET api/now/table/sys_user_group: HTTP 403" '(unknown)'
mock

# ---- groups, what cannot be read, Check, no credential ---------------------------------------------------------------
run "one group named: its tickets only, its page" ok "{$NOW, $NOACT, \"servicenow_insights_groups\": \"Ops - Linux\"}" \
    'Ticket insights for All assignment groups' 'Ops - Linux' '!Ops - Service Desk  '
run "a group that is not there: said, the job fails" fail "{$NOW, $NOACT, \"servicenow_insights_groups\": [\"Linux Ops\"]}" \
    'none of the assignment groups is in ServiceNow: Linux Ops'
run "  ... servicenow_insights_fail false: a report only" ok "{$NOW, $NOACT, \"servicenow_insights_groups\": [\"Linux Ops\"], \"servicenow_insights_fail\": false}" \
    'none of the assignment groups is in ServiceNow: Linux Ops'
mock SNMOCK_NO_TABLES=sys_audit,problem,sys_user_grmember
run "sys_audit, problem and sys_user_grmember unreadable: said under Not read, the paths from the metric, green" ok "{$NOW, $NOACT}" \
    'the hand-off paths: sys_audit: GET api/now/table/sys_audit: HTTP 403' 'the paths from metric_instance instead' 'problem: GET api/now/table/problem: HTTP 403' 'Top incident issues' \
    "sys_user_grmember (the teams' members): GET api/now/table/sys_user_grmember: HTTP 403" 'each leaderboard lists everyone the team'
mock SNMOCK_NO_TABLES=change_task
mkdir -p "$work/site6"
run "change_task unreadable (403): said under Not read, the rest of the report whole, green" ok "{$NOW, $NOACT, \"report_web_dest\": \"$work/site6\"}" \
    'change_task: GET api/now/table/change_task: HTTP 403' 'the change tasks are left out (their columns, Held up by' 'Top incident issues' \
    'Changes failed or backed out' '!Held up by: the team' '!Change tasks closed'
check "  ... the page without them" "$work/site6/servicenow-ticket-insights/latest.html" '!<h3>Held up by</h3>' '!Change tasks closed' 'id="changes"' \
    '<th class="n">Resolved</th>' 'the change tasks are left out'
mock SNMOCK_NO_TABLES=sys_audit,metric_instance
run "  ... sys_audit and metric_instance unreadable: the counts, no paths, said which" ok "{$NOW, $NOACT}" \
    'sys_audit and metric_instance (the hand-off paths): sys_audit: GET api/now/table/sys_audit: HTTP 403' 'metric_instance: GET api/now/table/metric_instance: HTTP 403'
mock
run "  ... servicenow_insights_handoff_source metric: sys_audit never asked" ok "{$NOW, $NOACT, \"servicenow_insights_handoff_source\": \"metric\"}" \
    '!sys_audit (the hand-off paths)' '!the hand-off paths: sys_audit'
grep -q '/sys_audit' "$work/log.jsonl" && { echo "FAIL - sys_audit asked though handoff_source is metric"; fails=$((fails + 1)); } \
    || { grep -q '/metric_instance' "$work/log.jsonl" && echo "ok   -   ... only metric_instance asked" || { echo "FAIL - metric_instance not asked"; fails=$((fails + 1)); }; }
CHECK=1 run "Check: read and printed, nothing written" ok "{$NOW, $NOACT, \"report_web_dest\": \"$work/site3\"}" 'Ticket insights for All assignment groups'
[ ! -e "$work/site3" ] && echo "ok   -   ... nothing written in Check" || { echo "FAIL - Check wrote $work/site3"; fails=$((fails + 1)); }
# the classification banner (1.3.0): the site's page, a team's page and the index - top and bottom
mkdir -p "$work/site7"
run "classified (site_enclave): the banner on the insights pages" ok "{$NOW, $NOACT, \"site_enclave\": \"classified\", \"report_web_dest\": \"$work/site7\"}" \
    'Banner SECRET (site_enclave: classified)'
for f in servicenow-ticket-insights/latest.html servicenow-ticket-insights/ops/other/windows/latest.html index.html; do
    [ "$(grep -o 'class="site-banner ' "$work/site7/$f" 2>/dev/null | wc -l)" = 2 ] && echo "ok   -   ... $f: top and bottom" \
        || { echo "FAIL - the banner on $f"; fails=$((fails + 1)); }
done
INS_ENV="SN_HOST=" run "no credential" fail "{$NOW}" 'it sets SN_HOST, SN_USERNAME, SN_PASSWORD'
run "months 0: said" fail "{$NOW, $NOACT, \"servicenow_insights_months\": 0}" 'servicenow_insights_months must be 1 or more'
run "the last 60 days instead (servicenow_insights_days)" ok "{$NOW, $NOACT, \"servicenow_insights_days\": 60}" 'Window: 2026-09-02 to 2026-10-31'

# ---- 1.7.0: your SLOs - task_sla refused: your targets in business hours instead, said; the surveys refused -------------
mock SNMOCK_NO_TABLES=task_sla,asmt_metric_result
mkdir -p "$work/site8"
run "task_sla refused: the targets in business hours (a response from the metric Assigned to), said; the surveys refused, said" ok \
    "{$NOW, $NOACT, \"report_web_dest\": \"$work/site8\", \"servicenow_insights_business_hours\": {\"days\": [\"mon\", \"tue\", \"wed\", \"thu\", \"fri\"], \"start\": \"07:30\", \"end\": \"16:30\"},
      \"servicenow_insights_sla_targets\": {\"incident\": {\"1\": {\"response\": 0.25, \"resolution\": 4}, \"2\": {\"response\": 0.5, \"resolution\": 9}, \"3\": {\"response\": 4, \"resolution\": 27}, \"4\": {\"response\": 9, \"resolution\": 45}, \"5\": \"none\"}}}" \
    'task_sla not read' 'the itil role has it' 'asmt_metric_result (the incident surveys)'
check "  ... the page: the targets, business hours, Response met measured, no SLA at risk list" "$work/site8/servicenow-ticket-insights/latest.html" \
    'Response target' 'Resolution target' 'Mon-Fri 07:30-16:30' 'from opened to first assigned to a person' 'no running SLA read (task_sla)' '!id="satisfaction"'
grep -q 'definition.name=Assigned to' "$work/log.jsonl" && echo "ok   -   ... the metric Assigned to read" || { echo "FAIL - Assigned to not read"; fails=$((fails + 1)); }
mock

# ---- emailed with the workbook -----------------------------------------------------------------------------------------
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done
run "the report emailed, the workbook attached" ok "{$NOW, $NOACT, \"report_email_to\": [\"ops@example.mil\"], \"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $smtp, \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\"}" \
    'emailed'
if ls "$work"/mail/*.eml >/dev/null 2>&1 && grep -q "Ticket insights for All assignment groups" "$work"/mail/*.eml && grep -q "SLA at risk now" "$work"/mail/*.eml && grep -q 'ticket-insights_.*\.xlsx\|servicenow-ticket-insights.*\.xlsx\|\.xlsx' "$work"/mail/*.eml; then
    echo "ok   -   ... the mail has the report and the workbook"
else
    echo "FAIL - no mail with the report and the workbook"; fails=$((fails + 1))
fi

# ---- the org map (1.8.0): management's own tree, any depth; the Custom view and its periods (1.10.0) -----------------
mock SNMOCK_SEED="$work/seed3.json"
mkdir -p "$work/site8"
orgmap='{"SITEA - J6 - Service Desk": ["SITEA", "SPLIT", "Operations", "EUCS"], "SITEA - j63 - Windows": "SITEA > SPLIT > Operations > Systems",
         "SITEB - Ops - VDI - nova": ["SITEB"], "SITEB - Network": ["SITEB", "Networks & Comms"]}'
run "the org map: a page and rollup at every level, the groups it does not name in Unknown, the Custom view" ok \
    "{$NOW, $NOACT, \"servicenow_insights_hierarchy_source\": \"map\", \"servicenow_insights_hierarchy_map\": $orgmap, \"report_web_dest\": \"$work/site8\",
      \"servicenow_insights_split_label\": \"SPLIT\", \"servicenow_insights_custom_view_presets\": {\"SITEB only\": {\"levels\": [\"SITEB\"]}},
      \"servicenow_insights_periods\": [\"all\", \"1m\", \"1w\"], \"servicenow_insights_week_start\": \"sun\"}" \
    'Sites side by side' 'SITEA' 'SITEB'
t8="$work/site8/servicenow-ticket-insights"
for f in latest.html sitea/latest.html sitea/split/latest.html sitea/split/operations/latest.html sitea/split/operations/eucs/latest.html \
         sitea/split/operations/eucs/sitea-j6-service-desk/latest.html sitea/split/operations/systems/sitea-j63-windows/latest.html \
         siteb/latest.html siteb/siteb-ops-vdi-nova/latest.html siteb/networks-comms/latest.html unknown/latest.html \
         latest-split.html latest-non-split.html sitea/split/latest-split.html siteb/latest-non-split.html \
         sitea/split/operations/eucs/sitea-j6-service-desk/latest-split.html; do
    [ -s "$t8/$f" ] || { echo "FAIL - no $f"; fails=$((fails + 1)); }
done
check "  ... All: the Custom view and its rows, the tree at any depth, the unmapped group to add" "$t8/latest.html" \
    '<section class="card" id="custom">' '<script type="application/json" id="cv-data">' 'Units and teams' '<li class="lv4">' \
    '<b>The org map</b>' 'SITE-C - CODEX - Linux (' 'Add them to servicenow_insights_hierarchy_map.' '!<b>Sites, directorates and teams</b>' \
    'class="cvp" data-name="SPLIT"' 'class="cvp" data-name="non-SPLIT"' 'class="cvp" data-name="SITEB only"' 'href="latest-split.html">SPLIT</a>'
check "  ... the periods (1.10.0): the ones set, calendar titles, weeks from Sunday, a from / to" "$t8/latest.html" \
    'class="cvpd" data-name="all"' 'class="cvpd" data-name="1m" aria-pressed="false" title="October 2026, compared with September 2026"' \
    'class="cvpd" data-name="1w" aria-pressed="false" title="2026-10-25 to 2026-10-31, compared with 2026-10-18 to 2026-10-24"' \
    '!data-name="2m"' 'id="cvfrom" min="2025-11-01" max="2026-10-31"' '"periods":[{"name":"all"' 'Time in each state' 'Open over 10 business days'
run "a week that starts on no day: said" fail "{$NOW, $NOACT, \"servicenow_insights_hierarchy_source\": \"map\", \"servicenow_insights_hierarchy_map\": $orgmap,
      \"servicenow_insights_week_start\": \"someday\"}" 'servicenow_insights_week_start: someday is not a day'
check "  ... the split by the map's level: SPLIT's page says where the Custom view is" "$t8/latest-split.html" \
    '<b>Custom view:</b> on the <a href="latest.html">All page</a>.' '!id="cv-data"' 'aria-current="page">SPLIT</a>'
check "  ... a unit four levels down: its breadcrumbs, its Custom view link" "$t8/sitea/split/operations/eucs/latest.html" \
    '<a href="../../../latest.html">SITEA</a>' '<b>EUCS</b>' 'href="../../../../latest.html#cv=' 'Custom view of this group'

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all ticket insights scenarios passed"
