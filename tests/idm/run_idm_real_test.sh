#!/usr/bin/env bash
# The IdM inventory (inventories/idm/inventory_plugins/site_idm.py) against a real IdM - not in CI: it needs podman and
# the FreeIPA server image quay.io/freeipa/freeipa-server:almalinux-9 already pulled (it never pulls), and installs IdM
# in it (5-10 minutes; IDM_TEST_KEEP=1 keeps it for the next run). Its realm EXAMPLE.MIL, idm1.example.mil; host groups
# nested (linux-all holds rhel9-servers and rhel8-servers), one named 2fa-pilot, one named all; five hosts with a keytab
# (enrolled), one without (vip01, a name only); svc_inv a plain IdM user (no role), svc_new one whose first password was
# never changed. ansible-inventory runs as AAP's inventory sync does (ANSIBLE_INVENTORY_UNPARSED_FAILED, the settings
# from environment variables - the Source variables box's dictionaries as str() of them), in a container in IdM's
# network (IdM answers only to its own name): IDM_TEST_CLIENT_IMAGE (python:3.13-slim) with a venv of ansible-core
# IDM_TEST_CORE (the latest; 2.16.* for the oldest) made once in IDM_TEST_CACHE.
#   bash tests/idm/run_idm_real_test.sh     (IDM_TEST_CORE='2.16.*' ; IDM_TEST_KEEP=1 ; CONTAINER_ENGINE=...)
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
eng="${CONTAINER_ENGINE:-podman}"
ipa_img=quay.io/freeipa/freeipa-server:almalinux-9
cli_img="${IDM_TEST_CLIENT_IMAGE:-docker.io/library/python:3.13-slim}"
core="${IDM_TEST_CORE:-}"
cache="${IDM_TEST_CACHE:-$HOME/.cache/sa-idm-test}"
venv="venv-$(echo "${core:-latest}" | tr -c 'A-Za-z0-9.\n' '_')"
name=sa-idm-test vol=sa-idm-test-data pw=Secret123-idm
work="$(mktemp -d)"
cleanup() {
    if [ -n "${IDM_TEST_KEEP:-}" ]; then echo "kept: container $name (volume $vol)"; else
        $eng rm -f "$name" >/dev/null 2>&1; $eng volume rm -f "$vol" >/dev/null 2>&1; fi
    rm -rf "$work"
}
trap cleanup EXIT
fails=0
for img in "$ipa_img" "$cli_img"; do
    $eng image exists "$img" || { echo "SKIP - the image $img is not here (this test never pulls)"; exit 0; }
done
mkdir -p "$cache"

if ! $eng exec "$name" test -f /data/sa-idm-filled 2>/dev/null; then
    $eng rm -f "$name" >/dev/null 2>&1; $eng volume rm -f "$vol" >/dev/null 2>&1
    $eng volume create "$vol" >/dev/null
    echo "installing IdM in $name (5-10 minutes)"
    $eng run -d --name "$name" -h idm1.example.mil --read-only -v "$vol:/data:Z" -e "PASSWORD=$pw" "$ipa_img" \
        ipa-server-install -U -r EXAMPLE.MIL --domain=example.mil --no-ntp --no-pkinit >/dev/null \
        || { echo "ABORT - IdM's container did not start"; exit 1; }
    for _ in $(seq 1 90); do $eng logs "$name" 2>&1 | grep -q "Setup complete\|command failed" && break; sleep 10; done
    $eng logs "$name" 2>&1 | grep -q "Setup complete" || { echo "ABORT - IdM not installed"; $eng logs "$name" 2>&1 | tail -5; exit 1; }
    $eng exec -i -e "PW=$pw" "$name" bash -s <<'EOF' || { echo "ABORT - IdM not filled"; exit 1; }
set -e
echo "$PW" | kinit admin >/dev/null
for g in rhel9-servers rhel8-servers linux-all aap-servers stigman-servers lab-hosts 2fa-pilot all; do
    ipa hostgroup-add "$g" --desc "test $g" >/dev/null
done
ipa hostgroup-add-member linux-all --hostgroups=rhel9-servers --hostgroups=rhel8-servers >/dev/null
el9=5.14.0-427.13.1.el9_4.x86_64
ipa host-add web01.example.mil --force --os=$el9 --platform=x86_64 --desc="web server" --locality=Bldg1 --location="Rack 4" >/dev/null
ipa host-add db01.example.mil --force --os=4.18.0-553.el8_10.x86_64 --platform=x86_64 >/dev/null
for h in aap01 stig01 lab01; do ipa host-add $h.example.mil --force --os=$el9 >/dev/null; done
ipa host-add vip01.example.mil --force --desc="load balancer name" >/dev/null
for h in web01 db01 aap01 stig01 lab01; do ipa-getkeytab -s idm1.example.mil -p host/$h.example.mil -k /tmp/$h.keytab >/dev/null 2>&1; done
ipa hostgroup-add-member rhel9-servers --hosts={web01,aap01,stig01,lab01}.example.mil >/dev/null
ipa hostgroup-add-member rhel8-servers --hosts=db01.example.mil >/dev/null
ipa hostgroup-add-member aap-servers --hosts=aap01.example.mil >/dev/null
ipa hostgroup-add-member stigman-servers --hosts=stig01.example.mil >/dev/null
ipa hostgroup-add-member lab-hosts --hosts=lab01.example.mil >/dev/null
ipa hostgroup-add-member 2fa-pilot --hosts=web01.example.mil >/dev/null
echo Inv-Pass-2026 | ipa user-add svc_inv --first=Svc --last=Inventory --password >/dev/null
ipa user-mod svc_inv --setattr krbPasswordExpiration=20991231000000Z >/dev/null
echo New-Pass-2026 | ipa user-add svc_new --first=Svc --last=New --password >/dev/null
echo Few-Pass-2026 | ipa user-add svc_few --first=Svc --last=Few --password >/dev/null
ipa user-mod svc_few --setattr krbPasswordExpiration=20991231000000Z >/dev/null
# svc_few: the directory gives it 3 entries a search (IdM's list cut short)
ldapmodify -x -D "cn=Directory Manager" -w "$PW" -H ldap://localhost >/dev/null <<LDIF
dn: uid=svc_few,cn=users,cn=accounts,dc=example,dc=mil
changetype: modify
add: objectClass
objectClass: nsContainer
-
add: nsSizeLimit
nsSizeLimit: 3
LDIF
touch /data/sa-idm-filled
EOF
fi
$eng cp "$name:/etc/ipa/ca.crt" "$work/ca.crt"

if [ ! -x "$cache/$venv/bin/ansible-inventory" ]; then
    echo "making $cache/$venv (ansible-core ${core:-latest})"
    $eng run --rm -v "$cache:/cache:Z" "$cli_img" sh -c "python3 -m venv /cache/$venv && /cache/$venv/bin/pip -q install 'ansible-core${core:+==$core}'" >/dev/null 2>&1 \
        || { echo "ABORT - no ansible-core venv"; exit 1; }
fi
echo "ansible-core: $($eng run --rm -v "$cache:/cache:Z" "$cli_img" /cache/$venv/bin/ansible --version | head -1)"

# inv NAME EXPECT(ok|fail) CHECK [VAR=value ...] - ansible-inventory --list as AAP runs it; CHECK is Python on the
# output: inv (the JSON), err (stderr), hosts, groups(name) -> its hosts and children's hosts, children(name)
inv() {
    local tname="$1" want="$2" chk="$3"; shift 3
    local envs=(-e ANSIBLE_INVENTORY_UNPARSED_FAILED=1 -e IDM_SERVER=idm1.example.mil -e IDM_USER=svc_inv
                -e IDM_PASSWORD=Inv-Pass-2026 -e IDM_CA_PATH=/w/ca.crt -e "ANSIBLE_CONFIG=${CFG:-/repo/ansible.cfg}")
    for kv in "$@"; do envs+=(-e "$kv"); done
    $eng run --rm --network "container:$name" -v "$repo:/repo:ro,Z" -v "$work:/w:Z" -v "$cache:/cache:Z" -w "${CWD:-/repo}" \
        "${envs[@]}" "$cli_img" /cache/$venv/bin/ansible-inventory --list -i /repo/inventories/idm/idm.yml ${ARGS:-} \
        > "$work/out.json" 2> "$work/err.txt"
    local rc=$?
    if { [ "$want" = ok ] && [ $rc -ne 0 ]; } || { [ "$want" = fail ] && [ $rc -eq 0 ]; }; then
        echo "FAIL - $tname: exit $rc (wanted $want)"; tail -5 "$work/err.txt"; fails=$((fails + 1)); return
    fi
    if python3 - "$work/out.json" "$work/err.txt" "$chk" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
inv = json.loads(raw) if raw.strip() else {}
err = " ".join(open(sys.argv[2]).read().split())
hosts = sorted(inv.get("_meta", {}).get("hostvars", {}))
def children(g):
    return sorted(inv.get(g, {}).get("children", []))
def groups(g):
    out = set(inv.get(g, {}).get("hosts", []))
    for c in children(g):
        out |= set(groups(c))
    return sorted(out)
def plain(v):   # a value marked unsafe ({"__ansible_unsafe": ...}) as the value
    if isinstance(v, dict):
        return plain(v["__ansible_unsafe"]) if list(v) == ["__ansible_unsafe"] else {k: plain(x) for k, x in v.items()}
    return [plain(x) for x in v] if isinstance(v, list) else v
hv = plain(inv.get("_meta", {}).get("hostvars", {}))
ok = eval(sys.argv[3])
if not ok:
    print("   hosts %s; groups %s; err %s" % (hosts, sorted(k for k in inv if k != "_meta"), err[:400]))
sys.exit(0 if ok else 1)
PY
    then echo "ok   - $tname"; else echo "FAIL - $tname"; fails=$((fails + 1)); fi
}
s=short.example.mil
all6='["aap01.example.mil", "db01.example.mil", "idm1.example.mil", "lab01.example.mil", "stig01.example.mil", "web01.example.mil"]'
map="IDM_GROUP_MAP={'rhel9-servers': 'rhel9_all', 'rhel8-servers': 'rhel8_all', 'aap-servers': ['aap_hosts', 'no_patch'], 'ipaservers': 'idm'}"

inv "a plain IdM user: the five enrolled hosts and the IdM server, vip01 (no keytab) left out" ok "hosts == $all6"
inv "... IdM's host groups as groups, nested as in IdM (linux-all holds rhel8/rhel9-servers); 2fa-pilot named idm_2fa_pilot" ok \
    "children('linux_all') == ['rhel8_servers', 'rhel9_servers'] and groups('linux_all') == ['aap01.example.mil', 'db01.example.mil', 'lab01.example.mil', 'stig01.example.mil', 'web01.example.mil'] and groups('idm_2fa_pilot') == ['web01.example.mil'] and groups('ipaservers') == ['idm1.example.mil'] and 'idm_all' not in inv"
inv "... each host's IdM details as idm_* variables" ok \
    "hv['web01.example.mil'] == {'idm_fqdn': 'web01.example.mil', 'idm_hostgroups': ['2fa-pilot', 'linux-all', 'rhel9-servers'], 'idm_enrolled': True, 'idm_description': 'web server', 'idm_locality': 'Bldg1', 'idm_location': 'Rack 4', 'idm_platform': 'x86_64', 'idm_os': '5.14.0-427.13.1.el9_4.x86_64'}"
inv "group_map from the Source variables box (str() of a dictionary): the runbooks' groups, a group's nested hosts too" ok \
    "groups('aap_hosts') == ['aap01.example.mil'] and groups('no_patch') == ['aap01.example.mil'] and len(groups('rhel9_all')) == 4 and groups('rhel8_all') == ['db01.example.mil'] and groups('idm') == ['idm1.example.mil'] and groups('rhel9_servers') == groups('rhel9_all')" \
    "$map" "IDM_REQUIRE_GROUPS=['aap_hosts']"
inv "group_map with a pattern; idm_groups false - only the mapped groups" ok \
    "sorted(k for k in inv if k not in ('_meta', 'all', 'ungrouped')) == ['linux_servers'] and len(groups('linux_servers')) == 5" \
    "IDM_GROUP_MAP={'rhel?-servers': 'linux_servers'}" IDM_GROUPS=false
inv "exclude_hostgroups lab-*: lab01 gone" ok "'lab01.example.mil' not in hosts and len(hosts) == 5" "IDM_EXCLUDE_HOSTGROUPS=['lab-*']"
inv "include_hostgroups linux-all (nested): the five Linux hosts, not the IdM server" ok \
    "'idm1.example.mil' not in hosts and len(hosts) == 5" "IDM_INCLUDE_HOSTGROUPS=['linux-all']"
inv "include_hostgroups with a name IdM does not have: said" ok "'rhel10-servers' in err and 'matches no IdM host group' in err" \
    "IDM_INCLUDE_HOSTGROUPS=['linux-all', 'rhel10-servers']"
inv "include and exclude hosts by name (comma-separated)" ok "hosts == ['db01.example.mil', 'web01.example.mil']" \
    "IDM_INCLUDE_HOSTS=web*,db*,lab*" "IDM_EXCLUDE_HOSTS=lab01.example.mil"
inv "enrolled_only false: vip01 too, idm_enrolled false" ok "hv['vip01.example.mil']['idm_enrolled'] is False and len(hosts) == 7" \
    IDM_ENROLLED_ONLY=false
inv "hostname short: web01, ansible_host its FQDN" ok "hv['web01']['ansible_host'] == 'web01.example.mil' and 'web01' in groups('rhel9_servers')" \
    IDM_HOSTNAME=short
inv "group_prefix idm_: idm_rhel9_servers" ok "len(groups('idm_rhel9_servers')) == 4 and 'rhel9_servers' not in inv" IDM_GROUP_PREFIX=idm_
cat > "$work/c-idm.yml" <<'EOF'
plugin: site_idm
groups:
  rhel9_by_os: "'.el9' in idm_os"
keyed_groups:
  - {key: idm_locality, prefix: site, default_value: none}
EOF
ARGS="-i /w/c-idm.yml" inv "constructed: groups from the OS (rhel9_by_os) and keyed_groups from the locality" ok \
    "len(groups('rhel9_by_os')) == 4 and groups('site_Bldg1') == ['web01.example.mil']"
inv "require_groups stigman_hosts, no host in it: the sync stops" fail "'no host in stigman_hosts (require_groups)' in err" \
    "$map" "IDM_REQUIRE_GROUPS=['aap_hosts', 'stigman_hosts']"
inv "min_hosts 50: the sync stops" fail "'only 6 host(s)' in err and 'min_hosts is 50' in err" IDM_MIN_HOSTS=50
inv "a filter that leaves nothing: the sync stops (min_hosts 1)" fail "'only 0 host(s)' in err and 'not in include_hosts: 7' in err" \
    "IDM_INCLUDE_HOSTS=nothing*" IDM_ENROLLED_ONLY=false
inv "a first password never changed: said (password-expired)" fail "'password has expired' in err and 'kinit svc_new' in err" \
    IDM_USER=svc_new IDM_PASSWORD=New-Pass-2026
inv "a wrong password: said" fail "'user name or password is wrong' in err" IDM_PASSWORD=wrong
inv "the list cut short by the directory (svc_few: 3 entries a search): the sync stops" fail \
    "'returned only' in err and 'nsSizeLimit' in err" IDM_USER=svc_few IDM_PASSWORD=Few-Pass-2026
inv "no CA: IdM's certificate not trusted - the sync stops" fail "'CERTIFICATE_VERIFY_FAILED' in err" IDM_CA_PATH=
inv "the server by its address (127.0.0.1, certificate not checked): IdM's redirect said" fail \
    "'set server to the IdM server' in err" IDM_SERVER=127.0.0.1 IDM_VALIDATE_CERTS=false
printf '[defaults]\n' > "$work/empty.cfg"
CFG=/w/empty.cfg CWD=/ ARGS="--playbook-dir /repo/inventories/idm" inv \
    "no ansible.cfg, run from / with --playbook-dir inventories/idm (AAP's sync): the plugin found" ok "hosts == $all6"
CFG=/w/empty.cfg CWD=/ inv "... without --playbook-dir and ansible.cfg: not parsed - the sync fails, never empty" fail "True"

echo
if [ $fails -eq 0 ]; then echo "all IdM inventory scenarios passed"; else echo "$fails IdM inventory scenario(s) FAILED"; exit 1; fi
