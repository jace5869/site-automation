#!/usr/bin/env bash
# Network device certificates (playbooks/net_cert_report.yml, net_cert_renew.yml) against stand-in devices
# (tests/netcerts/tls_servers.py: TLS servers on 127.0.0.1, certificates made here) and a fake ServiceNow
# (tests/servicenow/snmock.py). Devices in the inventory group net_cert_devices: sw01 (20 days: due, warning),
# pr01 (expired 3 days ago: critical), core01 (200 days: not due), ups01 (its port closed: not read), and
# rtr01, a Cisco IOS router (10 days; its CLI: tests/netcerts/fake_ios.py). The report lists every one, says what the renewal would do, writes the
# inventory file (cert_inventory_files) and sends nothing. A sweep of 127.0.0.1/32 finds the certificates by
# itself. The renewal's dry run sends nothing; the real run opens one incident per certificate due (sw01, pr01,
# rtr01), a run again none more; with net_cert_cisco_csr (dry run) rtr01 would get a CSR, the others the incident only;
# for real (when cisco.ios, ansible.netcommon and paramiko are there): the key, the trustpoint and crypto pki enroll
# on rtr01, its CSR on rtr01's incident - and a run again makes no second one.
#   bash tests/netcerts/run_net_certs_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${NET_CERTS_TEST_PYTHON:-$(command -v python3)}"
port="${NET_CERTS_TEST_PORT:-18390}"
sw=$((port + 10)); pr=$((port + 11)); core=$((port + 12)); ups=$((port + 13)); rtr=$((port + 14)); ssh=$((port + 15))
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }
cd "$work" || exit 1

"$py" "$here/tls_servers.py" "$sw:sw01.example.mil:20" "$pr:pr01.example.mil:-3" "$core:core01.example.mil:200" "$rtr:rtr01.example.mil:10" \
    >/dev/null 2>&1 & pids+=($!)
SNMOCK_LOG="$work/requests.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$port" >/dev/null 2>&1 & pids+=($!)
for p in "$sw" "$pr" "$core" "$rtr" "$port"; do for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done
: > requests.jsonl                     # the readiness checks above

cat > inv.yml <<EOF
all:
  children:
    net_cert_devices:
      hosts:
        sw01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$sw]}
        pr01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$pr]}
        core01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$core]}
        ups01.example.mil: {ansible_host: 127.0.0.1, net_cert_ports: [$ups]}
        rtr01.example.mil: {ansible_host: 127.0.0.2, net_cert_address: 127.0.0.1, net_cert_ports: [$rtr], ansible_network_os: cisco.ios.ios,
                            ansible_connection: ansible.netcommon.network_cli, ansible_port: $ssh, ansible_user: admin, ansible_password: admin}
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
EOF
vars="{\"servicenow_assignment_group\": \"Network Operations\", \"net_cert_timeout\": 3}"
# the SSH to the stand-in Cisco device: a HOME of its own, so no known_hosts of yours is read or written
cols="${ANSIBLE_COLLECTIONS_PATH:-$HOME/.ansible/collections:/usr/share/ansible/collections}"
mkdir -p "$work/home"
run() {  # run OUTFILE sn|none [ansible-playbook args...]
    local out="$1" s="$2"; shift 2
    local sn=()
    [ "$s" = sn ] && sn=(SN_HOST="http://127.0.0.1:$port" SN_USERNAME=api SN_PASSWORD=goodpw)
    (cd "$repo" && env "${sn[@]}" HOME="$work/home" ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 "$ap" -i "$work/inv.yml" "$@" -e "$vars") > "$out" 2>&1
}
has() {  # has NAME FILE TEXT...
    local name="$1" f="$2"; shift 2
    local t
    for t in "$@"; do grep -qF -- "$t" "$f" || { bad "$name: $f lacks: $t"; tail -15 "$f" | sed 's/^/       /'; return; }; done
    ok "$name"
}
writes() { grep -cE '"method": "(POST|PATCH)"' requests.jsonl; }

# ---- 1. the report: every device, what the renewal would do; the inventory file; nothing sent ----
run report.txt sn playbooks/net_cert_report.yml -e cert_inventory_files=true -e "cert_inventory_dir=$work/inventory"
rc=$?
[ $rc -eq 0 ] && ok "the report ran" || bad "the report: exit $rc"
has "the report: 3 due of 4 read, each device and port, the closed port listed" report.txt \
    "Network device certificates: 3 due within 30 days, of 4" "CN=sw01.example.mil" "CN=pr01.example.mil" "CN=core01.example.mil" \
    "https://127.0.0.1:$sw" "ups01.example.mil" "refused"
has "the report: what the renewal would do - the incident only, Cisco CSRs off" report.txt \
    "the report only - Network device certificate renewal opens its incident"
[ "$(writes)" = 0 ] && ok "the report sent nothing to ServiceNow" || bad "the report wrote to ServiceNow"
"$py" - "$work/inventory/net-certificates.json" <<'EOPY' && ok "the inventory file: 4 certificates, source net, in use, fingerprints, self-signed" || bad "the inventory file (above)"
import json, sys
recs = json.load(open(sys.argv[1]))
errs = []
if sorted(r["host"] for r in recs) != ["core01.example.mil", "pr01.example.mil", "rtr01.example.mil", "sw01.example.mil"]: errs.append([r["host"] for r in recs])
for r in recs:
    if r["source"] != "net" or r["in_use"] is not True or len(r["sha1"]) != 40 or r["tier"] != "self-signed" or "IP:127.0.0.1" not in r["sans"] \
       or r["public_key"] != "RSA 2048": errs.append(r); break
st = {r["host"]: r["status"] for r in recs}
if (st.get("pr01.example.mil"), st.get("sw01.example.mil"), st.get("core01.example.mil")) != ("expired", "warning", "ok"): errs.append(st)
print("\n".join("       %s" % e for e in errs))
sys.exit(1 if errs else 0)
EOPY

# ---- 2. a sweep: the addresses of a subnet, each port - found without the inventory ----
run sweep.txt none playbooks/net_cert_report.yml -e net_cert_targets=subnets -e "{\"net_cert_subnets\": [\"127.0.0.1/32\"], \"net_cert_ports\": [$sw, $pr, $ups]}"
has "a sweep of 127.0.0.1/32: two certificates found, the closed port only counted" sweep.txt \
    "Network device certificates: 2 due within 30 days, of 2" "1 addresses, 3 connections tried, 2 answered with a certificate"
run toobig.txt none playbooks/net_cert_report.yml -e net_cert_targets=subnets -e '{"net_cert_subnets": ["198.18.0.0/15"]}'
has "a sweep bigger than net_cert_max_addresses is refused" toobig.txt "more than 4096 addresses"

# ---- 3. the renewal, a dry run: nothing sent ----
run dry.txt sn playbooks/net_cert_renew.yml --check
[ $? -eq 0 ] && ok "the renewal's dry run ran" || { bad "the renewal's dry run"; tail -20 dry.txt; }
[ "$(writes)" = 0 ] && ok "the dry run sent nothing to ServiceNow" || bad "the dry run wrote to ServiceNow"

# ---- 4. the renewal: one incident per certificate due, the incident only ----
run real.txt sn playbooks/net_cert_renew.yml
[ $? -eq 0 ] && ok "the renewal ran" || { bad "the renewal: exit"; tail -25 real.txt; }
"$py" - requests.jsonl <<'EOPY' && ok "ServiceNow: 3 incidents (sw01 warning, pr01 critical, rtr01 warning), each with the device, port and steps; none for core01" || bad "ServiceNow incidents (above)"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
inc = [r for r in reqs if r.get("method") == "POST" and "/api/now/table/" in r.get("path", "")]
errs = []
txt = json.dumps(inc)
for h in ("sw01.example.mil", "pr01.example.mil", "rtr01.example.mil"):
    if h not in txt: errs.append("no incident for " + h)
if "core01" in txt: errs.append("an incident for core01")
if len(inc) != 3: errs.append("%d incidents" % len(inc))
if "Renew it on the device" not in txt: errs.append("no renewal steps")
print("\n".join("       " + e for e in errs))
sys.exit(1 if errs else 0)
EOPY
n1=$(grep -c '"method": "POST"' requests.jsonl)
run again.txt sn playbooks/net_cert_renew.yml
n2=$(grep -c '"method": "POST"' requests.jsonl)
[ "$n1" = "$n2" ] && ok "a run again: no new incident (the same ones are noted)" || bad "a run again opened $((n2 - n1)) more"

# ---- 5. Cisco CSRs on, a dry run: rtr01 (IOS) would get one, the others the incident only ----
run cisco.txt sn playbooks/net_cert_renew.yml --check -e net_cert_cisco_csr=true
has "net_cert_cisco_csr (dry run): rtr01 would get a CSR made on the device; sw01 and pr01 the incident only" cisco.txt \
    "rtr01.example.mil | rtr01-$rtr.csr | would make the CSR, and would attach it to INC" "not a Cisco IOS / IOS-XE host in the inventory"

# ---- 6. Cisco CSRs on, for real: the key, the trustpoint and the CSR made on rtr01 (a stand-in IOS-XE), on its incident ----
if "$py" -c "import paramiko" 2>/dev/null && (cd "$repo" && ansible-galaxy collection list 2>/dev/null | grep -q '^cisco.ios ') \
   && (cd "$repo" && ansible-galaxy collection list 2>/dev/null | grep -q '^ansible.netcommon '); then
    "$py" "$here/fake_ios.py" "$ssh" "$work/ios.log" rtr01 >/dev/null 2>&1 & pids+=($!)
    for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.2/$ssh") 2>/dev/null && break; sleep 0.1; done
    run ciscoreal.txt sn playbooks/net_cert_renew.yml -e net_cert_cisco_csr=true
    [ $? -eq 0 ] && ok "net_cert_cisco_csr: the renewal ran" || { bad "net_cert_cisco_csr: exit"; tail -30 ciscoreal.txt | sed 's/^/       /'; }
    tp="SA-RENEW-$(date -u -d '+10 days' +%Y%m%d)"
    has "...on rtr01: a new key, the trustpoint with the old certificate's subject and names, crypto pki enroll answered" ios.log \
        "config| crypto key generate rsa general-keys label $tp modulus 2048" "config| crypto pki trustpoint $tp" \
        "tp| subject-name O=Test Net,CN=rtr01.example.mil" "tp| subject-alt-name rtr01.example.mil" "tp| rsakeypair $tp" \
        "config| crypto pki enroll $tp" "answer| yes" "answer| no" "config| end"
    "$py" - requests.jsonl <<'EOPY' && ok "...rtr01's CSR attached to its incident, with the steps to import the issued certificate on the device" || bad "...the CSR on the incident (above)"
import json, sys
reqs = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
att = [r for r in reqs if "/api/now/attachment" in r.get("path", "") and r.get("method") == "POST"]
notes = json.dumps([r for r in reqs if r.get("method") == "PATCH"])
errs = []
if len(att) != 1 or "rtr01-" not in json.dumps(att[0]): errs.append("attachments: %s" % [a.get("path") for a in att])
elif "BEGIN CERTIFICATE REQUEST" not in json.dumps(att[0]): errs.append("not a CSR")
if "crypto pki import" not in notes or "write memory" not in notes: errs.append("the work note's steps")
print("\n".join("       " + e for e in errs))
sys.exit(1 if errs else 0)
EOPY
    n=$(grep -c 'crypto pki enroll' "$work/ios.log")
    run ciscoagain.txt sn playbooks/net_cert_renew.yml -e net_cert_cisco_csr=true
    [ "$(grep -c 'crypto pki enroll' "$work/ios.log")" = "$n" ] && ok "...a run again: no second CSR (the incident has it: a new one would replace the waiting key)" \
        || bad "...a run again made another CSR"
    grep -q "PRIVATE KEY" ciscoreal.txt ciscoagain.txt requests.jsonl && bad "a private key in the output or sent" || ok "...no private key anywhere"
    cisco_ran=1
else
    echo "skip - the real Cisco CSR (needs paramiko, and the collections cisco.ios and ansible.netcommon)"
fi

# ---- 7. cert_renew_servicenow: catalog - no incident. A device without a CSR made here: the report only; rtr01's CSR is
# ordered from the PKI's catalog item, and that request is its record ----
cport=$((port + 16))
SNMOCK_LOG="$work/requests-cat.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$cport" >/dev/null 2>&1 & pids+=($!)
for _ in $(seq 1 100); do (exec 3<>"/dev/tcp/127.0.0.1/$cport") 2>/dev/null && break; sleep 0.1; done
: > requests-cat.jsonl
cat='{"cert_renew_servicenow": "catalog", "cert_renew_catalog_item": "DoD PKI Certificate Request",
      "cert_renew_catalog_variables": {"requested_for": "jdoe", "contact_method": "email"}}'
catwrites() { grep -cE '"method": "(POST|PATCH)"' requests-cat.jsonl; }
(port=$cport; run catdry.txt sn playbooks/net_cert_renew.yml --check -e net_cert_cisco_csr=true -e "$cat")
[ "$(catwrites)" = 0 ] && has "catalog, dry run: rtr01 would get a CSR, and be ordered - nothing sent" catdry.txt \
    "rtr01.example.mil | rtr01-$rtr.csr | would make the CSR, and order it" || bad "catalog, dry run: $(catwrites) sent"
(port=$cport; run catnocsr.txt sn playbooks/net_cert_renew.yml -e "$cat"); rc=$?
[ $rc -eq 0 ] && [ "$(catwrites)" = 0 ] && ok "catalog, no CSR made here (net_cert_cisco_csr off): no incident, no order - the report only" \
    || { bad "catalog, no CSR: exit $rc, $(catwrites) sent"; tail -20 catnocsr.txt; }
if [ "${cisco_ran:-0}" = 1 ]; then
    n=$(grep -c 'crypto pki enroll' "$work/ios.log")
    (port=$cport; run catcisco.txt sn playbooks/net_cert_renew.yml -e net_cert_cisco_csr=true -e "$cat"); rc=$?
    [ $rc -eq 0 ] && [ "$(grep -c 'crypto pki enroll' "$work/ios.log")" = $((n + 1)) ] && has "catalog: rtr01's CSR made on the device and ordered, in the item's CSR question" \
        catcisco.txt "ORDERED RITM" "| rtr01.example.mil | rtr01-$rtr-" "the CSR is in the request (its question csr_file)" \
        || { bad "catalog, rtr01: exit $rc"; tail -25 catcisco.txt | sed 's/^/       /'; }
    grep -q '/table/incident' requests-cat.jsonl && bad "catalog: an incident was opened" || ok "...no incident"
    n=$(grep -c 'crypto pki enroll' "$work/ios.log"); w=$(catwrites)
    (port=$cport; run catcisco2.txt sn playbooks/net_cert_renew.yml -e net_cert_cisco_csr=true -e "$cat"); rc=$?
    [ $rc -eq 0 ] && [ "$(grep -c 'crypto pki enroll' "$work/ios.log")" = "$n" ] && [ "$(catwrites)" = "$w" ] \
        && has "catalog, a run again: the request is the CSR's record - no second CSR, no second order" catcisco2.txt "ORDERED ALREADY | rtr01.example.mil" \
        || { bad "catalog, rtr01 again: exit $rc"; tail -20 catcisco2.txt | sed 's/^/       /'; }
fi

[ "$fails" -eq 0 ] && echo "ok   - all network device certificate scenarios passed" || { echo "$fails FAILED"; exit 1; }
