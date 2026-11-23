# -*- coding: utf-8 -*-
"""SolarWinds - snapshot (1.96.0, roles/solarwinds_snapshot): the job's lines from what site_swis_snapshot returned."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type


def _n(v):
    return "{:,}".format(int(v or 0))


def _p(n, word, many=None):
    return "%s %s" % (_n(n), word if int(n or 0) == 1 else (many or word + "s"))


def swis_snapshot_lines(r, check=False):
    """The job output: what the snapshot holds, how long the read took, what was not read, what was deleted."""
    r = dict(r or {})
    if r.get("skipped_lock"):
        return ["Another SolarWinds snapshot run is writing: this run kept nothing."]
    c = r.get("counts") or {}
    head = ("Check: SolarWinds read, nothing written." if check else
            "Snapshot %s kept." % r.get("snap_id") if r.get("ok") else "No snapshot kept.")
    if r.get("topology"):
        head += " " + r["topology"]
    out = [head]
    if c:
        what = ["%s (%s down, %s warning)" % (_p(c.get("nodes"), "node"), _n(c.get("nodes_down")), _n(c.get("nodes_warning"))),
                _p(c.get("links"), "link"), _p(c.get("interfaces"), "interface") + " on links"]
        if c.get("alerts") is not None:
            what.append(_p(c["alerts"], "active alert"))
        if c.get("events") is not None:
            what.append(_p(c["events"], "new event"))
        if c.get("apps") is not None:
            what.append("%s (%s down)" % (_p(c["apps"], "SAM application"), _n(c.get("apps_down"))))
        if c.get("components") is not None:
            what.append(_p(c["components"], "SAM component") + " not up")
        if c.get("volumes") is not None:
            what.append(_p(c["volumes"], "volume"))
        if c.get("vim_hosts") is not None:
            what.append(_p(c["vim_hosts"], "VIM host"))
        if c.get("vim_vms") is not None:
            what.append(_p(c["vim_vms"], "VIM VM"))
        out.append("It holds:")
        out.extend("- " + w for w in what)
    v = (r.get("version") or {}).get("platform")
    out.append("Read in %s s with %s%s." % (r.get("seconds", 0), _p(r.get("queries"), "SWQL query", "SWQL queries"),
                                             (" from SolarWinds " + v) if v else ""))
    absent = [x for x in r.get("not_read") or [] if x.get("absent")]
    failed = [x for x in r.get("not_read") or [] if not x.get("absent")]
    if absent:
        out.append("Not on this Orion (a module it does not have):")
        out.extend("- %s" % x.get("what") for x in absent)
    if failed:
        out.append("Not read:")
        out.extend("- %s: %s" % (x.get("what"), x.get("why")) for x in failed)
    pr = r.get("pruned") or {}
    old = sum(v for k, v in pr.items() if k not in ("sw_event", "sw_snapshot", "sw_link_day"))
    if any(pr.values()):
        deleted = [_p(old, "row") + " of older snapshots", _p(pr.get("sw_event", 0), "old event"),
                   _p(pr.get("sw_snapshot", 0), "old snapshot row")]
        if pr.get("sw_link_day"):
            deleted.append(_p(pr["sw_link_day"], "old daily topology row"))
        out.append("Deleted: %s." % ", ".join(deleted))
    if r.get("created"):
        out.append("The SolarWinds tables were made in %s." % r.get("where", "the report database"))
    return out


class FilterModule(object):
    def filters(self):
        return {"swis_snapshot_lines": swis_snapshot_lines}
