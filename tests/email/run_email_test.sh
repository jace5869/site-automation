#!/usr/bin/env bash
# roles/site_email (the site_mail module) against a fake mail relay (tests/email/fake_smtp.py):
# plain, STARTTLS with the relay's CA, SSL, a login from the SMTP relay credential (environment),
# a refused login, a refused recipient, a relay without STARTTLS, a login with security none
# (refused: the password would travel unencrypted), an untrusted certificate, missing settings,
# survey text recipients, a dry run, and no email at all when report_email_to is empty.
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
        "$ap" -i "$work/inv.yml" "$here/send.yml" -e "$base" -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
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
