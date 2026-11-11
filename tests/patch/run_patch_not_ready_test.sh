#!/usr/bin/env bash
# Patch hosts (playbooks/patch_hosts.yml): a host where the job cannot become root is listed as NOT READY with the
# reason, and the job goes on without it - not failed. Two made-up hosts on this machine (the local connection) with a
# stand-in sudo that refuses: host01 is given a wrong password (asked, refused), host02 none (sudo -n: a password is
# required). Nothing runs as root and nothing is patched (Check mode; neither host gets past the readiness step).
#   bash tests/patch/run_patch_not_ready_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="$(dirname "$(command -v "$ap")")/python3"
[ -x "$py" ] || py="$(command -v python3)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
fails=0
check() { if grep -qF -- "$2" "$work/out.txt"; then echo "ok   - $1"; else echo "FAIL - $1: no [$2] in the output"; fails=$((fails + 1)); fi; }

cat > "$work/sudo" <<'EOF'
#!/bin/bash
# a sudo that refuses: with -p it asks for the password and says it is wrong; with -n it says one is required
prompt=""
while [ $# -gt 0 ]; do case "$1" in -p) prompt="$2"; shift 2 ;; -u) shift 2 ;; -*) shift ;; *) break ;; esac; done
if [ -n "$prompt" ]; then
    printf '%s' "$prompt" >&2; read -r _; echo "Sorry, try again." >&2
    printf '%s' "$prompt" >&2; read -r _; echo "sudo: 1 incorrect password attempt" >&2
    exit 1
fi
echo "sudo: a password is required" >&2
exit 1
EOF
chmod +x "$work/sudo"
cat > "$work/inv.yml" <<EOF
patch_hosts:
  hosts:
    host01.example.mil: {ansible_become_password: not-the-password}
    host02.example.mil: {}
  vars:
    ansible_connection: local
    ansible_python_interpreter: $py
    ansible_become_exe: $work/sudo
    ansible_become_user: sa-not-ready      # never the user running the test (as root, Ansible skips a become to root)
    ansible_pipelining: true               # no temporary files to hand to that user: the stand-in sudo is reached
    patch_skip_unsupported: false      # this machine may not be RHEL-like: the readiness step is what is tested
EOF
(cd "$repo" && "$ap" -i "$work/inv.yml" playbooks/patch_hosts.yml --check) > "$work/out.txt" 2>&1
rc=$?
if [ $rc -eq 0 ]; then echo "ok   - the job is not failed (rc 0)"; else echo "FAIL - the job failed (rc $rc)"; fails=$((fails + 1)); fi
check "host01 (wrong password): NOT READY, with why" "host01.example.mil: NOT READY - cannot become root"
check "host02 (no password): NOT READY, with why" "host02.example.mil: NOT READY - cannot become root"
check "the summary lists both" "2 host(s) not ready, not patched"
# the wrong password: a newer ansible-core (2.22) stops at the second prompt, 2.16 reads sudo's last line
if grep -qE 'host01\.example\.mil \(cannot become root: (Sorry, try again|sudo: 1 incorrect password attempt)\)' "$work/out.txt"; then
    echo "ok   - host01's reason: the password was refused"
else echo "FAIL - host01's reason: the password was refused"; fails=$((fails + 1)); fi
check "host02's reason: a password is required" "host02.example.mil (cannot become root: sudo: a password is required)"
if grep -qE 'failed=[1-9]' "$work/out.txt"; then echo "FAIL - a host is counted failed"; fails=$((fails + 1)); else echo "ok   - no host counted failed"; fi
if [ $fails -ne 0 ]; then
    echo "--- the run:"; tail -80 "$work/out.txt"
    echo "$fails failed"; exit 1
fi
echo "all not-ready scenarios passed"
