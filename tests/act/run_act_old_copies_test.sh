#!/usr/bin/env bash
# Old copies of ACT (1.53.0): playbooks/act_old_copies.yml and fapolicyd_denials.yml (ACT off) on UBI containers over the
# podman connection. web01 (UBI 9): an old act at /usr/local/bin/act and jdoe's saved model gemini-4-argon (with a
# made-up key); db01: /usr/local/bin/act a link to /opt/act/act already; with ansible-core 2.16 also old01 (UBI 8: only
# Python 3.6, no python3) - the module runs there, the old act is not linked (typing act would fail).
#   bash tests/act/run_act_old_copies_test.sh
# Needs: podman (CONTAINER_ENGINE for a wrapper), registry.access.redhat.com/ubi9/ubi and ubi8/ubi, the containers.podman
# collection (ACT_TEST_COLLECTIONS, else installed into a temporary folder from tests/patch/requirements.yml).
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
engine="${CONTAINER_ENGINE:-$(command -v podman)}"
web="sa-act-web-$$" db="sa-act-db-$$" old="sa-act-old-$$"
key="sk-test-made-up-4f1c9a7e2b"
work="$(mktemp -d)"
cleanup() { "$engine" rm -f -t 0 "$web" "$db" "$old" >/dev/null 2>&1; [ -n "${ACT_TEST_KEEP:-}" ] && cp "$work/out.txt" "$ACT_TEST_KEEP"; rm -rf "$work"; }
trap cleanup EXIT
fails=0

with36=0
"$ap" --version | head -1 | grep -q 'core 2\.16\.' && with36=1
cols="${ACT_TEST_COLLECTIONS:-}"
if [ -z "$cols" ]; then
    cols="$work/collections"
    ansible-galaxy collection install -r "$repo/tests/patch/requirements.yml" -p "$cols" >/dev/null || { echo "ABORT - could not install containers.podman"; exit 1; }
fi
start() { "$engine" run -d --name "$1" "$2" sleep infinity >/dev/null || { echo "ABORT - could not start $2"; exit 1; }; }
start "$web" registry.access.redhat.com/ubi9/ubi:latest
start "$db" registry.access.redhat.com/ubi9/ubi:latest
[ $with36 = 1 ] && start "$old" registry.access.redhat.com/ubi8/ubi:8.10
# an old act, as someone put it there by hand
oldact='#!/usr/bin/env python3
ACT_VERSION = "0.4.2"  # the old one
import sys; print("act 0.4.2 (old)")'
put_old() { "$engine" exec -i "$1" sh -c 'mkdir -p /usr/local/bin; rm -f /usr/local/bin/act; cat > /usr/local/bin/act; chmod 755 /usr/local/bin/act' <<< "$oldact"; }
put_old "$web"
"$engine" exec -i "$web" sh -c "useradd -m jdoe && mkdir -p /home/jdoe/.config/act && cat > /home/jdoe/.config/act/config.json \
    && chown -R jdoe: /home/jdoe/.config && chmod 600 /home/jdoe/.config/act/config.json" \
    <<< "{\"provider\": \"genai\", \"genai\": {\"key\": \"$key\", \"model\": \"gemini-4-argon\"}}"
"$engine" exec -i "$db" sh -c 'mkdir -p /opt/act /usr/local/bin && cat > /opt/act/act && chmod 755 /opt/act/act && ln -s /opt/act/act /usr/local/bin/act' < "$repo/vendor/act/act"
[ $with36 = 1 ] && put_old "$old"

c="ansible_connection: containers.podman.podman, ansible_podman_executable: $engine, ansible_become: false"
{
    echo "all:"
    echo "  children:"
    echo "    linux:"
    echo "      hosts:"
    echo "        web01.example.mil: {ansible_host: $web, ansible_python_interpreter: /usr/bin/python3, $c}"
    echo "        db01.example.mil: {ansible_host: $db, ansible_python_interpreter: /usr/bin/python3, $c}"
    [ $with36 = 1 ] && echo "        old01.example.mil: {ansible_host: $old, ansible_python_interpreter: /usr/libexec/platform-python, $c}"
} > "$work/inv.yml"
mkdir -p "$work/web"

# run NAME PLAYBOOK [args...] -- TEXT... (a TEXT starting with ! must NOT be in the output); the job must pass; -v, so a
# key in any result would show
run() {
    local tname="$1" pb="$2"; shift 2
    local args=() texts=() seen=0
    for a in "$@"; do if [ "$a" = "--" ]; then seen=1; elif [ $seen -eq 0 ]; then args+=("$a"); else texts+=("$a"); fi; done
    (cd "$repo" && ANSIBLE_COLLECTIONS_PATH="$cols" ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_DEPRECATION_WARNINGS=False \
        "$ap" -v -i "$work/inv.yml" "playbooks/$pb" -e report_web_dest="$work/web" "${args[@]}") > "$work/out.txt" 2>&1
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo "FAIL - $tname: exit $rc"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1
    fi
    if grep -qF -- "$key" "$work/out.txt" "$work"/web/*/*.json 2>/dev/null; then
        echo "FAIL - $tname: the key is in the output"; fails=$((fails + 1)); return 1
    fi
    local t
    for t in "${texts[@]}"; do
        if [ "${t:0:1}" = "!" ]; then
            ! grep -qF -- "${t:1}" "$work/out.txt" || { echo "FAIL - $tname: output has: ${t:1}"; fails=$((fails + 1)); return 1; }
        else
            grep -qF -- "$t" "$work/out.txt" || { echo "FAIL - $tname: output lacks: $t"; grep -E '"msg"|ERROR|fatal' "$work/out.txt" | head -8 | cut -c1-300; fails=$((fails + 1)); return 1; }
        fi
    done
    echo "ok   - $tname"
}
check() { if [ "$2" = "$3" ]; then echo "ok   - $1"; else echo "FAIL - $1: got [$2], expected [$3]"; fails=$((fails + 1)); fi; }
on() { "$engine" exec "$1" sh -c "$2" 2>&1; }

run "Check: says what it would do" act_old_copies.yml --check \
    -- "/usr/local/bin/act was an old copy (ACT 0.4.2): would be linked to /opt/act/act" \
       "user jdoe's saved model is gemini-4-argon: \`act\` uses it when typed; the jobs pass their own." \
       '!db01.example.mil: /usr/local/bin/act'
check "  ...nothing changed (still a file, no /opt/act)" "$(on "$web" 'test -L /usr/local/bin/act && echo link || echo file; test -e /opt/act && echo opt || echo no-opt')" "file
no-opt"

run "Run: linked, the old one kept, the saved model reported" act_old_copies.yml \
    -- "/usr/local/bin/act was an old copy (ACT 0.4.2): linked to /opt/act/act (ACT 0.6.25), the old one kept at /opt/act/old/act." \
       "web01.example.mil: user jdoe's saved model is gemini-4-argon" '!db01.example.mil: /usr/local/bin/act'
check "  ...the link" "$(on "$web" 'readlink /usr/local/bin/act')" "/opt/act/act"
check "  ...act --version is the new one" "$(on "$web" 'HOME=/nonexistent act --version | head -1')" "act 0.6.25"
check "  ...the backup: one file, root's 0700 folder" "$(on "$web" 'stat -c %a /opt/act/old; ls /opt/act/old | wc -l; grep -c "0.4.2" /opt/act/old/act.*')" "700
1
2"
check "  ...jdoe's settings never written" "$(on "$web" 'grep -c gemini-4-argon\" /home/jdoe/.config/act/config.json; stat -c %a /home/jdoe/.config/act/config.json')" "1
600"
check "  ...db01 left alone" "$(on "$db" 'readlink /usr/local/bin/act; test -e /opt/act/old && echo old || echo no-old')" "/opt/act/act
no-old"
check "  ...the report's ACT old copies section, web01's line in it" \
    "$(python3 -c 'import glob, json, sys
r = json.load(open(glob.glob(sys.argv[1] + "/*/latest.json")[0]))
r = r.get("report", r)
s = [x for x in r["sections"] if x.get("title") == "ACT old copies"]
print(len(s), any(row[0] == "web01.example.mil" and "was an old copy (ACT 0.4.2)" in row[1] for row in s[0]["rows"]))' "$work/web")" "1 True"

run "Again: nothing to do (old01 is said again: its copy stays)" act_old_copies.yml -l web01.example.mil,db01.example.mil \
    -- '!was an old copy' "web01.example.mil: user jdoe's saved model"

# ACT off in a job that can run ACT: the check still runs (here fapolicyd denials, which then reads and asks nothing)
put_old "$web"
run "ACT off (fapolicyd denials): the old copy still linked" fapolicyd_denials.yml -l web01.example.mil -e site_act_enabled=false \
    -- "ACT is off (site_act_enabled false)" "was an old copy (ACT 0.4.2): linked to /opt/act/act"
check "  ...linked, two backups" "$(on "$web" 'readlink /usr/local/bin/act; ls /opt/act/old | wc -l')" "/opt/act/act
2"

put_old "$web"
run "report: only said" act_old_copies.yml -l web01.example.mil -e site_act_old_bin=report \
    -- "left as it is (site_act_old_bin: report)"
check "  ...still the old file" "$(on "$web" 'test -L /usr/local/bin/act && echo link || echo file')" "file"
run "delete: kept, then removed" act_old_copies.yml -l web01.example.mil -e site_act_old_bin=delete \
    -- "deleted, the old one kept at /opt/act/old/act."
check "  ...gone" "$(on "$web" 'test -e /usr/local/bin/act && echo there || echo gone')" "gone"

run "the saved model rewritten when asked" act_old_copies.yml -l web01.example.mil -e site_act_fix_user_model=true \
    -- "user jdoe's saved model was gemini-4-argon: now gemini-4-argon-withtools"
check "  ...only that key, mode and owner kept, a backup beside it" \
    "$(on "$web" "python3 -c 'import json; d=json.load(open(\"/home/jdoe/.config/act/config.json\")); print(d[\"genai\"][\"model\"], d[\"genai\"][\"key\"] == \"$key\")'; stat -c '%a %U' /home/jdoe/.config/act/config.json; ls /home/jdoe/.config/act | grep -c '\.bak$'")" \
    "gemini-4-argon-withtools True
600 jdoe
1"

if [ $with36 = 1 ]; then
    run "old01, Python 3.6 only: looked at, not linked" act_old_copies.yml -l old01.example.mil \
        -- "old01.example.mil" "not linked (the new ACT needs Python 3.8 or newer)"
    check "  ...still the old file" "$(on "$old" 'test -L /usr/local/bin/act && echo link || echo file')" "file"
fi

[ $fails -eq 0 ] && echo "all ACT old copies scenarios passed" || { echo "$fails failed"; exit 1; }
