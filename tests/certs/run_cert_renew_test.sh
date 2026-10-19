#!/usr/bin/env bash
# Certificate renewal (playbooks/cert_renew_csr.yml) against a fake ServiceNow
# (tests/servicenow/snmock.py): certificates signed by a test CA - a server certificate (in its own file,
# in a chain file and served on a port: one CSR), one with commas, quotes and accents in its subject, one
# with a two-value RDN, an EC one, one expired 5 days ago, one served on a port with no file (read from
# the port; its ticket says to find where the service keeps it) - and the ones never renewed: the CA in
# the chain, a self-signed one, one under a subscription-manager path, one issued by Satellite's CA, one
# not expiring. Then (10) a JKS keystore entry and a PKCS12 with a password (read from the keystore; the
# ticket says how to put it back), a binary (DER) .CER file, and never: the keystore's CA and its
# trusted entry, a certificate in the trust store. And (11) a host renewed by hand (cert_renew_skip_hosts):
# its ticket says so, the renewal lists its certificates and makes no CSR. Hosts left out (no CSR, no ticket): one in group sat,
# two named in cert_skip_hosts (a wildcard, a short name). Then: the dry run makes and sends nothing; the
# CSRs come out with the old subject and names and a new key (0600, never in the output); the CSR lands on
# the ticket the Certificate watch opened, or on a new one; a run again changes nothing; a refused
# attachment puts the CSR in the work note and fails the job; servicenow_min_severity critical still gets
# every CSR a ticket, and the Certificate watch does not resolve the warning ones; without ServiceNow the
# CSRs are printed. postfix's placeholder certificate (O=SomeOrganization) never gets a CSR, even listed in
# check_certs_files with cert_renew_self_signed on.
#   bash tests/certs/run_cert_renew_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${CERTS_TEST_PYTHON:-$(command -v python3)}"
port="${CERT_RENEW_TEST_PORT:-18290}"
tls1=$((port + 10)); tls2=$((port + 11))
work="$(mktemp -d)"
pids=()
cleanup() {
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
    [ -n "${CERT_RENEW_TEST_KEEP:-}" ] && cp "$work"/*.txt "$work"/*.jsonl "$CERT_RENEW_TEST_KEEP"/ 2>/dev/null
    rm -rf "$work"
}
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
cd "$work" || exit 1
mkdir -p pki etc/rhsm sat ph
q() { openssl "$@" 2>/dev/null; }

# ---- a test CA, and the certificates it signs ----
q req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 25 -subj "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN=TEST ROOT CA 3" \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign
sign() {  # sign NAME DAYS KEYOPTS SUBJECT SAN [CA]  -> pki/NAME.crt
    local name="$1" days="$2" keyopt="$3" subj="$4" san="$5" ca="${6:-ca}" ext=()
    [ -n "$san" ] && ext=(-addext "subjectAltName=$san")
    q req -new $keyopt -nodes -keyout "$name.key" -out "$name.csr" -utf8 ${MULTI:-} -subj "$subj" "${ext[@]}"
    q x509 -req -in "$name.csr" -CA "$ca.crt" -CAkey "$ca.key" -CAcreateserial -days "$days" -out "pki/$name.crt" -copy_extensions copy
}
sign web01 20 "-newkey rsa:3072" "/C=US/O=U.S. Government/OU=DoD/OU=PKI/OU=TEST/CN=web01.example.mil" "DNS:web01.example.mil,DNS:web.example.mil,IP:192.0.2.3"
sign app01 10 "-newkey rsa:2048" "/C=US/O=Smith, Jones \\+ Co/OU=Ünit #1 \$x 'q' \"d\"/CN=app01.example.mil" "DNS:app01.example.mil"
MULTI=-multivalue-rdn sign mv01 15 "-newkey rsa:2048" "/C=US/O=Test/CN=mv01.example.mil+UID=mv01" ""
sign ec01 20 "-newkey ec -pkeyopt ec_paramgen_curve:P-384" "/C=US/O=U.S. Government/CN=ec01.example.mil" "DNS:ec01.example.mil"
sign tlsonly 20 "-newkey rsa:2048" "/C=US/O=U.S. Government/CN=tlsonly.example.mil" "DNS:tlsonly.example.mil"
sign later 200 "-newkey rsa:2048" "/C=US/O=U.S. Government/CN=later.example.mil" "DNS:later.example.mil"
sign rhsm01 15 "-newkey rsa:2048" "/C=US/O=U.S. Government/CN=rhsm01.example.mil" "" && mv pki/rhsm01.crt etc/rhsm/rhsm01.pem
# expired 5 days ago (x509 -req cannot date a certificate in the past; openssl ca can)
q req -new -newkey rsa:2048 -nodes -keyout old01.key -out old01.csr -subj "/C=US/O=U.S. Government/CN=old01.example.mil" -addext "subjectAltName=DNS:old01.example.mil"
: > ca.idx; echo 01 > ca.serial
printf '%s\n' '[ca]' 'default_ca = d' '[d]' 'database = ca.idx' 'new_certs_dir = .' 'serial = ca.serial' 'default_md = sha256' 'policy = p' \
    'copy_extensions = copy' 'preserve = yes' '[p]' 'countryName = optional' 'organizationName = optional' 'commonName = supplied' > ca.cnf
q ca -batch -config ca.cnf -cert ca.crt -keyfile ca.key -in old01.csr -out pki/old01.crt -notext \
    -startdate "$(date -u -d '-40 days' +%y%m%d%H%M%SZ)" -enddate "$(date -u -d '-5 days +1 hour' +%y%m%d%H%M%SZ)"
q req -x509 -newkey rsa:2048 -nodes -keyout katca.key -out katca.crt -days 300 -subj "/C=US/ST=North Carolina/O=Katello/OU=SomeOrgUnit/CN=sat.example.mil" \
    -addext basicConstraints=critical,CA:TRUE
sign proxy01 15 "-newkey rsa:2048" "/C=US/O=Test/CN=proxy01.example.mil" "" katca && mv pki/proxy01.crt sat/proxy01.crt
q req -x509 -newkey rsa:2048 -nodes -keyout self.key -out pki/self.crt -days 15 -subj "/CN=self01.example.mil"
q req -x509 -newkey rsa:2048 -nodes -keyout lh.key -out pki/lh.crt -days 15 -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
q req -x509 -newkey rsa:2048 -nodes -keyout root.key -out pki/root.crt -days 15 -subj "/C=US/O=Test/CN=Test Root CA" \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign
# postfix's placeholder, made as its package does at install (self-signed, CN = the host's name)
q genrsa -out ph.key 2048
q req -new -key ph.key -x509 -sha256 -days 15 -set_serial 4242 -out ph/postfix.pem \
    -subj "/C=--/ST=SomeState/L=SomeCity/O=SomeOrganization/OU=SomeOrganizationalUnit/CN=mail01.example.mil/emailAddress=root@mail01.example.mil"
cat pki/web01.crt ca.crt > pki/web01-fullchain.pem
mv pki/tlsonly.crt tlsonly.crt
for f in pki/web01.crt pki/app01.crt pki/mv01.crt pki/ec01.crt pki/old01.crt pki/later.crt pki/self.crt pki/lh.crt pki/root.crt pki/web01-fullchain.pem etc/rhsm/rhsm01.pem sat/proxy01.crt tlsonly.crt ph/postfix.pem; do
    [ -s "$f" ] || { echo "ABORT - test certificate $f not made"; exit 1; }
done
subj() { openssl x509 -noout -subject -nameopt RFC2253 -in "$1" | sed 's/^subject=//'; }
sans() { openssl "$1" -noout -text -in "$2" | grep -A1 'Subject Alternative Name' | tail -1 | sed 's/^ *//'; }

# ---- TLS: web01 on a port (the same certificate as its file), tlsonly on a port (no file) ----
openssl s_server -accept "$tls1" -cert pki/web01.crt -key web01.key -quiet </dev/null >/dev/null 2>&1 & pids+=($!)
openssl s_server -accept "$tls2" -cert tlsonly.crt -key tlsonly.key -quiet </dev/null >/dev/null 2>&1 & pids+=($!)

start_mock() {   # start_mock PORT [ENV=VALUE ...]
    local p="$1"; shift
    env "$@" SNMOCK_LOG="$work/requests-$p.jsonl" python3 "$repo/tests/servicenow/snmock.py" "$p" >/dev/null 2>&1 &
    pids+=("$!")
}
start_mock "$port"
start_mock $((port + 1)) SNMOCK_NO_ATTACH=1
start_mock $((port + 2))
start_mock $((port + 3))
start_mock $((port + 5)) SNMOCK_ATTACH_TXT_ONLY=1
start_mock $((port + 6)) SNMOCK_ATTACH_TXT_ONLY=1
for p in "$tls1" "$tls2" "$port" $((port + 1)) $((port + 2)) $((port + 3)) $((port + 5)) $((port + 6)); do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done
for p in "$port" $((port + 1)) $((port + 2)) $((port + 3)) $((port + 5)) $((port + 6)); do : > "requests-$p.jsonl"; done   # the readiness checks above

cat > inv.yml <<EOF
all:
  hosts:
    web01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
    cap01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
    edge01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
  children:
    sat:
      hosts:
        sat01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
EOF
vars="{\"check_certs_discover\": false, \"cert_report_discover\": false, \"site_fail_on\": [],
       \"check_certs_files\": [\"$work/pki/*.crt\", \"$work/pki/*.pem\", \"$work/etc/rhsm/*.pem\", \"$work/sat/*.crt\"],
       \"check_certs_endpoints\": [{\"name\": \"web\", \"host\": \"127.0.0.1\", \"port\": $tls1}, {\"name\": \"tlsonly\", \"host\": \"127.0.0.1\", \"port\": $tls2}],
       \"cert_renew_dir\": \"$work/renew\", \"servicenow_assignment_group\": \"Linux Operations\",
       \"cert_skip_hosts\": \"CAP0*, edge01\", \"cert_renew_install_notes\": {\"$work/pki/app\": \"APP: deploy it with the app job.\"}}"
run() {  # run OUTFILE PORT|none [ansible-playbook args...]
    local out="$1" p="$2"; shift 2
    local sn=()
    [ "$p" != none ] && sn=(SN_HOST="http://127.0.0.1:$p" SN_USERNAME=api SN_PASSWORD=goodpw)
    (cd "$repo" && env "${sn[@]}" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 "$ap" -i "$work/inv.yml" "$@" -e "$vars") > "$out" 2>&1
}
has() {  # has NAME FILE TEXT...
    local name="$1" f="$2"; shift 2
    local t
    for t in "$@"; do grep -qF -- "$t" "$f" || { bad "$name: $f lacks: $t"; return; }; done
    ok "$name"
}
writes() { grep -cE '"method": "(POST|PATCH)"' "requests-$1.jsonl"; }

# ---- the Certificate watch has already opened web01's tickets: its file, and the port that serves it
# (servicenow_tickets.yml, the real step) ----
web_subj="$(subj pki/web01.crt)"
"$py" - "$work" "$web_subj" "$tls1" > health.json <<'EOPY'
import json, sys
w, s, port = sys.argv[1], sys.argv[2], sys.argv[3]
f = [{"check": "certs", "id": "cert:%s:%s" % (where, s), "severity": "warning",
      "summary": "certificate %s in %s expires in 19 days" % (s, where), "hint": ""}
     for where in ["%s/pki/web01.crt" % w, "web (https://127.0.0.1:%s)" % port]]
print(json.dumps({"site_health": {"web01.example.mil": {"status": "findings", "checks": ["certs"], "findings": f}},
                  "site_health_targets": ["web01.example.mil"], "servicenow_fail_when_findings": False}))
EOPY
run watch.txt "$port" playbooks/servicenow_tickets.yml -e @"$work/health.json"
grep -qE "opened: INC[0-9]+, INC" watch.txt && ok "the Certificate watch opened web01's two tickets (file, port)" || { bad "watch ticket"; tail -20 watch.txt; }
before="$(writes "$port")"

# ---- 1. the dry run ----
run dry.txt "$port" playbooks/cert_renew_csr.yml --check; rc=$?
[ $rc -eq 0 ] && ok "dry run: exit 0" || { bad "dry run: exit $rc"; tail -30 dry.txt; }
[ ! -e renew ] && ok "dry run: no key, no CSR, no folder" || bad "dry run: wrote $(ls renew)"
[ "$(writes "$port")" = "$before" ] && ok "dry run: nothing sent to ServiceNow" || bad "dry run: a POST or PATCH was sent"
has "dry run: says what it would do" dry.txt "DRY RUN (Check mode)" "would make the CSR, and would attach it to INC" "would make the CSR, and would open a ticket"

# ---- 2. for real ----
run real.txt "$port" playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "real run: exit 0" || { bad "real run: exit $rc"; tail -40 real.txt; }
has "sat01 is left out (group sat)" real.txt "sat01.example.mil: left out - in group sat (cert_skip_groups) - its certificates"
has "cap01 and edge01 are left out (cert_skip_hosts: a wildcard in another case, a short name)" real.txt \
    "cap01.example.mil: left out - named in cert_skip_hosts (CAP0*) - its certificates are renewed some other way" \
    "edge01.example.mil: left out - named in cert_skip_hosts (edge01) - its"
has "never renewed, each with why" real.txt \
    "TEST ROOT CA 3,OU=PKI,OU=DoD,O=U.S. Government,C=US | expires" "a CA certificate (part of a chain)" \
    "CN=self01.example.mil | expires" "self-signed (made on the host" "cert_renew_self_signed: true makes one" \
    "CN=Test Root CA,O=Test,C=US | expires" "a CA's own certificate (a root: it signs certificates)" \
    "its path is in cert_renew_exclude" "issued by CN=sat.example.mil,OU=SomeOrgUnit,O=Katello"
grep -qE '"(MADE|EXISTS|WOULD MAKE|SKIPPED|FAILED) [|].*later[.]example[.]mil' real.txt && bad "a certificate not expiring was considered" || ok "a certificate that does not expire soon: left alone"
n="$(ls renew/*.csr 2>/dev/null | wc -l)"
[ "$n" = 6 ] && ok "6 CSRs: web01 (file, chain file and port: one), app01, mv01, ec01, old01 (expired), tlsonly (a port alone)" || bad "$n CSRs: $(ls renew)"
[ "$(stat -c %a renew)" = 700 ] && ok "the folder is 0700" || bad "folder mode $(stat -c %a renew)"
perm=$(stat -c %a renew/*.key | sort -u | tr '\n' ' ')
[ "$perm" = "600 " ] && ok "the keys are 0600" || bad "key modes: $perm"
for name in web01 app01 mv01 ec01 old01; do
    csr="$(ls renew/$name.example.mil-*.csr 2>/dev/null | head -1)"
    [ -n "$csr" ] || { bad "$name: no CSR"; continue; }
    key="${csr%.csr}.key"
    openssl req -in "$csr" -noout -verify >/dev/null 2>&1 || { bad "$name: the CSR does not verify"; continue; }
    [ "$(openssl req -in "$csr" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')" = "$(subj pki/$name.crt)" ] \
        || { bad "$name: subject $(openssl req -in "$csr" -noout -subject -nameopt RFC2253) not $(subj pki/$name.crt)"; continue; }
    [ "$(sans req "$csr")" = "$(sans x509 pki/$name.crt)" ] || { bad "$name: names $(sans req "$csr") not $(sans x509 pki/$name.crt)"; continue; }
    [ "$(openssl req -in "$csr" -noout -pubkey)" = "$(openssl pkey -in "$key" -pubout)" ] || { bad "$name: CSR and key do not match"; continue; }
    [ "$(openssl req -in "$csr" -noout -pubkey)" != "$(openssl x509 -in pki/$name.crt -noout -pubkey)" ] || { bad "$name: the old key was reused"; continue; }
    ok "$name: the CSR verifies, the same subject and names, a NEW key that matches it"
done
csr="$(ls renew/tlsonly.example.mil-*.csr 2>/dev/null | head -1)"
if [ -n "$csr" ] && openssl req -in "$csr" -noout -verify >/dev/null 2>&1 \
   && [ "$(openssl req -in "$csr" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')" = "$(subj tlsonly.crt)" ] \
   && [ "$(sans req "$csr")" = "$(sans x509 tlsonly.crt)" ] \
   && [ "$(openssl req -in "$csr" -noout -pubkey)" = "$(openssl pkey -in "${csr%.csr}.key" -pubout)" ]; then
    ok "tlsonly (served on a port, no file): its CSR read from the port - the same subject and names, a new key"
else bad "tlsonly: its CSR ($csr)"; fi
openssl pkey -in renew/web01.example.mil-*.key -noout -text 2>/dev/null | grep -q "Private-Key: (3072 bit" && ok "web01: a 3072-bit old key keeps its size" || bad "web01 key size"
openssl pkey -in renew/app01.example.mil-*.key -noout -text 2>/dev/null | grep -q "Private-Key: (2048 bit" && ok "app01: RSA 2048 (cert_renew_key_bits)" || bad "app01 key size"
openssl pkey -in renew/ec01.example.mil-*.key -noout -text 2>/dev/null | grep -q "secp384r1" && ok "ec01: an EC key on the same curve (P-384)" || bad "ec01 key curve"
grep -q '"aap_job"' renew/web01.example.mil-*.json && ok "an info file next to each CSR" || bad "info file"
"$py" - "requests-$port.jsonl" "$work" <<'EOPY' && ok "ServiceNow: web01's CSR on the watch's file ticket (file, chain and port), the port ticket says where; 5 new tickets, 6 attachments, 6 notes; tlsonly's step 3: find where the service keeps it" || bad "ServiceNow requests (above)"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1])]
inc = [r for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/table/incident"]
att = [r for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
allnotes = [r for r in reqs if r["method"] == "PATCH" and "Certificate renewal" in str(r["body"].get("work_notes", ""))]
notes = [r for r in allnotes if "\nCertificate: " in r["body"]["work_notes"]]
pointer = [r for r in allnotes if "is renewed on" in r["body"]["work_notes"]]
errs = []
if len(inc) != 7: errs.append("%d incidents opened (2 by the watch + 5)" % len(inc))
if len(pointer) != 1 or "web01.example.mil" not in pointer[0]["body"]["work_notes"] or "web (https://127.0.0.1:" not in pointer[0]["body"]["work_notes"]:
    errs.append("the port ticket's note: %s" % pointer)
if len(att) != 6: errs.append("%d attachments" % len(att))
if len(notes) != 6: errs.append("%d work notes" % len(notes))
tls = [n["body"]["work_notes"] for n in notes if "Certificate: CN=tlsonly.example.mil" in n["body"]["work_notes"]]
if not tls or "3. Its certificate file was not found on the host - only the port serves it" not in tls[0]: errs.append("tlsonly note: not the port's step 3")
names = sorted(a["query"]["file_name"] for a in att)
if not all(n.endswith(".csr") for n in names): errs.append("names %s" % names)
for a in att:
    if not a["body"]["raw"].startswith("-----BEGIN CERTIFICATE REQUEST-----"): errs.append("not a CSR: %s" % a["body"]["raw"][:40])
    if a["body"]["content_type"] != "text/plain": errs.append("content type %s" % a["body"]["content_type"])
new = [r for r in inc[1:]]
if any(r["body"].get("assignment_group") != "Linux Operations" for r in new): errs.append("assignment group")
if not all(r["body"]["correlation_id"].startswith("site-health:") and ":certs:" in r["body"]["correlation_id"] for r in new): errs.append("correlation id")
web = [n for n in notes if "web01.example.mil" in n["body"]["work_notes"] and "Certificate: CN=web01" in n["body"]["work_notes"]]
if not web: errs.append("no web01 note")
else:
    t = web[0]["body"]["work_notes"]
    for want in ["/pki/web01.crt ; ", "/pki/web01-fullchain.pem", "web (https://127.0.0.1:", "DNS:web01.example.mil, DNS:web.example.mil, IP:192.0.2.3",
                 "New key: RSA 3072-bit, made on the host", "Next:", "1. Submit the CSR"]:
        if want not in t: errs.append("web01 note lacks %r" % want)
    if "3. Install it with the new key in place of the file(s) above" not in t: errs.append("web01 note: the usual step 3")
    if pointer and pointer[0]["path"] == web[0]["path"]: errs.append("the port ticket's note went to the CSR's own ticket")
    if "BEGIN CERTIFICATE REQUEST" in t: errs.append("the CSR text in a note although attached")
app = [n["body"]["work_notes"] for n in notes if "Certificate: CN=app01" in n["body"]["work_notes"]]
if not app or "3. APP: deploy it with the app job." not in app[0]: errs.append("app01 note: not its own step 3 (cert_renew_install_notes)")
# the expired one: a critical ticket that says so, and a note
old = [r["body"] for r in inc if "old01" in r["body"].get("short_description", "")]
if not old or "EXPIRED 5 days ago" not in old[0]["short_description"] or old[0].get("urgency") != "1": errs.append("old01 ticket: %s" % old)
if not [n for n in notes if "Certificate: CN=old01.example.mil" in n["body"]["work_notes"] and "(EXPIRED 5 days ago)" in n["body"]["work_notes"]]:
    errs.append("no old01 note saying EXPIRED")
# web01's attachment went to the watch ticket (the first incident), not a new one
first = inc[0]
if first["body"].get("short_description", "").find("web01") < 0: errs.append("first incident is not the watch ticket")
print("\n".join(errs)); sys.exit(1 if errs else 0)
EOPY
grep -qE "sat01|cap01|edge01" "requests-$port.jsonl" && bad "a request about a host left out" || ok "nothing about sat01, cap01, edge01 sent to ServiceNow"
grep -lq "PRIVATE KEY" real.txt dry.txt "requests-$port.jsonl" 2>/dev/null && bad "a private key in the output or sent" || ok "no private key in the output or in what was sent"
sha_before="$(sha256sum renew/*.csr renew/*.key | sha256sum)"

# ---- 3. a run again: the same CSRs, nothing new on the tickets ----
before="$(writes "$port")"
run again.txt "$port" playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "run again: exit 0" || { bad "run again: exit $rc"; tail -30 again.txt; }
[ "$(sha256sum renew/*.csr renew/*.key | sha256sum)" = "$sha_before" ] && ok "run again: the same keys and CSRs (the one the PKI has stays valid)" || bad "run again: keys or CSRs changed"
[ "$(writes "$port")" = "$before" ] && ok "run again: nothing new sent to ServiceNow" || bad "run again: $(( $(writes "$port") - before )) new POST/PATCH"
has "run again: says already attached" again.txt "already attached" "EXISTS | CN=web01.example.mil"

# ---- 3b. a pending key lost: a new key and CSR, attached under a new name with its note ----
old_mv="$(ls renew/mv01.example.mil-*.csr)"; old_mv_sum="$(sha256sum < "$old_mv")"
rm -f renew/mv01.example.mil-*.key
before="$(writes "$port")"
run remade.txt "$port" playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "a lost key: exit 0" || { bad "a lost key: exit $rc"; tail -30 remade.txt; }
[ "$(sha256sum < "$old_mv")" != "$old_mv_sum" ] && [ -s renew/mv01.example.mil-*.key ] && ok "a lost key: a new key and CSR" || bad "a lost key: not made again"
[ "$(( $(writes "$port") - before ))" = 2 ] && ok "a lost key: the new CSR attached (a new name) with a note - nothing else sent" \
    || bad "a lost key: $(( $(writes "$port") - before )) POST/PATCH, expected 2"
has "a lost key: said" remade.txt "MADE | CN=mv01.example.mil" "attached" "already attached"

# ---- 4. an instance that refuses the attachment: the CSR goes in the work note, the job fails ----
run refused.txt $((port + 1)) playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 2 ] && ok "attachment refused: the job fails" || { bad "attachment refused: exit $rc"; tail -20 refused.txt; }
has "attachment refused: says why and where the CSR is" refused.txt "the attachment was refused" "as a .txt file too" "glide.attachment.extensions"
"$py" - "requests-$((port + 1)).jsonl" <<'EOPY' && ok "attachment refused: each work note carries the CSR and the server's reason" || bad "refused: work notes"
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in open(sys.argv[1])]
notes = [n for n in notes if "Certificate renewal" in str(n)]
ok = len(notes) == 6 and all("-----BEGIN CERTIFICATE REQUEST-----" in n and "File extension not allowed: csr" in n
                              and "refused as a .txt file too" in n for n in notes)
sys.exit(0 if ok else 1)
EOPY

# ---- 4a. an instance that refuses the .csr as text/plain (HTTP 400 "Invalid file type: text/plain"): attached
# again as .txt - the job passes, the note names the .txt; a run again attaches nothing more ----
run txtonly.txt $((port + 5)) playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "a .csr refused (Invalid file type): exit 0" || { bad "a .csr refused: exit $rc"; tail -20 txtonly.txt; }
has "a .csr refused: attached as .txt, and says so" txtonly.txt "attached as web01.example.mil-" ".txt (refused as web01.example.mil-"
"$py" - "requests-$((port + 5)).jsonl" <<'EOPY' && ok "a .csr refused: 6 CSRs attached as .txt (text/plain), each note names its .txt, no CSR text in a note" || bad "a .csr refused: ServiceNow"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1])]
att = [r for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
txt = [r["query"]["file_name"] for r in att if r["query"]["file_name"].endswith(".txt")]
notes = [r["body"]["work_notes"] for r in reqs if r["method"] == "PATCH" and "\nCertificate: " in str(r["body"].get("work_notes", ""))]
ok = (len(att) == 12 and len(txt) == 6 and len(notes) == 6
      and all(r["body"]["content_type"] == "text/plain" for r in att if r["query"]["file_name"].endswith(".txt"))
      and all(".txt" in n.split("\n")[0] and "BEGIN CERTIFICATE REQUEST" not in n for n in notes))
if not ok: print(len(att), txt, [n.split("\n")[0] for n in notes])
sys.exit(0 if ok else 1)
EOPY
before="$(writes $((port + 5)))"
run txtonly2.txt $((port + 5)) playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && [ "$(writes $((port + 5)))" = "$before" ] && ok "a .csr refused, run again: the .txt is found - nothing new sent" \
    || { bad "a .csr refused, run again: exit $rc, $(( $(writes $((port + 5))) - before )) new POST/PATCH"; tail -20 txtonly2.txt; }

# ---- 4a2. the same instance, cert_renew_attach_retry_txt false: no .txt try - the CSR in the work note, the job fails ----
run noretry.txt $((port + 6)) playbooks/cert_renew_csr.yml -e cert_renew_attach_retry_txt=false; rc=$?
[ $rc -eq 2 ] && ok "retry off: the job fails" || { bad "retry off: exit $rc"; tail -20 noretry.txt; }
has "retry off: says so" noretry.txt "the attachment was refused (HTTP 400)" "cert_renew_attach_retry_txt: true tries each one again as .txt"
"$py" - "requests-$((port + 6)).jsonl" <<'EOPY' && ok "retry off: no .txt sent; each work note carries the CSR and 'Invalid file type: text/plain'" || bad "retry off: ServiceNow"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1])]
att = [r["query"]["file_name"] for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
notes = [r["body"]["work_notes"] for r in reqs if r["method"] == "PATCH" and "\nCertificate: " in str(r["body"].get("work_notes", ""))]
ok = (len(att) == 6 and not [a for a in att if a.endswith(".txt")] and len(notes) == 6
      and all("BEGIN CERTIFICATE REQUEST" in n and "Invalid file type: text/plain" in n and ".txt file too" not in n for n in notes))
sys.exit(0 if ok else 1)
EOPY

# ---- 4b. servicenow_min_severity critical: still a ticket for every CSR (30 days or less, and expired) ----
run critical.txt $((port + 2)) playbooks/cert_renew_csr.yml -e servicenow_min_severity=critical; rc=$?
[ $rc -eq 0 ] && ok "min severity critical: exit 0" || { bad "min severity critical: exit $rc"; tail -20 critical.txt; }
"$py" - "requests-$((port + 2)).jsonl" <<'EOPY' && ok "min severity critical: a ticket for each of the 6 CSRs (web01, ec01 and tlsonly, 19 days, too)" || bad "min severity critical: tickets"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1])]
inc = [r["body"]["short_description"] for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/table/incident"]
ok = len(inc) == 6 and all(any(n in t for t in inc) for n in ["web01", "app01", "mv01", "ec01", "old01", "tlsonly"])
sys.exit(0 if ok else 1)
EOPY

# ---- 5. without ServiceNow: the CSRs printed to copy ----
run nosn.txt none playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "no ServiceNow credential: exit 0" || { bad "no ServiceNow: exit $rc"; tail -20 nosn.txt; }
has "no ServiceNow credential: the CSRs printed" nosn.txt "NOT CONNECTED" "-----BEGIN CERTIFICATE REQUEST-----"

# ---- 6. the certificate report leaves the Satellite host out, and does not count it as targeted ----
run report.txt none playbooks/cert_report.yml; rc=$?
[ $rc -eq 0 ] && ok "certificate report: exit 0" || { bad "certificate report: exit $rc"; tail -20 report.txt; }
has "certificate report: sat01, cap01, edge01 left out" report.txt "sat01.example.mil: left out" "cap01.example.mil: left out" "edge01.example.mil: left out"
"$py" - report.txt <<'EOPY' && ok "certificate report: the hosts left out are not in site_health_targets (no 'no result' ticket for them)" || bad "certificate report: targets"
import re, sys
t = open(sys.argv[1]).read()
m = re.search(r'"site_health_targets":\s*\[([^\]]*)\]', t)
sys.exit(0 if m and "web01.example.mil" in m.group(1) and not re.search("sat01|cap01|edge01", m.group(1)) else 1)
EOPY

# ---- 7. the Certificate watch with servicenow_min_severity critical, after 4b: the warning CSR tickets stay
# open (their certificate is still there); ec01's - its finding taken out, as if renewed - is resolved ----
"$py" - report.txt "$work" > health7.json <<'EOPY'
import json, sys
line = [l for l in open(sys.argv[1]) if l.startswith("\tRUN: ")][0]
run = json.loads(line[len("\tRUN: "):])
h = run["site_health"]["web01.example.mil"]
h["findings"] = [f for f in h["findings"] if "/pki/ec01.crt" not in f["id"]]
print(json.dumps({"site_health": run["site_health"], "site_health_targets": run["site_health_targets"],
                  "servicenow_fail_when_findings": False, "servicenow_min_severity": "critical", "servicenow_resolve_cleared": True}))
EOPY
seen="$(wc -l < "requests-$((port + 2)).jsonl")"
run watch7.txt $((port + 2)) playbooks/servicenow_tickets.yml -e @"$work/health7.json"; rc=$?
[ $rc -eq 0 ] && ok "watch at min severity critical: exit 0" || { bad "watch at min severity critical: exit $rc"; tail -20 watch7.txt; }
"$py" - "requests-$((port + 2)).jsonl" "$seen" "http://127.0.0.1:$((port + 2))" <<'EOPY' && ok "watch at min severity critical: the warning CSR tickets (web01) stay open; ec01's, cleared, is resolved" || bad "watch at min severity critical: resolved"
import base64, json, sys, urllib.parse, urllib.request
reqs = [json.loads(l) for l in open(sys.argv[1])][int(sys.argv[2]):]
q = urllib.parse.urlencode({"sysparm_fields": "short_description", "sysparm_query": "correlation_idSTARTSWITHsite-health:^active=false"})
req = urllib.request.Request(sys.argv[3] + "/api/now/table/incident?" + q, headers={"Accept": "application/json",
                             "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()})
closed = [r["short_description"] for r in json.load(urllib.request.urlopen(req))["result"]]
notes = [r for r in reqs if r["method"] == "PATCH" and "passes again" in json.dumps(r["body"])]
ok = len(closed) == 1 and "ec01" in closed[0] and len(notes) == 1
if not ok: print("closed: %s; %d 'passes again' updates" % (closed, len(notes)))
sys.exit(0 if ok else 1)
EOPY

# ---- 8. cert_renew_self_signed: the self-signed one gets a CSR too - never the root CA, never one only for localhost ----
run selfsigned.txt $((port + 3)) playbooks/cert_renew_csr.yml -e cert_renew_self_signed=true; rc=$?
[ $rc -eq 0 ] && ok "self-signed on: exit 0" || { bad "self-signed on: exit $rc"; tail -30 selfsigned.txt; }
has "self-signed on: self01 gets a CSR, the root CA and the localhost one do not" selfsigned.txt "MADE | CN=self01.example.mil" \
    "CN=Test Root CA,O=Test,C=US | expires" "a CA's own certificate (a root" "CN=localhost | expires" "self-signed, and only for localhost"
csr="$(ls renew/self01.example.mil-*.csr 2>/dev/null | head -1)"
if [ -n "$csr" ] && openssl req -in "$csr" -noout -verify >/dev/null 2>&1 \
   && [ "$(openssl req -in "$csr" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')" = "$(subj pki/self.crt)" ] \
   && [ "$(openssl req -in "$csr" -noout -pubkey)" = "$(openssl pkey -in "${csr%.csr}.key" -pubout)" ] \
   && [ "$(openssl req -in "$csr" -noout -pubkey)" != "$(openssl x509 -in pki/self.crt -noout -pubkey)" ]; then
    ok "self-signed on: self01's CSR verifies, the same subject, a NEW key that matches it"
else bad "self-signed on: self01's CSR ($csr)"; fi
ls renew/localhost-* renew/Test* >/dev/null 2>&1 && bad "self-signed on: a CSR for the root CA or localhost" || ok "self-signed on: no CSR for the root CA or localhost"
"$py" - "requests-$((port + 3)).jsonl" <<'EOPY' && ok "self-signed on: its ticket has the CSR and a note saying it was self-signed (7 CSRs in all)" || bad "self-signed on: ServiceNow"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1])]
att = [r for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
notes = [r["body"].get("work_notes", "") for r in reqs if r["method"] == "PATCH"]
own = [n for n in notes if "Certificate: CN=self01.example.mil" in n]
ok = len(att) == 7 and own and "It was self-signed: the PKI issues a real certificate in its place" in own[0] \
     and not [n for n in notes if "It was self-signed" in n and "CN=self01" not in n]
sys.exit(0 if ok else 1)
EOPY

# ---- 9. postfix's placeholder listed in check_certs_files (so checked, and expiring), self-signed on: no CSR ----
vars0="$vars"
vars="$("$py" -c 'import json, sys; v = json.loads(sys.argv[1]); v["check_certs_files"].append(sys.argv[2]); print(json.dumps(v))' "$vars" "$work/ph/postfix.pem")"
run placeholder.txt none playbooks/cert_renew_csr.yml -e cert_renew_self_signed=true; rc=$?
vars="$vars0"
[ $rc -eq 0 ] && ok "placeholder: exit 0" || { bad "placeholder: exit $rc"; tail -30 placeholder.txt; }
has "placeholder: listed, its reason given" placeholder.txt "SKIPPED | emailAddress=root@mail01.example.mil,CN=mail01.example.mil" \
    "a placeholder certificate a package made at install"
ls renew/mail01* >/dev/null 2>&1 && bad "placeholder: a CSR was made for it" || ok "placeholder: no CSR"

# ---- 10. a JKS keystore entry and a PKCS12 with a password (read from the keystore), a binary (DER)
# .CER file; never: the keystore's CA (its chain), its trusted entry, a certificate in the trust store ----
if command -v keytool >/dev/null; then
    mkdir -p ks bin anchors
    for c in "jks01 DNS:jks01.example.mil" "partner01 " "p12web DNS:p12web.example.mil" "der01 DNS:der01.example.mil" "anchor01 "; do
        set -- $c
        sign "$1" 20 "-newkey rsa:2048" "/C=US/O=U.S. Government/CN=$1.example.mil" "${2:-}" && mv "pki/$1.crt" "$1.crt"
    done
    mv anchor01.crt anchors/anchor01.crt
    q pkcs12 -export -in jks01.crt -inkey jks01.key -certfile ca.crt -name tomcat -out jks01.p12 -passout pass:changeit
    keytool -importkeystore -srckeystore jks01.p12 -srcstoretype PKCS12 -srcstorepass changeit -destkeystore ks/app.jks \
        -deststoretype JKS -deststorepass changeit -noprompt >/dev/null 2>&1
    keytool -importcert -noprompt -alias partner -file partner01.crt -keystore ks/app.jks -storepass changeit >/dev/null 2>&1
    q pkcs12 -export -in p12web.crt -inkey p12web.key -certfile ca.crt -name web -out ks/locked.p12 -passout pass:s3cret
    q x509 -in der01.crt -outform DER -out bin/der01.CER
    start_mock $((port + 4))
    for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$((port + 4))") 2>/dev/null && break; sleep 0.1; done
    : > "requests-$((port + 4)).jsonl"
    vars0="$vars"
    vars="{\"check_certs_discover\": false, \"cert_report_discover\": false, \"site_fail_on\": [],
           \"check_certs_files\": [\"$work/bin/*.CER\", \"$work/anchors/*.crt\"], \"check_certs_trust_dirs\": [\"$work/anchors\"],
           \"check_certs_keystores\": [{\"path\": \"$work/ks/app.jks\"}, {\"path\": \"$work/ks/locked.p12\", \"password_env\": \"TEST_KS_PW\"}],
           \"cert_renew_dir\": \"$work/renew10\", \"servicenow_assignment_group\": \"Linux Operations\", \"cert_skip_hosts\": \"CAP0*, edge01\"}"
    export TEST_KS_PW=s3cret
    run stores.txt $((port + 4)) playbooks/cert_renew_csr.yml; rc=$?
    unset TEST_KS_PW
    vars="$vars0"
    [ $rc -eq 0 ] && ok "keystores, DER: exit 0" || { bad "keystores, DER: exit $rc"; tail -30 stores.txt; }
    has "keystores, DER: CSRs for the JKS entry, the PKCS12 entry and the binary file" stores.txt \
        "MADE | CN=jks01.example.mil" "app.jks (alias tomcat)" "MADE | CN=p12web.example.mil" "locked.p12 (alias web)" "MADE | CN=der01.example.mil"
    has "never: the keystore's CA, its trusted entry, the trust store" stores.txt \
        "a CA certificate (part of a chain)" "CN=partner01.example.mil" "without its key (one the program trusts)" \
        "CN=anchor01.example.mil" "in the trust store (update-ca-trust): a certificate this host trusts, not its own - no CSR"
    for name in jks01 p12web der01; do
        csr="$(ls renew10/$name.example.mil-*.csr 2>/dev/null | head -1)"
        if [ -n "$csr" ] && openssl req -in "$csr" -noout -verify >/dev/null 2>&1 \
           && [ "$(openssl req -in "$csr" -noout -subject -nameopt RFC2253 | sed 's/^subject=//')" = "$(subj $name.crt)" ] \
           && [ "$(openssl req -in "$csr" -noout -pubkey)" = "$(openssl pkey -in "${csr%.csr}.key" -pubout)" ] \
           && [ "$(openssl req -in "$csr" -noout -pubkey)" != "$(openssl x509 -in $name.crt -noout -pubkey)" ]; then
            ok "$name: the CSR verifies, the same subject, a NEW key that matches it"
        else bad "$name: its CSR ($csr)"; fi
    done
    ls renew10/partner01* renew10/anchor01* renew10/TEST* >/dev/null 2>&1 && bad "a CSR for the trusted entry, the trust store or the CA" || ok "no CSR for the trusted entry, the trust store or the CA"
    grep -q "s3cret" stores.txt "requests-$((port + 4)).jsonl" && bad "the keystore password in the output or sent" || ok "the keystore password: never in the output or sent"
    "$py" - "requests-$((port + 4)).jsonl" <<'EOPY' && ok "ServiceNow: 3 CSRs; step 3 says how to put it in the keystore (alias), or to convert to DER" || bad "keystores, DER: ServiceNow"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1])]
att = [r for r in reqs if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
notes = [r["body"].get("work_notes", "") for r in reqs if r["method"] == "PATCH" and "\nCertificate: " in str(r["body"].get("work_notes", ""))]
def note(cn):
    n = [t for t in notes if "Certificate: CN=%s" % cn in t]
    return n[0] if n else ""
errs = []
if len(att) != 3: errs.append("%d attachments" % len(att))
j = note("jks01.example.mil")
if "3. Put the new key and the issued certificate in the keystore" not in j or "in place of alias tomcat" not in j or "keytool -importkeystore" not in j:
    errs.append("jks01 step 3: %s" % j[-400:])
w = note("p12web.example.mil")
if "in place of alias web" not in w: errs.append("p12web step 3: %s" % w[-400:])
d = note("der01.example.mil")
if "The file is binary (DER): convert the issued certificate first - openssl x509 -in <issued certificate> -outform DER -out" not in d:
    errs.append("der01 step 3: %s" % d[-400:])
print("\n".join(errs)); sys.exit(1 if errs else 0)
EOPY
else
    ok "keystores, DER: SKIPPED - no keytool (a Java JDK) here"
fi

# ---- 11. a host renewed by hand (cert_renew_skip_hosts): its ticket says so (the finding's note); the renewal
# lists its certificates - no key, no CSR, nothing sent ----
start_mock $((port + 7))
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$((port + 7))") 2>/dev/null && break; sleep 0.1; done
: > "requests-$((port + 7)).jsonl"
"$py" - "$work" "$web_subj" > health11.json <<'EOPY'
import json, sys
w, s = sys.argv[1], sys.argv[2]
note = ("Renewed by hand: this host is named in cert_renew_skip_hosts (web01) - the certificate renewal job makes no CSR for it."
        " Renew it the way its product says.")
f = [{"check": "certs", "id": "cert:%s/pki/web01.crt:%s" % (w, s), "severity": "warning", "note": note,
      "summary": "certificate %s in %s/pki/web01.crt expires in 19 days" % (s, w), "hint": ""}]
print(json.dumps({"site_health": {"web01.example.mil": {"status": "findings", "checks": ["certs"], "findings": f}},
                  "site_health_targets": ["web01.example.mil"], "servicenow_fail_when_findings": False}))
EOPY
run watch11.txt $((port + 7)) playbooks/servicenow_tickets.yml -e @"$work/health11.json"
"$py" - "requests-$((port + 7)).jsonl" <<'EOPY' && ok "renewed by hand: the ticket's description carries the note" || bad "renewed by hand: the ticket's description"
import json, sys
inc = [json.loads(l)["body"] for l in open(sys.argv[1]) if '"/api/now/table/incident"' in l and '"POST"' in l]
sys.exit(0 if inc and "\nNote: Renewed by hand: this host is named in cert_renew_skip_hosts (web01)" in inc[0].get("description", "") else 1)
EOPY
before="$(writes $((port + 7)))"; files_before="$(ls renew | wc -l)"
run hand.txt $((port + 7)) playbooks/cert_renew_csr.yml -e cert_renew_skip_hosts=web01; rc=$?
[ $rc -eq 0 ] && ok "renewed by hand: exit 0" || { bad "renewed by hand: exit $rc"; tail -20 hand.txt; }
has "renewed by hand: each certificate listed, with why" hand.txt "SKIPPED | CN=web01.example.mil" \
    "renewed by hand - the host is named in cert_renew_skip_hosts (web01): no CSR (its ticket says so)" "SKIPPED | CN=app01.example.mil"
grep -q '"MADE |\|"EXISTS |\|"WOULD MAKE |' hand.txt && bad "renewed by hand: a CSR was considered" || ok "renewed by hand: no CSR made or reused"
[ "$(writes $((port + 7)))" = "$before" ] && [ "$(ls renew | wc -l)" = "$files_before" ] && ok "renewed by hand: no key, no CSR, nothing sent to ServiceNow" \
    || bad "renewed by hand: $(( $(writes $((port + 7))) - before )) POST/PATCH, files $files_before -> $(ls renew | wc -l)"

if [ "$fails" -eq 0 ]; then echo "ok   - all certificate renewal scenarios passed"; else echo "$fails FAILED"; exit 1; fi
