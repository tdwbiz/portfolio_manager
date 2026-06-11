"""
Management command to compute security-level risk metrics.

Usage:
    python manage.py calc_security_metrics [--date YYYY-MM-DD] [--symbol SYM] [--skip-correlation]
"""

import logging
import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from risk.models import (
    SecurityRiskMetrics,
    CorrelationMatrix,
    SecurityUniverse,
    FxRate,
)
from risk.services.data_source import DataSource, DataNotAvailableError
from risk.services.metrics import (
    prepare_return_series,
    load_volatility,
    var_parametric,
    var_historical,
    var_cornish_fisher,
    cvar,
    max_drawdown,
    current_drawdown_from_peak,
    beta,
    fx_adjusted_returns,
    sharpe_ratio,
    sortino_ratio,
    liquidity_score,
    avg_daily_dollar_volume,
    compute_correlation_matrix,
    ugl_at_risk,
    to_usd,
    get_position_data,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Compute security-level risk metrics for all or specified symbols.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            default=None,
            help='Calculate for a specific date (YYYY-MM-DD). Default: today.',
        )
        parser.add_argument(
            '--symbol',
            type=str,
            default=None,
            help='Calculate for one symbol only. Default: all active held symbols.',
        )
        parser.add_argument(
            '--skip-correlation',
            action='store_true',
            help='Skip correlation matrix update (faster for single-symbol runs).',
        )

    def handle(self, *args, **options):
        start_time = time.time()
        
        calc_date_str = options['date']
        if calc_date_str:
            calc_date = date.fromisoformat(calc_date_str)
        else:
            calc_date = date.today()
        
        target_symbol = options['symbol']
        skip_correlation = options['skip_correlation']
        
        # Resolve symbols
        if target_symbol:
            symbols = [target_symbol]
        else:
            symbols = list(
                SecurityUniverse.objects.filter(is_held=True, is_active=True).values_list('symbol', flat=True)
            )
        
        if not symbols:
            self.stdout.write(self.style.WARNING('No held symbols found to process.'))
            return
        
        self.stdout.write(f'Calculating metrics for {len(symbols)} symbol(s) as of {calc_date}...')
        
        # Load FX rate
        try:
            fx_rate = float(FxRate.objects.get(date=calc_date).usd_cad_rate)
        except FxRate.DoesNotExist:
            prior = FxRate.objects.filter(date__lt=calc_date).order_by('-date').first()
            if prior:
                fx_rate = float(prior.usd_cad_rate)
                self.stdout.write(f'Using fallback FX rate from {prior.date}: {fx_rate}')
            else:
                fx_rate = 1.0
                self.stdout.write('No FX rate found, using 1.0 (USD).')
        
        # Load benchmark returns (SPY and XIU.TO)
        ds = DataSource()
        spy_returns = None
        xiu_returns = None
        fx_returns = None
        
        try:
            spy_prices = ds.get_price_series('SPY', calc_date - timedelta(days=365), calc_date)
            spy_returns = prepare_return_series(spy_prices)
        except DataNotAvailableError:
            logger.warning('SPY price data not available, beta calculations may fail.')
        
        try:
            xiu_prices = ds.get_price_series('XIU.TO', calc_date - timedelta(days=365), calc_date)
            xiu_returns = prepare_return_series(xiu_prices)
        except DataNotAvailableError:
            logger.warning('XIU.TO price data not available.')
        
        try:
            fx_prices = ds.get_price_series('USD/CAD', calc_date - timedelta(days=365), calc_date)
            fx_returns = prepare_return_series(fx_prices)
        except DataNotAvailableError:
            logger.warning('USD/CAD FX returns not available.')
        
        # Load risk-free rate from SGOV
        risk_free_annual = 0.045  # Default fallback
        try:
            from risk.models import EconomicIndicator
            sgov_data = EconomicIndicator.objects.filter(
                indicator_name='SGOV_YIELD',
                date__gte=calc_date - timedelta(days=30),
                date__lte=calc_date,
            ).order_by('-date').first()
            if sgov_data:
                risk_free_annual = float(sgov_data.value)
        except Exception:
            logger.warning("Could not fetch SGOV yield for risk-free rate — using 0.0")
            risk_free_annual = 0.0
        
        processed = 0
        skipped = 0
        
        for symbol in symbols:
            try:
                self._process_symbol(
                    symbol,
                    calc_date,
                    ds,
                    spy_returns,
                    xiu_returns,
                    fx_returns,
                    fx_rate,
                    risk_free_annual,
                )
                processed += 1
            except Exception as e:
                logger.error(f'Failed to process {symbol}: {e}', exc_info=True)
                skipped += 1
        
        # Correlation matrix
        if not skip_correlation:
            self.stdout.write('Computing correlation matrix...')
            try:
                compute_correlation_matrix(symbols, calc_date)
                self.stdout.write('Correlation matrix updated.')
            except Exception as e:
                logger.error(f'Correlation matrix computation failed: {e}', exc_info=True)
        
        elapsed = time.time() - start_time
        self.stdout.write(
            self.style.SUCCESS(
                f'Done. Processed: {processed}, Skipped: {skipped}, Time: {elapsed:.2f}s'
            )
        )

    def _process_symbol(
        self,
        symbol: str,
        calc_date: date,
        ds: DataSource,
        spy_returns,
        xiu_returns,
        fx_returns,
        fx_rate: float,
        risk_free_annual: float,
    ):
        """Process a single symbol and upsert metrics."""
        from risk.models import SecurityUniverse, MarketPriceHistory
        
        try:
            universe = SecurityUniverse.objects.get(symbol=symbol)
        except SecurityUniverse.DoesNotExist:
            raise DataNotAvailableError(f'Symbol {symbol} not in security universe.')
        
        # Load price history
        try:
            prices = ds.get_price_series(symbol, calc_date - timedelta(days=365), calc_date)
            returns = prepare_return_series(prices)
        except DataNotAvailableError:
            raise DataNotAvailableError(f'No price data for {symbol}.')
        
        # Get price data source info
        price_rows = len(prices)
        price_source_rows = MarketPriceHistory.objects.filter(
            symbol=symbol,
            date__gte=calc_date - timedelta(days=365),
            date__lte=calc_date,
        )
        amibroker_count = price_source_rows.filter(data_source='amibroker').count()
        yf_count = price_source_rows.filter(data_source='yfinance').count()
        
        if amibroker_count > 0 and yf_count > 0:
            price_data_source = 'mixed'
        elif amibroker_count > 0:
            price_data_source = 'amibroker'
        elif yf_count > 0:
            price_data_source = 'yfinance'
        else:
            price_data_source = ''
        
        # Volatility (load from rm_volatility_summary)
        vol_windows = [10, 21, 63, 252]
        vol_data = load_volatility(symbol, calc_date, vol_windows)
        
        vol_10d = vol_data.get(10)
        vol_21d = vol_data.get(21)
        vol_63d = vol_data.get(63)
        vol_252d = vol_data.get(252)
        
        # Skip mutual funds (RBF2010, RBF2014)
        if universe.security_type == 'MUTUAL_FUND':
            SecurityRiskMetrics.objects.update_or_create(
                symbol=symbol,
                calc_date=calc_date,
                defaults={
                    'vol_10d': None,
                    'vol_21d': None,
                    'vol_63d': None,
                    'vol_252d': None,
                    'max_drawdown_from_cost': None,
                    'max_drawdown_252d': None,
                    'current_drawdown_from_252d_peak': None,
                    'var_parametric_95': None,
                    'var_parametric_99': None,
                    'var_historical_95': None,
                    'var_cornish_fisher_95': None,
                    'cvar_95': None,
                    'var_parametric_95_10d': None,
                    'var_parametric_99_10d': None,
                    'beta_spy': None,
                    'beta_xiu': None,
                    'sharpe_ratio': None,
                    'sortino_ratio': None,
                    'liquidity_score': 95.0,
                    'avg_daily_volume_usd_30d': None,
                    'ugl_at_risk_95_usd': None,
                    'ugl_at_risk_pct_of_ugl': None,
                    'price_data_source': price_data_source,
                    'price_rows_used': price_rows,
                },
            )
            return
        
        # Drawdown
        max_dd_from_cost = None
        max_dd_252d = None
        current_dd = None
        
        # Get position history for cost basis drawdown
        positions = get_position_data(symbol, calc_date)
        if positions:
            earliest_cost = min(p.cost_basis_per_unit for p in positions)
            # Create a price series starting from earliest cost
            cost_prices = prices.copy()
            cost_prices.iloc[0] = earliest_cost
            max_dd_from_cost = max_drawdown(cost_prices)
        
        if len(prices) >= 252:
            prices_252d = prices.tail(252)
            max_dd_252d = max_drawdown(prices_252d)
            current_dd = current_drawdown_from_peak(prices, 252)
        
        # VaR
        var_param_95 = None
        var_param_99 = None
        var_hist_95 = None
        var_cf_95 = None
        cvar_95 = None
        
        if vol_21d is not None:
            var_param_95 = var_parametric(vol_21d, horizon_days=1, confidence=0.95)
            var_param_99 = var_parametric(vol_21d, horizon_days=1, confidence=0.99)
            var_param_95_10d = var_parametric(vol_21d, horizon_days=10, confidence=0.95)
            var_param_99_10d = var_parametric(vol_21d, horizon_days=10, confidence=0.99)
        else:
            var_param_95_10d = None
            var_param_99_10d = None
        
        if len(returns) >= 252:
            returns_252d = returns.tail(252)
            var_hist_95 = var_historical(returns_252d, 0.95)
            var_cf_95 = var_cornish_fisher(returns_252d, 0.95)
            cvar_95 = cvar(returns_252d, 0.95)
        else:
            var_cf_95 = None
            cvar_95 = None
        
        # Beta
        beta_spy_val = None
        beta_xiu_val = None
        
        if universe.security_type in ('MUTUAL_FUND',) or symbol in ('SGOV', 'ICSH'):
            beta_spy_val = 0.0
            beta_xiu_val = 0.0
        else:
            if spy_returns is not None:
                if universe.primary_currency == 'CAD' and fx_returns is not None:
                    adj_spy_returns = fx_adjusted_returns(spy_returns, fx_returns)
                else:
                    adj_spy_returns = spy_returns
                beta_spy_val = beta(returns, adj_spy_returns)
            
            if xiu_returns is not None:
                beta_xiu_val = beta(returns, xiu_returns)
        
        # Sharpe and Sortino
        sharpe_val = None
        sortino_val = None
        
        if len(returns) >= 252 and vol_252d is not None and vol_252d > 0:
            returns_252d = returns.tail(252)
            sharpe_val = sharpe_ratio(returns_252d, risk_free_annual, 252)
            sortino_val = sortino_ratio(returns_252d, risk_free_annual, 252)
        
        # Liquidity
        avg_vol_30d = avg_daily_dollar_volume(symbol, 30, fx_rate)
        
        # Get position market value for liquidity
        positions = get_position_data(symbol, calc_date)
        total_market_value_usd = 0.0
        if positions:
            try:
                latest_price = MarketPriceHistory.objects.filter(symbol=symbol).order_by('-date').first()
                if latest_price:
                    price_usd = to_usd(float(latest_price.close), latest_price.currency, fx_rate)
                    total_market_value_usd = sum(p.quantity * price_usd for p in positions)
            except Exception:
                logger.warning("Could not fetch price for %s — market value will be 0", symbol)
        
        is_mutual_fund = universe.security_type == 'MUTUAL_FUND'
        is_cash_equiv = symbol in ('SGOV', 'ICSH')
        is_cef = universe.is_cef
        
        liq_score = liquidity_score(
            avg_daily_dollar_volume_usd=avg_vol_30d,
            position_market_value_usd=total_market_value_usd,
            is_cef=is_cef,
            is_mutual_fund=is_mutual_fund,
            is_cash_equivalent=is_cash_equiv,
        )
        
        # UGL at Risk
        ugl_usd = None
        ugl_pct = None
        
        if var_param_95 is not None:
            ugl_usd, ugl_pct = ugl_at_risk(symbol, calc_date, var_param_95)
        
        # Upsert
        SecurityRiskMetrics.objects.update_or_create(
            symbol=symbol,
            calc_date=calc_date,
            defaults={
                'vol_10d': vol_10d,
                'vol_21d': vol_21d,
                'vol_63d': vol_63d,
                'vol_252d': vol_252d,
                'max_drawdown_from_cost': max_dd_from_cost,
                'max_drawdown_252d': max_dd_252d,
                'current_drawdown_from_252d_peak': current_dd,
                'var_parametric_95': var_param_95,
                'var_parametric_99': var_param_99,
                'var_historical_95': var_hist_95,
                'var_cornish_fisher_95': var_cf_95,
                'cvar_95': cvar_95,
                'var_parametric_95_10d': var_param_95_10d,
                'var_parametric_99_10d': var_param_99_10d,
                'beta_spy': beta_spy_val,
                'beta_xiu': beta_xiu_val,
                'sharpe_ratio': sharpe_val,
                'sortino_ratio': sortino_val,
                'liquidity_score': liq_score,
                'avg_daily_volume_usd_30d': avg_vol_30d,
                'ugl_at_risk_95_usd': ugl_usd,
                'ugl_at_risk_pct_of_ugl': ugl_pct,
                'price_data_source': price_data_source,
                'price_rows_used': price_rows,
            },
        )
