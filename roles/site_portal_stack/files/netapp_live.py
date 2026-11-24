"""The portal's NetApp page (/portal/netapp): the newest run of NetApp - refresh volume list in the report database, as
roles/ontap_report/library/site_ontap_live_db.py writes it (ontap_live_run, ontap_live_volume, ontap_live_aggr,
ontap_live_part), read with the portal's read-only login in one read-only transaction -> the page's model. The writer works
out the statuses, the growth and the days; this only reads, groups a volume's LUN rows into one row and orders them. The
Expand button of each volume is the portal's (site_portal.py NetappLive.expand): it needs the AAP template as the portal
reads it now. Standard library only."""
import datetime
import json
import re

NO_LOGIN = ('The NetApp page needs the "Report database" credential (report_reader) on Site portal - deploy. '
            'Run the deploy with that credential.')
NO_TABLES = ('No NetApp data in the report database yet: attach the "Report database" credential to NetApp - refresh volume '
             'list (ontap_live_db) and let it run once.')
DB_ERROR = 'The NetApp page could not read the report database (the portal\'s log says why). Check the report_reader credential on Site portal - deploy.'
RANK = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3, "": 4}
PARTS = ("snapmirror", "nodes", "disks", "ems", "clusters", "perf")


def _iso(v):
    if isinstance(v, datetime.datetime):
        return v.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%SZ")
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d)(?::(\d\d))?", str(v or ""))
    return ("%s-%s-%sT%s:%s:%sZ" % (m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6) or "00")) if m else ""


def _json(text, default):
    try:
        v = json.loads(text or "")
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _f(v):
    return None if v is None else float(v)


def missing_table(e):
    code = (getattr(e, "args", None) or [None])[0]
    if isinstance(code, int):
        return code == 1146
    t = str(e).lower()
    return "no such table" in t or "(mysql error 1146)" in t


class Database(object):
    """read() -> the model of the newest run, {"empty": why} when there is none, or an exception (said by the caller)."""

    def __init__(self, connect, mark="%s"):
        self.connect, self.mark = connect, mark

    def _q(self, cur, sql, args=()):
        cur.execute(sql.replace("%s", self.mark), tuple(args))
        return cur.fetchall()

    def has_data(self):
        """Whether there is a run to show (the bar's NetApp tab): False on no table, an exception on a fault."""
        conn = self.connect()
        try:
            try:
                return bool(self._q(conn.cursor(), "SELECT run_id FROM ontap_live_run ORDER BY run_id DESC LIMIT 1"))
            except Exception as e:  # pylint: disable=broad-except
                if missing_table(e):
                    return False
                raise
        finally:
            try:
                conn.close()
            except Exception:  # pylint: disable=broad-except
                pass

    def read(self):
        conn = self.connect()
        try:
            if self.mark == "%s":
                try:
                    conn.rollback()
                except Exception:  # pylint: disable=broad-except
                    pass
                conn.cursor().execute("START TRANSACTION READ ONLY")
            cur = conn.cursor()
            try:
                top = self._q(cur, "SELECT run_id, read_at, job, template_id, clusters, not_read, expand_template, expand_question, opts, "
                                   "volumes, offered FROM ontap_live_run ORDER BY read_at DESC, run_id DESC LIMIT 1")
            except Exception as e:  # pylint: disable=broad-except
                if missing_table(e):
                    return {"empty": NO_TABLES}
                raise
            if not top:
                return {"empty": NO_TABLES}
            t = top[0]
            rid = int(t[0])
            opts = _json(t[8], {})
            run = {"run_id": rid, "read_at": _iso(t[1]), "job": int(t[2] or 0), "template_id": int(t[3] or 0), "clusters": _json(t[4], []),
                   "not_read": _json(t[5], []), "expand_template": str(t[6] or ""), "expand_question": str(t[7] or ""), "opts": opts}
            vrows = self._q(cur, "SELECT address, cluster, svm, volume, lun, luns, size_b, used_b, aggr, aggr_free_b, datastore, ds_kind, "
                                 "offered, why, choice, root, growth_gb_day, days_full, rate_why, status, full_size_b, full_used_b, full_src, thick "
                                 "FROM ontap_live_volume WHERE run_id = %s", [rid])
            arows = self._q(cur, "SELECT address, aggr, cluster, node, size_b, used_b, growth_gb_day, days_limit, days_full, rate_why, status "
                                 "FROM ontap_live_aggr WHERE run_id = %s", [rid])
            prow = self._q(cur, "SELECT part, body FROM ontap_live_part WHERE run_id = %s", [rid])
        finally:
            try:
                conn.rollback()
                conn.close()
            except Exception:  # pylint: disable=broad-except
                pass
        return model(run, vrows, arows, prow)


SRC_WORD = {"datastore": "datastore used", "LUN": "LUN written", "volume": "volume used"}


def model(run, vrows, arows, prow):
    """The rows of one run -> the page's model: a row per LUN (a LUN volume is judged by its LUN) and per NFS volume, worst
    first then soonest full; root volumes left out (counted). 1.127.1: none left out for its size."""
    vols, small, roots = [], 0, 0
    for (addr, cl, svm, vol, lun, luns, size, used, aggr, afree, ds, kind, offered, why, choice, root, g, days, rwhy, st,
         fsize, fused, fsrc, thick) in vrows:
        if int(root or 0):
            roots += 1
            continue
        # 1.127.1: every volume and LUN the list read is on the page, however small (ontap_volume_min_gb is the health report's)
        fsize, fused = int(fsize or size or 0), int(fused if fused is not None else used or 0)
        lname = (lun or "").rsplit("/", 1)[-1]
        pct = round(100.0 * fused / fsize, 1) if fsize else 0.0
        vols.append({"k": "%s|%s|%s|%s" % (str(addr).lower(), svm, vol, lun or ""), "addr": addr, "c": cl, "svm": svm, "v": vol, "lun": lname,
                     "name": vol + (("/" + lname) if lun and int(luns or 0) > 1 else ""), "luns": int(luns or 0), "aggr": aggr,
                     "aggr_free": int(afree or 0), "ds": ds or "", "kind": kind or "", "thick": bool(int(thick or 0)),
                     "vol_size": int(size or 0), "size": fsize, "used": fused, "pct": pct, "free": max(0, fsize - fused),
                     "src": SRC_WORD.get(fsrc or "volume", "volume used"),
                     "full_text": "%s %d%% %s" % ((fsrc or "volume"), int(round(pct)), "written" if fsrc == "LUN" else "used"),
                     "g": _f(g), "days": _f(days), "rate_why": rwhy or "", "st": st or "ok", "offered": bool(int(offered or 0)) and bool(choice),
                     "why": why or "", "choice": choice or ""})
    # (user 1.127.0) the ones on a VMware datastore first, then the rest; each least free first (the fullness of the list:
    # datastore, else LUN written, else volume); ties by name
    vols.sort(key=lambda v: (0 if v["ds"] else 1, -(float(v["used"]) / v["size"] if v["size"] else 0.0), v["name"]))
    aggrs = []
    for (addr, name, cl, node, size, used, g, dlim, dfull, rwhy, st) in arows:
        size, used = int(size or 0), int(used or 0)
        aggrs.append({"addr": addr, "a": name, "c": cl, "node": node, "size": size, "used": used, "pct": round(100.0 * used / size, 1) if size else 0.0,
                      "free": size - used, "g": _f(g), "d97": _f(dlim), "full": _f(dfull), "rate_why": rwhy or "", "st": st or "ok"})
    aggrs.sort(key=lambda a: (RANK.get(a["st"], 4), a["d97"] if a["d97"] is not None else 1e12, -a["pct"], a["a"]))
    parts = {}
    for name, body in prow:
        if name in PARTS:
            parts[name] = _json(body, {})
    return add_sites({"run": run, "volumes": vols, "aggrs": aggrs, "parts": parts, "left_out": {"roots": roots, "small": small}})


AUTO_SITE = r"^([A-Za-z]{4}[0-9]{2})"       # ontap_live_site_pattern '' (auto): a site code as the cluster's (or SVM's) prefix


def site_rule(pattern):
    """ontap_live_site_pattern -> a compiled regex (its first group is the site), or None when it is not one"""
    try:
        rx = re.compile(str(pattern or "").strip() or AUTO_SITE)
    except re.error:
        return None
    return rx if rx.groups >= 1 else None


def site_of(rx, cluster, svm=""):
    """the site of a row: the pattern's first group on the cluster's name, else on the SVM's; '' when neither has one"""
    for name in (cluster, svm):
        m = rx.search(str(name or "")) if rx is not None else None
        if m and m.group(1):
            return m.group(1).upper()
    return ""


def add_sites(m):
    """1.127.2: each row's site (ontap_live_site_pattern on its cluster, then its SVM) and m["sites"]: [{site, n}] - the
    volumes and LUNs of each - only when there are two or more (one site: no filter to show)."""
    rx = site_rule((m["run"].get("opts") or {}).get("site_pattern"))
    for v in m["volumes"]:
        v["site"] = site_of(rx, v.get("c"), v.get("svm"))
    for a in m["aggrs"]:
        a["site"] = site_of(rx, a.get("c"))
    for name, body in (m.get("parts") or {}).items():
        for r in (body.get("rows") or []) if isinstance(body, dict) else []:
            if not isinstance(r, dict):
                continue
            if name == "clusters":
                r["site"] = site_of(rx, r.get("name"))
            elif name == "snapmirror":
                r["site"] = site_of(rx, r.get("dc"), str(r.get("dst") or "").split(":", 1)[0])
            else:
                r["site"] = site_of(rx, r.get("cluster"), r.get("svm"))
    n = {}
    for v in m["volumes"]:
        if v["site"]:
            n[v["site"]] = n.get(v["site"], 0) + 1
    for a in m["aggrs"]:
        if a["site"]:
            n.setdefault(a["site"], 0)
    m["sites"] = [{"site": k, "n": n[k]} for k in sorted(n)] if len(n) >= 2 else []
    return m
