#!/usr/bin/env bash
# Run EVERY Linux check role (playbooks/health_check.yml, health_checks=all) on this machine.
# What it proves: each role runs to the end here - none breaks on a real system (a check that
# raises an error becomes a "<check>:check-error" finding). It does NOT judge the findings: a
# CI runner is not a hardened RHEL host and will have some.
#   bash tests/linux/run_linux_checks.sh          (ANSIBLE_PLAYBOOK=... to pick one; BECOME=false to skip sudo)
set -u
repo="$(cd "$(dirname "$0")/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
become="${BECOME:-true}"
out="$(mktemp)"; trap 'rm -f "$out"' EXIT
(cd "$repo" && ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
    "$ap" -i localhost, -c local playbooks/health_check.yml -e health_checks=all -e "ansible_become=$become") > "$out" 2>&1
rc=$?
fails=0
# rc 2 = the host failed (findings). Anything else non-zero means the playbook itself broke.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then echo "FAIL - playbook exit $rc"; tail -30 "$out"; fails=$((fails + 1)); fi
if ! grep -q "Checks run: disk, mounts, services, performance, time, network, logging, mariadb, selinux, fapolicyd, auditd, accounts, certs, patching" "$out"; then
    echo "FAIL - not every check ran"; grep "Checks run" "$out"; fails=$((fails + 1))
fi
# A check that broke: reported as <check>:check-error. (Without sudo some checks cannot read root files:
# BECOME=false only, and only "permission" errors are tolerated then.)
bad="$(grep -o '"\[[A-Z]*\] [a-z]*: the [a-z]* check could not run: [^"]*' "$out" | grep -v -i 'permission denied' || true)"
if [ "$become" = "true" ]; then bad="$(grep -o '"\[[A-Z]*\] [a-z]*: the [a-z]* check could not run: [^"]*' "$out" || true)"; fi
if [ -n "$bad" ]; then echo "FAIL - a check could not run:"; echo "$bad"; fails=$((fails + 1)); fi
if grep -q "The task includes an option with an undefined variable\|is undefined\|TemplateSyntaxError" "$out"; then
    echo "FAIL - an undefined variable or template error"; grep -m3 "undefined\|TemplateSyntax" "$out"; fails=$((fails + 1))
fi
if [ "$fails" -eq 0 ]; then echo "ok   - all 14 Linux check roles ran (exit $rc)"; else exit 1; fi
