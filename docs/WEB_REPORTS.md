# The report website

Every report job ([EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Which jobs email") can also write its report as a
**web page**: the same report as the email, in the portal look (or `report_web_theme`: standard, command, glass), with your logo. Each run gets its own page,
each template a `latest.html` that is always its newest run, and an `index.html` lists every report's last run,
worst first. The report's data is written as JSON beside each page, so a web developer can build their own site
on it.

It works **without email**: set `report_web_dest` and leave `report_email_to` empty. With both set, the email
carries a link to its page ("Open this report in a browser").

The job prints the page's address, and keeps it as the job artifact `site_report_url`:

```
TASK [site_email : Email | the report's address]
ok: [localhost] => {
    "msg": "Report: https://reports.yoursite.mil/linux-certificate-trust-report/2026-10-23_0605_job-907.html"
}
```

## What it writes

```
index.html                                  every report's last run, worst first (status, name, title, when)
index.json                                  the same as data
linux-certificate-trust-report/
  2026-10-23_0605_job-907.html              this run's page - the address the job prints
  2026-10-23_0605_job-907.json              this run's data
  latest.html                               always the newest run (a link that never changes)
  latest.json
  2026-10-22_0600_job-901.html              earlier runs stay
  ...
vmware-snapshot-cleanup/
  ...
```

- **The folder** is the AAP template's name, lower case with dashes: `Linux - Certificate trust report` becomes
  `linux-certificate-trust-report`. Outside AAP (the command line), the play's name.
- **The file** is the time the job wrote it (UTC) and the AAP job number: listing a folder sorts the runs oldest
  to newest, and the number leads straight to the job in AAP.
- Renaming a template in AAP starts a new folder for its new runs (the old runs stay in the old one). The index
  shows each folder's last run; one that stopped can be deleted on the server.
- Every page stands alone: the logo is inside it (no separate image file), no scripts, no outside fonts or styles.
- Nothing is ever deleted. To keep, say, 90 days, a nightly job on the web server:
  `find /srv/reports -name '20??-*_job-*.*' -mtime +90 -delete`.

### Changing the names

Every name is a setting, with `{names}` filled in (`roles/site_email/defaults/main.yml`):

| Setting | Default |
|---|---|
| `report_web_page` | `{template}/{date}_{time}_job-{job}.html` - each run's page |
| `report_web_latest` | `{template}/latest.html` - `""` for none |
| `report_web_index` | `index.html` - `""` for none |
| `report_web_names` | `{}` - your own names, e.g. `{site: hq}` for `{site}/{template}/...` |

The names: `{template}`, `{template_id}`, `{job}` (`local` outside AAP), `{status}` (`ok`, `warning`, `critical`,
`info`), `{date}` (`2026-10-23`), `{time}` (`0605`), `{year}`, `{month}`, `{day}`, and your own. For example a
folder per site and per month:

```yaml
report_web_names: {site: hq}
report_web_page: "{site}/{template}/{year}-{month}/{date}_{time}_job-{job}.html"
report_web_latest: "{site}/{template}/latest.html"
```

A name must end in `.html` and stay inside the report folder (no leading `/`, no `..`); a value is made safe for
a file name (letters, digits, `.`, `-`, `_`; the rest becomes `-`). A name that does not exist stops the job and
lists the ones that do.

### The JSON (for your own site)

Each run's `.json` (and `latest.json`):

```json
{
  "report": {"title": "...", "subtitle": "...", "status": "warning",
             "summary": [{"label": "Hosts", "value": 34}, {"label": "Findings", "value": 752, "status": "warning"}],
             "sections": [{"title": "...", "status": "...", "columns": ["Host", "..."], "rows": [["web01", "..."]],
                           "row_status": ["critical"], "cell_status": [["critical", ""]]},
                          {"title": "Notes", "lines": ["..."]}],
             "footer": "..."},
  "job": 907, "template": "Linux - Certificate trust report", "template_id": 71,
  "when": "2026-10-23 06:05 UTC",
  "runs": [{"status": "ok", "value": 3, "label": "Findings", "when": "...", "ago": "2 days ago", "current": false}],
  "page": "linux-certificate-trust-report/2026-10-23_0605_job-907.html",
  "url": "https://reports.yoursite.mil/linux-certificate-trust-report/2026-10-23_0605_job-907.html",
  "latest": "linux-certificate-trust-report/latest.html", "index": "index.html"
}
```

`report` is the layout every report uses ([EMAIL_REPORTS.md](EMAIL_REPORTS.md), "Emailing a report from another
playbook"). `runs` is the strip of last runs (with an AAP credential on the template). `index.json`:
`{"generated": "...", "reports": [{"key", "name", "title", "status", "when", "job", "page", "latest"}, ...]}`,
worst first.

## Where the files go

Three ways; the pages are the same in each.

### A Windows server, over WinRM

```yaml
report_web_host: web01.yoursite.mil       # the Windows server (a file server, or the IIS server itself)
report_web_dest: 'D:\reports'             # the folder on it - there already
report_web_url: https://reports.yoursite.mil
```

The job makes the pages on the AAP side, as always, and writes them on that server over WinRM - the way the
Windows jobs reach your servers. Nothing to mount on the execution nodes, no WebDAV, no share. Two steps there
per run: one reads the folder's `index.json` back (to add this template's line), one PowerShell script writes
the run's files (each to a temporary name, then over the old one). Both work in Windows PowerShell 5.1 and 7, and
in Constrained Language Mode; the pages are written as ASCII (every other character as an HTML character
reference, e.g. `&#233;`), so no byte-order mark and no code page gets in the way.

1. **The folder**: e.g. `D:\reports`, made once by hand. The job makes the folders inside it.
2. **The account**: one that may use WinRM on that server (a local administrator, or a member of
   **Remote Management Users**) with **Modify** on the folder. A service account for it alone is best.
3. **The login**: create the **Report website** credential type (`aap/credential_types/report_web.yml`), a
   credential of it with that account (`DOMAIN\user` and its password), and attach it to each report template.
   It is a credential type of its own, so it goes beside the template's Machine credential (a Linux job's SSH
   key, say). Without it, the job logs in with the template's Machine credential.
4. **The connection**, as your Windows inventory's (docs/WINDOWS.md, step 4) - the defaults:

   | Setting | Default | |
   |---|---|---|
   | `report_web_host_connection` | `winrm` | or `psrp` |
   | `report_web_host_port` | `5986` | |
   | `report_web_host_scheme` | `https` | `http` with Kerberos or NTLM (they encrypt each message then) |
   | `report_web_host_transport` | `ntlm` | `kerberos` (needs Kerberos in the execution environment), `credssp`, `basic` |
   | `report_web_host_cert_validation` | `validate` | `ignore` only to try it; `report_web_ca_path` is the CA that signed the WinRM certificate |

   The execution environments from Red Hat do not trust DoD certificate authorities: with `validate`, set
   `report_web_ca_path` to your CA file inside the EE (as `ansible_winrm_ca_trust_path` in docs/WINDOWS.md), or
   the job stops with `certificate verify failed`.
5. **The website**: IIS on that server (or any web server reading the folder) serves `D:\reports` at
   `report_web_url`. Only reading - nothing is uploaded to IIS.

The server does not have to be in the job's inventory: the report jobs use many inventories, and the settings
above are all it needs. `report_web_dest` is a folder **on** that server (`D:\reports`), not a share
(`\\fileserver\reports`): over WinRM that would be a second hop, which NTLM does not allow.

### A web server that takes uploads (HTTP PUT)

`report_web_dest: https://reports.yoursite.mil/upload` - the job uploads each file with `PUT` (WebDAV), from the
AAP side. Nothing to mount, nothing on the execution nodes. The job reads `index.json` back from the same address
(`GET`) to add its line. nginx, for example (the web developer's part):

```nginx
server {
    listen 443 ssl;
    server_name reports.yoursite.mil;
    # ssl_certificate ... ;
    root /srv/reports;
    charset utf-8;

    location / {                         # reading: the pages
        index index.html;
        try_files $uri $uri/ =404;
    }
    location /upload/ {                  # writing: the jobs
        alias /srv/reports/;
        dav_methods PUT MKCOL;
        create_full_put_path on;
        client_max_body_size 50m;
        limit_except GET {               # GET (reading index.json back) open; PUT needs the login
            auth_basic "report uploads";
            auth_basic_user_file /etc/nginx/reports.htpasswd;   # htpasswd -c ... aap-reports
        }
    }
}
```

`/srv/reports` must be writable by nginx (`chown nginx: /srv/reports`; with SELinux
`semanage fcontext -a -t httpd_sys_rw_content_t '/srv/reports(/.*)?'` and `restorecon -R /srv/reports`). IIS
works too, with the WebDAV Publishing feature and a login on the upload path; so does Apache with `mod_dav`, or any
endpoint of your own that takes `PUT` (answering 409 when a folder is not there makes the job create it with
`MKCOL`).

The upload's login: create the **Report website** credential type (`aap/credential_types/report_web.yml`), a
credential of it (a user name and password - sent as basic authentication - or a token, sent as
`Authorization: Bearer ...`), and attach it to each report template. Never put it in Variables.

```yaml
report_web_dest: https://reports.yoursite.mil/upload
report_web_url: https://reports.yoursite.mil          # what people open; the job prints addresses under it
report_web_ca_path: /etc/pki/ca-trust/source/anchors/yoursite-ca.pem   # only if the EE does not trust the server's CA
```

The report folder itself (`/upload/` here) must exist; the job makes only the folders inside it.

### A folder: a share mounted on the execution nodes

`report_web_dest: /mnt/reports` - the job writes the files there, from the AAP side, so the folder must be
visible inside the job's container:

1. Mount the share on **every execution node** that runs these jobs (the same path on each), writable by the
   user AAP runs jobs as (`awx`). An SMB share, e.g. in `/etc/fstab`:
   `//fileserver/reports /mnt/reports cifs credentials=/etc/reports.cred,uid=awx,gid=awx,file_mode=0664,dir_mode=0775,_netdev 0 0`
   (with SELinux add `context=system_u:object_r:container_file_t:s0`).
2. In AAP: **Settings > Job settings > Paths to expose to isolated jobs**: `/mnt/reports:/mnt/reports:rw`.
3. The web server serves the same share (IIS on the file server, or nginx with the share mounted).

A folder that is not there stops the job with "is the share mounted?" - the job never creates it, so a share that
failed to mount does not fill the node's own disk. No credential is needed.

## Turning it on

For every report job, in `playbooks/group_vars/all.yml` (the address is the same for all of them):

```yaml
report_web_host: web01.yoursite.mil                     # a Windows server - or leave it out for an upload or a folder
report_web_dest: 'D:\reports'                           # (https://reports.yoursite.mil/upload, /mnt/reports)
report_web_url: https://reports.yoursite.mil
report_logo_file: files/report_logo.png                 # your logo, in your repository
```

Or on one template's Variables to try it on one job first. Like the email, not in an inventory group's Variables:
the page is written by the AAP side of the job, which never gets a group's variables.

| Setting | Default | |
|---|---|---|
| `report_web_dest` | `""` (off) | the folder on `report_web_host` (`D:\reports`), the upload address (`https://...`) or the folder (`/mnt/...`) |
| `report_web_host` | `""` | a Windows server to write the pages on, over WinRM; `report_web_host_*` for the connection ("A Windows server") |
| `report_web_url` | `""` | the address people open; `""` = `report_web_dest` when it is `https://` (else the job prints where the page is: `web01:D:\reports\...`) |
| `report_web_theme` | `portal` | `standard`, `command`, `portal` or `glass`, as `report_email_theme`; `glass` adds the blur behind the cards, icons and a plain print |
| `report_web_json` | `true` | the JSON beside each page |
| `report_web_ca_path` | `""` | a CA file for an `https://` upload address, or for `report_web_host`'s WinRM certificate |
| `report_web_page`, `_latest`, `_index`, `_names` | see "Changing the names" | |

A dry run (Check) writes nothing and prints the address it would have used.

## Using the address in a workflow or another job

The job keeps the page's address as the artifact `site_report_url`. In a workflow, the next step gets it as a
variable (`{{ site_report_url }}`) - for a Teams post or a ServiceNow work note. From AAP's API:
`GET /api/controller/v2/jobs/<id>/` - `artifacts.site_report_url`. From the job output, the line
`Report: https://...`.

## Troubleshooting

| The job says | Why, and what to do |
|---|---|
| `the folder /mnt/reports is not there - is the share mounted, and the path exposed to the job` | The share is not mounted on the execution node that ran the job, or not in "Paths to expose to isolated jobs" |
| `writing /mnt/reports/...: Permission denied` | The share is not writable by `awx` (the mount's `uid` / `file_mode`), or SELinux (`context=` on the mount) |
| `the folder https://.../upload is not there on the website` | The upload's own folder is missing on the server (`alias` / its directory) |
| `uploading ...: HTTP 401` or `403` ... `check the login` | Attach the Report website credential; check its user name and password or token |
| `uploading ...: HTTP 405` | The server does not take `PUT` at that address (nginx: `dav_methods PUT MKCOL;` in that location) |
| `uploading ...: HTTP 413` | The page is bigger than the server allows (nginx: `client_max_body_size`) |
| `certificate verify failed` | Set `report_web_ca_path` to the CA that signed the server's certificate |
| `could not reach report_web_host web01... (winrm, port 5986): ...` - `the specified credentials were rejected` | The login: the Report website credential (`DOMAIN\user`), or the account is locked; without that credential, the template's Machine credential is used |
| `could not reach report_web_host ...: ... certificate verify failed` | `report_web_ca_path` (the CA of the server's WinRM certificate), or `report_web_host_cert_validation: ignore` to try it |
| `could not reach report_web_host ...: ... Connection refused` or timed out | WinRM is not on that port (`report_web_host_port`, `_scheme`), or a firewall between AAP and the server |
| `the folder D:\reports is not there on WEB01 (report_web_dest)` | Make the folder on that server; the job makes only the folders inside it |
| `The report website: Access to the path 'D:\reports\...' is denied` | The account has no Modify on the folder (NTFS) |
| `winrm or requests is not installed` | The execution environment has no `pywinrm` (docs/WINDOWS.md, step 2) |
| `report_web_page '...': there is no {x}` | A name that does not exist; the message lists the ones that do |
| A report missing from the index | Two jobs finished in the same second and one line was lost; it is back after that job's next run |
