#!/usr/bin/env bash
# Patch hosts (playbooks/patch_hosts.yml) in batches (patch_serial): a host that fails or is lost stops the rollout -
# no later batch starts - and a host left out on purpose (a never-patch group, a protected VM) or before the rollout
# never does (1.70.5, 1.71.1). The play's own guard tasks are taken from the playbook; four made-up hosts on this
# machine (the local connection); a host is lost by a task to a closed port. Nothing is patched, nothing needs root.
#   bash tests/patch/run_serial_rollout_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(dirname "$(command -v "$ap")")/python3"
[ -x "$py" ] || py="$(command -v python3)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); }

# the guard: the "Patch hosts" play's pre_tasks up to the first include (its own tasks, as they are in the playbook);
# and every meta: end_host in roles/patch marked first (_patch_ended_on_purpose, the same condition)
"$py" - "$repo" "$work/guard.yml" <<'EOF' || exit 1
import sys, yaml
repo, out = sys.argv[1], sys.argv[2]
plays = yaml.safe_load(open(repo + "/playbooks/patch_hosts.yml"))
play = [p for p in plays if p.get("name") == "Patch hosts"][0]
guard = []
for t in play["pre_tasks"]:
    if "ansible.builtin.include_role" in t:
        break
    guard.append(t)
names = [t["name"] for t in guard]
assert any("already out" in n for n in names) and any(n.startswith("Stop the rollout") for n in names), names
yaml.safe_dump(guard, open(out, "w"), sort_keys=False, width=1000)
tasks = yaml.safe_load(open(repo + "/roles/patch/tasks/main.yml"))
ends = 0
def walk(tasks):                 # 1.74.0: the role's work is in a block (rescue, always) - every level is checked
    global ends
    for i, t in enumerate(tasks or []):
        for k in ("block", "rescue", "always"):
            if isinstance(t.get(k), list):
                walk(t[k])
        if t.get("ansible.builtin.meta") == "end_host":
            ends += 1
            if t.get("when") == "patch_not_started is defined":
                continue         # 1.74.0 stop mode (patch_continue_on_failure false): NOT STARTED after a peer failed -
                                 # the rollout is stopping anyway, so the guard may count it (it stops the later batches)
            prev = tasks[i - 1] if i else {}
            sf = prev.get("ansible.builtin.set_fact") or {}
            if "_patch_ended_on_purpose" not in sf or prev.get("when") != t.get("when"):
                sys.exit("FAIL - roles/patch: %r is not marked first (_patch_ended_on_purpose, the same when)" % t.get("name"))
walk(tasks)
assert ends, "no end_host in roles/patch/tasks/main.yml"
print("ok   - the guard: %d tasks from the play; roles/patch: %d end_host, each marked first" % (len(guard), ends))
EOF

cat > "$work/inv.ini" <<EOF
[rollout]
host01.example.mil
host02.example.mil
host03.example.mil
host04.example.mil

[rollout:vars]
ansible_connection=local
ansible_python_interpreter=$py
EOF

# a port nothing listens on, on this machine
closed=$("$py" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

# case NAME WANT-PATCHED WANT-MESSAGE(or -) EXTRA-VARS...
case_() {
    local name=$1 want=$2 msg=$3; shift 3
    ANSIBLE_HOST_KEY_CHECKING=False "$ap" -i "$work/inv.ini" "$here/serial_rollout.yml" \
        -e "guard_file=$work/guard.yml" -e "closed_port=$closed" -e patch_continue_on_failure=false "$@" > "$work/out.txt" 2>&1
    local got
    got=$(grep -o '"PATCHED host0[0-9]' "$work/out.txt" | sed 's/.*host0/0/' | sort | tr '\n' ' ' | sed 's/ $//')
    if [ "$got" != "$want" ]; then bad "$name: patched [$got], want [$want]"; tail -40 "$work/out.txt"; return; fi
    if [ "$msg" != "-" ] && ! grep -qF -- "$msg" "$work/out.txt"; then bad "$name: no [$msg]"; tail -40 "$work/out.txt"; return; fi
    if [ "$msg" = "-" ] && grep -q "Not patched:" "$work/out.txt"; then bad "$name: the guard stopped it"; return; fi
    ok "$name"
}

case_ "serial 1, host01 lost: no later batch"             ""            "Not patched: host01.example.mil failed or was lost in an earlier batch" -e lost=host01.example.mil
case_ "serial 1, host02 lost: host01 only"               "01"          "Not patched: host02.example.mil failed or was lost" -e lost=host02.example.mil
case_ "serial 2, host01 lost: its batch-mate named too"  ""            "in an earlier batch host01.example.mil, host02.example.mil stopped - one or more of them failed or was lost" -e lost=host01.example.mil -e ser=2
case_ "serial 2, host03 lost in the last batch: batch 1 done" "01 02"   "-" -e lost=host03.example.mil -e ser=2
case_ "serial 1, host01 fails: no later batch"           ""            "-" -e failhost=host01.example.mil
case_ "serial 1, host01 left out on purpose: goes on"    "02 03 04"    "-" -e skip=host01.example.mil
case_ "serial 2, host01 left out on purpose: goes on"    "02 03 04"    "-" -e skip=host01.example.mil -e ser=2
case_ "serial 1, host01 on purpose, host02 lost: stops"  ""            "Not patched: host02.example.mil failed or was lost" -e skip=host01.example.mil -e lost=host02.example.mil
case_ "serial 1, host01 lost before the rollout: goes on" "02 03 04"   "-" -e early=host01.example.mil
case_ "serial 1, lost before, then host02 lost: stops"   ""            "Not patched: host02.example.mil failed or was lost" -e early=host01.example.mil -e lost=host02.example.mil

# why the guard is there: without it this ansible-core starts the later batches after a lost host (any_errors_fatal
# and max_fail_percentage 0 count failed hosts, not lost ones). A note, not a failure: a core that stops by itself is fine.
ANSIBLE_HOST_KEY_CHECKING=False "$ap" -i "$work/inv.ini" "$here/serial_rollout.yml" -e "guard_file=$work/guard.yml" \
    -e "closed_port=$closed" -e patch_continue_on_failure=false -e guard=false -e lost=host01.example.mil > "$work/out.txt" 2>&1
if grep -q '"PATCHED host0[2-4]' "$work/out.txt"; then
    echo "note - without the guard this ansible-core goes on after a lost host (the guard is needed)"
else
    echo "note - without the guard this ansible-core stopped by itself"
fi

if [ $fails -ne 0 ]; then echo "$fails failed"; exit 1; fi
echo "all serial rollout scenarios passed"
