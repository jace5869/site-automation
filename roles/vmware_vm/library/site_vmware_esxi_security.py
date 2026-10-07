#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Set ESXi hosts' security settings through vCenter: the SSH (and ESXi Shell) service, advanced
settings such as the shell timeouts, and lockdown mode with its exception users. pyVmomi only:
the vmware.vmware collection has no lockdown or host advanced-settings module."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_vmware_esxi_security
short_description: Set ESXi hosts' SSH service, advanced settings and lockdown mode through vCenter
description:
  - For every connected host (or the ones named): the SSH service (TSM-SSH) and the ESXi Shell
    service (TSM) stopped and set to start manually (disabled) or started and set to start with
    the host (enabled); advanced settings set (e.g. UserVars.ESXiShellTimeOut); lockdown mode set to
    normal or disabled, after adding the exception users that must keep logging in directly.
  - Strict lockdown is refused, and a host already in strict is left as it is.
  - Changes only what differs; a host that already matches is not touched. Check mode changes
    nothing and says what it would change. A host that is not connected is skipped.
  - vCenter's address and account come from VMWARE_HOST, VMWARE_USER, VMWARE_PASSWORD (and
    VMWARE_PORT), as a "VMware vCenter" credential in AAP gives them.
options:
  datacenter: {description: Only this datacenter's hosts., type: str}
  hosts: {description: Only these hosts (names; * and ? allowed; the short name matches too). Empty = every host., type: list, elements: str, default: []}
  clusters: {description: Only the hosts of these clusters (names, wildcards)., type: list, elements: str, default: []}
  exclude: {description: Never these hosts (names, wildcards)., type: list, elements: str, default: []}
  ssh: {description: The SSH service. Empty = leave it., type: str, choices: [disabled, enabled, ''], default: ''}
  shell: {description: The ESXi Shell service. Empty = leave it., type: str, choices: [disabled, enabled, ''], default: ''}
  settings: {description: Advanced settings to set, name to value., type: dict, default: {}}
  lockdown: {description: Lockdown mode. Empty = leave it. strict is refused., type: str, default: ''}
  exception_users: {description: Added to the lockdown exception users before lockdown is turned on; never removed., type: list, elements: str, default: []}
  validate_certs: {description: Check vCenter's certificate., type: bool, default: true}
author: site automation
'''

RETURN = r'''
hosts:
  description: One record per host.
  type: list
  returned: success
  sample: [{name: esx01.example.mil, cluster: CL1, datacenter: DC1, connection: connected, status: changed,
            before: {SSH: "running, starts with the host", UserVars.ESXiShellTimeOut: 0, lockdown: disabled},
            changes: ["SSH stopped and set to start manually", "UserVars.ESXiShellTimeOut 0 -> 600", "lockdown disabled -> normal"],
            errors: []}]
'''

import fnmatch
import os

from ansible.module_utils.basic import AnsibleModule

try:
    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim, vmodl
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

SERVICES = {"ssh": ("TSM-SSH", "SSH"), "shell": ("TSM", "ESXi Shell")}
MODE = {"lockdownDisabled": "disabled", "lockdownNormal": "normal", "lockdownStrict": "strict"}
API_MODE = {"disabled": "lockdownDisabled", "normal": "lockdownNormal"}
POLICY = {"off": "starts manually", "on": "starts with the host", "automatic": "starts with its ports"}


def fault_text(e):
    """A vSphere fault (or any error) as one line. A missing privilege is named: that is what to grant."""
    pid = getattr(e, "privilegeId", None)
    if pid:
        return "the vCenter account lacks the privilege %s on this host" % pid
    msg = getattr(e, "msg", None) or str(e)
    msg = " ".join(str(msg).split())
    name = type(e).__name__.split(".")[-1]
    return "%s: %s" % (name, msg) if msg and name not in msg else (msg or name)


def _bool(v):
    return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")


def same_value(cur, want):
    """An advanced setting's current value equals the wanted one, compared as the current value's kind."""
    if isinstance(cur, bool):
        return cur == _bool(want)
    if isinstance(cur, int):
        try:
            return cur == int(str(want).strip())
        except ValueError:
            return False
    return str(cur) == str(want)


def _svc_text(running, policy):
    return "%s, %s" % ("running" if running else "stopped", POLICY.get(policy, policy))


def secure_host(api, want, check_mode=False):
    """Bring one host to the wanted settings. Pure: everything on the host goes through api, so it
    can be tested with a fake one.
      api   services() -> {key: (running, policy)}; stop_service / start_service(key); set_policy(key, p);
            option(key) -> value (KeyError: no such setting); set_option(key, value);
            lockdown() -> disabled | normal | strict; set_lockdown(mode); exceptions() -> [users];
            set_exceptions(users)
      want  {services: {key: disabled|enabled}, labels: {key: label}, settings: {name: value},
             lockdown: normal|disabled|'', exception_users: [...]}
    -> {before: {...}, changes: [...], notes: [...], errors: [...]}. In check mode nothing is changed
    (the changes are what would be done)."""
    rec = {"before": {}, "changes": [], "notes": [], "errors": []}
    labels = want.get("labels") or {}

    if want.get("services"):
        try:
            svcs = api.services()
        except Exception as e:                  # noqa: BLE001 - reported, the other settings still run
            rec["errors"].append("services: " + fault_text(e))
            svcs = None
        for key, state in sorted((want.get("services") or {}).items()):
            if svcs is None:
                break
            label = labels.get(key, key)
            if key not in svcs:
                rec["errors"].append("%s: the host has no service %s" % (label, key))
                continue
            running, policy = svcs[key]
            rec["before"][label] = _svc_text(running, policy)
            if state == "disabled":
                steps = ([("stop", None)] if running else []) + ([("policy", "off")] if policy != "off" else [])
                text = "%s stopped and set to start manually" % label
            else:
                steps = ([("policy", "on")] if policy != "on" else []) + ([("start", None)] if not running else [])
                text = "%s started and set to start with the host" % label
            if not steps:
                continue
            try:
                for what, arg in steps if not check_mode else []:
                    if what == "stop":
                        api.stop_service(key)
                    elif what == "start":
                        api.start_service(key)
                    else:
                        api.set_policy(key, arg)
                rec["changes"].append(text)
            except Exception as e:              # noqa: BLE001
                rec["errors"].append("%s: %s" % (label, fault_text(e)))

    for key, value in sorted((want.get("settings") or {}).items()):
        try:
            cur = api.option(key)
        except KeyError:
            rec["errors"].append("%s: the host has no such setting" % key)
            continue
        except Exception as e:                  # noqa: BLE001
            rec["errors"].append("%s: %s" % (key, fault_text(e)))
            continue
        rec["before"][key] = cur
        if same_value(cur, value):
            continue
        try:
            if not check_mode:
                api.set_option(key, value)
            rec["changes"].append("%s %s -> %s" % (key, cur, value))
        except Exception as e:                  # noqa: BLE001
            rec["errors"].append("%s: %s" % (key, fault_text(e)))

    mode = want.get("lockdown") or ""
    if mode:
        try:
            cur = api.lockdown()
        except Exception as e:                  # noqa: BLE001
            rec["errors"].append("lockdown: " + fault_text(e))
            cur = None
        if cur is not None:
            rec["before"]["lockdown"] = cur
            ready = True
            users = [u for u in (want.get("exception_users") or []) if str(u).strip()]
            if mode == "normal" and users:
                # first: the accounts that must keep logging in directly (never removes one)
                try:
                    have = list(api.exceptions())
                    known = {str(u).lower() for u in have}
                    missing = [u for u in users if str(u).lower() not in known]
                    if missing:
                        if not check_mode:
                            api.set_exceptions(have + missing)
                        rec["changes"].append("lockdown exception users added: " + ", ".join(missing))
                except Exception as e:          # noqa: BLE001
                    rec["errors"].append("lockdown exception users: %s - lockdown left %s, so they are not locked out"
                                         % (fault_text(e), cur))
                    ready = False
            if mode == "normal" and cur == "strict":
                rec["notes"].append("lockdown is strict: left as it is (stricter than normal)")
            elif ready and cur != mode:
                try:
                    if not check_mode:
                        api.set_lockdown(mode)
                    rec["changes"].append("lockdown %s -> %s" % (cur, mode))
                except Exception as e:          # noqa: BLE001
                    rec["errors"].append("lockdown: " + fault_text(e))
    return rec


def name_matches(name, patterns):
    """A host's name (or its short name) matches one of the patterns (* and ?, any case)."""
    n = str(name or "").lower()
    short = n.split(".")[0]
    return any(fnmatch.fnmatchcase(n, str(p).strip().lower()) or fnmatch.fnmatchcase(short, str(p).strip().lower())
               for p in patterns or [] if str(p).strip())


def pick_hosts(hosts, names=None, clusters=None, exclude=None, datacenter=None):
    """The hosts (dicts: name, cluster, datacenter, ...) the job acts on, and the excluded ones."""
    out, excluded = [], []
    for h in hosts:
        if datacenter and h.get("datacenter") != datacenter:
            continue
        if names and not name_matches(h["name"], names):
            continue
        if clusters and not any(fnmatch.fnmatchcase(str(h.get("cluster") or "").lower(), str(c).strip().lower())
                                for c in clusters if str(c).strip()):
            continue
        (excluded if exclude and name_matches(h["name"], exclude) else out).append(h)
    return out, excluded


# ---- vCenter ------------------------------------------------------------------------------------
class HostApi(object):
    """The host's settings through vCenter (pyVmomi)."""

    def __init__(self, host):
        cm = host.configManager
        self.ss, self.ao, self.ham = cm.serviceSystem, cm.advancedOption, cm.hostAccessManager

    def services(self):
        return {s.key: (bool(s.running), str(s.policy)) for s in self.ss.serviceInfo.service or []}

    def stop_service(self, key):
        self.ss.StopService(id=key)

    def start_service(self, key):
        self.ss.StartService(id=key)

    def set_policy(self, key, policy):
        self.ss.UpdateServicePolicy(id=key, policy=policy)

    def option(self, key):
        try:
            opts = self.ao.QueryOptions(name=key)
        except vim.fault.InvalidName:
            raise KeyError(key)
        for o in opts or []:
            if o.key == key:
                return o.value
        raise KeyError(key)

    def set_option(self, key, value):
        cur = self.option(key)
        # the type the host has for it: a plain int is refused for a long setting
        if isinstance(cur, bool):
            v = _bool(value)
        elif isinstance(cur, int):
            v = type(cur)(int(str(value).strip()))
        else:
            v = str(value)
        self.ao.UpdateOptions(changedValue=[vim.option.OptionValue(key=key, value=v)])

    def lockdown(self):
        m = str(self.ham.lockdownMode)
        return MODE.get(m, m)

    def set_lockdown(self, mode):
        self.ham.ChangeLockdownMode(mode=API_MODE[mode])

    def exceptions(self):
        return list(self.ham.QueryLockdownExceptions() or [])

    def set_exceptions(self, users):
        self.ham.UpdateLockdownExceptions(users=list(users))


def _datacenter_of(obj, cache):
    chain, x = [], obj
    while x is not None and x._moId not in cache and not isinstance(x, vim.Datacenter):
        chain.append(x._moId)
        try:
            x = x.parent
        except Exception:                       # noqa: BLE001
            x = None
    name = cache.get(x._moId, "") if x is not None and x._moId in cache else (x.name if isinstance(x, vim.Datacenter) else "")
    for m in chain:
        cache[m] = name
    return name


def main():
    module = AnsibleModule(
        argument_spec=dict(datacenter=dict(type="str"), hosts=dict(type="list", elements="str", default=[]),
                           clusters=dict(type="list", elements="str", default=[]),
                           exclude=dict(type="list", elements="str", default=[]),
                           ssh=dict(type="str", choices=["disabled", "enabled", ""], default=""),
                           shell=dict(type="str", choices=["disabled", "enabled", ""], default=""),
                           settings=dict(type="dict", default={}), lockdown=dict(type="str", default=""),
                           exception_users=dict(type="list", elements="str", default=[]),
                           validate_certs=dict(type="bool", default=True)),
        supports_check_mode=True,
    )
    p = module.params
    lockdown = (p["lockdown"] or "").strip().lower()
    if lockdown == "strict":
        module.fail_json(msg="lockdown strict is refused: it turns the DCUI off, so when vCenter is down the host can be "
                             "reached only by its exception users. Use normal.")
    if lockdown not in ("", "normal", "disabled"):
        module.fail_json(msg="lockdown must be normal, disabled or empty, not %s" % p["lockdown"])
    if not HAS_PYVMOMI:
        module.fail_json(msg="pyVmomi is not installed in the execution environment (the vmware.vmware collection needs it too)")
    host = os.environ.get("VMWARE_HOST")
    if not host:
        module.fail_json(msg='No vCenter to talk to: attach a credential of type "VMware vCenter" to this job template.')
    want = {"services": {SERVICES[k][0]: p[k] for k in ("ssh", "shell") if p[k]},
            "labels": {v[0]: v[1] for v in SERVICES.values()},
            "settings": p["settings"] or {}, "lockdown": lockdown, "exception_users": p["exception_users"]}
    try:
        si = SmartConnect(host=host, user=os.environ.get("VMWARE_USER", ""), pwd=os.environ.get("VMWARE_PASSWORD", ""),
                          port=int(os.environ.get("VMWARE_PORT") or 443),
                          disableSslCertValidation=not p["validate_certs"])
    except vim.fault.InvalidLogin:
        module.fail_json(msg="vCenter %s refused the login (the VMware vCenter credential)" % host)
    except Exception as e:                      # noqa: BLE001
        module.fail_json(msg="Could not connect to vCenter %s: %s" % (host, e))
    out = []
    try:
        content = si.RetrieveContent()
        view = content.viewManager.CreateContainerView(content.rootFolder, [vim.HostSystem], True)
        spec = vmodl.query.PropertyCollector.FilterSpec(
            objectSet=[vmodl.query.PropertyCollector.ObjectSpec(
                obj=view, skip=True,
                selectSet=[vmodl.query.PropertyCollector.TraversalSpec(name="v", path="view", skip=False, type=vim.view.ContainerView)])],
            propSet=[vmodl.query.PropertyCollector.PropertySpec(type=vim.HostSystem,
                                                                 pathSet=["name", "parent", "runtime.connectionState",
                                                                          "runtime.inMaintenanceMode"])])
        cache, found = {}, []
        for o in content.propertyCollector.RetrieveContents([spec]) or []:
            pr = {x.name: x.val for x in o.propSet}
            parent = pr.get("parent")
            found.append({"name": pr.get("name", ""), "obj": o.obj,
                          "cluster": parent.name if isinstance(parent, vim.ClusterComputeResource) else "",
                          "datacenter": _datacenter_of(parent, cache) if parent is not None else "",
                          "connection": str(pr.get("runtime.connectionState") or ""),
                          "maintenance": bool(pr.get("runtime.inMaintenanceMode"))})
        view.Destroy()
        found.sort(key=lambda h: (h["datacenter"], h["cluster"], h["name"]))
        hosts, excluded = pick_hosts(found, p["hosts"], p["clusters"], p["exclude"], p["datacenter"])
        if not hosts and not excluded:
            module.fail_json(msg="No host matches (hosts: %s; clusters: %s; datacenter: %s) among the %d host(s) of vCenter %s"
                                 % (", ".join(p["hosts"]) or "all", ", ".join(p["clusters"]) or "all", p["datacenter"] or "all",
                                    len(found), host))
        for h in excluded:
            out.append(dict({k: h[k] for k in ("name", "cluster", "datacenter", "connection", "maintenance")},
                            status="excluded", before={}, changes=[], notes=["on the exclusion list"], errors=[]))
        for h in hosts:
            base = {k: h[k] for k in ("name", "cluster", "datacenter", "connection", "maintenance")}
            if h["connection"] != "connected":
                out.append(dict(base, status="skipped", before={}, changes=[], notes=["not connected to vCenter (%s)" % h["connection"]],
                                errors=[]))
                continue
            try:
                rec = secure_host(HostApi(h["obj"]), want, module.check_mode)
            except Exception as e:              # noqa: BLE001
                rec = {"before": {}, "changes": [], "notes": [], "errors": [fault_text(e)]}
            rec["status"] = "failed" if rec["errors"] else ("changed" if rec["changes"] else "ok")
            out.append(dict(base, **rec))
    except Exception as e:                      # noqa: BLE001
        module.fail_json(msg="Setting the hosts through vCenter %s failed: %s" % (host, fault_text(e)), hosts=out)
    finally:
        try:
            Disconnect(si)
        except Exception:                       # noqa: BLE001
            pass
    module.exit_json(changed=any(h["status"] in ("changed", "failed") and h["changes"] for h in out) and not module.check_mode,
                     hosts=out, check_mode=module.check_mode)


if __name__ == "__main__":
    main()
