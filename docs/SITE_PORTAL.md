# The site portal

One web place for the reports and what people may start from them. People open it with their card (CAC):

- **The report website**, at `/portal/reports/`: the same pages the report jobs write.
- **Actions**: a report's button opens a confirm page with the values filled in. The person presses **Run**, and AAP
  runs the workflow or job template behind it. Only the actions you list exist.
- **The live view** of what they started, at `/portal/jobs/<id>`: its status, its steps, its output, then its result
  and links.
- **Approvals**, at `/portal/approvals`: the listed workflows waiting for a decision. Approvers press Approve or Deny.

It lives **behind the reverse proxy you have** - STIG Manager's nginx - at **`/portal/`**:
`https://stigman.yoursite.mil/portal/`. That nginx already does TLS and checks the card's certificate (mTLS). For a
server of its own, the portal can bring its own nginx instead (*Its own nginx*, below).

| Job template | Playbook | Credentials |
|---|---|---|
| Site portal - deploy | `playbooks/site_portal_deploy.yml` | Machine, Red Hat Ansible Automation Platform (the portal's own account, with a token) (Container registry login (hosts): optional) |

## What it may do

The portal starts only what the actions file lists, only with values its rules allow:

1. **Only listed actions.** `roles/site_portal_stack/files/actions.yml` (or the setting `portal_actions`) lists each
   action: its title, its AAP workflow or job template by name, and the values it takes. Any other name answers
   *There is no action ... here*.
2. **Only listed values.** Each value is checked against its rule twice: on the confirm page, and again when Run
   sends it. A host must be in the AAP inventory `portal_inventory`. A choice must be one of its list. A text must
   fit its length and, when the action names one, its pattern. A value the action does not list is refused.
3. **No template braces.** AAP renders Jinja in extra vars when a playbook reads them. So the portal refuses `{{`,
   `{%` and `{#` in every value - also with spaces or invisible characters between, and in look-alike (full-width)
   braces.
4. **Only listed people.** `portal_requesters` (or an action's own `who`) says who may run an action.
   `portal_approvers` says who may decide approvals here.
5. **The portal's own AAP account.** It needs Execute on the listed workflows and templates, Approve on those with
   an approval node, and Read on `portal_inventory`. Nothing else (step 2).

**What AAP gets with a launch:** the checked values as extra vars, `portal_requested_by` (the person's name from
their card), and the limit when the action sets one. Nothing else.

## How it works

```text
browser --TLS + card--> STIG Manager's nginx --/--> STIG Manager (as before)
                               |
                               +--/portal/--> the portal --its token--> AAP's API
                                              (podman)  |
                                                        +--read-only--> reports
                                                        +--each launch--> its log
```

- **One container**, `site-portal-app`, on podman, run by systemd (Quadlet). It joins the **podman network of
  STIG Manager's nginx container** (`portal_network`) and publishes **no port**. Only the containers on that network
  reach it. Keep only STIG Manager's containers on it: anything there could send the two headers below.
- **Who it is** is the card's certificate that nginx verified. nginx sets `X-SSL-Client-Verify` (`SUCCESS`) and
  `X-SSL-Client-S-DN` (the subject) on every request, over anything the browser sent. The person is the subject's
  name (CN): `DOE.JANE.Q.1234567890`. A request without `SUCCESS`, or without a name, is refused.
- **AAP** is asked with the portal's own account and its token. The token is a podman secret on the server. It is not
  in a file, a unit, `podman inspect` or the log, and it never reaches a browser.
- **A report's button** is a plain link to `/portal/actions/<name>?<var>=<value>`. Opening it never starts anything.
  The confirm page's Run sends the values with the page's own token (a CSRF check: the same site, this person, the
  last 8 hours).

## Set it up

**Step 100** of the to-do list. Six steps. Steps 1 and 2 only look or make an account; step 4 is the job; step 5 is
the one change to STIG Manager's nginx, by hand.

### 1. Look at STIG Manager's nginx

On the STIG Manager server (`sudo` throughout: STIG Manager's containers are root's):

```bash
sudo podman ps --format "{{.Names}}  {{.Networks}}  {{.Ports}}"
```

Note the **nginx container's name** (the one with `443` in its ports) and its **network**. Check the network has DNS:

```bash
sudo podman network inspect <the network> --format "{{.DNSEnabled}}"      # must say: true
```

`false`, or the network is `podman` or `host`: stop here. The portal needs a network with DNS; ask before changing
STIG Manager's.

Find the nginx config file with STIG Manager's `server { ... }` block:

```bash
sudo podman inspect <nginx> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

Its `server` block must have `ssl_client_certificate` (the DoD CAs) and **`ssl_verify_client on`** or
**`optional`**. Without them nginx checks no card, and the portal refuses everyone.

Note the folder the report jobs write to on this server (`report_web_dest`'s folder, e.g. the one STIG Manager's
nginx serves `/reports/` from). That is `portal_reports_dir`.

### 2. The portal's AAP account

The portal launches and approves as one AAP account of its own. Make it once, as an AAP administrator:

1. **Access Management > Users > Create user:** `svc_site_portal`, a normal user (not an administrator).
2. On each workflow and job template the actions name: **User and Team Access > Add roles:** the account,
   **Execute**. On each workflow with an approval node, also **Approve**.
3. On the inventory `portal_inventory` (the hosts a person may pick): **Read**.
4. Log in as the account, then **its user menu > User details > Tokens > Create token**, scope **Write**. Copy the
   token: AAP shows it once.
5. **Credentials > Create credential**, type **Red Hat Ansible Automation Platform**: AAP's address, the account's
   user name, and the token in the token field (no password).

Each listed template must take what the portal sends. On each one, tick **Prompt on launch** for **Variables** (or
give its survey a question for each of the action's vars and `portal_requested_by`). With `limit_from`, also tick
**Prompt on launch** for **Limit**. The deploy says which action AAP would not take, and why.

### 3. The server, in the inventory

It is the STIG Manager server. It needs to reach AAP on 443. Put it in your inventory in the group `site_portal`
(or give the job `target: <its name>`). Its podman must be 4.4 or later (RHEL 9.2+ / 8.8+).

### 4. The job template

*Templates > Create template > Create job template*:

- **Name:** `Site portal - deploy`
- **Inventory:** the one with the server. **Limit:** the server.
- **Playbook:** `playbooks/site_portal_deploy.yml`
- **Credentials:** Machine (ssh and sudo on the server), the Red Hat Ansible Automation Platform credential of step 2;
  Container registry login (hosts) when the image needs a login to pull.
- **Variables:** copy [`aap/extra_vars/site_portal_deploy.yml`](../aap/extra_vars/site_portal_deploy.yml) and set
  at least:

```yaml
portal_network: stigman                      # step 1: the network of STIG Manager's nginx container
portal_image: hub.yoursite.mil/site-ee-all:1
portal_reports_dir: /srv/reports             # step 1: the folder the report jobs write to
portal_inventory: Site hosts                 # step 2: the inventory a person's host must be in
portal_approvers: [DOE.JANE.Q.1234567890]    # who may approve or deny here
portal_public_url: https://stigman.yoursite.mil/portal/
```

Launch it with Job type **Check** first. It lists what it would write - only `/etc/site-portal`, the `site-portal-*`
unit files and the secret - and writes, starts and makes nothing. Then **Run**.

Success looks like this at the end of the job:

```text
The site portal is up on the podman network stigman. AAP: answered (the account svc_site_portal).
Actions and their state in AAP: act_triage - ok; ckl_import - ok; stig_recheck - ok.
```

An action that is not `ok` says why (*Workflow ... is not there*, *... does not take extra variables*). The portal
runs without it; that action's page says *Off*. Its last task shows **the lines to add to nginx** (also in
`/etc/site-portal/nginx-portal.conf` on the server).

**After an update of this repository**, run the template again. It copies the service and restarts it only if
something changed. nginx's lines stay as they are.

### 5. Add /portal/ to your nginx

Once, by hand, on the STIG Manager server, with the file and the container name from step 1:

1. **Keep a copy:** `sudo cp <the file> <the file>.before-portal`
2. **Read the lines in** from `/etc/site-portal/nginx-portal.conf`, **inside** STIG Manager's `server { ... }` block,
   just **above** its `location / {`. In vim: `:set backupcopy=yes`, the cursor on the line above, then
   `:r /etc/site-portal/nginx-portal.conf`. With nano: `sudo nano <the file>`, then Ctrl+R and the path.
3. **Check the container sees them** - this must print `1`:
   `sudo podman exec <nginx> grep -c 'location /portal/' /etc/nginx/conf.d/default.conf`. A `0`: the editor saved a
   new file; put the copy back and edit again in place.
4. **Test it:** `sudo podman exec <nginx> nginx -t` must say *syntax is ok* and *test is successful*. Anything else:
   put the copy back, and nothing has changed.
5. **Load it:** `sudo podman exec <nginx> nginx -s reload`. STIG Manager keeps working, and its sessions too.

The lines are two `location` blocks. They look like this (use the file's: its `resolver` is your network's address):

```nginx
location = /portal {
    absolute_redirect off;
    return 301 /portal/;
}
location /portal/ {
    resolver 192.0.2.1 valid=30s ipv6=off;   # the podman network stigman's DNS
    set $site_portal_app http://site-portal-app:8080;
    rewrite ^/portal/(.*)$ /$1 break;
    proxy_pass $site_portal_app;
    proxy_http_version 1.1;
    proxy_set_header Host                $http_host;
    proxy_set_header X-Real-IP           $remote_addr;
    proxy_set_header X-Forwarded-Proto   https;
    proxy_set_header X-SSL-Client-Verify $ssl_client_verify;
    proxy_set_header X-SSL-Client-S-DN   $ssl_client_s_dn;
    proxy_read_timeout 60s;
    client_max_body_size 64k;
}
```

**If STIG Manager's nginx file is written by a job** (*STIG Manager - deploy* writes it on each run), that run takes
the lines out again. Read them in again after it.

### 6. Try it

1. Open `https://stigman.yoursite.mil/portal/` with your card. The header shows your name from the card.
2. **Reports:** the report website's index.
3. **Actions:** the actions you may run. Open one: its confirm page lists what will run, where, and whether an
   approval follows.
4. Press **Run**. The job's page opens and follows it to the end.
5. An approver opens **Approvals** and decides; the workflow goes on in AAP.

STIG Manager, at `/`, is as it was.

## The actions

Each action is an entry in `roles/site_portal_stack/files/actions.yml`. The setting `portal_actions` (a dict of the
same shape) replaces the whole file.

```yaml
actions:
  act_triage:                                   # its name: lower case letters, digits and _
    title: Ask ACT about this finding
    aap: {type: workflow, name: "ACT - triage finding"}     # or type: job (a job template), by its name in AAP
    vars:                                       # each key is the extra var's name
      target: {kind: inventory_host, label: Host}
      report_job: {kind: text, max: 12, pattern: '[0-9]{1,12}'}
      finding_id: {kind: text, max: 120, pattern: '(fapolicyd|eventlog):[A-Za-z0-9_.:-]{1,100}'}
    limit_from: target                          # optional: the launch's limit
    approval: false                             # shown on the page; the workflow's approval node holds it
    who: any                                    # optional: any, or a list of CNs; left out = portal_requesters
```

| A var's kind | Its rule |
|---|---|
| `inventory_host` | a host of the AAP inventory `portal_inventory`, by its exact name (letters, digits, `.`, `-` and `_`) |
| `choice` | one of `values` (a list); `default` when none is sent |
| `text` | at most `max` characters (4000 when not set, 20000 at most); with `pattern`, the whole value must match it (a regular expression) |

Each var may also have `label` (the confirm page's name for it), `required` (default true) and `default`.

The deploy checks the file with the same rules as the portal, and stops on a wrong entry before it changes anything:
*The actions are not right - nothing was changed: action x: title is required*. The portal checks it again when it
starts.

**A report's link** to an action: `<the portal>/actions/<name>?<var>=<value>&...`, e.g.
`https://stigman.yoursite.mil/portal/actions/act_triage?finding_id=eventlog%3Adisk-errors&report_job=907&target=web01.yoursite.mil`.
The values are URL-encoded. A value that fails its rule shows its reason on the confirm page, so the person can
correct it; Run checks everything again.

## Approvals

`/portal/approvals` lists the pending approvals of the **listed** workflows, whoever started them (the portal, a
schedule, a person in AAP). Each shows who started it through the portal, with which values, and since when it waits.

- **Approve** and **Deny** are there only for the people in `portal_approvers`. Others see the list.
- **AAP records the decision as the portal's own account.** The portal's log keeps who decided: the name on the
  card, the decision and the time. The page says so.
- AAP's own approval page works as before.

## Who started what

Every launch, every refusal and every decision is one JSON line:

- **in the journal:** `journalctl -u site-portal-app` (or `podman logs site-portal-app`). Forward it to your SIEM with
  the rest of the server's journal.
- **in the data volume:** `portal-log.jsonl` in the volume `site-portal-data`.

```bash
sudo tail -5 "$(sudo podman volume inspect site-portal-data --format '{{.Mountpoint}}')/portal-log.jsonl"
```

A launch line holds the person (`user`), the action, the values, the job's id (`job_id`), the limit and the time
(`at`, UTC). A decision line holds the approver, `approve` or `deny`, the approval's id and who had started it.

## Security

- **The card, through nginx.** The two headers are set by nginx on every request; one the browser sends is replaced.
  The portal is reachable from the proxy's network only.
- **A launch only from the portal's own page.** Run sends a JSON body, the page's token (this person's, 8 hours) and
  the browser's Origin, which must be the portal's own address. A page of another site cannot start anything.
- **Limits.** A request body of 64 KB at most. At most `portal_launches_per_hour` (20) launches per person an hour.
- **AAP's setting `ALLOW_JINJA_IN_EXTRA_VARS`** must not be *Always*. The default, *Only On Template Definitions*,
  treats launch values as data. The deploy reads it from the portal and warns when it is *Always*. The portal's
  account usually may not read AAP's settings; the deploy then says to check it once by hand: **Settings > Job
  settings > Run Jinja in extra variables**.
- **Pages.** No inline script, `script-src 'self'`, no frames. The report pages under `/portal/reports/` keep their
  own styles.

## Its own nginx

For a server without STIG Manager: `portal_own_nginx: true`. The role then adds `site-portal-nginx`, an nginx
container with TLS and a card's certificate on every request, on a podman network of its own (`site-portal`). It
publishes `portal_port` (443) and opens it in firewalld.

```yaml
portal_own_nginx: true
portal_server_name: portal.yoursite.mil
portal_cert_file: /etc/pki/tls/certs/portal.pem      # the server's certificate, its chain after it
portal_key_file: /etc/pki/tls/private/portal.key     # its key, not encrypted
portal_ca_file: /etc/pki/tls/certs/dod-cas.pem       # the CAs of the cards
portal_crl_file: ""                                  # optional: their revocation list
portal_nginx_image: hub.yoursite.mil/nginx:1.28      # your registry's copy, pinned
```

The three files are paths on the server; the role copies them into `/etc/site-portal/tls`. `/` sends people to
`/portal/`.

## The report folder and SELinux

The report folder is mounted read-only with the shared label (`portal_reports_mount: ro,z`). A folder that another
container mounts with `:Z` (a private label) gets that label back when that container starts, and the portal then
cannot read it: *Reports* answers *Not found*. Check with `ls -dZ <the folder>`. A label with two categories
(`c123,c456`) is private: mount it with `:z` in that container too, or ask before changing it.

## Settings

Every setting, with its default: [`aap/extra_vars/site_portal_deploy.yml`](../aap/extra_vars/site_portal_deploy.yml)
and [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md). The ones you are likely to change:

| Setting | Default | What it does |
|---|---|---|
| `portal_network` | | the podman network of STIG Manager's nginx container (required behind it; it must exist, with DNS) |
| `portal_image` | | your execution environment image (any image with Python 3) |
| `portal_reports_dir` | `/srv/reports` | the folder the report jobs write to, on this server |
| `portal_inventory` | | the AAP inventory an action's host must be in |
| `portal_requesters` | `[]` | who may run the actions (CNs); `[]` = any person with a verified card |
| `portal_approvers` | `[]` | who may approve or deny here; `[]` = nobody (AAP's own page still works) |
| `portal_actions` | `{}` | the actions; `{}` = the role's `files/actions.yml` |
| `portal_path` | `/portal/` | the path on nginx |
| `portal_public_url` | `https://<the server>/portal/` | the address people open (said at the end) |
| `portal_classification` | `U` | the banner: U, CUI, C, S, TS, SCI, NONE |
| `portal_launches_per_hour` | 20 | one person's launches in an hour, at most |
| `portal_aap_ui` | AAP's address | the address of AAP's pages, for the *Open in AAP* links |
| `portal_own_nginx` | false | its own nginx (TLS, the cards) instead of STIG Manager's |

## When something is wrong

| What you see | What to do |
|---|---|
| The job stops: *Missing credentials ... WITH A TOKEN* | step 2: the AAP credential needs the account's token |
| The job stops: *Set portal_path ... portal_network ...* | step 1: the network of STIG Manager's nginx container, as `podman ps` names it |
| The job stops: *The actions are not right* | the line after it names the action and what is wrong |
| The job says *AAP: NOT REACHED* | the server cannot reach AAP on 443, the token is wrong, or AAP's certificate is not trusted (`update-ca-trust` on the server, then the job again) |
| An action *Off: ... does not take extra variables* | that template: Prompt on launch for Variables (step 2) |
| An action *Off: ... is not there* | the AAP name in the actions file, or the account has no Execute on it |
| `/portal/` says *502 Bad Gateway* | the service is not running (`sudo systemctl status site-portal-app`), or it is on another network than nginx |
| *404 Not Found* for `/portal/` | the lines are not loaded: step 5's `grep -c`, then `nginx -s reload` |
| *Not accepted. No client certificate was accepted* | nginx asked for no card (`ssl_verify_client`), or the browser sent none |
| *You are not on the list of people who may use this portal* | `portal_requesters`, or an action's `who` |
| *Refused: send it from the portal's page* | the page is older than 8 hours, or was opened from another address: reload it |
| A value *holds {{ - braces that start a template are not allowed* | the value holds template braces; take them out |
| *Reports* answers *Not found* | `portal_reports_dir`, or the folder's SELinux label (*The report folder and SELinux*) |

## Taking it out

1. Take the lines out of STIG Manager's nginx: put the copy back (step 5), `nginx -t`, `nginx -s reload`.
2. `sudo systemctl stop site-portal-app` (and `site-portal-nginx` with its own nginx).
3. Remove `/etc/containers/systemd/site-portal-*`, then `sudo systemctl daemon-reload`.
4. `sudo podman secret rm site-portal-aap-token`. Keep the volume `site-portal-data` (the log) as long as you need
   the record, then `sudo podman volume rm site-portal-data`.
5. Remove `/etc/site-portal`. In AAP, remove the account's token, then the account.

## Not yet

- **Tried on a real AAP and STIG Manager?** Not yet. It was tested end to end on podman with a stand-in AAP API
  (`tests/site_portal/fake_aap.py`: launch, job events, workflow nodes, approvals), an nginx with client
  certificates standing in for STIG Manager's, and its own nginx - as a user (rootless podman, `portal_scope: user`),
  not as root. The AAP calls follow AAP 2.5's API (`api/controller/v2/`); their field names were not checked against
  a real AAP. Try it on a test server first, and run step 4 as Check.
- **AAP's live output** is read every few seconds, not streamed.
- **Revocation** of cards is nginx's: `ssl_crl` in STIG Manager's nginx, or `portal_crl_file` with its own. The
  portal does not ask OCSP.
