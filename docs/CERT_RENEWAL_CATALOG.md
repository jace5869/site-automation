# Certificate renewal through the PKI's catalog item

The certificate renewals - **Certificate renewal - CSRs** (Linux), **Windows certificate renewal**,
**VMware - certificate renewal** and **Network device certificate renewal** - make a new key and a CSR
for each certificate that expires soon. By default each CSR goes on a ServiceNow **incident**, and a
person submits it to the PKI.

With `cert_renew_servicenow: catalog` there is no incident: the job **orders your PKI's catalog item**
(for example *DoD PKI Certificate Request*) for each certificate, with the CSR in the item's
*Please add CSR* question - exactly what a person would do on the portal.

| | `incident` (the default) | `catalog` |
|---|---|---|
| Per certificate | an incident, the CSR attached, a work note with the next steps | a request (REQ / RITM) on the PKI's catalog item, the CSR in its CSR question |
| Who sends it to the PKI | a person, by hand | the job |
| A run again | finds the incident; no second CSR | finds the request by the CSR on it; no second order |
| Once the certificate is replaced | the incident is noted as cleared (or resolved) | nothing to close: the PKI closes the request |

## What a run does

For each certificate that expires within the warning days (30 by default):

1. **The CSR** is made where the key stays, as always: on the Linux or Windows server, on vCenter or the
   ESXi host, on the Cisco device. The key never leaves it.
2. **Already ordered?** The job looks for a request carrying this CSR. The CSR's name in the order says
   which certificate it is for and when that certificate expires:
   `web01.example.mil-1a2b3c4d-exp20261114.csr`.
   - found **open, in progress, or Closed Complete** (the PKI issued it - install it): not ordered again.
   - found only **Closed Incomplete or Closed Skipped** (rejected, cancelled - `cert_renew_catalog_reorder_states`):
     ordered again. On vCenter, an ESXi host or a Cisco device that also means a new CSR.
   - not found: ordered.
3. **The order**: one request per certificate. Your answers (`cert_renew_catalog_variables`), the CSR in the
   CSR question, and - if you name a question for it (`cert_renew_catalog_names_variable`) - the certificate's
   names (subjectAltName).
4. **Checked**: the job reads the requested item (RITM) back and makes sure the CSR is on it. If ServiceNow
   did not move the CSR into the request, the job attaches it to the RITM (the paperclip) - the PKI sees it
   either way, and the next run finds the order by it.

The job output lists every certificate:

```
ORDERED RITM0012345 (REQ0012344) | web01.example.mil | web01.example.mil-1a2b3c4d-exp20261114.csr | the CSR is in the request (its question csr_file)
ORDERED ALREADY RITM0012001 (Work in Progress) | app01.example.mil | app01.example.mil-77aa1c2e-exp20261102.csr
ORDERED ALREADY RITM0011876 (Closed Complete) | db01.example.mil | ... | the PKI issued it: install it
ORDERED RITM0012350 (REQ0012349) (again: RITM0011990 closed Closed Incomplete) | ...
FAILED  | web02.example.mil | ... | the order was refused: HTTP 400: Mandatory Variables are required - ...
```

A certificate that could not be ordered fails the job (the others are still ordered), so a schedule shows red.

**Not ordered here:** a network device without a CSR made by the job (anything but Cisco IOS / IOS-XE with
`net_cert_cisco_csr`): it is in the report; make its CSR on the device and order it by hand. A VMCA-signed
vCenter or host certificate: renew it in vCenter, as before.

## Before you start

- **The item's sys_id.** Open the item on the portal: the address ends in
  `?id=sc_cat_item&sys_id=a3034a7325933210361adc6bd7ce8c93`. That sys_id is the surest way to name it.
- **The account that orders.** The renewal orders as the account in the *ServiceNow API* credential.
  That account must be allowed to order the item (the item's *Available for* / user criteria) and to read
  the requests it made. Ask the PKI team whether they accept requests from a service account, and who
  should be *Requested For*.
- **Same instance.** The job orders on the instance in the credential. If the PKI's catalog is on another
  ServiceNow, give the renewal templates a *ServiceNow API* credential for that one.

## Step 1 - the item's questions (their internal names)

The settings need each question's **internal name** (e.g. `requested_for`), not its label
("Requested For"). Two ways to see them:

**In your browser, no job needed.** Logged in to ServiceNow, open:

```
https://<your-instance>/api/sn_sc/servicecatalog/items/<the item's sys_id>
```

It shows the item as JSON (Firefox lays it out as a tree). Under `variables`, each question has:

| Field | Means |
|---|---|
| `name` | its internal name - what the settings use |
| `label` | what the form shows |
| `mandatory` | `true`: it needs an answer |
| `type` | `33` attachment (the CSR's), `31` requested for, `8` reference, `5` select box, `6` single line text, `2` multi line text, `11` label (nothing to fill in) |
| `reference` | for a reference: the table (`sys_user`, `cmn_location` ...) |
| `choices` | for a select box: each `value` and `label` |

This is what *your* account sees. The job sees what the credential's account sees - step 3 checks that.

With catalog admin rights there is also the list:
`https://<your-instance>/item_option_new_list.do?sysparm_query=cat_item=<the item's sys_id>` (columns Name,
Type, Mandatory).

**Or run the catalog test** (step 3): it prints the same, one line per question.

## Step 2 - the settings

In `playbooks/group_vars/all.yml` (every renewal job reads them). An example for a form like the DoD PKI
one - **the internal names are examples**: use the ones step 1 shows.

```yaml
cert_renew_servicenow: catalog
cert_renew_catalog_item: a3034a7325933210361adc6bd7ce8c93     # or its name: "DoD PKI Certificate Request"
cert_renew_catalog_variables:
  requested_for: svc_aap_sn            # a reference: a user name, a name, or a sys_id
  title_rank: CTR
  email: sysadmins@yoursite.mil
  dsn: "314-555-0100"
  directorate: 0f1e2d3c4b5a69788796a5b4c3d2e1f0   # a reference to a table of your own: its sys_id
  room_number: "010"
  contact_method: Email                # a select box: its label or its value
  location: "Building 100"             # a reference: the location's name, or its sys_id
cert_renew_catalog_csr_variable: ""    # empty = the item's only attachment question
cert_renew_catalog_names_variable: additional_sans   # the "List any additional SANs" box gets the certificate's names
```

- **A reference** (Requested For, Location ...): the record's **name** works - the job looks it up and
  sends its sys_id (a user: user name or full name). For a table of your own the job can only look up a
  `name` column: if it says "no record named", give the **sys_id** (open the record, or the reference
  field's lookup, and copy it).
- **A select box**: its label (`Email`) or its value (`email`).
- **In an answer**, `{host}`, `{subject}`, `{names}`, `{expires}`, `{issuer}` and `{where}` become the
  certificate's - for a free-text question such as a justification:
  `justification: "Renewal of {subject} on {host}, expires {expires}"`.
- **Not every question needs an answer** - only the mandatory ones (and the CSR's, which the job fills in).

| Setting | Default | What it does |
|---|---|---|
| `cert_renew_servicenow` | `incident` | `catalog` = order the item, no incident |
| `cert_renew_catalog_item` | `""` | the item: its name as the catalog shows it, or its sys_id |
| `cert_renew_catalog_variables` | `{}` | the answers, by the questions' internal names |
| `cert_renew_catalog_csr_variable` | `""` | the attachment question for the CSR (empty = the only one) |
| `cert_renew_catalog_names_variable` | `""` | a question that gets the certificate's names |
| `cert_renew_catalog_reorder_states` | `["4", "7"]` | a request closed this way (Closed Incomplete, Closed Skipped) is ordered again |
| `cert_renew_catalog_upload_table` | `ZZ_YYsc_cart_item` | where the CSR is uploaded before the order (ServiceNow moves it to the request) |
| `servicenow_catalog_test_order` | `false` | the catalog test also orders once, with a test CSR |

## Step 3 - the catalog test

A job template: **ServiceNow - catalog test**, playbook `playbooks/servicenow_catalog_test.yml`, inventory
any, credential **ServiceNow API** (the account that will order). Variables:
[aap/extra_vars/servicenow_catalog_test.yml](../aap/extra_vars/servicenow_catalog_test.yml). Launch it:

```
PASS 1 item: DoD PKI Certificate Request (sys_id a3034a73...), 11 question(s), read as svc_aap_sn at https://...
     2 questions: requested_for | Requested For | requested for (31) | MANDATORY | a sys_user record: its name or sys_id
                  contact_method | Preferred method of contact | select box (5) | MANDATORY | choices: email = Email, phone = Phone
                  csr_file | Please add CSR | attachment (33) | MANDATORY | the CSR goes here
                  ...
PASS 3 settings: the order would send these answers (references as sys_ids), the CSR in csr_file ...
SKIP 4 order: servicenow_catalog_test_order is false
```

Steps 1-3 only read. A FAIL says what to change: the item not found (or not orderable by that account), a
question name that is not on the item, a mandatory question left empty, a choice it does not have, a
name with no record.

**One real test order** (optional): `servicenow_catalog_test_order: true`. It orders the item once, exactly as
the renewal does, with a test CSR (`CN=site-automation-test.invalid`, named `site-automation-TEST-...csr`).
**The PKI gets a real request** - tell them first, then cancel it. Run again, it finds that order and makes
no second one (until it is cancelled: Closed Skipped).

## Step 4 - the renewals

- Set `cert_renew_servicenow: catalog` (step 2). Nothing else changes on the renewal templates: the same
  playbooks, the same *ServiceNow API* credential.
- **Try it first** with Job type **Check** and a **Limit** of one host: the output says `would order it`, or
  `ordered already: RITM...`, and nothing is sent.
- **The workflows:** the certificate workflows run *Certificate report -> ServiceNow tickets -> Certificate
  renewal*. The *ServiceNow tickets* step opens incidents for the report's findings: with no incidents
  wanted, take that step out (link the report straight to the renewal, **Run always**).
- **The VMware and network renewals** make no incident either: the request is the CSR's record (vCenter, a
  host and a Cisco device keep one pending key - a second CSR would replace it).

## After the PKI issues the certificate

The request shows **Closed Complete** (each run says `the PKI issued it: install it`).

**Installed by the job, after an approval:** *Certificate install - check* (on a schedule) finds the certificate on
the request - whatever the PKI attached or pasted, as long as its public key is the CSR's - and says what installing
it would do; after the approval, *Certificate install - approved* puts it in place, reloads the service, checks the
port, and puts the old one back if anything is wrong. Linux and Windows: [CERT_INSTALL.md](CERT_INSTALL.md).

**By hand** - a keystore, a service that needs a restart, vCenter, ESXi, Cisco - where the key is:

| Where | How |
|---|---|
| Linux | the new key is in `cert_renew_dir` next to the CSR: [RUNBOOKS.md](RUNBOOKS.md), "Certificate renewal: the CSRs" |
| Windows | `certreq -accept -machine <issued.cer>` on the server: [WINDOWS.md](WINDOWS.md), "Windows certificate renewal - CSRs" |
| vCenter, ESXi | the vSphere Client's *Import and Replace*: [VMWARE.md](VMWARE.md), "Certificates of vCenter and the ESXi hosts" |
| Cisco | `crypto pki import <trustpoint> certificate`: [NETWORK_CERTS.md](NETWORK_CERTS.md), "The renewal" |

Once the new certificate is in place, it has another expiry date: nothing more is ordered until it is due.

## When it goes wrong

| The job says | Means | Do |
|---|---|---|
| `no catalog item named "..." that the API account can see` | the name differs, or the account may not order it | use the sys_id from the portal address; ask the catalog owner to make it available to the account |
| `the item has no question X (its questions: ...)` | an internal name that is not on the item | take the names from step 1 |
| `X (Label) is mandatory: give it a value` | a mandatory question without an answer | add it to `cert_renew_catalog_variables` |
| `'Fax' is not one of its choices (email = Email, ...)` | a select box answer it does not have | one of the listed values or labels |
| `X: no record named 'Building 9' in cmn_location` / `2 records named` | a reference by name: none, or several | the sys_id |
| `the order was refused: HTTP 400: ...` | ServiceNow's reason (a mandatory answer, a value it does not take, a rule of the item) | fix what it says; the catalog test shows the same |
| `ordered (REQ...), but its requested item could not be read` | the account cannot read the RITM it ordered | let it read its own requests; check the request by hand - the next run would order again |
| `ordered (RITM...), but the CSR is not on it` | ServiceNow refused the attachment on the RITM | attach the CSR by hand (the job output names it); allow `.csr` / `.txt` attachments |
| `the CSR could not be uploaded` | the instance refused the file (an extension it does not allow) | `cert_renew_attach_ext: txt` |
| The PKI says the CSR question is empty | the instance did not take the uploaded file for the question | the CSR is attached to the request (the paperclip): point them there; tell us the instance's version |

## Limits

- **Tested against a stand-in ServiceNow** (`tests/servicenow/snmock.py`): the Service Catalog API calls are
  the documented ones (`/api/sn_sc/servicecatalog/items`, `order_now`, the Attachment API), but how an
  instance handles an attachment question filled through the API differs between releases and
  customizations. Run the catalog test with one test order first.
- **A catalog item, not a record producer**: the job uses `order_now`. A record producer (it makes some other
  record, not a request) is not supported yet.
- **One request per certificate.** The form's paperclip takes more CSRs, but one request each keeps each
  certificate's order findable, and the PKI can fulfil them one by one.
