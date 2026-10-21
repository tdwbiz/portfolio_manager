# Audit 4 of 4 — Account numbers and personal data

Scope: every place real account numbers (and owner names) can appear or leak — settings/`.env`, database, logs and command output, file names, UI/URLs/admin/exports, tests/fixtures, `.gitignore`, and git history. Method: grep + `git log -G`/`-S` + targeted reads; `.env` was never read or printed (field names only). **Per the strict rule for this audit, no real-looking account number — not even its last 4 digits — appears anywhere in this document; only file paths, line numbers, field names and masked format sketches (`###-#####`). The fake test accounts `90000001`–`90000008` are the only numbers named.**

Prior audits' Decisions respected here: risk module, ledger and reconciliation are **parked** (their scripts are listed but not treated as urgent); `events.jsonl` is already scheduled for deletion (audit 3); `docs/project-history.md` stays as it is (audit 3).

---

## 1. Where the numbers legitimately live

| Place | What | Notes |
|---|---|---|
| `.env` (never in repo) | `RBC_ACCOUNTS` (`id:label` pairs), `RBC_DEFAULT_ACCOUNT`, plus DB credentials, secret key | Parsed in `portfolio_manager/settings.py:238–253`. Pi's throwaway `.env` has no `RBC_ACCOUNTS` at all. |
| mercury DB | `core_account.account_id`, `name`, `owner`; `core_rbcmonthly.account_number`; `core_rbcannual.account_number` + `account_type_code`; `core_ledger_account.account_id_str`/`account_name`; string copies in risk tables (`rm_urgency_score.account_id`, `rm_strategy_recommendation.account_id`/`owner`, `rm_backtest_run`, `rm_alert_config`, `rm_audit_*`); `core_position_history.owner`; `rm_owner_config` (owner + tax rates); `rm_alert_config.notify_email` | From audit 1. This is the intended home. |
| Free-text DB fields that can embed them | `core_transaction.notes`, `core_rbcmonthlytransaction.description`, `core_brokerstatement.raw_filename`, `core_fileimportlog.filename` | Audit 1 "free text" flag. |
| Source files on disk | RBC PDF/CSV file **names** carry the full account number (`Statement-#### ####-##-##_…pdf`) or the last 4 digits (`Annual Report-#### ….pdf`); Holdings CSV names carry the account (`imports/parsers_holdings.py:150–155` extracts it from the name) | `inbox/`, `processed/` — gitignored, but see §2.4. |
| Owner names | `Account.owner` etc. In the repo and docs the pseudonyms `TOM_*` / `SUSAN_*` / `TS_*` are used (e.g. `docs/project-history.md:113–120`). Commit `cb95e967`'s message confirms **real owner names existed in the repo** before Oct 14 and were replaced with the mapped pseudonyms. | Real names belong in mercury only. |

## 2. Leak points found

Severity: **High** = real data reachable outside the intended stores now; **Med** = real data written to a secondary place (local, gitignored) or unverifiable fake; **Low** = by-design display in the private app.

### 2.1 Git history — real account numbers and owner names remain in old blobs — **High**

Commits `cb95e967` (2026-10-14, "Remove real RBC account numbers from repo…") and `a0cb62a9` (2026-10-15, "Replace real account last-4 digits in statement file-name examples with fakes") scrubbed the **tip**, but the pre-cleanup blobs remain in history, and `dev` is mirrored to GitHub (AGENTS.md). Files whose **historical versions** contain real-format numbers (dash form `###-#####`/`###-######` or plain 8-digit; commit → file, numbers not reproduced):

- `docs/rbc_pdf_coverage_report.md` (account table + "Auto-created from PDF: …" names) — also `docs/audit-investigation-sql.md`
- `docs/TEXT_PARSER_IMPLEMENTATION.md`, `docs/project-history.md` and the deleted `global_agents.md` (statement file-name examples incl. last-4 digits)
- `imports/parsers_annual.py`, `imports/parsers_pdf.py`, `imports/parsers_monthly_vl.py`, `imports/parsers_annual_vl.py` (docstring/sample examples)
- `core/models.py` and migrations `0015`, `0017` (field `help_text` examples), `portfolio_manager/settings_test.py`, `.env.example` (first version, `d4964dda`)
- tests: `imports/tests/test_annual_report_parser.py`, `imports/tests/test_parsers_pdf.py`, `tests/test_vl_parsers.py`, `tests/test_vl_config.py`
- `core/management/commands/manage_inbox_import.py` (docstring example), `specs/PhaseA/21_*`, `22_*`, `specs/PhaseC/C4-*` and the PhaseB/C review docs

Key commits: `cb95e967`, `a0cb62a9`, plus the first-appearance commits `32c616d0` (initial), `71034a37`, `055694a8`, `a4bc3ffd`, `8e586cc4`, `4f8991ea`, `156095aa`, `d31f7840`.

Note: account numbers are not credentials (read-only exposure, low entropy), but number + owner name + bank = a phishing/social-engineering profile. Fix options in §4, batch A — history rewrite is tdw's call (Pi must not rewrite history).

### 2.2 Real-format numbers still in HEAD (unverifiable) — **Med, ask tdw**

- `imports/tests/test_parsers_pdf.py` — a `###-#####` sample "Your Account Number" present **since the initial commit** `32c616d0`; pattern is not obviously synthetic and cannot be checked against the real list from here.
- `tests/test_vl_parsers.py` — a `######-######` "Account No:" fixture (2 occurrences); appears nowhere else in history, likely synthetic, but unverifiable.

All other 8-digit/dash numbers in HEAD resolve to fakes (`90000001`–`8`, `900-0000x`), dummies (`12345678`, `12345-678901`), HSTS seconds, spec sample volumes or test volumes. `events.jsonl` and `login_test.html` contain **no** account-format numbers (clean; `events.jsonl` is deleted in audit-3 batch 2 anyway).

### 2.3 Code that logs/prints account numbers — **Med**

Real numbers reach `logs/` (gitignored, but logs get pasted into AI chats and bug reports — see §4 batch F):

| Site | What leaks |
|---|---|
| `core/management/commands/manage_inbox_import.py:575` | `logger.warning(... %r, account_number)` — full unrecognised number; also builds account **name** `"Auto-created from PDF: <number>"` (line 569) — the number is duplicated into a second DB field |
| `core/management/commands/manage_annual_report_import.py:85` | same pattern (command is delete-listed per audit 2) |
| `imports/auto_import.py` (watcher CSV path) | logs each parsed row **dict** at INFO (includes the account column) and the `imports.duplicates` logger writes the whole CSV line — account included — into `logs/` (confirmed format in local `logs/portfolio_manager.log`) |
| `imports/parsers.py:213, 622, 634` | debug logs quote the raw account id (unknown/practice rows) |
| `core/views_transactions.py:59` | logs the `account_id` query-param of a bad filter request |
| `core/views_transactions.py:150` (and Excel twin) | logs export filters — includes an account number when filtering by account |
| `imports/classify_pdf_vl.py:121`, `imports/watcher.py:57` | log the full **file path**; RBC file names embed the number (§2.4) |
| Parked scripts (audit 3): `scripts/audit_holdings_data.py`, `reconcile_tx.py:349`, `reconcile_holdings.py:282`, `pdf_internal_reconciliation.py:366`, `dump_chains.py:34`, `check_db_state.py`, `fix_open_positions.py`, `verify_ledger_models.py`, `generate_accounting_ledger.py:307`, `ledger_high_change.py` | print `account.account_id` to stdout and into `docs/reconciliation/*.md` reports (gitignored) |

The local `logs/portfolio_manager.log` in this working copy contains **no** account numbers (the only 8-digit hits are money amounts) — but the paths above would write them on real imports.

### 2.4 File names — **Med**

- RBC export names carry the full number or last 4 (`Statement-…` / `Annual Report-…`); Holdings names carry the account (`imports/parsers_holdings.py:150–155`; `imports/parsers_annual.py` has a filename fallback too).
- Importers **keep the original name everywhere**: `BrokerStatement.raw_filename` (shown in admin `list_display`, `core/admin.py:66`), `FileImportLog.filename` (shown in `dashboard.html:263`, `import_history.html:28`), files moved to `processed/` and `processed/errors/` unchanged.
- `processed/json_logs/*.json`: file name = PDF stem (⇒ contains the number) and content contains `filename` plus the full VL parse payload including `account_number` (`manage_inbox_import.py:162–170, 196–209`; VL schema field in `imports/parsers_monthly_vl.py`). Gitignored, but these JSONs are exactly what gets attached to AI chats when debugging the VL parser.
- Rejection logs (`imports/rejection_log.py`, `IMPORT_LOGS_DIR` → `logs/`) have an explicit "Account ID" column.

### 2.5 UI / URLs / admin / exports — **Low** (private single-user app) but unmasked everywhere

- **Nothing masks account numbers anywhere in the codebase** (no mask/redact helper exists).
- Templates render `account.account_id` in full: `dashboard.html:150,223`, `accounts.html:60`, `transaction_list.html:40,101`, `realised_pnl.html:36–37`, `generate_statements.html`, `generate_annual.html`, `reconciliation.html`, `ledger_month.html:3–23`, `pdf_import.html:52` (`account_number`).
- **URLs carry the real number**: `core/urls.py:104–115` (`accounts/<str:account_id>/…`, `ledger/<str:account_id>/…`), `risk/urls.py:10,25` (parked). Numbers land in browser history, any proxy/access log, and screenshots.
- Admin: `core/admin.py` — `list_display`/`search_fields` expose `account_id`, `account_number`, `owner`, `raw_filename` (admin is the one place full visibility is legitimate).
- Exports: `transaction_export_csv` / `_excel` include `account.account_id` as a column.

### 2.6 Tests and fixtures — **Low**

`portfolio_manager/settings_test.py` defines fakes `90000001`–`90000008` (labels `ACCOUNT_A`–`H`); test e-mails are `*@test.com`; `.env.example` uses fakes. Exceptions: the two unverifiable fixtures in §2.2.

### 2.7 `.gitignore` coverage — **good**

Covered: `.env` (+ variants), `inbox/`, `processed/` (⇒ `processed/json_logs/`), `logs/`, `media/`, `*.log`, `docs/reconciliation/`, `docs/examples/`, `RECONCILIATION_REPORT.md`, run-output txt files, `db*.sqlite3`, `specs/**/*.pdf`. No gaps found for this audit. Residual: tracked junk (`events.jsonl` — clean, deletion already decided; `login_test.html` — clean).

## 3. Current protections (summary)

1. Real numbers only in `.env` + mercury; `.env` gitignored and never printed; `.env.example`/`settings_test.py` use fakes.
2. `RBC_ACCOUNTS` whitelist in `imports/parsers.py` rejects unknown accounts at CSV import.
3. Oct 2026 cleanup (`cb95e967`, `a0cb62a9`) scrubbed the working tree: code/docs/tests now use the fake↔real mapping (`90000001`–`8`, `TOM_*`/`SUSAN_*` pseudonyms).
4. All runtime artifacts that receive real data (inbox, processed, json_logs, logs, docs/reconciliation, media) are gitignored.
5. VL model is local (ports 11435/11438) — statement content never leaves saturn via the parser.
6. `docs/reconciliation/` reports (which contain numbers) are local-only.

## 4. Proposed design (small batches, one branch each)

**Batch A — `pi/history-exposure-decision`** (tdw decides first; no code).
Options: (a) accept — GitHub repo private and stays private, numbers are not credentials; (b) `git filter-repo` purge of the listed blobs + force-push + every clone re-cloned (violates "do not rewrite history" for Pi, so tdw executes or approves); (c) middle path: keep history, make the GitHub repo private, and rely on the tip scrub. Recommend (c) minimum regardless.

**Batch B — `pi/masking-helper`**: add `core/utils/masking.py` with `mask_account(value)` (returns `•••-•••<last-4>` or full dots — see Q2) that recognises all formats (`########`, `###-#####`, `###-#####-#-#`). Pure function + unit tests with fakes only.

**Batch C — `pi/log-filter`**: a `logging.Filter` (in `core/utils/masking.py`) that regex-scrubs every configured `settings.RBC_ACCOUNTS` id (and dash variants) out of every log record, installed on all handlers in `settings.LOGGING`. This makes §2.3 safe even when individual call sites are missed. Then fix the call sites: `manage_inbox_import.py:575` and `views_transactions.py:59,150` use `mask_account`; the `auto_import` row-dump and duplicates row-dump drop/mask the account column; `classify_pdf_vl`/`watcher` log a sanitized name (see batch D). (The `manage_annual_report_import.py` site disappears with audit-2 batch 1.)

**Batch D — `pi/safe-import-filenames`**: on import, when moving a file to `processed/`, rename it to `<statement-date>_<type>_<masked-id>_<hash8>.<ext>` (e.g. `2026-07-31_monthly_••••1234_a1b2c3d4.pdf`); `raw_filename`/`FileImportLog.filename` store the safe name, the original name goes in a new `original_filename` DB column only (mercury). `processed/json_logs` files get the same safe stem and `account_number` is masked in the JSON payload (the parsed DB rows keep the real value).

**Batch E — `pi/internal-ids-in-urls`**: switch `accounts/<str:account_id>/…` and `ledger/<str:account_id>/…` to the numeric `Account.pk` (already the internal id used in `docs/project-history.md` "Account DB ids"), templates pass `account.pk`; keep a redirect from the old number-based URL for bookmarks. Risk URLs: parked module — just note it in the parked labels.

**Batch F — `pi/ai-paste-rules`**: add a short "Sharing data with a cloud AI" section to `AGENTS.md`: never paste account numbers (not even last-4), owner real names, `.env`, log lines, `docs/reconciliation/` content, statement files or VL JSON output; quote file + line instead of data rows; use `90000001`–`90000008` and the TOM/SUSAN pseudonyms in examples; local models (11435/11438) are exempt because they have no internet path.

Deliberally **not** proposed: masking in the authenticated UI or admin (single-user private app — masking there is friction without benefit; see Q3), and masking inside the mercury DB.

## 5. Questions for tdw

1. Is the GitHub repo private, and which option in batch A (accept / rewrite / middle path)? If rewrite: who runs it (Pi cannot force-push)?
2. Two test fixtures contain real-format numbers that cannot be verified as fake from here (§2.2): `imports/tests/test_parsers_pdf.py` (since the initial commit) and `tests/test_vl_parsers.py`. Synthetic, or real? If real, replace with `900-00005`-style fakes.
3. UI policy: OK to keep full numbers in the authenticated UI/admin/exports (private app) and mask only logs, file names and shared artifacts (batches C–F)? Or do you want masked display in the UI too?
4. Mask format: last-4 visible (`•••-•••1234`) or fully dotted? (Last-4 is standard and enough to tell accounts apart, but this audit was told not to even repeat last-4 — your call for your own app.)
5. `Account.name` "Auto-created from PDF: <number>" duplicates the number into a second field — switch auto-created names to the masked form?
6. Exports: transaction CSV/Excel keep `account_id` (needed for your own reconciliation) or add a masked variant for sharing?
7. Confirm the real owner names never appear in any current tracked file (the tip scrub used pseudonyms; only history holds the originals — batch A covers that).
8. `processed/json_logs` are the main VL-debugging artifact — is masking `account_number` there (batch D) acceptable for your debugging workflow?
## Decisions (tdw, Oct 21 2026)

- **Q1: git history: middle path.**
  - Keep the history as it is (no rewrite, no force-push);
  - the GitHub repo **stays private** (recreated private on Oct 14 2026);
  - rely on the clean-up of the current files (`cb95e967`, `a0cb62a9`).

  Batch A needs no code; this decision closes it.
- **Q2: the two test fixtures are fake** (`imports/tests/test_parsers_pdf.py`, `tests/test_vl_parsers.py`), so they stay.
  - Optional, in batch C: replace them with obviously fake `9000000x`-style numbers, so future audits don't flag them again.
- **Q3: the UI keeps full numbers.** The authenticated UI, admin and exports show full account numbers (a private single-user app). Masking applies only to logs, file names and shared artifacts (batches C–F).
- **Q4: mask with the label, not with digits.**
  - Wherever an account number must not appear (logs, file names, JSON logs, anything shared), show the account's **label from `RBC_ACCOUNTS`** (e.g. `TOM_TFSA`) instead of the number, not even the last 4 digits.
  - For a number that isn't in `RBC_ACCOUNTS`: `UNKNOWN_ACCOUNT`.
  - `mask_account()` (batch B) does exactly this.
- **Q5: auto-created accounts** get the label, or `"Auto-created from PDF: UNKNOWN_ACCOUNT"`, never the number in `Account.name`.
- **Q6: exports keep the full number** (tdw's own use).
- **Q7: real owner names:** none in the current files, as far as known; the pseudonyms (`TOM_*`, `SUSAN_*`, `TS_*`) are used. The originals exist only in git history, which Q1 accepts.
- **Q8: `processed/json_logs`:** the label instead of the number, in the file name and in the payload. The parsed database rows keep the real value.

### Batches that follow from this

- **A:** closed by Q1.
- **B:** `mask_account()` returns the `RBC_ACCOUNTS` label, or `UNKNOWN_ACCOUNT`.
- **C:** the log filter replaces every `RBC_ACCOUNTS` number (all formats) with its label, then fixes the listed call sites. Optional: the Q2 fixture swap.
- **D:** safe file names with the label instead of the number, e.g. `2026-07-31_monthly_TOM_TFSA_a1b2c3d4.pdf`. The original name is kept only in a new `original_filename` database column (mercury).
- **E:** internal ids (`Account.pk`) in URLs instead of account numbers.
- **F:** the "Sharing data with a cloud AI" rule in `AGENTS.md`.
