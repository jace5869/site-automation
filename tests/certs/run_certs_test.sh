#!/usr/bin/env bash
# The certificate report finding certificates by itself (roles/check_certs: check_certs_discover_ports,
# _keystores), in a private network and process namespace (unshare): its probes reach only the test
# servers started here, never this machine's own ports. A TLS server (expires in 20 days), a plain
# HTTP server (not TLS: nothing reported), a listener on port 22 that records any connection (a
# skipped port: never probed), a JKS keystore (read without its password), a PKCS12 with a password
# (listed as not checked: no password guessed) and one without (read). Certificate files found in a
# folder: postfix's placeholder (O=SomeOrganization - not checked, no finding) and a real certificate
# named postfix.pem too (checked), a binary (DER) file named .CER (checked: any case, DER read), a bundle
# of 11 certificates (listed as not checked), one in a folder added by check_certs_discover_extra_dirs.
# The trust store (check_certs_trust_dirs): a file with no extension and a DER one - in the report, never a
# finding. Two hosts: what is not checked on both is one row. The email: the colours, the "Found but not
# checked" table.
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
mkdir -p "$work/ks" "$work/mail" "$work/disc/relay" "$work/home/alice" "$work/anchors"
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
# postfix's placeholder, made as its package does at install; a real certificate with the same file name
openssl genrsa -out ph.key 2048 2>/dev/null
openssl req -new -key ph.key -x509 -sha256 -days 15 -set_serial 4242 -out disc/postfix.pem \
    -subj "/C=--/ST=SomeState/L=SomeCity/O=SomeOrganization/OU=SomeOrganizationalUnit/CN=mail01.example.mil/emailAddress=root@mail01.example.mil" 2>/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -keyout relay.key -out disc/relay/postfix.pem -days 25 -subj "/C=US/O=U.S. Government/CN=relay01.example.mil" 2>/dev/null
# binary, upper-case name; a bundle of 11; a home folder (extra dirs); the trust store: no extension, and DER
openssl req -x509 -newkey rsa:2048 -nodes -keyout w.key -outform DER -out disc/winder.CER -days 22 -subj "/C=US/O=U.S. Government/CN=winder.example.mil" 2>/dev/null
for i in $(seq 1 11); do openssl req -x509 -newkey rsa:2048 -nodes -keyout b.key -days 90 -subj "/CN=bundle$i.example.mil" 2>/dev/null; done > disc/bundle.pem
openssl req -x509 -newkey rsa:2048 -nodes -keyout a.key -out home/alice/alice.crt -days 26 -subj "/C=US/O=U.S. Government/CN=alice01.example.mil" 2>/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -keyout r.key -out anchors/Test_Root_CA_9 -days 21 -subj "/C=US/O=U.S. Government/CN=TEST ROOT CA 9" \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign 2>/dev/null
openssl req -x509 -newkey rsa:2048 -nodes -keyout i.key -outform DER -out anchors/test-int.der -days 300 -subj "/C=US/O=U.S. Government/CN=TEST INT CA 70" 2>/dev/null
# the shared test certificates (tests/fixtures/pki): what the inventory reads from each
cp "$repo/tests/fixtures/pki/web.pem" disc/fixture-web.pem; cp "$repo/tests/fixtures/pki/int.pem" disc/fixture-int.pem
cp "$repo/tests/fixtures/pki/root.pem" anchors/fixture-root.pem
"$py" "$repo/tests/email/fake_smtp.py" 2525 "$work/mail" >/dev/null 2>&1 & pids+=($!)
for p in 8443 9000 22 2525; do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done
rm -f ssh-connections.log           # the readiness check above connected once
[ -s ks/app.jks ] && [ -s ks/open.p12 ] && [ -s ks/locked.p12 ] && [ -s disc/postfix.pem ] && [ -s disc/relay/postfix.pem ] && [ -s disc/winder.CER ] \
    && [ "$(grep -c 'BEGIN CERTIFICATE' disc/bundle.pem)" = 11 ] && [ -s home/alice/alice.crt ] && [ -s anchors/Test_Root_CA_9 ] && [ -s anchors/test-int.der ] \
    || { echo "ABORT - test keystores / certificates"; exit 1; }
printf 'all:\n  hosts:\n    web01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: %s}\n    web02.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: %s}\n' "$py" "$py" > inv.yml
vars="{\"check_certs_discover_dirs\": [\"$work/disc\"], \"check_certs_discover_keystore_dirs\": [\"$work/ks\"], \"site_fail_on\": [],
       \"check_certs_discover_extra_dirs\": [\"$work/home\"], \"check_certs_trust_dirs\": [\"$work/anchors\"], \"cert_renew_skip_hosts\": \"WEB02\",
       \"report_email_to\": \"certs@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": 2525, \"report_email_security\": \"none\",
       \"cert_inventory_files\": true, \"cert_inventory_dir\": \"$work/inventory\"}"
(cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 \
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
check "a real certificate named postfix.pem: checked" "disc/relay/postfix.pem" "CN=relay01.example.mil"
check "a binary (DER) file named .CER: found and read" "disc/winder.CER" "CN=winder.example.mil"
check "a folder from check_certs_discover_extra_dirs, two levels down: searched" "home/alice/alice.crt" "CN=alice01.example.mil"
check "the trust store: every file, whatever its name, text or binary - in the report" "anchors/Test_Root_CA_9 (trust store)" "CN=TEST ROOT CA 9" \
    "anchors/test-int.der (trust store)" "CN=TEST INT CA 70"
"$py" - "$work/inventory/linux-certificates.json" "$work/inventory/linux-certificates.csv" <<'EOPY' && echo "ok   - the inventory files: every certificate with its issuer, names, key, fingerprints and tier (root, intermediate, ca-signed, self-signed)" || { echo "FAIL - the inventory files (above)"; fails=$((fails + 1)); }
import csv, json, sys
recs = json.load(open(sys.argv[1]))
rows = list(csv.DictReader(open(sys.argv[2])))
errs = []
if len(rows) != len(recs): errs.append("csv %d rows, json %d" % (len(rows), len(recs)))
by = {}
for r in recs:
    by.setdefault((r["host"], r["location"].rsplit("/", 1)[-1].split(" ")[0]), r)
want = {
    "fixture-web.pem": dict(tier="ca-signed", public_key="RSA 2048", sha1="487B8E272CBC7625EE18207F58559BD961FD0561", serial="2002",
                            sha256="9D4FE442B5105EFD55B528022F310DCFF9255DE764E3222CC7B7BA2F43974DFA", issuer="CN=Test Issuing CA,O=Test PKI,C=US",
                            sans=["DNS:web01.example.mil", "DNS:web.example.mil", "IP:192.0.2.10"], sig_alg="ecdsa-with-SHA256", trust_store=False),
    "fixture-int.pem": dict(tier="intermediate", public_key="EC P-256", sha1="176D43333E4A3290F6E056CBD9DD0C96FB182F6F", serial="1001",
                            sig_alg="sha256WithRSAEncryption"),
    "fixture-root.pem": dict(tier="root", public_key="RSA 3072", serial="0F965ECAB20EF6EA9F895AA1C292DCD86E14B1D3", trust_store=True),
}
for f, w in want.items():
    r = by.get(("web01.example.mil", f))
    if not r: errs.append("%s not in the inventory" % f); continue
    for k, v in w.items():
        if r.get(k) != v: errs.append("%s %s: %r, expected %r" % (f, k, r.get(k), v))
tom = [r for r in recs if "(alias tomcat)" in r["location"] and r["host"] == "web01.example.mil"]
if not tom or tom[0]["tier"] != "self-signed" or tom[0]["public_key"] != "RSA 2048" or len(tom[0]["sha1"]) != 40 or len(tom[0]["sha256"]) != 64:
    errs.append("the JKS entry: %s" % (tom[:1],))
port = [r for r in recs if "TLS port 8443" in r["location"]]
if not port or port[0]["in_use"] is not True or port[0]["tier"] != "self-signed": errs.append("the TLS port: %s" % (port[:1],))
if any(r["status"] not in ("expired", "critical", "warning", "notice", "ok", "unknown") for r in recs): errs.append("a status")
if {r["source"] for r in recs} != {"linux"} or len({r["id"] for r in recs}) != len(recs): errs.append("source / ids")
print("\n".join("       " + e for e in errs))
sys.exit(1 if errs else 0)
EOPY
grep -q "bundle[0-9]*\.example\.mil" out.txt && { echo "FAIL - a certificate of the bundle was checked"; fails=$((fails + 1)); } || echo "ok   - the bundle's certificates: not checked one by one"
"$py" - out.txt <<'EOPY' && echo "ok   - findings: the DER file (its hint says -inform DER), the home folder's - never the trust store's; a host renewed by hand (cert_renew_skip_hosts) says so on each" || { echo "FAIL - findings (above)"; fails=$((fails + 1)); }
import json, sys
line = [l for l in open(sys.argv[1]) if l.startswith("\tRUN: ")][0]
health = json.loads(line[len("\tRUN: "):])["site_health"]
f = [x for h in health.values() for x in h.get("findings", [])]
errs = []
# web02 is renewed by hand (cert_renew_skip_hosts, a survey's string, another case): its findings say so, web01's do not
w1 = [x for x in health["web01.example.mil"]["findings"] if x["summary"].startswith("certificate ")]
w2 = [x for x in health["web02.example.mil"]["findings"] if x["summary"].startswith("certificate ")]
if not w2 or not all("Renewed by hand: this host is named in cert_renew_skip_hosts (WEB02)" in x.get("note", "") for x in w2): errs.append("web02 notes: %s" % w2[:1])
if not w1 or any(x.get("note") for x in w1): errs.append("web01 has a note: %s" % w1[:1])
der = [x for x in f if "winder.CER" in x["summary"]]
if not der or "openssl x509 -inform DER -noout" not in der[0]["hint"]: errs.append("the DER finding: %s" % der)
if not [x for x in f if "alice.crt" in x["summary"]]: errs.append("no finding for the home folder's certificate")
if [x for x in f if "anchors" in x["summary"] or "trust store" in x["summary"]]: errs.append("a finding for the trust store")
print("\n".join(errs)); sys.exit(1 if errs else 0)
EOPY
grep -q "SomeOrganization" out.txt && { echo "FAIL - postfix's placeholder was checked"; fails=$((fails + 1)); } || echo "ok   - postfix's placeholder (O=SomeOrganization): not checked, no finding"
grep -q "9000" out.txt && { echo "FAIL - the plain HTTP port was reported"; fails=$((fails + 1)); } || echo "ok   - a port that is not TLS: nothing reported"
[ -s ssh-connections.log ] && { echo "FAIL - port 22 (skipped) was probed"; fails=$((fails + 1)); } || echo "ok   - port 22 (in check_certs_discover_ports_skip) was never connected to"
# a host whose only expiring certificate is in the trust store: green with the default site_fail_on, no finding
(cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 \
    "$ap" -i "$work/inv.yml" playbooks/cert_report.yml --limit web01.example.mil -e check_certs_discover=false -e check_certs_discover_ports=false \
    -e check_certs_discover_keystores=false -e "{\"check_certs_trust_dirs\": [\"$work/anchors\"]}") > trust.txt 2>&1
rc=$?
if [ $rc -eq 0 ] && grep -q "Test_Root_CA_9 (trust store)" trust.txt && ! grep -q '"status": "findings"' trust.txt \
   && grep -q '"status": "findings"' out.txt; then          # (the first run, with findings, says so the same way)
    echo "ok   - only a trust store certificate expiring (21 days): listed, the host green with the default site_fail_on, no finding"
else echo "FAIL - trust store only: exit $rc"; grep -E 'fatal|status|trust store' trust.txt | head; fails=$((fails + 1)); fi
eml="$(ls mail/*.eml 2>/dev/null | head -1)"
if [ -n "$eml" ] && "$py" - "$eml" <<'EOPY'
import email, email.policy, re, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
h = m.get_body(preferencelist=("html",)).get_content()
ok = (re.search(r'bgcolor="#b45309"[^>]*>expires within 30 days<', h)          # the TLS port, 20 days
      and re.search(r'bgcolor="#1d4ed8"[^>]*>expires within 60 days<', h)       # the JKS, 45 days
      and ">Found but not checked" in h and "locked.p12" in h and "needs its password" in h
      and "SomeOrganization" not in h and "relay01.example.mil" in h
      # one row each for the two hosts: the placeholder, the locked PKCS12
      and re.search(r'web01\.example\.mil, web02\.example\.mil</td>\s*<td[^>]*>[^<]*/disc/postfix\.pem</td>\s*<td[^>]*>a placeholder certificate', h)
      and len(re.findall(r'locked\.p12</td>', h)) == 1
      # the bundle of 11: listed (once, both hosts), its certificates not checked one by one
      and re.search(r'/disc/bundle\.pem</td>\s*<td[^>]*>a bundle of 11 certificates \(more than check_certs_discover_max_per_file, 10\)', h)
      and "bundle1.example.mil" not in h)
sys.exit(0 if ok else 1)
EOPY
then echo "ok   - the email: amber 20 days, blue 45 days; under 'Found but not checked' the locked PKCS12, postfix's placeholder and the bundle of 11, one row for both hosts"
else echo "FAIL - the email"; fails=$((fails + 1)); fi
grep -q "s3cret" out.txt mail/*.eml 2>/dev/null && { echo "FAIL - a password in the output"; fails=$((fails + 1)); } || echo "ok   - no password anywhere in the output or the email"
if [ "$fails" -eq 0 ]; then echo "ok   - all certificate discovery scenarios passed"; else echo "$fails FAILED"; exit 1; fi
