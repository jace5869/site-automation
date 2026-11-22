# -*- coding: utf-8 -*-
"""DCs use an object/DSA GUID, else their exact server/NTDS DN, else domain|short name.
Sites use a GUID, exact DN, or exact case-insensitive name. Domains are exact lower-case
DNS names without a trailing dot. Links use receiver, source and exact partition.
Short names match only one identity in the whole polled and discovered population.
"""
import re


def domain_id(name):
    return str(name or "").strip().lower().rstrip(".")


def short_name(rec):
    return str(rec.get("dc") or rec.get("name") or rec.get("fqdn") or "").split("\\")[-1].split(".")[0].lower()


def domain(rec):
    fq = domain_id(rec.get("fqdn") or rec.get("host") or rec.get("name") or rec.get("dc"))
    return domain_id(rec.get("domain")) or (fq.split(".", 1)[1] if "." in fq and not re.fullmatch(r"\d+(?:\.\d+){3}", fq) else "")


def dc_id(rec):
    for key in ("objectGUID", "object_guid", "dsa_guid"):
        if rec.get(key):
            return str(rec[key]).lower().strip("{}")
    for key in ("dsa_dn", "server_dn", "dn"):
        if rec.get(key):
            return str(rec[key]).lower()
    if rec.get("dc_id"):
        return str(rec["dc_id"])
    return domain(rec) + "|" + short_name(rec)


def site_id(rec):
    for key in ("site_guid", "objectGUID", "object_guid", "site_dn", "dn", "site", "name"):
        if rec.get(key):
            value = str(rec[key]).lower()
            return value.strip("{}") if key in ("site_guid", "objectGUID", "object_guid") else value
    return ""


def dc_site(rec):
    return site_id({k: rec.get(k) for k in ("site_guid", "site_dn", "site")})


def same_dc(a, b):
    return dc_id(a) == dc_id(b)


def resolve(rec, population):
    """Resolve an alias only with compatible evidence, never across a known domain/site."""
    by = {dc_id(d): d for d in population}
    if dc_id(rec) in by:
        return by[dc_id(rec)]
    matches = []
    authority = ("objectGUID", "object_guid", "dsa_guid", "dsa_dn", "server_dn")
    for d in by.values():
        if any(rec.get(k) and d.get(k) and str(rec[k]).lower() != str(d[k]).lower()
               for k in ("objectGUID", "object_guid", "dsa_guid")):
            continue
        if any(rec.get(k) and str(rec[k]).lower() == str(d.get(k) or "").lower() for k in authority):
            matches.append(d)
            continue
        if any(rec.get(k) for k in authority) or (rec.get("dc_id") and "|" not in rec["dc_id"]):
            continue
        if short_name(d) != short_name(rec):
            continue
        if domain(rec) and domain(d) != domain(rec):
            continue
        if dc_site(rec) and dc_site(d) != dc_site(rec):
            continue
        matches.append(d)
    if len(matches) != 1:
        return None
    if domain(rec) or dc_site(rec) or any(rec.get(k) for k in authority):
        return matches[0]
    return matches[0] if len({dc_id(d) for d in by.values() if short_name(d) == short_name(rec)}) == 1 else None


def source(link):
    """The partner's evidence, separate from its display name and invocation ID."""
    name = str(link.get("partner") or link.get("source") or link.get("src") or link.get("frm") or "")
    rec = {"name": name, "domain": link.get("source_domain") or "", "site": link.get("source_site") or "",
           "dsa_guid": link.get("partner_guid") or "", "dsa_dn": link.get("partner_dn") or ""}
    old = str(link.get("partner_id") or link.get("src") or "")
    if not rec["dsa_dn"] and old.lower().startswith("cn="):
        rec["dsa_dn"] = old
    elif not rec["dsa_guid"] and old != str(link.get("invocation_id") or "") and re.fullmatch(r"[0-9a-fA-F-]{36}", old):
        rec["dsa_guid"] = old
    dn = rec["dsa_dn"]
    site = re.search(r",CN=Servers,(CN=((?:\\,|[^,])+),CN=Sites,.*)$", dn, re.I)
    if site:
        rec["site_dn"] = site.group(1)
        rec["site"] = rec["site"] or site.group(2)
    if not rec["site"] and "\\" in name:
        rec["site"] = name.split("\\", 1)[0]
    # Older repadmin output has only a site/server reference. Keep it unresolved,
    # distinct from every domain-qualified controller until discovery resolves it.
    if rec["site"] and not domain(rec):
        rec["dc_id"] = "|" + site_id(rec) + "\\" + short_name(rec)
    return rec


def link_id(link):
    dst = link.get("dst_id") or dc_id({"dc": link.get("to") or link.get("to_dc"), "domain": link.get("domain")})
    src = link.get("src_id") or dc_id(source(link))
    return (dst, src, str(link.get("nc_dn") or link.get("nc") or "").lower())


def fields(rec):
    """Identity evidence to carry across report boundaries."""
    return {k: rec[k] for k in ("dc_id", "objectGUID", "object_guid", "dsa_guid", "dsa_dn", "server_dn",
                                "site_dn", "site_guid", "site", "fqdn", "invocation_id") if rec.get(k)}
