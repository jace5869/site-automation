#!/usr/bin/env bash
# The network map's history in the report database (1.111.0, site_netmap_db, roles/netmap_report/tasks/db.yml) against a real MySQL 8.4 (the mysql:8.4
# image, in podman or docker) set up as docs/REPORT_DATABASE.md says (database site_reports, report_writer, report_reader), and the fake SolarWinds of
# tests/netmap/fake_swis.py. Checks: the first run makes the five tables and the schema_version row and keeps the read (a row, every site, every WAN
# link) - the reader sees them; a second run is a second read; netmap_db false touches nothing; SolarWinds not read is one row (ok 0) and the job still
# green; reads older than netmap_db_keep_reads_days (90) become one row a site and a link a day (reads, reads down, outages = two in a row, minutes down)
# and are deleted, the recent reads stay, a day row older than netmap_db_keep_days (400) is deleted; the same read twice is one row; a dry run (Check)
# writes nothing; never a failed job: a wrong password (not printed), a read-only account, the server not reached, no table with netmap_db_create
# false - one warning each, the report whole. Skipped (exit 0) with no container runtime, image or PyMySQL.
#   bash tests/netmap/run_netmap_db_test.sh     (ANSIBLE_PLAYBOOK=..., NETMAP_DB_TEST_IMAGE=..., NETMAP_DB_TEST_RUNTIME=docker, NETMAP_DB_TEST_PORT=18490)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${NETMAP_DB_TEST_PYTHON:-$(command -v python3)}"
image="${NETMAP_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${NETMAP_DB_TEST_PORT:-18490}"
port=$((dbport + 1))
name="${NETMAP_DB_TEST_NAME:-sa-netmap-db-test}"
rt=${NETMAP_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
root="Test-Only-root-$$"; wpw="Test-Only-writer-$$"; rpw="Test-Only-reader-$$"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; "${rtc[@]}" rm -f "$name" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

"${rtc[@]}" rm -f "$name" >/dev/null 2>&1
"${rtc[@]}" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" --tls-version= >/dev/null || { echo "ABORT - MySQL did not start"; exit 1; }
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
for sql in ("CREATE USER 'report_writer'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW ON site_reports.* TO 'report_writer'@'%'",
            "CREATE USER 'report_reader'@'%%' IDENTIFIED BY '%s'" % sys.argv[4],
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'"):
    cur.execute(sql)
EOPY

pw='ro&"<test>9'
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
"$py" "$here/fixture.py" "$work/fx.json" 300 svc_swis_ro "$pw" > /dev/null
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Test CA" -keyout "$work/ca.key" -out "$work/ca.pem" -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout "$work/srv.key" -out "$work/srv.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n' > "$work/ext.cnf"
openssl x509 -req -in "$work/srv.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial -days 2 -out "$work/srv.pem" -extfile "$work/ext.cnf" 2>/dev/null
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
FAKE_SWIS_STATE="$work/state.json" "$py" "$here/fake_swis.py" "$port" "$work/fx.json" "$work/swis.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
wait_port "$port"
base="\"netmap_solarwinds_url\": \"https://127.0.0.1:$port\", \"netmap_ca_path\": \"$work/ca.pem\", \"netmap_page_size\": 500, \"netmap_compare\": false, \"netmap_xlsx\": false"
DB="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$wpw"

# run NAME EXPECT(ok|fail) VARS_JSON [TEXT...]   (!TEXT: must not be there; DB_ENV: the credential, default the writer's; CHECK=1: a dry run)
run() {
    local tname="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && env -u REPORT_DB_HOST SOLARWINDS_USERNAME=svc_swis_ro SOLARWINDS_PASSWORD="$pw" ${DB_ENV-$DB} ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" playbooks/netmap_report.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$? t
    tr -s ' \n' '  ' < "$work/out.txt" > "$work/flat.txt"
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -5 | cut -c1-400; return 1
    fi
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then grep -qF -- "${t:1}" "$work/flat.txt" && { bad "$tname: has: ${t:1}"; return 1; }
        else grep -qF -- "$t" "$work/flat.txt" || { bad "$tname: lacks: $t"; grep -oE 'WARNING.*|Report database.*' "$work/out.txt" | head -4 | cut -c1-300; return 1; }; fi
    done
    ok "$tname"
}
# q USER PASSWORD SQL: the first row's first value
q() { "$py" - "$dbport" "$1" "$2" "$3" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user=sys.argv[2], password=sys.argv[3], database="site_reports", ssl_disabled=True, autocommit=True)
cur = c.cursor(); cur.execute(sys.argv[4])
r = cur.fetchone()
print("" if r is None else r[0])
EOPY
}
qw() { q report_writer "$wpw" "$1"; }
qr() { q report_reader "$rpw" "$1"; }
is() { local n="$1" got="$2" want="$3"; if [ "$got" = "$want" ]; then ok "  ...$n ($got)"; else bad "  ...$n: got '$got', wanted '$want'"; fi; }

# 1. the first run: the five tables, the schema row, the read
run "the first run: the tables made, this run's read kept" ok "{$base, \"awx_job_id\": 5001}" "Report database: read kept (" "tables made: netmap_read, netmap_site, netmap_link, netmap_site_day, netmap_link_day"
is "five tables" "$(qw "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name LIKE 'netmap\\_%'")" 5
is "schema_version netmap 1" "$(qw "SELECT version FROM schema_version WHERE component = 'netmap'")" 1
is "one read, ok" "$(qw "SELECT CONCAT(COUNT(*), '/', SUM(ok)) FROM netmap_read")" "1/1"
sites="$(qw "SELECT COUNT(*) FROM netmap_site")"; wan="$(qw "SELECT COUNT(*) FROM netmap_link")"
[ "${sites:-0}" -ge 5 ] && ok "  ...a row for each site ($sites)" || bad "  ...sites: $sites"
[ "${wan:-0}" -ge 1 ] && ok "  ...a row for each WAN link ($wan)" || bad "  ...WAN links: $wan"
is "the read's counts are the rows'" "$(qw "SELECT IF(sites = (SELECT COUNT(*) FROM netmap_site) AND wan_links = (SELECT COUNT(*) FROM netmap_link) AND nodes = (SELECT SUM(nodes) FROM netmap_site), 'same', 'differ') FROM netmap_read")" same
is "the reader sees the rows" "$(qr "SELECT COUNT(*) FROM netmap_read")" 1
is "the job number kept" "$(qw "SELECT job FROM netmap_read")" 5001
is "the site Down in the fixture is Down in the rows" "$(qw "SELECT IF(SUM(down) = (SELECT nodes_down FROM netmap_read), 'ok', 'no') FROM netmap_site")" ok
sleep 1.2
# 2. a second run is a second read
run "a second run: a second read" ok "{$base, \"awx_job_id\": 5002}" "Report database: read kept (" "!tables made"
is "two reads" "$(qw "SELECT COUNT(*) FROM netmap_read")" 2
is "each site twice" "$(qw "SELECT MIN(c) FROM (SELECT COUNT(*) c FROM netmap_site GROUP BY site) x")" 2
# 3. netmap_db false: not touched
run "netmap_db false: nothing kept, nothing said" ok "{$base, \"netmap_db\": false}" "!Report database:"
is "still two reads" "$(qw "SELECT COUNT(*) FROM netmap_read")" 2
# 4. a dry run writes nothing
CHECK=1 run "a dry run (Check): nothing written" ok "{$base}"
is "still two reads (Check)" "$(qw "SELECT COUNT(*) FROM netmap_read")" 2
# 5. SolarWinds not read: one row ok 0, the job green
run "SolarWinds not reached: a missed read is one row, the job green" ok "{$base, \"netmap_solarwinds_url\": \"https://127.0.0.1:$((port + 5))\", \"awx_job_id\": 5003}" "Report database: read kept (0 sites, 0 WAN links)"
is "a missed read: ok 0" "$(qw "SELECT COUNT(*) FROM netmap_read WHERE ok = 0")" 1
is "no site rows for it" "$(qw "SELECT COUNT(*) FROM netmap_site WHERE read_at = (SELECT read_at FROM netmap_read WHERE ok = 0)")" 0
sleep 1.2
# 6. the roll-up and the pruning: 3 old days of reads written by the module's own writer, then a run
"$py" - "$dbport" "$wpw" "$repo" <<'EOPY'
import datetime, os, sys
sys.path[:0] = [os.path.join(sys.argv[3], "roles", "netmap_report", "library"), os.path.join(sys.argv[3], "plugins", "module_utils")]
import pymysql, site_report_db, ansible.module_utils
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db); ansible.module_utils.site_report_db = site_report_db
import site_netmap_db as ndb
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="report_writer", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=False)
now = datetime.datetime.utcnow()
for d in (130, 129, 128, 127, 126):                                 # five days wholly older than 90
    base = (now - datetime.timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(48):
        t = base + datetime.timedelta(minutes=30 * i)
        down = d == 130 and 10 <= i <= 13                           # 4 reads down in a row on the first day: one outage, 120 minutes
        warn = d == 129 and i in (5, 20)                            # two single reads over the warning line
        one = d == 128 and i == 7                                   # one read down: not an outage
        mid = (d == 127 and i == 47) or (d == 126 and i in (0, 1))  # down at 23:30 and 00:00, 00:30: ONE outage across midnight, counted on the second day
        st = "Down" if (down or one or mid) else "Warning" if warn else "Up"
        ndb.write(c, t, 100 + i, True, [{"site": "SITEA", "nodes": 10, "down": 0, "warning": 0, "unknown": 0, "status": "Up"}],
                  [{"key": "L1", "site_a": "SITEA", "site_b": "SITEB", "node_a": "a1", "node_b": "b1", "status": st, "in": None if st == "Down" else 40, "out": None if st == "Down" else 10, "speed": "1 Gb/s"}])
        c.commit()
# a day row far older than 400 days, to be pruned
c.cursor().execute("INSERT INTO netmap_link_day (day, link_key, site_a, site_b) VALUES (%s, 'OLD', 'X', 'Y')", ((now - datetime.timedelta(days=500)).date(),))
c.commit()
EOPY
is "240 old reads written" "$(qw "SELECT COUNT(*) FROM netmap_read WHERE read_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY)")" 240
run "a run rolls the old days up and prunes" ok "{$base, \"awx_job_id\": 5004}" "5 day(s) rolled up"
is "the old reads are gone" "$(qw "SELECT COUNT(*) FROM netmap_read WHERE read_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 100 DAY)")" 0
is "their site and link reads too" "$(qw "SELECT COUNT(*) FROM netmap_link WHERE link_key = 'L1'")" 0
is "five link day rows" "$(qw "SELECT COUNT(*) FROM netmap_link_day WHERE link_key = 'L1'")" 5
is "across midnight: one outage, on the second day only" "$(qw "SELECT GROUP_CONCAT(outages ORDER BY day) FROM netmap_link_day WHERE link_key = 'L1'")" "1,0,0,0,1"
is "across midnight: the first day ends in a streak of 1, down 30 minutes" "$(qw "SELECT CONCAT(tail_down, '/', down_min) FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1 OFFSET 3")" "1/30"
is "across midnight: the second day 2 reads down, 60 minutes, one outage" "$(qw "SELECT CONCAT(down_reads, '/', down_min, '/', outages) FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1 OFFSET 4")" "2/60/1"
is "day 1: 48 reads, 4 down, one outage, 120 minutes" "$(qw "SELECT CONCAT(n_reads, '/', down_reads, '/', outages, '/', down_min) FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1")" "48/4/1/120"
is "day 2: 2 warning reads, no outage" "$(qw "SELECT CONCAT(warn_reads, '/', outages, '/', in_max, '/', out_max) FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1 OFFSET 1")" "2/0/40/10"
is "day 3: one read down is no outage" "$(qw "SELECT CONCAT(down_reads, '/', outages, '/', down_min) FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1 OFFSET 2")" "1/0/30"
is "a site day row" "$(qw "SELECT CONCAT(n_reads, '/', down_reads) FROM netmap_site_day WHERE site = 'SITEA' ORDER BY day LIMIT 1")" "48/0"
is "the recent reads stay" "$(qw "SELECT COUNT(*) FROM netmap_read WHERE read_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)")" 4
is "the 500-day-old day row is deleted" "$(qw "SELECT COUNT(*) FROM netmap_link_day WHERE link_key = 'OLD'")" 0
run "the next run rolls nothing more" ok "{$base, \"awx_job_id\": 5005}" "!day(s) rolled up"
is "still five link day rows" "$(qw "SELECT COUNT(*) FROM netmap_link_day WHERE link_key = 'L1'")" 5
# a late read for a day rolled up already is added to its row, never over it
"$py" - "$dbport" "$wpw" "$repo" <<'EOPY'
import datetime, os, sys
sys.path[:0] = [os.path.join(sys.argv[3], "roles", "netmap_report", "library"), os.path.join(sys.argv[3], "plugins", "module_utils")]
import pymysql, site_report_db, ansible.module_utils
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db); ansible.module_utils.site_report_db = site_report_db
import site_netmap_db as ndb
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="report_writer", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=False)
t = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=129)).replace(hour=12, minute=15, second=0, microsecond=0)
ndb.write(c, t, 999, True, [{"site": "SITEA", "nodes": 10, "down": 0, "warning": 0, "unknown": 0, "status": "Up"}],
          [{"key": "L1", "site_a": "SITEA", "site_b": "SITEB", "node_a": "a1", "node_b": "b1", "status": "Up", "in": 70, "out": 20, "speed": "1 Gb/s"}])
c.commit()
EOPY
run "a late read of a rolled day: rolled again, merged" ok "{$base, \"awx_job_id\": 5006}" "1 day(s) rolled up"
is "the day has 49 reads (48 and the late one), the busiest in 70 (merged, not overwritten)" "$(qw "SELECT CONCAT(n_reads, '/', in_max) FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1 OFFSET 1")" "49/70"
is "its warning reads are still 2" "$(qw "SELECT warn_reads FROM netmap_link_day WHERE link_key = 'L1' ORDER BY day LIMIT 1 OFFSET 1")" 2
# a daily schedule: two days of one read each, both down; the first day is rolled and deleted before the second - the usual gap (a day) is kept in the day row
"$py" - "$dbport" "$wpw" "$repo" <<'EOPY'
import datetime, os, sys
sys.path[:0] = [os.path.join(sys.argv[3], "roles", "netmap_report", "library"), os.path.join(sys.argv[3], "plugins", "module_utils")]
import pymysql, site_report_db, ansible.module_utils
sys.modules.setdefault("ansible.module_utils.site_report_db", site_report_db); ansible.module_utils.site_report_db = site_report_db
import site_netmap_db as ndb
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="report_writer", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=False)
now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
for d in (150, 149):
    t = (now - datetime.timedelta(days=d)).replace(hour=6, minute=0, second=0, microsecond=0)
    ndb.write(c, t, 300 + d, True, [], [{"key": "L2", "site_a": "SITEA", "site_b": "SITEB", "node_a": "a1", "node_b": "b1", "status": "Down", "in": None, "out": None, "speed": ""}])
    c.commit()
EOPY
run "a daily schedule rolled up" ok "{$base, \"awx_job_id\": 5007}" "2 day(s) rolled up"
is "daily: the outage is counted on the second day, 18 hours down each (06:00 to midnight)" "$(qw "SELECT GROUP_CONCAT(CONCAT(outages, '/', down_min, '/', gap_min) ORDER BY day) FROM netmap_link_day WHERE link_key = 'L2'")" "0/1080/1440.0,1/1080/1440.0"
# 7. never a failed job
DB_ENV="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=wrong-password" \
    run "a wrong password: one warning, the report whole, green" ok "{$base}" "Report database: not kept" "Network map: " "!wrong-password"
DB_ENV="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_reader REPORT_DB_PASSWORD=$rpw" \
    run "a read-only account: one warning, green" ok "{$base}" "Report database: not kept" "Network map: "
DB_ENV="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$((dbport + 9)) REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$wpw" \
    run "the server not reached: one warning, green" ok "{$base, \"netmap_db_timeout\": 3}" "Report database: not kept" "Network map: "
DB_ENV="" run "no credential: nothing written, nothing said" ok "{$base}" "!Report database:"
reads_before="$(qw "SELECT COUNT(*) FROM netmap_read")"
"$py" - "$dbport" "$root" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=True)
cur = c.cursor()
for t in ("netmap_link", "netmap_site", "netmap_read", "netmap_link_day", "netmap_site_day"):
    cur.execute("DROP TABLE " + t)
EOPY
run "netmap_db_create false and no tables: said, nothing made, green" ok "{$base, \"netmap_db_create\": false}" "Report database: not kept" "are not there (netmap_db_create: false)"
is "no table was made" "$(qw "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name LIKE 'netmap\\_%'")" 0
run "the tables made again by the next run" ok "{$base}" "tables made: netmap_read"
is "a read again" "$(qw "SELECT COUNT(*) FROM netmap_read")" 1

echo
[ "$fails" = 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
[ "$fails" = 0 ]
