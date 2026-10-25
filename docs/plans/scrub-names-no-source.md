# Plan: scrub_statement_file_names without the source PDFs (follow-up to phase 2 step 11)

Branch: `fix/scrub-names-no-source` (from `dev`). Follow-up to step 11 (`docs/plans/log-filter.md`).

## Goal

`scrub_statement_file_names` rewrites `RbcMonthly.file_name` / `RbcAnnual.file_name` to the safe
pattern `<date>_<kind>_<ACCOUNT_NAME>_<hash8>.<ext>`, and today `hash8` is the content hash of the
physical PDF found in `processed/` (or `processed/errors/`). On mercury the PDFs behind **958
monthly and 105 annual rows are not on this machine**, so every one of those rows is currently
counted as `missing-source` and skipped — the scrub does nothing.

Change: when the source file is **missing**, use as `hash8` the first 8 hex characters of
**HMAC-SHA256(`ACCOUNT_FINGERPRINT_KEY`, stored file name)**. When the file is found, keep the
content hash exactly as now. A keyed HMAC (not a plain hash of the name) is required because the
old names carry the account number and would otherwise be brute-forceable (see
`core/utils/safe_names.py` docstring). Requirements:

- the produced name must pass `is_safe_file_name`;
- rows sharing the same old name, account and date (the CAD and USD section of one PDF) must get
  the **same** new name — satisfied because the hash input is the stored name and the name is
  otherwise built from account + date;
- if `ACCOUNT_FINGERPRINT_KEY` is missing or shorter than 32 chars, **stop before writing anything**
  (also for `--dry-run`, so the dry run reports what the real run would do);
- rows with an empty/`NULL` `file_name` are hashed like any other name — the HMAC input is the
  empty string — so they end up safe (decision, tdw, Oct 25);
- `--dry-run` prints counts only, per model: `from-file`, `keyed`, `already-safe` (no names, no
  numbers);
- still idempotent: a second run is a no-op, because keyed names are already safe.

## What changes

- `core/utils/account_fingerprint.py` — add `keyed_hash8(text) -> str`: first 8 hex chars of
  HMAC-SHA256 with `ACCOUNT_FINGERPRINT_KEY` over `text.encode('utf-8')`, reusing `_get_key()` so a
  missing/short key raises `ValueError` at the point of use like the other helpers.
- `core/management/commands/scrub_statement_file_names.py` —
  - validate the key up front (`try: keyed_hash8('') except ValueError: raise CommandError(...)`),
    before any row is touched, so nothing is written without a key;
  - replace the `missing_source` counter with `keyed`: `hash8 = hash8_of(path)` if the file is
    found (counter `from_file`), else `keyed_hash8(row.file_name or '')` (counter `keyed`);
  - output line per model: `[dry-run] RbcMonthly: from-file=N keyed=N already-safe=N` (same three
    counters in the real run);
  - update the module docstring (missing source is now keyed, not skipped).
- `core/tests/test_scrub_statement_file_names.py` — rewrite the missing-source expectations and add
  the new cases (tests section).
- `docs/plans/log-filter.md` — no edit; this file is the record of the follow-up.

No migrations, no model changes, no view/template changes. Still **DB only**: files on disk are
never renamed.

## Out of scope

- The **monthly duplicate check** is not touched. mercury has **30 pairs** of `RbcMonthly` rows with
  the same account, date and currency under *different* file names; different old names hash
  differently, so this command leaves them distinct. They are step 13 (PDF duplicate check) work.
- Renaming or moving files on disk; re-downloading/re-importing the missing PDFs.
- Anything about `RbcAnnual`/`RbcMonthly` fields other than `file_name`; account-number scrubbing
  (`scrub_account_numbers`), log filter, importers.

## Steps (one commit each)

1. **`keyed_hash8` helper.** Add the function to `core/utils/account_fingerprint.py` with a
   docstring saying it is for name scrubbing when the source file is gone; key rules come from
   `_get_key()`.
   Check: `/opt/pm-venv/bin/python -m pytest --collect-only -q core/tests/test_account_fingerprint.py`
   and `git diff --stat`.

2. **Command: keyed fallback + key gate + new counters.** In
   `core/management/commands/scrub_statement_file_names.py`: key check before the loop
   (`CommandError`, nothing written, both modes), `from_file` / `keyed` / `already_safe` counters,
   `hash8 = hash8_of(path)` when found else `keyed_hash8(name)`, new stdout line, docstring update.
   Check: `/opt/pm-venv/bin/python -m pytest --collect-only -q tests/ imports/tests/` and
   `rg -n "missing-source" core/` (must be gone).

3. **Tests** in `core/tests/test_scrub_statement_file_names.py` (fake numbers `90000001` /
   `ACCOUNT_A` only, key from `settings_test`):
   - missing source → row rewritten to a safe name whose hash equals
     `keyed_hash8(OLD_NAME)`; counted as `keyed`;
   - found source → still the content hash (`from-file`), physical files untouched;
   - two `RbcMonthly` rows (CAD + USD) with the same old name, account and `statement_date` →
     identical new name;
   - every produced name passes `is_safe_file_name`;
   - `--dry-run` prints only `RbcMonthly: from-file=… keyed=… already-safe=…` / same for
     `RbcAnnual`, and contains no old name, no fake number, no key; writes nothing;
   - `override_settings(ACCOUNT_FINGERPRINT_KEY='')` and a 10-char key → `CommandError`, no row
     changed (dry run and real run);
   - second run is a no-op (all rows `already-safe`, nothing rewritten).
   Check: `git status --short` and `rg -c "def test_" core/tests/test_scrub_statement_file_names.py`
  (the count went up); no test run before step 4.

4. **Full test run** (only here, once):
   `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   then `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`. Commit.

## Stop conditions

- A produced name fails `is_safe_file_name` (e.g. an odd extension or an account name that
  sanitises to nothing) — stop, do not special-case silently.
- `safe_file_name` raises for a stored date/year (monthly row without `statement_date`, annual row
  without `report_year`): the plan has no fallback for that; ask instead of guessing.
- ~~The key gate turns out to be wanted only for real runs (see open question 1) — plan is revised.~~
  Resolved: the key gate applies to both modes (Decisions, tdw, Oct 25).
- Anything needed from `mercury` data (real counts, real names): agents do not touch that DB; tdw
  runs the dry run.

## Open questions

All four were answered by tdw on Oct 25 — see **Decisions** below.

1. *Should `--dry-run` also require a valid key?* **Recommended: yes** — check the key before the
   loop in both modes and raise `CommandError`, so the dry run cannot report success for a run that
   would then fail.
2. *What exactly is HMAC'd — the stored value verbatim, or its basename / a normalised form?*
   **Recommended: the stored `file_name` string verbatim (UTF-8)**, empty string if `NULL`. It is
   stable across re-runs and keeps rows with different stored names distinct; normalising to the
   basename would merge rows that only differ by a path prefix, which is not asked for.
3. *Rows with an empty/`NULL` `file_name`?* **Recommended: hash them like any other name** (empty
   input) so they end up safe; if tdw prefers them skipped, that is a one-line change.
4. *Where does `keyed_hash8` live?* **Recommended: `core/utils/account_fingerprint.py`**, next to
   `wrap_tx_hash`, because that module owns `_get_key()`; `safe_names.py` stays key-free.

## Decisions (tdw, Oct 25)

tdw accepts the recommended answer for every open question:

1. **Key gate in both modes.** The command validates `ACCOUNT_FINGERPRINT_KEY` before the loop and
   raises `CommandError` for a missing/short key in *both* `--dry-run` and real runs, so nothing is
   written (or reported as safe) without a valid key.
2. **HMAC input.** The stored `file_name` string verbatim, UTF-8 encoded; the empty string when
   `file_name` is `NULL`. No basename or other normalisation.
3. **Empty/`NULL` `file_name` rows** are hashed like any other name (empty input) and rewritten to
   a safe name — not skipped.
4. **`keyed_hash8` lives in `core/utils/account_fingerprint.py`**, next to `wrap_tx_hash`
   (the module owns `_get_key()`); `safe_names.py` stays key-free.
