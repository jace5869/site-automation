#!/usr/bin/env bash
# Patch hosts with the VMware snapshot before patching (playbooks/patch_hosts.yml, patch_snapshot): real hosts - UBI 9
# containers running systemd, patched with dnf over the podman connection - and their VMs in vcsim, VMware's vCenter
# simulator. Each host's VM found once before anything is patched (by its guest host name, its VM name, vmware_vm_name);
# a host matching two VMs stops the job before any host is patched, and so does a host without a VM when a vCenter was
# not read or patch_snapshot_required is true; the snapshot "patching" (no memory, the
# job and user in its description) taken in each batch right before that batch's update; the AAP host never; a dry run
# takes none; an old "patching" stops the next run, patch_snapshot_replace replaces it; a host in no vCenter is a
# physical server, patched without a snapshot; no vCenter credential; patch_snapshot false needs no vCenter; a host on the VMware
# protected lists is not patched, unless patch_protected_with_snapshot names it (then after its own snapshot); RHEL 7 is
# patched with yum, and a host with neither dnf nor yum is left out. The update check first (1.1.0): a host with nothing to
# update is UP TO DATE - no snapshot, not patched; a run again within patch_snapshot_reuse_hours reuses the snapshot the
# first took (no second one), an older one stops it as before, reuse_hours 0 = never reused; a dry run checks and
# says it would reuse - its packages the test's own (sa-patch-pending: one update; sa-patch-current: none), so the
# answer is the same whatever the image's age. (The cases before those patch only tzdata and take a snapshot each run:
# patch_update_check false and patch_snapshot_reuse_hours 0 there, the ways before 1.1.0.)
#   bash tests/patch/run_patch_snapshot_test.sh
# Needs: podman; registry.access.redhat.com/ubi9/ubi-init (pulled when missing) and its UBI repositories (internet);
# quay.io/centos/centos:stream9 and its repositories to build the test packages (PATCH_TEST_BUILDER);
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
builder_image="${PATCH_TEST_BUILDER:-quay.io/centos/centos:stream9}"
name="vcsim-patch-test-$$"
hosts=("sa-patch-a-$$" "sa-patch-b-$$" "sa-patch-c-$$" "sa-patch-d-$$" "sa-patch-e-$$")
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

# The update check's packages, the test's own, so the answer never depends on the UBI image's age: sa-patch-pending 1.0
# installed and 1.1 in a file:// repository (one update, always), sa-patch-current 1.0 in both (up to date, always).
# Built once with rpm-build in a throwaway CentOS Stream 9 container (UBI has no rpm-build), noarch, one small file each.
mkdir -p "$work/rpms"
"$engine" run --rm -v "$work/rpms:/out:Z" "$builder_image" sh -c '
    dnf -q install -y rpm-build createrepo_c >/dev/null 2>&1 || exit 1
    for p in pending:1.0 pending:1.1 current:1.0; do n=sa-patch-${p%%:*}; v=${p#*:}
        printf "%s\n" "Name: $n" "Version: $v" "Release: 1" "Summary: site-automation patch test package" "License: MIT" \
            "BuildArch: noarch" "%description" "For tests/patch/run_patch_snapshot_test.sh." "%install" \
            "mkdir -p %{buildroot}/usr/share/$n" "echo $v > %{buildroot}/usr/share/$n/version" "%files" "/usr/share/$n/version" > /tmp/$n.spec
        rpmbuild -bb --quiet --define "_rpmdir /tmp/rpm-$v" /tmp/$n.spec >/dev/null 2>&1 || exit 1
    done
    mkdir -p /out/installed /out/repo
    cp /tmp/rpm-1.0/noarch/*.rpm /out/installed/; cp /tmp/rpm-1.1/noarch/*.rpm /tmp/rpm-1.0/noarch/sa-patch-current-*.rpm /out/repo/
    createrepo_c -q /out/repo' || { echo "ABORT - could not build the test packages in $builder_image"; exit 1; }
for h in "${hosts[@]:0:3}"; do
    "$engine" cp "$work/rpms" "$h:/opt/sa-patch-rpms" && "$engine" exec "$h" sh -c 'rpm -i /opt/sa-patch-rpms/installed/*.rpm &&
        printf "%s\n" "[sa-patch-test]" "name=sa-patch-test" "baseurl=file:///opt/sa-patch-rpms/repo" "gpgcheck=0" "enabled=1" \
        > /etc/yum.repos.d/sa-patch-test.repo' || { echo "ABORT - could not install the test packages in $h"; exit 1; }
done

# old01: RHEL 7 - its dnf taken away and 7.9 written in its release files, so yum only (UBI's yum is dnf underneath).
# other01: neither dnf nor yum.
"$engine" exec "${hosts[3]}" sh -c 'rm -f /usr/bin/dnf /usr/bin/microdnf; sed -i "s/^VERSION_ID=.*/VERSION_ID=\"7.9\"/" /etc/os-release;
    echo "Red Hat Enterprise Linux Server release 7.9 (Maipo)" > /etc/redhat-release' || { echo "ABORT - old01"; exit 1; }
"$engine" exec "${hosts[4]}" rm -f /usr/bin/dnf /usr/bin/microdnf /usr/bin/yum || { echo "ABORT - other01"; exit 1; }

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
        lost01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh, ansible_python_interpreter: /usr/bin/python3}
        web01.example.mil: {ansible_host: ${hosts[0]}, $c}
        DC0_C0_RP0_VM1: {ansible_host: ${hosts[1]}, $c}
        web02.example.mil: {ansible_host: ${hosts[2]}, $c, vmware_vm_name: dc0_c0_rp0_vm0}
        phys01.example.mil: {ansible_host: ${hosts[0]}, $c}
        dup: {ansible_host: ${hosts[1]}, $c}
        old01.example.mil: {ansible_host: ${hosts[3]}, $c}
        other01.example.mil: {ansible_host: ${hosts[4]}, $c}
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
        -e '{"awx_job_id": 4711, "awx_user_name": "operator1", "patch_packages": ["tzdata"], "patch_update_check": false, "patch_snapshot_reuse_hours": 0}' "${args[@]}") > "$work/out.txt" 2>&1
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
order="$(grep -oE 'TASK \[(patch : Patch \| install the updates( \(security only\))?\]|vmware_vm : VMware \| snapshot "patching")' "$work/out.txt" | sed 's/.*| //; s/\]$//' | tr '\n' ';')"
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

# RHEL 7 (yum only): patched with yum. A host with neither dnf nor yum: left out and listed, the job green.
NOVC=1 run "RHEL 7: patched with yum" ok -e target=old01.example.mil \
    -- 'TASK [patch : Patch | install the updates with yum' '!old01.example.mil: NO DNF OR YUM' '!Could not detect'
NOVC=1 run "RHEL 7, a dry run: what yum would update, nothing installed" ok --check -e target=old01.example.mil \
    -- 'TASK [patch : Patch | the updates yum has' 'TASK [patch : Patch | what yum updated' '!changed: [old01.example.mil]'
NOVC=1 run "neither dnf nor yum: listed, left out, the others patched - the job not failed" ok \
    -e target=web01.example.mil:other01.example.mil \
    -- 'other01.example.mil: NO DNF OR YUM' '1 host(s) with neither dnf nor yum, not patched: other01.example.mil' "$installs"
NOVC=1 run "patch_skip_unsupported false: a host with neither fails at the update" fail \
    -e target=other01.example.mil -e patch_skip_unsupported=false -- 'other01.example.mil' '!other01.example.mil: NO DNF OR YUM'

# The VMware protected lists: a host whose name, vmware_vm_name or VM matches vmware_protected_vms or
# vm_snapshot_cleanup_exclude_vms is not patched, unless patch_protected_with_snapshot names it - then after its snapshot.
NOVC=1 run "protected (vmware_protected_vms, by vmware_vm_name), no snapshot: not patched" ok -e target=web02.example.mil \
    -e '{"vmware_protected_vms": ["DC0_C0_RP0_VM0"]}' -e patch_protected_with_snapshot=true -e patch_protected_vm_lookup=false \
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

# ---- the update check first, then the snapshot; a run again reuses it (1.1.0) ----
# sa-patch-current has no update (up to date), sa-patch-pending has one - in any UBI image, old or new.
uc='{"patch_update_check": true, "patch_snapshot_reuse_hours": 24, "patch_packages": ["sa-patch-current"]}'
pending='{"patch_update_check": true, "patch_snapshot_reuse_hours": 24, "patch_packages": ["sa-patch-pending"]}'
before="$(ID=1 snaps DC0_H0_VM1)"
run "up to date: listed, no snapshot, not patched - the job green" ok -e target=web01.example.mil -e patch_snapshot=true -e "$uc" \
    -- 'web01.example.mil: UP TO DATE - nothing to patch' '1 host(s) up to date, nothing to patch: web01.example.mil' \
       "!$installs" '!VMware | read every VM'
check "  ...no snapshot taken" "$(ID=1 snaps DC0_H0_VM1)" "$before"
check "  ...the summary: up to date, not needed" "$(grep -oE 'web01.example.mil +up to date +not needed - up to date' "$work/out.txt" | head -1 | tr -s ' ')" \
    "web01.example.mil up to date not needed - up to date"

before="$(ID=1 snaps DC0_C0_RP0_VM0)"
run "a dry run: the update check runs, the snapshot of a moment ago would be reused" ok --check -e target=web02.example.mil \
    -e patch_snapshot=true -e "$pending" -- 'reusing the snapshot \"patching\" from' 'would reuse \"patching\" from' '!web02.example.mil: UP TO DATE'
check "  ...the summary: 1 pending" "$(grep -oE 'web02.example.mil +1 pending' "$work/out.txt" | head -1 | tr -s ' ')" "web02.example.mil 1 pending"
check "  ...nothing taken" "$(ID=1 snaps DC0_C0_RP0_VM0)" "$before"

run "a snapshot older than patch_snapshot_reuse_hours: stops the job as before" fail -e target=DC0_C0_RP0_VM1 -e patch_snapshot=true \
    -e "$pending" -e patch_snapshot_reuse_hours=0.001 -- 'DC0_C0_RP0_VM1 (DC0_C0_RP0_VM1) still has the snapshot \"patching\" from' "!$installs"
run "patch_snapshot_reuse_hours 0: never reused - stops as before 1.1.0" fail -e target=DC0_C0_RP0_VM1 -e patch_snapshot=true \
    -e "$pending" -e patch_snapshot_reuse_hours=0 -- 'still has the snapshot \"patching\"' '!reusing the snapshot' "!$installs"

before="$(ID=1 snaps DC0_H0_VM1)"
run "run again within 24 h, updates left: the snapshot reused, no second one, patched" ok -e target=web01.example.mil \
    -e patch_snapshot=true -e "$pending" -- 'reusing the snapshot \"patching\" from' 'reused \"patching\" from' "$installs" \
    '!took \"patching\"'
check "  ...the same snapshot, no second one" "$(ID=1 snaps DC0_H0_VM1)" "$before"
check "  ...the summary: 1 pending, reused" "$(grep -oE 'web01.example.mil +1 pending +reused' "$work/out.txt" | head -1 | tr -s ' ')" \
    "web01.example.mil 1 pending reused"

# ---- 1.70.5 (review of bf0a69bd) ----
ver() { "$engine" exec "$1" cat /usr/share/sa-patch-pending/version 2>/dev/null; }
uc1='{"patch_update_check": true, "patch_snapshot_reuse_hours": 24}'

# string settings (a survey's text field, -e): one name each, never its letters
NOVC=1 run "patch_exclude as text (-e patch_exclude=sa-patch-pending): excluded, so up to date" ok -e target=DC0_C0_RP0_VM1 \
    -e "$uc1" -e patch_packages=sa-patch-pending -e patch_exclude=sa-patch-pending -- 'DC0_C0_RP0_VM1: UP TO DATE' "!$installs"
check "  ...not updated" "$(ver "${hosts[1]}")" "1.0"
NOVC=1 run "patch_packages as text (-e patch_packages=sa-patch-pending): that package updated" ok -e target=DC0_C0_RP0_VM1 \
    -e "$uc1" -e patch_packages=sa-patch-pending -- "$installs" '!DC0_C0_RP0_VM1: UP TO DATE'
check "  ...updated" "$(ver "${hosts[1]}")" "1.1"

# up to date, a reboot pending (the last run installed with automatic_restarts false): never left out silently
"$engine" exec "${hosts[1]}" sh -c 'printf "#!/bin/sh\necho Reboot is required\nexit 1\n" > /usr/local/bin/needs-restarting; chmod +x /usr/local/bin/needs-restarting'
NOVC=1 run "up to date, a reboot pending, automatic_restarts false: listed with the reboot still pending" ok -e target=DC0_C0_RP0_VM1 \
    -e "$uc1" -e patch_packages=sa-patch-pending -- 'A REBOOT IS STILL PENDING' "!$installs"
check "  ...the summary says so" "$(grep -oE 'DC0_C0_RP0_VM1 +up to date, REBOOT PENDING' "$work/out.txt" | head -1 | tr -s ' ')" \
    "DC0_C0_RP0_VM1 up to date, REBOOT PENDING"
NOVC=1 run "up to date, a reboot pending, the run again with automatic_restarts (dry run): it goes on to its reboot" ok --check \
    -e target=DC0_C0_RP0_VM1 -e "$uc1" -e patch_packages=sa-patch-pending -e automatic_restarts=true \
    -- 'UP TO DATE, but a REBOOT IS PENDING' 'TASK [patch : Patch | reboot]' '!A REBOOT IS STILL PENDING'
check "  ...the reboot task ran for it (Check mode: not done)" \
    "$(awk '/TASK \[patch : Patch \| reboot\]/{getline; print; exit}' "$work/out.txt" | cut -d: -f1)" "changed"
"$engine" exec "${hosts[1]}" rm -f /usr/local/bin/needs-restarting

# the machine running the job, by another name: its machine-id
mid="$(cat /etc/machine-id)"
"$engine" exec "${hosts[2]}" sh -c "cp /etc/machine-id /etc/machine-id.sa-test 2>/dev/null; echo $mid > /etc/machine-id"
NOVC=1 run "a host whose machine-id is the controller's (an alias of it): refused" fail -e target=web02.example.mil \
    -- 'its /etc/machine-id is the one of the machine running this job' "!$installs"
"$engine" exec "${hosts[2]}" sh -c 'if [ -f /etc/machine-id.sa-test ]; then mv /etc/machine-id.sa-test /etc/machine-id; else : > /etc/machine-id; fi'

# a host lost in a batch of 2: the rollout stops, the next batch is not patched
"$engine" exec "${hosts[1]}" sh -c 'rpm -q sa-patch-pending >/dev/null && rpm -U --oldpackage /opt/sa-patch-rpms/installed/sa-patch-pending-*.rpm' >/dev/null 2>&1
# a host down from the start (patch_skip_unreachable true, the default): NOT REACHED and left out, and it never stops the
# rollout - every good host patched, one at a time and two at a time
patched() {  # the hosts the update task ran on
    awk '/^TASK \[patch : Patch \| install the updates\]/{f=1; next} /^TASK /{f=0} f && /^(ok|changed): \[/{sub(/^[a-z]+: \[/, ""); sub(/\].*/, ""); print}' \
        "$work/out.txt" | sort -u | tr '\n' ' ' | sed 's/ $//'
}
for ser in 1 2; do
    NOVC=1 run "a host down from the start, patch_serial $ser: NOT REACHED, every other host patched" ok \
        -e target=lost01.example.mil:web01.example.mil:DC0_C0_RP0_VM1:web02.example.mil -e patch_serial=$ser \
        -- 'lost01.example.mil: NOT REACHED' '1 host(s) not reached, not patched: lost01.example.mil' '!Not patched: '
    check "  ...each good host patched (patch_serial $ser)" "$(patched)" "DC0_C0_RP0_VM1 web01.example.mil web02.example.mil"
done

# lost01 (ssh to a closed port, first in the inventory, so in batch 1 with DC0_C0_RP0_VM1 - batches follow the
# inventory's order) goes on with patch_skip_unreachable false and is lost in the patch play: web02 (batch 2) never patched
NOVC=1 run "patch_serial 2, a host lost in batch 1 (patch_skip_unreachable false): batch 2 is never patched" fail \
    -e target=lost01.example.mil:DC0_C0_RP0_VM1:web02.example.mil -e patch_serial=2 -e patch_skip_unreachable=false -e "$uc1" \
    -e patch_packages=sa-patch-pending -- 'UNREACHABLE! => ' 'Not patched: in an earlier batch DC0_C0_RP0_VM1, lost01.example.mil stopped - one or more of them failed or was lost' "!$installs"
check "  ...batch 1's other host not updated" "$(ver "${hosts[1]}")" "1.0"
check "  ...batch 2's host not updated" "$(ver "${hosts[2]}")" "1.0"

# the VMware protected lists by VM name without patch_snapshot: each host's VM is found (web01's VM is DC0_H0_VM1)
run "protected by its VM name, no snapshot: found in vCenter, not patched" ok -e target=web01.example.mil \
    -e '{"vmware_protected_vms": ["DC0_H0_VM1"]}' -- 'web01.example.mil is protected (DC0_H0_VM1 matches vmware_protected_vms' "!$installs"
NOVC=1 run "protected lists set, no vCenter credential: stopped in the first play" fail -e target=web01.example.mil \
    -e '{"vmware_protected_vms": ["DC0_H0_VM1"]}' -- 'Nothing was patched. vmware_protected_vms / vm_snapshot_cleanup_exclude_vms are set' \
    'patch_protected_vm_lookup: false' "!$installs" '!web01.example.mil: NOT REACHED'
NOVC=1 run "patch_protected_vm_lookup false: the inventory names only (web01 is not on the list): patched" ok -e target=web01.example.mil \
    -e '{"vmware_protected_vms": ["DC0_H0_VM1"]}' -e patch_protected_vm_lookup=false -- "$installs"

# an explicit mapping that names no VM: never a physical server
run "vmware_vm_name names no VM: stopped before any host is patched" fail -e target=web02.example.mil -e patch_snapshot=true \
    -e vmware_vm_name=no-such-vm -- 'vmware_vm_name is no-such-vm' 'Nothing was patched' "!$installs"
run "patch_physical: true - no lookup, patched without a snapshot even with patch_snapshot_required" ok -e target=phys01.example.mil \
    -e patch_snapshot=true -e patch_snapshot_required=true -e patch_physical=true \
    -- 'phys01.example.mil: PHYSICAL (patch_physical: true)' 'patch_physical: true, so a physical server' "$installs"

# right before a host's snapshot, what was found before the first batch is checked again (tests/patch/snapshot_revalidate.yml)
reval() {  # reval NAME EXPECT CASE TEXT...
    local tname="$1" want="$2" case="$3"; shift 3
    (cd "$repo" && VMWARE_HOST=127.0.0.1 VMWARE_PORT=$port VMWARE_USER=user VMWARE_PASSWORD=pass ANSIBLE_COLLECTIONS_PATH="$cols" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_ROLES_PATH="$repo/roles" \
        "$ap" -i "$work/inv.yml" tests/patch/snapshot_revalidate.yml \
        -e case="$case" ${SNAP_ID:+-e snap_id=$SNAP_ID}) > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        keep "$tname"; echo "FAIL - $tname: exit $rc"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -6 | cut -c1-300; fails=$((fails + 1)); return
    fi
    local t; for t in "$@"; do grep -qF -- "$t" "$work/out.txt" || { keep "$tname"; echo "FAIL - $tname: output lacks: $t"; fails=$((fails + 1)); return; }; done
    echo "ok   - $tname"
}
before="$(ID=1 snaps DC0_H0_VM1)"
reval "the snapshot to reuse is gone by the host's turn: a new one is taken" ok gone 'is gone now: a new one is taken' 'RESULT took \"patching\"'
after="$(ID=1 snaps DC0_H0_VM1)"
check "  ...a new \"patching\"" "$(snaps DC0_H0_VM1) $([ "$before" != "$after" ] && echo new)" "baseline,patching new"
SNAP_ID="$(ID=1 snaps DC0_H0_VM1 | tr ',' '\n' | grep '^patching:' | cut -d: -f2)" \
    reval "the snapshot to reuse is too old by the host's turn: stopped" fail aged 'h old (over patch_snapshot_reuse_hours' 'patch_snapshot_replace: true'
reval "the VM's ID now has another name: stopped" fail renamed 'is now named DC0_H0_VM1'

if [ "$fails" -eq 0 ]; then echo "all patch snapshot tests passed"; else echo "$fails patch snapshot test(s) FAILED"; exit 1; fi
