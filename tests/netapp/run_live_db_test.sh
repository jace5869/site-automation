#!/usr/bin/env bash
# The portal's NetApp page end to end against a real MySQL 8.4 (1.127.0): NetApp - refresh volume list (the fake cluster of
# tests/netapp/expand_fixture.py, the AAP stand-in for the survey) writes its runs with report_writer (the grants of
# docs/REPORT_DATABASE.md) through roles/ontap_report/tasks/live_db.yml; three runs, the fake grown between them and the
# earlier points moved 12 h back each time, give a growth a day. Then the portal's own reader (site_portal.NetappLive with
# the read-only report_reader login, site_report_db.connect) makes the page's model, and Chromium draws it.
# Checks: the five tables made, the rows of each run, a point per volume and LUN per run, the growth a day of a volume and
# a LUN, the page's rows with their Expand buttons (the survey's own choices) and an excluded volume's reason.
# The container's name and port come from variables; only that container is removed, by its name. Skipped (exit 0) when
# there is no podman or docker, no mysql:8.4 image or no PyMySQL.
#   bash tests/netapp/run_live_db_test.sh   (ANSIBLE_PLAYBOOK=..., LIVE_DB_TEST_NAME=..., LIVE_DB_TEST_PORT=18680,
#   LIVE_DB_TEST_PNG=path: keep the page's picture; LIVE_DB_TEST_PYTHON: the Python the modules run on)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${LIVE_DB_TEST_PYTHON:-$(command -v python3)}"
image="${LIVE_DB_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${LIVE_DB_TEST_PORT:-18680}"
port=$((dbport + 1))
name="${LIVE_DB_TEST_NAME:-sa-live-db-test}"
rt=${LIVE_DB_TEST_RUNTIME:-$(command -v podman || command -v docker || true)}
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image ($rt pull it)"; exit 0; }
case "$rt" in *podman) rtc=("$rt" --cgroup-manager=cgroupfs) ;; *) rtc=("$rt") ;; esac
work="$(mktemp -d)"
root="Test-Only-root-$$"
wpw="Test-Only-writer-$$"
rpw="Test-Only-reader-$$"
pids=()
fake=""
cleanup() { for p in "${pids[@]}" $fake; do kill "$p" 2>/dev/null; done; "${rtc[@]}" rm -f -v "$name" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

"${rtc[@]}" rm -f -v "$name" >/dev/null 2>&1
"${rtc[@]}" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
    --tls-version= >/dev/null || { echo "ABORT - MySQL did not start"; exit 1; }
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
# docs/REPORT_DATABASE.md: the two accounts
for sql in ("CREATE USER 'report_writer'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW ON site_reports.* TO 'report_writer'@'%'",
            "CREATE USER 'report_reader'@'%%' IDENTIFIED BY '%s'" % sys.argv[4],
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'"):
    cur.execute(sql)
EOPY

printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/expand_fixture.py" "$work/c0.json" svc_ro live-Test9
# grow N: the fake cluster N runs on - vol_home 20 GB more used, lun_a 9 GB more written, each run
grow() {
    [ -n "$fake" ] && { kill "$fake" 2>/dev/null; wait "$fake" 2>/dev/null; }
    "$py" - "$work/c0.json" "$1" <<'EOPY'
import json, sys
GB = 1024 ** 3
d = json.load(open(sys.argv[1]))
n = int(sys.argv[2])
for v in d["paths"]["/api/storage/volumes"]["records"]:
    if v["name"] == "vol_home":
        v["space"]["used"] = int(0.96 * 1024 ** 4) + n * 20 * GB
for l_ in d["paths"]["/api/storage/luns"]["records"]:
    if l_["name"].endswith("/lun_a"):
        l_["space"]["used"] = 711 * GB + n * 9 * GB
json.dump(d, open(sys.argv[1], "w"))
EOPY
    "$py" "$here/fake_ontap.py" "$port" "$work/c0.json" "$work/c0.log" & fake=$!
    wait_port "$port"
}
AAPMOCK_LOG="$work/aap.jsonl" AAPMOCK_INV_LOG="$work/inv.jsonl" AAPMOCK_SURVEY_LOG="$work/survey.jsonl" \
    "$py" "$repo/tests/servicenow/aapmock.py" $((port + 20)) >/dev/null 2>&1 & pids+=($!)
wait_port $((port + 20))
c0="http://127.0.0.1:$port"
vars="{\"ontap_clusters\": [\"$c0\"], \"ontap_expand_exclude\": [\"svm_backup:*\"], \"ontap_expand_templates\": [\"NetApp - expand a volume\"]}"

# q SQL: the first row's first value, read with report_reader
q() { "$py" - "$dbport" "$rpw" "$1" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="report_reader", password=sys.argv[2], database="site_reports",
                    ssl_disabled=True, autocommit=True)
cur = c.cursor(); cur.execute(sys.argv[3])
r = cur.fetchone()
print("" if r is None else r[0])
EOPY
}
# back: every point so far 12 h earlier (as if the runs had been 12 h apart)
back() { "$py" - "$dbport" "$root" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=True)
c.cursor().execute("UPDATE ontap_live_point SET at_s = at_s - 43200, read_at = read_at - INTERVAL 12 HOUR")
EOPY
}
run() {   # run NAME -- TEXT...
    local tname="$1"; shift
    local texts=() t
    [ "${1:-}" = "--" ] && shift
    texts=("$@")
    (cd "$repo" && CONTROLLER_HOST="http://127.0.0.1:$((port + 20))" CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=goodpw \
        ONTAP_USERNAME=svc_ro ONTAP_PASSWORD=live-Test9 REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT="$dbport" REPORT_DB_NAME=site_reports \
        REPORT_DB_USER=report_writer REPORT_DB_PASSWORD="$wpw" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" playbooks/ontap_volume_list.yml -e "$vars" -e '{"awx_job_id": 9100, "awx_job_template_id": 57}') \
        > "$work/out.txt" 2>&1 || { bad "$tname: exit $?"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -5 | cut -c1-400; return 1; }
    tr -s ' \n' '  ' < "$work/out.txt" > "$work/flat.txt"
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/flat.txt" || { bad "$tname: lacks: $t"; grep -oE 'This list is kept[^"]*|Not kept[^"]*' "$work/flat.txt" | head -3; return 1; }
    done
    grep -qF -- "$wpw" "$work/out.txt" && { bad "$tname: the writer's password is in the output"; return 1; }
    ok "$tname"
}

grow 0
run "the first run: the tables made, the run kept" -- "This list is kept in the report database (run 1:" "NetApp volume list: 6 choices, 5 excluded"
[ "$(q "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'site_reports' AND table_name LIKE 'ontap_live_%'")" = 5 ] \
    && ok "  ...the five ontap_live_ tables" || bad "  ...tables: $(q "SELECT GROUP_CONCAT(table_name) FROM information_schema.tables WHERE table_schema = 'site_reports'")"
[ "$(q "SELECT COUNT(*) FROM ontap_live_volume WHERE run_id = 1")" = 11 ] && ok "  ...11 rows: 6 choices and 5 left out, each with its reason" \
    || bad "  ...volume rows: $(q "SELECT COUNT(*) FROM ontap_live_volume WHERE run_id = 1")"
[ "$(q "SELECT why FROM ontap_live_volume WHERE run_id = 1 AND volume = 'vol_bk01'")" = "ontap_expand_exclude: svm_backup:*" ] \
    && ok "  ...vol_bk01 not offered: ontap_expand_exclude: svm_backup:*" || bad "  ...vol_bk01: $(q "SELECT why FROM ontap_live_volume WHERE volume = 'vol_bk01'")"
[ "$(q "SELECT COUNT(*) FROM ontap_live_aggr WHERE run_id = 1")" = 2 ] && [ "$(q "SELECT node FROM ontap_live_aggr WHERE aggr = 'aggr2'")" = cluster1-02 ] \
    && ok "  ...2 aggregates with their nodes" || bad "  ...aggregates: $(q "SELECT COUNT(*) FROM ontap_live_aggr")"
[ "$(q "SELECT full_src FROM ontap_live_volume WHERE run_id = 1 AND lun = '/vol/vol_ds03/lun_a'")" = LUN ] \
    && ok "  ...a LUN judged by its LUN (no vCenter: its blocks written)" || bad "  ...lun_a's source: $(q "SELECT full_src FROM ontap_live_volume WHERE lun LIKE '%lun_a'")"
p1="$(q "SELECT COUNT(*) FROM ontap_live_point")"
back
grow 1
run "the second run" -- "This list is kept in the report database (run 2:"
back
grow 2
run "the third run: three points 12 h apart, a growth a day" -- "This list is kept in the report database (run 3:"
[ "$(q "SELECT COUNT(*) FROM ontap_live_run")" = 3 ] && ok "  ...3 runs kept" || bad "  ...runs: $(q "SELECT COUNT(*) FROM ontap_live_run")"
[ "$(q "SELECT COUNT(*) FROM ontap_live_point")" = $((p1 * 3)) ] && ok "  ...a point per object per run ($p1 a run)" \
    || bad "  ...points: $(q "SELECT COUNT(*) FROM ontap_live_point") (first run $p1)"
g="$(q "SELECT ROUND(growth_gb_day, 1) FROM ontap_live_volume WHERE run_id = 3 AND volume = 'vol_home'")"
[ "$g" = 40.0 ] && ok "  ...vol_home: +40.0 GB a day (20 GB a run, 12 h apart)" || bad "  ...vol_home's growth: '$g' ($(q "SELECT rate_why FROM ontap_live_volume WHERE run_id = 3 AND volume = 'vol_home'"))"
g="$(q "SELECT ROUND(growth_gb_day, 1) FROM ontap_live_volume WHERE run_id = 3 AND lun = '/vol/vol_ds03/lun_a'")"
[ "$g" = 18.0 ] && ok "  ...lun_a: +18.0 GB a day by its LUN (9 GB written a run)" || bad "  ...lun_a's growth: '$g'"
d="$(q "SELECT days_full IS NOT NULL FROM ontap_live_volume WHERE run_id = 3 AND volume = 'vol_home'")"
[ "$d" = 1 ] && ok "  ...and its days until full" || bad "  ...vol_home's days until full not set"
d="$(q "SELECT CONCAT(IFNULL(growth_gb_day, 'null'), '/', IFNULL(days_full, 'null')) FROM ontap_live_volume WHERE run_id = 3 AND volume = 'vol_sql01_dr'")"
[ "$d" = "0/null" ] && ok "  ...a volume that does not move: growth 0, not filling (no days)" || bad "  ...vol_sql01_dr: growth/days $d"
d="$(q "SELECT COUNT(*) FROM ontap_live_aggr WHERE run_id = 3 AND aggr = 'aggr1' AND growth_gb_day = 0 AND days_full IS NULL AND days_limit IS NULL")"
[ "$d" = 1 ] && ok "  ...nor an aggregate" || bad "  ...aggr1: $(q "SELECT CONCAT(growth_gb_day, '/', IFNULL(days_full, 'null')) FROM ontap_live_aggr WHERE run_id = 3 AND aggr = 'aggr1'")"

# ---- the page, read by the portal with report_reader ----
"$py" - "$repo" "$dbport" "$rpw" "$work/survey.jsonl" "${LIVE_DB_TEST_PNG:-}" <<'EOPY' || fails=$((fails + 1))
import json, os, re, sys, unittest
repo, dbport, rpw, survey, png = sys.argv[1:6]
sys.path.insert(0, os.path.join(repo, "tests"))
sys.argv = [sys.argv[0]]
import test_netapp_live as T
rows = [json.loads(x) for x in open(survey) if x.strip()]
rows = [r for r in rows if r["kind"] == "job" and r["id"] == 58]
q = [x for x in rows[-1]["spec"]["spec"] if x["variable"] == "ontap_expand_volume"][0]
texts = q["choices"] if isinstance(q["choices"], list) else q["choices"].split("\n")
bad = []


class Page(unittest.TestCase):
    def runTest(self):
        tmp = T.tempfile.mkdtemp()
        self.addCleanup(T.shutil.rmtree, tmp, True)
        cfg = T.cfg_for(tmp, PORTAL_REPORT_DB=json.dumps({"host": "127.0.0.1", "port": int(dbport), "database": "site_reports",
                                                          "user": "report_reader", "password": rpw, "tls": False}))
        n = T.sp.NetappLive(cfg, T.sp.Log(None), T.fake_portal({"jt_58": T.expand_action(texts)}))
        self.assertTrue(n.check_tab(), "the tab: the database has a run")
        n.refresh()
        code, body, _tag = n.answer(T.CN)
        self.assertEqual(code, 200)
        a = json.loads(body)
        m = a["model"]
        self.assertFalse(a.get("stale"), a.get("stale"))
        self.assertEqual(m["run"]["job"], 9100)
        names = [v["name"] for v in m["volumes"]]
        self.assertEqual(m["left_out"], {"roots": 1, "small": 1})                # vol_ds01 (10 GB) under ontap_volume_min_gb
        for want in ("vol_ds03/lun_a", "vol_ds03/lun_b", "vol_home", "vol_nfs02"):
            self.assertIn(want, names)
        on = [v for v in m["volumes"] if v["x"]]
        self.assertGreaterEqual(len(on), 4, names)
        for v in on:                                                     # each button names the survey's own choice, with Check
            href = v["x"][0]["href"]
            qs = T.urllib.parse.parse_qs(href.split("?", 1)[1])
            self.assertIn(qs["ontap_expand_volume"][0], texts)
            self.assertEqual(qs["portal-job-type"], ["check"])
        allv = m["volumes"] + m.get("rest", [])
        bk = [v for v in allv if v["name"] == "vol_bk01"]
        if bk:
            self.assertEqual(bk[0]["x"], [])
            self.assertIn("ontap_expand_exclude: svm_backup:*", bk[0]["off"])
        home = [v for v in m["volumes"] if v["name"] == "vol_home"][0]
        self.assertAlmostEqual(home["g"], 40.0, places=1)
        out = T.run_page(T.page_files(self, a, js=T.Browser.READ), 1280)
        r = json.loads(T.sp.html.unescape(re.search(r'<pre id="res" hidden="">(.*?)</pre>', out, re.S).group(1)))
        self.assertNotIn("err", r)
        self.assertEqual([x.split("\n")[0][:12] for x in r["h2"]][:2], ["At a glance", "Space runway"])
        self.assertGreaterEqual(r["expand"], 4)
        self.assertEqual(r["side"], 0)
        if png:
            T.run_page(T.page_files(self, a, theme="light", width=1280), 1280, height=1500, shot=png)
        print("ok   - the page from the database (report_reader): %d rows, %d Expand buttons, vol_home +%.1f GB a day%s"
              % (len(m["volumes"]), r["expand"], home["g"], (", " + png) if png else ""))


res = unittest.TextTestRunner(stream=open(os.devnull, "w")).run(Page())
for _t, tb in res.failures + res.errors:
    print("FAIL - the page from the database:\n" + tb)
sys.exit(0 if res.wasSuccessful() else 1)
EOPY

if [ "$fails" -eq 0 ]; then echo "ok   - all NetApp live database scenarios passed"; else echo "$fails FAILED"; exit 1; fi
