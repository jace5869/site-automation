#!/usr/bin/env bash
# The certificate inventory's SQL Server step (roles/cert_inventory, cert_inventory_sql) against a real SQL
# Server (the mcr.microsoft.com/mssql/server image, in podman or docker), fed by the network device
# certificate report (stand-in devices: tests/netcerts/tls_servers.py) - no host to log in to. The table and its
# view are made (cert_inventory_sql_create); each certificate is a row, with its types (dates, bits, null);
# a run again updates the same rows (first_seen kept, last_seen moved); a device gone is present = 0; a
# subject with a quote and SQL in it is only data. Skipped (exit 0) when no container runtime or image.
#   bash tests/certinv/run_cert_inventory_sql_test.sh      (CERT_SQL_TEST_IMAGE=..., CERT_SQL_TEST_RUNTIME=docker, ANSIBLE_PLAYBOOK=...)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${CERT_SQL_TEST_PYTHON:-$(command -v python3)}"
image="${CERT_SQL_TEST_IMAGE:-mcr.microsoft.com/mssql/server:2022-latest}"
port="${CERT_SQL_TEST_PORT:-18490}"
sql=$((port + 1)); d1=$((port + 10)); d2=$((port + 11)); d3=$((port + 12))
rt=${CERT_SQL_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymssql" 2>/dev/null || { echo "skip - pymssql is not installed (pip install pymssql)"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image (podman pull it)"; exit 0; }
work="$(mktemp -d)"
name="sa-cert-sql-test-$$"
pw="Test-Only-$$-Pw1!"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; "$rt" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

"$rt" run -d --name "$name" -e ACCEPT_EULA=Y -e "MSSQL_SA_PASSWORD=$pw" -p "127.0.0.1:$sql:1433" "$image" >/dev/null || { echo "ABORT - SQL Server did not start"; exit 1; }
"$py" - "$sql" "$pw" <<'EOPY' || { echo "ABORT - SQL Server not ready"; "$rt" logs "$name" 2>&1 | tail -5; exit 1; }
import sys, time, pymssql
for _ in range(90):
    try:
        c = pymssql.connect(server="127.0.0.1", port=int(sys.argv[1]), user="sa", password=sys.argv[2], autocommit=True)
        c.cursor().execute("CREATE DATABASE certdb"); sys.exit(0)
    except Exception:
        time.sleep(2)
sys.exit(1)
EOPY

export TLS_SERVERS_DIR="$work/devices"        # the same certificates on every start
start_devices() {  # the stand-in devices: sw01 (20 days), pr01 (expired), and - when $1 is 3 - quote01, whose subject has a quote and SQL
    "$py" "$repo/tests/netcerts/tls_servers.py" "$d1:sw01.example.mil:20" "$d2:pr01.example.mil:-3" \
        ${1:+"$d3:x';DROP TABLE dbo.cert_inventory;--.example.mil:300"} >/dev/null 2>&1 & pids+=($!)
    devices=$!
    for p in "$d1" "$d2"; do for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done
    sleep 1
}
cat > "$work/inv.yml" <<EOF
all:
  children:
    net_cert_devices:
      hosts:
        sw01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$d1]}
        pr01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$d2]}
        quote01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$d3]}
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOF
vars="{\"cert_inventory_sql\": true, \"cert_inventory_sql_create\": true, \"cert_inventory_sql_server\": \"127.0.0.1\",
       \"cert_inventory_sql_port\": $sql, \"cert_inventory_sql_database\": \"certdb\", \"net_cert_timeout\": 3}"
run() {  # run OUTFILE [ansible-playbook args...]
    local out="$1"; shift
    (cd "$repo" && env CERT_INVENTORY_SQL_USER=sa CERT_INVENTORY_SQL_PASSWORD="$pw" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False "$ap" -i "$work/inv.yml" playbooks/net_cert_report.yml -e "$vars" "$@") > "$work/$out" 2>&1
}
q() { "$py" - "$sql" "$pw" "$1" <<'EOPY'
import json, sys, pymssql
c = pymssql.connect(server="127.0.0.1", port=int(sys.argv[1]), user="sa", password=sys.argv[2], database="certdb", as_dict=True)
cur = c.cursor(); cur.execute(sys.argv[3])
print(json.dumps(cur.fetchall(), default=str))
EOPY
}

# ---- 1. the first run: the table, the view, a row per certificate ----
start_devices 3
run first.txt
[ $? -eq 0 ] && ok "the report ran with cert_inventory_sql" || { bad "the first run"; tail -20 "$work/first.txt" | sed 's/^/       /'; }
grep -qF "3 certificates (net) in 127.0.0.1/certdb dbo.cert_inventory; no longer found on the hosts read: 0" "$work/first.txt" \
    && ok "the job says what it sent" || bad "the job's line about SQL Server"
rows=$(q "SELECT host, status, tier, days, not_after, in_use, trust_store, public_key, len(sha1) AS l1, len(sha256) AS l2, present, sans, subject FROM dbo.cert_inventory ORDER BY host")
"$py" - "$rows" <<'EOPY' && ok "3 rows: each with its status, tier, date, key, fingerprints, names; the quote and SQL in a subject are only data" || bad "the rows (above)"
import json, sys
r = {x["host"]: x for x in json.loads(sys.argv[1])}
errs = []
if sorted(r) != ["pr01.example.mil", "quote01.example.mil", "sw01.example.mil"]: errs.append(sorted(r))
if r.get("pr01.example.mil", {}).get("status") != "expired" or r.get("sw01.example.mil", {}).get("status") != "warning": errs.append("status")
for x in r.values():
    if x["tier"] != "self-signed" or x["in_use"] is not True or x["trust_store"] is not False or x["public_key"] != "RSA 2048" \
       or (x["l1"], x["l2"]) != (40, 64) or x["present"] is not True or "IP:127.0.0.1" not in x["sans"] or not x["not_after"]:
        errs.append(x)
if "DROP TABLE" not in r.get("quote01.example.mil", {}).get("subject", ""): errs.append("the quoted subject")
print("\n".join("       %s" % e for e in errs))
sys.exit(1 if errs else 0)
EOPY
view=$(q "SELECT COUNT(*) AS n FROM dbo.cert_inventory_current")
[ "$view" = '[{"n": 3}]' ] && ok "the view cert_inventory_current: the 3 present" || bad "the view: $view"
first=$(q "SELECT id, first_seen, last_seen FROM dbo.cert_inventory ORDER BY id")

# ---- 2. a run again, quote01 gone: the same rows updated, quote01 present = 0 ----
kill "$devices" 2>/dev/null; wait "$devices" 2>/dev/null
start_devices
sleep 1
run again.txt
[ $? -eq 0 ] && ok "a run again" || { bad "a run again"; tail -20 "$work/again.txt" | sed 's/^/       /'; }
grep -qF "no longer found on the hosts read: 0" "$work/again.txt" && ok "...quote01 was not read: not counted as gone (only hosts read are)" \
    || bad "...quote01 counted as gone though not read"
n=$(q "SELECT COUNT(*) AS n FROM dbo.cert_inventory")
[ "$n" = '[{"n": 3}]' ] && ok "...no new row: the same host, place and certificate keep their row" || bad "...rows: $n"
"$py" - "$first" "$(q "SELECT id, first_seen, last_seen FROM dbo.cert_inventory ORDER BY id")" <<'EOPY' && ok "...first_seen kept, last_seen moved on the rows read again" || bad "...first_seen / last_seen (above)"
import json, sys
a = {x["id"]: x for x in json.loads(sys.argv[1])}
b = {x["id"]: x for x in json.loads(sys.argv[2])}
moved = [i for i in b if b[i]["last_seen"] > a[i]["last_seen"]]
kept = all(b[i]["first_seen"] == a[i]["first_seen"] for i in b)
print("       moved %d, kept %s" % (len(moved), kept))
sys.exit(0 if kept and len(moved) == 2 else 1)
EOPY

# ---- 3. pr01 answers with a new certificate: its old row is present = 0 ----
kill "$devices" 2>/dev/null; wait "$devices" 2>/dev/null
"$py" "$repo/tests/netcerts/tls_servers.py" "$d1:sw01.example.mil:20" "$d2:pr01.example.mil:400" >/dev/null 2>&1 & pids+=($!)
for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$d2") 2>/dev/null && break; sleep 0.1; done
sleep 2
run renewed.txt
grep -qF "no longer found on the hosts read: 1" "$work/renewed.txt" && ok "pr01 renewed: its old certificate's row marked gone (1)" || bad "pr01 renewed: $(grep -o 'no longer found[^"]*' "$work/renewed.txt")"
p=$(q "SELECT host, status, present FROM dbo.cert_inventory WHERE host = 'pr01.example.mil' ORDER BY present")
[ "$p" = '[{"host": "pr01.example.mil", "status": "expired", "present": false}, {"host": "pr01.example.mil", "status": "ok", "present": true}]' ] \
    && ok "...two rows for pr01: the expired one gone, the new one present" || bad "...pr01 rows: $p"

[ "$fails" -eq 0 ] && echo "ok   - all certificate inventory SQL Server scenarios passed" || { echo "$fails FAILED"; exit 1; }
