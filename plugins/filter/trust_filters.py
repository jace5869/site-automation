# -*- coding: utf-8 -*-
"""The trust check (roles/check_trust, playbooks/trust_report.yml): the module's result as findings and as the
report's rows; vCenter's and the ESXi hosts' trust stores (roles/vmware_vm site_vmware_trust) the same way."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import os

ORDER = {"critical": 0, "warning": 1, "notice": 2}
KIND = {"system": "system CA bundle", "p11-kit": "p11-kit trust", "p11-kit-blocklist": "p11-kit blocklist", "blocklist": "blocklist folder",
        "java": "Java cacerts", "python": "Python certifi", "node": "Node built-in roots", "container": "podman container",
        "file": "certificate file", "vcenter": "vCenter trusted roots", "esxi": "ESXi trusted CAs",
        "nssdb": "NSS database", "winstore": "Windows certificate store"}
SOFTWARE = {"go": "Go program", "node": "Node.js", "nss": "NSS", "ca-certificates": "ca-certificates package"}
LABEL = {"critical": "COMPROMISED CA TRUSTED", "warning": "distrusted root still trusted", "notice": "notice", "blocked": "blocked", "": "ok"}
COLOUR = {"critical": "critical", "warning": "warning", "notice": "info", "blocked": "ok", "": "ok"}


def _sev(h, overrides):
    s = (overrides or {}).get(h.get("name"))
    return str(s or h.get("severity") or "warning").lower()


def _name(h):
    n = h.get("name", "?")
    if h.get("how") == "key":
        n += " (the same key, as %s)" % (h.get("cn") or "another certificate")
    return n


def _worst(hits, overrides):
    sev = [_sev(h, overrides) for h in hits]
    return min(sev, key=lambda s: ORDER.get(s, 1)) if sev else ""


def _live(store):
    """A store's hits that are not blocked (in that store)."""
    blocked = store.get("blocked") or []
    return [h for h in store.get("hits") or [] if h.get("name") not in blocked]


def store_status(store, overrides=None):
    """A store's status: the worst severity of what it holds (critical / warning / notice), 'blocked' when it only
    holds blocked ones, '' (ok) when nothing of the list is in it."""
    hits = _live(store)
    if hits:
        return _worst(hits, overrides)
    return "blocked" if store.get("blocked") else ""


def _group(store):
    """The finding a store belongs to: the system's bundles and p11-kit's live view are one thing (the system
    trust), every certifi bundle on the host one (the same fix in each environment); the rest one each."""
    k = store.get("kind", "")
    if k in ("system", "p11-kit", "winstore"):
        return "trust:system"
    if k == "python":
        return "trust:python"
    return "trust:%s:%s" % (k, store.get("where", "?"))


def _hint(kind, wheres):
    """A read-only command that shows the certificate in that kind of store, and the way out."""
    w = wheres[0] if wheres else ""
    if kind in ("system", "p11-kit"):
        return ("trust list | grep -i -B2 -A3 emsign; rpm -q ca-certificates  - fix: update ca-certificates (the update drops the root), or blocklist "
                "the certificate: copy its PEM into /etc/pki/ca-trust/source/blocklist/ and run update-ca-trust (Debian: dpkg-reconfigure ca-certificates)")
    if kind == "java":
        return ("keytool -list -keystore %s -storepass changeit | grep -i -B1 emsign  - fix: update the JDK, or keytool -delete -alias <alias> -keystore %s "
                "-storepass changeit (a cacerts that is a link to /etc/pki/java/cacerts follows the system trust: update-ca-trust refreshes it)" % (w, w))
    if kind == "python":
        return ("grep -c BEGIN %s  - fix: in each Python environment, pip install -U 'certifi>=2026.10.19' (pip's own copy under pip/_vendor: update pip); "
                "the environments: %s" % (w, ", ".join(_envs(wheres))))
    if kind == "node":
        return "%s --version  - fix: update Node to the fixed version of its line, or start it with --use-openssl-ca so it uses the system bundle" % w.split(" (")[0]
    if kind == "container":
        return "podman exec <container> cat /etc/pki/tls/certs/ca-bundle.crt | grep -c BEGIN  - fix: rebuild or pull the image with its updated ca-certificates, then restart the container"
    if kind == "file":
        return "openssl x509 -in %s -noout -subject -issuer -fingerprint -sha256  - the application's own bundle: update the application, or take the certificate out of the file" % w
    if kind == "nssdb":
        return "certutil -L -d sql:%s  - fix: certutil -D -d sql:%s -n '<nickname>' (check_trust_remove does this)" % (w, w)
    if kind == "winstore":
        return "certutil -store Root | findstr /i emsign  - fix: put the certificate in the Disallowed store and delete it from the store it is in (check_trust_remove does both)"
    return ""


def _envs(wheres):
    """The Python environments the certifi bundles belong to: /opt/app/venv/lib/python3.12/site-packages/certifi/cacert.pem
    -> /opt/app/venv; each once."""
    out = []
    for x in wheres:
        i = x.find("/lib/python")
        if i < 0:
            i = x.find("/lib64/python")
        env = x[:i] if i > 0 else os.path.dirname(os.path.dirname(x))
        if env not in out:
            out.append(env)
    return out


def _blocked_anywhere(scan):
    """The names any blocklist store (p11-kit's, the blocklist folders) holds."""
    out = set()
    for s in scan.get("stores") or []:
        if s.get("kind") in ("blocklist", "p11-kit-blocklist"):
            out.update(s.get("blocked") or [])
    return out


def trust_findings(scan, overrides=None, software_severity="warning"):
    """The module's result -> site_findings entries: one for the system trust, one for every certifi bundle, one
    per other store with an unblocked hit (its worst severity; notice-level hits are left out - they are in the
    report only); one per kind of program below the fixed version; one when no system bundle was read."""
    scan = scan or {}
    groups, order = {}, []
    for s in scan.get("stores") or []:
        hits = [h for h in _live(s) if _sev(h, overrides) != "notice"]
        if not hits:
            continue
        g = _group(s)
        if g not in groups:
            groups[g] = {"kind": "system" if s.get("kind") == "p11-kit" else s.get("kind", ""), "wheres": [], "hits": [], "version": ""}
            order.append(g)
        groups[g]["wheres"].append(s.get("where", "?"))
        groups[g]["version"] = groups[g]["version"] or s.get("version", "")
        for h in hits:
            if _name(h) not in [_name(x) for x in groups[g]["hits"]]:
                groups[g]["hits"].append(h)
    blocked = _blocked_anywhere(scan)
    out = []
    for g in order:
        d = groups[g]
        st = _worst(d["hits"], overrides)
        worst = [h for h in d["hits"] if _sev(h, overrides) == st]
        names = ", ".join(_name(h) for h in d["hits"])
        if d["kind"] == "system":
            ver = ""
            for p in scan.get("software") or []:
                if p.get("kind") == "ca-certificates":
                    ver = p.get("version", "")
            names_w = sorted(d["wheres"], key=lambda w: (0 if "tls-ca-bundle" in w or "ca-certificates.crt" in w else 1, w))
            where = "the system trust (%s%s%s)" % (os.path.basename(names_w[0]) if not names_w[0].startswith("p11-kit") else "p11-kit",
                                                    (" and %d more" % (len(names_w) - 1)) if len(names_w) > 1 else "", (", ca-certificates " + ver) if ver else "")
        elif d["kind"] == "python":
            where = "%d Python certifi bundle%s (%s)" % (len(d["wheres"]), "" if len(d["wheres"]) == 1 else "s", ", ".join(d["wheres"]))
        else:
            where = "%s%s (%s)" % (d["wheres"][0], (" " + d["version"]) if d["version"] else "", KIND.get(d["kind"], d["kind"]))
        note = ""
        also = [h["name"] for h in d["hits"] if h.get("name") in blocked]
        if also:
            note = " - %s %s in the blocklist already: run update-ca-trust so the bundles are rebuilt without it" % (", ".join(also), "is" if len(also) == 1 else "are")
        out.append({"check": "trust", "id": g, "severity": st,
                    "summary": "%s trusts %s - %s%s" % (where, names, worst[0].get("why", ""), note),
                    "hint": _hint(d["kind"], d["wheres"])})
    sw = {}
    for p in scan.get("software") or []:
        if p.get("status") in ("below", "unsupported"):
            sw.setdefault(p.get("kind", ""), []).append(p)
    for k, items in sw.items():
        if k == "go":
            progs = ["%s (%s, Go %s)" % (p.get("program") or os.path.basename(p.get("where", "")), p.get("where", ""), p.get("version", "?")) for p in items]
            if len(progs) > 10:
                progs = progs[:10] + ["and %d more (the report lists every one)" % (len(progs) - 10)]
            text = ("%d Go program%s built with a Go before the fix: %s - a Go program verifies certificates itself; Go 1.27.3 / 1.26.10 reject every "
                    "chain with one of the compromised keys (CVE-2026-111943). Rebuild each with a fixed Go, or get the vendor's build made with one"
                    % (len(items), "" if len(items) == 1 else "s", "; ".join(progs)))
            hint = "go version -m " + items[0].get("where", "")
        elif k == "node":
            text = "; ".join("Node.js %s (%s) is %s: its built-in roots still include the dropped root. Update Node, or start it with --use-openssl-ca"
                             % (p.get("version", "?"), p.get("where", ""), ("below " + p["fixed"]) if p.get("fixed") else "a line older than the ones that got the fix") for p in items)
            hint = items[0].get("where", "") + " --version"
        elif k == "nss":
            text = "NSS %s is below %s, the version that dropped emSign ECC Root CA - G3 from the roots NSS carries (Firefox, NSS clients without p11-kit): update the nss package" % (items[0].get("version", "?"), items[0].get("fixed", ""))
            hint = "rpm -q nss; dnf check-update nss"
        else:
            text = "%s %s is below %s: update the package" % (items[0].get("program") or k, items[0].get("version", "?"), items[0].get("fixed", ""))
            hint = "rpm -q " + (items[0].get("program") or k)
        out.append({"check": "trust", "id": "trust:software:" + k, "severity": software_severity or "warning", "summary": text, "hint": hint})
    if not any(s.get("kind") in ("system", "p11-kit") for s in scan.get("stores") or []):
        out.append({"check": "trust", "id": "trust:unreadable", "severity": "warning",
                    "summary": "no system CA bundle was read on this host (none of check_trust_bundles exists, and no p11-kit trust): where this host keeps its roots is unknown",
                    "hint": "ls -l /etc/pki/ca-trust/extracted/pem/ /etc/ssl/certs/ca-certificates.crt"})
    return out


def _holds(store, overrides):
    hits = _live(store)
    holds = ", ".join(_name(h) + (" [notice]" if _sev(h, overrides) == "notice" else "") for h in hits)
    if store.get("blocked"):
        holds += (", " if holds else "") + "blocked: " + ", ".join(store["blocked"])
    return "%d certificate%s%s" % (store.get("count", 0), "" if store.get("count") == 1 else "s", (": " + holds) if holds else "")


REMOVABLE = ("system", "p11-kit", "java", "python", "file", "vcenter", "esxi", "nssdb", "winstore")


def _fix(kind, where, fixed=""):
    """How a store loses the certificate (the removal does it where it can), or how a program is fixed."""
    if kind == "system":
        return "blocklist it and run update-ca-trust (check_trust_remove does this), or update ca-certificates"
    if kind == "p11-kit":
        return "follows the system trust: clears with the blocklist"
    if kind == "java":
        return "keytool -delete the entry (check_trust_remove does this), or update the JDK"
    if kind == "python":
        return "pip install -U certifi in %s (check_trust_remove cuts the block out)" % (_envs([where])[0] if where else "the environment")
    if kind == "file":
        return "take it out of the file (check_trust_remove does this); an application's own bundle: update the application"
    if kind == "node":
        return "update Node%s, or run it with --use-openssl-ca" % ((" to " + fixed) if fixed else "")
    if kind == "container":
        return "rebuild or pull the image with its updated ca-certificates, restart the container"
    if kind == "vcenter":
        return "delete the chain from vCenter's trusted roots (check_trust_remove on the VMware report)"
    if kind == "esxi":
        return "replace the host's trusted CA list without it (check_trust_remove on the VMware report)"
    if kind == "nssdb":
        return "certutil -D by its nickname (check_trust_remove does this)"
    if kind == "winstore":
        return "put it in the Disallowed store and delete it from this one (check_trust_remove does both; AuthRoot is Windows Update's: Disallowed overrides it)"
    if kind == "go":
        return ("rebuild it with Go %s or later" % fixed) if fixed else "rebuild it with a fixed Go (1.27.3 / 1.26.10): its line got no fix"
    if kind == "nss":
        return "update the nss package%s" % ((" to " + fixed) if fixed else "")
    if kind == "ca-certificates":
        return "update the ca-certificates package%s" % ((" to " + fixed) if fixed else "")
    return ("update to " + fixed) if fixed else "update it"


def trust_items(scan, host, overrides=None):
    """What the numbered sections of the report are made of: one item per store that holds something of the
    list, and per program below the fixed version - {host, where, what, kind, version, names, severity, fix}.
    severity: critical / warning / notice (the worst live hit), 'software' for a program."""
    items = []
    for st in scan.get("stores") or []:
        hits = _live(st)
        if not hits:
            continue
        sev = _worst(hits, overrides)
        names = [_name(h) for h in hits if _sev(h, overrides) == sev] if sev != "notice" else [_name(h) for h in hits]
        items.append({"host": host, "where": st.get("where", ""), "what": KIND.get(st.get("kind"), st.get("kind", "")), "kind": st.get("kind", ""),
                      "version": st.get("version", ""), "names": names, "severity": sev, "fix": _fix(st.get("kind", ""), st.get("where", ""))})
    for pr in scan.get("software") or []:
        if pr.get("status") in ("below", "unsupported"):
            items.append({"host": host, "where": pr.get("where", ""), "what": SOFTWARE.get(pr.get("kind"), pr.get("kind", "")), "kind": pr.get("kind", ""),
                          "version": pr.get("version", ""), "names": [pr.get("program", "")] if pr.get("kind") == "go" and pr.get("program") else [],
                          "severity": "software", "fix": _fix(pr.get("kind", ""), pr.get("where", ""), pr.get("fixed", ""))})
    return items


def trust_result(scan, host, overrides=None):
    """The module's result for the report: {'rows': [[host, where, what, version, holds, status]], 'row_status': [...],
    'skipped': [[host, where, why]], 'lines': [...] (the job output), 'hits': n}."""
    scan = scan or {}
    rows, rs, lines = [], [], []
    for s in scan.get("stores") or []:
        st = store_status(s, overrides)
        rows.append([host, s.get("where", ""), KIND.get(s.get("kind"), s.get("kind", "")), s.get("version", ""), _holds(s, overrides), LABEL[st]])
        rs.append(COLOUR[st])
        lines.append("%-8s %s (%s%s): %s" % (st.upper() if st in ("critical", "warning") else (st or "ok"), s.get("where", ""),
                                             KIND.get(s.get("kind"), s.get("kind", "")), (", " + s["version"]) if s.get("version") else "", _holds(s, overrides)))
    for p in scan.get("software") or []:
        st = p.get("status", "")
        label = {"below": "BELOW the fixed version", "unsupported": "older than every fixed line", "ok": "ok", "unknown": "version not compared"}.get(st, st)
        bad = st in ("below", "unsupported")
        rows.append([host, p.get("where", ""), SOFTWARE.get(p.get("kind"), p.get("kind", "")), p.get("version", ""),
                     ("fixed: " + p["fixed"]) if p.get("fixed") else (p.get("program", "") if p.get("kind") == "go" else ""), label])
        rs.append("warning" if bad else "ok")
        lines.append("%-8s %s (%s %s)%s" % ("BELOW" if bad else "ok", p.get("where", ""), p.get("kind", ""), p.get("version", ""), (" fixed: " + p["fixed"]) if p.get("fixed") else ""))
    skipped = [[host, x.get("where", ""), x.get("reason", "")] for x in scan.get("not_read") or []]
    c = scan.get("counts") or {}
    lines.insert(0, "%d stores read, %d certificates, %d programs looked at; %d not read" % (len(scan.get("stores") or []), c.get("certificates", 0), len(scan.get("software") or []), len(skipped)))
    return {"rows": rows, "row_status": rs, "skipped": skipped, "lines": lines, "items": trust_items(scan, host, overrides),
            "hits": sum(1 for s in scan.get("stores") or [] if store_status(s, overrides) in ("critical", "warning"))}


def _vmware_items(vm, overrides):
    out = []
    for st in vm.get("stores") or []:
        hits = _live(st)
        if not hits:
            continue
        sev = _worst(hits, overrides)
        names = [_name(h) for h in hits if _sev(h, overrides) == sev] if sev != "notice" else [_name(h) for h in hits]
        out.append({"host": st.get("vcenter", ""), "where": st.get("name", ""), "what": KIND.get(st.get("kind"), st.get("kind", "")), "kind": st.get("kind", ""),
                    "version": "", "names": names, "severity": sev, "fix": _fix(st.get("kind", ""), st.get("name", ""))})
    return out


def _item_table(title, items, status, text):
    rows = [[i["host"], i["where"], i["what"] + ((" " + i["version"]) if i.get("version") else ""), ", ".join(i["names"]), i["fix"]] for i in items]
    return {"title": title, "columns": ["Host", "Where", "What", "Certificate / program", "What to do"],
            "rows": rows, "row_status": [status] * len(rows), "cell_status": [[status, "", "", "", ""] for _ in rows],
            "status": status if status != "info" else "ok", "text": text}


def trust_report_sections(results, vmware=None, removed=None, overrides=None):
    """Every host's trust_result (+ the VMware module's result, + the hosts' removal results) -> the report's
    sections, in the order to act: 1 remove now (a compromised CA is trusted), 2 remove next (a distrusted root
    is still trusted), 3 then update (built-in roots, programs with their own trust logic), 4 listed only
    (notice-level, your decision); what was removed; then the proof - every store and program read, worst
    first (None: no host table - the VMware report); what was not read."""
    rows, rs, skipped, items = [], [], [], []
    for r in results or []:
        rows += r.get("rows") or []
        rs += r.get("row_status") or []
        skipped += r.get("skipped") or []
        items += r.get("items") or []
    vm = vmware or {}
    items += _vmware_items(vm, overrides)
    items.sort(key=lambda i: (i["host"], i["where"]))
    now = [i for i in items if i["severity"] == "critical"]
    nxt = [i for i in items if i["severity"] == "warning" and i["kind"] in REMOVABLE]
    upd = [i for i in items if i["severity"] == "software" or (i["severity"] == "warning" and i["kind"] not in REMOVABLE)]
    note = [i for i in items if i["severity"] == "notice"]
    sections = []
    hosts_of = lambda xs: len(set(i["host"] for i in xs))
    boxes = [{"label": "Remove now", "value": len(now), "status": "critical" if now else "ok"},
             {"label": "Remove next", "value": len(nxt), "status": "warning" if nxt else "ok"},
             {"label": "Then update", "value": len(upd), "status": "warning" if upd else "ok"},
             {"label": "Listed only", "value": len(note), "status": "info" if note else "ok"}]
    if now:
        sections.append(_item_table("1. Remove now - a compromised CA is trusted", now, "critical",
                                    "These stores hold one of the compromised intermediates: anything using them accepts certificates anyone can sign. "
                                    "Take the certificate out today (check_trust_remove: true does it, Check mode first)."))
    if nxt:
        sections.append(_item_table("2. Remove next - a distrusted root is still trusted", nxt, "warning",
                                    "The root the browsers dropped: it cross-signs the issuer of the compromised intermediates, so a client that still trusts "
                                    "it can be led to accept them. Blocklist or update at normal patching pace (check_trust_remove: true does it)."))
    if upd:
        sections.append(_item_table("3. Then update - built-in roots and programs with their own trust logic", upd, "warning",
                                    "Nothing to remove here: the roots are built into the program or the image. Update or rebuild; they stay in this list "
                                    "until then."))
    if note:
        sections.append(_item_table("4. Listed only - your decision", note, "info",
                                    "Notice-level: Sectigo's AAA Certificate Services cross-signed the eight intermediates, but it is not compromised, is in "
                                    "every trust store and most public certificates chain to it. No finding, no ticket. check_trust_severity: "
                                    "{\"AAA Certificate Services\": warning} when you decide to blocklist it."))
    if not (now or nxt or upd) and (results or vm.get("stores")):
        sections.append({"title": "Nothing to remove", "lines": ["No store holds a compromised or distrusted certificate, and no program is below the fixed version."
                                                                 + (" The notice-level entries are listed in section 4." if note else "")], "status": "ok"})
    removal = trust_remove_section(removed, vm.get("removed"))
    if removal:
        sections.append(removal)
    for s in sections:                          # these go above the per-host findings: what to act on, first
        s["top"] = True
    if sections:
        sections[0]["boxes"] = boxes
        if now:
            sections[0]["text"] = "%d store%s on %d host%s. " % (len(now), "" if len(now) == 1 else "s", hosts_of(now), "" if hosts_of(now) == 1 else "s") + sections[0]["text"]
    order = {"critical": 0, "warning": 1, "info": 2, "ok": 3}
    pairs = sorted(zip(rows, rs), key=lambda x: (order.get(x[1], 3), x[0][0], x[0][1]))
    rows, rs = [p[0] for p in pairs], [p[1] for p in pairs]
    if results is not None:
        sections.append({"title": "Everything read - every trust store and program, worst first", "columns": ["Host", "Where", "What", "Version", "Holds", "Status"],
                         "rows": rows, "row_status": rs, "cell_status": [[s if s != "ok" else "", "", "", "", "", s] for s in rs],
                         "text": "The proof of what was looked at. Red = holds a compromised CA, amber = holds a root that was dropped (or a program below the "
                                 "version that fixed it), blue = a notice-level certificate (listed only), green = nothing of the list in it, or blocked. "
                                 "The count is how many certificates the store holds.",
                         "status": "critical" if "critical" in rs else ("warning" if "warning" in rs else "ok")})
    if vm.get("stores") or vm.get("not_read") or vm.get("vcenters"):
        vrows, vrs = [], []
        for st in vm.get("stores") or []:
            x = store_status(st, overrides)
            vrows.append([st.get("vcenter", ""), st.get("name", ""), KIND.get(st.get("kind"), st.get("kind", "")), _holds(st, overrides), LABEL[x]])
            vrs.append(COLOUR[x])
        for n in vm.get("not_read") or []:
            vrows.append([n.get("vcenter", ""), n.get("name", ""), KIND.get(n.get("kind"), n.get("kind", "")), "not read: " + n.get("why", ""), "not read"])
            vrs.append("warning")
        for v in vm.get("vcenters") or []:
            if v.get("error"):
                vrows.append([v.get("name", ""), "", "vCenter", "not read: " + v["error"], "not read"])
                vrs.append("warning")
        sections.append({"title": "VMware - everything read: vCenter's trusted roots and each ESXi host's trusted CAs", "columns": ["vCenter", "Name", "What", "Holds", "Status"],
                         "rows": vrows, "row_status": vrs, "cell_status": [["", "", "", "", x if x != "ok" else ""] for x in vrs],
                         "status": "critical" if "critical" in vrs else ("warning" if "warning" in vrs else "ok"),
                         "text": "vCenter: its trusted root store (Administration > Certificates > Trusted Root Certificates: what vCenter and the hosts it "
                                 "manages trust for outbound TLS). ESXi: the CA certificates each host trusts. The count is how many certificates the store holds."})
    if skipped:
        sections.append({"title": "Not read", "columns": ["Host", "Where", "Why"], "rows": skipped,
                         "text": "A keystore keytool could not open, a container without a shell, a search that took longer than check_trust_timeout."})
    return sections


def trust_vmware_doc(vmware, overrides=None, check_mode=False):
    """The VMware module's result -> the VMware trust store report (roles/vmware_vm report.yml: title, status,
    sections, bad = why the job fails: the stores that hold one, or nothing read at all)."""
    vm = vmware or {}
    stores = vm.get("stores") or []
    bad, worst = [], "ok"
    for s in stores:
        st = store_status(s, overrides)
        if st in ("critical", "warning"):
            bad.append("%s %s (%s) trusts %s" % (KIND.get(s.get("kind"), s.get("kind", "")), s.get("name", ""), s.get("vcenter", ""),
                                                 ", ".join(h["name"] for h in s.get("hits") or [])))
        if st == "critical" or (st == "warning" and worst != "critical"):
            worst = st
    n_vc = sum(1 for s in stores if s.get("kind") == "vcenter")
    n_esx = sum(1 for s in stores if s.get("kind") == "esxi")
    not_read = len(vm.get("not_read") or [])
    removed = vm.get("removed") or []
    what = "%d vCenter%s, %d ESXi host%s read" % (n_vc, "" if n_vc == 1 else "s", n_esx, "" if n_esx == 1 else "s")
    if not_read:
        what += ", %d not read" % not_read
    if bad:
        title = "VMware trust store report: %d store%s hold%s a compromised or dropped CA - %s" % (len(bad), "" if len(bad) == 1 else "s", "s" if len(bad) == 1 else "", what)
    elif not stores:
        title = "VMware trust store report: nothing could be read - %s" % what
        worst = "warning"
        bad.append("nothing could be read: " + "; ".join("%s %s: %s" % (n.get("kind", ""), n.get("name", ""), n.get("why", ""))
                                                        for n in (vm.get("not_read") or [])[:3]))
    else:
        title = "VMware trust store report: nothing of the list - %s" % what
    if removed:
        done = sum(1 for r in removed if r.get("done"))
        title += "; %d removed" % done if not check_mode else "; %d would be removed" % len(removed)
    return {"title": title, "status": worst if worst != "ok" else "ok", "bad": bad,
            "sections": trust_report_sections(None, vm, None, overrides)}


def _remove_rows(host, result):
    """One row per action and per store left alone: [host, where, certificates, what was done, outcome], status."""
    rows, rs = [], []
    for a in (result or {}).get("actions") or []:
        if a.get("planned"):
            outcome, status = "would be removed (Check mode)", "info"
        elif a.get("done"):
            outcome, status = "removed", "ok"
        else:
            outcome, status = "NOT removed: " + (a.get("error") or "nothing to do"), "critical" if a.get("error") else "warning"
        what = a.get("action", "")
        if a.get("backup"):
            what += " (backup: %s)" % a["backup"]
        rows.append([host, a.get("where", ""), ", ".join(a.get("names") or []), what, outcome])
        rs.append(status)
    for n in (result or {}).get("not_removed") or []:
        rows.append([host, n.get("where", ""), ", ".join(n.get("names") or []), "left alone: " + n.get("why", ""), "not removable here"])
        rs.append("warning")
    return rows, rs


def trust_remove_section(removed=None, vmware_removed=None):
    """The per-host removal results (check_trust_removed, each with the host under 'host') and the VMware module's
    removed list -> the report's section 'Removed', or None when nothing was tried."""
    rows, rs = [], []
    for r in removed or []:
        if not r:
            continue
        a, b = _remove_rows(r.get("host", ""), r)
        rows += a
        rs += b
    for v in vmware_removed or []:
        a, b = _remove_rows(v.get("vcenter", ""), {"actions": [dict(v, where="%s %s" % (KIND.get(v.get("kind"), v.get("kind", "")), v.get("name", "")))]})
        rows += a
        rs += b
    if not rows:
        return None
    return {"title": "Removed (check_trust_remove)", "columns": ["Host", "Where", "Certificates", "What was done", "Outcome"],
            "rows": rows, "row_status": rs, "cell_status": [["", "", "", "", s if s != "ok" else ""] for s in rs],
            "status": "critical" if "critical" in rs else ("warning" if "warning" in rs else "ok"),
            "text": "The system trust is blocklisted, not edited; every file changed was copied to the backup folder first. "
                    "Left alone: a store this cannot change (a container's image, Node, a Go program: update or rebuild), "
                    "a file a package owns, a kind not in check_trust_remove_kinds. The table above shows what is left after the removal."}


def trust_remove_lines(result, host=""):
    """The removal module's result -> lines for the job output."""
    rows, rs = _remove_rows(host, result)
    out = ["%s  %s: %s - %s - %s" % (s.upper() if s != "ok" else "OK", r[1], r[2], r[3], r[4]) for r, s in zip(rows, rs)]
    return out or ["nothing to remove"]


class FilterModule(object):
    def filters(self):
        return {"trust_findings": trust_findings, "trust_result": trust_result, "trust_report_sections": trust_report_sections, "trust_items": trust_items,
                "trust_store_status": store_status, "trust_remove_section": trust_remove_section, "trust_remove_lines": trust_remove_lines,
                "trust_vmware_doc": trust_vmware_doc}
