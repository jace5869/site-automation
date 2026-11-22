#!/usr/bin/env bash
# 1.101.2: the AD job's runs in the report database (roles/ad_report/library/site_ad_db.py) and the portal's reader (site_portal
# AdDb, site_portal_db_check) against a real MySQL 8.4 set up as docs/REPORT_DATABASE.md says: database site_reports,
# report_writer and report_reader with their grants, plain TCP. Through ansible-playbook: the first run makes the tables and
# writes; more runs; the portal reads them back as report_reader (now, since when, the trend, the links); the reader cannot
# write; the deploy's check says the writer can write and the reader cannot; a wrong password, no tables with ad_db_create
# false and a read-only writer are one warning each, never a failed task; prune.
# The container's name and port come from variables; the container is removed by its name. Skipped (exit 0) when there is
# no container runtime, image or PyMySQL.
#   bash tests/ad/run_ad_db_test.sh   (ANSIBLE_PLAYBOOK=..., AD_DB_TEST_IMAGE=..., AD_DB_TEST_NAME=..., AD_DB_TEST_PORT=18490,
#                                      AD_DB_TEST_PYTHON=...: the Python the modules run on)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${AD_DB_TEST_PYTHON:-$(command -v python3)}"
image="${AD_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${AD_DB_TEST_PORT:-18490}"
name="${AD_DB_TEST_NAME:-sa-ad-db-test}"
rt=${AD_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
root="Test-Only-root-$$"
wpw="Test-Only-writer-$$"
rpw="Test-Only-reader-$$"
cleanup() { "${rtc[@]}" rm -f "$name" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

"${rtc[@]}" rm -f "$name" >/dev/null 2>&1
"${rtc[@]}" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
    --tls-version= >/dev/null || { echo "ABORT - MySQL did not start"; exit 1; }
"$py" - "$dbport" "$root" "$wpw" "$rpw" <<'EOPY' || { echo "ABORT - MySQL not ready"; "${rtc[@]}" logs "$name" 2>&1 | tail -5; exit 1; }
import sys, time, pymysql
for _ in range(300):                 # MySQL 8.4 first start: up to minutes on a busy box
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
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'",
            "CREATE USER 'report_ro_writer'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT ON site_reports.* TO 'report_ro_writer'@'%'"):
    cur.execute(sql)
EOPY

printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
# the playbook: one AD run (ad_db_filters makes the rows from an _ad as ad_filters gives it), DC03 as asked, minutes back
cat > "$work/run.yml" <<'EOYML'
- hosts: localhost
  gather_facts: false
  vars:
    _t: "{{ '%Y-%m-%dT%H:%M:%SZ' | strftime((now(utc=true).timestamp() | int) - (back | int) * 60, utc=true) }}"
    _ad:
      report: {status: "{{ 'critical' if dc3 == 'critical' else 'ok' }}"}
      critical: "{{ 1 if dc3 == 'critical' else 0 }}"
      warning: 0
      problems: "{{ [{'domain': 'corp.test', 'object': 'DC03', 'severity': dc3, 'text': 'dcdiag: Replications failed (8524)'}] if dc3 != 'ok' else [] }}"
      dcs: [{dc: DC01, domain: corp.test, read: true}, {dc: DC02, domain: corp.test, read: true}, {dc: DC03, domain: corp.test, read: true}]
      repl:
        now: "{{ _t }}"
        dcs: [{name: DC01, site: SITA0101}, {name: DC02, site: SITA0101}, {name: DC03, site: SITB0201}]
        links:
          - {frm: DC01, to: DC03, nc: corp.test, last_success: "2026-01-01T00:00:00Z", failures: 3, error: "8524", last_failure: "{{ _t }}"}
          - {frm: DC02, to: DC01, nc: Configuration, last_success: "{{ _t }}", failures: 0}
  tasks:
    - site_ad_db:
        host: 127.0.0.1
        port: "{{ dbport | int }}"
        database: site_reports
        user: "{{ dbuser }}"
        password: "{{ dbpw }}"
        create: "{{ create | default(true) | bool }}"
        read_at: "{{ _t }}"
        job: "{{ 9000 + (back | int) }}"
        template_id: 12
        run: "{{ _ad | ad_db_payload({'kind': 'ad', 'dcs': [{'dc': 'DC01'}]}, {}) }}"
        keep_days: "{{ keep | default(400) | int }}"
      register: r
    - ansible.builtin.copy:
        content: "{{ r | to_json }}"
        dest: "{{ out }}"
EOYML
export ANSIBLE_LIBRARY="$repo/roles/ad_report/library:$repo/roles/site_portal_stack/library" ANSIBLE_MODULE_UTILS="$repo/plugins/module_utils"
export ANSIBLE_FILTER_PLUGINS="$repo/plugins/filter" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_LOCALHOST_WARNING=false
play() {   # play OUT USER PW DC3 BACK [extra vars...]
    local out="$1" u="$2" p="$3" st="$4" back="$5"; shift 5
    "$ap" -i "$work/inv.yml" "$work/run.yml" -e "dbport=$dbport dbuser=$u dbpw=$p dc3=$st back=$back out=$work/$out" "$@" > "$work/$out.log" 2>&1
}
field() { "$py" -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]))" "$work/$1" "$2"; }

# 1. the first run: the tables made, the run written
play r1.json report_writer "$wpw" ok 300
[ "$(field r1.json created)" = "True" ] && [ "$(field r1.json ok)" = "True" ] && [ "$(field r1.json dcs)" = "3" ] && [ "$(field r1.json links)" = "2" ] \
    && ok "the first run: tables made, 3 DCs and 2 links written" || bad "the first run: $(cat "$work/r1.json" 2>/dev/null) $(tail -5 "$work/r1.json.log")"
# 2. more runs, DC03 critical in the last 3
for b in 270 240 210 180; do play rn.json report_writer "$wpw" ok "$b"; done
for b in 60 30 0; do play rn.json report_writer "$wpw" critical "$b"; done
[ "$(field rn.json ok)" = "True" ] && [ "$(field rn.json created)" = "False" ] && ok "8 runs kept" || bad "more runs: $(cat "$work/rn.json")"

# 3. the portal reads them as report_reader; the reader cannot write; the deploy's check of both
"$py" - "$repo" "$dbport" "$rpw" "$wpw" "$work" <<'EOPY' > "$work/read.txt" 2>&1
import json, os, sys
repo, port, rpw, wpw, work = sys.argv[1:6]
sys.path[:0] = [os.path.join(repo, "roles", "site_portal_stack", "files"), os.path.join(repo, "plugins", "filter"), os.path.join(repo, "plugins", "module_utils")]
import site_portal as sp, site_report_db as db
class C(object):
    data = work
    report_db = {"host": "127.0.0.1", "port": int(port), "database": "site_reports", "user": "report_reader", "password": rpw, "tls": False}
m = sp.AdDb(C()).read(template_id=12)
d3 = [d for d in m["dcs"] if d["dc"] == "dc03"][0]          # the DC key: its lower-case host label (1.101.2)
print("RUN", m["run"]["job"], m["run"]["status"], m["run"]["dcs"], m["run"]["dcs_read"])
print("DC03", d3["now"], d3["runs_since"], d3["since"] == m["times"][-3], "".join(x[0] for x in d3["trend"]))
print("ORDER", ",".join(d["dc"] for d in m["dcs"]))
print("LINKS", json.dumps([(x["frm"], x["to"], x["now"], x["failures"]) for x in m["links"]]))
print("HIST", len(m["hist"]), m["hist"][-1]["c"], m["hist"][0]["c"])
print("LIVE", m["live"].get("kind"))
conn, _ = db.connect({"host": "127.0.0.1", "port": int(port), "user": "report_reader", "password": rpw, "database": "site_reports", "tls": False, "ca_file": "", "connect_timeout": 10})
try:
    conn.cursor().execute("DELETE FROM ad_run")
    print("READER_WRITES yes")
except Exception as e:
    print("READER_WRITES no", e.args[0] if e.args else "")
EOPY
grep -q "^RUN 9000 critical 3 3$" "$work/read.txt" && ok "the portal (report_reader): the newest run" || bad "the newest run: $(cat "$work/read.txt")"
grep -q "^DC03 critical 3 True oooooccc$" "$work/read.txt" && ok "DC03: critical since the run 3 back, its trend" || bad "DC03: $(grep DC03 "$work/read.txt")"
grep -q "^ORDER dc03,dc01,dc02$" "$work/read.txt" && ok "the DCs worst first" || bad "order: $(grep ORDER "$work/read.txt")"
grep -q '^LINKS \[\["dc01", "dc03", "critical", 3\]\]$' "$work/read.txt" && ok "the link that needs a look, the healthy one left out" || bad "links: $(grep LINKS "$work/read.txt")"
grep -q "^HIST 8 1 0$" "$work/read.txt" && ok "the week: 8 runs" || bad "hist: $(grep HIST "$work/read.txt")"
grep -q "^LIVE ad$" "$work/read.txt" && ok "the run's live part (the DCs for SolarWinds)" || bad "live: $(grep LIVE "$work/read.txt")"
grep -q "^READER_WRITES no 1142$" "$work/read.txt" && ok "report_reader cannot write (1142)" || bad "the reader: $(grep READER "$work/read.txt")"

cat > "$work/check.yml" <<'EOYML'
- hosts: localhost
  gather_facts: false
  tasks:
    - site_portal_db_check: {host: 127.0.0.1, port: "{{ dbport | int }}", database: site_reports, user: "{{ dbuser }}", password: "{{ dbpw }}"}
      register: r
    - ansible.builtin.copy: {content: "{{ r | to_json }}", dest: "{{ out }}"}
EOYML
"$ap" -i "$work/inv.yml" "$work/check.yml" -e "dbport=$dbport dbuser=report_reader dbpw=$rpw out=$work/c1.json" > "$work/c1.log" 2>&1
"$ap" -i "$work/inv.yml" "$work/check.yml" -e "dbport=$dbport dbuser=report_writer dbpw=$wpw out=$work/c2.json" > "$work/c2.log" 2>&1
"$ap" -i "$work/inv.yml" "$work/check.yml" -e "dbport=$dbport dbuser=report_reader dbpw=wrong out=$work/c3.json" > "$work/c3.log" 2>&1
[ "$(field c1.json writes)" = "False" ] && [ "$(field c1.json ad_runs)" = "8" ] && ok "the deploy's check: the reader only reads, 8 AD runs" || bad "check reader: $(cat "$work/c1.json")"
[ "$(field c2.json writes)" = "True" ] && ok "the deploy's check: the writer can write (said as a warning by the deploy)" || bad "check writer: $(cat "$work/c2.json")"
[ "$(field c3.json ok)" = "False" ] && grep -q "Access denied" "$work/c3.json" && ! grep -q "wrong" "$work/c3.json" \
    && ok "the deploy's check: a wrong password said, never printed" || bad "check wrong: $(cat "$work/c3.json")"

# 4. never a failed task: a wrong password, a read-only writer, no tables with create false
play w1.json report_writer wrong ok 0
[ "$(field w1.json ok)" = "False" ] && grep -q "Access denied" "$work/w1.json" && grep -q "ok=2" "$work/w1.json.log" && ! grep -q "wrong" "$work/w1.json" \
    && ok "a wrong password: one warning, the play goes on" || bad "wrong password: $(cat "$work/w1.json") $(grep -E 'ok=|WARN' "$work/w1.json.log")"
play w2.json report_ro_writer "$wpw" ok 0
[ "$(field w2.json ok)" = "False" ] && grep -q "INSERT command denied" "$work/w2.json" && grep -q "ok=2" "$work/w2.json.log" \
    && ok "a writer that cannot write: one warning, nothing half written" || bad "read-only writer: $(cat "$work/w2.json")"
"${rtc[@]}" exec "$name" mysql -uroot "-p$root" site_reports -e "DROP TABLE ad_link, ad_dc, ad_run" >/dev/null 2>&1
play w3.json report_writer "$wpw" ok 0 -e create=false
[ "$(field w3.json ok)" = "False" ] && grep -q "ad_db_create: false" "$work/w3.json" && ok "no tables with ad_db_create false: said, nothing made" || bad "create false: $(cat "$work/w3.json")"

# 5. prune: runs older than keep_days go with their rows
play p1.json report_writer "$wpw" ok 3000
play p2.json report_writer "$wpw" ok 0 -e keep=1
[ "$(field p2.json pruned)" = "1" ] && ok "prune: the run 2 days old deleted (keep_days 1)" || bad "prune: $(cat "$work/p2.json")"
left="$("${rtc[@]}" exec "$name" mysql -uroot "-p$root" site_reports -N -e "SELECT COUNT(*) FROM ad_dc" 2>/dev/null | tail -1)"
[ "$left" = "3" ] && ok "prune: its DC rows too" || bad "prune DC rows: $left"

echo "-- $fails failed"
[ "$fails" -eq 0 ]
