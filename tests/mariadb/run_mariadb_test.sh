#!/usr/bin/env bash
# Run roles/check_mariadb against a fake `podman` (tests/mariadb/fakepodman.py) and check the
# findings for: one container, several containers, discovery, a stopped and a missing container,
# a rootless user that does not exist, MariaDB / MySQL 8.0 / MySQL 8.4 / MySQL 5.7 (the replication
# statement each one really has, Group Replication, a refused login), the groups that switch the
# check on, and that the database password never appears on a command line.
#   bash tests/mariadb/run_mariadb_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
cp "$here/fakepodman.py" "$work/bin/podman"; chmod +x "$work/bin/podman"
py="$(command -v python3)"
cat > "$work/inv.yml" <<EOI
all:
  children:
    mariadb_hosts:
      hosts:
        localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOI
for g in mysql_hosts database_hosts; do
cat > "$work/inv-$g.yml" <<EOI
all:
  children:
    $g:
      hosts:
        localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOI
done
cat > "$work/inv-nogroup.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOI
fails=0
# The password has a space, quotes, $, a backslash, # and a trailing space: it must arrive unchanged.
PW='s3cr3t-pw-XYZ q"'"'"'$x\y#z '

# scenario NAME "containers file content" "extra vars json" EXPECT_FINDINGS(comma list, sorted; "" = none) [EXPECT_SUMMARY_TEXT]
scenario() {
    local name="$1" conts="$2" vars="$3" want="$4" text="${5:-}"
    printf '%b' "$conts" > "$work/containers"; : > "$work/podman.log"
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_CONTAINERS="$work/containers" FAKE_LOG="$work/podman.log" \
        MARIADB_MONITOR_USER=monitor MARIADB_MONITOR_PASSWORD="$PW" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_VERBOSITY="${VERBOSITY:-0}" \
        "$ap" -i "$work/${INV:-inv.yml}" "$here/check.yml" -e "$vars") > "$work/out.txt" 2>&1
    local rc=$?
    local got; got="$(grep -o 'FINDINGS [^"]*' "$work/out.txt" | head -1 | sed 's/^FINDINGS *//')"
    if [ "$rc" -ne 0 ] || [ "$got" != "$want" ]; then
        echo "FAIL - $name: exit $rc, findings [$got], expected [$want]"; tail -25 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    if [ -n "$text" ] && ! grep -q -- "$text" "$work/out.txt"; then
        echo "FAIL - $name: output lacks: $text"; fails=$((fails + 1)); return
    fi
    if grep -qF 's3cr3t-pw-XYZ' "$work/podman.log" "$work/out.txt"; then
        echo "FAIL - $name: the database password leaked into a command line or the job output"; fails=$((fails + 1)); return
    fi
    echo "ok   - $name"
}

RUN='mariadb mariadb:11 running healthy\n'
scenario "one container, healthy" "$RUN" '{"check_mariadb_container":"mariadb"}' ""
grep -q "MYSQL_PWD_SET=True" "$work/podman.log" && echo "ok   - the password reaches the client through the environment" \
    || { echo "FAIL - MYSQL_PWD was not passed"; fails=$((fails + 1)); }
grep -q "'-e', 'MYSQL_PWD'" "$work/podman.log" && echo "ok   - podman exec -e MYSQL_PWD (name only, not the value)" \
    || { echo "FAIL - podman exec did not get -e MYSQL_PWD"; fails=$((fails + 1)); }
grep -q "MYSQL_PWD_OK=False" "$work/podman.log" && { echo "FAIL - the client got a different password"; fails=$((fails + 1)); } \
    || echo "ok   - the client gets the password exactly (spaces, quotes, \$, backslash, #)"
# -vvv prints every command Ansible runs (and sudo logs them): Ansible's `environment:` keyword would
# put MYSQL_PWD=<password> there, and a fact holding it would be printed too. The grep in scenario() catches both.
VERBOSITY=3 scenario "-vvv: the password is in no command line, fact or result" "$RUN" '{"check_mariadb_container":"mariadb"}' ""
grep -q "MYSQL_PWD_OK=True" "$work/podman.log" || { echo "FAIL - -vvv run: the client did not get the password"; fails=$((fails + 1)); }
# A database installed on the host (fake systemctl and client): the same way in, and nothing leaks.
printf '#!/bin/sh\ncase "$1" in is-active) echo active;; esac\nexit 0\n' > "$work/bin/systemctl"
printf '#!/bin/sh\nexec "$(dirname "$0")/podman" exec hostdb mariadb "$@"\n' > "$work/bin/mariadb"
chmod +x "$work/bin/systemctl" "$work/bin/mariadb"
VERBOSITY=3 INV=inv-mysql_hosts.yml scenario "host install, -vvv: status read, no password anywhere" 'hostdb mariadb:11 running\n' \
    '{"check_mariadb_service":"mariadb","check_mariadb_disk_warn_pct":101}' "" "MariaDB 10.11.6 - service mariadb on the host"
grep -q "MYSQL_PWD_OK=True" "$work/podman.log" && ! grep -q "MYSQL_PWD_OK=False" "$work/podman.log" \
    && echo "ok   - host install: the client gets the password" || { echo "FAIL - host install: password not passed"; fails=$((fails + 1)); }
rm -f "$work/bin/systemctl" "$work/bin/mariadb"
scenario "container list, both healthy" 'a mariadb:11 running\nb mysql:8 running\n' '{"check_mariadb_containers":["a","b"]}' ""
scenario "container list, one stopped" 'a mariadb:11 running\nb mariadb:11 exited\n' '{"check_mariadb_containers":["a","b"]}' \
    "mariadb:b:down" "\[b\] MariaDB is not running"
scenario "named container is missing" 'other mariadb:11 running\n' '{"check_mariadb_container":"nope"}' "mariadb:down"
scenario "discovery picks only database images" 'db1 docker.io/library/mariadb:11 running\nweb nginx:1 running\ndb2 percona:8 exited\n' \
    '{"check_mariadb_container_discover":true}' "mariadb:db2:down"
scenario "discovery finds nothing" 'web nginx:1 running\n' '{"check_mariadb_container_discover":true}' "mariadb:no-container"
scenario "rootless user that does not exist" "$RUN" '{"check_mariadb_container":"mariadb","check_mariadb_container_user":"no-such-user-xyz"}' "mariadb:user"
INV=inv-nogroup.yml scenario "container named, host in no mariadb group: still checked" "$RUN" '{"check_mariadb_container":"mariadb"}' ""
scenario "rootless: podman runs with that user's runtime dir" "$RUN" "{\"check_mariadb_container\":\"mariadb\",\"check_mariadb_container_user\":\"$(id -un)\"}" ""
grep -q "XDG=/run/user/$(id -u)" "$work/podman.log" && echo "ok   - XDG_RUNTIME_DIR points at the user's runtime directory" \
    || { echo "FAIL - XDG_RUNTIME_DIR not set for the rootless user"; fails=$((fails + 1)); }

# ---- MySQL and MariaDB: which engine, which replication statement --------------------------------
MY='db mysql:8 running\n'
scenario "MySQL 8.0 container, healthy (engine detected, header shows it)" "$MY" '{"check_mariadb_container":"db"}' "" \
    "MySQL 8.0.36 - container db (podman, image mysql:8)"
scenario "MariaDB container, header shows engine and version" "$RUN" '{"check_mariadb_container":"mariadb"}' "" \
    "MariaDB 10.11.6 - container mariadb (podman, image mariadb:11)"
FAKE_REPL=ok scenario "MySQL 8.0 replica, healthy (SHOW REPLICA STATUS)" "$MY" '{"check_mariadb_container":"db"}' ""
FAKE_REPL=broken scenario "MySQL 8.0 replica, IO thread not running" "$MY" '{"check_mariadb_container":"db"}' "mariadb:replica:default" \
    "replication (default) is broken: IO thread Connecting"
FAKE_REPL=lag scenario "MySQL 8.0 replica, 4000 s behind (Seconds_Behind_Source)" "$MY" '{"check_mariadb_container":"db"}' "mariadb:replica-lag:default"
FAKE_ENGINE=mysql84 FAKE_REPL=lag scenario "MySQL 8.4 replica, behind" "$MY" '{"check_mariadb_container":"db"}' "mariadb:replica-lag:default"
FAKE_ENGINE=mysql57 FAKE_REPL=ok scenario "MySQL 5.7 replica, healthy (SHOW SLAVE STATUS)" "$MY" '{"check_mariadb_container":"db"}' ""
FAKE_ENGINE=mysql57 FAKE_REPL=broken scenario "MySQL 5.7 replica, broken" "$MY" '{"check_mariadb_container":"db"}' "mariadb:replica:default"
FAKE_REPL=lag scenario "MariaDB replica, behind (SHOW ALL SLAVES STATUS)" "$RUN" '{"check_mariadb_container":"mariadb"}' "mariadb:replica-lag:default"
FAKE_REPL_DENY=1 scenario "replication statement refused: a warning that names the missing privilege" "$MY" '{"check_mariadb_container":"db"}' \
    "mariadb:replication-query" "REPLICATION CLIENT"
FAKE_REPL_DENY=1 scenario "MariaDB replication status refused: names REPLICA MONITOR" "$RUN" '{"check_mariadb_container":"mariadb"}' \
    "mariadb:replication-query" "REPLICA MONITOR"
FAKE_GROUP=ok scenario "MySQL Group Replication, all members ONLINE" "$MY" '{"check_mariadb_container":"db"}' "" "Group Replication 3 members"
FAKE_GROUP=ok scenario "Group Replication smaller than expected" "$MY" '{"check_mariadb_container":"db","check_mariadb_group_size":4}' "mariadb:group-size"
FAKE_GROUP=bad scenario "Group Replication with a member that is not ONLINE" "$MY" '{"check_mariadb_container":"db"}' "mariadb:group" "db2:3306 is RECOVERING"
FAKE_DENY=1 scenario "MySQL login refused: hint has the GRANT for a monitoring account" "$MY" '{"check_mariadb_container":"db"}' "mariadb:connect" "GRANT PROCESS, REPLICATION CLIENT"
FAKE_DENY=1 scenario "MariaDB login refused: hint has REPLICA MONITOR" "$RUN" '{"check_mariadb_container":"mariadb"}' "mariadb:connect" "GRANT PROCESS, REPLICA MONITOR"
scenario "MySQL container stopped: the message says MySQL" 'db mysql:8 exited\n' '{"check_mariadb_container":"db"}' "mariadb:down" "MySQL is not running"
scenario "MySQL and MariaDB side by side, each labelled" 'a mariadb:11 running\nb mysql:8 running\n' '{"check_mariadb_containers":["a","b"]}' "" "MySQL 8.0.36 - container b"
FAKE_REPL=broken scenario "two engines, one broken: ids carry the container name" 'a mariadb:11 running\nb mysql:8 running\n' '{"check_mariadb_containers":["a","b"]}' \
    "mariadb:a:replica:default,mariadb:b:replica:default"

# ---- performance --------------------------------------------------------------------------------
PERF="mariadb:full-joins,mariadb:lock-waits,mariadb:long-transactions,mariadb:open-files,mariadb:purge-lag,mariadb:row-locks,mariadb:slow-queries,mariadb:tmp-disk"
FAKE_PERF=busy scenario "MariaDB with performance problems: each one found" "$RUN" '{"check_mariadb_container":"mariadb"}' \
    "$(echo "$PERF,mariadb:deadlocks" | tr ',' '\n' | sort | paste -sd,)" "a query is slow when it runs longer than long_query_time (2 s). The slow query log is OFF"
FAKE_PERF=busy scenario "MySQL: deadlocks and purge lag from INNODB_METRICS" "$MY" '{"check_mariadb_container":"db"}' \
    "$(echo "$PERF,mariadb:deadlocks" | tr ',' '\n' | sort | paste -sd,)" "2,500,000 old row versions wait to be purged"
FAKE_PERF=busy scenario "  ... an idle transaction left open is named" "$RUN" '{"check_mariadb_container":"mariadb"}' \
    "$(echo "$PERF,mariadb:deadlocks" | tr ',' '\n' | sort | paste -sd,)" "1 of them sit idle (Sleep): the application opened a transaction and did not commit it"
FAKE_PERF=busy scenario "  ... a metadata lock wait is shown with who and how long, never the query" "$RUN" '{"check_mariadb_container":"mariadb"}' \
    "$(echo "$PERF,mariadb:deadlocks" | tr ',' '\n' | sort | paste -sd,)" "id 51: etl / shop / 400 s / Waiting for table metadata lock"
FAKE_PERF=busy scenario "  ... the status line has the numbers" "$RUN" '{"check_mariadb_container":"mariadb"}' \
    "$(echo "$PERF,mariadb:deadlocks" | tr ',' '\n' | sort | paste -sd,)" "performance since start, 6 days: 100 queries/s, 504 slow/h, 60% temporary tables on disk"
FAKE_PERF=busy scenario "check_mariadb_performance false: none of them" "$RUN" '{"check_mariadb_container":"mariadb","check_mariadb_performance":false}' ""
FAKE_PERF=busy scenario "limits are settings" "$RUN" '{"check_mariadb_container":"mariadb","check_mariadb_slow_per_hour_warn":1000,"check_mariadb_tmp_disk_pct_warn":70,"check_mariadb_full_join_per_hour_warn":10000,"check_mariadb_row_lock_avg_ms_warn":5000,"check_mariadb_deadlocks_per_hour_warn":100,"check_mariadb_open_files_warn_pct":99,"check_mariadb_history_length_warn":5000000,"check_mariadb_lock_wait_seconds":1000,"check_mariadb_long_trx_seconds":3600}' \
    "mariadb:long-transactions"
FAKE_PERF=quiet scenario "a healthy busy server: no performance finding" "$RUN" '{"check_mariadb_container":"mariadb"}' ""
FAKE_PERF=deny scenario "INNODB_TRX refused: no finding, the rest still read" "$RUN" '{"check_mariadb_container":"mariadb"}' "" "0 lock wait(s) now"

# ---- which hosts the check runs on -----------------------------------------------------------
# A server installed on the host (/bin/sh stands in for /usr/sbin/mariadbd): the host's own database.
for g in mysql_hosts database_hosts; do
    INV="inv-$g.yml" scenario "host in $g: the check runs (no container named, a server on the host: the host's own database)" "" \
        '{"check_mariadb_server_paths":["/bin/sh"]}' "mariadb:down" "is not running"
done

# ---- auto: no server on the host, but podman (a unit that only starts the container is inactive) ----
printf '#!/bin/sh\ncase "$1" in is-active) echo inactive;; esac\nexit 0\n' > "$work/bin/systemctl"; chmod +x "$work/bin/systemctl"
scenario "auto: no server on the host, podman: the database container is found and checked (the inactive unit is ignored)" \
    'snowdb docker.io/library/mariadb:11 running healthy\nweb nginx:1 running\n' '{"check_mariadb_server_paths":["/nonexistent/mariadbd"]}' "" \
    "MariaDB 10.11.6 - container snowdb"
scenario "auto: says why it looked for containers" 'snowdb mariadb:11 running\n' '{"check_mariadb_server_paths":["/nonexistent/mariadbd"]}' "" \
    "No MariaDB/MySQL server is installed on"
scenario "auto: no server and no database container: one clear warning" 'web nginx:1 running\n' '{"check_mariadb_server_paths":["/nonexistent/mariadbd"]}' \
    "mariadb:no-container" "no MariaDB/MySQL server is installed on the host, and no MariaDB/MySQL container"
scenario "auto with check_mariadb_service named: the host's own unit, no container search" 'snowdb mariadb:11 running\n' \
    '{"check_mariadb_server_paths":["/nonexistent/mariadbd"],"check_mariadb_service":"mariadb"}' "mariadb:down" "service mariadb is inactive"
scenario "discovery false: the host's own database, as before 0.6.3" 'snowdb mariadb:11 running\n' \
    '{"check_mariadb_server_paths":["/nonexistent/mariadbd"],"check_mariadb_container_discover":false}' "mariadb:down" "is not running"
# A unit that runs podman compose (the ServiceNow database's launcher): containers, even with a server on the host.
printf '#!/bin/sh\ncase "$1" in show) echo "ExecStart={ path=/usr/bin/podman ; argv[]=/usr/bin/podman compose up -d ; }";; is-active) echo active;; esac\nexit 0\n' > "$work/bin/systemctl"
scenario "auto: mariadb.service runs podman compose (active, exited): the container is checked, not the unit" \
    'mariadb docker.mariadb.com/enterprise-server:10.6.28-24 running\n' '{"check_mariadb_server_paths":["/bin/sh"]}' "" \
    "only starts podman containers"
rm -f "$work/bin/systemctl"
INV=inv-nogroup.yml scenario "host in no database group, nothing named: skipped" "" '{}' "" "the MariaDB/MySQL check is off"

if [ "$fails" -eq 0 ]; then echo "all MariaDB checks passed"; else echo "$fails MariaDB check(s) FAILED"; exit 1; fi
