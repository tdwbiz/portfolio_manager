# Plan: account fingerprints and last 4 (phase 2, step 8)

Branch: `feature/account-fingerprints` (from `dev`). Design: `docs/architecture.md` §5 and §12 step 8.

## Goal

`core_account` gets a **fingerprint** (HMAC-SHA256 of the normalised full account number, keyed by a new
`ACCOUNT_FINGERPRINT_KEY` env var) and the **last 4 digits** of the number. A one-off command lets tdw enter a
number once at a hidden prompt; the number is never printed, logged or stored. The importers
(`import_csv`, `import_csv --holdings`, `manage_inbox_import` and the parsers they call) resolve accounts by
fingerprint and reject unknown fingerprints instead of using the `RBC_ACCOUNTS` whitelist.
`RBC_ACCOUNTS` / `RBC_DEFAULT_ACCOUNT` are removed from code, tests, `.env.example` and current docs.

## What changes (files)

- **`core/utils/account_fingerprint.py` (new)** — the one small module:
  - `normalize_account_number(raw) -> str`: strip everything that is not a letter or digit, upper-case;
    if the result is all digits and has ≥ 8 digits, keep the first 8 (same rule as
    `_normalize_rbc_account_id` in `manage_inbox_import` and `auto_import`, so `900-00003-1-8`,
    `9000000318` and `90000003` all normalise to `90000003`).
  - `fingerprint_account_number(raw) -> str`: hex HMAC-SHA256 of the normalised number, key
    `settings.ACCOUNT_FINGERPRINT_KEY`; raise `ValueError` if the key is empty or shorter than 32 characters.
  - `last4(raw) -> str`: last 4 characters of the normalised number.
  - `lookup_account(raw) -> Account | None`: `Account.objects.filter(fingerprint=...).first()`.
- **`portfolio_manager/settings.py`** — add `ACCOUNT_FINGERPRINT_KEY = os.getenv("ACCOUNT_FINGERPRINT_KEY", "").strip()`;
  delete `_parse_rbc_accounts()`, `RBC_ACCOUNTS` and `RBC_DEFAULT_ACCOUNT` (lines ~239–253).
- **`portfolio_manager/settings_test.py`** — add fixed test key `ACCOUNT_FINGERPRINT_KEY = "test-fingerprint-key-0123456789ab"` (33 chars, ≥ 32);
  delete `RBC_ACCOUNTS` and `RBC_DEFAULT_ACCOUNT`.
- **`.env.example`** — delete the `RBC_ACCOUNTS` and `RBC_DEFAULT_ACCOUNT` blocks; add
  `ACCOUNT_FINGERPRINT_KEY=` with a placeholder comment only (never a real key).
- **`core/models.py`** — `Account` gets `fingerprint = CharField(max_length=64, unique=True, null=True, blank=True)`
  and `last_4 = CharField(max_length=4, blank=True, default="")`. Nullable so existing rows migrate cleanly.
- **`core/migrations/0041_account_fingerprint.py` (new)** — two `AddField`s only; `atomic = True`,
  reversible (run `migrate 0040` to undo).
- **`core/management/commands/set_account_fingerprint.py` (new)** — one-off: prompts for the account
  **name** (visible `input()`) and the account **number twice** (`getpass.getpass()`, hidden); stops if the
  two entries differ; creates the `Account` if the name doesn't exist yet (`broker='RBC'`), else updates it;
  stores only `fingerprint` and `last_4`. Never echoes, logs or stores the number; afterwards prints only the
  account **name** and the **last 4** (e.g. "Stored fingerprint and last 4 for TOM_TFSA (…0001)").
  Refuses to run without a valid `ACCOUNT_FINGERPRINT_KEY` (empty or < 32 chars → error, non-zero exit).
  Refuses to overwrite an existing fingerprint unless `--replace` is given.
- **`imports/parsers.py`** — replace `valid_accounts()` (the `settings.RBC_ACCOUNTS` whitelist, ~line 416)
  with a fingerprint check: normalise + fingerprint the row's account id and test
  `Account.objects.filter(fingerprint=...).exists()`; unknown fingerprint → rejected row (same rejection-log
  path as today, reason "Unknown account"). The `PRACEF` skip stays before this check.
- **`imports/auto_import.py`** — replace the `Account.objects.get(account_id=normalized_id)` lookup (~line 156)
  with `lookup_account(raw_account_id)`; unknown fingerprint → skip/reject as today. Do **not** change
  `compute_tx_hash` inputs (see Open questions).
- **`imports/parsers_holdings.py`** — `import_holdings_snapshot` builds its `valid_accounts` map from
  `Account.objects.filter(broker='RBC')` by `account_id`; rebuild it keyed by fingerprint and resolve rows
  via `lookup_account`; unknown fingerprint → skip with a warning (as today).
- **`core/management/commands/manage_inbox_import.py`** — `_get_or_create_account()` (~line 550): look up by
  fingerprint only; **no auto-create** — unknown fingerprint rejects the file (existing error-log/rejection path,
  `*_ERROR.json`). `_normalize_rbc_account_id` may now delegate to the shared module.
- **`core/management/commands/merge_duplicate_accounts.py`** — `CANONICAL_IDS = set(settings.RBC_ACCOUNTS)` →
  required `--canonical` argument (comma list of account pks).
- **Parked scripts** (`scripts/generate_accounting_ledger.py`, `ledger_high_change.py`, `reconcile_holdings.py`,
  `reconcile_tx.py`, `pdf_internal_reconciliation.py`, `verify_pdf_integrity.py`) — drop the
  `settings.RBC_DEFAULT_ACCOUNT` / `settings.RBC_ACCOUNTS` references: make `--account` a required argument;
  `generate_accounting_ledger` iterates `Account.objects.filter(broker='RBC')` instead. Scripts stay parked;
  this only keeps them importable.
- **`AGENTS.md`** — remove the "RBC account configuration (env-driven)" section and the whitelist mentions
  (CSV parser whitelist row, `--account` defaults); note the fingerprint lookup and `ACCOUNT_FINGERPRINT_KEY`.
- **Tests** — `core/tests/test_account_fingerprint.py` (new: normalisation cases, HMAC determinism, wrong key
  differs, empty key raises, `lookup_account`); update fixtures in `core/tests/test_csv_import.py`,
  `imports/tests/test_import_csv_command.py`, `imports/tests/test_csv_parsers.py`,
  `tests/e2e/test_user_workflows.py`: create fake accounts `90000001`–`90000008` as `Account` rows with
  `fingerprint=fingerprint_account_number('90000001')`, `last_4='0001'`, `account_id='ACCOUNT_A'` …
  (key from `settings_test`); add a case: row with an unknown number is rejected.

## Out of scope

- Step 9: names on screens/reports/exports, last-4 in templates, integer ids in URLs. No view/template changes.
- Step 10: dropping the other number columns (`RbcMonthly.account_number`, `RbcAnnualReport.account_number`,
  `core_account.account_id` cleanup) and scrubbing stored free text. **Step-10 note (from tdw):** the
  transaction hash includes the plain account number, and with the other row fields it can be brute-forced,
  so in step 10 recompute all transaction hashes from the account id instead of the number, if all other
  hash inputs are stored in the row.
- Step 11: the logging filter and safe `processed/` file names.
- IB import; the log filter; anything in `processed/`.
- Historical documents (`docs/audit/*`, old plan files like `docs/plans/vl-halogen.md`,
  `docs/plans/repo-hygiene.md`, `docs/project-history.md`) keep their `RBC_ACCOUNTS` mentions as the record.

## Steps (one commit each)

1. **Fingerprint module + settings + .env.example.** Add `core/utils/account_fingerprint.py`, the
   `ACCOUNT_FINGERPRINT_KEY` line in `settings.py` and `settings_test.py`, placeholder in `.env.example`.
   Check: `/opt/pm-venv/bin/python -m pytest --collect-only -q tests/ imports/tests/` and
   `git status` (`.env` untouched).
2. **Model fields + migration.** Add the two fields, `makemigrations`.
   Check: `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test`
   (clean) and that the new migration exists; `migrate 0040` then `migrate` once on a scratch SQLite DB to
   prove reversibility.
3. **One-off command.** `set_account_fingerprint` with `getpass`, plus its unit test (patch `getpass.getpass`).
   Check: `/opt/pm-venv/bin/python manage.py set_account_fingerprint --help`.
4. **Importer lookups by fingerprint.** `imports/parsers.py`, `imports/auto_import.py`,
   `imports/parsers_holdings.py`, `manage_inbox_import.py`.
   Check: `/opt/pm-venv/bin/python -m pytest imports/tests/ -q -x` (fixture updates may be needed here already;
   that is fine, commit them with this step).
5. **Remove RBC_ACCOUNTS everywhere else.** `settings.py`, `settings_test.py`, `.env.example`,
   `merge_duplicate_accounts.py`, the six parked scripts, `AGENTS.md`.
   Check: `git grep -l "RBC_ACCOUNTS\|RBC_DEFAULT_ACCOUNT"` → only `docs/audit/*`, `docs/architecture.md`,
   old plan files and this plan remain.
6. **Test fixtures + new tests.** Finish the fixture conversions and add `core/tests/test_account_fingerprint.py`.
   Check: `/opt/pm-venv/bin/python -m pytest --collect-only -q tests/ imports/tests/`.
7. **Full test run** (only here at the end):
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   Fix fallout in this commit; then push and open the PR.

## Stop conditions

- Stop if any step needs a package that isn't in `/opt/pm-venv` (nothing here should).
- Stop if a test fixture cannot be converted without changing what it asserts (report which test).
- Stop if `makemigrations --check` is not clean after step 2, or the migration is not reversible.
- Stop if any code path still needs the raw number after step 4 (means a lookup was missed) — list the sites.
- Never print, log or commit `.env` content or a real account number at any point.
- If a step turns out wrong, stop and report; the plan is revised first (docs/plans/README.md).

## After merge — what tdw must do on mercury

1. Set `ACCOUNT_FINGERPRINT_KEY` in `.env` on saturn (generate one, store a copy in the password manager).
2. SSMS check: `SELECT account_id, name FROM core_account;` — note which `account_id` values are raw numbers.
3. `migrate --plan` then `migrate` against the mercury DB (additive migration; safe).
4. Run `manage.py set_account_fingerprint` once per account, entering the number from a statement.
5. Verify: `SELECT name, last_4 FROM core_account;` shows a fingerprint/last-4 for every account.

## Decisions (tdw, Oct 25)

Answers to the open questions below:

1. **Yes** — keep `core_account.account_id` for now. tdw renames the number-valued `account_id`s in step 9;
   the column itself goes in step 10.
2. **Yes** — leave `compute_tx_hash` unchanged in step 8. See the step-10 note under Out of scope: the hash
   includes the plain number and, with the other row fields, can be brute-forced, so in step 10 recompute all
   transaction hashes from the account id instead of the number (if all other hash inputs are stored in the row).
3. **Yes** — fail loudly at the point of use (module `ValueError` → import command error), including in the
   one-off command, and reject a key shorter than 32 characters.
4. **Yes** — historical docs keep their mentions as dated records.
   Extra for the one-off command: ask for the number twice, both hidden, and stop if they differ; afterwards
   print only the account name and the last 4; refuse to overwrite an existing fingerprint unless `--replace`
   is given.

## Open questions (answered — see Decisions above)

1. **`core_account.account_id` already holds full account numbers** (auto-created RBC rows have `account_id`
   like `90000003`; other rows hold names like `TOM_TFSA`). *Decision: yes — leave the column in place for
   step 8; tdw renames the number values in step 9 and the column goes in step 10.*
2. **`compute_tx_hash` takes the normalised account number as input.** *Decision: yes — leave it unchanged in
   step 8 (switching to the fingerprint would change every hash and break dedup against existing `Transaction`
   rows). Step-10 note: the hash includes the plain number and can be brute-forced with the other row fields,
   so step 10 recomputes all transaction hashes from the account id instead of the number, if all other hash
   inputs are stored in the row.*
3. **Empty `ACCOUNT_FINGERPRINT_KEY` in production.** *Decision: yes — fail loudly at the point of use
   (`ValueError` from the module → import command error), also in the one-off command, not at Django startup,
   so pages keep working before tdw has set the key; and reject a key shorter than 32 characters.*
4. **Historical docs mentioning `RBC_ACCOUNTS`** (audits, old plans, `project-history.md`). *Decision: yes —
   leave them; they are dated records. Only `AGENTS.md` and `.env.example` are updated.*
