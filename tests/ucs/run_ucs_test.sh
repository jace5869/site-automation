#!/usr/bin/env bash
# playbooks/ucs_inventory_report.yml against a fake UCS Manager XML API (tests/ucs/fake_ucsm.py, data from
# tests/ucs/fixture.py): three domains (one read, one whose login is refused, one that cannot be reached), the report,
# the job's lines and artifacts, the email and its workbook, nothing but logins, logouts and queries sent and every
# session logged out, a session that runs out half way (logged in again, all read), a password with XML characters in
# it, no credential, no domains, ucs_inventory_fail, ucs_inventory_raw, a dry run.
#   bash tests/ucs/run_ucs_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
port="${UCS_TEST_PORT:-18543}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "$work"; }
trap cleanup EXIT
fails=0
pw='ro&"<test>'
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
"$py" "$here/fixture.py" "$work/d1.json" svc_ro "$pw"
"$py" "$here/fixture.py" "$work/d2.json" someone_else other-password
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
"$py" "$here/fake_ucsm.py" "$port" "$work/d1.json" "$work/d1.log" & pids+=($!)
"$py" "$here/fake_ucsm.py" $((port + 1)) "$work/d2.json" "$work/d2.log" & pids+=($!)
FAKE_UCSM_EXPIRE=5 "$py" "$here/fake_ucsm.py" $((port + 4)) "$work/d1.json" "$work/d4.log" & pids+=($!)
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" $((port + 3)) "$work/mail" 2>/dev/null & pids+=($!)
wait_port "$port"; wait_port $((port + 1)); wait_port $((port + 3)); wait_port $((port + 4))
domains="{\"ucs_domains\": [\"http://127.0.0.1:$port\", \"http://127.0.0.1:$((port + 1))\", \"http://127.0.0.1:$((port + 2))\"]}"
one="{\"ucs_domains\": [\"http://127.0.0.1:$port\"]}"
mail="{\"report_email_to\": \"compute@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $((port + 3)), \"report_email_security\": \"none\"}"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && UCSM_USERNAME="${UCSM_USERNAME-svc_ro}" UCSM_PASSWORD="${UCSM_PASSWORD-$pw}" ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "$work/inv.yml" playbooks/ucs_inventory_report.yml "${args[@]}") > "$work/out.txt" 2>&1
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

# only logins, logouts and queries; every session that was opened logged out
methods_ok() {
    local f="$1" bad logins logouts
    bad="$(grep -vE '^(aaaLogin|aaaLogout|configResolveClass) ' "$f")"
    logins="$(grep -c '^aaaLogin ' "$f")"; logouts="$(grep -c '^aaaLogout ' "$f")"
    if [ -n "$bad" ]; then echo "FAIL -   ...a call that is not a login, logout or query: $(echo "$bad" | head -3)"; fails=$((fails + 1)); return 1; fi
    if [ "$logouts" -lt 1 ] || [ "$logins" -lt "$logouts" ]; then
        echo "FAIL -   ...$logins login(s), $logouts logout(s) in $f"; fails=$((fails + 1)); return 1
    fi
    echo "ok   -   ...nothing but logins, logouts and queries ($(grep -c '^configResolveClass ' "$f") queries, $logins login(s), $logouts logout(s))"
}

run "the inventory: three domains (one refused, one unreachable), not operable and a critical fault in amber, green job" ok -e "$domains" -e "$mail" \
    -- "Cisco UCS: 4 servers, 3 models, 1 domain, 2 fabric interconnects - 2 not operable - 1 critical fault - 2 domains not read" \
       "UCSB-B200-M5: 2 server(s); BIOS B200M5.4.2.3b.0.0613230245 (1), B200M5.4.2.1c.0.0405220734 (1); CIMC 4.2(3d) (1), 4.2(1f) (1)" \
       "UCS-DC1: UCS Manager 4.2(3d); 2x UCS-FI-6454; 1 chassis, 4 server(s)" \
       "NOT READ - http://127.0.0.1:$((port + 1)): UCS Manager refused the login (error 551: Authentication failed)" \
       "ucs-DOMAIN\\\\user" "NOT READ - http://127.0.0.1:$((port + 2)):" "port 443" \
       "Not all read - UCS-DC1: could not read equipmentFex - error 103" '"ucs_inventory"' '"Servers": 4' "emailed"
eml="$(ls -t "$work/mail"/*.eml 2>/dev/null | head -1)"
if [ -n "$eml" ] && "$py" - "$eml" "$work/ucs.html" "$work/ucs.xlsx" <<'PY'
import email, email.policy, sys, zipfile
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
assert "Cisco UCS: 4 servers" in m["Subject"], m["Subject"]
open(sys.argv[2], "w").write(m.get_body(preferencelist=("html",)).get_content())
x = [p for p in m.iter_attachments() if (p.get_filename() or "").endswith(".xlsx")]
assert x, "no workbook attached"
open(sys.argv[3], "wb").write(x[0].get_content())
z = zipfile.ZipFile(sys.argv[3])
book = z.read("xl/workbook.xml").decode()
for sheet in ("Domains", "Fabric interconnects", "Chassis", "IO modules", "Servers", "Adapters", "CPUs", "Memory",
              "Storage controllers", "Power supplies", "Firmware", "Service profiles", "Faults"):
    assert 'name="%s"' % sheet in book, sheet
cells = " ".join(z.read(n).decode() for n in z.namelist() if n.startswith("xl/") and n.endswith(".xml"))
for text in ("FDO0000000A", "FOX0000001", "FCH0000001", "FLM0000002", "WZP0000001", "UCSB-MLOM-40G-04", "DIMM_A1",
             "Cisco 12G Modular RAID controller", "DTM0000004", "4.0(1a)", "ESX-TEMPLATE", "F0727", "192.0.2.11"):
    assert text in cells, text
PY
then
    grep -q "Fabric interconnects" "$work/ucs.html" && grep -q "Server models" "$work/ucs.html" && grep -q "Firmware in use" "$work/ucs.html" \
        && grep -q "A: UCS-IOM-2408 (4.2(3d)), B: UCS-IOM-2408 (4.2(3d))" "$work/ucs.html" && grep -q "4 (1 not operable)" "$work/ucs.html" \
        && grep -q "Power supply 4 in chassis 1 power: failed" "$work/ucs.html" && ! grep -q "F0727" "$work/ucs.html" \
        && grep -q "Read-only: XML API queries to UCS Manager" "$work/ucs.html" \
        && echo "ok   -   ...the email: FIs, chassis with IO modules, models, firmware in use, critical and major faults (not minor); the workbook's 13 sheets" \
        || { echo "FAIL -   ...the email's tables"; fails=$((fails + 1)); }
else
    echo "FAIL -   ...the email and its workbook"; fails=$((fails + 1))
fi
methods_ok "$work/d1.log"
grep -q '^aaaLogout' "$work/d2.log" && { echo "FAIL -   ...a logout after a refused login"; fails=$((fails + 1)); } \
    || echo "ok   -   ...the refused domain: one login tried, no query ($(grep -c configResolveClass "$work/d2.log") queries)"
run "a session that runs out half way: logged in again, every class read" ok -e "{\"ucs_domains\": [\"http://127.0.0.1:$((port + 4))\"]}" \
    -- "Cisco UCS: 4 servers, 3 models, 1 domain, 2 fabric interconnects" "UCS-DC1: UCS Manager 4.2(3d)"
[ "$(grep -c '^aaaLogin ' "$work/d4.log")" -ge 3 ] && echo "ok   -   ...logged in $(grep -c '^aaaLogin ' "$work/d4.log") times" \
    || { echo "FAIL -   ...no second login"; fails=$((fails + 1)); }
methods_ok "$work/d4.log"
UCSM_USERNAME= run "no Cisco UCS Manager credential: says which one to attach" fail -e "$one" -- 'attach a credential of type \"Cisco UCS Manager\"'
run "no domains listed: says what to set" fail -- "ucs_domains is empty"
run "ucs_inventory_fail: a domain not read marks the job failed (the report still out)" fail -e "$domains" -e ucs_inventory_fail=true \
    -- "Cisco UCS: 4 servers" "Not read: http://127.0.0.1:$((port + 1)), http://127.0.0.1:$((port + 2))"
run "ucs_inventory_raw: the first objects of each class as UCS Manager gave them" ok -e "$one" -e ucs_inventory_raw=true \
    -- '"computeBlade": {' '"count": 3' '"numOfCpus": "2"' '"error": "error 103: class equipmentFex is not a valid class"'
run "ucs_inventory_fault_severities: [] - no fault table" ok -e "$one" -e '{"ucs_inventory_fault_severities": []}' \
    -- "Cisco UCS: 4 servers"
grep -q "Faults (" "$work/out.txt" && { echo "FAIL -   ...a fault table"; fails=$((fails + 1)); } || echo "ok   -   ...no fault table"
n="$(ls "$work/mail" | grep -c 'eml$')"
run "a dry run (Check): read and reported, no email" ok --check -e "$one" -e "$mail" -- "Cisco UCS: 4 servers" "DRY RUN: would email"
[ "$(ls "$work/mail" | grep -c 'eml$')" = "$n" ] && echo "ok   -   ...no email" || { echo "FAIL - dry run emailed"; fails=$((fails + 1)); }

if [ "$fails" -eq 0 ]; then echo "ok   - all UCS scenarios passed"; else echo "$fails FAILED"; exit 1; fi
