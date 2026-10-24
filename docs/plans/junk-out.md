# Plan: junk out (phase 0 step 2 — audit 3 batch 2, `remove-junk-artifacts`)

Source: `docs/audit/03-commands-scripts-docs.md` (batch 2 + Decisions), `docs/architecture.md` §12 phase 0.

## Goal

Delete the committed junk artifacts (test-run reports, the old CrewAI log, a scratch script, the three stray debug files from Q10) so they stop being part of the repo, and gitignore the ones that can be regenerated at run time. **No behaviour changes** — nothing in live code, tests or settings uses any of these files (see table). Out of scope: moving files to `docs/history/`, stale-doc edits, anything in `docs/audit/`, `docs/plans/` or `docs/architecture.md`.

## Delete list

References were found with `git grep -l <name>` over tracked files, excluding `docs/audit/`, `docs/plans/` and `docs/architecture.md`.

| File | Tracked? | References it (file names only) | Notes |
|---|---|---|---|
| `e2e/report.html` | yes | `tests/e2e/PRODUCTION_READINESS_REPORT.md` (doc only; that doc moves to history in batch 4) | pytest-html artifact; the whole root `e2e/` dir contains only this file |
| `tests/e2e/report.html` | yes | `tests/e2e/PRODUCTION_READINESS_REPORT.md` (doc only) | **regenerated at run time**: `tests/e2e/run_all_tests.py` runs pytest with `--html=report.html` after `os.chdir(e2e_dir)` |
| `tests/e2e/tests/e2e/report.html` (nested `tests/e2e/tests/` folder) | yes | none (only itself) | same artifact produced once from the wrong cwd; the nested folder contains only this file |
| `login_test.html` | yes | none | page snapshot |
| `events.jsonl` | yes | none | old CrewAI session log; no code references it and `crewai` is not in `requirements.txt`, so nothing in this repo writes it any more — gitignore entry added anyway as cheap insurance |
| `calc_march.py` | yes | none | scratch script (hard-coded March figures) |
| `imports/debug_parser.py` | yes | none | stray debug file (Q10 decision: delete) |
| `imports/debug_test.py` | yes | none | stray debug file (Q10); it imports `imports.parsers_monthly_vl`, but nothing imports it; name does not match pytest's `test_*.py` collection pattern |
| `imports/test_regex.py` | yes | none | stray debug file (Q10). The `test_regex` hits in `tests/test_vl_parsers.py` are test-method names (`test_regex_extract_…`), not references to this file; file has no `def test…` functions |
| `db_test.sqlite3` | **no** (already in `.gitignore`) | `.gitignore`, `portfolio_manager/settings_test.py` | **kept** — it is the test database named by `settings_test.py` and is recreated by every test run (see Decisions) |

`tests/e2e/PRODUCTION_READINESS_REPORT.md` mentions the report files in prose only; the mentions stay as they are (see Decisions).

## `.gitignore` additions (in the plan)

Exact file entries only — no general `report.html` pattern, it could hide a real template:

- `events.jsonl` — nothing writes it today, but guard against it ever coming back as a run-time log.
- `e2e/report.html`
- `tests/e2e/report.html` — written by `tests/e2e/run_all_tests.py` on every e2e run.
- `tests/e2e/tests/e2e/report.html`
- `tests/test_report.html` — written by `run_e2e_tests.py` / the pytest-html commands in `tests/README.md`.

## Steps

1. **`.gitignore`**: add `events.jsonl`, `e2e/report.html`, `tests/e2e/report.html`, `tests/e2e/tests/e2e/report.html` and `tests/test_report.html` under the run-outputs block.
   Check: `git check-ignore -v events.jsonl e2e/report.html tests/e2e/report.html tests/e2e/tests/e2e/report.html tests/test_report.html` prints all five. Commit.
2. **Delete the report.html artifacts**: `git rm e2e/report.html tests/e2e/report.html tests/e2e/tests/e2e/report.html` and remove the now-empty local dirs `e2e/` and `tests/e2e/tests/`.
   Check: `git ls-files | grep -c report.html` → 0; `ls e2e tests/e2e/tests` → no such directory. Commit.
3. **Delete root junk**: `git rm events.jsonl login_test.html calc_march.py`.
   Check: `git ls-files | grep -E 'events\.jsonl|login_test|calc_march'` → empty. Commit.
4. **Delete the debug files**: `git rm imports/debug_parser.py imports/debug_test.py imports/test_regex.py`.
   Check: `/opt/pm-venv/bin/python -c "import imports.parsers_monthly_vl"` succeeds (the module `debug_test.py` used). Commit.
5. **Full test run (once, at the end)**:
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ --ignore=tests/e2e --ignore=tests/test_api_endpoints.py --ignore=tests/test_smoke_pytest.py --ignore=tests/test_e2e_user_journey.py --ignore=tests/test_user_workflows.py`
   Both must pass; no commit needed unless a fix was required.

## Open questions

None — all three were answered by tdw (see Decisions).

## Decisions (tdw, Oct 23)

1. **`.gitignore` entries**: use exactly these file entries — `e2e/report.html`, `tests/e2e/report.html`, `tests/e2e/tests/e2e/report.html`, `tests/test_report.html` (plus `events.jsonl`). No general `report.html` pattern and no folder entry for `tests/e2e/tests/`: a bare pattern could hide a real template.
2. **`db_test.sqlite3`: leave it.** It is the test database named by `settings_test.py`; nothing to delete from git.
3. **`tests/e2e/PRODUCTION_READINESS_REPORT.md`**: the prose mentions of the deleted `report.html` files stay as they are; the doc moves to `docs/history/` in batch 4.
