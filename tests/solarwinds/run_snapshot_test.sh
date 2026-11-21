#!/usr/bin/env bash
# SolarWinds - snapshot (1.96.0, playbooks/solarwinds_snapshot.yml) against a fake SolarWinds (tests/netmap/fake_swis.py over
# tests/solarwinds/fixture_more.py: ~1500 nodes, links, alerts, events, SAM, volumes, VIM) and a real report database set
# up as docs/REPORT_DATABASE.md says (site_reports, report_writer and report_reader with their grants, plain TCP): MySQL 8.4
# and MariaDB 10.11, one after the other (SNAPSHOT_DB_TEST_IMAGES).
# The first run: the tables made, one whole snapshot, every table filled; only SWQL SELECTs, no secret column, no person's
# name asked for. The second: the events read from the newest kept on, none twice. keep_snapshots: the older snapshots'
# rows deleted. The reader (site_swis_db.answer) gives back exactly the dict site_swis.collect() gives. A module this Orion
# does not have (no SAM, no VIM): said, green. Skipped properties are not kept. Red, with a row saying so: SolarWinds not
# reached (the password never printed). Red: the database not reached, a read-only account, no credential, no tables with
# solarwinds_db_create false. Another run holding the lock: nothing written, green. A dry run: nothing written.
# The network map reads the snapshot (even with no SolarWinds login), and SolarWinds itself when the snapshot is too old,
# when the last snapshot run failed, or when the database is not reached - each said.
# Containers by name, removed by name; ports from variables. Skipped (exit 0) without a container runtime, an image or PyMySQL.
#   bash tests/solarwinds/run_snapshot_test.sh   (ANSIBLE_PLAYBOOK=..., SNAPSHOT_DB_TEST_IMAGES="...", SNAPSHOT_DB_TEST_RUNTIME=docker,
#   SNAPSHOT_DB_TEST_PORT=18560: the database; the fakes on the ports after it; SNAPSHOT_DB_TEST_NAME=..., SNAPSHOT_DB_TEST_PYTHON=...)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SNAPSHOT_DB_TEST_PYTHON:-$(command -v python3)}"
images="${SNAPSHOT_DB_TEST_IMAGES:-docker.io/library/mysql:8.4 docker.io/library/mariadb:10.11}"
dbport="${SNAPSHOT_DB_TEST_PORT:-18560}"
port=$((dbport + 1))                     # +0 SWIS, +1 SWIS without SAM and VIM, +2 nothing listening, +3 fake AAP
name="${SNAPSHOT_DB_TEST_NAME:-sa-swis-snapshot-test}"
rt=${SNAPSHOT_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py (pip install PyMySQL cryptography)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
root="Test-Only-root-$$"
wpw="Test-Only-writer-$$"
rpw="Test-Only-reader-$$"
pw='ro&"<test>9'
pids=()
holder=""
cleanup() { for p in "${pids[@]}" $holder; do kill "$p" 2>/dev/null; done; "${rtc[@]}" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
"$py" "$repo/tests/netmap/fixture.py" "$work/fx0.json" 1500 svc_swis_ro "$pw" > /dev/null
"$py" "$here/fixture_more.py" "$work/fx0.json" "$work/fx.json" > /dev/null
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Test CA" -keyout "$work/ca.key" -out "$work/ca.pem" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout "$work/srv.key" -out "$work/srv.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n' > "$work/ext.cnf"
openssl x509 -req -in "$work/srv.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial -days 2 -out "$work/srv.pem" -extfile "$work/ext.cnf" 2>/dev/null
wait_port() { for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
FAKE_SWIS_ECHO=1 "$py" "$repo/tests/netmap/fake_swis.py" "$port" "$work/fx.json" "$work/swis.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
FAKE_SWIS_HIDE=Orion.APM.Application,Orion.APM.Component,Orion.VIM.Hosts,Orion.VIM.VirtualMachines,Orion.VIM.Clusters \
    "$py" "$repo/tests/netmap/fake_swis.py" $((port + 1)) "$work/fx.json" "$work/swis2.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
echo '[]' > "$work/jobs.json"
"$py" "$repo/tests/email/fake_aap.py" $((port + 3)) "$work/jobs.json" 2>"$work/aap.err" & pids+=($!)
FAKE_SWIS_HIDE=Metadata.Property "$py" "$repo/tests/netmap/fake_swis.py" $((port + 4)) "$work/fx.json" "$work/swis4.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 3)); wait_port $((port + 4))
SW="SOLARWINDS_URL=https://127.0.0.1:$port SOLARWINDS_USERNAME=svc_swis_ro SOLARWINDS_CA_PATH=$work/ca.pem"
AAP="CONTROLLER_HOST=http://127.0.0.1:$((port + 3)) CONTROLLER_USERNAME=audit CONTROLLER_PASSWORD=pw"

# run NAME EXPECT(ok|fail) PLAYBOOK [ansible-playbook args...] -- TEXT...   (!TEXT: must not be there; DB_ENV, SW_ENV: the credentials)
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && env -u REPORT_DB_HOST -u SOLARWINDS_URL -u SOLARWINDS_USERNAME -u SOLARWINDS_PASSWORD SOLARWINDS_PASSWORD="$pw" \
        ${SW_ENV-$SW} ${DB_ENV:-} $AAP ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True "$ap" -i "$work/inv.yml" "playbooks/$pb.yml" "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$? t
    tr -s ' \n' '  ' < "$work/out.txt" | sed 's/\\n/ /g; s/\\"/"/g' > "$work/flat.txt"
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -5 | cut -c1-400; return 1
    fi
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            grep -qF -- "${t:1}" "$work/flat.txt" && { bad "$tname: has: ${t:1}"; return 1; }
        else
            grep -qF -- "$t" "$work/flat.txt" || { bad "$tname: lacks: $t"; grep -E '"msg"|WARNING|fatal' "$work/out.txt" | head -6 | cut -c1-400; return 1; }
        fi
    done
    ok "$tname"
}
# q USER PASSWORD SQL: the first row's first value
q() { "$py" - "$dbport" "$1" "$2" "$3" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user=sys.argv[2], password=sys.argv[3], database="site_reports",
                    ssl_disabled=True, autocommit=True)
cur = c.cursor(); cur.execute(sys.argv[4])
r = cur.fetchone()
print("" if r is None else r[0])
EOPY
}
qr() { q report_reader "$rpw" "$1"; }
fmt() { "$py" -c 'import sys; print("{:,}".format(int(sys.argv[1])))' "$1"; }
reads_only() {
    local f="$1"
    if grep -qv '^POST /SolarWinds/InformationService/v3/Json/Query ' "$f"; then bad "  ...a call that is not a Query"; return 1; fi
    if grep -q 'SECRET-COLUMN-ASKED' "$f"; then bad "  ...a secret column or SELECT * was asked for"; return 1; fi
    if grep -q 'AcknowledgedBy\|AcknowledgedNote' "$f"; then bad "  ...a person's name was asked for (AcknowledgedBy)"; return 1; fi
    ok "  ...only SWQL SELECTs on the Query endpoint, no secret column, no person's name ($(wc -l < "$f") queries)"
}

one_db() {
    local image="$1" extra=()
    "${rtc[@]}" image exists "$image" 2>/dev/null || "${rtc[@]}" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; return 0; }
    case "$image" in *mysql*) extra=(--tls-version=) ;; esac
    echo "---- $image"
    "${rtc[@]}" rm -f "$name" >/dev/null 2>&1
    for _ in $(seq 1 60); do (exec 3<>"/dev/tcp/127.0.0.1/$dbport") 2>/dev/null || break; sleep 0.5; done     # the last one's port freed
    "${rtc[@]}" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
        "${extra[@]}" >/dev/null || { bad "$image did not start"; return 1; }
    "$py" - "$dbport" "$root" "$wpw" "$rpw" <<'EOPY' || { bad "$image not ready"; "${rtc[@]}" logs "$name" 2>&1 | tail -5; return 1; }
import sys, time, pymysql
for _ in range(180):
    try:
        c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], ssl_disabled=True, autocommit=True)
        c.cursor().execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'site_reports'")
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
    local DBW="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$wpw"
    local DBR="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_reader REPORT_DB_PASSWORD=$rpw"
    local nodes; nodes="$("$py" -c "import json,sys; print(len(json.load(open(sys.argv[1]))['nodes']))" "$work/fx.json")"

    # ---- no credential, no tables made by hand
    DB_ENV= run "no Report database credential: red, says which, nothing read" fail solarwinds_snapshot -- 'attach a "Report database" credential'
    DB_ENV="$DBW" run "no tables and solarwinds_db_create: false: red, says where the statements are" fail solarwinds_snapshot \
        -e solarwinds_db_create=false -- "The SolarWinds tables are not in 127.0.0.1:$dbport/site_reports (solarwinds_db_create: false)"
    DB_ENV="$DBW" run "a dry run before the first: no tables made" fail solarwinds_snapshot --check -- "(check mode: nothing made)"
    [ "$(qr "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name LIKE 'sw%'")" = 0 ] \
        && ok "  ...no table made by either" || bad "  ...tables were made"

    # ---- the first run
    : > "$work/swis.log"
    DB_ENV="$DBW" run "the first run: the tables made, one whole snapshot" ok solarwinds_snapshot -e '{"awx_job_id": 7001}' \
        -- "Snapshot 1 kept." "- $(fmt "$nodes") nodes (" "active alerts" "new events" "SAM applications (2 down)" "VIM hosts" \
           "The SolarWinds tables were made in 127.0.0.1:$dbport/site_reports" "SWQL queries from SolarWinds 2025.4.1" "!$wpw" "!$pw" "!Not read:"
    reads_only "$work/swis.log"
    [ "$(qr "SELECT CONCAT(ok, '/', nodes, '/', job) FROM sw_snapshot WHERE snap_id = 1")" = "1/$nodes/7001" ] \
        && [ "$(qr "SELECT COUNT(*) FROM sw_node WHERE snap_id = 1")" = "$nodes" ] \
        && [ "$(qr "SELECT version FROM schema_version WHERE component = 'solarwinds'")" = 1 ] \
        && ok "  ...sw_snapshot ok, $nodes nodes, the job's ID, schema_version solarwinds 1; the reader account reads them" \
        || bad "  ...the first snapshot: $(qr "SELECT CONCAT(ok, '/', nodes, '/', job) FROM sw_snapshot")"
    local t empty=""
    for t in sw_link sw_interface sw_alert_active sw_event sw_app sw_component sw_volume sw_vim_host sw_vim_vm; do
        [ "$(qr "SELECT COUNT(*) FROM $t")" -gt 0 ] || empty="$empty $t"
    done
    [ -z "$empty" ] && ok "  ...every table has rows" || bad "  ...empty:$empty"
    local got
    got="$(qr "SELECT CONCAT(COUNT(*), '/', MIN(severity)) FROM sw_alert_active WHERE name = 'Node is down' AND node_caption = 'esx02'")"
    [ "$got" = 1/2 ] && ok "  ...an active alert with its name, severity and node" || bad "  ...the alert on esx02: $got"
    got="$(qr "SELECT CONCAT(cluster, '/', connection_state) FROM sw_vim_host WHERE name_key = 'esx02'")"
    [ "$got" = cluster1/notResponding ] && ok "  ...a VIM host with its cluster and state" || bad "  ...the VIM host esx02: $got"
    got="$(qr "SELECT COUNT(*) FROM sw_interface WHERE in_errors_hour > 0")"
    [ "$got" -gt 0 ] && ok "  ...interface errors ($got interfaces)" || bad "  ...no interface errors"
    got="$(qr "SELECT COUNT(*) FROM sw_node WHERE snap_id = 1 AND ips LIKE '%198.51.100.%'")"
    [ "$got" = 10 ] && ok "  ...the nodes' other IPs (10 nodes)" || bad "  ...nodes with another IP: $got"
    got="$(qr "SELECT props FROM sw_node WHERE name_key = 'esx01'")"
    case "$got" in *'"Region": "Region 1"'*) ok "  ...custom properties as JSON" ;; *) bad "  ...esx01's properties: $got" ;; esac
    [ "$(qr "SELECT COUNT(*) FROM sw_event")" -gt 100 ] && [ "$(qr "SELECT COUNT(*) FROM sw_event WHERE event_time < UTC_TIMESTAMP() - INTERVAL 25 HOUR")" = 0 ] \
        && ok "  ...the first run's events: the last 24 hours (solarwinds_event_hours)" || bad "  ...events: $(qr "SELECT COUNT(*) FROM sw_event")"

    # ---- the second run: the events from the newest kept on
    local ev; ev="$(qr "SELECT COUNT(*) FROM sw_event")"
    : > "$work/swis.log"
    DB_ENV="$DBW" run "the second run: snapshot 2, the events read from the newest kept on" ok solarwinds_snapshot -- "Snapshot 2 kept." "- 0 new events"
    grep -q "Orion.Events.*EventID" "$work/swis.log" && [ "$(qr "SELECT COUNT(*) FROM sw_event")" = "$ev" ] \
        && ok "  ...no event twice ($ev)" || bad "  ...events: $ev -> $(qr "SELECT COUNT(*) FROM sw_event")"

    # ---- the reader gives back what collect() gives
    "$py" - "$repo" "$dbport" "$rpw" "$port" "$pw" "$work/ca.pem" <<'EOPY' && ok "site_swis_db.answer() gives exactly what site_swis.collect() gives (5 sets of settings), plus snapshot_at, age_seconds and source" || bad "the reader's dict is not collect()'s"
import sys, pymysql
repo, dbport, rpw, port, pw, ca = sys.argv[1:7]
sys.path.insert(0, repo + "/plugins/module_utils")
import site_swis, site_swis_db
conn = pymysql.connect(host="127.0.0.1", port=int(dbport), user="report_reader", password=rpw, database="site_reports", ssl_disabled=True)
bad = 0
for o in ({}, {"site_by": "Site", "lat_property": "Lat", "lon_property": "Lon", "properties": ["Region"]},
          {"properties": ["Nope", "bad name"], "max_nodes": 300}, {"site_by": "Location", "properties": ["Region", "Site"], "page": 200},
          {"discover": False, "properties": ["Region"]}):
    c = site_swis.Client("https://127.0.0.1:%s" % port, "svc_swis_ro", pw, ca_path=ca)
    live = site_swis.collect(c, o)
    db = site_swis_db.answer(o, conn=conn, max_age=3600)
    extra = dict((k, db.pop(k, None)) for k in ("snapshot_at", "age_seconds", "source"))
    for k in ("read_at", "seconds", "queries"):     # the snapshot's own read: when, how long, how many queries
        live.pop(k), db.pop(k)
    for r in (live, db):                            # a custom property that is not there: the same line, in the reader's words
        r["not_read"] = [dict(x, why="") if x["what"].startswith("custom property ") else x for x in r["not_read"]]
    if live != db or extra["source"] != "db-snapshot" or not extra["snapshot_at"]:
        bad += 1
        for k in sorted(set(live) | set(db)):
            if live.get(k) != db.get(k):
                print("    differs, %s: %s | %s" % (k, str(live.get(k))[:160], str(db.get(k))[:160]))
sys.exit(1 if bad else 0)
EOPY

    # ---- keep_snapshots
    DB_ENV="$DBW" run "solarwinds_db_keep_snapshots: 2 - the older snapshots' rows deleted" ok solarwinds_snapshot -e solarwinds_db_keep_snapshots=2 \
        -- "Snapshot 3 kept." "Deleted: " "rows of older snapshots"
    [ "$(qr "SELECT COUNT(DISTINCT snap_id) FROM sw_node")" = 2 ] && [ "$(qr "SELECT MIN(snap_id) FROM sw_interface")" = 2 ] \
        && [ "$(qr "SELECT COUNT(*) FROM sw_snapshot")" = 3 ] && ok "  ...snapshots 2 and 3 have rows, the three snapshot rows stay" \
        || bad "  ...snapshots with rows: $(qr "SELECT COUNT(DISTINCT snap_id) FROM sw_node")"
    "$py" - "$repo" "$dbport" "$rpw" <<'EOPY' && ok "  ...apps_for(): an application's components not up with value and message, \"2 of 9\"; nodes_for_all() by name, ip and other ip" || bad "  ...apps_for()"
import sys, pymysql
repo, dbport, rpw = sys.argv[1:4]
sys.path.insert(0, repo + "/plugins/module_utils")
import site_swis_db
conn = pymysql.connect(host="127.0.0.1", port=int(dbport), user="report_reader", password=rpw, database="site_reports", ssl_disabled=True)
snap = site_swis_db.latest(conn)
cur = conn.cursor()
cur.execute("SELECT node_id FROM sw_app WHERE snap_id = %s AND app_id = 309", (snap["snap_id"],))
node = cur.fetchone()[0]
apps = site_swis_db.apps_for(conn, snap["snap_id"], [node, 999999])
web = [a for a in apps[node] if a["app_id"] == 309][0]
got = (web["status"], web["components"], [(c["name"], c["status"], c["value"], c["message"]) for c in web["components_not_up"]], apps[999999],
       site_swis_db.part_note(snap, "app"))
want = ("Down", 9, [("Component 1", "Down", 0, "Port 443 refused the connection"), ("Component 5", "Down", 40, "Port 443 refused the connection")], [], None)
if got != want:
    print("    %r" % (got,))
    sys.exit(1)
cur.execute("SELECT name, ip FROM sw_node WHERE snap_id = %s AND ips LIKE '%%,%%' ORDER BY node_id LIMIT 1", (snap["snap_id"],))
name, ip = cur.fetchone()
cur.execute("SELECT ips FROM sw_node WHERE snap_id = %s AND name = %s", (snap["snap_id"], name))
other = [x for x in __import__("json").loads(cur.fetchone()[0]) if x != ip][0]
every = site_swis_db.nodes_for_all(conn, snap["snap_id"], [name, ip, other, "nobody-here"])
one = site_swis_db.nodes_for(conn, snap["snap_id"], [name])[name]
seen = ([r["matched_by"] for r in every[name]], [r["matched_by"] for r in every[ip]], [r["matched_by"] for r in every[other]],
        every["nobody-here"], every[name][0]["node_id"] == one["node_id"])
if seen != (["name"], ["ip"], ["other ip"], [], True):
    print("    nodes_for_all: %r" % (seen,))
    sys.exit(1)
EOPY
    "$py" - "$repo" "$dbport" "$rpw" "$port" "$pw" "$work/ca.pem" <<'EOPY' && ok "  ...1.96.1: apps_live() and alerts_live() (SolarWinds itself) give exactly what apps_for() and alerts_for() give" || bad "  ...the live readers differ"
import sys, pymysql
repo, dbport, rpw, port, pw, ca = sys.argv[1:7]
sys.path.insert(0, repo + "/plugins/module_utils")
import site_swis_db
sw = site_swis_db.site_swis
conn = pymysql.connect(host="127.0.0.1", port=int(dbport), user="report_reader", password=rpw, database="site_reports", ssl_disabled=True)
snap = site_swis_db.latest(conn)
cur = conn.cursor()
cur.execute("SELECT node_id FROM sw_node WHERE snap_id = %s", (snap["snap_id"],))
ids = [r[0] for r in cur.fetchall()]
c = sw.Client("https://127.0.0.1:%s" % port, "svc_swis_ro", pw, ca_path=ca)
bad = 0
for name, db, live in (("apps", site_swis_db.apps_for(conn, snap["snap_id"], ids), site_swis_db.apps_live(c, ids)),
                       ("alerts", site_swis_db.alerts_for(conn, snap["snap_id"], ids), site_swis_db.alerts_live(c, ids))):
    if db != live or not any(db.values()):
        bad += 1
        for k in sorted(set(db) | set(live)):
            if db.get(k) != live.get(k):
                print("    %s differs, node %s: %s | %s" % (name, k, str(db.get(k))[:200], str(live.get(k))[:200]))
                break
sys.exit(1 if bad else 0)
EOPY

    # ---- a module this Orion does not have; skipped properties
    SW_ENV="${SW/:$port /:$((port + 1)) }" DB_ENV="$DBW" run "no SAM, no VIM: said once, the rest kept, green" ok solarwinds_snapshot \
        -e '{"solarwinds_snapshot_skip_properties": ["Region"]}' \
        -- "Snapshot 4 kept." "Not on this Orion (a module it does not have):" "- SAM's applications (Orion.APM.Application)" \
           "- VIM's hosts and VMs" "!SAM applications (" "!Not read:"
    [ "$(qr "SELECT COALESCE(apps, 'none') FROM sw_snapshot WHERE snap_id = 4")" = none ] && [ "$(qr "SELECT COUNT(*) FROM sw_app WHERE snap_id = 4")" = 0 ] \
        && [ "$(qr "SELECT COUNT(*) FROM sw_node WHERE snap_id = 4 AND props LIKE '%Region%'")" = 0 ] \
        && ok "  ...apps NULL (not read, not 0), no Region kept (solarwinds_snapshot_skip_properties)" || bad "  ...snapshot 4"
    "$py" - "$repo" "$dbport" "$rpw" <<'EOPY' && ok "  ...apps_for() gives none, part_note(snapshot, \"app\") says no SAM" || bad "  ...apps_for() without SAM"
import sys, pymysql
repo, dbport, rpw = sys.argv[1:4]
sys.path.insert(0, repo + "/plugins/module_utils")
import site_swis_db
conn = pymysql.connect(host="127.0.0.1", port=int(dbport), user="report_reader", password=rpw, database="site_reports", ssl_disabled=True)
snap = site_swis_db.latest(conn)
cur = conn.cursor()
cur.execute("SELECT MIN(node_id) FROM sw_node WHERE snap_id = %s", (snap["snap_id"],))
node = cur.fetchone()[0]
note = site_swis_db.part_note(snap, "app") or {}
if snap["snap_id"] != 4 or site_swis_db.apps_for(conn, 4, [node]) != {node: []} or not note.get("absent"):
    print("    %r %r" % (snap["snap_id"], note))
    sys.exit(1)
EOPY
    # 1.96.1 (review P1-2): another Orion (port + 1) whose EventIDs are the first one's - its own cursor: all read, kept apart
    one="127.0.0.1:$port"; two="127.0.0.1:$((port + 1))"
    n1="$(qr "SELECT COUNT(*) FROM sw_event WHERE orion = '$one'")"; n2="$(qr "SELECT COUNT(*) FROM sw_event WHERE orion = '$two'")"
    nb="$("$py" - "$repo" "$dbport" "$rpw" <<'EOPY'
import sys, datetime, pymysql
sys.path.insert(0, sys.argv[1] + "/plugins/module_utils")
import site_swis_db
conn = pymysql.connect(host="127.0.0.1", port=int(sys.argv[2]), user="report_reader", password=sys.argv[3], database="site_reports", ssl_disabled=True)
print(len(site_swis_db.events_between(conn, datetime.datetime(2000, 1, 1), datetime.datetime(2100, 1, 1), limit=100000)))
EOPY
)"
    [ "$n2" -gt 0 ] && [ "$n2" -ge "$((n1 - 5))" ] && [ "$nb" = "$n2" ] \
        && ok "  ...1.96.1: another Orion's events with the same EventIDs: all read ($n2), kept apart from the first's ($n1); events_between gives the newest snapshot's Orion's only" \
        || bad "  ...events of two Orions: $n1 / $n2, events_between $nb"

    # ---- red: SolarWinds not reached; the reports keep the last whole snapshot
    SW_ENV="${SW/:$port /:$((port + 2)) }" DB_ENV="$DBW" run "SolarWinds not reached: red, a row says so, the password never printed" fail solarwinds_snapshot \
        -e solarwinds_timeout=3 -- "SolarWinds could not be read:" "the reports go on with the last whole snapshot" "!$pw" "!$wpw"
    [ "$(qr "SELECT CONCAT(ok, '/', error IS NOT NULL) FROM sw_snapshot WHERE snap_id = 5")" = "0/1" ] \
        && [ "$(qr "SELECT COUNT(*) FROM sw_node WHERE snap_id = 5")" = 0 ] && ok "  ...snapshot 5: ok 0 with its reason, no rows" || bad "  ...snapshot 5"

    # ---- red: the database
    DB_ENV="${DBW/PORT=$dbport/PORT=$((port + 2))}" run "the database not reached: red, says where" fail solarwinds_snapshot -e solarwinds_db_timeout=3 \
        -- "The report database 127.0.0.1:$((port + 2))/site_reports could not be reached" "!$wpw"
    DB_ENV="$DBR" run "a read-only account: red, nothing of this run kept" fail solarwinds_snapshot \
        -- "this snapshot was not kept" "command denied" "!$rpw"
    [ "$(qr "SELECT MAX(snap_id) FROM sw_snapshot")" = 5 ] && ok "  ...no snapshot row added" || bad "  ...rows added"

    # ---- the lock, a dry run
    "$py" - "$dbport" "$wpw" <<'EOPY' & holder=$!
import sys, time, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="report_writer", password=sys.argv[2], database="site_reports", ssl_disabled=True)
c.cursor().execute("SELECT GET_LOCK('site_reports.sw_snapshot', 0)")
time.sleep(90)
EOPY
    sleep 2
    DB_ENV="$DBW" run "another run holds the lock: nothing written, said, green" ok solarwinds_snapshot \
        -- "Another SolarWinds snapshot run is writing (the database lock site_reports.sw_snapshot is held)" "this run kept nothing"
    # 1.96.1 (review P2-9): the lock held and the newest whole snapshot older than solarwinds_db_max_age - a run may be stuck: red
    q report_writer "$wpw" "UPDATE sw_snapshot SET read_at = read_at - INTERVAL 2 HOUR WHERE snap_id = 4" >/dev/null
    DB_ENV="$DBW" run "the lock held and the newest snapshot 2 hours old: red" fail solarwinds_snapshot \
        -- "Another SolarWinds snapshot run holds the database lock (site_reports.sw_snapshot)" "the newest whole snapshot is 12" "is a run stuck?"
    q report_writer "$wpw" "UPDATE sw_snapshot SET read_at = read_at + INTERVAL 2 HOUR WHERE snap_id = 4" >/dev/null
    kill $holder 2>/dev/null; wait $holder 2>/dev/null; holder=""
    DB_ENV="$DBW" run "a dry run (Check): SolarWinds read, nothing written" ok solarwinds_snapshot --check -- "Check: SolarWinds read, nothing written." "nodes ("
    [ "$(qr "SELECT MAX(snap_id) FROM sw_snapshot")" = 5 ] && ok "  ...no snapshot row added by either" || bad "  ...rows added"

    # ---- the network map reads the snapshot
    # 1.96.1 (review P1-3): Metadata.Property refused - not a whole snapshot (the sites and points would be lost): red, ok 0
    SW_ENV="${SW/:$port /:$((port + 4)) }" DB_ENV="$DBW" run "Metadata.Property refused: red, a row with ok 0, the reports keep the last whole one" fail \
        solarwinds_snapshot -- "SolarWinds could not be read: which columns this Orion has could not be read (Metadata.Property)"
    [ "$(qr "SELECT CONCAT(ok, '/', error LIKE '%Metadata.Property%') FROM sw_snapshot WHERE snap_id = 6")" = "0/1" ] \
        && [ "$(qr "SELECT COUNT(*) FROM sw_node WHERE snap_id = 6")" = 0 ] && ok "  ...snapshot 6: ok 0, its reason, no rows" || bad "  ...snapshot 6"
    DB_ENV="$DBW" run "a whole snapshot again" ok solarwinds_snapshot -- "Snapshot 7 kept."
    : > "$work/swis.log"
    SW_ENV= DB_ENV="$DBR" run "the network map from the snapshot, with no SolarWinds login at all" ok netmap_report \
        -e '{"netmap_lat_property": "Lat", "netmap_lon_property": "Lon", "netmap_properties": ["Region"], "netmap_site_by": "Location"}' \
        -- "From the report database: the SolarWinds snapshot of" " UTC (" "min old)." "SolarWinds 2025.4.1 at https://127.0.0.1:$port:" \
           "Site A:" "!From SolarWinds itself" "!Z ("
    [ ! -s "$work/swis.log" ] && ok "  ...SolarWinds not asked" || bad "  ...SolarWinds was asked $(wc -l < "$work/swis.log") times"
    # 1.96.1 (review P1-2): a template reading another SolarWinds is never given this one's snapshot
    SW_ENV="${SW/:$port /:$((port + 1)) }" DB_ENV="$DBR" run "a template of another SolarWinds: SolarWinds itself, said" ok netmap_report \
        -e "{\"netmap_ca_path\": \"$work/ca.pem\"}" -- "From SolarWinds itself: the newest SolarWinds snapshot is of 127.0.0.1:$port, not 127.0.0.1:$((port + 1))"
    q report_writer "$wpw" "UPDATE sw_snapshot SET read_at = read_at - INTERVAL 2 HOUR WHERE snap_id = 7" >/dev/null
    DB_ENV="$DBR" run "a snapshot older than solarwinds_db_max_age: SolarWinds itself, said" ok netmap_report \
        -e "{\"netmap_ca_path\": \"$work/ca.pem\"}" -- "From SolarWinds itself: the newest SolarWinds snapshot is 12" "(solarwinds_db_max_age: 20 min)" \
           "SolarWinds 2025.4.1 at https://127.0.0.1:$port:"
    [ -s "$work/swis.log" ] && ok "  ...SolarWinds asked" || bad "  ...SolarWinds not asked"
    q report_writer "$wpw" "INSERT INTO sw_snapshot (read_at, ok, error) VALUES (UTC_TIMESTAMP(), 0, 'no answer in time')" >/dev/null
    DB_ENV="$DBR" run "and the last snapshot run failed: said with its reason" ok netmap_report -e "{\"netmap_ca_path\": \"$work/ca.pem\"}" \
        -- "the last snapshot run failed: no answer in time"
    DB_ENV="${DBR/PORT=$dbport/PORT=$((port + 2))}" run "the database not reached: SolarWinds itself, said, green" ok netmap_report \
        -e "{\"netmap_ca_path\": \"$work/ca.pem\"}" -- "From SolarWinds itself: the report database could not be reached" "!$rpw"
    DB_ENV="$DBR" run "netmap_from_db: false - SolarWinds itself" ok netmap_report -e "{\"netmap_ca_path\": \"$work/ca.pem\", \"netmap_from_db\": false}" \
        -- "From SolarWinds itself: the report database is not used"

    # ---- the statements of docs/REPORT_DATABASE.md, by hand
    "$py" - "$dbport" "$root" "$repo" <<'EOPY' && ok "the tables made by hand with the statements of docs/REPORT_DATABASE.md (site_swis_db.ddl())" || bad "the statements by hand"
import sys, pymysql
sys.path.insert(0, sys.argv[3] + "/plugins/module_utils")
import site_swis_db
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], ssl_disabled=True, autocommit=True)
cur = c.cursor()
cur.execute("CREATE DATABASE by_hand")
cur.execute("USE by_hand")
for sql in site_swis_db.ddl().split(";"):
    if sql.strip():
        cur.execute(sql)
cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'by_hand' AND table_name LIKE 'sw%'")
sys.exit(0 if cur.fetchone()[0] == len(site_swis_db.TABLES) else 1)
EOPY
    "${rtc[@]}" rm -f "$name" >/dev/null 2>&1
}

for image in $images; do one_db "$image"; done
if [ "$fails" -eq 0 ]; then echo "ok   - all SolarWinds snapshot scenarios passed"; else echo "$fails FAILED"; exit 1; fi
