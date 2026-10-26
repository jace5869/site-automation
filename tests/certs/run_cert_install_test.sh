#!/usr/bin/env bash
# Certificate install (playbooks/cert_install_check.yml -> approval -> cert_install_approved.yml, and
# cert_restore.yml) against a fake ServiceNow (tests/servicenow/snmock.py) and a test CA. The real renewal
# (cert_renew_csr.yml, cert_renew_servicenow: catalog) makes the CSRs and orders them; the "PKI" then signs them
# and puts the certificates on the requests - binary (DER) with the chain as a .p7b, a PEM PKCS7, someone else's
# certificate beside it. The web server is a stand-in: openssl s_server on a port, serving the certificate in
# its configuration's files; "systemctl reload httpd" starts it again on the files as they are then (bin/ here
# also has stand-ins for ss, ps and apachectl). Then: nothing issued yet - waiting, green; issued - the plan
# (web01: its file, its chain file and its key; reload httpd; the port) and NEEDS APPROVAL, red; app01 held (no
# service found), then by hand (a service that cannot reload); tlsonly by hand (only seen on a port). The install
# job: refused without the plan, and when a host of the plan is not in it; a dry run changes nothing; for real -
# each file in its old layout and mode, the backup, the port serves the new certificate, the renewal's files
# moved, a work note on the request. The restore puts the old one back and the renewal waits again. Then the
# rollbacks: a configuration test that fails, a reload after which the port still serves the old certificate,
# a file replaced between the check and the approval (refused).
#   bash tests/certs/run_cert_install_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${CERTS_TEST_PYTHON:-$(command -v python3)}"
port="${CERT_INSTALL_TEST_PORT:-18390}"
tls1=$((port + 10)); tls2=$((port + 11))
work="$(mktemp -d)"
pids=()
cleanup() {
    for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done
    [ -f "$work/httpd.pid" ] && kill "$(cat "$work/httpd.pid")" 2>/dev/null
    [ -n "${CERT_INSTALL_TEST_KEEP:-}" ] && cp "$work"/*.txt "$work"/*.jsonl "$CERT_INSTALL_TEST_KEEP"/ 2>/dev/null
    rm -rf "$work"
}
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
cd "$work" || exit 1
mkdir -p pki private etc/httpd/conf.d bin flags issued
q() { openssl "$@" 2>/dev/null; }

# ---- a test CA: a root and the intermediate that signs; the certificates in use, about to expire ----
q req -x509 -newkey rsa:2048 -nodes -keyout root.key -out root.crt -days 400 -subj "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN=TEST ROOT CA 3" \
    -addext basicConstraints=critical,CA:TRUE -addext keyUsage=critical,keyCertSign,cRLSign
q req -new -newkey rsa:2048 -nodes -keyout int.key -out int.csr -subj "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN=TEST ID CA-70"
printf 'basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign,cRLSign\n' > ca.ext
q x509 -req -in int.csr -CA root.crt -CAkey root.key -CAcreateserial -days 390 -out int.crt -extfile ca.ext
sign() {  # sign NAME DAYS SUBJECT SAN KEYFILE -> pki/NAME.crt (the key in KEYFILE)
    q req -new -newkey rsa:2048 -nodes -keyout "$5" -out "$1.csr" -subj "$3" -addext "subjectAltName=$4"
    q x509 -req -in "$1.csr" -CA int.crt -CAkey int.key -CAcreateserial -days "$2" -out "pki/$1.crt" -copy_extensions copy
}
sign web01 20 "/C=US/O=U.S. Government/OU=DoD/OU=PKI/CN=web01.example.mil" "DNS:web01.example.mil,DNS:web.example.mil" private/web01.key
sign app01 15 "/C=US/O=U.S. Government/CN=app01.example.mil" "DNS:app01.example.mil" private/app01.key
sign tlsonly 20 "/C=US/O=U.S. Government/CN=tlsonly.example.mil" "DNS:tlsonly.example.mil" tlsonly.key
mv pki/tlsonly.crt tlsonly.crt
cat pki/web01.crt int.crt > pki/web01-fullchain.pem
chmod 0640 private/web01.key pki/web01-fullchain.pem
for f in pki/web01.crt pki/web01-fullchain.pem pki/app01.crt tlsonly.crt private/web01.key; do
    [ -s "$f" ] || { echo "ABORT - test file $f not made"; exit 1; }
done
fp() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d : | tr A-F a-f; }
served() { echo | timeout 5 openssl s_client -connect "127.0.0.1:$1" -servername web01.example.mil 2>/dev/null | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 | tr -d : | tr A-F a-f; }
old_fp="$(fp pki/web01.crt)"
cp pki/web01.crt old-web01.crt; cp pki/web01-fullchain.pem old-fullchain.pem; cp private/web01.key old-web01.key

# ---- the web server's configuration, and the stand-ins: the server, systemctl, ss, ps, apachectl ----
cat > etc/httpd/conf.d/ssl.conf <<EOF
<VirtualHost _default_:$tls1>
SSLEngine on
SSLCertificateFile $work/pki/web01-fullchain.pem
SSLCertificateKeyFile $work/private/web01.key
</VirtualHost>
EOF
cat > bin/start-httpd <<EOF
#!/bin/bash
# the web server: serves what its configuration names (or, with flags/reload_stale, the old copies)
[ -f $work/httpd.pid ] && kill \$(cat $work/httpd.pid) 2>/dev/null && sleep 0.3
cert=$work/pki/web01-fullchain.pem; key=$work/private/web01.key
[ -e $work/flags/reload_stale ] && cert=$work/old-fullchain.pem && key=$work/old-web01.key
setsid openssl s_server -accept $tls1 -cert \$cert -key \$key -quiet </dev/null >/dev/null 2>&1 &
echo \$! > $work/httpd.pid
for _ in \$(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$tls1) 2>/dev/null && exit 0; sleep 0.1; done
exit 1
EOF
cat > bin/systemctl <<EOF
#!/bin/bash
echo "\$*" >> $work/systemctl.log
case "\$1" in
  show) u="\${@: -1}"
        case "\$u" in
          httpd.service) printf 'LoadState=loaded\nActiveState=active\nCanReload=yes\n' ;;
          appd.service)  printf 'LoadState=loaded\nActiveState=active\nCanReload=no\n' ;;
          *)             printf 'LoadState=not-found\nActiveState=inactive\nCanReload=no\n' ;;
        esac ;;
  reload) [ -e $work/flags/reload_fail ] && { echo "Job for \$2 failed." >&2; exit 1; }
          [ "\$2" = httpd.service ] && exec $work/bin/start-httpd
          exit 0 ;;
  *) exit 0 ;;
esac
EOF
cat > bin/ss <<EOF
#!/bin/bash
echo 'State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process'
echo "LISTEN 0      128    127.0.0.1:$tls1      0.0.0.0:*     users:((\"httpd\",pid=\$(cat $work/httpd.pid),fd=3))"
echo "LISTEN 0      128    127.0.0.1:$tls2      0.0.0.0:*     users:((\"openssl\",pid=\$(cat $work/tlsonly.pid),fd=3))"
EOF
cat > bin/ps <<EOF
#!/bin/bash
pid="\${@: -1}"
[ "\$pid" = "\$(cat $work/httpd.pid)" ] && { echo httpd.service; exit 0; }
echo -
EOF
cat > bin/apachectl <<EOF
#!/bin/bash
[ -e $work/flags/configtest_fail ] && { echo "AH00526: Syntax error on line 4 of ssl.conf" >&2; exit 1; }
a=\$(openssl x509 -in $work/pki/web01-fullchain.pem -noout -pubkey); b=\$(openssl pkey -in $work/private/web01.key -pubout)
[ "\$a" = "\$b" ] && { echo "Syntax OK"; exit 0; }
echo "AH02565: Certificate and private key do not match" >&2; exit 1
EOF
chmod +x bin/*
bin/start-httpd || { echo "ABORT - the stand-in web server did not start"; exit 1; }
openssl s_server -accept "$tls2" -cert tlsonly.crt -key tlsonly.key -quiet </dev/null >/dev/null 2>&1 & pids+=($!); echo $! > tlsonly.pid
SNMOCK_LOG="$work/requests.jsonl" python3 "$repo/tests/servicenow/snmock.py" "$port" >/dev/null 2>&1 & pids+=($!)
for p in "$tls1" "$tls2" "$port"; do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done
: > requests.jsonl
[ "$(served "$tls1")" = "$old_fp" ] && ok "the stand-in web server serves the old certificate" || bad "web server: $(served "$tls1") not $old_fp"

cat > inv.yml <<EOF
all:
  hosts:
    web01.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
EOF
vars="{\"check_certs_discover\": false, \"cert_report_discover\": false, \"site_fail_on\": [],
       \"check_certs_files\": [\"$work/pki/*.crt\", \"$work/pki/*.pem\"],
       \"check_certs_endpoints\": [{\"name\": \"web\", \"host\": \"127.0.0.1\", \"port\": $tls1}, {\"name\": \"tlsonly\", \"host\": \"127.0.0.1\", \"port\": $tls2}],
       \"cert_renew_dir\": \"$work/renew\", \"cert_skip_groups\": [],
       \"cert_renew_servicenow\": \"catalog\", \"cert_renew_catalog_item\": \"dod pki certificate request\",
       \"cert_renew_catalog_variables\": {\"requested_for\": \"jdoe\", \"contact_method\": \"Email\", \"location\": \"Building 100\"},
       \"cert_install_known_services\": [{\"dir\": \"$work/etc/httpd\", \"service\": \"httpd\", \"test\": [\"apachectl\", \"configtest\"]}],
       \"cert_install_key_dirs\": [\"$work/private\"], \"cert_install_verify_wait\": 5}"
run() {  # run OUTFILE [ansible-playbook args...]
    local out="$1"; shift
    (cd "$repo" && env PATH="$work/bin:$PATH" SN_HOST="http://127.0.0.1:$port" SN_USERNAME=api SN_PASSWORD=goodpw ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 \
        "$ap" -i "$work/inv.yml" "$@" -e "$vars") > "$out" 2>&1
}
has() {  # has NAME FILE TEXT...
    local name="$1" f="$2"; shift 2
    local t
    for t in "$@"; do grep -qF -- "$t" "$f" || { bad "$name: $f lacks: $t"; return; }; done
    ok "$name"
}
stats() {  # stats OUTFILE > plan.json - the set_stats the approval hands on
    "$py" - "$1" <<'EOPY'
import json, sys
lines = [l for l in open(sys.argv[1]) if l.startswith("\tRUN: ")]
print(json.dumps(json.loads(lines[0][len("\tRUN: "):]) if lines else {}))
EOPY
}
sn() {  # sn METHOD PATH [FILE CONTENT-TYPE] - the "PKI" on the fake ServiceNow
    "$py" - "$port" "$@" <<'EOPY'
import base64, json, sys, urllib.request
port, method, path = sys.argv[1:4]
data = open(sys.argv[4], "rb").read() if len(sys.argv) > 4 else None
ctype = sys.argv[5] if len(sys.argv) > 5 else "application/json"
r = urllib.request.Request("http://127.0.0.1:%s%s" % (port, path), data=data, method=method,
                           headers={"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode(), "Content-Type": ctype})
print(json.dumps(json.loads(urllib.request.urlopen(r).read() or b"{}").get("result")))
EOPY
}
ritm_of() {  # ritm_of HOSTNAME-PREFIX -> the sys_id of the RITM carrying that host's CSR
    sn GET "/api/now/attachment?sysparm_query=file_nameSTARTSWITH$1-%5Etable_nameINsc_req_item,ZZ_YYsc_req_item" \
        | "$py" -c 'import json,sys; r=json.load(sys.stdin); print(r[0]["table_sys_id"] if r else "")'
}

# ---- 1. the renewal: the CSRs, ordered from the catalog item ----
run renew.txt playbooks/cert_renew_csr.yml; rc=$?
[ $rc -eq 0 ] && ok "renewal (catalog): exit 0" || { bad "renewal: exit $rc"; tail -30 renew.txt; }
n="$(ls renew/*.csr 2>/dev/null | wc -l)"
[ "$n" = 3 ] && ok "3 CSRs: web01 (file, chain file, port: one), app01, tlsonly (a port alone)" || bad "$n CSRs: $(ls renew 2>/dev/null)"
web_ritm="$(ritm_of web01.example.mil)"; app_ritm="$(ritm_of app01.example.mil)"; tls_ritm="$(ritm_of tlsonly.example.mil)"
[ -n "$web_ritm" ] && [ -n "$app_ritm" ] && [ -n "$tls_ritm" ] && ok "each CSR is on its request" || bad "the requests: web $web_ritm app $app_ritm tls $tls_ritm"
: > requests.jsonl

# ---- 2. nothing issued yet: waiting, green, no plan ----
run wait.txt playbooks/cert_install_check.yml; rc=$?
[ $rc -eq 0 ] && has "nothing issued yet: green, each one waiting on its request" wait.txt "WAITING | web01.example.mil |" "no certificate for this key on it yet" \
    || { bad "waiting: exit $rc"; tail -30 wait.txt; }
[ "$(stats wait.txt)" = "{}" ] && ok "nothing issued yet: no plan handed on" || bad "waiting: a plan $(stats wait.txt | head -c 200)"
grep -qE '"method": "(POST|PATCH)"' requests.jsonl && bad "the check wrote to ServiceNow" || ok "the check only reads ServiceNow"

# ---- 3. the PKI issues them ----
issue() {  # issue NAME RITM -> issued/NAME.crt signed from the renewal's CSR
    q x509 -req -in "$(ls renew/$1.example.mil-*.csr | head -1)" -CA int.crt -CAkey int.key -CAcreateserial -days 400 -out "issued/$1.crt" -copy_extensions copy
}
issue web01; issue app01; issue tlsonly
q x509 -in issued/web01.crt -outform DER -out issued/web01.cer
q crl2pkcs7 -nocrl -certfile int.crt -certfile root.crt -outform DER -out issued/chain.p7b
q crl2pkcs7 -nocrl -certfile issued/app01.crt -certfile int.crt -certfile root.crt -out issued/app01.p7b
new_fp="$(fp issued/web01.crt)"
sn POST "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=$web_ritm&file_name=other.cer" pki/app01.crt application/pkix-cert >/dev/null
sn POST "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=$web_ritm&file_name=web01.example.mil.cer" issued/web01.cer application/pkix-cert >/dev/null
sn POST "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=$web_ritm&file_name=DoD_chain.p7b" issued/chain.p7b application/x-pkcs7-certificates >/dev/null
sn POST "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=$app_ritm&file_name=app01.p7b" issued/app01.p7b text/plain >/dev/null
sn POST "/api/now/attachment/file?table_name=sc_req_item&table_sys_id=$tls_ritm&file_name=tlsonly.pem" issued/tlsonly.crt text/plain >/dev/null
echo '{"state": "3"}' > closed.json
sn PATCH "/api/now/table/sc_req_item/$web_ritm" closed.json >/dev/null      # the PKI closes it: Closed Complete
: > requests.jsonl

# ---- 4. the check: the plan, NEEDS APPROVAL ----
run dry.txt playbooks/cert_install_check.yml --check; rc_dry=$?
run check.txt playbooks/cert_install_check.yml; rc=$?
[ $rc -ne 0 ] && [ $rc_dry -ne 0 ] && has "issued: red on purpose - NEEDS APPROVAL (a dry run says the same)" check.txt "NEEDS APPROVAL: 1 certificate(s) to install" \
    || { bad "check: exit $rc / dry $rc_dry"; tail -30 check.txt; }
run checkl.txt playbooks/cert_install_check.yml -l web01.example.mil; rc=$?     # a Limit of the host alone, no localhost
has "a Limit of the host alone (no localhost): the same plan" checkl.txt "NEEDS APPROVAL: 1 certificate(s) to install" \
    "READY | web01.example.mil | CN=web01.example.mil"
[ $rc -ne 0 ] && ! grep -q "skipping: no hosts matched" checkl.txt && ok "...no play skipped, red as the check without a Limit" \
    || bad "check with a Limit: exit $rc, or a play skipped"
grep -F "READY | web01.example.mil | CN=web01.example.mil" check.txt > ready.txt
has "web01 READY: its chain file, its key, reload httpd, the port" ready.txt "$work/pki/web01-fullchain.pem" "$work/private/web01.key" \
    "reload httpd.service" "then port $tls1 must serve it" "the new one: serial"
grep -qF "$work/pki/web01.crt" ready.txt && ok "web01: the leaf-only file too (no configuration names it: same key)" || bad "web01: pki/web01.crt not in the plan"
has "app01 held: no service found that uses it" check.txt "HELD | web01.example.mil | CN=app01.example.mil" "no service found that uses $work/pki/app01.crt" "cert_install_services"
has "tlsonly by hand: only seen on a port" check.txt "BY HAND | web01.example.mil | CN=tlsonly.example.mil" "only seen on a port (127.0.0.1:$tls2)"
grep -qE '"method": "(POST|PATCH)"' requests.jsonl && bad "the check wrote to ServiceNow" || ok "the check only reads ServiceNow, also with the certificates there"
stats check.txt > plan.json
"$py" - plan.json "$new_fp" <<'EOPY' && ok "the plan handed on: web01's certificate only, with the new certificate and its chain" || bad "the plan (above)"
import json, sys
p = json.load(open(sys.argv[1]))["cert_install_plan"]
items = p["web01.example.mil"]
assert [i["status"] for i in items] == ["ready"], items
i = items[0]
assert i["new"]["sha256"] == sys.argv[2], i["new"]
assert len(i["chain_pem"]) == 2, len(i["chain_pem"])
assert sorted(f["role"] for f in i["files"]) == ["cert", "cert", "key"], i["files"]
assert [u["unit"] for u in i["units"]] == ["httpd.service"], i["units"]
EOPY
[ "$(served "$tls1")" = "$old_fp" ] && [ "$(fp pki/web01.crt)" = "$old_fp" ] && ok "the check changed nothing" || bad "the check changed something"

# ---- 5. the install job: refused without a plan, or with a host it does not run on; a dry run ----
run noplan.txt playbooks/cert_install_approved.yml; rc=$?
[ $rc -ne 0 ] && has "on its own (no plan): refused" noplan.txt "Nothing to install: no approved plan" || { bad "no plan: exit $rc"; tail -10 noplan.txt; }
"$py" -c 'import json,sys; p=json.load(open(sys.argv[1])); p["cert_install_plan"]["web02.example.mil"]=p["cert_install_plan"]["web01.example.mil"]; json.dump(p, open(sys.argv[2],"w"))' plan.json plan2.json
run missing.txt playbooks/cert_install_approved.yml -e @"$work/plan2.json"; rc=$?
[ $rc -ne 0 ] && has "a host of the plan not in the job: refused, nothing installed" missing.txt "The approved plan has certificates for web02.example.mil" \
    && [ "$(fp pki/web01.crt)" = "$old_fp" ] || { bad "missing host: exit $rc"; tail -10 missing.txt; }
run apdry.txt playbooks/cert_install_approved.yml -e @"$work/plan.json" --check; rc=$?
[ $rc -eq 0 ] && has "dry run: would install" apdry.txt "WOULD INSTALL | web01.example.mil" && [ "$(fp pki/web01.crt)" = "$old_fp" ] && [ ! -d renew/backup ] \
    && [ "$(served "$tls1")" = "$old_fp" ] || { bad "dry run: exit $rc"; tail -20 apdry.txt; }

# ---- 6. the rollbacks: a failing configuration test; a reload after which the port serves the old one ----
touch flags/configtest_fail
run roll1.txt playbooks/cert_install_approved.yml -e @"$work/plan.json"; rc=$?
rm -f flags/configtest_fail
[ $rc -ne 0 ] && has "configuration test fails: rolled back, nothing reloaded" roll1.txt "ROLLED BACK | web01.example.mil" "the configuration test of httpd.service failed" \
    && cmp -s pki/web01-fullchain.pem old-fullchain.pem && cmp -s private/web01.key old-web01.key && cmp -s pki/web01.crt old-web01.crt \
    && ! grep -q '^reload' systemctl.log 2>/dev/null || { bad "configtest rollback: exit $rc"; tail -20 roll1.txt; }
[ "$(stat -c %a private/web01.key)" = 640 ] && ok "rolled back: the old key keeps its mode" || bad "key mode after rollback: $(stat -c %a private/web01.key)"
ls renew/web01.example.mil-*.key >/dev/null 2>&1 && ok "rolled back: the renewal still waits (its key and CSR in place)" || bad "rolled back: the renewal's files moved"
touch flags/reload_stale
run roll2.txt playbooks/cert_install_approved.yml -e @"$work/plan.json"; rc=$?
rm -f flags/reload_stale; bin/start-httpd
[ $rc -ne 0 ] && has "the port still serves the old one after the reload: rolled back, reloaded again" roll2.txt "ROLLED BACK | web01.example.mil" \
    "after the reload, 127.0.0.1:$tls1 serves a certificate" && cmp -s pki/web01-fullchain.pem old-fullchain.pem && cmp -s private/web01.key old-web01.key \
    && [ "$(grep -c '^reload httpd.service' systemctl.log)" = 2 ] || { bad "verify rollback: exit $rc, $(grep -c '^reload' systemctl.log 2>/dev/null) reloads"; tail -20 roll2.txt; }
: > systemctl.log

# ---- 7. for real ----
run real.txt playbooks/cert_install_approved.yml -e @"$work/plan.json"; rc=$?
[ $rc -eq 0 ] && has "installed: the files, the reload, the port serves it" real.txt "INSTALLED | web01.example.mil | CN=web01.example.mil" \
    "reloaded httpd.service" "127.0.0.1:$tls1 serves it" || { bad "install: exit $rc"; tail -30 real.txt; }
[ "$(served "$tls1")" = "$new_fp" ] && ok "the port serves the new certificate" || bad "the port serves $(served "$tls1")"
[ "$(fp pki/web01.crt)" = "$new_fp" ] && [ "$(grep -c 'BEGIN CERTIFICATE' pki/web01.crt)" = 1 ] && ok "pki/web01.crt: the new certificate, alone as before" || bad "pki/web01.crt"
[ "$(fp pki/web01-fullchain.pem)" = "$new_fp" ] && [ "$(grep -c 'BEGIN CERTIFICATE' pki/web01-fullchain.pem)" = 2 ] \
    && openssl crl2pkcs7 -nocrl -certfile pki/web01-fullchain.pem 2>/dev/null | openssl pkcs7 -print_certs -noout 2>/dev/null | grep '^subject' | grep -q "TEST ID CA-70" \
    && ! openssl crl2pkcs7 -nocrl -certfile pki/web01-fullchain.pem 2>/dev/null | openssl pkcs7 -print_certs -noout 2>/dev/null | grep '^subject' | grep -q "TEST ROOT CA 3" \
    && ok "the fullchain file: the new certificate and the intermediate - no root, as before" || bad "the fullchain file: $(grep -c 'BEGIN CERTIFICATE' pki/web01-fullchain.pem) certificates"
[ "$(openssl pkey -in private/web01.key -pubout 2>/dev/null)" = "$(openssl x509 -in issued/web01.crt -noout -pubkey)" ] && [ "$(stat -c %a private/web01.key)" = 640 ] \
    && [ "$(stat -c %a pki/web01-fullchain.pem)" = 640 ] && ok "the key file: the new key, the old mode (0640); the chain file keeps 0640" || bad "the key file"
bdir="$(ls -d renew/backup/*-web01.example.mil-* 2>/dev/null | tail -1)"     # the last: the two before were rolled back
[ -n "$bdir" ] && [ "$(stat -c %a "$bdir")" = 700 ] && [ "$(ls "$bdir/files" | wc -l)" = 3 ] && [ "$(stat -c %a "$bdir"/files/* | sort -u)" = 600 ] \
    && cmp -s "$(ls "$bdir"/files/*web01.key)" old-web01.key && ok "the backup: the 3 old files (0600) in a 0700 folder, the old key among them" || bad "the backup: $bdir"
[ -z "$(ls renew/*.key 2>/dev/null | grep web01)" ] && ls renew/installed/*-web01.example.mil-*/installed.json >/dev/null 2>&1 \
    && ok "the renewal's files moved to renew/installed" || bad "the renewal's files: $(ls renew)"
"$py" - requests.jsonl "$web_ritm" <<'EOPY' && ok "a work note on the request: what, where" || bad "the work note (above)"
import json, sys
notes = [json.loads(l) for l in open(sys.argv[1]) if '"PATCH"' in l]
notes = [r for r in notes if r["path"].endswith(sys.argv[2])]
assert len(notes) == 1, notes
t = notes[0]["body"]["work_notes"]
assert "Installed on web01.example.mil" in t and "web01-fullchain.pem" in t and "httpd.service" in t, t
EOPY
run again.txt playbooks/cert_install_check.yml; rc=$?
[ $rc -eq 0 ] && ! grep -q "READY | web01.example.mil | CN=web01" again.txt && ok "the check again: web01 no longer waits - green" || { bad "check again: exit $rc"; grep -E 'READY|NEEDS' again.txt | head; }

# ---- 8. the restore ----
run rdry.txt playbooks/cert_restore.yml --check; rc=$?
[ $rc -eq 0 ] && has "restore, nothing named: lists the backups" rdry.txt "Nothing named to restore" "backup $(basename "$bdir")" || { bad "restore list: exit $rc"; tail -10 rdry.txt; }
run restore.txt playbooks/cert_restore.yml -e cert_restore_only=web01; rc=$?
[ $rc -eq 0 ] && has "restore: put back, reloaded" restore.txt "RESTORED | $(basename "$bdir")" "reloaded httpd.service" || { bad "restore: exit $rc"; tail -20 restore.txt; }
[ "$(served "$tls1")" = "$old_fp" ] && cmp -s pki/web01-fullchain.pem old-fullchain.pem && cmp -s private/web01.key old-web01.key && cmp -s pki/web01.crt old-web01.crt \
    && [ "$(stat -c %a private/web01.key)" = 640 ] && ok "restored: the old files (and mode), the port serves the old certificate" || bad "restored files / port"
ls renew/web01.example.mil-*.key >/dev/null 2>&1 && ok "restored: the renewal waits again" || bad "restored: the renewal's files not back"
run again2.txt playbooks/cert_install_check.yml; rc=$?
[ $rc -ne 0 ] && has "the check after the restore: web01 ready again" again2.txt "READY | web01.example.mil | CN=web01.example.mil" || { bad "check after restore: exit $rc"; tail -10 again2.txt; }

# ---- 9. changed between the check and the approval: refused ----
stats again2.txt > plan3.json
cp issued/app01.crt pki/web01.crt        # someone put another certificate there meanwhile
run changed.txt playbooks/cert_install_approved.yml -e @"$work/plan3.json"; rc=$?
[ $rc -ne 0 ] && has "a file replaced since the check: refused, nothing installed" changed.txt "REFUSED | web01.example.mil" "changed since the approval" \
    && cmp -s pki/web01-fullchain.pem old-fullchain.pem && cmp -s private/web01.key old-web01.key || { bad "changed: exit $rc"; tail -20 changed.txt; }
cp old-web01.crt pki/web01.crt

# ---- 10. app01: a service named for it that cannot reload -> by hand ----
run app.txt playbooks/cert_install_check.yml -e '{"cert_install_services": [{"path": "*/app01.crt", "service": "appd"}]}'; rc=$?
has "app01 with its service named: it cannot reload - by hand" app.txt "BY HAND | web01.example.mil | CN=app01.example.mil" "appd.service cannot reload"

echo
[ "$fails" -eq 0 ] && echo "PASS: certificate install" || echo "FAILED: $fails"
exit "$fails"
