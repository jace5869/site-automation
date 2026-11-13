# -*- coding: utf-8 -*-
"""STIG Manager - health (playbooks/stigman_health.yml): what site_stigman_health read through the API, judged -
stigman_health_findings, stigman_health_sections (the report's tables) and stigman_health_line (the job's line)."""

_SMH_OPTS = {"slow_ms": 2000, "node_down": "critical", "conn_warn_pct": 80, "conn_crit_pct": 95, "restart_minutes": 60,
             "version": "", "check": "stigman"}


def _smh_opts(opts):
    o = dict(_SMH_OPTS)
    o.update({k: v for k, v in (opts or {}).items() if v is not None})
    return o


def _smh_up(v):
    return "up" if v is True else ("DOWN" if v is False else "?")


def _smh_ago(seconds):
    s = int(seconds or 0)
    if s < 3600:
        return "%d min" % (s // 60)
    if s < 172800:
        return "%d h" % (s // 3600)
    return "%d days" % (s // 86400)


def _smh_mb(n):
    return "-" if n is None else "%d MB" % int(float(n) / 1048576)


def stigman_health_findings(keycloak, targets, opts=None):
    """site_findings entries (check stigman) from site_stigman_health's keycloak and targets."""
    o = _smh_opts(opts)
    check, slow = o["check"], int(o["slow_ms"])
    out = []

    def add(sev, key, summary, hint=""):
        out.append({"check": check, "id": "%s:%s" % (check, key), "severity": sev, "summary": summary, "hint": hint})

    kc = keycloak or {}
    oidc, tok = kc.get("oidc") or {}, kc.get("token") or {}
    if oidc.get("status", 0) not in (0, 200):
        add("critical", "keycloak", "Keycloak's realm does not answer (%s): nobody can log in to STIG Manager" % (oidc.get("why") or "?"),
            "curl -sS %s/.well-known/openid-configuration" % kc.get("url", ""))
    if tok.get("status", 0) not in (0, 200):
        add("critical", "token", "Keycloak gave no token to the health check's client (%s)" % (tok.get("why") or "?"),
            "the client's secret and its service account (Keycloak: Clients - the client - Credentials)")
    versions, migrations, dbs = {}, {}, {}
    for t in targets or []:
        name, kind = t.get("name", ""), t.get("kind", "node")
        who = "STIG Manager's address" if kind == "service" else "node %s" % name
        down_sev = "critical" if kind == "service" else o["node_down"]
        st, cf = t.get("state") or {}, t.get("configuration") or {}
        if st.get("status") == 200 and st.get("currentState") not in (None, "available"):
            add(down_sev, "state:" + name, "%s: the API is %s since %s (database %s, Keycloak %s)" % (
                who, st.get("currentState"), st.get("since") or "?", _smh_up(st.get("db")), _smh_up(st.get("oidc"))),
                "the API's log: podman logs --since 1h <the STIG Manager container> (on the node)")
            continue
        if st.get("status") not in (200, 404) and cf.get("status") != 200:
            add(down_sev, "down:" + name, "%s does not answer: %s" % (who, st.get("why") or cf.get("why") or "no answer"),
                "curl -sS %s/op/state" % t.get("url", ""))
            continue
        if cf.get("status") != 200:
            add("warning", "configuration:" + name, "%s: /op/configuration does not answer (%s)" % (who, cf.get("why") or "?"))
        ms = max([x for x in (st.get("ms"), cf.get("ms")) if x is not None] or [0])
        if ms > slow:
            add("warning", "slow:" + name, "%s is slow: %d ms (warning above %d ms)" % (who, ms, slow))
        if cf.get("version"):
            versions[name] = str(cf["version"])
        if cf.get("lastMigration") is not None:
            migrations[name] = cf["lastMigration"]
        us = t.get("user") or {}
        if us.get("status", 0) in (401, 403):
            add(down_sev, "auth:" + name, "%s refuses Keycloak's token (%s): its OIDC settings (STIGMAN_OIDC_PROVIDER, the "
                                          "audience) may differ from the other nodes'" % (who, us.get("why")))
        elif us.get("status", 0) not in (0, 200):
            add(down_sev, "user:" + name, "%s: an authenticated call that reads the database failed (%s)" % (who, us.get("why") or "?"),
                "the API's log on the node, and the database host")
        elif us.get("status") == 200 and (us.get("ms") or 0) > slow:
            add("warning", "slow-db:" + name, "%s: an authenticated call took %d ms (warning above %d ms) - the database or the "
                                              "network to it" % (who, us["ms"], slow))
        co = t.get("collections") or {}
        if us.get("status") == 200 and co.get("status", 0) not in (0, 200):
            add("warning", "collections:" + name, "%s: the collections could not be listed (%s)" % (who, co.get("why") or "?"))
        ai = t.get("appinfo") or {}
        if ai.get("status") != 200:
            continue
        my, nj = ai.get("mysql") or {}, ai.get("nodejs") or {}
        if my.get("threads_connected") is not None and my.get("max_connections"):
            pct = 100.0 * my["threads_connected"] / my["max_connections"]
            if pct >= float(o["conn_warn_pct"]):
                add("critical" if pct >= float(o["conn_crit_pct"]) else "warning", "db-connections:" + name,
                    "the database has %d of its %d connections in use (%d%%)" % (my["threads_connected"], my["max_connections"], pct))
        if my.get("uptime") is not None and my["uptime"] < int(o["restart_minutes"]) * 60:
            add("warning", "db-restarted:" + name, "the database restarted %s ago (seen through %s)" % (_smh_ago(my["uptime"]), who))
        if nj.get("uptime") is not None and nj["uptime"] < int(o["restart_minutes"]) * 60 and kind == "node":
            add("warning", "restarted:" + name, "%s restarted %s ago" % (who, _smh_ago(nj["uptime"])))
        if my.get("uptime") is not None:
            dbs[name] = (str(my.get("version") or ""), float(my["uptime"]))
    if len(set(versions.values())) > 1:
        add("warning", "versions", "the nodes run different STIG Manager versions: %s" % ", ".join("%s %s" % kv for kv in sorted(versions.items())))
    if o["version"] and versions and any(v != str(o["version"]) for v in versions.values()):
        add("warning", "version", "STIG Manager is not at %s (stigman_health_version): %s" % (
            o["version"], ", ".join("%s %s" % kv for kv in sorted(versions.items()))))
    if len(set(migrations.values())) > 1:
        add("warning", "schema", "the nodes see different database schemas (lastMigration): %s - do they use the same database?"
            % ", ".join("%s %s" % kv for kv in sorted(migrations.items())))
    if len(dbs) > 1:
        vals = list(dbs.values())
        if len({v for v, _ in vals}) > 1 or max(u for _, u in vals) - min(u for _, u in vals) > 600:
            add("warning", "databases", "the nodes do not seem to use the same database (its version or uptime differ): %s"
                % ", ".join("%s %s up %s" % (n, v or "?", _smh_ago(u)) for n, (v, u) in sorted(dbs.items())))
    return out


def stigman_health_sections(keycloak, targets, opts=None):
    """The report's tables: the address and each node; the database as the API sees it; Keycloak."""
    o = _smh_opts(opts)
    slow = int(o["slow_ms"])
    rows, rs = [], []
    for t in targets or []:
        st, cf, us, co, ai = (t.get(k) or {} for k in ("state", "configuration", "user", "collections", "appinfo"))
        nj = ai.get("nodejs") or {}
        answered = st.get("status") == 200 or cf.get("status") == 200
        state = st.get("currentState") or ("answers (no /op/state)" if cf.get("status") == 200 else (st.get("why") or "no answer"))
        bad = not answered or st.get("currentState") not in (None, "available") or us.get("status", 0) not in (0, 200)
        sev = ("critical" if t.get("kind") == "service" else o["node_down"]) if bad else ""
        if sev == "low":                    # stigman_health_node_down: low (1.56.0) - the word, never the colour alone
            state += " (Low)"
        if not sev and max([x for x in (st.get("ms"), cf.get("ms"), us.get("ms")) if x is not None] or [0]) > slow:
            sev = "warning"
        rows.append([t.get("name", ""), t.get("url", ""), t.get("kind", ""), state, _smh_up(st.get("db")), _smh_up(st.get("oidc")),
                     str(cf.get("version") or "-"), str(cf.get("lastMigration") if cf.get("lastMigration") is not None else "-"),
                     "-" if st.get("ms") is None else "%d ms" % st["ms"],
                     ("%d ms" % us["ms"]) if us.get("status") == 200 else (us.get("why") or "-"),
                     "-" if co.get("count") is None else str(co["count"]),
                     "-" if nj.get("uptime") is None else _smh_ago(nj["uptime"]), _smh_mb(nj.get("rss"))])
        rs.append(sev)
    sec = {"title": "STIG Manager's address and nodes",
           "columns": ["Name", "API", "Kind", "State", "Database", "Keycloak", "Version", "Schema", "Answer", "Login + database",
                       "Collections", "Node.js up", "Memory"],
           "rows": rows, "row_status": rs,
           "text": "State, Database and Keycloak are the API's own view (/op/state). Login + database: a call with the health "
                   "check's token that reads the database (/user). Slow = above %d ms." % slow}
    st = "critical" if "critical" in rs else ("warning" if "warning" in rs else "")
    if st:
        sec["status"] = st
    out = [sec]
    db = next(((t.get("name"), (t.get("appinfo") or {}).get("mysql") or {}) for t in targets or []
               if (t.get("appinfo") or {}).get("status") == 200), None)
    if db:
        name, my = db
        conn = "-"
        if my.get("threads_connected") is not None and my.get("max_connections"):
            conn = "%d of %d (%d%%)" % (my["threads_connected"], my["max_connections"], 100.0 * my["threads_connected"] / my["max_connections"])
        out.append({"title": "The database, as STIG Manager sees it",
                    "columns": ["Seen through", "MySQL / MariaDB", "Up", "Connections now", "Most at once", "Failed connections"],
                    "rows": [[name, str(my.get("version") or "-"), "-" if my.get("uptime") is None else _smh_ago(my["uptime"]), conn,
                              "-" if my.get("max_used_connections") is None else str(int(my["max_used_connections"])),
                              "-" if my.get("aborted_connects") is None else str(int(my["aborted_connects"]))]],
                    "text": "From /op/appinfo (an admin client). The database host itself (its container, disk, the server's own "
                            "numbers) is in the findings of the database host above."})
    else:
        why = next(((t.get("appinfo") or {}).get("why") for t in targets or [] if (t.get("appinfo") or {}).get("why")), "")
        out.append({"title": "The database, as STIG Manager sees it",
                    "text": "Not read: %s." % (why or "no token").rstrip(".")})
    kc = keycloak or {}
    if kc.get("url") or (kc.get("token") or {}).get("status"):
        o2, tk = kc.get("oidc") or {}, kc.get("token") or {}

        def res(r):
            return "not asked" if not r.get("status") else ("OK, %d ms" % r["ms"] if r["status"] == 200 else (r.get("why") or "?"))
        ks = "critical" if any(r.get("status", 0) not in (0, 200) for r in (o2, tk)) else ""
        k = {"title": "Keycloak", "columns": ["Realm", "OpenID configuration", "Token for the client"],
             "rows": [[kc.get("url") or "-", res(o2), res(tk)]], "row_status": [ks]}
        if ks:
            k["status"] = ks
        out.append(k)
    return out


def stigman_health_line(keycloak, targets):
    parts = []
    for t in targets or []:
        st, cf = t.get("state") or {}, t.get("configuration") or {}
        parts.append("%s %s%s" % (t.get("name"), st.get("currentState") or (st.get("why") or "?"),
                                  (" " + str(cf["version"])) if cf.get("version") else ""))
    tk = (keycloak or {}).get("token") or {}
    parts.append("token " + ("OK" if tk.get("status") == 200 else (tk.get("why") or "not asked")))
    return "; ".join(parts)


class FilterModule(object):
    def filters(self):
        return {"stigman_health_findings": stigman_health_findings, "stigman_health_sections": stigman_health_sections,
                "stigman_health_line": stigman_health_line}
