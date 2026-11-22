#!/usr/bin/env bash
# SolarWinds in the health checks and troubleshoot (1.97.0, roles/solarwinds_report) against a
# real report database (MySQL 8.4 and MariaDB 10.11, one after the other: SWREPORT_DB_TEST_IMAGES) filled by the job
# "SolarWinds - snapshot" from a fake SolarWinds (tests/netmap/fake_swis.py over tests/solarwinds/fixture_hosts.py).
# The reports read with the reader account (report_reader: SELECT only), and Log Analyzer's syslog in one query.
# Health: SolarWinds says, the two disagree, Not in SolarWinds (solarwinds_report_skip_hosts), right under Findings, the
# tile, the syslog counted (a warning and an older error never). Without the SolarWinds credential, or without Log
# Analyzer: the syslog column says why. No Report database credential, or solarwinds_report: false: nothing. A wrong
# password, an old snapshot: one line under Not read, never counted, the job green. Troubleshoot: first on the page, the switch port, its
# events (another port's never). Only SWQL SELECTs.
# Containers by name, removed by name; ports from variables. Skipped (exit 0) without a container runtime, an image or PyMySQL.
#   bash tests/solarwinds/run_swreport_test.sh   (ANSIBLE_PLAYBOOK=..., SWREPORT_DB_TEST_IMAGES="...", SWREPORT_DB_TEST_PORT=18580:
#   the database, the fakes on the ports after it; SWREPORT_DB_TEST_NAME=..., SWREPORT_DB_TEST_PYTHON=..., SWREPORT_DB_TEST_RUNTIME=docker,
#   SWREPORT_TEST_LOGS=DIR: each run's whole output kept there)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SWREPORT_DB_TEST_PYTHON:-$(command -v python3)}"
images="${SWREPORT_DB_TEST_IMAGES:-docker.io/library/mysql:8.4 docker.io/library/mariadb:10.11}"
dbport="${SWREPORT_DB_TEST_PORT:-18580}"
port=$((dbport + 1))                     # +0 SWIS, +1 SWIS without Log Analyzer
name="${SWREPORT_DB_TEST_NAME:-sa-swreport-test}"
rt=${SWREPORT_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py (pip install PyMySQL cryptography)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
export MYSQL_ROOT_PASSWORD="Test-Only-root-$$"
export SWREPORT_WRITER_PASSWORD="Test-Only-writer-$$"
export SWREPORT_READER_PASSWORD="Test-Only-reader-$$"
export SWIS_FIXTURE_PASSWORD='ro&"<test>9'
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; "${rtc[@]}" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

{
    printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py"
    printf '  children:\n    servers:\n      hosts:\n'
    for h in web01 db01 app02 app03 lab07; do
        printf '        %s.example.mil: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$h" "$py"
    done
    # an IPv6 ansible_host (not in SolarWinds): the IPv6 part of nodes_for_hosts' SQL runs on both databases
    printf '        mon01.example.mil: {ansible_connection: local, ansible_host: "2001:db8:0::99", ansible_python_interpreter: %s}\n' "$py"
} > "$work/inv.yml"
cat > "$work/fs.json" <<'EOF'
{"fs": {"web01.example.mil": [{"id": "disk:/var", "check": "disk", "severity": "critical", "summary": "/var is 96 % full", "hint": "du -xh /var"},
                              {"id": "services:httpd", "check": "services", "severity": "warning", "summary": "httpd restarted 4 times", "hint": "journalctl -u httpd"}],
        "db01.example.mil": [{"id": "performance:iowait", "check": "performance", "severity": "warning", "summary": "I/O wait 31 %", "hint": "iostat -x 5 3"}]}}
EOF
{ "$py" "$repo/tests/netmap/fixture.py" "$work/fx0.json" 600 > /dev/null \
    && "$py" "$here/fixture_more.py" "$work/fx0.json" "$work/fx1.json" > /dev/null \
    && "$py" "$here/fixture_hosts.py" "$work/fx1.json" "$work/fx.json" > /dev/null; } || { echo "ABORT - the fixture"; exit 1; }
switch="$("$py" -c 'import json,sys; f=json.load(open(sys.argv[1])); print(next(n["Caption"] for n in f["nodes"] if n["NodeID"] == next(c["SrcNodeID"] for c in f["connections"] if c["SrcInterfaceID"] == 990001)))' "$work/fx.json")"
openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Test CA" -keyout "$work/ca.key" -out "$work/ca.pem" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign" 2>/dev/null
openssl req -newkey rsa:2048 -nodes -subj "/CN=127.0.0.1" -keyout "$work/srv.key" -out "$work/srv.csr" 2>/dev/null
printf 'subjectAltName=IP:127.0.0.1\nbasicConstraints=CA:FALSE\n' > "$work/ext.cnf"
openssl x509 -req -in "$work/srv.csr" -CA "$work/ca.pem" -CAkey "$work/ca.key" -CAcreateserial -days 2 -out "$work/srv.pem" -extfile "$work/ext.cnf" 2>/dev/null
wait_port() { for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
# the SolarWinds of the snapshot, on $port: fake_up [HIDE] starts it (again), this script's own process, stopped by its PID
swpid=""
fake_up() {
    if [ -n "$swpid" ]; then kill "$swpid" 2>/dev/null; wait "$swpid" 2>/dev/null; fi
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || break; sleep 0.1; done
    FAKE_SWIS_HIDE="${1:-}" FAKE_SWIS_ECHO=1 "$py" "$repo/tests/netmap/fake_swis.py" "$port" "$work/fx.json" "$work/swis.log" "$work/srv.pem" "$work/srv.key" &
    swpid=$!; pids+=($swpid)
    wait_port "$port"
}
fake_up
# another SolarWinds (another address): its snapshot is not this one's
"$py" "$repo/tests/netmap/fake_swis.py" $((port + 1)) "$work/fx.json" "$work/swis2.log" "$work/srv.pem" "$work/srv.key" & pids+=($!)
wait_port $((port + 1))
SW="SOLARWINDS_URL=https://127.0.0.1:$port SOLARWINDS_USERNAME=svc_swis_ro SOLARWINDS_CA_PATH=$work/ca.pem"

# run NAME EXPECT(ok|fail) PLAYBOOK [ansible-playbook args...] -- TEXT...   (!TEXT: must not be there; DB_ENV, SW_ENV: the credentials)
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -rf "$work/web"; mkdir -p "$work/web"
    (cd "$repo" || exit 1
     unset REPORT_DB_HOST SOLARWINDS_URL SOLARWINDS_USERNAME SOLARWINDS_PASSWORD
     export SOLARWINDS_PASSWORD="$SWIS_FIXTURE_PASSWORD"
     for setting in ${SW_ENV-$SW} ${DB_ENV:-}; do export "$setting"; done
     ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_STDOUT_CALLBACK=default "$ap" -i "$work/inv.yml" "$pb" "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$? t
    [ -n "${SWREPORT_TEST_LOGS:-}" ] && cp "$work/out.txt" "$SWREPORT_TEST_LOGS/$(echo "$tname" | tr -c 'a-z0-9' '_' | cut -c1-40).txt"
    tr -s ' \n' '  ' < "$work/out.txt" | sed 's/\\n/ /g; s/\\"/"/g' > "$work/flat.txt"
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$tname: exit $rc (wanted $want)"; grep -E 'ERROR|fatal|FAILED' "$work/out.txt" | head -5 | cut -c1-600; return 1
    fi
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            grep -qF -- "${t:1}" "$work/flat.txt" && { bad "$tname: has: ${t:1}"; return 1; }
        else
            grep -qF -- "$t" "$work/flat.txt" || { bad "$tname: lacks: $t"; grep -E '"msg"|WARNING|fatal|SolarWinds' "$work/out.txt" | head -8 | cut -c1-400; return 1; }
        fi
    done
    ok "$tname"
}
# page CHECK PYTHON: the report website's newest page (its JSON's report) as r; the check is Python asserts on it
page() {
    local tname="$1" code="$2"
    if "$py" - "$work/web" "$code" <<'EOPY'; then ok "  ...$tname"; else bad "  ...$tname"; fi
import glob, json, os, sys
docs = []
for p in glob.glob(os.path.join(sys.argv[1], "**", "*.json"), recursive=True):
    try:
        d = json.load(open(p))
    except ValueError:
        continue
    if isinstance(d, dict) and isinstance(d.get("report"), dict):
        docs.append((os.path.getmtime(p), d["report"]))
if not docs:
    print("no page written"); sys.exit(1)
r = sorted(docs, key=lambda x: x[0])[-1][1]
t = [s.get("title") for s in r.get("sections") or []]
sec = dict((s.get("title"), s) for s in r.get("sections") or [])
try:
    exec(sys.argv[2])
except AssertionError as e:
    print("assert:", e, t); sys.exit(1)
EOPY
}
olm_queries() { grep -c ' Orion.OLM.LogEntry NodeID' "$1" 2>/dev/null || true; }
reads_only() {
    local f="$1"
    if grep -qv '^POST /SolarWinds/InformationService/v3/Json/Query ' "$f"; then bad "  ...a call that is not a Query"; return 1; fi
    if grep -q 'SECRET-COLUMN-ASKED' "$f"; then bad "  ...a secret column or SELECT * was asked for"; return 1; fi
    ok "  ...only SWQL SELECTs on the Query endpoint ($(wc -l < "$f") queries)"
}

one_db() {
    local image="$1" extra=()
    "${rtc[@]}" image exists "$image" 2>/dev/null || "${rtc[@]}" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; return 0; }
    case "$image" in *mysql*) extra=(--tls-version=) ;; esac
    echo "---- $image"
    "${rtc[@]}" rm -f "$name" >/dev/null 2>&1
    for _ in $(seq 1 60); do (exec 3<>"/dev/tcp/127.0.0.1/$dbport") 2>/dev/null || break; sleep 0.5; done
    "${rtc[@]}" run -d --name "$name" -e MYSQL_ROOT_PASSWORD -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
        "${extra[@]}" >/dev/null || { bad "$image did not start"; return 1; }
    "$py" - "$dbport" <<'EOPY' || { bad "$image not ready"; "${rtc[@]}" logs "$name" 2>&1 | tail -5; return 1; }
import os, sys, time, pymysql
for _ in range(180):
    try:
        c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=os.environ["MYSQL_ROOT_PASSWORD"], ssl_disabled=True, autocommit=True)
        c.cursor().execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = 'site_reports'")
        break
    except Exception:
        time.sleep(1)
else:
    sys.exit(1)
cur = c.cursor()
for sql in ("CREATE USER 'report_writer'@'%%' IDENTIFIED BY '%s'" % os.environ["SWREPORT_WRITER_PASSWORD"],
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW ON site_reports.* TO 'report_writer'@'%'",
            "CREATE USER 'report_reader'@'%%' IDENTIFIED BY '%s'" % os.environ["SWREPORT_READER_PASSWORD"],
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'"):
    cur.execute(sql)
EOPY
    local DBW="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_writer REPORT_DB_PASSWORD=$SWREPORT_WRITER_PASSWORD"
    local DBR="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_reader REPORT_DB_PASSWORD=$SWREPORT_READER_PASSWORD"
    local web="report_web_dest=$work/web"
    DB_ENV="$DBW" run "the snapshot job fills the database" ok playbooks/solarwinds_snapshot.yml -- "kept."

    if PYTHONPATH="$repo/plugins/module_utils" "$py" - "$dbport" <<'EOPY'; then
import os, sys, pymysql
import site_host_swis as hs
import site_swis_db as db

options = dict(host="127.0.0.1", port=int(sys.argv[1]), database="site_reports", ssl_disabled=True)
with pymysql.connect(user="root", password=os.environ["MYSQL_ROOT_PASSWORD"], **options) as root:
    cur = root.cursor()
    cur.execute("SELECT MAX(snap_id) FROM sw_snapshot WHERE ok = 1")
    sid, = cur.fetchone()
    cur.execute("INSERT INTO sw_node (snap_id, node_id, seq, name, name_key, dns, status_code) VALUES "
                "(%s, 910001, 910001, CONCAT(CHAR(9), 'TABHOST01.EXAMPLE.MIL.'), 'mgmt91', 'mgmt91.example.mil', 2), "
                "(%s, 910002, 910002, 'tabhost01', 'retired91', 'retired91', 1)", (sid, sid))
    root.commit()
    try:
        with pymysql.connect(user="report_reader", password=os.environ["SWREPORT_READER_PASSWORD"], **options) as reader:
            full = "tabhost01.example.mil"
            candidates = db.nodes_for_hosts(reader, sid, [full], ["tabhost01"], [])
            node, how, _ = hs.match(hs._hosts([{"host": full}]), candidates)[0]
            assert (node["node_id"], how) == (910001, "name")
    finally:
        cur.execute("DELETE FROM sw_node WHERE snap_id = %s AND node_id IN (910001, 910002)", (sid,))
        root.commit()
EOPY
        ok "reader matches the full caption with a leading tab"
    else
        bad "reader matches the full caption with a leading tab"
    fi

    # ---- the health check
    : > "$work/swis.log"
    DB_ENV="$DBR" run "health: SolarWinds says, the two disagree, Not in SolarWinds; with the reader account" ok tests/solarwinds/swreport.yml \
        -e "@$work/fs.json" -e "$web" \
        -- "SolarWinds says (6 hosts): 3 alerts on 2 hosts, 1 application not up, 1 volume over 90 %." \
           "SolarWinds alerts, our checks are green: app02." "Not in SolarWinds: lab07, mon01."
    page "right under Findings, the table, the tile, the syslog counted" '
i = t.index("Findings")
assert t[i + 1:i + 4] == ["SolarWinds says (6 hosts)", "SolarWinds alerts, our checks are green (1)", "Not in SolarWinds (2)"], t
s = sec["SolarWinds says (6 hosts)"]
rows = dict((x[1], x) for x in s["rows"])
assert rows["web01"][2:] == ["Up", "2", "1 of 2", "/var 96 %", "41", "2 findings"], rows["web01"]
assert rows["db01"][6] == "3" and rows["app02"][2] == "Warning" and rows["mon01"][2] == "Missing", rows
assert [d["columns"][0] for d in s["details"][0]] == ["Severity", "SAM application", "Volume", "Syslog errors (24 h)"], s["details"][0]
assert [x[1] for x in s["details"][0][3]["rows"]] == ["28", "9", "4"], s["details"][0][3]
assert any(b["label"] == "SolarWinds alerts" and b["value"] == 3 and b["counted"] is False for b in r["summary"]), r["summary"]
assert all(sec[x].get("counted") is False for x in t if "SolarWinds" in str(x)), "a SolarWinds section counted"
assert r["status"] == "critical"'
    n=$(olm_queries "$work/swis.log")
    [ "$n" = 1 ] && ok "  ...Log Analyzer asked once for the run" || bad "  ...Log Analyzer asked $n times"
    reads_only "$work/swis.log"
    DB_ENV="$DBR" run "health with solarwinds_report_skip_hosts: lab07 left out" ok tests/solarwinds/swreport.yml -e "@$work/fs.json" \
        -e '{"solarwinds_report_skip_hosts": ["lab*"]}' -- "SolarWinds says (5 hosts)" "Not in SolarWinds: mon01." "!lab07, mon01"
    DB_ENV="$DBR" SW_ENV= run "health without the SolarWinds credential: the syslog column says why" ok tests/solarwinds/swreport.yml \
        -e "@$work/fs.json" -e "$web" -- "SolarWinds says (6 hosts)" 'syslog not read: this job has no "SolarWinds (read-only)" credential'
    page "the note under the table" 'assert "Syslog not read: this job has no" in sec["SolarWinds says (6 hosts)"]["note"]'
    fake_up Orion.OLM.LogEntry
    DB_ENV="$DBR" run "health with a SolarWinds without Log Analyzer: said, the rest complete" ok tests/solarwinds/swreport.yml \
        -e "@$work/fs.json" -- "syslog not read: this SolarWinds has no Log Analyzer (no Orion.OLM.LogEntry)" \
        "SolarWinds says (6 hosts): 3 alerts on 2 hosts"
    fake_up
    DB_ENV="$DBR" SW_ENV="SOLARWINDS_URL=https://127.0.0.1:$((port + 1)) SOLARWINDS_USERNAME=svc_swis_ro SOLARWINDS_CA_PATH=$work/ca.pem" \
        run "health with the credential of another SolarWinds: the snapshot not used, said" ok tests/solarwinds/swreport.yml \
        -e "@$work/fs.json" -- "SolarWinds not read: the SolarWinds snapshot is of another SolarWinds (127.0.0.1:$port, not 127.0.0.1:$((port + 1)))"
    DB_ENV="$DBR" run "health, syslog off (solarwinds_report_syslog: false): never asked" ok tests/solarwinds/swreport.yml -e "@$work/fs.json" \
        -e solarwinds_report_syslog=false -- "SolarWinds says (6 hosts)" "!syslog not read"
    DB_ENV= run "health without a Report database credential: nothing" ok tests/solarwinds/swreport.yml -e "@$work/fs.json" -e "$web" \
        -- "!SolarWinds says" "!SolarWinds not read"
    page "no SolarWinds section" 'assert not [x for x in t if "SolarWinds" in str(x)], t'
    DB_ENV="$DBR" run "health with solarwinds_report: false: nothing" ok tests/solarwinds/swreport.yml -e "@$work/fs.json" \
        -e solarwinds_report=false -- "!SolarWinds says"
    DB_ENV="REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT=$dbport REPORT_DB_NAME=site_reports REPORT_DB_USER=report_reader REPORT_DB_PASSWORD=wrong" \
        run "health with a wrong password: one line under Not read, green, the password never printed" ok tests/solarwinds/swreport.yml \
        -e "@$work/fs.json" -e "$web" -- "SolarWinds not read: the report database: the login was refused (the Report database credential)"
    if "$py" - "$work/out.txt" <<'EOPY'; then ok "passwords hidden"; else bad "a password appeared in the report"; fi
import os, sys
with open(sys.argv[1]) as stream:
    output = stream.read()
sys.exit(any(os.environ[k] in output for k in ("SWREPORT_READER_PASSWORD", "SWIS_FIXTURE_PASSWORD")))
EOPY
    page "one line under Not read, right under Findings, never counted" 's = sec["Not read"]
assert s["lines"][0].startswith("SolarWinds: the report database: the login was refused") and "status" not in s, s
assert s["counted"] is False and t.index("Not read") == t.index("Findings") + 1, t'

    # ---- troubleshoot
    : > "$work/swis.log"
    DB_ENV="$DBR" run "troubleshoot: what SolarWinds sees, first on the page" ok tests/solarwinds/swreport.yml -e "@$work/fs.json" -e "$web" \
        -e swr_mode=troubleshoot --limit web01.example.mil \
        -- "web01: node up, 2 alerts, port $switch Gi1/0/14, 1 Gb/s: 412 errors this hour, " " events in 24 h."
    page "above Start here, the port, its events only" '
assert t[:4] == ["What SolarWinds sees", "Its switch port", t[2], "Start here"] and t[2].startswith("Its events, last 24 h ("), t
ev = [x[2] for x in sec[t[2]]["rows"]]
assert "Gi1/0/14 is down" in ev and "Gi1/0/14 is up" in ev and "another port is down" not in ev, ev
assert "Volume /var over 95 % on web01" in ev and "web01 is not responding" in ev and "an event of yesterday" not in ev, ev
assert sec["Its switch port"]["rows"][0][1:] == ["Gi1/0/14", "Up", "1 Gb/s", "71", "12", "412", "0"], sec["Its switch port"]["rows"]'
    n=$(olm_queries "$work/swis.log")
    [ "$n" = 0 ] && ok "  ...troubleshoot never asks Log Analyzer" || bad "  ...troubleshoot asked Log Analyzer $n times"

    # ---- an old snapshot (its time moved back): one line under Not read with its age, the job green
    q "UPDATE sw_snapshot SET read_at = DATE_SUB(read_at, INTERVAL 2 HOUR)" > /dev/null
    DB_ENV="$DBR" run "health with an old snapshot: one line under Not read with its age, green" ok tests/solarwinds/swreport.yml -e "@$work/fs.json" \
        -- "SolarWinds not read: the SolarWinds snapshot is 2 h " "older than solarwinds_db_max_age (20 min)" "!SolarWinds says ("
    "${rtc[@]}" rm -f "$name" >/dev/null 2>&1
}
# q SQL (as root): the first row's first value
q() { "$py" - "$dbport" "$1" <<'EOPY'
import os, sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=os.environ["MYSQL_ROOT_PASSWORD"], database="site_reports",
                    ssl_disabled=True, autocommit=True)
cur = c.cursor(); cur.execute(sys.argv[2])
r = cur.fetchone()
print("" if r is None else r[0])
EOPY
}

for image in $images; do one_db "$image"; done
echo "fails: $fails"
[ "$fails" -eq 0 ]
