# Plan: exceptions page (phase 4, step 14c)

Branch: `feature/exceptions-page`. Plan only in this run; implementation is the coding model, one step per commit.

## Goal

Web pages over the stored monthly check (`core_monthly_check_result` / `core_monthly_exception`, written by `monthly_check --store`):

- `/exceptions/` — list of exceptions with filters, status counts, taxable-first ordering.
- `/exceptions/<id>/` — one exception with its stored detail text; Accept (when open) and Reopen (when accepted) forms that call the same functions as the command's `--accept` / `--reopen`.
- `/exceptions/coverage/` — coverage grid: accounts × statement dates, one combined cell per statement.
- Navbar link "Exceptions" with an open-count badge (logged-in users only).

No new models, no migrations. Accept/reopen change only the exception row — never transactions, statements or results.

## What changes

- `core/utils/monthly_check_store.py` — move the accept/reopen logic out of the command:
  - `accept_exceptions(reason, *, row_id=None, account_name=None, kind=None, ref=None, month_start=None, month_end=None) -> (count, per)` — same rules as the command's `_accept` (open rows only, `ref` exact or `PREFIX*`, month window on `statement_date`); sets `status=accepted`, `reason`, `accepted_at=now`.
  - `reopen_exception(row_id, reason) -> row | None` — same rules as `_reopen`: open status, `resolved_at`/`accepted_at` cleared, `[reopened YYYY-MM-DD: reason]` appended to `detail`.
  - `core/management/commands/monthly_check.py` — `_accept` / `_reopen` become thin argument-parsing wrappers around these (validate options, raise `CommandError`, print). Existing command tests must pass unchanged.
- `core/views_exceptions.py` (new) — three views, all `@login_required`:
  - `exceptions_list` (`/exceptions/`, name `exceptions_list`): GET filters `account` (Account pk), `kind`, `status`, `year` (of `statement_date`), `statement_date` (YYYY-MM-DD). Ordering: status rank open → accepted → resolved (`Case`/`When`), then taxable first, then account name, `statement_date`, `kind`. Taxable = `account.category == "TAXABLE"` (see Open questions 1) — annotate, never a name list. Counts per status (over the filtered queryset) above the table.
  - `exception_detail` (`/exceptions/<int:pk>/`, name `exception_detail`): all fields; `detail` rendered exactly as stored in a `<pre>` (autoescape on — it holds the check lines and unmatched CSV/PDF rows); reason and dates. POST (CSRF only) with `action=accept` (only when status open) or `action=reopen` (only when status accepted); `reason` required — blank or wrong-status POST gets an error message and a redirect back with nothing changed; success calls `accept_exceptions(reason, row_id=pk)` / `reopen_exception(pk, reason)`, then `messages.success` + redirect to the detail page. Unknown id → 404.
  - `exceptions_coverage` (`/exceptions/coverage/`, name `exceptions_coverage`): rows = all accounts (taxable first, then name); columns = every `statement_date` present in `MonthlyCheckResult`, grouped by year header. Exactly two queries (all results `select_related("account")`, all exceptions `select_related("account")`); the grid is built in Python. Cell per (account, date) combining all currencies of that statement:
    - open CASH/CHAIN/QUANTITY exception for that account+date → **FAIL** (QUANTITY has blank currency — it counts for the account+date);
    - combined verdict FAIL and every CASH/CHAIN/QUANTITY exception for it accepted → **ACCEPTED**;
    - otherwise the combined verdict: any currency FAIL → FAIL, else any FIRST → FIRST, else any NO_CSV → NO_CSV, else PASS;
    - an IMPORT exception on that date adds its own marker (it never drives FAIL/ACCEPTED);
    - no stored result for the pair → plain gap cell, no colour (gaps are normal).
    - Each cell links to `/exceptions/?account=<pk>&statement_date=<date>`; `title` = date + verdict per currency (e.g. `2026-01-31 — CAD PASS, USD FAIL`). Every state also readable without colour: a letter in the cell (P, F, A, 1 = FIRST, – = NO_CSV, · = gap, `i` = IMPORT marker) plus a legend above the grid.
- `core/urls.py` — the three routes (module is mounted at root).
- `core/context_processors.py` — `exception_badge(request)`: `{}` for anonymous users, else `{"open_exception_count": MonthlyException.objects.filter(status="open").count()}` — one COUNT query per page (same shape as `alert_badge`). Register in `portfolio_manager/settings.py` `context_processors`.
- `core/templates/core/base.html` — nav item "Exceptions" → `exceptions_list`, badge with `{{ open_exception_count }}` when > 0; wrapped in `{% if user.is_authenticated %}` so it is never shown to anonymous users.
- `core/templates/core/exceptions_list.html`, `exception_detail.html`, `exceptions_coverage.html` (new) — extend `base.html`, Bootstrap tables like `reconciliation.html` / `transaction_list.html`. Small "taxable" badge next to taxable account names in the list.
- `tests/test_exceptions_page.py` (new) — Django test client, `settings_test`, fake accounts `90000001`–`90000008`.
- `tests/e2e/test_exceptions_page.py` (new) — Playwright style of the existing e2e tests.
- `docs/IMPORT_COMMANDS.md` — short "Exceptions page" section (see Open questions 2).

## Migrations

None.

## Out of scope

Statements forward (14d), changing any imported data, bulk accept on the page (the command does that), the old reconciliation pages, anything IB-specific.

## Steps (one commit each)

1. **Move accept/reopen into `monthly_check_store.py`**, command delegates. Check: `/opt/pm-venv/bin/python -m pytest tests/test_monthly_check.py -q` (unchanged tests still pass). Commit.
2. **Context processor + navbar link + badge.** Check: `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test` and `rg -n "exception_badge" portfolio_manager/settings.py core/context_processors.py`. Commit.
3. **List view + template + URL** (filters, ordering, counts, taxable badge). Check: `manage.py check --settings=portfolio_manager.settings_test` and `pytest --collect-only -q tests/` (still collects). Commit.
4. **Detail view + template + accept/reopen POST forms.** Check: `manage.py check --settings=portfolio_manager.settings_test`. Commit.
5. **Coverage grid view + template + URL** (two queries, legend, letters, cell links). Check: `manage.py check --settings=portfolio_manager.settings_test`. Commit.
6. **`tests/test_exceptions_page.py`** covering: login required on all three URLs; list ordering (open → accepted → resolved, taxable first, then name/date/kind); each filter (account pk, kind, status, year, statement_date); the taxable badge; status counts; detail shows the stored detail lines verbatim; accept without reason refused (row unchanged); accept with reason sets status/reason/`accepted_at`; reopen resets status and appends the note; a GET never changes anything; accept/reopen refused on the wrong status; grid cell states FAIL / ACCEPTED / PASS / FIRST / NO_CSV / gap / IMPORT marker and a QUANTITY (blank-currency) exception marking its cell; navbar badge count for a logged-in user and absent for anonymous; no run of 8 digits in any rendered page (use letter-containing fake hash8 refs like `ab12cd34`). Check: `/opt/pm-venv/bin/python -m pytest tests/test_exceptions_page.py -q`. Commit.
7. **`tests/e2e/test_exceptions_page.py`** (navbar link, list loads, one filter, detail, accept with a reason) in the style of `tests/e2e/test_portfolio.py`. Check (Pi cannot run browsers): `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e/test_exceptions_page.py --collect-only -q`. Commit. tdw runs them.
8. **Docs section.** Check: `git status --short` shows only the docs file. Commit.
9. **Full test run** (no code expected to change):
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   Commit any fixups needed to make them pass.

## Stop conditions

- A `monthly_check` command test fails after the move in step 1 → stop, don't paper over it.
- A failure line would need a new exception kind or model change → stop and ask (no migrations in this plan).
- The stored `detail` cannot be rendered verbatim without escaping problems → stop and ask.
- Anything that would print or expose account digits → stop (the no-8-digits test guards this).
- A step turns out wrong → stop and report; the plan is revised first (plans/README rule).

## Open questions

1. **Taxable marker.** `Account` already has `category` with choices RRSP / LIRA / TFSA / TAXABLE / RESP / OTHER — use `category == "TAXABLE"`, no name list. Recommendation: treat only `"TAXABLE"` as taxable (leave `OTHER` non-taxable); tdw should confirm `TS_HUISGELD` and `TS_CAR_LIVING` have `category='TAXABLE'` in production (the page degrades gracefully — they just lose the badge — and the IB accounts get it for free later by setting their category).
2. **Docs location.** `monthly_check` is currently described only in `docs/plans/monthly-check-report.md` and `monthly-check-store.md`, which are frozen records. Recommendation: add the short "Exceptions page" section to `docs/IMPORT_COMMANDS.md` (the command reference) with the three URLs and the accept/reopen rules, pointing at the plans for background.
