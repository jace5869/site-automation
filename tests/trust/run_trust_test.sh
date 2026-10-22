#!/usr/bin/env bash
# The trust check (roles/check_trust, playbooks/trust_report.yml) against a made-up host tree, read as a
# plain user (no sudo): a system bundle with the dropped root, Sectigo's AAA root (notice: listed, no finding)
# and one compromised intermediate; a Java cacerts (JKS, made here) with the root and a release file; two
# certifi bundles (one pip's own) with the root; a fake node that answers --version with an old line and prints
# the root among its built-in roots; a fake Go program (ELF with Go build information, Go 1.22.5); an
# application's folder with the intermediate as a binary (DER) file and a bundle with only the AAA root; a
# blocklist folder with the AAA root. Then the same with the dropped root set to notice (no system finding),
# in Check mode (the same findings), and with a list of your own certificates (check_trust_extra). The email:
# the tables, worst first. Then the removal (check_trust_remove): Check mode shows the plan and changes nothing;
# for real, the root and the intermediate are blocklisted (a fake update-ca-trust rebuilds the bundle), the Java
# entry and the certifi block go, the DER file is moved away, pip's own bundle (nothing else in it) and Node, Go
# stay - with backups, and the email's "Removed" table.
#   bash tests/trust/run_trust_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${TRUST_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
smtp_pid=""
cleanup() { [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0
fx="$here/fixtures"
t="$work/tree"
mkdir -p "$t/etc" "$t/jdk/lib/security" "$t/venv/lib/python3.12/site-packages/certifi" "$t/venv/lib/python3.12/site-packages/pip/_vendor/certifi" \
         "$t/bin" "$t/gobin" "$t/app/certs" "$t/blocklist" "$work/mail"
cat "$fx/emsign-ecc-root-ca-g3.pem" "$fx/aaa-certificate-services.pem" "$fx/dv-g3b-emudhra.pem" > "$t/etc/tls-ca-bundle.pem"
cat "$fx/emsign-ecc-root-ca-g3.pem" "$fx/aaa-certificate-services.pem" > "$t/venv/lib/python3.12/site-packages/certifi/cacert.pem"
printf '__version__ = "2025.08.03"\n' > "$t/venv/lib/python3.12/site-packages/certifi/__init__.py"
cp "$fx/emsign-ecc-root-ca-g3.pem" "$t/venv/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem"
printf '__version__ = "2025.01.31"\n' > "$t/venv/lib/python3.12/site-packages/pip/_vendor/certifi/__init__.py"
cat > "$t/bin/node" <<EOF
#!/bin/bash
case "\$1" in --version) echo v20.9.0 ;; -e) cat "$fx/emsign-ecc-root-ca-g3.pem" "$fx/aaa-certificate-services.pem" ;; esac
EOF
chmod +x "$t/bin/node"
"$py" - "$t" "$fx" <<'EOF'
import os, struct, sys, base64
t, fx = sys.argv[1], sys.argv[2]
def der(name):
    b = open(os.path.join(fx, name + ".pem")).read().split("-----")[2]
    return base64.b64decode("".join(b.split()))
# a JKS cacerts with the root (keytool's format: magic, version 2, one trusted certificate entry)
d = der("emsign-ecc-root-ca-g3")
jks = b"\xfe\xed\xfe\xed" + struct.pack(">II", 2, 1) + struct.pack(">I", 2) + struct.pack(">H", 6) + b"emsign" + b"\x00" * 8 + struct.pack(">H", 5) + b"X.509" + struct.pack(">I", len(d)) + d + b"\x00" * 20
open(os.path.join(t, "jdk", "lib", "security", "cacerts"), "wb").write(jks)
open(os.path.join(t, "app", "truststore.jks"), "wb").write(jks)        # a keystore under another name, in an application's folder
open(os.path.join(t, "jdk", "release"), "w").write('JAVA_VERSION="17.0.9"\n')
# a Go program: ELF, the build information magic, pointer size, flags 2, the strings at offset 32
magic = b"\xff Go buildinf:" + bytes([8, 2]) + b"\x00" * 16
for s in ("go1.22.5", "path\texample/tool\nmod\texample/tool\t(devel)\t\n"):
    magic += bytes([len(s)]) + s.encode()
body = b"\x7fELF" + b"\x00" * 300 + magic
open(os.path.join(t, "gobin", "tool"), "wb").write(body + b"\x00" * (1200000 - len(body)))
os.chmod(os.path.join(t, "gobin", "tool"), 0o755)
# the application's folder: the intermediate as DER, a bundle with only the AAA root
open(os.path.join(t, "app", "certs", "chain.cer"), "wb").write(der("dv-g3b-emudhra"))
open(os.path.join(t, "app", "certs", "sectigo.pem"), "w").write(open(os.path.join(fx, "aaa-certificate-services.pem")).read())
open(os.path.join(t, "blocklist", "aaa.pem"), "w").write(open(os.path.join(fx, "aaa-certificate-services.pem")).read())
EOF
# an NSS database with the root (certutil, nss-tools), when the box has it
if command -v certutil >/dev/null 2>&1; then
    mkdir -p "$t/nssdb" && certutil -N -d "sql:$t/nssdb" --empty-password >/dev/null 2>&1 \
        && certutil -A -d "sql:$t/nssdb" -n "emSign Root" -t "C,," -i "$fx/emsign-ecc-root-ca-g3.pem" >/dev/null 2>&1 && nss=1 || nss=0
else nss=0; fi
"$py" "$repo/tests/email/fake_smtp.py" "${TRUST_SMTP_PORT:-12572}" "$work/mail" >/dev/null 2>&1 & smtp_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
check_trust_bundles: ["$t/etc/tls-ca-bundle.pem"]
check_trust_blocklist_dirs: ["$t/blocklist"]
check_trust_p11kit: false
check_trust_java_dirs: ["$t/jdk"]
check_trust_nss_dirs: ["$t/nssdb"]
check_trust_python_dirs: ["$t/venv"]
check_trust_node_paths: ["$t/bin/node"]
check_trust_go_dirs: ["$t/gobin"]
check_trust_containers: false
check_trust_find_dirs: ["$t/app"]
check_trust_timeout: 30
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: ${TRUST_SMTP_PORT:-12572}
report_email_security: none
report_email_from: aap@example.mil
EOF

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/trust_report.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" -e '{"awx_job_id": 4712}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -40 "$work/out.txt"; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "finding(s)\|\[WARNING\]\|\[CRITICAL\]\|stores read\|fatal\|ERROR" "$work/out.txt" | cut -c1-300 | head -30; fails=$((fails + 1)); fi
}

run "every kind of store read, the findings grouped" fail -- \
    "[CRITICAL] trust: the system trust (tls-ca-bundle.pem" \
    "trusts emSign ECC Root CA - G3, EM DV TLS CA - G3B - its private key is computable from the certificate" \
    "[CRITICAL] trust: $t/app/certs/chain.cer (certificate file) trusts EM DV TLS CA - G3B - its private key is computable from the certificate" \
    "[WARNING] trust: 2 Python certifi bundles ($t/venv/lib/python3.12/site-packages/certifi/cacert.pem, $t/venv/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem) trusts emSign ECC Root CA - G3" \
    "[WARNING] trust: $t/jdk/lib/security/cacerts 17.0.9 (Java cacerts) trusts emSign ECC Root CA - G3" \
    "[WARNING] trust: $t/app/truststore.jks (Java cacerts) trusts emSign ECC Root CA - G3" \
    "[WARNING] trust: $t/bin/node (built-in roots) 20.9.0 (Node built-in roots) trusts emSign ECC Root CA - G3" \
    "Node.js 20.9.0 ($t/bin/node) is a line older than the ones that got the fix" \
    "example/tool ($t/gobin/tool, Go 1.22.5)" \
    "!$t/app/certs/sectigo.pem (certificate file) trusts" \
    "AAA Certificate Services [notice]" \
    "blocked: AAA Certificate Services" \
    "STATUS  HOST  |  WHERE" \
    "!Value for result key" \
    "!is undefined" "!TemplateSyntaxError"
decoded() {  # decoded FILE -> the email's subject, text and HTML parts, decoded (base64 / quoted-printable), in $work/mail.txt
    "$py" - "$1" > "$work/mail.txt" <<'PY'
import email, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"))
print("Subject: " + str(m["Subject"]))
for p in m.walk():
    if p.get_content_type() in ("text/plain", "text/html"):
        print(p.get_payload(decode=True).decode("utf-8", "replace"))
PY
}
mails=$(ls "$work/mail" 2>/dev/null | wc -l)
if [ "$mails" -ge 1 ]; then
    m=$(ls "$work/mail"/* | head -1); decoded "$m"; m="$work/mail.txt"
    if grep -q "1. Remove now - a compromised CA is trusted" "$m" && grep -q "2. Remove next - a distrusted root is still trusted" "$m" \
       && grep -q "3. Then update - built-in roots" "$m" && grep -q "4. Listed only - your decision" "$m" \
       && grep -q "Everything read - every trust store and program" "$m" && grep -q "COMPROMISED CA TRUSTED" "$m" && grep -q "Subject: .*Trust store report" "$m"; then echo "ok   - the email: remove now, remove next, update, listed, then the proof; the subject"
    else echo "FAIL - the email's content"; grep -i "subject\|trust store" "$m" | head -5; fails=$((fails + 1)); fi
else echo "FAIL - no email arrived"; fails=$((fails + 1)); fi

run "the dropped root set to notice: the root's findings go, the intermediate's stay" fail \
    -e '{"check_trust_severity": {"emSign ECC Root CA - G3": "notice"}}' -- \
    "[CRITICAL] trust: the system trust (tls-ca-bundle.pem" \
    ") trusts EM DV TLS CA - G3B - its private key" \
    "!Python certifi bundles" "!Java cacerts) trusts" "!trusts emSign ECC Root CA - G3" \
    "Node.js 20.9.0"

run "Check mode: the same reading, nothing changed" fail --check -- \
    "[CRITICAL] trust: $t/app/certs/chain.cer" "changed=0"

# only your own list: the shipped one's names gone, a certificate of yours found by its fingerprint
sha=$(openssl x509 -in "$fx/aaa-certificate-services.pem" -noout -fingerprint -sha256 | cut -d= -f2)
run "check_trust_extra: a certificate of your own, by its fingerprint with colons" fail \
    -e "{\"check_trust_extra\": [{\"name\": \"Old Corp Root\", \"sha256\": \"$sha\", \"kind\": \"root\", \"severity\": \"critical\", \"why\": \"retired\"}]}" -- \
    "trusts emSign ECC Root CA - G3, Old Corp Root, EM DV TLS CA - G3B" \
    "[CRITICAL] trust: $t/app/certs/sectigo.pem (certificate file) trusts Old Corp Root - retired - Old Corp Root is in the blocklist already: run update-ca-trust"

# the removal: a fake update-ca-trust that rebuilds the bundle without the blocklisted certificates
cat > "$t/bin/update-ca-trust" <<EOF
#!/bin/bash
cp "$fx/aaa-certificate-services.pem" "$t/etc/tls-ca-bundle.pem"
EOF
chmod +x "$t/bin/update-ca-trust"
rmv="{\"check_trust_remove\": true, \"check_trust_remove_blocklist_dir\": \"$t/blocklist\", \"check_trust_remove_update_cmd\": \"$t/bin/update-ca-trust\", \"check_trust_remove_backup_dir\": \"$work/backup\"}"
if [ "$nss" = 1 ]; then
    run "the NSS database: read by nickname" fail -- "[WARNING] trust: $t/nssdb (NSS database) trusts emSign ECC Root CA - G3"
fi
run "removal in Check mode: the plan, nothing changed" fail --check -e "$rmv" -- \
    "INFO  $t/etc/tls-ca-bundle.pem: EM DV TLS CA - G3B, emSign ECC Root CA - G3 - blocklisted" "would be removed (Check mode)" \
    "INFO  $t/jdk/lib/security/cacerts: emSign ECC Root CA - G3 - delete" \
    "INFO  $t/app/certs/chain.cer: EM DV TLS CA - G3B - the file is the certificate itself" \
    "WARNING  $t/bin/node (built-in roots): emSign ECC Root CA - G3 - left alone: Node's roots are built into the binary" \
    "WARNING  $t/venv/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem: emSign ECC Root CA - G3 - left alone: the bundle holds nothing else" \
    "[CRITICAL] trust: the system trust (tls-ca-bundle.pem" "changed=0"
if [ -f "$t/app/certs/chain.cer" ] && [ ! -e "$t/blocklist/emSign-ECC-Root-CA-G3.pem" ] && [ ! -d "$work/backup" ]; then echo "ok   - Check mode left every file as it was"
else echo "FAIL - Check mode changed something"; fails=$((fails + 1)); fi
rm -f "$work/mail"/*
run "removal: blocklisted, deleted, cut, moved away - then read again" fail -e "$rmv" -- \
    "OK  $t/etc/tls-ca-bundle.pem: EM DV TLS CA - G3B, emSign ECC Root CA - G3 - blocklisted" \
    "OK  $t/jdk/lib/security/cacerts: emSign ECC Root CA - G3 - deleted the entries emsign from the keystore (backup: $work/backup/" \
    "OK  $t/app/truststore.jks: emSign ECC Root CA - G3 - deleted the entries emsign from the keystore" "!truststore.jks (Java cacerts) trusts" \
    "OK  $t/venv/lib/python3.12/site-packages/certifi/cacert.pem: emSign ECC Root CA - G3 - removed the block from the bundle (1 certificates kept)" \
    "OK  $t/app/certs/chain.cer: EM DV TLS CA - G3B - the file is the certificate itself: moved to the backup folder" \
    "!NOT removed" \
    "![CRITICAL] trust:" "!Java cacerts) trusts" \
    "[WARNING] trust: 1 Python certifi bundle ($t/venv/lib/python3.12/site-packages/pip/_vendor/certifi/cacert.pem) trusts emSign ECC Root CA - G3" \
    "[WARNING] trust: $t/bin/node (built-in roots) 20.9.0 (Node built-in roots) trusts emSign ECC Root CA - G3" \
    "example/tool ($t/gobin/tool, Go 1.22.5)" \
    "blocked: EM DV TLS CA - G3B" "blocked: emSign ECC Root CA - G3" \
    "!changed=0"
if [ ! -e "$t/app/certs/chain.cer" ] && [ -s "$t/blocklist/emSign-ECC-Root-CA-G3.pem" ] && [ -s "$t/blocklist/EM-DV-TLS-CA-G3B.pem" ] \
   && [ "$(ls "$work/backup"/*/ | wc -l)" -eq $((4 + nss)) ] && ! grep -q emSign "$t/venv/lib/python3.12/site-packages/certifi/cacert.pem" \
   && openssl x509 -in "$t/venv/lib/python3.12/site-packages/certifi/cacert.pem" -noout -subject | grep -q "AAA Certificate Services" \
   && [ "$(grep -c 'BEGIN CERT' "$t/etc/tls-ca-bundle.pem")" -eq 1 ]; then
    echo "ok   - the files: blocklist written, bundle rebuilt, certifi cut, DER moved, keystore rewritten, $((4 + nss)) backups"
    if [ "$nss" = 1 ] && certutil -L -d "sql:$t/nssdb" 2>/dev/null | grep -q "emSign Root"; then echo "FAIL - the NSS database still has the root"; fails=$((fails + 1)); fi
    keytool -list -keystore "$t/app/truststore.jks" -storepass changeit >/dev/null 2>&1 && echo "ok   - keytool opens the rewritten keystore" || { command -v keytool >/dev/null 2>&1 && { echo "FAIL - keytool refuses the rewritten keystore"; fails=$((fails + 1)); }; }
else echo "FAIL - the files after the removal"; ls -la "$t/blocklist" "$work/backup"/* "$t/app/certs" 2>&1 | head -20; fails=$((fails + 1)); fi
m=$(ls "$work/mail"/* 2>/dev/null | head -1)
if [ -n "$m" ] && decoded "$m" && grep -q "Removed (check_trust_remove)" "$work/mail.txt" && grep -q "moved to the backup folder" "$work/mail.txt" \
   && ! grep -q "1. Remove now" "$work/mail.txt" && grep -q "2. Remove next" "$work/mail.txt" && grep -q "3. Then update" "$work/mail.txt"; then echo "ok   - the email: the Removed table, no more 'remove now'"
else echo "FAIL - the email after the removal"; fails=$((fails + 1)); fi
run "removal again: nothing left to remove, the same findings" fail -e "$rmv" -- \
    "!NOT removed" "!- removed" "!blocklisted" "!moved to the backup" "left alone: the bundle holds nothing else" "[WARNING] trust: 1 Python certifi bundle ("

run "nothing on the list: healthy" ok -e "{\"check_trust_bundles\": [\"$t/app/certs/sectigo.pem\"], \"check_trust_find_dirs\": [], \"check_trust_java\": false, \"check_trust_python\": false, \"check_trust_node\": false, \"check_trust_go\": false, \"check_trust_nss_fixed\": \"\", \"check_trust_severity\": {\"AAA Certificate Services\": \"notice\"}}" -- \
    "HEALTHY. Checks run: trust" "AAA Certificate Services [notice]"

if [ "$fails" -eq 0 ]; then echo "all trust tests passed"; else echo "$fails trust test(s) failed"; exit 1; fi
