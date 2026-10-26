#!/usr/bin/env bash
# vCenter single sign-on and OCSP (roles/vmware_vm/tasks/sso_read.yml, library/site_vcenter_sso.py, plugins/filter/
# vcenter_sso_filters.py) against a stand-in vCenter 8.0 REST API (tests/vcsso/vcenter_mock.py, HTTPS) and a real
# OCSP responder (openssl ocsp) with a throwaway CA. In order: all well; an SSO service stopped; the test account
# refused; the test account authenticated without permission (fine); the STS certificate within the critical and the
# warning window (the health report only, never the watch); OCSP - a revoked test certificate, a responder that does
# not answer, a stale answer, a URL only; the credential's own login refused; the APIs not there (notes, no problem);
# nothing but reads reached vCenter, and the test account's session was closed.
#   bash tests/vcsso/run_vcsso_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${VCSSO_TEST_PYTHON:-$(command -v python3)}"
W="$(mktemp -d)"
pids=""
cleanup() { for p in $pids; do kill "$p" 2>/dev/null; done; rm -rf "$W"; }
trap cleanup EXIT
fails=0
pv="${VCSSO_PORT:-12601}" po="${VCSSO_OCSP_PORT:-12602}" pc="${VCSSO_CA_PORT:-12603}"
cd "$W" || exit 1
o() { openssl "$@" >/dev/null 2>&1 || { echo "ABORT - openssl $1"; exit 1; }; }
# a CA; the STS signing certificates (300, 40 and 9 days); the OCSP responder's certificate; two user certificates, one revoked
o req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 3650 -subj "/CN=Test Root CA"
printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\n' > leaf.ext
printf 'basicConstraints=CA:FALSE\nextendedKeyUsage=OCSPSigning\n' > ocsp.ext
# the user certificate names its OCSP responder and where its issuer's certificate is, as DoD certificates do
printf 'basicConstraints=CA:FALSE\nkeyUsage=digitalSignature\nauthorityInfoAccess=OCSP;URI:http://127.0.0.1:%s,caIssuers;URI:http://127.0.0.1:%s/ca.cer\n' "$po" "$pc" > aia.ext
for n in sts300:300 sts40:40 sts9:9 ocsp:365 user:365 gone:365 vc:365; do
    name=${n%%:*} days=${n##*:}
    o req -newkey rsa:2048 -nodes -keyout $name.key -out $name.csr -subj "/CN=$name.example.mil"
    o x509 -req -in $name.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out $name.pem -days $days -extfile $(case $name in ocsp) echo ocsp.ext;; user) echo aia.ext;; *) echo leaf.ext;; esac)
done
: > index.txt
o ca -gencrl -help 2>/dev/null; true
sgood=$(openssl x509 -in user.pem -noout -serial | cut -d= -f2) sgone=$(openssl x509 -in gone.pem -noout -serial | cut -d= -f2)
ex=$(date -u -d '+1 year' +%y%m%d%H%M%SZ) rv=$(date -u -d '-1 day' +%y%m%d%H%M%SZ)
printf 'V\t%s\t\t%s\tunknown\t/CN=user.example.mil\nR\t%s\t%s\t%s\tunknown\t/CN=gone.example.mil\n' "$ex" "$sgood" "$ex" "$rv" "$sgone" > index.txt
openssl ocsp -index index.txt -port "$po" -rsigner ocsp.pem -rkey ocsp.key -CA ca.pem -nmin 60 -ignore_err >/dev/null 2>&1 & pids="$pids $!"
mkdir -p pb/files pub && cp user.pem gone.pem ca.pem pb/files/ && openssl x509 -in ca.pem -outform DER -out pub/ca.cer
"$py" -m http.server "$pc" --bind 127.0.0.1 --directory "$W/pub" >/dev/null 2>&1 & pids="$pids $!"
# a responder like DISA's: it closes a bare GET without a reply, and answers an OCSP POST ("unauthorized" for a made-up
# certificate); and one whose server behind it is down (503)
ph=$((pc + 1)) pd=$((pc + 2))
"$py" -c 'import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509 import ocsp
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.close_connection = True
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = ocsp.OCSPResponseBuilder.build_unsuccessful(ocsp.OCSPResponseStatus.UNAUTHORIZED).public_bytes(Encoding.DER)
        self.send_response(int(sys.argv[2])); self.send_header("Content-Type", "application/ocsp-response")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()' "$ph" 200 >/dev/null 2>&1 & pids="$pids $!"
"$py" -c 'import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(503); self.end_headers()
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()' "$pd" >/dev/null 2>&1 & pids="$pids $!"
chain() { "$py" -c 'import json,sys; print(json.dumps({"active_cert_chain": {"cert_chain": [open(sys.argv[1]).read(), open("ca.pem").read()]}, "signing_cert_chains": []}))' "$1"; }
data() {  # data STS STATE [forbidden]
    cat > data.json <<J
{"users": {"administrator@vsphere.local": "adminpw", "svc-sso-test@example.mil": "testpw"}, "forbidden": [${3:-}],
 "services": {"sts": {"state": "$2", "health": "HEALTHY"}, "lookupsvc": {"state": "STARTED", "health": "HEALTHY"}, "trustmanagement": {"state": "STARTED", "health": "HEALTHY"}, "vpxd": {"state": "STARTED"}},
 "appliance": {"vmdird": {"state": "STARTED", "description": "VMware Directory Service"}, "vmafdd": {"state": "STARTED"}},
 "signing": $(chain $1.pem),
 "providers": [{"provider": "p1", "name": "example.mil", "config_tag": "ActiveDirectoryOverLdap", "is_default": true, "domain_names": ["example.mil"]}]}
J
}
data sts300 STARTED
"$py" "$here/vcenter_mock.py" "$pv" vc.pem vc.key data.json vc.log >/dev/null 2>&1 & pids="$pids $!"
sleep 1

run() {  # run NAME [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1"; shift
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && VMWARE_HOST="127.0.0.1:$pv" VMWARE_USER="${VU:-administrator@vsphere.local}" VMWARE_PASSWORD="${VP:-adminpw}" \
        VMWARE_SSO_TEST_USER=svc-sso-test@example.mil VMWARE_SSO_TEST_PASSWORD="${TP:-testpw}" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, tests/vcsso/sso_play.yml -e "ansible_python_interpreter=$py" -e vmware_validate_certs=false -e "playbook_dir=$W/pb" \
        -e "{\"vm_sso_ocsp\": [{\"url\": \"http://127.0.0.1:$po\", \"cert\": \"$W/pb/files/user.pem\", \"issuer\": \"$W/pb/files/ca.pem\"}]}" "${args[@]}") > "$W/out.txt" 2>&1
    local rc=$? miss=0
    [ $rc -ne 0 ] && { echo "FAIL - $tname: exit $rc"; grep -E 'fatal|ERROR|msg' "$W/out.txt" | head -8; fails=$((fails + 1)); return; }
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$W/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$W/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else sed -n '/CONDITIONS/,/^}/p' "$W/out.txt" | cut -c1-260 | head -24; fails=$((fails + 1)); fi
}

run "all well: nothing in the watch, nothing in the health report" -- \
    '"CONDITIONS",' '"PROBLEMS",' '"TITLE VMware health report: healthy"' \
    "OK | 127.0.0.1:$pv | sts started, lookupsvc started, trustmanagement started, vmdird started, vmafdd started" \
    "example.mil (default)" "svc-sso-test@example.mil: logs in" "OK | OCSP http://127.0.0.1:$po | successful, certificate good, next update" "!CRITICAL" "!WARNING"

data sts300 STOPPED
run "an SSO service stopped: critical in the watch and the report" -- \
    "CRITICAL vCenter 127.0.0.1:$pv's single sign-on service sts is stopped: nobody can log in to vCenter" "single sign-on: 1 critical"

data sts300 STARTED
TP=wrong run "the test account refused: critical" -- \
    "CRITICAL the test domain account svc-sso-test@example.mil cannot log in to vCenter 127.0.0.1:$pv: vCenter refused the login (HTTP 401)" "svc-sso-test@example.mil: REFUSED"

data sts300 STARTED '"svc-sso-test@example.mil"'
run "the test account authenticated, no permission in vCenter (403): single sign-on works" -- "svc-sso-test@example.mil: logs in" "!CRITICAL"

data sts9 STARTED
run "the STS certificate within 14 days: critical in the report, not in the watch" -- \
    "CRITICAL vCenter 127.0.0.1:$pv's STS signing certificate CN=sts9.example.mil expires in" '"CONDITIONS",' '"PROBLEMS",'
grep -A1 '"CONDITIONS",' "$W/out.txt" | grep -q '"PROBLEMS",' && echo "ok   - ... and the watch has no condition for it" || { echo "FAIL - the watch reports an STS expiry"; fails=$((fails + 1)); }
data sts40 STARTED
run "the STS certificate within 60 days: a warning" -- "WARNING vCenter 127.0.0.1:$pv's STS signing certificate CN=sts40.example.mil expires in" "single sign-on: 1 warning(s)"
data sts300 STARTED

run "OCSP: the test certificate revoked" -e "{\"vm_sso_ocsp\": [{\"url\": \"http://127.0.0.1:$po\", \"cert\": \"$W/pb/files/gone.pem\", \"issuer\": \"$W/pb/files/ca.pem\"}]}" -- \
    "CRITICAL the OCSP responder http://127.0.0.1:$po: the responder says the test certificate is revoked - smart card (CAC) logins"
run "OCSP: only the certificate - the responder's address and the issuer read from it" -e "{\"vm_sso_ocsp\": [{\"cert\": \"$W/pb/files/user.pem\"}]}" -- \
    "OK | OCSP http://127.0.0.1:$po | successful, certificate good, next update" "!CRITICAL"
run "OCSP: site_ocsp_certs, for both reports, when vm_sso_ocsp is empty" -e "{\"vm_sso_ocsp\": [], \"site_ocsp_certs\": [\"$W/pb/files/user.pem\"]}" -- \
    "OK | OCSP http://127.0.0.1:$po | successful, certificate good, next update" "!CRITICAL"
run "OCSP: a certificate that names no responder" -e "{\"vm_sso_ocsp\": [{\"cert\": \"$W/pb/files/gone.pem\"}]}" -- \
    "the certificate CN=gone.example.mil names no OCSP address (Authority Information Access): give the responder's url"
run "OCSP: a responder that does not answer" -e '{"vm_sso_ocsp": [{"url": "http://127.0.0.1:9"}]}' -- "CRITICAL the OCSP responder http://127.0.0.1:9: no answer"
run "OCSP: a stale answer (its next update passed)" -e "_vm_now=$(date -u -d '+1 day' +%Y-%m-%dT%H:%M:%SZ)" -- "the answer is stale: its next update"
run "OCSP: a URL only - a made-up OCSP request (this responder closes a bare GET)" -e "{\"vm_sso_ocsp\": [{\"url\": \"http://127.0.0.1:$ph/\"}]}" -- \
    "OK | OCSP http://127.0.0.1:$ph/ | answers (OCSP, unauthorized to a made-up certificate)" "!CRITICAL"
run "OCSP: a URL only - 503, the server behind it down" -e "{\"vm_sso_ocsp\": [{\"url\": \"http://127.0.0.1:$pd/\"}]}" -- \
    "CRITICAL the OCSP responder http://127.0.0.1:$pd/: the responder answered HTTP 503 to an OCSP request (the server behind the address is not answering)"
run "OCSP: a URL only - a web server that is not an OCSP responder (refuses the POST)" -e "{\"vm_sso_ocsp\": [{\"url\": \"http://127.0.0.1:$pc/ca.cer\"}]}" -- \
    "CRITICAL | OCSP http://127.0.0.1:$pc/ca.cer | the responder answered HTTP 501 to an OCSP request"
run "OCSP: a URL only - it answers" -e "{\"vm_sso_ocsp\": [{\"url\": \"http://127.0.0.1:$po\"}]}" -e vm_sso_ocsp_severity=warning -- "OK | OCSP http://127.0.0.1:$po | answers" "!CRITICAL"

VP=wrong run "the credential's own login refused: critical, and the test login is still tried" -- \
    "CRITICAL single sign-on: vCenter 127.0.0.1:$pv refused the credential's login (HTTP 401)" "svc-sso-test@example.mil: logs in"

cat > data.json <<J
{"users": {"administrator@vsphere.local": "adminpw", "svc-sso-test@example.mil": "testpw"}, "services": {"sts": {"state": "STARTED"}}, "appliance": null, "signing": null, "providers": null}
J
run "the APIs not there (an older vCenter): notes, no problem" -- "the STS signing certificate was not readable (HTTP 404)" "!CRITICAL" "!WARNING"

cat > data.json <<J
{"no_api": true, "users": {}}
J
run "no REST API at /api (an older vCenter, a simulator): a note, no problem, no test login" -e '{"vm_sso_ocsp": []}' -- \
    "has no REST API at /api (HTTP 404): single sign-on is read on vCenter 8.0" "!CRITICAL" "!logs in" "!REFUSED"

if grep -vE '^(GET /api/|POST /api/session |DELETE /api/session )' vc.log | grep -q .; then echo "FAIL - a request that is not a read reached vCenter"; grep -vE '^(GET|POST /api/session|DELETE /api/session)' vc.log | head -3; fails=$((fails + 1))
elif [ "$(grep -c '^POST /api/session svc-sso-test' vc.log)" -lt 3 ] || [ "$(grep -c '^DELETE /api/session svc-sso-test' vc.log)" -lt 3 ]; then echo "FAIL - the test account's sessions were not closed"; fails=$((fails + 1))
else echo "ok   - only reads reached vCenter ($(grep -c '^GET' vc.log) GETs), and each test login was logged out again"; fi

echo
if [ $fails -eq 0 ]; then echo "all vCenter single sign-on scenarios passed"; else echo "$fails vCenter single sign-on scenario(s) FAILED"; exit 1; fi
