#!/usr/bin/env bash
# Patch hosts with the VMware snapshot before patching (playbooks/patch_hosts.yml, patch_snapshot): real hosts - UBI 9
# containers running systemd, patched with dnf over the podman connection - and their VMs in vcsim, VMware's vCenter
# simulator. Each host's VM found once before anything is patched (by its guest host name, its VM name, vmware_vm_name);
# a host matching two VMs stops the job before any host is patched, and so does a host without a VM when a vCenter was
# not read or patch_snapshot_required is true; the snapshot "patching" (no memory, the
# job and user in its description) taken in each batch right before that batch's update; the AAP host never; a dry run
# takes none; an old "patching" stops the next run, patch_snapshot_replace replaces it; a host in no vCenter is a
# physical server, patched without a snapshot; no vCenter credential; patch_snapshot false needs no vCenter; a host on the VMware
# protected lists is not patched, unless patch_protected_with_snapshot names it (then after its own snapshot).
#   bash tests/patch/run_patch_snapshot_test.sh
# Needs: podman; registry.access.redhat.com/ubi9/ubi-init (pulled when missing) and its UBI repositories (internet);
# a python with pyVmomi for the vcsim helpers (VMWARE_TEST_PYTHON); pyVmomi and requests in ansible-playbook's own
# python too (the snapshot tasks run there, as in AAP's execution environment); the vmware.vmware collection
# (VMWARE_COLLECTIONS, default ~/.ansible/collections); the containers.podman collection (installed into a temporary
# folder from tests/patch/requirements.yml unless PATCH_TEST_COLLECTIONS points at one with it).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
vmw="$repo/tests/vmware"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${VMWARE_TEST_PYTHON:-$(command -v python3)}"
engine="$(command -v podman)" || { echo "ABORT - podman is needed (the podman connection)"; exit 1; }
port="${VCSIM_PORT:-18999}"
image="docker.io/vmware/vcsim:v0.52.0"
host_image="${PATCH_TEST_IMAGE:-registry.access.redhat.com/ubi9/ubi-init:latest}"
name="vcsim-patch-test-$$"
hosts=("sa-patch-a-$$" "sa-patch-b-$$" "sa-patch-c-$$")
work="$(mktemp -d)"
cleanup() { "$engine" rm -f -t 0 "$name" "${hosts[@]}" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0

"$py" -c 'import pyVmomi' 2>/dev/null || { echo "ABORT - $py has no pyVmomi (pip install pyvmomi)"; exit 1; }
cols="${PATCH_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$here/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi
cols="$cols:${VMWARE_COLLECTIONS:-$HOME/.ansible/collections}"

for h in "${hosts[@]}"; do
    "$engine" run -d --name "$h" --systemd=always "$host_image" /sbin/init >/dev/null || { echo "ABORT - could not start $host_image"; exit 1; }
done
for h in "${hosts[@]}"; do  # systemd up (the patch compares the running services before and after)
    for _ in $(seq 60); do "$engine" exec "$h" systemctl is-system-running 2>/dev/null | grep -qE 'running|degraded' && break; sleep 1; done
done

# web01: its VM DC0_H0_VM1 by the guest host name (vcsim.py); DC0_C0_RP0_VM1: by its VM name; web02: vmware_vm_name.
# phys01: no VM. dup: two VMs of that name. aap01: the AAP host - never patched, so never a snapshot.
c="ansible_connection: containers.podman.podman, ansible_python_interpreter: /usr/bin/python3, ansible_become: false"
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
  children:
    aap:
      hosts:
        aap01.example.mil: {ansible_host: 192.0.2.5}
    linux:
      hosts:
        web01.example.mil: {ansible_host: ${hosts[0]}, $c}
        DC0_C0_RP0_VM1: {ansible_host: ${hosts[1]}, $c}
        web02.example.mil: {ansible_host: ${hosts[2]}, $c, vmware_vm_name: dc0_c0_rp0_vm0}
        phys01.example.mil: {ansible_host: ${hosts[0]}, $c}
        dup: {ansible_host: ${hosts[1]}, $c}
        down01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh, ansible_python_interpreter: /usr/bin/python3}
EOI
good='target=web01.example.mil:DC0_C0_RP0_VM1:web02.example.mil:aap01.example.mil'

"$engine" run -d --name "$name" -p "127.0.0.1:$port:8989" "$image" -l 0.0.0.0:8989 -api-version 8.0 -pg 2 >/dev/null \
    || { echo "ABORT - could not start $image"; exit 1; }
VMWARE_PORT=$port "$py" "$vmw/vcsim.py" setup || { echo "ABORT - vcsim setup"; exit 1; }
# vcsim leaves a VM without snapshots without the property at all (pyVmomi: AttributeError 'snapshot'; vCenter answers
# none), and vmware.vmware.vm_snapshot then fails with "snapshot": web02's VM gets one first
"$py" - "$port" <<'EOP' || { echo "ABORT - vcsim: a snapshot of DC0_C0_RP0_VM0"; exit 1; }
import sys
from pyVim.connect import SmartConnect
from pyVim.task import WaitForTask
from pyVmomi import vim
c = SmartConnect(host="127.0.0.1", port=int(sys.argv[1]), user="user", pwd="pass", disableSslCertValidation=True).RetrieveContent()
v = [x for x in c.viewManager.CreateContainerView(c.rootFolder, [vim.VirtualMachine], True).view if x.name == "DC0_C0_RP0_VM0"][0]
WaitForTask(v.CreateSnapshot_Task(name="baseline", description="test fixture", memory=False, quiesce=False))
EOP

snaps() {  # snaps VM -> its snapshots' names (and with ID=1 their IDs), comma-separated
    VMWARE_PORT=$port "$py" "$vmw/vcsim.py" state | "$py" -c "import json,sys,os; v=json.load(sys.stdin)['$1'][0]
print(','.join(s['name'] + (':%s' % s['id'] if os.environ.get('ID') else '') for s in v['snapshots']))"
}
# run NAME EXPECT(ok|fail) [args...] then TEXT... after '--'; a TEXT starting with ! must NOT be in the output
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && VMWARE_HOST=127.0.0.1 VMWARE_PORT=$port VMWARE_USER=user VMWARE_PASSWORD=pass \
        ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_HOST_KEY_CHECKING=False \
        ${NOVC:+env -u VMWARE_HOST} "$ap" -i "$work/inv.yml" playbooks/patch_hosts.yml -e vmware_validate_certs=false \
        -e '{"awx_job_id": 4711, "awx_user_name": "operator1", "patch_packages": ["tzdata"]}' "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        keep "$tname"; echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-400; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            ! grep -qF -- "${t:1}" "$work/out.txt" || { echo "FAIL - $tname: output has: ${t:1}"; fails=$((fails + 1)); return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { keep "$tname"; echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-400; fails=$((fails + 1)); return 1; }
        fi
    done
    echo "ok   - $tname"
}
keep() {  # PATCH_TEST_LOGS=<a folder>: the output of each test that failed is kept there
    [ -n "${PATCH_TEST_LOGS:-}" ] && cp "$work/out.txt" "$PATCH_TEST_LOGS/$(printf '%s' "$1" | tr -c 'A-Za-z0-9' _ | cut -c1-60).txt"
    return 0
}
check() {  # check NAME GOT EXPECTED
    if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi
}
installs='TASK [patch : Patch | install the updates'

run "dry run: each host's VM found, a snapshot would be taken, none is" ok --check -e "$good" -e patch_snapshot=true \
    -- 'web01.example.mil: VM DC0_H0_VM1 (by guest host name)' 'DC0_C0_RP0_VM1: VM DC0_C0_RP0_VM1 (by VM name)' \
       'web02.example.mil: VM DC0_C0_RP0_VM0 (by vmware_vm_name)' 'would take \"patching\" of VM DC0_H0_VM1' '!aap01.example.mil: VM'
check "  ...nothing taken" "$(snaps DC0_H0_VM1)/$(snaps DC0_C0_RP0_VM1)/$(snaps DC0_C0_RP0_VM0)" "baseline/baseline/baseline"

run "patch_snapshot_required true: a host without a VM, and one that matches two VMs: stopped before any host is patched" fail \
    -e "$good:phys01.example.mil:dup" -e patch_snapshot=true -e patch_snapshot_required=true -- 'phys01.example.mil: no VM by its name' \
    'No vCenter has them (physical servers?)' 'dup matches more than one VM (dup (127.0.0.1), dup (127.0.0.1))' \
    'set vmware_vm_name for it' 'Nothing was patched' "!$installs"
check "  ...no snapshot taken" "$(snaps DC0_H0_VM1)/$(snaps DC0_C0_RP0_VM0)" "baseline/baseline"

run "a host without a VM while a vCenter was not read: stopped before any host is patched" fail \
    -e "$good:phys01.example.mil" -e patch_snapshot=true -e "{\"vmware_vcenters\": [\"127.0.0.1:$port\", \"127.0.0.1:1\"]}" \
    -- 'phys01.example.mil: no VM by its name' 'A vCenter was not read, so these may be VMs there' 'Nothing was patched' "!$installs"
check "  ...no snapshot taken" "$(snaps DC0_H0_VM1)/$(snaps DC0_C0_RP0_VM0)" "baseline/baseline"

NOVC=1 run "no vCenter credential: stopped before any host is patched" fail -e "$good" -e patch_snapshot=true \
    -- 'attach a credential of type' "!$installs"

run "patched in batches of 2: each batch's snapshots right before its update; the AAP host skipped" ok -e "$good" \
    -e patch_snapshot=true -e patch_serial=2 -- 'took \"patching\" of VM DC0_H0_VM1' 'took \"patching\" of VM DC0_C0_RP0_VM0' \
    'aap01.example.mil is in aap: never patched' 'PLAY RECAP'
check "  ...each VM has its snapshot" "$(snaps DC0_H0_VM1)/$(snaps DC0_C0_RP0_VM1)/$(snaps DC0_C0_RP0_VM0)" \
    "baseline,patching/baseline,patching/baseline,patching"
order="$(grep -oE 'TASK \[(patch : Patch \| install the updates|vmware_vm : VMware \| snapshot "patching")' "$work/out.txt" | sed 's/.*| //' | tr '\n' ';')"
check "  ...the order: batch 1 snapshot, update; batch 2 snapshot, update" "$order" \
    'snapshot "patching";install the updates;snapshot "patching";install the updates;'
desc="$(VMWARE_PORT=$port "$py" "$vmw/vcsim.py" state | "$py" -c "import json,sys; v=json.load(sys.stdin)['DC0_H0_VM1'][0]
print([s['description'] for s in v['snapshots'] if s['name'] == 'patching'][0].split(' 20')[0])")"
check "  ...its description: the host, the job and the user" "$desc" "Before patching web01.example.mil [AAP job 4711, operator1,"
before="$(ID=1 snaps DC0_H0_VM1)"

run "the next run: the old snapshot stops it before any host is patched" fail -e "$good" -e patch_snapshot=true \
    -- 'DC0_H0_VM1 (web01.example.mil) still has the snapshot \"patching\" from' 'patch_snapshot_replace: true' "!$installs"

run "patch_snapshot_replace: the old one deleted, a new one taken" ok -e "$good" -e patch_snapshot=true -e patch_snapshot_replace=true \
    -- 'the old one was deleted first' 'took \"patching\" of VM DC0_C0_RP0_VM1'
after="$(ID=1 snaps DC0_H0_VM1)"
check "  ...still one \"patching\", a new one" "$(snaps DC0_H0_VM1) $([ "$before" != "$after" ] && echo new)" "baseline,patching new"

run "a host in no vCenter (the default): a physical server, patched without a snapshot" ok -e target=phys01.example.mil \
    -e patch_snapshot=true -- 'a physical server: patched without a snapshot' 'In no vCenter, so a physical server' "$installs"

NOVC=1 run "patch_snapshot false (the default): no vCenter needed, no snapshot" ok -e target=web01.example.mil \
    -- 'none (patch_snapshot is false)' "$installs" '!VMware | read every VM'
check "  ...no new snapshot" "$(ID=1 snaps DC0_H0_VM1)" "$after"

# A host that cannot be reached (down01: nothing listens on its port): left out, the others patched, the job green.
NOVC=1 run "a host not reached: listed, left out, the others patched - the job not failed" ok \
    -e target=web01.example.mil:down01.example.mil \
    -- 'down01.example.mil: NOT REACHED' '1 host(s) not reached, not patched: down01.example.mil' "$installs"
NOVC=1 run "patch_skip_unreachable false: a host not reached fails the job" fail \
    -e target=web01.example.mil:down01.example.mil -e patch_skip_unreachable=false -- 'down01.example.mil'
NOVC=1 run "no host reached at all: the job fails (the machine credential?)" fail -e target=down01.example.mil \
    -- 'No host could be reached' "!$installs"
run "a host not reached, with patch_snapshot: no VM looked for it, no snapshot" ok -e "$good:down01.example.mil" \
    -e patch_snapshot=true -e patch_snapshot_replace=true -- 'down01.example.mil: NOT REACHED' '!down01.example.mil: NO VM' \
    'took \"patching\" of VM DC0_H0_VM1'

# The VMware protected lists: a host whose name, vmware_vm_name or VM matches vmware_protected_vms or
# vm_snapshot_cleanup_exclude_vms is not patched, unless patch_protected_with_snapshot names it - then after its snapshot.
NOVC=1 run "protected (vmware_protected_vms, by vmware_vm_name), no snapshot: not patched" ok -e target=web02.example.mil \
    -e '{"vmware_protected_vms": ["DC0_C0_RP0_VM0"]}' -e patch_protected_with_snapshot=true \
    -- 'web02.example.mil is protected (dc0_c0_rp0_vm0 matches vmware_protected_vms (DC0_C0_RP0_VM0))' \
       'To patch it after a snapshot of its VM' "!$installs"

before="$(ID=1 snaps DC0_H0_VM1)"
run "protected by its VM in vCenter (vm_snapshot_cleanup_exclude_vms): skipped, its old snapshot stops nothing" ok \
    -e target=web01.example.mil -e patch_snapshot=true -e '{"vm_snapshot_cleanup_exclude_vms": "SQL*, DC0_H0_*"}' \
    -- 'web01.example.mil: PROTECTED - not patched (DC0_H0_VM1 matches vm_snapshot_cleanup_exclude_vms (DC0_H0_*))' \
       'patch_protected_with_snapshot does not name it' "!$installs" '!still has the snapshot'
check "  ...no snapshot taken" "$(ID=1 snaps DC0_H0_VM1)" "$before"

run "patch_protected_with_snapshot names it: patched right after its own snapshot" ok \
    -e target=web01.example.mil:DC0_C0_RP0_VM1 -e patch_snapshot=true -e patch_snapshot_replace=true \
    -e '{"vm_snapshot_cleanup_exclude_vms": ["DC0_*"], "patch_protected_with_snapshot": ["web01*"]}' \
    -- 'web01.example.mil is protected (DC0_H0_VM1 matches vm_snapshot_cleanup_exclude_vms (DC0_*)): patched after a snapshot' \
       'DC0_C0_RP0_VM1: PROTECTED - not patched' 'took \"patching\" of VM DC0_H0_VM1' "$installs" '!took \"patching\" of VM DC0_C0_RP0_VM1'
after="$(ID=1 snaps DC0_H0_VM1)"
check "  ...a new snapshot of the protected VM" "$(snaps DC0_H0_VM1) $([ "$before" != "$after" ] && echo new)" "baseline,patching new"

run "protected, named by patch_protected_with_snapshot, but no VM: never patched (patch_snapshot_required false or not)" ok \
    -e target=phys01.example.mil -e patch_snapshot=true -e patch_snapshot_required=false \
    -e '{"vmware_protected_vms": "phys*", "patch_protected_with_snapshot": true}' \
    -- 'phys01.example.mil is protected (phys01.example.mil matches vmware_protected_vms (phys*)): not patched' \
       'Its VM was not found in vCenter' "!$installs" '!Patched without a snapshot'

if [ "$fails" -eq 0 ]; then echo "all patch snapshot tests passed"; else echo "$fails patch snapshot test(s) FAILED"; exit 1; fi
