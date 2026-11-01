#!/usr/bin/env bash
# VMware ask (roles/vmware_ask_stack): the service's vCenter read - refresh.yml, the read-only modules of "VM - ask a
# question" - against vcsim, VMware's vCenter simulator, then questions answered from what it read: the job's own
# reader, GenAI's query (tests/containers/fake_act.py stands in) with a join, a question that asks for a change refused;
# a read that fails (a wrong password) keeps the last good one, said. tests/test_vmware_ask.py covers the rest (the
# sign-in, the page's API); tests/vmware_ask/run_stack_test.sh the whole stack on podman.
#   bash tests/vmware_ask/run_vmware_ask_test.sh
# Needs: podman or docker; a python with pyVmomi (VMWARE_TEST_PYTHON=... to pick it); ansible-playbook
# (ANSIBLE_PLAYBOOK=... to pick it).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${VMWARE_TEST_PYTHON:-$(command -v python3)}"
engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
port="${VCSIM_PORT:-18979}"
image="docker.io/vmware/vcsim:v0.52.0"
name="vcsim-ask-test-$$"
work="$(mktemp -d)"
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0

"$py" -c 'import pyVmomi' 2>/dev/null || { echo "ABORT - $py has no pyVmomi (pip install pyvmomi)"; exit 1; }
"$engine" run -d --name "$name" -p "127.0.0.1:$port:8989" "$image" -l 0.0.0.0:8989 -api-version 8.0 -username user -password pass >/dev/null \
    || { echo "ABORT - could not start vcsim"; exit 1; }
for _ in $(seq 1 30); do
    curl -sk "https://127.0.0.1:$port/about" >/dev/null 2>&1 && break
    sleep 1
done

mkdir -p "$work/data" "$work/act"
# check NAME PYTHON-CODE: the code runs with the service imported as s and its settings in cfg; it prints OK or why not
check() {
    local what="$1" code="$2"
    if out="$(cd "$work" && ASK_DATA="$work/data" ASK_ENGINE="$repo/plugins/filter" ASK_ANSIBLE_PLAYBOOK="$ap" \
        ASK_REFRESH_PLAYBOOK="$repo/roles/vmware_ask_stack/files/refresh.yml" \
        ASK_LIBRARY="$repo/roles/vmware_vm/library" ASK_MODULE_UTILS="$repo/roles/vmware_vm/module_utils" \
        ASK_ACT="$repo/tests/containers/fake_act.py" FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" \
        VMWARE_HOST=127.0.0.1 VMWARE_PORT=$port VMWARE_USER=user VMWARE_PASSWORD="${PW:-pass}" ASK_VALIDATE_CERTS=false \
        "$py" - "$repo/roles/vmware_ask_stack/files" <<EOF 2>&1
import json, sys
sys.path.insert(0, sys.argv[1])
import vmware_ask as s
s.log = lambda rec: None
cfg = s.Config()
inv = s.Inventory(cfg)
def ok(cond, why):
    print("OK" if cond else "NOT OK: %s" % why)
$code
EOF
)" && [ "$(printf '%s\n' "$out" | tail -1)" = "OK" ]; then
        echo "PASS  $what"
    else
        echo "FAIL  $what"; printf '%s\n' "$out" | tail -25 | sed 's/^/      /'; fails=$((fails + 1))
    fi
}

check "vCenter read: refresh.yml's five readers, kept as read.json" '
good = inv.refresh()
r = inv.current() or {}
st = inv.summary()
ok(good and st["state"] == "ok" and len(r.get("vms", [])) == 4 and len(r.get("hosts", [])) == 4 and r.get("datastores")
   and [v["name"] for v in r["vcenters"]] == ["127.0.0.1"] and not r["vcenters"][0].get("error") and r["read_at"],
   json.dumps(st)[:2000])'

check "a simple question: the job's own reader (no GenAI key)" '
r = s.Asker(cfg, inv).ask("give me all vms with h0 in the name")
ok(r["status"] == "ok" and [x[0] for x in r["rows"]] == ["DC0_H0_VM0", "DC0_H0_VM1"] and "no API key" in r["note"], json.dumps(r)[:2000])'

check "GenAI's query, with a join (the VMs on a host and their datastores)" '
import os
os.environ["GENAI_KEY"] = "test-key"
json.dump({"summary": "BEGIN_ACT_ANALYSIS\n" + json.dumps({"object": "vm", "where": [{"field": "host", "op": "eq", "value": "dc0_h0"}],
           "columns": ["name", "host", "datastore.name"]}) + "\nEND_ACT_ANALYSIS"}, open(os.environ["FAKE_ACT_CONFIG"], "w"))
r = s.Asker(s.Config(), inv).ask("VMs on host DC0_H0 and their datastores")
ok(r["status"] == "ok" and r["read_by"].startswith("GenAI (genai") and r["rows"] == [["DC0_H0_VM0", "DC0_H0", "LocalDS_0"],
   ["DC0_H0_VM1", "DC0_H0", "LocalDS_0"]] and open(os.environ["FAKE_ACT_OUT"] + "/fake-act-stdin.txt").read()
   == "VMs on host DC0_H0 and their datastores", json.dumps(r)[:2000])'

check "a question that asks for a change: refused, nothing read" '
r = s.Asker(cfg, inv).ask("find the VMs with h0 in the name and shut them down")
ok(r["status"] == "refused" and "Refused: this job only reads vCenter" in r["message"], json.dumps(r)[:2000])'

PW=wrong check "a read that fails (a wrong password) keeps the last good one, said" '
good = inv.refresh()
st = inv.summary()
ok(not good and st["state"] == "stale" and "no vCenter could be read" in st["error"] and len(inv.current()["vms"]) == 4,
   json.dumps(st)[:2000])'

echo "RESULT fails=$fails"
[ "$fails" -eq 0 ]
