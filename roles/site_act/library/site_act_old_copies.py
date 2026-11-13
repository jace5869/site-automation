#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Old copies of ACT on a host (1.53.0): an `act` someone put by hand (/usr/local/bin/act) runs instead of the one the
jobs install (/opt/act/act), with its own ~/.config/act/config.json. Python 3.6 and newer (the managed nodes)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
module: site_act_old_copies
short_description: Find old copies of ACT; link, delete or report them; report the users' saved models
description:
  - Each path in I(paths) - missing, or a link to I(target) or into its folder - is left alone.
  - Anything else (an old file, a link elsewhere) is linked to I(target), deleted, or only reported (I(action)).
    Before a link or a delete the old one is kept in I(backup_dir) (0700) as act.<UTC time>.<sha256, first 8>.
  - Root's and each /home user's ~/.config/act/config.json is read for genai.model only; never the key, never printed.
    With I(fix_user_model) that one key is rewritten (a backup beside it, the file's mode and owner kept).
  - Check mode changes nothing and says what it would do.
options:
  paths: {description: The places an old act may be., type: list, elements: str, default: [/usr/local/bin/act]}
  action: {description: link, delete or report., type: str, default: link, choices: [link, delete, report]}
  target: {description: The installed ACT., type: str, default: /opt/act/act}
  backup_dir: {description: Where the old ones are kept., type: str, default: /opt/act/old}
  job_model: {description: The model the jobs pass (genai); a saved one that differs is reported., type: str, default: ''}
  fix_user_model: {description: Rewrite a user's genai.model to job_model (with a backup)., type: bool, default: false}
  homes: {description: The home folders looked in., type: list, elements: str, default: [/root, /home/*]}
  apply: {description: false = only decide (nothing changed, as check mode)., type: bool, default: true}
  installing: {description: The installed ACT is put in place just before (a check run did not put it)., type: bool, default: false}
"""

import datetime
import glob
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess

from ansible.module_utils.basic import AnsibleModule

VERSION_RE = re.compile(r'^ACT_VERSION\s*=\s*["\']([^"\']+)["\']', re.M)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def act_version(path):
    """The ACT_VERSION line of an act script ('' when there is none, or it cannot be read)."""
    try:
        with open(path, "rb") as f:
            head = f.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except (IOError, OSError):
        return ""
    m = VERSION_RE.search(head)
    return m.group(1) if m else ""


def inspect(path, target):
    """What is at path: missing, ours (a link to target or into its folder), file, link (elsewhere), other."""
    if not os.path.lexists(path):
        return {"path": path, "kind": "missing"}
    keep = os.path.dirname(os.path.realpath(target)) + os.sep
    if os.path.islink(path):
        dest = os.readlink(path)
        real = os.path.realpath(path)
        if real == os.path.realpath(target) or real.startswith(keep):
            return {"path": path, "kind": "ours", "points_to": dest}
        return {"path": path, "kind": "link", "points_to": dest,
                "version": act_version(real) if os.path.isfile(real) else ""}
    if os.path.isfile(path):
        return {"path": path, "kind": "file", "version": act_version(path)}
    return {"path": path, "kind": "other"}


def decide(found, action, target, target_there, python_ok):
    """What to do with one path: none, link, delete, report - and why (a short reason when it is not done)."""
    kind = found["kind"]
    if kind in ("missing", "ours"):
        return "none", ""
    if kind == "other":
        return "report", "not a file or a link: left alone"
    if action == "report":
        return "report", ""
    if action == "link":
        if not python_ok[0]:
            return "report", python_ok[1]
        if not target_there:
            return "report", "the installed ACT (%s) is not there: not linked" % target
    return action, ""


def python3_ok():
    """Typing `act` runs it with the python3 on PATH; the new ACT needs 3.8 or newer."""
    env = dict(os.environ, PATH="/usr/local/bin:/usr/bin:/bin")
    try:
        p = subprocess.Popen(["python3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        out, _ = p.communicate()
        ver = out.decode().strip()
    except (OSError, ValueError):
        return False, "no python3 on this host: not linked (the new ACT needs Python 3.8 or newer)"
    try:
        ok = tuple(int(x) for x in ver.split(".")) >= (3, 8)
    except ValueError:
        ok = False
    if ok:
        return True, ""
    return False, ("python3 here is %s: not linked, as typing act would fail (the new ACT needs 3.8 or newer)"
                   % (ver or "unknown"))


def backup(path, backup_dir, now):
    """Keep the old one: act.<UTC time>.<sha256 first 8> in backup_dir (0700). A link is kept as a link."""
    if not os.path.isdir(backup_dir):
        os.makedirs(backup_dir, 0o700)
    os.chmod(backup_dir, 0o700)
    if os.path.islink(path):
        dest = os.readlink(path)
        tag = hashlib.sha256(dest.encode("utf-8")).hexdigest()[:8]
        name = os.path.join(backup_dir, "act.%s.%s" % (now, tag))
        if not os.path.lexists(name):
            os.symlink(dest, name)
        return name
    name = os.path.join(backup_dir, "act.%s.%s" % (now, sha256(path)[:8]))
    if not os.path.exists(name):
        shutil.copy2(path, name)
    return name


def link(path, target):
    """ln -sf target path, in one step (a link made beside it, then renamed over it)."""
    tmp = "%s.site-act-%d" % (path, os.getpid())
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.rename(tmp, path)


def homes(patterns):
    out = []
    for p in patterns:
        out.extend(sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p])
    return [h for h in out if os.path.isdir(h)]


def user_of(home, st):
    try:
        return pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        return os.path.basename(home.rstrip("/")) or home


def read_model(cfg):
    """genai.model of an ACT config ('' when not set); None when the file is not JSON. Only that key is kept."""
    try:
        with open(cfg) as f:
            data = json.load(f)
    except (IOError, OSError, ValueError):
        return None
    g = data.get("genai") if isinstance(data, dict) else None
    m = g.get("model") if isinstance(g, dict) else None
    return m if isinstance(m, str) else ""


def fix_model(cfg, model, now):
    """Rewrite genai.model only. A backup beside it (the same mode and owner); the file's mode and owner kept."""
    st = os.stat(cfg)
    with open(cfg) as f:
        data = json.load(f)
    data.setdefault("genai", {})["model"] = model
    bak = "%s.%s.bak" % (cfg, now)
    shutil.copy2(cfg, bak)
    os.chown(bak, st.st_uid, st.st_gid)
    tmp = "%s.site-act-%d" % (cfg, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, st.st_mode & 0o7777)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    os.rename(tmp, cfg)
    return bak


def say_copy(r):
    v = (" (ACT %s)" % r["version"]) if r.get("version") else ""
    what = ("an old copy%s" % v) if r["kind"] == "file" else ("a link to %s%s" % (r.get("points_to", ""), v))
    if r["kind"] == "other":
        what = "something that is not a file or a link"
    act, would = r["action"], r["would"]
    if act == "link":
        done = ("would be linked to %s (the old one kept in %s)" if would else "linked to %s, the old one kept at %s") % (
            r["target"], r.get("backup") or r["backup_dir"])
    elif act == "delete":
        done = ("would be deleted (the old one kept in %s)" if would else "deleted, the old one kept at %s") % (
            r.get("backup") or r["backup_dir"])
    else:
        done = "left as it is" + ((": " + r["why"]) if r.get("why") else " (site_act_old_bin: report)")
    return "%s was %s: %s." % (r["path"], what, done)


def run(params, check_mode, now=None, python_check=python3_ok):
    now = now or datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    target = params["target"]
    apply = params["apply"] and not check_mode
    changed = False
    copies, lines = [], []
    # a check run did not put the installed ACT in place; a real run must find it there
    target_there = os.path.isfile(os.path.realpath(target)) or bool(params.get("installing") and not apply)
    new = act_version(target) if os.path.isfile(target) else ""
    py = None
    for path in params["paths"]:
        found = inspect(path, target)
        if found["kind"] == "missing":
            continue
        if found["kind"] in ("file", "link") and params["action"] == "link" and py is None:
            py = python_check()
        todo, why = decide(found, params["action"], target, target_there, py or (True, ""))
        r = dict(found, action=todo, why=why, would=not apply, backup_dir=params["backup_dir"],
                 target=target + ((" (ACT %s)" % new) if new else ""))
        if todo in ("link", "delete") and apply:
            r["backup"] = backup(path, params["backup_dir"], now)
            if todo == "link":
                link(path, target)
            else:
                os.unlink(path)
            changed = True
        copies.append(r)
        if todo != "none":
            lines.append(say_copy(r))
    users = []
    for home in homes(params["homes"]):
        cfg = os.path.join(home, ".config", "act", "config.json")
        if not os.path.isfile(cfg):
            continue
        st = os.stat(cfg)
        who = user_of(home, st)
        model = read_model(cfg)
        u = {"user": who, "file": cfg}
        if model is None:
            u["state"] = "unreadable"
            lines.append("user %s's ACT settings (%s) are not JSON: not read." % (who, cfg))
        elif not model or not params["job_model"] or model == params["job_model"]:
            u["state"] = "ok"
            u["model"] = model
        else:
            u["model"] = model
            u["state"] = "differs"
            if params["fix_user_model"]:
                if apply:
                    u["backup"] = fix_model(cfg, params["job_model"], now)
                    changed = True
                    lines.append("user %s's saved model was %s: now %s (the old file kept at %s)."
                                 % (who, model, params["job_model"], u["backup"]))
                else:
                    lines.append("user %s's saved model is %s: it would be set to %s (site_act_fix_user_model)."
                                 % (who, model, params["job_model"]))
            else:
                lines.append("user %s's saved model is %s: `act` uses it when typed; the jobs pass their own."
                             % (who, model))
        users.append(u)
    return {"changed": changed, "copies": copies, "users": users, "lines": lines,
            "target_there": target_there, "target_version": act_version(target) if os.path.isfile(target) else ""}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            paths=dict(type="list", elements="str", default=["/usr/local/bin/act"]),
            action=dict(type="str", default="link", choices=["link", "delete", "report"]),
            target=dict(type="str", default="/opt/act/act"),
            backup_dir=dict(type="str", default="/opt/act/old"),
            job_model=dict(type="str", default=""),
            fix_user_model=dict(type="bool", default=False),
            homes=dict(type="list", elements="str", default=["/root", "/home/*"]),
            apply=dict(type="bool", default=True),
            installing=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    try:
        result = run(module.params, module.check_mode)
    except (IOError, OSError) as e:
        module.fail_json(msg="old copies of ACT: %s" % e)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
