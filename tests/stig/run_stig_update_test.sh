#!/usr/bin/env bash
# Workflow 37 - STIG Manager updated, with an approval: the STIG recheck's check (playbooks/stig_recheck.yml with
# check_stig_update_workflow: true) and the job after the approval (playbooks/stigman_update.yml), on this box as a plain
# user, with the fake oscap of the recheck test and the stand-in Keycloak + STIG Manager (tests/stig/stigman_mock.py,
# which writes only for the clients it lists). In order: the check lists the reviews to write (fixed, answered), hands
# them on (set_stats, read here from CUSTOM STATS) and fails on purpose (NEEDS APPROVAL), writing nothing; the update in
# Check mode writes nothing; with the read-only client STIG Manager refuses and the job fails; with the writing client
# each review is written, read back, as planned (result, status, detail, comment, result engine); run again: already so;
# a review changed in STIG Manager since the check is left; a review STIG Manager rejects fails the job; a plan item
# that is not one the check gives is refused; no plan: nothing written; a review with attachments in STIG Manager is
# left out of the check's plan, and left as it is by the update. Only GET, the token POSTs and the reviews' POST
# reach STIG Manager. 0.72.0, a host with no asset (check_stig_update_create_assets): off - not rechecked; on - planned
# (every rule), Check creates nothing, a client without Manage is refused, then created once with its reviews.
#   bash tests/stig/run_stig_update_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; STIG_KEEP=FILE keeps the last output)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${STIG_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
sm_pid=""
cleanup() { [ -n "${STIG_KEEP:-}" ] && cp "$work/out.txt" "$STIG_KEEP"; [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0
port_sm="${STIG_UPDATE_SM_PORT:-12597}"
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/bin" "$work/content"
"$py" "$here/make_results.py" "$work/content/ssg-test-ds.xml" '{}'
# the session timeout still fails, the banner fails, the rest pass
echo '{"accounts_tmout": "fail", "banner_etc_issue": "fail"}' > "$work/results.json"
cat > "$work/bin/oscap" <<EOF
#!/bin/bash
res=""
while [ \$# -gt 0 ]; do case "\$1" in --results) res="\$2"; shift ;; esac; shift; done
"$py" "$here/make_results.py" "\$res" "\$(cat "$work/results.json")" || exit 1
grep -q '"fail"' "$work/results.json" && exit 2 || exit 0
EOF
chmod +x "$work/bin/oscap"
"$py" - "$work/sm.json" <<'PY'
import json, sys
def rv(rule, result, status, ver, sev, title, comment=""):
    return {"ruleId": rule, "ruleIds": [rule], "result": result, "status": {"label": status}, "ts": "2026-08-15T10:00:00Z",
            "comment": comment, "detail": "", "rule": {"ruleId": rule, "version": ver, "severity": sev, "title": title}}
R = [("SV-257883r1044925_rule", "RHEL-09-412035", "medium", "RHEL 9 must automatically exit interactive sessions after 10 minutes."),
     ("SV-258176r1045388_rule", "RHEL-09-654225", "medium", "RHEL 9 must audit all uses of privileged commands."),
     ("SV-257985r1069364_rule", "RHEL-09-255045", "medium", "RHEL 9 must not permit direct logons to the root account using remote access through SSH."),
     ("SV-257821r1015077_rule", "RHEL-09-214020", "high", "RHEL 9 must check the GPG signature of locally installed software packages."),
     ("SV-257779r958390_rule", "RHEL-09-211020", "medium", "RHEL 9 must display the Standard Mandatory DoD Notice and Consent Banner."),
     ("SV-257835r1044874_rule", "RHEL-09-215040", "high", "RHEL 9 must not have the telnet-server package installed."),
     ("SV-258012r1014855_rule", "RHEL-09-271010", "medium", "RHEL 9 must display the banner before granting access via the GUI."),
     ("SV-258013r1045082_rule", "RHEL-09-271015", "medium", "RHEL 9 must prevent a user from overriding the banner-message-enable setting."),
     ("SV-258999r1000001_rule", "RHEL-09-999990", "medium", "A manual check: a document, never scanned.")]
reviews = [rv(*R[0][:1], "fail", "saved", *R[0][1:], comment="POA&M 12: the timeout is set by the new build"),
           rv(R[1][0], "fail", "accepted", *R[1][1:], comment="POA&M 14: auditd rules in the next change window"),
           rv(R[2][0], "pass", "accepted", *R[2][1:]), rv(R[3][0], "pass", "submitted", *R[3][1:]),
           rv(R[4][0], "pass", "rejected", *R[4][1:])]
json.dump({"secret": "s3cret", "client": "autostig", "writers": ["stigwrite"], "reject_fail_without_comment": True,
           "collections": [{"collectionId": "1", "name": "RHEL Servers"}],
           "assets": {"1": [{"assetId": "11", "name": "localhost", "fqdn": "", "stigs": [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R7"}]}]},
           "reviews": {"11": reviews},
           "rules": {"RHEL_9_STIG": [{"ruleId": r, "groupId": "V-" + r[3:9], "version": v, "severity": s, "title": t} for r, v, s, t in R]}},
          open(sys.argv[1], "w"))
PY
cp "$work/sm.json" "$work/sm-first.json"
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
check_stig_oscap: $work/bin/oscap
check_stig_datastream: $work/content/ssg-test-ds.xml
check_stig_dir: $work/kept
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: autostig
check_stig_stigman_client_secret: s3cret
report_web_dest: $work/site
EOF
writer='{"check_stig_stigman_client_id": "stigwrite"}'

play() {  # play PLAYBOOK NAME EXPECT(ok|fail|2) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local pb="$1" tname="$2" want="$3"; shift 3
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_SHOW_CUSTOM_STATS=True \
        "$ap" -i localhost, -c local "playbooks/$pb" -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" "${args[@]}") > "$work/out.txt" 2>&1
    rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; } || { [ "$want" = 2 ] && [ $rc -ne 2 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -n "fatal\|FAILED\|ERROR\|msg" "$work/out.txt" | cut -c1-400 | tail -15; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "localhost \|NEEDS\|written\|would write\|fatal\|msg" "$work/out.txt" | cut -c1-300 | head -20; fails=$((fails + 1)); fi
}
review() {  # review RULE -> "result status" as the stand-in STIG Manager holds it
    "$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); r = [x for x in d["reviews"]["11"] if x["ruleId"] == sys.argv[2]]
print(("%s %s" % (r[0]["result"], r[0]["status"]["label"])) if r else "none")' "$work/sm.json" "$1"
}
writes() { [ -e "$work/sm.log.writes" ] && wc -l < "$work/sm.log.writes" || echo 0; }

# ---- the check: what Approve writes, handed on, NEEDS APPROVAL - nothing written --------------------------------------
play stig_recheck.yml "the check: the reviews to write listed, NEEDS APPROVAL (exit 2)" 2 -e '{"check_stig_update_workflow": true}' -- \
    "NEEDS APPROVAL - 5 review(s) of 1 asset(s) to write to STIG Manager" \
    "localhost RHEL_9_STIG V-258176: Open -> Not a Finding (submitted)" "localhost RHEL_9_STIG V-257779: Not a Finding -> Open (submitted)" \
    "localhost RHEL_9_STIG V-257835: no review -> Not a Finding (submitted)" "!RHEL_9_STIG V-257883:" "!RHEL_9_STIG V-258999:" "stigman_update_plan"
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' > "$work/stats.json"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); json.dump({"stigman_update_plan": d["stigman_update_plan"]}, open(sys.argv[2], "w"))' \
    "$work/stats.json" "$work/plan.json" 2>/dev/null && echo "ok   - ... the plan handed on (set_stats stigman_update_plan)" \
    || { echo "FAIL - no plan in the custom stats: $(head -c 300 "$work/stats.json")"; fails=$((fails + 1)); }
[ "$(writes)" = 0 ] && ! grep -q '^POST /api/' "$work/sm.log" && echo "ok   - ... nothing written to STIG Manager by the check" \
    || { echo "FAIL - the check wrote"; fails=$((fails + 1)); }
if "$py" - "$work/site" <<'PY'
import glob, json, sys
r = json.load(open(sorted(glob.glob(sys.argv[1] + "/*/latest.json"))[-1]))
r = r.get("report", r)
s = r["sections"][0]
assert s["title"] == "Approve writes these reviews to STIG Manager", s["title"]
assert len(s["rows"]) == 5 and any(x[3] == "V-258176" and x[6] == "Open (accepted)" and x[7] == "Not a Finding" and x[9] == "kept" for x in s["rows"]), s["rows"]
assert any(x[3] == "V-257779" and x[7] == "Open" and x[9] == "set" for x in s["rows"])
PY
then echo "ok   - ... the report starts with what Approve writes (a review's own comment kept; a finding gets one)"
else echo "FAIL - the report's plan section"; fails=$((fails + 1)); fi

# ---- the update: Check, the read-only client, then the writing one -----------------------------------------------------
play stigman_update.yml "the update in Check mode: would write 5, nothing written" ok -e "@$work/plan.json" -e "$writer" --check -- \
    "would write - localhost RHEL_9_STIG V-258176: Open -> Not a Finding (submitted)"
[ "$(writes)" = 0 ] && echo "ok   - ... nothing written" || { echo "FAIL - Check mode wrote"; fails=$((fails + 1)); }
play stigman_update.yml "the read-only client: STIG Manager refuses, nothing written, the job fails" fail -e "@$work/plan.json" -- \
    "5 review(s) not written" "HTTP 403"
[ "$(review SV-258176r1045388_rule)" = "fail accepted" ] && echo "ok   - ... the review as it was" || { echo "FAIL - written by the read-only client"; fails=$((fails + 1)); }
play stigman_update.yml "the writing client: the 5 reviews written, read back" ok -e "@$work/plan.json" -e "$writer" -- \
    "written - localhost RHEL_9_STIG V-258176: Open -> Not a Finding (submitted)" "written - localhost RHEL_9_STIG V-257835: no review -> Not a Finding (submitted)"
[ "$(review SV-258176r1045388_rule)" = "pass submitted" ] && [ "$(review SV-257779r958390_rule)" = "fail submitted" ] \
    && [ "$(review SV-257835r1044874_rule)" = "pass submitted" ] && [ "$(review SV-257883r1044925_rule)" = "fail saved" ] \
    && [ "$(review SV-258999r1000001_rule)" = "none" ] \
    && echo "ok   - ... STIG Manager: fixed -> Not a Finding, answered as the scan said; still open and by hand not touched" \
    || { echo "FAIL - what STIG Manager holds: $(review SV-258176r1045388_rule) / $(review SV-257779r958390_rule) / $(review SV-257835r1044874_rule)"; fails=$((fails + 1)); }
if "$py" - "$work/sm.log.writes" "$work/sm.json" <<'PY'
import json, sys
posts = [json.loads(l) for l in open(sys.argv[1])]
assert len(posts) == 1 and posts[0]["collection"] == "1" and posts[0]["asset"] == "11", posts      # one POST for the asset
by = dict((b["ruleId"], b) for b in posts[0]["reviews"])
assert set(by) == {"SV-258176r1045388_rule", "SV-257779r958390_rule", "SV-257835r1044874_rule", "SV-258012r1014855_rule", "SV-258013r1045082_rule"}, by.keys()
assert by["SV-258176r1045388_rule"]["comment"] is None                             # its POA&M comment kept
assert by["SV-257779r958390_rule"]["comment"].startswith("Set by the STIG recheck (site-automation), approved in AAP: OpenSCAP checked it again on localhost")
assert all(b["resultEngine"]["product"] == "OpenSCAP" and b["resultEngine"]["type"] == "scap" and b["detail"] and b["status"] == "submitted" for b in by.values())
d = json.load(open(sys.argv[2]))
r = [x for x in d["reviews"]["11"] if x["ruleId"] == "SV-258176r1045388_rule"][0]
assert r["comment"] == "POA&M 14: auditd rules in the next change window", r
PY
then echo "ok   - ... one POST for the asset: result, detail, status, the result engine; a review's own comment kept, a finding's set"
else echo "FAIL - what was posted"; head -c 1500 "$work/sm.log.writes"; fails=$((fails + 1)); fi

play stigman_update.yml "the same plan again: already so, nothing written" ok -e "@$work/plan.json" -e "$writer" -- \
    "already so - localhost RHEL_9_STIG V-258176" "!written - "
[ "$(writes)" = 1 ] && echo "ok   - ... no second POST" || { echo "FAIL - written twice"; fails=$((fails + 1)); }

# ---- a review changed in STIG Manager since the check: left as it is ----------------------------------------------------
cp "$work/sm-first.json" "$work/sm.json"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1]))
for r in d["reviews"]["11"]:
    if r["ruleId"] == "SV-258176r1045388_rule":
        r.update(result="notapplicable", status={"label": "submitted"}, ts="2026-11-01T09:00:00Z")
json.dump(d, open(sys.argv[1], "w"))' "$work/sm.json"
rm -f "$work/sm.log.writes"
play stigman_update.yml "a review changed since the check: left, said; the rest written" ok -e "@$work/plan.json" -e "$writer" -- \
    "changed since - localhost RHEL_9_STIG V-258176" "STIG Manager has it as notapplicable (submitted, 2026-11-01T09:00:00Z) now" \
    "written - localhost RHEL_9_STIG V-257779"
[ "$(review SV-258176r1045388_rule)" = "notapplicable submitted" ] && echo "ok   - ... the person's review kept" || { echo "FAIL - overwritten"; fails=$((fails + 1)); }

# ---- rejected by STIG Manager; a plan item the check never gives; no plan -----------------------------------------------
cp "$work/sm-first.json" "$work/sm.json"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1]))
for p in d["stigman_update_plan"]:
    if p["v"] == "V-257779": p["comment"] = None
json.dump(d, open(sys.argv[2], "w"))' "$work/plan.json" "$work/plan-nocomment.json"
play stigman_update.yml "a review STIG Manager rejects (a finding without a comment): said, the job fails" fail -e "@$work/plan-nocomment.json" -e "$writer" -- \
    "rejected - localhost RHEL_9_STIG V-257779" "STIG Manager: comment is required for findings" "written - localhost RHEL_9_STIG V-258176"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); d["stigman_update_plan"][0].update(result="delete", line="bad one")
d["stigman_update_plan"][1].update(ruleId="SV-1/../../users", line="bad two")
json.dump({"stigman_update_plan": d["stigman_update_plan"][:2]}, open(sys.argv[2], "w"))' "$work/plan.json" "$work/plan-bad.json"
n=$(writes)
play stigman_update.yml "plan items the check never gives: refused, nothing written" fail -e "@$work/plan-bad.json" -e "$writer" -- \
    "bad - bad one - a result this job never writes: delete" "bad - bad two - not a STIG rule id"
[ "$(writes)" = "$n" ] && echo "ok   - ... nothing posted" || { echo "FAIL - a bad item was posted"; fails=$((fails + 1)); }
play stigman_update.yml "no plan (not run from workflow 37): nothing written, said why" fail -e "$writer" -- \
    "No plan: this job writes only the reviews the STIG recheck's or the checklist import's check listed"

# ---- a review with attachments in STIG Manager (evidence a person attached): never written ------------------------------
cp "$work/sm-first.json" "$work/sm.json"
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1]))
for r in d["reviews"]["11"]:
    if r["ruleId"] == "SV-258176r1045388_rule":       # as the review panel writes it: the list, and each file under its digest
        r["metadata"] = {"artifacts": json.dumps([{"name": "auditd-plan.pdf", "digest": "ab12", "type": "application/pdf", "size": 9}]),
                         "ab12": "JVBERi0xLjQK"}
json.dump(d, open(sys.argv[1], "w"))' "$work/sm.json"
rm -f "$work/sm.log.writes"
play stig_recheck.yml "the check: a review with attachments left out of the plan, listed" 2 -e '{"check_stig_update_workflow": true}' -- \
    "NEEDS APPROVAL - 4 review(s) of 1 asset(s)" \
    "localhost RHEL_9_STIG V-258176: Open -> Not a Finding (submitted) - LEFT AS IT IS: it has attachments in STIG Manager" \
    "localhost RHEL_9_STIG V-257779: Not a Finding -> Open (submitted)"
if "$py" - "$work/site" <<'PY'
import glob, json, sys
r = json.load(open(sorted(glob.glob(sys.argv[1] + "/*/latest.json"))[-1]))
r = r.get("report", r)
t = [s["title"] for s in r["sections"]]
assert t[:2] == ["Approve writes these reviews to STIG Manager", "Left as they are: reviews with attachments (1)"], t
left = r["sections"][1]["rows"]
assert left == [[left[0][0], "RHEL Servers / localhost", "V-258176", "Open (accepted)", "Not a Finding", "auditd-plan.pdf"]], left
assert all(x[3] != "V-258176" for x in r["sections"][0]["rows"])
PY
then echo "ok   - ... the report lists it apart, with the file's name; not in what Approve writes"
else echo "FAIL - the report's attachments section"; fails=$((fails + 1)); fi
play stig_recheck.yml "the check with check_stig_update_skip_attachments: false: in the plan like the others" 2 \
    -e '{"check_stig_update_workflow": true, "check_stig_update_skip_attachments": false}' -- "NEEDS APPROVAL - 5 review(s)" "!LEFT AS IT IS"
play stigman_update.yml "the update: a review given attachments after the check - left as it is, said; the rest written" ok \
    -e "@$work/plan.json" -e "$writer" -- \
    "has attachments - localhost RHEL_9_STIG V-258176: Open -> Not a Finding (submitted) - 1 attachment(s) in STIG Manager: auditd-plan.pdf" \
    "written - localhost RHEL_9_STIG V-257779"
[ "$(review SV-258176r1045388_rule)" = "fail accepted" ] && [ "$(review SV-257779r958390_rule)" = "fail submitted" ] \
    && echo "ok   - ... the review with attachments as it was; the others written" || { echo "FAIL - $(review SV-258176r1045388_rule)"; fails=$((fails + 1)); }

if grep -qvE '^(GET /api/|POST /token$|POST /api/collections/1/reviews/11$)' "$work/sm.log"; then
    echo "FAIL - a request other than a read, the token or the asset's reviews reached STIG Manager"; grep -vE '^(GET /api/|POST /token$|POST /api/collections/1/reviews/11$)' "$work/sm.log" | head -3; fails=$((fails + 1))
else echo "ok   - only GET, the token POSTs and POST /api/collections/1/reviews/11 reached STIG Manager"; fi

# ---- 0.72.0: a host with no asset (check_stig_update_create_assets) - created once approved, by a client with Manage ----
cp "$work/sm-first.json" "$work/sm.json"
: > "$work/sm.log"; rm -f "$work/sm.log.writes"
newvars='{"check_stig_update_workflow": true, "check_stig_update_create_assets": true, "check_stig_stigman_asset": "newbox", "check_stig_stigman_collection": "RHEL Servers"}'
play stig_recheck.yml "create off (the default): a host with no asset is not rechecked, nothing planned" 2 \
    -e '{"check_stig_update_workflow": true, "check_stig_stigman_asset": "newbox", "check_stig_stigman_collection": "RHEL Servers"}' -- \
    "NOT RECHECKED - nothing to recheck: no asset named newbox with RHEL_9_STIG in the collection RHEL Servers" "!NEW ASSET" "!NEEDS APPROVAL"
play stig_recheck.yml "the check: no asset - every rule checked, the asset and its reviews planned, NEEDS APPROVAL" 2 -e "$newvars" -- \
    "no asset in STIG Manager - newbox to create in RHEL Servers once approved, with RHEL_9_STIG V2R7: every rule checked (a full scan)" \
    "NEEDS APPROVAL - 8 review(s) of 1 asset(s) to write to STIG Manager, and 1 new asset(s) to create" \
    "NEW ASSET newbox in RHEL Servers with RHEL_9_STIG V2R7 - then 8 review(s): Not a Finding 6, Open 2, Not Applicable 0"
grep -P '^\tRUN: ' "$work/out.txt" | sed 's/^\tRUN: //' | "$py" -c 'import json, sys; json.dump({"stigman_update_plan": json.load(sys.stdin)["stigman_update_plan"]}, open(sys.argv[1], "w"))' "$work/plan-new.json"
[ "$(writes)" = 0 ] && echo "ok   - ... nothing written by the check" || { echo "FAIL - the check wrote"; fails=$((fails + 1)); }
play stigman_update.yml "the update in Check mode: would create, nothing written" ok -e "@$work/plan-new.json" -e "$writer" --check -- \
    "would create - NEW ASSET newbox in RHEL Servers" "its reviews: would write 8"
play stigman_update.yml "a client without the Manage grant: not created (HTTP 403), its reviews not written, the job fails" fail \
    -e "@$work/plan-new.json" -e "$writer" -- "error - NEW ASSET newbox" "not created: HTTP 403" "its reviews: asset not created 8"
[ "$(writes)" = 0 ] && echo "ok   - ... nothing written" || { echo "FAIL - written without Manage"; fails=$((fails + 1)); }
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); d["managers"] = ["stigwrite"]; json.dump(d, open(sys.argv[1], "w"))' "$work/sm.json"
play stigman_update.yml "with Manage: newbox created with RHEL_9_STIG, its 8 reviews written and read back" ok -e "@$work/plan-new.json" -e "$writer" -- \
    "created - NEW ASSET newbox in RHEL Servers" "its reviews: written 8"
"$py" -c 'import glob, json, sys
r = json.load(open(sorted(glob.glob(sys.argv[1] + "/*/latest.json"))[-1])); r = r.get("report", r)
assert r["title"] == "STIG Manager update: 8 review(s) written, 1 asset(s) created", r["title"]
assert r["sections"][0]["title"] == "New assets" and r["sections"][0]["rows"][0][5:7] == ["created", "written 8"], r["sections"][0]
assert not any(s.get("title") == "Each review of the plan" for s in r["sections"])' "$work/site" \
    && echo "ok   - ... the report: 8 written, 1 created - the new asset in one row, its reviews counted" || { echo "FAIL - the update report"; fails=$((fails + 1)); }
"$py" -c 'import json, sys; d = json.load(open(sys.argv[1])); a = [x for x in d["assets"]["1"] if x["name"] == "newbox"]
assert a and a[0]["stigs"] == [{"benchmarkId": "RHEL_9_STIG", "revisionStr": "V2R7"}] and a[0]["fqdn"] is None, a
r = d["reviews"][a[0]["assetId"]]
assert len(r) == 8 and all(x["status"]["label"] == "submitted" for x in r), r
assert all("checked it with the new asset" in x["comment"] for x in r), [x["comment"] for x in r]' "$work/sm.json" \
    && echo "ok   - ... in STIG Manager: the asset (no FQDN: the inventory name has no dot), 8 reviews submitted, each with the job's comment" \
    || { echo "FAIL - the new asset in STIG Manager"; fails=$((fails + 1)); }
n=$(writes)
# 1.24.0: the asset this plan made on its first run is used, each review read again: already so, nothing written
play stigman_update.yml "the same plan again: the asset is there - not created twice, nothing written" ok -e "@$work/plan-new.json" -e "$writer" -- \
    "there from an earlier run - NEW ASSET newbox" "its reviews: already so 8"
[ "$(writes)" = "$n" ] && echo "ok   - ... nothing posted" || { echo "FAIL - posted again"; fails=$((fails + 1)); }
if grep -qvE '^(GET /api/|POST /token$|POST /api/assets\?projection=stigs$|POST /api/collections/1/reviews/1[0-9][0-9]$)' "$work/sm.log"; then
    echo "FAIL - a request other than a read, the token, the new asset or its reviews"; fails=$((fails + 1))
else echo "ok   - only GET, the token, POST /api/assets and the new asset's reviews reached STIG Manager"; fi

echo
if [ $fails -eq 0 ]; then echo "all STIG Manager update scenarios passed"; else echo "$fails STIG Manager update scenario(s) FAILED"; exit 1; fi
