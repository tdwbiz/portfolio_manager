# Feature Spec: M8-Spec-2 — Extended Audit Trail (Action Log & Calculation Entry)

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m8-audit

---

**Architecture ref:** PM_PhaseB_Architecture_Final_v2.0.md
**Depends on:** M8-Spec-1 (audit trail, explainability, tax acknowledgement) already implemented and committed
**Predecessor:** M8-Spec-1 defined 5 audit tables; this spec adds 2 more and cleans up 2 redundant ones

---

## Context

M8-Spec-1 built the core audit infrastructure: 5 immutable tables that capture **what the system calculated** (scores, recommendations, simulations, regime state). However, two critical gaps remain in the audit story:

1. **"What happened AFTER the recommendation?"** — The system can answer "what did it recommend and why," but not "what did the user do with it?" Was it actioned? Deferred? Rejected? This is essential for compliance audits and for measuring system accuracy vs. actual outcomes.

2. **"What ran when?"** — The 5 M8-Spec-1 tables are granular (per-score, per-candidate, per-simulation), but there is no high-level summary of which calculation jobs ran on which dates, their overall status, and any errors. This is needed for operational monitoring and for the retention check to know which tables to scan.

Additionally, M8-Spec-1 left 4 "empty" model tables in the codebase — schemas that were never wired to management commands. This spec resolves them:

| Model | Status | Action |
|-------|--------|--------|
| `AuditEntry` | Schema exists, never written | **Keep + Spec it** — high-level calculation entry |
| `AuditRationale` | Schema exists, never written | **Merge** — fold into `AuditRecommendationLog` (already has `selection_rationale`) |
| `AuditActionLog` | Schema exists, never written | **Keep + Spec it** — post-recommendation action tracking |
| `AuditTaxLog` | Schema exists, never written | **Delete** — redundant with `TaxDecisionAcknowledgement` |

---

## Requirements

### 1. New / Modified Database Tables

#### 1.1 `rm_audit_entry` — Calculation Event Entry

**Purpose:** One row per management command execution that produces risk output. Provides a high-level "what ran when" summary.

**Written by:** Every management command that produces risk data:
- `calc_urgency` (M2-Spec-2)
- `calc_strategy_recommendations` (M4-Spec-1)
- `calc_regime` (M2-Spec-1)
- `calc_market_conditions` (M7-Spec-1)
- `run_backtest` (M5-Spec-1)
- `run_montecarlo` (M5-Spec-2)
- `run_stress_test` (M5-Spec-2)

**Model:**

```python
class AuditEntry(ImmutableModel):
    """
    Immutable record of a risk calculation event.
    Written by the respective management command at the end of each run.
    Unique on (model_name, calc_date) — re-runs for the same model+date
    are skipped (log WARNING, do not raise ImmutableRecordError).
    """
    calc_date = models.DateField()
    model_name = models.CharField(max_length=50)
    # 'calc_urgency', 'calc_strategy_recommendations', 'calc_regime',
    # 'calc_market_conditions', 'run_backtest', 'run_montecarlo', 'run_stress_test'
    
    input_snapshot = models.JSONField(default=dict)
    # Key inputs at run time: e.g. for calc_urgency: {config_version, date_range, entity_count}
    # For calc_strategy_recommendations: {eligible_count, rejected_count, calc_date}
    
    output_summary = models.JSONField(default=dict)
    # Key outputs: e.g. for calc_urgency: {scores_written, by_level: {Low: 5, Medium: 3, High: 1}}
    # For calc_strategy_recommendations: {recommendations_written, by_status: {active: 10, actioned: 2}}
    
    status = models.CharField(max_length=20, default='success')
    # 'success' | 'partial' | 'failed'
    
    error_message = models.TextField(blank=True, default='')
    # If status != 'success', the error message

    class Meta:
        db_table = 'rm_audit_entry'
        unique_together = [('model_name', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'model_name']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.model_name} — {self.calc_date} ({self.status})"
```

**Hook pattern (same as M8-Spec-1 §2):**

```python
def write_audit_entry(model_name: str, calc_date: date, input_snapshot: dict,
                      output_summary: dict, status: str = 'success',
                      error_message: str = '') -> None:
    """
    Create one rm_audit_entry row.
    Unique on (model_name, calc_date) — if a row already exists for this
    combination, log a WARNING and skip rather than raising ImmutableRecordError,
    since re-running the command is a normal operational activity.
    
    Wrapped in try/except: on ImmutableRecordError or any DB error, log
    logger.error(...) with full context and continue — does not raise.
    """
```

**Where to hook:**

At the **end** of each management command's `handle()` method, after all calculations complete:

```python
# In calc_urgency.py, at the end of handle():
write_audit_entry(
    model_name='calc_urgency',
    calc_date=target_date,
    input_snapshot={'config_version': config_version, 'entity_count': len(entities)},
    output_summary={'scores_written': scores_written, 'by_level': level_counts},
    status='success' if not errors else 'partial',
)

# In calc_strategy_recommendations.py, at the end of handle():
write_audit_entry(
    model_name='calc_strategy_recommendations',
    calc_date=target_date,
    input_snapshot={'eligible_count': eligible_count, 'rejected_count': rejected_count},
    output_summary={'recommendations_written': recs_written, 'by_status': status_counts},
    status='success' if not errors else 'partial',
)
```

#### 1.2 `rm_audit_action_log` — Post-Recommendation Action Log

**Purpose:** Immutable record of every user action taken on a strategy recommendation. Tracks what happened AFTER the system made a recommendation.

**Written by:**
- `strategy_mark_actioned` view (risk/views/strategy.py) — when status changes to 'actioned'
- Future views for 'deferred' and 'rejected' actions (not built yet, but schema prepared)

**Model:**

```python
class AuditActionLog(ImmutableModel):
    """
    Immutable record of a user action on a recommendation.
    Written by strategy_mark_actioned view and future action views.
    """
    recommendation = models.ForeignKey(
        'StrategyRecommendation', on_delete=models.PROTECT, related_name='action_logs'
    )
    action_type = models.CharField(max_length=30)
    # 'actioned', 'deferred', 'rejected', 'tax_acknowledged'
    
    performed_by = models.CharField(max_length=100)
    # Username or 'anonymous'
    
    reason = models.TextField(blank=True, default='')
    # Optional user-entered reason (e.g., "rejected: market changed", "deferred: waiting for earnings")
    
    tax_acknowledged = models.BooleanField(default=False)
    # Whether tax was acknowledged before this action

    class Meta:
        db_table = 'rm_audit_action_log'
        indexes = [
            models.Index(fields=['recommendation', 'action_type']),
            models.Index(fields=['performed_by']),
            models.Index(fields=['action_type', 'recommendation_id']),
        ]

    def __str__(self):
        return f"{self.performed_by} → {self.action_type} on {self.recommendation}"
```

**Hook (in strategy_mark_actioned view):**

```python
# In risk/views/strategy.py, after mark_actioned() succeeds:
try:
    AuditActionLog.objects.create(
        recommendation=recommendation,
        action_type='actioned',
        performed_by=actioned_by,
        reason='',  # User can optionally provide a reason in a future enhancement
        tax_acknowledged=recommendation.is_tax_triggering,  # True if tax was required
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditActionLog for rec_id=%s: %s", recommendation.id, e)
```

#### 1.3 Merge `AuditRationale` into `AuditRecommendationLog`

**Rationale:** `AuditRationale` stores `rationale_text` and `factors` per recommendation. `AuditRecommendationLog` already stores `selection_rationale` (plain English) and `cost_result_json`/`var_result_json` (factors). There is no functional difference — `AuditRationale` is redundant.

**Action:** Delete the `AuditRationale` model, its admin registration, and its references in `audit_retention_check.py`. The `selection_rationale` field on `AuditRecommendationLog` already serves this purpose.

#### 1.4 Delete `AuditTaxLog`

**Rationale:** `AuditTaxLog` stores tax acknowledgement events. `TaxDecisionAcknowledgement` (from M8-Spec-1 §1.6) already records this with richer fields: `presented_to_user`, `presented_at`, `acknowledged_at`, `acknowledged_by`, `tax_cost_estimate_presented`, `user_notes`. `AuditTaxLog` is completely redundant.

**Action:** Delete the `AuditTaxLog` model, its admin registration, and its references in `audit_retention_check.py`.

---

### 2. Management Command Hooks

#### 2.1 Hook into `calc_urgency`

At the end of `risk/management/commands/calc_urgency.py`, after all urgency scores are written:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=target_date,
        model_name='calc_urgency',
        input_snapshot={
            'config_version': config.get('version', 'unknown'),
            'entity_count': len(entities),
            'date_range': {'start': start_date.isoformat(), 'end': target_date.isoformat()},
        },
        output_summary={
            'scores_written': scores_written,
            'by_level': {level: count for level, count in level_counts.items()},
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for calc_urgency on %s: %s", target_date, e)
```

#### 2.2 Hook into `calc_strategy_recommendations`

At the end of `risk/management/commands/calc_strategy_recommendations.py`:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=target_date,
        model_name='calc_strategy_recommendations',
        input_snapshot={
            'eligible_count': eligible_count,
            'rejected_count': rejected_count,
            'calc_date': target_date.isoformat(),
        },
        output_summary={
            'recommendations_written': recs_written,
            'by_status': {status: count for status, count in status_counts.items()},
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for calc_strategy_recommendations on %s: %s", target_date, e)
```

#### 2.3 Hook into `calc_regime`

At the end of `risk/management/commands/calc_regime.py`:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=target_date,
        model_name='calc_regime',
        input_snapshot={
            'regime_model': config.get('model', 'unknown'),
        },
        output_summary={
            'quadrant': quadrant,
            'confidence_score': float(confidence_score),
            'transition_flag': transition_flag,
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for calc_regime on %s: %s", target_date, e)
```

#### 2.4 Hook into `calc_market_conditions`

At the end of `risk/management/commands/calc_market_conditions.py`:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=target_date,
        model_name='calc_market_conditions',
        input_snapshot={
            'symbols_count': len(symbols),
        },
        output_summary={
            'conditions_written': conditions_written,
            'death_cross_count': death_cross_count,
            'golden_cross_count': golden_cross_count,
            'oversold_count': oversold_count,
            'overbought_count': overbought_count,
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for calc_market_conditions on %s: %s", target_date, e)
```

#### 2.5 Hook into `run_backtest`

At the end of `risk/management/commands/run_backtest.py`:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=run_date,
        model_name='run_backtest',
        input_snapshot={
            'symbol': symbol,
            'strategy': strategy_code,
            'scenario': scenario_name,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
        },
        output_summary={
            'results_written': results_written,
            'sharpe_ratio': float(final_metrics.get('sharpe_ratio', 0)),
            'max_drawdown': float(final_metrics.get('max_drawdown', 0)),
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for run_backtest on %s: %s", run_date, e)
```

#### 2.6 Hook into `run_montecarlo`

At the end of `risk/management/commands/run_montecarlo.py`:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=run_date,
        model_name='run_montecarlo',
        input_snapshot={
            'symbol': symbol,
            'strategy': strategy_code,
            'scenarios': scenarios_count,
            'iterations': iterations,
        },
        output_summary={
            'results_written': results_written,
            'mean_return': float(final_metrics.get('mean_return', 0)),
            'var_95': float(final_metrics.get('var_95', 0)),
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for run_montecarlo on %s: %s", run_date, e)
```

#### 2.7 Hook into `run_stress_test`

At the end of `risk/management/commands/run_stress_test.py`:

```python
from risk.models import AuditEntry

# ... existing code ...

# Write audit entry
try:
    AuditEntry.objects.create(
        calc_date=run_date,
        model_name='run_stress_test',
        input_snapshot={
            'symbol': symbol,
            'strategy': strategy_code,
            'scenario': scenario_name,
            'shocks': shocks_json,
        },
        output_summary={
            'results_written': results_written,
            'pnl_impact': float(final_metrics.get('pnl_impact', 0)),
        },
        status='success' if not errors else 'partial',
        error_message=str(errors[0]) if errors else '',
    )
except (ImmutableRecordError, Exception) as e:
    logger.error("Failed to write AuditEntry for run_stress_test on %s: %s", run_date, e)
```

---

### 3. View Hook: `strategy_mark_actioned`

In `risk/views/strategy.py`, after `mark_actioned()` succeeds:

```python
from risk.models import AuditActionLog, ImmutableRecordError

# ... existing code ...

if request.method == 'POST':
    allowed, reason = can_mark_actioned(recommendation)
    if not allowed:
        messages.error(request, reason)
    else:
        actioned_by = (
            request.user.username if request.user.is_authenticated else 'anonymous'
        )
        mark_actioned(recommendation, actioned_by)
        
        # Write audit action log
        try:
            AuditActionLog.objects.create(
                recommendation=recommendation,
                action_type='actioned',
                performed_by=actioned_by,
                reason='',
                tax_acknowledged=recommendation.is_tax_triggering,
            )
        except (ImmutableRecordError, Exception) as e:
            logger.error("Failed to write AuditActionLog for rec_id=%s: %s", recommendation.id, e)
        
        messages.success(request, "Recommendation marked as actioned.")
    return redirect('risk:strategy_report', rec_id=rec_id)
```

---

### 4. Cleanup: Delete Redundant Models

#### 4.1 Delete `AuditRationale`

Remove from `risk/models.py`:
```python
class AuditRationale(ImmutableModel):
    """..."""
    # All fields and class
```

Remove from `risk/admin.py`:
```python
@admin.register(AuditRationale)
class AuditRationaleAdmin(ReadOnlyAuditAdmin):
    # All admin class
```

Remove from `risk/management/commands/audit_retention_check.py`:
- Import line
- Registration in `tables_to_check` list
- Comment reference

#### 4.2 Delete `AuditTaxLog`

Remove from `risk/models.py`:
```python
class AuditTaxLog(ImmutableModel):
    """..."""
    # All fields and class
```

Remove from `risk/admin.py`:
```python
@admin.register(AuditTaxLog)
class AuditTaxLogAdmin(ReadOnlyAuditAdmin):
    # All admin class
```

Remove from `risk/management/commands/audit_retention_check.py`:
- Import line
- Registration in `tables_to_check` list
- Comment reference

---

### 5. Migration Strategy

1. **Migration 0015:** Add `AuditEntry` model, add `AuditActionLog` model
2. **Migration 0016:** Delete `AuditRationale` model, delete `AuditTaxLog` model
   - This will drop tables `rm_audit_rationale` and `rm_audit_tax_log`
   - Review migration carefully — confirm no data loss concerns

---

### 6. Tests

File: `risk/tests/test_audit_extended.py`

```python
from datetime import date
from django.test import TestCase
from django.utils import timezone
from risk.models import (
    AuditEntry, AuditActionLog, ImmutableRecordError,
    StrategyRecommendation, StrategyTemplate, UrgencyScore,
)


class AuditEntryTests(TestCase):

    def test_entry_written_on_urgency_calc(self):
        """Run calc_urgency and assert one AuditEntry row exists."""
        pass  # Implement with M2-Spec-2 fixtures

    def test_entry_written_on_recommendation_calc(self):
        """Run calc_strategy_recommendations and assert one AuditEntry row exists."""
        pass

    def test_entry_unique_on_model_date(self):
        """AuditEntry is unique on (model_name, calc_date) — re-runs are skipped."""
        AuditEntry.objects.create(
            calc_date=date(2026, 6, 2),
            model_name='calc_urgency',
            input_snapshot={},
            output_summary={},
            status='success',
        )
        # Re-run should not raise — it should log WARNING and skip
        # (This is enforced by the hook, not the model itself)

    def test_entry_cannot_be_updated(self):
        """AuditEntry.save() raises ImmutableRecordError on update."""
        entry = AuditEntry.objects.create(
            calc_date=date(2026, 6, 2),
            model_name='calc_urgency',
            input_snapshot={},
            output_summary={},
            status='success',
        )
        entry.status = 'failed'
        with self.assertRaises(ImmutableRecordError):
            entry.save()

    def test_entry_cannot_be_deleted(self):
        """AuditEntry.delete() raises ImmutableRecordError."""
        entry = AuditEntry.objects.create(
            calc_date=date(2026, 6, 2),
            model_name='calc_urgency',
            input_snapshot={},
            output_summary={},
            status='success',
        )
        with self.assertRaises(ImmutableRecordError):
            entry.delete()


class AuditActionLogTests(TestCase):

    def setUp(self):
        """Create a minimal StrategyRecommendation for FK references."""
        template = StrategyTemplate.objects.create(
            code='TEST',
            name='Test Template',
            description='Test',
            is_active=True,
        )
        # Create urgency score first (required by recommendation FK chain)
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='TEST::AAPL',
            symbol='AAPL',
            calc_date=date(2026, 6, 2),
            urgency_level='Medium',
            score_raw=45.0,
            component_scores_json='{}',
            weights_config_json='{}',
            input_metrics_json='{}',
        )
        self.recommendation = StrategyRecommendation.objects.create(
            symbol='AAPL',
            account_id='TEST',
            owner='TEST_USER',
            calc_date=date(2026, 6, 2),
            strategy_template=template,
            rank=1,
            status='active',
            is_tax_triggering=False,
        )

    def test_action_log_written_on_mark_actioned(self):
        """Mark recommendation as actioned and assert one AuditActionLog row."""
        from risk.services.tax_acknowledgement import mark_actioned
        mark_actioned(self.recommendation, 'testuser')
        self.assertEqual(AuditActionLog.objects.filter(
            recommendation=self.recommendation,
            action_type='actioned',
        ).count(), 1)

    def test_action_log_cannot_be_updated(self):
        """AuditActionLog.save() raises ImmutableRecordError on update."""
        log = AuditActionLog.objects.create(
            recommendation=self.recommendation,
            action_type='actioned',
            performed_by='testuser',
            tax_acknowledged=False,
        )
        log.reason = 'modified'
        with self.assertRaises(ImmutableRecordError):
            log.save()

    def test_action_log_cannot_be_deleted(self):
        """AuditActionLog.delete() raises ImmutableRecordError."""
        log = AuditActionLog.objects.create(
            recommendation=self.recommendation,
            action_type='actioned',
            performed_by='testuser',
            tax_acknowledged=False,
        )
        with self.assertRaises(ImmutableRecordError):
            log.delete()

    def test_action_log_tax_flag(self):
        """Tax-triggering recommendation sets tax_acknowledged=True."""
        self.recommendation.is_tax_triggering = True
        self.recommendation.save()
        # mark_actioned will succeed (tax acknowledged via TaxDecisionAcknowledgement)
        from risk.services.tax_acknowledgement import mark_actioned
        mark_actioned(self.recommendation, 'testuser')
        log = AuditActionLog.objects.get(recommendation=self.recommendation)
        self.assertTrue(log.tax_acknowledged)
```

---

### 7. Admin Registration

Register `AuditEntry` and `AuditActionLog` in `risk/admin.py`:

```python
@admin.register(AuditEntry)
class AuditEntryAdmin(ReadOnlyAuditAdmin):
    list_display = ['model_name', 'calc_date', 'status', 'created_at']
    list_filter = ['model_name', 'status']
    search_fields = ['model_name', 'error_message']
    readonly_fields = [f.name for f in AuditEntry._meta.fields]


@admin.register(AuditActionLog)
class AuditActionLogAdmin(ReadOnlyAuditAdmin):
    list_display = ['recommendation', 'action_type', 'performed_by', 'tax_acknowledged', 'created_at']
    list_filter = ['action_type', 'tax_acknowledged', 'performed_by']
    search_fields = ['recommendation__symbol', 'performed_by', 'reason']
    readonly_fields = [f.name for f in AuditActionLog._meta.fields]
```

---

### 8. Update `audit_retention_check.py`

Update the `tables_to_check` list to include `AuditEntry` and `AuditActionLog`, and remove `AuditRationale` and `AuditTaxLog`:

```python
# Models with calc_date: AuditEntry, AuditScoreSnapshot, AuditRecommendationLog,
# AuditSimulationRun, AuditRegimeLog
# Models with created_at only: AuditActionLog, AuditRetentionLog
# Models with acknowledged_at: TaxDecisionAcknowledgement

tables_to_check = [
    ('AuditEntry', AuditEntry.objects),
    ('AuditScoreSnapshot', AuditScoreSnapshot.objects),
    ('AuditRecommendationLog', AuditRecommendationLog.objects),
    ('AuditSimulationRun', AuditSimulationRun.objects),
    ('AuditRegimeLog', AuditRegimeLog.objects),
    ('AuditActionLog', AuditActionLog.objects),
    ('AuditRetentionLog', AuditRetentionLog.objects),
    ('TaxDecisionAcknowledgement', TaxDecisionAcknowledgement.objects),
]
```

---

### 9. Manual Steps Required (in order)

- [ ] Verify M8-Spec-1 tests pass: `python manage.py test risk.tests.test_audit --settings=portfolio_manager.settings_test`
- [ ] Add `AuditEntry` and `AuditActionLog` models to `risk/models.py` (if not already present)
- [ ] Add hooks per §2.1–§2.7 to all 7 management commands + 1 view
- [ ] Delete `AuditRationale` and `AuditTaxLog` from models.py, admin.py, audit_retention_check.py
- [ ] `python manage.py makemigrations risk`
- [ ] Review generated migration — confirm no destructive changes to existing tables
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_dev`
- [ ] Register admin classes per §7
- [ ] `python manage.py daily_risk_refresh --skip-data-refresh --date 2026-06-02 --settings=portfolio_manager.settings_dev`
- [ ] Verify in SSMS: `SELECT * FROM rm_audit_entry WHERE calc_date='2026-06-02'` — should have 7 rows (one per model)
- [ ] Verify in SSMS: `SELECT * FROM rm_audit_action_log` — should have rows for any actioned recommendations
- [ ] `python manage.py test risk.tests.test_audit_extended --settings=portfolio_manager.settings_test`
- [ ] Run the **full** test suite: `python manage.py test risk core imports --settings=portfolio_manager.settings_test`
- [ ] `git add -A && git commit -m "feat: M8-Spec-2 extended audit trail — action log, calculation entry, cleanup redundant models"`
- [ ] `git push origin feature/risk-m8-audit`

---

### 10. Human Approval Required

1. Verify `rm_audit_entry` has one row per model per date (7 rows for a full `daily_risk_refresh`)
2. Verify `rm_audit_action_log` captures the actioned recommendation with correct `tax_acknowledged` flag
3. Confirm `rm_audit_rationale` and `rm_audit_tax_log` tables were dropped cleanly (no data loss concerns)
4. Review the `selection_rationale` field on `AuditRecommendationLog` — confirm it provides sufficient rationale detail (since `AuditRationale` is being merged away)

---

*M8-Spec-2 | Portfolio Manager Phase B | June 2026 | tdwdev*
