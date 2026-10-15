# -*- coding: utf-8 -*-
"""Filters for the Active Directory health report (roles/ad_report): what the collectors
(roles/ad_report/files/ad_dc.ps1 on every domain controller, ad_domain.ps1 on one DC per domain)
read, as a report in the layout roles/site_email renders - problems first, every row coloured by
severity. The collectors only gather facts; every judgement (red, amber, green) is made here."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import re

RANK = {"critical": 0, "warning": 1, "unknown": 2, "info": 3, "ok": 4, "": 5}

# dcdiag tests whose failure means the DC is not doing its job (the others are amber)
DCDIAG_CRITICAL = {"connectivity", "advertising", "netlogons", "replications", "sysvolcheck", "services",
                   "knowsofroleholders", "ridmanager", "machineaccount"}

# Services a DC needs. DNS only where it is installed; DFSR / NtFrs: whichever replicates SYSVOL.
SERVICES = {"ntds": ("Active Directory Domain Services", "critical"), "netlogon": ("Netlogon", "critical"),
            "kdc": ("Kerberos Key Distribution Center", "critical"), "dns": ("DNS Server", "critical"),
            "w32time": ("Windows Time", "critical"), "lanmanserver": ("Server (SYSVOL / NETLOGON shares)", "critical"),
            "adws": ("Active Directory Web Services", "warning"), "ismserv": ("Intersite Messaging", "warning")}

# DFSR's state of the SYSVOL replicated folder (DfsrReplicatedFolderInfo.State)
DFSR_STATE = {0: ("Uninitialized", "critical"), 1: ("Initialized", "warning"), 2: ("Initial sync", "warning"),
              3: ("Auto recovery", "warning"), 4: ("Normal", "ok"), 5: ("In error", "critical")}

# Event log entries that matter on a DC: (log, id) -> (severity, what it means). Other errors are
# listed for information.
EVENTS = {
    ("Directory Service", 467): ("critical", "the AD database is corrupt (restore or repair it)"),
    ("Directory Service", 1168): ("critical", "internal error in Active Directory"),
    ("Directory Service", 2042): ("critical", "it has not replicated with a partner for longer than the tombstone lifetime: replication with it is blocked"),
    ("Directory Service", 1388): ("critical", "lingering objects came in from a partner (strict replication consistency is off)"),
    ("Directory Service", 1988): ("critical", "a partner tried to replicate a lingering object: replication from it is blocked"),
    ("Directory Service", 2087): ("critical", "replication failed: the partner's DNS name could not be resolved"),
    ("Directory Service", 2088): ("warning", "the partner's DNS name could not be resolved; replication fell back to NetBIOS / IP"),
    ("Directory Service", 1311): ("warning", "the KCC cannot build a replication topology that connects every site"),
    ("Directory Service", 1865): ("warning", "the KCC cannot build a spanning tree: some sites cannot be reached"),
    ("Directory Service", 1925): ("warning", "a replication link to a partner could not be set up"),
    ("Directory Service", 1926): ("warning", "replication with a partner failed"),
    ("Directory Service", 1084): ("warning", "an inbound change could not be applied"),
    ("Directory Service", 1645): ("warning", "the DC could not register a Kerberos service principal name"),
    ("Directory Service", 2089): ("warning", "a partition has not been backed up within the backup latency interval"),
    ("Directory Service", 2886): ("info", "LDAP signing is not required: unsigned binds are accepted"),
    ("Directory Service", 2887): ("warning", "clients made unsigned or clear-text LDAP binds (this event counts them; 2889 names them when diagnostic logging is on)"),
    ("Directory Service", 2889): ("info", "a client made an unsigned or clear-text LDAP bind"),
    ("DFS Replication", 2213): ("critical", "DFSR stopped replicating SYSVOL after an unexpected shutdown and waits for a manual resume"),
    ("DFS Replication", 4012): ("critical", "DFSR stopped replicating SYSVOL: the DC was disconnected longer than MaxOfflineTimeInDays"),
    ("DFS Replication", 2104): ("critical", "DFSR failed to recover from an internal database error"),
    ("DFS Replication", 4614): ("warning", "SYSVOL waits for its initial synchronization (it is not shared until then)"),
    ("DFS Replication", 5002): ("warning", "DFSR could not reach a replication partner"),
    ("DFS Replication", 5008): ("warning", "DFSR could not reach a replication partner"),
    ("DFS Replication", 5014): ("info", "a DFSR connection was interrupted (usually recovers by itself)"),
    ("DNS Server", 4000): ("critical", "the DNS server could not open Active Directory: AD-integrated zones are not loaded"),
    ("DNS Server", 4004): ("critical", "the DNS server could not enumerate its AD-integrated zones"),
    ("DNS Server", 4015): ("critical", "the DNS server got a critical error from Active Directory"),
    ("DNS Server", 4013): ("warning", "the DNS server waits for AD's initial synchronization (normal right after a restart)"),
    ("System", 5719): ("warning", "Netlogon could not reach a domain controller"),
    ("System", 5722): ("warning", "a computer's secure channel failed (its machine password)"),
    ("System", 5723): ("warning", "a computer with no account in the domain tried to log on"),
    ("System", 5805): ("warning", "a computer's secure channel failed (access denied)"),
    ("System", 5807): ("info", "clients connected from IP addresses in no AD site (the netlogon.log lists them)"),
    ("System", 7): ("warning", "the KDC could not find a principal name"),
    ("System", 19): ("warning", "the KDC has no suitable certificate for smart card logon"),
    ("System", 26): ("warning", "the KDC could not agree on an encryption type with a client"),
    ("System", 27): ("warning", "the KDC could not agree on an encryption type with a server"),
    ("System", 29): ("warning", "the KDC has no suitable certificate (smart card / PKINIT logons fail)"),
    ("System", 24): ("warning", "the Windows Time service has no source it can use"),
    ("System", 36): ("warning", "the Windows Time service has not synchronized for a long time"),
    ("System", 47): ("warning", "the time provider got no valid answer from its NTP source"),
    ("System", 129): ("warning", "the time provider could not reach its NTP source"),
    ("System", 134): ("warning", "the time provider could not resolve its NTP source's name"),
    ("System", 1030): ("warning", "Group Policy processing failed on the DC"),
    ("System", 1058): ("warning", "the DC could not read a GPO from SYSVOL (gpt.ini)"),
}

# Security log events the changes tables use for "by whom"
SEC_ADD = {4728: "global", 4732: "domain local", 4756: "universal"}
SEC_REMOVE = {4729: "global", 4733: "domain local", 4757: "universal"}
SEC_NOTABLE = {1102: ("critical", "the Security log was cleared"),
               4794: ("warning", "the Directory Services Restore Mode (DSRM) administrator password was set"),
               4706: ("warning", "a trust to another domain was created"),
               4707: ("warning", "a trust to another domain was removed"),
               4739: ("info", "the domain policy (password / lockout / Kerberos) was changed")}
FSMO_SHORT = {"PDCEmulator": "PDC", "RIDMaster": "RID", "InfrastructureMaster": "Infra", "SchemaMaster": "Schema",
              "DomainNamingMaster": "Naming"}


# ---- small helpers --------------------------------------------------------------------------------
def parse_time(v):
    """'2026-10-15T05:59:31Z' (what the collectors send) -> aware datetime, or None."""
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        t = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def time_text(v):
    t = parse_time(v)
    return t.strftime("%Y-%m-%d %H:%M") if t else ""


def age_hours(v, now):
    t = parse_time(v)
    return None if t is None else (now - t).total_seconds() / 3600.0


def age_text(hours):
    if hours is None:
        return "never"
    if hours < 1:
        return "%d min" % max(0, int(hours * 60))
    if hours < 48:
        return "%.1f h" % hours
    return "%d days" % int(hours / 24)


def level(value, warn, crit, higher_is_worse=True):
    if value is None:
        return "unknown"
    if higher_is_worse:
        return "critical" if value >= crit else ("warning" if value >= warn else "ok")
    return "critical" if value <= crit else ("warning" if value <= warn else "ok")


def worst(statuses):
    s = sorted((x for x in statuses if x), key=lambda x: RANK.get(x, 9))
    return s[0] if s else ""


def short(name):
    """'dc01.corp.example.mil' -> 'dc01'; 'CN=DC01,OU=...' -> 'DC01'."""
    s = str(name or "")
    m = re.match(r"^(?:CN|OU|DC)=((?:\\,|[^,])+)", s, re.I)
    if m:
        return m.group(1).replace("\\,", ",")
    return s.split(".")[0]


# The replication errors seen most, in plain words (repadmin's "Last Failure Status")
REPL_ERRORS = {"5": "access denied", "1256": "the remote system is not available", "1396": "logon failure: the target account name is wrong (SPN / duplicate name)",
               "1722": "the RPC server is unavailable (DC down, firewall, DNS)", "1753": "no more endpoints (RPC port blocked)",
               "1908": "could not find the domain controller", "8453": "replication access was denied",
               "8456": "the source DC is refusing replication (inbound/outbound disabled)", "8457": "this DC is refusing replication (disabled)",
               "8524": "DNS lookup failure: the partner's name or CNAME does not resolve", "8589": "the DC cannot build the partner's SPN",
               "8606": "insufficient attributes: lingering objects", "8614": "too long since the last replication (tombstone lifetime)",
               "-2146893022": "the target principal name is incorrect (Kerberos / machine password)"}


def nc_name(dn):
    """'DC=corp,DC=test' -> 'corp.test'; 'CN=Configuration,DC=...' -> 'Configuration';
    'DC=DomainDnsZones,DC=corp,DC=test' -> 'DomainDnsZones'."""
    s = str(dn or "")
    m = re.match(r"^CN=(Configuration|Schema),", s, re.I)
    if m:
        return m.group(1)
    m = re.match(r"^DC=(DomainDnsZones|ForestDnsZones),", s, re.I)
    if m:
        return m.group(1)
    parts = re.findall(r"DC=([^,]+)", s, re.I)
    return ".".join(parts) if parts and s.upper().startswith("DC=") else s


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hours(h):
    try:
        h = float(h)
    except (TypeError, ValueError):
        return str(h)
    return str(int(h)) if h == int(h) else str(h)


def _row(cells, statuses=None, status=None):
    statuses = list(statuses or [""] * len(cells))
    statuses += [""] * (len(cells) - len(statuses))
    return (["" if x is None else x for x in cells], statuses, status if status is not None else worst(statuses))


class _Report(object):
    def __init__(self, opts):
        self.o = opts
        self.max = int(opts.get("max_rows") or 50)
        self.sections, self.problems = [], []

    def problem(self, domain, area, obj, severity, text):
        if severity in ("critical", "warning"):
            self.problems.append({"domain": domain, "area": area, "object": obj, "severity": severity, "text": text})

    def table(self, title, about, columns, rows, none_text, notes=None, keep_order=False):
        """rows: [(cells, cell_statuses, row_status)]. Problems first (stable), capped at max_rows."""
        if not keep_order:
            rows = sorted(rows, key=lambda r: RANK.get(r[2], 9))
        more = len(rows) - self.max if len(rows) > self.max else 0
        shown = rows[:self.max]
        text = about
        if more:
            text += " The first %d of %d are shown; the job's artifacts (ad_health) have all." % (self.max, len(rows))
        s = {"title": title}
        if shown:
            s.update({"columns": columns, "rows": [r[0] for r in shown],
                      "cell_status": [[x or "" for x in r[1]] for r in shown],
                      "row_status": [r[2] if r[2] in ("critical", "warning", "info", "unknown") else "" for r in shown]})
            s["text"] = text
        else:
            s["text"] = (about + " " + none_text).strip()
        st = worst([r[2] for r in rows if r[2] in ("critical", "warning")])
        if st:
            s["status"] = st
        if notes:
            s["lines"] = notes
        self.sections.append(s)


def _sev(o, key, default):
    v = str(o.get(key) or default).lower()
    return v if v in ("critical", "warning", "info") else default


# ---- the report -------------------------------------------------------------------------------------
def ad_report(dcs, domains, opts=None):
    """The AD health report (roles/site_email layout) and the problems found.
    dcs:     [{host, reachable, error, data: <ad_dc.ps1's dc>}]          one per DC in the inventory
    domains: [{host, error, data: <ad_domain.ps1's domain>}]             one per domain
    -> {'report': {...}, 'problems': [...], 'dcs': [...], 'domains': [...], 'critical': n, 'warning': n}"""
    o = opts or {}
    R = _Report(o)
    now = datetime.datetime.now(datetime.timezone.utc)
    if o.get("now"):
        now = parse_time(o["now"]) or now
    hours = float(o.get("hours", 24))
    dcs = [dict(x) for x in (dcs or [])]
    domains = [dict(x) for x in (domains or []) if x]
    doms = [d for d in domains if (d.get("data") or {}).get("dns")]

    # Which domain each DC belongs to, and every DC AD knows about (also those not in the inventory)
    known = {}                                          # fqdn -> (domain, the domain's record of it)
    for d in doms:
        for x in d["data"].get("dcs") or []:
            known[str(x.get("name", "")).lower()] = (d["data"]["dns"], x)
    read = {}                                           # fqdn -> dc data
    for x in dcs:
        data = x.get("data") or {}
        fq = str(data.get("fqdn") or "").lower()
        x["_fqdn"] = fq or str(x.get("host", "")).lower()
        x["_name"] = short(data.get("name") or x["_fqdn"]).upper() if (data.get("name") or x["_fqdn"]) else str(x.get("host"))
        dom = str(data.get("domain") or "").lower()
        if not dom:
            h = x["_fqdn"]
            for k, (dn, _) in known.items():
                if k == h or short(k) == short(h):
                    dom = dn
        x["_domain"] = dom or "?"
        x["_ok"] = bool(x.get("reachable", True)) and bool(data)
        if x["_ok"]:
            read[x["_fqdn"]] = data
    by_domain = {}
    for x in dcs:
        by_domain.setdefault(x["_domain"], []).append(x)

    # The Security log entries of every DC, for "by whom"
    sec = []
    for x in dcs:
        for e in (x.get("data") or {}).get("security") or []:
            e = dict(e)
            e["_dc"] = x["_name"]
            sec.append(e)

    def who(ids, target=None, member=None, group=None, at=None):
        best = None
        for e in sec:
            if int(e.get("id", 0)) not in ids:
                continue
            if target is not None and str(e.get("target", "")).lower() != str(target).lower():
                continue
            if member is not None and str(e.get("member", "")).lower() != str(member).lower():
                continue
            if group is not None and str(e.get("group", "")).lower() != str(group).lower():
                continue
            if at is not None:
                t, a = parse_time(e.get("time")), parse_time(at)
                if t and a and abs((t - a).total_seconds()) > 900:
                    continue
            best = e
        return best["by"] if best else ""

    rw, rc = float(o.get("repl_warn_hours", 6)), float(o.get("repl_crit_hours", 24))

    # ---- 1. domains ------------------------------------------------------------------------------------
    rows = []
    summaries = []
    for d in domains:
        data = d.get("data") or {}
        if not data.get("dns"):
            err = d.get("error") or "; ".join(data.get("errors") or []) or "no answer"
            rows.append(_row([d.get("domain") or "?", "", "", "", "", "", "", "", "", "", "", "", "NOT READ on %s: %s" % (d.get("host"), err)],
                             [""] * 12 + ["critical"]))
            R.problem(d.get("domain") or "?", "domain", d.get("host"), "critical", "the domain-wide part could not be read: " + err)
            continue
        dn = data["dns"]
        fo = data.get("forest") or {}
        all_dcs = data.get("dcs") or []
        n_read = sum(1 for x in all_dcs if str(x.get("name", "")).lower() in read)
        # tombstone lifetime
        tsl = data.get("tombstone_days")
        tsl_txt, tsl_st = ("%d days" % tsl, "ok") if tsl else ("not set (60 days)", "info")
        # Recycle Bin
        rb = data.get("recycle_bin")
        rb_st = "ok" if rb else ("warning" if rb is False else "")
        if rb is False:
            R.problem(dn, "domain", "Recycle Bin", "warning", "the AD Recycle Bin is not enabled: a deleted object cannot be restored with its attributes")
        # RID pool
        rid = data.get("rid") or {}
        total, issued = _num(rid.get("total")), _num(rid.get("issued"))
        rid_txt, rid_st = "?", ""
        if total:
            left = 100.0 * (total - (issued or 0)) / total
            rid_txt = "%.1f%% (%d issued)" % (left, issued or 0)
            rid_st = level(left, float(o.get("rid_warn_pct", 10)), float(o.get("rid_crit_pct", 2)), higher_is_worse=False)
            R.problem(dn, "domain", "RID pool", rid_st, "%.1f%% of the domain's RID pool is left: new accounts fail when it runs out" % left)
        # backups
        bw, bc = float(o.get("backup_warn_days", 7)), float(o.get("backup_crit_days", 30))
        b_ages = [(b.get("partition"), age_hours(b.get("last"), now)) for b in data.get("backups") or []]
        b_ages = [(p, a) for p, a in b_ages if a is not None]
        if b_ages:
            oldest = max(b_ages, key=lambda x: x[1])
            days = oldest[1] / 24.0
            b_st = level(days, bw, bc)
            b_txt = "%s ago (%s)" % (age_text(oldest[1]), nc_name(oldest[0]))
            if tsl and days >= tsl:
                b_st = "critical"
            R.problem(dn, "backup", oldest[0], b_st, "the last system-state backup of %s was %s ago%s" % (
                oldest[0], age_text(oldest[1]), " - older than the tombstone lifetime: it cannot be restored" if tsl and days >= tsl else ""))
        else:
            b_txt, b_st = "?", "unknown"
        # krbtgt
        kmax = float(o.get("krbtgt_max_days", 180))
        k_age = [age_hours(k.get("pwd_last_set"), now) for k in data.get("krbtgt") or [] if str(k.get("name", "")).lower() == "krbtgt"]
        k_age = k_age[0] if k_age else None
        k_st = "" if k_age is None else ("warning" if k_age / 24.0 > kmax else "ok")
        if k_st == "warning":
            R.problem(dn, "domain", "krbtgt", "warning", "the krbtgt password was last changed %s ago (more than %s days)" % (age_text(k_age), _hours(kmax)))
        # SYSVOL migration
        mig = data.get("sysvol_migration") or ""
        mig_st = "" if not mig else ("ok" if mig.lower() == "eliminated" else "warning")
        if mig_st == "warning":
            R.problem(dn, "sysvol", "dfsrmig", "warning", "SYSVOL migration from FRS to DFSR is at '%s', not 'Eliminated': FRS is removed from Windows Server 2019 and later" % mig)
        dc_st = "ok" if n_read == len(all_dcs) else "info"
        rows.append(_row([dn, fo.get("name", ""), data.get("mode", ""), fo.get("mode", ""), data.get("schema", {}).get("version", ""),
                          "%d of %d" % (n_read, len(all_dcs)), short(next((f.get("holder") for f in data.get("fsmo") or [] if f.get("role") == "PDC emulator"), "")),
                          tsl_txt, "on" if rb else ("OFF" if rb is False else "?"), rid_txt, b_txt,
                          age_text(k_age) if k_age is not None else "?", mig or "-"],
                         ["", "", "", "", "", dc_st, "", tsl_st, rb_st, rid_st, b_st, k_st, mig_st]))
        summaries.append({"domain": dn, "forest": fo.get("name", ""), "dcs": len(all_dcs), "dcs_read": n_read, "collector": d.get("host")})
    R.table("Domains", "Each domain: functional levels, schema version, its DCs (checked here / in AD), the PDC emulator, the "
            "tombstone lifetime, the Recycle Bin, how much of the RID pool is left, the oldest partition's last backup, the age "
            "of the krbtgt password and the SYSVOL migration state.",
            ["Domain", "Forest", "Domain level", "Forest level", "Schema", "DCs checked", "PDC", "Tombstone", "Recycle Bin",
             "RID pool left", "Oldest backup", "krbtgt password", "SYSVOL migration"], rows, "", keep_order=True)

    # ---- 2. domain controllers ----------------------------------------------------------------------------
    rows = []
    dw, dcrit = float(o.get("disk_warn_pct", 15)), float(o.get("disk_crit_pct", 5))
    tw, tc = float(o.get("time_warn_seconds", 5)), float(o.get("time_crit_seconds", 60))
    up_warn = float(o.get("uptime_warn_days", 0) or 0)
    for x in dcs:
        dn = x["_domain"]
        if not x["_ok"]:
            err = x.get("error") or "no answer"
            rows.append(_row([x["_name"], dn, "", "", "", "", "UNREACHABLE", "", "", "", "", "", err], ["", "", "", "", "", "", "critical"]))
            R.problem(dn, "dc", x["_name"], "critical", "the DC could not be read: " + err)
            continue
        d = x["data"]
        name = x["_name"]
        # LDAP
        ld = d.get("ldap") or {}
        if d.get("ad_module") is False:
            l_txt, l_st = "not checked", "warning"
            R.problem(dn, "dc", name, "warning", "the ActiveDirectory PowerShell module is missing on the DC (RSAT-AD-PowerShell): LDAP and the domain-wide part are not checked")
        elif not ld.get("ok"):
            l_txt, l_st = "NO ANSWER", "critical"
            R.problem(dn, "dc", name, "critical", "LDAP does not answer on the DC: " + str(ld.get("error", "")))
        elif ld.get("synchronized") is False:
            l_txt, l_st = "not synchronized", "critical"
            R.problem(dn, "dc", name, "critical", "the DC has not finished its initial replication (isSynchronized false): it does not serve logons")
        elif d.get("is_gc") and (d.get("gc_port") or {}).get("ok") is False:
            l_txt, l_st = "GC port 3268 down", "critical"
            R.problem(dn, "dc", name, "critical", "the global catalog port 3268 does not answer: " + str(d["gc_port"].get("error", "")))
        elif d.get("is_gc") and ld.get("gc_ready") is False:
            l_txt, l_st = "GC not ready", "warning"
            R.problem(dn, "dc", name, "warning", "the DC is a global catalog but not ready as one yet (isGlobalCatalogReady false)")
        else:
            l_txt, l_st = "OK", "ok"
        # services
        svc = {str(s.get("name", "")).lower(): s for s in d.get("services") or []}
        down = []
        s_st = "ok"
        engine = (d.get("sysvol") or {}).get("engine", "")
        need = dict(SERVICES)
        if engine == "dfsr":
            need["dfsr"] = ("DFS Replication (SYSVOL)", "critical")
        elif engine == "frs":
            need["ntfrs"] = ("File Replication (SYSVOL)", "critical")
        for k, (label, sev) in need.items():
            s = svc.get(k)
            if s is None:
                if k in ("ntds", "netlogon", "kdc"):
                    down.append(label + " missing")
                    s_st = worst([s_st, "critical"])
                    R.problem(dn, "service", name, "critical", "service %s is not installed" % label)
                continue
            if str(s.get("state", "")).lower() != "running":
                down.append("%s (%s)" % (s.get("name"), s.get("state")))
                s_st = worst([s_st, sev])
                R.problem(dn, "service", name, sev, "service %s (%s) is %s" % (s.get("name"), label, s.get("state")))
        # dcdiag
        dd = d.get("dcdiag") or {}
        failed = [t for t in dd.get("tests") or [] if t.get("result") == "failed"]
        if dd.get("error") and not dd.get("tests"):
            g_txt, g_st = "not run", "warning"
            R.problem(dn, "dcdiag", name, "warning", "dcdiag: " + str(dd.get("error")))
        elif failed:
            g_txt = ", ".join(sorted(set(t.get("test", "") for t in failed)))
            g_st = "critical" if any(str(t.get("test", "")).lower() in DCDIAG_CRITICAL for t in failed) else "warning"
        else:
            g_txt, g_st = ("%d passed" % len(dd.get("tests") or [])) if dd.get("tests") else "?", "ok" if dd.get("tests") else ""
        # SYSVOL
        sv = d.get("sysvol") or {}
        shares = set(str(s).upper() for s in sv.get("shares") or [])
        sv_bad = []
        if "SYSVOL" not in shares or "NETLOGON" not in shares:
            sv_bad.append("share missing: " + ", ".join(s for s in ("SYSVOL", "NETLOGON") if s not in shares))
        if sv.get("ready") == 0:
            sv_bad.append("SysvolReady 0")
        st_txt, st_sev = "", "ok"
        if sv.get("dfsr_state") is not None:
            st_txt, st_sev = DFSR_STATE.get(int(sv["dfsr_state"]), ("state %s" % sv["dfsr_state"], "warning"))
        if engine == "frs":
            st_txt, st_sev = "FRS", "warning"
            R.problem(dn, "sysvol", name, "warning", "SYSVOL replicates with FRS: migrate to DFSR (dfsrmig) - FRS is removed from Windows Server 2019 and later")
        sv_st = worst(["critical" if sv_bad else "", st_sev if st_sev != "ok" else ""]) or "ok"
        if sv_bad:
            R.problem(dn, "sysvol", name, "critical", "SYSVOL is not shared: " + "; ".join(sv_bad))
        if engine == "dfsr" and st_sev in ("critical", "warning"):
            R.problem(dn, "sysvol", name, st_sev, "the SYSVOL replicated folder is '%s' in DFSR" % st_txt)
        sv_txt = "; ".join(sv_bad + ([st_txt] if st_txt else [])) or ("OK" if shares else "?")
        # time
        tm = d.get("time") or {}
        off = _num(tm.get("offset"))
        t_txt, t_st = "?", ""
        src = str(tm.get("source", ""))
        if d.get("role") == "pdc" and re.search(r"Local CMOS Clock|Free-running System Clock", src, re.I):
            t_txt, t_st = "PDC on " + src, "critical"
            R.problem(dn, "time", name, "critical", "the PDC emulator - the time source of the whole domain - runs on its own clock (%s): give it an NTP source" % src)
        elif off is not None:
            t_st = level(abs(off), tw, tc)
            t_txt = "%+.2f s vs %s" % (off, short(tm.get("ref")))
            R.problem(dn, "time", name, t_st, "the clock is %+.1f s off %s (Kerberos fails beyond 5 minutes)" % (off, tm.get("ref")))
        elif tm.get("error"):
            t_txt, t_st = "not read", "warning"
            R.problem(dn, "time", name, "warning", "Windows Time: " + str(tm.get("error")))
        # disks
        lows = []
        k_st = ""
        for dk in d.get("disks") or []:
            p = _num(dk.get("free_pct"))
            st = level(p, dw, dcrit, higher_is_worse=False)
            k_st = worst([k_st, st]) or st
            lows.append("%s %s %s%%" % (dk.get("what"), dk.get("drive"), _hours(p)))
            R.problem(dn, "disk", name, st, "the %s volume %s has %s%% (%s GB) free" % (dk.get("what"), dk.get("drive"), _hours(p), dk.get("free_gb")))
        free_txt = min(((_num(dk.get("free_pct")) or 0, "%s%% (%s)" % (_hours(dk.get("free_pct")), dk.get("drive")))
                        for dk in d.get("disks") or []), default=(0, "?"))[1]
        # uptime
        up_h = age_hours(d.get("boot"), now)
        u_st = "warning" if up_warn and up_h is not None and up_h / 24.0 > up_warn else ""
        kind = ("GC" if d.get("is_gc") else "") + (" RODC" if d.get("is_rodc") else "")
        roles = ", ".join(FSMO_SHORT.get(r, r) for r in d.get("fsmo") or [])
        rows.append(_row([name, dn, d.get("site", ""), d.get("os", "").replace("Microsoft Windows Server", "Server").strip(),
                          kind.strip() or "-", roles or "-", l_txt, ", ".join(down) or "all running", g_txt, sv_txt, t_txt,
                          free_txt, age_text(up_h)],
                         ["", "", "", "", "", "", l_st, s_st, g_st, sv_st, t_st, k_st, u_st]))
    # DCs AD knows but the inventory has not
    seen = set(x["_fqdn"] for x in dcs) | set(short(x["_fqdn"]).lower() for x in dcs)
    for fq, (dn, rec) in sorted(known.items()):
        if fq in seen or short(fq).lower() in seen:
            continue
        rows.append(_row([short(fq).upper(), dn, rec.get("site", ""), str(rec.get("os", "")).replace("Windows Server", "Server"),
                          ("GC" if rec.get("gc") else "") + (" RODC" if rec.get("rodc") else ""), ", ".join(FSMO_SHORT.get(r, r) for r in rec.get("roles") or []),
                          "not in the inventory", "", "", "", "", "", ""], [""] * 6 + ["info"], "info"))
    R.table("Domain controllers", "Each DC: site, operating system, global catalog / read-only, the operations master roles it "
            "holds, LDAP answering (and synchronized), the DC services not running, dcdiag's failed tests, SYSVOL (shared, "
            "DFSR state), its clock against the PDC emulator (the PDC: against its NTP source), the least free space of the "
            "database, log and SYSVOL volumes, and the time since it started. Blue: a DC in AD that is not in this job's "
            "inventory (only the domain-wide checks cover it).",
            ["DC", "Domain", "Site", "OS", "Kind", "Roles", "LDAP", "Services", "dcdiag failed", "SYSVOL", "Clock", "Least free",
             "Up"], rows, "No domain controller was read.")

    # ---- 3. FSMO ---------------------------------------------------------------------------------------------
    rows = []
    for d in doms:
        data = d["data"]
        dn = data["dns"]
        names = set(str(x.get("name", "")).lower() for x in data.get("dcs") or [])
        for f in data.get("fsmo") or []:
            h = str(f.get("holder", "")).lower()
            is_dc = h in names or any(short(n) == short(h) for n in names)
            reach = f.get("reachable")
            st_dc = "ok" if is_dc else "critical"
            st_r = "ok" if reach else ("critical" if reach is False else "unknown")
            rows.append(_row([dn, f.get("role"), h or "?", "yes" if is_dc else "NO", "yes" if reach else ("NO" if reach is False else "?")],
                             ["", "", "", st_dc, st_r]))
            if not is_dc:
                R.problem(dn, "fsmo", f.get("role"), "critical", "the %s role is held by %s, which is not a DC of the domain (seize it to a live DC)" % (f.get("role"), h or "nobody"))
            elif reach is False:
                R.problem(dn, "fsmo", f.get("role"), "critical", "the %s (%s) does not answer on LDAP port 389" % (f.get("role"), h))
    R.table("Operations masters (FSMO roles)", "Who holds each role, whether that holder is a DC that still exists, and whether it "
            "answers on LDAP (TCP 389) from the DC that read the domain.",
            ["Domain", "Role", "Holder", "A DC in AD", "Answers"], rows, "No role holder was read.", keep_order=True)

    # ---- 4. replication -----------------------------------------------------------------------------------------
    rows = []
    ok_links = 0
    for x in dcs:
        if not x["_ok"]:
            continue
        tsl = next((dd["data"].get("tombstone_days") for dd in doms if dd["data"]["dns"] == x["_domain"]), None) or 60
        for r in x["data"].get("repl") or []:
            fails = int(r.get("failures") or 0)
            ls_h = age_hours(r.get("last_success"), now)
            st = "ok"
            why = ""
            if ls_h is not None and ls_h / 24.0 >= tsl:
                st, why = "critical", "no success for longer than the tombstone lifetime (%d days): this DC must not replicate with it again until lingering objects are removed" % tsl
            elif fails:
                st = "critical" if (ls_h is None or ls_h >= rc) else "warning"
                why = "%d consecutive failure(s): %s" % (fails, r.get("status") or "?")
            elif ls_h is not None and ls_h >= rw:
                st = level(ls_h, rw, rc)
                why = "last success %s ago" % age_text(ls_h)
            if st == "ok":
                ok_links += 1
                continue
            code = str(r.get("status") or "").strip()
            err = ("%s (%s)" % (code, REPL_ERRORS[code]) if code in REPL_ERRORS else code) if fails else ""
            if fails:
                why = "%d consecutive failure(s), last success %s: error %s" % (fails, age_text(ls_h) + " ago" if ls_h is not None else "never", err or "?")
            rows.append(_row([x["_name"], nc_name(r.get("nc")), r.get("source", ""), r.get("source_site", ""), fails,
                              age_text(ls_h) if ls_h is not None else "never", time_text(r.get("last_failure")), err],
                             ["", "", "", "", "critical" if fails else "", st]))
            R.problem(x["_domain"], "replication", "%s <- %s (%s)" % (x["_name"], r.get("source"), nc_name(r.get("nc"))), st, why)
        q = x["data"].get("queue")
        qw = int(o.get("repl_queue_warn", 50))
        if q is not None and int(q) >= qw:
            rows.append(_row([x["_name"], "(inbound queue)", "", "", "", "", "", "%s requests waiting" % q], [""] * 7 + ["warning"]))
            R.problem(x["_domain"], "replication", x["_name"], "warning", "%s replication requests wait in the inbound queue" % q)
    notes = ["%s: repadmin could not read it - %s" % (x["_name"], e) for x in dcs if x["_ok"]
             for e in x["data"].get("errors") or [] if e.startswith("repadmin /showrepl")]
    R.table("Inbound replication", "Each DC's inbound replication from each partner, for every partition (repadmin /showrepl), "
            "with consecutive failures, the last success and the last error. Only links with a problem are listed: amber after "
            "%s h without success (or a failing link that succeeded recently), red after %s h or longer than the tombstone "
            "lifetime. %d link(s) are healthy." % (_hours(rw), _hours(rc), ok_links),
            ["DC", "Partition", "From", "From site", "Failures", "Last success", "Last failure", "Last error"],
            rows, "No link has a problem.", notes)

    # ---- 5. up-to-dateness: has every DC's latest change reached the collector? --------------------------------------
    rows = []
    for d in doms:
        data = d["data"]
        names = {short(x.get("name")).lower(): x for x in data.get("dcs") or []}
        coll = short(data.get("collector") or d.get("host")).lower()
        for u in data.get("utdv") or []:
            dsa = str(u.get("dsa", ""))
            n = dsa.split("\\")[-1].strip().lower()
            if n not in names or n == coll or "deleted dsa" in dsa.lower():
                continue                                # retired invocation IDs, deleted DCs, the collector itself
            h = age_hours(u.get("last"), now)
            st = level(h, rw, rc) if h is not None else "unknown"
            rows.append(_row([data["dns"], n.upper(), names[n].get("site", ""), short(coll).upper(), age_text(h)], ["", "", "", "", st]))
            R.problem(data["dns"], "replication", n.upper(), st, "the last replication from %s that reached %s was %s ago" % (n.upper(), coll.upper(), age_text(h)))
    R.table("Replication from every DC (up-to-dateness)", "For each DC of the domain, how long ago the latest of its changes "
            "reached the DC that read the domain (repadmin /showutdvec /latency). This covers the DCs that are not in the "
            "inventory too: a DC that stopped replicating shows up here.",
            ["Domain", "DC", "Site", "Seen from", "Last replication"], rows, "No up-to-dateness vector was read.")

    # ---- 6. dcdiag ---------------------------------------------------------------------------------------------------
    rows = []
    for x in dcs:
        if not x["_ok"]:
            continue
        for t in (x["data"].get("dcdiag") or {}).get("tests") or []:
            if t.get("result") != "failed":
                continue
            sv = "critical" if str(t.get("test", "")).lower() in DCDIAG_CRITICAL else "warning"
            det = " / ".join(t.get("detail") or [])[:700]
            rows.append(_row([x["_name"], t.get("target", ""), t.get("test", ""), "FAILED", det], ["", "", "", sv]))
            R.problem(x["_domain"], "dcdiag", x["_name"], sv, "dcdiag test %s failed: %s" % (t.get("test"), det[:300]))
    R.table("dcdiag", "The tests of dcdiag that failed on each DC, with the lines dcdiag printed about it. Red: Connectivity, "
            "Advertising, NetLogons, Replications, SysVolCheck, Services, KnowsOfRoleHolders, RidManager, MachineAccount. "
            "Skipped: %s." % (", ".join(o.get("dcdiag_skip") or []) or "none"),
            ["DC", "Target", "Test", "Result", "What dcdiag said"], rows, "Every test passed.",
            ["%s: %s" % (x["_name"], (x["data"].get("dcdiag") or {}).get("error")) for x in dcs
             if x["_ok"] and (x["data"].get("dcdiag") or {}).get("error")])

    # ---- 7. DNS --------------------------------------------------------------------------------------------------------
    rows = []
    for x in dcs:
        if not x["_ok"]:
            continue
        dd = x["data"].get("dcdiag_dns") or {}
        for t in dd.get("tests") or []:
            if t.get("result") == "failed":
                det = " / ".join(dd.get("problems") or t.get("detail") or [])[:700]
                rows.append(_row([x["_name"], "dcdiag /test:DNS", "FAILED", det], ["", "", "warning"]))
                R.problem(x["_domain"], "dns", x["_name"], "warning", "dcdiag DNS test failed: " + det[:300])
        if dd.get("error"):
            rows.append(_row([x["_name"], "dcdiag /test:DNS", "not run", dd["error"]], ["", "", "unknown"]))
    for d in doms:
        data = d["data"]
        all_dcs = data.get("dcs") or []
        for s in data.get("dns_srv") or []:
            rec = s.get("record", "")
            targets = set(str(t).lower() for t in s.get("targets") or [])
            if s.get("error"):
                rows.append(_row([data["dns"], rec, "NOT FOUND", s["error"]], ["", "", "critical"]))
                R.problem(data["dns"], "dns", rec, "critical", "the SRV record %s could not be resolved: %s" % (rec, s["error"]))
                continue
            want = [x for x in all_dcs if not x.get("rodc")] if "._msdcs." + data["dns"] in rec else \
                [x for x in all_dcs if x.get("gc")] if "_gc." in rec else []
            missing = sorted(short(x.get("name")).upper() for x in want if str(x.get("name", "")).lower() not in targets)
            if missing:
                rows.append(_row([data["dns"], rec, "MISSING", "no record for " + ", ".join(missing)], ["", "", "warning"]))
                R.problem(data["dns"], "dns", rec, "warning", "%s has no record for %s (restart Netlogon on them, or check DNS registration)" % (rec, ", ".join(missing)))
    R.table("DNS", "dcdiag's DNS tests on each DC (basic, forwarders, delegation, record registration - the tests that only read), "
            "and the SRV records clients use to find a DC: every DC must be registered.",
            ["DC / domain", "Test / record", "Result", "Details"], rows, "Every DNS test passed and every DC is registered.")

    # ---- 8. time -----------------------------------------------------------------------------------------------------------
    rows = []
    sync_h = float(o.get("time_sync_warn_hours", 24))
    for x in dcs:
        if not x["_ok"]:
            continue
        tm = x["data"].get("time") or {}
        off = _num(tm.get("offset"))
        ls = age_hours(tm.get("last_sync"), now)
        src = str(tm.get("source", ""))
        is_pdc = x["data"].get("role") == "pdc"
        s_st = "critical" if is_pdc and re.search(r"Local CMOS Clock|Free-running System Clock", src, re.I) else ""
        l_st = ("warning" if ls is None or ls > sync_h else "ok") if src else ""
        if l_st == "warning" and not s_st:
            R.problem(x["_domain"], "time", x["_name"], "warning", "Windows Time last synchronized %s ago" % age_text(ls))
        o_st = level(abs(off), tw, tc) if off is not None else ""
        rows.append(_row([x["_name"], "PDC emulator" if is_pdc else "", src or "?", tm.get("stratum", ""), age_text(ls) if src else "?",
                          tm.get("ref", "") or "-", ("%+.3f s" % off) if off is not None else (tm.get("offset_error") or "-")],
                         ["", "", s_st, "", l_st, "", o_st]))
    R.table("Time", "Each DC's time source, stratum, last synchronization and offset: a DC against the PDC emulator, the PDC "
            "emulator against its NTP source. Amber %s s, red %s s off (Kerberos fails at 5 minutes); red: the PDC on its own "
            "clock." % (_hours(tw), _hours(tc)),
            ["DC", "Role", "Source", "Stratum", "Last sync", "Measured against", "Offset"], rows, "No DC was read.", keep_order=True)

    # ---- 9. events ------------------------------------------------------------------------------------------------------------
    rows = []
    ev_h = float(o.get("event_hours", hours))
    for x in dcs:
        if not x["_ok"]:
            continue
        for e in x["data"].get("events") or []:
            k = (e.get("log"), int(e.get("id", 0)))
            sv, what = EVENTS.get(k, ("info" if int(e.get("level", 2)) > 1 else "warning", ""))
            if k not in EVENTS and int(e.get("level", 2)) == 1:
                sv = "warning"
            rows.append(_row([x["_name"], e.get("log", ""), e.get("source", ""), e.get("id", ""), sv.upper(), e.get("count", ""),
                              time_text(e.get("last")), what or str(e.get("message", ""))[:300]], ["", "", "", "", sv]))
            R.problem(x["_domain"], "event", x["_name"], sv, "%s %s x%s: %s" % (e.get("log"), e.get("id"), e.get("count"), what or str(e.get("message", ""))[:200]))
    R.table("Events (last %s h)" % _hours(ev_h), "The Directory Service, DFS Replication and DNS Server logs, and the Netlogon, KDC, "
            "time and Group Policy entries of the System log: the events that matter on a DC (with what they mean) and every "
            "other error (blue, for information). The same event on the same DC is counted once.",
            ["DC", "Log", "Source", "ID", "Severity", "Count", "Last", "What it means / last message"], rows, "No such event.")

    # ---- 10. clients in no site ---------------------------------------------------------------------------------------------
    rows = []
    ncs = int(o.get("no_client_site_warn", 1))
    for x in dcs:
        if not x["_ok"]:
            continue
        n = x["data"].get("no_client_site") or {}
        cnt = int(n.get("distinct") or len(n.get("clients") or []))
        if not cnt:
            continue
        st = "warning" if cnt >= ncs else "info"
        rows.append(_row([x["_name"], cnt, n.get("count", ""), ", ".join(n.get("clients") or [])[:600]], ["", st]))
        R.problem(x["_domain"], "sites", x["_name"], st, "%d client address(es) are in no AD subnet: they may log on at a DC in another site" % cnt)
    for d in doms:
        nos = d["data"].get("subnets_without_site") or []
        if nos:
            rows.append(_row(["(%s)" % d["data"]["dns"], len(nos), "", "subnets with no site: " + ", ".join(nos)[:600]], ["", "warning"]))
            R.problem(d["data"]["dns"], "sites", "subnets", "warning", "%d subnet(s) are not linked to a site" % len(nos))
    R.table("Clients in no site", "Client addresses that are in no AD subnet (netlogon.log NO_CLIENT_SITE): they are sent to any "
            "DC, often across the WAN. Add their subnets to Active Directory Sites and Services.",
            ["DC", "Addresses", "Logons", "Addresses (computer)"], rows, "Every client was in a known subnet.")

    # ---- 11. certificates --------------------------------------------------------------------------------------------------
    rows = []
    cw, cc = float(o.get("cert_warn_days", 30)), float(o.get("cert_crit_days", 7))
    for x in dcs:
        if not x["_ok"]:
            continue
        certs = x["data"].get("certs") or []
        for p in ("ldaps", "kdc"):
            mine = [c for c in certs if p in (c.get("purposes") or [])]
            if not mine:
                rows.append(_row([x["_name"], p.upper(), "none", "", ""], ["", "", "info"], "info"))
                continue
            c = max(mine, key=lambda c: parse_time(c.get("not_after")) or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc))
            t = parse_time(c.get("not_after"))
            days = (t - now).total_seconds() / 86400.0 if t else None
            st = level(days, cw, cc, higher_is_worse=False)
            rows.append(_row([x["_name"], p.upper(), c.get("subject", ""), time_text(c.get("not_after")),
                              "EXPIRED" if days is not None and days < 0 else (int(days) if days is not None else "?")], ["", "", "", "", st]))
            R.problem(x["_domain"], "certificate", x["_name"], st, "the %s certificate %s %s" % (
                p.upper(), c.get("subject", ""), "has EXPIRED" if days is not None and days < 0 else "expires in %d day(s)" % int(days or 0)))
    R.table("Certificates (LDAPS and Kerberos)", "The newest certificate each DC has for LDAPS (server authentication) and for "
            "the KDC (smart card / PKINIT logons). Amber %s days, red %s days before it expires; blue: none (that service "
            "is not available on the DC)." % (_hours(cw), _hours(cc)),
            ["DC", "For", "Subject", "Expires", "Days left"], rows, "No DC was read.")

    # ---- 12. trusts -----------------------------------------------------------------------------------------------------------
    rows = []
    for d in doms:
        for t in d["data"].get("trusts") or []:
            v = str(t.get("verify") or "")
            v_st = "" if not v else ("ok" if re.search(r"NERR_Success|Status = 0 0x0", v) else "critical")
            if v_st == "critical":
                R.problem(d["data"]["dns"], "trust", t.get("name"), "critical", "the trust with %s does not verify: %s" % (t.get("name"), v))
            rows.append(_row([d["data"]["dns"], t.get("name", ""), t.get("direction", ""), t.get("type", ""), "yes" if t.get("forest") else "no",
                              "yes" if t.get("selective") else "no", "on" if t.get("quarantined") else "-",
                              "OK" if v_st == "ok" else (v or "not verified (inbound only, or trust_verify off)"), time_text(t.get("changed"))],
                             ["", "", "", "", "", "", "", v_st]))
    R.table("Trusts", "Every trust of each domain; outbound and two-way trusts are verified with nltest /sc_verify (red when the "
            "secure channel to the other domain fails).",
            ["Domain", "Trust", "Direction", "Type", "Forest trust", "Selective auth", "SID quarantine", "Verified", "Changed"],
            rows, "No trust.")

    # ---- 13. sites -----------------------------------------------------------------------------------------------------------
    rows = []
    for d in doms:
        for s in d["data"].get("sites") or []:
            st = "info" if not s.get("subnets") or not s.get("dcs") else ""
            if st or len(d["data"].get("sites") or []) <= int(o.get("sites_list_max", 25)):
                rows.append(_row([d["data"]["dns"], s.get("name", ""), s.get("dcs", 0), s.get("subnets", 0)],
                                 ["", "", "info" if not s.get("dcs") else "", "info" if not s.get("subnets") else ""], st))
    R.table("Sites", "AD sites, the DCs of this domain in each and their subnets (blue: no DC of this domain, or no subnet - "
            "clients are never placed in a site without one).",
            ["Domain", "Site", "DCs", "Subnets"], rows, "No site was read.")

    # ---- changes ------------------------------------------------------------------------------------------------------------------
    priv_sev = _sev(o, "privileged_change_severity", "warning")
    # 14. privileged group changes
    rows = []
    for d in doms:
        for g in d["data"].get("groups") or []:
            for c in g.get("changes") or []:
                add = c.get("action") == "added"
                by = who(SEC_ADD if add else SEC_REMOVE, member=c.get("member"), group=g.get("sam"), at=c.get("time"))
                rows.append(_row([time_text(c.get("time")), d["data"]["dns"], g.get("name"), c.get("action", "").upper(), short(c.get("member")),
                                  c.get("dc", ""), by or "-"], ["", "", "", priv_sev], priv_sev))
                R.problem(d["data"]["dns"], "privileged", g.get("name"), priv_sev, "%s %s %s%s" % (
                    short(c.get("member")), c.get("action"), "to" if add else "from", " " + g.get("name") + (" by " + by if by else "")))
    rows.sort(key=lambda r: r[0][0], reverse=True)
    R.table("Privileged group changes (last %s h)" % _hours(hours), "Members added to or removed from the privileged groups, "
            "with the DC the change was made on (from AD's replication metadata: no auditing needed) and who made it (from that "
            "DC's Security log, when auditing of security group management is on).",
            ["When", "Domain", "Group", "Change", "Member", "Made on", "By"], rows, "No change.", keep_order=True)
    # 15. privileged groups now
    rows = []
    empty = set(str(x).lower() for x in o.get("groups_should_be_empty") or [])
    for d in doms:
        for g in d["data"].get("groups") or []:
            n = int(g.get("members") or 0)
            st = "warning" if n and str(g.get("name", "")).lower() in empty else ""
            if st:
                R.problem(d["data"]["dns"], "privileged", g.get("name"), "warning", "%s has %d member(s) and should be empty" % (g.get("name"), n))
            rows.append(_row([d["data"]["dns"], g.get("name", ""), n, ", ".join(g.get("member_names") or []) + (" ..." if n > len(g.get("member_names") or []) else "")],
                             ["", "", st]))
    R.table("Privileged groups", "The direct members of each privileged group now (nested groups are not expanded).%s" % (
        (" Amber: a group that should be empty (%s) has members." % ", ".join(sorted(o.get("groups_should_be_empty") or []))) if empty else ""),
        ["Domain", "Group", "Members", "Direct members"], rows, "No group was read.", keep_order=True)
    # 16. accounts created / deleted
    rows = []
    for d in doms:
        for c in d["data"].get("created") or []:
            ids = {4741} if c.get("class") == "computer" else ({4720} if c.get("class") == "user" else set())
            by = who(ids, target=c.get("name")) if ids else ""
            rows.append(_row([time_text(c.get("time")), d["data"]["dns"], c.get("class", ""), c.get("name", ""), "CREATED",
                              re.sub(r"^CN=[^,]+,", "", c.get("dn", "")), by or "-"], ["", "", "", "", "info"], "info"))
        for c in d["data"].get("deleted") or []:
            ids = {4743} if c.get("class") == "computer" else ({4726} if c.get("class") == "user" else set())
            by = who(ids, target=c.get("name")) if ids else ""
            rows.append(_row([time_text(c.get("time")), d["data"]["dns"], c.get("class", ""), c.get("name", ""), "DELETED",
                              c.get("parent", ""), by or "-"], ["", "", "", "", "info"], "info"))
    rows.sort(key=lambda r: r[0][0], reverse=True)
    R.table("Users, groups and computers created or deleted (last %s h)" % _hours(hours),
            "New and deleted accounts, where they are (for a deleted one: where it was), and who (from the Security log, when "
            "audited).", ["When", "Domain", "Type", "Name", "Change", "Where", "By"], rows, "None.", keep_order=True)
    # 17. enabled / disabled / unlocked
    rows = []
    for d in doms:
        for c in d["data"].get("account_changes") or []:
            ids = {"enabled": {4722}, "disabled": {4725}, "unlocked": {4767}}.get(c.get("change"), set())
            by = who(ids, target=c.get("name")) if ids else ""
            rows.append(_row([time_text(c.get("time")), d["data"]["dns"], c.get("class", ""), c.get("name", ""), str(c.get("change", "")).upper(),
                              c.get("dc", ""), by or "-"], ["", "", "", "", "info"], "info"))
    rows.sort(key=lambda r: r[0][0], reverse=True)
    lock = ["%s: locked out now: %s" % (d["data"]["dns"], ", ".join(d["data"].get("locked_out") or [])) for d in doms if d["data"].get("locked_out")]
    lock += ["%s: only the first %s changed accounts were looked at (changes_max)" % (d["data"]["dns"], o.get("changes_max", 500))
             for d in doms if d["data"].get("changes_capped")]
    R.table("Accounts enabled, disabled, locked or unlocked (last %s h)" % _hours(hours),
            "From the replication metadata of userAccountControl and lockoutTime: the DC it happened on, and who (Security log).",
            ["When", "Domain", "Type", "Name", "Change", "Made on", "By"], rows, "None.", lock, keep_order=True)
    # 18. Group Policy
    rows = []
    for d in doms:
        data = d["data"]
        gname = {g.get("guid"): g.get("name") for g in data.get("gpos") or []}
        for g in data.get("gpos") or []:
            cr, ch = age_hours(g.get("created"), now), age_hours(g.get("changed"), now)
            if cr is not None and cr <= hours:
                rows.append(_row([time_text(g.get("created")), data["dns"], g.get("name", ""), "CREATED", g.get("ad_version", "")], ["", "", "", "info"], "info"))
            elif ch is not None and ch <= hours:
                rows.append(_row([time_text(g.get("changed")), data["dns"], g.get("name", ""), "CHANGED", g.get("ad_version", "")], ["", "", "", "info"], "info"))
        for g in data.get("gpo_deleted") or []:
            rows.append(_row([time_text(g.get("time")), data["dns"], g.get("name") or g.get("guid", ""), "DELETED", ""], ["", "", "", "info"], "info"))
        for lk in data.get("gpo_links") or []:
            rows.append(_row([time_text(lk.get("time")), data["dns"], "links on " + short(lk.get("target")),
                              "LINKS CHANGED", "now: " + (", ".join(gname.get(x, x) for x in lk.get("links") or []) or "none") + " (on %s)" % lk.get("dc", "")],
                             ["", "", "", "info"], "info"))
    rows.sort(key=lambda r: r[0][0], reverse=True)
    n_gpo = len(rows)
    R.table("Group Policy changes (last %s h)" % _hours(hours), "GPOs created, changed or deleted, and OUs, domains or sites whose "
            "GPO links changed (with the GPOs linked there now).",
            ["When", "Domain", "GPO / where", "Change", "Version / links"], rows, "None.", keep_order=True)
    # 19. GPO consistency (AD against SYSVOL)
    rows = []
    for d in doms:
        data = d["data"]
        if not data.get("sysvol_read"):
            continue
        for g in data.get("gpos") or []:
            if not g.get("folder"):
                rows.append(_row([data["dns"], g.get("name", ""), g.get("guid", ""), "no folder in SYSVOL"], ["", "", "", "warning"]))
                R.problem(data["dns"], "gpo", g.get("name"), "warning", "GPO %s has no folder in SYSVOL" % g.get("name"))
            elif g.get("sysvol_version") is not None and int(g.get("sysvol_version")) != int(g.get("ad_version") or 0):
                rows.append(_row([data["dns"], g.get("name", ""), g.get("guid", ""), "version AD %s, SYSVOL %s" % (g.get("ad_version"), g.get("sysvol_version"))],
                                 ["", "", "", "warning"]))
                R.problem(data["dns"], "gpo", g.get("name"), "warning", "GPO %s: version %s in AD but %s in SYSVOL (replication behind, or a broken GPO)" % (
                    g.get("name"), g.get("ad_version"), g.get("sysvol_version")))
        for f in data.get("orphan_folders") or []:
            rows.append(_row([data["dns"], "", f, "folder in SYSVOL, no GPO in AD"], ["", "", "", "info"], "info"))
    R.table("Group Policy: AD against SYSVOL", "On the DC that read the domain: GPOs without a SYSVOL folder, GPOs whose version "
            "differs between AD and SYSVOL (normal for a few minutes after an edit), and SYSVOL folders with no GPO (left over).",
            ["Domain", "GPO", "GUID", "Problem"], rows, "Every GPO matches.")
    # 20. schema and configuration
    rows = []
    for d in doms:
        data = d["data"]
        sch = data.get("schema") or {}
        sh = age_hours(sch.get("changed"), now)
        if sh is not None and sh <= hours:
            st = _sev(o, "schema_change_severity", "warning")
            rows.append(_row([time_text(sch.get("changed")), data["dns"], "schema", "version %s" % sch.get("version"), "CHANGED"], ["", "", "", "", st], st))
            R.problem(data["dns"], "schema", "objectVersion", st, "the schema version changed to %s" % sch.get("version"))
        for s in sch.get("new_objects") or []:
            rows.append(_row([time_text(s.get("time")), data["dns"], s.get("class", ""), s.get("name", ""), "ADDED TO SCHEMA"], ["", "", "", "", "info"], "info"))
        for c in data.get("config_changes") or []:
            cls = {"nTDSDSA": "domain controller", "server": "server (site)", "crossRef": "partition"}.get(c.get("class"), c.get("class"))
            st = "info"
            if c.get("class") == "nTDSDSA" and c.get("change") in ("created", "deleted"):
                st = _sev(o, "dc_change_severity", "info")
                R.problem(data["dns"], "config", c.get("name"), st, "domain controller %s was %s" % (c.get("name"), "added" if c.get("change") == "created" else "removed"))
            rows.append(_row([time_text(c.get("time")), data["dns"], cls, c.get("name", ""), str(c.get("change", "")).upper()], ["", "", "", "", st], st))
        for t in data.get("trusts") or []:
            if (age_hours(t.get("created"), now) or 1e9) <= hours:
                rows.append(_row([time_text(t.get("created")), data["dns"], "trust", t.get("name", ""), "CREATED"], ["", "", "", "", "warning"]))
                R.problem(data["dns"], "trust", t.get("name"), "warning", "a trust with %s was created" % t.get("name"))
    rows.sort(key=lambda r: r[0][0], reverse=True)
    R.table("Schema and configuration changes (last %s h)" % _hours(hours), "Schema updates (the version and new classes and "
            "attributes), sites, subnets, site links and domain controllers added, changed or removed, and new trusts.",
            ["When", "Domain", "What", "Name", "Change"], rows, "None.", keep_order=True)
    # 21. notable security events
    rows = []
    for e in sec:
        k = int(e.get("id", 0))
        if k not in SEC_NOTABLE:
            continue
        sv, what = SEC_NOTABLE[k]
        rows.append(_row([time_text(e.get("time")), e["_dc"], k, sv.upper(), what + ((" (" + e["target"] + ")") if e.get("target") else ""), e.get("by", "")],
                         ["", "", "", sv]))
        R.problem("", "security", e["_dc"], sv, "%s on %s by %s" % (what, e["_dc"], e.get("by") or "?"))
    rows.sort(key=lambda r: r[0][0], reverse=True)
    R.table("Security log: notable events (last %s h)" % _hours(hours), "The Security log cleared on a DC, the DSRM password set, "
            "trusts created or removed, the domain policy changed.",
            ["When", "DC", "Event", "Severity", "What", "By"], rows, "None (or the Security log is not read: security_events).", keep_order=True)

    # ---- what could not be read ----------------------------------------------------------------------------------------------------
    notes = []
    for x in dcs:
        if x["_ok"]:
            notes += ["%s: %s" % (x["_name"], e) for e in x["data"].get("errors") or [] if not e.startswith("repadmin /showrepl")]
    for d in doms:
        notes += ["%s (domain-wide, on %s): %s" % (d["data"]["dns"], d.get("host"), e) for e in d["data"].get("errors") or []]
    if notes:
        R.sections.append({"title": "Not read", "text": "What could not be read (the report is complete apart from these):",
                           "lines": notes[:100]})

    # ---- title, summary ----------------------------------------------------------------------------------------------------------------
    crit = [p for p in R.problems if p["severity"] == "critical"]
    warn = [p for p in R.problems if p["severity"] == "warning"]

    def count(*areas):
        return sum(1 for p in R.problems if p["area"] in areas)

    def st_of(*areas):
        return worst([p["severity"] for p in R.problems if p["area"] in areas]) or "ok"
    n_ok = sum(1 for x in dcs if x["_ok"])
    names = ", ".join(sorted(set(d["data"]["dns"] for d in doms))) or ", ".join(sorted(set(x["_domain"] for x in dcs)))
    if crit or warn:
        title = "Active Directory health report: %d critical, %d warning (%s)" % (len(crit), len(warn), names)
    else:
        title = "Active Directory health report: all %d domain controller(s) healthy (%s)" % (len(dcs), names)
    n_priv = sum(len(g.get("changes") or []) for d in doms for g in d["data"].get("groups") or [])
    n_obj = sum(len(d["data"].get("created") or []) + len(d["data"].get("deleted") or []) for d in doms)
    summary = [{"label": "DCs read", "value": "%d of %d" % (n_ok, len(dcs)), "status": "ok" if n_ok == len(dcs) else "critical"},
               {"label": "Replication problems", "value": count("replication"), "status": st_of("replication")},
               {"label": "dcdiag failures", "value": count("dcdiag"), "status": st_of("dcdiag")},
               {"label": "Services down", "value": count("service"), "status": st_of("service")},
               {"label": "SYSVOL / GPO", "value": count("sysvol", "gpo"), "status": st_of("sysvol", "gpo")},
               {"label": "FSMO / DNS / time", "value": count("fsmo", "dns", "time"), "status": st_of("fsmo", "dns", "time")},
               {"label": "Privileged changes", "value": n_priv, "status": priv_sev if n_priv and priv_sev in ("warning", "critical") else ("info" if n_priv else "ok")},
               {"label": "Accounts created / deleted", "value": n_obj, "status": "info" if n_obj else "ok"},
               {"label": "GPO changes", "value": n_gpo, "status": "info" if n_gpo else "ok"}]
    report = {"title": title, "status": "critical" if crit else ("warning" if warn else "ok"),
              "subtitle": "Read %s UTC - changes of the last %s h" % (now.strftime("%Y-%m-%d %H:%M"), _hours(hours)),
              "summary": summary, "sections": R.sections}
    dc_sum = [{"dc": x["_name"], "domain": x["_domain"], "host": x.get("host"), "read": x["_ok"], "error": x.get("error", "")} for x in dcs]
    return {"report": report, "problems": R.problems, "dcs": dc_sum, "domains": summaries,
            "critical": len(crit), "warning": len(warn)}


def ps_error(stderr):
    """PowerShell's error output over WinRM (often CLIXML) as one line of plain text."""
    s = str(stderr or "")
    errs = re.findall(r'<S S="Error">(.*?)</S>', s, re.S)
    if errs:
        s = " ".join(errs)
    s = re.sub(r"_x001B_\[[0-9;]*m", "", s)
    s = re.sub(r"_x000[AD]_", " ", s)
    s = re.sub(r"#< CLIXML", "", s)
    return re.sub(r"\s+", " ", s).strip()


class FilterModule(object):
    def filters(self):
        return {"ad_report": ad_report, "ad_ps_error": ps_error}
