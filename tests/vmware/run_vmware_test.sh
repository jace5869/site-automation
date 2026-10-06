#!/usr/bin/env bash
# The VMware playbooks (playbooks/vm_*.yml, roles/vmware_vm) against vcsim, VMware's vCenter
# simulator, in a container: restart / shut down (guest and hard, VMware Tools check), the AAP
# server and protected VMs refused, snapshots without memory (create, delete one, delete all),
# notes, VLAN (port group) change of one adapter leaving the others alone, the secure boot report,
# dry runs, duplicate and unknown names, no credential.
#   bash tests/vmware/run_vmware_test.sh
# Needs: podman or docker; a python with pyVmomi and the vSphere Automation SDK (pip install pyvmomi
# vmware-vcenter; VMWARE_TEST_PYTHON=... to pick it); the vmware.vmware collection (installed into a
# temporary folder from tests/vmware/requirements.yml unless VMWARE_COLLECTIONS points at one).
# vcsim is pinned to v0.52.0: newer images send a runtime.faultToleranceState newer pyVmomi cannot
# read, older ones have no REST API (vm_info needs it).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${VMWARE_TEST_PYTHON:-$(command -v python3)}"
engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
port="${VCSIM_PORT:-18989}"
image="docker.io/vmware/vcsim:v0.52.0"
name="vcsim-site-test-$$"
work="$(mktemp -d)"
smtp_pid=""
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1; [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0

"$py" -c 'import pyVmomi, vmware.vapi' 2>/dev/null || { echo "ABORT - $py has no pyVmomi / vSphere Automation SDK (pip install pyvmomi vmware-vcenter)"; exit 1; }
cols="${VMWARE_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -q -r "$here/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install vmware.vmware"; exit 1; }
fi
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    aap:
      hosts:
        aap01.example.mil: {ansible_host: 10.0.0.5}
EOI
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv-noaap.yml"
# the AAP host under another name (only its IP matches the VM), and by name only (no ansible_host)
sed 's/aap01.example.mil: {ansible_host: 10.0.0.5}/aapnode.example.mil: {ansible_host: 10.0.0.5}/' "$work/inv.yml" > "$work/inv-ip.yml"
sed 's/aap01.example.mil: {ansible_host: 10.0.0.5}/AAP01: {}/' "$work/inv.yml" > "$work/inv-name.yml"

fresh() {  # a new simulator with the test VMs (tests/vmware/vcsim.py setup)
    "$engine" rm -f "$name" >/dev/null 2>&1
    "$engine" run -d --name "$name" -p "127.0.0.1:$port:8989" "$image" -l 0.0.0.0:8989 -api-version 8.0 -pg 2 >/dev/null \
        || { echo "ABORT - could not start $image"; exit 1; }
    VMWARE_PORT=$port "$py" "$here/vcsim.py" setup || { echo "ABORT - vcsim setup"; exit 1; }
}
state() {  # state VM FIELD-EXPRESSION (python, v = the VM's first record)
    VMWARE_PORT=$port "$py" "$here/vcsim.py" state | "$py" -c "import json,sys; d=json.load(sys.stdin); v=d['$1'][0]; print($2)"
}

# run NAME EXPECT(ok|fail) PLAYBOOK [args...] then TEXT... after '--'
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && VMWARE_HOST=127.0.0.1 VMWARE_PORT=$port VMWARE_USER=user VMWARE_PASSWORD=pass \
        ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "${INV:-$work/inv.yml}" "playbooks/$pb" -e vmware_validate_certs=false \
        -e '{"awx_job_id": 4711, "awx_user_name": "operator1"}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -6 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -6 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $tname"
}
check() {  # check NAME VM EXPR EXPECTED
    local got; got="$(state "$2" "$3")"
    if [ "$got" = "$4" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$got], expected [$4]"; fails=$((fails + 1)); fi
}

fresh
# ---- protection --------------------------------------------------------------------------------
run "AAP server refused (found by its guest host name / IP, VM name differs)" fail vm_reboot.yml -e vm_names=DC0_H0_VM0 \
    -- "Refused: DC0_H0_VM0 is the AAP server"
INV="$work/inv-ip.yml" run "AAP server refused when only its IP address matches (inventory name differs)" fail vm_reboot.yml \
    -e vm_names=DC0_H0_VM0 -- "Refused: DC0_H0_VM0 is the AAP server"
INV="$work/inv-name.yml" run "AAP server refused when only its guest host name matches (no ansible_host)" fail vm_reboot.yml \
    -e vm_names=DC0_H0_VM0 -- "Refused: DC0_H0_VM0 is the AAP server"
INV="$work/inv-ip.yml" run "...and a VM that matches neither is not refused" ok vm_reboot.yml --check -e vm_names=DC0_H0_VM1 -- "Would restart"
run "AAP server refused for hard power off too, even with the protected groups emptied" fail vm_shutdown.yml \
    -e vm_names=DC0_H0_VM0 -e vm_power_mode=hard -e '{"vmware_protected_groups": []}' -- "Refused: DC0_H0_VM0"
check "  ...and it is still on" DC0_H0_VM0 "v['power']" poweredOn
run "VLAN change on the AAP server refused" fail vm_vlan.yml -e vm_names=DC0_H0_VM0 -e vm_portgroup=DC0_DVPG1 -- "Refused: DC0_H0_VM0"
run "a VM in vmware_protected_vms refused" fail vm_reboot.yml -e vm_names=DC0_H0_VM1 -e '{"vmware_protected_vms": ["dc0_h0_vm1"]}' \
    -- "Refused: DC0_H0_VM1"
run "two VMs, one of them AAP: nothing is done to either" fail vm_reboot.yml -e '{"vm_names": ["DC0_H0_VM1", "DC0_H0_VM0"]}' \
    -- "Refused: DC0_H0_VM0"
INV="$work/inv-noaap.yml" run "inventory without the aap group and no protected list: refused" fail vm_reboot.yml -e vm_names=DC0_H0_VM1 \
    -- "cannot tell which VM is the AAP server"
INV="$work/inv-noaap.yml" run "...but with the AAP VM named in vmware_protected_vms it runs" ok vm_reboot.yml -e vm_names=DC0_H0_VM1 \
    -e '{"vmware_protected_vms": ["DC0_H0_VM0"]}' -- "Restart requested for (operating system restart through VMware Tools): DC0_H0_VM1"
run "snapshots and notes are allowed on the AAP VM (they do not stop it)" ok vm_notes.yml -e vm_names=DC0_H0_VM0 -e vm_notes=x

# ---- names, credential ---------------------------------------------------------------------------
run "unknown VM name" fail vm_reboot.yml -e vm_names=nope -- "No VM is named nope"
run "two VMs with the same name: refused" fail vm_snapshot.yml -e vm_names=dup -- "2 VMs are named dup"
run "wildcards refused" fail vm_reboot.yml -e "vm_names='DC0_*'" -- "Wildcards (* ?) are not accepted"
run "survey text, one per line" ok vm_snapshot.yml -e "{\"vm_names\": \"DC0_H0_VM1\\nDC0_C0_RP0_VM1\\n\", \"vm_snapshot_name\": \"multi\"}" \
    -- 'Took snapshot \"multi\" (no memory) of: DC0_H0_VM1, DC0_C0_RP0_VM1'
(unset VMWARE_HOST; cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
    env -u VMWARE_HOST "$ap" -i "$work/inv.yml" playbooks/vm_reboot.yml -e vm_names=x > "$work/out.txt" 2>&1)
grep -q 'attach a credential of type' "$work/out.txt" && echo "ok   - no vCenter credential: says so" \
    || { echo "FAIL - no credential message"; fails=$((fails + 1)); }
(cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
    env -u VMWARE_HOST "$ap" -i "$work/inv.yml" playbooks/vm_secure_boot_report.yml > "$work/out.txt" 2>&1)
grep -q 'attach a credential of type' "$work/out.txt" && echo "ok   - secure boot report (every VM) without a vCenter credential: says so" \
    || { echo "FAIL - report: no credential message"; grep -m2 -E 'ERROR|msg' "$work/out.txt"; fails=$((fails + 1)); }

# ---- restart / shut down -------------------------------------------------------------------------
run "dry run (check mode) restart: says what it would do" ok vm_reboot.yml --check -e vm_names=DC0_H0_VM1 -- "Would restart" '"vm_report"' '"dry_run": true' 
run "restart through VMware Tools" ok vm_reboot.yml -e vm_names=DC0_H0_VM1 -- "Restart requested for (operating system restart through VMware Tools): DC0_H0_VM1"
run "no VMware Tools: guest restart refused, with the hard option explained" fail vm_reboot.yml -e vm_names=DC0_C0_RP0_VM0 \
    -- "VMware Tools is not running in it" "vm_power_mode: hard"
run "no VMware Tools: hard reset works" ok vm_reboot.yml -e vm_names=DC0_C0_RP0_VM0 -e vm_power_mode=hard -- "(hard reset): DC0_C0_RP0_VM0"
run "unknown power mode refused" fail vm_reboot.yml -e vm_names=DC0_H0_VM1 -e vm_power_mode=soft -- "use guest"
run "dry run shut down: still on" ok vm_shutdown.yml --check -e vm_names=DC0_C0_RP0_VM1 -- "Would shut down"
check "  ...and it is still on" DC0_C0_RP0_VM1 "v['power']" poweredOn
run "shut down through VMware Tools, waits until off" ok vm_shutdown.yml -e vm_names=DC0_C0_RP0_VM1 -- "Shut down (operating system shutdown through VMware Tools): DC0_C0_RP0_VM1"
check "  ...and it is off" DC0_C0_RP0_VM1 "v['power']" poweredOff
run "shut down again: already off" ok vm_shutdown.yml -e vm_names=DC0_C0_RP0_VM1 -- "none. Already off: DC0_C0_RP0_VM1"
run "restart of a VM that is off: refused" fail vm_reboot.yml -e vm_names=DC0_C0_RP0_VM1 -- "is poweredOff: there is nothing to restart"

# ---- snapshots -----------------------------------------------------------------------------------
run "dry run snapshot" ok vm_snapshot.yml --check -e vm_names=DC0_H0_VM1 -e vm_snapshot_name=dry -- 'Would take snapshot \"dry\"'
check "  ...nothing taken" DC0_H0_VM1 "','.join(s['name'] for s in v['snapshots'])" "baseline,multi"
run "snapshot with a name and description: job and user recorded" ok vm_snapshot.yml -e vm_names=DC0_H0_VM1 \
    -e vm_snapshot_name=before-patch -e vm_snapshot_description=CHG123 -- 'Took snapshot \"before-patch\" (no memory) of: DC0_H0_VM1'
check "  ...description" DC0_H0_VM1 "[s['description'] for s in v['snapshots'] if s['name'] == 'before-patch'][0].split(' 20')[0]" \
    "CHG123 [AAP job 4711, operator1,"
run "snapshot, default name aap-<date>-<time>" ok vm_snapshot.yml -e vm_names=DC0_H0_VM1 -- 'Took snapshot \"aap-20'
run "delete: neither a name nor delete-all is refused" fail vm_snapshot_delete.yml -e vm_names=DC0_H0_VM1 -- "not both, not neither"
run "delete: a name AND delete-all is refused" fail vm_snapshot_delete.yml -e vm_names=DC0_H0_VM1 -e vm_snapshot_name=x \
    -e vm_snapshot_delete_all=true -- "not both, not neither"
run "delete a name the VM does not have: says what it has" ok vm_snapshot_delete.yml -e vm_names=DC0_H0_VM1 -e vm_snapshot_name=nope \
    -- 'no snapshot named \"nope\" (it has: baseline, multi, before-patch, aap-'
run "delete one by name" ok vm_snapshot_delete.yml -e vm_names=DC0_H0_VM1 -e vm_snapshot_name=before-patch -- "DC0_H0_VM1: deleted before-patch"
check "  ...the others stay" DC0_H0_VM1 "','.join(s['name'][:5] for s in v['snapshots'])" "basel,multi,aap-2"
run "dry run delete all" ok vm_snapshot_delete.yml --check -e vm_names=DC0_H0_VM1 -e vm_snapshot_delete_all=true -- "would delete baseline, multi, aap-"
run "delete all" ok vm_snapshot_delete.yml -e vm_names=DC0_H0_VM1 -e vm_snapshot_delete_all=true -- "DC0_H0_VM1: deleted baseline, multi, aap-"
check "  ...none left" DC0_H0_VM1 "len(v['snapshots'])" 0

# ---- notes ---------------------------------------------------------------------------------------
devices="$(state DC0_H0_VM1 "v['devices']")"
run "notes: append (with date and user)" ok vm_notes.yml -e vm_names=DC0_H0_VM1 -e "vm_notes='Owner: ops'" -- "AFTER: Owner: ops ("
run "notes: append a second line, the old one is kept" ok vm_notes.yml -e vm_names=DC0_H0_VM1 -e vm_notes=Patched -- "BEFORE: Owner: ops ("
check "  ...two lines, by operator1" DC0_H0_VM1 "str((len(v['annotation'].splitlines()), v['annotation'].count('operator1')))" "(2, 2)"
run "notes: replace" ok vm_notes.yml -e vm_names=DC0_H0_VM1 -e vm_notes=Web -e vm_notes_mode=replace -- "AFTER: Web"
check "  ...replaced" DC0_H0_VM1 "v['annotation']" "Web"
run "notes: clear" ok vm_notes.yml -e vm_names=DC0_H0_VM1 -e vm_notes_mode=clear -- "AFTER: (empty)"
check "  ...hardware untouched by the notes changes" DC0_H0_VM1 "v['devices']" "$devices"

# ---- VLAN (port group) ---------------------------------------------------------------------------
run "VLAN: adapter that does not exist" fail vm_vlan.yml -e vm_names=DC0_H0_VM1 -e vm_nic=3 -e vm_portgroup=VLAN200 \
    -- "has 2 network adapter(s), there is no adapter 3"
run "VLAN: dry run" ok vm_vlan.yml --check -e vm_names=DC0_H0_VM1 -e vm_nic=2 -e vm_portgroup=VLAN200 -- "would move VLAN200"
check "  ...unchanged" DC0_H0_VM1 "[n['network'] for n in v['nics']]" "['DC0_DVPG0', 'VM Network']"
run "VLAN: adapter 2 to another standard port group" ok vm_vlan.yml -e vm_names=DC0_H0_VM1 -e vm_nic=2 -e vm_portgroup=VLAN200 -- "moved VLAN200"
check "  ...adapter 1 untouched, still 2 adapters" DC0_H0_VM1 "sorted((n['type'].split('Virtual')[-1], n['network']) for n in v['nics'])" \
    "[('E1000', 'DC0_DVPG0'), ('Vmxnet3', 'VLAN200')]"
run "VLAN: again - already on it" ok vm_vlan.yml -e vm_names=DC0_H0_VM1 -e vm_nic=2 -e vm_portgroup=VLAN200 -- "already on VLAN200"
run "VLAN: standard -> distributed explains the collection limit" fail vm_vlan.yml -e vm_names=DC0_H0_VM1 -e vm_nic=2 \
    -e vm_portgroup=DC0_DVPG1 -- "between a STANDARD switch port group and a DISTRIBUTED one" "Not changed: DC0_H0_VM1."
run "VLAN: adapter 1 to another distributed port group" ok vm_vlan.yml -e vm_names=DC0_H0_VM1 -e vm_nic=1 -e vm_portgroup=DC0_DVPG1 -- "moved DC0_DVPG1"
check "  ...adapter 2 untouched" DC0_H0_VM1 "sorted((n['type'].split('Virtual')[-1], n['network']) for n in v['nics'])" \
    "[('E1000', 'DC0_DVPG1'), ('Vmxnet3', 'VLAN200')]"

# ---- secure boot report --------------------------------------------------------------------------
# (vcsim cannot store secure boot ON; that case is in tests/test_filters.py)
run "secure boot report: EFI with secure boot off and BIOS listed apart, job fails" fail vm_secure_boot_report.yml \
    -- "SECURE BOOT OFF (fix: power the VM off" "  DC0_C0_RP0_VM0  folder /DC0/vm  on DC0_C0_H" "BIOS FIRMWARE (fix:" \
       "  DC0_H0_VM0  folder /DC0/vm  on DC0_H0  guest aap01.example.mil 10.0.0.5  (poweredOn" "  dup  folder /DC0/vm/other" \
       "6 VM(s) (templates not counted): 0 EFI with secure boot, 2 EFI with secure boot OFF, 4 BIOS"
run "secure boot report, report only (vm_secure_boot_fail: false)" ok vm_secure_boot_report.yml -e vm_secure_boot_fail=false -- "EFI with secure boot OFF"
grep -q '"secure_boot_off": \[' "$work/out.txt" && grep -A3 '"secure_boot_off"' "$work/out.txt" | grep -q '"name": "DC0_C0_RP0_VM0"' \
    && grep -q '"vms": 6\b' "$work/out.txt" && grep -q '"secure_boot_on": 0\b' "$work/out.txt" && grep -q '"folder": "/DC0/vm/other"' "$work/out.txt" \
    && grep -q '"vm_report"' "$work/out.txt" \
    && echo "ok   -   ...the lists are job artifacts (vm_secure_boot, vm_report)" \
    || { echo "FAIL -   ...artifacts"; sed -n '/CUSTOM STATS/,$p' "$work/out.txt" | head -30; fails=$((fails + 1)); }
run "secure boot report for named VMs only" fail vm_secure_boot_report.yml -e vm_names=DC0_H0_VM1 \
    -- "1 VM(s) (templates not counted): 0 EFI with secure boot, 1 EFI with secure boot OFF, 0 BIOS" "  DC0_H0_VM1  folder /DC0/vm  on DC0_H0  guest web01.example.mil 10.0.0.20"

# ---- email the result (roles/site_email; a fake mail relay without encryption) ---------------------
smtp_port=$((port + 37))
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp_port" "$work/mail" 2>/dev/null &
smtp_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp_port") 2>/dev/null && break; sleep 0.1; done
mail="{\"report_email_to\": \"ops@example.mil, vmteam@example.mil\", \"report_email_from\": \"aap@example.mil\",
       \"report_email_smtp_host\": \"127.0.0.1\", \"report_email_smtp_port\": $smtp_port, \"report_email_security\": \"none\"}"
run "secure boot report emailed (and the job still fails on the findings)" fail vm_secure_boot_report.yml -e "$mail" -- "emailed"
eml="$(ls "$work/mail"/*.eml 2>/dev/null | head -1)"
# the body as a mail client shows it (decoded), one line per line
body="$work/body.txt"
getpart() {  # getpart plain|html EML
    "$py" -c 'import email, email.policy, sys
m = email.message_from_binary_file(open(sys.argv[2], "rb"), policy=email.policy.default)
b = m.get_body(preferencelist=(sys.argv[1],))
print(b.get_content() if b is not None and b.get_content_subtype() == sys.argv[1] else "")' "$1" "$2"
}
[ -n "$eml" ] && getpart plain "$eml" > "$body" && getpart html "$eml" > "$work/body.html"
if [ -n "$eml" ] && grep -q "Subject: \[AAP\] VMware secure boot report: 6 VM(s) to fix" "$eml" \
   && grep -q "^Secure boot OFF (EFI firmware) (2)" "$body" && grep -q "^BIOS firmware (4)" "$body" \
   && grep -Eq "^DC0_H0_VM0 +/DC0/vm +DC0_H0 +aap01.example.mil +10.0.0.5 +poweredOn +otherGuest$" "$body" \
   && grep -Eq "^dup +/DC0/vm/other " "$body" \
   && grep -q "^AAP job 4711, started by operator1" "$body" && ! grep -qF '\n' "$body" && grep -q "X-Envelope-To: ops@example.mil,vmteam@example.mil" "$eml" \
   && grep -q "BIOS firmware" "$work/body.html" && grep -q ">DC0_C0_RP0_VM1<" "$work/body.html" && grep -q ">/DC0/vm/other<" "$work/body.html"; then
    echo "ok   -   ...the email: HTML with both tables, a text copy with aligned columns, who ran it, both recipients"
else
    echo "FAIL -   ...email content"; [ -n "$eml" ] && head -20 "$body"; fails=$((fails + 1))
fi
run "an action job emails its result too (snapshot)" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=mailed -e "$mail" -- "emailed"
grep -l 'Subject: \[AAP\] VM snapshot mailed: DC0_C0_RP0_VM1' "$work/mail"/*.eml >/dev/null && echo "ok   -   ...subject names the snapshot and the VM" \
    || { echo "FAIL -   ...snapshot email"; fails=$((fails + 1)); }
run "dry run: says it would email, sends nothing" ok vm_reboot.yml --check -e vm_names=DC0_H0_VM1 -e "$mail" -- "DRY RUN: would email"
n="$(ls "$work/mail" | grep -c 'eml$')"; [ "$n" = 2 ] && echo "ok   -   ...still 2 emails" || { echo "FAIL - dry run emailed ($n)"; fails=$((fails + 1)); }

if [ "$fails" -eq 0 ]; then echo "ok   - all VMware scenarios passed"; else echo "$fails FAILED"; exit 1; fi
