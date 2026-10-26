#!/usr/bin/env bash
# The report website on a Windows file share over SMB (report_web_dest \\server\share\folder; the site_report_web
# module's SmbTarget, smbprotocol - no mount) against a real Samba server in a container: a dry run that logs in and
# finds the folder (writes nothing), the pages written (page, latest, JSON, index) and read back on the server, a second
# template added to the index, the address with report_web_url, the share given as smb://, a wrong password, no
# credential, a folder that is not there, a share that is not there, and the report part of a play on the hosts (a
# Limit with no localhost: the role delegated to the AAP side, as log_analysis.yml and the others since 0.32.0).
#   bash tests/email/run_smb_test.sh        (ANSIBLE_PLAYBOOK=... for another ansible-core; CONTAINER_ENGINE=...)
# Needs: podman or docker (an Alpine image with Samba, installed at start), smbprotocol for the Python Ansible runs on.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(head -1 "$(command -v "$ap")" | sed -n 's/^#! *//p' | awk '{print $1}')"
[ -x "$py" ] || py="$(command -v python3)"
engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
port="${SMB_TEST_PORT:-14451}"
name="sa-smb-test-$$"
work="$(mktemp -d)"
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
"$py" -c 'import smbclient' 2>/dev/null || { echo "ABORT - $py has no smbprotocol (pip install smbprotocol)"; exit 1; }
[ -n "$engine" ] || { echo "ABORT - no podman or docker for the Samba server"; exit 1; }

cat > "$work/start.sh" <<'EOF'
#!/bin/sh
# a share "its" with aap/reports (written by svc_reports), SMB 2 and 3
set -e
apk add --no-cache samba >/dev/null
mkdir -p /srv/its/aap/reports /srv/its/other
adduser -D -H svc_reports
chown -R svc_reports /srv/its/aap
cat > /etc/samba/smb.conf <<CONF
[global]
  server role = standalone server
  server min protocol = SMB2_02
  map to guest = never
[its]
  path = /srv/its
  read only = no
  valid users = svc_reports
CONF
printf 'Pa55-word\nPa55-word\n' | smbpasswd -s -a svc_reports >/dev/null
touch /srv/ready
exec smbd --foreground --no-process-group --debug-stdout
EOF
chmod 755 "$work/start.sh"
"$engine" run -d --name "$name" -p "127.0.0.1:$port:445" -v "$work/start.sh:/start.sh:ro,Z" docker.io/library/alpine:3.20 /start.sh >/dev/null \
    || { echo "ABORT - could not start the Samba container"; exit 1; }
for _ in $(seq 1 120); do "$engine" exec "$name" test -f /srv/ready 2>/dev/null && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 1; done
(exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || { echo "ABORT - Samba did not start"; "$engine" logs "$name" | tail -5; exit 1; }
sleep 1

printf 'all:\n  hosts:\n    localhost: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" > "$work/inv.yml"
printf 'all:\n  hosts:\n    lin01.example.mil: {ansible_connection: local, ansible_python_interpreter: %s}\n    lin02.example.mil: {ansible_connection: local, ansible_python_interpreter: %s}\n' "$py" "$py" > "$work/inv-hosts.yml"
share='\\\\127.0.0.1\\its\\aap\\reports'
smb="{\"report_web_dest\": \"$share\", \"report_web_smb_port\": $port, \"report_web_smb_auth\": \"ntlm\"}"

# run NAME EXPECT(ok|fail) [ansible-playbook args...] -- TEXT...: tests/email/send_report.yml (or PB=, INV=), the texts in the output
run() {
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && REPORT_WEB_USERNAME="${SMB_USER-svc_reports}" REPORT_WEB_PASSWORD="${SMB_PW-Pa55-word}" ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_SHOW_CUSTOM_STATS=1 \
        "$ap" -i "${INV:-$work/inv.yml}" "${PB:-$here/send_report.yml}" -e '{"awx_job_template_name": "Linux - health check", "awx_job_id": 501}' \
        "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-400; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR' "$work/out.txt" | head -4 | cut -c1-400; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $tname"
}
on_server() { "$engine" exec "$name" sh -c "$1" 2>/dev/null; }
check() {  # check NAME SHELL-TEST (in the container)
    if on_server "$2"; then echo "ok   - $1"; else echo "FAIL - $1"; on_server 'find /srv/its -type f' | head -20; fails=$((fails + 1)); fi
}

run "a dry run (Check): logs in, finds the folder, writes nothing" ok --check -e "$smb" \
    -- 'Dry run (Check): logged in to 127.0.0.1, the folder \\\\127.0.0.1\\its\\aap\\reports is there'
check "  ...nothing written" '[ -z "$(ls -A /srv/its/aap/reports)" ]'
run "the pages written on the share (no address: the share path printed)" ok -e "$smb" \
    -- 'Published: \\\\127.0.0.1\\its\\aap\\reports\\linux-health-check\\'
check "  ...the page, latest, their JSON and the index on the server" \
    'cd /srv/its/aap/reports && ls linux-health-check/*_job-501.html && grep -q "Test formatted report" linux-health-check/latest.html && [ -f linux-health-check/latest.json ] && grep -q "linux-health-check/latest.html" index.json && [ -f index.html ]'
check "  ...no temporary file left" '[ -z "$(find /srv/its/aap/reports -name "*.tmp")" ]'
run "a second template: added to the index; the address with report_web_url" ok -e "$smb" \
    -e '{"awx_job_template_name": "VM - health report", "awx_job_id": 502, "report_web_url": "https://reports.example.mil/aap"}' \
    -- 'Report: https://reports.example.mil/aap/vm-health-report/'
check "  ...both templates in the index" 'grep -q vm-health-report /srv/its/aap/reports/index.json && grep -q linux-health-check /srv/its/aap/reports/index.json'
run "the share given as smb://, the same folder" ok -e "{\"report_web_dest\": \"smb://127.0.0.1/its/aap/reports\", \"report_web_smb_port\": $port, \"report_web_smb_auth\": \"ntlm\"}" \
    -e '{"awx_job_id": 503}' -- 'Published: \\\\127.0.0.1\\its\\aap\\reports\\linux-health-check\\'
check "  ...its page there" 'ls /srv/its/aap/reports/linux-health-check/*_job-503.html'
SMB_PW=wrong run "a wrong password: said in words" fail -e "$smb" -- "the user name or password is wrong"
SMB_USER= SMB_PW= run "no credential: says which one to attach" fail -e "$smb" -- 'attach the \"Report website\" credential'
run "a folder that is not there: never made" fail -e "{\"report_web_dest\": \"\\\\\\\\127.0.0.1\\\\its\\\\nope\", \"report_web_smb_port\": $port, \"report_web_smb_auth\": \"ntlm\"}" \
    -- 'is not there - make it once by hand'
check "  ...and it was not made" '[ ! -e /srv/its/nope ]'
run "a share that is not there: said" fail -e "{\"report_web_dest\": \"\\\\\\\\127.0.0.1\\\\nosuch\\\\x\", \"report_web_smb_port\": $port, \"report_web_smb_auth\": \"ntlm\"}" \
    -- "no such share on that server"
INV="$work/inv-hosts.yml" PB="$here/send_report_delegated.yml" run "a play on the hosts (a Limit, no localhost): the report written once, from the AAP side" ok \
    -l lin01.example.mil,lin02.example.mil -e "$smb" -e '{"awx_job_template_name": "Log analysis", "awx_job_id": 504}' \
    -- 'Published: \\\\127.0.0.1\\its\\aap\\reports\\log-analysis\\'
check "  ...one page" '[ "$(ls /srv/its/aap/reports/log-analysis/*_job-504.html | wc -l)" = 1 ]'

if [ "$fails" -eq 0 ]; then echo "ok   - all SMB report website checks passed"; else echo "$fails FAILED"; exit 1; fi
