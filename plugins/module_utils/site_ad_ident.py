# -*- coding: utf-8 -*-
"""One forest, one domain. DC host labels are keys; AD supplies their attributes."""
import re

REFUSAL = "More than one AD domain found - not supported in this version"
COVERAGE_UNKNOWN = "DC list not read - coverage unknown"


def domain_id(name):
    return str(name or "").strip().lower().rstrip(".")


def short_name(rec):
    name = str(rec.get("dns_host") or rec.get("dNSHostName") or rec.get("fqdn") or rec.get("dc") or rec.get("sAMAccountName") or rec.get("name") or "")
    if name.startswith("dsa:"):
        return name.lower()
    return name.split("\\")[-1].split(".")[0].rstrip("$").lower()


def domain(rec):
    return domain_id(rec.get("domain"))


def dc_id(rec):
    return short_name(rec)


def site_id(rec):
    return str(rec.get("site") or rec.get("name") or "").lower()


def dc_site(rec):
    return str(rec.get("site") or "").lower()


def same_dc(a, b):
    return dc_id(a) == dc_id(b)


def resolve(rec, population):
    for d in population:
        if any(rec.get(k) and str(rec[k]).lower() == str(d.get(k) or "").lower()
               for k in ("dsa_guid", "dsa_dn", "server_dn")):
            return d
    return next((d for d in population if dc_id(rec) and same_dc(rec, d)), None)


def source(link):
    name = str(link.get("partner") or link.get("source") or link.get("frm") or "")
    guid = str(link.get("partner_guid") or "").lower().strip("{}")
    dn = str(link.get("partner_dn") or "")
    old = str(link.get("partner_id") or "")
    if not dn and old.lower().startswith("cn="):
        dn = old
    if not guid and old != str(link.get("invocation_id") or "") and re.fullmatch(r"[0-9a-fA-F-]{36}", old):
        guid = old.lower()
    hidden = re.search(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", name)
    if name.startswith("dsa:") or hidden or name.startswith("("):
        guid = guid or (hidden.group(0).lower() if hidden else "")
        name = ""
    return {"name": name, "domain": link.get("source_domain") or "", "site": link.get("source_site") or "",
            "dsa_guid": guid, "dsa_dn": dn}


def partner(link, population):
    rec = source(link)
    found = resolve(rec, population)
    if found:
        if rec.get("dsa_guid") and found.get("dsa_guid") and rec["dsa_guid"].lower() != found["dsa_guid"].lower():
            found["stale_dsa"] = True
        return found
    # A named repadmin partner is AD evidence; an unresolved DSA is not a DC.
    if rec["name"] and not link.get("unresolved") and (link.get("unresolved") is False or (not rec["dsa_guid"] and not rec["dsa_dn"])):
        return dict(rec, name=short_name(rec).upper(), read=False, why="not polled")
    return dict(rec, name="dsa:" + (rec["dsa_guid"] or rec["dsa_dn"].lower() or "unknown"))


def link_id(link):
    return (str(link.get("dst_id") or link.get("dst") or link.get("to") or link.get("to_dc") or "").lower(),
            str(link.get("src_id") or link.get("frm") or dc_id(source(link))).lower(),
            str(link.get("nc_dn") or link.get("nc") or "").lower())


def fields(rec):
    return {k: rec[k] for k in ("dsa_guid", "dsa_dn", "server_dn", "site", "fqdn", "dns_host", "invocation_id",
                                "stale_dsa", "node_id", "ip", "forest") if rec.get(k)}


def merge(population):
    out = {}
    for d in population:
        key = dc_id(d)
        if not key or key.startswith("dsa:"):
            continue
        old = out.get(key, {})
        stale = old.get("stale_dsa") or d.get("stale_dsa") or (old.get("dsa_guid") and d.get("dsa_guid") and
                old["dsa_guid"].lower() != d["dsa_guid"].lower())
        out[key] = dict(old, **d)
        if stale:
            out[key]["stale_dsa"] = True
    return out


def unsupported(data):
    """Only AD evidence: never a host's DNS suffix or a SolarWinds caption."""
    domains, forests, where, ncs = set(), set(), [], []

    def walk(value, path="AD"):
        if isinstance(value, list):
            for v in value:
                walk(v, path)
        elif isinstance(value, dict):
            for k, v in value.items():
                if k in ("live", "sw", "solarwinds"):
                    continue
                if k in ("domain", "source_domain", "_domain", "dns") and isinstance(v, str) and v and v != "?" and not path.endswith(".forest"):
                    domains.add(domain_id(v))
                    where.append(("DC domain attribute (%s): " % short_name(value) if short_name(value) and k == "domain" else path + ": ") + k + "=" + v)
                elif (k == "forest" or (k in ("name", "dns") and path.endswith(".forest"))) and isinstance(v, str) and v:
                    forests.add(domain_id(v))
                    where.append(path + ": forest=" + v)
                elif k == "domains" and isinstance(v, list) and all(isinstance(x, str) for x in v):
                    domains.update(domain_id(x) for x in v if x)
                    where.append(path + ": domains=" + ", ".join(v))
                elif k in ("nc", "nc_dn") and isinstance(v, str):
                    nc = re.sub(r"^DC=(?:DomainDnsZones|ForestDnsZones),", "", v, flags=re.I)
                    if re.fullmatch(r"DC=[^,]+(?:,DC=[^,]+)*", nc, re.I):
                        ncs.append((domain_id(".".join(x[3:] for x in nc.split(","))), v))    # weighed after the walk
                elif isinstance(v, (dict, list)):
                    walk(v, path + "." + k)
    walk(data)
    # a partition NC under a known domain's DN with more DC= labels in front (an application partition such as
    # DC=TAPI3Directory,DC=ad,...) is that domain's partition, not another domain; shortest first, so the domain's own NC
    # is known before its application partitions
    for name, v in sorted(ncs, key=lambda x: len(x[0])):
        if any(name == k or name.endswith("." + k) for k in domains):
            continue
        domains.add(name)
        where.append("partner NC: " + v)
    return "; ".join(where) if len(domains) > 1 or len(forests) > 1 else ""


def coverage(repl):
    if not repl.get("dcs") or not repl.get("discovered", True):
        return COVERAGE_UNKNOWN
    dcs = repl.get("dcs") or []
    read = [d for d in dcs if d.get("read") and not d.get("partial")]
    unread = [d for d in dcs if d not in read]
    text = "Replication read in full on %d of %d DCs" % (len(read), len(dcs))
    if unread:
        text += "; not read: " + ", ".join("%s (%s)" % (d["name"], ", ".join(d.get("partial") or []) or d.get("why") or "not polled") for d in unread)
    return text


def links(rows):
    """One inbound edge per partition; keep the worst observation during metadata lag."""
    rank = {"critical": 0, "warning": 1, "unknown": 2, "low": 3, "ok": 4}
    out = {}
    def order(x):
        return (rank.get(x.get("status"), 5), -int(x.get("failures") or 0), str(x.get("last_success") or ""))
    for row in rows:
        key = link_id(row)
        if key not in out or order(row) < order(out[key]):
            out[key] = row
    return list(out.values())
