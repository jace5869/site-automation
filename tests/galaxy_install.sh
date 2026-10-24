#!/usr/bin/env bash
# CI only: ansible-galaxy collection install, tried three times. Galaxy still failing (an outage: its downloads
# answer 500, as on 2026-10-24), each collection is installed from its source on GitHub instead
# (ansible-collections/<name>, its newest release tag) - the same collection the tests would get from Galaxy.
#   bash tests/galaxy_install.sh -r tests/vmware/requirements.yml
#   bash tests/galaxy_install.sh cisco.ios ansible.netcommon
set -u
for i in 1 2 3; do
    ansible-galaxy collection install "$@" && exit 0
    echo "Galaxy failed (attempt $i of 3)"
    sleep 15
done
if [ "$1" = -r ]; then
    mapfile -t names < <(python -c 'import sys, yaml; [print(c["name"]) for c in yaml.safe_load(open(sys.argv[1]))["collections"]]' "$2")
else
    names=("$@")
fi
for n in "${names[@]}"; do
    tag="$(git ls-remote --tags --refs --sort=-v:refname "https://github.com/ansible-collections/$n.git" \
           | sed 's#.*refs/tags/##' | grep -E '^v?[0-9]+\.[0-9]+\.[0-9]+$' | head -1)"
    [ -n "$tag" ] || { echo "no release tag for $n on GitHub"; exit 1; }
    echo "Galaxy is down: $n $tag from https://github.com/ansible-collections/$n"
    ansible-galaxy collection install --force "git+https://github.com/ansible-collections/$n.git,$tag" || exit 1
done
