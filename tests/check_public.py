#!/usr/bin/env python3
"""Nothing of a real site goes into the repo: every host name, e-mail domain and IP address in it is a
placeholder. Fails on a name outside the placeholder domains and on an IPv4 address outside the ranges
kept for documentation (RFC 5737: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24; 198.18.0.0/15 for
the masked addresses), loopback and link-local.

  python tests/check_public.py            the repo's files (git ls-files; vendor/ is checked upstream)
  python tests/check_public.py DIR        every file below DIR (e.g. an unpacked release zip)

Words of your own site that must never be published (a site code, a ticket title, a team name) go in a
file outside the repo, one per line: ~/.config/site-automation/private-words (or SA_PRIVATE_WORDS=path).
They are looked for too, in any case; that file is never read in CI.

A line that has to show something else (a version number laid out like an address) ends with the marker
public-ok in a comment.
"""
import ipaddress
import os
import re
import subprocess
import sys

# Placeholder domains: a name is allowed when it is one of these or ends with "." + one of these.
DOMAINS = (
    "example", "test", "invalid", "localhost",                      # RFC 2606 / 6761
    "example.mil", "example.com", "example.org", "example.net",
    "yoursite.mil", "yourdomain.mil", "yourinstance.servicenowservices.com",
    "vsphere.local",                                                # vCenter's own SSO domain
    "corp",                                                         # the tests' Active Directory domain
    # public services and vendors the docs and code point to
    "genai.mil", "genai.army.mil", "github.com", "githubusercontent.com", "docker.io", "quay.io",
    "redhat.io", "redhat.com", "ansible.com", "mariadb.com", "mariadb.org", "netapp.com", "vmware.com",
    "broadcom.com", "microsoft.com", "adaptivecards.io", "python.org", "pypi.org", "pythonhosted.org", "servicenow.com", "w3.org",
    "nist.gov", "cyber.mil",                                        # XCCDF's namespace; DISA's STIG references (the STIG drift report)
)
# Dotted words of code, not host names (System.Net, $vm.guest.net ...).
CODE = ("system.io", "system.net", "guest.net", "g.net")
TLDS = "mil|gov|com|net|org|edu|us|io|local|lan|corp|internal|int|cloud|intra|priv|ai|dev|app"
NAME = re.compile(r"(?<![\w.-])((?:[a-z0-9][a-z0-9_-]*\.)+(?:%s))(?![\w-])" % TLDS, re.I)
IPV4 = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w.]|\.\d)")
NETS = [ipaddress.ip_network(n) for n in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "198.18.0.0/15",
    "127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/32", "255.0.0.0/8", "240.0.0.0/4",
    "2.5.29.0/24")]                                                 # 2.5.29.17: an X.509 extension OID, not an address
SKIP_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".gz", ".whl", ".pyc")


def domain_ok(name):
    n = name.lower().rstrip(".")
    return any(n == d or n.endswith("." + d) for d in DOMAINS + CODE)


def ip_ok(addr):
    try:
        a = ipaddress.ip_address(addr)
    except ValueError:
        return True                                                 # 1.400.1.0: not an address
    return any(a in n for n in NETS)


def private_words():
    p = os.environ.get("SA_PRIVATE_WORDS") or os.path.expanduser("~/.config/site-automation/private-words")
    try:
        with open(p, encoding="utf-8") as f:
            return [w.strip() for w in f if w.strip() and not w.lstrip().startswith("#")]
    except OSError:
        return []


def files(root):
    if root:
        for d, _, fs in os.walk(root):
            for f in fs:
                yield os.path.join(d, f)
        return
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=REPO).decode().split("\0")
    for f in out:
        if f and not f.startswith("vendor/") and f != "tests/check_public.py":
            yield os.path.join(REPO, f)


def check(path, words):
    if path.lower().endswith(SKIP_EXT):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except (UnicodeDecodeError, OSError):
        return []
    bad = []
    for no, line in enumerate(lines, 1):
        if "public-ok" in line:
            continue
        low = line.lower()
        bad += ["%s:%d: host name %s (use a placeholder: example.mil, yoursite.mil)" % (path, no, m)
                for m in NAME.findall(line) if not domain_ok(m)]
        bad += ["%s:%d: IP address %s (use 192.0.2.x, 198.51.100.x or 203.0.113.x)" % (path, no, m)
                for m in IPV4.findall(line) if not ip_ok(m)]
        bad += ["%s:%d: a word of your site (private-words)" % (path, no) for w in words if w.lower() in low]
    return bad


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else None
    words = private_words()
    bad = [b for p in files(root) for b in check(p, words)]
    rel = (lambda s: os.path.relpath(s.split(":", 1)[0], root or REPO) + ":" + s.split(":", 1)[1])
    for b in bad:
        print("FAIL - " + rel(b))
    print(("%d found" % len(bad)) if bad else "ok   - no real host names, IP addresses or site words%s"
          % (" (and %d private words)" % len(words) if words else ""))
    sys.exit(1 if bad else 0)
