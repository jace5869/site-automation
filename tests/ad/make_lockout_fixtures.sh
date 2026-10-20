#!/usr/bin/env bash
# Rebuild tests/ad/fixtures/lockout_*.json - what the account lockout scripts (roles/ad_lockout/files)
# answer for the made-up domain of tests/windows/run_lockout_scenario.ps1 - after changing a script or
# a scenario. tests/test_ad_lockout.py and tests/ad/run_lockout_test.sh build the report from these
# files. Needs pwsh.
#   bash tests/ad/make_lockout_fixtures.sh
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
mkdir -p "$here/fixtures"
run() {  # run SCRIPT SCENARIO
    pwsh -NoProfile -File "$repo/tests/windows/run_lockout_scenario.ps1" -Script "$repo/roles/ad_lockout/files/$1.ps1" -Scenario "$2" \
        | grep '^###SITE-JSON### ' | tail -n 1 | cut -c17- | python3 -m json.tool --sort-keys > "$here/fixtures/lockout_$2.json"
    echo "fixtures/lockout_$2.json"
}
for s in find_one find_upn find_all find_no_ad; do run lockout_find "$s"; done
for s in dc_pdc dc_bdc dc_quote; do run lockout_dc "$s"; done
for s in unlock_ok unlock_denied; do run lockout_unlock "$s"; done
for s in source_pc source_nobody; do run lockout_source "$s"; done
