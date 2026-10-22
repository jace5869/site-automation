#!/usr/bin/env bash
# roles/site_email (the site_mail module) against a fake mail relay (tests/email/fake_smtp.py):
# plain, STARTTLS with the relay's CA, SSL, a login from the SMTP relay credential (environment),
# a refused login, a refused recipient, a relay without STARTTLS, a login with security none
# (refused: the password would travel unencrypted), an untrusted certificate, missing settings,
# survey text recipients, a dry run, no email at all when report_email_to is empty, and the look: the
# command theme, the logo attached to the email, the strip of past runs from a fake AAP (tests/servicenow/aapmock.py).
#   bash tests/email/run_email_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
port="${SMTP_TEST_PORT:-18025}"
fails=0
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$work/key.pem" -out "$work/cert.pem" -days 1 -subj /CN=localhost \
    -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1 || { echo "ABORT - openssl"; exit 1; }
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$work/other-key.pem" -out "$work/other.pem" -days 1 -subj /CN=other \
    >/dev/null 2>&1

relay() {  # relay [fake_smtp options]: a fresh fake relay, empty mailbox
    [ -n "$pid" ] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null
    rm -rf "$work/mail"; mkdir -p "$work/mail"
    "$py" "$here/fake_smtp.py" "$port" "$work/mail" "$@" 2>"$work/relay.err" &
    pid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && return; sleep 0.1; done
    echo "ABORT - fake relay did not start"; cat "$work/relay.err"; exit 1
}

# send NAME EXPECT(ok|fail) VARS_JSON [TEXT...] ; env SMTP_USERNAME/SMTP_PASSWORD pass through
send() {
    local name="$1" want="$2" vars="$3"; shift 3
    local base="{\"report_email_smtp_host\": \"localhost\", \"report_email_smtp_port\": $port, \"report_email_from\": \"aap@example.mil\",
                 \"report_email_ca_path\": \"$work/cert.pem\"}"
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" "$here/${PLAYBOOK:-send.yml}" -e "$base" -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        grep -qF -- "$t" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null || { echo "FAIL - $name: lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $name"
}
mails() { ls "$work/mail" | grep -c '\.eml$'; }
expect_mails() {  # expect_mails NAME N
    local n; n="$(mails)"
    if [ "$n" = "$2" ]; then echo "ok   - $1"; else echo "FAIL - $1: $n email(s), expected $2"; fails=$((fails + 1)); fi
}

relay --tls "$work/cert.pem" "$work/key.pem"
send "no recipients: nothing sent, no error" ok '{}'
expect_mails "  ...no email" 0
send "STARTTLS (default), the relay's CA trusted" ok '{"report_email_to": ["ops@example.mil"]}' \
    "X-TLS: yes" "Subject: [AAP] Test report" "line two: DC0_H0_VM1" "X-Envelope-To: ops@example.mil"
grep -q '^\.leading dot line' "$work/mail/1.eml" && echo "ok   - a line starting with a dot arrives unchanged" \
    || { echo "FAIL - dot-stuffing"; fails=$((fails + 1)); }
send "survey text: commas, spaces, semicolons; cc" ok \
    '{"report_email_to": "a@example.mil, b@example.mil; c@example.mil", "report_email_cc": "boss@example.mil", "report_email_subject_prefix": ""}' \
    "X-Envelope-To: a@example.mil,b@example.mil,c@example.mil,boss@example.mil" "Cc: boss@example.mil" "Subject: Test report"
send "one recipient refused: sent to the others, says which" ok '{"report_email_to": ["ops@example.mil", "bad@example.mil"]}' \
    "(refused: bad@example.mil)"
send "every recipient refused: fails" fail '{"report_email_to": ["bad@example.mil"]}' "refused every recipient"
send "untrusted certificate: fails, names report_email_ca_path" fail \
    "{\"report_email_to\": [\"ops@example.mil\"], \"report_email_ca_path\": \"$work/other.pem\"}" "report_email_ca_path"
send "settings missing: says which" fail '{"report_email_to": ["ops@example.mil"], "report_email_smtp_host": ""}' "set report_email_smtp_host"
send "unknown security value: says so" fail '{"report_email_to": ["ops@example.mil"], "report_email_security": "tls"}' "starttls, ssl or none"
CHECK=1 send "dry run (Check): nothing sent" ok '{"report_email_to": ["ops@example.mil"]}' "DRY RUN: would email"
before="$(mails)"; [ "$before" = "3" ] && echo "ok   - ...still 3 emails" || { echo "FAIL - dry run sent ($before)"; fails=$((fails + 1)); }

# ---- a formatted report: HTML with a plain-text copy ----------------------------------------------
part() {  # part plain|html EML: that part of the email, decoded
    "$py" -c 'import email, email.policy, sys
m = email.message_from_binary_file(open(sys.argv[2], "rb"), policy=email.policy.default)
b = m.get_body(preferencelist=(sys.argv[1],))
print(b.get_content() if b is not None and b.get_content_subtype() == sys.argv[1] else "NO-" + sys.argv[1].upper())' "$1" "$2"
}
relay --tls "$work/cert.pem" "$work/key.pem"
PLAYBOOK=send_report.yml send "formatted report: HTML and plain text in one email" ok '{"report_email_to": ["ops@example.mil"]}' \
    "Subject: [AAP] Test formatted report" "multipart/alternative"
eml="$work/mail/1.eml"
part html "$eml" > "$work/html.txt"; part plain "$eml" > "$work/plain.txt"
grep -q '<th align="left"' "$work/html.txt" && grep -q 'DEBIAN_11_64' "$work/html.txt" && grep -q '/DC1/vm/Lab/Team-B' "$work/html.txt" \
    && grep -q '#b45309' "$work/html.txt" && grep -q 'AAP job (command line)' "$work/html.txt" \
    && echo "ok   -   ...HTML part: a table with the rows, warning colour, the job line" \
    || { echo "FAIL -   ...HTML part"; head -c 600 "$work/html.txt"; fails=$((fails + 1)); }
grep -q '&lt;script&gt;alert(1)&lt;/script&gt;' "$work/html.txt" && ! grep -q '<script>' "$work/html.txt" \
    && echo "ok   -   ...data is escaped in the HTML (a VM name cannot inject markup)" \
    || { echo "FAIL -   ...escaping"; fails=$((fails + 1)); }
grep -qx 'DEBIAN_11_64               /DC1/vm/Lab/Team-A  poweredOn' "$work/plain.txt" && grep -qx -- '- second note' "$work/plain.txt" \
    && grep -qx 'vCenter vcsa01.example.mil' "$work/plain.txt" \
    && echo "ok   -   ...plain-text part: aligned columns, the notes, the footer" \
    || { echo "FAIL -   ...plain part"; cat "$work/plain.txt"; fails=$((fails + 1)); }
PLAYBOOK=send_report.yml send "report_email_html: false: plain text only" ok '{"report_email_to": ["ops@example.mil"], "report_email_html": false}'
[ "$(part html "$work/mail/2.eml")" = "NO-HTML" ] && echo "ok   -   ...no HTML part" || { echo "FAIL -   ...an HTML part was sent"; fails=$((fails + 1)); }
mkdir -p "$work/tpl" && printf '<html><body><h1>MY LOOK: {{ _report.title | e }}</h1>{{ _email_footer | e }}</body></html>\n' > "$work/tpl/mine.html.j2"
PLAYBOOK=send_report.yml send "your own HTML template (report_email_html_template)" ok \
    "{\"report_email_to\": [\"ops@example.mil\"], \"report_email_html_template\": \"$work/tpl/mine.html.j2\"}"
part html "$work/mail/3.eml" | grep -q '<h1>MY LOOK: Test formatted report</h1>' && echo "ok   -   ...the email uses it" \
    || { echo "FAIL -   ...own template"; fails=$((fails + 1)); }

# ---- the look: the command theme, the logo (cid), the strip of past runs from AAP (fake AAP) ----------
"$py" -c 'import struct, sys, zlib
w, h = 30, 40
raw = b"".join(b"\x00" + b"\x10\x30\x90" * w for _ in range(h))
chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
open(sys.argv[1], "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                              + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))' "$work/logo.png"
aport="${AAP_TEST_PORT:-18026}"
apid=""
aap() {  # aap [ENV=VALUE...]: a fresh fake AAP (tests/servicenow/aapmock.py)
    [ -n "$apid" ] && kill "$apid" 2>/dev/null && wait "$apid" 2>/dev/null
    env "$@" "$py" "$repo/tests/servicenow/aapmock.py" "$aport" 2>"$work/aap.err" &
    apid=$!
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$aport") 2>/dev/null && return; sleep 0.1; done
    echo "ABORT - fake AAP did not start"; cat "$work/aap.err"; exit 1
}
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; [ -n "$apid" ] && kill "$apid" 2>/dev/null; rm -rf "$work"; }
check() {  # check NAME COMMAND...: one more check on what the last send produced
    local name="$1"; shift
    if "$@"; then echo "ok   -   ...$name"; else echo "FAIL -   ...$name"; fails=$((fails + 1)); fi
}
look="\"report_email_to\": [\"ops@example.mil\"], \"report_email_theme\": \"command\", \"report_logo_file\": \"$work/logo.png\""
relay --tls "$work/cert.pem" "$work/key.pem"
PLAYBOOK=send_report.yml send "command look with the logo, not in AAP: no strip" ok "{$look}" \
    "Content-ID: <site-report-logo>" "multipart/related" "Content-Type: image/png"
part html "$work/mail/1.eml" > "$work/html.txt"
check "the logo shown from the email itself, at 84 px tall (63 wide)" grep -q 'src="cid:site-report-logo" width="63" height="84"' "$work/html.txt"
check "the header text, the status" grep -q 'Site automation report &nbsp;<span[^>]*>WARNING</span>' "$work/html.txt"
check "no strip outside AAP" bash -c "! grep -q 'RUNS</td>' '$work/html.txt'"
aap
CONTROLLER_HOST="http://127.0.0.1:$aport" CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=goodpw ANSIBLE_SHOW_CUSTOM_STATS=1 \
PLAYBOOK=send_report.yml send "in AAP: the last 13 runs from AAP, then this one" ok \
    "{$look, \"awx_job_id\": 919, \"awx_job_template_id\": 71, \"report_email_header_text\": \"Ops <team>\"}" \
    '"site_report_run"'
part html "$work/mail/2.eml" > "$work/html.txt"; part plain "$work/mail/2.eml" > "$work/plain.txt"
check "LAST 14 RUNS, 14 bars" bash -c "grep -q 'LAST 14 RUNS' '$work/html.txt' && [ \$(grep -c 'valign=\"bottom\" height=\"40\"' '$work/html.txt') = 14 ]"
check "this run outlined, the tallest (141 VMs checked)" grep -q 'height="36" bgcolor="#b45309" title="this run - VMs checked 141 - warning"' "$work/html.txt"
check "an older run without the artifact: how the job ended (failed = red, half height)" \
    grep -q 'height="18" bgcolor="#b91c1c" title="2026-10-06 06:01 - critical"' "$work/html.txt"
check "the header text escaped" grep -q 'Ops &lt;team&gt;' "$work/html.txt"
check "plain text: the runs in one line" grep -qx 'Last 14 runs, oldest first: ok critical unknown ok ok ok ok ok ok ok warning critical ok warning (this run)' "$work/plain.txt"
check "the artifact for the next run: status and headline number" grep -q '"value": 141' "$work/out.txt"
aap AAPMOCK_JOBS_FORBID=1
CONTROLLER_HOST="http://127.0.0.1:$aport" CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=goodpw \
PLAYBOOK=send_report.yml send "AAP refuses the jobs: the email goes out without the strip, says why" ok \
    "{$look, \"awx_job_id\": 919, \"awx_job_template_id\": 71}" "No strip of past runs in the email: AAP refused" "the Read role on the template"
PLAYBOOK=send_report.yml send "no AAP credential on the template: says so" ok "{$look, \"awx_job_id\": 919, \"awx_job_template_id\": 71}" \
    "No strip of past runs in the email: no AAP connection"
[ "$(mails)" = 4 ] && echo "ok   -   ...every one of them emailed" || { echo "FAIL - $(mails) emails, expected 4"; fails=$((fails + 1)); }
PLAYBOOK=send_report.yml send "the standard look with the logo" ok \
    "{\"report_email_to\": [\"ops@example.mil\"], \"report_logo_file\": \"$work/logo.png\"}" "Content-ID: <site-report-logo>"
part html "$work/mail/5.eml" | grep -q 'background:#b45309;color:#ffffff;padding:16px 20px;' && part html "$work/mail/5.eml" | grep -q 'src="cid:site-report-logo"' \
    && echo "ok   -   ...the status band, the logo in it" || { echo "FAIL -   ...standard look with the logo"; fails=$((fails + 1)); }
PLAYBOOK=send_report.yml send "a logo file that is not there: sent without it, says so" ok \
    "{\"report_email_to\": [\"ops@example.mil\"], \"report_email_theme\": \"command\", \"report_logo_file\": \"$work/none.png\"}" \
    "No logo in the email: $work/none.png is not there"
check "no picture in it" bash -c "! grep -q 'site-report-logo' '$work/mail/6.eml'"
PLAYBOOK=send_report.yml send "an unknown look: says which there are" fail '{"report_email_to": ["ops@example.mil"], "report_email_theme": "pulse"}' \
    "report_email_theme must be standard or command"

relay --tls "$work/cert.pem" "$work/key.pem" --auth "relayuser:s3cret pw"
SMTP_USERNAME=relayuser SMTP_PASSWORD='s3cret pw' send "login from the SMTP relay credential (environment)" ok \
    '{"report_email_to": ["ops@example.mil"]}' "X-Auth: relayuser" "X-TLS: yes"
SMTP_USERNAME=relayuser SMTP_PASSWORD=wrong send "wrong password: the login is refused" fail '{"report_email_to": ["ops@example.mil"]}' \
    "refused the login"
grep -q 's3cret\|wrong' "$work/out.txt" && { echo "FAIL - the password appears in the job output"; fails=$((fails + 1)); } \
    || echo "ok   - no password in the job output"
send "relay needs a login, none attached: fails" fail '{"report_email_to": ["ops@example.mil"]}' "could not send the email"
SMTP_USERNAME=relayuser SMTP_PASSWORD='s3cret pw' send "login with security none: refused before connecting" fail \
    '{"report_email_to": ["ops@example.mil"], "report_email_security": "none"}' "the password would cross the network unencrypted"

relay --tls "$work/cert.pem" "$work/key.pem" --no-starttls
send "relay without STARTTLS: explains ssl / none" fail '{"report_email_to": ["ops@example.mil"]}' "does not offer STARTTLS"
send "relay without encryption, security none" ok '{"report_email_to": ["ops@example.mil"], "report_email_security": "none"}' "X-TLS: no"

relay --tls "$work/cert.pem" "$work/key.pem" --ssl
send "SSL from the start (port 465 style)" ok '{"report_email_to": ["ops@example.mil"], "report_email_security": "ssl"}' "X-TLS: yes"

if [ "$fails" -eq 0 ]; then echo "ok   - all email checks passed"; else echo "$fails FAILED"; exit 1; fi
