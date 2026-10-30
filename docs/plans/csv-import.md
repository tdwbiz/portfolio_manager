# Plan: fix the RBC Activity CSV import (fix/csv-import)

Phase 4 step 14-0b. Fixes `import_csv` (Activity path) and the code it uses:
`imports/parsers.py` (`parse_rbc_csv`, `_normalize_type`), `imports/auto_import.py`,
`core/models.py` (`compute_tx_hash`) and `core/management/commands/import_csv.py`.

## Goal

The Activity CSV import reads both real export layouts correctly, stores a
settlement date for every stored row, accounts for every data row of every
file (imported + duplicate + practice + rejected = data rows), produces a
hash that matches across overlapping exports, never invents transaction
types, and has a `--dry-run` that reports everything it would do.

Facts from tdw's original exports:

- **Old layout (2017–2019), 9 columns:** `Date, Activity, Symbol, Quantity,
  Price, Settlement Date, Account, Value, Currency` — no Symbol Description,
  no Description. Dates look like `31-Dec-18` in both `Date` and
  `Settlement Date`.
- **New layout, 11 columns:** adds `Symbol Description` after `Symbol` and
  `Description` at the end.
- `csv_coverage` read no settlement dates from the 2018 export and only May
  dates from the 2017/2019 exports — because `parse_rbc_csv` maps columns
  badly (`Settlement Date` contains "date", so the `date` branch wins for
  it depending on order, and the settlement parse only tried `%B %d, %Y`),
  and today every stored `core_transaction.settlement_date` is NULL.

What the import does with each column (both layouts, found **by header
name, never by position**):

| Column | Use |
|---|---|
| Date | `Transaction.date` (reject row if unparseable) |
| Activity | mapped through `_RBC_TYPE_MAP` to a known type code (reject row if unknown) |
| Symbol | `Transaction.symbol` |
| Symbol Description (11-col only) | ignored (not stored) |
| Quantity | `Transaction.quantity` (abs, per existing convention) |
| Price | `Transaction.price`; commission derived as today |
| Settlement Date | `Transaction.settlement_date`, same date parsing as Date; missing/unreadable → reject row with a reason |
| Account | fingerprint lookup → `Account`; `PRAC*` → practice skip; unknown → reject. **Never printed.** |
| Value | `Transaction.amount` (signed CAD/USD per Currency) |
| Currency | `Transaction.currency`, drives `amount_cad` via exchange rate |
| Description (11-col only) | `Transaction.notes` (scrubbed), as today's `notes` |

## What changes

- `imports/parsers.py` — header mapping strictly by name (fix the
  `Settlement Date` vs `Date` collision); one shared `parse_csv_date()`
  handling `31-Dec-18`, `31-Dec-2018`, `Dec 31, 2018`, `December 31, 2018`;
  settlement date required and parsed with it; unknown activity type →
  row rejection (no `TransactionType` auto-create for RBC); a per-file row
  accounting dict (data rows, imported, duplicate, practice, rejected by
  reason); identical rows within one file get a within-file sequence
  (`seq` 1, 2, …) instead of the CSV row number.
- `core/models.py` — `compute_tx_hash()`: drop `row_number`, add `seq`
  (default 1, omitted from the hash when 1). The hash uses only fields
  present in both layouts — date, settlement date, activity/type, symbol,
  quantity, price, value, currency, plus the account and seq — never
  Symbol Description or Description. Update the docstring and the AGENTS.md
  gotcha line.
- `imports/auto_import.py` — use `seq` in the hash; assign `seq` by grouping
  rows with identical hash inputs in file order; return a rich report
  (counts + per-account/type/month breakdown + rejections by reason +
  near-duplicates) instead of the bare 3-tuple; make `--dry-run` do the
  duplicate check against the DB without writing anything.
- `core/management/commands/import_csv.py` — print the report: per account
  (by **name**), type and month the rows it would import / skip as
  duplicates, rejections by reason, near-duplicates across files, and the
  row-accounting sum check per file. Names, dates and counts only, never
  Account column values.
- `core/management/commands/csv_coverage.py` — reuse the shared date parser
  so its settlement-date columns become correct (no other change).
- `docs/IMPORT_COMMANDS.md`, `AGENTS.md` — update the `compute_tx_hash` /
  dedup notes.

Out of scope: Holdings CSV import (`--holdings`), PDF/VL import, IB import,
`TransactionType` model changes, migrations, cleaning `docs/reconciliation/`
logs, the actual production re-import (tdw runs it after this branch).

## Steps (one commit each)

1. **Header mapping by name only.** Rewrite the header block in
   `parse_rbc_csv`: exact header-name match (case-insensitive, quotes
   stripped) for the ten known names; unknown extra columns ignored; a
   required column missing from the header → the file fails with a clear
   error. Remove the positional `col_map` defaults. Drop the `len(row) < 9`
   check in favour of per-field presence.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_csv_parsers.py -q`
   and `git diff --stat` shows only `imports/parsers.py`.
2. **One date parser, settlement date mandatory.** Add `parse_csv_date()`
   in `imports/parsers.py` (formats above); use it for `Date` and
   `Settlement Date`; a missing or unreadable settlement date rejects the
   row with reason `Missing settlement date` / `Invalid settlement date: …`.
   `csv_coverage` imports and uses the same helper.
   Check: `rg -n "strptime" imports/parsers.py core/management/commands/csv_coverage.py`
   shows the helper only.
3. **Row accounting.** `parse_rbc_csv` also returns per-file counts
   (data rows seen, imported, practice, rejected-by-reason) such that
   imported + practice + rejected = data rows; `auto_import` adds
   duplicates so imported + duplicate + practice + rejected = data rows;
   `import_csv` prints the sum per file. A row-count mismatch **fails that
   whole file**: nothing is stored for it (atomic), the `FileImportLog`
   status is `error` with the counts as the reason, and the run continues
   with the next file (decision 4).
   Check: `pytest imports/tests/test_import_csv_command.py -q`.
4. **Unknown type rejects the row.** For the RBC path, `_normalize_type`
   no longer auto-creates `TransactionType` rows: an activity string not in
   `_RBC_TYPE_MAP` (after normalisation) rejects the row with reason
   `Unknown activity type: <raw>` and counts as rejected. (IB path keeps
   its behaviour; only the RBC call site changes.)
   Check: `rg -n "auto_create_or_get" imports/parsers.py` shows no RBC-path call.
5. **Hash without row number.** `compute_tx_hash`: remove `row_number`,
   add `seq` (omitted when 1); the hash uses only fields present in both
   layouts — date, settlement date, activity/type, symbol, quantity,
   price, value, currency, plus the account and seq — never Symbol
   Description or Description. `auto_import` assigns `seq` per group of
   rows with identical hash inputs, in file order, and hashes over parsed
   fields + broker + normalised account. Two identical rows in one file →
   seq 1 and 2, both imported; the same row in two overlapping exports →
   same hash, second skipped.
   Check: `/opt/pm-venv/bin/python -m pytest --collect-only -q` collects,
   and `rg -n "row_number" core/models.py imports/` finds nothing in the hash path.
   (`core/tests/test_csv_import.py` calls `compute_tx_hash` and is updated here.)
6. **Dry-run report.** `--dry-run` (Activity path) writes nothing (no
   BrokerStatement, no FileImportLog, no file moves — verify current code
   already holds) and prints, per account name / type / YYYY-MM: rows it
   would import, duplicates it would skip (checked against the DB),
   rejections grouped by reason, and near-duplicates: rows in **different
   files** with the same account, date, symbol and value that differ in
   another field (listed by file + row number + which field differs).
   Output uses account **names**, dates and counts only.
   Check: `pytest imports/tests/test_import_csv_command.py -q`.
7. **Tests with synthetic CSVs, both layouts.** Add to
   `imports/tests/test_csv_parsers.py` and `imports/tests/test_import_csv_command.py`:
   old 9-column file with `31-Dec-18` dates (Date + Settlement Date stored);
   new 11-column file; two overlapping files (second fully duplicate);
   two identical rows in one file (both imported, different hashes); an
   unknown activity type (row rejected, no new `TransactionType` row); a
   near-duplicate pair across two files (reported in dry-run); row counts
   add up in every case; a forced row-count mismatch fails the whole file
   atomically (nothing stored, `FileImportLog` status error with the
   counts, run continues with the next file) — decision 4; no Account
   column value appears in command output.
   Check (full suite, run once here):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.
8. **Docs.** Update `docs/IMPORT_COMMANDS.md` (import_csv section: layouts,
   settlement date, row accounting, dry-run report) and the `AGENTS.md`
   gotcha ("compute_tx_hash excludes amount…" → also mentions `seq` and no
   row number).
   Check: `git status` shows only the two doc files.

## Tests

Only at the end (step 7):

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

No browser tests. After the branch merges, tdw clears the transactions and
re-imports (old `tx_hash` values are not migrated).

## Stop conditions

- A real export layout appears that matches neither 9- nor 11-column header
  set → stop and ask.
- An existing test depends on row-number-based hashes or on
  `TransactionType` auto-creation from RBC activity → stop, do not silently
  rewrite the test's intent.
- `compute_tx_hash` has a caller other than `auto_import` / the fingerprint
  helpers whose semantics would change → stop and ask.
- Any output path that would print an Account column value → stop.

## Open questions

1. **Should a row with a missing settlement date be rejected even in the
   11-column layout?** Recommended: yes, uniformly — every RBC Activity
   export has a Settlement Date column; a blank one means an unreadable row
   and should not silently become NULL.
2. **Keep `row_number` in `compute_tx_hash` for other callers?**
   Recommended: remove the parameter entirely (only `auto_import` passes
   it) and add `seq` with default 1, omitted from the hash when 1, so
   hashes of single (non-repeated) rows stay simple and stable.
3. **Do near-duplicates block anything?** Recommended: no — they are
   reported in `--dry-run` only (and in the per-file report); the hash and
   duplicate logic stay unchanged.
4. **Row-count mismatch (sum ≠ data rows): fail or warn?** Recommended:
   import proceeds but the file is flagged in the output and the
   `FileImportLog` status becomes `partial`; a true invariant break is a
   parser bug and the loud warning plus rejection reasons are enough.
5. **Is `csv_coverage` in scope?** Recommended: only the shared date-parser
   reuse (step 2), since its broken settlement column is the symptom that
   motivated this task; nothing else in it changes.

## Decisions (tdw, Oct 30)

1. A row with a missing settlement date is rejected even in the 11-column
   layout — yes, uniformly.
2. `row_number` comes out of `compute_tx_hash` entirely and `seq` (default
   1, omitted when 1) goes in — yes. The hash uses only fields present in
   both layouts: date, settlement date, activity/type, symbol, quantity,
   price, value, currency, plus the account and seq. Never Symbol
   Description or Description.
3. Near-duplicates block nothing — yes, reported in `--dry-run` only.
4. Row-count mismatch is not a warning — no: it fails that whole file,
   nothing is stored for it (atomic), the `FileImportLog` status is
   `error` with the counts as the reason, and the run continues with the
   next file. Tested.
5. `csv_coverage` is in scope only for the shared date-parser reuse — yes.
6. Step 4 stop resolved with option (a): change the 'Div Reinvest' fixture
   row at `imports/tests/test_csv_parsers.py:266` to 'Dividends', because
   real RBC exports never use 'Div Reinvest' (found in the Oct 24 test
   gate); do not add it to `_RBC_TYPE_MAP`.

## Review fixes (Oct 30)

Claude's review of steps 1–8. Done in order, one commit each, each with a
test; then the full suites (`manage.py test core` + `pytest tests/ imports/tests/`).

1. **Remove the file-level content-hash skip for CSV Activity imports.** The
   skip in `auto_import` decides by an earlier `FileImportLog`, so after
   `core_transaction` is cleared every re-import would be skipped, and a file
   whose earlier log is an error is skipped forever. The row `tx_hash`
   already decides duplicates — a re-imported identical file just reports all
   rows as duplicates. Keep writing `FileImportLog` (with the content hash).
2. **Any exception other than the accounting mismatch = failed file.** Today
   it is only logged. Treat it like a failed file: `imported` and
   `duplicates` 0 in the report, `report.failed` with a scrubbed reason, a
   `FileImportLog` error row, no rows written to the CSV_Activity_Imported
   tracking file, the file moved to errors/, and the run continues with the
   next file.
3. **Dry-run: in-run duplicates + same accounting check.** In `--dry-run`,
   treat a `tx_hash` already seen in an earlier file of the same run as a
   duplicate (not only hashes in the DB), and run the same row-accounting
   check as the real run, reporting a mismatch as a failed file.
4. **Missing currency rejects the row** with reason `Missing currency`
   instead of defaulting to USD.
5. **Keep the CSV sign of quantity.** Do not change how quantity is stored,
   but keep the CSV sign in the parsed row as an extra key, and in
   `--dry-run` print per transaction type how many rows have a negative
   quantity in the CSV.

## Dry-run fixes (Oct 30)

A `--dry-run` on tdw's real exports found three more things; each is fixed
in order, one commit each, each with a test, then the full suites
(`manage.py test core` + `pytest tests/ imports/tests/`).

### A. Footer rows are counted separately, never data or rejections

Every export ends with RBC's disclaimer block, starting at the first line
whose first cell is `Disclaimer` or `Disclaimers` (7 lines in the 11-column
layout with 1 cell each, 9 lines in the 9-column layout with 10 cells
each). From that line on, rows are **footer**: counted separately as
`footer` in the per-file row accounting and printed in the output; they are
never data rows and never rejections (today each one is rejected as
`Missing activity type`). The invariant stays
`imported + duplicates + practice + rejected = data rows`; footer rows sit
outside `data rows`. Applies to the RBC Activity path only.

### B. Three more known activity types in `_RBC_TYPE_MAP`

Add, each with the code the old auto-create produced for that activity
string (the sanitised uppercase form in `_normalize_type`), so the
`TransactionType` rows already in the database are reused:

| Activity string | Code | Label |
|---|---|---|
| `Reorganization` | `REORGANIZATION` | Reorganization |
| `Return of Capital` | `RETURN_OF_CAPITAL` | Return of Capital |
| `Adjustment` | `ADJUSTMENT` | Adjustment |

Their rows (no special parsing — the type maps, quantity/price/value/
notes are stored as today):
- **Reorganization** descriptions start with: `EXP` (option expired),
  `DIS` (split, spin-off, stock dividend), `CIL` (cash in lieu: value,
  empty quantity), `MGR` or `MER` (merger: old security negative quantity,
  new security positive, sometimes cash), `NAC` (name change: old negative,
  new positive).
- **Return of Capital**: value 0 and an empty quantity; the book-cost
  adjustment is only in the description (kept in notes as today).
- **Adjustment** (`ADJINCR`, `ADJDEL`): value 0 and signed quantities on
  RBC security codes.

### C. Quantity is stored signed, exactly as the CSV gives it (tdw's decision)

SELL negative, transfers out negative, the old security in a merger
negative, expiries with their printed sign. Remove the `abs()` in
`parse_rbc_csv` and the `quantity_csv` extra key; the dry-run
negative-quantity count stays (now computed from the stored quantity).
Commission derivation uses `abs(quantity)` so BUY and SELL results are
unchanged.

Every place that reads `Transaction.quantity` (rg), how it treats the sign
today, and what changes (today's stored quantity is always positive):

**Changed — assumed a positive quantity; now use `abs()` in the SELL /
negative branches so BUY and SELL results stay the same, with a test each:**
1. `core/utils/portfolio.py:119-121` `compute_portfolio_value_series` —
   branches on type: BUY `+qty`, SELL `-qty`. SELL branch → `-abs(qty)`.
2. `core/utils/portfolio.py:282-288` `compute_realised_pnl` — BUY lot qty
   (positive, unchanged); SELL `qty_to_sell = tx.quantity` assumed
   positive → `abs(tx.quantity)`. Realised P&L unchanged for a SELL of -10.
3. `core/utils/portfolio.py:420-427` `compute_cost_basis_series` — BUY
   `+qty` (unchanged); SELL `sold_cost = qty*avg`, `shares -= qty` assumed
   positive → `abs(qty)`.
4. `core/utils/portfolio.py:668-672` `compute_series_by_account` — same
   pattern as 1 → `-abs(qty)` in the SELL branch. (Also fixes a latent bug
   found while testing this: the symbol set was built as
   `tx.symbol for tx in …values_list("symbol", flat=True)` — iterating
   strings, so the function always crashed to `{}` whenever the price cache
   was empty; now `sym for sym in …`.)
5. `core/utils/portfolio.py:810-815` `compute_positions` — BUY `+qty`,
   SELL `-qty` → `-abs(qty)` in the SELL branch.
6. `core/views_accounts.py:54-60` `account_detail` active holdings — BUY
   `+= qty`, SELL `-= qty` → `-abs(qty)` in the SELL branch.
7. `core/management/commands/write_ledger.py:212-216` — SELL passes
   `tx.quantity` to a CREDIT position entry (a signed -10 would flip the
   sign) → `abs(tx.quantity)`. The BUY DEBIT (:201), DIV REIN (:277) and
   SPLIT (:288) entries stay: those rows carry positive quantities in the
   CSV. The REORGANIZATION branch's `tx.quantity > 0` test (:339) is
   unchanged for DIS (+qty) and CIL (0); a negative-quantity reorg (long
   option expiry, new after fix B) now falls into the "no qty, no cash"
   skip instead of being read as a new-total reorg — such rows were never
   imported before fix B, so nothing stored changes.
8. `core/management/commands/rebuild_positions_from_transactions.py:214-226`
   — BUY `running_qty + qty` (unchanged); SELL `running_qty - tx.quantity`
   assumed positive → `-abs(tx.quantity)`.
9. `scripts/quantity_reconciliation_all.py:55-74` `classify()` (parked) —
   BUY `+qty`, SELL `-qty`, REORG `EXP` short `+qty` / long `-qty`; all
   assumed positive → the abs forms, so its report is unchanged.
10. `imports/parsers.py` commission derivation — `quantity > 0` gate and
    `gross = quantity * price` assumed positive; a signed SELL would make
    gross negative and the sanity cap zero the commission → `abs(quantity)`.

**Not changed (no positive-quantity assumption):**
- `core/models.py:332` `Transaction.__str__` — prints the stored quantity
  (now signed).
- `core/views_transactions.py:175,206` CSV/Excel export — prints the stored
  quantity verbatim; it now shows signed values (inherent to the decision,
  no computation).
- `core/admin.py:101,158` — admin list display.
- `scripts/generate_accounting_ledger.py:228`, `scripts/dump_chains.py:48`,
  `scripts/reconcile_tx.py:63,90` (parked) — print the stored quantity
  only.
- `scripts/fix_write_ledger.py` — one-off patcher that embeds a copy of the
  old `write_ledger.py` as a string literal (already applied); not live
  code, left as is.
- `docs/history/scripts/*` — archived.
- Files reading *other* models' quantity (`RbcMonthlyTransaction`,
  `RbcMonthlyHolding`, `LedgerAccount`, `PositionHistory`) are out of scope:
  `audit_rbc_data.py:730`, `reconcile_holdings_cash.py:201`,
  `ledger_high_change.py:87`, `pdf_internal_reconciliation.py:191`,
  `verify_pdf_integrity.py:342/440/468`, `data_quality_audit.py`,
  `check_db_state.py`, `check_ledger.py`, `compare_monthly_pdf.py:278`,
  `manage_inbox_import.py` (PDF path), `parsers_pdf.py`,
  `parsers_monthly_vl.py`.

### D. Near-duplicates: exact cross-file duplicates are never reported

A row that has an exact duplicate in the other file is never reported as a
near-duplicate. Today the pair loop compares every cross-file pair in a
value group, so when two rows of one file share account/date/symbol/value
with each other and both are exactly duplicated in the other file, the
cross pairing (row 1 of file A vs row 2 of file B) is reported although
both rows have their exact twin in the other file. Fix: mark every row
whose full signature (account, date, symbol, value, type, quantity, price,
currency, settlement date) also appears in a different file; any pair
involving a marked row is not reported. Genuine near-duplicates (no exact
twin in the other file) are still reported.
