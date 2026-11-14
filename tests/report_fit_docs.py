"""The reports' documents for tests/test_report_fit.py (1.21.0), into tests/fixtures/report_fit: every report document a
filter returns while the unit tests run (a dict with sections, or a list of table sections - the ones a findings report
adds), and with --pages, the reports of e2e runs that wrote a report website (their page's JSON: report_web_dest), for
the reports made in YAML. Run it again when a report gets new tables.
  python3 tests/report_fit_docs.py                       (about 5 minutes: each tests/test_*.py once)
  python3 tests/report_fit_docs.py --pages DIR [PREFIX]  (only the pages' reports, named PREFIX-<template>)
  python3 tests/report_fit_docs.py --named               (only NAMED: one test's document of a filter, its own name)"""
import glob
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, "tests", "fixtures", "report_fit")
# returned on the way to a report, or a workbook's sheets, not a page of their own
SKIP = re.compile(r":(report_act_off|report_notice|vm_vcenter_report|_report|servicenow_war_add_act|\w*_xlsx|"
                  r"report_xlsx_sheets|<genexpr>|vm_ask_run|table|_table|_section|_item_table|_moved_section|"
                  r"_written_section)$")
# a filter's document from one test, under a name of its own - a report's parts the biggest document of the filter has not
# (1.26.0: the checklist import's routes, moves and assignments; the update's Moves and STIG assignments)
NAMED = (("test_stig_ckl_import.py", "RouteReport.test_report_fit_document", "stig_ckl_filters.py:stig_ckl_report", "stig_ckl_report_routes"),
         ("test_stig.py", "UpdateReportMoves.test_report_fit_document", "stig_filters.py:stig_update_report", "stig_update_report_moves"),
         # 1.32.0: the Front-end nodes table with friendly names, the address that answered, nodes by their stats page only
         ("test_servicenow_node.py", "Labels.test_report_fit_document", "servicenow_node_filters.py:sn_node_sections", "sn_node_sections_pages"),
         # 1.33.0: the VMware health report's "How it was read" (a host's clock not read, the read times)
         ("test_vmware_modules.py", "HealthReadSpeed.test_the_report_says_what_was_not_read", "vmware_health_filters.py:vm_health_report",
          "vm_health_report_read"),
         # 1.56.0: the Low level - the node log with every level, a findings report with Low rows and a Low box
         ("test_low_servicenow.py", "Fit.test_report_fit_document", "servicenow_node_filters.py:sn_node_sections", "sn_node_sections_low"),
         ("test_low_servicenow.py", "Fit.test_findings_report_fit_document", "site_filters.py:findings_report", "findings_report_low"),
         # 1.61.0: patch age - the by-group table with every column, the oldest hosts; the website's copy with the squares
         ("test_patch_age.py", "Looks.test_report_fit_document", "patch_age_filters.py:patch_age_sections", "patch_age_sections"),
         ("test_patch_age.py", "Looks.test_report_fit_document", "site_filters.py:findings_report", "patch_age_web"),
         # 1.64.0: the certificate timeline - the email's renew list, the website's page with the timeline above it
         ("test_cert_timeline.py", "Looks.test_report_fit_document", "cert_timeline_filters.py:cert_timeline", "cert_timeline"),
         ("test_cert_timeline.py", "Looks.test_report_fit_document", "site_filters.py:findings_report", "cert_timeline_web"),
         # 1.66.0: the outage review - the website's page with the time line, and the email's
         ("test_outage_timeline.py", "Looks.test_report_fit_document", "outage_timeline_filters.py:outage_timeline_doc", "outage_review_web"),
         ("test_outage_timeline.py", "Looks.test_report_fit_email", "outage_timeline_filters.py:outage_timeline_doc", "outage_review_email"),
         # int-11: the STIG scorecard with rows in "Fixed on the host, still Open" (Low; report_fit draws it in every look)
         ("test_stig_scorecard.py", "Scorecard.test_fixed_on_the_host_still_open", "stig_scorecard_filters.py:stig_scorecard",
          "stig_scorecard_fixed"),
         # 1.63.0: the AD replication matrix - the website's copy (matrix, late links, trend), the email's, a forest past
         # ad_repl_matrix_max_dcs (site x site, long site names)
         ("test_ad_repl.py", "Fit.test_report_fit_web", "ad_repl_filters.py:ad_repl_insert", "ad_repl_web"),
         ("test_ad_repl.py", "Fit.test_report_fit_email", "ad_repl_filters.py:ad_repl_insert", "ad_repl_email"),
         ("test_ad_repl.py", "Fit.test_report_fit_forest", "ad_repl_filters.py:ad_repl_insert", "ad_repl_forest"),
         # int-13: 12 DCs with long site names (the band rows), 15 and 20 sites (past 12: the site pairs listed)
         ("test_ad_repl.py", "Fit.test_report_fit_dcs_12", "ad_repl_filters.py:ad_repl_insert", "ad_repl_dcs_12"),
         ("test_ad_repl.py", "Fit.test_report_fit_sites_15", "ad_repl_filters.py:ad_repl_insert", "ad_repl_sites_15"),
         ("test_ad_repl.py", "Fit.test_report_fit_sites_20", "ad_repl_filters.py:ad_repl_insert", "ad_repl_sites_20"),
         # 1.65.0: the VMware cluster heat grid - the website's copy, the email's, a cluster of 40 hosts with long names
         ("test_vmware_heat.py", "Fit.test_report_fit_web", "vmware_heat_filters.py:vm_heat_insert", "vm_heat_web"),
         ("test_vmware_heat.py", "Fit.test_report_fit_email", "vmware_heat_filters.py:vm_heat_insert", "vm_heat_email"),
         ("test_vmware_heat.py", "Fit.test_report_fit_big", "vmware_heat_filters.py:vm_heat_insert", "vm_heat_big"))

# run inside each test file's process: keep the biggest document each filter function returns
CHILD = r"""
import json, os, runpy, sys
test, out = sys.argv[1], sys.argv[2]
keep = {}
def rows(d):
    return sum(len(s.get('rows') or []) for s in d['sections'])
def doc(v, name):
    if isinstance(v, dict) and isinstance(v.get('report'), dict):
        v = v['report']
    if isinstance(v, dict) and isinstance(v.get('sections'), list) and v['sections'] and all(isinstance(s, dict) for s in v['sections']):
        return v
    secs = [v] if isinstance(v, dict) and 'columns' in v and 'rows' in v else v
    if isinstance(secs, list) and secs and all(isinstance(s, dict) and 'columns' in s and 'rows' in s and s.get('title') for s in secs):
        return {'title': name + ' (its tables, inside a findings report)', 'status': 'info', 'sections': secs}
def prof(frame, event, arg):
    if event != 'return' or arg is None:
        return
    fn = frame.f_code.co_filename
    if '/filter_plugins/' not in fn and '/plugins/filter/' not in fn:
        return
    k = os.path.basename(fn) + ':' + frame.f_code.co_name
    # 1.57.0: a report's website version too (its 'web': the capacity report's runway board), as <function>_web
    for kk, v in ((k, arg), (k + '_web', arg.get('web') if isinstance(arg, dict) and isinstance(arg.get('web'), dict) else None)):
        d = doc(v, frame.f_code.co_name) if v is not None else None
        if d is None:
            continue
        try:
            d = json.loads(json.dumps(d, default=str))
        except Exception:
            continue
        if kk not in keep or rows(d) > rows(keep[kk]):
            keep[kk] = d
sys.path.insert(0, os.path.dirname(test))
sys.argv = [test] + sys.argv[3:]        # a test's name: that test only (NAMED)
sys.setprofile(prof)
try:
    runpy.run_path(test, run_name='__main__')
except SystemExit:
    pass
finally:
    sys.setprofile(None)
    json.dump(keep, open(out, 'w'))
"""


def from_tests():
    found = {}
    for t in sorted(glob.glob(os.path.join(REPO, "tests", "test_*.py"))):
        if os.path.basename(t) == "test_report_fit.py":
            continue
        tmp = os.path.join(OUT, ".capture.json")
        subprocess.run([sys.executable, "-c", CHILD, t, tmp], cwd=REPO, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
        if os.path.exists(tmp):
            for k, v in json.load(open(tmp)).items():
                if not SKIP.search(k) and (k not in found or sum(len(s.get("rows") or []) for s in v["sections"]) >
                                           sum(len(s.get("rows") or []) for s in found[k]["sections"])):
                    found[k] = v
            os.remove(tmp)
    for k, v in found.items():
        name = k.split(":")[1].strip("_")
        if name in ("extra",):                    # a name that says nothing by itself: its file's too
            name = k.split(":")[0].replace("_filters.py", "") + "_" + name
        with open(os.path.join(OUT, name + ".json"), "w") as f:
            json.dump(v, f, indent=0, sort_keys=True)
    return len(found)


def from_named():
    for test, name, key, out in NAMED:
        tmp = os.path.join(OUT, ".capture.json")
        subprocess.run([sys.executable, "-c", CHILD, os.path.join(REPO, "tests", test), tmp, name], cwd=REPO, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900)
        got = json.load(open(tmp)) if os.path.exists(tmp) else {}
        if os.path.exists(tmp):
            os.remove(tmp)
        if key not in got:
            raise SystemExit("%s %s made no %s document" % (test, name, key))
        with open(os.path.join(OUT, out + ".json"), "w") as f:
            json.dump(got[key], f, indent=0, sort_keys=True)
    return len(NAMED)


def from_pages(folder, prefix):
    """Each report template's run with the most table rows (a page's JSON beside it: {template, report, ...})."""
    best = {}
    for p in sorted(glob.glob(os.path.join(folder, "**", "*.json"), recursive=True)):
        try:
            d = json.load(open(p))
        except ValueError:
            continue
        r = d.get("report") if isinstance(d, dict) else None
        if not (isinstance(r, dict) and any(s.get("rows") for s in r.get("sections") or [])):
            continue
        name = re.sub(r"[^a-z0-9]+", "-", (prefix + "-" + (d.get("template") or os.path.basename(p)[:-5])).lower()).strip("-")
        n = sum(len(s.get("rows") or []) for s in r["sections"])
        if name not in best or n > best[name][0]:
            best[name] = (n, r)
    for name, (_, r) in best.items():
        with open(os.path.join(OUT, name + ".json"), "w") as f:
            json.dump(r, f, indent=0, sort_keys=True)
    return len(best)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    if len(sys.argv) > 2 and sys.argv[1] == "--pages":
        print("%d report(s) from %s" % (from_pages(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "e2e"), sys.argv[2]))
    elif sys.argv[1:] == ["--named"]:
        print("%d named report document(s)" % from_named())
    else:
        print("%d report document(s) from the unit tests, %d named" % (from_tests(), from_named()))
