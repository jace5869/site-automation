# -*- coding: utf-8 -*-
"""Several vCenters, one report: the site_vmware_* modules read every vCenter of the job - the one of
the "VMware vCenter" credential (VMWARE_HOST) and the ones in vmware_vcenters - with the same account
(VMWARE_USER / VMWARE_PASSWORD: in Enhanced Linked Mode the vCenters share single sign-on).

The same vCenter under two names (a short name and its FQDN, or its IP) is read once: vCenter's own
instance UUID tells. A vCenter that cannot be reached does not stop the others: it is returned with
its error, and the report lists it."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os

try:
    from pyVim.connect import Disconnect, SmartConnect
    from pyVmomi import vim
    HAS_PYVMOMI = True
except ImportError:
    HAS_PYVMOMI = False

NO_VCENTER = 'No vCenter to talk to: attach a credential of type "VMware vCenter" to this job template.'


class Skip(Exception):
    """Raised by a reader when this vCenter has nothing for the job (e.g. not the datacenter asked
    for): the vCenter is read, with nothing from it, and the message is kept."""


def vcenter_list(extra=None, env=None):
    """The vCenters to read: the credential's (VMWARE_HOST) first, then extra; each once (the name
    compared in lower case, without https:// and a trailing /). An entry may be 'name:port'."""
    env = os.environ if env is None else env
    out, seen = [], set()
    for v in [env.get("VMWARE_HOST", "")] + list(extra or []):
        v = str(v or "").strip()
        for pre in ("https://", "http://"):
            if v.lower().startswith(pre):
                v = v[len(pre):]
        v = v.rstrip("/").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def host_port(v, env=None):
    """'vc01:8443' -> ('vc01', 8443); 'vc01' -> ('vc01', VMWARE_PORT or 443). An IPv6 address (more
    than one colon) is taken as it is."""
    env = os.environ if env is None else env
    port = int(env.get("VMWARE_PORT") or 443)
    if v.count(":") == 1:
        h, p = v.split(":")
        if p.isdigit():
            return h, int(p)
    return v, port


def refused(name, env=None):
    """The message for a login vCenter refused: the likely reason, from the user name's form (never
    the name itself)."""
    env = os.environ if env is None else env
    user = str(env.get("VMWARE_USER") or "").strip()
    why = ("the credential has no user name (a credential type whose injector does not set VMWARE_USER: use the "
           "built-in VMware vCenter type)" if not user else
           "the user name has no domain: a local vCenter account is written name@vsphere.local, a domain account "
           "name@yourdomain" if "@" not in user and "\\" not in user else
           "the user name or password is wrong, the account is locked or its password expired - or this vCenter does "
           "not know the account (every vCenter of the job is logged in to with it: a vsphere.local account works on "
           "the vCenters of its own single sign-on domain, in linked mode, only)")
    return "vCenter %s refused the login (the VMware vCenter credential): %s" % (name, why)


def no_rights(name, e=None):
    """The message for a login vCenter accepted but the account has no role in that vCenter at all:
    vCenter then refuses the session (System.View on the root folder)."""
    pid = getattr(e, "privilegeId", None) or "System.View"
    return ("vCenter %s accepted the login, but the account has no permission in that vCenter (it lacks %s on the top "
            "of the inventory, which vCenter gives only to an account with a role somewhere in it): give the account a role "
            "there - a Global Permission (vSphere Client: Administration > Access Control > Global Permissions, Propagate to "
            "children) covers every vCenter of its single sign-on domain. Read-only is enough where the job only reads; the "
            "deploy reads every vCenter of vmware_vcenters, to check the new VM's name is free" % (name, pid))


def connect(name, validate_certs=True, timeout=None):
    """A session with one vCenter (name or name:port), the credential's account. Raises
    RuntimeError with the message the jobs show."""
    host, port = host_port(name)
    kw = {"connectionPoolTimeout": timeout} if timeout else {}
    try:
        return SmartConnect(host=host, user=os.environ.get("VMWARE_USER", ""), pwd=os.environ.get("VMWARE_PASSWORD", ""),
                            port=port, disableSslCertValidation=not validate_certs, **kw)
    except vim.fault.InvalidLogin:
        raise RuntimeError(refused(name))
    except vim.fault.NoPermission as e:
        raise RuntimeError(no_rights(name, e))
    except Exception as e:                      # noqa: BLE001
        raise RuntimeError("Could not connect to vCenter %s: %s" % (name, e))


def disconnect(si):
    try:
        Disconnect(si)
    except Exception:                           # noqa: BLE001
        pass


def each_vcenter(module, read, extra=None, validate_certs=True, what="vCenter", timeout=None,
                 error_format="Reading {what} from vCenter {name} failed: {error}"):
    """read(si, content, name) for every vCenter -> ([(name, what read returned)], statuses).

    statuses: one per vCenter, in order: {name, ok, error, note}. ok = read; error = could not log
    in, connect or read (the others are still read); note = read but nothing from it (Skip), or the
    same vCenter as an earlier name. When not one vCenter could be read, the module fails with the
    reasons (the single-vCenter messages of before, word for word)."""
    names = vcenter_list(extra)
    if not names:
        module.fail_json(msg=NO_VCENTER)
    done, statuses, uuids = [], [], {}
    for name in names:
        st = {"name": name, "ok": False, "error": "", "note": ""}
        statuses.append(st)
        host, port = host_port(name)
        kw = {"connectionPoolTimeout": timeout} if timeout else {}
        try:
            si = SmartConnect(host=host, user=os.environ.get("VMWARE_USER", ""), pwd=os.environ.get("VMWARE_PASSWORD", ""),
                              port=port, disableSslCertValidation=not validate_certs, **kw)
        except vim.fault.InvalidLogin:
            st["error"] = refused(name)
            continue
        except vim.fault.NoPermission as e:
            st["error"] = no_rights(name, e)
            continue
        except Exception as e:                  # noqa: BLE001
            st["error"] = "Could not connect to vCenter %s: %s" % (name, e)
            continue
        try:
            content = si.RetrieveContent()
            uuid = getattr(content.about, "instanceUuid", None)
            if uuid and uuid in uuids:
                st.update(ok=True, note="the same vCenter as %s: read once" % uuids[uuid])
                continue
            if uuid:
                uuids[uuid] = name
            done.append((name, read(si, content, name)))
            st["ok"] = True
        except Skip as e:
            st.update(ok=True, note=str(e))
        except Exception as e:                  # noqa: BLE001
            st["error"] = error_format.format(what=what, name=name, error=e)
        finally:
            try:
                Disconnect(si)
            except Exception:                   # noqa: BLE001
                pass
    if not done:
        notes = [s["note"] for s in statuses if s["note"] and not s["note"].startswith("the same vCenter")]
        module.fail_json(msg="; ".join([s["error"] for s in statuses if s["error"]] + notes) or NO_VCENTER, vcenters=statuses)
    return done, statuses
