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
    DataQualityLog,
    SecurityRiskMetrics,
    CorrelationMatrix,
    PortfolioRiskMetrics,
    RegimeState,
    MarketCondition,
    RegimeSensitivity,
    SecurityDriver,
    UrgencyScore,
    InstrumentType,
    StrategyTemplate,
    StrategyInstrumentLink,
)


class StrategyInstrumentLinkInline(admin.TabularInline):
    model = StrategyInstrumentLink
    extra = 1
    fields = ['instrument', 'role', 'sort_order']


@admin.register(InstrumentType)
class InstrumentTypeAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'name',
        'eligible_tfsa', 'eligible_rrsp', 'eligible_lif', 'eligible_rrif', 'eligible_taxable',
        'requires_options_availability', 'wash_sale_applicable', 'is_active'
    ]
    list_filter = [
        'eligible_tfsa', 'eligible_taxable',
        'requires_options_availability', 'wash_sale_applicable', 'is_active'
    ]
    search_fields = ['code', 'name']
    ordering = ['sort_order', 'name']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        ('Identity', {'fields': ['code', 'name', 'sort_order', 'is_active']}),
        ('Description', {'fields': ['mechanics_description', 'use_cases_json']}),
        ('Cost & Tax', {'fields': ['cost_structure_json', 'tax_treatment_json']}),
        ('Eligibility', {'fields': [
            'eligible_tfsa', 'eligible_rrsp', 'eligible_lif', 'eligible_rrif', 'eligible_taxable',
            'requires_options_availability', 'wash_sale_applicable'
        ]}),
        ('Metadata', {'fields': ['created_at', 'updated_at'], 'classes': ['collapse']}),
    ]


@admin.register(StrategyTemplate)
class StrategyTemplateAdmin(admin.ModelAdmin):
    list_display = [
        'code', 'name',
        'applicable_urgency_levels', 'applicable_regime_quadrants',
        'requires_options', 'cef_eligible', 'lif_rrif_eligible',
        'is_tax_triggering', 'is_active'
    ]
    list_filter = [
        'applicable_urgency_levels', 'applicable_regime_quadrants',
        'requires_options', 'cef_eligible', 'lif_rrif_eligible',
        'is_tax_triggering', 'is_active'
    ]
    search_fields = ['code', 'name', 'description']
    ordering = ['sort_order', 'name']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [StrategyInstrumentLinkInline]
    fieldsets = [
        ('Identity', {'fields': ['code', 'name', 'sort_order', 'is_active']}),
        ('Description', {'fields': ['description']}),
        ('Applicability Tags', {'fields': [
            'applicable_urgency_levels', 'applicable_regime_quadrants',
            'applicable_asset_types', 'applicable_account_categories',
            'min_unrealised_gain_usd'
        ]}),
        ('Constraints', {'fields': [
            'requires_options', 'cef_eligible', 'lif_rrif_eligible', 'is_tax_triggering'
        ]}),
        ('Metadata', {'fields': ['created_at', 'updated_at'], 'classes': ['collapse']}),
    ]


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


@admin.register(DataQualityLog)
class DataQualityLogAdmin(admin.ModelAdmin):
    list_display = ("symbol", "event_type", "severity", "logged_at", "resolved")
    list_filter = ("severity", "resolved", "event_type")
    search_fields = ("symbol", "description")
    actions = ["mark_resolved"]

    def mark_resolved(self, request, queryset):
        queryset.update(resolved=True, resolved_at=admin.utils.now())
    mark_resolved.short_description = "Mark selected as resolved"


@admin.register(SecurityRiskMetrics)
class SecurityRiskMetricsAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'calc_date', 'vol_21d', 'var_parametric_95', 
                    'beta_spy', 'sharpe_ratio', 'liquidity_score', 'calc_timestamp']
    list_filter = ['calc_date', 'symbol']
    search_fields = ['symbol']
    ordering = ['-calc_date', 'symbol']
    readonly_fields = [f.name for f in SecurityRiskMetrics._meta.fields]  # Never edited manually


@admin.register(CorrelationMatrix)
class CorrelationMatrixAdmin(admin.ModelAdmin):
    list_display = ['symbol_a', 'symbol_b', 'calc_date', 'pearson_252d', 'price_rows_overlap']
    list_filter = ['calc_date']
    search_fields = ['symbol_a', 'symbol_b']
    ordering = ['-calc_date', 'symbol_a', 'symbol_b']
    readonly_fields = [f.name for f in CorrelationMatrix._meta.fields]


@admin.register(PortfolioRiskMetrics)
class PortfolioRiskMetricsAdmin(admin.ModelAdmin):
    list_display = [
        'entity_type', 'entity_id', 'calc_date',
        'var_portfolio_95_usd', 'var_portfolio_95_pct',
        'diversification_ratio', 'hhi_symbol',
        'effective_sp500_weight', 'portfolio_beta_spy',
        'tax_risk_flag', 'total_market_value_usd'
    ]
    list_filter = ['calc_date', 'entity_type', 'tax_risk_flag']
    search_fields = ['entity_id']
    ordering = ['-calc_date', 'entity_type', 'entity_id']
    readonly_fields = [f.name for f in PortfolioRiskMetrics._meta.fields]


@admin.register(RegimeState)
class RegimeStateAdmin(admin.ModelAdmin):
    list_display = [
        'calc_date', 'quadrant', 'confidence_score', 'transition_flag',
        'prob_q1', 'prob_q2', 'prob_q3', 'prob_q4',
        'yield_curve_score', 'vix_score'
    ]
    list_filter = ['quadrant', 'transition_flag']
    ordering = ['-calc_date']
    readonly_fields = [f.name for f in RegimeState._meta.fields]


@admin.register(MarketCondition)
class MarketConditionAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'calc_date', 'market_phase',
        'rsi_14', 'crossover_type', 'macd_histogram',
        'bb_pct_b', 'obv_trend', 'pct_from_52w_high'
    ]
    list_filter = ['calc_date', 'market_phase', 'crossover_type', 'symbol']
    search_fields = ['symbol']
    ordering = ['-calc_date', 'symbol']
    readonly_fields = [f.name for f in MarketCondition._meta.fields]


@admin.register(RegimeSensitivity)
class RegimeSensitivityAdmin(admin.ModelAdmin):
    list_display = ['asset_class', 'regime_quadrant', 'sensitivity_multiplier', 'rationale']
    list_filter = ['regime_quadrant']
    ordering = ['asset_class', 'regime_quadrant']


@admin.register(UrgencyScore)
class UrgencyScoreAdmin(admin.ModelAdmin):
    list_display = [
        'entity_type', 'entity_id', 'symbol', 'calc_date',
        'urgency_level', 'score_raw',
        'component_m1_score', 'component_driver_score',
        'component_regime_score', 'component_market_score',
        'dominant_component'
    ]
    list_filter = ['calc_date', 'entity_type', 'urgency_level', 'dominant_component']
    search_fields = ['entity_id', 'symbol']
    ordering = ['-calc_date', '-score_raw']
    readonly_fields = [f.name for f in UrgencyScore._meta.fields]


@admin.register(SecurityDriver)
class SecurityDriverAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'asset_class_baseline', 'sector_cyclicality',
        'liquidity_tier', 'earnings_proximity_flag', 'updated_at'
    ]
    list_filter = ['earnings_proximity_flag']
    search_fields = ['symbol']
    ordering = ['symbol']
    # Editable — developer updates these as portfolio changes
