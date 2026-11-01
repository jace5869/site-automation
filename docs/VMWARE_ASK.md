# VMware ask - the web page

*VM - ask a question* ([VM_ASK.md](VM_ASK.md)) as a web page. People open it with their card (CAC), type a
question about vCenter in plain words and get the answer on the page in seconds, with no AAP job to launch:

- *all VMs with dbv in the name*
- *VMs on datastores with less than 10% free*
- *hosts in maintenance mode, and how many VMs each has*
- *the 10 biggest snapshots older than a week*
- *VMs not restarted in 90 days*

It is the job's engine: the same guard, the same GenAI prompt, the same whitelist and the same readers. A question
reads exactly as it does in the job, and everything [VM_ASK.md](VM_ASK.md) says about what you can ask applies
here too. Each answer shows how the question was read and when vCenter was read, the rows (a CSV of every row to
download), and *Edit and ask again*.

It lives **behind the reverse proxy you have** - STIG Manager's nginx - at **`/ask/`**:
`https://stigman.yoursite.mil/ask/`. That nginx already does TLS and checks the card's certificate (mTLS); the page
needs no Keycloak, no certificate of its own and no port of its own.

| Job template | Playbook | Credentials |
|---|---|---|
| VMware ask - deploy | `playbooks/vmware_ask_deploy.yml` | Machine, VMware vCenter (read-only is enough), ACT model key (Container registry login (hosts): optional) |

## It only reads

The page can only look. It cannot restart, shut down, delete, move, snapshot or change anything:

1. **A read-only vCenter account.** Give the account of its *VMware vCenter* credential the built-in
   **Read-only** role at the top of each vCenter, propagated ([VMWARE.md](VMWARE.md#3-the-credential)). Even a
   bug could then change nothing.
2. **The job's four locks.** A question that asks for a change is refused before GenAI sees it; GenAI only
   turns the question into a query, never code or a command; the query is held to the whitelist; only the
   read-only readers run ([VM_ASK.md](VM_ASK.md#it-only-reads)).
3. **No code that writes.** The service has no call that changes vCenter. Its vCenter reads are
   `roles/vmware_ask_stack/files/refresh.yml`: the job's five readers and nothing else, and
   `tests/test_vmware_ask.py` fails if anything else is added.

**What GenAI gets.** The question and the list of fields, as with the job. No VM names, hosts, addresses or
anything else read from vCenter, not who asked, and not the vCenter account: the service starts ACT without it.

## How it works

```text
browser --TLS + the card--> STIG Manager's nginx --/--> STIG Manager (as before)
                                   |
                                   +--/ask/--> the service --read-only--> vCenter(s)
                                               (podman)    |
                                                           +--the question only--> GenAI
                                                           +--who asked what--> SQLite
```

- **One container**, `vmware-ask-app`, on podman, run by systemd (Quadlet). It joins the **podman network of
  STIG Manager's nginx container** (`vmware_ask_network`) and publishes **no port**: only the containers on that
  network reach it - STIG Manager's nginx (by its name), and STIG Manager's own other containers (its API, its
  database). Who asks rests on that: anything on that network could send the two headers below, so keep only STIG
  Manager's containers on it.
- **Who asks** is the card's certificate that nginx verified. nginx hands the service two headers on every request -
  `X-SSL-Client-Verify` (`SUCCESS`) and `X-SSL-Client-S-DN` (the certificate's subject) - set by nginx over anything
  the browser sent. The person is the subject's name (CN): `DOE.JANE.Q.1234567890`. **Anyone whose certificate your
  nginx accepts may ask** (with DoD CAs: every card holder): it only reads, and every question is kept with who asked.
  No certificate, or one nginx did not accept: the page says so and answers nothing.
- **What the job does on the server** - and only this: the folder `/etc/vmware-ask`, the container
  `vmware-ask-app` with its unit file (`/etc/containers/systemd/vmware-ask-app.container`, and
  `vmware-ask-data.volume`), its volume `vmware-ask-data`, the podman secrets `vmware-ask-vcenter` and
  `vmware-ask-genai`. Then `systemctl daemon-reload`, so systemd reads the new unit file: that reads every unit file
  again - STIG Manager's too - but starts, stops or restarts nothing but `vmware-ask-app`. With the *Container
  registry login (hosts)* credential, it also logs root's podman in to that registry (`podman login`), which replaces
  a login saved for that same registry. **It never changes STIG Manager**: not its nginx, its files, its network, its
  containers or their units. It only reads the network's DNS address. The lines nginx needs are written to
  `/etc/vmware-ask/nginx-ask.conf`, and **you** add them (step 4), once.
- **STIG Manager does not need the service.** nginx looks the service up when a request for `/ask/` comes, not
  when it starts: if the service is stopped, or the server restarts before it, nginx starts and STIG Manager works
  as always; only `/ask/` answers *502* until the service is back.
- **The service runs in your execution environment image** (the all-in-one EE: [EXECUTION_ENVIRONMENT.md](EXECUTION_ENVIRONMENT.md)).
  Python, ansible-core and pyVmomi are in it already, so nothing is installed on the server and nothing is downloaded
  when it starts.
- **vCenter is read every 10 minutes** (`vmware_ask_refresh_minutes`), with everything a question may need: VMs
  with their disks and events, hosts, clusters, datastores, snapshots (and who took them), triggered alarms. Each
  answer is from the last read, and says when that was. The page's header says it too, with any vCenter that could
  not be read. A read that fails keeps the last good one, and says so.
- **Several vCenters**: the credential's, and `vmware_vcenters` (`vmware_ask_vcenters`), with the same account, as
  for the VMware reports.
- **No key, or GenAI not reachable**: the job's own reader answers the simplest questions (*VMs with dbv in the
  name*, *hosts in maintenance mode*), and says so on the answer.

## Set it up

Five steps. Steps 1 and 2 only look; step 3 is the job; step 4 is the one change to STIG Manager's nginx, by hand.

### 1. Look at STIG Manager's nginx

On the STIG Manager server (`sudo` throughout: STIG Manager's containers are root's):

```bash
sudo podman ps --format "{{.Names}}  {{.Networks}}  {{.Ports}}"
```

Note the **nginx container's name** (the one with `443` in its ports) and its **network**. Then check the network
has DNS - its containers find each other by name:

```bash
sudo podman network inspect <the network> --format "{{.DNSEnabled}}"      # must say: true
```

`true`: good. `false`, or the network is `podman` (podman's default, without DNS) or `host`: stop here - the
page needs a network with DNS; ask before changing STIG Manager's.

Find the nginx config file on the server, the one with STIG Manager's `server { ... }` block:

```bash
sudo podman inspect <nginx> --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
```

The line ending in `/etc/nginx/conf.d/default.conf` (or another `.conf`) is it - the left side is the file on the
server. Open it, read only, and check its `server` block has:

- `ssl_client_certificate` (your CA bundle: the DoD CAs) and **`ssl_verify_client on`** or **`optional`** - the
  mTLS. Without them nginx checks no card, and the page refuses everyone (*No client certificate was accepted (NONE)*).
- a `location / { ... }` - STIG Manager. The page's lines go just above it.

### 2. The server, in the inventory

It is the STIG Manager server: it can already reach your automation hub, and needs to reach

- vCenter on 443 (each of them),
- the GenAI API (`https://api.genai.mil` - or through your proxy: `site_act_env: {HTTPS_PROXY: http://proxy.yoursite.mil:8080}`).

Put it in your inventory in the group `vmware_ask` (or give the job `target: <its name>`). Its podman must be 4.4 or
later (RHEL 9.2+ / 8.8+): it is already, if STIG Manager runs with Quadlet.

### 3. The job template

*Templates > Add > Job template*:

- **Name:** `VMware ask - deploy`
- **Inventory:** the one with the server. **Limit:** the server.
- **Playbook:** `playbooks/vmware_ask_deploy.yml`
- **Credentials:** Machine (ssh and sudo on the server), VMware vCenter (the read-only account), ACT model key;
  Container registry login (hosts) when the image needs a login to pull.
- **Variables:** copy [`aap/extra_vars/vmware_ask_deploy.yml`](../aap/extra_vars/vmware_ask_deploy.yml) and set at
  least:

```yaml
vmware_ask_network: stigman              # step 1: the network of STIG Manager's nginx container
vmware_ask_image: hub.yoursite.mil/site-ee-all:1
vmware_ask_public_url: https://stigman.yoursite.mil/ask/
```

GenAI's settings are ACT's, as for every job: `site_act_provider`, `site_act_models`, `site_act_urls`
(roles/site_act/defaults/main.yml). `vm_ask_extra_instructions` (your naming) applies here too.

Launch it with Job type **Check** first: it lists what it would write - only `/etc/vmware-ask`, the
`vmware-ask-*` unit files and secrets - and writes, starts and makes nothing (tested on a server with nothing of it
yet). Then **Run**. It starts the service, checks it answers,
and its last task shows **the lines to add to nginx** (also in `/etc/vmware-ask/nginx-ask.conf` on the server).

**After an update of this repository**, run the template again: it copies the service and the job's engine, and
restarts the service if they changed. A second run with nothing changed changes nothing. nginx's lines stay as they
are (they change only if you change `vmware_ask_path` or STIG Manager's network: the job then says so).

### 4. Add /ask/ to your nginx

Once, by hand, on the STIG Manager server, with the file and the container name from step 1:

1. **Keep a copy:** `sudo cp <the file> <the file>.before-ask`
2. **Paste** the lines of `/etc/vmware-ask/nginx-ask.conf` (`sudo cat /etc/vmware-ask/nginx-ask.conf`) **inside**
   STIG Manager's `server { ... }` block, just **above** its `location / {`. Edit the file **in place** - `sudo nano`
   does; in vim first `:set backupcopy=yes` - because the container sees that one file: an editor that saves a new
   file in its place leaves nginx reading the old one. They are two `location` blocks:

```nginx
location = /ask {
    absolute_redirect off;
    return 301 /ask/;
}
location /ask/ {
    resolver 192.0.2.1 valid=30s ipv6=off;   # the podman network stigman's DNS
    set $vmware_ask_app http://vmware-ask-app:8080;
    rewrite ^/ask/(.*)$ /$1 break;
    proxy_pass $vmware_ask_app;
    proxy_http_version 1.1;
    proxy_set_header Host                $host;
    proxy_set_header X-Real-IP           $remote_addr;
    proxy_set_header X-Forwarded-Proto   https;
    proxy_set_header X-SSL-Client-Verify $ssl_client_verify;
    proxy_set_header X-SSL-Client-S-DN   $ssl_client_s_dn;
    proxy_read_timeout 60s;
    client_max_body_size 64k;
}
```

   (Use the file's lines, not these: its `resolver` is your network's address.)

   Check the container sees them - this must print `1`, with the path from step 1's `->`:
   `sudo podman exec <nginx> grep -c 'location /ask/' /etc/nginx/conf.d/default.conf`. A `0`: the editor saved a new
   file - put the copy back, edit again in place.
3. **Test it:** `sudo podman exec <nginx> nginx -t` - it must say *syntax is ok* and *test is successful*. Anything
   else: put the copy back (`sudo cp <the file>.before-ask <the file>`), and nothing has changed.
4. **Load it:** `sudo podman exec <nginx> nginx -s reload`. nginx reloads without dropping anyone: STIG Manager
   keeps working, and its sessions too.

**To take it out again:** put the copy back, `nginx -t`, `nginx -s reload`; then, if you like,
`sudo systemctl stop vmware-ask-app` (and remove `/etc/containers/systemd/vmware-ask-*` and `/etc/vmware-ask`).

**If STIG Manager's nginx file is written by a job** - this repository's *STIG Manager - deploy* writes it on each
run (*Managed by Ansible*) - that run takes the lines out again: paste them again after it (steps 1-4), or ask for
them to be added to that job.

### 5. Try it

Open `https://stigman.yoursite.mil/ask/` with your card, and ask *all VMs with dbv in the name* (or a part of a name
you know). The header shows your name from the card. The answer says *Read by: GenAI (genai, ...)*. If it says
*the job's own reader*, its note says why GenAI was not used. STIG Manager, at `/`, is as it was.

**Browsers and a web proxy.** The page is a path on STIG Manager's own name, so it goes wherever STIG Manager goes:
if your browsers reach STIG Manager without the web proxy (its name on the proxy bypass list), the page does too.

## Who asked what

Every question is kept with who asked it (the name on the card), from which address, when, how it was read and what
came of it:

- **in the journal**, one JSON line each: `journalctl -u vmware-ask-app` (or `podman logs vmware-ask-app`).
  Requests refused for want of a certificate are there too (*refused*, with the reason). Forward it to your SIEM with
  the rest of the server's journal.
- **in a database** on the server, `ask.db` in the volume `vmware-ask-data`, for `vmware_ask_keep_days` (365):

```bash
sudo sqlite3 "$(sudo podman volume inspect vmware-ask-data --format '{{.Mountpoint}}')/ask.db" \
  "select at, user, question, status, total from asks order by at desc limit 20"
```

## Settings

Every setting, with its default: [`aap/extra_vars/vmware_ask_deploy.yml`](../aap/extra_vars/vmware_ask_deploy.yml)
and [VARIABLES_REFERENCE.md](VARIABLES_REFERENCE.md). The ones you are likely to change:

| Setting | Default | What it does |
|---|---|---|
| `vmware_ask_network` | | the podman network of STIG Manager's nginx container (required; it must exist, with DNS) |
| `vmware_ask_image` | | your execution environment image (the all-in-one EE) |
| `vmware_ask_path` | `/ask/` | the page's path on nginx (the lines' `location`) |
| `vmware_ask_public_url` | `https://<the server>/ask/` | the page's address, as people open it (said at the end, logged) |
| `vmware_ask_classification` | `U` | the banner: U, CUI, C, S, TS, SCI, NONE |
| `vmware_ask_refresh_minutes` | 10 | how often vCenter is read |
| `vmware_ask_genai` | true | false = only the job's own reader (simple questions) |
| `vmware_ask_max_rows` | 500 | rows shown on the page (the CSV has every row) |
| `vmware_ask_keep_days` | 365 | how long who asked what is kept (0 = for ever) |
| `vmware_ask_per_user` | 2 | questions one person may have running at once |
| `vmware_ask_trust_host_cas` | true | the service trusts the CAs the server trusts (`/etc/pki/ca-trust`): for vCenter and GenAI |

## When something is wrong

| What you see | What to do |
|---|---|
| The job stops: *Set vmware_ask_network to the podman network ...*, or *... does not exist*, or *... has no DNS* | step 1: the network of STIG Manager's nginx container, as `podman ps` names it |
| `nginx -t` fails | put the copy back (step 4). *unknown directive* or *"location" directive is not allowed here*: the lines went outside the `server { }` block, or inside another `location` |
| `/ask/` says *502 Bad Gateway* | the service is not running: `sudo systemctl status vmware-ask-app`, `journalctl -u vmware-ask-app`. Or it is on another network than nginx: run the job again with the network nginx is on |
| *404 Not Found* for `/ask/` | the lines are not loaded: in the right file? `nginx -s reload` done? Does the container see them (step 4's `grep -c`)? A `0`: the editor saved a new file - edit it in place and reload |
| *No client certificate was accepted (NONE)* | nginx asked for no certificate: its `server` block needs `ssl_verify_client on` (or `optional`) and `ssl_client_certificate`. Or the browser sent none: card in the reader, pick its authentication certificate (not the e-mail one); close the browser and try again |
| *No client certificate was accepted (FAILED: ...)* | nginx did not accept the certificate: expired, revoked, or of a CA not in `ssl_client_certificate` |
| *vCenter has not been read yet*, or the header says the last read failed | `journalctl -u vmware-ask-app`: the `vcenter_read` line says why. A wrong password: update the credential and run the template again. A certificate not trusted: put its CA in the server's trust (`update-ca-trust`) and run the template again. |
| *Not in this answer - not read: vc02 ...* | that vCenter could not be read at the last read; the others were |
| *GenAI did not run: ...* on an answer | the note says why: no key (attach the ACT model key credential and run the template again), the URL not reachable from the server (firewall, proxy: `site_act_env`), a certificate |
| *Refused: this job only reads vCenter* | the question asks for a change; ask what you want to see |

## Not yet

- **Tried on a real vCenter, STIG Manager and GenAI.MIL?** Not yet: it was tested end to end on podman with vcsim
  (the vCenter simulator), an nginx with client certificates standing in for STIG Manager's (the lines of step 4 in
  its `server` block), the real ACT with a stand-in GenAI API, and an execution environment built from
  `ee/all-in-one` - as a user (rootless podman, `vmware_ask_scope: user`), not yet as root, as on your server
  (`vmware_ask_scope: system`, the default: the same tasks with `/etc/containers/systemd` and `systemctl`). Try it on
  a test server first, or run step 3 as Check and read what it would do.
- **Follow-up questions.** Each question stands alone: *now only the powered-off ones* does not know the question
  before. Use *Edit and ask again*.
- **Answers are as of the last read** - up to `vmware_ask_refresh_minutes` old. For an answer read live, run the
  job *VM - ask a question*.
