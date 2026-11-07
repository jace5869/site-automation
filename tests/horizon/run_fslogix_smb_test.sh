#!/usr/bin/env bash
# Horizon - FSLogix profiles report, the share for real: FSLogix folders on a Windows file share (horizon_fslogix_shares:
# \\server\share\folder, listed over SMB with smbprotocol - no mount; the "Windows file share" credential) against a
# Samba server in a container. Fake containers (sparse files, sizes chosen against a 100 MB cap, old times): one near
# the cap and one over it, a stale one, two folders for one user (the %username%_%sid% form), an empty folder, a
# differencing disk in use and one left behind; the share's free space read. A Check run reads the same; a wrong
# password: the share NOT READ, said why (red; the job fails only with horizon_health_fail). The share is served
# read-only and every file's size and time are the same after the runs: nothing written.
#   bash tests/horizon/run_fslogix_smb_test.sh   (ANSIBLE_PLAYBOOK=... for another ansible-core; CONTAINER_ENGINE=...)
# Needs: podman or docker (an Alpine image with Samba, installed at start), smbprotocol for the Python Ansible runs on.
# Rootless podman without systemd: CONTAINER_RUN_OPTS="--cgroup-manager=cgroupfs --cgroups=disabled".
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(head -1 "$(command -v "$ap")" | sed -n 's/^#! *//p' | awk '{print $1}')"
[ -x "$py" ] || py="$(command -v python3)"
engine="${CONTAINER_ENGINE:-$(command -v podman || command -v docker)}"
read -r -a run_opts <<< "${CONTAINER_RUN_OPTS:-}"
port="${SMB_TEST_PORT:-14465}"
name="sa-fslogix-smb-test-$$"
work="$(mktemp -d)"
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT
fails=0
"$py" -c 'import smbclient' 2>/dev/null || { echo "ABORT - $py has no smbprotocol (pip install smbprotocol)"; exit 1; }
[ -n "$engine" ] || { echo "ABORT - no podman or docker for the Samba server"; exit 1; }

# the share "profiles": user folders as FSLogix makes them (%sid%_%username%), fake containers as sparse files
p="$work/srv/profiles"
J=S-1-5-21-1-2-3-1001
ago() { date -d "@$(( $(date +%s) - $1 ))" '+%Y-%m-%d %H:%M:%S'; }
mkdir -p "$p/${J}_jdoe" "$p/S-1-5-21-1-2-3-1002_asmith" "$p/jdoe_$J" "$p/S-1-5-21-1-2-3-1003_carol" "$p/S-1-5-21-1-2-3-1004_dave" \
    "$p/S-1-5-21-1-2-3-1005_erin"
truncate -s 97M "$p/${J}_jdoe/Profile_jdoe.vhdx"                      # 97% of 100 MB: red
truncate -s 10M "$p/${J}_jdoe/ODFC_jdoe.vhdx"
truncate -s 88M "$p/S-1-5-21-1-2-3-1002_asmith/Profile_asmith.vhdx"   # 88%: amber
truncate -s 5M "$p/S-1-5-21-1-2-3-1002_asmith/RW.vhdx"                # a session's differencing disk, written now
truncate -s 20M "$p/jdoe_$J/Profile_jdoe.vhdx"                        # jdoe again, the other folder form, old
touch -d "$(ago $((400 * 86400)))" "$p/jdoe_$J/Profile_jdoe.vhdx"
truncate -s 30M "$p/S-1-5-21-1-2-3-1003_carol/Profile_carol.vhdx"     # not written for 200 days: stale
touch -d "$(ago $((200 * 86400)))" "$p/S-1-5-21-1-2-3-1003_carol/Profile_carol.vhdx"
truncate -s 10M "$p/S-1-5-21-1-2-3-1005_erin/Profile_erin.vhdx"
truncate -s 2M "$p/S-1-5-21-1-2-3-1005_erin/RW.vhdx"                  # not written for 3 days: left behind
touch -d "$(ago $((3 * 86400)))" "$p/S-1-5-21-1-2-3-1005_erin/RW.vhdx" "$p/S-1-5-21-1-2-3-1005_erin/Profile_erin.vhdx"
chmod -R a+rX "$work/srv"
snapshot() { (cd "$p" && find . -printf '%p %s %T@\n' | sort); }
before="$(snapshot)"

cat > "$work/start.sh" <<'EOF'
#!/bin/sh
set -e
apk add --no-cache samba >/dev/null
adduser -D -H svc_fx
cat > /etc/samba/smb.conf <<CONF
[global]
  server role = standalone server
  server min protocol = SMB2_02
  map to guest = never
[profiles]
  path = /seed/profiles
  read only = yes
  valid users = svc_fx
CONF
printf 'Pa55-word\nPa55-word\n' | smbpasswd -s -a svc_fx >/dev/null
touch /ready
exec smbd --foreground --no-process-group --debug-stdout
EOF
chmod 755 "$work/start.sh"
"$engine" run "${run_opts[@]}" -d --name "$name" -p "127.0.0.1:$port:445" -v "$work/start.sh:/start.sh:ro,Z" -v "$work/srv:/seed:ro,Z" \
    docker.io/library/alpine:3.20 /start.sh >/dev/null || { echo "ABORT - could not start the Samba container"; exit 1; }
for _ in $(seq 1 120); do "$engine" exec "$name" test -f /ready 2>/dev/null && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && break; sleep 1; done
(exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null || { echo "ABORT - Samba did not start"; "$engine" logs "$name" | tail -5; exit 1; }
sleep 1

cat > "$work/settings.yml" <<EOF
horizon_fslogix_shares: ['\\\\127.0.0.1\\profiles']
horizon_fslogix_port: $port
horizon_fslogix_auth: ntlm
horizon_fslogix_user: svc_fx
horizon_fslogix_password: Pa55-word
horizon_fslogix_size_mb: 100
EOF

play() {  # play NAME EXPECT(ok|fail) [args...] -- TEXT...  (a TEXT starting with ! must not be there)
    local tname="$1" want="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, -c local playbooks/horizon_fslogix_report.yml -e "ansible_python_interpreter=$py" -e "@$work/settings.yml" "${args[@]}") \
        > "$work/out.txt" 2>&1
    rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; grep -n "fatal\|FAILED\|msg\|NOT READ" "$work/out.txt" | cut -c1-300 | tail -8; fails=$((fails + 1)); return
    fi
    local miss=0
    for x in "${texts[@]}"; do
        if [ "${x:0:1}" = "!" ]; then grep -qF -- "${x:1}" "$work/out.txt" && { echo "FAIL - $tname: found '${x:1}'"; miss=1; }
        else grep -qF -- "$x" "$work/out.txt" || { echo "FAIL - $tname: missing '$x'"; miss=1; }; fi
    done
    if [ $miss -eq 0 ]; then echo "ok   - $tname"; else sed -n '/Horizon FSLogix profiles:/,/PLAY RECAP/p' "$work/out.txt" | cut -c1-260 | head -120; fails=$((fails + 1)); fi
}

play "the share listed: over and near the cap, stale, two for jdoe, empty folder, differencing disks, free space" ok -- \
    '\\\\127.0.0.1\\profiles as svc_fx: 6 folder(s), 8 file(s),' " free of " \
    "Horizon FSLogix profiles: 2 over or near the cap" \
    "those users get errors when it fills: jdoe (profile, 97.0%)" \
    '"Near the cap: 1"' '"Two for one user: 1"' '"Stale: 2 (50 MB to reclaim)"' \
    "another profile container for jdoe: jdoe_S-1-5-21-1-2-3-1001\\\\Profile_jdoe.vhdx" \
    "it is %username%_%sid% (FlipFlopProfileDirectoryName)" \
    "no container in it - empty" \
    "yes - RW.vhdx (5 MB) written 0 min ago - a session's differencing disk" \
    "RW.vhdx (2 MB) not written for 3 days - a differencing disk left behind" \
    "88.0%" \
    "!NOT READ" "!another odfc container"
play "Check: the same report" ok --check -- "those users get errors when it fills: jdoe (profile, 97.0%)" "!NOT READ"
play "a wrong password: the share NOT READ, said why - the job stays green" ok -e '{"horizon_fslogix_password": "Not-the-Pa55"}' -- \
    'NOT READ - \\\\127.0.0.1\\profiles: logging in to 127.0.0.1 as svc_fx' "the user name or password is wrong" \
    "not read: logging in" '"Shares low: 1"'
play "... and with horizon_health_fail: the job fails" fail -e '{"horizon_fslogix_password": "Not-the-Pa55", "horizon_health_fail": true}' -- \
    "1 critical finding(s)"
play "a wrong share: said" ok -e '{"horizon_fslogix_shares": ["\\\\127.0.0.1\\nope"]}' -- 'NOT READ - \\\\127.0.0.1\\nope' "no such share or folder on that server"
[ "$(snapshot)" = "$before" ] && echo "ok   - every file's size and time as before: nothing written" \
    || { echo "FAIL - the share changed"; diff <(echo "$before") <(snapshot) | head; fails=$((fails + 1)); }

echo
if [ $fails -eq 0 ]; then echo "all FSLogix share scenarios passed"; else echo "$fails FSLogix share scenario(s) FAILED"; exit 1; fi
