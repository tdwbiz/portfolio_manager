# Plan: Phase 3 step 13c-a — text-parser Account Activity check (check only)

## Goal

Find out what `imports/parsers_pdf.py` (the deterministic text parser) does today with the
**Account Activity table** of RBC monthly statements, and how well, compared with the VL parser
(`imports/parsers_monthly_vl.py`, Halogen on port 11435). This is the evidence base for step 13c
(PDF transactions); step 13 of `docs/architecture.md` §12 minus PDF transactions is already
planned in `docs/plans/import-checks.md`, which explicitly defers transactions to 13c.

Using the four check PDFs in `inbox/` (`check-1.pdf`…`check-4.pdf`; per audit 05: check-1 recent
CAD, check-2 old CAD, check-3 old CAD+USD, check-4 recent CAD+USD), per **currency section**:

- compare the transactions from the text parser with those from the VL parser **row by row** on
  `date`, `activity_type`, `symbol`, `currency`, `quantity`, `price`, `debit`, `credit` — money to
  the cent, quantity and price to 4 decimals — never "found on both sides";
- report the **number of rows per side**;
- run a **cash reconciliation per side**: opening cash + sum(credits) − sum(debits) equals
  closing cash, to the cent (opening/closing cash from each side's own Cash balance row).

**This is a check only** (like step 12a, `docs/plans/text-parser-check.md`). No behaviour change,
no database writes, no migrations, no parser changes, nothing imported. The only file added to
the repo is the report `docs/audit/06-text-activity-check.md`. Pi has no route to mercury.

## Hard rules

- Run both parsers on the four PDFs **directly** (helper script), never through
  `manage_inbox_import` or the watcher, with `portfolio_manager.settings_test` only.
- The report and console output carry **verdicts, field names, counts and timings only** — never
  amounts, quantities, prices, descriptions, symbols, dates or account digits. Rows are
  identified by **index within the currency section**, not by date. The only enum words allowed
  in verdicts are the `activity_type` names (fixed vocabulary from `core/models.py` choices).
- **Silence the parsers' loggers** while they run (reuse the `_silence_parsers` pattern from
  `pm-logs/text_parser_check.py`): `parsers_pdf` logs warnings that embed raw values
  (e.g. "Invalid price value …") which must never reach the console.
- Do not move, rename or delete the PDFs in `inbox/`.
- Helper script lives in `pm-logs/` (gitignored, `.gitignore` line 53), not in the repo.

## What changes

| File | Change |
|---|---|
| `pm-logs/text_activity_check.py` | new helper script (untracked, never committed) |
| `docs/audit/06-text-activity-check.md` | new report (the only committed artifact) |

Nothing else. No code, no tests, no migrations touched.

## Out of scope

- Any change to `imports/parsers_pdf.py`, `imports/parsers_monthly_vl.py`, the importers,
  `manage_inbox_import`, the watcher, or any model (incl. `RbcMonthlyTransaction`).
- Nothing is imported; no DB touched.
- Holdings / cash / totals comparison — already done in steps 12a/12b (`docs/audit/05-text-parser-check.md`).
- Updating `docs/architecture.md` — the report carries the recommendation; tdw folds the
  decision into 13c afterwards.

## Helper script design (`pm-logs/text_activity_check.py`)

- `DJANGO_SETTINGS_MODULE=portfolio_manager.settings_test`, `django.setup()`, then import both
  parsers. Entries:
  - **text**: `parse_rbc_pdf_multi(path)` → per-currency sections; each has `currency`,
    `balances` (Cash row: `opening`, `closing`) and `transactions`
    (`date`, `activity_type`, `symbol`, `quantity`, `price`, `debit`, `credit`, …). Note today
    `symbol` is hard-coded `''` (see the "Symbol extraction disabled" comment in
    `_parse_account_activity`) and the per-tx `currency` field is hard-coded `'CAD'`. The
    comparison uses the **per-tx `currency` field** (per tdw's decision on Q4), so USD sections
    show the text side as `CAD`.
  - **VL**: `parse_rbc_monthly_vl(path)` → per-currency sections with the same transaction
    field names and `balances` (`opening`, `closing`).
- **Row alignment** (per tdw's decision on Q1): **align per date, not by index after a sort.**
  Group both sides' transactions by `date`, keep statement order within a date, and compare the
  rows of each date **by position**. A date whose counts differ gets a `COUNT_DIFFER` line with
  both counts; its overlapping rows are still compared, so a missing row only affects its own
  date. Rows whose date appears on only one side are reported as `MISSING(<side>)`. Row identity
  in the report is the 1-based index within the currency section.
- **Per-row, per-field verdicts** for `date` (exact), `activity_type` (mapped, see below),
  `symbol` (exact string; text side is always `''`), `currency` (exact string; text side is
  always `'CAD'`, so USD sections show the gap — reported per tdw's decision on Q4), `quantity`
  and `price` (equal to 4 decimals; `None` vs `0` counts as DIFFER and is reported as such),
  `debit` and `credit` (equal to the cent). Verdict words: `MATCH`, `DIFFER`, `MISSING(TEXT)`,
  `MISSING(VL)`, plus `COUNT_DIFFER` lines for dates whose row counts differ.
- **Activity-type mapping** (text vocabulary → VL normalised vocabulary), defined in the script:
  `BUY→BOUGHT`, `BOUGHT→BOUGHT`, `SELL→SOLD`, `SOLD→SOLD`, `DIVIDEND→DIVIDEND`, `DIVID→DIVIDEND`,
  `INTEREST→INTEREST_RECEIVED`, `TAX→TAX`, `WITHDRAW→WITHDRAWAL`, `TRANSFER→TRANSFER`.
  A text type not in the map → verdict `UNMAPPED(<text-type-name>)` (a finding, not a stop).
- **Cash reconciliation per side, per currency**: `opening + Σcredit − Σdebit == closing` on
  that side's own numbers (Cash balance row `opening`/`closing`, all transactions of the
  section, naive sum — net-zero types are **not** excluded; a DIFFER caused by how DIV REIN /
  INTEREST rows are represented is itself the finding). Verdict `MATCH`/`DIFFER` per
  (file, currency, side).
- **Timings**: `time.perf_counter()` around each parser call; report seconds per file per side.
- **Output**: only file name (`check-N.pdf`), currency code (CAD/USD), row index, field name,
  verdict word (incl. activity-type enum names), counts and timings. The script must never
  print a parser result dict. No file output; the report is written by hand from the console.
- `--text-only` flag: run and print only the text side (counts, cash reconciliation, timings)
  so the fast half can be checked before spending VL time.

## Steps (one commit each)

1. **Write the helper script** `pm-logs/text_activity_check.py` (untracked).
   Check: `/opt/pm-venv/bin/python -c "import ast; ast.parse(open('pm-logs/text_activity_check.py').read())"`
   and `git status --short` shows **no** new tracked files.

2. **Run the text side only** (fast, no model):
   `/opt/pm-venv/bin/python pm-logs/text_activity_check.py --text-only`.
   Check: exit code 0; output contains only file names, currency codes, row indices, field
   names, verdict words, counts and timings — eyeball with
   `... | grep -E '\$|[0-9]{5,}'` (nothing except timings like `0.42` may look like money).

3. **Run the full comparison** (VL on Halogen, ~26–48 s per PDF):
   `/opt/pm-venv/bin/python pm-logs/text_activity_check.py`.
   Check: exit code 0; per file × currency: two row counts, one verdict line per row × field,
   two cash-reconciliation verdicts (text, VL), two timings. If Halogen (port 11435) is
   unreachable or VL errors on a file → stop (see Stop conditions).

4. **Write `docs/audit/06-text-activity-check.md`** with:
   - what `_parse_account_activity` does today (field list; the hard-coded empty `symbol`; the
     hard-coded per-tx `currency: 'CAD'`; the fixed text activity vocabulary; the
     price/quantity/debit/credit token-mapping heuristics per activity type; continuation-line
     handling) — with file and line references, no values;
   - method: entries, alignment rule, tolerances (cent / 4 decimals), the type-mapping table;
   - per file, per currency: a row-by-row table (rows = row index, columns = the eight fields
     `date`, `activity_type`, `symbol`, `currency`, `quantity`, `price`, `debit`, `credit`,
     cells = MATCH / DIFFER / MISSING(side) / UNMAPPED(type), plus COUNT_DIFFER lines for dates
     whose counts differ); rows per side; the two
     cash-reconciliation verdicts;
   - a summary verdict per file: does the text parser reproduce the Activity table well enough
     for text-first transactions in 13c, or must transactions stay VL-sourced;
   - timings per parser per file.
   Check: `grep -nE '[0-9]{8}|\$' docs/audit/06-text-activity-check.md` returns nothing;
   `git status --short` shows only this one new file. Commit:
   `docs: text-parser vs VL Account Activity check on four statements (step 13c-a)`.

5. **Tests once at the end** (nothing was changed; this proves the run left the tree clean):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.
   Check: all pass; `git status --short` clean.

## Stop conditions

- Halogen on port 11435 unreachable, or the VL parser errors on any of the four PDFs → stop and
  report; do **not** write a partial report.
- Any amount, quantity, price, description, symbol, date or account digits appears on screen or
  would land in the report → stop, fix the script's output first.
- A PDF turns out not to be a monthly statement → stop and ask; do not substitute other files.
- A parser raising on a file is a **finding** (record "parser error" for that side), not a stop —
  unless VL fails on all four (then stop).

## Tests

Run once, at the end of step 5 only:

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

No new tests (check only, no code change).

## Open questions

Answered in **Decisions (tdw, Oct 27)** below.

1. *Row alignment: index-after-date-sort or greedy matching by (date, amount)?* —
   Recommended: index-after-date-sort. **Superseded by tdw's decision: align per date** (group
   both sides by date, keep statement order within a date, compare by position; COUNT_DIFFER on
   count mismatch).
2. *May the report name the activity types in verdicts?* — **Recommended: yes.** The type names
   are a fixed enum (choices in `core/models.py`), not data; a bare `DIFFER` on `activity_type`
   would be useless for deciding 13c. Amounts, dates and symbols stay out.
3. *Exclude net-zero types (DIV REIN, INTEREST_RECEIVED) from the cash reconciliation?* —
   **Recommended: no.** Compute the naive sum; if a DIFFER is caused by how net-zero rows are
   represented (e.g. DIV REIN carrying a debit without a matching credit), that is a parser
   finding 13c must know about.
4. *Symbol will DIFFER on every text row (hard-coded `''`).* — **Recommended: still compare it
   row by row** and report it honestly; it quantifies the gap 13c has to close (or argues for
   VL-sourced transactions).

## Decisions (tdw, Oct 27)

- **Q1 — Row alignment:** align per date instead of index-after-sort: group both sides by
  `date`, keep statement order within a date, and compare the rows of each date by position; a
  date where the counts differ gets a `COUNT_DIFFER` line with both counts, and its overlapping
  rows are still compared, so a missing row only affects its own date.
- **Q2 — Naming activity types in verdicts:** yes, as recommended.
- **Q3 — Net-zero types in the cash reconciliation:** yes, as recommended (naive sum, no
  exclusions).
- **Q4 — Symbol comparison:** yes, as recommended; also compare the per-transaction `currency`
  field, since the text parser sets `CAD` on every row, and report it for the USD sections.
