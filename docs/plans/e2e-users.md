# Plan: e2e-users — fix the remaining 18 browser test failures in tests/e2e

Branch: `chore/e2e-users` (from `dev`). Tests only — **no app code, templates or settings changes**.

## Goal

tdw's `RUN_E2E=1` run left 18 failures in `tests/e2e`. Fix them:

1. **Main cause (12 tests):** `LiveServerTestCase` is a `TransactionTestCase`, so the DB is flushed after **every test**. Users (and one `Account`) created in `setUpClass` are gone from the second test on; login then fails and `wait_for_url` times out. Fix: create the test user in `setUp` instead of `setUpClass`, in every class that logs in.
2. **Dashboard-in-URL after login:** login redirects to `/` (`LOGIN_REDIRECT_URL = "/"`; the dashboard view is mounted at both `""` and `dashboard/`), so `assert "dashboard" in page.url` after a login is wrong. Fix each occurrence (see step list).
3. **Title:** `TestUserAuthentication.test_login_page_loads` asserts `"Login" in title`; the real title is `Sign In - Portfolio Manager`. Assert `"Sign In"` instead.
4. **`TestTransactionManagement` has no `_login` helper** but `test_transaction_filters` calls `self._login()`. Add one (same shape as the other classes' helpers).
5. **`TestResponsiveDesign.test_responsive_viewports`** uses `@pytest.mark.parametrize` on a `unittest` class (invalid). Use a `for … with self.subTest(...)` loop over the four viewports instead.
6. **`test_open_redirect_protection`** must go to `/login/?next=https://evil.com`, log in, and assert the **final URL is on the live server** (starts with `self.live_server_url`) and does not contain `evil.com` — not that the login-page URL contains "login".

## What changes (files)

Only these test files:

- `tests/e2e/test_e2e_user_journey.py` — 5 classes: move user creation `setUpClass` → `setUp`; drop the `tearDownClass` user deletes (rows are flushed per test anyway); fix title assert; fix dashboard-URL asserts (`test_successful_login`, `test_dashboard_loads`); rewrite `test_open_redirect_protection`; convert the parametrize to a `subTest` loop.
- `tests/e2e/test_user_workflows.py` — 5 classes: move user creation (and the `Account` in `TestTransactionManagement`) `setUpClass` → `setUp`; drop `tearDownClass` deletes; add `_login` to `TestTransactionManagement`; fix the dashboard-URL assert in `test_new_user_first_login`.
- `tests/e2e/test_api_endpoints.py` — 3 classes: move user creation `setUpClass` → `setUp`; drop `tearDownClass` deletes.
- `tests/e2e/test_smoke_pytest.py` — `TestInfrastructure`: move user creation to `setUp`; fix the dashboard-URL assert in `test_user_can_login`.
- `tests/e2e/test_login.py` — `test_login_with_valid_credentials`: replace the dashboard-URL assert with "redirected to the live-server root, away from /login/". Its `test_login_page_loads` already accepts `"Sign In"` — no change.

`tests/e2e/conftest.py`, `pw_session.py`, `test_complete_app.py`, `test_smoke.py`, `test_portfolio.py`, `test_reports.py` need no change (fixture-based, per-test `test_user`; already clean).

## Out of scope

- Any change to app code, URLs, views, templates, settings (incl. `LOGIN_REDIRECT_URL`).
- `tests/e2e/test_complete_app.py`, `test_smoke.py`, `test_portfolio.py`, `test_reports.py` (already pass).
- New browser tests, screenshots handling, CI wiring.
- Running the browser tests from Pi — impossible here; Pi's checks are `--collect-only`, `rg` and the normal gate. **tdw runs `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/ imports/tests/ -v` before merge.**

## Migrations

None.

## Steps (one commit each)

Pi cannot run the browser tests; every check below is one Pi can run.

1. **`tests/e2e/test_e2e_user_journey.py`**
   - In all 5 classes (`TestUserAuthentication`, `TestDashboardNavigation`, `TestPortfolioFeatures`, `TestResponsiveDesign`, `TestFormValidation`): create `cls.user` in `setUp` (use `self.user = …`; keep the existing auto-login `setUp` bodies, creating the user *before* the login steps). Remove the `setUpClass` user creation and the `tearDownClass` user deletes.
   - `test_login_page_loads`: `self.assertIn("Sign In", self.page.title())`.
   - `test_successful_login`: after `wait_for_url(f"{self.live_server_url}/")`, assert the URL is the live-server root and not the login page (e.g. `self.assertTrue(self.page.url.startswith(self.live_server_url))` and `"/login" not in self.page.url`) — no "dashboard" in URL.
   - `test_dashboard_loads`: `self.page.goto(f"{self.live_server_url}/dashboard/")` first, then keep the `dashboard`-in-URL assert.
   - `test_open_redirect_protection`: goto `/login/?next=https://evil.com`, log in, then `self.assertNotIn("evil.com", self.page.url)` and `self.assertTrue(self.page.url.startswith(self.live_server_url))`.
   - `test_responsive_viewports`: delete the `@pytest.mark.parametrize`; loop `for viewport, device_name in [("375x667", "iPhone 8"), ("768x1024", "iPad"), ("1440x900", "Desktop Small"), ("1920x1080", "Desktop HD")]` inside `with self.subTest(device_name=device_name):`, body unchanged.
   - Check: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` still collects everything; `rg -n "setUpClass|parametrize" tests/e2e/test_e2e_user_journey.py` is empty. Commit.
2. **`tests/e2e/test_user_workflows.py`**
   - All 5 classes: move user creation (and `TestTransactionManagement`'s `Account.objects.create(...)`) from `setUpClass` into `setUp`; remove `setUpClass`/`tearDownClass`. Keep `TestCompleteUserOnboarding.setUp`'s viewport line and its `filter(...).delete()` guard (now harmless but keep it before the create).
   - Add `_login` to `TestTransactionManagement` (copy the shape used in the other classes: goto `/login/`, fill `transaction_user` / `trans_pass_456`, submit, `wait_for_url(f"{self.live_server_url}/")`).
   - `test_new_user_first_login`: replace `assert "dashboard" in self.page.url.lower()` with "on the live-server root, away from /login/".
   - Check: collect-only as above; `rg -n "setUpClass" tests/e2e/test_user_workflows.py` empty. Commit.
3. **`tests/e2e/test_api_endpoints.py` + `tests/e2e/test_smoke_pytest.py`**
   - `TestAPIEndpoints`, `TestChartRendering`, `TestRealTimeFeatures`, `TestInfrastructure`: move user creation to `setUp`, drop `setUpClass`/`tearDownClass` (keep the mixin's `setUpClass` working — the classes just define `def setUp(self): super().setUp()` … nothing overrides it today, plain `setUp` is fine).
   - `test_user_can_login`: replace the dashboard-URL assert with the live-server-root assert.
   - Check: collect-only; `rg -n "setUpClass" tests/e2e/test_api_endpoints.py tests/e2e/test_smoke_pytest.py` empty. Commit.
4. **`tests/e2e/test_login.py`**
   - `test_login_with_valid_credentials(self, page, test_user, live_server)`: after `page.wait_for_url("/")`, assert `page.url.rstrip("/") == live_server.url` (add the `live_server` fixture to the signature; the `page` fixture is already bound to it).
   - Check: collect-only; `rg -n "dashboard" tests/e2e/test_login.py` empty. Commit.
5. **Final sweep + full tests (once, at the end):**
   - `rg -n "setUpClass" tests/e2e` → only `pw_session.py` (the mixin) remains.
   - `rg -n "dashboard.*page.url|page.url.*dashboard" tests/e2e` → only the `test_dashboard_loads` goto-based check and any `goto .../dashboard/` usages remain.
   - Run both gates:
     - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
     - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/` (plain run — e2e is collect-ignored without `RUN_E2E=1`)
   - Commit any leftovers; then hand off to tdw for the `RUN_E2E=1` run before merge.

## Stop conditions

- If `--collect-only` fails to import a module after a step (e.g. `playwright` not importable for Pi), stop and report — do not "fix" by skipping tests.
- If a dashboard-URL test still cannot be made to pass by the patterns above without touching app code or settings, stop and ask.
- If tdw's `RUN_E2E=1` run shows failures not covered here, stop; the plan is revised first (per `docs/plans/README.md`).
- Never print `.env`, statement files or `processed/json_logs/` content.

## Tests

Run **once**, at the end (step 5) — not after every step:

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

Browser tests themselves: tdw only, before merge: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/ imports/tests/ -v`.

## Open questions

1. *The task says "2 tests assert dashboard in the URL after login", but the pattern occurs 5 times* (`test_login.py::test_login_with_valid_credentials`, `test_e2e_user_journey.py::test_successful_login` and `::test_dashboard_loads`, `test_user_workflows.py::test_new_user_first_login`, `test_smoke_pytest.py::test_user_can_login`). **Recommended:** fix all five with the same pattern; the extra ones are the same bug and would fail once the user-in-setUp fix lets them run.
2. *`tearDownClass` user deletes:* keep or drop? **Recommended:** drop them — the rows are flushed after every test anyway, and `cls.user` no longer exists per-test.
3. *`test_dashboard_loads` intent:* assert root URL, or actually visit `/dashboard/`? **Recommended:** `goto /dashboard/` then keep the dashboard-in-URL assert — the test is about the dashboard page, and the route exists.
4. *`test_login.py::test_login_page_loads`* already accepts `"Sign In"` / `"portfolio"` in the title, so the title fix is only needed in `test_e2e_user_journey.py`. **Recommended:** leave `test_login.py`'s title assert as is.

All four questions are resolved — see Decisions below.

## Decisions (tdw, Oct 24)

tdw accepts the recommended answers to all open questions:

1. Fix **all five** dashboard-URL-after-login occurrences with the same pattern (`test_login.py::test_login_with_valid_credentials`, `test_e2e_user_journey.py::test_successful_login` and `::test_dashboard_loads`, `test_user_workflows.py::test_new_user_first_login`, `test_smoke_pytest.py::test_user_can_login`).
2. **Drop** the `tearDownClass` user deletes — rows are flushed after every test anyway, and `cls.user` no longer exists per-test.
3. `test_dashboard_loads`: `goto /dashboard/` first, then keep the dashboard-in-URL assert (the route exists; the test is about the dashboard page).
4. Leave `test_login.py::test_login_page_loads`'s title assert as is (it already accepts `"Sign In"`).

The steps above already reflect these answers.
