# Repo hygiene (phase 0, step 1)

Source: `docs/architecture.md` §12, phase 0 step 1. Where the audits and `docs/architecture.md` disagree, `docs/architecture.md` wins.

## Goal

Tidy the repo bookkeeping before any code clean-up starts: correct dependency list, correct ignore rules, and one authoritative agent-instructions file. **No behaviour changes — no code is touched.** Five items only:

1. `mssql-django` in `requirements.txt`, pinned to the installed version.
2. `.env.*` in `.gitignore`, `.env.example` stays tracked.
3. The "sharing with a cloud AI" rule in `AGENTS.md`, worded per the fingerprint design in `docs/architecture.md` §5.
4. The JSON-log correction in `AGENTS.md` (audit 2, Q11).
5. `CONVENTIONS.md` folded into `AGENTS.md` and deleted (audit 3, Q7).

Explicitly **out of scope** (later steps): anything `RBC_ACCOUNTS`-related, deleting `events.jsonl` / `calc_march.py` / `login_test.html` or any other junk, moving files to history, stale-doc edits, and the `django-mssql-backend` question (see Open questions).

## What changes (files)

| File | Change |
|---|---|
| `requirements.txt` | Add `mssql-django==1.8.0` (verified: `/opt/pm-venv/bin/pip show mssql-django` → 1.8.0). Leave everything else as is. |
| `.gitignore` | In the "Environment variables" block, replace the `.env.local` / `.env.*.local` lines with `.env` + `.env.*` + `!.env.example` (broader glob, explicit un-ignore so `.env.example` stays tracked). |
| `AGENTS.md` | Three edits, no other rewrites: (a) new short section "Sharing data with a cloud AI"; (b) fix the JSON-log bullet under "Import commands"; (c) fold in the one live rule from `CONVENTIONS.md`. |
| `CONVENTIONS.md` | Deleted (content folded into `AGENTS.md`). |

No migrations (no models touched).

### Content notes

**(a) Cloud-AI rule.** New short `## Sharing data with a cloud AI` section in `AGENTS.md`, consistent with `docs/architecture.md` §5 (the app stores no account numbers; accounts are identified by name; the only digits shown anywhere are last-4 on the accounts page). Wording to implement:

- Never paste into a cloud AI: account numbers (not even last-4 digits), owner real names, `.env` content, log lines, `docs/reconciliation/` content, statement files (CSV or PDF), or VL JSON output from `processed/json_logs/`.
- When describing a problem, quote the **file and line** (and the account **name**, per the fingerprint design in `docs/architecture.md`), not data rows.
- In examples, docs and prompts use the fake accounts `90000001`–`90000008` (`ACCOUNT_A`–`ACCOUNT_H`) and the `TOM_*` / `SUSAN_*` pseudonyms.
- Local models (ports 11435 / 11438, no internet path) are exempt.

**(b) JSON-log correction (audit 2 Q11).** The bullet currently reads "holds JSON only for **failed** imports". Per audit 2 §(JSON logs) and Q11, `manage_inbox_import` writes a JSON log to `processed/json_logs/` for **every** processed PDF (`*_ERROR.json` when the parse has errors); only the deprecated commands write on error alone. Correct the bullet to say exactly that; `FileImportLog` still records every import.

**(c) CONVENTIONS.md fold-in.** `CONVENTIONS.md` is one line: "Strictly avoid hardcoded strings for configuration. Always use environment variables." `AGENTS.md` does not currently state this rule (the env-driven examples like `RBC_ACCOUNTS`/`VL_MODEL_*` imply it but never say it), so add it once, e.g. as a bullet in the "Environment" section. Nothing else in `CONVENTIONS.md` to carry over.

**References to `CONVENTIONS.md`.** The only mentions in the repo are in `docs/architecture.md` §12 and `docs/audit/03-commands-scripts-docs.md` — both are decision records describing this very task; leave them. No code, config or live doc references it, so nothing else to update.

## Tests to run

No code changes, so the suites are regression guards only. After each step:

```bash
/opt/pm-venv/bin/python manage.py test core --settings=portfolio_manager.settings_test   # ~35 s, 220 tests
/opt/pm-venv/bin/python -m pytest tests/ -v
```

Plus step-specific checks:

- Step 1: `/opt/pm-venv/bin/pip show mssql-django` still reports 1.8.0 (pin matches the installed version).
- Step 2: `git check-ignore -v .env .env.local .env.foo` → ignored; `git check-ignore .env.example` → **not** ignored; `git ls-files .env.example` still lists the file.
- Steps 3–5: manual read of `AGENTS.md` (each rule appears exactly once); `git grep -l CONVENTIONS` returns only the two record files above.

## Steps

Each step is one commit on this branch (`chore/repo-hygiene`).

1. **requirements.txt**: add `mssql-django==1.8.0`. Verify the pin against `/opt/pm-venv/bin/pip show mssql-django`. Commit.
2. **.gitignore**: replace the env-var block entries with `.env`, `.env.*` and `!.env.example`. Verify with `git check-ignore` as above. Commit.
3. **AGENTS.md — cloud-AI rule**: add the "Sharing data with a cloud AI" section (wording above, adjusted to fit the file's style). Commit.
4. **AGENTS.md — JSON-log correction**: fix the JSON-log bullet per audit 2 Q11. Commit.
5. **CONVENTIONS.md fold-in**: add the env-vars rule to `AGENTS.md`, `git rm CONVENTIONS.md`, confirm no live references remain. Commit.
6. Run both test suites; if green, the branch is done — report and stop.

## Open questions

1. `requirements.txt` still lists `django-mssql-backend>=2.0`, which `docs/architecture.md` §13 calls wrong ("the app actually needs `mssql-django`"). This task only says *add* `mssql-django` — should the stale `django-mssql-backend` line be removed here (step 1) or left for a later step?
2. Should the cloud-AI rule also live verbatim in any project-level `AGENTS.md` a user might add (e.g. `~/.pi/agent/AGENTS.md`), or is the repo file the only place? (Plan assumes repo only.)
3. `.env.example` currently passes the new ignore rule via `!.env.example`; confirm tdw is OK with the negation pattern rather than listing each variant explicitly.
