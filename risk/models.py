from django.db import models
from django.core.exceptions import ValidationError
from decimal import Decimal
from core.models import Account


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
