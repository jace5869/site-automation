#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Checklists (CKL, CKLB) into STIG Manager, the check: reads the checklists from where your site keeps them - a Windows
file share (\\\\server\\share\\folder, over SMB with smbprotocol: no mount) or a folder on the AAP side -, finds each one's
asset in the collection by the checklist's host name (HOST_NAME; a web site or database checklist: <host>-<site>-<instance>,
as STIG Manager names it), matches each STIG of the checklist to a STIG installed in STIG Manager by its rules (the
benchmark id, else the rules' ids, V-numbers and versions - an InSpec checklist's own STIG id is not one STIG Manager
knows), and plans the reviews to write: workflow 40's approval, then playbooks/stigman_update.yml writes them. Reads only:
nothing on the share or in STIG Manager is written. Standard library only, but for a share: smbprotocol."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_ckl_import
short_description: Plan the reviews of checklists (CKL, CKLB) for STIG Manager, read-only
description:
  - Reads the files of C(src) whose names match C(patterns) - a share's folder (C(\\server\share\folder),
    C(smb://server/share/folder), C(//server/share/folder)) or a folder on this side - and its sub-folders with C(recurse).
  - Each checklist's asset is its host name (HOST_NAME, CKLB target_data.host_name) in C(collection) - by name, FQDN or
    short name; a web site or database checklist's is <host>-<site>-<instance> (NA for none), as STIG Manager's own import
    names it. No asset there - planned as a new one (C(create_assets)), with every STIG its checklists matched.
  - Each STIG of a checklist is matched to an installed STIG (GET /stigs) by its rules - a rule matches by its rule id
    (SV-257985), its V-number (the group) or its version (VRPE-8X-000001) - at the revision the asset (else the collection)
    has, else the newest; C(benchmarks) names one for a checklist's STIG id; at least C(min_match) of its rules must match.
  - A rule is planned when the checklist answered it (Not a Finding, Open, Not Applicable) and STIG Manager has no review,
    Not Reviewed or another result - not an Accepted one (C(overwrite_accepted)), not a rule two checklists answer
    differently, not one whose rule id names another revision of the rule (C(other_revision)).
  - GET requests only, to STIG Manager; the share is only read.
options:
  api: {description: "STIG Manager's API (https://stigman.example.mil/api).", type: str, required: true}
  token_url: {description: The Keycloak token endpoint., type: str, required: true}
  client_id: {description: The client (read access is enough)., type: str, required: true}
  client_secret: {description: Its secret., type: str, required: true, no_log: true}
  scope: {description: "Scopes to ask for ('' = the client's default scopes).", type: str, default: ''}
  validate_certs: {description: Check the HTTPS certificates., type: bool, default: true}
  ca_path: {description: "A CA bundle to check them with ('' = the system's).", type: str, default: ''}
  timeout: {description: Seconds per request., type: int, default: 60}
  src: {description: "The share's folder, or a folder on the AAP side.", type: str, required: true}
  base: {description: "A folder src is relative to, when it is a folder on the AAP side (the playbooks folder).", type: str, default: ''}
  patterns: {description: "File names to take (fnmatch, any case).", type: list, elements: str, default: ['*.ckl', '*.cklb']}
  recurse: {description: Read the sub-folders too., type: bool, default: true}
  username: {description: "The account (DOMAIN\\user or user@DOMAIN) for the share ('' = Kerberos, a ticket already here).", type: str, default: ''}
  password: {description: Its password., type: str, default: '', no_log: true}
  auth: {description: "negotiate (Kerberos, else NTLM), ntlm or kerberos.", type: str, default: negotiate}
  encrypt: {description: Encrypt (SMB 3) even when the share does not require it., type: bool, default: false}
  port: {description: The share's port., type: int, default: 445}
  max_files: {description: At most this many files., type: int, default: 500}
  max_mb: {description: A file larger than this many MB is left out (and said)., type: int, default: 50}
  collection: {description: The collection's name (or id)., type: str, required: true}
  create_assets: {description: "Plan a new asset for a host name with none (the update needs the Manage grant).", type: bool, default: true}
  benchmarks: {description: "{a checklist's STIG id (fnmatch, any case): the installed STIG's benchmark id} - used instead of matching.", type: dict, default: {}}
  min_match: {description: "The share of a checklist STIG's rules that must match the installed STIG (0.8 = 80%).", type: float, default: 0.8}
  candidates: {description: "At most this many installed STIGs read for the rules of a checklist's STIG (the likeliest by name).", type: int, default: 25}
  search_all: {description: "No match among those: read every installed STIG's rules (slow with hundreds).", type: bool, default: false}
  other_revision: {description: "Write a rule whose rule id (SV-...rNNN) differs from STIG Manager's - another revision of it.", type: bool, default: false}
  overwrite_accepted: {description: Write over an Accepted review too., type: bool, default: false}
  status: {description: "The reviews' status: saved, submitted or accepted.", type: str, default: submitted}
  detail_max: {description: A detail longer than this is cut., type: int, default: 8000}
  day: {description: "Today (YYYY-MM-DD), for the comment.", type: str, default: ''}
author: site automation
'''

RETURN = r'''
files:
  description: "Each file: {name (its path under src), size, sha256, modified, checklists (how many STIGs), error ('' = read)}."
  type: list
  returned: always
checklists:
  description: "Each STIG of each checklist: {file, stig (its STIG id), title, release, host, asset, assetId, new (true: no asset yet),
    benchmark, revision, match (rules matched / rules), by (benchmark id, rule ids, V-numbers, versions, setting), counts
    {pass, fail, notapplicable, notchecked}, planned, state (planned, nothing to write, not matched, no asset, no STIG on the
    asset, error), why}."
  type: list
  returned: always
plan:
  description: "The plan for playbooks/stigman_update.yml (stig_update_plan's form): {kind: create, ...} for each new asset, then
    one item per review {kind: import, collectionId, assetId (or '' and new_asset), ruleId, was, result, status, detail, comment,
    engine, line, file, ...}."
  type: list
  returned: always
left:
  description: "The answered rules not planned: {file, asset, benchmark, v, version, title, result, why, kind (same, accepted, conflict,
    revision, unmatched, no_asset, no_stig)}."
  type: list
  returned: always
collection:
  description: "{collectionId, name} as found ('' = not found)."
  type: dict
  returned: always
source:
  description: Where they were read from, as given (the report says it).
  type: str
  returned: always
account:
  description: The account the share was read as ('' for a folder on the AAP side) - never its password.
  type: str
  returned: always
error:
  description: Why nothing could be planned (the share, the token, the collection), in plain words ('' = read).
  type: str
  returned: always
'''

import datetime
import fnmatch
import hashlib
import json
import os
import re
import ssl
import xml.etree.ElementTree as ET

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six.moves.urllib.parse import urlencode, quote
from ansible.module_utils.six.moves.urllib.request import Request, urlopen
from ansible.module_utils.six.moves.urllib.error import HTTPError, URLError

SV_RE = re.compile(r"^(SV-\d+)(r\d+)?(_rule)?$", re.I)
V_RE = re.compile(r"^V-\d+$", re.I)
ES_RE = re.compile(r"Evaluate-STIG\s+[\d.]+", re.I)
# a checklist's status -> STIG Manager's result (Not Reviewed is never written)
CKL_RESULT = {"notafinding": "pass", "not_a_finding": "pass", "open": "fail", "not_applicable": "notapplicable",
              "notapplicable": "notapplicable", "not_reviewed": "notchecked", "notreviewed": "notchecked"}
SM_WORD = {"pass": "Not a Finding", "fail": "Open", "notapplicable": "Not Applicable", "notchecked": "Not Reviewed"}
STATUSES = ("saved", "submitted", "accepted")
# words that say nothing about which STIG it is
STOP = set("stig stigs security technical implementation guide profile inspec compliance benchmark the a an of for and "
           "version release x v r readiness srg".split())


class Fail(Exception):
    pass


# ---- the files -------------------------------------------------------------------------------------------------------
def smb_path(src):
    """\\\\server\\share\\folder, smb://server/share/folder or //server/share/folder -> (server, the UNC path); None for a
    folder on this side."""
    s = (src or "").strip()
    if s.lower().startswith("smb:"):
        s = "\\\\" + s[4:].lstrip("/").replace("/", "\\")
    elif s.startswith("//"):
        s = "\\\\" + s[2:].replace("/", "\\")
    if not s.startswith("\\\\"):
        return None
    parts = [x for x in s.rstrip("\\")[2:].split("\\") if x]
    if len(parts) < 2:
        raise Fail("%s names no share (\\\\server\\share\\folder)" % src)
    return parts[0], "\\\\" + "\\".join(parts)


def wanted(name, patterns):
    n = name.lower()
    return any(fnmatch.fnmatchcase(n, p.lower()) for p in patterns or ["*"])


def when(t):
    """A time (seconds, or a datetime) -> 'YYYY-MM-DD HH:MM UTC' ('' when not known)."""
    try:
        if isinstance(t, datetime.datetime):
            d = t.astimezone(datetime.timezone.utc) if t.tzinfo else t
        else:
            d = datetime.datetime.fromtimestamp(float(t), datetime.timezone.utc)
        return d.strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def why_share(what, e, p):
    """A failure on the share in plain words, with what to look at (as site_answer_files.py)."""
    text = str(e)
    low = text.lower()
    if "logon_failure" in low or "logon failure" in low or "status_logon" in low:
        hint = ": the user name or password is wrong (the Windows file share credential - user@YOURDOMAIN.MIL), or the account is locked"
    elif "access_denied" in low or "access is denied" in low:
        hint = ": the account may log in but not read this folder - give it Read on the share and the folder"
    elif "bad_network_name" in low or "0xc00000cc" in low:
        hint = ": no such share on that server (the part after the server name)"
    elif "0xc0000034" in low or "0xc000003a" in low or "no such file" in low:
        hint = ": no such folder on the share"
    elif "default realm" in low:
        hint = ": write the credential's user name as user@YOURDOMAIN.MIL - the domain's full name in capitals (its Kerberos realm)"
    elif "server not found in kerberos database" in low:
        hint = ": write the server by its full name (\\\\fileserver.yourdomain.mil\\share\\folder) - Kerberos asks for a ticket to that name"
    elif "md4" in low or "unsupported hash" in low:
        hint = (": NTLM needs MD4, which a FIPS execution environment does not have, and Kerberos did not happen - "
                "stigman_ckl_import_auth: negotiate, the user name user@YOURDOMAIN.MIL, the server's full name")
    elif "failed to connect" in low or "timed out" in low or "refused" in low or "name or service" in low:
        hint = ": the server cannot be reached on port %d from the execution node (a firewall, or the name)" % p["port"]
    else:
        hint = ""
    return "%s: %s%s" % (what, text.splitlines()[0][:300] if text else type(e).__name__, hint)


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


def from_folder(p):
    """A folder on this side -> [(path under it, size, modified, read)]."""
    src = p["src"] if os.path.isabs(p["src"]) or not p["base"] else os.path.join(p["base"], p["src"])
    if not os.path.isdir(src):
        raise Fail("no folder %s on the AAP side" % src)
    out = []
    for top, dirs, names in os.walk(src):
        dirs[:] = sorted(dirs) if p["recurse"] else []
        for n in sorted(names):
            full = os.path.join(top, n)
            if os.path.isfile(full) and wanted(n, p["patterns"]):
                out.append((os.path.relpath(full, src).replace(os.sep, "/"), os.path.getsize(full), when(os.path.getmtime(full)),
                            (lambda f=full: read_file(f))))
    return out


def from_share(p, server, unc):
    try:
        import smbclient
    except ImportError:
        raise Fail("the checklists are on a file share (%s), and this execution environment has no smbprotocol: add it to the "
                   "execution environment (pip install smbprotocol)" % unc)
    if not p["username"] and p["auth"] != "kerberos":
        raise Fail("no login for the share %s: attach the \"Windows file share\" credential (user@YOURDOMAIN.MIL and its password) "
                   "to the template - or stigman_ckl_import_auth: kerberos with a ticket" % unc)
    kw = {"port": p["port"]}
    try:
        smbclient.register_session(server, username=p["username"] or None, password=p["password"] or None, port=p["port"],
                                   encrypt=p["encrypt"] or None, auth_protocol=p["auth"], connection_timeout=30)
    except Exception as e:                              # noqa: BLE001 - every way a login fails, said in words
        raise Fail(why_share("logging in to %s as %s" % (server, p["username"] or "the Kerberos ticket"), e, p))
    out = []

    def walk(folder, rel):
        try:
            entries = sorted(smbclient.scandir(folder, **kw), key=lambda x: x.name.lower())
        except Exception as e:                          # noqa: BLE001
            raise Fail(why_share("reading the folder %s" % folder, e, p))
        for x in entries:
            path = folder + "\\" + x.name
            if x.is_dir():
                if p["recurse"] and x.name not in (".", ".."):
                    walk(path, rel + x.name + "/")
                continue
            if not wanted(x.name, p["patterns"]):
                continue
            info = getattr(x, "smb_info", None)
            size = int(info.end_of_file) if info is not None and getattr(info, "end_of_file", None) is not None else \
                smbclient.stat(path, **kw).st_size

            def read(path=path):
                try:
                    with smbclient.open_file(path, mode="rb", **kw) as f:
                        return f.read()
                except Exception as e:                  # noqa: BLE001
                    raise Fail(why_share("reading %s" % path, e, p))
            out.append((rel + x.name, size, when(getattr(info, "last_write_time", None)), read))
    walk(unc, "")
    return out


def read_files(p):
    """-> [{name, size, sha256, modified, data or error}]."""
    share = smb_path(p["src"])
    listed = from_share(p, *share) if share else from_folder(p)
    if not listed:
        raise Fail("no checklist (%s) in %s" % (", ".join(p["patterns"]), p["src"]))
    if len(listed) > p["max_files"]:
        raise Fail("%d files in %s - over stigman_ckl_import_max_files (%d)" % (len(listed), p["src"], p["max_files"]))
    out = []
    for name, size, modified, read in listed:
        rec = {"name": name, "size": size, "modified": modified, "sha256": "", "checklists": 0, "error": ""}
        if size > p["max_mb"] * 1024 * 1024:
            rec["error"] = "%.1f MB - over stigman_ckl_import_max_mb (%d): left out" % (size / 1048576.0, p["max_mb"])
        else:
            try:
                data = read()
                rec.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), data=data)
            except Fail as e:
                rec["error"] = str(e)
        out.append(rec)
    return out


# ---- a checklist -----------------------------------------------------------------------------------------------------
def _t(el, path):
    x = el.find(path)
    return (x.text or "").strip() if x is not None and x.text else ""


def _revision(version, release):
    """'2', 'Release: 9 Benchmark Date: 24 Jul 2024' -> 'V2R9' ('' when not known)."""
    m = re.search(r"Release:\s*(\d+)", release or "")
    v = re.match(r"\s*(\d+)\s*$", version or "")
    return "V%sR%s" % (v.group(1), m.group(1)) if m and v else ""


def _engine(tools, details, custom):
    """What made the checklist's answer, for the review's result engine: InSpec (an HDF checklist: SAF CLI, MITRE's Heimdall),
    Evaluate-STIG; None - a person (STIG Viewer)."""
    if "hdfSpecificData" in (tools or "") or "hdfSpecificData" in (custom or ""):
        return {"type": "script", "product": "InSpec"}
    if ES_RE.search(details or ""):
        return {"type": "script", "product": "Evaluate-STIG"}
    return None


def parse_ckl(data):
    """A CKL (STIG Viewer 2, XML) -> {target, stigs: [{stig, title, revision, rules: [...]}]}."""
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise Fail("it declares a DOCTYPE or entities - not read (a checklist has none)")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise Fail("not a checklist (XML): %s" % e)
    if root.tag != "CHECKLIST":
        raise Fail("not a checklist: its XML is <%s>, not <CHECKLIST>" % root.tag)
    a = root.find("ASSET")
    a = a if a is not None else ET.Element("ASSET")
    target = {"host": _t(a, "HOST_NAME"), "fqdn": _t(a, "HOST_FQDN"), "ip": _t(a, "HOST_IP"), "mac": _t(a, "HOST_MAC"),
              "web": _t(a, "WEB_OR_DATABASE").lower() == "true", "site": _t(a, "WEB_DB_SITE"), "instance": _t(a, "WEB_DB_INSTANCE")}
    stigs = []
    for ist in root.findall("STIGS/iSTIG"):
        info = dict((_t(s, "SID_NAME"), _t(s, "SID_DATA")) for s in ist.findall("STIG_INFO/SI_DATA"))
        rules = []
        for v in ist.findall("VULN"):
            sd = {}
            for s in v.findall("STIG_DATA"):
                k = _t(s, "VULN_ATTRIBUTE")
                if k and k not in sd:
                    sd[k] = _t(s, "ATTRIBUTE_DATA")
            rules.append({"v": sd.get("Vuln_Num", ""), "rule_id": sd.get("Rule_ID", ""), "version": sd.get("Rule_Ver", ""),
                          "title": sd.get("Rule_Title", ""), "severity": sd.get("Severity", ""), "status": _t(v, "STATUS"),
                          "details": _t(v, "FINDING_DETAILS"), "comments": _t(v, "COMMENTS"),
                          "engine": _engine(sd.get("Third_Party_Tools"), _t(v, "FINDING_DETAILS"), info.get("customname"))})
        stigs.append({"stig": info.get("stigid", ""), "title": info.get("title", ""),
                      "revision": _revision(info.get("version"), info.get("releaseinfo")), "rules": rules})
    return {"target": target, "stigs": stigs}


def parse_cklb(data):
    """A CKLB (STIG Viewer 3, JSON) -> the same."""
    try:
        d = json.loads(data.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Fail("not a checklist (JSON): %s" % e)
    if not isinstance(d, dict) or not isinstance(d.get("stigs"), list):
        raise Fail("not a checklist: no stigs in its JSON")
    t = d.get("target_data") or {}
    target = {"host": str(t.get("host_name") or "").strip(), "fqdn": str(t.get("fqdn") or "").strip(),
              "ip": str(t.get("ip_address") or "").strip(), "mac": str(t.get("mac_address") or "").strip(),
              "web": bool(t.get("is_web_database")), "site": str(t.get("web_db_site") or "").strip(),
              "instance": str(t.get("web_db_instance") or "").strip()}
    stigs = []
    for s in d["stigs"]:
        rules = []
        for r in s.get("rules") or []:
            details = str(r.get("finding_details") or "").strip()
            rules.append({"v": str(r.get("group_id") or ""), "rule_id": str(r.get("rule_id") or r.get("rule_id_src") or ""),
                          "version": str(r.get("rule_version") or ""), "title": str(r.get("rule_title") or ""),
                          "severity": str(r.get("severity") or ""), "status": str(r.get("status") or ""), "details": details,
                          "comments": str(r.get("comments") or "").strip(),
                          "engine": _engine(json.dumps(r.get("third_party_tools") or ""), details, "")})
        stigs.append({"stig": str(s.get("stig_id") or ""), "title": str(s.get("stig_name") or s.get("display_name") or ""),
                      "revision": _revision(str(s.get("version") or ""), str(s.get("release_info") or "")), "rules": rules})
    return {"target": target, "stigs": stigs}


def parse(name, data):
    if name.lower().endswith(".cklb") or data.lstrip()[:1] == b"{":
        return parse_cklb(data)
    return parse_ckl(data)


def asset_name(target):
    """The checklist's asset, as STIG Manager's own import names it: the host name; a web site or database checklist's
    <host>-<site>-<instance> (NA for none)."""
    host = target.get("host") or ""
    if not host or not target.get("web"):
        return host
    return "%s-%s-%s" % (host, target.get("site") or "NA", target.get("instance") or "NA")


# ---- STIG Manager ----------------------------------------------------------------------------------------------------
class Client(object):
    def __init__(self, p):
        self.p = p
        self.api = p["api"].rstrip("/")
        self.ctx = ssl._create_unverified_context() if not p["validate_certs"] else ssl.create_default_context(cafile=p["ca_path"] or None)
        self.token = None
        self.cache = {}

    def _open(self, req):
        try:
            resp = urlopen(req, timeout=self.p["timeout"], context=self.ctx)
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else None
        except HTTPError as e:
            raise RuntimeError("HTTP %d from %s: %s" % (e.code, req.get_full_url().split("?")[0],
                                                        e.read().decode("utf-8", "replace")[:300].strip() or e.reason))
        except URLError as e:
            raise RuntimeError("cannot reach %s: %s" % (req.get_full_url().split("?")[0], e.reason))
        except ValueError:
            raise RuntimeError("%s did not answer JSON" % req.get_full_url().split("?")[0])

    def login(self):
        form = {"grant_type": "client_credentials", "client_id": self.p["client_id"], "client_secret": self.p["client_secret"]}
        if self.p["scope"]:
            form["scope"] = self.p["scope"]
        ans = self._open(Request(self.p["token_url"], data=urlencode(form).encode("utf-8"),
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})) or {}
        if not ans.get("access_token"):
            raise RuntimeError("the token endpoint gave no access_token")
        self.token = ans["access_token"]

    def get(self, path, query=None):
        url = self.api + path + (("?" + urlencode(query)) if query else "")
        if url not in self.cache:
            self.cache[url] = self._open(Request(url, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"}))
        return self.cache[url]

    def rules(self, bench, rev):
        return self.get("/stigs/%s/revisions/%s/rules" % (quote(bench, safe=''), quote(rev, safe=''))) or []

    def reviews(self, c, a, bench):
        return self.get("/collections/%s/reviews/%s" % (quote(c, safe=''), quote(a, safe='')), [("benchmarkId", bench)]) or []


def short(name):
    return (name or "").lower().split(".")[0]


def matching(names, assets):
    """The assets whose name or FQDN is one of names: an exact (case-blind) name or FQDN first, then the short names."""
    want = [n.lower() for n in names if n]
    exact = [a for a in assets if (a.get("name") or "").lower() in want or (a.get("fqdn") or "").lower() in want]
    if exact:
        return exact
    shorts = set(short(n) for n in want)
    return [a for a in assets if short(a.get("name")) in shorts or (a.get("fqdn") and short(a.get("fqdn")) in shorts)]


def words(text):
    return set(w for w in re.findall(r"[a-z0-9]+", (text or "").lower().replace("_", " ")) if w not in STOP and len(w) > 1)


def candidates(ck, stigs, overrides, n):
    """The installed STIGs to try for a checklist's STIG, likeliest first: one named in benchmarks (by its STIG id), its own
    benchmark id, then those whose id and title share the most words with its id and title."""
    sid = ck.get("stig") or ""
    by_id = dict((s.get("benchmarkId"), s) for s in stigs)
    for pat, bench in sorted((overrides or {}).items()):
        if fnmatch.fnmatchcase(sid.lower(), str(pat).lower()) or fnmatch.fnmatchcase((ck.get("title") or "").lower(), str(pat).lower()):
            return [(by_id.get(bench) or {"benchmarkId": bench}, "setting")]
    out = [(by_id[sid], "benchmark id")] if sid in by_id else []
    mine = words(sid) | words(ck.get("title"))
    scored = sorted(((len(mine & (words(s.get("benchmarkId")) | words(s.get("title")))), s.get("benchmarkId") or "", s)
                     for s in stigs if s.get("benchmarkId") != sid), key=lambda x: (-x[0], x[1]))
    out += [(s, "rules") for k, _b, s in scored if k > 0][:max(0, n - len(out))]
    return out


def index(rules):
    """A revision's rules -> lookups by rule (SV-257985), V-number and version (upper case)."""
    sv, v, ver = {}, {}, {}
    for r in rules:
        m = SV_RE.match(r.get("ruleId") or "")
        if m:
            sv.setdefault(m.group(1).upper(), r)
        if r.get("groupId"):
            v.setdefault(r["groupId"].upper(), r)
        if r.get("version"):
            ver.setdefault(r["version"].upper(), r)
    return sv, v, ver


def match_rules(ck_rules, rules):
    """Each checklist rule -> (STIG Manager's rule or None, by what); a real rule id first, then the V-number, then the version."""
    sv, v, ver = index(rules)
    out = []
    for r in ck_rules:
        m = SV_RE.match(r.get("rule_id") or "")
        hit = (sv.get(m.group(1).upper()), "rule id") if m else (None, "")
        if not hit[0] and V_RE.match(r.get("v") or ""):
            hit = (v.get(r["v"].upper()), "V-number")
        if not hit[0] and r.get("version"):
            hit = (ver.get(r["version"].upper()), "version")
        if not hit[0] and r.get("v") and not V_RE.match(r["v"]):     # an InSpec checklist's "Vuln_Num" is the rule's version
            hit = (ver.get(r["v"].upper()), "version")
        out.append(hit if hit[0] else (None, ""))
    return out


def revision_for(bench, asset, col_stigs, stigs_by_id):
    """The revision whose rules a review is written to: the asset's, else the collection's, else the newest."""
    for s in (asset or {}).get("stigs") or []:
        if s.get("benchmarkId") == bench and s.get("revisionStr"):
            return s["revisionStr"]
    if col_stigs.get(bench):
        return col_stigs[bench]
    return (stigs_by_id.get(bench) or {}).get("lastRevisionStr") or "latest"


def match_stig(client, ck, stigs, p, asset, col_stigs):
    """A checklist's STIG -> {bench, revision, rules: [(sm rule or None, by)], matched, by} of the best installed STIG, or
    {best...} with why when none matches well enough."""
    stigs_by_id = dict((s.get("benchmarkId"), s) for s in stigs)
    tries = candidates(ck, stigs, p["benchmarks"], p["candidates"])
    if not tries and p["search_all"]:
        tries = [(s, "rules") for s in stigs]
    best = None
    total = len(ck["rules"]) or 1
    for s, how in tries:
        bench = s.get("benchmarkId") or ""
        rev = revision_for(bench, asset, col_stigs, stigs_by_id)
        try:
            rules = client.rules(bench, rev)
        except RuntimeError as e:
            if how == "setting":
                return {"why": "the STIG %s named for it in stigman_ckl_import_benchmarks could not be read: %s" % (bench, e)}
            continue
        hits = match_rules(ck["rules"], rules)
        n = len([h for h in hits if h[0]])
        if best is None or n > best["matched"]:
            kinds = sorted(set(h[1] for h in hits if h[0]))
            best = {"bench": bench, "revision": rev, "rules": hits, "matched": n, "how": how,
                    "by": how if how in ("setting", "benchmark id") else ", ".join(kinds)}
        if how in ("setting", "benchmark id") and n >= p["min_match"] * total:
            break
    if p["search_all"] and (best is None or best["matched"] < p["min_match"] * total) and tries and tries[0][1] != "setting":
        tried = set(s.get("benchmarkId") for s, _h in tries)
        for s in stigs:
            if s.get("benchmarkId") in tried:
                continue
            bench = s.get("benchmarkId") or ""
            rev = revision_for(bench, asset, col_stigs, stigs_by_id)
            try:
                hits = match_rules(ck["rules"], client.rules(bench, rev))
            except RuntimeError:
                continue
            n = len([h for h in hits if h[0]])
            if best is None or n > best["matched"]:
                best = {"bench": bench, "revision": rev, "rules": hits, "matched": n, "how": "rules",
                        "by": ", ".join(sorted(set(h[1] for h in hits if h[0])))}
    if best is None:
        return {"why": "no installed STIG has a name like it (%s) - name one in stigman_ckl_import_benchmarks, or "
                       "stigman_ckl_import_search_all: true" % (ck.get("stig") or ck.get("title") or "no STIG id")}
    if best["matched"] < p["min_match"] * total:
        best["why"] = ("no installed STIG matches %d%% of its rules - the closest, %s %s, has %d of %d - import its STIG into STIG "
                       "Manager, or name it in stigman_ckl_import_benchmarks" % (round(p["min_match"] * 100), best["bench"],
                                                                                best["revision"], best["matched"], len(ck["rules"])))
    return best


# ---- the plan ----------------------------------------------------------------------------------------------------------
def word(r):
    return SM_WORD.get(r, r or "no review")


def plan_all(p, client, files):
    """The files read -> (checklists, plan, left, collection)."""
    cols = client.get("/collections") or []
    want = p["collection"].strip()
    col = [c for c in cols if (c.get("name") or "").lower() == want.lower() or str(c.get("collectionId")) == want]
    if not col:
        raise Fail("no collection %s that the client can read (it reads: %s)" % (want, ", ".join(sorted(c.get("name") or "" for c in cols)) or "none"))
    col = {"collectionId": str(col[0]["collectionId"]), "name": col[0].get("name") or ""}
    cid = col["collectionId"]
    assets = client.get("/assets", [("collectionId", cid), ("projection", "stigs")]) or []
    col_stigs = dict((s.get("benchmarkId"), s.get("revisionStr") or "") for s in client.get("/collections/%s/stigs" % quote(cid, safe='')) or [])
    stigs = client.get("/stigs") or []
    checklists, left, picks = [], [], {}
    new_assets = {}
    for f in files:
        if f.get("error"):
            continue
        try:
            ckl = parse(f["name"], f.pop("data"))
        except Fail as e:
            f["error"] = str(e)
            continue
        f["checklists"] = len(ckl["stigs"])
        name = asset_name(ckl["target"])
        for ck in ckl["stigs"]:
            counts = dict((k, 0) for k in ("pass", "fail", "notapplicable", "notchecked"))
            for r in ck["rules"]:
                r["result"] = CKL_RESULT.get((r.get("status") or "").lower().replace(" ", "_"), "notchecked")
                counts[r["result"]] += 1
            row = {"file": f["name"], "modified": f.get("modified", ""), "stig": ck["stig"], "title": ck["title"], "release": ck["revision"],
                   "host": ckl["target"]["host"], "asset": name, "assetId": "", "new": False, "benchmark": "", "revision": "",
                   "match": "", "by": "", "counts": counts, "planned": 0, "state": "", "why": ""}
            checklists.append(row)
            answered = [r for r in ck["rules"] if r["result"] != "notchecked"]

            def leave(kind, why, rules=answered, row=row):
                for r in rules:
                    left.append({"file": row["file"], "asset": row["asset"], "benchmark": row["benchmark"] or ck["stig"], "v": r.get("v", ""),
                                 "version": r.get("version", ""), "title": r.get("title", ""), "result": r["result"], "why": why, "kind": kind})
            if not name:
                row.update(state="no asset", why="the checklist has no host name (HOST_NAME) - fill it in STIG Viewer, or in the file")
                leave("no_asset", row["why"])
                continue
            found = matching([name, "" if ckl["target"]["web"] else ckl["target"]["fqdn"]], assets)
            if len(found) > 1:
                row.update(state="no asset", why="several assets in %s fit %s: %s - not written" % (
                    col["name"], name, ", ".join(sorted(a.get("name") or "" for a in found))))
                leave("no_asset", row["why"])
                continue
            asset = found[0] if found else None
            if asset:
                row.update(asset=asset.get("name") or name, assetId=str(asset.get("assetId") or ""))
            elif not p["create_assets"]:
                row.update(state="no asset", why="no asset %s in %s - create it (with its STIG) in STIG Manager, or "
                                                 "stigman_ckl_import_create_assets: true" % (name, col["name"]))
                leave("no_asset", row["why"])
                continue
            else:
                row["new"] = True
            m = match_stig(client, ck, stigs, p, asset, col_stigs)
            if m.get("bench"):
                row.update(benchmark=m["bench"], revision=m["revision"], match="%d of %d" % (m["matched"], len(ck["rules"])), by=m["by"])
            if m.get("why"):
                row.update(state="not matched", why=m["why"])
                leave("unmatched", m["why"])
                continue
            if asset and m["bench"] not in [s.get("benchmarkId") for s in asset.get("stigs") or []]:
                row.update(state="no STIG on the asset", why="%s has no %s in STIG Manager - assign it to the asset (Collection > "
                                                             "Assets), then run this again" % (row["asset"], m["bench"]))
                leave("no_stig", row["why"])
                continue
            now = {}
            if asset:
                try:
                    for rv in client.reviews(cid, row["assetId"], m["bench"]):
                        for rid in [rv.get("ruleId")] + list(rv.get("ruleIds") or []):
                            if rid:
                                now.setdefault(rid, rv)
                except RuntimeError as e:
                    row.update(state="error", why="its reviews could not be read: %s" % e)
                    leave("error", row["why"])
                    continue
            else:
                new_assets.setdefault(name.lower(), {"name": name, "fqdn": "" if ckl["target"]["web"] else ckl["target"]["fqdn"],
                                                     "ip": ckl["target"]["ip"], "mac": ckl["target"]["mac"], "benchmarks": [],
                                                     "revisions": {}, "files": []})
                na = new_assets[name.lower()]
                if m["bench"] not in na["benchmarks"]:
                    na["benchmarks"].append(m["bench"])
                    na["revisions"][m["bench"]] = m["revision"]
                if f["name"] not in na["files"]:
                    na["files"].append(f["name"])
            for r, (smr, by) in zip(ck["rules"], m["rules"]):
                if r["result"] == "notchecked":
                    continue
                if not smr:
                    leave("unmatched", "not in %s %s" % (m["bench"], m["revision"]), [r])
                    continue
                rid = smr.get("ruleId") or ""
                mine = SV_RE.match(r.get("rule_id") or "")
                if mine and mine.group(2) and rid and rid.lower() != r["rule_id"].lower() and not p["other_revision"]:
                    leave("revision", "the checklist answered %s, STIG Manager's %s has %s - another revision of the rule "
                                      "(stigman_ckl_import_other_revision)" % (r["rule_id"], m["revision"], rid), [r])
                    continue
                cur = now.get(rid) or {}
                st = cur.get("status") or {}
                was = {"result": cur.get("result") or "", "status": (st.get("label", "") if isinstance(st, dict) else str(st)),
                       "ts": cur.get("ts") or cur.get("touchTs") or ""}
                if was["result"] == r["result"]:
                    leave("same", "STIG Manager has it so: %s (%s)" % (word(was["result"]), was["status"] or "-"), [r])
                    continue
                if was["status"] == "accepted" and was["result"] not in ("", "notchecked") and not p["overwrite_accepted"]:
                    leave("accepted", "Accepted in STIG Manager as %s - left for the reviewers (stigman_ckl_import_overwrite_accepted)"
                          % word(was["result"]), [r])
                    continue
                key = (name.lower() if not asset else row["assetId"], rid)
                picks.setdefault(key, []).append((row, r, smr, m, was, ck))
    plan, conflicts = [], 0
    for key in sorted(picks, key=lambda k: (str(k[0]), k[1])):
        got = picks[key]
        if len(set(g[1]["result"] for g in got)) > 1:
            conflicts += 1
            for row, r, smr, m, was, ck in got:
                left.append({"file": row["file"], "asset": row["asset"], "benchmark": m["bench"], "v": smr.get("groupId") or r.get("v", ""),
                             "version": smr.get("version") or r.get("version", ""), "title": r.get("title", ""), "result": r["result"],
                             "why": "the checklists disagree: %s" % ", ".join("%s says %s" % (g[0]["file"], word(g[1]["result"])) for g in got),
                             "kind": "conflict"})
            continue
        row, r, smr, m, was, ck = sorted(got, key=lambda g: g[0]["modified"])[-1]     # the same answer twice: the newest file's
        plan.append(review_item(p, col, row, r, smr, m, was, ck))
        row["planned"] += 1
    for row in checklists:
        if not row["state"]:
            row["state"] = "planned" if row["planned"] else "nothing to write"
    creates = []
    for k in sorted(new_assets):
        na = new_assets[k]
        mine = [i for i in plan if i.get("new_asset", "").lower() == k]
        if not mine:
            continue                    # nothing to write to it: not created
        n = dict((x, len([i for i in mine if i["result"] == x])) for x in ("pass", "fail", "notapplicable"))
        creates.append({"kind": "create", "host": na["name"], "label": na["name"], "collection": col["name"], "collectionId": cid,
                        "asset": na["name"], "fqdn": na["fqdn"], "ip": na["ip"], "mac": na["mac"], "assetId": "",
                        "benchmark": na["benchmarks"][0], "benchmarks": na["benchmarks"],
                        "revision": ", ".join(na["revisions"][b] for b in na["benchmarks"]), "reviews": len(mine), "counts": n,
                        "description": "Created by the checklist import (site-automation), approved in AAP: %s" % ", ".join(na["files"])[:200],
                        "line": "NEW ASSET %s%s in %s with %s - then %d review(s): Not a Finding %d, Open %d, Not Applicable %d" % (
                            na["name"], (" (" + na["fqdn"] + ")") if na["fqdn"] else "", col["name"],
                            ", ".join("%s %s" % (b, na["revisions"][b]) for b in na["benchmarks"]), len(mine), n["pass"], n["fail"],
                            n["notapplicable"])})
    return checklists, creates + plan, left, col


def review_item(p, col, row, r, smr, m, was, ck):
    detail = (r.get("details") or "").strip() or "%s: %s (the checklist gives no finding details)" % (row["file"], word(r["result"]))
    if len(detail) > p["detail_max"]:
        detail = detail[:p["detail_max"] - 40].rstrip() + "\n[... cut at %d characters]" % p["detail_max"]
    note = "Imported from the checklist %s (%s%s) by site-automation, approved in AAP%s." % (
        row["file"], ck.get("stig") or ck.get("title") or "its STIG", (" " + ck["revision"]) if ck.get("revision") else "",
        (" on " + p["day"]) if p["day"] else "")
    comment = ((r.get("comments") or "").strip() + "\n\n" + note).strip() if r.get("comments") else note
    item = {"kind": "import", "host": row["host"], "label": row["asset"], "file": row["file"], "collection": col["name"],
            "collectionId": col["collectionId"], "asset": row["asset"], "assetId": row["assetId"], "benchmark": m["bench"],
            "revision": m["revision"], "ruleId": smr.get("ruleId") or "", "v": smr.get("groupId") or r.get("v", ""),
            "version": smr.get("version") or r.get("version", ""), "title": smr.get("title") or r.get("title", ""),
            "severity": (smr.get("severity") or r.get("severity") or "").lower(), "was": was, "earlier": {}, "result": r["result"],
            "status": p["status"], "detail": detail, "comment": comment[:32767], "engine": r.get("engine"),
            "line": "%s %s %s: %s -> %s (%s) from %s" % (row["asset"], m["bench"], smr.get("version") or smr.get("groupId") or "",
                                                       word(was["result"]) if was["result"] else "no review", word(r["result"]),
                                                       p["status"], row["file"])}
    if row["new"]:
        item["new_asset"] = row["asset"]
    return item


def main():
    module = AnsibleModule(argument_spec=dict(
        api=dict(type="str", required=True),
        token_url=dict(type="str", required=True),
        client_id=dict(type="str", required=True),
        client_secret=dict(type="str", required=True, no_log=True),
        scope=dict(type="str", default=""),
        validate_certs=dict(type="bool", default=True),
        ca_path=dict(type="str", default=""),
        timeout=dict(type="int", default=60),
        src=dict(type="str", required=True),
        base=dict(type="str", default=""),
        patterns=dict(type="list", elements="str", default=["*.ckl", "*.cklb"]),
        recurse=dict(type="bool", default=True),
        username=dict(type="str", default=""),
        password=dict(type="str", default="", no_log=True),
        auth=dict(type="str", default="negotiate", choices=["negotiate", "ntlm", "kerberos"]),
        encrypt=dict(type="bool", default=False),
        port=dict(type="int", default=445),
        max_files=dict(type="int", default=500),
        max_mb=dict(type="int", default=50),
        collection=dict(type="str", required=True),
        create_assets=dict(type="bool", default=True),
        benchmarks=dict(type="dict", default={}),
        min_match=dict(type="float", default=0.8),
        candidates=dict(type="int", default=25),
        search_all=dict(type="bool", default=False),
        other_revision=dict(type="bool", default=False),
        overwrite_accepted=dict(type="bool", default=False),
        status=dict(type="str", default="submitted", choices=list(STATUSES)),
        detail_max=dict(type="int", default=8000),
        day=dict(type="str", default=""),
    ), supports_check_mode=True)
    p = module.params
    try:
        account = (p["username"] or ("the Kerberos ticket" if p["auth"] == "kerberos" else "")) if smb_path(p["src"]) else ""
    except Fail:
        account = ""
    out = {"changed": False, "files": [], "checklists": [], "plan": [], "left": [], "collection": {}, "account": account,
           "source": p["src"], "error": ""}
    try:
        files = read_files(p)
        out["files"] = files
        client = Client(p)
        try:
            client.login()
        except RuntimeError as e:
            raise Fail("no token for the client %s: %s" % (p["client_id"], e))
        try:
            out["checklists"], out["plan"], out["left"], out["collection"] = plan_all(p, client, files)
        except RuntimeError as e:
            raise Fail("STIG Manager could not be read: %s" % e)
    except Fail as e:
        out["error"] = str(e)
    for f in out["files"]:
        f.pop("data", None)
    module.exit_json(**out)


if __name__ == "__main__":
    main()
