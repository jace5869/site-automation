# After the approved fapolicyd fixes (roles/fapolicyd_fix): restart fapolicyd, or reload its trust (fapolicyd-cli
# --update), and check it runs and a program still starts - with the way back in the same go on the host: once
# fapolicyd runs again with a bad trust database it can deny python, and then no further Ansible task would start.
# Not a file the host runs: apply.yml sends it inline (ansible.builtin.shell), after "set --" and the function back()
# (fapolicyd_fix_rollback) - nothing new is opened from disk to run while fapolicyd runs.
#   $1 restart | update   $2 fapolicyd's state before the fixes   $3 the python Ansible uses on the host   $4 seconds
# Prints "RESULT active", "RESULT still-<state>" (it was down before and still is: the fixes stay) or
# "RESULT rolled-back <why> <state now>" with "BACK ok" or "BACK failed" (everything put back, or not), then
# fapolicyd's last journal lines ("JOURNAL ...").
action=$1 was=$2 py=$3 wait=${4:-30}
up() { systemctl is-active --quiet fapolicyd; }
runs() { "$py" -c 1 >/dev/null 2>&1; }
settle() { i=0; while [ "$i" -lt "$wait" ]; do up && return 0; sleep 1; i=$((i + 1)); done; return 1; }

if [ "$action" = update ]; then
    fapolicyd-cli --update >/dev/null 2>&1
else
    # a unit that failed again and again hits systemd's start limit: restart is refused until it is reset
    [ "$was" = active ] || systemctl reset-failed fapolicyd >/dev/null 2>&1
    systemctl restart fapolicyd >/dev/null 2>&1
fi
settle
if up && runs; then
    echo "RESULT active"
elif [ "$was" != active ] && ! up; then
    echo "RESULT still-$(systemctl is-active fapolicyd 2>/dev/null)"
else
    # running before and not now - or running, but a program no longer starts: put everything back
    if up; then why=programs-denied; else why=$(systemctl is-active fapolicyd 2>/dev/null); fi
    if back >/dev/null 2>&1; then echo "BACK ok"; else echo "BACK failed"; fi
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
