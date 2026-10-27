# Plan: Phase 3 step 13c-b — VL transactions with a cash-reconciliation gate (manage_inbox_import)

Branch: `feature/pdf-vl-transactions`. Implements tdw's decision after `docs/audit/06-text-activity-check.md`
(the text parser does not reproduce Account Activity; transactions stay VL-sourced).

## Goal

In `manage_inbox_import`, a monthly statement whose **text** parse passes the self-check keeps
holdings, cash and totals from the text parser (VL fallback unchanged when the self-check fails),
but its **Account Activity transactions always come from the VL parser**, matched to the text
currency sections by currency. Before anything is stored, a gate must pass **per currency
section**:

- stored opening cash + Σ(credits) − Σ(debits) == stored closing cash, to the cent — a naive
  sum over **all** VL transactions of that section, no activity type excluded;
- a section with no transactions must have equal opening and closing cash (same formula, empty
  sum);
- cash = the section's balance rows with `account_type` `cash` (case-insensitive), fields
  `opening` / `closing`; a section with no cash row fails the gate.

Any gate failure, a VL error (the VL call now happens on the text path too), or a currency
section that the text and VL parses do not both have (missing on either side, or a duplicated
currency) rejects the **whole file** to `processed/errors/` with verdict `reconcile_error` and
a reason naming **field names and currencies only** — no values, descriptions or account
digits — using the step 13b all-or-nothing path (no rows stored, `FileImportLog` error row,
JSON log with `_ERROR` suffix). A `--dry-run` computes the real verdict, including the VL call,
and writes nothing (no rows, no log rows, no JSON log, no move), as today.

The JSON log additionally records: the parser used for holdings and for transactions
separately, and per section the transaction count and the reconciliation verdict — names and
counts only in these new fields; descriptions stay scrubbed exactly as today.

## What changes

- `core/utils/import_checks.py` — new pure helper (no Django models):
  `merge_and_reconcile(text_sections, vl_sections) -> (sections, reasons)`.
  - Normalises both sides' `currency` with `.upper().strip()`; builds a currency → VL section
    map. A currency present on one side only, or duplicated on either side, is a reason
    (currency names only).
  - Returns copies of the text sections with `transactions` replaced by the matching VL
    section's transactions, and a list of reasons (empty = gate passed).
  - Gate per section (Decimal, quantised to 0.01): cash opening/closing summed over balance
    rows with `str(account_type).strip().lower() == 'cash'`; no cash row → reason naming the
    field (`balances`) and the currency; else `opening + Σcredit − Σdebit != closing` →
    reason naming the fields (`opening`, `credits`, `debits`, `closing`) and the currency.
    Reason strings never contain amounts, quantities, descriptions or digits beyond the
    currency code.
- `core/management/commands/manage_inbox_import.py` — `_process_monthly` only:
  - Text path (self-check passed): instead of `sec['transactions'] = []`, call
    `parse_rbc_monthly_vl(str(pdf_file))`. A raise or a `None` return → `reconcile_error`
    rejection (reason: field name `vl_parser` + error text as today). Otherwise run
    `merge_and_reconcile`; non-empty reasons → `reconcile_error` rejection; empty → the merged
    sections become `parsed_list` (pass 1/pass 2 storage unchanged — transactions are stored
    with the section currency as today).
  - New `reconcile_msgs` list alongside `conflict_msgs` / `date_msgs`; verdict priority:
    `conflict` > `date_error` > `reconcile_error` > `duplicate`/`imported`/`error`. The
    rejection path mirrors the conflict/date path: `_log_import(..., 'error', ...)`, return
    `verdict='reconcile_error'`, errors>0 moves the file to `errors/` (existing Step 4).
  - Dry run: the VL call and gate run before the dry-run branch, so the dry-run verdict
    includes `reconcile_error`; nothing extra is written.
  - JSON log (`json_data` for monthly): replace `'parser'` with `'parser_holdings'` and
    `'parser_transactions'` (text path: `text` / `vl`; VL fallback path: `vl` / `vl`); add to
    each entry of `json_sections` the keys `'txn_count'` (int) and `'reconcile'`
    (`'ok'` / `'failed'`). No other payload change — `_scrubbed_json` and description scrubbing
    stay as they are.
- `tests/test_import_checks.py` — unit tests for `merge_and_reconcile`.
- `imports/tests/test_monthly_txn_reconcile.py` — new command-level tests (pattern:
  `ManageInboxImportTextFirstTests` in `imports/tests/test_manage_inbox_import.py`: fake
  account `90000004` by fingerprint, `classify_pdf`/`parse_rbc_pdf_multi`/`parse_rbc_monthly_vl`
  patched in the command module, synthetic section dicts, `override_settings` for the dirs).
- `docs/IMPORT_COMMANDS.md` — one line for the new `reconcile_error` verdict if the file lists
  verdicts.

## Migrations

None. No model changes.

## Out of scope

- The text parser (`imports/parsers_pdf.py`) — no change.
- The annual path (`_process_annual`), `dedupe_monthly_statements`, `compare_monthly_pdf`.
- The VL fallback path behaviour (self-check failed → all-VL sections, verdicts as today, no
  gate — see Open questions).
- `import_csv`, `watch_inbox` beyond reusing this command, scripts, UI.

## Steps

1. **Helper + unit tests.** Add `merge_and_reconcile` to `core/utils/import_checks.py` and
   unit tests in `tests/test_import_checks.py`: reconciles; off by one cent; no-transaction
   section with equal/unequal cash; missing cash row; currency missing on one side; duplicated
   currency; lowercase currency matched.
   Check: `/opt/pm-venv/bin/python -m pytest tests/test_import_checks.py -q`. Commit.
2. **Wire into `_process_monthly`.** VL call on the text path, merge, gate, `reconcile_msgs`,
   `reconcile_error` verdict (real run and dry run), JSON log fields `parser_holdings` /
   `parser_transactions` and per-section `txn_count` / `reconcile`. Update the existing
   text-first tests in `imports/tests/test_manage_inbox_import.py` whose VL mocks must now
   reconcile (test fixtures only).
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_manage_inbox_import.py -q` and
   `git grep -n "reconcile_error" core/management/commands/manage_inbox_import.py`. Commit.
3. **Integration tests** in `imports/tests/test_monthly_txn_reconcile.py` (synthetic text
   fixtures + mocked VL, SQLite):
   - reconciles → stored: `RbcMonthly`, balances/holdings from text, transactions from VL;
     JSON log verdict `imported`, parsers and per-section counts recorded;
   - off by one cent → `errors/`, verdict `reconcile_error`, reason names fields + currency
     only, nothing stored (no `RbcMonthly` rows), `FileImportLog` status `error`;
   - CAD + USD sections matched by currency, both reconcile → stored;
   - VL raises → rejected `reconcile_error`, nothing stored;
   - a currency section on one side only (text CAD+USD, VL CAD only, and the reverse) →
     rejected;
   - `--dry-run` on a fully reconciling file: verdict `imported` printed, no rows, no
     `FileImportLog`, no JSON log, file still in the inbox dir.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/test_monthly_txn_reconcile.py -q`.
   Commit.
4. **Docs.** Note the `reconcile_error` verdict in `docs/IMPORT_COMMANDS.md` (only if it lists
   verdicts). Check: `rg -n "reconcile_error" docs/IMPORT_COMMANDS.md`. Commit.
5. **Full tests, once:**
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   and `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`. Fix fallout, commit.

## Stop conditions

- A change to the text parser, the annual path, `dedupe_monthly_statements` or
  `compare_monthly_pdf` looks necessary → stop and ask (out of scope).
- A migration seems needed → stop (none expected).
- The VL fallback path turns out to need the gate to make the flow coherent → stop and ask
  (Open question 1).
- A step of this plan turns out wrong → stop; the plan is revised first, not silently changed.
- `makemigrations --check --settings=portfolio_manager.settings_test` reports changes → stop.

## Open questions

1. **Does the gate also apply to the VL fallback path (self-check failed)?**
   Recommendation: **no** — "VL fallback as today"; the gate is the text path's cross-check of
   VL transactions against text cash. Audit 06 showed VL reconciles against its own numbers on
   all six sections, so gating it would add rejections without new information.
2. **Verdict priority when several failures coexist** (e.g. a conflict and a reconcile
   failure). Recommendation: `conflict` > `date_error` > `reconcile_error` >
   `duplicate`/`imported`/`error` — the file-level identity/date problems are the more
   actionable reason; all reasons still appear in `error_messages`.
3. **Scope of "names and counts only, never values"** for the JSON log. Recommendation: it
   governs the **new** fields (parsers, `txn_count`, `reconcile`, reasons); the existing
   section payload stays as today (it is a local file; descriptions scrubbed, account numbers
   labelled) — "descriptions stay scrubbed as today" implies the payload itself is unchanged.
4. **Multiple or missing cash rows in a section.** Recommendation: sum every balance row whose
   `account_type` lowercases to `cash` (mirrors `_section_cash` in `imports/parsers_pdf.py`);
   a section with no such row fails the gate with a reason naming `balances` and the currency.
5. **Existing text-first tests** must now provide a reconciling VL mock (the text path always
   calls VL). Recommendation: update those fixtures in step 2 — test-only change, allowed.
