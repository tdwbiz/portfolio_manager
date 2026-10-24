# Plan: e2e-fix — make tests/e2e self-contained

Branch: `chore/e2e-fix` (from `dev`).

## Goal

The Playwright browser tests in `tests/e2e/` must run entirely from the test suite: a pytest-django `live_server` on the SQLite test database, never a hand-started dev server on port 8000 and never an `admin`/`admin` account that happens to exist. Exactly one Playwright session runs per pytest process and it is always shut down, even when a test class fails in `setUpClass`. The PyTorch example test is deleted.

## What changes (files)

- `tests/e2e/pw_session.py` — **new**. Process-wide Playwright singleton + a mixin for the `LiveServerTestCase` files.
- `tests/e2e/conftest.py` — `page` / `api_page` take base_url from the `live_server` fixture; `DJANGO_ALLOW_ASYNC_UNSAFE` via `setdefault`; browser fixtures use the singleton; new `test_user` fixture.
- `tests/e2e/test_api_endpoints.py`, `test_e2e_user_journey.py`, `test_smoke_pytest.py`, `test_user_workflows.py` — use the mixin instead of starting/stopping their own `sync_playwright`.
- `tests/e2e/test_login.py`, `test_portfolio.py`, `test_reports.py`, `test_complete_app.py` — log in with the `test_user` fixture instead of `admin`/`admin`.
- `tests/e2e/test_example.py` — **deleted** (it tests PyTorch; the only other mention is a historical row in `docs/audit/03-commands-scripts-docs.md`, which is a record and stays).
- `tests/e2e/README.md` — drop the PyTorch setup mention (one-line touch in the delete commit).

## Design: one Playwright session, always stopped (item 2)

Simplest safe way: a **module-level singleton with an `atexit` shutdown**, plus a mixin — not pytest fixtures, because the four files are `unittest` classes whose `setUpClass` cannot consume pytest fixtures.

```python
# tests/e2e/pw_session.py
import atexit
from playwright.sync_api import sync_playwright

_pw = None

def get_playwright():
    global _pw
    if _pw is None:
        _pw = sync_playwright().start()
        atexit.register(shutdown)
    return _pw

def shutdown():
    global _pw
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:
            pass
        _pw = None


class PlaywrightLiveServerMixin:
    """Mixin for LiveServerTestCase: shared Playwright, per-class browser + page."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()  # LiveServerTestCase has started the server
        try:
            cls.playwright = get_playwright()  # shared; never stopped here
            cls.browser = cls.playwright.chromium.launch(headless=True)
            cls.page = cls.browser.new_page()
        except Exception:
            super().tearDownClass()  # setUpClass failed -> tearDownClass will not run
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            super().tearDownClass()  # the shared Playwright is stopped by atexit
```

Why this is the simplest safe option:

- **Only one session:** `get_playwright()` starts `sync_playwright()` at most once per process; the conftest `browser_type` fixture uses the same singleton, so conftest-based and class-based tests cannot run two sessions.
- **Always stopped:** no class ever calls `playwright.stop()`; the `atexit` handler does, so a failing `setUpClass`, a failing `tearDownClass`, or an interpreter exit can't leak the driver process.
- **setUpClass failure:** unittest does *not* call `tearDownClass` when `setUpClass` raises, so the mixin's `try/except` calls `super().tearDownClass()` itself (stops the live server) and re-raises — the standard Django idiom.
- Classes keep their own `create_user` calls and test data; they just inherit browser/page plumbing from the mixin and delete their `sync_playwright().start()` / `.stop()` code.

## Out of scope

- Making every legacy e2e assertion pass (some old tests hit URLs like `/portfolio/`, `/reports/`, `/health/` that may not exist; fixing those is a follow-up).
- `tests/e2e/run_all_tests.py`, `pytest.ini`, `tests/conftest.py` (the `RUN_E2E` gate stays as is).
- Anything outside `tests/e2e/`; the audit docs; headed-mode / `run_e2e_tests.py` docs beyond the PyTorch line.
- Class-scoped `live_server` optimisation (see open questions).

## Steps (one commit each)

1. **Add `tests/e2e/pw_session.py`** with the singleton and mixin above.
   Check: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` (still green; playwright imports fine on saturn, browsers are only needed to *run*).
2. **Rewrite `tests/e2e/conftest.py`:**
   - `os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')` next to the existing `setdefault`s.
   - `browser_type` fixture yields `get_playwright().chromium` (drop `with sync_playwright()`).
   - `page` and `api_page` fixtures take `live_server` and use `base_url=live_server.url` (no more `http://127.0.0.1:8000`).
   - New fixture: `test_user(django_user_model)` → `django_user_model.objects.create_user(username="e2euser", password="e2e-pass-123")`.
   Check: `rg -n "8000" tests/e2e/conftest.py` is empty; `RUN_E2E=1 ... --collect-only -q`.
3. **Refactor the four LiveServerTestCase files** (`test_api_endpoints.py`, `test_e2e_user_journey.py`, `test_smoke_pytest.py`, `test_user_workflows.py`): inherit `PlaywrightLiveServerMixin` before `LiveServerTestCase`, delete their own `sync_playwright().start()` / `playwright.stop()` and browser/page creation, keep user/fixture data creation. `from pw_session import PlaywrightLiveServerMixin` works because pytest puts `tests/e2e` on `sys.path` (no `__init__.py`).
   Check: `rg -n "sync_playwright" tests/e2e/test_api_endpoints.py tests/e2e/test_e2e_user_journey.py tests/e2e/test_smoke_pytest.py tests/e2e/test_user_workflows.py` is empty; `RUN_E2E=1 ... --collect-only -q`.
4. **Conftest-based login tests create their own user:** in `test_login.py`, `test_portfolio.py`, `test_reports.py`, `test_complete_app.py`, add the `test_user` fixture parameter to the tests that fill the login form and use `test_user.username` / the fixture password instead of `admin`/`admin`.
   Check: `rg -n '"admin"' tests/e2e` is empty; `RUN_E2E=1 ... --collect-only -q`.
5. **Delete `tests/e2e/test_example.py`** (`git rm`) and remove the PyTorch bullet from `tests/e2e/README.md`.
   Check: `git status --short` shows the deletion; `rg -l test_example tests/` is empty.
6. **Full test run (once, at the end):**
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/` (normal gate — e2e is deselected without `RUN_E2E`)
   - `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e --collect-only -q` (Pi's ceiling)
   Then **tdw** runs `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/e2e -v` with browsers before merging.

## Stop conditions

- `RUN_E2E=1 pytest tests/e2e --collect-only -q` fails with an import or fixture error after any step → stop, fix or ask; do not push on.
- `live_server` fixture is unavailable (pytest-django missing/misconfigured) → stop and report.
- A step would require touching files outside `tests/e2e/` (except the README line) → stop and ask.
- Tests fail for reasons unrelated to self-containment (wrong URLs, missing views) → do **not** fix in this branch; list them in the summary for a follow-up.
- Never read, print or commit `.env`; never run against production settings.

## Open questions (all resolved — see Decisions below)

1. **Function-scoped `live_server` starts a server per test — slow.** Recommended: accept it; it is correct and simple. If the suite gets too slow, switch to a class-scoped `live_server` (pytest-django documents overriding the fixture scope) in a follow-up.
2. **Some legacy conftest tests will still fail on nonexistent URLs (`/portfolio/`, `/health/`).** Recommended: out of scope here; tdw's pre-merge run produces the failure list and a follow-up branch fixes or deletes those tests.
3. **`api_page` fixture appears unused.** Recommended: keep it (the task names it) and wire it to `live_server` like `page`; delete later if it stays unused.
4. **`test_smoke_pytest.py` vs `test_smoke.py` overlap.** Recommended: leave both; dedupe is a follow-up.

## Decisions (tdw, Oct 24)
tdw accepts the recommended answers in all four open questions:

1. **Function-scoped `live_server`:** accepted — keep it function-scoped for now (correct and simple). If the suite gets too slow, switch to a class-scoped `live_server` in a follow-up.
2. **Legacy URL failures (`/portfolio/`, `/health/`):** out of scope for this branch. tdw's pre-merge `RUN_E2E=1` run produces the failure list; a follow-up branch fixes or deletes those tests.
3. **`api_page`:** keep it and wire it to `live_server` like `page` (done in step 2); delete later if it stays unused.
4. **`test_smoke_pytest.py` vs `test_smoke.py`:** leave both; dedupe is a follow-up.
