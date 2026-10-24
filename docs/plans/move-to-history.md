# Plan: move to history (phase 0 step 3 — audit 3 batches 3, 4 and 7)

## Goal

Move the one-off history scripts, stale docs and finished phase specs out of the live tree into the history folders decided by audit 3 (batches 3, 4 and 7), in one branch (`chore/move-to-history`). Pure `git mv` + link path updates — **no behaviour changes**, nothing deleted, no content edits.

Source: `docs/audit/03-commands-scripts-docs.md` (batches 3, 4, 7 and the Decisions section) and `docs/architecture.md` §12 phase 0 step 3.

## What changes

Destinations decided by the audit:

- **Batch 3** (`pi/history-scripts`): the 10 history scripts → `docs/history/scripts/` (moved, not deleted — see Decisions).
- **Batch 4** (`pi/history-docs`): `docs/history/` is created; the 12 stale docs move to `docs/history/`, plus `SECURITY.md` and `USER_MANUAL.md` added by the Decisions section, plus `tests/e2e/PRODUCTION_READINESS_REPORT.md` flattened to `docs/history/PRODUCTION_READINESS_REPORT.md`.
- **Batch 7** (`pi/specs-archive`): PhaseA/C/D/E specs → `docs/history/specs/` (Decisions: all history lives under `docs/history/`). PhaseB stays (batch 6). The dividends-tracker question that gated this batch was answered (Q2: no tracker), so the batch is unblocked.

Reference check (one `rg -l` run over all moved file names together, excluding `docs/audit/`, `docs/plans/` and `docs/architecture.md`): **no live code, tests, settings or scripts import or run any moved file**, so nothing goes to open questions for that reason. The only referencing live docs are `docs/README.md`, `docs/DEVELOPMENT_GUIDE.md`, `docs/IMPLEMENTATION_PLAN.md` and `docs/COMPREHENSIVE_CODE_REVIEW.md` — links/prose only, handled in step 4.

### Batch 3 — scripts (10 files)

| From | To |
|---|---|
| scripts/audit_holdings_data.py | docs/history/scripts/audit_holdings_data.py |
| scripts/check_holdings_csv_keys.py | docs/history/scripts/check_holdings_csv_keys.py |
| scripts/count_csv_activity.py | docs/history/scripts/count_csv_activity.py |
| scripts/fix_bad_transactions.sql | docs/history/scripts/fix_bad_transactions.sql |
| scripts/fix_open_positions.py | docs/history/scripts/fix_open_positions.py |
| scripts/generate_holdings_reports.py | docs/history/scripts/generate_holdings_reports.py |
| scripts/generate_monthly_statements_report.py | docs/history/scripts/generate_monthly_statements_report.py |
| scripts/generate_rbc_monthly_csv.py | docs/history/scripts/generate_rbc_monthly_csv.py |
| scripts/generate_rbc_pdf_report.py | docs/history/scripts/generate_rbc_pdf_report.py |
| scripts/verify_holdings_import.py | docs/history/scripts/verify_holdings_import.py |

### Batch 4 — docs (13 files)

| From | To |
|---|---|
| docs/APP_STATUS.md | docs/history/APP_STATUS.md |
| docs/CODE_REVIEW.md | docs/history/CODE_REVIEW.md |
| docs/CODE_REVIEW_IMPLEMENTATION.md | docs/history/CODE_REVIEW_IMPLEMENTATION.md |
| docs/HARDCODING_REFACTORING.md | docs/history/HARDCODING_REFACTORING.md |
| docs/IMPLEMENTATION_STATUS_REPORT.md | docs/history/IMPLEMENTATION_STATUS_REPORT.md |
| docs/NEXT_FEATURES.md | docs/history/NEXT_FEATURES.md |
| docs/SECURITY.md | docs/history/SECURITY.md |
| docs/USER_MANUAL.md | docs/history/USER_MANUAL.md |
| docs/audit-investigation-sql.md | docs/history/audit-investigation-sql.md |
| docs/rbc_earliest_continuous_data.sql | docs/history/rbc_earliest_continuous_data.sql |
| docs/rbc_problematic_files.sql | docs/history/rbc_problematic_files.sql |
| docs/rbc_pdf_coverage_report.md | docs/history/rbc_pdf_coverage_report.md |
| tests/e2e/PRODUCTION_READINESS_REPORT.md | docs/history/PRODUCTION_READINESS_REPORT.md |

### Batch 7 — specs (52 files)

| From | To |
|---|---|
| specs/PhaseA/00_spec_template.md | docs/history/specs/PhaseA/00_spec_template.md |
| specs/PhaseA/01_bootstrap_portfolio_app.md | docs/history/specs/PhaseA/01_bootstrap_portfolio_app.md |
| specs/PhaseA/02_import_broker_statements.md | docs/history/specs/PhaseA/02_import_broker_statements.md |
| specs/PhaseA/03_rbc_cad_autoimport.md | docs/history/specs/PhaseA/03_rbc_cad_autoimport.md |
| specs/PhaseA/04_portfolio_dashboard.md | docs/history/specs/PhaseA/04_portfolio_dashboard.md |
| specs/PhaseA/05_accounts_dedup.md | docs/history/specs/PhaseA/05_accounts_dedup.md |
| specs/PhaseA/06_account_management_ui.md | docs/history/specs/PhaseA/06_account_management_ui.md |
| specs/PhaseA/07_portfolio_summary.md | docs/history/specs/PhaseA/07_portfolio_summary.md |
| specs/PhaseA/08_realised_pnl.md | docs/history/specs/PhaseA/08_realised_pnl.md |
| specs/PhaseA/09_fx_audit.md | docs/history/specs/PhaseA/09_fx_audit.md |
| specs/PhaseA/10_transaction_export.md | docs/history/specs/PhaseA/10_transaction_export.md |
| specs/PhaseA/11_performance_chart.md | docs/history/specs/PhaseA/11_performance_chart.md |
| specs/PhaseA/12_income_summary.md | docs/history/specs/PhaseA/12_income_summary.md |
| specs/PhaseA/13_import_history_ui.md | docs/history/specs/PhaseA/13_import_history_ui.md |
| specs/PhaseA/14_auth.md | docs/history/specs/PhaseA/14_auth.md |
| specs/PhaseA/15_account_categories.md | docs/history/specs/PhaseA/15_account_categories.md |
| specs/PhaseA/16_portfolio_summary_enhanced.md | docs/history/specs/PhaseA/16_portfolio_summary_enhanced.md |
| specs/PhaseA/17_performance_chart_enhanced.md | docs/history/specs/PhaseA/17_performance_chart_enhanced.md |
| specs/PhaseA/18_reporting_improvements.md | docs/history/specs/PhaseA/18_reporting_improvements.md |
| specs/PhaseA/19_performance_chart_fixes.md | docs/history/specs/PhaseA/19_performance_chart_fixes.md |
| specs/PhaseA/20_brand_and_ui_consistency.md | docs/history/specs/PhaseA/20_brand_and_ui_consistency.md |
| specs/PhaseA/21_rbc_pdf_statement_import.md | docs/history/specs/PhaseA/21_rbc_pdf_statement_import.md |
| specs/PhaseA/22_rbc_annual_report_import.md | docs/history/specs/PhaseA/22_rbc_annual_report_import.md |
| specs/PhaseA/feature_account_management_ui.md | docs/history/specs/PhaseA/feature_account_management_ui.md |
| specs/PhaseA/portfolio_manager Feature Roadmap.md | docs/history/specs/PhaseA/portfolio_manager Feature Roadmap.md |
| specs/PhaseA/recon_annual_vs_monthly.md | docs/history/specs/PhaseA/recon_annual_vs_monthly.md |
| specs/PhaseA/recon_csv_vs_pdf.md | docs/history/specs/PhaseA/recon_csv_vs_pdf.md |
| specs/PhaseC/C1-Spec-1_tx_hash_dedup.md | docs/history/specs/PhaseC/C1-Spec-1_tx_hash_dedup.md |
| specs/PhaseC/C1-Spec-2_login_security.md | docs/history/specs/PhaseC/C1-Spec-2_login_security.md |
| specs/PhaseC/C2-Spec-1_statement_date_and_llm_fallback.md | docs/history/specs/PhaseC/C2-Spec-1_statement_date_and_llm_fallback.md |
| specs/PhaseC/C2-Spec-2_upload_rate_limit_and_currency.md | docs/history/specs/PhaseC/C2-Spec-2_upload_rate_limit_and_currency.md |
| specs/PhaseC/C3-Spec-1_transaction_types_and_performance.md | docs/history/specs/PhaseC/C3-Spec-1_transaction_types_and_performance.md |
| specs/PhaseC/C3-Spec-2_indexes_input_validation.md | docs/history/specs/PhaseC/C3-Spec-2_indexes_input_validation.md |
| specs/PhaseC/C4-Spec-1_polish_and_operational.md | docs/history/specs/PhaseC/C4-Spec-1_polish_and_operational.md |
| specs/PhaseC/PhaseC_Implementation_Plan.md | docs/history/specs/PhaseC/PhaseC_Implementation_Plan.md |
| specs/PhaseC/Review from Gemma4 20260627.md | docs/history/specs/PhaseC/Review from Gemma4 20260627.md |
| specs/PhaseC/Review from Qwen36_35BQ6_ 20260627.md | docs/history/specs/PhaseC/Review from Qwen36_35BQ6_ 20260627.md |
| specs/PhaseD/00_spec_template.md | docs/history/specs/PhaseD/00_spec_template.md |
| specs/PhaseD/01_portfolio_summary_enhanced.md | docs/history/specs/PhaseD/01_portfolio_summary_enhanced.md |
| specs/PhaseD/02_performance_chart_enhanced.md | docs/history/specs/PhaseD/02_performance_chart_enhanced.md |
| specs/PhaseD/02b_performance_chart_fixes.md | docs/history/specs/PhaseD/02b_performance_chart_fixes.md |
| specs/PhaseD/03_dashboard_dollar_summary.md | docs/history/specs/PhaseD/03_dashboard_dollar_summary.md |
| specs/PhaseD/04_total_return.md | docs/history/specs/PhaseD/04_total_return.md |
| specs/PhaseD/05_branding_ui_consistency.md | docs/history/specs/PhaseD/05_branding_ui_consistency.md |
| specs/PhaseD/06_top_movers_widget.md | docs/history/specs/PhaseD/06_top_movers_widget.md |
| specs/PhaseD/07_upcoming_dividends_tracker.md | docs/history/specs/PhaseD/07_upcoming_dividends_tracker.md |
| specs/PhaseD/PHASED_IMPLEMENTATION_PLAN.md | docs/history/specs/PhaseD/PHASED_IMPLEMENTATION_PLAN.md |
| specs/PhaseD/README.md | docs/history/specs/PhaseD/README.md |
| specs/PhaseE/01_symbol_resolution_and_fixed_prices.md | docs/history/specs/PhaseE/01_symbol_resolution_and_fixed_prices.md |
| specs/PhaseE/02_transaction_reconciliation_and_statement_generation.md | docs/history/specs/PhaseE/02_transaction_reconciliation_and_statement_generation.md |
| specs/PhaseE/03_ACCOUNT_CURRENCY_REFACTOR_AND_REVERSE_STATEMENTS.md | docs/history/specs/PhaseE/03_ACCOUNT_CURRENCY_REFACTOR_AND_REVERSE_STATEMENTS.md |
| specs/PhaseE/PHASEE_IMPLEMENTATION_PLAN.md | docs/history/specs/PhaseE/PHASEE_IMPLEMENTATION_PLAN.md |

Note: `specs/PhaseD/07_upcoming_dividends_tracker.md` moves here; batch 8 (phase 1) deletes it at its new path.

Not part of this plan: the four Playwright test files already under `tests/e2e/`; anything from batches 1, 2, 5, 6; content edits to moved or referencing docs beyond link paths (that is batches 5–6).

## Migrations

None.

## Tests

None added (file moves only). Run the full suite once at the end (step 5).

## Steps

Each step: do the moves with `git mv`, run the check, commit.

1. **Batch 3** — `mkdir -p docs/history/scripts`, `git mv` the 10 files above, commit "Move 10 one-off scripts to docs/history/scripts (audit 3 batch 3)".
   Check: `git status --short | wc -l` shows 10 renames before commit, 0 after; the leftover-path check below finds no hits for these names in `scripts/`.
2. **Batch 4** — `mkdir -p docs/history`, `git mv` the 13 files above, commit "Move 13 stale docs to docs/history (audit 3 batch 4)".
   Check: same `git status --short | wc -l` pattern (13 renames, then 0); leftover-path check finds nothing.
3. **Batch 7** — `mkdir -p docs/history/specs`, `git mv` the 52 spec files above, commit "Archive PhaseA/C/D/E specs under docs/history/specs (audit 3 batch 7)".
   Check: `git status --short | wc -l` (52 renames, then 0); `ls specs/` shows only `PhaseB`.
4. **Link path updates only** (no other doc edits — those are batches 5/6):
   - `docs/README.md`: the `SECURITY.md`, `NEXT_FEATURES.md`, `USER_MANUAL.md` links → `history/…`.
   - `docs/DEVELOPMENT_GUIDE.md`: `specs/PhaseD/PHASED_IMPLEMENTATION_PLAN.md` → `docs/history/specs/PhaseD/PHASED_IMPLEMENTATION_PLAN.md`.
   - `docs/COMPREHENSIVE_CODE_REVIEW.md` and `docs/IMPLEMENTATION_PLAN.md` only mention moved file names in prose (no paths or links); leave them for batch 5.
   Check: the leftover-path `rg -l` below returns empty; commit "Update doc links to moved files".
5. **Full test run** (once, at the end):
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/`

   Both must pass unchanged; no commit unless something had to be fixed.

Leftover-path check for steps 1–4 (must return nothing outside `docs/audit/`, `docs/plans/`, `docs/architecture.md`):

```bash
printf '%s\n' audit_holdings_data.py check_holdings_csv_keys.py count_csv_activity.py fix_bad_transactions.sql fix_open_positions.py generate_holdings_reports.py generate_monthly_statements_report.py generate_rbc_monthly_csv.py generate_rbc_pdf_report.py verify_holdings_import.py APP_STATUS.md CODE_REVIEW.md CODE_REVIEW_IMPLEMENTATION.md HARDCODING_REFACTORING.md IMPLEMENTATION_STATUS_REPORT.md NEXT_FEATURES.md audit-investigation-sql.md rbc_earliest_continuous_data.sql rbc_problematic_files.sql rbc_pdf_coverage_report.md PRODUCTION_READINESS_REPORT.md SECURITY.md USER_MANUAL.md specs/PhaseA specs/PhaseC specs/PhaseD specs/PhaseE > /tmp/moved-names.txt
rg -l -F -f /tmp/moved-names.txt --glob '!docs/audit/**' --glob '!docs/plans/**' --glob '!docs/architecture.md' --glob '!docs/history/**'
```

## Open questions

None — all four were answered by tdw; see Decisions below.

## Decisions (tdw, Oct 24)

1. **Batch 7 destination**: `docs/history/specs/` — all history lives under `docs/history/`, not `specs/archive/`.
2. **Batch 3**: the scripts are **moved** to `docs/history/scripts/`, not deleted.
3. **`tests/e2e/PRODUCTION_READINESS_REPORT.md` is flattened** to `docs/history/PRODUCTION_READINESS_REPORT.md` (no `tests/e2e/` sub-path under `docs/history/`).
4. **`docs/audit-investigation-sql.md` goes to history** (`docs/history/audit-investigation-sql.md`), as batch 4 says.
