#!/usr/bin/env bash
# Evaluate-STIG's answer files from a Windows file share (check_stig_evaluate_answers: \\server\share\folder, read over
# SMB from the AAP side with smbprotocol - no mount; the "Windows file share" credential) against a real Samba server in
# a container, with the STIG drift report and a fake Evaluate-STIG_Bash.sh on this box: the .xml files of the folder
# (not the others, not its sub-folder) copied to a folder of this run, Evaluate-STIG given it (--AFPath), the folder
# removed after; a dry run (Check) gives them too and keeps nothing; the share left as it was (only read); a wrong
# password, no credential, a folder that is not there: Evaluate-STIG not run, the host not scanned, said why.
#   bash tests/stig/run_stig_answers_smb_test.sh   (ANSIBLE_PLAYBOOK=... for another ansible-core; CONTAINER_ENGINE=...)
# Needs: podman or docker (an Alpine image with Samba, installed at start), smbprotocol for the Python Ansible runs on.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(head -1 "$(command -v "$ap")" | sed -n 's/^#! *//p' | awk '{print $1}')"
[ -x "$py" ] || py="$(command -v python3)"
engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
port="${SMB_TEST_PORT:-14461}"
name="sa-stig-smb-test-$$"
work="$(mktemp -d)"
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
"$py" -c 'import smbclient' 2>/dev/null || { echo "ABORT - $py has no smbprotocol (pip install smbprotocol)"; exit 1; }
[ -n "$engine" ] || { echo "ABORT - no podman or docker for the Samba server"; exit 1; }

cat > "$work/start.sh" <<'EOF'
#!/bin/sh
# a share "stig" with AnswerFiles (read only for svc_stig): two answer files, a note, an old one in a sub-folder
set -e
apk add --no-cache samba >/dev/null
mkdir -p /srv/stig/AnswerFiles/old
echo '<STIGComments Name="RHEL_9_STIG"/>' > /srv/stig/AnswerFiles/RHEL_9_STIG_Answers.xml
echo '<STIGComments Name="MOZ_Firefox"/>' > /srv/stig/AnswerFiles/Firefox.XML
echo 'how to edit these' > /srv/stig/AnswerFiles/README.txt
echo '<old/>' > /srv/stig/AnswerFiles/old/RHEL_9_STIG_2024.xml
adduser -D -H svc_stig
cat > /etc/samba/smb.conf <<CONF
[global]
  server role = standalone server
  server min protocol = SMB2_02
  map to guest = never
[stig]
  path = /srv/stig
  read only = yes
  valid users = svc_stig
CONF
printf 'Pa55-word\nPa55-word\n' | smbpasswd -s -a svc_stig >/dev/null
find /srv/stig -type f | sort | xargs md5sum > /srv/before.md5
touch /srv/ready
exec smbd --foreground --no-process-group --debug-stdout
EOF
chmod 755 "$work/start.sh"
"$engine" run -d --name "$name" -p "127.0.0.1:$port:445" -v "$work/start.sh:/start.sh:ro,Z" docker.io/library/alpine:3.20 /start.sh >/dev/null \
    || { echo "ABORT - could not start the Samba container"; exit 1; }
for _ in $(seq 1 120); do "$engine" exec "$name" test -f /srv/ready 2>/dev/null && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 1; done
(exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || { echo "ABORT - Samba did not start"; "$engine" logs "$name" | tail -5; exit 1; }
sleep 1

# a fake Evaluate-STIG_Bash.sh: what it was given, and what was in its answer files' folder
major=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
mkdir -p "$work/es"
cat > "$work/es/Evaluate-STIG_Bash.sh" <<EOF
#!/bin/bash
# its options: --Output, --OutputPath, --SelectSTIG, --AFPath ...
out=""; af=""; echo "\$*" > "$work/es_args.txt"
while [ \$# -gt 0 ]; do case "\$1" in --OutputPath) out="\$2"; shift ;; --AFPath) af="\$2"; shift ;; esac; shift; done
[ -n "\$af" ] && ls "\$af" > "$work/es_af.txt" && cat "\$af"/* > "$work/es_af_content.txt"
mkdir -p "\$out/WEB01/Checklist" && "$py" "$here/make_cklb.py" "\$out/WEB01/Checklist/WEB01_RHEL9_V2R10.cklb" "\$(cat "$work/es_results.json")" V2R10
echo "Done"
EOF
cat > "$work/settings.yml" <<EOF
check_stig_scanner: evaluate_stig
check_stig_evaluate: $work/es
check_stig_dir: $work/kept
check_stig_stigman_benchmarks: {"$major": RHEL_9_STIG}
check_stig_evaluate_answers: '\\\\127.0.0.1\\stig\\AnswerFiles'
check_stig_evaluate_answers_port: $port
check_stig_evaluate_answers_auth: ntlm
EOF

echo '{"SV-257985": "af:not_a_finding"}' > "$work/es_results.json"   # one rule answered from an answer file

run() {  # run NAME EXPECT(ok|fail) [args...] -- TEXT...
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -f "$work/es_args.txt" "$work/es_af.txt"
    (cd "$repo" && SMB_SHARE_USERNAME="${SMB_USER-svc_stig}" SMB_SHARE_PASSWORD="${SMB_PW-Pa55-word}" ANSIBLE_NOCOLOR=1 \
        ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False ANSIBLE_STDOUT_CALLBACK=default \
        "$ap" -i localhost, -c local playbooks/stig_drift_report.yml -e ansible_become=false -e "ansible_python_interpreter=$py" \
        -e "@$work/settings.yml" "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -E '"msg"|ERROR|WARNING\]' "$work/out.txt" | head -6 | cut -c1-400; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR|answer' "$work/out.txt" | head -6 | cut -c1-400; fails=$((fails + 1)); return 1; }
    done
    echo "ok   - $tname"
}

run "the answer files read from the share, copied to the host for this run, Evaluate-STIG given them" ok -- \
    'answer files: USED - read from \\\\127.0.0.1\\stig\\AnswerFiles' \
    '2 answer file(s) from \\\\127.0.0.1\\stig\\AnswerFiles as svc_stig: Firefox.XML, RHEL_9_STIG_Answers.xml' \
    "RHEL_9_STIG_Answers.xml - 35 bytes, changed 20" "SHA-256 $(printf '%s\n' '<STIGComments Name="RHEL_9_STIG"/>' | sha256sum | cut -c1-64)" \
    'localhost: given 2 answer file(s) from \\\\127.0.0.1\\stig\\AnswerFiles; 1 rule answered from them (answer key DEFAULT)' \
    'Evaluate-STIG: with 2 answer files from \\\\127.0.0.1\\stig\\AnswerFiles; 1 rule answered from them (answer key DEFAULT)'
afd="$(sed -n 's/.*--AFPath \([^ ]*\).*/\1/p' "$work/es_args.txt" 2>/dev/null)"
if [ "$(tr '\n' ' ' < "$work/es_af.txt" 2>/dev/null)" = "Firefox.XML RHEL_9_STIG_Answers.xml " ] && grep -q 'Name="RHEL_9_STIG"' "$work/es_af_content.txt" \
   && [ -n "$afd" ] && [ ! -e "$afd" ]; then
    echo "ok   - ... the .xml files only (not README.txt, not the sub-folder), as on the share; their folder removed after the scan"
else echo "FAIL - what Evaluate-STIG was given: $(cat "$work/es_af.txt" 2>&1) ($afd)"; fails=$((fails + 1)); fi
ls -d "${TMPDIR:-/tmp}"/*.stig-answers >/dev/null 2>&1 && { echo "FAIL - an answer files folder left behind"; fails=$((fails + 1)); } \
    || echo "ok   - ... no answer files folder left on the AAP side either"
run "a dry run (Check): the answer files given too, nothing kept" ok --check -- '2 answer file(s) from' '(not kept: dry run)'
[ -s "$work/es_af.txt" ] && echo "ok   - ... Evaluate-STIG had them in the dry run" || { echo "FAIL - none in the dry run"; fails=$((fails + 1)); }
if "$engine" exec "$name" sh -c 'find /srv/stig -type f | sort | xargs md5sum | diff - /srv/before.md5' >/dev/null 2>&1; then
    echo "ok   - the share as it was: only read"
else echo "FAIL - something on the share changed"; fails=$((fails + 1)); fi

echo '{}' > "$work/es_results.json"
run "given, but no rule answered from them: said, a warning in the report" fail -- \
    'localhost: given 2 answer file(s) from \\\\127.0.0.1\\stig\\AnswerFiles; NO rule answered from them' \
    'Evaluate-STIG was given 2 answer files from \\\\127.0.0.1\\stig\\AnswerFiles and answered no rule from them'
echo '{"SV-257985": "af:not_a_finding"}' > "$work/es_results.json"
run "not set in this run: said, Evaluate-STIG run with its own" ok -e '{"check_stig_evaluate_answers": ""}' -- \
    'answer files: NOT USED - check_stig_evaluate_answers is not set in this run'
grep -q -- '--AFPath' "$work/es_args.txt" && { echo "FAIL - given answer files when none were set"; fails=$((fails + 1)); } \
    || echo "ok   - ... Evaluate-STIG not given any"

SMB_PW=Not-it-9 run "a wrong password: Evaluate-STIG not run, the host not scanned, said why" fail -- \
    'NOT READ - Evaluate-STIG is not run: logging in to 127.0.0.1 as svc_stig' 'the user name or password is wrong' \
    '[WARNING] stig: the STIG scan did not run: Evaluate-STIG was not run: its answer files could not be read'
[ ! -e "$work/es_args.txt" ] && echo "ok   - ... Evaluate-STIG was not run" || { echo "FAIL - it ran without its answer files"; fails=$((fails + 1)); }
SMB_USER= SMB_PW= run "no credential: said which to attach" fail -- 'no login for the share' 'Windows file share' 'credential (user@YOURDOMAIN.MIL and its password)'
run "a folder that is not there: said" fail -e "{\"check_stig_evaluate_answers\": \"\\\\\\\\127.0.0.1\\\\stig\\\\Nope\"}" -- \
    'reading the folder \\\\127.0.0.1\\stig\\Nope' 'no such folder on the share'

[ -n "${SMB_KEEP:-}" ] && cp "$work/out.txt" "$SMB_KEEP"
echo
if [ $fails -eq 0 ]; then echo "all STIG answer files (SMB) scenarios passed"; else echo "$fails STIG answer files (SMB) scenario(s) FAILED"; exit 1; fi
