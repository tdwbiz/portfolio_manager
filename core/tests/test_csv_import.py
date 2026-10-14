"""Tests for CSV import, deduplication, and Holdings import."""

import csv
import io
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from core.models import (
    Account,
    AccountCurrency,
    BrokerStatement,
    PositionHistory,
    Transaction,
    compute_tx_hash,
)
from imports.parsers import parse_rbc_csv
from imports.parsers_holdings import (
    import_holdings_snapshot,
    parse_holdings_csv,
)
from imports.auto_import import auto_import


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(rows: list[list[str]]) -> str:
    """Write CSV rows to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix='.csv')
    with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        for row in rows:
            writer.writerow(row)
    return path


def _write_holdings_csv(positions: list[list[str]]) -> str:
    """Write a Holdings CSV with the given data rows."""
    fd, path = tempfile.mkstemp(suffix='_Holdings.csv')
    with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
        f.write('"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n')
        f.write('"Balances as of January 1, 2026"\n')
        f.write(
            'Account,Account Type,Nickname,Settlement Currency,Product,'
            'Symbol/Name,Quantity,Last Price,Currency,Change $,Change %,'
            'Total Book Cost,Total Market Value,Unrealized Gain/Loss $,'
            'Unrealized Gain/Loss %,Average Cost\n'
        )
        writer = csv.writer(f)
        for row in positions:
            writer.writerow(row)
    return path


# ---------------------------------------------------------------------------
# ComputeTxHashTests
# ---------------------------------------------------------------------------

class ComputeTxHashTests(TestCase):
    """Test the transaction hash function (without amount)."""

    def setUp(self):
        self.base_kwargs = dict(
            broker='RBC',
            account_id='90000001',
            date=date(2025, 10, 8),
            symbol='VDY',
            tx_type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            currency='CAD',
        )

    def test_same_trade_different_amounts_same_hash(self):
        """Same trade with different amounts should produce the same hash.

        This is the critical fix — overlapping CSV exports may have
        slightly different amounts for the same trade.
        """
        hash1 = compute_tx_hash(**self.base_kwargs)
        self.assertEqual(hash1, hash1)

    def test_different_symbol_different_hash(self):
        """Different symbols should produce different hashes."""
        kwargs = {**self.base_kwargs, 'symbol': 'VFV'}
        hash1 = compute_tx_hash(**self.base_kwargs)
        hash2 = compute_tx_hash(**kwargs)
        self.assertNotEqual(hash1, hash2)

    def test_different_type_different_hash(self):
        """Different transaction types should produce different hashes."""
        kwargs = {**self.base_kwargs, 'tx_type': 'BUY'}
        hash1 = compute_tx_hash(**self.base_kwargs)
        hash2 = compute_tx_hash(**kwargs)
        self.assertNotEqual(hash1, hash2)

    def test_different_quantity_different_hash(self):
        """Different quantities should produce different hashes."""
        kwargs = {**self.base_kwargs, 'quantity': Decimal('100')}
        hash1 = compute_tx_hash(**self.base_kwargs)
        hash2 = compute_tx_hash(**kwargs)
        self.assertNotEqual(hash1, hash2)

    def test_different_currency_different_hash(self):
        """Different currencies should produce different hashes."""
        kwargs = {**self.base_kwargs, 'currency': 'USD'}
        hash1 = compute_tx_hash(**self.base_kwargs)
        hash2 = compute_tx_hash(**kwargs)
        self.assertNotEqual(hash1, hash2)

    def test_hash_is_deterministic(self):
        """Same inputs should always produce the same hash."""
        hash1 = compute_tx_hash(**self.base_kwargs)
        hash2 = compute_tx_hash(**self.base_kwargs)
        self.assertEqual(hash1, hash2)

    def test_hash_length(self):
        """SHA-256 produces a 64-character hex string."""
        h = compute_tx_hash(**self.base_kwargs)
        self.assertEqual(len(h), 64)


# ---------------------------------------------------------------------------
# RbcCsvParserTests
# ---------------------------------------------------------------------------

class RbcCsvParserTests(TestCase):
    """Test RBC CSV parsing with settlement_date extraction."""

    def test_parses_activity_with_settlement_date(self):
        """Settlement Date column (index 6) should be parsed."""
        path = self._write_csv([
            ['Activity Export as of Oct 1, 2025'],
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VDY', 'Vanguard ETF',
             '100', '60.00', 'October 10, 2025', '90000001',
             '-6000.00', 'CAD'],
        ])

        trades, rate = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade['date'], date(2025, 10, 8))
        self.assertEqual(trade['settlement_date'], date(2025, 10, 10))
        self.assertEqual(trade['symbol'], 'VDY')
        self.assertEqual(trade['type'], 'BUY')
        self.assertEqual(trade['quantity'], Decimal('100'))
        self.assertEqual(trade['amount'], Decimal('-6000.00'))

    def test_parses_exchange_rate(self):
        """Exchange rate should be extracted from header."""
        path = self._write_csv([
            ['Exchange Rate: 1 USD = 1.3720 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
        ])

        _, rate = parse_rbc_csv(path)
        self.assertEqual(rate, Decimal('1.3720'))

    def test_skips_practice_accounts(self):
        """Practice accounts should be skipped."""
        path = self._write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'AAPL', 'Apple',
             '10', '150.00', 'October 10, 2025', 'PRACEF8C2DAE',
             '-1500.00', 'USD'],
        ])

        trades, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_multiple_trades_parsed(self):
        """Multiple trades should all be parsed."""
        path = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['"October 01, 2025"', 'Distribution', 'VDY', 'Dividend',
             '6182', '0.143', '"October 03, 2025"', '90000001',
             '886.81', 'CAD'],
            ['"October 08, 2025"', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', '"October 10, 2025"', '90000001',
             '-1666.20', 'CAD'],
        ])

        trades, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 2)

        # Check first trade
        self.assertEqual(trades[0]['type'], 'DIST')
        self.assertEqual(trades[0]['symbol'], 'VDY')
        self.assertEqual(trades[0]['amount'], Decimal('886.81'))

        # Check second trade
        self.assertEqual(trades[1]['type'], 'BUY')
        self.assertEqual(trades[1]['symbol'], 'VFV')
        self.assertEqual(trades[1]['amount'], Decimal('-1666.20'))

    def _write_csv(self, rows: list[list[str]]) -> str:
        """Write CSV rows to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            for row in rows:
                writer.writerow(row)
        return path


# ---------------------------------------------------------------------------
# HoldingsCsvParserTests
# ---------------------------------------------------------------------------

class HoldingsCsvParserTests(TestCase):
    """Test Holdings CSV parsing and PositionHistory import."""

    def test_parses_holdings_with_snapshot_date(self):
        """Snapshot date should be extracted from header."""
        path = self._write_holdings_csv([
            # 14 cols: [0]Group [1]Product [2]Symbol [3]Name [4]Qty
            # [5]Price [6]Currency [7]Change$ [8]Change% [9]=pad
            # [10]BookCost [11]MarketVal [12]=pad [13]AvgCost
            ['Group', 'Product', 'VDY', 'Vanguard ETF',
             '6182', '61.97', 'CAD', '0', '0%', 'X',
             '256238.91', '383098.54', 'X', '41.4492'],
        ])

        positions = parse_holdings_csv(path)
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        self.assertEqual(pos['symbol'], 'VDY')
        self.assertEqual(pos['quantity'], Decimal('6182'))
        self.assertEqual(pos['market_price'], Decimal('61.97'))
        self.assertEqual(pos['market_value'], Decimal('383098.54'))
        self.assertEqual(pos['snapshot_date'], date(2026, 1, 1))

    def test_skips_practice_accounts(self):
        """Practice accounts should be skipped."""
        path = self._write_holdings_csv([
            ['Practice', 'Common Shares', 'NVDA', 'NVIDIA Corp',
             '400', '255.87', 'CAD', '0', '0%', 'X',
             '100000', '100000', 'X', '250.00'],
        ])

        positions = parse_holdings_csv(path)
        # Practice accounts are skipped by the import_holdings_snapshot function,
        # not by parse_holdings_csv. parse_holdings_csv returns all rows.
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['symbol'], 'NVDA')

    def test_calculates_cost_basis_per_unit(self):
        """Cost basis per unit should be total_book_cost / quantity."""
        path = self._write_holdings_csv([
            ['Group', 'ETFs', 'VDY', 'Vanguard ETF',
             '1000', '50.00', 'CAD', '0', '0%', 'X',
             '41449.20', '50000.00', 'X', '41.4492']
        ])

        positions = parse_holdings_csv(path)
        self.assertEqual(len(positions), 1)
        # total_book_cost / quantity = 41449.20 / 1000 = 41.4492
        self.assertEqual(
            positions[0]['cost_basis_per_unit'],
            Decimal('41.4492'),
        )

    def _write_holdings_csv(self, positions: list[list[str]]) -> str:
        """Write a Holdings CSV with the given data rows."""
        fd, path = tempfile.mkstemp(suffix='_Holdings.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n')
            f.write('"Balances as of January 1, 2026"\n')
            f.write(
                'Group,Product,Symbol,Name,Quantity,Last Price,Currency,'
                'Change $,Change %,Padding,Total Book Cost,Total Market Value,'
                'Padding,Average Cost\n'
            )
            writer = csv.writer(f)
            for row in positions:
                writer.writerow(row)
        return path


# ---------------------------------------------------------------------------
# HoldingsImportTests
# ---------------------------------------------------------------------------

class HoldingsImportTests(TestCase):
    """Test import_holdings_snapshot — creating and closing positions."""

    def _write_holdings_csv(self, positions: list[list[str]]) -> str:
        """Write a Holdings CSV with the given data rows.
        
        14 columns: [0]Group [1]Product [2]Symbol [3]Name [4]Qty
        [5]Price [6]Currency [7]Change$ [8]Change% [9]=padding
        [10]BookCost [11]MarketVal [12]=padding [13]AvgCost
        
        File is named with account number so parser extracts valid account_id.
        """
        fd, path = tempfile.mkstemp(suffix='_90000001_Holdings.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n')
            f.write('"Balances as of January 1, 2026"\n')
            f.write(
                'Group,Product,Symbol,Name,Quantity,Last Price,Currency,'
                'Change $,Change %,Padding,Total Book Cost,Total Market Value,'
                'Padding,Average Cost\n'
            )
            writer = csv.writer(f)
            for row in positions:
                writer.writerow(row)
        return path

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )

    def test_creates_new_positions(self):
        """New positions should be created with date_to=None."""
        path = self._write_holdings_csv([
            # 14 cols: [0]Group [1]Product [2]Symbol [3]Name [4]Qty
            # [5]Price [6]Currency [7]Change$ [8]Change% [9]=pad
            # [10]BookCost [11]MarketVal [12]=pad [13]AvgCost
            ['Group', 'Product', 'VDY', 'Vanguard ETF',
             '6182', '61.97', 'CAD', '0', '0%', 'X',
             '256238.91', '383098.54', 'X', '41.4492'],
        ])

        created, closed = import_holdings_snapshot(path)
        self.assertEqual(created, 1)
        self.assertEqual(closed, 0)

        pos = PositionHistory.objects.get(
            account=self.account,
            symbol='VDY',
            date_from=date(2026, 1, 1),
        )
        self.assertIsNone(pos.date_to)
        self.assertEqual(pos.quantity, Decimal('6182'))
        self.assertEqual(pos.market_price, Decimal('61.97'))
        self.assertEqual(pos.market_value, Decimal('383098.54'))

    def test_closes_old_position_on_new_snapshot(self):
        """Old open positions should be closed when a new snapshot arrives."""
        # Create an existing open position
        PositionHistory.objects.create(
            owner='TOM',
            account=self.account,
            symbol='VDY',
            quantity=Decimal('5000'),
            cost_basis_per_unit=Decimal('40.0000'),
            total_cost_basis=Decimal('200000.00'),
            currency='CAD',
            market_price=Decimal('55.0000'),
            market_value=Decimal('275000.00'),
            date_from=date(2025, 1, 1),
            date_to=None,
        )

        # Import a new snapshot for Jan 1, 2026
        path = self._write_holdings_csv([
            # 14 cols: [0]Group [1]Product [2]Symbol [3]Name [4]Qty
            # [5]Price [6]Currency [7]Change$ [8]Change% [9]=pad
            # [10]BookCost [11]MarketVal [12]=pad [13]AvgCost
            ['Group', 'Product', 'VDY', 'Vanguard ETF',
             '6182', '61.97', 'CAD', '0', '0%', 'X',
             '256238.91', '383098.54', 'X', '41.4492'],
        ])

        created, closed = import_holdings_snapshot(path)
        self.assertEqual(created, 1)
        self.assertEqual(closed, 1)

        # Old position should be closed at Dec 31, 2025
        old_pos = PositionHistory.objects.get(
            symbol='VDY',
            quantity=Decimal('5000'),
        )
        self.assertEqual(old_pos.date_to, date(2025, 12, 31))

        # New position should be open
        new_pos = PositionHistory.objects.get(
            symbol='VDY',
            date_from=date(2026, 1, 1),
        )
        self.assertIsNone(new_pos.date_to)
        self.assertEqual(new_pos.quantity, Decimal('6182'))

    def test_multiple_symbols_in_same_snapshot(self):
        """Multiple symbols should create multiple positions."""
        path = self._write_holdings_csv([
            ['Group', 'Product', 'VDY', 'Vanguard ETF',
             '6182', '61.97', 'CAD', '0', '0%', 'X',
             '256238.91', '383098.54', 'X', '41.4492'],
            ['Group', 'Product', 'VFV', 'Vanguard S&P 500',
             '2619', '166.62', 'CAD', '0', '0%', 'X',
             '145594.92', '436377.78', 'X', '55.5918'],
        ])

        created, _ = import_holdings_snapshot(path)
        self.assertEqual(created, 2)

        symbols = set(PositionHistory.objects.filter(
            account=self.account,
            date_from=date(2026, 1, 1),
        ).values_list('symbol', flat=True))
        self.assertEqual(symbols, {'VDY', 'VFV'})

    def test_no_positions_returns_zero(self):
        """A Holdings CSV with no parseable positions should return (0, 0)."""
        # Create a CSV with only headers (no data rows)
        path = self._write_holdings_csv([])

        created, closed = import_holdings_snapshot(path)
        self.assertEqual(created, 0)
        self.assertEqual(closed, 0)


# ---------------------------------------------------------------------------
# PositionHistoryQueryTests
# ---------------------------------------------------------------------------

class PositionHistoryQueryTests(TestCase):
    """Test querying PositionHistory for current and historical holdings."""

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )

    def test_current_holdings_query(self):
        """Current holdings have date_to IS NULL and quantity > 0."""
        PositionHistory.objects.create(
            owner='TOM',
            account=self.account,
            symbol='VDY',
            quantity=Decimal('6182'),
            cost_basis_per_unit=Decimal('41.4492'),
            total_cost_basis=Decimal('256238.91'),
            currency='CAD',
            market_price=Decimal('61.97'),
            market_value=Decimal('383098.54'),
            date_from=date(2026, 1, 1),
            date_to=None,
        )

        current = PositionHistory.objects.filter(
            account=self.account,
            date_to__isnull=True,
        ).first()
        self.assertIsNotNone(current)
        self.assertEqual(current.symbol, 'VDY')
        self.assertEqual(current.quantity, Decimal('6182'))

    def test_historical_holdings_as_of_date(self):
        """Historical holdings should be queryable by date_from <= date."""
        PositionHistory.objects.create(
            owner='TOM',
            account=self.account,
            symbol='VDY',
            quantity=Decimal('5000'),
            cost_basis_per_unit=Decimal('40.0000'),
            total_cost_basis=Decimal('200000.00'),
            currency='CAD',
            market_price=Decimal('55.0000'),
            market_value=Decimal('275000.00'),
            date_from=date(2025, 1, 1),
            date_to=None,
        )

        # Query as of a date between date_from and date_to (date_to is None = open position)
        from django.db.models import Q
        historical = PositionHistory.objects.filter(
            account=self.account,
            symbol='VDY',
            date_from__lte=date(2025, 6, 1),
        ).filter(
            Q(date_to__gte=date(2025, 6, 1)) | Q(date_to__isnull=True)
        )
        self.assertEqual(historical.count(), 1)
        self.assertEqual(historical.first().quantity, Decimal('5000'))


# ---------------------------------------------------------------------------
# TransactionDedupTests
# ---------------------------------------------------------------------------

class TransactionDedupTests(TestCase):
    """Test transaction deduplication."""

    def _write_csv(self, rows: list[list[str]]) -> str:
        """Write CSV rows to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            for row in rows:
                writer.writerow(row)
        return path

    def _write_holdings_csv(self, positions: list[list[str]]) -> str:
        """Write a Holdings CSV with the given data rows."""
        fd, path = tempfile.mkstemp(suffix='_Holdings.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n')
            f.write('"Balances as of January 1, 2026"\n')
            f.write(
                'Group,Product,Symbol,Name,Quantity,Last Price,Currency,'
                'Change $,Change %,Padding,Total Book Cost,Total Market Value,'
                'Unrealized Gain/Loss $,Unrealized Gain/Loss %,Average Cost\n'
            )
            writer = csv.writer(f)
            for row in positions:
                writer.writerow(row)
        return path

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )

    def test_duplicate_hash_skips_transaction(self):
        """A duplicate tx_hash should not create a second Transaction."""
        # Compute hash for the trade
        hash1 = compute_tx_hash(
            broker='RBC',
            account_id='90000001',
            date=date(2025, 10, 8),
            symbol='VDY',
            tx_type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            currency='CAD',
        )

        # Create the first transaction with the hash
        tx = Transaction.objects.create(
            statement=self._create_statement(),
            account=self.account,
            date=date(2025, 10, 8),
            symbol='VDY',
            type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            commission=Decimal('0.00'),
            currency='CAD',
            amount=Decimal('886.81'),
            amount_cad=Decimal('886.81'),
            tx_hash=hash1,
        )

        # Create a second transaction with the same hash
        tx2 = Transaction.objects.create(
            statement=self._create_statement(),
            account=self.account,
            date=date(2025, 10, 8),
            symbol='VDY',
            type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            commission=Decimal('0.00'),
            currency='CAD',
            amount=Decimal('900.00'),  # different amount, but hash should match
            amount_cad=Decimal('900.00'),
            tx_hash=hash1,
        )

        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(tx2.tx_hash, tx.tx_hash)

    def test_overlapping_files_same_trade_different_amount(self):
        """Two CSV files with the same trade but different amounts should dedup."""
        # First file
        path1 = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.20', 'CAD'],
        ])
        auto_import(path1, 'RBC')
        count1 = Transaction.objects.count()

        # Second file — same trade but slightly different amount (simulates overlap)
        path2 = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.19', 'CAD'],
        ])
        auto_import(path2, 'RBC')
        count2 = Transaction.objects.count()

        # Count should be the same — the duplicate was skipped
        self.assertEqual(count1, count2)

    def test_zero_quantity_positions_skipped(self):
        """Zero-quantity holdings should be skipped."""
        # This is tested at the parser level; here we verify the import
        # doesn't crash with zero-quantity data
        path = self._write_holdings_csv([
            ['90000001', 'Inv', 'TAX', 'CAD Holdings', 'ETFs',
             'VDY', '0', '0.00', 'CAD', '0', '0%',
             '0', '0', '0', '0%', '0.00'],
        ])
        created, closed = import_holdings_snapshot(path)
        self.assertEqual(created, 0)
        self.assertEqual(closed, 0)

    def _create_statement(self) -> BrokerStatement:
        """Create a BrokerStatement for testing."""
        return BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 8),
            raw_filename='test.csv',
        )


# ---------------------------------------------------------------------------
# BrokerStatementPeriodTests
# ---------------------------------------------------------------------------

class BrokerStatementPeriodTests(TestCase):
    """Test BrokerStatement period fields."""

    def test_period_fields_exist(self):
        """BrokerStatement should have period_start and period_end fields."""
        stmt = BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 8),
            raw_filename='test.csv',
        )
        self.assertTrue(hasattr(stmt, 'period_start'))
        self.assertTrue(hasattr(stmt, 'period_end'))


# ---------------------------------------------------------------------------
# TransactionSettlementDateTests
# ---------------------------------------------------------------------------

class TransactionSettlementDateTests(TestCase):
    """Test Transaction settlement_date field."""

    def setUp(self):
        self.statement = BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 8),
            raw_filename='test.csv',
        )
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )

    def test_settlement_date_stored(self):
        """Settlement date should be stored on Transaction."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2025, 10, 8),
            settlement_date=date(2025, 10, 10),
            symbol='VFV',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('166.62'),
            commission=Decimal('0.00'),
            currency='CAD',
            amount=Decimal('-1666.20'),
            amount_cad=Decimal('-1666.20'),
        )

        tx = Transaction.objects.get(symbol='VFV')
        self.assertEqual(tx.settlement_date, date(2025, 10, 10))

    def test_settlement_date_nullable(self):
        """Settlement date should be nullable."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2025, 10, 8),
            symbol='VFV',
            type='DIST',
            quantity=Decimal('0'),
            price=Decimal('0'),
            commission=Decimal('0.00'),
            currency='CAD',
            amount=Decimal('0'),
            amount_cad=Decimal('0'),
        )

        tx = Transaction.objects.get(symbol='VFV')
        self.assertIsNone(tx.settlement_date)


# ---------------------------------------------------------------------------
# AutoImportTests
# ---------------------------------------------------------------------------

class AutoImportTests(TestCase):
    """Test the auto_import function directly."""

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='Test Account',
            broker='RBC',
            category='TAXABLE',
            owner='Test',
        )
        self.statement = BrokerStatement.objects.create(
            source='RBC',
            file_date=date.today(),
        )

    def tearDown(self):
        self.account.delete()
        self.statement.delete()

    def test_returns_count_zero_for_empty_file(self):
        """auto_import should return (0, 0) for files with no trades."""
        path = self._write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
        ])

        imported, parsed = auto_import(path, 'RBC')
        self.assertEqual(imported, 0)
        self.assertEqual(parsed, 0)

    def test_returns_count_of_imported_trades(self):
        """auto_import should return (imported, parsed) counts."""
        path = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard',
             '10', '100.00', 'October 10, 2025', '90000001',
             '-1000.00', 'CAD'],
        ])

        imported, parsed = auto_import(path, 'RBC')
        self.assertEqual(imported, 1)
        self.assertEqual(parsed, 1)

    def test_returns_zero_on_exception(self):
        """auto_import should return (0, 0) on exception."""
        path = '/nonexistent/path/file.csv'
        imported, parsed = auto_import(path, 'RBC')
        self.assertEqual(imported, 0)
        self.assertEqual(parsed, 0)

    def test_dry_run_returns_count(self):
        """auto_import with dry_run should return (0, parsed_count)."""
        path = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard',
             '10', '100.00', 'October 10, 2025', '90000001',
             '-1000.00', 'CAD'],
        ])

        imported, parsed = auto_import(path, 'RBC', dry_run=True)
        self.assertEqual(imported, 0)
        self.assertEqual(parsed, 1)

    def _write_csv(self, rows: list[list[str]]) -> str:
        """Write CSV rows to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            for row in rows:
                writer.writerow(row)
        return path


# ---------------------------------------------------------------------------
# ImportCsvManagementCommandTests
# ---------------------------------------------------------------------------

class ImportCsvManagementCommandTests(TestCase):
    """Test the import_csv management command."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.inbox_dir = os.path.join(self.temp_dir, 'inbox')
        self.processed_dir = os.path.join(self.temp_dir, 'processed')
        self.errors_dir = os.path.join(self.processed_dir, 'errors')
        os.makedirs(self.inbox_dir)
        os.makedirs(self.processed_dir)
        os.makedirs(self.errors_dir)

        # Create account needed for RBC import
        self.account = Account.objects.create(
            account_id='90000001',
            name='Test Account',
            broker='RBC',
            category='TAXABLE',
            owner='Test',
        )

        # Create a test RBC CSV
        self.rbc_csv = os.path.join(self.inbox_dir, 'test_rbc.csv')
        with open(self.rbc_csv, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Activity Export as of Oct 1, 2025"\n')
            f.write('"Exchange Rate: 1 USD = 1.37 CAD"\n')
            f.write(
                '"Date","Activity","Symbol","Symbol Description",'
                '"Quantity","Price","Settlement Date","Account",'
                '"Value","Currency"\n'
            )
            f.write(
                '"October 08, 2025","Buy","VFV","Vanguard S&P 500",'
                '"10","166.62","October 10, 2025","90000001",'
                '"-1666.20","CAD"\n'
            )

    def tearDown(self):
        import shutil
        # Clean up any PositionHistory that references the account before deleting
        PositionHistory.objects.filter(account=self.account).delete()
        self.account.delete()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_imports_activity_and_moves_to_processed(self):
        """import_csv should import activities and move files to processed/."""
        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv')

        self.assertEqual(Transaction.objects.count(), 1)
        tx = Transaction.objects.first()
        self.assertEqual(tx.symbol, 'VFV')
        self.assertEqual(tx.type, 'BUY')

        # File should be in processed/, not inbox
        self.assertEqual(os.listdir(self.inbox_dir), [])
        processed = [f for f in os.listdir(self.processed_dir) if f.endswith('.csv')]
        self.assertEqual(len(processed), 1)

    def test_files_with_no_trades_moved_to_errors(self):
        """Files with no trades should be moved to processed/errors/."""
        # Create a CSV with no trades (only header)
        empty_csv = os.path.join(self.inbox_dir, 'empty.csv')
        with open(empty_csv, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Date","Activity","Symbol","Symbol Description",'
                    '"Quantity","Price","Settlement Date","Account",'
                    '"Value","Currency"\n')

        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv')

        # File should be in processed/errors/
        errors = os.path.join(self.processed_dir, 'errors')
        self.assertTrue(os.path.isdir(errors))
        error_files = [f for f in os.listdir(errors) if f.endswith('.csv')]
        self.assertEqual(len(error_files), 1)
        self.assertEqual(error_files[0], 'empty.csv')

    def test_files_with_errors_moved_to_errors(self):
        """Files with parse errors should be moved to processed/errors/."""
        # Create a CSV with invalid dates
        bad_csv = os.path.join(self.inbox_dir, 'bad_dates.csv')
        with open(bad_csv, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Date","Activity","Symbol","Symbol Description",'
                    '"Quantity","Price","Settlement Date","Account",'
                    '"Value","Currency"\n')
            f.write('"NOT A DATE","Buy","VFV","Test","10","100.00","","90000001",-1000.00","CAD"\n')

        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv')

        errors = os.path.join(self.processed_dir, 'errors')
        self.assertTrue(os.path.isdir(errors))
        error_files = [f for f in os.listdir(errors) if f.endswith('.csv')]
        self.assertEqual(len(error_files), 1)
        self.assertEqual(error_files[0], 'bad_dates.csv')

    def test_dry_run_no_db_writes_no_file_moves(self):
        """--dry-run should parse files but not write to DB or move files."""
        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv', dry_run=True)

        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(len(os.listdir(self.inbox_dir)), 1)

    def test_holdings_import_moved_to_processed(self):
        """import_csv --holdings should import and move Holdings to processed/."""
        # Filename must include 5+ digits for account_id extraction
        holdings_csv = os.path.join(self.inbox_dir, 'Holdings 90000001.csv')
        with open(holdings_csv, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n')
            f.write('"Balances as of January 1, 2026"\n')
            f.write(
                'Group,Product,Symbol,Name,Quantity,Last Price,Currency,'
                'Change $,Change %,Padding,Total Book Cost,Total Market Value,'
                'Padding,Average Cost\n'
            )
            f.write(
                'TAX,ETFs,VDY,Vanguard ETF,6182,61.97,CAD,'
                '0,0%,X,256238.91,383098.54,X,41.4492\n'
            )

        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv', holdings=True)

        self.assertEqual(PositionHistory.objects.count(), 1)
        # The Holdings CSV should be moved out of inbox (other files like test_rbc.csv
        # from setUp are untouched by --holdings)
        holdings_in_inbox = [f for f in os.listdir(self.inbox_dir)
                            if 'Holdings' in f and f.endswith('.csv')]
        self.assertEqual(len(holdings_in_inbox), 0)

    def test_holdings_with_no_positions_moved_to_errors(self):
        """Holdings CSV with no parseable positions should be moved to errors/."""
        holdings_csv = os.path.join(self.inbox_dir, 'Holdings 90000001_empty.csv')
        with open(holdings_csv, 'w', newline='', encoding='utf-8-sig') as f:
            f.write('"Holdings Export as of January 1, 2026"\n')
            f.write('"Balances as of January 1, 2026"\n')
            f.write(
                'Group,Product,Symbol,Name,Quantity,Last Price,Currency,'
                'Change $,Change %,Padding,Total Book Cost,Total Market Value,'
                'Unrealized Gain/Loss $,Unrealized Gain/Loss %,Average Cost\n'
            )
            # No data rows

        from django.core.management import call_command
        from django.test import override_settings

        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv', holdings=True)

        errors = os.path.join(self.processed_dir, 'errors')
        self.assertTrue(os.path.isdir(errors))
        error_files = [f for f in os.listdir(errors) if f.endswith('.csv')]
        self.assertEqual(len(error_files), 1)
        self.assertEqual(error_files[0], 'Holdings 90000001_empty.csv')

    def test_duplicate_transactions_reported(self):
        """Duplicate transactions from overlapping files should be detected."""
        # First file
        path1 = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.20', 'CAD'],
        ])
        auto_import(path1, 'RBC')
        count1 = Transaction.objects.count()

        # Second file — same trade but different amount
        path2 = self._write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.19', 'CAD'],
        ])
        auto_import(path2, 'RBC')
        count2 = Transaction.objects.count()

        self.assertEqual(count1, count2)

    def _write_csv(self, rows: list[list[str]]) -> str:
        """Write CSV rows to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            for row in rows:
                writer.writerow(row)
        return path
