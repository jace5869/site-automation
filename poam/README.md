# POA&M list for `playbooks/poam_status.yml`

`poam.csv` holds your Plan of Action & Milestones items, one row per item. The rows here are
EXAMPLES - replace them with yours. POA&M data is sensitive: keep this repository
access-controlled (your work Git, never a public place).

## Columns

| Column (default name) | What | Needed |
|---|---|---|
| `POAM ID` | the item's number (eMASS POA&M ID) | yes |
| `Weakness` | what is wrong | recommended |
| `Severity` | CAT I / CAT II / CAT III (or High / Medium / Low) | recommended |
| `Status` | Ongoing, Completed, Risk Accepted ... Items with a status in `poam_closed_statuses` are ignored | yes |
| `Scheduled Completion Date` | `2026-12-31`, `12/31/2026`, `31-Dec-2026` or `Dec 31, 2026` | yes |
| `Owner` | person or office | optional |
| `Vulnerability IDs` | the STIG group IDs the item covers (`V-123456`, several allowed) - used by the STIG Manager cross-check | optional |

Your file can have more columns (they are ignored) and different names: set `poam_columns` in
the inventory (`group_vars/all.yml`) to map them, e.g.

```yaml
poam_columns:
  id: POA&M Item ID
  weakness: Control Vulnerability Description
  severity: Raw Severity
  status: Status
  due: Scheduled Completion Date
  owner: Office/Org
  vuln_ids: Security Checks
```

## From eMASS

1. Export the POA&M from eMASS (Excel).
2. Delete the title rows above the header row, so row 1 is the column names.
3. **File > Save As > CSV (Comma delimited)** as `poam/poam.csv`.
4. Commit and push; the AAP project sync picks it up before the next run.
