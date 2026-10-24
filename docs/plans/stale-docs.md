# Plan: stale-docs (phase 0 step 4 — audit 3 batches 5 and 6)

Branch: `chore/stale-docs` (from `dev`). Docs/config only — no behaviour changes, no code moves.

## Goal

Finish the two last audit-3 doc batches:

- **Batch 5 (stale docs):** delete the duplicate deployment guide and the mkdocs site (audit 3 Q4), fix stale prose in `docs/DEVELOPMENT_GUIDE.md`, `docs/README.md`, `docs/COMPREHENSIVE_CODE_REVIEW.md` and `docs/IMPLEMENTATION_PLAN.md` (references to files that step 3 moved to `docs/history/`), fix `.zed/settings.json` `extraPaths` for Linux, and add `pm-logs/` to `.gitignore`.
- **Batch 6 (parked labels):** one-line PARKED banners on the accounting docs, the reconciliation SQL/docs and the AGENTS.md "Reconciliation scripts" row; move `specs/PhaseB/` to `specs/parked/PhaseB/`; label the two rebuild commands as parked (audit 3 Q5). Per audit 3: nothing parked is deleted — only labelled. `docs/architecture.md` §11/§12 (step 4) is the authority where the audit disagrees.

## What changes (files)

- `.gitignore` — add `pm-logs/` (runtime log dir; nothing in the repo references it yet).
- Deleted: `docs/DEPLOYMENT (2).md` (duplicate of `docs/deployment.md`), `docs/mkdocs.yml`, `docs/index.md`, `docs/api/` (3 files) — audit 3 Q4 "mkdocs site: delete".
- `docs/ACCOUNTING_IMPLEMENTATION_STATUS.md`, `ACCOUNTING_OPTION_C_UPDATE.md`, `ACCOUNTING_QUICK_START.md`, `ACCOUNTING_SYSTEM_DESIGN.md`, `ACCOUNTING_SYSTEM_SUMMARY.md`, `ACCOUNTING_SYSTEM_VIEWS_DESIGN.md` — one-line `> **PARKED** (ledger/accounting layer — see docs/architecture.md §11)` banner under the title.
- `docs/rbc_balance_reconciliation.sql`, `docs/rbc_csv_vs_pdf_comparison.sql` — `-- PARKED (reconciliation — see docs/architecture.md §11)` first line; `docs/rbc-data-load-process.md` — banner "superseded by docs/IMPORT_COMMANDS.md".
- `AGENTS.md` — mark the "Reconciliation scripts" row (line ~158) as parked.
- `specs/PhaseB/` → `specs/parked/PhaseB/` (`git mv`, 17 files) + new `specs/README.md` (one paragraph: only Phase B remains, parked; archived specs in `docs/history/specs/`).
- `core/management/commands/rebuild_position_history.py`, `rebuild_positions_from_transactions.py` — one-line "PARKED (see docs/architecture.md §11)" at the top of each docstring (docstring only, no logic change).
- `docs/DEVELOPMENT_GUIDE.md` line 123 — stop calling `docs/history/specs/PhaseD/PHASED_IMPLEMENTATION_PLAN.md` "the current implementation plan"; point at `docs/architecture.md` + `docs/plans/` instead.
- `docs/README.md` — Specs Directory row (line 124): link `../specs/parked/PhaseB/` (specs/ is at repo root, not under docs/), drop "Phase A" (only Phase B remains, parked); fix the tree line (~178) the same way; fix `[Deployment Guide](DEPLOYMENT.md)` → `deployment.md` (file is lowercase).
- `docs/COMPREHENSIVE_CODE_REVIEW.md` lines 394–398 — point SECURITY.md, CODE_REVIEW.md, NEXT_FEATURES.md at `history/…`, note `specs/` now holds only parked Phase B.
- `docs/IMPLEMENTATION_PLAN.md` — rg found only one candidate mention (line 3, `COMPREHENSIVE_CODE_REVIEW.md`, which did **not** move) plus MkDocs mentions (lines ~310–346) that go stale when the mkdocs files are deleted; add a short "historical record" note at the top and an "(since removed)" note on the MkDocs rows. No other moved-file prose exists (verified by rg).
- `.zed/settings.json` — `extraPaths`: `C:/AI-DevTeam/ai-env/Lib/site-packages` → `/opt/pm-venv/lib/python3.14/site-packages`.

## Out of scope

- No rewrite of `DEVELOPMENT_GUIDE.md` for saturn/Linux (only the stale line 123; see open questions).
- No moving `COMPREHENSIVE_CODE_REVIEW.md` / `IMPLEMENTATION_PLAN.md` to `docs/history/`.
- Nothing deleted that is merely parked (batch 6 = labels only); no code or table moves.
- Don't touch `docs/history/`, `docs/audit/`, other plan files, `docs/reconciliation/`, `.env`.
- No migrations, no model/view/template changes.

## Steps (one commit each)

1. **`.gitignore`**: add `pm-logs/`. Check: `git check-ignore -q pm-logs/x && echo ok` prints `ok`; `git status` clean otherwise.
2. **Deletions**: `git rm "docs/DEPLOYMENT (2).md" docs/mkdocs.yml docs/index.md docs/api`. Check: `ls docs/api docs/mkdocs.yml` fails; `git status` shows 5 deletions staged.
3. **Parked banners — docs**: add the one-line banner to the 6 `docs/ACCOUNTING_*.md`, the 2 `docs/*.sql`, and the superseded note to `docs/rbc-data-load-process.md`. Check: `rg -l "PARKED" docs/ACCOUNTING_* | wc -l` = 6; `head -1 docs/rbc_balance_reconciliation.sql` shows the banner.
4. **Parked labels — AGENTS.md + rebuild commands**: mark the AGENTS.md "Reconciliation scripts" row parked; add the PARKED line to the two rebuild-command docstrings. Check: `rg -n "PARKED" core/management/commands/rebuild_position*.py` = 2 hits; `rg -n "Reconciliation scripts" AGENTS.md` shows "parked".
5. **specs/PhaseB → specs/parked/PhaseB**: `git mv specs/PhaseB specs/parked/PhaseB`, add `specs/README.md`. Check: `ls specs/parked/PhaseB | wc -l` = 17; `git status` shows renames only.
6. **DEVELOPMENT_GUIDE.md**: fix line 123. Check: `rg -n "current implementation plan" docs/DEVELOPMENT_GUIDE.md` = no hits.
7. **docs/README.md**: fix Specs row, tree line and `deployment.md` case. Check: `rg -n "Specs Directory" docs/README.md` shows `../specs/parked/PhaseB/`; `rg -n "\(DEPLOYMENT.md\)" docs/README.md` = no hits.
8. **COMPREHENSIVE_CODE_REVIEW.md + IMPLEMENTATION_PLAN.md**: apply the prose fixes listed above (re-run the moved-file rg first to catch anything missed). Check: `rg -n "SECURITY.md|CODE_REVIEW.md|NEXT_FEATURES.md" docs/COMPREHENSIVE_CODE_REVIEW.md` shows only `history/` paths.
9. **`.zed/settings.json`**: point `extraPaths` at `/opt/pm-venv/lib/python3.14/site-packages`. Check: `rg -n extraPaths .zed/settings.json` shows the Linux path.
10. **Tests** (once, at the end):
    - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
    - `/opt/pm-venv/bin/python -m pytest tests/` (plus `--collect-only -q` sanity if desired)
    - `git status` must show a clean tree after the test run.

## Stop conditions

- A rg/git-grep check shows a deleted or moved file referenced from **live code, templates or settings** (not docs/audits/plans) → stop and ask.
- `specs/PhaseB` turns out to be referenced by a path in code/tests (only docs/audit/plans hits were found) → stop and ask.
- Either test suite fails after step 10 with a change traceable to this branch → stop, do not fix by touching code outside this plan.
- Anything here turns out wrong mid-step → stop and report; the plan is revised first (plans/README rule).

## Open questions

1. `DEVELOPMENT_GUIDE.md` is Windows/PowerShell-era (audit 3 also flagged Windows paths; port 11440 is no longer mentioned there). Fix only the stale line 123 now, or rewrite the guide for saturn?
   **Recommended:** minimal fix now (as the task scopes it); a saturn rewrite is tdw's call, separate branch.
2. Keep `COMPREHENSIVE_CODE_REVIEW.md` and `IMPLEMENTATION_PLAN.md` in `docs/` with fixed references, or move them to `docs/history/`?
   **Recommended:** keep and fix (what the task asks); they are dated records and tdw can move them later.
3. `specs/PhaseB`: move to `specs/parked/PhaseB/` or just note it in a README?
   **Recommended:** move (audit 3's first option) and update the `docs/README.md` link in step 7.
4. `pm-logs/` is not referenced anywhere in the repo — presumably a runtime log dir on saturn.
   **Recommended:** add it to `.gitignore` anyway (harmless, explicitly requested).
