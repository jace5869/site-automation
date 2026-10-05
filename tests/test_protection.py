#!/usr/bin/env python3
"""The AAP hosts must be protected from patching, reboots and self-heal in EVERY place that
decides it. Fails if one place forgets them.   python tests/test_protection.py"""
import pathlib
import re
import sys
import yaml

root = pathlib.Path(__file__).resolve().parent.parent
bad = 0


def check(ok, what):
    global bad
    print(("ok   - " if ok else "FAIL - ") + what)
    bad += 0 if ok else 1


def load(p):
    return yaml.safe_load((root / p).read_text())


AAP = {"aap", "aap_hosts"}
check(AAP <= set(load("roles/patch/vars/main.yml")["_site_always_protected"]), "roles/patch/vars: AAP always protected (cannot be removed by a setting)")
check(AAP <= set(load("roles/site_act/vars/main.yml")["_site_always_diagnose_only"]), "roles/site_act/vars: AAP always diagnose-only")

pd = load("roles/patch/defaults/main.yml")
for k in ("patch_never_reboot_groups", "patch_never_patch_groups"):
    check(AAP <= set(pd[k]), f"roles/patch/defaults: {k} lists AAP too (visible to the reader)")
check(AAP <= set(load("roles/site_act/defaults/main.yml")["site_act_diagnose_only_groups"]), "roles/site_act/defaults: diagnose-only groups list AAP")

wd = load("roles/win_patch/defaults/main.yml")
check("patch_never_reboot_groups" in str(wd["win_patch_never_reboot_groups"]), "win_patch never-reboot list follows the Linux list")

# A play that does not load a role's defaults falls back to a hard-coded list: it must be exactly the
# role default, or the protection silently shrinks there (all.yml holds no live lists any more).
role_default = {"patch_never_patch_groups": pd["patch_never_patch_groups"],
                "patch_never_reboot_groups": pd["patch_never_reboot_groups"],
                "site_act_diagnose_only_groups": load("roles/site_act/defaults/main.yml")["site_act_diagnose_only_groups"]}
found = 0
for p in sorted(list((root / "playbooks").glob("*.yml")) + list((root / "roles").glob("*/*/*.yml"))):
    for m in re.finditer(r"\b(%s)\s*\|\s*default\((\[[^\]]*\])" % "|".join(role_default), p.read_text()):
        found += 1
        rel = p.relative_to(root)
        check(yaml.safe_load(m.group(2)) == role_default[m.group(1)], f"{rel}: the fallback for {m.group(1)} equals the role default")
check(found >= 4, f"found the hard-coded fallbacks ({found}: act_fix_approved.yml, win_patch x2, service_watch)")

fix = (root / "playbooks/act_fix_approved.yml").read_text()
check(re.search(r"_no_apply.*\['aap', ?'aap_hosts'\]", fix, re.S) is not None, "act_fix_approved.yml: never applies fixes on AAP hosts")
check(re.search(r"meta: end_host\n\s+when: group_names \| intersect\(_no_apply \+ \['aap', 'aap_hosts'\]\)", fix) is not None,
      "act_fix_approved.yml: the skip tests the literal AAP groups (no setting or list name can remove them)")
tasks = (root / "roles/patch/tasks/main.yml").read_text()
check("_patch_no_patch" in tasks and "_patch_no_reboot" in tasks and "_site_always_protected" in tasks, "roles/patch/tasks: uses the always-protected list")
# extra variables beat every variable (even _site_always_protected / _patch_no_patch): the skip and the
# reboot also test the literal AAP group names, which no setting or list name can change
check(re.search(r"meta: end_host\n\s+when: group_names \| intersect\(_patch_no_patch \+ \['aap', 'aap_hosts'\]\)", tasks) is not None,
      "roles/patch/tasks: the skip tests the literal AAP groups")
check(re.search(r"ansible.builtin.reboot:(.|\n)*?when: .*intersect\(\['aap', 'aap_hosts'\]\) \| length == 0", tasks) is not None,
      "roles/patch/tasks: the reboot tests the literal AAP groups")
act = (root / "roles/site_act/tasks/main.yml").read_text()
check("_site_always_diagnose_only" in act, "roles/site_act/tasks: uses the always-diagnose-only list")

watch = (root / "roles/service_watch/tasks/watch.yml").read_text()
check(re.search(r"_watch_mode: >-\n\s+\{\{ 'approval' if \(watch_mode == 'self-heal' and group_names \| intersect\(_watch_no_heal \+ \['aap', 'aap_hosts'\]\)", watch) is not None,
      "service watch: self-heal is forced off on AAP and the diagnose-only groups (literal AAP groups)")
check(len(re.findall(r"(?<!_)watch_mode == 'self-heal'", watch)) == 1
      and "act_triage_allow: \"{{ _watch_fix_patterns if _watch_mode == 'self-heal'" in watch
      and re.search(r"- _watch_mode == 'self-heal'\n\s+- group_names \| intersect\(\['aap', 'aap_hosts'\]\) \| length == 0", watch) is not None,
      "service watch: ACT's allow list and the standard self-heal fix use the per-host mode (and never run on AAP)")
apply = (root / "roles/service_watch/tasks/apply.yml").read_text()
check(re.search(r"meta: end_host\n\s+when: group_names \| intersect\(_watch_no_heal \+ \['aap', 'aap_hosts'\]\) \| length > 0", apply) is not None,
      "service watch apply: skips AAP and the diagnose-only hosts (literal AAP groups)")
for f in ("playbooks/act_fix_approved.yml", "roles/service_watch/tasks/run_fixes.yml"):
    check("apply_guard.yml" in (root / f).read_text(), f"{f}: runs approved commands only through the apply-time guard")
check("_apply_refuse" in load("roles/site_findings/vars/main.yml"), "the guard pattern lives in one place (roles/site_findings/vars)")

# VMware jobs (restart, shut down, VLAN change) never touch the AAP VM: literal groups in the condition,
# checked before the change in each of them.
vguard = (root / "roles/vmware_vm/tasks/guard.yml").read_text()
check("item.ids | intersect(((groups['aap'] | default([])) + (groups['aap_hosts'] | default([]))) | host_ids(hostvars)) | length == 0" in vguard,
      "VMware guard: the AAP VM is refused through the literal groups aap / aap_hosts")
check("((groups['aap'] | default([])) + (groups['aap_hosts'] | default([]))) | length > 0" in vguard,
      "VMware guard: refuses when the inventory cannot say which VM is AAP")
check(AAP <= set(load("roles/vmware_vm/defaults/main.yml")["vmware_protected_groups"]), "roles/vmware_vm/defaults: protected groups list AAP")
for f in ("reboot.yml", "shutdown.yml", "vlan.yml"):
    txt = (root / "roles/vmware_vm/tasks" / f).read_text()
    check("guard.yml" in txt and txt.index("guard.yml") < txt.index("vmware.vmware.vm"), f"roles/vmware_vm/tasks/{f}: the AAP guard runs before the change")

for p in sorted((root / "playbooks/group_vars").glob("*.yml")) + [root / "inventories/example/group_vars/aap_hosts.yml"]:
    txt = p.read_text()
    live = [l for l in txt.splitlines() if re.match(r"^(patch_never|site_act_diagnose_only)", l)]
    if p.name == "all.yml":
        check(not live, "playbooks/group_vars/all.yml has no live safety-list lines (single source: role defaults)")

print("all checks passed" if not bad else f"{bad} failed")
sys.exit(1 if bad else 0)
