# fill_from_pdf — fill CSV gaps from PDF reference rows (phase 4, step 14-0d)

Branch: `feature/fill-from-pdf`. No migrations.

## Goal

A management command `fill_from_pdf --account NAME --from YYYY-MM --to YYYY-MM [--apply]`
(dry run by default) that fills gaps in the CSV Transactions using the stored PDF
reference rows (`RbcMonthlyTransaction`) of the monthly statements, reusing
`core/utils/monthly_check.py`. A period is filled **only if the monthly check of that
statement passes after the change**; otherwise it is left unchanged and reported with
the check's remaining differences.

Decisions by tdw (fixed, not re-litigated here):

1. Only statements that **FAIL** the monthly check are touched, and only the PDF rows
   the check's helper (`align_cash_rows`) shows as **unmatched**; a row the CSV already
   has is never copied. FIRST / NO_CSV / PASS periods are never touched.
2. In a failing period, Transactions that came from the `BrokerStatement` whose
   `raw_filename` is `rbc-act-cleaned-slice.csv` are **removed first and replaced** by
   the mapped PDF rows. Slice rows in passing periods stay.
3. A filled row gets the PDF row's `date` as both `date` and `settlement_date`.
4. Verification: a period is filled only if the check passes after the change —
   computed **in memory** in the dry run; in `--apply`, one database transaction per
   period, re-checked inside it and rolled back if it still fails.

## Source fields: what RbcMonthlyTransaction really has

Fields (core/models.py `RbcMonthlyTransaction`): `rbc_monthly` (FK → `RbcMonthly`,
which carries account + `statement_date`), `date`, `activity_type`, `description`,
`symbol`, `quantity`, `price`, `debit`, `credit`, `currency`, `commission`, `notes`.
Net cash of a row = `credit - debit` in the section's currency.

`activity_type` choices (fixed list): BOUGHT, SOLD, DIST, DIVIDEND, DIV REIN,
INTEREST_RECEIVED, INTEREST_PAID, TRANSFER, TFR OUT, WIRE TFR, CONTRIB, DEPOSIT,
WITHDRAWAL, ADJUST, RTC, FEE, OTHER.

## Mapping to CSV type codes (targets are the codes in `_RBC_TYPE_MAP`, imports/parsers.py)

| PDF activity_type | Transaction.type | quantity | amount (= credit − debit) |
|---|---|---|---|
| BOUGHT | `BUY` | +PDF quantity | negative (debit = cost) |
| SOLD | `SELL` | −PDF quantity | positive (credit = proceeds) |
| DIVIDEND | `DIV` | 0 | positive |
| DIST | `DIST` | 0 | positive |
| DIV REIN | `DIV` | +PDF quantity | negative (as the CSV shows reinvestments: negative `amount_cad`, positive qty) |
| INTEREST_RECEIVED | `INT` | +PDF quantity | 0 (RBF funds: cash value = qty × 10, `amount` 0 like the CSV INT rows) |
| INTEREST_PAID | `INT` | 0 | negative |
| DEPOSIT | `DEP` | 0 | positive |
| CONTRIB | `DEP` | 0 | positive |
| WITHDRAWAL | `WDRAW` | 0 | negative |
| TFR OUT | `WDRAW` | 0 | negative |
| WIRE TFR | `TRANSFER` | 0 | credit − debit |
| TRANSFER | `TRANSFER` | 0 | credit − debit |
| FEE | `FEE` | 0 | negative |
| ADJUST, RTC, OTHER | — | — | **unmapped: stops that period, reported, never guessed** |

Common fields on every filled row: `date` = `settlement_date` = PDF `date`;
`symbol` = PDF symbol; `currency` = PDF currency; `price` = PDF price;
`commission` = 0; `amount` = credit − debit (section currency — the monthly check's
CASH uses `Transaction.amount` signed in the native currency); `amount_cad` = `amount`
and `exchange_rate_cad` = 1 (PDF rows are already in the statement currency; the check
never reads `amount_cad`); `notes` = PDF description (plus `source=pdf-fill`).

**NRT split:** a PDF row carrying **both** a credit and a debit (a dividend with NRT
withholding) becomes **two** Transactions — the income row (`credit`, mapped by the
activity type) and a `TAX` row (`−debit`) — as the CSV does. A row with both zero or
only one side is a single row.

## What changes (files)

- `core/utils/monthly_check.py` — small read-only refactor: `check_cash`,
  `check_holdings` and `check_statement` gain an optional `txs=None` parameter; when
  given, that list of (saved or unsaved) Transaction objects replaces the
  `period_transactions(...)` query (filtered by currency inside `check_cash`).
  Default `None` keeps today's behaviour exactly; existing tests must not change.
- `core/utils/fill_from_pdf.py` — new: the mapping table above,
  `pdf_rows_to_transactions()` (incl. NRT split and the unmapped→stop rule), and
  `plan_period()` which for one failing `StatementGroup` computes: slice rows to
  remove, unmatched PDF rows (via `align_cash_rows` on the candidate set), mapped
  candidate Transactions, and the before/after verdicts (after = `check_statement`
  with the `txs` override).
- `core/management/commands/fill_from_pdf.py` — new command: args, dry-run report,
  `--apply` path.
- `core/tests/test_fill_from_pdf.py` — new tests (synthetic data only).

## Out of scope

- No migrations, no model changes.
- Nothing in the check itself changes: verdicts, tolerances and the unmatched-row
  helper stay as they are.
- Zero-net PDF rows (DIV REIN, INTEREST_RECEIVED with all money columns 0) are
  dropped from `align_cash_rows`' unmatched lists (real-data fix 5), so they are not
  fill candidates; a period that needs them simply still fails the after-check and is
  reported unchanged.
- No views/templates; no changes to `monthly_check` command output.
- Never prints account digits; output uses account names, dates, types, symbols,
  amounts only.

## Command behaviour

- `--account NAME` required (no default, like the other `--account` commands);
  `--from` / `--to` are `YYYY-MM` statement-month windows (same `_parse_month`
  pattern as `monthly_check`).
- Group statements with `group_statements` / `previous_groups` / `csv_coverage_bounds`
  over **all** statements of the account (so chaining works), then keep groups whose
  `statement_date` is inside the window and whose verdict is FAIL.
- Per failing period, in order:
  1. `before` verdict (from the check).
  2. Slice rows of the period = `period_transactions(...)` whose
     `statement.raw_filename == 'rbc-act-cleaned-slice.csv'` → removed.
  3. Candidates = period transactions minus slice rows; per currency run
     `align_cash_rows(candidates_of_currency, RbcMonthlyTransaction rows of the
     winning RbcMonthly row of that currency)` → unmatched PDF rows.
  4. Map every unmatched row; any unmapped activity type **stops the period**
     (reported, nothing changes for it).
  5. Simulate: `check_statement(group, prev_group, bounds, txs=candidates + new rows)`
     — must be PASS in every currency, else leave unchanged and report the check's
     remaining failure lines.
- Dry run (default): only the simulation above; writes nothing to the database.
- `--apply`: one `BrokerStatement` for the whole run (`source='RBC'`,
  `raw_filename='pdf-fill-<YYYY-MM-DD>'`, `file_date` = run date,
  `period_start`/`period_end` = min/max date of the rows actually inserted), created
  up front and deleted again at the end if no period committed any rows. Per period:
  `transaction.atomic()` → delete slice rows, insert mapped rows, re-run
  `check_statement` through the normal DB path, roll back (and report) if it fails.
- `tx_hash`: `wrap_tx_hash(compute_tx_hash(broker='PDF', account_id=str(account.pk),
  date=..., tx_type=..., symbol=..., quantity=..., price=..., currency=...,
  amount=..., settlement_date=...))` — the `'PDF'` broker label keeps these hashes
  disjoint from the CSV ones; before inserting, skip any `tx_hash` that already
  exists, so a second run adds nothing (the previously filled rows also now match in
  `align_cash_rows`, so they are not even unmatched). Requires
  `ACCOUNT_FINGERPRINT_KEY` (wrap_tx_hash raises without it) — let it error out.
- Output per period: `verdict before`, rows removed (one line each), rows added (one
  line each), `verdict after`; for untouched failing periods, the check's remaining
  difference lines.

## Steps (one commit each)

1. **Refactor monthly_check helpers.** Add the optional `txs=None` parameter to
   `check_cash`, `check_holdings`, `check_statement` (pass-through). No behaviour
   change. Check: `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/ -q -k
   monthly` and `rg -n "txs=None" core/utils/monthly_check.py`.
2. **Mapping module.** `core/utils/fill_from_pdf.py`: the table above as a dict,
   `pdf_rows_to_transactions()` incl. NRT split and `UnmappedActivity` error.
   Check: `--collect-only` plus a quick import via
   `/opt/pm-venv/bin/python -c "import core.utils.fill_from_pdf"`.
3. **Planning.** `plan_period()` (slice rows, unmatched PDF rows, mapped candidates,
   before/after verdicts via the `txs` override). Check: `pytest --collect-only -q`.
4. **Command, dry run.** Args, window/FAIL filtering, per-period report as specified.
   Check: `/opt/pm-venv/bin/python manage.py fill_from_pdf --help`.
5. **Command, --apply.** One BrokerStatement per run, per-period atomic with re-check
   and rollback, tx_hash dedup. Check: `manage.py fill_from_pdf --help` and
   `git status` (no stray files).
6. **Tests** (`core/tests/test_fill_from_pdf.py`, synthetic data, fake accounts
   90000001+): missing SELL filled and period passes; period still failing after the
   fill is left unchanged; slice row with a wrong sign is removed and replaced; NRT
   row splits into income + TAX; unmapped activity (e.g. ADJUST) stops the period;
   a second `--apply` run adds nothing; the dry run writes nothing (row counts and
   hashes unchanged). Then run the full gate (below) once.

## Tests (final gate, step 6 only)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

## Stop conditions

- Step 1 changes any existing monthly-check test result → stop, the refactor is wrong.
- Any real-data activity type outside the mapping table appears → that period is
  stopped and reported (by design); if *most* periods stop this way, stop and report
  to tdw instead of extending the table.
- A period would need a model/migration change → stop and ask.
- Anything about to print an account number → stop (never allowed).

## Open questions

1. **Zero-net rows** (DIV REIN, INTEREST_RECEIVED): `align_cash_rows` drops them from
   both unmatched lists, so they can never be filled by this command.
   *Recommended:* accept it — a period that needs them fails the after-check and is
   reported unchanged; extend the helper in a later step if real data demands it.
2. **TFR OUT / WIRE TFR / CONTRIB** are not in tdw's list of mapped labels.
   *Recommended:* map TFR OUT→WDRAW, WIRE TFR→TRANSFER, CONTRIB→DEP (the sign rule
   makes them correct), and treat only ADJUST / RTC / OTHER as unmapped; flag in the
   report so tdw can veto.
3. **INTEREST_PAID** → `INT` (negative) rather than `FEE`.
   *Recommended:* `INT`, matching the CSV's interest rows.
4. **Empty fill BrokerStatement** if every period rolls back.
   *Recommended:* delete it at the end of the run when it has no transactions (as
   specified above), so no empty statements accumulate.
