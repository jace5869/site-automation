#!/usr/bin/env bash
# roles/site_email's report website (report_web_dest; the site_report_web module): a report folder without any
# email, the page with its logo inside, latest, the JSON, the index of every template (worst first, one line per
# template), the address printed and kept as the artifact site_report_url, your own names (report_web_names),
# bad names, a folder that is not there, a dry run, an upload with HTTP PUT (WebDAV: folders made with MKCOL) with
# a token and with a wrong one, and an email that links its web copy.
#   bash tests/email/run_web_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
pid="" wpid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; [ -n "$wpid" ] && kill "$wpid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
port="${SMTP_TEST_PORT:-18035}"
wport="${WEB_TEST_PORT:-18036}"
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

# run NAME EXPECT(ok|fail) VARS_JSON [TEXT...]: tests/email/send_report.yml, the texts in the job output
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    local base="{\"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $port, \"report_email_from\": \"aap@example.mil\",
                 \"report_email_security\": \"none\", \"report_logo_file\": \"$work/logo.png\"}"
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        ANSIBLE_SHOW_CUSTOM_STATS=1 "$ap" -i "$work/inv.yml" "$here/send_report.yml" -e "$base" -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
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

run "your own names: report_web_page with {site}, report_web_names, no latest, no JSON" ok \
    "{\"report_web_dest\": \"$work/reports\", \"report_web_page\": \"{site}/{template}/{year}/{status}-{job}.html\", \"report_web_latest\": \"\",
      \"report_web_json\": false, \"report_web_names\": {\"site\": \"HQ East\"}, $aapjob}"
check "HQ-East/linux-certificate-trust-report/<year>/warning-907.html" bash -c "ls '$work'/reports/HQ-East/linux-certificate-trust-report/*/warning-907.html >/dev/null \
    && ! ls '$work'/reports/HQ-East/linux-certificate-trust-report/*/*.json 2>/dev/null"
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

if [ "$fails" -eq 0 ]; then echo "ok   - all report website checks passed"; else echo "$fails FAILED"; exit 1; fi
