#!/usr/bin/env bash
# The AD health report's reader mode (playbooks/ad_health_report.yml, ad_health_mode: reader / auto) against a real
# Active Directory: two Samba AD domain controllers in containers (tests/ad/samba - samba-tool domain provision, then a
# second DC joined), the made-up domain corp.example.mil, a CA of the test's own for their LDAPS certificates
# (make_test_ca.py; DC02's expires in 5 days). The job binds as svc_aap_adreader, a plain domain user with nothing
# granted, from this machine: the DCs' LDAP, LDAPS and Kerberos ports and the first DC's DNS are published on
# 127.0.0.1 (ad_reader_endpoints, ad_reader_dns_servers). Cases: a healthy domain read as a plain user (what it may not
# read said as not read), auto with no WinRM -> reader, admin as before, the certificate, an untrusted certificate (no
# password sent), a wrong password, a DC in the inventory AD does not have, short inventory names (the domain from the DCs'
# rootDSE, or an FQDN ansible_host), the privileged groups since the last run
# (from the last job's artifact in a fake AAP, and from ad_reader_history_file - not written in Check mode), the
# accounts locked out and disabled, replication failing with a DC stopped.
#   bash tests/ad/run_ad_reader_test.sh      (ANSIBLE_PLAYBOOK=...; AD_TEST_RUNTIME=docker; AD_TEST_RUNTIME_OPTS=
#   "--cgroup-manager=cgroupfs" for podman where systemd cannot place it; AD_TEST_PORT_BASE=28300; AD_TEST_SUBNET=10.89.77;
#   AD_TEST_OUT=<dir> keeps each run's output)
# Needs podman or docker, ldap3, dnspython, cryptography and pywinrm; kinit, when there is one, gets a TGT from each DC.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
apdir="$(dirname "$(command -v "$ap")")"
py="$("$apdir/python" -c 'import sys; print(sys.executable)' 2>/dev/null || command -v python3)"
rt="${AD_TEST_RUNTIME:-podman}"
read -r -a rtopts <<< "${AD_TEST_RUNTIME_OPTS:-}"
base="${AD_TEST_PORT_BASE:-28300}"
subnet="${AD_TEST_SUBNET:-10.89.77}"
image="localhost/sa-samba-dc:test"
net="sa-adtest-$$"
dc1="sa-adtest-dc01-$$"
dc2="sa-adtest-dc02-$$"
pass_admin="Adm1n-Test-Pass"
pass_reader="Read3r-Test-Pass"
work="$(mktemp -d)"
aap_pid=""
R() { "$rt" "${rtopts[@]}" "$@"; }
cleanup() {
    [ -n "$aap_pid" ] && kill "$aap_pid" 2>/dev/null
    R rm -f "$dc1" "$dc2" > /dev/null 2>&1
    R network rm "$net" > /dev/null 2>&1
    rm -rf "$work"
}
trap cleanup EXIT
fails=0
runs=0
p_ldap1=$base p_ldaps1=$((base + 1)) p_krb1=$((base + 2)) p_dns=$((base + 3))
p_ldap2=$((base + 10)) p_ldaps2=$((base + 11)) p_krb2=$((base + 12))
p_closed=$((base + 19)) p_aap=$((base + 20))

for lib in ldap3 dns cryptography winrm; do
    "$py" -c "import $lib" 2>/dev/null || { echo "SKIP - the Python library $lib is missing for $py"; exit 1; }
done

# ---- the domain ------------------------------------------------------------------------------------------------------
echo "building the test DC image (Alpine, samba-dc)"
R build -q -t "$image" -f "$here/samba/Containerfile" "$here/samba" > "$work/build.log" 2>&1 || { cat "$work/build.log"; exit 1; }
"$py" "$here/make_test_ca.py" "$work/certs" dc01=400 dc02=5 > /dev/null || exit 1
R network create --subnet "$subnet.0/24" "$net" > /dev/null || exit 1

wait_for() {  # wait_for CONTAINER SECONDS: its start script said SA-DC-READY, and LDAP answers on PORT
    local c="$1" n="$2" port="$3" i
    for i in $(seq 1 "$n"); do
        if R logs "$c" 2>&1 | grep -q SA-DC-READY && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then sleep 3; return 0; fi
        R ps -a --format '{{.Names}} {{.Status}}' | grep -q "^$c Exited" && break
        sleep 2
    done
    echo "the test DC $c did not start:"; R logs "$c" 2>&1 | tail -20; exit 1
}
on1() { R exec "$dc1" "$@"; }
on2() { R exec "$dc2" "$@"; }

echo "starting DC01 (samba-tool domain provision)"
R run -d --name "$dc1" --hostname dc01 --network "$net" --ip "$subnet.11" --dns "$subnet.11" \
    -e SA_ROLE=provision -e SA_IP="$subnet.11" -e SA_ADMIN_PASS="$pass_admin" -v "$work/certs:/sa-certs:ro,z" \
    -p "127.0.0.1:$p_ldap1:389" -p "127.0.0.1:$p_ldaps1:636" -p "127.0.0.1:$p_krb1:88" -p "127.0.0.1:$p_krb1:88/udp" \
    -p "127.0.0.1:$p_dns:53" -p "127.0.0.1:$p_dns:53/udp" "$image" > /dev/null || exit 1
wait_for "$dc1" 120 "$p_ldap1"
# the accounts the cases use - made now, so that their creation is older than the account-change case's window
on1 samba-tool user create svc_aap_adreader "$pass_reader" > /dev/null
for u in leaver lockme gone jdoe; do on1 samba-tool user create "$u" "Us3r-$u-Pass" > /dev/null; done
on1 samba-tool group addmembers "Domain Admins" leaver > /dev/null
on1 samba-tool domain passwordsettings set --account-lockout-threshold=5 > /dev/null
on1 sa-dc-admin recycle-bin > /dev/null
made_at=$(date +%s)
# DC01's replication service answering before the join asks it for the partitions (1.48.0)
for i in $(seq 1 30); do
    on1 samba-tool drs bind dc01 -U Administrator --password="$pass_admin" > /dev/null 2>&1 && break
    [ "$i" = 30 ] && { echo "DC01's replication service did not answer"; exit 1; }
    sleep 2
done

echo "starting DC02 (samba-tool domain join)"
joined_from=$(date +%s)
R run -d --name "$dc2" --hostname dc02 --network "$net" --ip "$subnet.12" --dns "$subnet.11" \
    -e SA_ROLE=join -e SA_IP="$subnet.12" -e SA_ADMIN_PASS="$pass_admin" -v "$work/certs:/sa-certs:ro,z" \
    -p "127.0.0.1:$p_ldap2:389" -p "127.0.0.1:$p_ldaps2:636" -p "127.0.0.1:$p_krb2:88" -p "127.0.0.1:$p_krb2:88/udp" \
    "$image" > /dev/null || exit 1
wait_for "$dc2" 240 "$p_ldap2"            # the join: up to 3 tries (tests/ad/samba/start.sh)
echo "DC02 joined and serving after $(( $(date +%s) - joined_from )) s$(R logs "$dc2" 2>&1 | grep -c 'the join failed (try' | sed 's/^0$//; s/^[1-9].*/, after a failed try/')"
# DC02's DNS records (its own update cannot sign them here), then replication both ways, so each DC has its partner -
# again until the domain is settled: every partition of each DC has the other in its repsFrom, with no failure, and DNS
# (DC01's) has both DCs in every SRV record. The cases start only then: a domain still settling after the join made a
# healthy case flaky (1.4.0).
nc="DC=corp,DC=example,DC=mil"
settle() {
    on2 samba_dnsupdate --use-samba-tool > /dev/null 2>&1
    on1 samba-tool drs kcc dc01 -U Administrator --password="$pass_admin" > /dev/null 2>&1
    on2 samba-tool drs kcc dc02 -U Administrator --password="$pass_admin" > /dev/null 2>&1
    local p
    for p in "$nc" "CN=Configuration,$nc" "CN=Schema,CN=Configuration,$nc" "DC=DomainDnsZones,$nc" "DC=ForestDnsZones,$nc"; do
        on1 samba-tool drs replicate dc01 dc02 "$p" -U Administrator --password="$pass_admin" > /dev/null 2>&1
        on2 samba-tool drs replicate dc02 dc01 "$p" -U Administrator --password="$pass_admin" > /dev/null 2>&1
    done
}
settled() {  # '' when settled, else what is not yet - read as the plain user, as the job reads it
    "$py" - "$repo/roles/ad_report/library" "$p_ldaps1" "$p_ldaps2" "$p_dns" <<'PY'
import ssl, sys
import dns.resolver, ldap3
sys.path.insert(0, sys.argv[1])
from site_ad_reader import parse_reps_from
nc = "DC=corp,DC=example,DC=mil"
ncs = [nc, "CN=Configuration," + nc, "CN=Schema,CN=Configuration," + nc, "DC=DomainDnsZones," + nc, "DC=ForestDnsZones," + nc]
why = []
for name, port in (("dc01", sys.argv[2]), ("dc02", sys.argv[3])):
    try:
        c = ldap3.Connection(ldap3.Server("127.0.0.1", port=int(port), use_ssl=True, tls=ldap3.Tls(validate=ssl.CERT_NONE)),
                             user="svc_aap_adreader@corp.example.mil", password="Read3r-Test-Pass", auto_bind=True)
    except Exception as e:
        why.append("%s: no bind (%s)" % (name, e))
        continue
    for p in ncs:
        c.search(p, "(objectClass=*)", ldap3.BASE, attributes=["repsFrom"])
        reps = [e for e in c.response if e.get("type") == "searchResEntry"]
        links = [parse_reps_from(v) for v in (reps[0]["raw_attributes"].get("repsFrom") or [])] if reps else []
        if not links:
            why.append("%s: no partner in repsFrom of %s" % (name, p))
        elif any(x["failures"] or not x["last_success"] for x in links):
            why.append("%s: a failing link in %s" % (name, p))
r = dns.resolver.Resolver(configure=False)
r.nameservers, r.port = ["127.0.0.1"], int(sys.argv[4])
for rec in ("_ldap._tcp.dc._msdcs", "_kerberos._tcp.dc._msdcs", "_ldap._tcp", "_kerberos._tcp", "_ldap._tcp.gc._msdcs"):
    try:
        got = set(str(a.target).rstrip(".") for a in r.resolve(rec + ".corp.example.mil", "SRV"))
    except Exception as e:
        got = set()
    missing = {"dc01.corp.example.mil", "dc02.corp.example.mil"} - got
    if missing:
        why.append("%s: no record for %s" % (rec, ", ".join(sorted(missing))))
print("; ".join(why))
PY
}
for i in $(seq 1 20); do
    settle
    not_yet="$(settled)"
    [ -z "$not_yet" ] && break
    sleep 3
done
[ -n "$not_yet" ] && { echo "the test domain did not settle: $not_yet"; exit 1; }
echo "the domain settled (round $i): both DCs replicate from each other, DNS has both"
# lockme locked out: five wrong passwords (the threshold)
"$py" - "$p_ldaps1" <<'PY'
import ssl, sys, ldap3
for _ in range(5):
    c = ldap3.Connection(ldap3.Server("127.0.0.1", port=int(sys.argv[1]), use_ssl=True, tls=ldap3.Tls(validate=ssl.CERT_NONE)),
                         user="lockme@corp.example.mil", password="wrong")
    c.bind()
PY

# ---- the job -------------------------------------------------------------------------------------------------------------
cat > "$work/inv.yml" <<INV
all:
  children:
    win_dc:
      hosts:
        dc01.corp.example.mil:
        dc02.corp.example.mil:
      vars:
        # WinRM to a closed port: as a DC that refuses the account - auto mode turns to reader
        ansible_connection: winrm
        ansible_host: 127.0.0.1
        ansible_port: $p_closed
        ansible_winrm_scheme: http
        ansible_winrm_transport: basic
        ansible_winrm_connection_timeout: 3
INV
cat > "$work/vars.yml" <<VARS
ad_reader_dns_servers: [127.0.0.1]
ad_reader_ports: {dns: $p_dns}
ad_reader_ca_file: $work/certs/ca.crt
ad_reader_timeout: 3
ad_reader_endpoints:
  dc01.corp.example.mil: {address: 127.0.0.1, ldap: $p_ldap1, ldaps: $p_ldaps1, kerberos: $p_krb1}
  dc02.corp.example.mil: {address: 127.0.0.1, ldap: $p_ldap2, ldaps: $p_ldaps2, kerberos: $p_krb2}
  dc09.corp.example.mil: {address: 127.0.0.1, ldap: $p_closed, ldaps: $p_closed, kerberos: $p_closed}
ad_cert_warn_days: 2
ad_cert_crit_days: 1
ad_reader_history: false
VARS
printf '%s\n' "$pass_reader" > "$work/pw"
printf 'not-the-password\n' > "$work/pw-wrong"

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output must contain each TEXT (!TEXT: must not)
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        ANSIBLE_SHOW_CUSTOM_STATS=True "$ap" -i "${INV:-$work/inv.yml}" playbooks/ad_health_report.yml -e "@$work/vars.yml" -e "$vars" \
        -u svc_aap_adreader@corp.example.mil --connection-password-file "${PW:-$work/pw}" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    runs=$((runs + 1))
    [ -n "${AD_TEST_OUT:-}" ] && mkdir -p "$AD_TEST_OUT" && cp "$work/out.txt" "$AD_TEST_OUT/run$runs.txt"
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            ! grep -qF -- "${t:1}" "$work/out.txt" || { echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $name: lacks: $t"; grep -F 'Active Directory health report:' "$work/out.txt" | head -2; fails=$((fails + 1)); return 1; }
        fi
    done
    echo "ok   - $name"
}
if command -v kinit > /dev/null; then krb='  TGT  '; else krb='  answers  '; fi

run "reader: a healthy domain, read as a plain domain user" ok '{"ad_health_mode": "reader"}' \
    'Active Directory health report: ' '!NOT READ' \
    'Reader mode (ad_health_mode: reader): read from the AAP side over LDAP (simple bind over LDAPS)' \
    'Not run - needs admin on the DCs (reader mode): dcdiag, repadmin' 'svc_aap_adreader@corp.example.mil - a plain domain user' \
    'OK (LDAPS)' "$krb" 'PDC, RID, Infra, Schema, Naming' '2 of 2' 'here: repsFrom' 'link(s) are healthy' \
    '_ldap._tcp.gc._msdcs.corp.example.mil      OK' 'CN=dc02.corp.example.mil' 'Test Issuing CA' \
    'Administrator, leaver' '!UNREACHABLE' 'lockme' \
    "the account may not read the members' replication metadata" \
    'when members were added or removed (msDS-ReplValueMetaData of member): not read (needs admin)' \
    'accounts deleted (the Deleted Objects container): not read (needs admin)'
run "auto: no DC answers over WinRM with the credential -> reader mode" ok '{}' \
    'reader mode - ad_health_mode auto: no DC could be reached over WinRM with this credential' \
    'Reader mode (ad_health_mode auto: no DC could be reached over WinRM' 'OK (LDAPS)' '2 of 2' '!admin mode (WinRM on each DC)'
run "admin: as before - the DCs not reached are red, no reader" ok '{"ad_health_mode": "admin"}' \
    'admin mode (WinRM on each DC)' 'UNREACHABLE' '!Reader mode'
run "the certificate: DC02's expires in 5 days - red with 30 / 7 days" ok '{"ad_health_mode": "reader", "ad_cert_warn_days": 30, "ad_cert_crit_days": 7}' \
    ' critical, ' 'the LDAPS certificate of DC02 expires in 4 day(s)'
run "an untrusted certificate: no password sent, nothing read, red (the job stays green)" ok '{"ad_health_mode": "reader", "ad_reader_ca_file": ""}' \
    'Active Directory health report: NOT READ (corp.example.mil)' 'certificate verify failed' '!OK (LDAPS)'
PW="$work/pw-wrong" run "a wrong password: red, the reason AD gave" ok '{"ad_health_mode": "reader"}' \
    'NOT READ' 'the bind was refused - invalidCredentials'
PW="$work/pw-wrong" run "  ...and ad_report_fail fails the job" fail '{"ad_health_mode": "reader", "ad_report_fail": true}' \
    'ad_report_fail: false makes this a report only'
cp "$work/inv.yml" "$work/inv9.yml"
printf '        dc09.corp.example.mil:\n' > "$work/dc09"
sed -i "/        dc02.corp.example.mil:/r $work/dc09" "$work/inv9.yml"
INV="$work/inv9.yml" run "a DC in the inventory that AD does not have: amber, the rest read" ok '{"ad_health_mode": "reader"}' \
    "dc09.corp.example.mil is in the inventory's DCs but is not a domain controller in AD" 'OK (LDAPS)' '2 of 2'

# the classification banner (1.3.0): the report runs on the AAP side, the DCs still say the enclave
mkdir -p "$work/web"
run "the banner: the DCs' domain is a classified one - the report (on the website) has the banner" ok \
    '{"ad_health_mode": "reader", "site_enclave_classified_domains": ["corp.example.mil"], "report_web_dest": "'"$work/web"'"}' \
    'Banner SECRET' 'OK (LDAPS)'
grep -q "SECRET" "$work/web/ad_health_report.html" "$work/web"/*.html 2>/dev/null || { echo "FAIL - the banner is not on the website's page"; fails=$((fails + 1)); }

# short inventory names (dc01): the domain learned - from the DCs' rootDSE (no bind), or an FQDN ansible_host
sed -e 's/^        dc01.corp.example.mil:$/        dc01:/' -e 's/^        dc02.corp.example.mil:$/        dc02:/' "$work/inv.yml" > "$work/inv-short.yml"
INV="$work/inv-short.yml" run "short inventory names: the domain from the DCs' rootDSE, read without a bind" ok '{"ad_health_mode": "reader"}' \
    'The domain corp.example.mil: from the rootDSE of dc01 (read without a bind).' 'OK (LDAPS)' '2 of 2' '!NOT READ' \
    "!is in the inventory's DCs but is not a domain controller in AD"
sed -e 's/^        dc01:$/        dc01: {ansible_host: dc01.corp.example.mil}/' "$work/inv-short.yml" > "$work/inv-short-fqdn.yml"
INV="$work/inv-short-fqdn.yml" run "  ...or from an ansible_host that is an FQDN" ok '{"ad_health_mode": "reader"}' \
    "The domain corp.example.mil: from the inventory's ansible_host (dc01.corp.example.mil)." 'OK (LDAPS)' '2 of 2' '!NOT READ'

# the privileged groups since the last run: the last job's artifact in AAP (a fake AAP)
dn() { echo "CN=$1,CN=Users,$nc"; }
cat > "$work/jobs.json" <<JOBS
[{"id": 10, "job_template": 7, "name": "AD health", "status": "successful", "job_type": "run", "finished": "2026-11-01T06:00:00Z",
  "artifacts": {"ad_privileged_members": {"domain": "corp.example.mil", "read_at": "2026-11-01T06:00:00Z",
                "groups": {"Domain Admins": ["$(dn Administrator)", "$(dn olduser)"]}}}}]
JOBS
"$py" "$repo/tests/email/fake_aap.py" "$p_aap" "$work/jobs.json" 2>"$work/aap.err" &
aap_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p_aap") 2>/dev/null && break; sleep 0.1; done
aap='{"ad_health_mode": "reader", "ad_reader_history": true, "awx_job_id": 11, "awx_job_template_id": 7}'
CONTROLLER_HOST="http://127.0.0.1:$p_aap" CONTROLLER_USERNAME=audit CONTROLLER_PASSWORD=pw \
    run "since the last run, from the last job's artifact in AAP: joined, left - and this run's members kept" ok "$aap" \
    'Privileged groups since the last run' 'job 10' 'Domain Admins  JOINED  leaver' 'Domain Admins  LEFT    olduser' 'leaver joined Domain Admins since the last run' \
    '"ad_privileged_members": {'
run "  ...no AAP credential: one line, no change" ok "$aap" \
    "no earlier list: the first run, or the last run's artifact couldn't be read (the AAP credential?)" '!JOINED' '!LEFT' \
    '!joined Domain Admins since the last run'

# ... and from ad_reader_history_file (written after the run, never in Check mode)
hist="$work/hist/ad-privileged-groups.json"
hf='{"ad_health_mode": "reader", "ad_reader_history": true, "ad_reader_history_file": "'"$hist"'"}'
CHECK=1 run "the history file: a dry run (Check) writes nothing" ok "$hf" "no earlier list: the first run, or $hist could not be read"
if [ -e "$hist" ]; then echo "FAIL - the history file was written in Check mode"; fails=$((fails + 1)); fi
run "  ...a run writes it" ok "$hf" "no earlier list: the first run, or $hist could not be read"
[ -s "$hist" ] || { echo "FAIL - the history file was not written"; fails=$((fails + 1)); }
on1 samba-tool group addmembers "Domain Admins" jdoe > /dev/null
run "  ...the next run: who joined since (the file)" ok "$hf" "the file $hist" 'Domain Admins  JOINED  jdoe' '!LEFT'

# the accounts: gone disabled now (its creation outside the window: the replication metadata tells them apart)
wait=$((made_at + 65 - $(date +%s)))
[ "$wait" -gt 0 ] && sleep "$wait"
on1 samba-tool user disable gone > /dev/null
run "the accounts: disabled (replication metadata, on DC01), locked out now" ok '{"ad_health_mode": "reader", "ad_report_hours": 0.015}' \
    'gone    DISABLED    DC01     replication metadata' 'Accounts locked out now (1)' 'lockme'

# replication failing: DC02 stopped, DC01 tries again
R stop -t 2 "$dc2" > /dev/null
on1 samba-tool drs replicate dc01 dc02 "$nc" -U Administrator --password="$pass_admin" > /dev/null 2>&1
run "replication failing, DC02 stopped: red, the failing link from DC01's own repsFrom" ok '{"ad_health_mode": "reader", "ad_reader_kerberos": "probe"}' \
    'critical' 'LDAP does not answer on DC02' 'consecutive failure(s)' 'DC01  corp.example.mil'

if [ "$fails" -gt 0 ]; then echo "$fails test(s) failed"; exit 1; fi
echo "all AD reader mode tests passed"
