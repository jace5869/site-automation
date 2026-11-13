#!/bin/bash
# 1.61.0 patch age on the Windows health check, no Windows needed: playbooks/win_health_check.yml (health_checks=patching)
# runs through the local connection with PowerShell 7 (pwsh) standing in for Windows PowerShell and a stand-in Get-HotFix
# (Modules/FakeHotFix: the last update FAKE_HOTFIX_DAYS days ago). Checks the patching check's finding, the report's
# "Patch age by group" in the job output, and the website page's squares. Needs ansible-playbook, pwsh and python3.
#   bash tests/windows/patch_age_sim/run_patch_age_sim.sh          (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
ap=${ANSIBLE_PLAYBOOK:-ansible-playbook}
pwsh=$(command -v pwsh) || { echo "pwsh not found"; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/reports"
pshome=$(dirname "$(readlink -f "$pwsh")")
cat > "$work/bin/powershell" <<SHIM
#!/bin/bash
export PSModulePath="$here/Modules:$pshome/Modules:\${PSModulePath:-}"
exec "$pwsh" "\$@"
SHIM
chmod +x "$work/bin/powershell"; ln -s powershell "$work/bin/PowerShell"
# a copy of the repository: the local PowerShell connection leaves temporary files where it runs
mkdir -p "$work/repo" && (cd "$repo" && tar --exclude=.git -cf - .) | (cd "$work/repo" && tar -xf -)
cat > "$work/inv.yml" <<INV
all:
  children:
    SITEB-windows-app:
      hosts:
        fakewin:
          ansible_connection: local
          ansible_shell_type: powershell
          ansible_python_interpreter: $(command -v python3)
INV
fail=0
run() {  # run DAYS -> $out
    out=$(cd "$work/repo" && FAKE_HOTFIX_DAYS="$1" PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH=roles ANSIBLE_NOCOLOR=1 timeout 600 "$ap" -i "$work/inv.yml" \
        playbooks/win_health_check.yml -e health_checks=patching -e site_fail_on=[] -e site_syslog=false \
        -e "{\"report_web_dest\": \"$work/reports\", \"report_web_theme\": \"atlas\"}" </dev/null 2>&1)
}
expect() {  # expect LABEL TEXT...
    local label=$1; shift
    for t in "$@"; do
        if ! echo "$out" | grep -q -F -- "$t"; then echo "FAIL - $label: no '$t'"; echo "$out" | grep -E 'Patch age|fatal|ERROR' | head -8; fail=1; return; fi
    done
    echo "ok   - $label"
}
run 40
expect "40 days: late (win_check_patching_warn_days 35), the finding as before" \
    "[WARNING] patching: the last update (KB5099999) was installed 40 days ago" \
    "Patch age: 1 host(s) - 0 on time, 0 due soon, 1 late; oldest 40 days (fakewin)" "SITEB-windows-app: 1 late"
page=$(grep -rl --include='*.html' 'Patch age at a glance' "$work/reports" | grep -v -e index -e latest | head -1)
if [ -n "$page" ] && grep -q 'title="fakewin: 40 days (' "$page"; then echo "ok   - ...the website (atlas): the square, its days on hover"
else echo "FAIL - the website page ($page)"; fail=1; fi
run 61
expect "61 days: late, critical from win_check_patching_crit_days (60)" "[CRITICAL] patching: the last update" "SITEB-windows-app: 1 late"
run 30
expect "30 days: due soon (Low), no finding" "Patch age: 1 host(s) - 0 on time, 1 due soon, 0 late" "SITEB-windows-app: 1 due soon"
run ""
expect "no update found: no data (and the finding as before)" "no installed updates were found (Get-HotFix is empty)" \
    "Patch age: 1 host(s) - 0 on time, 0 due soon, 0 late, 1 no data"
[ $fail -eq 0 ] && echo "all patch age simulations passed"
exit $fail
