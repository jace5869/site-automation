#!/usr/bin/env bash
# The log analysis (playbooks/log_analysis.yml) against this machine as a Linux host, with made-up logs:
# a stand-in journalctl (JSON lines, and the options it was called with), a stand-in podman, text logs in a
# temporary folder, a fake ACT (tests/containers/fake_act.py), a fake mail relay and the report website as
# a folder. Checks: a profile switched on adds its units, log_analysis_files_extra adds a file,
# log_analysis_ignore_extra drops a line, the since / until window reaches journalctl and podman and leaves
# out a line of the text log outside it, the question reaches ACT's prompt, the patterns are grouped and
# ranked, ACT's answer and the patterns table are emailed and published, a host that cannot be reached is
# listed (the job stays green), log_analysis_act: false needs no key, no key with ACT fails after the report, a
# Limit of hosts only (an inventory without localhost, as AAP's) is read, analysed and reported - and one with
# localhost in it still works.
#   bash tests/logs/run_log_test.sh                    (ANSIBLE_PLAYBOOK=... for another ansible-core)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${LOG_TEST_PYTHON:-$(command -v python3)}"
port="${LOG_TEST_PORT:-18560}"
work="$(mktemp -d)"
smtp_pid=""
http_pid=""
cleanup() { [ -n "$smtp_pid" ] && kill "$smtp_pid" 2>/dev/null; [ -n "$http_pid" ] && kill "$http_pid" 2>/dev/null; rm -rf "$work"; }
trap cleanup EXIT
fails=0
mkdir -p "$work/bin" "$work/logs" "$work/act" "$work/mail" "$work/www" "$work/site"

# ---- stand-ins ---------------------------------------------------------------------------------------
now_us=$(( $(date +%s) * 1000000 ))
cat > "$work/bin/journalctl" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$work/journalctl.args"
for a in "\$@"; do [ "\$a" = "-n" ] && exit 0; done           # the --output-fields probe
if printf '%s\n' "\$@" | grep -qx -- '-u'; then
  echo '{"PRIORITY":"4","MESSAGE":"Backend is offline","SYSLOG_IDENTIFIER":"sssd_be","__REALTIME_TIMESTAMP":"$((now_us - 120000000))","__CURSOR":"u1"}'
  echo '{"PRIORITY":"3","MESSAGE":"error: kex_exchange_identification: Connection reset by 192.0.2.10 port 4711","SYSLOG_IDENTIFIER":"sshd","__REALTIME_TIMESTAMP":"$((now_us - 60000000))","__CURSOR":"j1"}'
  exit 0
fi
echo '{"PRIORITY":"3","MESSAGE":"error: kex_exchange_identification: Connection reset by 192.0.2.10 port 4711","SYSLOG_IDENTIFIER":"sshd","__REALTIME_TIMESTAMP":"$((now_us - 60000000))","__CURSOR":"j1"}'
echo '{"PRIORITY":"3","MESSAGE":"error: kex_exchange_identification: Connection reset by 198.51.100.7 port 50022","SYSLOG_IDENTIFIER":"sshd","__REALTIME_TIMESTAMP":"$((now_us - 30000000))","__CURSOR":"j2"}'
echo '{"PRIORITY":"2","MESSAGE":"kernel: Out of memory: Killed process 4242 (java)","SYSLOG_IDENTIFIER":"kernel","__REALTIME_TIMESTAMP":"$((now_us - 90000000))","__CURSOR":"j3"}'
echo '{"PRIORITY":"3","MESSAGE":"NOISE we do not want","SYSLOG_IDENTIFIER":"noisy","__REALTIME_TIMESTAMP":"$((now_us - 10000000))","__CURSOR":"j4"}'
EOF
cat > "$work/bin/podman" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$work/podman.args"
[ "\$1" = logs ] || exit 1
case "\${@: -1}" in
  web) echo "$(date -u +%Y-%m-%dT%H:%M:%S).000000000+00:00 ERROR could not connect to db01:5432"
       echo "$(date -u +%Y-%m-%dT%H:%M:%S).000000000+00:00 INFO request served" ;;
  *) echo "Error: no container with name or ID \"\${@: -1}\" found: no such container" >&2; exit 125 ;;
esac
EOF
chmod +x "$work/bin/journalctl" "$work/bin/podman"
old="$(date -u -d '-3 days' +%Y-%m-%dT%H:%M:%SZ)"
recent="$(date -u -d '-10 minutes' +%Y-%m-%dT%H:%M:%SZ)"
printf '%s app: FATAL could not open the database\n%s app: ERROR request failed with status 500\n%s app: INFO all good\n' \
    "$old" "$recent" "$recent" > "$work/logs/app.log"
printf '%s web: warning: certificate expires in 5 days\n' "$recent" > "$work/logs/extra.log"
"$py" - "$work/act/act.json" <<'EOPY'
import json, sys
items = [{"id": "P1", "cause": "The JVM ran out of memory.", "evidence": "Out of memory: Killed process", "fix": "Raise the heap.", "confidence": 90},
         {"id": "P2", "cause": "A scanner.", "evidence": "Connection reset", "fix": "Ignore it.", "confidence": "60%"}]
json.dump({"summary": "BEGIN_ACT_ANALYSIS\n" + json.dumps({"overall": "Give the JVM more memory first.", "items": items}) + "\nEND_ACT_ANALYSIS"},
          open(sys.argv[1], "w"))
EOPY
"$py" "$repo/tests/email/fake_smtp.py" "$port" "$work/mail" 2>/dev/null &
smtp_pid=$!
"$py" -m http.server "$((port + 1))" --bind 127.0.0.1 --directory "$work/www" >/dev/null 2>&1 &
http_pid=$!
for _ in $(seq 1 50); do (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && (exec 3<>"/dev/tcp/127.0.0.1/$((port + 1))") 2>/dev/null && break; sleep 0.1; done

cat > "$work/inv.yml" <<EOI
all:
  hosts:
    localhost: {ansible_connection: local, ansible_python_interpreter: $py}
    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh}
  children:
    web:
      hosts:
        localhost:
        gone01.example.mil:
EOI
# as an AAP inventory: no localhost in it (the report part runs once, from the first host, on the AAP side)
cat > "$work/inv-nolocal.yml" <<EOI
all:
  hosts:
    lin01.example.mil: {ansible_connection: local, ansible_python_interpreter: $py}
    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh}
EOI
mail="{\"report_email_to\": \"ops@example.mil\", \"report_email_from\": \"aap@example.mil\", \"report_email_smtp_host\": \"127.0.0.1\",
       \"report_email_smtp_port\": $port, \"report_email_security\": \"none\", \"report_web_dest\": \"$work/site\"}"
actvars="{\"act_triage_script_src\": \"$repo/tests/containers/fake_act.py\", \"act_triage_poll\": 1,
          \"site_act_url\": \"http://127.0.0.1:$((port + 1))/v1/chat/completions\"}"
base="{\"log_analysis_become\": false, \"log_analysis_profiles\": [\"sssd\", \"mine\"],
       \"log_analysis_profiles_extra\": {\"mine\": {\"files\": [\"$work/logs/app.log\"], \"containers\": [\"web\", \"gone\"]}},
       \"log_analysis_files_extra\": [\"$work/logs/extra.log\", \"$work/logs/none*.log\"],
       \"log_analysis_ignore_extra\": [\"NOISE we do not want\"]}"

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_ACT_OUT="$work/act" FAKE_ACT_CONFIG="$work/act/act.json" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_SHOW_CUSTOM_STATS=True ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_TIMEOUT=5 \
        "$ap" -i "${INV:-$work/inv.yml}" playbooks/log_analysis.yml -e target=web -e "$base" -e '{"awx_job_id": 4711}' "${args[@]}") \
        > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $tname"
}

export GENAI_KEY=test-key
run "a group with a host not reached: patterns grouped and ranked, ACT's answer, emailed and published, green" ok \
    -e "$mail" -e "$actvars" -e 'log_analysis_question="users report slow logins since 08:00"' \
    -- "Log analysis: likely causes and fixes for" "1 host(s) not read" "gone01.example.mil" "90% high" "60% medium" \
       "Give the JVM more memory first." "Patterns (" "Profile mine: localhost." "Profile sssd: localhost." \
       "could not connect to db01:5432" "request failed with status 500" "certificate expires in 5 days" \
       "no such container" "emailed" '"log_analysis"'
grep -q -- "-p warning .*-u sssd" "$work/journalctl.args" && grep -Eq -- "-p err --since -1440min -o json( --output-fields=[A-Z_,]+)?$" "$work/journalctl.args" && echo "ok   -   ...the sssd profile's unit read at its own priority (journalctl -u sssd -p warning)" \
    || { echo "FAIL -   ...no journalctl -u sssd"; cat "$work/journalctl.args"; fails=$((fails + 1)); }
grep -q "users report slow logins since 08:00" "$work/act/fake-act-task.txt" && grep -q "THE OPERATIONS TEAM ASKS" "$work/act/fake-act-stdin.txt" \
    && echo "ok   -   ...the question reached ACT's prompt and its evidence" || { echo "FAIL -   ...question"; fails=$((fails + 1)); }
grep -q "NOISE we do not want" "$work/act/fake-act-stdin.txt" && { echo "FAIL -   ...an ignored line reached ACT"; fails=$((fails + 1)); } \
    || echo "ok   -   ...log_analysis_ignore_extra kept a line away from ACT and the report"
grep -q "could not open the database" "$work/act/fake-act-stdin.txt" && { echo "FAIL -   ...a line outside the window reached ACT"; fails=$((fails + 1)); } \
    || echo "ok   -   ...a text-log line older than the window left out"
grep -q "Connection reset by .* port" "$work/act/fake-act-stdin.txt" && [ "$(grep -c '^P[0-9]* \[ERROR\] journal sshd' "$work/act/fake-act-stdin.txt")" = 1 ] \
    && grep -q "^P1 \[CRITICAL\] journal kernel" "$work/act/fake-act-stdin.txt" \
    && echo "ok   -   ...two sshd lines one pattern (the units' read of the same line counted once), the critical one first" \
    || { echo "FAIL -   ...grouping"; head -30 "$work/act/fake-act-stdin.txt"; fails=$((fails + 1)); }
eml="$(ls "$work/mail"/*.eml 2>/dev/null | sort -V | tail -1)"
if [ -n "$eml" ] && grep -q "Likely causes and fixes" "$eml" && grep -q "First seen" "$eml" && grep -q "What was read" "$eml" \
   && ls "$work/site"/*/latest.html >/dev/null 2>&1; then
    echo "ok   -   ...the email has the ACT table, the patterns table and the settings; the page is on the website"
else
    echo "FAIL -   ...email / website"; ls -R "$work/site" | head; fails=$((fails + 1))
fi

: > "$work/journalctl.args"; : > "$work/podman.args"
since="$(date -u -d '-2 hours' '+%Y-%m-%d %H:%M')"; until="$(date -u -d '-1 hour' '+%Y-%m-%d %H:%M')"
run "a fixed window (since / until) reaches journalctl and podman; no ACT: the patterns only, no key needed" ok \
    -e log_analysis_act=false -e "log_analysis_since='$since'" -e "log_analysis_until='$until'" \
    -- "ACT: not asked (log_analysis_act: false)." "UTC" "No email: report_email_to is not set"
grep -Eq -- "--since -1[0-9][0-9]min --until -(59|60|61)min" "$work/journalctl.args" && grep -Eq -- "--since 1[0-9][0-9]m --until (59|60|61)m" "$work/podman.args" \
    && echo "ok   -   ...journalctl --since/--until and podman logs --since/--until in minutes" \
    || { echo "FAIL -   ...window"; cat "$work/journalctl.args" "$work/podman.args"; fails=$((fails + 1)); }

run "an unknown profile is named, with the known ones" fail -e '{"log_analysis_profiles": ["nginxx"]}' \
    -- "Unknown profile(s) in log_analysis_profiles: nginxx" "mid_server"
run "a wrong window is refused" fail -e "log_analysis_since=yesterday" -- "must look like 2026-10-23 08:00"
run "a dry run (Check): ACT not called, the report still made, green" ok --check -e "$actvars" -- "A dry run (Check): ACT was not called"
INV="$work/inv-nolocal.yml" run "a Limit of hosts only, no localhost anywhere: read, analysed, reported" ok \
    -l "gone01.example.mil,lin01.example.mil" -e target=all -e log_analysis_act=false -e "$mail" \
    -- "ACT: not asked (log_analysis_act: false)." "1 host(s) not read" "Log analysis: " "emailed" \
    && { grep -q "skipping: no hosts matched" "$work/out.txt" && { echo "FAIL -   ...a play was skipped"; fails=$((fails + 1)); } \
         || echo "ok   -   ...no play skipped"; }
run "a Limit with localhost in it: read, analysed, reported" ok -l "gone01.example.mil,localhost" -e log_analysis_act=false \
    -- "ACT: not asked (log_analysis_act: false)." "1 host(s) not read"
unset GENAI_KEY
run "ACT without an API key: says which credential, still reports, fails" fail -e "$actvars" \
    -- "This job has no API key for provider genai" "ACT did not run" "Patterns ("

[ $fails -eq 0 ] && echo "ALL PASSED" || echo "$fails FAILED"
exit $fails
