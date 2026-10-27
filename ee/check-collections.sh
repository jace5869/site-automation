#!/usr/bin/env bash
# Does your automation hub have every collection an EE asks for? The build stops at the first one it cannot find;
# this checks each collection of the folder's requirements.yml at once, as the build does: one the base EE already
# has, in a version that fits, is kept (the hub is not asked); the others are asked of the hub - with the base EE's
# own ansible-galaxy and the folder's ansible.cfg - and the missing ones listed. Run it in the folder that holds
# windows/, network/ and all-in-one/:
#   bash check-collections.sh all-in-one
#   INSECURE=1 bash check-collections.sh all-in-one           # behind TLS inspection: no certificate checked
#   FETCH=1 INSECURE=1 bash check-collections.sh all-in-one   # and the missing ones downloaded from Galaxy (no
#       login), the newest version that fits, into collections-to-upload/ - for AAP: Automation Content > Namespaces >
#       the collection's namespace > Upload collection, then Collection Approvals > Approve (docs/EXECUTION_ENVIRONMENT.md,
#       chapter 2, step 4)
#   bash check-collections.sh all-in-one --list               # only the list it asks for (name:version)
# The hub token: it asks for it (nothing shows), or takes TOKEN from the environment. It is never written to a file
# or shown.
set -euo pipefail
cd "$(dirname "$0")"

folder=${1:-}
case "$folder" in
  windows|network|all-in-one) ;;
  *) echo "usage: bash check-collections.sh windows|network|all-in-one [--list]" >&2; exit 2 ;;
esac
# name:version of each collection in requirements.yml ("*" when it has no version: line)
reqs=$(awk '/^  - name:/ {if (n) print n ":" v; n = $3; v = "*"}
            /^    version:/ {v = $2; gsub(/"/, "", v)}
            END {if (n) print n ":" v}' "$folder/requirements.yml")
if [ "${2:-}" = --list ]; then echo "$reqs"; exit 0; fi

cfg=$folder/ansible.cfg
[ -f "$cfg" ] || { echo "no $cfg: cp $folder/ansible.cfg.example $cfg and put your hub's address in it (chapter 2, step 4)" >&2; exit 1; }
base=$(awk '/base_image:/ {b = 1} b && /name:/ {print $2; exit}' "$folder/execution-environment.yml")
podman image exists "$base" || { echo "the base image $base is not on this machine (chapter 2, step 3)" >&2; exit 1; }
if [ -z "${TOKEN:-}" ]; then
  read -rsp "Your automation hub token (chapter 2, step 5; nothing shows): " TOKEN || TOKEN=; echo
fi
[ -n "${TOKEN:-}" ] || { echo "no hub token (chapter 2, step 5)" >&2; exit 1; }
export TOKEN

work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
cp "$cfg" "$work/ansible.cfg"; printf '%s\n' "$reqs" > "$work/reqs.txt"
out=$work
if [ -n "${FETCH:-}" ]; then out=$PWD/collections-to-upload; mkdir -p "$out"; fi

# in the base EE, as root of the container (= you: your files, readable by you only, are readable there)
inner=$(cat <<'INNER'
export ANSIBLE_CONFIG=/ee/ansible.cfg ANSIBLE_GALAXY_SERVER_RH_CERTIFIED_TOKEN=$TOKEN \
       ANSIBLE_GALAXY_SERVER_COMMUNITY_TOKEN=$TOKEN ANSIBLE_GALAXY_SERVER_PUBLISHED_TOKEN=$TOKEN
c=(); [ -z "${INSECURE:-}" ] || c=(--ignore-certs)
core=$(ansible --version | sed -n '1s/.*core \([0-9.]*\).*/\1/p')
py=$(head -1 "$(command -v ansible)" | sed -e 's/^#! *//' -e 's/ .*//')
fits() {  # a downloaded collection's requires_ansible, and whether this ansible-core is in it
  "$py" - "$1" "$core" <<'PY'
import sys, tarfile, yaml
from packaging.specifiers import SpecifierSet
with tarfile.open(sys.argv[1]) as t:
    try:
        ra = (yaml.safe_load(t.extractfile("meta/runtime.yml")) or {}).get("requires_ansible") or ""
    except KeyError:
        ra = ""
ok = not ra or SpecifierSet(ra).contains(sys.argv[2])
print("needs ansible-core %s%s" % (ra or "(any)", "" if ok else " - NOT " + sys.argv[2] + ": find an older one"))
PY
}
got() { sed -n "s/^Collection '\(.*\)' was downloaded successfully.*/\1/p" <<< "$1" | head -1; }
# the base EE's collections, where the build installs: one that fits is kept, the hub is not asked for it
ansible-galaxy collection list --format json -p /usr/share/ansible/collections 2>/dev/null > /tmp/have.json || echo '{}' > /tmp/have.json
in_base() {  # the base EE's version of collection $1 when it fits $2 ("*" = any version)
  "$py" - "$1" "$2" <<'PY'
import json, re, sys
from packaging.specifiers import SpecifierSet
from packaging.version import Version
name, want = sys.argv[1], sys.argv[2]
for path, cols in json.load(open("/tmp/have.json")).items():
    v = (cols.get(name) or {}).get("version") if path.rstrip("/").endswith("/usr/share/ansible/collections/ansible_collections") else None
    if v:
        spec = ",".join(s if re.match(r"[<>=!~]", s.strip()) else "==" + s.strip() for s in want.split(","))
        if want == "*" or v == "*" or SpecifierSet(spec).contains(Version(v)):
            print(v)
PY
}
n=0; missing=0
echo "== The base EE, then your hub (ansible.cfg), for ansible-core $core:"
while IFS= read -r r; do
  [ -n "$r" ] || continue
  n=$((n + 1)); name=${r%%:*}; want=${r#*:}
  if v=$(in_base "$name" "$want" 2>/dev/null) && [ -n "$v" ]; then
    printf '   ok       %-28s %s (the base EE has it)\n' "$name" "$v"
    continue
  fi
  if log=$(ansible-galaxy collection download --no-deps "${c[@]}" -p /tmp/hub "$r" 2>&1); then
    printf '   ok       %-28s %s\n' "$name" "$(got "$log" | sed 's/.*://')"
    continue
  fi
  if ! grep -q 'Could not satisfy' <<< "$log"; then
    echo "   STOP     $name: $(grep -m1 '^ERROR!' <<< "$log" | sed -e 's/^ERROR! //' -e 's/\. <urlopen.*//' | cut -c1-220)"
    echo "   the hub did not answer as it should: its address in ansible.cfg (chapter 2, step 4), the token (step 5)$([ -n "${INSECURE:-}" ] || echo ', or INSECURE=1 behind TLS inspection')"
    exit 3
  fi
  missing=$((missing + 1))
  printf '   MISSING  %-28s %s\n' "$name" "$([ "$want" = '*' ] && echo 'not on your hub' || echo "no version $want on your hub")"
  [ -n "${FETCH:-}" ] || continue
  if glog=$(ansible-galaxy collection download --no-deps "${c[@]}" -s https://galaxy.ansible.com/ -p /out "$r" 2>&1); then
    f=$(got "$glog"); tgz=/out/${name/./-}-${f#*:}.tar.gz
    echo "            from Galaxy: collections-to-upload/${tgz#/out/} ($(fits "$tgz"))"
  elif grep -q 'Could not satisfy' <<< "$glog"; then
    echo "            not on Galaxy either$([[ $name != redhat.* ]] || echo " - a Red Hat collection: your hub's rh-certified repository syncs it")"
    echo "            or leave it out: its line out of $FOLDER/requirements.yml and $FOLDER/check-list.txt (chapter 2, step 4)"
  else
    echo "            Galaxy did not answer: $(grep -m1 '^ERROR!' <<< "$glog" | sed 's/^ERROR! //' | cut -c1-160)"
  fi
done < /ee/reqs.txt
rm -f /out/requirements.yml
if [ "$missing" = 0 ]; then echo "== all $n in the base EE or on your hub: build"; exit 0; fi
echo "== $missing of $n not on your hub$([ -n "${FETCH:-}" ] || echo ' - FETCH=1 downloads them from Galaxy')."
echo "   Upload each into AAP: Automation Content > Namespaces > the namespace its name begins with (Create namespace"
echo "   if it is not there) > Upload collection; then Collection Approvals > Approve. Then run this again."
exit 1
INNER
)
podman run --rm --user 0 --network=host --entrypoint bash -e TOKEN -e INSECURE -e FETCH -e "FOLDER=$folder" \
  -v "$work:/ee:Z" -v "$out:/out:Z" "$base" -c "$inner" 2> >(grep -v 'Failed to mount subscriptions' >&2)
