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

2. **The OCSP responders** (optional). With a test certificate the responder knows and the CA that
   issued it (PEM files in your repository under `playbooks/`; a certificate is not a secret), each
   host sends a real OCSP request - made once on the AAP side - and the answer is read there:

   ```yaml
   net_perf_ocsp:
     - {name: OCSP, url: "http://ocsp.example.mil", cert: files/ocsp/test-user.pem, issuer: files/ocsp/issuing-ca.pem}
   ```

   With a `url` only, the responder must answer HTTP at all.
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

## Not in this version

- vCenter's own counters (dropped packets per VM and per host, `net.droppedRx` / `net.droppedTx`):
  planned for the VMware health report.
- Paths between two hosts other than iperf3 pairs and the targets: add the other host as a target.
