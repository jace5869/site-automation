# Enclaves: setting up another enclave's AAP

The same repository runs in each enclave. **Each enclave has its own AAP**, built on its own: you import the
settings there (the project, `playbooks/group_vars/all.yml` and the group files, the job templates from
`aap/extra_vars`) and then change, by hand, what is different in that enclave. Nothing in a job template has to
change: what differs between enclaves lives in that AAP's credentials and in its `all.yml`.

This chapter is the list of what to change. Go through it top to bottom on the new AAP, then run
*AAP - template variables audit* ([TEMPLATE_AUDIT.md](TEMPLATE_AUDIT.md)): it names any address, path or secret still
sitting in a template's Variables, where it would quietly win over `all.yml`.

The examples use placeholders only: `ENCLAVE-A` / `ENCLAVE-B`, `yoursite.mil`, `classified.example.mil`. Never put
an enclave's real network name or domain in the repository.

## 1. Which enclave this AAP serves, and the banner

In `all.yml`:

```yaml
site_enclave: classified            # or unclassified; empty = found from the job's hosts (below)
report_banner_text: SECRET          # the marking, e.g. SECRET, SECRET//NOFORN, TOP SECRET, CUI, UNCLASSIFIED
```

| Setting | Default | What it does |
|---|---|---|
| `site_enclave` | `""` | `classified` or `unclassified`. Empty: classified when a target host's name or address (`inventory_hostname`, `ansible_host`) ends in one of `site_enclave_classified_domains`, else unclassified. When set, it always wins |
| `site_enclave_classified_domains` | `[]` | the classified enclave's DNS suffixes, e.g. `[classified.example.mil]` - a suffix, any case (`web01.CLASSIFIED.example.mil` matches; `notclassified.example.mil` does not). One classified host makes the report classified |
| `report_banner` | `auto` | the classification banner on every report: `auto` = on in the classified enclave, off in the unclassified; `true` / `false` = always / never |
| `report_banner_text` | `SECRET` | the banner's text |
| `report_banner_color` | `""` | the banner's colour as `#rrggbb`; empty = the standard colour of the text's classification (below) |
| `report_banner_subject` | `true` | with the banner on: `(SECRET) ` before the email subject (before `report_email_subject_prefix`) |

The banner's colour follows the classification the text starts with - **UNCLASSIFIED** green (`#007a33`), **CUI**
purple (`#502b85`), **CONFIDENTIAL** blue (`#0033a0`), **SECRET** red (`#c8102e`), **TOP SECRET** orange
(`#ff8c00`) - with the text in white or near-black, whichever reads 4.5:1 or better. A text of no known
classification is dark grey unless `report_banner_color` says otherwise.

Where the banner goes, top and bottom:

- every email look (standard, command, portal, glass), and the plain text (its first and last line);
- every report website page and the index, the ticket insights pages (the site's and each team's), and the pages
  the job *Report website - copy* writes (the banner is kept with the report);
- in print: at the top and bottom of every sheet;
- the first row of each sheet of the workbooks (`report_email_xlsx`, and the reports' own), across the table, the
  column names under it.

Each job says which it used, in one line: `Banner SECRET (site_enclave: classified)` or
`No banner (unclassified: no target host under site_enclave_classified_domains)`.

The outage AAR and SOP (Word, [INCIDENT_DOCS.md](INCIDENT_DOCS.md)) follow the banner: `incident_docs_banner: auto`
(the default) is the report banner's text and colour when it is on - SECRET on the classified side without setting it
twice - else the CUI marking; your own `incident_docs_banner` still wins. STIG Manager's (`stigman_classification`) and
VMware ask's page (`vmware_ask_classification`) keep their own markings - set them too (section 4).

## 2. ACT (GenAI)

An enclave without a model endpoint - or one that loses it - turns ACT off with `site_act_enabled` (default `true`):

```yaml
site_act_enabled: false
```

Then **no job asks ACT**: no request, no model address or key needed, no error. Each report that would have had
ACT's part says so in one line - *ACT is off (site_act_enabled false).* - and the job stays green:

- the runbooks with `use_act=true` (Health check, Database health, Troubleshoot): the findings, without ACT;
- the analyses (VMware alarms, capacity, right-sizing, log analysis, ticket insights, the ticket review, incident
  documents, fapolicyd): the report without ACT's part, the job not failed for it;
- *VM - ask a question*: the job's own reader takes the simple questions; one it cannot take ends green with one
  line ("Not answered: ACT is off ...");
- *VMware ask* (the page): the job's own reader only;
- *Service watch*: the standard fix, as when ACT cannot run;
- the VM deploy with `vm_deploy_genai`: the ticket's own `key: value` lines, as with `vm_deploy_genai: false`;
- *fapolicyd denials* (ACT's own playbook): nothing read, nothing asked - *fapolicyd* reads the denials without ACT.

`tests/test_act_switch.py` walks every place that calls ACT, so a job added later cannot miss the setting.

An enclave **with** a model: its own provider, models and addresses -

| Setting | What to change |
|---|---|
| `site_act_provider` | the provider there (`genai`, `genai-beta`) |
| `site_act_models` | the model for each provider there |
| `site_act_urls` | the API address for each provider there (ACT's defaults are the unclassified enclave's) |
| `site_act_ca` | the CA file that signed the model's certificate |
| `site_act_env` | a proxy there (`HTTPS_PROXY`), if one is needed |
| the **ACT model key** credential | that enclave's key |

## 3. The credentials

Every login is an AAP credential ([SECRETS.md](SECRETS.md) lists each type and what it carries), so each enclave's
AAP has its own: create each one again there with that enclave's account - never copy one from the other AAP.
Most addresses come with them, so they need no setting:

| Credential | Carries the address of |
|---|---|
| Machine (SSH), Machine (WinRM) | - (the hosts are in the inventory) |
| VMware vCenter | the vCenter (`VMWARE_HOST`) |
| ServiceNow API | the instance (`SN_HOST`) |
| STIG Manager API | Keycloak's token address (`STIGMAN_TOKEN_URL`) |
| Registry | the registry (`REGISTRY_HOST`) |
| PAN-OS, IdM, Red Hat Ansible Automation Platform | the firewall, the IdM server (`IDM_SERVER`), AAP itself |
| Report website, mail relay login, MariaDB monitor, ACT model key, Teams webhook | - |

## 4. Paths, addresses and hosts in `all.yml` (and the group files)

What a job reaches by its address, its files and what it trusts. Each is empty or a placeholder by default unless the
table says otherwise; change each that the enclave uses.

**Mail and the report website** ([EMAIL_REPORTS.md](EMAIL_REPORTS.md), [WEB_REPORTS.md](WEB_REPORTS.md),
[REPORT_SHARE.md](REPORT_SHARE.md)):

| Setting | What to change |
|---|---|
| `report_email_smtp_host`, `report_email_smtp_port`, `report_email_ca_path` | that enclave's mail relay and its CA |
| `report_email_from` | an address that relay takes |
| `report_web_dest`, `report_web_url`, `report_web_host`, `report_web_ca_path` | that enclave's report website: the folder or share, its address, the Windows server |
| `report_logo_file`, `report_email_header_text` | the logo and the line with it |

**Trust and certificates**:

| Setting | What to change |
|---|---|
| `pki_bundle_url`, `pki_bundle_proxy`, `pki_bundle_ca_path` | the CA bundle's address there (the default is the unclassified bundle on the internet) |
| `aap_health_trust_cas` | the root CAs the AAP host must trust there |
| every `*_ca_path` (`servicenow_ca_path`, `stigman_health_ca_path`, `ucs_ca_path`, `ontap_ca_path`, `horizon_ca_path`, `tenable_ca_path`, `aap_ee_ca_path`, `net_config_panos_ca_path`) | the CA files there - same names, that enclave's files |
| `vmware_validate_certs` | `true` once the execution environment trusts that vCenter's CA |

**What the jobs reach**:

| Setting | What to change |
|---|---|
| `check_network_dns_names`, `check_network_tcp` | the names and ports every host must reach there (directory, ServiceNow, the log server) |
| `vmware_vcenters`, `vm_hostnet_targets` | the vCenters, and what the hosts' networks must reach |
| `vm_deploy_templates`, `vm_deploy_clusters`, `vm_deploy_datastores`, `vm_deploy_folders`, `vm_deploy_domain` | that vCenter's objects and the domain of new VMs ([VM_DEPLOY_GUIDE.md](VM_DEPLOY_GUIDE.md)) |
| `ontap_clusters`, `ucs_domains` | the storage clusters and UCS domains |
| `check_stig_stigman_api`, `check_stig_stigman_token_url`, `poam_stigman_api`, `stigman_health_api`, `stigman_health_token_url`, `stigman_oidc_provider` | STIG Manager and its Keycloak there |
| `check_stig_evaluate_answers` | the share with Evaluate-STIG's answer files |
| `stigman_images`, `vmware_ask_image` | the images from that enclave's registry or hub |
| `net_perf_targets`, `net_perf_ocsp`, `net_perf_iperf_pairs`, `tenable_scan_targets`, `net_cert_subnets` | the addresses to measure and scan there |
| `net_config_git_repo`, `net_config_share_host`, `cert_inventory_sql_server` | the repository, share and SQL Server there |

**Markings of their own** (section 1): `stigman_classification`, `vmware_ask_classification` (`incident_docs_banner`
follows the banner).

**Outside `all.yml`**:

- the inventories: the hosts, and an AD inventory source's `search_base` and `server` (the domain's own);
- each execution environment's `ansible.cfg` (copied from `ansible.cfg.example`): that enclave's hub
  ([EXECUTION_ENVIRONMENT.md](EXECUTION_ENVIRONMENT.md));
- the project's source control address in AAP.

## 5. Check it

1. *AAP - template variables audit* ([TEMPLATE_AUDIT.md](TEMPLATE_AUDIT.md)): nothing in a template's Variables
   that belongs in `all.yml` or a credential.
2. A report job with an email - say *Health check* on one host: the banner (or none) as section 1 says, the
   mail through that relay, the page on that website.
3. With ACT on: a job with ACT (*VM - ask a question*); with it off: the same job ends green with
   *ACT is off (site_act_enabled false)*.
