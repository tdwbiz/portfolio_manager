# Plan: vl-window-backdated (Phase 3 step 13d fix — back-dated rows)

Branch: `fix/vl-window-backdated` (from `dev`).

## Goal

RBC prints back-dated rows inside a monthly statement: the Nov 30 2018
statement (Date of Last Statement Oct 31 2018) lists two rows dated
OCT. 29 2018 "AS OF 10/29/18" — a RESIDEO spin-off share distribution from
HONEYWELL (20 shares, no cash) and a CASH-LIEU credit of 10.96 (cash in lieu
of a fractional share). The out-of-window filter in
`imports/parsers_monthly_vl._coerce_response` (plan `vl-statement-rows`,
fix 1) drops them because they are before `last_statement_date`, so the cash
gate fails by 10.96 (opening 698.97 + 10.96 = closing 709.93).

Fix: widen only the **lower bound** of the transaction date window:

- with `last_statement_date`: keep rows dated on or after
  `last_statement_date − 60 days` (was: `last_statement_date`);
- without `last_statement_date`: keep rows dated on or after
  `statement_date − 160 days` (was: 100 days).

The **upper bound stays**: rows after `statement_date` are still dropped
(Trades For Future Settlement). A same-year misread (Jan 02 read as the
statement's own year on a Dec 31 statement) is still far enough before the
new lower bound to be dropped (e.g. Dec 31 2020 statement, last Nov 30 →
window start Oct 1 → Jan 02 2020 dropped).

## Activity labels — is a label change needed?

**No label change in this fix.** Checked against `normalize_activity_type()`
(`imports/parsers_monthly_vl.py:93`):

- `DISTRIB` (spin-off, no cash): not a valid type and not in `type_map`, but
  the partial-match loop finds `'DIST' in 'DISTRIB'` → labelled `DIST`. The
  row carries quantity only, credit 0 / debit 0. The cash gate
  (`core/utils/import_checks.cash_gate_details`) is a naive sum over **all**
  transactions with no type excluded, so a 0/0 row is harmless.
- `CASH-LIEU` (cash in lieu, 10.96 credit): matches no rule and no
  partial match → labelled `OTHER` (with a warning log). `OTHER` rows are
  **kept** by `_coerce_response` (only OPENING/CLOSING BALANCE lines are
  skipped) and their credit is counted by the type-agnostic cash gate, so
  the 10.96 lands in the sum and the gate passes.

Amounts never move: both rows keep their printed DEBIT/CREDIT columns; the
fix only changes which rows survive the window filter. The manual
verification step records the labels the VL actually produced for check-11;
if they differ from the analysis above, stop and report rather than
re-labelling on the fly.

## What changes

- `imports/parsers_monthly_vl.py` — `_coerce_response` window block only:
  `window_start = last_statement_date - timedelta(days=60)`, else
  `statement_date - timedelta(days=160)`; update the adjacent comment (the
  old "same 100-day bound as statement_date_problems" note no longer holds —
  the window is deliberately wider than the 1–100-day statement-date gap
  check so back-dated rows survive).
- `tests/test_vl_parsers.py` — `OutOfWindowDropTest`: add the back-dated
  test, update the no-`last_statement_date` bound test (100 → 160 days).
- `pm-logs/vl_cash_gate_check.py` — extend the default `FILES` list to
  `check-11.pdf` (docstring says check-1 … check-10; check-11 is the
  spin-off statement).
- `docs/IMPORT_COMMANDS.md` — the "Monthly statement row rules" note
  (line ~59, "any transaction dated outside the statement's own window is
  dropped"): say the window starts 60 days before the Date of Last
  Statement (160 days before the statement date when none is printed) so
  back-dated rows (spin-off distributions, cash-in-lieu printed "AS OF"
  the previous month) are kept, and that the upper bound (rows after the
  statement date) is unchanged.

## Migrations

None.

## Out of scope

- No change to `normalize_activity_type()`, the extraction prompt, the cash
  gate, `statement_date_problems()`, the text parser, or the ledger.
- No amount moved between DEBIT and CREDIT.
- No change to `manage_inbox_import` printing (the `out_of_window_dropped`
  count already covers it).
- Gap check across months is step 14 (`vl-statement-rows`), not here.

## Tests (synthetic fixtures only, run once at the end)

In `tests/test_vl_parsers.py::OutOfWindowDropTest`:

1. **New** `test_backdated_rows_kept_and_gate_passes`: statement
   2018-11-30, last 2018-10-31, cash balance opening 698.97 / closing
   709.93; two rows dated 2018-10-29:
   - spin-off: `activity_type='DISTRIB'`, description
     'DISTRIB 20 SHS RESIDEO FROM HONEYWELL AS OF 10/29/18', quantity 20,
     credit 0, debit 0;
   - cash in lieu: `activity_type='CASH-LIEU'`, description
     'CASH-LIEU FOR FRACTIONAL SHARE AS OF 10/29/18', credit 10.96,
     debit 0.
   Assert: both rows kept, `out_of_window_dropped == 0`, and
   `cash_gate_details(section)['ok']` is True (698.97 + 10.96 = 709.93).
2. **Update** `test_no_last_statement_date_uses_100_day_bound` → 160-day
   bound: Dec 31 2020 − 160 = Jul 24 2020, so a Jul 23 row is dropped and
   a Jul 25 row is kept (rename the test accordingly).
3. **Unchanged, must still pass**: the DEC. 02 BOUGHT on a Nov 30 2020
   statement is dropped (upper bound); the Jan 02 same-year misread on a
   Dec 31 2020 statement is dropped (new lower bound Oct 1 still excludes
   it).

End of implementation, full suites once:

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

## Steps

1. **Window lower bound** in `imports/parsers_monthly_vl.py` (60 / 160
   days + comment).
   Cheap check: `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py -q`
   (the 100-day test fails here — expected; everything else passes).
   Commit: "widen VL transaction window for back-dated rows".
2. **Tests** in `tests/test_vl_parsers.py` (new back-dated test, 160-day
   bound test).
   Cheap check: `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py -q`
   — all green. Commit.
3. **`pm-logs/vl_cash_gate_check.py`**: `FILES` → `range(1, 12)`, docstring
   check-1 … check-11.
   Cheap check: `/opt/pm-venv/bin/python -m py_compile pm-logs/vl_cash_gate_check.py`.
   Commit.
4. **Docs**: update the window note in `docs/IMPORT_COMMANDS.md`
   ("Monthly statement row rules").
   Cheap check: `rg -n "60 days" docs/IMPORT_COMMANDS.md` shows the new
   note; `git status` shows only the docs file. Commit.
5. **Manual verification (VL model, ports 11435/11438 reachable)**:
   `/opt/pm-venv/bin/python pm-logs/vl_cash_gate_check.py` over
   `inbox/check-1.pdf … check-11.pdf`. Expected: **check-11** (the spin-off
   statement) — every currency section PASSes the cash gate and the section
   checks; check-1 … check-10 unchanged from the `vl-statement-rows` run.
   Note the activity labels the VL produced for the two back-dated rows in
   the commit message of this step (no code change unless a stop condition
   fires). No commit if nothing changes.
6. **Full tests** (commands above), then done.

## Stop conditions

- check-11 still fails the cash gate or a section check after the fix →
  stop, report the verdicts (safe summary only — never amounts or
  descriptions from the PDF).
- Any of check-1 … check-10 regresses versus the earlier
  `vl-statement-rows`/`vl-interest-columns` runs → stop.
- The VL model is unreachable → skip step 5, land steps 1–4 + tests, and
  say the manual verification is pending (tdw can run it).
- A step turns out wrong or a label behaves differently than analysed above
  → stop and report; the plan is revised first (README rule).

## Open questions (all decided — see Decisions below)

1. Should `CASH-LIEU` be mapped to `DIST` (or a new type) instead of
   `OTHER`? **Decided: no** (tdw accepted the recommendation). The cash gate is type-agnostic and keeps
   the credit; `OTHER` only warns in the ledger path, and the CSV Activity
   import (master for transactions) is untouched. Revisit only if step 5
   shows the VL emits a label that gets dropped.
2. Should the fallback stay aligned with the 1–100-day
   `statement_date_problems` gap? **Decided: no** (tdw accepted the
   recommendation) — the 160-day window
   bound is a different rule (row inclusion, not statement-date sanity)
   and must be wider to catch back-dated rows; the plan says so in the
   comment and in IMPORT_COMMANDS.md.
3. Is the lower bound inclusive ("on or after")? **Decided: yes** (tdw
   accepted the recommendation), as the task states; the 160-day test uses
   Jul 23 dropped / Jul 25 kept to avoid a brittle exact-boundary assertion.

## Decisions (tdw, Oct 29)

tdw accepts the recommended answers to all three open questions:

1. **No `CASH-LIEU` label change.** It stays labelled `OTHER` (with a
   warning log). The cash gate is type-agnostic and keeps the credit;
   `OTHER` only warns in the ledger path, and the CSV Activity import
   (master for transactions) is untouched. Revisit only if step 5 shows the
   VL emits a label that gets dropped.
2. **No alignment with `statement_date_problems`.** The 160-day fallback
   bound stays a separate rule (row inclusion, not statement-date sanity)
   and is deliberately wider than the 1–100-day gap check so back-dated
   rows survive; the comment in `_coerce_response` and
   `docs/IMPORT_COMMANDS.md` say so.
3. **Lower bound inclusive** ("on or after"). The 160-day test uses
   Jul 23 dropped / Jul 25 kept to avoid a brittle exact-boundary
   assertion.
