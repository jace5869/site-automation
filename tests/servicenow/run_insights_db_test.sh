#!/usr/bin/env bash
# Ticket insights' history (1.13.0, servicenow_insights_db) against a real MySQL 8.4 (the mysql:8.4 image, in podman or docker)
# set up as docs/REPORT_DATABASE.md says: database site_reports, report_writer and report_reader with their grants,
# caching_sha2_password, plain TCP (TLS off in the server too). The fake ServiceNow (snmock.py) with the made-up site.
# No credential: said once, green. The backfill: past calendar months read once (plain TCP, a cold cache: the RSA exchange,
# cryptography), each written, nothing else; twice: replaced. A normal run: the window and its months, the backfill's months
# replaced, the page's Trends. A window ending inside a complete month: that month kept. This month so far: partial, dashed.
# Check: nothing written. Unreachable: one warning, the report whole, green; required (and the backfill): failed. The
# reader sees the views. Without cryptography and a cold cache: said in words. 1.15.0, is it writing?: the job's last line
# (read back after the write, or why not) and the page's How this was made; servicenow_insights_db_check as the writer
# and the reader (read only, no ServiceNow read, no pages), not reached or no credential: failed.
# The container has a fixed name and port and is removed by name. Skipped (exit 0) when no container runtime or image.
#   bash tests/servicenow/run_insights_db_test.sh      (INSIGHTS_DB_TEST_IMAGE=..., INSIGHTS_DB_TEST_RUNTIME=docker, ANSIBLE_PLAYBOOK=...)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
# the Python the playbook's modules run on: ansible-playbook's own (PyMySQL and cryptography installed there)
py="${INSIGHTS_DB_TEST_PYTHON:-$(head -1 "$(command -v "$ap")" | sed -e 's/^#! *//' -e 's/ .*//')}"
[ -x "$py" ] || py="$(command -v python3)"
image="${INSIGHTS_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${INSIGHTS_DB_TEST_PORT:-18562}"
port="${INSIGHTS_DB_TEST_SN_PORT:-18563}"
name="sa-insights-db-test"
rt=${INSIGHTS_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py (pip install PyMySQL cryptography)"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
root="Test-Only-root-$$"
wpw="Test-Only-writer-$$"
rpw="Test-Only-reader-$$"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; "${rtc[@]}" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

"${rtc[@]}" rm -f "$name" >/dev/null 2>&1
"${rtc[@]}" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
    --tls-version= >/dev/null || { echo "ABORT - MySQL did not start"; exit 1; }
"$py" - "$dbport" "$root" "$wpw" "$rpw" <<'EOPY' || { echo "ABORT - MySQL not ready"; "${rtc[@]}" logs "$name" 2>&1 | tail -5; exit 1; }
import sys, time, pymysql
for _ in range(120):
    try:
        c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], ssl_disabled=True, autocommit=True)
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit(1)
cur = c.cursor()
# docs/REPORT_DATABASE.md: the two accounts (caching_sha2_password, MySQL 8.4's default)
for sql in ("CREATE USER 'report_writer'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW ON site_reports.* TO 'report_writer'@'%'",
            "CREATE USER 'report_reader'@'%%' IDENTIFIED BY '%s'" % sys.argv[4],
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'"):
    cur.execute(sql)
EOPY

cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
INV
"$py" "$here/insights_seed.py" "$work/seed.json" 0.4 >/dev/null
env SNMOCK_LOG="$work/snmock.jsonl" SNMOCK_SEED="$work/seed.json" "$py" "$here/snmock.py" "$port" >/dev/null 2>&1 &
pid=$!
for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done

NOW='"servicenow_insights_now": "2026-11-01T06:00:00", "servicenow_insights_until": "2026-10-31", "servicenow_insights_hierarchy_source": "names", "servicenow_insights_act": false'
DB="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$wpw"

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output must contain each TEXT (!TEXT: must not). DB_ENV: the credential.
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && env SN_HOST="http://127.0.0.1:$port" SN_USERNAME=api SN_PASSWORD=goodpw ${DB_ENV:-} \
        ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles:vendor/act/ansible/roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" playbooks/servicenow_insights.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$? t
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal|WARNING' "$work/out.txt" | head -8 | cut -c1-400; return 1
    fi
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            grep -qF -- "${t:1}" "$work/out.txt" && { bad "$name: has: ${t:1}"; return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { bad "$name: lacks: $t"; grep -E '"msg"|WARNING|fatal' "$work/out.txt" | head -6 | cut -c1-400; return 1; }
        fi
    done
    ok "$name"
}
# q USER PASSWORD SQL: the rows as JSON
q() { "$py" - "$dbport" "$1" "$2" "$3" <<'EOPY'
import json, sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user=sys.argv[2], password=sys.argv[3], database="site_reports", ssl_disabled=True)
cur = c.cursor(); cur.execute(sys.argv[4])
print(json.dumps(cur.fetchall(), default=str))
EOPY
}
# page DIR: the All page the run wrote (the shortest latest.html)
page() { find "$1" -name latest.html | awk '{ print length, $0 }' | sort -n | head -1 | cut -d' ' -f2-; }
has() {  # has NAME FILE TEXT... (!TEXT: must not)
    local name="$1" file="$2" t; shift 2
    [ -f "$file" ] || { bad "$name: no page"; return 1; }
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then grep -qF -- "${t:1}" "$file" && { bad "$name: page has: ${t:1}"; return 1; }
        else grep -qF -- "$t" "$file" || { bad "$name: page lacks: $t"; return 1; }; fi
    done
    ok "$name"
}

# last NAME TEXT: the job's "is it writing?" line (1.15.0) has TEXT and comes after the time line - the job's last lines
last() {
    local l
    l=$(grep -o '"msg": "Report database[^"]*"' "$work/out.txt" | tail -1)
    case "$l" in *"$2"*) ;; *) bad "$1: the last line is: $l"; return 1 ;; esac
    if grep -q '"msg": "Time: ' "$work/out.txt" && [ "$(grep -n '"msg": "Time: ' "$work/out.txt" | tail -1 | cut -d: -f1)" -gt "$(grep -n '"msg": "Report database' "$work/out.txt" | tail -1 | cut -d: -f1)" ]; then
        bad "$1: the database line is before the time line"; return 1
    fi
    ok "$1"
}

NOWB='"servicenow_insights_now": "2026-11-01T06:00:00", "servicenow_insights_hierarchy_source": "names", "servicenow_insights_act": false'
mkdir -p "$work/w0" "$work/w1" "$work/w2" "$work/w3" "$work/wb" "$work/wcut"
# rows SQL: one value
one() { q report_reader "$rpw" "$1" | "$py" -c "import json,sys; print(json.load(sys.stdin)[0][0])"; }

# ---- 1. no credential: said once, nothing else ----
DB_ENV="" run "servicenow_insights_db with no Report database credential: said once, green" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w0\"}" \
    'credential (aap/credential_types/report_database.yml): nothing read or written' '!The report database: the window'
has "  ... no trends on the page" "$(page "$work/w0")" '!id="trend"' 'ACT is off: the issues are grouped by category'
last "  ... is it writing? the last line says why not" "Report database: NOT written - no Report database credential on the job template"
has "  ... and the page" "$(page "$work/w0")" 'Report database.</b> Not written this run: no Report database credential on the job template.'
DB_ENV="$DB" run "servicenow_insights_db false: the last line says so, the page says nothing" ok "{$NOW, \"report_web_dest\": \"$work/w0\"}"
last "  ... the line" "Report database: NOT written - servicenow_insights_db is false"
has "  ... the page: nothing" "$(page "$work/w0")" '!Report database.</b>'
DB_ENV="" run "  ... servicenow_insights_db_required: the job fails" fail \
    "{$NOW, \"servicenow_insights_db\": true, \"servicenow_insights_db_required\": true}" 'servicenow_insights_db_required is true and the job template has no'

# ---- 2. the backfill: 6 past calendar months, once - the tables and views made, plain TCP (a cold cache: the RSA exchange) ----
DB_ENV="$DB" run "the backfill: 6 calendar months read once, each worked out and written, nothing else" ok \
    "{$NOWB, \"servicenow_insights_db\": true, \"servicenow_insights_backfill_months\": 6, \"report_web_dest\": \"$work/wb\"}" \
    'The backfill wrote 6 months to 127.0.0.1:' '2026-05: ' ' incidents, ' '2026-10: ' '(item=2026-07)' '!Insights | ACT' '![WARNING]'
[ -z "$(find "$work/wb" -name '*.html')" ] && ok "  ... no pages, no email" || bad "  ... the backfill wrote pages"
grep -o '"msg": "Report database[^"]*"' "$work/out.txt" | tail -1 | grep -qE "Report database 127.0.0.1:$dbport/site_reports: written - run [0-9]+, 6 months \(2026-05, 2026-06, 2026-07, 2026-08, 2026-09, 2026-10\), [0-9,]+ figures; the database now holds 6 runs, first month 2026-05, last month 2026-10" \
    && ok "  ... is it writing? the backfill's last line, read back" || bad "  ... the backfill's last line: $(grep -o '"msg": "Report database[^"]*"' "$work/out.txt" | tail -1)"
v=$(q report_reader "$rpw" "SELECT period, month, partial, window_start, window_end FROM insights_run ORDER BY month")
case "$v" in '[["month", "2026-05", 0, "2026-05-01 00:00:00", "2026-06-01 00:00:00"], ["month", "2026-06", 0,'*'["month", "2026-10", 0, "2026-10-01 00:00:00", "2026-11-01 00:00:00"]]')
    ok "  ... six month rows, May to October, complete - read as report_reader" ;; *) bad "  ... the months: $v" ;; esac
[ "$(one "SELECT version FROM schema_version WHERE component = 'servicenow_insights'")" = 1 ] && ok "  ... schema_version 1" || bad "  ... no schema_version"
v=$(q report_reader "$rpw" "SELECT node, depth, sla_met_pct IS NOT NULL, month FROM insights_v_service WHERE node = 'all' ORDER BY month LIMIT 1")
[ "$v" = '[["all", 0, 1, "2026-05"]]' ] && ok "  ... the views: insights_v_service (node, depth, month, figures)" || bad "  ... the view: $v"
[ "$(one "SELECT COUNT(*) > 0 FROM insights_v_labelled WHERE metric = 'outcome'")" = 1 ] && ok "  ... insights_v_labelled (outcomes, categories, aging, hot CIs)" || bad "  ... insights_v_labelled empty"
v=$(q report_writer "$wpw" "SELECT plugin FROM mysql.user" 2>&1)
case "$v" in *denied*) ok "  ... report_writer reads nothing outside site_reports" ;; *) bad "  ... report_writer read mysql.user: $v" ;; esac
[ "$(one "SELECT COUNT(*) FROM insights_figure WHERE label IN ('Dana Whitfield', 'Marcus Lee') OR node LIKE '%Whitfield%'")" = 0 ] \
    && ok "  ... no person's name stored" || bad "  ... a name stored"
DB_ENV="$DB" run "  ... run twice: the same months replaced, not added" ok \
    "{$NOWB, \"servicenow_insights_db\": true, \"servicenow_insights_backfill_months\": 6}" '2026-08: ' ', replaced'
[ "$(one "SELECT COUNT(*) FROM insights_run")" = 6 ] && ok "  ... still six months" || bad "  ... the runs: $(one "SELECT COUNT(*) FROM insights_run")"

# ---- 3. a normal run: the window and its six months - the months replace the backfill's, the page draws them ----
DB_ENV="$DB" run "a 6-month run: the window and its six calendar months written, the backfill's months replaced" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w1\"}" \
    'The report database: the window and 6 calendar months (2026-05, 2026-06, 2026-07, 2026-08, 2026-09, 2026-10)' 'older runs replaced' \
    'TLS none)' '6 months kept' '![WARNING]'
[ "$(one "SELECT COUNT(*) FROM insights_run")" = 7 ] && ok "  ... seven runs: the window's own and six months" || bad "  ... the runs: $(one "SELECT COUNT(*) FROM insights_run")"
p1="$(page "$work/w1")"
last "  ... is it writing? written, read back: the run, its months, what the database holds" \
    "written - run $(one "SELECT MIN(run_id) FROM insights_run WHERE period = '6m'"), 6 months (2026-05, 2026-06, 2026-07, 2026-08, 2026-09, 2026-10), "
last "  ... the database now holds 7 runs" "the database now holds 7 runs, first month 2026-05, last month 2026-10"
has "  ... How this was made: written, without the host" "$p1" 'Report database.</b> Written this run: run ' \
    'The database holds 7 runs of this instance, months 2026-05 to 2026-10.' "!127.0.0.1:$dbport"
has "  ... the All page: Trends, 6 months so far, two charts, a legend, the months as a table" "$p1" \
    '<a href="#trend">Trends' '6 months so far · each point a calendar month' '<h3>Service levels</h3>' '<h3>Volume</h3>' \
    '<i style="background:var(--st1)"></i>SLA met' '<summary>The months as a table</summary>' 'May 2026' 'Oct 2026' '!stroke-dasharray="5 4"'
team="$(find "$work/w1" -path '*service-desk*' -name latest.html | head -1)"
has "  ... a team's page: its own trends" "$team" '<h3>Service levels</h3>' '6 months so far'
DB_ENV="$DB" run "  ... the same window again: replaced, not added" ok "{$NOW, \"servicenow_insights_db\": true}" 'older runs replaced'
[ "$(one "SELECT COUNT(*) FROM insights_run")" = 7 ] && ok "  ... still seven runs" || bad "  ... the runs after a re-run"

# ---- 4. a window that ends inside October: October so far is not written over the complete October ----
DB_ENV="$DB" run "a window to 2026-10-20: the complete October kept, not replaced by the month so far" ok \
    "{$NOW, \"servicenow_insights_until\": \"2026-10-20\", \"servicenow_insights_months\": 1, \"servicenow_insights_db\": true}" \
    'kept complete: 2026-10'
[ "$(one "SELECT COUNT(*) FROM insights_run WHERE month = '2026-10' AND period = 'month' AND partial = 0")" = 1 ] && ok "  ... October still complete" || bad "  ... October replaced"

# ---- 5. this month so far: written partial, drawn dashed; a later run replaces it ----
NOWN='"servicenow_insights_now": "2026-11-15T06:00:00", "servicenow_insights_hierarchy_source": "names", "servicenow_insights_act": false'
DB_ENV="$DB" run "mid-November: November so far written, marked partial" ok \
    "{$NOWN, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w2\"}" '2026-11 (so far)'
[ "$(one "SELECT partial FROM insights_run WHERE month = '2026-11' AND period = 'month'")" = 1 ] && ok "  ... partial = 1" || bad "  ... November not partial"
has "  ... the page: 7 months so far, the line to November dashed" "$(page "$work/w2")" '7 months so far' 'stroke-dasharray="5 4"' \
    'Nov 2026 (so far)' 'dashed: the month so far'
v=$(q report_reader "$rpw" "SELECT month FROM insights_v_month_run WHERE period = 'month' ORDER BY month")
[ "$v" = '[["2026-05"], ["2026-06"], ["2026-07"], ["2026-08"], ["2026-09"], ["2026-10"], ["2026-11"]]' ] && ok "  ... insights_v_month_run: one row a month" || bad "  ... month runs: $v"

# ---- 6. Check: read, nothing written ----
before=$(one "SELECT COUNT(*) FROM insights_run")
CHECK=1 DB_ENV="$DB" run "Check: read, nothing written" ok "{$NOW, \"servicenow_insights_db\": true}" \
    'Check mode: nothing written to the report database'
last "  ... the last line" "Report database 127.0.0.1:$dbport/site_reports: NOT written - Check mode"
[ "$(one "SELECT COUNT(*) FROM insights_run")" = "$before" ] && ok "  ... the same runs" || bad "  ... Check wrote"

# ---- 7. unreachable: one warning, the report whole, green; required: failed; a wrong password ----
DB_ENV="${DB/REPORT_DB_PORT=$dbport/REPORT_DB_PORT=$((dbport + 9))}" run "the database not reached: one warning, the report whole, green" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w3\", \"servicenow_insights_db_timeout\": 3}" \
    '[WARNING]: The report database 127.0.0.1:' '!The report database: the window'
[ "$(grep -c '\[WARNING\]: The report database' "$work/out.txt")" = 1 ] && ok "  ... said once" || bad "  ... said more than once"
last "  ... the last line: not written, why" "NOT written - not reached or refused: Can't connect to MySQL server"
has "  ... How this was made: not written" "$(page "$work/w3")" 'Report database.</b> Not written this run: not reached or refused: '
has "  ... the page says so" "$(page "$work/w3")" 'The report database was not reached this run:' 'id="glance"'
DB_ENV="${DB/REPORT_DB_PORT=$dbport/REPORT_DB_PORT=$((dbport + 9))}" run "  ... servicenow_insights_db_required: the job fails" fail \
    "{$NOW, \"servicenow_insights_db\": true, \"servicenow_insights_db_required\": true, \"servicenow_insights_db_timeout\": 3}" \
    'servicenow_insights_db_required: true'
DB_ENV="${DB/REPORT_DB_PASSWORD=$wpw/REPORT_DB_PASSWORD=wrong}" run "a wrong password: one warning, green" ok \
    "{$NOW, \"servicenow_insights_db\": true}" 'Access denied for'
last "  ... the last line" "NOT written - not reached or refused: Access denied for user"
DB_ENV="${DB/REPORT_DB_PORT=$dbport/REPORT_DB_PORT=$((dbport + 9))}" run "the backfill, the database not reached: failed" fail \
    "{$NOWB, \"servicenow_insights_db\": true, \"servicenow_insights_backfill_months\": 2, \"servicenow_insights_db_timeout\": 3}" \
    "Can't connect to MySQL server"

# ---- 9. is it writing? the check launch (1.15.0): read only, no ServiceNow read, no pages - the writer and the reader ----
before=$(wc -l < "$work/snmock.jsonl")
runs=$(one "SELECT COUNT(*) FROM insights_run")
DB_ENV="$DB" run "servicenow_insights_db_check: the server, the tables and views, what is held - nothing else" ok \
    "{\"servicenow_insights_db_check\": true, \"report_web_dest\": \"$work/wc\"}" \
    "Report database 127.0.0.1:$dbport/site_reports: reached - MySQL 8.4" 'as report_writer@% (may write)' \
    'Tables and views: all 15 there, schema version 1' ', period month - 7 runs, 7 months (6 complete), 2026-05 to 2026-11, newest written 20' \
    ', period 6m - ' '!Insights | read the window' '!Insights | the settings the report uses'
[ "$(wc -l < "$work/snmock.jsonl")" = "$before" ] && ok "  ... ServiceNow not asked" || bad "  ... ServiceNow was read"
[ -z "$(find "$work/wc" -name '*.html' 2>/dev/null)" ] && ok "  ... no pages" || bad "  ... pages written"
[ "$(one "SELECT COUNT(*) FROM insights_run")" = "$runs" ] && ok "  ... nothing written" || bad "  ... the check wrote"
RDB="${DB/REPORT_DB_USER=report_writer/REPORT_DB_USER=report_reader}"
RDB="${RDB/REPORT_DB_PASSWORD=$wpw/REPORT_DB_PASSWORD=$rpw}"
DB_ENV="$RDB" run "  ... as report_reader: the same, reads only" ok "{\"servicenow_insights_db_check\": true}" 'as report_reader@% (reads only)' \
    'Tables and views: all 15 there' ', period month - 7 runs'
DB_ENV="${DB/REPORT_DB_PORT=$dbport/REPORT_DB_PORT=$((dbport + 9))}" run "  ... not reached: the job fails" fail \
    "{\"servicenow_insights_db_check\": true, \"servicenow_insights_db_timeout\": 3}" "Can't connect to MySQL server"
DB_ENV="" run "  ... no credential: the job fails" fail "{\"servicenow_insights_db_check\": true}" 'servicenow_insights_db_check and the job template has no'

# ---- 8. no cryptography, a cold cache: said in words (the module alone) ----
"${rtc[@]}" exec "$name" mysql -uroot "-p$root" -e "FLUSH PRIVILEGES" >/dev/null 2>&1
out=$("$py" -c "import sys, runpy; sys.modules['cryptography'] = None; sys.argv = ['m', sys.argv[1]]
runpy.run_path('$repo/roles/servicenow_insights/library/site_insights_db.py', run_name='__main__')" \
    "{\"ANSIBLE_MODULE_ARGS\": {\"mode\": \"read\", \"host\": \"127.0.0.1\", \"port\": $dbport, \"database\": \"site_reports\", \"user\": \"report_writer\", \"password\": \"$wpw\", \"month\": \"2026-10\"}}" 2>&1)
case "$out" in *"the login needs the cryptography library"*) ok "without cryptography, a cold cache: the login says it needs cryptography" ;;
    *) bad "without cryptography: $out" ;; esac

# ---- 11. records cut (1.16.0): servicenow_insights_max_records cuts the window itself - nothing of the run written (a ticket
# opened before the cut is missing from every later month too: the tester's August), the rows already there untouched ----
since=$(one "SELECT UTC_TIMESTAMP()")      # a month replaced keeps its run_id (UPDATE): what this run wrote, by written_at
tk0=$(q report_reader "$rpw" "SELECT COUNT(*), COUNT(DISTINCT updated_on), MAX(written_at) FROM insights_ticket")
held=$(q report_reader "$rpw" "SELECT r.month, f.metric, f.value FROM insights_figure f JOIN insights_run r ON r.run_id = f.run_id WHERE r.period = 'month' AND f.node = 'all' AND f.variant = '' ORDER BY r.month, f.metric, f.label")
DB_ENV="$DB" run "max_records 250: the window cut - nothing of the run written, said" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"servicenow_insights_max_records\": 250, \"report_web_dest\": \"$work/wcut\"}" \
    'WARNING - records cut, some figures are incomplete:'
last "  ... the last line: not written, the records cut" "NOT written - the records were cut (servicenow_insights_max_records): an incomplete run is never written"
new=$(q report_reader "$rpw" "SELECT period, month FROM insights_run WHERE written_at >= '$since' ORDER BY month")
[ "$new" = "[]" ] && ok "  ... no run, no month written" || bad "  ... written after a window cut: $new"
[ "$(q report_reader "$rpw" "SELECT r.month, f.metric, f.value FROM insights_figure f JOIN insights_run r ON r.run_id = f.run_id WHERE r.period = 'month' AND f.node = 'all' AND f.variant = '' ORDER BY r.month, f.metric, f.label")" = "$held" ] \
    && ok "  ... the complete months already there untouched, figure for figure" || bad "  ... the months already there changed"
has "  ... the page says it" "$(page "$work/wcut")" 'Not written this run: the records were cut (servicenow_insights_max_records). An incomplete run is never written.'
# 1.17.0: nor the ticket store - not one ticket row written by a cut run
[ "$(q report_reader "$rpw" "SELECT COUNT(*), COUNT(DISTINCT updated_on), MAX(written_at) FROM insights_ticket")" = "$tk0" ] \
    && grep -qF 'nothing written: the records were cut (servicenow_insights_max_records)' "$work/out.txt" \
    && ok "  ... the ticket store untouched, said" || bad "  ... the ticket store written by a cut run: $tk0"

# ---- 12. the survey figures (1.16.0): survey_avg with its scale; the doc's query for rows written before runs as written ----
sq=$("$py" -c "import re,sys; d=open(sys.argv[1]).read(); print(re.search(r'To see those months:\n\n\x60\x60\x60sql\n(.*?)\n\x60\x60\x60', d, re.S).group(1))" "$repo/docs/REPORT_DATABASE.md")
old=$(q report_reader "$rpw" "$sq")
fx=$(one "SELECT COUNT(*) FROM insights_figure WHERE metric = 'survey_scale_max' AND node = 'all'")
[ "$old" = "[]" ] && [ "$fx" -gt 0 ] && ok "the survey figures: survey_scale_max beside survey_avg; the doc's query for older rows runs (none here)" \
    || bad "the survey figures: older rows $old, survey_scale_max $fx"

# ---- 13. the ticket store (1.17.0): a run that reads only what changed gives the pages and the workbook a full read
# gives, file for file and cell for cell - new, changed, reopened, deleted, moved to another group, closed then updated, a
# closed ticket's text edited without sys_updated_on moving, and a database restored from an older backup (stale rows) ----
smtp="${INSIGHTS_DB_TEST_SMTP_PORT:-18564}"
mkdir -p "$work/mail" "$work/w13i" "$work/w13f" "$work/w13s"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" --no-starttls 2>"$work/relay.err" &
spid=$!
trap 'kill $spid 2>/dev/null; cleanup' EXIT
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done
MAIL="\"report_email_to\": [\"ops@example.mil\"], \"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $smtp, \"report_email_from\": \"aap@example.mil\", \"report_email_security\": \"none\""
"$py" - "$work/seed.json" "$work/seed2.json" <<'EOPY'
import copy, json, sys
recs = json.load(open(sys.argv[1]))
inc = [x for x in recs if (x.get("_table") or "incident") == "incident" and str(x.get("opened_at", "")) >= "2026-06"]
bump = "2026-10-31 23:00:00"
closed = [x for x in inc if x.get("state") == "7"]
inc[0].update(short_description="Changed text now", priority="1", sys_updated_on=bump)                         # changed
closed[0].update(state="2", active="true", closed_at="", resolved_at="", reopen_count="1", sys_updated_on=bump)   # reopened
inc[5].update(assignment_group=[x for x in inc if x.get("assignment_group") != inc[5].get("assignment_group")][0]["assignment_group"],
              sys_updated_on=bump)                                                                               # moved to another group
recs.remove(inc[7])                                                                                              # deleted
closed[1].update(close_notes="Replaced the token, then confirmed with the user.", sys_updated_on=bump)            # closed, then updated
closed[2]["short_description"] = "Quietly edited"                                                                # text edited, sys_updated_on not moved
recs.append(dict(copy.deepcopy(inc[2]), sys_id="s-INC9999999", number="INC9999999", opened_at="2026-10-30 10:00:00", sys_updated_on=bump))  # new
json.dump(recs, open(sys.argv[2], "w"))
open(sys.argv[2] + ".stale", "w").write(closed[3]["sys_id"])
EOPY
kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
env SNMOCK_LOG="$work/snmock.jsonl" SNMOCK_SEED="$work/seed2.json" "$py" "$here/snmock.py" "$port" >/dev/null 2>&1 &
pid=$!
for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
# same(A B NAME): every page (latest.html of each scope, by path) and the workbook's sheets alike, but for what each run
# says of itself (the time, its own page's file name, how it read, the report database's line)
same() { "$py" - "$work/$1" "$work/$2" <<'EOPY'
import email, email.policy, glob, io, os, re, sys, zipfile
a, b = sys.argv[1:3]
def norm(t):
    t = re.sub(r'<details class="notes"><summary><b>Time this run</b>.*?</details>', "", t, flags=re.S)
    t = re.sub(r'<p class="dbline">.*?</p>|<div class="callout"><b>Report database\.</b>.*?</div>', "", t, flags=re.S)
    t = re.sub(r"\d{4}-\d{2}-\d{2}_\d{4}_job-[\w-]+", "PAGE", t)
    return re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}( [A-Z]{2,5})?", "WHEN", t)
def pages(d):
    return dict((os.path.relpath(f, d), norm(open(f).read())) for f in glob.glob(d + "/**/latest.html", recursive=True))
pa, pb = pages(a), pages(b)
bad = sorted(set(pa) ^ set(pb)) + [k for k in sorted(set(pa) & set(pb)) if pa[k] != pb[k]]
for k in bad[:3]:
    x, y = pa.get(k, ""), pb.get(k, "")
    i = next((i for i in range(min(len(x), len(y))) if x[i] != y[i]), min(len(x), len(y)))
    print("page differs: %s at %d: %r / %r" % (k, i, x[i - 80:i + 120], y[i - 80:i + 120]))
def book(d):                     # the run's email (moved to its own folder, mail-NAME)
    for f in sorted(glob.glob(d.replace("/w13", "/mail-w13") + "/*.eml")):
        m = email.message_from_binary_file(open(f, "rb"), policy=email.policy.default)
        for part in m.iter_attachments():
            if part.get_filename().endswith(".xlsx"):
                z = zipfile.ZipFile(io.BytesIO(part.get_content()))
                return dict((n, z.read(n)) for n in z.namelist() if n.startswith("xl/worksheets/") or n == "xl/sharedStrings.xml")
    return None
ba, bb = book(a), book(b)
if ba is None or bb is None:
    bad.append("no workbook"); print("no workbook for %s or %s" % (a, b))
elif ba != bb:
    bad.append("workbook"); print("workbook differs:", [n for n in sorted(set(ba) | set(bb)) if ba.get(n) != bb.get(n)])
print("pages %d, sheets %d: %s" % (len(pa), len(ba or {}), "same" if not bad else "DIFFERENT"))
sys.exit(1 if bad else 0)
EOPY
}
# mailed DIR: the run's email moved to its own folder
mailed() { mkdir -p "$work/mail-$1"; mv "$work"/mail/*.eml "$work/mail-$1/" 2>/dev/null; }
held=$(one "SELECT COUNT(*) FROM insights_ticket")
DB_ENV="$DB" run "the ticket store: changed in ServiceNow, read again - only what changed read in full" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w13i\", $MAIL}" \
    'Tickets: ' 'read from ServiceNow (new or changed)' 'from the report database'
mailed w13i
echo "info -   ... incremental: $(grep -o '"msg": "Time: ServiceNow reads [^,]*' "$work/out.txt" | tail -1 | cut -d'"' -f4)"
fromdb=$(grep -oE '[0-9][0-9,]* from the report database' "$work/out.txt" | head -1 | tr -d ', ' | sed 's/from.*//')
[ "${held:-0}" -gt 0 ] && [ "${fromdb:-0}" -gt 1000 ] && ok "  ... $fromdb tickets from the database ($held held)" \
    || bad "  ... too few from the database ($held held): $(grep -o '"msg": "Tickets[^"]*"' "$work/out.txt")"
grep -c 'sysparm_query.*sys_idIN' "$work/snmock.jsonl" >/dev/null && ok "  ... the changed ones read by sys_id" || bad "  ... no read by sys_id"
DB_ENV="$DB" run "  ... the same data read in full (servicenow_insights_full_refresh)" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"servicenow_insights_full_refresh\": true, \"report_web_dest\": \"$work/w13f\", $MAIL}" \
    'all read from ServiceNow - a full read (servicenow_insights_full_refresh)'
mailed w13f
echo "info -   ... full read: $(grep -o '"msg": "Time: ServiceNow reads [^,]*' "$work/out.txt" | tail -1 | cut -d'"' -f4); $(grep -o 'written to the report database in [0-9]* s' "$work/out.txt" | tail -1)"
out13=$(same w13i w13f) && ok "  ... the pages and the workbook the same as the full read's ($out13)" || bad "  ... incremental and full differ: $out13"
# a database restored from an older backup: rows older than ServiceNow's, one with a field ServiceNow no longer has
stale=$(cat "$work/seed2.json.stale")
"${rtc[@]}" exec "$name" mysql -uroot "-p$root" site_reports -e "UPDATE insights_ticket SET updated_on = '2020-01-01 00:00:00', fields = JSON_SET(fields, '\$.priority', 'STALE') WHERE sys_id = '$stale' OR sys_id IN (SELECT sys_id FROM (SELECT sys_id FROM insights_ticket ORDER BY sys_id LIMIT 40) x)" >/dev/null 2>&1
DB_ENV="$DB" run "  ... a database restored from an older backup: its stale rows read again" ok \
    "{$NOW, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w13s\", $MAIL}" 'from the report database'
mailed w13s
out13=$(same w13s w13f) && ok "  ... the same as the full read's ($out13)" || bad "  ... stale rows used: $out13"
[ "$(one "SELECT COUNT(*) FROM insights_ticket WHERE updated_on < '2021-01-01' OR JSON_EXTRACT(fields, '\$.priority') = 'STALE'")" = "0" ] \
    && ok "  ... and written again" || bad "  ... stale rows left"
# never a person, a text or a CI in the store
leak=$(q report_reader "$rpw" "SELECT COUNT(*) FROM insights_ticket WHERE JSON_CONTAINS_PATH(fields, 'one', '\$.short_description', '\$.close_notes', '\$.assigned_to', '\$.opened_by', '\$.closed_by', '\$.resolved_by', '\$.caller', '\$.caller_id', '\$.cmdb_ci')")
[ "$leak" = "[[0]]" ] && ok "  ... no text, person or CI in insights_ticket" || bad "  ... insights_ticket holds text or people: $leak"

# ---- 14. the ACT cache (1.17.0): each description's issue kept - the next run asks ACT only what is new; the issue names
# masked before they are stored (a name ACT should never give, with a person, an IP address and a host); cache_clear ----
mport="${INSIGHTS_DB_TEST_MODEL_PORT:-18565}"
: > "$work/model.log"
env FAKE_MODEL_LOG="$work/model.log" FAKE_MODEL_LEAKY_LABEL=1 "$py" "$here/fake_insights_model.py" "$mport" &
mpid=$!
trap 'kill $spid $mpid 2>/dev/null; cleanup' EXIT
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$mport") 2>/dev/null && break; sleep 0.1; done
NOWA="${NOW/\"servicenow_insights_act\": false/\"servicenow_insights_act\": true}, \"site_act_url\": \"http://127.0.0.1:$mport/v1/chat/completions\", \"site_act_env\": {\"ACT_STREAM\": \"0\"}, \"servicenow_insights_act_batch\": 15"
asked() { grep -c 'Group IT service desk incidents' "$work/model.log"; }
mkdir -p "$work/w14a" "$work/w14b" "$work/w14c"
DB_ENV="$DB GENAI_KEY=test-key" run "the ACT cache: the first run asks ACT every description" ok \
    "{$NOWA, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w14a\"}" 'questions answered'
a1=$(asked); : > "$work/model.log"
nc=$(one "SELECT COUNT(*) FROM insights_act_class"); ni=$(one "SELECT COUNT(*) FROM insights_act_issue")
[ "$a1" -gt 0 ] && [ "$nc" -gt 0 ] && [ "$ni" -gt 0 ] && ok "  ... $a1 grouping questions; $nc descriptions and $ni issues kept" || bad "  ... asked $a1, kept $nc / $ni"
labels=$(q report_reader "$rpw" "SELECT label FROM insights_act_issue ORDER BY issue_no")
case "$labels" in *Whitfield*|*198.51.100*|*wks-1001*) bad "  ... an issue name stored as ACT gave it: $labels" ;;
    *"VPN drops for [person] at [ip] on [host]"*) ok "  ... the issue names masked before they are stored (a person, an IP address, a host)" ;;
    *) bad "  ... the masked name is not there: $labels" ;; esac
DB_ENV="$DB GENAI_KEY=test-key" run "  ... the next run: no description asked again, the same issues" ok \
    "{$NOWA, \"servicenow_insights_db\": true, \"report_web_dest\": \"$work/w14b\"}" 'questions answered'
a2=$(asked); : > "$work/model.log"
[ "$a2" = 0 ] && ok "  ... no grouping question (the fixes still asked)" || bad "  ... $a2 grouping questions asked again"
has "  ... said under How this was made" "$(page "$work/w14b")" 'descriptions placed from the ACT cache, not asked again' 'Account locked out / password reset'
"$py" - "$(page "$work/w14a")" "$(page "$work/w14b")" <<'EOPY' && ok "  ... the issues table the same as the first run's" || bad "  ... the issues differ"
import re, sys
def issues(f):
    h = open(f).read()
    sec = h[h.index('id="issues"'):]
    sec = sec[:sec.index("</section>")]
    return re.findall(r'<span class="t">(.*?)</span>.*?<span class="big">([\d,]+)</span>.*?(<dt>About</dt><dd>.*?</dd>|</dl>)', sec, re.S)
a, b = issues(sys.argv[1]), issues(sys.argv[2])
assert a and a == b, (a[:5], b[:5])
EOPY
DB_ENV="$DB GENAI_KEY=test-key" run "  ... servicenow_insights_act_cache_clear: everything asked again" ok \
    "{$NOWA, \"servicenow_insights_db\": true, \"servicenow_insights_act_cache_clear\": true, \"report_web_dest\": \"$work/w14c\"}" 'questions answered'
a3=$(asked)
[ "$a3" = "$a1" ] && ok "  ... $a3 grouping questions, as the first run" || bad "  ... $a3 asked (the first run: $a1)"

# ---- 10. PyMySQL 1.2.3 (1.16.0): its first login after FLUSH PRIVILEGES raises AttributeError; the module tries once more ----
if "$py" -m pip install -q --disable-pip-version-check --target "$work/pm123" "PyMySQL==1.2.3" >/dev/null 2>&1; then
    flush() { "${rtc[@]}" exec "$name" mysql -uroot "-p$root" -e "FLUSH PRIVILEGES" >/dev/null 2>&1; }
    flush
    out=$(PYTHONPATH="$work/pm123" "$py" - "$dbport" "$wpw" <<'EOPY' 2>&1
import sys, pymysql
assert pymysql.VERSION_STRING == "1.2.3", pymysql.VERSION_STRING
try:
    pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="report_writer", password=sys.argv[2], database="site_reports", ssl_disabled=True)
    print("no bug")
except AttributeError as e:
    print("the bug: %s" % e)
EOPY
)
    case "$out" in *"the bug: "*"is_auth_switch_request"*) ok "PyMySQL 1.2.3, a cold cache: the first login raises the bug (reproduced)" ;;
        *) bad "PyMySQL 1.2.3 did not show the bug: $out" ;; esac
    flush
    out=$(PYTHONPATH="$work/pm123" "$py" -c "import sys, runpy; sys.argv = ['m', sys.argv[1]]
runpy.run_path('$repo/roles/servicenow_insights/library/site_insights_db.py', run_name='__main__')" \
        "{\"ANSIBLE_MODULE_ARGS\": {\"mode\": \"read\", \"host\": \"127.0.0.1\", \"port\": $dbport, \"database\": \"site_reports\", \"user\": \"report_writer\", \"password\": \"$wpw\", \"month\": \"2026-10\"}}" 2>&1)
    case "$out" in *'"ok": true'*) ok "  ... the module with PyMySQL 1.2.3 after FLUSH PRIVILEGES: logged in at the second try, read" ;;
        *) bad "  ... the module with PyMySQL 1.2.3: $(echo "$out" | tail -c 400)" ;; esac
else
    echo "skip - PyMySQL 1.2.3 could not be installed (no PyPI): the login bug's retry is covered by the unit test only"
fi

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all report database scenarios passed"
