# Feature Spec: M6-Spec-2 — Security Detail, Strategy Reports, Alternative Portfolios, Export

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m6-reports

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M4-Spec-1 strategy recommendations available; M5-Spec-1 backtest results available; M5-Spec-2 Monte Carlo simulation results available; M6-Spec-1 base templates and CSS established

---

## Context

M6-Spec-1 delivered the portfolio-level views — dashboard, account summaries, owner comparison, and heat map. This spec completes the reporting layer with the security-level and strategy-level detail views that TOM and SUSAN will use most often when acting on a recommendation.

The six views in this spec are the most actionable in the system. They take the user from "AAPL is High risk" (dashboard) all the way to "here is the collar recommendation, here is what it would have cost in the 2022 rate shock, here is the tax consequence, and here is the button to acknowledge I understand the tax impact before marking this actioned."

M6-Spec-2 also introduces:
- Alternative portfolio maintenance — the per-account watchlist feature from M0-Spec-1's data model, finally exposed as a UI
- Side-by-side comparison between current portfolio, historical snapshots, and alternatives
- PDF and CSV export for all report views
- The urgency history chart component, reusable across views

**Views delivered by this spec:**

| URL | View | Key data |
|---|---|---|
| risk/security/SYM/ | Security Risk Card | M1, M2, M4, M5-Spec-1 |
| risk/strategy/REC_ID/ | Strategy Recommendation Report | M4, M5-Spec-1 |
| risk/strategy/REC_ID/acknowledge/ | Tax Acknowledgement | M4, M8 workflow |
| risk/alternatives/ | Alternative Portfolio List | rm_alternative_portfolios |
| risk/alternatives/account/ACC/ | Alternative Portfolio for Account | rm_alternative_holdings |
| risk/compare/ | Side-by-Side Comparison | M1, M2, vw_portfolio_as_of |
| risk/stress/RUN_ID/ | Stress Test Results | M5-Spec-2 |
| risk/security/SYM/export/ | Export Security Card | PDF/CSV |
| risk/strategy/REC_ID/export/ | Export Strategy Report | PDF/CSV |

**Stack:**
- Python 3.12 / Django 5.1
- Bootstrap 5 (consistent with M6-Spec-1)
- Chart.js for sparklines and RSI/MACD mini-charts
- WeasyPrint for PDF export (match Phase A approach; install if not already present)
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. URL Configuration Additions

Add to `risk/urls.py` (extend M6-Spec-1 urlpatterns):

```python
urlpatterns += [
    # Security detail
    path('security/<str:symbol>/',                  views.security_detail,      name='security_detail'),
    path('security/<str:symbol>/export/<str:fmt>/', views.security_export,      name='security_export'),

    # Strategy reports
    path('strategy/<int:rec_id>/',                  views.strategy_report,      name='strategy_report'),
    path('strategy/<int:rec_id>/acknowledge/',      views.strategy_acknowledge, name='strategy_acknowledge'),
    path('strategy/<int:rec_id>/export/<str:fmt>/', views.strategy_export,      name='strategy_export'),

    # Alternative portfolios
    path('alternatives/',                            views.alternatives_list,    name='alternatives_list'),
    path('alternatives/account/<str:account_id>/',  views.alternatives_account, name='alternatives_account'),
    path('alternatives/<int:portfolio_id>/edit/',   views.alternatives_edit,    name='alternatives_edit'),
    path('alternatives/<int:portfolio_id>/delete/', views.alternatives_delete,  name='alternatives_delete'),
    path('alternatives/create/',                    views.alternatives_create,  name='alternatives_create'),

    # Comparison and simulation
    path('compare/',                                 views.portfolio_compare,   name='portfolio_compare'),
    path('stress/<int:run_id>/',                     views.stress_results,      name='stress_results'),
]
```

---

### 2. Security Risk Card

**View:** `risk/views/security.py`
**Template:** `risk/templates/risk/security_detail.html`
**URL:** `risk/security/<symbol>/`

#### 2.1 View Logic

```python
def security_detail(request, symbol: str):
    """
    Full per-security risk card. Aggregates metrics across all accounts holding this symbol.
    """
    calc_date = get_latest_calc_date()

    # Verify symbol is in universe
    security = get_object_or_404(SecurityUniverse, symbol=symbol)

    # Urgency scores for this symbol across all accounts
    urgency_scores = UrgencyScore.objects.filter(
        entity_type='security_in_account',
        symbol=symbol,
        calc_date=calc_date
    ).order_by('-score_raw')

    # Highest urgency across all accounts (worst case)
    top_urgency = urgency_scores.first()

    # M1 metrics
    security_metrics = get_object_or_404(SecurityRiskMetrics, symbol=symbol, calc_date=calc_date)

    # Market condition indicators
    market_condition = MarketCondition.objects.filter(
        symbol=symbol, calc_date=calc_date
    ).first()

    # Regime sensitivity — how does this security behave per quadrant?
    regime_sensitivities = RegimeSensitivity.objects.filter(
        asset_class=security.asset_class
    ).order_by('regime_quadrant')

    # Top 3 strategy recommendations (rank 1–3, most recent calc_date, any account)
    top_recommendations = StrategyRecommendation.objects.filter(
        symbol=symbol,
        status='active',
        calc_date=calc_date,
        rank__lte=3
    ).select_related('strategy_template').order_by('rank')[:9]
    # Limit 9 = top 3 per account; deduplicate by strategy_template in template

    # Most recent backtest for this symbol (any strategy, any account)
    recent_backtest = BacktestResult.objects.filter(
        run__symbol=symbol
    ).select_related('run', 'run__strategy_template', 'run__scenario').order_by(
        '-run__run_timestamp'
    ).first()

    # Urgency history — last 90 days
    urgency_history_90d = UrgencyScore.objects.filter(
        entity_type='security_in_account',
        symbol=symbol,
        calc_date__gte=calc_date - timedelta(days=90)
    ).values('calc_date').annotate(
        max_score=models.Max('score_raw'),
        worst_level=models.Max('urgency_level')
    ).order_by('calc_date')

    # Urgency history JSON for Chart.js sparkline
    history_labels = [str(r['calc_date']) for r in urgency_history_90d]
    history_values = [float(r['max_score']) for r in urgency_history_90d]

    # Position summary — how many accounts hold this, total MV, total UGL
    positions = get_position_summary_for_symbol(symbol, calc_date)

    context = {
        'calc_date':            calc_date,
        'security':             security,
        'top_urgency':          top_urgency,
        'urgency_scores':       urgency_scores,
        'security_metrics':     security_metrics,
        'market_condition':     market_condition,
        'regime_sensitivities': regime_sensitivities,
        'top_recommendations':  top_recommendations,
        'recent_backtest':      recent_backtest,
        'history_labels_json':  json.dumps(history_labels),
        'history_values_json':  json.dumps(history_values),
        'positions':            positions,
    }
    return render(request, 'risk/security_detail.html', context)
```

#### 2.2 Helper — Position Summary for Symbol

```python
def get_position_summary_for_symbol(symbol: str, calc_date: date) -> list[dict]:
    """
    Query core_position_history for all open positions of this symbol.
    Join with rm_market_price_history for current price and rm_fx_rates for conversion.
    Returns list of dicts: account_id, account_name, account_category, owner,
                            quantity, cost_basis_usd, current_price_usd,
                            market_value_usd, unrealised_gain_usd, is_taxable.
    """
```

#### 2.3 Template Sections — `risk/templates/risk/security_detail.html`

**Section 1: Hero header**
```html
<div class="row align-items-center mb-4">
  <div class="col">
    <h2>{{ security.symbol }}
      <small class="text-muted fs-5">{{ security.name }}</small>
    </h2>
    <span class="badge urgency-{{ top_urgency.urgency_level|lower }} fs-5">
      {{ top_urgency.urgency_level }}
    </span>
    <span class="badge bg-secondary ms-2">{{ security.asset_class }}</span>
    <span class="badge bg-light text-dark border ms-1">{{ security.sector }}</span>
  </div>
  <div class="col-auto">
    <a href="{% url 'risk:security_export' security.symbol 'pdf' %}"
       class="btn btn-outline-secondary btn-sm me-1">
      <i class="bi bi-file-pdf"></i> PDF
    </a>
    <a href="{% url 'risk:security_export' security.symbol 'csv' %}"
       class="btn btn-outline-secondary btn-sm">
      <i class="bi bi-download"></i> CSV
    </a>
  </div>
</div>
```

**Section 2: M1 Metrics Panels (3-column grid)**
```html
<div class="row g-3 mb-4">
  <!-- Volatility panel -->
  <div class="col-md-4">
    <div class="card h-100">
      <div class="card-header">Volatility (Annualised)</div>
      <div class="card-body">
        <table class="table table-sm mb-0">
          <tr><td>10-day</td><td class="text-end fw-bold">{{ security_metrics.vol_10d|floatformat:1 }}%</td></tr>
          <tr><td>21-day</td><td class="text-end fw-bold">{{ security_metrics.vol_21d|floatformat:1 }}%</td></tr>
          <tr><td>63-day</td><td class="text-end fw-bold">{{ security_metrics.vol_63d|floatformat:1 }}%</td></tr>
          <tr><td>252-day</td><td class="text-end fw-bold">{{ security_metrics.vol_252d|floatformat:1 }}%</td></tr>
        </table>
      </div>
    </div>
  </div>
  <!-- VaR panel -->
  <div class="col-md-4">
    <div class="card h-100">
      <div class="card-header">Value at Risk (1-day, 95%)</div>
      <div class="card-body">
        <table class="table table-sm mb-0">
          <tr><td>Parametric</td><td class="text-end fw-bold">{{ security_metrics.var_parametric_95|floatformat:2 }}%</td></tr>
          <tr><td>Historical</td><td class="text-end fw-bold">{{ security_metrics.var_historical_95|floatformat:2 }}%</td></tr>
          <tr><td>Cornish-Fisher</td><td class="text-end fw-bold">{{ security_metrics.var_cornish_fisher_95|floatformat:2 }}%</td></tr>
          <tr><td>CVaR (ES)</td><td class="text-end fw-bold text-danger">{{ security_metrics.cvar_95|floatformat:2 }}%</td></tr>
        </table>
      </div>
    </div>
  </div>
  <!-- Risk ratios panel -->
  <div class="col-md-4">
    <div class="card h-100">
      <div class="card-header">Risk Ratios</div>
      <div class="card-body">
        <table class="table table-sm mb-0">
          <tr><td>Beta vs SPY</td><td class="text-end fw-bold">{{ security_metrics.beta_spy|floatformat:2|default:"—" }}</td></tr>
          <tr><td>Beta vs XIU</td><td class="text-end fw-bold">{{ security_metrics.beta_xiu|floatformat:2|default:"—" }}</td></tr>
          <tr><td>Sharpe Ratio</td><td class="text-end fw-bold">{{ security_metrics.sharpe_ratio|floatformat:2|default:"—" }}</td></tr>
          <tr><td>Sortino Ratio</td><td class="text-end fw-bold">{{ security_metrics.sortino_ratio|floatformat:2|default:"—" }}</td></tr>
          <tr><td>Liquidity Score</td><td class="text-end fw-bold">{{ security_metrics.liquidity_score|floatformat:0 }}/100</td></tr>
        </table>
      </div>
    </div>
  </div>
</div>
```

**Section 3: Drawdown and UGL at Risk**
```html
<div class="row g-3 mb-4">
  <div class="col-md-6">
    <div class="card">
      <div class="card-header">Drawdown</div>
      <div class="card-body">
        <dl class="row mb-0 small">
          <dt class="col-8">Max Drawdown (holding period)</dt>
          <dd class="col-4 text-end text-danger fw-bold">{{ security_metrics.max_drawdown_from_cost|floatformat:1 }}%</dd>
          <dt class="col-8">Max Drawdown (252d)</dt>
          <dd class="col-4 text-end text-danger fw-bold">{{ security_metrics.max_drawdown_252d|floatformat:1 }}%</dd>
          <dt class="col-8">Current Drawdown from Peak</dt>
          <dd class="col-4 text-end fw-bold {% if security_metrics.current_drawdown_from_252d_peak < -0.1 %}text-danger{% endif %}">
            {{ security_metrics.current_drawdown_from_252d_peak|floatformat:1 }}%
          </dd>
        </dl>
      </div>
    </div>
  </div>
  <div class="col-md-6">
    <div class="card">
      <div class="card-header">Unrealised Gain at Risk</div>
      <div class="card-body">
        <dl class="row mb-0 small">
          <dt class="col-8">1-day VaR on Position (USD)</dt>
          <dd class="col-4 text-end fw-bold">${{ security_metrics.ugl_at_risk_95_usd|floatformat:0|intcomma }}</dd>
          <dt class="col-8">% of Total Unrealised Gain</dt>
          <dd class="col-4 text-end fw-bold {% if security_metrics.ugl_at_risk_pct_of_ugl > 0.05 %}text-warning{% endif %}">
            {{ security_metrics.ugl_at_risk_pct_of_ugl|floatformat:1 }}%
          </dd>
        </dl>
      </div>
    </div>
  </div>
</div>
```

**Section 4: Market Condition Indicators**
```html
{% if market_condition %}
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between">
    <span>Market Condition Indicators</span>
    <span class="badge bg-{% if market_condition.market_phase == 'bear' %}danger{% elif market_condition.market_phase == 'correction' %}warning{% elif market_condition.market_phase == 'uptrend' %}success{% else %}info{% endif %}">
      {{ market_condition.market_phase|title }}
    </span>
  </div>
  <div class="card-body">
    <div class="row g-3">
      <div class="col-md-3">
        <!-- RSI Gauge -->
        <h6 class="text-muted small">RSI (14-period)</h6>
        <div class="fs-3 fw-bold {% if market_condition.rsi_14 < 30 %}text-danger{% elif market_condition.rsi_14 > 70 %}text-warning{% else %}text-success{% endif %}">
          {{ market_condition.rsi_14|floatformat:1|default:"—" }}
        </div>
        <div class="small text-muted">
          {% if market_condition.rsi_14 < 30 %}Oversold
          {% elif market_condition.rsi_14 > 70 %}Overbought
          {% else %}Neutral{% endif %}
        </div>
      </div>
      <div class="col-md-3">
        <!-- SMA Status -->
        <h6 class="text-muted small">Moving Averages</h6>
        <div class="small">
          <div>vs 50d SMA: <strong class="{% if market_condition.price_vs_sma50_pct > 0 %}text-success{% else %}text-danger{% endif %}">
            {% if market_condition.price_vs_sma50_pct > 0 %}+{% endif %}{{ market_condition.price_vs_sma50_pct|floatformat:1 }}%
          </strong></div>
          <div>vs 200d SMA: <strong class="{% if market_condition.price_vs_sma200_pct > 0 %}text-success{% else %}text-danger{% endif %}">
            {% if market_condition.price_vs_sma200_pct > 0 %}+{% endif %}{{ market_condition.price_vs_sma200_pct|floatformat:1 }}%
          </strong></div>
        </div>
        {% if market_condition.crossover_type == 'death' %}
          <span class="badge bg-danger mt-1">Death Cross {{ market_condition.crossover_date }}</span>
        {% elif market_condition.crossover_type == 'golden' %}
          <span class="badge bg-success mt-1">Golden Cross {{ market_condition.crossover_date }}</span>
        {% endif %}
      </div>
      <div class="col-md-3">
        <!-- Bollinger Bands -->
        <h6 class="text-muted small">Bollinger Bands</h6>
        <div class="small">
          <div>%B: <strong>{{ market_condition.bb_pct_b|floatformat:2|default:"—" }}</strong></div>
          <div>Bandwidth: <strong>{{ market_condition.bb_bandwidth|floatformat:3|default:"—" }}</strong></div>
        </div>
        <div class="small text-muted">
          {% if market_condition.bb_pct_b > 0.8 %}Near upper band
          {% elif market_condition.bb_pct_b < 0.2 %}Near lower band
          {% else %}Within bands{% endif %}
        </div>
      </div>
      <div class="col-md-3">
        <!-- MACD + OBV -->
        <h6 class="text-muted small">MACD & Volume</h6>
        <div class="small">
          <div>Histogram: <strong class="{% if market_condition.macd_histogram > 0 %}text-success{% else %}text-danger{% endif %}">
            {{ market_condition.macd_histogram|floatformat:4|default:"—" }}
          </strong></div>
          <div>OBV Trend: <strong>{{ market_condition.obv_trend|default:"—" }}</strong></div>
          <div>52w High: <strong class="{% if market_condition.pct_from_52w_high < -0.15 %}text-danger{% endif %}">
            {{ market_condition.pct_from_52w_high|floatformat:1 }}%
          </strong></div>
        </div>
      </div>
    </div>
  </div>
</div>
{% endif %}
```

**Section 5: Regime Sensitivity Table**
```html
<div class="card mb-4">
  <div class="card-header">Regime Sensitivity — {{ security.asset_class }}</div>
  <div class="table-responsive">
    <table class="table table-sm mb-0">
      <thead class="table-light">
        <tr>
          <th>Quadrant</th><th>Description</th>
          <th class="text-center">Multiplier</th><th>Interpretation</th>
        </tr>
      </thead>
      <tbody>
        {% for rs in regime_sensitivities %}
        <tr {% if rs.regime_quadrant == current_regime.quadrant %}class="table-warning"{% endif %}>
          <td><strong>{{ rs.regime_quadrant }}</strong></td>
          <td>{{ rs.regime_quadrant|quadrant_description }}</td>
          <td class="text-center">
            <span class="badge {% if rs.sensitivity_multiplier > 1.0 %}bg-danger{% elif rs.sensitivity_multiplier < 1.0 %}bg-success{% else %}bg-secondary{% endif %}">
              {{ rs.sensitivity_multiplier }}x
            </span>
          </td>
          <td class="small text-muted">{{ rs.rationale }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

**Section 6: Top Strategy Recommendations**
```html
<div class="card mb-4">
  <div class="card-header">Strategy Recommendations</div>
  <div class="table-responsive">
    <table class="table table-sm table-hover mb-0">
      <thead class="table-light">
        <tr>
          <th>Rank</th><th>Account</th><th>Strategy</th>
          <th class="text-end">Cost (USD)</th>
          <th class="text-end">Tax Cost</th>
          <th class="text-end">VaR Reduction</th>
          <th>Tax Sensitive</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {% for rec in top_recommendations %}
        <tr>
          <td>{{ rec.rank }}</td>
          <td>{{ rec.account_id }}</td>
          <td>{{ rec.strategy_template.name }}</td>
          <td class="text-end">${{ rec.estimated_strategy_cost_usd|floatformat:0|intcomma }}</td>
          <td class="text-end {% if rec.tax_cost_estimate_usd > 0 %}text-danger fw-bold{% endif %}">
            {% if rec.tax_cost_estimate_usd > 0 %}
              ${{ rec.tax_cost_estimate_usd|floatformat:0|intcomma }}
            {% else %}—{% endif %}
          </td>
          <td class="text-end text-success">${{ rec.estimated_var_reduction_usd|floatformat:0|intcomma }}</td>
          <td>
            {% if rec.is_tax_triggering %}
              <span class="badge bg-warning text-dark">Tax Event</span>
            {% else %}
              <span class="badge bg-success">No Tax</span>
            {% endif %}
          </td>
          <td>
            <a href="{% url 'risk:strategy_report' rec.id %}" class="btn btn-xs btn-outline-primary">
              Detail
            </a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

**Section 7: Urgency History Sparkline**
```html
<div class="card mb-4">
  <div class="card-header">Urgency Score History (90 days)</div>
  <div class="card-body">
    <canvas id="urgencyHistoryChart" height="80"></canvas>
  </div>
</div>
<script>
new Chart(document.getElementById('urgencyHistoryChart'), {
  type: 'line',
  data: {
    labels: {{ history_labels_json|safe }},
    datasets: [{
      label: 'Urgency Score',
      data: {{ history_values_json|safe }},
      borderColor: '#0d6efd',
      backgroundColor: 'rgba(13,110,253,0.08)',
      fill: true, tension: 0.3, pointRadius: 2,
    }]
  },
  options: {
    plugins: { legend: { display: false } },
    scales: {
      y: { min: 0, max: 100,
           annotations: {
             low:    { type: 'line', yMin: 25, yMax: 25, borderColor: '#198754', borderDash: [4,4] },
             medium: { type: 'line', yMin: 50, yMax: 50, borderColor: '#ffc107', borderDash: [4,4] },
             high:   { type: 'line', yMin: 75, yMax: 75, borderColor: '#dc3545', borderDash: [4,4] },
           }
         }
    }
  }
});
</script>
```

**Section 8: Most Recent Backtest Summary**
```html
{% if recent_backtest %}
<div class="card mb-4">
  <div class="card-header">
    Most Recent Backtest —
    {{ recent_backtest.run.strategy_template.name }}
    {% if recent_backtest.run.scenario %}
      / {{ recent_backtest.run.scenario.display_name }}
    {% endif %}
  </div>
  <div class="card-body">
    <div class="row g-3 text-center">
      <div class="col">
        <div class="small text-muted">Return (Unhedged)</div>
        <div class="fs-5 fw-bold {% if recent_backtest.total_return_unhedged_pct < 0 %}text-danger{% else %}text-success{% endif %}">
          {{ recent_backtest.total_return_unhedged_pct|floatformat:1 }}%
        </div>
      </div>
      <div class="col">
        <div class="small text-muted">Return (Hedged)</div>
        <div class="fs-5 fw-bold {% if recent_backtest.total_return_hedged_pct < 0 %}text-danger{% else %}text-success{% endif %}">
          {{ recent_backtest.total_return_hedged_pct|floatformat:1 }}%
        </div>
      </div>
      <div class="col">
        <div class="small text-muted">Max Drawdown (Unhedged)</div>
        <div class="fs-5 fw-bold text-danger">{{ recent_backtest.max_drawdown_unhedged|floatformat:1 }}%</div>
      </div>
      <div class="col">
        <div class="small text-muted">Max Drawdown (Hedged)</div>
        <div class="fs-5 fw-bold">{{ recent_backtest.max_drawdown_hedged|floatformat:1 }}%</div>
      </div>
      <div class="col">
        <div class="small text-muted">Hedge Effectiveness</div>
        <div class="fs-5 fw-bold text-primary">{{ recent_backtest.hedge_effectiveness_ratio|floatformat:2|default:"—" }}x</div>
      </div>
      <div class="col">
        <div class="small text-muted">Stop Triggered</div>
        <div class="fs-5">{% if recent_backtest.stop_triggered %}<span class="badge bg-warning">Yes</span>{% else %}<span class="badge bg-success">No</span>{% endif %}</div>
      </div>
    </div>
  </div>
</div>
{% endif %}
```

---

### 3. Strategy Recommendation Report

**View:** `risk/views/strategy.py`
**Template:** `risk/templates/risk/strategy_report.html`
**URL:** `risk/strategy/<rec_id>/`

#### 3.1 View Logic

```python
def strategy_report(request, rec_id: int):
    """
    Full detail report for one strategy recommendation.
    Shows all candidates considered, not just the selected one.
    Links to backtest if available.
    """
    recommendation = get_object_or_404(
        StrategyRecommendation.objects.select_related(
            'strategy_template', 'strategy_template__instrument_links__instrument'
        ),
        id=rec_id
    )

    # All candidates for the same symbol+account+date (all ranks, not just this one)
    all_candidates = StrategyRecommendation.objects.filter(
        symbol=recommendation.symbol,
        account_id=recommendation.account_id,
        calc_date=recommendation.calc_date,
    ).select_related('strategy_template').order_by('rank')

    # Most recent backtest for this symbol + strategy template combination
    related_backtest = BacktestResult.objects.filter(
        run__symbol=recommendation.symbol,
        run__strategy_template=recommendation.strategy_template,
    ).select_related('run__scenario').order_by('-run__run_timestamp').first()

    # Has this recommendation been tax-acknowledged?
    tax_acknowledgement = None
    if recommendation.is_tax_triggering:
        from risk.models import TaxDecisionAcknowledgement
        tax_acknowledgement = TaxDecisionAcknowledgement.objects.filter(
            recommendation_id=rec_id
        ).first()

    # Can the user mark this as actioned?
    can_action = (
        recommendation.status == 'active' and
        (not recommendation.is_tax_triggering or tax_acknowledgement is not None)
    )

    context = {
        'recommendation':      recommendation,
        'all_candidates':      all_candidates,
        'related_backtest':    related_backtest,
        'tax_acknowledgement': tax_acknowledgement,
        'can_action':          can_action,
    }
    return render(request, 'risk/strategy_report.html', context)


def strategy_acknowledge(request, rec_id: int):
    """
    Tax acknowledgement workflow for tax-triggering recommendations.
    GET: show acknowledgement form with tax cost estimate.
    POST: record acknowledgement to rm_tax_decision_acknowledgement.
    """
    recommendation = get_object_or_404(StrategyRecommendation, id=rec_id,
                                        is_tax_triggering=True)

    if request.method == 'POST':
        user_notes = request.POST.get('user_notes', '')
        TaxDecisionAcknowledgement.objects.create(
            recommendation_id=rec_id,
            presented_to_user=recommendation.owner,
            presented_at=recommendation.calc_timestamp,
            acknowledged_at=timezone.now(),
            acknowledged_by=request.user.username if request.user.is_authenticated else 'anonymous',
            tax_cost_estimate_presented=recommendation.tax_cost_estimate_usd,
            user_notes=user_notes,
        )
        messages.success(request, "Tax impact acknowledged. You may now mark this recommendation as actioned.")
        return redirect('risk:strategy_report', rec_id=rec_id)

    context = {
        'recommendation': recommendation,
    }
    return render(request, 'risk/strategy_acknowledge.html', context)
```

#### 3.2 Template Key Sections — `risk/templates/risk/strategy_report.html`

**Tax Impact Warning Panel** (shown prominently when is_tax_triggering=True):
```html
{% if recommendation.is_tax_triggering %}
<div class="alert {% if tax_acknowledgement %}alert-success{% else %}alert-danger{% endif %} mb-4">
  <h5 class="alert-heading">
    <i class="bi bi-exclamation-triangle-fill"></i>
    Tax-Triggering Strategy
  </h5>
  <p>{{ recommendation.tax_impact_note }}</p>
  {% if not tax_acknowledgement %}
    <hr>
    <p class="mb-0">
      You must acknowledge the tax impact before this recommendation can be marked as actioned.
    </p>
    <a href="{% url 'risk:strategy_acknowledge' recommendation.id %}"
       class="btn btn-danger mt-2">
      Review and Acknowledge Tax Impact
    </a>
  {% else %}
    <p class="mb-0 small">
      Acknowledged by {{ tax_acknowledgement.acknowledged_by }}
      on {{ tax_acknowledgement.acknowledged_at|date:"Y-m-d H:i" }}.
      {% if tax_acknowledgement.user_notes %}
        Notes: {{ tax_acknowledgement.user_notes }}
      {% endif %}
    </p>
  {% endif %}
</div>
{% endif %}
```

**Mark as Actioned Button**:
```html
{% if can_action and recommendation.status == 'active' %}
<form method="post" action="{% url 'risk:strategy_mark_actioned' recommendation.id %}">
  {% csrf_token %}
  <button type="submit" class="btn btn-primary">
    <i class="bi bi-check-circle"></i> Mark as Actioned
  </button>
</form>
{% elif not can_action and recommendation.is_tax_triggering %}
<button class="btn btn-primary" disabled title="Acknowledge tax impact first">
  Mark as Actioned (tax acknowledgement required)
</button>
{% endif %}
```

**All Candidates Table**:
```html
<div class="card mb-4">
  <div class="card-header">All Strategies Considered — Ranked by Cost-Effectiveness</div>
  <div class="table-responsive">
    <table class="table table-sm table-hover mb-0">
      <thead class="table-light">
        <tr>
          <th>Rank</th><th>Strategy</th>
          <th class="text-end">Strategy Cost</th>
          <th class="text-end">Tax Cost</th>
          <th class="text-end">VaR Reduction</th>
          <th class="text-end">Cost/VaR$</th>
          <th>Tax Flag</th>
        </tr>
      </thead>
      <tbody>
        {% for cand in all_candidates %}
        <tr {% if cand.id == recommendation.id %}class="table-primary"{% endif %}>
          <td><strong>{{ cand.rank }}</strong></td>
          <td>{{ cand.strategy_template.name }}</td>
          <td class="text-end">${{ cand.estimated_strategy_cost_usd|floatformat:0|intcomma }}</td>
          <td class="text-end {% if cand.tax_cost_estimate_usd > 0 %}text-danger{% endif %}">
            {% if cand.tax_cost_estimate_usd > 0 %}${{ cand.tax_cost_estimate_usd|floatformat:0|intcomma }}{% else %}—{% endif %}
          </td>
          <td class="text-end text-success">${{ cand.estimated_var_reduction_usd|floatformat:0|intcomma }}</td>
          <td class="text-end small">
            {% if cand.cost_per_var_reduction_usd %}
              {{ cand.cost_per_var_reduction_usd|floatformat:3 }}
            {% else %}∞{% endif %}
          </td>
          <td>
            {% if cand.is_tax_triggering %}
              <span class="badge bg-warning text-dark">Tax</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

---

### 4. Alternative Portfolio Maintenance

**Views:** `risk/views/alternatives.py`
**Templates:** `risk/templates/risk/alternatives_*.html`
**URLs:** `risk/alternatives/` and sub-paths

#### 4.1 View Logic

```python
def alternatives_list(request):
    """List all alternative portfolios across all accounts."""
    portfolios = AlternativePortfolio.objects.filter(
        is_active=True
    ).select_related('account').prefetch_related('holdings').order_by('account', 'name')
    return render(request, 'risk/alternatives_list.html', {'portfolios': portfolios})


def alternatives_account(request, account_id: str):
    """
    List and create alternative portfolios for a specific account.
    Also provides risk preview for each alternative.
    """
    account = get_object_or_404(Account, account_id=account_id)
    portfolios = AlternativePortfolio.objects.filter(
        account=account, is_active=True
    ).prefetch_related('holdings')

    context = {
        'account':    account,
        'portfolios': portfolios,
    }
    return render(request, 'risk/alternatives_account.html', context)


def alternatives_edit(request, portfolio_id: int):
    """
    Edit an alternative portfolio's name and holdings.
    Holdings must sum to 100% — enforce server-side and client-side.
    """
    portfolio = get_object_or_404(AlternativePortfolio, id=portfolio_id)
    symbols_available = SecurityUniverse.objects.filter(is_active=True).order_by('symbol')

    if request.method == 'POST':
        # Parse holdings from POST: symbol_N, weight_N pairs
        holdings_data = []
        i = 0
        while f'symbol_{i}' in request.POST:
            sym = request.POST.get(f'symbol_{i}', '').strip()
            try:
                wt = Decimal(request.POST.get(f'weight_{i}', '0')) / 100  # convert % to fraction
            except Exception:
                wt = Decimal('0')
            if sym and wt > 0:
                holdings_data.append({'symbol': sym, 'target_weight': wt})
            i += 1

        # Validate total weight
        total = sum(h['target_weight'] for h in holdings_data)
        if abs(total - Decimal('1.0')) > Decimal('0.001'):
            messages.error(request, f"Holdings must sum to 100%. Current total: {total * 100:.2f}%")
        else:
            # Update holdings atomically
            with transaction.atomic():
                portfolio.holdings.all().delete()
                for h in holdings_data:
                    AlternativeHolding.objects.create(
                        portfolio=portfolio,
                        symbol=h['symbol'],
                        target_weight=h['target_weight']
                    )
                portfolio.name = request.POST.get('name', portfolio.name)
                portfolio.description = request.POST.get('description', portfolio.description)
                portfolio.save()
            messages.success(request, "Alternative portfolio updated.")
            return redirect('risk:alternatives_account', account_id=portfolio.account.account_id)

    context = {
        'portfolio':          portfolio,
        'symbols_available':  symbols_available,
        'holdings':           portfolio.holdings.all(),
    }
    return render(request, 'risk/alternatives_edit.html', context)


def alternatives_create(request):
    """Create a new alternative portfolio for a given account."""
    accounts = Account.objects.filter(exclude_from_risk=False).order_by('account_id')

    if request.method == 'POST':
        account = get_object_or_404(Account, account_id=request.POST.get('account_id'))
        portfolio = AlternativePortfolio.objects.create(
            owner=account.owner,
            account=account,
            name=request.POST.get('name', 'New Alternative'),
            description=request.POST.get('description', ''),
        )
        return redirect('risk:alternatives_edit', portfolio_id=portfolio.id)

    return render(request, 'risk/alternatives_create.html', {'accounts': accounts})
```

#### 4.2 Weight Validation JavaScript (client-side, alternatives_edit.html)

```javascript
// Real-time weight total validation
function updateWeightTotal() {
    let total = 0;
    document.querySelectorAll('.holding-weight').forEach(input => {
        total += parseFloat(input.value) || 0;
    });
    const totalEl = document.getElementById('weight-total');
    totalEl.textContent = total.toFixed(2) + '%';
    totalEl.className = Math.abs(total - 100) < 0.1
        ? 'fw-bold text-success'
        : 'fw-bold text-danger';
    document.getElementById('save-btn').disabled = Math.abs(total - 100) >= 0.1;
}
document.querySelectorAll('.holding-weight').forEach(el =>
    el.addEventListener('input', updateWeightTotal));
```

---

### 5. Side-by-Side Portfolio Comparison

**View:** `risk/views/compare.py`
**Template:** `risk/templates/risk/portfolio_compare.html`
**URL:** `risk/compare/`

#### 5.1 View Logic

```python
def portfolio_compare(request):
    """
    Compare current portfolio vs historical snapshot vs alternative portfolio.
    Query params: as_of_date (YYYY-MM-DD), alt_portfolio_id (int)
    """
    calc_date = get_latest_calc_date()
    as_of_date_str = request.GET.get('as_of_date', '')
    alt_portfolio_id = request.GET.get('alt_portfolio_id')

    # Current portfolio metrics
    current_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='portfolio', entity_id='ALL', calc_date=calc_date
    ).first()
    current_score = UrgencyScore.objects.filter(
        entity_type='portfolio', entity_id='ALL', calc_date=calc_date
    ).first()

    # Historical snapshot (as-of-date)
    historical_metrics = None
    historical_score = None
    as_of_date = None
    if as_of_date_str:
        try:
            as_of_date = date.fromisoformat(as_of_date_str)
            historical_metrics = PortfolioRiskMetrics.objects.filter(
                entity_type='portfolio', entity_id='ALL', calc_date=as_of_date
            ).first()
            historical_score = UrgencyScore.objects.filter(
                entity_type='portfolio', entity_id='ALL', calc_date=as_of_date
            ).first()
        except ValueError:
            pass

    # Alternative portfolio
    alt_portfolio = None
    alt_risk_preview = None
    if alt_portfolio_id:
        alt_portfolio = AlternativePortfolio.objects.filter(
            id=alt_portfolio_id, is_active=True
        ).prefetch_related('holdings').first()
        if alt_portfolio:
            # Compute on-demand risk preview for alternative portfolio
            alt_risk_preview = compute_alternative_risk_preview(alt_portfolio, calc_date)

    # All alternative portfolios for selector
    all_alternatives = AlternativePortfolio.objects.filter(is_active=True).select_related('account')

    context = {
        'calc_date':          calc_date,
        'current_metrics':    current_metrics,
        'current_score':      current_score,
        'as_of_date':         as_of_date,
        'historical_metrics': historical_metrics,
        'historical_score':   historical_score,
        'alt_portfolio':      alt_portfolio,
        'alt_risk_preview':   alt_risk_preview,
        'all_alternatives':   all_alternatives,
    }
    return render(request, 'risk/portfolio_compare.html', context)


def compute_alternative_risk_preview(portfolio: AlternativePortfolio, calc_date: date) -> dict:
    """
    Compute estimated risk metrics for an alternative portfolio on-demand.
    Uses rm_security_risk_metrics for individual metrics and builds a simple
    weighted portfolio metric (not full Markowitz — that requires correlation matrix recompute).

    Returns dict with:
        weighted_var_95_pct: float  (simple weighted average, not correlated)
        weighted_beta_spy: float
        hhi_symbol: float
        position_count: int
        top1_symbol: str
        top1_weight: float
    """
```

---

### 6. Stress Test Results View

**View:** `risk/views/stress.py`
**Template:** `risk/templates/risk/stress_results.html`
**URL:** `risk/stress/<run_id>/`

```python
def stress_results(request, run_id: int):
    """
    Display stress test or Monte Carlo simulation results.
    Works for both simulation_type='stress_test' and 'montecarlo'.
    """
    sim_run = get_object_or_404(SimulationRun, id=run_id)
    sim_result = get_object_or_404(SimulationResult, run=sim_run)

    # Parse per-position breakdown for stress tests
    per_position = []
    if sim_result.stress_per_position_json:
        per_position = json.loads(sim_result.stress_per_position_json)
        per_position.sort(key=lambda x: x['loss_usd'])  # worst losses first

    # Parse percentile series for Monte Carlo chart
    percentile_series = {}
    if sim_result.percentile_series_json:
        percentile_series = json.loads(sim_result.percentile_series_json)

    context = {
        'sim_run':           sim_run,
        'sim_result':        sim_result,
        'per_position':      per_position,
        'percentile_series': percentile_series,
        'percentile_json':   sim_result.percentile_series_json or '{}',
    }
    return render(request, 'risk/stress_results.html', context)
```

---

### 7. Export (PDF and CSV)

Create `risk/services/export.py`.

```python
def export_security_card_pdf(symbol: str, calc_date: date) -> HttpResponse:
    """
    Generate a PDF version of the security risk card using WeasyPrint.
    Renders risk/templates/risk/security_detail_print.html (print-optimised version).
    """
    # Build same context as security_detail view
    context = build_security_detail_context(symbol, calc_date)
    html_string = render_to_string('risk/security_detail_print.html', context)
    html = weasyprint.HTML(string=html_string, base_url=settings.STATIC_ROOT)
    pdf_bytes = html.write_pdf()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="risk_{symbol}_{calc_date}.pdf"'
    return response


def export_security_card_csv(symbol: str, calc_date: date) -> HttpResponse:
    """
    Export security risk metrics as CSV.
    One row per metric name/value pair (vertical format for readability).
    """
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="risk_{symbol}_{calc_date}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Metric', 'Value', 'Notes'])

    security_metrics = SecurityRiskMetrics.objects.get(symbol=symbol, calc_date=calc_date)
    writer.writerow(['Symbol', symbol, ''])
    writer.writerow(['Calc Date', str(calc_date), ''])
    writer.writerow(['Vol 21d (%)', float(security_metrics.vol_21d or 0) * 100, 'Annualised'])
    writer.writerow(['VaR Parametric 95% (%)', float(security_metrics.var_parametric_95 or 0) * 100, '1-day'])
    writer.writerow(['VaR Cornish-Fisher 95% (%)', float(security_metrics.var_cornish_fisher_95 or 0) * 100, '1-day'])
    writer.writerow(['CVaR 95% (%)', float(security_metrics.cvar_95 or 0) * 100, '1-day'])
    writer.writerow(['Beta vs SPY', float(security_metrics.beta_spy or 0), '252d'])
    writer.writerow(['Sharpe Ratio', float(security_metrics.sharpe_ratio or 0), ''])
    writer.writerow(['Liquidity Score', float(security_metrics.liquidity_score or 0), '0-100'])
    writer.writerow(['UGL at Risk USD', float(security_metrics.ugl_at_risk_95_usd or 0), '1-day 95%'])
    return response


def export_strategy_report_pdf(rec_id: int) -> HttpResponse:
    """
    Generate PDF for a strategy recommendation report.
    """
    recommendation = get_object_or_404(StrategyRecommendation, id=rec_id)
    context = {'recommendation': recommendation}
    html_string = render_to_string('risk/strategy_report_print.html', context)
    html = weasyprint.HTML(string=html_string)
    pdf_bytes = html.write_pdf()
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="strategy_{recommendation.symbol}_{recommendation.account_id}_{rec_id}.pdf"'
    )
    return response
```

**Export view wrappers** in `risk/views/exports.py`:

```python
def security_export(request, symbol: str, fmt: str):
    calc_date = get_latest_calc_date()
    if fmt == 'pdf':
        return export_security_card_pdf(symbol, calc_date)
    elif fmt == 'csv':
        return export_security_card_csv(symbol, calc_date)
    raise Http404

def strategy_export(request, rec_id: int, fmt: str):
    if fmt == 'pdf':
        return export_strategy_report_pdf(rec_id)
    raise Http404
```

---

### 8. Template Tag Additions

Add to `risk/templatetags/risk_tags.py` (extend M6-Spec-1 file):

```python
@register.filter
def quadrant_description(quadrant: str) -> str:
    """Human-readable quadrant description."""
    return {
        'Q1': 'Growth + Low Inflation',
        'Q2': 'Growth + High Inflation',
        'Q3': 'Contraction + Low Inflation',
        'Q4': 'Stagflation',
    }.get(quadrant, quadrant)

@register.filter
def pct(value, decimals=1):
    """Format decimal as percentage string."""
    try:
        return f"{float(value) * 100:.{decimals}f}%"
    except (TypeError, ValueError):
        return "—"
```

---

### 9. Tests

File: `risk/tests/test_security_strategy_views.py`

```python
from django.test import TestCase, Client
from django.urls import reverse
from risk.models import (
    SecurityUniverse, SecurityRiskMetrics, MarketCondition,
    UrgencyScore, StrategyRecommendation, StrategyTemplate,
    AlternativePortfolio, AlternativeHolding, SimulationRun, SimulationResult
)
from core.models import Account
import datetime, json


class SecurityDetailViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.today = datetime.date(2026, 6, 2)
        cls.client = Client()
        # Seed minimal SecurityUniverse, SecurityRiskMetrics, UrgencyScore for VDY
        SecurityUniverse.objects.create(
            symbol='VDY', name='Vanguard FTSE Canadian High Div',
            security_type='held', asset_class='etf_equity', sector='Dividend',
            primary_currency='CAD', is_held=True, is_active=True
        )
        SecurityRiskMetrics.objects.create(
            symbol='VDY', calc_date=cls.today,
            vol_21d=0.085, var_parametric_95=0.0088, beta_spy=0.45,
            liquidity_score=85.0, sharpe_ratio=1.2, price_rows_used=252
        )
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='TAXABLE_TOM_SUSAN::VDY',
            symbol='VDY', account_id='TAXABLE_TOM_SUSAN',
            calc_date=cls.today, score_raw=38.0, urgency_level='Medium',
            component_scores_json='{}', dominant_component='m1_metrics'
        )

    def test_security_detail_returns_200(self):
        response = self.client.get(reverse('risk:security_detail', kwargs={'symbol': 'VDY'}))
        self.assertEqual(response.status_code, 200)

    def test_security_detail_shows_symbol_name(self):
        response = self.client.get(reverse('risk:security_detail', kwargs={'symbol': 'VDY'}))
        self.assertContains(response, 'VDY')

    def test_security_detail_404_for_unknown_symbol(self):
        response = self.client.get(reverse('risk:security_detail', kwargs={'symbol': 'XXXX'}))
        self.assertEqual(response.status_code, 404)

    def test_security_export_csv_returns_csv(self):
        response = self.client.get(
            reverse('risk:security_export', kwargs={'symbol': 'VDY', 'fmt': 'csv'})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_security_export_invalid_format_returns_404(self):
        response = self.client.get(
            reverse('risk:security_export', kwargs={'symbol': 'VDY', 'fmt': 'xlsx'})
        )
        self.assertEqual(response.status_code, 404)


class AlternativePortfolioTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.get(account_id='TFSA_TOM')
        cls.portfolio = AlternativePortfolio.objects.create(
            owner='TOM', account=cls.account,
            name='Test Alt', description='Test alternative'
        )

    def test_alternatives_list_returns_200(self):
        response = self.client.get(reverse('risk:alternatives_list'))
        self.assertEqual(response.status_code, 200)

    def test_alternatives_account_returns_200(self):
        response = self.client.get(
            reverse('risk:alternatives_account', kwargs={'account_id': 'TFSA_TOM'})
        )
        self.assertEqual(response.status_code, 200)

    def test_edit_valid_weights_saves_correctly(self):
        # Post holdings summing to 100%
        response = self.client.post(
            reverse('risk:alternatives_edit', kwargs={'portfolio_id': self.portfolio.id}),
            data={
                'name': 'Updated Alt',
                'description': 'Updated',
                'symbol_0': 'QQQ', 'weight_0': '60',
                'symbol_1': 'CGDV', 'weight_1': '40',
            }
        )
        # Should redirect on success
        self.assertEqual(response.status_code, 302)
        # Holdings should be saved
        self.portfolio.refresh_from_db()
        holdings = self.portfolio.holdings.all()
        self.assertEqual(holdings.count(), 2)
        total = sum(h.target_weight for h in holdings)
        self.assertAlmostEqual(float(total), 1.0, places=3)

    def test_edit_weights_not_summing_to_100_shows_error(self):
        response = self.client.post(
            reverse('risk:alternatives_edit', kwargs={'portfolio_id': self.portfolio.id}),
            data={
                'name': 'Bad Alt',
                'symbol_0': 'QQQ', 'weight_0': '70',
                'symbol_1': 'CGDV', 'weight_1': '20',  # Only 90% total
            }
        )
        # Should not redirect — stays on form with error
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'must sum to 100')

    def test_create_alternative_portfolio(self):
        count_before = AlternativePortfolio.objects.count()
        response = self.client.post(
            reverse('risk:alternatives_create'),
            data={'account_id': 'TFSA_TOM', 'name': 'New Test Alt'}
        )
        self.assertEqual(AlternativePortfolio.objects.count(), count_before + 1)


class StressResultsViewTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.today = datetime.date(2026, 6, 2)
        cls.run = SimulationRun.objects.create(
            simulation_type='stress_test',
            entity_type='portfolio', entity_id='ALL',
            scenario_name='2022_rate_shock',
            calc_date=cls.today
        )
        cls.result = SimulationResult.objects.create(
            run=cls.run,
            portfolio_value_start_usd=3000000,
            stress_loss_unhedged_usd=-540000,
            stress_loss_unhedged_pct=-0.18,
            stress_per_position_json=json.dumps([
                {'symbol': 'VFV', 'account': 'TAXABLE_TOM_SUSAN',
                 'loss_usd': -82000, 'loss_pct': -0.174}
            ])
        )

    def test_stress_results_returns_200(self):
        response = self.client.get(
            reverse('risk:stress_results', kwargs={'run_id': self.run.id})
        )
        self.assertEqual(response.status_code, 200)

    def test_stress_results_shows_loss(self):
        response = self.client.get(
            reverse('risk:stress_results', kwargs={'run_id': self.run.id})
        )
        self.assertContains(response, '2022_rate_shock')
        self.assertContains(response, 'VFV')

    def test_stress_results_404_for_invalid_run(self):
        response = self.client.get(
            reverse('risk:stress_results', kwargs={'run_id': 99999})
        )
        self.assertEqual(response.status_code, 404)
```

Run with: python manage.py test risk.tests.test_security_strategy_views --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement M8 explainability view — that is M8-Spec-1
- DO NOT implement M8 audit tables — that is M8-Spec-1
- DO NOT push to git (manual step)
- WeasyPrint must be pip-installed if not already present: `pip install weasyprint`
- Weight validation for alternative portfolios is enforced both client-side (JavaScript) and server-side (view) — never trust only one
- All export filenames must include symbol/account and date to avoid confusion
- The tax acknowledgement workflow records to rm_tax_decision_acknowledgement — this table is defined in M8-Spec-1 but its write path is triggered here; coordinate with M8 team on model placement
- PEP 8 throughout; views must not contain raw SQL

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_strategy_recommendation WHERE calc_date='2026-06-02' — expect rows
- [ ] Verify gate: SELECT COUNT(*) FROM rm_backtest_result — expect rows from M5-Spec-1
- [ ] Verify gate: SELECT COUNT(*) FROM rm_simulation_result — expect rows from M5-Spec-2
- [ ] pip install weasyprint --break-system-packages (on mercury) or in venv
- [ ] Add new URL patterns to risk/urls.py (extend from M6-Spec-1)
- [ ] Add quadrant_description and pct filters to risk/templatetags/risk_tags.py
- [ ] python manage.py test risk.tests.test_security_strategy_views --settings=portfolio_manager.settings_test
- [ ] Open browser: http://saturn:8000/risk/security/VDY/ — verify all panels render
- [ ] Open browser: http://saturn:8000/risk/security/AAPL/ — verify UGL at risk panel highlighted
- [ ] Open browser: http://saturn:8000/risk/strategy/1/ — verify candidates table and tax warning
- [ ] Open browser: http://saturn:8000/risk/alternatives/ — verify list renders
- [ ] Test create + edit alternative portfolio: verify weight validation fires at 99% and 101%
- [ ] Test compare view: http://saturn:8000/risk/compare/?as_of_date=2026-01-01
- [ ] Test CSV export: http://saturn:8000/risk/security/VDY/export/csv/
- [ ] Test PDF export: http://saturn:8000/risk/security/VDY/export/pdf/
- [ ] git add -A && git commit -m "feat: M6-Spec-2 security detail + strategy reports + alternatives + export"
- [ ] git push origin feature/risk-m6-reports

---

## Human Approval Required

Review the security detail page for AAPL (IB_SUSAN) before marking complete. The UGL at risk panel should be prominent, the collar recommendation should appear at rank 1, and the backtest summary should show the 2022 rate shock result if M5 runs have been completed.

---

M6-Spec-2 | Portfolio Manager Phase B | June 2026 | tdwdev
