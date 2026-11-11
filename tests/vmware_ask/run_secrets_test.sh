#!/usr/bin/env bash
# VMware ask (roles/vmware_ask_stack): its podman secrets on an older and a newer podman, with a stand-in podman (it
# keeps the secrets in a folder and refuses what the real one refuses: --replace before 4.7, a name in use). The
# "Secrets |" tasks of tasks/main.yml run as they are. podman 4.4 (RHEL 9.2 / 8.8): made; a new value - the old one
# removed, then made again; the same value - nothing; podman 4.9: replaced in one step (--replace); no GenAI key - its
# secret removed.
#   bash tests/vmware_ask/run_secrets_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${ASK_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap 'rm -rf "${work:?}"' EXIT
fails=0
mkdir -p "$work/bin" "$work/secrets"

# the stand-in podman: podman secret inspect / rm / create, each call logged
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
        --replace) [ "\$(printf '%s\n' "\$FAKE_PODMAN_VERSION" 4.7 | sort -V | head -1)" = 4.7 ] || { echo "Error: unknown flag: --replace" >&2; exit 125; }
                   replace=1 ;;
        --label) shift; label="\${1#site.sha256=}" ;;
        -) ;;
        *) name="\$1" ;;
      esac
      shift
    done
    cat > /dev/null
    [ -f "\$d/\$name" ] && [ \$replace -eq 0 ] && { echo "Error: \$name: secret name in use" >&2; exit 125; }
    printf '%s' "\$label" > "\$d/\$name"; exit 0 ;;
esac
exit 1
EOF
chmod +x "$work/bin/podman"

# the "Secrets |" tasks of the role, as they are, in a file of their own
"$py" - "$repo/roles/vmware_ask_stack/tasks/main.yml" "$work/secrets.yml" <<'PY'
import sys, yaml
tasks = [t for t in yaml.safe_load(open(sys.argv[1])) if str(t.get("name", "")).startswith("Secrets |")]
yaml.safe_dump(tasks, open(sys.argv[2], "w"), sort_keys=False)
PY
cat > "$work/play.yml" <<EOF
- hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: The role's secrets tasks
      ansible.builtin.include_tasks: $work/secrets.yml
EOF

# run NAME PODMAN_VERSION PASSWORD KEY: the role's tasks once
run() {
    : > "$work/calls"
    PATH="$work/bin:$PATH" FAKE_PODMAN_VERSION="$2" ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i localhost, "$work/play.yml" -e "{\"_ask_podman\": {\"stdout\": \"$2\"}, \"vmware_ask_vcenter_password\": \"$3\", \"_ask_key\": \"$4\"}" \
        > "$work/out.txt" 2>&1
}
sha() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
check() {  # check NAME OK-CONDITION
    if eval "$2"; then echo "ok   - $1"; else echo "FAIL - $1"; sed 's/^/       /' "$work/calls"; grep -E 'fatal|Error' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); fi
}

run "podman 4.4: made" 4.4.1 pw1 key1
check "podman 4.4 (RHEL 9.2 / 8.8): both secrets made, without --replace" \
    '[ $(grep -c "secret create" "$work/calls") = 2 ] && ! grep -q -- --replace "$work/calls" && [ "$(cat "$work/secrets/vmware-ask-vcenter")" = "$(sha pw1)" ]'
run "podman 4.4: a new password" 4.4.1 pw2 key1
check "podman 4.4, a new password: the old secret removed, then made again" \
    'grep -q "secret rm vmware-ask-vcenter" "$work/calls" && [ $(grep -c "secret create" "$work/calls") = 1 ] && [ "$(cat "$work/secrets/vmware-ask-vcenter")" = "$(sha pw2)" ]'
run "podman 4.4: the same" 4.4.1 pw2 key1
check "podman 4.4, the same values: nothing removed or made" '! grep -qE "secret (rm|create)" "$work/calls"'
run "podman 4.9: a new password" 4.9.4 pw3 key1
check "podman 4.9, a new password: replaced in one step (--replace), nothing removed" \
    'grep -q -- "secret create --replace" "$work/calls" && ! grep -q "secret rm" "$work/calls" && [ "$(cat "$work/secrets/vmware-ask-vcenter")" = "$(sha pw3)" ]'
run "no GenAI key" 4.4.1 pw3 ""
check "no GenAI key: its secret removed" 'grep -q "secret rm vmware-ask-genai" "$work/calls" && [ ! -f "$work/secrets/vmware-ask-genai" ]'

echo
if [ $fails -eq 0 ]; then echo "all VMware ask secrets scenarios passed"; else echo "$fails VMware ask secrets scenario(s) FAILED"; exit 1; fi
