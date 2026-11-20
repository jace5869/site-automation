# 1.79.15: restore WAS - after an approved fapolicyd change that left Ansible unable to run a module: every file put
# back (back(), sent before this), the rules built from them again, and fapolicyd as it was before the change - running
# (restarted: it reads the rules and the trust again) or not running (stopped: the change may have started it).
# Not a file the host runs: apply.yml sends it inline with back() over raw - no module, no script file opened while
# fapolicyd may block them; the guard (fapolicyd_guard_arm) runs the same text later.
# Prints "STATE <fapolicyd now>"; returns 1 when anything failed or fapolicyd is not as it was.
restore() {
    rc=0
    back || rc=1
    if command -v fagenrules >/dev/null 2>&1; then fagenrules >/dev/null 2>&1 || rc=1; fi
    if [ "$1" = active ]; then
        systemctl reset-failed fapolicyd >/dev/null 2>&1
        systemctl restart fapolicyd >/dev/null 2>&1 || rc=1
    else
        systemctl stop fapolicyd >/dev/null 2>&1
    fi
    now=$(systemctl is-active fapolicyd 2>/dev/null)
    if [ "$1" = active ]; then
        [ "$now" = active ] || rc=1
    else
        [ "$now" != active ] || rc=1
    fi
    echo "STATE ${now:-unknown}"
    return $rc
}
