#!/usr/bin/env bash
# The mail relay whitelist workflows (roles/mail_relay: playbooks/mail_relay_plan.yml -> approval ->
# mail_relay_approved.yml) against main.cf files in a temporary folder (two relays: one long mynetworks line, and a
# mynetworks line over two lines) and a fake mail relay (tests/email/fake_smtp.py): the plan and its "waiting for
# approval" email with the exact lines, the plan handed on as AAP does (set_stats), the approved job writing exactly
# the planned lines - and every other byte of main.cf as it was - several addresses and subnets at once, an address
# already listed, removing everything that was added giving back the original file byte for byte, 198.51.100.3 never
# touching 198.51.100.30 and 203.0.113.0 never 203.0.113.0/24, other settings' addresses never read as the list, the
# notes (a subnet that lets an address relay, addresses inside a subnet), a list file and an access map (style
# file), refused requests, an unreachable relay, a plan changed since, a failed configuration check and a wrong
# read-back putting the file back, a dry run, and the approved job refusing a plan it did not get from the plan job;
# the addresses that sent no mail (playbooks/mail_relay_unused.yml: mail logs with a gzipped rotated file, how far
# back they go, connections without mail, the plan, approved and removed, logs too short, kept, no logs).
# No Postfix service here: mail_relay_check_command is "true", no reload; the read-back uses postconf when the
# machine has it (tests/mail_relay/README or the real-Postfix run in CI checks it against a running Postfix).
#   bash tests/mail_relay/run_mail_relay_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
pc="$(command -v postconf || true)"
work="$(mktemp -d)"
smtp="${SMTP_TEST_PORT:-18029}"
spid=""
fails=0
cleanup() { [ -n "$spid" ] && kill "$spid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
mkdir -p "$work/r1" "$work/r2" "$work/r3" "$work/r4" "$work/orig" "$work/mail" "$work/text"
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    mail_relays:
      hosts:
        relay1: {ansible_connection: local, ansible_python_interpreter: $py, mail_relay_file: $work/r1/main.cf}
        relay2: {ansible_connection: local, ansible_python_interpreter: $py, mail_relay_file: $work/r2/main.cf}
    relays_list:
      hosts:
        relay3: {ansible_connection: local, ansible_python_interpreter: $py, mail_relay_file: $work/r3/mynetworks, mail_relay_style: file}
    relays_map:
      hosts:
        relay3: {}
        relay4: {ansible_connection: local, ansible_python_interpreter: $py, mail_relay_file: $work/r4/relay_access, mail_relay_style: file,
                 mail_relay_entry_suffix: ' OK', mail_relay_postmap: true}
    relays_fmt:
      hosts:
        relay1: {}
        relay2: {}
    relays_one_down:
      hosts:
        relay1: {}
        relay9: {ansible_connection: ssh, ansible_host: 127.0.0.1, ansible_port: 9, ansible_ssh_common_args: '-o ConnectTimeout=2'}
INV
# main.cf as sites keep it: other settings with addresses, one long mynetworks line (relay1, no line break at the
# end of the file) or one over two lines (relay2), mynetworks_style after it
printf '%s\n' '# Postfix main.cf - relay1' 'myhostname = relay1.example.mil' 'relayhost = [192.0.2.25]' \
    'inet_interfaces = 192.0.2.5, 127.0.0.1' 'mynetworks = 127.0.0.0/8, 192.0.2.10, 192.0.2.11' 'mynetworks_style = host' \
    'smtpd_relay_restrictions = permit_mynetworks,' '    reject_unauth_destination' | head -c -1 > "$work/orig/r1"
printf '%s\n' 'mynetworks = 127.0.0.0/8,' '    192.0.2.10' 'mynetworks_style = host' > "$work/orig/r2"
printf '# Printers\n192.0.2.10\n192.0.2.11\n' > "$work/orig/r3"
printf '192.0.2.10 OK\n' > "$work/orig/r4"
reset() {
    cp "$work/orig/r1" "$work/r1/main.cf"; cp "$work/orig/r2" "$work/r2/main.cf"
    cp "$work/orig/r3" "$work/r3/mynetworks"; cp "$work/orig/r4" "$work/r4/relay_access"
    rm -f "$work"/r?/*~
}
"$py" "$repo/tests/email/fake_smtp.py" "$smtp" "$work/mail" 2>"$work/relay.err" &
spid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp") 2>/dev/null && break; sleep 0.1; done

BASE="{\"mail_relay_check_command\": \"true\", \"mail_relay_reload_service\": \"\", \"mail_relay_become\": false,
       \"mail_relay_postconf\": \"$pc\",
       \"report_email_to\": [\"approvers@example.mil\"], \"report_email_from\": \"aap@example.mil\",
       \"report_email_smtp_host\": \"127.0.0.1\", \"report_email_smtp_port\": $smtp, \"report_email_security\": \"none\",
       \"awx_user_name\": \"jdoe\"}"

# run NAME EXPECT(ok|fail) PLAYBOOK EXTRA_VARS_JSON [TEXT...]: the output and the emails of this run (decoded) must
# contain each TEXT; a TEXT starting with ! must NOT be there.
run() {
    local name="$1" want="$2" pb="$3" vars="$4"; shift 4
    rm -f "$work"/mail/*.eml "$work"/text/*.txt
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 ANSIBLE_HOST_KEY_CHECKING=False \
        "$ap" -i "${INV:-$work/inv.yml}" "playbooks/$pb" -e "$BASE" -e "$vars" ${CHECK:+--check} ${DIFF:+--diff} ${LIMIT:+--limit "$LIMIT"}) > "$work/out.txt" 2>&1
    local rc=$?
    local f
    for f in "$work"/mail/*.eml; do
        [ -e "$f" ] || continue
        "$py" -c 'import email, email.policy, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
print("Subject: " + " ".join(str(m["subject"]).split())); print(m.get_body(preferencelist=("plain",)).get_content())' "$f" > "$work/text/$(basename "$f").txt"
    done
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            if grep -qF -- "${t:1}" "$work/out.txt" "$work"/text/*.txt 2>/dev/null; then
                echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1
            fi
        elif ! grep -qF -- "$t" "$work/out.txt" "$work"/text/*.txt 2>/dev/null; then
            echo "FAIL - $name: lacks: $t"; grep -E '"msg"|fatal' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
        fi
    done
    echo "ok   - $name"
}
# the job's set_stats (ANSIBLE_SHOW_CUSTOM_STATS), as AAP hands them to the next workflow node: KEY -> extra vars JSON
stats() {
    "$py" - "$work/out.txt" "$1" <<'PY'
import json, sys
text = open(sys.argv[1]).read()
i = text.rindex("RUN: ")
obj, _ = json.JSONDecoder().raw_decode(text[i + 5:].lstrip())
print(json.dumps({sys.argv[2]: obj[sys.argv[2]]}))
PY
}
check() {  # check NAME COMMAND...: a test on the files
    if "${@:2}"; then echo "ok   - $1"; else echo "FAIL - $1"; fails=$((fails + 1)); fi
}
same() {   # same FILE EXPECTED_TEXT: the file is exactly the text (printf %b: \n are line breaks)
    printf '%b' "$2" | cmp -s - "$1" || { echo "    $1 is:"; sed 's/^/      | /' "$1"; return 1; }
}
postfix_reads() {  # postfix_reads DIR VALUE: postconf (when this machine has it) reads DIR/main.cf's mynetworks as VALUE
    [ -z "$pc" ] || [ "$("$pc" -c "$1" -h mynetworks 2>/dev/null)" = "$2" ] || { echo "    postconf: $("$pc" -c "$1" -h mynetworks 2>&1)"; return 1; }
}
today="$(date +%F)"
c() { printf '# %-18s %s  [added %s by %s]' "$1" "$2" "$3" "$4"; }   # c IP COMMENT DATE WHO: the comment line an add writes

# ---- add one address: plan -> approval -> approved ---------------------------------------------------------------
reset
run "plan add: the approval email has the IP, the exact lines, who asked, the relays" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": " 198.51.100.3\r\n", "mail_relay_comment": "Payroll app (svc-payroll)\r\n"}' \
    'Subject: [AAP] Mail relay whitelist - waiting for approval: add 198.51.100.3 (Payroll app (svc-payroll))' \
    "$(c 198.51.100.3 'Payroll app (svc-payroll)' "$today" jdoe)" 'mynetworks = ..., 198.51.100.3' \
    'Requested by jdoe. Nothing changes until a person approves it in AAP.' 'Relays to change: 2 of 2' 'Workflow Approvals' \
    'READY FOR APPROVAL: add 198.51.100.3 (Payroll app (svc-payroll)) on relay1, relay2' '"mail_relay_plan": {' '!Note'
check "  ...the plan changed no file" cmp -s "$work/orig/r1" "$work/r1/main.cf"
plan="$(stats mail_relay_plan | sed "s/\"date\": \"$today\"/\"date\": \"2026-01-02\"/")"
run "approved add (planned on another day): exactly the planned lines, on both relays" ok mail_relay_approved.yml "$plan" \
    'Subject: [AAP] Mail relay whitelist - added 198.51.100.3 (Payroll app (svc-payroll))' '2 of 2 relay(s) changed' \
    'The lines added' "$(c 198.51.100.3 'Payroll app (svc-payroll)' 2026-01-02 jdoe)" "!by jdoe] $today"
check "  ...relay1: the comment line above mynetworks, ', 198.51.100.3' at its end, every other byte as it was" same "$work/r1/main.cf" \
    "# Postfix main.cf - relay1\nmyhostname = relay1.example.mil\nrelayhost = [192.0.2.25]\ninet_interfaces = 192.0.2.5, 127.0.0.1\n$(c 198.51.100.3 'Payroll app (svc-payroll)' 2026-01-02 jdoe)\nmynetworks = 127.0.0.0/8, 192.0.2.10, 192.0.2.11, 198.51.100.3\nmynetworks_style = host\nsmtpd_relay_restrictions = permit_mynetworks,\n    reject_unauth_destination"
check "  ...relay2: at the end of the value's second line" same "$work/r2/main.cf" \
    "$(c 198.51.100.3 'Payroll app (svc-payroll)' 2026-01-02 jdoe)\nmynetworks = 127.0.0.0/8,\n    192.0.2.10, 198.51.100.3\nmynetworks_style = host\n"
check "  ...Postfix reads them so (postconf${pc:+, here})" sh -c "$(declare -f postfix_reads); pc='$pc'; \
    postfix_reads '$work/r1' '127.0.0.0/8, 192.0.2.10, 192.0.2.11, 198.51.100.3' && postfix_reads '$work/r2' '127.0.0.0/8, 192.0.2.10, 198.51.100.3'"
check "  ...a backup was made" sh -c "ls '$work'/r1/main.cf.*~ >/dev/null"
run "approved again (the same plan): already on the list now - nothing changed, green" ok mail_relay_approved.yml "$plan" \
    'Mail relay whitelist - nothing changed: add 198.51.100.3' '198.51.100.3 already on the list now (changed since the plan) - left as it is'
check "  ...still once" test "$(grep -o '198\.51\.100\.3\b' "$work/r1/main.cf" | wc -l)" = 2
run "plan add again: already on the list - nothing to approve, red, says who added it" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.3", "mail_relay_comment": "Payroll again"}' \
    'Mail relay whitelist - nothing to approve: add 198.51.100.3 (Payroll again)' '198.51.100.3 is already on the list.' \
    '198.51.100.3 is on the list already (Payroll app (svc-payroll), added 2026-01-02 by jdoe) - left as it is.' '!"mail_relay_plan": {'

# ---- several at once, subnets, one already listed ----------------------------------------------------------------
run "plan add three (one listed): only the two new ones, the note says which one is listed" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.3, 198.51.100.30,203.0.113.0/24", "mail_relay_comment": "Scanners, room 210", "mail_relay_requested_by": "asmith"}' \
    'waiting for approval: add 198.51.100.30, 203.0.113.0/24 (Scanners, room 210)' 'Add: 198.51.100.30, 203.0.113.0/24 (256 addresses)' \
    "$(c 198.51.100.30 'Scanners, room 210' "$today" asmith)" "$(c 203.0.113.0/24 'Scanners, room 210' "$today" asmith)" \
    'mynetworks = ..., 198.51.100.30, 203.0.113.0/24' '198.51.100.3 is on the list already (Payroll app (svc-payroll)'
run "approved add the two" ok mail_relay_approved.yml "$(stats mail_relay_plan)" 'added 198.51.100.30, 203.0.113.0/24 (Scanners, room 210)'
check "  ...relay1: one comment line each, the table under the first one, the addresses at the end of the line" same "$work/r1/main.cf" \
    "# Postfix main.cf - relay1\nmyhostname = relay1.example.mil\nrelayhost = [192.0.2.25]\ninet_interfaces = 192.0.2.5, 127.0.0.1\n$(c 198.51.100.3 'Payroll app (svc-payroll)' 2026-01-02 jdoe)\n$(c 198.51.100.30 'Scanners, room 210' "$today" asmith)\n$(c 203.0.113.0/24 'Scanners, room 210' "$today" asmith)\nmynetworks = 127.0.0.0/8, 192.0.2.10, 192.0.2.11, 198.51.100.3, 198.51.100.30, 203.0.113.0/24\nmynetworks_style = host\nsmtpd_relay_restrictions = permit_mynetworks,\n    reject_unauth_destination"
run "plan add 203.0.113.9 (inside the subnet): goes on, the note says it can relay already" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.9", "mail_relay_comment": "Inside"}' \
    'waiting for approval: add 203.0.113.9 (Inside)' '203.0.113.9 can relay already: it is inside 203.0.113.0/24'
run "plan add 192.0.2.25 (relayhost's address, not on the list): other settings are not the list" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "192.0.2.25", "mail_relay_comment": "Smart host"}' 'waiting for approval: add 192.0.2.25'
run "plan add 192.0.2.0/24: the addresses already inside it, per relay (they differ)" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "192.0.2.0/24", "mail_relay_comment": "Printers subnet"}' \
    'relay1: Inside 192.0.2.0/24 and on the list already, with lines of their own (they stay): 192.0.2.10, 192.0.2.11.' \
    'relay2: Inside 192.0.2.0/24 and on the list already, with lines of their own (they stay): 192.0.2.10.'
plan="$(stats mail_relay_plan)"
run "approved with subnets turned off since the plan: refused" fail mail_relay_approved.yml \
    "$(echo "$plan" | sed 's/^{/{"mail_relay_allow_subnets": false, /')" 'is not a plan of playbooks/mail_relay_plan.yml'
run "approved with the subnet changed to one not at its start: refused" fail mail_relay_approved.yml \
    "$(echo "$plan" | sed 's#192.0.2.0/24#192.0.2.5/24#g')" 'is not a plan of playbooks/mail_relay_plan.yml'
check "  ...nothing written" sh -c "! grep -q '192.0.2.0/24\|192.0.2.5/24' '$work/r1/main.cf'"

# ---- remove -----------------------------------------------------------------------------------------------------
run "plan remove 198.51.100.3: its comment line and the address, never 198.51.100.30" ok mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "198.51.100.3"}' \
    'waiting for approval: remove 198.51.100.3' 'The lines to be removed' "$(c 198.51.100.3 'Payroll app (svc-payroll)' 2026-01-02 jdoe)" \
    '198.51.100.3 (out of the mynetworks line)' '!198.51.100.30 (out' '!Scanners'
run "approved remove 198.51.100.3" ok mail_relay_approved.yml "$(stats mail_relay_plan)" 'removed 198.51.100.3' 'The lines removed'
check "  ...gone with its comment; 198.51.100.30 and its comment stay" sh -c \
    "! grep -q 'Payroll' '$work/r1/main.cf' && grep -qxF 'mynetworks = 127.0.0.0/8, 192.0.2.10, 192.0.2.11, 198.51.100.30, 203.0.113.0/24' '$work/r1/main.cf' && grep -q '198.51.100.30 .*Scanners' '$work/r1/main.cf'"
run "plan remove 198.51.100.3 again: not on the list - nothing to approve, red" fail mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "198.51.100.3"}' '198.51.100.3 is not on the list.' 'nothing to approve: remove 198.51.100.3'
run "plan remove 203.0.113.9 (no line of its own, inside the subnet): nothing to approve, the note says it can relay" fail mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "203.0.113.9"}' '203.0.113.9 is not on the list. See the note.' \
    'has no line of its own, but it can relay: it is inside 203.0.113.0/24' '!"mail_relay_plan": {'
run "plan remove 192.0.2.25 (relayhost's address): not on the list" fail mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "192.0.2.25"}' '192.0.2.25 is not on the list.'
run "plan add the address 203.0.113.0 (not the subnet)" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.0", "mail_relay_comment": "Host zero"}' 'waiting for approval: add 203.0.113.0 (Host zero)'
run "approved add 203.0.113.0" ok mail_relay_approved.yml "$(stats mail_relay_plan)" 'added 203.0.113.0 (Host zero)'
run "plan remove 203.0.113.0: only its own; the note says the subnet still lets it relay" ok mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "203.0.113.0"}' 'waiting for approval: remove 203.0.113.0' \
    '203.0.113.0 (out of the mynetworks line)' '203.0.113.0 can STILL relay after this: it is inside 203.0.113.0/24' \
    '!203.0.113.0/24 (out' '!Scanners'
run "approved remove 203.0.113.0: the result says it too" ok mail_relay_approved.yml "$(stats mail_relay_plan)" \
    'removed 203.0.113.0' 'can STILL relay after this'
check "  ...203.0.113.0/24 and its comment stay" sh -c "grep -qxF 'mynetworks = 127.0.0.0/8, 192.0.2.10, 192.0.2.11, 198.51.100.30, 203.0.113.0/24' '$work/r1/main.cf' && ! grep -q 'Host zero' '$work/r1/main.cf'"
run "plan remove the rest that was added (two at once)" ok mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "203.0.113.0/24 198.51.100.30"}' 'waiting for approval: remove 203.0.113.0/24, 198.51.100.30' \
    'Remove: 203.0.113.0/24 (256 addresses), 198.51.100.30'
run "approved remove the two" ok mail_relay_approved.yml "$(stats mail_relay_plan)" 'removed 203.0.113.0/24, 198.51.100.30'
check "  ...relay1's main.cf is the original again, byte for byte" cmp "$work/orig/r1" "$work/r1/main.cf"
check "  ...relay2's too" cmp "$work/orig/r2" "$work/r2/main.cf"
run "plan remove everything from relay2: it would be left empty - refused" fail mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "127.0.0.0/8, 192.0.2.10"}' 'would leave mynetworks empty' \
    '127.0.0.0/8 is the relay server itself' '!"mail_relay_plan": {'

# ---- a list file and an access map (mail_relay_style: file) -------------------------------------------------------
run "file: plan add - the comment line above the address" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.3", "mail_relay_comment": "Payroll app", "mail_relay_group": "relays_list"}' \
    "# Payroll app  [added $today by jdoe]" '- 198.51.100.3'
# the workflow's Variables (mail_relay_group here) reach both jobs
run "file: approved add" ok mail_relay_approved.yml "$(stats mail_relay_plan | sed 's/^{/{"mail_relay_group": "relays_list", /')" 'added 198.51.100.3 (Payroll app)'
check "  ...the list file" same "$work/r3/mynetworks" "# Printers\n192.0.2.10\n192.0.2.11\n# Payroll app  [added $today by jdoe]\n198.51.100.3\n"
run "file: plan remove 198.51.100.3 and 192.0.2.10" ok mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "198.51.100.3, 192.0.2.10", "mail_relay_group": "relays_list"}' "# Payroll app  [added $today by jdoe]"
run "file: approved remove" ok mail_relay_approved.yml "$(stats mail_relay_plan | sed 's/^{/{"mail_relay_group": "relays_list", /')" 'removed 198.51.100.3, 192.0.2.10'
check "  ...the heading '# Printers' stays, 192.0.2.11 stays" same "$work/r3/mynetworks" "# Printers\n192.0.2.11\n"
run "an access map (hash, its host_vars) and a subnet: refused there, nothing to approve" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.0/24", "mail_relay_comment": "Map", "mail_relay_group": "relays_map"}' \
    'a subnet cannot go into this list' 'relay4' 'Use a cidr: table' '!"mail_relay_plan": {'
run "the access map and one address: its suffix" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.9", "mail_relay_comment": "Map", "mail_relay_group": "relays_map"}' \
    'waiting for approval: add 198.51.100.9 (Map)' 'relay4:' '198.51.100.9 OK'

# ---- a dry run, a failed check, a wrong read-back, plans that are not the plan job's ------------------------------
reset
run "plan add 198.51.100.4" ok mail_relay_plan.yml '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.4", "mail_relay_comment": "Dry run test"}'
plan="$(stats mail_relay_plan)"
CHECK=1 DIFF=1 run "approved in Check: shows the change, changes nothing, sends nothing" ok mail_relay_approved.yml "$plan" \
    '+mynetworks = 127.0.0.0/8, 192.0.2.10, 192.0.2.11, 198.51.100.4' 'Mail relay whitelist - dry run: add 198.51.100.4' '!Subject:'
check "  ...file unchanged" cmp "$work/orig/r1" "$work/r1/main.cf"
run "approved, the configuration check fails: the file is put back, red, the email says so" fail mail_relay_approved.yml \
    "$(echo "$plan" | sed 's/^{/{"mail_relay_check_command": "false", /')" \
    'Mail relay whitelist - FAILED: add 198.51.100.4' 'is back as it was' 'the mail service was not reloaded'
check "  ...file as it was, byte for byte" cmp "$work/orig/r1" "$work/r1/main.cf"
run "approved, Postfix would read the list otherwise (read-back): put back" fail mail_relay_approved.yml \
    "$(echo "$plan" | sed 's/^{/{"mail_relay_postconf": "echo", /')" 'Postfix reads mynetworks as' 'is back as it was'
check "  ...file as it was, byte for byte" cmp "$work/orig/r1" "$work/r1/main.cf"
run "approved on its own (no plan): refused" fail mail_relay_approved.yml '{}' 'no approved plan (mail_relay_plan) reached this job'
for t in 's/"ips": \["198.51.100.4"\]/"ips": ["0.0.0.0\/0"]/' \
         's/"ips": \["198.51.100.4"\]/"ips": "198.51.100.4"/' \
         's/"todo": \["198.51.100.4"\]/"todo": ["203.0.113.0\/24"]/' \
         's/"comment": "Dry run test"/"comment": "Dry run test\\n0.0.0.0\/0"/' \
         's/"by": "jdoe"/"by": "jdoe\\n"/' \
         's/"comment": "Dry run test"/"comment": "Dry run [added 2026-01-01 by x]"/' \
         's/"style": "main_cf"/"style": "shell"/' \
         's/"parameter": "mynetworks"/"parameter": "mynetworks = 0.0.0.0\/0\\n#"/' \
         's#"file": "[^"]*r1/main.cf"#"file": "main.cf"#'; do
    run "approved with a plan changed ($t): refused" fail mail_relay_approved.yml "$(echo "$plan" | sed "$t")" 'is not a plan of playbooks/mail_relay_plan.yml'
done
check "  ...file as it was" cmp "$work/orig/r1" "$work/r1/main.cf"

# ---- the format job: one address per line, named; then add and remove keep the layout ----------------------------
# relay1: an add's table line from before (192.0.2.11), 127.0.0.1 (localhost in /etc/hosts), 192.0.2.10 (no name)
reset
fmt_before="# Postfix main.cf - relay1\nmyhostname = relay1.example.mil\n$(c 192.0.2.11 Printer 2026-10-22 jdoe)\nmynetworks = 127.0.0.0/8, 192.0.2.11, 127.0.0.1, 192.0.2.10\nmynetworks_style = host\n"
fmt_after="# Postfix main.cf - relay1\nmyhostname = relay1.example.mil\nmynetworks =\n    127.0.0.0/8\n\n    # localhost [AAP]\n    127.0.0.1\n\n    192.0.2.10\n\n    # Printer  [added 2026-10-22 by jdoe]\n    192.0.2.11\nmynetworks_style = host\n"
printf '%b' "$fmt_before" > "$work/r1/main.cf"
cp "$work/r1/main.cf" "$work/fmt_before"
FMT='{"mail_relay_group": "relays_fmt", "mail_relay_format_dns_timeout": 2}'
CHECK=1 run "format as Check: shows the new list, writes nothing" ok mail_relay_format.yml "$FMT" \
    'Subject: [AAP] Mail relay whitelist - dry run: one address per line' 'DRY RUN (Check mode): nothing was written' 'Check mode: nothing was written' \
    'Each address looked up' '127.0.0.1   localhost' '192.0.2.10  -' \
    '# localhost [AAP]' '# Printer  [added 2026-10-22 by jdoe]' 'No VMware vCenter credential on the template'
check "  ...main.cf as it was, no copy made" bash -c "cmp -s '$work/fmt_before' '$work/r1/main.cf' && ! ls '$work'/r1/main.cf.before-format-* 2>/dev/null"
run "format: one address per line, the add's comment on its address, named from DNS" ok mail_relay_format.yml "$FMT" \
    'Subject: [AAP] Mail relay whitelist - one address per line' '2 of 2 relay(s) formatted' 'a copy of the file before:' \
    'The list on relay1' '127.0.0.1   localhost' 'matched a VM'
check "  ...relay1 one address per line, every other line as it was" same "$work/r1/main.cf" "$fmt_after"
check "  ...Postfix reads the same addresses" postfix_reads "$work/r1" "127.0.0.0/8 127.0.0.1 192.0.2.10 192.0.2.11"
check "  ...the copy before is the file as it was" bash -c "cmp -s '$work/fmt_before' $work/r1/main.cf.before-format-*"
check "  ...relay2 (its list over two lines): one address per line" same "$work/r2/main.cf" "mynetworks =\n    127.0.0.0/8\n\n    192.0.2.10\nmynetworks_style = host\n"
run "format again: nothing changes, no new copy" ok mail_relay_format.yml "$FMT" 'one address per line already' '!a copy of the file before'
check "  ...one copy only" bash -c "[ \$(ls '$work'/r1/main.cf.before-format-* | wc -l) = 1 ]"
check "  ...main.cf the same" same "$work/r1/main.cf" "$fmt_after"
cp "$work/fmt_before" "$work/r1/main.cf"
run "format, Postfix would read the list otherwise (read-back): the copy put back" fail mail_relay_format.yml \
    "$(echo "$FMT" | sed 's/^{/{"mail_relay_postconf": "echo", /')" 'is back as it was' 'FAILED: one address per line'
check "  ...main.cf as it was before that run" cmp -s "$work/fmt_before" "$work/r1/main.cf"
printf '%b' "$fmt_after" > "$work/r1/main.cf"
rm -f "$work"/r?/main.cf.before-format-*
run "plan add on the formatted list" ok mail_relay_plan.yml \
    '{"mail_relay_group": "relays_fmt", "mail_relay_action": "add", "mail_relay_ip": "192.0.2.4, 203.0.113.0/28", "mail_relay_comment": "Scanner"}' \
    "# Scanner  [added $today by jdoe]" '!mynetworks = ..., '
run "approved add: each on its line in its place, its comment above it" ok mail_relay_approved.yml "$(stats mail_relay_plan)" 'added 192.0.2.4'
check "  ...in their places" same "$work/r1/main.cf" "# Postfix main.cf - relay1\nmyhostname = relay1.example.mil\nmynetworks =\n    127.0.0.0/8\n    # Scanner  [added $today by jdoe]\n    203.0.113.0/28\n\n    # localhost [AAP]\n    127.0.0.1\n\n    # Scanner  [added $today by jdoe]\n    192.0.2.4\n\n    192.0.2.10\n\n    # Printer  [added 2026-10-22 by jdoe]\n    192.0.2.11\nmynetworks_style = host\n"
check "  ...Postfix reads them" postfix_reads "$work/r1" "127.0.0.0/8 203.0.113.0/28 127.0.0.1 192.0.2.4 192.0.2.10 192.0.2.11"
run "plan remove on the formatted list: the lines and their comments" ok mail_relay_plan.yml \
    '{"mail_relay_group": "relays_fmt", "mail_relay_action": "remove", "mail_relay_ip": "192.0.2.4, 203.0.113.0/28"}' \
    "# Scanner  [added $today by jdoe]" '192.0.2.4 (its line in mynetworks)'
run "approved remove" ok mail_relay_approved.yml "$(stats mail_relay_plan)" 'removed 192.0.2.4'
check "  ...back to the formatted file, byte for byte" same "$work/r1/main.cf" "$fmt_after"
reset

# ---- refused requests (nothing read, nothing emailed) --------------------------------------------------------------
for ip in '' ' , ' '198.51.100' '198.51.100.300' '198.051.100.3' '0.0.0.0' 'abc' '198.51.100.3;reboot' '198.51.100.0/33' '198.51.100.3, 198.51.100.300'; do
    run "IP '$ip' refused" fail mail_relay_plan.yml "{\"mail_relay_action\": \"add\", \"mail_relay_ip\": \"$ip\", \"mail_relay_comment\": \"Valid\"}" \
        "mail_relay_ip '" '!Subject:'
done
run "one bad address of several: says which" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.3, 198.51.100.300", "mail_relay_comment": "Valid"}' "'198.51.100.300': not an IPv4 address"
run "a subnet too wide: refused" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.0/23", "mail_relay_comment": "Wide"}' 'the subnet is too wide: /24 or smaller'
run "a wider subnet with mail_relay_min_prefix 22: accepted" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.0/22", "mail_relay_comment": "Wide", "mail_relay_min_prefix": 22}' \
    'Add: 198.51.100.0/22 (1024 addresses)'
run "a subnet not at its start: refused, says the right one" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.5/24", "mail_relay_comment": "Net"}' 'a subnet starts at its first address: 198.51.100.0/24'
run "a subnet with mail_relay_allow_subnets false: refused" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.0/24", "mail_relay_comment": "Net", "mail_relay_allow_subnets": false}' \
    'not a subnet (mail_relay_allow_subnets: true allows subnets)' '!Subject:'
run "198.51.100.3/32: one address" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "198.51.100.3/32", "mail_relay_comment": "Slash 32"}' 'waiting for approval: add 198.51.100.3 (Slash 32)'
run "IP_address (the older playbook's name) works too" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "IP_address": "198.51.100.77, 198.51.100.78", "mail_relay_comment": "Old name"}' \
    'waiting for approval: add 198.51.100.77, 198.51.100.78 (Old name)'
run "21 addresses: too many" fail mail_relay_plan.yml \
    "{\"mail_relay_action\": \"add\", \"mail_relay_ip\": \"$(seq -s, -f '198.51.100.%g' 1 21)\", \"mail_relay_comment\": \"Many\"}" '21 addresses: at most 20'
for cm in '' 'ab' 'line one\n203.0.113.0/24' 'has # hash' 'has [added 2026-01-01 by x]'; do
    run "comment '$cm' refused" fail mail_relay_plan.yml "{\"mail_relay_action\": \"add\", \"mail_relay_ip\": \"203.0.113.9\", \"mail_relay_comment\": \"$cm\"}" \
        'mail_relay_comment is required' '!Subject:'
done
# AAP never templates survey answers (it marks them unsafe): {{ }} arrives as it is - the same here with !unsafe
printf 'mail_relay_action: add\nmail_relay_ip: 203.0.113.9\nmail_relay_comment: !unsafe "{{ 7 * 7 }}"\n' > "$work/unsafe.yml"
run "comment '{{ 7 * 7 }}' (as AAP sends it) refused" fail mail_relay_plan.yml "@$work/unsafe.yml" 'mail_relay_comment is required' '!Subject:'
LIMIT=relay1 run "a Limit without localhost: refused before anything is read" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.9", "mail_relay_comment": "Limit"}' 'leaves out localhost' '!Subject:'
LIMIT=relay1,localhost run "a Limit with localhost: only that relay" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.9", "mail_relay_comment": "Limit"}' 'READY FOR APPROVAL: add 203.0.113.9 (Limit) on relay1' '!relay2'
# report_web_dest as all.yml sets it (not as extra vars: those win over everything)
sed "s#^    localhost: {ansible_connection: local#    localhost: {report_web_dest: /nonexistent/reports, ansible_connection: local#" "$work/inv.yml" > "$work/inv-web.yml"
INV="$work/inv-web.yml" run "a report website that cannot be written: not used (mail_relay_web false) - the plan still goes on to the approval" ok mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.9", "mail_relay_comment": "Web"}' \
    '"mail_relay_plan": {' '!/nonexistent/reports is not there'
run "no mail_relay_action: refused, says where to set it" fail mail_relay_plan.yml '{"mail_relay_ip": "203.0.113.9"}' "set it to add or remove in the workflow's Variables"
run "no relay group in the inventory: refused" fail mail_relay_plan.yml \
    '{"mail_relay_action": "remove", "mail_relay_ip": "203.0.113.9", "mail_relay_group": "no_such_group"}' 'No relay server to change'
run "a relay that cannot be reached: nothing to approve" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.9", "mail_relay_comment": "One down", "mail_relay_group": "relays_one_down"}' \
    'nothing to approve: add 203.0.113.9' 'a relay could not be read' 'relay9' '!"mail_relay_plan": {'
check "  ...nothing was written" cmp "$work/orig/r1" "$work/r1/main.cf"
run "a file that is not there: nothing to approve, says mail_relay_file" fail mail_relay_plan.yml \
    "{\"mail_relay_action\": \"add\", \"mail_relay_ip\": \"203.0.113.9\", \"mail_relay_comment\": \"No file\", \"mail_relay_file\": \"$work/nope\"}" \
    'set mail_relay_file'
run "a main.cf without the parameter: nothing to approve, says so" fail mail_relay_plan.yml \
    '{"mail_relay_action": "add", "mail_relay_ip": "203.0.113.9", "mail_relay_comment": "No line", "mail_relay_parameter": "no_such"}' \
    "no 'no_such =' line in it"

# ---- the addresses that sent nothing (playbooks/mail_relay_unused.yml -> approval -> mail_relay_approved.yml) ------------
# the mail logs as rsyslog writes them (no year; the rotated one gzipped, its file dated by its last line): relay1 -
# 192.0.2.10 sent 10 days ago, 192.0.2.11 sent 190 days ago and only connected since (a monitor); relay2 - 192.0.2.10
# sent 3 days ago; both go back 200 days
mkdir -p "$work/log1" "$work/log2"
ts() { LC_ALL=C date -d "-$1 days" '+%b %e %H:%M:%S'; }
{ echo "$(ts 200) relay1 postfix/smtpd[100]: connect from scanner.example.mil[203.0.113.50]"
  echo "$(ts 190) relay1 postfix/smtpd[101]: connect from app11.example.mil[192.0.2.11]"
  echo "$(ts 190) relay1 postfix/smtpd[101]: 4AbCd1: client=app11.example.mil[192.0.2.11]"
  echo "$(ts 150) relay1 postfix/qmgr[90]: 4AbCd1: removed"; } | gzip > "$work/log1/old.gz"
mkdir -p "$work/log1/maillog_archive/2026"                # an archive folder: its files are read too
mv "$work/log1/old.gz" "$work/log1/maillog_archive/2026/old.gz"
touch -d "-150 days" "$work/log1/maillog_archive/2026/old.gz"
{ echo "$(ts 30) relay1 postfix/smtpd[102]: connect from app10.example.mil[192.0.2.10]"
  echo "$(ts 10) relay1 postfix/smtpd[102]: 4AbCd2: client=app10.example.mil[192.0.2.10]"
  echo "$(ts 5) relay1 postfix/smtpd[103]: connect from app11.example.mil[192.0.2.11]"
  echo "$(ts 5) relay1 postfix/smtpd[103]: disconnect from app11.example.mil[192.0.2.11] ehlo=1 quit=1 commands=2"; } > "$work/log1/maillog"
{ echo "$(ts 200) relay2 systemd[1]: Started Postfix Mail Transport Agent."
  echo "$(ts 3) relay2 postfix/smtpd[200]: 5XyZ: client=unknown[192.0.2.10]"; } > "$work/log2/maillog"
sed -e "s#mail_relay_file: $work/r1/main.cf}#mail_relay_file: $work/r1/main.cf, mail_relay_unused_logs: ['$work/log1/maillog*']}#" \
    -e "s#mail_relay_file: $work/r2/main.cf}#mail_relay_file: $work/r2/main.cf, mail_relay_unused_logs: ['$work/log2/maillog*']}#" \
    "$work/inv.yml" > "$work/inv-unused.yml"
reset
INV="$work/inv-unused.yml" run "unused: 192.0.2.11 sent nothing in 183 days (connections only) - its removal waits for approval" ok \
    mail_relay_unused.yml '{}' \
    'Subject: [AAP] Mail relay whitelist - waiting for approval: unused - remove 192.0.2.11' 'How far back the logs go' \
    'Each address on the list' '192.0.2.11' 'UNUSED' '2 connection(s)' 'in use' 'kept: the relay itself' \
    'READY FOR APPROVAL: remove 192.0.2.11 on relay1' '"kind": "unused"' '!remove 192.0.2.10'
check "  ...the plan changed no file" cmp -s "$work/orig/r1" "$work/r1/main.cf"
INV="$work/inv-unused.yml" run "unused: approved - 192.0.2.11 removed from relay1" ok mail_relay_approved.yml "$(stats mail_relay_plan)" \
    'removed 192.0.2.11'
check "  ...relay1's list without it" grep -q '^mynetworks = 127.0.0.0/8, 192.0.2.10$' "$work/r1/main.cf"
INV="$work/inv-unused.yml" run "unused again: every address sent mail - nothing to approve" fail mail_relay_unused.yml '{}' \
    'Subject: [AAP] Mail relay whitelist - unused addresses: none' 'every address on the list sent mail in the last 183 days' \
    '!"mail_relay_plan": {'
reset
INV="$work/inv-unused.yml" run "unused, 400 days asked: the logs go back 200 - nothing proposed, says how far they go" fail \
    mail_relay_unused.yml '{"mail_relay_unused_days": 400}' 'unused addresses: logs too short to tell' \
    'the mail logs do not go back 400 days on every relay' 'The logs do not go back 400 days on relay1, relay2' '(200 days)' \
    '!"mail_relay_plan": {'
INV="$work/inv-unused.yml" run "unused, logs of 30 days with mail_relay_unused_accept_short_logs: proposed anyway, the note says so" ok \
    mail_relay_unused.yml "{\"mail_relay_unused_logs\": [\"$work/log1/maillog\"], \"mail_relay_unused_accept_short_logs\": true}" \
    'waiting for approval: unused - remove 192.0.2.11' 'The logs do not go back 183 days' '(30 days)'
INV="$work/inv-unused.yml" run "unused, 192.0.2.11 in mail_relay_unused_keep: nothing to approve" fail mail_relay_unused.yml \
    '{"mail_relay_unused_keep": ["192.0.2.0/28"]}' 'kept: mail_relay_unused_keep' '!"mail_relay_plan": {'
INV="$work/inv-unused.yml" run "unused, no log file and no journal: the relay not read, nothing to approve" fail mail_relay_unused.yml \
    "{\"mail_relay_unused_logs\": [\"$work/nope*\"], \"mail_relay_unused_journal\": \"false\"}" 'No mail log line was read on relay1' \
    'FAILED: a relay could not be read' '!"mail_relay_plan": {'
CHECK=1 INV="$work/inv-unused.yml" run "unused, a dry run (Check): the same plan, nothing written" ok mail_relay_unused.yml '{}' \
    'READY FOR APPROVAL: remove 192.0.2.11 on relay1'
check "  ...nothing was written" cmp -s "$work/orig/r1" "$work/r1/main.cf"

echo
if [ "$fails" -eq 0 ]; then echo "All mail relay tests passed."; else echo "$fails mail relay test(s) FAILED."; fi
[ "$fails" -eq 0 ]
