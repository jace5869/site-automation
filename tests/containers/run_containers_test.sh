#!/usr/bin/env bash
# The containers Health check (roles/check_containers + roles/podman_discover) and Service watch
# (roles/service_watch) against fake podman / systemctl / runuser / ps (tests/containers/fakes.py):
# root and two users with a container of the SAME name, a stopped Quadlet whose container is gone,
# unhealthy (podman 3 and podman 4 JSON), a recent crash, old exits that must stay quiet, a user with
# storage but no /run/user/UID, restart loops, OOM kills, no podman at all; and for Service watch:
# the watched set from discovery, fixes keyed by owner, the rootless fix command, evidence for ACT,
# self-heal, the approved-fix job and its dry run; ACT off (site_act_enabled false) asks nothing. Runs nothing for
# real: every tool it touches is fake.
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
    for t in podman systemctl runuser ps journalctl id getent loginctl chronyc auditctl getenforce curl openssl df; do
        if [ "$(PATH="$H/bin:$PATH" command -v "$t")" != "$H/bin/$t" ] || [ "$(PATH="$H/bin:$PATH" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
            echo "ABORT - $t is not the fake; not running anything"; exit 1
        fi
    done
}

hooks() {  # the paths of the fake host (test hooks of the roles) as extra variables
    cat <<EOJ
{"target": "testhosts", "_pd_now": "$NOW", "_pd_subuid_file": "$H/subuid", "_pd_linger_dir": "$H/linger", "_pd_run_user_dir": "$H/run/user",
 "_pd_users_quadlet_dir": "$H/etc/containers/systemd/users", "podman_discover_unit_dirs": ["$H/etc/systemd/system"],
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
has "the discovery's registered result holds no environment variable and no healthcheck output (cut down on the host)" "REGISTERED secrets=none"
has "  ... nor a container's command line (podman ps json: Command), a label's token or a unit's ExecStart arguments" "REGISTERED secrets=none"
hasnt "  ... (a password of a command line)" "cmd-secret"
hasnt "  ... (a password in a unit's ExecStart)" "exec-secret"
hasnt "  ... (a token in a label)" "label-secret"
hasnt "  ... and none reaches the job output" "hunter2"
hasnt "  ... (a token of a healthcheck)" "hc-secret"
hasnt "  ... (a secret)" "s3cr3t"
has "discovery: root, alice, bob and eve read; three containers called web" "DISCOVERED alice/tool,alice/web,bob/web,eve/svc,root/app,root/big,root/crash,root/job,root/loop,root/oldcrash,root/sick,root/systemd-db,root/web,root/web2"
has "should_run: restart policy, enabled units, Quadlet [Install], wanted by a target" "SHOULDRUN alice/web,bob/web,eve/svc,root/app,root/systemd-db,root/web,root/web2"
has "users: dave (no /run/user) is skipped, frank (subuid, no storage) is not a candidate" "USERS alice,bob,eve"
has "skipped users" "SKIPPED dave,gina"
hasnt "storage only (an admin who once ran podman, nothing to start at boot): no finding" "containers:no-linger:gina"
has "  ... but it is listed as not checked" "not checked: gina"
has "a stopped Quadlet whose container is gone is down (user unit, owner named)" "SUMMARY containers:down:alice/web | critical | container web (owner alice) should be running (its Quadlet file"
has "  ... and says the unit failed" "it does not exist; its systemd user unit web.service is failed (failed), result exit-code"
has "hint for a user's container runs as that user (and only reads: state, the unit's properties, a count of the journal's errors)" "HINT containers:down:alice/web | runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 podman ps -a --filter 'name=^web\$' --format '{{.Names}} {{.Status}}'; runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user show web.service -p ActiveState -p SubState -p Result -p NRestarts; journalctl _UID=1001 --since -2h -n 80 --no-pager | grep -ci error"
has "podman 3 shape (State.Healthcheck) unhealthy" "SUMMARY containers:unhealthy:bob/web | critical | container web (owner bob) is running but its healthcheck reports unhealthy"
has "podman 4 shape (State.Health) unhealthy" "SUMMARY containers:unhealthy:root/sick | critical"
has "bob's hint names bob" "HINT containers:unhealthy:bob/web | runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002 podman container inspect web --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}} {{.RestartCount}}'"
has "root's hint has no runuser: a state query and a COUNT of error lines, never the log or the whole inspect" "HINT containers:unhealthy:root/sick | podman container inspect sick --format '{{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}} {{.RestartCount}}'; podman ps -a --filter 'name=^sick\$' --format '{{.Names}} {{.Status}}'; podman logs --tail 50 sick 2>&1 | grep -ci error"
has "ACT's evidence (the hints run as ACT runs them) holds no environment variable, no healthcheck output and no raw log line" "EVIDENCE secrets=none rawlog=none"
hasnt "  ... the old hints (podman inspect NAME, podman logs --tail 50 NAME unpiped) are not among them" "HINT containers:unhealthy:root/sick | podman inspect"
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
n_inspect="$(grep 'podman container inspect' "$H/calls.log" | grep -vc -- '--format')"      # discovery's; the evidence hints (a scoped --format) are the test's own
[ "$n_inspect" -eq 4 ] && ok "one podman container inspect per owner (4 owners)" || bad "expected 4 inspect calls, got $n_inspect"

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
# user.max_user_namespaces = 0 (a STIG setting): no user is tried, one INFO line, only root's containers
ROOTONLY="containers:crashed:root/big,containers:crashed:root/crash,containers:down:root/app,containers:down:root/web2,containers:oom:root/big,containers:restarting:root/loop,containers:unhealthy:root/sick"
printf '0\n' > "$H/userns0"; printf '15000\n' > "$H/userns"
: > "$H/calls.log"
FAKE_USERNS_OFF=1 run tests/containers/check.yml -e "{\"_pd_userns_file\": \"$H/userns0\"}"
expect "user namespaces off: only root's findings - no containers:query per user, no linger warning" "$ROOTONLY"
has "  ... one INFO line says so" "INFO: rootless podman is off on this host (user.max_user_namespaces = 0): only root's containers read"
has "  ... no user read or skipped" "USERS  SKIPPED"
if grep -qv '^root ' "$H/calls.log"; then bad "  ... podman ran as a user with namespaces off"; grep -v '^root ' "$H/calls.log" | head -3; else ok "  ... podman ran only as root"; fi
FAKE_USERNS_OFF=1 run tests/containers/check.yml -e "{\"_pd_userns_file\": \"$H/userns0\", \"podman_discover_users\": [\"alice\"]}"
expect "  ... a user named in podman_discover_users: one warning" "$(echo "$ROOTONLY,containers:query:alice" | tr ',' '\n' | sort | paste -sd,)"
has "  ... which names the setting" "SUMMARY containers:query:alice | warning | podman_discover_users names alice, but rootless podman is off on this host (user.max_user_namespaces = 0, a STIG setting"
has "  ... and how to look" "HINT containers:query:alice | sysctl user.max_user_namespaces; grep -rs max_user_namespaces /etc/sysctl.conf /etc/sysctl.d/"
FAKE_USERNS_OFF=1 run tests/containers/check.yml -e "{\"_pd_userns_file\": \"$H/userns\"}"
expect "podman says \"user namespaces are not enabled\" (the setting not 0): taken the same way, no warning, no user tried again" "$ROOTONLY"
has "  ... the INFO line names the users" "INFO: rootless podman is off for alice, bob, eve (podman: "
FAKE_USERNS_OFF=1 run tests/containers/check.yml -e "{\"_pd_userns_file\": \"$H/userns\", \"podman_discover_users\": [\"bob\"]}"
has "  ... bob named in podman_discover_users: one warning" "SUMMARY containers:query:bob | warning | podman_discover_users names bob, but its podman says "
FAKE_USERNS_OFF=1 run playbooks/service_watch.yml -e "{\"_pd_userns_file\": \"$H/userns0\"}"
has "service watch with namespaces off: says the users are not looked at" "Not looked at: the users (rootless podman) - user namespaces are off on this host (user.max_user_namespaces = 0)"
hasnt "  ... and proposes no linger for them" "loginctl enable-linger dave"
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
# what a unit runs besides one named container: pods (Quadlet .kube, podman-kube@), compose, podman pod
prepare units
run tests/containers/check.yml
expect "units: a down .kube, podman-kube@, compose and pod unit are down; finished oneshot, disabled and podman's tools are not" \
    "containers:down:alice/notes,containers:down:root/mypod,containers:down:root/podman-kube@-etc-containers-blog.yaml,containers:down:root/shop,containers:down:root/stack2"
has "  ... a Quadlet .kube is a pod, named as one" "SUMMARY containers:down:root/shop | critical | pod (podman kube play) shop (owner root) should be running (its Quadlet file $H/etc/containers/systemd/shop.kube starts it at boot ([Install] WantedBy=multi-user.target)) but it is not running; its systemd unit shop.service is failed (failed), result exit-code"
has "  ... a compose project" "SUMMARY containers:down:root/stack2 | critical | compose project stack2 (owner root) should be running (its systemd unit stack2.service is enabled"
has "  ... a podman pod" "SUMMARY containers:down:root/mypod | critical | podman pod mypod (owner root)"
has "  ... a user's .kube, as that user" "runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001 systemctl --user show notes.service -p ActiveState"
has "  ... a running .kube's containers carry its unit, and its [Install] makes them should-run" "DISCOVERED alice/notes,root/api-pod-api,root/manual,root/mypod,root/podman-kube@-etc-containers-blog.yaml,root/shop,root/stack,root/stack2"
has "  ... should run" "SHOULDRUN alice/notes,root/api-pod-api,root/mypod,root/podman-kube@-etc-containers-blog.yaml,root/shop,root/stack,root/stack2"
has "  ... a oneshot compose that finished counts as up" "root         stack                      finished"
readonly_calls "  ... read-only"
run playbooks/health_check.yml -e '{"health_checks": ["services", "containers"], "check_services_required": []}'
has "one outage, one finding: the services check's unit-failed of a container unit is folded in" \
    "the services check sees its systemd unit shop.service fail too (unit-failed:shop.service is not reported separately)"
hasnt "  ... no separate unit-failed for it" "systemd unit shop.service has FAILED"
has "  ... a failed unit that runs no container stays the services check's" "systemd unit autoupd.service has FAILED"
prepare fleet
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
# one outage, one finding: a database container that is down is reported by the containers check
run playbooks/health_check.yml -e '{"health_checks": ["mariadb", "containers"], "check_mariadb_containers": ["app", "web2", "job"]}'
cp "$work/out.txt" "$work/dedup.txt"
has "a down database container: one finding, the containers check's, naming the database" \
    "container app (owner root) should be running (its systemd unit container-app.service is enabled"
has "  ... which says the database is down too" "it is a MariaDB/MySQL database container, so the database is down too (mariadb:app:down is not reported separately)"
has "  ... also when its container is gone" "(mariadb:web2:down is not reported separately)"
hasnt "  ... no second finding for it" "MariaDB/MySQL is not running (container app"
has "  ... a stopped database container the containers check does not report keeps its mariadb:down" "[job] MariaDB/MySQL is not running (container job"
run playbooks/health_check.yml -e '{"health_checks": ["mariadb"], "check_mariadb_containers": ["app", "web2"]}'
has "the database check alone still reports a down database container" "[app] MariaDB/MySQL is not running (container app"

# a container that went away between the listing and the inspect: podman prints the survivors and exits non-zero; the raw output is never printed
prepare fleet
: > "$H/inspect_partial"
run tests/containers/check.yml
rm -f "${H:?}/inspect_partial"
has "a partial inspect failure: the discovery's registered result still holds no environment, command line or token" "REGISTERED secrets=none"
has "  ... and the survivors are still read" "DISCOVERED alice/tool,alice/web,bob/web,eve/svc,root/app"
# an owner of check_containers_required that is no user name (or not a user at all) is never put in a command
prepare fleet
run tests/containers/check.yml -e '{"check_containers_required": ["evil;reboot/web", "a b/web", "root/nothing"]}'
has "a required container of an owner that is no user name: the hint lists as root, nothing of the owner in it" "HINT containers:down:evil;reboot/web | podman ps -a --format '{{.Names}} {{.Status}}'"
hasnt "  ... no runuser of it" "runuser -u evil"
hasnt "  ... nor of the one with a space" "runuser -u a b"
has "  ... and the name filter is one quoted argument" "'name=^sick\$'"
prepare fleet
# names a container's label and a user's Quadlet file choose: a label that adds an option to systemctl show, a ServiceName that is shell syntax
prepare inject
run tests/containers/check.yml
has "a unit label that adds an option (-p Environment) to systemctl show: the unit's environment is not in the registered result" "REGISTERED secrets=none"
grep -E ' systemctl show ' "$H/calls.log" | grep -q ' -- ' && ok "  ... the unit names go to systemctl show after --" || bad "  ... systemctl show has no --"
if grep -qE ' systemctl show .* -p Environment| -p Environment' "$H/calls.log"; then bad "  ... a label added an option to systemctl show"; else ok "  ... no label added an option to systemctl show"; fi
if grep -q 'touch-pwn' "$H/calls.log"; then bad "  ... a Quadlet's ServiceName reached a command"; else ok "  ... a Quadlet's ServiceName that is no unit name reaches no command"; fi
prepare fleet
# ---------------------------------------------------------------- Service watch
ALICE="runuser -u alice -- env XDG_RUNTIME_DIR=/run/user/1001"
BOB="runuser -u bob -- env XDG_RUNTIME_DIR=/run/user/1002"
prepare watch_inject
run playbooks/service_watch.yml
# the container's name may be an ARGUMENT (data) of podman; a unit named by ServiceName, or anything of them as a command, may not
if grep 'touch-pwn' "$H/calls.log" | grep -vF 'ev$(touch-pwn2)' | grep -q .; then bad "a user's Quadlet name reached a command"; else ok "a user's Quadlet with shell syntax as ServiceName / ContainerName: nothing of it is run (the container name only as an argument)"; fi
if grep -F 'ev$(touch-pwn2)' "$H/calls.log" | grep -vE '^alice /run/user/1001 podman (container exists|container inspect|logs|inspect)' | grep -q .; then bad "  ... the container name reached a command other than a read of podman"; else ok "  ... the container name is in reads of podman only"; fi
hasnt "  ... and no fix is proposed with it" "start bad;touch-pwn" 
hasnt "  ... (not quoted either)" "systemctl --user start bad"
has "  ... its heading in the evidence is the name as written, not evaluated" 'ev$(touch-pwn2)' "$H/fake-act-evidence.txt"
prepare watch
run playbooks/service_watch.yml -vv
for w in hunter2 s3cr3t hc-secret cmd-secret exec-secret label-secret; do
    hasnt "service watch (job output at -vv: every task's result): no secret '$w' of a container's environment, command line, label or unit" "$w"
done
has "  ... its state reads are the cut-down ones (state and the healthcheck status only)" '\"Health\": {\"Status\"'
[ "$rc" -ne 0 ] && ok "approval: the job fails on purpose (NEEDS APPROVAL)" || bad "approval: the job passed" 30
has "watched set from discovery, keyed by owner (three containers called nginx)" "cache (root): plain podman (no systemd unit runs it)"
has "  ... root's nginx is watched and up" "nginx (root) running"
has "NEEDS APPROVAL lists each owner's own fix, in order" "Fix to approve (the standard fix (ACT proposed nothing)): podman start cache ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx"
has "  ... names the owners" "container nginx (user alice) is not running: its systemd user unit nginx.service is failed"
has "ACT is told the owner and the only right start command" "- nginx (user alice): run by the systemd user unit nginx.service (Quadlet file $H/home/alice/.config/containers/systemd/nginx.container; now failed (failed), result exit-code). Start: \`$ALICE systemctl --user start nginx.service\`" "$H/fake-act-task.txt"
has "  ... and to read the rootless evidence, not to run runuser reads" "are refused in this job, so do not try them" "$H/fake-act-task.txt"
has "  ... and what to run for a root container: state and counts, with the format templates as they are" "podman ps -a --format '{{.Names}} {{.Status}}'" "$H/fake-act-task.txt"
has "  ... a count of error lines, not the log" "podman logs --tail 80 NAME 2>&1 | grep -ci error" "$H/fake-act-task.txt"
hasnt "  ... never podman inspect NAME, systemctl status or an unpiped journalctl" "podman inspect NAME" "$H/fake-act-task.txt"
hasnt "  ... (systemctl status)" "systemctl status UNIT" "$H/fake-act-task.txt"
has "evidence for alice's container (read as alice): the unit's properties, not its status lines" "### \$ $ALICE systemctl --user show nginx.service -p ActiveState" "$H/fake-act-evidence.txt"
has "evidence for bob's container: a COUNT of its error lines" "podman logs --tail 60 nginx | grep -ci error" "$H/fake-act-evidence.txt"
has "  ... and its state from --format" "podman container inspect --format {{.State.Status}} {{.State.ExitCode}} {{.State.OOMKilled}} {{.RestartCount}} {{.State.FinishedAt}} nginx" "$H/fake-act-evidence.txt"
hasnt "  ... no raw log line reaches ACT" "fake log line of nginx" "$H/fake-act-evidence.txt"
hasnt "  ... nor an environment variable" "hunter2" "$H/fake-act-evidence.txt"
hasnt "  ... nor a token" "s3cr3t" "$H/fake-act-evidence.txt"
hasnt "  ... nor a healthcheck's output" "hc-secret" "$H/fake-act-evidence.txt"
hasnt "no evidence collected for root's containers (ACT reads those itself)" "container cache (root)" "$H/fake-act-evidence.txt"
evpath="$(grep -o '`cat [^`]*service-watch-evidence.txt`' "$H/fake-act-task.txt" | head -1 | sed 's/^`cat //; s/`$//')"       # this run's file (another run may have its own)
if [ -n "$evpath" ] && [ ! -e "$evpath" ]; then ok "the evidence file is removed after ACT"; else bad "the evidence file was left behind or not found: [$evpath]"; fi
has "incident record keyed by owner" '"units": {"root/cache": ""' "$H/incidents.jsonl"

printf '{"propose": ["sudo -u alice podman start nginx", "podman start nginx", "systemctl daemon-reload"]}' > "$H/act.json"
run playbooks/service_watch.yml
has "ACT's proposals made right per owner, the standard fix for the rest, in dependency order" "systemctl daemon-reload ; podman start cache ; podman start nginx ; $ALICE systemctl --user start nginx.service ; $BOB podman start nginx"
has "  ... and the approver is told which part is not from ACT" "ACT + the standard fix for cache (root), nginx (user bob), which ACT did not cover"

# ACT off (site_act_enabled false, 1.3.0): ACT never asked - the standard fix, as when ACT cannot run; the Health check's
# use_act asks nothing either and says so
prepare watch
run playbooks/service_watch.yml -e watch_mode=self-heal -e site_act_enabled=false
[ "$rc" -eq 0 ] && ok "ACT off, self-heal: the standard fix brings all back, the job passes" || bad "ACT off, self-heal: exit $rc" 40
has "  ... said in one line" "ACT is off (site_act_enabled false) - not asked; the standard fix for the down containers is used."
[ -e "$H/fake-act-calls.log" ] && bad "  ... ACT was called" || ok "  ... ACT was not called"
prepare fleet
run playbooks/health_check.yml -e health_checks=containers -e use_act=true -e site_act_enabled=false
has "ACT off, Health check with use_act: not asked, said" "ACT is off (site_act_enabled false) - not asked."
[ -e "$H/fake-act-calls.log" ] && bad "  ... ACT was called" || ok "  ... ACT was not called"

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

# ---------------------------------------------------------------- the NetApp Console profile (1.84.0)
# ds_* containers (root's, some with a _1 suffix), a clock 41 s off, auditd flooded, fapolicyd active with an agent denial, the data folder 88% full,
# the certificate 23 days left, an empty certificate volume, VM_SNAME empty in traefik, jwt errors in ds_occmauth_1, one container up with its
# process gone, one service port refusing. The generic checks are test doubles (tests/containers/roles/fakechk_*): the profile adds fapolicyd and
# auditd even in the daily set, gives the generic findings its wording and never repeats them. Every hint it makes must be read only (ACT runs
# a hint as its evidence): what to do is the fix.
prepare console
CROLES="ANSIBLE_ROLES_PATH=$repo/tests/containers/roles:$repo/roles:$repo/vendor/act/ansible/roles"
# a service that answers (ds_occmauth_1's port) and one that does not (ds_krakend_1's port 1)
okport=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
python3 -m http.server "$okport" --bind 127.0.0.1 > /dev/null 2>&1 &
lpid=$!
trap 'kill $lpid 2>/dev/null; rm -rf "$work"' EXIT
for _ in $(seq 1 30); do (exec 3<>"/dev/tcp/127.0.0.1/$okport") 2>/dev/null && break; sleep 0.1; done
run_console() {  # run_console [ansible args]: the profile with the test doubles of the generic checks
    (cd "$repo" && env "$CROLES" PATH="$H/bin:$PATH" FAKE_DIR="$H" FAKE_NOW="$NOW" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False \
        ANSIBLE_DEPRECATION_WARNINGS=False "$ap" -i "$work/inv.yml" tests/containers/console.yml -e "@$work/hooks.json" -e site_check_role_prefix=fakechk_ \
        -e "_con_proc=$H/proc" -e "{\"check_containers_console_ports\": {\"ds_occmauth\": $okport, \"ds_krakend\": 1}}" "$@") > "$work/out.txt" 2>&1
    rc=$?
}
hooks > "$work/hooks.json"
run_console
[ "$rc" -eq 0 ] && ok "the Console profile runs" || bad "the Console profile: exit $rc" 30
has "the checks that ran: fapolicyd and auditd were added after the set" "CHECKS auditd,containers,fapolicyd"
has "the host's own clock: a finding of the profile (the time check did not run)" "SUMMARY console:clock | critical | host01.example.com: the clock is 41.2 s fast and synchronized: NetApp Console logins fail while it is off"
has "  ... its hint only reads (ACT runs a hint as evidence)" "HINT console:clock | chronyc tracking"
has "  ... what to do is the fix, not the hint" "FIX console:clock | chronyc makestep (as root), then log in to the Console again"
has "the generic fapolicyd finding, in the Console's wording" "SUMMARY fapolicyd:down | critical | fapolicyd is not running - the NetApp Console's containers depend on its rules"
has "the generic auditd finding, with the job that tunes it" "SUMMARY auditd:lost | warning | auditd lost 1203 events - a busy container host can flood auditd; see the job Linux - auditd tuning"
has "auditd: the low backlog_limit is the profile's finding (the lost events are the check's)" "SUMMARY console:auditd | warning | host01.example.com: auditd: backlog_limit is 64 (under 8192)"
hasnt "  ... and it does not say the events were lost again" "event(s) lost"
has "the data folder, found from the mounts: 88% full" "SUMMARY console:disk:/opt/application/netapp | warning | host01.example.com: the NetApp Console data folder /opt/application/netapp/data is 88% full (12.0 GB free)"
has "  ... no hint (what to look at is in the fix, for a person), and du and df on the quoted path there" "FIX console:disk:/opt/application/netapp | see what fills it: du -xh --max-depth=2 /opt/application/netapp/data/core | sort -h | tail; df -h /opt/application/netapp/data/core"
has "  ... its hint is empty" "HINT console:disk:/opt/application/netapp | "
has "the certificate (the volume has none: the HTTPS answer says 23 days)" "SUMMARY console:cert | warning | host01.example.com: the Console's HTTPS certificate expires in 23 days"
has "the errors of ds_occmauth_1: counted by kind, no line of text" "SUMMARY console:errors:ds_occmauth_1 | warning | host01.example.com: ds_occmauth_1 logged 6 error line(s) in the last 6 h (the clock is off: jwt errors usually mean that): jwt or token validation x3, signing key x2, connection refused or timed out x1"
has "  ... one state query of the container's name and status, never a log line" "HINT console:errors:ds_occmauth_1 | podman ps -a '--filter=name=^ds_occmauth_1\$' --format '{{.Names}} {{.Status}}'"
has "  ... the count is in the fix, for a person" "FIX console:errors:ds_occmauth_1 | count them: podman logs --since 6h ds_occmauth_1 2>&1 | grep -ciE 'error|recover|corrupt|jwt'"
hasnt "  ... no log text, token, address or name reaches the output" "user.name@example.mil"
hasnt "  ... " "start time 192.0.2.3"
hasnt "ds_mongo's 2 lines are under the limit" "console:errors:ds_mongo"
has "a container that is unhealthy is the Console's" "SUMMARY containers:unhealthy:root/ds_occmauth_1 | critical | NetApp Console: container ds_occmauth_1 (owner root) is running but its healthcheck reports unhealthy"
hasnt "another container of the host is left alone" "NetApp Console: container web"
has "the generic container finding of a Console container: ONE state query (name and status), never the log, an inspect or the environment" "HINT containers:unhealthy:root/ds_occmauth_1 | podman ps -a '--filter=name=^ds_occmauth_1\$' --format '{{.Names}} {{.Status}}'"
has "  ... what to do is its fix" "FIX containers:unhealthy:root/ds_occmauth_1 | read the log of ds_occmauth_1 for the cause, then podman restart ds_occmauth_1 (then the containers that depend on it)"
hasnt "  ... no hint of the host reads a log" "podman logs --tail"
has "a container that shows Up with its process gone: critical" "SUMMARY console:process-gone:ds_cloudmanager_core | critical | host01.example.com: the container ds_cloudmanager_core shows Up but its process is gone (podman has no process id for it)"
has "  ... what to do: restart it, then the dependents" "FIX console:process-gone:ds_cloudmanager_core | podman restart ds_cloudmanager_core (then the containers that depend on it)"
hasnt "  ... a container whose process is there is not reported" "console:process-gone:ds_traefik_1"
has "a service port that refuses (ds_krakend_1, port 1): critical" "SUMMARY console:port:ds_krakend_1 | critical | host01.example.com: the container ds_krakend_1 is Up but its service port 1 refuses connections inside the network"
hasnt "  ... a port that answers (ds_occmauth_1) is not" "console:port:ds_occmauth_1"
has "the certificate volume is empty: critical, the 502 through the gateway, traefik's 'Cert files removed' counted" "SUMMARY console:certs-empty | critical | host01.example.com: the certificate volume ds_certs is empty (no cert.pem / privkey.pem): the Console's login fails with 502 through the API gateway - traefik logged 'Cert files removed' 2 time(s) in 24 h"
has "  ... no hint: no volume inspect (its Options can hold a share's password), the listing is in the fix" "HINT console:certs-empty | "
hasnt "  ... never a podman volume inspect among the hints" "HINT console:certs-empty | podman volume"
has "  ... what to do: the self-signed certificate with the host's names, then restart traefik and krakend (their real names, with _1)" "FIX console:certs-empty | look: ls -l $H/var/lib/containers/storage/volumes/ds_certs/_data; create cert.pem and privkey.pem in $H/var/lib/containers/storage/volumes/ds_certs/_data - self-signed: openssl req -x509"
has "  ... the restart names both containers" "then podman restart ds_traefik_1 ds_krakend_1"
has "VM_SNAME empty inside traefik: a warning that says what it breaks" "SUMMARY console:vm-names | warning | host01.example.com: VM_SNAME is empty inside ds_traefik_1: its entrypoint (/conf/entrypoint.sh) then makes no certificate"
has "  ... the hint reads the variables, the fix points to the install configuration (nothing is changed)" "FIX console:vm-names | see them: podman exec ds_traefik_1 printenv VM_SNAME VM_FQDN; set them in the NetApp Console's install configuration and recreate the container"
has "the three sections" "SECTIONS NetApp Console containers | NetApp Console host checks | Errors in the last 6 hours"
has "the containers table: ds_occmauth_1 unhealthy, 4 restarts, up 6 h, its version" "CROW host01.example.com | ds_occmauth_1 | running | unhealthy | 4 | 6 h | v4.10.0"
has "  ... ds_mysql with its own image's version" "CROW host01.example.com | ds_mysql | running | healthy | 0 | 41 d | 8.0.36"
has "  ... the container whose process is gone" "CROW host01.example.com | ds_cloudmanager_core | running (process gone) |"
has "  ... a name with _1 and one without (ds_mongo) both match" "CROW host01.example.com | ds_krakend_1 | running | healthy | 0 | 41 d | v4.10.0"
hasnt "  ... the host's web container is not in it" "CROW host01.example.com | web |"
has "the host checks: the clock" "HROW host01.example.com | Clock offset (chronyc tracking) | 41.23 s fast, synced: yes | 1 s | CRITICAL"
has "  ... the data folder" "HROW host01.example.com | Disk: /opt/application/netapp/data (agent data) | 88% used, 12.0 GB free | 85% | WARNING"
has "  ... podman's storage" "HROW host01.example.com | Disk: /var/lib/containers/storage (podman) | 61% used, 78.0 GB free | 85% | OK"
has "  ... the Console's HTTPS answer and certificate" "answers (200 in 0.3 s), certificate 23 days left | 30 days | WARNING"
has "  ... fapolicyd: the agent's denial counted, not the other" "HROW host01.example.com | fapolicyd | active, 1 denial(s) under the agent's folders since boot | 0 denials | WARNING"
has "  ... the certificate volume and traefik's variables" "HROW host01.example.com | Certificate volume ds_certs | empty: no cert.pem / privkey.pem | cert.pem and privkey.pem | CRITICAL"
has "  ... " "HROW host01.example.com | Traefik VM_SNAME and VM_FQDN | short name EMPTY, FQDN set | both set | WARNING"
has "  ... the ports" "HROW host01.example.com | Port 1 of ds_krakend_1 | refuses connections | answers | CRITICAL"
has "the errors table: counts by kind, no text" "EROW host01.example.com | ds_occmauth_1 | 6 | jwt or token validation x3, signing key x2, connection refused or timed out x1"
for tool in podman chronyc auditctl; do grep -q " $tool " "$H/calls.log" && ok "  ... $tool was called (read only)" || bad "  ... $tool was not called"; done
if grep -E ' podman (start|stop|restart|rm|run|create|kill|pull|pod|system|network)| podman volume (create|rm|prune|import)| podman exec [^ ]+ (sh|bash|rm|touch|tee)| systemctl (start|stop|restart|enable|disable)|auditctl -[a-zA-Z]*[eDRb]' "$H/calls.log" | grep -v 'auditctl -s' >/dev/null; then bad "the profile ran a command that changes something"; else ok "  ... only reads: no start, stop, change of the audit rules"; fi
# certificate files in the volume: no longer empty, and their end date is read too (the earlier of the two counts)
mkdir -p "$H/var/lib/containers/storage/volumes/ds_certs/_data"
: > "$H/var/lib/containers/storage/volumes/ds_certs/_data/cert.pem"; : > "$H/var/lib/containers/storage/volumes/ds_certs/_data/privkey.pem"
run_console
hasnt "a volume with cert.pem and privkey.pem is not empty" "console:certs-empty"
has "  ... the certificate file's 22 days (earlier than the HTTPS answer's 23) is the one reported" "SUMMARY console:cert | warning | host01.example.com: the certificate file in ds_certs expires in 22 days"
has "  ... and the table shows its days left" "HROW host01.example.com | Certificate volume ds_certs | cert.pem and privkey.pem, 22 days left | cert.pem and privkey.pem | WARNING"
rm -f "$H/var/lib/containers/storage/volumes/ds_certs/_data/cert.pem" "$H/var/lib/containers/storage/volumes/ds_certs/_data/privkey.pem"
# ACT's recheck (roles/site_act/tasks/recheck.yml, as it is): every check that had findings runs again, the profile's wording and findings are made
# over ALL of them afterwards, and what cleared is decided by finding id
recheck_ids() {  # recheck_ids before|after|cleared -> that part of the RECHECK line
    local line; line="$(grep -o 'RECHECK before=[^"]*' "$work/out.txt" | head -1)"
    line="${line#RECHECK before=}"
    case "$1" in
        before) printf '%s' "${line%% || after=*}" ;;
        after) line="${line#* || after=}"; printf '%s' "${line%% || cleared=*}" ;;
        cleared) line="${line#* || cleared=}"; printf '%s' "${line%% || again=*}" ;;
        again) printf '%s' "${line#* || again=}" ;;
    esac
}
run_console -e '{"console_recheck": true, "console_more_logs": true}'
rm -f "${H:?}/more_logs" "${H:?}/inspect_fail"
[ "$rc" -eq 0 ] && ok "ACT's recheck runs" || bad "ACT's recheck: exit $rc" 30
b="$(recheck_ids before)"; a="$(recheck_ids after)"
[ -n "$b" ] && [ "$b" = "$a" ] && ok "a recheck of a persistent fault finds the same findings (the profile's own and the generic ones in its wording)" || bad "recheck: before [$b] after [$a]"
[ -z "$(recheck_ids cleared)" ] && ok "  ... and calls nothing cleared (one more error line: the same finding by its id, not a cleared one and a new one)" || bad "recheck: cleared [$(recheck_ids cleared)]"
has "  ... the new count is the one shown" "logged 7 error line(s) in the last 6 h"
hasnt "  ... the old one is not shown as cleared" "logged 6 error line(s)"
has "  ... the generic finding keeps the profile's wording after the recheck" "SUMMARY fapolicyd:down | critical | fapolicyd is not running - the NetApp Console's containers depend on its rules"
has "  ... and its hints that only read" "HINT fapolicyd:down | systemctl is-active fapolicyd // systemctl is-enabled fapolicyd"
run_console -e '{"console_recheck": true, "console_only": ["auditd"]}'
rm -f "${H:?}/more_logs" "${H:?}/inspect_fail"
has "only auditd had findings: its re-run finding has the profile's wording (the containers check did not run again)" "SUMMARY auditd:lost | warning | auditd lost 1203 events - a busy container host can flood auditd; see the job Linux - auditd tuning"
has "  ... and its hint only reads" "HINT auditd:lost | auditctl -s"
has "  ... nothing cleared" '|| cleared= || again='
run_console -e '{"console_recheck": true, "console_fail": "fail"}'
rm -f "${H:?}/more_logs" "${H:?}/inspect_fail"
has "the Console's containers cannot be inspected in a recheck (a timeout): a warning says so" "SUMMARY console:inspect | warning | host01.example.com: the Console's containers could not be inspected"
has "  ... the process that was gone is kept, marked, not called cleared" "SUMMARY console:process-gone:ds_cloudmanager_core | critical | host01.example.com: the container ds_cloudmanager_core shows Up but its process is gone (podman has no process id for it) (not checked again: the Console's containers could not be inspected)"
has "  ... and so is the data folder that is 88% full" "(not checked again: the Console's containers could not be inspected)"
[ -z "$(recheck_ids cleared)" ] && ok "  ... nothing is cleared that was not verified" || bad "recheck: cleared [$(recheck_ids cleared)]"
run_console -e '{"console_recheck": true, "console_fail": "partial"}'
rm -f "${H:?}/more_logs" "${H:?}/inspect_fail"
has "a read cut short (one container of several) is a failed read too" "SUMMARY console:inspect | warning"
[ -z "$(recheck_ids cleared)" ] && ok "  ... and clears nothing" || bad "recheck: cleared [$(recheck_ids cleared)]"
printf fail > "$H/inspect_fail"
run_console
rm -f "${H:?}/inspect_fail"
has "the first read fails: a warning, in the report" "SUMMARY console:inspect | warning | host01.example.com: the Console's containers could not be inspected"
hasnt "  ... and no process is 'gone' for what was not read" "console:process-gone"
# ACT never calls a finding of the profile cleared: it comes from many separate probes, and one that failed or timed out looks like "no finding"
run_console -e '{"console_recheck": true, "console_fake_before": true}'
rm -f "${H:?}/more_logs" "${H:?}/inspect_fail"
[ -z "$(recheck_ids cleared)" ] && ok "a console:* finding that is gone after a fix is not listed as cleared" || bad "recheck: cleared [$(recheck_ids cleared)]"
has "  ... it is listed as not shown again (run the Health check again)" "old disk finding of a mount that is gone"
hasnt "  ... and not as cleared" "cleared: old disk finding"
has "  ... the Re-checked line says so" "not shown again, run the Health check again to see if it is gone: old disk finding of a mount that is gone"
# an address with a user, a password or a token: refused, never shown, kept or sent
prepare console
run_console -vv -e '{"check_containers_console_url": "https://user:pw-secret@console1.example.mil/?token=tok-secret"}'
[ "$rc" -eq 0 ] && ok "an address with a credential: the run goes on" || bad "console URL with a credential: exit $rc" 30
for w in pw-secret tok-secret "user:pw"; do
    hasnt "  ... '$w' is nowhere in the job output (-vv)" "$w"
    hasnt "  ... nor in a command that was run" "$w" "$H/calls.log"
done
has "  ... a warning says it was not probed" "SUMMARY console:url | warning | host01.example.com: the Console's address"
hasnt "  ... no https-down for a probe that was not made" "console:https-down"
hasnt "  ... curl was not called" " curl " "$H/calls.log"
prepare console
run_console -e '{"console_first": ["time", "disk"]}'
has "the time check ran first: its finding gets the Console's wording and the profile does not repeat it" "SUMMARY time:offset | critical | the clock is 41 s off and not within the limit - NetApp Console logins fail while the clock is off"
has "  ... its hint is replaced by a state query (ACT runs a hint), its fix is the fix field" "HINT time:offset | chronyc tracking"
hasnt "  ... no console:clock then" "console:clock"
hasnt "  ... the disk check's finding is replaced by the profile's (one id per mount, the worse severity)" "SUMMARY disk:"
run_console -e '{"check_containers_profiles": []}'
has "check_containers_profiles [] : no profile - nothing is added" 'CHECKS containers"'
hasnt "  ... no Console finding, no sections" "console:"
run_console -e '{"check_containers_console_run_checks": []}'
has "check_containers_console_run_checks [] : fapolicyd and auditd not added" 'CHECKS containers"'
prepare fleet
run_console
hasnt "a host without ds_* containers is left alone: no Console finding" "console:"
hasnt "  ... and has no Console data" "SECTIONS"
run_console --check
[ "$rc" -eq 0 ] && ok "the profile in a dry run (Check mode)" || bad "the profile in Check mode: exit $rc" 30

if [ "$fails" -eq 0 ]; then echo "all $oks containers checks passed"; else echo "$fails of $((oks + fails)) containers check(s) FAILED"; exit 1; fi
