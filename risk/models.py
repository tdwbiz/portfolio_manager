from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal
from core.models import Account


class ImmutableRecordError(Exception):
    """
    Raised when an attempt is made to modify or delete an immutable (append-only) record.
    """
    pass


class SecurityUniverse(models.Model):
    symbol = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=200, blank=True)
    security_type = models.CharField(max_length=20)
    asset_class = models.CharField(max_length=50)
    sector = models.CharField(max_length=100, blank=True)
    primary_currency = models.CharField(max_length=3, default="USD")
    is_held = models.BooleanField(default=False)
    is_benchmark = models.BooleanField(default=False)
    is_hedge = models.BooleanField(default=False)
    is_cef = models.BooleanField(default=False)
    use_jump_diffusion = models.BooleanField(default=False)
    options_available = models.BooleanField(default=True)
    amibroker_symbol = models.CharField(max_length=30, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "rm_security_universe"
        ordering = ["symbol"]

    def __str__(self):
        return self.symbol


class MarketPriceHistory(models.Model):
    symbol = models.CharField(max_length=20)
    date = models.DateField()
    open = models.DecimalField(max_digits=18, decimal_places=6)
    high = models.DecimalField(max_digits=18, decimal_places=6)
    low = models.DecimalField(max_digits=18, decimal_places=6)
    close = models.DecimalField(max_digits=18, decimal_places=6)
    adj_close = models.DecimalField(max_digits=18, decimal_places=6)
    volume = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, default="USD")
    data_source = models.CharField(max_length=20)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rm_market_price_history"
        unique_together = [("symbol", "date")]
        indexes = [
            models.Index(fields=["symbol", "date"]),
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"{self.symbol} - {self.date}"


class VolatilitySummary(models.Model):
    symbol = models.CharField(max_length=20)
    calc_date = models.DateField()
    lookback_days = models.IntegerField()
    volatility_annualised = models.DecimalField(max_digits=10, decimal_places=6)
    data_source = models.CharField(max_length=20)
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rm_volatility_summary"
        unique_together = [("symbol", "calc_date", "lookback_days")]
        indexes = [models.Index(fields=["symbol", "calc_date"])]

    def __str__(self):
        return f"{self.symbol} - {self.calc_date}"


class FxRate(models.Model):
    date = models.DateField(unique=True)
    usd_cad_rate = models.DecimalField(max_digits=10, decimal_places=6)
    data_source = models.CharField(max_length=20)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rm_fx_rates"
        indexes = [models.Index(fields=["date"])]

    def __str__(self):
        return f"USD/CAD on {self.date}"


class EconomicIndicator(models.Model):
    date = models.DateField()
    indicator_name = models.CharField(max_length=50)
    value = models.DecimalField(max_digits=12, decimal_places=4)
    data_source = models.CharField(max_length=30)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "rm_economic_indicators"
        unique_together = [("date", "indicator_name")]
        indexes = [
            models.Index(fields=["indicator_name", "date"]),
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f"{self.indicator_name} on {self.date}"


class AlternativePortfolio(models.Model):
    owner = models.CharField(max_length=50)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="alternative_portfolios")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "rm_alternative_portfolios"
        unique_together = [("account", "name")]

    def __str__(self):
        return f"{self.owner} - {self.name}"


class AlternativeHolding(models.Model):
    portfolio = models.ForeignKey(AlternativePortfolio, on_delete=models.CASCADE, related_name="holdings")
    symbol = models.CharField(max_length=20)
    target_weight = models.DecimalField(max_digits=6, decimal_places=4)

    class Meta:
        db_table = "rm_alternative_holdings"
        unique_together = [("portfolio", "symbol")]

    def clean(self):
        total = self.portfolio.holdings.exclude(pk=self.pk).aggregate(
            total=models.Sum("target_weight")
        )["total"] or 0
        if total + self.target_weight > Decimal("1.00001"):
            raise ValidationError("Holdings weights exceed 100% for this portfolio.")

    def __str__(self):
        return f"{self.symbol} - {self.target_weight}"


class ScoringConfig(models.Model):
    config_key = models.CharField(max_length=100, unique=True)
    config_value = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "rm_scoring_config"

    def __str__(self):
        return self.config_key


class SecurityIndexExposure(models.Model):
    symbol = models.CharField(max_length=20)
    index_name = models.CharField(max_length=50)
    exposure_weight = models.DecimalField(max_digits=6, decimal_places=4)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "rm_security_index_exposure"
        unique_together = [("symbol", "index_name")]

    def __str__(self):
        return f"{self.symbol} - {self.index_name}"


class DataQualityLog(models.Model):
    """Records data quality events: gaps, fetch failures, symbol mismatches."""

    logged_at = models.DateTimeField(auto_now_add=True)
    symbol = models.CharField(max_length=20, blank=True)
    event_type = models.CharField(max_length=30)
    # Values: 'gap_detected', 'fetch_failed', 'symbol_not_in_universe',
    #         'mutual_fund_skipped', 'ticker_mapping_applied', 'insufficient_history'
    severity = models.CharField(max_length=10)  # 'info', 'warning', 'critical'
    description = models.TextField()
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    gap_days = models.IntegerField(null=True, blank=True)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'rm_data_quality_log'
        indexes = [
            models.Index(fields=['symbol', 'logged_at']),
            models.Index(fields=['event_type', 'severity']),
            models.Index(fields=['resolved']),
        ]

    def __str__(self):
        return f"{self.event_type} - {self.symbol} - {self.severity}"


class SecurityRiskMetrics(models.Model):
    """
    Security-level risk metrics computed daily per held symbol.
    All monetary values in USD. CAD positions converted via rm_fx_rates.
    """
    symbol = models.CharField(max_length=20)
    calc_date = models.DateField()
    
    # --- Volatility ---
    vol_10d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    vol_21d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    vol_63d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    vol_252d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Annualised: std(daily log returns over window) * sqrt(252)
    # NULL if insufficient price history for that window
    
    # --- Drawdown ---
    max_drawdown_from_cost = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Maximum peak-to-trough drawdown since earliest holding date per position_history
    max_drawdown_252d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Maximum peak-to-trough drawdown over last 252 trading days from rm_market_price_history
    current_drawdown_from_252d_peak = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # (current_price - max_price_252d) / max_price_252d; negative if below peak
    
    # --- Value at Risk (1-day) ---
    var_parametric_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Parametric normal: -z_0.05 * vol_21d / sqrt(252). Stored as positive fraction (e.g. 0.0312 = 3.12% loss)
    var_parametric_99 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    var_historical_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # 5th percentile of last 252 daily log returns. Stored as positive fraction.
    var_cornish_fisher_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Cornish-Fisher expansion adjusts for skewness and kurtosis. See §3.3.
    
    # --- CVaR / Expected Shortfall ---
    cvar_95 = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Mean of daily returns below the 5th percentile of the 252-day return distribution.
    # Stored as positive fraction (expected loss in tail events).
    
    # --- 10-day VaR ---
    var_parametric_95_10d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # 1-day parametric 95% VaR scaled by sqrt(10). Square-root-of-time rule.
    var_parametric_99_10d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    
    # --- Beta ---
    beta_spy = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # OLS regression of symbol daily returns vs SPY daily returns, 252-day window.
    # USD-denominated positions use SPY directly.
    # CAD-denominated positions: use CAD-adjusted SPY returns (SPY return + FX change).
    beta_xiu = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # OLS regression vs XIU.TO daily returns, 252-day window.
    # Computed for all symbols but most meaningful for CAD-denominated ones.
    
    # --- Risk-Adjusted Return ---
    sharpe_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (mean_daily_return - daily_risk_free) / std_daily_return * sqrt(252)
    # daily_risk_free = annualised SGOV yield / 252, from rm_economic_indicators
    # mean_daily_return: log return from cost_basis_per_unit to current price / holding_days
    # If no position_history entry: use 252-day price return instead
    sortino_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Same as Sharpe but denominator uses downside deviation only (returns below risk-free)
    
    # --- Liquidity ---
    liquidity_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    # 0–100 scale. See §3.7 for formula. 100 = highly liquid; 0 = illiquid.
    avg_daily_volume_usd_30d = models.DecimalField(max_digits=20, decimal_places=2, null=True)
    # Average (volume * close) over last 30 trading days from rm_market_price_history.
    # USD equivalent. NULL for mutual funds with no volume data.
    
    # --- Unrealised Gain at Risk ---
    ugl_at_risk_95_usd = models.DecimalField(max_digits=15, decimal_places=2, null=True)
    # For the largest position in this symbol across all accounts:
    # 1-day 95% parametric VaR applied to total market value in USD.
    # = position_market_value_usd * var_parametric_95
    ugl_at_risk_pct_of_ugl = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # ugl_at_risk_usd / total_unrealised_gain_usd (across all accounts for this symbol).
    # NULL if total UGL <= 0.
    
    # --- Metadata ---
    calc_timestamp = models.DateTimeField(auto_now_add=True)
    price_data_source = models.CharField(max_length=20, blank=True)
    # 'amibroker', 'yfinance', or 'mixed' — which source provided the price data used
    price_rows_used = models.IntegerField(default=0)
    # Number of price rows available for this symbol at calc time (for audit)
    
    class Meta:
        db_table = 'rm_security_risk_metrics'
        unique_together = [('symbol', 'calc_date')]
        indexes = [
            models.Index(fields=['symbol', 'calc_date']),
            models.Index(fields=['calc_date']),
        ]

    def __str__(self):
        return f"{self.symbol} - {self.calc_date}"


class CorrelationMatrix(models.Model):
    """
    Pairwise correlation between symbols.
    Both (A, B) and (B, A) are stored for query convenience.
    Diagonal (A, A) is stored as 1.0.
    """
    symbol_a = models.CharField(max_length=20)
    symbol_b = models.CharField(max_length=20)
    calc_date = models.DateField()
    pearson_252d = models.DecimalField(max_digits=8, decimal_places=6)
    # Pearson r of daily log returns over the last 252 trading days.
    # Range: -1.0 to 1.0.
    # Only computed where both symbols have >= 126 overlapping trading days.
    price_rows_overlap = models.IntegerField()
    # Number of dates where both symbols had price data (for quality assessment)
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_correlation_matrix'
        unique_together = [('symbol_a', 'symbol_b', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'symbol_a']),
            models.Index(fields=['calc_date', 'symbol_b']),
        ]

    def __str__(self):
        return f"{self.symbol_a} - {self.symbol_b} - {self.calc_date}"


class RegimeState(models.Model):
    """
    Detected macroeconomic regime at each daily calculation.
    Four-quadrant model: growth vs contraction, inflation vs deflation.
    """
    calc_date = models.DateField(unique=True)

    quadrant = models.CharField(max_length=2)
    # Q1: Growth + Low Inflation (risk-on, equities supported)
    # Q2: Growth + High Inflation (reflation; commodities, real assets favoured)
    # Q3: Contraction + Low Inflation (deflation/recession; defensive positioning)
    # Q4: Contraction + High Inflation (stagflation; most adverse for equities and bonds)

    confidence_score = models.DecimalField(max_digits=5, decimal_places=2)
    # 0.0 to 100.0. Probability gap between highest and second-highest quadrant.

    transition_flag = models.BooleanField(default=False)
    # True if probability of quadrant change exceeds transition_threshold_pct

    yield_curve_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    vix_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    cpi_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    pmi_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    gdp_score = models.DecimalField(max_digits=5, decimal_places=2, null=True)

    prob_q1 = models.DecimalField(max_digits=5, decimal_places=4)
    prob_q2 = models.DecimalField(max_digits=5, decimal_places=4)
    prob_q3 = models.DecimalField(max_digits=5, decimal_places=4)
    prob_q4 = models.DecimalField(max_digits=5, decimal_places=4)

    indicator_snapshot_json = models.TextField()
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_regime_state'
        indexes = [models.Index(fields=['calc_date'])]

    def __str__(self):
        return f"{self.calc_date} - {self.quadrant}"


class MarketCondition(models.Model):
    """
    Technical indicator values per symbol per calculation date.
    Computed from rm_market_price_history OHLCV.
    """
    symbol = models.CharField(max_length=20)
    calc_date = models.DateField()

    # --- Trend: Moving Averages ---
    sma_50 = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    sma_200 = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    price_vs_sma50_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    price_vs_sma200_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    # --- Moving Average Crossover ---
    crossover_type = models.CharField(max_length=10, blank=True)
    crossover_date = models.DateField(null=True, blank=True)

    # --- Momentum: RSI ---
    rsi_14 = models.DecimalField(max_digits=6, decimal_places=2, null=True)

    # --- Momentum: MACD ---
    macd_line = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    macd_signal = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    macd_histogram = models.DecimalField(max_digits=12, decimal_places=6, null=True)
    macd_crossover = models.CharField(max_length=10, blank=True)

    # --- Volatility: Bollinger Bands ---
    bb_upper = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    bb_middle = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    bb_lower = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    bb_bandwidth = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    bb_pct_b = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    # --- Volume: On-Balance Volume ---
    obv = models.BigIntegerField(null=True)
    obv_trend = models.CharField(max_length=10, blank=True)

    # --- Market Phase Classification ---
    market_phase = models.CharField(max_length=15)

    high_52w = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    low_52w = models.DecimalField(max_digits=18, decimal_places=4, null=True)
    pct_from_52w_high = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    price_rows_used = models.IntegerField(default=0)
    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_market_condition'
        unique_together = [('symbol', 'calc_date')]
        indexes = [
            models.Index(fields=['symbol', 'calc_date']),
            models.Index(fields=['calc_date']),
            models.Index(fields=['market_phase', 'calc_date']),
        ]

    def __str__(self):
        return f"{self.symbol} - {self.calc_date} - {self.market_phase}"


class RegimeSensitivity(models.Model):
    """
    Sensitivity multiplier for each (asset_class, regime_quadrant) combination.
    """
    asset_class = models.CharField(max_length=50)
    regime_quadrant = models.CharField(max_length=2)
    sensitivity_multiplier = models.DecimalField(max_digits=5, decimal_places=2)
    rationale = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = 'rm_regime_sensitivity'
        unique_together = [('asset_class', 'regime_quadrant')]

    def __str__(self):
        return f"{self.asset_class} / {self.regime_quadrant}: {self.sensitivity_multiplier}"


class PortfolioRiskMetrics(models.Model):
    """
    Aggregated risk metrics at account, owner, or portfolio level.
    All monetary values in USD. Computed from rm_security_risk_metrics
    and rm_correlation_matrix using position weights from vw_current_portfolio.
    """
    entity_type = models.CharField(max_length=20)
    # Values: 'account', 'owner', 'portfolio'
    entity_id = models.CharField(max_length=50)
    # account: core_account.account_id string (e.g. 'TAXABLE_TOM_SUSAN')
    # owner: 'TOM' or 'SUSAN'
    # portfolio: 'ALL'
    calc_date = models.DateField()

    # --- Aggregate VaR ---
    var_portfolio_95_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Markowitz correlated VaR at 95% confidence, 1-day horizon, in USD.
    # = sqrt(w^T * Sigma * w) * z_0.05
    var_portfolio_99_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    var_portfolio_95_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # var_portfolio_95_usd / total_market_value_usd — fraction of portfolio at risk
    var_undiversified_95_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Simple sum of individual VaRs (ignores correlation) — for comparison only
    diversification_benefit_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # var_undiversified_95_usd - var_portfolio_95_usd — dollar value of diversification

    # --- Portfolio Volatility ---
    portfolio_vol_annualised = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # Annualised portfolio volatility = sqrt(w^T * Sigma_annual * w)
    # where Sigma_annual uses 252d annualised vols on the diagonal
    diversification_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # weighted_avg_individual_vol / portfolio_vol_annualised
    # > 1.0 means diversification is reducing risk. The higher, the better.
    weighted_avg_vol = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # sum(weight_i * vol_252d_i) — naive weighted average, ignoring correlations

    # --- Beta ---
    portfolio_beta_spy = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Weighted average of individual beta_spy values using market value weights
    portfolio_beta_xiu = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Weighted average of individual beta_xiu values

    # --- Risk-Adjusted Returns ---
    portfolio_sharpe = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Portfolio Sharpe: constructed from daily portfolio return series
    # (sum of weight_i * return_i each day over 252-day window), then standard Sharpe formula
    portfolio_sortino = models.DecimalField(max_digits=8, decimal_places=4, null=True)

    # --- Concentration Metrics ---
    top1_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Weight of largest single position as fraction of entity total MV
    top3_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    top5_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    top1_symbol = models.CharField(max_length=20, blank=True)
    # Symbol name of largest single position (for display)

    # --- HHI Concentration ---
    hhi_symbol = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # Herfindahl-Hirschman Index by symbol weight. sum(weight_i^2). Range 0–1.
    # 1.0 = single position; ~0.04 = perfectly equal 25-position portfolio
    hhi_sector = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # HHI computed on sector weights (from rm_security_universe.sector)
    hhi_asset_class = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # HHI computed on asset_class weights

    # --- Effective Index Concentration ---
    # Derived from rm_security_index_exposure × position weights
    # E.g. VFV(13.5% of portfolio) × SP500 exposure(1.0) + SPY(3.4%) × 1.0 + VOO(2.9%) × 1.0 = 19.8% effective SP500
    effective_sp500_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_nasdaq100_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_tsx_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_semiconductor_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_technology_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    effective_dividend_weight = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # effective_DIVIDEND_CA + effective_DIVIDEND_US combined

    # --- Tax Risk Flag ---
    taxable_ugl_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Total unrealised gain in USD across all taxable accounts in this entity
    # Only populated for entity_type='account' (taxable) and owner/portfolio roll-up
    tax_risk_flag = models.BooleanField(default=False)
    # True if taxable_ugl_usd > threshold from rm_scoring_config ('taxable_ugl_alert_usd')

    # --- Portfolio Size ---
    total_market_value_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    total_market_value_cad = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    position_count = models.IntegerField(default=0)
    # Number of distinct symbol positions in this entity

    # --- Metadata ---
    calc_timestamp = models.DateTimeField(auto_now_add=True)
    positions_used = models.IntegerField(default=0)
    # Positions for which security-level metrics were available
    positions_skipped = models.IntegerField(default=0)
    # Positions where M1 metrics were NULL (insufficient data) — excluded from VaR calc

    class Meta:
        db_table = 'rm_portfolio_risk_metrics'
        unique_together = [('entity_type', 'entity_id', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'entity_type']),
            models.Index(fields=['entity_id', 'calc_date']),
        ]

    def __str__(self):
        return f"{self.entity_type} - {self.entity_id} - {self.calc_date}"


class SecurityDriver(models.Model):
    """
    Static risk driver characteristics per symbol.
    Updated by the developer as portfolio changes; not recomputed daily.
    These values represent the inherent riskiness of the instrument itself,
    independent of market conditions.
    """
    symbol = models.CharField(max_length=20, unique=True)

    asset_class_baseline = models.DecimalField(max_digits=5, decimal_places=2)
    # Baseline risk score 0–100 for this asset class. Higher = riskier.
    # Reference baselines:
    #   cash_equiv: 10    mutual_fund: 12    etf_bond: 25
    #   etf_equity: 45    cef: 55            common_stock: 60
    #   crypto_etf: 80

    sector_cyclicality = models.DecimalField(max_digits=5, decimal_places=2)
    # 0–100. Higher = more cyclical = more sensitive to economic regime.
    # Reference:
    #   Utilities/Consumer Staples: 20   Healthcare: 30
    #   Financials/Dividend: 40          Broad Market: 45
    #   Technology: 65                   Semiconductor: 75
    #   Crypto: 85

    liquidity_tier = models.DecimalField(max_digits=5, decimal_places=2)
    # Risk score from liquidity profile. Higher = less liquid = more risky.
    # cash_equiv/mutual_fund: 5    large ETF: 15    mid ETF: 25
    #   CEF (ADX): 45              small-cap stock: 55

    earnings_proximity_flag = models.BooleanField(default=False)
    # True if earnings announcement is within 14 days (updated externally or manually)
    # When True: add earnings_proximity_boost from rm_scoring_config to driver score

    notes = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_security_driver'
        ordering = ['symbol']

    def __str__(self):
        return self.symbol


class InstrumentType(models.Model):
    """
    A category of risk management instrument.
    Defines mechanics, cost structure, and eligibility rules.
    This is reference data — edited via Django admin, not by application logic.
    """
    code = models.CharField(max_length=30, unique=True)
    # Machine-readable identifier used by M4 engine.
    # Values: 'protective_put', 'collar', 'covered_call', 'trailing_stop',
    #         'inverse_etf', 'sector_rotation', 'partial_liquidation', 'cash_raise',
    #         'diversification'

    name = models.CharField(max_length=100)
    # Human-readable name. E.g. "Protective Put Option"

    mechanics_description = models.TextField()
    # Plain English: what this instrument is and how it works.
    # Written for a knowledgeable investor, not a finance textbook.

    use_cases_json = models.TextField()
    # JSON list of strings. When is this instrument appropriate?
    # E.g. ["High urgency single stock", "Pre-earnings risk reduction",
    #        "Taxable position where sale is not desirable"]

    cost_structure_json = models.TextField()
    # JSON object. How does this instrument cost money?
    # E.g. {"type": "premium", "typical_pct_of_notional": 0.02,
    #        "bid_ask_spread_pct": 0.003, "commission_per_contract": 1.50}
    # For trailing_stop: {"type": "friction_only", "bid_ask_spread_pct": 0.001,
    #                     "commission_per_trade": 1.00}

    tax_treatment_json = models.TextField()
    # JSON object keyed by account_category.
    # E.g. {
    #   "TFSA": {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "No tax consequence"},
    #   "RRSP": {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "Sheltered"},
    #   "LIF":  {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "Sheltered; locked-in"},
    #   "RRIF": {"tax_cost_on_exercise": false, "wash_sale_applicable": false, "notes": "Sheltered; mandatory drawdown"},
    #   "Taxable": {"tax_cost_on_exercise": true, "wash_sale_applicable": true,
    #               "notes": "Exercise triggers capital gain; wash sale if repurchase within 30 days"}
    # }

    # Account type eligibility flags
    eligible_tfsa = models.BooleanField(default=True)
    eligible_rrsp = models.BooleanField(default=True)
    eligible_lif = models.BooleanField(default=True)
    eligible_rrif = models.BooleanField(default=True)
    eligible_taxable = models.BooleanField(default=True)
    # False means this instrument CANNOT be used in that account type.
    # E.g. options strategies are generally not available in registered accounts at most brokers.
    # For this portfolio (IB accounts): options ARE available in IB_SUSAN, IB_TOM, IB_JOINT.
    # For registered accounts (RRSP, TFSA, etc.): options are NOT available — set to False.

    requires_options_availability = models.BooleanField(default=False)
    # True for protective_put, collar, covered_call.
    # M4 checks rm_security_universe.options_available before recommending these.

    wash_sale_applicable = models.BooleanField(default=False)
    # True if this instrument can trigger wash sale rules (US tax law).
    # Applies to: partial_liquidation followed by repurchase.
    # Relevant for IB_SUSAN, IB_TOM, IB_JOINT (US-listed securities in taxable accounts).

    sort_order = models.IntegerField(default=0)
    # Display ordering in admin and UI. Lower = shown first.

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_instrument_type'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.code} - {self.name}"


class StrategyTemplate(models.Model):
    """
    A named, scenario-specific risk management strategy.
    Combines one or more instrument types into an actionable playbook.
    M4 queries this table to find candidates for a given position/urgency/regime.
    """
    code = models.CharField(max_length=50, unique=True)
    # Machine-readable identifier. E.g. 'collar_high_gain_taxable_stock'

    name = models.CharField(max_length=150)
    # Human-readable name shown in reports and dashboard.

    description = models.TextField()
    # Plain English explanation of the strategy: what it does, why it works,
    # and what the investor gives up. Written for an informed investor.

    # --- Tagging — used by M4 for candidate filtering ---

    applicable_urgency_levels = models.CharField(max_length=50)
    # Comma-separated list of urgency levels where this template is relevant.
    # Values: 'Low', 'Medium', 'High', 'Critical'
    # E.g. 'High,Critical' — only recommend this template for High or Critical urgency.

    applicable_regime_quadrants = models.CharField(max_length=20)
    # Comma-separated list of regime quadrants. 'ALL' means regime-agnostic.
    # E.g. 'Q3,Q4' — only recommend in contraction/stagflation regimes.
    # E.g. 'ALL' — recommend regardless of regime.

    applicable_asset_types = models.CharField(max_length=100)
    # Comma-separated list of asset_class values from rm_security_universe.
    # E.g. 'common_stock,etf_equity' — applies to stocks and equity ETFs.
    # E.g. 'common_stock' — stocks only (e.g. options-based strategies).

    applicable_account_categories = models.CharField(max_length=100)
    # Comma-separated list of account categories where this template is valid.
    # Values match core_account.category: 'Investment', 'Investment Joint',
    #   'TFSA', 'RRSP', 'LIF', 'RRIF'
    # E.g. 'Investment,Investment Joint' — taxable accounts only.
    # E.g. 'ALL' — any account type.

    min_unrealised_gain_usd = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Minimum UGL in USD for this template to be applicable.
    # E.g. 50000 — only recommend partial liquidation if gain > $50K USD.
    # 0 = no minimum (applicable at any gain level, including losses).

    requires_options = models.BooleanField(default=False)
    # True if any instrument in this template requires options availability.
    # M4 checks rm_security_universe.options_available and skips if False.

    cef_eligible = models.BooleanField(default=True)
    # False if this template cannot be used for closed-end funds (e.g. ADX).
    # Options-based templates must have cef_eligible=False.

    lif_rrif_eligible = models.BooleanField(default=True)
    # False if withdrawal restrictions make this template impractical for LIF/RRIF.
    # Partial liquidation in a LIF triggers locked-in pension rules — flag it.

    is_tax_triggering = models.BooleanField(default=False)
    # True if applying this strategy will crystallise a taxable gain.
    # Drives M8 tax acknowledgement workflow when recommendation is marked actioned.
    # True for: partial_liquidation in taxable accounts, covered_call (premium is income),
    #           inverse_etf (when closed at a gain).

    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_strategy_template'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.code} - {self.name}"


class StrategyInstrumentLink(models.Model):
    """
    Links a strategy template to the instrument types it uses.
    role describes how the instrument functions within the strategy.
    """
    strategy = models.ForeignKey(
        StrategyTemplate, on_delete=models.CASCADE, related_name='instrument_links'
    )
    instrument = models.ForeignKey(
        InstrumentType, on_delete=models.CASCADE, related_name='strategy_links'
    )
    role = models.CharField(max_length=50)
    # How this instrument is used in the strategy.
    # E.g. 'primary_hedge', 'income_offset', 'exit_trigger'
    sort_order = models.IntegerField(default=0)

    class Meta:
        db_table = 'rm_strategy_instrument_link'
        unique_together = [('strategy', 'instrument')]
        ordering = ['sort_order']

    def __str__(self):
        return f"{self.strategy.code} -> {self.instrument.code} ({self.role})"


class UrgencyScore(models.Model):
    """
    Urgency score for a risk entity. Produced daily after M1 and M2-Spec-1 complete.
    entity_type='security_in_account': per symbol per account (most granular)
    entity_type='account': rolled-up per account
    entity_type='owner': rolled-up per owner
    entity_type='portfolio': whole portfolio
    """
    ENTITY_TYPES = [
        ('security_in_account', 'Security in Account'),
        ('account', 'Account'),
        ('owner', 'Owner'),
        ('portfolio', 'Portfolio'),
    ]

    URGENCY_LEVELS = [
        ('Low', 'Low'),
        ('Medium', 'Medium'),
        ('High', 'High'),
        ('Critical', 'Critical'),
    ]

    DOMINANT_COMPONENTS = [
        ('m1_metrics', 'M1 Metrics'),
        ('security_driver', 'Security Driver'),
        ('regime', 'Regime'),
        ('market_condition', 'Market Condition'),
    ]

    entity_type = models.CharField(max_length=20, choices=ENTITY_TYPES)
    entity_id = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20, blank=True)
    account_id = models.CharField(max_length=50, blank=True)
    calc_date = models.DateField()

    # --- Composite Score ---
    score_raw = models.DecimalField(max_digits=6, decimal_places=2)
    # Weighted composite 0.0–100.0

    urgency_level = models.CharField(max_length=10, choices=URGENCY_LEVELS)
    # Thresholds from rm_scoring_config:
    #   Low: score < threshold_low (default 25)
    #   Medium: threshold_low <= score < threshold_medium (default 50)
    #   High: threshold_medium <= score < threshold_high (default 75)
    #   Critical: score >= threshold_high

    # --- Component Scores (0–100 each, before weighting) ---
    component_m1_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    component_driver_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    component_regime_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    component_market_score = models.DecimalField(max_digits=6, decimal_places=2, null=True)

    # --- Dominant Factor ---
    dominant_component = models.CharField(max_length=20, blank=True, choices=DOMINANT_COMPONENTS)

    component_scores_json = models.TextField()
    # Full JSON snapshot for M8 audit trail

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_urgency_score'
        # Note: Removed unique_together to allow multiple security_in_account entries per account
        # Each (entity_type, entity_id, calc_date) should be unique EXCEPT for security_in_account
        # where multiple symbols per account are expected
        indexes = [
            models.Index(fields=['calc_date', 'entity_type']),
            models.Index(fields=['entity_id', 'calc_date']),
            models.Index(fields=['urgency_level', 'calc_date']),
            models.Index(fields=['symbol', 'calc_date']),
            # Composite index for security_in_account queries
            models.Index(fields=['entity_type', 'entity_id', 'symbol', 'calc_date'], name='idx_urgency_sec_account'),
        ]

    def __str__(self):
        return f"{self.entity_type} - {self.entity_id} - {self.urgency_level} ({self.calc_date})"


class OwnerConfig(models.Model):
    """
    Owner-level configuration for tax rates and strategy preferences.
    Populated manually via Django admin — NOT computed by the system.
    """
    owner = models.CharField(max_length=50, unique=True)
    # 'TOM' or 'SUSAN'

    marginal_tax_rate_ca = models.DecimalField(max_digits=5, decimal_places=4)
    # Canadian marginal income tax rate (federal + provincial combined).
    # Used for: capital gains tax = 0.5 * unrealised_gain * marginal_tax_rate_ca
    # (Canada taxes 50% of capital gains as income for gains up to $250K annually;
    #  66.67% inclusion for gains above $250K — store both rates if needed)
    # Example: 0.4326 = 43.26% combined marginal rate

    capital_gains_inclusion_rate = models.DecimalField(max_digits=4, decimal_places=4, default=0.5000)
    # Fraction of capital gain included in income for tax purposes.
    # Canada 2024+: 0.5000 for first $250K annually; 0.6667 above $250K.
    # Default 0.5000. Set to 0.6667 if owner's annual gains exceed $250K threshold.

    cost_tolerance_pct = models.DecimalField(max_digits=5, decimal_places=4, default=0.0200)
    # Maximum acceptable hedging cost as a fraction of position market value.
    # Example: 0.0200 = willing to spend up to 2% of position value on hedging annually.
    # M4 uses this to filter out strategies that are too expensive.

    preferences_json = models.TextField(default='{}')
    # JSON blob for any additional owner preferences.
    # E.g. {"max_option_expiry_months": 6, "preferred_trail_pct": 0.10,
    #        "avoid_inverse_etf": false, "max_annual_gain_realisation_usd": 50000}

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=100, blank=True)
    # Who last updated these rates — for audit trail.

    class Meta:
        db_table = 'rm_owner_config'

    def __str__(self):
        return f"{self.owner} — {self.marginal_tax_rate_ca * 100:.2f}% marginal tax"


class StrategyRecommendation(models.Model):
    """
    A ranked strategy recommendation for a specific position in a specific account.
    Produced by calc_strategy_recommendations management command.
    All monetary values in USD.
    """
    # --- Entity identification ---
    symbol = models.CharField(max_length=20)
    account_id = models.CharField(max_length=50)
    # core_account.account_id string (e.g. 'IB_SUSAN')
    owner = models.CharField(max_length=50)
    calc_date = models.DateField()

    # --- Linked objects ---
    strategy_template = models.ForeignKey(
        'StrategyTemplate', on_delete=models.PROTECT, related_name='recommendations'
    )
    rank = models.IntegerField()
    # 1 = best recommendation for this position on this date. Lower is better.

    # --- Input context (snapshot at time of calculation) ---
    urgency_level = models.CharField(max_length=10)
    # Urgency level that triggered this recommendation run
    urgency_score = models.DecimalField(max_digits=6, decimal_places=2)
    regime_quadrant = models.CharField(max_length=2)
    position_quantity = models.DecimalField(max_digits=12, decimal_places=4)
    position_market_value_usd = models.DecimalField(max_digits=15, decimal_places=2)
    position_unrealised_gain_usd = models.DecimalField(max_digits=15, decimal_places=2)
    cost_basis_per_unit_usd = models.DecimalField(max_digits=12, decimal_places=4)
    current_price_usd = models.DecimalField(max_digits=12, decimal_places=4)
    vol_63d = models.DecimalField(max_digits=10, decimal_places=6, null=True)
    # 63-day volatility used for option pricing

    # --- Cost estimates ---
    estimated_strategy_cost_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Total estimated cost to implement this strategy:
    # For options: Black-Scholes premium + commission + bid-ask
    # For trailing stop: commission only (no premium)
    # For inverse ETF: estimated annual decay cost
    estimated_strategy_cost_pct = models.DecimalField(max_digits=8, decimal_places=4)
    # estimated_strategy_cost_usd / position_market_value_usd

    tax_cost_estimate_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    # Estimated capital gains tax if this strategy triggers a taxable sale.
    # = unrealised_gain_usd_sold * inclusion_rate * marginal_tax_rate
    # 0 for strategies that do not require a sale (protective_put, collar, inverse_etf)
    # 0 for tax-free accounts (TFSA, RRSP, RRIF)

    tax_impact_note = models.CharField(max_length=300, blank=True)
    # Plain English tax impact description.
    # E.g. "Triggering this stop would crystallise ~$331K USD gain. Estimated tax: ~$71K USD."
    # E.g. "No taxable event unless call is exercised (VFV called away at strike price)."
    # E.g. "No tax consequence — TFSA account."

    is_tax_triggering = models.BooleanField(default=False)
    # True if this recommendation will definitely trigger a taxable event on execution.
    # Drives M8 tax acknowledgement workflow.

    # --- Risk reduction estimates ---
    estimated_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Estimated reduction in 1-day 95% VaR in USD if this strategy is applied.
    estimated_var_reduction_pct = models.DecimalField(max_digits=8, decimal_places=4)
    # As fraction of position_market_value_usd

    estimated_urgency_delta = models.DecimalField(max_digits=6, decimal_places=2, null=True)
    # Estimated change in urgency score if strategy is applied. Negative = score decreases.

    # --- Ranking metric ---
    cost_per_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True)
    # (estimated_strategy_cost_usd + tax_cost_estimate_usd * tax_penalty_multiplier)
    #   / estimated_var_reduction_usd
    # Lower is better — more risk reduction per dollar spent.
    # NULL if estimated_var_reduction_usd is zero.

    # --- Constraint audit ---
    constraints_applied_json = models.TextField()
    # JSON list of constraints that filtered the candidate set.
    # E.g. ["options_not_available: removed protective_put, collar, covered_call",
    #        "cef: removed all options templates",
    #        "lif_restriction: removed partial_liquidation"]
    # Stored for M8 explainability.

    # --- Status ---
    status = models.CharField(max_length=20, default='active')
    # 'active': current recommendation
    # 'actioned': user has marked this as implemented
    # 'superseded': a newer recommendation replaced this one
    # 'acknowledged': user acknowledged but did not act

    actioned_at = models.DateTimeField(null=True, blank=True)
    actioned_by = models.CharField(max_length=100, blank=True)

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_strategy_recommendation'
        indexes = [
            models.Index(fields=['symbol', 'account_id', 'calc_date']),
            models.Index(fields=['calc_date', 'urgency_level']),
            models.Index(fields=['status', 'calc_date']),
            models.Index(fields=['is_tax_triggering', 'status']),
        ]

    def __str__(self):
        return f"{self.symbol} ({self.account_id}) — {self.strategy_template.code} — rank {self.rank} ({self.calc_date})"


class HistoricalScenario(models.Model):
    """
    A historical market scenario with predefined asset-class and benchmark returns.
    Used by the backtesting engine to simulate portfolio performance under
    known historical conditions (e.g., 2022 rate shock, 2020 COVID crash).
    """
    name = models.CharField(max_length=100, unique=True)
    # Machine-readable name, e.g. '2022_rate_shock', '2020_covid_crash'

    display_name = models.CharField(max_length=200)
    # Human-readable name, e.g. '2022 Rate Shock (Bank of Canada 425bp hikes)'

    start_date = models.DateField()
    end_date = models.DateField()

    description = models.TextField(blank=True)
    # Longer description of the scenario context

    asset_class_returns_json = models.TextField()
    # JSON object mapping asset class codes to total returns.
    # E.g. {"equity_ca": -0.18, "equity_us": -0.05, "fixed_income_ca": -0.12,
    #       "commodities": 0.02, "real_estate": -0.20, "cash": 0.04}
    # These are applied in stress-test mode when no price history exists.

    benchmark_spy_return = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    # S&P 500 ETF (SPY) total return during the scenario period.
    # Used as a US equity benchmark for comparison.

    benchmark_qqq_return = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    # NASDAQ-100 ETF (QQQ) total return during the scenario period.
    # Used as a tech-heavy benchmark.

    benchmark_vdy_return = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    # Canadian broad-market ETF (VDY) total return during the scenario period.
    # Used as a Canadian equity benchmark.

    is_active = models.BooleanField(default=True)
    # Only active scenarios are available for backtesting

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_historical_scenario'
        ordering = ['start_date']

    def __str__(self):
        return f"{self.display_name} ({self.start_date} → {self.end_date})"


class BacktestRun(models.Model):
    """
    A single backtest execution record. Append-only — never updated or deleted.
    Links a position, strategy, and historical scenario to produce a BacktestResult.
    """
    symbol = models.CharField(max_length=20)
    account_id = models.CharField(max_length=50)
    owner = models.CharField(max_length=50)
    strategy_template = models.ForeignKey(
        'StrategyTemplate', on_delete=models.PROTECT, related_name='backtest_runs'
    )
    scenario = models.ForeignKey(
        'HistoricalScenario', on_delete=models.PROTECT, related_name='backtest_runs'
    )
    start_date = models.DateField()
    end_date = models.DateField()
    quantity = models.DecimalField(max_digits=12, decimal_places=4)
    cost_basis_per_unit_usd = models.DecimalField(max_digits=12, decimal_places=4)
    start_price_usd = models.DecimalField(max_digits=12, decimal_places=4)
    currency = models.CharField(max_length=3, default='CAD')
    # CAD or USD

    strategy_params_json = models.TextField(default='{}')
    # JSON blob of strategy-specific parameters used in this run.
    # E.g. {"trail_pct": 0.10} for trailing stop,
    #      {"hedge_ratio": 0.50, "option_expiry_months": 3} for protective put,
    #      {"put_strike_pct": 0.85, "call_strike_pct": 1.12} for collar

    run_timestamp = models.DateTimeField(auto_now_add=True)
    run_by = models.CharField(max_length=100, blank=True)
    # 'system' or the username of the person who triggered the run

    class Meta:
        db_table = 'rm_backtest_run'
        indexes = [
            models.Index(fields=['symbol', 'account_id']),
            models.Index(fields=['scenario', 'run_timestamp']),
            models.Index(fields=['strategy_template', 'run_timestamp']),
        ]

    def __str__(self):
        return f"{self.symbol} ({self.account_id}) — {self.strategy_template.code} — {self.scenario.name}"


class BacktestResult(models.Model):
    """
    Computed results from a single backtest run. One-to-one with BacktestRun.
    All monetary values in USD. Append-only.
    """
    run = models.OneToOneField(
        BacktestRun, on_delete=models.CASCADE, related_name='result'
    )

    # --- Total Returns ---
    total_return_unhedged_pct = models.DecimalField(max_digits=8, decimal_places=6)
    # Total return of unhedged position over the period (e.g. -0.18 = -18%)

    total_return_hedged_pct = models.DecimalField(max_digits=8, decimal_places=6)
    # Total return of hedged position including hedge P&L

    return_improvement_pct = models.DecimalField(max_digits=8, decimal_places=6)
    # total_return_hedged_pct - total_return_unhedged_pct

    # --- Drawdown ---
    max_drawdown_unhedged = models.DecimalField(max_digits=8, decimal_places=6)
    # Peak-to-trough drawdown of unhedged position (negative value)

    max_drawdown_hedged = models.DecimalField(max_digits=8, decimal_places=6)
    # Peak-to-trough drawdown of hedged position

    drawdown_reduction_pct = models.DecimalField(max_digits=8, decimal_places=6)
    # max_drawdown_unhedged - max_drawdown_hedged (positive = reduction)

    # --- Hedge Cost ---
    hedge_cost_total_usd = models.DecimalField(max_digits=12, decimal_places=2)
    # Total cost of the hedge over the period (premium + commission)

    hedge_cost_pct_of_position = models.DecimalField(max_digits=8, decimal_places=6)
    # hedge_cost_total_usd / position_value_start_usd

    # --- Hedge Effectiveness ---
    hedge_effectiveness_ratio = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    # (drawdown_unhedged - drawdown_hedged) / hedge_cost_pct_of_position
    # Positive = hedge reduced drawdown more than its cost. NULL if cost is zero.

    breakeven_annual_cost_pct = models.DecimalField(max_digits=8, decimal_places=6, null=True, blank=True)
    # Annualized cost percentage at which hedge breaks even vs. unhedged drawdown.
    # Used to assess whether the hedge cost was justified.

    # --- Strategy-specific outcomes ---
    stop_triggered = models.BooleanField(null=True, blank=True, default=None)
    # True if trailing stop was triggered during the period
    stop_trigger_date = models.DateField(null=True, blank=True)
    stop_trigger_price_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    option_expired_worthless = models.BooleanField(null=True, blank=True, default=None)
    # True if put/call options expired without being exercised
    option_payoff_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # Net payoff from options (positive = gain, negative = loss)

    # --- Position values ---
    position_value_start_usd = models.DecimalField(max_digits=15, decimal_places=2)
    # quantity * start_price_usd

    position_value_end_unhedged_usd = models.DecimalField(max_digits=15, decimal_places=2)
    # Final unhedged position value

    position_value_end_hedged_usd = models.DecimalField(max_digits=15, decimal_places=2)
    # Final hedged position value + hedge P&L

    # --- Regime split ---
    regime_split_json = models.TextField(default='{}')
    # JSON object mapping regime quadrants to sub-period returns.
    # E.g. {"bull_bear": {"unhedged": -0.05, "hedged": -0.02, "days": 45}, ...}

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_backtest_result'

    def __str__(self):
        return f"{self.run.symbol} — {self.run.strategy_template.code} — {self.run.scenario.name}"


class SimulationRun(models.Model):
    """
    Input parameters for a Monte Carlo or stress test run.
    Append-only — no modification after creation.
    """
    SIMULATION_TYPES = [
        ('montecarlo', 'Monte Carlo'),
        ('stress_test', 'Stress Test'),
        ('custom_stress', 'Custom Stress'),
    ]

    simulation_type = models.CharField(max_length=20, choices=SIMULATION_TYPES)

    # --- Scope ---
    entity_type = models.CharField(max_length=20)
    # 'portfolio' | 'account' | 'symbol'
    entity_id = models.CharField(max_length=50)
    # portfolio: 'ALL', account: account_id string, symbol: ticker

    # --- Monte Carlo parameters (null for stress tests) ---
    n_paths = models.IntegerField(null=True, blank=True)
    # Number of simulated paths. Default 10,000.
    horizon_days = models.IntegerField(null=True, blank=True)
    # Simulation horizon in trading days. Default 252 (1 year).
    use_jump_diffusion = models.BooleanField(default=False)
    # If True: apply Merton jump-diffusion for symbols flagged in rm_security_universe
    random_seed = models.IntegerField(null=True, blank=True)
    # If set: reproducible results. NULL = random seed each run.

    # --- Strategy overlay (optional) ---
    recommendation_id = models.IntegerField(null=True, blank=True)
    # FK to rm_strategy_recommendation (nullable FK — store id only to avoid circular imports)
    # If set: apply this strategy to the simulated paths and compare hedged vs unhedged.

    # --- Stress test parameters (null for Monte Carlo) ---
    scenario_name = models.CharField(max_length=100, blank=True)
    # Named scenario from rm_historical_scenario, or 'custom'
    custom_shocks_json = models.TextField(blank=True)
    # For custom stress: JSON {symbol: shock_pct} e.g. {"SOXX": -0.30, "SMH": -0.28, "NVDA": -0.35}

    # --- Metadata ---
    calc_date = models.DateField()
    # Position weights and prices as of this date
    run_timestamp = models.DateTimeField(auto_now_add=True)
    run_by = models.CharField(max_length=100, blank=True)
    # 'system' or username
    duration_seconds = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    # Wall-clock time for this run — useful for performance monitoring

    class Meta:
        db_table = 'rm_simulation_run'
        indexes = [
            models.Index(fields=['entity_type', 'entity_id', 'calc_date']),
            models.Index(fields=['simulation_type', 'run_timestamp']),
        ]

    def __str__(self):
        return f"{self.simulation_type} — {self.entity_type} {self.entity_id} — {self.calc_date}"


class SimulationResult(models.Model):
    """
    Output metrics for a completed simulation run.
    All monetary values in USD.
    """
    run = models.OneToOneField(
        SimulationRun, on_delete=models.CASCADE, related_name='result'
    )

    # --- Portfolio value at start ---
    portfolio_value_start_usd = models.DecimalField(max_digits=18, decimal_places=2)
    # Total market value of positions in scope at calc_date

    # --- Monte Carlo outcome distribution (unhedged) ---
    pct5_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # 5th percentile of final portfolio value across all paths — worst 5% of outcomes
    pct25_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct50_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Median outcome
    pct75_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct95_outcome_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # 95th percentile — best 5% of outcomes

    # --- Loss probability thresholds ---
    prob_loss_5pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.95 (>5% loss)
    prob_loss_10pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.90
    prob_loss_20pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.80
    prob_loss_50pct = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    # Fraction of paths where final value < start * 0.50

    # --- Expected Shortfall (tail risk) ---
    expected_shortfall_5pct_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Mean portfolio value across the worst 5% of paths (CVaR at 95%)
    expected_shortfall_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    # (portfolio_value_start - expected_shortfall_5pct) / portfolio_value_start

    # --- Hedged comparison (populated only if recommendation_id was set) ---
    pct5_outcome_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct50_outcome_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    pct95_outcome_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    prob_loss_10pct_hedged = models.DecimalField(max_digits=6, decimal_places=4, null=True)
    expected_shortfall_5pct_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    hedge_cost_total_usd = models.DecimalField(max_digits=12, decimal_places=2, null=True)
    # Upfront cost of the hedge strategy (option premium or friction)

    # --- Stress test results (populated only for stress_test runs) ---
    stress_loss_unhedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Total dollar loss if scenario shocks applied instantaneously
    stress_loss_unhedged_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    stress_loss_hedged_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    # Loss after applying the strategy overlay
    stress_loss_hedged_pct = models.DecimalField(max_digits=8, decimal_places=4, null=True)
    stress_per_position_json = models.TextField(blank=True)
    # JSON: per-symbol contribution to total stress loss
    # [{"symbol": "SOXX", "account": "TFSA_TOM", "loss_usd": -11500, "loss_pct": -0.302}, ...]

    # --- Full path distribution (for charting in M6) ---
    percentile_series_json = models.TextField(blank=True)
    # JSON: time-series of percentile bands for charting.
    # {"days": [0,21,63,126,252],
    #  "p5":  [100000, 95000, 88000, 83000, 79000],
    #  "p25": [100000, 98000, 96000, 95000, 94000],
    #  "p50": [100000, 101000, 103000, 105000, 107000],
    #  "p75": [100000, 104000, 110000, 116000, 122000],
    #  "p95": [100000, 108000, 119000, 131000, 144000]}
    # Sampled at 5 time points to keep storage manageable.

    calc_timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'rm_simulation_result'

    def __str__(self):
        return f"SimulationResult — {self.run} — {self.calc_timestamp}"


class TaxDecisionAcknowledgement(models.Model):
    """
    Records when a user has been presented with the tax impact of a
    strategy recommendation and acknowledged it.

    Write path triggered by M6-Spec-2 strategy_acknowledge view;
    table definition from M8-Spec-1.
    """
    recommendation = models.ForeignKey(
        StrategyRecommendation, on_delete=models.CASCADE, related_name='tax_acknowledgements'
    )
    presented_to_user = models.CharField(max_length=50, blank=True)
    # Owner who was presented the recommendation (TOM, SUSAN, etc.)
    presented_at = models.DateTimeField(null=True, blank=True)
    # Original calc_timestamp from StrategyRecommendation
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.CharField(max_length=100, blank=True)
    # Username or 'anonymous'
    tax_cost_estimate_presented = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    # Tax cost shown to user at acknowledgement time
    user_notes = models.TextField(blank=True)
    # Optional user-entered notes

    class Meta:
        db_table = 'rm_tax_decision_acknowledgement'

    def __str__(self):
        return f"TaxAck — {self.recommendation_id} — {self.acknowledged_by}"


class CustomStressScenario(models.Model):
    """
    User-defined instantaneous shock scenario for stress testing.
    """
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    owner = models.CharField(max_length=50, blank=True)
    # TOM, SUSAN, or blank (shared)
    shocks_json = models.TextField()
    # JSON: {symbol: shock_fraction}
    # E.g. {"SOXX": -0.30, "SMH": -0.28, "NVDA": -0.35, "DRAM": -0.32}
    # Positive values = price increase. Negative = price drop.
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'rm_custom_stress_scenario'
        ordering = ['-created_at']

    def __str__(self):
        return self.name


# =============================================================================
# M7-Spec-1: Alerting & Continuous Monitoring
# =============================================================================


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
    account_id = models.CharField(max_length=50, null=True, blank=True)
    symbol = models.CharField(max_length=20, null=True, blank=True)

    # --- Threshold ---
    threshold_value = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    threshold_operator = models.CharField(max_length=5, default='>')
    # '>' | '>=' | '<' | '<=' | '==' | '!='

    is_active = models.BooleanField(default=True)
    notify_email = models.EmailField(blank=True)
    description = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rm_alert_config'
        indexes = [
            models.Index(fields=['alert_type', 'is_active']),
            models.Index(fields=['owner', 'account_id', 'symbol']),
        ]

    def __str__(self):
        return f"{self.alert_type} — {self.owner or 'ALL'} / {self.account_id or 'ALL'} / {self.symbol or 'ALL'}"


class AlertHistory(models.Model):
    """
    Immutable record of a fired alert. Created by evaluate_alerts.
    The only mutable fields are acknowledged_at / acknowledged_by, set
    when a user dismisses the alert in the dashboard.
    """
    alert_config = models.ForeignKey(
        AlertConfig, on_delete=models.PROTECT, related_name='history'
    )
    alert_type = models.CharField(max_length=30)
    calc_date = models.DateField()
    triggered_at = models.DateTimeField(auto_now_add=True)

    entity_description = models.CharField(max_length=255)
    entity_type = models.CharField(max_length=20, blank=True)
    entity_id = models.CharField(max_length=100, blank=True)
    symbol = models.CharField(max_length=20, blank=True)

    trigger_value = models.CharField(max_length=50)
    previous_value = models.CharField(max_length=50, blank=True)

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

    def __str__(self):
        return f"{self.alert_type} — {self.entity_description} ({self.calc_date})"


# =============================================================================
# M8-Spec-1: Audit Trail & Explainability
# =============================================================================


class ImmutableModel(models.Model):
    """
    Abstract base model that enforces append-only behaviour.
    - save() is allowed (creates or updates no fields — subclasses override to
      set only new fields on creation).
    - delete() is blocked.
    Subclasses must set managed=True in Meta (default).
    """
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(max_length=100, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            # Prevent updates to existing records — append-only
            raise ImmutableRecordError(
                f"ImmutableModel '{self.__class__.__name__}' is append-only. "
                "Use add_audit_entry() to create new entries instead."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableRecordError(
            f"ImmutableModel '{self.__class__.__name__}' is append-only. "
            "Deletion is not permitted."
        )


class AuditEntry(ImmutableModel):
    """
    Immutable record of a risk calculation event.
    """
    calc_date = models.DateField()
    model_name = models.CharField(max_length=50)
    input_snapshot = models.JSONField(default=dict)
    output_summary = models.JSONField(default=dict)
    status = models.CharField(max_length=20, default='success')
    error_message = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'rm_audit_entry'
        indexes = [
            models.Index(fields=['calc_date', 'model_name']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.model_name} — {self.calc_date} ({self.status})"


class AuditRationale(ImmutableModel):
    """
    Immutable record of a recommendation rationale.
    """
    recommendation = models.ForeignKey(
        'StrategyRecommendation', on_delete=models.PROTECT, related_name='rationales'
    )
    calc_date = models.DateField()
    rationale_text = models.TextField()
    factors = models.JSONField(default=dict)

    class Meta:
        db_table = 'rm_audit_rationale'
        indexes = [
            models.Index(fields=['recommendation', 'calc_date']),
        ]

    def __str__(self):
        return f"Rationale for {self.recommendation} ({self.calc_date})"


class AuditActionLog(ImmutableModel):
    """
    Immutable record of a user action on a recommendation.
    """
    recommendation = models.ForeignKey(
        'StrategyRecommendation', on_delete=models.PROTECT, related_name='action_logs'
    )
    action_type = models.CharField(max_length=30)
    # 'actioned', 'deferred', 'rejected', 'tax_acknowledged'
    performed_by = models.CharField(max_length=100)
    reason = models.TextField(blank=True, default='')
    tax_acknowledged = models.BooleanField(default=False)

    class Meta:
        db_table = 'rm_audit_action_log'
        indexes = [
            models.Index(fields=['recommendation', 'action_type']),
            models.Index(fields=['performed_by']),
        ]

    def __str__(self):
        return f"{self.performed_by} → {self.action_type} on {self.recommendation}"


class AuditRetentionLog(ImmutableModel):
    """
    Immutable record of a retention check run.
    """
    check_date = models.DateField(auto_now_add=True)
    retention_days = models.IntegerField()
    records_reviewed = models.IntegerField(default=0)
    records_flagged = models.IntegerField(default=0)
    details = models.JSONField(default=dict)

    class Meta:
        db_table = 'rm_audit_retention_log'
        indexes = [
            models.Index(fields=['check_date']),
        ]

    def __str__(self):
        return f"Retention check — {self.check_date} (flagged={self.records_flagged})"


class AuditTaxLog(ImmutableModel):
    """
    Immutable record of a tax acknowledgement event.
    """
    recommendation = models.ForeignKey(
        'StrategyRecommendation', on_delete=models.PROTECT, related_name='tax_logs'
    )
    tax_year = models.CharField(max_length=10)
    estimated_tax_impact = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    acknowledged_by = models.CharField(max_length=100)
    acknowledgement_text = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'rm_audit_tax_log'
        indexes = [
            models.Index(fields=['recommendation', 'tax_year']),
            models.Index(fields=['acknowledged_by']),
        ]

    def __str__(self):
        return f"Tax ack — {self.recommendation} ({self.tax_year})"


# =============================================================================
# M8-Spec-1: Detailed Audit Models (Score Snapshot, Recommendation Log,
#            Simulation Run, Regime Log)
# =============================================================================


class AuditScoreSnapshot(ImmutableModel):
    """
    Immutable snapshot of an urgency score calculation with component breakdown.
    Written by calc_urgency command after each UrgencyScore UPSERT.
    Unique on (entity_type, entity_id, symbol, calc_date).
    """
    entity_type = models.CharField(max_length=30)
    # 'security_in_account' | 'account' | 'owner' | 'portfolio'
    entity_id = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20, blank=True)
    calc_date = models.DateField()
    urgency_level = models.CharField(max_length=20)
    score_raw = models.DecimalField(max_digits=10, decimal_places=2)

    # JSON fields preserving exact state at calculation time
    component_scores_json = models.JSONField(default=dict)
    # {m1: {score, weight}, driver: {score, weight}, regime: {score, weight}, market: {score, weight}}
    weights_at_calc_json = models.JSONField(default=dict)
    # {weight_m1_metrics, weight_security_driver, weight_regime, weight_market_condition,
    #  threshold_low, threshold_medium, threshold_high}
    input_metrics_json = models.JSONField(default=dict)
    # {var_parametric_95, current_drawdown_from_252d_peak, vol_252d, ...}

    class Meta:
        db_table = 'rm_audit_score_snapshot'
        unique_together = [('entity_type', 'entity_id', 'symbol', 'calc_date')]
        indexes = [
            models.Index(fields=['calc_date', 'entity_type']),
            models.Index(fields=['calc_date', 'urgency_level']),
            models.Index(fields=['entity_id', 'calc_date']),
        ]

    def __str__(self):
        return f"ScoreSnapshot — {self.entity_id} ({self.symbol}) — {self.calc_date}"


class AuditRecommendationLog(ImmutableModel):
    """
    Immutable log of every strategy template considered (eligible or rejected)
    for each (symbol, account, calc_date). Written by
    calc_strategy_recommendations command.
    """
    symbol = models.CharField(max_length=20)
    account_id = models.CharField(max_length=50)
    owner = models.CharField(max_length=50, blank=True)
    calc_date = models.DateField()

    # Input state at calculation time
    input_urgency_level = models.CharField(max_length=20)
    input_urgency_score = models.DecimalField(max_digits=10, decimal_places=2)
    input_regime_quadrant = models.CharField(max_length=4)

    # Template info
    strategy_template_code = models.CharField(max_length=50)
    strategy_template_name = models.CharField(max_length=200, blank=True)

    # Eligibility
    was_eligible = models.BooleanField()
    ineligibility_reason = models.CharField(max_length=50, blank=True, default='')
    # 'options_not_available', 'not_in_universe', 'insufficient_history',
    # 'no_instrument_links', 'inactive', 'tax_shelter_ineligible',
    # 'risk_limit_exceeded', 'concentration_limit', 'other'

    # Ranking (only for eligible)
    rank = models.IntegerField(null=True, blank=True)

    # Cost/VaR results (only for eligible and ranked)
    cost_result_json = models.JSONField(default=dict, blank=True)
    var_result_json = models.JSONField(default=dict, blank=True)
    cost_per_var_reduction_usd = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    # Selection rationale (plain English)
    selection_rationale = models.TextField(blank=True)

    # Constraints
    constraints_applied_json = models.JSONField(default=list, blank=True)

    # Tax
    is_tax_triggering = models.BooleanField(default=False)
    tax_cost_estimate_usd = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        db_table = 'rm_audit_recommendation_log'
        indexes = [
            models.Index(fields=['calc_date', 'symbol', 'account_id']),
            models.Index(fields=['calc_date', 'was_eligible']),
            models.Index(fields=['calc_date', 'strategy_template_code']),
            models.Index(fields=['symbol', 'account_id', 'calc_date']),
        ]

    def __str__(self):
        status = 'eligible' if self.was_eligible else f'rejected ({self.ineligibility_reason})'
        return f"RecLog — {self.symbol} ({self.account_id}) — {self.strategy_template_code} — {status}"


class AuditSimulationRun(ImmutableModel):
    """
    Immutable record of a simulation run (backtest, Monte Carlo, or stress test).
    Written after each simulation command completes.
    """
    simulation_family = models.CharField(max_length=30)
    # 'backtest' | 'montecarlo' | 'stress_test'
    source_run_id = models.IntegerField()
    # FK to the primary result table (BacktestRun.id / SimulationRun.id)

    symbol = models.CharField(max_length=20, blank=True)
    account_id = models.CharField(max_length=50, blank=True)
    entity_type = models.CharField(max_length=30, blank=True)
    entity_id = models.CharField(max_length=100, blank=True)

    strategy_template_code = models.CharField(max_length=50, blank=True)
    scenario_name = models.CharField(max_length=200, blank=True)

    parameters_json = models.JSONField(default=dict)
    summary_json = models.JSONField(default=dict)

    calc_date = models.DateField()
    run_timestamp = models.DateTimeField()

    class Meta:
        db_table = 'rm_audit_simulation_run'
        indexes = [
            models.Index(fields=['calc_date', 'simulation_family']),
            models.Index(fields=['simulation_family', 'source_run_id']),
            models.Index(fields=['symbol', 'calc_date']),
        ]

    def __str__(self):
        return f"SimRun — {self.simulation_family} — {self.symbol} ({self.account_id}) — {self.calc_date}"


class AuditRegimeLog(ImmutableModel):
    """
    Immutable record of regime state + market condition summary for each date.
    Written at the end of calc_market_conditions command.
    Unique on calc_date — re-runs for the same date are skipped (no error).
    """
    calc_date = models.DateField(unique=True)

    # From RegimeState
    quadrant = models.CharField(max_length=4)
    confidence_score = models.DecimalField(max_digits=10, decimal_places=2)
    transition_flag = models.BooleanField(default=False)
    indicator_snapshot_json = models.JSONField(default=dict)

    # Aggregated from MarketCondition for calc_date
    death_cross_count = models.IntegerField(default=0)
    golden_cross_count = models.IntegerField(default=0)
    oversold_count = models.IntegerField(default=0)
    overbought_count = models.IntegerField(default=0)
    bear_phase_symbols_json = models.JSONField(default=list)

    class Meta:
        db_table = 'rm_audit_regime_log'
        indexes = [
            models.Index(fields=['calc_date']),
            models.Index(fields=['quadrant', 'calc_date']),
        ]

    def __str__(self):
        return f"RegimeLog — {self.calc_date} ({self.quadrant})"
