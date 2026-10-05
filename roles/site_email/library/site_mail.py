#!/usr/bin/python
# -*- coding: utf-8 -*-
"""Send one plain-text email through an SMTP relay - Python standard library only, so it works in
every execution environment (no collection needed)."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type

DOCUMENTATION = r'''
module: site_mail
short_description: Send a plain-text email through an SMTP relay
description:
  - Sends one message with Python's smtplib. Encrypted with STARTTLS (default) or SSL; the relay's
    certificate is checked. A login, when the relay needs one, comes from the environment variables
    SMTP_USERNAME and SMTP_PASSWORD (an AAP "SMTP relay" credential), never from a parameter.
  - In check mode nothing is sent.
options:
  host: {description: The mail relay., type: str, required: true}
  port: {description: Its port., type: int, default: 25}
  security:
    description: starttls (encrypt after connecting), ssl (encrypted from the start), none (not encrypted; a login is then refused).
    type: str
    choices: [starttls, ssl, none]
    default: starttls
  sender: {description: The From address., type: str, required: true}
  to: {description: Recipients., type: list, elements: str, required: true}
  cc: {description: Copy recipients., type: list, elements: str, default: []}
  subject: {description: Subject line., type: str, required: true}
  body: {description: The text., type: str, required: true}
  ca_path: {description: A CA file to trust for the relay's certificate (default - the system's trusted CAs)., type: path}
  timeout: {description: Seconds to wait for the relay., type: int, default: 30}
author: site automation
'''

EXAMPLES = r'''
- name: Email the report
  site_mail:
    host: smtp.example.mil
    sender: aap-noreply@example.mil
    to: [ops@example.mil]
    subject: VMware secure boot report
    body: "{{ report_text }}"
'''

RETURN = r'''
recipients: {description: Who the relay accepted., type: list, returned: success}
'''

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ansible.module_utils.basic import AnsibleModule


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type='str', required=True),
            port=dict(type='int', default=25),
            security=dict(type='str', default='starttls', choices=['starttls', 'ssl', 'none']),
            sender=dict(type='str', required=True),
            to=dict(type='list', elements='str', required=True),
            cc=dict(type='list', elements='str', default=[]),
            subject=dict(type='str', required=True),
            body=dict(type='str', required=True),
            ca_path=dict(type='path'),
            timeout=dict(type='int', default=30),
        ),
        supports_check_mode=True,
    )
    p = module.params
    user = os.environ.get('SMTP_USERNAME') or None
    password = os.environ.get('SMTP_PASSWORD') or ''
    recipients = [r for r in p['to'] + p['cc'] if r]
    where = '%s:%s' % (p['host'], p['port'])
    if not recipients:
        module.fail_json(msg='no recipient')
    if user and p['security'] == 'none':
        module.fail_json(msg='refused: an SMTP login is attached but report_email_security is none - the password would '
                             'cross the network unencrypted. Use starttls or ssl, or a relay that needs no login.')
    if module.check_mode:
        module.exit_json(changed=False, recipients=recipients,
                         msg='DRY RUN: would email "%s" to %s through %s' % (p['subject'], ', '.join(recipients), where))
    try:
        msg = EmailMessage()
        msg['Subject'] = p['subject']
        msg['From'] = p['sender']
        msg['To'] = ', '.join(p['to'])
        if p['cc']:
            msg['Cc'] = ', '.join(p['cc'])
        msg['Date'] = formatdate(localtime=True)
        msg['Message-ID'] = make_msgid()
        msg.set_content(p['body'])
    except (ValueError, TypeError) as e:
        module.fail_json(msg='the email could not be built (an address or the subject is not valid): %s' % e)
    context = ssl.create_default_context(cafile=p['ca_path'] or None)
    try:
        if p['security'] == 'ssl':
            smtp = smtplib.SMTP_SSL(p['host'], p['port'], timeout=p['timeout'], context=context)
        else:
            smtp = smtplib.SMTP(p['host'], p['port'], timeout=p['timeout'])
        with smtp:
            smtp.ehlo()
            if p['security'] == 'starttls':
                if not smtp.has_extn('starttls'):
                    module.fail_json(msg='the mail relay %s does not offer STARTTLS: use report_email_security ssl (usually port '
                                         '465), or none if your relay has no encryption at all' % where)
                smtp.starttls(context=context)
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            refused = smtp.send_message(msg, from_addr=p['sender'], to_addrs=recipients)
    except ssl.SSLError as e:
        module.fail_json(msg='TLS with the mail relay %s failed: %s. If its certificate is from your own CA, set '
                             'report_email_ca_path to that CA file.' % (where, e))
    except smtplib.SMTPAuthenticationError as e:
        module.fail_json(msg='the mail relay %s refused the login (SMTP relay credential): %s' % (where, e.smtp_code))
    except smtplib.SMTPRecipientsRefused as e:
        module.fail_json(msg='the mail relay %s refused every recipient: %s' % (where, ', '.join(e.recipients)))
    except (smtplib.SMTPException, OSError) as e:
        module.fail_json(msg='could not send the email through %s: %s' % (where, e))
    accepted = [r for r in recipients if r not in refused]
    module.exit_json(changed=True, recipients=accepted, refused=sorted(refused),
                     msg='emailed "%s" to %s%s' % (p['subject'], ', '.join(accepted),
                                                    ' (refused: %s)' % ', '.join(sorted(refused)) if refused else ''))


if __name__ == '__main__':
    main()
