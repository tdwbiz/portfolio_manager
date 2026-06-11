"""
Management command: calc_strategy_recommendations

Produces ranked strategy recommendations for each position at risk.
Reads urgency scores, risk metrics, regime state, and owner configs,
then computes cost estimates, VaR reduction estimates, and ranking
metrics for each eligible strategy template.

Usage:
    python manage.py calc_strategy_recommendations [--date YYYY-MM-DD]
        [--account ACC] [--symbol SYM] [--min-urgency Medium]

Dependencies:
    - M3 instrument catalogue seeded (rm_instrument_type, rm_strategy_template)
    - M2 urgency scores available (rm_urgency_score)
    - M1 security metrics available (rm_security_risk_metrics)
    - Regime state available (rm_regime_state)
    - FX rates available (rm_fx_rates)
    - Owner configs available (rm_owner_config)
    - Scoring config available (rm_scoring_config)
"""

import json
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from risk.models import (
    OwnerConfig,
    StrategyRecommendation,
    StrategyTemplate,
    UrgencyScore,
)
from risk.services.audit_hooks import write_audit_entry, write_audit_recommendation_log
from risk.services.catalogue import get_eligible_templates
from risk.services.option_pricing import estimate_option_cost
from risk.services.strategy_costs import compute_ranking_metric, estimate_strategy_cost
from risk.services.var_reduction import estimate_var_reduction


# Urgency level ordering for --min-urgency filtering
URGENCY_ORDER = {
    'Low': 0,
    'Medium': 1,
    'High': 2,
    'Critical': 3,
}

MIN_URGENCY_DEFAULT = 'Medium'


class Command(BaseCommand):
    help = (
        'Calculate and store ranked strategy recommendations for all positions at risk.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Calculation date in YYYY-MM-DD format. Defaults to today.',
        )
        parser.add_argument(
            '--account',
            type=str,
            help='Filter by account ID (e.g. IB_SUSAN).',
        )
        parser.add_argument(
            '--symbol',
            type=str,
            help='Filter by symbol (e.g. AAPL).',
        )
        parser.add_argument(
            '--min-urgency',
            type=str,
            choices=['Low', 'Medium', 'High', 'Critical'],
            default=MIN_URGENCY_DEFAULT,
            help='Only process positions at this urgency level or above.',
        )

    def handle(self, *args, **options):
        calc_date_str = options.get('date')
        if calc_date_str:
            try:
                calc_date = date.fromisoformat(calc_date_str)
            except ValueError:
                self.stderr.write(
                    self.style.ERROR(
                        f"Invalid date format: {calc_date_str}. Use YYYY-MM-DD."
                    )
                )
                return
        else:
            calc_date = timezone.now().date()

        account_filter = options.get('account')
        symbol_filter = options.get('symbol')
        min_urgency = options.get('min_urgency')

        self.stdout.write(f"Calculating strategy recommendations for date: {calc_date}")
        self.stdout.write(f"  Account filter: {account_filter or '(all)'}")
        self.stdout.write(f"  Symbol filter: {symbol_filter or '(all)'}")
        self.stdout.write(f"  Min urgency: {min_urgency}")

        # Load owner configs
        owner_configs = {oc.owner: oc for oc in OwnerConfig.objects.all()}
        if not owner_configs:
            self.stderr.write(
                self.style.ERROR(
                    "No OwnerConfig records found. "
                    "Run `python manage.py seed_owner_config` first."
                )
            )
            return
        self.stdout.write(f"  Loaded {len(owner_configs)} owner config(s): {', '.join(owner_configs.keys())}")

        # Load scoring config for tax_penalty_multiplier
        from risk.models import ScoringConfig
        scoring_config = {}
        for sc in ScoringConfig.objects.all():
            try:
                scoring_config[sc.config_key] = float(sc.config_value)
            except (ValueError, TypeError):
                scoring_config[sc.config_key] = sc.config_value
        tax_penalty_multiplier = scoring_config.get('tax_penalty_multiplier', 1.0)
        self.stdout.write(f"  Tax penalty multiplier: {tax_penalty_multiplier}")

        # Load FX rates for calc_date (or most recent)
        from risk.models import FxRate
        fx_rate_obj = FxRate.objects.filter(date__lte=calc_date).order_by('-date').first()
        fx_rate = float(fx_rate_obj.usd_cad_rate) if fx_rate_obj else 1.384  # fallback
        self.stdout.write(f"  FX rate (USD/CAD): {fx_rate}")

        # Load risk-free rate from economic indicators (SGOV yield)
        from risk.models import EconomicIndicator
        sgov_obj = EconomicIndicator.objects.filter(
            indicator_name='SGOV Yield', date__lte=calc_date
        ).order_by('-date').first()
        risk_free_rate = float(sgov_obj.value) if sgov_obj else 0.045  # fallback 4.5%
        self.stdout.write(f"  Risk-free rate (SGOV): {risk_free_rate}")

        # Load urgency scores for calc_date
        urgency_qs = UrgencyScore.objects.filter(
            calc_date=calc_date,
            entity_type='security_in_account',
        )
        if min_urgency:
            min_level = URGENCY_ORDER.get(min_urgency, 1)
            urgency_qs = urgency_qs.filter(
                urgency_level__in=[
                    lvl for lvl, order in URGENCY_ORDER.items() if order >= min_level
                ]
            )

        # Load accounts (exclude those with exclude_from_risk=True)
        from core.models import Account
        accounts = Account.objects.filter(exclude_from_risk=False)
        if account_filter:
            accounts = accounts.filter(account_id=account_filter)

        # Build position context: latest position per (account, symbol)
        positions = {}
        for acc in accounts:
            latest = acc.position_history.filter(date_to__isnull=True).order_by('-date_from')
            for ph in latest:
                key = (acc.account_id, ph.symbol)
                positions[key] = {
                    'account_id': acc.account_id,
                    'account_category': acc.category,
                    'owner': acc.owner,
                    'symbol': ph.symbol,
                    'quantity': float(ph.quantity),
                    'cost_basis_per_unit': float(ph.cost_basis_per_unit),
                    'currency': ph.currency,
                }

        self.stdout.write(f"  Positions found: {len(positions)}")

        # Filter positions by urgency scores
        urgency_map = {}
        for us in urgency_qs:
            key = us.entity_id  # format: "SYMBOL|ACCOUNT_ID"
            urgency_map[key] = us

        # Filter positions to those with urgency >= min
        eligible_positions = []
        for (account_id, symbol), pos in positions.items():
            urgency_key = f"{symbol}|{account_id}"
            us = urgency_map.get(urgency_key)
            if not us:
                continue
            if us.urgency_level not in URGENCY_ORDER or URGENCY_ORDER[us.urgency_level] < URGENCY_ORDER.get(min_urgency, 1):
                continue
            eligible_positions.append((account_id, symbol, pos, us))

        self.stdout.write(f"  Eligible positions (urgency >= {min_urgency}): {len(eligible_positions)}")

        # Load security metrics
        from risk.models import SecurityRiskMetrics, VolatilitySummary
        security_metrics_map = {}
        for sm in SecurityRiskMetrics.objects.filter(calc_date=calc_date):
            security_metrics_map[sm.symbol] = {
                'vol_63d': float(sm.vol_21d) if sm.vol_21d else 0.30,
                'var_parametric_95': float(sm.var_parametric_95) if sm.var_parametric_95 else 0.03,
                'beta_spy': float(sm.beta_spy) if sm.beta_spy else 1.0,
            }

        # Current regime from rm_regime_state
        from risk.models import RegimeState
        regime = RegimeState.objects.filter(calc_date=calc_date).order_by('-calc_date').first()
        regime_quadrant = regime.quadrant if regime else 'Q1'
        self.stdout.write(f"  Current regime quadrant: {regime_quadrant}")

        # Process each position
        total_recommendations = 0
        positions_processed = 0
        positions_skipped = []

        for account_id, symbol, pos, us in eligible_positions:
            # Get current price from security metrics or fallback
            sm = security_metrics_map.get(symbol, {})
            vol_63d = sm.get('vol_63d', 0.30)
            var_95 = sm.get('var_parametric_95', 0.03)
            beta_spy = sm.get('beta_spy', 1.0)

            # Get current price from MarketPriceHistory
            from risk.models import MarketPriceHistory
            price_obj = MarketPriceHistory.objects.filter(
                symbol=symbol, date=calc_date
            ).order_by('-date').first()
            current_price = float(price_obj.close) if price_obj else 100.0

            # Get cost basis
            cost_basis = pos['cost_basis_per_unit']
            quantity = pos['quantity']
            position_market_value = current_price * quantity
            total_cost_basis = cost_basis * quantity
            unrealised_gain = position_market_value - total_cost_basis

            # Owner config for this position
            owner = pos['owner']
            owner_config = owner_configs.get(owner)
            if not owner_config:
                positions_skipped.append(f"{symbol} ({account_id}): no owner config for {owner}")
                continue

            # Build position dict for service functions
            position_dict = {
                'symbol': symbol,
                'quantity': quantity,
                'current_price_usd': current_price,
                'cost_basis_per_unit_usd': cost_basis,
                'unrealised_gain_usd': unrealised_gain,
                'account_category': pos['account_category'],
                'owner': owner,
                'currency': pos['currency'],
            }

            # Determine position characteristics
            from risk.models import SecurityUniverse
            su = SecurityUniverse.objects.filter(symbol=symbol).first()
            asset_class = su.asset_class if su else 'common_stock'
            is_cef = su.is_cef if su else False
            options_available = su.options_available if su else True
            is_lif_or_rrif = pos['account_category'] in ('LIF', 'RRIF')

            # Get eligible templates and rejected templates with reasons
            eligible_templates, rejected_templates = get_all_templates_with_reasons(
                asset_class=asset_class,
                account_category=pos['account_category'],
                urgency_level=us.urgency_level,
                regime_quadrant=regime_quadrant,
                options_available=options_available,
                is_cef=is_cef,
                is_lif_or_rrif=is_lif_or_rrif,
                unrealised_gain_usd=max(0, unrealised_gain),
            )

            if not eligible_templates:
                positions_skipped.append(f"{symbol} ({account_id}): no eligible templates")
                # Still audit rejected templates
                for template, reason in rejected_templates:
                    try:
                        write_audit_recommendation_log(
                            entity_type='security_in_account',
                            entity_id=f"{account_id}::{symbol}",
                            symbol=symbol,
                            calc_date=calc_date,
                            strategy_code=template.code,
                            strategy_name=template.name,
                            urgency_level=us.urgency_level,
                            urgency_score=us.score_raw,
                            rationale='rejected',
                            ineligibility_reason=reason,
                            estimated_strategy_cost_usd=0,
                            estimated_var_reduction_usd=0,
                            is_tax_triggering=False,
                            tax_cost_estimate_usd=0,
                        )
                    except Exception:
                        pass
                continue

            # Build constraints list
            constraints = []
            if not options_available:
                constraints.append('options_not_available: removed protective_put, collar, covered_call')
            if is_cef:
                constraints.append('cef: removed all options templates')
            if is_lif_or_rrif:
                constraints.append('lif_restriction: removed partial_liquidation')

            # Score each template
            candidates = []
            for template in templates:
                # Cost estimation
                cost_result = estimate_strategy_cost(
                    strategy_template=template,
                    position=position_dict,
                    security_metrics={'vol_63d': vol_63d, 'var_parametric_95': var_95, 'beta_spy': beta_spy},
                    risk_free_rate=risk_free_rate,
                    fx_rate=fx_rate,
                    owner_config=owner_config,
                    scoring_config=scoring_config,
                )

                # VaR reduction estimation
                var_result = estimate_var_reduction(
                    strategy_code=template.code,
                    position_market_value_usd=position_market_value,
                    var_parametric_95=var_95,
                    beta_spy=beta_spy,
                    current_price_usd=current_price,
                )

                # Ranking metric
                is_taxable = pos['account_category'] in ('Investment', 'Investment Joint')
                cost_per_var = compute_ranking_metric(
                    strategy_cost_usd=cost_result['strategy_cost_usd'],
                    tax_cost_usd=cost_result['tax_cost_usd'],
                    var_reduction_usd=var_result['var_reduction_usd'],
                    is_taxable_account=is_taxable,
                    tax_penalty_multiplier=tax_penalty_multiplier,
                )

                # Cost as percentage of position
                cost_pct = cost_result['strategy_cost_usd'] / position_market_value if position_market_value > 0 else 0

                candidates.append({
                    'template': template,
                    'cost_result': cost_result,
                    'var_result': var_result,
                    'cost_per_var': cost_per_var,
                    'cost_pct': cost_pct,
                })

            # Sort by cost_per_var ascending (best first)
            candidates.sort(key=lambda c: c['cost_per_var'])

            # Mark previous recommendations as superseded
            with transaction.atomic():
                StrategyRecommendation.objects.filter(
                    symbol=symbol,
                    account_id=account_id,
                    calc_date=calc_date,
                    status='active',
                ).update(status='superseded')

                # Insert new recommendations
                for rank_idx, candidate in enumerate(candidates, start=1):
                    cr = candidate['cost_result']
                    vr = candidate['var_result']
                    template = candidate['template']

                    StrategyRecommendation.objects.create(
                        symbol=symbol,
                        account_id=account_id,
                        owner=owner,
                        calc_date=calc_date,
                        strategy_template=template,
                        rank=rank_idx,
                        urgency_level=us.urgency_level,
                        urgency_score=us.score_raw,
                        regime_quadrant=regime_quadrant,
                        position_quantity=quantity,
                        position_market_value_usd=position_market_value,
                        position_unrealised_gain_usd=unrealised_gain,
                        cost_basis_per_unit_usd=cost_basis,
                        current_price_usd=current_price,
                        vol_63d=vol_63d,
                        estimated_strategy_cost_usd=cr['strategy_cost_usd'],
                        estimated_strategy_cost_pct=cost_pct,
                        tax_cost_estimate_usd=cr['tax_cost_usd'],
                        tax_impact_note=cr['tax_impact_note'],
                        is_tax_triggering=cr['is_tax_triggering'],
                        estimated_var_reduction_usd=vr['var_reduction_usd'],
                        estimated_var_reduction_pct=vr['var_reduction_pct_of_mv'],
                        estimated_urgency_delta=None,  # Will be updated by M5 backtesting
                        cost_per_var_reduction_usd=candidate['cost_per_var'],
                        constraints_applied_json=json.dumps(constraints),
                        status='active',
                    )
                    total_recommendations += 1

                    # Audit hook: write recommendation log
                    try:
                        write_audit_recommendation_log(
                            entity_type='security_in_account',
                            entity_id=f"{account_id}::{symbol}",
                            symbol=symbol,
                            calc_date=calc_date,
                            strategy_code=template.code,
                            strategy_name=template.name,
                            urgency_level=us.urgency_level,
                            urgency_score=us.score_raw,
                            rationale='eligible',
                            ineligibility_reason=None,
                            estimated_strategy_cost_usd=cr['strategy_cost_usd'],
                            estimated_var_reduction_usd=vr['var_reduction_usd'],
                            is_tax_triggering=cr['is_tax_triggering'],
                            tax_cost_estimate_usd=cr['tax_cost_usd'],
                        )
                    except Exception:
                        pass

                # Audit rejected templates
                for template, reason in rejected_templates:
                    try:
                        write_audit_recommendation_log(
                            entity_type='security_in_account',
                            entity_id=f"{account_id}::{symbol}",
                            symbol=symbol,
                            calc_date=calc_date,
                            strategy_code=template.code,
                            strategy_name=template.name,
                            urgency_level=us.urgency_level,
                            urgency_score=us.score_raw,
                            rationale='rejected',
                            ineligibility_reason=reason,
                            estimated_strategy_cost_usd=0,
                            estimated_var_reduction_usd=0,
                            is_tax_triggering=False,
                            tax_cost_estimate_usd=0,
                        )
                    except Exception:
                        pass

            positions_processed += 1

        # Summary
        self.stdout.write(self.style.SUCCESS(
            f"Strategy recommendation calculation complete for {calc_date}"
        ))
        self.stdout.write(f"  Positions processed: {positions_processed}")
        self.stdout.write(f"  Total recommendations: {total_recommendations}")
        if positions_skipped:
            self.stdout.write(f"  Positions skipped: {len(positions_skipped)}")
            for ps in positions_skipped:
                self.stdout.write(f"    - {ps}")

        # Write AuditEntry
        try:
            write_audit_entry(
                model_name='strategy_recommendations',
                calc_date=calc_date,
                status='success',
                summary=f"Strategy recommendations calculated for {positions_processed} positions, "
                        f"{total_recommendations} recommendations generated",
                details_json={
                    'positions_processed': positions_processed,
                    'total_recommendations': total_recommendations,
                    'positions_skipped': positions_skipped,
                },
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Warning: audit entry write failed: {e}"))
