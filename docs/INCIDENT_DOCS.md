# Outage AAR and SOP - Word documents

Two jobs turn what someone types in an AAP survey into Word documents with your logo and marking banner, and email
them to the addresses typed in the same survey:

- **Outage AAR - Word document** (`playbooks/incident_aar.yml`): the after action report - overview (start, impact),
  event response (detection, actions), reason for outage and recovery, lessons learned and action plan (successes,
  improvements, tasks), then your own instructions. **Also make an SOP: Yes** attaches an SOP from the steps typed in
  the same survey.
- **SOP - Word document** (`playbooks/incident_sop.yml`): an SOP on its own - title, purpose and when to use, steps to
  troubleshoot, steps to fix, how to check it is fixed, escalation, revision history.

Optional, each a Yes/No in the survey (both **No** unless chosen): **ACT: tidy the wording** - GenAI fixes spelling
and wording; facts, names and numbers stay as typed - and **ACT: suggest what may be missing** - GenAI adds a section
of its own, **From ACT**, with checks or steps that may be missing or better, for you to review. What was typed is
never changed by a suggestion.

The documents are **emailed only**: never on the report website, and only to the addresses typed in the survey
(`report_email_also`) and `incident_docs_email_to` - **not** to the reports' usual recipients (`report_email_to`,
`report_email_cc`). When `report_email_also_domains` is set, an address outside it gets nothing (the job says so).
The job runs on the AAP side: no inventory of servers, no Limit, no machine credential.

## What the documents look like

**The AAR** (`AAR.docx`, or `AAR-<outage>.docx` when the outage is named): your logo at the top left, the marking
banner (`incident_docs_banner`, e.g. `CONTROLLED UNCLASSIFIED INFORMATION (CUI)`) at the top and bottom of every
page - in bold, in the marking's usual colour: CUI purple, UNCLASSIFIED green, CONFIDENTIAL blue, SECRET red, TOP SECRET
orange (`incident_docs_banner_color`: your own, hex in quotes) - then:

| Heading | Lines |
|---|---|
| **OVERVIEW** | Outage (when named), Incident Date/Time (`Start: ...  End: ...`), Impact (`The operational impact is MAJOR because it is affecting users enterprise-wide.`) |
| **Event Response** | Detection, Actions |
| **Reason for Outage (RFO) & Recovery** | Root Cause, Recovery |
| **Lessons Learned & Action Plan** | Successes, Improvements, Tasks/Responsibility; SOP (its file name, when one is attached) |
| **From ACT - suggestions to review** | only with *ACT: suggest*: each suggestion, what it is for and why |
| *Instructions:* | `incident_aar_instructions`, numbered, in italics |

A box of one line follows its label (`Root Cause: A patch ...`); a box of several lines becomes bullets under its
label, one per line. Bullets or numbers typed at the start of a line (`- `, `* `, `1. `, `Step 2:`) are taken off -
the document has its own. An empty box says *Not given.* The text is black: the red of a paper template only marks
what to fill in.

**The SOP** (`SOP-<title>.docx`): the logo and banner, *Standard Operating Procedure* and its title, a table (SOP, from
outage, date, written by - the AAP user who launched it -, status: `incident_sop_status`), then **Purpose and when to
use**, **Troubleshooting steps** and **Fix steps** (numbered, each list from 1), **Check it is fixed** and
**Escalation** (when typed), **From ACT - steps to consider** (with *ACT: suggest*), and **Revision history** (1.0,
the date, who, *First version - from outage ...*). An SOP made with an AAR with no purpose typed says: *Use it when
this outage's symptoms are seen again. Its root cause was: ...*

The email itself names the attachments, says what ACT did, and holds none of the documents' text.

## Set it up

1. **Your logo, banner and instructions** - in **your** `playbooks/group_vars/all.yml` (never the public repository):
   ```yaml
   report_logo_file: files/report_logo.png          # the reports' logo is used; incident_docs_logo_file for another
   incident_docs_banner: CONTROLLED UNCLASSIFIED INFORMATION (CUI)
   incident_aar_instructions:
     - Save this file as AAR.docx.
     - Send the AAR to your manager for review and approval before it is posted.
     - Attach this file, logs and screenshots to the outage's record.
   incident_impact_levels: [MINOR, MODERATE, MAJOR, CRITICAL]
   ```
   The mail relay is the reports' (`report_email_smtp_host`, `report_email_from`: [EMAIL_REPORTS.md](EMAIL_REPORTS.md)).
2. **Two job templates.** *Outage AAR - Word document*: playbook `playbooks/incident_aar.yml`; *SOP - Word document*:
   playbook `playbooks/incident_sop.yml`. Any inventory (`localhost` is enough), no Limit, any execution environment
   with Python 3. Credentials: none - or the **ACT model key** credential, only if the ACT options are to work.
3. **Their surveys:** a third template, *AAP - incident document surveys*: playbook `playbooks/incident_docs_survey.yml`,
   a **Red Hat Ansible Automation Platform** credential (its user: the Admin role on the two templates), no Limit.
   Launch it with **Check** first - it says what it would add - then without. It finds the two templates by their
   playbook (your names do not matter), adds the questions below in order, and turns the surveys on. A question
   already there (its variable) is left as it is, so your own wording, defaults and choices stay; running it again
   changes nothing. `incident_docs_survey_state: absent` takes the questions out again.
4. **Launch** *Outage AAR - Word document*, fill in the survey, and check the email.

### The AAR job's survey

| Question | Variable | Type |
|---|---|---|
| Outage (name or number) | `incident_name` | Text, optional |
| Start date and time | `incident_start` | Text, required |
| End date and time | `incident_end` | Text, optional |
| Impact level | `incident_impact_level` | Multiple choice, required: `incident_impact_levels` |
| Impact - because | `incident_impact` | Textarea |
| Detection | `incident_detection` | Textarea |
| Actions | `incident_actions` | Textarea, one per line |
| Root cause | `incident_root_cause` | Textarea |
| Recovery | `incident_recovery` | Textarea |
| Successes | `incident_successes` | Textarea, one per line |
| Improvements | `incident_improvements` | Textarea, one per line |
| Tasks / responsibility | `incident_tasks` | Textarea, one per line (*task - who*) |
| Email the documents to | `report_email_also` | Textarea, required: addresses separated by commas, semicolons or lines |
| Also make an SOP | `incident_make_sop` | Multiple choice No / Yes |
| SOP: title, purpose and when to use, steps to troubleshoot, steps to fix, check it is fixed, escalation | `sop_title`, `sop_purpose`, `sop_troubleshoot`, `sop_fix`, `sop_verify`, `sop_escalate` | Text, then Textareas (steps one per line) - read only when *Also make an SOP* is Yes |
| ACT: tidy the wording | `incident_act_tidy` | Multiple choice No / Yes |
| ACT: suggest what may be missing | `incident_act_suggest` | Multiple choice No / Yes |

The SOP job's survey: *From outage (optional)*, the six SOP questions (the title required), *Email the documents to*,
and the two ACT questions. A text box takes up to `incident_docs_survey_max` characters (8000; AAP's own default is
1024, which would cut a long list of steps).

**Also make an SOP: Yes** with both step boxes empty: the AAR is still made and sent, without an SOP, and the email
says why - launch again (*Relaunch* keeps the other answers) with the steps.

## ACT - tidy and suggest

ACT runs in **evidence mode**: it runs no command; it only reads what was typed (sent as data, never followed as
instructions) and answers once. It needs the **ACT model key** credential on the template ([SECRETS.md](SECRETS.md)),
and the AAP node must reach the model's address, as for the other ACT jobs. A dry run (Check) does not ask it.

- **Tidy:** each box comes back with the spelling and wording fixed. The job keeps ACT's wording for a box **only**
  when it keeps everything with a digit in it (addresses, CVE and ticket numbers, times, versions), has no masked word,
  keeps the number of steps, and is not much longer than what was typed; otherwise that box stays as typed, and the
  email says which and why. A line at the end of the document says the wording was tidied by GenAI.
- **Suggest:** up to `incident_act_max_suggestions` short suggestions - a check or a step that may be missing, a safer
  order, a way to confirm the fix, a lasting fix - each with what it is for and why, in **From ACT** sections: the
  AAR's for the AAR's boxes, the SOP's for its steps (no SOP: all in the AAR). Nothing is added to what was typed.
- ACT not reachable, no key, or an answer that cannot be read: the documents are made from the text as typed, and the
  email says *ACT was not used:* with the reason. The job does not fail for it.

`incident_act_extra_instructions` gives ACT your own words (*Our monitoring tool is called X; tickets start with
INC.*).

## Later: filled from the outage record

Every value the documents use comes from one record the job makes from the survey (`incident_record` in
`plugins/filter/incident_doc_filters.py`). A later version can fill the same record from the outage's record in
ServiceNow - start, impact, description - so only the lessons learned are typed; the documents, ACT and the email stay
as they are.

## Settings

Every setting and its default: [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md#incident_docs). The main ones:
`incident_docs_banner`, `incident_docs_banner_color`, `incident_docs_logo_file` (empty: `report_logo_file`), `incident_docs_logo_height`,
`incident_docs_font`, `incident_docs_font_size`, `incident_docs_heading_color`, `incident_aar_instructions`,
`incident_sop_instructions`, `incident_sop_status`, `incident_docs_aar_file`, `incident_docs_sop_file`,
`incident_docs_email_to`, `incident_impact_levels`.

## Troubleshooting

| What you see | Why, and what to do |
|---|---|
| `Not made: "Email the documents to" (report_email_also) is empty` | type one or more addresses; an address outside `report_email_also_domains` is refused (listed in the message) |
| `Not made: the outage's start date and time ... is empty` / `the SOP has no steps` | the required boxes; launch again |
| No logo in the document; the job says `the logo ... is not there, or not a PNG, JPEG or GIF` | `report_logo_file` (or `incident_docs_logo_file`) must be a PNG, JPEG or GIF in the project, e.g. `files/report_logo.png` = `playbooks/files/report_logo.png` |
| A long list of steps is cut off | the question's *Maximum length* in the survey: run *AAP - incident document surveys* on a new template, or raise it by hand (AAP's default is 1024) |
| A question still has the old wording after an update | the setup job leaves questions that are there as they are: delete the question in the survey, and run it again |
| `ACT was not used: This job has no API key ...` | attach the **ACT model key** credential to the template |
| Word opens the attachment in *Protected View* | as for any emailed file: *Enable Editing* |
| `AAP refused the survey (HTTP 400)` | the AAP user needs the Admin role on the template; the message has AAP's reason |
