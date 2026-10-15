#!/usr/bin/env bash
# The Active Directory health report's Ansible side (roles/ad_report) with the collectors' answers
# from tests/ad/fixtures: the PDC emulator reads the domain, a DC that cannot be reached is red and
# does not stop the job, the report is printed and emailed (fake mail relay), ad_report_fail makes a
# critical finding fail the job, and a healthy domain is green.
#   bash tests/ad/run_ad_test.sh            (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
work="$(mktemp -d)"
pid=""
cleanup() { [ -n "$pid" ] && kill "$pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
port="${SMTP_TEST_PORT:-18026}"
fails=0
cat > "$work/inv.yml" <<INV
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    dcs:
      hosts:
        dc02.corp.test: {fixture: dc_broken}
        dc01.corp.test: {fixture: dc_ok}
        dc03.corp.test: {fixture: unreachable}
      vars: {ansible_connection: local, ansible_python_interpreter: $py}
INV
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$port" "$work/mail" --no-starttls 2>"$work/relay.err" &
pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done

# run NAME EXPECT(ok|fail) EXTRA_VARS_JSON [TEXT...]: the output (and the mail) must contain each TEXT
run() {
    local name="$1" want="$2" vars="$3"; shift 3
    (cd "$repo" && ANSIBLE_NOCOLOR=1 ANSIBLE_ROLES_PATH=roles ANSIBLE_FILTER_PLUGINS=plugins/filter ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i "$work/inv.yml" "$here/report.yml" -e '{"fixture_domain": "domain_changes"}' -e "$vars" ${CHECK:+--check} ${LIMIT:+-l "$LIMIT"}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|FAILED' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "$@"; do
        grep -qF -- "$t" "$work/out.txt" "$work"/mail/*.eml 2>/dev/null || { echo "FAIL - $name: lacks: $t"; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $name"
}
mail='{"report_email_to": ["ad-team@example.mil"], "report_email_smtp_host": "localhost", "report_email_smtp_port": '"$port"', "report_email_from": "aap@example.mil", "report_email_security": "none"}'

run "the PDC emulator reads the domain, not the first DC" ok '{}' \
    'corp.test: read by dc01.corp.test'
run "a broken domain: red, the unreachable DC listed, the job stays green" ok '{}' \
    'Active Directory health report: ' 'critical, ' 'UNREACHABLE' 'ntlm: the specified credentials were rejected' \
    'DC02  corp.test' '8453 (replication access was denied)' 'No email: report_email_to is not set'
run "emailed, HTML and plain text" ok "$mail" \
    '[AAP] Active Directory health report: ' 'Content-Type: text/html' 'X-Envelope-To: ad-team@example.mil' \
    'emailed \"[AAP] Active Directory health report:'
run "ad_report_fail: a critical finding fails the job" fail '{"ad_report_fail": true}' \
    'ad_report_fail: false makes this a report only'
run "a dry run (Check) shows the same report" ok '{}' 'Inbound replication'
CHECK=1 run "  ...in check mode" ok '{}' 'Inbound replication'
LIMIT=localhost,dc01.corp.test run "a healthy domain is green, also with ad_report_fail" ok '{"ad_report_fail": true, "fixture_domain": "domain_ok"}' \
    'Active Directory health report: all 1 domain controller(s) healthy (corp.test)'
if [ "$fails" -gt 0 ]; then echo "$fails test(s) failed"; exit 1; fi
echo "all AD report tests passed"
