#!/usr/bin/env bash
# The capacity trends report's history in the report database (1.52.0, site_ontap_capacity_db) against a real MySQL 8.4
# (the mysql:8.4 image, in podman or docker) set up as docs/REPORT_DATABASE.md says: database site_reports, report_writer
# and report_reader with their grants, plain TCP. Two fake ONTAP clusters and a fake AAP (tests/netapp/capacity_fixture.py).
# The first run: the table made, this run's rows written, the reader sees them. 30 earlier days in the table: the whole
# story with no AAP. AAP and the database together: a job the database gave is not read from AAP again, a run both hold
# counts once. Rows older than ontap_capacity_db_keep_days deleted (0: kept). A dry run (Check): read, nothing written.
# Never a failed job: the server not reached, a wrong password (not printed), a read-only account (the earlier points
# still used), a table of a newer release, no table with ontap_capacity_db_create: false - one warning each, the report
# whole, green. ontap_capacity_db: false: not touched.
# The container's name and the ports come from variables; the container is removed by its name. Skipped (exit 0) when
# there is no container runtime, image or PyMySQL.
#   bash tests/netapp/run_capacity_db_test.sh     (ANSIBLE_PLAYBOOK=..., CAPACITY_DB_TEST_IMAGE=..., CAPACITY_DB_TEST_RUNTIME=docker)
#   CAPACITY_DB_TEST_NAME=..., CAPACITY_DB_TEST_PORT=18480 (the database; the fakes on the 4 ports after it),
#   CAPACITY_DB_TEST_PYTHON=...: the Python the modules run on (PyMySQL and cryptography installed there)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${CAPACITY_DB_TEST_PYTHON:-$(command -v python3)}"
image="${CAPACITY_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${CAPACITY_DB_TEST_PORT:-18480}"
port=$((dbport + 1))
name="${CAPACITY_DB_TEST_NAME:-sa-capacity-db-test}"
rt=${CAPACITY_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
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

printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$py" "$here/capacity_fixture.py" ontap "$work/c1.json" 0 "$now"
"$py" "$here/capacity_fixture.py" ontap "$work/c2.json" 1 "$now"
"$py" "$here/capacity_fixture.py" jobs "$work/jobs.json" "$now" 30
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/fake_ontap.py" "$port" "$work/c1.json" "$work/c1.log" & pids+=($!)
"$py" "$here/fake_ontap.py" $((port + 1)) "$work/c2.json" "$work/c2.log" & pids+=($!)
"$py" "$here/fake_aap_jobs.py" $((port + 2)) "$work/jobs.json" "$work/aap.log" & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 2))
clusters="{\"ontap_clusters\": [\"http://127.0.0.1:$port\", \"http://127.0.0.1:$((port + 1))\"]}"
job='{"awx_job_id": 5000, "awx_job_template_id": 88}'
AAP="CONTROLLER_HOST=http://127.0.0.1:$((port + 2)) CONTROLLER_OAUTH_TOKEN=goodtoken"
DB="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$wpw"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...   (!TEXT: must not be there; DB_ENV, AAP_ENV: the credentials)
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && env -u REPORT_DB_HOST ONTAP_USERNAME=svc_ro ONTAP_PASSWORD=readonly-test ${DB_ENV:-} ${AAP_ENV:-} ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "$work/inv.yml" playbooks/ontap_capacity_trends.yml "${args[@]}") > "$work/out.txt" 2>&1
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
                grep -oE 'WARNING.*|ERROR.*|fatal.*|"NetApp ONTAP capacity trends: [^"]*|[0-9]+ runs in the last [^"]*' "$work/out.txt" | head -5 | cut -c1-400; return 1; }
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
rows() { q report_reader "$rpw" "SELECT COUNT(*) FROM ontap_capacity_point${1:+ WHERE $1}"; }
# seed NOW JOBS FROM TO: the points of the days FROM..TO before NOW as earlier runs' rows (JOBS=1: with the fake AAP's job
# IDs; 0: written outside AAP) - through the filters the playbook uses
seed() { "$py" - "$dbport" "$wpw" "$repo" "$@" <<'EOPY'
import datetime, sys, pymysql
port, pw, repo, now, with_jobs, first, last = sys.argv[1:8]
sys.path[:0] = [repo + "/plugins/filter", repo + "/tests/netapp"]
import capacity_fixture as fx
import ontap_capacity_filters as f
c = pymysql.connect(host="127.0.0.1", port=int(port), user="report_writer", password=pw, database="site_reports", ssl_disabled=True)
cur = c.cursor()
for d in range(int(first), int(last) - 1, -1):
    col = fx.collected(now, min(d, 30))                 # the fixture's story is 30 days long
    at = datetime.datetime.strptime(fx.when(now, d), "%Y-%m-%dT%H:%M:%SZ")
    job = 4000 + (30 - d) * 3 if with_jobs == "1" and d <= 30 else 0
    for r in f.ontap_capacity_db_rows(f.ontap_capacity_points(col, {"volume_min_gb": 50})):
        cur.execute("INSERT INTO ontap_capacity_point (read_at, job, cluster, cluster_name, kind, uuid, name, svm, size_b, used_b, "
                    "reserve_pct, snap_used_b) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", [at, job] + r)
c.commit()
EOPY
}

# ---- 1. the first run: the table made, this run's rows ----
DB_ENV="$DB" run "the first run with the credential: the table made, this run's point written (16 rows)" ok -e "$clusters" \
    -- "run 1 - a trend needs 3" "1 run in the last 30 days (ontap_capacity_days), this one included: 0 from AAP's job history, 0 from the report database." \
       "The report database (127.0.0.1:$dbport/site_reports) holds 1 run" "(kept 800 days, ontap_capacity_db_keep_days)" "!$wpw" "![WARNING]"
[ "$(rows)" = 16 ] && [ "$(rows "kind = 'cluster'")" = 2 ] && [ "$(rows "kind = 'aggregate'")" = 5 ] && [ "$(rows "kind = 'volume'")" = 9 ] \
    && ok "  ...2 clusters, 5 aggregates, 9 volumes (not the SVM's root, not the small volume); the reader account reads them" \
    || bad "  ...rows: $(rows)"
[ "$(q report_reader "$rpw" "SELECT version FROM schema_version WHERE component = 'ontap_capacity'")" = 1 ] \
    && [ "$(q report_reader "$rpw" "SELECT CONCAT(name, '/', svm, '/', reserve_pct) FROM ontap_capacity_point WHERE name = 'vol_db_logs'")" = "vol_db_logs/svm2/5.00" ] \
    && ok "  ...schema_version says ontap_capacity 1; a volume's row has its name, SVM and snapshot reserve" || bad "  ...schema_version or the volume's row"

# ---- 2. 30 earlier days in the table, no AAP ----
q report_writer "$wpw" "DELETE FROM ontap_capacity_point" >/dev/null
seed "$now" 1 30 1
DB_ENV="$DB" run "30 earlier days in the database, outside AAP: the whole story from the database alone" ok -e "$clusters" \
    -- "NetApp ONTAP capacity trends: 2 aggregates full within 90 days, 2 volumes back again, 2 filling fast" \
       "31 runs in the last 30 days (ontap_capacity_days), this one included: 0 from AAP's job history, 30 from the report database." \
       "holds 31 runs, the oldest of" "used space dropped 13%" "130.0" "Not run by AAP: no job history to read." "![WARNING]"
[ "$(rows)" = $((30 * 16 - 29 + 16)) ] && ok "  ...this run's 16 rows added to the 451 (vol_new is a day old)" || bad "  ...rows: $(rows)"

# ---- 3. AAP and the database together ----
q report_writer "$wpw" "DELETE FROM ontap_capacity_point" >/dev/null
seed "$now" 1 30 11; seed "$now" 0 10 6
: > "$work/aap.log"
DB_ENV="$DB" AAP_ENV="$AAP" run "AAP and the database: 20 runs from the database, 10 from AAP, 5 of them in both - each run once" ok \
    -e "$clusters" -e "$job" \
    -- "31 runs in the last 30 days (ontap_capacity_days), this one included: 10 from AAP's job history, 20 from the report database (5 more in both, counted once)." \
       "2 volumes back again, 2 filling fast" "![WARNING]"
n="$(grep -o 'GET /api/controller/v2/jobs/[0-9]*/' "$work/aap.log" | sort -u | wc -l)"
[ "$n" = 12 ] && ! grep -q 'jobs/4000/\|jobs/4057/' "$work/aap.log" && grep -q 'jobs/4087/' "$work/aap.log" \
    && ok "  ...AAP: the 20 jobs the database gave were not read again (12 job pages read)" || bad "  ...AAP job pages read: $n"
[ "$(rows "job = 5000")" = 16 ] && ok "  ...this run's rows carry its job ID" || bad "  ...job 5000's rows: $(rows "job = 5000")"
DB_ENV="$DB" AAP_ENV="$AAP" run "the same job launched again: still one run each" ok -e "$clusters" -e "$job" \
    -- "31 runs in the last 30 days" "![WARNING]"

# ---- 4. old rows deleted ----
q report_writer "$wpw" "DELETE FROM ontap_capacity_point" >/dev/null
seed "$now" 1 30 1; seed "$now" 0 900 900
before="$(rows)"
DB_ENV="$DB" run "ontap_capacity_db_keep_days: 0 keeps every row" ok -e "$clusters" -e ontap_capacity_db_keep_days=0 -- "runs in the last 30 days"
[ "$(rows "read_at < NOW() - INTERVAL 850 DAY")" = 15 ] && ok "  ...the rows of 900 days ago are there" || bad "  ...old rows: $(rows "read_at < NOW() - INTERVAL 850 DAY")"
DB_ENV="$DB" run "rows older than ontap_capacity_db_keep_days (800) are deleted" ok -e "$clusters" -- "runs in the last 30 days" "kept 800 days"
[ "$(rows "read_at < NOW() - INTERVAL 850 DAY")" = 0 ] && [ "$(rows)" -gt "$before" ] && ok "  ...gone, nothing else" || bad "  ...old rows left"
DB_ENV="$DB" run "a longer window (ontap_capacity_days: 60) reads no more than there is" ok -e "$clusters" -e ontap_capacity_days=60 \
    -- "runs in the last 60 days"

# ---- 5. a dry run ----
before="$(rows)"
DB_ENV="$DB" run "a dry run (Check): the earlier points read, nothing written" ok --check -e "$clusters" \
    -- "from the report database" "2 volumes back again" "![WARNING]"
[ "$(rows)" = "$before" ] && ok "  ...no row added" || bad "  ...a dry run wrote: $before -> $(rows)"

# ---- 6. never a failed job ----
before="$(rows)"
DB_ENV="${DB/PORT=$dbport/PORT=$((dbport + 4))}" AAP_ENV="$AAP" run "the database not reached: one warning, the report whole from AAP's history, green" ok \
    -e "$clusters" -e "$job" -e ontap_capacity_db_timeout=3 \
    -- "[WARNING]: The report database 127.0.0.1:$((dbport + 4))/site_reports:" "goes on with AAP's job history" \
       "30 from AAP's job history, 0 from the report database." "2 volumes back again" "The report database: " "!$wpw"
[ "$(said "[WARNING]: The report database")" = 1 ] && ok "  ...said once" || bad "  ...the warning $(said "[WARNING]: The report database") times"
DB_ENV="${DB/PASSWORD=$wpw/PASSWORD=Test-Only-wrong-$$}" run "a wrong password: one warning (the password not printed), green" ok -e "$clusters" \
    -- "[WARNING]: The report database" "Access denied" "run 1 - a trend needs 3" "!Test-Only-wrong-$$"
DB_ENV="${DB/report_writer REPORT_DB_PASSWORD=$wpw/report_reader REPORT_DB_PASSWORD=$rpw}" run "a read-only account: the earlier points used, this run's not kept - one warning, green" ok \
    -e "$clusters" -- "[WARNING]: The report database" "this run's point was not kept:" "command denied" "from the report database" \
       "2 volumes back again" "!$rpw"
[ "$(rows)" = "$before" ] && ok "  ...nothing written by the three" || bad "  ...rows: $before -> $(rows)"
DB_ENV="$DB" run "ontap_capacity_db: false - the database is not touched" ok -e "$clusters" -e ontap_capacity_db=false \
    -- "The report database is not used: ontap_capacity_db is false." "run 1 - a trend needs 3" "![WARNING]"
[ "$(rows)" = "$before" ] && ok "  ...no row added" || bad "  ...rows: $before -> $(rows)"
q report_writer "$wpw" "UPDATE schema_version SET version = 99 WHERE component = 'ontap_capacity'" >/dev/null
DB_ENV="$DB" run "a table of a newer release: not read, not written, said - green" ok -e "$clusters" \
    -- "[WARNING]: The report database" "is version 99, newer than this release knows (1) - update site-automation" "run 1 - a trend needs 3"
[ "$(rows)" = "$before" ] && ok "  ...no row added" || bad "  ...rows: $before -> $(rows)"

# ---- 7. no table, and not allowed to make it ----
q root "$root" "DROP TABLE ontap_capacity_point" >/dev/null
q root "$root" "DELETE FROM schema_version WHERE component = 'ontap_capacity'" >/dev/null
tables() { q report_reader "$rpw" "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name = 'ontap_capacity_point'"; }
DB_ENV="$DB" run "no table and ontap_capacity_db_create: false - said with where the statement is, green" ok -e "$clusters" \
    -e ontap_capacity_db_create=false \
    -- "[WARNING]: The report database" "the table ontap_capacity_point is not there (ontap_capacity_db_create: false) - make it once, docs/REPORT_DATABASE.md" \
       "run 1 - a trend needs 3"
DB_ENV="$DB" run "no table, a dry run: not made" ok --check -e "$clusters" -- "the table ontap_capacity_point is not there (check mode: nothing made)"
[ "$(tables)" = 0 ] && ok "  ...the table was not made by either" || bad "  ...the table was made"
# the statement of docs/REPORT_DATABASE.md, run by hand as an admin: then a run with ontap_capacity_db_create: false writes
"$py" - "$dbport" "$root" "$repo" <<'EOPY' && ok "the table made by hand with the module's statement (the one in docs/REPORT_DATABASE.md)" || bad "the statement by hand"
import importlib.util, sys, types, pymysql
repo = sys.argv[3]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); sys.modules[name] = m; spec.loader.exec_module(m); return m
load("ansible.module_utils.site_report_db", repo + "/plugins/module_utils/site_report_db.py")
import ansible.module_utils
ansible.module_utils.site_report_db = sys.modules["ansible.module_utils.site_report_db"]
m = load("site_ontap_capacity_db", repo + "/roles/ontap_report/library/site_ontap_capacity_db.py")
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=True)
cur = c.cursor()
for sql in m.ddl().split(";"):
    if sql.strip():
        cur.execute(sql)
EOPY
DB_ENV="$DB" run "then ontap_capacity_db_create: false writes into it" ok -e "$clusters" -e ontap_capacity_db_create=false \
    -- "holds 1 run" "![WARNING]"
[ "$(rows)" = 16 ] && ok "  ...16 rows" || bad "  ...rows: $(rows)"

if [ "$fails" -eq 0 ]; then echo "ok   - all capacity trends database scenarios passed"; else echo "$fails FAILED"; exit 1; fi
