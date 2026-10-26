# Plan: Phase 3 step 12a — text-parser check (check only)

## Goal

Find out what `imports/parsers_pdf.py` (the deterministic text parser) extracts from real RBC
monthly statements, and how well, compared with the VL parser (`imports/parsers_monthly_vl.py`,
Halogen on port 11435). Answer the open question in `docs/architecture.md` §6 ("a deterministic
text parser … with VL as the fallback, to be confirmed by the check of `imports/parsers_pdf.py`,
step 12") with evidence from the two real statements `inbox/check-1.pdf` (recent) and
`inbox/check-2.pdf` (old), both account TS_HUISGELD.

**This is a check only.** No behaviour change, no database writes, no migrations, no parser
changes. The only file added to the repo is the report `docs/audit/05-text-parser-check.md`.

## Hard rules (from the task)

- Run both parsers on both PDFs **directly** (helper script), never through
  `manage_inbox_import` or the watcher, with `portfolio_manager.settings_test` only.
- **Never print or write account numbers, people's names, amounts, quantities or prices** to the
  screen, logs, the report or a commit. Compare values in-process and print/store only: field
  names, counts, and per-field **match / differ / missing**. Dates (statement date) are allowed —
  they are not amounts or identifiers.
- Do not move, rename or delete the PDFs in `inbox/`.
- Helper script lives in `pm-logs/` (already gitignored, `.gitignore` line 53), not in the repo.

## What changes

| File | Change |
|---|---|
| `pm-logs/text_parser_check.py` | new helper script (untracked, never committed) |
| `docs/audit/05-text-parser-check.md` | new report (the only committed artifact) |

Nothing else. No code, no tests, no migrations touched.

## Out of scope

- Any change to `imports/parsers_pdf.py`, `imports/parsers_monthly_vl.py`, the watcher,
  `manage_inbox_import`, or any other parser/command.
- Wiring a text-first/VL-fallback path into the import pipeline (that is step 12b).
- The annual-statement parser path, transaction extraction quality, and the DB side of PDF
  imports (duplicate check etc. is step 13).
- Updating `docs/architecture.md` — the report carries the recommendation; tdw folds the
  decision in afterwards.

## Helper script design (`pm-logs/text_parser_check.py`)

- `DJANGO_SETTINGS_MODULE=portfolio_manager.settings_test`, `django.setup()`, then import both
  parsers. Text parser entry: `parse_rbc_pdf(path)` (returns `statement_date`,
  `last_statement_date`, `account_number`, `fx_rate`, `balances`, `transactions`,
  `income_summary`, `contribution_summary`; note `symbol` is hard-coded `''` at
  `parsers_pdf.py` L661). VL entry: `parse_rbc_monthly_vl(path)` (returns per-currency
  sections with `statement_date`, `currency`, `begin_total_market_value`,
  `end_total_market_value`, cash balances and `holdings` with symbols).
- For each (parser, file): run once, `time.perf_counter()` around the call, keep the result in
  process.
- Derive, per (parser, file), a **safe summary**: statement date; sorted list of currencies
  found; number of holdings; sorted list of holding symbols (VL only — the text parser has no
  per-security symbols); cash per currency (presence + count of cash rows only, never the
  value); total market value presence (begin/end found or not).
- Compare text vs VL per file on exactly these fields: **statement date, currency sections,
  number of holdings, symbols, cash per currency, total market value** → print `MATCH`,
  `DIFFER` or `MISSING` (+ which side is missing). Numeric comparisons (holdings count, cash
  presence, total-MV presence) are done in-process; only the verdict word is printed.
- **Holdings in the raw text layer** (per tdw's decision on open question 1): for each PDF,
  from the extracted raw text (not `parse_rbc_pdf`'s output), locate the holdings table
  (the "Asset Review" section with the SECURITY/SYMBOL … QUANTITY/SEGREGATED header) and count
  the holding rows found in the extracted text; compare that count against VL's total holdings
  count. Also report whether the symbol column and the quantity column are recognisable in the
  extracted text. Reported as **counts and yes/no only** — no values. This is the evidence for
  whether step 12b should add holdings to the text parser, with VL as the fallback.
- Also record, per file, whether the text parser found each expected section
  (Asset Summary / Cash Balance / Income Summary / Account Activity) — section names only. This
  is the evidence for old-vs-recent layout differences, together with whether the VL parser had
  to fall back to its regex path (check its log output for the fallback warning, not its data).
- The script must never `print()` a parser result dict; only the derived summary. No file
  output; the report is written by hand from the console verdicts.

## Steps (one commit each)

1. **Write the helper script** `pm-logs/text_parser_check.py` (untracked).
   Check: `/opt/pm-venv/bin/python -c "import ast; ast.parse(open('pm-logs/text_parser_check.py').read())"` and
   `git status --short` shows **no** new tracked files.

2. **Run the text parser on both PDFs** (fast, no model needed):
   `/opt/pm-venv/bin/python pm-logs/text_parser_check.py --text-only`.
   Check: exit code 0; console output contains only dates, counts, section names, currency
   codes (CAD/USD) and MATCH/DIFFER/MISSING — eyeball it for stray digits with
   `... | grep -E '\$|[0-9]{3}'` returning nothing except dates/counts you expect.

3. **Run both parsers and the comparison** (VL on Halogen, ~90 s per PDF):
   `/opt/pm-venv/bin/python pm-logs/text_parser_check.py`.
   Check: exit code 0 and six verdict lines (6 fields × 2 files) plus two timings per file,
   and per file the holdings readability lines (holding rows found in extracted text vs VL
   holdings count, symbol column yes/no, quantity column yes/no). If Halogen (port 11435)
   is unreachable — i.e. the VL regex fallback was used: stop (see Stop conditions).

4. **Write `docs/audit/05-text-parser-check.md`** with: what the text parser extracts and what
   it misses (field list, incl. the empty-symbol finding at L661); the comparison table (rows =
   the six fields, columns = check-1 / check-2, cells = match/differ/missing); timings per
   parser per file; the layout difference between the old and the recent statement (which
   sections each parser could find); the holdings-in-text-layer finding per PDF (holding rows
   found in extracted text vs VL holdings count, symbol column recognisable yes/no, quantity
   column recognisable yes/no — counts and yes/no only); the verdict on whether step 12b should
   add holdings to the text parser with VL as the fallback; and the overall recommendation
   (text-first or not — decided by this run, per tdw).
   Check: `grep -nE '[0-9]{8}|\$' docs/audit/05-text-parser-check.md` returns nothing;
   `git status --short` shows only this one new file. Commit:
   `docs: text-parser vs VL check on two RBC statements (step 12a)`.

5. **Tests once at the end** (nothing was changed, this proves the run left the tree clean):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`.
   Check: all pass; `git status --short` clean.

## Stop conditions

- Halogen on port 11435 unreachable, or the VL parser errors on both PDFs → stop and report;
  do **not** write a one-sided report.
- Any account number, name, amount, quantity or price appears on screen or would land in the
  report → stop, fix the script's output filter first.
- A PDF turns out not to be a monthly statement (e.g. annual) → stop and ask; do not substitute
  other files.
- The text parser raises on a PDF → that is a finding (record "parser error"), not a stop.

## Tests

Run once, at the end of step 5 only:

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

No new tests (check only, no code change).

## Decisions (tdw, Oct 26)

1. *Text first with VL as the fallback, or not?* — **Decide nothing yet; the run gives the
   verdict.** The report states it based on the step-12a evidence. In addition to the six-field
   comparison, the check also tests whether the holdings section is readable from the text
   layer: per PDF, the number of holding rows found in the extracted text versus VL's holdings
   count, and whether the symbol and quantity columns are recognisable — reported as counts
   and yes/no only, no values. The report then says whether step 12b should add holdings to the
   text parser, with VL as the fallback.
2. *Should the report also time the VL regex fallback?* — **Agreed: no.** No timing of the
   regex fallback; the report notes only whether it was used or not used.
