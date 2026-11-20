#!/usr/bin/env python3
"""tests/netmap/run_netmap_test.sh: what a report job kept for the copy job (its artifact site_report_page) holds of the map -
its data only ({kind, data, motion}), no markup and no script: the copy job builds those from its own repository.
  kept_piece.py REPO JOBS.json JOB_ID"""
import json
import sys

sys.path.insert(0, sys.argv[1] + "/plugins/module_utils")
import site_report_page as rp  # noqa: E402

job = [j for j in json.load(open(sys.argv[2])) if j["id"] == int(sys.argv[3])][0]
packed = job["artifacts"]["site_report_page"]
doc = rp.unpack(packed[0] if isinstance(packed, list) else packed)
x = doc["page_extra"]
assert sorted(x) == ["data", "kind", "motion"] and x["kind"] == "netmap", sorted(x)
assert "<script" not in json.dumps(x) and "@@NETMAP@@" not in json.dumps(x["data"]), "markup in the kept piece"
assert len(x["data"]["nodes"]) > 1400
