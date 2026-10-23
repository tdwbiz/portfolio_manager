# Repo hygiene (phase 0, step 1)

Source: `docs/architecture.md` §12, phase 0 step 1. Where the audits and `docs/architecture.md` disagree, `docs/architecture.md` wins.

## Goal

Tidy the repo bookkeeping before any code clean-up starts: correct dependency list, correct ignore rules, and one authoritative agent-instructions file. **No behaviour changes — no code is touched, and no settings files are touched.** Five items only:

1. `requirements.txt`: add `mssql-django` pinned to the installed version, and remove the stale `django-mssql-backend` line (guarded — see step 1).
2. `.env.*` in `.gitignore`, `.env.example` stays tracked.
3. The "sharing with a cloud AI" rule in `AGENTS.md`, worded per the fingerprint design in `docs/architecture.md` §5.
4. The JSON-log correction in `AGENTS.md` (audit 2, Q11).
5. `CONVENTIONS.md` folded into `AGENTS.md` and deleted (audit 3, Q7).

Explicitly **out of scope** (later steps): anything `RBC_ACCOUNTS`-related, deleting `events.jsonl` / `calc_march.py` / `login_test.html` or any other junk, moving files to history, stale-doc edits, and **any change to the settings files** (the engine value is only *read* in step 1, never edited).

## What changes (files)

| File | Change |
|---|---|
| `requirements.txt` | Add `mssql-django==1.8.0` (verified: `/opt/pm-venv/bin/pip show mssql-django` → 1.8.0) and remove `django-mssql-backend>=2.0` — but only if the engine guard in step 1 passes. Leave everything else as is. |
| `.gitignore` | In the "Environment variables" block, replace the `.env.local` / `.env.*.local` lines with `.env` + `.env.*` + `!.env.example` (broader glob, explicit un-ignore so `.env.example` stays tracked). |
| `AGENTS.md` | Three edits, no other rewrites: (a) new short section "Sharing data with a cloud AI"; (b) fix the JSON-log bullet under "Import commands"; (c) fold in the one live rule from `CONVENTIONS.md`. |
| `CONVENTIONS.md` | Deleted (content folded into `AGENTS.md`). |

No migrations (no models touched). No settings files touched.

### Content notes

**(a) Cloud-AI rule.** New short `## Sharing data with a cloud AI` section in `AGENTS.md`, consistent with `docs/architecture.md` §5 (the app stores no account numbers; accounts are identified by name; the only digits shown anywhere are last-4 on the accounts page). Wording to implement:

- Never paste into a cloud AI: account numbers (not even last-4 digits), `.env` content, log lines, `docs/reconciliation/` content, statement files (CSV or PDF), or VL JSON output from `processed/json_logs/`.
- Never paste **full names, surnames, addresses or SINs** — these never leave the machine.
- Account names (`TOM_TFSA`, `SUSAN IB`, `TS_HUISGELD`) do contain real first names, but they are the identifiers the app itself uses, so they **may be shared** with a cloud AI when describing a problem.
- When describing a problem, quote the **file and line** (and the account **name**, per the fingerprint design in `docs/architecture.md`), not data rows.
- In examples, docs and prompts use the fake accounts `90000001`–`90000008` (`ACCOUNT_A`–`ACCOUNT_H`), or the real account names above.
- Local models (ports 11435 / 11438, no internet path) are exempt.

**(b) JSON-log correction (audit 2 Q11).** The bullet currently reads "holds JSON only for **failed** imports". Per audit 2 §(JSON logs) and Q11, `manage_inbox_import` writes a JSON log to `processed/json_logs/` for **every** processed PDF (`*_ERROR.json` when the parse has errors); only the deprecated commands write on error alone. Correct the bullet to say exactly that; `FileImportLog` still records every import.

**(c) CONVENTIONS.md fold-in.** `CONVENTIONS.md` is one line: "Strictly avoid hardcoded strings for configuration. Always use environment variables." `AGENTS.md` does not currently state this rule (the env-driven examples like `RBC_ACCOUNTS`/`VL_MODEL_*` imply it but never say it), so add it once, e.g. as a bullet in the "Environment" section. Nothing else in `CONVENTIONS.md` to carry over.

**Where the rule lives.** Repo `AGENTS.md` only (see Decisions).

**References to `CONVENTIONS.md`.** The only mentions in the repo are in `docs/architecture.md` §12 and `docs/audit/03-commands-scripts-docs.md` — both are decision records describing this very task; leave them. No code, config or live doc references it, so nothing else to update.

## Tests to run

No code changes, so the suites are regression guards only. **Run both suites once, in step 6** (not after every step):

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test   # ~35 s, 220 tests
/opt/pm-venv/bin/python -m pytest tests/ --ignore=tests/e2e   # e2e needs playwright, which is not installed
```

Per-step checks (cheap, no test runs):

- Step 1: `/opt/pm-venv/bin/pip show mssql-django` still reports 1.8.0 (pin matches the installed version); the engine guard below passed; `git diff HEAD~1 -- requirements.txt` shows exactly one added and one removed line.
- Step 2: `git check-ignore -v .env .env.local .env.foo` → ignored; `git check-ignore .env.example` → **not** ignored; `git ls-files .env.example` still lists the file; `git ls-files '.env*'` lists `.env.example` and **nothing else** (report any other tracked `.env*` file instead of continuing).
- Steps 3–5: manual read of `AGENTS.md` (each rule appears exactly once); `git grep -l CONVENTIONS` returns only the two record files above.

## Steps

Each step is one commit on this branch (`chore/repo-hygiene`).

1. **requirements.txt**: add `mssql-django==1.8.0` and remove `django-mssql-backend>=2.0`.
   - Verify the new pin against `/opt/pm-venv/bin/pip show mssql-django`.
   - Rationale: `docs/architecture.md` §13 lists "`requirements.txt` names `django-mssql-backend`; the app actually needs `mssql-django` (step 1)" — so the stale line belongs to this step.
   - **Engine guard before removing the line:** grep the settings files (`portfolio_manager/settings*.py`) for the `DATABASES` `ENGINE` value. It must be the `mssql-django` engine (`mssql`), **not** `sql_server.pyodbc`. If **any** settings file uses `sql_server.pyodbc`, **stop and report** — do not remove the line and do not change any settings file in this branch.
   - Commit.
2. **.gitignore**: replace the env-var block entries with `.env`, `.env.*` and `!.env.example`. Verify with `git check-ignore` as above, and run `git ls-files '.env*'` to confirm `.env.example` is the only tracked `.env*` file (report any other). Commit.
3. **AGENTS.md — cloud-AI rule**: add the "Sharing data with a cloud AI" section (wording above, adjusted to fit the file's style). Commit.
4. **AGENTS.md — JSON-log correction**: fix the JSON-log bullet per audit 2 Q11. Commit.
5. **CONVENTIONS.md fold-in**: add the env-vars rule to `AGENTS.md`, `git rm CONVENTIONS.md`, confirm no live references remain. Commit.
6. Run both test suites (commands above); if green, the branch is done — report and stop.

## Decisions (tdw, Oct 22)

1. **`django-mssql-backend`.** Step 1 removes the stale `django-mssql-backend` line as well, guarded by the engine check: the settings `ENGINE` must be `mssql` (mssql-django's); if any settings file uses `sql_server.pyodbc`, stop and report instead of removing the line. No settings changes in this branch.
2. **Where the cloud-AI rule lives.** Repo `AGENTS.md` only — not in `~/.pi/agent/AGENTS.md` or any project-level file.
3. **`.gitignore` negation.** The `!.env.example` negation is fine (no explicit list of variants). Step 2 additionally runs `git ls-files '.env*'` and reports any tracked `.env` file other than `.env.example`.
4. **Account names vs. personal data.** Account names (`TOM_TFSA`, `SUSAN IB`, `TS_HUISGELD`) contain real first names and may be shared; full names, surnames, addresses and SINs never. `TOM_*` / `SUSAN_*` are real account names, not pseudonyms.
5. **Test runs.** No test runs per step; both suites run once in step 6, with pytest as `/opt/pm-venv/bin/python -m pytest tests/ --ignore=tests/e2e` (the e2e tests need playwright, which is not installed). The per-step checks stay.
