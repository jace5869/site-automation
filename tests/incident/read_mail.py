#!/usr/bin/env python3
"""Check the one email tests/incident/run_incident_test.sh's fake mail relay received (standard library only).

  read_mail.py MAILDIR CHECK...

CHECK: to=ADDR,ADDR (exactly these got it), attach=NAME,NAME (exactly these Word documents), NAME~TEXT (the document's
text has TEXT), logo=NAME (the document has the logo), banner=TEXT (every document's header and footer say it),
body=TEXT (the plain-text body has it), subject=TEXT. Every part of every document must be well-formed XML.
Exits 1 with what is wrong."""
import email
import glob
import io
import os
import re
import sys
import zipfile
from xml.dom import minidom

files = sorted(glob.glob(os.path.join(sys.argv[1], "*.eml")))
if len(files) != 1:
    sys.exit("%d emails, not 1" % len(files))
with open(files[0], "rb") as fh:
    msg = email.message_from_binary_file(fh)
docs, body = {}, ""
for part in msg.walk():
    name = part.get_filename()
    if name and name.endswith(".docx"):
        z = zipfile.ZipFile(io.BytesIO(part.get_payload(decode=True)))
        parts = dict((n, z.read(n)) for n in z.namelist())
        for n, data in parts.items():
            if n.endswith((".xml", ".rels")):
                try:
                    minidom.parseString(data)
                except Exception as e:  # noqa: BLE001
                    sys.exit("%s: %s is not well-formed XML: %s" % (name, n, e))
        docs[name] = parts
    elif part.get_content_type() == "text/plain" and not name:
        body += part.get_payload(decode=True).decode("utf-8", "replace")


def text(parts, n="word/document.xml"):
    x = parts.get(n, b"").decode("utf-8")
    return "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", x)).replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


bad = []
for c in sys.argv[2:]:
    if c.startswith("to="):
        got = sorted(a.strip().lower() for a in (msg["X-Envelope-To"] or "").split(",") if a.strip())
        if got != sorted(c[3:].lower().split(",")):
            bad.append("sent to %s, not %s" % (got, c[3:]))
    elif c.startswith("attach="):
        if sorted(docs) != sorted(c[7:].split(",")):
            bad.append("attached %s, not %s" % (sorted(docs), c[7:]))
    elif c.startswith("logo="):
        if not any(n.startswith("word/media/logo.") for n in docs.get(c[5:], {})):
            bad.append("%s has no logo" % c[5:])
    elif c.startswith("banner="):
        for n, parts in docs.items():
            if c[7:] not in text(parts, "word/header1.xml") or c[7:] not in text(parts, "word/footer1.xml"):
                bad.append("%s: no banner %s" % (n, c[7:]))
    elif c.startswith("body="):
        if c[5:] not in body:
            bad.append("the body has no %s" % c[5:])
    elif c.startswith("subject="):
        if c[8:] not in (msg["Subject"] or ""):
            bad.append("subject %s has no %s" % (msg["Subject"], c[8:]))
    elif "~" in c:
        n, t = c.split("~", 1)
        if n not in docs:
            bad.append("no %s attached" % n)
        elif t not in text(docs[n]):
            bad.append("%s has no %r" % (n, t))
    else:
        bad.append("unknown check %s" % c)
if bad:
    sys.exit("; ".join(bad))
