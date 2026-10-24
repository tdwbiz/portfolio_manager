# Plan: separate Playwright (browser) tests from the normal pytest run

## Goal

`/opt/pm-venv/bin/python -m pytest tests/` must work for Pi with **no `--ignore` flags**.
Today it fails during collection: `tests/e2e/conftest.py` imports `playwright` at module
level and playwright is not installed in `/opt/pm-venv`
(`pytest tests/ --collect-only` → `ERROR tests/e2e - ModuleNotFoundError: No module named 'playwright'`).

After this change:

- `pytest tests/` collects only the plain tests (`tests/test_vl_config.py`, `tests/test_vl_parsers.py`).
- All browser tests live under `tests/e2e/` and are collected **only** when `RUN_E2E=1` is set
  (tdw only — Pi's sandbox can never run browser tests; playwright may be installed into the
  shared `/opt/pm-venv` later for tdw, so we gate on the env var, not on the import failing).
- No behaviour changes to the app; no production code touched.

## What changes

**Files moved (git mv, contents otherwise untouched except the path fix below):**

- `tests/test_api_endpoints.py` → `tests/e2e/test_api_endpoints.py`
- `tests/test_smoke_pytest.py` → `tests/e2e/test_smoke_pytest.py`
- `tests/test_e2e_user_journey.py` → `tests/e2e/test_e2e_user_journey.py`
- `tests/test_user_workflows.py` → `tests/e2e/test_user_workflows.py`

(All four launch their own browser via `from playwright.sync_api import sync_playwright`
inside `setUpClass`, use `LiveServerTestCase`, and do their own Django setup — they do **not**
use the `page`/`browser` fixtures from `tests/e2e/conftest.py`, so no fixture work is needed.)

**Path fix in the moved files:** each has

```python
project_root = str(Path(__file__).parent.parent)
```

which pointed at the project root from `tests/`; from `tests/e2e/` it must be
`Path(__file__).parent.parent.parent`. (Same one-liner in all four files.)

**`tests/conftest.py`** — add the gate near the top (after `import os`):

```python
# Browser tests need Playwright; only collected when RUN_E2E=1 (tdw only, never Pi).
collect_ignore = [] if os.environ.get("RUN_E2E") == "1" else ["e2e"]
```

`collect_ignore = ["e2e"]` skips the whole directory, including its `conftest.py`, so
playwright is never imported when the gate is closed. (`collect_ignore_glob = ["e2e/*"]`
would also work, but ignores the children one by one instead of the directory itself;
the plain `collect_ignore` is simpler and complete.)

**Scripts / docs that reference the moved paths:**

- `run_e2e_tests.py` (project root): file list at ~lines 129–131 and the docstring/usage
  examples (~lines 11, 147, 162) reference `tests/test_e2e_user_journey.py`,
  `tests/test_user_workflows.py`, `tests/test_api_endpoints.py` → change to `tests/e2e/...`.
- `tests/e2e/run_all_tests.py`: only runs `test_complete_app.py` (already in `tests/e2e/`),
  so no path change needed — verify with grep that nothing else in it points at the old paths.
- `tests/README.md`: update the references to the three moved file names (~lines 98, 147,
  156, 164, 374–376) to the new `tests/e2e/...` paths, and note the `RUN_E2E=1` requirement.
- `AGENTS.md` "Key commands": the pytest line becomes the **normal** run
  (`/opt/pm-venv/bin/python -m pytest tests/` — no browser tests, works for Pi) and a new
  line documents browser tests: `RUN_E2E=1 /opt/pm-venv/bin/python -m pytest tests/ -v`
  marked **tdw only, not Pi** (needs playwright + browsers in `/opt/pm-venv`).

**Not touched:** `docs/plans/` (except this file), `docs/audit/`, `docs/architecture.md`,
`pytest.ini` (its `asyncio_mode = auto` and markers stay as-is; unknown ini keys are ignored
without `--strict-config`), all app code.

## Migrations

None.

## Tests

- Cheap check per step: `git status --short` and
  `/opt/pm-venv/bin/python -m pytest tests/ --collect-only -q 2>&1 | tail -3`.
- Final, once:
  - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
  - `/opt/pm-venv/bin/python -m pytest tests/` (no ignore flags) — must collect and pass the
    two plain vl test modules; `tests/e2e` silently skipped.

## Steps

1. **Move the four files into `tests/e2e/` with `git mv`** and fix the `project_root`
   line in each (`parent.parent` → `parent.parent.parent`). No other content changes.
   Check: `git status --short` shows four renames;
   `pytest tests/ --collect-only -q | tail -3` — still the same single pre-existing
   `ERROR tests/e2e - ModuleNotFoundError: No module named 'playwright'` as the baseline,
   no new errors. Commit.
2. **Add the `RUN_E2E` gate to `tests/conftest.py`** (snippet above).
   Check: `pytest tests/ --collect-only -q | tail -3` now shows only the `test_vl_*` tests
   collected and **0 errors**; with `RUN_E2E=1` the same command still errors on playwright
   (expected on Pi). Commit.
3. **Update scripts and docs that point at the old paths**: `run_e2e_tests.py`
   (file list + usage strings), `tests/README.md` (paths + a line saying browser tests
   need `RUN_E2E=1` and are tdw-only); grep-verify `tests/e2e/run_all_tests.py` needs
   nothing. Check: `grep -rn "tests/test_api_endpoints\|tests/test_smoke_pytest\|tests/test_e2e_user_journey\|tests/test_user_workflows" --include="*.py" --include="*.md" . | grep -v __pycache__`
   returns nothing; collect-only unchanged. Commit.
4. **Update `AGENTS.md` key commands** as described above (normal pytest line +
   `RUN_E2E=1` browser line, tdw only). Check: `grep -n "RUN_E2E" AGENTS.md`. Commit.
5. **Full test run** (no code changes expected):
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/`
   Both must pass. If anything fails, fix and amend into a small fix commit; commit only if
   a fix was needed.

## Open questions

- Branch: work continues on the current `chore/test-setup` branch (plans say `feature/<name>`
  from `dev`; `chore/test-setup` already exists and matches the junk-out naming style — OK?)
- The four moved files each start their own browser in `setUpClass` instead of using the
  shared session-scoped `browser`/`page` fixtures in `tests/e2e/conftest.py`. Refactoring
  them onto the shared fixtures would be faster but is a behaviour change to the tests —
  left out of this plan on purpose. Do it later?
- `pytest.ini` has `-x` in `addopts` (stop after first failure) — kept as-is; fine for the
  two remaining plain test modules?
- `tests/e2e/run_all_tests.py` uses `--html` (pytest-html) and `test_complete_app.py`;
  whether those run at all on tdw's `/opt/pm-venv` is untested from Pi. tdw to verify once
  with `RUN_E2E=1` after playwright is installed there.
- Should the moved files also get a `@pytest.mark.e2e` marker for `-m` selection? The
  directory gate already covers the requirement; marker only if tdw wants finer selection.

## Decisions (tdw, Oct 23)

- Branch name `chore/test-setup` is fine.
- No refactor onto the shared browser fixtures now.
- Leave `-x` in `pytest.ini`.
- `pytest-html` is out of scope.
- No e2e marker; the `RUN_E2E=1` switch is enough.
- In step 3, `run_e2e_tests.py` and `tests/e2e/run_all_tests.py` must set `RUN_E2E=1` in
  the environment of the pytest run they start, otherwise they would collect nothing.
