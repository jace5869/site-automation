#!/usr/bin/env python3
"""The fake UCS Manager domain for tests/ucs/run_ucs_test.sh and tests/test_ucs_inventory.py: the class IDs and attribute
names of Cisco's UCS Manager object model (as Cisco's ucsmsdk defines them), the values made up.

  fixture.py OUT.json [USER PASSWORD]

One domain UCS-DC1 (a cluster of two UCS-FI-6454, HA ready): chassis 1 (UCSB-5108-AC2, two UCS-IOM-2408, four
power supplies - psu-4 inoperable), three blades (two UCSB-B200-M5 on two BIOS versions, one UCSB-B200-M6 degraded)
and one rack server (UCSC-C240-M5SX), their CPUs, DIMMs (one slot empty), adapters, a RAID controller, every
running firmware, two service profiles and a template, faults (critical, major, minor, cleared). equipmentFex is
refused (error 103 - an older UCS Manager without it), for Not all read."""
import json
import sys

out = sys.argv[1]
user, password = (sys.argv[2], sys.argv[3]) if len(sys.argv) > 3 else ("svc_ro", "readonly-test")


def fw(dn, typ, version, deployment="system", package=""):
    return {"dn": dn, "type": typ, "deployment": deployment, "version": version, "packageVersion": package or version + "B",
            "invTag": "", "status": ""}


def server(dn, model, serial, bios, cimc, sp, cpus=2, cores=40, memory=393216, operability="operable", rack=False, **kw):
    d = {"dn": dn, "model": model, "serial": serial, "vendor": "Cisco Systems Inc", "numOfCpus": str(cpus), "numOfCores": str(cores),
         "numOfThreads": str(cores * 2), "totalMemory": str(memory), "availableMemory": str(memory), "memorySpeed": "2933",
         "assignedToDn": "org-root/ls-%s" % sp if sp else "", "association": "associated" if sp else "none",
         "operability": operability, "operState": "ok" if operability == "operable" else operability, "operPower": "on",
         "presence": "equipped", "uuid": "00000000-0000-0000-0000-%012d" % sum(map(ord, dn)), "usrLbl": "",
         "name": "", "partNumber": "73-0000-01", "assetTag": ""}
    if rack:
        d["id"] = dn.rsplit("-", 1)[-1]
    else:
        d["chassisId"], d["slotId"] = "1", dn.rsplit("-", 1)[-1]
    d.update(kw)
    firmware = [fw(dn + "/bios/fw-boot-loader", "blade-bios", bios, "boot-loader", bios),
                fw(dn + "/mgmt/fw-system", "blade-controller", cimc),
                fw(dn + "/mgmt/fw-boot-loader", "blade-controller", cimc, "boot-loader"),
                fw(dn + "/boardController/mgmt/fw-system", "board-controller", "14.0")]
    return d, firmware


classes = {"topSystem": [{"dn": "sys", "name": "UCS-DC1", "address": "192.0.2.10", "mode": "cluster",
                          "systemUpTime": "120:05:03:22", "currentTime": "2026-11-03T06:00:00.000"}],
           "mgmtEntity": [{"dn": "sys/mgmt-entity-A", "id": "A", "leadership": "primary", "state": "up", "haReady": "yes",
                           "haReadiness": "ready", "haFailureReason": ""},
                          {"dn": "sys/mgmt-entity-B", "id": "B", "leadership": "subordinate", "state": "up", "haReady": "yes",
                           "haReadiness": "ready", "haFailureReason": ""}],
           "networkElement": [{"dn": "sys/switch-%s" % s, "id": s, "model": "UCS-FI-6454", "serial": "FDO0000000%s" % s,
                               "vendor": "Cisco Systems, Inc.", "revision": "0", "oobIfIp": "192.0.2.%d" % (11 + i),
                               "oobIfMask": "255.255.255.0", "oobIfGw": "192.0.2.1", "oobIfMac": "00:00:5e:00:53:0%d" % i,
                               "totalMemory": "65536", "operability": "operable", "thermal": "ok"} for i, s in enumerate("AB")],
           "equipmentChassis": [{"dn": "sys/chassis-1", "id": "1", "model": "UCSB-5108-AC2", "serial": "FOX0000001",
                                 "vendor": "Cisco Systems Inc", "partNumber": "68-0000-01", "usrLbl": "", "power": "ok",
                                 "thermal": "ok", "operability": "operable", "operState": "operable", "presence": "equipped"}],
           "equipmentIOCard": [{"dn": "sys/chassis-1/slot-%d" % n, "id": str(n), "chassisId": "1", "side": "left" if n == 1 else "right",
                                "switchId": s, "model": "UCS-IOM-2408", "serial": "FCH000000%d" % n, "operability": "operable",
                                "operState": "operable", "presence": "equipped"} for n, s in ((1, "A"), (2, "B"))],
           "computeBlade": [], "computeRackUnit": [], "firmwareRunning": [], "adaptorUnit": [], "processorUnit": [],
           "memoryUnit": [], "storageController": [], "equipmentPsu": [],
           "lsServer": [{"dn": "org-root/ls-ESX01", "name": "ESX01", "type": "instance", "pnDn": "sys/chassis-1/blade-1",
                         "assocState": "associated", "operState": "ok", "srcTemplName": "ESX-TEMPLATE",
                         "operSrcTemplName": "org-root/ls-ESX-TEMPLATE"},
                        {"dn": "org-root/ls-ESX02", "name": "ESX02", "type": "instance", "pnDn": "sys/chassis-1/blade-2",
                         "assocState": "associated", "operState": "ok", "srcTemplName": "", "operSrcTemplName": ""},
                        {"dn": "org-root/ls-ESX-TEMPLATE", "name": "ESX-TEMPLATE", "type": "updating-template", "pnDn": "",
                         "assocState": "unassociated", "operState": "ok", "srcTemplName": "", "operSrcTemplName": ""}],
           "faultInst": [{"dn": "sys/chassis-1/psu-4/fault-F0369", "code": "F0369", "severity": "critical", "ack": "no",
                          "descr": "Power supply 4 in chassis 1 power: failed", "cause": "power-problem",
                          "created": "2026-10-30T08:15:00.000", "lastTransition": "2026-10-30T08:15:00.000", "occur": "1"},
                         {"dn": "sys/chassis-1/blade-3/fault-F0283", "code": "F0283", "severity": "major", "ack": "yes",
                          "descr": "DIMM 4 on server 1/3 operability: degraded", "cause": "equipment-degraded",
                          "created": "2026-10-28T10:00:00.000", "lastTransition": "2026-10-29T10:00:00.000", "occur": "3"},
                         {"dn": "sys/switch-A/slot-1/switch-ether/port-49/fault-F0727", "code": "F0727", "severity": "minor",
                          "ack": "no", "descr": "Port 1/49 on fabric interconnect A oper state: link-down", "cause": "link-down",
                          "created": "2026-11-01T01:00:00.000", "lastTransition": "2026-11-01T01:00:00.000", "occur": "1"},
                         {"dn": "sys/chassis-1/blade-2/fault-F0181", "code": "F0181", "severity": "cleared", "ack": "no",
                          "descr": "Local disk 1 on server 1/2 operability: inoperable", "cause": "equipment-inoperable",
                          "created": "2026-10-01T10:00:00.000", "lastTransition": "2026-10-02T10:00:00.000", "occur": "1"}]}

F = classes["firmwareRunning"]
F += [fw("sys/mgmt/fw-system", "system", "4.2(3d)", package="4.2(3d)A")]
for s in "AB":
    F += [fw("sys/switch-%s/mgmt/fw-system" % s, "switch-software", "9.3(5)I42(3d)", package="4.2(3d)A"),
          fw("sys/switch-%s/mgmt/fw-kernel" % s, "switch-kernel", "9.3(5)I42(3d)", "kernel", "4.2(3d)A")]
for n in (1, 2):
    F += [fw("sys/chassis-1/slot-%d/mgmt/fw-system" % n, "iocard", "4.2(3d)"),
          fw("sys/chassis-1/slot-%d/mgmt/fw-boot-loader" % n, "iocard", "4.2(3d)", "boot-loader")]

for dn, model, serial, bios, cimc, sp, extra in (
        ("sys/chassis-1/blade-1", "UCSB-B200-M5", "FLM0000001", "B200M5.4.2.3b.0.0613230245", "4.2(3d)", "ESX01", {}),
        ("sys/chassis-1/blade-2", "UCSB-B200-M5", "FLM0000002", "B200M5.4.2.1c.0.0405220734", "4.2(1f)", "ESX02", {}),
        ("sys/chassis-1/blade-3", "UCSB-B200-M6", "FLM0000003", "B200M6.4.2.3b.0.0613230246", "4.2(3d)", "",
         {"operability": "degraded", "cores": 56, "memory": 524288}),
        ("sys/rack-unit-1", "UCSC-C240-M5SX", "WZP0000001", "C240M5.4.2.3b.0.0613230245", "4.2(3d)", "", {"rack": True})):
    srv, sfw = server(dn, model, serial, bios, cimc, sp, **extra)
    classes["computeRackUnit" if dn.startswith("sys/rack") else "computeBlade"].append(srv)
    F += sfw
    a = dn + "/adaptor-1"
    classes["adaptorUnit"].append({"dn": a, "id": "1", "model": "UCSB-MLOM-40G-04" if "blade" in dn else "UCSC-PCIE-C25Q-04",
                                   "serial": "FCH%07d" % (len(classes["adaptorUnit"]) + 11), "vendor": "Cisco Systems Inc",
                                   "pciSlot": "MLOM", "operability": "operable", "presence": "equipped"})
    F += [fw(a + "/mgmt/fw-system", "adaptor", "5.2(3a)" if "blade-2" not in dn else "5.2(1b)"),
          fw(a + "/mgmt/fw-boot-loader", "adaptor", "4.0(1a)", "boot-loader")]
    for c in (1, 2):
        classes["processorUnit"].append({"dn": dn + "/board/cpu-%d" % c, "id": str(c), "socketDesignation": "CPU%d" % c,
                                         "model": "Intel(R) Xeon(R) Gold 6248 CPU @ 2.50GHz" if "M5" in model else
                                         "Intel(R) Xeon(R) Gold 6330 CPU @ 2.00GHz",
                                         "cores": "20", "threads": "40", "speed": "2.5", "operability": "operable",
                                         "presence": "equipped"})
    for m in (1, 2, 3):
        classes["memoryUnit"].append({"dn": dn + "/board/memarray-1/mem-%d" % m, "id": str(m), "location": "DIMM_A%d" % m,
                                      "capacity": "32768" if m < 3 else "unspecified", "presence": "equipped" if m < 3 else "missing",
                                      "model": "UCS-MR-X32G2RW" if m < 3 else "", "serial": "%08d" % m if m < 3 else "",
                                      "vendor": "0xCE00" if m < 3 else "", "type": "DDR4" if m < 3 else "",
                                      "clock": "2933" if m < 3 else "", "operability": "operable" if m < 3 else "unknown"})
classes["storageController"].append({"dn": "sys/chassis-1/blade-1/board/storage-SAS-1", "id": "1", "type": "SAS",
                                     "model": "Cisco 12G Modular RAID controller with 2GB cache", "serial": "SK00000001",
                                     "vendor": "LSI Logic", "operability": "operable", "presence": "equipped"})
F += [fw("sys/chassis-1/blade-1/board/storage-SAS-1/fw-system", "storage-controller", "51.10.0-3612")]
for p in (1, 2, 3, 4):
    classes["equipmentPsu"].append({"dn": "sys/chassis-1/psu-%d" % p, "id": str(p), "model": "UCSB-PSU-2500ACDV",
                                    "serial": "DTM000000%d" % p, "vendor": "Cisco Systems Inc", "psuFirmwareVersion": "05.08",
                                    "psuWattage": "2500", "power": "on" if p < 4 else "off",
                                    "operability": "operable" if p < 4 else "inoperable", "presence": "equipped"})
for s in "AB":
    classes["equipmentPsu"].append({"dn": "sys/switch-%s/psu-1" % s, "id": "1", "model": "UCS-PSU-6332-AC", "serial": "LIT0000%s" % s,
                                    "vendor": "Cisco Systems Inc", "psuFirmwareVersion": "", "psuWattage": "650", "power": "on",
                                    "operability": "operable", "presence": "equipped"})
classes["equipmentPsu"].append({"dn": "sys/rack-unit-1/psu-2", "id": "2", "model": "", "serial": "", "presence": "missing",
                                "operability": "unknown"})

json.dump({"user": user, "password": password, "version": "4.2(3d)", "priv": "read-only", "classes": classes,
           "fail": {"equipmentFex": [103, "class equipmentFex is not a valid class"]}}, open(out, "w"), indent=1)
