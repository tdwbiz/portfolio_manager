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
    OwnerConfig,
    StrategyRecommendation,
    HistoricalScenario,
    BacktestRun,
    BacktestResult,
    SimulationRun,
    SimulationResult,
    CustomStressScenario,
    AlertConfig,
    AlertHistory,
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


@admin.register(OwnerConfig)
class OwnerConfigAdmin(admin.ModelAdmin):
    list_display = ['owner', 'marginal_tax_rate_ca', 'capital_gains_inclusion_rate',
                    'cost_tolerance_pct', 'updated_at', 'updated_by']
    fields = ['owner', 'marginal_tax_rate_ca', 'capital_gains_inclusion_rate',
              'cost_tolerance_pct', 'preferences_json', 'updated_by']
    # NOTE: marginal_tax_rate_ca and capital_gains_inclusion_rate are sensitive.
    # These are user-supplied; the system only displays and uses them.


@admin.register(StrategyRecommendation)
class StrategyRecommendationAdmin(admin.ModelAdmin):
    list_display = [
        'symbol', 'account_id', 'calc_date', 'rank',
        'strategy_template', 'urgency_level',
        'estimated_strategy_cost_usd', 'tax_cost_estimate_usd',
        'estimated_var_reduction_usd', 'cost_per_var_reduction_usd',
        'is_tax_triggering', 'status'
    ]
    list_filter = ['calc_date', 'urgency_level', 'status', 'is_tax_triggering', 'account_id']
    search_fields = ['symbol', 'account_id', 'tax_impact_note']
    ordering = ['-calc_date', 'symbol', 'rank']
    readonly_fields = [
        'symbol', 'account_id', 'owner', 'calc_date', 'strategy_template', 'rank',
        'urgency_level', 'urgency_score', 'regime_quadrant',
        'position_quantity', 'position_market_value_usd', 'position_unrealised_gain_usd',
        'cost_basis_per_unit_usd', 'current_price_usd', 'vol_63d',
        'estimated_strategy_cost_usd', 'estimated_strategy_cost_pct',
        'tax_cost_estimate_usd', 'tax_impact_note', 'is_tax_triggering',
        'estimated_var_reduction_usd', 'estimated_var_reduction_pct',
        'estimated_urgency_delta', 'cost_per_var_reduction_usd',
        'constraints_applied_json', 'calc_timestamp'
    ]
    # status, actioned_at, actioned_by are editable (user can mark as actioned)


@admin.register(HistoricalScenario)
class HistoricalScenarioAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'start_date', 'end_date', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'display_name']
    readonly_fields = ['created_at']
    fieldsets = [
        ('Identity', {'fields': ['name', 'display_name', 'is_active']}),
        ('Date Range', {'fields': ['start_date', 'end_date']}),
        ('Description', {'fields': ['description']}),
        ('Asset Class Returns', {'fields': ['asset_class_returns_json']}),
        ('Benchmark Returns', {
            'fields': [
                'benchmark_spy_return', 'benchmark_qqq_return', 'benchmark_vdy_return'
            ]
        }),
        ('Audit', {'fields': ['created_at']}),
    ]


@admin.register(BacktestRun)
class BacktestRunAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'account_id', 'strategy_template', 'scenario',
                    'start_date', 'end_date', 'run_timestamp']
    list_filter = ['run_timestamp', 'symbol', 'account_id']
    search_fields = ['symbol', 'account_id']
    readonly_fields = [
        'symbol', 'account_id', 'owner', 'strategy_template', 'scenario',
        'start_date', 'end_date', 'quantity', 'cost_basis_per_unit_usd',
        'start_price_usd', 'currency', 'strategy_params_json', 'run_timestamp', 'run_by'
    ]
    fieldsets = [
        ('Position', {'fields': ['symbol', 'account_id', 'owner', 'quantity',
                                 'cost_basis_per_unit_usd', 'start_price_usd', 'currency']}),
        ('Strategy & Scenario', {'fields': ['strategy_template', 'scenario']}),
        ('Date Range', {'fields': ['start_date', 'end_date']}),
        ('Strategy Params', {'fields': ['strategy_params_json']}),
        ('Audit', {'fields': ['run_timestamp', 'run_by']}),
    ]


@admin.register(BacktestResult)
class BacktestResultAdmin(admin.ModelAdmin):
    list_display = [
        'run_symbol', 'run_account_id', 'run_scenario',
        'total_return_unhedged_pct', 'total_return_hedged_pct',
        'hedge_effectiveness_ratio', 'calc_timestamp'
    ]
    list_filter = ['calc_timestamp']
    search_fields = ['run__symbol', 'run__account_id']
    readonly_fields = [
        'run', 'total_return_unhedged_pct', 'total_return_hedged_pct',
        'return_improvement_pct', 'max_drawdown_unhedged', 'max_drawdown_hedged',
        'drawdown_reduction_pct', 'hedge_cost_total_usd', 'hedge_cost_pct_of_position',
        'hedge_effectiveness_ratio', 'breakeven_annual_cost_pct',
        'stop_triggered', 'stop_trigger_date', 'stop_trigger_price_usd',
        'option_expired_worthless', 'option_payoff_usd',
        'position_value_start_usd', 'position_value_end_unhedged_usd',
        'position_value_end_hedged_usd', 'regime_split_json', 'calc_timestamp'
    ]
    fieldsets = [
        ('Run', {'fields': ['run']}),
        ('Returns', {'fields': [
            'total_return_unhedged_pct', 'total_return_hedged_pct', 'return_improvement_pct'
        ]}),
        ('Drawdown', {'fields': [
            'max_drawdown_unhedged', 'max_drawdown_hedged', 'drawdown_reduction_pct'
        ]}),
        ('Hedge Cost', {'fields': [
            'hedge_cost_total_usd', 'hedge_cost_pct_of_position',
            'hedge_effectiveness_ratio', 'breakeven_annual_cost_pct'
        ]}),
        ('Strategy Outcomes', {'fields': [
            'stop_triggered', 'stop_trigger_date', 'stop_trigger_price_usd',
            'option_expired_worthless', 'option_payoff_usd'
        ]}),
        ('Position Values', {'fields': [
            'position_value_start_usd', 'position_value_end_unhedged_usd',
            'position_value_end_hedged_usd'
        ]}),
        ('Regime Split', {'fields': ['regime_split_json']}),
        ('Audit', {'fields': ['calc_timestamp']}),
    ]

    def run_symbol(self, obj):
        return obj.run.symbol
    run_symbol.short_description = 'Symbol'
    run_symbol.admin_order_field = 'run__symbol'

    def run_account_id(self, obj):
        return obj.run.account_id
    run_account_id.short_description = 'Account'
    run_account_id.admin_order_field = 'run__account_id'

    def run_scenario(self, obj):
        return obj.run.scenario.display_name if obj.run.scenario else None
    run_scenario.short_description = 'Scenario'
    run_scenario.admin_order_field = 'run__scenario__display_name'


@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'simulation_type', 'entity_type', 'entity_id',
        'horizon_days', 'n_paths', 'calc_date', 'run_timestamp',
        'duration_seconds',
    ]
    list_filter = ['simulation_type', 'entity_type', 'use_jump_diffusion', 'calc_date']
    search_fields = ['entity_id', 'scenario_name']
    readonly_fields = [
        'run_timestamp', 'duration_seconds',
    ]
    fieldsets = [
        ('Simulation Type', {'fields': [
            'simulation_type', 'entity_type', 'entity_id', 'scenario_name'
        ]}),
        ('Parameters', {'fields': [
            'n_paths', 'horizon_days', 'use_jump_diffusion', 'random_seed',
            'recommendation_id', 'custom_shocks_json'
        ]}),
        ('Audit', {'fields': [
            'calc_date', 'run_timestamp', 'run_by', 'duration_seconds'
        ]}),
    ]


@admin.register(SimulationResult)
class SimulationResultAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'run', 'portfolio_value_start_usd',
        'pct50_outcome_usd', 'pct5_outcome_usd', 'pct95_outcome_usd',
        'prob_loss_10pct', 'expected_shortfall_5pct_usd',
    ]
    list_filter = ['run__simulation_type', 'run__calc_date']
    search_fields = ['run__entity_id']
    readonly_fields = [
        'pct5_outcome_usd', 'pct25_outcome_usd', 'pct50_outcome_usd',
        'pct75_outcome_usd', 'pct95_outcome_usd',
        'prob_loss_5pct', 'prob_loss_10pct', 'prob_loss_20pct', 'prob_loss_50pct',
        'expected_shortfall_5pct_usd', 'expected_shortfall_pct',
        'percentile_series_json',
        'pct5_outcome_hedged_usd', 'pct50_outcome_hedged_usd', 'pct95_outcome_hedged_usd',
        'prob_loss_10pct_hedged', 'expected_shortfall_5pct_hedged_usd',
        'hedge_cost_total_usd',
        'stress_loss_unhedged_usd', 'stress_loss_unhedged_pct',
        'stress_loss_hedged_usd', 'stress_loss_hedged_pct',
        'stress_per_position_json',
    ]
    fieldsets = [
        ('Run', {'fields': ['run']}),
        ('Portfolio Value', {'fields': ['portfolio_value_start_usd']}),
        ('Unhedged Percentiles', {'fields': [
            'pct5_outcome_usd', 'pct25_outcome_usd', 'pct50_outcome_usd',
            'pct75_outcome_usd', 'pct95_outcome_usd'
        ]}),
        ('Loss Probabilities', {'fields': [
            'prob_loss_5pct', 'prob_loss_10pct', 'prob_loss_20pct', 'prob_loss_50pct'
        ]}),
        ('Expected Shortfall', {'fields': [
            'expected_shortfall_5pct_usd', 'expected_shortfall_pct'
        ]}),
        ('Percentile Series', {'fields': ['percentile_series_json']}),
        ('Hedged Outcomes', {'fields': [
            'pct5_outcome_hedged_usd', 'pct50_outcome_hedged_usd', 'pct95_outcome_hedged_usd',
            'prob_loss_10pct_hedged', 'expected_shortfall_5pct_hedged_usd',
            'hedge_cost_total_usd'
        ]}),
        ('Stress Test Results', {'fields': [
            'stress_loss_unhedged_usd', 'stress_loss_unhedged_pct',
            'stress_loss_hedged_usd', 'stress_loss_hedged_pct',
            'stress_per_position_json'
        ]}),
    ]


@admin.register(CustomStressScenario)
class CustomStressScenarioAdmin(admin.ModelAdmin):
    list_display = ['name', 'owner', 'is_active', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'owner']
    readonly_fields = ['created_at']
    fieldsets = [
        ('Scenario', {'fields': ['name', 'description', 'owner', 'is_active']}),
        ('Shocks', {'fields': ['shocks_json']}),
        ('Audit', {'fields': ['created_at']}),
    ]


# =============================================================================
# M7-Spec-1: Alerting Admin
# =============================================================================


@admin.register(AlertConfig)
class AlertConfigAdmin(admin.ModelAdmin):
    list_display = ['alert_type', 'owner', 'account_id', 'symbol', 'is_active', 'threshold_value']
    list_filter = ['alert_type', 'is_active']
    search_fields = ['alert_type', 'owner', 'account_id', 'symbol']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        ('Alert Type', {'fields': ['alert_type', 'is_active', 'threshold_operator', 'threshold_value']}),
        ('Scope', {'fields': ['owner', 'account_id', 'symbol']}),
        ('Notification', {'fields': ['notify_email', 'description']}),
        ('Audit', {'fields': ['created_at', 'updated_at']}),
    ]


@admin.register(AlertHistory)
class AlertHistoryAdmin(admin.ModelAdmin):
    list_display = ['alert_type', 'entity_description', 'calc_date', 'trigger_value', 'acknowledged_at']
    list_filter = ['alert_type', 'calc_date', 'acknowledged_at']
    search_fields = ['entity_description', 'symbol', 'entity_id']
    readonly_fields = [
        'alert_config', 'alert_type', 'calc_date', 'triggered_at',
        'entity_description', 'entity_type', 'entity_id', 'symbol',
        'trigger_value', 'previous_value', 'acknowledged_at', 'acknowledged_by',
    ]
    fieldsets = [
        ('Alert', {'fields': ['alert_config', 'alert_type', 'calc_date', 'triggered_at']}),
        ('Entity', {'fields': ['entity_description', 'entity_type', 'entity_id', 'symbol']}),
        ('Values', {'fields': ['trigger_value', 'previous_value']}),
        ('Acknowledgement', {'fields': ['acknowledged_at', 'acknowledged_by']}),
    ]
