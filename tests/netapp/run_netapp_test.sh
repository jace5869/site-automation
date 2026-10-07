#!/usr/bin/env bash
# playbooks/ontap_health_report.yml against a fake ONTAP REST API (tests/netapp/fake_ontap.py, data
# from tests/netapp/fixture.py): every section with a problem in it, an older-ONTAP field fallback
# (reduced detail), a cluster whose login is refused, one that cannot be reached, the email (colours),
# the artifacts, GET only (the fake logs every request), no credential, no clusters, a dry run,
# ontap_report_fail.
#   bash tests/netapp/run_netapp_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
port="${ONTAP_TEST_PORT:-18443}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
"$py" "$here/fixture.py" "$work/c1.json" svc_ro readonly-test
"$py" "$here/fixture.py" "$work/c2.json" someone_else other-password
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/fake_ontap.py" "$port" "$work/c1.json" "$work/c1.log" & pids+=($!)
"$py" "$here/fake_ontap.py" $((port + 1)) "$work/c2.json" "$work/c2.log" & pids+=($!)
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" $((port + 3)) "$work/mail" 2>/dev/null & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 3))
clusters="{\"ontap_clusters\": [\"http://127.0.0.1:$port\", \"http://127.0.0.1:$((port + 1))\", \"http://127.0.0.1:$((port + 2))\"]}"
mail="{\"report_email_to\": \"storage@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $((port + 3)), \"report_email_security\": \"none\"}"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ONTAP_USERNAME="${ONTAP_USERNAME-svc_ro}" ONTAP_PASSWORD="${ONTAP_PASSWORD-readonly-test}" ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "$work/inv.yml" playbooks/ontap_health_report.yml "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $tname"
}

run "the report: every kind of problem found, coloured, three clusters (one refused, one unreachable), green" ok -e "$clusters" -e "$mail" \
    -- "NetApp ONTAP health report: " "on 3 cluster(s)" "UNREACHABLE" "the ONTAP account was refused (HTTP 401)" \
       "cluster1-02  DOWN" "storage failover not possible" "DualPathToDiskShelf_Alert" "callhome.battery.low  2" \
       "controller cluster1-01" "psu PSU2" "shelf 1.1" "1.0.3" "lif_data1" "not on its home port" "e0c" "LINK DISCONNECTED" \
       "12000" "aggr1_n1" "92%" "Aggregates not on their home node (1)" "vol_full" "vol_off" "95%" "svm1 / vol_ok" "Transfer failed." \
       "transferring" "1d 06h 00m" "dr_cluster" "svm_stopped" "a DR destination: stopped by design" "CIFS2" "dc2 (unavailable)" \
       "lun_unmapped" "lun_off" "cluster1.example.mil" "Clock vs AAP" "reduced detail - ONTAP refused a field" '"ontap_health"' "emailed"
eml="$(ls "$work/mail"/*.eml 2>/dev/null | head -1)"
html="$work/mail.html"
[ -n "$eml" ] && "$py" -c 'import email, email.policy, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
open(sys.argv[2], "w").write(m.get_body(preferencelist=("html",)).get_content())
print(m["Subject"])' "$eml" "$html" > "$work/subject.txt"
if [ -n "$eml" ] && grep -q "^\[AAP\] NetApp ONTAP health report: .* critical, .* warning on 3 cluster(s)" "$work/subject.txt" \
   && grep -q 'bgcolor="#b91c1c"[^>]*>DOWN<' "$html" && grep -q 'bgcolor="#15803d"[^>]*>UP<' "$html" \
   && grep -q 'bgcolor="#b91c1c"[^>]*>92%<' "$html" && grep -q 'bgcolor="#b45309"[^>]*>87%<' "$html" \
   && grep -q 'bgcolor="#b91c1c"[^>]*>UNREACHABLE<' "$html" && grep -q "Read-only: GET requests to the ONTAP REST API" "$html" \
   && grep -Eq 'bgcolor="#b91c1c"[^>]*>\+[34][0-9][0-9] s<' "$html"; then
    echo "ok   -   ...the email: red DOWN and green UP, red 92%, amber 87%, the unreachable cluster and a 400 s clock drift in red"
else
    echo "FAIL -   ...email"; cat "$work/subject.txt" 2>/dev/null; fails=$((fails + 1))
fi
if grep -qv '^GET ' "$work/c1.log" "$work/c2.log"; then
    echo "FAIL - a request that was not GET:"; grep -v '^GET ' "$work/c1.log" "$work/c2.log" | head; fails=$((fails + 1))
else
    echo "ok   -   ...nothing but GET requests reached the clusters ($(wc -l < "$work/c1.log") on cluster1)"
fi
grep -q "ignore_unknown_fields=true" "$work/c1.log" && grep -q "/api/storage/volumes?.*fields=name%2Csvm.name\|/api/storage/volumes?.*fields=name,svm.name" "$work/c1.log" \
    && echo "ok   -   ...an unknown field was retried with ignore_unknown_fields, then the fallback fields" \
    || { echo "FAIL -   ...fallback requests"; grep volumes "$work/c1.log"; fails=$((fails + 1)); }
ONTAP_USERNAME= run "no NetApp ONTAP credential: says which one to attach" fail -e "$clusters" -- "attach a credential of type"
run "no clusters listed: says what to set" fail -- "ontap_clusters is empty"
n="$(ls "$work/mail" | grep -c 'eml$')"
run "a dry run (Check) reads and reports, sends no email" ok --check -e "{\"ontap_clusters\": [\"http://127.0.0.1:$port\"]}" -e "$mail" \
    -- "NetApp ONTAP health report: " "DRY RUN: would email"
[ "$(ls "$work/mail" | grep -c 'eml$')" = "$n" ] && echo "ok   -   ...no email" || { echo "FAIL - dry run emailed"; fails=$((fails + 1)); }
run "ontap_report_fail: true marks the job failed on critical findings, for workflows" fail -e "{\"ontap_clusters\": [\"http://127.0.0.1:$port\"]}" \
    -e ontap_report_fail=true -- "critical finding(s)" "ontap_report_fail: false makes this a report only"
run "a healthy-looking threshold change: volumes from 96% are not listed" ok -e "{\"ontap_clusters\": [\"http://127.0.0.1:$port\"]}" \
    -e ontap_volume_warn_pct=96 -e ontap_volume_crit_pct=99 -- "Volumes 96% full or more"

if [ "$fails" -eq 0 ]; then echo "ok   - all NetApp scenarios passed"; else echo "$fails FAILED"; exit 1; fi
