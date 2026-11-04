#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Evaluate-STIG - update (playbooks/evaluate_stig_update.yml): the new Evaluate-STIG zip read on the AAP side, each
host's Evaluate-STIG read, and - only where the zip is newer - the new one put in its place: unpacked beside it, its
version checked, the site's own answer files carried over, a quick run to prove it works, the old folder kept as
<folder>.previous-<version> (put back if the quick run fails). Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_evaluate_stig
short_description: Read an Evaluate-STIG zip, read the Evaluate-STIG on a host, or put the zip's in its place
description:
  - action=zip (on the AAP side) - the zip's version, the RHEL STIG revisions its content has, where its script is.
    Given a folder, the zip of the newest version in it.
  - action=inspect (on the host) - the Evaluate-STIG in O(dest): its version, its STIG content, whether it runs now, and
    what to do (update, up to date, newer than the zip, not installed, unknown version, running).
  - action=install (on the host) - O(zip) put in place of O(dest). In check mode it only inspects.
options:
  action: {description: zip, inspect or install., type: str, required: true, choices: [zip, inspect, install]}
  path: {description: "action=zip: the zip, or a folder of zips (the newest version is taken).", type: str, default: ''}
  dest: {description: "Evaluate-STIG on the host: its folder, its Src/Evaluate-STIG folder or its script.", type: str, default: ''}
  zip: {description: action=install - the zip, on the host., type: str, default: ''}
  want: {description: action=inspect/install - the zip's version., type: str, default: ''}
  sha256: {description: action=install - the zip's SHA-256 (checked before it is unpacked)., type: str, default: ''}
  force: {description: Put the zip's in place even when the host's is the same or newer., type: bool, default: false}
  install_missing: {description: A host without Evaluate-STIG in dest gets it., type: bool, default: false}
  keep: {description: "Folders of the old Evaluate-STIG whose files are carried into the new one (a file the zip also has: the old one's).", type: list, elements: str, default: [AnswerFiles]}
  backups: {description: How many earlier folders to keep (<dest>.previous-<version>); 0 = none., type: int, default: 1}
  smoke: {description: "The quick run after the swap: auto (Evaluate-STIG_Bash.sh --ListSupportedProducts, else pwsh Evaluate-STIG.ps1 -ListSupportedProducts), none, or a command ({dir}: the script's folder).", type: str, default: auto}
  smoke_timeout: {description: Seconds the quick run may take., type: int, default: 600}
  pwsh: {description: PowerShell 7 for the quick run of Evaluate-STIG.ps1 ("" = pwsh in PATH)., type: str, default: ''}
  version_regex: {description: Where the version is in Evaluate-STIG.ps1 (its first group)., type: str}
author: site automation
'''

import fnmatch
import hashlib
import os
import re
import shutil
import subprocess
import time
import zipfile

from ansible.module_utils.basic import AnsibleModule

SCRIPT = "Evaluate-STIG.ps1"
BASH = "Evaluate-STIG_Bash.sh"
# its header ("# # Version:     1.2604.0"), else a $EvaluateStigVersion = "..." line
VERSION_RE = r"""(?:^[ \t]*#[ \t#]*Version:[ \t]*|\$EvaluateStigVersion\s*=\s*["'])([0-9]+(?:\.[0-9]+)+)"""
NAME_RE = re.compile(r"Evaluate-STIG[ _-]?v?([0-9]+(?:\.[0-9]+)+)", re.I)
# the RHEL STIG content: U_RHEL_8_STIG_V2R9_Manual-xccdf.xml, U_RHEL_9_V2R4_STIG.xml ...
CONTENT_RE = re.compile(r"U_RHEL_(\d+)[A-Za-z_]*?_V(\d+)R(\d+)", re.I)


def vtuple(v):
    """'1.2507.0' -> (1, 2507, 0); '' or not a version -> None."""
    v = str(v or "").strip().lstrip("vV")
    if not re.match(r"^\d+(\.\d+)*$", v):
        return None
    t = [int(x) for x in v.split(".")]
    while len(t) > 1 and t[-1] == 0:
        t.pop()
    return tuple(t)


def newer(a, b):
    """a newer than b (both versions); None when either is unknown."""
    ta, tb = vtuple(a), vtuple(b)
    if ta is None or tb is None:
        return None
    return ta > tb


def version_in(text, regex=VERSION_RE):
    m = re.search(regex, text or "", re.I | re.M)
    return m.group(1) if m else ""


def content_of(names):
    """File names -> {'RHEL 8': 'V2R9', ...}: the newest revision of each RHEL STIG in them."""
    best = {}
    for n in names:
        m = CONTENT_RE.search(os.path.basename(n))
        if not m:
            continue
        key, rev = "RHEL %s" % m.group(1), (int(m.group(2)), int(m.group(3)))
        if key not in best or rev > best[key]:
            best[key] = rev
    return dict((k, "V%dR%d" % v) for k, v in sorted(best.items()))


def _read_text(data):
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return ""


# ---- the zip, on the AAP side ----
def zip_info(path, regex=VERSION_RE):
    """A zip -> {path, version, from, content, prefix (the folder its files are in, stripped), script (where
    Evaluate-STIG.ps1 is under it), files, bytes (unpacked), sha256} or {error}."""
    out = {"path": path, "version": "", "from": "", "content": {}, "prefix": "", "script": "", "files": 0, "bytes": 0,
           "sha256": "", "left_out": [], "error": ""}
    try:
        z = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipfile) as e:
        out["error"] = "%s is not a zip that can be read: %s" % (path, e)
        return out
    with z:
        infos = [i for i in z.infolist() if not i.filename.endswith("/")]
        names = [i.filename.replace("\\", "/") for i in infos]
        bad = [n for n in names if n.startswith("/") or ".." in n.split("/")]
        if bad:
            out["error"] = "%s has a path outside its folder (%s): not used" % (path, bad[0])
            return out
        scripts = sorted((n for n in names if n.split("/")[-1].lower() == SCRIPT.lower()), key=lambda n: n.count("/"))
        if not scripts:
            out["error"] = "%s has no %s: not an Evaluate-STIG zip" % (path, SCRIPT)
            return out
        # what goes in the host's folder: the folder the script is in (or the one above its Src/Evaluate-STIG) - the
        # zip's other files beside it (a readme, a checksum) are not unpacked, and named
        sdir = scripts[0].rsplit("/", 1)[0] + "/" if "/" in scripts[0] else ""
        if sdir.lower().endswith("src/evaluate-stig/"):
            sdir = sdir[:-len("src/evaluate-stig/")]
        out["prefix"] = sdir
        out["script"] = scripts[0][len(sdir):]
        out["left_out"] = [n for n in names if not n.startswith(sdir)]
        infos = [i for i, n in zip(infos, names) if n.startswith(sdir)]
        info = [i for i in z.infolist() if i.filename.replace("\\", "/") == scripts[0]][0]
        out["version"] = version_in(_read_text(z.read(info)), regex)
        out["from"] = SCRIPT if out["version"] else ""
        if not out["version"]:
            m = NAME_RE.search(os.path.basename(path))
            out["version"], out["from"] = (m.group(1), "the file name") if m else ("", "")
        out["content"] = content_of([n for n in names if n.startswith(sdir)])
        out["files"] = len(infos)
        out["bytes"] = sum(i.file_size for i in infos)
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    out["sha256"] = h.hexdigest()
    return out


def pick_zip(path, regex=VERSION_RE):
    """A zip, or a folder of them -> (the newest version's info, [the others' names]) or ({error}, [])."""
    if os.path.isdir(path):
        zips = sorted(os.path.join(path, n) for n in os.listdir(path) if fnmatch.fnmatch(n.lower(), "*.zip"))
        if not zips:
            return {"error": "no .zip in %s: put the Evaluate-STIG zip there (docs/EVALUATE_STIG_UPDATE.md)" % path}, []
        infos = [zip_info(p, regex) for p in zips]
        good = [i for i in infos if not i["error"] and vtuple(i["version"])]
        if not good:
            return {"error": "; ".join(i["error"] or "%s: its version could not be read" % i["path"] for i in infos)}, []
        good.sort(key=lambda i: vtuple(i["version"]))
        best = good[-1]
        return best, [os.path.basename(i["path"]) for i in infos if i is not best]
    if not os.path.isfile(path):
        return {"error": "%s is not there (evaluate_stig_update_zip)" % path}, []
    info = zip_info(path, regex)
    if not info["error"] and not vtuple(info["version"]):
        info["error"] = "%s: its version could not be read (in %s, or the file name)" % (path, SCRIPT)
    return info, []


# ---- the host ----
def root_of(dest):
    """What the setting names (the folder, its Src/Evaluate-STIG, or the script) -> the folder Evaluate-STIG is in."""
    d = os.path.normpath(dest or "")
    if os.path.isfile(d) or d.lower().endswith((".ps1", ".sh")):
        d = os.path.dirname(d)
    parts = d.split(os.sep)
    if len(parts) >= 3 and parts[-1].lower() == "evaluate-stig" and parts[-2].lower() == "src":
        d = os.sep.join(parts[:-2])
    return d


def find_script(root, depth=3):
    """Evaluate-STIG.ps1 under root (the shallowest), or ''."""
    if not os.path.isdir(root):
        return ""
    base = root.rstrip(os.sep).count(os.sep)
    found = []
    for d, dirs, files in os.walk(root):
        if d.count(os.sep) - base >= depth:
            dirs[:] = []
        for f in files:
            if f.lower() == SCRIPT.lower():
                found.append(os.path.join(d, f))
    return sorted(found, key=lambda p: p.count(os.sep))[0] if found else ""


def running(proc="/proc"):
    """Evaluate-STIG running now (a scan of the drift report or the recheck): [(pid, its command)] - a process whose
    program or first argument is Evaluate-STIG_Bash.sh or Evaluate-STIG.ps1."""
    me, out = os.getpid(), []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return out
    for p in pids:
        if int(p) == me:
            continue
        try:
            with open(os.path.join(proc, p, "cmdline"), "rb") as fh:
                argv = [a.decode("utf-8", "replace") for a in fh.read().split(b"\0") if a]
        except (OSError, IOError):
            continue
        heads = [os.path.basename(a).lower() for a in argv[:4] if not a.startswith("-")][:2]
        if any(h in (SCRIPT.lower(), BASH.lower()) for h in heads):
            out.append((int(p), " ".join(argv)[:120]))
    return out


def inspect(dest, want="", force=False, install_missing=False, regex=VERSION_RE, proc="/proc"):
    """{root, script, version, content, decision, why, running}: decision update / current / newer / missing / unknown /
    running."""
    root = root_of(dest)
    r = {"root": root, "script": "", "version": "", "content": {}, "decision": "", "why": "", "running": []}
    r["script"] = find_script(root)
    if r["script"]:
        try:
            with open(r["script"], "rb") as fh:
                r["version"] = version_in(_read_text(fh.read()), regex)
        except (OSError, IOError):
            r["version"] = ""
        names = []
        for d, dirs, files in os.walk(root):
            names += files
        r["content"] = content_of(names)
    found = running(proc)
    r["running"] = [pid for pid, _ in found]
    if not r["script"]:
        r["decision"], r["why"] = ("update", "not installed: installed from the zip") if install_missing else (
            "missing", "no %s in %s (evaluate_stig_update_install_missing: true installs it)" % (SCRIPT, root))
    elif not r["version"]:
        r["decision"], r["why"] = ("update", "its version could not be read: forced") if force else (
            "unknown", "its version could not be read in %s: left as it is (evaluate_stig_update_force: true replaces it)" % r["script"])
    elif force:
        r["decision"], r["why"] = "update", "forced (%s; the zip: %s)" % (r["version"], want)
    elif newer(want, r["version"]):
        r["decision"], r["why"] = "update", "%s -> %s" % (r["version"], want)
    elif vtuple(want) == vtuple(r["version"]):
        r["decision"], r["why"] = "current", "%s: up to date" % r["version"]
    else:
        r["decision"], r["why"] = "newer", "%s is newer than the zip's %s: left as it is" % (r["version"], want)
    if r["decision"] == "update" and found:
        r["decision"], r["why"] = "running", "Evaluate-STIG is running now (process %s: %s): left as it is - run this again later" % (
            found[0][0], found[0][1])
    return r


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack(zpath, prefix, into):
    """The zip's files under prefix, into the folder 'into': modes from the zip (else 0644, 0755 for folders and .sh),
    root-owned when run as root."""
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/")
            if prefix and not name.startswith(prefix):
                continue
            rel = name[len(prefix):]
            if not rel or rel.startswith("/") or ".." in rel.split("/"):
                continue
            target = os.path.join(into, *rel.rstrip("/").split("/"))
            if name.endswith("/"):
                if not os.path.isdir(target):
                    os.makedirs(target, 0o755)
                continue
            parent = os.path.dirname(target)
            if not os.path.isdir(parent):
                os.makedirs(parent, 0o755)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            mode = (info.external_attr >> 16) & 0o777
            if not mode:
                mode = 0o755 if rel.lower().endswith(".sh") else 0o644
            os.chmod(target, 0o755 if rel.lower().endswith(".sh") else (mode & 0o755) | 0o644)
    for d, dirs, files in os.walk(into):
        os.chmod(d, 0o755)
        if os.geteuid() == 0:
            os.chown(d, 0, 0)
            for f in files:
                os.chown(os.path.join(d, f), 0, 0)


def carry(old_root, new_root, keep):
    """The old Evaluate-STIG's files in the keep folders, into the new one: ([kept], [replaced: the zip had them too])."""
    kept, replaced = [], []
    for k in keep or []:
        src = os.path.join(old_root, k)
        if not os.path.isdir(src):
            continue
        for d, dirs, files in os.walk(src):
            for f in files:
                s = os.path.join(d, f)
                rel = os.path.relpath(s, old_root)
                t = os.path.join(new_root, rel)
                if os.path.isfile(t):
                    with open(s, "rb") as a, open(t, "rb") as b:
                        if a.read() == b.read():
                            continue
                    replaced.append(rel)
                else:
                    kept.append(rel)
                if not os.path.isdir(os.path.dirname(t)):
                    os.makedirs(os.path.dirname(t), 0o755)
                shutil.copy2(s, t)
    return kept, replaced


def smoke_cmd(root, smoke, pwsh=""):
    """The quick run: argv, or [] for none."""
    if smoke in ("", "none", None):
        return []
    script = find_script(root)
    sdir = os.path.dirname(script) if script else root
    if smoke != "auto":
        return ["/bin/sh", "-c", smoke.replace("{dir}", sdir)]
    for d in (root, sdir):
        if os.path.isfile(os.path.join(d, BASH)):
            return ["/bin/bash", os.path.join(d, BASH), "--ListSupportedProducts"]
    exe = pwsh or shutil.which("pwsh") or ""
    if not exe:
        return []
    return [exe, "-NoProfile", "-File", script, "-ListSupportedProducts"]


def prune(root, backups):
    """The earlier folders beyond the number kept: removed (the oldest first). Their names."""
    parent, base = os.path.dirname(root), os.path.basename(root) + ".previous-"
    olds = sorted((os.path.join(parent, n) for n in os.listdir(parent) if n.startswith(base)), key=os.path.getmtime)
    gone = olds[:max(len(olds) - max(backups, 0), 0)]
    for g in gone:
        shutil.rmtree(g, ignore_errors=True)
    return [os.path.basename(g) for g in gone]


def install(p, run=subprocess.run):
    """Put the zip's Evaluate-STIG in place of the host's -> {changed, decision, why, version_before, version, content_before,
    content, previous, kept, replaced, preferences, smoke, pruned, error}."""
    regex = p.get("version_regex") or VERSION_RE
    insp = inspect(p["dest"], p["want"], p["force"], p["install_missing"], regex, p.get("proc", "/proc"))
    out = {"changed": False, "decision": insp["decision"], "why": insp["why"], "root": insp["root"], "version_before": insp["version"],
           "version": insp["version"], "content_before": insp["content"], "content": insp["content"], "previous": "", "kept": [],
           "replaced": [], "preferences": "", "smoke": "", "pruned": [], "error": ""}
    if insp["decision"] != "update":
        return out
    root = insp["root"]
    if p.get("sha256") and _sha256(p["zip"]).lower() != p["sha256"].lower():
        out["error"] = "the zip copied to the host does not match the one read on the AAP side (SHA-256): nothing changed"
        return out
    zi = zip_info(p["zip"], regex)
    if zi["error"]:
        out["error"] = zi["error"]
        return out
    parent = os.path.dirname(root) or "/"
    if not os.path.isdir(parent):
        os.makedirs(parent, 0o755)
    st = os.statvfs(parent)
    free = st.f_bavail * st.f_frsize
    if free < zi["bytes"] * 1.2 + (50 << 20):
        out["error"] = "not enough space in %s: %d MB free, the new Evaluate-STIG takes %d MB: nothing changed" % (
            parent, free >> 20, zi["bytes"] >> 20)
        return out
    stamp = time.strftime("%Y%m%d%H%M%S")
    new = "%s.new-%s" % (root, stamp)
    try:
        os.makedirs(new, 0o755)
        unpack(p["zip"], zi["prefix"], new)
        script = find_script(new)
        got = version_in(_read_text(open(script, "rb").read()), regex) if script else ""
        if not script:
            raise RuntimeError("the unpacked zip has no %s" % SCRIPT)
        if vtuple(got) and vtuple(got) != vtuple(p["want"]):
            raise RuntimeError("the unpacked %s says %s, not %s" % (SCRIPT, got, p["want"]))
        rel = os.path.relpath(os.path.dirname(script), new)
        if rel not in (".", os.path.join("Src", "Evaluate-STIG")):
            raise RuntimeError("the new %s is in %s - not where the STIG jobs look (the folder, or Src/Evaluate-STIG)" % (SCRIPT, rel))
        if os.path.isdir(root):
            out["kept"], out["replaced"] = carry(root, new, p.get("keep") or [])
            for pref in ("Preferences.xml",):
                a, b = find_named(root, pref), find_named(new, pref)
                if a and b:
                    with open(a, "rb") as fa, open(b, "rb") as fb:
                        if fa.read() != fb.read():
                            out["preferences"] = ("%s differs from the old one (the new one is used): put your settings in it "
                                                  "again if you had changed it - the old one is in the previous folder" % pref)
    except Exception as e:                      # noqa: BLE001 - whatever went wrong, the host keeps its Evaluate-STIG
        shutil.rmtree(new, ignore_errors=True)
        out["error"] = "%s: nothing changed" % e
        return out
    previous = ""
    if os.path.isdir(root):
        previous = "%s.previous-%s" % (root, insp["version"] or stamp)
        if os.path.exists(previous):
            shutil.rmtree(previous, ignore_errors=True)
        os.rename(root, previous)
    os.rename(new, root)
    if os.path.exists("/sys/fs/selinux/enforce") and shutil.which("restorecon"):
        run(["restorecon", "-R", root], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out["changed"], out["previous"] = True, previous
    cmd = smoke_cmd(root, p.get("smoke", "auto"), p.get("pwsh", ""))
    if cmd:
        try:
            r = run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=int(p.get("smoke_timeout") or 600), cwd=root)
            ok, said = r.returncode == 0, (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        except (OSError, subprocess.TimeoutExpired) as e:
            ok, said = False, [str(e)]
        if not ok:
            shutil.rmtree(root, ignore_errors=True)
            if previous:
                os.rename(previous, root)
            out["changed"], out["previous"] = False, ""
            out["error"] = "the new Evaluate-STIG did not run (%s: %s) - %s" % (
                _shown(cmd), (said[-1] if said else "no output")[:300],
                "the old one is back in place" if previous else "it was removed again")
            return out
        out["smoke"] = "%s ran (%d lines)" % (_shown(cmd), len(said))
    else:
        out["smoke"] = "no quick run (evaluate_stig_update_smoke: none, or no pwsh)"
    if previous:
        out["pruned"] = prune(root, int(p.get("backups", 1)))
        if int(p.get("backups", 1)) <= 0:
            out["previous"] = ""
    out["version"] = p["want"]
    names = []
    for d, dirs, files in os.walk(root):
        names += files
    out["content"] = content_of(names)
    return out


def _shown(cmd):
    return " ".join(os.path.basename(c) if os.sep in c else c for c in cmd)


def find_named(root, name, depth=3):
    base = root.rstrip(os.sep).count(os.sep)
    for d, dirs, files in os.walk(root):
        if d.count(os.sep) - base >= depth:
            dirs[:] = []
        if name in files:
            return os.path.join(d, name)
    return ""


def main():
    module = AnsibleModule(argument_spec=dict(
        action=dict(type="str", required=True, choices=["zip", "inspect", "install"]), path=dict(type="str", default=""),
        dest=dict(type="str", default=""), zip=dict(type="str", default=""), want=dict(type="str", default=""),
        sha256=dict(type="str", default=""), force=dict(type="bool", default=False), install_missing=dict(type="bool", default=False),
        keep=dict(type="list", elements="str", default=["AnswerFiles"]), backups=dict(type="int", default=1),
        smoke=dict(type="str", default="auto"), smoke_timeout=dict(type="int", default=600), pwsh=dict(type="str", default=""),
        version_regex=dict(type="str", default=VERSION_RE)), supports_check_mode=True)
    p = module.params
    if p["action"] == "zip":
        info, others = pick_zip(p["path"], p["version_regex"])
        module.exit_json(changed=False, zip=info, others=others)
    if not p["dest"]:
        module.fail_json(msg="no Evaluate-STIG folder: set check_stig_evaluate (or evaluate_stig_update_dest)")
    if p["action"] == "inspect" or module.check_mode:
        r = inspect(p["dest"], p["want"], p["force"], p["install_missing"], p["version_regex"])
        module.exit_json(changed=False, **r)
    r = install(p)
    module.exit_json(**r)


if __name__ == "__main__":
    main()
