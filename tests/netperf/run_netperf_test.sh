#!/usr/bin/env bash
# The network performance report (roles/net_perf, playbooks/net_perf_report.yml) on this box, as a plain user, with a
# fake ping (answers per target from $W/ping/<host>), a fake iperf3 (JSON from $W/iperf), a made-up /sys/class/net, a
# real TCP listener and a real OCSP responder (openssl ocsp, a throwaway CA). In order: all well (the first run: no
# usual yet); loss and latency warnings and criticals, a target's own thresholds; a closed TCP port; slower than the
# host's usual; interface drops since the last run, a counter reset; OCSP good, revoked, no answer, a URL only; iperf3
# between two hosts of the job, below its min_mbps; a dry run keeps nothing; the email's tables.
#   bash tests/netperf/run_netperf_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${NETPERF_TEST_PYTHON:-$(command -v python3)}"
W="$(mktemp -d)"
pids=""
cleanup() { for p in $pids; do kill "$p" 2>/dev/null; done; rm -rf "$W"; }
trap cleanup EXIT
fails=0
pc="${NETPERF_CA_PORT:-12614}" po="${NETPERF_OCSP_PORT:-12611}" pt="${NETPERF_TCP_PORT:-12612}" ps="${NETPERF_SMTP_PORT:-12613}"
mkdir -p "$W/bin" "$W/ping" "$W/sys/eth0/statistics" "$W/sys/lo/statistics" "$W/mail" "$W/pb/files"
# fake ping: the last argument is the target; its answer is $W/ping/<target> ("loss avg max mdev"), none = no answer
cat > "$W/bin/ping" <<EOF
#!/bin/bash
t="\${@: -1}"; f="$W/ping/\$t"
if [ ! -f "\$f" ]; then echo "PING \$t: 20 packets transmitted, 0 received, 100% packet loss, time 3800ms"; exit 1; fi
read loss avg max mdev < "\$f"
recv=\$(( 20 - 20 * \${loss%.*} / 100 ))
echo "--- \$t ping statistics ---"
echo "20 packets transmitted, \$recv received, \$loss% packet loss, time 3800ms"
[ "\$recv" -gt 0 ] && echo "rtt min/avg/max/mdev = 0.1/\$avg/\$max/\$mdev ms"
exit 0
EOF
cat > "$W/bin/iperf3" <<EOF
#!/bin/bash
case " \$* " in *" -s "*) exit 0 ;; esac
cat "$W/iperf"
EOF
chmod +x "$W/bin/ping" "$W/bin/iperf3"
counters() {  # counters DROPPED ERRORS: eth0's rx_dropped and tx_dropped = DROPPED, rx_errors = ERRORS; lo always busy (left out)
    for k in rx_dropped tx_dropped rx_errors tx_errors; do echo 0 > "$W/sys/eth0/statistics/$k"; echo "$RANDOM" > "$W/sys/lo/statistics/$k"; done
    echo "$1" > "$W/sys/eth0/statistics/rx_dropped"; echo "$1" > "$W/sys/eth0/statistics/tx_dropped"; echo "$2" > "$W/sys/eth0/statistics/rx_errors"; }
counters 1000 0
# a TCP listener that accepts; a port nobody listens on is the closed one
"$py" -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", int(sys.argv[1]))); s.listen(50)
while True: s.accept()[0].close()' "$pt" & pids="$pids $!"
# the OCSP responder: a CA, its responder certificate, a good and a revoked user certificate
cd "$W" || exit 1
o() { openssl "$@" >/dev/null 2>&1 || { echo "ABORT - openssl $1"; exit 1; }; }
o req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 3650 -subj "/CN=Test Root CA"
printf 'extendedKeyUsage=OCSPSigning\n' > ocsp.ext; printf 'basicConstraints=CA:FALSE\n' > leaf.ext
# the user certificate names its OCSP responder and where its issuer's certificate is, as DoD certificates do
printf 'basicConstraints=CA:FALSE\nauthorityInfoAccess=OCSP;URI:http://127.0.0.1:%s,caIssuers;URI:http://127.0.0.1:%s/ca.cer\n' "$po" "$pc" > aia.ext
for n in ocsp user gone; do
    o req -newkey rsa:2048 -nodes -keyout $n.key -out $n.csr -subj "/CN=$n.example.mil"
    o x509 -req -in $n.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out $n.pem -days 365 -extfile $(case $n in ocsp) echo ocsp.ext;; user) echo aia.ext;; *) echo leaf.ext;; esac)
done
ex=$(date -u -d '+1 year' +%y%m%d%H%M%SZ) rv=$(date -u -d '-1 day' +%y%m%d%H%M%SZ)
printf 'V\t%s\t\t%s\tunknown\t/CN=user.example.mil\nR\t%s\t%s\t%s\tunknown\t/CN=gone.example.mil\n' "$ex" "$(openssl x509 -in user.pem -noout -serial | cut -d= -f2)" \
    "$ex" "$rv" "$(openssl x509 -in gone.pem -noout -serial | cut -d= -f2)" > index.txt
openssl ocsp -index index.txt -port "$po" -rsigner ocsp.pem -rkey ocsp.key -CA ca.pem -nmin 60 -ignore_err >/dev/null 2>&1 & pids="$pids $!"
cp user.pem gone.pem ca.pem pb/files/
mkdir -p pub && openssl x509 -in ca.pem -outform DER -out pub/ca.cer
"$py" -m http.server "$pc" --bind 127.0.0.1 --directory "$W/pub" >/dev/null 2>&1 & pids="$pids $!"
"$py" "$repo/tests/email/fake_smtp.py" "$ps" "$W/mail" >/dev/null 2>&1 & pids="$pids $!"
cd "$repo" || exit 1
sleep 1
cat > "$W/inv.yml" <<EOI
all:
  hosts:
    app01: {ansible_connection: local, ansible_python_interpreter: $py}
    db01: {ansible_connection: local, ansible_python_interpreter: $py}
EOI
cat > "$W/settings.yml" <<EOF
net_perf_targets:
  - {name: Gateway, host: gw.example.mil}
  - {name: DC01, host: 127.0.0.1, port: $pt}
  - {name: Far, host: far.example.mil, rtt_warn_ms: 300, rtt_crit_ms: 600}
net_perf_sysfs: $W/sys
net_perf_dir: "$W/kept/{{ inventory_hostname }}"
net_perf_ocsp: [{name: Test OCSP, url: "http://127.0.0.1:$po", cert: $W/pb/files/user.pem, issuer: $W/pb/files/ca.pem}]
report_email_to: [ops@example.mil]
report_email_smtp_host: 127.0.0.1
report_email_smtp_port: $ps
report_email_security: none
report_email_from: aap@example.mil
EOF
answer() { echo "$2" > "$W/ping/$1"; }
well() { answer gw.example.mil "0 0.4 0.9 0.1"; answer 127.0.0.1 "0 1.2 2.0 0.3"; answer far.example.mil "0 250 270 5"; }
run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    PATH="$W/bin:$PATH" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$W/inv.yml" playbooks/net_perf_report.yml -e ansible_become=false -e "@$W/settings.yml" -e '{"awx_job_id": 4714}' "${args[@]}" > "$W/out.txt" 2>&1
    local rc=$? miss=0
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '\[(CRITICAL|WARNING)\]|HEALTHY|fatal|ERROR' "$W/out.txt" | head -10; fails=$((fails + 1)); return
    fi
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$W/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$W/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -nE '\[(CRITICAL|WARNING)\]|HEALTHY|  app01 ->|fatal|ERROR' "$W/out.txt" | cut -c1-250 | head -20; fails=$((fails + 1)); fi
}

well
run "all well, the first run: no usual yet; the target's own thresholds (Far: 250 ms is fine)" ok -- \
    "HEALTHY. Checks run: netperf" "OK  app01 -> Gateway: loss 0.0 %, avg 0.4 ms" "OK  app01 -> Far: loss 0.0 %, avg 250.0 ms" "!, usual "
sleep 1
answer gw.example.mil "5 0.5 1.0 0.1"; answer 127.0.0.1 "0 75 90 4"; answer far.example.mil "0 700 800 20"
rm -f "$W/mail"/*
run "loss 5 % (warning), 75 ms (warning), Far over its own critical; the usual from the first run" fail -- \
    "[WARNING] netperf: 5.0 % packet loss to Gateway (gw.example.mil)" "[WARNING] netperf: 75.0 ms average round trip to DC01 (127.0.0.1) (warning from 50 ms)" \
    "[CRITICAL] netperf: 700.0 ms average round trip to Far (far.example.mil) (critical from 600 ms)" "usual 0.4 ms"
m=$(ls "$W/mail"/* 2>/dev/null | head -1)
if [ -n "$m" ] && "$py" -c 'import email,sys; m=email.message_from_binary_file(open(sys.argv[1],"rb")); print("\n".join(p.get_payload(decode=True).decode() for p in m.walk() if p.get_content_type()=="text/html"))' "$m" \
   | grep -q "Latency and loss - from each host to its targets, worst first" ; then echo "ok   - the email: the latency table"; else echo "FAIL - the email"; fails=$((fails + 1)); fi
well; answer gw.example.mil "50 0.5 1.0 0.1"
run "loss 50 %: critical" fail -- "[CRITICAL] netperf: 50 % packet loss to Gateway"
rm -f "$W/ping/gw.example.mil"
run "no answer at all: critical" fail -- "[CRITICAL] netperf: Gateway (gw.example.mil) does not answer ping from this host"
well
sed -i "s/port: $pt/port: 9/" "$W/settings.yml"
run "a closed TCP port: critical" fail -- "[CRITICAL] netperf: DC01 (127.0.0.1) port 9 does not accept connections from this host"
sed -i "s/port: 9}/port: $pt}/" "$W/settings.yml"
answer 127.0.0.1 "0 9.0 11 1"
run "slower than usual: 9 ms against about 1.2 ms" fail -- "[WARNING] netperf: DC01 (127.0.0.1) is" "x slower than usual from this host: 9.0 ms, usually"
well
counters 1500 0
run "interface drops: 500 since the last run seconds ago: a warning; lo left out" fail -- "[WARNING] netperf: interface eth0:" "dropped and 0 errored packets per hour" "!interface lo"
counters 10 0
run "a counter that went down (a reboot): counted from now, no finding" ok -- "HEALTHY"
run "OCSP: the test certificate good" ok -- "HEALTHY"
run "OCSP: revoked" fail -e "{\"net_perf_ocsp\": [{\"name\": \"Test OCSP\", \"url\": \"http://127.0.0.1:$po\", \"cert\": \"$W/pb/files/gone.pem\", \"issuer\": \"$W/pb/files/ca.pem\"}]}" -- \
    "[CRITICAL] netperf: the OCSP responder Test OCSP (http://127.0.0.1:$po), from this host: the responder says the test certificate is revoked"
run "OCSP: no answer (warning by setting)" fail -e '{"net_perf_ocsp": [{"name": "Gone", "url": "http://127.0.0.1:9"}], "net_perf_ocsp_severity": "warning"}' -- \
    "[WARNING] netperf: the OCSP responder Gone (http://127.0.0.1:9), from this host: no answer"
run "OCSP: site_ocsp_certs only - the address and the issuer read from the certificate" ok -e "{\"net_perf_ocsp\": [], \"site_ocsp_certs\": [\"$W/pb/files/user.pem\"]}" -- "HEALTHY"
run "OCSP: a certificate that names no responder" fail -e "{\"net_perf_ocsp\": [{\"name\": \"Bare\", \"cert\": \"$W/pb/files/gone.pem\"}]}" -- \
    "[CRITICAL] netperf: the OCSP responder Bare" "names no OCSP address"
run "OCSP: a cert file that is not in the project - a finding that says so" fail -e '{"net_perf_ocsp": [{"name": "Lost", "cert": "files/ocsp/missing.crt"}]}' -- \
    "the OCSP responder Lost" "not found in the project: files/ocsp/missing.crt"
run "OCSP: a URL only that answers" ok -e "{\"net_perf_ocsp\": [{\"name\": \"Plain\", \"url\": \"http://127.0.0.1:$po\"}]}" -- "HEALTHY"
printf '{"start": {}, "end": {"sum_sent": {"bits_per_second": 4.2e8, "retransmits": 37}, "sum_received": {"bits_per_second": 4.1e8}}}' > "$W/iperf"
run "iperf3: db01 -> app01 at 410 Mbit/s, expected 900: a warning on db01 (the client)" fail -e '{"net_perf_iperf_pairs": [{"server": "app01", "client": "db01", "min_mbps": 900}]}' -- \
    "[WARNING] netperf: throughput to app01: 410.0 Mbit/s, below the expected 900 Mbit/s"
grep -q 'db01: .*throughput to app01' "$W/out.txt" && ! grep -q 'app01: .*throughput to app01' "$W/out.txt" && echo "ok   - ... on the client only" || { echo "FAIL - the iperf finding's host"; fails=$((fails + 1)); }
n=$(wc -l < "$W/kept/app01/history.jsonl")
run "Check mode: measured, nothing kept, iperf3 not run" ok --check -e '{"net_perf_iperf_pairs": [{"server": "app01", "client": "db01", "min_mbps": 900}]}' -- "!throughput to app01"
[ "$(wc -l < "$W/kept/app01/history.jsonl")" -eq "$n" ] && echo "ok   - Check mode kept nothing" || { echo "FAIL - Check mode kept a run"; fails=$((fails + 1)); }
[ "$(stat -c %a "$W/kept/app01")" = 700 ] && [ "$(stat -c %a "$W/kept/app01/history.jsonl")" = 600 ] && echo "ok   - the history is root-only" || { echo "FAIL - the history's modes"; fails=$((fails + 1)); }

echo
if [ $fails -eq 0 ]; then echo "all network performance scenarios passed"; else echo "$fails network performance scenario(s) FAILED"; exit 1; fi
