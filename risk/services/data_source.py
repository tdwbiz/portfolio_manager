"""
DataSource abstraction layer for the Risk Management Module.

This is the single interface all downstream code uses to request price data.
It is source-agnostic — callers never know or care whether data came from
Amibroker or yFinance.

DataSource reads only from rm_market_price_history, rm_fx_rates,
rm_economic_indicators. It never calls yFinance.
All M1, M2, M3 code imports DataSource from this module.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import pandas as pd
from django.db import connection

from risk.models import EconomicIndicator, FxRate, MarketPriceHistory

logger = logging.getLogger(__name__)


class DataNotAvailableError(Exception):
    """Raised when requested price data does not exist in rm_market_price_history."""
    pass


class DataSource:
    """
    Source-agnostic price series provider.

    Priority: Amibroker data (data_source='amibroker') → yFinance fallback
    (data_source='yfinance').
    Returns data already persisted in rm_market_price_history.
    Does NOT fetch — fetch is done by management commands.
    """

    def get_price_series(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        field: str = 'adj_close',
    ) -> pd.Series:
        """
        Return a daily price series for symbol between start_date and end_date
        (inclusive). Index: DatetimeIndex. Values: Decimal → float.
        Raises DataNotAvailableError if no data exists for the requested range.
        Prioritises amibroker rows; fills gaps with yfinance rows.
        """
        df = self._get_price_df(symbol, start_date, end_date)
        if df.empty:
            raise DataNotAvailableError(
                f"No price data available for {symbol} between "
                f"{start_date} and {end_date}"
            )
        values = df[field].astype(float)
        return values

    def get_ohlcv(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Return OHLCV DataFrame for symbol.
        Columns: open, high, low, close, adj_close, volume.
        Index: DatetimeIndex.
        """
        df = self._get_price_df(symbol, start_date, end_date)
        if df.empty:
            raise DataNotAvailableError(
                f"No OHLCV data available for {symbol} between "
                f"{start_date} and {end_date}"
            )
        return df[['open', 'high', 'low', 'close', 'adj_close', 'volume']].astype(float)

    def get_latest_price(self, symbol: str) -> Decimal:
        """Return the most recent close price for symbol from rm_market_price_history."""
        row = MarketPriceHistory.objects.filter(symbol=symbol).order_by('-date').first()
        if row is None:
            raise DataNotAvailableError(f"No price data for {symbol}")
        return row.close

    def get_available_date_range(self, symbol: str) -> tuple[date, date]:
        """Return (earliest_date, latest_date) for symbol in rm_market_price_history."""
        earliest = MarketPriceHistory.objects.filter(symbol=symbol).order_by('date').first()
        latest = MarketPriceHistory.objects.filter(symbol=symbol).order_by('-date').first()
        if earliest is None or latest is None:
            raise DataNotAvailableError(f"No date range available for {symbol}")
        return (earliest.date, latest.date)

    def get_fx_rate(self, date_val: date) -> Decimal:
        """
        Return USD/CAD rate for given date.
        Falls back to nearest prior date if exact date missing.
        """
        try:
            rate = FxRate.objects.get(date=date_val)
            return rate.usd_cad_rate
        except FxRate.DoesNotExist:
            # Fall back to nearest prior date
            prior = FxRate.objects.filter(date__lt=date_val).order_by('-date').first()
            if prior is None:
                raise DataNotAvailableError(
                    f"No FX rate available for {date_val} or any prior date"
                )
            logger.warning(
                "No FX rate for %s; falling back to nearest prior date %s",
                date_val, prior.date,
            )
            return prior.usd_cad_rate

    def get_economic_indicator(
        self,
        indicator_name: str,
        start_date: date,
        end_date: date,
    ) -> pd.Series:
        """Return time series for a named economic indicator from rm_economic_indicators."""
        qs = EconomicIndicator.objects.filter(
            indicator_name=indicator_name,
            date__gte=start_date,
            date__lte=end_date,
        ).order_by('date')
        if not qs.exists():
            raise DataNotAvailableError(
                f"No data for economic indicator '{indicator_name}' "
                f"between {start_date} and {end_date}"
            )
        data = {row.date: float(row.value) for row in qs}
        return pd.Series(data)

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _get_price_df(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """
        Load price data from rm_market_price_history.
        Prioritises amibroker rows; fills gaps with yfinance rows.
        """
        # First, get all amibroker rows
        amibroker_qs = MarketPriceHistory.objects.filter(
            symbol=symbol,
            date__gte=start_date,
            date__lte=end_date,
            data_source='amibroker',
        ).order_by('date')

        amibroker_df = self._qs_to_df(amibroker_qs)

        # Then get yfinance rows for the same range
        yf_qs = MarketPriceHistory.objects.filter(
            symbol=symbol,
            date__gte=start_date,
            date__lte=end_date,
            data_source='yfinance',
        ).order_by('date')

        yf_df = self._qs_to_df(yf_qs)

        # Merge: amibroker takes priority; yfinance fills gaps
        if not amibroker_df.empty and not yf_df.empty:
            # Use amibroker as base, fill missing dates from yfinance
            merged = amibroker_df.set_index('date')
            yf_indexed = yf_df.set_index('date')
            # Only fill dates not already present in amibroker
            missing_dates = yf_indexed.index.difference(merged.index)
            if missing_dates.size > 0:
                fill_df = yf_indexed.loc[missing_dates]
                merged = pd.concat([merged, fill_df])
            merged = merged.sort_index()
        elif not yf_df.empty:
            merged = yf_df.set_index('date')
        elif not amibroker_df.empty:
            merged = amibroker_df.set_index('date')
        else:
            merged = pd.DataFrame()

        return merged

    @staticmethod
    def _qs_to_df(qs) -> pd.DataFrame:
        """Convert a queryset to a DataFrame with date as index."""
        data = []
        for row in qs:
            data.append({
                'date': row.date,
                'open': row.open,
                'high': row.high,
                'low': row.low,
                'close': row.close,
                'adj_close': row.adj_close,
                'volume': row.volume,
            })
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df['date'] = pd.to_datetime(df['date'])
        return df
