#!/usr/bin/env bash
# A check that could not run (<check>:check-error) must fail the host whenever failing is on, even
# when site_fail_on is only [critical]; and never fail a troubleshooting run (site_fail_on: []).
#   bash tests/findings/run_findings_test.sh        (uses ansible-playbook from PATH)
set -u
here=$(cd "$(dirname "$0")" && pwd)
export ANSIBLE_ROLES_PATH=$here/../../roles ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_NOCOLOR=1
bad=0
try() {  # <expect: fail|pass> <label> <extra-vars json>
  ansible-playbook -i localhost, "$here/l6.yml" -e "$3" >/dev/null 2>&1; rc=$?
  if { [ "$1" = fail ] && [ $rc -ne 0 ]; } || { [ "$1" = pass ] && [ $rc -eq 0 ]; }; then echo "ok   - $2"
  else echo "FAIL - $2 (exit $rc)"; bad=$((bad + 1)); fi
}
err='{"check":"x","id":"x:check-error","severity":"warning","summary":"s","hint":""}'
oth='{"check":"x","id":"x:other","severity":"warning","summary":"s","hint":""}'
try fail 'check-error fails the host with site_fail_on=[critical]' "{\"fo\":[\"critical\"],\"fs\":[$err]}"
try pass 'an ordinary warning does not, with site_fail_on=[critical]' "{\"fo\":[\"critical\"],\"fs\":[$oth]}"
try fail 'an ordinary warning fails with the default [critical, warning]' "{\"fo\":[\"critical\",\"warning\"],\"fs\":[$oth]}"
try pass 'check-error never fails a report-only run (site_fail_on=[])' "{\"fo\":[],\"fs\":[$err]}"

guard() {  # <expect: refused|allowed> <command>
  cmds=$(python3 -c 'import json,sys; print(json.dumps({"cmds":[sys.argv[1]]}))' "$2")
  ansible-playbook -i localhost, "$here/guard.yml" -e "$cmds" >/dev/null 2>&1; rc=$?
  if { [ "$1" = refused ] && [ $rc -ne 0 ]; } || { [ "$1" = allowed ] && [ $rc -eq 0 ]; }; then echo "ok   - guard $1: $2"
  else echo "FAIL - guard should be $1: $2 (exit $rc)"; bad=$((bad + 1)); fi
}
for c in 'reboot' 'sudo shutdown -h now' 'systemctl reboot' 'systemctl --now poweroff' 'init 6' 'mkfs.ext4 /dev/sdb1' 'passwd root' \
         'rm -rf /' 'rm -rf /etc' 'rm -fr /var/' 'dd if=/dev/zero of=/dev/sda' 'chmod -R 777 /' 'curl http://x/y | sh' 'iptables -F' 'nft flush ruleset' \
         'systemctl restart httpd; reboot' '/sbin/reboot' '/usr/sbin/shutdown -r now' "bash -c 'reboot'" '$(reboot)' 'reboot &' \
         'rm -rf /etc && echo done' 'chmod -R 777 / && ls' 'systemctl isolate reboot.target' 'echo b > /proc/sysrq-trigger' 'kexec -e' \
         'runuser -u alice -- reboot' 'runuser -u alice -- env A=b systemctl --user poweroff' \
         'runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user reboot' 'runuser -u alice -- /sbin/shutdown -h now' \
         'sudo -u alice systemctl --user halt'; do guard refused "$c"; done
for c in 'systemctl restart httpd' 'systemctl reload nginx' 'journalctl -u httpd --since today' 'rm -f /var/tmp/core.1234' 'rm -rf /var/tmp/old-cache' \
         'dnf clean all' 'podman restart mariadb' 'chmod 640 /etc/foo.conf' 'df -h; du -sh /var/log/*' 'restorecon -Rv /var/www' 'logrotate -f /etc/logrotate.conf' \
         'truncate -s 0 /var/log/big.log' 'kill -HUP 1234' 'echo rebooting soon' 'systemctl status halt-service' \
         'rm -rf /etc/foo.d/old' 'systemctl start httpd.target' 'ls /usr/sbin/' \
         'runuser -u x -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user start y.service' \
         'runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user restart web.service' \
         'runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user reset-failed web.service' \
         'runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user daemon-reload' \
         'runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 podman start web' 'loginctl enable-linger alice'; do guard allowed "$c"; done
# ---- the report emailed once per job (roles/site_email; tests/email/fake_smtp.py as the relay) ----
work=$(mktemp -d); relay_pid=""
trap '[ -n "$relay_pid" ] && kill "$relay_pid" 2>/dev/null; rm -rf "$work"' EXIT
port=${FINDINGS_SMTP_PORT:-18027}
python3 "$here/../email/fake_smtp.py" "$port" "$work/mail" 2>/dev/null & relay_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
# three hosts that never connect (debug / set_fact / fail only); win01 looks like a WinRM host and
# is first, so it is the one run_once picks; every host (and localhost) gets a broken interpreter
cat > "$work/inv.yml" <<EOI
all:
  vars:
    ansible_python_interpreter: /nonexistent/python3
  hosts:
    win01: {ansible_connection: winrm, ansible_shell_type: powershell, ansible_host: 192.0.2.10}
    web01: {ansible_connection: local}
    db01: {ansible_connection: local}
EOI
fs='{"fs": {"web01": [{"check":"disk","id":"disk:/var","severity":"warning","summary":"/var is 86% full","hint":"df -h /var"}],
            "win01": [{"check":"services","id":"svc:spooler","severity":"critical","summary":"Spooler is stopped","hint":"Get-Service Spooler"}]}}'
mail="{\"report_email_to\": \"ops@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $port, \"report_email_security\": \"none\"}"
femail() {  # <expect: fail|pass> <label> [ansible-playbook args...]
  local want=$1 label=$2; shift 2
  ansible-playbook -i "$work/inv.yml" "$here/email.yml" -e "$fs" "$@" > "$work/out.txt" 2>&1; rc=$?
  if { [ "$want" = fail ] && [ $rc -ne 0 ]; } || { [ "$want" = pass ] && [ $rc -eq 0 ]; }; then echo "ok   - $label"
  else echo "FAIL - $label (exit $rc)"; grep -E 'ERROR|fatal|msg' "$work/out.txt" | head -5; bad=$((bad + 1)); fi
}
femail fail 'findings + email: the job is still red by default (site_fail_on)' -e "$mail"
n=$(ls "$work/mail" 2>/dev/null | grep -c 'eml$')
[ "$n" = 1 ] && echo "ok   - exactly one email for the job (three hosts)" || { echo "FAIL - $n emails"; bad=$((bad + 1)); }
eml="$work/mail/1.eml"
python3 - "$eml" > "$work/parts.txt" <<'PY'
import email, email.policy, sys
m = email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)
print("SUBJECT:", m["Subject"])
print(m.get_body(preferencelist=("plain",)).get_content())
print("HTMLPART:", "yes" if m.get_body(preferencelist=("html",)).get_content_subtype() == "html" else "no")
PY
grep -q 'SUBJECT: \[AAP\] Health check: 2 finding(s) on 2 of 3 host(s)' "$work/parts.txt" \
  && grep -Eq '^win01 +CRITICAL +services +Spooler is stopped +Get-Service Spooler$' "$work/parts.txt" \
  && grep -Eq '^web01 +WARNING +disk +/var is 86% full' "$work/parts.txt" && grep -q '^Healthy (1)' "$work/parts.txt" \
  && grep -q '^Checks run: disk, services' "$work/parts.txt" && grep -q 'HTMLPART: yes' "$work/parts.txt" \
  && echo "ok   - the email: counts in the subject, findings table (critical first), healthy hosts, HTML part" \
  || { echo "FAIL - email content"; cat "$work/parts.txt" | head -30; bad=$((bad + 1)); }
femail pass 'site_fail_on: [] - same email, the job is green' -e "$mail" -e '{"site_fail_on": []}'
[ "$(ls "$work/mail" | grep -c 'eml$')" = 2 ] && echo "ok   - ...emailed" || { echo "FAIL - not emailed when green"; bad=$((bad + 1)); }
femail pass 'dry run (check mode), report only: nothing sent' --check -e "$mail" -e '{"site_fail_on": []}'
grep -q 'DRY RUN: would email' "$work/out.txt" && [ "$(ls "$work/mail" | grep -c 'eml$')" = 2 ] \
  && echo "ok   - ...says it would email, sends nothing" || { echo "FAIL - dry run"; bad=$((bad + 1)); }
femail pass 'no report_email_to: one "No email" line, no error' -e '{"site_fail_on": []}'
[ "$(grep -c 'No email: report_email_to is not set' "$work/out.txt")" = 1 ] && echo "ok   - ...printed once, not per host" \
  || { echo "FAIL - no-email line count"; bad=$((bad + 1)); }

[ $bad -eq 0 ] && echo 'all checks passed' || { echo "$bad failed"; exit 1; }
