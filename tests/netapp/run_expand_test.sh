#!/usr/bin/env bash
# 1.123.0: playbooks/ontap_volume_list.yml and playbooks/ontap_volume_expand.yml (NetApp - expand a volume) against a fake
# ONTAP that takes changes (tests/netapp/fake_ontap.py with "writable", data from tests/netapp/expand_fixture.py), a fake AAP
# (tests/servicenow/aapmock.py, template 58) and a fake ServiceNow (tests/servicenow/snmock.py):
# the list (choices, one per LUN, the exclusions, the survey question added single select, GET only, a dry run writes
# nothing); a Check run = the plan only; the volume and its LUN grown and read back, the incident typed in the comment, no
# vCenter credential = the datastore steps by hand; the same choice again = nothing to do; the default over 97 % refused
# with the largest that fits, then that size typed; an excluded volume named by hand, a cluster not in ontap_clusters,
# text without the list's key, a volume with two LUNs named without one, a bad size, a size over the volume's LUN: refused,
# nothing changed; the volume changed between the plan and the change; ONTAP's job failing; ontap_expand_incident: create
# (no group: refused; with one: opened, then resolved with the steps).
# A LUN volume is judged and grown by its LUN: the list's size and free space are the LUN's (fullest first by it); the growth
# is the LUN's; the volume grows only as much as the bigger LUN needs (thick + its snapshot reserve), not at all with room.
# The vCenter side (rescan, VMFS expand, NFS refresh) is tests/test_ontap_expand.py's (fake pyVmomi objects): vcsim has no
# NetApp LUN and no VMFS extent, and answers MethodNotFound to QueryVmfsDatastoreExpandOptions and RefreshDatastoreStorageInfo.
#   bash tests/netapp/run_expand_test.sh            (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(command -v python3)"
port="${ONTAP_EXPAND_TEST_PORT:-18543}"
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
wait_port() { for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return; sleep 0.1; done; echo "ABORT - port $1"; exit 1; }
# fake ONTAP N MODE: a fresh cluster on port+N (its log c$N.log)
fresh() {
    "$py" "$here/expand_fixture.py" "$work/c$1.json" svc_rw expand-Test9 ${2:-}
    "$py" "$here/fake_ontap.py" $((port + $1)) "$work/c$1.json" "$work/c$1.log" & pids+=($!)
    wait_port $((port + $1))
}
fresh 0
AAPMOCK_LOG="$work/aap.jsonl" AAPMOCK_INV_LOG="$work/inv.jsonl" AAPMOCK_SURVEY_LOG="$work/survey.jsonl" \
    "$py" "$repo/tests/servicenow/aapmock.py" $((port + 20)) >/dev/null 2>&1 & pids+=($!)
SNMOCK_LOG="$work/sn.jsonl" "$py" "$repo/tests/servicenow/snmock.py" $((port + 21)) >/dev/null 2>&1 & pids+=($!)
mkdir -p "$work/mail"
"$py" "$repo/tests/email/fake_smtp.py" $((port + 22)) "$work/mail" 2>/dev/null & pids+=($!)
wait_port $((port + 20)); wait_port $((port + 21)); wait_port $((port + 22))
c0="http://127.0.0.1:$port"
mail="\"report_email_to\": \"storage@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
      \"report_email_smtp_port\": $((port + 22)), \"report_email_security\": \"none\""
export CONTROLLER_HOST="http://127.0.0.1:$((port + 20))" CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD=goodpw

# run NAME EXPECT(ok|fail) PLAYBOOK [args...] -- TEXT...  (NOT=text: must not be in the output)
run() {
    local tname="$1" want="$2" pb="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ONTAP_USERNAME=svc_rw ONTAP_PASSWORD=expand-Test9 ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i "$work/inv.yml" "playbooks/$pb" "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -6 | cut -c1-400; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -6 | cut -c1-400; fails=$((fails + 1)); return 1; }
    done
    if [ -n "${NOT:-}" ] && grep -qF -- "$NOT" "$work/out.txt"; then echo "FAIL - $tname: output has: $NOT"; fails=$((fails + 1)); return 1; fi
    if grep -qF -- "expand-Test9" "$work/out.txt"; then echo "FAIL - $tname: the ONTAP password is in the output"; fails=$((fails + 1)); return 1; fi
    echo "ok   - $tname"
}
patches() { grep -cE '^(PATCH|POST)' "$work/c${1:-0}.log" 2>/dev/null || true; }
check() {   # check NAME CONDITION...
    local tname="$1"; shift
    if "$@"; then echo "ok   - $tname"; else echo "FAIL - $tname"; fails=$((fails + 1)); fi
}
# the survey's last list for template 58: its volume question as JSON
lastq() { "$py" -c 'import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
rows = [r for r in rows if r["kind"] == "job" and r["id"] == 58]
q = [x for x in rows[-1]["spec"]["spec"] if x["variable"] == "ontap_expand_volume"][0] if rows else {}
print(json.dumps(q))' "$work/survey.jsonl"; }
pick() { lastq | "$py" -c 'import json, sys; q = json.load(sys.stdin); c = q["choices"]; c = c if isinstance(c, list) else c.split("\n")
print([x for x in c if x.startswith(sys.argv[1] + " ")][0])' "$1"; }
cl="\"ontap_clusters\": [\"$c0\"], \"ontap_expand_exclude\": [\"svm_backup:*\"], \"ontap_expand_templates\": [\"NetApp - expand a volume\"]"

# ---- the list --------------------------------------------------------------------------------------------------------
run "the list, a dry run (Check): says what it would write, writes nothing" ok ontap_volume_list.yml --check -e "{$cl}" \
    -- "NetApp volume list: 6 choices, 5 excluded" "6 choice(s) - would be updated (the question would be added)"
check "  no survey written by the dry run" test ! -s "$work/survey.jsonl"
run "the list: 6 choices (one per LUN), 5 excluded each with its reason, the question added single select" ok ontap_volume_list.yml -e "{$cl}" \
    -- "NetApp volume list: 6 choices, 5 excluded" "vol_ds01 (svm_vmware) - LUN 9.0 GB, 10% free [LUN]" \
       "vol_ds03/lun_a (svm_vmware) - LUN 900.0 GB, 21% free [LUN lun_a of 2]" "vol_ds03/lun_b (svm_vmware) - LUN 900.0 GB, 67% free" \
       "SVM root volume - always" "DP volume (a SnapMirror destination) - always" "offline - always" "FlexGroup - not yet" \
       "ontap_expand_exclude: svm_backup:*" "NetApp - expand a volume, question ontap_expand_volume: 6 choice(s) - updated (the question was added)"
check "  the question: single select, required, 6 choices with their keys" "$py" -c 'import json, sys
q = json.loads(sys.argv[1]); c = q["choices"] if isinstance(q["choices"], list) else q["choices"].split("\n")
sys.exit(not (q["type"] == "multiplechoice" and q["required"] and len(c) == 6 and all("[vol|" in x for x in c)))' "$(lastq)"
check "  the list only read: no PATCH" test "$(patches)" = 0

# ---- a Check run, then the change ------------------------------------------------------------------------------------
ds01="$(pick vol_ds01)"
check "  fullest first, by the LUN: one volume's two LUNs apart - lun_a (79 % written) before vol_home, lun_b (33 %) after it" "$py" -c 'import json, sys
q = json.loads(sys.argv[1]); c = q["choices"] if isinstance(q["choices"], list) else q["choices"].split("\n")
i = lambda p: [k for k, x in enumerate(c) if x.startswith(p)][0]
sys.exit(not i("vol_ds03/lun_a ") < i("vol_home ") < i("vol_ds03/lun_b "))' "$(lastq)"
run "a Check run: the plan only (the LUN 9.0 -> 10.0 GB, the volume as much as the thick LUN and its 5 % snapshot reserve need), nothing changed" ok \
    ontap_volume_expand.yml --check -e "{$cl, \"ontap_expand_volume\": \"$ds01\"}" \
    -- "NetApp expand: vol_ds01 - the plan: LUN 9.0 GB -> 10.0 GB; volume 10.0 GB -> 10.5 GB (thick LUN + 5 % snapshot reserve) (nothing changed)" \
       "Not run - the plan only (Check)" "/vol/vol_ds01/lun_ds01 space.size 9.0 GB -> 10.0 GB"
check "  the Check run changed nothing" test "$(patches)" = 0
run "grown: the volume and its LUN read back, the ticket in the comment, the datastore by hand (no vCenter credential), emailed" ok \
    ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$ds01\", \"ontap_expand_incident_number\": \"xinc0001234\", $mail}" \
    -- "NetApp expand: vol_ds01 grown LUN 9.0 GB -> 10.0 GB; volume 10.0 GB -> 10.5 GB (thick LUN + 5 % snapshot reserve) - expand the datastore in vCenter by hand" \
       "Done - read back 10.5 GB" \
       "Done - read back 10.0 GB" "Increase Datastore Capacity, pick the device naa.600a09803830416231446d54357a5871" \
       "XINC0001234 (typed at the launch)"
check "  ONTAP got: the volume to 10779 MB with the comment kept and added to, then the LUN to 10 GB" grep -qE \
    '^PATCH /api/storage/volumes/v-ds01 \{"size": 11302600704, "comment": "app team datastore \| XINC0001234: \+1.0 GB [0-9-]+ \(AAP job -\)"\}' "$work/c0.log"
check "  ... and the LUN" grep -qF 'PATCH /api/storage/luns/l-ds01 {"space": {"size": 10737418240}}' "$work/c0.log"
check "  the email went out" grep -rqF "vol_ds01 grown LUN 9.0 GB" "$work/mail"
run "the same choice again: the LUN already 10.0 GB - nothing to do (green), nothing changed" ok ontap_volume_expand.yml \
    -e "{$cl, \"ontap_expand_volume\": \"$ds01\"}" -- "NetApp expand: vol_ds01 already LUN 10.0 GB - nothing to do"
check "  still the 2 changes of before" test "$(patches)" = 2
ds03a="$(pick vol_ds03/lun_a)"
run "a thick LUN whose volume has room: the LUN grown 900 -> 990 GB, the volume not" ok ontap_volume_expand.yml \
    -e "{$cl, \"ontap_expand_volume\": \"$ds03a\"}" \
    -- "NetApp expand: vol_ds03 grown LUN 900.0 GB -> 990.0 GB; volume 2.00 TB stays (it has room for the LUN's growth already (thick LUN))" \
       "Not needed - it has room for the LUN's growth already"
check "  ... the volume's size not changed (its comment only)" test "$(grep 'PATCH /api/storage/volumes/v-ds03' "$work/c0.log" | grep -c '"size"')" = 0
check "  ... the LUN to 990 GB" grep -qF 'PATCH /api/storage/luns/l-a {"space": {"size": 1063004405760}}' "$work/c0.log"

# ---- refusals: nothing changed ---------------------------------------------------------------------------------------
sql="$(pick vol_sql01)"
home="$(pick vol_home)"
run "the default over 97 %: refused, with the largest size that fits" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$sql\"}" \
    -- "Default 204.8 GB would put aggr2 at 97.1 %; up to 180.0 GB fits" "Launch again with a size in the Size box: 180.0GB or less"
run "that size as Check: the plan says autosize's maximum is under the new size (and is left as it is)" ok ontap_volume_expand.yml --check \
    -e "{$cl, \"ontap_expand_volume\": \"$sql\", \"ontap_expand_size\": \"180GB\"}" \
    -- "Autosize maximum 2.10 TB is below the new size 2.18 TB; ONTAP raises it (to about 120 % of the new size)."
run "that size typed: grown 2.00 TB -> 2.18 TB" ok ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$sql\", \"ontap_expand_size\": \"180GB\"}" \
    -- "NetApp expand: vol_sql01 grown 2.00 TB -> 2.18 TB" "The Size box: 180GB" \
       "autosize maximum 2.10 TB is below the new size 2.18 TB; ONTAP raises it (to about 120 % of the new size) - its maximum reads"
check "  5 changes now (vol_ds03's comment and LUN; vol_sql01: its size only - autosize never changed)" test "$(patches)" = 5
check "  ... vol_sql01's autosize not touched" test "$(grep 'PATCH /api/storage/volumes/v-sql01' "$work/c0.log" | grep -c autosize)" = 0
n=$(patches)
bk="vol_bk01 (svm_backup, cluster1) - 4.00 TB [vol|$c0|svm_backup|vol_bk01||4398046511104||v-bk01]"
run "an excluded volume named by hand: refused" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$bk\"}" \
    -- "vol_bk01 NOT grown - excluded: ontap_expand_exclude: svm_backup:*" "even when its name is typed by hand"
root="svm1_root [vol|$c0|svm1|svm1_root||1073741824||v-root]"
run "an SVM root named by hand: refused whatever the settings" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$root\"}" \
    -- "svm1_root NOT grown - excluded: SVM root volume - always"
other="vol_home (svm1, cluster9) [vol|http://192.0.2.99:443|svm1|vol_home||1649267441664]"
run "a cluster not in ontap_clusters: refused before any request" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$other\"}" \
    -- "which is not in ontap_clusters - nothing was read or changed"
run "two volumes picked (a multiple-select question): refused" fail ontap_volume_expand.yml \
    -e "{$cl, \"ontap_expand_volume\": [\"$home\", \"$sql\"]}" -- "pick one volume per run (2 were picked)"
again="vol_home (svm1, cluster1) [vol|$c0|svm1|vol_home||1649267441664||0000-made-again]"
run "a volume made again under the listed name (another UUID): refused" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$again\"}" \
    -- "is not the one the list showed"
run "text without the list's key: refused" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"vol_home\"}" \
    -- "Pick the volume from the list"
ds03="vol_ds03 (svm_vmware, cluster1) [vol|$c0|svm_vmware|vol_ds03||2199023255552||v-ds03]"
run "a volume with two LUNs named without one: refused" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$ds03\"}" \
    -- "holds 2 LUN(s): pick its LUN in the list"
run "a size that is not one: refused" fail ontap_volume_expand.yml -e "{$cl, \"ontap_expand_volume\": \"$home\", \"ontap_expand_size\": \"lots\"}" \
    -- "the size 'lots' is not understood"
run "ontap_expand_incident: create without a group: refused before anything is read" fail ontap_volume_expand.yml \
    -e "{$cl, \"ontap_expand_volume\": \"$home\", \"ontap_expand_incident\": \"create\"}" -- "create needs ontap_expand_incident_group"
check "  none of the refusals changed anything" test "$(patches)" = "$n"

# ---- ontap_expand_incident: create (the fake ServiceNow) -----------------------------------------------------------------
SN_HOST="http://127.0.0.1:$((port + 21))" SN_USERNAME=api SN_PASSWORD=goodpw \
run "create: an incident opened before the change, resolved with the steps after it, its number in the comment" ok ontap_volume_expand.yml \
    -e "{$cl, \"ontap_expand_volume\": \"$home\", \"ontap_expand_size\": \"10GB\", \"ontap_expand_incident\": \"create\", \"ontap_expand_incident_group\": \"STORAGE-TEAM\"}" \
    -- "NetApp expand: vol_home grown 1.50 TB -> 1.51 TB" "INC0" "opened" "Resolved - the steps above in its close notes"
check "  ServiceNow got the incident (group STORAGE-TEAM) and its resolution" "$py" -c 'import json, sys
rows = [json.loads(x) for x in open(sys.argv[1]) if x.strip()]
post = [r for r in rows if r.get("method") == "POST" and "incident" in r.get("path", "")]
patch = [r for r in rows if r.get("method") == "PATCH" and "incident" in r.get("path", "")]
b = post and (post[0].get("body") or {})
sys.exit(not (post and b.get("assignment_group") == "STORAGE-TEAM" and "vol_home" in b.get("short_description", "") and patch
              and "close_notes" in json.dumps(patch[-1].get("body"))))' "$work/sn.jsonl"
check "  the incident's number is in the volume's comment" grep -qE '^PATCH /api/storage/volumes/v-home \{"size": [0-9]+, "comment": "INC[0-9]+: \+10.0 GB' "$work/c0.log"
n=$(patches)
SN_HOST="http://127.0.0.1:$((port + 21))" SN_USERNAME=api SN_PASSWORD=goodpw \
run "create, but nothing to do: no incident opened" ok ontap_volume_expand.yml \
    -e "{$cl, \"ontap_expand_volume\": \"$ds01\", \"ontap_expand_incident\": \"create\", \"ontap_expand_incident_group\": \"STORAGE-TEAM\"}" \
    -- "already LUN 10.0 GB - nothing to do"
check "  ... only the one incident of before" test "$(grep -c '"POST"' "$work/sn.jsonl")" = 1

# ---- the volume changes between the plan and the change; ONTAP's job fails ---------------------------------------------
fresh 1 race
cl1="\"ontap_clusters\": [\"http://127.0.0.1:$((port + 1))\"]"
h1="vol_home (svm1, cluster1) [vol|http://127.0.0.1:$((port + 1))|svm1|vol_home||1649267441664||v-home]"
run "the volume grew between the plan and the change: stopped, nothing changed" fail ontap_volume_expand.yml -e "{$cl1, \"ontap_expand_volume\": \"$h1\"}" \
    -- "the volume changed between the plan and the change"
check "  no PATCH" test "$(patches 1)" = 0
fresh 2 jobfail
h2="vol_home (svm1, cluster1) [vol|http://127.0.0.1:$((port + 2))|svm1|vol_home||1649267441664||v-home]"
run "ONTAP's job fails: the step says so, the job fails" fail ontap_volume_expand.yml \
    -e "{\"ontap_clusters\": [\"http://127.0.0.1:$((port + 2))\"], \"ontap_expand_volume\": \"$h2\"}" \
    -- "FAILED at: grow the volume" "ONTAP's job failed: fake job"

echo
[ $fails -eq 0 ] && echo "PASS - NetApp expand e2e" || echo "FAIL - $fails NetApp expand e2e check(s) failed"
exit $fails
