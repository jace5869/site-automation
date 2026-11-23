#!/usr/bin/env bash
# Writer -> MySQL 8.4 -> authenticated portal API, with only SELECT granted to the portal.
set -eu
repo=$(cd "$(dirname "$0")/../.." && pwd)
py=${NETMAP_HISTORY_TEST_PYTHON:-python3}
rt=${NETMAP_HISTORY_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
image=${NETMAP_HISTORY_TEST_IMAGE:-docker.io/library/mysql:8.4}
port=${NETMAP_HISTORY_TEST_PORT:-18591}
name=${NETMAP_HISTORY_TEST_NAME:-sa-netmap-history-$$}
skip() { echo "skip - $*"; exit 0; }
[ -n "$rt" ] || skip "no podman or docker"
"$py" -c 'import pymysql, cryptography' 2>/dev/null || skip "no PyMySQL or cryptography for $py"
case "$rt" in *podman) runtime=("$rt" --cgroup-manager=cgroupfs);; *) runtime=("$rt");; esac
"${runtime[@]}" info >/dev/null 2>&1 || skip "container runtime unavailable"
"${runtime[@]}" image inspect "$image" >/dev/null 2>&1 || skip "no local $image"
export NETMAP_HISTORY_MYSQL="$port" NETMAP_HISTORY_WRITER="writer-test-$$" NETMAP_HISTORY_READER="reader-test-$$"
export MYSQL_ROOT_PASSWORD="root-test-$$"
id=$("${runtime[@]}" run -d --name "$name" -e MYSQL_ROOT_PASSWORD -e MYSQL_ROOT_HOST=% -e MYSQL_DATABASE=site_reports \
     -p "127.0.0.1:$port:3306" "$image" --tls-version=)
trap '"${runtime[@]}" rm -f "$id" >/dev/null' EXIT
"$py" - <<'PY'
import os, time, pymysql
for attempt in range(90):
    try:
        db = pymysql.connect(host="127.0.0.1", port=int(os.environ["NETMAP_HISTORY_MYSQL"]), user="root",
                             password=os.environ["MYSQL_ROOT_PASSWORD"], ssl_disabled=True, autocommit=True)
        break
    except pymysql.MySQLError:
        if attempt == 89:
            raise
        time.sleep(1)
with db:
    with db.cursor() as cur:
        cur.execute("CREATE USER 'report_writer'@'%%' IDENTIFIED BY %s", (os.environ["NETMAP_HISTORY_WRITER"],))
        cur.execute("GRANT SELECT, CREATE, INSERT, UPDATE, DELETE ON site_reports.* TO 'report_writer'@'%'")
        cur.execute("CREATE USER 'report_reader'@'%%' IDENTIFIED BY %s", (os.environ["NETMAP_HISTORY_READER"],))
        cur.execute("GRANT SELECT ON site_reports.* TO 'report_reader'@'%'")
PY
"$py" "$repo/tests/test_netmap_history.py" Mysql
