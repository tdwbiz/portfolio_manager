# Plan: taxable_events (step 15a — read-only report on the two taxable RBC accounts)

Branch: `chore/taxable-events`, from `dev`.

## Goal

A management command `taxable_events [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE`
that writes a plain-text report gathering the facts needed for the "known gaps in the taxable
RBC accounts" (architecture §12 step 15) — opening-ACB questions, RBF interest, DLR currency
round-trips, cross-account symbols, book-cost anomalies. The command is strictly read-only:
the only file written is `--out`. Default accounts: every `Account` with `category="TAXABLE"`
that has at least one `Transaction` (today: `TS_HUISGELD`, `TS_CAR_LIVING`). `--account NAME`
restricts to one account (still must be TAXABLE with transactions). `--from` / `--to`
(`YYYY-MM`) bound the transaction/statement periods; open-ended on either side.

Report sections (numbered as in the task):

1. **Non-plain transactions** — every `Transaction` in range whose `type` is not one of
   `BUY, SELL, DIV, DIST, INT`, plus two extra catches from *any* type: rows with
   `quantity != 0 and amount_cad == 0`, and rows carrying an option symbol. Printed with
   date, type, symbol, quantity, price, amount_cad, currency, notes. Sub-rules:
   - `REORGANIZATION`: print the subtype = first token of `notes` (`DIS`, `CIL`, `MGR`, `MER`,
     `NAC`, `EXP`); mark unknown subtypes `?`.
   - `RETURN_OF_CAPITAL`: print value (`amount_cad`), `quantity` and full `notes` (to see
     where its amount went).
   - `TRANSFER`: only listed when `quantity != 0` (the plain cash transfers are noise).
   - Option symbols: detected with the existing helpers — `normalize_option_text` /
     `option_text_from_notes` from `core/utils/monthly_check.py` (option text in notes) and
     the short-option convention (`symbol` starts with `9`).
2. **RBF interest rows** — every `Transaction` with `symbol` starting `RBF` in range: date,
   settlement date, quantity, price, amount, notes. Then, per account, the
   `RbcMonthlyTransaction` reference rows of the **January** statements of 2024, 2025 and
   2026 (rows whose `symbol` starts `RBF` or whose `activity_type` is `INTEREST_RECEIVED`),
   with date, symbol, quantity, price, debit, credit, description.
3. **Currencies** — per account: transaction count per (currency, type); and for USD rows,
   whether an exchange rate is carried: `exchange_rate_cad != 1` (field) or a rate-looking
   token in `notes` (regex `@\s*\d+\.\d+` or `FX`), counted separately.
4. **DLR rows** — every `Transaction` with `symbol == "DLR"` (notes) and every
   `RbcMonthlyTransaction` with `symbol == "DLR"` or `DLR` in `description` (description),
   with dates and amounts.
5. **Symbols held in both accounts in the same month** — group `RbcMonthlyHolding` rows by
   statement year-month and normalised symbol (via `make_symbol_key(account_pk)`), report
   symbols present in ≥ 2 distinct accounts in the same month, with quantity and currency
   per account.
6. **TS_HUISGELD Dec 2017 statement** — the `RbcMonthly` of that account with
   `statement_date` in 2017-12: each `RbcMonthlyHolding` (symbol, quantity, currency,
   book_cost), flagging rows with missing book cost (`book_cost` is 0 — the column is
   non-null `Decimal("0.00")` default, so 0 means "not printed on the PDF"). If no such
   statement is stored, print a one-line note instead.
7. **First stored TS_CAR_LIVING statement** — same columns for the earliest
   `statement_date` of that account. Same missing-book-cost flag and same "not stored" note.
8. **Unexplained PDF book-cost changes** — per account, symbol and year (year of the later
   statement): between consecutive `RbcMonthly` statements (per account+currency, ordered
   by `statement_date`), where a symbol's `book_cost` changed but its `quantity` did not and
   no transaction of that symbol falls in `(prev_date, cur_date]`. Period membership and
   symbol matching use `period_transactions(...)` and `make_symbol_key` from
   `core/utils/monthly_check.py` — do not copy that logic.
9. **Stored monthly-check verdicts** — `MonthlyCheckResult` rows of `TS_CAR_LIVING` whose
   `statement_date` is in 2021-06 or 2024-05: statement date, currency, verdict, notes.
   If none stored, print a one-line note.

Privacy: the report prints only account names, dates, symbols, currencies, quantities and
amounts — never account digits. Statement files are referenced by account name +
statement_date + currency; if a file name is ever printed it goes through
`classify_safe_name` (`core/utils/monthly_check.py`) and only the safe name is printed.

## What changes

- `core/utils/taxable_events.py` — new. One function per section, each returning a list of
  report lines; imports `effective_tx_date`, `period_transactions`, `make_symbol_key`,
  `normalize_option_text`, `option_text_from_notes`, `classify_safe_name` from
  `core/utils/monthly_check.py`. Section 6/7 targets (account name + month / "first") are
  module-level constants.
- `core/management/commands/taxable_events.py` — new. Mirrors `monthly_check` report mode:
  `--out` required, `--account`, `--from`, `--to` parsing (reuse the same validation style),
  default-account selection, writes the file, prints the path. No DB writes.
- `core/tests/test_taxable_events.py` — new.
- `docs/IMPORT_COMMANDS.md` — one short section for the command (usage line + one paragraph,
  pointing here for background).

No models, no migrations, no views.

## Out of scope

- Anything that writes to the database (no `--store`), the opening-ACB table itself
  (step 15 proper), ACB/gain computation, owner split, PortfolioTax export, any UI page.
- Fixing the data the report exposes (missing book costs, sign-flipped rows).
- Non-taxable accounts, IB accounts.

## Steps

1. **Utils skeleton + account selection.** `core/utils/taxable_events.py` with
   `taxable_accounts(only_name=None)` (TAXABLE + has transactions), period helpers
   (`month_bounds` for `--from`/`--to`), and empty section functions with docstrings;
   command file that parses args, requires `--out`, resolves accounts, writes the file.
   Check: `/opt/pm-venv/bin/python manage.py taxable_events --help` and
   `git status --short` shows only the two new files.
2. **Sections 1–2.** Non-plain transactions and RBF interest (incl. January PDF reference
   rows). Check: `rg -n "make_symbol_key|effective_tx_date" core/utils/taxable_events.py`
   shows imports from `monthly_check`, no local re-implementation.
3. **Sections 3–5.** Currency counts + FX presence, DLR rows, cross-account same-month
   symbols. Check: `git diff --stat` touches only the two new files.
4. **Sections 6–7.** Fixed-target holdings dumps with the missing-book-cost flag.
   Check: `rg -n "account_number|account_id" core/utils/taxable_events.py` → no hits.
5. **Sections 8–9.** Book-cost-change scan (via `period_transactions` + `make_symbol_key`)
   and stored verdicts. Check: `rg -n "def " core/utils/taxable_events.py` lists all nine
   section functions.
6. **Docs.** Add the `taxable_events` section to `docs/IMPORT_COMMANDS.md`.
   Check: `git grep -n taxable_events -- docs/IMPORT_COMMANDS.md` → hits.
7. **Tests** (see below), then the full test gate.

## Tests (step 7, once)

Synthetic data only, fake accounts `90000001`–`90000004` as `Account` rows with
`category="TAXABLE"` and `fingerprint=fingerprint_account_number(...)`; always create a
`BrokerStatement` before a `Transaction`; BUY rows use negative `amount_cad`.

- Selection: only TAXABLE accounts with transactions are picked; `--account` filters and
  rejects a non-taxable name; `--from`/`--to` bound the sections.
- One test per section on small fixtures: reorg subtype parsing; RETURN_OF_CAPITAL fields;
  TRANSFER with/without quantity; qty≠0/amount=0 catch; option-symbol detection; RBF rows
  and January reference rows; USD FX-presence counting; DLR rows; cross-account same-month
  symbol hit; missing book cost flagged (section 6/7 constants pointed at the test account
  and month); section 8 fires on a book-cost change with no tx and stays silent when a tx
  exists in the period; section 9 prints a stored `MonthlyCheckResult` and the "none" note.
- **Nothing is written**: run the command via `call_command` inside
  `CaptureQueriesContext` and assert no query contains `INSERT`/`UPDATE`/`DELETE` against
  the app tables, and that row counts of every model touched by the fixtures are unchanged
  after the run; the only artefact is the `--out` file (written to `tempfile` dir).
- Privacy: report text contains none of the fake account digits (`90000001`… absent).

Final gate (once, at the end of step 7):

```
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

## Stop conditions

- Stop if `monthly_check` helpers cannot be reused as-is (e.g. `make_symbol_key` needs
  per-account option code maps that make cross-account comparison in section 5 impossible) —
  revise the plan instead of copying logic.
- Stop if a read-only run turns out to need a DB write (it must not; the command is report-only).
- Stop if `--out` output would require printing an account number or an unsafe file name.
- Don't change this plan while implementing; if a step is wrong, stop and report.

## Open questions

1. **Section 2 "reference rows" scope.** The task says "the RbcMonthlyTransaction reference
   rows of the January statements 2024–2026". Recommendation: restrict to RBF-related rows
   (`symbol` starts `RBF` or `activity_type = INTEREST_RECEIVED`) rather than the whole
   January statement, keeping the report focused on RBF interest; years fixed at 2024–2026
   as module constants.
2. **Hard-coded section 6/7 targets.** Sections 6 and 7 name specific accounts/months.
   Recommendation: module-level constants in `core/utils/taxable_events.py`
   (`SECTION6_ACCOUNT="TS_HUISGELD"`, `SECTION6_MONTH="2017-12"`,
   `SECTION7_ACCOUNT="TS_CAR_LIVING"` → first stored statement), each section printing a
   one-line "not stored" note when the target is absent (e.g. in the test DB); no extra CLI
   flags. These are report targets, not configuration, so constants are acceptable.
3. **Missing book cost definition.** `book_cost` is non-null with default 0.
   Recommendation: flag `book_cost == 0` as "missing" (a real position with zero book cost
   does not occur in these statements); note the convention in the report header.
4. **`--out` required or optional.** `monthly_check` requires `--out` in report mode.
   Recommendation: require it here too — the report is long and stdout-only use is not a goal.
