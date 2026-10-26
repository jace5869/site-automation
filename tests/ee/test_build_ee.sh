#!/usr/bin/env bash
# ee/build-ee.sh against fake podman / ansible-builder / curl (they record what they were asked; nothing is built):
# each check stops with the step of the guide that fixes it, the build gets the hub token as build arguments and
# never prints it, ee-check decides, and PUSH / SAVE do what they say.      bash tests/ee/test_build_ee.sh
set -u
repo="$(cd "$(dirname "$0")/../.." && pwd)"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
fail=0
ok()  { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fail=$((fail + 1)); sed 's/^/       /' "$work/out.txt" | tail -8; }
mkdir -p "$work/bin" "$work/graph"
cat > "$work/bin/podman" <<'EOF'
#!/usr/bin/env bash
echo "podman $*" >> "$FAKE_LOG"
case "$1 $2" in
  "image exists") [ -n "${FAKE_BASE:-}" ] ;;
  "run --rm")
    if [ "${*: -1}" = ee-check ]; then
      if [ -n "${FAKE_CHECK_FAIL:-}" ]; then echo "  no   smbprotocol"; echo "== 1 MISSING"; exit 1; fi
      echo "== ALL YES"
    else echo "#!${FAKE_PY:-/usr/bin/python3.12} -s"; fi ;;
  "info --format") echo "$FAKE_GRAPH" ;;
  "login --get-login") echo someone ;;
  "build --network=host") echo "Successfully tagged ${*: -2:1}" ;;
  *) : ;;
esac
EOF
cat > "$work/bin/ansible-builder" <<'EOF'
#!/usr/bin/env bash
echo "ansible-builder $*" >> "$FAKE_LOG"; mkdir -p context; : > context/Containerfile
EOF
cat > "$work/bin/curl" <<'EOF'
#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"; [ -z "${FAKE_NO_PYPI:-}" ] || [[ "$*" != */pip/* ]]
EOF
chmod 755 "$work/bin/"*

fresh() {  # a copy of ee/ as the build machine has it, ansible.cfg with a real-looking hub
  rm -rf "$work/ee"; cp -a "$repo/ee" "$work/ee"
  for d in windows network all-in-one; do
    sed 's/aap\.example\.mil/hub.lab.test/' "$work/ee/$d/ansible.cfg.example" > "$work/ee/$d/ansible.cfg"
  done
  : > "$work/log"
}
run() {  # run [VAR=value ...] -- args: build-ee.sh with the fakes first on PATH
  local envs=(); while [ "$1" != -- ]; do envs+=("$1"); shift; done; shift
  env PATH="$work/bin:$PATH" FAKE_LOG="$work/log" FAKE_GRAPH="$work/graph" FAKE_BASE=1 "${envs[@]}" \
      bash "$work/ee/build-ee.sh" "$@" > "$work/out.txt" 2>&1 < /dev/null
  rc=$?
}
has() { grep -qF -- "$1" "${2:-$work/out.txt}"; }

fresh; run TOKEN=s3cr3t-tok -- all-in-one 2
[ "$rc" = 0 ] && has "== ALL YES" && has "Image: localhost/site-ee-all:2" && ok "builds, ee-check ALL YES, the AAP fields" || bad "the build (rc $rc)"
has "ansible-builder create -f execution-environment.yml -c context" "$work/log" && ok "  ... ansible-builder create" || bad "ansible-builder"
has "--build-arg ANSIBLE_GALAXY_SERVER_RH_CERTIFIED_TOKEN=s3cr3t-tok --build-arg ANSIBLE_GALAXY_SERVER_COMMUNITY_TOKEN=s3cr3t-tok --build-arg ANSIBLE_GALAXY_SERVER_PUBLISHED_TOKEN=s3cr3t-tok -f context/Containerfile -t localhost/site-ee-all:2 context" "$work/log" \
  && ok "  ... podman build with the three token build arguments and the tag" || bad "build arguments: $(grep 'podman build' "$work/log")"
has "s3cr3t-tok" && bad "the token is printed" || ok "  ... the token is never printed"
has "PIP_INDEX_URL" "$work/log" && bad "PIP_INDEX_URL passed without being set" || ok "  ... no PIP_INDEX_URL unless set"
has "podman push" "$work/log" && bad "pushed without PUSH" || ok "  ... nothing pushed without PUSH"
grep -q -- '--tls-verify\|ignore-certs\|PIP_TRUSTED_HOST\|curl -k\|curl -sfI -k' "$work/log" && bad "certificates not checked without INSECURE" || ok "  ... every certificate checked (no INSECURE)"

fresh; run TOKEN=t INSECURE=1 PUSH=hub.lab.test -- all-in-one 2
[ "$rc" = 0 ] && ok "INSECURE=1: builds" || bad "INSECURE (rc $rc)"
has "--build-arg PIP_TRUSTED_HOST=pypi.org files.pythonhosted.org" "$work/log" && ok "  ... pip trusts PyPI's two hosts" || bad "trusted hosts: $(grep 'podman build' "$work/log")"
has "--build-arg ANSIBLE_GALAXY_CLI_COLLECTION_OPTS=--ignore-certs --build-arg ANSIBLE_GALAXY_CLI_ROLE_OPTS=--ignore-certs" "$work/log" && ok "  ... ansible-galaxy --ignore-certs" || bad "ignore-certs"
has "podman build --network=host --tls-verify=false" "$work/log" && has "podman push --tls-verify=false hub.lab.test/site-ee-all:2" "$work/log" && ok "  ... podman build and push --tls-verify=false" || bad "tls-verify"
grep -q '^curl -sfI -k ' "$work/log" && grep -q '^curl -sI -k ' "$work/log" && ok "  ... curl -k for PyPI and the hub" || bad "curl -k: $(grep '^curl' "$work/log")"
has "INSECURE=1: no certificate is checked" && ok "  ... and it says so" || bad "INSECURE note"
fresh; run TOKEN=t INSECURE=1 PIP_INDEX_URL=https://reader:pw@mirror.lab.test:8443/pypi/simple -- all-in-one
has "--build-arg PIP_TRUSTED_HOST=mirror.lab.test:8443" "$work/log" && ok "INSECURE=1 with a mirror: pip trusts the mirror's host:port (no user, no path)" || bad "mirror trusted host: $(grep 'podman build' "$work/log")"
fresh; run TOKEN=t PIP_TRUSTED_HOST=pypi.example.test -- all-in-one
has "--build-arg PIP_TRUSTED_HOST=pypi.example.test" "$work/log" && ! has "ignore-certs" "$work/log" && ! has "--tls-verify" "$work/log" \
  && ok "PIP_TRUSTED_HOST alone: only pip's check is off" || bad "PIP_TRUSTED_HOST alone"

fresh; rm "$work/ee/all-in-one/ansible.cfg"; run TOKEN=t -- all-in-one
[ "$rc" = 1 ] && has "STOP  no all-in-one/ansible.cfg" && has "chapter 2, step 4" && ok "no ansible.cfg: stops (step 4)" || bad "no ansible.cfg (rc $rc)"
has "podman build" "$work/log" && bad "built anyway" || ok "  ... before any build"
fresh; cp "$work/ee/all-in-one/ansible.cfg.example" "$work/ee/all-in-one/ansible.cfg"; run TOKEN=t -- all-in-one
[ "$rc" = 1 ] && has "still says aap.example.mil" && ok "ansible.cfg still the example: stops (step 4)" || bad "example ansible.cfg (rc $rc)"
fresh; run TOKEN=t FAKE_BASE= -- all-in-one
[ "$rc" = 1 ] && has "the base image localhost/base-ee:1.0 is not on this machine" && has "chapter 2, step 3" && ok "no base-ee:1.0: stops (step 3)" || bad "no base (rc $rc)"
fresh; run TOKEN=t FAKE_PY=/usr/bin/python3.11 -- all-in-one
[ "$rc" = 1 ] && has "runs on /usr/bin/python3.11" && has "sed -i 's|/usr/bin/python3.12|/usr/bin/python3.11|' */execution-environment.yml" && ok "another Python in the base: stops with the sed (step 3)" || bad "python mismatch (rc $rc)"
fresh; run TOKEN=t FAKE_NO_PYPI=1 -- all-in-one
[ "$rc" = 1 ] && has "set PIP_INDEX_URL to your PyPI mirror" && ok "no PyPI: stops (step 7)" || bad "no PyPI (rc $rc)"
fresh; run TOKEN=t FAKE_NO_PYPI=1 PIP_INDEX_URL=https://mirror.lab.test/pypi/simple -- all-in-one
[ "$rc" = 1 ] && has "cannot reach https://mirror.lab.test/pypi/simple/pip/" && ok "a mirror that does not answer: stops" || bad "mirror (rc $rc)"
fresh; run TOKEN=t PIP_INDEX_URL=https://mirror.lab.test/pypi/simple -- all-in-one
[ "$rc" = 0 ] && has "--build-arg PIP_INDEX_URL=https://mirror.lab.test/pypi/simple" "$work/log" && ok "a PyPI mirror: passed to the build" || bad "mirror build arg (rc $rc)"
fresh; run TOKEN= -- all-in-one
[ "$rc" = 1 ] && has "no hub token" && ok "no token (none typed): stops (step 5)" || bad "no token (rc $rc)"
fresh; run TOKEN=t FAKE_CHECK_FAIL=1 -- all-in-one
[ "$rc" = 1 ] && has "something is missing in localhost/site-ee-all:1" && ok "ee-check finds something missing: stops" || bad "ee-check fail (rc $rc)"
fresh; run TOKEN=t PUSH=hub.lab.test -- all-in-one 3
[ "$rc" = 0 ] && has "podman tag localhost/site-ee-all:3 hub.lab.test/site-ee-all:3" "$work/log" && has "podman push hub.lab.test/site-ee-all:3" "$work/log" \
  && has "Image: hub.lab.test/site-ee-all:3" && ok "PUSH: tagged, pushed, the AAP fields with the hub's image" || bad "push (rc $rc)"
fresh; run TOKEN=t SAVE=1 -- windows 1
[ "$rc" = 0 ] && has "podman save -o site-ee-windows-1.tar localhost/site-ee-windows:1" "$work/log" && has "Never pull container before running" \
  && ok "SAVE: the windows EE saved as a file" || bad "save (rc $rc)"
fresh; run TOKEN=t -- bogus
[ "$rc" = 2 ] && has "usage: bash build-ee.sh windows|network|all-in-one" && ok "an unknown EE: the usage" || bad "usage (rc $rc)"
fresh; run TOKEN=t -- all-in-one '2;rm'
[ "$rc" = 2 ] && ok "a tag that is not a version: refused" || bad "bad tag (rc $rc)"

if [ "$fail" -gt 0 ]; then echo "$fail check(s) failed"; exit 1; fi
echo "all build-ee checks passed"
