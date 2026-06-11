# Feature Spec: M8-Spec-1 — Audit Trail, Explainability & Tax Acknowledgement Workflow

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m8-audit

---

**Architecture ref:** PM_PhaseB_Architecture_Final_v2.0.md
**Depends on:** All other Phase B modules functionally complete and verified —
M2-Spec-2 (`rm_urgency_score`), M4-Spec-1 (`rm_strategy_recommendation`,
`rm_owner_config`), M5-Spec-1/M5-Spec-2 (`rm_backtest_run`/`rm_backtest_result`,
`rm_simulation_run`/`rm_simulation_result`), M2-Spec-1 (`rm_regime_state`,
`rm_market_condition`), M6-Spec-2 (security detail, strategy report templates,
and the tax acknowledgement view stub)

---

## Context

This is the final spec of the 14-spec Phase B plan. Every prior module produces a
number, a score, or a recommendation — but none of them, by themselves, answer the
two questions that actually matter to TOM and SUSAN:

1. **"Why is AAPL High risk today?"** — a plain-English explanation of *which*
   factor (M1 metrics, security driver, regime, or market condition) drove the
   score, ranked by contribution, with historical context.
2. **"What did the system know when it told me to do this, and did I acknowledge
   the tax consequences before acting on it?"** — an immutable record sufficient
   to reconstruct, a year from now, exactly what inputs, weights, and candidates
   were considered for any score or recommendation, and proof that the tax cost
   was presented to and acknowledged by the relevant owner before the
   recommendation was actioned.

M8 does **not** introduce new calculations. It is almost entirely additive: small
hooks into the existing M2/M4/M5 management commands that write a parallel,
immutable audit record alongside the existing mutable output tables, plus two new
user-facing views (explainability and tax acknowledgement) and one maintenance
command (retention check).

**Important — coordination with M6-Spec-2:** M6-Spec-2 already built
`risk/strategy/<rec_id>/acknowledge/` (the `strategy_acknowledge` view) and its
template, and noted that `rm_tax_decision_acknowledgement` model placement was
deferred to M8. This spec is the canonical home for that model. If M6-Spec-2 was
implemented with a placeholder model, this spec's migration reconciles it
(rename/alter in place — do not create a second table). M6-Spec-2 also referenced
a `risk:strategy_mark_actioned` URL that was never defined — this spec adds it,
gated by the tax-acknowledgement rule.

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- Project location: `C:\AI-DevTeam\projects\portfolio_manager`

---

## Requirements

### 1. New Database Tables

All audit tables use the `rm_audit_` prefix except `rm_tax_decision_acknowledgement`
(named per the architecture doc). All audit tables are **append-only**: `save()` is
overridden to raise on any attempt to update an existing row, and `delete()` is
overridden to raise unconditionally. A shared base class enforces this.

#### 1.1 Shared Immutability Base Class

```python
class ImmutableRecordError(Exception):
    """Raised when code attempts to UPDATE or DELETE an immutable audit record."""
    pass


class ImmutableModel(models.Model):
    """
    Abstract base for append-only audit tables.

    - save(): if self.pk is set AND a row with that pk already exists in the
      database, raise ImmutableRecordError. New inserts (pk=None, or pk set but
      not yet present — e.g. explicit pk assignment patterns) proceed normally.
    - delete(): always raises ImmutableRecordError. No audit row is ever deleted
      by application code. (DBA-level deletion for retention purposes, if ever
      required, happens outside the ORM and outside this spec's scope — see §6
      retention check, which only REPORTS, never deletes.)
    """
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and type(self).objects.filter(pk=self.pk).exists():
            raise ImmutableRecordError(
                f"{type(self).__name__} records are immutable — "
                f"cannot update pk={self.pk}"
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(
            f"{type(self).__name__} records are immutable — deletion not permitted"
        )
```

#### 1.2 `rm_audit_score_snapshot`

Created alongside every `rm_urgency_score` write (one audit row per urgency score
row, same `(entity_type, entity_id, symbol, calc_date)` granularity).

```python
class AuditScoreSnapshot(ImmutableModel):
    """
    Immutable snapshot of every input that produced a given rm_urgency_score row.
    Written by calc_urgency (M2-Spec-2) at the same time as the UrgencyScore
    UPSERT — see §4.1 for the hook.

    Note: rm_urgency_score itself is UPSERTed (history is overwritten on
    recalculation of the same calc_date). This table is the permanent record —
    even if rm_urgency_score for a given calc_date is later recalculated with
    different config, the original snapshot remains here, timestamped.
    """
    entity_type = models.CharField(max_length=20)
    entity_id = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20, blank=True)
    calc_date = models.DateField()

    urgency_level = models.CharField(max_length=10)
    score_raw = models.DecimalField(max_digits=6, decimal_places=2)

    component_scores_json = models.TextField()
    # Full component breakdown, copied verbatim from rm_urgency_score at write time:
    # {"m1": {"score": ..., "weight": ..., "inputs": {...}}, "driver": {...},
    #  "regime": {...}, "market": {...}}

    weights_at_calc_json = models.TextField()
    # Snapshot of rm_scoring_config weight_* and threshold_* keys at calc time.
    # E.g. {"weight_m1_metrics": 0.35, "weight_security_driver": 0.20,
    #       "weight_regime": 0.25, "weight_market_condition": 0.20,
    #       "threshold_low": 25, "threshold_medium": 50, "threshold_high": 75,
    #       "regime_transition_dampener": 0.80, "earnings_proximity_boost": 10}

    input_metrics_json = models.TextField()
    # Raw M1/M2-Spec-1 values used as inputs to the four components, for symbols
    # (blank '{}' for entity_type in ('account','owner','portfolio') — those are
    # roll-ups with no direct M1/regime/market inputs of their own):
    # {"var_parametric_95": 0.0312, "current_drawdown_from_252d_peak": -0.08,
    #  "vol_252d": 0.21, "regime_quadrant": "Q3", "regime_sensitivity_multiplier": 1.20,
    #  "rsi_14": 68.0, "crossover_type": "none", "market_phase": "uptrend",
    #  "asset_class_baseline": 45, "sector_cyclicality": 40, "liquidity_tier": 15,
    #  "earnings_proximity_flag": false}

    written_at = models.DateTimeField(auto_now_add=True)
    # Wall-clock time this audit record was created — distinct from calc_date,
    # which is the *as-of* date of the calculation.

    class Meta:
        db_table = 'rm_audit_score_snapshot'
        indexes = [
            models.Index(fields=['entity_type', 'entity_id', 'symbol', 'calc_date']),
            models.Index(fields=['calc_date']),
            models.Index(fields=['written_at']),
        ]
```

#### 1.3 `rm_audit_recommendation_log`

Created alongside every `rm_strategy_recommendation` write — **one audit row per
candidate evaluated**, not just the selected/top-ranked one. This is the only way
M4's "we considered X and rejected it because Y" reasoning survives.

```python
class AuditRecommendationLog(ImmutableModel):
    """
    Immutable record of every strategy candidate considered by
    calc_strategy_recommendations (M4-Spec-1) for a given position on a given
    calc_date — including candidates that were filtered out before costing,
    and candidates that were costed but ranked below the winner.
    """
    symbol = models.CharField(max_length=20)
    account_id = models.CharField(max_length=50)
    owner = models.CharField(max_length=50)
    calc_date = models.DateField()

    input_urgency_level = models.CharField(max_length=10)
    input_urgency_score = models.DecimalField(max_digits=6, decimal_places=2)
    input_regime_quadrant = models.CharField(max_length=2)

    strategy_template_code = models.CharField(max_length=50)
    # rm_strategy_template.code — stored as a string, not FK, so the audit log
    # remains readable even if a template is later deactivated/renamed.

    was_eligible = models.BooleanField()
    # False if get_eligible_templates() filtered this template out before costing
    # (e.g. options not available, CEF-ineligible, LIF-ineligible, urgency/regime
    # tag mismatch, min_unrealised_gain_usd not met).
    ineligibility_reason = models.CharField(max_length=200, blank=True)
    # E.g. "options_not_available", "cef_ineligible", "lif_rrif_ineligible",
    # "urgency_level_not_applicable", "regime_quadrant_not_applicable",
    # "min_unrealised_gain_not_met". Blank if was_eligible=True.

    rank = models.IntegerField(null=True, blank=True)
    # NULL if was_eligible=False (never ranked). Otherwise matches
    # rm_strategy_recommendation.rank for the corresponding row.

    estimated_strategy_cost_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    tax_cost_estimate_usd = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    estimated_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    cost_per_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    is_tax_triggering = models.BooleanField(null=True)

    selection_rationale = models.CharField(max_length=300, blank=True)
    # Plain English. For the rank-1 candidate: why it won.
    # For others: why it ranked where it did, relative to the winner.
    # E.g. "Selected: lowest cost per dollar of VaR reduction among eligible,
    #       non-tax-penalised candidates."
    # E.g. "Ranked 3rd: tax_cost_estimate_usd ($43,219) with penalty multiplier
    #       1.0 made cost_per_var_reduction (8.64) far higher than rank-1 (0.21)."

    constraints_applied_json = models.TextField()
    # Copy of rm_strategy_recommendation.constraints_applied_json for ranked
    # candidates; for ineligible candidates, a one-element list containing
    # ineligibility_reason.

    written_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_audit_recommendation_log'
        indexes = [
            models.Index(fields=['symbol', 'account_id', 'calc_date']),
            models.Index(fields=['calc_date', 'was_eligible']),
            models.Index(fields=['strategy_template_code']),
        ]
```

#### 1.4 `rm_audit_simulation_run`

A unified, lightweight audit entry written alongside **both** M5-Spec-1
(`rm_backtest_run`/`rm_backtest_result`) and M5-Spec-2
(`rm_simulation_run`/`rm_simulation_result`) outputs. Provides a single table to
query "show me every simulation ever run against this symbol" without joining
across the two M5 schemas.

```python
class AuditSimulationRun(ImmutableModel):
    """
    Immutable cross-reference entry for every M5 backtest or Monte Carlo /
    stress test run. Does not duplicate the full result — points back to the
    M5 tables via loosely-typed reference fields (no FK, so this table survives
    even if M5 result rows are ever pruned).
    """
    simulation_family = models.CharField(max_length=20)
    # 'backtest' (M5-Spec-1, rm_backtest_run/result) |
    # 'montecarlo' (M5-Spec-2, rm_simulation_run/result, simulation_type='montecarlo') |
    # 'stress_test' (M5-Spec-2, rm_simulation_run/result, simulation_type IN
    #   ('stress_test','custom_stress'))

    source_run_id = models.IntegerField()
    # rm_backtest_run.id or rm_simulation_run.id depending on simulation_family

    symbol = models.CharField(max_length=20, blank=True)
    account_id = models.CharField(max_length=50, blank=True)
    entity_type = models.CharField(max_length=20, blank=True)
    entity_id = models.CharField(max_length=50, blank=True)

    strategy_template_code = models.CharField(max_length=50, blank=True)
    scenario_name = models.CharField(max_length=100, blank=True)

    parameters_json = models.TextField()
    # Snapshot of the run's input parameters:
    #   backtest: strategy_params_json + start_date/end_date/quantity/cost_basis
    #   montecarlo: n_paths, horizon_days, use_jump_diffusion, random_seed,
    #               recommendation_id
    #   stress_test: scenario_name or custom_shocks_json, account scope

    summary_json = models.TextField()
    # Compact result summary for quick display without joining to M5 tables:
    #   backtest: {"total_return_hedged_pct": ..., "max_drawdown_hedged": ...,
    #              "hedge_effectiveness_ratio": ...}
    #   montecarlo: {"pct5_outcome_usd": ..., "pct50_outcome_usd": ...,
    #                "prob_loss_10pct": ...}
    #   stress_test: {"stress_loss_unhedged_pct": ..., "stress_loss_hedged_pct": ...}

    calc_date = models.DateField()
    run_timestamp = models.DateTimeField()
    # Copied from the source run's run_timestamp (not auto_now_add — preserves
    # the original execution time even if this audit row is backfilled).

    written_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_audit_simulation_run'
        indexes = [
            models.Index(fields=['simulation_family', 'source_run_id']),
            models.Index(fields=['symbol', 'calc_date']),
            models.Index(fields=['calc_date']),
        ]
```

#### 1.5 `rm_audit_regime_log`

One row per `calc_date`, written alongside `calc_regime` (M2-Spec-1). Serves as
the permanent historical regime record independent of any future changes to
`rm_regime_state` scoring methodology.

```python
class AuditRegimeLog(ImmutableModel):
    """
    Immutable daily record of the detected regime and a market-condition
    summary. Written alongside calc_regime (and, for the market condition
    summary fields, after calc_market_conditions has run for the same date —
    see §4.3 for sequencing).
    """
    calc_date = models.DateField(unique=True)

    quadrant = models.CharField(max_length=2)
    confidence_score = models.DecimalField(max_digits=5, decimal_places=2)
    transition_flag = models.BooleanField()
    indicator_snapshot_json = models.TextField()
    # Copied from rm_regime_state.indicator_snapshot_json

    # --- Market condition summary across all held symbols for this calc_date ---
    death_cross_count = models.IntegerField(default=0)
    golden_cross_count = models.IntegerField(default=0)
    oversold_count = models.IntegerField(default=0)
    # rsi_14 < 30
    overbought_count = models.IntegerField(default=0)
    # rsi_14 > 70
    bear_phase_symbols_json = models.TextField(default='[]')
    # JSON list of symbols with market_phase='bear' on this date

    written_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_audit_regime_log'
        indexes = [models.Index(fields=['calc_date'])]
```

#### 1.6 `rm_tax_decision_acknowledgement`

Canonical definition (reconciles any M6-Spec-2 placeholder — see Context).

```python
class TaxDecisionAcknowledgement(models.Model):
    """
    Records that a tax-triggering strategy recommendation's estimated tax cost
    was presented to, and acknowledged by, the relevant owner before the
    recommendation was marked 'actioned'.

    NOT an ImmutableModel subclass: acknowledged_at/acknowledged_by/user_notes
    are written once at creation and never updated thereafter in normal use,
    but the table is not subject to the same blanket immutability as the
    rm_audit_* tables (it is a workflow record, not a calculation snapshot).
    However, application code never updates or deletes existing rows — once
    created, a TaxDecisionAcknowledgement is final. (No save()/delete()
    override is added; this is enforced by convention + the view logic in §5,
    which only ever creates new rows via get_or_create-style logic that first
    checks for an existing row.)
    """
    recommendation_id = models.IntegerField()
    # rm_strategy_recommendation.id. Not a FK to avoid a circular import between
    # the risk app's M4 models module and this audit module — both live in
    # risk/models.py in practice, but the loose coupling matches the
    # architecture's "rm_ tables are independently queryable" principle and
    # keeps this table valid even if a recommendation row is ever pruned.

    presented_to_user = models.CharField(max_length=50)
    # Owner the recommendation belongs to — copied from
    # rm_strategy_recommendation.owner at presentation time.
    presented_at = models.DateTimeField()
    # Copied from rm_strategy_recommendation.calc_timestamp — i.e. when the
    # recommendation (and its tax estimate) was first computed/shown.

    acknowledged_at = models.DateTimeField()
    acknowledged_by = models.CharField(max_length=100)
    # Django username, or 'anonymous' if auth is not configured for this
    # deployment (matches M6-Spec-2's strategy_acknowledge view behaviour).

    tax_cost_estimate_presented = models.DecimalField(max_digits=15, decimal_places=2)
    # Snapshot of rm_strategy_recommendation.tax_cost_estimate_usd at the moment
    # of acknowledgement — if the recommendation is later recalculated with a
    # different estimate, this field preserves what the user actually saw.

    user_notes = models.TextField(blank=True)

    class Meta:
        db_table = 'rm_tax_decision_acknowledgement'
        indexes = [
            models.Index(fields=['recommendation_id']),
            models.Index(fields=['presented_to_user', 'acknowledged_at']),
        ]
```

**Reconciliation note for the developer:** if M6-Spec-2 was implemented and
already created a `TaxDecisionAcknowledgement` model/table with a different
schema, generate a Django migration that `AlterField`s the existing table to
match the definition above (do not `CreateModel` a duplicate). If M6-Spec-2's
`strategy_acknowledge` view already writes to it, verify its field names align
with §1.6 and adjust the view's `.objects.create(...)` call if needed — this is
expected to be a small diff, not a rewrite.

---

### 2. Hooks Into Existing Commands (Additive Modifications)

This section lists the **only** changes to code from prior specs. Each is a small,
additive change — no existing behaviour, output table schema, or CLI signature
changes. All hooks are wrapped so that an audit-write failure is logged as an
ERROR but does **not** prevent the primary (M2/M4/M5) write from succeeding —
audit logging must never block the core pipeline. (This is a deliberate departure
from "audit must be atomic with the primary write" in the literal sense of a DB
transaction; instead, atomicity is approximated by writing the audit row
immediately after the primary UPSERT within the same loop iteration, and any
audit failure is surfaced loudly in the command's summary output and via Django
logging at ERROR level, so a missing audit row is never silent.)

#### 2.1 Hook into `calc_urgency` (M2-Spec-2)

In `risk/management/commands/calc_urgency.py`, immediately after each
`rm_urgency_score` UPSERT (for every entity_type), call:

```python
def write_audit_score_snapshot(
    entity_type: str, entity_id: str, symbol: str, calc_date: date,
    urgency_level: str, score_raw: Decimal,
    component_scores_json: str, weights_config: dict,
    input_metrics: dict,
) -> None:
    """
    Create one rm_audit_score_snapshot row. weights_config is the dict returned
    by load_scoring_config() (already loaded once per command run). input_metrics
    is '{}' for entity_type in ('account', 'owner', 'portfolio').

    Wrapped in try/except: on ImmutableRecordError or any DB error, log
    logger.error(...) with full context and continue — does not raise.
    """
```

#### 2.2 Hook into `calc_strategy_recommendations` (M4-Spec-1)

In `risk/management/commands/calc_strategy_recommendations.py`, the existing
algorithm (per M4-Spec-1 §2) already computes the **full eligible candidate set**
before ranking, and `get_eligible_templates()` (M3-Spec-1 §4) already determines
ineligible templates as a side effect of its filter chain. Two additions:

1. `get_eligible_templates()` — or a thin wrapper around it — must also return the
   **rejected** templates with their `ineligibility_reason` (one of the values
   listed in §1.3), not just the eligible ones. This is a small extension to the
   existing filter chain: instead of returning only templates that pass all 9
   filters, return `(eligible, rejected_with_reasons)`.
2. After ranking, for **every** template in the full universe (eligible + rejected)
   for this `(symbol, account_id, calc_date)`, call:

```python
def write_audit_recommendation_log(
    symbol: str, account_id: str, owner: str, calc_date: date,
    input_urgency_level: str, input_urgency_score: Decimal,
    input_regime_quadrant: str,
    strategy_template_code: str,
    was_eligible: bool, ineligibility_reason: str,
    rank: int | None,
    cost_result: dict | None,    # output of estimate_strategy_cost(), or None
    var_result: dict | None,     # output of estimate_var_reduction(), or None
    cost_per_var_reduction: float | None,
    selection_rationale: str,
    constraints_applied: list[str],
) -> None:
    """
    Create one rm_audit_recommendation_log row. Same try/except-and-log pattern
    as §2.1 — never raises.
    """
```

`selection_rationale` generation (`risk/services/audit_rationale.py`,
`build_selection_rationale()`):
- Rank 1: `"Selected: lowest cost per dollar of VaR reduction among eligible candidates ({cost_per_var_reduction:.4f})."` — append `" Not tax-triggering."` or `" Tax cost (${tax_cost:,.0f}) included at penalty multiplier {multiplier}."` as applicable.
- Rank > 1: `"Ranked {rank}: cost_per_var_reduction ({value:.4f}) higher than rank-1 ({rank1_value:.4f})."` — if `tax_cost_estimate_usd > 0` and rank-1's is 0 (or much lower), append `" Primary driver: tax cost (${tax_cost:,.0f})."`
- Ineligible: `"Not considered: {ineligibility_reason_human_readable}."` — a small lookup dict maps each `ineligibility_reason` code to a human-readable phrase, e.g. `'options_not_available'` → `"options are not available on this security"`.

#### 2.3 Hook into `calc_regime` and `calc_market_conditions` (M2-Spec-1)

`calc_regime` runs first in the daily pipeline (per M0-Spec-2 §9 / M7-Spec-1 §7
`daily_risk_refresh` ordering: `calc_regime` precedes `calc_market_conditions`).
Because `rm_audit_regime_log` needs **both** the regime row and the market
condition summary for the same `calc_date`, the audit write happens at the **end**
of `calc_market_conditions` (the later of the two steps), reading back the
already-written `rm_regime_state` row for `calc_date`:

```python
def write_audit_regime_log(calc_date: date) -> None:
    """
    Called at the end of calc_market_conditions.
    1. Read rm_regime_state for calc_date (must exist — calc_regime ran first
       in the pipeline; if missing, log ERROR and skip — do not raise)
    2. Aggregate rm_market_condition for calc_date:
       death_cross_count, golden_cross_count (crossover_type == 'death'/'golden'
       AND crossover_date == calc_date — same "today only" semantics as
       M7-Spec-1 §3.4),
       oversold_count (rsi_14 < 30), overbought_count (rsi_14 > 70),
       bear_phase_symbols (market_phase == 'bear')
    3. UPSERT-as-insert into rm_audit_regime_log (unique on calc_date — if a row
       already exists for calc_date, this is a re-run; log a WARNING and skip
       rather than raising ImmutableRecordError, since re-running
       calc_market_conditions for an already-audited date is a normal
       operational scenario, not a data integrity violation)
    """
```

#### 2.4 Hook into M5-Spec-1 `run_backtest` and M5-Spec-2 `run_montecarlo` /
`run_stress_test`

At the end of each command, after the primary result row(s) are inserted, call:

```python
def write_audit_simulation_run(
    simulation_family: str, source_run_id: int,
    symbol: str, account_id: str, entity_type: str, entity_id: str,
    strategy_template_code: str, scenario_name: str,
    parameters: dict, summary: dict,
    calc_date: date, run_timestamp: datetime,
) -> None:
    """
    Create one rm_audit_simulation_run row. Same try/except-and-log pattern.
    """
```

---

### 3. Explainability View

**View:** `risk/views/explain.py`
**Template:** `risk/templates/risk/explain.html`
**URL:** `risk/explain/<int:urgency_score_id>/`

Add to `risk/urls.py`:
```python
urlpatterns += [
    path('explain/<int:urgency_score_id>/', views.explain_score, name='explain_score'),
]
```

#### 3.1 View Logic

```python
def explain_score(request, urgency_score_id: int):
    """
    "Why is this entity at this urgency level?" — reads the audit snapshot for
    a specific rm_urgency_score row and presents a ranked, plain-English
    breakdown.
    """
    score = get_object_or_404(UrgencyScore, id=urgency_score_id)

    snapshot = AuditScoreSnapshot.objects.filter(
        entity_type=score.entity_type,
        entity_id=score.entity_id,
        symbol=score.symbol,
        calc_date=score.calc_date,
    ).order_by('-written_at').first()
    # If multiple snapshots exist for the same key (re-run on the same day),
    # use the most recently written one — it corresponds to the current
    # rm_urgency_score row (which was UPSERTed, so only the latest values
    # are "live").

    if snapshot is None:
        # Score predates M8 deployment, or audit write failed — degrade
        # gracefully rather than 404, since the urgency score itself is real.
        context = {'score': score, 'snapshot': None}
        return render(request, 'risk/explain.html', context)

    ranked_factors = build_ranked_factors(snapshot)
    # See §3.2

    history = build_historical_context(score)
    # See §3.3

    context = {
        'score': score,
        'snapshot': snapshot,
        'ranked_factors': ranked_factors,
        'history': history,
    }
    return render(request, 'risk/explain.html', context)
```

#### 3.2 Ranked Factor Construction

```python
def build_ranked_factors(snapshot: AuditScoreSnapshot) -> list[dict]:
    """
    Parse component_scores_json and weights_at_calc_json. For each of the four
    components (m1, driver, regime, market), compute:
        contribution = component_score * component_weight

    Sort descending by contribution. Return a list of dicts:
        {
            'component': 'm1' | 'driver' | 'regime' | 'market',
            'label': 'Risk Metric Severity' | 'Security Risk Profile' |
                     'Macro Regime Adjustment' | 'Market Technicals',
            'score': float (0-100),
            'weight': float (0-1),
            'contribution': float,
            'contribution_pct_of_total': float,  # contribution / score_raw * 100
            'explanation': str,  # plain English, see below
        }

    Plain English templates per component (template-driven, NOT AI-generated,
    per architecture §M8):

    m1: built from input_metrics_json's var_parametric_95,
        current_drawdown_from_252d_peak, vol_252d:
        "{symbol}'s 1-day VaR ({var:.2%}), drawdown from 52-week peak
         ({dd:.1%}), and annualised volatility ({vol:.1%}) rank it {score:.0f}/100
         for risk severity relative to the rest of the portfolio."
        For account/owner/portfolio entities (input_metrics empty):
        "This is a roll-up of the underlying securities' risk metric severity."

    driver: built from asset_class_baseline, sector_cyclicality, liquidity_tier,
        earnings_proximity_flag:
        "As a {asset_class}, this security carries a baseline risk profile of
         {asset_class_baseline:.0f}/100, with sector cyclicality
         {sector_cyclicality:.0f}/100 and liquidity tier {liquidity_tier:.0f}/100."
        Append " An upcoming earnings announcement adds to this score." if
        earnings_proximity_flag is true.

    regime: built from regime_quadrant, regime_sensitivity_multiplier:
        "The current macro regime ({quadrant}) applies a
         {multiplier:.2f}x {'amplifier' if multiplier > 1 else 'dampener'} to
         this security's baseline risk profile
         ({'increasing' if multiplier > 1 else 'reducing'} the contribution
         from {driver_score:.0f} to {regime_score:.0f})."
        If regime_transition_dampener was applied (transition_flag was True at
        calc time — infer from weights_at_calc_json combined with whether
        regime_score < driver_score * multiplier_raw):
        append " A regime transition was in progress, which moderated this
        adjustment."

    market: built from rsi_14, crossover_type, market_phase, bb_pct_b:
        "Current technicals: RSI {rsi:.0f} ({'oversold' if <30 else 'overbought'
         if >70 else 'neutral'}), {phase} phase"
        + (", with a recent Death Cross" if crossover_type == 'death' else
           ", with a recent Golden Cross" if crossover_type == 'golden' else "")
        + "."

    For account/owner/portfolio entity types where input_metrics_json == '{}':
    driver/regime/market explanations read:
        "Not applicable at this entity level — this score is a weighted
         roll-up of constituent securities."
    (These three components should have score 0 for roll-up entities per
    M2-Spec-2's scoring engine, which only computes the four components for
    entity_type='security_in_account'; account/owner/portfolio scores are
    produced by rollup_account/rollup_owner/rollup_portfolio, which do not
    populate component_*_score. Document this in the template: for non-security
    entities, the explain view shows only the M1 "roll-up severity" framing
    and the historical context section — the four-component breakdown is
    security-level only.)
    """
```

#### 3.3 Historical Context

```python
def build_historical_context(score: UrgencyScore, lookback_days: int = 30) -> dict:
    """
    Query rm_urgency_score for the same (entity_type, entity_id, symbol) over
    the most recent `lookback_days` distinct calc_dates (including today),
    ordered descending, limited to lookback_days rows.

    days_at_current_level = count of those rows where urgency_level ==
        score.urgency_level
    days_examined = total rows found (may be < lookback_days near the start
        of the system's operational history)

    Returns:
        {
            'days_at_current_level': int,
            'days_examined': int,
            'summary': f"This {entity_label} has been {score.urgency_level} for "
                       f"{days_at_current_level} of the last {days_examined} "
                       f"calculated days."
        }

    entity_label:
        security_in_account: f"{symbol} position in {account_id}"
        account: f"{entity_id} account"
        owner: f"{entity_id}'s portfolio"
        portfolio: "the portfolio"
    """
```

#### 3.4 Template — `risk/templates/risk/explain.html`

```html
{% extends "base.html" %}
{% block content %}
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between align-items-center">
    <span>Why is {{ score.entity_id }}{% if score.symbol %} ({{ score.symbol }}){% endif %}
          rated <span class="badge urgency-{{ score.urgency_level|lower }}">{{ score.urgency_level }}</span>?</span>
    <span class="text-muted small">{{ score.calc_date }}</span>
  </div>
  <div class="card-body">
    {% if not snapshot %}
      <div class="alert alert-secondary mb-0">
        No detailed audit snapshot is available for this score (it may predate
        the audit module, or the snapshot write failed). The score itself
        ({{ score.score_raw }}/100) is still valid.
      </div>
    {% else %}
      <h6 class="text-muted">Contributing Factors (ranked)</h6>
      <table class="table table-sm">
        <thead class="table-light">
          <tr><th>Factor</th><th class="text-end">Score</th><th class="text-end">Weight</th>
              <th class="text-end">Contribution</th><th>Explanation</th></tr>
        </thead>
        <tbody>
          {% for f in ranked_factors %}
          <tr {% if forloop.first %}class="table-warning"{% endif %}>
            <td><strong>{{ f.label }}</strong>
                {% if forloop.first %}<span class="badge bg-secondary ms-1">Primary driver</span>{% endif %}</td>
            <td class="text-end">{{ f.score|floatformat:1 }}</td>
            <td class="text-end">{{ f.weight|floatformat:2 }}</td>
            <td class="text-end">{{ f.contribution|floatformat:1 }}
                <span class="text-muted small">({{ f.contribution_pct_of_total|floatformat:0 }}%)</span></td>
            <td class="small">{{ f.explanation }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>

      <h6 class="text-muted mt-4">Historical Context</h6>
      <p>{{ history.summary }}</p>

      <h6 class="text-muted mt-4">Configuration at Calculation Time</h6>
      <p class="small text-muted">
        Weights: M1 metrics {{ weights_at_calc.weight_m1_metrics|floatformat:2 }},
        security driver {{ weights_at_calc.weight_security_driver|floatformat:2 }},
        regime {{ weights_at_calc.weight_regime|floatformat:2 }},
        market condition {{ weights_at_calc.weight_market_condition|floatformat:2 }}.
        Thresholds: Low &lt; {{ weights_at_calc.threshold_low }},
        Medium &lt; {{ weights_at_calc.threshold_medium }},
        High &lt; {{ weights_at_calc.threshold_high }}, Critical above.
      </p>
    {% endif %}
  </div>
</div>
{% endblock %}
```

**Linking from existing views:** add a small "Why?" link/icon next to every
urgency badge rendered in `risk/dashboard.html`, `risk/account_summary.html`,
`risk/owner_summary.html`, and `risk/security_detail.html` (M6-Spec-1/M6-Spec-2
templates), pointing to `{% url 'risk:explain_score' score.id %}`. This is the
only change to M6 templates in this spec — purely additive (one `<a>` tag per
existing badge).

---

### 4. Tax Acknowledgement Workflow — Finalisation

#### 4.1 `can_mark_actioned` / `mark_actioned` Service Functions

Create `risk/services/tax_acknowledgement.py`:

```python
def can_mark_actioned(recommendation: StrategyRecommendation) -> tuple[bool, str]:
    """
    Returns (allowed, reason_if_not).

    allowed = True if:
        recommendation.status == 'active'
        AND (
            recommendation.is_tax_triggering == False
            OR TaxDecisionAcknowledgement.objects.filter(
                   recommendation_id=recommendation.id).exists()
        )

    reason_if_not, when allowed=False:
        "Recommendation is not active (status={status})." OR
        "Tax impact must be acknowledged before this recommendation can be
         marked as actioned."
    """

def mark_actioned(recommendation: StrategyRecommendation, actioned_by: str) -> None:
    """
    Sets status='actioned', actioned_at=now(), actioned_by=actioned_by, and
    saves. Raises ValueError (with the reason from can_mark_actioned) if not
    allowed — caller (the view) is responsible for catching this and showing
    a user-facing error; this function does not silently no-op.
    """
```

#### 4.2 `strategy_mark_actioned` View

Add to `risk/urls.py` (the URL referenced but not defined by M6-Spec-2's
template):

```python
urlpatterns += [
    path('strategy/<int:rec_id>/action/', views.strategy_mark_actioned,
         name='strategy_mark_actioned'),
]
```

```python
def strategy_mark_actioned(request, rec_id: int):
    """
    POST-only. Calls can_mark_actioned(); if allowed, calls mark_actioned()
    and redirects to risk:strategy_report with a success message. If not
    allowed, redirects back with an error message (does not raise a 403 —
    the M6-Spec-2 template already conditionally disables the button, so
    reaching this view in a disallowed state means the button state was
    stale; handle gracefully).
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    recommendation = get_object_or_404(StrategyRecommendation, id=rec_id)
    allowed, reason = can_mark_actioned(recommendation)
    if not allowed:
        messages.error(request, reason)
    else:
        mark_actioned(recommendation,
                       request.user.username if request.user.is_authenticated else 'anonymous')
        messages.success(request, "Recommendation marked as actioned.")
    return redirect('risk:strategy_report', rec_id=rec_id)
```

#### 4.3 Dashboard — Unacknowledged Tax-Triggering Recommendations Panel

Additive change to `risk/views/dashboard.py` (M6-Spec-1) and
`risk/templates/risk/dashboard.html`:

```python
def get_unacknowledged_tax_recommendations(calc_date: date) -> list[StrategyRecommendation]:
    """
    Return active, is_tax_triggering=True recommendations at calc_date for
    which no rm_tax_decision_acknowledgement row exists, ordered by
    tax_cost_estimate_usd descending (largest tax exposure first).

    Implementation note: TaxDecisionAcknowledgement has no FK to
    StrategyRecommendation (see §1.6 rationale), so this is a Python-side
    set difference: load the candidate recommendation ids, load the set of
    acknowledged recommendation_ids, subtract.
    """
```

Template addition (new card, placed near the top of the dashboard, only rendered
if the list is non-empty):

```html
{% if unacknowledged_tax_recs %}
<div class="card mb-4 border-warning">
  <div class="card-header bg-warning bg-opacity-25">
    <i class="bi bi-exclamation-triangle-fill"></i>
    Unacknowledged Tax-Triggering Recommendations
  </div>
  <div class="table-responsive">
    <table class="table table-sm mb-0">
      <thead class="table-light">
        <tr><th>Symbol</th><th>Account</th><th>Strategy</th>
            <th class="text-end">Estimated Tax Cost</th><th></th></tr>
      </thead>
      <tbody>
        {% for rec in unacknowledged_tax_recs %}
        <tr>
          <td>{{ rec.symbol }}</td>
          <td>{{ rec.account_id }}</td>
          <td>{{ rec.strategy_template.name }}</td>
          <td class="text-end text-danger fw-bold">
            ${{ rec.tax_cost_estimate_usd|floatformat:0|intcomma }}
          </td>
          <td><a href="{% url 'risk:strategy_report' rec.id %}"
                 class="btn btn-sm btn-outline-warning">Review</a></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}
```

---

### 5. Audit Admin — Read-Only Views with CSV Export

```python
class ReadOnlyAuditAdmin(admin.ModelAdmin):
    """
    Shared base for all rm_audit_* admin registrations.
    - has_add_permission / has_change_permission / has_delete_permission
      all return False — admin is strictly read-only, consistent with the
      ImmutableModel enforcement at the model layer.
    - Adds an "Export selected to CSV" action.
    """
    actions = ['export_csv']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def export_csv(self, request, queryset):
        import csv
        response = HttpResponse(content_type='text/csv')
        model_name = self.model._meta.db_table
        response['Content-Disposition'] = f'attachment; filename="{model_name}_export.csv"'
        writer = csv.writer(response)
        field_names = [f.name for f in self.model._meta.fields]
        writer.writerow(field_names)
        for obj in queryset:
            writer.writerow([getattr(obj, f) for f in field_names])
        return response
    export_csv.short_description = "Export selected to CSV"


@admin.register(AuditScoreSnapshot)
class AuditScoreSnapshotAdmin(ReadOnlyAuditAdmin):
    list_display = ['calc_date', 'entity_type', 'entity_id', 'symbol',
                     'urgency_level', 'score_raw', 'written_at']
    list_filter = ['calc_date', 'entity_type', 'urgency_level']
    search_fields = ['entity_id', 'symbol']
    ordering = ['-calc_date', 'entity_type', 'entity_id']


@admin.register(AuditRecommendationLog)
class AuditRecommendationLogAdmin(ReadOnlyAuditAdmin):
    list_display = ['calc_date', 'symbol', 'account_id', 'strategy_template_code',
                     'was_eligible', 'rank', 'cost_per_var_reduction_usd',
                     'is_tax_triggering']
    list_filter = ['calc_date', 'was_eligible', 'is_tax_triggering', 'strategy_template_code']
    search_fields = ['symbol', 'account_id', 'selection_rationale']
    ordering = ['-calc_date', 'symbol', 'account_id', 'rank']


@admin.register(AuditSimulationRun)
class AuditSimulationRunAdmin(ReadOnlyAuditAdmin):
    list_display = ['calc_date', 'simulation_family', 'symbol', 'account_id',
                     'strategy_template_code', 'scenario_name', 'run_timestamp']
    list_filter = ['calc_date', 'simulation_family', 'scenario_name']
    search_fields = ['symbol', 'account_id']
    ordering = ['-run_timestamp']


@admin.register(AuditRegimeLog)
class AuditRegimeLogAdmin(ReadOnlyAuditAdmin):
    list_display = ['calc_date', 'quadrant', 'confidence_score', 'transition_flag',
                     'death_cross_count', 'oversold_count', 'overbought_count']
    list_filter = ['quadrant', 'transition_flag']
    ordering = ['-calc_date']


@admin.register(TaxDecisionAcknowledgement)
class TaxDecisionAcknowledgementAdmin(admin.ModelAdmin):
    # Not read-only via ReadOnlyAuditAdmin — this is a workflow table, and
    # admins may need to record an acknowledgement that happened verbally
    # (out-of-band) by adding a row manually. Editing existing rows is still
    # disallowed by convention (see §1.6) but not enforced at the admin layer
    # to allow correction of typos in user_notes if needed.
    list_display = ['recommendation_id', 'presented_to_user', 'presented_at',
                     'acknowledged_at', 'acknowledged_by', 'tax_cost_estimate_presented']
    list_filter = ['presented_to_user']
    search_fields = ['recommendation_id', 'user_notes']
    ordering = ['-acknowledged_at']
```

---

### 6. Retention Check Command

Create `risk/management/commands/audit_retention_check.py`.

```
python manage.py audit_retention_check [--warning-days 90]
```

- `--warning-days`: how close to the 7-year mark a record must be to be reported
  (default 90 days — i.e. records that will reach 7 years old within 90 days).

**Algorithm:**
```
retention_period = 7 years (2557 days; do not hardcode "7" without a comment
    explaining the regulatory basis — Canadian tax record retention guidance)
warning_threshold = retention_period - warning_days

For each audit table (rm_audit_score_snapshot, rm_audit_recommendation_log,
                      rm_audit_simulation_run, rm_audit_regime_log,
                      rm_tax_decision_acknowledgement):
    age_field = 'written_at' for rm_audit_* tables,
                'acknowledged_at' for rm_tax_decision_acknowledgement
    age = today - record[age_field]
    if age >= warning_threshold:
        add to report: table name, record identifying fields (calc_date +
            entity_id/symbol or recommendation_id), age in days,
            days_until_7_years = retention_period - age

Print a report grouped by table:
    "rm_audit_score_snapshot: 0 records approaching 7-year retention limit"
    or
    "rm_audit_score_snapshot: 3 records approaching 7-year retention limit:
       - entity_id=TFSA_TOM::IBIT, calc_date=2019-XX-XX, written_at=...,
         123 days until 7-year mark"

NEVER deletes anything. Exit code 0 always (this is an informational report,
not a failure condition) — unless a database error occurs, in which case
exit non-zero.
```

Given this is the first year of operation (2026), this command is expected to
report **zero** records on every table for several years. It exists now so the
schema and tooling are in place — no behaviour change is needed when records
actually start approaching the limit around 2033.

---

### 7. Tests

File: `risk/tests/test_audit.py`

```python
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.core.management import call_command
from risk.models import (
    AuditScoreSnapshot, AuditRecommendationLog, AuditSimulationRun,
    AuditRegimeLog, TaxDecisionAcknowledgement, UrgencyScore,
    StrategyRecommendation, StrategyTemplate, ImmutableRecordError,
)
from risk.services.tax_acknowledgement import can_mark_actioned, mark_actioned
from risk.services.audit_rationale import build_selection_rationale


class ImmutabilityTests(TestCase):

    def test_score_snapshot_cannot_be_updated(self):
        snap = AuditScoreSnapshot.objects.create(
            entity_type='security_in_account', entity_id='IB_SUSAN::AAPL',
            symbol='AAPL', calc_date=date(2026, 6, 2),
            urgency_level='High', score_raw=Decimal('62.0'),
            component_scores_json='{}', weights_at_calc_json='{}',
            input_metrics_json='{}',
        )
        snap.urgency_level = 'Critical'
        with self.assertRaises(ImmutableRecordError):
            snap.save()

    def test_score_snapshot_cannot_be_deleted(self):
        snap = AuditScoreSnapshot.objects.create(
            entity_type='portfolio', entity_id='ALL', symbol='',
            calc_date=date(2026, 6, 2),
            urgency_level='Medium', score_raw=Decimal('40.0'),
            component_scores_json='{}', weights_at_calc_json='{}',
            input_metrics_json='{}',
        )
        with self.assertRaises(ImmutableRecordError):
            snap.delete()

    def test_new_inserts_succeed(self):
        # Distinct calc_dates / entity_ids — no conflict with immutability
        for i in range(3):
            AuditRegimeLog.objects.create(
                calc_date=date(2026, 6, 1) + timedelta(days=i),
                quadrant='Q3', confidence_score=Decimal('55.0'),
                transition_flag=False, indicator_snapshot_json='{}',
                death_cross_count=0, golden_cross_count=0,
                oversold_count=0, overbought_count=0,
                bear_phase_symbols_json='[]',
            )
        self.assertEqual(AuditRegimeLog.objects.count(), 3)


class AuditSnapshotWriteTests(TestCase):

    def test_snapshot_written_when_urgency_score_calculated(self):
        """
        Run calc_urgency for a minimal seeded entity (or call
        write_audit_score_snapshot directly with seeded UrgencyScore data).
        Assert exactly one AuditScoreSnapshot row exists matching the
        UrgencyScore row's (entity_type, entity_id, symbol, calc_date).
        """
        pass  # Implement with M2-Spec-2 fixtures

    def test_audit_failure_does_not_block_urgency_score_write(self):
        """
        Mock write_audit_score_snapshot to raise an exception.
        Run calc_urgency.
        Assert rm_urgency_score row IS still created/updated.
        Assert an ERROR-level log message was emitted.
        """
        pass


class SelectionRationaleTests(TestCase):

    def test_rank_one_non_tax_triggering(self):
        rationale = build_selection_rationale(
            rank=1, was_eligible=True, ineligibility_reason='',
            cost_per_var_reduction=0.21, rank1_cost_per_var_reduction=0.21,
            tax_cost_estimate_usd=Decimal('0'), is_tax_triggering=False,
            tax_penalty_multiplier=1.0,
        )
        self.assertIn('Selected', rationale)
        self.assertIn('Not tax-triggering', rationale)

    def test_lower_rank_cites_tax_cost_when_dominant(self):
        rationale = build_selection_rationale(
            rank=3, was_eligible=True, ineligibility_reason='',
            cost_per_var_reduction=8.64, rank1_cost_per_var_reduction=0.21,
            tax_cost_estimate_usd=Decimal('43219'), is_tax_triggering=True,
            tax_penalty_multiplier=1.0,
        )
        self.assertIn('Ranked 3', rationale)
        self.assertIn('43,219', rationale.replace(',', ',') if ',' in rationale else rationale)

    def test_ineligible_template_explains_reason(self):
        rationale = build_selection_rationale(
            rank=None, was_eligible=False,
            ineligibility_reason='options_not_available',
            cost_per_var_reduction=None, rank1_cost_per_var_reduction=None,
            tax_cost_estimate_usd=None, is_tax_triggering=None,
            tax_penalty_multiplier=1.0,
        )
        self.assertIn('Not considered', rationale)
        self.assertIn('options', rationale.lower())


class TaxAcknowledgementTests(TestCase):

    def setUp(self):
        call_command('seed_instrument_catalogue', verbosity=0)
        self.template = StrategyTemplate.objects.first()
        self.rec = StrategyRecommendation.objects.create(
            symbol='AAPL', account_id='IB_SUSAN', owner='SUSAN',
            calc_date=date(2026, 6, 2),
            strategy_template=self.template, rank=1,
            urgency_level='High', urgency_score=Decimal('62.0'),
            regime_quadrant='Q3',
            position_quantity=Decimal('670'),
            position_market_value_usd=Decimal('211000'),
            position_unrealised_gain_usd=Decimal('200000'),
            cost_basis_per_unit_usd=Decimal('15.98'),
            current_price_usd=Decimal('315.0'),
            estimated_strategy_cost_usd=Decimal('5000'),
            estimated_strategy_cost_pct=Decimal('0.024'),
            tax_cost_estimate_usd=Decimal('43219'),
            tax_impact_note='If sold, triggers ~$200K gain.',
            is_tax_triggering=True,
            estimated_var_reduction_usd=Decimal('5000'),
            estimated_var_reduction_pct=Decimal('0.024'),
            cost_per_var_reduction_usd=Decimal('1.0'),
            constraints_applied_json='[]',
        )

    def test_cannot_action_without_acknowledgement(self):
        allowed, reason = can_mark_actioned(self.rec)
        self.assertFalse(allowed)
        self.assertIn('acknowledg', reason.lower())

    def test_can_action_after_acknowledgement(self):
        TaxDecisionAcknowledgement.objects.create(
            recommendation_id=self.rec.id,
            presented_to_user='SUSAN',
            presented_at=datetime(2026, 6, 2, 9, 0),
            acknowledged_at=datetime(2026, 6, 2, 10, 0),
            acknowledged_by='tdwdev',
            tax_cost_estimate_presented=Decimal('43219'),
        )
        allowed, _ = can_mark_actioned(self.rec)
        self.assertTrue(allowed)

    def test_mark_actioned_updates_status(self):
        TaxDecisionAcknowledgement.objects.create(
            recommendation_id=self.rec.id,
            presented_to_user='SUSAN',
            presented_at=datetime(2026, 6, 2, 9, 0),
            acknowledged_at=datetime(2026, 6, 2, 10, 0),
            acknowledged_by='tdwdev',
            tax_cost_estimate_presented=Decimal('43219'),
        )
        mark_actioned(self.rec, actioned_by='tdwdev')
        self.rec.refresh_from_db()
        self.assertEqual(self.rec.status, 'actioned')
        self.assertEqual(self.rec.actioned_by, 'tdwdev')

    def test_mark_actioned_raises_without_acknowledgement(self):
        with self.assertRaises(ValueError):
            mark_actioned(self.rec, actioned_by='tdwdev')

    def test_non_tax_triggering_recommendation_can_be_actioned_directly(self):
        self.rec.is_tax_triggering = False
        self.rec.save()
        allowed, _ = can_mark_actioned(self.rec)
        self.assertTrue(allowed)


class ExplainViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.calc_date = date(2026, 6, 2)
        cls.score = UrgencyScore.objects.create(
            entity_type='security_in_account', entity_id='IB_SUSAN::AAPL',
            symbol='AAPL', account_id='IB_SUSAN',
            calc_date=cls.calc_date, score_raw=Decimal('62.0'),
            urgency_level='High', dominant_component='m1_metrics',
            component_m1_score=Decimal('80.0'),
            component_driver_score=Decimal('55.0'),
            component_regime_score=Decimal('66.0'),
            component_market_score=Decimal('40.0'),
            component_scores_json='{}',
        )
        AuditScoreSnapshot.objects.create(
            entity_type='security_in_account', entity_id='IB_SUSAN::AAPL',
            symbol='AAPL', calc_date=cls.calc_date,
            urgency_level='High', score_raw=Decimal('62.0'),
            component_scores_json=json.dumps({
                'm1': {'score': 80.0, 'weight': 0.35, 'inputs': {
                    'var_parametric_95': 0.031, 'current_drawdown_from_252d_peak': -0.05,
                    'vol_252d': 0.30}},
                'driver': {'score': 55.0, 'weight': 0.20, 'inputs': {
                    'asset_class_baseline': 60, 'sector_cyclicality': 65,
                    'liquidity_tier': 15, 'earnings_proximity_flag': False}},
                'regime': {'score': 66.0, 'weight': 0.25, 'inputs': {
                    'driver_score': 55, 'multiplier': 1.20, 'quadrant': 'Q3'}},
                'market': {'score': 40.0, 'weight': 0.20, 'inputs': {
                    'rsi_14': 58, 'crossover_type': 'none',
                    'market_phase': 'uptrend', 'bb_pct_b': 0.55}},
            }),
            weights_at_calc_json=json.dumps({
                'weight_m1_metrics': 0.35, 'weight_security_driver': 0.20,
                'weight_regime': 0.25, 'weight_market_condition': 0.20,
                'threshold_low': 25, 'threshold_medium': 50, 'threshold_high': 75,
            }),
            input_metrics_json='{}',
        )

    def test_explain_view_returns_200(self):
        response = self.client.get(reverse('risk:explain_score', args=[self.score.id]))
        self.assertEqual(response.status_code, 200)

    def test_m1_is_top_factor(self):
        response = self.client.get(reverse('risk:explain_score', args=[self.score.id]))
        # m1 contribution = 80*0.35=28.0; regime=66*0.25=16.5; driver=55*0.20=11.0;
        # market=40*0.20=8.0 — m1 should be ranked first
        ranked = response.context['ranked_factors']
        self.assertEqual(ranked[0]['component'], 'm1')

    def test_missing_snapshot_degrades_gracefully(self):
        score2 = UrgencyScore.objects.create(
            entity_type='portfolio', entity_id='ALL', symbol='',
            calc_date=self.calc_date, score_raw=Decimal('40.0'),
            urgency_level='Medium', dominant_component='m1_metrics',
            component_scores_json='{}',
        )
        response = self.client.get(reverse('risk:explain_score', args=[score2.id]))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['snapshot'])

    def test_historical_context_counts_matching_days(self):
        # Seed 5 prior days, 3 of which are also 'High'
        for i, level in enumerate(['High', 'High', 'Medium', 'High', 'Low']):
            UrgencyScore.objects.create(
                entity_type='security_in_account', entity_id='IB_SUSAN::AAPL',
                symbol='AAPL', account_id='IB_SUSAN',
                calc_date=self.calc_date - timedelta(days=i + 1),
                score_raw=Decimal('50.0'), urgency_level=level,
                dominant_component='m1_metrics', component_scores_json='{}',
            )
        response = self.client.get(reverse('risk:explain_score', args=[self.score.id]))
        history = response.context['history']
        # 3 prior High days + today's High = 4 of 6 examined
        self.assertEqual(history['days_at_current_level'], 4)
        self.assertEqual(history['days_examined'], 6)


class RetentionCheckTests(TestCase):

    def test_no_records_near_limit_reports_zero(self):
        AuditScoreSnapshot.objects.create(
            entity_type='portfolio', entity_id='ALL', symbol='',
            calc_date=date(2026, 6, 2),
            urgency_level='Medium', score_raw=Decimal('40.0'),
            component_scores_json='{}', weights_at_calc_json='{}',
            input_metrics_json='{}',
        )
        out = call_command('audit_retention_check')
        # Command should run cleanly with exit code 0 (no exception raised)

    def test_old_record_flagged(self):
        old = AuditScoreSnapshot.objects.create(
            entity_type='portfolio', entity_id='ALL', symbol='',
            calc_date=date(2019, 1, 1),
            urgency_level='Medium', score_raw=Decimal('40.0'),
            component_scores_json='{}', weights_at_calc_json='{}',
            input_metrics_json='{}',
        )
        # Manually backdate written_at (auto_now_add prevents direct creation
        # with an old timestamp — use queryset.update())
        AuditScoreSnapshot.objects.filter(pk=old.pk).update(
            written_at=date(2019, 1, 1)
        )
        # With default --warning-days 90, a 2019 record (>7 years before a
        # 2026 'today') should be flagged.
        call_command('audit_retention_check')


class IntegrationTests(TestCase):

    def test_full_pipeline_audit_coverage(self):
        """
        After running daily_risk_refresh (M7-Spec-1) for a seeded date:
        - rm_audit_score_snapshot has a row for every rm_urgency_score row
          for that calc_date
        - rm_audit_regime_log has exactly one row for that calc_date
        - rm_audit_recommendation_log has rows for every (eligible AND
          ineligible) template considered for every position at >= the
          configured min-urgency
        """
        pass  # Implement with full fixture stack
```

Run with: `python manage.py test risk.tests.test_audit --settings=portfolio_manager.settings_test`

---

## Constraints

- **DO NOT** modify the schema or write paths of `rm_urgency_score`,
  `rm_strategy_recommendation`, `rm_regime_state`, `rm_market_condition`,
  `rm_backtest_run`/`rm_backtest_result`, or `rm_simulation_run`/`rm_simulation_result`
  — this spec only adds parallel audit writes alongside them
- **DO NOT** push to git (manual step)
- All `rm_audit_*` tables are append-only — enforced via `ImmutableModel`; no
  migration may add `UPDATE`/`DELETE` paths to these tables
- Audit-write failures must be logged at ERROR level but must **never** raise out
  of the M2/M4/M5 commands they are hooked into — the primary calculation pipeline
  takes priority over audit completeness, but a failure must be loud (visible in
  command output and logs), never silent
- `audit_retention_check` never deletes data — it is purely a reporting command
- Explainability text is template-driven plain English, not generated by an LLM —
  per the architecture's explicit requirement
- The tax acknowledgement gate (`can_mark_actioned`) is the **only** mechanism that
  prevents marking a tax-triggering recommendation as actioned — no other code path
  may set `status='actioned'` on a tax-triggering recommendation
- MSSQL-compatible SQL only — no PostgreSQL constructs
- PEP 8 throughout; full type hints and docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: confirm M2-Spec-2, M4-Spec-1, M5-Spec-1, M5-Spec-2, M6-Spec-2,
      M7-Spec-1 are all implemented and their respective test suites pass
- [ ] Check whether M6-Spec-2 created a `TaxDecisionAcknowledgement` model/table —
      reconcile per §1.6 if so
- [ ] `python manage.py makemigrations risk` (for all `rm_audit_*` models, and
      `TaxDecisionAcknowledgement` create-or-alter)
- [ ] Review generated migration — confirm no destructive changes to existing
      M6-Spec-2 tables if reconciling `TaxDecisionAcknowledgement`
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_dev`
- [ ] Apply hooks per §2.1–§2.4 to `calc_urgency`, `calc_strategy_recommendations`,
      `calc_market_conditions`, `run_backtest`, `run_montecarlo`, `run_stress_test`
- [ ] Add `risk/views/explain.py`, `risk/templates/risk/explain.html`, and the
      `explain_score` URL
- [ ] Add the "Why?" link to urgency badges in the four M6 templates (additive)
- [ ] Add `strategy_mark_actioned` view/URL and the dashboard unacknowledged-tax
      panel (§4.3)
- [ ] Register all audit admin classes (§5)
- [ ] `python manage.py daily_risk_refresh --skip-data-refresh --date 2026-06-02 --settings=portfolio_manager.settings_dev`
- [ ] Verify in SSMS:
      `SELECT COUNT(*) FROM rm_audit_score_snapshot WHERE calc_date='2026-06-02'`
      should match `SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02'`
- [ ] `python manage.py calc_strategy_recommendations --date 2026-06-02 --settings=portfolio_manager.settings_dev`
- [ ] Verify in SSMS:
      `SELECT symbol, account_id, was_eligible, COUNT(*) FROM rm_audit_recommendation_log WHERE calc_date='2026-06-02' GROUP BY symbol, account_id, was_eligible`
      — confirm both eligible and ineligible rows exist for at least one position
- [ ] Open browser: `http://saturn:8000/risk/explain/<id>/` for the AAPL/IB_SUSAN
      urgency score — verify ranked factors and historical context render
- [ ] Open browser: `http://saturn:8000/risk/strategy/<rec_id>/` for the AAPL collar
      recommendation — verify "Mark as Actioned" is disabled until tax
      acknowledgement is completed, then becomes enabled afterward
- [ ] `python manage.py audit_retention_check --settings=portfolio_manager.settings_dev`
      — verify it runs cleanly and reports zero records near the limit
- [ ] `python manage.py test risk.tests.test_audit --settings=portfolio_manager.settings_test`
- [ ] Run the **full** Phase B test suite one more time to confirm no regressions
      from the additive hooks: `python manage.py test risk core --settings=portfolio_manager.settings_test`
- [ ] `git add -A && git commit -m "feat: M8-Spec-1 audit trail, explainability, tax acknowledgement workflow"`
- [ ] `git push origin feature/risk-m8-audit`

---

## Human Approval Required

This is the final spec of Phase B — sign-off here represents approval for the
entire risk module to move toward production deployment on mercury. Before
marking complete:

1. Walk through the explainability view for at least three different entities
   (a security with High/Critical urgency, a security with Low urgency, and the
   portfolio-level entity) and confirm the plain-English explanations are
   accurate and would make sense to TOM and SUSAN without further context.
2. Confirm the tax acknowledgement gate genuinely blocks "Mark as Actioned" for
   AAPL's collar recommendation (or whichever recommendation is tax-triggering)
   until the acknowledgement form is completed, and that the dashboard panel
   correctly shows/hides it before/after acknowledgement.
3. Confirm `rm_audit_recommendation_log` for at least one position shows a
   complete picture — every strategy template either ranked or explicitly
   excluded with a reason — sufficient to answer "why wasn't X recommended?"
   a year from now without re-running any calculation.
4. Review the additive hooks' performance impact: re-run `daily_risk_refresh`
   and confirm the total duration reported by M7-Spec-1 has not materially
   increased (audit writes should be a small fraction of total runtime).

---

*M8-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev*
