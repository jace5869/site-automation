# Network performance: is the network slow, and where?

"The network is slow" is hard to act on. The **Network performance report** measures it from where it
matters - each server - to what that server depends on, and compares it with the server's own usual:

- **latency, jitter and loss** to each target (ping), and the time to open a target's port (a timed
  TCP connect) - from every Linux host of the job;
- the **OCSP responders**, asked a real question from each host: smart card logins and certificate
  checks fail when they do not answer;
- each host's **interfaces**: packets dropped and errors since the last run, per hour;
- the **trend**: each path against the median of the host's last runs (kept on the host), so "3 ms,
  usually 1 ms" stands out even below the thresholds;
- **throughput** with iperf3, only between the pairs you name, one at a time, for a few seconds.

Read-only, but for the few files of each host's own history (`net_perf_dir`). The hosts with a
finding fail (`site_fail_on`), so a workflow can open tickets; the report is emailed and published
like every other report.

## What it reports

| Finding | Severity | When |
|---|---|---|
| `netperf:loss:<target>` | warning / critical | packet loss from `net_perf_loss_warn_pct` (1 %) / `net_perf_loss_crit_pct` (10 %); 100 % = the target does not answer |
| `netperf:rtt:<target>` | warning / critical | the average round trip from `net_perf_rtt_warn_ms` (50) / `net_perf_rtt_crit_ms` (200) |
| `netperf:tcp:<target>` | critical / warning | the target's port refuses every connect / some of them |
| `netperf:slower:<target>` | warning | `net_perf_slower_factor` (2x) the host's usual, and at least `net_perf_slower_min_ms` (5) more |
| `netperf:drops:<interface>` | warning | dropped packets (in + out) from `net_perf_drops_warn_per_hour` (100), or errors from `net_perf_errors_warn_per_hour` (10), per hour since the last run |
| `netperf:ocsp:<name>` | `net_perf_ocsp_severity` (critical) | the responder does not answer, answers not successful, says the test certificate is not good, or its answer is stale |
| `netperf:ocsp-slow:<name>` | warning | it answers, slower than `net_perf_ocsp_warn_ms` (2000) |
| `netperf:iperf:<server>` | warning | on the client of a pair: below the pair's `min_mbps`, or the test did not run |

A target far away can have thresholds of its own: `{name: Remote site, host: ..., rtt_warn_ms: 300}`.
An interface's first run (or after a reboot: its counters start over) is counted from then on.

## Set it up

1. **The targets** - in `all.yml` for every host, more per group in its group_vars
   (`net_perf_targets_extra`):

   ```yaml
   net_perf_targets:
     - {name: Gateway, host: 192.0.2.1}
     - {name: DC01, host: dc01.example.mil, port: 389}
     - {name: STIG Manager, host: stigman.example.mil, port: 443}
   ```

2. **The OCSP responders** (optional). Put a certificate the responder knows - any user's, from a
   smart card or the PKI's site - in your repository as a PEM file (it is not a secret), and name it
   once in `all.yml`; the VMware health report uses the same setting:

   ```yaml
   site_ocsp_certs: [files/ocsp/test-user.pem]     # playbooks/files/ocsp/test-user.pem
   ```

   The responder's address and the certificate of the CA that issued it are read from the certificate
   itself (its *Authority Information Access*: every DoD certificate names its OCSP responder and where
   its CA's certificate is). The CA's certificate is downloaded on the AAP side; when the AAP node
   cannot reach that address, put the CA's certificate after the user's in the same file (or give it
   as `issuer`). Each host then sends a real OCSP request - made once on the AAP side - and the answer
   is read there. `net_perf_ocsp`, when set, is used instead - to name a responder, give its address,
   or ask a second one:

   ```yaml
   net_perf_ocsp:
     - {name: DISA OCSP, cert: files/ocsp/test-user.pem}           # the address and issuer from the certificate
     - {name: OCSP 2, url: "http://ocsp2.example.mil", cert: files/ocsp/test-user.pem, issuer: files/ocsp/issuing-ca.pem}
   ```

   With a `url` only, each host sends it a HEAD request (what `curl -I` sends): any HTTP answer is the
   responder up, but 502, 503 or 504 (a proxy or load balancer saying the server behind it is down).
3. **iperf3** (optional): `iperf3` installed on both hosts of a pair, TCP port 5201 open on the server.
   Both hosts must be in the job; the server answers one test and exits.

   ```yaml
   net_perf_iperf_pairs:
     - {server: app01.example.mil, client: db01.example.mil, min_mbps: 900}
   ```

4. **The template** (docs/SETUP_AAP.md, "Network performance report"): `playbooks/net_perf_report.yml`,
   the machine credential, `target:` the group to measure from. Run it as **Check** first (it measures
   and keeps nothing), then schedule it - hourly gives the trend something to compare with. With tickets:
   workflow 34 ([WORKFLOWS_AND_SCHEDULES.md](WORKFLOWS_AND_SCHEDULES.md#34-network-performance-hourly)).

## On the host

`net_perf_dir` (`/var/lib/site-automation/netperf`, root-only): `history.jsonl` (each run's average round
trip and loss per target; the last `net_perf_keep`, 30) and `counters.json` (the interfaces' counters at
the last run).

## The hosts' and VMs' drops seen by vCenter

The drops counted here are the Linux guest's own. What the ESXi hosts' physical NICs and every VM's
virtual NIC dropped - Windows VMs too - is in the daily **VM - health report**, section *Network drops and
errors*, from vCenter's own counters: [VMWARE.md](VMWARE.md#network-drops-and-errors).

## Not in this version

- Paths between two hosts other than iperf3 pairs and the targets: add the other host as a target.
