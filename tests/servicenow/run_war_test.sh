#!/usr/bin/env bash
# The weekly activity report (roles/servicenow_war, playbooks/servicenow_war.yml) against the fake ServiceNow (snmock.py) with a
# week of records (war_seed.json) and a fixed clock (Friday 2026-10-23 20:00 UTC): the last 7 days - what was completed (with
# the date of closure), opened, open at the end (the old ones amber, worked on or not), by person, the lines to paste, a
# ticket of another group and one opened after the week left out, one resolved before the week and auto-closed in it left
# out; the previous calendar week; two dates in a time zone; every class in one read (task); a table the account cannot
# read (said, not failed); a group that is not there (failed, or not with servicenow_war_fail false); no group set; the
# tasks of one person; the group table unreadable (matched by name); too many records (said); a wrong password; no
# credential; Check; emailed with the workbook.
#   bash tests/servicenow/run_war_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
port="${WAR_TEST_PORT:-18290}"
smtp="${SMTP_TEST_PORT:-18029}"
pid=""
spid=""
fails=0
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; [ -n "$spid" ] && kill "$spid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
INV
NOW='"servicenow_war_now": "2026-10-23T20:00:00"'
GROUP='"servicenow_war_groups": ["ABC - Ops - IT - Linux"]'

# mock [VAR=value ...]: (re)start the fake ServiceNow with the week's records and an empty log
mock() {
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    : > "$work/log.jsonl"
    env SNMOCK_LOG="$work/log.jsonl" SNMOCK_SEED="$here/war_seed.json" "$@" "$py" "$here/snmock.py" "$port" &
    pid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
}

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output and the mail must contain each TEXT; a TEXT starting
# with ! must NOT be there. WAR_ENV: more environment (SN_PASSWORD=bad). CHECK=1: --check.
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && env SN_HOST="http://127.0.0.1:$port" SN_USERNAME=api SN_PASSWORD=goodpw \
        ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 ${WAR_ENV:-} \
        "$ap" -i "$work/inv.yml" playbooks/servicenow_war.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            if grep -qF -- "${t:1}" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null; then
                echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1
            fi
        elif ! grep -qF -- "$t" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null; then
            echo "FAIL - $name: lacks: $t"; grep -E '"msg"|fatal' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
        fi
    done
    echo "ok   - $name"
}

mock
# ---- the week ---------------------------------------------------------------------------------------
run "the last 7 days: completed with the date of closure, opened, open at the end, the old ones, by person, the lines to paste" ok "{$NOW, $GROUP}" \
    'Weekly activity report for ABC - Ops - IT - Linux: 5 completed, 6 opened, 6 open at the end (2026-10-16 20:00 to 2026-10-23 20:00 (UTC))' \
    'Assignment group: ABC - Ops - IT - Linux' '!Catalog task (SCTASK)' \
    'SLA breached or close to breaching' 'P2 resolution (2 days)' '160.2%' 'P4 resolution (30 days)' 'close to breaching' \
    'breached, completed' 'Incident INC0020002: SSH login slow on the jump hosts - In Progress, new this week (Jane Doe)' '!(Jane Doe) - SLA breached' \
    '"sla_breached": 2' '"sla_at_risk": 1' '!P2 response (1 hour)' \
    'Completed this week (5)' '2026-10-20 15:30  INC0020001' 'Restarted postfix; the queue drained in 10 minutes.' \
    '2026-10-22 07:45  INC0020004' '2026-10-18 12:00  SCTASK0020001' '2026-10-21 18:00  CHG0020001' '2026-10-21 17:50  CTASK0020001' \
    'Opened this week (6)' 'Open at the end of the week (6)' 'Older than 30 days: 2' 'Worked on, still open: 2' \
    'Incident INC0020001: Mail relay queue growing on mail01 - resolved 2026-10-20 (Jane Doe)' \
    'Change CHG0020001: Kernel patching, web tier (October) - closed 2026-10-21 (Jane Doe)' \
    'Problem task PTASK0020001: Find why the NFS mounts hang on the batch hosts - Work in Progress, open since 2026-09-01, 52 days (Jane Doe)' \
    'Catalog task SCTASK0020002: Add 200 GB to the share for the GIS team - Open, new this week (unassigned)' \
    'Jane Doe      3' 'Bob Smith     2' '!INC0020006' '!INC0020007' '!INC0020005' \
    '"opened": 6' '"done": 5' '"open_at_end": 6'
run "the previous calendar week (Monday to Sunday)" ok "{$NOW, $GROUP, \"servicenow_war_period\": \"last-week\"}" \
    'ABC - Ops - IT - Linux: 1 completed, 3 opened, 5 open at the end (2026-10-12 to 2026-10-18 (UTC))' 'SCTASK0020001' '!INC0020005'
run "the week that starts on Friday" ok "{$NOW, $GROUP, \"servicenow_war_period\": \"last-week\", \"servicenow_war_week_start\": \"friday\"}" \
    '(2026-10-16 to 2026-10-22 (UTC))'
run "two dates in a time zone: through the end of the last day, the dates in that zone" ok \
    "{$NOW, $GROUP, \"servicenow_war_since\": \"2026-10-19\", \"servicenow_war_until\": \"2026-10-23\", \"report_time_zone\": \"America/New_York\"}" \
    '(2026-10-19 to 2026-10-23 (America/New_York))' 'INC0020007' '2026-10-20 11:30  INC0020001' 'Dates in America/New_York'
run "every class in one read: the task table (the incidents' resolution from their own table)" ok "{$NOW, $GROUP, \"servicenow_war_tables\": [\"task\"]}" \
    '5 completed, 6 opened, 6 open at the end' 'task: 11 record(s) kept of 13 read' 'Problem task' 'Change task' '2026-10-20 15:30  INC0020001' '!INC0020005'
run "the tasks of one person, whatever the group" ok "{$NOW, \"servicenow_war_users\": [\"bsmith\"]}" \
    'Weekly activity report for bsmith: 2 completed, 3 opened, 2 open at the end' 'Assigned to: bsmith' 'INC0020003' 'SCTASK0020001' '!INC0020001'
run "too many records: said" ok "{$NOW, $GROUP, \"servicenow_war_max_records\": 3}" \
    'incident: more than the first 3 records - raise servicenow_war_max_records, or narrow the week' 'Not read'
CHECK=1 run "Check: read and printed, nothing sent" ok "{$NOW, $GROUP}" '5 completed, 6 opened, 6 open at the end'

# ---- what goes wrong -----------------------------------------------------------------------------------
mock SNMOCK_NO_TABLES=problem_task
run "a table the account cannot read: said, the rest reported, the job green" ok "{$NOW, $GROUP}" \
    '5 completed, 6 opened, 5 open at the end' 'problem_task: GET api/now/table/problem_task: HTTP 403: Operation Failed: ACL Exception: no read access to problem_task' \
    'Not read: 1'
mock SNMOCK_NO_GROUPS=1
run "the group table unreadable: the groups matched by name instead" ok "{$NOW, $GROUP}" \
    '5 completed, 6 opened, 6 open at the end' 'matched by name instead'
mock
run "a group that is not there: said, the job fails" fail "{$NOW, \"servicenow_war_groups\": [\"Linux Ops\"]}" \
    'none of the assignment groups is in ServiceNow: Linux Ops'
run "two groups, one not there: reported for the other, the job fails" fail "{$NOW, \"servicenow_war_groups\": [\"ABC - Ops - IT - Linux\", \"Linux Ops\"]}" \
    '5 completed, 6 opened, 6 open at the end' 'assignment group Linux Ops is not in ServiceNow' 'assignment group(s) not in ServiceNow: Linux Ops'
run "  ... servicenow_war_fail false: a report only" ok "{$NOW, \"servicenow_war_groups\": [\"ABC - Ops - IT - Linux\", \"Linux Ops\"], \"servicenow_war_fail\": false}" \
    'assignment group Linux Ops is not in ServiceNow'
run "no group set" fail "{$NOW}" 'Set servicenow_war_groups to the assignment group(s)'
run "a window that ends before it starts" fail "{$NOW, $GROUP, \"servicenow_war_since\": \"2026-10-23\", \"servicenow_war_until\": \"2026-10-20\"}" \
    'the window ends (2026-10-21 00:00) before it starts (2026-10-23 00:00)'
mock SNMOCK_NO_TABLES=task_sla
run "the SLAs unreadable: said under Not read, the rest reported, the job green" ok "{$NOW, $GROUP}" \
    'task_sla (the SLAs): GET api/now/table/task_sla: HTTP 403' '5 completed, 6 opened, 6 open at the end' '!SLA breached or close to breaching'
run "servicenow_war_sla false: no SLA read, nothing said" ok "{$NOW, $GROUP, \"servicenow_war_sla\": false}" \
    '!task_sla' '!SLA breached'
mock
# ---- one template, several teams ----------------------------------------------------------------------
mkdir -p "$work/site" "$work/site2"
run "a team as plain text (a survey's or a schedule's answer): one group, not cut at its spaces and dashes" ok \
    "{$NOW, \"servicenow_war_groups\": \"ABC - Ops - IT - Linux\", \"report_web_dest\": \"$work/site\", \"servicenow_war_web\": true}" \
    'Weekly activity report for ABC - Ops - IT - Linux: 5 completed' '!assignment group ABC is not'
run "  ... another team on the same template, two groups on separate lines" ok \
    "{$NOW, \"servicenow_war_groups\": \"Windows Operations\\nLinux Operations\", \"report_web_dest\": \"$work/site\", \"servicenow_war_web\": true}" \
    'Weekly activity report for Windows Operations, Linux Operations: 0 completed, 1 opened'
latest=$(cd "$work/site" && find . -name latest.html | sort | tr '\n' ' ')
if [ "$latest" = "./servicenow-weekly-activity-report/abc-ops-it-linux/latest.html ./servicenow-weekly-activity-report/windows-operations-linux-operations/latest.html " ] \
   && [ "$(grep -o 'abc-ops-it-linux/latest.html\|windows-operations-linux-operations/latest.html' "$work/site/index.json" | sort -u | wc -l)" = 2 ]; then
    echo "ok   -   ... each team its own folder, latest page and line on the website index"
else
    echo "FAIL - the teams' website folders: $latest"; fails=$((fails + 1))
fi
run "  ... servicenow_war_web_per_team false: one folder for every team" ok \
    "{$NOW, \"servicenow_war_groups\": \"ABC - Ops - IT - Linux\", \"report_web_dest\": \"$work/site2\", \"servicenow_war_web\": true, \"servicenow_war_web_per_team\": false}" \
    '5 completed'
[ -f "$work/site2/servicenow-weekly-activity-report/latest.html" ] && echo "ok   -   ... servicenow-weekly-activity-report/latest.html" \
    || { echo "FAIL - no shared latest page"; fails=$((fails + 1)); }

mkdir -p "$work/site3"
run "report_web_dest set, servicenow_war_web not: not on the report website" ok \
    "{$NOW, $GROUP, \"report_web_dest\": \"$work/site3\"}" '5 completed'
[ -z "$(ls -A "$work/site3")" ] && echo "ok   -   ... nothing written to the website folder" \
    || { echo "FAIL - the website folder has: $(ls -A "$work/site3")"; fails=$((fails + 1)); }

WAR_ENV="SN_PASSWORD=bad" run "a wrong password" fail "{$NOW, $GROUP}" 'HTTP 401: User Not Authenticated'
WAR_ENV="SN_HOST=" run "no credential" fail "{$NOW, $GROUP}" 'it sets SN_HOST, SN_USERNAME, SN_PASSWORD'

# ---- emailed with the workbook ------------------------------------------------------------------------
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done
run "the report emailed, the workbook attached" ok "{$NOW, $GROUP, \"report_email_to\": [\"ops@example.mil\"], \"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $smtp, \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\"}" \
    '5 completed, 6 opened, 6 open at the end' 'emailed'
if ls "$work"/mail/*.eml >/dev/null 2>&1 && grep -q "Weekly activity report" "$work"/mail/*.eml && grep -q 'weekly-activity-report_.*\.xlsx' "$work"/mail/*.eml; then
    echo "ok   -   ... the mail has the report and the workbook"
else
    echo "FAIL - no mail with the report and the workbook"; fails=$((fails + 1))
fi

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all weekly activity report scenarios passed"
