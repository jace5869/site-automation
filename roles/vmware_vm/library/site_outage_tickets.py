#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Read the ServiceNow tickets of one stretch of time, for the outage review's time line (GET only)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_outage_tickets
short_description: Read the ServiceNow tickets of one stretch of time, for the outage review's time line (GET only)
description:
  - The tickets the health workflows opened (their correlation id starts with I(prefix)) that were open at some
    moment between I(begin) and I(end), the tickets named in I(numbers), and their work notes. The outage review of
    the VM outage watch (playbooks/vm_outage_watch.yml, vm_watch_review) draws them on its time line.
  - The address and the account come from SN_HOST, SN_USERNAME, SN_PASSWORD, as the "ServiceNow API" credential in
    AAP gives them. GET only - it changes nothing in ServiceNow.
  - It never fails the job. What could not be read comes back as I(error) (the tickets) or I(notes_error) (the work
    notes), and the review says so in its ServiceNow lane.
options:
  host: {description: "The instance (name or https URL). Default: SN_HOST.", type: str}
  username: {description: "The account. Default: SN_USERNAME.", type: str}
  password: {description: "Its password. Default: SN_PASSWORD.", type: str}
  table: {description: The ticket table., type: str, default: incident}
  prefix: {description: The correlation prefix of the tickets the health workflows open (servicenow_correlation_prefix)., type: str, default: site-health}
  numbers: {description: More tickets to add by number (a ticket opened by hand)., type: list, elements: str, default: []}
  begin: {description: The start of the stretch (seconds since 1970)., type: float, required: true}
  end: {description: Its end (seconds since 1970)., type: float, required: true}
  max_tickets: {description: At most this many tickets., type: int, default: 200}
  validate_certs: {description: Check the instance's certificate., type: bool, default: true}
  ca_path: {description: A CA bundle to check it against., type: str}
  timeout: {description: Seconds to wait for an answer., type: int, default: 30}
author: site automation
'''

RETURN = r'''
tickets:
  description: The tickets, oldest first. Times are UTC, like 2026-11-12T02:14:00Z; '' = not set.
  type: list
  returned: always
  sample: [{sys_id: 0a1b, number: INC0012345, short: "[site-health] esx03.example.mil: host not responding", description: "...",
            opened: "2026-11-12T02:14:00Z", resolved: "2026-11-12T03:40:00Z", closed: "", close_notes: "path replaced",
            state: "6", active: true, correlation_id: "site-health:1a2b3c4d:vm_watch", notes: [["2026-11-12T02:20:00Z", "text"]]}]
asked: {description: True when the credential was there and ServiceNow was asked., type: bool, returned: always}
error: {description: "Why the tickets were not read ('' = read).", type: str, returned: always}
notes_error: {description: "Why the work notes were not read ('' = read, or nothing to read).", type: str, returned: always}
'''

import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from ansible.module_utils.basic import AnsibleModule, env_fallback
from ansible.module_utils.urls import open_url

UTC = datetime.timezone.utc
FIELDS = ["sys_id", "number", "short_description", "description", "opened_at", "resolved_at", "closed_at", "close_notes", "state",
          "active", "correlation_id", "sys_created_on"]


class SnError(Exception):
    pass


def error_text(e):
    body = ""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:            # noqa: BLE001
        pass
    try:
        err = json.loads(body).get("error") or {}
        text = ": ".join(x for x in (str(err.get("message") or ""), str(err.get("detail") or "")) if x)
    except (ValueError, AttributeError):
        text = " ".join(body.split())[:200]
    return "HTTP %s%s" % (getattr(e, "code", "?"), (": " + text) if text else "")


class Api(object):
    """The Table API with basic authentication (GET only)."""

    def __init__(self, url, username, password, validate_certs=True, ca_path=None, timeout=30):
        self.url = url.rstrip("/")
        self.username, self.password = username, password
        self.validate_certs, self.ca_path, self.timeout = validate_certs, ca_path or None, timeout

    def get(self, path, params):
        full = "%s/%s?%s" % (self.url, path.lstrip("/"), urlencode(params))
        try:
            r = open_url(full, method="GET", url_username=self.username, url_password=self.password, force_basic_auth=True,
                         headers={"Accept": "application/json"}, validate_certs=self.validate_certs, ca_path=self.ca_path,
                         timeout=self.timeout)
            rows = json.loads(r.read().decode("utf-8", "replace")).get("result")
        except HTTPError as e:
            raise SnError("GET %s: %s" % (path, error_text(e)))
        except URLError as e:
            raise SnError("GET %s: no connection (%s)" % (path, getattr(e, "reason", e)))
        except (ValueError, OSError, AttributeError) as e:
            raise SnError("GET %s: %s" % (path, e))
        if not isinstance(rows, list):
            raise SnError("GET %s: no result list in the answer" % path)
        return rows


def base_url(host):
    h = str(host or "").strip().rstrip("/")
    return h if h.startswith(("http://", "https://")) else "https://" + h


def sn_text(seconds):
    """Seconds since 1970 -> the UTC text ServiceNow compares with."""
    return datetime.datetime.fromtimestamp(float(seconds), tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


def iso(text):
    """ServiceNow's UTC text (2026-11-12 02:14:00) -> 2026-11-12T02:14:00Z ('' when it is not a time)."""
    t = str(text or "").strip()
    try:
        return datetime.datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ""


def ticket(x):
    return {"sys_id": str(x.get("sys_id") or ""), "number": str(x.get("number") or ""),
            "short": " ".join(str(x.get("short_description") or "").split()), "description": str(x.get("description") or "")[:6000],
            "opened": iso(x.get("opened_at") or x.get("sys_created_on")), "resolved": iso(x.get("resolved_at")),
            "closed": iso(x.get("closed_at")), "close_notes": str(x.get("close_notes") or "").strip()[:1500],
            "state": str(x.get("state") or ""), "active": str(x.get("active")).lower() in ("true", "1"),
            "correlation_id": str(x.get("correlation_id") or ""), "notes": []}


def read_tickets(api, table, prefix, numbers, begin, end, cap):
    """The tickets open at some moment of the stretch -> oldest first."""
    path = "api/now/table/%s" % table
    common = {"sysparm_fields": ",".join(FIELDS), "sysparm_display_value": "false", "sysparm_exclude_reference_link": "true",
              "sysparm_limit": cap}
    found = {}
    if prefix:
        # opened before the stretch ends, and touched after it starts (a ticket resolved long before is left out)
        q = "correlation_idSTARTSWITH%s:^sys_created_on<=%s^sys_updated_on>=%s^ORDERBYsys_created_on" % (prefix, sn_text(end), sn_text(begin))
        for x in api.get(path, dict(common, sysparm_query=q)):
            found[str(x.get("sys_id") or "")] = ticket(x)
    wanted = sorted({str(n).strip() for n in numbers if str(n or "").strip()})
    if wanted:
        for x in api.get(path, dict(common, sysparm_query="numberIN" + ",".join(wanted))):
            found.setdefault(str(x.get("sys_id") or ""), ticket(x))
    found.pop("", None)
    return sorted(found.values(), key=lambda t: (t["opened"], t["number"]))[:cap]


def read_notes(api, table, tickets, per_ticket=30, chars=1500):
    """The tickets' work notes, oldest first, into each ticket's notes -> error text ('' = read)."""
    by_id = {t["sys_id"]: t for t in tickets}
    if not by_id:
        return ""
    try:
        rows = api.get("api/now/table/sys_journal_field",
                       {"sysparm_query": "element=work_notes^name=%s^element_idIN%s^ORDERBYsys_created_on" % (table, ",".join(sorted(by_id))),
                        "sysparm_fields": "element_id,value,sys_created_on", "sysparm_display_value": "false",
                        "sysparm_limit": min(10000, per_ticket * len(by_id))})
    except SnError as e:
        text = str(e)
        if "HTTP 403" in text or "HTTP 401" in text:
            text += " - the account needs read on sys_journal_field (the work notes)"
        return text
    for x in rows:
        t, when, text = by_id.get(str(x.get("element_id") or "")), iso(x.get("sys_created_on")), str(x.get("value") or "").strip()
        if t is None or not when or not text or len(t["notes"]) >= per_ticket:
            continue
        t["notes"].append([when, text[:chars]])
    for t in tickets:
        t["notes"].sort()
    return ""


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", fallback=(env_fallback, ["SN_HOST"])),
            username=dict(type="str", fallback=(env_fallback, ["SN_USERNAME"])),
            password=dict(type="str", no_log=True, fallback=(env_fallback, ["SN_PASSWORD"])),
            table=dict(type="str", default="incident"), prefix=dict(type="str", default="site-health"),
            numbers=dict(type="list", elements="str", default=[]),
            begin=dict(type="float", required=True), end=dict(type="float", required=True),
            max_tickets=dict(type="int", default=200), validate_certs=dict(type="bool", default=True),
            ca_path=dict(type="str"), timeout=dict(type="int", default=30)),
        supports_check_mode=True,
    )
    p = module.params
    out = {"changed": False, "tickets": [], "asked": False, "error": "", "notes_error": ""}
    if not (p["host"] and p["username"] and p["password"]):
        out["error"] = "not read: attach the ServiceNow API credential to the job template"
        module.exit_json(**out)
    out["asked"] = True
    api = Api(base_url(p["host"]), p["username"], p["password"], p["validate_certs"], p["ca_path"], p["timeout"])
    try:
        out["tickets"] = read_tickets(api, p["table"], p["prefix"], p["numbers"], p["begin"], p["end"], max(1, p["max_tickets"]))
    except SnError as e:
        out["error"] = "not read: %s" % e
        module.exit_json(**out)
    out["notes_error"] = read_notes(api, p["table"], out["tickets"])
    module.exit_json(**out)


if __name__ == "__main__":
    main()
