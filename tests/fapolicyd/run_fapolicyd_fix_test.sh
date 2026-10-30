#!/usr/bin/env bash
# playbooks/fapolicyd.yml, getting fapolicyd running (roles/fapolicyd_fix): fake rpm / systemctl / journalctl / fapolicyd-cli / selinuxenabled / restorecon, each
# host told apart by its python (FAKE_FA_HOST) and its own fapolicyd folder (fapolicyd_fix_dir) under the work folder.
# fapolicyd failed (a trust file it cannot read, db_max_size too small, a very large trust file), a healthy host, one
# near its database's limit (auto possible, a group-writable trust file, an SELinux label), fapolicyd not installed, a
# host that cannot be reached; the fixes proposed; a Check run; the fixes made (the approver's own db_max_size, the
# restart); approved again once fixed (not proposed here); a line the job never gives (nothing changed); a host where
# fapolicyd breaks after the fix (everything put back); and the restart script's way back on its own.
# Runs nothing for real: every tool it touches is fake.      bash tests/fapolicyd/run_fapolicyd_fix_test.sh
# (FAPOLICYD_TEST_KEEP=1 keeps the work folder)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ap="${ANSIBLE_PLAYBOOK:-ansible-playbook}"
py="${FAPOLICYD_TEST_PYTHON:-$(command -v python3)}"
work="$(mktemp -d)"
trap '[ -n "${FAPOLICYD_TEST_KEEP:-}" ] && echo "kept: $work" || rm -rf "$work"' EXIT
fails=0
ok() { echo "ok   - $1"; }
bad() { echo "FAIL - $1"; fails=$((fails + 1)); [ -n "${2:-}" ] && tail -n "$2" "$work/out.txt"; }
me="$(id -un)"; mygroup="$(id -gn)"

# ---- the fake hosts: their fapolicyd folders --------------------------------------------------------------------
mkdir -p "$work/bin" "$work/state" "$work/calls"
for h in fa01 fa02 fa03; do
    d="$work/$h/etc"; mkdir -p "$d/trust.d"
    printf 'permissive = 0\nuid = fapolicyd\ngid = fapolicyd\ndb_max_size = %s\ntrust = rpmdb,file\n' \
        "$([ $h = fa03 ] && echo 100 || echo 50)" > "$d/fapolicyd.conf"
    printf '/usr/bin/tool 1 0000\n' > "$d/fapolicyd.trust"; chmod 644 "$d/fapolicyd.trust" "$d/fapolicyd.conf"
    chmod 755 "$d" "$d/trust.d"
done
printf '/opt/app/run 10 abc\n' > "$work/fa01/etc/trust.d/app"; chmod 600 "$work/fa01/etc/trust.d/app"
head -c 2200000 /dev/zero | tr '\0' 'x' | fold -w 100 > "$work/fa01/etc/trust.d/big-list"; chmod 644 "$work/fa01/etc/trust.d/big-list"
printf '/srv/x 1 0\n' > "$work/fa03/etc/trust.d/x"; chmod 664 "$work/fa03/etc/trust.d/x"
printf 'fapolicyd 1.3 - sizes nothing itself\n' > "$work/fapolicyd-13"
printf 'fapolicyd 1.4 - autosize: utilisation\n' > "$work/fapolicyd-14"
cat > "$work/hosts.json" <<EOJ
{"fa01": {"installed": true, "active": "failed", "enabled": "disabled", "restarts": ["active"], "rpm_files": 3000,
          "journal": ["Cannot read $work/fa01/etc/trust.d/app (Permission denied)", "db_max_size needs to be increased",
                      "fapolicyd.service: Failed with result 'exit-code'."]},
 "fa02": {"installed": true, "active": "active", "rpm_files": 3000, "journal": ["Starting fapolicyd"]},
 "fa03": {"installed": true, "active": "active", "restarts": ["failed", "active"], "rpm_files": 3000, "selinux": true,
          "journal": ["Trust database at 92% capacity - might want to increase db_max_size setting"]},
 "fa04": {"installed": false}}
EOJ
cat > "$work/bin/fake.py" <<'EOP'
import json, os, sys
tool, args = os.path.basename(sys.argv[1]), sys.argv[2:]
work = os.environ["FAKE_FA_WORK"]
name = os.environ.get("FAKE_FA_HOST", "")
hosts = json.load(open(work + "/hosts.json"))
h = hosts.get(name, {})
if args == ["--is-fake"]:
    print("FAKE " + tool); sys.exit(0)
with open(os.path.join(work, "calls", name or "none"), "a") as f:
    f.write(tool + " " + " ".join(args) + "\n")
state_file = os.path.join(work, "state", name + ".active")
def state():
    return open(state_file).read().strip() if os.path.exists(state_file) else h.get("active", "inactive")
if tool == "rpm":
    if args == ["-q", "fapolicyd"]:
        print("fapolicyd-1.3.2-1.el8.x86_64" if h.get("installed") else "package fapolicyd is not installed")
        sys.exit(0 if h.get("installed") else 1)
    if args[:1] == ["-q"] and args[-1] == "fapolicyd":
        u, g = os.environ["FAKE_FA_USER"], os.environ["FAKE_FA_GROUP"]
        print("drwxr-x--- %s %s /etc/fapolicyd\n-rw-r--r-- %s %s /etc/fapolicyd/fapolicyd.trust\n"
              "drwxr-x--- %s %s /etc/fapolicyd/trust.d\n-rw-r--r-- %s %s /etc/fapolicyd/fapolicyd.conf" % ((u, g) * 4))
        sys.exit(0)
    if args[:1] == ["-qa"]:
        sys.stdout.write("/usr/bin/x\n" * h.get("rpm_files", 0)); sys.exit(0)
    sys.exit(1)
if tool == "systemctl":
    if args[0] == "is-enabled":
        en = os.path.join(work, "state", name + ".enabled")
        print(open(en).read().strip() if os.path.exists(en) else h.get("enabled", "enabled")); sys.exit(0)
    if args[0] in ("enable", "disable"):
        open(os.path.join(work, "state", name + ".enabled"), "w").write(args[0] + "d"); sys.exit(0)
    if args[0] == "is-active":
        s = state(); "--quiet" in args or print(s); sys.exit(0 if s == "active" else 3)
    if args[0] == "restart":
        seq = h.get("restarts", ["active"])
        n = len([l for l in open(os.path.join(work, "calls", name)) if l.startswith("systemctl restart")])
        s = seq[min(n, len(seq)) - 1]
        open(state_file, "w").write(s); sys.exit(0 if s == "active" else 1)
    if args[0] == "stop":
        open(state_file, "w").write("inactive"); sys.exit(0)
    if args[0] == "show":
        sys.exit(0)
    sys.exit(0)
if tool == "journalctl":
    print("\n".join(h.get("journal", []))); sys.exit(0)
if tool == "selinuxenabled":
    sys.exit(0 if h.get("selinux") else 1)
if tool == "restorecon":
    if "-n" in args:
        print("Would relabel %s/%s/etc/trust.d/x from unconfined_u:object_r:user_tmp_t:s0 to system_u:object_r:fapolicyd_config_t:s0" % (work, name))
    sys.exit(0)
if tool == "ausearch":                      # the rules part reads the blocks: none here
    print("<no matches>", file=sys.stderr); sys.exit(1)
if tool == "fagenrules":
    sys.exit(0)
if tool == "fapolicyd-cli":
    if "--check-config" in args:
        conf = open(os.path.join(work, name, "etc", "fapolicyd.conf")).read()
        if "db_max_size = 13" in conf:              # this test's bad value
            print("Value 13 is not liked here - line 4\nConfiguration errors reported"); sys.exit(1)
        print("Daemon config is OK")
    sys.exit(0)
sys.exit(0)
EOP
for t in rpm systemctl journalctl fapolicyd-cli selinuxenabled restorecon ausearch fagenrules; do
    printf '#!/bin/sh\nexec "%s" "%s" "$0" "$@"\n' "$py" "$work/bin/fake.py" > "$work/bin/$t"; chmod 755 "$work/bin/$t"
    if [ "$(PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
        echo "ABORT - $t is not the fake; not running anything"; exit 1
    fi
done
inv() {
    echo "all:"; echo "  hosts:"
    for h in fa01 fa02 fa03 fa04; do
        printf '#!/bin/sh\nFAKE_FA_HOST=%s exec "%s" "$@"\n' "$h" "$py" > "$work/py-$h"; chmod 755 "$work/py-$h"
        echo "    $h.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $work/py-$h,"
        echo "                     fapolicyd_dir: $work/$h/etc, fapolicyd_fix_binary: $work/fapolicyd-$([ $h = fa03 ] && echo 14 || echo 13)}"
    done
    echo "    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh}"
}
inv > "$work/inv.yml"
cat > "$work/vars.json" <<EOJ
{"report_web_dest": "$work/site", "site_syslog": false, "fapolicyd_fix_large_mb": 1, "fapolicyd_wait": 2}
EOJ

run() {  # run HOST|all [ansible-playbook args...] -> $work/out.txt, $rc (one host: its fakes know it in the restart script too)
    local h="$1"; shift
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" FAKE_FA_USER="$me" FAKE_FA_GROUP="$mygroup" \
        FAKE_FA_HOST="$([ "$h" = all ] || echo "$h")" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_TIMEOUT=3 \
        "$ap" -i "$work/inv.yml" playbooks/fapolicyd.yml -e "@$work/vars.json" \
        $([ "$h" = all ] || echo "-l $h.example.mil") "$@") > "$work/out.txt" 2>&1
    rc=$?
}
report() {
    "$py" -c 'import json, glob, sys; f = sorted(glob.glob(sys.argv[1] + "/*/latest.json")); print(json.dumps(json.load(open(f[-1])))) if f else print("{}")' "$work/site"
}
check() {  # check "label" PYTHON-EXPRESSION-ON-r (the report) and s (its sections by title)
    if report | "$py" -c 'import json, sys; r = json.load(sys.stdin); r = r.get("report", r); s = {x["title"]: x for x in r.get("sections", [])}; sys.exit(0 if eval(sys.argv[1]) else 1)' "$2"; then
        ok "$1"; else bad "$1: $(report | head -c 2500)"; fi
}
mode() { stat -c %a "$1"; }
A1="trust file access: $work/fa01/etc/trust.d/app $me:$mygroup 0600 -> $me:$mygroup 0644"

# ---- read only: every host -------------------------------------------------------------------------------------
run all
[ "$rc" = 0 ] || [ "$rc" = 4 ] && ok "read only: the job runs (rc $rc: gone01 cannot be reached)" || bad "read only: rc $rc" 40
check "the title: fixes to approve, fapolicyd not running on 1" '"fix(es) to approve on 2 host(s)" in r["title"] and "fapolicyd not running on 1" in r["title"] and r["status"] == "critical"'
check "fa01: its unreadable trust file, db_max_size 50 -> a number (no auto on 1.3), its restart" \
      'any(x.startswith("trust file access: ") and "fa01/etc/trust.d/app" in x and "0600 -> " in x for x in s["Changes to approve"]["lines"]) and any(x.startswith("db_max_size: 50 -> ") and "auto" not in x.split("(")[0] for x in s["Changes to approve"]["lines"]) and any(x.startswith("restart fapolicyd (it is failed)") for x in s["Changes to approve"]["lines"]) and "permissive: 0 -> 1" in s["Changes to approve"]["lines"] and "enable fapolicyd at boot (it is disabled)" in s["Changes to approve"]["lines"]'
check "fa03: db_max_size 100 -> auto (1.4), its group-writable trust file, its SELinux label" \
      'any(x.startswith("db_max_size: 100 -> auto") for x in s["Changes to approve"]["lines"]) and any("fa03/etc/trust.d/x" in x and "0664 -> " in x for x in s["Changes to approve"]["lines"]) and any(x.startswith("SELinux label: ") and "fapolicyd_config_t" in x for x in s["Changes to approve"]["lines"])'
check "the why: fapolicyd's own words (Cannot read, db_max_size needs to be increased, 92% capacity)" \
      '" ".join(row[2] for row in s["Why each change"]["rows"]).count("fapolicyd says") >= 3'
check "for a person: fa01's very large trust file" 'any("big-list: a large trust file" in x for x in s["For a person"]["lines"])'
check "fa02 has nothing; fa04 not installed; gone01 not read" \
      'not any("fa02" in x for x in s["Changes to approve"]["lines"]) and "fa04.example.mil" in s["fapolicyd not installed"]["lines"][0] and "gone01.example.mil" in s["Not read"]["lines"][0]'
check "fa01's journal in the report" 'any("db_max_size needs to be increased" in x for x in s["fa01.example.mil: fapolicyd\x27s journal"]["lines"])'
[ "$(mode "$work/fa01/etc/trust.d/app")" = 600 ] && grep -q '^db_max_size = 50' "$work/fa01/etc/fapolicyd.conf" && ok "read only: nothing changed" || bad "read only changed something"

# ---- Check: what it would change -------------------------------------------------------------------------------
cat > "$work/ok1.json" <<EOJ
{"fapolicyd_approved": ["$A1", "db_max_size: 50 -> 200", "enable fapolicyd at boot (it is disabled)", "permissive: 0 -> 1", "restart fapolicyd (it is failed)"]}
EOJ
run fa01 -e "@$work/ok1.json" --check
[ "$rc" = 0 ] && ok "Check with the fixes approved: the job ends well" || bad "Check: rc $rc" 40
grep -q 'would change - made: .*trust.d/app.*db_max_size: 50 -> 2' "$work/out.txt" && ok "  ... it says what it would fix (Check writes no report page)" || bad "Check: no 'would change'" 30
[ "$(mode "$work/fa01/etc/trust.d/app")" = 600 ] && grep -q '^db_max_size = 50' "$work/fa01/etc/fapolicyd.conf" && ! grep -q 'systemctl restart' "$work/calls/fa01" \
    && ok "  ... and nothing changed: the file, fapolicyd.conf, no restart" || bad "Check changed something"

# ---- the fixes made: the approver's own db_max_size, the restart ------------------------------------------------
run fa01 -e "@$work/ok1.json"
[ "$rc" = 0 ] && ok "the fixes approved: the job ends well" || bad "fixes: rc $rc" 40
[ "$(mode "$work/fa01/etc/trust.d/app")" = 644 ] && ok "  ... the trust file is 0644 (the package's mode)" || bad "the trust file is $(mode "$work/fa01/etc/trust.d/app")"
grep -q '^db_max_size = 200$' "$work/fa01/etc/fapolicyd.conf" && [ "$(grep -c db_max_size "$work/fa01/etc/fapolicyd.conf")" = 1 ] \
    && grep -q '^permissive = 1$' "$work/fa01/etc/fapolicyd.conf" \
    && ok "  ... db_max_size = 200, the approver's own number, once; permissive = 1 (it starts without blocking)" || bad "fapolicyd.conf: $(cat "$work/fa01/etc/fapolicyd.conf")"
[ "$(cat "$work/state/fa01.active")" = active ] && ok "  ... fapolicyd restarted and running" || bad "fapolicyd: $(cat "$work/state/fa01.active" 2>&1)"
[ "$(cat "$work/state/fa01.enabled" 2>&1)" = enabled ] && ok "  ... and enabled at boot" || bad "not enabled: $(cat "$work/state/fa01.enabled" 2>&1)"
grep -q 'fapolicyd-cli --check-config' "$work/calls/fa01" && grep -q 'systemctl reset-failed fapolicyd' "$work/calls/fa01" \
    && ok "  ... the new fapolicyd.conf checked (--check-config), systemd's start limit reset before the restart" || bad "no check-config or reset-failed: $(cat "$work/calls/fa01")"
check "  ... the report: changed, failed -> active; left to approve on fa01: only enforcing again, for a quiet day" \
      's["Getting fapolicyd running: what this run did"]["rows"][0][1] == "changed" and s["Getting fapolicyd running: what this run did"]["rows"][0][3] == "failed -> active" and s["Changes to approve"]["lines"] == ["permissive: 1 -> 0"]'
if ls -d /tmp/*.fapolicyd-fix "${TMPDIR:-/tmp}"/*.fapolicyd-fix >/dev/null 2>&1; then bad "the way-back folder was left behind"; else ok "  ... the way-back folder removed"; fi

# ---- approved again once fixed: not proposed here, nothing changed ---------------------------------------------
run fa01 -e "@$work/ok1.json"
[ "$rc" = 0 ] && check "approved again once fixed: not proposed here, unchanged" \
    's["Getting fapolicyd running: what this run did"]["rows"][0][1] == "unchanged" and s["Getting fapolicyd running: what this run did"]["rows"][0][4].count("\n") == 4' || bad "again: rc $rc" 40

# ---- a line the job never gives: nothing changed, the host fails after the report -------------------------------
chmod 600 "$work/fa01/etc/trust.d/app"
run fa01 -e "{\"fapolicyd_approved\": [\"$A1\", \"rm -rf /\"]}"
[ "$rc" = 2 ] && [ "$(mode "$work/fa01/etc/trust.d/app")" = 600 ] && grep -q 'refused: a line approved is not one this job gives' "$work/out.txt" \
    && ok "a line the job never gives: nothing changed, the host fails (exit 2)" || bad "refused: rc $rc, mode $(mode "$work/fa01/etc/trust.d/app")" 30
check "  ... the report says why" 's["Getting fapolicyd running: what this run did"]["rows"][0][1] == "refused" and "rm -rf /: not a line this job gives" in s["Getting fapolicyd running: what this run did"]["rows"][0][5]'

# ---- a line copied with the host name after it: refused, not silently "not proposed" ----------------------------
chmod 600 "$work/fa01/etc/trust.d/app"
run fa01 -e "{\"fapolicyd_approved\": [\"$A1 (fa01.example.mil)\"]}"
[ "$rc" = 2 ] && [ "$(mode "$work/fa01/etc/trust.d/app")" = 600 ] && grep -q 'not exactly as the report gives it' "$work/out.txt" \
    && ok "a line copied with a host name after it: refused, nothing changed (exit 2)" || bad "suffix: rc $rc" 30
check "  ... the report's lines are exactly what to paste (no host after them)" 'not any("(fa01" in x for x in s["Changes to approve"]["lines"])'

# ---- fapolicyd breaks after the fix on a host where it ran: everything put back --------------------------------
X3="trust file access: $work/fa03/etc/trust.d/x $me:$mygroup 0664 -> $me:$mygroup 0644"
run fa03 -e "{\"fapolicyd_approved\": [\"$X3\", \"db_max_size: 100 -> auto\"]}"
[ "$rc" = 2 ] && ok "fa03: fapolicyd fails after the restart - the host fails after the report (exit 2)" || bad "fa03: rc $rc" 40
[ "$(mode "$work/fa03/etc/trust.d/x")" = 664 ] && grep -q '^db_max_size = 100$' "$work/fa03/etc/fapolicyd.conf" && [ "$(cat "$work/state/fa03.active")" = active ] \
    && ok "  ... the trust file and fapolicyd.conf put back, fapolicyd running again" || bad "put back: mode $(mode "$work/fa03/etc/trust.d/x"), $(grep db_max "$work/fa03/etc/fapolicyd.conf"), $(cat "$work/state/fa03.active")"
check "  ... the report: rolled back, why" 's["Getting fapolicyd running: what this run did"]["rows"][0][1] == "rolled back" and "fapolicyd failed after the fix: everything put back, fapolicyd active" in s["Getting fapolicyd running: what this run did"]["rows"][0][5]'

# ---- auto where this fapolicyd cannot: refused on that host ------------------------------------------------------
# fa02 down for these two (the estimate counts only while fapolicyd is down), its db_max_size 1
cp "$work/fa02/etc/fapolicyd.conf" "$work/fa02.conf.bak"; sed -i 's/^db_max_size = 50/db_max_size = 1/' "$work/fa02/etc/fapolicyd.conf"
echo failed > "$work/state/fa02.active"
run fa02 -e '{"fapolicyd_approved": ["db_max_size: 1 -> auto"]}'
[ "$rc" = 2 ] && grep -q '^db_max_size = 1$' "$work/fa02/etc/fapolicyd.conf" && grep -q 'does not size its database itself' "$work/out.txt" \
    && ok "auto on fapolicyd 1.3: refused on that host, fapolicyd.conf as it was" || bad "auto on 1.3: rc $rc" 30
cp "$work/fa02.conf.bak" "$work/fa02/etc/fapolicyd.conf"

# ---- a db_max_size fapolicyd does not take: --check-config fails, fapolicyd.conf put back, no restart -------------
sed -i 's/^db_max_size = 50/db_max_size = 1/' "$work/fa02/etc/fapolicyd.conf"; : > "$work/calls/fa02"; echo failed > "$work/state/fa02.active"
run fa02 -e '{"fapolicyd_approved": ["db_max_size: 1 -> 13"]}'
[ "$rc" = 2 ] && grep -q '^db_max_size = 1$' "$work/fa02/etc/fapolicyd.conf" && ! grep -q 'systemctl restart' "$work/calls/fa02" \
    && grep -q 'failed: .*what was changed is put back' "$work/out.txt" \
    && ok "a db_max_size fapolicyd does not take: --check-config fails, fapolicyd.conf put back, not restarted" \
    || bad "check-config: rc $rc, $(grep db_max "$work/fa02/etc/fapolicyd.conf")" 30
cp "$work/fa02.conf.bak" "$work/fa02/etc/fapolicyd.conf"

# ---- a stopped host through the workflow: the check plans getting it running, the approved job makes it ----------
# fa01 as it was at first: failed, disabled, a trust file it cannot read, db_max_size 50
printf 'permissive = 0\nuid = fapolicyd\ngid = fapolicyd\ndb_max_size = 50\ntrust = rpmdb,file\n' > "$work/fa01/etc/fapolicyd.conf"
chmod 600 "$work/fa01/etc/trust.d/app"; echo failed > "$work/state/fa01.active"; echo disabled > "$work/state/fa01.enabled"; : > "$work/calls/fa01"
run fa01 -e '{"fapolicyd_workflow": true}'
[ "$rc" = 2 ] && grep -q 'NEEDS APPROVAL - ' "$work/out.txt" && [ "$(mode "$work/fa01/etc/trust.d/app")" = 600 ] \
    && ok "the workflow's check on a stopped host: NEEDS APPROVAL, nothing changed" || bad "workflow check: rc $rc" 40
check "  ... its report: what Approve makes - enabled, permissive, started - and enforcing held back" \
      'all(any(x == "fa01.example.mil: " + l for x in s["Approve makes these"]["lines"]) for l in ("enable fapolicyd at boot (it is disabled)", "permissive: 0 -> 1", "restart fapolicyd (it is failed)")) and not any("permissive: 1 -> 0" in x for x in s["Approve makes these"]["lines"])'
"$py" - "$work/out.txt" "$work/plan.json" <<'EOP'
import json, re, sys
m = re.search(r"NEEDS APPROVAL - [^\n]*", open(sys.argv[1]).read())
text = re.sub(r'["}\s]+$', "", m.group(0).split("deny it to make none.", 1)[1])   # 2.16 prints {"msg": "..."}
plan = {}
for part in text.split(" || "):
    host, _, lines = part.partition(": ")
    plan[host.strip()] = [x.strip() for x in lines.split(" | ") if x.strip()]
json.dump({"fapolicyd_workflow_plan": plan}, open(sys.argv[2], "w"))
EOP
run fa01 -e "@$work/plan.json"
[ "$rc" = 0 ] && [ "$(cat "$work/state/fa01.active")" = active ] && [ "$(cat "$work/state/fa01.enabled")" = enabled ] \
    && grep -q '^permissive = 1$' "$work/fa01/etc/fapolicyd.conf" && [ "$(mode "$work/fa01/etc/trust.d/app")" = 644 ] \
    && ok "  ... the approved job: fa01 running, permissive, enabled at boot, its trust file readable" || bad "workflow approved: rc $rc, $(cat "$work/state/fa01.active" "$work/state/fa01.enabled" 2>&1)" 40
check "  ... and its report offers only enforcing again, for a quiet day" 's["Changes to approve"]["lines"] == ["permissive: 1 -> 0"]'

# ---- getting it running fails: the trust and rules changes approved beside it wait ---------------------------------
X3="trust file access: $work/fa03/etc/trust.d/x $me:$mygroup 0664 -> $me:$mygroup 0644"
: > "$work/calls/fa03"; echo active > "$work/state/fa03.active"
run fa03 -e "{\"fapolicyd_approved\": [\"$X3\", \"db_max_size: 100 -> auto\", \"trust file: /opt/nothing size 1 sha256 $(printf 'a%.0s' $(seq 1 64))\"]}"
[ "$rc" = 2 ] && ! grep -q 'fapolicyd-cli --file add' "$work/calls/fa03" && ok "a fix rolled back: the trust and rules lines beside it not made" || bad "rules made after a failed fix: rc $rc" 30
check "  ... the report says they wait" 'any("were not made - getting fapolicyd running failed first" in x for x in s["For a person"]["lines"])'

# ---- the restart script's way back, on its own, sent inline as the job sends it: a program no longer starts --------
printf '#!/bin/sh\nexit 126\n' > "$work/bin/denied-python"; chmod 755 "$work/bin/denied-python"
inline() {  # inline BACK-BODY ARGS...: back() and "set --" before the script, one sh -c - as apply.yml sends it
    local body="$1"; shift
    PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" sh -c "back() { $body; }
set -- $*
$(cat "$repo/roles/fapolicyd_fix/files/fapolicyd_fix_reload.sh")"
}
echo active > "$work/state/fa02.active"
out="$(FAKE_FA_HOST=fa02 inline "echo put-back >> $work/putback.txt" restart active "$work/bin/denied-python" 1)"
echo "$out" | grep -q '^RESULT rolled-back programs-denied inactive' && echo "$out" | grep -q '^BACK ok' && grep -q put-back "$work/putback.txt" \
    && ok "the restart script: python denied after the restart - put back, fapolicyd stopped (never left unmanageable)" || bad "script: $out"
echo active > "$work/state/fa02.active"
out="$(FAKE_FA_HOST=fa02 inline "false" restart active "$work/bin/denied-python" 1)"
echo "$out" | grep -q '^BACK failed' && ok "the restart script: a way back that fails says so" || bad "script back failed: $out"
rm -f "$work/putback.txt"
out="$(FAKE_FA_HOST=fa04 inline "echo put-back >> $work/putback.txt" update inactive "$(command -v true)" 1)"
echo "$out" | grep -q '^RESULT still-' && [ ! -e "$work/putback.txt" ] && ok "the restart script: down before, still down - the fixes stay" || bad "script still-down: $out"

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all fapolicyd fix scenarios passed"
