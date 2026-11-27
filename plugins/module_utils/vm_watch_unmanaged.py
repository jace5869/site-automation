"""Remove current VM outages covered by SolarWinds maintenance before the watch builds conditions."""
from collections import Counter, defaultdict
import datetime
from decimal import Decimal
import re
import time

from ansible.module_utils import site_host_swis
from ansible.module_utils import site_swis
from ansible.module_utils.site_swis_db import normalized_ip

QUERY = "SELECT Caption, DNS, SysName, IPAddress, UnManaged, UnManageFrom, UnManageUntil, Status FROM Orion.Nodes"

HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
VM_EVENTS = {"VmFailoverFailed", "com.vmware.vc.ha.VmRestartedByHAEvent", "VmRestartedOnAlternateHostEvent",
             "VmDasBeingResetEvent", "VmDasBeingResetWithScreenshotEvent"}


def timestamp(value):
    # Keep the full SWIS fraction at the window boundaries, including seven-digit .NET times.
    match = re.fullmatch(r"(\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?", str(value or ""))
    if not match:
        return None
    base, fraction, zone = match.groups()
    zone = "+0000" if zone in (None, "Z") else zone.replace(":", "")
    try:
        whole = datetime.datetime.strptime(base.replace(" ", "T") + zone, "%Y-%m-%dT%H:%M:%S%z")
        return int(whole.timestamp()) + Decimal("0." + (fraction or "0"))
    except ValueError:
        return None


def vm_key(vm):
    return vm["vcenter"], vm["moid"]


def suppress(data, settings, now=None):
    if not settings.get("enabled"):
        return
    vm_events = [(event, event.pop("_watch_vm_moid", "")) for event in data.get("events", [])]
    groups = ("suspects", "questions", "vms_lost")
    inventory = {vm_key(vm): vm for vm in data.pop("_watch_inventory", [])}
    candidates = set()
    for group in groups:
        for vm in data[group]:
            key = vm_key(vm)
            inventory.setdefault(key, vm)
            if not vm.get("not_judged") and not (group == "suspects" and vm["checks"][0]["busy"]):
                candidates.add(key)
    candidates.update((e["vcenter"], moid) for e, moid in vm_events if moid and e["type"] in VM_EVENTS)
    if not candidates:
        return
    try:
        why = []
        client = site_host_swis.client_of(settings, why, retries=0)
        if client is None:
            data["solarwinds_lines"] = ["SolarWinds not read: " + (why[0] if why else "missing URL or read-only credential")]
            return
        nodes = client.query(QUERY)
        names, addresses = defaultdict(set), defaultdict(set)
        for i, node in enumerate(nodes):
            for field in ("Caption", "DNS", "SysName"):
                value = str(node[field] or "").strip().casefold()
                if field != "Caption":
                    host = value[:-1] if value.endswith(".") else value
                    if len(host) > 253 or normalized_ip(host) or not all(HOST_LABEL.fullmatch(label) for label in host.split(".")):
                        continue
                    value = host.split(".", 1)[0]
                if value:
                    names[value].add(i)
            ip = normalized_ip(node["IPAddress"])
            if ip:
                addresses[ip].add(i)
        # Count every edge, including healthy VMs and VMs that themselves match several nodes.
        owners = Counter()
        unique = {}
        for key, vm in inventory.items():
            matches = names.get(vm["name"].strip().casefold(), set()) | addresses.get(normalized_ip(vm.get("ip")), set())
            owners.update(matches)
            if key in candidates and len(matches) == 1:
                unique[key] = next(iter(matches))
        now = time.time() if now is None else now
        suppressed = {}
        for key, i in unique.items():
            if owners[i] != 1:
                continue
            node = nodes[i]
            start, end = timestamp(node["UnManageFrom"]), timestamp(node["UnManageUntil"])
            unmanaged = str(node["UnManaged"]).lower() in ("true", "1")
            if unmanaged and start is not None and end is not None and start <= now < end:
                suppressed[key] = "VM %s - not mailed: unmanaged in SolarWinds until %s (node %s)" % (
                    inventory[key]["name"], node["UnManageUntil"], node["Caption"])
    except Exception as exc:  # A partial or malformed read must never suppress an alert.
        reason = exc.kind if isinstance(exc, site_swis.SwisError) else type(exc).__name__
        data["solarwinds_lines"] = ["SolarWinds not read: %s" % reason]
        return

    if suppressed:
        data["solarwinds_lines"] = list(suppressed.values())
        data["unmanaged_email_ids"] = []
        name_counts = Counter((vm["vcenter"], vm["name"]) for vm in inventory.values())
        for vc, moid in suppressed:
            name = inventory[vc, moid]["name"]
            ids = ["lost:%s:%s" % (vc, moid)]
            if name_counts[vc, name] == 1:
                ids.extend(("hung:" + name, "no-heartbeat:" + name, "question:" + name))
            data["unmanaged_email_ids"].extend("vm-watch:%s@%s" % (suffix, vc) for suffix in ids)
        data["events"] = []
        for event, moid in vm_events:
            key = event["vcenter"], moid
            if key in suppressed and event["type"] in VM_EVENTS:
                name = inventory[key]["name"]
                if event["vm"] == name and name_counts[key[0], name] == 1:
                    data["unmanaged_email_ids"].append("vm-watch:event:%s:%s@%s" % (event["type"], name, key[0]))
            else:
                data["events"].append(event)
        for group in groups:
            data[group] = [vm for vm in data[group] if vm_key(vm) not in suppressed or vm.get("not_judged")]
