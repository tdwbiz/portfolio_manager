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
| TFR OUT | `TRANSFER` | 0 | credit − debit (RBC's CSV calls transfers between accounts Transfers, as the deregistration rows in 13d) |
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

## Zero-net rows with quantity (decision 1, tdw Oct 30)

`align_cash_rows` drops zero-net rows from both unmatched lists (real-data fix 5), so
it cannot surface them as fill candidates. A second matcher in
`core/utils/fill_from_pdf.py` handles the zero-net rows that **carry a quantity**
(spin-off shares, DLR journals, savings-fund units) — they matter for the holdings
check:

- **Candidates:** PDF rows of the period whose net cash (`credit − debit`) is 0 and
  whose `quantity != 0`. Rows with neither cash nor quantity stay out (not fill
candidates). The NRT split does not apply to zero-net rows.
- **Match rule** against the candidate CSV set (period transactions minus slice rows,
same currency): equal signed quantity (±`QTY_TOL`), equal normalised symbol
(`SYMBOL_ALIASES` plus option normalisation — `normalize_option_text`, and the CSV
option-code map from `monthly_check._option_code_map`), with ≤ 5 days date slack (the
PDF row's date vs the CSV row's effective date, as in `align_cash_rows`). A matched
row is not a fill candidate.
- **Fill rule** for an unmatched zero-net row: type per the mapping table; quantity =
the PDF quantity signed by the type's convention (BOUGHT +, SOLD −, every other type
+); amount = 0; all common fields as usual (`date` = `settlement_date` = PDF date,
symbol, currency, price, notes). An unmapped activity type still stops the period.

## What changes (files)

- `core/utils/monthly_check.py` — small read-only refactor: `check_cash`,
  `check_holdings` and `check_statement` gain an optional `txs=None` parameter; when
  given, that list of (saved or unsaved) Transaction objects replaces the
  `period_transactions(...)` query (filtered by currency inside `check_cash`).
  Default `None` keeps today's behaviour exactly; existing tests must not change.
- `core/utils/fill_from_pdf.py` — new: the mapping table above,
  `pdf_rows_to_transactions()` (incl. NRT split and the unmapped→stop rule),
  `match_zero_net_rows()` (the zero-net-with-quantity matcher above), and
  `plan_period()` which for one failing `StatementGroup` computes: slice rows to
  remove, unmatched PDF rows (via `align_cash_rows` on the candidate set **plus**
  `match_zero_net_rows()`), mapped candidate Transactions, and the before/after
  verdicts (after = `check_statement` with the `txs` override).
- `core/management/commands/fill_from_pdf.py` — new command: args, dry-run report,
  `--apply` path.
- `core/tests/test_fill_from_pdf.py` — new tests (synthetic data only).

## Out of scope

- No migrations, no model changes.
- Nothing in the check itself changes: verdicts, tolerances and the unmatched-row
  helper stay as they are.
- Zero-net PDF rows with **neither cash nor quantity** stay out of the fill entirely
  (as `align_cash_rows` already drops them, real-data fix 5). Zero-net rows that carry
  a quantity ARE fill candidates — see "Zero-net rows with quantity" above
  (decision 1).
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
     winning RbcMonthly row of that currency)` → unmatched cash PDF rows, plus
     `match_zero_net_rows()` on the same candidate set → unmatched zero-net PDF rows
     with quantity (decision 1).
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
   `pdf_rows_to_transactions()` incl. NRT split and `UnmappedActivity` error, and
   `match_zero_net_rows()` (decision 1: date ±5 days, normalised symbol with the
   check's aliases and option normalisation, signed quantity; rows with neither cash
   nor quantity stay out).
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

## Real-data fixes (Oct 30)

A dry run on tdw's real data (8 accounts, 2019-01..2021-01) exposed four flaws.
Each is one commit, in this order:

1. **Crash — sorting unsaved rows.** `align_cash_rows` sorts the CSV side by
   `(t.date, t.pk)`; in-memory new rows (the fill's own candidates passed via
   the `txs` override) have `pk=None`, so Python compares `None` with an int
   and raises `TypeError` (seen on TOM_TFSA 2020-12). Sort with a key that
   works for unsaved rows: `(t.date, t.pk or 0)` plus a stable index.
2. **Symbols.** Most PDF rows carry no symbol — only the security name in the
   description (e.g. `VANGUARD FTSE CDN HIGH DIVID YIELD INDEX ETF TR UNIT`).
   Derive the symbol from the `RbcMonthlyHolding` rows of the same statement
   (falling back to the previous stored statement of that account, same
   currency): match the description to a holding's security name — normalised
   (upper case, stripped), the holding name at the start of the description or
   the reverse — and take its symbol **only when exactly one holding matches**;
   otherwise leave the row without a symbol (the period then fails its
   after-check and is reported). The check's aliases and option normalisation
   (`SYMBOL_ALIASES`, `normalize_option_text`, the option-code map) apply as
   usual.
3. **Signs.** BUY rows always get `+abs(quantity)` and SELL rows
   `-abs(quantity)`, whatever sign the PDF row has (the run produced
   SELL qty=10 and SELL qty=1430).
4. **Timing pairs.** Before filling, an unmatched cash PDF row — or the PDF
   rows of one date and symbol taken together by their net amount (a dividend
   plus its NRT tax) — that matches an existing Transaction of the same
   account and currency with the same net amount dated within 35 days but
   **outside** this period, is not filled; it is reported as
   `timing pair with CSV row dated <date>` (the run would have added
   SUSAN_RRIF VO 281.29 in July 2020 while the CSV has it in June, and
   SUSAN_TFSA QQQ 31.12 minus 4.66 in Nov 2019 while the CSV has 26.46 in
   October).

## Decisions (tdw, Oct 30)

Answers to the open questions above:

1. **Zero-net rows: no** — do not accept dropping them. Zero-net PDF rows that carry
   a quantity (spin-off shares, DLR journals, savings-fund units) matter for the
   holdings check: match them against the CSV by date with 5 days of slack, symbol
   (with the check's aliases and option normalisation) and quantity instead of
   amount, and fill them when unmatched. Only rows with neither cash nor quantity
   stay out. Implemented as the "Zero-net rows with quantity" section above.
2. **TFR OUT / WIRE TFR / CONTRIB:** `TFR OUT` → `TRANSFER` (RBC's CSV calls transfers
   between accounts Transfers, as the deregistration rows in 13d), `WIRE TFR` →
   `TRANSFER`, `CONTRIB` → `DEP`. `ADJUST`, `RTC` and `OTHER` stay unmapped and are
   flagged (they stop their period by design). The mapping table above is updated.
3. **INTEREST_PAID:** yes — `INT` (negative), matching the CSV's interest rows, as
   recommended.
4. **Empty fill BrokerStatement:** yes — delete it at the end of the run when no
   period committed any rows, as specified in "Command behaviour".
