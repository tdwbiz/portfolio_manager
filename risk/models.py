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
