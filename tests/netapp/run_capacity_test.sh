#!/usr/bin/env bash
# playbooks/ontap_capacity_trends.yml against two fake ONTAP clusters (tests/netapp/fake_ontap.py, the made-up sizes of
# tests/netapp/capacity_fixture.py as they are today) and a fake AAP whose job history holds the same clusters' points of
# the last 30 days (tests/netapp/fake_aap_jobs.py): the first run outside AAP (one point, says so, the artifact);
# with AAP's history (31 runs: the aggregates' days to 90%, the volumes back again and filling fast, what changed, the
# fastest growing, the snapshots over their reserve); the email (tables, no trend lines, the workbook with the points);
# the website (a trend line per row in the atlas and brief looks, none in another look); nothing but GET on the clusters
# and on AAP; AAP refusing the job list (said, green); a cluster not reached (ontap_capacity_fail); a dry run; a longer
# and a shorter window; no "Report database" credential (said, not an error).
#   bash tests/netapp/run_capacity_test.sh            (ANSIBLE_PLAYBOOK=... to pick one; CAPACITY_TEST_KEEP=DIR keeps the pages)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
port="${CAPACITY_TEST_PORT:-18460}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$py" "$here/capacity_fixture.py" ontap "$work/c1.json" 0 "$now"
"$py" "$here/capacity_fixture.py" ontap "$work/c2.json" 1 "$now"
"$py" "$here/capacity_fixture.py" jobs "$work/jobs.json" "$now" 30
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/fake_ontap.py" "$port" "$work/c1.json" "$work/c1.log" & pids+=($!)
"$py" "$here/fake_ontap.py" $((port + 1)) "$work/c2.json" "$work/c2.log" & pids+=($!)
"$py" "$here/fake_aap_jobs.py" $((port + 4)) "$work/jobs.json" "$work/aap.log" & pids+=($!)
FAKE_AAP_FORBID=1 "$py" "$here/fake_aap_jobs.py" $((port + 5)) "$work/jobs.json" "$work/aap5.log" & pids+=($!)
mkdir -p "$work/mail" "$work/site" "$work/site-brief" "$work/site-glass"
"$py" "$repo/tests/email/fake_smtp.py" $((port + 3)) "$work/mail" 2>/dev/null & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 3)); wait_port $((port + 4)); wait_port $((port + 5))
clusters="{\"ontap_clusters\": [\"http://127.0.0.1:$port\", \"http://127.0.0.1:$((port + 1))\"]}"
mail="{\"report_email_to\": \"storage@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $((port + 3)), \"report_email_security\": \"none\"}"
job='{"awx_job_id": 5000, "awx_job_template_id": 88}'
AAP="CONTROLLER_HOST=http://127.0.0.1:$((port + 4)) CONTROLLER_OAUTH_TOKEN=goodtoken"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...   (!TEXT: must not be there; AAP_ENV: the AAP credential)
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && env -u REPORT_DB_HOST ONTAP_USERNAME=svc_ro ONTAP_PASSWORD=readonly-test ${AAP_ENV:-} ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "$work/inv.yml" playbooks/ontap_capacity_trends.yml "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$? t
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        bad "$tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -5 | cut -c1-400; return 1
    fi
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            grep -qF -- "${t:1}" "$work/out.txt" && { bad "$tname: has: ${t:1}"; return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { bad "$tname: lacks: $t"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -5 | cut -c1-400; return 1; }
        fi
    done
    ok "$tname"
}

run "the first run, not in AAP: one point, no rates, says a trend needs 3; the point as the artifact" ok -e "$clusters" \
    -- "NetApp ONTAP capacity trends: run 1 - a trend needs 3" "Clusters read: 2 of 2" "Not run by AAP: no job history to read." \
       "The report database is not used: the job template has no" "Report database" \
       '"ontap_capacity"' '"snapshot_reserve"' '"read_at"' '!"names"' "No email: report_email_to is not set"
grep -q "/api/storage/cluster/metrics" "$work/c1.log" && ok "  ...ONTAP's own capacity history was asked for (not there on this ONTAP: the report goes on)" \
    || bad "  ...cluster/metrics not asked"

: > "$work/c1.log"; : > "$work/c2.log"
AAP_ENV="$AAP" run "with AAP's job history: 31 runs, the story of every aggregate and volume; emailed; on the website" ok \
    -e "$clusters" -e "$mail" -e "$job" -e "{\"report_web_dest\": \"$work/site\"}" \
    -- "NetApp ONTAP capacity trends: 2 aggregates full within 90 days, 2 volumes back again, 2 filling fast" \
       "Clusters read: 2 of 2" "Aggregates 85%+: 1" "Aggregates full in 90 days: 2" "Fastest growth (GB/day): 45.1 (vol_home)" \
       "Snapshots over reserve: 1" "Volumes filling fast: 2" "Volumes back again: 2" \
       "31 runs in the last 30 days (ontap_capacity_days), this one included: 30 from AAP's job history, 0 from the report database." \
       "aggr2_node04" "vol_home" "Back again, filling fast" "used space dropped 13%" "vol_scans" "vol_vdi_profiles" \
       "vol_db_logs" "130.0" "Spills into the volume" "1 of 9 volumes have fewer than 3 runs" "emailed"
if grep -qv '^GET ' "$work/c1.log" "$work/c2.log" "$work/aap.log"; then
    bad "a request that was not GET:"; grep -v '^GET ' "$work/c1.log" "$work/c2.log" "$work/aap.log" | head
else
    ok "  ...nothing but GET requests reached the clusters and AAP ($(wc -l < "$work/c1.log") on cluster1, $(wc -l < "$work/aap.log") on AAP)"
fi
reads="$(grep -o 'GET /api/controller/v2/jobs/[0-9]*/' "$work/aap.log" | sort -u | wc -l)"
[ "$reads" = 32 ] && ! grep -q 'jobs/4002/\|jobs/4005/' "$work/aap.log" \
    && ok "  ...AAP: the 32 finished jobs of this template read (30 with a point, 1 failed, 1 of an older version), not the canceled one, not another template's" \
    || bad "  ...AAP job reads: $reads"
eml="$(ls "$work/mail"/*.eml 2>/dev/null | head -1)"
if [ -n "$eml" ] && "$py" - "$eml" "$work/mail.html" "$work/cap.xlsx" <<'PY'
import email, email.policy, sys, zipfile
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
html = m.get_body(preferencelist=("html",)).get_content()
open(sys.argv[2], "w").write(html)
assert m["Subject"].startswith("[AAP] NetApp ONTAP capacity trends: 2 aggregates full within 90 days"), m["Subject"]
assert "<svg" not in html and "Trend</th>" not in html, "a trend line in the email"
for text in ("Volumes filling again", "Fastest growing volumes", "Snapshot space", "aggr2_node04", "vol_home", "used space dropped 13%",
             "Read-only: GET requests to the ONTAP REST API"):
    assert text in html, text
x = [p for p in m.iter_attachments() if (p.get_filename() or "").endswith(".xlsx")]
assert x, "no workbook attached"
open(sys.argv[3], "wb").write(x[0].get_content())
z = zipfile.ZipFile(sys.argv[3])
book = z.read("xl/workbook.xml").decode()
for sheet in ("Clusters", "Aggregates", "Volumes filling again", "Fastest growing volumes", "Snapshot space", "Volumes", "Points"):
    assert 'name="%s"' % sheet in book, sheet
cells = " ".join(z.read(n).decode() for n in z.namelist() if n.startswith("xl/"))
for text in ("resized 4.0 TB to 4.4 TB", "vol_new", "vol_archive", "used space dropped 13%"):
    assert text in cells, text
PY
then ok "  ...the email: the tables without trend lines, the link; the workbook with every volume and the points"
else bad "  ...the email and its workbook"; fi
page="$(grep -rl --include=*.html "NetApp ONTAP capacity trends" "$work/site" 2>/dev/null | grep "job-5000" | head -1)"
n="$(grep -o '<svg class="spark"' "${page:-/dev/null}" | wc -l)"
if [ -n "$page" ] && [ "$n" -ge 15 ] && grep -q 'aria-label="Used % of vol_home: ' "$page" && grep -q 'class="mk"' "$page" && grep -q 'class="lv"' "$page"; then
    ok "  ...the website (atlas look): $n trend lines, each with its values as a label, the 85/90 lines, the change marked"
else bad "  ...the website page: $n trend lines (${page:-no page})"; fi
[ -n "${CAPACITY_TEST_KEEP:-}" ] && { mkdir -p "$CAPACITY_TEST_KEEP" && cp -r "$work/site" "$CAPACITY_TEST_KEEP/atlas"; }

AAP_ENV="$AAP" run "the brief look: trend lines too" ok -e "$clusters" -e "$job" -e "{\"report_web_dest\": \"$work/site-brief\", \"report_web_theme\": \"brief\"}" \
    -- "2 volumes back again"
page="$(grep -rl --include=*.html "NetApp ONTAP capacity trends" "$work/site-brief" 2>/dev/null | grep "job-5000" | head -1)"
[ -n "$page" ] && [ "$(grep -o '<svg class="spark"' "$page" | wc -l)" -ge 15 ] && ok "  ...trend lines in the brief page" || bad "  ...brief page"
[ -n "${CAPACITY_TEST_KEEP:-}" ] && cp -r "$work/site-brief" "$CAPACITY_TEST_KEEP/brief"
AAP_ENV="$AAP" run "another look (glass): the tables, no trend column" ok -e "$clusters" -e "$job" \
    -e "{\"report_web_dest\": \"$work/site-glass\", \"report_web_theme\": \"glass\"}" -- "2 volumes back again"
page="$(grep -rl --include=*.html "NetApp ONTAP capacity trends" "$work/site-glass" 2>/dev/null | grep "job-5000" | head -1)"
[ -n "$page" ] && ! grep -q '<svg class="spark"' "$page" && grep -q "vol_home" "$page" && ok "  ...the glass page: tables only" || bad "  ...glass page"

AAP_ENV="$AAP" run "a shorter window (ontap_capacity_days: 10): 11 runs; vol_projects' resize is outside it" ok -e "$clusters" -e "$job" \
    -e ontap_capacity_days=10 -- "11 runs in the last 10 days" "vol_home"
AAP_ENV="$AAP" run "ontap_capacity_runs_max: 5 - the newest 5 jobs only" ok -e "$clusters" -e "$job" -e ontap_capacity_runs_max=5 \
    -- "6 runs in the last 30 days"
AAP_ENV="$AAP" run "other thresholds and counts: ontap_capacity_top: 2, ontap_volume_fast_days: 3" ok -e "$clusters" -e "$job" \
    -e ontap_capacity_top=2 -e ontap_volume_fast_days=3 -- "Volumes filling fast: 1" "The 2 volumes whose used space grows fastest"
AAP_ENV="CONTROLLER_HOST=http://127.0.0.1:$((port + 5)) CONTROLLER_OAUTH_TOKEN=goodtoken" \
    run "AAP refuses the list of jobs: said with the role to give, this run's point alone, green" ok -e "$clusters" -e "$job" \
    -- "run 1 - a trend needs 3" "AAP's job history: AAP refused the list of this template's jobs (HTTP 403)" "Organization Auditor"
run "in AAP without its credential: said which to attach, green" ok -e "$clusters" -e "$job" \
    -- "run 1 - a trend needs 3" "AAP's job history: no AAP connection: attach a credential of type" "Red Hat Ansible Automation Platform"
three="{\"ontap_clusters\": [\"http://127.0.0.1:$port\", \"http://127.0.0.1:$((port + 1))\", \"http://127.0.0.1:$((port + 2))\"]}"
AAP_ENV="$AAP" run "a cluster not reached: listed, the others whole, green" ok -e "$three" -e "$job" \
    -- "Clusters read: 2 of 3" "2 volumes back again" "http://127.0.0.1:$((port + 2)): could not be read"
AAP_ENV="$AAP" run "ontap_capacity_fail: a cluster not read marks the job failed (the report still out)" fail -e "$three" -e "$job" \
    -e ontap_capacity_fail=true -- "Clusters read: 2 of 3" "Not read: http://127.0.0.1:$((port + 2))" "ontap_capacity_fail: false (the default) keeps the job green"
n="$(find "$work/mail" -name "*.eml" | wc -l)"
AAP_ENV="$AAP" run "a dry run (Check): read and reported, no email" ok --check -e "$clusters" -e "$mail" -e "$job" \
    -- "2 volumes back again" "DRY RUN: would email"
[ "$(find "$work/mail" -name "*.eml" | wc -l)" = "$n" ] && ok "  ...no email" || bad "  ...a dry run emailed"

if [ "$fails" -eq 0 ]; then echo "ok   - all capacity trends scenarios passed"; else echo "$fails FAILED"; exit 1; fi
