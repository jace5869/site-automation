#!/usr/bin/env bash
# The site portal's fix jobs (1.67.0 part E) against this machine as a Linux host, with a stand-in AAP (the report job's
# site_findings artifact, read back by roles/finding_ref), stand-in systemctl and journalctl, a fake ServiceNow
# (tests/servicenow/snmock.py) and tmp folders of their own:
#   Disk - plan a clean up / clean up one mount (workflow 49): what it would delete, the plan of its own workflow only,
#     the deleting with the old files gone and the new ones kept, the mount read again (fixed or still there), a mount no
#     tmp folder is on, nothing old, a Windows drive, a plan of another workflow or of another host;
#   Service - restart (workflow 50): status and log lines (read only), the restart of that one service, its state after,
#     the never-list refused in the look step, a failing restart, a finding of the wrong kind;
#   Disk - space request: the text (host, mount, size, used, %, growth per week and days to full from the history), no
#     history, a Windows drive, nothing sent;
#   Open ticket: dry_run sends nothing (the fake ServiceNow sees 0 requests), the draft equals what live sends, off
#     refuses, live opens one and the same finding again finds it (no second one), no credential refuses.
# The jobs that end in a result use the real roles/portal_result when it is there, else tests/portal/stub_roles.
#   bash tests/portal/run_portal_fix_test.sh                 (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_FIX_TEST_PYTHON:-$(command -v python3)}"
port="${PORTAL_FIX_TEST_PORT:-18700}"
snport=$((port + 1))
work="$(mktemp -d)"
pids=()
cleanup() { for p in "${pids[@]}"; do kill "$p" 2>/dev/null; done; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
mkdir -p "$work/bin" "$work/data/tmp/app" "$work/hist"
roles_path=""
[ -d "$repo/roles/portal_result" ] || roles_path="$here/stub_roles"

# ---- the findings the report job made --------------------------------------------------------------------------------
mount="$(df -P "$work/data/tmp" | awk 'NR==2{print $6}')"
"$py" - "$repo" "$work" "$mount" <<'EOPY'
import json, os, sys
repo, work, mount = sys.argv[1:4]
sys.path.insert(0, os.path.join(repo, "plugins", "filter"))
import finding_filters as ff
def e(kind, host, key, title, sev, data):
    return ff.entry(kind, host, key, title, sev, data)
size, avail = 100 * 1024 ** 3, 4 * 1024 ** 3
lin = [
    e("disk_full", "lin01.example.mil", mount, "%s is 99%% full" % mount, "critical",
      {"mount": mount, "pct": 99, "warn": 99, "crit": 100, "size_b": size, "avail_b": avail, "os": "linux", "look": "df -h"}),
    e("disk_full", "lin01.example.mil", "/srv/data", "/srv/data is 97% full", "critical",
      {"mount": "/srv/data", "pct": 97, "warn": 85, "crit": 95, "size_b": size, "avail_b": avail, "os": "linux"}),
    e("service_down", "lin01.example.mil", "httpd", "httpd is not running", "critical", {"service": "httpd", "state": "failed", "os": "linux"}),
    e("service_down", "lin01.example.mil", "sshd", "sshd is not running", "critical", {"service": "sshd", "state": "failed", "os": "linux"}),
    e("service_down", "lin01.example.mil", "stuck", "stuck is not running", "critical", {"service": "stuck", "state": "failed", "os": "linux"}),
    e("service_down", "lin01.example.mil", "gone", "gone is not installed", "warning", {"service": "gone", "state": "missing", "os": "linux"}),
    e("eventlog", "lin01.example.mil", "x", "an event", "warning", {"look": "journalctl -p err"}),
]
win = [e("disk_full", "win01.example.mil", "C:", "C: is 96% full", "critical", {"mount": "C:", "pct": 96, "warn": 85, "crit": 95, "os": "windows"}),
       e("service_down", "win01.example.mil", "Spooler", "Spooler is stopped", "warning", {"service": "Spooler", "state": "stopped", "os": "windows"})]
json.dump({"site_findings": {"lin01.example.mil": lin, "win01.example.mil": win}}, open(work + "/job4711.json", "w"))
# the same, with warn 1: the mount cannot be under it after any clean up
lin2 = json.loads(json.dumps(lin)); lin2[0]["data"]["warn"] = 1
json.dump({"site_findings": {"lin01.example.mil": lin2}}, open(work + "/job4712.json", "w"))
print(json.dumps({"mount": mount, "ids": [x["id"] for x in lin + win]}), file=open(work + "/ids.json", "w"))
EOPY
disk_id="disk_full:lin01.example.mil:$mount"
cat > "$work/aap.py" <<'EOPY'
import http.server, json, sys
port, work = int(sys.argv[1]), sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def answer(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer made-up-token":
            return self.answer(401, {"detail": "no"})
        if self.path == "/api/controller/v2/me/":
            return self.answer(200, {"results": [{"username": "reader"}]})
        if self.path == "/api/controller/v2/jobs/4713/":     # the forge: another template's job with a planted finding
            return self.answer(200, {"id": 4713, "name": "Some unrelated job template", "status": "failed",
                                     "playbook": "playbooks/our_own.yml", "finished": "2025-03-02T10:00:00Z",
                                     "artifacts": json.load(open("%s/job4711.json" % work))})
        for job in (4711, 4712):
            if self.path == "/api/controller/v2/jobs/%d/" % job:
                return self.answer(200, {"id": job, "name": "Linux health check", "status": "successful", "playbook": "playbooks/health_check.yml",
                                         "finished": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "artifacts": json.load(open("%s/job%d.json" % (work, job)))})
        return self.answer(404, {"detail": "Not found."})
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
EOPY
"$py" "$work/aap.py" "$port" "$work" & pids+=($!)
SNMOCK_LOG="$work/sn.jsonl" "$py" "$repo/tests/servicenow/snmock.py" "$snport" >/dev/null 2>&1 & pids+=($!)
for p in "$port" "$snport"; do for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null && break; sleep 0.1; done; done

# ---- stand-ins: systemctl and journalctl --------------------------------------------------------------------------
cat > "$work/bin/systemctl" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$work/systemctl.args"
unit="\${@: -1}"
case "\$1" in
  status) echo "* \$unit.service - made-up service"; echo "     Active: failed (Result: exit-code)"; exit 3;;
  restart) [ "\$unit" = stuck ] && { echo "Job for stuck.service failed" >&2; exit 1; }; echo active > "$work/state-\$unit"; exit 0;;
  is-active) cat "$work/state-\$unit" 2>/dev/null || echo failed; exit 0;;
esac
exit 0
EOS
cat > "$work/bin/journalctl" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$work/journalctl.args"
for n in 1 2 3; do echo "Nov 16 06:0\$n:00 lin01 \${2:-x}[1]: made-up log line \$n"; done
EOS
chmod +x "$work/bin/systemctl" "$work/bin/journalctl"

cat > "$work/inv.yml" <<EOI
all:
  hosts:
    lin01.example.mil: {ansible_connection: local, ansible_python_interpreter: $py, ansible_become: false}
    lin02.example.mil: {ansible_connection: local, ansible_python_interpreter: $py, ansible_become: false}
    win01.example.mil: {ansible_connection: winrm, ansible_host: 192.0.2.250}
EOI
cat > "$work/unsafe.yml" <<'EOU'
finding_id: !unsafe "disk_full:{{ 7 * 6 }}"
EOU

LAST_OUT=""
# run NAME ok|fail [ansible args...] -- expected text... (a text starting with ! must NOT be in the output)
run() {
  local name="$1" want="$2"; shift 2
  local args=() expect=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do args+=("$1"); shift; done
  [ $# -gt 0 ] && shift
  expect=("$@")
  local out rc
  out="$(cd "$repo" && PATH="$work/bin:$PATH" ANSIBLE_ROLES_PATH="$repo/roles${roles_path:+:$roles_path}" \
         CONTROLLER_HOST="${AAP_HOST-http://127.0.0.1:$port}" CONTROLLER_OAUTH_TOKEN="made-up-token" \
         ANSIBLE_SHOW_CUSTOM_STATS=1 ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
         ${RUN_ENV:-} "$ap" -i "$work/inv.yml" "${args[@]}" 2>&1)"
  rc=$?
  local ok=1 e
  if [ "$want" = ok ] && [ $rc -ne 0 ]; then ok=0; fi
  if [ "$want" = fail ] && [ $rc -eq 0 ]; then ok=0; fi
  for e in "${expect[@]}"; do
    if [ "${e:0:1}" = "!" ]; then
      if printf '%s' "$out" | grep -qF -- "${e:1}"; then ok=0; echo "   unexpected: ${e:1}"; fi
    elif ! printf '%s' "$out" | grep -qF -- "$e"; then ok=0; echo "   missing: $e"; fi
  done
  if [ $ok -eq 1 ]; then echo "ok   - $name"; else echo "FAIL - $name (rc $rc)"; printf '%s\n' "$out" | tail -40; fails=$((fails + 1)); fi
  LAST_OUT="$out"
}
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }
there() { [ -e "$1" ] && echo there || echo gone; }
by='{"portal_requested_by": "CN=Pat Example"}'
ref() { echo "-l lin01.example.mil -e target=lin01.example.mil -e report_job=${2:-4711} -e finding_id=$1"; }

# ---- disk: plan, approval's plan, clean up ---------------------------------------------------------------------------
dirs="{\"tmp_cleanup_dirs\": [{\"path\": \"$work/data/tmp\", \"days\": 1}]}"
head -c 200000 /dev/zero > "$work/data/tmp/app/old1.log"; head -c 300000 /dev/zero > "$work/data/tmp/old2.dat"
sleep 70
head -c 1000 /dev/zero > "$work/data/tmp/new.log"
dpb=playbooks/disk_cleanup_plan.yml
run "disk plan: the tmp folder on the finding's mount, what it would delete (nothing is)" ok "$dpb" $(ref "$disk_id") -e "$by" -e "$dirs" -e _tmp_cleanup_test_minutes=1 \
    -e awx_workflow_job_id=7 -- "would delete 2 file(s)" "Requested through the site portal by CN=Pat Example" "disk_cleanup_plan" "disk_cleanup_plan_workflow"
check "  ...the old files are still there" "$(there "$work/data/tmp/old2.dat") $(there "$work/data/tmp/app/old1.log")" "there there"
planout="$LAST_OUT"
"$py" - "$work" <<'EOPY' "$planout"
import json, sys
text = sys.argv[2]
i = text.find("RUN:", text.find("CUSTOM STATS"))
stats = json.JSONDecoder().raw_decode(text[i + 4:].lstrip())[0] if i > 0 else {}
json.dump({"disk_cleanup_plan": stats.get("disk_cleanup_plan"), "disk_cleanup_plan_workflow": stats.get("disk_cleanup_plan_workflow")}, open(sys.argv[1] + "/plan.json", "w"))
EOPY
check "  ...the plan names this host and its folder, and the workflow" "$(cat "$work/plan.json")" "{\"disk_cleanup_plan\": {\"lin01.example.mil\": [\"$work/data/tmp\"]}, \"disk_cleanup_plan_workflow\": \"7\"}"
gate=playbooks/workflow_gate.yml
run "the gate (workflow 49): the plan the plan step handed on opens the way to the approval" ok "$gate" -e @"$work/plan.json" -e workflow_gate_var=disk_cleanup_plan -- "on to the approval"
run "the gate (workflow 49): no plan - red, nothing to approve" fail "$gate" -e workflow_gate_var=disk_cleanup_plan -- "nothing to approve"
run "disk plan: a mount no tmp folder is on - stops before an approval is asked" fail "$dpb" $(ref "disk_full:lin01.example.mil:/srv/data") -e "$by" -e "$dirs" -e awx_workflow_job_id=7 \
    -- "none of the tmp folders this clean up knows" "Disk - space request"
run "disk plan: a Windows host is passed over by this Linux job (no connection)" ok "$dpb" -l win01.example.mil -e target=win01.example.mil -e report_job=4711 -e finding_id=disk_full:win01.example.mil:C: \
    -e "$by" -- "skipped by this Linux playbook"
run "disk plan: a service finding of the wrong kind is refused before it is read" fail "$dpb" $(ref service_down:lin01.example.mil:httpd) -e "$by" -- \
    "finding_id: service_down:lin01.example.mil:httpd is not in the expected form"
run "disk plan: a template in a value (as AAP passes launch values: unsafe)" fail "$dpb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e @"$work/unsafe.yml" \
    -e "$by" -- "holds a template"
run "disk plan: a finding planted in a job of another template is refused, nothing read or touched" fail "$dpb" \
    -l lin01.example.mil -e target=lin01.example.mil -e report_job=4713 -e "finding_id=$disk_id" -e "$by" -- \
    "(Some unrelated job template, playbook our_own.yml) is not a report job this fix takes"
run "disk plan: another host's finding" fail "$dpb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=disk_full:win01.example.mil:C: -e "$by" -- \
    "has no finding disk_full:win01.example.mil:C: for lin01.example.mil"
cpb=playbooks/disk_cleanup.yml
run "disk clean up: a plan of another workflow is refused, nothing deleted" fail "$cpb" $(ref "$disk_id") -e "$by" -e "$dirs" -e _tmp_cleanup_test_minutes=1 -e awx_workflow_job_id=8 \
    -e @"$work/plan.json" -- "Refused: disk_cleanup_plan did not come from this workflow"
check "  ...the old files are still there" "$(there "$work/data/tmp/old2.dat")" "there"
run "disk clean up: a plan for another host is refused" fail "$cpb" $(ref "$disk_id") -e "$by" -e "$dirs" -e _tmp_cleanup_test_minutes=1 -e awx_workflow_job_id=7 \
    -e "{\"disk_cleanup_plan\": {\"lin02.example.mil\": [\"$work/data/tmp\"]}, \"disk_cleanup_plan_workflow\": \"7\"}" -- "there is no clean up plan for lin01.example.mil alone"
run "disk clean up: no plan at all is refused" fail "$cpb" $(ref "$disk_id") -e "$by" -e "$dirs" -e awx_workflow_job_id=7 -- "there is no clean up plan for lin01.example.mil alone"
check "  ...the old files are still there" "$(there "$work/data/tmp/old2.dat")" "there"
run "disk clean up: the plan of this workflow - the old files deleted, the mount read again: fixed" ok "$cpb" $(ref "$disk_id") -e "$by" -e "$dirs" -e _tmp_cleanup_test_minutes=1 \
    -e awx_workflow_job_id=7 -e @"$work/plan.json" -- "deleted 2 file(s)" ": Fixed - before:"
check "  ...the old files gone, the new one kept" "$(there "$work/data/tmp/old2.dat") $(there "$work/data/tmp/app/old1.log") $(there "$work/data/tmp/new.log")" "gone gone there"
run "disk clean up: warning level 1% - the mount is still over it: still there" ok "$cpb" $(ref "$disk_id" 4712) -e "$by" -e "$dirs" -e _tmp_cleanup_test_minutes=1 \
    -e awx_workflow_job_id=7 -e @"$work/plan.json" -- ": Still there - before:"

# ---- services ------------------------------------------------------------------------------------------------------------
spb=playbooks/service_restart.yml
sref() { echo "-l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=service_down:lin01.example.mil:$1"; }
run "service look: status and the last log lines, read only (nothing restarted)" ok "$spb" $(sref httpd) -e "$by" -e portal_service_phase=look -- \
    "STATUS of httpd on lin01.example.mil" "made-up service" "made-up log line 3" "!Fixed - before:"
svc="$LAST_OUT"
"$py" - "$work" <<'EOPY' "$svc"
import json, sys
text = sys.argv[2]
i = text.find("RUN:", text.find("CUSTOM STATS"))
stats = json.JSONDecoder().raw_decode(text[i + 4:].lstrip())[0] if i > 0 else {}
json.dump({"portal_service_plan": stats.get("portal_service_plan")}, open(sys.argv[1] + "/svcplan.json", "w"))
EOPY
check "  ...the look step hands on that one service as the plan (the gate's variable)" "$(cat "$work/svcplan.json")" '{"portal_service_plan": {"lin01.example.mil": "httpd"}}'
run "the gate (workflow 50): the look step's plan opens the way to the approval" ok "$gate" -e @"$work/svcplan.json" -e workflow_gate_var=portal_service_plan -- "on to the approval"
if grep -q restart "$work/systemctl.args" 2>/dev/null; then echo "FAIL - the look step restarted something"; fails=$((fails + 1)); else echo "ok   - the look step restarted nothing"; fi
if ! grep -q "^-u httpd -n 30" "$work/journalctl.args"; then echo "FAIL - journalctl was not asked for 30 lines of httpd"; fails=$((fails + 1)); else echo "ok   - 30 log lines of that unit"; fi
run "service restart: that one service, its state after the wait: fixed" ok "$spb" $(sref httpd) -e "$by" -e portal_service_phase=restart -e portal_restart_wait=1 -- \
    ": Fixed - before:" "after: httpd active"
check "  ...only httpd was restarted" "$(grep '^restart' "$work/systemctl.args" | tr '\n' ' ')" "restart -- httpd "
run "service restart: a restart that fails - still there" ok "$spb" $(sref stuck) -e "$by" -e portal_service_phase=restart -e portal_restart_wait=1 -- \
    ": Still there - before:" "Job for stuck.service failed"
run "service look: sshd is on the never-list - refused before anyone approves" fail "$spb" $(sref sshd) -e "$by" -e portal_service_phase=look -- \
    "sshd is not restarted from here" "portal_restart_never"
run "service restart: the never-list also refuses the restart step" fail "$spb" $(sref sshd) -e "$by" -e portal_service_phase=restart -- "sshd is not restarted from here"
run "service restart: a never-list name in another case (the setting is replaced)" fail "$spb" $(sref httpd) -e "$by" -e portal_service_phase=restart \
    -e '{"portal_restart_never": ["HTTPD"]}' -- "httpd is not restarted from here"
run "service restart: a service the check found missing - nothing to restart" fail "$spb" $(sref gone) -e "$by" -e portal_service_phase=restart -- "there is nothing to restart"
run "service: a disk finding of the wrong kind is refused" fail "$spb" $(ref "$disk_id") -e "$by" -e portal_service_phase=look -- "is not in the expected form"
run "service: a Windows service is the Windows template's - passed over on a Linux host, no connection" ok "$spb" -l win01.example.mil -e target=win01.example.mil -e report_job=4711 \
    -e finding_id=service_down:win01.example.mil:Spooler -e "$by" -e portal_service_phase=look -- "skipped by this Linux playbook"
run "service: the Windows template passes over a Linux host" ok "$spb" $(sref httpd) -e "$by" -e portal_service_phase=look -e portal_service_os=windows -- "skipped by this Windows playbook"

# ---- the space request --------------------------------------------------------------------------------------------------
now="$(date +%s)"
cat > "$work/hist/history.json" <<EOJ
{"$mount": [[$((now - 7 * 86400)), $((90 * 1024 ** 3))], [$((now - 3 * 86400)), $((94 * 1024 ** 3))], [$now, $((96 * 1024 ** 3))]]}
EOJ
rpb=playbooks/disk_space_request.yml
run "space request: the text with host, mount, size, used, % and the growth (from the capacity history)" ok "$rpb" $(ref "$disk_id") -e "$by" -e "check_disk_history_file=$work/hist/history.json" -- \
    "Request: more space on lin01.example.mil, $mount" "99% used" "100.0 GiB" "Growth: about" "a week (over 7.0 days of history), full in about" "Nothing was sent" "disk_space_request"
run "space request: no history - said, no growth made up" ok "$rpb" $(ref "$disk_id") -e "$by" -e "check_disk_history_file=$work/hist/none.json" -- \
    "Growth: not known" "!full in about"
run "space request: a Windows drive - the numbers of the finding, no connection" ok "$rpb" -l win01.example.mil -e target=win01.example.mil -e report_job=4711 -e finding_id=disk_full:win01.example.mil:C: \
    -e "$by" -- "Request: more space on win01.example.mil, C:" "96% used" "Growth: not known"
run "space request: a service finding is refused" fail "$rpb" $(sref httpd) -e "$by" -- "is not in the expected form"
if [ -s "$work/sn.jsonl" ]; then echo "FAIL - the space request or a dry run contacted ServiceNow"; fails=$((fails + 1)); else echo "ok   - nothing was sent to ServiceNow so far"; fi

# ---- open ticket -------------------------------------------------------------------------------------------------------------
opb=playbooks/portal_open_ticket.yml
snenv="SN_HOST=http://127.0.0.1:$snport SN_USERNAME=api SN_PASSWORD=goodpw"
RUN_ENV="env $snenv" run "ticket (default dry_run, the credential IS on the template): a draft, nothing sent" ok "$opb" $(ref "$disk_id") -e "$by" -- \
    "DRY RUN: nothing was sent to ServiceNow." "short_description: [site-health] lin01.example.mil: $mount is 99% full" "Asked for in the site portal by CN=Pat Example" \
    "ticket_draft" "Dry run: nothing was sent" ": Not checked - before:"
if [ -s "$work/sn.jsonl" ]; then echo "FAIL - dry_run contacted ServiceNow"; fails=$((fails + 1)); else echo "ok   - dry_run: the fake ServiceNow saw 0 requests"; fi
run "ticket (dry_run, no credential at all): the same draft" ok "$opb" $(ref "$disk_id") -e "$by" -- "DRY RUN: nothing was sent to ServiceNow." "ticket_draft"
run "ticket: a finding of any kind (an event-log one)" ok "$opb" $(ref eventlog:lin01.example.mil:x) -e "$by" -- "Check: eventlog" "Look further with: journalctl -p err"
run "ticket: the assignment group by finding kind" ok "$opb" $(ref "$disk_id") -e "$by" -e '{"portal_ticket_groups": {"disk_full": "Linux Operations"}}' -- "assignment_group: Linux Operations"
run "ticket: off refuses" fail "$opb" $(ref "$disk_id") -e "$by" -e portal_ticket_mode=off -- "Tickets from the portal are off"
run "ticket: a wrong mode refuses" fail "$opb" $(ref "$disk_id") -e "$by" -e portal_ticket_mode=maybe -- "it must be dry_run, off or live"
run "ticket: live without the ServiceNow credential refuses, nothing sent" fail "$opb" $(ref "$disk_id") -e "$by" -e portal_ticket_mode=live -- "has no ServiceNow API credential"
if [ -s "$work/sn.jsonl" ]; then echo "FAIL - a refused live run contacted ServiceNow"; fails=$((fails + 1)); else echo "ok   - still 0 requests to ServiceNow"; fi
RUN_ENV="env $snenv" run "ticket live: one incident opened" ok "$opb" $(ref "$disk_id") -e "$by" -e portal_ticket_mode=live -e '{"portal_ticket_groups": {"disk_full": "Linux Operations"}}' -- \
    "Ticket INC" ": Not checked - before:"
posts="$(grep -c '"POST"' "$work/sn.jsonl" 2>/dev/null || echo 0)"
check "  ...exactly one incident was created" "$posts" "1"
RUN_ENV="env $snenv" run "ticket live: the same finding again - the open ticket is returned, no second one" ok "$opb" $(ref "$disk_id") -e "$by" -e portal_ticket_mode=live -e '{"portal_ticket_groups": {"disk_full": "Linux Operations"}}' -- \
    "Already open: INC" "no second one was opened"
check "  ...still one incident" "$(grep -c '"POST"' "$work/sn.jsonl")" "1"
"$py" - "$work/sn.jsonl" "$mount" <<'EOPY' && echo "ok   - the draft is what live sent (short description, description, urgency, impact, correlation id, group)" || { echo "FAIL - draft and live differ"; fails=$((fails + 1)); }
import json, sys
posts = [json.loads(l) for l in open(sys.argv[1]) if '"POST"' in l]
body = posts[0].get("body") or posts[0].get("json") or {}
if isinstance(body, str):
    body = json.loads(body)
assert body["short_description"].startswith("[site-health] lin01.example.mil: " + sys.argv[2]), body
assert body["urgency"] == "1" and body["impact"] == "2", body
assert body["correlation_id"].startswith("site-health:") and body["assignment_group"] == "Linux Operations", body
assert "Asked for in the site portal by CN=Pat Example" in body["description"], body
EOPY

echo
if [ "$fails" -eq 0 ]; then echo "ok   - all portal fix scenarios passed"; exit 0; fi
echo "FAIL - $fails portal fix scenario(s) failed"; exit 1
