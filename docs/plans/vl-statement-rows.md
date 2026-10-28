# Plan: vl-statement-rows (phase 3, step 13d — four re-import fixes)

## Goal

The full monthly PDF re-import rejected 83 files (59 `reconcile_error`, 8
`vl_check_error`). Four causes, each fixed here:

1. **Trades for future settlement.** RBC prints a "Trades For Future
   Settlement" table below Account Activity (check-5: a Nov 30 2020 statement
   lists `DEC.02 BOUGHT … 16 51.380 832.03-`). VL reads those rows as
   transactions, so the cash gate is off by exactly that amount. Tell VL to
   ignore the table, and in code drop transactions dated outside the section's
   statement window (a wrongly guessed year, e.g. "JAN. 02" on a Dec 31
   statement read as the same year, must also be dropped). Log counts only.
2. **Withholding tax on one row.** A DIVIDEND row can carry the gross amount
   as CREDIT and the withheld tax in DEBIT marked NRT on the same row
   (check-6: credit 65.48, debit 9.82 NRT; cash moves 55.66). Both amounts
   must survive to the cash gate (the gate already nets `credits − debits`).
3. **Options in holdings.** check-7 row
   `CALLNVDA 07/30/21 210 4- 0.010 225.04- $4.00-`: a trailing minus means
   negative for quantity, book cost and market value; option descriptions
   start with CALL/PUT and have no separate symbol token; the
   quantity × price check needs a contract multiplier of 100 for them
   (4 × 0.010 × 100 = 4.00 = |market value| — verified against the PDF text).
   Verified cause of the text self-check failure on check-7: the trailing
   minus makes `$4.00-` fail `_HOLDING_MV`, so the row is silently skipped as
   a continuation line and USD `holdings+cash != end_total_market_value` is
   off by exactly 4.00.
4. **US savings label.** The savings-interest rule matches only
   `RBC INVESTMENT SAVINGS`; US sections print `RBC U.S INVESTMENT SAVINGS`.
   Match `INVESTMENT SAVINGS` instead so US savings interest stays
   `INTEREST_RECEIVED`.

## What changes

- `imports/parsers_monthly_vl.py`
  - `EXTRACTION_PROMPT`: ignore the "Trades For Future Settlement" table;
    a DIVIDEND row may have both CREDIT (gross) and DEBIT (NRT withholding);
    trailing minus = negative (holdings quantity/book cost/market value and
    activity amounts); option rows (description starts with CALL/PUT) have no
    symbol — leave symbol empty.
  - `_coerce_response`: per section, drop transactions dated before
    `last_statement_date` or after `statement_date` (when `last_statement_date`
    is missing, use `statement_date − 100 days`, the same bound as
    `statement_date_problems`); log and carry the number of dropped rows per
    section — counts only, never dates or descriptions. Each section dict
    gains an `out_of_window_dropped` count key. Savings rules
    (`RBC INVESTMENT SAVINGS` here and in the REINV post-fix) match
    `INVESTMENT SAVINGS`. Verified: the DIVIDEND fix in `_coerce_response`
    only moves debit→credit when credit is 0, so a filled NRT pair already
    survives — step 2 only proves that with a test and adds the prompt line.
- `imports/parsers_pdf.py`
  - `_parse_asset_review`: `_HOLDING_QTY` / `_HOLDING_MONEY` /
    `_HOLDING_MV` accept a trailing `-`; `_decimal` negates such values;
    when the fixed five tokens do not match and the description is an option
    by its **full pattern** — `CALL` or `PUT`, optional space, underlying
    symbol, expiry date like `07/30/21`, strike (never a description that
    merely starts with CALL or PUT, so holdings like CALLAWAY or PUTNAM funds
    are never read as options) — accept the four-token layout
    `QTY PRICE BOOK_COST $MV` with `symbol=''` and the description as
    `security`.
  - `self_check_sections`: for a holding whose `security` matches the same
    full option pattern, the quantity × market_price check uses a multiplier
    of 100 and compares **signed** values (`quantity × price × 100 =
    market_value`, all negative for a short position) — not absolute values.
  - `_parse_account_activity`: **no change** (tdw Oct 28, extra C) — the
    text path does not store transactions.
- `core/utils/import_checks.py`: no change expected — the cash gate already
  sums credit and debit per row and `self_check_vl_sections` delegates the
  holdings checks to `self_check_sections`, so fixes 2 and 3 land there.
- `core/management/commands/manage_inbox_import.py`: next to the existing
  per-section cash-gate line, print the number of dropped out-of-window
  transactions per section (counts only — tdw Oct 28, extra D).
- Tests: `tests/test_vl_parsers.py` (VL coercion + prompt-adjacent behaviour,
  incl. a BOUGHT of RBC U.S INVESTMENT SAVINGS that keeps BOUGHT and its
  debit — tdw Oct 28, extra D),
  `imports/tests/test_parsers_pdf.py` (text holdings / self-check, incl.
  synthetic CALLAWAY/PUTNAM rows that must NOT be read as options — tdw
  Oct 28, extra A),
  `imports/tests/test_csv_parsers.py` or a VL test for `normalize_activity_type`.
- `docs/IMPORT_COMMANDS.md`: short note under PDF import — future-settlement
  trades are ignored, NRT withholding stays on the dividend row, short option
  rows are read as negatives.

No migrations. No model changes.

## Out of scope

- Anything that touches the database, the import commands' verdict logic, or
  `merge_and_reconcile` (13c-b stays as is).
- Cross-month checks (begin total vs previous month) — step 14.
- The annual PDF path, CSV importers, ledger, statements.
- Re-running the full 83-file re-import; verification is check-1…check-7 only.
- Changing what the text parser stores for option quantity (the printed
  contract count stays; the ×100 lives only in the check).

## Steps

Each step: implement, run the cheap check, run the touched test file, commit.

1. **Future-settlement rows (fix 1).** Prompt rule + date-window filter in
   `_coerce_response` + per-section drop count (log + `out_of_window_dropped`
   key) + the per-section on-screen print in `manage_inbox_import` next to
   the cash-gate line (counts only; tdw Oct 28, extra D). The text-path
   `_parse_account_activity` sub-step is skipped (tdw Oct 28, extra C).
   Test: synthetic VL section (statement Nov 30 2020, last Oct 31 2020) with
   one DEC. 02 BOUGHT row → dropped, cash gate passes; a Jan 02 row on a
   Dec 31 statement → dropped.
   Cheap check: `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py imports/tests/test_monthly_txn_reconcile.py -q`.
2. **NRT withholding (fix 2).** Prompt rule; confirm by test that
   `_coerce_response` keeps credit 65.48 + debit 9.82 on a DIVIDEND row
   (no zeroing/swapping) and that `cash_gate_details` nets to 55.66.
   Cheap check: `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py tests/test_import_checks.py -q`.
3. **Options in the text parser (fix 3a).** Trailing-minus regexes +
   negation + option four-token layout with empty symbol in
   `_parse_asset_review`, gated on the full option pattern (tdw Oct 28,
   extra A). Test: synthetic row
   `CALL NVDA 07/30/21 210 4- 0.010 225.04- $4.00-` parses to
   quantity −4, price 0.010, book cost −225.04, market value −4.00, symbol '';
   synthetic CALLAWAY/PUTNAM rows without an expiry+strike pattern are NOT
   read as options.
   Cheap check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_parsers_pdf.py -q`.
4. **Options in the checks and in VL (fix 3b).** ×100 multiplier in
   `self_check_sections` for full-pattern option holdings, comparing signed
   values (tdw Oct 28, extra B); prompt lines for negative trailing minus
   and empty option symbols. Test: `self_check_sections`
   passes on a section containing the check-7 option row (−4 × 0.010 × 100 =
   −4.00 = mv) and the holdings+cash sum includes −4.00.
   Cheap check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_parsers_pdf.py tests/test_import_checks.py -q`.
5. **US savings label (fix 4).** `normalize_activity_type` and the REINV
   post-fix match `INVESTMENT SAVINGS`. Tests: description
   "RBC U.S INVESTMENT SAVINGS REINV@..." → `INTEREST_RECEIVED`, debit/credit
   cleared, not `DIV REIN`; a BOUGHT whose description is
   "RBC U.S INVESTMENT SAVINGS" keeps BOUGHT and its debit (tdw Oct 28,
   extra D).
   Cheap check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_csv_parsers.py tests/test_vl_parsers.py -q`.
6. **Docs.** Update `docs/IMPORT_COMMANDS.md`.
   Cheap check: `rg -n "Future Settlement" docs/IMPORT_COMMANDS.md`, `git status`.
7. **Verification with the real VL model, then full tests.**
   First (tdw Oct 28, extra E): run `pm-logs/vl_cash_gate_check.py` extended
   to check-5, check-6 and check-7 yourself — check-5/6/7 must pass the cash
   gate, check-7 must also pass the holdings checks (`self_check_vl_sections`),
   and check-1…check-4 must still pass. Report pass/fail per section (verdict
   words only). Then the full tests:
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.

## Stop conditions

- A step needs a prompt change that makes the VL model regress on check-1…4
  (cash gate or holdings check newly fails) → stop, report, revise the plan.
- The ×100 option multiplier turns out wrong against a real PDF (the check
  fails with it) → stop and ask; do not widen the tolerance instead.
- The date-window filter drops legitimate in-window rows on any check file →
  stop and report the count.
- A trailing-minus row appears in a column other than quantity, book cost or
  market value → stop and list the column (no guessing).
- Any reason string or log line would carry amounts, dates, descriptions or
  account digits → fix before committing (privacy rule).
- If a step turns out wrong, stop; the plan is revised first, not edited.

## Open questions

1. *Lower bound when the VL section has no `last_statement_date`?*
   Recommended: use `statement_date − 100 days`, the same gap bound
   `statement_date_problems` already uses — it still catches a same-year
   January misread on a December statement.
2. *Should option quantity be stored as contracts (4) or shares (400)?*
   Recommended: keep the printed contract count; apply ×100 only inside the
   quantity × price self-check. Changing stored quantity would ripple into
   positions and ledger for no cash-gate benefit.
3. *NRT withholding: keep one row or split into a DIVIDEND + TAX row?*
   Recommended: keep one row (gross credit + NRT debit), as the task says —
   the cash gate nets them, and splitting invents a transaction type RBC does
   not print.
4. *Should `pm-logs/vl_cash_gate_check.py` be extended to check-5…7 in this
   plan?*
   Recommended: yes, in step 7 (it is a helper, prints verdict words only, and
   the task's manual verification needs those files).

## Decisions (tdw, Oct 28)

- **Q1–Q4: yes, as recommended** in each case.
- **Extra A — option recognition.** An option is recognised only by its full
  pattern: `CALL` or `PUT`, optional space, underlying symbol, expiry date
  like `07/30/21`, strike. Never by a description that merely starts with
  CALL or PUT — holdings like CALLAWAY or PUTNAM funds must never be read as
  options. Applies to both `_parse_asset_review` (four-token layout) and
  `self_check_sections` (×100 check). Synthetic tests with such names are
  added (step 3).
- **Extra B — signed option check.** The option self-check compares signed
  values: `quantity × price × 100 = market_value`, all negative for a short
  position. Not absolute values — a sign mismatch must fail the check.
- **Extra C — text-parser Account Activity: skip.** The `_parse_account_activity`
  change is dropped; the text path does not store transactions (VL always
  supplies them, step 13c-b). Step 1 no longer includes the one-off
  check-5 text-path read.
- **Extra D — print drop counts + US-savings BOUGHT test.** The number of
  dropped out-of-window rows is printed per section on screen in the import
  output, next to the cash-gate line (counts only). A test verifies that a
  BOUGHT whose description is "RBC U.S INVESTMENT SAVINGS" keeps BOUGHT and
  its debit after the `INVESTMENT SAVINGS` match is widened (step 5).
- **Extra E — verification run by the agent.** The check-1…check-7
  verification with the real VL model (extended `pm-logs/vl_cash_gate_check.py`)
  is run by Pi itself in step 7, before the full tests, and pass/fail is
  reported per section.
