# Phase C — Code Review Remediation Plan

**Project:** portfolio_manager  
**Customer:** tdwdev  
**Author:** Qwen3.5 35B Q6 (from code review dated 2026-06-27)  
**Feature branch:** feature_code_review_fixes  
**Status:** Draft for human review — CrewAI execution pending

---

## Overview

Phase C addresses all findings from the comprehensive code review report **"Review from Qwen36_35BQ6_ 20260627.md"**. The work is split into four phases, one per severity category, to allow incremental deployment and testing.

| Phase | Category | Issues | Estimated Specs | Priority |
|-------|----------|--------|-----------------|----------|
| Phase C1 | Severe (S1–S4) | 4 issues | 2 specs | P0 — Critical |
| Phase C2 | Urgent (U1–U5) | 5 issues | 2 specs | P1 — High |
| Phase C3 | Moderate (M1–M6) | 6 issues | 2 specs | P2 — Medium |
| Phase C4 | Low (L1–L6) | 6 issues | 1 spec | P3 — Nice-to-have |

---

## Phase C1 — Severe Issues (P0)

**Goal:** Fix data integrity, security, and configuration-critical bugs.

| Issue | Description | Spec |
|-------|-------------|------|
| S1 | Duplicate detection ignores `tx_hash` — manual field comparison instead of canonical SHA-256 | `C1-Spec-1_tx_hash_dedup.md` |
| S2 | Hardcoded SQLite fallback in `auto_import.py` standalone mode | (combined into C1-Spec-1) |
| S3 | Open redirect vulnerability in login `next` parameter | `C1-Spec-2_login_security.md` |
| S4 | Missing CSRF/rate-limiting on PDF import endpoints | (combined into C1-Spec-2) |

**Order:** C1-Spec-1 first (data integrity), then C1-Spec-2 (security).

---

## Phase C2 — Urgent Issues (P1)

**Goal:** Improve robustness of import pipeline and prevent data inconsistencies.

| Issue | Description | Spec |
|-------|-------------|------|
| U1 | Statement date uses import time instead of actual file date | `C2-Spec-1_statement_date.md` |
| U2 | LLM failure halts entire import — no fallback | (combined into C2-Spec-1) |
| U3 | No rate limiting on PDF import endpoints | `C2-Spec-2_upload_rate_limit.md` |
| U4 | Incorrect currency assumptions for non-CAD/USD | (combined into C2-Spec-2) |
| U5 | Watcher filename-based classification is brittle | (combined into C2-Spec-2) |

**Order:** C2-Spec-1 first (data accuracy), then C2-Spec-2 (robustness).

---

## Phase C3 — Moderate Issues (P2)

**Goal:** Improve performance, code quality, and maintainability.

| Issue | Description | Spec |
|-------|-------------|------|
| M1 | Transaction type normalization lacks canonical mapping | `C3-Spec-1_transaction_types.md` |
| M2 | Portfolio view loads all transactions into memory | (combined into C3-Spec-1) |
| M3 | Missing database indexes on filtered fields | `C3-Spec-2_performance_indexes.md` |
| M4 | No input sanitization on symbol field | (combined into C3-Spec-2) |
| M5 | Hardcoded CDN URLs for Bootstrap/Chart.js | (combined into C3-Spec-2) |
| M6 | Risk dashboard returns empty placeholder | (combined into C3-Spec-2) |

**Order:** C3-Spec-1 first (performance), then C3-Spec-2 (quality & UX).

---

## Phase C4 — Low Issues (P3)

**Goal:** Polish, documentation, and operational improvements.

| Issue | Description | Spec |
|-------|-------------|------|
| L1 | Polling-based watcher could use `watchdog` | `C4-Spec-1_filesystem_events.md` |
| L2 | Symbol length constraint may be insufficient | (combined into C4-Spec-1) |
| L3 | No automated tests for VL parsers | (combined into C4-Spec-1) |
| L4 | Missing `__all__` in module `__init__.py` | (combined into C4-Spec-1) |
| L5 | No health check endpoint | (combined into C4-Spec-1) |
| L6 | Template variable naming inconsistency | (combined into C4-Spec-1) |

---

## Execution Notes for CrewAI

1. **Each spec is independent** — CrewAI agents can work on them in order within a phase, but phases should be merged sequentially.
2. **No code changes to existing tests** — CrewAI should add new tests, not modify existing ones.
3. **All specs follow the PhaseA template** (`00_spec_template.md`) for consistency.
4. **Human approval required** before any git push or DB migration.
5. **Reference the code review report** at `c:\AI-DevTeam\projects\portfolio_manager\Review from Qwen36_35BQ6_ 20260627.md` for full context on each issue.

---

## Manual Steps Required (After All Phases)

- [ ] `python manage.py makemigrations` (after C1-Spec-1, C3-Spec-2, C4-Spec-1)
- [ ] `python manage.py migrate --settings=...`
- [ ] Run full test suite: `python run_tests.py`
- [ ] Review and merge each phase branch in order: C1 → C2 → C3 → C4
- [ ] Push to `feature_rbc_pdf_fixes`
- [ ] Human approval required before any git push or DB changes
