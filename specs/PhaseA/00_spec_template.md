# Feature Spec: <Short Title>

Project name: <project_folder_name>
Customer: tdwdev
Feature branch: feature/<branch-name>

---

## Context

What exists right now that is relevant to this spec.
Reference previous spec numbers if this builds on earlier work.
Example: "Spec 02 added IB import. The core_transaction table exists in PortfolioDEV."

If this is the first spec for the project, write: "New project — no existing code."

---

## Stack

- Language / Framework: <e.g. Python 3.12 / Django 5.1>
- Database: <e.g. MSSQL — existing DB "PortfolioDEV" via .env>
- Existing project location: <e.g. C:\AI-DevTeam\projects\portfolio_manager, or "none — new project">

---

## Requirements

Number each requirement. Use sub-items for detail.

1. **First requirement**
   - Detail
   - Detail

2. **Second requirement**
   - Detail

3. **Tests** *(always include)*
   - What to test
   - Fixture or sample data needed
   - Which test runner / settings file to use

---

## Constraints

Hard rules the agent must not violate.

- Do NOT run migrations — that is a manual step
- Do NOT push to git — list it under Manual Steps Required
- Follow PEP8, add type hints to all new functions
- <add any stack-specific constraints>

---

## Manual Steps Required

Steps the human must run after the agent finishes. List them here so the agent
copies them verbatim into its run summary.

- [ ] `python manage.py makemigrations`
- [ ] `python manage.py migrate --settings=...`
- [ ] Review and push feature branch: `git push origin feature/<branch-name>`
- [ ] <any other manual step>

---

## Human Approval Required

State exactly what must be approved before the agent proceeds.
Example: "Human approval required before any git push or DB changes."
Delete this section if no approval gate is needed.
