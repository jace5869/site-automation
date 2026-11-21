#!/usr/bin/python
"""Read the latest successful setup Run's ownership map, using the setup credential and CA."""
import re

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.site_aap import Api, connection


DOCUMENTATION = r'''
module: site_aap_setup_history
short_description: Read this setup job template's latest ownership map
options:
  job_id: {description: Current AAP job ID (zero outside AAP)., type: int, required: true}
  template_id: {description: Setup job template ID (zero outside AAP)., type: int, required: true}
  ca_path: {description: The setup role's CA file for AAP., type: str, default: ''}
author: site automation
'''

RETURN = r'''
fingerprints: {description: The saved map or null when no Run supplied one., type: dict}
errors: {description: Read errors encountered while recovering the map. Any error stops setup., type: list, elements: str}
'''


def read(api, path, errors):
    code, data = api.call("GET", path)
    if code != 200 or not isinstance(data, dict):
        errors.append("could not read %s (HTTP %s)" % (path.split("?")[0], code))
        # Retry this exact read solely to carry the previous map forward. Never authorize writes after a failed read.
        code, data = api.call("GET", path)
        if code != 200 or not isinstance(data, dict):
            raise ValueError(errors[-1])
    return data


def latest_map(api, job_id, template_id, errors):
    before = job_id
    while True:
        page = read(api, "jobs/?job_template=%d&id__lt=%d&status=successful&job_type=run&order_by=-id&page_size=100"
                    % (template_id, before), errors)
        rows = page.get("results")
        if not isinstance(rows, list) or "next" not in page:
            raise ValueError("incomplete job list")
        if not rows and before == job_id:
            # AAP can return an empty list when this credential cannot see the template's jobs.
            read(api, "jobs/%d/" % job_id, errors)
        for row in rows:
            oid = row.get("id") if isinstance(row, dict) else None
            if not isinstance(oid, int) or not 0 < oid < before:
                raise ValueError("invalid job ID or order in setup history")
            before = oid
            if row.get("status", "successful") != "successful" or row.get("job_type", "run") != "run":
                continue
            artifacts = read(api, "jobs/%d/" % oid, errors).get("artifacts")
            if not isinstance(artifacts, dict):
                raise ValueError("unreadable artifacts on job %d" % oid)
            if "aap_setup_fingerprints" in artifacts:
                saved = artifacts["aap_setup_fingerprints"]
                if not isinstance(saved, dict) or any(not isinstance(fp, str) or not re.fullmatch("[0-9a-f]{12}", fp)
                                                      for fp in saved.values()):
                    raise ValueError("invalid fingerprint map on job %d" % oid)
                return saved
        if not page["next"]:
            return None
        if not rows:
            raise ValueError("incomplete page in setup history")


def main():
    module = AnsibleModule(argument_spec=dict(job_id=dict(type="int", required=True),
                                              template_id=dict(type="int", required=True),
                                              ca_path=dict(type="str", default="")), supports_check_mode=True)
    p = module.params
    job, template = p["job_id"], p["template_id"]
    if not job and not template:  # standalone ansible-playbook, outside AAP
        module.exit_json(changed=False, fingerprints=None, errors=[])
    try:
        if job <= 0 or template <= 0:
            raise ValueError("missing setup job or template ID")
        api = Api(connection(), p["ca_path"])
        why = api.probe()
        if why:
            raise ValueError(why)
        errors = []
        known = latest_map(api, job, template, errors)
    except (ValueError, OSError) as ex:
        module.fail_json(msg="Setup history unreadable: %s. Ownership unverified (not ours) for every object. "
                             "All objects left as they are. The previous fingerprint map remains in effect." % ex)
    module.exit_json(changed=False, fingerprints=known, errors=errors)


if __name__ == "__main__":
    main()
