# Plan: csv_coverage (phase 4, step 14-0a)

Branch: `chore/csv-coverage`. Base: `dev`.

## Goal

A new **read-only** management command `csv_coverage` that audits a folder of RBC
Activity CSV files and writes a coverage report. It reads every `.csv` in a folder
given by `--dir`, using the **same CSV reading and fingerprint account lookup as
`import_csv`** (`parse_rbc_csv()` in `imports/parsers.py`, `is_known_account()` /
`fingerprint_account_number()` / `build_fingerprint_name_map()`), and writes
nothing to the database. The report (a file via `--out`, plus a short summary on
screen) contains:

- **Per file**: row count, first and last `Date`, first and last `Settlement Date`,
  the accounts (by name) with row count and first/last date per account, and the
  count of rows whose account is unknown (no stored fingerprint).
- **Per account**: overlaps between file pairs (overlapping date range and how many
  rows inside the overlap are identical in both files on all columns), and every gap
  of more than 31 days between consecutive rows, naming the files on each side.
- **Per account and calendar month, from 2017-11**: CSV rows across all files
  (a row identical across files counted once; identical rows *within* one file kept)
  next to the `RbcMonthlyTransaction` rows dated in that month; months with PDF rows
  but no CSV rows are flagged.

Privacy: the report contains **only account names, file names, dates and counts**.
Never any value from the CSV `Account` column — not even the last 4 digits. Unknown
accounts are reported only as a count of "unknown-account rows" per file.

## What changes

- `core/management/commands/csv_coverage.py` — new command (the only code file).
- `core/tests/test_csv_coverage.py` — new tests (synthetic CSVs in a temp folder,
  fake accounts `90000001`/`90000002` created as `Account` rows with
  `fingerprint=fingerprint_account_number(...)` per the test convention).

No migrations. **No change to any import code** (`import_csv`, `parsers.py`,
`auto_import.py` are used as-is, read-only).

## Design notes

- Arguments: `--dir PATH` (required, folder of Activity CSVs; non-recursive
  `*.csv`), `--out PATH` (optional; when given, the full report is written there;
  the short summary always goes to stdout).
- Per file: call `parse_rbc_csv(path, rejections=[])`. `trades` gives the accepted
  rows (each has `date`, `settlement_date`, `account_id`, `symbol`, `type`,
  `quantity`, `price`, `commission`, `amount`, `currency`, `notes`, `row_number`);
  `rejections` gives the skipped rows. Unknown-account rows = rejections whose
  `Reason Rejected` starts with `"Unknown account"`; practice rows = rejections
  starting with `"Practice account"` (counted separately, never analysed).
- Account resolution: build `fingerprint → name` once with
  `build_fingerprint_name_map()`, then map each trade's `account_id` via
  `fingerprint_account_number(account_id)`. The raw `account_id` is used only as a
  lookup key and is never printed or written.
- **Row identity** (for overlap and dedup): all parsed trade fields except
  `row_number` — `date, settlement_date, symbol, type, quantity, price,
  commission, amount, currency, notes` — plus the row's account id, as a tuple.
  The account id is used internally only (its fingerprint resolves to exactly
  one stored account) and is never printed or written; without it, equal rows
  in two different accounts would count as one. `parse_rbc_csv` already returns
  `settlement_date` (possibly `None`), so no parser change is needed — had it
  not, the command would read the Settlement Date column itself from the same
  rows. Values are used only for comparison, never printed.
- **Overlap per file pair (same account)**: overlap range =
  `[max(first dates), min(last dates)]` by `Date`; if non-empty, count identical
  rows inside the range as `sum over row tuples of min(count_in_file_A,
  count_in_file_B)` (multiplicity, so duplicates within one file pair up).
- **Gaps per account**: distinct `Date` values across all files for the account,
  sorted; report every consecutive pair more than 31 days apart, naming the files
  that contain the row(s) at the start and at the end of the gap.
- **Monthly table per account**: months from 2017-11 up to the latest month seen in
  the CSVs or in `RbcMonthlyTransaction` for that account. CSV count per month =
  for each row tuple, `max(count in file_i)` across files (identical across files →
  once; duplicates within one file → kept). PDF count =
  `RbcMonthlyTransaction.objects.filter(rbc_monthly__account=acc,
  date__year=y, date__month=m).count()`. Flag months with PDF count > 0 and CSV
  count == 0.
- Files that fail to parse (or are not Activity CSVs) are reported as an error line
  per file; the command continues with the rest. Files named `Holdings*.csv` are
  skipped with a note (they are a different format).
- Short stdout summary: files read, accounts seen, unknown-account rows, gaps
  found, flagged months.

## Out of scope

- Any database write, any migration, any change to `import_csv`, the parsers, the
  watcher or the models.
- Holdings CSV coverage, PDF parsing, IB CSVs, any UI/page for the report.
- Comparing against `CoreMonthlyStatement` or RBC balance totals (that is step 14 proper).

## Steps (one commit each)

1. **Command skeleton + per-file stats.** `--dir`/`--out` args, file discovery
   (skip `Holdings*.csv`, report parse errors), `parse_rbc_csv` per file, per-file
   row count and first/last `Date` and `Settlement Date`; short stdout summary.
   Check: `/opt/pm-venv/bin/python manage.py csv_coverage --help` and
   `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/ --collect-only -q`.
2. **Per-account breakdown + unknown/practice counts.** Fingerprint→name map,
   per-file per-account row counts and first/last dates, unknown-account and
   practice-row counts per file (names only in output).
   Check: `git status --short` (only the command file changed) + `--help`.
3. **Overlaps between files per account.** Overlap range + identical-row count per
   file pair. Check: `--help` still runs; `git grep -n "account_id" core/management/commands/csv_coverage.py` shows `account_id` used only for lookup, never in a report string.
4. **Gaps > 31 days per account.** With the files on each side of each gap.
   Check: `git status --short`; quick manual smoke on a tiny fixture folder under `/tmp` (throwaway, not committed).
5. **Monthly CSV-vs-PDF table from 2017-11 + flags.** Per account and month, CSV
   count (max-multiplicity dedup) vs `RbcMonthlyTransaction` count; flag
   PDF-only months. Check: `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test` (must stay clean).
6. **Report file + privacy pass.** Full report to `--out`, short summary to stdout;
   review every emitted string for raw account numbers.
   Check: `git grep -n "print(" core/management/commands/csv_coverage.py` is empty (all output via `self.stdout`).
7. **Tests.** `core/tests/test_csv_coverage.py` with synthetic RBC-format CSVs
   written to a `tempfile` folder and fake `Account` rows `90000001`/`90000002`
   (see `imports/tests/test_import_csv_command.py` for the fixture pattern).
   Cases:
   - two files with an overlapping range and identical rows in the overlap → correct overlap range and identical count;
   - a gap of > 31 days → reported with the right files on each side;
   - a row with an account that has no stored fingerprint → counted as unknown, and the raw number appears nowhere in the report (assert the fake number is absent from the report text);
   - a row repeated within one file → kept twice in the monthly CSV count;
   - a month with `RbcMonthly`/`RbcMonthlyTransaction` rows and no CSV rows → flagged.
   Full test run (the only time tests run in this plan):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.

## Stop conditions

- If `parse_rbc_csv` cannot be reused without touching import code (e.g. it needs a
  flag to keep unknown-account rows) — stop and ask; do **not** edit the parser.
  (Current reading: it already returns rejections, so no change is needed.)
- If a CSV in `--dir` is not an RBC Activity export and the parse raises instead of
  returning cleanly — catch, report the file as an error line, continue; if that
  turns out impossible without parser changes, stop and ask.
- Never read or print `.env`; never connect to mercury; tests only with
  `settings_test`.
- Do not change this plan while implementing; if a step is wrong, stop and report.

## Open questions (all answered — see Decisions below)

1. **Skip `Holdings*.csv` in `--dir`?** Recommended: yes — skip by filename and note
   it in the report; this command is for Activity CSVs only.
2. **End month of the monthly table.** Recommended: the latest month seen in the
   CSVs or in that account's `RbcMonthlyTransaction` rows (not "today"), so results
   are stable and tests are deterministic. Start is fixed at 2017-11.
3. **Practice-account (`PRAC…`) rows.** Recommended: count them per file as
   "practice rows skipped", separate from unknown-account rows; never analysed.
4. **Row identity for overlap/dedup.** Recommended: the parsed fields listed in
   Design notes (excluding `row_number` and the account), because raw CSV lines
   differ between exports by whitespace alone.
5. **`--out` optional?** Recommended: yes — without it only the short stdout summary
   is produced; nothing is ever written next to the input files.

## Decisions (tdw, Oct 29)

Answers to the open questions above:

1. Skip `Holdings*.csv` in `--dir`: **yes** — skip by filename and note it in
   the report; this command is for Activity CSVs only.
2. End month of the monthly table: **yes** — the latest month seen in the CSVs
   or in that account's `RbcMonthlyTransaction` rows (start fixed at 2017-11).
3. Practice-account (`PRAC…`) rows: **yes** — counted per file as "practice rows
   skipped", separate from unknown-account rows; never analysed.
4. Row identity for overlap/dedup: **no** to the recommendation — the row
   identity must include the resolved account (its id, used internally, never
   printed), otherwise equal rows in two different accounts count as one. Use
   all parsed fields except `row_number`, plus the account id and the
   Settlement Date. If `parse_rbc_csv` did not return the Settlement Date,
   read that column in the command itself from the same rows, without changing
   the parser. (It does return it — `settlement_date`, possibly `None` — so no
   fallback is needed.)
5. `--out` optional: **yes** — without it only the short stdout summary is
   produced; nothing is ever written next to the input files.
