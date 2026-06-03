from django.contrib import admin
from .models import (
    SecurityUniverse,
    MarketPriceHistory,
    VolatilitySummary,
    FxRate,
    EconomicIndicator,
    AlternativePortfolio,
    AlternativeHolding,
    ScoringConfig,
    SecurityIndexExposure,
)


class AlternativeHoldingInline(admin.TabularInline):
    model = AlternativeHolding
    extra = 1


@admin.register(SecurityUniverse)
class SecurityUniverseAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "asset_class", "is_held", "is_benchmark", "is_active")
    list_filter = ("is_held", "is_benchmark", "is_hedge", "is_cef", "asset_class")
    search_fields = ("symbol", "name")


@admin.register(MarketPriceHistory)
class MarketPriceHistoryAdmin(admin.ModelAdmin):
    list_display = ("symbol", "date", "close", "currency", "data_source")
    list_filter = ("currency", "data_source")
    search_fields = ("symbol",)


@admin.register(VolatilitySummary)
class VolatilitySummaryAdmin(admin.ModelAdmin):
    list_display = ("symbol", "calc_date", "lookback_days", "volatility_annualised")
    list_filter = ("lookback_days", "data_source")
    search_fields = ("symbol",)


@admin.register(FxRate)
class FxRateAdmin(admin.ModelAdmin):
    list_display = ("date", "usd_cad_rate", "data_source")
    search_fields = ("date",)


@admin.register(EconomicIndicator)
class EconomicIndicatorAdmin(admin.ModelAdmin):
    list_display = ("indicator_name", "date", "value", "data_source")
    list_filter = ("indicator_name", "data_source")
    search_fields = ("indicator_name",)


@admin.register(AlternativePortfolio)
class AlternativePortfolioAdmin(admin.ModelAdmin):
    list_display = ("owner", "account", "name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "owner")
    inlines = [AlternativeHoldingInline]


@admin.register(ScoringConfig)
class ScoringConfigAdmin(admin.ModelAdmin):
    list_display = ("config_key", "config_value", "updated_at")
    search_fields = ("config_key",)


@admin.register(SecurityIndexExposure)
class SecurityIndexExposureAdmin(admin.ModelAdmin):
    list_display = ("symbol", "index_name", "exposure_weight")
    list_filter = ("index_name",)
    search_fields = ("symbol",)
