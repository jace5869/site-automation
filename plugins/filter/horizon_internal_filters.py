# -*- coding: utf-8 -*-
"""Filters for the Horizon internal VMs report (playbooks/horizon_internal_vms_report.yml): what kind each
internal VM of Horizon's instant clones is (cp-template, cp-replica, cp-parent), its golden image VM and
snapshot, the Horizon pool, farm or desktops that still use it, and the verdict - in use, probably left over
(with the reason), inaccessible or orphaned, or not decided (and why). Standard library only.

Where the rules come from:
- The names cp-template-, cp-replica-, cp-parent- and the settings cloneprep.master.metadata, the template's and
  the replica's UUID (TEMPLATE_REF, REPLICA_REF) and cloneprep.client.uuid: Omnissa's euc-samples,
  Horizon-Samples/Get Horizon Instant Clone Pool Information/get-horizon-ic-pool-info.ps1. That script reads the
  golden VM's instance UUID and the snapshot's ID from cloneprep.master.metadata by position (split on '%',
  fields 9 and 11 without their '3D'): the value is URL-encoded key=value pairs. hzi_master reads it that way
  first, then by key names, then the first UUID in it.
- The ClonePrep folders: Horizon's documentation (the four folders made when an image is published).
- INFERRED, not documented: that Horizon's parent_vm_id and base_snapshot_id of a pool are vCenter's own IDs
  (vm-123, snapshot-45). They are matched as such, and also by the name and path Horizon gives for them
  (external/base-vms, base-snapshots); a pool whose golden VM is found neither way stops every "left over"
  in its vCenter (not decided). The pending image's IDs (provisioning_status_data.instant_clone_pending_image_
  parent_vm_id / _snapshot_id), a farm's (automated_farm_settings.provisioning_settings) and a desktop's
  (managed_machine_data.base_vm_id / base_vm_snapshot_id) are read under the names the REST API is reported
  to use; each is looked for under several names, and a missing one is never an error."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

try:
    from urllib.parse import unquote
except ImportError:                             # Python 2
    from urllib import unquote

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_ANY = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
NAME_KINDS = (("cp-template-", "template"), ("cp-replica-", "replica"), ("cp-parent-", "parent"))
# ClonePrepInternalTemplateFolder, ClonePrepReplicaVmFolder, ClonePrepParentVmFolder, ClonePrepResyncVmFolder
FOLDER_KINDS = (("cloneprepinternaltemplate", "template"), ("clonepreptemplate", "template"), ("cloneprepreplica", "replica"),
                ("cloneprepparent", "parent"), ("cloneprepresync", "resync"))
INSTANT = ("INSTANT_CLONE", "INSTANT_CLONE_ENGINE")
MASTER = "cloneprep.master.metadata"
TEMPLATE_REF = "cloneprep.internal.template.uuid"   # public-ok: a setting, not a host
REPLICA_REF = "cloneprep.replica.uuid"
POD = "cloneprep.client.uuid"
CLEANUP = ("To clean up, on a Connection Server as an administrator (C:\\Program Files\\Omnissa\\Horizon\\Server\\tools\\bin; "
           "older releases: C:\\Program Files\\VMware\\VMware View\\Server\\tools\\bin): iccleanup.cmd -vc <vcenter> -uid <user>, "
           "then list (the groups of internal VMs, by index), unprotect -I <index> and delete -I <index>. Check each group "
           "against this list first; a pool must not be provisioning while you do it.")


def _g(d, *paths, **kw):
    """The first value found under any of the dotted paths (None and '' count as not there)."""
    for p in paths:
        cur, ok = d, True
        for k in p.split("."):
            if isinstance(cur, dict) and cur.get(k) not in (None, ""):
                cur = cur[k]
            else:
                ok = False
                break
        if ok:
            return cur
    return kw.get("default")


def _s(v):
    return str(v if v is not None else "").strip()


def _decode(text):
    t = _s(text)
    for _ in range(3):
        u = unquote(t)
        if u == t:
            break
        t = u
    return t


def hzi_master(value):
    """cloneprep.master.metadata -> {uuid, snapshot, how}: the golden image VM's instance UUID (lower case) and
    the snapshot's ID ('' when not found). how: position (as Omnissa's sample reads it), keys, or any UUID."""
    raw = _s(value)
    out = {"uuid": "", "snapshot": "", "how": ""}
    if not raw:
        return out
    parts = raw.split("%")
    if len(parts) > 11:
        a, b = parts[9], parts[11]
        a = a[2:] if a[:2].upper() == "3D" else a
        b = b[2:] if b[:2].upper() == "3D" else b
        if UUID_RE.match(a.strip()):
            out.update(uuid=a.strip().lower(), snapshot=b.strip(), how="position")
            return out
    text = _decode(raw)
    pairs = []
    for f in re.split(r"[,;&|\n]+", text):
        m = re.match(r"\s*([^=:]+?)\s*[=:]\s*(.*?)\s*$", f)
        if m:
            pairs.append((m.group(1).strip().lower(), m.group(2).strip().strip('"\'')))
        elif f.strip():
            pairs.append(("", f.strip()))
    for k, v in pairs:
        if not out["uuid"] and UUID_RE.match(v) and re.search(r"master|golden|parent|base|vm", k) \
                and not re.search(r"template|replica|client|pod", k):
            out["uuid"] = v.lower()
        if not out["snapshot"] and re.search(r"snap", k) and v:
            out["snapshot"] = v
    if out["uuid"]:
        out["how"] = "keys"
        return out
    vals = [v for _, v in pairs]
    if len(vals) > 5 and UUID_RE.match(vals[4]):
        out.update(uuid=vals[4].lower(), snapshot=out["snapshot"] or vals[5], how="position")
        return out
    m = UUID_ANY.search(text)
    if m:
        out.update(uuid=m.group(0).lower(), how="any UUID")
    return out


def hzi_kind(vm, others=()):
    """template, replica, parent, resync or internal: by its name, its folder, or another internal VM naming it."""
    n = _s(vm.get("name")).lower()
    for pre, kind in NAME_KINDS:
        if n.startswith(pre):
            return kind
    f = re.sub(r"[^a-z]", "", _s(vm.get("folder_name")).lower())
    for pre, kind in FOLDER_KINDS:
        if f.startswith(pre):
            return kind
    u = _s(vm.get("uuid")).lower()
    if u:
        for o in others or []:
            cp = o.get("cloneprep") or {}
            if o.get("vcenter") == vm.get("vcenter"):
                if u in _s(cp.get(TEMPLATE_REF)).lower():
                    return "template"
                if u in _s(cp.get(REPLICA_REF)).lower():
                    return "replica"
    return "internal"


def _host_key(name):
    """'https://VC01.example.mil:443/' -> ('vc01.example.mil', 'vc01')."""
    n = _s(name).lower()
    for pre in ("https://", "http://"):
        if n.startswith(pre):
            n = n[len(pre):]
    n = n.split("/")[0]
    if n.count(":") == 1:
        n = n.split(":")[0]
    short = n if re.match(r"^\d+(\.\d+){3}$", n) else n.split(".")[0]
    return n, short


def _vc_map(hz_raw, read_names):
    """Horizon's vCenter ID -> the vCenter of this job it is ('' when it matches none, or more than one)."""
    out = {}
    recs = list(hz_raw.get("vcenters") or []) + list(hz_raw.get("virtual_centers") or [])
    for r in recs:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        name = _s(_g(r, "server_name", "name", "details.name", default=""))
        if not name or str(r["id"]) in out and out[str(r["id"])]:
            continue
        full, short = _host_key(name)
        hits = [x for x in read_names if _host_key(x)[0] == full] or [x for x in read_names if _host_key(x)[1] == short]
        out[str(r["id"])] = hits[0] if len(hits) == 1 else ""
    return out


def _sources(hz, vc_of):
    """What Horizon says uses an image: [{what, pool, vcenter, vm_id, snap_id}] and the reasons a "left over"
    cannot be decided: [(vcenter or '' for all, reason)]."""
    raw = hz.get("raw") or {}
    errors = hz.get("errors") or {}
    src, gaps = [], []
    for part, what in (("desktop_pools", "the desktop pools"), ("farms", "the farms"), ("machines", "the desktops")):
        if part in errors:
            gaps.append(("", "Horizon did not give %s (%s)" % (what, errors[part])))
    pools = {}
    for p in raw.get("desktop_pools") or []:
        if not isinstance(p, dict):
            continue
        pid, name = _s(p.get("id")), _s(_g(p, "name", "display_name", default=p.get("id")))
        pools[pid] = p
        if _s(p.get("source")).upper() not in INSTANT:
            continue
        vc = vc_of(_s(_g(p, "vcenter_id", "virtual_center_id", default="")))
        vm_id = _s(_g(p, "provisioning_settings.parent_vm_id", "parent_vm_id", default=""))
        snap = _s(_g(p, "provisioning_settings.base_snapshot_id", "base_snapshot_id", default=""))
        if not vm_id or not snap:
            gaps.append((vc, "pool %s: Horizon did not give its golden image VM and snapshot" % name))
        else:
            src.append({"what": "pool %s" % name, "pool": name, "vcenter_id": _s(_g(p, "vcenter_id", "virtual_center_id", default="")),
                        "vcenter": vc, "vm_id": vm_id, "snap_id": snap})
        pvm = _s(_g(p, "provisioning_status_data.instant_clone_pending_image_parent_vm_id", "instant_clone_pending_image_parent_vm_id",
                    default=""))
        psn = _s(_g(p, "provisioning_status_data.instant_clone_pending_image_snapshot_id", "instant_clone_pending_image_snapshot_id",
                    default=""))
        if psn:
            src.append({"what": "pool %s (its pending image)" % name, "pool": name, "vcenter_id": _s(p.get("vcenter_id")), "vcenter": vc,
                        "vm_id": pvm, "snap_id": psn})
    for f in raw.get("farms") or []:
        if not isinstance(f, dict):
            continue
        src_kind = _s(_g(f, "source", "automated_farm_settings.source", default="")).upper()
        if src_kind not in INSTANT and _s(f.get("type")).upper() != "AUTOMATED":
            continue
        name = _s(_g(f, "name", "display_name", default=f.get("id")))
        vcid = _s(_g(f, "automated_farm_settings.vcenter_id", "vcenter_id", default=""))
        vc = vc_of(vcid)
        vm_id = _s(_g(f, "automated_farm_settings.provisioning_settings.parent_vm_id", "provisioning_settings.parent_vm_id",
                      "parent_vm_id", default=""))
        snap = _s(_g(f, "automated_farm_settings.provisioning_settings.base_snapshot_id", "provisioning_settings.base_snapshot_id",
                     "base_snapshot_id", default=""))
        if not vm_id or not snap:
            gaps.append((vc, "farm %s: Horizon did not give its golden image VM and snapshot" % name))
        else:
            src.append({"what": "farm %s" % name, "pool": name, "vcenter_id": vcid, "vcenter": vc, "vm_id": vm_id, "snap_id": snap})
    # desktops still on an older image (a push that waits for the users to log off): their replica is still needed
    seen = {}
    for m in raw.get("machines") or []:
        if not isinstance(m, dict):
            continue
        snap = _s(_g(m, "managed_machine_data.base_vm_snapshot_id", "base_vm_snapshot_id", default=""))
        if not snap:
            continue
        p = pools.get(_s(_g(m, "desktop_pool_id", "pool_id", default=""))) or {}
        name = _s(_g(p, "name", "display_name", default="?"))
        vcid = _s(_g(p, "vcenter_id", "virtual_center_id", default=""))
        vm_id = _s(_g(m, "managed_machine_data.base_vm_id", "base_vm_id", default=""))
        key = (name, vcid, vm_id, snap)
        seen[key] = seen.get(key, 0) + 1
    for (name, vcid, vm_id, snap), n in sorted(seen.items()):
        src.append({"what": "%d desktop(s) of pool %s" % (n, name), "pool": name, "vcenter_id": vcid, "vcenter": vc_of(vcid),
                    "vm_id": vm_id, "snap_id": snap, "desktops": n})
    return src, gaps


def _resolve(s, golden, raw):
    """The golden image VM (a record of the module's golden list) Horizon means by s, and how it was found:
    by vCenter's ID, or by the name and path Horizon gives for that ID."""
    if not s["vm_id"]:
        return None, ""
    here = [g for g in golden if not s["vcenter"] or g["vcenter"] == s["vcenter"]]
    by_id = [g for g in here if g["moid"] == s["vm_id"]]
    if len(by_id) == 1:
        return by_id[0], "ID"
    rec = [r for r in raw.get("base_vms:" + s["vcenter_id"]) or [] if isinstance(r, dict) and _s(r.get("id")) == s["vm_id"]]
    if rec:
        path, name = _s(rec[0].get("path")), _s(rec[0].get("name")).lower()
        hit = [g for g in here if path and g["path"] == path] or [g for g in here if name and g["name"].lower() == name]
        if len(hit) == 1:
            return hit[0], "name"
    return None, ""


def _snap_match(s, g, gs, master_snap, raw):
    """Does source s point at snapshot gs of golden VM g? By vCenter's ID (snapshot-45), the snapshot's own number, or the
    name and path Horizon gives for its ID."""
    sid = s["snap_id"]
    if gs is not None:
        if sid == gs["moid"]:
            return "ID"
        if s["vm_id"] and sid == str(gs["id"]):
            return "ID"
        for key in ("base_snapshots:%s:%s" % (s["vcenter_id"], s["vm_id"]),):
            rec = [r for r in raw.get(key) or [] if isinstance(r, dict) and _s(r.get("id")) == sid]
            if rec and ((_s(rec[0].get("path")) and _s(rec[0].get("path")) == gs["path"]) or
                        (not _s(rec[0].get("path")) and _s(rec[0].get("name")) == gs["name"])):
                return "name"
        return ""
    return "ID" if s["vm_id"] and master_snap and sid == master_snap else ""


def hzi_golden_ids(hz):
    """The VM IDs Horizon gives as golden image VMs (pools, pending images, farms, desktops): the module reads them."""
    src, _ = _sources(hz or {}, lambda x: "")
    return sorted(set(s["vm_id"] for s in src if s["vm_id"]))


def hzi_base_reads(hz):
    """The extra Horizon reads that name Horizon's golden VM and snapshot IDs (site_horizon resources): base-vms per
    vCenter, base-snapshots per golden VM - for matching by name when the IDs are not vCenter's."""
    src, _ = _sources(hz or {}, lambda x: "")
    out, seen = [], set()
    for s in src:
        if not s["vcenter_id"]:
            continue
        k = "base_vms:" + s["vcenter_id"]
        if k not in seen:
            seen.add(k)
            out.append({"name": "base_vms", "key": k, "query": {"vcenter_id": s["vcenter_id"]}})
        if s["vm_id"]:
            k = "base_snapshots:%s:%s" % (s["vcenter_id"], s["vm_id"])
            if k not in seen:
                seen.add(k)
                out.append({"name": "base_snapshots", "key": k, "query": {"vcenter_id": s["vcenter_id"], "base_vm_id": s["vm_id"]}})
    return out


def _gb(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def hzint_report(vc, hz=None, opts=None):
    """The report -> {report, vms, critical, warning, undecided, left_over_gb}.

    vc:   site_vmware_horizon_internal's result: {internal, golden, vcenters}
    hz:   {ok, error, data}: data = hz_data of Horizon's pools, farms, desktops (and base_vms/base_snapshots reads)
    opts: min_age_hours, other_pods, max_rows"""
    o = opts or {}
    hz = hz or {}
    min_age = float(o.get("min_age_hours") if o.get("min_age_hours") not in (None, "") else 24)
    other = [_s(x).lower() for x in o.get("other_pods") or [] if _s(x)]
    max_rows = int(o.get("max_rows") or 100)
    vms = [v for v in (vc or {}).get("internal") or [] if isinstance(v, dict)]
    golden = [g for g in (vc or {}).get("golden") or [] if isinstance(g, dict)]
    read = [s.get("name") for s in (vc or {}).get("vcenters") or [] if s.get("ok") and not _s(s.get("note")).startswith("the same vCenter")]
    hz_ok = str(hz.get("ok")).strip().lower() in ("true", "1", "yes")   # a bool, or its text (ansible-core 2.16)
    data = hz.get("data") or {}
    raw = data.get("raw") or {}
    vmap = _vc_map(raw, read) if hz_ok else {}
    src, gaps = _sources(data, lambda i: vmap.get(i, "")) if hz_ok else ([], [])
    for s in src:
        s["golden"], s["found"] = _resolve(s, golden, raw)
        if s["vm_id"] and s["golden"] is None:
            gaps.append((s["vcenter"], "%s: its golden image VM (Horizon's ID %s) is not a VM found in the vCenters read" % (s["what"], s["vm_id"])))

    # each internal VM: its kind, its golden image VM and snapshot
    by_uuid = dict(((v["vcenter"], _s(v.get("uuid")).lower()), v) for v in vms if v.get("uuid"))
    for v in vms:
        cp = v.get("cloneprep") or {}
        v["kind"] = hzi_kind(v, vms)
        v["master"] = hzi_master(cp.get(MASTER))
        v["master"]["from"] = "itself" if v["master"]["uuid"] else ""
    for v in vms:                               # one without it: its replica's or template's, or one that names it
        if v["master"]["uuid"]:
            continue
        cp = v.get("cloneprep") or {}
        refs = [by_uuid.get((v["vcenter"], u.lower())) for k in (REPLICA_REF, TEMPLATE_REF) for u in UUID_ANY.findall(_s(cp.get(k)))]
        refs += [w for w in vms if w is not v and w["vcenter"] == v["vcenter"] and v.get("uuid")
                 and any(v["uuid"] in _s((w.get("cloneprep") or {}).get(k)).lower() for k in (REPLICA_REF, TEMPLATE_REF))]
        for r in refs:
            if r is not None and r["master"]["uuid"] and r["master"]["from"] == "itself":
                v["master"] = dict(r["master"], **{"from": r["name"]})
                break
    for v in vms:
        m = v["master"]
        g = [x for x in golden if x["vcenter"] == v["vcenter"] and m["uuid"] and x["uuid"] == m["uuid"]]
        v["g"] = g[0] if g else None
        gs = None
        if v["g"] is not None and m["snapshot"]:
            hit = [x for x in v["g"].get("snapshots") or [] if str(x.get("id")) == m["snapshot"] or x.get("moid") == m["snapshot"]]
            gs = hit[0] if hit else None
        v["gs"] = gs

    # what uses each one, and the verdict
    rows = {"critical": [], "warning": [], "info": [], "ok": []}
    notes, recs = [], []
    pods_in_use = set()
    for v in vms:
        v["uses"] = []
        if hz_ok and v["g"] is not None:
            for s in src:
                if s["vcenter"] and s["vcenter"] != v["vcenter"]:
                    continue
                if s["golden"] is not None and (s["golden"]["vcenter"], s["golden"]["moid"]) != (v["g"]["vcenter"], v["g"]["moid"]):
                    continue
                if s["golden"] is None and s["vm_id"]:
                    continue
                if s["golden"] is None and (v["gs"] is None or s["snap_id"] != v["gs"]["moid"]):
                    continue                    # a desktop whose golden VM Horizon does not name: by vCenter's snapshot ID only
                how = _snap_match(s, v["g"], v["gs"], v["master"]["snapshot"], raw)
                if how:
                    v["uses"].append((s, "by name" if "name" in (how, s["found"]) else "by ID"))
        if v["uses"]:
            pod = _s((v.get("cloneprep") or {}).get(POD)).lower()
            if pod:
                pods_in_use.add(pod)
    for v in vms:
        cp = v.get("cloneprep") or {}
        pod = _s(cp.get(POD)).lower()
        conn = _s(v.get("connection")).lower() or "?"
        gname = (v["g"]["name"] if v["g"] is not None else
                 ("instance UUID %s (not in vCenter)" % v["master"]["uuid"]) if v["master"]["uuid"] else "")
        sname = (v["gs"]["path"] if v["gs"] is not None else
                 ("ID %s (not on that VM)" % v["master"]["snapshot"]) if v["master"]["snapshot"] and v["g"] is not None else
                 v["master"]["snapshot"])
        users = sorted(set(s["what"] for s, _ in v["uses"]))
        how = sorted(set(h for _, h in v["uses"]))
        status, verdict, why = "ok", "in use", ("in use (matched %s)" % " and ".join(how)) if users else ""
        gap = [r for vcn, r in gaps if not vcn or vcn == v["vcenter"]]
        age = v.get("age_hours")
        if conn != "connected":
            status, verdict = "critical", conn
            why = {"inaccessible": "inaccessible: vCenter cannot reach its files (a datastore gone, or files deleted or damaged)",
                   "orphaned": "orphaned: its host no longer knows it (a host that failed or was rebuilt)",
                   "disconnected": "disconnected: its ESXi host is not connected to vCenter",
                   "invalid": "invalid: vCenter cannot read its configuration"}.get(conn, "connection state %s" % conn)
            why += (" - " + ("used by " + ", ".join(users)) if users else "")
        elif users:
            pass
        elif pod and pod in other:
            status, verdict, why = "info", "another pod", "made by another Horizon pod (cloneprep.client.uuid %s, in horizon_internal_other_pods)" % pod
        elif not hz_ok:
            status, verdict, why = "info", "not decided", "Horizon could not be read: whether a pool uses it is not known"
        elif gap:
            status, verdict, why = "info", "not decided", gap[0] + ("; and %d more reason(s): the notes below" % (len(gap) - 1) if len(gap) > 1 else "")
        elif not v["master"]["uuid"]:
            status, verdict, why = "info", "not decided", "no golden image recorded on it or its replica (cloneprep.master.metadata)"
        elif age is not None and age < min_age:
            status, verdict, why = "info", "not decided", "made %.1f hour(s) ago, less than horizon_internal_min_age_hours (%g)" % (age, min_age)
        else:
            status, verdict = "warning", "probably left over"
            if v["g"] is None:
                why = "its golden image VM (instance UUID %s) is no longer in vCenter" % v["master"]["uuid"]
            elif v["gs"] is None and v["master"]["snapshot"]:
                why = "its snapshot (ID %s) is no longer on golden image VM %s" % (v["master"]["snapshot"], v["g"]["name"])
            else:
                why = "no pool, farm or desktop uses golden image VM %s, snapshot %s" % (v["g"]["name"], sname or "?")
            if age is None:
                why += " (when it was made is not known)"
            if pod and pods_in_use and pod not in pods_in_use:
                why += ("; made by another Horizon pod (cloneprep.client.uuid %s): if another pod shares this vCenter, name it in "
                        "horizon_internal_other_pods" % pod)
        if v["kind"] == "parent" and v.get("host_maintenance"):
            notes.append("%s is on host %s, which is in maintenance mode: a cp-parent VM can keep a host from entering maintenance "
                         "mode (Broadcom KB 322191)" % (v["name"], v.get("host") or "?"))
        rec = {"vcenter": v["vcenter"], "name": v["name"], "moid": v.get("moid", ""), "kind": v["kind"], "host": v.get("host", ""),
               "host_maintenance": bool(v.get("host_maintenance")), "datastores": v.get("datastores") or [],
               "committed_gb": _gb(v.get("committed_gb")), "power": v.get("power", ""), "connection": v.get("connection", ""),
               "created": v.get("created", ""), "golden_vm": gname, "snapshot": sname or "", "golden_from": v["master"]["from"],
               "used_by": users, "matched": how, "pod": pod, "found_by": v.get("found_by") or [], "status": status,
               "verdict": verdict, "why": why}
        recs.append(rec)
        rows[status].append(rec)

    def table(rs):
        rs = sorted(rs, key=lambda r: (r["vcenter"].lower(), r["name"].lower()))
        multi = len(set(r["vcenter"] for r in recs)) > 1
        out = [[r["name"] + (" (vCenter %s)" % r["vcenter"] if multi else ""), r["kind"], r["host"] or "-", ", ".join(r["datastores"]) or "-",
                "%.1f" % r["committed_gb"], r["power"].replace("powered", "").lower() or "?", r["connection"] or "?",
                r["created"][:10] or "?", (r["golden_vm"] or "?") + (" / " + r["snapshot"].lstrip("/") if r["snapshot"] else ""),
                ", ".join(r["used_by"]) or "none", r["why"]] for r in rs[:max_rows]]
        return out, len(rs) - len(out)

    cols = ["VM", "Kind", "ESXi host", "Datastores", "GB on disk", "Power", "Connection", "Created", "Golden image VM / snapshot",
            "Used by", "Verdict"]
    sections = []
    for st, title, text in (
            ("critical", "Inaccessible, orphaned or disconnected",
             "vCenter cannot use these (Broadcom KB 424675 for internal VMs whose files are named by UUIDs). " + CLEANUP),
            ("warning", "Probably left over",
             "No current pool, farm or desktop uses these: the reason is on each. Horizon makes them again when a pool needs "
             "them. " + CLEANUP),
            ("info", "Not decided", "Not called left over: the reason is on each."),
            ("ok", "In use", "A current pool, farm or desktop uses these: leave them.")):
        if not rows[st]:
            continue
        r, more = table(rows[st])
        sec = {"title": title, "status": st, "columns": cols, "rows": r,
               "row_status": [st if st in ("critical", "warning") else ""] * len(r), "text": text}
        if more:
            sec["text"] += " And %d more: the job's artifacts (horizon_internal_vms) have all." % more
        sections.append(sec)
    lines = list(notes)
    if not hz_ok:
        lines.append("Horizon could not be read (%s): the internal VMs are listed from vCenter, but whether a pool still uses them "
                     "is not decided." % (_s(hz.get("error")) or "no answer"))
    else:
        by_name = [r["name"] for r in recs if "by name" in r["matched"]]
        if by_name:
            lines.append("Matched to a pool by the golden image VM's name and path, as Horizon gives them, not by vCenter's ID: %s."
                         % ", ".join(by_name[:10]) + (" and %d more" % (len(by_name) - 10) if len(by_name) > 10 else ""))
        for vcn, r in gaps:
            lines.append("Not decided%s: %s." % (" in vCenter " + vcn if vcn else "", r))
    inherited = [r["name"] for r in recs if r["golden_from"] and r["golden_from"] != "itself"]
    if inherited:
        lines.append("Golden image read from the replica or template that names them: %s." % ", ".join(inherited[:10]))
    if lines:
        sections.append({"title": "Notes", "lines": lines})

    nc, nw, ni = len(rows["critical"]), len(rows["warning"]), len(rows["info"])
    left_gb = round(sum(r["committed_gb"] for r in rows["warning"] + rows["critical"]), 1)
    total_gb = round(sum(r["committed_gb"] for r in recs), 1)
    if nc or nw:
        title = "Horizon internal VMs: %s" % ", ".join(x for x in (
            "%d probably left over (%.1f GB)" % (nw, round(sum(r["committed_gb"] for r in rows["warning"]), 1)) if nw else "",
            "%d inaccessible or orphaned" % nc if nc else "") if x)
    elif not recs:
        title = "Horizon internal VMs: none found"
    elif not hz_ok:
        title = "Horizon internal VMs: %d found, in use not decided (Horizon not read)" % len(recs)
    else:
        title = "Horizon internal VMs: none left over" + (" (%d not decided)" % ni if ni else "")
    report = {"title": title,
              "subtitle": "%d internal VM(s) in %d vCenter(s), %.1f GB on disk" % (len(recs), len(read), total_gb),
              "status": "critical" if nc else ("warning" if nw or not hz_ok else ("info" if ni else "ok")),
              "summary": [{"label": "Internal VMs", "value": len(recs)}, {"label": "In use", "value": len(rows["ok"])},
                          {"label": "Probably left over", "value": nw, "status": "warning" if nw else "ok"},
                          {"label": "Inaccessible or orphaned", "value": nc, "status": "critical" if nc else "ok"},
                          {"label": "Not decided", "value": ni, "status": "info" if ni else "ok"},
                          {"label": "GB to reclaim", "value": left_gb}],
              "sections": sections or [{"title": "Internal VMs", "lines": ["No internal VM of Horizon's instant clones was found "
                                                                          "(cp-template, cp-replica, cp-parent)."]}]}
    return {"report": report, "vms": recs, "critical": nc, "warning": nw, "undecided": ni, "left_over_gb": left_gb}


class FilterModule(object):
    def filters(self):
        return {"hzi_master": hzi_master, "hzi_kind": hzi_kind, "hzi_golden_ids": hzi_golden_ids,
                "hzi_base_reads": hzi_base_reads, "hzint_report": hzint_report}
