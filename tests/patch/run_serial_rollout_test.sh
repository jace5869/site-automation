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
"$py" - "$repo" "$work/guard.yml" "$work/role.yml" <<'EOF' || exit 1
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

# 1.75.1, patch_continue_on_failure true: roles/patch's own block - its lost check (_patch_lost_at, "Patch | LOST") and
# its rescue (_patch_could_not) - with stand-in steps in place of the real ones (serial_rollout_continue.yml)
top = [t for t in tasks if t.get("name") == "Patch | this host"]
assert len(top) == 1 and "rescue" in top[0] and "_patch_lost_at" in top[0].get("vars", {}), "roles/patch: no 'Patch | this host' block"
role = dict(top[0])
inner, lost_check = role["block"][0], role["block"][1:]
assert inner.get("ignore_unreachable") and isinstance(inner.get("block"), list), inner.get("name")
assert [t["name"] for t in lost_check] == ["Patch | LOST - the job lost it on the way"], [t.get("name") for t in lost_check]
steps = yaml.safe_load("""
- {name: Its steps begin, ansible.builtin.set_fact: {_patch_at: the start}}
- name: Left out on purpose - marked first, as roles/patch does
  ansible.builtin.set_fact: {_patch_ended_on_purpose: never-patch group}
  when: inventory_hostname == skip | default('')
- {name: Left out on purpose, ansible.builtin.meta: end_host, when: "inventory_hostname == skip | default('')"}
- name: The host is lost here (as the role's first remote step)
  ansible.builtin.ping:
  register: _patch_host_mid
  vars:
    ansible_connection: ssh
    ansible_host: 127.0.0.1
    ansible_port: "{{ closed_port | default(9) }}"
    ansible_ssh_common_args: "-o ConnectTimeout=3 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
  when: inventory_hostname == lost | default('')
- {name: The host fails here, ansible.builtin.fail: {msg: failed on purpose}, when: "inventory_hostname == failhost | default('')"}
- CHECKPOINT the snapshot
- {name: The snapshot (stand-in), ansible.builtin.debug: {msg: "SNAPSHOT {{ inventory_hostname }}"}}
- CHECKPOINT the update
- {name: Patched, ansible.builtin.debug: {msg: "PATCHED {{ inventory_hostname }}"}}
- {name: What it did, ansible.builtin.set_fact: {_patch_done: {updates: 1, rebooted: false, reboot: ''}}}
""")
# its two checkpoints, as they are in the role (four tasks each): where the batch-mates of a host that failed or was lost
# stop with patch_continue_on_failure false, and go on with true
STOP = "not patch_continue_on_failure | bool and "
real = inner["block"]
def checkpoint(at, what):
    names = ["Patch | got to the checkpoint before its " + at,
             "Patch | the rollout stopped (patch_continue_on_failure false) - not " + what,
             "Patch | NOT STARTED - the rollout stopped before its " + what,
             "Patch | not %s - the rollout stopped" % what]
    i = [t.get("name") for t in real].index(names[0])
    got = real[i:i + 4]
    assert [t.get("name") for t in got] == names, [t.get("name") for t in got]
    assert got[1]["when"].startswith(STOP), got[1]["when"]
    return got
cps = {"the snapshot": checkpoint("snapshot", "snapshot"), "the update": checkpoint("install", "update")}
def with_checkpoints(mutate=None):
    out = []
    for t in steps:
        if isinstance(t, str):
            cp = [dict(x) for x in cps[t[len("CHECKPOINT "):]]]
            if mutate and t.endswith(mutate):
                cp[1]["when"] = cp[1]["when"][len(STOP):]     # the mutation: the stop no longer asks patch_continue_on_failure
            out += cp
        else:
            out.append(t)
    return out
for path, mutate in ((sys.argv[3], None), (sys.argv[3][:-4] + "_mut_snapshot.yml", "the snapshot"),
                     (sys.argv[3][:-4] + "_mut_update.yml", "the update")):
    role["block"] = [dict(inner, block=with_checkpoints(mutate))] + lost_check
    yaml.safe_dump([role], open(path, "w"), sort_keys=False, width=1000)
print("ok   - roles/patch: its lost check, both batch checkpoints and its rescue taken for the continue cases")
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

# 1.75.1: patch_continue_on_failure true (the default) - a host that fails or is lost is caught by the role (its rescue),
# never stops the rollout, and is in the report's data; every other host is patched.
# ccase NAME WANT-PATCHED WANT-REPORT("host:state:reason ...", + for a space) WANT-NEEDS-LOOK EXTRA-VARS...
ccase_() {
    local name=$1 want=$2 report=$3 look=$4; shift 4
    ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_FILTER_PLUGINS="$repo/plugins/filter" ANSIBLE_LOCALHOST_WARNING=False \
        "$ap" -i "$work/inv.ini" "$here/serial_rollout_continue.yml" -e "guard_file=$work/guard.yml" \
        -e "role_file=${role_file:-$work/role.yml}" -e "closed_port=$closed" -e patch_continue_on_failure=true "$@" > "$work/out.txt" 2>&1
    local rc=$? got r
    got=$(grep -o '"PATCHED host0[0-9]' "$work/out.txt" | sed 's/.*host0/0/' | sort | tr '\n' ' ' | sed 's/ $//')
    if [ $rc -ne 0 ]; then bad "$name: rc $rc, want 0 (the role catches it)"; tail -40 "$work/out.txt"; return; fi
    if [ "$got" != "$want" ]; then bad "$name: patched [$got], want [$want]"; tail -40 "$work/out.txt"; return; fi
    if grep -q "Not patched:\|: NOT STARTED -" "$work/out.txt"; then bad "$name: the rollout stopped"; return; fi
    for r in $report; do
        r=$(echo ${r//:/ }); r=${r//+/ }     # host state reason, the whole line (+ for a space in the reason)
        if ! grep -qF -- "\"REPORT $r\"" "$work/out.txt"; then bad "$name: report data, no [$r]"; grep '"REPORT' "$work/out.txt"; return; fi
    done
    if ! grep -qF -- "\"NEEDS LOOK $look\"" "$work/out.txt"; then bad "$name: needs a look, want [$look]"; grep '"NEEDS LOOK' "$work/out.txt"; return; fi
    if grep -q "failed=[1-9]" "$work/out.txt"; then bad "$name: a host counted failed"; return; fi
    ok "$name"
}

ccase_ "continue, serial 1, host01 lost: the others patched" "02 03 04" \
    "host01.example.mil:could_not:LOST+mid-run host02.example.mil:patched:" "host01.example.mil" -e lost=host01.example.mil
ccase_ "continue, serial 2, host01 fails: its batch-mate and the later batch patched" "02 03 04" \
    "host01.example.mil:could_not:FAILED+before+the+update host04.example.mil:patched:" "host01.example.mil" -e failhost=host01.example.mil -e ser=2
ccase_ "continue, serial 1, host01 on purpose, host02 lost: the others patched" "03 04" \
    "host01.example.mil:skipped: host02.example.mil:could_not:LOST+mid-run" "host02.example.mil" \
    -e skip=host01.example.mil -e lost=host02.example.mil
ccase_ "continue, serial 2, host01 lost: its batch-mate and the later batch patched" "02 03 04" \
    "host01.example.mil:could_not:LOST+mid-run host02.example.mil:patched:" "host01.example.mil" -e lost=host01.example.mil -e ser=2

# the batch-mate cases above go through the role's own checkpoints. Their stop must ask patch_continue_on_failure:
# with that taken out of either checkpoint (a mutated copy of the role's block), the batch-mate stops and the case fails.
for cp in snapshot update; do
    out=$(role_file="$work/role_mut_$cp.yml" ccase_ "mutation" "02 03 04" "host04.example.mil:patched:" "host01.example.mil" \
        -e failhost=host01.example.mil -e ser=2)
    if echo "$out" | grep -q '^FAIL - mutation: patched \[03 04\]'; then
        ok "continue, the stop in the checkpoint before $cp not asking patch_continue_on_failure: the case fails"
    else
        bad "continue, the checkpoint before $cp mutated: the case still passes"; echo "$out" | head -5
    fi
done

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
