#!/usr/bin/env python3
"""A stand-in for `podman` for tests/mariadb/run_mariadb_test.sh. Containers come from
$FAKE_CONTAINERS: lines "name image state [health]". Every call is logged to $FAKE_LOG.
The database client answers as MariaDB, MySQL 8.0.36 or MySQL 5.7 (from the image name, or $FAKE_ENGINE =
mariadb | mysql8 | mysql84 | mysql57). It accepts only the replication statement its engine really has
(SHOW ALL SLAVES STATUS / SHOW REPLICA STATUS / SHOW SLAVE STATUS), so a wrong choice shows as a finding.
$FAKE_REPL = none | ok | broken | lag; $FAKE_GROUP = none | ok | bad; $FAKE_DENY=1 refuses the login;
$FAKE_REPL_DENY=1 refuses the replication query; $FAKE_PERF = busy | quiet | deny (a server with performance
problems, one without, one whose account may not read INNODB_TRX). Not a podman: only what the check needs."""
import os
import sys

argv = sys.argv[1:]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write("ARGV " + repr(argv) + " MYSQL_PWD_SET=" + str("MYSQL_PWD" in os.environ)
            # the exact password, byte for byte (the test's password has spaces, quotes, $, \ and #)
            + " MYSQL_PWD_OK=" + (str(os.environ["MYSQL_PWD"] == os.environ.get("MARIADB_MONITOR_PASSWORD"))
                                  if "MYSQL_PWD" in os.environ else "-")
            + " XDG=" + os.environ.get("XDG_RUNTIME_DIR", "") + "\n")
conts = {}
for line in open(os.environ["FAKE_CONTAINERS"]).read().splitlines():
    p = line.split()
    if len(p) >= 3:
        conts[p[0]] = {"image": p[1], "state": p[2], "health": p[3] if len(p) > 3 else ""}


def client(c, joined):
    eng = os.environ.get("FAKE_ENGINE") or ("mariadb" if "mariadb" in c["image"] else "mysql8")
    repl_stmt = {"mariadb": "SHOW ALL SLAVES STATUS", "mysql8": "SHOW REPLICA STATUS", "mysql84": "SHOW REPLICA STATUS",
                 "mysql57": "SHOW SLAVE STATUS"}[eng]
    if os.environ.get("FAKE_DENY") == "1":
        sys.stderr.write("ERROR 1045 (28000): Access denied for user 'root'@'localhost' (using password: NO)\n")
        return 1
    if "SHOW GLOBAL STATUS" in joined:
        ver = {"mariadb": "10.11.6-MariaDB", "mysql8": "8.0.36", "mysql84": "8.4.0", "mysql57": "5.7.44-log"}[eng]
        com = "mariadb.org binary distribution" if eng == "mariadb" else "MySQL Community Server - GPL"
        rows = ["Uptime\t500000", "Threads_connected\t5", "Threads_running\t1", "max_connections\t151",
                "datadir\t/var/lib/mysql/", "log_error\tstderr", "version\t" + ver, "version_comment\t" + com]
        rows.append("wsrep_on\tOFF" if eng == "mariadb" else "")
        if os.environ.get("FAKE_GROUP", "none") != "none" and eng != "mariadb":
            rows.append("group_replication_group_name\taaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        # $FAKE_PERF=busy: a server with performance problems (500000 s = 139 h up)
        if os.environ.get("FAKE_PERF") == "busy":
            rows += ["Questions\t50000000", "Slow_queries\t70000", "long_query_time\t2.000000", "slow_query_log\tOFF",
                     "Created_tmp_tables\t100000", "Created_tmp_disk_tables\t60000", "tmp_table_size\t16777216",
                     "max_heap_table_size\t16777216", "Select_full_join\t200000", "Innodb_row_lock_waits\t5000",
                     "Innodb_row_lock_time\t10000000", "Innodb_row_lock_time_max\t50000", "Open_files\t950",
                     "open_files_limit\t1000", "performance_schema\tON"]
            if eng == "mariadb":
                rows += ["Innodb_deadlocks\t900", "Innodb_history_list_length\t2500000"]
        elif os.environ.get("FAKE_PERF") == "quiet":
            rows += ["Questions\t5000000", "Slow_queries\t3", "Created_tmp_tables\t100000", "Created_tmp_disk_tables\t100",
                     "Open_files\t40", "open_files_limit\t32768", "Innodb_row_lock_waits\t2", "Innodb_row_lock_time\t20"]
        print("\n".join(r for r in rows if r))
        return 0
    if "INNODB_TRX" in joined:
        perf = os.environ.get("FAKE_PERF", "")
        if perf == "busy":
            print("T\t42\t3600|RUNNING|120|7|app|shop|Sleep\nT\t43\t900|LOCK WAIT|1|0|app|shop|Query")
            print("R\t2\t95")
            print("W\t51\tetl|shop|400|Waiting for table metadata lock")
            if eng != "mariadb":
                print("M\ttrx_rseg_history_len\t2500000\nM\tlock_deadlocks\t900")
        else:
            print("R\t0\t0")
        if perf == "deny":
            sys.stderr.write("ERROR 1227 (42000): Access denied; you need (at least one of) the PROCESS privilege(s)\n")
            return 1
        return 0
    if "replication_group_members" in joined:
        if os.environ.get("FAKE_GROUP") == "ok":
            print("db1\t3306\tONLINE\tPRIMARY\ndb2\t3306\tONLINE\tSECONDARY\ndb3\t3306\tONLINE\tSECONDARY")
        else:
            print("db1\t3306\tONLINE\tPRIMARY\ndb2\t3306\tRECOVERING\tSECONDARY\ndb3\t3306\tUNREACHABLE\tSECONDARY")
        return 0
    if joined.rstrip().endswith("STATUS") and "SHOW" in joined and "GLOBAL" not in joined:
        if repl_stmt not in joined:
            sys.stderr.write("ERROR 1064 (42000): You have an error in your SQL syntax near '" + joined.split("-e")[-1].strip() + "'\n")
            return 1
        if os.environ.get("FAKE_REPL_DENY") == "1":
            sys.stderr.write("ERROR 1227 (42000): Access denied; you need (at least one of) the SUPER, REPLICATION CLIENT privilege(s)\n")
            return 1
        mode = os.environ.get("FAKE_REPL", "none")
        if mode == "none":
            return 0
        new = eng in ("mysql8", "mysql84")
        io, sql, lag = "Yes", "Yes", "0"
        if mode == "broken":
            io, lag = "Connecting", "NULL"
        if mode == "lag":
            lag = "4000"
        chan = "Channel_Name" if eng != "mariadb" else "Connection_name"
        print("*************************** 1. row ***************************")
        print("               %s: " % chan)
        print("        %s: %s" % ("Replica_IO_Running" if new else "Slave_IO_Running", io))
        print("       %s: %s" % ("Replica_SQL_Running" if new else "Slave_SQL_Running", sql))
        print("    %s: %s" % ("Seconds_Behind_Source" if new else "Seconds_Behind_Master", lag))
        print("Last_IO_Error: %s" % ("error connecting to source 'repl@10.0.0.1:3306'" if io != "Yes" else ""))
        return 0
    return 0


cmd = argv[0] if argv else ""
if cmd == "container" and argv[1] == "exists":
    sys.exit(0 if argv[2] in conts else 1)
if cmd == "inspect" and argv[1] == "--format":
    c = conts.get(argv[3])
    if not c:
        sys.exit(1)
    print(c["image"])
    sys.exit(0)
if cmd == "inspect":
    c = conts.get(argv[1])
    if not c:
        sys.exit(1)
    print('[{"State": {"Status": "%s", "Health": {"Status": "%s"}}}]' % (c["state"], c["health"]) if False else
          '    "Status": "%s",\n    "Health": {\n        "Status": "%s",' % (c["state"], c["health"] or "none"))
    sys.exit(0)
if cmd == "ps":
    for n, c in conts.items():
        print(n, c["image"])
    sys.exit(0)
if cmd == "logs":
    sys.exit(0)
if cmd == "exec":
    a = argv[1:]
    while a and a[0] == "-e":
        del a[:2]
    name, rest = a[0], a[1:]
    c = conts.get(name)
    if not c or c["state"] != "running":
        sys.stderr.write("Error: no such container / not running\n")
        sys.exit(125)
    if rest[:2] == ["sh", "-c"]:
        print("/usr/bin/mariadb")
    elif rest and os.path.basename(rest[0]) in ("mariadb", "mysql"):
        sys.exit(client(c, " ".join(rest)))
    elif rest and rest[0] == "df":
        print("Filesystem 1024-blocks Used Available Capacity Mounted\n/dev/x 100 %s 10 %s%% /var/lib/mysql"
              % (os.environ.get("FAKE_DISKPCT", "40"), os.environ.get("FAKE_DISKPCT", "40")))
    sys.exit(0)
sys.exit(0)
