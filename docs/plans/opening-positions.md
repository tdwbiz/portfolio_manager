# Opening positions and owners (step 15c)

Branch: `feature/opening-positions`. Plan only — no ACB calculation in this step.

## Goal

Lay the data foundation for opening ACB (architecture §8, clean-up step 15):

- **Owners** exist as first-name-only records, with per-account ownership shares.
- **Opening positions** can be stored per (account, symbol, currency), either copied
  from an RBC monthly statement's book cost (`RBC_BOOK_COST`) or entered by hand
  (`MANUAL`).
- Two commands to populate that data safely (dry run by default), admin visibility,
  tests. Nothing reads the new tables yet; ACB calculation is a later step.

## What changes

| File | Change |
|---|---|
| `core/models.py` | Add `Owner`, `AccountOwner`, `OpeningPosition` (db tables `core_owner`, `core_account_owner`, `core_opening_position`). |
| `core/migrations/0048_owner_accountowner_openingposition.py` | One new migration (next number after `0047_…`). |
| `core/management/commands/set_account_owners.py` | New command. |
| `core/management/commands/load_opening_positions.py` | New command. |
| `core/admin.py` | Read-only registration of the three models. |
| `core/tests/test_opening_positions.py` | New tests (both commands, all refusals, dry-run, re-run). |
| `docs/IMPORT_COMMANDS.md` | One entry per command. |

### Model shapes

- `Owner` (`core_owner`): `name` CharField, unique (first names only, e.g. `Tom`).
- `AccountOwner` (`core_account_owner`): `account` FK → `Account`, `owner` FK → `Owner`,
  `share_percent` Decimal(5,2). `unique_together = [("account", "owner")]`.
- `OpeningPosition` (`core_opening_position`):
  - `account` FK → `Account`;
  - `statement` FK → `RbcMonthly`, **null/blank** (nullable for later IB rows; `--set`
  keeps the existing row's statement link — see Decisions, Q1);
  - `symbol` CharField(50); `security_name` CharField(200, blank);
  - `currency` CharField(3);
  - `quantity` Decimal(12,4); `book_cost` Decimal(15,2) — in the currency section's currency;
  - `source` CharField with choices `RBC_BOOK_COST` / `MANUAL`;
  - `note` TextField(blank), `created_at`, `updated_at`;
  - `unique_together = [("account", "symbol", "currency")]`.

### `set_account_owners`

`--account NAME --share NAME:PERCENT` (repeatable) `[--apply]`. Dry run by default.

- Resolves account by `name` (exact); refuses if no such account.
- Validates every `--share` value parses as `NAME:PERCENT` with a decimal 0–100.
- Refuses unless the shares add up to **exactly 100** (printed total on refusal).
- Creates missing `Owner` rows (names as given, trimmed).
- Replaces that account's whole `AccountOwner` set with the given shares.
- Output: owner names, percentages, account **name** only.

### `load_opening_positions`

Two modes, both dry run unless `--apply`.

**Snapshot mode** — `--account NAME --statement YYYY-MM-DD [--apply] [--replace]`:

- Finds that account's `RbcMonthly` rows with `statement_date == <date>` (all currency
  sections); refuses if none.
- Refuses if the account already has any `OpeningPosition` rows, unless `--replace`
  (with `--replace`, existing rows are deleted in the same transaction as the insert).
- Refuses if any selected holding has an empty `symbol` or `book_cost == 0` — prints
  symbol/name and currency of each offender, writes nothing.
- Refuses if the account has any `Transaction` with `settlement_date <= <date>` —
  prints the count and the distinct settlement dates, writes nothing.
- Writes one `OpeningPosition` per `RbcMonthlyHolding` of those `RbcMonthly` rows:
  symbol, security name, holding currency, quantity, book cost, `source=RBC_BOOK_COST`,
  `note="from RBC statement YYYY-MM-DD"`, `statement` = the RbcMonthly row.
- Output: account name, statement date, symbols, quantities, book-cost amounts.
  Never account digits.

**Override mode** — `--account NAME --set SYMBOL --currency CODE --book-cost AMOUNT [--note TEXT] [--apply]`:

- Requires `--set`, `--currency` and `--book-cost` together; `--statement` and `--replace`
  are not allowed with `--set`.
- Refuses if no `OpeningPosition` exists for that (account, symbol, currency) — this
  command only overrides existing rows.
- Updates `book_cost`, `note` (if given) and sets `source=MANUAL`; the existing row's
  `statement` link is kept unchanged; `updated_at` refreshes.

## Out of scope

- Any ACB / capital-gain calculation, per-owner reporting, or use of these tables by
  `monthly_check`, `fill_from_pdf` or `generate_statements` — none of those files are touched.
- No views, templates, URLs or forms; admin is read-only.
- No changes to `Account`, `RbcMonthly`, `RbcMonthlyHolding` or `Transaction`.
- No backfill of real data (tdw runs the commands themselves).

## Steps (one commit each)

1. **Models + migration.** Add the three models to `core/models.py`, run
   `makemigrations` (one migration, `0048_…`).
   Check: `/opt/pm-venv/bin/python manage.py makemigrations --check --settings=portfolio_manager.settings_test` is clean;
   `git status` shows exactly one new migration file.
2. **`set_account_owners` command.**
   Check: `/opt/pm-venv/bin/python manage.py set_account_owners --help` exits 0.
3. **`load_opening_positions` command** (snapshot + override modes).
   Check: `/opt/pm-venv/bin/python manage.py load_opening_positions --help` exits 0.
4. **Admin registrations** (read-only: `has_add_permission`/`has_change_permission`/
   `has_delete_permission` → False; list display of the key fields).
   Check: `git grep -n "OpeningPosition" core/admin.py` shows all three models.
5. **Tests** in `core/tests/test_opening_positions.py` (see below).
   Check: `/opt/pm-venv/bin/python -m pytest core/tests/test_opening_positions.py -q` passes.
6. **Docs**: one `IMPORT_COMMANDS.md` entry per command (purpose, flags, dry-run default,
   refusal rules).
   Check: `git grep -n "set_account_owners\|load_opening_positions" docs/IMPORT_COMMANDS.md`.

## Tests (step 5, synthetic data only)

Use the fake accounts `90000001`–`90000008` (`Account` rows with fingerprints, as in
`core/tests/test_set_account_fingerprint.py`), synthetic `RbcMonthly` /
`RbcMonthlyHolding` / `Transaction` rows. Cover:

- `set_account_owners`: dry run writes nothing; `--apply` creates owners and shares;
  re-run replaces (no duplicates); refusal when shares ≠ 100 (e.g. 60+30, 50+50.01);
  refusal on malformed `--share`; refusal on unknown account; owners created on demand.
- `load_opening_positions` snapshot mode: dry run writes nothing; `--apply` writes one
  row per holding across both currency sections with `source=RBC_BOOK_COST` and the
  note naming the date; re-run refuses (rows exist) and `--replace` rewrites cleanly
  (still one row per holding); refusal on missing symbol, on `book_cost == 0`, on a
  transaction with `settlement_date <= statement date` (and acceptance when the only
  transaction settles after it); refusal when no RbcMonthly on that date.
- Override mode: dry run changes nothing; `--apply` updates book cost and sets
  `source=MANUAL`; refusal when the row does not exist; `--set` without `--book-cost`
  refused.
- Output check: printed text contains account **name**, never the fake account number
  digits.

## Final test run (after step 6, once)

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test
/opt/pm-venv/bin/python -m pytest tests/ imports/tests/
```

## Stop conditions

- Migration number collides with an existing one, or `makemigrations --check` stays dirty.
- A refusal rule turns out to contradict this plan — stop, ask, revise the plan first.
- Any need to touch `monthly_check`, `fill_from_pdf` or `generate_statements`.
- Any output path that would print account digits (must stop, not truncate).
- A missing package — stop and name it.

## Open questions

1. **May `OpeningPosition.statement` be null?** Needed for `--set`/MANUAL overrides,
   which have no statement. **Recommended: yes, `null=True, blank=True`**, and the
   unique key stays (account, symbol, currency).
2. **Does `--set` create a row if missing?** The task says "override one existing row".
   **Recommended: refuse if the row does not exist**; creating positions from scratch
   is a later step's job.
3. **`--replace` and the transaction guard:** should `--replace` skip the
   "transactions on or before the statement date" refusal? **Recommended: no** — the
   guard protects the semantics of an opening position and applies in both modes.
4. **Duplicate symbols across currency sections** (e.g. a CAD and a USD row for the
   same ticker): the unique key includes currency, so both are stored.
   **Recommended: keep it**, and print both rows in the summary so a human can spot it.

## Decisions (tdw, Oct 31)

1. `OpeningPosition.statement` **is nullable** (`null=True, blank=True`) — needed for
   later IB rows. But `--set` **keeps the existing row's statement link** and only
   changes book cost, source and note.
2. `--set` **refuses if the row does not exist** (as recommended); creating positions
   from scratch is a later step's job.
3. `--replace` does **not** skip the "transactions on or before the statement date"
   refusal (as recommended) — the guard applies in both modes.
4. Duplicate symbols across currency sections are **both stored** (unique key includes
   currency, as recommended), and both rows are printed in the summary.
