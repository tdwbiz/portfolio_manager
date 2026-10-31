# Plan: book_cost_check — read-only book-cost check for the taxable accounts (step 15d)

## Goal

A read-only command that replays each taxable account's transactions RBC-style
and compares the replayed **quantity and book cost (native currency, per
security, no FX, no pooling across accounts)** with the book costs printed on
the stored RBC monthly PDFs. It finds where the CSV ledger and RBC's book cost
disagree and where the difference arose — facts needed before any ACB work
(architecture §12, step 15). Nothing is written to the database; the only file
written is `--out`.

Command:

```
python manage.py book_cost_check [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE
```

Default accounts: every `category='TAXABLE'` account that has at least one
`RbcMonthly` statement (today: TS_HUISGELD and TS_CAR_LIVING). `--account`
restricts to one exact name (same error style as `taxable_events` when it
matches nothing). `--from`/`--to` bound which statement dates are compared and
the candidates period (open question 5); the replay itself always starts from
the seed, never re-anchors on the PDF.

## Replay rules (per account, per (symbol_key, currency))

State per security: `quantity` and `cost` (native currency, Decimal); average
cost = `cost / quantity` while quantity > 0. Symbol keys via
`make_symbol_key(account.pk)` from `core/utils/monthly_check.py`; rows are
ordered by the same placement as monthly_check — `effective_tx_date(tx)`,
then `tx.date`, then `tx.pk` — and only rows with `effective_tx_date` strictly
after the seed date are replayed. Always `Transaction.amount` (native),
**never** `amount_cad` (a USD row's `amount_cad` is CAD at the export day's
rate).

Seed: the account's `OpeningPosition` rows at their statement date
(`op.statement.statement_date`) if it has any, else zero everywhere with no
start bound.

Per transaction row:

- **quantity > 0** — add the quantity; if `amount < 0` add `-amount` to the
  cost; elif `amount == 0` and the notes match reinvest-at-price
  (`REINV(AT)?@ C?$X`, the RBF savings funds at 10.00) add `quantity × X`;
  else add 0 **and flag** the row.
- **quantity < 0** — remove `average_cost × units_sold` from the cost
  (snap cost to 0 when quantity reaches 0).
- **RETURN_OF_CAPITAL type, or notes starting `RTC -` and containing
  `ADJUSTMENT TO BOOK COST $X`** — reduce cost by X.
- **notes starting `ADJ -` and containing
  `NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK VALUE $X`** — raise cost by X,
  whatever the stored type. Parsed amounts may start with a dot (`$.59`).
- **TRANSFER with quantity != 0** — out (qty < 0): remove average cost;
  in (qty > 0): add 0 cost; flag the row (the receiving side loses basis).
- **any REORGANIZATION row** — flag it and mark that security *not checked*
  from then on (still replayed, but comparisons after that date report
  NOT CHECKED instead of MATCH/DIFF).
- quantity == 0 rows do nothing except the RTC/ADJ note rules above.

## Comparison (at each stored RbcMonthly statement of the account on/after the seed)

Statements are grouped per `statement_date` (a date may have CAD and USD
`RbcMonthly` rows; holdings carry their own currency). Per PDF holding (key
via `make_symbol_key`, blank symbol → normalised option text or security name
as in monthly_check; **skip `book_cost == 0`** — means "not printed"):

- MATCH when the book cost is equal to the cent (±0.005) — and the quantity
  equal at 4 dp (open question 4); else DIFF with the difference and the
  **change of the difference since this security's previous statement** (so
  where it arose shows once).
- Securities on one side only (replay-only with qty != 0, PDF-only) are
  listed too.
- For every DIFF, list that security's transactions since its last MATCH.

Summary section: per account and security — counts of MATCH and DIFF, the
first DIFF, the largest |difference|. Candidates section: PDF book-cost
changes between consecutive statements with no transaction of that security
in the period — reuse `section_8_book_cost_changes` from
`core/utils/taxable_events.py` directly (import, do not copy).

Output: account names, dates, symbols, currencies, quantities and amounts
only — never account digits (same discipline as `taxable_events`).

## What changes

- **new** `core/utils/book_cost_check.py` — replay engine, comparison,
  summary and report-line builders (pure read-only helpers).
- **new** `core/management/commands/book_cost_check.py` — argument parsing
  (`--account`, `--from`, `--to`, `--out` required), header, sections;
  pattern: `core/management/commands/taxable_events.py`.
- **new** `core/tests/test_book_cost_check.py` — synthetic data, fake
  accounts `90000001`–`90000004` (`category='TAXABLE'`, fingerprints),
  `BrokerStatement` before every `Transaction`, BUY rows with negative
  `amount_cad`; pattern: `core/tests/test_taxable_events.py`.
- **edit** `docs/IMPORT_COMMANDS.md` — one `### book_cost_check` entry under
  "Taxable Accounts Report (read-only)".

No models, no migrations. Out of scope: writing anything (no `PositionHistory`
/ ACB updates), the UI, non-taxable accounts, pooling across accounts, FX
conversion of book cost, correcting any CSV rows the check flags.

## Steps

1. **This plan** — commit `docs/plans/book-cost-check.md`. Check: `git status`
   clean after commit.
2. **Replay engine** in `core/utils/book_cost_check.py`: account selection
   (`taxable_accounts_with_statements`), seeding from `OpeningPosition`,
   transaction ordering (reuse `effective_tx_date`, `make_symbol_key`), the
   per-row rules above, flags. Check:
   `/opt/pm-venv/bin/python -c "import core.utils.book_cost_check"`.
3. **Comparison + summary + candidates wiring**: statement grouping,
   MATCH/DIFF/one-sided lines, delta-since-previous, txs-since-last-MATCH,
   summary counts, call into `taxable_events.section_8_book_cost_changes`.
   Check: `import` again plus `rg -n "section_8_book_cost_changes"
   core/utils/book_cost_check.py` shows the import, not a copy.
4. **Command** `core/management/commands/book_cost_check.py`. Check:
   `/opt/pm-venv/bin/python manage.py book_cost_check --help`.
5. **Tests** in `core/tests/test_book_cost_check.py` (see Tests below).
   Check: `/opt/pm-venv/bin/python -m pytest core/tests/test_book_cost_check.py --collect-only -q`.
6. **Docs**: the `### book_cost_check` entry in `docs/IMPORT_COMMANDS.md`.
   Check: `rg -n "book_cost_check" docs/IMPORT_COMMANDS.md`.
7. **Full test run** (once, at the end):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`. Commit.

## Tests (all synthetic, all in step 5)

- Each replay rule: buy adds cost; sell removes average cost; qty>0 with
  amount 0 and a `REINV@C$10.00`-style note (the RBF rule) adds qty × 10;
  qty>0 with amount 0 and no note is flagged.
- A `RETURN_OF_CAPITAL` row and a notional row **typed `DEP`** with notes
  `ADJ - … NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK VALUE $.59` (dot-first
  amount) — cost moves by the note amount, not the row amount.
- A USD row whose `amount_cad` differs from `amount`: replay uses `amount`.
- A TRANSFER journal pair (+qty / −qty, same account+symbol) that nets the
  quantity to zero; the in-row is flagged.
- A REORGANIZATION row: flagged, and the security is NOT CHECKED at the next
  statement.
- MATCH and DIFF with the delta: two statements where the difference changes
  — the DIFF line shows the delta since the previous statement; the
  txs-since-last-MATCH list appears under the DIFF.
- Seeding from `OpeningPosition` (statement date respected).
- Candidates section fires for a PDF book-cost change with no transaction of
  that security in the period.
- Nothing is written: `CaptureQueriesContext` around `call_command`, assert no
  INSERT/UPDATE/DELETE (same pattern as `core/tests/test_taxable_events.py:469`).

## Stop conditions

- A rule above contradicts real data behaviour discovered while implementing
  (e.g. notes text differs from `RTC -` / `ADJ -` / `REINV@`) — stop and ask;
  the plan is revised first.
- Any need to write to the database, add a model or a migration — stop.
- `make_symbol_key` / `effective_tx_date` cannot be reused as-is — stop and
  ask instead of copying them.

## Open questions

1. **Exact reinvest notes text.** Real CSV notes read `REI - … REINV@C$X.XX`;
   the task says "REINVEST @ $X". *Recommended:* one case-insensitive regex
   `REINV(?:EST)?\s*@\s*C?\$?\s*(\d+\.\d+)` accepting both spellings.
2. **RETURN_OF_CAPITAL row whose notes carry no `$X`.** *Recommended:* use
   `abs(tx.amount)` when non-zero, otherwise add 0 and flag the row.
3. **OpeningPosition with `statement=None` (MANUAL).** *Recommended:* seed it
   at the account's first stored statement date and print a note in the
   report.
4. **Quantity differs but book cost matches.** The task defines MATCH by the
   book cost. *Recommended:* MATCH requires both cost (±0.005) and quantity
   (±0.0001) to agree; a DIFF line names which side differs.
5. **Meaning of `--from`/`--to`.** *Recommended:* they bound which statement
   dates are compared and the candidates period (like `taxable_events`); the
   replay always starts from the seed so no re-anchoring can creep in.
6. **Flag TRANSFER-out too?** The task only says the in-row "is flagged".
   *Recommended:* flag both directions — the pair is the interesting event.
