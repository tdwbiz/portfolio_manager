# Plan: make set_account_fingerprint safe (phase 2 step 8 follow-up)

Branch: `fix/fingerprint-command`. Follow-up to step 8 (`docs/plans/account-fingerprints.md`, architecture §12 phase 2 step 8).

## Goal

`manage.py set_account_fingerprint` must never create an Account. It only updates an existing row whose `name` matches the entered name **exactly**:

- **No** Account with that name → error, nothing stored, no row created.
- **More than one** Account with that name → error, nothing stored.
- The two hidden entries are compared **after `normalize_account_number`**, so equivalent formats of the same number (e.g. `900-00003-1-8` vs `9000000318`) count as equal.
- Error messages must not list account names or `account_id` values from the database (auto-created rows can carry a number in `name` or `account_id`). Generic wording only.

Kept unchanged: the 32-character key check, the hidden double prompt (`getpass`), `--replace`, and the success output of only the account name and last 4.

## What changes

| File | Change |
|---|---|
| `core/management/commands/set_account_fingerprint.py` | Replace create-or-get with a uniqueness check on `name`; compare normalized entries; generic error messages; update module docstring. |
| `core/tests/test_set_account_fingerprint.py` | Adapt to no-create behaviour; add unknown-name, duplicate-name and equivalent-format tests. |
| `AGENTS.md` | Update the one-off command line (§Environment / fingerprints bullet, line ~36). |

No migrations. No model, view, template or other command changes.

## Command behaviour (decided)

Order of checks in `handle()`:

1. Key check (unchanged, ≥ `MIN_KEY_LENGTH`).
2. Prompt for name; empty → error (unchanged).
3. `matches = list(Account.objects.filter(name=name))`:
   - `len(matches) == 0` → `CommandError("No account with that name; nothing stored.")` — before the number prompt, so nothing is asked or stored.
   - `len(matches) > 1` → `CommandError("Multiple accounts share that name; nothing stored. Fix the duplicate accounts first.")`
   - exactly one → continue with that row.
4. Two hidden prompts; empty (after `normalize_account_number`) → error (unchanged).
5. Compare `normalize_account_number(number1) == normalize_account_number(number2)`; differ → error (message unchanged in spirit: "The two entries differ; nothing stored.").
6. Existing fingerprint without `--replace` → error (message keeps the user-typed `{name}` — it is what the user just typed, not a DB listing).
7. Save fingerprint + last 4, print `name (…last4)` (unchanged).

`Account.objects.create(...)` is deleted entirely.

## Tests (`core/tests/test_set_account_fingerprint.py`)

- `test_stores_fingerprint_and_last4_for_new_account` → rename/repurpose: **pre-create** the `TOM_TFSA` row, then run; keep the assertions (fingerprint, last_4, number never printed).
- `test_updates_existing_account_by_name` — unchanged (already pre-creates).
- **New** `test_unknown_name_rejected_no_row_created`: empty `Account` table, expect `CommandError`, `Account.objects.count() == 0`.
- **New** `test_duplicate_name_rejected`: create two Accounts named `TOM_TFSA` (different `account_id`), expect `CommandError`, neither row gets a fingerprint, count still 2.
- **New** `test_equivalent_formats_accepted`: `number1="900-00003-1-8"`, `number2="9000000318"` → stores, fingerprint equals `fingerprint_account_number("90000003")`, `last_4 == "0003"`.
- `test_mismatched_entries_store_nothing` — keep; pre-create the account and additionally assert its fingerprint stays empty.
- `test_refuses_overwrite_without_replace`, `test_replace_overwrites`, `test_empty_number_stores_nothing` (pre-create the row here too), `test_refuses_without_key` — kept.
- Error-message hygiene: in the new tests, assert the raised message contains neither the account name nor any digits of the numbers (e.g. `"900"` not in message) for the unknown/duplicate cases.

## AGENTS.md

Update the bullet to say the command **refuses to create accounts**: it requires exactly one Account with the given name (error and nothing stored otherwise), compares the two hidden entries after normalisation, and keeps: hidden double prompt, key check, `--replace`, output of name + last 4 only.

## Out of scope

- Anything else from step 8 (importer lookup by fingerprint, `RBC_ACCOUNTS` removal) — already done in `account-fingerprints.md`.
- Duplicate-account cleanup / `merge_duplicate_accounts` changes.
- The `account_id` column, URLs, screens (steps 9–10).

## Steps (one commit each)

1. **Command**: rewrite lookup/compare logic + docstring in `set_account_fingerprint.py`.
   Check: `/opt/pm-venv/bin/python manage.py help set_account_fingerprint --settings=portfolio_manager.settings_test` prints the help; `rg -n "Account.objects.create" core/management/commands/set_account_fingerprint.py` is empty; `git status --short` shows only that file.
2. **Tests**: update `core/tests/test_set_account_fingerprint.py` as above.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_set_account_fingerprint --settings=portfolio_manager.settings_test` (single module only, quick) passes; `git status --short` shows only that file.
3. **AGENTS.md**: update the command bullet.
   Check: `rg -n "set_account_fingerprint" AGENTS.md` shows the new wording; `git status --short` shows only the three intended files across the branch.

Then run the full tests **once**, at the end of step 3 (before the last commit or as a final gate):

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

## Stop conditions

- A step needs any file beyond the three listed → stop and ask.
- Any existing test cannot be kept as described (e.g. a hidden dependency on auto-create elsewhere) → stop and report, do not change other files.
- Full test suite fails and the fix is not in these three files → stop.

## Open questions

1. *May the "already has a fingerprint" error still echo the user-typed name?* Recommended: **yes** — the prohibition is on listing database names/`account_id`s; the name is what the user just typed, and the current message already does this. The new unknown/duplicate errors stay generic.
2. *Should the duplicate-name check also consider case/whitespace?* Recommended: **no** — exact `name` match only, as specified; fixing near-duplicates is `merge_duplicate_accounts`' job (step 9/10 territory).
