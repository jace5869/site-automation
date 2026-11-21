# Network map (SolarWinds)

One job, *Network map* (`playbooks/netmap_report.yml`), reads SolarWinds Orion and shows your network as a map: the
sites on a globe (or a flat map of the world) with the WAN links between them, and every node and link of each site.
It emails a short summary with a workbook, and puts the map on the report website. The same map, live, is a page of the
site portal (*Network map*, below).

**It only reads.** It talks to SolarWinds' **SWIS REST API** (port 17774, **HTTPS only**) with a **read-only** account and sends SWQL
`SELECT` queries only. The code refuses anything else, and it names every column it reads. It never asks for the SNMP
community strings or the license key that some of these tables also hold. It runs on the AAP side: no inventory, no
Limit.

## What you get

### The email (plain)

The subject says what matters: `Network map: 1 down, 14 warning - 9 sites, 1494 nodes - 1 WAN link down`.

- **At a glance:** Nodes, Down, Warning, WAN links down, Changed (since the last run), Unplaced sites.
- **Down or warning:** each node SolarWinds marks down or warning, the worst first, with what SolarWinds says.
- **WAN links:** the links between two sites, the worst and the busiest first: bandwidth, % in, % out, status.
- **What changed since the last run:** new and missing nodes and links, and status changes (`Up → Down`).
- **Sites:** each site, its nodes, how many are down or warning, and where it is on the map (and from where).
- **Unplaced sites:** sites with no coordinates (below: how to give them).
- **Not all read:** what SolarWinds would not give (a custom property it does not have, no topology), the rest in.

The workbook (`network-map_2026-11-20.xlsx`) has every row, nothing cut:

| Sheet | What |
|---|---|
| Nodes | every node: status, site, role, IP, vendor, model, location, response time, what SolarWinds says, the change, NodeID, your `netmap_properties` |
| Links | every link: both ends and their interfaces, the sites, WAN or not, speed, % in and out, status, the change |
| WAN links | the links between two sites |
| Sites | each site, its counts, latitude, longitude, and what placed it |
| Changes | what changed since the last run |

### The map (the report website)

The website page has the same tables and, first, **the map**, in the website's own looks `atlas` (the default), `brief`
and `ops` (`report_web_theme`; the email looks show the tables only, and the job says so). It works offline and under a
strict Content-Security-Policy (no inline script, no outside font or script).

**One level at a time (1.95.0)** - never every node at once:

- **World:** the sites as **tiles**, worst first: devices, buildings, the worst status (a mark and a word), the WAN link
  (a hub says how many sites it links). Beside them, **Links between sites**: counted, down first, then the busiest.
  When most sites have a place (below), the World is the **Globe** or **Flat** map instead; *Tiles / Map* switches. On
  the map, sites on one spot share one label (the worst site and "+N") and a count; a label with no free place is left
  off (down and warning sites are labeled first); pointing at a dot names it and the sites beside it.
- **Site:** its **buildings** as cards (devices, closets, core, distribution and access), and the links between two
  buildings **counted** on one line (red: one is down). Its WAN links, per site at the other end.
- **Building:** its devices **in tiers**: the edge (routers, firewalls), the core, servers and wireless, then each
  **closet** - a distribution switch over its access switches - packed in rows (never wider than the page). A closet
  with more than 18 switches shows 18 - its down and warning ones always among them - and **Show all N**. The lines run
  in the gaps between rows, a closet's own on its left edge, the rest in the left gutter and a channel above each row
  of closets: no line crosses a device or another closet.
- **Device:** its links' interfaces (those on SolarWinds' topology) and the neighbour at the other end; a neighbour
  opens its own.

**The bar** - one slim row:
- the **breadcrumbs** (World › site › building › device): a click goes back up;
- **one search**: a site, building, device or IP; the matches grouped by level; arrow keys and Enter;
- three chips: **Down**, **Warnings**, **Changed** (Down jumps straight to the site when only one has it);
- **Filters**: role and vendor (a count on the button when on; *Clear*).

No step goes into the browser's history: **Back** leaves the page. **Motion:** `report_motion` (auto, always, off).

### Live on the portal

With a SolarWinds credential on the portal's deploy, the site portal has a **Network map** page (behind the card login),
the same map as the report's.

- **At once:** the page shows the last map the portal's server kept - on disk, so also after a restart - with *as of
  HH:MM · refreshing*. It never waits for SolarWinds.
- **Behind it**, while someone has the page open, the server reads SolarWinds - **one read for everyone**, never one per
  viewer:
  - the **status** (each node and the links' interfaces: 2 or 3 queries) every `netmap_live_seconds` (60; 30 at least);
  - **the whole map** (the nodes, their sites, the links: about 15 queries) every `netmap_full_seconds` (1800; 300 at
    least).
- After 5 minutes with nobody on the page it stops reading, until the next person opens it.
- What changed shows as it happens (a node that goes down turns red), *updated N s ago*, and a toast for each change.
- A read that fails keeps the last map, says why, and tries again later. `portal_motion` applies.

## Before you start

### 1. A read-only account in SolarWinds

In the Orion web console: **Settings > All Settings > Manage Accounts > Add New Account** (an Orion account or a Windows
account), e.g. `svc_swis_ro`.

- **Allow administrator rights:** No.
- **Allow node management rights:** No.
- Everything else as it is.

SWIS uses the web console's accounts and follows their limitations: the map shows what the account may see. A link to a
node the account may not see is left out (the report says how many). Do not give it a limitation if the map should
show every site.

### 2. The network path

The SWIS port, **17774** (Orion 2023.1 and later; older ones use 17778), must be open:
- from the AAP execution nodes, for the report job;
- from the portal's server, for the live page.

### 3. SolarWinds' certificate

Orion's own certificate is self-signed. Choose one:
- put the CA that signed yours in the project (e.g. `playbooks/files/ca/orion-ca.pem`) and set `netmap_ca_path`;
- or set the credential's *CA file*;
- or, on the portal, `portal_solarwinds_ca_file`, a file on the portal server;
- or turn the check off (`netmap_validate_certs: false`).

## Set it up

1. **The credential type.** In AAP: **Automation Execution > Infrastructure > Credential Types > Create**. Name it
   `SolarWinds (read-only)`. Paste *Input configuration* and *Injector configuration* from
   `aap/credential_types/solarwinds.yml`.
2. **The credential.** **Credentials > Create**. Type `SolarWinds (read-only)`. Fill in:
   - *SolarWinds address*: `orion.yoursite.mil`;
   - the user name and password from step 1;
   - *Check SolarWinds' certificate*;
   - and, if you need it, the *CA file*.
3. **Where sites are.** In `playbooks/group_vars/all.yml`, if needed (below, *How a site gets its place*):

   ```yaml
   netmap_sites:
     "Site A": {lat: 39.4, lon: -93.2}
     "Site B": {lat: 34.9, lon: -112.8}
   ```

4. **The job template** *Network map* (`playbooks/netmap_report.yml`, in `aap/job_templates.yml`). Give it:
   - the `SolarWinds (read-only)` credential;
   - for *What changed* and the strip of past runs, the *Red Hat Ansible Automation Platform* credential the reports
     use (its user sees the template's jobs).

   Variables: `aap/extra_vars/netmap_report.yml`. No Limit.
5. **Run it once.** The output has a line per site (`Site A: 299 nodes, 0 down, 0 warning - 39.406, -93.204
   (SolarWinds' Worldwide Map)`). The email and the page follow.
6. **Schedule it** (daily, e.g. 06:00). Each run keeps a copy for the next one (*What changed*).
7. **Live on the portal (optional).** Add the same `SolarWinds (read-only)` credential to the portal's deploy template
   (*Site portal - deploy*) and run it. `portal_netmap: auto` turns the page on when the credential is there.
   - The login becomes a podman secret on the server (`site-portal-swis`): never in a file, the unit or the logs.
   - The menu gets *Network map*.

Success looks like: the email's subject starts with `Network map:`, and the page shows the globe with your sites.

## From the report database (1.96.0)

With the job *SolarWinds - snapshot* running ([REPORT_DATABASE.md](REPORT_DATABASE.md#the-solarwinds-snapshot-1960)),
the map reads the newest snapshot instead of SolarWinds. The run is then a few seconds shorter, and SolarWinds is not
asked at all.

1. Add the **Report database** credential to *Network map*. The reader `report_reader` is enough.
2. Run it. The output says where the read came from:
   - `From the report database: the SolarWinds snapshot of 2026-11-21 10:05 CET (4 min old).`
   - or `From SolarWinds itself: ...` with the reason.

It reads SolarWinds itself when:

- the newest snapshot is older than `solarwinds_db_max_age` (20 minutes), or there is none
- the snapshot is of another SolarWinds address than this template's (1.96.1)
- the snapshot's own read was cut at `solarwinds_snapshot_max_nodes`, or it has more links than `netmap_max_nodes` x 20
  (1.96.1): a read of SolarWinds would give another map
- the snapshot lacks the custom property `netmap_site_by`, `netmap_lat_property` or `netmap_lon_property` names
  (1.96.1): left out by `solarwinds_snapshot_skip_properties`, or not in `solarwinds_snapshot_properties`
- the snapshot lacks a custom property a `netmap_hierarchy` level reads (`from: property`, 1.96.2)
- `solarwinds_db_max_age` is `0`: the database is not asked at all (1.96.2)
- the database cannot be reached
- `netmap_from_db` is `false`

The map is the same either way: the snapshot gives back exactly what a read of SolarWinds gives. Its time is the
snapshot's. With the snapshot, the SolarWinds credential is only needed for that fallback.

## Where a node belongs: site, building, closet (1.95.0)

**Read, not guessed - and said.** The job reads where each node belongs from what should agree (`netmap_site_by: auto`,
the default):
- the node's **name**: its parts (split at `-` `_` `.` and where a digit meets a letter: `SITA0101B02CR01` is `SITA0101`,
  `B02`, `CR01`) and their letter and digit runs;
- its **Location**: its parts (`netmap_site_split`, a comma);
- **every node custom property** (their names from SolarWinds' metadata; `netmap_discover: false`: only
  `netmap_properties`);
- its **IPv4 /16 and /24**;
- the **links**: a site, a building holds together on its own links (sites meet at a hub).

Each level takes the reading that covers the most nodes with a sane number of groups, where the links stay inside a
group and each group holds together. The **closet** is the distribution switch a device hangs under. The **role**
(core, distribution, access, firewall, router, wireless, server, other) comes from the name (CR, DS, AS, FW, RT, WC,
SV ...), else the model, else the number of links.

**The report says how** - the section *Discovery*, last (the workbook's *Discovery* sheet, the job's output):
- the rule chosen a level, how many nodes it covers, its groups, what agrees with it, and **what decided** it;
- the other readings with their scores, the chosen one first;
- every field: how many nodes have a value, how many different values, and five of them - a custom property's
  values only when `netmap_properties` or `netmap_hierarchy` names it or a level reads it (the others: name and counts,
  so a contact, a serial or a note is never published); e-mail addresses, phone numbers and serial-like values never;
- the roles and where each came from; the nodes not placed (on the site *Not placed*).

**Or name it yourself** (all.yml), and the job uses it as it is:

| Setting | e.g. | What |
|---|---|---|
| `netmap_site_pattern` | `'^([A-Z]{4}[0-9]{4})'` | the site is this regular expression's first group in the caption (else the DNS name) |
| `netmap_site_by` | `Location`, `Site` | Location up to the first comma (as before 1.95.0), or a custom property's value |
| `netmap_hierarchy` | `[{level: building, from: location, value: 1}]` | each level: `from` pattern, property, location (its part), subnet (8, 16, 24), part (the name's part), topology (closet), none |
| `netmap_roles` | `{"*-WC??": wireless}` | a role by the name (`*` and `?`) |

A setting that cannot work (a pattern with no group, a level or role not known) stops the job before SolarWinds is
asked, and says which.

**Where a site is**, in this order:

1. **SolarWinds' Worldwide Map** (`Orion.WorldMap.Point`): the points of the site's nodes; the site sits in their middle.
2. **Two node custom properties** with the latitude and longitude: `netmap_lat_property` and `netmap_lon_property`.
3. **`netmap_sites`** in all.yml: `{site: {lat, lon}}` by the site's name (any case), or by a **pattern** with `*` and `?`
   - `{"SITA*": {lat: 38.9, lon: -77.0}}` places every site whose name starts so; the site's own name wins, then the most
   specific pattern.

A site with none of these is **unplaced**: the World shows tiles while most sites are, and the report lists them.

**After the upgrade:** the first run compares with a copy whose sites were read another way. It says so once (*Sites
read a new way*); node and link changes are still compared, a site's own from the next run on.

## What changed since the last run

Each run keeps a compact copy of what it read (the job artifact `netmap_snapshot`: each node's status, name and site,
and each link). The next run reads it back from AAP, from this template's last successful run, and compares. This needs
the *Red Hat Ansible Automation Platform* credential (as `report_history`).

Without it, the report says `not compared` and why; the rest is the same. `netmap_compare: false` turns it off.

**Keep *Network map* out of workflows.** In a workflow, each job's artifacts become the next step's extra variables,
and the kept copy is about 140 KB for 1,500 nodes. If it must be in one, set `netmap_snapshot_links: false`: the copy
keeps only the links' interface ids (about 90 KB), and a missing link then shows its interfaces' ids instead of
their names.

## The report website's copy job

With `report_web_copy: true`, the report job keeps its report and the map's **data** with it - no markup, no script.
The job *Report website - copy* builds the map's page part from its own repository's files and writes the page, map
included, and `site-netmap.js` beside it. A map too big for `report_web_copy_max_kb` is
left out, and the job says so: raise it. 1,500 nodes add about 45 KB to the default 256 KB.

## Settings

| Setting | Default | What |
|---|---|---|
| `netmap_solarwinds_url` | `""` | SolarWinds' address; the credential's wins |
| `netmap_validate_certs` | `true` | check SolarWinds' certificate |
| `netmap_ca_path` | `""` | the CA file it is checked with (a file in the project) |
| `netmap_timeout` | `60` | seconds for one request |
| `netmap_site_by` | `auto` | where a node belongs: auto (read, the Discovery section), Location, or a custom property's name |
| `netmap_site_split` | `","` | Location's parts are split at this |
| `netmap_site_pattern` | `""` | the site: this regular expression's first group in the caption |
| `netmap_hierarchy` | `[]` | each level as you name it (see above) |
| `netmap_roles` | `{}` | a device's role by its name |
| `netmap_discover` | `true` | read every custom property for the discovery |
| `netmap_lat_property` / `netmap_lon_property` | `""` | custom properties with the latitude and longitude |
| `netmap_sites` | `{}` | `{site or pattern: {lat, lon}}` for sites SolarWinds has no place for |
| `netmap_properties` | `[]` | more custom properties shown with each node |
| `netmap_view` | `globe` | the world view's first look: `globe` or `flat` |
| `netmap_link_warn_percent` | `90` | a link this busy (in or out) is Warning; 0 = never by load |
| `report_motion` | `auto` | the map's motion on the report website: auto, always, off |
| `netmap_page_size` | `1000` | rows a request |
| `netmap_max_nodes` | `5000` | nodes read at most |
| `netmap_compare` | `true` | compare with the last run's kept copy |
| `netmap_snapshot_links` | `true` | the copy names each link's interfaces; `false` = ids only (smaller - for a workflow) |
| `netmap_max_rows` | `500` | rows in a table of the email and page (the workbook has all) |
| `netmap_xlsx` | `true` | attach the workbook |
| `netmap_fail` | `false` | mark the job failed when SolarWinds could not be read |
| `netmap_raw` | `false` | print the first nodes and links as read |
| `netmap_from_db` | `true` | read the report database's SolarWinds snapshot first (1.96.0) |
| `solarwinds_db_max_age` | `20` | minutes the snapshot may be old; older: SolarWinds itself. `0`: always SolarWinds itself |
| `portal_netmap` | `auto` | the portal's live page: auto (with a SolarWinds credential), true, false |
| `netmap_live_seconds` | `60` | how often the portal reads the status (30 at least) |
| `netmap_full_seconds` | `1800` | how often the portal reads the whole map (300 at least) |
| `portal_solarwinds_ca_file` | `""` | the CA of SolarWinds' certificate, a file on the portal server |

## What it reads

Every query names its columns (the OrionSDK schema, 2026):

| Entity | Columns |
|---|---|
| `Metadata.Entity` | `FullName` - which of the others this Orion has |
| `Orion.InstalledModule`, `Orion.Engines` | `Name`, `DisplayName`, `Version`; `ServerType`, `EngineVersion` - the version in the report |
| `Orion.Nodes` | `NodeID`, `Caption`, `DNS`, `IPAddress`, `Status`, `StatusDescription`, `Vendor`, `MachineType`, `Location`, `ResponseTime`, `PercentLoss`, `UnManaged`; `CustomProperties.<name>` for the properties you name; `WorldMapPoint.Latitude` / `.Longitude` |
| `Orion.TopologyConnections` (else `Orion.TopologyData`) | `SrcNodeID`, `SrcInterfaceID`, `DestNodeID`, `DestInterfaceID` - NPM's topology |
| `Orion.NPM.Interfaces` | `InterfaceID`, `NodeID`, `Name`, `Caption`, `Speed`, `InPercentUtil`, `OutPercentUtil`, `Inbps`, `Outbps`, `Status`, `OperStatus` - only the interfaces on a link |

Nodes and links are read page by page (`WITH ROWS`), interfaces in chunks. About 15 queries read 1,500 nodes. An answer
bigger than 64 MB is not read (a failed read). Nothing SolarWinds says in an error is repeated: the reports and the
portal say the HTTP status and what to do. A run that could not read the node list keeps nothing; a run that could not
read every link (or was cut by `netmap_max_nodes`) calls nothing missing it did not see.

## When something is wrong

| The report or the job says | What to do |
|---|---|
| `No SolarWinds account: attach a credential of type "SolarWinds (read-only)"` | step 2 and 4 above |
| `SolarWinds refused the login (HTTP 401)` | the user name or password; a Windows account as `DOMAIN\user`; not locked |
| `nothing answered on SolarWinds' SWIS port` | port 17774 from the execution node (or 17778 on Orion before 2023.1: give it in the address) |
| `SolarWinds' certificate is not trusted` | `netmap_ca_path` or the credential's CA file, or `netmap_validate_certs: false` |
| `custom property X: SolarWinds could not run this query (HTTP 400) - is it one of your node custom properties?` | the name in `netmap_*_property` / `netmap_properties` is not one of your node custom properties (the case counts) |
| `the SolarWinds address must be https://` | give the server's name (`orion.yoursite.mil`): the login goes with every query, so plain HTTP is refused |
| `the SolarWinds address is not a server name` | the credential's address has a login in it, or is not a name: only the server, the login is the credential's. 1.96.2: checked before the report database is asked, and no part of the address is repeated |
| `SolarWinds not read: ...` (any reason) | the email says so; the report website is not written that run, so its latest page keeps the last good map; nothing is kept for "what changed" |
| `netmap_solarwinds_url has a user name or password in it` | give only the server; the job stops before anything is read, and the address is not repeated |
| `the links: this Orion has neither Orion.TopologyConnections nor Orion.TopologyData` | NPM's topology is not there: the map shows the nodes without links |
| `Unplaced sites (N)` | give them coordinates (*How a site gets its place*) |
| `not compared: ...` | attach the AAP credential (*What changed*); the first run is never compared |
| `From SolarWinds itself: the newest SolarWinds snapshot is N min old` | the job *SolarWinds - snapshot* has not run lately: its job output says why |
| `From SolarWinds itself: the report database could not be reached` | the *Report database* credential on this template, and the path to port 3309 |
| the portal: `The network map is off here: ...` | the deploy had no SolarWinds credential, or `portal_netmap: false` |
| the portal: `not read again: ...` | the live read failed (or the links could not be read whole): the reason follows; the map shown is the last good one |
