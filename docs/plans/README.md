# Plans

One file per feature: `docs/plans/<feature>.md`. The planning model (Flash-Next, via Pi) writes it, tdw reviews it, and the coding model (Qwen3.8 27B, via Pi) implements it one step at a time.

Each plan contains:

- **Goal**: what the feature does, in plain words.
- **What changes**: files, models, views, templates.
- **Migrations** needed, if any.
- **Tests** to add or change.
- **Steps**: small numbered steps, each one implementable and testable on its own.
- **Open questions** at the end.

Rules:

- Implement one step at a time; run the tests after each step; commit per step.
- Don't change the plan while implementing. If a step turns out wrong, stop and say so; the plan is revised first.
- The plan file stays in the repo as the record of what was decided.
