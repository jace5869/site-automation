#!/usr/bin/env bash
# The VMware playbooks (playbooks/vm_*.yml, roles/vmware_vm) against vcsim, VMware's vCenter
# simulator, in a container: restart / shut down (guest and hard, VMware Tools check), the AAP
# server and protected VMs refused, snapshots without memory (create, delete one, delete all),
# notes, VLAN (port group) change of one adapter leaving the others alone, the secure boot report,
# dry runs, duplicate and unknown names, no credential; the alarms report (posted events, a
# disconnected host, an alarm that fired and cleared) and its ACT analysis (a fake ACT:
# tests/containers/fake_act.py); the ESXi security settings; capacity planning (with and without
# GenAI's estimate); the health report (a third simulator with 2 datacenters).
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
name2="vcsim-site-test2-$$"      # a second vCenter (several vCenters, one report)
name3="vcsim-site-test3-$$"      # a third: 2 datacenters (the health report)
port2=$((port + 7))
work="$(mktemp -d)"
smtp_pid=""
http_pid=""
cleanup() { "$engine" rm -f "$name" "$name2" "$name3" >/dev/null 2>&1; [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$http_pid" ] && kill "$http_pid" 2>/dev/null; rm -rf "$work"; }
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
state() {  # state VM FIELD-EXPRESSION (python, v = the VM's first record); STATE_PORT=... for the second vCenter
    VMWARE_PORT=${STATE_PORT:-$port} "$py" "$here/vcsim.py" state | "$py" -c "import json,sys; d=json.load(sys.stdin); v=d['$1'][0]; print($2)"
}

# run NAME EXPECT(ok|fail) PLAYBOOK [args...] then TEXT... after '--'
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && VMWARE_HOST=127.0.0.1 VMWARE_PORT=${RUN_PORT:-$port} VMWARE_USER=user VMWARE_PASSWORD=pass \
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
run "survey text, separated by commas (a dry run)" ok vm_snapshot.yml --check -e '{"vm_names": "DC0_H0_VM1, DC0_C0_RP0_VM1", "vm_snapshot_name": "commas"}' \
    -- 'Would take snapshot \"commas\" (no memory) of: DC0_H0_VM1, DC0_C0_RP0_VM1'
(unset VMWARE_HOST; cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
    env -u VMWARE_HOST "$ap" -i "$work/inv.yml" playbooks/vm_reboot.yml -e vm_names=x > "$work/out.txt" 2>&1)
grep -q 'attach a credential of type' "$work/out.txt" && echo "ok   - no vCenter credential: says so" \
    || { echo "FAIL - no credential message"; fails=$((fails + 1)); }
(cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
    env -u VMWARE_HOST "$ap" -i "$work/inv.yml" playbooks/vm_secure_boot_report.yml > "$work/out.txt" 2>&1)
grep -q 'attach a credential of type' "$work/out.txt" && echo "ok   - secure boot report (every VM) without a vCenter credential: says so" \
    || { echo "FAIL - report: no credential message"; grep -m2 -E 'ERROR|msg' "$work/out.txt"; fails=$((fails + 1)); }

# ---- restart / shut down -------------------------------------------------------------------------
run "dry run (check mode) restart: says what it would do" ok vm_reboot.yml --check -e vm_names=DC0_H0_VM1 -- "Would restart" '"vm_report"' '"dry_run": true' "No email: report_email_to is not set" 
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
run "secure boot report: EFI with secure boot off and BIOS listed apart, job stays green (report only by default)" ok vm_secure_boot_report.yml \
    -- "SECURE BOOT OFF (fix: power the VM off" "  DC0_C0_RP0_VM0  folder /DC0/vm  on DC0_C0_H" "BIOS FIRMWARE (fix:" \
       "  DC0_H0_VM0  folder /DC0/vm  on DC0_H0  guest aap01.example.mil 10.0.0.5  (poweredOn" "  dup  folder /DC0/vm/other" \
       "6 VM(s) (templates not counted): 0 EFI with secure boot, 2 EFI with secure boot OFF, 4 BIOS"
run "secure boot report with vm_secure_boot_fail: true: the job fails, for workflows" fail vm_secure_boot_report.yml -e vm_secure_boot_fail=true -- "EFI with secure boot OFF" "vm_secure_boot_fail: false makes this a report only"
grep -q '"secure_boot_off": \[' "$work/out.txt" && grep -A3 '"secure_boot_off"' "$work/out.txt" | grep -q '"name": "DC0_C0_RP0_VM0"' \
    && grep -q '"vms": 6\b' "$work/out.txt" && grep -q '"secure_boot_on": 0\b' "$work/out.txt" && grep -q '"folder": "/DC0/vm/other"' "$work/out.txt" \
    && grep -q '"vm_report"' "$work/out.txt" \
    && echo "ok   -   ...the lists are job artifacts (vm_secure_boot, vm_report)" \
    || { echo "FAIL -   ...artifacts"; sed -n '/CUSTOM STATS/,$p' "$work/out.txt" | head -30; fails=$((fails + 1)); }
run "secure boot report for named VMs only" ok vm_secure_boot_report.yml -e vm_names=DC0_H0_VM1 \
    -- "1 VM(s) (templates not counted): 0 EFI with secure boot, 1 EFI with secure boot OFF, 0 BIOS" "  DC0_H0_VM1  folder /DC0/vm  on DC0_H0  guest web01.example.mil 10.0.0.20"

# ---- snapshot report and cleanup (DC0_C0_RP0_VM1: baseline, multi; DC0_H0_VM0 = AAP: aap-snap; "later" = 5 days on) --
later=$(( $(date +%s) + 5 * 86400 ))
run "snapshot report: nothing old yet" ok vm_snapshot_report.yml -- "VMware snapshot report (older than 3 days): nothing to delete; 3 snapshot(s) on 2 VM(s)"
run "a snapshot to keep (its name says so)" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=keep-me -- "Took snapshot"
run "snapshot report five days later: to delete apart, held back apart with the reason, sizes readable" ok vm_snapshot_report.yml -e _vm_now=$later \
    -- "VMware snapshot report (older than 3 days): 2 to delete (0 MB), 2 held back; 4 snapshot(s) on 2 VM(s)" \
       "HELD BACK (the VM is the AAP server) DC0_H0_VM0" "HELD BACK (its name or description says keep) DC0_C0_RP0_VM1" \
       "OLD DC0_C0_RP0_VM1" "days old, 0 MB, taken" "jdoe" '"vm_snapshot_cleanup_candidates"' '"held_back"'
run "exclusion list (wildcards): the VM's snapshots are held back" ok vm_snapshot_report.yml -e _vm_now=$later \
    -e '{"vm_snapshot_cleanup_exclude_vms": ["dc0_c0_*"]}' -- "nothing to delete, 4 held back; 4 snapshot(s) on 2 VM(s)" \
       "HELD BACK (the VM is on the exclusion list) DC0_C0_RP0_VM1"
run "vmware_protected_vms is honoured too" ok vm_snapshot_report.yml -e _vm_now=$later -e '{"vmware_protected_vms": ["DC0_C0_RP0_VM?"]}' \
    -- "HELD BACK (the VM is on the exclusion list) DC0_C0_RP0_VM1"
run "a 1-day limit two days on: the heading says 1 day, every snapshot is old" ok vm_snapshot_report.yml -e _vm_now=$(( $(date +%s) + 2 * 86400 )) \
    -e vm_snapshot_max_age_days=1 -- "VMware snapshot report (older than 1 day): 2 to delete (0 MB), 2 held back; 4 snapshot(s)" \
       "OLD = older than 1 day"
run "a limit that is not a number: refused before anything is read as old" fail vm_snapshot_report.yml -e vm_snapshot_max_age_days=three \
    -- "vm_snapshot_max_age_days must be a number of days"
run "size limit: bigger than vm_snapshot_cleanup_max_size_gb is held back" ok vm_snapshot_report.yml -e _vm_now=$later -e vm_snapshot_cleanup_max_size_gb=-1 \
    -- "HELD BACK (bigger than"
run "cleanup on its own, not confirmed: lists, deletes nothing, green" ok vm_snapshot_cleanup.yml -e _vm_now=$later \
    -- "would delete 2 snapshot(s) older than 3 days (nothing deleted)" "WOULD DELETE DC0_C0_RP0_VM1" "HELD BACK DC0_H0_VM0"
check "  ...all three still there" DC0_C0_RP0_VM1 "len(v['snapshots'])" 3
run "cleanup confirmed but more than vm_snapshot_cleanup_max: refused" fail vm_snapshot_cleanup.yml -e _vm_now=$later \
    -e vm_snapshot_cleanup_confirm=true -e vm_snapshot_cleanup_max=1 -- "Refused: 2 snapshots"
run "cleanup confirmed, as a dry run (Check): deletes nothing" ok vm_snapshot_cleanup.yml --check -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -- "would delete 2 snapshot(s)"
check "  ...still three" DC0_C0_RP0_VM1 "len(v['snapshots'])" 3
run "cleanup confirmed, the VM excluded: deletes nothing" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -e '{"vm_snapshot_cleanup_exclude_vms": "DC0_C0_RP0_VM1"}' -- "deleted 0 snapshot(s)"
check "  ...still three" DC0_C0_RP0_VM1 "len(v['snapshots'])" 3
snapmoid="$(state DC0_C0_RP0_VM1 "v['moid']")"; multi="$(state DC0_C0_RP0_VM1 "[s['id'] for s in v['snapshots'] if s['name'] == 'multi'][0]")"
aapmoid="$(state DC0_H0_VM0 "v['moid']")"; aapsnap="$(state DC0_H0_VM0 "v['snapshots'][0]['id']")"
approved="{\"vm_snapshot_cleanup_candidates\": [{\"vm\": \"DC0_C0_RP0_VM1\", \"moid\": \"$snapmoid\", \"id\": $multi, \"name\": \"multi\"},
           {\"vm\": \"gone01\", \"moid\": \"vm-999\", \"id\": 1, \"name\": \"old\"},
           {\"vm\": \"DC0_H0_VM0\", \"moid\": \"$aapmoid\", \"id\": $aapsnap, \"name\": \"aap-snap\"}]}"
run "cleanup after the approval: only the approved snapshot - never the AAP server's, even if listed" ok vm_snapshot_cleanup.yml -e _vm_now=$later \
    -e "$approved" -- "deleted 1 snapshot(s) older than 3 days" 'DELETED DC0_C0_RP0_VM1: \"multi\"' "NOT DELETED gone01" "NOT DELETED DC0_H0_VM0"
check "  ...baseline (old, not approved) and keep-me remain" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "baseline,keep-me"
check "  ...the AAP server's snapshot remains" DC0_H0_VM0 "len(v['snapshots'])" 1
run "cleanup confirmed: deletes the old one, never keep-me or the AAP server's" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -- "deleted 1 snapshot(s)" 'DELETED DC0_C0_RP0_VM1: \"baseline\"'
check "  ...only keep-me remains" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me"
check "  ...and aap-snap" DC0_H0_VM0 "len(v['snapshots'])" 1

# ---- datastore report (read-only; vcsim's LocalDS_0 is about 1% used) -------------------------------
run "datastore report, default thresholds: all below 80%, green" ok vm_datastore_report.yml \
    -- "VMware datastore report: all 1 below 85% used" "LocalDS_0 (DC0): " '"vm_datastores"'
run "datastore report over a (test) threshold: listed apart, still green" ok vm_datastore_report.yml -e vm_datastore_warn_pct=0.5 -e vm_datastore_crit_pct=50 \
    -- "VMware datastore report: 1 of 1 at 0.5%+ used" "WARNING LocalDS_0 (DC0)" '"warning": [' 
run "datastore report with vm_datastore_fail: true: the job fails, for workflows" fail vm_datastore_report.yml -e vm_datastore_warn_pct=0.5 \
    -e vm_datastore_fail=true -- "vm_datastore_fail: false makes this a report only"
run "datastore report for another datacenter: none" ok vm_datastore_report.yml -e vmware_datacenter=NOPE -- "VMware datastore report: all 0 below 85% used"

# ---- email the result (roles/site_email; a fake mail relay without encryption) ---------------------
smtp_port=$((port + 37))
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" "$smtp_port" "$work/mail" 2>/dev/null &
smtp_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$smtp_port") 2>/dev/null && break; sleep 0.1; done
mail="{\"report_email_to\": \"ops@example.mil, vmteam@example.mil\", \"report_email_from\": \"aap@example.mil\",
       \"report_email_smtp_host\": \"127.0.0.1\", \"report_email_smtp_port\": $smtp_port, \"report_email_security\": \"none\"}"
run "secure boot report emailed (and still sent when the job is marked failed)" fail vm_secure_boot_report.yml -e "$mail" -e vm_secure_boot_fail=true -- "emailed"
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
# (the snapshot "mailed" taken above is old by "later": the first run deletes it and emails, the second finds nothing)
run "cleanup, email only if deleted: deleted one, emailed" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -e "$mail" -e vm_snapshot_cleanup_email_only_if_deleted=true -- "deleted 1 snapshot(s)" "emailed"
run "cleanup again, nothing left to delete: no email" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -e "$mail" -e vm_snapshot_cleanup_email_only_if_deleted=true -- "No email: nothing was deleted"
run "snapshot report emailed" ok vm_snapshot_report.yml -e _vm_now=$later -e "$mail" -- "emailed"
eml4="$(ls "$work/mail"/*.eml | sort -V | tail -1)"
getpart html "$eml4" | grep -q ">keep-me<" && grep -q "Subject: \[AAP\] VMware snapshot report (older than 3 days): " "$eml4" \
    && getpart html "$eml4" | grep -q "VMware snapshot report (older than 3 days): " && getpart html "$eml4" | grep -q "Older than 3 days, held back" \
    && echo "ok   -   ...the snapshot table in the email, the age limit in the subject and headings" || { echo "FAIL -   ...snapshot email"; fails=$((fails + 1)); }
run "datastore report emailed" ok vm_datastore_report.yml -e vm_datastore_warn_pct=0.5 -e "$mail" -- "emailed"
eml3="$(ls "$work/mail"/*.eml | sort -V | tail -1)"
getpart plain "$eml3" | grep -Eq "^LocalDS_0 +DC0 +.* ok$" && grep -q "Subject: \[AAP\] VMware datastore report: 1 of 1 at 0.5%+ used" "$eml3" \
    && echo "ok   -   ...subject with the count, a row per datastore" || { echo "FAIL -   ...datastore email"; getpart plain "$eml3" | head; fails=$((fails + 1)); }
run "datastore report emailed, default thresholds" ok vm_datastore_report.yml -e "$mail" -- "emailed"
eml5="$(ls "$work/mail"/*.eml | sort -V | tail -1)"
grep -q "Subject: \[AAP\] VMware datastore report: all 1 below 85% used" "$eml5" && getpart html "$eml5" | grep -q "all 1 below 85% used" \
    && getpart html "$eml5" | grep -q "warning at 85% used, critical at 90%" \
    && echo "ok   -   ...85% (the default) in the subject and the heading" || { echo "FAIL -   ...datastore 85%"; getpart plain "$eml5" | head; fails=$((fails + 1)); }

# ---- ESXi security settings (vcsim has no service system and no lockdown: tests/test_vmware_modules.py
# covers those with a fake host; here an advanced setting vcsim has, the report, email, failures) -------
only='{"esxi_security_ssh": "", "esxi_security_shell": "", "esxi_security_lockdown": "", "esxi_security_settings": {"Config.HostAgent.log.level": "warning"}}'
n0="$(ls "$work/mail" | grep -c 'eml$')"
run "ESXi security, dry run: says what it would change, changes nothing, no email" ok esxi_security.yml --check -e "$only" -e "$mail" \
    -- "ESXi security settings (dry run): 4 of 4 host(s) would change" "Config.HostAgent.log.level info -> warning" "DRY RUN: would email"
run "ESXi security: sets it on every host but the excluded one, emails what it changed" ok esxi_security.yml -e "$only" -e "$mail" \
    -e '{"esxi_security_exclude_hosts": ["dc0_h0"]}' -- "ESXi security settings: 3 of 3 host(s) changed" "on the exclusion list" "emailed"
eml="$(ls "$work/mail"/*.eml | sort -V | tail -1)"
[ "$(ls "$work/mail" | grep -c 'eml$')" = $((n0 + 1)) ] && getpart html "$eml" | grep -q 'bgcolor="#b45309"[^>]*>CHANGED<' \
    && getpart html "$eml" | grep -q "Config.HostAgent.log.level info -&gt; warning" \
    && echo "ok   -   ...one email, the changed hosts in amber" || { echo "FAIL -   ...ESXi security email"; fails=$((fails + 1)); }
run "ESXi security again: nothing to change, no email (only when something changed)" ok esxi_security.yml -e "$only" -e "$mail" \
    -e '{"esxi_security_exclude_hosts": ["dc0_h0"]}' -- "ESXi security settings: all 3 host(s) already set" "No email: no host was changed or failed"
run "ESXi security with the defaults where the host cannot do it: the host fails, named, the job fails" fail esxi_security.yml \
    -e esxi_security_hosts=DC0_H0 -- "ESXi security settings: 0 of 1 host(s) changed, 1 failed" "DC0_H0: services:" "the host has no such setting"
run "ESXi security: strict lockdown is refused" fail esxi_security.yml -e esxi_security_lockdown=strict -- "lockdown strict is refused"
run "ESXi security: a host name that matches nothing is an error, not a silent no-op" fail esxi_security.yml -e esxi_security_hosts=nope \
    -- "No host matches (hosts: nope; clusters: all; datacenter: all)"

# ---- alarms report and its ACT analysis (vcsim triggers no alarms: tests/test_filters.py covers them) --
http_port=$((port + 38))
mkdir -p "$work/www" "$work/act"
"$py" -m http.server "$http_port" --bind 127.0.0.1 --directory "$work/www" >/dev/null 2>&1 &
http_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$http_port") 2>/dev/null && break; sleep 0.1; done
# the model's URL is the local web server (any HTTP answer = reachable); ACT is the fake
actvars="{\"act_triage_script_src\": \"$repo/tests/containers/fake_act.py\", \"act_triage_poll\": 1,
          \"site_act_url\": \"http://127.0.0.1:$http_port/v1/chat/completions\"}"
export FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json"
lastmail() { ls "$work/mail"/*.eml | sort -V | tail -1; }
subject() {  # the decoded Subject of an email (a long one is folded over two lines in the file)
    "$py" -c 'import email, email.policy, sys; print(email.message_from_binary_file(open(sys.argv[1], "rb"), policy=email.policy.default)["Subject"])' "$1"
}
nmail() { ls "$work/mail" | grep -c 'eml$'; }
run "ACT analysis with nothing to analyze (no alarm, no events read): ACT not called, no email" ok vm_alarm_act_analysis.yml \
    -e vm_alarm_hours=0 -e "$mail" -e "$actvars" -- "VMware alarms ACT analysis: nothing to analyze" "No email: nothing to analyze"
VMWARE_PORT=$port "$py" "$here/vcsim.py" events || { echo "ABORT - vcsim events"; exit 1; }
n0="$(nmail)"
run "alarms report: host not connected, connection lost, failed logins grouped, VM and other events, an alarm that fired and cleared; green" \
    ok vm_alarm_report.yml -e "$mail" \
    -- "VMware alarms report: no triggered alarms, 1 host(s) not connected, 1 host(s) lost the connection, 3 failed login(s), 1 alarm(s) fired and cleared (events: last 24 h)" \
       "Host DC0_C0_H1 lost its connection to vCenter" "Guest OS shut down for DC0_H0_VM1" "Datastore LocalDS_0 latency is high" \
       '"hosts_not_connected": [   "DC0_C0_H2"' '"failed_logins"' "emailed" "Alarms that fired in the last 24 h (1)" '"alarms_fired"'
eml="$(lastmail)"; getpart html "$eml" > "$work/al.html"; getpart plain "$eml" > "$work/al.txt"
if [ "$(nmail)" = $((n0 + 1)) ] && subject "$eml" | grep -q "^\[AAP\] VMware alarms report: no triggered alarms, 1 host(s) not connected" \
   && grep -q 'bgcolor="#fee2e2"' "$work/al.html" && grep -q 'bgcolor="#b91c1c"[^>]*>CRITICAL<' "$work/al.html" \
   && grep -q 'bgcolor="#fef3c7"' "$work/al.html" && grep -q 'bgcolor="#b45309"[^>]*>WARNING<' "$work/al.html" \
   && grep -q ">DC0_C0_H2<" "$work/al.html" && grep -q ">disconnected<" "$work/al.html" \
   && grep -Eq "^CRITICAL +DC0_C0_H2 +disconnected" "$work/al.txt" && grep -Eq "^WARNING +user +10.9.9.9 +DC0_C0_H0 +3 " "$work/al.txt" \
   && grep -Eq "^INFO +Host +DC0_C0_H0 +Host error +1 +0 .* cleared +DC0_C0 +DC0" "$work/al.txt" && grep -q 'bgcolor="#dbeafe"' "$work/al.html"; then
    echo "ok   -   ...the email: red and amber rows, the severity cells filled, a text copy"
else
    echo "FAIL -   ...alarms email"; head -40 "$work/al.txt"; fails=$((fails + 1))
fi
run "alarms report with vm_alarm_fail: true: a host not connected fails the job, for workflows" fail vm_alarm_report.yml -e vm_alarm_fail=true \
    -- "1 host(s) not connected (the report is above)"
run "alarms report: an unknown object type is refused" fail vm_alarm_report.yml -e '{"vm_alarm_types": ["host", "hosst"]}' -- "unknown object type(s) hosst"
"$py" - "$work/act/act.json" <<'EOPY'
import json, sys
items = [{"id": "P1", "cause": "The host was disconnected in vCenter.", "evidence": "connection disconnected",
          "fix": "Right-click the host, Connection, Connect.", "confidence": 92},
         {"id": "P2", "cause": "A short management network outage.", "evidence": "HostConnectionLostEvent",
          "fix": "Check the vmk0 uplinks.", "confidence": "55%"},
         {"id": "P3", "cause": "A service account with an old password.", "evidence": "3 failed logins from 10.9.9.9",
          "fix": "Update its password.", "confidence": "low"}]
answer = json.dumps({"overall": "Reconnect DC0_C0_H2 first.", "items": items}, indent=1)
json.dump({"summary": "Analysis below.\nBEGIN_ACT_ANALYSIS\n" + answer + "\nEND_ACT_ANALYSIS"}, open(sys.argv[1], "w"))
EOPY
export GENAI_KEY=test-key
n0="$(nmail)"
run "ACT analysis: problems numbered, the evidence piped to ACT (it runs nothing), causes, fixes and confidence emailed" ok vm_alarm_act_analysis.yml \
    -e "$mail" -e "$actvars" -- "VMware alarms ACT analysis: likely causes and fixes for 4 problem(s)" "92% high" "55% medium" "30% low" \
       "ACT gave no answer for this one" "Reconnect DC0_C0_H2 first." '"vm_alarm_act"' "emailed"
eml="$(lastmail)"; getpart html "$eml" > "$work/act.html"
if [ "$(nmail)" = $((n0 + 1)) ] && grep -q "P1 \[CRITICAL\] Host DC0_C0_H2 (DC0_C0 / DC0) - host is disconnected" "$work/act/fake-act-stdin.txt" \
   && grep -q "^\[DC0_C0_H0\]" "$work/act/fake-act-stdin.txt" && grep -q "DC0_C0_H1: cluster DC0_C0, datacenter DC0, connection connected" "$work/act/fake-act-stdin.txt" \
   && grep -q "BEGIN_ACT_ANALYSIS" "$work/act/fake-act-task.txt" && grep -q "You cannot run any commands" "$work/act/fake-act-task.txt" \
   && grep -q 'bgcolor="#15803d"[^>]*>92% high<' "$work/act.html" && grep -q 'bgcolor="#b45309"[^>]*>55% medium<' "$work/act.html" \
   && grep -q 'bgcolor="#b91c1c"[^>]*>30% low<' "$work/act.html" && grep -q "Likely causes and fixes" "$work/act.html" \
   && subject "$eml" | grep -q "^\[AAP\] VMware alarms ACT analysis: likely causes and fixes for 4 problem(s)"; then
    echo "ok   -   ...ACT got the evidence on stdin and the JSON contract; the email colours the confidence"
else
    echo "FAIL -   ...ACT evidence / email"; head -c 800 "$work/act/fake-act-stdin.txt"; fails=$((fails + 1))
fi
echo '{"summary": "ROOT CAUSE: the host is disconnected. CONFIDENCE: high"}' > "$work/act/act.json"
run "ACT analysis: an answer without the JSON block is shown as text, the job stays green" ok vm_alarm_act_analysis.yml -e "$actvars" \
    -- "ACT's answer is below as text" "ROOT CAUSE: the host is disconnected."
run "ACT analysis as a dry run (Check): ACT not called, green" ok vm_alarm_act_analysis.yml --check -e "$actvars" \
    -- "A dry run (Check): ACT was not called. It would analyze the 4 item(s) below."
INV="$work/inv.yml" run "ACT analysis when the model's URL cannot be reached: says so, emails the problems, fails" fail vm_alarm_act_analysis.yml \
    -e "$mail" -e "$actvars" -e "site_act_url=http://127.0.0.1:$((port + 39))/v1/chat/completions" \
    -- "This AAP node cannot reach the model at http://127.0.0.1:$((port + 39))/v1/chat/completions" "or set a proxy" "emailed"
unset GENAI_KEY
n0="$(nmail)"
run "ACT analysis without an API key: says which credential, still emails the problems, fails" fail vm_alarm_act_analysis.yml -e "$mail" -e "$actvars" \
    -- "This job has no API key for provider genai" "ACT model key" "emailed"
[ "$(nmail)" = $((n0 + 1)) ] && subject "$(lastmail)" | grep -q "ACT did not run - 4 problem(s) not analyzed" \
    && echo "ok   -   ...the email says ACT did not run, and lists the problems" || { echo "FAIL -   ...no-key email"; fails=$((fails + 1)); }

# ---- capacity planning (vcsim's statistics are generated: the checks are about the report, not its numbers) ----
run "capacity planning: overall and per cluster, the disconnected host not counted, emailed" ok vm_capacity_report.yml -e "$mail" \
    -- "vSphere capacity planning: " "Overall" "Clusters: CPU and memory" "Clusters: storage" "Datastores: runway" "Hosts per cluster" \
       "DISCONNECTED" "2 of 3" "Memory after host failure" "LocalDS_0" '"vm_capacity"' "emailed"
eml="$(lastmail)"
subject "$eml" | grep -q "^\[AAP\] vSphere capacity planning: " && getpart html "$eml" | grep -q ">Math vs GenAI" && { echo "FAIL -   ...no GenAI section without vm_capacity_act"; fails=$((fails + 1)); } \
    || echo "ok   -   ...the email, without a GenAI section unless asked"
"$py" - "$work/act/act.json" <<'EOPY'
import json, sys
items = [{"id": "P%d" % i, "estimate": "in about %d months" % i, "estimate_days": 30 * i, "recommendation": "add a host",
          "reasoning": "memory grows", "confidence": 70} for i in range(1, 6)]
json.dump({"summary": "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "Plan one more host.", "items": items}) + "\nEND_ACT_ANALYSIS"},
          open(sys.argv[1], "w"))
EOPY
export GENAI_KEY=test-key FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json"
run "capacity planning with GenAI: the math and ACT's estimate side by side" ok vm_capacity_report.yml -e "$mail" -e "$actvars" \
    -e vm_capacity_act=true -- "Math vs GenAI: runway estimates side by side" "GenAI (ACT): what to do first" "Plan one more host." \
       "in about 1 months" "emailed"
grep -q "estimate_days" "$work/act/fake-act-task.txt" && grep -q "^P1 OVERALL" "$work/act/fake-act-stdin.txt" \
    && grep -q "cluster DC0_C0" "$work/act/fake-act-stdin.txt" && getpart html "$(lastmail)" | grep -q "Math vs GenAI" \
    && grep -q '"ACT_STREAM": "1"' "$work/act/fake-act-env.json" && grep -q '"GENAI_TIMEOUT": "600"' "$work/act/fake-act-env.json" \
    && grep -q '"GENAI_MODEL": "gemini-3.8-flash"' "$work/act/fake-act-env.json" \
    && echo "ok   -   ...ACT got the numbers and the days format, streams, 600 s per answer, gemini-3.8-flash; the email has both estimates" \
    || { echo "FAIL -   ...capacity ACT"; head -5 "$work/act/fake-act-stdin.txt"; fails=$((fails + 1)); }
run "capacity planning with GenAI as a dry run (Check): ACT not called, the math still shown" ok vm_capacity_report.yml --check \
    -e "$actvars" -e vm_capacity_act=true -- "ACT did not run: A dry run (Check)" "Clusters: CPU and memory"
run "capacity planning for one cluster" ok vm_capacity_report.yml -e '{"vm_capacity_clusters": ["dc0_c0"]}' \
    -- "clusters dc0_c0" "Clusters: 1"
run "capacity planning: a cluster name that matches nothing is an error" fail vm_capacity_report.yml -e vm_capacity_clusters=nope \
    -- "No cluster matches nope"
unset GENAI_KEY FAKE_ACT_OUT FAKE_ACT_CONFIG

# ---- several vCenters, one report: a second simulator with the SAME inventory (same VM names, same
# moids) - nothing may be mixed up, above all the snapshot cleanup ------------------------------------
"$engine" run -d --name "$name2" -p "127.0.0.1:$port2:8989" "$image" -l 0.0.0.0:8989 -api-version 8.0 -pg 2 >/dev/null \
    || { echo "ABORT - could not start the second $image"; exit 1; }
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port2") 2>/dev/null && break; sleep 0.2; done
vc2="127.0.0.1:$port2"
two="{\"vmware_vcenters\": [\"$vc2\"]}"
run "vCenter 1: a snapshot old-a on DC0_C0_RP0_VM1" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=old-a -- "Took snapshot"
# (a VM that never had a snapshot trips vmware.vmware.vm_snapshot on a fresh vcsim: take it with pyVmomi, as vcsim.py setup does)
VMWARE_PORT=$port2 "$py" - <<'EOPY' || { echo "ABORT - snapshot on the second vCenter"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
vm = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM1"][0]
t = vm.CreateSnapshot_Task(name="old-b", description="", memory=False, quiesce=False)
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
EOPY
echo "ok   - vCenter 2: a snapshot old-b on its own DC0_C0_RP0_VM1"
m1="$(state DC0_C0_RP0_VM1 "v['moid']")"; m2="$(STATE_PORT=$port2 state DC0_C0_RP0_VM1 "v['moid']")"
id2="$(STATE_PORT=$port2 state DC0_C0_RP0_VM1 "[s['id'] for s in v['snapshots'] if s['name'] == 'old-b'][0]")"
id1="$(state DC0_C0_RP0_VM1 "[s['id'] for s in v['snapshots'] if s['name'] == 'old-a'][0]")"
[ "$m1" = "$m2" ] && echo "ok   -   ...the two VMs have the same ID ($m1): the case to get right" || echo "ok   -   ...(moids differ here: $m1 / $m2)"
run "snapshot report of both vCenters: one report, each snapshot with its vCenter" ok vm_snapshot_report.yml -e _vm_now=$later -e "$two" \
    -- 'OLD DC0_C0_RP0_VM1 (127.0.0.1): \"old-a\"' 'OLD DC0_C0_RP0_VM1 ('"$vc2"'): \"old-b\"' "\"vcenter\": \"$vc2\""
approved2="{\"vm_snapshot_cleanup_candidates\": [{\"vm\": \"DC0_C0_RP0_VM1\", \"vcenter\": \"$vc2\", \"moid\": \"$m2\", \"id\": $id2, \"name\": \"old-b\"}]}"
run "cleanup after the approval of vCenter 2's snapshot: deleted there, and only there" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e "$two" \
    -e "$approved2" -- "deleted 1 snapshot(s)" 'DELETED DC0_C0_RP0_VM1 ('"$vc2"'): \"old-b\"'
check "  ...vCenter 1's VM (same ID) keeps old-a" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me,old-a"
got="$(STATE_PORT=$port2 state DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])")"
[ -z "$got" ] && echo "ok   -   ...vCenter 2's old-b is gone" || { echo "FAIL -   ...vCenter 2 still has [$got]"; fails=$((fails + 1)); }
approved1="{\"vm_snapshot_cleanup_candidates\": [{\"vm\": \"DC0_C0_RP0_VM1\", \"moid\": \"$m1\", \"id\": $id1, \"name\": \"old-a\"}]}"
run "an approval from an older report (no vCenter on it) means the credential's vCenter only" ok vm_snapshot_cleanup.yml --check -e _vm_now=$later \
    -e "$two" -e "$approved1" -- "would delete 1 snapshot(s)" 'WOULD DELETE DC0_C0_RP0_VM1 (127.0.0.1): \"old-a\"'
run "datastore report of both: each datastore with its vCenter" ok vm_datastore_report.yml -e "$two" -e "$mail" \
    -- "VMware datastore report: all 2 below 85% used" "LocalDS_0 (127.0.0.1 / DC0)" "LocalDS_0 ($vc2 / DC0)" "emailed"
getpart html "$(lastmail)" | grep -q "vCenters 127.0.0.1, $vc2" && echo "ok   -   ...the email names both vCenters" \
    || { echo "FAIL -   ...the email's vCenters"; fails=$((fails + 1)); }
run "the same vCenter under a second name is read once" ok vm_datastore_report.yml -e "{\"vmware_vcenters\": [\"localhost:$port\"]}" \
    -- "all 1 below 85% used" "the same vCenter as 127.0.0.1: read once"
run "alarms report of both vCenters" ok vm_alarm_report.yml -e "$two" -e vm_alarm_hours=0 -- "vCenters 127.0.0.1, $vc2"
run "capacity planning of both: the same cluster name in each, never merged" ok vm_capacity_report.yml -e "$two" \
    -- "DC0_C0 (127.0.0.1 / DC0)" "DC0_C0 ($vc2 / DC0)" "vCenters 127.0.0.1, $vc2"
run "ESXi security settings (dry run) on both vCenters' hosts" ok esxi_security.yml --check -e "$two" \
    -e '{"esxi_security_ssh": "", "esxi_security_shell": "", "esxi_security_lockdown": "", "esxi_security_settings": {"Config.HostAgent.log.level": "info"}}' \
    -- "$vc2 / DC0"
run "a vCenter that cannot be reached: the others are reported, it is listed, the report completes green" ok vm_datastore_report.yml \
    -e "{\"vmware_vcenters\": [\"$vc2\", \"127.0.0.1:1\"]}" -e "$mail" \
    -- "NOT READ: Could not connect to vCenter 127.0.0.1:1" "all 2 below 85% used" "emailed"
subject "$(lastmail)" | grep -q "1 vCenter(s) not read" && getpart html "$(lastmail)" | grep -q ">vCenters not read <span" \
    && echo "ok   -   ...the email says so, in red on top" || { echo "FAIL -   ...unreachable vCenter email"; subject "$(lastmail)"; fails=$((fails + 1)); }
run "...vmware_vcenter_fail_unread: true makes the job fail at its end (for a workflow)" fail vm_datastore_report.yml \
    -e "{\"vmware_vcenters\": [\"127.0.0.1:1\"]}" -e vmware_vcenter_fail_unread=true \
    -- "NOT READ: Could not connect to vCenter 127.0.0.1:1" "Not read: Could not connect to vCenter 127.0.0.1:1"

# ---- deploy a VM from a ServiceNow ticket (a fake ServiceNow, tests/servicenow/snmock.py) -----------------
sn_port=$((port + 41))
SNMOCK_LOG="$work/sn.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$sn_port" >/dev/null 2>&1 &
sn_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$sn_port") 2>/dev/null && break; sleep 0.1; done
VMWARE_PORT=$port "$py" - <<'EOPY' || { echo "ABORT - template"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
vms = c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view
src = [v for v in vms if v.name == "DC0_C0_RP0_VM0"][0]
t = src.CloneVM_Task(folder=src.parent, name="rhel9-gold", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False, template=True))
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
EOPY
newticket() {  # newticket DESCRIPTION [COMMENT] -> the incident number
    "$py" - "$sn_port" "$1" "${2:-}" <<'EOPY'
import base64, json, sys, urllib.request
body = {"short_description": "New VM", "description": sys.argv[2], "assignment_group": "Linux Operations"}
import os
if os.environ.get("NEWTICKET_NUMBER"):          # a site's own number prefix (INCN...)
    body["number"] = os.environ["NEWTICKET_NUMBER"]
if sys.argv[3]:
    body["comments"] = sys.argv[3]
r = urllib.request.Request("http://127.0.0.1:%s/api/now/table/incident" % sys.argv[1], data=json.dumps(body).encode(), method="POST",
                           headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()})
print(json.load(urllib.request.urlopen(r))["result"]["number"])
EOPY
}
export SN_HOST="http://127.0.0.1:$sn_port" SN_USERNAME=api SN_PASSWORD=goodpw
rules='{"vm_deploy_templates": ["rhel9-*"], "vm_deploy_assignment_group": "Linux Operations", "vm_deploy_max_cpu": 8, "vm_deploy_wait_ip": 3, "vm_deploy_wait_customization": 60}'
inc="$(newticket $'Please build a server.\nvm_name: app01\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4\nName: Jane Doe' $'more power please\ncpu: 4\nmemory_gb: 8')"
n0=$(wc -l < "$work/sn.jsonl")
run "deploy from a ticket, dry run: the request with where each value came from, where it would go; nothing built or written" ok \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$inc" -e "$rules" \
    -- "vm_name: app01   (from description)" "cpu: 4   (from comment" "ready to build in DC0_C0 - dry run"
[ "$(sed -n "$((n0 + 1)),\$p" "$work/sn.jsonl" | grep -c '"PATCH"')" = 0 ] && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"app01"' \
    && echo "ok   -   ...no VM built, nothing written on the ticket" || { echo "FAIL -   ...the dry run built or wrote something"; fails=$((fails + 1)); }
run "deploy from a ticket: a template not allowed is refused, and the ticket says why" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc" -e "$rules" -e '{"vm_deploy_templates": ["win*"]}' -- "REFUSED: template 'rhel9-gold' is not in vm_deploy_templates"
tail -1 "$work/sn.jsonl" | "$py" -c '
import json, sys
n = json.loads(sys.stdin.read())["body"]["work_notes"].splitlines()
sys.exit(0 if n[0].startswith("[AAP] VM not deployed (") and n[1].startswith("- template ") and n[-1].startswith("To fix it:") else 1)' \
    && echo "ok   -   ...the work note says why: a header, one problem per line, what to do" || { echo "FAIL -   ...refusal note"; fails=$((fails + 1)); }
run "deploy from a ticket: built - 4 vCPU and 8 GB (the comment wins), host name by customization, the result on the ticket" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc" -e "$rules" -e "$mail" \
    -- "VM deploy $inc: app01 deployed, no IP yet" "Customization" "succeeded" "no IPv4 address within 3 s" "emailed"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...app01 exists: 4 vCPU, 8192 MB, notes name the ticket, guest host name app01" || { echo "FAIL -   ...the VM"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "app01"][0]
ok = v.config.hardware.numCPU == 4 and v.config.hardware.memoryMB == 8192 and "INC" in (v.config.annotation or "") and v.guest.hostName == "app01"
print(v.config.hardware.numCPU, v.config.hardware.memoryMB, v.config.annotation, v.guest.hostName)
sys.exit(0 if ok else 1)
EOPY
"$py" - "$work/sn.jsonl" <<'EOPY' && echo "ok   -   ...the ticket got 'Deploying' (with the request) before the build, then 'deployed'" || { echo "FAIL -   ...ticket notes"; fails=$((fails + 1)); }
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in open(sys.argv[1]) if '"PATCH"' in l]
i = [k for k, n in enumerate(notes) if n.startswith("[AAP] Deploying this VM")]
j = [k for k, n in enumerate(notes) if n.startswith("[AAP] VM app01 deployed")]
sys.exit(0 if i and j and i[-1] < j[-1] and "cpu: 4" in notes[i[-1]] and "Customization: succeeded" in notes[j[-1]]
         and "\nIP address: " in notes[j[-1]] else 1)
EOPY
run "deploy from the same ticket again: the VM it built is left as it is (its note and the VM's notes say so)" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc" -e "$rules" -- "VM deploy $inc: app01 deployed" "NOTE: built by an earlier run for this ticket"
tail -1 "$work/sn.jsonl" | grep -q 'Built by an earlier run for this ticket: left as it is' && echo "ok   -   ...noted on the ticket" \
    || { echo "FAIL -   ...left-as-it-is note"; fails=$((fails + 1)); }
incd="$(newticket $'vm_name: app01\ntemplate: rhel9-gold')"
run "another ticket for the same name: the name exists - refused, nothing built, the ticket says so" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incd" -e "$rules" -- "a VM named app01 already exists in vCenter 127.0.0.1"
tail -1 "$work/sn.jsonl" | grep -q 'NOT deployed' && echo "ok   -   ...noted on the ticket" || { echo "FAIL -   ...duplicate note"; fails=$((fails + 1)); }
inc2="$(newticket $'vm_name: app02\ntemplate: rhel9-gold')"
"$py" - "$sn_port" "$inc2" <<'EOPY'
import base64, json, sys, urllib.request
h = {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
u = "http://127.0.0.1:%s/api/now/table/incident" % sys.argv[1]
sid = json.load(urllib.request.urlopen(urllib.request.Request(u + "?sysparm_query=number=" + sys.argv[2], headers=h)))["result"][0]["sys_id"]
urllib.request.urlopen(urllib.request.Request(u + "/" + sid, data=b'{"state": "7"}', method="PATCH", headers=h))
EOPY
run "deploy from a closed ticket: refused" fail vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc2" -e "$rules" -- "the ticket is closed"
# ---- a task whose change holds the request (vm_deploy_parent_*); only the task is closed (vm_deploy_done_fields_by_table)
newrec() {  # newrec TABLE JSON-BODY -> the new record's number
    "$py" - "$sn_port" "$1" "$2" <<'EOPY'
import base64, json, sys, urllib.request
r = urllib.request.Request("http://127.0.0.1:%s/api/now/table/%s" % (sys.argv[1], sys.argv[2]), data=sys.argv[3].encode(), method="POST",
                           headers={"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()})
print(json.load(urllib.request.urlopen(r))["result"]["number"])
EOPY
}
chg="$(newrec change_request '{"short_description": "Build chgapp01", "description": "Approved.\nvm_name: chgapp01\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Change Board"}')"
sct="$(newrec sc_task "{\"short_description\": \"VM request for $chg\", \"description\": \"memory_gb: 8\", \"parent\": \"$chg\", \"assignment_group\": \"Linux Operations\"}")"
close='{"vm_deploy_done_fields_by_table": {"sc_task": {"state": "3", "close_notes": "VM deployed by AAP"}}}'
run "a task whose change holds the request, dry run: the change's lines under the task's own" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$sct" -e "$rules" -e "$close" \
    -- "Parent: parent $chg (change_request, field parent) - read: description" "vm_name: chgapp01   (from parent $chg description)" \
       "cpu: 2   (from parent $chg description)" "memory_gb: 8   (from description)" "ready to build in DC0_C0 - dry run"
n1=$(wc -l < "$work/sn.jsonl")
run "the task built: closed by vm_deploy_done_fields_by_table" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$sct" -e "$rules" -e "$close" \
    -- "VM deploy $sct: chgapp01 deployed" "Ticket closed: close_notes = VM deployed by AAP, state = 3"
"$py" - "$work/sn.jsonl" "$n1" "$sn_port" "$sct" "$chg" <<'EOPY' && echo "ok   -   ...only the task was written and closed (state 3); the change has no note and is still in Implement" || { echo "FAIL -   ...the task / change after the build"; fails=$((fails + 1)); }
import base64, json, sys, urllib.request
lines = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):]]
writes = [l for l in lines if l["method"] in ("PATCH", "POST")]
h = {"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
get = lambda t, n: json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/now/table/%s?sysparm_query=number=%s" % (sys.argv[3], t, n), headers=h)))["result"][0]
task, change = get("sc_task", sys.argv[4]), get("change_request", sys.argv[5])
ok = (writes and all(l["path"].startswith("/api/now/table/sc_task/") for l in writes)
      and [l["body"] for l in writes if "state" in l["body"]] == [{"state": "3", "close_notes": "VM deployed by AAP"}]
      and "deployed" in [l["body"].get("work_notes", "") for l in writes if "work_notes" in l["body"]][-1]
      and task["state"] == "3" and change["state"] == "-1" and not change.get("work_notes_list"))
print(task["state"], change["state"], [l["path"] for l in writes])
sys.exit(0 if ok else 1)
EOPY
run "the same task again: closed now - refused" fail vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$sct" -e "$rules" -- "the ticket is closed"
sct2="$(newrec sc_task '{"short_description": "VM request [mock:keep-state]", "description": "vm_name: chgapp02\ntemplate: rhel9-gold", "assignment_group": "Linux Operations"}')"
run "a business rule keeps the task open: VM built, job green, it says the ticket is NOT closed - and so does the ticket" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$sct2" -e "$rules" -e "$close" \
    -- "Parent: no parent ticket (looked at the fields change_request, parent)" \
       "WARNING: the ticket was NOT closed: ServiceNow answered OK but kept state = '1' (sent '3')"
tail -1 "$work/sn.jsonl" | grep -q 'this ticket could not be closed' && echo "ok   -   ...a work note says to close it by hand" || { echo "FAIL -   ...not-closed note"; fails=$((fails + 1)); }
chg2="$(newrec change_request '{"short_description": "Old change", "description": "vm_name: chgapp03\ntemplate: rhel9-gold", "state": "3"}')"
sct3="$(newrec sc_task "{\"short_description\": \"VM request\", \"parent\": \"$chg2\", \"assignment_group\": \"Linux Operations\"}")"
run "a task whose change is closed: refused, nothing built" fail vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$sct3" -e "$rules" \
    -- "the parent ticket $chg2 is closed"
sct4="$(newrec sc_task "{\"short_description\": \"VM request\", \"description\": \"vm_name: chgapp04\ntemplate: rhel9-gold\", \"parent\": \"$inc\", \"assignment_group\": \"Linux Operations\"}")"
run "a parent that is not a change (an incident): not read" ok vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$sct4" -e "$rules" \
    -- "Parent: parent $inc (incident, field parent): not read - only a parent in change_request (vm_deploy_parent_tables)" "vm_name: chgapp04   (from description)"
# ---- a change task: built only while its change is approved and in Implement (vm_deploy_ready_by_table),
# the change's plain words read under the task's own, the task closed 'successful with issues' when something did not finish
setrec() {  # setrec TABLE NUMBER JSON-BODY
    "$py" - "$sn_port" "$1" "$2" "$3" <<'EOPY'
import base64, json, sys, urllib.request
h = {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
u = "http://127.0.0.1:%s/api/now/table/%s" % (sys.argv[1], sys.argv[2])
sid = json.load(urllib.request.urlopen(urllib.request.Request(u + "?sysparm_query=number=" + sys.argv[3], headers=h)))["result"][0]["sys_id"]
urllib.request.urlopen(urllib.request.Request(u + "/" + sid, data=sys.argv[4].encode(), method="PATCH", headers=h))
EOPY
}
ctw='{"vm_deploy_plain_words": true, "vm_deploy_templates": ["rhel9-gold"],
      "vm_deploy_done_fields_by_table": {"change_task": {"state": "3", "close_code": "successful", "close_notes": "VMs deployed by AAP"}},
      "vm_deploy_done_fields_issues_by_table": {"change_task": {"close_code": "successful_issues"}}}'
chg6="$(newrec change_request '{"short_description": "New servers", "description": "I need 2 vms created named ctapp01 and ctapp02\nboth servers need:\nRHEL 9\n2 cpus\n4gb of memory", "state": "-4", "approval": "requested"}')"
ct6="$(newrec change_task "{\"short_description\": \"Platform team to deploy VM with access, (CPU, Storage, Memory, OS image details and location) for the service owner to install\", \"change_request\": \"$chg6\", \"assignment_group\": \"Linux Operations\"}")"
run "a change task whose change is not approved, in Assess: refused, nothing built - the task says why" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$ct6" -e "$rules" -e "$ctw" \
    -- "the change $chg6 (this ticket's parent) is not ready to build: approval is 'Requested', not Approved; state is 'Assess', not Implement"
tail -1 "$work/sn.jsonl" | grep -q "run the job again once it is" && echo "ok   -   ...noted on the change task" || { echo "FAIL -   ...not-ready note"; fails=$((fails + 1)); }
setrec change_request "$chg6" '{"approval": "approved", "state": "-1"}'
run "...approved, in Implement: the propose step reads the change's plain words - two VMs, checked, nothing built" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$ct6" -e "$rules" -e "$ctw" -e vm_deploy_phase=propose \
    -- "vm_name: ctapp01, ctapp02   (from the words 'named ctapp01 and ctapp02' in the parent $chg6 description)" \
       "template: rhel9-gold   (from the words 'rhel 9' in the parent $chg6 description)" "waiting for the approval"
setrec change_request "$chg6" '{"state": "-4"}'
run "...sent back to Assess while the approval waited: the build step refuses" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$ct6" -e "$rules" -e "$ctw" -e vm_deploy_phase=build -- "state is 'Assess', not Implement"
VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"ctapp01"' && { echo "FAIL -   ...built while the change was not ready"; fails=$((fails + 1)); } \
    || echo "ok   -   ...no VM built"
setrec change_request "$chg6" '{"state": "-1"}'
n3=$(wc -l < "$work/sn.jsonl")
run "...in Implement again: both built; the task closed 'successful with issues' (no IP in time); the change untouched" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$ct6" -e "$rules" -e "$ctw" -e vm_deploy_phase=build \
    -- "2 VMs (ctapp01, ctapp02) deployed" "Ticket closed: close_code = successful_issues, close_notes = VMs deployed by AAP, state = 3"
"$py" - "$work/sn.jsonl" "$n3" <<'EOPY' && echo "ok   -   ...only the change task was written" || { echo "FAIL -   ...wrote to the change"; fails=$((fails + 1)); }
import json, sys
writes = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):]]
writes = [l for l in writes if l["method"] in ("PATCH", "POST")]
sys.exit(0 if writes and all(l["path"].startswith("/api/now/table/change_task/") for l in writes) else 1)
EOPY
chg7="$(newrec change_request '{"short_description": "VM request", "description": "vm_name: chgapp07\ntemplate: rhel9-gold", "state": "-1", "approval": "requested", "assignment_group": "Linux Operations"}')"
run "a change as the ticket itself, not approved: refused (dry run)" fail vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$chg7" -e "$rules" \
    -- "this ticket ($chg7) is not ready to build: approval is 'Requested', not Approved"
run "...vm_deploy_ready_by_table: {} - no such check (dry run)" ok vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$chg7" -e "$rules" \
    -e '{"vm_deploy_ready_by_table": {}}' -- "chgapp07 ready to build in DC0_C0 - dry run"
run "deploy from a ticket: not a ticket number" fail vm_deploy_from_ticket.yml -e vm_deploy_ticket=app01 -e "$rules" -- "Give the ticket number"
# a site's own number prefix (INCN...): the job asks ServiceNow's task table which table the ticket is in
incn="$(NEWTICKET_NUMBER=INCN0012345 newticket $'vm_name: appincn01\ntemplate: rhel9-gold')"
inc10="$(newticket $'VM request\r\nvm_name: wordsdisk01\r\ntemplate: rhel9-gold\r\n2 cpus\r\n4gb of memory\r\nstorage 120gb and 50gb')"
run "plain words read the disks: 'storage 120gb and 50gb' = disk 1 of 120 GB and an added 50 GB disk (dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc10" -e "$rules" -e '{"vm_deploy_plain_words": true}' \
    -- "disks_gb: 120, 50   (from the words 'storage 120gb and 50gb' in the description)" "memory_gb: 4   (from the words '4gb of memory'" \
       "ready to build in DC0_C0 - dry run"
inc12="$(newticket $'VM request\r\nHostname: wordsfold01\r\ntemplate: rhel9-gold\r\nCores: 2\r\nMemory: 4096 MB\r\nDisk 1: 120 GB\r\nDisk 2: 50 GB\r\nput it in the other folder')"
run "the names people write: Hostname:, Cores:, Memory in MB, Disk 1 / Disk 2, 'the other folder' (dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc12" -e "$rules" -e '{"vm_deploy_plain_words": true}' \
    -- "vm_name: wordsfold01   (from description)" "cpu: 2   (from description)" "memory_gb: 4   (from description)" \
       "disks_gb: 120, 50   (from the words 'Disk 1: 120 GB', 'Disk 2: 50 GB' in the description)" "folder: other   (from the words" \
       "ready to build in DC0_C0 - dry run"
# ---- several VMs on one ticket: one per name, the same size; closed when all are built ------------
words='{"vm_deploy_plain_words": true, "vm_deploy_templates": ["rhel9-gold"]}'
incm="$(newticket $'I need 2 vms created named multi01 and multi02\r\nboth servers need:\r\nRHEL 9\r\n2 cpus\r\n4gb of memory')"
run "two VMs on one ticket, dry run: both names, one size, nothing built" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$incm" -e "$rules" -e "$words" \
    -- "vm_name: multi01, multi02   (from the words 'named multi01 and multi02' in the description)" \
       "2 VMs (multi01, multi02) ready to build - dry run" "multi01: ready to build" "multi02: ready to build"
n2=$(wc -l < "$work/sn.jsonl")
run "two VMs on one ticket: both built, one note each, the ticket closed once" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incm" -e "$rules" -e "$words" -e '{"vm_deploy_done_fields": {"state": "6", "close_notes": "VMs deployed by AAP"}}' \
    -- "2 VMs (multi01, multi02) deployed" "multi01: deployed" "multi02: deployed" "Ticket closed: close_notes = VMs deployed by AAP, state = 6"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...multi01 and multi02 exist: 2 vCPU, 4096 MB each, their notes name the ticket" || { echo "FAIL -   ...the two VMs"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
vs = dict((x.name, x) for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view)
sys.exit(0 if all(n in vs and vs[n].config.hardware.numCPU == 2 and vs[n].config.hardware.memoryMB == 4096
                  and "INC" in (vs[n].config.annotation or "") for n in ("multi01", "multi02")) else 1)
EOPY
"$py" - "$work/sn.jsonl" "$n2" <<'EOPY' && echo "ok   -   ...the ticket: 'Deploying 2 VMs', then 'VM multi01 deployed - 1 of 2' and '- 2 of 2', then closed" || { echo "FAIL -   ...two-VM notes"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):] if '"PATCH"' in l]
notes = [r["body"].get("work_notes", "") for r in rows]
closes = [r for r in rows if r["body"].get("state") == "6"]
a = [k for k, n in enumerate(notes) if n.startswith("[AAP] Deploying 2 VMs, one after the other")]
b = [k for k, n in enumerate(notes) if n.startswith("[AAP] VM multi01 deployed (") and n.splitlines()[0].endswith(" - 1 of 2.")]
c = [k for k, n in enumerate(notes) if n.startswith("[AAP] VM multi02 deployed (") and n.splitlines()[0].endswith(" - 2 of 2.")]
sys.exit(0 if a and b and c and a[0] < b[0] < c[0] and len(closes) == 1 and "vm_name: multi01, multi02" in notes[a[0]] else 1)
EOPY
# one of two fails (its name is taken by a VM that is not the ticket's): the other is built, the ticket stays open;
# the name freed, the job run again builds only the missing one and closes the ticket
VMWARE_PORT=$port "$py" - <<'EOPY' || { echo "ABORT - multi04"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
src = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM1"][0]
t = src.CloneVM_Task(folder=src.parent, name="multi04", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False))
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
EOPY
incp="$(newticket $'Server names:\r\n- multi03\r\n- multi04\r\nRHEL 9, 2 cpus, 4gb ram')"
close6='{"vm_deploy_done_fields": {"state": "6", "close_notes": "VMs deployed by AAP"}}'
n3=$(wc -l < "$work/sn.jsonl")
run "two VMs, one name taken: the other built, the job fails, the ticket stays open and says what to do" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incp" -e "$rules" -e "$words" -e "$close6" \
    -- "1 of 2 deployed - the ticket stays open" "multi03: deployed" "multi04: NOT deployed - a VM named multi04 already exists" \
       "1 of 2 VMs not deployed: multi04: a VM named multi04 already exists"
"$py" - "$work/sn.jsonl" "$n3" <<'EOPY' && echo "ok   -   ...notes: multi03 deployed, multi04 NOT deployed, '1 of 2 VMs built' with each one's result; not closed" || { echo "FAIL -   ...partial notes"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):] if '"PATCH"' in l]
notes = [r["body"].get("work_notes", "") for r in rows]
s = [n for n in notes if n.startswith("[AAP] 1 of 2 VMs built (")]
sys.exit(0 if any(n.startswith("[AAP] VM multi03 deployed (") for n in notes) and any(n.startswith("[AAP] VM multi04 NOT deployed (") for n in notes)
         and s and "\n- multi03: built\n- multi04: NOT deployed - a VM named multi04" in s[0] and s[0].splitlines()[-1].startswith("To fix it:")
         and not any(r["body"].get("state") == "6" for r in rows) else 1)
EOPY
VMWARE_PORT=$port "$py" - <<'EOPY' || { echo "ABORT - multi04 delete"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "multi04"][0]
t = v.Destroy_Task()
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
EOPY
run "...the name freed, run again: multi03 left as it is, multi04 built, the ticket closed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incp" -e "$rules" -e "$words" -e "$close6" \
    -- "2 VMs (multi03, multi04) deployed" "multi03: already built (left as it is)" "multi04: deployed" "Ticket closed: close_notes = VMs deployed by AAP"
incq="$(newticket $'2 vms named multi05, RHEL 9, 2 cpus, 4gb ram')"
run "two VMs asked, one name given: a question to the requester, nothing built" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incq" -e "$rules" -e "$words" -- "REFUSED: the ticket asks for 2 VMs ('2 vms', in the description) but names 1 (multi05)"
tail -1 "$work/sn.jsonl" | grep -q '"comments"' && echo "ok   -   ...asked on the ticket (a comment)" || { echo "FAIL -   ...count question"; fails=$((fails + 1)); }
incr="$(newticket $'named mx01, mx02 and mx03 - RHEL 9, 2 cpus, 4gb ram')"
run "more VMs than vm_deploy_max_vms: refused, nothing built" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incr" -e "$rules" -e "$words" -e vm_deploy_max_vms=2 -- "the ticket asks for 3 VMs (mx01, mx02, mx03): more than 2 (vm_deploy_max_vms)"
# the workflow with two VMs: step 1 (propose) checks and writes where each would go, step 3 (after the approval) builds both
incw="$(newticket $'Please build 2 servers named wfl01 and wfl02, RHEL 9, 2 cpus, 4gb ram')"
nw=$(wc -l < "$work/sn.jsonl")
run "two VMs, workflow step 1 (propose): each checked and placed, a note each, nothing built, the approver emailed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incw" -e "$rules" -e "$words" -e vm_deploy_phase=propose -e "$mail" \
    -- "2 VMs (wfl01, wfl02) ready to build - waiting for the approval" "wfl01: ready to build" "wfl02: ready to build" "emailed"
"$py" - "$work/sn.jsonl" "$nw" <<'EOPY' && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"wfl01"' \
    && echo "ok   -   ...'Checked - VM wfl01 - 1 of 2' and '- 2 of 2' (with the placement caveat) on the ticket; nothing built" \
    || { echo "FAIL -   ...two-VM propose notes"; fails=$((fails + 1)); }
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):] if '"PATCH"' in l]
a = [n for n in notes if n.startswith("[AAP] Checked - VM wfl01 - 1 of 2 - ready to build after the approval (")]
b = [n for n in notes if n.startswith("[AAP] Checked - VM wfl02 - 2 of 2 - ready to build after the approval (")]
sys.exit(0 if a and b and "\nvm_name: wfl01\n" in a[0] and "\n- Cluster: " in a[0] and "Each VM is placed at the build" in b[0]
         and "Each VM is placed" not in a[0] else 1)
EOPY
getpart html "$(lastmail)" | grep -q "Checked: nothing is built yet. Approve the workflow to build them" \
    && echo "ok   -   ...the approver's email: both VMs, nothing built yet" || { echo "FAIL -   ...two-VM approval email"; fails=$((fails + 1)); }
nw=$(wc -l < "$work/sn.jsonl")
run "...workflow step 3 (build, after the approval): both built, the ticket closed once" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incw" -e "$rules" -e "$words" -e vm_deploy_phase=build -e "$close6" \
    -- "2 VMs (wfl01, wfl02) deployed" "wfl01: deployed" "wfl02: deployed" "Ticket closed: close_notes = VMs deployed by AAP"
"$py" -c 'import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):] if "\"PATCH\"" in l]
sys.exit(0 if len([r for r in rows if r["body"].get("state") == "6"]) == 1 else 1)' "$work/sn.jsonl" "$nw" \
    && echo "ok   -   ...closed with one PATCH" || { echo "FAIL -   ...two-VM workflow close"; fails=$((fails + 1)); }
# ---- the VMs created report (e.g. Fridays at noon): the week's VMs, by whom, the AAP ones with their ticket, the deleted ones
run "VMs created report: the week's VMs, the deploy's with their ticket, the one deleted since; emailed" ok vm_created_report.yml -e "$mail" \
    -- "VMs created in the last 7 days (" "by AAP from tickets" "deleted since)" "multi01 by user - cloned from rhel9-gold - ticket $incm" \
       "multi02 by user - cloned from rhel9-gold - ticket $incm" " multi04 by user - cloned from DC0_C0_RP0_VM1" "By whom: " "emailed"
grep -F "DELETED " "$work/out.txt" | grep -qF "multi04" && echo "ok   -   ...the multi04 deleted earlier is listed as DELETED" \
    || { echo "FAIL -   ...deleted multi04"; fails=$((fails + 1)); }
getpart html "$(lastmail)" | grep -q ">Created and deleted since <span" && getpart html "$(lastmail)" | grep -q ">By whom <span" \
    && echo "ok   -   ...the email: the VMs, by whom, by day, created and deleted since" || { echo "FAIL -   ...created report email"; fails=$((fails + 1)); }
run "VMs created report: a week with none says so" ok vm_created_report.yml -e _vm_now=1000000000 -- "VMware: no VMs created in the last 7 days"
run "VMs created report: vm_created_days not a number - refused" fail vm_created_report.yml -e vm_created_days=week -- "vm_created_days must be a number of days"
inc15="$(newticket $'VM request\r\nRed Hat Enterprise Linux 9 server named osword01\r\n2 cpus, 4gb ram')"
run "the OS as people write it finds the template (no vm_deploy_plain_templates; dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc15" -e "$rules" -e '{"vm_deploy_plain_words": true, "vm_deploy_templates": ["rhel9-gold"]}' \
    -- "template: rhel9-gold   (from the words 'red hat enterprise linux 9' in the description)" "ready to build in DC0_C0 - dry run"
run "vm_deploy_plain_templates written as a list of words: the job says how it is written (nothing read or built)" fail \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$inc15" -e "$rules" \
    -e '{"vm_deploy_plain_words": true, "vm_deploy_templates": ["rhel9-gold"], "vm_deploy_plain_templates": ["Server", "2022", "Windows"]}' \
    -- "vm_deploy_plain_templates maps words to a template - e.g. {" "rhel9-gold" "not a list of words"
run "deploy from a ticket with a site's own prefix (INCN...): its table found through the task table (dry run)" ok \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$incn" -e "$rules" -- "appincn01 ready to build in"
run "deploy from an unknown prefix that is in no table: says which setting names it" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket=VMR0001234 -e "$rules" -- "Which table is VMR0001234 in? ServiceNow has no ticket VMR0001234" \
    "vm_deploy_ticket_prefixes"
# a request in plain words, without GenAI (vm_deploy_plain_words): the words for the template and a size word
words='{"vm_deploy_plain_words": true, "vm_deploy_plain_templates": {"red hat 9": "rhel9-gold"},
        "vm_deploy_plain_sizes": {"small": {"cpu": 1, "memory_gb": 1}, "medium": {"cpu": 2, "memory_gb": 2}}}'
incw="$(newticket 'Hi, please build a Red Hat 9 server named lnxwords01, medium size, for the portal. Thanks, Jane')"
run "plain words: the workflow's propose step reads the words, checks and places - nothing built" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incw" -e "$rules" -e "$words" -e vm_deploy_phase=propose \
    -- "lnxwords01 ready to build in" "(from the words 'red hat 9' in the description)" "(from size 'medium'"
run "plain words: the build step builds the same VM (template from 'red hat 9', 2 vCPU and 2 GB from 'medium')" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incw" -e "$rules" -e "$words" -e vm_deploy_phase=build -- "lnxwords01 deployed" \
       "cpu: 2   (from size 'medium'" "memory_gb: 2   (from size 'medium'" "template: rhel9-gold   (from the words 'red hat 9'"
incq="$(newticket 'Red Hat 9, small or medium, named lnxwords02')"
run "plain words: two sizes - never a guess: a question, nothing built" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incq" -e "$rules" -e "$words" -- "the ticket names more than one size ('small', 'medium'"
tail -1 "$work/sn.jsonl" | grep -q '"comments": "\[AAP\] We cannot build this VM yet' \
    && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"lnxwords02"' \
    && echo "ok   -   ...asked as a comment (requesters see comments), lnxwords02 not built" || { echo "FAIL -   ...plain words question"; tail -1 "$work/sn.jsonl" | cut -c1-300; fails=$((fails + 1)); }
run "deploy from a ticket: no ticket number at all (no survey): says how to add the survey" fail vm_deploy_from_ticket.yml -e "$rules" \
    -- "Give the ticket number: the job got none" "answer variable name vm_deploy_ticket"
SN_HOST= run "deploy from a ticket without the ServiceNow credential: says which to attach" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc" -e "$rules" -- 'attach the \"ServiceNow API\" credential'
VMWARE_PORT=$port "$py" - <<'EOPY' || { echo "ABORT - PXE template"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
src = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM1"][0]
t = src.CloneVM_Task(folder=src.parent, name="win2022-pxe", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False, template=False))
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
# a Windows guest OS, as the real template has: the 15-character name limit is for Windows only
v = t.info.result
t = v.ReconfigVM_Task(spec=vim.vm.ConfigSpec(guestId="windows2019srv_64Guest"))
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
v.MarkAsTemplate()
assert v.config.template and v.config.guestId.startswith("win"), (v.config.template, v.config.guestId)
EOPY
sites='{"vm_deploy_templates": ["rhel9-*", "win2022-*"], "vm_deploy_pxe_templates": ["*-pxe"], "vm_deploy_wait_ip": 3,
        "vm_deploy_sites": {"SiteA": {"cluster": "DC0_C0"}, "SiteB": {"cluster": "DC0_H0"}}}'
inc3="$(newticket $'vm_name: winapp01\ntemplate: win2022-pxe\nsite: siteb')"
run "deploy from a ticket: a network-boot (PXE / MECM) Windows template, its site picks the cluster; the ticket gets the MAC" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc3" -e "$sites" \
    -- "cluster: DC0_H0   (from site SiteB)" "winapp01 deployed - installs its OS, MAC 00:" "none: the template installs its operating system"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...winapp01 is on SiteB's host DC0_H0, powered on, not customized, its name in guestinfo.hostname" || { echo "FAIL -   ...the PXE VM"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "winapp01"][0]
evs = [type(e).__name__ for e in c.eventManager.QueryEvents(vim.event.EventFilterSpec(entity=vim.event.EventFilterSpec.ByEntity(entity=v, recursion="self")))]
gi = {o.key: o.value for o in (v.config.extraConfig or [])}
sys.exit(0 if v.resourcePool.parent.name == "DC0_H0" and v.runtime.host.name == "DC0_H0" and str(v.runtime.powerState) == "poweredOn"
         and not any("Customization" in e for e in evs) and gi.get("guestinfo.hostname") == "winapp01" else 1)
EOPY
tail -1 "$work/sn.jsonl" | grep -q 'MAC address: 00:' && echo "ok   -   ...the ticket has the MAC address for MECM" || { echo "FAIL -   ...MAC note"; fails=$((fails + 1)); }
inc4="$(newticket $'vm_name: winapp02\ntemplate: win2022-pxe')"
run "deploy from a ticket: sites set up, the ticket names none - refused" fail vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc4" -e "$sites" \
    -- "the ticket does not say which site"
# the 15-character limit is for Windows only: a Linux template that boots from its CD takes a longer name
inc4b="$(newticket $'vm_name: winapp-0123456789\ntemplate: win2022-pxe\nsite: siteb')"
run "a Windows network-boot VM with a 17-character name: refused by the dry run (the propose step)" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc4b" -e "$sites" -- "Windows computer names (and MECM's) have 15 characters at most"
# the same Windows template NOT listed as network boot: the message names both fixes (vm_deploy_pxe_templates, a Sysprep spec)
inc4d="$(newticket $'vm_name: winapp03\ntemplate: win2022-pxe\nsite: siteb')"
run "a Windows template not in vm_deploy_pxe_templates and no specification: the dry run names both fixes" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc4d" -e "$sites" -e '{"vm_deploy_pxe_templates": []}' \
    -- "is Windows and has no customization specification" "list it in vm_deploy_pxe_templates" \
       "vm_deploy_template_specs on the deploy template" "Windows specification"
inc4c="$(newticket $'vm_name: lnxcdboot-0123456\ntemplate: rhel9-gold\nsite: siteb')"
run "a Linux template that boots from its CD, 17-character name: the dry run passes" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc4c" -e "$sites" -e '{"vm_deploy_pxe_templates": ["*-pxe", "rhel9-*"]}' -- "lnxcdboot-0123456 ready to build in"
run "...and the build: no customization, its name in guestinfo.hostname" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc4c" -e "$sites" -e '{"vm_deploy_pxe_templates": ["*-pxe", "rhel9-*"]}' \
    -- "lnxcdboot-0123456 deployed - installs its OS, MAC 00:" "none: the template installs its operating system"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...lnxcdboot-0123456 has guestinfo.hostname and guestinfo.ticket" || { echo "FAIL -   ...the CD-boot VM's guestinfo"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "lnxcdboot-0123456"][0]
gi = {o.key: o.value for o in (v.config.extraConfig or [])}
sys.exit(0 if gi.get("guestinfo.hostname") == "lnxcdboot-0123456" and gi.get("guestinfo.ticket", "").startswith("INC") else 1)
EOPY
tail -1 "$work/sn.jsonl" | grep -q 'also in the VM setting guestinfo.hostname' && echo "ok   -   ...the ticket says the name is in guestinfo" || { echo "FAIL -   ...guestinfo note"; fails=$((fails + 1)); }
# ---- GenAI reads a request written in plain words (vm_deploy_genai; ACT is the fake, scripted) ---------------
export GENAI_KEY=test-key FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json"
genai='{"vm_deploy_genai": true, "vm_deploy_template_catalog": {"win2022-pxe": "Windows Server 2022 (MECM)", "rhel9-gold": "RHEL 9"},
        "vm_deploy_templates": ["rhel9-*", "win2022-*"], "vm_deploy_pxe_templates": ["*-pxe"], "vm_deploy_mem_target_pct": 95,
        "vm_deploy_sites": {"SiteA": {"clusters": ["DC0_C0", "DC0_H0"]}}, "vm_deploy_datastores": ["LocalDS_*"], "vm_deploy_max_disk_gb": 500}'
act_says() {  # act_says JSON-OF-THE-ANSWER: what the fake ACT answers next
    "$py" - "$work/act/act.json" "$1" <<'EOPY'
import json, sys
json.dump({"summary": "Here is the request.\nBEGIN_ACT_ANALYSIS\n" + sys.argv[2] + "\nEND_ACT_ANALYSIS"}, open(sys.argv[1], "w"))
EOPY
}
good='{"vm_name": {"value": "winapp05", "evidence": "called winapp05"}, "template": {"value": "win2022-pxe", "evidence": "Windows Server 2022"},
       "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"},
       "disks_gb": {"value": [120, 200], "evidence": "a 120 GB system disk plus a 200 GB data disk"}, "site": {"value": "SiteA", "evidence": "for SiteA"},
       "questions": []}'
chg5="$(newrec change_request '{"short_description": "Build", "description": "vm_name: chgapp05\ntemplate: rhel9-gold", "state": "-1", "approval": "approved"}')"
sct5="$(newrec sc_task "{\"short_description\": \"Please build the server of the change\", \"parent\": \"$chg5\", \"assignment_group\": \"Linux Operations\"}")"
run "GenAI on, a task whose change gives the name and template: no GenAI needed (the build step goes on)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$sct5" -e "$genai" -e "$actvars" -e '{"vm_deploy_sites": {}, "vm_deploy_phase": "build"}' \
    -- "vm_name: chgapp05   (from parent $chg5 description)" "template: rhel9-gold   (from parent $chg5 description)"
inc5="$(newticket 'Hi team, we need a new Windows Server 2022 box called winapp05 for SiteA with 2 GB of RAM, 2 CPUs and a 120 GB system disk plus a 200 GB data disk. Thanks, Jane')"
act_says "$good"
run "GenAI reads a request in plain words: a proposal on the ticket, checked and placed, waiting for a person - nothing built" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc5" -e "$genai" -e "$actvars" \
    -- "winapp05 ready to build in DC0_" "waiting for the approval" "vm_name: winapp05   (from GenAI proposal (this run))" "disks_gb: 120, 200"
grep -q '\[AAP\] GenAI proposal (ticket text sha256:' "$work/sn.jsonl" && tail -1 "$work/sn.jsonl" | grep -q '\[AAP\] Checked - ready to build' \
    && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"winapp05"' \
    && echo "ok   -   ...the proposal and where it would go are work notes; no VM yet" || { echo "FAIL -   ...proposal"; fails=$((fails + 1)); }
rm -f "$work/act/fake-act-stdin.txt"
act_says '{"vm_name": {"value": "evil01", "evidence": "x"}, "questions": []}'          # must not be asked again
run "the next run builds the proposal - GenAI not asked again - placed by the rules among the site's clusters" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc5" -e "$genai" -e "$actvars" \
    -- "winapp05 deployed - installs its OS" "cluster chosen: DC0_" "datastore chosen: LocalDS_0" "vm_name: winapp05   (from GenAI proposal"
[ ! -e "$work/act/fake-act-stdin.txt" ] && echo "ok   -   ...ACT was not called by the build run" || { echo "FAIL -   ...the build asked GenAI"; fails=$((fails + 1)); }
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...winapp05: 2 vCPU, 2048 MB, disks 120 + 200 GB (thin), on one of the site's clusters" || { echo "FAIL -   ...the GenAI-requested VM"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "winapp05"][0]
disks = sorted(d.capacityInKB // 1048576 for d in v.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk))
# the simulator's numbers are its own: only that the choice is one of the candidates is checked here (tests/test_vm_deploy.py: the math)
sys.exit(0 if v.config.hardware.numCPU == 2 and v.config.hardware.memoryMB == 2048 and disks == [120, 200]
         and v.resourcePool.parent.name in ("DC0_C0", "DC0_H0") else 1)
EOPY
inc6="$(newticket 'please build me a RHEL 9 server with 4 CPUs')"
act_says '{"vm_name": {"value": "rhelsrv01", "evidence": "a RHEL 9 server"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 4, "evidence": "4 CPUs"}, "questions": ["Which site: SiteA?"]}'
run "GenAI makes up a name: dropped; the questions go to the requester as a comment; nothing built (red: a workflow stops)" fail \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc6" -e "$genai" -e "$actvars" \
    -- "questions for the requester (nothing built)" "DROPPED (not in the ticket): vm_name 'rhelsrv01'" "QUESTION: Which site: SiteA?" \
       "the requester has to answer"
tail -1 "$work/sn.jsonl" | grep -q '"comments": "\[AAP\] To build this VM' && echo "ok   -   ...a comment the requester sees" || { echo "FAIL -   ...questions comment"; fails=$((fails + 1)); }
inc7="$(newticket 'New RHEL 9 server called lnxapp07 for SiteA please, 2 CPUs and 2 GB of RAM')"
act_says '{"vm_name": {"value": "lnxapp07", "evidence": "called lnxapp07"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"}, "site": {"value": "SiteA", "evidence": "for SiteA"}}'
run "GenAI proposal for lnxapp07" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc7" -e "$genai" -e "$actvars" -- "lnxapp07 ready to build"
"$py" - "$sn_port" "$inc7" <<'EOPY'
import base64, json, sys, urllib.request
h = {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
u = "http://127.0.0.1:%s/api/now/table/incident" % sys.argv[1]
sid = json.load(urllib.request.urlopen(urllib.request.Request(u + "?sysparm_query=number=" + sys.argv[2], headers=h)))["result"][0]["sys_id"]
urllib.request.urlopen(urllib.request.Request(u + "/" + sid, data=b'{"comments": "actually make it 4 CPUs"}', method="PATCH", headers=h))
EOPY
rm -f "$work/act/fake-act-stdin.txt"
run "the ticket changed after the proposal: void - GenAI reads it again, nothing built" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc7" \
    -e "$genai" -e "$actvars" -- "lnxapp07 ready to build" "waiting for the approval"
[ -e "$work/act/fake-act-stdin.txt" ] && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"lnxapp07"' \
    && echo "ok   -   ...asked again, not built" || { echo "FAIL -   ...stale proposal"; fails=$((fails + 1)); }
# the workflow: one launch - propose, the approval, build (the steps as AAP's workflow runs them)
inc8="$(newticket 'Please build a RHEL 9 server called lnxapp08 for SiteA, 2 CPUs, 2 GB of RAM')"
act_says '{"vm_name": {"value": "lnxapp08", "evidence": "called lnxapp08"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"}, "site": {"value": "SiteA", "evidence": "for SiteA"}}'
run "workflow step 1 (vm_deploy_phase: propose): GenAI proposes, checked and placed - nothing built" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc8" -e "$genai" -e "$actvars" -e vm_deploy_phase=propose -- "lnxapp08 ready to build in DC0_" "waiting for the approval"
rm -f "$work/act/fake-act-stdin.txt"
run "workflow step 3, after the approval (vm_deploy_phase: build): built from the posted proposal, GenAI not asked" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc8" -e "$genai" -e "$actvars" -e vm_deploy_phase=build -- "lnxapp08 deployed"
[ ! -e "$work/act/fake-act-stdin.txt" ] && VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"lnxapp08"' \
    && echo "ok   -   ...lnxapp08 exists; ACT was not called by the build step" || { echo "FAIL -   ...workflow build"; fails=$((fails + 1)); }
act_says_raw() {  # act_says_raw TEXT: the fake ACT answers exactly this text
    "$py" - "$work/act/act.json" "$1" <<'EOPY'
import json, sys
json.dump({"summary": sys.argv[2]}, open(sys.argv[1], "w"))
EOPY
}
inc13="$(newticket 'Please build a RHEL 9 server called lnxapp13 for SiteA, 2 CPUs, 2 GB of RAM')"
act_says_raw 'Sure. Here is what the ticket asks for:
```json
{"vm_name": {"value": "lnxapp13", "evidence": "called lnxapp13"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
 "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"}, "site": {"value": "SiteA", "evidence": "for SiteA"},
 "questions": [],}
```'
run "GenAI answers in a code block with a trailing comma (no markers): read all the same" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc13" -e "$genai" -e "$actvars" -e vm_deploy_phase=propose \
    -- "vm_name: lnxapp13   (from GenAI proposal (this run))" "lnxapp13 ready to build in DC0_"
inc14="$(newticket 'Please build a RHEL 9 server called lnxapp14 for SiteA')"
act_says_raw 'I am ready. What task would you like me to do?'
run "GenAI answers like a chat: nothing built, and the job shows what it answered" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc14" -e "$genai" -e "$actvars" -e vm_deploy_phase=propose \
    -- "GenAI did not read the ticket" "WHAT GENAI ANSWERED (the job could not read it): I am ready. What task would you like me to do?"
# lines give the name and template, plain words the rest - and the disks in words: GenAI is asked for what is open
inc9="$(newticket $'VM request\r\nvm_name: lnxapp09\r\ntemplate: rhel9-gold\r\nsite: SiteA\r\n2 cpus\r\n2gb of memory\r\n120gb disk\r\n50gb disk')"
act_says '{"vm_name": {"value": "lnxapp09", "evidence": "vm_name: lnxapp09"}, "template": {"value": "rhel9-gold", "evidence": "template: rhel9-gold"},
           "disks_gb": {"value": [120, 50], "evidence": "120gb disk 50gb disk"}, "questions": []}'
rm -f "$work/act/fake-act-stdin.txt"
run "lines plus plain words, two sizes for disk 1: GenAI asked for the disks (memory not read across a line break)" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc9" -e "$genai" -e "$actvars" -e '{"vm_deploy_plain_words": true}' -e vm_deploy_phase=propose \
    -- "disks_gb: 120, 50   (from GenAI proposal (this run))" "memory_gb: 2   (from the words '2gb of memory'" "lnxapp09 ready to build in DC0_"
[ -e "$work/act/fake-act-stdin.txt" ] && ! grep -q "more than one memory size" "$work/out.txt" \
    && echo "ok   -   ...ACT was asked; one memory size" || { echo "FAIL -   ...GenAI for the words' questions"; fails=$((fails + 1)); }
inc9="$(newticket 'Need a RHEL 9 server called lnxapp09 for SiteA')"
run "workflow build step on a ticket with no proposal: refused, nothing built (GenAI is never asked in the build step)" fail \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc9" -e "$genai" -e "$actvars" -e vm_deploy_phase=build -- "has no GenAI proposal"
inc10="$(newticket 'Please build a RHEL 9 server called lnxapp10 for SiteA, 2 CPUs, 2 GB of RAM')"
act_says '{"vm_name": {"value": "lnxapp10", "evidence": "called lnxapp10"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"}, "site": {"value": "SiteA", "evidence": "for SiteA"}}'
run "one run, no approval (vm_deploy_genai_approval: false): GenAI reads, AAP checks and builds" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc10" -e "$genai" -e "$actvars" -e vm_deploy_genai_approval=false -- "lnxapp10 deployed"
grep -q 'Building it now (no approval step' "$work/sn.jsonl" && echo "ok   -   ...the proposal note says it is built now" || { echo "FAIL -   ...auto note"; fails=$((fails + 1)); }
inc11="$(newticket $'vm_name: lnxapp11\ntemplate: rhel9-gold\nsite: SiteA\ncpu: 2\nmemory_gb: 2')"
run "workflow step 1 with key: value lines (no GenAI needed): checked and placed, nothing built" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc11" -e "$genai" -e "$actvars" -e vm_deploy_phase=propose -- "lnxapp11 ready to build in DC0_"
! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"lnxapp11"' && echo "ok   -   ...no VM before the approval" || { echo "FAIL -   ...built too early"; fails=$((fails + 1)); }
# name rules: the VM's name picks the cluster (the template is on DC0_C0; DC0_H0 is a standalone host)
rules='{"vm_deploy_templates": ["rhel9-*"], "vm_deploy_wait_ip": 3, "vm_deploy_wait_customization": 60,
        "vm_deploy_name_rules": [{"match": "*AB01*", "cluster": "DC0_H0"}, {"match": "*CD02*", "cluster": "DC0_C0"}]}'
inc12="$(newticket $'vm_name: xyab01web\ntemplate: rhel9-gold')"
run "a name rule: a name with AB01 in it goes to its cluster (DC0_H0)" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc12" \
    -e "$rules" -- "cluster: DC0_H0   (from name rule *AB01*)" "xyab01web deployed"
inc13="$(newticket $'vm_name: XYCD02WEB\ntemplate: rhel9-gold')"
run "...and a name with CD02 in it (upper case) to the other one (DC0_C0)" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc13" \
    -e "$rules" -- "cluster: DC0_C0   (from name rule *CD02*)" "XYCD02WEB deployed"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...each VM is on its rule's cluster" || { echo "FAIL -   ...name rule placement"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
vms = {x.name: x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view}
sys.exit(0 if vms["xyab01web"].resourcePool.parent.name == "DC0_H0" and vms["XYCD02WEB"].resourcePool.parent.name == "DC0_C0" else 1)
EOPY
# template names as people write them: another case (vCenter's exact name is found), a short name
inc14="$(newticket $'vm_name: casetest01\ntemplate: RHEL9-GOLD')"
run "a template written in another case: found by vCenter's exact name (dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc14" -e "$rules" -- "casetest01 ready to build in" "rhel9-gold"
inc15="$(newticket $'vm_name: aliastest01\ntemplate: Gold')"
run "a template by its short name (vm_deploy_template_aliases, dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc15" -e "$rules" -e '{"vm_deploy_template_aliases": {"gold": "rhel9-gold"}}' \
    -- "template: rhel9-gold" "short name 'Gold'" "aliastest01 ready to build in"
# ---- the deploy settings finder (read-only), over both vCenters: the second one has no template ----
run "deploy settings finder: clusters, templates, folders and the lines to paste, both vCenters" ok vm_deploy_inventory.yml \
    -e "{\"vmware_vcenters\": [\"$vc2\"]}" \
    -- "DATACENTER DC0   (vCenter 127.0.0.1)" "  CLUSTER DC0_C0: " "  STANDALONE HOST DC0_H0: " "  TEMPLATE rhel9-gold: " \
       "vm_deploy_templates: ['rhel9-gold'" "{match: '*CHANGE-ME*', cluster: 'DC0_C0', datacenter: 'DC0', vcenter: '127.0.0.1'}" \
       "LOOK AT: datacenter $vc2 / DC0 has no template" '"vm_deploy_inventory"'
before_vms="$(VMWARE_PORT=$port "$py" "$here/vcsim.py" state | "$py" -c "import json,sys; print(len(json.load(sys.stdin)))")"
run "deploy settings finder as a dry run (Check): the same, nothing changed" ok vm_deploy_inventory.yml --check -- "  TEMPLATE win2022-pxe: "
after_vms="$(VMWARE_PORT=$port "$py" "$here/vcsim.py" state | "$py" -c "import json,sys; print(len(json.load(sys.stdin)))")"
[ "$before_vms" = "$after_vms" ] && echo "ok   -   ...nothing created" || { echo "FAIL -   ...the finder changed vCenter"; fails=$((fails + 1)); }
"$py" - "$work/sn.jsonl" <<'EOPY' && echo "ok   - every note written on a ticket has real line breaks (no backslash-n as text)" || { echo "FAIL - a ticket note has a literal backslash-n"; fails=$((fails + 1)); }
import json, sys
bad, multi = [], 0
for l in open(sys.argv[1]):
    r = json.loads(l)
    for k, v in (r.get("body") or {}).items() if isinstance(r.get("body"), dict) else []:
        if isinstance(v, str):
            bad += [v[:120]] if "\\n" in v else []
            multi += "\n" in v
print("\n".join(bad))
sys.exit(0 if not bad and multi >= 5 else 1)
EOPY
unset GENAI_KEY FAKE_ACT_OUT FAKE_ACT_CONFIG
unset SN_HOST SN_USERNAME SN_PASSWORD
kill "$sn_pid" 2>/dev/null

# ---- health report: a third simulator with 2 datacenters, 2 clusters each and a standalone host each;
# HA on for DC0_C0 (admission control off), DC1_C0 in vCLS retreat mode, host DC0_C1_H1 disconnected ----
port3=$((port + 9))
"$engine" run -d --name "$name3" -p "127.0.0.1:$port3:8989" "$image" -l 0.0.0.0:8989 -api-version 8.0 \
    -dc 2 -cluster 2 -host 2 -standalone-host 1 -vm 1 >/dev/null || { echo "ABORT - could not start the third $image"; exit 1; }
VMWARE_PORT=$port3 "$py" "$here/vcsim.py" health || { echo "ABORT - vcsim health"; exit 1; }
n0="$(nmail)"
RUN_PORT=$port3 run "health report: every datacenter, cluster and host; retreat mode and a disconnected host critical, HA off a warning" \
    ok vm_health_report.yml -e "$mail" \
    -- "VMware health report: 2 critical, 3 warning(s) - 2 datacenter(s), 4 cluster(s), 10 host(s)" \
       "vSphere Cluster Services (vCLS) in retreat mode: DRS is not working" "config.vcls.clusters.domain-c" \
       "Disconnected from vCenter" "vSphere HA is off: the VMs of a failed host are not restarted" \
       "HA admission control is off" '"vm_health"' '"problems"' "emailed"
eml="$(lastmail)"; getpart html "$eml" > "$work/hl.html"; getpart plain "$eml" > "$work/hl.txt"
if [ "$(nmail)" = $((n0 + 1)) ] && subject "$eml" | grep -q "^\[AAP\] VMware health report: 2 critical, 3 warning(s)" \
   && grep -Eq "^CRITICAL +DC1_C0 +DC1 +2 of 2 +OFF +on +RETREAT MODE" "$work/hl.txt" \
   && grep -Eq "^CRITICAL +DC0_C1 +DC0 +1 of 2 \(1 not connected\) +OFF" "$work/hl.txt" \
   && grep -Eq "^OK +DC0_C0 +DC0 +2 of 2 +on, admission control off +on +normal" "$work/hl.txt" \
   && grep -Eq "^CRITICAL +DC0_C1_H1 +DC0_C1 +DC0 +disconnected" "$work/hl.txt" && grep -Eq "^OK +DC1_H0 +\(standalone\) +DC1 +connected" "$work/hl.txt" \
   && grep -q 'bgcolor="#b91c1c"[^>]*>RETREAT MODE<' "$work/hl.html" && grep -q 'bgcolor="#b45309"[^>]*>OFF<' "$work/hl.html" \
   && grep -q 'bgcolor="#fee2e2"' "$work/hl.html" && grep -q '>Needs attention' "$work/hl.html"; then
    echo "ok   -   ...the email: clusters with HA, DRS, vCLS coloured, every host by datacenter and cluster, a text copy"
else
    echo "FAIL -   ...health email"; head -60 "$work/hl.txt"; fails=$((fails + 1))
fi
RUN_PORT=$port3 run "health report with vm_health_require_ha: false - HA off is not a warning" ok vm_health_report.yml \
    -e vm_health_require_ha=false -- "VMware health report: 2 critical - 2 datacenter(s), 4 cluster(s), 10 host(s)"
RUN_PORT=$port3 run "health report of one datacenter (vmware_datacenter)" ok vm_health_report.yml -e vmware_datacenter=DC1 \
    -- "VMware health report: 1 critical, 2 warning(s) - 1 datacenter(s), 2 cluster(s), 5 host(s)" "of datacenter DC1"
RUN_PORT=$port3 run "health report with vm_health_fail: true: a critical problem fails the job, for workflows" fail vm_health_report.yml \
    -e vm_health_fail=true -- "2 critical problem(s): DC0_C1_H1, DC1_C0" "vm_health_fail: false makes this a report only"
RUN_PORT=$port3 run "health report as a dry run (Check): the same report" ok vm_health_report.yml --check \
    -- "VMware health report: 2 critical, 3 warning(s)"
RUN_PORT=$port3 run "health report of two vCenters with the same datacenter names: nothing mixed up" ok vm_health_report.yml \
    -e "{\"vmware_vcenters\": [\"$vc2\"]}" \
    -- "VMware health report: 2 critical, 4 warning(s) - 3 datacenter(s), 5 cluster(s), 14 host(s)" "$vc2 / DC0" "127.0.0.1 / DC1"

if [ "$fails" -eq 0 ]; then echo "ok   - all VMware scenarios passed"; else echo "$fails FAILED"; exit 1; fi
