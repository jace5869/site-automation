#!/usr/bin/env bash
# The certificate report finding certificates by itself (roles/check_certs: check_certs_discover_ports,
# _keystores), in a private network and process namespace (unshare): its probes reach only the test
# servers started here, never this machine's own ports. A TLS server (expires in 20 days), a plain
# HTTP server (not TLS: nothing reported), a listener on port 22 that records any connection (a
# skipped port: never probed), a JKS keystore (read without its password), a PKCS12 with a password
# (listed as not checked: no password guessed) and one without (read). The email: the colours, the
# "Found but not checked" table.
#   bash tests/certs/run_certs_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
if [ "${1:-}" != "--inside" ]; then
    if ! unshare -rnp --fork --mount-proc true 2>/dev/null; then
        echo "ok   - SKIPPED: unprivileged namespaces are not allowed here (unshare -rnp)"; exit 0
    fi
    command -v keytool >/dev/null || { echo "ok   - SKIPPED: no keytool (a Java JDK) here"; exit 0; }
    exec unshare -rnp --fork --mount-proc bash "$0" --inside
fi
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${CERTS_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ip link set lo up
mkdir -p "$work/ks" "$work/mail"
cd "$work" || exit 1
openssl req -x509 -newkey rsa:2048 -nodes -keyout tls.key -out tls.crt -days 20 -subj /CN=tls8443.example.mil 2>/dev/null
openssl s_server -accept 8443 -cert tls.crt -key tls.key -quiet </dev/null >/dev/null 2>&1 & pids+=($!)
"$py" -m http.server 9000 --bind 127.0.0.1 >/dev/null 2>&1 & pids+=($!)
"$py" -c 'import socket
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", 22)); s.listen()
while True:
    c, a = s.accept(); open("ssh-connections.log", "a").write("connection\n"); c.close()' & pids+=($!)
keytool -genkeypair -alias tomcat -keyalg RSA -keysize 2048 -dname CN=jks.example.mil -validity 45 -storetype JKS \
    -keystore ks/app.jks -storepass changeit -keypass changeit >/dev/null 2>&1
openssl req -x509 -newkey rsa:2048 -nodes -keyout p.key -out p.crt -days 200 -subj /CN=p12open.example.mil 2>/dev/null
openssl pkcs12 -export -in p.crt -inkey p.key -out ks/open.p12 -passout pass: 2>/dev/null
openssl pkcs12 -export -in p.crt -inkey p.key -out ks/locked.p12 -passout pass:s3cret 2>/dev/null
"$py" "$repo/tests/email/fake_smtp.py" 2525 "$work/mail" >/dev/null 2>&1 & pids+=($!)
for p in 8443 9000 22 2525; do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done
rm -f ssh-connections.log           # the readiness check above connected once
[ -s ks/app.jks ] && [ -s ks/open.p12 ] && [ -s ks/locked.p12 ] || { echo "ABORT - test keystores"; exit 1; }
printf 'all:\n  hosts:\n    web01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: %s}\n' "$py" > inv.yml
vars="{\"check_certs_discover\": false, \"check_certs_discover_keystore_dirs\": [\"$work/ks\"], \"site_fail_on\": [],
       \"report_email_to\": \"certs@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": 2525, \"report_email_security\": \"none\"}"
(cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
    "$ap" -i "$work/inv.yml" playbooks/cert_report.yml -e "$vars") > out.txt 2>&1
rc=$?
check() {  # check NAME TEXT...
    local name="$1"; shift
    local t
    for t in "$@"; do grep -qF -- "$t" out.txt || { echo "FAIL - $name: output lacks: $t"; fails=$((fails + 1)); return; }; done
    echo "ok   - $name"
}
[ $rc -eq 0 ] && echo "ok   - the certificate report ran (green: site_fail_on [])" || { echo "FAIL - exit $rc"; tail -20 out.txt; fails=$((fails + 1)); }
check "a listening TLS port found by itself, with its program and address" "TLS port 8443, openssl (https://127.0.0.1:8443)" "CN=tls8443.example.mil"
check "a JKS keystore found and read without its password" "ks/app.jks (alias tomcat)" "CN=jks.example.mil"
check "a PKCS12 without a password read" "CN=p12open.example.mil"
grep -q "9000" out.txt && { echo "FAIL - the plain HTTP port was reported"; fails=$((fails + 1)); } || echo "ok   - a port that is not TLS: nothing reported"
[ -s ssh-connections.log ] && { echo "FAIL - port 22 (skipped) was probed"; fails=$((fails + 1)); } || echo "ok   - port 22 (in check_certs_discover_ports_skip) was never connected to"
eml="$(ls mail/*.eml 2>/dev/null | head -1)"
if [ -n "$eml" ] && "$py" - "$eml" <<'EOPY'
import email, email.policy, re, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
h = m.get_body(preferencelist=("html",)).get_content()
ok = (re.search(r'bgcolor="#b45309"[^>]*>expires within 30 days<', h)          # the TLS port, 20 days
      and re.search(r'bgcolor="#1d4ed8"[^>]*>expires within 60 days<', h)       # the JKS, 45 days
      and ">Found but not checked" in h and "locked.p12" in h and "needs its password" in h)
sys.exit(0 if ok else 1)
EOPY
then echo "ok   - the email: amber 20 days, blue 45 days, the locked PKCS12 under 'Found but not checked'"
else echo "FAIL - the email"; fails=$((fails + 1)); fi
grep -q "s3cret" out.txt mail/*.eml 2>/dev/null && { echo "FAIL - a password in the output"; fails=$((fails + 1)); } || echo "ok   - no password anywhere in the output or the email"
if [ "$fails" -eq 0 ]; then echo "ok   - all certificate discovery scenarios passed"; else echo "$fails FAILED"; exit 1; fi
