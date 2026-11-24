# -*- coding: utf-8 -*-
"""NetApp - expand a volume (1.123.0): the arithmetic and the rules, with no network - what to grow, by how much, and why
not. Shared by roles/ontap_report/library/site_ontap_volume.py (ONTAP) and roles/vmware_vm/library/site_vmware_netapp_ds.py
(vCenter); tests/test_ontap_expand.py tests it.

Sizes are bytes; GB and TB are what ONTAP's own tools show (1 GB = 1024^3 bytes)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import fnmatch
import math
import re

GB = 1024 ** 3
TB = 1024 ** 4
MB = 1024 ** 2
NETAPP_NAA = "naa.600a0980"          # NetApp's NAA prefix: + the LUN's serial number (12 characters) as hex
SLACK = MB                           # sizes within 1 MB are the same size (ONTAP rounds to its blocks)
KEY = re.compile(r"\s*\[vol\|([^|\]]+)\|([^|\]]+)\|([^|\]]+)\|([^|\]]*)\|(\d+)(?:\|(\d*))?(?:\|([A-Za-z0-9-]*))?(?:\|([A-Za-z0-9-]*))?\]\s*$")
MIN_TYPED = GB // 10                 # the Size box: at least 0.1 GB (less is within ONTAP's rounding of a size)


def human(n, floor=False):
    """bytes -> '10.0 GB' / '2.00 TB' (1024-based, as ONTAP System Manager shows them); floor: never more than n (a size
    to type that must fit)"""
    n = float(n or 0)
    if floor:
        return ("%.2f TB" % (math.floor(n / TB * 100) / 100)) if n >= TB else ("%.1f GB" % (math.floor(n / GB * 10) / 10))
    return ("%.2f TB" % (n / TB)) if n >= TB else ("%.1f GB" % (n / GB))


def pct(part, whole):
    return round(100.0 * float(part) / float(whole), 1) if whole else 0.0


def parse_size(text):
    """The launch's Size box -> bytes. '150GB', '150 gb', '1.5TB', '1 TiB', '200' (GB). Raises ValueError with what to type."""
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(g|gb|gib|t|tb|tib)?\s*$", str(text or ""), re.I)
    if not m:
        raise ValueError("the size '%s' is not understood - type a number and GB or TB, e.g. 150GB or 1TB" % str(text).strip()[:40])
    n = float(m.group(1)) * (TB if (m.group(2) or "g").lower().startswith("t") else GB)
    if n < MIN_TYPED:
        raise ValueError("the size must be at least 0.1 GB")
    return int(math.ceil(n / MB) * MB)


def default_growth(size, pct_=10, min_gb=1, max_gb=512):
    """pct_ % of the volume, at least min_gb, at most max_gb - whole MB, up"""
    g = float(size) * float(pct_) / 100.0
    g = max(float(min_gb) * GB, min(float(max_gb) * GB, g))
    return int(math.ceil(g / MB) * MB)


def room(aggr_size, aggr_used, max_pct):
    """the most an aggregate can still take and stay at or under max_pct % used - whole MB, down; 0 when none"""
    r = float(aggr_size) * float(max_pct) / 100.0 - float(aggr_used)
    return max(0, int(math.floor(r / MB) * MB))


def naa(serial):
    """A NetApp LUN's serial number -> the NAA id ESXi shows its device by (naa.600a0980 + the serial's characters as hex)."""
    s = str(serial or "")
    return (NETAPP_NAA + s.encode("ascii", "replace").hex()).lower() if s else ""


def serial_of(naa_id):
    """naa.600a0980... -> the NetApp serial number, '' for another vendor's device"""
    n = str(naa_id or "").lower()
    if not n.startswith(NETAPP_NAA):
        return ""
    try:
        return bytes.fromhex(n[len(NETAPP_NAA):]).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return ""


def _match(pattern, svm, vol, clusters=()):
    """pattern: vol, svm:vol or cluster/svm:vol (cluster/ alone = every volume there); * and ?, any case. The cluster part
    matches the cluster's ONTAP name or its ontap_clusters address (clusters: both); not known = no match."""
    p = str(pattern or "").strip()
    if not p:
        return False
    if "/" in p:
        pc, p = p.rsplit("/", 1)          # the last /: an address may have its own (https://...)
        if not any(fnmatch.fnmatchcase(str(c or "").lower(), pc.strip().lower()) for c in clusters or ()):
            return False
        p = p.strip() or "*"
    if ":" in p:
        ps, pv = p.split(":", 1)
        return fnmatch.fnmatchcase(svm.lower(), ps.strip().lower()) and fnmatch.fnmatchcase(vol.lower(), (pv.strip() or "*").lower())
    return fnmatch.fnmatchcase(vol.lower(), p.lower())


def excluded(vol, patterns=(), clusters=()):
    """Why a volume may never be grown by this job ('' = it may). vol: an ONTAP volume record (name, svm.name, state, type,
    style, is_svm_root). Always: a node's root volume (vol0) or an SVM's root, a DP (SnapMirror destination) or
    load-sharing volume, one not online, a FlexGroup (a later version). Then ontap_expand_exclude: name patterns,
    svm:volume or cluster/svm:volume (* and ? are wildcards, any case; clusters: the volume's cluster name and address)."""
    name = str(vol.get("name") or "")
    svm = str((vol.get("svm") or {}).get("name") or "")
    if vol.get("is_svm_root") or name.endswith("_root") and name == svm + "_root":
        return "SVM root volume - always"
    if name == "vol0" or not svm:
        return "root volume of a node - always"
    t = str(vol.get("type") or "rw").lower()
    if t == "dp":
        return "DP volume (a SnapMirror destination) - always"
    if t != "rw":
        return "%s volume (not read-write) - always" % t.upper()
    st = str(vol.get("state") or "").lower()
    if st != "online":
        return "%s - always" % (st or "state unknown")
    if str(vol.get("style") or "flexvol").lower() == "flexgroup" or len(vol.get("aggregates") or []) > 1:
        return "FlexGroup - not yet"
    for p in patterns or ():
        if _match(p, svm, name, clusters):
            return "ontap_expand_exclude: %s" % str(p).strip()
    return ""


def choice_text(e, show_cluster=False):
    """One choice of the survey's Volume list. e: {cluster (its name), address (as in ontap_clusters), svm, volume, lun (its
    path, or ''), luns (how many the volume has), size, used, aggr, aggr_free, datastore ('' or 'DS01'), ds_kind (VMFS/NFS)}.
    The key at the end ([vol|address|svm|volume|lun|bytes|lun bytes|volume uuid|lun uuid]) is what the job reads back: the sizes
    are exact (the text is rounded), the UUIDs tell the listed volume and LUN from others made under their names since."""
    name = e["volume"] + (("/" + e["lun"].rsplit("/", 1)[-1]) if e.get("lun") and int(e.get("luns") or 1) > 1 else "")
    # (user) a VMware datastore is said first, by its name: "VMware datastore DS01, LUN" / "..., NFS"
    what = ""
    if e.get("lun"):
        what = "LUN" + ((" %s of %d" % (e["lun"].rsplit("/", 1)[-1], e["luns"])) if int(e.get("luns") or 1) > 1 else "")
        what = ("VMware datastore %s, %s" % (e["datastore"], what)) if e.get("datastore") else what
    elif e.get("datastore"):
        what = "VMware datastore %s, NFS" % e["datastore"]
    # 1.123.1 (user): short - the name, its SVM, the size and how much is free; the cluster and aggregate are in the job's
    # plan and the refresh report, the key stays last (the portal hides it)
    # Codex 1.123.1: two clusters with the same SVM and volume names - the caller asks for the cluster (show_cluster)
    # (user) a LUN volume is judged by its LUN: the size and the free space shown are the LUN's (or its datastore's, lun_fullness)
    # - display only: the key (sizes the expand job plans with) is unchanged
    fsize, fused, _src = lun_fullness(e)
    return "%s (%s) - %s%s, %d%% free%s [vol|%s|%s|%s|%s|%d|%s|%s|%s]" % (
        name, e["svm"] + ((", " + e["cluster"]) if show_cluster else ""), "LUN " if e.get("lun") else "", human(fsize),
        max(0, 100 - int(round(pct(fused, fsize)))),
        (" [%s]" % what) if what else "", e["address"], e["svm"], e["volume"], e.get("lun") or "", int(e["size"]),
        int(e.get("lun_size") or 0) if e.get("lun") else "", e.get("uuid") or "", e.get("lun_uuid") or "")


def choice_shown(text):
    """The part of a choice a person reads: the text before its key."""
    m = KEY.search(str(text or ""))
    return str(text or "")[:m.start()] if m else str(text or "")


def parse_choice(text):
    """A choice (or anything typed) -> {address, svm, volume, lun, listed, lun_listed} or None when it carries no key"""
    m = KEY.search(str(text or ""))
    if not m:
        return None
    return {"address": m.group(1), "svm": m.group(2), "volume": m.group(3), "lun": m.group(4), "listed": int(m.group(5)),
            "lun_listed": int(m.group(6) or 0), "uuid": m.group(7) or "", "lun_uuid": m.group(8) or ""}


def comment_with(old, line, limit=1023):
    """The volume's comment with line added after what is there (kept); over ONTAP's limit, the oldest text goes first"""
    old = str(old or "").strip()
    new = (old + " | " + line) if old else line
    return new if len(new) <= limit else new[len(new) - limit:]


def plan(vol, aggr, listed, opts, lun=None, size_text="", lun_listed=0):
    """What a run does to one volume (and its LUN). vol: {name, svm, size, used, state, type, style, is_svm_root}
    (svm as a name); aggr: {name, size, used} (used None: not read); listed: the size the list showed (bytes); lun: {name,
    size} or None, lun_listed: its size in the list (0: not known);
    opts: pct, min_gb, max_gb, max_pct, exclude, lun_mode.
    -> {status: grow | nothing | refused, why, growth, by ('default' or 'size'), target, ...the numbers before and after}"""
    o = dict({"pct": 10, "min_gb": 1, "max_gb": 512, "max_pct": 97, "exclude": [], "lun_mode": "same"}, **(opts or {}))
    rec = {"name": vol["name"], "svm": {"name": vol["svm"]}, "state": vol.get("state"), "type": vol.get("type"),
           "style": vol.get("style"), "is_svm_root": vol.get("is_svm_root"), "aggregates": vol.get("aggregates") or [aggr.get("name")]}
    if not vol.get("size") or not listed:
        return {"status": "refused", "why": "the size of volume %s could not be read - nothing is changed without it" % vol.get("name"),
                "volume": vol.get("name"), "svm": vol.get("svm"), "size": 0, "used": 0, "growth": 0, "target": 0, "lun": None,
                "aggr": aggr.get("name"), "max_pct": float(o["max_pct"]), "listed": int(listed or 0)}
    size, used = int(vol["size"]), int(vol.get("used") or 0)
    p = {"status": "refused", "why": "", "volume": vol["name"], "svm": vol["svm"], "size": size, "used": used,
         "used_pct": pct(used, size), "aggr": aggr.get("name"), "aggr_size": int(aggr.get("size") or 0),
         "aggr_used": int(aggr.get("used") or 0), "max_pct": float(o["max_pct"]), "listed": int(listed),
         "growth": 0, "by": "size" if str(size_text or "").strip() else "default", "target": size, "lun": None}
    if aggr.get("used") is None or not p["aggr_size"]:
        p["why"] = "the space of aggregate %s could not be read - nothing is changed without it" % (aggr.get("name") or "?")
        return p
    p["aggr_pct"] = pct(p["aggr_used"], p["aggr_size"])
    p["room"] = room(p["aggr_size"], p["aggr_used"], o["max_pct"])
    why = excluded(rec, o["exclude"], o.get("clusters") or ())
    if why:
        p["why"] = "excluded: " + why
        return p
    try:
        g = parse_size(size_text) if p["by"] == "size" else default_growth(int(listed), o["pct"], o["min_gb"], o["max_gb"])
    except ValueError as e:
        p["why"] = str(e)
        return p
    p["growth"] = g
    p["target"] = want = int(listed) + g
    if lun is not None and not lun_listed:
        p["why"] = "the list did not have the size of LUN %s - run NetApp - refresh volume list and pick it again" % lun["name"]
        return p
    lun_done = lun is None or int(lun["size"]) >= int(lun_listed) + g - SLACK
    p["default_text"] = "%d %% of %s, at least %s GB, at most %s GB" % (int(o["pct"]), human(listed), o["min_gb"], o["max_gb"])
    if size >= p["target"] - SLACK:
        if not lun_done:
            p["why"] = ("the volume is already %s, but its LUN is %s, not %s: grow the LUN %s in ONTAP System Manager, then its "
                        "datastore" % (human(size), human(lun["size"]), human(int(lun_listed) + g), lun["name"]))
            return p
        p.update(status="nothing", why="already %s (the list's %s + %s)%s - nothing to do" % (
            human(size), human(listed), human(g), " and its LUN too" if lun is not None else ""), growth=0, target=size)
        # exactly this choice's sizes: the datastore's step may run again (a rescan or expand that failed before) - never after
        # someone else grew them more
        p["retry_ok"] = abs(size - want) <= SLACK and (lun is None or abs(int(lun["size"]) - (int(lun_listed) + g)) <= SLACK)
        if lun is not None and p["retry_ok"]:
            p["lun"] = {"name": lun["name"], "size": int(lun["size"]), "target": int(lun["size"])}
        return p
    if abs(size - int(listed)) > SLACK:
        p["why"] = ("the volume changed since the list was made (listed %s, now %s) - run NetApp - refresh volume list and pick it again"
                    % (human(listed), human(size)))
        return p
    p["target"] = size + g                          # the live size (within 1 MB of the list's) + what the aggregate was checked for
    if lun is not None:
        if not lun.get("size"):
            p["why"] = "the size of LUN %s could not be read - nothing is changed without it" % lun["name"]
            return p
        if lun_listed and abs(int(lun["size"]) - int(lun_listed)) > SLACK:
            p["why"] = ("the LUN changed since the list was made (listed %s, now %s) - run NetApp - refresh volume list and pick it again"
                        % (human(lun_listed), human(lun["size"])))
            return p
        p["lun"] = {"name": lun["name"], "size": int(lun["size"]), "target": int(lun["size"]) + g}
        if p["lun"]["target"] > p["target"]:
            p["why"] = "the LUN would be bigger than its volume (%s > %s)" % (human(p["lun"]["target"]), human(p["target"]))
            return p
    p["aggr_pct_after"] = pct(p["aggr_used"] + g, p["aggr_size"])
    p["used_pct_after"] = pct(used, p["target"])
    if g > p["room"]:
        what = ("Default %s" % human(g)) if p["by"] == "default" else human(g)
        p["why"] = ("%s would put %s at %.1f %%; %s" % (what, p["aggr"], p["aggr_pct_after"],
                    ("up to %s fits" % human(p["room"], floor=True)) if p["room"] >= MIN_TYPED else "nothing fits (it is at %.1f %% now, the limit is %g %%)"
                    % (p["aggr_pct"], o["max_pct"])))
        return p
    p.update(status="grow", why="")
    return p


def lun_fullness(e):
    """How full a choice is, as the person sees it -> (size, used, source). A LUN volume is always about as full as its LUN
    takes (a space-reserved LUN all of it), so for a LUN it is the LUN: the VMware datastore's own used space when vCenter
    was read (capacity and free space), else the LUN's blocks written in ONTAP (space.used - it drops only after UNMAP).
    A volume without a LUN (NFS): the volume's."""
    if e.get("lun"):
        cap, free = e.get("ds_capacity"), e.get("ds_free")
        if cap and free is not None and int(cap) > 0:
            return int(cap), max(0, int(cap) - int(free)), "datastore"
        size = int(e.get("lun_size") or 0)
        if size:
            return size, int(e.get("lun_used") or 0), "LUN"
    return int(e.get("size") or 0), int(e.get("used") or 0), "volume"


def fullness_text(e):
    """'datastore 82% used' / 'LUN 82% written' / 'volume 82% used'"""
    size, used, src = lun_fullness(e)
    return "%s %d%% %s" % (src, int(round(pct(used, size))), "written" if src == "LUN" else "used")


def recheck(p, vol, aggr, exclude=(), clusters=()):
    """Right before the change: '' when the volume and its aggregate still are as planned, else why it stops. vol: the volume
    read again ({name, svm, size, state, type, style, is_svm_root, aggregates}), aggr: its aggregate now."""
    rec = {"name": vol.get("name", p["volume"]), "svm": {"name": vol.get("svm", p["svm"])}, "state": vol.get("state", "online"),
           "type": vol.get("type"), "style": vol.get("style"), "is_svm_root": vol.get("is_svm_root"), "aggregates": vol.get("aggregates") or [p["aggr"]]}
    why = excluded(rec, exclude, clusters)
    if why:
        return "the volume is now excluded (%s) - nothing was changed" % why
    if (vol.get("aggregates") or [p["aggr"]])[0] != p["aggr"]:
        return "the volume moved to aggregate %s since the plan - nothing was changed" % vol["aggregates"][0]
    if aggr.get("used") is None or not aggr.get("size"):
        return "the space of aggregate %s could not be read again - nothing was changed" % p["aggr"]
    if abs(int(vol["size"]) - int(p["size"])) > SLACK:
        return "the volume changed between the plan and the change (%s, now %s) - nothing was changed" % (human(p["size"]), human(vol["size"]))
    left = room(int(aggr.get("size") or 0), int(aggr.get("used") or 0), p["max_pct"])
    if p["growth"] > left:
        return ("%s got fuller between the plan and the change (%.1f %% now): %s no longer fits under %g %% - nothing was changed"
                % (p["aggr"], pct(aggr.get("used"), aggr.get("size")), human(p["growth"]), p["max_pct"]))
    return ""
