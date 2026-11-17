#!/usr/bin/env bash
# 1.70.3: the real roles, rootless (stigman_scope / portal_scope: user), real podman, with images that are not on this
# host (an air-gapped host has no registry to pull from): the job stops within seconds, nothing is written (no Quadlet
# file, no folder, no secret), the message names each image and its setting and has the load steps - and no unit ever
# tried to pull. Then the same roles with the image tagged locally pass the image step.
#   bash tests/airgap/run_missing_image_test.sh        (ANSIBLE_PLAYBOOK=... to pick one; needs podman)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
command -v podman >/dev/null || { echo "SKIP - no podman here"; exit 0; }
work="$(mktemp -d)"
tag="localhost/site-airgap-test-$$"
cleanup() { podman rmi -f "$tag-present:1" >/dev/null 2>&1; rm -rf "${work:?}"; }
trap cleanup EXIT
fails=0
home="$work/home"; mkdir -p "$home" "$work/tls"
printf 'x' > "$work/tls/c.pem"; printf 'x' > "$work/tls/k.pem"

cat > "$work/play.yml" <<EOF
- hosts: localhost
  connection: local
  gather_facts: false
  vars:
    ansible_user_dir: "$home"
    ansible_check_mode: false
  roles:
    - role: "{{ the_role }}"
EOF
# stand-in image: any local image, tagged with the name the setting has (as a loaded image is)
present() { podman image exists "$tag-present:1" || { local img; img="$(podman images -q 2>/dev/null | head -1)"; [ -n "$img" ] && podman tag "$img" "$tag-present:1" >/dev/null 2>&1; }; }

run() {
    local tname="$1"; shift
    (cd "$repo" && ANSIBLE_FILTER_PLUGINS="$repo/plugins/filter" ANSIBLE_ROLES_PATH="$repo/roles" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 \
        ANSIBLE_DEPRECATION_WARNINGS=False STIGMAN_MYSQL_ROOT_PASSWORD=aaaaaaaaaaaa1 STIGMAN_DB_PASSWORD=bbbbbbbbbbbb2 \
        TLS_CERT_FILE="$work/tls/c.pem" TLS_KEY_FILE="$work/tls/k.pem" \
        "$ap" "$work/play.yml" "$@") > "$work/out.txt" 2>&1
    echo $?
}
text() { grep -qF -- "$2" "$work/out.txt" && echo "ok   - $1" || { echo "FAIL - $1: output lacks: $2"; grep -E 'fatal|ERROR' "$work/out.txt" | head -4 | cut -c1-300; fails=$((fails + 1)); }; }
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }
secrets() { podman secret ls --format '{{.Name}}' 2>/dev/null | sort; }
new_secrets() { comm -13 "$work/secrets-before" <(secrets) | wc -l; }   # only what this test's own runs made
secrets > "$work/secrets-before"
written() { find "$home" -mindepth 1 | wc -l; }

# ---- STIG Manager's stack, three images missing
t0=$(date +%s)
rc=$(run "x" -e the_role=stigman_stack -e stigman_scope=user -e "{\"stigman_images\": {\"mysql\": \"$tag-absent/mysql:8.4\", \"api\": \"$tag-absent/api:1\", \"nginx\": \"$tag-absent/nginx:1\"}}")
t1=$(date +%s)
check "stigman: missing images stop the job" "$([ "$rc" -ne 0 ] && echo stopped || echo "passed (rc $rc)")" "stopped"
check "  ...within seconds (not the units' pull timeout)" "$([ $((t1 - t0)) -lt 60 ] && echo quick || echo "slow ($((t1 - t0)) s)")" "quick"
text "  ...names the images and the settings" "(setting: stigman_images.mysql)"
text "  ...and the api's" "$tag-absent/api:1   (setting: stigman_images.api)"
text "  ...with the steps" "podman load -i mysql-8.4.tar"
check "  ...nothing written" "$(written)" "0"
check "  ...no secret made" "$(new_secrets)" "0"

# ---- the portal
rc=$(run "x" -e the_role=site_portal_stack -e portal_scope=user -e portal_aap_url=https://aap.example.mil -e portal_aap_token=tttttttttttt \
        -e portal_network=site-net -e portal_reports_dir=/srv/reports -e "portal_image=$tag-absent/ee:1")
check "portal: a missing image stops the job" "$([ "$rc" -ne 0 ] && echo stopped || echo "passed (rc $rc)")" "stopped"
text "  ...names the image and the setting" "$tag-absent/ee:1   (setting: portal_image)"
check "  ...nothing written" "$(written)" "0"
check "  ...no secret made" "$(new_secrets)" "0"

# ---- the image there: the image step passes (the job goes on and stops at a later step, not at this one)
if present; then
    rc=$(run "x" -e the_role=site_portal_stack -e portal_scope=user -e portal_aap_url=https://aap.example.mil -e portal_aap_token=tttttttttttt \
            -e portal_network=site-net -e portal_reports_dir=/srv/reports -e "portal_image=$tag-present:1")
    check "portal: the image there - not stopped at the image step" "$(grep -c 'is not on this host' "$work/out.txt")" "0"
    text "  ...the image step ran" "Images | every image is on this host"
fi

present || echo "note - no local image to tag: the image-there case was not run"
[ $fails -eq 0 ] && echo "all missing-image scenarios passed" || { echo "$fails failed"; exit 1; }
