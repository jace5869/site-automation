"""Capture the default portal locally. --regen extracts the release used for the golden files."""
import argparse
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
FILES = Path("roles/site_portal_stack/files")
GOLD = ROOT / "tests/fixtures/wall_v1351"
NOW = 1700000000


def capture(files, out, tmp):
    plugins = files.parents[2] / "plugins"
    sys.path[:0] = list(map(str, (files, plugins / "module_utils", plugins / "filter")))
    import site_portal as sp
    import wall_live as wl

    objects = [("HostSystem", "h1", {"name": "host-a", "runtime.connectionState": "connected"}),
               ("HostSystem", "h2", {"name": "host-b", "runtime.connectionState": "disconnected"}),
               ("VirtualMachine", "v1", {"name": "app", "runtime.host": ("HostSystem", "h1"), "guest.ipAddress": "192.0.2.1"}),
               ("Datastore", "ds1", {"name": "store", "summary.capacity": "2048", "summary.freeSpace": "1024"})]
    shaped = wl.shape(objects)
    view = {"sources": {"vc": {"name": "vc.corp.example.mil", "kind": "vcenter", "state": "ok",
                               "at": NOW - 20, "snap": shaped}}, "history": {}}
    source = wl.VcenterSource.__new__(wl.VcenterSource)
    source.view, source.sc = "view", {"propertyCollector": "pc"}
    source._send = Mock(return_value=ElementTree.Element("response"))
    source._retrieve()
    (tmp / "actions").write_text('{"actions":{}}')
    cfg = sp.Config({"PORTAL_DATA": str(tmp), "PORTAL_ACTIONS": str(tmp / "actions"),
                     "PORTAL_AAP_URL": "https://aap.corp.example.mil", "PORTAL_AAP_TOKEN": "fixture", "PORTAL_TIME_ZONE": "UTC"})
    portal = sp.Portal(cfg)
    portal.key = b"fixture"
    wall = sp.WallLive(cfg, portal.log, collector=SimpleNamespace(view=lambda: view))
    handler = sp.make_handler(cfg, portal, files / "static", wall)
    out.mkdir(parents=True, exist_ok=True)

    def request(path, headers=None):
        h = handler.__new__(handler)
        h.command, h.path, h.request_version = "GET", path, "HTTP/1.1"
        h.requestline = "GET " + path + " HTTP/1.1"
        h.client_address = ("192.0.2.1", 1234)
        h.headers = {"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-S-DN": "CN=Viewer"}
        h.headers.update(headers or {})
        h.wfile = io.BytesIO()
        h.do_GET()
        head, body = h.wfile.getvalue().split(b"\r\n\r\n", 1)
        lines = head.decode().split("\r\n")
        return int(lines[0].split()[1]), dict(line.split(": ", 1) for line in lines[1:]), body

    code, headers, body = request("/api/wall")
    assert code == 200
    (out / "wall.body").write_bytes(body)
    tag = headers["ETag"]
    status = request("/api/wall", {"If-None-Match": tag})[0]
    assert status == 304
    for route, name in [("/api/wall/summary", "summary.body"), ("/wall", "wall.html")]:
        code, _, body = request(route)
        assert code == 200
        (out / name).write_bytes(body)
    nal = sp.NetappLive(cfg, portal.log, portal, db=Mock())
    wall.nal = nal
    nal.refresh = Mock()
    wall.viewed = Mock(wraps=wall.viewed)
    with patch("threading.Thread.start", lambda thread: thread.run()):
        fc, fh, fb = request("/api/wall/fleet")
    assert fc == 404
    unknown = {}
    for ext in ("js", "css"):
        code, _, body = request("/static/wall-fleet." + ext)
        assert code == 404
        unknown[ext] = [code, body.decode()]
    inputs = [{}, {"wall": False}, {"wall": True, "vc_on": True, "ontap_on": True},
              {"wall": True, "vc_mode": "false", "netmap_sites": {"SITEA": "DE"}}]
    record = dict(etag=tag, repeat=status, fleet=[fc, fh, fb.decode()], viewed=wall.viewed.call_count,
                  refresh=nal.refresh.call_count, static=unknown, vm_paths=wl.VM_PATHS, retrieve=source._send.call_args.args,
                  shape=shaped, deploy=[sp.rules.site_portal_wall_summary(w) for w in inputs],
                  hashes={n: hashlib.sha256((files / "static" / n).read_bytes()).hexdigest()
                          for n in ("wall-share.js", "portal-looks.js")})
    (out / "observations.txt").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="?", type=Path, default=ROOT / FILES)
    parser.add_argument("out", nargs="?", type=Path, default=GOLD)
    parser.add_argument("--regen", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory, patch.object(time, "time", return_value=NOW):
        tmp = Path(directory)
        files = args.files
        if args.regen:
            with tempfile.TemporaryFile() as archive:
                subprocess.run(["git", "archive", "v1.135.1", str(FILES), "plugins"], cwd=ROOT, stdout=archive, check=True)
                archive.seek(0)
                subprocess.run(["tar", "-x", "-C", directory], stdin=archive, check=True)
            files = tmp / FILES
        capture(files.resolve(), args.out, tmp)
