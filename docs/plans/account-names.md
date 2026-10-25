# Plan: account names everywhere, integer ids in URLs (phase 2, step 9 — audit 4 batch E)

## Goal

Every screen, report, export (CSV and Excel) and admin list shows the **account name**, never the account number and never a number-valued `account_id`. The accounts page is the only page with digits and shows only `Account.last_4`. URLs, links, forms and query parameters identify accounts by `Account.pk` only (per `docs/architecture.md` §5 display rules; where audit 4 Q3/Q6 said "UI keeps full numbers", architecture.md wins).

No migrations. No data changes — tdw renames the number-valued `account_id` values in SSMS separately.

## What changes

- **Form validation (decision 3)**
  - `core/forms.py` — `AccountForm.account_id` rejects any value containing a digit (unit test in `test_account_crud.py`). The field itself stays until step 10.
- **URLs / views (pk routing)**
  - `core/urls.py` — `accounts/<str:account_id>/[edit/|delete/]` → `accounts/<int:pk>/…`. Ledger URL is parked (see Decisions).
  - `core/views_accounts.py` — `account_detail/edit/delete` look up by `pk`; internal redirects use `pk`.
- **Query parameters (`?account=`) switch from account_id value to pk**
  - `core/views.py` (dashboard, gap-analysis page context, generate-statements, generate-annual, reverse-statements), `core/views_reports.py` (income, realised P&L, tax summary), `core/views_transactions.py` (list + export filters).
  - Templates with `<option value="{{ acct.account_id }}">` → `value="{{ acct.pk }}"`, label = account name: `transaction_list.html`, `income_summary.html`, `realised_pnl.html`, `generate_statements.html`, `generate_annual.html`, `reverse_statements.html`.
- **Display: name instead of account_id in active templates**
  - `dashboard.html` (account rows + recent transactions), `transaction_list.html`, `gap_analysis.html`, `generate_statements.html`, `generate_annual.html`, `income_summary.html`, `realised_pnl.html`, `reverse_statements.html`.
  - `accounts.html` — replace the `account_id` column with **last-4** (`••••{{ account.last_4 }}`); links use `account.pk`. This stays the only page with digits.
  - `account_detail.html` — drop the "Account ID: {{ account.account_id }}" line (name is already the heading); links use `pk`.
- **Exports**
  - `core/views_transactions.py` — CSV and Excel exports: account column `tx.account.account_id` → `tx.account.name`; the `?account=` filter uses pk.
- **Admin (`core/admin.py`)**
  - `AccountAdmin`: `list_display`/`search_fields` use `name` (+ `last_4`), not `account_id`.
  - Related-model admins: replace `account_number` / `account__account_id` in `list_display`/`search_fields` with the account **name** (e.g. `account__name`). Columns themselves are untouched (step 10).
- **Report/util code feeding the above templates**
  - `core/utils/gap_analysis.py` — result dict carries `account_name` (name) for display; template uses it.
  - `core/utils/portfolio.py` (realised P&L grouping) and `core/views_reports.py` — display value = account name; internal grouping keys may stay as they are as long as nothing number-valued reaches a template.
- **Tests**
  - `core/tests/*` — reverse URLs with `pk`, assert names (not numbers) in HTML: at least `test_account_crud.py`, `test_views.py`, `test_dashboard.py`, `test_export.py`, `test_income_summary.py`, `test_realised_pnl.py`, `test_reverse_statements.py`, `test_batch_statements.py`, `test_portfolio_summary.py`, `test_performance_chart.py`, `test_tax_summary.py`, `test_reconciliation.py` (only if it touches the active generate/reconcile page URLs), `test_account_currency.py`.
  - `tests/e2e/*.py` — update expectations that look for numbers in pages or URLs (Pi cannot run them; tdw runs `RUN_E2E=1` before merge).

## Out of scope

- Removing `account_id` / `account_number` columns, scrubbing free text, transaction hash — step 10.
- Log filter and safe file names — step 11.
- Any data change (tdw renames number-valued `account_id` values in SSMS).
- **Parked pages** (risk module, ledger, old reconciliation): not changed — see Open questions.
- Management commands (`write_ledger`, `generate_*`, `reconcile`, `--account` args) and `scripts/` — not screens/URLs; leave for step 10+.
- No migrations. `Account.account_id` stays as a column until step 10 (decision 3).

## Steps (one commit each)

1. **pk URLs for account detail/edit/delete.** `core/urls.py`, `core/views_accounts.py`, links in `accounts.html` / `account_detail.html`; update `test_account_crud.py`, `test_views.py`.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_account_crud core.tests.test_views --settings=portfolio_manager.settings_test` and `git status` shows only these files.
2. **`AccountForm` rejects number-valued `account_id`.** `core/forms.py` — validation on the `account_id` field rejecting any value containing a digit (decision 3); unit test in `test_account_crud.py`.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_account_crud --settings=portfolio_manager.settings_test`.
3. **Exports show names.** `core/views_transactions.py` CSV + Excel account column → `account.name`; `?account=` filter by pk; update `test_export.py` (and `transaction_list.html` option values, since the filter param changes here).
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_export --settings=portfolio_manager.settings_test`.
4. **Admin shows names.** `core/admin.py` list_display/search_fields → `name` / `account__name` / last-4; no `account_id`/`account_number` in any admin column.
   Check: `/opt/pm-venv/bin/python manage.py check --settings=portfolio_manager.settings_test` and `rg -n "account_id|account_number" core/admin.py` returns nothing.
5. **`?account=` query params → pk on the remaining pages.** `core/views.py`, `core/views_reports.py` and the option values in `income_summary.html`, `realised_pnl.html`, `generate_statements.html`, `generate_annual.html`, `reverse_statements.html`; update the matching tests.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_income_summary core.tests.test_realised_pnl core.tests.test_reverse_statements core.tests.test_batch_statements core.tests.test_tax_summary --settings=portfolio_manager.settings_test`.
6. **Names in the remaining active templates and report utils.** `dashboard.html`, `gap_analysis.html`, result dicts in `core/utils/gap_analysis.py` and realised-P&L display values in `core/utils/portfolio.py` / `core/views_reports.py`; `accounts.html` shows last-4 instead of `account_id`; `account_detail.html` drops the number line (decision 4); update `test_dashboard.py`, `test_portfolio_summary.py`, `test_performance_chart.py`.
   Check: `rg -l "account\.account_id|account__account_id" core/templates/core/` lists only parked templates (`ledger_month.html`, `reconciliation.html`).
7. **Browser tests.** Update `tests/e2e/*.py` expectations (names in pages, pk in URLs).
   Check: `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/ --collect-only -q` collects without errors (full run is tdw's).
8. **Full test pass + docs touch.** Run both suites (below); if `AGENTS.md`/docs mention number-based URLs, fix the wording. Commit.

## Tests (once, at the end)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test   # must stay clean
```

Browser tests (`RUN_E2E=1 … -v`) are run by tdw before the merge, not by Pi.

## Stop conditions

- A test expects a number in a page or URL and changing it would change behaviour beyond display/identification — stop and ask.
- A template or view outside the listed active pages shows an account number — stop and report instead of silently widening scope.
- Any step needs a migration — stop (step 9 has none).
- An e2e file cannot be updated by reading alone (needs a live browser run to verify) — leave a note in the commit and stop; tdw runs them.
- If a step turns out wrong, stop and report; the plan is revised first (docs/plans/README.md).

## Decisions (tdw, Oct 25)

Answers to the open questions below:

1. **Parked pages:** yes — leave them untouched in this step (as recommended).
2. **Redirect from old number-based URLs:** yes — skip it, no redirect (as recommended).
3. **`AccountForm.account_id`:** keep the field until step 10, but add form validation that rejects any `account_id` value containing a digit, with a unit test (implemented as step 2).
4. **`account_detail.html`:** yes — no digits on the detail page; the name is the heading (as recommended).

## Open questions

1. **Parked pages (risk, ledger, old reconciliation) show account numbers.** `ledger_month.html`/`views_ledger.py` display and link by `account_id`; `reconciliation.html` (old reconcile page) shows `account.account_id`; the risk module uses `account_id` throughout. **Recommendation:** leave all of them untouched in this step (they are parked per architecture §11); after tdw renames `account_id` values to names in SSMS they will show names anyway. Revisit when each module is un-parked.
2. **Redirect from old number-based URLs (audit 4 batch E suggested one for bookmarks).** **Recommendation:** skip it — the SSMS rename makes old number URLs unresolvable regardless, and the app is single-user; a dead bookmark is cheaper than a compat route.
3. **`AccountForm` still has an `account_id` field (create/edit).** **Recommendation:** leave it in this step; the column and the field go together in step 10. New accounts get their number only via `set_account_fingerprint`, so the field is effectively a label today.
4. **`account_detail.html` — show last-4 there too?** Architecture §5 says the accounts page is the *only* page with digits. **Recommendation:** drop all digits from the detail page; the name is the heading.
