# Feature Spec: M6-Spec-1 — Portfolio Dashboard, Account & Owner Views, Risk Heat Map

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m6-dashboard

Architecture ref: PM_PhaseB_Architecture_Final_v2.0.md
Depends on: M2-Spec-2 fully implemented — rm_urgency_score, rm_regime_state, rm_market_condition all populated; M1-Spec-2 rm_portfolio_risk_metrics available for all entities

---

## Context

This spec delivers the first user-visible output of the entire Phase B risk module. Every prior spec has produced tables of numbers; this one turns them into something TOM and SUSAN can actually read and act on.

The UI is Django views + Bootstrap 5 templates, consistent with the Phase A Portfolio Manager UI already in production on mercury. No new frontend framework — use the existing template base, extend it with risk-specific components. Chart.js is used for the heat map and index concentration bar chart (already a common CDN dependency in Django projects of this type).

M6-Spec-1 covers the high-level views only: portfolio dashboard, account summaries, owner summaries, and the risk heat map. The security-level detail pages, strategy reports, and alternative portfolio UI are M6-Spec-2, which requires M4 and M5 to be complete.

**Views delivered by this spec:**

| URL | View | Gate data required |
|---|---|---|
| risk/dashboard/ | Portfolio Risk Dashboard | M2-Spec-2 |
| risk/account/ACC/ | Account Risk Summary | M2-Spec-2, M1-Spec-2 |
| risk/owner/TOM/ | Owner Summary (TOM) | M2-Spec-2, M1-Spec-2 |
| risk/owner/SUSAN/ | Owner Summary (SUSAN) | M2-Spec-2, M1-Spec-2 |
| risk/heatmap/ | Risk Heat Map | M2-Spec-2 |

**Portfolio accounts to support (June 2, 2026):**
RRSP_TOM, LIF_TOM, IB_TOM, TFSA_TOM, RRIF_SUSAN, LIF_SUSAN, IB_SUSAN, TFSA_SUSAN, IB_JOINT, TAXABLE_TOM_SUSAN, TAXABLE_CAR (excluded by default; toggle to include).

**Stack:**
- Python 3.12 / Django 5.1
- Bootstrap 5 (match existing Phase A templates)
- Chart.js (CDN) for heat map and bar charts
- MSSQL 2025 (dev: PortfolioDEV)
- Project location: C:\AI-DevTeam\projects\portfolio_manager

---

## Requirements

### 1. URL Configuration

Add to `risk/urls.py` (create if it does not exist):

```python
from django.urls import path
from risk import views

app_name = 'risk'

urlpatterns = [
    path('dashboard/',                     views.dashboard,         name='dashboard'),
    path('account/<str:account_id>/',      views.account_summary,   name='account_summary'),
    path('owner/<str:owner>/',             views.owner_summary,     name='owner_summary'),
    path('heatmap/',                       views.risk_heatmap,      name='heatmap'),
]
```

Include in `portfolio_manager/urls.py`:
```python
path('risk/', include('risk.urls', namespace='risk')),
```

---

### 2. Context Helper — Latest Calc Date

All views require the most recent `calc_date` across the risk tables. Create a shared helper:

```python
# risk/views/helpers.py

def get_latest_calc_date() -> date | None:
    """
    Return the most recent calc_date in rm_urgency_score.
    Returns None if no data has been calculated yet.
    """
    from risk.models import UrgencyScore
    result = UrgencyScore.objects.aggregate(latest=models.Max('calc_date'))
    return result['latest']

def get_show_excluded(request) -> bool:
    """
    Read the 'show_excluded' query param or session flag.
    When True, TAXABLE_CAR and any other exclude_from_risk accounts are included.
    Default: False (excluded accounts hidden).
    """
    return request.GET.get('show_excluded', 'false').lower() == 'true'
```

---

### 3. Portfolio Risk Dashboard

**View:** `risk/views/dashboard.py`
**Template:** `risk/templates/risk/dashboard.html`
**URL:** `risk/dashboard/`

#### 3.1 View Logic

```python
def dashboard(request):
    """
    Portfolio-level risk dashboard.
    Reads from: rm_urgency_score (entity_type='portfolio'),
                rm_portfolio_risk_metrics (entity_type='portfolio'),
                rm_regime_state,
                rm_market_condition,
                vw_current_portfolio (for top UGL positions),
                core_position_history (for UGL computation).
    """
    calc_date = get_latest_calc_date()
    show_excluded = get_show_excluded(request)

    # Portfolio urgency score
    portfolio_score = UrgencyScore.objects.filter(
        entity_type='portfolio',
        entity_id='ALL',
        calc_date=calc_date
    ).first()

    # Portfolio risk metrics
    portfolio_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='portfolio',
        entity_id='ALL',
        calc_date=calc_date
    ).first()

    # Current regime
    regime = RegimeState.objects.filter(calc_date__lte=calc_date).order_by('-calc_date').first()

    # Market condition summary: count of death crosses and RSI extremes across held symbols
    market_conditions = MarketCondition.objects.filter(calc_date=calc_date)
    death_cross_count = market_conditions.filter(crossover_type='death').count()
    oversold_count = market_conditions.filter(rsi_14__lt=30).count()
    overbought_count = market_conditions.filter(rsi_14__gt=70).count()

    # Top 5 unrealised gains with tax-sensitive flag
    # Query positions from vw_current_portfolio joined with latest prices
    # For each symbol: compute UGL = (current_price - cost_basis_per_unit) * quantity
    # Flag as tax_sensitive if account_category in ('Investment', 'Investment Joint')
    top_ugl_positions = get_top_ugl_positions(
        calc_date=calc_date,
        limit=5,
        include_excluded=show_excluded
    )

    # Account urgency scores for nav/summary strip
    account_scores = UrgencyScore.objects.filter(
        entity_type='account',
        calc_date=calc_date
    ).order_by('-score_raw')

    if not show_excluded:
        excluded_ids = list(
            Account.objects.filter(exclude_from_risk=True).values_list('account_id', flat=True)
        )
        account_scores = account_scores.exclude(entity_id__in=excluded_ids)

    # Effective index concentration (from portfolio_metrics)
    index_concentration = {
        'SP500':         float(portfolio_metrics.effective_sp500_weight or 0) * 100,
        'NASDAQ100':     float(portfolio_metrics.effective_nasdaq100_weight or 0) * 100,
        'TSX':           float(portfolio_metrics.effective_tsx_weight or 0) * 100,
        'Semiconductor': float(portfolio_metrics.effective_semiconductor_weight or 0) * 100,
        'Technology':    float(portfolio_metrics.effective_technology_weight or 0) * 100,
        'Dividend':      float(portfolio_metrics.effective_dividend_weight or 0) * 100,
    }

    context = {
        'calc_date':            calc_date,
        'show_excluded':        show_excluded,
        'portfolio_score':      portfolio_score,
        'portfolio_metrics':    portfolio_metrics,
        'regime':               regime,
        'death_cross_count':    death_cross_count,
        'oversold_count':       oversold_count,
        'overbought_count':     overbought_count,
        'top_ugl_positions':    top_ugl_positions,
        'account_scores':       account_scores,
        'index_concentration':  index_concentration,  # for Chart.js bar chart
    }
    return render(request, 'risk/dashboard.html', context)
```

#### 3.2 Helper — Top UGL Positions

```python
def get_top_ugl_positions(calc_date: date, limit: int, include_excluded: bool) -> list[dict]:
    """
    Return the top N positions by unrealised gain in USD, with tax-sensitivity flag.

    Query logic:
    1. Load all open positions from core_position_history (date_to IS NULL)
    2. Join with rm_market_price_history (latest close for calc_date)
    3. Join with rm_fx_rates (for CAD conversion)
    4. Join with core_account (for account_category and exclude_from_risk)
    5. Compute UGL per position: (current_price_usd - cost_basis_per_unit_usd) * quantity
    6. Filter: exclude_from_risk=True positions excluded unless include_excluded=True
    7. Sort descending by UGL, limit to N

    Returns list of dicts with keys:
        symbol, account_id, account_name, account_category, owner,
        quantity, current_price_usd, cost_basis_usd, market_value_usd,
        unrealised_gain_usd, unrealised_gain_cad, is_tax_sensitive
    """
```

#### 3.3 Template Sections — `risk/templates/risk/dashboard.html`

Extend the Phase A base template. Use `{% extends "base.html" %}`.

**Section 1: Header strip**
```html
<!-- Urgency badge + key metrics in a single responsive row -->
<div class="row g-3 mb-4">
  <div class="col-md-2">
    <!-- Large urgency badge: bg-danger (Critical), bg-warning (High),
         bg-info (Medium), bg-success (Low) -->
    <div class="card text-center h-100">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Portfolio Urgency</h6>
        <span class="badge bg-{{ urgency_colour }} fs-4 mt-2">
          {{ portfolio_score.urgency_level }}
        </span>
        <div class="small text-muted mt-1">Score: {{ portfolio_score.score_raw|floatformat:1 }}</div>
      </div>
    </div>
  </div>
  <div class="col-md-2">
    <!-- Regime quadrant badge -->
    <div class="card text-center h-100">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Macro Regime</h6>
        <span class="badge bg-secondary fs-5 mt-2">{{ regime.quadrant }}</span>
        <div class="small text-muted mt-1">
          Confidence: {{ regime.confidence_score|floatformat:0 }}%
          {% if regime.transition_flag %}
            <span class="badge bg-warning text-dark ms-1">Transition</span>
          {% endif %}
        </div>
      </div>
    </div>
  </div>
  <div class="col-md-2">
    <!-- 1-day 95% VaR -->
    <div class="card text-center h-100">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">1-Day VaR 95%</h6>
        <div class="fs-5 fw-bold mt-2">
          ${{ portfolio_metrics.var_portfolio_95_usd|floatformat:0|intcomma }} USD
        </div>
        <div class="small text-muted">
          {{ portfolio_metrics.var_portfolio_95_pct|floatformat:2 }}% of portfolio
        </div>
      </div>
    </div>
  </div>
  <div class="col-md-2">
    <!-- Portfolio Beta -->
    <div class="card text-center h-100">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Beta vs SPY</h6>
        <div class="fs-5 fw-bold mt-2">{{ portfolio_metrics.portfolio_beta_spy|floatformat:2 }}</div>
      </div>
    </div>
  </div>
  <div class="col-md-2">
    <!-- Diversification ratio -->
    <div class="card text-center h-100">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Diversification</h6>
        <div class="fs-5 fw-bold mt-2">{{ portfolio_metrics.diversification_ratio|floatformat:2 }}x</div>
        <div class="small text-muted">ratio</div>
      </div>
    </div>
  </div>
  <div class="col-md-2">
    <!-- Market condition summary -->
    <div class="card text-center h-100">
      <div class="card-body">
        <h6 class="card-subtitle text-muted">Market Signals</h6>
        <div class="small mt-2">
          {% if death_cross_count > 0 %}
            <span class="badge bg-danger">{{ death_cross_count }} Death Cross</span>
          {% endif %}
          {% if oversold_count > 0 %}
            <span class="badge bg-warning text-dark">{{ oversold_count }} Oversold</span>
          {% endif %}
          {% if overbought_count > 0 %}
            <span class="badge bg-info">{{ overbought_count }} Overbought</span>
          {% endif %}
          {% if not death_cross_count and not oversold_count and not overbought_count %}
            <span class="text-muted">No extremes</span>
          {% endif %}
        </div>
      </div>
    </div>
  </div>
</div>
```

**Section 2: Effective Index Concentration (Chart.js horizontal bar)**
```html
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between align-items-center">
    <span>Effective Index Concentration</span>
    <small class="text-muted">Accounts for underlying index weights in ETFs</small>
  </div>
  <div class="card-body">
    <canvas id="indexConcentrationChart" height="100"></canvas>
  </div>
</div>

<script>
const ctx = document.getElementById('indexConcentrationChart').getContext('2d');
new Chart(ctx, {
  type: 'bar',
  data: {
    labels: ['S&P 500', 'Nasdaq 100', 'TSX', 'Semiconductor', 'Technology', 'Dividend'],
    datasets: [{
      label: '% Exposure',
      data: [
        {{ index_concentration.SP500|floatformat:1 }},
        {{ index_concentration.NASDAQ100|floatformat:1 }},
        {{ index_concentration.TSX|floatformat:1 }},
        {{ index_concentration.Semiconductor|floatformat:1 }},
        {{ index_concentration.Technology|floatformat:1 }},
        {{ index_concentration.Dividend|floatformat:1 }},
      ],
      backgroundColor: ['#0d6efd','#6f42c1','#dc3545','#fd7e14','#20c997','#ffc107'],
    }]
  },
  options: {
    indexAxis: 'y',
    plugins: { legend: { display: false } },
    scales: { x: { min: 0, max: 60, ticks: { callback: v => v + '%' } } }
  }
});
</script>
```

**Section 3: Top Unrealised Gains with Tax Flag**
```html
<div class="card mb-4">
  <div class="card-header">Largest Unrealised Gains</div>
  <div class="table-responsive">
    <table class="table table-sm table-hover mb-0">
      <thead class="table-light">
        <tr>
          <th>Symbol</th><th>Account</th><th>Owner</th>
          <th class="text-end">MV (USD)</th>
          <th class="text-end">UGL (USD)</th>
          <th class="text-end">UGL (CAD)</th>
          <th>Tax Sensitive</th>
        </tr>
      </thead>
      <tbody>
        {% for pos in top_ugl_positions %}
        <tr>
          <td><a href="{% url 'risk:security_detail' pos.symbol %}">{{ pos.symbol }}</a></td>
          <td><a href="{% url 'risk:account_summary' pos.account_id %}">{{ pos.account_id }}</a></td>
          <td>{{ pos.owner }}</td>
          <td class="text-end">${{ pos.market_value_usd|floatformat:0|intcomma }}</td>
          <td class="text-end fw-bold {% if pos.unrealised_gain_usd > 0 %}text-success{% else %}text-danger{% endif %}">
            ${{ pos.unrealised_gain_usd|floatformat:0|intcomma }}
          </td>
          <td class="text-end">${{ pos.unrealised_gain_cad|floatformat:0|intcomma }}</td>
          <td>
            {% if pos.is_tax_sensitive %}
              <span class="badge bg-danger">Taxable</span>
            {% else %}
              <span class="badge bg-success">Sheltered</span>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

**Section 4: Account Urgency Summary Strip**
```html
<div class="card mb-4">
  <div class="card-header d-flex justify-content-between">
    <span>Account Urgency Overview</span>
    <a href="?show_excluded={% if show_excluded %}false{% else %}true{% endif %}"
       class="btn btn-sm btn-outline-secondary">
      {% if show_excluded %}Hide TAXABLE_CAR{% else %}Show All Accounts{% endif %}
    </a>
  </div>
  <div class="table-responsive">
    <table class="table table-sm table-hover mb-0">
      <thead class="table-light">
        <tr>
          <th>Account</th><th>Owner</th><th>Type</th>
          <th>Urgency</th><th class="text-end">Score</th>
          <th class="text-end">VaR 95% (USD)</th>
          <th class="text-end">Total MV</th>
        </tr>
      </thead>
      <tbody>
        {% for score in account_scores %}
        {% with metrics=score.account_metrics %}
        <tr>
          <td><a href="{% url 'risk:account_summary' score.entity_id %}">{{ score.entity_id }}</a></td>
          <td>{{ score.account.owner }}</td>
          <td><span class="badge bg-secondary">{{ score.account.category }}</span></td>
          <td>
            <span class="badge urgency-{{ score.urgency_level|lower }}">
              {{ score.urgency_level }}
            </span>
          </td>
          <td class="text-end">{{ score.score_raw|floatformat:1 }}</td>
          <td class="text-end">${{ metrics.var_portfolio_95_usd|floatformat:0|intcomma }}</td>
          <td class="text-end">${{ metrics.total_market_value_usd|floatformat:0|intcomma }}</td>
        </tr>
        {% endwith %}
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

---

### 4. Account Risk Summary

**View:** `risk/views/account.py`
**Template:** `risk/templates/risk/account_summary.html`
**URL:** `risk/account/<account_id>/`

#### 4.1 View Logic

```python
def account_summary(request, account_id: str):
    """
    Per-account risk summary page.
    """
    calc_date = get_latest_calc_date()
    account = get_object_or_404(Account, account_id=account_id)

    account_score = get_object_or_404(
        UrgencyScore, entity_type='account', entity_id=account_id, calc_date=calc_date
    )
    account_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='account', entity_id=account_id, calc_date=calc_date
    ).first()

    # All security-level urgency scores for positions in this account
    security_scores = UrgencyScore.objects.filter(
        entity_type='security_in_account',
        account_id=account_id,
        calc_date=calc_date
    ).order_by('-score_raw')

    # Security risk metrics for each held symbol
    security_metrics = {
        m.symbol: m for m in SecurityRiskMetrics.objects.filter(
            symbol__in=security_scores.values_list('symbol', flat=True),
            calc_date=calc_date
        )
    }

    # Regime sensitivity for each symbol (from rm_regime_sensitivity)
    current_regime = RegimeState.objects.filter(calc_date__lte=calc_date).order_by('-calc_date').first()

    # Is this a taxable account?
    is_taxable = account.category in ('Investment', 'Investment Joint')
    is_lif_rrif = account.category in ('LIF', 'RRIF')

    context = {
        'calc_date':         calc_date,
        'account':           account,
        'account_score':     account_score,
        'account_metrics':   account_metrics,
        'security_scores':   security_scores,
        'security_metrics':  security_metrics,
        'current_regime':    current_regime,
        'is_taxable':        is_taxable,
        'is_lif_rrif':       is_lif_rrif,
    }
    return render(request, 'risk/account_summary.html', context)
```

#### 4.2 Template Sections — `risk/templates/risk/account_summary.html`

**Section 1: Account header with urgency badge and key metrics**
Same card-strip pattern as dashboard. Show: urgency badge, VaR 95%, HHI, total MV, total UGL.

**Section 2: Tax Sensitivity Panel (taxable accounts only)**
```html
{% if is_taxable %}
<div class="alert alert-warning d-flex align-items-center mb-4" role="alert">
  <i class="bi bi-exclamation-triangle-fill me-2"></i>
  <div>
    <strong>Taxable Account</strong> — Total unrealised gain:
    <strong>${{ account_metrics.taxable_ugl_usd|floatformat:0|intcomma }} USD</strong>.
    Any strategy that sells positions in this account triggers a capital gains tax event.
    {% if account_metrics.tax_risk_flag %}
      <span class="badge bg-danger ms-2">Tax Risk Flag Active</span>
    {% endif %}
  </div>
</div>
{% endif %}
```

**Section 3: Locked-In Panel (LIF/RRIF only)**
```html
{% if is_lif_rrif %}
<div class="alert alert-info mb-4" role="alert">
  <i class="bi bi-lock-fill me-2"></i>
  <strong>{{ account.category }} Account</strong> — Withdrawal restrictions apply.
  Partial liquidation strategies are not eligible. Stop-loss and inverse ETF hedges
  operate within the plan and do not trigger locked-in pension rules.
</div>
{% endif %}
```

**Section 4: Holdings risk table**
```html
<div class="card">
  <div class="card-header">Holdings — Risk Metrics</div>
  <div class="table-responsive">
    <table class="table table-sm table-hover mb-0">
      <thead class="table-light">
        <tr>
          <th>Symbol</th>
          <th>Urgency</th>
          <th class="text-end">MV (USD)</th>
          <th class="text-end">UGL (USD)</th>
          <th class="text-end">VaR 95%</th>
          <th class="text-end">Vol 21d</th>
          <th class="text-end">Beta SPY</th>
          <th>Regime Sensitivity</th>
          <th>Phase</th>
        </tr>
      </thead>
      <tbody>
        {% for score in security_scores %}
        {% with m=security_metrics|get_item:score.symbol %}
        <tr>
          <td><a href="{% url 'risk:security_detail' score.symbol %}">{{ score.symbol }}</a></td>
          <td>
            <span class="badge urgency-{{ score.urgency_level|lower }}">
              {{ score.urgency_level }}
            </span>
          </td>
          <td class="text-end">${{ m.position_mv_usd|floatformat:0|intcomma }}</td>
          <td class="text-end {% if m.ugl > 0 %}text-success{% else %}text-danger{% endif %}">
            ${{ m.ugl_usd|floatformat:0|intcomma }}
          </td>
          <td class="text-end">{{ m.var_parametric_95|floatformat:2|default:"—" }}%</td>
          <td class="text-end">{{ m.vol_21d|floatformat:1|default:"—" }}%</td>
          <td class="text-end">{{ m.beta_spy|floatformat:2|default:"—" }}</td>
          <td>
            <span class="badge bg-secondary">{{ score.dominant_component }}</span>
          </td>
          <td>
            {% with mc=market_conditions|get_item:score.symbol %}
            {{ mc.market_phase|default:"—" }}
            {% if mc.crossover_type == 'death' %}
              <span class="badge bg-danger ms-1">Death X</span>
            {% endif %}
            {% endwith %}
          </td>
        </tr>
        {% endwith %}
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
```

---

### 5. Owner Summary

**View:** `risk/views/owner.py`
**Template:** `risk/templates/risk/owner_summary.html`
**URL:** `risk/owner/<owner>/` (owner = 'TOM' or 'SUSAN')

#### 5.1 View Logic

```python
def owner_summary(request, owner: str):
    """
    Aggregated view for one owner across all their accounts.
    owner: 'TOM' or 'SUSAN' (case-sensitive, matches core_account.owner)
    """
    if owner not in ('TOM', 'SUSAN'):
        raise Http404("Owner must be TOM or SUSAN")

    calc_date = get_latest_calc_date()

    owner_score = UrgencyScore.objects.filter(
        entity_type='owner', entity_id=owner, calc_date=calc_date
    ).first()

    owner_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='owner', entity_id=owner, calc_date=calc_date
    ).first()

    # This owner's account scores, ordered by urgency
    account_scores = UrgencyScore.objects.filter(
        entity_type='account',
        calc_date=calc_date
    ).filter(
        models.Q(entity_id__in=Account.objects.filter(owner=owner).values('account_id')) |
        models.Q(entity_id__in=Account.objects.filter(owner='JOINT').values('account_id'))
        # Joint accounts are attributed 50/50; show them for both owners
    ).order_by('-score_raw')

    # Other owner for comparison panel
    other_owner = 'SUSAN' if owner == 'TOM' else 'TOM'
    other_owner_score = UrgencyScore.objects.filter(
        entity_type='owner', entity_id=other_owner, calc_date=calc_date
    ).first()
    other_owner_metrics = PortfolioRiskMetrics.objects.filter(
        entity_type='owner', entity_id=other_owner, calc_date=calc_date
    ).first()

    context = {
        'calc_date':           calc_date,
        'owner':               owner,
        'other_owner':         other_owner,
        'owner_score':         owner_score,
        'owner_metrics':       owner_metrics,
        'account_scores':      account_scores,
        'other_owner_score':   other_owner_score,
        'other_owner_metrics': other_owner_metrics,
    }
    return render(request, 'risk/owner_summary.html', context)
```

#### 5.2 Template — Side-by-Side Owner Comparison Panel

```html
<div class="card mb-4">
  <div class="card-header">Owner Comparison</div>
  <div class="row g-0">
    <!-- TOM column -->
    <div class="col-md-6 border-end">
      <div class="p-3">
        <h5>{{ owner }}</h5>
        <span class="badge urgency-{{ owner_score.urgency_level|lower }} fs-6">
          {{ owner_score.urgency_level }}
        </span>
        <dl class="row mt-3 small">
          <dt class="col-7">Portfolio VaR 95%</dt>
          <dd class="col-5 text-end">
            ${{ owner_metrics.var_portfolio_95_usd|floatformat:0|intcomma }}
          </dd>
          <dt class="col-7">Taxable UGL</dt>
          <dd class="col-5 text-end">
            ${{ owner_metrics.taxable_ugl_usd|floatformat:0|intcomma }}
          </dd>
          <dt class="col-7">Diversification</dt>
          <dd class="col-5 text-end">{{ owner_metrics.diversification_ratio|floatformat:2 }}x</dd>
          <dt class="col-7">Beta vs SPY</dt>
          <dd class="col-5 text-end">{{ owner_metrics.portfolio_beta_spy|floatformat:2 }}</dd>
          <dt class="col-7">Top Holding</dt>
          <dd class="col-5 text-end">
            {{ owner_metrics.top1_symbol }} ({{ owner_metrics.top1_weight|floatformat:1 }}%)
          </dd>
        </dl>
      </div>
    </div>
    <!-- Other owner column -->
    <div class="col-md-6">
      <div class="p-3">
        <h5>{{ other_owner }}</h5>
        <span class="badge urgency-{{ other_owner_score.urgency_level|lower }} fs-6">
          {{ other_owner_score.urgency_level }}
        </span>
        <dl class="row mt-3 small">
          <dt class="col-7">Portfolio VaR 95%</dt>
          <dd class="col-5 text-end">
            ${{ other_owner_metrics.var_portfolio_95_usd|floatformat:0|intcomma }}
          </dd>
          <dt class="col-7">Taxable UGL</dt>
          <dd class="col-5 text-end">
            ${{ other_owner_metrics.taxable_ugl_usd|floatformat:0|intcomma }}
          </dd>
          <dt class="col-7">Diversification</dt>
          <dd class="col-5 text-end">{{ other_owner_metrics.diversification_ratio|floatformat:2 }}x</dd>
          <dt class="col-7">Beta vs SPY</dt>
          <dd class="col-5 text-end">{{ other_owner_metrics.portfolio_beta_spy|floatformat:2 }}</dd>
          <dt class="col-7">Top Holding</dt>
          <dd class="col-5 text-end">
            {{ other_owner_metrics.top1_symbol }} ({{ other_owner_metrics.top1_weight|floatformat:1 }}%)
          </dd>
        </dl>
      </div>
    </div>
  </div>
</div>
```

---

### 6. Risk Heat Map

**View:** `risk/views/heatmap.py`
**Template:** `risk/templates/risk/heatmap.html`
**URL:** `risk/heatmap/`

#### 6.1 View Logic

```python
def risk_heatmap(request):
    """
    Heat map: rows = accounts, columns = symbols.
    Cell colour = urgency level. Cell opacity/size = position weight.
    """
    calc_date = get_latest_calc_date()
    show_excluded = get_show_excluded(request)

    # All security-in-account urgency scores for today
    scores = UrgencyScore.objects.filter(
        entity_type='security_in_account',
        calc_date=calc_date
    ).select_related()

    if not show_excluded:
        excluded_ids = list(
            Account.objects.filter(exclude_from_risk=True).values_list('account_id', flat=True)
        )
        scores = scores.exclude(account_id__in=excluded_ids)

    # Build grid: {account_id: {symbol: {urgency_level, score_raw, weight}}}
    # Weight = position_mv / total portfolio MV
    portfolio_mv = PortfolioRiskMetrics.objects.filter(
        entity_type='portfolio', entity_id='ALL', calc_date=calc_date
    ).values_list('total_market_value_usd', flat=True).first() or 1

    grid = {}
    all_symbols = set()
    for score in scores:
        if score.account_id not in grid:
            grid[score.account_id] = {}
        grid[score.account_id][score.symbol] = {
            'urgency_level': score.urgency_level,
            'score_raw': float(score.score_raw),
        }
        all_symbols.add(score.symbol)

    # Sort: accounts by total score descending, symbols by portfolio weight descending
    sorted_accounts = sorted(grid.keys(),
        key=lambda a: max(v['score_raw'] for v in grid[a].values()), reverse=True)
    sorted_symbols = sorted(all_symbols)

    context = {
        'calc_date':       calc_date,
        'show_excluded':   show_excluded,
        'grid':            grid,
        'sorted_accounts': sorted_accounts,
        'sorted_symbols':  sorted_symbols,
        'portfolio_mv':    portfolio_mv,
    }
    return render(request, 'risk/heatmap.html', context)
```

#### 6.2 Template — Heat Map Grid

```html
<div class="card">
  <div class="card-header d-flex justify-content-between">
    <span>Risk Heat Map — {{ calc_date }}</span>
    <a href="?show_excluded={% if show_excluded %}false{% else %}true{% endif %}"
       class="btn btn-sm btn-outline-secondary">
      {% if show_excluded %}Hide TAXABLE_CAR{% else %}Show All Accounts{% endif %}
    </a>
  </div>
  <div class="card-body p-2" style="overflow-x: auto;">
    <table class="table table-bordered table-sm heatmap-table mb-0"
           style="min-width: {{ sorted_symbols|length|multiply:70 }}px;">
      <thead class="table-dark">
        <tr>
          <th style="min-width:140px;">Account</th>
          {% for sym in sorted_symbols %}
            <th class="text-center" style="min-width:70px; font-size:0.75rem;">
              <a href="{% url 'risk:security_detail' sym %}" class="text-white">{{ sym }}</a>
            </th>
          {% endfor %}
        </tr>
      </thead>
      <tbody>
        {% for account_id in sorted_accounts %}
        <tr>
          <td class="fw-bold">
            <a href="{% url 'risk:account_summary' account_id %}">{{ account_id }}</a>
          </td>
          {% for sym in sorted_symbols %}
            {% with cell=grid|get_item:account_id|get_item:sym %}
            {% if cell %}
              <td class="text-center heatmap-cell urgency-bg-{{ cell.urgency_level|lower }}"
                  title="{{ sym }} in {{ account_id }}: {{ cell.urgency_level }} ({{ cell.score_raw|floatformat:1 }})">
                <span class="small fw-bold">{{ cell.urgency_level|slice:":1" }}</span>
              </td>
            {% else %}
              <td class="text-center bg-light"></td>
            {% endif %}
            {% endwith %}
          {% endfor %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="card-footer small text-muted">
    Legend:
    <span class="badge urgency-critical">C = Critical</span>
    <span class="badge urgency-high ms-1">H = High</span>
    <span class="badge urgency-medium ms-1">M = Medium</span>
    <span class="badge urgency-low ms-1">L = Low</span>
  </div>
</div>
```

---

### 7. CSS — Urgency Colour Classes

Add to `risk/static/risk/css/risk.css`:

```css
/* Urgency badge colours */
.urgency-critical, .badge.urgency-critical { background-color: #dc3545; color: #fff; }
.urgency-high,     .badge.urgency-high     { background-color: #fd7e14; color: #fff; }
.urgency-medium,   .badge.urgency-medium   { background-color: #ffc107; color: #000; }
.urgency-low,      .badge.urgency-low      { background-color: #198754; color: #fff; }

/* Heat map cell backgrounds */
.urgency-bg-critical { background-color: #f8d7da !important; }
.urgency-bg-high     { background-color: #ffe5d0 !important; }
.urgency-bg-medium   { background-color: #fff3cd !important; }
.urgency-bg-low      { background-color: #d1e7dd !important; }

/* Heat map cell sizing */
.heatmap-table td, .heatmap-table th { padding: 0.25rem 0.3rem; }
.heatmap-cell { cursor: pointer; transition: opacity 0.15s; }
.heatmap-cell:hover { opacity: 0.75; }
```

---

### 8. Template Tag — get_item

Create `risk/templatetags/risk_tags.py`:

```python
from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Allow dict lookups with variable keys in templates: {{ dict|get_item:key }}"""
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def multiply(value, arg):
    """Multiply filter for template width calculations."""
    try:
        return int(value) * int(arg)
    except (ValueError, TypeError):
        return 0

@register.simple_tag
def urgency_colour(level: str) -> str:
    """Return Bootstrap colour class for an urgency level."""
    return {
        'Critical': 'danger',
        'High': 'warning',
        'Medium': 'info',
        'Low': 'success',
    }.get(level, 'secondary')
```

---

### 9. Navigation

Add risk links to the Phase A base template nav. In `templates/base.html` (or equivalent), add:

```html
<li class="nav-item dropdown">
  <a class="nav-link dropdown-toggle" href="#" role="button" data-bs-toggle="dropdown">
    Risk
  </a>
  <ul class="dropdown-menu">
    <li><a class="dropdown-item" href="{% url 'risk:dashboard' %}">Portfolio Dashboard</a></li>
    <li><a class="dropdown-item" href="{% url 'risk:heatmap' %}">Risk Heat Map</a></li>
    <li><hr class="dropdown-divider"></li>
    <li><a class="dropdown-item" href="{% url 'risk:owner_summary' 'TOM' %}">TOM Summary</a></li>
    <li><a class="dropdown-item" href="{% url 'risk:owner_summary' 'SUSAN' %}">SUSAN Summary</a></li>
  </ul>
</li>
```

---

### 10. Tests

File: `risk/tests/test_dashboard_views.py`

```python
from django.test import TestCase, Client
from django.urls import reverse
from django.core.management import call_command
from risk.models import UrgencyScore, PortfolioRiskMetrics, RegimeState
from core.models import Account
import datetime


class DashboardViewTests(TestCase):
    """Test that all dashboard views return 200 with seeded risk data."""

    @classmethod
    def setUpTestData(cls):
        """Seed minimal risk data required by all dashboard views."""
        today = datetime.date(2026, 6, 2)

        # Seed portfolio urgency score
        UrgencyScore.objects.create(
            entity_type='portfolio', entity_id='ALL',
            calc_date=today, score_raw=42.5, urgency_level='Medium',
            component_scores_json='{}', dominant_component='m1_metrics'
        )

        # Seed account urgency scores for all 11 accounts
        for acc_id in ['RRSP_TOM', 'LIF_TOM', 'IB_TOM', 'TFSA_TOM',
                       'RRIF_SUSAN', 'LIF_SUSAN', 'IB_SUSAN', 'TFSA_SUSAN',
                       'IB_JOINT', 'TAXABLE_TOM_SUSAN', 'TAXABLE_CAR']:
            UrgencyScore.objects.create(
                entity_type='account', entity_id=acc_id,
                calc_date=today, score_raw=35.0, urgency_level='Medium',
                account_id=acc_id, component_scores_json='{}', dominant_component='regime'
            )

        # Seed owner urgency scores
        for owner in ('TOM', 'SUSAN'):
            UrgencyScore.objects.create(
                entity_type='owner', entity_id=owner,
                calc_date=today, score_raw=38.0, urgency_level='Medium',
                component_scores_json='{}', dominant_component='m1_metrics'
            )

        # Seed regime state
        RegimeState.objects.create(
            calc_date=today, quadrant='Q3', confidence_score=55.0,
            transition_flag=False,
            prob_q1=0.10, prob_q2=0.15, prob_q3=0.55, prob_q4=0.20,
            indicator_snapshot_json='{}'
        )

    def setUp(self):
        self.client = Client()

    def test_dashboard_returns_200(self):
        response = self.client.get(reverse('risk:dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_contains_urgency_level(self):
        response = self.client.get(reverse('risk:dashboard'))
        self.assertContains(response, 'Medium')

    def test_dashboard_contains_regime_quadrant(self):
        response = self.client.get(reverse('risk:dashboard'))
        self.assertContains(response, 'Q3')

    def test_dashboard_show_excluded_toggle(self):
        # Default: TAXABLE_CAR excluded from account strip
        response = self.client.get(reverse('risk:dashboard'))
        self.assertEqual(response.status_code, 200)

        # With show_excluded: TAXABLE_CAR should appear
        response = self.client.get(reverse('risk:dashboard') + '?show_excluded=true')
        self.assertEqual(response.status_code, 200)

    def test_account_summary_returns_200_for_all_accounts(self):
        for acc_id in ['RRSP_TOM', 'LIF_TOM', 'IB_TOM', 'TFSA_TOM',
                       'RRIF_SUSAN', 'LIF_SUSAN', 'IB_SUSAN', 'TFSA_SUSAN',
                       'IB_JOINT', 'TAXABLE_TOM_SUSAN']:
            with self.subTest(account=acc_id):
                response = self.client.get(
                    reverse('risk:account_summary', kwargs={'account_id': acc_id})
                )
                self.assertEqual(response.status_code, 200, f"Failed for {acc_id}")

    def test_taxable_account_shows_tax_panel(self):
        response = self.client.get(
            reverse('risk:account_summary', kwargs={'account_id': 'IB_SUSAN'})
        )
        self.assertContains(response, 'Taxable Account')

    def test_lif_account_shows_locked_in_panel(self):
        response = self.client.get(
            reverse('risk:account_summary', kwargs={'account_id': 'LIF_TOM'})
        )
        self.assertContains(response, 'LIF Account')

    def test_owner_summary_returns_200_for_tom(self):
        response = self.client.get(reverse('risk:owner_summary', kwargs={'owner': 'TOM'}))
        self.assertEqual(response.status_code, 200)

    def test_owner_summary_returns_200_for_susan(self):
        response = self.client.get(reverse('risk:owner_summary', kwargs={'owner': 'SUSAN'}))
        self.assertEqual(response.status_code, 200)

    def test_owner_summary_invalid_owner_returns_404(self):
        response = self.client.get(reverse('risk:owner_summary', kwargs={'owner': 'ALICE'}))
        self.assertEqual(response.status_code, 404)

    def test_owner_summary_contains_comparison_panel(self):
        response = self.client.get(reverse('risk:owner_summary', kwargs={'owner': 'TOM'}))
        self.assertContains(response, 'SUSAN')  # Comparison panel shows other owner

    def test_heatmap_returns_200(self):
        # Seed a security_in_account score to populate the grid
        UrgencyScore.objects.create(
            entity_type='security_in_account',
            entity_id='TFSA_TOM::QQQ',
            symbol='QQQ', account_id='TFSA_TOM',
            calc_date=datetime.date(2026, 6, 2),
            score_raw=55.0, urgency_level='High',
            component_scores_json='{}', dominant_component='market_condition'
        )
        response = self.client.get(reverse('risk:heatmap'))
        self.assertEqual(response.status_code, 200)

    def test_heatmap_contains_symbol(self):
        response = self.client.get(reverse('risk:heatmap'))
        self.assertContains(response, 'QQQ')

    def test_urgency_badge_css_class_rendered_correctly(self):
        response = self.client.get(reverse('risk:dashboard'))
        # Medium urgency: badge should use urgency-medium class
        self.assertContains(response, 'urgency-medium')
```

Run with: python manage.py test risk.tests.test_dashboard_views --settings=portfolio_manager.settings_test

---

## Constraints

- DO NOT implement security detail pages or strategy reports — that is M6-Spec-2
- DO NOT implement alternative portfolio UI — that is M6-Spec-2
- DO NOT implement export functionality — that is M6-Spec-2
- DO NOT push to git (manual step)
- Bootstrap 5 only — no additional CSS frameworks
- Chart.js loaded from CDN — no npm build step
- All views must respect the exclude_from_risk flag on core_account
- Template tag get_item is required for dict lookups — implement it cleanly
- PEP 8 throughout; views must not contain raw SQL (use ORM or DataSource)

---

## Manual Steps Required (in order)

- [ ] Verify gate: SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' AND entity_type='portfolio' — expect 1 row
- [ ] Verify gate: SELECT COUNT(*) FROM rm_portfolio_risk_metrics WHERE calc_date='2026-06-02' AND entity_type='portfolio' — expect 1 row
- [ ] Create risk/urls.py and wire into portfolio_manager/urls.py
- [ ] Create risk/templatetags/__init__.py and risk/templatetags/risk_tags.py
- [ ] Create risk/static/risk/css/risk.css
- [ ] python manage.py collectstatic --settings=portfolio_manager.settings_dev
- [ ] python manage.py test risk.tests.test_dashboard_views --settings=portfolio_manager.settings_test
- [ ] Open browser: http://saturn:8000/risk/dashboard/ — verify all panels render
- [ ] Open browser: http://saturn:8000/risk/account/IB_SUSAN/ — verify tax panel visible
- [ ] Open browser: http://saturn:8000/risk/account/LIF_TOM/ — verify locked-in panel visible
- [ ] Open browser: http://saturn:8000/risk/owner/TOM/ — verify comparison panel shows SUSAN
- [ ] Open browser: http://saturn:8000/risk/heatmap/ — verify grid renders for all symbols
- [ ] Test show_excluded toggle: verify TAXABLE_CAR appears/disappears correctly
- [ ] git add -A && git commit -m "feat: M6-Spec-1 portfolio dashboard + account/owner views + heat map"
- [ ] git push origin feature/risk-m6-dashboard

---

## Human Approval Required

Review the dashboard on saturn before deploying to mercury. Confirm the index concentration bar chart shows the hidden S&P 500 overlap (VFV + SPY + VOO combined) and that the heat map renders cleanly for all 11 accounts.

---

M6-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev
