# PDFs

Printable versions of the guides, for reading offline or handing out. The markdown files in
`docs/` (and in ACT-Linux for the ACT guides) are the source of truth. The PDFs are rebuilt from
them at each release. Copy YAML from the repository files, not from a PDF: PDF copy can change
spaces.

| PDF | For | Built from |
|---|---|---|
| [Site-Automation-Ops-Runbooks-Guide.pdf](Site-Automation-Ops-Runbooks-Guide.pdf) | **Start here.** Setting up the runbooks in AAP, step by step: health checks, troubleshooting, ServiceNow, POA&M, patching, using your existing AAP inventory, workflows and schedules, adding ACT later (and picking its provider) | `docs/START_HERE.md`, `SETUP_AAP.md`, `USING_YOUR_AAP_INVENTORY.md`, `WORKFLOWS_AND_SCHEDULES.md`, `RUNBOOKS.md`, `ADDING_ACT.md`, `aap/credential_types/` |
| [Site-Automation-How-It-Fits-Together.pdf](Site-Automation-How-It-Fits-Together.pdf) | **Read with the setup guide.** How the pieces fit: code, inventory, credentials, surveys; how a setting travels from its YAML default to a host; groups (a host in several); updating the repository at work safely. Appendix: every setting you can change | `docs/HOW_IT_FITS_TOGETHER.md`, `roles/*/defaults/main.yml` |
| [Service-Watch-Demo-AAP-ACT.pdf](Service-Watch-Demo-AAP-ACT.pdf) | The service-watch demo: stop `nginx`/`stigman`, ACT finds the root cause, approve the fix | `docs/SERVICE_WATCH_DEMO.md` |
| [ACT-Automated-Triage-Guide.pdf](ACT-Automated-Triage-Guide.pdf) | What ACT is, how it works, setup, examples, AAP integration (ACT 0.6.18) | ACT-Linux docs |
| [ACT-AAP-2.7-Runbook.pdf](ACT-AAP-2.7-Runbook.pdf) | ACT's own playbooks on AAP 2.7, step by step | ACT-Linux `ansible/AAP_2.7_RUNBOOK.md` |
| [ACT-5Ws-Leadership-Brief.pdf](ACT-5Ws-Leadership-Brief.pdf) | Leadership brief: ACT with AAP (who, what, when, where, why) | - |
| [ACT-5Ws-Standalone-Brief.pdf](ACT-5Ws-Standalone-Brief.pdf) | Leadership brief: ACT on its own | - |

The PDFs carry no classification or distribution markings. Add the ones your site requires
before you distribute them.
