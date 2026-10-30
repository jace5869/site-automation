#!/usr/bin/env bash
# playbooks/fapolicyd_rules.yml: fake rpm / systemctl / journalctl / ausearch / fapolicyd-cli / fagenrules, each host
# told apart by its python (FAKE_FA_HOST) and its own fapolicyd folder (fapolicyd_rules_dir) under the work folder. The
# fakes write what the real ones do (fapolicyd-cli --file add: "path size sha256" lines, root's 0600 file under umask
# 077; fagenrules: compiled.rules from rules.d's rules files, in their order; --check: "No change").
# An enforcing host with denials (a host file: its trust entry; a container's file: the storage rule; a file in a user
# folder and an RPM-owned file: for a person) and a rules file to clean up (a repeat, a wildcard, an RPM-owned file's
# rule, a rule that opens a system folder, a file that sorts after the stock deny rules); a permissive host with
# would-be blocks (and an allow_audit allow, not a block); fapolicyd not installed; a host that cannot be reached. Read
# only; Check; the changes made (the stricter clean-up refused while enforcing); permissive: 1 -> 0; fapolicyd failing
# after a rules change (every file put back); a line the job never gives.
# Runs nothing for real: every tool it touches is fake.      bash tests/fapolicyd/run_fapolicyd_rules_test.sh
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
mkdir -p "$work/bin" "$work/state" "$work/calls" "$work/opt/app"
printf '#!/bin/sh\necho tool\n' > "$work/opt/app/tool"; chmod 755 "$work/opt/app/tool"
printf '#!/bin/sh\necho tool2\n' > "$work/opt/app/tool2"; chmod 755 "$work/opt/app/tool2"
for h in ra01 ra02 ra04; do
    d="$work/$h/etc"; mkdir -p "$d/rules.d" "$d/trust.d"
    printf 'permissive = %s\nuid = fapolicyd\ngid = fapolicyd\ndb_max_size = 100\n' "$([ $h = ra02 ] && echo 1 || echo 0)" > "$d/fapolicyd.conf"
    # 13 rules first: the deny rule's number is 16 - 0x10 as the kernel writes it, 10 if read as decimal (an allow rule)
    { printf 'allow perm=any uid=0 trust=1 : all\n'; for n in $(seq 1 12); do printf 'allow perm=open uid=0 : dir=/srv/filler%s/\n' "$n"; done; } \
        > "$d/rules.d/10-languages.rules"
    printf 'deny_audit perm=any all : ftype=application/x-sharedlib trust=0\n' > "$d/rules.d/41-shared-obj.rules"
    printf 'deny_audit perm=execute all : all\nallow perm=open all : all\n' > "$d/rules.d/90-deny-execute.rules"
    printf '# the containers job'"'"'s own\nallow perm=execute exe=/usr/bin/crun : path=/srv/app/server\nallow perm=execute all : dir=/srv/*/bin/\n' \
        > "$d/rules.d/35-site-containers.rules"
done
cat > "$work/ra01/etc/rules.d/50-local.rules" <<'EOR'
# site rules
allow perm=execute exe=/usr/bin/crun : path=/srv/app/server
allow  perm=open all : dir=/srv/data/
allow perm=open all : dir=/srv/data/
allow perm=execute all : dir=/home/*/bin/
allow perm=execute all : path=/usr/bin/ls
allow perm=any all : dir=/usr/lib/
EOR
cp "$work/ra01/etc/rules.d/50-local.rules" "$work/ra04/etc/rules.d/50-local.rules"
printf 'allow_audit perm=open all : dir=/srv/audited/\n' > "$work/ra02/etc/rules.d/36-audited.rules"
cat > "$work/hosts.json" <<EOJ
{"ra01": {"installed": true, "active": "active",
          "events": [["/usr/bin/bash", "$work/opt/app/tool", "execve", "deny", "deny"], ["/usr/bin/bash", "$work/opt/app/tool", "execve", "deny", "deny"],
                     ["/usr/bin/crun", "/app/server", "execve", "deny", "deny"],
                     ["/usr/bin/bash", "/home/u/bin/x", "execve", "deny", "deny"], ["/usr/bin/bash", "/usr/bin/ls", "execve", "deny", "deny"]]},
 "ra02": {"installed": true, "active": "active", "restarts": ["active"],
          "events": [["/usr/bin/bash", "$work/opt/app/tool2", "execve", "allow", "deny"], ["/usr/bin/cat", "/srv/audited/x", "openat", "allow", "allow_audit"]]},
 "ra04": {"installed": true, "active": "active", "restarts": ["active"], "break_on_load": true,
          "events": [["/usr/bin/bash", "$work/opt/app/tool", "execve", "deny", "deny"]]},
 "ra03": {"installed": false}}
EOJ
cat > "$work/bin/fake.py" <<'EOP'
import glob, hashlib, json, os, re, sys, time
tool, args = os.path.basename(sys.argv[1]), sys.argv[2:]
work = os.environ["FAKE_FA_WORK"]
name = os.environ.get("FAKE_FA_HOST", "")
h = json.load(open(work + "/hosts.json")).get(name, {})
if args == ["--is-fake"]:
    print("FAKE " + tool); sys.exit(0)
with open(os.path.join(work, "calls", name or "none"), "a") as f:
    f.write(tool + " " + " ".join(args) + "\n")
d = os.path.join(work, name, "etc")
state_file = os.path.join(work, "state", name + ".active")
def state():
    return open(state_file).read().strip() if os.path.exists(state_file) else h.get("active", "inactive")
def nat(x):
    return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", os.path.basename(x))]
def built():
    out = ["## This file is automatically generated from %s/rules.d" % d]
    for f in sorted(glob.glob(d + "/rules.d/*.rules"), key=nat):
        out += [l.rstrip("\n") for l in open(f) if l.strip() and not re.match(r"^\s*#", l)]
    return "\n".join(out) + "\n"
def compiled_rules():
    try:
        return [l.strip() for l in open(d + "/compiled.rules") if l.strip() and not l.startswith("#")]
    except IOError:
        return []
if tool == "rpm":
    if args == ["-q", "fapolicyd"]:
        print("fapolicyd-1.4.5-1.el9.x86_64" if h.get("installed") else "package fapolicyd is not installed"); sys.exit(0 if h.get("installed") else 1)
    if args[:1] == ["-q"] and args[-1] == "fapolicyd":
        u, g = os.environ["FAKE_FA_USER"], os.environ["FAKE_FA_GROUP"]
        print("-rw-r--r-- %s %s /etc/fapolicyd/fapolicyd.trust\ndrwxr-x--- %s %s /etc/fapolicyd/trust.d" % (u, g, u, g)); sys.exit(0)
    if args[:1] == ["-qf"]:
        if args[1] in ("/usr/bin/ls",):
            print("coreutils-8.32-35.el9.x86_64"); sys.exit(0)
        print("file %s is not owned by any package" % args[1]); sys.exit(1)
    sys.exit(1)
if tool == "systemctl":
    unit = args[-1]
    if args[0] == "is-active":
        s = state() if unit == "fapolicyd" else "active"
        "--quiet" in args or print(s); sys.exit(0 if s == "active" else 3)
    if args[0] == "restart":
        seq = h.get("restarts", ["active"])
        n = len([l for l in open(os.path.join(work, "calls", name)) if l.startswith("systemctl restart")])
        s = seq[min(n, len(seq)) - 1]; open(state_file, "w").write(s); sys.exit(0 if s == "active" else 1)
    if args[0] == "stop":
        open(state_file, "w").write("inactive"); sys.exit(0)
    sys.exit(0)
if tool == "journalctl":
    print("Loading rule file\nRuleset identity: 0f00\nUpdating trust database\nUpdated"); sys.exit(0)
if tool == "ausearch":
    rules = compiled_rules()
    num = {}
    for i, r in enumerate(rules, 1):
        num.setdefault(r.split()[0], i)
    if not h.get("events"):
        print("<no matches>", file=sys.stderr); sys.exit(1)
    now = time.strftime("%m/%d/%Y %H:%M:%S", time.localtime(time.time() + 5))
    for n, (exe, path, sc, resp, by) in enumerate(h["events"]):
        print("----")
        print("type=PATH msg=audit(%s.000:1%d) : item=0 name=%s inode=1 mode=file,755" % (now, n, path))
        print("type=SYSCALL msg=audit(%s.000:1%d) : arch=x86_64 syscall=%s success=%s exe=%s uid=root" % (now, n, sc, "no" if resp == "deny" else "yes", exe))
        no = num.get("deny_audit" if by == "deny" else by, 0)
        # the first event as an old ausearch prints it: fan_type=1, fan_info in hex (as the kernel writes it)
        ft, fi = ("1", "%X" % no) if n == 0 else ("rule_info", str(no))
        print("type=FANOTIFY msg=audit(%s.000:1%d) : resp=%s fan_type=%s fan_info=%s" % (now, n, resp, ft, fi))
    sys.exit(0)
if tool == "fapolicyd-cli":
    if "--list" in args:
        for i, r in enumerate(compiled_rules(), 1):
            print("%d. %s" % (i, r))
        sys.exit(0)
    if args[:2] == ["--file", "add"]:
        path, tf = args[2], os.path.join(d, "trust.d", args[args.index("--trust-file") + 1])
        line = "%s %d %s" % (path, os.path.getsize(path), hashlib.sha256(open(path, "rb").read()).hexdigest())
        have = open(tf).read() if os.path.exists(tf) else "# AUTOGENERATED FILE VERSION 3\n"
        if line in have.splitlines():
            print("After removing duplicates, there is nothing to add"); sys.exit(9)
        old = os.umask(0o077); open(tf, "w").write(have + line + "\n"); os.umask(old); sys.exit(0)
    if "--check-config" in args:
        print("Daemon config is OK"); sys.exit(0)
    sys.exit(0)
if tool == "fagenrules":
    b = built()
    have = open(d + "/compiled.rules").read() if os.path.exists(d + "/compiled.rules") else ""
    if "--check" in args:
        print("/usr/sbin/fagenrules: No change" if b == have else "/usr/sbin/fagenrules: Rules have changed and should be updated"); sys.exit(0)
    open(d + "/compiled.rules", "w").write(b)
    if "--load" in args and h.get("break_on_load") and any("# removed by" in open(f).read() for f in glob.glob(d + "/rules.d/*.rules")):
        open(state_file, "w").write("failed")
    sys.exit(0)
sys.exit(0)
EOP
for t in rpm systemctl journalctl ausearch fapolicyd-cli fagenrules; do
    printf '#!/bin/sh\nexec "%s" "%s" "$0" "$@"\n' "$py" "$work/bin/fake.py" > "$work/bin/$t"; chmod 755 "$work/bin/$t"
    if [ "$(PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" "$t" --is-fake 2>&1)" != "FAKE $t" ]; then
        echo "ABORT - $t is not the fake; not running anything"; exit 1
    fi
done
for h in ra01 ra02 ra04; do PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" FAKE_FA_HOST=$h fagenrules >/dev/null; done
inv() {
    echo "all:"; echo "  hosts:"
    for h in ra01 ra02 ra03 ra04; do
        printf '#!/bin/sh\nFAKE_FA_HOST=%s exec "%s" "$@"\n' "$h" "$py" > "$work/py-$h"; chmod 755 "$work/py-$h"
        echo "    $h.example.mil: {ansible_connection: local, ansible_become: false, ansible_python_interpreter: $work/py-$h, fapolicyd_rules_dir: $work/$h/etc}"
    done
    echo "    gone01.example.mil: {ansible_host: 127.0.0.1, ansible_port: 1, ansible_connection: ssh}"
}
inv > "$work/inv.yml"
cat > "$work/vars.json" <<EOJ
{"report_web_dest": "$work/site", "site_syslog": false, "fapolicyd_rules_wait": 2,
 "fapolicyd_rules_user_dirs": ["/home/", "/run/user/", "/var/tmp/", "/dev/shm/"]}
EOJ

run() {  # run HOST|all [ansible-playbook args...] -> $work/out.txt, $rc
    local h="$1"; shift
    rm -rf "$work/site"; mkdir -p "$work/site"
    (cd "$repo" && PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" FAKE_FA_USER="$me" FAKE_FA_GROUP="$mygroup" \
        FAKE_FA_HOST="$([ "$h" = all ] || echo "$h")" \
        ANSIBLE_STDOUT_CALLBACK=default ANSIBLE_NOCOLOR=1 ANSIBLE_LOCALHOST_WARNING=False ANSIBLE_DEPRECATION_WARNINGS=False \
        ANSIBLE_HOST_KEY_CHECKING=False ANSIBLE_TIMEOUT=3 \
        "$ap" -i "$work/inv.yml" playbooks/fapolicyd_rules.yml -e "@$work/vars.json" \
        $([ "$h" = all ] || echo "-l $h.example.mil") "$@") > "$work/out.txt" 2>&1
    rc=$?
}
report() {
    "$py" -c 'import json, glob, sys; f = sorted(glob.glob(sys.argv[1] + "/*/latest.json")); print(json.dumps(json.load(open(f[-1])))) if f else print("{}")' "$work/site"
}
check() {  # check "label" PYTHON-EXPRESSION-ON-r (the report) and s (its sections by title)
    if report | "$py" -c 'import json, sys; r = json.load(sys.stdin); r = r.get("report", r); s = {x["title"]: x for x in r.get("sections", [])}; sys.exit(0 if eval(sys.argv[1]) else 1)' "$2"; then
        ok "$1"; else bad "$1: $(report | head -c 3000)"; fi
}
same() { [ "$(cat "$1")" = "$(cat "$2")" ]; }   # cmp -s without diffutils (not in every test image)
lines_of() {  # the report's "Changes to approve" lines, one per line
    report | "$py" -c 'import json, sys; r = json.load(sys.stdin); r = r.get("report", r); [print(l) for x in r["sections"] if x["title"] == "Changes to approve" for l in x["lines"]]'
}

# ---- read only: every host -------------------------------------------------------------------------------------
run all
[ "$rc" = 0 ] || [ "$rc" = 4 ] && ok "read only: the job runs (rc $rc: gone01 cannot be reached)" || bad "read only: rc $rc" 40
lines_of > "$work/lines.txt"
sha="$(sha256sum "$work/opt/app/tool" | cut -d' ' -f1)"; size="$(stat -c %s "$work/opt/app/tool")"
T1="trust file: $work/opt/app/tool size $size sha256 $sha"
grep -qxF "$T1" "$work/lines.txt" && ok "ra01: the host file blocked gets its trust entry, pinned to its size and sha256" || bad "no trust line: $(cat "$work/lines.txt")"
grep -qxF "allow rule: allow perm=any all : dir=/var/lib/containers/storage/overlay/" "$work/lines.txt" && ok "  ... the container's file inside an image: container storage's rule" || bad "no storage rule"
grep -qxF "remove rule: 50-local.rules: allow perm=open all : dir=/srv/data/" "$work/lines.txt" \
    && grep -qxF "remove rule: 50-local.rules: allow perm=execute all : dir=/home/*/bin/" "$work/lines.txt" \
    && grep -qxF "remove rule: 50-local.rules: allow perm=execute all : path=/usr/bin/ls" "$work/lines.txt" \
    && grep -qxF "remove rule: 50-local.rules: allow perm=execute exe=/usr/bin/crun : path=/srv/app/server" "$work/lines.txt" \
    && ok "  ... the clean-up: a repeat (in the file, and of the containers job's file), a wildcard, an RPM-owned file's rule" || bad "clean-up: $(cat "$work/lines.txt")"
grep -qxF "remove rule: 50-local.rules: allow perm=any all : dir=/usr/lib/" "$work/lines.txt" && grep -qxF "rename rules file: 50-local.rules -> 39-local.rules" "$work/lines.txt" \
    && ok "  ... a rule that opens a system folder; the file renamed before the stock deny rules" || bad "broad/rename: $(cat "$work/lines.txt")"
grep -qxF "permissive: 0 -> 1" "$work/lines.txt" && grep -qxF "permissive: 1 -> 0" "$work/lines.txt" && ok "permissive: 0 -> 1 offered (ra01 blocks while enforcing); 1 -> 0 (ra02 is permissive)" || bad "permissive lines"
grep -q "35-site-containers.rules" "$work/lines.txt" && bad "a line for the containers job's own file" || ok "  ... nothing for the containers job's own file (its wildcard: for a person)"
check "the report: blocked, would be blocked (permissive), permissive on 1 - critical" \
      '[(x["label"], x["value"]) for x in r["summary"]][:2] == [("Blocked", 6), ("Would be blocked (permissive)", 1)] and "permissive on 1" in r["title"] and r["status"] == "critical"'
check "ra02's would-be block gets its trust entry; its allow_audit allow is not a block" \
      'any(row[0] == "ra02.example.mil" and row[1] == "would be (permissive)" and row[3].endswith("/opt/app/tool2") for row in s["What fapolicyd blocked"]["rows"]) and not any("/srv/audited/" in row[3] for row in s["What fapolicyd blocked"]["rows"])'
check "for a person: the file in a user folder, the RPM-owned file, the containers job's wildcard" \
      'any("/home/u/bin/x is in a folder users can write" in x for x in s["For a person"]["lines"]) and any("/usr/bin/ls is RPM-owned" in x for x in s["For a person"]["lines"]) and any("35-site-containers.rules" in x and "the containers job" in x for x in s["For a person"]["lines"])'
check "what each change does: the system folder's rule blocks more" 'any(row[1].endswith("dir=/usr/lib/") and row[3].startswith("blocks more") for row in s["Why each change"]["rows"])'
check "ra03 not installed, gone01 not read" '"ra03.example.mil" in s["fapolicyd not installed"]["lines"][0] and "gone01.example.mil" in s["Not read"]["lines"][0]'

# ---- the workflow's check: each host's changes handed on, NEEDS APPROVAL, nothing changed ------------------------
run all -e '{"fapolicyd_rules_workflow": true}'
[ "$rc" = 2 ] && grep -q 'NEEDS APPROVAL - ' "$work/out.txt" && ! [ -e "$work/ra01/etc/trust.d/site-approved" ] \
    && ok "the workflow's check: NEEDS APPROVAL (exit 2), nothing changed" || bad "workflow check: rc $rc" 40
grep 'NEEDS APPROVAL' "$work/out.txt" > "$work/needs.txt"
grep -q "ra01.example.mil: trust file: $work/opt/app/tool size" "$work/needs.txt" && grep -q "ra02.example.mil: trust file: .*tool2" "$work/needs.txt" \
    && ok "  ... each host with its own lines (ra01's trust entry, ra02's would-be block's)" || bad "workflow plan: $(cat "$work/needs.txt")"
grep -q 'permissive: 0 -> 1' "$work/needs.txt" && bad "permissive: 0 -> 1 in the workflow's plan" || ok "  ... permissive: 0 -> 1 held back for a person"
grep -q 'dir=/usr/lib/' "$work/needs.txt" && bad "a stricter clean-up on an enforcing host in the plan" || ok "  ... the clean-up that blocks more, on an enforcing host, held back"
grep -q 'ra02.example.mil: [^|]*permissive: 1 -> 0' "$work/needs.txt" && bad "permissive: 1 -> 0 with would-be blocks in the window" \
    || { grep -q 'ra02.example.mil: permissive: 1 -> 0: 1 would-be block' "$work/out.txt" && ok "  ... permissive: 1 -> 0 held while ra02 has a would-be block" || bad "held line not said"; }
# the approved job, as the workflow hands it the plan: each host its own lines only
T2="$(grep '^trust file: .*tool2' "$work/lines.txt")"
run all -e "{\"fapolicyd_rules_workflow_plan\": {\"ra02.example.mil\": [\"$T2\"]}}"
grep -q "/opt/app/tool2 " "$work/ra02/etc/trust.d/site-approved" && ! [ -e "$work/ra01/etc/trust.d/site-approved" ] && grep -q '^permissive = 1$' "$work/ra02/etc/fapolicyd.conf" \
    && ok "the approved job: ra02's plan made on ra02 only (ra01 has none)" || bad "workflow approved: rc $rc" 40

# ---- the changes, approved: Check, then for real --------------------------------------------------------------
"$py" - "$work/lines.txt" "$work/ok1.json" <<'EOP'
import json, sys
lines = [l.strip() for l in open(sys.argv[1]) if l.strip()]
want = [l for l in lines if l.startswith(("trust file: ", "allow rule: ")) and "tool2" not in l] + \
       [l for l in lines if l.startswith(("remove rule: 50-local", "rename rules file: 50-local"))]
json.dump({"fapolicyd_rules_approved": want}, open(sys.argv[2], "w"))
EOP
run ra01 -e "@$work/ok1.json" --check
[ "$rc" = 0 ] && ! [ -e "$work/ra01/etc/trust.d/site-approved" ] && [ -e "$work/ra01/etc/rules.d/50-local.rules" ] && grep -q "would change - made: " "$work/out.txt" \
    && ok "Check with the changes approved: would change, nothing changed" || bad "Check: rc $rc" 40
run ra01 -e "@$work/ok1.json"
[ "$rc" = 0 ] && ok "the changes approved: the job ends well (the stricter one refused while enforcing, said)" || bad "changes: rc $rc" 40
grep -qxF "$work/opt/app/tool $size $sha" "$work/ra01/etc/trust.d/site-approved" && [ "$(stat -c %a "$work/ra01/etc/trust.d/site-approved")" = 644 ] \
    && ok "  ... the trust entry in site-approved, the file readable to fapolicyd (0644, not fapolicyd-cli's 0600)" || bad "trust file: $(stat -c %a "$work/ra01/etc/trust.d/site-approved" 2>&1)"
grep -qxF "allow perm=any all : dir=/var/lib/containers/storage/overlay/" "$work/ra01/etc/rules.d/36-site-approved.rules" && ok "  ... the storage rule in 36-site-approved.rules" || bad "own rules file"
f="$work/ra01/etc/rules.d/39-local.rules"
[ -e "$f" ] && [ ! -e "$work/ra01/etc/rules.d/50-local.rules" ] && [ "$(grep -c '^# removed by site-automation (fapolicyd rules job, approved ' "$f")" = 4 ] \
    && [ "$(grep -c '^allow perm=open all : dir=/srv/data/' "$f")$(grep -c '^allow  perm=open all : dir=/srv/data/' "$f")" = 01 ] \
    && grep -q '^allow perm=any all : dir=/usr/lib/$' "$f" \
    && ok "  ... 50-local renamed 39-local; 4 rules commented out, marked (the later repeat); the stricter one still there" || bad "rules file: $(cat "$f" 2>&1)"
[ "$(PATH="$work/bin:$PATH" FAKE_FA_WORK="$work" FAKE_FA_HOST=ra01 fagenrules --check)" = "/usr/sbin/fagenrules: No change" ] && grep -q 'fagenrules --load' "$work/calls/ra01" \
    && ok "  ... the rules loaded (fagenrules --load), compiled.rules as the files say" || bad "compiled.rules not rebuilt"
check "  ... the report: changed, the stricter one refused - it blocks more while enforcing" \
      's["What this run changed"]["rows"][0][1] == "changed" and "it blocks more, and fapolicyd is enforcing" in s["What this run changed"]["rows"][0][5]'

# ---- permissive: 1 -> 0, and its would-be block's trust entry ----------------------------------------------------
T2="$(grep '^trust file: .*tool2' "$work/lines.txt")"
run ra02 -e "{\"fapolicyd_rules_approved\": [\"$T2\", \"permissive: 1 -> 0\"]}"
[ "$rc" = 0 ] && grep -q '^permissive = 0$' "$work/ra02/etc/fapolicyd.conf" && grep -q 'systemctl restart fapolicyd' "$work/calls/ra02" \
    && grep -q "/opt/app/tool2 " "$work/ra02/etc/trust.d/site-approved" \
    && ok "ra02: permissive = 0, fapolicyd restarted, the would-be block trusted first" || bad "ra02: rc $rc" 40
check "  ... the report: permissive no longer counted" '"permissive on" not in r["title"]'

# ---- fapolicyd fails after a rules change on a host where it ran: every file put back ----------------------------
cp "$work/ra04/etc/rules.d/50-local.rules" "$work/ra04-before.rules"
run ra04 -e '{"fapolicyd_rules_approved": ["remove rule: 50-local.rules: allow perm=execute all : dir=/home/*/bin/"]}'
[ "$rc" = 2 ] && same "$work/ra04-before.rules" "$work/ra04/etc/rules.d/50-local.rules" && [ "$(cat "$work/state/ra04.active")" = active ] \
    && ok "ra04: fapolicyd failed after the rules change - the file put back, the rules rebuilt, fapolicyd running (exit 2)" || bad "ra04: rc $rc, $(cat "$work/ra04/etc/rules.d/50-local.rules")" 40
check "  ... the report: rolled back, why" 's["What this run changed"]["rows"][0][1] == "rolled back" and "every file put back" in s["What this run changed"]["rows"][0][5]'

# ---- a line the job never gives: nothing changed ------------------------------------------------------------------
cp "$work/ra04/etc/rules.d/50-local.rules" "$work/ra04-before.rules"
run ra04 -e '{"fapolicyd_rules_approved": ["remove rule: 50-local.rules: allow perm=execute all : dir=/home/*/bin/ (ra04)", "rm -rf /"]}'
[ "$rc" = 2 ] && same "$work/ra04-before.rules" "$work/ra04/etc/rules.d/50-local.rules" && grep -q 'refused: a line approved is not one this job gives' "$work/out.txt" \
    && ok "a line the job never gives (and one copied with a host name): nothing changed (exit 2)" || bad "refused: rc $rc" 30
if ls -d /tmp/*.fapolicyd-rules "${TMPDIR:-/tmp}"/*.fapolicyd-rules >/dev/null 2>&1; then bad "a way-back folder was left behind"; else ok "no way-back folder left behind"; fi

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails"; exit 1; fi
echo "all fapolicyd rules scenarios passed"
