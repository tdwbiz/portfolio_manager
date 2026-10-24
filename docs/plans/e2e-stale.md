# Plan: e2e-stale — fix the stale Playwright tests in tests/e2e

Branch: `chore/e2e-stale` (from `dev`).

## Goal

tdw's `RUN_E2E=1` run of `tests/e2e` produced 38 failures. The tests were written against an older app (requests-style `page.get`, `page.url()`, an old `Account` model, a `/core/reports/` URL, a `**/dashboard/**` post-login URL). Fix every failing test to match the current app. **No app code, templates or settings change.** Pi cannot run browser tests; its checks are `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q`, `rg`, and the normal gate. tdw runs the browser tests before merge.

Facts established from the current code (verify with the cited files, do not re-litigate):

- **Login redirects to `/`** — `core/views_auth.py:63` (`redirect("/")`) and `core/urls.py:52` (`path("", dashboard, name="dashboard")`). So every `wait_for_url("**/dashboard/**")` can never match: it is one of the two 30 s timeouts (test_login.py:29) and will break every class test once `page.get` is fixed. Replace with `wait_for_url("/")` (fixture tests, base_url set) or `wait_for_url(f"{self.live_server_url}/")` (class tests).
- **`page.get(url)` → `page.goto(url)`** — 28 AttributeErrors across `test_smoke_pytest.py`, `test_e2e_user_journey.py`, `test_api_endpoints.py`, `test_user_workflows.py`.
- **`page.url()` → `page.url`** (property) — test_reports.py:41,67 and test_portfolio.py:33 (3 TypeErrors).
- **`page.status_code` does not exist** — use the response returned by `page.goto()`: `resp = page.goto(...); resp.status`. Occurrences: test_smoke.py:47 (the reported one), test_complete_app.py:133 and :177 (same bug, fix together), plus `page.response_status()` in test_smoke.py:20 (swallowed by `except`, fix anyway).
- **Current `Account` fields** (core/models.py:138–183): `account_id` (unique, e.g. `"90000001"`), `name`, `broker` (`"RBC"`), `currency` (deprecated but present, `"CAD"`), `category` (default `"TAXABLE"`), `owner`, deprecated `starting_balance*`. **No `user`, `account_number` or `institution` fields.**
- **Reports URL is `/annual-reports/`** (`core/urls.py:105`); there is no `/core/reports/` (URLs are at root). Nav link text is "Annual Reports" (base.html:139) — `a:has-text("Report")` is strict-mode-fragile; navigate by URL instead.
- **Login error message** is `"Invalid username or password."` rendered as `{{ error }}` inside `div.alert.alert-danger` (core/views_auth.py:73, core/templates/core/login.html:18–21). `page.text_content("div")` is a strict-mode violation (many divs).
- **`TestReportGeneration`** (test_user_workflows.py:162) calls `self._login()` (line 197) but has no `_login`; sibling classes define their own `_login` helper against their class user — add the same for `report_user` / `report_pass_789`.

## What changes (files)

Only files under `tests/e2e/`:

- `tests/e2e/test_smoke_pytest.py` — `page.get` → `page.goto`; dashboard wait → root URL.
- `tests/e2e/test_e2e_user_journey.py` — same.
- `tests/e2e/test_api_endpoints.py` — same.
- `tests/e2e/test_user_workflows.py` — `page.get` → `page.goto`; dashboard waits → root URL; fix `Account.objects.create` (test_user_workflows.py:112) to `Account.objects.create(account_id="90000001", name="ACCOUNT_A", broker="RBC", currency="CAD")`; add `_login` to `TestReportGeneration`; `/core/reports/` → `/annual-reports/`.
- `tests/e2e/test_login.py` — dashboard wait → root URL; invalid-login assertion → wait for `.alert-danger` and assert `"Invalid username or password"` in its text; fix the contradictory asserts in `test_login_form_validation` (lines 58–59: `assert "/login/" in page.url` then `assert "/login/" not in page.url` — the second can never pass; keep only `assert "/login/" in page.url`, the empty submit is blocked by the HTML `required` attribute so the URL stays at /login/).
- `tests/e2e/test_portfolio.py` — after login wait for `/` then `page.goto("/portfolio/")` / `page.goto("/transactions/")`; `page.url()` → `page.url`; **delete `test_portfolio_detail_page`** (see Deletions).
- `tests/e2e/test_reports.py` — replace `page.click('a:has-text("Report")')` with `page.goto("/annual-reports/")`; after login wait for `/`; `page.url()` → `page.url`.
- `tests/e2e/test_smoke.py` — favicon: `resp = page.goto("/favicon.ico"); assert resp.status in [200, 404]`; health check: `resp = page.goto("/health/"); assert resp.status == 200` (still inside the try/except).
- `tests/e2e/test_complete_app.py` — :133 and :177 use `resp = page.goto(...)` + `resp.status`; :177 also drops the hardcoded `http://localhost:8000/` (live server uses a random port) and uses the relative `"/"`.

`tests/e2e/conftest.py`, `pw_session.py`, `run_all_tests.py` — no changes (already fixed by the e2e-fix plan; `run_all_tests.py` is not collected by pytest).

## Deletions (with reasons)

- `test_portfolio.py::test_portfolio_detail_page` — it clicks a nav link to reach a "specific portfolio" detail page; no portfolio detail page exists in the current URLconf (only `/portfolio/` summary and `/portfolio/chart/`, core/urls.py:67–69). The chart page is already covered by `test_api_endpoints.py::TestPortfolioCharts`.

No other deletions: every other failing test targets a page that still exists.

## Out of scope

- Any change to app code, templates, URLs or settings.
- `tests/e2e/conftest.py`, `pw_session.py` (already fixed in the e2e-fix plan).
- `tests/e2e/run_all_tests.py` (standalone runner, not collected).
- `docs/plans/e2e-fix.md` and the audit docs.
- Adding new coverage; only making the existing tests match the app.

## Steps (one commit each)

1. **test_smoke_pytest.py + test_e2e_user_journey.py**: `self.page.get(` → `self.page.goto(`; `wait_for_url("**/dashboard/**"[, timeout=…])` → `wait_for_url(f"{self.live_server_url}/")`.
   Check: `rg -n 'page\.get\(|\*\*/dashboard' tests/e2e/test_smoke_pytest.py tests/e2e/test_e2e_user_journey.py` → no output; `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` → no errors. Commit.
2. **test_api_endpoints.py**: same two replacements.
   Check: `rg -n 'page\.get\(|\*\*/dashboard' tests/e2e/test_api_endpoints.py` → no output; collect-only. Commit.
3. **test_user_workflows.py**: same two replacements; fix `Account.objects.create` to the current fields with fake account `90000001` / `ACCOUNT_A`; add `_login` to `TestReportGeneration` (copy the `TestPerformanceMetrics._login` pattern with `report_user` / `report_pass_789`); `/core/reports/` → `/annual-reports/`.
   Check: `rg -n 'page\.get\(|\*\*/dashboard|user=cls\.user|account_number|institution|core/reports' tests/e2e/test_user_workflows.py` → no output; `rg -c 'def _login' tests/e2e/test_user_workflows.py` → 3; collect-only. Commit.
4. **test_login.py**: dashboard wait → `wait_for_url("/")`; invalid-login test waits for `.alert-danger` and asserts `"invalid username or password" in page.text_content(".alert-danger").lower()`; remove the inverted second assert in `test_login_form_validation`.
   Check: `rg -n 'text_content\("div"\)|\*\*/dashboard|not in page\.url' tests/e2e/test_login.py` → no output; collect-only. Commit.
5. **test_portfolio.py + test_reports.py**: apply the fixes listed above, delete `test_portfolio_detail_page`.
   Check: `rg -n 'page\.url\(\)|\*\*/|has-text\("Report"\)' tests/e2e/test_portfolio.py tests/e2e/test_reports.py` → no output; `rg -c 'def test_' tests/e2e/test_portfolio.py` → 2; collect-only. Commit.
6. **test_smoke.py + test_complete_app.py**: replace `page.status_code` / `page.response_status()` with the `page.goto()` response's `.status`; drop the hardcoded `:8000` URL.
   Check: `rg -n 'status_code|response_status|localhost:8000' tests/e2e/test_smoke.py tests/e2e/test_complete_app.py` → no output; collect-only. Commit.
7. **Full sweep + gate**:
   `rg -n 'page\.get\(|page\.url\(\)|status_code|\*\*/dashboard|response_status' tests/e2e/` → no output.
   `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` → all tests collected, no errors.
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   Commit (amend nothing; final commit only if step 6 left anything).

## Tests (once, at the end — step 7)

- `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
- `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/` (normal run: browser tests skipped without `RUN_E2E=1`)
- tdw before merge: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e -v`

## Stop conditions

- A fix would require touching app code, templates or settings — stop and ask.
- A failure appears in tdw's run that is not in the 38 reported (new category) — stop, report, re-plan.
- A test cannot be made to match the app without deleting a feature test not listed in Deletions — stop and ask.
- Pi is asked to run the browser tests — it cannot (no playwright browsers); tdw runs them.

## Open questions

1. **Change all `wait_for_url("**/dashboard/**")` even though only 2 timeouts were reported?** Yes — login redirects to `/` (views_auth.py:63), so the class-based ones are hidden behind the earlier `page.get` AttributeError and would all time out after step 1–3. Recommended: replace every occurrence as planned.
2. **`test_portfolio_detail_page`: rewrite against `/portfolio/chart/` or delete?** Recommended: delete (chart is already covered in `test_api_endpoints.py`); listed under Deletions.
3. **`test_smoke.py::test_health_check` hits `/health/`, which does not exist (guarded by try/except).** Recommended: keep the test, just fix `response_status()` → `resp.status`; it stays a no-op until a health endpoint exists.
4. **`test_complete_app.py::test_https_redirect` hardcodes `http://localhost:8000/` and can never test anything on the live server.** Recommended: minimal rewrite to the relative `"/"` with `resp.status` (kept in try/except); delete only if tdw prefers.
