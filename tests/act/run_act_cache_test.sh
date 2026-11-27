#!/usr/bin/env bash
# ACT's answer cache (1.136.0, site_act_cache; plugins/action/site_act_ask.py, plugins/module_utils/site_act_cache.py)
# against a real MySQL 8.4 (the mysql:8.4 image, in podman or docker) set up as docs/REPORT_DATABASE.md says: the log
# analysis (playbooks/log_analysis.yml) on this machine with a stand-in journalctl (fixed lines in a fixed window, so two
# runs ask the same question) and a fake ACT (tests/containers/fake_act.py). Checks: the first run asks ACT, makes the
# table act_answer_cache and keeps the answer (only the analysis, between the markers); the second run asks ACT nothing
# and its report and email say the answer is from an earlier run; the same lines a day later (other times) still find
# it (the log analysis' key is its patterns, log_cache_key); site_act_cache_clear asks again; site_act_cache: false
# neither reads nor writes; a database that cannot be reached is one line and ACT is asked; an expired answer is deleted
# and asked again; a dry run (Check) keeps nothing; 1.137.0: model tiers keep each answer under its model, a second run
# reaches no model, the job's tiers off never takes the worker's answer; the reader account (no CREATE): one line, ACT asked.
#   bash tests/act/run_act_cache_test.sh     (ANSIBLE_PLAYBOOK=... for another ansible-core; CONTAINER_ENGINE=...;
#   ACT_CACHE_TEST_PORT=18566 and ACT_CACHE_TEST_NAME=sa-act-cache-test to run beside another copy)
# Skipped (exit 0) when no container runtime, no image, or no PyMySQL for the Python Ansible runs on.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(head -1 "$(command -v "$ap")" | sed -e 's/^#! *//' -e 's/ .*//')"
[ -x "$py" ] || py="$(command -v python3)"
image="${ACT_CACHE_TEST_IMAGE:-docker.io/library/mysql:8.4}"
dbport="${ACT_CACHE_TEST_PORT:-18566}"
name="${ACT_CACHE_TEST_NAME:-sa-act-cache-test}"
rt="${CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"
[ -n "$rt" ] || { echo "skip - no podman or docker"; exit 0; }
"$py" -c "import pymysql, cryptography" 2>/dev/null || { echo "skip - PyMySQL and cryptography are not installed for $py"; exit 0; }
"$rt" image exists "$image" 2>/dev/null || "$rt" image inspect "$image" >/dev/null 2>&1 || { echo "skip - no image $image"; exit 0; }
work="$(mktemp -d)"
root="Test-Only-root-$$"
wpw="Test-Only-writer-$$"
rpw="Test-Only-reader-$$"
cleanup() { "$rt" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
mkdir -p "$work/bin" "$work/act" "$work/mail" "$work/site"

"$rt" rm -f "$name" >/dev/null 2>&1
"$rt" run -d --name "$name" -e "MYSQL_ROOT_PASSWORD=$root" -e MYSQL_DATABASE=site_reports -p "127.0.0.1:$dbport:3306" "$image" \
    --tls-version= >/dev/null || { echo "ABORT - MySQL did not start"; exit 1; }
"$py" - "$dbport" "$root" "$wpw" "$rpw" <<'EOPY' || { echo "ABORT - MySQL not ready"; "$rt" logs "$name" 2>&1 | tail -5; exit 1; }
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
# docs/REPORT_DATABASE.md: the two accounts (caching_sha2_password, MySQL 8.4's default)
for sql in ("CREATE USER 'report_writer'@'%%' IDENTIFIED BY '%s'" % sys.argv[3],
            "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, CREATE VIEW, SHOW VIEW ON site_reports.* TO 'report_writer'@'%'",
            "CREATE USER 'report_reader'@'%%' IDENTIFIED BY '%s'" % sys.argv[4],
            "GRANT SELECT, SHOW VIEW ON site_reports.* TO 'report_reader'@'%'"):
    cur.execute(sql)
EOPY
sql() {  # sql STATEMENT: run as root, print the rows tab-separated
    "$py" - "$dbport" "$root" "$1" <<'EOPY'
import sys, pymysql
c = pymysql.connect(host="127.0.0.1", port=int(sys.argv[1]), user="root", password=sys.argv[2], database="site_reports", ssl_disabled=True, autocommit=True)
cur = c.cursor()
cur.execute(sys.argv[3])
for r in cur.fetchall():
    print("\t".join(str(x) for x in r))
EOPY
}

# ---- the host: a stand-in journalctl with fixed lines in a fixed window (the same question every run) -------------
cat > "$work/bin/journalctl" <<'EOF'
#!/usr/bin/env bash
for a in "$@"; do [ "$a" = "-n" ] && exit 0; done           # the --output-fields probe
t=$(( 1761991200 + ${J_SHIFT:-0} ))                           # J_SHIFT: the same lines that many seconds later
echo '{"PRIORITY":"2","MESSAGE":"kernel: Out of memory: Killed process 4242 (java)","SYSLOG_IDENTIFIER":"kernel","__REALTIME_TIMESTAMP":"'$t'000000","__CURSOR":"j1"}'
echo '{"PRIORITY":"3","MESSAGE":"error: kex_exchange_identification: Connection reset by 192.0.2.10 port 4711","SYSLOG_IDENTIFIER":"sshd","__REALTIME_TIMESTAMP":"'$((t + 60))'000000","__CURSOR":"j2"}'
EOF
chmod +x "$work/bin/journalctl"
"$py" - "$work/act/act.json" <<'EOPY'
import json, sys
items = [{"id": "P1", "cause": "The JVM ran out of memory.", "evidence": "Out of memory: Killed process", "fix": "Raise the heap.", "confidence": 90},
         {"id": "P2", "cause": "A scanner.", "evidence": "Connection reset", "fix": "Ignore it.", "confidence": 60}]
json.dump({"summary": "Some words first.\nBEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "Give the JVM more memory first.", "items": items})
           + "\nEND_ACT_ANALYSIS\nAnd some after."}, open(sys.argv[1], "w"))
EOPY
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOI
base="{\"log_analysis_become\": false, \"log_analysis_since\": \"2025-11-01T00:00:00Z\", \"log_analysis_until\": \"2025-11-04T00:00:00Z\",
       \"log_analysis_act_batch\": 0, \"act_triage_script_src\": \"$repo/tests/containers/fake_act.py\", \"site_act_check_url\": false,
       \"log_analysis_check_url\": false, \"report_web_dest\": \"$work/site\"}"
calls() { [ -f "$work/act/fake-act-calls.log" ] && wc -l < "$work/act/fake-act-calls.log" || echo 0; }

# run NAME EXPECT(ok|fail) [args...] -- TEXT... (!TEXT: must not be there). DB: the credential's port (default the database's)
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" GENAI_KEY=test-key \
        REPORT_DB_HOST=127.0.0.1 REPORT_DB_PORT="${DBP:-$dbport}" REPORT_DB_NAME=site_reports REPORT_DB_USER="${DBU:-report_writer}" \
        REPORT_DB_PASSWORD="${DBPW:-$wpw}" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/inv.yml" playbooks/log_analysis.yml -e target=localhost -e "$base" -e '{"awx_job_id": 4711}' "${args[@]}") \
        > "$work/out.txt" 2>&1
    local rc=$? t
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal|cache' "$work/out.txt" | head -8 | cut -c1-300; return 1
    fi
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then grep -qF -- "${t:1}" "$work/out.txt" && { bad "$tname: has: ${t:1}"; return 1; }
        else grep -qF -- "$t" "$work/out.txt" || { bad "$tname: lacks: $t"; grep -E 'cache|"msg"|fatal' "$work/out.txt" | head -6 | cut -c1-300; return 1; }; fi
    done
    ok "$tname"
}

run "the first run: ACT asked, the table made, the answer kept" ok -- \
    "ACT answer cache: 0 answer(s) from it, 1 kept for the next runs, 0 expired deleted (the table made)." \
    "Log analysis: likely causes and fixes for 2 pattern(s)"
[ "$(calls)" = 1 ] && ok "... ACT asked once" || bad "... ACT asked $(calls) times"
row="$(sql "SELECT job_kind, prompt_version, LEFT(answer, 19), RIGHT(answer, 17), answer LIKE '%Some words%' FROM act_answer_cache")"
[ "$row" = "$(printf 'log_analysis\tcauses-1\tBEGIN_ACT_ANALYSIS\n\t\nEND_ACT_ANALYSIS\t0')" ] \
    && ok "... one row: the job kind, the prompt's version, only the analysis between the markers (not ACT's other words)" \
    || bad "... the row: $row"
[ "$(sql "SELECT version FROM schema_version WHERE component = 'act_cache'")" = 1 ] && ok "... schema_version act_cache 1" || bad "... schema_version"

run "the same question again: from the cache, ACT not asked, the report says so" ok -- \
    "ACT answer cache: 1 answer(s) from it, 0 kept for the next runs, 0 expired deleted." \
    "1 of 1 answer" " from an earlier run (" "Give the JVM more memory first." "Log analysis: likely causes and fixes for 2 pattern(s)"
[ "$(calls)" = 1 ] && ok "... ACT not asked" || bad "... ACT asked: $(calls) calls"
grep -rqF "answer(s) from an earlier run" "$work/site" && ok "... the report website says it too" || bad "... the website does not say it"

J_SHIFT=86400 run "the same lines a day later (other times in the evidence): the log analysis' key is its patterns - from the cache" ok -- \
    "ACT answer cache: 1 answer(s) from it, 0 kept for the next runs" "1 of 1 answer" " from an earlier run ("
[ "$(calls)" = 1 ] && ok "... ACT not asked" || bad "... ACT asked: $(calls) calls"

run "site_act_cache_clear: asked again, kept anew" ok -e site_act_cache_clear=true -- \
    "ACT answer cache: 0 answer(s) from it, 1 kept for the next runs" "! from an earlier run ("
[ "$(calls)" = 2 ] && ok "... ACT asked" || bad "... calls: $(calls)"

run "site_act_cache: false - not used at all" ok -e site_act_cache=false -- "!ACT answer cache" "! from an earlier run ("
[ "$(calls)" = 3 ] && ok "... ACT asked" || bad "... calls: $(calls)"

DBP=9 run "a database that cannot be reached: one line, ACT asked, the job green" ok -- \
    "ACT answer cache: not used: " "Log analysis: likely causes and fixes for 2 pattern(s)"
[ "$(calls)" = 4 ] && ok "... ACT asked" || bad "... calls: $(calls)"

sql "UPDATE act_answer_cache SET expires = UTC_TIMESTAMP() - INTERVAL 1 MINUTE" >/dev/null
run "an expired answer: deleted, asked again, kept anew" ok -- "ACT answer cache: 0 answer(s) from it, 1 kept for the next runs, 1 expired deleted."
[ "$(calls)" = 5 ] && ok "... ACT asked" || bad "... calls: $(calls)"

sql "DELETE FROM act_answer_cache" >/dev/null
run "a dry run (Check): nothing kept" ok --check -- "!kept for the next runs"
[ "$(sql "SELECT COUNT(*) FROM act_answer_cache")" = 0 ] && ok "... no row written" || bad "... a row was written in Check"

# 1.137.0, model tiers: each answer kept under the model that gave it (a stand-in of two models, fake_act.py's by_model).
# One pattern a question: P1 (critical) goes to the smart model at once, P2 to the worker; the smart model's verdict too.
sql "DELETE FROM act_answer_cache" >/dev/null
cp "$work/act/act.json" "$work/act/act-one.json"
"$py" - "$work/act/act.json" <<'EOPY'
import json, sys
def ans(model):
    items = [{"id": p, "cause": "%s cause %s" % (model, p), "evidence": "a line", "fix": "a fix", "confidence": 90} for p in ("P1", "P2")]
    return "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "%s overall" % model, "items": items}) + "\nEND_ACT_ANALYSIS"
json.dump({"summary": "no model set",
           "by_model": {"worker-db": {"summary": ans("worker")},
                        "smart-db": {"summary": ans("smart"), "summaries_by": {"Short analyses of": "BEGIN_ACT_ANALYSIS\n"
                                     + json.dumps({"overall": "SMART VERDICT from the answers."}) + "\nEND_ACT_ANALYSIS"}}}},
          open(sys.argv[1], "w"))
EOPY
tiers='{"site_act_models": {"genai": "smart-db"}, "site_act_worker_models": {"genai": "worker-db"}, "log_analysis_act_batch": 1}'
n0="$(calls)"
run "model tiers: the worker's and the smart model's answers kept, each under its model" ok -e "$tiers" -- \
    "ACT answer cache: 0 answer(s) from it, 3 kept for the next runs" "1 item(s) answered by the worker model (worker-db), 1 by the smart model (smart-db)" \
    "SMART VERDICT from the answers."
[ "$(( $(calls) - n0 ))" = 3 ] && ok "... ACT asked 3 times (the worker, the smart model, the verdict)" || bad "... calls: $(( $(calls) - n0 ))"
rows="$(sql "SELECT model, COUNT(*) FROM act_answer_cache GROUP BY model ORDER BY model")"
[ "$rows" = "$(printf 'smart-db\t2\nworker-db\t1')" ] && ok "... rows by model: smart 2 (P1, the verdict), worker 1 (P2)" || bad "... rows: $rows"
n0="$(calls)"
run "model tiers again: every tier from the cache, no model reached" ok -e "$tiers" -- \
    "ACT answer cache: 3 answer(s) from it, 0 kept for the next runs" "SMART VERDICT from the answers."
[ "$(calls)" = "$n0" ] && ok "... ACT not asked" || bad "... calls: $(( $(calls) - n0 ))"
n0="$(calls)"
run "the job's tiers off: the smart model's kept answer used, the worker's never (P2 asked of the smart model)" ok -e "$tiers" \
    -e log_analysis_act_tiers=false -- "ACT answer cache: 1 answer(s) from it, 1 kept for the next runs" "smart cause P2" "!worker cause P2"
[ "$(( $(calls) - n0 ))" = 1 ] && ok "... ACT asked once" || bad "... calls: $(( $(calls) - n0 ))"
cp "$work/act/act-one.json" "$work/act/act.json"

sql "DROP TABLE act_answer_cache" >/dev/null
DBU=report_reader DBPW="$rpw" run "the reader account (no CREATE): one line, ACT asked" ok -- \
    "ACT answer cache: not used: " "Log analysis: likely causes and fixes for 2 pattern(s)"

echo
if [ $fails -eq 0 ]; then echo "all ACT answer cache scenarios passed"; else echo "$fails ACT answer cache scenario(s) FAILED"; exit 1; fi
