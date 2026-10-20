#!/usr/bin/python
# -*- coding: utf-8 -*-
"""site_ios_csr: a new key and its CSR, made on a Cisco IOS / IOS-XE device (network_cli)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r"""
module: site_ios_csr
short_description: Make a new key and its CSR on a Cisco IOS / IOS-XE device
description:
  - Over the network_cli connection (ansible_network_os cisco.ios.ios): a new RSA key with the trustpoint's name
    as its label (not exportable - it never leaves the device; one already there is used again), the trustpoint
    (enrollment terminal pem, the subject and names given, rsakeypair the key), then crypto pki enroll, its
    questions answered, and the CSR it prints. All in one configuration session (crypto pki enroll is a
    configuration command), through the device's own edit_config, which answers prompts.
  - The certificate in use is not touched. The running configuration gets the key and the trustpoint; saving it
    (write memory) is left to the person who imports the issued certificate.
options:
  trustpoint: {description: The trustpoint's name - also the key's label., type: str, required: true}
  subject: {description: "The subject, as subject-name takes it (CN=...,O=...,C=...).", type: str, required: true}
  names: {description: The DNS names (subject-alt-name; IOS-XE 16 or later)., type: list, elements: str, default: []}
  key_bits: {description: The new RSA key's size., type: int, default: 2048}
"""

RETURN = r"""
csr: {description: The CSR (PEM)., type: str, returned: success}
key_made: {description: A new key was made (false - one with the label was there already)., type: bool, returned: success}
output: {description: What crypto pki enroll printed (the last part)., type: str, returned: always}
"""

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.connection import Connection, ConnectionError

PROMPTS = [r"Display Certificate Request to terminal\? \[yes/no\]:", r"Redisplay enrollment request\? \[yes/no\]:"]


def csr_text(output):
    """The CSR in what crypto pki enroll printed: its PEM block, or the Base64 between 'Certificate Request
    follows:' and '---End' wrapped as one. '' when there is none."""
    lines = [x.strip() for x in str(output or "").replace("\r", "").split("\n")]
    if "-----BEGIN CERTIFICATE REQUEST-----" in lines:
        i = lines.index("-----BEGIN CERTIFICATE REQUEST-----")
        j = next((k for k in range(i, len(lines)) if lines[k] == "-----END CERTIFICATE REQUEST-----"), None)
        return "\n".join(lines[i:j + 1]) + "\n" if j else ""
    b64, on = [], False
    for x in lines:
        if x.startswith("Certificate Request follows"):
            on = True
            continue
        if on and x.startswith("---End"):
            break
        if on and x:
            b64.append(x)
    return ("-----BEGIN CERTIFICATE REQUEST-----\n%s\n-----END CERTIFICATE REQUEST-----\n" % "\n".join(b64)) if b64 else ""


def main():
    module = AnsibleModule(
        argument_spec=dict(trustpoint=dict(type="str", required=True), subject=dict(type="str", required=True),
                           names=dict(type="list", elements="str", default=[]), key_bits=dict(type="int", default=2048)),
        supports_check_mode=False,
    )
    p = module.params
    tp = p["trustpoint"]
    if not tp or any(c.isspace() for c in tp):
        module.fail_json(msg="trustpoint: one word, no spaces (%r)" % tp)
    conn = Connection(module._socket_path)
    try:
        have = conn.get(command="show crypto key mypubkey rsa %s" % tp)
        key_made = ("Key name: %s" % tp) not in str(have)
        lines = []
        if key_made:
            lines.append("crypto key generate rsa general-keys label %s modulus %d" % (tp, p["key_bits"]))
        lines += ["crypto pki trustpoint %s" % tp, " enrollment terminal pem", " subject-name %s" % p["subject"],
                  " serial-number none", " ip-address none", " revocation-check none", " rsakeypair %s" % tp]
        if p["names"]:
            lines.append(" subject-alt-name %s" % " ".join(p["names"]))
        lines.append(" exit")
        lines.append({"command": "crypto pki enroll %s" % tp, "prompt": PROMPTS, "answer": ["yes", "no"], "check_all": True})
        res = conn.edit_config(candidate=lines)
    except ConnectionError as e:
        module.fail_json(msg="on the device: %s" % e, output="")
    out = (res.get("response") or [""])[-1] if isinstance(res, dict) else ""
    csr = csr_text(out)
    if not csr:
        module.fail_json(msg="crypto pki enroll printed no CSR", output=str(out)[-600:])
    module.exit_json(changed=True, csr=csr, key_made=key_made, output=str(out)[-600:])


if __name__ == "__main__":
    main()
