# Plan: monthly_check — store results and accept exceptions (phase 4, step 14b)

## Goal

`monthly_check` (core/utils/monthly_check.py) currently prints a report and writes
nothing to the database. This step persists what it finds so tdw can triage over
time:

- Every per-currency verdict is stored as a `MonthlyCheckResult`
  (account, statement_date, currency, verdict PASS/FAIL/NO_CSV/FIRST, notes,
  checked_at; unique account+statement_date+currency).
- Every exception found is stored as a `MonthlyException`
  (kind CASH/QUANTITY/CHAIN/IMPORT, account, statement_date, currency — blank
  for QUANTITY which is per account —, ref — symbol for QUANTITY, hash8 for
  IMPORT, blank otherwise —, difference (decimal, null when not numeric),
  detail (the check's lines for it, including the unmatched rows), status
  open/accepted/resolved, reason, accepted_at, first_seen, last_seen,
  resolved_at; unique kind+account+statement_date+currency+ref).
- A new `--store` flag writes: results upserted; each exception found created
  or updated (detail, difference, last_seen); an accepted exception whose
  difference changed goes back to open with a note appended to detail — only
  when both the old and the new difference are non-NULL; a NULL difference
  never auto-reopens; an open or accepted exception no longer found becomes
  resolved (resolved_at); a resolved exception found again returns to open;
  re-runs never create a second row for the same key.
- IMPORT exceptions come from the check's parked list (account and statement
  date from the safe file name, ref = hash8, detail = the reasons). Conflict
  second copies never become exceptions.
- Accepting is a decision only (a note, no data change, reason required):
  `monthly_check --accept --reason TEXT` with filters `--id N`, `--account NAME`,
  `--kind`, `--ref` (exact symbol or prefix ending in `*`, e.g. `RBF*`),
  `--from YYYY-MM`, `--to YYYY-MM`; it accepts only open exceptions matching
  all filters and prints how many per account and kind. `--reopen --id N
  --reason TEXT` reopens one.
- Both models are registered in the admin as read-only lists (account name,
  never digits). Output stays names, dates, currencies, symbols and amounts.

Without `--store`/`--accept`/`--reopen` the command stays exactly as today:
read-only, only the `--out` file written.

## What changes

- `core/models.py` — add `MonthlyCheckResult` and `MonthlyException`
  (choices as class constants; `currency` and `ref` use `blank=True,
  default=""` — never NULL, so the unique constraints work on SQLite/SQL
  Server; `difference = DecimalField(max_digits=18, decimal_places=4,
  null=True, blank=True)`).
- `core/migrations/00XX_...` — one new migration for both models (next free
  number; never reuse a number — see AGENTS.md gotcha).
- `core/utils/monthly_check_store.py` (new) — pure helpers:
  - `exceptions_from_check(check)` — turn a `StatementCheck` into exception
    dicts grouped by (kind, currency, ref):
    - lines starting `CASH:` → kind CASH, currency = that currency, ref "",
      difference parsed from `diff <amount>` (null if absent);
    - lines starting `CHAIN:` → kind CHAIN, currency = that currency, ref "",
      difference parsed from `(diff <amount>)` (null if absent);
    - lines starting `QUANTITY: <sym>:` → kind QUANTITY, **currency ""**,
      ref = symbol, difference = got − expected parsed from the line;
      QUANTITY failures are attached to every currency by `check_statement`
      (pooled), so deduplicate across currencies;
    - detail = all grouped lines joined by newline (this keeps the unmatched
      CSV/PDF rows inside the exception's detail).
  - `import_exceptions_from_parked(parked)` — parse each parked safe name
    `YYYY-MM-DD_monthly_<ACCOUNT>_<hash8>.pdf` → kind IMPORT, ref = hash8,
    statement_date from the name, detail = the reason(s); entries whose
    account name matches no `Account` are returned as "skipped" (printed, not
    stored). Conflicts are never passed in.
  - `store_results(...)` / `store_exceptions(...)` — upserts and the
    resolve/reopen rules described in the Goal; `checked_at`/`first_seen` set
    only on create, `last_seen` always.
- `core/management/commands/monthly_check.py` — add `--store`, `--accept`,
  `--reopen`, `--reason`, `--id`, `--kind`, `--ref`; `--out` becomes required
  only in report mode (not with `--store`/`--accept`/`--reopen`); `--store`
  runs the same checks and stores; `--accept`/`--reopen` touch the DB only
  (no checks run, no report). Update the module docstring/help.
- `core/admin.py` — register both models read-only (`has_add_permission`
  False, no change/delete; `readonly_fields` on everything). Lists show
  account **name**, statement_date, currency, verdict/kind/ref/difference/
  status/reason/dates — never account digits.
- Tests: `core/tests/test_monthly_check_store.py` (new, Django TestCase) and
  command-level cases there too.

## Out of scope

- No UI page for exceptions (admin only for now).
- No changes to the check logic itself (chain/cash/quantity rules, tolerances,
  real-data fixes) — `monthly_check.py` stays read-only by default.
- No auto-accept, no scheduled runs, no `watch_inbox` integration.
- No symbol-map work; `symbol_pairs` stay report-only.
- No changes to `fill_from_pdf` or the importers.

## Steps (one commit each)

1. **Models + migration.** Add both models to `core/models.py`, run
   `makemigrations`, add them to `core/admin.py` read-only in the same commit
   is *not* done here (step 5). Check:
   `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test`
   prints "No changes detected" and
   `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/ --collect-only -q`
   still collects. Commit.
2. **Grouping helper.** `core/utils/monthly_check_store.py`:
   `exceptions_from_check` and `import_exceptions_from_parked` (pure functions
   over the dataclasses, no DB writes). Check: quick inline
   `/opt/pm-venv/bin/python -m pytest tests/test_monthly_check.py -q` (unchanged
   behaviour) plus a small new unit test for the grouping (QUANTITY dedup across
   currencies, diff parsing, unmatched rows kept in detail). Commit.
3. **Store logic.** `store_results` / `store_exceptions` with upsert,
   resolve-vanished and reopen-on-changed-difference rules (difference null →
   never auto-reopens). Check: new tests for store-twice-same-rows,
   disappeared→resolved, accepted+same-diff stays accepted, accepted+changed
   diff reopens with a note in detail. Commit.
4. **`--store` flag.** Wire into the command (report still written when
   `--out` given; `--out` optional with `--store`). IMPORT exceptions from the
   parked list only, conflicts excluded, unknown account names printed as
   skipped. Check: command tests — `--store` twice gives the same rows; without
   `--store` nothing is written (row counts unchanged); IMPORT rows created from
   parked files and none from conflicts. Commit.
5. **`--accept` / `--reopen`.** Filters `--id`, `--account`, `--kind`, `--ref`
   (exact or `PREFIX*`), `--from`/`--to` (on statement_date); only open rows
   are accepted; refuse (CommandError) when `--reason` is missing; print counts
   per account and kind; `--reopen --id N --reason TEXT` sets status open and
   appends the reason note to detail. Check: tests — bulk accept with
   `--ref RBF*` touches only matching open rows; accept without `--reason` is
   refused; reopen works. Commit.
6. **Admin.** Register both models read-only (no add/change/delete), list
   display with account name only. Check:
   `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test`
   and a test that the admin changelists render for a logged-in superuser.
   Commit.
7. **Docs.** Append a short "implemented" note to this plan file (what landed,
   any deviation). Check: `git status` clean after commit.

## Tests (run once, at the end)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

Required new cases (from the task):

- `--store` twice gives exactly the same rows (no duplicates).
- An exception that disappears becomes resolved with `resolved_at`.
- An accepted exception with the same difference stays accepted; with a changed
  difference it reopens and detail gains a note.
- Bulk accept with `--ref RBF*` touches only matching open rows.
- `--accept` without `--reason` is refused.
- IMPORT exceptions are created from parked files and none from conflicts.
- Without `--store` nothing is written (model counts unchanged).

## Stop conditions

- Stop if a `monthly_check` check itself would have to change to produce
  storable exceptions (e.g. a failure line that cannot be attributed to a
  kind/currency/ref) — report instead of hacking the check.
- Stop if the migration would need to touch an existing table.
- Stop if `--store` cannot stay idempotent for any case.
- Stop if accepting would require changing any imported data (it must be a note
  only).
- Do not print or store account numbers anywhere; names, dates, currencies,
  symbols and amounts only.

## Open questions

All six questions below were answered by tdw (Oct 30): he accepted the
recommended answer in every case — see "Decisions (tdw, Oct 30)" at the end.

1. **Exception granularity when several lines share a key.** Several CASH lines
   (main diff + unmatched rows + trade-date replay) share
   kind+account+date+currency+ref. Recommended: one exception per key, detail =
   all grouped lines joined by newline (this is what keeps the unmatched rows
   "inside" the exception), difference parsed from the line that carries
   `diff <amount>`.
2. **QUANTITY exceptions and currency.** `check_statement` attaches the pooled
   QUANTITY failures to every currency. Recommended: the report keeps doing
   that, but storage writes one QUANTITY exception with `currency=""` and
   `ref=<symbol>` (deduplicated across currencies), per the task text.
3. **`--out` requirement.** Recommended: `--out` stays required in report mode
   but is optional (ignored if given) with `--store`, `--accept`, `--reopen`.
4. **Parked entries whose account name matches no Account.** Recommended: skip
   them, print a "skipped (unknown account)" line, store nothing for them —
   `MonthlyException.account` is not nullable.
5. **Non-numeric exceptions and auto-reopen.** CHAIN lines without a parseable
   diff and IMPORT rows keep `difference=NULL`. Recommended: NULL difference
   never auto-reopens (a vanished-then-reappearing exception just returns as
   open if it was resolved; an accepted one stays accepted until its difference
   changes or tdw reopens it manually).
6. **Verdicts FIRST/NO_CSV.** Recommended: store them as `MonthlyCheckResult`
   rows too (verdict column), so the table is a complete per-month picture;
   they never produce exceptions.

## Decisions (tdw, Oct 30)

tdw accepted the recommended answer for every open question:

1. **One exception per key** (kind+account+statement_date+currency+ref).
   `detail` = all grouped lines joined by newline (this keeps the unmatched
   CSV/PDF rows inside the exception); `difference` parsed from the line that
   carries `diff <amount>` (NULL when no line does).
2. **QUANTITY exceptions are per account:** one row with `currency=""` and
   `ref=<symbol>`, deduplicated across currencies; the report keeps attaching
   them to every currency.
3. **`--out`** stays required in report mode; optional (ignored if given)
   with `--store`, `--accept`, `--reopen`.
4. **Parked entries whose account name matches no Account:** skip, print a
   "skipped (unknown account)" line, store nothing for them.
5. **Non-numeric exceptions and auto-reopen:** CHAIN lines without a
   parseable diff and all IMPORT rows keep `difference=NULL`; a NULL
   difference never auto-reopens. A vanished-then-reappearing exception
   returns to open if it was resolved; an accepted one stays accepted until
   its (non-NULL) difference changes or tdw reopens it manually.
6. **FIRST/NO_CSV** are stored as `MonthlyCheckResult` rows too, so the table
   is a complete per-month picture; they never produce exceptions.

## Implemented (Pi, Oct 30)

All seven steps landed on `feature/monthly-check-store`, one commit each:

1. Models `MonthlyCheckResult` + `MonthlyException` in `core/models.py`,
   migration `0046_monthlycheckresult_monthlyexception.py`.
2. `core/utils/monthly_check_store.py`: `exceptions_from_check` (grouping,
   QUANTITY deduplicated across currencies, identical pooled lines stored
   once) and `import_exceptions_from_parked` (unknown account names /
   unparsable names come back as skipped).
3. `store_results` / `store_exceptions`: upserts, resolve-vanished, reopen
   on changed non-NULL difference with a note in detail; NULL difference
   never auto-reopens; resolved-found-again returns to open.
4. `--store` flag: results + exceptions persisted (check and import
   exceptions for the same account+date stored together); IMPORT from the
   parked list only, conflicts excluded, unknown accounts printed as
   skipped; `--out` optional with `--store`, still required in report mode.
5. `--accept` (filters `--id/--account/--kind/--ref` exact or `PREFIX*`,
   `--from/--to`; reason required; per-account/kind counts) and `--reopen
   --id N --reason TEXT`.
6. Read-only admin for both models (`ReadOnlyMixin`: no add/change/delete,
   all fields readonly); changelists show the account name only.
7. This note.

Tests: `core/tests/test_monthly_check_store.py` (22 cases: grouping, store
rules, command-level `--store`/`--accept`/`--reopen`, admin rendering).
Deviations: none material — a manual `--reopen` also clears `resolved_at`
and `accepted_at` (consistency with the resolve logic); everything else is
as planned. Full suite green (Django + pytest).
