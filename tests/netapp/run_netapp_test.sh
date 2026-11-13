#!/usr/bin/env bash
# playbooks/ontap_health_report.yml against a fake ONTAP REST API (tests/netapp/fake_ontap.py, data
# from tests/netapp/fixture.py): every section with a problem in it, an older-ONTAP field fallback
# (reduced detail), a cluster whose login is refused, one that cannot be reached, the email (colours),
# the artifacts, GET only (the fake logs every request), no credential, no clusters, a dry run,
# ontap_report_fail, a slow request that runs out of time (the rest still read); the hardware inventory
# (playbooks/ontap_hardware_report.yml): the models, controllers, shelves and disks, its workbook, GET only, an older
# ONTAP's disks (reduced detail), ontap_hardware_fail.
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
FAKE_ONTAP_SLOW=/api/support/ems/events=4 "$py" "$here/fake_ontap.py" $((port + 5)) "$work/c1.json" "$work/c5.log" & pids+=($!)
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" $((port + 3)) "$work/mail" 2>/dev/null & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 3)); wait_port $((port + 5))
clusters="{\"ontap_clusters\": [\"http://127.0.0.1:$port\", \"http://127.0.0.1:$((port + 1))\", \"http://127.0.0.1:$((port + 2))\"]}"
mail="{\"report_email_to\": \"storage@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $((port + 3)), \"report_email_security\": \"none\"}"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...   (PB=...: another playbook than the health report)
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ONTAP_USERNAME="${ONTAP_USERNAME-svc_ro}" ONTAP_PASSWORD="${ONTAP_PASSWORD-readonly-test}" ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "$work/inv.yml" "playbooks/${PB:-ontap_health_report.yml}" "${args[@]}") > "$work/out.txt" 2>&1
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
       "2 LDAP event(s) left out - no SVM has an LDAP client configured" \
       "controller cluster1-01" "psu PSU2" "shelf 1.1" "1.0.3" "lif_data1" "not on its home port" "e0c" "LINK DISCONNECTED" \
       "12000" "aggr1_n1" "92%" "Aggregates not on their home node (1)" "vol_full" "vol_off" "95%" "svm1 / vol_ok" "Transfer failed." \
       "transferring" "1d 06h 00m" "dr_cluster" "svm_stopped" "a DR destination: stopped by design" "CIFS2" "dc2 (unavailable)" "1 OK · 1 not contacted · 1 not reachable" "3 not contacted" "none listed" \
       "CIFS domain controllers" "vserver cifs domain discovered-servers reset-servers -vserver svm3" \
       "lun_unmapped" "lun_off" "cluster1.example.mil" "Clock vs AAP" "reduced detail - ONTAP refused a field" '"ontap_health"' "emailed"
eml="$(ls "$work/mail"/*.eml 2>/dev/null | head -1)"
html="$work/mail.html"
# the html without 1.55.0's column labels and long-word spans, so a cell reads as before
[ -n "$eml" ] && "$py" -c 'import email, email.policy, re, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
h = re.sub(r"<span class=\"(?:sa|g)-lab\"[^>]*>[^<]*</span>", "", m.get_body(preferencelist=("html",)).get_content())
open(sys.argv[2], "w").write(re.sub(r"<span class=\"(?:sa|g)-brk\">([^<]*)</span>", r"\1", h))
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
run "a slow request (the event log) runs out of time: listed, the rest of the cluster still read" ok \
    -e "{\"ontap_clusters\": [\"http://127.0.0.1:$((port + 5))\"]}" -e ontap_timeout=2 \
    -- "could not read ems - no answer within 2 s, asked twice - ontap_timeout: 120 gives it longer" \
       "/127.0.0.1:$((port + 5)): ems " "s - NO ANSWER in time" "cluster1-02  DOWN" "vol_full"
grep -q "/api/storage/volumes" "$work/c5.log" && echo "ok   -   ...the requests after it were still sent" \
    || { echo "FAIL -   ...stopped at the slow request"; fails=$((fails + 1)); }

# ---- the hardware inventory -------------------------------------------------------------------------------------
: > "$work/c1.log"; : > "$work/c2.log"
n="$(ls "$work/mail" | grep -c 'eml$')"
PB=ontap_hardware_report.yml run "hardware inventory: models, controllers, shelves, disks by model; one refused, one unreachable; green" ok \
    -e "$clusters" -e "$mail" \
    -- "NetApp hardware: 2 controllers, 1 model, 1 shelf, 4 disks - 1 not up - 2 clusters not read" \
       "AFF-A400: 2 controller(s); ONTAP 9.14.1P3 (1), 9.13.1P8 (1); firmware 11.10 (1), 11.9 (1); SP / BMC BMC 13.10 (1)" \
       "cluster1: 2 controller(s), 1 shelf, 4 disk(s)" "NOT READ - http://127.0.0.1:$((port + 1)): the ONTAP account was refused (HTTP 401)" \
       "Not all read - cluster1: disks - reduced detail - ONTAP refused a field" '"ontap_hardware"' '"Controllers": 2' "emailed"
eml="$(ls -t "$work/mail"/*.eml 2>/dev/null | head -1)"
if [ "$(ls "$work/mail" | grep -c 'eml$')" -gt "$n" ] && "$py" - "$eml" "$work/hw.html" "$work/hw.xlsx" <<'PY'
import email, email.policy, sys, zipfile
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
open(sys.argv[2], "w").write(m.get_body(preferencelist=("html",)).get_content())
x = [p for p in m.iter_attachments() if (p.get_filename() or "").endswith(".xlsx")]
assert x, "no workbook attached"
open(sys.argv[3], "wb").write(x[0].get_content())
z = zipfile.ZipFile(sys.argv[3])
book = z.read("xl/workbook.xml").decode()
for sheet in ("Clusters", "Controllers", "Shelves", "Shelf modules", "Disks", "Ethernet ports", "FC ports"):
    assert 'name="%s"' % sheet in book, sheet
cells = " ".join(z.read(n).decode() for n in z.namelist() if n.startswith("xl/worksheets/"))
for text in ("SN0001", "0537000001", "Intel(R) Xeon(R) Silver 4210", "128.0 GB", "BMC", "192.0.2.51", "X1970A 1.0 TB",
             "IOM-B", "0210", "X357", "NA02", "3.8 TB", "00:a0:98:00:00:01", "50:0a:09:81:00:00:00:0e"):
    assert text in cells, text
PY
then
    grep -q "System firmware" "$work/hw.html" && grep -q "Disks by model" "$work/hw.html" && grep -q "0220 (1), 0210 (1)" "$work/hw.html" \
        && grep -q "NA02 (2), NA01 (2)" "$work/hw.html" && grep -q "2x 25G, 3x down; 2x 32G FC" "$work/hw.html" \
        && echo "ok   -   ...the email: models, controllers with their ports, shelf module and disk firmware; the workbook's seven sheets" \
        || { echo "FAIL -   ...the hardware email's tables"; fails=$((fails + 1)); }
else
    echo "FAIL -   ...the hardware email and its workbook"; fails=$((fails + 1))
fi
grep -qv '^GET ' "$work/c1.log" "$work/c2.log" && { echo "FAIL - a request that was not GET"; fails=$((fails + 1)); } \
    || echo "ok   -   ...nothing but GET requests ($(wc -l < "$work/c1.log") on cluster1)"
grep -q "/api/storage/disks?.*ignore_unknown_fields=true" "$work/c1.log" \
    && echo "ok   -   ...the disks an older ONTAP refused were asked again, then with the fallback fields" \
    || { echo "FAIL -   ...disks fallback"; fails=$((fails + 1)); }
PB=ontap_hardware_report.yml run "hardware inventory, ontap_hardware_fail: a cluster not read marks the job failed (the report still out)" fail \
    -e "$clusters" -e ontap_hardware_fail=true -- "NetApp hardware: 2 controllers" "Not read: http://127.0.0.1:$((port + 1))"
PB=ontap_hardware_report.yml run "hardware inventory, a dry run (Check): read and reported, no email" ok --check \
    -e "{\"ontap_clusters\": [\"http://127.0.0.1:$port\"]}" -e "$mail" -- "NetApp hardware: 2 controllers, 1 model, 1 shelf, 4 disks - 1 not up" "DRY RUN: would email"

if [ "$fails" -eq 0 ]; then echo "ok   - all NetApp scenarios passed"; else echo "$fails FAILED"; exit 1; fi
