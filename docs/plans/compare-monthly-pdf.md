# Plan: compare-monthly-pdf — Phase 3 step 13a-2, read-only check of the old July imports

Branch: `chore/compare-monthly-pdf` (from `dev`). Follows step 12b (`docs/plans/pdf-text-holdings.md`):
the deterministic text parser `imports/parsers_pdf.py` with its self-check `self_check_sections()`
now exists; this step uses it to audit what the earlier (VL) monthly imports actually stored.

## Goal

Add a **read-only** management command `compare_monthly_pdf` that takes one or more monthly PDF
paths, parses each with the step 12 text parser and its self-check, finds the stored
`RbcMonthly` rows for the same account, statement date and currency, and compares them field by
field:

- `last_statement_date`
- `begin_total_market_value`, `end_total_market_value`
- cash per currency where stored (parsed `balances` entry whose `account_type` is Cash vs.
  stored `RbcMonthlyBalance` with `account_type="Cash"`, `closing_balance`)
- number of holdings
- per holding symbol: `quantity`, `book_cost`, `market_price`, `market_value`

Numeric money values are compared **to the cent** (`Decimal.quantize(Decimal("0.01"))`), never
"found on both sides"; `quantity` and `market_price` (4-dp columns) are compared exactly at 4 dp.
Output is one line per field and per holding with verdict `MATCH`, `DIFFER`, `ONLY_PDF` or
`ONLY_DB`, plus the number of stored rows found per (file, currency), and a totals line at
the end of each file (fields compared, MATCH, DIFFER, ONLY_PDF, ONLY_DB). The command never writes
to the database — a test proves the row counts are unchanged.

**Privacy (hard rule, as in step 12a):** output, logs, tests and commits contain only field
names, symbols, account **names**, the given file names, dates, counts and verdicts — never
amounts, quantities, prices, account numbers or any account digits. The parsed
`account_number` is used only to resolve the `Account` via `lookup_account()` (fingerprint) and
is never printed.

Pi has no route to mercury: tests use synthetic text fixtures and SQLite only; tdw runs the
command on mercury against the four check PDFs (`tests/check-1.pdf` … `check-4.pdf`).

## What changes

| File | Change |
|---|---|
| `core/management/commands/compare_monthly_pdf.py` | new command (the only production code added) |
| `core/tests/test_compare_monthly_pdf.py` | new tests with synthetic statement text |

Nothing else. **Out of scope:**

- No change to the importers, `imports/parsers_pdf.py`, `self_check_sections`,
  `dedupe_monthly_statements`, `manage_inbox_import`, the watcher, or any model.
- No migrations, no DB writes of any kind (no log rows, no `FileImportLog`).
- No fixing of anything the comparison finds — this is a check; findings feed later steps.
- No web UI, no wiring into the monthly check page (step 14).
- `fx_rate`, income/contribution summaries and `RbcMonthlyTransaction` are not compared.

## Command design

`manage.py compare_monthly_pdf <path> [<path> ...]` (settings from `--settings`, tests pass
`portfolio_manager.settings_test`).

1. **Read the file.** If the file's first bytes are `%PDF`, pass the path to the parser;
   otherwise read it as text and pass the **string** to the parser (step 12's
   `parse_rbc_pdf`/`parse_rbc_pdf_multi` accept raw text for exactly this purpose). This lets
   the tests use synthetic text fixtures without touching the parser.
2. **Parse** with `parse_rbc_pdf_multi()` → one section per currency; run
   `self_check_sections(sections, full_text)` and print its reasons verbatim (they already name
   only fields, currencies and symbols) or `SELF_CHECK OK`. A failed self-check does **not**
   stop the comparison — it is printed as a warning line.
3. **Resolve the account** per section via `lookup_account(parsed['account_number'])`
   (fingerprint lookup, `core/utils/account_fingerprint.py`). Unknown fingerprint → one line
   `UNKNOWN_ACCOUNT <file> <currency>` and move on. The number itself is never echoed.
4. **Find stored rows**: `RbcMonthly.objects.filter(account=…, statement_date=…, currency=…)`
   (all `file_name`s — the old July imports have their own safe names). Print
   `ROWS FOUND <n>` per (file, currency). `n == 0` → every field line is `ONLY_PDF`.
5. **Compare** each stored row (each line labels that stored row by its **id**, e.g.
   `[row 98]` — tdw decision Oct 27; not by `file_name`): the four scalar fields, cash,
   holding count, then per-symbol holding fields.
   Holdings are matched by `symbol` within the currency section; a symbol present on only one
   side yields `ONLY_PDF` / `ONLY_DB` lines for its four fields. Duplicate symbols on either
   side are matched in sorted order and flagged with a `DUP_SYMBOLS <symbol> <n_pdf> <n_db>`
   count line (counts only). Blank symbols are matched in sorted order among themselves.
6. **Output line format** (all tokens safe):
   `<given_file> <account_name> <CUR> <statement_date> [row <id>] <field>: <VERDICT>`, e.g.
   `check-1.pdf TS_HUISGELD CAD 2025-07-31 [row 98] end_total_market_value: DIFFER`
   `check-1.pdf TS_HUISGELD CAD 2025-07-31 [row 98] holding NVU.UN quantity: MATCH`
   With 0 stored rows the `[row <id>]` part is omitted. The statement date is allowed in
   output (Q2, tdw decision Oct 27).
7. **Totals line** at the end of each file, after all of its field lines:
   `TOTALS <given_file>: fields compared <n>, MATCH <n>, DIFFER <n>, ONLY_PDF <n>,
   ONLY_DB <n>` — counts only (tdw decision Oct 27). A skipped file (unknown account,
   missing file) gets a totals line with all zeros.
8. **Never writes.** No `save()`, `create()`, `update()`, `delete()` anywhere in the command.

## Steps (one commit each)

1. **Command skeleton** — args (one or more paths), file sniffing (PDF vs text), parse via
   `parse_rbc_pdf_multi`, self-check printing, account resolution, row lookup and
   `ROWS FOUND <n>`; comparison stubbed out.
   Check: `/opt/pm-venv/bin/python manage.py help compare_monthly_pdf --settings=portfolio_manager.settings_test`
   and `git status --short`.
2. **Field + holdings comparison** — scalar fields, cash, holding count, per-symbol fields,
   cent/4-dp quantised comparison, `MATCH/DIFFER/ONLY_PDF/ONLY_DB`, dup-symbol handling,
   and the per-file totals line.
   Check: `/opt/pm-venv/bin/python -m py_compile core/management/commands/compare_monthly_pdf.py`
   and `rg -n "create|save|update|delete" core/management/commands/compare_monthly_pdf.py`
   returns nothing.
3. **Tests** — `core/tests/test_compare_monthly_pdf.py` with synthetic statement text
   (fake accounts `90000001`–`90000008`, key from `settings_test.py`; build stored rows
   directly). Cases:
   - all fields and holdings equal → all `MATCH`, `ROWS FOUND 1`;
   - two stored rows for the same (account, statement date, currency) → both compared,
     each line labelled `[row <id>]` with that row's id, `ROWS FOUND 2`;
   - one stored value off by one cent → that field `DIFFER`, others `MATCH`;
   - stored row missing (no `RbcMonthly`) → `ROWS FOUND 0`, all fields `ONLY_PDF`;
   - holding only in PDF and only in DB → `ONLY_PDF` / `ONLY_DB` lines;
   - **read-only proof**: snapshot `count()` of `Account`, `RbcMonthly`,
     `RbcMonthlyBalance`, `RbcMonthlyHolding` before and after `call_command` → unchanged;
   - **totals line**: for each file the totals line's MATCH/DIFFER/ONLY_PDF/ONLY_DB counts
     equal the verdicts printed for that file;
   - **privacy**: every output line matches a safe pattern (no digits except dates, counts and
     the fixture symbols' own characters; the fixture amounts appear nowhere in stdout).
   Check: `/opt/pm-venv/bin/python -m pytest core/tests/test_compare_monthly_pdf.py --collect-only -q`.
4. **Full test run** (once, at the end):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   then `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`. All green → final commit.

## Stop conditions

- Any step needs a change to the importers, `imports/parsers_pdf.py`, its self-check,
  `dedupe_monthly_statements`, or any model → stop and report (explicitly out of scope).
- The command would need to write anything to the DB → stop.
- An output line would have to carry a value, an account number or account digits to be
  meaningful → stop and ask.
- A step turns out wrong or the plan is unclear → stop and report instead of guessing; the plan
  is revised first (per `docs/plans/README.md`).

## Migrations

None.

## Open questions

1. **Multiple stored rows** for the same (account, statement date, currency) — the old July
   imports may have left more than one `file_name`. *Recommended:* compare against **each**
   stored row and print the row count; that is exactly what the check needs to expose about
   the July imports. *Resolved (Decisions, tdw Oct 27):* yes — each line labels the row by
   its id, not its `file_name`.
2. **Statement dates in output.** The task bans values and account digits; step 12a allowed
   dates. *Recommended:* allow the statement date in output (it is not money or an identifier);
   it makes the per-file lines interpretable.
3. **Duplicate symbols within a currency** (e.g. option legs). *Recommended:* match in sorted
   order and emit a `DUP_SYMBOLS` count line; the check PDFs are ordinary holdings, so this is
   a safety net, not the main path.
4. **Failed self-check.** *Recommended:* print the reasons and still run the comparison — the
   command is read-only and the reasons are themselves a finding about the July imports.

## Decisions (tdw, Oct 27)

1. **Q1 — multiple stored rows:** yes — compare against each stored row; identify each row
   by its **id** (e.g. `row 98`), not by its `file_name`.
2. **Q2 — statement dates in output:** yes, as recommended.
3. **Q3 — duplicate symbols:** as recommended (sorted-order matching + `DUP_SYMBOLS` count
   line).
4. **Q4 — failed self-check:** yes, as recommended — print the reasons and still compare.

Also decided:
- When the account cannot be resolved, print `UNKNOWN_ACCOUNT` for that file and skip it.
- End each file with a totals line: fields compared, MATCH, DIFFER, ONLY_PDF, ONLY_DB.
