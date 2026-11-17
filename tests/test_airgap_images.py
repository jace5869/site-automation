#!/usr/bin/env python3
"""1.70.3: an air-gapped host. Every Quadlet container template never pulls (PodmanArgs=--pull=never); every stack role checks
its images (podman image exists) before it writes anything, after the registry login (so a pull, when allowed, can work); the
preflight's message names each image and the setting, with the load steps; the pull settings default to false; the images
are listed in docs/ENCLAVES.md.
python3 tests/test_airgap_images.py"""
import glob
import os
import re
import sys
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "plugins", "filter"))
import airgap_filters as f  # noqa: E402

STACKS = {"stigman_stack": "stigman", "site_portal_stack": "portal", "vmware_ask_stack": "vmware_ask"}
# the tasks that change the host: before the first of them the images must be known
WRITES = re.compile(r"^(Files|TLS|Secrets?|Units|Config|Own nginx|Actions \| the file|Trust \|)")


def tasks_of(role):
    with open(os.path.join(REPO, "roles", role, "tasks", "main.yml")) as fh:
        return [t for t in yaml.safe_load(fh) if isinstance(t, dict)]


class Text(unittest.TestCase):
    def test_one_image(self):
        t = f.airgap_images_text([{"var": "portal_image", "image": "hub.example.mil/ee:1"}], "system", "portal_pull_images")
        self.assertIn("STOPPED: the image hub.example.mil/ee:1 is not on this host. Nothing was changed.", t)
        self.assertIn("(setting: portal_image)", t)
        self.assertIn("podman pull hub.example.mil/ee:1", t)
        self.assertIn("podman save -o ee-1.tar hub.example.mil/ee:1", t)
        self.assertIn("as root:", t)
        self.assertIn("podman load -i ee-1.tar", t)
        self.assertIn("podman tag <the name podman load printed> hub.example.mil/ee:1", t)
        self.assertIn("portal_pull_images: true", t)
        self.assertIn("Images for an air-gapped enclave", t)

    def test_several_images_each_named_with_its_setting(self):
        m = [{"var": "stigman_images.mysql", "image": "docker.io/library/mysql:8.4"},
             {"var": "stigman_images.api", "image": "docker.io/nuwcdivnpt/stig-manager:1.6.17"}]
        t = f.airgap_images_text(m, "system", "stigman_pull_images")
        self.assertIn("STOPPED: 2 images are not on this host.", t)
        for x in m:
            self.assertIn("%s   (setting: %s)" % (x["image"], x["var"]), t)
        self.assertIn("podman load -i stig-manager-1.6.17.tar", t)
        self.assertIn("podman load -i mysql-8.4.tar", t)

    def test_a_failed_pull_is_said_per_image_and_the_hint_is_left_out(self):
        m = [{"var": "stigman_images.api", "image": "docker.io/x/api:1", "error": "Error: pinging container registry: refused"},
             {"var": "stigman_images.mysql", "image": "docker.io/x/mysql:8", "error": "no route to host"}]
        t = f.airgap_images_text(m, "system", "stigman_pull_images", True)
        self.assertIn("STOPPED: 2 images are not on this host and could not be pulled.", t)
        self.assertIn("stigman_pull_images is true, so the job tried to pull", t)
        self.assertIn("podman pull docker.io/x/api:1 failed: Error: pinging container registry: refused", t)
        self.assertIn("podman pull docker.io/x/mysql:8 failed: no route to host", t)
        self.assertNotIn("set stigman_pull_images: true", t)
        self.assertNotIn("The units never pull (Pull is never), so", t)
        self.assertIn("podman load -i api-1.tar", t)                         # the steps by hand stay

    def test_a_failed_pull_without_a_reason(self):
        t = f.airgap_images_text([{"var": "v", "image": "a/b:1"}], "system", "p", True)
        self.assertIn("podman pull a/b:1 failed: no reason given", t)

    def test_pull_failures_from_the_loop_results(self):
        item = {"item": {"var": "portal_image", "image": "hub/ee:1"}, "rc": 1}
        res = [{"rc": 125, "item": item, "stderr": "Error: first line\nsecond line\n"},
               {"rc": 0, "item": {"item": {"var": "ok", "image": "a:1"}}},
               {"skipped": True, "item": item}]
        self.assertEqual(f.airgap_pull_failures(res),
                         [{"var": "portal_image", "image": "hub/ee:1", "error": "Error: first line"}])
        self.assertEqual(f.airgap_pull_failures([{"rc": 1, "item": item}])[0]["error"], "no reason given")
        self.assertEqual(f.airgap_pull_failures(None), [])

    def test_a_rootless_stack_says_whose_account(self):
        t = f.airgap_images_text([{"var": "x", "image": "a/b:1"}], "user", "")
        self.assertIn("the account these units run as", t)
        self.assertNotIn("as root:", t)

    def test_no_pull_setting_named_when_none_given(self):
        self.assertNotIn("_pull_images", f.airgap_images_text([{"var": "x", "image": "a/b:1"}], "system", ""))

    def test_short_lines_and_no_wall_of_text(self):
        t = f.airgap_images_text([{"var": "stigman_images.api", "image": "docker.io/nuwcdivnpt/stig-manager:1.6.17"}], "system", "p")
        self.assertTrue(all(len(x) < 130 for x in t.split("\n")))

    def test_nothing_missing_nothing_said_wrongly(self):
        self.assertIn("STOPPED", f.airgap_images_text([], "system", ""))   # the role only calls it with something missing

    def test_the_filter_is_registered(self):
        self.assertIn("airgap_images_text", f.FilterModule().filters())


class Templates(unittest.TestCase):
    def test_every_container_template_never_pulls(self):
        files = glob.glob(os.path.join(REPO, "roles", "*", "templates", "*.container.j2"))
        self.assertGreaterEqual(len(files), 6)
        for p in files:
            with open(p) as fh:
                text = fh.read()
            self.assertRegex(text, r"(?m)^PodmanArgs=.*--pull=never", p)
            self.assertLess(text.index("Image="), text.index("PodmanArgs="), p)

    def test_the_unit_files_have_the_one_image_each(self):
        for p in glob.glob(os.path.join(REPO, "roles", "*", "templates", "*.container.j2")):
            with open(p) as fh:
                self.assertEqual(len(re.findall(r"(?m)^Image=", fh.read())), 1, p)


class Roles(unittest.TestCase):
    def test_the_image_check_comes_before_anything_is_written(self):
        for role, pre in STACKS.items():
            names = [str(t.get("name", "")) for t in tasks_of(role)]
            first_images = next(i for i, n in enumerate(names) if n.startswith("Images |"))
            writes = [i for i, n in enumerate(names) if WRITES.match(n)]
            self.assertTrue(writes, role)
            self.assertLess(first_images, min(writes), "%s: %s" % (role, names[min(writes)]))

    def test_the_registry_login_comes_before_the_images(self):
        for role in STACKS:
            names = [str(t.get("name", "")) for t in tasks_of(role)]
            self.assertLess(next(i for i, n in enumerate(names) if n.startswith("Registry |")),
                            next(i for i, n in enumerate(names) if n.startswith("Images |")), role)
            self.assertEqual(sum(1 for n in names if n.startswith("Registry |")), 1, role)

    def test_each_image_of_each_role_is_checked(self):
        want = {"stigman_stack": ["stigman_images.mysql", "stigman_images.api", "stigman_images.nginx"],
                "site_portal_stack": ["portal_image", "portal_nginx_image"], "vmware_ask_stack": ["vmware_ask_image"]}
        for role, vars_ in want.items():
            with open(os.path.join(REPO, "roles", role, "tasks", "main.yml")) as fh:
                text = fh.read()
            for v in vars_:
                self.assertTrue(("var: %s," % v) in text or ("'var': '%s'" % v) in text, "%s %s" % (role, v))

    def test_podman_image_exists_and_pull_are_the_commands(self):
        for role in STACKS:
            ts = [t for t in tasks_of(role) if str(t.get("name", "")).startswith("Images |")]
            self.assertEqual(len(ts), 3, role)
            argv = [t.get("ansible.builtin.command", {}).get("argv") for t in ts]
            self.assertEqual(argv[0][:3], ["podman", "image", "exists"])
            self.assertEqual(argv[1][:2], ["podman", "pull"])
            self.assertTrue(ts[0].get("check_mode") is False, role)       # only reads: a dry run knows too
            self.assertIn("not ansible_check_mode", str(ts[1].get("when")), role)

    def test_a_failed_pull_is_not_shown_as_changed(self):
        for role, pre in STACKS.items():
            pull = [t for t in tasks_of(role) if str(t.get("name", "")).startswith("Images | pulled")][0]
            reg = pull["register"]
            self.assertEqual(pull["changed_when"], "%s.rc == 0" % reg, role)
            self.assertIs(pull["failed_when"], False, role)

    def test_the_message_gets_the_pull_setting_and_the_pull_results(self):
        for role, pre in STACKS.items():
            with open(os.path.join(REPO, "roles", role, "tasks", "main.yml")) as fh:
                text = fh.read()
            self.assertIn("airgap_images_text(", text)
            self.assertRegex(text, r"airgap_images_text\(\w+, '\w+_pull_images', \w+_pull_images \| bool\)", role)
            self.assertIn("| airgap_pull_failures", text, role)

    def test_the_pull_settings_default_to_false(self):
        for role, pre in STACKS.items():
            with open(os.path.join(REPO, "roles", role, "defaults", "main.yml")) as fh:
                d = yaml.safe_load(fh)
            self.assertIs(d.get(pre + "_pull_images"), False, role)

    def test_the_portal_nginx_image_only_counts_with_its_own_nginx(self):
        with open(os.path.join(REPO, "roles", "site_portal_stack", "tasks", "main.yml")) as fh:
            text = fh.read()
        self.assertIn("'image': portal_nginx_image}] if portal_own_nginx | bool else []", text)


class Docs(unittest.TestCase):
    def test_enclaves_lists_every_image_with_its_setting(self):
        with open(os.path.join(REPO, "docs", "ENCLAVES.md")) as fh:
            text = fh.read()
        sec = text[text.index("## 5. Images for an air-gapped enclave"):text.index("## 6. Check it")]
        for v in ("IMAGE", "stigman_images.mysql", "stigman_images.api", "stigman_images.nginx", "portal_image",
                  "portal_nginx_image", "vmware_ask_image", "stigman_pull_images", "portal_pull_images", "vmware_ask_pull_images",
                  "podman load -i", "podman tag", "podman save"):
            self.assertIn(v, sec)

    def test_the_stack_docs_link_to_it(self):
        for doc in ("SITE_PORTAL.md", "VMWARE_ASK.md", "WORKFLOWS_AND_SCHEDULES.md"):
            with open(os.path.join(REPO, "docs", doc)) as fh:
                self.assertIn("ENCLAVES.md", fh.read(), doc)

    def test_every_paragraph_in_the_section_is_short(self):
        with open(os.path.join(REPO, "docs", "ENCLAVES.md")) as fh:
            text = fh.read()
        sec = text[text.index("## 5. Images for an air-gapped enclave"):text.index("## 6. Check it")]
        for para in re.split(r"\n\s*\n", sec):
            if para.lstrip().startswith(("|", "```", "-", "#")):
                continue
            self.assertLessEqual(len(re.findall(r"[.!?](?:\s|$)", para)), 3, para[:80])


if __name__ == "__main__":
    unittest.main()
