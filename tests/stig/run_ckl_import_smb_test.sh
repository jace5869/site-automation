#!/usr/bin/env bash
# Workflow 40 / the checklist import as STIG Manager Watcher (0.76.0), the share for real: checklists on a Windows file
# share (stigman_ckl_import_src: \\server\share\folder, read over SMB with smbprotocol - no mount; the "Windows file share"
# credential) against a Samba server in a container, and the stand-in STIG Manager (tests/stig/stigman_mock.py). With
# stigman_ckl_import_auto: existing and stigman_ckl_import_move: the reviews of the asset that is there written at once,
# its file moved on the share to imported/<date>/ (its sub-folder kept); the new host's file left (it waits for the
# approval); imported/ never read; a Check run moves nothing; a share the account may only read: nothing moved, said why.
#   bash tests/stig/run_ckl_import_smb_test.sh   (ANSIBLE_PLAYBOOK=... for another ansible-core; CONTAINER_ENGINE=...)
# Needs: podman or docker (an Alpine image with Samba, installed at start), smbprotocol for the Python Ansible runs on.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(head -1 "$(command -v "$ap")" | sed -n 's/^#! *//p' | awk '{print $1}')"
[ -x "$py" ] || py="$(command -v python3)"
engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
port="${SMB_TEST_PORT:-14463}"
port_sm="${STIG_CKL_SM_PORT:-12599}"
name="sa-ckl-smb-test-$$"
work="$(mktemp -d)"
sm_pid=""
cleanup() { [ -n "$sm_pid" ] && kill "$sm_pid" 2>/dev/null; "$engine" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
"$py" -c 'import smbclient' 2>/dev/null || { echo "ABORT - $py has no smbprotocol (pip install smbprotocol)"; exit 1; }
[ -n "$engine" ] || { echo "ABORT - no podman or docker for the Samba server"; exit 1; }
aria=VMW_Aria_Operations_8-x_Apache_STIG

# the share "stig": ckl/ with vrops04 (an asset in Ops) in a sub-folder, vrops06 (no asset), an old import; svc_ckl may
# change it, svc_ro only read it
mkdir -p "$work/srv/ckl/ops" "$work/srv/ckl/imported/2026-01-01"
for h in vrops04 vrops06; do
    sed -e "s#<HOST_NAME>vrops01</HOST_NAME>#<HOST_NAME>$h</HOST_NAME>#" -e "s#vrops01.example.mil#$h.example.mil#" "$here/ckl/inspec_apache.ckl" > "$work/srv/ckl/$h.ckl"
done
mv "$work/srv/ckl/vrops04.ckl" "$work/srv/ckl/ops/vrops04.ckl"
cp "$work/srv/ckl/ops/vrops04.ckl" "$work/srv/ckl/imported/2026-01-01/vrops01.ckl"
cat > "$work/start.sh" <<'EOF'
#!/bin/sh
set -e
apk add --no-cache samba >/dev/null
mkdir -p /srv/stig && cp -r /seed/ckl /srv/stig/ckl
adduser -D -H svc_ckl
adduser -D -H svc_ro
chown -R svc_ckl /srv/stig
cat > /etc/samba/smb.conf <<CONF
[global]
  server role = standalone server
  server min protocol = SMB2_02
  map to guest = never
[stig]
  path = /srv/stig
  read only = no
  valid users = svc_ckl svc_ro
  read list = svc_ro
CONF
printf 'Pa55-word\nPa55-word\n' | smbpasswd -s -a svc_ckl >/dev/null
printf 'Pa55-word\nPa55-word\n' | smbpasswd -s -a svc_ro >/dev/null
touch /srv/ready
exec smbd --foreground --no-process-group --debug-stdout
EOF
chmod 755 "$work/start.sh"
"$engine" run -d --name "$name" -p "127.0.0.1:$port:445" -v "$work/start.sh:/start.sh:ro,Z" -v "$work/srv:/seed:ro,Z" \
    docker.io/library/alpine:3.20 /start.sh >/dev/null || { echo "ABORT - could not start the Samba container"; exit 1; }
for _ in $(seq 1 120); do "$engine" exec "$name" test -f /srv/ready 2>/dev/null && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 1; done
(exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || { echo "ABORT - Samba did not start"; "$engine" logs "$name" | tail -5; exit 1; }
sleep 1
on_share() { "$engine" exec "$name" sh -c 'cd /srv/stig/ckl && find . -type f | sort | tr "\n" " "'; }

"$py" - "$work/sm.json" "$aria" <<'PY'
import json, sys
aria = sys.argv[2]
rules = {aria: [{"ruleId": "SV-2600%02dr1_rule" % n, "groupId": "V-2600%02d" % n, "version": "VRPE-8X-%06d" % n, "severity": "medium",
                 "title": "Aria Operations Apache rule %d" % n} for n in (1, 2, 5, 7, 9)]}
json.dump({"secret": "s3cret", "client": "stigwrite", "writers": ["stigwrite"], "managers": ["stigwrite"],
           "collections": [{"collectionId": "3", "name": "Watcher"}, {"collectionId": "4", "name": "Ops"}],
           "assets": {"3": [], "4": [{"assetId": "41", "name": "vrops04", "fqdn": "vrops04.example.mil", "stigs": [{"benchmarkId": aria, "revisionStr": "V1R1"}]}]},
           "reviews": {}, "titles": {aria: "VMware Aria Operations 8.x Apache Server Security Technical Implementation Guide"},
           "latest": {aria: "V1R1"}, "rules": rules}, open(sys.argv[1], "w"))
PY
"$py" "$here/stigman_mock.py" "$port_sm" "$work/sm.json" "$work/sm.log" >/dev/null 2>&1 & sm_pid=$!
sleep 1
cat > "$work/settings.yml" <<EOF
check_stig_stigman_api: http://127.0.0.1:$port_sm/api
check_stig_stigman_token_url: http://127.0.0.1:$port_sm/token
check_stig_stigman_client_id: stigwrite
check_stig_stigman_client_secret: s3cret
stigman_ckl_import_src: '\\\\127.0.0.1\\stig\\ckl'
stigman_ckl_import_port: $port
stigman_ckl_import_auth: ntlm
stigman_ckl_import_user: svc_ckl
stigman_ckl_import_password: Pa55-word
stigman_ckl_import_collection: Watcher
stigman_ckl_import_collections: ['*']
stigman_ckl_import_auto: existing
stigman_ckl_import_move: true
report_web_dest: $work/site
EOF
day=$(date -u +%F)

play() {  # play NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/stigman_ckl_import.yml -e "ansible_python_interpreter=$py" -e "@$work/settings.yml" "${args[@]}") \
        > "$work/out.txt" 2>&1
    rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -n "fatal\|FAILED\|msg" "$work/out.txt" | cut -c1-300 | tail -8; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else grep -n "moved\|LEFT\|written - \|fatal\|NOT READ" "$work/out.txt" | cut -c1-300 | head -12; fails=$((fails + 1)); fi
}

play "read-only account: read, vrops04 written, but nothing moved - said why" ok -e '{"stigman_ckl_import_user": "svc_ro"}' -- \
    "2 file(s) from " "ckl as svc_ro" "written - Ops/vrops04 $aria VRPE-8X-000001" "error - ops/vrops04.ckl" \
    "the account may read but not move files here - give it Modify on the folder" "!imported/2026-01-01" "!moved - "
[ "$(on_share)" = "./imported/2026-01-01/vrops01.ckl ./ops/vrops04.ckl ./vrops06.ckl " ] && echo "ok   - ... the share as it was" \
    || { echo "FAIL - the share changed: $(on_share)"; fails=$((fails + 1)); }
play "Check: would move vrops04's file, nothing moved" ok --check -- "would move - ops/vrops04.ckl" "!moved - " "!error - "
[ "$(on_share)" = "./imported/2026-01-01/vrops01.ckl ./ops/vrops04.ckl ./vrops06.ckl " ] && echo "ok   - ... the share as it was" \
    || { echo "FAIL - Check moved: $(on_share)"; fails=$((fails + 1)); }
play "vrops04 (in STIG Manager already): its file moved on the share, its sub-folder kept; vrops06 (new) left" ok -- \
    "moved - ops/vrops04.ckl - imported/$day/ops/vrops04.ckl" "vrops06.ckl - LEFT: waits for the approval" "!imported/2026-01-01"
[ "$(on_share)" = "./imported/2026-01-01/vrops01.ckl ./imported/$day/ops/vrops04.ckl ./vrops06.ckl " ] && echo "ok   - ... on the share: ops/vrops04.ckl in imported/$day/ops/" \
    || { echo "FAIL - the share: $(on_share)"; fails=$((fails + 1)); }
"$engine" exec "$name" sh -c "mkdir -p /srv/stig/ckl/ops && cp /srv/stig/ckl/imported/$day/ops/vrops04.ckl /srv/stig/ckl/ops/ && chown -R svc_ckl /srv/stig/ckl"
play "the same file again the same day: a second name" ok -- "moved - ops/vrops04.ckl - imported/$day/ops/vrops04-2.ckl"

echo
if [ $fails -eq 0 ]; then echo "all checklist import share scenarios passed"; else echo "$fails checklist import share scenario(s) FAILED"; exit 1; fi
