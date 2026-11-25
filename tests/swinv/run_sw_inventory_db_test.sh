#!/usr/bin/env bash
# 1.133.0: the Linux software inventory in the report database (roles/sw_inventory/library/site_sw_inventory.py, output
# report_db) against a real MySQL 8.4 set up as docs/REPORT_DATABASE.md says (site_reports, report_writer with its grants,
# plain TCP), through ansible-playbook on a UBI 9 container: the first run makes the four tables and writes; which removed;
# the second run's sw_inventory_change has it (removed), the baseline being the database's last run of the host; the
# tables' statements are the doc's (MySQL takes the backticked release and change); a writer with no tables and
# sw_inventory_db_create false is SA-SWI-11 with the fix, the CSV still written.
# Skipped (exit 0) when there is no podman, image or PyMySQL.
#   bash tests/swinv/run_sw_inventory_db_test.sh   (ANSIBLE_PLAYBOOK=..., SWINV_DB_TEST_PORT=18491, SWINV_TEST_COLLECTIONS=...)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SWINV_TEST_PYTHON:-$(command -v python3)}"
image="${SWINV_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${SWINV_DB_TEST_PORT:-18491}"
name="sa-swinv-db-$$" host="sa-swinv-db-ubi9-$$"
engine="${CONTAINER_ENGINE:-$(command -v podman || true)}"
[ -n "$engine" ] || { echo "skip - no podman"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py"; exit 0; }
"$engine" image exists "$image" 2>/dev/null || { echo "skip - no image $image"; exit 0; }
work="$(mktemp -d)"
root="Test-Only-root-$$" wpw="Test-Only-writer-$$"
cleanup() { "$engine" rm -f -t 0 "$name" "$host" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }

cols="${SWINV_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$repo/tests/patch/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi
"$engine" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
    --tls-version= >/dev/null || { echo "ABORT - MySQL did not start"; exit 1; }
"$engine" run -d --name "$host" registry.access.redhat.com/ubi9/ubi:latest sleep infinity >/dev/null || { echo "ABORT - no ubi9"; exit 1; }
"$py" - "$dbport" "$root" "$wpw" <<'EOPY' || { echo "ABORT - MySQL not ready"; exit 1; }
import sys, time, pymysql
for _ in range(300):
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
            "CREATE DATABASE other_db",
            "CREATE USER 'nocreate'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT, INSERT, DELETE ON other_db.* TO 'nocreate'@'%'"):
    cur.execute(sql)
EOPY
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
   linux:
    hosts:
     rhel9-db01.example.mil: {ansible_host: $host, ansible_connection: containers.podman.podman, ansible_podman_executable: $engine, ansible_python_interpreter: /usr/bin/python3, ansible_become: false}
EOI
play() {    # play USER DATABASE VARS
    (cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
        REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT="$dbport" REPORT_DB_NAME="$2" REPORT_DB_USER="$1" REPORT_DB_PASSWORD="$wpw" \
        "$ap" -i "$work/inv.yml" playbooks/linux_sw_inventory.yml -e target=linux -e "$3") > "$work/run.txt" 2>&1
}
q() { "$py" -c 'import sys, pymysql; c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True); cur = c.cursor(); cur.execute(sys.argv[3]); print("|".join(",".join(str(x) for x in r) for r in cur.fetchall()))' "$dbport" "$root" "$1"; }

v="{\"sw_inventory_outputs\": [\"report_db\"]}"
play report_writer site_reports "$v"; check "run 1: green" "$?" 0
check "run 1: four tables" "$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name LIKE 'sw_inventory_%'")" 4
check "run 1: schema_version" "$(q "SELECT version FROM schema_version WHERE component = 'sw_inventory'")" 1
check "run 1: packages = rpm -qa" "$(q 'SELECT COUNT(*) FROM sw_inventory_pkg WHERE run_id = 1')" "$("$engine" exec "$host" rpm -qa | wc -l | tr -d ' ')"
check "run 1: the run row" "$(q 'SELECT id, hosts_ok, hosts_failed FROM sw_inventory_run')" "1,1,0"
check "run 1: no change rows (no earlier list)" "$(q 'SELECT COUNT(*) FROM sw_inventory_change')" 0
check "run 1: release kept" "$(q "SELECT COUNT(*) FROM sw_inventory_pkg WHERE name = 'bash' AND \`release\` LIKE '%el9%'")" 1
"$engine" exec "$host" rpm -e --nodeps which >/dev/null 2>&1
play report_writer site_reports "$v"; check "run 2: green" "$?" 0
check "run 2: which removed" "$(q 'SELECT run_id, host, name, `change`, new_evr FROM sw_inventory_change')" "2,rhel9-db01.example.mil,which,removed,"
grep -q "Compared with: the report database" "$work/run.txt" && check "run 2: baseline from the database" yes yes || check "run 2: baseline from the database" no yes
# the doc's statements are the module's, and MySQL takes them (the tables made by hand)
"$py" - "$repo" "$dbport" "$root" <<'EOPY'; check "the doc's statements run on MySQL 8.4" "$?" 0
import re, sys, pymysql
doc = open(sys.argv[1] + "/docs/REPORT_DATABASE.md").read()
part = doc.split("<!-- the statements: the software inventory")[1].split("<!-- end of the software inventory statements -->")[0]
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[2]), user="root", password=sys.argv[3], ssl_disabled=True, autocommit=True)
cur = c.cursor()
cur.execute("CREATE DATABASE byhand")
cur.execute("USE byhand")
for sql in re.findall(r"```sql\n(.*?)```", part, re.S):
    cur.execute(sql.strip().rstrip(";"))
EOPY
# no tables, may not make them
play nocreate other_db "{\"sw_inventory_outputs\": [\"report_db\", \"csv\"], \"sw_inventory_db_create\": false, \"sw_inventory_dir\": \"$work/out\"}"
check "no tables: the job fails at its end" "$([ $? -ne 0 ] && echo failed)" failed
grep -q "SA-SWI-11" "$work/run.txt" && check "no tables: SA-SWI-11 and the fix" yes yes || check "no tables: SA-SWI-11 and the fix" no yes
check "no tables: the CSV still written" "$(test -s "$work/out/sw-inventory-latest.csv" && echo yes)" yes

if [ $fails -ne 0 ]; then echo "$fails FAILED"; tail -n 40 "$work/run.txt"; exit 1; fi
echo "all passed"
