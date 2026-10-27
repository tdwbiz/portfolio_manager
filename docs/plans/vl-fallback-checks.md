# Plan: vl-fallback-checks (phase 3, step 13d fixes)

Branch: `fix/vl-fallback-checks` (from `dev`).

## Goal

Before the full monthly PDF re-import, fix the five problems the dry run of 29 real
PDFs found:

1. The VL fallback path stores sections unchecked. Run the same per-section checks
   the text path uses on VL sections; any failure gives a new verdict
   `vl_check_error` (whole file to `processed/errors/`, nothing stored, reasons name
   only fields, currencies and symbols). Remove the silent begin_total/end_total
   replacement heuristics; the begin_total-vs-balances warning becomes a check
   reason. When the VL model fails or returns invalid JSON there is **no** regex
   fallback: the file gets verdict `error` with a reason.
2. DIV REIN rows keep their printed debit/credit (no clearing). A reinvested
   dividend is two PDF rows: DIV REIN (debit, whole shares bought) + a DIVIDEND or
   DIST row (credit, full cash dividend); the second row may have an empty ACTIVITY
   column with DIST / CASH DIV only in the description and must still be a credit
   row. The cash gate needs both amounts.
3. Print the cash-gate result per currency section, also in `--dry-run`: opening
   cash, credits, debits, closing cash, difference, PASS/FAIL, plus the reasons of
   every failed check. Amounts may be printed; account numbers never.
4. Default `VL_MAX_TOKENS` 8192 → 16384; a truncated VL answer says `truncated` in
   the reason.
5. `import fitz` → `import pymupdf` everywhere (same API, PyMuPDF 1.28.2 installed)
   so the deprecation warning goes.

## What changes

- `imports/parsers_monthly_vl.py`
  - `parse_rbc_monthly_vl`: drop the `except → _parse_rbc_monthly_regex` fallback;
    VL failures re-raise (VLParseError / ValueError) so the command records verdict
    `error`. Delete `_parse_rbc_monthly_regex`.
  - `_coerce_response`: delete the whole post-processing block that replaces
    `end_total_market_value` / `begin_total_market_value` with the balance sum
    (the `10x` / `<1000` heuristics); delete the DIV REIN block that clears
    debit/credit; drop DIV REIN from `ZERO_CASH_TYPES` (constant is otherwise
    unused — delete it).
  - `normalize_activity_type`: empty `raw_type` must not partial-match everything
    (`'' in valid` is always True today). Add description fallback for empty type:
    `DIST` in description → DIST, else `DIV` → DIVIDEND (the credit row of a
    DIV REIN pair). Check-1 style RBC savings rows: make the
    `RBC INVESTMENT SAVINGS` → INTEREST_RECEIVED rule win over the `REINV` →
    DIV REIN rule (see Open questions Q1). The INTEREST_RECEIVED fix (clears
    price/debit/credit) stays: RBF2010/RBF2014 interest is one net-zero row on the
    PDF (AGENTS: price/debit/credit 0).
  - `import pymupdf` instead of `import fitz`.
- `core/utils/import_checks.py`
  - New `self_check_vl_sections(sections, *, today) -> list[str]`: per VL section —
    the text-path checks via `imports.parsers_pdf.self_check_sections` (lazy
    import; map VL 0-coerced `book_cost`/`quantity`/`market_price`/`market_value`
    of 0 to None first so "unreadable" still fires), the date checks via
    `statement_date_problems(statement_date, today=today, last_known=...)`, and a
    new begin-total reason: `begin_total_market_value inconsistent with balances`
    when the old heuristic conditions fire (begin_total < 1000 while the balance
    sum > 1000, or begin_total > 10× the balance sum). Reasons name only fields,
    currencies and symbols.
  - New `cash_gate_details(section) -> dict` returning `opening, credits, debits,
    closing, difference, ok, reasons`; `reconcile_section` delegates to it.
- `core/management/commands/manage_inbox_import.py`
  - VL fallback branch of `_process_monthly`: after `parse_rbc_monthly_vl` returns,
    run `self_check_vl_sections(parsed_list, today=...)`; any reason → verdict
    `vl_check_error`, nothing stored, file to `errors/`, reasons printed and put in
    the JSON log / FileImportLog (scrubbed). Add the verdict to the dry-run
    priority block and the non-dry-run return paths.
  - Print the gate line per currency section on both paths (text-merge and VL
    fallback), in normal and dry runs, e.g.
    `  [CAD] cash gate: opening X credits Y debits Z closing W diff D PASS`
    and print every failed check's reasons in dry run too.
- `imports/llm_client.py`: `finish_reason == 'length'` raises
  `VLParseError("VL model output truncated (finish_reason=length, max_tokens=N)")`
  (retried; final reason contains `truncated`) instead of only logging.
- `portfolio_manager/settings.py`: `VL_MAX_TOKENS` default `"16384"`; `.env.example`
  line to `# VL_MAX_TOKENS=16384`.
- `import pymupdf` also in `imports/parsers_annual_vl.py`, `imports/classify_pdf_vl.py`,
  `imports/parsers_pdf.py` (PyMuPDF fallback in `_extract_text_from_pdf`) and
  `tests/test_vl_parsers.py`.
- `docs/IMPORT_COMMANDS.md`: table of **all** monthly verdicts — `imported`,
  `duplicate`, `conflict`, `date_error`, `reconcile_error`, `vl_check_error` (new),
  `error` — with what each means and where the file goes; note the new
  `VL_MAX_TOKENS` default and the cash-gate print lines.
- Tests (synthetic fixtures only): `tests/test_vl_parsers.py` (rewrite
  `test_vl_failure_falls_back_to_regex` → VL failure raises; `RegexFallbackTest`
  deleted) and new cases in `imports/tests/test_monthly_txn_reconcile.py` /
  `imports/tests/test_manage_inbox_import.py` (see Tests).

No migrations.

## Out of scope

- The text parser itself (`imports/parsers_pdf.py` parsing code) — only its
  `self_check_sections` is reused, unchanged.
- Annual PDF path, CSV import paths, `watch_inbox` behaviour.
- Re-running the real 29-PDF import (tdw does that after this lands).
- `processed/`, `inbox/check-*.pdf` files: read-only, never moved or modified.
- The parked scripts in `scripts/`.

## Steps (one commit each)

1. **fitz → pymupdf** in `imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py`,
   `imports/classify_pdf_vl.py`, `imports/parsers_pdf.py`, `tests/test_vl_parsers.py`.
   Check: `rg -n "import fitz" imports tests` is empty;
   `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py --collect-only -q`.
2. **VL_MAX_TOKENS 16384 + truncated reason**: settings.py, `.env.example`,
   `imports/llm_client.py` (finish_reason='length' → VLParseError containing
   `truncated`).
   Check: `rg -n "VL_MAX_TOKENS" portfolio_manager/settings.py .env.example`;
   `pytest tests/test_vl_config.py -q`.
3. **DIV REIN amounts**: remove the DIV REIN clearing block and `ZERO_CASH_TYPES`;
   empty-activity description fallback in `normalize_activity_type`; savings-interest
   ordering fix (Q1).
   Check: `pytest imports/tests/test_monthly_txn_reconcile.py -q`.
4. **No regex fallback**: `parse_rbc_monthly_vl` re-raises on VL failure; delete
   `_parse_rbc_monthly_regex`; update `tests/test_vl_parsers.py`.
   Check: `rg -n "_parse_rbc_monthly_regex" imports tests` is empty;
   `pytest tests/test_vl_parsers.py -q`.
5. **VL section checks**: delete the begin/end replacement heuristics in
   `_coerce_response`; add `self_check_vl_sections` and `cash_gate_details` to
   `core/utils/import_checks.py` (unit-testable, no command wiring yet).
   Check: `pytest imports/tests/test_import_checks_flow.py -q`.
6. **Wire `vl_check_error`** into `_process_monthly` (fallback path + dry-run
   priority + JSON log), and the `error` verdict when the VL parser raises
   (no fallback).
   Check: `pytest imports/tests/test_manage_inbox_import.py -q`.
7. **Gate printing**: cash-gate line per currency section on both paths, also in
   `--dry-run`, plus every failed check's reasons in dry run.
   Check: `pytest imports/tests/test_monthly_txn_reconcile.py -q`.
8. **New synthetic tests** (see Tests).
   Check: `pytest tests/ imports/tests/ --collect-only -q`.
9. **Docs**: `docs/IMPORT_COMMANDS.md` verdict table + gate-print + VL_MAX_TOKENS note.
   Check: `git status` shows only the doc.
10. **Full test run (once, at the end)**:
    `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
    then `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.

Manual verification after step 7 (needs the VL model on 11435; Pi can run it,
~2.5 min total): a helper in `pm-logs/` modelled on audit 06
(`pm-logs/text_activity_check.py`) runs `parse_rbc_monthly_vl` +
`reconcile_section` on `inbox/check-1.pdf … check-4.pdf` and prints per-section
cash-gate PASS/FAIL only. check-1…4 must still reconcile through the VL
transaction path. If a savings-interest row now breaks the gate, stop and revisit
Q1 before step 10.

## Tests (added at step 8, run once at step 10)

Synthetic fixtures only (patched VL client / hand-built section dicts; no real PDFs,
no model calls):

- Two-row DIV REIN pair reconciles: `_coerce_response` keeps the DIV REIN debit and
  the DIVIDEND/DIST credit (second row with empty `activity_type`, "CASH DIV …"
  description → credit row), and `reconcile_section` on the section returns [].
- VL totals that do not add up (holdings + cash ≠ end_total, or unreadable
  book_cost) → command dry-run verdict `vl_check_error`, nothing stored.
- VL parser raises (or invalid JSON) → dry-run verdict `error` with a reason; a
  patched regex parser is never called (assert the fallback is gone).
- Dry run prints the gate line (`cash gate:` with PASS/FAIL) for every currency
  section and prints the reasons of failed checks.
- `self_check_vl_sections` fires the begin_total-vs-balances reason.

## Stop conditions

- Stop and ask if reusing `self_check_sections` for VL sections needs a change to
  the text-path checks (the text path must keep behaving exactly as today).
- Stop and ask if the check-1…4 verification shows the DIV REIN change breaking a
  section that used to reconcile — the INTEREST_RECEIVED correction decision (Q1)
  is revisited before anything else changes.
- Never touch `.env`, production settings, `processed/` or the real database.
- If the VL model on 11435 is unavailable, skip the manual verification and say so;
  do not fake it.

## Open questions

- **Q1: should the INTEREST_RECEIVED → DIV REIN correction for RBF2010/RBF2014 go?**
  The description-based `REINV` → DIV REIN rule currently fires before the
  `RBC INVESTMENT SAVINGS` → INTEREST_RECEIVED rule (audit 06: check-1/check-4 rows
  "text INTEREST_RECEIVED, VL DIV REIN"). The INTEREST_RECEIVED fix zeroes
  price/debit/credit because savings interest is one net-zero row on the PDF; once
  DIV REIN keeps amounts, a savings row mislabelled DIV REIN would keep hallucinated
  amounts and break the gate. **Recommendation: keep the correction but reorder
  `normalize_activity_type` so `RBC INVESTMENT SAVINGS` wins over `REINV`** (savings
  rows stay INTEREST_RECEIVED, amounts cleared); real equity DIV REIN rows keep
  their printed amounts. Verify on check-1…4 with the pm-logs helper.
- **Q2: what exactly is the begin_total check?** There is no begin total derivable
  from the same statement (the text path deliberately has no begin check).
  **Recommendation: keep the old heuristic thresholds as reasons only** —
  `begin_total < 1000 while balance sum > 1000` or `begin_total > 10× balance sum`
  → `begin_total_market_value inconsistent with balances`; never replace the value.
- **Q3: does `vl_check_error` also apply on the text path (where VL supplies only
  transactions)?** **Recommendation: no** — the text path stores text sections
  (already self-checked) and gates VL transactions with the cash gate; the VL
  totals/holdings checks only matter where VL sections are stored, i.e. the
  fallback path.
- **Q4: treat VL `book_cost == 0` as unreadable?** The VL coercion turns missing
  numbers into 0, so the text check (`is None`) never fires on VL sections.
  **Recommendation: yes** — map 0 to None for book_cost/quantity/market_price/
  market_value before delegating to `self_check_sections`; a real holding with
  zero book cost is rare and can be handled when it actually appears.
