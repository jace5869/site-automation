"""The reports' documents for tests/test_report_fit.py (1.21.0), into tests/fixtures/report_fit: every report document a
filter returns while the unit tests run (a dict with sections, or a list of table sections - the ones a findings report
adds), and with --pages, the reports of e2e runs that wrote a report website (their page's JSON: report_web_dest), for
the reports made in YAML. Run it again when a report gets new tables.
  python3 tests/report_fit_docs.py                       (about 5 minutes: each tests/test_*.py once)
  python3 tests/report_fit_docs.py --pages DIR [PREFIX]  (only the pages' reports, named PREFIX-<template>)"""
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
    d = doc(arg, frame.f_code.co_name)
    if d is None:
        return
    try:
        d = json.loads(json.dumps(d, default=str))
    except Exception:
        return
    k = os.path.basename(fn) + ':' + frame.f_code.co_name
    if k not in keep or rows(d) > rows(keep[k]):
        keep[k] = d
sys.path.insert(0, os.path.dirname(test))
sys.argv = [test]
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
    else:
        print("%d report document(s) from the unit tests" % from_tests())
