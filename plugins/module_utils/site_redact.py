# -*- coding: utf-8 -*-
"""1.77.0: one redaction for every text a STIG answer keys job takes from a host and shows - the cross-check's evidence
(site_stig_answer_reads, sent to ACT and shown in the report), the trial's "What it read" (site_stig_answer_host), the
comments and Write-Verbose text of "try my answer file" (site_stig_scan rule_texts), and every error text those jobs
show. It runs on the host, before the text leaves it, and again in the filters that build the report, workbook and email.

Two layers (1.77.0d):

1. Precise masking, for the shapes it knows - the value goes, the name and the line stay, so the evidence still reads:
   - crypt hashes ($1$, $2b$, $5$, $6$, $y$, $argon2id$ ...): "$<hash removed>"; GRUB 2 hashes (grub.pbkdf2...);
   - a GRUB "password USER PASS" or "password_pbkdf2 USER HASH" line;
   - the hash of a shadow-like line (name:hash:...); a locked or empty password (!, !!, *, "") is kept - it is evidence;
   - PEM private keys (a whole BEGIN ... PRIVATE KEY block, or to the end when it was cut);
   - values named password, passwd, pwd, pw, passphrase, secret, token, authtok, credential, community, psk, or
     ending in _key / -key / apikey / _pass / a camelCase Pass (authPass, dbPASS; not bypass): the rest of the line
     after name=value, name: value, name = value; a JSON "name": "value" its string; a "name:" with nothing after it,
     the next line that is not blank or a comment (and the lines after while a [ or { it opened is open); a YAML
     block under the name; <name>...</name>, nested or over several lines;
   - bindpw, rootpw, ldap_default_authtok, rocommunity, rwcommunity, trapcommunity, com2sec, createUser; net-snmp's
     -A and -X pass phrases (on an snmp line, beside an SNMPv3 protocol or level, or both on one line);
     trapsink / trap2sink / informsink communities; chrony's keys (ID TYPE KEY, HEX:...); a reg query value;
     smbpasswd's LM and NT hashes (and any two 32-hex fields joined by a colon);
   - a URL's password (scheme://user:PASSWORD@host); keytab keys (klist -K "(0x...)") and other long 0x hex keys.
   A name may end in one or two digits (wep-key0, psk1).
   1.77.0h: also gshadow hashes, OpenPGP private keys, klist -K's short keys, an snmp command's -c community, an HTTP
   Authorization header and a Bearer token, an option named like a secret (--db-pass x; a quoted value whole), a
   secret's XML attributes (<password value=...>, value= beside key="password"); a value over lines - a [ or { opened
   on its name's line to its close, a YAML value's more indented lines, an XML tag over lines, CDATA and comments.
   A value is left only when it is exactly a marker this module writes (so redacting twice is the same) or empty; a
   value that is one whole crypt or GRUB hash says which kind it was. Those passes run before the hash passes.

2. The backstop: a line that still holds a secret word (password, passwd, passphrase, pwd, secret, token, authtok,
   credential, community, bindpw, rootpw, pbkdf2, keytab, psk, _key, a camelCase Pass, PRIVATE KEY, or a crypt prefix
   like $6$) is replaced as a whole with "<line removed: may hold a secret>" - unless the first layer understood
   every such word on it (a name whose value it removed), the word is only in a file's path (/etc/passwd,
   ssh_host_rsa_key), or the line is a known evidence shape: a passwd or shadow record; nsswitch's "passwd:" sources;
   a PAM line; an audit rule; a setting with a yes/no value (PermitEmptyPasswords no), or a number or a path when its
   name is not a secret's (password 1234 goes); PermitRootLogin prohibit-password; chage -l and passwd -S lines; an
   RPM package name; klist's "Keytab name:"; sssd's cache_credentials = True and ldap_default_authtok_type =
   password; sudoers' Defaults flags (!rootpw) and a rule's tags (NOPASSWD:). A line with a -A or -X value left that
   may be SNMP's goes too (an snmp line, a -u name or -v 3, or nothing but -A and -X; not an iptables rule). When in
   doubt, the line goes. redact(text, backstop=False) leaves it out: for command lines (DISA's own text, not what a
   host printed).
   1.77.0h: the words -key, -pass and a whole pw too (api-key x); a PAM line only when its arguments are PAM options or
   paths; a yes/no only when its name is not a secret's (my_password no goes); sshd's AuthenticationMethods; an
   evidence line with a comment holding no secret's word; a sudoers tag after a comma; a tag's attributes read as
   words of the line.

head(text, limit) cuts a text read only in part at a line's end; run_main(main) is a host module's entry point (an
unexpected error leaves redacted, never as a traceback); cap(text) is how much of a host's text a report shows (20 lines,
2000 characters - answer_keys_evidence: web; the email shows none of it). Shapes known to pass: docs/STIG_ANSWER_KEYS.md,
"Known limits".

A line may start with grep's prefix (path:, 12:, path:12:, path-12-); a dropped line keeps it when it is a path or a
line number. Redact the whole text first, then cut it: a cut inside a value would leave the rest of it bare. Its cost
grows with the text's length, never its square (tests/test_site_redact_table.py times 200 KB of each hard shape, and
1 MB of URL heads).
Standard library only; Python 3.6 too (RHEL 8's platform-python)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import json
import re
from functools import lru_cache, partial

HASH_MARK = "<hash removed>"
MARK = "<removed>"
DROPPED = "<line removed: may hold a secret>"

# 1.77.0h: an OpenPGP one too (BEGIN PGP PRIVATE KEY BLOCK)
_PEM = re.compile(r"-----BEGIN ((?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----.*?(?:-----END \1-----|\Z)", re.S)
_GRUB = re.compile(r"grub\.pbkdf2\.[A-Za-z0-9]+\.\d+\.[A-Fa-f0-9.]+")
_CRYPT = re.compile(r"\$(?:1|2[abxy]?|5|6|7|y|gy|sha1|md5|argon2(?:id|i|d)|pbkdf2(?:-sha\d+)?|scrypt)\$[^\s:'\"]+")
# name:hash:digits: (shadow) - the hash 13+ characters (DES crypt is 13); "!" before it (locked) kept
# 1.77.0h: a name may start with a digit (123svc)
_SHADOW = re.compile(r"(?m)(?<![\w.$-])([A-Za-z0-9_][A-Za-z0-9._-]*\$?):(!*)([^:\s!*][^:\s]{12,}):(\d*):")
_HEXKEY = re.compile(r"\b0x[0-9A-Fa-f]{32,}\b")
# 1.77.0h: klist -K's key, "(0x...)" - a single DES key is 16 hex digits
_KLIST_KEY = re.compile(r"\(0x[0-9A-Fa-f]{16,}\)")
# 1.77.0h: a gshadow record (group:password:admins:members) - its password a hash (a DES crypt is 13 characters)
_GSHADOW = re.compile(r"(?m)(?<![\w.$-])([A-Za-z0-9_][A-Za-z0-9._-]*\$?):(!*)([^:\s!*][^:\s]{12,}):([^:\s]*):([^:\s]*)(?=[ \t]*\r?$)")
# a line as grep shows it - "path:", "path:12:", "12:", or a context line's "path-12-" / "12-" - may come first
_GREP = r"(?:[^\s:]+[:-](?:\d+[:-])?|\d+[:-])?"
# a secret's value is the rest of its line (its last blank left), quoted or not
_REST = r"(\S(?:[^\n]*\S)?)"
_JSON_STR = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"")
_JSON_END = re.compile(r"[ \t\r]*(?:[,}\]]|$)")
_BLANK = re.compile(r"\s*$")
# a secret's name: a word that ends like one. The word is read once, then its end is looked at (a lookbehind for each
# ending) - never tried again from each of its characters, so a long word costs its length, not its square
_NAME_ENDS = ("password", "passwd", "passphrase", "pwd", "pw", "secret", "token", "authtok", "credential", "credentials",
              "[_-]session", "sessionid", "session[_-]id", "session[_-]cookie", "phpsessid",
              "community", "psk", "[_.-]pass", "[A-Za-z0-9][_-]key", "apikey",
              # 1.77.0g: camelCase - Pass after a small letter or a digit (authPass, dbPASS); bypass is no name
              "(?-i:[a-z0-9]P(?:ass|ASS))")
# 1.77.0f: and one or two digits after it (NetworkManager's wep-key0 .. wep-key3, psk1)
_NAMES = r"(?:(?:session(?=\d)|session[ \t]+(?:id|cookie)|[A-Za-z0-9_.-]+(?:" + "|".join("(?<=%s)" % e for e in _NAME_ENDS) + r"))\d{0,2}(?![A-Za-z0-9_.-]))"
_NAME_WORD = re.compile(r"(?i)" + _NAMES)
# a name starts a word: not inside a word, and not after a / (/etc/passwd: is a file, not a name) - unless an = follows
# it (/opt/app/config-7-password=x)
_NAME_START = r"(?:(?<![A-Za-z0-9_./-])|(?<=/)(?=[A-Za-z0-9_.-]+[\"']?[ \t]*=))"
# name=value, name: value, "name": value, name = value ... (the opening and closing quote of the name each optional;
# \" too - JSON inside a string)
_NAMED = re.compile(r"(?i)" + _NAME_START + r"(?P<head>(?P<q>[\"']?)(?P<name>" + _NAMES + r")(?P<cq>\\?[\"']?)[ \t]*(?P<sep>[=:])[ \t]*)")
# 1.77.0h: a value as a command line gives it - quoted (to its closing quote, or the line's end: "a b" is one), else a word
_ARG = r"(\"[^\"\n]*(?:\"|$)|'[^'\n]*(?:'|$)|\S+)"
# 1.77.0h: an option named like a secret, its value after a blank (--db-pass x, -password 'a b'); the = form is _NAMED's
_OPTION = re.compile(r"(?im)(?<!\S)(-" + _NAMES + r"[ \t]+)(?!-|(?:required|requisite|sufficient|optional|include|substack)\b|\[)"
                     + _ARG)                  # not PAM's "-password optional pam_x.so" (a type, then its control)
# a YAML key under a secret's name; what follows its colon is read in _block_kind (indicator, anchor, tag, comment)
_BLOCK = re.compile(r"(?i)([ \t]*(?:-[ \t]+)?)[\"']?" + _NAMES + r"[\"']?[ \t]*:(?P<tail>[^\n]*)$")
_INDICATOR = re.compile(r"[|>][+-]?[0-9]?[+-]?")
# a GRUB password: plain, or password_pbkdf2 USER HASH (not PAM's "password <control> module" line)
# A password reset request in an operator note can otherwise look like a GRUB user and passphrase.
_RESET_NOTE = r"reset[ \t]+(?:requested|completed|failed|pending|required)\b"
_STATUS_NOTE = r"(?:(?:has|had|was|is)[ \t]+)?(?:expired|rotated|revoked|expires)\b"
_PASSWORD_NOTE = r"(?:" + _RESET_NOTE + "|" + _STATUS_NOTE + ")"
_GRUB_ARGS = ("(" + _GREP + r"[ \t]*password(?:_pbkdf2)?[ \t]+(?!(?:required|requisite|sufficient|optional|include|substack)\b|\[)"
              r"\S+[ \t]+)(?!#(?:[ \t]|$))" + _REST)
_GRUB_PLAIN = re.compile(r"(?m)^" + _GRUB_ARGS)
_REVIEW_GRUB = re.compile(r"(?m)^(?![ \t]*password[ \t]+(?i:" + _PASSWORD_NOTE +
                          r")(?=[.!]?[ \t]*$|[ \t]+(?:by|at|on|for|from)\b))" + _GRUB_ARGS)
_COOKIE_HEADER = re.compile(r"(?i)\b(?:set-)?cookie[ \t]*:[^\n]*")
_COOKIE_SESSION = re.compile(r"(?i)(?<![\w.-])(session[ \t]*=[ \t]*)([^\n]+)")
_COOKIE_OBJECT = re.compile(r'(?i)"(?:cookies?|set-cookie)"\s*:\s*\{')
_JSON_PART = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:\s*)?|[{}\[\]]|[^\s"{}\[\]]+')
_DIRECTIVE = re.compile(r"(?im)^(" + _GREP + r"[ \t]*(?:bindpw|rootpw|ldap_default_authtok|r[ow]community6?|trapcommunity)[ \t]+)(?![=:](?:[ \t]|$))" + _REST)
_COM2SEC = re.compile(r"(?im)^(" + _GREP + r"[ \t]*com2sec6?[ \t]+(?:-Cn[ \t]+\S+[ \t]+)?\S+[ \t]+\S+[ \t]+)" + _REST)
_CREATEUSER = re.compile(r"(?im)^(" + _GREP + r"[ \t]*createUser[ \t]+(?:-e[ \t]+\S+[ \t]+)?\S+)([ \t][^\n]*)$")
# 1.77.0f: net-snmp's -A (authentication) and -X (privacy) pass phrases, on a line of snmpd.conf (trapsess,
# informsess) or an snmp command (snmpwalk -A ..., the value apart or run on)
_SNMP_LINE = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:trapsess|informsess|snmp[a-z0-9]*)(?![A-Za-z0-9_])")
_SNMP_OPT = re.compile(r"(?m)(?<!\S)(-[AX][ \t]*)" + _ARG)
# 1.77.0h: an SNMP v1/v2c community on a command line (snmpget -c ...); not snmpd's or snmptrapd's -c (their config file)
_SNMP_CMD = re.compile(r"(?i)(?<![A-Za-z0-9_.-])snmp(?!d\b|trapd\b)[a-z0-9]+(?![A-Za-z0-9_.-])")   # its name (after a / too)
_SNMP_COMMUNITY = re.compile(r"(?m)(?<!\S)(-c[ \t]*)" + _ARG)
# 1.77.0g: and on a line with no snmp word, when an SNMPv3 option is there: -a or -x with a protocol, -l authPriv or
# authNoPriv. A -u name or -v 3 alone: the backstop drops the line. An iptables rule (-A INPUT ... -j ACCEPT) is none
_SNMP_V3 = re.compile(r"(?<!\S)(?:-[ax][ \t]*(?i:md5|sha(?:-?\d+)?|3?des|aes(?:-?\d+)?c?)"
                      r"|-l[ \t]*(?i:auth(?:no)?priv))(?![\w-])")
_SNMP_WEAK = re.compile(r"(?<!\S)(?:-u[ \t]*[^\s-]|-v[ \t]*3(?!\S))")
# a line of -A and -X options only (a command's next line, after a \): the backstop drops it
_SNMP_ONLY = re.compile(r"[ \t]*-[AX][ \t]*\S+(?:[ \t]+-[AX][ \t]*\S+)*[ \t]*\\?[ \t\r]*")
_SNMP_A = re.compile(r"(?<!\S)-A[ \t]*\S")
_SNMP_X = re.compile(r"(?<!\S)-X[ \t]*\S")
# 1.77.0h: or a match, protocol, interface or comment only iptables has (a rule with no target: a counter)
_IPTABLES = re.compile(r"(?<!\S)(?:-j|--jump|--dport|--sport|--to-ports?|--comment|--ctstate|--state|--icmp-type"
                       r"|-p[ \t]+(?:tcp|udp|udplite|icmp|icmpv6|ipv6-icmp|sctp|esp|ah|gre|all|\d+)"
                       r"|-m[ \t]+(?:comment|state|conntrack|tcp|udp|icmp|icmp6|multiport|limit|owner|mark|connmark|set|recent"
                       r"|iprange|mac|addrtype|hashlimit|physdev|pkttype|rpfilter)|-[io][ \t]+[A-Za-z0-9_.@+*-]+)(?![\w-])")
# 1.77.0g: snmpd.conf's trapsink / trap2sink / informsink HOST [COMMUNITY [PORT]]: the community goes
_SINK = re.compile(r"(?im)^(" + _GREP + r"[ \t]*(?:trapsink|trap2sink|informsink)[ \t]+\S+[ \t]+)" + _ARG)
# 1.77.0g: smbpasswd and pdbedit -Lw (name:uid:LM:NT:[flags]:LCT-...:): both hashes go; a disabled one (32 X, or
# NO PASSWORD and X) stays. Anywhere, two 32-hex fields joined by a colon go too
_SMB_RECORD = re.compile(r"(:\d+:)([0-9A-Fa-f]{32}|X{32}|NO PASSWORDX{21}):([0-9A-Fa-f]{32}|X{32}):(?=\[)")
_SMB_PAIR = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}:[0-9A-Fa-f]{32}(?![0-9A-Fa-f])")
_HEX32 = re.compile(r"[0-9A-Fa-f]{32}")
# 1.77.0f: chrony's keys ("ID TYPE KEY"): the key goes - when its type is a hash or cipher chrony knows, or the line is
# grep's from a chrony.keys file (the type may be left out there); HEX: and ASCII: kept before it
_CHRONY_TYPES = r"(?:MD5|SHA1|SHA224|SHA256|SHA384|SHA512|SHA3-224|SHA3-256|SHA3-384|SHA3-512|RMD128|RMD160|RMD256|RMD320" \
                r"|TIGER|WHIRLPOOL|AES128|AES256)"
# 1.77.0h: a comment may follow the key (1 MD5 ASCII:key # who uses it)
_CHRONY_END = r"(?=[ \t]*(?:#[^\n]*)?\r?$)"
_CHRONY_TYPED = re.compile(r"(?im)^(" + _GREP + r"[ \t]*\d+[ \t]+" + _CHRONY_TYPES + r"[ \t]+)([^\s#]\S*)" + _CHRONY_END)
_CHRONY_FILE = re.compile(r"(?m)^([^\s:]*chrony\.keys[:-](?:\d+[:-])?[ \t]*\d+[ \t]+(?:[A-Za-z0-9-]+[ \t]+)?)([^\s#]\S*)" + _CHRONY_END)
# 1.77.0h: an HTTP Authorization header (its scheme kept: Bearer, Basic ...), and a Bearer token anywhere
_AUTH_HEADER = re.compile(r"(?im)((?<![A-Za-z0-9_-])(?:proxy-)?authorization[\"']?[ \t]*[:=][ \t]*[\"']?"
                          r"(?:(?:bearer|basic|digest|negotiate|ntlm|token|apikey)[ \t]+)?)(\S[^\n]*)$")
_BEARER = re.compile(r"(?i)(?<![A-Za-z0-9_-])(bearer[ \t]+)(?!<)([A-Za-z0-9._~+/-]+=*)")
_CHRONY_HEX = re.compile(r"\bHEX:[0-9A-Fa-f]{16,}\b")
# reg query: "    Service Password    REG_SZ    value" - the type column (after two blanks or a tab) ends the name
_REG_AT = re.compile(r"(?i)REG_")
_REG_TYPE = re.compile(r"(?i)REG_[A-Z_]+[ \t]+")
# grep's prefix of one line, each way it can be read - tried one by one, so a long line is read a few times, not once
# for every - or : in it. A context prefix is the last -N- before any = : quote or blank
_PRE_COLON = re.compile(r"[^\s:]+:(?:\d+[:-])?")
_PRE_NUMBER = re.compile(r"\d+[:-]")
_PRE_CONTEXT = re.compile(r"[^\s:=\"']*-\d+-")
# a URL with a user and password: scheme://user:password@host
_URL_HEAD = re.compile(r"(?i)(?<![A-Za-z0-9+.-])[a-z][a-z0-9+.-]*://[^\s:/@\"'<>]*:")
_URL_WIDE = re.compile(r"[^\s\"<>]+")            # a URL's characters, an apostrophe too (valid in a password)
_QUOTE_EDGE = re.compile(r"'[ \t]*[,:}\]]|[,:{\[][ \t]*'")
_JSON_EDGE = re.compile(r"\"[ \t]*[,:}\]]|[,:{\[][ \t]*\"")
# XML: an opening tag named like a secret (attributes end at the next < or >), and any closing tag
_XML_OPEN = re.compile(r"(?i)<(" + _NAMES + r")(?:[ \t][^<>\n]*)?>")
_XML_CLOSE = re.compile(r"</([A-Za-z0-9_.-]+)[ \t]*>")
# 1.77.0h: an opening tag named like a secret whose > is on a later line (<password\n type="x">), a CDATA section or a
# comment (a </password> inside one closes nothing), and a tag's attributes (<entry key="password" value="x"/>)
_XML_OPEN_START = re.compile(r"(?i)<(" + _NAMES + r")(?=[ \t]|$)[^<>\n]*$")
_XML_SKIPS = (("<![CDATA[", "]]>"), ("<!--", "-->"))
# a tag (not a marker this module writes; a marker may be an attribute's value)
_XML_TAG = re.compile(r"<(?!(?:hash |line )?removed\b)([A-Za-z_][A-Za-z0-9_.:-]*)([ \t](?:[^<>\n]|<(?:hash )?removed>)*?)?(/?)>")
_XML_ATTR = re.compile(r"(?<![A-Za-z0-9_.:-])([A-Za-z_][A-Za-z0-9_.:-]*)([ \t]*=[ \t]*)(\"[^\"\n]*\"|'[^'\n]*')")   # a name once
_XML_VALUE_ATTR = re.compile(r"(?i)value|val|data|content|text|default")
# nsswitch.conf's "passwd: files sss": kept only when every word after it is an NSS source or an [ACTION=...]
_NSS = frozenset("files sss sssd systemd ldap nis nisplus dns compat myhostname mymachines resolve winbind wins altfiles "
                 "usrfiles db hesiod cache mdns mdns4 mdns6 mdns_minimal mdns4_minimal mdns6_minimal".split())
# 1.77.0h: read in one forward pass - a [ never looks past the next [ (a run of [[[[ cost its square), and an [...] is
# an action only when it holds STATUS=ACTION pairs (passwd: [anything] was taken for one)
_NSS_TOKEN = re.compile(r"\[[^\[\]\n]*\]|[^\s\[]+|\[")
_NSS_ACTION = re.compile(r"(?i)\[[ \t]*(?:!?(?:success|notfound|unavail|tryagain)[ \t]*=[ \t]*(?:return|continue|merge)[ \t]*)+\]")
# a whole passwd record (name:x:uid:gid:gecos:home:shell) or shadow record (name:field: and 7 more), after the name
_ENTRY = re.compile(r"([^:\n]*)(?::\d+:\d+:[^:\n]*:[^:\n]*:[^:\n]*|(?::\d*){6}:[^:\n]*)$")
_BOOLEANS = frozenset(("true", "false", "yes", "no"))
_EVIDENCE = re.compile(r"|x|\*|!+\*?|!*\$?" + re.escape(HASH_MARK))
# what this module writes for a value: a value that is exactly one of them is left (so redacting twice is the same), and
# an empty "" or '' (evidence). A value that only holds one of them is a value like any other.
_WRITTEN = [MARK, HASH_MARK, "$" + HASH_MARK, "grub.pbkdf2." + HASH_MARK]
_DONE = frozenset(_WRITTEN + ['"%s"' % w for w in _WRITTEN] + ["'%s'" % w for w in _WRITTEN] + ['""', "''"])

# the backstop: a secret's word, and what the first layer understood
_SECRET_WORD = re.compile(r"(?i)password|passwd|passphrase|pwd|secret|token|authtok|credential|community|bindpw|rootpw|pbkdf2|keytab|psk"
                          r"|vmware[_-]soap[_-]session|session[_-]?id|session[_-]cookie|phpsessid"
                          r"|_key|private key|\$(?:1|2[abxy]?|5|6|y|7|gy|md5|sha1)\$|\$argon2|\$scrypt"
                          r"|(?-i:(?<=[a-z0-9])P(?:ass|ASS)(?![a-z]))"
                          # 1.77.0h: a name's ending the first layer knows, in a line it did not understand (api-key x,
                          # db-pass x, pw x)
                          r"|[-.]key\d{0,2}(?![a-z0-9])|apikey|(?<=[a-z0-9])[-._]pass(?![a-z0-9])|(?<![a-z0-9])pw(?![a-z0-9])")
_MARKQ = r"(?:[\"']?(?:grub\.pbkdf2\.<hash removed>|\$<hash removed>|<hash removed>|<removed>)[\"']?|\"\"|'')"
# These noun phrases describe operations. Consume only the phrase, so another secret on the line is still checked.
_OPERATOR_PHRASE = re.compile(r"(?i:(?<![\w.-])(?:token[ \t]+(?:ring|refresh[ \t]+(?:succeeded|failed))|(?:token|secret)[ \t]+" + _STATUS_NOTE +
                    r"|password[ \t]+" + _PASSWORD_NOTE + r"|no[ \t]+secret)(?![\w-]))")
_UNDERSTOOD = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?----- <removed> -----END [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----"
    r"|<[^<>\s/]+(?:[ \t][^<>]*)?>" + _MARKQ + r"</[^<>\s]+[ \t]*>"
    r"|<[A-Za-z0-9_.-]+(?:[ \t][^<>]*)?/?>(?:<removed>)?[ \t]*\r?$|</[A-Za-z0-9_.-]+[ \t]*>"
    r"|(?i:(?<![A-Za-z0-9+.-])[a-z][a-z0-9+.-]*://[^\s:/@]*:<removed>(?:@|$))"
    r"|(?<![A-Za-z0-9_.-])password(?:_pbkdf2)?[ \t]+\S+[ \t]+" + _MARKQ +
    r"|(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+[ \t]+(?i:REG_)[A-Za-z_]+[ \t]+" + _MARKQ +
    r"|(?<![A-Za-z0-9_.-])\\?[\"']?[A-Za-z0-9_.-]+\\?[\"']?[ \t]*[=:][ \t]*(?:" + _MARKQ + r"|\r?$)" +
    r"|(?<![A-Za-z0-9_.-])[A-Za-z0-9_.-]+[ \t]+" + _MARKQ)
# a file's path (absolute, or a word with a / in it): a secret's word inside one names a file, not a secret
_PATH = re.compile(r"(?<![A-Za-z0-9_./~-])[A-Za-z0-9_.~-]*/[^\s:=,;\"'<>()\[\]{}]*")
_METHOD = r"(?:publickey|password|keyboard-interactive(?::[a-z]+)?|hostbased|gssapi-with-mic|none)"
_METHODS = _METHOD + r"(?:," + _METHOD + r")*"
_SAFE = re.compile(
    r"(?i)(?:"
    # an audit rule (auditctl -l, audit.rules)
    r"|[ \t]*(?:auditctl[ \t]+)?-[aAdDwW][ \t].*"
    # 1.77.0h: sshd's AuthenticationMethods (password names a method there)
    r"|[ \t]*authenticationmethods[ \t]+(?:any|" + _METHODS + r"(?:[ \t]+" + _METHODS + r")*)"
    # sssd: which kind of token ldap_default_authtok holds (1.77.0f: a STIG check reads it)
    r"|[ \t]*ldap_default_authtok_type[ \t]*=[ \t]*(?:obfuscated_)?password"
    # chage -l
    r"|(?:last password change|password expires|password inactive|account expires|minimum number of days between password"
    r" change|maximum number of days between password change|number of days of warning before password expires)[ \t]*:.*"
    # passwd -S
    r"|\S+[ \t]+(?:PS|LK|NP|P|L)[ \t].*\((?:password set|password locked|empty password|alternate authentication)[^)\n]*\)\.?"
    # an RPM package
    r"|[A-Za-z0-9_.+-]+-[A-Za-z0-9_.+~^]+-[A-Za-z0-9_.+~^]+\.(?:x86_64|noarch|aarch64|i686|ppc64le|s390x|src)"
    r"|package [^\s]+ is not installed"
    # klist's header
    r"|keytab name:[ \t]*\S+"
    r")[ \t\r]*")
# 1.77.0h: PAM - type, control, module, then its arguments read one by one: an argument with a secret's word is kept
# only when it is a PAM option (use_authtok, try_first_pass, authtok_type=...) or a path (pam_exec ... --password x goes)
_SAFE_PAM = re.compile(r"(?i)[ \t]*-?(?:auth|account|password|session)[ \t]+(?:required|requisite|sufficient|optional|include"
                       r"|substack|\[[^\]\n]*\])[ \t]+\S+([^\n]*)")
_PAM_ARG_OK = re.compile(r"(?i)(?:use|try|forward|expose|not_set|use_mapped)_(?:first_)?(?:authtok|pass)|authtok_type=[A-Za-z0-9_-]*|[A-Za-z_]+=/\S*|/\S*")
# 1.77.0h: a setting with a yes/no value, sshd's PermitRootLogin values - not when its name is a secret's (my_password no);
# sssd's cache_credentials = True is a yes/no setting
_SAFE_BOOL = re.compile(r"(?i)[ \t]*([A-Za-z0-9_.-]+)(?:[ \t]*[=:][ \t]*|[ \t]+)(yes|no|true|false|on|off|enabled?|disabled?"
                        r"|never|without-password|prohibit-password|forced-commands-only)[ \t\r]*")
_COMMENT = re.compile(r"[ \t]+#")
# 1.77.0f: a setting with a number or a file's path for its value - not when its name is a secret's (password 1234)
_SAFE_SETTING = re.compile(r"[ \t]*([A-Za-z0-9_.-]+)(?:[ \t]*[=:][ \t]*|[ \t]+)(?:-?\d+(?:\.\d+)?|(?:FILE:|WRFILE:)?/[^\s]*)[ \t\r]*")
# 1.77.0f: sudoers holds no secrets. A Defaults line: its flags (!rootpw, targetpw) and number settings are not read as
# words; a user's rule: its tags (NOPASSWD:, PASSWD:) are not names
_SUDO_DEFAULTS = re.compile(r"[ \t]*Defaults(?:[:@!>][^\s]+)?[ \t]")
_SUDO_FLAG = re.compile(r"(?<![^\s,])!*[A-Za-z_]+(?:[ \t]*[+-]?=[ \t]*-?\d+(?:\.\d+)?)?(?=[ \t]*(?:,|$))")
_SUDO_SPEC = re.compile(r"[ \t]*[%+]?[^\s=]+[ \t]+[^\s=]+[ \t]*=[ \t]*(?:\([^)\n]*\)[ \t]*)?(?:[A-Z_]+:[ \t]*)*")
_SUDO_TAG = re.compile(r"[=,][ \t]*(?:\([^)\n]*\)[ \t]*)?(?:[A-Z_]+:[ \t]*)*$")   # 1.77.0h: or after a comma
_SUDO_LATER_TAG = re.compile(r"(,[ \t]*(?:\([^)\n]*\)[ \t]*)?(?:[A-Z_]+:[ \t]*)*?)(?:NO)?PASSWD:")
# a passwd or shadow record, its password field evidence (or a hash this module removed)
_SAFE_RECORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*\$?:(?:|x|\*|!+\*?|!*\$?" + re.escape(HASH_MARK) + r")"
                          r"(?::\d+:\d+:[^:\n]*:[^:\n]*:[^:\n]*|(?::\d*){6}:[^:\n]*)")
# a grep prefix a dropped line keeps: a path or a line number, nothing that could be a word of the line
_KEEP_PRE = re.compile(r"(?:/[^\s:]*|\d+)[:-](?:\d+[:-])?|/[^\s:]*?-\d+-")


def _value(v):
    """A value's replacement: <removed> - in its quotes when it starts with one; a value that is only a crypt or GRUB hash
    says so ($<hash removed>, grub.pbkdf2.<hash removed>): which kind of hash it is, is evidence."""
    if v in _DONE:
        return v
    q = v[0] if v[:1] in ("\"", "'") else ""
    inner = v[1:-1] if q and len(v) > 1 and v[-1] == q else v[len(q):]
    if _GRUB.fullmatch(inner):
        mark = "grub.pbkdf2." + HASH_MARK
    elif _CRYPT.fullmatch(inner):
        mark = "$" + HASH_MARK
    else:
        mark = MARK
    return q + mark + q


def _nss_only(text):
    """nsswitch.conf's sources after "passwd:" - every word a source, or an [ACTION=...]."""
    sources = 0
    for m in _NSS_TOKEN.finditer(text):
        w = m.group(0)
        if w.startswith("["):
            if not _NSS_ACTION.fullmatch(w):
                return False
        elif w.lower() in _NSS:
            sources += 1
        else:
            return False
    return sources > 0


def _prefixes(ln):
    """The ways a line may start with grep's prefix, the longest first, and none last."""
    out = []
    for rx in (_PRE_CONTEXT, _PRE_COLON, _PRE_NUMBER):
        m = rx.match(ln)
        if m and m.group(0) not in out:
            out.append(m.group(0))
    m = re.match(r"[^\s:]+:", ln)
    if m and m.group(0) not in out:
        out.append(m.group(0))
    return out + [""]


def _cont_of(pre):
    """The prefix grep gives the lines after a line with prefix pre: the same file (path:13:, path-13-), a line number
    (13-), or none."""
    path = re.match(r"([^\s:]+?)[:-](?:\d+[:-])?$", pre) if pre and not re.fullmatch(r"\d+[:-]", pre) else None
    return re.compile(re.escape(path.group(1)) + r"[:-](?:\d+[:-])?" if path else (r"\d+[:-]" if pre else ""))


def _block_kind(tail):
    """What follows a YAML key's colon -> 'empty' (nothing: a sequence or a mapping may follow), 'scalar' (| or >), or
    None (a value on the line itself). Anchors (&a), tags (!!str) and a comment (# ...) are read past."""
    if tail[:1] not in ("", " ", "\t", "\r"):
        return None
    t = tail.strip()
    t = "" if t.startswith("#") else re.split(r"[ \t]#", t, maxsplit=1)[0]
    words = [w for w in t.split() if not w.startswith(("&", "!"))]
    if not words:
        return "empty"
    return "scalar" if len(words) == 1 and _INDICATOR.fullmatch(words[0]) else None


def _blocks(s):
    """The lines of a YAML block under a secret's name (name: |, name: >, name: and nothing): each one <removed>. Its
    lines as grep shows them too. Blank lines and comments in between do not end it."""
    out, under, cont, seq = [], None, None, False
    for ln in s.split("\n"):
        if under is not None:
            c = cont.match(ln)
            rest = ln[c.end():] if c else None
            body = rest.lstrip(" \t") if rest is not None else None
            if body is not None and not body.strip():
                out.append(ln)
                continue
            ind = len(rest) - len(body) if body is not None else -1
            # an indentless sequence ("credentials:" then "- item" in the same column) belongs to an empty value too
            if ind > under or (seq and ind == under and body[:1] == "-" and body[1:2] in ("", " ", "\t", "\r")):
                out.append(ln[:len(ln) - len(body)] + MARK)
                continue
            if body is not None and body.startswith("#"):
                out.append(ln)                # a comment line: the value may still come
                continue
            under = None
        if ":" in ln:
            for pre in _prefixes(ln):
                m = _BLOCK.match(ln, len(pre))
                kind = _block_kind(m.group("tail")) if m else None
                if kind:
                    under, seq, cont = len(m.group(1)), kind == "empty", _cont_of(pre)
                    break
                if m and m.group("tail").strip():
                    # 1.77.0h: a value on the key's line - a plain or quoted scalar goes on over the lines indented more
                    under, seq, cont = len(m.group(1)), False, _cont_of(pre)
                    break
        out.append(ln)
    return "\n".join(out)


def _no_strings(text):
    """A line with its JSON strings ("...", escapes read) left out - one forward pass. A " whose string never closes
    is a plain character; then no later one closes either (their escapes are read the same way), so the rest is kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        q = text.find('"', i)
        if q < 0:
            break
        j = q + 1
        while j < n and text[j] not in '"\n':
            j += 2 if text[j] == "\\" else 1
        if j < n and text[j] == '"':
            out.append(text[i:q])
            i = j + 1
        else:
            break
    out.append(text[i:])
    return "".join(out)


def _depth(text):
    """How many [ and { a line leaves open: its quoted strings and a comment (# ...) left out."""
    t = _no_strings(text)
    t = re.sub(r"'[^'\n]*'", "", t)
    t = "" if t.lstrip().startswith("#") else re.split(r"[ \t]#", t, maxsplit=1)[0]
    return t.count("[") + t.count("{") - t.count("]") - t.count("}")


def _empty_name_at(ln):
    """Where a "name:" with nothing after it ends the line (its value is on the next line), or None."""
    pos, last = 0, None
    while True:
        m = _NAMED.search(ln, pos)
        if not m:
            return last
        pos = m.end()
        last = m.start("head") if m.group("sep") == ":" and _BLANK.match(ln, pos) else None


def _opened_at(ln):
    """The first secret's name on a line whose value starts with [ or { and leaves it open -> (where, how deep), or None.
    Read once: the rest of the line after it is its value."""
    pos = 0
    while True:
        m = _NAMED.search(ln, pos)
        if not m:
            return None
        pos = m.end()
        if ln[pos:pos + 1] in ("[", "{"):
            d = _depth(ln[pos:])
            return (m.start("head"), d) if d > 0 else None


def _later_values(s):
    """A "name:" with nothing after it: its value is the next line that is not blank or a comment, under the same grep
    prefix - the whole of it, and the lines after it while a [ or { it opened is not closed. Read on the text as it
    came, before any other pass rewrites a line (a JSON value on the next line, a YAML value under its key)."""
    out, pend = [], None                      # pend: [the prefix's regex, the brackets still open]
    for ln in s.split("\n"):
        if pend is not None:
            c = pend[0].match(ln)
            if c is not None:
                rest = ln[c.end():]
                body = rest.strip()
                if not body or (body.startswith("#") and pend[1] <= 0):
                    out.append(ln)
                    continue
                lead = rest[:len(rest) - len(rest.lstrip())]
                if body[:2] in ("- ", "-\t"):  # a YAML list's item: its "- " stays, so the list is still seen as one
                    lead, body = lead + body[:2], body[2:].lstrip()
                out.append(ln[:c.end()] + lead + (body if body in _DONE else _value(body)) + rest[len(rest.rstrip()):])
                pend[1] += _depth(body)
                if pend[1] <= 0:
                    pend = None
                continue
            pend = None
        p = _PRE_CONTEXT.match(ln)
        k = p.end() if p else 0
        at = _empty_name_at(ln[k:]) if ":" in ln else None
        if at is not None:
            pre = max((x for x in _prefixes(ln) if len(x) <= k + at), key=len)
            pend = [_cont_of(pre), 0]
        elif ("[" in ln or "{" in ln) and ("=" in ln or ":" in ln):
            # 1.77.0h: a value that opens a [ or { on its name's line ("token": ["a",) - the lines after it too, to its close
            o = _opened_at(ln[k:])
            if o is not None:
                pre = max((x for x in _prefixes(ln) if len(x) <= k + o[0]), key=len)
                pend = [_cont_of(pre), o[1]]
        out.append(ln)
    return "\n".join(out)


def _xml_skips(ln, carry):
    """The spans of a line inside a CDATA section or a comment, in order -> (spans, the end marker still open or None)."""
    spans, i = [], 0
    if carry:
        e = ln.find(carry)
        if e < 0:
            return [(0, len(ln))], carry
        spans.append((0, e + len(carry)))
        i = e + len(carry)
    nxt = {}                                  # each opener's next place, found again only once passed (one pass)
    while True:
        best = None
        for a, b in _XML_SKIPS:
            if a not in nxt or (0 <= nxt[a] < i):
                nxt[a] = ln.find(a, i)
            k = nxt[a]
            if k >= 0 and (best is None or k < best[0]):
                best = (k, a, b)
        if best is None:
            return spans, None
        k, a, b = best
        e = ln.find(b, k + len(a))
        if e < 0:
            spans.append((k, len(ln)))
            return spans, b
        spans.append((k, e + len(b)))
        i = e + len(b)


def _xml_all_attrs(text):
    """Every attribute value in a text: <removed> (an opening tag's attributes over lines, the tag named like a secret)."""
    return _XML_ATTR.sub(lambda a: a.group(0) if a.group(3)[1:-1] in _WRITTEN or not a.group(3)[1:-1]
                         else a.group(1) + a.group(2) + a.group(3)[0] + MARK + a.group(3)[0], text)


def _xml_attrs(ln):
    """A tag's attribute values: every one in a tag named like a secret; in a tag one of whose values names a secret
    (key="password"), its value-like ones (value=, data= ...)."""
    def tag(m):
        attrs = m.group(2)
        if not attrs or "=" not in attrs:
            return m.group(0)
        if _NAME_WORD.fullmatch(m.group(1)):
            keep = None
        elif any(_NAME_WORD.fullmatch(a.group(3)[1:-1]) for a in _XML_ATTR.finditer(attrs)):
            keep = "value"
        else:
            return m.group(0)
        attrs = _XML_ATTR.sub(lambda a: a.group(0) if a.group(3)[1:-1] in _WRITTEN or not a.group(3)[1:-1]
                              or (keep and not _XML_VALUE_ATTR.fullmatch(a.group(1)))
                              else a.group(1) + a.group(2) + a.group(3)[0] + MARK + a.group(3)[0], attrs)
        return "<" + m.group(1) + attrs + m.group(3) + ">"
    return _XML_TAG.sub(tag, ln) if "<" in ln and "=" in ln else ln


def _xml_blocks(s):
    """Elements named like a secret: <password>value</password> - the value <removed>, nested ones paired with their own
    closing tag; one open at a line's end - the rest of that line and every line to its closing tag, however long. Only
    a whole closing tag closes one; <password /> opens nothing. Before the passes that rewrite lines, so none of them
    can take a closing tag away. Each line is read once: its tags are paired by their places, in order."""
    out, open_ = [], None                     # open_: [the element's name, how deep it is nested]
    tag, carry = None, None                   # 1.77.0h: an opening tag still open (its name); a CDATA or comment's end
    for ln in s.split("\n"):
        lead = ""
        if tag is not None:                   # the rest of an opening tag: its attributes to its >
            g = ln.find(">")
            if g < 0:                         # its attributes' values go (the tag is named like a secret)
                out.append(_xml_all_attrs(ln))
                continue
            if ln[g - 1:g] == "/":            # <password ... /> opens nothing
                tag = None
                out.append(_xml_all_attrs(ln[:g]) + ln[g:])
                continue
            open_, tag = [tag, 1], None
            lead, ln = _xml_all_attrs(ln[:g]) + ">", ln[g + 1:]
        if "<" not in ln and carry is None:
            out.append(lead + (MARK if open_ is not None and ln.strip() else ln))
            continue
        ln = _xml_attrs(ln) if open_ is None else ln
        skips, carry = _xml_skips(ln, carry)
        events = []
        for m in _XML_OPEN.finditer(ln):
            if not m.group(0).endswith("/>"):
                events.append((m.start(), m.end(), "open", m.group(1).lower()))
        for m in _XML_CLOSE.finditer(ln):
            events.append((m.start(), m.end(), "close", m.group(1).lower()))
        events.sort()
        if skips:                             # a tag inside a CDATA section or a comment is the value's text
            kept, j = [], 0
            for ev in events:
                while j < len(skips) and skips[j][1] <= ev[0]:
                    j += 1
                if not (j < len(skips) and skips[j][0] <= ev[0] < skips[j][1]):
                    kept.append(ev)
            events = kept
        parts, cur, start = [], 0, (0 if open_ is not None else None)
        for a, b, kind, name in events:
            if open_ is None:
                if kind == "open":
                    open_, start = [name, 1], b
                    parts.append(ln[cur:b])
                    cur = b
                continue
            if name != open_[0]:
                continue
            open_[1] += 1 if kind == "open" else -1
            if open_[1] == 0:                 # its own closing tag: the value between goes
                content = ln[start:a]
                parts.append(_value(content) if content.strip() and start > 0 else (MARK if content.strip() else content))
                parts.append(ln[a:b])
                cur, open_, start = b, None, None
        if open_ is not None:                 # still open at the line's end
            content = ln[start:]
            parts.append((_value(content) if start > 0 else MARK) if content.strip() else content)
        else:
            parts.append(ln[cur:])
            o = _XML_OPEN_START.search(ln, cur) if "<" in ln else None
            if o:                             # 1.77.0h: an opening tag whose > comes on a later line
                tag = o.group(1).lower()
        out.append(lead + "".join(parts))
    return "\n".join(out)


def _reg(s):
    """reg query lines: a value whose name (the words before the type column) ends like a secret's, <removed>."""
    out = []
    for ln in s.split("\n"):
        if "reg_" in ln or "REG_" in ln or "Reg_" in ln:
            for r in _REG_AT.finditer(ln):
                i = r.start()
                if i < 2 or not (ln[i - 1] == "\t" or ln[i - 2:i] == "  "):
                    continue
                t = _REG_TYPE.match(ln, i)
                if not t:
                    continue                  # 1.77.0h: the type first - a column that is none costs no copy
                words = ln[:i].split()
                if not words:
                    continue                  # not the type column: no name before it
                if _NAME_WORD.fullmatch(words[-1]):
                    rest = ln[t.end():]
                    v = rest.rstrip()
                    if v:
                        ln = ln[:t.end()] + _value(v) + rest[len(v):]
                break
        out.append(ln)
    return "\n".join(out)


def _urls(s):
    """scheme://user:password@host: the password, to the last @ of the URL's own characters. A URL with no @ there but
    one further on (a password holding an apostrophe): the rest of its run, to the next blank or double quote. Each run
    is read once, whatever its length or how many URLs it holds: the URL's own characters end at the run's next
    apostrophe, and that piece's end and its last @ are found once for every URL in it (1.77.0f: not once per URL)."""
    out, pos = [], 0
    for w in _URL_WIDE.finditer(s):
        wide = w.group(0)
        if "://" not in wide or "@" not in wide:
            continue
        parts, i, base = [], 0, w.start()
        last_at = wide.rfind("@")
        seg_end, seg_at = -1, -1              # the piece up to the next apostrophe, and its last @
        for h in _URL_HEAD.finditer(wide):
            if h.start() < i:
                continue
            he = h.end()
            if seg_end < he:                  # a head holds no apostrophe: its URL's own characters end at the next one
                seg_end = wide.find("'", he)
                seg_end = len(wide) if seg_end < 0 else seg_end
                seg_at = wide.rfind("@", he, seg_end)
            at = seg_at if seg_at >= he else -1
            if at > he:                       # the URL's own @: its password goes
                pw = wide[he:at]
                parts.append(wide[i:he] + (pw if pw == MARK else MARK))
                i = at
            elif wide[he:he + 1] != "@" and last_at > he:
                last = last_at
                if not _QUOTE_EDGE.search(wide, he, last):
                    parts.append(wide[i:h.end()] + MARK)     # an apostrophe in the password: to its @
                    i = last
                    continue
                parts.append(wide[i:h.end()] + MARK)         # quoted fields ('a','b'): the rest of the run
                i = len(wide)
                break
        if parts:
            out.append(s[pos:base] + "".join(parts) + wide[i:])
            pos = w.end()
    out.append(s[pos:])
    return _urls_any("".join(out))


def _urls_any(s):
    """A password holding a double quote or < > (not valid in a URL, but a host may print it): to the URL's last @
    before a blank - unless what lies between holds a JSON field's boundary (","): then it is the next field's @."""
    out, pos = [], 0
    for w in re.finditer(r"\S+", s) if "://" in s and "@" in s else ():
        run = w.group(0)
        if "://" not in run or "@" not in run:
            continue
        h = _URL_HEAD.search(run)
        at = run.rfind("@")
        if not h or at <= h.end() or run[h.end():at] == MARK or _JSON_EDGE.search(run, h.end(), at):
            continue
        out.append(s[pos:w.start()] + run[:h.end()] + MARK + run[at:])
        pos = w.end()
    out.append(s[pos:])
    return "".join(out)


def _named_line(ln):
    """One line: each secret's name in it, left to right. A JSON string goes and the line goes on after it; any other
    value is the rest of the line. A loop, not a recursion: a JSON line of thousands of names is read once."""
    out, pos, spec = [], 0, None
    while True:
        m = _NAMED.search(ln, pos)
        if not m:
            out.append(ln[pos:])
            return "".join(out)
        out.append(ln[pos:m.end()])
        pos = m.end()
        if _BLANK.match(ln, pos):
            continue                          # a bare "name=" or "name:" - nothing on this line (_later_values)
        if m.group("name") in ("NOPASSWD", "PASSWD") and m.group("sep") == ":":
            if spec is None:
                spec = bool(_SUDO_SPEC.match(ln))   # once a line: a rule (user host = ...)
            if spec and _SUDO_TAG.search(ln, max(0, m.start("head") - 512), m.start("head")):
                continue                      # 1.77.0f: a sudoers rule's tag (ALL=(ALL) NOPASSWD: ALL), not a name
        if m.group("q") == "\"" and m.group("cq") == "\"" and m.group("sep") == ":":
            j = _JSON_STR.match(ln, pos)
            if j and _JSON_END.match(ln, j.end()):
                out.append(_value(j.group(0)))
                pos = j.end()
                continue
        if m.group("sep") == ":" and not m.group("q") and not m.group("cq"):
            if m.group("name").lower() == "passwd" and _nss_only(ln[pos:]):
                continue                      # "passwd: files sss" - a list of NSS sources, not a secret
            f = _ENTRY.match(ln, pos) if m.group("head").endswith(":") else None
            # a whole record at the line's start (after a grep prefix): a user whose name ends like a secret's
            if (f and ln[:m.start("head")] in _prefixes(ln)
                    and (_EVIDENCE.fullmatch(f.group(1)) or (_SHADOW.match(ln, m.start("head")) and ":" not in f.group(1)))):
                continue
        rest = ln[pos:]
        v = rest.rstrip()
        if m.group("name").lower() == "cache_credentials" and v.lower() in _BOOLEANS:   # 1.77.0h: that setting only
            out.append(rest)                  # sssd's cache_credentials = True: whether logins are cached (a STIG check)
            return "".join(out)
        out.append(_value(v) + rest[len(v):])
        return "".join(out)


def _named_lines(s):
    """The name pass, line by line, after grep's context prefix of a path (/etc/x.conf-3-db_password=)."""
    out = []
    for ln in s.split("\n"):
        p = _PRE_CONTEXT.match(ln)
        k = p.end() if p else 0
        out.append(ln[:k] + _named_line(ln[k:]))
    return "\n".join(out)


def _snmp_kind(ln):
    """How a line's -A and -X are read: 2 net-snmp's pass phrases (an snmp word, an SNMPv3 protocol or level, or both
    -A and -X on the line), 1 maybe (a -u name, -v 3, or nothing but -A and -X: the backstop drops the line), 0 none
    (no -A or -X; an iptables rule)."""
    if ("-A" not in ln and "-X" not in ln) or _IPTABLES.search(ln):
        return 0
    if _SNMP_LINE.search(ln) or _SNMP_V3.search(ln) or (_SNMP_A.search(ln) and _SNMP_X.search(ln)):
        return 2
    return 1 if _SNMP_WEAK.search(ln) or (len(ln) <= 1024 and _SNMP_ONLY.fullmatch(ln)) else 0


def _snmp(s):
    """net-snmp's -A and -X pass phrases, and an snmp command's -c community (1.77.0h): <removed>. A quoted value whole."""
    out = []
    for ln in s.split("\n"):
        if _snmp_kind(ln) == 2:
            ln = _SNMP_OPT.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) in _DONE else MARK), ln)
        if "-c" in ln and _SNMP_CMD.search(ln) and not _IPTABLES.search(ln):
            ln = _SNMP_COMMUNITY.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) in _DONE else MARK), ln)
        out.append(ln)
    return "\n".join(out)


def _smb_record(m):
    return m.group(1) + ":".join(MARK if _HEX32.fullmatch(v) else v for v in (m.group(2), m.group(3))) + ":"


def _chrony_key(m):
    v = m.group(2)
    kind = v[:6].upper() if v[:6].upper() == "ASCII:" else (v[:4].upper() if v[:4].upper() == "HEX:" else "")
    return m.group(1) + (v if v in _DONE else v[:len(kind)] + MARK)


def _sudo(r):
    """A sudoers line with what is not a word of a secret left out: a Defaults line's flags and number settings, a
    rule's tags. Any other line as it is."""
    d = _SUDO_DEFAULTS.match(r)
    if d:
        return r[:d.end()] + _SUDO_FLAG.sub(" ", r[d.end():])
    m = _SUDO_SPEC.match(r) if ("PASSWD" in r) else None
    if m:
        return re.sub(r"(?:NO)?PASSWD:", " ", r[:m.end()]) + _SUDO_LATER_TAG.sub(r"\1 ", r[m.end():])
    return r


def _understood(rest, review):
    """Whether every secret's word on a line (after its grep prefix) is one the first layer understood, a file's path
    holds, or the line is a known evidence shape. 1.77.0h: an evidence line with a comment after it - the setting read
    without it, the comment holding no secret's word (PasswordAuthentication no # policy)."""
    if review:
        rest = _OPERATOR_PHRASE.sub(" ", rest)
    if _understood_line(rest):
        return True
    c = _COMMENT.search(rest) if len(rest) <= 1024 else None
    return bool(c and rest[:c.start()].strip() and not _SECRET_WORD.search(rest[c.end():]) and _understood_line(rest[:c.start()]))


def _pam_ok(r):
    m = _SAFE_PAM.fullmatch(r)
    return bool(m) and all(_PAM_ARG_OK.fullmatch(a) or not _SECRET_WORD.search(a) for a in m.group(1).split())


def _bool_ok(r):
    m = _SAFE_BOOL.fullmatch(r)
    if not m:
        return False
    name = m.group(1)
    return not _NAME_WORD.fullmatch(name) or (name.lower() == "cache_credentials" and m.group(2).lower() in _BOOLEANS)


def _understood_line(rest):
    r = rest.rstrip("\r")
    # an evidence shape is a short line: a longer one is only read word by word below (its cost stays linear)
    if r == DROPPED or (len(r) <= 1024 and (_SAFE.fullmatch(r) or _SAFE_RECORD.fullmatch(r) or _pam_ok(r) or _bool_ok(r))):
        return True
    if r.lstrip().lower().startswith("passwd:") and _nss_only(r.lstrip()[7:]):
        return True
    if len(r) <= 1024:
        st = _SAFE_SETTING.fullmatch(r)
        if st and not _NAME_WORD.fullmatch(st.group(1)):
            return True                       # a number or a path, its name not a secret's (password 1234 is not kept)
    # 1.77.0h: a tag's attributes are read as words of the line, not passed with the tag (<password value="x"/>)
    r = _XML_TAG.sub(lambda m: " %s <%s%s>" % (m.group(2) or "", m.group(1), m.group(3)), _sudo(r)) if "<" in r else _sudo(r)
    return not _SECRET_WORD.search(_PATH.sub(" ", _UNDERSTOOD.sub(" ", r)))


def _snmp_bare(ln):
    """A line with a -A or -X value still in it that may be net-snmp's pass phrase (redact(backstop=False) leaves it
    on a "maybe" line only)."""
    return _snmp_kind(ln) and any(m.group(2) not in _DONE for m in _SNMP_OPT.finditer(ln))


def _backstop(s, review):
    """Every line that still holds a secret's word and is not understood: replaced as a whole (DROPPED), its grep prefix
    kept when that is a path or a line number."""
    out = []
    for ln in s.split("\n"):
        if not _SECRET_WORD.search(ln) and not _snmp_bare(ln):
            out.append(ln)
            continue
        pres = _prefixes(ln)
        if pres[0] and _KEEP_PRE.fullmatch(pres[0]):
            readings = [pres[0]]              # grep's prefix, plainly (a path or a line number): read the line after it
        else:                                 # or any reading - a prefix holding a secret's word is none
            readings = [p for p in pres if not _SECRET_WORD.search(p)]
        if not _snmp_bare(ln) and any(_understood(ln[len(p):], review) for p in readings):
            out.append(ln)
            continue
        out.append((pres[0] if pres[0] and _KEEP_PRE.fullmatch(pres[0]) else "") + DROPPED)
    return "\n".join(out)


def _shadow(m):
    return "%s:%s%s:%s:" % (m.group(1), m.group(2), HASH_MARK, m.group(4))


def redact(text, backstop=True, review=False):
    """A text with every secret value this module knows replaced (see the module's notes). Not a string: str() of it.
    backstop=False: the first layer only (a command line - DISA's text, not what a host printed)."""
    if text is None:
        return ""
    s = text if isinstance(text, str) else str(text)
    if not s:
        return s
    s = _PEM.sub(lambda m: "-----BEGIN %s----- %s -----END %s-----" % (m.group(1), MARK, m.group(1)), s)
    # the passes that find a secret by its name, its line or its place first, on the text as it came - the hash passes
    # after them, so the two never meet inside one value
    s = _xml_blocks(s)                        # 1.77.0h: first - a value on the next line cannot take its opening tag
    if "\n" in s:
        s = _later_values(s)
        s = _blocks(s)
    s = _urls(s)
    s = _AUTH_HEADER.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).strip() in _DONE else MARK), s)
    s = _BEARER.sub(lambda m: m.group(1) + MARK, s)
    if "cookie" in s.lower():
        s = _json_cookie_sessions(s)
        s = _COOKIE_HEADER.sub(lambda m: _cookie_sessions(m.group()), s)
    grub = _REVIEW_GRUB if review else _GRUB_PLAIN
    s = grub.sub(lambda m: m.group(1) + _value(m.group(2)), s)
    s = _DIRECTIVE.sub(lambda m: m.group(1) + _value(m.group(2)), s)
    s = _COM2SEC.sub(lambda m: m.group(1) + _value(m.group(2)), s)
    s = _CREATEUSER.sub(lambda m: m.group(1) + " " + MARK, s)
    s = _snmp(s)
    s = _SINK.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) in _DONE else MARK), s)
    s = _SMB_RECORD.sub(_smb_record, s)
    s = _SMB_PAIR.sub(MARK + ":" + MARK, s)
    s = _CHRONY_TYPED.sub(_chrony_key, s)
    s = _CHRONY_FILE.sub(_chrony_key, s)
    s = _reg(s)
    s = _OPTION.sub(lambda m: m.group(1) + (m.group(2) if m.group(2) in _DONE else _value(m.group(2))), s)
    s = _named_lines(s)
    s = _GRUB.sub("grub.pbkdf2." + HASH_MARK, s)
    s = _CRYPT.sub("$" + HASH_MARK, s)
    s = _SHADOW.sub(_shadow, s)
    s = _GSHADOW.sub(lambda m: "%s:%s%s:%s:%s" % (m.group(1), m.group(2), HASH_MARK, m.group(4), m.group(5)), s)
    s = _KLIST_KEY.sub("(0x" + MARK + ")", s)
    s = _HEXKEY.sub("0x" + MARK, s)
    s = _CHRONY_HEX.sub("HEX:" + MARK, s)
    return _backstop(s, review) if backstop else s


def head(text, limit):
    """1.77.0h: at most limit characters of a text, to a line's end - for a text read only in part. A line the limit
    would split is left out whole (a URL or a value cut in two would show its start bare: the redaction cannot see
    what it was); a text of one line over the limit: empty. Then redact() it."""
    if text is None or len(text) <= limit:
        return text
    t = text[:limit]
    k = t.rfind("\n")
    return t[:k] if k >= 0 else ""


# 1.77.0h: how much of a host's text a report shows (answer_keys_evidence: web), and what it says when it shows none
CAP_LINES, CAP_CHARS = 20, 2000
MORE = "(more in the job's artifacts)"
EVIDENCE_OFF = "(not shown: answer_keys_evidence is none - the AAP job's output has it)"


def cap(text, lines=CAP_LINES, chars=CAP_CHARS):
    """A host's text (already redacted) as a report shows it: at most 20 lines and 2000 characters, then a line that
    says where the rest is."""
    text = "" if text is None else str(text)
    ls = text.split("\n")
    if len(ls) <= lines and len(text) <= chars:
        return text
    return "\n".join(ls[:lines])[:chars].rstrip() + "\n" + MORE


def run_main(main):
    """1.77.0h: a host module's entry point. An error it did not expect leaves as its type and its redacted text, never
    as a raw traceback (Ansible shows a module's failure in the job's output - ansible-core 2.19 and later even under
    no_log: true)."""
    import json
    import sys
    try:
        main()
    except Exception as e:  # noqa: BLE001 - every error: its text is the host's
        print(json.dumps({"failed": True, "msg": "%s: %s" % (type(e).__name__, redact(str(e))[:300])}))
        sys.exit(1)


def _json_cookie_sessions(text):
    """Mask a session member of a serialized cookie jar, preserving the surrounding evidence verbatim."""
    if not _COOKIE_OBJECT.search(text):
        return text
    out, copied, containers = [], 0, []
    cookie_next = False
    decoder = json.JSONDecoder()
    for part in _JSON_PART.finditer(text):
        if part.start() < copied:
            continue
        token = part.group()
        if part[2]:
            field = part[1].lower()
            cookie_next = field in ('"cookie"', '"cookies"', '"set-cookie"')
            if containers and containers[-1] and field == '"session"':
                try:
                    value, end = decoder.raw_decode(text, part.end())
                except (ValueError, RecursionError):
                    continue
                if value is not None and value != "" and not isinstance(value, bool):
                    out.extend((text[copied:part.end()], '"' + MARK + '"'))
                    copied = end
        elif token in ("{", "["):
            containers.append(cookie_next and token == "{")
            cookie_next = False
        elif token in ("}", "]") and containers:
            containers.pop()
        else:
            cookie_next = False
    return "".join(out) + text[copied:] if out else text


def _cookie_sessions(header):
    return _COOKIE_SESSION.sub(lambda m: m[1] + _value(m[2]), header)


def redact_all(value, review=False):
    """Mask nested values. Only review output opts into the operator-note prose exceptions."""
    if review and isinstance(value, (dict, list, tuple)):
        # The chart, workbook, Word draft and email repeat the same strings. Cache only for this copy.
        scrub = lru_cache(maxsize=2048)(partial(redact, review=True))
    else:
        scrub = partial(redact, review=True) if review else redact
    return _redact_all(value, scrub)


def _redact_all(value, scrub, cookie_map=False):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = scrub(k, backstop=False) if isinstance(k, str) else k
            name = k.lower() if isinstance(k, str) else ""
            secret = isinstance(k, str) and (_NAME_WORD.fullmatch(k) or (cookie_map and name == "session"))
            if secret and not (v is None or v == "" or isinstance(v, bool)):
                out[key] = MARK
            else:
                cookie = name in ("cookie", "cookies", "set-cookie")
                out[key] = _redact_all(v, scrub, cookie)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_all(v, scrub, cookie_map) for v in value]
    if isinstance(value, str):
        return scrub(_cookie_sessions(value) if cookie_map else value)
    return value
