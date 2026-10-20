#!/usr/bin/env bash
# The account lockout job's Ansible side (roles/ad_lockout) with the scripts' answers from
# tests/ad/fixtures/lockout_*.json: one account and where its bad passwords came from, every account
# locked out now, the input refused before anything is read, an account in no domain, the unlock (and
# a refused one failing the job, a dry run unlocking nothing), the source computers of the inventory,
# the email.
#   bash tests/ad/run_lockout_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
port="${SMTP_TEST_PORT:-18027}"
fails=0
# inv FIND_FIXTURE UNLOCK_FIXTURE: the inventory, the DCs answering with these fixtures
inv() {
    cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    dcs:
      hosts:
        dc01.corp.test: {fixture: dc_pdc, fixture_find: $1, fixture_unlock: $2}
        dc02.corp.test: {fixture: dc_bdc, fixture_find: $1, find_override: {name: DC02, role: dc, fqdn: dc02.corp.test, users: [], locked: []}}
        dc03.corp.test: {fixture: unreachable}
      vars: {ansible_connection: local, ansible_python_interpreter: $py}
    workstations:
      hosts:
        pc123.corp.test: {fixture_source: source_pc, ansible_connection: local, ansible_python_interpreter: $py}
INV
}
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$port" "$work/mail" --no-starttls 2>"$work/relay.err" &
pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output (and the mail) must contain each TEXT; !TEXT: must not
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    rm -f "$work"/mail/*.eml
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" "$here/lockout.yml" -e '{"target": "dcs"}' -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        if [ "${t:0:1}" = "!" ]; then
            grep -qF -- "${t:1}" "$work/out.txt" && { echo "FAIL - $name: has: ${t:1}"; fails=$((fails + 1)); return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null || { echo "FAIL - $name: lacks: $t"; fails=$((fails + 1)); return 1; }
        fi
    done
    echo "ok   - $name"
}
mail='"report_email_to": ["helpdesk@example.mil"], "report_email_smtp_host": "localhost", "report_email_smtp_port": '"$port"', "report_email_from": "aap@example.mil", "report_email_security": "none"'

inv find_one unlock_ok
run "one account: locked out, from PC123, what to do, every DC" ok '{"ad_lockout_user": "CORP\\jdoe", "ad_lockout_exchange_servers": ["EXCH*"]}' \
    'corp.test: read by dc01.corp.test' 'Account lockout: CORP\\jdoe LOCKED OUT - from PC123' \
    'Most bad passwords came from PC123 (192.0.2.50)' 'kept trying after the account was locked out' \
    'EXCH01 is a mail server' 'DC01 (PDC)' 'UNREACHABLE' 'locks an account out after 5 bad passwords within 30 minutes' \
    '!was NOT unlocked'
run "emailed, HTML and plain text" ok "{\"ad_lockout_user\": \"jdoe\", $mail}" \
    '[AAP] Account lockout: CORP\jdoe LOCKED OUT - from PC123' 'Content-Type: text/html' 'X-Envelope-To: helpdesk@example.mil'
run "the unlock: on the PDC emulator, said first" ok '{"ad_lockout_user": "CORP\\jdoe", "ad_lockout_unlock": true}' \
    'Account lockout: CORP\\jdoe unlocked by this job - from PC123' 'UNLOCKED by this job on DC01' 'then UNLOCK it if it is locked out'
CHECK=1 run "a dry run unlocks nothing" ok '{"ad_lockout_user": "CORP\\jdoe", "ad_lockout_unlock": true}' \
    'Would be unlocked (dry run)' 'Account lockout: CORP\\jdoe LOCKED OUT'
run "the source computers of the inventory: what runs as the account" ok '{"ad_lockout_user": "jdoe", "ad_lockout_check_sources": true}' \
    'Looking on pc123.corp.test' 'On PC123: the service BackupAgent' 'has a DISCONNECTED session' 'drive H: mapped' \
    'WS9 is not in this job' "s inventory: check it by hand"
run "an account name refused before anything is read" fail '{"ad_lockout_user": "jdoe*"}' \
    "Nothing was read: 'jdoe*' is not an account name" '!corp.test: read by'
run "an unlock names its account" fail '{"ad_lockout_unlock": true}' 'ad_lockout_unlock unlocks only an account asked for by name'
inv find_one unlock_denied
run "a refused unlock fails the job, after the report" fail '{"ad_lockout_user": "CORP\\jdoe", "ad_lockout_unlock": true}' \
    'NOT unlocked: Insufficient access rights' 'jdoe was NOT unlocked: Insufficient access rights'
inv find_upn unlock_ok
run "an account in no domain: stops before the Security logs" fail '{"ad_lockout_user": "JDOE@corp.test, nobody, OTHER\\x, o'"'"'brien"}' \
    'No account named nobody, OTHER\\x in corp.test' '!Account lockout:'
inv find_no_ad unlock_ok
run "no DC can read its domain: says why, per DC" fail '{"ad_lockout_user": "jdoe"}' \
    'No domain controller could read its domain' 'dc01.corp.test: ActiveDirectory PowerShell module' 'dc03.corp.test: ntlm' '!Account lockout:'
inv find_all unlock_ok
run "no account given: every account locked out now, each with its source" ok '{}' \
    'Account lockout: 2 account(s) locked out now (corp.test)' 'PC123 (8), EXCH01 (2), WS9 (1)' 'CORP\\erin' \
    'fine-grained password policy Admins-PSO' 'the DCs may not audit Account Lockout'

# ---- the real playbook and the role's own tasks (collect.yml, sources.yml): each script replaced by
# one that runs it in the stand-in domain of tests/windows/run_lockout_scenario.ps1, with the settings
# the role gave it. One DC (the PDC), as a local host taken for Windows; pc123 a source computer.
if command -v pwsh >/dev/null; then
    mkdir -p "$work/roles"
    cp -r "$repo/roles/ad_lockout" "$work/roles/"
    for pair in lockout_find:find_one lockout_dc:dc_pdc lockout_unlock:unlock_ok lockout_source:source_pc; do
        s="${pair%%:*}"
        printf '#!/usr/bin/env bash\nexec pwsh -NoProfile -File "%s" -Script "%s" -Scenario %s -KeepConfig\n' \
            "$repo/tests/windows/run_lockout_scenario.ps1" "$repo/roles/ad_lockout/files/$s.ps1" "${pair##*:}" > "$work/roles/ad_lockout/files/$s.ps1"
    done
    cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    win_dc:
      hosts:
        dc01.corp.test: {ansible_connection: local, ansible_python_interpreter: $py, _site_host_is_windows: true}
    workstations:
      hosts:
        pc123.corp.test: {ansible_connection: local, ansible_python_interpreter: $py, _site_host_is_windows: true}
INV
    e2e() {
        local name="$1" want="$2" vars="$3"; shift 3
        (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH="$work/roles:roles" ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
            ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
            "$ap" -i "$work/inv.yml" playbooks/ad_lockout.yml -e "$vars" ${CHECK:+--check}) > "$work/out.txt" 2>&1
        local rc=$?
        if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
            echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
        fi
        local t
        for t in "$@"; do
            grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $name: lacks: $t"; fails=$((fails + 1)); return 1; }
        done
        echo "ok   - $name"
    }
    e2e "the playbook: the scripts run, the PDC reads, the report" ok '{"ad_lockout_user": "CORP\\jdoe"}' \
        'corp.test: read by dc01.corp.test' 'Account lockout: CORP\\jdoe LOCKED OUT - from PC123' 'DCs read: 1 of 1' \
        'Most bad passwords came from PC123 (192.0.2.50)'
    e2e "the playbook: the unlock runs on the PDC" ok '{"ad_lockout_user": "jdoe", "ad_lockout_unlock": true}' \
        'Lockout | unlock jdoe (ad_lockout_unlock)' 'Account lockout: CORP\\jdoe unlocked by this job - from PC123'
    CHECK=1 e2e "the playbook in check mode: read, nothing unlocked" ok '{"ad_lockout_user": "jdoe", "ad_lockout_unlock": true}' \
        'Would be unlocked (dry run)'
    e2e "the playbook: the source computer looked on" ok '{"ad_lockout_user": "jdoe", "ad_lockout_check_sources": true}' \
        'Looking on pc123.corp.test' 'On PC123: the service BackupAgent'
else
    echo "skip - the playbook with its own tasks (no pwsh)"
fi
if [ "$fails" -gt 0 ]; then echo "$fails test(s) failed"; exit 1; fi
echo "all account lockout tests passed"
