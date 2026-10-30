#!/usr/bin/python
# -*- coding: utf-8 -*-
"""The DISA STIG scan of a Linux host with OpenSCAP (oscap and the SCAP Security Guide's STIG profile, or a
DISA SCAP benchmark), one result per STIG rule, and the host's own history: each run's report, results and
summary are kept on the host, and the last summary is returned with this one so the two can be compared - with
every STIG rule that passed on the host in any scan before this one (passed_before).
Standard library only."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_stig_scan
short_description: Scan this host against the DISA STIG with OpenSCAP and keep the result on the host
description:
  - Runs C(oscap xccdf eval) with the STIG profile of the SCAP Security Guide for this RHEL version
    (C(content_dir)/ssg-rhel<major>-ds.xml), or the datastream and profile named, and reads the results - one
    result per STIG rule (several SCAP rules for one STIG rule - the worst counts; one SCAP rule for several STIG
    rules - its result counts for each).
  - Keeps the run on the host in C(dir) - the HTML report, the results (gzip) and a summary (JSON) - and the
    last C(keep) runs; returns the summary before this one (C(previous)) to compare with.
  - In check mode (or with save false) it scans but keeps nothing.
options:
  dir: {description: Where the runs are kept on the host (created root-only)., type: str, default: /var/lib/site-automation/stig}
  keep: {description: How many runs are kept (the oldest are deleted)., type: int, default: 10}
  save: {description: Keep this run on the host (false - a dry run - scans and keeps nothing)., type: bool, default: true}
  keep_report: {description: Keep oscap's HTML report of each run., type: bool, default: true}
  keep_results: {description: Keep the XCCDF results of each run (gzip; about 1 MB)., type: bool, default: true}
  oscap: {description: The oscap program ('' = found in PATH)., type: str, default: ''}
  content_dir: {description: Where the SCAP Security Guide's datastreams are., type: str, default: /usr/share/xml/scap/ssg/content}
  datastream: {description: "A datastream instead ('' = content_dir/ssg-rhel<major>-ds.xml).", type: str, default: ''}
  profile: {description: The profile (its full id or the part after content_profile_)., type: str, default: stig}
  tailoring: {description: A tailoring file ('' = none)., type: str, default: ''}
  timeout: {description: Seconds the scan may take., type: int, default: 1800}
author: site automation
'''

RETURN = r'''
scanned:
  description: The scan ran and its results were read.
  type: bool
  returned: always
why:
  description: Why it did not scan (not scanned), in plain words.
  type: str
  returned: always
items:
  description: One per STIG rule (or per SCAP rule that names none) in the profile.
  type: list
  returned: success
  sample: [{key: SV-257985, v: V-257985, rule: SV-257985r1069364_rule, stigid: RHEL-09-255045, severity: medium,
            title: "RHEL 9 must not permit direct logons to the root account using remote access through SSH.",
            result: fail, scap: [sshd_disable_root_login], scap_ids: [xccdf_org.ssgproject.content_rule_sshd_disable_root_login]}]
previous:
  description: "The last summary kept on the host before this run ({} = none), with its items as {key: result}."
  type: dict
  returned: success
passed_before:
  description: "Every STIG rule that passed on the host in a scan before this one, with the time it last passed
    ({key: time}). Kept in each summary, so it outlives the scans deleted (keep); built from the scans kept when the
    last summary has none."
  type: dict
  returned: success
'''

import datetime
import glob
import gzip
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from ansible.module_utils.basic import AnsibleModule

X = "{http://checklists.nist.gov/xccdf/1.2}"
# worst first: the result a STIG rule gets from its SCAP rules
ORDER = ["fail", "error", "unknown", "notchecked", "pass", "fixed", "informational", "notapplicable", "notselected"]
SV_RE = re.compile(r"(SV-\d+)(r\d+)?(_rule)?")
V_RE = re.compile(r"^V-\d+$")
STIGID_RE = re.compile(r"^[A-Z0-9]+-\d{2}-\d{6}$")
VERSION_RE = re.compile(r"\bV(\d+)R(\d+)\b")
STAMP_RE = re.compile(r"^\d{8}T\d{6}Z_summary\.json$")


def worst(results):
    rs = [r for r in results if r in ORDER]
    return min(rs, key=ORDER.index) if rs else "notchecked"


def short_rule(idref):
    """xccdf_org.ssgproject.content_rule_sshd_disable_root_login -> sshd_disable_root_login"""
    for p in ("xccdf_org.ssgproject.content_rule_", "xccdf_mil.disa.stig_rule_"):
        if idref.startswith(p):
            return idref[len(p):]
    return idref


def read_results(path):
    """The XCCDF results file oscap wrote (the benchmark and its TestResult in one) -> (meta, rules, results).
    rules: {idref: {title, severity, svs, stigids, sv, rule, v, stigid}} - svs: every STIG rule it checks (SV ids
    with their revision: one SCAP rule can check several), sv / rule / v / stigid: the first; results: {idref:
    (result, severity)} of the selected rules. Read as a stream: the file is about 20 MB."""
    rules, results, meta = {}, {}, {"profile_title": "", "profile_text": "", "ssg_version": "", "benchmark": "",
                                     "start": "", "end": ""}
    profile_id = None
    for _, el in ET.iterparse(path, events=("end",)):
        tag = el.tag
        if tag == X + "Rule":
            info = {"title": (el.findtext(X + "title") or "").strip(), "severity": el.get("severity", ""),
                    "svs": [], "stigids": [], "sv": "", "rule": "", "v": "", "stigid": ""}
            for ref in el.findall(X + "reference"):
                text = (ref.text or "").strip()
                if SV_RE.fullmatch(text):
                    if text not in info["svs"]:
                        info["svs"].append(text)
                elif STIGID_RE.match(text) and "cyber.mil" in (ref.get("href") or "") and text not in info["stigids"]:
                    info["stigids"].append(text)
            m = SV_RE.search(el.get("id", ""))           # a DISA benchmark: the rule's own id is the SV id
            if m and not info["svs"]:
                info["svs"].append(m.group(0))
            if not info["stigids"] and (el.findtext(X + "version") or "").strip():
                info["stigids"].append((el.findtext(X + "version") or "").strip())
            if info["svs"]:
                info["rule"] = info["svs"][0]
                info["sv"] = SV_RE.fullmatch(info["rule"]).group(1)
                info["v"] = "V-" + info["sv"][3:]
            info["stigid"] = info["stigids"][0] if info["stigids"] else ""
            rules[el.get("id")] = info
            el.clear()
        elif tag == X + "Group" or tag == X + "Value":
            el.clear()
        elif tag == X + "TestResult":
            meta["ssg_version"] = el.get("version", "")
            meta["start"], meta["end"] = el.get("start-time", ""), el.get("end-time", "")
            p = el.find(X + "profile")
            profile_id = p.get("idref") if p is not None else None
            b = el.find(X + "benchmark")
            meta["benchmark"] = b.get("id", "") if b is not None else ""
            for rr in el.findall(X + "rule-result"):
                res = rr.findtext(X + "result") or "unknown"
                if res != "notselected":
                    results[rr.get("idref")] = (res, rr.get("severity", ""))
            el.clear()
        elif tag == X + "Profile":
            if profile_id is None or el.get("id") == profile_id:
                meta.setdefault("_profiles", {})[el.get("id")] = ((el.findtext(X + "title") or "").strip(),
                                                                  (el.findtext(X + "description") or "").strip())
    profiles = meta.pop("_profiles", {})
    if profile_id in profiles:
        meta["profile_title"], meta["profile_text"] = profiles[profile_id]
    meta["profile"] = profile_id or ""
    return meta, rules, results


def stig_version(*texts):
    """'... DISA STIG for Red Hat Enterprise Linux 9 V2R9.' -> 'V2R9' ('' = not named)."""
    for t in texts:
        m = VERSION_RE.search(t or "")
        if m:
            return "V%sR%s" % (m.group(1), m.group(2))
    return ""


def items_of(rules, results):
    """One item per STIG rule: the SCAP rules that check the same STIG rule (SV id) are one item, and the worst
    of their results is its result; a SCAP rule that checks several STIG rules (dconf_gnome_banner_enabled: two,
    RHEL 8's configure_crypto_policy: six) counts for each of them. A SCAP rule with no STIG rule is an item of its
    own (key = its name). The STIG id (RHEL-09-255045) only when the SCAP rule checks one STIG rule: the content
    lists several in no order that pairs them with their SV ids."""
    by = {}
    sevs = ["high", "medium", "low"]
    for idref, (res, sev) in results.items():
        info = rules.get(idref) or {"title": short_rule(idref), "severity": sev, "svs": [], "stigids": []}
        svs = info.get("svs") or []
        for full in svs or [""]:
            key = SV_RE.fullmatch(full).group(1) if full else short_rule(idref)
            it = by.get(key)
            if it is None:
                it = by[key] = {"key": key, "v": ("V-" + key[3:]) if full else "", "rule": full,
                                "stigid": (info.get("stigids") or [""])[0] if len(svs) <= 1 else "",
                                "severity": info["severity"] or sev, "title": info["title"], "results": [], "scap": [], "scap_ids": []}
            if not it["stigid"] and len(svs) == 1:
                it["stigid"] = (info.get("stigids") or [""])[0]
            it["results"].append(res)
            it["scap"].append(short_rule(idref))
            it["scap_ids"].append(idref)
            if (info["severity"] or sev) in sevs and (it["severity"] not in sevs or sevs.index(info["severity"] or sev) < sevs.index(it["severity"])):
                it["severity"] = info["severity"] or sev
    out = []
    for key in sorted(by):
        it = by[key]
        it["result"] = worst(it.pop("results"))
        it["scap"] = sorted(set(it["scap"]))
        it["scap_ids"] = sorted(set(it["scap_ids"]))
        out.append(it)
    return out


def passed_before(d, stamps, prev):
    """Every STIG rule that passed on the host before this scan -> {key: the time it last passed}: the last summary's
    own list, or - a summary from before 0.44.0 has none - built from the scans kept on the host."""
    if isinstance((prev or {}).get("passed_before"), dict):
        return dict(prev["passed_before"])
    out = {}
    for s in stamps:
        summ = load_summary(d, s) or {}
        for k, v in (summ.get("items") or {}).items():
            if v == "pass":
                out[k] = summ.get("time") or s
    return out


def counts(items):
    c = {}
    for it in items:
        c[it["result"]] = c.get(it["result"], 0) + 1
    return c


def os_release(path="/etc/os-release"):
    d = {}
    try:
        with open(path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    d[k] = v.strip().strip('"')
    except (IOError, OSError):
        pass
    return d


def full_profile(p):
    return p if p.startswith("xccdf_") else "xccdf_org.ssgproject.content_profile_" + p


def runs_in(d):
    """The runs kept in d, oldest first: their time stamps (the summaries' names)."""
    return sorted(os.path.basename(p)[:-len("_summary.json")] for p in glob.glob(os.path.join(d, "*_summary.json"))
                  if STAMP_RE.match(os.path.basename(p)))


def load_summary(d, stamp):
    try:
        with open(os.path.join(d, stamp + "_summary.json")) as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return None


class Scan(object):
    def __init__(self, module):
        self.m = module
        self.p = module.params

    def fail(self, why, **kw):
        return dict(scanned=False, why=why, items=[], previous={}, **kw)

    def run(self):
        p = self.p
        osr = os_release()
        ident = {"os": osr.get("PRETTY_NAME", ""), "os_id": osr.get("ID", ""),
                 "major": (osr.get("VERSION_ID", "") or "").split(".")[0]}
        oscap = p["oscap"] or shutil.which("oscap") or ""
        if not oscap or not os.access(oscap, os.X_OK):
            return self.fail("oscap is not installed (the package openscap-scanner; check_stig_install: true installs it)", **ident)
        ds = p["datastream"] or os.path.join(p["content_dir"], "ssg-rhel%s-ds.xml" % ident["major"])
        if not os.path.isfile(ds):
            return self.fail("no STIG content: %s is not there (the package scap-security-guide; check_stig_install: true "
                             "installs it, or check_stig_datastream names another)" % ds, datastream=ds, **ident)
        if p["tailoring"] and not os.path.isfile(p["tailoring"]):
            return self.fail("the tailoring file %s is not there" % p["tailoring"], datastream=ds, **ident)
        profile = full_profile(p["profile"])
        tmp = tempfile.mkdtemp(prefix="site-stig-")
        try:
            res_xml, rep_html = os.path.join(tmp, "results.xml"), os.path.join(tmp, "report.html")
            cmd = [oscap, "xccdf", "eval", "--profile", profile, "--results", res_xml]
            if p["keep_report"]:
                cmd += ["--report", rep_html]
            if p["tailoring"]:
                cmd += ["--tailoring-file", p["tailoring"]]
            cmd.append(ds)
            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=p["timeout"])
            except subprocess.TimeoutExpired:
                return self.fail("the scan took longer than %d seconds (check_stig_timeout)" % p["timeout"], datastream=ds, **ident)
            err = proc.stderr.decode("utf-8", "replace").strip()
            # oscap: 0 = every rule passed, 2 = at least one failed, 1 = it could not scan
            if proc.returncode not in (0, 2) or not os.path.isfile(res_xml):
                return self.fail("oscap could not scan (exit %d): %s" % (proc.returncode, err[-600:] or "no message"),
                                 datastream=ds, profile=profile, **ident)
            meta, rules, results = read_results(res_xml)
            items = items_of(rules, results)
            now = datetime.datetime.now(datetime.timezone.utc)
            stamp = now.strftime("%Y%m%dT%H%M%SZ")
            summary = {"time": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "stamp": stamp, "datastream": ds, "profile": profile,
                       "profile_title": meta["profile_title"], "stig_version": stig_version(meta["profile_title"], meta["profile_text"]),
                       "ssg_version": meta["ssg_version"], "benchmark": meta["benchmark"], "tailoring": p["tailoring"],
                       "counts": counts(items), "os": ident["os"], "major": ident["major"],
                       "items": {it["key"]: it["result"] for it in items}}
            d = p["dir"]
            stamps = runs_in(d) if os.path.isdir(d) else []
            prev = {}
            for s in reversed(stamps):
                prev = load_summary(d, s) or {}
                if prev:
                    break
            before = passed_before(d, stamps, prev)
            summary["passed_before"] = dict(before)
            summary["passed_before"].update((it["key"], summary["time"]) for it in items if it["result"] == "pass")
            saved = {}
            if self.p["save"] and not self.m.check_mode:
                saved = self.keep(d, stamp, summary, res_xml, rep_html, stamps)
            out = dict(ident)
            out.update((k, v) for k, v in summary.items() if k not in ("items", "passed_before"))
            prev.pop("passed_before", None)
            out.update(scanned=True, why="", items=items, previous=prev, passed_before=before, saved=saved, oscap_exit=proc.returncode,
                       stderr=err[-600:])
            return out
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def keep(self, d, stamp, summary, res_xml, rep_html, stamps):
        """This run in d (root-only), and only the last `keep` runs."""
        os.makedirs(d, mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
        saved = {"dir": d}
        old = os.umask(0o077)
        try:
            if self.p["keep_report"] and os.path.isfile(rep_html):
                saved["report"] = os.path.join(d, stamp + "_report.html")
                shutil.copyfile(rep_html, saved["report"])
            if self.p["keep_results"]:
                saved["results"] = os.path.join(d, stamp + "_results.xml.gz")
                with open(res_xml, "rb") as src, gzip.open(saved["results"], "wb") as dst:
                    shutil.copyfileobj(src, dst)
            saved["summary"] = os.path.join(d, stamp + "_summary.json")
            with open(saved["summary"], "w") as f:
                json.dump(summary, f, indent=1, sort_keys=True)
            latest = os.path.join(d, "latest-summary.json")
            shutil.copyfile(saved["summary"], latest + ".tmp")
            os.rename(latest + ".tmp", latest)
            if saved.get("report"):
                shutil.copyfile(saved["report"], os.path.join(d, "latest-report.html"))
        finally:
            os.umask(old)
        all_stamps = stamps + [stamp]
        for s in all_stamps[:max(0, len(all_stamps) - max(1, self.p["keep"]))]:
            for suffix in ("_summary.json", "_report.html", "_results.xml.gz"):
                try:
                    os.remove(os.path.join(d, s + suffix))
                except OSError:
                    pass
        return saved


def main():
    module = AnsibleModule(argument_spec=dict(
        dir=dict(type="str", default="/var/lib/site-automation/stig"),
        keep=dict(type="int", default=10),
        save=dict(type="bool", default=True),
        keep_report=dict(type="bool", default=True),
        keep_results=dict(type="bool", default=True),
        oscap=dict(type="str", default=""),
        content_dir=dict(type="str", default="/usr/share/xml/scap/ssg/content"),
        datastream=dict(type="str", default=""),
        profile=dict(type="str", default="stig"),
        tailoring=dict(type="str", default=""),
        timeout=dict(type="int", default=1800),
    ), supports_check_mode=True)
    result = Scan(module).run()
    module.exit_json(changed=False, **result)


if __name__ == "__main__":
    main()
