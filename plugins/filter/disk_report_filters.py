# -*- coding: utf-8 -*-
"""The disk space report's tables (playbooks/disk_report.yml): disk_report_sections."""


def _disk_bytes(n):
    """Bytes in the units df -h uses (1024s), one decimal: 1.5G, 380M, 0B."""
    n = float(n or 0)
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return ("%d%s" % (n, unit)) if unit == "B" else ("%.1f%s" % (n, unit))
        n /= 1024.0


def _disk_status(pct, warn, crit):
    return "critical" if pct >= crit else ("warning" if pct >= warn else "")


def disk_report_sections(hosts, warn=85, crit=90, show_all=False):
    """The disk space report's tables (playbooks/disk_report.yml) from each host's hostvars: check_disk_inventory
    ({filesystems: [{mount, source, type, size, used, avail, pct, ipct}], lvm: {pools: [...], vgs: [...]}}) and
    disk_tmp_plan (the old files of its tmp folders on a full filesystem, roles/tmp_cleanup scan.yml). The filesystems
    and LVM thin pools at or above warn (all of them with show_all), fullest first, red at crit, amber at warn; the
    volume groups of the hosts with a full filesystem or thin pool (room to grow one); the old tmp files workflow 42
    cleans."""
    warn, crit = int(warn), int(crit)
    fs_rows, fs_rs, fs_cs, lv_rows, lv_rs, lv_cs, vg_rows, tmp_rows = [], [], [], [], [], [], [], []
    for hv in sorted(hosts or [], key=lambda x: str(x.get("inventory_hostname", ""))):
        host = str(hv.get("inventory_hostname", ""))
        inv = hv.get("check_disk_inventory") or {}
        tmp = {t.get("mount"): t for t in (hv.get("disk_tmp_plan") or [])}
        full = False
        for r in inv.get("filesystems") or []:
            pct, ipct = int(r.get("pct", -1)), int(r.get("ipct", -1))
            st = _disk_status(max(pct, ipct), warn, crit)
            if st:
                full = True
            if not st and not show_all:
                continue
            t = tmp.get(r.get("mount"))
            old = "" if not t else "%s in %s (%d files)" % (_disk_bytes(t.get("bytes")), t.get("path"), int(t.get("files", 0)))
            fs_rows.append([host, r.get("mount", ""), r.get("source", ""), r.get("type", ""), "%d%%" % pct,
                            _disk_bytes(r.get("used")), _disk_bytes(r.get("avail")), _disk_bytes(r.get("size")),
                            ("%d%%" % ipct) if ipct >= 0 else "", old, st or "ok"])
            fs_rs.append(st)
            fs_cs.append(["", "", "", "", _disk_status(pct, warn, crit), "", "", "", _disk_status(ipct, warn, crit), "", st])
        lvm = inv.get("lvm") or {}
        for p in lvm.get("pools") or []:
            data, meta = float(p.get("data", -1)), float(p.get("meta", -1))
            st = "critical" if p.get("invalid") else _disk_status(max(data, meta), warn, crit)
            if st:
                full = True
            if not st and not show_all:
                continue
            lv_rows.append([host, "%s/%s" % (p.get("vg"), p.get("lv")), p.get("kind", "") + (" of " + p["origin"] if p.get("origin") else ""),
                            _disk_bytes(p.get("size")), ("%.1f%%" % data) if data >= 0 else "", ("%.1f%%" % meta) if meta >= 0 else "",
                            "no longer valid (filled up)" if p.get("invalid") else (st or "ok")])
            lv_rs.append(st)
            lv_cs.append(["", "", "", "", _disk_status(data, warn, crit), _disk_status(meta, warn, crit), st])
        if full or show_all:
            for v in lvm.get("vgs") or []:
                vg_rows.append([host, v.get("vg", ""), _disk_bytes(v.get("size")), _disk_bytes(v.get("free")), "%s%%" % v.get("free_pct", 0)])
        for t in hv.get("disk_tmp_plan") or []:
            big = "; ".join("%s %s" % (_disk_bytes(b[0]), b[1]) for b in (t.get("big") or [])[:3])
            tmp_rows.append([host, t.get("path", ""), t.get("mount", ""), "%d days" % int(t.get("days", 0)), int(t.get("files", 0)),
                             _disk_bytes(t.get("bytes")), big])
    order = {"critical": 0, "warning": 1, "": 2}

    def by_pct(rows, rs, cs, col):
        z = sorted(zip(rows, rs, cs), key=lambda x: (order[x[1]], -float(str(x[0][col]).rstrip("%") or -1), x[0][0]))
        return [a for a, _b, _c in z], [b for _a, b, _c in z], [c for _a, _b, c in z]

    def status(rs):
        return "critical" if "critical" in rs else ("warning" if "warning" in rs else "")

    out = []
    fs_rows, fs_rs, fs_cs = by_pct(fs_rows, fs_rs, fs_cs, 4)
    sec = {"title": ("Every filesystem" if show_all else "Filesystems at or above %d%%" % warn),
           "columns": ["Host", "Mounted on", "Device", "Type", "Used", "Used space", "Free", "Size", "Inodes used", "Old tmp files on it", "Status"],
           "rows": fs_rows, "row_status": fs_rs, "cell_status": fs_cs,
           "text": ("Red = %d%% or more, amber = %d%% or more (space or inodes). Logical volumes are here by their mount point."
                    % (crit, warn)) if fs_rows else "No filesystem is %d%% full or more." % warn}
    if status(fs_rs):
        sec["status"] = status(fs_rs)
    out.append(sec)
    if lv_rows:
        lv_rows, lv_rs, lv_cs = by_pct(lv_rows, lv_rs, lv_cs, 4)
        sec = {"title": "LVM thin pools and snapshots", "columns": ["Host", "Volume", "Kind", "Size", "Data", "Metadata", "Status"],
               "rows": lv_rows, "row_status": lv_rs, "cell_status": lv_cs,
               "text": "A thin pool whose data or metadata is full stops every volume in it; a snapshot that fills up is lost. "
                       "lvextend the pool (--poolmetadatasize for the metadata), or remove the snapshot."}
        if status(lv_rs):
            sec["status"] = status(lv_rs)
        out.append(sec)
    if vg_rows:
        out.append({"title": "Volume groups" + ("" if show_all else " of those hosts"), "columns": ["Host", "Volume group", "Size", "Free", "Free %"],
                    "rows": vg_rows, "text": "Free space in a volume group can grow a full logical volume and its filesystem in "
                                             "one step: lvextend -r -L +10G /dev/VG/LV."})
    if tmp_rows:
        out.append({"title": "Old files in the tmp folders of a full filesystem", "columns": ["Host", "Folder", "On", "Older than", "Files", "Size", "Biggest"],
                    "rows": tmp_rows, "text": "Workflow 42 (Linux - tmp cleanup) deletes these after its approval: files accessed, changed and "
                                              "modified longer ago than that, never sockets, never systemd's or X's own folders."})
    return out



class FilterModule(object):
    def filters(self):
        return {"disk_report_sections": disk_report_sections}
