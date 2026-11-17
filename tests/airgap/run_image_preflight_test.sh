#!/usr/bin/env bash
# 1.70.3: the image preflight of the three stacks (roles/stigman_stack, roles/site_portal_stack, roles/vmware_ask_stack):
# their "Registry |" and "Images |" tasks, as they are, in a play of their own, with a stand-in podman that knows which
# images are on the host. A missing image stops the job at once, naming the image and the setting, with the load steps,
# and nothing is pulled; with <stack>_pull_images: true a missing image is pulled (not in a dry run); a pull podman
# refuses stops the job the same way. The portal's nginx image only counts with portal_own_nginx.
#   bash tests/airgap/run_image_preflight_test.sh        (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${AIRGAP_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap 'rm -rf "${work:?}"' EXIT
fails=0
mkdir -p "$work/bin" "$work/images"

# the stand-in: images are files in $work/images (the name, / and : made safe); pull adds one unless FAKE_PULL_FAIL
cat > "$work/bin/podman" <<EOF
#!/bin/bash
echo "\$*" >> "$work/calls"
f() { printf '%s' "\$1" | tr '/:' '__'; }
case "\$1 \$2" in
  "image exists") [ -f "$work/images/\$(f "\$3")" ] ;;
  "pull "*) [ -n "\${FAKE_PULL_FAIL:-}" ] && { echo "Error: initializing source docker://\$2: pinging container registry: connection refused" >&2; exit 125; }
            : > "$work/images/\$(f "\$2")" ;;
  "login "*) cat > /dev/null ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$work/bin/podman"

# tasks ROLE: the role's "Registry |" and "Images |" tasks, as they are, in a play of their own
tasks() {
    "$py" - "$repo/roles/$1/tasks/main.yml" "$work/$1.yml" <<'PY'
import sys, yaml
tasks = [t for t in yaml.safe_load(open(sys.argv[1])) if str(t.get("name", "")).startswith(("Registry |", "Images |"))]
yaml.safe_dump(tasks, open(sys.argv[2], "w"), sort_keys=False)
PY
    printf -- '- hosts: localhost\n  connection: local\n  gather_facts: false\n  tasks:\n    - ansible.builtin.include_tasks: %s\n' "$work/$1.yml" > "$work/$1-play.yml"
}
for r in stigman_stack site_portal_stack vmware_ask_stack; do tasks $r; done

have() { : > "$work/images/$(printf '%s' "$1" | tr '/:' '__')"; }
clear_images() { find "$work/images" -type f -delete; : > "$work/calls"; }

# run NAME ROLE EXPECT(pass|fail) [ansible args...] -- TEXT... (a TEXT starting with ! must NOT be in the output)
run() {
    local tname="$1" role="$2" want="$3"; shift 3
    local args=() texts=() seen=0 a
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && PATH="$work/bin:$PATH" ANSIBLE_FILTER_PLUGINS="$repo/plugins/filter" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
        ANSIBLE_DEPRECATION_WARNINGS=False "$ap" "$work/$role-play.yml" "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if [ "$want" = pass ] && [ $rc -ne 0 ]; then echo "FAIL - $tname: exit $rc"; grep -E 'fatal|ERROR' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1; fi
    if [ "$want" = fail ] && [ $rc -eq 0 ]; then echo "FAIL - $tname: the job passed"; fails=$((fails + 1)); return 1; fi
    local t
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            ! grep -qF -- "${t:1}" "$work/out.txt" || { echo "FAIL - $tname: output has: ${t:1}"; fails=$((fails + 1)); return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E 'fatal|ERROR' "$work/out.txt" | head -5 | cut -c1-300; fails=$((fails + 1)); return 1; }
        fi
    done
    echo "ok   - $tname"
}
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }
pulls() { grep -c '^pull ' "$work/calls"; }

S_MY="docker.io/library/mysql:8.4"; S_API="docker.io/nuwcdivnpt/stig-manager:1.6.17"; S_NG="docker.io/library/nginx:stable"
echo "{\"stigman_images\": {\"mysql\": \"$S_MY\", \"api\": \"$S_API\", \"nginx\": \"$S_NG\"}}" > "$work/imgs.json"
SV=(-e stigman_scope=system -e "@$work/imgs.json")

# ---- STIG Manager's stack
clear_images; have "$S_MY"; have "$S_API"; have "$S_NG"
run "stigman: all three images there" stigman_stack pass "${SV[@]}" -e stigman_pull_images=false -- '!STOPPED'
check "  ...nothing pulled" "$(pulls)" "0"

clear_images; have "$S_MY"
run "stigman: api and nginx missing - the job stops, naming both" stigman_stack fail "${SV[@]}" -e stigman_pull_images=false \
    -- "STOPPED: 2 images are not on this host. Nothing was changed." "$S_API" "(setting: stigman_images.api)" "$S_NG" "(setting: stigman_images.nginx)" \
       "podman save -o stig-manager-1.6.17.tar $S_API" "podman load -i nginx-stable.tar" "as root:" "podman tag <the name podman load printed> $S_API" \
       "stigman_pull_images: true" '!(setting: stigman_images.mysql)'
check "  ...nothing pulled" "$(pulls)" "0"

run "stigman: rootless says whose account loads them" stigman_stack fail -e "@$work/imgs.json" -e stigman_scope=user -e stigman_pull_images=false \
    -- "the account these units run as"

clear_images
run "stigman: pull allowed - a missing image is pulled (shown as changed)" stigman_stack pass "${SV[@]}" -e stigman_pull_images=true \
    -- '!STOPPED' "changed: [localhost] => (item=stigman_images.mysql)"
check "  ...three pulls" "$(pulls)" "3"

clear_images
FAKE_PULL_FAIL=1 run "stigman: pull allowed but podman refuses - the job stops, naming them" stigman_stack fail "${SV[@]}" -e stigman_pull_images=true \
    -- "STOPPED: 3 images are not on this host and could not be pulled" "(setting: stigman_images.mysql)" \
       "stigman_pull_images is true, so the job tried to pull" \
       "podman pull $S_MY failed: Error: initializing source docker://$S_MY: pinging container registry: connection refused" \
       "podman pull $S_NG failed: " "podman load -i mysql-8.4.tar" '!set stigman_pull_images: true' '!The units never pull (Pull is never), so' \
       '!changed: [localhost] => (item=stigman_images.mysql)'

clear_images
run "stigman: a dry run with pull allowed pulls nothing and does not stop" stigman_stack pass --check "${SV[@]}" -e stigman_pull_images=true -- '!STOPPED'
check "  ...nothing pulled" "$(pulls)" "0"
run "stigman: a dry run says the same as a real run when pull is off" stigman_stack fail --check "${SV[@]}" -e stigman_pull_images=false -- "STOPPED: 3 images are not on this host"

# ---- the portal
PV=(-e portal_scope=system -e portal_image=aap.example.mil/site-ee-all:1 -e portal_nginx_image=docker.io/library/nginx:stable)
clear_images
run "portal: the image missing" site_portal_stack fail "${PV[@]}" -e portal_own_nginx=false -e portal_pull_images=false \
    -- "STOPPED: the image aap.example.mil/site-ee-all:1 is not on this host. Nothing was changed." "(setting: portal_image)" \
       "podman save -o site-ee-all-1.tar aap.example.mil/site-ee-all:1" "portal_pull_images: true" '!portal_nginx_image'
have aap.example.mil/site-ee-all:1
run "portal: its nginx image is not needed without portal_own_nginx" site_portal_stack pass "${PV[@]}" -e portal_own_nginx=false -e portal_pull_images=false -- '!STOPPED'
run "portal: with portal_own_nginx its nginx image is needed" site_portal_stack fail "${PV[@]}" -e portal_own_nginx=true -e portal_pull_images=false \
    -- "(setting: portal_nginx_image)"
have docker.io/library/nginx:stable
run "portal: both there" site_portal_stack pass "${PV[@]}" -e portal_own_nginx=true -e portal_pull_images=false -- '!STOPPED'
check "  ...nothing pulled" "$(pulls)" "0"
clear_images
run "portal: pull allowed" site_portal_stack pass "${PV[@]}" -e portal_own_nginx=true -e portal_pull_images=true -- '!STOPPED'
check "  ...two pulls" "$(pulls)" "2"
clear_images
FAKE_PULL_FAIL=1 run "portal: pull allowed but refused - says the pull failed, no hint to set it" site_portal_stack fail "${PV[@]}" -e portal_own_nginx=false -e portal_pull_images=true \
    -- "and could not be pulled" "podman pull aap.example.mil/site-ee-all:1 failed: Error: initializing source" '!set portal_pull_images: true'

# ---- VMware ask
AV=(-e vmware_ask_scope=system -e vmware_ask_image=aap.example.mil/site-ee-all:1)
clear_images
run "vmware ask: the image missing" vmware_ask_stack fail "${AV[@]}" -e vmware_ask_pull_images=false \
    -- "(setting: vmware_ask_image)" "vmware_ask_pull_images: true"
FAKE_PULL_FAIL=1 run "vmware ask: pull allowed but refused" vmware_ask_stack fail "${AV[@]}" -e vmware_ask_pull_images=true \
    -- "podman pull aap.example.mil/site-ee-all:1 failed: " '!set vmware_ask_pull_images: true'
have aap.example.mil/site-ee-all:1
run "vmware ask: there" vmware_ask_stack pass "${AV[@]}" -e vmware_ask_pull_images=false -- '!STOPPED'

[ $fails -eq 0 ] && echo "all image preflight scenarios passed" || { echo "$fails failed"; exit 1; }
