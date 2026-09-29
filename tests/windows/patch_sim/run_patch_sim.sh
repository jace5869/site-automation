#!/bin/bash
# Simulated Windows patch runs, no Windows needed: playbooks/win_patch.yml runs through the local
# connection with PowerShell 7 (pwsh) standing in for Windows PowerShell, a stand-in ConfigMgr
# client (Modules/FakeCim: updates move on as they are polled), a stand-in shutdown.exe that takes
# a fake WinRM port down and brings it back with a new boot time, and a service that comes back
# late after the restart. Needs ansible-playbook, pwsh and python3.
#   bash tests/windows/patch_sim/run_patch_sim.sh          (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
ap=${ANSIBLE_PLAYBOOK:-ansible-playbook}
pwsh=$(command -v pwsh) || { echo "pwsh not found"; exit 1; }
work=$(mktemp -d)
port=${PATCH_SIM_PORT:-15986}
cleanup() { [ -f "$work/port.pid" ] && kill "$(cat "$work/port.pid")" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
mkdir -p "$work/bin"
cat > "$work/bin/powershell" <<SHIM
#!/bin/bash
export PSModulePath="$here/Modules:\${PSModulePath:-}"
exec "$pwsh" "\$@"
SHIM
chmod +x "$work/bin/powershell"; ln -s powershell "$work/bin/PowerShell"
cp "$here/shutdown.exe" "$work/bin/shutdown.exe"; chmod +x "$work/bin/shutdown.exe"
# a copy of the repository: the local PowerShell connection leaves temporary files where it runs
mkdir -p "$work/repo" && (cd "$repo" && tar --exclude=.git -cf - .) | (cd "$work/repo" && tar -xf -)
cat > "$work/inv.yml" <<INV
all:
  children:
    win_patch_hosts:
      hosts:
        fakewin:
          ansible_connection: local
          ansible_shell_type: powershell
          ansible_python_interpreter: $(command -v python3)
          win_patch_winrm_host: 127.0.0.1
          win_patch_winrm_port: $port
INV
export FAKECCM_STATE="$work/state.json" FAKECCM_PORTPID="$work/port.pid"
export FAKECCM_PORTCMD="python3 $here/listener.py $port $work/port.pid"
fail=0
run() {  # run NAME STATEFILE EXPECT_RC [ansible args] -> sets $out
    local name=$1 state=$2 want=$3; shift 3
    cp "$here/$state" "$work/state.json"
    ( $FAKECCM_PORTCMD </dev/null >/dev/null 2>&1 & ); sleep 1
    out=$(cd "$work/repo" && PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH=roles ANSIBLE_NOCOLOR=1 timeout 600 "$ap" -i "$work/inv.yml" playbooks/win_patch.yml \
        -e win_patch_scan_wait_sec=1 -e win_patch_poll_sec=2 -e win_patch_restart_delay_sec=5 -e win_patch_restart_settle_sec=2 \
        -e win_patch_reconnect_pause_sec=2 -e win_patch_services_grace_min=1 "$@" </dev/null 2>&1)
    local rc=$?
    [ -f "$work/port.pid" ] && kill "$(cat "$work/port.pid")" 2>/dev/null; sleep 0.5
    if { [ "$want" = 0 ] && [ $rc -ne 0 ]; } || { [ "$want" != 0 ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $name: exit code $rc"; echo "$out" | tail -25; fail=1; return 1
    fi
    return 0
}
check() {  # check NAME PYTHON-EXPRESSION-ON-s   GREP-TEXT
    local name=$1 expr=$2 text=$3
    if python3 -c "import json,sys; s=json.load(open('$work/state.json')); sys.exit(0 if ($expr) else 1)" && echo "$out" | grep -q -- "$text"; then
        echo "ok   - $name"
    else
        echo "FAIL - $name ($expr / '$text')"; echo "$out" | grep -E 'Round|Restart|DRY|fatal' | head -10; fail=1
    fi
}
run "two rounds" state_two_rounds.json 0 -e automatic_restarts=true && check "automatic_restarts: install, restart, the update the restart revealed, late service" \
    "s['install_calls'] == 2 and s['restarts'] == 1 and not s['updates']" "Round 2: asked to install 2026-09 Servicing Stack Update"
run "dry run" state_two_rounds.json 0 --check && check "dry run lists and changes nothing (rescan only)" \
    "s['install_calls'] == 0 and s['restarts'] == 0 and s['scans'] == 1" "DRY RUN (Check mode): would install"
run "maintenance window" state_blocked.json 1 && check "outside a maintenance window it stops and says so" \
    "s['restarts'] == 0" "waiting for a maintenance window"
run "default: no automatic restart" state_two_rounds.json 0 && check "by default: installs, never restarts, says a restart is needed" \
    "s['restarts'] == 0 and s['install_calls'] == 1" "automatic restarts are off"
run "no client" state_noclient.json 1 && check "no ConfigMgr client: stops" "s['install_calls'] == 0" "no working ConfigMgr client"
[ $fail -eq 0 ] && echo "all patch simulations passed"
exit $fail
