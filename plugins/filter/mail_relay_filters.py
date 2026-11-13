"""Mail relay whitelist (roles/mail_relay, docs/MAIL_RELAY.md): the addresses a request names, what it changes in a
relay's list - the mynetworks line of Postfix's main.cf (style main_cf) or a list file with one address per line
(style file) - the new text, and the notes the approver reads. Everything that reads or writes a list is here, so the
plan the approver sees and the change the approved job makes come from the same code. Only the list's own lines and
the comment lines an add wrote are ever touched: every other byte of the file stays as it is."""
import calendar
import importlib.util
import ipaddress
import os
import re
import time

# the times a report shows, in report_time_zone (1.54.0): plugins/module_utils/site_time.py, loaded as site_filters.py does
_spec = importlib.util.spec_from_file_location(
    "site_time", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module_utils", "site_time.py"))
_tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tm)

OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
SHAPE = re.compile(r"^(%s[.]){3}%s(/([0-9]|[12][0-9]|3[0-2]))?\Z" % (OCTET, OCTET))
# an address or subnet on a list (198.51.100.3, 198.51.100.0/24); as a map's key also the short form of a Postfix or
# sendmail access map (198.51.100 = 198.51.100.0/24)
FULL = re.compile(r"^%s([.]%s){3}(/([0-9]|[12][0-9]|3[0-2]))?\Z" % (OCTET, OCTET))
SHORT = re.compile(r"^%s([.]%s){0,2}\Z" % (OCTET, OCTET))
# a map line with one of these does not let the address relay
NOT_ALLOWED = re.compile(r"^(REJECT|DEFER|DEFER_IF_PERMIT|DEFER_IF_REJECT|DISCARD|HOLD|[45][0-9][0-9])\Z", re.I)
# the end of a comment line an add wrote: who and when, in brackets (a comment cannot have [ ])
STAMP = r"\[added [0-9]{4}-[0-9]{2}-[0-9]{2} by [A-Za-z0-9._@-]+\][ \t]*$"
# the comment line an add wrote above an entry of a list file
FILE_COMMENT = re.compile(r"^# .*" + STAMP)
LOOPBACK = ipaddress.ip_network("127.0.0.0/8")
STYLES = ("main_cf", "file")


def _true(v):
    return v is True or str(v).strip().lower() in ("true", "yes", "on", "1")


def mail_relay_ip_problem(ip, action="add", allow_subnets=True, min_prefix=24):
    """Why one address cannot be added ('' = usable): one IPv4 address, or a subnet when allow_subnets - no wider than
    /min_prefix and written from its first address. A removal takes any address or subnet written as on the list
    (taking one away never opens the relay)."""
    ip = str(ip if ip is not None else "")
    subnets = _true(allow_subnets)
    min_prefix = int(min_prefix)
    if not ip.strip():
        return "it is empty: give the IP address to %s." % action
    if not SHAPE.match(ip):
        return "not an IPv4 address. Give one address, like 198.51.100.3%s." % (
            (" (or a subnet, like 198.51.100.0/%d)" % min_prefix) if subnets else "")
    if action == "remove":
        return ""
    if "/" in ip and not subnets:
        return "give one address, like 198.51.100.3, not a subnet (mail_relay_allow_subnets: true allows subnets)."
    prefix = int(ip.split("/")[1]) if "/" in ip else 32
    if prefix < min_prefix:
        return "the subnet is too wide: /%d or smaller (mail_relay_min_prefix)." % min_prefix
    net = ipaddress.ip_network(ip, strict=False)
    if str(net.network_address) != ip.split("/")[0]:
        return "a subnet starts at its first address: %s." % net
    if ip.split("/")[0] in ("0.0.0.0", "255.255.255.255"):
        return "not an address a mail client has."
    return ""


def mail_relay_ips(text, action="add", allow_subnets=True, min_prefix=24, most=20):
    """The addresses of a request as typed: one, or several separated by commas, spaces, semicolons or line breaks;
    /32 dropped, each once. {'ips': [...], 'problem': ''}. One unusable address refuses the whole request."""
    words = [w for w in re.split(r"[\s,;]+", str(text if text is not None else "")) if w]
    if not words:
        return {"ips": [], "problem": mail_relay_ip_problem("", action, allow_subnets, min_prefix)}
    ips = []
    for w in words:
        ip = w[:-3] if w.endswith("/32") else w
        why = mail_relay_ip_problem(ip, action, allow_subnets, min_prefix)
        if why:
            return {"ips": [], "problem": ("'%s': %s" % (w, why)) if len(words) > 1 else why}
        if ip not in ips:
            ips.append(ip)
    if len(ips) > int(most):
        return {"ips": [], "problem": "%d addresses: at most %d in one request (mail_relay_max_ips)." % (len(ips), int(most))}
    return {"ips": ips, "problem": ""}


def mail_relay_size(ip):
    """'198.51.100.0/24 (256 addresses)' for a subnet, the address as it is for one address."""
    ip = str(ip or "").strip()
    if "/" not in ip:
        return ip
    try:
        return "%s (%d addresses)" % (ip, ipaddress.ip_network(ip, strict=False).num_addresses)
    except ValueError:
        return ip


def mail_relay_short(ips, most=3):
    """The addresses for a title: all of them, or the first ones and how many more."""
    ips = list(ips or [])
    if len(ips) <= most:
        return ", ".join(ips)
    return "%s and %d more" % (", ".join(ips[:most - 1]), len(ips) - most + 1)


# ---- reading a list ------------------------------------------------------------------------------------------------

def _same(word, ip):
    """Is this word of a list the address (198.51.100.3 or 198.51.100.3/32), or the subnet, exactly?"""
    return word == ip or ("/" not in ip and word == ip + "/32")


def _split(line):
    """(body, line break) of a physical line."""
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _logical(lines, param):
    """(first, more): the index of the last 'param =' line of a main.cf (Postfix uses the last one) and the indexes
    of its continuation lines (they start with whitespace; comment and blank lines between them are skipped, as
    Postfix does). (None, []) when there is none. 'mynetworks_style =' is another parameter, never this one."""
    rx = re.compile(r"^%s[ \t]*=" % re.escape(param))
    first = None
    for i, line in enumerate(lines):
        if rx.match(line):
            first = i
    if first is None:
        return None, []
    more = []
    for j in range(first + 1, len(lines)):
        s = lines[j].strip()
        if not s or s.startswith("#"):
            continue
        if lines[j][0] in " \t":
            more.append(j)
            continue
        break
    return first, more


def _value(line, is_first):
    """(prefix, value, line break) of a physical line of the parameter: the value after '=' on its first line."""
    body, brk = _split(line)
    if is_first:
        k = body.index("=") + 1
        return body[:k], body[k:], brk
    return "", body, brk


def _words(value):
    return [w for w in re.split(r"[\s,]+", value) if w]


def _main_cf_words(lines, first, more):
    words = []
    for i in [first] + more:
        words += _words(_value(lines[i], i == first)[1])
    return words


def _drop(value, ips):
    """value without the words that are one of ips, each with one separator: the one before it when a word is
    before it on this line, else the one after it."""
    pieces = re.split(r"([\s,]+)", value)   # words at even indexes ('' at the ends), separators at odd ones
    for k in range(0, len(pieces), 2):
        if pieces[k] and any(_same(pieces[k], ip) for ip in ips):
            before = any(pieces[m] for m in range(0, k, 2))
            pieces[k] = ""
            if before and pieces[k - 1]:
                pieces[k - 1] = ""
            elif k + 1 < len(pieces):
                pieces[k + 1] = ""
    return "".join(pieces)


def _own_comment(ip):
    """The comment line an add wrote above the parameter for ip: '# 198.51.100.3   Payroll app  [added DATE by WHO]'."""
    return re.compile(r"^#[ \t]+%s(/32)?[ \t].*" % re.escape(ip) + STAMP)


def _rejects(line):
    """A map line that refuses its key (REJECT, DISCARD, 554 ...): the address is not one that may relay."""
    return any(NOT_ALLOWED.match(w) for w in _words(line.strip())[1:])


def _file_entries(lines, refused=False):
    """{index: first word} of every address line of a list file that lets its address relay (comment and blank lines,
    and a line that refuses, are not entries). refused: the lines that refuse instead."""
    return {i: line.split()[0] for i, line in enumerate(lines)
            if line.strip() and not line.strip().startswith("#") and _rejects(line) == refused}


def _networks(line, all_words=False):
    """(network, word) the line lets relay. A list file: a map's key, or every address on a mynetworks-file line;
    a map line that rejects lets nothing relay. all_words: every word is an address or a subnet (main.cf)."""
    text = line.strip()
    if not text or text.startswith("#"):
        return []
    words = _words(text)
    if not all_words and _rejects(text):
        return []
    found = []
    for i, w in enumerate(words):
        if FULL.match(w):
            found.append((ipaddress.ip_network(w, strict=False), w))
        elif i == 0 and not all_words and SHORT.match(w):
            parts = w.split(".")
            found.append((ipaddress.ip_network("%s/%d" % (".".join(parts + ["0"] * (4 - len(parts))), 8 * len(parts))), w))
    return found


def _comment_line(req, ip, style):
    """main_cf: '# 198.51.100.3       Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]' - the address first, in
    a column, so the lines above the parameter read as a table. file (the address is on the next line):
    '# Payroll app (svc-payroll)  [added 2026-10-22 by jdoe]'."""
    stamp = "[added %s by %s]" % (req.get("date", ""), req.get("by", ""))
    if style == "main_cf":
        return "# %-18s %s  %s" % (ip, req.get("comment", ""), stamp)
    return "# %s  %s" % (req.get("comment", ""), stamp)


def _bad_request(req, ips):
    """A line break anywhere it would be written means the request is not one the plan job made."""
    fields = [req.get("date", ""), req.get("by", ""), req.get("comment", "")] + list(ips)
    return any(re.search(r"[\r\n]", str(f)) for f in fields)


# ---- the one-column layout (playbooks/mail_relay_format.yml) -------------------------------------------------------
# mynetworks =
#     127.0.0.0/8
#     192.0.2.0/24
#
#     # web01.example.mil  Payroll [AAP]
#     198.51.100.7
#
#     # Scanner, room 210  [added 2026-10-22 by jdoe]
#     198.51.100.9
# One address or subnet per line; anything not an IPv4 address first (as it was), then the subnets, then the single
# addresses, each in number order; an address's comment lines just above it (Postfix skips comment and blank lines
# inside a parameter: postconf reads "127.0.0.0/8 192.0.2.0/24 198.51.100.7 198.51.100.9").

INDENT = "    "
# the end of a comment line a job of this repository wrote: an add's [added DATE by WHO], the format's [AAP] (before
# 0.59.0: [AAP mail relay format DATE job N] - still known as the format's, so mail_relay_format_refresh rewrites it)
FORMAT_STAMP = re.compile(r"\[AAP(?: mail relay format [^\]\r\n]*)?\][ \t]*$")
OUR_STAMP = re.compile(r"(%s|\[AAP(?: mail relay format [^\]\r\n]*)?\][ \t]*$)" % STAMP)


def _kind(word):
    """0: not an IPv4 address (a map, $parameter, [::1]/128, a host name), 1: a subnet, 2: one address."""
    if not FULL.match(word):
        return 0
    return 1 if "/" in word and not word.endswith("/32") else 2


def _order(word):
    net = ipaddress.ip_network(word, strict=False)
    return (int(net.network_address), net.prefixlen)


def _entries(lines, first, more):
    """The parameter's value as entries: ([comment lines inside it that belong to no one address], [{'word', 'comments'}]).
    Comment lines just above a line with one word (no blank line between) are that word's; others are kept at the
    top of the list."""
    top, entries, pending = [], [], []
    last = (more or [first])[-1]
    for i in range(first, last + 1):
        body = _split(lines[i])[0]
        s = body.strip()
        if i != first and (not s or s.startswith("#")):
            if s.startswith("#"):
                pending.append(s)
            elif pending:
                top += pending
                pending = []
            continue
        words = _words(_value(lines[i], i == first)[1])
        if len(words) == 1:
            entries.append({"word": words[0], "comments": pending})
        else:
            top += pending
            entries += [{"word": w, "comments": []} for w in words]
        pending = []
    return top + pending, entries


def _is_column(lines, first, more):
    """Is the list in the one-column layout: nothing after '=' and one word on each of its lines?"""
    if _words(_value(lines[first], True)[1]) or not more:
        return False
    return all(len(_words(_split(lines[i])[0])) == 1 for i in more)


def _blank_between(lines, first, more):
    """Does the list have blank lines between its entries (the format's mail_relay_format_blank_lines)?"""
    return any(not lines[i].strip() for i in range(first + 1, (more or [first])[-1]))


def _render(param, top, entries, blank, brk):
    """The parameter's lines in the one-column layout."""
    def key(e):
        k = _kind(e["word"])
        return (0, 0, 0) if k == 0 else (k,) + _order(e["word"])
    out = ["%s =" % param] + [INDENT + c for c in top]
    ordered = [e for e in entries if _kind(e["word"]) == 0] + sorted([e for e in entries if _kind(e["word"])], key=key)
    for e in ordered:
        if blank and _kind(e["word"]) == 2 and len(out) > 1:
            out.append("")
        out += [INDENT + c for c in e["comments"]] + [INDENT + e["word"]]
    return [x + brk for x in out]


def _clean(text, most=120):
    """Text for a comment line: one line, no [ ] (they end the stamp), at most most characters."""
    text = re.sub(r"[\[\]\r\n\t]+", " ", str(text or ""))
    return re.sub(r" {2,}", " ", text).strip()[:most].strip()


def _old_table(lines, first, words):
    """{index: (address, 'COMMENT  [added DATE by WHO]')} of the comment lines an add wrote above the parameter
    (before the one-column layout) for an address on the list."""
    found = {}
    rx = re.compile(r"^#[ \t]+([0-9.]+)(?:/32)?[ \t]+(.*%s)" % STAMP)
    for i in range(first):
        m = rx.match(_split(lines[i])[0])
        if m and SHAPE.match(m.group(1)) and any(_same(w, m.group(1)) for w in words):
            found[i] = (m.group(1), m.group(2).strip())
    return found


def mail_relay_singles(text, param="mynetworks"):
    """The single addresses on the list (no subnet) - the ones a name is looked up for - and which have a comment
    already: {'ips': [...], 'named': [...]} (named: a comment line of their own, or an add's line above the list)."""
    lines = str(text or "").splitlines(True)
    first, more = _logical(lines, param)
    if first is None:
        return {"ips": [], "named": []}
    top, entries = _entries(lines, first, more)
    words = [e["word"] for e in entries]
    old = {ip for ip, _ in _old_table(lines, first, words).values()}
    ips, named = [], []
    for e in entries:
        if _kind(e["word"]) != 2:
            continue
        ip = e["word"][:-3] if e["word"].endswith("/32") else e["word"]
        if ip not in ips:
            ips.append(ip)
        if (e["comments"] or ip in old) and ip not in named:
            named.append(ip)
    return {"ips": sorted(ips, key=_order), "named": sorted(named, key=_order)}


def mail_relay_getent(stdout):
    """{address: name} from the lookups on the relay ('198.51.100.7 web01.example.mil' a line; '-' = no name)."""
    out = {}
    for line in str(stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and SHAPE.match(parts[0]) and parts[1] != "-":
            out[parts[0]] = parts[1].rstrip(".")
    return out


def mail_relay_service(folder):
    """The folder the VM is in, below its datacenter's VM folder: '/DC1/vm/Finance/Payroll' -> 'Payroll' ('' at the top)."""
    parts = [p for p in str(folder or "").split("/") if p]
    if len(parts) >= 2 and parts[1] == "vm":
        parts = parts[2:]
    elif parts:
        parts = parts[1:]
    return parts[-1] if parts else ""


def mail_relay_names(ips, dns=None, vms=None):
    """{address: {'name', 'vm', 'service', 'how'}} for the addresses: the name DNS gave it (dns: {address: name}), the
    VM that has it (by its IP address in VMware Tools, else by the DNS name as the VM's name or guest host name;
    templates left out; several: the one powered on; still several in different folders: no service) and the VM's
    folder as its service. An address with neither is left out."""
    dns = dns or {}
    vms = [v for v in (vms or []) if not v.get("template")]
    out = {}
    for ip in ips or []:
        name = dns.get(ip, "")
        found = [v for v in vms if ip in (v.get("ips") or []) or v.get("ip") == ip]
        how = "IP address"
        if not found and name:
            short = name.split(".")[0].lower()
            found = [v for v in vms if str(v.get("name", "")).lower() in (name.lower(), short)
                     or str(v.get("hostname", "")).lower() in (name.lower(), short)]
            how = "DNS name"
        if len(found) > 1:
            on = [v for v in found if v.get("power") == "poweredOn"]
            found = on or found
        folders = {v.get("folder", "") for v in found}
        vm = found[0] if found else {}
        service = mail_relay_service(vm.get("folder", "")) if len(folders) == 1 else ""
        if not name and not vm:
            continue
        out[ip] = {"name": name or vm.get("name", ""), "vm": vm.get("name", "") if len(found) == 1 else
                   ", ".join(sorted(v.get("name", "") for v in found)), "service": service,
                   "how": (how if vm else "")}
    return out


def mail_relay_format(text, names=None, param="mynetworks", blank=True, refresh=False):
    """main.cf with the list in the one-column layout. names: {address: {'name', 'service'}} (mail_relay_names) - a
    comment line '# NAME  SERVICE [AAP]' for an address with no comment yet (refresh: also in place of one the
    format wrote before); an add's comment line above the list goes onto its address; comment lines inside the list
    stay (above their address, or at the top). Every other line of the file stays as it is, and the list keeps
    exactly its words (only their order changes): else nothing is changed and problem says why.
    {'text', 'rewrite' (it differs), 'lines' (the new list), 'words' (as Postfix reads it after), 'named' (comments
    written), 'moved' (an add's comments moved), 'problem'}."""
    names = names or {}
    out = {"text": str(text or ""), "rewrite": False, "lines": [], "words": [], "named": [], "moved": [], "problem": ""}
    lines = str(text or "").splitlines(True)
    first, more = _logical(lines, param)
    if first is None:
        out["problem"] = "no '%s =' line in it: the list is not there (mail_relay_file, mail_relay_parameter)." % param
        return out
    before = _main_cf_words(lines, first, more)
    top, entries = _entries(lines, first, more)
    old = _old_table(lines, first, [e["word"] for e in entries])
    for e in entries:
        if _kind(e["word"]) != 2:
            continue
        ip = e["word"][:-3] if e["word"].endswith("/32") else e["word"]
        mine = [i for i, (o, _) in old.items() if o == ip]
        if mine and not e["comments"]:
            e["comments"] = ["# " + old[i][1] for i in mine]
            out["moved"].append(ip)
        if ip in names and (not e["comments"] or (refresh and all(FORMAT_STAMP.search(c) for c in e["comments"]))):
            n = names[ip]
            label = "  ".join(x for x in (_clean(n.get("name", "")), _clean(n.get("service", ""))) if x)
            if label:
                new = ["# %s [AAP]" % label]
                if new != e["comments"]:
                    e["comments"] = new
                    out["named"].append(ip)
    brk = _split(lines[first])[1] or "\n"
    last = (more or [first])[-1]
    tail = lines[last + 1:]
    block = _render(param, top, entries, blank, brk)
    if not _split(lines[last])[1]:
        block[-1] = block[-1].rstrip("\r\n")       # the file ended there with no line break: so it does still
    head = [line for i, line in enumerate(lines[:first]) if i not in old]
    new_lines = head + block + tail
    new_first, new_more = _logical(new_lines, param)
    after = _main_cf_words(new_lines, new_first, new_more) if new_first is not None else []
    if sorted(after) != sorted(before):
        out["problem"] = "the list would not keep exactly its words (%d before, %d after): nothing changed." % (
            len(before), len(after))
        return out
    out["text"] = "".join(new_lines)
    out["rewrite"] = out["text"] != str(text or "")
    out["lines"] = [x.rstrip("\r\n") for x in block]
    out["words"] = after
    return out


# ---- the change ----------------------------------------------------------------------------------------------------

def _edit_column(out, lines, first, more, req, ips, param):
    """mail_relay_edit on a list in the one-column layout: an add puts each address on a line of its own in its
    place in the order, its comment line '# COMMENT  [added DATE by WHO]' just above it; a remove takes the address's
    line and the comment lines just above it (and an add's line above the list, from before the layout)."""
    top, entries = _entries(lines, first, more)
    words = [e["word"] for e in entries]
    blank = _blank_between(lines, first, more)
    gone = set()
    if req.get("action") == "add":
        todo = [ip for ip in ips if not any(_same(w, ip) for w in words)]
        stamp = "[added %s by %s]" % (req.get("date", ""), req.get("by", ""))
        new = [{"word": ip, "comments": ["# %s  %s" % (req.get("comment", ""), stamp)]} for ip in todo]
        entries += new
        out["lines"] = [x for e in new for x in e["comments"] + [e["word"]]]
    else:
        todo = [ip for ip in ips if any(_same(w, ip) for w in words)]
        taken = [e for e in entries if any(_same(e["word"], ip) for ip in todo)]
        entries = [e for e in entries if e not in taken]
        rxs = [_own_comment(ip) for ip in todo]
        gone = {i for i in range(first) if any(rx.match(lines[i]) for rx in rxs)}
        out["lines"] = ([lines[i].strip() for i in sorted(gone)]
                        + [x for e in taken for x in e["comments"] + ["%s (its line in %s)" % (e["word"], param)]])
    out["done"] = todo
    if todo:
        last = (more or [first])[-1]
        block = _render(param, top, entries, blank, _split(lines[first])[1] or "\n")
        if not _split(lines[last])[1]:
            block[-1] = block[-1].rstrip("\r\n")
        lines = [x for i, x in enumerate(lines[:first]) if i not in gone] + block + lines[last + 1:]
    new_first, new_more = _logical(lines, param)
    out["words"] = _main_cf_words(lines, new_first, new_more) if new_first is not None else []
    out["text"] = "".join(lines)
    return out


def mail_relay_edit(text, req, ips=None, style="main_cf", param="mynetworks", suffix=""):
    """The list after the request. {'text': the whole new file, 'lines': what the approver reads (the lines added,
    or removed), 'done': the addresses changed, 'words': the parameter's value after (main_cf), 'problem': ''}.
    ips: the addresses to change here (default: all of the request's). main_cf: an add puts '# IP  COMMENT  [added
    DATE by WHO]' above the parameter's line and ', IP' at the end of its value; a remove takes IP out of the
    value and the comment line an add wrote for it. file: an add appends the comment line and the address (and
    suffix); a remove takes the address's line and the comment line an add wrote above it."""
    req = req or {}
    action = req.get("action")
    ips = list(req.get("ips") or []) if ips is None else list(ips)
    out = {"text": str(text or ""), "lines": [], "done": [], "words": [], "problem": ""}
    if _bad_request(req, ips) or style not in STYLES or action not in ("add", "remove"):
        out["problem"] = "not a request of the plan job: nothing changed."
        return out
    lines = str(text or "").splitlines(True)
    if style == "main_cf":
        first, more = _logical(lines, param)
        if first is None:
            out["problem"] = "no '%s =' line in it." % param
            return out
        words = _main_cf_words(lines, first, more)
        brk = _split(lines[first])[1] or "\n"
        if _is_column(lines, first, more):
            return _edit_column(out, lines, first, more, req, ips, param)
        if action == "add":
            todo = [ip for ip in ips if not any(_same(w, ip) for w in words)]
            if todo:
                last = (more or [first])[-1]
                prefix, value, end = _value(lines[last], last == first)
                value = value.rstrip(" \t")
                sep = " " if not words or value.endswith(",") or not value.strip() else ", "
                lines[last] = prefix + value + sep + ", ".join(todo) + end
                comments = [_comment_line(req, ip, style) for ip in todo]
                lines[first:first] = [c + brk for c in comments]
                out["lines"] = comments + ["%s = %s%s" % (param, "..., " if words else "", ", ".join(todo))]
        else:
            todo = [ip for ip in ips if any(_same(w, ip) for w in words)]
            if todo:
                gone = set()
                for i in [first] + more:
                    prefix, value, end = _value(lines[i], i == first)
                    new = _drop(value, todo)
                    if i != first and not new.strip():
                        gone.add(i)                      # a continuation line left with nothing on it
                    elif i == first and not new.strip():
                        lines[i] = prefix + end
                    else:
                        lines[i] = prefix + new + end
                rxs = [_own_comment(ip) for ip in todo]
                comments = [i for i, line in enumerate(lines) if any(rx.match(line) for rx in rxs)]
                gone.update(comments)
                out["lines"] = [lines[i].strip() for i in comments] + ["%s (out of the %s line)" % (ip, param) for ip in todo]
                lines = [line for i, line in enumerate(lines) if i not in gone]
        out["done"] = todo
        new_first, new_more = _logical(lines, param)
        out["words"] = _main_cf_words(lines, new_first, new_more) if new_first is not None else []
    else:
        entries = _file_entries(lines)
        if action == "add":
            todo = [ip for ip in ips if not any(_same(w, ip) for w in entries.values())]
            added = []
            for ip in todo:
                added += [_comment_line(req, ip, style), ip + suffix]
            if added:
                body = str(text or "")
                lines = [body + ("\n" if body and not body.endswith("\n") else "") + "".join(a + "\n" for a in added)]
            out["lines"] = added
        else:
            todo = [ip for ip in ips if any(_same(w, ip) for w in entries.values())]
            gone = set()
            for i, w in entries.items():
                if any(_same(w, ip) for ip in todo):
                    gone.add(i)
                    if i > 0 and FILE_COMMENT.match(lines[i - 1]):
                        gone.add(i - 1)
            out["lines"] = [lines[i].strip() for i in sorted(gone)]
            lines = [line for i, line in enumerate(lines) if i not in gone]
        out["done"] = todo
    out["text"] = "".join(lines)
    return out


def _who(lines, i, ip, style, param="mynetworks"):
    """'COMMENT, added DATE by WHO' of an entry, from the comment line an add wrote - or the format's 'NAME  SERVICE'
    ('' = none)."""
    line = ""
    if style == "main_cf":
        rx = _own_comment(ip)
        line = next((x for x in lines if rx.match(x)), "")
        first, more = _logical(lines, param)
        if not line and first is not None:
            mine = [c for e in _entries(lines, first, more)[1] if _same(e["word"], ip) for c in e["comments"]]
            line = next((c for c in mine if OUR_STAMP.search(c)), "")
            m = FORMAT_STAMP.search(line)
            if m:
                return line[:m.start()].lstrip("# \t").rstrip()
    elif i is not None and i > 0 and FILE_COMMENT.match(lines[i - 1]):
        line = lines[i - 1]
    m = re.match(r"^#[ \t]+(?:%s(?:/32)?[ \t]+)?(.*?)[ \t]*\[(added [^\]]*)\]" % re.escape(ip) if style == "main_cf"
                 else r"^#[ \t]+(.*?)[ \t]*\[(added [^\]]*)\]", line.strip())
    return "%s, %s" % (m.group(1), m.group(2)) if m else ""


def mail_relay_plan(text, req, style="main_cf", param="mynetworks", suffix="", postmap=False):
    """What a request changes on one relay: {'change': add | remove | none, 'todo': the addresses to change here,
    'already': on the list already (add) or not on it (remove), 'lines': the lines to be added or removed, 'notes':
    for the approver (an address a subnet on the list lets relay already, or still after a removal; addresses inside a
    subnet), 'problem': why this relay cannot take the change ('' = it can)}."""
    req = req or {}
    action = req.get("action")
    ips = list(req.get("ips") or [])
    out = {"change": "none", "todo": [], "already": [], "lines": [], "notes": [], "problem": ""}
    if style not in STYLES:
        out["problem"] = "mail_relay_style is '%s': main_cf or file." % style
        return out
    lines = str(text or "").splitlines(True)
    if style == "main_cf":
        first, more = _logical(lines, param)
        if first is None:
            out["problem"] = ("no '%s =' line in it: the list is not there (mail_relay_file, mail_relay_parameter, "
                              "or mail_relay_style: file for a list file)." % param)
            return out
        words = _main_cf_words(lines, first, more)
        where = {w: None for w in words}
        nets = [(n, w, w) for w in words for n, _ in _networks(w, all_words=True)]
    else:
        entries = _file_entries(lines)
        words = list(entries.values())
        where = {w: i for i, w in entries.items()}
        nets = [(n, w, line.strip()) for line in lines for n, w in _networks(line)]
    # a line that refuses the address (REJECT ...) is never changed: an add would not let it relay (its first line
    # counts), a remove would let it relay
    refused = {}
    if style == "file":
        for i, w in _file_entries(lines, refused=True).items():
            for ip in ips:
                if _same(w, ip):
                    refused.setdefault(ip, lines[i].strip())
    listed = [ip for ip in ips if any(_same(w, ip) for w in words) or (action == "add" and ip in refused)]
    out["todo"] = [ip for ip in ips if ip not in listed] if action == "add" else listed
    out["already"] = listed if action == "add" else [ip for ip in ips if ip not in listed]
    notes = []
    for ip in out["already"]:
        if ip in refused:
            notes.append("%s has a line that refuses it (%s): it cannot relay, and that line is left as it is. %s"
                         % (ip, refused[ip], "Change that line by hand to let it relay." if action == "add" else
                            "It is not on the list of those that may relay."))
        elif action == "add":
            i = next((where[w] for w in words if _same(w, ip)), None)
            who = _who(lines, i, ip, style, param)
            notes.append("%s is on the list already%s - left as it is." % (ip, (" (%s)" % who) if who else ""))
        else:
            notes.append("%s is not on the list." % ip)
    for ip in ips:
        want = ipaddress.ip_network(ip, strict=False)
        covers = list(dict.fromkeys(s for n, w, s in nets if want.subnet_of(n) and not (n == want and _same(w, ip))))
        inside = list(dict.fromkeys(s for n, w, s in nets if n.subnet_of(want) and n != want))
        if covers and (ip in out["todo"] or action == "remove") and ip not in refused:
            c = ", ".join(covers)
            if action == "add":
                notes.append("%s can relay already: it is inside %s, on the list. Adding it gives it a line of its own "
                             "with the comment; %s stays." % (ip, c, c))
            elif ip in out["todo"]:
                notes.append("%s can STILL relay after this: it is inside %s, which stays on the list. To stop it, "
                             "remove %s too (every address in it stops)." % (ip, c, c))
            else:
                notes.append("%s has no line of its own, but it can relay: it is inside %s. To stop it, remove %s "
                             "(every address in it stops)." % (ip, c, c))
        if inside and ip in out["todo"]:
            if action == "add":
                notes.append("Inside %s and on the list already, with lines of their own (they stay): %s."
                             % (ip, ", ".join(inside)))
            else:
                notes.append("These addresses inside %s have lines of their own and can still relay after this: %s."
                             % (ip, ", ".join(inside)))
        if action == "remove" and ip in out["todo"] and want.subnet_of(LOOPBACK):
            notes.append("%s is the relay server itself: programs on it that send mail through it stop being let "
                         "through." % ip)
    out["notes"] = notes
    if out["todo"]:
        edit = mail_relay_edit(text, req, out["todo"], style, param, suffix)
        out["lines"] = edit["lines"]
        out["change"] = action
        if edit["problem"]:
            out["problem"] = edit["problem"]
        elif style == "main_cf" and action == "remove" and not edit["words"]:
            out["problem"] = ("removing %s would leave %s empty: then no client may relay through it, not even its own "
                              "programs. Nothing to approve." % (", ".join(out["todo"]), param))
        elif style == "file" and _true(postmap) and action == "add" and any("/" in ip for ip in out["todo"]):
            out["problem"] = ("a subnet cannot go into this list: it is a hash/lmdb map (mail_relay_postmap), and such "
                              "a map never matches 198.51.100.0/24 written as a key. Use a cidr: table "
                              "(mail_relay_postmap: false), the mynetworks line, or add the addresses one by one.")
    return out


def mail_relay_lookup_rows(ips, dns=None, names=None):
    """The table of every address looked up (format job): [address, DNS name, VM, folder, found by] - '-' where
    nothing was found, so an address with no name shows too."""
    dns, names = dns or {}, names or {}
    rows = []
    for ip in sorted(ips or [], key=_order):
        n = names.get(ip) or {}
        rows.append([ip, dns.get(ip) or "-", n.get("vm") or "-", n.get("service") or "", n.get("how") or ""])
    return rows


def mail_relay_vcenter(vms=None, vcenters=None, ips=None, names=None, read=True):
    """What vCenter gave the format job, for its report: {'vms': VMs read (templates left out), 'matched': addresses
    that matched a VM, 'by_ip', 'by_name', 'folder': with a folder, 'lines': one per vCenter and what to do when it
    found nothing}. read: false = vCenter was not read (no credential, mail_relay_format_vmware: false)."""
    vms = [v for v in (vms or []) if not v.get("template")]
    names = names or {}
    ips = list(ips or [])
    found = [names[ip] for ip in ips if (names.get(ip) or {}).get("vm")]
    out = {"vms": len(vms), "matched": len(found), "by_ip": sum(1 for n in found if n.get("how") == "IP address"),
           "by_name": sum(1 for n in found if n.get("how") == "DNS name"),
           "folder": sum(1 for n in found if n.get("service")), "lines": []}
    if not read or not ips:
        return out
    for vc in vcenters or []:
        name = str(vc.get("name") or "")
        if vc.get("ok"):
            out["lines"].append("vCenter %s: %d VM(s) read%s." % (name, sum(1 for v in vms if v.get("vcenter") == name),
                                                                 (" (%s)" % vc["note"]) if vc.get("note") else ""))
        else:
            out["lines"].append("vCenter %s: not read - %s" % (name, str(vc.get("error") or "")[:200]))
    out["lines"].append("%d of %d address(es) looked up matched a VM (%d by its IP address, %d by its DNS name); %d "
                        "of them in a folder." % (out["matched"], len(ips), out["by_ip"], out["by_name"], out["folder"]))
    if not vms:
        out["lines"].append("No VM was read: the vCenter account sees none. Give it the Read-only role at the top of "
                            "vCenter with Propagate to children ticked (docs/VMWARE.md), and check vmware_datacenter.")
    elif not found:
        out["lines"].append("None of the VMs has these addresses (VMware Tools reports a VM's addresses; a VM without "
                            "it running shows none). Mail from behind NAT or a load balancer, or from servers that are "
                            "not VMs, cannot be matched.")
    elif out["folder"] < out["matched"]:
        out["lines"].append("%d matched VM(s) sit at the top of their datacenter (no folder), or two VMs in different "
                            "folders matched (no guess)." % (out["matched"] - out["folder"]))
    return out


def mail_relay_format_section(result):
    """A report section with a relay's new list (format job)."""
    result = result or {}
    return {"title": "The list on %s" % result.get("host", ""), "lines": list(result.get("lines") or [])}


def mail_relay_notes(relays):
    """The approver's notes of all relays: once when every relay that could be read has the same, else 'relay: note'.
    [] = none."""
    read = [r for r in (relays or []) if not r.get("problem")]
    if not any(r.get("notes") for r in read):
        return []
    if len({tuple(r.get("notes") or []) for r in read}) == 1:
        return list(read[0].get("notes") or [])
    return ["%s: %s" % (r.get("host", ""), n) for r in read for n in (r.get("notes") or [])]


# ---- the addresses that sent nothing (playbooks/mail_relay_unused.yml) ------------------------------------------------

def mail_relay_list_words(text, style="main_cf", param="mynetworks"):
    """Every word of a relay's list, as Postfix reads it: main_cf - the parameter's words; file - each line's address
    (a map line that rejects is left out). None when the list is not there."""
    lines = str(text or "").splitlines(True)
    if style == "file":
        return [w for line in lines for _, w in _networks(line)]
    first, more = _logical(lines, param)
    return None if first is None else _main_cf_words(lines, first, more)


def _epoch(text):
    """'2026-11-03T06:15:01Z' -> seconds (None for '')."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)Z$", str(text or ""))
    if not m:
        return None
    return calendar.timegm(tuple(int(x) for x in m.groups()))


def _day(text):
    return str(text or "")[:10] or "-"


def mail_relay_unused(relays, opts=None):
    """Which addresses on the relays' lists sent no mail in the last opts.days days, by every relay's logs together.
    relays: [{host, words (mail_relay_list_words), logs (site_mail_relay_clients), problem}]. opts: days, now (seconds),
    keep (addresses or subnets never proposed), max (the most proposed at once), accept_short (propose even when a
    relay's logs do not go back days days), time_zone (report_time_zone, 1.54.0: the cutoff and each address's last mail as
    the report shows them; "" = UTC). Only single addresses are proposed for removal; a subnet that sent nothing
    is listed only. -> {cutoff, days, covered, coverage, rows, row_status, unused, unused_subnets, later, used,
    problems, notes}"""
    o = opts or {}
    days = float(o.get("days") or 183)
    now = float(o.get("now") or 0)
    cutoff = now - days * 86400
    most = int(o.get("max") or 50)
    tz = _tm.zone(o.get("time_zone"))[0]
    keep = []
    for k in o.get("keep") or []:
        try:
            keep.append(ipaddress.ip_network(str(k).strip(), strict=False))
        except ValueError:
            pass
    relays = list(relays or [])
    problems = [r for r in relays if r.get("problem")]
    ok = [r for r in relays if not r.get("problem")]
    coverage = []
    for r in ok:
        logs = r.get("logs") or {}
        oldest = _epoch(logs.get("oldest"))
        back = (now - oldest) / 86400.0 if oldest is not None else 0
        errs = [x["source"] + ": " + x["error"] for x in logs.get("sources") or [] if x.get("error")]
        coverage.append({"host": r.get("host", ""), "oldest": logs.get("oldest", ""), "newest": logs.get("newest", ""),
                         "days_back": int(round(back)), "ok": oldest is not None and oldest <= cutoff + 86400,
                         "sources": len(logs.get("sources") or []), "errors": errs})
    covered = bool(ok) and all(c["ok"] for c in coverage)
    # every client of every relay: address -> the newest mail, messages since the cutoff, connections only
    clients = {}
    for r in ok:
        for ip, c in ((r.get("logs") or {}).get("clients") or {}).items():
            a = clients.setdefault(ip, {"last_sent": None, "sent": 0, "connects": 0, "last_connect": None})
            ls, lc = _epoch(c.get("last_sent")), _epoch(c.get("last_connect"))
            if ls is not None and (a["last_sent"] is None or ls > a["last_sent"]):
                a["last_sent"] = ls
            a["sent"] += int(c.get("sent") or 0)
            a["connects"] += int(c.get("connects") or 0)
            if lc is not None and (a["last_connect"] is None or lc > a["last_connect"]):
                a["last_connect"] = lc
    nets = {}
    for ip in clients:
        try:
            nets[ip] = ipaddress.ip_address(ip)
        except ValueError:
            pass
    listed = {}
    for r in ok:
        for w in r.get("words") or []:
            listed.setdefault(w[:-3] if w.endswith("/32") else w, []).append(r.get("host", ""))
    hosts = [r.get("host", "") for r in ok]

    def when(t):
        return _tm.local(time_text(t), tz) if t else ""
    rows, status, unused, unused_nets, used = [], [], [], [], 0
    for w in sorted(listed, key=lambda x: (_kind(x) == 0, _order(x) if _kind(x) else (0, 0), x)):
        on = "all" if sorted(set(listed[w])) == sorted(hosts) else ", ".join(sorted(set(listed[w])))
        k = _kind(w)
        if k == 0:
            rows.append([w, "not an address", on, "", "", "", "kept: not an address"])
            status.append("")
            continue
        net = ipaddress.ip_network(w, strict=False)
        inside = [ip for ip, a in nets.items() if a in net]
        last = max([clients[ip]["last_sent"] for ip in inside if clients[ip]["last_sent"]] or [None])
        recent = [ip for ip in inside if clients[ip]["last_sent"] and clients[ip]["last_sent"] >= cutoff]
        conn = sum(clients[ip]["connects"] for ip in inside)
        kind = "subnet" if k == 1 else "address"
        last_text = when(last) or "never in the logs"
        conn_text = ("%d connection(s)%s" % (conn, "" if last else ", never mail")) if conn else ""
        if net.subnet_of(LOOPBACK):
            verdict, st = "kept: the relay itself", ""
        elif any(net.subnet_of(x) for x in keep):
            verdict, st = "kept: mail_relay_unused_keep", ""
        elif recent:
            verdict, st = "in use" + ((" (%d address(es) in it)" % len(recent)) if k == 1 else ""), ""
            used += 1
        elif k == 1:
            verdict, st = "UNUSED subnet - listed only, not removed", "warning"
            unused_nets.append(w)
        else:
            verdict, st = "UNUSED", "warning"
            unused.append(w)
        rows.append([w, kind, on, last_text, sum(clients[ip]["sent"] for ip in inside), conn_text, verdict])
        status.append(st)
    later = unused[most:]
    unused = unused[:most]
    notes = []
    if not covered and ok:
        short = [c for c in coverage if not c["ok"]]
        notes.append("The logs do not go back %d days on %s: %s. An address may have sent mail before that and be "
                     "called unused. Keep the mail logs longer (logrotate: /etc/logrotate.d/syslog), or set "
                     "mail_relay_unused_days to what they cover." % (
                         days, ", ".join(c["host"] for c in short),
                         "; ".join("%s back to %s (%d days)" % (c["host"], _day(c["oldest"]), c["days_back"])
                                   if c["oldest"] else "%s none read" % c["host"] for c in short)))
    if later:
        notes.append("%d more unused address(es) are not in this plan (mail_relay_unused_max: %d): the next run takes "
                     "them - %s." % (len(later), most, mail_relay_short(later, 5)))
    if unused_nets:
        notes.append("Subnets that sent nothing are listed, not removed: remove one with the Mail relay - remove an IP "
                     "workflow if it is no longer needed.")
    return {"cutoff": _tm.local(time_text(cutoff), tz), "days": int(days), "covered": covered, "coverage": coverage, "rows": rows,
            "row_status": status, "unused": unused, "unused_subnets": unused_nets, "later": later, "used": used,
            "problems": [{"host": r.get("host", ""), "problem": r.get("problem", "")} for r in problems], "notes": notes}


def mail_relay_coverage_row(c):
    """A row of 'How far back the logs go': [relay, oldest, newest, days back, covers, logs read (files, journal), errors]."""
    c = c or {}
    return [c.get("host", ""), _day(c.get("oldest")), _day(c.get("newest")), c.get("days_back", 0),
            "yes" if c.get("ok") else "NO", c.get("sources", 0), "; ".join(c.get("errors") or [])[:300]]


def time_text(t):
    """seconds -> '2026-11-03 06:15 UTC'."""
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(t))


class FilterModule(object):
    def filters(self):
        return {"mail_relay_ip_problem": mail_relay_ip_problem, "mail_relay_ips": mail_relay_ips,
                "mail_relay_size": mail_relay_size, "mail_relay_short": mail_relay_short,
                "mail_relay_edit": mail_relay_edit, "mail_relay_plan": mail_relay_plan,
                "mail_relay_notes": mail_relay_notes, "mail_relay_singles": mail_relay_singles,
                "mail_relay_getent": mail_relay_getent, "mail_relay_names": mail_relay_names,
                "mail_relay_format": mail_relay_format, "mail_relay_format_section": mail_relay_format_section,
                "mail_relay_lookup_rows": mail_relay_lookup_rows, "mail_relay_vcenter": mail_relay_vcenter,
                "mail_relay_list_words": mail_relay_list_words, "mail_relay_unused": mail_relay_unused,
                "mail_relay_coverage_row": mail_relay_coverage_row}
