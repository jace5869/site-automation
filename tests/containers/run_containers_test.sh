#!/usr/bin/env bash
# The containers Health check (roles/check_containers + roles/podman_discover) and Service watch
# (roles/service_watch) against fake podman / systemctl / runuser / ps (tests/containers/fakes.py):
# root and two users with a container of the SAME name, a stopped Quadlet whose container is gone,
# unhealthy (podman 3 and podman 4 JSON), a recent crash, old exits that must stay quiet, a user with
# storage but no /run/user/UID, restart loops, OOM kills, no podman at all; and for Service watch:
# the watched set from discovery, fixes keyed by owner, the rootless fix command, evidence for ACT,
# self-heal, the approved-fix job and its dry run. Runs nothing for real: every tool it touches is fake.
#   bash tests/containers/run_containers_test.sh      (ANSIBLE_PLAYBOOK=... to pick one)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
py="$(command -v python3)"
NOW=1790000000
fails=0
oks=0
cat > "$work/inv.yml" <<EOI
all:
  children:
    testhosts:
      hosts:
        host01.example.com: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $py}
EOI
sed 's/^    testhosts:$/    aap:/' "$work/inv.yml" > "$work/inv-aap.yml"
H="$work/h"

prepare() {  # prepare FIXTURE: a fresh fake host
    rm -rf "$H"; mkdir -p "$H"
    python3 "$here/fakes.py" setup "$here/fixtures/$1.json" "$H" >/dev/null || { echo "FAIL - setup $1"; exit 1; }
    printf '{}' > "$H/act.json"
    # SAFETY: every tool the roles may run to look or to fix must be the fake, or nothing runs
    # (a real podman / systemctl / runuser here would act on the machine running the test).
    local t
    for t in podman systemctl runuser ps journalctl id getent loginctl; do
        if [ "$(PATH="$H/bin:$PATH" command -v "$t")" != "$H/bin/$t" ] || [ "$(PATH="$H/bin:$PATH" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
            echo "ABORT - $t is not the fake; not running anything"; exit 1
        fi
    done
}

hooks() {  # the paths of the fake host (test hooks of the roles) as extra variables
    cat <<EOJ
{"target": "testhosts", "_pd_now": "$NOW", "_pd_subuid_file": "$H/subuid", "_pd_linger_dir": "$H/linger", "_pd_run_user_dir": "$H/run/user",
 "_pd_users_quadlet_dir": "$H/etc/containers/systemd/users", "_pd_system_unit_dir": "$H/etc/systemd/system",
 "podman_discover_quadlet_dirs": ["$H/etc/containers/systemd", "$H/usr/share/containers/systemd"],
 "_watch_run_user_dir": "$H/run/user", "_watch_users_quadlet_dir": "$H/etc/containers/systemd/users",
 "watch_quadlet_dirs": ["$H/etc/containers/systemd"], "watch_record_file": "$H/incidents.jsonl",
 "watch_record_syslog": false, "site_syslog": false, "watch_verify_delay": 0, "watch_verify_retries": 1,
 "act_triage_script_src": "$here/fake_act.py", "act_triage_dest_dir": "$H/act", "act_triage_poll": 1}
EOJ
}

run() {  # run PLAYBOOK [ansible-playbook args...] -> $work/out.txt, $rc
    local pb="$1"; shift
    hooks > "$work/hooks.json"
    (cd "$repo" && PATH="$H/bin:$PATH" FAKE_DIR="$H" FAKE_NOW="$NOW" FAKE_ACT_OUT="$H" FAKE_ACT_CONFIG="$H/act.json" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -i "$work/${INV:-inv.yml}" "$pb" -e "@$work/hooks.json" "$@") > "$work/out.txt" 2>&1
    rc=$?
}

ok() { echo "ok   - $1"; oks=$((oks + 1)); }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); [ -n "${2:-}" ] && tail -n "${2}" "$work/out.txt"; }
has() {  # has "label" "text" [file]: the output (or file) contains the text
    if grep -qF -- "$2" "${3:-$work/out.txt}"; then ok "$1"; else bad "$1 - missing: $2" 20; fi
}
hasnt() {
    if grep -qF -- "$2" "${3:-$work/out.txt}"; then bad "$1 - must not contain: $2" 20; else ok "$1"; fi
}
findings() { grep -o 'FINDINGS [^"]*' "$work/out.txt" | head -1 | sed 's/^FINDINGS *//'; }
expect() {  # expect "label" "sorted,ids"
    local got; got="$(findings)"
    if [ "$rc" -eq 0 ] && [ "$got" = "$2" ]; then ok "$1"; else bad "$1: exit $rc, findings [$got], expected [$2]" 30; fi
}
readonly_calls() {  # nothing but reads reached podman / systemctl
    if grep -E ' podman (start|stop|restart|rm|run|create|kill|pull|pod|system|volume|network)| systemctl( --user)? (start|stop|restart|reset-failed|daemon-reload|enable|disable)| loginctl (enable|disable)-linger' "$H/calls.log"; then
        bad "$1: a command that changes something was run"
    else ok "$1"; fi
}

# ---------------------------------------------------------------- the containers Health check
prepare fleet
run tests/containers/check.yml
ALL="containers:crashed:root/big,containers:crashed:root/crash,containers:down:alice/web,containers:down:root/app,containers:down:root/web2,containers:no-linger:dave,containers:no-linger:eve,containers:oom:root/big,containers:restarting:root/loop,containers:unhealthy:bob/web,containers:unhealthy:root/sick"
expect "fleet: every problem found, old exits and exit 0 stay quiet" "$ALL"
has "discovery: root, alice, bob and eve read; three containers called web" "DISCOVERED alice/tool,alice/web,bob/web,eve/svc,root/app,root/big,root/crash,root/job,root/loop,root/oldcrash,root/sick,root/systemd-db,root/web,root/web2"
has "should_run: restart policy, enabled units, Quadlet [Install], wanted by a target" "SHOULDRUN alice/web,bob/web,eve/svc,root/app,root/systemd-db,root/web,root/web2"
has "users: dave (no /run/user) is skipped, frank (subuid, no storage) is not a candidate" "USERS alice,bob,eve"
has "skipped users" "SKIPPED dave,gina"
hasnt "storage only (an admin who once ran podman, nothing to start at boot): no finding" "containers:no-linger:gina"
has "  ... but it is listed as not checked" "not checked: gina"
has "a stopped Quadlet whose container is gone is down (user unit, owner named)" "SUMMARY containers:down:alice/web | critical | container web (owner alice) should be running (its Quadlet file"
has "  ... and says the unit failed" "it does not exist; its systemd user unit web.service is failed (failed), result exit-code"
has "hint for a user's container runs as that user" "HINT containers:down:alice/web | runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user status web.service --no-pager -l; journalctl _UID=1001 --since -2h -n 80 --no-pager"
has "podman 3 shape (State.Healthcheck) unhealthy" "SUMMARY containers:unhealthy:bob/web | critical | container web (owner bob) is running but its healthcheck reports unhealthy"
has "podman 4 shape (State.Health) unhealthy" "SUMMARY containers:unhealthy:root/sick | critical"
has "bob's hint names bob" "HINT containers:unhealthy:bob/web | runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002 podman inspect web"
has "root's hint has no runuser" "HINT containers:unhealthy:root/sick | podman inspect sick; podman logs --tail 50 sick"
has "recent crash, with its age" "container crash (owner root) stopped with an error: it exited with code 2, 1.0 hours ago"
has "generate systemd unit (container-NAME.service, enabled) found and down" "SUMMARY containers:down:root/app | critical | container app (owner root) should be running (its systemd unit container-app.service is enabled"
has "generate systemd --new unit whose container is gone" "SUMMARY containers:down:root/web2 | critical | container web2 (owner root) should be running (its systemd unit myweb.service is wanted by multi-user.target"
has "OOM kill" "SUMMARY containers:oom:root/big | warning | container big (owner root) was killed by the out-of-memory killer"
has "restart loop" "SUMMARY containers:restarting:root/loop | warning | container loop (owner root) has restarted 5 times"
has "linger off with containers that should run" "SUMMARY containers:no-linger:eve | warning | user eve (uid 1005) has containers that should run (svc) but linger is off"
has "storage but no /run/user/UID: could not be checked" "SUMMARY containers:no-linger:dave | warning | user dave (uid 1004) has podman containers (container storage in their home) and 1 unit(s) that should start at boot, but they could not be checked"
has "the hint for an unchecked user starts no podman" "HINT containers:no-linger:dave | ls -ld /run/user/1004; ls /var/lib/systemd/linger/; loginctl show-user dave"
has "the table" "root         systemd-db                 running            db.service (active)            yes"
readonly_calls "the check only reads (podman ps / inspect, systemctl show)"
if grep -q '^dave \|^frank ' "$H/calls.log"; then bad "podman ran for a user without /run/user/UID (it would create it)"; else ok "no podman call for dave or frank"; fi
grep -q "^alice $H/run/user/1001\|^alice /run/user/1001 podman ps -a --format json" "$H/calls.log" && ok "alice's podman runs with her XDG_RUNTIME_DIR" \
    || bad "alice's podman did not get XDG_RUNTIME_DIR=/run/user/1001"
[ "$(grep -c 'podman container inspect' "$H/calls.log")" -eq 4 ] && ok "one podman container inspect per owner (4 owners)" \
    || bad "expected 4 inspect calls, got $(grep -c 'podman container inspect' "$H/calls.log")"

run tests/containers/check.yml --check
expect "dry run (Check mode): the same findings" "$ALL"

run tests/containers/check.yml -e '{"podman_discover_ignore": ["alice/.*", "dave/.*", "root/big"]}'
expect "podman_discover_ignore: OWNER/NAME and OWNER/.* (whole match)" "containers:crashed:root/crash,containers:down:root/app,containers:down:root/web2,containers:no-linger:eve,containers:restarting:root/loop,containers:unhealthy:bob/web,containers:unhealthy:root/sick"
run tests/containers/check.yml -e '{"check_containers_recent_hours": 100, "check_containers_restart_warn": 10}'
expect "a longer window reports the 3-day-old crash; a higher restart limit silences the loop" "containers:crashed:root/big,containers:crashed:root/crash,containers:crashed:root/oldcrash,containers:down:alice/web,containers:down:root/app,containers:down:root/web2,containers:no-linger:dave,containers:no-linger:eve,containers:oom:root/big,containers:unhealthy:bob/web,containers:unhealthy:root/sick"
run tests/containers/check.yml -e '{"check_containers_required": ["root/nothere", "tool", "alice/web", "web"]}'
expect "required: missing -> down; stopped -> down; already down or running: nothing more" "$(echo "$ALL,containers:down:root/nothere,containers:down:alice/tool" | tr ',' '\n' | sort | paste -sd,)"
run tests/containers/check.yml -e '{"podman_discover_rootless": false}'
expect "rootless off: only root's containers" "containers:crashed:root/big,containers:crashed:root/crash,containers:down:root/app,containers:down:root/web2,containers:oom:root/big,containers:restarting:root/loop,containers:unhealthy:root/sick"
FAKE_PODMAN_FAIL=bob run tests/containers/check.yml
has "podman fails for one user: containers:query, the others still checked" "SUMMARY containers:query:bob | warning | the containers of bob could not be read: podman ps failed"
has "  ... hint to see it" "HINT containers:query:bob | runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002 podman ps -a"
has "  ... alice still reported" "containers:down:alice/web"
run tests/containers/check.yml -e '{"podman_discover_users": ["ghost"]}'
has "an extra user that does not exist" "SUMMARY containers:query:ghost | warning | podman_discover_users names ghost, but there is no such user"
run tests/containers/check.yml -e '{"_pd_podman": "podman-not-installed-here"}'
expect "no podman: no finding" ""
has "  ... and says so" "skipped: podman is not installed"
run tests/containers/check.yml -e '{"_pd_podman": "podman-not-installed-here", "check_containers_required": ["web"]}'
expect "no podman but containers are required: critical" "containers:no-podman"
run tests/containers/check.yml -e '{"podman_discover_enabled": false}'
expect "discovery off: no finding" ""
prepare empty
run tests/containers/check.yml
expect "podman, no containers: healthy" ""
has "  ... table says so" "(no containers)"
prepare fleet
run playbooks/health_check.yml -e health_checks=containers
has "health_check.yml runs the containers check" "finding(s). Checks run: containers"
hasnt "  ... and it did not break" "could not run"
run playbooks/health_check.yml -e health_checks=daily --list-tasks
[ "$rc" -eq 0 ] && ok "daily includes containers (the playbook accepts it)" || bad "health_check.yml --list-tasks failed" 20

# ---------------------------------------------------------------- Service watch
ALICE="runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001"
BOB="runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002"
prepare watch
run playbooks/service_watch.yml
[ "$rc" -ne 0 ] && ok "approval: the job fails on purpose (NEEDS APPROVAL)" || bad "approval: the job passed" 30
has "watched set from discovery, keyed by owner (three containers called nginx)" "cache (root): plain podman (no systemd unit runs it)"
has "  ... root's nginx is watched and up" "nginx (root) running"
has "NEEDS APPROVAL lists each owner's own fix, in order" "Fix to approve (the standard fix (ACT proposed nothing)): podman start cache ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx"
has "  ... names the owners" "container nginx (user alice) is not running: its systemd user unit nginx.service is failed"
has "ACT is told the owner and the only right start command" "- nginx (user alice): run by the systemd user unit nginx.service (Quadlet file $H/home/alice/.config/containers/systemd/nginx.container; now failed (failed), result exit-code). Start: \`$ALICE systemctl --user start nginx.service\`" "$H/fake-act-task.txt"
has "  ... and to read the rootless evidence, not to run runuser reads" "are refused in this job, so do not try them" "$H/fake-act-task.txt"
has "evidence for alice's container (read as alice)" "### \$ $ALICE systemctl --user status nginx.service --no-pager -l" "$H/fake-act-evidence.txt"
has "evidence for bob's container: its logs" "fake log line of nginx (owner bob)" "$H/fake-act-evidence.txt"
hasnt "no evidence collected for root's containers (ACT reads those itself)" "container cache (root)" "$H/fake-act-evidence.txt"
if ls /tmp/ansible.*service-watch-evidence.txt >/dev/null 2>&1 && grep -q "fake log line of nginx (owner bob)" /tmp/ansible.*service-watch-evidence.txt 2>/dev/null; then
    bad "the evidence file was left behind"; else ok "the evidence file is removed after ACT"; fi
has "incident record keyed by owner" '"units": {"root/cache": ""' "$H/incidents.jsonl"

printf '{"propose": ["sudo -u alice podman start nginx", "podman start nginx", "systemctl daemon-reload"]}' > "$H/act.json"
run playbooks/service_watch.yml
has "ACT's proposals made right per owner, the standard fix for the rest, in dependency order" "systemctl daemon-reload ; podman start cache ; podman start nginx ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx"
has "  ... and the approver is told which part is not from ACT" "ACT + the standard fix for cache (root), nginx (user bob), which ACT did not cover"

prepare watch
run playbooks/service_watch.yml -e watch_mode=self-heal
[ "$rc" -eq 0 ] && ok "self-heal: the standard fix brings all back, the job passes" || bad "self-heal: exit $rc" 40
has "  ... ran the owner-aware fixes" "standard fix: podman start cache ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx"
python3 - "$H/fake-act-allow.json" <<'PY' && ok "self-heal: ACT may run exactly the right commands for each owner" || bad "self-heal allow patterns"
import json, re, sys
pats = [re.compile(r"\A(?:" + p + r")\Z") for p in json.load(open(sys.argv[1]))]
A = "runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 "
B = "runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002 "
good = ["podman start cache", "systemctl restart db.service", A + "systemctl --user start nginx.service", B + "podman start nginx"]
wrong = [A + "podman start nginx", B + "systemctl --user start nginx.service", "systemctl start nginx.service", A + "systemctl --user stop nginx.service"]
assert all(any(p.match(c) for p in pats) for c in good), good
assert not any(any(p.match(c) for p in pats) for c in wrong), wrong
PY

prepare watch
cat > "$work/fix.json" <<EOJ
{"service_watch_fix": {"host01.example.com": {"commands": ["podman start cache", "$ALICE systemctl --user start nginx.service", "$BOB podman start nginx"], "source": "the standard fix", "findings": []}}}
EOJ
run playbooks/service_fix_approved.yml -e "@$work/fix.json" --check
has "apply, dry run: what it would run" "DRY RUN on host01.example.com: approved, and would run: podman start cache ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx. Nothing was changed (Check mode)."
readonly_calls "apply, dry run: nothing started"
run playbooks/service_fix_approved.yml -e "@$work/fix.json"
[ "$rc" -eq 0 ] && ok "apply: the approved owner-aware commands ran and the re-check passed" || bad "apply: exit $rc" 40
has "  ... summary" "Up: ran podman start cache ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx and the re-check confirms it"
grep -q "^alice $H/run/user/1001 systemctl --user start nginx.service\|^alice /run/user/1001 systemctl --user start nginx.service" "$H/calls.log" \
    && ok "  ... alice's unit was started as alice (systemctl --user)" || bad "alice's unit was not started as alice"
prepare watch
cat > "$work/fix-bad.json" <<EOJ
{"service_watch_fix": {"host01.example.com": {"commands": ["runuser -u alice -- reboot"], "source": "ACT", "findings": []}}}
EOJ
run playbooks/service_fix_approved.yml -e "@$work/fix-bad.json" --check
[ "$rc" -ne 0 ] && ok "apply, dry run: the guard refuses runuser -u alice -- reboot" || bad "guard did not refuse (dry run)" 30
has "  ... and says so" "Refused on host01.example.com: runuser -u alice -- reboot"

prepare watch
run playbooks/service_watch.yml -e '{"watch_discover": false, "watch_containers": [{"name": "cache"}, {"name": "nginx", "user": "alice"}, {"name": "web", "user": "carol"}]}'
has "static list only (watch_discover: false): each owner's fix, and linger for a user whose systemd is not running" "podman start cache ; $ALICE systemctl --user start nginx.service ; loginctl enable-linger carol"
has "  ... carol cannot be checked" "container web (user carol) cannot be checked: there is no /run/user/1003"
hasnt "  ... discovered containers are not watched then" "nginx (user bob)"

# AAP host: never self-heal, and the apply job applies nothing there (a person does it by hand)
prepare watch
INV=inv-aap.yml run playbooks/service_watch.yml -e watch_mode=self-heal -e target=aap
[ "$rc" -ne 0 ] && ok "AAP host: self-heal is off, the job asks for approval instead" || bad "AAP host: self-heal ran (exit $rc)" 30
has "  ... and says so" "(diagnose only): self-heal is off here"
[ "$(cat "$H/fake-act-allow.json")" = "[]" ] && ok "  ... ACT may run nothing by itself there" || bad "AAP host: ACT got allow patterns: $(cat "$H/fake-act-allow.json")"
readonly_calls "  ... nothing was started on the AAP host"
INV=inv-aap.yml run playbooks/service_fix_approved.yml -e "@$work/fix.json" -e target=aap
has "AAP host: the apply job runs nothing and says what to apply by hand" "(diagnose only: a restart there could stop the job that runs it). Nothing was run. Apply by hand if you agree: podman start cache"
readonly_calls "  ... nothing was started by the apply job"

# a user without /run/user/UID whose units should start at boot: watched as a whole
prepare watch
mkdir -p "$H/home/carol/.config/containers/systemd"
printf '[Container]\nImage=registry.example.com/web:1\n\n[Install]\nWantedBy=default.target\n' > "$H/home/carol/.config/containers/systemd/web.container"
run playbooks/service_watch.yml
has "a user whose systemd is not running, with units that should start at boot, is reported" "the containers of user carol (1 should start at boot) cannot be checked: there is no /run/user/1003"
has "  ... the fix to approve is linger, after the starts" "$BOB podman start nginx ; loginctl enable-linger carol"
run playbooks/service_watch.yml -e watch_mode=self-heal
[ "$rc" -ne 0 ] && ok "self-heal starts what it may, and leaves linger to a person" || bad "self-heal with carol: exit $rc" 30
if grep -q ' loginctl enable-linger' "$H/calls.log"; then bad "self-heal ran loginctl itself"; else ok "  ... it did not run loginctl"; fi
has "  ... linger is offered for approval (it was never run)" "Fix to approve (the standard fix (ACT proposed nothing)): loginctl enable-linger carol"
cat > "$work/fix-linger.json" <<EOJ
{"service_watch_fix": {"host01.example.com": {"commands": ["loginctl enable-linger carol"], "source": "the standard fix", "findings": []}}}
EOJ
run playbooks/service_fix_approved.yml -e "@$work/fix-linger.json"
[ "$rc" -eq 0 ] && ok "apply: loginctl enable-linger carol, and the re-check sees her systemd running" || bad "apply linger: exit $rc" 40

prepare empty
run playbooks/service_watch.yml
[ "$rc" -eq 0 ] && ok "nothing to watch: passes" || bad "nothing to watch: exit $rc" 30
has "  ... and says so" "Nothing to watch on host01.example.com"

if [ "$fails" -eq 0 ]; then echo "all $oks containers checks passed"; else echo "$fails of $((oks + fails)) containers check(s) FAILED"; exit 1; fi
