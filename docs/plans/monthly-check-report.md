# monthly_check — read-only CSV-vs-RBC report (phase 4, step 14a)

Clean-up order (`docs/architecture.md` §12): phase 4 step 14 is "the monthly check and the exceptions page". This plan is **step 14a: the command only** — read-only, no DB writes, no migrations, no exceptions page.

## Goal

`manage.py monthly_check [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE` compares the CSV `Transaction` rows with the stored RBC monthly statements (`RbcMonthly` + balances/holdings/PDF transactions) and reports, per account / statement / currency, a verdict:

- **FIRST** — the account's first stored statement (no previous one).
- **NO_CSV** — the period is outside CSV coverage: before the first or after the last `Transaction` settlement date over **all** accounts (global bounds; one set of exports covers every account).
- **PASS** — CASH, QUANTITY and CHAIN all hold.
- **FAIL** — one or more of CASH / QUANTITY / CHAIN failed (each failure listed with details).

Plus an IMPORT section listing monthly PDFs that errored and were never stored (the 3 parked files), separately from the 26 conflict second copies (kept out of the parked list, but shown with their log status and reason).

Everything is derived at read time; nothing is stored. The report contains only account names, dates, currencies, symbols, amounts and file names — never account digits.

## Definitions (fixed here, used by all code)

- **Statement grouping**: one "statement" = all `RbcMonthly` rows of one account with the same `statement_date` (usually a CAD and a USD row). If two rows share (statement_date, currency) (the `unique_together` also has `file_name`), use the newest `parsed_at`; every such duplicate is listed as its own line in the report summary — a finding, since it should not happen after the content-hash re-import.
- **Period**: `last_statement_date` (exclusive) … `statement_date` (inclusive) of the statement itself. **Previous statement** = the account's latest stored statement with `statement_date` < this one (a gap of several months is fine).
- **Transaction period membership**: `settlement_date` in the period; when `settlement_date` is NULL use `date` (trade date). Currency membership: `Transaction.currency`. Cash amount per transaction: `amount` (signed, native currency; `amount_cad` is its CAD conversion).
- **CASH (per currency)**: start = previous statement's `RbcMonthlyBalance` with `account_type='Cash'` and that `currency` `closing_balance` (0 if that currency had no Cash section) + Σ `amount` of the period's Transactions of that account+currency; must equal this statement's Cash `closing_balance` for that currency to the cent (±0.005).
- **HOLDINGS / QUANTITY (per account, all currencies pooled)**: start = previous statement's `RbcMonthlyHolding` quantities per symbol (both currencies), + Σ signed `Transaction.quantity` in the period; must equal this statement's `RbcMonthlyHolding` quantities per symbol at the stored precision (tolerance 0.0001). Pooling across currencies is deliberate: the DLR journal moves shares/cash between the CAD and USD sides. A symbol present on only one side is a difference; collect such CSV-only ↔ PDF-only **symbol pairs per account** (e.g. `9NKKHD9` vs `CALL NVDA 01/26 150 C`) in a dedicated report section so a symbol map can be designed later. Option-code matching is out of scope.
- **CHAIN (per currency)**: `begin_total_market_value` == previous statement's `end_total_market_value` for the same currency (±0.005) **and** `last_statement_date` == previous `statement_date`. When both hold, a month without a statement is not an exception.
- **CASH failure detail**: the difference, plus the period's Transactions lined up against that statement's `RbcMonthlyTransaction` rows (same currency; PDF net = `credit − debit`; greedy match on equal amount ±0.005 with ≤ 5 days date slack) — list the rows on one side only; plus whether replaying the cash sum with trade date (`date`) instead of settlement date would pass.
- **IMPORT section**: monthly PDFs whose **latest** `FileImportLog` row has `status='error'` and whose `content_hash` matches **no** `RbcMonthly` row → "parked" list with `error_message`. Files whose log reason marks them a **conflict** (second copy of a corrected statement) are listed separately (status + reason) and kept out of the parked list.

## What changes

- `core/utils/monthly_check.py` — new, pure read-only helpers: statement grouping/periods, coverage (NO_CSV), cash check, holdings check + symbol pairs, chain check, PDF-tx alignment helper, import-log scan, and a result dataclass per (account, statement_date, currency).
- `core/management/commands/monthly_check.py` — new command: `--account`, `--from`, `--to` (YYYY-MM, filter on `statement_date`), `--out`; orchestrates helpers, prints the summary (counts per verdict and per failure kind, per account), writes the full report to `--out`. Refuses to run without `--out`? No — `--out` is required by the task; make it required.
- `tests/test_monthly_check.py` — new pytest tests (synthetic data only, fake accounts `90000001`+).
- No models, no migrations, no views/templates, no writes of any kind (helpers only ever `.filter()`/`.aggregate()`; the single file written is `--out`).

## Out of scope

- The exceptions page (step 14b), any storage of verdicts, `write_ledger`, `CoreMonthlyStatement`, symbol mapping for option codes, fixing any data, touching `compare_monthly_pdf` / `reconcile` / parked scripts, account digits anywhere.

## Steps (one commit each)

1. **Skeleton**: `core/utils/monthly_check.py` dataclasses + `core/management/commands/monthly_check.py` with arguments and a "0 statements checked" run. Check: `/opt/pm-venv/bin/python manage.py monthly_check --help` prints the four options; `git status` shows only the two new files.
2. **Iteration + FIRST + NO_CSV + CHAIN**: group statements, order them, compute periods, coverage bounds and chain per currency. Check: `--account` on a name with no data prints the empty summary; `pytest --collect-only -q tests/` unchanged.
3. **CASH check + failure detail**: cash per currency, the PDF-tx alignment helper (5-day slack, greedy match) and the trade-date replay flag. Check: `python -c "import core.utils.monthly_check"` under `/opt/pm-venv/bin/python`; `--collect-only` unchanged.
4. **HOLDINGS check + symbol pairs**: pooled-quantity walk per account, per-symbol diff at 0.0001 tolerance, CSV-only/PDF-only pair collection. Check: import check as step 3; `rg -n "RbcMonthlyHolding" core/utils/monthly_check.py` shows the new query.
5. **IMPORT section**: latest-log-per-filename scan, parked vs conflict split, monthly-vs-annual classification by the kind in the safe file name (`<date>_monthly_<NAME>_<hash8>.pdf` / `_annual_`); anything else under other errors. Check: `rg -n "FileImportLog" core/utils/monthly_check.py`.
6. **Report rendering**: full text report to `--out` (sections: per-account verdicts with failure details, symbol-pair list, import section) and the on-screen summary (counts per verdict and per failure kind, per account). Check: run against the SQLite test DB with no data → file created, summary shows zeros.
7. **Tests** (all at once, synthetic data in `tests/test_monthly_check.py`): PASS month; CASH fail with exactly one missing row shown by the alignment helper; QUANTITY fail; gap month with a matching chain → PASS; chain break → FAIL(CHAIN); NO_CSV before the first and after the last settlement date; FIRST; a CAD+USD DLR journal that passes when holdings are pooled per account; a trade whose settlement date is after the statement date and which passes on settlement date (and would fail on trade date). Then run, once:
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`

## Stop conditions

- Anything would need a write, a migration or a new model → stop (this step is read-only).
- The conflict-vs-parked distinction cannot be made from `FileImportLog` fields on real data → stop and ask (do not guess from file names alone).
- `Transaction.amount` turns out not to be signed in native currency → stop, revisit open question 1.
- A step needs production settings or a mercury connection → stop (never).
- A required package is missing → stop and name it.
- If a step turns out wrong during implementation → stop and summarise; the plan is revised first (README rule).

## Open questions

All resolved by tdw on Oct 30 — see **Decisions** at the end of this file. The definitions and steps above already reflect the answers.

1. Cash sum field — `Transaction.amount` (signed, native currency).
2. NO_CSV coverage scope — global (first/last settlement date over all accounts), not per account.
3. Quantity tolerance — absolute difference ≤ 0.0001 counts as equal.
4. Multiple rows per (statement_date, currency) — newest `parsed_at` wins; every duplicate listed as its own line in the report summary (a finding).
5. Monthly vs annual in the IMPORT section — the kind in the safe file name; anything else under other errors.
6. Conflict identification — latest error log whose `error_message` contains "conflict" (case-insensitive).

## Decisions (tdw, Oct 30)

1. **Cash sum field** — yes: use `Transaction.amount` (signed, native currency), not `amount_cad`; `RbcMonthlyBalance.closing_balance` is native-currency.
2. **NO_CSV coverage scope** — no, not per account: global coverage, the first and last `settlement_date` over all `Transaction` rows, because one set of exports covers all accounts; per account a quiet account would wrongly be NO_CSV after its last transaction.
3. **Quantity tolerance** — yes: absolute difference ≤ 0.0001 counts as equal; anything larger is a difference.
4. **Multiple rows per (statement_date, currency)** — use the newest `parsed_at`, but list every such duplicate as its own line in the report summary (it should not happen after the content-hash re-import, so it is a finding).
5. **Monthly vs annual in the IMPORT section** — use the kind in the safe file name (`<date>_monthly_<NAME>_<hash8>.pdf` or `_annual_`); anything else under other errors.
6. **Conflict identification** — yes: a latest error log whose `error_message` contains "conflict" (case-insensitive) is a conflict second copy; the report lists both groups with status + reason so tdw can confirm the 3 + 26 split on real data.
