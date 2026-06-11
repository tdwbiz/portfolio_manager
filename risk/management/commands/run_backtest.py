"""
Run a single backtest for a position under a strategy and scenario.

Usage examples:
    python manage.py run_backtest \\
        --symbol VFV \\
        --account TAXABLE_TOM_SUSAN \\
        --strategy trailing_stop_etf_taxable \\
        --scenario 2022_rate_shock \\
        --settings=portfolio_manager.settings_dev

    python manage.py run_backtest \\
        --symbol AAPL \\
        --account IB_SUSAN \\
        --strategy collar_high_gain_taxable_stock \\
        --scenario 2022_rate_shock \\
        --option-expiry-months 3 \\
        --settings=portfolio_manager.settings_dev

    python manage.py run_backtest \\
        --symbol QQQ \\
        --account TFSA_TOM \\
        --strategy protective_put_registered \\
        --scenario 2020_covid_crash \\
        --trail-pct 0.10 \\
        --settings=portfolio_manager.settings_dev

Strategy-specific flags:
    --trail-pct          Trailing stop trail percentage (default from strategy template)
    --hedge-ratio        Protective put hedge ratio (default from strategy template)
    --option-expiry-months  Option expiry in months (default 6)
    --put-strike-pct     Put strike as fraction of spot (default 0.85)
    --call-strike-pct    Call strike as fraction of spot (default 1.12)
    --inverse-leverage   Inverse ETF leverage (default -1.0)
"""

import json
import logging
from datetime import date

from django.core.management.base import BaseCommand
from django.core.management import CommandError
from django.db import transaction

from risk.models import (
    BacktestResult,
    BacktestRun,
    HistoricalScenario,
    StrategyTemplate,
)
from risk.services.audit_hooks import write_audit_entry, write_audit_simulation_run
from risk.services.backtest_engine import (
    load_price_series,
    get_position_at_date,
    simulate_trailing_stop,
    simulate_protective_put,
    simulate_collar,
    simulate_inverse_etf,
    run_stress_test_mode,
)
from risk.services.backtest_metrics import (
    compute_backtest_metrics,
    build_regime_split,
)
from risk.services.option_pricing import estimate_option_cost

logger = logging.getLogger(__name__)


# Default parameters
DEFAULT_TRAIL_PCT = 0.10
DEFAULT_HEDGE_RATIO = 1.0
DEFAULT_OPTION_EXPIRY_MONTHS = 6
DEFAULT_PUT_STRIKE_PCT = 0.85
DEFAULT_CALL_STRIKE_PCT = 1.12
DEFAULT_INVERSE_LEVERAGE = -1.0
DEFAULT_RISK_FREE_RATE = 0.045  # Fallback SGOV yield


class Command(BaseCommand):
    help = 'Run a single backtest for a position under a strategy and scenario.'

    def add_arguments(self, parser):
        parser.add_argument('--symbol', type=str, required=True, help='Ticker symbol (e.g. VFV)')
        parser.add_argument('--account', type=str, required=True, help='Account ID (e.g. TAXABLE_TOM_SUSAN)')
        parser.add_argument('--strategy', type=str, required=True, help='Strategy template code')
        parser.add_argument('--scenario', type=str, required=True, help='Scenario name (e.g. 2022_rate_shock)')
        parser.add_argument('--start', type=str, default=None, help='Override start date (YYYY-MM-DD)')
        parser.add_argument('--end', type=str, default=None, help='Override end date (YYYY-MM-DD)')
        parser.add_argument('--trail-pct', type=float, default=None, help='Trailing stop trail %')
        parser.add_argument('--hedge-ratio', type=float, default=None, help='Protective put hedge ratio')
        parser.add_argument('--option-expiry-months', type=int, default=None, help='Option expiry in months')
        parser.add_argument('--put-strike-pct', type=float, default=None, help='Put strike % of spot')
        parser.add_argument('--call-strike-pct', type=float, default=None, help='Call strike % of spot')
        parser.add_argument('--inverse-leverage', type=float, default=None, help='Inverse ETF leverage')
        parser.add_argument('--risk-free-rate', type=float, default=None, help='Risk-free rate override')

    def handle(self, *args, **options):
        symbol = options['symbol']
        account_id = options['account']
        strategy_code = options['strategy']
        scenario_name = options['scenario']
        verbosity = options.get('verbosity', 1)

        # Load scenario
        try:
            scenario = HistoricalScenario.objects.get(name=scenario_name, is_active=True)
        except HistoricalScenario.DoesNotExist:
            raise CommandError(f'Scenario "{scenario_name}" not found or inactive.')

        # Load strategy template
        try:
            template = StrategyTemplate.objects.get(code=strategy_code)
        except StrategyTemplate.DoesNotExist:
            raise CommandError(f'Strategy template "{strategy_code}" not found.')

        # Get position
        as_of_date = scenario.start_date
        position = get_position_at_date(symbol, account_id, as_of_date)
        if position is None:
            raise CommandError(
                f'No position found for {symbol} in {account_id} as of {as_of_date}.'
            )

        quantity = float(position['quantity'])
        cost_basis = float(position['cost_basis_per_unit'])
        currency = position['currency']

        # Determine date range
        start_date = options['start'] or scenario.start_date
        end_date = options['end'] or scenario.end_date
        if isinstance(start_date, str):
            start_date = date.fromisoformat(start_date)
        if isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)

        # Strategy parameters — use CLI overrides or template defaults
        trail_pct = options['trail_pct'] if options['trail_pct'] is not None else DEFAULT_TRAIL_PCT
        hedge_ratio = options['hedge_ratio'] if options['hedge_ratio'] is not None else DEFAULT_HEDGE_RATIO
        expiry_months = options['option_expiry_months'] if options['option_expiry_months'] is not None else DEFAULT_OPTION_EXPIRY_MONTHS
        put_strike_pct = options['put_strike_pct'] if options['put_strike_pct'] is not None else DEFAULT_PUT_STRIKE_PCT
        call_strike_pct = options['call_strike_pct'] if options['call_strike_pct'] is not None else DEFAULT_CALL_STRIKE_PCT
        inverse_leverage = options['inverse_leverage'] if options['inverse_leverage'] is not None else DEFAULT_INVERSE_LEVERAGE
        risk_free_rate = options['risk_free_rate'] if options['risk_free_rate'] is not None else DEFAULT_RISK_FREE_RATE

        if verbosity >= 1:
            self.stdout.write(
                f'Backtest: {symbol} ({account_id}) — {strategy_code} — {scenario_name}'
            )
            self.stdout.write(
                f'  Period: {start_date} to {end_date} | Qty: {quantity:.0f} | '
                f'Currency: {currency}'
            )

        # Load price series or run stress test
        is_stress_test = False
        try:
            prices = load_price_series(symbol, start_date, end_date)
        except Exception:
            # No price history — run in stress test mode
            logger.warning("No price history found for %s (%s to %s) — running in stress test mode", symbol, start_date, end_date)
            if verbosity >= 1:
                self.stdout.write('  No price history found — running in stress test mode.')
            is_stress_test = True
            asset_class = 'equity_ca' if currency == 'CAD' else 'equity_us'
            stress_result = run_stress_test_mode(
                scenario, quantity, cost_basis, asset_class
            )
            self._save_stress_test_result(
                symbol, account_id, template, scenario,
                start_date, end_date, quantity, cost_basis,
                stress_result, options,
            )
            return

        # Get current price for option pricing
        current_price_usd = float(prices['close'].iloc[0])

        # Run strategy simulation
        strategy_type = template.code.split('_')[0]  # 'trailing', 'protective', 'collar', 'inverse'

        if strategy_type == 'trailing':
            outcome = simulate_trailing_stop(prices, trail_pct)
            hedge_payoff = outcome.final_price_series - prices['close']
            hedge_cost = 0.0  # Trailing stop has no premium cost (commission only)
            strategy_outcomes = {
                'stop_triggered': outcome.stop_triggered,
                'stop_trigger_date': outcome.stop_trigger_date,
                'stop_trigger_price': outcome.stop_trigger_price,
            }
        elif strategy_type == 'protective':
            put_strike = current_price_usd * put_strike_pct
            outcome = simulate_protective_put(
                prices, quantity, put_strike_pct,
                expiry_months, template.params.get('vol_63d', 0.20),
                risk_free_rate, current_price_usd,
            )
            hedge_payoff = outcome.daily_hedge_payoff
            hedge_cost = outcome.net_premium_usd
            strategy_outcomes = {
                'option_expired_worthless': outcome.expired_worthless,
            }
        elif strategy_type == 'collar':
            outcome = simulate_collar(
                prices, quantity, put_strike_pct, call_strike_pct,
                expiry_months, template.params.get('vol_63d', 0.20),
                risk_free_rate, current_price_usd,
            )
            hedge_payoff = outcome.daily_hedge_payoff
            hedge_cost = outcome.net_premium_usd
            strategy_outcomes = {
                'option_expired_worthless': outcome.expired_worthless,
            }
        elif strategy_type == 'inverse':
            outcome = simulate_inverse_etf(prices, inverse_leverage)
            # Inverse ETF: daily return difference vs unhedged
            hedge_payoff = prices['close'] * (outcome.cumulative_leverage - 1)
            hedge_cost = outcome.decay_loss_usd  # Decay as cost
            strategy_outcomes = {}
        else:
            raise CommandError(f'Unknown strategy type: {strategy_type}')

        # Compute metrics
        metrics = compute_backtest_metrics(
            prices=prices,
            hedge_payoff_daily=hedge_payoff,
            hedge_cost_total_usd=hedge_cost,
            quantity=quantity,
            start_price_usd=current_price_usd,
            strategy_specific_outcomes=strategy_outcomes,
        )

        # Build regime split
        regime_split = build_regime_split(prices)

        if verbosity >= 1:
            self._print_metrics(metrics, strategy_type, is_stress_test)

        # Save to database
        self._save_result(
            symbol, account_id, template, scenario,
            start_date, end_date, quantity, cost_basis,
            currency, strategy_outcomes, metrics,
            regime_split, options,
        )

    def _save_result(self, symbol, account_id, template, scenario,
                     start_date, end_date, quantity, cost_basis,
                     currency, strategy_outcomes, metrics, regime_split, options):
        """Save BacktestRun + BacktestResult to database."""
        strategy_params = {
            'trail_pct': options.get('trail_pct'),
            'hedge_ratio': options.get('hedge_ratio'),
            'option_expiry_months': options.get('option_expiry_months'),
            'put_strike_pct': options.get('put_strike_pct'),
            'call_strike_pct': options.get('call_strike_pct'),
            'inverse_leverage': options.get('inverse_leverage'),
        }

        with transaction.atomic():
            run = BacktestRun.objects.create(
                symbol=symbol,
                account_id=account_id,
                owner=options.get('owner', 'system'),
                strategy_template=template,
                scenario=scenario,
                start_date=start_date,
                end_date=end_date,
                quantity=quantity,
                cost_basis_per_unit_usd=cost_basis,
                start_price_usd=metrics['position_value_start_usd'] / quantity if quantity > 0 else 0,
                currency=currency,
                strategy_params_json=json.dumps(strategy_params),
                run_by='system',
            )

            BacktestResult.objects.create(
                run=run,
                total_return_unhedged_pct=metrics['total_return_unhedged_pct'],
                total_return_hedged_pct=metrics['total_return_hedged_pct'],
                return_improvement_pct=metrics['return_improvement_pct'],
                max_drawdown_unhedged=metrics['max_drawdown_unhedged'],
                max_drawdown_hedged=metrics['max_drawdown_hedged'],
                drawdown_reduction_pct=metrics['drawdown_reduction_pct'],
                hedge_cost_total_usd=metrics['hedge_cost_total_usd'],
                hedge_cost_pct_of_position=metrics['hedge_cost_pct_of_position'],
                hedge_effectiveness_ratio=metrics['hedge_effectiveness_ratio'],
                breakeven_annual_cost_pct=metrics['breakeven_annual_cost_pct'],
                stop_triggered=metrics.get('stop_triggered'),
                stop_trigger_date=metrics.get('stop_trigger_date'),
                stop_trigger_price_usd=metrics.get('stop_trigger_price_usd'),
                option_expired_worthless=metrics.get('option_expired_worthless'),
                option_payoff_usd=metrics['option_payoff_usd'],
                position_value_start_usd=metrics['position_value_start_usd'],
                position_value_end_unhedged_usd=metrics['position_value_end_unhedged_usd'],
                position_value_end_hedged_usd=metrics['position_value_end_hedged_usd'],
                regime_split_json=json.dumps(regime_split),
            )

        if options.get('verbosity', 1) >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  ✓ Saved BacktestRun #{run.pk} + BacktestResult'
                )
            )

        # Audit hook: write simulation run log
        try:
            write_audit_simulation_run(
                entity_type='security_in_account',
                entity_id=f"{account_id}::{symbol}",
                symbol=symbol,
                calc_date=end_date if isinstance(end_date, date) else date.today(),
                simulation_family='backtest',
                source_run_id=run.pk,
                parameters_json=json.dumps({
                    'scenario': scenario.name,
                    'strategy': template.code,
                    'start_date': str(start_date),
                    'end_date': str(end_date),
                    'quantity': quantity,
                    'cost_basis': cost_basis,
                    'currency': currency,
                    'strategy_params': {k: v for k, v in {
                        'trail_pct': options.get('trail_pct'),
                        'hedge_ratio': options.get('hedge_ratio'),
                        'option_expiry_months': options.get('option_expiry_months'),
                        'put_strike_pct': options.get('put_strike_pct'),
                        'call_strike_pct': options.get('call_strike_pct'),
                        'inverse_leverage': options.get('inverse_leverage'),
                    }.items() if v is not None},
                }),
                summary_json=json.dumps({
                    'total_return_unhedged_pct': metrics['total_return_unhedged_pct'],
                    'total_return_hedged_pct': metrics['total_return_hedged_pct'],
                    'return_improvement_pct': metrics['return_improvement_pct'],
                    'max_drawdown_unhedged': metrics['max_drawdown_unhedged'],
                    'max_drawdown_hedged': metrics['max_drawdown_hedged'],
                    'hedge_cost_total_usd': metrics['hedge_cost_total_usd'],
                }),
            )
        except Exception:
            pass

        # Write AuditEntry
        try:
            write_audit_entry(
                model_name='backtest',
                calc_date=end_date if isinstance(end_date, date) else date.today(),
                status='success',
                summary=f"Backtest: {symbol} {scenario} {template.code}",
                details_json={
                    'symbol': symbol,
                    'account_id': account_id,
                    'scenario': scenario.name,
                    'strategy': template.code,
                    'start_date': str(start_date),
                    'end_date': str(end_date),
                    'metrics': {k: float(v) if isinstance(v, (int, float)) else v for k, v in metrics.items()},
                },
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Warning: audit entry write failed: {e}"))

    def _save_stress_test_result(self, symbol, account_id, template, scenario,
                                  start_date, end_date, quantity, cost_basis,
                                  stress_result, options):
        """Save a stress test result (no price history available)."""
        with transaction.atomic():
            run = BacktestRun.objects.create(
                symbol=symbol,
                account_id=account_id,
                owner=options.get('owner', 'system'),
                strategy_template=template,
                scenario=scenario,
                start_date=start_date,
                end_date=end_date,
                quantity=quantity,
                cost_basis_per_unit_usd=cost_basis,
                start_price_usd=cost_basis,
                currency='CAD',
                strategy_params_json=json.dumps({
                    'stress_test_mode': True,
                    'asset_class': 'equity_ca',
                }),
                run_by='system',
            )

            BacktestResult.objects.create(
                run=run,
                total_return_unhedged_pct=stress_result['total_return_pct'],
                total_return_hedged_pct=stress_result['total_return_pct'],
                return_improvement_pct=0.0,
                max_drawdown_unhedged=stress_result['total_return_pct'],
                max_drawdown_hedged=stress_result['total_return_pct'],
                drawdown_reduction_pct=0.0,
                hedge_cost_total_usd=0.0,
                hedge_cost_pct_of_position=0.0,
                hedge_effectiveness_ratio=None,
                breakeven_annual_cost_pct=None,
                stop_triggered=None,
                stop_trigger_date=None,
                stop_trigger_price_usd=None,
                option_expired_worthless=None,
                option_payoff_usd=0.0,
                position_value_start_usd=cost_basis * quantity,
                position_value_end_unhedged_usd=stress_result['end_value_usd'],
                position_value_end_hedged_usd=stress_result['end_value_usd'],
                regime_split_json=json.dumps({
                    'stress_test': {
                        'benchmark_spy_return': stress_result.get('benchmark_spy_return'),
                        'benchmark_qqq_return': stress_result.get('benchmark_qqq_return'),
                        'benchmark_vdy_return': stress_result.get('benchmark_vdy_return'),
                    }
                }),
            )

        if options.get('verbosity', 1) >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  ✓ Saved stress test result for BacktestRun #{run.pk}'
                )
            )

        # Audit hook: write simulation run log (stress test mode)
        try:
            write_audit_simulation_run(
                entity_type='security_in_account',
                entity_id=f"{account_id}::{symbol}",
                symbol=symbol,
                calc_date=end_date if isinstance(end_date, date) else date.today(),
                simulation_family='backtest',
                source_run_id=run.pk,
                parameters_json=json.dumps({
                    'scenario': scenario.name,
                    'strategy': template.code,
                    'start_date': str(start_date),
                    'end_date': str(end_date),
                    'quantity': quantity,
                    'cost_basis': cost_basis,
                    'stress_test_mode': True,
                }),
                summary_json=json.dumps({
                    'total_return_pct': stress_result['total_return_pct'],
                    'end_value_usd': stress_result['end_value_usd'],
                }),
            )
        except Exception:
            pass

    def _print_metrics(self, metrics, strategy_type, is_stress_test):
        """Print metrics to stdout."""
        self.stdout.write(f'  Strategy: {strategy_type}')
        self.stdout.write(f'  Unhedged return: {metrics["total_return_unhedged_pct"] * 100:.2f}%')
        self.stdout.write(f'  Hedged return:   {metrics["total_return_hedged_pct"] * 100:.2f}%')
        self.stdout.write(f'  Return delta:    {metrics["return_improvement_pct"] * 100:.2f}%')
        self.stdout.write(f'  Max DD unhedged: {metrics["max_drawdown_unhedged"] * 100:.2f}%')
        self.stdout.write(f'  Max DD hedged:   {metrics["max_drawdown_hedged"] * 100:.2f}%')
        self.stdout.write(f'  DD reduction:    {metrics["drawdown_reduction_pct"] * 100:.2f}%')
        self.stdout.write(f'  Hedge cost:      ${metrics["hedge_cost_total_usd"]:,.2f} '
                          f'({metrics["hedge_cost_pct_of_position"] * 100:.2f}%)')
        if metrics['hedge_effectiveness_ratio'] is not None:
            self.stdout.write(f'  Effectiveness:   {metrics["hedge_effectiveness_ratio"]:.2f}x')
        if strategy_type == 'trailing':
            self.stdout.write(f'  Stop triggered:  {metrics.get("stop_triggered", "N/A")}')
            if metrics.get('stop_trigger_date'):
                self.stdout.write(f'  Stop date:       {metrics["stop_trigger_date"]}')
                self.stdout.write(f'  Stop price:      ${metrics["stop_trigger_price_usd"]:,.2f}')
