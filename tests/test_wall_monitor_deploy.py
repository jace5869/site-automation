"""Render the role's deploy expressions with real ansible-core, entirely on localhost."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / "roles/site_portal_stack"
BASE = "Wall: VMware on, Storage on, Map places from netmap_sites: 0"


@unittest.skipUnless(shutil.which("ansible-playbook"), "ansible-playbook unavailable")
class Deploy(unittest.TestCase):
    def run_cases(self, invalid=False):
        tasks = {t["name"]: t for t in yaml.safe_load((ROLE / "tasks/main.yml").read_text())}
        switch = tasks["Settings | the wall's Ops monitor area (portal_wall_monitor)"]
        settings = tasks["Config | the service's settings"]["ansible.builtin.copy"]["content"]
        summary = tasks["Verify | summary"]["vars"]["_wall_line"]
        with tempfile.TemporaryDirectory() as tmp:
            plays = []

            def case(variables, enabled):
                values = dict(portal_wall=True, _portal_wall_vc=True, _portal_wall_ontap=True,
                              _settings={"PORTAL_WALL": "1", "PORTAL_PATH": "/portal/"},
                              portal_solarwinds_username="reader" if enabled else None, portal_solarwinds_password="fixture",
                              portal_solarwinds_url="https://swis.corp.example.mil", portal_solarwinds_ca_file="",
                              _portal_dirs={"results": [{}]}, **variables)
                steps = [switch]
                if enabled:
                    steps.append({"ansible.builtin.include_tasks": str(ROLE / "tasks/wall_monitor.yml")})
                steps.extend([{"ansible.builtin.set_fact": {"rendered": settings, "summary": summary}},
                              {"ansible.builtin.assert": {"that": [
                                  "summary == expected", "(_portal_wall_monitor | bool) == enabled",
                                  "('PORTAL_WALL_MONITOR_OPTS' in (rendered | from_json)) == enabled",
                                  "enabled or rendered == (_settings | to_nice_json) ~ '\\n'",
                                  "not enabled or (rendered | from_json).PORTAL_WALL_MONITOR_OPTS == expected_opts"]}}])
                values.update(enabled=enabled, expected=BASE + (", Ops monitor on (by groups)" if enabled else ""),
                              expected_opts=dict(seconds=60, max_nodes=10000, poll_stale_minutes=15, group_by="groups",
                                                 groups=[], vm_match=["*VMware*"], ca="", swis_url=values["portal_solarwinds_url"]))
                overrides = Path(tmp) / ("case-%d.yml" % len(plays))
                overrides.write_text(yaml.safe_dump(values))
                plays.append(dict(hosts="localhost", gather_facts=False,
                                  vars_files=[str(ROLE / "defaults/main.yml"), str(overrides)], tasks=steps))

            if not invalid:
                case(dict(portal_wall_monitor=False), False)
                for switch_value in (False, "no", None):
                    for bad in (float("inf"), None, {"a": 1}, [1], "x"):
                        values = {"portal_wall_monitor_" + k: bad for k in
                                  ("seconds", "group_by", "groups", "vm_match", "max_nodes", "poll_stale_minutes")}
                        values["portal_wall_monitor"] = switch_value
                        case(values, False)
                case({}, True)
            case(dict(portal_wall_monitor=True, portal_wall_monitor_seconds=float("inf") if invalid else 60), True)
            path = Path(tmp) / "play.yml"
            path.write_text(yaml.safe_dump(plays, sort_keys=False))
            env = dict(os.environ, ANSIBLE_LOCAL_TEMP=tmp, ANSIBLE_REMOTE_TEMP=tmp,
                       ANSIBLE_FILTER_PLUGINS=str(ROOT / "plugins/filter"), ANSIBLE_NOCOLOR="1")
            return subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(path)],
                                  cwd=ROOT, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def check_disabled_monitor_ca(self, map_ca):
        tasks = yaml.safe_load((ROLE / "tasks/main.yml").read_text())
        names = [task["name"] for task in tasks]
        steps = tasks[names.index("Settings | prepare the Ops monitor"):
                      names.index("Files | the service, its pages, the actions' rules, AAP's client")]
        from ansible.parsing.dataloader import DataLoader
        from ansible.template import Templar, trust_as_template
        ca = "/etc/site-portal/app/solarwinds-monitor.pem"
        values = dict(portal_own_nginx=True, portal_netmap=False, _portal_netmap=False,
                      _portal_wall_monitor=False, portal_config_dir="/etc/site-portal",
                      portal_ca_file=ca, portal_solarwinds_ca_file=trust_as_template(map_ca))
        for task in steps:
            for item in task.get("loop", [None]):
                templar = Templar(loader=DataLoader(), variables=dict(values, item=item))
                if templar.evaluate_conditional(trust_as_template(task["when"])):
                    file = task["ansible.builtin.file"]
                    path = templar.template(trust_as_template(file["path"]))
                    self.assertNotEqual(path, ca, task["name"])

    def test_disabled_monitor_preserves_nginx_ca(self):
        self.check_disabled_monitor_ca("")

    def test_disabled_monitor_allows_undefined_map_ca(self):
        self.check_disabled_monitor_ca("{{ sitea_swis_ca }}")

    def test_switch_settings_and_summary(self):
        result = self.run_cases()
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_enabled_invalid_interval(self):
        result = self.run_cases(invalid=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("portal_wall_monitor_seconds 60 to 3600", result.stdout)
        self.assertIn("portal_wall_monitor_seconds | string", result.stdout)


if __name__ == "__main__":
    unittest.main()
