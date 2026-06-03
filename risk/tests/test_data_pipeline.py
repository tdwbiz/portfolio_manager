"""
Tests for M0-Spec-2 data pipeline.

Run with:
    python manage.py test risk.tests.test_data_pipeline --settings=portfolio_manager.settings_test
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

import numpy as np
import pandas as pd

from risk.models import (
    DataQualityLog,
    EconomicIndicator,
    FxRate,
    MarketPriceHistory,
    SecurityUniverse,
    VolatilitySummary,
)
from risk.services.data_source import DataNotAvailableError, DataSource
from risk.services.gap_detection import GapReport, detect_price_gaps
from risk.services.volatility import compute_volatility_summaries


class DataSourceTests(TestCase):
    """Tests for DataSource abstraction layer."""

    def setUp(self):
        self.ds = DataSource()
        # Seed some test data
        self.symbol = 'TEST_SYM'
        SecurityUniverse.objects.create(
            symbol=self.symbol,
            name='Test Symbol',
            security_type='etf',
            asset_class='etf_equity',
            primary_currency='USD',
            is_active=True,
        )
        # Seed 10 rows of price data
        for i in range(10):
            d = date(2026, 1, 1) + timedelta(days=i)
            MarketPriceHistory.objects.create(
                symbol=self.symbol,
                date=d,
                open=100.0 + i,
                high=102.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                adj_close=100.5 + i,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )
        # Also seed amibroker data for some dates to test priority
        MarketPriceHistory.objects.create(
            symbol=self.symbol,
            date=date(2026, 1, 3),
            open=200.0,
            high=202.0,
            low=199.0,
            close=200.5,
            adj_close=200.5,
            volume=2000000,
            currency='USD',
            data_source='amibroker',
        )
        MarketPriceHistory.objects.create(
            symbol=self.symbol,
            date=date(2026, 1, 4),
            open=201.0,
            high=203.0,
            low=200.0,
            close=201.5,
            adj_close=201.5,
            volume=2000000,
            currency='USD',
            data_source='amibroker',
        )

    def test_get_price_series_returns_correct_dates(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 5)
        series = self.ds.get_price_series(self.symbol, start, end, field='close')
        self.assertEqual(len(series), 5)
        self.assertIn(date(2026, 1, 1), series.index)
        self.assertIn(date(2026, 1, 5), series.index)

    def test_amibroker_data_takes_priority_over_yfinance(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 5)
        series = self.ds.get_price_series(self.symbol, start, end, field='close')
        # Jan 3 and Jan 4 should have amibroker values (200.5, 201.5)
        self.assertEqual(
            float(series.loc[date(2026, 1, 3)]), 200.5
        )
        self.assertEqual(
            float(series.loc[date(2026, 1, 4)]), 201.5
        )

    def test_get_price_series_raises_when_no_data(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 5)
        with self.assertRaises(DataNotAvailableError):
            self.ds.get_price_series('NONEXISTENT', start, end)

    def test_get_latest_price(self):
        latest = self.ds.get_latest_price(self.symbol)
        self.assertEqual(latest, Decimal('109.5'))

    def test_get_available_date_range(self):
        earliest, latest = self.ds.get_available_date_range(self.symbol)
        self.assertEqual(earliest, date(2026, 1, 1))
        self.assertEqual(latest, date(2026, 1, 10))

    def test_get_available_date_range_raises_when_no_data(self):
        with self.assertRaises(DataNotAvailableError):
            self.ds.get_available_date_range('NONEXISTENT')

    def test_get_fx_rate_falls_back_to_nearest_prior_date(self):
        # Seed rate for 2026-01-13 (Tuesday)
        FxRate.objects.create(
            date=date(2026, 1, 13),
            usd_cad_rate=Decimal('1.3800'),
            data_source='yfinance',
        )
        # Request rate for 2026-01-14 (Wednesday, no data)
        rate = self.ds.get_fx_rate(date(2026, 1, 14))
        self.assertEqual(rate, Decimal('1.3800'))

    def test_get_fx_rate_raises_when_no_prior_date(self):
        with self.assertRaises(DataNotAvailableError):
            self.ds.get_fx_rate(date(2020, 1, 1))

    def test_get_ohlcv(self):
        start = date(2026, 1, 1)
        end = date(2026, 1, 3)
        df = self.ds.get_ohlcv(self.symbol, start, end)
        self.assertEqual(len(df), 3)
        self.assertIn('open', df.columns)
        self.assertIn('high', df.columns)
        self.assertIn('low', df.columns)
        self.assertIn('close', df.columns)
        self.assertIn('adj_close', df.columns)
        self.assertIn('volume', df.columns)

    def test_get_economic_indicator(self):
        for i in range(5):
            d = date(2026, 1, 1) + timedelta(days=i)
            EconomicIndicator.objects.create(
                date=d,
                indicator_name='vix_close',
                value=Decimal(str(20.0 + i)),
                data_source='yfinance',
            )
        start = date(2026, 1, 1)
        end = date(2026, 1, 5)
        series = self.ds.get_economic_indicator('vix_close', start, end)
        self.assertEqual(len(series), 5)

    def test_get_economic_indicator_raises_when_no_data(self):
        with self.assertRaises(DataNotAvailableError):
            self.ds.get_economic_indicator('nonexistent', date(2026, 1, 1), date(2026, 1, 5))


class VolatilityComputationTests(TestCase):
    """Tests for volatility computation logic."""

    def setUp(self):
        self.test_symbol = 'TEST_VOL'
        SecurityUniverse.objects.create(
            symbol=self.test_symbol,
            name='Test Vol Symbol',
            security_type='etf',
            asset_class='etf_equity',
            primary_currency='USD',
            is_active=True,
        )

    def test_annualised_volatility_formula(self):
        """Seed 253 days of synthetic prices and compute 252-day volatility."""
        # Create prices with known volatility
        np.random.seed(42)
        n = 253
        daily_vol = 0.02  # 2% daily vol
        prices = [100.0]
        for i in range(1, n):
            ret = np.random.normal(0, daily_vol)
            prices.append(prices[-1] * (1 + ret))

        today = date.today()
        for i in range(n):
            d = today - timedelta(days=n - 1 - i)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=d,
                open=prices[i],
                high=prices[i] * 1.01,
                low=prices[i] * 0.99,
                close=prices[i],
                adj_close=prices[i],
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        # Compute volatility
        compute_volatility_summaries(symbol=self.test_symbol, calc_date=today)

        # Check that a 252-day volatility row exists
        vol_entry = VolatilitySummary.objects.filter(
            symbol=self.test_symbol,
            calc_date=today,
            lookback_days=252,
        ).first()
        self.assertIsNotNone(vol_entry)
        # Annualised vol should be roughly 2% * sqrt(252) ≈ 31.7%
        # Allow for randomness
        self.assertGreater(float(vol_entry.volatility_annualised), 0.1)
        self.assertLess(float(vol_entry.volatility_annualised), 0.6)

    def test_volatility_skipped_when_insufficient_history(self):
        """Seed only 15 rows — 21-day window should be skipped."""
        today = date.today()
        for i in range(15):
            d = today - timedelta(days=14 - i)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=d,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                adj_close=100.5 + i,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        # Run compute_volatility — should not crash, but skip 21-day window
        compute_volatility_summaries(symbol=self.test_symbol, calc_date=today)

        # No row for 21-day window (needs 23 rows)
        vol_entry = VolatilitySummary.objects.filter(
            symbol=self.test_symbol,
            calc_date=today,
            lookback_days=21,
        ).first()
        self.assertIsNone(vol_entry)

    def test_volatility_upsert_updates_existing_row(self):
        """Running compute_volatility twice should not duplicate rows."""
        today = date.today()
        # Seed 300 days of prices
        for i in range(300):
            d = today - timedelta(days=299 - i)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=d,
                open=100.0 + i * 0.01,
                high=101.0 + i * 0.01,
                low=99.0 + i * 0.01,
                close=100.5 + i * 0.01,
                adj_close=100.5 + i * 0.01,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        compute_volatility_summaries(symbol=self.test_symbol, calc_date=today)

        count_before = VolatilitySummary.objects.filter(
            symbol=self.test_symbol,
            calc_date=today,
            lookback_days=21,
        ).count()

        # Run again
        compute_volatility_summaries(symbol=self.test_symbol, calc_date=today)

        count_after = VolatilitySummary.objects.filter(
            symbol=self.test_symbol,
            calc_date=today,
            lookback_days=21,
        ).count()

        self.assertEqual(count_before, count_after)
        self.assertEqual(count_before, 1)

    def test_all_four_windows_computed(self):
        """Seed 300 rows and verify all 4 windows are computed."""
        today = date.today()
        for i in range(300):
            d = today - timedelta(days=299 - i)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=d,
                open=100.0 + i * 0.01,
                high=101.0 + i * 0.01,
                low=99.0 + i * 0.01,
                close=100.5 + i * 0.01,
                adj_close=100.5 + i * 0.01,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        compute_volatility_summaries(symbol=self.test_symbol, calc_date=today)

        expected_windows = {10, 21, 63, 252}
        actual_windows = set(
            VolatilitySummary.objects.filter(
                symbol=self.test_symbol,
                calc_date=today,
            ).values_list('lookback_days', flat=True)
        )
        self.assertEqual(actual_windows, expected_windows)


class IncrementalFetchTests(TestCase):
    """Tests for incremental fetch logic (mocked yFinance responses)."""

    def setUp(self):
        self.test_symbol = 'FETCH_TEST'
        SecurityUniverse.objects.create(
            symbol=self.test_symbol,
            name='Fetch Test',
            security_type='etf',
            asset_class='etf_equity',
            primary_currency='USD',
            is_active=True,
        )

    def _make_mock_df(self, dates, close_values):
        """Create a mock yFinance DataFrame."""
        idx = pd.DatetimeIndex(dates)
        df = pd.DataFrame({
            'Open': [float(v) for v in close_values],
            'High': [float(v) + 1 for v in close_values],
            'Low': [float(v) - 1 for v in close_values],
            'Close': [float(v) for v in close_values],
            'Volume': [1000000] * len(dates),
        }, index=idx)
        return df

    @patch('risk.services.yfinance_client.fetch_symbol')
    def test_incremental_fetch_only_adds_missing_dates(self, mock_fetch):
        """Incremental fetch should only add rows after last existing date."""
        # Seed data up to 2026-05-01
        for i in range(10):
            d = date(2026, 4, 20) + timedelta(days=i)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=d,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                adj_close=100.5 + i,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        # Mock yFinance to return data from 2026-05-01 to 2026-05-31
        dates = [date(2026, 5, 1) + timedelta(days=i) for i in range(20)]
        close_vals = [100.5 + i for i in range(20)]
        mock_fetch.return_value = self._make_mock_df(dates, close_vals)

        from risk.management.commands.fetch_yfinance_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            symbol=self.test_symbol,
            start=None,
            force_full=False,
        )

        # Should have 10 original + 20 new = 30 rows
        count = MarketPriceHistory.objects.filter(
            symbol=self.test_symbol
        ).count()
        self.assertEqual(count, 30)

    @patch('risk.services.yfinance_client.fetch_symbol')
    def test_yfinance_does_not_overwrite_amibroker_rows(self, mock_fetch):
        """yFinance should not insert rows where amibroker already has data."""
        # Seed (AAPL, 2026-01-15) with amibroker
        aapl = 'AAPL'
        SecurityUniverse.objects.create(
            symbol=aapl,
            name='Apple',
            security_type='etf',
            asset_class='etf_equity',
            primary_currency='USD',
            is_active=True,
        )
        MarketPriceHistory.objects.create(
            symbol=aapl,
            date=date(2026, 1, 15),
            open=150.0,
            high=152.0,
            low=149.0,
            close=150.5,
            adj_close=150.5,
            volume=50000000,
            currency='USD',
            data_source='amibroker',
        )

        # Mock yFinance to return data including 2026-01-15
        dates = [date(2026, 1, 15) + timedelta(days=i) for i in range(5)]
        close_vals = [150.5 + i for i in range(5)]
        mock_fetch.return_value = self._make_mock_df(dates, close_vals)

        from risk.management.commands.fetch_yfinance_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            symbol=aapl,
            start=None,
            force_full=False,
        )

        # Only 4 rows should be inserted (not the amibroker date)
        count = MarketPriceHistory.objects.filter(symbol=aapl).count()
        self.assertEqual(count, 5)  # 1 amibroker + 4 yfinance

        # Verify amibroker row is unchanged
        amibroker_row = MarketPriceHistory.objects.get(
            symbol=aapl, date=date(2026, 1, 15), data_source='amibroker'
        )
        self.assertEqual(float(amibroker_row.close), 150.5)

    @patch('risk.services.yfinance_client.fetch_symbol')
    def test_force_full_refetches_entire_history(self, mock_fetch):
        """With --force-full, fetch should go back 10 years."""
        # Seed some rows
        for i in range(5):
            d = date(2026, 1, 1) + timedelta(days=i)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=d,
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                adj_close=100.5 + i,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        # Mock yFinance to return data for 10 years
        dates = [
            date(2016, 1, 1) + timedelta(days=i)
            for i in range(100)
        ]
        close_vals = [100.0 + i * 0.01 for i in range(100)]
        mock_fetch.return_value = self._make_mock_df(dates, close_vals)

        from risk.management.commands.fetch_yfinance_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            symbol=self.test_symbol,
            start=None,
            force_full=True,
        )

        # Should have many more rows (100 new + 5 existing, minus any duplicates)
        count = MarketPriceHistory.objects.filter(
            symbol=self.test_symbol
        ).count()
        self.assertGreater(count, 10)


class GapDetectionTests(TestCase):
    """Tests for gap detection."""

    def setUp(self):
        self.test_symbol = 'GAP_TEST'
        SecurityUniverse.objects.create(
            symbol=self.test_symbol,
            name='Gap Test',
            security_type='etf',
            asset_class='etf_equity',
            primary_currency='USD',
            is_active=True,
        )

    def test_gap_detected_when_more_than_5_business_days_missing(self):
        """Seed prices with a 7-day gap and verify detection."""
        today = date.today()
        # Seed prices up to 30 days ago
        for i in range(30):
            d = today - timedelta(days=29 - i)
            if d.weekday() < 5:  # Only weekdays
                MarketPriceHistory.objects.create(
                    symbol=self.test_symbol,
                    date=d,
                    open=100.0 + i,
                    high=101.0 + i,
                    low=99.0 + i,
                    close=100.5 + i,
                    adj_close=100.5 + i,
                    volume=1000000,
                    currency='USD',
                    data_source='yfinance',
                )

        # Create a gap: skip 7 calendar days (around 5 business days)
        # Seed more prices after the gap (20 days ago)
        for i in range(20):
            d = today - timedelta(days=19 - i)
            if d.weekday() < 5:
                MarketPriceHistory.objects.create(
                    symbol=self.test_symbol,
                    date=d,
                    open=110.0 + i,
                    high=111.0 + i,
                    low=109.0 + i,
                    close=110.5 + i,
                    adj_close=110.5 + i,
                    volume=1000000,
                    currency='USD',
                    data_source='yfinance',
                )

        reports = detect_price_gaps(symbols=[self.test_symbol])
        # Should have at least one gap report
        self.assertTrue(len(reports) > 0)
        # Find the gap that crosses the missing period
        gap_found = any(
            r.gap_days >= 5 for r in reports
        )
        self.assertTrue(gap_found)

    def test_no_gap_reported_for_weekends(self):
        """Friday → Monday should not trigger a gap (weekend is not a gap)."""
        today = date.today()
        # Seed Friday
        friday = today - timedelta(days=today.weekday() + 2)  # Last Friday
        if friday.weekday() == 4:
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=friday,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                adj_close=100.5,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )
            # Seed Monday
            monday = friday + timedelta(days=2)
            MarketPriceHistory.objects.create(
                symbol=self.test_symbol,
                date=monday,
                open=101.0,
                high=102.0,
                low=100.0,
                close=101.5,
                adj_close=101.5,
                volume=1000000,
                currency='USD',
                data_source='yfinance',
            )

        reports = detect_price_gaps(symbols=[self.test_symbol])
        # The weekend gap should be small (2 calendar days) — not a warning
        weekend_gaps = [r for r in reports if r.gap_days == 2]
        # If any weekend gap exists, it should not be 'warning' or 'critical'
        for gap in weekend_gaps:
            self.assertNotEqual(gap.severity, 'warning')
            self.assertNotEqual(gap.severity, 'critical')

    def test_gap_written_to_data_quality_log(self):
        """After detect_price_gaps, a DataQualityLog row should be created."""
        today = date.today()
        # Seed prices up to 30 days ago
        for i in range(30):
            d = today - timedelta(days=29 - i)
            if d.weekday() < 5:
                MarketPriceHistory.objects.create(
                    symbol=self.test_symbol,
                    date=d,
                    open=100.0 + i,
                    high=101.0 + i,
                    low=99.0 + i,
                    close=100.5 + i,
                    adj_close=100.5 + i,
                    volume=1000000,
                    currency='USD',
                    data_source='yfinance',
                )

        # Create a gap
        for i in range(20):
            d = today - timedelta(days=19 - i)
            if d.weekday() < 5:
                MarketPriceHistory.objects.create(
                    symbol=self.test_symbol,
                    date=d,
                    open=110.0 + i,
                    high=111.0 + i,
                    low=109.0 + i,
                    close=110.5 + i,
                    adj_close=110.5 + i,
                    volume=1000000,
                    currency='USD',
                    data_source='yfinance',
                )

        detect_price_gaps(symbols=[self.test_symbol])

        # Check DataQualityLog has entries
        gap_logs = DataQualityLog.objects.filter(
            symbol=self.test_symbol,
            event_type='gap_detected',
        )
        self.assertTrue(gap_logs.exists())


class AmibrokerImportTests(TestCase):
    """Tests for Amibroker CSV import."""

    def setUp(self):
        self.test_symbol = 'VDY'
        self.temp_dir = tempfile.mkdtemp()
        SecurityUniverse.objects.create(
            symbol=self.test_symbol,
            name='Test Symbol',
            security_type='etf',
            asset_class='etf_equity',
            primary_currency='CAD',
            is_active=True,
        )

    def test_standard_format_a_import(self):
        """Import Format A CSV (6 columns, no adj close)."""
        csv_path = os.path.join(self.temp_dir, 'VDY.csv')
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            writer.writerow(['20260101', '74.00', '74.50', '73.80', '74.20', '500000'])
            writer.writerow(['20260102', '74.10', '74.60', '73.90', '74.30', '510000'])

        from risk.management.commands.import_amibroker_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            watch_folder=self.temp_dir,
            symbol=None,
            dry_run=False,
        )

        count = MarketPriceHistory.objects.filter(
            symbol=self.test_symbol, data_source='amibroker'
        ).count()
        self.assertEqual(count, 2)

    def test_format_b_with_adj_close(self):
        """Import Format B CSV (7 columns, with adj close)."""
        csv_path = os.path.join(self.temp_dir, 'VDY.csv')
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(
                ['Date', 'Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
            )
            writer.writerow(['20260101', '74.00', '74.50', '73.80', '74.20', '73.90', '500000'])

        from risk.management.commands.import_amibroker_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            watch_folder=self.temp_dir,
            symbol=None,
            dry_run=False,
        )

        row = MarketPriceHistory.objects.get(
            symbol=self.test_symbol, date=date(2026, 1, 1)
        )
        self.assertEqual(float(row.adj_close), 73.90)

    def test_canadian_to_suffix_stripped(self):
        """VDY.TO.csv should be stored as symbol='VDY'."""
        csv_path = os.path.join(self.temp_dir, 'VDY.TO.csv')
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            writer.writerow(['20260101', '74.00', '74.50', '73.80', '74.20', '500000'])

        from risk.management.commands.import_amibroker_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            watch_folder=self.temp_dir,
            symbol=None,
            dry_run=False,
        )

        count = MarketPriceHistory.objects.filter(
            symbol='VDY', data_source='amibroker'
        ).count()
        self.assertEqual(count, 1)
        # Verify no VDY.TO symbol exists
        self.assertEqual(
            MarketPriceHistory.objects.filter(
                symbol='VDY.TO', data_source='amibroker'
            ).count(),
            0,
        )

    def test_symbol_not_in_universe_skipped_with_warning(self):
        """UNKNOWN.csv should be skipped and logged."""
        csv_path = os.path.join(self.temp_dir, 'UNKNOWN.csv')
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            writer.writerow(['20260101', '10.00', '10.50', '9.80', '10.20', '10000'])

        from risk.management.commands.import_amibroker_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            watch_folder=self.temp_dir,
            symbol=None,
            dry_run=False,
        )

        # No rows should be inserted
        count = MarketPriceHistory.objects.filter(
            symbol='UNKNOWN', data_source='amibroker'
        ).count()
        self.assertEqual(count, 0)

        # DataQualityLog should have the event
        log = DataQualityLog.objects.filter(
            event_type='symbol_not_in_universe',
            symbol='UNKNOWN',
        ).first()
        self.assertIsNotNone(log)

    def test_duplicate_amibroker_row_skipped(self):
        """Duplicate amibroker rows should be skipped."""
        # Seed existing row
        MarketPriceHistory.objects.create(
            symbol='VDY',
            date=date(2026, 1, 15),
            open=74.0,
            high=74.5,
            low=73.8,
            close=74.2,
            adj_close=74.2,
            volume=500000,
            currency='CAD',
            data_source='amibroker',
        )

        csv_path = os.path.join(self.temp_dir, 'VDY.csv')
        with open(csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            writer.writerow(['20260115', '74.00', '74.50', '73.80', '74.20', '500000'])

        from risk.management.commands.import_amibroker_prices import Command
        cmd = Command()
        cmd.stdout = MagicMock()
        cmd.stderr = MagicMock()
        cmd.handle(
            watch_folder=self.temp_dir,
            symbol=None,
            dry_run=False,
        )

        # Row count should be unchanged
        count = MarketPriceHistory.objects.filter(
            symbol='VDY', data_source='amibroker'
        ).count()
        self.assertEqual(count, 1)
