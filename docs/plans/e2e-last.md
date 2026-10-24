# Plan: e2e-last — two small browser-test fixes (branch chore/e2e-last)

## Goal

1. Fix `TestDashboardNavigation.test_navigation_menu` (tests/e2e/test_e2e_user_journey.py): it clicks
   menu items found with loose `a:has-text("<label>")` selectors and `links[0]`, and fails by staying
   on `/dashboard/` when a click lands on an element that does not navigate (the current base-template
   menu has no plain "Reports" link — the matching element is the Bootstrap dropdown toggle with
   `href="#"`; the same loose-matching pattern is what breaks the "Portfolio" expectation). The test's
   link list and selectors must match the **current** menu in `core/templates/core/base.html`.
2. Serve static files on the live test servers so pages load with CSS/JS:
   - unittest classes: use `django.contrib.staticfiles.testing.StaticLiveServerTestCase` instead of
     `django.test.LiveServerTestCase`.
   - pytest: pytest-django's `live_server` fixture runs `django.test.LiveServer` with the plain WSGI
     app, which does **not** serve static files (the `TypeError` / `NoneType` document-root error from
     `django/views/static.py`). Override the `live_server` fixture in `tests/e2e/conftest.py` to wrap
     the application in `django.contrib.staticfiles.handlers.StaticFilesHandler` — the same mechanism
     `StaticLiveServerTestCase` uses.

## What changes (files)

- `tests/e2e/test_e2e_user_journey.py` — rewrite `test_navigation_menu`; switch 5 classes to
  `StaticLiveServerTestCase`.
- `tests/e2e/test_api_endpoints.py` — switch 3 classes to `StaticLiveServerTestCase`.
- `tests/e2e/test_user_workflows.py` — switch 5 classes to `StaticLiveServerTestCase`.
- `tests/e2e/test_smoke_pytest.py` — switch 1 class to `StaticLiveServerTestCase`.
- `tests/e2e/conftest.py` — session-scoped `live_server` fixture override that wraps the WSGI app in
  `StaticFilesHandler`.
- `tests/e2e/pw_session.py` — docstring only (mentions `LiveServerTestCase`).

No app code, templates or settings are changed. No migrations.

## Out of scope

- Any change to `core/templates/core/base.html` or any other template/view/settings.
- Testing the dropdown menus (Risk / Reports / Data Tools toggles need Bootstrap JS clicks) — only
  top-level links with real `href`s are asserted.
- The other pytest e2e files that use the fixture-based `page` (test_login.py, test_portfolio.py,
  test_reports.py, test_smoke.py, test_complete_app.py) — they get static files for free from the
  conftest fixture override; no per-file changes.
- `tests/e2e/run_all_tests.py` unless it references `LiveServerTestCase` (check with rg).

## Steps

1. **Fix `test_navigation_menu`.** Replace the `nav_links` loop in
   `TestDashboardNavigation.test_navigation_menu` with navbar-scoped, exact-text selectors that match
   the current menu in `base.html`, one real link per entry, each asserting the real path:
   - Dashboard → `/dashboard/`
   - Portfolio → `/portfolio/`
   - Transactions → `/transactions/`
   - Accounts → `/accounts/`
   - Annual Reports → `/annual-reports/`
   Use e.g. `self.page.locator("nav a.nav-link", has_text=...)` / `get_by_role("link", name=..., exact=...)`
   (never `links[0]` on a page-wide `a:has-text`), `wait_for_url` instead of a fixed 500 ms sleep, and
   `goto` the dashboard before each click so a failed click cannot cascade. Drop the "Reports" entry
   (top-level "Reports" is a `href="#"` dropdown toggle).
   Check: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` and
   `rg -n "has-text" tests/e2e/test_e2e_user_journey.py` (no loose page-wide matches left in this test).
   Commit: `test(e2e): navigation test uses the real navbar links from base.html`.

2. **StaticLiveServerTestCase for the unittest-style e2e classes.** In
   `test_e2e_user_journey.py`, `test_api_endpoints.py`, `test_user_workflows.py`,
   `test_smoke_pytest.py`: add `from django.contrib.staticfiles.testing import StaticLiveServerTestCase`
   and change every `(PlaywrightLiveServerMixin, LiveServerTestCase)` to
   `(PlaywrightLiveServerMixin, StaticLiveServerTestCase)` (remove the now-unused
   `LiveServerTestCase` import where nothing else uses it; keep it in `pw_session.py`'s docstring or
   reword the docstring).
   Check: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` and
   `rg -n "LiveServerTestCase" tests/e2e/*.py` (every class line shows `StaticLiveServerTestCase`).
   Commit: `test(e2e): live server classes serve static files`.

3. **Serve static files on the pytest `live_server` fixture.** In `tests/e2e/conftest.py`, override
   the session-scoped `live_server` fixture (shadows pytest-django's): depend on
   `django_test_environment`, build `LiveServer(StaticFilesHandler(get_wsgi_application()))`, start it,
   yield it, stop it — mirroring what `StaticLiveServerTestCase` does. Keep the existing `page` /
   `api_page` fixtures unchanged (they already take `live_server`).
   Check: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` and
   `rg -n "def live_server" tests/e2e/conftest.py`.
   Commit: `test(e2e): pytest live_server serves static files via StaticFilesHandler`.

4. **Full test gate (once, at the end).**
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/` (plain run; browser tests skipped without RUN_E2E)
   Then tdw runs: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/ imports/tests/ -v` before merge.
   Commit any fixups needed to make the gate pass (still no app/template/settings changes).

## Stop conditions

- Stop and ask if making any of this pass would require touching app code, templates or settings.
- Stop if the `live_server` override cannot be made to work with pytest-django 4.14's fixture graph
  (report instead of hacking around it in the tests that use the fixture).
- Stop if `--collect-only` errors after a step, or the final gate fails for reasons unrelated to these
  two fixes.
- Pi cannot run the browser tests; do not claim they pass — tdw verifies with RUN_E2E=1 before merge.

## Open questions (resolved — see Decisions below)

1. Which element exactly matched `a:has-text("Portfolio")` at runtime cannot be confirmed without a
   browser run (static reading of base.html shows a real "Portfolio" → `/portfolio/` link; the
   page-wide `links[0]` pattern plus the `href="#"` dropdown toggles is the failure class the task
   describes). **Decided:** the rewrite removes all loose page-wide matching and clicks only
   navbar `a.nav-link` elements with real hrefs, so the test is correct either way; tdw's RUN_E2E run
   confirms.
2. Should the dropdown menus (Risk, Reports, Data Tools) be covered too? **Decided:** no — out of
   scope here; they need Bootstrap JS interaction and can get their own test later.
3. Should the `live_server` override live in `tests/e2e/conftest.py` or a root conftest?
   **Decided:** `tests/e2e/conftest.py` only — that is the only suite using `live_server`.

## Decisions (tdw, Oct 24)

tdw accepts the recommended answers to all three open questions above:

1. Rewrite `test_navigation_menu` to click only navbar `a.nav-link` elements with real hrefs (no
   loose page-wide `has-text` matching); tdw's RUN_E2E run before merge is the confirmation.
2. Dropdown menus (Risk / Reports / Data Tools) are out of scope — no Bootstrap JS interaction in
   this change; a dedicated test can come later.
3. The `live_server` fixture override goes in `tests/e2e/conftest.py` only, not a root conftest.

## Decisions (tdw, Oct 24 — after first browser run)

The StaticLiveServerTestCase switch works (no more static NoneType errors), but the two remaining
defects are fixed as follows:

1. **Remove the `live_server` fixture override in `tests/e2e/conftest.py` completely** (supersedes
   step 3). The `_StaticLiveServer` shim broke all 35 pytest-style tests at setup with
   `AttributeError: '_StaticLiveServer' object has no attribute '_live_server_modified_settings'`
   (pytest-django, `fixtures.py` line 663, `_live_server_helper`). pytest-django's own
   `live_server` fixture already serves static files when `django.contrib.staticfiles` is in
   INSTALLED_APPS — `pytest_django/live_server_helper.py` lines 30–33 wrap the server in
   `StaticFilesHandler` in exactly that case. It **is** installed for the test settings:
   `portfolio_manager/settings_test.py` line 5 (`from .settings import *`) inherits
   `INSTALLED_APPS` from `portfolio_manager/settings.py`, where line 56 is
   `"django.contrib.staticfiles",`. So the override is deleted and the existing `page` /
   `api_page` fixtures keep depending on pytest-django's `live_server` unchanged.
2. **`test_navigation_menu`: every selector must match the base-template markup exactly.** The run
   timed out waiting for `locator("nav").get_by_role("link", name="Dashboard", exact=True)`. Exact
   top-level link markup in `core/templates/core/base.html` (all inside `<ul class="navbar-nav">`,
   line 69; each is `<a class="nav-link …" href="{% url '…' %}">`):
   - Dashboard — `<a>` lines 71–76, `href` line 73 (`{% url 'dashboard' %}` → `/dashboard/`),
     label line 75: `<i class="bi bi-speedometer2"></i> Dashboard`
   - Portfolio — `<a>` lines 79–84, `href` line 81 (`portfolio_summary` → `/portfolio/`), label
     line 83
   - Transactions — `<a>` lines 111–116, `href` line 113 (`transaction_list` → `/transactions/`),
     label line 115
   - Accounts — `<a>` lines 119–124, `href` line 121 (`accounts_list` → `/accounts/`), label
     line 123
   - Annual Reports — `<a>` lines 135–141, `href` line 137 (`annual_reports` → `/annual-reports/`);
     the label wraps across two source lines ("Annual" line 139 / "Reports" line 140)

   A role/name query inside `<nav>` cannot disambiguate "Dashboard": the Risk dropdown contains a
   second link with that name (`a.dropdown-item`, lines 159–163). Each top-level link is therefore
   selected uniquely by `nav ul.navbar-nav a.nav-link[href="<path>"]` — dropdown toggles are
   `a.nav-link.dropdown-toggle` with `href="#"` and dropdown items are `a.dropdown-item`, so the
   selector matches exactly one element per entry. The test also asserts the whitespace-normalised
   label text before clicking (guards the two-line "Annual Reports" case).
