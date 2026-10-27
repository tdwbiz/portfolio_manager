# Step 13c-a — text parser vs VL: Account Activity check on four RBC statements

Check only, per `docs/plans/text-activity-check.md` (open questions answered by tdw, Oct 27).
No code change, no database writes, nothing imported. Per the data rules, this report contains
only file names, currency codes, row indices, field names, activity-type enum names, counts,
verdicts and timings — no amounts, quantities, prices, descriptions, symbols, dates or account
digits. Rows are identified by 1-based index within the currency section.

The four check PDFs (per audit 05): `check-1.pdf` recent CAD, `check-2.pdf` old CAD,
`check-3.pdf` old CAD+USD, `check-4.pdf` recent CAD+USD.

## What `_parse_account_activity` does today

`imports/parsers_pdf.py`, function at line 646. Per currency section (called from
`parse_rbc_pdf_multi`):

- Captures the text between the first Account Activity header and the next non-Activity
  section header; repeated headers on continuation pages are skipped (lines 671–689).
- A transaction starts at a date pattern — month abbreviation + day, optionally followed by an
  activity type from the fixed **text vocabulary**: BUY, SELL, SOLD, BOUGHT, DIVIDEND, DIVID,
  INTEREST, TAX, WITHDRAW, TRANSFER (line 692). Lines that do not start with a date are
  skipped.
- When no type is on the line, it looks up to four lines ahead for a continuation containing
  "DIVID" and labels the row DIVIDEND; otherwise the line is dropped (lines 723–736).
- Continuation lines that do not end in a number are appended to the description
  (lines 738–749).
- Tokens are split into dollar-prefixed tokens (debit/credit column) and plain numeric tokens
  (price/rate, quantity); 2-digit numbers and 4-digit year-like numbers are excluded
  (lines 754–776).
- Token-to-field mapping heuristics per activity type (lines 790–818):
  - BUY / BOUGHT: price = plain[0], quantity = plain[1], debit = dollar[0];
  - SELL / SOLD: price = plain[0], quantity = plain[1], credit = dollar[0];
  - INTEREST / DIVIDEND / DIVID: price = plain[0], quantity = plain[1], credit = last dollar
    token (falling back to the last plain token);
  - TAX: debit = first dollar token (fallback first plain);
  - WITHDRAW: **credit** = first dollar token (fallback first plain) — the money lands in the
    credit column, not debit;
  - TRANSFER: quantity = plain[0] only, no money fields.
- Unparseable tokens become `None` with a warning log (`safe_decimal`, line 831).
- `symbol` is hard-coded `''` — "Symbol extraction disabled" (lines 845–847).
- The per-tx `currency` field is hard-coded `'CAD'` (line 861); **however**
  `parse_rbc_pdf_multi` overwrites it with the section currency for every row
  (lines 1317–1319), so the multi-parser output carries the section currency, not `'CAD'`.
- Output fields per transaction: `date`, `activity_type`, `description`, `symbol`,
  `quantity`, `price`, `debit`, `credit`, `currency`, `commission` (always zero), `notes`
  (the raw line).

## Method

- Entries: `parse_rbc_pdf_multi` (text) and `parse_rbc_monthly_vl` (VL, Halogen on port
  11435; the regex fallback was **not used** on any of the four files), each run once per PDF
  **directly** by the helper script `pm-logs/text_activity_check.py` (untracked, gitignored),
  with `portfolio_manager.settings_test` only — never through `manage_inbox_import` or the
  watcher. Parser loggers were silenced; comparisons happen in-process.
- **Alignment** (tdw decision Q1): rows are grouped by date on both sides, statement order is
  kept within a date, and the rows of each date are compared **by position**. A date whose
  counts differ gets a `COUNT_DIFFER` line with both counts; its overlapping rows are still
  compared, so a missing row only affects its own date. A date present on only one side yields
  `MISSING(<side>)` for each of its rows.
- **Tolerances**: `debit`/`credit` to the cent; `quantity`/`price` to 4 decimals; exact string
  for `symbol` and `currency`; exact for `date`. `None` vs a value (including zero) counts as
  DIFFER and is reported as "one side absent"; both sides absent is MATCH.
- **Activity-type mapping** (text vocabulary → VL normalised vocabulary):
  BUY→BOUGHT, BOUGHT→BOUGHT, SELL→SOLD, SOLD→SOLD, DIVIDEND→DIVIDEND, DIVID→DIVIDEND,
  INTEREST→INTEREST_RECEIVED, TAX→TAX, WITHDRAW→WITHDRAWAL, TRANSFER→TRANSFER. A text type not
  in the map would be reported `UNMAPPED(<type>)`; none occurred.
- **Cash reconciliation per side and currency**: opening + Σcredit − Σdebit == closing, to the
  cent, on that side's own numbers (Cash balance row `opening`/`closing`, all section
  transactions, naive sum — net-zero types not excluded, per tdw decision Q3).

## Results per file

Verdict cells: MATCH / DIFFER / MISSING(side) / UNMAPPED(type); "absent" = one side absent
(None vs a value, incl. zero).

### check-1.pdf (recent, CAD)

Rows per side: text = 2, VL = 2. Cash reconciliation: **text DIFFER**, **VL MATCH**.

| row | date | activity_type | symbol | currency | quantity | price | debit | credit |
|---|---|---|---|---|---|---|---|---|
| 1 | MATCH | DIFFER (text INTEREST_RECEIVED, VL DIV REIN) | DIFFER | MATCH | DIFFER (absent) | DIFFER | DIFFER (absent) | DIFFER |
| 2 | MATCH | DIFFER (text DIVIDEND, VL DIST) | DIFFER | MATCH | DIFFER (absent) | DIFFER | DIFFER (absent) | DIFFER |

### check-2.pdf (old, CAD)

Rows per side: text = 1, VL = 4. Cash reconciliation: **text DIFFER**, **VL MATCH**.

Three dates carry exactly one VL row and no text row each: `COUNT_DIFFER (text=0, VL=1)` for
VL rows 1, 2 and 4 — all three `MISSING(TEXT)`. The fourth date matches by position:

| row (text = VL) | date | activity_type | symbol | currency | quantity | price | debit | credit |
|---|---|---|---|---|---|---|---|---|
| 1 (= VL row 3) | MATCH | MATCH | DIFFER | MATCH | DIFFER | DIFFER | DIFFER (absent) | DIFFER (absent) |

### check-3.pdf (old, CAD + USD)

**CAD** — rows per side: text = 0, VL = 2. Cash reconciliation: **text DIFFER**, **VL MATCH**.
Both dates carry one VL row and no text row: `COUNT_DIFFER (text=0, VL=1)` for VL rows 1 and 2
— both `MISSING(TEXT)`. No comparable rows.

**USD** — rows per side: text = 1, VL = 1. Cash reconciliation: **text MATCH**, **VL MATCH**.

| row | date | activity_type | symbol | currency | quantity | price | debit | credit |
|---|---|---|---|---|---|---|---|---|
| 1 | MATCH | MATCH | DIFFER | MATCH | DIFFER | MATCH | DIFFER (absent) | MATCH |

### check-4.pdf (recent, CAD + USD)

**CAD** — rows per side: text = 2, VL = 3. Cash reconciliation: **text DIFFER**, **VL MATCH**.
One date carries one VL row and no text row: `COUNT_DIFFER (text=0, VL=1)` for VL row 2 —
`MISSING(TEXT)`.

| row (text = VL) | date | activity_type | symbol | currency | quantity | price | debit | credit |
|---|---|---|---|---|---|---|---|---|
| 1 | MATCH | DIFFER (text INTEREST_RECEIVED, VL DIV REIN) | DIFFER | MATCH | DIFFER (absent) | DIFFER | DIFFER (absent) | DIFFER |
| 2 (= VL row 3) | MATCH | DIFFER (text DIVIDEND, VL DIST) | DIFFER | MATCH | DIFFER (absent) | DIFFER | DIFFER (absent) | DIFFER |

**USD** — rows per side: text = 2, VL = 2. Cash reconciliation: **text DIFFER**, **VL MATCH**.

| row | date | activity_type | symbol | currency | quantity | price | debit | credit |
|---|---|---|---|---|---|---|---|---|
| 1 | MATCH | DIFFER (text INTEREST_RECEIVED, VL DIV REIN) | DIFFER | MATCH | DIFFER (absent) | DIFFER | DIFFER (absent) | DIFFER |
| 2 | MATCH | MATCH | DIFFER | MATCH | DIFFER | MATCH | DIFFER (absent) | DIFFER |

## Currency field (tdw decision Q4)

The per-transaction `currency` field is **MATCH on every compared row, including the USD
sections** — because `parse_rbc_pdf_multi` overwrites the hard-coded `'CAD'` with the section
currency (lines 1317–1319). The gap tdw asked to quantify therefore exists only below the
multi-parser entry point: `_parse_account_activity` itself stamps `'CAD'` on every row
(line 861), which would DIFFER on all rows of a USD section if that function were used
directly.

## Summary verdict per file

- **check-1.pdf**: both rows found and dated, but both activity types mislabelled (INTEREST vs
  DIV REIN; DIVIDEND vs DIST), symbol empty, and quantity/price/debit/credit all wrong.
  Not sufficient for text-first transactions.
- **check-2.pdf**: 1 of 4 rows found (3 dates missed entirely). The found row has the right
  date and type but wrong quantity/price and no money on either field. Not sufficient.
- **check-3.pdf**: CAD 0 of 2 rows found; USD 1 of 1 with date, type, price and credit right
  but quantity wrong and debit absent. Not sufficient.
- **check-4.pdf**: CAD 2 of 3 (both mislabelled); USD 2 of 2 (one mislabelled). Money fields
  wrong on every compared row. Not sufficient.

**Overall recommendation:** the text parser does not yet reproduce the Account Activity table
well enough for text-first transactions in 13c; transactions should stay VL-sourced unless the
text parser is reworked first. The gaps, in order of impact: (1) row detection misses most
rows — on old statements only dividend-like lines that carry a recognisable date+type or a
"DIVID" continuation are seen; (2) the token-to-column mapping is wrong for DIV REIN / DIST /
INTEREST rows (money and per-share rate land in the wrong fields, or not at all); (3) symbol
extraction is disabled (hard-coded `''`); (4) the text vocabulary has no DIV REIN / DIST /
TFR-OUT / WIRE TFR / CONTRIB / DEPOSIT / ADJUST / RTC / FEE equivalents, so type alignment
relies on the INTEREST/DIVIDEND mislabels above; (5) WITHDRAW money is mapped to the credit
column. The text side's cash reconciliation DIFFERs in 5 of 6 currency sections while the VL
side MATCHes in all 6 — consistent with the missing/mis-mapped rows rather than with the VL
numbers.

## Timings (seconds, single run per parser per file)

| file | text parser | VL parser |
|---|---|---|
| check-1.pdf | 0.25 | 30.11 |
| check-2.pdf | 0.11 | 35.71 |
| check-3.pdf | 0.23 | 41.50 |
| check-4.pdf | 0.30 | 46.39 |
