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
  cash-gate line (counts only, never dates or descriptions). The window
  starts **60 days before the Date of Last Statement** (or **160 days before
  the statement date** when no last-statement date is printed) so back-dated
  rows RBC prints inside a monthly statement — spin-off distributions and
  cash-in-lieu credits printed "AS OF" the previous month — are kept; this
  bound is deliberately wider than the 1–100-day statement-date gap check.
  The upper bound is unchanged: rows dated after the statement date (e.g.
  future-settlement trades) are still dropped (plan `vl-window-backdated`).
- A DIVIDEND row may carry both the gross CREDIT and the NRT withholding
  DEBIT on the same row. Both amounts are kept and the cash gate nets them.
- The VL parser never moves an amount between the DEBIT and CREDIT columns —
  amounts stay in the column they are printed in, and the cash gate catches
  a column the model read wrongly (plan `vl-interest-columns`). Only activity
  labels may be corrected:
  - A `DEREGISTRATION FROM ACCOUNT` credit is money into this account from a
    deregistered RBC account → labelled `TRANSFER` (inter-account transfer
    in), amount untouched.
  - Interest on the cash balance (`INT FR … THRU … @…% BAL … AVBAL …`) keeps
    its printed amount: a pure DEBIT is interest charged on a negative
    balance → `INTEREST_PAID`; a CREDIT (or both columns, e.g. gross +
    charge) stays `INTEREST_RECEIVED` and the gate nets it. Only the RBC
    savings-fund rows (`INVESTMENT SAVINGS`, reinvested at the fixed rate,
    net cash 0) have their amounts cleared as before.
- Short option holdings (e.g. `CALL NVDA 07/30/21 210`) have no separate
  symbol; a trailing `-` in quantity, book cost or market value means
  negative, and the holdings self-check applies a ×100 contract multiplier
  to such rows (signed comparison). The full CALL/PUT pattern (underlying,
  expiry date, strike) must start the security name, but extra text may
  follow the strike (VL sometimes appends the underlying's name);
  names that merely start with CALL or PUT (CALLAWAY, PUTNAM) are not
  options.

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
- `--dry-run` — parse files without writing to the database (no BrokerStatement, no FileImportLog, no file moves); duplicates are checked against the DB read-only

Both real RBC export layouts are supported; columns are matched **by header name, never by position**:

- **Old layout (2017–2019), 9 columns:** `Date, Activity, Symbol, Quantity, Price, Settlement Date, Account, Value, Currency`
- **New layout, 11 columns:** adds `Symbol Description` after `Symbol` and `Description` at the end (`Symbol Description` is ignored; `Description` is stored as scrubbed notes)

Behaviour:

- **Dates** use one shared parser (`parse_csv_date`): `31-Dec-18`, `31-Dec-2018`, `Dec 31, 2018`, `December 31, 2018`. The **Settlement Date is mandatory** — a missing or unreadable cell rejects the row (`Missing settlement date` / `Invalid settlement date: …`), it never becomes NULL.
- **Unknown activity types reject the row** (`Unknown activity type: <raw>`); no `TransactionType` rows are auto-created from RBC exports. (The IB path keeps its auto-create behaviour.)
- **Row accounting:** every data row of every file is accounted for — `data rows = imported + duplicates + practice + rejected`. The sum is printed per file; a mismatch **fails that whole file atomically** (nothing stored, `FileImportLog` status `error` with the counts as the reason) and the run continues with the next file. Rejections are printed grouped by reason.
- **Dedup / `tx_hash`:** the hash uses only fields present in both layouts — date, settlement date, activity/type, symbol, quantity, price, value, currency, plus account — never Symbol Description or Description. It excludes `amount` for BUY/SELL (overlapping exports with slightly different amounts still match). Identical rows within one file get a within-file sequence (`seq` 1, 2, …) so both are imported with different hashes; the same row in two overlapping exports gets `seq` 1 in each and the second is skipped as a duplicate.
- **`--dry-run` report:** per account name / type / YYYY-MM it prints the rows that would be imported and the duplicates that would be skipped (checked against the DB), rejections grouped by reason, and **near-duplicates across files** — rows in different files with the same account, date, symbol and value that differ in another field (listed by file + row number + which field differs). Near-duplicates are reported only; they block nothing. Output uses account names, dates and counts — never Account column values.

## Watcher

### `watch_inbox`

Standalone polling daemon. Not needed when running `runserver` (the watcher starts automatically).

```powershell
python manage.py watch_inbox --interval 300
```

## Statement Generation (from imported data)

| Command | Purpose |
|---|---|
| `generate_statements` | One forward generator for `CoreMonthlyStatement` rows (anchored on the stored RBC PDF statements + CSV transactions). Dry run by default; `--apply` writes after the built-in PASS-vs-PDF check, replacing the selected accounts' rows in one transaction |
| `generate_annual_reports` | Generate `CoreAnnualReport` from monthly statements |
| `reconcile` | Compare CSV vs RBC data |

## Data Quality

| Command | Purpose |
|---|---|
| `audit_rbc_data` | Check RBC monthly data integrity |
| `audit_fx` | Audit/repair `amount_cad` on transactions |
| `merge_duplicate_accounts` | Merge auto-created duplicate accounts |
| `clean_misclassified_annuals` | Delete RbcMonthly records created from annual report PDFs |
| `dedupe_monthly_statements` | Report duplicate RbcMonthly pairs (same account/date/currency); `--apply` deletes the higher-id row of each identical pair |

## Exceptions page

The exceptions stored by `monthly_check --store` (`MonthlyException`) are browsable on three pages (logged-in users only; a navbar badge shows the open count):

| URL | What it shows |
|---|---|
| `/exceptions/` | All exceptions, filterable by account, exception type (GET param `kind`), status, year and statement month (`month=YYYY-MM`; invalid values are ignored). Ordered open → accepted → resolved, taxable accounts (`category = TAXABLE`) first. |
| `/exceptions/<id>/` | One exception with its stored detail verbatim. **Accept** (only when open) and **Reopen** (only when accepted) forms; both require a reason. Accepting/reopening changes only the exception row — never transactions, statements or results. |
| `/exceptions/coverage/` | Coverage grid: accounts × statement dates, one combined cell per stored statement. Letters: `P` PASS, `F` FAIL, `A` ACCEPTED (verdict FAIL with all check exceptions accepted), `1` FIRST, `–` NO_CSV, `·` no stored result (gap), `i` IMPORT exception on that date. |

Bulk accept/reopen is still the command (`monthly_check --accept` / `--reopen`, same rules as the page forms). Background: `docs/plans/exceptions-page.md`, `docs/plans/monthly-check-store.md`.

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
