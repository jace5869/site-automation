#!/usr/bin/env bash
# The VMware playbooks (playbooks/vm_*.yml, roles/vmware_vm) against vcsim, VMware's vCenter
# simulator, in a container: restart / shut down (guest and hard, VMware Tools check), the AAP
# server and protected VMs refused, snapshots without memory (create, delete one, delete all),
# notes, VLAN (port group) change of one adapter leaving the others alone, the secure boot report,
# dry runs, duplicate and unknown names, no credential; the alarms report (posted events, a
# disconnected host, an alarm that fired and cleared) and its ACT analysis (a fake ACT:
# tests/containers/fake_act.py); the ESXi security settings; capacity planning (with and without
# GenAI's estimate); the health report (a third simulator with 2 datacenters; its network drops); the certificate renewal of vCenter
# and its hosts (the days raised, one host, incidents and CSRs in a fake ServiceNow, no second CSR, a refused attachment);
# the pick lists (the survey's choices written in a fake AAP, the picked snapshots deleted and VMs restarted, each checked again);
# the outage watch (hung VMs by heartbeat and ping, protected ones, HA and storage events, a disconnected host, its
# incidents opened and resolved in a fake ServiceNow);
# the vCenter log analysis (vcsim hands out no logs: the job says so).
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
pick_pid=""
cleanup() { "$engine" rm -f "$name" "$name2" "$name3" >/dev/null 2>&1; [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$http_pid" ] && kill "$http_pid" 2>/dev/null
            [ -n "$pick_pid" ] && kill "$pick_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0

"$py" -c 'import pyVmomi, vmware.vapi' 2>/dev/null || { echo "ABORT - $py has no pyVmomi / vSphere Automation SDK (pip install pyvmomi vmware-vcenter)"; exit 1; }
cols="${VMWARE_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$here/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install vmware.vmware"; exit 1; }
fi
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    aap:
      hosts:
        aap01.example.mil: {ansible_host: 192.0.2.5}
EOI
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv-noaap.yml"
# the AAP host under another name (only its IP matches the VM), and by name only (no ansible_host)
sed 's/aap01.example.mil: {ansible_host: 192.0.2.5}/aapnode.example.mil: {ansible_host: 192.0.2.5}/' "$work/inv.yml" > "$work/inv-ip.yml"
sed 's/aap01.example.mil: {ansible_host: 192.0.2.5}/AAP01: {}/' "$work/inv.yml" > "$work/inv-name.yml"

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
       "  DC0_H0_VM0  folder /DC0/vm  on DC0_H0  guest aap01.example.mil 192.0.2.5  (poweredOn" "  dup  folder /DC0/vm/other" \
       "6 VM(s) (templates not counted): 0 EFI with secure boot, 2 EFI with secure boot OFF, 4 BIOS"
run "secure boot report with vm_secure_boot_fail: true: the job fails, for workflows" fail vm_secure_boot_report.yml -e vm_secure_boot_fail=true -- "EFI with secure boot OFF" "vm_secure_boot_fail: false makes this a report only"
grep -q '"secure_boot_off": \[' "$work/out.txt" && grep -A3 '"secure_boot_off"' "$work/out.txt" | grep -q '"name": "DC0_C0_RP0_VM0"' \
    && grep -q '"vms": 6\b' "$work/out.txt" && grep -q '"secure_boot_on": 0\b' "$work/out.txt" && grep -q '"folder": "/DC0/vm/other"' "$work/out.txt" \
    && grep -q '"vm_report"' "$work/out.txt" \
    && echo "ok   -   ...the lists are job artifacts (vm_secure_boot, vm_report)" \
    || { echo "FAIL -   ...artifacts"; sed -n '/CUSTOM STATS/,$p' "$work/out.txt" | head -30; fails=$((fails + 1)); }
run "secure boot report for named VMs only" ok vm_secure_boot_report.yml -e vm_names=DC0_H0_VM1 \
    -- "1 VM(s) (templates not counted): 0 EFI with secure boot, 1 EFI with secure boot OFF, 0 BIOS" "  DC0_H0_VM1  folder /DC0/vm  on DC0_H0  guest web01.example.mil 192.0.2.20"

# ---- snapshot report and cleanup (DC0_C0_RP0_VM1: baseline, multi; DC0_H0_VM0 = AAP: aap-snap; "later" = 7 days on) --
later=$(( $(date +%s) + 7 * 86400 ))
run "snapshot report: nothing old yet" ok vm_snapshot_report.yml -- "VMware snapshot report (older than 5 days): nothing to delete; 3 snapshot(s) on 2 VM(s)"
run "a snapshot to keep (its name says so)" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=keep-me -- "Took snapshot"
run "snapshot report seven days later: to delete apart, held back apart with the reason, sizes readable" ok vm_snapshot_report.yml -e _vm_now=$later \
    -- "VMware snapshot report (older than 5 days): 2 to delete (0 MB), 2 held back; 4 snapshot(s) on 2 VM(s)" \
       "HELD BACK (the VM is the AAP server) DC0_H0_VM0" "HELD BACK (its name or description says keep) DC0_C0_RP0_VM1" \
       "OLD DC0_C0_RP0_VM1" "days old, 0 MB, taken" "jdoe" '"vm_snapshot_cleanup_candidates"' '"held_back"'
run "exclusion list (wildcards): the VM's snapshots are held back" ok vm_snapshot_report.yml -e _vm_now=$later \
    -e '{"vm_snapshot_cleanup_exclude_vms": ["dc0_c0_*"]}' -- "nothing to delete, 4 held back; 4 snapshot(s) on 2 VM(s)" \
       "HELD BACK (the VM is on the exclusion list) DC0_C0_RP0_VM1"
run "datacenter exclusion (any case): its VMs' snapshots are held back" ok vm_snapshot_report.yml -e _vm_now=$later \
    -e '{"vm_snapshot_cleanup_exclude_datacenters": ["dc0"]}' -- "nothing to delete, 4 held back" \
       "HELD BACK (its datacenter DC0 is on the exclusion list) DC0_C0_RP0_VM1"
run "cluster exclusion (survey text, wildcards): the snapshots of its VMs are held back" ok vm_snapshot_report.yml -e _vm_now=$later \
    -e '{"vm_snapshot_cleanup_exclude_clusters": "DC0_C*"}' -- "HELD BACK (its cluster DC0_C0 is on the exclusion list) DC0_C0_RP0_VM1"
run "vmware_protected_vms is honoured too" ok vm_snapshot_report.yml -e _vm_now=$later -e '{"vmware_protected_vms": ["DC0_C0_RP0_VM?"]}' \
    -- "HELD BACK (the VM is on the exclusion list) DC0_C0_RP0_VM1"
run "a 1-day limit two days on: the heading says 1 day, every snapshot is old" ok vm_snapshot_report.yml -e _vm_now=$(( $(date +%s) + 2 * 86400 )) \
    -e vm_snapshot_max_age_days=1 -- "VMware snapshot report (older than 1 day): 2 to delete (0 MB), 2 held back; 4 snapshot(s)" \
       "OLD = older than 1 day"
run "a limit that is not a number: refused before anything is read as old" fail vm_snapshot_report.yml -e vm_snapshot_max_age_days=three \
    -- "vm_snapshot_max_age_days must be a number of days"
run "size limit: bigger than vm_snapshot_cleanup_max_size_gb is held back" ok vm_snapshot_report.yml -e _vm_now=$later -e vm_snapshot_cleanup_max_size_gb=-1 \
    -- "HELD BACK (bigger than"
# vm_snapshot_cleanup_name_words (read-only here; the deleting run is after the cleanup checks below)
run "name words: only a name with one of them is to delete (any case), the others held back with the reason" ok vm_snapshot_report.yml \
    -e _vm_now=$later -e '{"vm_snapshot_cleanup_name_words": ["MULTI"]}' -- "1 to delete (0 MB), 3 held back; 4 snapshot(s) on 2 VM(s)" \
       "HELD BACK (its name has none of: MULTI) DC0_C0_RP0_VM1" "OLD DC0_C0_RP0_VM1" "OLD = older than 5 days whose name has MULTI"
run "name words as survey text (commas): the cleanup names them, lists, deletes nothing" ok vm_snapshot_cleanup.yml -e _vm_now=$later \
    -e '{"vm_snapshot_cleanup_name_words": "Patching, multi"}' \
    -- "would delete 1 snapshot(s) older than 5 days whose name has Patching or multi (nothing deleted)" 'WOULD DELETE DC0_C0_RP0_VM1: \"multi\"' \
       'HELD BACK DC0_C0_RP0_VM1: \"baseline\" 0 MB - its name has none of: Patching, multi'
run "name words, confirmed as a dry run (Check): deletes nothing" ok vm_snapshot_cleanup.yml --check -e _vm_now=$later \
    -e vm_snapshot_cleanup_confirm=true -e '{"vm_snapshot_cleanup_name_words": ["Patch"]}' \
    -- "would delete 0 snapshot(s) older than 5 days whose name has Patch (nothing deleted)" "Nothing deleted: a dry run (Check)." \
       'HELD BACK DC0_C0_RP0_VM1: \"multi\" 0 MB - its name has none of: Patch'
check "  ...still three" DC0_C0_RP0_VM1 "len(v['snapshots'])" 3
run "cleanup on its own, not confirmed: lists, deletes nothing, green" ok vm_snapshot_cleanup.yml -e _vm_now=$later \
    -- "would delete 2 snapshot(s) older than 5 days (nothing deleted)" "WOULD DELETE DC0_C0_RP0_VM1" "HELD BACK DC0_H0_VM0"
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
           {\"vm\": \"DC0_H0_VM0\", \"moid\": \"$aapmoid\", \"id\": $aapsnap, \"name\": \"aap-snap\"}], \"vm_snapshot_cleanup_candidates_workflow\": \"90\"}"
run "cleanup with the list of another workflow: refused, nothing deleted" fail vm_snapshot_cleanup.yml -e _vm_now=$later \
    -e "$approved" -e awx_workflow_job_id=91 -- "did not come from this workflow's own plan step" "Nothing was deleted"
run "cleanup with a list typed in, outside a workflow: refused" fail vm_snapshot_cleanup.yml -e _vm_now=$later \
    -e '{"vm_snapshot_cleanup_candidates": [{"vm": "DC0_C0_RP0_VM1", "moid": "x", "id": 1, "name": "multi"}]}' -- "this is no workflow"
run "cleanup after the approval: only the approved snapshot - never the AAP server's, even if listed" ok vm_snapshot_cleanup.yml -e _vm_now=$later \
    -e "$approved" -e awx_workflow_job_id=90 -- "deleted 1 snapshot(s) older than 5 days" 'DELETED DC0_C0_RP0_VM1: \"multi\"' "NOT DELETED gone01" "NOT DELETED DC0_H0_VM0"
check "  ...baseline (old, not approved) and keep-me remain" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "baseline,keep-me"
check "  ...the AAP server's snapshot remains" DC0_H0_VM0 "len(v['snapshots'])" 1
run "cleanup confirmed: deletes the old one, never keep-me or the AAP server's" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -- "deleted 1 snapshot(s)" 'DELETED DC0_C0_RP0_VM1: \"baseline\"'
check "  ...only keep-me remains" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me"
check "  ...and aap-snap" DC0_H0_VM0 "len(v['snapshots'])" 1
run "a patching snapshot" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=Pre-Patching-Oct -- "Took snapshot"
run "and one that is not" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=app-upgrade -- "Took snapshot"
run "cleanup confirmed with name words: deletes the patching one only" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e vm_snapshot_cleanup_confirm=true \
    -e '{"vm_snapshot_cleanup_name_words": ["Patching", "Patches", "Patch"]}' \
    -- "deleted 1 snapshot(s) older than 5 days whose name has Patching, Patches or Patch" 'DELETED DC0_C0_RP0_VM1: \"Pre-Patching-Oct\"' \
       'HELD BACK DC0_C0_RP0_VM1: \"app-upgrade\" 0 MB - its name has none of: Patching, Patches, Patch'
check "  ...keep-me and app-upgrade remain" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me,app-upgrade"
run "  (app-upgrade deleted again, for the checks below)" ok vm_snapshot_delete.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=app-upgrade \
    -- "DC0_C0_RP0_VM1: deleted app-upgrade"

# ---- pick lists: the survey lists what may be restarted / deleted, the pick is checked again live (a fake AAP:
# tests/servicenow/aapmock.py - job templates 51/52, workflow 61; DC0_H0_VM0 = AAP, keep-me is held back) ----------
pick_port=$((port + 45))
AAPMOCK_LOG="$work/pick-aap.jsonl" AAPMOCK_INV_LOG="$work/pick-inv.jsonl" AAPMOCK_SURVEY_LOG="$work/survey.jsonl" \
    "$py" "$repo/tests/servicenow/aapmock.py" "$pick_port" >/dev/null 2>&1 &
pick_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$pick_port") 2>/dev/null && break; sleep 0.1; done
export CONTROLLER_HOST="http://127.0.0.1:$pick_port" CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=goodpw
nsurvey() { cat "$work/survey.jsonl" 2>/dev/null | wc -l; }
# choices KIND ID [N]: the choices of the last survey written for that template, one per line ([N] = only the Nth, from 0)
choices() {
    "$py" -c 'import json, sys
rows = [json.loads(x) for x in open(sys.argv[1])]
q = [r for r in rows if r["kind"] == sys.argv[2] and r["id"] == int(sys.argv[3])][-1]["spec"]["spec"][0]
c = q["choices"] if isinstance(q["choices"], list) else q["choices"].split("\n")
print("\n".join(c) if len(sys.argv) < 5 else c[int(sys.argv[4])])' "$work/survey.jsonl" "$@"
}
picklist='{"vm_pick_snapshot_templates": ["VM - delete picked snapshots", {"template": "61", "kind": "workflow"}],
           "vm_pick_reboot_templates": ["VM - restart picked VMs"]}'
# (on DC0_C0_RP0_VM1, which keeps keep-me: vcsim cannot take a snapshot of a VM whose snapshots were all deleted)
run "pick lists: two snapshots of the same name, and a third" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=pick-a -- "Took snapshot"
VMWARE_PORT=$port "$py" "$here/vcsim.py" snap DC0_C0_RP0_VM1 pick-a
run "  (pick-b)" ok vm_snapshot.yml -e vm_names=DC0_C0_RP0_VM1 -e vm_snapshot_name=pick-b -- "Took snapshot"
run "pick lists, a dry run (Check): says what it would write, writes nothing" ok vm_pick_refresh.yml --check -e "$picklist" \
    -- "VMware pick lists (dry run): 3 snapshot(s) to delete, 2 VM(s) to restart" \
       "job template VM - delete picked snapshots, question vm_snapshot_picks: 3 choice(s) - would change: 3 choice(s), default cleared" \
       "workflow job template VM - delete picked snapshots (workflow), question vm_snapshot_picks: 3 choice(s)" \
       "job template VM - restart picked VMs, question vm_reboot_picks: 2 choice(s) - would change: made multiple select"
[ "$(nsurvey)" = 0 ] && echo "ok   -   ...no survey written" || { echo "FAIL -   ...the dry run wrote a survey"; fails=$((fails + 1)); }
run "pick lists: the snapshots that may be deleted and the VMs that may be restarted, written into the surveys" ok vm_pick_refresh.yml -e "$picklist" \
    -- "VMware pick lists: 3 snapshot(s) to delete, 2 VM(s) to restart" \
       "Snapshots: 3 offered, oldest first; 2 held back: 1 its name or description says keep, 1 the VM is the AAP server" \
       'DC0_C0_RP0_VM1: \"pick-a\" (0 MB, 0.0 days old, taken ' \
       "VMs: 2 offered; 1 held back: 1 the AAP server or a protected VM (vmware_protected_groups); 3 powered off or templates" \
       "DC0_H0_VM1 (VMware Tools running, DC0, guest web01.example.mil 192.0.2.20) [127.0.0.1/" \
       "DC0_C0_RP0_VM0 (no VMware Tools running, DC0 / DC0_C0) [127.0.0.1/" "No email: the pick list refresh never emails" '"vm_pick_lists"'
[ "$(nsurvey)" = 3 ] && [ "$(choices job 51 | grep -c '\[127.0.0.1/vm-[0-9]*/[0-9]*\]$')" = 3 ] && [ "$(choices workflow 61 | grep -c pick-)" = 3 ] \
    && [ "$(choices job 52 | grep -c '\[127.0.0.1/vm-[0-9]*\]$')" = 2 ] && ! choices job 52 | grep -q DC0_H0_VM0 \
    && echo "ok   -   ...3 surveys: each choice ends with its vCenter/VM/snapshot, the AAP server never offered" \
    || { echo "FAIL -   ...surveys"; cat "$work/survey.jsonl" | cut -c1-400; fails=$((fails + 1)); }
run "pick lists again, nothing new: no choice changed (at most the time of the list)" ok vm_pick_refresh.yml -e "$picklist" \
    -- "question vm_snapshot_picks: 3 choice(s) - " && ! grep -q "updated: 3 choice" "$work/out.txt" && echo "ok   -   ...the same choices" \
    || { echo "FAIL -   ...the choices were written again"; fails=$((fails + 1)); }
run "pick lists: a template that is not there - the others are still written, the job fails and says so" fail vm_pick_refresh.yml \
    -e '{"vm_pick_snapshot_templates": ["Nope", "VM - delete picked snapshots"]}' \
    -- "NOT UPDATED template Nope (question vm_snapshot_picks): no job template or workflow job template Nope that the AAP user can see" \
       "job template VM - delete picked snapshots, question vm_snapshot_picks: 3 choice(s) - "
run "pick lists: no template named - says which settings" fail vm_pick_refresh.yml -- "Nothing to refresh: name the templates"
(unset CONTROLLER_HOST; run "pick lists: no AAP credential - says which credential" fail vm_pick_refresh.yml -e "$picklist" \
    -- 'No AAP connection: attach a credential of type \"Red Hat Ansible Automation Platform\"') || fails=$((fails + 1))
INV="$work/inv-noaap.yml" run "pick lists: the inventory cannot tell which VM is AAP - no VM is offered" fail vm_pick_refresh.yml -e "$picklist" \
    -- "cannot tell which VM is the AAP server"
hb0="$(VMWARE_PORT=$port "$py" -c 'from pyVim.connect import SmartConnect; from pyVmomi import vim
c = SmartConnect(host="127.0.0.1", port='"$port"', user="u", pwd="p", disableSslCertValidation=True).RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "DC0_H0_VM1"][0]
print(v.guest.toolsStatus)')"
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_H0_VM1 guestHeartbeatStatus=red guest.toolsStatus=toolsOk
run "pick lists, only the hung VMs: Tools installed but no heartbeat" ok vm_pick_refresh.yml -e "$picklist" -e vm_pick_reboot_only_hung=true \
    -- "1 hung VM(s)" "VMs: 1 offered (only the hung ones); 2 held back: 1 not hung, 1 the AAP server" "DC0_H0_VM1 (no heartbeat, DC0, guest "
[ "$(choices job 52)" != "" ] && [ "$(choices job 52 | wc -l)" = 1 ] && echo "ok   -   ...the restart list has the hung VM only" \
    || { echo "FAIL -   ...hung list"; choices job 52; fails=$((fails + 1)); }

# the snapshot picks: from the job template's list (3 choices); a typed line and a stale one too
sp="$work/snap-picks.json"
"$py" -c 'import json, sys; c = sys.stdin.read().splitlines(); json.dump({"vm_snapshot_picks": c + ["web01 typed by hand",
    "DC0_C0_RP0_VM1: \"gone\" (x) [127.0.0.1/vm-999/9]"]}, open(sys.argv[1], "w"))' "$sp" <<< "$(choices job 51)"
run "picked snapshots, on their own: the plan only - what would be deleted, the stale and typed ones skipped, nothing deleted" ok \
    vm_snapshot_delete_picked.yml -e "@$sp" \
    -- "VMware picked snapshots: would delete 3, 2 skipped (nothing deleted)" 'WOULD DELETE DC0_C0_RP0_VM1: \"pick-a\"' 'WOULD DELETE DC0_C0_RP0_VM1: \"pick-b\"' \
       "SKIPPED web01 typed by hand - not a choice of the pick list" 'SKIPPED DC0_C0_RP0_VM1: \"gone\" - no longer there' \
       "run again with vm_pick_confirm: true" '"vm_snapshot_pick_plan"'
check "  ...all three still there" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me,pick-a,pick-a,pick-b"
run "picked snapshots in a workflow, without a plan: vm_pick_confirm is not used - the plan for the approval" ok vm_snapshot_delete_picked.yml \
    -e "@$sp" -e vm_pick_confirm=true -e awx_workflow_job_id=77 -- "would delete 3 - waiting for the approval" "the plan for the approval"
check "  ...still there" DC0_C0_RP0_VM1 "len(v['snapshots'])" 4
run "picked snapshots: more than vm_pick_max - refused" fail vm_snapshot_delete_picked.yml -e "@$sp" -e vm_pick_confirm=true -e vm_pick_max=2 \
    -- "Refused: 3 snapshots picked - more than vm_pick_max (2)"
run "picked snapshots, confirmed as a dry run (Check): deletes nothing" ok vm_snapshot_delete_picked.yml --check -e "@$sp" -e vm_pick_confirm=true \
    -- "would delete 3" "Nothing deleted: a dry run (Check)."
check "  ...still there" DC0_C0_RP0_VM1 "len(v['snapshots'])" 4
run "picked snapshots: nothing picked - says so" fail vm_snapshot_delete_picked.yml -- "Nothing picked: pick the snapshots to delete in the survey"
run "picked snapshots: only the empty list's line - says the list had nothing" fail vm_snapshot_delete_picked.yml \
    -e '{"vm_snapshot_picks": ["(nothing to pick: no snapshot may be deleted - list of x)"]}' -- "The list had nothing to pick"
renamed="$(choices job 51 0 | sed 's/: "pick-[ab]"/: "renamed"/')"
run "picked snapshots: one renamed since the list was made - skipped, nothing to delete: failed" fail vm_snapshot_delete_picked.yml \
    -e "{\"vm_snapshot_picks\": [\"${renamed//\"/\\\"}\"]}" -e vm_pick_confirm=true -- "the snapshot was renamed since the list was made" \
       "None of the picks can be deleted now"
pa1="$(state DC0_C0_RP0_VM1 "[s['id'] for s in v['snapshots'] if s['name'] == 'pick-a'][0]")"
pa2="$(state DC0_C0_RP0_VM1 "[s['id'] for s in v['snapshots'] if s['name'] == 'pick-a'][1]")"; m1="$(state DC0_C0_RP0_VM1 "v['moid']")"
aapmoid="$(state DC0_H0_VM0 "v['moid']")"; aapsnap="$(state DC0_H0_VM0 "v['snapshots'][0]['id']")"
plan="{\"vm_snapshot_pick_plan\": [{\"vm\": \"DC0_C0_RP0_VM1\", \"name\": \"pick-a\", \"vcenter\": \"127.0.0.1\", \"moid\": \"$m1\", \"id\": $pa1},
       {\"vm\": \"DC0_H0_VM0\", \"name\": \"aap-snap\", \"vcenter\": \"127.0.0.1\", \"moid\": \"$aapmoid\", \"id\": $aapsnap}],
       \"vm_snapshot_pick_plan_workflow\": \"77\"}"
run "picked snapshots after the approval: exactly the plan - one pick-a (not its namesake), never the AAP server's" ok vm_snapshot_delete_picked.yml \
    -e "$plan" -e "@$sp" -e awx_workflow_job_id=77 \
    -- "VMware picked snapshots: deleted 1 (approved), 1 skipped" 'DELETED DC0_C0_RP0_VM1: \"pick-a\"' \
       'SKIPPED DC0_H0_VM0: \"aap-snap\" - held back now: the VM is the AAP server'
check "  ...the other pick-a is still there" DC0_C0_RP0_VM1 "[s['id'] for s in v['snapshots'] if s['name'] == 'pick-a'] == [$pa2]" "True"
check "  ...and aap-snap" DC0_H0_VM0 "len(v['snapshots'])" 1
run "picked snapshots, confirmed on their own: the rest deleted, the one already gone skipped" ok vm_snapshot_delete_picked.yml -e "@$sp" -e vm_pick_confirm=true \
    -- "VMware picked snapshots: deleted 2, 3 skipped" 'DELETED DC0_C0_RP0_VM1: \"pick-b\"' '"vm_snapshot_picked"'
check "  ...only keep-me left" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me"

# the restart picks (DC0_H0_VM1 is hung now: Tools installed, no heartbeat; DC0_C0_RP0_VM0 runs no Tools)
rp="$work/vm-picks.json"
run "  (the restart list, every powered-on VM)" ok vm_pick_refresh.yml -e "$picklist" -- "2 VM(s) to restart"
"$py" -c 'import json, sys; json.dump({"vm_reboot_picks": sys.stdin.read().splitlines()}, open(sys.argv[1], "w"))' "$rp" <<< "$(choices job 52 | grep -v DC0_C0_RP0_VM1)"
run "picked VMs, a guest restart: the ones without VMware Tools running skipped, saying to use hard" ok vm_reboot_picked.yml -e "@$rp" \
    -- "VM restart (picked): would restart 1 (guest), 1 skipped (nothing restarted)" "WOULD RESTART (operating system restart through VMware Tools) DC0_H0_VM1" \
       "SKIPPED DC0_C0_RP0_VM0 - VMware Tools is not running in it" "with vm_power_mode hard"
run "picked VMs: the AAP server's ID typed in - held back, nothing to restart: failed" fail vm_reboot_picked.yml -e vm_power_mode=hard -e vm_pick_confirm=true \
    -e "{\"vm_reboot_picks\": [\"DC0_H0_VM0 (x) [127.0.0.1/$aapmoid]\"]}" \
    -- "SKIPPED DC0_H0_VM0 - held back now: the AAP server or a protected VM" "None of the picks can be restarted now"
check "  ...the AAP server still on" DC0_H0_VM0 "v['power']" poweredOn
run "picked VMs, a hard reset, confirmed on their own" ok vm_reboot_picked.yml -e "@$rp" -e vm_power_mode=hard -e vm_pick_confirm=true \
    -- "VM restart (picked): 2 restarted (hard)" "RESTARTED (hard reset) DC0_H0_VM1" "RESTARTED (hard reset) DC0_C0_RP0_VM0" '"vm_reboot_picked"'
m0="$(state DC0_C0_RP0_VM0 "v['moid']")"
rplan="{\"vm_reboot_pick_plan\": [{\"vm\": \"DC0_C0_RP0_VM0\", \"vcenter\": \"127.0.0.1\", \"moid\": \"$m0\", \"power_mode\": \"hard\"}],
        \"vm_reboot_pick_plan_workflow\": \"78\"}"
run "picked VMs, a plan from another workflow: refused" fail vm_reboot_picked.yml -e "$rplan" -e vm_power_mode=hard -e awx_workflow_job_id=79 \
    -- "did not come from this workflow's own plan step" "Nothing was restarted"
run "picked VMs after the approval, but run with another power mode: refused" fail vm_reboot_picked.yml -e "$rplan" -e vm_power_mode=guest -e awx_workflow_job_id=78 \
    -- "Refused: the approved plan is for a hard restart, this run says vm_power_mode guest"
run "picked VMs after the approval: exactly the plan" ok vm_reboot_picked.yml -e "$rplan" -e vm_power_mode=hard -e awx_workflow_job_id=78 \
    -- "VM restart (picked): 1 restarted (hard) (approved)" "RESTARTED (hard reset) DC0_C0_RP0_VM0"
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_H0_VM1 guestHeartbeatStatus=green guest.toolsStatus="$hb0" guest.toolsRunningStatus=guestToolsRunning
run "picked VMs, only the hung ones: DC0_H0_VM1 has its heartbeat back - skipped" fail vm_reboot_picked.yml -e "@$rp" -e vm_power_mode=hard \
    -e vm_pick_reboot_only_hung=true -e vm_pick_confirm=true -- "SKIPPED DC0_H0_VM1 - held back now: not hung: heartbeat green"
run "picked VMs: one that is off now - skipped" fail vm_reboot_picked.yml -e vm_pick_confirm=true -e vm_power_mode=hard \
    -e "{\"vm_reboot_picks\": [\"DC0_C0_RP0_VM1 (x) [127.0.0.1/$(state DC0_C0_RP0_VM1 "v['moid']")]\"]}" -- "not offered now: gone, powered off"
INV="$work/inv-noaap.yml" run "picked VMs: the inventory cannot tell which VM is AAP - refused" fail vm_reboot_picked.yml -e "@$rp" -e vm_pick_confirm=true \
    -- "cannot tell which VM is the AAP server"
kill "$pick_pid" 2>/dev/null
unset CONTROLLER_HOST CONTROLLER_USERNAME CONTROLLER_PASSWORD

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
   && grep -Eq "^DC0_H0_VM0 +/DC0/vm +DC0_H0 +aap01.example.mil +192.0.2.5 +poweredOn +otherGuest$" "$body" \
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
getpart html "$eml4" | grep -q ">keep-me<" && grep -q "Subject: \[AAP\] VMware snapshot report (older than 5 days): " "$eml4" \
    && getpart html "$eml4" | grep -q "VMware snapshot report (older than 5 days): " && getpart html "$eml4" | grep -q "Older than 5 days, held back" \
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
   && grep -Eq "^CRITICAL +DC0_C0_H2 +disconnected" "$work/al.txt" && grep -Eq "^WARNING +user +192.0.2.99 +DC0_C0_H0 +3 " "$work/al.txt" \
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
         {"id": "P3", "cause": "A service account with an old password.", "evidence": "3 failed logins from 192.0.2.99",
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
approved2="{\"vm_snapshot_cleanup_candidates\": [{\"vm\": \"DC0_C0_RP0_VM1\", \"vcenter\": \"$vc2\", \"moid\": \"$m2\", \"id\": $id2, \"name\": \"old-b\"}],
           \"vm_snapshot_cleanup_candidates_workflow\": \"92\", \"awx_workflow_job_id\": 92}"
run "cleanup after the approval of vCenter 2's snapshot: deleted there, and only there" ok vm_snapshot_cleanup.yml -e _vm_now=$later -e "$two" \
    -e "$approved2" -- "deleted 1 snapshot(s)" 'DELETED DC0_C0_RP0_VM1 ('"$vc2"'): \"old-b\"'
check "  ...vCenter 1's VM (same ID) keeps old-a" DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])" "keep-me,old-a"
got="$(STATE_PORT=$port2 state DC0_C0_RP0_VM1 "','.join(s['name'] for s in v['snapshots'])")"
[ -z "$got" ] && echo "ok   -   ...vCenter 2's old-b is gone" || { echo "FAIL -   ...vCenter 2 still has [$got]"; fails=$((fails + 1)); }
approved1="{\"vm_snapshot_cleanup_candidates\": [{\"vm\": \"DC0_C0_RP0_VM1\", \"moid\": \"$m1\", \"id\": $id1, \"name\": \"old-a\"}],
           \"vm_snapshot_cleanup_candidates_workflow\": \"93\", \"awx_workflow_job_id\": 93}"
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
incd="$(newticket $'vm_name: app94\ntemplate: rhel9-gold\ndisks_gb: 120, 30, 40')"
run "three disks: disk 1 grown, two new disks added in one change (each its own device key)" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incd" -e "$rules" -- "app94 deployed" "2 disk(s) added (thin): 30, 40 GB"
VMWARE_PORT=$port "$py" - <<'EOPY' && echo "ok   -   ...app94 has disks 120 + 30 + 40 GB" || { echo "FAIL -   ...three disks"; fails=$((fails + 1)); }
import os, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "app94"][0]
disks = sorted((d.unitNumber, d.capacityInKB // 1048576) for d in v.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk))
print(disks)
sys.exit(0 if [gb for _, gb in disks] == [120, 30, 40] else 1)
EOPY
incc="$(newticket $'vm_name: app91\ntemplate: rhel9-gold\ncluster: 127.0.0.1')"
run "a cluster that is the vCenter's name: refused, saying so and naming the clusters there" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$incc" -e "$rules" \
    -- "no cluster named 127.0.0.1 in datacenter DC0: 127.0.0.1 is a vCenter, not a cluster - the clusters there: DC0_C0" "(from ...)"
incc="$(newticket $'vm_name: app92\ntemplate: rhel9-gold\ncluster: dc0_c0')"
run "a cluster written in other case: found, with a note" ok vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$incc" -e "$rules" \
    -- "cluster dc0_c0 is written 'DC0_C0' in vCenter" "ready to build in DC0_C0 - dry run"
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
# ---- the change wins over its task's description (vm_deploy_parent_wins); the notes on both (vm_deploy_notes_also)
both='{"vm_deploy_parent_wins": true, "vm_deploy_notes_also": ["parent", "tasks"], "vm_deploy_notes_tasks_query": "short_descriptionLIKEto deploy",
       "vm_deploy_done_fields_by_table": {"change_task": {"state": "3", "close_notes": "VM deployed by AAP"}}}'
chg7="$(newrec change_request '{"short_description": "Staging servers", "description": "vm_name: cwapp01\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Linux Operations"}')"
ct7="$(newrec change_task "{\"short_description\": \"Task to deploy a staging VM\", \"description\": \"vm_name: wrong01\ncpu: 1\", \"change_request\": \"$chg7\", \"assignment_group\": \"Linux Operations\"}")"
run "vm_deploy_parent_wins: the change's lines over its task's description (dry run); the notes would go on both" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$ct7" -e "$rules" -e "$both" \
    -- "vm_name: cwapp01   (from parent $chg7 description)" "cpu: 2   (from parent $chg7 description)" "Notes on $ct7 and $chg7. $ct7 is closed when every VM is built."
n1=$(wc -l < "$work/sn.jsonl")
run "built from the task: every note on the task and on its change, only the task closed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$ct7" -e "$rules" -e "$both" -- "VM deploy $ct7: cwapp01 deployed"
"$py" - "$work/sn.jsonl" "$n1" "$sn_port" "$ct7" "$chg7" <<'EOPY' && echo "ok   -   ...the change has the Deploying and the deployed notes, no state change; the task is closed" || { echo "FAIL -   ...notes on the change"; fails=$((fails + 1)); }
import base64, json, sys, urllib.request
lines = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):]]
h = {"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
get = lambda t, n: json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/now/table/%s?sysparm_query=number=%s" % (sys.argv[3], t, n), headers=h)))["result"][0]
task, change = get("change_task", sys.argv[4]), get("change_request", sys.argv[5])
on_chg = [l["body"] for l in lines if l["method"] == "PATCH" and l["path"].startswith("/api/now/table/change_request/" + change["sys_id"])]
notes = [b.get("work_notes", "") for b in on_chg]
ok = (on_chg and all(set(b) <= {"work_notes", "comments"} for b in on_chg)
      and any(n.startswith("[AAP] Deploying this VM") for n in notes) and any(n.startswith("[AAP] VM cwapp01 deployed") for n in notes)
      and task["state"] == "3" and change["state"] == "-1")
print(task["state"], change["state"], [n.splitlines()[0] for n in notes])
sys.exit(0 if ok else 1)
EOPY
chg8="$(newrec change_request '{"short_description": "Staging server two", "description": "vm_name: cwapp02\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Linux Operations"}')"
ct8="$(newrec change_task "{\"short_description\": \"Task to deploy a staging VM\", \"change_request\": \"$chg8\", \"assignment_group\": \"Linux Operations\"}")"
ct8b="$(newrec change_task "{\"short_description\": \"Open the firewall\", \"change_request\": \"$chg8\", \"assignment_group\": \"Linux Operations\"}")"
n1=$(wc -l < "$work/sn.jsonl")
run "launched with the change (a web front end): its deploy task gets every note too, not closed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$chg8" -e "$rules" -e "$both" -- "Notes on $chg8 and $ct8. $chg8 is never closed (no close values for change_request); its tasks found are left open (vm_deploy_close_tasks: false)." "VM deploy $chg8: cwapp02 deployed"
"$py" - "$work/sn.jsonl" "$n1" "$sn_port" "$ct8" "$ct8b" <<'EOPY' && echo "ok   -   ...the deploy task has the notes; the other task none; neither closed" || { echo "FAIL -   ...notes on the change's task"; fails=$((fails + 1)); }
import base64, json, sys, urllib.request
lines = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):]]
h = {"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
get = lambda t, n: json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/now/table/%s?sysparm_query=number=%s" % (sys.argv[3], t, n), headers=h)))["result"][0]
vi, other = get("change_task", sys.argv[4]), get("change_task", sys.argv[5])
on = lambda r: [l["body"] for l in lines if l["method"] == "PATCH" and l["path"].startswith("/api/now/table/change_task/" + r["sys_id"])]
notes = [b.get("work_notes", "") for b in on(vi)]
ok = (any(n.startswith("[AAP] VM cwapp02 deployed") for n in notes) and all(set(b) <= {"work_notes", "comments"} for b in on(vi))
      and not on(other) and vi["state"] == "1" and other["state"] == "1")
print(vi["state"], [n.splitlines()[0] for n in notes], len(on(other)))
sys.exit(0 if ok else 1)
EOPY
chg10="$(newrec change_request '{"short_description": "Staging server three", "description": "vm_name: cwapp04\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Linux Operations"}')"
ct10="$(newrec change_task "{\"short_description\": \"Task to deploy a staging VM\", \"change_request\": \"$chg10\", \"assignment_group\": \"Linux Operations\"}")"
ct10b="$(newrec change_task "{\"short_description\": \"Open the firewall\", \"change_request\": \"$chg10\", \"assignment_group\": \"Linux Operations\"}")"
run "vm_deploy_close_tasks: launched with the change, its deploy task closed the way a change task is - not the change" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$chg10" -e "$rules" -e "$both" -e vm_deploy_close_tasks=true \
    -- "VM deploy $chg10: cwapp04 deployed" "Task $ct10 closed: close_notes = VM deployed by AAP, state = 3"
"$py" - "$sn_port" "$ct10" "$ct10b" "$chg10" <<'EOPY' && echo "ok   -   ...the deploy task closed (3), the other task and the change still open" || { echo "FAIL -   ...closing the change's task"; fails=$((fails + 1)); }
import base64, json, sys, urllib.request
h = {"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
get = lambda t, n: json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/now/table/%s?sysparm_query=number=%s" % (sys.argv[1], t, n), headers=h)))["result"][0]
vi, other, change = get("change_task", sys.argv[2]), get("change_task", sys.argv[3]), get("change_request", sys.argv[4])
print(vi["state"], other["state"], change["state"])
sys.exit(0 if (vi["state"], other["state"], change["state"]) == ("3", "1", "-1") else 1)
EOPY
chg9="$(newrec change_request '{"short_description": "No task", "description": "vm_name: cwapp03\ntemplate: rhel9-gold", "state": "-1", "approval": "approved", "assignment_group": "Linux Operations"}')"
run "launched with a change that has no deploy task: the notes on the change only, and it says so" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$chg9" -e "$rules" -e "$both" -- "No open task of $chg9 matches group Linux Operations and short_descriptionLIKEto deploy: the notes are on the change only."
# ---- launched with a change of another group (vm_deploy_assignment_group_by_table): any group on the change;
# its task of vm_deploy_assignment_group gets every note and is closed, another group's task is left alone
anyg='{"vm_deploy_notes_also": ["parent", "tasks"], "vm_deploy_close_tasks": true,
       "vm_deploy_done_fields_by_table": {"change_task": {"state": "3", "close_notes": "VM deployed by AAP"}}}'
chg11="$(newrec change_request '{"short_description": "Staging server five", "description": "vm_name: cwapp05\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Change Board"}')"
ct11="$(newrec change_task "{\"short_description\": \"Deploy the VM\", \"change_request\": \"$chg11\", \"assignment_group\": \"Linux Operations\"}")"
ct11b="$(newrec change_task "{\"short_description\": \"Deploy the VM\", \"change_request\": \"$chg11\", \"assignment_group\": \"Change Board\"}")"
run "launched with a change of another group: refused, the setting named (dry run)" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$chg11" -e "$rules" -e "$anyg" \
    -- "the ticket is assigned to 'Change Board', not to 'Linux Operations' (vm_deploy_assignment_group)"
run "...vm_deploy_assignment_group_by_table {change: ''}: a change of any group (dry run); the notes would go on its task of the group" ok \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$chg11" -e "$rules" -e "$anyg" -e '{"vm_deploy_assignment_group_by_table": {"change": ""}}' \
    -- "Notes on $chg11 and $ct11. $chg11 is never closed (no close values for change_request); its tasks found are closed too (vm_deploy_close_tasks)." "vm_name: cwapp05   (from description)"
n1=$(wc -l < "$work/sn.jsonl")
run "...built: every note on the change and on its task of the group, that task closed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$chg11" -e "$rules" -e "$anyg" -e '{"vm_deploy_assignment_group_by_table": {"change_request": ""}}' \
    -- "VM deploy $chg11: cwapp05 deployed" "Task $ct11 closed: close_notes = VM deployed by AAP, state = 3"
"$py" - "$work/sn.jsonl" "$n1" "$sn_port" "$chg11" "$ct11" "$ct11b" <<'EOPY' && echo "ok   -   ...the change: the notes, still open; its task of the group: the notes, closed (3); the other group's task: nothing" || { echo "FAIL -   ...a change of any group"; fails=$((fails + 1)); }
import base64, json, sys, urllib.request
lines = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):]]
h = {"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
get = lambda t, n: json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/now/table/%s?sysparm_query=number=%s" % (sys.argv[3], t, n), headers=h)))["result"][0]
change, task, other = get("change_request", sys.argv[4]), get("change_task", sys.argv[5]), get("change_task", sys.argv[6])
on = lambda t, r: [l["body"] for l in lines if l["method"] == "PATCH" and l["path"].startswith("/api/now/table/%s/%s" % (t, r["sys_id"]))]
first = lambda t, r: [b.get("work_notes", "").splitlines()[0] for b in on(t, r) if b.get("work_notes")]
ok = (all(set(b) <= {"work_notes", "comments"} for b in on("change_request", change))
      and "[AAP] Deploying this VM (AAP job 4711 by operator1):" in first("change_request", change)
      and "[AAP] VM cwapp05 deployed (AAP job 4711 by operator1)." in first("change_request", change)
      and "[AAP] VM cwapp05 deployed (AAP job 4711 by operator1)." in first("change_task", task)
      and not on("change_task", other) and (change["state"], task["state"], other["state"]) == ("-1", "3", "1"))
print(change["state"], task["state"], other["state"], first("change_request", change), first("change_task", task), len(on("change_task", other)))
sys.exit(0 if ok else 1)
EOPY
run "...a change task of another group is still refused: the setting is for changes only (dry run)" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$ct11b" -e "$rules" -e "$anyg" -e '{"vm_deploy_assignment_group_by_table": {"change_request": ""}}' \
    -- "the ticket is assigned to 'Change Board', not to 'Linux Operations' (vm_deploy_assignment_group)"
# ---- the standard setup (docs/VM_DEPLOY_GUIDE.md): launched with the change of any group, its task found by its
# words alone (no vm_deploy_assignment_group), the notes on both; the task left open - or closed with the one switch
std='{"vm_deploy_templates": ["rhel9-*"], "vm_deploy_assignment_group": "", "vm_deploy_wait_ip": 3, "vm_deploy_wait_customization": 60,
      "vm_deploy_notes_also": ["parent", "tasks"], "vm_deploy_notes_tasks_query": "short_descriptionLIKEwill deploy the VM",
      "vm_deploy_done_fields_by_table": {"change_task": {"state": "3", "close_code": "successful", "close_notes": "VM deployed by AAP"}}}'
chg13="$(newrec change_request '{"short_description": "Two staging servers", "description": "vm_name: cwapp13\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Change Board"}')"
ct13="$(newrec change_task "{\"short_description\": \"The server team will deploy the VM\", \"change_request\": \"$chg13\", \"assignment_group\": \"Linux Operations\"}")"
ct13b="$(newrec change_task "{\"short_description\": \"Open the firewall\", \"change_request\": \"$chg13\", \"assignment_group\": \"Linux Operations\"}")"
run "the standard setup: a change of any group, its task found by its words; the change never closed, the task left open" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$chg13" -e "$std" \
    -- "Notes on $chg13 and $ct13. $chg13 is never closed (no close values for change_request); its tasks found are left open (vm_deploy_close_tasks: false)." \
       "VM deploy $chg13: cwapp13 deployed"
chg14="$(newrec change_request '{"short_description": "One more staging server", "description": "vm_name: cwapp14\ntemplate: rhel9-gold\ncpu: 2\nmemory_gb: 4", "state": "-1", "approval": "approved", "assignment_group": "Change Board"}')"
ct14="$(newrec change_task "{\"short_description\": \"The server team will deploy the VM\", \"change_request\": \"$chg14\", \"assignment_group\": \"Linux Operations\"}")"
run "...vm_deploy_close_tasks: true - the same, and the task closed when the VM is built" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$chg14" -e "$std" -e vm_deploy_close_tasks=true \
    -- "its tasks found are closed too (vm_deploy_close_tasks)." "Task $ct14 closed:"
"$py" - "$sn_port" "$chg13" "$ct13" "$ct13b" "$chg14" "$ct14" <<'EOPY' && echo "ok   -   ...the changes still open; the first task open with the notes, the other task untouched; the second task closed" || { echo "FAIL -   ...the standard setup"; fails=$((fails + 1)); }
import base64, json, sys, urllib.request
h = {"Authorization": "Basic " + base64.b64encode(b"api:goodpw").decode()}
get = lambda t, n: json.load(urllib.request.urlopen(urllib.request.Request(
    "http://127.0.0.1:%s/api/now/table/%s?sysparm_query=number=%s&sysparm_display_value=false" % (sys.argv[1], t, n), headers=h)))["result"][0]
c13, t13, o13, c14, t14 = (get("change_request", sys.argv[2]), get("change_task", sys.argv[3]), get("change_task", sys.argv[4]),
                           get("change_request", sys.argv[5]), get("change_task", sys.argv[6]))
states = (c13["state"], t13["state"], o13["state"], c14["state"], t14["state"])
print(states, t14.get("close_code"))
sys.exit(0 if states == ("-1", "1", "1", "-1", "3") and t14.get("close_code") == "successful" else 1)
EOPY
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
# the table named as people say it: "change" is no ServiceNow table (HTTP 400 "Invalid table change" before);
# a site's own prefix (CHGN...) goes to vm_deploy_ticket_table, a known one (CTASK...) keeps its table
chgn="$(newrec change_request '{"number": "CHGN0007001", "short_description": "VM request", "description": "vm_name: chgnapp01\ntemplate: rhel9-gold", "state": "-1", "approval": "approved", "assignment_group": "Linux Operations"}')"
run "vm_deploy_ticket_table: change - read as change_request (a CHGN... number, dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$chgn" -e "$rules" -e '{"vm_deploy_ticket_table": "change"}' -- "chgnapp01 ready to build in DC0_C0 - dry run"
run "vm_deploy_ticket_prefixes: {CHGN: Change Request} - read as change_request (dry run)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$chgn" -e "$rules" -e '{"vm_deploy_ticket_prefixes": {"CHGN": "Change Request"}}' -- "chgnapp01 ready to build in DC0_C0 - dry run"
run "a table ServiceNow does not have: said plainly, where it comes from" fail vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$chgn" -e "$rules" \
    -e '{"vm_deploy_ticket_table": "nosuchtable"}' -- 'ServiceNow has no table \"nosuchtable\" (Invalid table nosuchtable). It comes from vm_deploy_ticket_table: leave it empty'
ct8="$(newrec change_task "{\"short_description\": \"Platform team to deploy VM\", \"description\": \"vm_name: ctapp08\\ntemplate: rhel9-gold\", \"change_request\": \"$chgn\", \"assignment_group\": \"Linux Operations\"}")"
run "a change task with vm_deploy_ticket_table: change - its CTASK prefix wins: read as a change task, its change as the parent (dry run)" ok \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$ct8" -e "$rules" -e '{"vm_deploy_ticket_table": "change"}' \
    -- "Parent: parent $chgn (change_request, field change_request)" "ctapp08 ready to build in DC0_C0 - dry run"
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
run "two VMs, one name taken: every VM checked in vCenter first - none built, the ticket says why" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incp" -e "$rules" -e "$words" -e "$close6" \
    -- "Nothing was built: multi04: a VM named multi04 already exists"
vstate="VMWARE_PORT=$port $py $here/vcsim.py state"
"$py" - "$work/sn.jsonl" "$n3" "$vstate" <<'EOPY' && echo "ok   -   ...one note: nothing built, multi04 and why; multi03 not in vCenter; not closed" || { echo "FAIL -   ...nothing built note"; fails=$((fails + 1)); }
import json, subprocess, sys
rows = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):] if '"PATCH"' in l]
notes = [r["body"].get("work_notes", "") for r in rows]
s = [n for n in notes if n.startswith("[AAP] Nothing was built (")]
names = list(json.loads(subprocess.check_output(sys.argv[3], shell=True)))
sys.exit(0 if s and "1 of 2 cannot be:\n- multi04: a VM named multi04 already exists" in s[0] and "multi03" not in names
         and not any(n.startswith("[AAP] Deploying") for n in notes) and not any(r["body"].get("state") == "6" for r in rows) else 1)
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
run "...the name freed, run again: both built, the ticket closed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incp" -e "$rules" -e "$words" -e "$close6" \
    -- "2 VMs (multi03, multi04) deployed" "multi03: deployed" "multi04: deployed" "Ticket closed: close_notes = VMs deployed by AAP"
VMWARE_PORT=$port "$py" "$here/vcsim.py" state | "$py" -c "import json,re,sys; v=json.load(sys.stdin)['multi04'][0]; sys.exit(0 if re.match(r'$incp - built \d{4}-\d{2}-\d{2}\nTemplate: ', v['annotation']) else 1)" \
    && echo "ok   -   ...each VM marked for its ticket on the first line of its notes (built)" || { echo "FAIL -   ...the ticket mark"; fails=$((fails + 1)); }
# a job of three VMs that stopped after the first two: one built (no result note), one cloned and not finished
incs="$(newticket $'Server names:\r\n- rsm01\r\n- rsm02\r\n- rsm03\r\nRHEL 9, 2 cpus, 4gb ram')"
VMWARE_PORT=$port "$py" - "$incs" <<'EOPY' || { echo "ABORT - the earlier run's VMs"; exit 1; }
import os, sys, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
wait = lambda t: [time.sleep(0.1) for _ in iter(lambda: t.info.state in ("success", "error"), True)] and None or t.info.result
src = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM1"][0]
for name, state in (("rsm01", "built"), ("rsm02", "building")):
    note = "Deployed from rhel9-gold for %s by an earlier job.\n[site-automation ticket=%s state=%s]" % (sys.argv[1], sys.argv[1], state)
    new = wait(src.CloneVM_Task(folder=src.parent, name=name, spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False)))
    wait(new.ReconfigVM_Task(spec=vim.vm.ConfigSpec(annotation=note)))
    if state == "built":
        wait(new.PowerOnVM_Task())
    elif new.runtime.powerState == "poweredOn":
        wait(new.PowerOffVM_Task())
EOPY
nr=$(wc -l < "$work/sn.jsonl")
run "three VMs, run again after a job stopped: the one built left, the one started finished, the third built - the ticket closed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incs" -e "$rules" -e "$words" -e "$close6" \
    -- "3 VMs (rsm01, rsm02, rsm03) deployed" "rsm01: already built (left as it is)" "rsm02: deployed" "rsm03: deployed" \
       "picked up rsm02 where an earlier run of this ticket stopped" "Ticket closed: close_notes = VMs deployed by AAP"
"$py" - "$work/sn.jsonl" "$nr" "$vstate" "$incs" <<'EOPY' && echo "ok   -   ...the work notes say where it picked up; rsm02 (an older release's mark) powered on, its notes in the new layout" || { echo "FAIL -   ...resume notes"; fails=$((fails + 1)); }
import json, subprocess, sys
rows = [json.loads(l) for l in open(sys.argv[1]).readlines()[int(sys.argv[2]):] if '"PATCH"' in l]
notes = [r["body"].get("work_notes", "") for r in rows]
dep = [n for n in notes if n.startswith("[AAP] Deploying 3 VMs")]
vms = dict((k, v[0]) for k, v in json.loads(subprocess.check_output(sys.argv[3], shell=True)).items())
ok = (dep and "Picking up where an earlier run for this ticket stopped:\n- rsm01: built already - left as it is\n"
              "- rsm02: started, not finished - finished in place\n- rsm03: to build" in dep[0]
      and any(n.startswith("[AAP] VM rsm01 deployed (") and "Built by an earlier run for this ticket: left as it is." in n for n in notes)
      and any(n.startswith("[AAP] VM rsm02 deployed (") and "Picked up where an earlier run for this ticket stopped" in n for n in notes)
      and any(n.startswith("[AAP] VM rsm03 deployed (") for n in notes)
      and vms["rsm02"]["power"] == "poweredOn" and vms["rsm02"]["annotation"].startswith("%s - built " % sys.argv[4])
      and "[site-automation" not in vms["rsm02"]["annotation"] and "Deployed from" not in vms["rsm02"]["annotation"])
print([(n.splitlines() or [""])[0] for n in notes], vms["rsm02"]["power"])
sys.exit(0 if ok else 1)
EOPY
# vm_deploy_resume: false - the VM's mark is not enough (only the ticket's own note, as before 0.15.5)
inct="$(newticket $'vm_name: rsm09\ntemplate: rhel9-gold')"
VMWARE_PORT=$port "$py" - "$inct" <<'EOPY' || { echo "ABORT - rsm09"; exit 1; }
import os, sys, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
wait = lambda t: [time.sleep(0.1) for _ in iter(lambda: t.info.state in ("success", "error"), True)] and None or t.info.result
src = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM1"][0]
new = wait(src.CloneVM_Task(folder=src.parent, name="rsm09", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False)))
wait(new.ReconfigVM_Task(spec=vim.vm.ConfigSpec(annotation="x\n[site-automation ticket=%s state=built]" % sys.argv[1])))
EOPY
run "vm_deploy_resume: false - a VM marked for the ticket, no note on it: refused" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inct" -e "$rules" -e vm_deploy_resume=false -- "a VM named rsm09 already exists"
run "...on (the default): the same VM left as it is" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inct" -e "$rules" -- "built by an earlier run for this ticket (the VM's notes say so): left as it is"
# datastores by the VM's name (vm_deploy_datastore_rules): SQL servers on theirs, the others never there (dry runs)
incq1="$(newticket $'vm_name: ab01sql01\ntemplate: rhel9-gold')"
run "a SQL server goes on a datastore its rule names - chosen among them, the least used first" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$incq1" -e "$rules" -e '{"vm_deploy_datastore_rules": [{"match": ["*SQL*", "*DBV*"], "datastores": ["LocalDS_*"]}]}' \
    -- "datastores: LocalDS_*   (from datastore rule *SQL*, *DBV*)" "datastore chosen: LocalDS_0"
run "...and none of them on its cluster: not built anywhere else" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$incq1" -e "$rules" -e '{"vm_deploy_datastore_rules": [{"match": "*SQL*", "datastores": "*SQL*"}]}' \
    -- "no datastore among *SQL* of"
incq2="$(newticket $'vm_name: ab01app07\ntemplate: rhel9-gold')"
run "a VM no rule matches never goes on a datastore kept for the rule's VMs" fail vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$incq2" -e "$rules" -e '{"vm_deploy_datastore_rules": [{"match": "*SQL*", "datastores": ["LocalDS_*"]}]}' \
    -- "the template's datastore LocalDS_0 is kept for other VMs" "(not LocalDS_*)"
run "vm_deploy_datastore_balance: a VM nothing places goes on the least used of its cluster's datastores" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$incq2" -e "$rules" -e vm_deploy_datastore_balance=true \
    -- "datastores: *   (from vm_deploy_datastore_balance (the cluster's least used))" "datastore chosen: LocalDS_0"
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
# the approval is for the ticket as the propose step checked it: changed after it, the build step builds nothing
incx="$(newticket 'Hi, please build a Red Hat 9 server named lnxwords03, medium size. Thanks, Jane')"
run "plain words: propose step for lnxwords03" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incx" -e "$rules" -e "$words" -e vm_deploy_phase=propose -- "lnxwords03 ready to build in"
setrec incident "$incx" '{"comments": "vm_name: lnxother03\ntemplate: rhel9-gold\ncpu: 4"}'
run "...a comment added after the approval: the build step builds nothing, says the ticket changed" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$incx" -e "$rules" -e "$words" -e vm_deploy_phase=build -- "was changed after the approval"
VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q -e '"lnxother03"' -e '"lnxwords03"' \
    && { echo "FAIL -   ...built after a change"; fails=$((fails + 1)); } || echo "ok   -   ...nothing built"
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
run "GenAI on, a task whose change gives the name and template: no GenAI needed (the propose step goes on)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$sct5" -e "$genai" -e "$actvars" -e '{"vm_deploy_sites": {}, "vm_deploy_phase": "propose"}' \
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
    -- "questions for the requester (nothing built)" "DROPPED: vm_name 'rhelsrv01'" "GENAI NOTE (not sent to the requester): Which site: SiteA?" \
       "the requester has to answer"
tail -1 "$work/sn.jsonl" | grep -q '"comments": "\[AAP\] To build this VM' && ! tail -1 "$work/sn.jsonl" | grep -q 'Which site' \
    && echo "ok   -   ...a comment the requester sees - the job's question, not GenAI's" || { echo "FAIL -   ...questions comment"; fails=$((fails + 1)); }
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
# a dry run (Check) asks GenAI only with vm_deploy_genai_in_check - and writes nothing on the ticket
inc15="$(newticket 'Please build a RHEL 9 server called lnxapp15 for SiteA, 2 CPUs, 2 GB of RAM')"
act_says '{"vm_name": {"value": "lnxapp15", "evidence": "called lnxapp15"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 2, "evidence": "2 CPUs"}, "memory_gb": {"value": 2, "evidence": "2 GB of RAM"}, "site": {"value": "SiteA", "evidence": "for SiteA"}}'
rm -f "$work/act/fake-act-stdin.txt"
run "GenAI on, a dry run (Check): GenAI not asked (vm_deploy_genai_in_check off)" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc15" -e "$genai" -e "$actvars" -- "GenAI would read the ticket (dry run: not asked)"
[ ! -e "$work/act/fake-act-stdin.txt" ] && echo "ok   -   ...ACT was not called" || { echo "FAIL -   ...ACT called in Check"; fails=$((fails + 1)); }
n0=$(wc -l < "$work/sn.jsonl")
run "vm_deploy_genai_in_check: a dry run asks GenAI, shows the proposal and where it would go" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc15" -e "$genai" -e "$actvars" -e vm_deploy_genai_in_check=true \
    -- "vm_name: lnxapp15   (from GenAI proposal (this run))" "lnxapp15 ready to build in DC0_" "dry run"
[ -e "$work/act/fake-act-stdin.txt" ] && [ "$(sed -n "$((n0 + 1)),\$p" "$work/sn.jsonl" | grep -c '"PATCH"')" = 0 ] \
    && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"lnxapp15"' \
    && echo "ok   -   ...ACT was asked; nothing written on the ticket, no VM" || { echo "FAIL -   ...GenAI in Check"; fails=$((fails + 1)); }
inc16="$(newticket 'please build me a RHEL 9 server with 4 CPUs')"
act_says '{"vm_name": {"value": "rhelsrv16", "evidence": "a RHEL 9 server"}, "template": {"value": "rhel9-gold", "evidence": "RHEL 9"},
           "cpu": {"value": 4, "evidence": "4 CPUs"}, "questions": []}'
n0=$(wc -l < "$work/sn.jsonl")
run "vm_deploy_genai_in_check, questions: shown, not sent" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$inc16" -e "$genai" -e "$actvars" -e vm_deploy_genai_in_check=true \
    -- "dry run: GenAI read the ticket - questions for the requester (not sent)" "DROPPED: vm_name 'rhelsrv16'" "nothing was written on the ticket"
[ "$(sed -n "$((n0 + 1)),\$p" "$work/sn.jsonl" | grep -c '"PATCH"')" = 0 ] \
    && echo "ok   -   ...no comment to the requester" || { echo "FAIL -   ...a dry run wrote on the ticket"; fails=$((fails + 1)); }
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
# a change laid out per server (the servers only in its description), its task to the platform team: names after
# "new ... VM /", "CPU : CORE.2", "HDD'S C: /120", Windows Server by its year alone - and no 2025 template
laybody="$("$py" - <<'EOPY'
import json
d = ("Approval is required. Steps for the new web tier.\n\n1.  Build new web tier :  ab01lay01 (front end)\n"
     "a.  Platform team : spin up new 2025 VM / ab01lay01 / CPU :  CORE.2  / RAM : 2 GB.  / HDD'S C: /120  Location: Site One\n\n"
     "1.  Build new web tier :  ab01lay02\n"
     "a.  Platform team : spin up new 2025 VM / ab01lay02 /  CPU :  CORE.2  / RAM : 2 GB.  / HDD'S C: /120 Location: Site Two")
print(json.dumps({"number": "CHGN0007101", "short_description": "Windows Server deployment - Server 2025", "description": d,
                  "state": "-1", "approval": "approved", "assignment_group": "Change Board"}))
EOPY
)"
chgl="$(newrec change_request "$laybody")"
ctl="$(newrec change_task "{\"short_description\": \"Platform team to deploy VM (CPU, Storage, Memory, OS image and location)\", \"change_request\": \"$chgl\", \"assignment_group\": \"Linux Operations\"}")"
layw='{"vm_deploy_plain_words": true, "vm_deploy_templates": ["rhel9-gold", "win2022-pxe"], "vm_deploy_pxe_templates": ["win2022-pxe"],
       "vm_deploy_name_rules": [{"match": "ab01*", "cluster": "DC0_C0", "datacenter": "DC0"}], "vm_deploy_sites": {}}'
run "a change laid out per server asking for Server 2025, only a 2022 template: a question naming Windows Server 2025, never the 2022 one (dry run)" \
    fail vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$ctl" -e "$rules" -e "$layw" \
    -- "the operating system Windows Server 2025" "no template here is for it"
run "...with vm_deploy_plain_templates {server 2025: win2022-pxe}: two VMs, 2 vCPU, 2 GB, C: 120 GB, placed by the name rule (dry run)" ok \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$ctl" -e "$rules" -e "$layw" -e '{"vm_deploy_plain_templates": {"server 2025": "win2022-pxe"}}' \
    -- "vm_name: ab01lay01, ab01lay02   (from the words 'spin up new 2025 VM / ab01lay01'" "cpu: 2   (from the words 'CPU :  CORE.2'" \
       "disks_gb: 120   (from the words 'HDD'S C: /120'" "template: win2022-pxe   (from the words" \
       "2 VMs (ab01lay01, ab01lay02) ready to build - dry run" "ab01lay01: ready to build, cluster DC0_C0" "ab01lay02: ready to build, cluster DC0_C0"
act_says '{"vm_name": {"value": "ab01lay01, ab01lay02", "evidence": "spin up new 2025 VM / ab01lay01 ... spin up new 2025 VM / ab01lay02"},
           "os": {"value": "Windows Server 2025", "evidence": "new 2025 VM"}, "template": {"value": "win2022-pxe", "evidence": "new 2025 VM"},
           "cpu": {"value": 2, "evidence": "CPU :  CORE.2"}, "questions": ["Which vCenter?"]}'
n5=$(wc -l < "$work/sn.jsonl")
run "GenAI on, no approval: it names the 2022 template for a Server 2025 change - dropped, the job asks which template, nothing built" fail \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$ctl" -e "$rules" -e "$genai" -e "$actvars" -e "$layw" -e vm_deploy_genai_approval=false \
    -- "DROPPED: template 'win2022-pxe': it is for Windows Server 2022, the ticket asks for Windows Server 2025" \
       "GENAI NOTE (not sent to the requester): Which vCenter?" "QUESTION: the ticket asks for the operating system Windows Server 2025"
sed -n "$((n5 + 1)),\$p" "$work/sn.jsonl" | grep '"comments"' | grep -q 'Windows Server 2025' && ! sed -n "$((n5 + 1)),\$p" "$work/sn.jsonl" | grep -q 'Which vCenter' \
    && ! VMWARE_PORT=$port "$py" "$here/vcsim.py" state | grep -q '"ab01lay01"' \
    && echo "ok   -   ...the requester is asked which template; GenAI's note not sent; no VM" || { echo "FAIL -   ...2025 question / built"; fails=$((fails + 1)); }
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
# ---- a change laid out per server at two sites in two vCenters: each line its own CPU, memory and disks;
# its Location (a site's name, or its words in vm_deploy_plain_sites) its site - and so its vCenter ----
VMWARE_PORT=$port2 "$py" - <<'EOPY' || { echo "ABORT - template on the second vCenter"; exit 1; }
import os, time
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
src = [v for v in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if v.name == "DC0_C0_RP0_VM0"][0]
t = src.CloneVM_Task(folder=src.parent, name="rhel9-gold", spec=vim.vm.CloneSpec(location=vim.vm.RelocateSpec(), powerOn=False, template=True))
while t.info.state not in ("success", "error"):
    time.sleep(0.1)
assert t.info.state == "success", t.info.error
EOPY
twobody="$("$py" - <<'EOPY'
import json
d = ("Approval is required. Steps for the new app tier.\n\n"
     "a.  Platform team : spin up new RHEL 9 VM / ab01xy05a / CPU :  CORE.1  / RAM : 1 GB.  / HDD'S C: /40  Location: North Campus\n\n"
     "a.  Platform team : spin up new RHEL 9 VM / ab01xy05b / CPU :  CORE.2  / RAM : 2 GB.  / HDD'S C: /60, D: /10 Location: SiteTwo")
print(json.dumps({"number": "CHGN0007201", "short_description": "New app tier", "description": d,
                  "state": "-1", "approval": "approved", "assignment_group": "Change Board"}))
EOPY
)"
chgs="$(newrec change_request "$twobody")"
cts="$(newrec change_task "{\"short_description\": \"Platform team to deploy VM\", \"change_request\": \"$chgs\", \"assignment_group\": \"Linux Operations\"}")"
twosites="{\"vm_deploy_plain_words\": true, \"vm_deploy_templates\": [\"rhel9-gold\"], \"vm_deploy_assignment_group\": \"Linux Operations\",
           \"vm_deploy_wait_ip\": 3, \"vm_deploy_wait_customization\": 60, \"vmware_vcenters\": [\"$vc2\"],
           \"vm_deploy_sites\": {\"Site One\": {\"vcenter\": \"127.0.0.1\", \"cluster\": \"DC0_C0\"}, \"Site Two\": {\"vcenter\": \"$vc2\", \"cluster\": \"DC0_C0\"}},
           \"vm_deploy_plain_sites\": {\"north campus\": \"Site One\"},
           \"vm_deploy_name_rules\": [{\"match\": \"ab01xy05*\", \"sites\": [\"Site One\", \"Site Two\"]}]}"
run "a change laid out per server, a name rule for two sites: each VM its line's size, its Location's vCenter (dry run)" ok \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$cts" -e "$twosites" \
    -- "cpu: ab01xy05a: 1; ab01xy05b: 2" "memory_gb: ab01xy05a: 1; ab01xy05b: 2" "disks_gb: ab01xy05a: 40; ab01xy05b: 60, 10" \
       "site: ab01xy05a: Site One; ab01xy05b: Site Two" "vcenter: ab01xy05a: 127.0.0.1; ab01xy05b: $vc2" \
       "2 VMs (ab01xy05a, ab01xy05b) ready to build - dry run"
run "...a Location no site has: a question naming the sites, nothing built (dry run)" fail \
    vm_deploy_from_ticket.yml --check -e vm_deploy_ticket="$cts" -e "$twosites" -e '{"vm_deploy_plain_sites": {}}' \
    -- "the ticket does not say where ab01xy05a goes - Site One or Site Two: write it on its line, e.g. 'Location: Site One'"
run "...built: each VM in its own vCenter" ok vm_deploy_from_ticket.yml -e vm_deploy_ticket="$cts" -e "$twosites" \
    -- "2 VMs (ab01xy05a, ab01xy05b) deployed" "ab01xy05a: deployed" "ab01xy05b: deployed"
"$py" - "$port" "$port2" <<'EOPY' && echo "ok   -   ...ab01xy05a: 1 vCPU, 1024 MB, 40 GB in the first vCenter; ab01xy05b: 2 vCPU, 2048 MB, 60 + 10 GB in the second" \
    || { echo "FAIL -   ...the two-site VMs"; fails=$((fails + 1)); }
import sys
from pyVim.connect import SmartConnect
from pyVmomi import vim


def vms(port):
    si = SmartConnect(host="127.0.0.1", port=int(port), user="user", pwd="pass", disableSslCertValidation=True)
    c = si.RetrieveContent()
    return {x.name: x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view}


def shape(v):
    return (v.config.hardware.numCPU, v.config.hardware.memoryMB,
            sorted((d.capacityInKB // 1048576 for d in v.config.hardware.device if isinstance(d, vim.vm.device.VirtualDisk)), reverse=True))


one, two = vms(sys.argv[1]), vms(sys.argv[2])
print(shape(one["ab01xy05a"]) if "ab01xy05a" in one else "-", shape(two["ab01xy05b"]) if "ab01xy05b" in two else "-")
sys.exit(0 if "ab01xy05b" not in one and "ab01xy05a" not in two and shape(one["ab01xy05a"]) == (1, 1024, [40])
         and shape(two["ab01xy05b"]) == (2, 2048, [60, 10]) else 1)
EOPY
unset GENAI_KEY FAKE_ACT_OUT FAKE_ACT_CONFIG
# ---- the service picked at launch (vm_deploy_service): words in the VM's notes, each VM built joins its AAP
# inventory and group (a fake AAP: tests/servicenow/aapmock.py - inventory "Linux servers" has a source with Overwrite on)
aap_port=$((port + 43)); aap_port2=$((port + 44))
AAPMOCK_LOG="$work/aap.jsonl" AAPMOCK_INV_LOG="$work/inv.jsonl" AAPMOCK_OVERWRITE=1 "$py" "$repo/tests/servicenow/aapmock.py" "$aap_port" >/dev/null 2>&1 &
aap_pid=$!
AAPMOCK_LOG="$work/aap2.jsonl" AAPMOCK_INV_LOG="$work/inv2.jsonl" AAPMOCK_INV_FAIL=1 "$py" "$repo/tests/servicenow/aapmock.py" "$aap_port2" >/dev/null 2>&1 &
aap_pid2=$!
for p_ in $aap_port $aap_port2; do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p_") 2>/dev/null && break; sleep 0.1; done; done
svcs='{"vm_deploy_services": {"web": {"inventory": "Linux servers", "group": "web", "note": "Web server for the test."},
       "nogrp": {"inventory": "Linux servers", "group": "nope"}, "test": {"note": "Test box."}}, "vm_deploy_inventory_name": "{name}.example.mil"}'
export CONTROLLER_HOST="http://127.0.0.1:$aap_port" CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=goodpw
nsn() { wc -l < "$work/sn.jsonl"; }
ninv() { cat "$work/inv.jsonl" 2>/dev/null | wc -l; }
patches_since() { tail -n +"$(($1 + 1))" "$work/sn.jsonl" | grep -c '"PATCH"'; }
svc1="$(newticket $'vm_name: svc01\ntemplate: rhel9-gold')"
n1=$(nsn)
run "service: one not in vm_deploy_services - refused before the ticket is read or written" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc1" -e "$rules" -e "$svcs" -e vm_deploy_service=webb \
    -- "Nothing was built or written on the ticket: service webb is not one of vm_deploy_services (nogrp, test, web)"
run "service: its group is not in the AAP inventory - refused before anything is built or written, saying what to create" fail \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$svc1" -e "$rules" -e "$svcs" -e vm_deploy_service=nogrp \
    -- "service nogrp (vm_deploy_services): inventory Linux servers has no group nope: create it in AAP"
(unset CONTROLLER_HOST; run "service with an inventory, no AAP credential: refused before anything is built" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc1" -e "$rules" -e "$svcs" -e vm_deploy_service=web \
    -- 'No AAP connection: attach a credential of type \"Red Hat Ansible Automation Platform\"') || fails=$((fails + 1))
[ "$(patches_since "$n1")" = 0 ] && echo "ok   -   ...nothing written on the ticket" || { echo "FAIL -   ...a refused service wrote on the ticket"; fails=$((fails + 1)); }
run "service, dry run: what it would do in AAP, the Overwrite warning; nothing added" ok vm_deploy_from_ticket.yml --check \
    -e vm_deploy_ticket="$svc1" -e "$rules" -e "$svcs" -e vm_deploy_service=WEB \
    -- "AAP inventory Linux servers, group web: would be added as svc01.example.mil" \
       "WARNING: inventory Linux servers is filled by source core file with Overwrite on" 'Service: web - in the VM notes: \"Web server for the test.\"'
[ "$(ninv)" = 0 ] && echo "ok   -   ...nothing added in AAP" || { echo "FAIL -   ...the dry run added to AAP"; fails=$((fails + 1)); }
n1=$(nsn)
run "service web: svc01 built, its notes say the service, it joins AAP inventory Linux servers, group web" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc1" -e "$rules" -e "$svcs" -e vm_deploy_service=web \
    -- "VM deploy $svc1: svc01 deployed" "AAP inventory Linux servers, group web: added as svc01.example.mil"
"$py" - "$work/inv.jsonl" "$svc1" <<'EOPY' && echo "ok   -   ...AAP: added with its ticket and service, then joined the group" || { echo "FAIL -   ...AAP inventory"; cat "$work/inv.jsonl"; fails=$((fails + 1)); }
import json, sys
log = [json.loads(l) for l in open(sys.argv[1])]
a = log[0]
sys.exit(0 if len(log) == 2 and a["added"] == "svc01.example.mil" and a["inventory"] == 7 and a["variables"]["deployed_ticket"] == sys.argv[2]
         and a["variables"]["deployed_service"] == "web" and "ansible_host" not in a["variables"] and a["description"].startswith("Built by AAP job 4711")
         and log[1]["joined"] == "web" else 1)
EOPY
VMWARE_PORT=$port "$py" - "$svc1" <<'EOPY' && echo "ok   -   ...the VM's notes: the ticket's line, then the template, the job and the service, one line each" || { echo "FAIL -   ...VM notes"; fails=$((fails + 1)); }
import os, re, sys
from pyVim.connect import SmartConnect
from pyVmomi import vim
si = SmartConnect(host="127.0.0.1", port=int(os.environ["VMWARE_PORT"]), user="user", pwd="pass", disableSslCertValidation=True)
c = si.RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "svc01"][0]
a = v.config.annotation or ""
lines = a.split("\n")
print(lines)
sys.exit(0 if len(lines) == 4 and re.match(r"^%s - built \d{4}-\d{2}-\d{2}$" % re.escape(sys.argv[1]), lines[0])
         and lines[1:] == ["Template: rhel9-gold", "AAP job 4711 (operator1)", "Service: Web server for the test."] else 1)
EOPY
"$py" - "$work/sn.jsonl" "$n1" <<'EOPY' && echo "ok   -   ...the ticket's result note says the service and the AAP inventory" || { echo "FAIL -   ...result note"; fails=$((fails + 1)); }
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in list(open(sys.argv[1]))[int(sys.argv[2]):] if '"PATCH"' in l]
r = [n for n in notes if n.startswith("[AAP] VM svc01 deployed")]
sys.exit(0 if r and "\nService: web\nAAP inventory Linux servers, group web: added as svc01.example.mil" in r[-1] else 1)
EOPY
n2=$(ninv)
run "service web, the same ticket again: svc01 left as it is, already in AAP - nothing added twice" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc1" -e "$rules" -e "$svcs" -e vm_deploy_service=web \
    -- "AAP inventory Linux servers, group web: svc01.example.mil is in it already"
[ "$(ninv)" = "$n2" ] && echo "ok   -   ...nothing added in AAP" || { echo "FAIL -   ...added twice"; fails=$((fails + 1)); }
svc2="$(newticket $'vm_name: svc02, app01\ntemplate: rhel9-gold')"
n2=$(ninv)
run "service, two VMs, vCenter refuses one (its name exists): nothing built, nothing added in AAP" fail vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc2" -e "$rules" -e "$svcs" -e vm_deploy_service=web \
    -- "Nothing was built: app01: a VM named app01 already exists"
[ "$(ninv)" = "$n2" ] && echo "ok   -   ...nothing added in AAP" || { echo "FAIL -   ...added though nothing was built"; fails=$((fails + 1)); }
svc5="$(newticket $'vm_name: svc02, svc05\ntemplate: rhel9-gold')"
n1=$(nsn)
run "service, two VMs: each joins AAP right after its build, each note says so" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc5" -e "$rules" -e "$svcs" -e vm_deploy_service=web \
    -- "svc02 - AAP inventory Linux servers, group web: added as svc02.example.mil" "svc05 - AAP inventory Linux servers, group web: added as svc05.example.mil"
"$py" - "$work/sn.jsonl" "$n1" <<'EOPY' && echo "ok   -   ...each VM's note has its own AAP line" || { echo "FAIL -   ...per-VM notes"; fails=$((fails + 1)); }
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in list(open(sys.argv[1]))[int(sys.argv[2]):] if '"PATCH"' in l]
ok = all(any(n.startswith("[AAP] VM %s deployed" % v) and "\nAAP inventory Linux servers, group web: added as %s.example.mil" % v in n for n in notes)
         for v in ("svc02", "svc05"))
sys.exit(0 if ok else 1)
EOPY
svc6="$(newticket $'vm_name: svc06\ntemplate: rhel9-gold')"
n1=$(nsn); n2=$(ninv)
run "service, the workflow's propose step: the plan note says where it would go in AAP; nothing built, nothing added" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$svc6" -e "$rules" -e "$svcs" -e vm_deploy_service=web -e vm_deploy_phase=propose \
    -- "would be added as svc06.example.mil"
"$py" - "$work/sn.jsonl" "$n1" <<'EOPY' && echo "ok   -   ...the plan note" || { echo "FAIL -   ...plan note"; fails=$((fails + 1)); }
import json, sys
notes = [json.loads(l)["body"].get("work_notes", "") for l in list(open(sys.argv[1]))[int(sys.argv[2]):] if '"PATCH"' in l]
sys.exit(0 if any(n.startswith("[AAP] Checked") and "\n- Service: web\n- AAP inventory Linux servers, group web: would be added as svc06.example.mil" in n
                  for n in notes) else 1)
EOPY
if [ "$(ninv)" = "$n2" ] && ! state svc06 "1" >/dev/null 2>&1; then echo "ok   -   ...nothing built or added"; else echo "FAIL -   ...the propose step built or added"; fails=$((fails + 1)); fi
svc3="$(newticket $'vm_name: svc03\ntemplate: rhel9-gold')"
CONTROLLER_HOST="http://127.0.0.1:$aap_port2" run "service, AAP refuses the host (HTTP 403): the VM is built, the ticket closed, a WARNING says add it by hand" ok \
    vm_deploy_from_ticket.yml -e vm_deploy_ticket="$svc3" -e "$rules" -e "$svcs" -e vm_deploy_service=web \
    -e '{"vm_deploy_done_fields": {"state": "6", "close_notes": "VM deployed"}}' \
    -- "VM deploy $svc3: svc03 deployed" "WARNING: AAP inventory Linux servers, group web: NOT added - AAP refused to add it to inventory Linux servers (HTTP 403)" \
       "Add svc03.example.mil by hand" "Ticket closed: close_notes = VM deployed, state = 6"
"$py" - "$work/sn.jsonl" "$svc3" <<'EOPY' && echo "ok   -   ...said on the ticket, and the ticket closed after it" || { echo "FAIL -   ...403 note / close"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if '"PATCH"' in l]
rows = rows[-3:]
note = [r for r in rows if "NOT added - AAP refused" in r["body"].get("work_notes", "")]
close = [r for r in rows if r["body"].get("state") == "6"]
sys.exit(0 if note and close and rows.index(note[0]) < rows.index(close[0]) else 1)
EOPY
svc4="$(newticket $'vm_name: svc04\ntemplate: rhel9-gold')"
(unset CONTROLLER_HOST; run "service with the notes only (no inventory): no AAP credential needed" ok vm_deploy_from_ticket.yml \
    -e vm_deploy_ticket="$svc4" -e "$rules" -e "$svcs" -e vm_deploy_service=test -- "VM deploy $svc4: svc04 deployed" 'Service: test - in the VM notes: \"Test box.\"') \
    || fails=$((fails + 1))
check "  ...its notes have the service's words on their own line, last" svc04 "v['annotation'].split('\\n')[1:]" "['Template: rhel9-gold', 'AAP job 4711 (operator1)', 'Service: Test box.']"
kill "$aap_pid" "$aap_pid2" 2>/dev/null
unset CONTROLLER_HOST CONTROLLER_USERNAME CONTROLLER_PASSWORD

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
# network drops: vcsim's 5-minute statistics are at level 1 and its counters have no samples - the last hour is read,
# every host and powered-on VM, nothing dropped, and the note says how to get the past day
if grep -q "^Network drops and errors" "$work/hl.txt" && grep -q "[0-9]* host(s) and [0-9]* powered-on VM(s) read" "$work/hl.txt" \
   && grep -q "the last hour (the hosts' real-time statistics)" "$work/hl.txt" && grep -q "the 5-minute interval to level 2" "$work/hl.txt" \
   && grep -q "^- No dropped packets and no network errors." "$work/hl.txt" && ! grep -q "network: [0-9]* warning" "$work/hl.txt"; then
    echo "ok   -   ...the network drops and errors: every host and VM read from vCenter's statistics, the level-1 note"
else
    echo "FAIL -   ...network drops section"; sed -n '/^Network drops/,/^$/p' "$work/hl.txt" | head -20; fails=$((fails + 1))
fi
# the trust store report: vcsim has neither the certificate-management REST API nor ListCACertificates, so every
# store is "not read" and the job fails saying so (a real vCenter answers both); no credential refused
run "trust store report: nothing readable on vcsim - every store not read, the job fails and says so" fail vmware_trust_report.yml \
    -- "VMware trust store report: nothing could be read - 0 vCenters, 0 ESXi hosts read" "not read" \
       "no certificate API on vCenter" "does not implement: ListCACertificates" "nothing could be read:"
run "trust store report, Check mode with the removal asked: the same, nothing changed" fail vmware_trust_report.yml --check \
    -e check_trust_remove=true -- "nothing could be read" "changed=0"
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

# ---- VMware certificate renewal: vCenter's and the hosts' certificates (vcsim's expire in 2084 - the days
# to expiry raised to make them due, as a site tries the job), the incidents and the CSRs in a fake ServiceNow.
# DC0_C0_H2 may be disconnected by now (the alarms report's events): the hosts used are DC0_H0, DC0_C0_H0, DC0_C0_H1 ----
csn_port=$((port + 45)); csn_port2=$((port + 46))
SNMOCK_LOG="$work/csn.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$csn_port" >/dev/null 2>&1 &
csn_pid=$!
SNMOCK_NO_ATTACH=1 SNMOCK_LOG="$work/csn2.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$csn_port2" >/dev/null 2>&1 &
csn_pid2=$!
for p_ in $csn_port $csn_port2; do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p_") 2>/dev/null && break; sleep 0.1; done; done
touch "$work/csn.jsonl" "$work/csn2.jsonl"
cn() { wc -l < "$work/${1:-csn}.jsonl"; }
sent_since() {  # sent_since LOG LINE METHOD PATH-START: how many such requests after that line
    tail -n +"$(($2 + 1))" "$work/$1.jsonl" | "$py" -c "import json,sys; print(sum(1 for l in sys.stdin if json.loads(l)['method'] == '$3' and json.loads(l)['path'].startswith('$4')))"
}
run "VMware certificates, no ServiceNow credential, 30 days: every certificate read, none due" ok vmware_cert_renew.yml \
    -- "VMware certificates: 0 due within 30 days, of " "ESXi host DC0_C0_H0 (vCenter 127.0.0.1)" "vCenter 127.0.0.1 (Machine SSL certificate, https://127.0.0.1:$port)"
export SN_HOST="http://127.0.0.1:$csn_port" SN_USERNAME=api SN_PASSWORD=goodpw
n1=$(cn)
run "the certificate REPORT, every host, the days raised, the ServiceNow credential there: what the renewal would do, nothing sent" ok \
    vmware_cert_report.yml -e vmware_cert_warn_days=30000 \
    -- "VMware certificate report: " "due within 30000 days, of " "Due within 30000 days - what the renewal would do" \
       "open an incident, and make a CSR on the host" "open an incident, and make a CSR on vCenter" "The report only: nothing was sent to ServiceNow"
[ "$(sent_since csn "$n1" GET /)$(sent_since csn "$n1" POST /)$(sent_since csn "$n1" PATCH /)" = 000 ] && ! grep -q "make the CSRs" "$work/out.txt" \
    && echo "ok   -   ...not one request to ServiceNow, no CSR" || { echo "FAIL -   ...the report talked to ServiceNow"; fails=$((fails + 1)); }
run "...the report of one host, 30 days" ok vmware_cert_report.yml -e vmware_cert_only=DC0_H0 -- "VMware certificate report: 0 due within 30 days, of 1"
run "...the certificate inventory files (cert_inventory_files): vCenter and every host, with fingerprints and tier" ok vmware_cert_report.yml \
    -e cert_inventory_files=true -e "cert_inventory_dir=$work/inventory" -- "certificates (vmware) in $work/inventory/vmware-certificates.csv"
"$py" - "$work/inventory/vmware-certificates.json" <<'EOPY' && echo "ok   -   ...each record: host, location, issuer, key, sha1, sha256, tier, in use" || { echo "FAIL -   ...the inventory records"; fails=$((fails + 1)); }
import json, sys
recs = json.load(open(sys.argv[1]))
errs = []
if not any(r["location"].startswith("vCenter Machine SSL") for r in recs): errs.append("no vCenter record")
if not any(r["location"].startswith("ESXi host (vCenter") for r in recs): errs.append("no ESXi record")
for r in recs:
    if r["source"] != "vmware" or not r["host"] or len(r["sha1"]) != 40 or len(r["sha256"]) != 64 or not r["public_key"] \
       or r["tier"] not in ("root", "intermediate", "ca-signed", "self-signed") or r["in_use"] is not True or not r["sans"] == [x for x in r["sans"] if ":" in x]:
        errs.append("record: %s" % r); break
print("\n".join("       " + e for e in errs))
sys.exit(1 if errs else 0)
EOPY
run "...with ServiceNow, 30 days: no incident" ok vmware_cert_renew.yml -- "No findings at or above warning."
[ "$(sent_since csn "$n1" POST /)" = 0 ] && echo "ok   -   ...nothing opened" || { echo "FAIL -   ...opened at 30 days"; fails=$((fails + 1)); }
cdays='{"vmware_cert_warn_days": 30000, "vmware_cert_only": "dc0_h0"}'
run "VMware certificates, dry run with the days raised (every one due) and one host: what it would do, nothing sent" ok vmware_cert_renew.yml --check \
    -e "$cdays" -- "would open: DC0_H0: ESXi host certificate" "DC0_H0 | DC0_H0.csr | would make the CSR, and would open a ticket and attach it" \
    "VMware certificates (dry run): 1 due within 30000 days, of 1" "Left out (vmware_cert_only)"
[ "$(sent_since csn "$n1" POST /)$(sent_since csn "$n1" PATCH /)" = 00 ] && echo "ok   -   ...nothing sent" || { echo "FAIL -   ...the dry run sent"; fails=$((fails + 1)); }
run "...the incident only (vmware_cert_make_csr: false): opened, no CSR made" ok vmware_cert_renew.yml -e "$cdays" -e vmware_cert_make_csr=false \
    -- "opened: INC" "SKIPPED | DC0_H0 | ESXi host DC0_H0 (vCenter 127.0.0.1) | vmware_cert_make_csr: false - the incident only"
"$py" - "$work/csn.jsonl" "$n1" <<'EOPY' && echo "ok   -   ...one incident: the host, check vmware_certs, the days used, where to look" || { echo "FAIL -   ...the incident"; fails=$((fails + 1)); }
import json, sys
rows = [json.loads(l) for l in list(open(sys.argv[1]))[int(sys.argv[2]):]]
new = [r["body"] for r in rows if r["method"] == "POST" and r["path"] == "/api/now/table/incident"]
d = new[0]["description"] if len(new) == 1 else ""
sys.exit(0 if new[0]["short_description"].startswith("[site-health] DC0_H0: ESXi host certificate") and "Check: vmware_certs" in d
         and "vmware_cert_warn_days (30000 days)" in d and "Configure > System > Certificate" in d and ":vmware_certs:" in new[0]["correlation_id"]
         and not [r for r in rows if r["path"].startswith("/api/now/attachment/file")] else 1)
EOPY
n1=$(cn)
run "...then with the CSR: the host makes it, it is attached to the same incident with what to do next" ok vmware_cert_renew.yml -e "$cdays" \
    -- "updated: INC" "| DC0_H0 | DC0_H0-" ".csr | attached" "CSR made"
"$py" - "$work/csn.jsonl" "$n1" <<'EOPY' && echo "ok   -   ...the CSR attached, the work note: the host keeps the key, ESXi's install steps" || { echo "FAIL -   ...CSR attachment / note"; fails=$((fails + 1)); }
import json, re, sys
rows = [json.loads(l) for l in list(open(sys.argv[1]))[int(sys.argv[2]):]]
att = [r for r in rows if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
notes = [r["body"].get("work_notes", "") for r in rows if r["method"] == "PATCH"]
note = [n for n in notes if n.startswith("Certificate renewal")]
sys.exit(0 if len(att) == 1 and re.match(r"^DC0_H0-[0-9a-f]{8}\.csr$", att[0]["query"]["file_name"]) and "BEGIN CERTIFICATE REQUEST" in att[0]["body"]["raw"]
         and not [r for r in rows if r["method"] == "POST" and r["path"] == "/api/now/table/incident"]
         and note and "New key: made by the host and kept on it" in note[0] and "CSR is generated by ESXi (Private key embedded)" in note[0]
         and "vpxd.certmgmt.mode to custom" in note[0] else 1)
EOPY
n1=$(cn)
run "...again: its CSR is on the incident - no second CSR (it would replace the key that waits)" ok vmware_cert_renew.yml -e "$cdays" \
    -- "already attached" "CSR on the ticket already"
grep -q "make the CSRs.*\]" "$work/out.txt" && grep -A1 "make the CSRs" "$work/out.txt" | grep -q skipping && [ "$(sent_since csn "$n1" POST /api/now/attachment)" = 0 ] \
    && echo "ok   -   ...no CSR made, nothing attached" || { echo "FAIL -   ...a second CSR"; fails=$((fails + 1)); }
n1=$(cn)
run "...back to 30 days: the test incident is noted as cleared" ok vmware_cert_renew.yml -- "noted as cleared: INC"
[ "$(sent_since csn "$n1" POST /)" = 0 ] && echo "ok   -   ...nothing opened" || { echo "FAIL -   ...opened at 30 days"; fails=$((fails + 1)); }
run "VMCA-signed (vmware_cert_vmca_issuers): an incident that says renew it in vCenter, no CSR" ok vmware_cert_renew.yml \
    -e '{"vmware_cert_warn_days": 30000, "vmware_cert_only": "DC0_C0_H1", "vmware_cert_vmca_issuers": ["*acme co*"]}' \
    -- "opened: INC" "SKIPPED | DC0_C0_H1 | ESXi host DC0_C0_H1 (vCenter 127.0.0.1) | signed by vCenter's own CA (VMCA): renew it in" "VMCA (vCenter's own CA)"
run "vCenter's own certificate: the simulator has no certificate API - the job fails, saying so (its incident is open)" fail vmware_cert_renew.yml \
    -e '{"vmware_cert_warn_days": 30000, "vmware_cert_only": "127.0.0.1"}' \
    -- "opened: INC" "vCenter 127.0.0.1 has no certificate API" "Machine SSL Certificate > Generate Certificate Signing Request"
SN_HOST="http://127.0.0.1:$csn_port2" run "the attachment refused (as .csr and as .txt): the CSR goes in the work note, the job fails" fail \
    vmware_cert_renew.yml -e '{"vmware_cert_warn_days": 30000, "vmware_cert_only": "DC0_C0_H0"}' -- "the attachment was refused (HTTP 403), as a .txt file too"
n1=$(cn csn2)
SN_HOST="http://127.0.0.1:$csn_port2" run "...again: the CSR in the work note counts - no second CSR" ok vmware_cert_renew.yml \
    -e '{"vmware_cert_warn_days": 30000, "vmware_cert_only": "DC0_C0_H0"}' -- "CSR on the ticket already"
[ "$(sent_since csn2 "$n1" POST /api/now/attachment)" = 0 ] && echo "ok   -   ...nothing attached again" || { echo "FAIL -   ...a second CSR"; fails=$((fails + 1)); }
(unset SN_HOST; run "no ServiceNow credential, certificates due: no CSR (nothing would record it)" ok vmware_cert_renew.yml -e "$cdays" \
    -- "NOT CONNECTED - what would be opened" "no ServiceNow API credential: the CSR is made only with its ticket") || fails=$((fails + 1))
(unset SN_HOST; run "...vmware_cert_csr_without_servicenow: true makes it and prints it" ok vmware_cert_renew.yml -e "$cdays" \
    -e vmware_cert_csr_without_servicenow=true -- "NOT CONNECTED - the CSRs to hand to the PKI" "BEGIN CERTIFICATE REQUEST") || fails=$((fails + 1))
run "a near-miss setting name (vm_cert_warn_days): refused before anything is read, naming the right one" fail vmware_cert_report.yml \
    -e vm_cert_warn_days=498 -- "vm_cert_warn_days: not a setting of this job" "vmware_cert_warn_days"
run "vmware_cert_only that matches nothing: refused, nothing sent" fail vmware_cert_renew.yml -e vmware_cert_only=nosuchhost \
    -- "vmware_cert_only (nosuchhost) matches none of the vCenters and hosts read"
(unset SN_HOST; RUN_PORT=$port3 run "a host that is not connected: listed as not checked, the job stays green" ok vmware_cert_renew.yml \
    -- "1 not checked" "DC0_C1_H1" "not connected (disconnected)") || fails=$((fails + 1))
# cert_renew_servicenow: catalog - the CSR ordered from the PKI's catalog item, no incident; the request is the CSR's record
csn_port3=$((port + 47))
SNMOCK_LOG="$work/csn3.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$csn_port3" >/dev/null 2>&1 &
csn_pid3=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$csn_port3") 2>/dev/null && break; sleep 0.1; done
: > "$work/csn3.jsonl"
export SN_HOST="http://127.0.0.1:$csn_port3"
ccat='{"vmware_cert_warn_days": 30000, "vmware_cert_only": "DC0_H0", "cert_renew_servicenow": "catalog",
       "cert_renew_catalog_item": "DoD PKI Certificate Request", "cert_renew_catalog_variables": {"requested_for": "jdoe", "contact_method": "email"}}'
run "catalog, dry run: it would make the CSR and order it - no incident, nothing sent" ok vmware_cert_renew.yml --check -e "$ccat" \
    -- "DC0_H0 | DC0_H0.csr | would make the CSR, and order it"
[ "$(sent_since csn3 0 POST /)$(sent_since csn3 0 PATCH /)" = 00 ] && echo "ok   -   ...nothing sent" || { echo "FAIL -   ...the dry run sent"; fails=$((fails + 1)); }
run "catalog: the host makes the CSR, it is ordered - one request, the CSR in its question, no incident" ok vmware_cert_renew.yml -e "$ccat" \
    -- "ORDERED RITM" "| DC0_H0 | DC0_H0-" "the CSR is in the request (its question csr_file)"
"$py" - "$work/csn3.jsonl" <<'EOPY' && echo "ok   -   ...one order; the CSR named for the host and the certificate's expiry; no incident" || { echo "FAIL -   ...the order"; fails=$((fails + 1)); }
import json, re, sys
rows = [json.loads(l) for l in open(sys.argv[1])]
orders = [r for r in rows if r["path"].endswith("/order_now")]
ups = [r for r in rows if r["method"] == "POST" and r["path"] == "/api/now/attachment/file"]
sys.exit(0 if len(orders) == 1 and orders[0]["body"]["variables"].get("csr_file") and len(ups) == 1
         and re.match(r"^DC0_H0-[0-9a-f]{8}-exp[0-9]{8}\.csr$", ups[0]["query"]["file_name"]) and "BEGIN CERTIFICATE REQUEST" in ups[0]["body"]["raw"]
         and not [r for r in rows if "/table/incident" in r["path"]] else 1)
EOPY
ritm_v="$(grep -oE 'ORDERED RITM[0-9]+' "$work/out.txt" | head -1 | grep -oE 'RITM[0-9]+')"
n1=$(cn csn3)
run "catalog, again: its request is the CSR's record - no second CSR, no second order" ok vmware_cert_renew.yml -e "$ccat" \
    -- "its CSR is on $ritm_v (Open, attached: DC0_H0-" "ORDERED ALREADY | DC0_H0"
grep -A1 "make the CSRs" "$work/out.txt" | grep -q skipping && [ "$(sent_since csn3 "$n1" POST /)" = 0 ] \
    && echo "ok   -   ...no CSR made, nothing sent" || { echo "FAIL -   ...a second CSR or order"; fails=$((fails + 1)); }
sid_v="$(curl -s -u api:goodpw "$SN_HOST/api/now/table/sc_req_item?sysparm_query=number=$ritm_v" | "$py" -c 'import json, sys; print(json.load(sys.stdin)["result"][0]["sys_id"])')"
curl -s -u api:goodpw -X PATCH -H 'Content-Type: application/json' -d '{"state": "7"}' "$SN_HOST/api/now/table/sc_req_item/$sid_v" >/dev/null
n1=$(cn csn3)
run "catalog, its request cancelled (Closed Skipped): a new CSR (a new key on the host), and a new order" ok vmware_cert_renew.yml -e "$ccat" \
    -- "ORDERED RITM" "| DC0_H0 | DC0_H0-" "the CSR is in the request"
[ "$(sent_since csn3 "$n1" POST /api/sn_sc/servicecatalog)" = 1 ] && ! grep -q "ORDERED RITM.*$ritm_v" "$work/out.txt" \
    && echo "ok   -   ...one new order, not the cancelled one" || { echo "FAIL -   ...the new order"; fails=$((fails + 1)); }
unset SN_HOST SN_USERNAME SN_PASSWORD
kill "$csn_pid" "$csn_pid2" "$csn_pid3" 2>/dev/null

# ---- the vCenter log analysis: vcsim has no DiagnosticManager, so it hands out no log - the job says so for
# vCenter and each host and fails after its report (tests/test_log_analysis.py reads made-up vpxd / hostd /
# vmkernel lines through a stand-in DiagnosticManager) ---------------------------------------------------------
run "vCenter log analysis against vcsim (no logs through its API): said for vCenter and each host, reported, fails" fail vm_log_analysis.yml \
    -e log_analysis_act=false \
    -- "does not hand out logs through its API (DiagnosticManager not implemented)" "vCenter log analysis: no warning or error" \
       "host(s) not read" "No host's logs could be read"
run "vCenter log analysis: a wrong window is refused before vCenter is read" fail vm_log_analysis.yml -e log_analysis_until=soon \
    -- "must look like 2026-10-23 08:00"

# ---- the VM outage watch (playbooks/vm_outage_watch.yml) ---------------------------------------------------
# a fresh simulator; the suspects checked twice, 1 s apart; vcsim's VMs have just booted (no minimum up time).
# The fake IP addresses (192.0.2.x) do not answer a ping: a VM with no heartbeat there is hung. One at 127.0.0.1 answers.
fresh
wt='{"vm_watch_confirm_seconds": 1, "vm_watch_probe_timeout": 1, "vm_watch_min_up_minutes": 0}'
stats() {  # stats FILE: the job's artifacts (the CUSTOM STATS line of the last run) as an extra-vars file
    "$py" - "$work/out.txt" "$1" <<'EOPY'
import json, sys
line = [l for l in open(sys.argv[1]) if l.strip().startswith("RUN: {")][-1]
d = json.loads(line.strip()[len("RUN: "):])
json.dump({"site_health": d["site_health"], "site_health_targets": d["site_health_targets"]}, open(sys.argv[2], "w"))
EOPY
}
run "outage watch, nothing down: one line, green" ok vm_outage_watch.yml -e "$wt" \
    -- "No outage: 4 host(s), 1 datastore(s), 4 powered-on VM(s) checked (vCenter 127.0.0.1)." '"site_health_targets": []'
grep -qF "VMware | email it" "$work/out.txt" && { echo "FAIL -   ...a clean run must not report"; fails=$((fails + 1)); } || echo "ok   -   ...no report, no email"
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_C0_RP0_VM1 guestHeartbeatStatus=red guest.toolsStatus=toolsOk guest.ipAddress=127.0.0.1
run "outage watch: no heartbeat but answers a ping - one signal, a warning, green, not reported (vm_watch_report_on)" ok vm_outage_watch.yml -e "$wt" \
    -- "WARNING VM DC0_C0_RP0_VM1 has no guest heartbeat, but it answers on the network: not called hung (one signal only)"
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_H0_VM1 guestHeartbeatStatus=red guest.toolsStatus=toolsOk
run "outage watch: a hung VM (no heartbeat, no answer, in both checks) - reported, red, nothing restarted" fail vm_outage_watch.yml -e "$wt" \
    -- "VM outage watch: 1 critical, 1 warning" "VM DC0_H0_VM1 is hung: no guest heartbeat (red); no answer at 192.0.2.20, in 2 check(s)" \
       "Would be restarted in phase 2 (nothing was done)" "Nothing was restarted (watch only)" '"id": "vm-watch:hung:DC0_H0_VM1"'
check "  ...and it is still on" DC0_H0_VM1 "v['power']" poweredOn
stats "$work/watch-hung.json"
run "outage watch: a hung VM in vm_watch_protect - a ticket only" fail vm_outage_watch.yml -e "$wt" -e '{"vm_watch_protect": ["dc0_h0_*"]}' \
    -- "Protected: ticket only" "DC0_H0_VM1  127.0.0.1  DC0_H0  vmware_protected_vms / vm_watch_protect"
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_H0_VM0 guestHeartbeatStatus=red guest.toolsStatus=toolsOk
run "outage watch: the AAP server hung - never restarted, a ticket only" fail vm_outage_watch.yml -e "$wt" \
    -- "VM DC0_H0_VM0 is hung" "the AAP server, or a host of vmware_protected_groups"
INV="$work/inv-noaap.yml" run "outage watch: the inventory cannot tell which VM is AAP - nothing would be restarted" fail vm_outage_watch.yml -e "$wt" \
    -- "does not say which VM is the AAP server"
run "outage watch as a dry run (Check): the same report" fail vm_outage_watch.yml --check -e "$wt" \
    -- "VM outage watch: 2 critical, 1 warning" "VM DC0_H0_VM1 is hung"
run "outage watch with vm_watch_fail_on: [] - reported, green" ok vm_outage_watch.yml -e "$wt" -e '{"vm_watch_fail_on": []}' \
    -- "VM outage watch: 2 critical, 1 warning"
run "outage watch with vm_watch_skip_vms: the skipped VMs are not judged" ok vm_outage_watch.yml -e "$wt" \
    -e '{"vm_watch_skip_vms": ["DC0_H0_*", "dc0_c0_rp0_vm1"]}' -- "No outage: 4 host(s), 1 datastore(s), 1 powered-on VM(s) checked"
run "outage watch with vm_watch_probe: false - no VM is hung (one signal only)" ok vm_outage_watch.yml -e "$wt" -e vm_watch_probe=false \
    -- "it could not be probed (unavailable)"
# the tickets job after it, in a fake ServiceNow: one incident per condition; resolved once the watch is clean
wsn_port=$((port + 53))
SNMOCK_LOG="$work/wsn.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$wsn_port" >/dev/null 2>&1 &
wsn_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$wsn_port") 2>/dev/null && break; sleep 0.1; done
export SN_HOST="http://127.0.0.1:$wsn_port" SN_USERNAME=api SN_PASSWORD=goodpw
run "outage watch -> ServiceNow tickets: an incident for the hung VM, one for the warning" fail servicenow_tickets.yml -e "@$work/watch-hung.json" \
    -e servicenow_resolve_cleared=true -- "opened: INC0010001, INC0010002" "2 finding(s) on 127.0.0.1"
grep -qF '[site-health] 127.0.0.1: VM DC0_H0_VM1 is hung: no guest heartbeat (red); no answer at 192.0.2.20, in 2 check(s)' "$work/wsn.jsonl" \
    && echo "ok   -   ...the incident says which VM, and why" || { echo "FAIL -   ...the incident's short description"; fails=$((fails + 1)); }
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_H0_VM1 guestHeartbeatStatus=green guest.toolsRunningStatus=guestToolsRunning
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_H0_VM0 guestHeartbeatStatus=green guest.toolsRunningStatus=guestToolsRunning
VMWARE_PORT=$port "$py" "$here/vcsim.py" guest DC0_C0_RP0_VM1 guestHeartbeatStatus=green guest.toolsRunningStatus=guestToolsRunning
run "outage watch: the VMs have their heartbeat back - clean again" ok vm_outage_watch.yml -e "$wt" -- "No outage: 4 host(s)"
stats "$work/watch-clean.json"
run "outage watch -> ServiceNow tickets: the clean run resolves the incident" ok servicenow_tickets.yml -e "@$work/watch-clean.json" \
    -e servicenow_resolve_cleared=true -- "resolved: INC0010001, INC0010002"
unset SN_HOST SN_USERNAME SN_PASSWORD
kill "$wsn_pid" 2>/dev/null
VMWARE_PORT=$port "$py" "$here/vcsim.py" watch
run "outage watch: all paths down (not cleared), an HA restart, a VMFS heartbeat that recovered" fail vm_outage_watch.yml -e "$wt" \
    -- "all paths down on host DC0_C0_H0 (device naa.600a098038303053)" ", not cleared" \
       "VM DC0_C0_RP0_VM0: HA restarted the VM on another host" "VMFS heartbeat timed out on host DC0_C0_H1" ", cleared at"
run "outage watch with vm_watch_window_minutes: 0 - no events read" ok vm_outage_watch.yml -e "$wt" -e vm_watch_window_minutes=0 -- "No outage"
VMWARE_PORT=$port "$py" "$here/vcsim.py" events >/dev/null
run "outage watch: a host disconnected" fail vm_outage_watch.yml -e "$wt" -e vm_watch_window_minutes=0 \
    -- "ESXi host DC0_C0_H2 is disconnected (cluster DC0_C0): its VMs may be down"

if [ "$fails" -eq 0 ]; then echo "ok   - all VMware scenarios passed"; else echo "$fails FAILED"; exit 1; fi
