# Plan: scrub bank account numbers (15-digit runs)

Branch: `fix/scrub-bank-accounts` (from `dev`). Follow-up to phase 2 step 10.

## Goal

The step-10 dry run on mercury found 27 unmatched 15-digit numbers (4 distinct
values) in `Transaction.notes` and `RbcMonthlyTransaction.description`, in
"transfer to/from account" text. A fingerprint check showed no 8-digit window
of any of them matches a stored account: they are **outside bank accounts**
(institution 3 + transit 5 + account 7 digits), not RBC numbers.

Extend `scrub_text` so a run of **exactly 15 digits**, not part of a longer
digit run, is replaced with the fixed label `BANK_ACCOUNT` — no fingerprint
lookup. Existing fingerprint matching stays unchanged; the 15-digit rule runs
in the same pass, so step 11's log filter gets it for free. Import-time
scrubbing (`auto_import`, `manage_inbox_import`) inherits the behaviour
automatically because both go through `scrub_text`.

`scrub_account_numbers` reports these as an extra per-column count
(`bank=N`). It still prints counts only, never a value. Nothing in this plan
runs against mercury.

## What changes

- `core/utils/account_fingerprint.py` — `scrub_text`:
  - Inside the existing `_SCRUB_CANDIDATE_RE` loop, after `_is_scrub_candidate`:
    if the candidate is **pure digits and exactly 15 long**, replace it with
    `BANK_ACCOUNT`, increment `bank` and `replaced`, and skip the fingerprint
    lookup. The greedy `\d[\d-]*` alternative already captures the whole digit
    run, so a 14- or 16-digit run is a single candidate of that length and is
    left alone (it falls through to the unchanged fingerprint path).
  - Return a 5-tuple `(cleaned, matched, replaced, unmatched, bank)`.
    `bank` counts 15-digit replacements; they are **not** counted in
    `matched` or `unmatched` (previously they fell into `unmatched`).
    `replaced` counts every rewritten span, bank ones included.
  - Update the docstring (5-tuple, `BANK_ACCOUNT` rule).
- `core/management/commands/scrub_account_numbers.py` — unpack the 5-tuple,
  accumulate `bank` per column, append `,bank=N` to the per-column report
  line. Counts only, as before.
- `imports/auto_import.py`, `core/management/commands/manage_inbox_import.py`
  — **no change**: both use `scrub_text(...)[0]` only.
- Tests: `core/tests/test_account_fingerprint.py` (`ScrubTextTests`,
  `FingerprintNameMapTests` unpack 4-tuples → 5), `core/tests/test_scrub.py`
  (report-line assertions gain `,bank=0` / `,bank=N`).

No migrations, no models, no views.

## Out of scope

- Anything run against mercury; the actual scrub (`--apply`) and re-running
  the dry run stay with tdw.
- Step 11 (log filter) itself — this plan only makes sure `scrub_text` feeds
  it the bank rule.
- Dashed digit runs totalling 15 digits (e.g. `12345-67890-12345`): the rule
  applies to contiguous pure-digit runs only; dashed candidates keep the
  existing fingerprint behaviour (see Open questions).
- Changing the `TARGETS` list or chunking in `scrub_account_numbers`.

## Steps

1. **`scrub_text` 15-digit rule + 5-tuple** in `core/utils/account_fingerprint.py`.
   Check: `/opt/pm-venv/bin/python -c "from core.utils.account_fingerprint import scrub_text"`
   via `manage.py shell -c` is not needed — just
   `/opt/pm-venv/bin/python -m py_compile core/utils/account_fingerprint.py`
   and `git diff` review. Commit.
2. **`scrub_account_numbers` report** gains `bank=N` (unpack 5-tuple).
   Check: `py_compile` the command; `rg -n "bank=" core/management/commands/scrub_account_numbers.py`. Commit.
3. **Update existing tests** to the 5-tuple and the new report line
   (`test_account_fingerprint.py`, `test_scrub.py`).
   Check: `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/ --collect-only -q`
   (collection only) and `rg -n "unmatched = 0$|, 0, 0)" core/tests/` finds no
   leftover 4-tuple unpacks. Commit.
4. **New tests** (fake values only, in `ScrubTextTests`):
   - a 15-digit run is replaced with `BANK_ACCOUNT` (`bank=1`, `replaced=1`,
     `matched=0`, `unmatched=0`), e.g. `"transfer to 123456789012345 done"`;
   - a 14-digit and a 16-digit run are **not** replaced (text unchanged);
   - `"90000003 123456789012345"` → `"ACCOUNT_C BANK_ACCOUNT"` (adjacent
     8-digit account still resolves to its name);
   - amounts, dates and an exchange rate such as `1.36540000` are unchanged;
   - one `test_scrub.py` case: a row whose notes contain a 15-digit run is
     rewritten on `--apply` and the dry-run line reports `bank=1`.
   Check: `rg -c "def test_" core/tests/test_account_fingerprint.py` grew. Commit.
5. **Tests once, at the end**:
   - `/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test`
   - `/opt/pm-venv/bin/python -m pytest tests/ imports/tests/`
   Fix anything that fails, commit, stop and summarise.

## Stop conditions

- If a 15-digit run that starts with a stored account's 8 digits turns up in
  a test as ambiguous behaviour beyond the recommended precedence below, stop
  and ask.
- If any caller of `scrub_text` other than the four listed files is found
  (`git grep -l scrub_text`), stop and ask before touching it.
- If a step needs anything not in this plan (new file, model, migration),
  stop; the plan is revised first.
- Never print or log an original number; tests use fake 15-digit values only.

## Open questions (all answered — see Decisions below)

1. **Precedence:** may a 15-digit run whose first 8 digits match a stored
   fingerprint still become `BANK_ACCOUNT`? **Recommended: yes** — the rule
   applies unconditionally with no lookup. A real RBC number padded to the
   15-digit bank form starts with institution+transit, never the account
   number, so a practical conflict is not expected.
2. **Do bank hits count in `replaced`?** **Recommended: yes** (it is a
   rewritten span), and they are excluded from `matched` and `unmatched`.
   Note the behaviour change: previously these runs were counted as
   `unmatched`; step 11's log filter should read `bank=` instead.
3. **Dashed runs totalling 15 digits?** **Recommended: no** — only a
   contiguous pure-digit run of exactly 15 digits is replaced; the 27 mercury
   findings are contiguous runs, and dashed candidates already have the
   fingerprint path.
4. **Return shape:** 5-tuple vs dict/dataclass. **Recommended: 5-tuple** —
   smallest diff; the two import callers use `[0]` and are unaffected.

## Decisions (tdw, Oct 25)

tdw accepts all the recommended answers above:

1. **Precedence:** yes — a contiguous pure-digit run of exactly 15 digits
   becomes `BANK_ACCOUNT` unconditionally, with no fingerprint lookup, even
   if its first 8 digits match a stored account.
2. **Bank hits in `replaced`:** yes — counted in `replaced`, excluded from
   `matched` and `unmatched`; step 11's log filter reads `bank=` instead of
   relying on `unmatched`.
3. **Dashed runs totalling 15 digits:** no — only a contiguous pure-digit
   run of exactly 15 digits is replaced; dashed candidates keep the existing
   fingerprint behaviour.
4. **Return shape:** 5-tuple `(cleaned, matched, replaced, unmatched, bank)`.
