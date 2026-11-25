#!/usr/bin/env bash
# 1.133.0: the Linux software inventory's SQL Server output (sw_inventory_mssql) against a real SQL Server 2022
# (mcr.microsoft.com/mssql/server), through ansible-playbook on a UBI 9 container: the table made on the first run
# (create_table) and appended to on the second (two run_ids, the same row count each); a table made by hand with other
# names (column_map, a column left out, create_table false); a table missing a column - SA-SWI-40 with the columns it
# expects, nothing written there, the CSV still written. Skipped (exit 0) when there is no podman, image or pymssql for the controller's Python.
#   bash tests/swinv/run_sw_inventory_mssql_test.sh   (SWINV_TEST_PYTHON=a Python with pymssql, SWINV_MSSQL_TEST_PORT=18433,
#                                                    ANSIBLE_PLAYBOOK=..., SWINV_TEST_COLLECTIONS=...)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SWINV_TEST_PYTHON:-$(command -v python3)}"
image="${SWINV_MSSQL_TEST_IMAGE:-mcr.microsoft.com/mssql/server:2022-latest}"
sqlport="${SWINV_MSSQL_TEST_PORT:-18433}"
name="sa-swinv-mssql-$$" host="sa-swinv-mssql-ubi9-$$"
engine="${CONTAINER_ENGINE:-$(command -v podman || true)}"
[ -n "$engine" ] || { echo "skip - no podman"; exit 0; }
"$py" -c "import pymssql" 2>/dev/null || { echo "skip - pymssql is not installed for $py"; exit 0; }
"$engine" image exists "$image" 2>/dev/null || { echo "skip - no image $image"; exit 0; }
work="$(mktemp -d)"
pw="Test-Only-Sa-$$-x9!"
cleanup() { "$engine" rm -f -t 0 "$name" "$host" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }

cols="${SWINV_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$repo/tests/patch/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi
"$engine" run -d --name "$name" -e ACCEPT_EULA=Y -e "MSSQL_SA_PASSWORD=$pw" -p "127.0.0.1:$sqlport:1433" "$image" >/dev/null || { echo "ABORT - SQL Server did not start"; exit 1; }
"$engine" run -d --name "$host" registry.access.redhat.com/ubi9/ubi:latest sleep infinity >/dev/null || { echo "ABORT - no ubi9"; exit 1; }
"$py" - "$sqlport" "$pw" <<'EOPY' || { echo "ABORT - SQL Server not ready"; exit 1; }
import sys, time, pymssql
for _ in range(120):
    try:
        c = pymssql.connect(server="127.0.0.1", port=sys.argv[1], user="sa", password=sys.argv[2], autocommit=True)
        break
    except Exception:
        time.sleep(2)
else:
    sys.exit(1)
cur = c.cursor()
cur.execute("CREATE DATABASE Inventory")
cur.execute("USE Inventory")
cur.execute("EXEC('CREATE SCHEMA cmdb')")
cur.execute("CREATE TABLE cmdb.InstalledSoftware (RunName nvarchar(64) NOT NULL, ComputerName nvarchar(255) NOT NULL, "
            "PackageName nvarchar(255) NOT NULL, Ver nvarchar(128), Rel nvarchar(128), Arch nvarchar(32), InstallDate datetime2(0), "
            "Epoch int, ReadAt datetime2(0), OS nvarchar(64), Kernel nvarchar(128), Repo nvarchar(255))")
cur.execute("CREATE TABLE cmdb.Short (run_id nvarchar(64), host nvarchar(255), name nvarchar(255))")
EOPY
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    linux:
      hosts:
        rhel9-sql01.example.mil: {ansible_host: $host, ansible_connection: containers.podman.podman, ansible_podman_executable: $engine, ansible_python_interpreter: /usr/bin/python3, ansible_become: false}
EOI
play() {
    (cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
        CERT_INVENTORY_SQL_USER=sa CERT_INVENTORY_SQL_PASSWORD="$pw" \
        "$ap" -i "$work/inv.yml" playbooks/linux_sw_inventory.yml -e target=linux -e "$1") > "$work/run.txt" 2>&1
}
q() { "$py" -c 'import sys, pymssql; c = pymssql.connect(server="127.0.0.1", port=sys.argv[1], user="sa", password=sys.argv[2], database="Inventory"); cur = c.cursor(); cur.execute(sys.argv[3]); print("|".join(",".join(str(x) for x in r) for r in cur.fetchall()))' "$sqlport" "$pw" "$1"; }
n="$("$engine" exec "$host" rpm -qa | wc -l | tr -d ' ')"
base="\"server\": \"127.0.0.1\", \"port\": $sqlport, \"database\": \"Inventory\""

play "{\"sw_inventory_outputs\": [\"mssql\"], \"sw_inventory_mssql\": {$base}}"; check "run 1: green" "$?" 0
check "run 1: the table made, a row per package" "$(q 'SELECT COUNT(*) FROM dbo.sw_inventory')" "$n"
check "run 1: its index" "$(q "SELECT COUNT(*) FROM sys.indexes WHERE name = 'IX_sw_inventory_run'")" 1
check "run 1: epoch NULL for none, release kept" "$(q "SELECT COUNT(*) FROM dbo.sw_inventory WHERE name = 'bash' AND epoch IS NULL AND [release] LIKE '%el9%'")" 1
check "run 1: times as datetime2" "$(q "SELECT COUNT(*) FROM dbo.sw_inventory WHERE installed_at IS NULL OR read_at IS NULL")" 0
play "{\"sw_inventory_outputs\": [\"mssql\"], \"sw_inventory_mssql\": {$base}}"; check "run 2: green" "$?" 0
check "run 2: appended, two run_ids" "$(q 'SELECT COUNT(DISTINCT run_id), COUNT(*) FROM dbo.sw_inventory')" "2,$((n * 2))"

map="\"schema\": \"cmdb\", \"table\": \"InstalledSoftware\", \"create_table\": false, \"column_map\": {\"run_id\": \"RunName\", \"host\": \"ComputerName\", \"name\": \"PackageName\", \"version\": \"Ver\", \"release\": \"Rel\", \"installed_at\": \"InstallDate\", \"read_at\": \"ReadAt\", \"vendor\": \"\"}"
play "{\"sw_inventory_outputs\": [\"mssql\"], \"sw_inventory_mssql\": {$base, $map}}"; check "column_map: green" "$?" 0
check "column_map: the rows in their columns" "$(q "SELECT COUNT(*) FROM cmdb.InstalledSoftware WHERE ComputerName = 'rhel9-sql01.example.mil' AND PackageName = 'bash' AND Rel LIKE '%el9%'")" 1
check "column_map: every package" "$(q 'SELECT COUNT(*) FROM cmdb.InstalledSoftware')" "$n"

play "{\"sw_inventory_outputs\": [\"mssql\", \"csv\"], \"sw_inventory_dir\": \"$work/out\", \"sw_inventory_mssql\": {$base, \"schema\": \"cmdb\", \"table\": \"Short\", \"create_table\": false}}"
check "missing columns: the job fails at its end" "$([ $? -ne 0 ] && echo failed)" failed
grep -q "SA-SWI-40.*has no column read_at" "$work/run.txt" && check "missing columns: SA-SWI-40 names them" yes yes || check "missing columns: SA-SWI-40 names them" no yes
check "missing columns: nothing written there" "$(q 'SELECT COUNT(*) FROM cmdb.Short')" 0
check "missing columns: the CSV still written" "$(test -s "$work/out/sw-inventory-latest.csv" && echo yes)" yes
check "never dropped: the tables are all there" "$(q "SELECT COUNT(*) FROM sys.tables")" 3

if [ $fails -ne 0 ]; then echo "$fails FAILED"; tail -n 40 "$work/run.txt"; exit 1; fi
echo "all passed"
