#!/usr/bin/env bash
# The STIG compliance scorecard's history in the report database (1.58.0, site_stig_scorecard_db) against a real MySQL 8.4
# (the mysql:8.4 image, in podman or docker) set up as docs/REPORT_DATABASE.md says: database site_reports, report_writer
# and report_reader with their grants, plain TCP. The stand-in STIG Manager and the fake AAP of run_scorecard_test.sh
# (tests/stig/scorecard_fixture.py). The first run: the table made, this run's rows written, the reader sees them. 8
# earlier weeks in the table: the week's change and the trend with no AAP. AAP and the database together: a job the
# database gave is not read from AAP again, a run both hold counts once. Rows older than stigman_scorecard_db_keep_days
# deleted (0: kept). A dry run (Check): read, nothing written. Never a failed job: the server not reached, a wrong
# password (not printed), a read-only account (the earlier points still used), a table of a newer release, no table with
# stigman_scorecard_db_create: false - one warning each, the scorecard whole, green. stigman_scorecard_db: false: not
# touched. STIG Manager gets GET requests only throughout.
# The container's name and the ports come from variables; the container is removed by its name. Skipped (exit 0) when
# there is no container runtime, image or PyMySQL.
#   bash tests/stig/run_scorecard_db_test.sh     (ANSIBLE_PLAYBOOK=..., SCORECARD_DB_TEST_IMAGE=..., SCORECARD_DB_TEST_RUNTIME=docker)
#   SCORECARD_DB_TEST_NAME=..., SCORECARD_DB_TEST_PORT=18640 (the database; the stand-ins on the 2 ports after it),
#   SCORECARD_DB_TEST_PYTHON=...: the Python the modules run on (PyMySQL and cryptography installed there)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SCORECARD_DB_TEST_PYTHON:-$(command -v python3)}"
image="${SCORECARD_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${SCORECARD_DB_TEST_PORT:-18640}"
port=$((dbport + 1))
name="${SCORECARD_DB_TEST_NAME:-sa-scorecard-db-test}"
rt=${SCORECARD_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py (pip install PyMySQL cryptography)"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
root="Test-Only-root-$$"
wpw="Test-Only-writer-$$"
rpw="Test-Only-reader-$$"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; "${rtc[@]}" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
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
# docs/REPORT_DATABASE.md: the two accounts
for sql in ("CREATE USER 'report_writer'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW ON site_reports.* TO 'report_writer'@'%'",
            "CREATE USER 'report_reader'@'%%' IDENTIFIED BY '%s'" % sys.argv[4],
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'"):
    cur.execute(sql)
EOPY

now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$py" "$here/scorecard_fixture.py" sm "$work/sm.json"
"$py" "$here/scorecard_fixture.py" jobs "$work/jobs.json" "$now"
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/stigman_mock.py" "$port" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & pids+=($!)
"$py" "$here/fake_aap_jobs.py" $((port + 1)) "$work/jobs.json" "$work/aap.log" & pids+=($!)
wait_port "$port"; wait_port $((port + 1))
cat > "$work/settings.yml" <<EOF
check_stig_stigman_api: http://127.0.0.1:$port/api
check_stig_stigman_token_url: http://127.0.0.1:$port/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
stigman_ckl_import_mode: scorecard
EOF
job='{"awx_job_id": 5000, "awx_job_template_id": 140}'
AAP="CONTROLLER_HOST=http://127.0.0.1:$((port + 1)) CONTROLLER_OAUTH_TOKEN=goodtoken"
DB="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$wpw"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...   (!TEXT: must not be there; DB_ENV, AAP_ENV: the credentials)
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && env -u REPORT_DB_HOST ${DB_ENV:-} ${AAP_ENV:-} ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i localhost, -c local playbooks/stigman_ckl_import.yml -e "ansible_python_interpreter=$py" -e "@$work/settings.yml" \
        "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$? t
    tr -s ' \n' '  ' < "$work/out.txt" > "$work/flat.txt"      # a warning is wrapped: its lines joined
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -5 | cut -c1-400; return 1
    fi
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            grep -qF -- "${t:1}" "$work/flat.txt" && { bad "$tname: has: ${t:1}"; return 1; }
        else
            grep -qF -- "$t" "$work/flat.txt" || { bad "$tname: lacks: $t"
                grep -oE 'WARNING.*|ERROR.*|fatal.*|"STIG compliance: [^"]*|Earlier points: [^"]*' "$work/out.txt" | head -5 | cut -c1-400; return 1; }
        fi
    done
    ok "$tname"
}
# said TEXT: how many times the run's output says TEXT
said() { grep -oF -- "$1" "$work/flat.txt" | wc -l; }
# q USER PASSWORD SQL: the first row's first value (statements that change are committed)
q() { "$py" - "$dbport" "$1" "$2" "$3" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user=sys.argv[2], password=sys.argv[3], database="site_reports",
                    ssl_disabled=True, autocommit=True)
cur = c.cursor(); cur.execute(sys.argv[4])
r = cur.fetchone()
print("" if r is None else r[0])
EOPY
}
rows() { q report_reader "$rpw" "SELECT COUNT(*) FROM stig_scorecard_point${1:+ WHERE $1}"; }
# the module, loaded as the playbook's Python sees it (its statements and its rows)
LOAD='
import importlib.util, sys
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m
def module(repo):
    load("ansible.module_utils.site_report_db", repo + "/plugins/module_utils/site_report_db.py")
    import ansible.module_utils
    ansible.module_utils.site_report_db = sys.modules["ansible.module_utils.site_report_db"]
    return load("site_stig_scorecard_db", repo + "/roles/stigman_ckl_import/library/site_stig_scorecard_db.py")
'
# seed NOW JOBS FROM TO [v3]: the points of the weeks FROM..TO before NOW as earlier runs' rows (JOBS=1: with the fake AAP's
# job IDs; 0: written outside AAP); seed NOW 0 days N: one point of N days ago - the rows the module itself would write.
# v3 (1.68.0): points with each host's counts, written as 1.68.0 writes them - the point's, platform and host rows
seed() { "$py" - "$dbport" "$wpw" "$repo" "$@" <<EOPY
$LOAD
import pymysql
port, pw, repo, now, with_jobs, first, last = sys.argv[1:8]
v3 = sys.argv[8:9] == ["v3"]
sys.path.insert(0, repo + "/tests/stig")
import scorecard_fixture as fx
m = module(repo)
t = fx.seconds(now)
c = pymysql.connect(host="127.0.0.1", port=int(port), user="report_writer", password=pw, database="site_reports", ssl_disabled=True)
cur = c.cursor()
if first == "days":
    todo = [(t - int(last) * 86400, 8, 0)]
else:                                                   # the fake AAP's weekly jobs are 4001 (8 weeks ago) .. 4008 (last week)
    todo = [(t - k * 7 * 86400 + 120, k, 4009 - k if with_jobs == "1" else 0) for k in range(int(first), int(last) - 1, -1)]
for when, k, job in todo:
    at = fx.stamp(when)
    pt = fx.point(fx.state(k), at, True, v3)
    cur.executemany(m.INSERT, m.rows_of(pt, m.stamp(at), job))
    if v3:
        cur.executemany(m.INSERT_P, m.rows_p_of(pt, m.stamp(at), job))
        cur.executemany(m.INSERT_H, m.rows_h_of(pt, m.stamp(at), job))
c.commit()
EOPY
}
only_get() {  # nothing but GET requests and the token's POST reached STIG Manager, and no write was logged
    if grep -v '^GET \|^POST /token' "$work/sm.log" | grep -q . || [ -e "$work/sm.log.writes" ]; then
        bad "  ...a request to STIG Manager that was not a GET:"; grep -v '^GET \|^POST /token' "$work/sm.log" | head -5
    else ok "  ...$1"; fi
}

# ---- 1. the first run: the table made, this run's rows ----
DB_ENV="$DB" run "the first run with the credential: the table made, this run's point written (3 rows)" ok \
    -- "STIG compliance: 4 CAT I open on 2 collections" "Earlier points: 0 from AAP's job history, 0 from the report database." \
       "Closed this week: - (no run last week)" "!$wpw" "![WARNING]"
[ "$(rows)" = 3 ] && [ "$(rows "cat1 > 0")" = 2 ] && [ "$(q report_reader "$rpw" "SELECT SUM(cat1 + cat2 + cat3) FROM stig_scorecard_point")" = 22 ] \
    && ok "  ...a row per collection, 22 open findings in all; the reader account reads them" || bad "  ...rows: $(rows)"
[ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'stig_scorecard'")" = 3 ] \
    && [ "$(q report_reader "$rpw" "SELECT CONCAT(collection, '/', assets, '/', JSON_VALID(rules)) FROM stig_scorecard_point WHERE collection_name = 'SITEA - RHEL 9'")" = "2/6/1" ] \
    && [ "$(q report_reader "$rpw" "SELECT CONCAT(JSON_EXTRACT(rules, '$.rr'), '/', JSON_EXTRACT(rules, '$.hk')) FROM stig_scorecard_point WHERE collection = '2'")" = "true/true" ] \
    && ok "  ...schema_version says stig_scorecard 3; a collection's row has its id, its assets and its rules as JSON (rules read, hosts kept)" \
    || bad "  ...schema_version or the collection's row"
# 1.67.0: a row per collection and platform (VMware: Other; RHEL 9: Linux, and Other for rhel01's Apache STIG; Windows)
prow() { q report_reader "$rpw" "SELECT ${2:-COUNT(*)} FROM stig_scorecard_platform${1:+ WHERE $1}"; }
# 1.70.0: the rules column is an object {rules, ra, rd} when the platform has CORA counts - its rules then
[ "$(prow)" = 4 ] && [ "$(prow "collection = '2' AND platform = 'Linux'" "CONCAT(assets, '/', cat1, '/', cat2, '/', cat3, '/', JSON_LENGTH(COALESCE(JSON_EXTRACT(rules, '$.rules'), rules)))")" = "6/1/8/2/4" ] \
    && ok "  ...stig_scorecard_platform: a row per collection and platform, its counts and rules" || bad "  ...platform rows: $(prow)"
# 1.68.0: a row per collection, host and platform - as many as the point's h holds, each with the host's counts
hrow() { q report_reader "$rpw" "SELECT ${2:-COUNT(*)} FROM stig_scorecard_host${1:+ WHERE $1}"; }
[ "$(hrow)" -gt 0 ] && [ "$(hrow "collection = '2' AND asset = 'rhel07'" "CONCAT(platform, '/', cat1, '/', cat2, '/', cat3)")" = "Linux/0/0/1" ] \
    && [ "$(hrow "collection = '3' AND asset = 'win03'" "CONCAT(platform, '/', cat1, '/', cat2, '/', cat3)")" = "Windows/2/1/0" ] \
    && [ "$(hrow "" "SUM(cat1 + cat2 + cat3)")" = 22 ] \
    && ok "  ...stig_scorecard_host: a row per host and platform ($(hrow)), its CAT I/II/III open, 22 in all" || bad "  ...host rows: $(hrow), $(hrow "" "SUM(cat1 + cat2 + cat3)")"
seed "$now" 0 days 1                                    # yesterday's run (a run of the same minute counts once, with this one)
DB_ENV="$DB" run "the next run reads the earlier point back" ok -- "from AAP's job history, " "from the report database." \
    "!, 0 from the report database" "![WARNING]"
[ "$(rows)" = 8 ] && ok "  ...and adds its own 3 rows (3 + yesterday's 2 + 3)" || bad "  ...rows: $(rows)"

# ---- 2. 8 earlier weeks in the table, no AAP ----
q report_writer "$wpw" "DELETE FROM stig_scorecard_point" >/dev/null
seed "$now" 0 8 1
DB_ENV="$DB" run "8 earlier weeks in the database, outside AAP: the week's change and the trend from the database alone" ok \
    -- "STIG compliance: 4 CAT I open on 2 collections, 1 more than last week" "Closed this week: 4" "Closed this week: 4" "Opened this week: 2" \
       "Earlier points: 0 from AAP's job history, 8 from the report database." "AAP's job history: not read (the job is not run by AAP)." \
       "The platform trend starts with this run: the earlier points have no platforms (before 1.67.0)." "![WARNING]"
[ "$(rows)" = 22 ] && ok "  ...this run's 3 rows added to the 19 (SITEA - VMware is 4 weeks old)" || bad "  ...rows: $(rows)"
only_get "STIG Manager got GET requests only"

# ---- 2b. 1.68.0: last week's point with each host's counts, from the database alone: the week compared host by host ----
q report_writer "$wpw" "DELETE FROM stig_scorecard_point" >/dev/null
q report_writer "$wpw" "DELETE FROM stig_scorecard_platform" >/dev/null
q report_writer "$wpw" "DELETE FROM stig_scorecard_host" >/dev/null
seed "$now" 0 8 3; seed "$now" 0 2 1 v3
mkdir -p "$work/site"
DB_ENV="$DB" run "v3 points in the database: the week compared host by host" ok -e "report_web_dest=$work/site" \
    -- "STIG compliance: 4 CAT I open on 2 collections, 1 more than last week" "Closed this week: 4" "Closed this week: 4" "Opened this week: 2" \
       "Earlier points: 0 from AAP's job history, 8 from the report database." "![WARNING]"
"$py" - "$work/site" <<'EOPY' && ok "  ...the hosts read back: the tips and the workbook's rows name hosts (rhel07: 2 CAT II closed), not rules" || bad "  ...not host by host"
import glob, json, sys
page = json.dumps(json.load(open(sorted(glob.glob(sys.argv[1] + "/*/latest.json"))[-1])))
assert "rhel07: 2 CAT II" in page, "no host tip"
assert "SV-257985 on 3 hosts" not in page, "a rule tip: compared rule by rule"
EOPY
[ "$(hrow "read_at < NOW() - INTERVAL 1 DAY")" -gt 0 ] && ok "  ...the seeded weeks' host rows are there to read" || bad "  ...no seeded host rows"

# ---- 3. AAP and the database together ----
q report_writer "$wpw" "DELETE FROM stig_scorecard_point" >/dev/null
seed "$now" 1 8 4; seed "$now" 0 days 2
: > "$work/aap.log"
DB_ENV="$DB" AAP_ENV="$AAP" run "AAP and the database: 5 weeks from the database with their job IDs - not read from AAP again" ok -e "$job" \
    -- "STIG compliance: 4 CAT I open on 2 collections, 1 more than last week" "Closed this week: 4" "from the report database" "![WARNING]"
grep -oE "Earlier points: [^\"]*" "$work/out.txt" | head -1 > "$work/earlier.txt"
grep -q "Earlier points: 4 from AAP's job history, 6 from the report database." "$work/earlier.txt" \
    && ok "  ...4 runs from AAP (3 weeks and the run of 3 days ago), 6 from the database" || bad "  ...$(cat "$work/earlier.txt")"
! grep -q 'jobs/4001/\|jobs/4005/' "$work/aap.log" && grep -q 'jobs/4008/' "$work/aap.log" \
    && ok "  ...AAP: the jobs the database gave were not read again" || bad "  ...AAP read a job the database gave"
[ "$(rows "job = 5000")" = 3 ] && ok "  ...this run's rows carry its job ID" || bad "  ...job 5000's rows: $(rows "job = 5000")"

# ---- 4. old rows deleted ----
q report_writer "$wpw" "DELETE FROM stig_scorecard_point" >/dev/null
seed "$now" 0 8 1; seed "$now" 0 days 900
before="$(rows)"
DB_ENV="$DB" run "stigman_scorecard_db_keep_days: 0 keeps every row" ok -e stigman_scorecard_db_keep_days=0 -- "8 from the report database"
[ "$(rows "read_at < NOW() - INTERVAL 850 DAY")" = 2 ] && ok "  ...the rows of 900 days ago are there" || bad "  ...old rows: $(rows "read_at < NOW() - INTERVAL 850 DAY")"
DB_ENV="$DB" run "rows older than stigman_scorecard_db_keep_days (800) are deleted" ok -- "from the report database"
[ "$(rows "read_at < NOW() - INTERVAL 850 DAY")" = 0 ] && [ "$(rows)" -gt "$before" ] && ok "  ...gone, nothing else" || bad "  ...old rows left"

# ---- 5. a dry run ----
before="$(rows)"
DB_ENV="$DB" run "a dry run (Check): the earlier points read, nothing written" ok --check \
    -- "from the report database" "Closed this week: 4" "![WARNING]"
[ "$(rows)" = "$before" ] && ok "  ...no row added" || bad "  ...a dry run wrote: $before -> $(rows)"

# ---- 6. never a failed job ----
before="$(rows)"
DB_ENV="${DB/PORT=$dbport/PORT=$((dbport + 4))}" AAP_ENV="$AAP" run "the database not reached: one warning, the scorecard whole from AAP's history, green" ok \
    -e "$job" \
    -- "[WARNING]: The report database 127.0.0.1:$((dbport + 4))/site_reports:" "goes on with AAP's job history" \
       "9 from AAP's job history, 0 from the report database." "Closed this week: 4" "The report database: " "!$wpw"
[ "$(said "[WARNING]: The report database")" = 1 ] && ok "  ...said once" || bad "  ...the warning $(said "[WARNING]: The report database") times"
DB_ENV="${DB/PASSWORD=$wpw/PASSWORD=Test-Only-wrong-$$}" run "a wrong password: one warning (the password not printed), green" ok \
    -- "[WARNING]: The report database" "Access denied" "STIG compliance: 4 CAT I open on 2 collections" "!Test-Only-wrong-$$"
DB_ENV="${DB/report_writer REPORT_DB_PASSWORD=$wpw/report_reader REPORT_DB_PASSWORD=$rpw}" run "a read-only account: the earlier points used, this run's not kept - one warning, green" ok \
    -- "[WARNING]: The report database" "this run's point was not kept:" "command denied" "from the report database" \
       "Closed this week: 4" "!$rpw"
[ "$(rows)" = "$before" ] && ok "  ...nothing written by the three" || bad "  ...rows: $before -> $(rows)"
DB_ENV="$DB" run "stigman_scorecard_db: false - the database is not touched" ok -e stigman_scorecard_db=false \
    -- "Earlier points: 0 from AAP's job history, 0 from the report database." "STIG compliance: 4 CAT I open on 2 collections" "![WARNING]"
[ "$(rows)" = "$before" ] && ok "  ...no row added" || bad "  ...rows: $before -> $(rows)"
q report_writer "$wpw" "UPDATE schema_version SET version = 99 WHERE component = 'stig_scorecard'" >/dev/null
DB_ENV="$DB" run "a table of a newer release: not read, not written, said - green" ok \
    -- "[WARNING]: The report database" "is version 99, newer than this release knows (3) - update site-automation" \
       "STIG compliance: 4 CAT I open on 2 collections"
[ "$(rows)" = "$before" ] && ok "  ...no row added" || bad "  ...rows: $before -> $(rows)"

# ---- 6b. 1.67.0: a database of 1.58.0 to 1.66.x (schema version 1, no platform table) ----
q root "$root" "DROP TABLE stig_scorecard_platform" >/dev/null
q root "$root" "DROP TABLE stig_scorecard_host" >/dev/null
q root "$root" "UPDATE schema_version SET version = 1 WHERE component = 'stig_scorecard'" >/dev/null
before="$(rows)"
DB_ENV="$DB" run "version 1 with stigman_scorecard_db_create: false - used as before, the platforms and hosts not kept, said; green" ok \
    -e stigman_scorecard_db_create=false \
    -- "the table stig_scorecard_platform is not there: the platforms are not kept" \
       "the table stig_scorecard_host is not there: each host's counts are not kept" "Closed this week: 4" "![WARNING]"
[ "$(rows)" = $((before + 3)) ] && [ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'stig_scorecard'")" = 1 ] \
    && ok "  ...this run's rows kept, version 1, no new table" || bad "  ...rows $before -> $(rows)"
before="$(rows)"
DB_ENV="$DB" run "version 1, the next run: the platform and host tables made, version 3, the rows kept" ok \
    -- "The report database: made the tables of schema version 3; the rows kept before are as they were." \
       "Closed this week: 4" "![WARNING]"
[ "$(rows)" = $((before + 3)) ] && [ "$(prow)" = 4 ] && [ "$(hrow)" -gt 0 ] \
    && [ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'stig_scorecard'")" = 3 ] \
    && ok "  ...every row of before there, this run's 3, its 4 platform rows and its host rows added, version 3" || bad "  ...rows $before -> $(rows), $(prow), $(hrow)"
DB_ENV="$DB" run "and the run after it says nothing more" ok -- "Closed this week: 4" "!made the tables of schema version" "![WARNING]"

# ---- 6c. 1.68.0: a database of 1.67.x (schema version 2: the platform table, no host table) ----
q root "$root" "DROP TABLE stig_scorecard_host" >/dev/null
q root "$root" "UPDATE schema_version SET version = 2 WHERE component = 'stig_scorecard'" >/dev/null
before="$(rows)"; pbefore="$(prow)"
DB_ENV="$DB" run "version 2 with stigman_scorecard_db_create: false - used as before, each host's counts not kept, said; green" ok \
    -e stigman_scorecard_db_create=false \
    -- "the table stig_scorecard_host is not there: each host's counts are not kept" "!the table stig_scorecard_platform is not there" \
       "Closed this week: 4" "![WARNING]"
[ "$(rows)" = $((before + 3)) ] && [ "$(prow)" = $((pbefore + 4)) ] \
    && [ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'stig_scorecard'")" = 2 ] \
    && ok "  ...this run's point and platform rows kept, version 2, no host table" || bad "  ...rows $before -> $(rows), $pbefore -> $(prow)"
before="$(rows)"; pbefore="$(prow)"
DB_ENV="$DB" run "version 2, the next run: the host table made, version 3, every row kept" ok \
    -- "The report database: made the tables of schema version 3; the rows kept before are as they were." \
       "Closed this week: 4" "![WARNING]"
[ "$(rows)" = $((before + 3)) ] && [ "$(prow)" = $((pbefore + 4)) ] && [ "$(hrow)" -gt 0 ] \
    && [ "$(hrow "" "COUNT(DISTINCT read_at)")" = 1 ] \
    && [ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'stig_scorecard'")" = 3 ] \
    && ok "  ...the point and platform rows of before all there, this run's added; host rows for this run only; version 3" \
    || bad "  ...rows $before -> $(rows), platforms $pbefore -> $(prow), hosts $(hrow)"
DB_ENV="$DB" run "and the run after it says nothing more" ok -- "Closed this week: 4" "!made the tables of schema version" "![WARNING]"

# ---- 7. no table, and not allowed to make it ----
q root "$root" "DROP TABLE stig_scorecard_point" >/dev/null
q root "$root" "DROP TABLE stig_scorecard_platform" >/dev/null
q root "$root" "DROP TABLE stig_scorecard_host" >/dev/null
q root "$root" "DELETE FROM schema_version WHERE component = 'stig_scorecard'" >/dev/null
tables() { q report_reader "$rpw" "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name = 'stig_scorecard_point'"; }
DB_ENV="$DB" run "no table and stigman_scorecard_db_create: false - said with where the statement is, green" ok \
    -e stigman_scorecard_db_create=false \
    -- "[WARNING]: The report database" "the table stig_scorecard_point is not there (stigman_scorecard_db_create: false) - make it once, docs/REPORT_DATABASE.md" \
       "STIG compliance: 4 CAT I open on 2 collections"
DB_ENV="$DB" run "no table, a dry run: not made" ok --check -- "the table stig_scorecard_point is not there (check mode: nothing made)"
[ "$(tables)" = 0 ] && ok "  ...the table was not made by either" || bad "  ...the table was made"
# the statement of docs/REPORT_DATABASE.md, run by hand as an admin: then a run with stigman_scorecard_db_create: false writes
"$py" - "$dbport" "$root" "$repo" <<EOPY && ok "the table made by hand with the module's statement (the one in docs/REPORT_DATABASE.md)" || bad "the statement by hand"
$LOAD
import pymysql
m = module(sys.argv[3])
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=True)
cur = c.cursor()
for sql in m.ddl().split(";"):
    if sql.strip():
        cur.execute(sql)
EOPY
DB_ENV="$DB" run "then stigman_scorecard_db_create: false writes into it" ok -e stigman_scorecard_db_create=false \
    -- "Earlier points: 0 from AAP's job history, 0 from the report database." "![WARNING]"
[ "$(rows)" = 3 ] && [ "$(hrow)" -gt 0 ] && ok "  ...3 rows, and the host rows" || bad "  ...rows: $(rows), hosts $(hrow)"
# 1.68.0: a version 2 database brought to 3 by hand (docs/REPORT_DATABASE.md, ddl_upgrade), then create: false
q root "$root" "DROP TABLE stig_scorecard_host" >/dev/null
q root "$root" "UPDATE schema_version SET version = 2 WHERE component = 'stig_scorecard'" >/dev/null
before="$(rows)"
"$py" - "$dbport" "$root" "$repo" <<EOPY && ok "a version 2 database brought to 3 by hand with the module's upgrade statements" || bad "the upgrade by hand"
$LOAD
import pymysql
m = module(sys.argv[3])
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=True)
cur = c.cursor()
for sql in m.ddl_upgrade().split(";"):
    if sql.strip():
        cur.execute(sql)
EOPY
DB_ENV="$DB" run "then stigman_scorecard_db_create: false keeps the hosts too, nothing said" ok -e stigman_scorecard_db_create=false \
    -- "4 CAT I open" "!is not there" "![WARNING]"
[ "$(rows)" = $((before + 3)) ] && [ "$(hrow)" -gt 0 ] \
    && [ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'stig_scorecard'")" = 3 ] \
    && ok "  ...the rows of before kept, this run's added with its host rows, version 3" || bad "  ...rows $before -> $(rows), hosts $(hrow)"
only_get "STIG Manager got GET requests only, all through"

if [ "$fails" -eq 0 ]; then echo "ok   - all scorecard database scenarios passed"; else echo "$fails FAILED"; exit 1; fi
