import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKED = ("wall-page.js", "wall-styles.js", "portal-theme.js", "portal-looks.css")


def normalize(html):
    for name in HOOKED:
        html = re.sub(rb'(/static/' + re.escape(name.encode()) + rb'\?v=)[0-9a-f]{10}(?=")', rb'\1HASH', html)
    # 1.136.1: the portal owns these parts of the shell. Keep comparing the wall itself to the release.
    html = re.sub(rb' data-skin="[^"]+" data-skin-default="[^"]+"', b'', html, count=1)
    html = re.sub(rb'(<select id="look"[^>]*>).*?(</select>)', rb'\g<1>\g<2>', html, count=1)
    return re.sub(rb'<div class="appbar-contour"[^>]*>.*?</svg></div>', b'', html, count=1)


class Isolation(unittest.TestCase):
    def test_disabled_portal_matches_release(self):
        fixtures = ROOT / "tests/fixtures/wall_v1351"
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, str(ROOT / "tests/wall/isolation_probe.py"),
                            str(ROOT / "roles/site_portal_stack/files"), tmp], check=True)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), sorted(p.name for p in fixtures.iterdir()))
            for expected in fixtures.iterdir():
                with self.subTest(expected=expected.name):
                    actual, baseline = (Path(tmp) / expected.name).read_bytes(), expected.read_bytes()
                    if expected.name == "wall.html":
                        actual, baseline = normalize(actual), normalize(baseline)
                    self.assertEqual(actual, baseline)


if __name__ == "__main__":
    unittest.main()
