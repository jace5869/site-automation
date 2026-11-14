# Outage AAR and SOP - Word documents

Two jobs turn what someone types in an AAP survey into Word documents with your logo and marking banner, and email
them to the addresses typed in the same survey:

- **Outage AAR - Word document** (`playbooks/incident_aar.yml`): the after action report - overview (start, impact),
  event response (detection, actions), reason for outage and recovery, lessons learned and action plan (successes,
  improvements, tasks), then your own instructions. **Also make an SOP: Yes** attaches an SOP made from the AAR: its
  Actions become the steps to troubleshoot, its Recovery the steps to fix - or the SOP's own boxes, where typed
  ([The SOP made from the AAR](#the-sop-made-from-the-aar)).
- **SOP - Word document** (`playbooks/incident_sop.yml`): an SOP on its own - title, purpose and when to use, steps to
  troubleshoot, steps to fix, how to check it is fixed, escalation, revision history.

Optional, each a Yes/No in the survey (all **No** unless chosen): **ACT: tidy the wording** - GenAI fixes spelling
and wording; facts, names and numbers stay as typed - **ACT: suggest what may be missing** - GenAI adds a section
of its own, **From ACT**, with checks or steps that may be missing or better, for you to review - and, in the AAR job,
**ACT: write the SOP's steps from the AAR**. What was typed is never changed by a suggestion.

The documents are **emailed only**: never on the report website, and only to the addresses typed in the survey
(`report_email_also`) and `incident_docs_email_to` - **not** to the reports' usual recipients (`report_email_to`,
`report_email_cc`). When `report_email_also_domains` is set, an address outside it gets nothing (the job says so).
The job runs on the AAP side: no inventory of servers, no Limit, no machine credential.

## What the documents look like

**The AAR** (`AAR.docx`, or `AAR-<outage>.docx` when the outage is named): your logo at the top left, the marking
banner (`incident_docs_banner`) at the top and bottom of every page - by default (`auto`) the reports' classification
banner when it is on (the classified enclave: `report_banner_text`, e.g. SECRET; [ENCLAVES.md](ENCLAVES.md)), else
`CONTROLLED UNCLASSIFIED INFORMATION (CUI)`; your own text wins - in bold, in the marking's usual colour: CUI purple,
UNCLASSIFIED green, CONFIDENTIAL blue, SECRET red, TOP SECRET orange (`incident_docs_banner_color`: your own, hex in
quotes) - then:

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
this outage's symptoms are seen again. Its root cause was: ...*; it also has **Signs it is happening** (the AAR's
Detection) after the purpose.

### The SOP made from the AAR

With **Also make an SOP: Yes**, each SOP step box left empty is filled from the AAR, box by box:

| SOP | From the AAR |
|---|---|
| Troubleshooting steps (`sop_troubleshoot`) | Actions (`incident_actions`) |
| Fix steps (`sop_fix`) | Recovery (`incident_recovery`) |
| Signs it is happening | Detection (`incident_detection`) |
| Purpose (`sop_purpose` empty) | *Use it when this outage's symptoms are seen again. Its root cause was:* Root cause |
| Title (`sop_title` empty) | *Troubleshoot and fix:* the outage's name |

A box you type wins: type only the fix steps, and the troubleshooting steps still come from Actions. The AAR's lines
are what was done during the outage (*Failed over to the spare at 10:40*), not yet steps for next time, so the SOP
says under each such list: *Taken from the AAR's Actions as written ... Reword them as steps anyone can follow before
this SOP is used.* The email's list of attachments says the same. Tasks and Successes are not put in the SOP (they
belong to this outage). No SOP is made only when Actions, Recovery and both step boxes are all empty.

**ACT: write the SOP's steps from the AAR** (`incident_act_sop`) has GenAI do the rewording: the steps to troubleshoot
from Actions, the steps to fix from Recovery, and the checks that it is fixed - short, in order, in the imperative,
no names or times of day - for the boxes left empty. A list is used only when every host name, address, number,
version or ticket in it is one in the AAR (nothing made up) and it has no masked word; otherwise that list stays the
AAR's lines as written, and the job and email say why (*ACT's steps had 192.0.2.99, not in the AAR*). Each list ACT
wrote says *Written by GenAI (ACT) from the AAR - not checked by anyone yet. Review each step before this SOP is used.*
ACT not reachable: the AAR's lines as written.

### Review the SOP before it is made (the SOP draft)

To read the steps and add to them before the SOP is written, answer **SOP: fill in the SOP job's form to review: Yes**
(`incident_sop_draft`) in the AAR job - with *Also make an SOP* **No** (or Yes, for a first copy as well):

1. The AAR job makes and emails the AAR as usual, and writes the SOP draft into the survey of the **SOP - Word
   document** template, as its default answers: *From outage*, the title, the purpose (the signs it is happening and
   the root cause), the steps to troubleshoot and to fix (from the table above - or ACT's, with *ACT: write the SOP's
   steps from the AAR*), the checks (typed, or ACT's) and escalation (typed). The job and the email say so.
2. You launch **SOP - Word document**: the form is filled in. Review each box, reword the steps, add the missing ones
   and the escalation, then **Launch** - the SOP is made from what you launched and emailed.
3. Once it is made, the SOP job empties the form again (`incident_sop_draft_clear`), but only when it was launched with
   that draft's *From outage*: an SOP for another outage leaves the draft where it is.

Good to know: the form holds **one** draft - the next AAR with a draft replaces it. Until it is used, anyone who can
open the SOP template sees the draft (the same text is in the AAR job's own answers). A box's text longer than the
question's maximum is cut, and the job says so. A dry run (Check) writes nothing.

**What it needs:** both templates need a **Red Hat Ansible Automation Platform** credential whose user has the
**Admin** role on the *SOP - Word document* template (the AAR job writes its survey; the SOP job empties it). A user
of its own for this is best - one with only that role, not the survey setup job's admin user. To have the setup job
attach it, name it in `all.yml` (or the setup template's Variables) and run *AAP - incident document surveys* again:

```yaml
incident_docs_draft_credential: AAP - SOP draft      # the credential's name in AAP
```

It attaches it to both templates, unless a template already has a credential of that type (it says so: attach it by
hand there). The user running the setup job needs the **Use** role on that credential. Without the credential, the AAR
is made as usual and the email says *No SOP draft: No AAP connection ...*.

The email itself names the attachments, says what ACT did, and holds none of the documents' text.

## Set it up

1. **Your logo, banner and instructions** - in **your** `playbooks/group_vars/all.yml` (never the public repository):
   ```yaml
   report_logo_file: files/report_logo.png          # the reports' logo is used; incident_docs_logo_file for another
   incident_docs_banner: auto                       # the default: the reports' banner when on, else CUI; or your own text
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

**The facts for the survey** (1.66.0): after a VMware outage, launch *VM - outage watch* with `vm_watch_review: true`.
Its section *For the after-action report* has the start, the end, the detection and ACT's reading of the root cause,
each named by its survey variable: [VM_OUTAGE_WATCH.md](VM_OUTAGE_WATCH.md#from-the-review-to-the-aar).

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
| SOP: title, purpose and when to use, steps to troubleshoot, steps to fix, check it is fixed, escalation | `sop_title`, `sop_purpose`, `sop_troubleshoot`, `sop_fix`, `sop_verify`, `sop_escalate` | Text, then Textareas (steps one per line) - read only when *Also make an SOP* is Yes; all optional: empty = from the AAR |
| ACT: tidy the wording | `incident_act_tidy` | Multiple choice No / Yes |
| ACT: suggest what may be missing | `incident_act_suggest` | Multiple choice No / Yes |
| ACT: write the SOP's steps from the AAR | `incident_act_sop` | Multiple choice No / Yes |
| SOP: fill in the SOP job's form to review | `incident_sop_draft` | Multiple choice No / Yes ([the SOP draft](#review-the-sop-before-it-is-made-the-sop-draft)) |

The SOP job's survey: *From outage (optional)*, the six SOP questions (the title required), *Email the documents to*,
and the two ACT questions. A text box takes up to `incident_docs_survey_max` characters (8000; AAP's own default is
1024, which would cut a long list of steps).

**Also make an SOP: Yes** with both step boxes empty: the SOP is made from the AAR (above). With Actions and Recovery
empty too, the AAR is still made and sent, without an SOP, and the email says why - launch again (*Relaunch* keeps
the other answers) with them.

Surveys set up with 0.64.0: run the setup job again - it adds *ACT: write the SOP's steps from the AAR*. Questions
already there keep their wording; to get the new descriptions of *Also make an SOP* and the two step boxes, delete
those questions in the survey first. It also adds *SOP: fill in the SOP job's form to review* (`incident_sop_draft`).

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
- **Write the SOP's steps:** see [The SOP made from the AAR](#the-sop-made-from-the-aar).
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
| The SOP's steps read like a log (*Opened a bridge*, *Failed over ...*) | they are the AAR's Actions and Recovery as written: reword them in the document, type the SOP's own steps, or choose *ACT: write the SOP's steps from the AAR* |
| `No SOP draft: No AAP connection` | attach the AAP credential to the AAR template (`incident_docs_draft_credential`, above) |
| `No SOP draft in '...': ... may not change this job template` / `HTTP 403` | the credential's user needs the Admin role on the *SOP - Word document* template |
| `Its survey has no question for ...` | the SOP template's survey is not set up: run *AAP - incident document surveys* |
| The SOP form still has an old draft | it is emptied only by an SOP launched with that draft's *From outage* (or the next draft): clear the boxes by hand, or launch it |
| A question still has the old wording after an update | the setup job leaves questions that are there as they are: delete the question in the survey, and run it again |
| `ACT was not used: This job has no API key ...` | attach the **ACT model key** credential to the template |
| Word opens the attachment in *Protected View* | as for any emailed file: *Enable Editing* |
| `AAP refused the survey (HTTP 400)` | the AAP user needs the Admin role on the template; the message has AAP's reason |
