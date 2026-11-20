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

- **World**, as a **Globe** or **Flat**. The buttons are in the map's top bar. Your choice is kept in your browser; the
  site's default is `netmap_view`. Switching unrolls the globe into the flat map.
- **Sites** are dots sized by their number of nodes, coloured by their worst status, with a mark (a check, !, × or ?).
- **WAN links** are arcs: width = bandwidth, colour = how busy (one blue scale), dashes that move both ways (in and
  out) at the speed of their traffic. A link that is down is red, dashed and still. Point at one for its numbers.
- **Drag** to turn, the **wheel** or **+/−** to zoom. **Click a site** to fly in and open it.
- **Unplaced:** sites without coordinates wait in a tray at the top left; a click opens them.
- **Sites view:** each site as a bubble; opened, it shows every node: the core in the middle, firewalls and
  distribution switches around it, access switches around their distribution switch. *Only down*, *Changes*, *Site*,
  *Vendor*, *Find a node or IP*, *Expand all*. Click a node for its details and neighbours.
- **Motion:** `report_motion` (auto, always, off). Off: nothing turns or moves, and the switch and the fly-in are jumps.

A node's **role** (core, distribution, access, firewall, wireless controller) is worked out from the topology (the nodes
with WAN links are the core) and from the vendor and model. SolarWinds does not store it.

### Live on the portal

With a SolarWinds credential on the portal's deploy, the site portal has a **Network map** page (behind the card login).
The portal's server reads SolarWinds every `netmap_live_seconds` (60; 30 at least) while someone has the page open.
That is **one read for everyone**, never one per viewer. After 5 minutes with nobody on the page it stops reading, until
the next person opens it.

The page shows:
- what changed, as it happens: a node that goes down pulses red, and a link that drops stops and turns red;
- the dashes' speed following each link's traffic;
- *updated N s ago*;
- a toast for each change.

A read that fails keeps the last map, says why, and tries again later. `portal_motion` applies.

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

## How a site gets its place

**Which site a node is in:** `netmap_site_by`.
- With `Location`, the default, it is the node's Location up to the first comma (`netmap_site_split`), so
  `Site A, building 1, IDF 2` is `Site A`.
- Or give the name of a node custom property (e.g. `Site`); its value is the site.
- A node with neither is on the site `No site`.

**Where the site is**, in this order:

1. **SolarWinds' Worldwide Map** (`Orion.WorldMap.Point`): the points you placed for the site's nodes there. The site
   sits in their middle. Nothing to set.
2. **Two node custom properties** with the latitude and longitude: name them in `netmap_lat_property` and
   `netmap_lon_property` (e.g. `Latitude`, `Longitude`).
3. **`netmap_sites`** in all.yml: `{site: {lat, lon}}`, by the site's name as the map shows it (any case).

A site with none of these is **unplaced**:
- it waits in the globe's *Unplaced* tray;
- it is listed in the report;
- the Sites view and the tables have it like any other site.

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
| `netmap_site_by` | `Location` | which node field is its site: Location, or a custom property's name |
| `netmap_site_split` | `","` | the site is the text before this; `""` = the whole value |
| `netmap_lat_property` / `netmap_lon_property` | `""` | custom properties with the latitude and longitude |
| `netmap_sites` | `{}` | `{site: {lat, lon}}` for sites SolarWinds has no place for |
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
| `portal_netmap` | `auto` | the portal's live page: auto (with a SolarWinds credential), true, false |
| `netmap_live_seconds` | `60` | how often the portal reads SolarWinds (30 at least) |
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
| `the SolarWinds address is not a server name` | the credential's address has a login in it, or is not a name: only the server, the login is the credential's |
| `SolarWinds not read: ...` (any reason) | the email says so; the report website is not written that run, so its latest page keeps the last good map; nothing is kept for "what changed" |
| `netmap_solarwinds_url has a user name or password in it` | give only the server; the job stops before anything is read, and the address is not repeated |
| `the links: this Orion has neither Orion.TopologyConnections nor Orion.TopologyData` | NPM's topology is not there: the map shows the nodes without links |
| `Unplaced sites (N)` | give them coordinates (*How a site gets its place*) |
| `not compared: ...` | attach the AAP credential (*What changed*); the first run is never compared |
| the portal: `The network map is off here: ...` | the deploy had no SolarWinds credential, or `portal_netmap: false` |
| the portal: `not read again: ...` | the live read failed (or the links could not be read whole): the reason follows; the map shown is the last good one |
