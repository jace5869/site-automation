# PDFs

Printable versions of the guides, for reading offline or handing out. The markdown files in
`docs/` (and in ACT-Linux for the ACT guides) are the source of truth. The PDFs are rebuilt from
them at each release. Copy YAML from the repository files, not from a PDF: PDF copy can change
spaces.

| PDF | For | Built from |
|---|---|---|
| [Site-Automation-Ops-Runbooks-Guide.pdf](Site-Automation-Ops-Runbooks-Guide.pdf) | **Start here.** Setting up the runbooks in AAP, step by step: health checks, troubleshooting, ServiceNow (with a test ticket), POA&M, patching, using your existing AAP inventory, workflows and schedules (and how ACT fits one), dry runs, adding ACT later (and picking its provider), Windows servers, VMware jobs (vCenter, with the pick lists; the VM deploy has its own guide, below), the VM outage watch, asking vCenter a question in plain words, log analysis with ACT, the NetApp ONTAP health report, the STIG drift report, the network performance report, network device certificates and configurations, the certificate inventory, certificate renewal through the PKI's catalog item and installing the issued certificate from its request (Linux and Windows, with an approval), emailed (formatted) reports and the report website (on a Windows server over WinRM, a folder or an upload), the MariaDB / MySQL check, the Active Directory health report and account lockout, the Horizon 8 (VDI) jobs | `docs/START_HERE.md`, `SETUP_AAP.md`, `USING_YOUR_AAP_INVENTORY.md`, `SERVICENOW_SETUP.md`, `WORKFLOWS_AND_SCHEDULES.md`, `DRY_RUNS.md`, `RUNBOOKS.md`, `ADDING_ACT.md`, `WINDOWS.md`, `VMWARE.md`, `VM_OUTAGE_WATCH.md`, `VM_ASK.md`, `LOG_ANALYSIS.md`, `NETAPP.md`, `NETWORK_CERTS.md`, `STIG_DRIFT.md`, `NETWORK_PERF.md`, `NETWORK_CONFIG.md`, `CERT_INVENTORY.md`, `CERT_RENEWAL_CATALOG.md`, `CERT_INSTALL.md`, `EMAIL_REPORTS.md`, `WEB_REPORTS.md`, `SECRETS.md`, `APPROVED_COMMANDS.md`, `MARIADB.md`, `ACTIVE_DIRECTORY.md`, `HORIZON.md`, `aap/credential_types/` |
| [Site-Automation-Settings-Reference.pdf](Site-Automation-Settings-Reference.pdf) | **Read next to the setup guide.** Every setting of every job: its default, what it does, where to put it and which place wins | `docs/VARIABLES.md`, `docs/VARIABLES_REFERENCE.md` (generated from `roles/*/defaults/main.yml`) |
| [Site-Automation-VM-Deploy-Guide.pdf](Site-Automation-VM-Deploy-Guide.pdf) | **The VM deploy job, complete.** What it does and the standard setup to start with; setting it up in AAP step by step (the execution environment, the vCenter role and its privileges, the credentials, the settings finder, the job template and its survey, the first runs, the approval workflow, the scheduled pickup, the server's service and its AAP inventory, launching from AAP's API); how a run works (which ticket number to launch with, what closes the task and what never closes the change, where the notes go, several VMs, a run again); what the requester writes; where the VM goes; ready-made configurations; every message with what to do; where the logic lives - and every `vm_deploy_*` setting with its default, its whole comment, an example and when to set it | `docs/VM_DEPLOY_GUIDE.md`, `roles/vmware_vm/defaults/main.yml`, `docs/vm_deploy_examples.yml` |
| [Site-Automation-EE-Guide.pdf](Site-Automation-EE-Guide.pdf) | **Execution environments, step by step.** Three EEs, each built on your base EE (`base-ee:1.0`): Windows, AD and SQL; Network (Cisco and Palo Alto); and the all-in-one for every site-automation job - the build machine, your hub and its token, your CAs, building each with ansible-builder, checking it (`ee-check`), adding it to AAP, a first job, AD as an inventory, keeping them up to date, troubleshooting - with the build files | `docs/EXECUTION_ENVIRONMENT.md`, `ee/` |
| [Site-Automation-How-It-Fits-Together.pdf](Site-Automation-How-It-Fits-Together.pdf) | **Read with the setup guide.** How the pieces fit: code, inventory, credentials, surveys; how a setting travels from its YAML default to a host; groups (a host in several); updating the repository at work safely. Appendix: your settings files | `docs/HOW_IT_FITS_TOGETHER.md`, `playbooks/group_vars/` |
| [Site-Automation-Report-Share-Guide.pdf](Site-Automation-Report-Share-Guide.pdf) | **The reports on a file share (SMB).** Every report job's page written straight onto a Windows file share, no mount: the folder and its permissions, the service account, the execution environment, the network path, the credential, the settings, a test, your reports page, and what each message means | `docs/REPORT_SHARE.md` |
| [Service-Watch-Demo-AAP-ACT.pdf](Service-Watch-Demo-AAP-ACT.pdf) | The service-watch demo: stop `nginx`/`stigman`, ACT finds the root cause, approve the fix | `docs/SERVICE_WATCH_DEMO.md` |
| [ACT-Automated-Triage-Guide.pdf](ACT-Automated-Triage-Guide.pdf) | What ACT is, how it works, setup, examples, AAP integration (ACT version: see the PDF's first page) | ACT-Linux docs |
| [ACT-AAP-2.7-Runbook.pdf](ACT-AAP-2.7-Runbook.pdf) | ACT's own playbooks on AAP 2.7, step by step | ACT-Linux `docs/AAP_2.7_RUNBOOK.md` |
| [ACT-5Ws-Leadership-Brief.pdf](ACT-5Ws-Leadership-Brief.pdf) | Leadership brief: ACT with AAP (who, what, when, where, why) | - |
| [ACT-5Ws-Standalone-Brief.pdf](ACT-5Ws-Standalone-Brief.pdf) | Leadership brief: ACT on its own | - |

**Versions.** All eleven PDFs are for this release (site-automation 0.45.2, ACT 0.6.25) and share one
look. The four ACT PDFs say GenAI.mil only (Ask Sage is retired for now), what changed in ACT 0.6.22 to
0.6.25, how site-automation's jobs use ACT (the Triage Guide, section 4.4), and the podman health job's
containers found by themselves (the AAP 2.7 Runbook, D1).

The PDFs carry no classification or distribution markings. Add the ones your site requires
before you distribute them.

## Your logo on them

The PDFs here carry no logo. To put your site's logo on the cover (the 5W's briefs: in their band) and at
the top of every page of all eleven, build them again (see below) with `SITE_PDF_LOGO` set to the logo: a PNG with a
transparent background, or a JPEG. The report logo works (`playbooks/files/report_logo.png`); a copy 300 px tall
or more prints sharper. Keep the copies with your logo in your own repository or on a share - never in the public
repository.

```bash
cd docs/pdf
SITE_PDF_LOGO=../../playbooks/files/report_logo.png python3 build_ops_guide.py Site-Automation-Ops-Runbooks-Guide.pdf
# all eleven (the runbook needs ACT_LINUX_DIR, below):
export SITE_PDF_LOGO=../../playbooks/files/report_logo.png
for b in "ops_guide Site-Automation-Ops-Runbooks-Guide" "how_it_fits Site-Automation-How-It-Fits-Together" \
         "service_watch Service-Watch-Demo-AAP-ACT" "settings_reference Site-Automation-Settings-Reference" \
         "vm_deploy_guide Site-Automation-VM-Deploy-Guide" "ee_guide Site-Automation-EE-Guide" \
         "report_share Site-Automation-Report-Share-Guide" \
         "guide ACT-Automated-Triage-Guide" "runbook ACT-AAP-2.7-Runbook" \
         "5ws ACT-5Ws-Leadership-Brief" "5ws_act ACT-5Ws-Standalone-Brief"; do
  set -- $b; python3 build_$1.py $2.pdf
done
```

```powershell
cd docs\pdf
$env:SITE_PDF_LOGO = '..\..\playbooks\files\report_logo.png'
python build_ops_guide.py Site-Automation-Ops-Runbooks-Guide.pdf
```

## Rebuilding them

The scripts that draw the PDFs are in this folder (`build_*.py`, with `actpdf.py` for the shared
parts and `sapdf.py` for the look of all of them). They need Python 3, the
`reportlab` package and the **Noto Sans** and **Noto Sans Mono**
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
python3 build_report_share.py Site-Automation-Report-Share-Guide.pdf
python3 build_guide.py ACT-Automated-Triage-Guide.pdf
ACT_LINUX_DIR=/opt/ACT python3 build_runbook.py ACT-AAP-2.7-Runbook.pdf
python3 build_5ws.py ACT-5Ws-Leadership-Brief.pdf
python3 build_5ws_act.py ACT-5Ws-Standalone-Brief.pdf
```

The site-automation PDFs find the repository from where the script sits (or from
`SITE_AUTOMATION_DIR`). The ACT ones (`build_guide.py`, `build_5ws*.py`) hold their text in the
script; `build_runbook.py` reads `docs/AAP_2.7_RUNBOOK.md` from an ACT-Linux checkout: set
`ACT_LINUX_DIR` to it (default `/opt/ACT`). The version on a cover
(`SA_VERSION` in `build_ops_guide.py`, `VERSION` in `actpdf.py` for ACT) is edited by hand at each
release. To check a PDF: open it, look at the contents page and at any page with a table or code.

## Workflow diagrams

Draw a workflow in a doc top to bottom, in a block marked `workflow`. It reads as text on screen and
the setup guide draws it as boxes and arrows (green on success, red on fail, blue always):

```workflow
[Certificate install - check]  on a schedule
   +-- Run always -->  [ServiceNow tickets]
   |  Run on fail
   v
[Approval]  "Install these?"
   |  Run on success
   v
[Certificate install - approved]
```

A step is `[the job template's name]`, then two spaces and what to answer (`;` between lines). A
branch to the right, `+-- link -->  [step]`, comes straight after its step (one per step). A blank
line starts another chain: two or three of them are drawn side by side. Keep every line within 88
characters (`tests/test_docs.py` checks it), so nothing wraps in the PDF or scrolls on a screen.
A block the PDF cannot draw is printed as the text it is.
