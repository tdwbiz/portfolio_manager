# Import Commands Reference

## Quick Start

| What you want | Command |
|---|---|
| **Import all PDFs from inbox** (annual + monthly) | `manage_inbox_import` |
| **Import Activity CSVs from inbox** | `import_csv` |
| **Import Holdings CSVs into PositionHistory** | `import_csv --holdings` |
| **Watch inbox continuously** | `watch_inbox` |

## PDF Import

### `manage_inbox_import` (PRIMARY — use this)

Classifies each PDF and routes to the correct parser. Handles both annual reports and monthly statements in one pass.

```powershell
python manage.py manage_inbox_import --inbox-dir inbox
```

Options:
- `--inbox-dir` — path to folder containing PDFs (default: `inbox/`)
- `--dry-run` — classify and parse but don't write to database
- `--limit N` — stop after N PDFs (useful for testing)

**How it works:**
1. Checks filename for known patterns ("Annual Report" → annual, "Monthly Statement" → monthly)
2. Falls back to VL model classification if filename doesn't match
3. Routes to the correct parser automatically

## CSV Import

### `import_csv`

Scans inbox for Activity CSVs (RBC) and imports transactions. Idempotent: deduplicates by `tx_hash` and only parses files still in `inbox/`.

```powershell
python manage.py import_csv
```

Options:
- `--holdings` — import Holdings CSVs into `PositionHistory` instead of Activity CSVs
- `--dry-run` — parse files without writing to the database

## Watcher

### `watch_inbox`

Standalone polling daemon. Not needed when running `runserver` (the watcher starts automatically).

```powershell
python manage.py watch_inbox --interval 300
```

## Statement Generation (from imported data)

| Command | Purpose |
|---|---|
| `generate_monthly_statements` | Generate `CoreMonthlyStatement` from transactions |
| `generate_annual_reports` | Generate `CoreAnnualReport` from monthly statements |
| `batch_generate_statements` | Anchor from RBC data, fill gaps, generate all |
| `reconcile` | Compare CSV vs RBC data |

## Data Quality

| Command | Purpose |
|---|---|
| `audit_rbc_data` | Check RBC monthly data integrity |
| `audit_fx` | Audit/repair `amount_cad` on transactions |
| `merge_duplicate_accounts` | Merge auto-created duplicate accounts |
| `clean_misclassified_annuals` | Delete RbcMonthly records created from annual report PDFs |
| `dedupe_monthly_statements` | Report duplicate RbcMonthly pairs (same account/date/currency); `--apply` deletes the higher-id row of each identical pair |

## Workflow

```
Drop PDFs/CSVs in inbox/
         │
         ▼
┌─────────────────────┐
│ manage_inbox_import  │  ← ONE COMMAND handles everything
│   (PDFs + CSVs)      │
└────────┬────────────┘
         │
    ┌────┴────┐
    ▼         ▼
 RbcAnnual  RbcMonthly
 (annual)   + RbcMonthlyTransaction
            + RbcMonthlyBalance
            + RbcMonthlyHolding
```
