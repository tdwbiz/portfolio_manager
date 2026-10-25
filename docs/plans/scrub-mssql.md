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
  - `scrub_text(text, name_map=None)`: uses the given map, never queries per candidate. If `name_map is None`, build it once inside the call (fallback keeps the old call sites working and is at most one query per call, not per candidate). Return contract `(cleaned, matched, replaced, unmatched)` unchanged.
- `imports/auto_import.py` (`auto_import()`): build the map once at the top of the function, pass it to the `scrub_text(...)` call (~line 231).
- `core/management/commands/manage_inbox_import.py`: build the map once in `handle()`, pass it down to `_process_monthly()` (used at ~line 488). Update the `_process_monthly` signature accordingly.
- `core/management/commands/scrub_account_numbers.py`:
  - Build the map once in `handle()` and pass it to every `scrub_text` call.
  - Per `(model, field)` target: first materialize `pks = list(model.objects.values_list('pk', flat=True))` (result set closed), then process in chunks of `CHUNK_SIZE = 500` pks: fetch `list(model.objects.filter(pk__in=chunk))`, scrub in Python (0 queries), and on `--apply` write with `model.objects.bulk_update(objs, [field], batch_size=CHUNK_SIZE)`. No iterator, no open result set during writes.
  - Printed output format (`model,field,matched=…,replaced=…,unmatched=…`) unchanged.
- Tests: `core/tests/test_account_fingerprint.py`, `core/tests/test_scrub.py`.

No migrations. No settings changes.

## Out of scope

- Enabling MARS or any settings/connection changes.
- Any change to scrub targets (`TARGETS`), the dry-run/`--apply` semantics, or the output format.
- Running anything against mercury; all verification is on SQLite test settings.
- `docs/plans/number-columns.md` and the scripts under `scripts/` (parked).

## Steps

1. **Map helper + scrub_text signature.** Add `build_fingerprint_name_map()`; change `scrub_text` to accept the optional `name_map` and drop the per-candidate `Account` query. Update existing scrub tests in `core/tests/test_account_fingerprint.py` to pass a map where they currently rely on the DB lookup.
   Check: `/opt/pm-venv/bin/python manage.py test core.tests.test_account_fingerprint --settings=portfolio_manager.settings_test`. Commit.

2. **Callers pass the map.** `auto_import()` builds the map once and passes it at ~line 231; `manage_inbox_import` builds it in `handle()` and passes it into `_process_monthly()`.
   Check: `rg -n "scrub_text\(" imports/auto_import.py core/management/commands/manage_inbox_import.py` (every call passes the map) and `git status`. Commit.

3. **Command: pk list + chunked fetch/update.** Rewrite the loop in `scrub_account_numbers.py` as described (map built once, `values_list('pk')` into a list, chunked `filter(pk__in=…)` + `bulk_update`).
   Check: `/opt/pm-venv/bin/python manage.py scrub_account_numbers --settings=portfolio_manager.settings_test` (dry run on the empty test DB prints the header lines without error). Commit.

4. **New tests.**
   - `test_account_fingerprint.py`: `scrub_text` with a given map → `assertNumQueries(0)`; `build_fingerprint_name_map()` → `assertNumQueries(1)` and correct contents.
   - `test_scrub.py`: create rows (e.g. 3 and then 6 rows on one target field), run the command via `call_command`, and assert the query count with more rows is not larger than with fewer rows (same chunk size, so same query count — use `CaptureStdOut` and `assertNumQueries` or a `Connection.queries_count` capture around both runs).
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

1. Should `name_map` be required or optional on `scrub_text`? **Recommended: optional (`None` → build the map once inside the call).** It keeps the public contract backward compatible, still removes the per-candidate query everywhere, and the three real call sites all pass a map explicitly.
2. Where exactly does `manage_inbox_import` build the map? **Recommended: in `handle()`, passed down to `_process_monthly()`** — one query per command run, matching the task wording ("builds it once per run and passes it in").
3. Chunk size? **Recommended: module constant `CHUNK_SIZE = 500`** in the command — small enough for SQL Server parameter limits, and the query-count test uses row counts well below one chunk so the count is flat.
