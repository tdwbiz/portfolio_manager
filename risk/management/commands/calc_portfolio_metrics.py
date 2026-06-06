"""
Management command to compute portfolio-level risk metrics.

Usage:
    python manage.py calc_portfolio_metrics [--date YYYY-MM-DD] [--entity-type account|owner|portfolio] [--entity-id ID]
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

import numpy as np

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from decimal import Decimal

from risk.models import (
    CorrelationMatrix,
    FxRate,
    PortfolioRiskMetrics,
    ScoringConfig,
    SecurityIndexExposure,
    SecurityRiskMetrics,
    SecurityUniverse,
)
from risk.services.portfolio_metrics import (
    build_covariance_matrix,
    build_position_weights,
    compute_tax_risk_flag,
    diversification_ratio,
    effective_index_weights,
    get_owner_positions,
    hhi,
    hhi_by_group,
    portfolio_return_series,
    portfolio_var_markowitz,
    portfolio_var_undiversified,
)

logger = logging.getLogger(__name__)

# Entity definitions
ACCOUNTS = [
    'RRSP_TOM', 'LIF_TOM', 'IB_TOM', 'TFSA_TOM',
    'RRIF_SUSAN', 'LIF_SUSAN', 'IB_SUSAN', 'TFSA_SUSAN',
    'TAXABLE_TOM_SUSAN', 'IB_JOINT', 'TAXABLE_CAR',
]
OWNERS = ['TOM', 'SUSAN']
JOINT_ACCOUNTS = ['TAXABLE_TOM_SUSAN', 'IB_JOINT']


class Command(BaseCommand):
    help = 'Compute aggregated portfolio risk metrics'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Calculation date (YYYY-MM-DD), default: today',
        )
        parser.add_argument(
            '--entity-type',
            type=str,
            choices=['account', 'owner', 'portfolio'],
            default=None,
            help='Restrict to one entity type',
        )
        parser.add_argument(
            '--entity-id',
            type=str,
            default=None,
            help='Restrict to one specific entity ID',
        )

    def handle(self, *args, **options):
        calc_date_str = options['date'] or date.today().isoformat()
        try:
            calc_date = date.fromisoformat(calc_date_str)
        except ValueError:
            raise CommandError(f"Invalid date format: {calc_date_str}")

        entity_type_filter = options['entity_type']
        entity_id_filter = options['entity_id']

        self.stdout.write(f"Computing portfolio metrics for date: {calc_date}")

        # 1. Load FX rate
        fx_rate = self._get_fx_rate(calc_date)
        self.stdout.write(f"FX rate (USD/CAD): {fx_rate}")

        # 2. Load scoring config
        joint_split = self._get_config_value('joint_account_owner_split', 0.5)
        tax_threshold = self._get_config_value('taxable_ugl_alert_usd', 100000)

        # 3. Load security metrics
        security_metrics_raw = SecurityRiskMetrics.objects.filter(calc_date=calc_date)
        security_metrics = {}
        for sm in security_metrics_raw:
            security_metrics[sm.symbol] = {
                'var_parametric_95': sm.var_parametric_95,
                'vol_252d': sm.vol_252d,
                'beta_spy': sm.beta_spy,
                'beta_xiu': sm.beta_xiu,
            }

        # 4. Load correlation matrix
        corr_raw = CorrelationMatrix.objects.filter(calc_date=calc_date)
        correlation_matrix = {}
        for cr in corr_raw:
            correlation_matrix[(cr.symbol_a, cr.symbol_b)] = float(cr.pearson_252d)

        # 5. Load index exposures
        index_exposure_raw = SecurityIndexExposure.objects.all()
        index_exposures = {}
        for ie in index_exposure_raw:
            if ie.symbol not in index_exposures:
                index_exposures[ie.symbol] = {}
            index_exposures[ie.symbol][ie.index_name] = float(ie.exposure_weight)

        # 6. Load positions from vw_current_portfolio
        positions = self._load_positions(calc_date)

        # 7. Compute for each entity type
        if entity_type_filter is None or entity_type_filter == 'account':
            self._compute_accounts(
                positions, security_metrics, correlation_matrix,
                index_exposures, fx_rate, calc_date, entity_id_filter, tax_threshold,
            )

        if entity_type_filter is None or entity_type_filter == 'owner':
            self._compute_owners(
                positions, security_metrics, correlation_matrix,
                index_exposures, fx_rate, calc_date, joint_split,
                entity_id_filter, tax_threshold,
            )

        if entity_type_filter is None or entity_type_filter == 'portfolio':
            self._compute_portfolio(
                positions, security_metrics, correlation_matrix,
                index_exposures, fx_rate, calc_date, entity_id_filter, tax_threshold,
            )

        self.stdout.write(self.style.SUCCESS("Portfolio metrics computation complete."))

    def _compute_entity_metrics(
        self,
        entity_type: str,
        entity_id: str,
        positions: list[dict],
        security_metrics: dict[str, dict],
        correlation_matrix: dict[tuple[str, str], float],
        index_exposures: dict[str, dict[str, float]],
        fx_rate: float,
        calc_date: date,
        tax_threshold: float,
    ):
        """
        Shared computation logic for account, owner, and portfolio entities.
        
        Builds weights, covariance matrix, computes all risk metrics,
        and UPSERTs into rm_portfolio_risk_metrics.
        """
        # Build weights
        weights, symbols, total_mv_usd = build_position_weights(
            positions, security_metrics, fx_rate
        )

        if len(symbols) == 0:
            return

        # Build covariance matrix
        cov_matrix = build_covariance_matrix(
            symbols, security_metrics, correlation_matrix
        )

        # Individual VaRs
        individual_vars_95 = np.array([
            float(security_metrics[s]['var_parametric_95'])
            for s in symbols
        ])

        # Portfolio VaR
        var_frac, var_usd = portfolio_var_markowitz(
            weights, cov_matrix, total_mv_usd, 0.95
        )
        _, var_99_usd = portfolio_var_markowitz(
            weights, cov_matrix, total_mv_usd, 0.99
        )
        var_pct = var_usd / total_mv_usd if total_mv_usd > 0 else 0.0

        # Undiversified VaR
        var_univ = portfolio_var_undiversified(
            weights, individual_vars_95, total_mv_usd
        )
        diversification_benefit = var_univ - var_usd

        # Portfolio volatility
        portfolio_variance = float(weights @ cov_matrix @ weights)
        portfolio_vol_annualised = float(np.sqrt(portfolio_variance))

        # Diversification ratio
        individual_vols = np.array([
            float(security_metrics[s]['vol_252d'])
            for s in symbols
        ])
        div_ratio = diversification_ratio(
            weights, individual_vols, portfolio_vol_annualised
        )
        weighted_avg_vol = float(np.sum(weights * individual_vols))

        # Beta
        beta_spy_vals = [
            float(security_metrics[s].get('beta_spy') or 0)
            for s in symbols
        ]
        beta_xiu_vals = [
            float(security_metrics[s].get('beta_xiu') or 0)
            for s in symbols
        ]
        beta_spy = float(np.sum(weights * np.array(beta_spy_vals)))
        beta_xiu = float(np.sum(weights * np.array(beta_xiu_vals)))

        # Concentration
        sorted_weights = np.sort(weights)[::-1]
        top1_weight = float(sorted_weights[0]) if len(sorted_weights) > 0 else 0.0
        top3_weight = float(np.sum(sorted_weights[:3])) if len(sorted_weights) >= 3 else 0.0
        top5_weight = float(np.sum(sorted_weights[:5])) if len(sorted_weights) >= 5 else 0.0

        # Top symbol
        symbol_mv = {}
        for pos in positions:
            symbol_mv[pos['symbol']] = symbol_mv.get(pos['symbol'], 0) + float(pos['quantity']) * float(pos['price'])
        top1_symbol = max(symbol_mv, key=symbol_mv.get) if symbol_mv else ''

        # HHI
        hhi_symbol = hhi(weights)

        # HHI by sector
        sector_map = {s: SecurityUniverse.objects.filter(symbol=s).first().sector
                      for s in symbols}
        hhi_sector = hhi_by_group(symbols, weights, sector_map)

        # HHI by asset class
        ac_map = {s: SecurityUniverse.objects.filter(symbol=s).first().asset_class
                  for s in symbols}
        hhi_asset_class = hhi_by_group(symbols, weights, ac_map)

        # Effective index weights
        eff_idx = effective_index_weights(symbols, weights, index_exposures)

        # Tax risk
        taxable_ugl, tax_flag = compute_tax_risk_flag(
            entity_type, entity_id, positions,
            {p['symbol']: float(p['price']) for p in positions},
            fx_rate, tax_threshold,
        )

        # Upsert
        PortfolioRiskMetrics.objects.update_or_create(
            entity_type=entity_type,
            entity_id=entity_id,
            calc_date=calc_date,
            defaults={
                'var_portfolio_95_usd': Decimal(str(round(var_usd, 2))),
                'var_portfolio_99_usd': Decimal(str(round(var_99_usd, 2))),
                'var_portfolio_95_pct': Decimal(str(round(var_pct, 4))),
                'var_undiversified_95_usd': Decimal(str(round(var_univ, 2))),
                'diversification_benefit_usd': Decimal(str(round(diversification_benefit, 2))),
                'portfolio_vol_annualised': Decimal(str(round(portfolio_vol_annualised, 6))),
                'diversification_ratio': Decimal(str(round(div_ratio, 4))),
                'weighted_avg_vol': Decimal(str(round(weighted_avg_vol, 6))),
                'portfolio_beta_spy': Decimal(str(round(beta_spy, 4))),
                'portfolio_beta_xiu': Decimal(str(round(beta_xiu, 4))),
                'top1_weight': Decimal(str(round(top1_weight, 4))),
                'top3_weight': Decimal(str(round(top3_weight, 4))),
                'top5_weight': Decimal(str(round(top5_weight, 4))),
                'top1_symbol': top1_symbol,
                'hhi_symbol': Decimal(str(round(hhi_symbol, 4))),
                'hhi_sector': Decimal(str(round(hhi_sector, 4))),
                'hhi_asset_class': Decimal(str(round(hhi_asset_class, 4))),
                'effective_sp500_weight': Decimal(str(round(eff_idx.get('SP500', 0), 4))),
                'effective_nasdaq100_weight': Decimal(str(round(eff_idx.get('NASDAQ100', 0), 4))),
                'effective_tsx_weight': Decimal(str(round(eff_idx.get('TSX', 0), 4))),
                'effective_semiconductor_weight': Decimal(str(round(eff_idx.get('SEMICONDUCTOR', 0), 4))),
                'effective_technology_weight': Decimal(str(round(eff_idx.get('TECHNOLOGY', 0), 4))),
                'effective_dividend_weight': Decimal(str(round(
                    eff_idx.get('DIVIDEND_CA', 0) + eff_idx.get('DIVIDEND_US', 0), 4
                ))),
                'taxable_ugl_usd': Decimal(str(round(taxable_ugl, 2))),
                'tax_risk_flag': tax_flag,
                'total_market_value_usd': Decimal(str(round(total_mv_usd, 2))),
                'position_count': len(symbols),
                'positions_used': len(symbols),
                'positions_skipped': 0,
            },
        )

    def _get_fx_rate(self, calc_date: date) -> float:
        try:
            fx = FxRate.objects.get(date=calc_date)
            return float(fx.usd_cad_rate)
        except FxRate.DoesNotExist:
            prior = FxRate.objects.filter(date__lt=calc_date).order_by('-date').first()
            if prior:
                return float(prior.usd_cad_rate)
            return 1.0

    def _get_config_value(self, key: str, default: float) -> float:
        try:
            config = ScoringConfig.objects.get(config_key=key)
            return float(config.config_value)
        except ScoringConfig.DoesNotExist:
            return default

    def _load_positions(self, calc_date: date) -> list[dict]:
        """Load positions from vw_current_portfolio view."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT account_id, owner, symbol, quantity,
                       currency, price, cost_basis_per_unit,
                       account_category, exclude_from_risk
                FROM vw_current_portfolio
                WHERE calc_date = %s
            """, [calc_date])

            columns = [col[0] for col in cursor.description]
            positions = []
            for row in cursor.fetchall():
                positions.append(dict(zip(columns, row)))
        return positions

    def _compute_accounts(
        self,
        positions: list[dict],
        security_metrics: dict[str, dict],
        correlation_matrix: dict[tuple[str, str], float],
        index_exposures: dict[str, dict[str, float]],
        fx_rate: float,
        calc_date: date,
        entity_id_filter: Optional[str],
        tax_threshold: float,
    ):
        """Compute metrics for each account."""
        for account_id in ACCOUNTS:
            if entity_id_filter and entity_id_filter != account_id:
                continue

            # Skip TAXABLE_CAR by default
            if account_id == 'TAXABLE_CAR':
                continue

            # Filter positions for this account
            account_positions = [
                p for p in positions
                if p['account_id'] == account_id
            ]

            if not account_positions:
                continue

            self._compute_entity_metrics(
                entity_type='account',
                entity_id=account_id,
                positions=account_positions,
                security_metrics=security_metrics,
                correlation_matrix=correlation_matrix,
                index_exposures=index_exposures,
                fx_rate=fx_rate,
                calc_date=calc_date,
                tax_threshold=tax_threshold,
            )
            self.stdout.write(f"  Account: {account_id} computed")

    def _compute_owners(
        self,
        positions: list[dict],
        security_metrics: dict[str, dict],
        correlation_matrix: dict[tuple[str, str], float],
        index_exposures: dict[str, dict[str, float]],
        fx_rate: float,
        calc_date: date,
        joint_split: float,
        entity_id_filter: Optional[str],
        tax_threshold: float,
    ):
        """Compute metrics for each owner."""
        for owner in OWNERS:
            if entity_id_filter and entity_id_filter != owner:
                continue

            # Get attributed positions
            owner_positions = get_owner_positions(owner, positions, joint_split)

            if not owner_positions:
                continue

            self._compute_entity_metrics(
                entity_type='owner',
                entity_id=owner,
                positions=owner_positions,
                security_metrics=security_metrics,
                correlation_matrix=correlation_matrix,
                index_exposures=index_exposures,
                fx_rate=fx_rate,
                calc_date=calc_date,
                tax_threshold=tax_threshold,
            )
            self.stdout.write(f"  Owner: {owner} computed")

    def _compute_portfolio(
        self,
        positions: list[dict],
        security_metrics: dict[str, dict],
        correlation_matrix: dict[tuple[str, str], float],
        index_exposures: dict[str, dict[str, float]],
        fx_rate: float,
        calc_date: date,
        entity_id_filter: Optional[str],
        tax_threshold: float,
    ):
        """Compute metrics for the combined portfolio."""
        if entity_id_filter and entity_id_filter != 'ALL':
            return

        # Filter out excluded accounts
        portfolio_positions = [
            p for p in positions
            if p.get('exclude_from_risk', 0) == 0
            and p['account_id'] != 'TAXABLE_CAR'
        ]

        if not portfolio_positions:
            return

        self._compute_entity_metrics(
            entity_type='portfolio',
            entity_id='ALL',
            positions=portfolio_positions,
            security_metrics=security_metrics,
            correlation_matrix=correlation_matrix,
            index_exposures=index_exposures,
            fx_rate=fx_rate,
            calc_date=calc_date,
            tax_threshold=tax_threshold,
        )
        self.stdout.write(f"  Portfolio: ALL computed")
