"""
Management command: calc_market_conditions

Reads rm_market_price_history OHLCV, computes all technical indicators
for each held symbol.
"""

from __future__ import annotations

import logging
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import models

from risk.models import MarketCondition, RegimeState, SecurityUniverse
from risk.services.audit_hooks import write_audit_entry, write_audit_regime_log
from risk.services.data_source import DataSource, DataNotAvailableError
from risk.services.technical_indicators import (
    bollinger_bands,
    classify_market_phase,
    fifty_two_week_range,
    macd,
    macd_crossover_today,
    on_balance_volume,
    obv_trend,
    rsi,
    sma,
    sma_crossover,
)

logger = logging.getLogger(__name__)

# Symbols that are mutual funds or cash equivalents (no OHLCV data)
MUTUAL_FUND_SYMBOLS = {'RBF2010', 'RBF2014', 'SGOV', 'ICSH'}


class Command(BaseCommand):
    help = 'Calculate technical market conditions for held symbols'

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            type=str,
            help='Calculation date (YYYY-MM-DD). Defaults to today.',
        )
        parser.add_argument(
            '--symbol',
            type=str,
            help='Calculate for a specific symbol only.',
        )

    def handle(self, *args, **options):
        calc_date_str = options.get('date')
        if calc_date_str:
            try:
                calc_date = date.fromisoformat(calc_date_str)
            except ValueError:
                raise CommandError(f"Invalid date format: {calc_date_str}. Use YYYY-MM-DD.")
        else:
            calc_date = date.today()

        symbol_filter = options.get('symbol')

        # Resolve symbols
        if symbol_filter:
            symbols = [symbol_filter]
        else:
            symbols = list(
                SecurityUniverse.objects.filter(is_held=True).values_list('symbol', flat=True)
            )

        ds = DataSource()
        processed = 0
        skipped = 0

        for symbol in symbols:
            try:
                if symbol in MUTUAL_FUND_SYMBOLS:
                    # No OHLCV data for mutual funds/cash equivalents
                    self._store_null_conditions(symbol, calc_date)
                    self.stdout.write(
                        self.style.WARNING(
                            f"Symbol {symbol}: no price data for technical indicators — setting NULL"
                        )
                    )
                    processed += 1
                    continue

                # Load OHLCV data
                start_date, end_date = ds.get_available_date_range(symbol)
                ohlcv = ds.get_ohlcv(symbol, start_date, calc_date)

                if ohlcv.empty:
                    self.stdout.write(
                        self.style.WARNING(f"Symbol {symbol}: no OHLCV data available")
                    )
                    skipped += 1
                    continue

                # Compute indicators
                closes = ohlcv['close']
                volumes = ohlcv['volume']

                # SMA-50 and SMA-200
                sma_50 = sma(closes, 50)
                sma_200 = sma(closes, 200)

                # Crossover detection
                crossover_type, crossover_date = sma_crossover(sma_50, sma_200)

                # RSI
                rsi_14 = rsi(closes, 14)

                # MACD
                macd_line, macd_signal, macd_histogram = macd(closes)
                macd_cross = macd_crossover_today(macd_histogram)

                # Bollinger Bands
                bb_upper, bb_middle, bb_lower, bb_bandwidth, bb_pct_b = bollinger_bands(closes)

                # OBV
                obv_series = on_balance_volume(closes, volumes)
                obv_trend_val = obv_trend(obv_series)

                # 52-week range
                high_52w, low_52w, pct_from_52w_high = fifty_two_week_range(closes)

                # Current values
                current_price = float(closes.iloc[-1])
                current_sma_50 = float(sma_50.iloc[-1]) if not pd.isna(sma_50.iloc[-1]) else None
                current_sma_200 = float(sma_200.iloc[-1]) if not pd.isna(sma_200.iloc[-1]) else None
                current_rsi = float(rsi_14.iloc[-1]) if not pd.isna(rsi_14.iloc[-1]) else None
                current_macd_line = float(macd_line.iloc[-1]) if not pd.isna(macd_line.iloc[-1]) else None
                current_macd_signal = float(macd_signal.iloc[-1]) if not pd.isna(macd_signal.iloc[-1]) else None
                current_macd_hist = float(macd_histogram.iloc[-1]) if not pd.isna(macd_histogram.iloc[-1]) else None
                current_bb_upper = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else None
                current_bb_middle = float(bb_middle.iloc[-1]) if not pd.isna(bb_middle.iloc[-1]) else None
                current_bb_lower = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else None
                current_bb_bw = float(bb_bandwidth.iloc[-1]) if not pd.isna(bb_bandwidth.iloc[-1]) else None
                current_bb_pct_b = float(bb_pct_b.iloc[-1]) if not pd.isna(bb_pct_b.iloc[-1]) else None
                current_obv = int(obv_series.iloc[-1]) if not pd.isna(obv_series.iloc[-1]) else None

                # Price vs SMA percentages
                price_vs_sma50_pct = ((current_price - current_sma_50) / current_sma_50 * 100) if current_sma_50 else None
                price_vs_sma200_pct = ((current_price - current_sma_200) / current_sma_200 * 100) if current_sma_200 else None

                # Market phase classification
                market_phase = classify_market_phase(
                    current_price=current_price,
                    sma_200=current_sma_200,
                    pct_from_52w_high=pct_from_52w_high,
                    rsi=current_rsi,
                )

                # Store
                MarketCondition.objects.update_or_create(
                    symbol=symbol,
                    calc_date=calc_date,
                    defaults={
                        'sma_50': current_sma_50,
                        'sma_200': current_sma_200,
                        'price_vs_sma50_pct': price_vs_sma50_pct,
                        'price_vs_sma200_pct': price_vs_sma200_pct,
                        'crossover_type': crossover_type,
                        'crossover_date': crossover_date,
                        'rsi_14': current_rsi,
                        'macd_line': current_macd_line,
                        'macd_signal': current_macd_signal,
                        'macd_histogram': current_macd_hist,
                        'macd_crossover': macd_cross,
                        'bb_upper': current_bb_upper,
                        'bb_middle': current_bb_middle,
                        'bb_lower': current_bb_lower,
                        'bb_bandwidth': current_bb_bw,
                        'bb_pct_b': current_bb_pct_b,
                        'obv': current_obv,
                        'obv_trend': obv_trend_val,
                        'market_phase': market_phase,
                        'high_52w': high_52w,
                        'low_52w': low_52w,
                        'pct_from_52w_high': pct_from_52w_high,
                        'price_rows_used': len(ohlcv),
                    },
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Symbol {symbol}: phase={market_phase}, "
                        f"RSI={current_rsi}, crossover={crossover_type}"
                    )
                )
                processed += 1

            except DataNotAvailableError as e:
                logger.warning("Data not available for %s: %s", symbol, e)
                skipped += 1
            except Exception as e:
                logger.error("Error processing %s: %s", symbol, e, exc_info=True)
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} symbols, skipped {skipped}"
            )
        )

        # Audit hook: write regime log
        try:
            regime = RegimeState.objects.filter(calc_date=calc_date).order_by('-calc_date').first()
            if regime:
                # Aggregate market conditions for this date
                conditions = MarketCondition.objects.filter(calc_date=calc_date)
                phases = list(conditions.values_list('market_phase', flat=True).distinct())
                avg_rsi = conditions.filter(rsi_14__isnull=False).aggregate(avg_rsi=models.Avg('rsi_14'))['avg_rsi']
                uptrend_count = conditions.filter(market_phase='uptrend').count()
                total = conditions.count()

                write_audit_regime_log(
                    calc_date=calc_date,
                    regime_quadrant=regime.quadrant,
                    regime_volatility=regime.volatility_regime,
                    regime_trend=regime.trend_regime,
                    market_phases=phases,
                    avg_rsi=float(avg_rsi) if avg_rsi is not None else None,
                    uptrend_count=uptrend_count,
                    total_symbols=total,
                )
        except Exception:
            # Audit write failures logged at ERROR but never raise out of M4
            pass

        # Write AuditEntry
        try:
            write_audit_entry(
                model_name='market_conditions',
                calc_date=calc_date,
                status='success',
                summary=f"Market conditions processed for {processed} symbols, skipped {skipped}",
                details_json={
                    'processed': processed,
                    'skipped': skipped,
                },
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Warning: audit entry write failed: {e}"))

    def _store_null_conditions(self, symbol: str, calc_date: date):
        """Store NULL conditions for mutual funds/cash equivalents."""
        MarketCondition.objects.update_or_create(
            symbol=symbol,
            calc_date=calc_date,
            defaults={
                'sma_50': None,
                'sma_200': None,
                'price_vs_sma50_pct': None,
                'price_vs_sma200_pct': None,
                'crossover_type': '',
                'crossover_date': None,
                'rsi_14': None,
                'macd_line': None,
                'macd_signal': None,
                'macd_histogram': None,
                'macd_crossover': '',
                'bb_upper': None,
                'bb_middle': None,
                'bb_lower': None,
                'bb_bandwidth': None,
                'bb_pct_b': None,
                'obv': None,
                'obv_trend': '',
                'market_phase': 'uptrend',
                'high_52w': None,
                'low_52w': None,
                'pct_from_52w_high': None,
                'price_rows_used': 0,
            },
        )
