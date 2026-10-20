# -*- coding: utf-8 -*-
"""Filters for the certificate renewal's ServiceNow catalog orders (cert_renew_servicenow: catalog -
roles/servicenow/tasks/catalog_item.yml, cert_order.yml, cert_on_order.yml): the catalog item's questions
checked against cert_renew_catalog_variables, each order's answers, and the name the CSR carries in the
order - with the expiring certificate's date, so a run again finds the order it made for that certificate."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re

# ServiceNow's question types (item_option_new.type)
TYPES = {0: "variable set", 1: "yes/no", 2: "multi line text", 3: "multiple choice", 4: "numeric scale", 5: "select box",
         6: "single line text", 7: "checkbox", 8: "reference", 9: "date", 10: "date/time", 11: "label", 12: "break",
         14: "macro", 15: "UI page", 16: "wide single line text", 17: "macro with label", 18: "lookup select box",
         19: "container start", 20: "container end", 21: "list collector", 22: "lookup multiple choice", 23: "HTML",
         24: "container split", 25: "masked", 26: "email", 27: "URL", 28: "IP address", 29: "duration",
         31: "requested for", 32: "rich text label", 33: "attachment"}
NO_ANSWER = {0, 11, 12, 14, 15, 17, 19, 20, 24, 32}    # shown on the form, nothing to fill in (0: a variable set's own entry)
REFERENCE = {8, 31}                                    # a record: its sys_id
CHOICE = {3, 5}                                        # one of its choices: their value
ATTACHMENT = 33
SYS_ID = re.compile(r"^[0-9a-f]{32}$")
# a requested item's states (sc_req_item.state), for the report
RITM_STATES = {"-5": "Pending", "1": "Open", "2": "Work in Progress", "3": "Closed Complete", "4": "Closed Incomplete",
               "7": "Closed Skipped"}


def _type(q):
    """A question's type as a number: the API gives a number, or its text; a friendly name says attachment."""
    t = q.get("type")
    try:
        return int(t)
    except (TypeError, ValueError):
        return ATTACHMENT if "attachment" in str(q.get("friendly_type") or t or "").lower() else 0


def _questions(variables):
    """The item's questions, flat (a container's or a variable set's come as its children)."""
    out = []
    for q in variables or []:
        if not isinstance(q, dict):
            continue
        out.append(q)
        out.extend(_questions(q.get("children")))
    return out


def _text(v, size=120):
    """A label as text: a rich text label's HTML tags and entities out, spaces squeezed, cut at size."""
    t = re.sub(r"\s+", " ", re.sub(r"&[a-z]+;|&#[0-9]+;", " ", re.sub(r"<[^>]*>", " ", str(v or "")))).strip()
    return t if len(t) <= size else t[:size - 3].rstrip() + "..."


def _truthy(v):
    return v is True or str(v).lower() == "true"


def cert_catalog_questions(variables):
    """The item's questions as the catalog test prints them: name, label, type (its number and name), mandatory,
    the table a reference points to, the choices (value = label), the default."""
    out = []
    for q in _questions(variables):
        t = _type(q)
        out.append({"name": str(q.get("name") or ""), "label": _text(q.get("label")), "type": t,
                    "type_name": TYPES.get(t, str(q.get("type"))), "mandatory": _truthy(q.get("mandatory")),
                    "reference": str(q.get("reference") or ("sys_user" if t == 31 else "")),
                    "choices": [(str(c.get("value", "")), str(c.get("label", ""))) for c in q.get("choices") or [] if isinstance(c, dict)],
                    "default": str(q.get("value") or ""), "answer": t not in NO_ANSWER})
    return out


def cert_catalog_check(variables, values, csr_var="", names_var=""):
    """The item's questions (its 'variables', from the Service Catalog API) against the settings: which question
    gets the CSR, the answers (a choice's label becomes its value), the reference answers still to look up (not a
    sys_id), and what is wrong - a question that is not on the item, a mandatory one left empty, a choice the
    question does not have. -> {csr_var, names_var, answers, lookups [{name, table, value}], errors, warnings}"""
    qs = [q for q in cert_catalog_questions(variables) if q["name"]]
    by = dict((q["name"], q) for q in qs)
    values = dict(values or {})
    errors, warnings, lookups = [], [], []
    names = ", ".join(sorted(by)) or "none"
    attach = [q["name"] for q in qs if q["type"] == ATTACHMENT]
    if csr_var:
        if csr_var not in by:
            errors.append("cert_renew_catalog_csr_variable: the item has no question %s (its questions: %s)" % (csr_var, names))
        elif by[csr_var]["type"] != ATTACHMENT:
            errors.append("cert_renew_catalog_csr_variable: %s is a %s question, not an attachment" % (csr_var, by[csr_var]["type_name"]))
    elif len(attach) == 1:
        csr_var = attach[0]
    elif len(attach) > 1:
        errors.append("the item has %d attachment questions (%s): set cert_renew_catalog_csr_variable to the one for the CSR"
                      % (len(attach), ", ".join(attach)))
    else:
        warnings.append("the item has no attachment question: the CSR is attached to the request (the paperclip)")
    if names_var and names_var not in by:
        errors.append("cert_renew_catalog_names_variable: the item has no question %s (its questions: %s)" % (names_var, names))
    out, wrong = {}, set()
    for name in sorted(values):
        v = values[name]
        v = ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else ("" if v is None else str(v))
        q = by.get(name)
        if q is None:
            errors.append("cert_renew_catalog_variables: the item has no question %s (its questions: %s)" % (name, names))
            continue
        if name == csr_var:
            errors.append("cert_renew_catalog_variables: %s is the CSR's question - the job fills it in" % name)
            continue
        if q["type"] in CHOICE and q["choices"] and v and "{" not in v:
            match = [cv for cv, cl in q["choices"] if v in (cv, cl) or v.lower() in (cv.lower(), cl.lower())]
            if not match:
                errors.append("%s (%s): %r is not one of its choices (%s)" % (
                    name, q["label"], v, ", ".join("%s = %s" % (cv, cl) for cv, cl in q["choices"] if cv)))
                wrong.add(name)
                continue
            v = match[0]
        if q["type"] in REFERENCE and v and not SYS_ID.match(v) and "{" not in v:
            lookups.append({"name": name, "table": q["reference"] or "sys_user", "value": v})
        out[name] = v
    for q in qs:
        if q["mandatory"] and q["answer"] and q["name"] not in (csr_var, names_var) and q["name"] not in wrong \
           and not out.get(q["name"]) and not q["default"]:
            errors.append("%s (%s) is mandatory: give it a value in cert_renew_catalog_variables" % (q["name"], q["label"]))
    return {"csr_var": csr_var, "names_var": names_var, "answers": out, "lookups": lookups, "errors": errors, "warnings": warnings}


def cert_catalog_resolve(check, found):
    """The reference answers looked up by name (found: [{name, table, value, rows}]) -> the check, with each
    one's sys_id in its values, or an error: no record, or several with that name."""
    check = dict(check or {})
    values, errors = dict(check.get("answers") or {}), list(check.get("errors") or [])
    for f in found or []:
        v = str(f.get("value", "")).lower()
        rows = [r for r in f.get("rows") or [] if v in (str(r.get("name", "")).lower(), str(r.get("user_name", "")).lower())]
        if len(rows) == 1:
            values[f["name"]] = rows[0].get("sys_id", "")
        elif not rows:
            errors.append("%s: no record named %r in %s - give its sys_id instead" % (f["name"], f.get("value"), f.get("table")))
        else:
            errors.append("%s: %d records named %r in %s - give the sys_id of the right one (%s)" % (
                f["name"], len(rows), f.get("value"), f.get("table"), ", ".join(r.get("sys_id", "") for r in rows)))
    check.update(answers=values, errors=errors)
    return check


def cert_catalog_date(expires):
    """The expiring certificate's date as the order's key: '2026-11-14 10:00 UTC' -> '20261114' ('' without one)."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(expires or ""))
    return "".join(m.groups()) if m else ""


def cert_catalog_name(file_name, expires):
    """The CSR's name in the order: its file name with the expiring certificate's date before the extension -
    web01.example.mil-1a2b3c4d.csr -> web01.example.mil-1a2b3c4d-exp20261114.csr. A run again finds the
    order by it; once the certificate is replaced, its successor has another date: a new order."""
    d = cert_catalog_date(expires)
    stem, dot, ext = str(file_name or "").rpartition(".")
    if not dot:
        stem, ext = ext, ""
    return "%s%s%s" % (stem, ("-exp" + d) if d else "", ("." + ext) if ext else "")


def cert_catalog_query(prefix, expires, tables):
    """The attachment query that finds the orders of one certificate: a CSR whose name starts with prefix
    (a CSR's name, or a host's for one made on vCenter, a host or a device), for this expiry date, on a
    record of these tables (ServiceNow keeps an attachment question's file as ZZ_YY<table>)."""
    d = cert_catalog_date(expires)
    t = []
    for x in tables or ["sc_req_item"]:
        t.extend([x, "ZZ_YY" + x])
    q = "file_nameSTARTSWITH%s" % prefix
    if d:
        q += "^file_nameLIKE-exp%s." % d
    return q + "^table_nameIN" + ",".join(t)


def cert_catalog_vars(cert, values, csr_var="", attachment="", names_var=""):
    """One order's answers: the settings' (each {host}, {subject}, {names}, {expires}, {issuer}, {where} replaced
    by the certificate's), the CSR's attachment for its question, the certificate's names for theirs."""
    c = cert or {}
    names = ", ".join(str(x) for x in c.get("sans") or [])
    subs = {"host": c.get("host", ""), "subject": c.get("subject", ""), "names": names, "expires": c.get("expires", ""),
            "issuer": c.get("issuer", ""), "where": " ; ".join(c.get("wheres") or [])}
    out = {}
    for k, v in (values or {}).items():
        v = str(v)
        for key, sub in subs.items():
            v = v.replace("{%s}" % key, str(sub))
        out[k] = v
    if names_var:
        out[names_var] = names
    if csr_var and attachment:
        out[csr_var] = attachment
    return out


def cert_catalog_state(state):
    """A requested item's state as ServiceNow shows it: '4' -> 'Closed Incomplete'."""
    return RITM_STATES.get(str(state), str(state))


class FilterModule(object):
    def filters(self):
        return {"cert_catalog_questions": cert_catalog_questions, "cert_catalog_check": cert_catalog_check,
                "cert_catalog_resolve": cert_catalog_resolve, "cert_catalog_name": cert_catalog_name,
                "cert_catalog_date": cert_catalog_date, "cert_catalog_query": cert_catalog_query,
                "cert_catalog_vars": cert_catalog_vars, "cert_catalog_state": cert_catalog_state}
