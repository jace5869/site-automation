#!/usr/bin/env bash
# The disk space report and the tmp cleanup (playbooks/disk_report.yml, playbooks/tmp_cleanup.yml) on UBI 9 containers
# over the podman connection: web01's /tmp is a folder of a real filesystem (a container's own / is overlay, which the
# disk check leaves out), db01 has none. The limits are set low so web01's /tmp is "full"; files age in minutes
# (_tmp_cleanup_test_minutes - a change time cannot be made old), so the test waits a little over one.
#   bash tests/disk/run_disk_report_test.sh
# Needs: podman (CONTAINER_ENGINE for a wrapper), registry.access.redhat.com/ubi9/ubi, the containers.podman collection
# (DISK_TEST_COLLECTIONS, else installed into a temporary folder from tests/patch/requirements.yml), a folder on a real
# filesystem for web01's /tmp (DISK_TEST_DIR, default a new folder under ~/.cache).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
engine="${CONTAINER_ENGINE:-$(command -v podman)}"
image="registry.access.redhat.com/ubi9/ubi:latest"
web="sa-disk-web-$$" db="sa-disk-db-$$"
work="$(mktemp -d)"
base="${DISK_TEST_DIR:-$HOME/.cache}"
mkdir -p "$base"
data="$(mktemp -d "$base/sa-disk-test.XXXXXX")"
cleanup() { "$engine" rm -f -t 0 "$web" "$db" >/dev/null 2>&1; "$engine" unshare rm -rf "$data" 2>/dev/null || rm -rf "$data"; rm -rf "$work"; }
trap cleanup EXIT
fails=0

cols="${DISK_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$repo/tests/patch/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi
"$engine" run -d --name "$web" -v "$data:/tmp:Z" "$image" sleep infinity >/dev/null || { echo "ABORT - could not start $image"; exit 1; }
"$engine" run -d --name "$db" "$image" sleep infinity >/dev/null || { echo "ABORT - could not start $image"; exit 1; }
"$engine" exec "$web" sh -c 'mkdir -p /tmp/app/logs /tmp/systemd-private-x/tmp /var/tmp/cache
    head -c 200000 /dev/zero > /tmp/app/logs/old1.log; head -c 300000 /dev/zero > /tmp/app/old2.dat
    head -c 50000 /dev/zero > /tmp/systemd-private-x/tmp/kept; head -c 1000 /dev/zero > /var/tmp/cache/old3'
sleep 70
"$engine" exec "$web" sh -c 'head -c 1000 /dev/zero > /tmp/new.log'
# db01: LVM as lvs and vgs print it (--noheadings --separator '|'): a thin pool 91% full, a snapshot that filled up
"$engine" exec "$db" sh -c 'printf "#!/bin/sh\nprintf \"  rhel|pool|twi-aotz--|17179869184|91.20|12.00|\\n  rhel|root|Vwi-aotz--|10737418240|40.00||\\n  rhel|snap|Swi-I-s---|2147483648|100.00||root\\n\"\n" > /usr/local/bin/lvs
    printf "#!/bin/sh\nprintf \"  rhel|34359738368|4294967296\\n\"\n" > /usr/local/bin/vgs; chmod +x /usr/local/bin/lvs /usr/local/bin/vgs'

c="ansible_connection: containers.podman.podman, ansible_python_interpreter: /usr/bin/python3, ansible_podman_executable: $engine, ansible_become: false"
cat > "$work/inv.yml" <<EOI
all:
  children:
    linux:
      hosts:
        web01.example.mil: {ansible_host: $web, $c}
        db01.example.mil: {ansible_host: $db, $c}
EOI
# run NAME EXPECT(ok|fail) PLAYBOOK [args...] -- TEXT... (a TEXT starting with ! must NOT be in the output)
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/inv.yml" "playbooks/$pb" -e _tmp_cleanup_test_minutes=1 "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            ! grep -qF -- "${t:1}" "$work/out.txt" || { echo "FAIL - $tname: output has: ${t:1}"; fails=$((fails + 1)); return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1; }
        fi
    done
    echo "ok   - $tname"
}
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }
there() { "$engine" exec "$web" sh -c "[ -e '$1' ] && echo there || echo gone"; }
tmpdirs='{"tmp_cleanup_dirs": [{"path": "/tmp", "days": 1}, {"path": "/var/tmp", "days": 1}]}'

run "report: /tmp of web01 critical, its old files measured, the job green" ok disk_report.yml \
    -e disk_report_warn_pct=1 -e disk_report_crit_pct=2 -e "$tmpdirs" \
    -- '== Filesystems at or above 1%' 'web01.example.mil  |  /tmp  |' '== Old files in the tmp folders of a full filesystem' \
       'web01.example.mil  |  /tmp  |  /tmp  |  1 days  |  2  |' '!db01.example.mil  |  /' '!/var/tmp  |  /var/tmp'
run "report at the normal limits: no filesystem at 85% here; LVM's thin pool and lost snapshot; still green" ok disk_report.yml \
    -- 'No filesystem is 85% full or more.' '!== Old files' '== LVM thin pools and snapshots (2)' \
       'db01.example.mil  |  rhel/snap  |  snapshot of root  |  2.0G  |  100.0%  |    |  no longer valid (filled up)' \
       'db01.example.mil  |  rhel/pool  |  thin pool  |  16.0G  |  91.2%  |  12.0%  |  critical' \
       'db01.example.mil  |  rhel  |  32.0G  |  4.0G  |  12.5%' '!rhel/root'
run "cleanup on its own, without tmp_cleanup_confirm: lists, deletes nothing" ok tmp_cleanup.yml -l web01.example.mil -e "$tmpdirs" \
    -- '/tmp: would delete 2 file(s)' 'tmp_cleanup_confirm is not true'
check "  ...old2.dat still there" "$(there /tmp/app/old2.dat)" "there"
run "cleanup as Check with tmp_cleanup_confirm: deletes nothing" ok tmp_cleanup.yml -l web01.example.mil -e "$tmpdirs" -e tmp_cleanup_confirm=true --check \
    -- '(dry run)'
check "  ...old2.dat still there" "$(there /tmp/app/old2.dat)" "there"
run "a system folder in tmp_cleanup_dirs is refused" fail tmp_cleanup.yml -l web01.example.mil -e tmp_cleanup_confirm=true \
    -e '{"tmp_cleanup_dirs": [{"path": "/var", "days": 5}]}' -- 'is refused - only a tmp folder'
run "a plan from another workflow is refused" fail tmp_cleanup.yml -e "$tmpdirs" -e awx_workflow_job_id=7 \
    -e '{"disk_cleanup_plan": {"web01.example.mil": ["/tmp"]}, "disk_cleanup_plan_workflow": "6"}' -- 'Refused: disk_cleanup_plan did not come from this workflow'
check "  ...old2.dat still there" "$(there /tmp/app/old2.dat)" "there"
run "the workflow's plan: only web01's /tmp, after the approval" ok tmp_cleanup.yml -e "$tmpdirs" -e awx_workflow_job_id=7 \
    -e '{"disk_cleanup_plan": {"web01.example.mil": ["/tmp"]}, "disk_cleanup_plan_workflow": "7"}' \
    -- '/tmp: deleted 2 file(s)' "db01.example.mil: not in the disk space report's plan" '!/var/tmp: deleted'
check "  ...old files gone" "$(there /tmp/app/old2.dat) $(there /tmp/app/logs/old1.log)" "gone gone"
check "  ...systemd's folder, the new file and /var/tmp (not planned) stay" \
    "$(there /tmp/systemd-private-x/tmp/kept) $(there /tmp/new.log) $(there /var/tmp/cache/old3)" "there there there"

[ $fails -eq 0 ] && echo "all disk report scenarios passed" || { echo "$fails failed"; exit 1; }
