#!/usr/bin/env bash
# The site portal's AAP side (1.67.0): "ACT - triage finding" (playbooks/act_triage_finding.yml, workflow 44) against
# this machine as a Linux host, with a fake AAP API (the job that reported the finding, its site_findings artifact), a
# fake ACT (tests/containers/fake_act.py), stand-in systemctl and ausearch, and the report website as a folder.
# Checks: the finding is read from the reporting job, not sent; the finding's look command and the fapolicyd reads run
# on the host and reach ACT; ACT's answer is the artifact act_triage_finding and the report (who asked in both); a
# finding of the other kind (event log, a Windows host) is passed over without connecting; and what the playbook
# refuses: a template in a value (as AAP passes launch values: unsafe), a report_job not a number, a target that is
# not the Limit, a finding the job does not have, a job AAP does not know, no AAP credential. The STIG recheck and the
# checklist import check the portal's values too (only when the portal launched them).
#   bash tests/portal/run_portal_test.sh                    (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${PORTAL_TEST_PYTHON:-$(command -v python3)}"
port="${PORTAL_TEST_PORT:-18690}"
work="$(mktemp -d)"
aap_pid=""
cleanup() { [ -n "$aap_pid" ] && kill "$aap_pid" 2>/dev/null; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
mkdir -p "$work/bin" "$work/act" "$work/site"

# ---- stand-ins -----------------------------------------------------------------------------------------
cat > "$work/bin/systemctl" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$work/systemctl.args"
echo "fapolicyd.service - File Access Policy Daemon"
echo "   Active: active (running) since today"
EOS
cat > "$work/bin/ausearch" <<EOS
#!/usr/bin/env bash
echo "\$*" >> "$work/ausearch.args"
echo "type=FANOTIFY msg=audit(today) : resp=deny exe=/opt/tool/bin/tool-made-up"
EOS
chmod +x "$work/bin/systemctl" "$work/bin/ausearch"
cat > "$work/aap.py" <<'EOPY'
import http.server, json, sys
port = int(sys.argv[1])
JOB = {"id": 4711, "name": "Linux health check", "status": "failed", "playbook": "playbooks/health_check.yml",
       "finished": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
       "artifacts": {"site_findings": {
           "lin01.example.mil": [
               {"id": "fapolicyd:lin01.example.mil:denials", "kind": "fapolicyd", "host": "lin01.example.mil", "severity": "warning",
                "title": "fapolicyd denied 3 executions today", "data": {"look": "echo look-command-ran"}},
               {"id": "disk_full:lin01.example.mil:/var", "kind": "disk_full", "host": "lin01.example.mil", "severity": "critical",
                "title": "/var is 93% full", "data": {"mount": "/var", "pct": 93}}],
           "win01.example.mil": [
               {"id": "eventlog:win01.example.mil:disk-errors", "kind": "eventlog", "host": "win01.example.mil", "severity": "critical",
                "title": "2 disk errors in the last 24 hours", "data": {"look": "Get-WinEvent -LogName System -MaxEvents 5"}}]}}}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def answer(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        with open(sys.argv[2], "a") as fh:
            fh.write(self.path + " " + self.headers.get("Authorization", "") + "\n")
        if self.path == "/api/controller/v2/me/":
            return self.answer(200, {"results": [{"username": "portal-reader"}]})
        if self.path == "/api/controller/v2/jobs/4711/":
            return self.answer(200, JOB)
        if self.path.startswith("/api/controller/v2/jobs/"):
            return self.answer(404, {"detail": "Not found."})
        return self.answer(404, {"detail": "no such page"})        # the model's URL: any answer means reachable
http.server.ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
EOPY
"$py" "$work/aap.py" "$port" "$work/aap.log" &
aap_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 0.1; done
cat > "$work/act/act.json" <<'EOJ'
{"summary": "Looked at it.\nBEGIN_ACT_ANALYSIS\n{\"overall\": \"Add a trust entry for the tool after review.\", \"items\": [{\"id\": \"F1\", \"cause\": \"The tool under /opt is not in fapolicyd's trust database.\", \"evidence\": \"resp=deny exe=/opt/tool/bin/tool-made-up\", \"fix\": \"Review the tool, then add it with fapolicyd-cli --file add.\", \"confidence\": 85}]}\nEND_ACT_ANALYSIS"}
EOJ
cp "$work/act/act.json" "$work/act/act.keep"
cat > "$work/inv.yml" <<EOI
all:
  hosts:
    lin01.example.mil: {ansible_connection: local, ansible_python_interpreter: $py, ansible_become: false}
    lin02.example.mil: {ansible_connection: local, ansible_python_interpreter: $py, ansible_become: false}
    win01.example.mil: {ansible_connection: winrm, ansible_host: 192.0.2.250}
EOI
cat > "$work/unsafe.yml" <<'EOU'
finding_id: !unsafe "fapolicyd:{{ 7 * 6 }}"
EOU
actvars="{\"act_triage_script_src\": \"$repo/tests/containers/fake_act.py\", \"act_triage_poll\": 1,
          \"site_act_url\": \"http://127.0.0.1:$port/v1/chat/completions\", \"report_web_dest\": \"$work/site\"}"

# run NAME ok|fail [ansible args...] -- expected text...
run() {
  local name="$1" want="$2"; shift 2
  local args=() expect=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do args+=("$1"); shift; done
  [ $# -gt 0 ] && shift
  expect=("$@")
  cp "$work/act/act.keep" "$work/act/act.json"
  rm -f "$work/act/fake-act-calls.log" "$work/act/fake-act-stdin.txt"
  local out rc
  out="$(cd "$repo" && PATH="$work/bin:$PATH" FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" \
         CONTROLLER_HOST="${AAP_HOST-http://127.0.0.1:$port}" CONTROLLER_OAUTH_TOKEN="made-up-token" GENAI_KEY=test-key \
         ANSIBLE_SHOW_CUSTOM_STATS=1 "$ap" -i "$work/inv.yml" "${args[@]}" 2>&1)"
  rc=$?
  local ok=1
  if [ "$want" = ok ] && [ $rc -ne 0 ]; then ok=0; fi
  if [ "$want" = fail ] && [ $rc -eq 0 ]; then ok=0; fi
  for e in "${expect[@]}"; do
    if ! printf '%s' "$out" | grep -qF -- "$e"; then ok=0; echo "   missing: $e"; fi
  done
  if [ $ok -eq 1 ]; then echo "ok   - $name"; else echo "FAIL - $name (rc $rc)"; printf '%s\n' "$out" | tail -40; fails=$((fails + 1)); fi
  LAST_OUT="$out"
}

pb=playbooks/act_triage_finding.yml
by='{"portal_requested_by": "CN=Pat Example"}'
run "triage: the finding read from job 4711, the look command and fapolicyd's reads, ACT's answer, who asked" ok \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=fapolicyd:lin01.example.mil:denials \
    -e "$by" -e "$actvars" -- \
    "Requested through the site portal by CN=Pat Example" "ACT - triage finding: fapolicyd:lin01.example.mil:denials on lin01.example.mil" \
    "The tool under /opt is not in fapolicyd" "act_triage_finding" "\"confidence\": 85" "portal_request"
for t in "FINDING (from the health check job 4711" "summary: fapolicyd denied 3 executions today" "look-command-ran" \
         "resp=deny exe=/opt/tool/bin/tool-made-up" "fapolicyd.service - File Access Policy Daemon"; do
  if ! grep -qF -- "$t" "$work/act/fake-act-stdin.txt" 2>/dev/null; then echo "FAIL - ACT's evidence lacks: $t"; fails=$((fails + 1)); fi
done
if grep -q "made-up-token" "$work/act/fake-act-stdin.txt" 2>/dev/null; then echo "FAIL - the AAP token reached ACT"; fails=$((fails + 1)); fi
if ! grep -q "^/api/controller/v2/jobs/4711/ Bearer made-up-token" "$work/aap.log"; then echo "FAIL - job 4711 not read with the credential"; fails=$((fails + 1)); fi
page="$(grep -rl "ACT - triage finding: fapolicyd:lin01.example.mil:denials" "$work/site" 2>/dev/null | head -1)"
if [ -z "$page" ] || ! grep -q "Requested through the site portal by CN=Pat Example" "$page"; then
  echo "FAIL - the report website has no triage page with who asked"; fails=$((fails + 1))
else echo "ok   - the report website has the page, with who asked"; fi

run "an event-log finding is the Windows template's: passed over, nothing asked, no connection" ok \
    "$pb" -l win01.example.mil -e target=win01.example.mil -e report_job=4711 -e finding_id=eventlog:win01.example.mil:disk-errors \
    -e "$by" -e "$actvars" -- "skipped by this Linux playbook"
if [ -e "$work/act/fake-act-calls.log" ]; then echo "FAIL - ACT was asked for the other template's finding"; fails=$((fails + 1)); fi
run "the Windows template passes over a fapolicyd finding (no AAP read, no ACT)" ok \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=fapolicyd:lin01.example.mil:denials \
    -e portal_triage_os=windows -e "$by" -e "$actvars" -- "skipped by this Windows playbook"

run "a template in a value is refused (as AAP passes launch values: unsafe)" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e @"$work/unsafe.yml" -e "$by" -e "$actvars" -- \
    "finding_id: holds a template (braces that start one) - not allowed"
run "a report_job that is not a number is refused" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=47x -e finding_id=fapolicyd:lin01.example.mil:denials -e "$by" -e "$actvars" -- \
    "report_job: 47x is not in the expected form"
run "a target that is not the Limit is refused" fail \
    "$pb" -l "lin01.example.mil,lin02.example.mil" -e target=lin01.example.mil -e report_job=4711 -e finding_id=fapolicyd:lin01.example.mil:denials \
    -e "$by" -e "$actvars" -- "target: lin01.example.mil is not this job's Limit"
run "a finding the job does not have" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=fapolicyd:lin01.example.mil:gone -e "$by" \
    -e "$actvars" -- "has no finding fapolicyd:lin01.example.mil:gone for lin01.example.mil" "nothing was changed"
run "another host's finding (part C: finding_ref - the id names win01, the job runs on lin01)" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=fapolicyd:win01.example.mil:denials \
    -e "$by" -e "$actvars" -- "has no finding fapolicyd:win01.example.mil:denials for lin01.example.mil"
run "a finding of a kind this workflow does not look at (a disk one) is refused before it is read" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=disk_full:lin01.example.mil:/var \
    -e "$by" -e "$actvars" -- "finding_id: disk_full:lin01.example.mil:/var is not in the expected form"
run "a job AAP does not know" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=999 -e finding_id=fapolicyd:lin01.example.mil:denials -e "$by" -e "$actvars" -- \
    "AAP job 999 cannot be read (HTTP 404)"
if [ -e "$work/act/fake-act-calls.log" ]; then echo "FAIL - ACT was asked although the finding could not be read"; fails=$((fails + 1)); fi
AAP_HOST="" run "no AAP credential" fail \
    "$pb" -l lin01.example.mil -e target=lin01.example.mil -e report_job=4711 -e finding_id=fapolicyd:lin01.example.mil:denials -e "$by" \
    -e "$actvars" -- "attach a credential of type"

run "the STIG recheck checks the portal's host again (not the Limit: refused before anything runs)" fail \
    playbooks/stig_recheck.yml -l "lin01.example.mil,lin02.example.mil" -e target=lin01.example.mil -e "$by" -- \
    "target: lin01.example.mil is not this job's Limit"
run "the checklist import checks the portal's mode again" fail \
    playbooks/stigman_ckl_import.yml -e stigman_ckl_import_mode=everything -e "$by" -- \
    "stigman_ckl_import_mode: everything is not one of import, import and scorecard, scorecard"

echo
if [ "$fails" -eq 0 ]; then echo "ok   - all site portal scenarios passed"; exit 0; fi
echo "FAIL - $fails site portal scenario(s) failed"; exit 1
