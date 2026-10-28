# Plan: vl-interest-columns — Phase 3 step 13d, last two causes (amount movers + interest)

Branch: `fix/vl-interest-columns` (already checked out). One commit per step.

## Goal

Stop the VL monthly-PDF parser from moving amounts between the DEBIT and
CREDIT columns, and scope the interest fix to the rows it was written for.
Two of the 18 remaining `reconcile_error` causes:

1. **Amount movers.** `_coerce_response` rewrites amounts into the column the
   label "should" use (e.g. a `WITHDRAWAL` credit moved to debit). The
   `DEREGISTRATION FROM ACCOUNT` rows are money **into** this account (a
   credit), VL labels them WITHDRAWAL, the rule moves the credit to debit and
   the cash gate then fails by exactly 2 × the amount (e.g. 9000.00 =
   2 × 4500.00). Fix: **activity labels may still be corrected, amounts are
   never moved or cleared** — the cash gate (naive `credits − debits` vs the
   printed opening/closing cash) catches a column VL reads wrongly.

2. **Interest.** The `INTEREST_RECEIVED` fix clears debit, credit and price
   for *every* interest row. That is right only for the RBC savings funds
   (description contains `INVESTMENT SAVINGS`, reinvested at the fixed 10.00
   rate, net cash 0). Interest on the cash balance (description like
   `INT FR 07/22 THRU08/21 @20 3/4% BAL 306- AVBAL 5,519`) must keep its
   printed amount: a **debit** is `INTEREST_PAID` (charged on a negative
   balance, e.g. 97.28), a **credit** is `INTEREST_RECEIVED`.

## Rules to remove (all in `imports/parsers_monthly_vl.py`, `_coerce_response`)

Every rule that moves or swaps an amount between debit and credit, or clears
one of them:

| # | Line (≈) | Rule | What it does |
|---|---|---|---|
| 1 | 646 | Fix BOUGHT | moves credit→debit when debit==0; clears credit when both set |
| 2 | 664 | Fix SOLD | moves debit→credit when credit==0; clears debit when both set ("opening balance leak") |
| 3 | 685 | Fix DIST | moves debit→credit when credit==0 (the qty→price rate fix **stays** — it is not a debit/credit move) |
| 4 | 708 | Fix INTEREST_RECEIVED | clears debit and credit unconditionally, clears price==10 — **scoped, not deleted** (see below) |
| 5 | 737 | Fix TFR OUT | moves credit→debit when debit==0 |
| 6 | 747 | Fix DIVIDEND | moves debit→credit when credit==0 |
| 7 | 757 | Fix WITHDRAWAL | moves credit→debit when debit==0 — the DEREGISTRATION case |

Kept unchanged: the DIST qty→price rate fix, the cash-only `quantity=0`
clearing, the zero-amount validation skips, the date-window drop, the
label-only corrections (`normalize_activity_type`, BOUGHT→DIST,
REINV→DIV REIN, savings rule).

## What replaces them

- **Interest, scoped** (replaces rule 4): if `activity == 'INTEREST_RECEIVED'`
  and `'INVESTMENT SAVINGS' in description.upper()` → clear price (==10),
  debit and credit as before (net-zero reinvested shares). Otherwise the row
  keeps its printed amounts; if `debit > 0 and credit == 0` the label becomes
  `INTEREST_PAID` (interest charged on a negative cash balance). A credit (or
  both columns, e.g. gross + charge) stays `INTEREST_RECEIVED` with the amounts
  as printed — the gate nets them, same policy as DIVIDEND NRT rows.
  Add `INTEREST_PAID` to `CASH_OUT_TYPES` so the zero-amount validation covers
  it. `INTEREST_PAID` is already a valid `RBC_ACTIVITY_TYPES` / model choice.
- **DEREGISTRATION FROM ACCOUNT → `TRANSFER`** (label only, amount untouched).
  Why TRANSFER and not DEPOSIT: the money comes from another RBC account that
  was deregistered — an inter-account transfer in, the same family as
  `TFR IN` which `normalize_activity_type` already maps to TRANSFER. DEPOSIT
  is a fresh deposit into the account from outside; CONTRIB a contribution.
  Implement in `_coerce_response` after the amounts are parsed (so it can
  require `credit > 0 and debit == 0`), before the validation skips.
- **Prompt** (`EXTRACTION_PROMPT`): add a global rule "keep every amount in
  the column it is printed in — never move an amount between debit and
  credit"; add `INTEREST_PAID` to the allowed values with a short rule
  (cash-balance interest charged on a negative balance, DEBIT = amount;
  interest received keeps CREDIT); note that a DEREGISTRATION FROM ACCOUNT
  row is money in (CREDIT) → TRANSFER.

## What changes (files)

- `imports/parsers_monthly_vl.py` — remove rules 1,2,3(debit part),5,6,7;
  scope rule 4; add INTEREST_PAID derivation + `CASH_OUT_TYPES` entry; add
  DEREGISTRATION→TRANSFER label fix; prompt updates.
- `tests/test_vl_parsers.py` — new synthetic-fixture tests (below).
- `pm-logs/vl_cash_gate_check.py` — check files 1–10 instead of 1–7.
- `docs/IMPORT_COMMANDS.md` — document the new policy (amounts never moved,
  gate catches misreads; INT FR semantics; DEREGISTRATION = TRANSFER).

No models, no migrations.

## Out of scope

- The cash-gate / `self_check_vl_sections` code (`core/utils/import_checks.py`)
  — unchanged.
- `normalize_activity_type` label rules other than as stated (the
  `'INTEREST' in desc → INTEREST_RECEIVED` fallback stays; the debit-based
  INTEREST_PAID derivation in `_coerce_response` fixes the direction).
- The text (regex) parser, CSV importers, ledger, anything in `scripts/`.
- The other 16 reconcile_error causes (fixed in earlier 13d work).
- Re-running the full inbox re-import (tdw).

## Tests (synthetic fixtures only, in `tests/test_vl_parsers.py`)

1. **Deregistration credit**: raw row labelled WITHDRAWAL, description
   "DEREGISTRATION FROM ACCOUNT ...", credit 4500.00, debit 0 → coerced to
   `TRANSFER`, credit still 4500.00, debit 0; a section with opening cash X
   and closing X+4500 passes `cash_gate_details`.
2. **INT FR debit**: description "INT FR 07/22 THRU08/21 @20 3/4% BAL 306-
   AVBAL 5,519", raw type INTEREST, debit 97.28, credit 0, no other rows,
   opening 306.67, closing 209.39 → `INTEREST_PAID`, debit stays 97.28, cash
   gate passes.
3. **INT FR credit** (small extra): same description shape with credit 12.34 →
   stays `INTEREST_RECEIVED`, credit kept, gate passes.
4. **Savings fund row**: "RBC INVESTMENT SAVINGS ... REINV@10.00" with
   qty/price 10/debit/credit set → still `INTEREST_RECEIVED` with debit,
   credit and price cleared (existing `test_us_savings_coercion_clears_amounts_not_div_rein`
   covers the US label; keep it).
5. **No amount moving**: a BOUGHT row with the amount in credit and a SOLD row
   with the amount in debit keep their printed columns untouched.

## Steps (one commit each; cheap check after each)

1. **Remove the amount movers.** Delete rules 1, 2, 3 (the debit→credit part
   only), 5, 6, 7 from `_coerce_response`; delete rule 4 entirely (re-added,
   scoped, in step 2).
   Check: `rg -n "moved credit->debit|moved debit->credit" imports/parsers_monthly_vl.py`
   is empty; `/opt/pm-venv/bin/python -m pytest tests/test_vl_parsers.py --collect-only -q`.
2. **Scoped interest rule + INTEREST_PAID.** Re-add the INTEREST_RECEIVED
   clearing inside an `INVESTMENT SAVINGS` branch; add the non-savings
   branch (keep amounts; debit-only → INTEREST_PAID); add INTEREST_PAID to
   `CASH_OUT_TYPES`.
   Check: `rg -n "INVESTMENT SAVINGS" imports/parsers_monthly_vl.py`;
   `pytest tests/test_vl_parsers.py --collect-only -q`.
3. **DEREGISTRATION → TRANSFER** label fix in `_coerce_response`.
   Check: `rg -n "DEREGISTRATION" imports/parsers_monthly_vl.py`.
4. **Prompt updates** (amounts-never-moved line, INTEREST_PAID value + rule,
   deregistration note).
   Check: `rg -n "INTEREST_PAID" imports/parsers_monthly_vl.py`.
5. **New tests** (list above) in `tests/test_vl_parsers.py`.
   Check: `pytest tests/test_vl_parsers.py --collect-only -q`.
6. **Manual VL verification.** Extend `pm-logs/vl_cash_gate_check.py` to
   `check-1 … check-10` and run it with the VL model: check-8 (deregistration
   deposit), check-9 (INT FR debit only) and check-10 (DLR purchase + INT FR
   debit in USD) must pass the cash gate and the section checks for every
   section, and check-1 … check-7 must still pass. The script prints PASS/FAIL
   verdicts only (safe summary).
   Check: script exit 0, ten PASS lines.
7. **Docs.** Update `docs/IMPORT_COMMANDS.md` (amounts stay in the printed
   column and the cash gate catches misreads; INT FR debit = INTEREST_PAID /
   credit = INTEREST_RECEIVED; DEREGISTRATION FROM ACCOUNT = TRANSFER).
   Check: `rg -n "DEREGISTRATION" docs/IMPORT_COMMANDS.md`.
8. **Full tests, once** (after all steps):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`. Commit.

## Stop conditions

- An existing test turns out to depend on a removed amount-moving rule and its
  intent is not obvious → stop and ask (the savings-fund and BOUGHT-of-savings
  tests are expected to keep passing unchanged).
- Any of check-1 … check-10 fails the cash gate or the section checks after
  the change → stop and report file + section verdicts; do **not** re-add an
  amount-moving rule.
- The VL model (port 11435) is unreachable during step 6 → stop.
- A PDF is missing from `inbox/` → stop.
- Never print amounts, descriptions or account digits from the PDFs or VL
  output into commits, logs or the chat; the check script's safe-summary rule
  applies.

## Open questions

1. **DEPOSIT or TRANSFER for a DEREGISTRATION FROM ACCOUNT credit?**
   Recommended: **TRANSFER** — the money arrives from another RBC account
   that was deregistered (inter-account transfer in, same family as TFR IN);
   DEPOSIT/CONTRIB mean money entered from outside the account family.
2. **Add INTEREST_PAID to the VL prompt's allowed values?**
   Recommended: **yes** — the model field already allows it (AGENTS.md choice
   list), and letting VL label margin-interest debits directly reduces
   coercion; the debit-based derivation in `_coerce_response` stays as the
   backstop either way.
3. **Non-savings INTEREST_RECEIVED row with both debit and credit set?**
   Recommended: keep both amounts and the label (gate nets them), same policy
   as DIVIDEND NRT rows; only a pure debit flips the label to INTEREST_PAID.
4. **Keep the `INTEREST`-in-description fallback in `normalize_activity_type`?**
   Recommended: **yes** — it only fires for invalid raw labels and the new
   INTEREST_PAID derivation corrects the direction from the printed column.
