#!/usr/bin/env bash
# CI only: ansible-galaxy collection install, tried three times. Galaxy still failing (an outage: its downloads
# answer 500, as on 2026-10-24), each collection is installed from its source on GitHub instead
# (ansible-collections/<name>, its newest release tag THAT MATCHES the version the requirements file asks for, e.g.
# vmware.vmware ">=2.11.0,<3.0.0") - with the collections it depends on (its galaxy.yml at that tag, their version
# specs honoured too), each from GitHub too: the same collections the tests would get from Galaxy.
#   bash tests/galaxy_install.sh -r tests/vmware/requirements.yml
#   bash tests/galaxy_install.sh cisco.ios ansible.netcommon
set -u
for i in 1 2 3; do
    ansible-galaxy collection install "$@" && exit 0
    echo "Galaxy failed (attempt $i of 3)"
    sleep 15
done
# todo: "name version-spec" pairs ('*' = any)
if [ "$1" = -r ]; then
    mapfile -t todo < <(python -c 'import sys, yaml
for c in yaml.safe_load(open(sys.argv[1]))["collections"]:
    c = {"name": c} if isinstance(c, str) else c
    print(c["name"], str(c.get("version") or "*").replace(" ", ""))' "$2")
else
    todo=()
    for n in "$@"; do todo+=("${n%%:*} $([ "${n#*:}" != "$n" ] && echo "${n#*:}" || echo '*')"); done
fi
done_=" "
while [ "${#todo[@]}" -gt 0 ]; do
    pair="${todo[0]}"; todo=("${todo[@]:1}")
    n="${pair%% *}"; spec="${pair#* }"
    case "$done_" in *" $n "*) continue ;; esac
    done_="$done_$n "
    # the newest release tag on GitHub that satisfies the spec (packaging comes with ansible-core)
    tag="$(git ls-remote --tags --refs "https://github.com/ansible-collections/$n.git" | sed 's#.*refs/tags/##' \
           | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | python -c '
import sys
from packaging.specifiers import SpecifierSet
from packaging.version import Version
spec = sys.argv[1]
want = SpecifierSet("" if spec in ("*", "") else spec)
ok = sorted((t.strip() for t in sys.stdin if t.strip() and Version(t.strip().lstrip("v")) in want),
            key=lambda t: Version(t.lstrip("v")), reverse=True)
print(ok[0] if ok else "")' "$spec")"
    [ -n "$tag" ] || { echo "no release tag of $n on GitHub satisfies $spec"; exit 1; }
    echo "::warning::Galaxy is down: $n $tag (wanted $spec) installed from https://github.com/ansible-collections/$n"
    ansible-galaxy collection install --force --no-deps "git+https://github.com/ansible-collections/$n.git,$tag" || exit 1
    # what it depends on, from its galaxy.yml at that tag, each with its version spec
    mapfile -t -O "${#todo[@]}" todo < <(curl -fsSL "https://raw.githubusercontent.com/ansible-collections/$n/$tag/galaxy.yml" \
        | python -c 'import sys, yaml
for d, v in ((yaml.safe_load(sys.stdin) or {}).get("dependencies") or {}).items():
    print(d, str(v or "*").replace(" ", ""))')
done
