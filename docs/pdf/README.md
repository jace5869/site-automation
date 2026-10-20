# PDFs

Printable versions of the guides, for reading offline or handing out. The markdown files in
`docs/` (and in ACT-Linux for the ACT guides) are the source of truth. The PDFs are rebuilt from
them at each release. Copy YAML from the repository files, not from a PDF: PDF copy can change
spaces.

| PDF | For | Built from |
|---|---|---|
| [Site-Automation-Ops-Runbooks-Guide.pdf](Site-Automation-Ops-Runbooks-Guide.pdf) | **Start here.** Setting up the runbooks in AAP, step by step: health checks, troubleshooting, ServiceNow (with a test ticket), POA&M, patching, using your existing AAP inventory, workflows and schedules (and how ACT fits one), dry runs, adding ACT later (and picking its provider), Windows servers, VMware jobs (vCenter; the VM deploy has its own guide, below), the NetApp ONTAP health report, network device certificates, the certificate inventory and certificate renewal through the PKI's catalog item, emailed (formatted) reports, the MariaDB / MySQL check, the Active Directory health report | `docs/START_HERE.md`, `SETUP_AAP.md`, `USING_YOUR_AAP_INVENTORY.md`, `SERVICENOW_SETUP.md`, `WORKFLOWS_AND_SCHEDULES.md`, `DRY_RUNS.md`, `RUNBOOKS.md`, `ADDING_ACT.md`, `WINDOWS.md`, `VMWARE.md`, `NETAPP.md`, `NETWORK_CERTS.md`, `CERT_INVENTORY.md`, `CERT_RENEWAL_CATALOG.md`, `EMAIL_REPORTS.md`, `SECRETS.md`, `APPROVED_COMMANDS.md`, `MARIADB.md`, `ACTIVE_DIRECTORY.md`, `aap/credential_types/` |
| [Site-Automation-Settings-Reference.pdf](Site-Automation-Settings-Reference.pdf) | **Read next to the setup guide.** Every setting of every job: its default, what it does, where to put it and which place wins | `docs/VARIABLES.md`, `docs/VARIABLES_REFERENCE.md` (generated from `roles/*/defaults/main.yml`) |
| [Site-Automation-VM-Deploy-Guide.pdf](Site-Automation-VM-Deploy-Guide.pdf) | **The VM deploy job, complete.** What it does and the standard setup to start with; setting it up in AAP step by step (the execution environment, the vCenter role and its privileges, the credentials, the settings finder, the job template and its survey, the first runs, the approval workflow, the scheduled pickup, the server's service and its AAP inventory, launching from AAP's API); how a run works (which ticket number to launch with, what closes the task and what never closes the change, where the notes go, several VMs, a run again); what the requester writes; where the VM goes; ready-made configurations; every message with what to do; where the logic lives - and every `vm_deploy_*` setting with its default, its whole comment, an example and when to set it | `docs/VM_DEPLOY_GUIDE.md`, `roles/vmware_vm/defaults/main.yml`, `docs/vm_deploy_examples.yml` |
| [Site-Automation-EE-Guide.pdf](Site-Automation-EE-Guide.pdf) | **The all-in-one execution environment.** One EE for Linux, Windows and Active Directory, VMware, NetApp, MySQL / MariaDB / SQL Server, Cisco and ServiceNow - and every site-automation job: what is in it, building it with ansible-builder, checking it, adding it to AAP, Active Directory from it (AD as an inventory), a first task for databases, Cisco and ServiceNow, troubleshooting - with the build files | `docs/EXECUTION_ENVIRONMENT.md`, `ee/all-in-one/` |
| [Site-Automation-How-It-Fits-Together.pdf](Site-Automation-How-It-Fits-Together.pdf) | **Read with the setup guide.** How the pieces fit: code, inventory, credentials, surveys; how a setting travels from its YAML default to a host; groups (a host in several); updating the repository at work safely. Appendix: your settings files | `docs/HOW_IT_FITS_TOGETHER.md`, `playbooks/group_vars/` |
| [Service-Watch-Demo-AAP-ACT.pdf](Service-Watch-Demo-AAP-ACT.pdf) | The service-watch demo: stop `nginx`/`stigman`, ACT finds the root cause, approve the fix | `docs/SERVICE_WATCH_DEMO.md` |
| [ACT-Automated-Triage-Guide.pdf](ACT-Automated-Triage-Guide.pdf) | What ACT is, how it works, setup, examples, AAP integration (ACT version: see the PDF's first page) | ACT-Linux docs |
| [ACT-AAP-2.7-Runbook.pdf](ACT-AAP-2.7-Runbook.pdf) | ACT's own playbooks on AAP 2.7, step by step | ACT-Linux `docs/AAP_2.7_RUNBOOK.md` |
| [ACT-5Ws-Leadership-Brief.pdf](ACT-5Ws-Leadership-Brief.pdf) | Leadership brief: ACT with AAP (who, what, when, where, why) | - |
| [ACT-5Ws-Standalone-Brief.pdf](ACT-5Ws-Standalone-Brief.pdf) | Leadership brief: ACT on its own | - |

**Versions.** The six site-automation PDFs are for this release (site-automation 0.16.1, ACT
0.6.24). The four ACT PDFs were made for ACT 0.6.18 (two of them were corrected for 0.6.21: how
the key reaches a host); for what
changed since (both endpoint formats and `:probe`, the danger tier under `--auto`, https-only
keys), see [ADDING_ACT.md](../ADDING_ACT.md), [APPROVED_COMMANDS.md](../APPROVED_COMMANDS.md) and the
`CHANGELOG.md`.

The PDFs carry no classification or distribution markings. Add the ones your site requires
before you distribute them.

## Rebuilding them

The scripts that draw the PDFs are in this folder (`build_*.py`, with `actpdf.py` for the shared
look). They need Python 3, the `reportlab` package and the **Noto Sans** and **Noto Sans Mono**
fonts (Fedora/RHEL: `dnf install google-noto-sans-fonts google-noto-sans-mono-fonts`; elsewhere set
`NOTO_FONT_DIR` to the folder that holds `NotoSans-Regular.ttf`). They need no network.

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install reportlab
cd docs/pdf
python3 build_ops_guide.py Site-Automation-Ops-Runbooks-Guide.pdf   # the setup guide
python3 build_how_it_fits.py Site-Automation-How-It-Fits-Together.pdf
python3 build_service_watch.py Service-Watch-Demo-AAP-ACT.pdf
python3 build_settings_reference.py Site-Automation-Settings-Reference.pdf
python3 build_vm_deploy_guide.py Site-Automation-VM-Deploy-Guide.pdf
python3 build_ee_guide.py Site-Automation-EE-Guide.pdf
```

The site-automation PDFs find the repository from where the script sits (or from
`SITE_AUTOMATION_DIR`). The ACT ones (`build_guide.py`, `build_5ws*.py`) hold their text in the
script; `build_runbook.py` reads `docs/AAP_2.7_RUNBOOK.md` from an ACT-Linux checkout: set
`ACT_LINUX_DIR` to it (default `/opt/ACT`). The version on a cover
(`SA_VERSION` in `build_ops_guide.py`, `VERSION` in `actpdf.py` for ACT) is edited by hand at each
release. To check a PDF: open it, look at the contents page and at any page with a table or code.
