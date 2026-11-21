import sys, os
sys.path.insert(0, "plugins/module_utils")
import report_head as rh
for t in sys.argv[1:]:
    print(t, "=>", rh.report_head({"title": t, "status": "warning"})["counts_text"], "|", rh.report_head({"title": t, "status": "warning"})["scope_text"])
