# After the approved fapolicyd fixes (roles/fapolicyd_fix) or rules changes (roles/fapolicyd_rules): restart fapolicyd,
# reload its rules (fagenrules --load) or its trust (fapolicyd-cli --update), and check it runs and a program still
# starts - with the way back in the same go on the host: once fapolicyd runs again with a bad trust database or rules
# it can deny python, and then no further Ansible task would start.
# Not a file the host runs: apply.yml sends it inline (ansible.builtin.shell), after "set --" and the function back()
# (fapolicyd_fix_rollback, fapolicyd_rules_job_rollback) - nothing new is opened from disk to run while fapolicyd runs.
#   $1 restart | rules | update   $2 fapolicyd's state before the change   $3 the python Ansible uses on the host
#   $4 seconds fapolicyd may take
# Prints "RESULT active", "RESULT still-<state>" (it was down before and still is: the change stays) or
# "RESULT rolled-back <why> <state now>" with "BACK ok" or "BACK failed" (everything put back, or not), then
# fapolicyd's last journal lines ("JOURNAL ...").
action=$1 was=$2 py=$3 wait=${4:-30}
up() { systemctl is-active --quiet fapolicyd; }
runs() { "$py" -c 1 >/dev/null 2>&1; }
settle() { i=0; while [ "$i" -lt "$wait" ]; do up && return 0; sleep 1; i=$((i + 1)); done; return 1; }
# fapolicyd applies new rules (SIGHUP) or a trust update (its fifo) after the signal: until it says so, the old ones
# hold and a check would pass too early. 1.4 logs "Ruleset identity" for rules, every version "Updated" for the trust;
# 1.3 says nothing of rules - the wait runs out. Then a program must start twice, a few seconds apart.
applied() {
    case $action in update) mark=Updated ;; rules) mark='Ruleset identity' ;; *) return 0 ;; esac
    i=0
    while [ "$i" -lt "$wait" ]; do
        journalctl -u fapolicyd --since "$since" --no-pager -o cat 2>/dev/null | grep -q "$mark" && return 0
        sleep 1; i=$((i + 1))
    done
    return 1
}
works() { up && runs && sleep 2 && up && runs; }
since=$(date '+%Y-%m-%d %H:%M:%S')

if [ "$action" = update ]; then
    fapolicyd-cli --update >/dev/null 2>&1
elif [ "$action" = rules ]; then
    # the rules files changed (fagenrules builds compiled.rules, then tells fapolicyd), and maybe the trust
    fagenrules --load >/dev/null 2>&1
    fapolicyd-cli --update >/dev/null 2>&1
else
    # a unit that failed again and again hits systemd's start limit: restart is refused until it is reset
    [ "$was" = active ] || systemctl reset-failed fapolicyd >/dev/null 2>&1
    systemctl restart fapolicyd >/dev/null 2>&1
fi
settle && applied
sleep 1
if works; then
    echo "RESULT active"
elif [ "$was" != active ] && ! up; then
    echo "RESULT still-$(systemctl is-active fapolicyd 2>/dev/null)"
else
    # running before and not now - or running, but a program no longer starts: put everything back
    if up; then why=programs-denied; else why=$(systemctl is-active fapolicyd 2>/dev/null); fi
    if back >/dev/null 2>&1; then echo "BACK ok"; else echo "BACK failed"; fi
    # the rules as they were, from the files put back (no change when only the trust or fapolicyd.conf changed)
    command -v fagenrules >/dev/null 2>&1 && fagenrules >/dev/null 2>&1
    if [ "$was" = active ]; then
        systemctl reset-failed fapolicyd >/dev/null 2>&1
        systemctl restart fapolicyd >/dev/null 2>&1
        settle
    fi
    # never left worse than found: not running before, or programs still denied - fapolicyd stopped
    if [ "$was" != active ] || ! runs; then systemctl stop fapolicyd >/dev/null 2>&1; fi
    echo "RESULT rolled-back $why $(systemctl is-active fapolicyd 2>/dev/null)"
fi
journalctl -u fapolicyd -b --no-pager -o cat -n 20 2>/dev/null | sed 's/^/JOURNAL /'
exit 0
