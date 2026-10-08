#!/usr/bin/env bash
# The VMware playbooks (playbooks/vm_*.yml, roles/vmware_vm) against vcsim, VMware's vCenter
# simulator, in a container: restart / shut down (guest and hard, VMware Tools check), the AAP
# server and protected VMs refused, snapshots without memory (create, delete one, delete all),
# notes, VLAN (port group) change of one adapter leaving the others alone, the secure boot report,
# dry runs, duplicate and unknown names, no credential; the alarms report (posted events, a
# disconnected host) and its ACT analysis (a fake ACT: tests/containers/fake_act.py); the ESXi
# security settings; capacity planning (with and without GenAI's estimate).
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
port2=$((port + 7))
work="$(mktemp -d)"
smtp_pid=""
http_pid=""
cleanup() { "$engine" rm -f "$name" "$name2" >/dev/null 2>&1; [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$http_pid" ] && kill "$http_pid" 2>/dev/null; rm -rf "$work"; }
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
run "alarms report: host not connected, connection lost, failed logins grouped, VM and other events; green" ok vm_alarm_report.yml -e "$mail" \
    -- "VMware alarms report: no triggered alarms, 1 host(s) not connected, 1 host(s) lost the connection, 3 failed login(s) (events: last 24 h)" \
       "Host DC0_C0_H1 lost its connection to vCenter" "Guest OS shut down for DC0_H0_VM1" "Datastore LocalDS_0 latency is high" \
       '"hosts_not_connected": [   "DC0_C0_H2"' '"failed_logins"' "emailed"
eml="$(lastmail)"; getpart html "$eml" > "$work/al.html"; getpart plain "$eml" > "$work/al.txt"
if [ "$(nmail)" = $((n0 + 1)) ] && subject "$eml" | grep -q "^\[AAP\] VMware alarms report: no triggered alarms, 1 host(s) not connected" \
   && grep -q 'bgcolor="#fee2e2"' "$work/al.html" && grep -q 'bgcolor="#b91c1c"[^>]*>CRITICAL<' "$work/al.html" \
   && grep -q 'bgcolor="#fef3c7"' "$work/al.html" && grep -q 'bgcolor="#b45309"[^>]*>WARNING<' "$work/al.html" \
   && grep -q ">DC0_C0_H2<" "$work/al.html" && grep -q ">disconnected<" "$work/al.html" \
   && grep -Eq "^CRITICAL +DC0_C0_H2 +disconnected" "$work/al.txt" && grep -Eq "^WARNING +user +10.9.9.9 +DC0_C0_H0 +3 " "$work/al.txt"; then
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
    -- "vm_name: app01   (from description)" "cpu: 4   (from comment" "would be deployed in DC0_C0 (dry run)"
[ "$(sed -n "$((n0 + 1)),\$p" "$work/sn.jsonl" | grep -c '"PATCH"')" = 0 ] && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"app01"' \
    && echo "ok   -   ...no VM built, nothing written on the ticket" || { echo "FAIL -   ...the dry run built or wrote something"; fails=$((fails + 1)); }
run "deploy from a ticket: a template not allowed is refused, and the ticket says why" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc" -e "$rules" -e '{"vm_deploy_templates": ["win*"]}' -- "REFUSED: template 'rhel9-gold' is not in vm_deploy_templates"
tail -1 "$work/sn.jsonl" | grep -q 'VM not deployed' && echo "ok   -   ...the work note says why" || { echo "FAIL -   ...refusal note"; fails=$((fails + 1)); }
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
sys.exit(0 if i and j and i[-1] < j[-1] and "cpu: 4" in notes[i[-1]] and "Customization: succeeded" in notes[j[-1]] else 1)
EOPY
run "deploy from a ticket again: the name exists - refused, nothing built, the ticket says so" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc" -e "$rules" -- "a VM named app01 already exists in vCenter 127.0.0.1"
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
run "deploy from a ticket: not a ticket number" fail vm_deploy_from_ticket.yml -e vm_deploy_ticket=app01 -e "$rules" -- "Give the ticket number"
SN_HOST= run "deploy from a ticket without the ServiceNow credential: says which to attach" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$inc" -e "$rules" -- 'attach the \"ServiceNow API\" credential'
VMWARE_PORT=$port "$py" - <<'EOPY' || { echo "ABORT - PXE template"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
src = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM1"][0]
t = src.CloneVM_Task(folder=src.parent, name="win2022-pxe", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False, template=True))
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
EOPY
sites='{"vm_deploy_templates": ["rhel9-*", "win2022-*"], "vm_deploy_pxe_templates": ["*-pxe"], "vm_deploy_wait_ip": 3,
        "vm_deploy_sites": {"SiteA": {"cluster": "DC0_C0"}, "SiteB": {"cluster": "DC0_H0"}}}'
inc3="$(newticket $'vm_name: winapp01\ntemplate: win2022-pxe\nsite: siteb')"
run "deploy from a ticket: a network-boot (PXE / MECM) Windows template, its site picks the cluster; the ticket gets the MAC" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc3" -e "$sites" \
    -- "cluster: DC0_H0   (from site SiteB)" "winapp01 deployed - PXE boot, MAC 00:" "none: boots from the network (PXE)"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...winapp01 is on SiteB's host DC0_H0, powered on, not customized" || { echo "FAIL -   ...the PXE VM"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "winapp01"][0]
evs = [type(e).__name__ for e in c.eventManager.QueryEvents(vim.event.EventFilterSpec(entity=vim.event.EventFilterSpec.ByEntity(entity=v, recursion="self")))]
sys.exit(0 if v.resourcePool.parent.name == "DC0_H0" and v.runtime.host.name == "DC0_H0" and str(v.runtime.powerState) == "poweredOn" and not any("Customization" in e for e in evs) else 1)
EOPY
tail -1 "$work/sn.jsonl" | grep -q 'MAC address: 00:' && echo "ok   -   ...the ticket has the MAC address for MECM" || { echo "FAIL -   ...MAC note"; fails=$((fails + 1)); }
inc4="$(newticket $'vm_name: winapp02\ntemplate: win2022-pxe')"
run "deploy from a ticket: sites set up, the ticket names none - refused" fail vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc4" -e "$sites" \
    -- "the ticket does not say which site"
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
inc5="$(newticket 'Hi team, we need a new Windows Server 2022 box called winapp05 for SiteA with 2 GB of RAM, 2 CPUs and a 120 GB system disk plus a 200 GB data disk. Thanks, Jane')"
act_says "$good"
run "GenAI reads a request in plain words: a proposal on the ticket, each value with its words - nothing built" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc5" -e "$genai" -e "$actvars" \
    -- "GenAI proposal posted - run the job again to build it" "vm_name: winapp05" "disks_gb: 120, 200"
tail -1 "$work/sn.jsonl" | grep -q '\[AAP\] GenAI proposal (ticket text sha256:' && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"winapp05"' \
    && echo "ok   -   ...the proposal is a work note; no VM yet" || { echo "FAIL -   ...proposal"; fails=$((fails + 1)); }
rm -f "$work/act/fake-act-stdin.txt"
act_says '{"vm_name": {"value": "evil01", "evidence": "x"}, "questions": []}'          # must not be asked again
run "the next run builds the proposal - GenAI not asked again - placed by the rules among the site's clusters" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc5" -e "$genai" -e "$actvars" \
    -- "winapp05 deployed - PXE boot" "cluster chosen: DC0_" "datastore chosen: LocalDS_0" "vm_name: winapp05   (from GenAI proposal"
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
run "GenAI makes up a name: dropped; the questions go to the requester as a comment; nothing built" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc6" -e "$genai" -e "$actvars" \
    -- "questions for the requester (nothing built)" "DROPPED (not in the ticket): vm_name 'rhelsrv01'" "QUESTION: Which site: SiteA?"
tail -1 "$work/sn.jsonl" | grep -q '"comments": "\[AAP\] To build this VM' && echo "ok   -   ...a comment the requester sees" || { echo "FAIL -   ...questions comment"; fails=$((fails + 1)); }
inc7="$(newticket 'New RHEL 9 server called lnxapp07 for SiteA please, 2 CPUs and 2 GB of RAM')"
act_says '{"vm_name": {"value": "lnxapp07", "evidence": "called lnxapp07"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"}, "site": {"value": "SiteA", "evidence": "for SiteA"}}'
run "GenAI proposal for lnxapp07" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc7" -e "$genai" -e "$actvars" -- "GenAI proposal posted"
"$py" - "$sn_port" "$inc7" <<'EOPY'
import base64, json, sys, urllib.request
h = {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
u = "http://127.0.0.1:%s/api/now/table/incident" % sys.argv[1]
sid = json.load(urllib.request.urlopen(urllib.request.Request(u + "?sysparm_query=number=" + sys.argv[2], headers=h)))["result"][0]["sys_id"]
urllib.request.urlopen(urllib.request.Request(u + "/" + sid, data=b'{"comments": "actually make it 4 CPUs"}', method="PATCH", headers=h))
EOPY
rm -f "$work/act/fake-act-stdin.txt"
run "the ticket changed after the proposal: void - GenAI reads it again, nothing built" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$inc7" \
    -e "$genai" -e "$actvars" -- "GenAI proposal posted - run the job again to build it"
[ -e "$work/act/fake-act-stdin.txt" ] && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"lnxapp07"' \
    && echo "ok   -   ...asked again, not built" || { echo "FAIL -   ...stale proposal"; fails=$((fails + 1)); }
unset GENAI_KEY FAKE_ACT_OUT FAKE_ACT_CONFIG
unset SN_HOST SN_USERNAME SN_PASSWORD
kill "$sn_pid" 2>/dev/null

if [ "$fails" -eq 0 ]; then echo "ok   - all VMware scenarios passed"; else echo "$fails FAILED"; exit 1; fi
