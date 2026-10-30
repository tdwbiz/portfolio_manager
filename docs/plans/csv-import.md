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
  (default 1, omitted from the hash when 1). Update the docstring and the
  AGENTS.md gotcha line.
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
   `import_csv` prints the sum per file and flags a mismatch loudly.
   Check: `pytest imports/tests/test_import_csv_command.py -q`.
4. **Unknown type rejects the row.** For the RBC path, `_normalize_type`
   no longer auto-creates `TransactionType` rows: an activity string not in
   `_RBC_TYPE_MAP` (after normalisation) rejects the row with reason
   `Unknown activity type: <raw>` and counts as rejected. (IB path keeps
   its behaviour; only the RBC call site changes.)
   Check: `rg -n "auto_create_or_get" imports/parsers.py` shows no RBC-path call.
5. **Hash without row number.** `compute_tx_hash`: remove `row_number`,
   add `seq` (omitted when 1); `auto_import` assigns `seq` per group of
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
   add up in every case; no Account column value appears in command output.
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
