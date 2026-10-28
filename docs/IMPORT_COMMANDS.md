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

**Monthly statement verdicts** (per file; `--dry-run` reports the same
verdicts without writing anything):

| Verdict | Meaning | File goes to |
|---|---|---|
| `imported` | All sections passed the checks and were stored | `processed/` |
| `duplicate` | The file's content (SHA-256) is already stored — nothing new | `processed/` |
| `conflict` | Same account + date + currency already stored with different content — nothing stored (all-or-nothing) | `processed/errors/` |
| `date_error` | Statement date failed sanity (missing, in the future, before 2000, or not 1–100 days after the Date of Last Statement on the same PDF) — nothing stored | `processed/errors/` |
| `reconcile_error` | The VL transactions do not reconcile with the section's cash rows (opening + credits − debits ≠ closing), or the text/VL currency sections don't line up — nothing stored | `processed/errors/` |
| `vl_check_error` | The file fell back to the VL parser and a VL section failed its checks (holdings + cash ≠ end total, unreadable holding values, date problems) — nothing stored. There is no begin-total check: VL's balances often list only cash for a USD section, and the begin total is checked against the previous month's end in step 14 | `processed/errors/` |
| `error` | Anything else: the VL model failed or returned invalid JSON (there is no regex fallback), the document type is unknown, or a section could not be attributed to an account | `processed/errors/` |

**End summary:** after the run, the `Inbox Import Summary` counts files per
verdict — one line each for `imported`, `duplicate`, `conflict`,
`date_error`, `reconcile_error`, `vl_check_error` and `error` — in normal and
`--dry-run` runs, alongside the existing record counts.

**Cash gate print lines:** for every currency section of a monthly statement,
the import prints one line in normal and `--dry-run` runs, e.g.
`[CAD] cash gate: opening 90.00 credits 10.00 debits 0 closing 100.00 diff 0.00 PASS`
(opening cash, sum of credits, sum of debits, closing cash, the difference,
PASS/FAIL). In `--dry-run` runs the reasons of every failed check are printed
as well (field/currency/symbol names only — never account numbers).

**Monthly statement row rules:**
- The "Trades For Future Settlement" table below Account Activity is ignored,
  and any transaction dated outside the statement's own window is dropped
  before the cash gate; the per-section drop count is printed next to the
  cash-gate line (counts only, never dates or descriptions).
- A DIVIDEND row may carry both the gross CREDIT and the NRT withholding
  DEBIT on the same row. Both amounts are kept and the cash gate nets them.
- Short option holdings (e.g. `CALL NVDA 07/30/21 210`) have no separate
  symbol; a trailing `-` in quantity, book cost or market value means
  negative, and the holdings self-check applies a ×100 contract multiplier
  to such rows (signed comparison).

**VL model:** the VL answer limit is `VL_MAX_TOKENS` (default **16384**; set it
in `.env` to override). A truncated VL answer (`finish_reason=length`) is an
error whose reason says `truncated` — raise `VL_MAX_TOKENS` if you see it.

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
