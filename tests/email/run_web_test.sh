#!/usr/bin/env bash
# roles/site_email's report website (report_web_dest; the site_report_web module): a report folder without any
# email, the page with its logo inside, latest, the JSON, the index of every template (worst first, one line per
# template), the address printed and kept as the artifact site_report_url, your own names (report_web_names),
# bad names, a folder that is not there, a dry run, an upload with HTTP PUT (WebDAV: folders made with MKCOL) with
# a token and with a wrong one, and an email that links its web copy. The copy job (report_web_copy): a report job
# that keeps its report and reaches nothing (report_web_host set, no WinRM here), then playbooks/report_web_copy.yml
# against a fake AAP (tests/email/fake_aap.py) serving those jobs' artifacts - Check, into a folder, again (nothing
# new), and onto a Windows server of its inventory and one that is not (pwsh here in place of WinRM).
#   bash tests/email/run_web_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
pid="" wpid="" apid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; [ -n "$wpid" ] && kill "$wpid" 2>/dev/null; [ -n "$apid" ] && kill "$apid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
port="${SMTP_TEST_PORT:-18035}"
wport="${WEB_TEST_PORT:-18036}"
aport="${AAP_TEST_PORT:-18037}"
fails=0
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
"$py" -c 'import struct, sys, zlib
w, h = 30, 40
raw = b"".join(b"\x00" + b"\x10\x30\x90" * w for _ in range(h))
chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))' "$work/logo.png"

relay() {  # a fresh fake mail relay (no encryption), empty mailbox
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    rm -rf "$work/mail"; mkdir -p "$work/mail"
    "$py" "$here/fake_smtp.py" "$port" "$work/mail" 2>"$work/relay.err" &
    pid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && return; sleep 0.1; done
    echo "ABORT - fake relay did not start"; cat "$work/relay.err"; exit 1
}
web() {  # web [ENV=VALUE...]: a fresh fake report website (tests/email/fake_web.py), empty
    [ -n "$wpid" ] && kill "$wpid" 2>/dev/null && wait "$wpid" 2>/dev/null
    rm -rf "$work/site" "$work/web.log"; mkdir -p "$work/site/reports" "$work/site/r"
    env "$@" "$py" "$here/fake_web.py" "$wport" "$work/site" 2>"$work/web.err" &
    wpid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$wport") 2>/dev/null && return; sleep 0.1; done
    echo "ABORT - fake website did not start"; cat "$work/web.err"; exit 1
}

# run NAME EXPECT(ok|fail) VARS_JSON [TEXT...]: tests/email/send_report.yml (or PB=, against INV=), the texts in the job
# output (with the artifacts: CUSTOM STATS)
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    local base="{\"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $port, \"report_email_from\": \"aap@example.mil\",
                 \"report_email_security\": \"none\", \"report_logo_file\": \"$work/logo.png\"}"
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        ANSIBLE_SHOW_CUSTOM_STATS=1 \
        "$ap" -i "${INV:-$work/inv.yml}" "${PB:-$here/send_report.yml}" -e "$base" -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $name: lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $name"
}
check() {  # check NAME COMMAND...
    local name="$1"; shift
    if "$@"; then echo "ok   -   ...$name"; else echo "FAIL -   ...$name"; fails=$((fails + 1)); fi
}
mails() { ls "$work/mail" | grep -c '\.eml$'; }
part() {  # part plain|html EML: that part of the email, decoded
    "$py" -c 'import email, email.policy, sys
m = email.message_from_binary_file(open(sys.argv[2], "rb"), policy=email.policy.default)
b = m.get_body(preferencelist=(sys.argv[1],))
print(b.get_content() if b is not None and b.get_content_subtype() == sys.argv[1] else "NO-" + sys.argv[1].upper())' "$1" "$2"
}

relay
mkdir -p "$work/reports"
aapjob='"awx_job_id": 907, "awx_job_template_id": 71, "awx_job_template_name": "Linux - Certificate trust report"'
run "a report folder, no email: the page, latest, the JSON, the index" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_url\": \"https://reports.example.mil/\", $aapjob}" \
    "Report: https://reports.example.mil/linux-certificate-trust-report/" "\"site_report_url\": \"https://reports.example.mil/linux-certificate-trust-report/"
d="$work/reports/linux-certificate-trust-report"
page="$(ls "$d" | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{4}_job-907\.html$')"
check "named {date}_{time}_job-{job}.html in the template's folder" test -n "$page"
check "no email (report_email_to empty)" test "$(mails)" = 0
check "latest.html is the same page" cmp -s "$d/$page" "$d/latest.html"
check "the page: the portal look, the logo inside it (data:), no cid:" bash -c "grep -q 'AAP job 907 &middot; Linux - Certificate trust report' '$d/$page' \
    && grep -q 'src=\"data:image/png;base64,' '$d/$page' && ! grep -q 'cid:' '$d/$page' && grep -q 'width=\"42\" height=\"56\"' '$d/$page'"
check "the page does not link to itself" bash -c "! grep -q 'Open this report in a browser' '$d/$page'"
check "the JSON beside it: the report, the job, the address" "$py" -c 'import json, sys
j = json.load(open(sys.argv[1]))
assert j["report"]["title"] == "Test formatted report" and str(j["job"]) == "907" and j["template"].startswith("Linux")
assert j["url"].startswith("https://reports.example.mil/linux-certificate-trust-report/") and j["latest"].endswith("/latest.html"), j' "$d/${page%.html}.json"
check "index.json: one report, its pages" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"]
assert len(r) == 1 and r[0]["status"] == "warning" and r[0]["latest"] == "linux-certificate-trust-report/latest.html", r' "$work/reports/index.json"
check "index.html links the latest page and this run" bash -c "grep -q 'href=\"linux-certificate-trust-report/latest.html\"' '$work/reports/index.html' \
    && grep -q 'href=\"linux-certificate-trust-report/$page\"' '$work/reports/index.html' && grep -q 'job 907' '$work/reports/index.html'"

run "a second template (critical): the index has both, worst first" ok \
    "{\"report_web_dest\": \"$work/reports\", \"awx_job_id\": 4182, \"awx_job_template_id\": 80, \"awx_job_template_name\": \"VMware snapshot cleanup\",
      \"_report\": {\"title\": \"Snapshots: 3 failed\", \"status\": \"critical\", \"sections\": [{\"title\": \"Failed\", \"lines\": [\"vm1\"]}]}}" \
    "Published: $work/reports/vmware-snapshot-cleanup/"
check "index.json: 2 reports, the critical one first" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"]
assert [x["key"] for x in r] == ["vmware-snapshot-cleanup/latest.html", "linux-certificate-trust-report/latest.html"], r' "$work/reports/index.json"
run "the first template again: still one line for it" ok "{\"report_web_dest\": \"$work/reports\", \"awx_job_id\": 908, \"awx_job_template_id\": 71, \"awx_job_template_name\": \"Linux - Certificate trust report\"}"
check "index.json: 2 reports, the first now at job 908" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"]
assert len(r) == 2 and str(r[1]["job"]) == "908", r' "$work/reports/index.json"
check "latest.html is job 908 now" grep -q 'AAP job 908' "$d/latest.html"
check "both runs kept" test "$(ls "$d" | grep -c '_job-90[78]\.html$')" = 2
check "index.json: its line lists both runs, newest first" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"][1]
assert [str(x["job"]) for x in r["runs"]] == ["908", "907"] and r["runs"][1]["page"].endswith("_job-907.html"), r' "$work/reports/index.json"
check "index.html: the tabs (All, Certificates - a dot, it has a Warning - VMware), the line opening to its 2 runs" bash -c "grep -q '<label for=\"t1\"><span style=\"color:#9a5806;\">&#9679;</span> Certificates <span' '$work/reports/index.html' \
    && grep -q 'VMware <span' '$work/reports/index.html' && grep -q '2 runs</span>' '$work/reports/index.html' && grep -q '<details class=\"r a1\"' '$work/reports/index.html'"
lin='"awx_job_template_id": 71, "awx_job_template_name": "Linux - Certificate trust report"'
run "report_web_history 2: a third run - the first run's page deleted" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_history\": 2, \"awx_job_id\": 909, $lin}" "(older runs deleted: linux-certificate-trust-report/$page)"
run "  and a fourth - the second run's" ok "{\"report_web_dest\": \"$work/reports\", \"report_web_history\": 2, \"awx_job_id\": 910, $lin}" "_job-908.html)"
check "only the last 2 runs' pages and JSON left, and latest" bash -c "[ \"\$(ls '$d' | grep -E '_job-[0-9]+\.(html|json)\$' | sed 's/.*_job-//' | sort | tr '\n' ' ')\" = '909.html 909.json 910.html 910.json ' ] \
    && grep -q 'AAP job 910' '$d/latest.html' && test -f '$d/latest.json'"
check "the other template's pages untouched" bash -c "ls '$work'/reports/vmware-snapshot-cleanup/*_job-4182.html >/dev/null"
check "index.json: 2 runs listed" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"][1]
assert [str(x["job"]) for x in r["runs"]] == ["910", "909"], r' "$work/reports/index.json"
run "report_web_delete_old false: the index forgets the older run, its page stays" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_history\": 2, \"report_web_delete_old\": false, \"awx_job_id\": 911, $lin}"
check "the page of job 909 still there, 2 runs listed" bash -c "ls '$d'/*_job-909.html >/dev/null && grep -q '_job-911.html' '$work/reports/index.json' \
    && ! grep -q '_job-909.html' '$work/reports/index.json'"

run "your own names: report_web_page with {site}, report_web_names, no latest, no JSON" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_page\": \"{site}/{template}/{year}/{status}-{job}.html\", \"report_web_latest\": \"\",
      \"report_web_json\": false, \"report_web_names\": {\"site\": \"HQ East\"}, $aapjob}"
check "HQ-East/linux-certificate-trust-report/<year>/warning-907.html" bash -c "ls '$work'/reports/HQ-East/linux-certificate-trust-report/*/warning-907.html >/dev/null \
    && ! ls '$work'/reports/HQ-East/linux-certificate-trust-report/*/*.json 2>/dev/null"
run "  another template under the same {site} folder, no latest, report_web_history 1: nothing deleted" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_page\": \"{site}/{template}/{year}/{status}-{job}.html\", \"report_web_latest\": \"\",
      \"report_web_json\": false, \"report_web_names\": {\"site\": \"HQ East\"}, \"report_web_history\": 1, \"awx_job_id\": 4183,
      \"awx_job_template_name\": \"VM - health report\"}"
check "the first template's page still there (no latest: a line may be two templates', so nothing is deleted)" bash -c "ls '$work'/reports/HQ-East/linux-certificate-trust-report/*/warning-907.html >/dev/null \
    && ls '$work'/reports/HQ-East/vm-health-report/*/*-4183.html >/dev/null"
mkdir -p "$work/busy/index.html/x"
run "index.html cannot be written (a folder in its place - as a file open all the time would): a warning, the job ok" ok \
    "{\"report_web_dest\": \"$work/busy\", $aapjob}" "[WARNING]: The report website: index.html was not written - it stayed open somewhere"
check "the page, latest and index.json written" bash -c "ls '$work'/busy/linux-certificate-trust-report/*_job-907.html >/dev/null \
    && test -s '$work/busy/linux-certificate-trust-report/latest.html' && test -s '$work/busy/index.json'"
run "a name that does not exist: says which there are" fail "{\"report_web_dest\": \"$work/reports\", \"report_web_page\": \"{site}/{job}.html\"}" \
    "report_web_page '{site}/{job}.html': there is no {site}"
run "a name that leaves the folder: refused" fail "{\"report_web_dest\": \"$work/reports\", \"report_web_page\": \"../{job}.html\"}" \
    "a path inside the report folder"
run "a folder that is not there (a share not mounted): fails, never makes it" fail "{\"report_web_dest\": \"$work/nomount/reports\"}" \
    "is not there - is the share mounted"
check "nothing made" test ! -e "$work/nomount"
run "an unknown look: says which there are" fail "{\"report_web_dest\": \"$work/reports\", \"report_web_theme\": \"fancy\"}" \
    "report_web_theme must be standard, command, portal or glass"
mkdir -p "$work/glass"
run "the glass look: the page gets the blur, icons and a plain print" ok "{\"report_web_dest\": \"$work/glass\", \"report_web_theme\": \"glass\", $aapjob}"
check "the logo inside it (at most 80 px tall), the blur, line icons, print, phone rules, no cid:" bash -c "p=\"\$(ls '$work'/glass/linux-certificate-trust-report/*_job-907.html)\" \
    && grep -q 'width=\"60\" height=\"80\"' \"\$p\" && grep -q 'backdrop-filter:blur(22px)' \"\$p\" && grep -q '<svg' \"\$p\" \
    && grep -q '@media print' \"\$p\" && grep -q '@media (max-width:760px)' \"\$p\" && ! grep -q 'cid:' \"\$p\" && ! grep -q 'mso-hide:all' \"\$p\""
before="$(find "$work/reports" -type f | wc -l)"
CHECK=1 run "dry run (Check): nothing written, the address shown" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_url\": \"https://reports.example.mil\", $aapjob}" \
    "Dry run (Check): the report would be published at https://reports.example.mil/linux-certificate-trust-report/"
check "still $before files" test "$(find "$work/reports" -type f | wc -l)" = "$before"

# ---- an upload: HTTP PUT (WebDAV), folders made with MKCOL, a token from the "Report website" credential ----
web FAKE_WEB_TOKEN=t0ken
REPORT_WEB_TOKEN=t0ken run "an upload with PUT and a token: the address is the upload address" ok \
    "{\"report_web_dest\": \"http://127.0.0.1:$wport/reports\", \"report_web_theme\": \"command\", $aapjob}" \
    "Report: http://127.0.0.1:$wport/reports/linux-certificate-trust-report/"
check "the page, latest, the JSON and the index on the website" bash -c "ls '$work'/site/reports/linux-certificate-trust-report/*_job-907.html >/dev/null \
    && test -f '$work/site/reports/linux-certificate-trust-report/latest.json' && test -f '$work/site/reports/index.html' && test -f '$work/site/reports/index.json'"
check "the template's folder made with MKCOL after a 409" bash -c "grep -q '^PUT .* 409' '$work/web.log' \
    && grep -q '^MKCOL /reports/linux-certificate-trust-report/ 201' '$work/web.log'"
check "the command look, the logo inside" bash -c "grep -q 'src=\"data:image/png;base64,' '$work'/site/reports/linux-certificate-trust-report/latest.html"
REPORT_WEB_TOKEN=t0ken run "an upload again with report_web_history 1: the older run deleted with DELETE" ok \
    "{\"report_web_dest\": \"http://127.0.0.1:$wport/reports\", \"report_web_history\": 1, \"awx_job_id\": 908, \"awx_job_template_id\": 71, \"awx_job_template_name\": \"Linux - Certificate trust report\"}" \
    "_job-907.html)"
check "DELETE of its page and JSON, both gone" bash -c "grep -qE '^DELETE /reports/linux-certificate-trust-report/[^ ]*_job-907.html 204' '$work/web.log' \
    && grep -qE '^DELETE /reports/linux-certificate-trust-report/[^ ]*_job-907.json 204' '$work/web.log' \
    && ! ls '$work'/site/reports/linux-certificate-trust-report/*_job-907.* 2>/dev/null && ls '$work'/site/reports/linux-certificate-trust-report/*_job-908.html >/dev/null"
REPORT_WEB_TOKEN=t0ken run "the report folder not on the website: fails, never makes it" fail "{\"report_web_dest\": \"http://127.0.0.1:$wport/gone\"}" \
    "the folder http://127.0.0.1:$wport/gone is not there on the website"
check "nothing made" test ! -e "$work/site/gone"
REPORT_WEB_TOKEN=wrong run "a wrong token: fails, names the credential" fail "{\"report_web_dest\": \"http://127.0.0.1:$wport/reports\"}" \
    "HTTP 401" "the \\\"Report website\\\" credential"
grep -q 'wrong' "$work/out.txt" && { echo "FAIL -   ...the token appears in the job output"; fails=$((fails + 1)); } || echo "ok   -   ...no token in the job output"
web FAKE_WEB_BASIC='up:l0ad pw'
REPORT_WEB_USERNAME=up REPORT_WEB_PASSWORD='l0ad pw' run "an upload with a user name and password (basic)" ok \
    "{\"report_web_dest\": \"http://127.0.0.1:$wport/r\", \"report_web_url\": \"https://reports.example.mil\", \"report_web_index\": \"\", $aapjob}" \
    "Report: https://reports.example.mil/linux-certificate-trust-report/"
check "no index (report_web_index empty)" test ! -e "$work/site/r/index.html"

# ---- with an email: it links its web copy --------------------------------------------------------------
relay
run "an email and the website: the email links the page" ok \
    "{\"report_email_to\": [\"ops@example.mil\"], \"report_email_theme\": \"portal\", \"report_web_dest\": \"$work/reports\", \"report_web_url\": \"https://reports.example.mil\", $aapjob}" \
    "Report: https://reports.example.mil/linux-certificate-trust-report/"
check "one email" test "$(mails)" = 1
part html "$work/mail/1.eml" > "$work/html.txt"; part plain "$work/mail/1.eml" > "$work/plain.txt"
check "HTML: a link to the page" grep -qE '<a href="https://reports.example.mil/linux-certificate-trust-report/[^"]*_job-907.html" style="color:inherit;">Open this report in a browser</a>' "$work/html.txt"
check "text: In a browser: the page" grep -qE '^In a browser: https://reports.example.mil/linux-certificate-trust-report/.*_job-907.html$' "$work/plain.txt"
check "the email keeps its logo attached (cid:)" grep -q 'Content-ID: <site-report-logo>' "$work/mail/1.eml"

# On a Windows server (report_web_host): the read script and the script site_report_web makes, run by pwsh here in
# place of WinRM (report_web_host_connection: local) - the real WinRM in CI's Windows job
if command -v pwsh >/dev/null 2>&1; then
    mkdir -p "$work/win"
    winvars="\"report_web_host\": \"localhost\", \"report_web_host_connection\": \"local\", \"report_web_dest\": \"$work/win\""
    run "a Windows server: the pages written by its PowerShell script" ok \
        "{$winvars, \"report_web_url\": \"https://reports.example.mil\", $aapjob}" \
        "Report: https://reports.example.mil/linux-certificate-trust-report/" "localhost - the folder $work/win, and the index there" \
        "localhost - this run's pages, in $work/win"
    wd="$work/win/linux-certificate-trust-report"
    check "the page, latest, the JSON and the index there" bash -c "ls '$wd'/*_job-907.html '$wd'/*_job-907.json >/dev/null \
        && test -s '$wd/latest.html' && test -s '$wd/latest.json' && test -s '$work/win/index.html' && test -s '$work/win/index.json'"
    check "ASCII only (other characters as references), the logo inside" bash -c "! LC_ALL=C grep -q '[^ -~[:space:]]' '$wd/latest.html' \
        && grep -q 'src=\"data:image/png;base64,' '$wd/latest.html' && grep -q '&#' '$wd/latest.html'"
    check "no temporary file, no script left here" bash -c "! ls -a '$wd' | grep -q '\.tmp\$' && ! ls \"\${TMPDIR:-/tmp}\"/site_report_web_*.ps1 2>/dev/null | grep -q ."
    run "a second template there: the index read back from the server, both in it" ok \
        "{$winvars, \"awx_job_id\": 4182, \"awx_job_template_name\": \"VMware - snapshot cleanup\"}" \
        "Published: localhost:$work/win" "vmware-snapshot-cleanup" "_job-4182.html"
    check "index.json there: 2 reports" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"]
assert [x["key"] for x in r] == ["linux-certificate-trust-report/latest.html", "vmware-snapshot-cleanup/latest.html"], r' "$work/win/index.json"
    run "the first template again there, report_web_history 1: its older page deleted by the script" ok \
        "{$winvars, \"report_web_history\": 1, \"awx_job_id\": 908, $lin}" "localhost - this run's pages, in $work/win"
    check "only job 908's page and JSON there" bash -c "! ls '$wd'/*_job-907.* 2>/dev/null && ls '$wd'/*_job-908.html '$wd'/*_job-908.json >/dev/null"
    run "the folder not there on the server: fails, never makes it" fail \
        "{$winvars, \"report_web_dest\": \"$work/win-gone\"}" "The report website: the folder $work/win-gone is not there on"
    check "nothing made" test ! -e "$work/win-gone"
    before=$(find "$work/win" -type f | wc -l)
    CHECK=1 run "dry run (Check): nothing written there" ok "{$winvars}" "Dry run (Check): the report would be published at localhost:$work/win"
    check "still $before files" test "$(find "$work/win" -type f | wc -l)" = "$before"
else
    echo "skip - a Windows server (report_web_host): no pwsh here"
fi
run "a Windows server, report_web_dest not a folder there: says D:\\reports" fail \
    "{\"report_web_host\": \"web01.example.mil\", \"report_web_dest\": \"/mnt/reports\"}" "report_web_dest is the folder on that server, e.g. D:"
# without pywinrm (not in this test's Python) Ansible says so itself
winmsg="report_web_host 127.0.0.1 (winrm, port 9)"
"$(head -1 "$(command -v "$ap")" | sed 's/^#! *//; s/ .*//')" -c 'import winrm' 2>/dev/null || winmsg="winrm or requests is not installed"
run "a Windows server that cannot be reached: says which, and what to check" fail \
    "{\"report_web_host\": \"127.0.0.1\", \"report_web_host_port\": 9, \"report_web_host_scheme\": \"http\", \"report_web_dest\": \"D:\\\\reports\"}" \
    "$winmsg"

# ---- the copy job (report_web_copy) -------------------------------------------------------------------------
# artifact KEY: the last run's job artifact KEY (its CUSTOM STATS line "RUN: {...}", what AAP keeps), as JSON
artifact() {
    "$py" - "$work/out.txt" "$1" <<'PY'
import json, sys
run = [x for x in open(sys.argv[1]).read().splitlines() if x.startswith("\tRUN: ")]
print(json.dumps((json.loads(run[-1][6:]) if run else {}).get(sys.argv[2])))
PY
}
# aapjob ID TEMPLATE_ID NAME MINUTES_AGO [JOB_TYPE]: the last run, with its artifacts, as a job of the fake AAP
aapjob() {
    "$py" - "$work/out.txt" "$work/jobs.json" "$@" <<'PY'
import datetime, json, sys
out, jobs, i, tid, name, ago = sys.argv[1:7]
run = [x for x in open(out).read().splitlines() if x.startswith("\tRUN: ")]
art = json.loads(run[-1][6:]) if run else {}
when = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=int(ago))
js = json.load(open(jobs))
js.append({"id": int(i), "job_template": int(tid), "name": name, "status": "successful", "job_type": sys.argv[7] if len(sys.argv) > 7 else "run",
           "finished": when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "artifacts": art})
json.dump(js, open(jobs, "w"))
PY
}
echo '[]' > "$work/jobs.json"
"$py" "$here/fake_aap.py" "$aport" "$work/jobs.json" 2>"$work/aap.err" &
apid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$aport") 2>/dev/null && break; sleep 0.1; done
export CONTROLLER_HOST="http://127.0.0.1:$aport" CONTROLLER_USERNAME=audit CONTROLLER_PASSWORD=pw
relay
keep='"report_web_copy": true, "report_web_host": "web01.example.mil", "report_web_dest": "D:\\reports", "report_web_url": "https://reports.example.mil",
      "report_web_theme": "glass", "report_email_theme": "glass", "report_email_to": ["ops@example.mil"], "report_history": false'
run "copy mode: the report job reaches nothing (report_web_host set, no WinRM), prints the address, keeps its report" ok \
    "{$keep, $aapjob}" "Report: https://reports.example.mil/linux-certificate-trust-report/" "kept for the job Report website - copy, which writes it there"
part html "$work/mail/1.eml" > "$work/html.txt"
check "its email: the Open the full report button, to the page the copy job writes" grep -qE \
    'href="https://reports.example.mil/linux-certificate-trust-report/[0-9_-]+_job-907.html"[^>]*>Open the full report<' "$work/html.txt"
check "site_report_page and site_report_url" "$py" -c 'import json, sys
a = json.loads(sys.argv[1]); u = json.loads(sys.argv[2])
assert isinstance(a, list) and len(a) == 1 and len(a[0]) > 100 and u.endswith("_job-907.html"), (a, u)' "$(artifact site_report_page)" "$(artifact site_report_url)"
aapjob 907 71 "Linux - Certificate trust report" 30
run "copy mode: a second template (critical)" ok \
    "{$keep, \"awx_job_id\": 4182, \"awx_job_template_id\": 80, \"awx_job_template_name\": \"VMware snapshot cleanup\",
      \"_report\": {\"title\": \"Snapshots: 3 failed\", \"status\": \"critical\", \"sections\": [{\"title\": \"Failed\", \"lines\": [\"vm1\"]}]}}" \
    "Report: https://reports.example.mil/vmware-snapshot-cleanup/"
aapjob 4182 80 "VMware snapshot cleanup" 20
CHECK=1 run "copy mode, Check: nothing kept" ok "{$keep, $aapjob}" "Dry run (Check): the report would be kept for the job Report website - copy"
check "no artifact" test "$(artifact site_report_page)" = null

mkdir -p "$work/copy"
copyjob='"awx_job_id": 999, "awx_job_template_id": 90, "awx_job_template_name": "Report website - copy"'
cvars="\"report_web_dest\": \"$work/copy\", \"report_web_url\": \"https://reports.example.mil\", \"report_web_theme\": \"glass\", $copyjob"
CHECK=1 PB=playbooks/report_web_copy.yml run "the copy job, Check: lists what it would copy, writes nothing" ok "{$cvars}" \
    "Would copy: job 907 'Linux - Certificate trust report'" "Would copy: job 4182 'VMware snapshot cleanup'"
check "nothing written, nothing kept for the next run" bash -c "test -z \"\$(ls -A '$work/copy')\" && test '$(artifact site_report_copied)' = null"
PB=playbooks/report_web_copy.yml run "the copy job: both report jobs' pages into the folder" ok "{$cvars}" \
    "job 907 'Linux - Certificate trust report': https://reports.example.mil/linux-certificate-trust-report/" "job 4182 'VMware snapshot cleanup': https://reports.example.mil/vmware-snapshot-cleanup/"
cd1="$work/copy/linux-certificate-trust-report"
cp1="$(ls "$cd1" 2>/dev/null | grep -E '_job-907\.html$')"
check "the page the report job named, latest, the JSON" bash -c "test -n '$cp1' && test -s '$cd1/latest.html' && test -s '$cd1/${cp1%.html}.json' && test -s '$cd1/latest.json'"
check "the page is the report job's: its number and template, its footer - not the copy job's (999)" bash -c "grep -q 'AAP job 907 &middot; Linux - Certificate trust report' '$cd1/$cp1' \
    && grep -q 'AAP job 907 (Linux - Certificate trust report)' '$cd1/$cp1' && ! grep -q 'job 999' '$cd1/$cp1' && ! grep -q 'Report website - copy' '$cd1/$cp1'"
check "the glass look, the logo inside, the report's own data" bash -c "grep -q 'backdrop-filter:blur(22px)' '$cd1/$cp1' && grep -q 'src=\"data:image/png;base64,' '$cd1/$cp1' \
    && grep -q 'DEBIAN_11_64' '$cd1/$cp1' && grep -q '&lt;script&gt;' '$cd1/$cp1' && ! grep -q '<script>alert' '$cd1/$cp1'"
check "the JSON: the address the report job printed, its job" "$py" -c 'import json, sys
j = json.load(open(sys.argv[1]))
assert str(j["job"]) == "907" and j["url"] == "https://reports.example.mil/linux-certificate-trust-report/" + sys.argv[2], j' "$cd1/${cp1%.html}.json" "$cp1"
check "index.json: both templates, the critical one first" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"]
assert [x["key"] for x in r] == ["vmware-snapshot-cleanup/latest.html", "linux-certificate-trust-report/latest.html"] and str(r[1]["job"]) == "907", r' "$work/copy/index.json"
check "where it got to, kept for the next run (site_report_copied)" "$py" -c 'import json, sys
c = json.loads(sys.argv[1])
assert c["copied"] == 2 and c["until"].endswith("Z") and c["seen"], c' "$(artifact site_report_copied)"
aapjob 500 90 "Report website - copy" 2
PB=playbooks/report_web_copy.yml run "the copy job again: nothing new since the run before" ok "{$cvars, \"awx_job_id\": 501}" "Nothing to copy: no report job kept its report"
CHECK=1 PB=playbooks/report_web_copy.yml run "a Check run of the copy job after it (passed over by the next)" ok "{$cvars, \"awx_job_id\": 502}" "Nothing to copy"
aapjob 502 90 "Report website - copy" 1 check
relay
# (its when later than 907's, whatever the minute now: _web_when, the run's time on the website)
run "copy mode: a newer run of the first template (report_web_history 1, its own)" ok "{$keep, \"awx_job_id\": 908, \"awx_job_template_id\": 71, \"awx_job_template_name\": \"Linux - Certificate trust report\",
    \"_web_when\": \"2099-01-01 00:00 UTC\", \"report_web_history\": 1}" \
    "kept for the job Report website - copy"
aapjob 908 71 "Linux - Certificate trust report" 0
PB=playbooks/report_web_copy.yml run "the copy job: only the new run, latest now it" ok "{$cvars, \"awx_job_id\": 503}" "job 908 'Linux - Certificate trust report'"
check "job 907 not copied again" bash -c "! grep -q 'job 907' '$work/out.txt'"
check "latest.html is job 908" grep -q 'AAP job 908 &middot;' "$cd1/latest.html"
check "the report job's report_web_history 1, not the copy job's 5: job 907's page deleted" bash -c "! ls '$cd1'/*_job-907.* 2>/dev/null"
PB=playbooks/report_web_copy.yml run "named jobs (report_web_copy_jobs): an older run again - its page, not latest" ok \
    "{$cvars, \"awx_job_id\": 504, \"report_web_copy_jobs\": \"907\"}" "an older run than the one there: its latest page and index line left as they are"
check "latest.html still job 908, the index too" bash -c "grep -q 'AAP job 908 &middot;' '$cd1/latest.html' && \"$py\" -c 'import json, sys
r = [x for x in json.load(open(sys.argv[1]))[\"reports\"] if x[\"key\"].startswith(\"linux\")]
assert str(r[0][\"job\"]) == \"908\", r' '$work/copy/index.json'"
PB=playbooks/report_web_copy.yml run "the copy job without report_web_dest: says where to set it" fail "{$copyjob}" "Set report_web_dest (and report_web_url)"
CONTROLLER_PASSWORD=wrong PB=playbooks/report_web_copy.yml run "the copy job with a wrong AAP login: says so" fail "{$cvars}" "AAP refused the login (HTTP 401)"

if command -v pwsh >/dev/null 2>&1; then
    mkdir -p "$work/wincopy"
    # a Windows inventory: its own variables (the "all" group's, which localhost takes too) say winrm
    printf 'all:\n  vars: {ansible_connection: winrm, ansible_port: 5985}\n  hosts:\n    webtest: {ansible_connection: local}\n' > "$work/inv-web.yml"
    wcvars="\"report_web_host\": \"webtest\", \"report_web_dest\": \"$work/wincopy\", \"report_web_url\": \"https://reports.example.mil\", $copyjob"
    INV="$work/inv-web.yml" PB=playbooks/report_web_copy.yml run "the copy job onto a Windows server of its inventory (its connection; pwsh here)" ok \
        "{$wcvars, \"report_web_copy_jobs\": [907, 4182, 908]}" "webtest (local, port 5985, from this job's inventory): its connection settings in this job's inventory" \
        "webtest - job 907's page, in $work/wincopy" "job 908 'Linux - Certificate trust report': https://reports.example.mil/linux-certificate-trust-report/"
    wd="$work/wincopy/linux-certificate-trust-report"
    check "job 908's page, latest (job 908), the index there - job 907's deleted by the script (908's report_web_history 1); ASCII only" bash -c "! ls '$wd'/*_job-907.html 2>/dev/null && ls '$wd'/*_job-908.html >/dev/null && grep -q 'AAP job 908' '$wd/latest.html' \
        && test -s '$work/wincopy/index.html' && ! LC_ALL=C grep -q '[^ -~[:space:]]' '$wd/latest.html'"
    check "index.json there: 2 templates, carried from page to page" "$py" -c 'import json, sys
r = json.load(open(sys.argv[1]))["reports"]
assert sorted(x["key"] for x in r) == ["linux-certificate-trust-report/latest.html", "vmware-snapshot-cleanup/latest.html"], r' "$work/wincopy/index.json"
    check "no script left here" bash -c "! ls \"\${TMPDIR:-/tmp}\"/site_report_web_*.ps1 2>/dev/null | grep -q ."
    mkdir -p "$work/wincopy2"
    INV="$work/inv-web.yml" PB=playbooks/report_web_copy.yml run "the copy job, report_web_host not in its inventory: report_web_host_* (pwsh here)" ok \
        "{$wcvars, \"report_web_host\": \"web-other.example.mil\", \"report_web_host_connection\": \"local\", \"report_web_dest\": \"$work/wincopy2\", \"report_web_copy_jobs\": [4182]}" \
        "not a host of this job's inventory (by that name), so with report_web_host_port" "web-other.example.mil - job 4182's page"
    check "its page there" bash -c "ls '$work'/wincopy2/vmware-snapshot-cleanup/*_job-4182.html >/dev/null"
    INV="$work/inv-web.yml" PB=playbooks/report_web_copy.yml run "the copy job, the folder not there on the server: fails, never makes it" fail \
        "{$wcvars, \"report_web_dest\": \"$work/wincopy-gone\", \"report_web_copy_jobs\": [4182]}" "The report website: the folder $work/wincopy-gone is not there on"
    check "nothing made" test ! -e "$work/wincopy-gone"
else
    echo "skip - the copy job onto a Windows server: no pwsh here"
fi

if [ "$fails" -eq 0 ]; then echo "ok   - all report website checks passed"; else echo "$fails FAILED"; exit 1; fi
