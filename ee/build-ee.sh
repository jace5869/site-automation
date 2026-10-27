#!/usr/bin/env bash
# Build one execution environment of this folder in one go - the steps of docs/EXECUTION_ENVIRONMENT.md: first
# the checks of chapter 2 (it stops at the first problem and says which step fixes it), then ansible-builder and
# podman build with your hub token, ee-check, and - when you ask - the image pushed to your hub, or saved as a
# file to load on the execution nodes. Run it on the build machine, in the folder that holds windows/, network/
# and all-in-one/:
#   bash build-ee.sh all-in-one 2                         # builds localhost/site-ee-all:2
#   PUSH=aap.example.mil bash build-ee.sh all-in-one 2    # ... and pushes it: aap.example.mil/site-ee-all:2
#   SAVE=1 bash build-ee.sh all-in-one 2                  # ... and saves it: site-ee-all-2.tar (no registry)
#   PIP_INDEX_URL=https://nexus.example.mil/repository/pypi/simple bash build-ee.sh ...   # your PyPI mirror
#   INSECURE=1 bash build-ee.sh all-in-one 2              # behind TLS inspection: no certificate checked - pip
#       --trusted-host (PyPI's hosts, or your mirror's), ansible-galaxy --ignore-certs, podman --tls-verify=false
#   PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org" bash build-ee.sh ...   # only pip's (INSECURE=1 sets it)
#   sudo env INSECURE=1 bash build-ee.sh all-in-one 2     # as root: when podman cannot give the build your RHEL
#       subscription as you (it says so) - the base image in root's podman first: podman save ... | sudo podman load
# The hub token: it asks for it (nothing shows as you paste), or takes TOKEN from the environment. It is never
# written to a file or shown.
set -euo pipefail
cd "$(dirname "$0")"

folder=${1:-}
tag=${2:-1}
case "$folder" in
  windows)    name=site-ee-windows; aap="Windows, AD and SQL EE" ;;
  network)    name=site-ee-network; aap="Network EE (Cisco and Palo Alto)" ;;
  all-in-one) name=site-ee-all;     aap="All-in-one EE" ;;
  *) echo "usage: bash build-ee.sh windows|network|all-in-one [tag]   (e.g. bash build-ee.sh all-in-one 2)" >&2; exit 2 ;;
esac
[[ "$tag" =~ ^[A-Za-z0-9._-]+$ ]] || { echo "the tag is a version such as 2 or 2.1, not '$tag'" >&2; exit 2; }
image="localhost/$name:$tag"
curl_k=(); tls=()
if [ -n "${INSECURE:-}" ]; then
  curl_k=(-k); tls=(--tls-verify=false)
  if [ -z "${PIP_TRUSTED_HOST:-}" ]; then
    if [ -n "${PIP_INDEX_URL:-}" ]; then h=${PIP_INDEX_URL#*://}; h=${h%%/*}; PIP_TRUSTED_HOST=${h##*@}
    else PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org"; fi
  fi
fi
step() { echo; echo "== $1"; }
ok()   { echo "   ok    $1"; }
note() { echo "   note  $1"; }
stop() { echo "   STOP  $1" >&2; exit 1; }
err=$(mktemp); trap 'rm -f "$err"' EXIT

step "Checks (docs/EXECUTION_ENVIRONMENT.md, chapter 2)"
for t in podman ansible-builder curl; do
  command -v "$t" >/dev/null || stop "$t is not installed: sudo dnf install -y podman ansible-builder (chapter 2, step 1)"
done
ok "podman, ansible-builder"
yml=$folder/execution-environment.yml
[ -f "$yml" ] || stop "no $yml here: run it in the folder with windows/, network/ and all-in-one/ (chapter 2, step 2)"
cfg=$folder/ansible.cfg
[ -f "$cfg" ] || stop "no $cfg: cp $folder/ansible.cfg.example $cfg and put your hub's address in it (chapter 2, step 4)"
! grep -q 'aap\.example\.mil' "$cfg" || stop "$cfg still says aap.example.mil: put your hub's address in it (chapter 2, step 4)"
hub=$(sed -n 's/^url *= *//p' "$cfg" | head -1)
ok "$cfg: the collections from $hub"

base=$(awk '/base_image:/ {b = 1} b && /name:/ {print $2; exit}' "$yml")
want=$(awk '/python_path:/ {print $2; exit}' "$yml")
if ! podman image exists "$base"; then
  case "$base" in
    localhost/*) stop "the base image $base is not on this machine: podman pull or podman load it, then podman tag it $base (chapter 2, step 3)" ;;
    *) podman pull -q "${tls[@]}" "$base" >/dev/null || stop "could not pull the base image $base: podman login ${base%%/*} first (chapter 2, step 3)" ;;
  esac
fi
# past the base's entrypoint: in some base images the user they run as cannot read it - the EE built here gets a
# readable one (execution-environment.yml)
got=$(podman run --rm --entrypoint bash "$base" -c 'head -1 "$(command -v ansible)"' 2>"$err" | sed -e 's/^#! *//' -e 's/ .*//') || true
[ -n "$got" ] || { sed 's/^/   /' "$err" >&2; stop "could not see which Python Ansible runs on in $base (chapter 2, step 3)"; }
if [ -n "$want" ] && [ "$got" != "$want" ]; then
  stop "Ansible in $base runs on $got, $yml says $want: sed -i 's|$want|$got|' */execution-environment.yml (chapter 2, step 3)"
fi
ok "the base image $base, its Ansible on $got"
case "$(podman run --rm "$base" true 2>&1 || true)" in
  *"entrypoint: Permission denied"*)
    note "$base's entrypoint cannot be read by the user EEs run as: the EE built here gets a readable one" ;;
esac
if grep -q 'Failed to mount subscriptions' "$err" && [ "$(id -u)" != 0 ]; then
  note "podman cannot give the build this machine's RHEL subscription as $(id -un) (WARN ... Failed to mount subscriptions):"
  note "the system packages can come only from $base's own repositories. If the build stops at 'No match for argument'"
  note "or a repository error, build as root: podman save $base | sudo podman load, then sudo env ${INSECURE:+INSECURE=1 }bash build-ee.sh $folder $tag (chapter 8)"
fi

n=$(find "$folder/ca" -maxdepth 1 -name '*.pem' 2>/dev/null | wc -l)
if [ "$n" -gt 0 ]; then ok "$n CA certificate(s) in $folder/ca"
else note "no CA certificate in $folder/ca: the EE will not trust your CA (chapter 2, step 6)"; fi

# curl's own words in the STOP: a certificate (TLS inspection), a name that does not resolve, no way out
tls_hint=$([ -n "${INSECURE:-}" ] || echo ' - or, behind TLS inspection, INSECURE=1')
said() { local w=${why#curl: }; printf '%s' "${w%%$'\n'*}"; }   # its first line
pypi=${PIP_INDEX_URL:-https://pypi.org/simple}
why=$(curl -sSfI "${curl_k[@]}" --max-time 20 "${pypi%/}/pip/" 2>&1 >/dev/null) \
  || stop "cannot reach ${pypi%/}/pip/ ($(said)): set PIP_INDEX_URL to your PyPI mirror (chapter 2, step 7)$tls_hint"
ok "PyPI: $pypi"
[ -z "$hub" ] || why=$(curl -sSI "${curl_k[@]}" --max-time 20 "$hub" 2>&1 >/dev/null) \
  || stop "cannot reach your hub at $hub ($(said)) (chapter 2, step 4)$tls_hint"
[ -z "$hub" ] || ok "the hub answers"
if [ -n "${INSECURE:-}" ]; then
  note "INSECURE=1: no certificate is checked - pip (trusted: $PIP_TRUSTED_HOST), ansible-galaxy (--ignore-certs), podman (--tls-verify=false)"
elif [ -n "${PIP_TRUSTED_HOST:-}" ]; then
  note "pip does not check the certificate of: $PIP_TRUSTED_HOST"
fi

if [ -z "${TOKEN:-}" ]; then
  read -rsp "   Your automation hub token (chapter 2, step 5; nothing shows): " TOKEN || TOKEN=; echo
fi
[ -n "${TOKEN:-}" ] || stop "no hub token (chapter 2, step 5)"
ok "the hub token (${#TOKEN} characters)"

free=$(df -Pk "$(podman info --format '{{.Store.GraphRoot}}' 2>/dev/null || echo "$HOME")" | awk 'NR == 2 {print int($4 / 1048576)}')
if [ "${free:-0}" -lt 15 ]; then note "only ${free:-?} GB free for podman's images: the build needs about 15"; else ok "${free} GB free"; fi

step "Build $image (ansible-builder, then podman: 10 to 25 minutes)"
args=(--build-arg "ANSIBLE_GALAXY_SERVER_RH_CERTIFIED_TOKEN=$TOKEN"
      --build-arg "ANSIBLE_GALAXY_SERVER_COMMUNITY_TOKEN=$TOKEN"
      --build-arg "ANSIBLE_GALAXY_SERVER_PUBLISHED_TOKEN=$TOKEN")
[ -z "${PIP_INDEX_URL:-}" ] || args+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
[ -z "${PIP_TRUSTED_HOST:-}" ] || args+=(--build-arg "PIP_TRUSTED_HOST=$PIP_TRUSTED_HOST")
[ -z "${INSECURE:-}" ] || args+=(--build-arg ANSIBLE_GALAXY_CLI_COLLECTION_OPTS=--ignore-certs
                                 --build-arg ANSIBLE_GALAXY_CLI_ROLE_OPTS=--ignore-certs)
(cd "$folder" && ansible-builder create -f execution-environment.yml -c context) || stop "ansible-builder create failed: the error above (chapter 8)"
(cd "$folder" && podman build --network=host "${tls[@]}" "${args[@]}" -f context/Containerfile -t "$image" context) \
  || stop "the build failed: the error above (chapter 8, When something goes wrong; 'Could not satisfy': bash check-collections.sh $folder lists every collection your hub lacks)"
ok "built $image"

step "ee-check"
podman run --rm "$image" ee-check || stop "something is missing in $image: the lines with 'no' above (chapter 8)"

if [ -n "${PUSH:-}" ]; then
  step "Push to $PUSH (chapter 6, step 1)"
  podman login --get-login "$PUSH" >/dev/null 2>&1 || podman login "${tls[@]}" "$PUSH"
  podman tag "$image" "$PUSH/$name:$tag"
  podman push "${tls[@]}" "$PUSH/$name:$tag" || stop "the push failed: the error above (chapter 6, step 1)"
  ok "pushed $PUSH/$name:$tag"
  where="$PUSH/$name:$tag"; pull="Only pull the image if not present before running"
elif [ -n "${SAVE:-}" ]; then
  step "Save as a file (chapter 6, step 1, without a registry)"
  podman save -o "$name-$tag.tar" "$image"
  ok "saved $name-$tag.tar: on each execution node, as the user AAP runs jobs as: podman load -i $name-$tag.tar"
  where=$image; pull="Never pull container before running"
else
  where=$image; pull="(push it or save it first: PUSH=your-hub or SAVE=1, chapter 6, step 1)"
fi

step "Done. In AAP: Automation Execution > Infrastructure > Execution Environments > Create (chapter 6, step 2)"
echo "   Name:  $aap"
echo "   Image: $where"
echo "   Pull:  $pull"
