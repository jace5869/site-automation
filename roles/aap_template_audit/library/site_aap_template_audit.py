#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read every job template and workflow template through AAP's own API and say which settings in their Variables
box should not be there (playbooks/aap_template_audit.yml): connection and infrastructure settings, site-wide ones,
one set the same way in many templates, and secrets. Read-only (GET). The address and login come from the "Red Hat
Ansible Automation Platform" credential's variables (plugins/module_utils/site_aap.py). Standard library and PyYAML
(which ansible-core brings) only. The values never leave this module: a secret comes back as its name, a repeated
setting's value only when it is not a secret, not named like one and holds no list or dict."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_aap_template_audit
short_description: Which settings in AAP's job and workflow templates' Variables should be somewhere else
description:
  - Reads every job template and workflow template the AAP user can see (O(project) and O(exclude) narrow it), each
    one's Variables (extra_vars, YAML or JSON) and, where its survey is on, the survey's questions. GET only.
  - "Flags each setting that is: connection (an C(ansible_*) setting); an address (a host name, a URL or an IP address
    in its value); site-wide (in O(site_wide): the settings usually in all.yml); repeated (the same value in at least
    O(repeat_min) templates); a secret (a word of its name - password, pwd, secret, token, api key, pin, or a word ending
    in one: PGPASSWORD -, a credential's setting whose value is no credential name, or its value: a long random string,
    a URL with a password, --password X, an Authorization header, a PEM private key, a key named like a secret in a text
    holding JSON or YAML). It hides on suspicion."
  - "A secret is never returned: only its name. A repeated setting's value is returned (cut) only when it is no secret,
    is not named like one, and is not a list or a dict or a text holding one (those come back as '(same; not shown)')."
  - "A template that asks for its Variables on launch, or whose survey asks for a flagged setting: a launch can set
    these settings again."
  - AAP's address and login come from the environment, as the "Red Hat Ansible Automation Platform" credential sets
    it. Its user needs read access to the templates (Organization Auditor is enough).
options:
  site_wide: {description: The settings usually in all.yml (every job reads them)., type: list, elements: str, default: []}
  per_enclave: {description: "The settings that differ by enclave (all.yml of each enclave - its own AAP, docs/ENCLAVES.md).", type: list, elements: str, default: []}
  per_template: {description: Settings that belong in a template (who gets its email, its target) - never flagged as site-wide or as an address., type: list, elements: str, default: []}
  ignore: {description: Settings never flagged., type: list, elements: str, default: []}
  repeat_min: {description: A setting with the same value in this many templates or more is flagged (0 = never)., type: int, default: 3}
  project: {description: Only the job templates of this AAP project (its name); workflows are all read. Empty = every one., type: str, default: ''}
  exclude: {description: Templates left out, by name or ID., type: list, elements: str, default: []}
  workflows: {description: Read the workflow templates too., type: bool, default: true}
  ca_path: {description: A CA file for AAP's HTTPS certificate ("" = what the execution environment trusts)., type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 30}
author: site automation
'''

RETURN = r'''
api: {description: The API path that answered., type: str, sample: api/controller/v2/}
problems: {description: What could not be read (AAP, a list, a survey)., type: list}
templates:
  description: "Each template read: kind (job or workflow), id, name, project, settings (how many), ask_variables (asks
    for its Variables on launch), survey (the variables its survey asks for), error (its Variables could not be read),
    flags: [{setting, why: [connection, address, site_wide, per_enclave, repeated, secret, launch], repeats, suggestion, note}]."
  type: list
settings:
  description: "Each flagged setting once: setting, why, templates (how many have it), same (how many with the same value),
    value (cut; '' for a secret, or when the values differ; '(same; not shown)' for a setting named like a secret or a
    list or dict), suggestion."
  type: list
'''

import hashlib
import json
import math
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection, error_text

try:
    import yaml
except ImportError:                                  # pragma: no cover - ansible-core brings PyYAML
    yaml = None

NO_CREDENTIAL = ('No AAP connection: attach a credential of type "Red Hat Ansible Automation Platform" to this job template '
                 '(its AAP URL, and the user name and password - or a token - of an AAP user that can read the job and '
                 'workflow templates: Organization Auditor is enough)')

# a name's words: split at _ - . (any other sign too), where camelCase turns and between letters and digits
# (adminPwd, client-secret, db.password, APIKey, secret1)
NAME_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
# a setting named like a secret: one of these words, whole (bypass, compass, keyboard and spin are not) ...
SECRET_WORDS = frozenset(("password", "passwords", "passwd", "pwd", "pass", "pw", "secret", "secrets", "token", "tokens",
                          "apikey", "apikeys", "passphrase", "pin", "privatekey", "secretkey", "accesskey", "community",
                          "communities"))
# ... or a word that ends in a long one, joined with no sign (PGPASSWORD, dbpassword, clientsecret, apitoken) ...
SECRET_ENDS = ("password", "passwords", "passwd", "pwd", "passphrase", "secret", "secrets", "token", "tokens", "apikey",
               "apikeys", "privatekey", "secretkey", "accesskey", "community")
# ... or in pw, 4 letters or more (rootpw in a kickstart, bindpw for LDAP) ...
# ... or in pass, 6 letters or more (adminpass, dbpass, rootpass) - but for these words of their own
PASS_WORDS = frozenset(("bypass", "compass", "encompass", "overpass", "underpass", "surpass", "trespass", "multipass",
                        "firstpass", "onepass", "singlepass", "twopass", "flypass"))
# ... credential(s) only when its value looks like a secret: the repo's own name a credential (tenable_scan_credentials,
# incident_docs_draft_credential: a credential's name, a list of names or ids)
CREDENTIAL_WORDS = frozenset(("credential", "credentials", "creds", "cred"))
# ... or two of them in a row (api_key, private-key, aws_access_key, key_material)
SECRET_PAIRS = frozenset((("api", "key"), ("private", "key"), ("secret", "key"), ("access", "key"), ("key", "material")))
# a name like these is about a secret, not one: where it is, what it is called, a rule for it (its words, joined by _)
# (and a limit's: max_tokens, min_password)
NOT_SECRET_NAME = re.compile(r"^(max|min|num)_|_(file|path|dir|name|names|id|ids|url|host|user|username|type|length|len|days|age|"
                             r"expires?|expiry|policy|rotation|field|header|check|checks|warn|crit|min|max|"
                             r"count|counts|version|ver|total|limit|limits|size|retries|attempts|tries)$", re.I)
# a URL with a password in it (mysql://svc:<password>@db01)
URL_PASSWORD = re.compile(r"[a-z][a-z0-9+.-]{0,31}://[^\s/?#@:]*:[^\s/?#]+@", re.I)
# name=value or name: value in a text (Server=db01;Uid=svc;Pwd=...), the name in quotes or not
TEXT_SETTING = re.compile(r"""([A-Za-z][\w.-]{0,63})["']?\s*[:=]\s*["']?[^\s"',;]""")
# ... read from each = or : back to its name - the last 64 characters of it, from its first letter there - so a long
# text is read once (text_names)
SETTING_SEP = re.compile(r"[:=]")
SETTING_VALUE = re.compile(r"""\s*["']?[^\s"',;]""")
NAME_END = re.compile(r"[A-Za-z][\w.-]{0,63}$")
# a command line's option and its value after a space (--password X, -p X)
CLI_OPTION = re.compile(r"(?:^|\s)(--?)([A-Za-z][\w-]*)(?:\s+|=)([\"']?)([^\s\"']+)")
# -p with a value joined to it (tool -pS3cret!) - a password when it has a digit or a sign in it, but not a port
# (-p8080:80), a variable (-p$SSH_PORT) or one of these single-dash options (find -print0, openssl -pubout)
GLUED_P = re.compile(r"(?:^|\s)-p([^\s\d:/$][^\s]*)")
P_OPTIONS = frozenset(("print", "print0", "printf", "prune", "perm", "pubout", "pthread", "progress", "pattern"))
# a command's own password option, only after that command (mysql -p2024x, sqlcmd -P X, ldapsearch -w X, smbclient
# -U user%X, ipmitool -P X, redis-cli -a X, winrs -p:X, sshpass -p X, curl -u user:X): (option, the value's kind) -
# any (the value), colon (user:password), percent (user%password); the value after a space or joined to it. Windows
# tools (loose: True) take a switch with / or - in any case (winrs /p:X, sqlcmd /P X): their options in lower case
TOOL_OPTIONS = (
    (re.compile(r"^(?:mysql\w*|mariadb(?:-\w+)?)$"), (("-p", "any"), ("--password", "any")), False),
    (re.compile(r"^(?:curl|wget)$"), (("-u", "colon"), ("--user", "colon"), ("--proxy-user", "colon")), False),
    (re.compile(r"^sqlcmd$"), (("-p", "not1"),), True),
    (re.compile(r"^ldap\w+$"), (("-w", "any"),), False),
    (re.compile(r"^(?:smbclient|smbcacls|smbget|rpcclient)$"), (("-U", "percent"), ("--user", "percent")), False),
    (re.compile(r"^net$"), (("-u", "percent"), ("--user", "percent")), True),
    (re.compile(r"^ipmitool$"), (("-P", "any"),), False),
    (re.compile(r"^redis-cli$"), (("-a", "any"),), False),
    (re.compile(r"^winrs$"), (("-p:", "any"), ("-password:", "any")), True),
    (re.compile(r"^sshpass$"), (("-p", "any"),), False),
)
# a script's words as a shell splits them (1.24.0g): ; | & ( ) ` $( and a new line are signs that end a command
# (N=$(mysql ...), cd /tmp;mysql ..., echo x|mysql ...); a quoted string ('...' or "...", glued too: -p"a b") is one
# word of the command around it, whatever signs it holds (mysql -e "select count(*)" -pX), and is read again as a
# command line of its own (ssh db01 'mysql ...', bash -c '...')
SIGNS = set(";|&()`")
TOOL_HINT = re.compile(r"mysql|mariadb|curl|wget|sqlcmd|ldap|smb|rpcclient|net|ipmitool|redis-cli|winrs|sshpass")
# its tokens: a '...' string, a "..." string (\\" inside it no end), a backslash and the character after it, spaces, a new
# line, a sign, and a run of other characters - each matched once, so a long text is read in one pass
SHELL_TOKEN = re.compile(r"""(?P<sq>'(?P<sqb>[^']*)(?P<sqe>'?))|(?P<dq>"(?P<dqb>(?:[^"\\]|\\.)*)(?P<dqe>"?))|(?P<esc>\\.)"""
                         r"""|(?P<sp>[^\S\r\n]+)|(?P<nl>\r\n?|\n)|(?P<sign>\$\(|[;|&()`])|(?P<plain>[^\s;|&()`'"\\$]+|[$\\])""", re.S)
DQ_ESCAPE = re.compile(r"""\\([\\"$`])""")
OUT_ESCAPE = set(" \t\"'\\$`;|&()<>")
# a value that is an HTTP Authorization header's credentials (Basic <base64>, Bearer <token>), alone or after its name
AUTH_VALUE = re.compile(r"(?:^|authorization\"?\s*[:=]\s*\"?)(?:basic|bearer|token|negotiate|digest)\s+[A-Za-z0-9+/=._~-]{8,}", re.I)
# a PEM private key (RSA, EC, OPENSSH, ENCRYPTED ...)
PEM_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]{0,80}PRIVATE KEY(?: BLOCK)?-----")
# a text that may be YAML without a colon (1.29.0): a list item ("- ", "-" and a tab or a new line), the document
# marker "---" alone or with a comment after it, a comment line, a %YAML directive, [ or { - not -----BEGIN or -a
YAML_START = re.compile(r"-(?:\s|$)|---(?:\s|$)|[\[{#%]")
LIST_TAB = re.compile(r"(?m)^([ ]*)-\t")
# a sentence of plain words (Change the password: see the runbook) - prose, not a name=value text
PROSE_WORD = re.compile(r"""^["'(]?[A-Za-z]+(?:'[a-z]+)?[.,:;!?)"']*$""")
URL = re.compile(r"^[a-z][a-z0-9+.-]*://[^\s/]+", re.I)
IPV4 = re.compile(r"^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:/\d{1,2})?(?::\d+)?$")
IPV6 = re.compile(r"^\[?(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{0,4}\]?(?:/\d{1,3})?$", re.I)
# a host name: two labels or more, the last letters only (aap01.yoursite.mil); a file name (report.html.j2) is not one
FQDN = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}\.?(?::\d+)?$", re.I)
FILE_EXT = re.compile(r"\.(ya?ml|j2|json|html?|txt|csv|xlsx?|docx?|pem|crt|cer|der|key|pfx|p12|log|py|ps1|psm1|sh|conf|cfg|ini|xml|zip|gz|tgz|tar|md|pdf|png|jpe?g|gif|svg|ico|css|js|repo|rpm|iso|ova|ovf|vmdk)$", re.I)

WHY = ("connection", "address", "secret", "site_wide", "per_enclave", "repeated", "launch")
CUT = 40


def parse_vars(text):
    """A template's Variables box: (dict, why not). YAML or JSON; empty = {}."""
    if isinstance(text, dict):
        return text, ""
    t = str(text or "").strip()
    if not t or t in ("---", "{}"):
        return {}, ""
    try:
        d = json.loads(t)
    except ValueError:
        if yaml is None:
            return {}, "not JSON, and PyYAML is not here to read YAML"
        try:
            d = yaml.safe_load(t)
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            return {}, "not YAML or JSON%s" % (" (line %d)" % (mark.line + 1) if mark is not None else "")
    if d is None:
        return {}, ""
    if not isinstance(d, dict):
        return {}, "not a list of settings (name: value) but a %s" % type(d).__name__
    return d, ""


def _value(v):
    """A text that holds JSON or YAML - a dict or a list - is that dict or list; any other value (a sentence of plain
    words too: "Change the password: see the runbook" is no setting) is itself. Pure."""
    if not isinstance(v, str):
        return v
    t, d = v.strip(), None
    if not t:
        return v
    if t[0] in "[{":
        try:
            d = json.loads(t)
        except Exception:                            # not JSON (or too deep): YAML may read it
            d = None
    if d is None and yaml is not None and (":" in t or YAML_START.match(t)) and not prose(t):   # a sentence with a colon: a text
        try:
            d = yaml.safe_load(t)
        except Exception:                            # not YAML either: it stays a text
            d = None
        if d is None and LIST_TAB.search(t):         # "-<tab>item": YAML takes no tab there, a person may write one
            try:
                d = yaml.safe_load(LIST_TAB.sub(r"\1- ", t))
            except Exception:
                d = None
    return d if isinstance(d, (dict, list)) else v


def _strings(v, depth=0):
    """Every text inside a value (lists and dicts, a few levels down; a text holding JSON or YAML is read)."""
    if depth > 6:
        return
    v = _value(v)
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for k, x in v.items():
            for s in _strings(x, depth + 1):
                yield s
    elif isinstance(v, (list, tuple)):
        for x in v:
            for s in _strings(x, depth + 1):
                yield s


def is_address(s):
    """A host name, a URL or an IP address (with a port or a prefix length). Pure."""
    s = str(s or "").strip()
    if not s or " " in s or "{{" in s:
        return False
    if URL.match(s):
        return True
    if "@" in s:                                     # an email address names a person, not a host
        return False
    if IPV4.match(s) or (s.count(":") >= 2 and IPV6.match(s)):
        return True
    return bool(FQDN.match(s)) and not FILE_EXT.search(s.split(":")[0])


def _entropy(s):
    n = float(len(s))
    return -sum(c / n * math.log(c / n, 2) for c in (s.count(ch) for ch in set(s)))


def looks_random(s):
    """A long random string - a password, a token, a key - not a path, an address or a sentence. Pure."""
    s = str(s or "").strip()
    if len(s) < 20 or re.search(r"\s", s) or "{{" in s or s.startswith("$ANSIBLE_VAULT"):
        return False
    if s.startswith(("/", "./", "~", "\\\\")) or re.match(r"^[A-Za-z]:\\", s) or is_address(s) or "@" in s:
        return False
    if re.match(r"^[a-z0-9_.-]+$", s) and ("_" in s or "-" in s) and not re.search(r"\d", s):
        return False                                 # a long name (some_setting_name, my-long-hostname-group)
    classes = sum(bool(re.search(p, s)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    return classes >= 3 and _entropy(s) >= 3.5


def name_words(name):
    """A name's words, in lower case: adminPwd -> admin, pwd; API-KEY -> api, key; secret1 -> secret, 1. Pure."""
    return [w.lower() for w in NAME_WORDS.findall(str(name or ""))]


def _secret_word(w):
    return w in SECRET_WORDS or w.endswith(SECRET_ENDS) or (len(w) >= 4 and w.endswith("pw")) or (
        len(w) >= 6 and w.endswith("pass") and w not in PASS_WORDS)


def secret_name(name):
    """A setting named like a secret (db_password, adminPwd, client-secret, API-KEY, secret1, PGPASSWORD, adminpass): a
    whole word of its name, or a word ending in a long one - never a short one inside a word (bypass, keyboard, spin) -
    and not one that names where it is or counts it (vcenter_password_file, pin_version, pass_count). Pure."""
    w = name_words(name)
    if not w or NOT_SECRET_NAME.search("_".join(w)):
        return False
    return any(_secret_word(x) for x in w) or any((a, "key" if b == "keys" else b) in SECRET_PAIRS for a, b in zip(w, w[1:]))


def credential_name(name):
    """A setting about a credential (scan_credentials, ldap_cred) - a secret only when its value looks like one. Pure."""
    w = name_words(name)
    return bool(w) and not NOT_SECRET_NAME.search("_".join(w)) and any(x in CREDENTIAL_WORDS for x in w)


def names_only(v, depth=0, listed=False):
    """A credential's name as AAP shows it, its id, or a list of them (AAP - SOP draft, Tenable scan (Linux), 12, [12, 15]):
    two words or more with spaces, or a number; in a list also words joined by - _ . with no digit (["ssh-linux"]). A
    single word on its own (tenable-linux-ssh, Summer2024) may be a password: not a name - when in doubt, hidden. Pure."""
    if depth > 6:
        return False
    v = _value(v)
    if isinstance(v, bool) or v is None:
        return True
    if isinstance(v, int):
        return True
    if isinstance(v, (list, tuple)):
        return all(names_only(x, depth + 1, True) for x in v)
    if not isinstance(v, str):
        return False
    s = v.strip()
    if re.match(r"^\d{1,12}$", s) or (listed and re.match(r"^[A-Za-z]+(?:[_.-][A-Za-z]+)+$", s)):
        return True
    words = s.split()
    return 2 <= len(words) and len(s) <= 128 and bool(re.match(r"^[\w ()\[\].,:/&'-]+$", s)) and not any(
        looks_random(x) for x in words)


def prose(s):
    """A sentence of plain words, four or more (Change the password: see the runbook). Pure."""
    words = str(s or "").split()
    return len(words) >= 4 and all(PROSE_WORD.match(x) for x in words)


def _cli_secret(dash, opt, value):
    """--password X: an option named like a secret, its value after a space or =; -p X too (a password for mysql and
    others), but not a number, a port mapping or a path (ssh -p 2222, podman -p 8080:80, rsync -p /src)."""
    if value.startswith("-"):
        return False
    if dash == "-" and opt == "p":
        return not re.match(r"^[\d:./~]+$", value) and not value.startswith(("/", "./", "~", "$"))
    return secret_name(opt)


def secret_text(s):
    """A text with a secret written in it: a URL with a password (mysql://svc:...@db01), a PEM private key, an HTTP
    Authorization header's credentials (Basic ..., Bearer ...), --password X, -p X or -pX, a command's own password
    option (TOOL_OPTIONS), or name=value / name: value under a
    secret's name (Uid=svc;Pwd=..., PGPASSWORD=...). A sentence of plain words is prose: only a URL or a key counts
    there. Pure."""
    s = str(s or "")
    if ("://" in s and URL_PASSWORD.search(s)) or ("-----BEGIN" in s and PEM_KEY.search(s)):
        return True
    if prose(s):
        return False
    return bool(AUTH_VALUE.search(s.strip())) or any(_cli_secret(*m[:2], m[3]) for m in CLI_OPTION.findall(s)) or any(
        _glued_p(g) for g in GLUED_P.findall(s)) or _tool_secret(s) or any(secret_name(k) for k in set(text_names(s)))


def text_names(s):
    """The names of a text's name=value and name: value (TEXT_SETTING), each read back from its = or :. Linear. Pure."""
    for m in SETTING_SEP.finditer(s):
        j = m.start()
        while j > 0 and s[j - 1].isspace():               # each run of spaces walked once: by the sign after it
            j -= 1
        j -= 1 if j > 0 and s[j - 1] in "\"'" else 0
        if j == 0 or not (s[j - 1].isalnum() or s[j - 1] in "_.-") or not SETTING_VALUE.match(s, m.end()):
            continue
        name = NAME_END.search(s, max(0, j - 64), j)
        if name:
            yield name.group()


def _glued_p(glued):
    """-pXXXX after any command: a password when it has a digit or a sign in it (tool -pS3cret) - letters only are flags
    (cp -pR, rsync -prltDvz, netstat -plantu), and -print0, -perm ... options of their own."""
    return "p" + glued not in P_OPTIONS and bool(re.search(r"[^A-Za-z]", glued))


def _tool_value(kind, value):
    if not value or value.startswith("-"):
        return False
    if kind == "not1":                                   # sqlcmd -p1: statistics in colon format, -P X the password
        return value != "1"
    if kind == "colon":
        return bool(re.match(r"^[^:]+:.", value))
    if kind == "percent":
        return bool(re.match(r"^[^%]+%.", value))
    return True


def shell_words(s):
    """(words, quoted): a text's words as a shell splits them - each sign (; | & ( ) ` $( and a new line, as ;) a word of
    its own; a quoted string part of the word it is in (its quotes off), however many signs it holds; an unclosed one
    runs to the end; a \\ at a line's end goes on - and the quoted strings' insides, each to read as a command line of
    its own (with an unclosed quote, the whole text without it as well: a typo hides nothing). A backslash as POSIX has
    it (1.24.0h): inside "..." it makes " \\ $ ` plain (ssh h "mysql -e \\"select 1\\" -pX"), the inside handed on
    without it; inside '...' nothing is special; outside quotes it makes a shell's sign plain - before any other
    character it stays (C:\\tools\\sqlcmd.exe). Linear. Pure."""
    t = re.sub(r"\\\r?\n", " ", str(s or ""))
    words, quoted, cur = [], [], None
    for m in SHELL_TOKEN.finditer(t):
        k = m.lastgroup
        if k in ("sp", "nl", "sign"):
            if cur is not None:
                words.append("".join(cur))
            cur = None
            if k != "sp":
                words.append(";" if k == "nl" else m.group())
            continue
        if k == "sq" or k == "dq":
            inner = m.group(k + "b") if k == "sq" else DQ_ESCAPE.sub(r"\1", m.group("dqb"))
            quoted.append(inner)
            if not m.group(k + "e"):                     # unclosed: to the end - the text read again without it too
                quoted.append(t[:m.start()] + t[m.start() + 1:])
            piece = inner
        elif k == "esc":
            piece = m.group()[1] if m.group()[1] in OUT_ESCAPE else m.group()
        else:
            piece = m.group()
        cur = [] if cur is None else cur
        cur.append(piece)
    if cur is not None:
        words.append("".join(cur))
    return words, quoted


def _tool_secret(s, depth=0):
    """A command's own password option, after that command word, up to the end of its command (TOOL_OPTIONS): mysql
    -pXXXX (any value - mysql's port is -P), curl -u svc:XXXX or -usvc:XXXX, smbclient -U svc%XXXX, sqlcmd -P XXXX ...
    The command word anywhere a shell starts one: N=$(mysql ...), cd /tmp;mysql ..., and inside a quoted string read as
    a command line of its own (ssh db01 'mysql ...'). Pure."""
    if not TOOL_HINT.search(re.sub(r"""["'\\\\]""", "", s).lower()):      # none of the tools named: nothing to split
        return False
    words, quoted = shell_words(s)
    options, loose = (), False
    for i, w in enumerate(words):
        if w in SIGNS or w == "$(" or w == ";":
            options = ()
            continue
        name = w.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        name = name[:-4] if name.endswith(".exe") else name
        found = next(((o, lz) for rx, o, lz in TOOL_OPTIONS if rx.match(name)), None)
        if found is not None:
            options, loose = found
            continue
        key = (("-" + w[1:]) if w[:1] in "/-" else w).lower() if loose else w
        for opt, kind in options:
            if key == opt or (key.startswith(opt + "=") and opt.startswith("--")):
                nxt = words[i + 1] if i + 1 < len(words) else ""
                value = ("" if nxt in SIGNS or nxt in ("$(", ";") else nxt) if key == opt else w[len(opt) + 1:]
            elif key.startswith(opt) and not opt.startswith("--") and len(key) > len(opt):
                value = w[len(opt):]
            else:
                continue
            if _tool_value(kind, value):
                return True
    return depth < 3 and any(_tool_secret(q, depth + 1) for q in quoted if q.strip())


def _named_secret(name, value):
    """Named like a secret, with a value (an empty one is no secret; a number counts) - named for a credential, with a
    value that is not just a credential's name or id."""
    return _filled(value) and (secret_name(name) or (credential_name(name) and not names_only(value)))


def _keys(v, depth=0):
    """Every (key, value) of the dicts inside a value (lists and dicts, a few levels down; a text holding JSON or YAML
    is read)."""
    if depth > 6:
        return
    v = _value(v)
    if isinstance(v, dict):
        for k, x in v.items():
            yield k, x
            for kx in _keys(x, depth + 1):
                yield kx
    elif isinstance(v, (list, tuple)):
        for x in v:
            for kx in _keys(x, depth + 1):
                yield kx


def _filled(v, depth=0):
    """A value with something in it: a text that is not empty, or a number (true/false is a switch, not a secret)."""
    if depth > 6:
        return False
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, dict):
        return any(_filled(x, depth + 1) for x in v.values())
    if isinstance(v, (list, tuple)):
        return any(_filled(x, depth + 1) for x in v)
    return bool(str(v).strip())


def is_secret(name, value):
    """A secret: named like one (with a value - an empty one is no secret; a number counts), a key named like one
    anywhere inside it (stigman_db: {password: ...}, or a text holding JSON or YAML: '{"password": ...}'), a text with
    a secret written in it (a URL with a password: the whole value is the secret), or a value that is a long random
    string. A vault-encrypted value is a secret too: it is fine in a vars file, not in a template's Variables. Pure."""
    if _named_secret(name, value):
        return True
    if any(_named_secret(k, x) for k, x in _keys(value)):
        return True
    return any(looks_random(s) or secret_text(s) or str(s).lstrip().startswith("$ANSIBLE_VAULT") for s in _strings(value))


def hidden(name, value):
    """A value the report never shows, secret or not: one named like a secret (even true/false), and a list or a dict
    (a key inside may be one), a text holding one as JSON or YAML too. Pure."""
    return secret_name(name) or (credential_name(name) and not names_only(value)) or isinstance(_value(value), (dict, list, tuple))


def connection_suggestion(name):
    """Where an ansible_* setting goes. Pure."""
    n = str(name).lower()
    if n.startswith(("ansible_winrm_", "ansible_psrp_")) or n in ("ansible_shell_type", "ansible_shell_executable"):
        return "the windows group (group_vars/windows.yml)"
    if n in ("ansible_user", "ansible_ssh_user", "ansible_password", "ansible_ssh_pass", "ansible_become_user",
             "ansible_become_password", "ansible_become_pass", "ansible_become_method", "ansible_ssh_private_key_file"):
        return "the Machine credential"
    return "the group's or host's variables"


ENCLAVE = "all.yml of each enclave (its own AAP, docs/ENCLAVES.md)"
ALL_YML = "all.yml"
SECRET = "a credential (docs/SECRETS.md)"


def classify(name, value, site_wide=(), per_enclave=(), per_template=()):
    """(why codes, suggestion) for one setting, before repeats and launch are known. Pure."""
    why, n = [], str(name)
    if n.startswith("ansible_"):
        why.append("connection")
    elif n not in per_template and any(is_address(s) for s in _strings(value)):
        why.append("address")
    if is_secret(n, value):
        why.append("secret")
    if n in site_wide and n not in per_template:
        why.append("site_wide")
    if n in per_enclave and "address" not in why:
        why.append("per_enclave")
    return why, suggest(n, why)


def suggest(name, why):
    """The place a flagged setting belongs. Pure."""
    if "secret" in why:
        machine = connection_suggestion(name)
        return machine if name.startswith("ansible_") and "Machine" in machine else SECRET
    if "connection" in why:
        return connection_suggestion(name)
    if "address" in why or "per_enclave" in why:
        return ENCLAVE
    if "site_wide" in why or "repeated" in why:
        return ALL_YML
    if "launch" in why:
        return "ask at launch only what a launch must choose"
    return ""


def _canon(v):
    """A value as plain JSON that keeps each type (1.47.0): the key 443 and the key "443" differ, 1 and true and "1"
    differ, and a mapping's order does not count - its keys of any type sorted by their own canonical form."""
    if isinstance(v, dict):
        items = [(json.dumps(_canon(k), separators=(",", ":")), _canon(x)) for k, x in v.items()]
        return ["d", [[k, x] for k, x in sorted(items, key=lambda kx: kx[0])]]
    if isinstance(v, (list, tuple)):
        return ["l", [_canon(x) for x in v]]
    if isinstance(v, bool):
        return ["b", v]
    if isinstance(v, int):
        return ["i", str(v)]
    if isinstance(v, float):
        return ["f", repr(v)]
    if v is None:
        return ["n"]
    if isinstance(v, str):
        return ["s", v]
    return ["o", type(v).__name__, str(v)]


def fingerprint(value):
    """Compares two values without keeping them: the same for the same value, whatever a mapping's order or its keys'
    types (1.47.0: '443: x' next to 'default: y' was a TypeError)."""
    return hashlib.sha256(json.dumps(_canon(value), separators=(",", ":")).encode()).hexdigest()


def _text(v):
    """A value as JSON text for the report; a mapping's keys of mixed types in a fixed order (1.47.0)."""
    if isinstance(v, dict):
        items = sorted(v.items(), key=lambda kx: (str(kx[0]), type(kx[0]).__name__))
        return "{" + ", ".join("%s: %s" % (json.dumps(k) if isinstance(k, str) else _text(k), _text(x)) for k, x in items) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_text(x) for x in v) + "]"
    return json.dumps(v, default=str)


def shown(value):
    """A value as the report shows it, cut. Never called for a secret, or for a value hidden() keeps out."""
    s = value if isinstance(value, str) else _text(value)
    s = " ".join(s.split())
    return s if len(s) <= CUT else s[:CUT - 3] + "..."


def audit(records, opts):
    """records: [{kind, id, name, project, extra_vars, ask_variables, survey: [{variable, type}]}] ->
    (templates, settings). Pure: the whole judgement, testable without AAP."""
    sw, pe = set(opts.get("site_wide") or ()), set(opts.get("per_enclave") or ())
    pt, ign = set(opts.get("per_template") or ()), set(opts.get("ignore") or ())
    repeat_min = int(opts.get("repeat_min") or 0)
    templates, seen = [], {}                       # seen: setting -> {fingerprint: [template index]}
    secrets = set()
    for rec in records:
        d, err = parse_vars(rec.get("extra_vars"))
        t = {"kind": rec.get("kind", "job"), "id": rec.get("id"), "name": rec.get("name") or "", "project": rec.get("project") or "",
             "settings": len(d), "ask_variables": bool(rec.get("ask_variables")),
             "survey": {str(q.get("variable")): str(q.get("type") or "") for q in rec.get("survey") or [] if q.get("variable")},
             "error": err, "flags": []}
        for name in sorted(d, key=lambda k: (str(k), type(k).__name__)):
            n, v = str(name), d[name]
            if n in ign:
                continue
            try:
                why, sug = classify(n, v, sw, pe, pt)
                one = None if "secret" in why else (fingerprint(v), None if hidden(n, v) else shown(v))
            except Exception as e:                  # a value this audit cannot weigh: said for its template, the rest go on
                t["error"] = "; ".join(x for x in (t["error"], "%s could not be checked (%s)" % (n, type(e).__name__)) if x)
                continue
            if one is None:
                secrets.add(n)
            else:
                seen.setdefault(n, {}).setdefault(one[0], []).append((len(templates), one[1]))
            if why:
                t["flags"].append({"setting": n, "why": why, "suggestion": sug, "note": ""})
        templates.append(t)
    # repeats: the same value in repeat_min templates or more (a secret's are not compared: it is flagged already)
    repeats = {}
    for n, by_value in seen.items():
        for fp, where in by_value.items():
            if repeat_min and len(where) >= repeat_min:
                repeats[(n, fp)] = where
                for i, _ in where:
                    t = templates[i]
                    f = next((x for x in t["flags"] if x["setting"] == n), None)
                    if f is None:
                        f = {"setting": n, "why": [], "suggestion": "", "note": ""}
                        t["flags"].append(f)
                    f["why"].append("repeated")
                    f["repeats"] = len(where)
                    others = [templates[j]["name"] for j, _ in where if j != i]
                    f["note"] = "also in " + ", ".join(others[:4]) + (" and %d more" % (len(others) - 4) if len(others) > 4 else "")
                    f["suggestion"] = suggest(n, f["why"])
    # a launch can set them again: Variables asked on launch (any setting), or a survey question for a flagged one
    for t in templates:
        flagged = {f["setting"] for f in t["flags"]}
        for q, qtype in sorted(t["survey"].items()):
            if q in ign or q in pt:
                continue
            why, _ = classify(q, None, sw, pe, pt)
            # a Password question is AAP's own way to ask for a secret (kept encrypted); a text one shows it
            plain = secret_name(q) and qtype != "password"
            if q in flagged or why or plain:
                f = next((x for x in t["flags"] if x["setting"] == q), None)
                if f is None:
                    f = {"setting": q, "why": list(why), "suggestion": "", "note": ""}
                    t["flags"].append(f)
                f["why"] += ["launch"] + (["secret"] if plain else [])
                f["note"] = "; ".join(x for x in (f["note"], "its survey asks for it as plain text (%s, not Password)" % (qtype or "text")
                                                  if plain else "its survey asks for it on every launch") if x)
                f["suggestion"] = "make the survey question a Password question" if plain else suggest(q, f["why"])
        t["survey"] = sorted(t["survey"])
        if t["ask_variables"] and t["flags"]:
            t["flags"].append({"setting": "(Variables on launch)", "why": ["launch"], "suggestion": suggest("", ["launch"]),
                               "note": "Prompt on launch is on for Variables: a launch can set any of the settings above, and any other"})
        for f in t["flags"]:
            f["why"] = [w for w in WHY if w in f["why"]]
        t["flags"].sort(key=lambda f: (f["setting"].startswith("("), min(WHY.index(w) for w in f["why"]) if f["why"] else 9, f["setting"]))
    # each flagged setting once
    settings = {}
    for t in templates:
        for f in t["flags"]:
            if f["setting"].startswith("("):
                continue
            s = settings.setdefault(f["setting"], {"setting": f["setting"], "why": [], "templates": 0, "same": 0, "value": "",
                                                   "suggestion": ""})
            s["templates"] += 1
            s["why"] = [w for w in WHY if w in set(s["why"]) | set(f["why"])]
    for n, s in settings.items():
        by_value = seen.get(n) or {}
        if n not in secrets and by_value:
            fp, where = max(by_value.items(), key=lambda kv: len(kv[1]))
            s["same"] = len(where)
            if len(by_value) == 1 or (n, fp) in repeats:
                # named like a secret, or a list or dict: only that it is the same, never the value
                s["value"] = where[0][1] if where[0][1] is not None else ("(same; not shown)" if len(where) > 1 else "")
        s["suggestion"] = suggest(n, s["why"])
    out = sorted(settings.values(), key=lambda s: (-s["templates"], min(WHY.index(w) for w in s["why"]) if s["why"] else 9, s["setting"]))
    return templates, out


def _all(api, path):
    """Every page of a list: (records, why not)."""
    out, nxt = [], path + ("&" if "?" in path else "?") + "page_size=200"
    for _ in range(500):
        code, d = api.call("GET", nxt)
        if code != 200:
            return out, "%s could not be read (HTTP %d): %s" % (path.split("?")[0], code, error_text(d))
        out += d.get("results") or []
        n = d.get("next")
        if not n:
            return out, ""
        nxt = n.split(api.base, 1)[1] if api.base in n else n.lstrip("/")
    return out, ""


def read(api, p):
    """(records, problems): the templates as audit() takes them, read from AAP."""
    problems, records = [], []
    skip = {str(x).strip() for x in p.get("exclude") or [] if str(x).strip()}
    lists = [("job", "job_templates/")] + ([("workflow", "workflow_job_templates/")] if p.get("workflows", True) else [])
    for kind, path in lists:
        got, why = _all(api, path)
        if why:
            problems.append("the %s templates: %s" % (kind, why))
        for r in got:
            sf = r.get("summary_fields") or {}
            project = (sf.get("project") or {}).get("name") or ""
            if str(r.get("id")) in skip or r.get("name") in skip:
                continue
            if kind == "job" and p.get("project") and project != p["project"]:
                continue
            rec = {"kind": kind, "id": r.get("id"), "name": r.get("name") or "", "project": project,
                   "extra_vars": r.get("extra_vars"), "ask_variables": bool(r.get("ask_variables_on_launch")), "survey": []}
            if r.get("survey_enabled"):
                code, d = api.call("GET", "%s%s/survey_spec/" % (path, r.get("id")))
                if code == 200:
                    rec["survey"] = [{"variable": q.get("variable"), "type": q.get("type")} for q in d.get("spec") or []]
                else:
                    problems.append("the survey of %s template %s (ID %s) could not be read (HTTP %d): %s"
                                    % (kind, rec["name"], rec["id"], code, error_text(d)))
            records.append(rec)
    if p.get("project") and not any(r["kind"] == "job" for r in records) and not problems:
        problems.append("aap_template_audit_project: no job template of a project named %s that the AAP user can see" % p["project"])
    return records, problems


def main():
    module = AnsibleModule(argument_spec={
        "site_wide": dict(type="list", elements="str", default=[]), "per_enclave": dict(type="list", elements="str", default=[]),
        "per_template": dict(type="list", elements="str", default=[]), "ignore": dict(type="list", elements="str", default=[]),
        "repeat_min": dict(type="int", default=3), "project": dict(type="str", default=""),
        "exclude": dict(type="list", elements="str", default=[]), "workflows": dict(type="bool", default=True),
        "ca_path": dict(type="str", default=""), "timeout": dict(type="int", default=30)}, supports_check_mode=True)
    p = module.params
    api = Api(connection(), p["ca_path"], p["timeout"], ca_setting="aap_template_audit_ca_path", no_credential=NO_CREDENTIAL)
    why = api.probe()
    if why:
        module.exit_json(changed=False, api=api.base, problems=[why], templates=[], settings=[])
    records, problems = read(api, p)
    templates, settings = audit(records, p)
    module.exit_json(changed=False, api=api.base, problems=problems, templates=templates, settings=settings)


if __name__ == "__main__":
    main()
