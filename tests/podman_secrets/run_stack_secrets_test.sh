#!/usr/bin/env bash
# 1.68.2: the podman secrets of the three stacks (roles/site_portal_stack, roles/stigman_stack, roles/vmware_ask_stack),
# their "Secret |" / "Secrets |" tasks as they are, with a stand-in podman that refuses what the real ones refuse:
# --replace before 4.7 (an unknown flag), a name in use without it, and podman 4.9.4-rhel's bug - --replace for a secret
# that is not there yet ("deleting secret : : no secret data with ID"). Each role: a new secret on 4.9.4-rhel (made,
# without --replace); a new value (4.9.4-rhel: --replace; 4.4: removed, then made); a create podman refuses - the job
# stops with podman's reason, never the value or its hash.
#   bash tests/podman_secrets/run_stack_secrets_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${SECRETS_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap 'rm -rf "${work:?}"' EXIT
fails=0
mkdir -p "$work/bin" "$work/secrets"

cat > "$work/bin/podman" <<EOF
#!/bin/bash
echo "\$*" >> "$work/calls"
d="$work/secrets"
[ "\$1" = secret ] || exit 1
case "\$2" in
  inspect) [ -f "\$d/\$3" ] || { echo "Error: no such secret \$3" >&2; exit 125; }; cat "\$d/\$3"; exit 0 ;;
  rm) [ -f "\$d/\$3" ] || { echo "Error: no such secret \$3" >&2; exit 1; }; rm -f "\$d/\$3"; exit 0 ;;
  create)
    shift 2; replace=0; label=""
    while [ \$# -gt 0 ]; do
      case "\$1" in
        --replace) [ "\$(printf '%s\n' "\${FAKE_PODMAN_VERSION%%-*}" 4.7 | sort -V | head -1)" = 4.7 ] || { echo "Error: unknown flag: --replace" >&2; exit 125; }
                   replace=1 ;;
        --label) shift; label="\${1#site.sha256=}" ;;
        -) ;;
        *) name="\$1" ;;
      esac
      shift
    done
    cat > /dev/null
    [ -n "\${FAKE_PODMAN_FAIL:-}" ] && { echo "\$FAKE_PODMAN_FAIL" >&2; exit 125; }
    [ \$replace -eq 1 ] && [ ! -f "\$d/\$name" ] && [ "\$FAKE_PODMAN_VERSION" = 4.9.4-rhel ] && { echo "Error: deleting secret : : no secret data with ID" >&2; exit 125; }
    [ -f "\$d/\$name" ] && [ \$replace -eq 0 ] && { echo "Error: \$name: secret name in use" >&2; exit 125; }
    printf '%s' "\$label" > "\$d/\$name"; exit 0 ;;
esac
exit 1
EOF
chmod +x "$work/bin/podman"

# tasks ROLE PREFIX: the role's secret tasks, as they are, in a play of their own
tasks() {
    "$py" - "$repo/roles/$1/tasks/main.yml" "$work/$1.yml" "$2" <<'PY'
import sys, yaml
tasks = [t for t in yaml.safe_load(open(sys.argv[1])) if str(t.get("name", "")).startswith(sys.argv[3])]
yaml.safe_dump(tasks, open(sys.argv[2], "w"), sort_keys=False)
PY
    printf -- '- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks:\n    - ansible.builtin.include_tasks: %s\n' "$work/$1.yml" > "$work/$1-play.yml"
}
tasks site_portal_stack "Secret |"
tasks stigman_stack "Secrets |"
tasks vmware_ask_stack "Secrets |"

# run ROLE PODMAN_VERSION VARS_JSON: the role's tasks once (rc in $rc)
run() {
    : > "$work/calls"
    PATH="$work/bin:$PATH" FAKE_PODMAN_VERSION="$2" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, "$work/$1-play.yml" -e "$3" > "$work/out.txt" 2>&1
    rc=$?
}
sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
check() {  # check NAME OK-CONDITION
    if eval "$2"; then echo "ok   - $1"; else echo "FAIL - $1"; sed 's/^/       /' "$work/calls"; grep -E 'fatal|Error|msg' "$work/out.txt" | head -6 | cut -c1-300; fails=$((fails + 1)); fi
}
made() { [ "$(cat "$work/secrets/$1" 2>/dev/null)" = "$(sha "$2")" ]; }
clean() { rm -f "$work/secrets/"*; }

# ---- the site portal: one secret, the AAP token
P() { printf '{"_portal_podman": {"stdout": "%s"}, "portal_aap_token": "%s"}' "$1" "$2"; }
clean
run site_portal_stack 4.9.4-rhel "$(P 4.9.4-rhel tok-one-0001)"
check "portal, podman 4.9.4-rhel, a new secret: made, without --replace" \
    '[ $rc = 0 ] && ! grep -q -- --replace "$work/calls" && made site-portal-aap-token tok-one-0001'
run site_portal_stack 4.9.4-rhel "$(P 4.9.4-rhel tok-two-0002)"
check "portal, podman 4.9.4-rhel, a new value: replaced (--replace), nothing removed" \
    '[ $rc = 0 ] && grep -q -- "secret create --replace" "$work/calls" && ! grep -q "secret rm" "$work/calls" && made site-portal-aap-token tok-two-0002'
run site_portal_stack 4.4.1 "$(P 4.4.1 tok-three-0003)"
check "portal, podman 4.4, a new value: removed, then made" \
    '[ $rc = 0 ] && grep -q "secret rm site-portal-aap-token" "$work/calls" && ! grep -q -- --replace "$work/calls" && made site-portal-aap-token tok-three-0003'
run site_portal_stack 4.4.1 "$(P 4.4.1 tok-three-0003)"
check "portal, the same value: nothing removed or made" '[ $rc = 0 ] && ! grep -qE "secret (rm|create)" "$work/calls"'
clean
FAKE_PODMAN_FAIL="Error: creating secret: disk quota exceeded" run site_portal_stack 4.9.4-rhel "$(P 4.9.4-rhel tok-four-0004)"
check "portal, podman refuses: the job stops with podman's reason, never the token or its hash" \
    '[ $rc != 0 ] && grep -q "podman secret create site-portal-aap-token failed (rc 125): Error: creating secret: disk quota exceeded" "$work/out.txt" && ! grep -qE "tok-four-0004|$(sha tok-four-0004)" "$work/out.txt"'

# ---- STIG Manager: two secrets; a value that differs is replaced only with stigman_replace_secrets
S() { printf '{"_stigman_podman": {"stdout": "%s"}, "stigman_mysql_root_password": "%s", "stigman_db_password": "%s", "stigman_replace_secrets": %s}' "$1" "$2" "$3" "$4"; }
clean
run stigman_stack 4.9.4-rhel "$(S 4.9.4-rhel root-pw-0001 db-pw-0001 false)"
check "STIG Manager, podman 4.9.4-rhel, a first deployment: both made, without --replace" \
    '[ $rc = 0 ] && [ $(grep -c "secret create" "$work/calls") = 2 ] && ! grep -q -- --replace "$work/calls" && made stigman-db-password db-pw-0001'
run stigman_stack 4.9.4-rhel "$(S 4.9.4-rhel root-pw-0001 db-pw-0002 false)"
check "STIG Manager, a value that differs, not allowed: refused, nothing changed" \
    '[ $rc != 0 ] && grep -q "stigman-db-password value in AAP differs" "$work/out.txt" && ! grep -qE "secret (rm|create)" "$work/calls" && made stigman-db-password db-pw-0001'
run stigman_stack 4.9.4-rhel "$(S 4.9.4-rhel root-pw-0001 db-pw-0002 true)"
check "STIG Manager, podman 4.9.4-rhel, replacing allowed: replaced (--replace), the other left" \
    '[ $rc = 0 ] && [ "$(grep -c "secret create" "$work/calls")" = 1 ] && grep -q -- "secret create --replace .*stigman-db-password" "$work/calls" && made stigman-db-password db-pw-0002'
run stigman_stack 4.4.1 "$(S 4.4.1 root-pw-0001 db-pw-0003 true)"
check "STIG Manager, podman 4.4, replacing allowed: removed, then made (no --replace there)" \
    '[ $rc = 0 ] && grep -q "secret rm stigman-db-password" "$work/calls" && ! grep -q -- --replace "$work/calls" && made stigman-db-password db-pw-0003'
clean
FAKE_PODMAN_FAIL="Error: creating secret: disk quota exceeded" run stigman_stack 4.9.4-rhel "$(S 4.9.4-rhel root-pw-0009 db-pw-0009 false)"
check "STIG Manager, podman refuses: the job stops with each secret's name and podman's reason, no value" \
    '[ $rc != 0 ] && grep -q "stigman-mysql-root - podman secret create failed: Error: creating secret: disk quota exceeded" "$work/out.txt" && ! grep -qE "root-pw-0009|db-pw-0009|$(sha db-pw-0009)" "$work/out.txt"'

# ---- VMware ask: a new secret on 4.9.4-rhel (the rest: tests/vmware_ask/run_secrets_test.sh)
A() { printf '{"_ask_podman": {"stdout": "%s"}, "vmware_ask_vcenter_password": "%s", "_ask_key": "%s"}' "$1" "$2" "$3"; }
clean
run vmware_ask_stack 4.9.4-rhel "$(A 4.9.4-rhel vc-pw-0001 key-0001)"
check "VMware ask, podman 4.9.4-rhel, new secrets: made, without --replace" \
    '[ $rc = 0 ] && [ $(grep -c "secret create" "$work/calls") = 2 ] && ! grep -q -- --replace "$work/calls" && made vmware-ask-vcenter vc-pw-0001'
run vmware_ask_stack 4.9.4-rhel "$(A 4.9.4-rhel vc-pw-0002 key-0001)"
check "VMware ask, podman 4.9.4-rhel, a new value: replaced (--replace)" \
    '[ $rc = 0 ] && grep -q -- "secret create --replace .*vmware-ask-vcenter" "$work/calls" && made vmware-ask-vcenter vc-pw-0002'
clean
FAKE_PODMAN_FAIL="Error: creating secret: disk quota exceeded" run vmware_ask_stack 4.9.4-rhel "$(A 4.9.4-rhel vc-pw-0009 key-0009)"
check "VMware ask, podman refuses: the job stops with podman's reason, no value" \
    '[ $rc != 0 ] && grep -q "vmware-ask-vcenter - podman secret create failed: Error: creating secret: disk quota exceeded" "$work/out.txt" && ! grep -qE "vc-pw-0009|key-0009" "$work/out.txt"'

# ---- the portal's preflight: its message shown (not no_log - it holds no secret)
"$py" - "$repo/roles/site_portal_stack/tasks/main.yml" <<'PY' && echo "ok   - portal preflight: its fail_msg not hidden by no_log" || { echo "FAIL - portal preflight still no_log"; fails=$((fails + 1)); }
import sys, yaml
t = [x for x in yaml.safe_load(open(sys.argv[1])) if str(x.get("name", "")).startswith("Preflight | AAP's address")][0]
sys.exit(1 if t.get("no_log") else 0)
PY

echo
if [ $fails -eq 0 ]; then echo "all stack secrets scenarios passed"; else echo "$fails stack secrets scenario(s) FAILED"; exit 1; fi
