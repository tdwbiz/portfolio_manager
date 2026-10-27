# Plan: pdf-text-holdings — Phase 3 step 12b, text-first monthly PDF parsing

Branch: `feature/pdf-text-holdings` (from `dev`). Builds on `docs/audit/05-text-parser-check.md`
(step 12a) and `docs/architecture.md` clean-up step 12 ("the text-parser check, then holdings,
cash and totals from the PDFs with VL as the fallback").

## Goal

Make the deterministic text parser (`imports/parsers_pdf.py`) the **first** path for RBC monthly
PDFs in `manage_inbox_import`, with the VL parser (`imports/parsers_monthly_vl.py`) as the
fallback. The text parser must fix the statement date (both layouts), parse holdings from the
Asset Review table (same field shape as VL), handle CAD + USD statements, and pass a per-file
self-check; a failed self-check sends that file to VL. The choice and reason are logged —
never values.

Hard rule everywhere (code, logs, tests, report, commits): no account numbers, names, amounts,
quantities or prices. Only counts, field names, symbols and MATCH/DIFFER/MISSING verdicts.

## What changes

- `imports/parsers_pdf.py`
  - Date fix: a shared date-extraction helper used by `parse_rbc_pdf` and `parse_rbc_pdf_multi`.
    In both layouts the month and day sit on a line that starts with other words and the year
    is on the **next** line; the current single-line and two-line matchers miss this. Also keep
    the "last statement" line exclusion.
  - Holdings: new `_parse_asset_review()` that reads the Asset Review table per currency
    section and returns rows with the **same keys as the VL parser's holdings**
    (`security`, `symbol`, `quantity`, `book_cost`, `market_price`, `market_value` — see
    `_coerce_response()` in `imports/parsers_monthly_vl.py`). Handle the old layout's joined
    words (e.g. `TotalValueof…`) on the total lines; the recent layout reads `Total Value of …`.
    Add a `holdings` key to the section dicts returned by both entry points.
  - Multi-currency: `parse_rbc_pdf_multi` already splits CAD/USD sections; make sure holdings,
    cash and the begin/end totals are taken **per section**, not from the full text.
  - Self-check: new `self_check_sections(sections) -> list[str]` returning empty when the parse
    is trustworthy, else reasons naming only fields/currencies (e.g. `"statement_date missing"`,
    `"USD holdings+cash != end_total_market_value"`). Per currency section:
    `sum(holdings.market_value) + cash == end_total_market_value` to the cent, and
    `statement_date` is not None. Cash = the closing cash value for that currency (same source
    the 12a check used).
- `core/management/commands/manage_inbox_import.py` — `_process_monthly` becomes text-first:
  run `parse_rbc_pdf_multi`, run `self_check_sections`; on any reason, fall back to
  `parse_rbc_monthly_vl`. Log (and add to the JSON log status) which parser was used and the
  self-check reasons — field names and currency codes only, never values. The VL classifier
  (`classify_pdf`) is unchanged.
- `imports/tests/test_parsers_pdf.py` — new synthetic-text tests (see Tests).
- `imports/tests/test_manage_inbox_import.py` — fallback test with monkeypatched parsers.
- `pm-logs/text_parser_check.py` — **untracked**, extended (stays untracked).
- `docs/audit/05-text-parser-check.md` — append the 12b comparison section (verdicts only).

No migrations: no model changes. If implementing a step seems to need one, stop (see Open
questions).

## Out of scope

- The PDF duplicate check, date sanity check and `BrokerStatement` periods (step 13).
- No import into any database; no `manage_inbox_import` run against real data; no watcher.
- No moving, renaming or deleting of PDFs in `inbox/`.
- The VL classifier and the annual-report path.
- `RbcMonthlyTransaction` quality — CSV Activity stays the master for transactions.

## Tests (synthetic text fixtures only, never real statement data)

All in `imports/tests/`, feeding raw text strings to `parse_rbc_pdf` / `parse_rbc_pdf_multi`
(both accept raw text as `file_path`). Fixtures mimic layout structure only, with fake
numbers. Cases:

1. Recent-layout date line (month/day mid-line, year on next line) → date found.
2. Old-layout date line (e.g. `MAR. 31` + next line `2020`) → date found.
3. Holdings from a synthetic Asset Review table, including a joined-word total line
   (`TotalValueof…`) → correct symbols/counts, VL-shaped keys.
4. CAD-only section → self-check passes.
5. CAD + USD sections, each with own cash, totals and holdings → two sections, self-check
   passes per currency.
6. Failing self-check (holdings + cash ≠ end total, or date missing) → reasons returned, and
   in `manage_inbox_import` (monkeypatched parsers) the file falls back to VL; log line names
   only fields/currencies.

## Steps (one commit each)

1. **Date fix.** Shared helper in `imports/parsers_pdf.py`, used by both entry points; both
   layouts. Add tests 1–2.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_parsers_pdf.py -q`
2. **Holdings parsing.** `_parse_asset_review()` + `holdings` key in both entry points'
   output, joined-word totals handled. Add test 3.
   Check: same pytest file, plus `pytest --collect-only -q imports/tests/`.
3. **Per-currency correctness.** Verify/fix that cash, totals and holdings in
   `parse_rbc_pdf_multi` come from each currency section. Add tests 4–5.
   Check: same pytest file.
4. **Self-check helper.** `self_check_sections()` with cent-exact Decimal comparison.
   Add test 6 (helper part).
   Check: same pytest file.
5. **manage_inbox_import text-first.** Wire text → self-check → VL fallback; log parser used +
   reasons (no values). Add test 6 (command part).
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_manage_inbox_import.py -q`
6. **Extend `pm-logs/text_parser_check.py`** (stays untracked, `git status` must stay clean of
   it): compare text parser vs VL on `inbox/check-1.pdf`…`inbox/check-4.pdf` (check-3 = old
   layout, check-4 = recent, same account, CAD + USD sections). Compare to the cent:
   statement_date, currency sections, cash per currency, begin/end total market value,
   holdings per symbol (quantity, market value). Console prints verdicts only. Run it
   (VL on port 11435, ~2 min for 4 files).
   Check: `git status --short` shows no `pm-logs/` entry; output contains no digits that look
   like values (spot-check verdict lines).
7. **Report.** Append a "Step 12b results" section to `docs/audit/05-text-parser-check.md`
   with the four-file comparison table (MATCH/DIFFER/MISSING, counts, symbols only) and the
   fallback verdict.
   Check: `git diff --stat` touches only the audit doc; skim the diff for any amounts.
8. **Full test pass** (see Tests) and `makemigrations --check`; fix fallout; commit any fixes.

## Tests (final, run once at the end)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test
```

## Stop conditions

- A step needs a migration → stop, add to Open questions, ask tdw.
- A real-PDF comparison (step 6) shows a DIFFER on date, cash, totals or holdings that the
  self-check would **not** have caught → stop and report (the self-check is incomplete), do
  not paper over it.
- Any output (console, log format, report, test fixture) would contain a real value, name or
  account number → stop and rework to verdicts only.
- `inbox/check-3.pdf` / `check-4.pdf` missing or unreadable → stop and ask (do not move or
  rename anything).
- A step turns out wrong → stop; the plan is revised first (per `docs/plans/README.md`).

## Open questions

1. *Should `manage_inbox_import` store the text parser's transactions, given VL's may be
   better?* Recommended: yes — store whatever the chosen parser returns.
   `RbcMonthlyTransaction` is documented as unreliable and CSV Activity is the master for
   transactions, so parity of shape matters more than transaction quality here.
2. *`book_cost` column in the old layout's Asset Review table.* Recommended: parse it when the
   column is recognisable, store 0 when not; the self-check does not use it, and a DIFFER on
   book_cost alone would not trigger fallback.
3. *Should the self-check also verify the begin total?* Recommended: no — only the end total
   is derivable from holdings + cash on the same statement; the begin total is a snapshot of
   the previous statement.
4. *Does `_scrubbed_json` / `_parsed_account_sections` in `manage_inbox_import` handle the
   text parser's payload (it has extra `income_summary`/`contribution_summary` keys)?*
   Recommended: verify in step 5; extra keys are ignored by the scrubbing path, and if they
   are not, adapt the scrubber — still no migration.
