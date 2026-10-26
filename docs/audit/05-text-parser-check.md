# Step 12a — text parser vs VL check on two RBC statements

Check only, per `docs/plans/text-parser-check.md` (decisions: tdw, Oct 26). No code change,
no database writes. Per the data rules, this report contains only field names, counts, dates,
section names, currency codes, yes/no flags and MATCH/DIFFER/MISSING verdicts — no account
numbers, names, amounts, quantities or prices.

## Method

- PDFs: `inbox/check-1.pdf` (statement date SEPT 30, 2025 — recent layout) and
  `inbox/check-2.pdf` (statement date MAR. 31, 2020 — old layout), both account TS_HUISGELD,
  both CAD-only (one currency section each).
- The deterministic text parser (`imports/parsers_pdf.py`: `parse_rbc_pdf`,
  `parse_rbc_pdf_multi`) and the VL parser (`imports/parsers_monthly_vl.py`, Halogen on port
  11435) were each run once per PDF **directly** by the helper script
  `pm-logs/text_parser_check.py` (untracked, gitignored), with
  `portfolio_manager.settings_test` only — never through `manage_inbox_import` or the watcher.
- The VL regex fallback was **not used** on either PDF (the model answered both; no timing of
  the fallback, per tdw's decision).

## What the text parser extracts and what it misses

Extracted (both PDFs):

- Currency sections: CAD found (one section per PDF).
- Sections found in the raw text: Asset Summary, Cash Balance, Income Summary, Account
  Activity — all four on both PDFs.
- Cash per currency: present (1 row) for CAD on both PDFs.
- Total market value: beginning total and ending total both found on both PDFs.

Missed (both PDFs):

- **Statement date: not extracted.** In both layouts the month/day sits on a line that begins
  with other words, and the year is on the following line, so neither the parser's single-line
  nor its two-line date matcher fires; the filenames carry no date either. The VL parser did
  extract the dates (SEPT 30, 2025 / MAR. 31, 2020).
- **Holdings: no field at all.** `symbol` is hard-coded `''` at L661 of
  `imports/parsers_pdf.py`; the text parser output has no per-security rows.

## Comparison table (text vs VL)

| Field | check-1 (2025) | check-2 (2020) |
|---|---|---|
| statement_date | MISSING (text side) | MISSING (text side) |
| currency_sections | MATCH (CAD) | MATCH (CAD) |
| number_of_holdings | MISSING (text side has no holdings field; VL count = 3) | MISSING (text side has no holdings field; VL count = 3) |
| symbols | MISSING (text side has no per-security symbols; VL: RBF2010, VDY, VFV) | MISSING (text side has no per-security symbols; VL: VDY, VFV, XDIV) |
| cash_per_currency | MATCH (CAD present both sides, 1 row each) | MATCH (CAD present both sides, 1 row each) |
| total_market_value | MATCH (begin + end found both sides) | MATCH (begin + end found both sides) |

## Holdings in the raw text layer (added check per tdw's decision)

The raw extracted text (not the text parser's output) contains an "Asset Review" holdings
table with a two-line header (SECURITY/SYMBOL … QUANTITY/SEGREGATED … MKT. PRICE / BOOK COST /
MARKET VALUE). Holding rows were counted as table lines carrying a market value, excluding the
total lines:

| PDF | holding rows found in extracted text | VL holdings count | symbol column recognisable | quantity column recognisable |
|---|---|---|---|---|
| check-1 (2025) | 3 | 3 | yes | yes |
| check-2 (2020) | 3 | 3 | yes | yes |

**Verdict for step 12b: add holdings to the text parser, with VL as the fallback.** The table
is present in both layouts, the row counts match VL exactly on both PDFs, and the symbol and
quantity columns are recognisable.

## Timings (per file)

| File | text parser | VL parser (Halogen) |
|---|---|---|
| check-1.pdf | 0.56 s | 42.08 s |
| check-2.pdf | 0.33 s | 51.38 s |

## Layout difference (old vs recent statement)

- Both layouts contain all four expected sections plus the Asset Review holdings table, and
  both parsers could find them.
- Old (check-2): marketing copy is interleaved with the Cash Balance section text, and some
  words come out joined without spaces (the total lines read "TotalValueof…"). The text parser
  still found the cash row and the totals, and the holdings row count was correct.
- Recent (check-1): cleaner spacing; total lines read "Total Value of …".
- Both PDFs are CAD-only, so multi-currency splitting was not exercised by this check.

## Recommendation (the verdict this run gives)

Text-first with VL as the fallback is supported by the evidence for: **currency sections, cash
per currency and total market value** (all MATCH on both layouts), and — once step 12b adds
holdings parsing — **per-security holdings** (text-layer table readable, counts match VL).

It is not yet supported for **statement date**: the text parser missed it on both layouts. Keep
VL as the source for that field until the text parser's date matching is fixed (a small change,
candidate for 12b).

So: proceed with step 12b as text-first / VL-fallback covering currency sections, cash, total
market value and holdings; statement date stays VL-sourced until the text parser fix lands.
