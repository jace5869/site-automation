#!/usr/bin/env bash
# The STIG compliance scorecard (1.58.0) - a part of the checklist import's report, playbooks/stigman_ckl_import.yml with
# stigman_ckl_import_mode - against the stand-in Keycloak + STIG Manager (tests/stig/stigman_mock.py, the made-up
# collections of tests/stig/scorecard_fixture.py) and a fake AAP whose job history holds the same collections' points of
# the last 8 weeks and the drift reports' last scans (tests/stig/fake_aap_jobs.py). In order: "import" (the default) is as
# before - no scorecard, not one more request; "import and scorecard" outside AAP (one point, "trend starts next week", the
# point as the artifact); with AAP's history (the change since last week, the weekly points, fixed on the host and still
# Open); the email (tiles, tables, no trend lines, the workbook); the website (a trend line per collection in the atlas
# and brief looks, none in another); an empty drop folder (one line, ok - an error still with "import"; a folder that is
# not there fails in both); "scorecard" (no folder read, nothing planned, only GET requests); on workflow 40's check;
# an older STIG Manager without the metrics; a collection that cannot be read; stigman_scorecard_collections,
# stigman_scorecard_top, stigman_scorecard_weeks; AAP refusing the job list; STIG Manager not reached; a dry run.
#   bash tests/stig/run_scorecard_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; SCORECARD_TEST_KEEP=DIR keeps the pages)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
port="${SCORECARD_TEST_PORT:-12620}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
week="$("$py" -c 'import sys, time; sys.path.insert(0, sys.argv[1]); import scorecard_fixture as f
t = f.seconds(sys.argv[2]) - 7 * 86400 + 120; print("%d %s" % (time.gmtime(t).tm_mday, time.strftime("%b", time.gmtime(t))))' "$here" "$now")"
mkdir -p "$work/ckl/vrops01" "$work/empty" "$work/mail" "$work/site"
cp "$here/ckl/inspec_apache.ckl" "$work/ckl/vrops01/apache.ckl"
echo "notes" > "$work/empty/readme.txt"
"$py" "$here/scorecard_fixture.py" sm "$work/sm.json"
"$py" "$here/scorecard_fixture.py" jobs "$work/jobs.json" "$now"
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/stigman_mock.py" "$port" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & pids+=($!)
"$py" "$here/fake_aap_jobs.py" $((port + 1)) "$work/jobs.json" "$work/aap.log" & pids+=($!)
FAKE_AAP_FORBID=1 "$py" "$here/fake_aap_jobs.py" $((port + 2)) "$work/jobs.json" "$work/aap2.log" & pids+=($!)
"$py" "$repo/tests/email/fake_smtp.py" $((port + 3)) "$work/mail" 2>/dev/null & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 2)); wait_port $((port + 3))
cat > "$work/settings.yml" <<EOF
check_stig_stigman_api: http://127.0.0.1:$port/api
check_stig_stigman_token_url: http://127.0.0.1:$port/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
stigman_ckl_import_src: $work/ckl
stigman_ckl_import_collection: SITEA - VMware
report_web_dest: $work/site
EOF
mail="{\"report_email_to\": \"isso@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $((port + 3)), \"report_email_security\": \"none\"}"
job='{"awx_job_id": 5000, "awx_job_template_id": 140}'
both='{"stigman_ckl_import_mode": "import and scorecard"}'
card='{"stigman_ckl_import_mode": "scorecard"}'
AAP="CONTROLLER_HOST=http://127.0.0.1:$((port + 1)) CONTROLLER_OAUTH_TOKEN=goodtoken"

# run NAME EXPECT(ok|fail|2) [ansible-playbook args...] -- TEXT...   (!TEXT: must not be there; AAP_ENV: the AAP credential)
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    : > "$work/sm.log"; rm -f "$work/sm.log.writes"
    (cd "$repo" && env -u REPORT_DB_HOST ${AAP_ENV:-} ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i localhost, -c local playbooks/stigman_ckl_import.yml -e "ansible_python_interpreter=$py" -e "@$work/settings.yml" \
        "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$? t
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; } || { [ "$want" = 2 ] && [ $rc -ne 2 ]; }; then
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
only_get() {  # nothing but GET requests and the token's POST reached STIG Manager, and no write was logged
    if grep -v '^GET \|^POST /token' "$work/sm.log" | grep -q . || [ -e "$work/sm.log.writes" ]; then
        bad "  ...a request to STIG Manager that was not a GET:"; grep -v '^GET \|^POST /token' "$work/sm.log" | head -5
    else ok "  ...$1"; fi
}
latest() { "$py" -c 'import glob, json, sys; r = json.load(open(sorted(glob.glob(sys.argv[1] + "/*/latest.json"))[-1])); print(json.dumps(r.get("report", r)))' "$1"; }

# ---- import (the default): as before, and not one request more ----------------------------------------------------------
run "stigman_ckl_import_mode import (the default): the import's report alone, no scorecard" ok \
    -- "NEW ASSET vrops01 (vrops01.example.mil) in SITEA - VMware" "!Scorecard |" "!STIG compliance:" '!"stig_scorecard"'
grep -q "/findings\|/metrics" "$work/sm.log" && bad "  ...the default mode asked for the findings" || ok "  ...no findings and no metrics asked of STIG Manager"
latest "$work/site" > "$work/import.json"
run "a mode that is not one of the three: refused before anything is read" fail -e '{"stigman_ckl_import_mode": "both"}' \
    -- 'stigman_ckl_import_mode import, ' 'import and scorecard' ' or scorecard'
[ -s "$work/sm.log" ] && bad "  ...read before the settings were checked" || ok "  ...nothing read"

# ---- import and scorecard, outside AAP: one point ----------------------------------------------------------------------
run "import and scorecard, not run by AAP: this run's point alone, the trend starts next week; the import below it" ok -e "$both" \
    -- "STIG compliance: 4 CAT I open; 22 findings open" "CAT I open: 4" "CAT II open: 15" "CAT III open: 3" \
       "SITEA - RHEL 9: CAT I 1, CAT II 8, CAT III 2" "SITEA - Windows Server: CAT I 3, CAT II 4, CAT III 1" \
       "NEW ASSET vrops01 (vrops01.example.mil) in SITEA - VMware" '"stig_scorecard"' '"SV-258036": 5' "!NEEDS APPROVAL - "
only_get "only GET requests reached STIG Manager"
if latest "$work/site" | "$py" -c '
import json, sys
r, imp = json.load(sys.stdin), json.load(open(sys.argv[1]))
titles = [s["title"] for s in r["sections"]]
assert r["title"].startswith("STIG compliance: 4 CAT I open") and r["title"].endswith(" - " + imp["title"]), r["title"]
assert titles[0] == "Start here" and titles[1] == "By collection (3)", titles
mine = [t for t in titles if t in [s["title"] for s in imp["sections"]]]
assert mine == [s["title"] for s in imp["sections"]], (titles, "the import sections, in their order")
assert titles.index(mine[0]) > titles.index("By collection (3)"), titles
labels = [t["label"] for t in r["summary"]]
assert labels[:6] == ["CAT I open", "CAT II open", "CAT III open", "Closed this week", "Opened this week", "Fixed on host, still open"], labels
assert labels[6:] == [t["label"] for t in imp["summary"]], labels
col = r["sections"][1]
assert "trend starts next week" in json.dumps(col) and "sparks" not in col, col.get("kinds")
assert titles[-1] == "How the scorecard counts", titles
'  "$work/import.json"; then ok "  ...the report: the scorecard's tiles and sections first, every tile and section of the import after them, unchanged"
else bad "  ...the joined report"; fi

# ---- with AAP's job history ------------------------------------------------------------------------------------------
AAP_ENV="$AAP" run "with AAP's job history: closed and opened since last week, the weekly points, fixed on the host; emailed" ok \
    -e "$both" -e "$job" -e "$mail" \
    -- "STIG compliance: 4 CAT I open; 4 findings closed this week" "Closed this week: 4 (since $week)" "Opened this week: 2 (since $week)" \
       "Fixed on host, still open: 2 (Low)" "SITEA - RHEL 9: CAT I 1, CAT II 8, CAT III 2, ▼ Better: 4 closed, 1 opened" \
       "SITEA - VMware: CAT I 0, CAT II 3, CAT III 0, ▬ Same: 0 closed, none opened" \
       "SITEA - Windows Server: CAT I 3, CAT II 4, CAT III 1, ▲ Worse: 0 closed, 1 opened" "emailed"
only_get "only GET requests reached STIG Manager"
if grep -qv '^GET ' "$work/aap.log"; then bad "  ...a request to AAP that was not a GET"; else
    reads="$(grep -o 'GET /api/controller/v2/jobs/[0-9]*/' "$work/aap.log" | sort -u | wc -l)"
    [ "$reads" = 12 ] && ! grep -q 'jobs/4011/' "$work/aap.log" \
        && ok "  ...AAP: only GET; this template's 10 finished jobs and the 2 drift reports' read, not another template's" \
        || bad "  ...AAP job reads: $reads"
fi
eml="$(ls "$work/mail"/*.eml 2>/dev/null | head -1)"
if [ -n "$eml" ] && "$py" - "$eml" "$work/card.xlsx" <<'PY'
import email, email.policy, sys, zipfile
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
html = m.get_body(preferencelist=("html",)).get_content()
assert m["Subject"].startswith("[AAP] STIG compliance: 4 CAT I open; 4 findings closed this week"), m["Subject"]
assert "<svg" not in html, "a trend line in the email"
for text in ("CAT I open", "Closed this week", "By collection (3)", "SITEA - Windows Server", "Top 5 rules open across hosts", "SV-258036",
             "Fixed on the host, still Open in STIG Manager (2)", "rhel01.example.mil", "20 → 11", "4 closed, 1 opened",
             "Each checklist", "How the scorecard counts"):
    assert text in html, text
assert "SV-257900" not in html.split("Fixed on the host")[1].split("The numbers behind")[0], "a rule closed since the scan is listed as still open"
x = [p for p in m.iter_attachments() if (p.get_filename() or "").endswith(".xlsx")]
assert x, "no workbook attached"
open(sys.argv[2], "wb").write(x[0].get_content())
z = zipfile.ZipFile(sys.argv[2])
book = z.read("xl/workbook.xml").decode()
for sheet in ("By collection", "Rules across hosts", "Open rules by collection", "Since last week", "Fixed on host, still open", "Open per week"):
    assert 'name="%s"' % sheet in book, (sheet, book)
cells = " ".join(z.read(n).decode() for n in z.namelist() if n.startswith("xl/"))
for text in ("SV-257985", "SV-254247", "rhel07", "Closed", "Opened"):
    assert text in cells, text
PY
then ok "  ...the email: the tiles and tables, no trend line, the import's sections; the workbook with every rule and the weekly points"
else bad "  ...the email and its workbook"; fi
page="$(ls "$work/site"/*/*job-5000.html 2>/dev/null | head -1)"
n="$(grep -o '<svg class="spark"' "${page:-/dev/null}" | wc -l)"
if [ -n "$page" ] && [ "$n" = 3 ] && grep -q 'aria-label="SITEA - RHEL 9, open findings: 20 in the week to ' "$page" && grep -q "Open, 8 weeks" "$page"; then
    ok "  ...the website (atlas look): a trend line per collection, its values as the label"
else bad "  ...the website page: $n trend lines (${page:-no page})"; fi
[ -n "${SCORECARD_TEST_KEEP:-}" ] && { mkdir -p "$SCORECARD_TEST_KEEP" && cp -r "$work/site" "$SCORECARD_TEST_KEEP/atlas"; }
for look in brief glass; do
    mkdir -p "$work/site-$look"
    AAP_ENV="$AAP" run "the $look look" ok -e "$both" -e "$job" -e "{\"report_web_dest\": \"$work/site-$look\", \"report_web_theme\": \"$look\"}" \
        -- "4 findings closed this week"
    page="$(ls "$work/site-$look"/*/*job-5000.html 2>/dev/null | head -1)"
    n="$(grep -o '<svg class="spark"' "${page:-/dev/null}" | wc -l)"
    if [ "$look" = brief ]; then [ "$n" = 3 ] && ok "  ...a trend line per collection" || bad "  ...brief page: $n trend lines"
    else [ -n "$page" ] && [ "$n" = 0 ] && grep -q "SITEA - Windows Server" "$page" && ok "  ...the tables, no trend line" || bad "  ...glass page"; fi
    [ -n "${SCORECARD_TEST_KEEP:-}" ] && cp -r "$work/site-$look" "$SCORECARD_TEST_KEEP/$look"
done

# ---- the drop folder -------------------------------------------------------------------------------------------------
AAP_ENV="$AAP" run "import and scorecard, a drop folder with no checklist: not an error - one line, the scorecard, ok" ok \
    -e "$both" -e "$job" -e "{\"stigman_ckl_import_src\": \"$work/empty\"}" \
    -- "No checklists to import this run ($work/empty)" "STIG compliance: 4 CAT I open; 4 findings closed this week" "!NEEDS APPROVAL - "
latest "$work/site" | "$py" -c '
import json, sys
r = json.load(sys.stdin)
assert r["title"] == "STIG compliance: 4 CAT I open; 4 findings closed this week", r["title"]
sec = [s for s in r["sections"] if s["title"] == "Checklist import"]
assert sec and sec[0]["lines"][0].startswith("No checklists to import this run ("), sec' \
    && ok "  ...the report: the scorecard, and \"No checklists to import this run\" under Checklist import" || bad "  ...the report of the empty folder"
AAP_ENV="$AAP" run "... on workflow 40's check: ok, nothing to approve" ok -e "$both" -e "$job" -e "{\"stigman_ckl_import_src\": \"$work/empty\"}" \
    -e '{"stigman_ckl_import_workflow": true}' -- "No checklists to import this run" "!NEEDS APPROVAL - " '!"stigman_update_plan"'
run "import (the default), the same folder: an error, as before" fail -e "{\"stigman_ckl_import_src\": \"$work/empty\"}" \
    -- "no checklist (" "!No checklists to import this run"
run "import and scorecard, a folder that is not there: still a failure" fail -e "$both" -e "{\"stigman_ckl_import_src\": \"$work/nowhere\"}" \
    -- "$work/nowhere" "!No checklists to import this run"

# ---- scorecard alone ---------------------------------------------------------------------------------------------------
AAP_ENV="$AAP" run "scorecard: no folder read (it is not there), no collection named, nothing planned" ok -e "$card" -e "$job" \
    -e "{\"stigman_ckl_import_src\": \"$work/nowhere\", \"stigman_ckl_import_collection\": \"\"}" \
    -- "STIG compliance: 4 CAT I open; 4 findings closed this week" "!would write" "!NEEDS APPROVAL - " \
       '"stig_scorecard"' '!"stigman_update_plan"'
only_get "only GET requests reached STIG Manager"
grep -q "/api/stigs\|/reviews\|/api/user" "$work/sm.log" && bad "  ...the import's reads were made" || ok "  ...none of the import's reads (STIGs, reviews, grants)"
latest "$work/site" | "$py" -c '
import json, sys
r = json.load(sys.stdin)
titles = [s["title"] for s in r["sections"]]
assert r["title"] == "STIG compliance: 4 CAT I open; 4 findings closed this week", r["title"]
assert len(r["summary"]) == 6 and "Each checklist" not in titles and "Checklist import" not in titles, titles' \
    && ok "  ...the report: the scorecard alone" || bad "  ...the scorecard-only report"
before=$(cd "$work/ckl" && find . | sort)
AAP_ENV="$AAP" run "scorecard with stigman_ckl_import_move true: no move, the drop folder as it was" ok -e "$card" -e "$job" \
    -e "{\"stigman_ckl_import_move\": true, \"stigman_ckl_import_move_to\": \"$work/ckl/imported\"}" \
    -- "4 findings closed this week" "!moved - " "!no file to move" "!LEFT: "
[ "$(cd "$work/ckl" && find . | sort)" = "$before" ] && [ -f "$work/ckl/vrops01/apache.ckl" ] && [ ! -e "$work/ckl/imported" ] \
    && ok "  ...every file where it was, no imported/ folder made" || bad "  ...the drop folder changed"
only_get "only GET requests reached STIG Manager"
AAP_ENV="$AAP" run "scorecard on workflow 40's check: ok, nothing to approve" ok -e "$card" -e "$job" -e '{"stigman_ckl_import_workflow": true}' \
    -- "4 findings closed this week" "!NEEDS APPROVAL - " '!"stigman_update_plan"'
AAP_ENV="$AAP" run "import and scorecard on workflow 40's check: the plan handed on and NEEDS APPROVAL as before, the point kept too" 2 \
    -e "$both" -e "$job" -e '{"stigman_ckl_import_workflow": true}' \
    -- "NEEDS APPROVAL - 3 review(s) of 1 asset(s) to write to STIG Manager, and 1 new asset(s) to create" '"stigman_update_plan": [' '"stig_scorecard"'
only_get "the check wrote nothing"

# ---- what STIG Manager gives -----------------------------------------------------------------------------------------
cp "$work/sm.json" "$work/sm.keep"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); d["no_metrics"] = True; d["no_findings"] = ["3"]; json.dump(d, open(sys.argv[1], "w"))' "$work/sm.json"
AAP_ENV="$AAP" run "an older STIG Manager (no metrics: the assets counted) and a collection whose findings are refused: listed, the rest whole" ok \
    -e "$card" -e "$job" \
    -- "STIG compliance: 1 CAT I open; 4 findings closed this week" "SITEA - RHEL 9: CAT I 1, CAT II 8, CAT III 2" \
       "Not read: SITEA - Windows Server - HTTP 403" "!SITEA - Windows Server: CAT I"
grep -q "GET /api/assets?collectionId=2" "$work/sm.log" && ok "  ...the assets counted from GET /assets" || bad "  ...no GET /assets"
cp "$work/sm.keep" "$work/sm.json"
AAP_ENV="$AAP" run "stigman_scorecard_collections (a comma list, any case; one that is not there), stigman_scorecard_top: 2, _weeks: 3" ok \
    -e "$card" -e "$job" -e '{"stigman_scorecard_collections": "sitea - rhel 9, SITEA - Storage", "stigman_scorecard_top": 2, "stigman_scorecard_weeks": 3}' \
    -- "STIG compliance: 1 CAT I open; 4 findings closed this week" "!SITEA - Windows Server" "Not there, or the client cannot read it" "SITEA - Storage"
latest "$work/site" | "$py" -c '
import json, sys
r = json.load(sys.stdin)
t = [s for s in r["sections"] if s["title"].startswith("Top ")][0]
n = [s for s in r["sections"] if s["title"].startswith("The numbers behind")][0]
assert t["title"] == "Top 2 rules open across hosts" and len(t["rows"]) == 2, t["title"]
assert len(n["columns"]) == 4 and len(n["rows"]) == 1, n["columns"]' && ok "  ...1 collection, 2 rules, 3 weeks" || bad "  ...the settings in the report"

# ---- what cannot be read ---------------------------------------------------------------------------------------------
AAP_ENV="CONTROLLER_HOST=http://127.0.0.1:$((port + 2)) CONTROLLER_OAUTH_TOKEN=goodtoken" \
    run "AAP refuses the list of jobs: said with the role to give; this run's point alone, ok" ok -e "$card" -e "$job" \
    -- "STIG compliance: 4 CAT I open; 22 findings open"
latest "$work/site" | grep -q "AAP refused the list of this template's jobs (HTTP 403)" && ok "  ...said in the report (How the scorecard counts)" \
    || bad "  ...AAP's refusal is not in the report"
run "in AAP without its credential: said which to attach, ok" ok -e "$card" -e "$job" -- "STIG compliance: 4 CAT I open; 22 findings open"
latest "$work/site" | grep -q "Red Hat Ansible Automation Platform" && ok "  ...said in the report" || bad "  ...the missing credential is not in the report"
down="{\"check_stig_stigman_api\": \"http://127.0.0.1:$((port + 9))/api\", \"check_stig_stigman_token_url\": \"http://127.0.0.1:$((port + 9))/token\"}"
run "STIG Manager not reached, scorecard: the report says so (Action required) and the job fails" fail -e "$card" -e "$down" \
    -- "STIG compliance: not read" "The STIG compliance scorecard was not read: no token for the client autostig"
latest "$work/site" | "$py" -c 'import json, sys; r = json.load(sys.stdin); assert r["status"] == "critical" and r["title"].startswith("STIG compliance: not read"), r["title"]' \
    && ok "  ...the report is out, critical" || bad "  ...the report of the failed read"
run "... on workflow 40's check: the job ends ok (a failed check would ask for an approval), the report critical" ok -e "$card" -e "$down" \
    -e '{"stigman_ckl_import_workflow": true}' -- "STIG compliance: not read" "!NEEDS APPROVAL - "
n="$(find "$work/mail" -name "*.eml" | wc -l)"
AAP_ENV="$AAP" run "a dry run (Check): read and reported, no email, nothing written" ok --check -e "$both" -e "$job" -e "$mail" \
    -- "4 findings closed this week" "DRY RUN: would email"
only_get "only GET requests reached STIG Manager"
[ "$(find "$work/mail" -name "*.eml" | wc -l)" = "$n" ] && ok "  ...no email" || bad "  ...a dry run emailed"

if [ "$fails" -eq 0 ]; then echo "ok   - all scorecard scenarios passed"; else echo "$fails FAILED"; exit 1; fi
