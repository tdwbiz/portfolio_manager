# Feature Spec: M7-Spec-1 — Alerting & Continuous Monitoring

Project name: portfolio_manager
Customer: tdwdev
Feature branch: feature/risk-m7-alerting

---

**Architecture ref:** PM_PhaseB_Architecture_Final_v2.0.md
**Depends on:** M2-Spec-2 fully implemented and verified — `rm_urgency_score` populated for all entity types; M1-Spec-2 `rm_portfolio_risk_metrics` populated; M2-Spec-1 `rm_regime_state` and `rm_market_condition` populated

---

## Context

This spec closes the loop on the entire P1/P2 quantitative pipeline. Up to this point, the system computes risk every time a management command is run by hand. M7 makes that automatic and makes the *changes* visible — a portfolio dashboard that looks the same today as it did yesterday is not useful; an alert that says "AAPL just moved from Medium to High urgency" is.

Two things are delivered here:

1. **`evaluate_alerts`** — a management command that compares the current calculation date's outputs against the prior calculation date's outputs (and against configured thresholds) and writes `rm_alert_history` rows when a condition fires.
2. **`daily_risk_refresh`** — the single orchestration command that runs the entire M0→M2 pipeline plus alert evaluation, in the correct dependency order, suitable for Windows Task Scheduler on saturn or cron on mercury.

**Known immediate-fire conditions for this portfolio (June 2, 2026) — useful for verification:**

| Alert type | Expected on first run |
|---|---|
| `concentration_breach` | VDY at 20.4–20.7% of portfolio exceeds the default 20% threshold — **fires immediately** |
| `taxable_gain_bracket` | AAPL (IB_SUSAN, ~$200K UGL) and VFV (TAXABLE_TOM_SUSAN, ~$331K UGL) both already exceed the $100K and $250K brackets — **fires immediately on first run** (this is expected; document it as a "baseline" alert, not a new event) |
| `urgency_level_change` | No prior-day data exists on first run — command must handle this gracefully (no alert, log as baseline) |
| `regime_shift` | Depends on `rm_regime_state.transition_flag` from M2-Spec-1 — likely False on a stable day |
| `death_cross` | Depends on `rm_market_condition.crossover_type` for `calc_date` — may or may not fire |
| `earnings_proximity` | `rm_security_driver.earnings_proximity_flag` is currently False for all symbols (manually maintained) — will not fire until the developer sets a flag |
| `var_portfolio_breach` | Portfolio VaR ~1–2% of portfolio value typically; default threshold 5% — unlikely to fire on a normal day |

**Stack:**
- Python 3.12 / Django 5.1
- MSSQL 2025 (dev: PortfolioDEV)
- Project location: `C:\AI-DevTeam\projects\portfolio_manager`

---

## Requirements

### 1. New Database Tables

#### 1.1 `rm_alert_config`

Reference/configuration data — one row per monitored condition. Maintained via Django admin.

```python
class AlertConfig(models.Model):
    """
    Defines a single monitored alert condition.
    Scope filters (owner, account, symbol) are nullable — NULL means "applies to all".
    This is reference data, edited via Django admin, not written by application logic
    except for the seed command on first run.
    """
    alert_type = models.CharField(max_length=30)
    # Values: 'urgency_level_change', 'var_portfolio_breach', 'regime_shift',
    #         'death_cross', 'earnings_proximity', 'concentration_breach',
    #         'taxable_gain_bracket'

    # --- Scope filters (NULL = applies to all) ---
    owner = models.CharField(max_length=50, null=True, blank=True)
    # 'TOM' | 'SUSAN' | NULL (all owners)
    account_id = models.CharField(max_length=50, null=True, blank=True)
    # core_account.account_id string, or NULL (all accounts)
    symbol = models.CharField(max_length=20, null=True, blank=True)
    # Held symbol, or NULL (all symbols / not symbol-scoped)

    # --- Threshold ---
    threshold_value = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    # Meaning depends on alert_type:
    #   var_portfolio_breach:   fraction, e.g. 0.05 = 5% of portfolio value
    #   concentration_breach:   fraction, e.g. 0.20 = 20% of portfolio value
    #   regime_shift:           confidence_score threshold (0-100), e.g. 40
    #   taxable_gain_bracket:   USD bracket size, e.g. 100000 (brackets are multiples of this)
    #   earnings_proximity:     days, e.g. 14
    #   urgency_level_change:   unused (NULL) — any change fires
    #   death_cross:            unused (NULL) — any death cross on calc_date fires

    threshold_operator = models.CharField(max_length=5, default='>')
    # '>' | '>=' | '<' | '<=' | '==' | '!=' — comparison applied to the computed value
    # vs threshold_value. Most alert types use '>' or '>=' (breach above threshold).

    is_active = models.BooleanField(default=True)
    notify_email = models.EmailField(blank=True)
    # If set: send an email when this alert fires. Blank = in-app notification only.

    description = models.CharField(max_length=255, blank=True)
    # Human-readable note shown in admin, e.g. "VDY concentration limit"

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_alert_config'
        indexes = [
            models.Index(fields=['alert_type', 'is_active']),
            models.Index(fields=['owner', 'account_id', 'symbol']),
        ]
```

#### 1.2 `rm_alert_history`

Append-only log of fired alerts. Never updated except for `acknowledged_at` / `acknowledged_by`.

```python
class AlertHistory(models.Model):
    """
    Immutable record of a fired alert. Created by evaluate_alerts.
    The only mutable fields are acknowledged_at / acknowledged_by, set
    when a user dismisses the alert in the dashboard (M6).
    """
    alert_config = models.ForeignKey(
        AlertConfig, on_delete=models.PROTECT, related_name='history'
    )
    alert_type = models.CharField(max_length=30)
    # Denormalised copy of alert_config.alert_type — survives if config is later
    # retargeted, and makes querying history without a join simple.

    calc_date = models.DateField()
    triggered_at = models.DateTimeField(auto_now_add=True)

    # --- What fired and against what ---
    entity_description = models.CharField(max_length=255)
    # Human-readable description of what triggered.
    # E.g. "AAPL in IB_SUSAN: urgency Medium -> High"
    # E.g. "Portfolio: VaR 95% = 5.8% of MV (threshold 5.0%)"
    # E.g. "VDY: 20.4% of portfolio (threshold 20.0%)"
    # E.g. "AAPL in IB_SUSAN: unrealised gain crossed $250,000 USD bracket"

    entity_type = models.CharField(max_length=20, blank=True)
    # 'security_in_account' | 'account' | 'owner' | 'portfolio' | '' (not entity-scoped)
    entity_id = models.CharField(max_length=100, blank=True)
    symbol = models.CharField(max_length=20, blank=True)

    trigger_value = models.CharField(max_length=50)
    # The computed value that caused the trigger, as a string for display.
    # E.g. "High", "0.0580", "0.2040", "275000"
    previous_value = models.CharField(max_length=50, blank=True)
    # The prior value being compared against, where applicable.
    # E.g. "Medium", "0.0410", "" (blank if no prior comparison — e.g. concentration_breach)

    # --- Acknowledgement (mutable) ---
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = 'rm_alert_history'
        indexes = [
            models.Index(fields=['calc_date', 'alert_type']),
            models.Index(fields=['entity_type', 'entity_id', 'calc_date']),
            models.Index(fields=['acknowledged_at']),
            models.Index(fields=['symbol', 'calc_date']),
        ]
        ordering = ['-triggered_at']
```

**Deduplication rule (enforced in code, not as a unique constraint):** for a given `(alert_config_id, entity_type, entity_id, symbol, calc_date)` combination, `evaluate_alerts` must not insert more than one `rm_alert_history` row. Re-running `evaluate_alerts` for the same `calc_date` is idempotent — it must check for an existing row before inserting.

---

### 2. Seed Data — `rm_alert_config`

Create management command `risk/management/commands/seed_alert_configs.py`. Idempotent (MSSQL MERGE on a synthetic key of `alert_type` + scope fields, or check-then-insert).

```python
ALERT_CONFIG_SEED = [
    # --- Urgency level changes: portfolio, both owners, all accounts ---
    {
        'alert_type': 'urgency_level_change',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': None, 'threshold_operator': '!=',
        'description': 'Any urgency level change at any entity level',
    },

    # --- Portfolio VaR breach ---
    {
        'alert_type': 'var_portfolio_breach',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': Decimal('0.05'), 'threshold_operator': '>',
        'description': 'Portfolio 1-day VaR 95% exceeds 5% of portfolio value '
                       '(value sourced from rm_scoring_config.var_portfolio_alert_pct '
                       'if present, else this threshold)',
    },

    # --- Regime shift ---
    {
        'alert_type': 'regime_shift',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': Decimal('40'), 'threshold_operator': '>',
        'description': 'Regime quadrant change with confidence_score above 40',
    },

    # --- Death cross: all held symbols ---
    {
        'alert_type': 'death_cross',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': None, 'threshold_operator': '==',
        'description': 'Any held symbol records a Death Cross (50d crosses below 200d) '
                       'on calc_date',
    },

    # --- Earnings proximity: all held symbols ---
    {
        'alert_type': 'earnings_proximity',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': Decimal('14'), 'threshold_operator': '<=',
        'description': 'Earnings date within 14 days for a held symbol '
                       '(requires rm_security_driver.earnings_proximity_flag '
                       'or earnings date data — see note in §3.5)',
    },

    # --- Concentration breach: VDY specifically (will fire immediately) ---
    {
        'alert_type': 'concentration_breach',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': Decimal('0.20'), 'threshold_operator': '>',
        'description': 'Any single symbol exceeds 20% of total portfolio value '
                       '(VDY at ~20.4% will trigger this immediately on first run — '
                       'this is expected and documents the known concentration risk)',
    },

    # --- Taxable gain brackets: portfolio-wide, $100K increments ---
    {
        'alert_type': 'taxable_gain_bracket',
        'owner': None, 'account_id': None, 'symbol': None,
        'threshold_value': Decimal('100000'), 'threshold_operator': '>=',
        'description': 'Per-position unrealised gain in a taxable account crosses a '
                       '$100,000 USD bracket boundary (100K, 200K, 300K, ...). '
                       'AAPL (~$200K) and VFV (~$331K) will both already be above '
                       'one or more brackets on first run — documented as baseline.',
    },
]
```

Seed command algorithm:
```
For each entry in ALERT_CONFIG_SEED:
    Check for existing row matching (alert_type, owner, account_id, symbol)
    WHEN MATCHED: update threshold_value, threshold_operator, description
    WHEN NOT MATCHED: INSERT
Print: X alert configs seeded
```

---

### 3. Alert Detection Engine

Create `risk/services/alert_engine.py`. All detection functions are pure Python — they take plain dicts/lists (already loaded by the management command) and return a list of "candidate" alert dicts. The management command is responsible for deduplication and persistence.

A candidate alert dict has this shape:
```python
{
    'alert_type': str,
    'entity_type': str,        # '' if not entity-scoped
    'entity_id': str,           # '' if not entity-scoped
    'symbol': str,               # '' if not symbol-scoped
    'entity_description': str,
    'trigger_value': str,
    'previous_value': str,       # '' if not applicable
}
```

#### 3.1 Urgency Level Change

```python
def detect_urgency_level_changes(
    today_scores: list[dict],
    yesterday_scores_by_key: dict[tuple[str, str, str], dict],
) -> list[dict]:
    """
    Compare today's rm_urgency_score rows to yesterday's for the same
    (entity_type, entity_id, symbol) key.

    today_scores: list of dicts with keys entity_type, entity_id, symbol,
                   urgency_level, score_raw
    yesterday_scores_by_key: dict keyed by (entity_type, entity_id, symbol)
                             from the most recent prior calc_date with data

    For each row in today_scores:
        key = (entity_type, entity_id, symbol)
        If key not in yesterday_scores_by_key: skip (no baseline — first run
            or new entity; do not fire a "change" alert for a brand new entity)
        Else: if today.urgency_level != yesterday.urgency_level:
            emit a candidate alert.
            entity_description built as:
                security_in_account: "{symbol} in {entity_id}: urgency {prev} -> {curr}"
                account:             "{entity_id}: account urgency {prev} -> {curr}"
                owner:               "{entity_id}: owner urgency {prev} -> {curr}"
                portfolio:           "Portfolio: urgency {prev} -> {curr}"

    Returns list of candidate alert dicts (alert_type='urgency_level_change').
    """
```

**Finding "yesterday":** the management command resolves "yesterday" as the most recent `rm_urgency_score.calc_date` strictly less than the current `calc_date` — not necessarily the calendar day before (handles weekends/holidays where no calculation ran). If no prior `calc_date` exists at all, `yesterday_scores_by_key` is empty and no urgency_level_change alerts fire — this is the expected first-run behaviour.

#### 3.2 Portfolio VaR Breach

```python
def detect_var_portfolio_breach(
    portfolio_metrics: dict,
    threshold_pct: float,
) -> list[dict]:
    """
    portfolio_metrics: single row from rm_portfolio_risk_metrics for
                        entity_type='portfolio', entity_id='ALL', calc_date.

    If portfolio_metrics is None: return [] (M1-Spec-2 not yet run for this date —
        log a warning in the management command, do not raise).

    var_pct = portfolio_metrics['var_portfolio_95_pct']  (already a fraction, e.g. 0.0412)

    If var_pct > threshold_pct:
        emit candidate:
            alert_type='var_portfolio_breach'
            entity_type='portfolio', entity_id='ALL', symbol=''
            entity_description = f"Portfolio 1-day VaR 95% = {var_pct:.2%} "
                                  f"of portfolio value (threshold {threshold_pct:.2%})"
            trigger_value = f"{var_pct:.4f}"
            previous_value = ''

    Returns [] or [candidate].
    """
```

#### 3.3 Regime Shift

```python
def detect_regime_shift(
    today_regime: dict,
    yesterday_regime: dict | None,
    confidence_threshold: float,
) -> list[dict]:
    """
    today_regime: row from rm_regime_state for calc_date
                  (keys: quadrant, confidence_score, transition_flag)
    yesterday_regime: row from rm_regime_state for the prior calc_date, or None

    Fires when:
        today_regime['transition_flag'] is True
        AND today_regime['confidence_score'] > confidence_threshold
        AND (yesterday_regime is None OR
             yesterday_regime['quadrant'] != today_regime['quadrant'])

    The third condition prevents repeated daily alerts for a transition_flag
    that stays True across multiple days without an actual quadrant change —
    only the day the quadrant itself changes (or the first day transition_flag
    is observed with no prior data) fires.

    entity_description = (
        f"Regime shift detected: {prev_quadrant or 'unknown'} -> "
        f"{today_regime['quadrant']} (confidence {today_regime['confidence_score']:.0f})"
    )
    trigger_value = today_regime['quadrant']
    previous_value = yesterday_regime['quadrant'] if yesterday_regime else ''

    Returns [] or [candidate] (alert_type='regime_shift', entity_type='portfolio',
    entity_id='ALL', symbol='').
    """
```

#### 3.4 Death Cross

```python
def detect_death_crosses(
    market_conditions: list[dict],
    calc_date: date,
) -> list[dict]:
    """
    market_conditions: rows from rm_market_condition for calc_date,
                        keys: symbol, crossover_type, crossover_date

    For each row:
        if crossover_type == 'death' and crossover_date == calc_date:
            emit candidate:
                alert_type='death_cross'
                entity_type='', entity_id='', symbol=row['symbol']
                entity_description = f"{symbol}: Death Cross (50d SMA crossed below 200d SMA)"
                trigger_value = 'death'
                previous_value = ''

    Note: crossover_date == calc_date specifically (not just crossover_type=='death')
    ensures the alert fires only on the day the cross actually occurred, not every
    day afterward while the symbol remains below its 200d SMA.

    Returns list of candidates (zero or more).
    """
```

#### 3.5 Earnings Proximity

```python
def detect_earnings_proximity(
    security_drivers: list[dict],
) -> list[dict]:
    """
    security_drivers: rows from rm_security_driver,
                       keys: symbol, earnings_proximity_flag

    For each row where earnings_proximity_flag is True:
        emit candidate:
            alert_type='earnings_proximity'
            entity_type='', entity_id='', symbol=row['symbol']
            entity_description = f"{symbol}: earnings announcement within 14 days "
                                  f"(flag set manually in rm_security_driver)"
            trigger_value = 'true'
            previous_value = ''

    NOTE — known limitation (per M0-Spec-2 §5): earnings dates are not currently
    fetched from any data source. rm_security_driver.earnings_proximity_flag is a
    manually-maintained boolean (default False for all 28 symbols per M2-Spec-2 seed).
    Until an earnings-calendar data source is integrated (yFinance .calendar or a
    paid provider), this alert type only fires when the developer manually sets the
    flag in Django admin. This is documented behaviour, not a bug — the alert_type
    and table plumbing exist now so that no schema change is needed when a real
    earnings feed is added in a future spec.

    Returns list of candidates (zero or more — typically zero today).
    """
```

#### 3.6 Concentration Breach

```python
def detect_concentration_breach(
    symbol_weights: dict[str, float],
    threshold_pct: float,
) -> list[dict]:
    """
    symbol_weights: {symbol: weight_fraction} — portfolio-wide weight per symbol,
                     computed as sum across all non-excluded accounts of
                     (position_market_value_usd) / (total_portfolio_market_value_usd).
                     Sourced from vw_current_portfolio + rm_market_price_history +
                     rm_fx_rates (same pattern as M1-Spec-2 §3.1, but aggregated by
                     symbol across the whole portfolio rather than per-entity).

    For each (symbol, weight) where weight > threshold_pct:
        emit candidate:
            alert_type='concentration_breach'
            entity_type='', entity_id='', symbol=symbol
            entity_description = f"{symbol}: {weight:.1%} of portfolio "
                                  f"(threshold {threshold_pct:.0%})"
            trigger_value = f"{weight:.4f}"
            previous_value = ''

    Returns list of candidates. For this portfolio, expect at least one
    candidate for VDY (~20.4-20.7%).
    """
```

#### 3.7 Taxable Gain Bracket

```python
def detect_taxable_gain_brackets(
    taxable_positions: list[dict],
    bracket_size_usd: float,
    previous_brackets_by_key: dict[tuple[str, str], int],
) -> list[dict]:
    """
    taxable_positions: positions in accounts where account_category in
        ('Investment', 'Investment Joint'), with keys:
        symbol, account_id, owner, unrealised_gain_usd

    bracket_size_usd: e.g. 100000 — brackets are floor(gain / size) * size,
        i.e. 0-99999 -> bracket 0, 100000-199999 -> bracket 1, etc.
        Negative or zero gains -> bracket 0 (never fires).

    previous_brackets_by_key: {(symbol, account_id): previous_bracket_index}
        from the most recent prior calc_date's rm_alert_history entries for
        alert_type='taxable_gain_bracket' (or empty dict on first run).

    For each position:
        current_bracket = max(0, floor(unrealised_gain_usd / bracket_size_usd))
        previous_bracket = previous_brackets_by_key.get((symbol, account_id), 0)
        if current_bracket > previous_bracket:
            emit candidate:
                alert_type='taxable_gain_bracket'
                entity_type='security_in_account'
                entity_id=f"{account_id}::{symbol}"
                symbol=symbol
                entity_description = (
                    f"{symbol} in {account_id}: unrealised gain "
                    f"${unrealised_gain_usd:,.0f} USD crossed the "
                    f"${current_bracket * bracket_size_usd:,.0f} USD bracket"
                )
                trigger_value = f"{unrealised_gain_usd:.2f}"
                previous_value = f"{previous_bracket * bracket_size_usd:.2f}"

    On FIRST RUN (previous_brackets_by_key is empty for all keys), every
    taxable position with gain >= bracket_size_usd will fire once — for AAPL
    (~$200K, bracket 2) and VFV (~$331K, bracket 3) and VDY (~$195K, bracket 1)
    this is EXPECTED. The management command should print a clear note that
    first-run taxable_gain_bracket alerts represent the current state, not new
    events, but they are still recorded in rm_alert_history (the audit trail
    should reflect the true historical state).

    Returns list of candidates (zero or more).
    """
```

**Resolving `previous_brackets_by_key`:** the management command queries the most recent `rm_alert_history` row per `(symbol, account_id)` for `alert_type='taxable_gain_bracket'` with `calc_date < current_calc_date`, parses `trigger_value` back into a bracket index via `floor(trigger_value / bracket_size_usd)`, and builds the lookup dict. If no prior row exists for a key, its previous bracket is `0`.

---

### 4. Management Command — `evaluate_alerts`

Create `risk/management/commands/evaluate_alerts.py`.

```
python manage.py evaluate_alerts [--date YYYY-MM-DD] [--dry-run]
```

- `--date`: evaluation date (default: today). Must match an existing `rm_urgency_score.calc_date` — if not, abort with a clear error message naming the missing date.
- `--dry-run`: run all detection functions and print what *would* fire, but do not write to `rm_alert_history` or send emails.

**Execution order:**
```
1. Resolve calc_date (default today). Verify rm_urgency_score has rows for calc_date —
   if not, print error and exit non-zero.
2. Resolve "yesterday" = max(calc_date) from rm_urgency_score where calc_date < current
   (None if no prior data).
3. Load active rm_alert_config rows, grouped by alert_type.
4. For each alert_type with at least one active config:
   a. Load the data needed for that detection function (see §3 for sources)
   b. Call the detection function -> list of candidates
5. For each candidate:
   a. Find the matching active rm_alert_config row (by alert_type + scope filters:
      a config with owner/account_id/symbol all NULL matches everything; a config
      with a specific symbol only matches candidates for that symbol; etc.
      If multiple configs match, use all of them — a candidate can trigger
      multiple notifications if multiple configs are scoped to it.)
   b. For each matching config:
      - Check threshold_operator/threshold_value where applicable (some detection
        functions already apply the threshold internally — see note below;
        for those, this step is a pass-through)
      - Check for an existing rm_alert_history row with the same
        (alert_config_id, entity_type, entity_id, symbol, calc_date) —
        skip if found (deduplication)
      - If --dry-run: print the candidate and matching config; do not write
      - Else: INSERT rm_alert_history row
      - If config.notify_email is set and not --dry-run: send email (see §5)
6. Print summary:
   - alerts evaluated per type
   - alerts fired (new) per type
   - alerts skipped as duplicates
   - any alert types skipped due to missing source data (e.g. M2-Spec-1 not run)
```

**Note on threshold application:** for `var_portfolio_breach`, `regime_shift`, `concentration_breach`, and `earnings_proximity`, the detection functions in §3 take a threshold parameter directly — the management command passes `config.threshold_value` (falling back to the seeded default if a matching config has `threshold_value=NULL`, except for `urgency_level_change` and `death_cross` where threshold is not applicable). For `urgency_level_change`, `death_cross`, and `taxable_gain_bracket`, any detected change/cross/bracket-crossing is a candidate regardless of `threshold_value` — the config row exists primarily for scoping (owner/account/symbol filters) and email routing.

**Config-to-candidate scope matching:**
```python
def config_matches_candidate(config: AlertConfig, candidate: dict) -> bool:
    """
    A config matches a candidate if, for each of owner/account_id/symbol:
    the config's value is NULL, OR equals the candidate's corresponding value.

    candidate may not have an 'owner' field directly (most detection functions
    don't resolve owner) — for entity_type='security_in_account', resolve owner
    via core_account.owner for entity_id's account; for entity_type='account',
    via core_account.owner for entity_id; for entity_type='owner', entity_id IS
    the owner. For entity_type='portfolio' or non-entity-scoped alerts (death_cross,
    earnings_proximity, concentration_breach, var_portfolio_breach, regime_shift),
    owner-scoped configs only match if owner is NULL on the config (these alert
    types are not owner-attributable in a meaningful way without double-firing
    for joint accounts) — document this limitation in code comments.
    """
```

---

### 5. Email Notifications

Create `risk/services/alert_email.py`.

```python
def send_alert_email(alert_history: AlertHistory, config: AlertConfig) -> bool:
    """
    Send an email notification for a fired alert using Django's email backend.

    Uses settings.EMAIL_BACKEND — for dev, configure
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
    in settings_dev.py so emails print to stdout rather than requiring an SMTP
    server. Production (mercury) settings configure a real backend separately
    (out of scope for this spec — document as a manual step).

    Subject: f"[Portfolio Risk Alert] {alert_history.get_alert_type_display()}"
    Body: rendered from a per-alert-type template in
          risk/templates/risk/email/{alert_type}.txt

    Returns True if sent successfully, False on any exception (logged as a
    warning — email failure must never abort evaluate_alerts).
    """
```

**Email templates** — one plain-text template per alert type in `risk/templates/risk/email/`:

- `urgency_level_change.txt`
- `var_portfolio_breach.txt`
- `regime_shift.txt`
- `death_cross.txt`
- `earnings_proximity.txt`
- `concentration_breach.txt`
- `taxable_gain_bracket.txt`

Each template receives context `{'alert': alert_history, 'config': config}` and is a short plain-text message, e.g.:

```
Subject: [Portfolio Risk Alert] Concentration Breach

{{ alert.entity_description }}

Calculation date: {{ alert.calc_date }}
Triggered at: {{ alert.triggered_at }}

This is an automated message from the Portfolio Manager Risk Module.
View details: http://mercury:5000/risk/heatmap/
```

Add `EMAIL_BACKEND` setting to `settings_dev.py`:
```python
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'portfolio-risk@localhost'
```

---

### 6. In-App Notification Badge (Dashboard Hook)

This spec does not build new dashboard UI (M6-Spec-1 is already complete), but it adds the data hook M6 needs for an "unacknowledged alerts" badge.

Create a small helper in `risk/services/alerts.py`:

```python
def get_unacknowledged_alert_count() -> int:
    """
    Return the count of rm_alert_history rows where acknowledged_at IS NULL.
    Used by the dashboard header badge (M6-Spec-1 base template).
    """

def acknowledge_alert(alert_id: int, acknowledged_by: str) -> None:
    """
    Set acknowledged_at = now() and acknowledged_by on the given AlertHistory row.
    Idempotent — if already acknowledged, do nothing.
    """
```

A context processor `risk/context_processors.py` exposes `unacknowledged_alert_count` to all templates:

```python
def alert_badge(request):
    from risk.services.alerts import get_unacknowledged_alert_count
    return {'unacknowledged_alert_count': get_unacknowledged_alert_count()}
```

Register in `settings_dev.py` `TEMPLATES['OPTIONS']['context_processors']`. Add a small badge snippet to the existing base template nav (the same nav block M6-Spec-1 added the Risk dropdown to):

```html
{% if unacknowledged_alert_count > 0 %}
  <span class="badge bg-danger ms-1">{{ unacknowledged_alert_count }}</span>
{% endif %}
```

This spec does **not** build a dedicated alert list/acknowledge view — that is left to M6 follow-up or M8 if not already covered. The badge and the two service functions above are the complete UI hook for this spec.

---

### 7. Master Pipeline Command — `daily_risk_refresh`

Create `risk/management/commands/daily_risk_refresh.py`.

```
python manage.py daily_risk_refresh [--date YYYY-MM-DD] [--dry-run] [--skip-data-refresh]
```

- `--date`: calculation date for all M1/M2/alert steps (default: today)
- `--dry-run`: pass through to `evaluate_alerts --dry-run`; other commands run normally
  (their outputs are needed for evaluate_alerts to have something to evaluate even
  in dry-run mode)
- `--skip-data-refresh`: skip step 1 (`refresh_market_data`) — useful when market
  data was already refreshed earlier in the day and only recalculation is needed

**Execution order** (each step is a `call_command()` invocation; if any step raises,
log the error, print a summary of what completed, and exit non-zero — do not
silently continue to dependent steps):

```
1. refresh_market_data            (M0-Spec-2)        — skipped if --skip-data-refresh
2. calc_security_metrics --date   (M1-Spec-1)
3. calc_portfolio_metrics --date  (M1-Spec-2)
4. calc_regime --date             (M2-Spec-1)
5. calc_market_conditions --date  (M2-Spec-1)
6. calc_urgency --date            (M2-Spec-2)
7. evaluate_alerts --date          (M7-Spec-1, this spec)
8. Print final summary:
   - duration of each step
   - total duration
   - alert counts from step 7
   - any step that failed (and which downstream steps were skipped as a result)
```

Per the architecture's non-functional requirement (§3, item 9), the full sequence
should complete in well under 5 minutes for this portfolio's size (~30 securities).
Print total duration prominently so this can be monitored over time.

**Scheduling** (document only — no code change to scheduler config in this spec):
- saturn (Windows Task Scheduler): `python manage.py daily_risk_refresh`, daily after
  US market close (consistent with `refresh_market_data`'s existing 6:00 PM ET trigger
  from M0-Spec-2 — `daily_risk_refresh` supersedes running `refresh_market_data` as a
  separate scheduled task; update the Task Scheduler entry to call
  `daily_risk_refresh` instead, or add `--skip-data-refresh` to a *second* scheduled
  task if the team prefers to keep them separate).
- mercury (cron, when Phase B is deployed to production): same command, full path to
  `python` and working directory per the existing `refresh_market_data` cron pattern
  in M0-Spec-2 §9.

---

### 8. Admin Registration

```python
@admin.register(AlertConfig)
class AlertConfigAdmin(admin.ModelAdmin):
    list_display = [
        'alert_type', 'owner', 'account_id', 'symbol',
        'threshold_value', 'threshold_operator',
        'is_active', 'notify_email', 'description'
    ]
    list_filter = ['alert_type', 'is_active', 'owner']
    search_fields = ['symbol', 'account_id', 'description']
    ordering = ['alert_type', 'owner', 'account_id', 'symbol']
    # Fully editable — this is the user's control panel for alert behaviour


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    list_display = [
        'calc_date', 'alert_type', 'entity_description',
        'trigger_value', 'previous_value',
        'triggered_at', 'acknowledged_at', 'acknowledged_by'
    ]
    list_filter = ['alert_type', 'calc_date', ('acknowledged_at', admin.EmptyFieldListFilter)]
    search_fields = ['entity_description', 'symbol', 'entity_id']
    ordering = ['-triggered_at']
    readonly_fields = [
        'alert_config', 'alert_type', 'calc_date', 'triggered_at',
        'entity_description', 'entity_type', 'entity_id', 'symbol',
        'trigger_value', 'previous_value'
    ]
    # acknowledged_at / acknowledged_by are editable (admin can manually acknowledge)

    actions = ['mark_acknowledged']

    def mark_acknowledged(self, request, queryset):
        from django.utils import timezone
        updated = queryset.filter(acknowledged_at__isnull=True).update(
            acknowledged_at=timezone.now(),
            acknowledged_by=request.user.username,
        )
        self.message_user(request, f"{updated} alert(s) acknowledged.")
    mark_acknowledged.short_description = "Mark selected alerts as acknowledged"
```

---

### 9. Tests

File: `risk/tests/test_alerting.py`

```python
import json
from datetime import date, timedelta
from decimal import Decimal
from django.test import TestCase
from django.core import mail
from django.core.management import call_command
from risk.models import AlertConfig, AlertHistory, UrgencyScore, RegimeState, MarketCondition
from risk.services.alert_engine import (
    detect_urgency_level_changes, detect_var_portfolio_breach,
    detect_regime_shift, detect_death_crosses,
    detect_earnings_proximity, detect_concentration_breach,
    detect_taxable_gain_brackets,
)


class UrgencyChangeDetectionTests(TestCase):

    def test_level_change_detected(self):
        today = [{'entity_type': 'security_in_account', 'entity_id': 'IB_SUSAN::AAPL',
                  'symbol': 'AAPL', 'urgency_level': 'High', 'score_raw': 62.0}]
        yesterday = {('security_in_account', 'IB_SUSAN::AAPL', 'AAPL'):
                      {'urgency_level': 'Medium', 'score_raw': 48.0}}
        candidates = detect_urgency_level_changes(today, yesterday)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['alert_type'], 'urgency_level_change')
        self.assertIn('Medium', candidates[0]['previous_value'])
        self.assertIn('High', candidates[0]['trigger_value'])

    def test_no_change_no_alert(self):
        today = [{'entity_type': 'account', 'entity_id': 'TFSA_TOM',
                  'symbol': '', 'urgency_level': 'Medium', 'score_raw': 40.0}]
        yesterday = {('account', 'TFSA_TOM', ''):
                      {'urgency_level': 'Medium', 'score_raw': 39.0}}
        candidates = detect_urgency_level_changes(today, yesterday)
        self.assertEqual(candidates, [])

    def test_no_baseline_no_alert(self):
        # Symbol present today but not in yesterday's data — first run / new entity
        today = [{'entity_type': 'security_in_account', 'entity_id': 'TFSA_TOM::DRAM',
                  'symbol': 'DRAM', 'urgency_level': 'Medium', 'score_raw': 45.0}]
        candidates = detect_urgency_level_changes(today, {})
        self.assertEqual(candidates, [])


class VarBreachTests(TestCase):

    def test_breach_above_threshold(self):
        metrics = {'var_portfolio_95_pct': Decimal('0.0612')}
        candidates = detect_var_portfolio_breach(metrics, threshold_pct=0.05)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['alert_type'], 'var_portfolio_breach')

    def test_no_breach_below_threshold(self):
        metrics = {'var_portfolio_95_pct': Decimal('0.018')}
        candidates = detect_var_portfolio_breach(metrics, threshold_pct=0.05)
        self.assertEqual(candidates, [])

    def test_none_metrics_returns_empty(self):
        candidates = detect_var_portfolio_breach(None, threshold_pct=0.05)
        self.assertEqual(candidates, [])


class RegimeShiftTests(TestCase):

    def test_shift_with_quadrant_change_and_high_confidence(self):
        today = {'quadrant': 'Q3', 'confidence_score': Decimal('55'), 'transition_flag': True}
        yesterday = {'quadrant': 'Q2', 'confidence_score': Decimal('48'), 'transition_flag': False}
        candidates = detect_regime_shift(today, yesterday, confidence_threshold=40)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['trigger_value'], 'Q3')
        self.assertEqual(candidates[0]['previous_value'], 'Q2')

    def test_no_alert_when_confidence_too_low(self):
        today = {'quadrant': 'Q3', 'confidence_score': Decimal('30'), 'transition_flag': True}
        yesterday = {'quadrant': 'Q2', 'confidence_score': Decimal('48'), 'transition_flag': False}
        candidates = detect_regime_shift(today, yesterday, confidence_threshold=40)
        self.assertEqual(candidates, [])

    def test_no_alert_when_quadrant_unchanged(self):
        today = {'quadrant': 'Q3', 'confidence_score': Decimal('55'), 'transition_flag': True}
        yesterday = {'quadrant': 'Q3', 'confidence_score': Decimal('50'), 'transition_flag': True}
        candidates = detect_regime_shift(today, yesterday, confidence_threshold=40)
        self.assertEqual(candidates, [])

    def test_no_alert_when_transition_flag_false(self):
        today = {'quadrant': 'Q3', 'confidence_score': Decimal('80'), 'transition_flag': False}
        candidates = detect_regime_shift(today, None, confidence_threshold=40)
        self.assertEqual(candidates, [])


class DeathCrossTests(TestCase):

    def test_death_cross_on_calc_date_fires(self):
        calc_date = date(2026, 6, 2)
        conditions = [
            {'symbol': 'QQQ', 'crossover_type': 'death', 'crossover_date': calc_date},
            {'symbol': 'VDY', 'crossover_type': 'none', 'crossover_date': None},
        ]
        candidates = detect_death_crosses(conditions, calc_date)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['symbol'], 'QQQ')

    def test_death_cross_on_prior_date_does_not_refire(self):
        calc_date = date(2026, 6, 2)
        conditions = [
            {'symbol': 'QQQ', 'crossover_type': 'death',
             'crossover_date': date(2026, 5, 20)},
        ]
        candidates = detect_death_crosses(conditions, calc_date)
        self.assertEqual(candidates, [])


class EarningsProximityTests(TestCase):

    def test_flag_set_fires(self):
        drivers = [
            {'symbol': 'AAPL', 'earnings_proximity_flag': True},
            {'symbol': 'VDY', 'earnings_proximity_flag': False},
        ]
        candidates = detect_earnings_proximity(drivers)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['symbol'], 'AAPL')

    def test_no_flags_set_returns_empty(self):
        drivers = [{'symbol': 'AAPL', 'earnings_proximity_flag': False}]
        candidates = detect_earnings_proximity(drivers)
        self.assertEqual(candidates, [])


class ConcentrationBreachTests(TestCase):

    def test_vdy_breaches_default_threshold(self):
        weights = {'VDY': 0.207, 'VFV': 0.135, 'QQQ': 0.116}
        candidates = detect_concentration_breach(weights, threshold_pct=0.20)
        symbols = [c['symbol'] for c in candidates]
        self.assertIn('VDY', symbols)
        self.assertNotIn('VFV', symbols)

    def test_no_breach_when_diversified(self):
        weights = {'A': 0.10, 'B': 0.10, 'C': 0.10}
        candidates = detect_concentration_breach(weights, threshold_pct=0.20)
        self.assertEqual(candidates, [])


class TaxableGainBracketTests(TestCase):

    def test_first_run_fires_for_existing_large_gains(self):
        positions = [
            {'symbol': 'AAPL', 'account_id': 'IB_SUSAN', 'owner': 'SUSAN',
             'unrealised_gain_usd': 200000.0},
            {'symbol': 'VFV', 'account_id': 'TAXABLE_TOM_SUSAN', 'owner': 'JOINT',
             'unrealised_gain_usd': 331000.0},
            {'symbol': 'IBIT', 'account_id': 'TFSA_TOM', 'owner': 'TOM',
             'unrealised_gain_usd': -5000.0},
        ]
        candidates = detect_taxable_gain_brackets(positions, bracket_size_usd=100000, previous_brackets_by_key={})
        symbols = [c['symbol'] for c in candidates]
        self.assertIn('AAPL', symbols)
        self.assertIn('VFV', symbols)
        self.assertNotIn('IBIT', symbols)

    def test_no_refire_when_bracket_unchanged(self):
        positions = [
            {'symbol': 'AAPL', 'account_id': 'IB_SUSAN', 'owner': 'SUSAN',
             'unrealised_gain_usd': 205000.0},  # still bracket 2
        ]
        prev = {('AAPL', 'IB_SUSAN'): 2}
        candidates = detect_taxable_gain_brackets(positions, bracket_size_usd=100000, previous_brackets_by_key=prev)
        self.assertEqual(candidates, [])

    def test_refire_when_new_bracket_crossed(self):
        positions = [
            {'symbol': 'AAPL', 'account_id': 'IB_SUSAN', 'owner': 'SUSAN',
             'unrealised_gain_usd': 305000.0},  # now bracket 3
        ]
        prev = {('AAPL', 'IB_SUSAN'): 2}
        candidates = detect_taxable_gain_brackets(positions, bracket_size_usd=100000, previous_brackets_by_key=prev)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['previous_value'], '200000.00')


class SeedConfigTests(TestCase):

    def test_seed_creates_seven_alert_types(self):
        call_command('seed_alert_configs', verbosity=0)
        types = set(AlertConfig.objects.values_list('alert_type', flat=True))
        expected = {
            'urgency_level_change', 'var_portfolio_breach', 'regime_shift',
            'death_cross', 'earnings_proximity', 'concentration_breach',
            'taxable_gain_bracket',
        }
        self.assertEqual(types, expected)

    def test_seed_idempotent(self):
        call_command('seed_alert_configs', verbosity=0)
        call_command('seed_alert_configs', verbosity=0)
        self.assertEqual(AlertConfig.objects.count(), 7)


class IntegrationTests(TestCase):
    """End-to-end: seed configs + minimal M1/M2 data, run evaluate_alerts."""

    def test_no_duplicate_alerts_on_rerun(self):
        """
        Seed enough rm_urgency_score / rm_portfolio_risk_metrics / rm_regime_state /
        rm_market_condition data for calc_date=2026-06-02 such that at least the
        concentration_breach alert fires (VDY weight seeded above 20%).
        Run evaluate_alerts --date 2026-06-02 twice.
        Assert rm_alert_history row count is identical after the second run
        (no duplicates inserted).
        """
        pass  # Implement with fixtures

    def test_first_run_produces_baseline_taxable_gain_alerts(self):
        """
        Seed taxable positions with large gains, no prior rm_alert_history.
        Run evaluate_alerts.
        Assert rm_alert_history has taxable_gain_bracket rows for AAPL and VFV.
        """
        pass

    def test_dry_run_writes_nothing(self):
        """
        Run evaluate_alerts --dry-run with data that would trigger
        concentration_breach.
        Assert AlertHistory.objects.count() == 0 after the run.
        """
        pass

    def test_email_sent_when_notify_email_configured(self):
        """
        Configure an AlertConfig with notify_email set, scoped to match a
        candidate that will fire (e.g. concentration_breach for VDY).
        Run evaluate_alerts.
        Assert len(mail.outbox) == 1 (using Django's test email backend,
        which is locmem regardless of settings_dev console backend).
        """
        pass


class DailyRefreshTests(TestCase):

    def test_daily_risk_refresh_runs_without_error(self):
        """
        With minimal seed data sufficient for each pipeline step to run without
        raising (even if it processes zero symbols), run:
            call_command('daily_risk_refresh', '--skip-data-refresh',
                          '--date', '2026-06-02')
        Assert the command completes (return code / no exception) and prints
        a summary containing 'Total duration'.
        """
        pass

    def test_failed_step_aborts_downstream_steps(self):
        """
        Simulate a failure in calc_security_metrics (e.g. by pointing at a
        date with no price data and forcing an exception via mocking).
        Assert daily_risk_refresh exits non-zero and does not call
        evaluate_alerts.
        """
        pass
```

Run with: `python manage.py test risk.tests.test_alerting --settings=portfolio_manager.settings_test`

---

## Constraints

- **DO NOT** implement any new dashboard pages or alert list/detail UI views beyond the badge context processor and the two service helper functions in §6 — that is M6 follow-up scope if needed
- **DO NOT** implement an earnings-calendar data fetcher — `earnings_proximity` relies on the existing manually-maintained `rm_security_driver.earnings_proximity_flag` (document the limitation as specified in §3.5)
- **DO NOT** push to git (manual step)
- `evaluate_alerts` must be idempotent — re-running for the same `calc_date` must never create duplicate `rm_alert_history` rows
- `rm_alert_history` is append-only except for `acknowledged_at` / `acknowledged_by`
- Email failures must never abort `evaluate_alerts` or `daily_risk_refresh` — log and continue
- `daily_risk_refresh` must abort downstream steps if an upstream step fails, and report this clearly
- All thresholds configurable via `rm_alert_config` (or `rm_scoring_config` where already established, e.g. `concentration_alert_pct`, `var_portfolio_alert_pct`, `taxable_ugl_alert_usd`) — never hard-coded in the detection functions themselves
- MSSQL-compatible SQL only — no PostgreSQL constructs
- PEP 8 throughout; full type hints and docstrings on all public functions

---

## Manual Steps Required (in order)

- [ ] Verify gate: `SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02'` — expect rows for all entity types (M2-Spec-2 complete)
- [ ] `python manage.py makemigrations risk` (for AlertConfig, AlertHistory)
- [ ] `python manage.py migrate --settings=portfolio_manager.settings_dev`
- [ ] Add `EMAIL_BACKEND` and `DEFAULT_FROM_EMAIL` to `settings_dev.py`
- [ ] Add `risk.context_processors.alert_badge` to `TEMPLATES['OPTIONS']['context_processors']`
- [ ] Add the unacknowledged-alert badge snippet to the base template nav
- [ ] `python manage.py seed_alert_configs --settings=portfolio_manager.settings_dev`
- [ ] Verify in SSMS: `SELECT alert_type, owner, account_id, symbol, threshold_value, threshold_operator FROM rm_alert_config ORDER BY alert_type`
- [ ] `python manage.py evaluate_alerts --date 2026-06-02 --dry-run --settings=portfolio_manager.settings_dev`
- [ ] Review dry-run output — confirm `concentration_breach` (VDY) and `taxable_gain_bracket` (AAPL, VFV, likely VDY) appear as expected baseline alerts
- [ ] `python manage.py evaluate_alerts --date 2026-06-02 --settings=portfolio_manager.settings_dev`
- [ ] Verify in SSMS: `SELECT calc_date, alert_type, entity_description, trigger_value, previous_value FROM rm_alert_history ORDER BY triggered_at DESC`
- [ ] Re-run `evaluate_alerts --date 2026-06-02` a second time — verify row count in `rm_alert_history` is unchanged (no duplicates)
- [ ] `python manage.py daily_risk_refresh --skip-data-refresh --date 2026-06-02 --settings=portfolio_manager.settings_dev`
- [ ] Review the printed step-by-step duration summary — confirm total duration is reasonable (well under 5 minutes)
- [ ] `python manage.py test risk.tests.test_alerting --settings=portfolio_manager.settings_test`
- [ ] Update saturn Windows Task Scheduler: replace (or supplement) the existing `refresh_market_data` daily task with `daily_risk_refresh`
- [ ] `git add -A && git commit -m "feat: M7-Spec-1 alerting + daily risk refresh pipeline"`
- [ ] `git push origin feature/risk-m7-alerting`

---

## Human Approval Required

This is the gate spec confirming the P1/P2 pipeline is fully automatable end-to-end. Before marking complete:

1. Confirm at least 3 alert types fired correctly on the first `evaluate_alerts` run (per the architecture's gate condition) — `concentration_breach` and `taxable_gain_bracket` should fire by construction given the current portfolio; a third (e.g. `urgency_level_change` on a second day's run, or `regime_shift`/`death_cross` if conditions permit) should also be exercised before sign-off.
2. Confirm `rm_alert_history` correctly records both `trigger_value` and `previous_value` where applicable, and that the `entity_description` text is genuinely useful — these strings will be read directly by TOM and SUSAN.
3. Confirm `daily_risk_refresh` runs cleanly end-to-end at least once with `--skip-data-refresh` before scheduling it to also run `refresh_market_data` automatically against live market data.

---

*M7-Spec-1 | Portfolio Manager Phase B | June 2026 | tdwdev*
