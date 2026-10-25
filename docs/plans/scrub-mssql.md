# Plan: fix scrub_account_numbers on SQL Server (no MARS)

Branch: `fix/scrub-mssql`. Reference: `docs/plans/number-columns.md` (the scrub feature this fixes).

## Goal

`scrub_account_numbers --apply` failed on mercury with ODBC error HY000 "Connection is busy with results for another command". Two causes, both fixed here:

1. `scrub_text()` (`core/utils/account_fingerprint.py`, ~line 125) runs one `Account` query per candidate while the command's `.iterator()` result set is still open. SQLite allows this; SQL Server without MARS does not.
2. The command streams rows with `model.objects.all().iterator()` and queries/writes while that result set is open.

Fix: `scrub_text` takes a prebuilt fingerprint→name map (Account has ~12 rows, one query); the command reads pks into a list first, then fetches and updates in pk chunks so no result set is open during queries or writes. **Do not enable MARS in settings.** Output rules unchanged: dry run by default, counts only, never a value. Nothing runs against mercury.

## What changes

- `core/utils/account_fingerprint.py`
  - New helper `build_fingerprint_name_map() -> dict[str, str]`: one query, `Account.objects.values_list('fingerprint', 'name')`, skips rows with an empty fingerprint.
  - `scrub_text(text, name_map)`: uses the given map, never queries per candidate. `name_map` is **required** (no default), so a caller that forgets the map fails in tests instead of querying per call on mercury. All callers and tests are updated to pass it. Return contract `(cleaned, matched, replaced, unmatched)` unchanged.
- `imports/auto_import.py` (`auto_import()`): build the map once at the top of the function, pass it to the `scrub_text(...)` call (~line 231).
- `core/management/commands/manage_inbox_import.py`: build the map once in `handle()`, pass it down to `_process_monthly()` (used at ~line 488). Update the `_process_monthly` signature accordingly.
- `core/management/commands/scrub_account_numbers.py`:
  - Build the map once in `handle()` and pass it to every `scrub_text` call.
  - Per `(model, field)` target: first materialize `pks = list(model.objects.values_list('pk', flat=True))` (result set closed), then process in chunks of `CHUNK_SIZE = 200` pks: fetch `list(model.objects.filter(pk__in=chunk))`, scrub in Python (0 queries), and on `--apply` write with `model.objects.bulk_update(objs, [field], batch_size=CHUNK_SIZE)`. `batch_size=CHUNK_SIZE` is required because SQL Server allows at most 2100 parameters per statement and `bulk_update` uses about 2 per row per field plus the pks. No iterator, no open result set during writes.
  - Printed output format (`model,field,matched=…,replaced=…,unmatched=…`) unchanged.
- Tests: `core/tests/test_account_fingerprint.py`, `core/tests/test_scrub.py`.

No migrations. No settings changes.

## Out of scope

- Enabling MARS or any settings/connection changes.
- Any change to scrub targets (`TARGETS`), the dry-run/`--apply` semantics, or the output format.
- Running anything against mercury; all verification is on SQLite test settings.
- `docs/plans/number-columns.md` and the scripts under `scripts/` (parked).

## Steps

1. **Map helper + scrub_text signature.** Add `build_fingerprint_name_map()`; change `scrub_text` to take the required `name_map` (no default) and drop the per-candidate `Account` query. Update existing scrub tests in `core/tests/test_account_fingerprint.py` to pass a map where they currently rely on the DB lookup.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_account_fingerprint --settings=portfolio_manager.settings_test`. Commit.

2. **Callers pass the map.** `auto_import()` builds the map once and passes it at ~line 231; `manage_inbox_import` builds it in `handle()` and passes it into `_process_monthly()`.
   Check: `rg -n "scrub_text\(" imports/auto_import.py core/management/commands/manage_inbox_import.py` (every call passes the map) and `git status`. Commit.

3. **Command: pk list + chunked fetch/update.** Rewrite the loop in `scrub_account_numbers.py` as described (map built once, `values_list('pk')` into a list, chunked `filter(pk__in=…)` + `bulk_update`).
   Check: `/opt/pm-venv/bin/python manage.py scrub_account_numbers --settings=portfolio_manager.settings_test` (dry run on the empty test DB prints the header lines without error). Commit.

4. **New tests.**
   - `test_account_fingerprint.py`: `scrub_text` with a given map → `assertNumQueries(0)`; `build_fingerprint_name_map()` → `assertNumQueries(1)` and correct contents.
   - `test_scrub.py`: create 3 rows on one target field, run the command via `call_command`, record the query count; then bring the total to 10 rows and run again — assert the two query counts are **equal** (both well under one chunk of 200, so the count is flat). Use a `Connection.queries_count` capture around both runs.
   Check: `/opt/pm-venv/bin/python -m pytest --collect-only -q` (collection still clean). Commit.

5. **Full test run (once, at the end).**
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   Fix anything these surface (still on this branch), then commit.

## Stop conditions

- Stop and ask if a full-suite failure cannot be fixed without changing scrub semantics, output format, or settings.
- Stop if `bulk_update` on a `TextField`/`CharField` target behaves unexpectedly on the test settings (it should not).
- Do not touch `.env`, do not enable MARS, do not rewrite git history.

## Open questions

All resolved — see Decisions below.

1. Should `name_map` be required or optional on `scrub_text`? (Resolved: required.)
2. Where exactly does `manage_inbox_import` build the map? (Resolved: in `handle()`.)
3. Chunk size? (Resolved: `CHUNK_SIZE = 200`, with `batch_size` passed to `bulk_update`.)

## Decisions (tdw, Oct 25)

- **Q1:** required, not optional: `scrub_text(text, name_map)` with no default, so a caller that forgets the map fails in tests instead of querying per call on mercury; update all callers and tests.
- **Q2:** yes, build it in `handle()` and pass it to `_process_monthly()`.
- **Q3:** `CHUNK_SIZE = 200`, and also pass `batch_size` to `bulk_update`, because SQL Server allows at most 2100 parameters per statement and `bulk_update` uses about 2 per row per field plus the pks; a test that updates two fields in one chunk must stay under that limit. In the query-count test compare two different row counts (e.g. 3 rows and 10 rows) and assert the same count.
