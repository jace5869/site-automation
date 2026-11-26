# -*- coding: utf-8 -*-
"""ACT's answer cache (1.136.0) in the report database (MySQL; the "Report database" credential, docs/REPORT_DATABASE.md,
"ACT's answer cache"). The jobs that ask ACT in batches on the AAP side (the log analysis, the VMware alarms, capacity
and right-sizing analyses) keep each question's answer: the next run with the same question - the same evidence, the
same prompt, the same provider and model - takes it from here instead of asking ACT again, until it expires.

- The key: sha256 of [provider, model, prompt version, job kind, sha256(the task), sha256(what ACT read)] - what ACT
  read is the evidence as it was sent (cut, with its first line): the same text, the same key.
- What is kept: only the analysis ACT gave, as JSON between the markers (stored()), never the rest of its words. An
  answer the jobs cannot read is not kept.
- expires: the run's time and the job kind's days (site_act_cache_ttl_days, site_act_cache_ttl_days_by). Each run that
  uses the cache deletes the rows that expired.

Used by plugins/action/site_act_ask.py (loaded by path). Standard library and PyMySQL (site_report_db.py), Python 3.6."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

import datetime
import hashlib
import importlib.util
import json
import os
import re

SCHEMA_VERSION = 1
COMPONENT = "act_cache"
TABLE = "act_answer_cache"
ANSWER_MAX = 262144                    # characters of an answer kept; a longer one is not kept
MARK_BEGIN, MARK_END = "BEGIN_ACT_ANALYSIS", "END_ACT_ANALYSIS"
TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"        # as site_report_db.TABLE_OPTS
CREATE = (
    "CREATE TABLE IF NOT EXISTS act_answer_cache (cache_key CHAR(64) NOT NULL, job_kind VARCHAR(64) NOT NULL, "
    "prompt_version VARCHAR(32) NOT NULL, model VARCHAR(128) NOT NULL, created DATETIME NOT NULL, expires DATETIME NOT NULL, "
    "answer MEDIUMTEXT NOT NULL, PRIMARY KEY (cache_key), KEY act_answer_cache_expires (expires)) " + TABLE_OPTS)
SCHEMA_VERSION_TABLE = ("CREATE TABLE IF NOT EXISTS schema_version (component VARCHAR(64) NOT NULL PRIMARY KEY, version INT NOT NULL, "
                        "applied_at DATETIME NOT NULL) " + TABLE_OPTS)
SAVE = ("INSERT INTO act_answer_cache (cache_key, job_kind, prompt_version, model, created, expires, answer) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) ON DUPLICATE KEY UPDATE job_kind = VALUES(job_kind), "
        "prompt_version = VALUES(prompt_version), model = VALUES(model), created = VALUES(created), expires = VALUES(expires), "
        "answer = VALUES(answer)")


def _h(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def key(provider, model, prompt_version, kind, task, piped):
    """The question's key (64 hex characters)."""
    return _h(json.dumps([str(provider or ""), str(model or ""), str(prompt_version or ""), str(kind or ""), _h(task), _h(piped)]))


def _analysis(text):
    """The JSON object of ACT's analysis (with items or causes), or None: between the markers, else a ```json block, else
    the outermost {...}; a trailing comma tolerated - as the jobs' reader (act_analysis_parse) finds it."""
    t = str(text or "")
    cands = []
    m = re.search(MARK_BEGIN + r"\s*(.*?)\s*" + MARK_END, t, re.S)
    if m:
        cands.append(m.group(1))
    cands += re.findall(r"```(?:json)?\s*(.*?)```", t, re.S)
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        cands.append(t[i:j + 1])
    for c in cands:
        for attempt in (c.strip(), re.sub(r",\s*([}\]])", r"\1", c.strip())):
            try:
                v = json.loads(attempt)
            except ValueError:
                continue
            if isinstance(v, list):
                v = {"items": v}
            if isinstance(v, dict) and (isinstance(v.get("items"), list) or isinstance(v.get("causes"), list)):
                return v
    return None


_redactor = []


def _redact_all(value):
    """site_redact.redact_all (the repository's one redaction), loaded by path once: what is kept never holds a secret
    ACT quoted from the evidence (a password in a log line ...)."""
    if not _redactor:
        spec = importlib.util.spec_from_file_location("site_redact_for_act_cache",
                                                      os.path.join(os.path.dirname(os.path.abspath(__file__)), "site_redact.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _redactor.append(mod.redact_all)
    return _redactor[0](value)


def _unwrap(text):
    """A model that wraps its answer ({"message": "..."}): the text inside, as the jobs' reader (act_answer_text) takes it."""
    t = str(text or "").strip()
    if t.startswith("{"):
        try:
            v = json.loads(t)
        except ValueError:
            return text
        if isinstance(v, dict) and "items" not in v and "causes" not in v:
            for k in ("message", "answer", "content", "text"):
                if isinstance(v.get(k), str):
                    return v[k]
    return text


_URL_QUERY = re.compile(r"(?i)\b((?:https?|wss?|ftp)://[^\s\"'<>?#]+|[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?/[^\s\"'<>?#]*)\?[^\s\"'<>]*")


def _scrub(value):
    """A URL's query out of every text (a gateway key rides in ?key=...): what site_redact does not take."""
    if isinstance(value, dict):
        return dict((k, _scrub(x)) for k, x in value.items())
    if isinstance(value, list):
        return [_scrub(x) for x in value]
    if isinstance(value, str):
        return _URL_QUERY.sub(lambda m: m.group(1) + "?<removed>", value)
    return value


def stored(answer):
    """What is kept of an answer: its analysis between the markers, every text in it redacted (site_redact), or '' (not
    kept: none, or longer than ANSWER_MAX)."""
    v = _analysis(_unwrap(answer))
    if v is None:
        return ""
    v = _scrub(_redact_all(v))
    s = "%s\n%s\n%s" % (MARK_BEGIN, json.dumps(v, ensure_ascii=False, sort_keys=True), MARK_END)
    return s if len(s) <= ANSWER_MAX else ""


def pretty(sql):
    """A CREATE TABLE as docs/REPORT_DATABASE.md shows it: a column or key per line."""
    head, rest = sql.split(" (", 1)
    body, tail = rest.rsplit(") ", 1)
    parts, depth, cur = [], 0, ""
    for ch in body:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return "%s (\n  %s\n) %s;" % (head, ",\n  ".join(parts + [cur.strip()]), tail)


def ddl():
    """The statements the first run sends, for making the table by hand (site_act_cache_create: false). The doc carries
    this text; a test keeps them alike."""
    return "\n\n".join([pretty(SCHEMA_VERSION_TABLE), pretty(CREATE),
                        "INSERT INTO schema_version (component, version, applied_at) VALUES ('%s', %d, UTC_TIMESTAMP());"
                        % (COMPONENT, SCHEMA_VERSION)])


def schema(conn, create):
    """(there, created): the table, made when it is not there and create is true; schema_version's row with it."""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (TABLE,))
    there = cur.fetchone() is not None
    if not there:
        if not create:
            return False, False
        cur.execute(SCHEMA_VERSION_TABLE)
        cur.execute(CREATE)
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'schema_version'")
    if cur.fetchone() is not None:
        cur.execute("SELECT version FROM schema_version WHERE component = %s", (COMPONENT,))
        r = cur.fetchone()
        if r is None and create:
            cur.execute("INSERT INTO schema_version (component, version, applied_at) VALUES (%s, %s, UTC_TIMESTAMP())",
                        (COMPONENT, SCHEMA_VERSION))
        elif r is not None and int(r[0]) > SCHEMA_VERSION:
            raise RuntimeError("the table %s is version %s, newer than this release knows (%s) - update site-automation"
                               % (TABLE, r[0], SCHEMA_VERSION))
    conn.commit()
    return True, not there


def load(conn, keys, now, days=None):
    """{key: {answer, model, created}} for the keys that have an answer not expired at now - and, with days, kept no
    longer ago than that (a shorter site_act_cache_ttl_days holds for answers kept under a longer one)."""
    keys = sorted(set(k for k in keys if k))
    since = (now - datetime.timedelta(days=float(days))) if days else None
    out = {}
    cur = conn.cursor()
    for i in range(0, len(keys), 200):
        part = keys[i:i + 200]
        cur.execute("SELECT cache_key, answer, model, created FROM act_answer_cache WHERE cache_key IN (%s) AND expires > %%s"
                    % ", ".join(["%s"] * len(part)), tuple(part) + (now,))
        for k, answer, model, created in cur.fetchall():
            if since is not None and hasattr(created, "strftime") and created < since:
                continue
            out[str(k)] = {"answer": str(answer or ""), "model": str(model or ""),
                           "created": created.strftime("%Y-%m-%d %H:%M") if hasattr(created, "strftime") else str(created or "")}
    return out


def save(conn, rows, now, days):
    """rows: [(key, kind, prompt_version, model, answer)] -> how many kept. expires: now + days."""
    exp = now + datetime.timedelta(days=float(days))
    data = [(k, str(kind)[:64], str(pv)[:32], str(model)[:128], now, exp, a) for k, kind, pv, model, a in rows if k and a]
    if data:
        conn.cursor().executemany(SAVE, data)
    conn.commit()
    return len(data)


def purge(conn, now):
    """The rows that expired, deleted -> how many."""
    n = conn.cursor().execute("DELETE FROM act_answer_cache WHERE expires <= %s", (now,))
    conn.commit()
    return int(n or 0)
