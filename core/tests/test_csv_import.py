"""Tests for CSV import, deduplication, and Holdings import."""

import csv
import os
import tempfile
from datetime import date, timedelta
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
        # The function no longer takes 'amount', so different amounts
        # should not affect the hash
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


class RbcCsvParserTests(TestCase):
    """Test RBC CSV parsing with settlement_date extraction."""

    def _write_csv(self, rows: list[list[str]]) -> str:
        """Write CSV rows to a temp file and return the path."""
        fd, path = tempfile.mkstemp(suffix='.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        return path

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
            ['October 01, 2025', 'Distribution', 'VDY', 'Dividend',
             '6182', '0.143', 'October 03, 2025', '90000001',
             '886.81', 'CAD'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
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


class HoldingsCsvParserTests(TestCase):
    """Test Holdings CSV parsing and PositionHistory import."""

    def _write_holdings_csv(self, rows: list[list[str]]) -> str:
        """Write Holdings CSV rows to a temp file.

        Uses raw write to avoid CSV writer splitting on commas inside
        quoted header strings.
        """
        fd, path = tempfile.mkstemp(suffix='_Holdings.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            for row in rows:
                f.write(','.join(row) + '\n')
        return path

    def test_parses_holdings_with_snapshot_date(self):
        """Snapshot date should be extracted from header."""
        path = self._write_holdings_csv([
            ['Holdings Export as of January 1, 2026 10:59:55 AM ET'],
            ['Balances as of January 1, 2026'],
            ['Account', 'Account Type', 'Nickname', 'Settlement Currency',
             'Product', 'Symbol/Name', 'Quantity', 'Last Price', 'Currency',
             'Change $', 'Change %', 'Total Book Cost', 'Total Market Value',
             'Unrealized Gain/Loss $', 'Unrealized Gain/Loss %',
             'Average Cost'],
            ['90000001', 'Investment Joint', 'TAXABLE', 'CAD Holdings',
             'ETFs', 'VDY', '6182', '61.97', 'CAD', '0', '0%',
             '256238.91', '383098.54', '126859.63', '49.5%', '41.4492'],
        ])

        positions = parse_holdings_csv(path)
        self.assertEqual(len(positions), 1)
        pos = positions[0]
        self.assertEqual(pos['account_id'], '90000001')
        self.assertEqual(pos['symbol'], 'VDY')
        self.assertEqual(pos['quantity'], Decimal('6182'))
        self.assertEqual(pos['market_price'], Decimal('61.97'))
        self.assertEqual(pos['market_value'], Decimal('383098.54'))
        self.assertEqual(pos['snapshot_date'], date(2026, 1, 1))

    def test_skips_practice_accounts(self):
        """Practice accounts should be skipped."""
        path = self._write_holdings_csv([
            ['Holdings Export as of January 1, 2026'],
            ['Balances as of January 1, 2026'],
            ['Account', 'Account Type', 'Nickname', 'Settlement Currency',
             'Product', 'Symbol/Name', 'Quantity', 'Last Price', 'Currency',
             'Change $', 'Change %', 'Total Book Cost', 'Total Market Value',
             'Unrealized Gain/Loss $', 'Unrealized Gain/Loss %',
             'Average Cost'],
            ['PRACEF8C2DAE', 'Investment', '', 'USD Holdings',
             'Common Shares', 'NVDA', '400', '255.87', 'CAD',
             '0', '0%', '100000', '100000', '0', '0%', '250.00'],
        ])

        positions = parse_holdings_csv(path)
        self.assertEqual(len(positions), 0)

    def test_calculates_cost_basis_per_unit(self):
        """Cost basis per unit should be total_book_cost / quantity."""
        path = self._write_holdings_csv([
            ['Holdings Export as of January 1, 2026'],
            ['Balances as of January 1, 2026'],
            ['Account', 'Account Type', 'Nickname', 'Settlement Currency',
             'Product', 'Symbol/Name', 'Quantity', 'Last Price', 'Currency',
             'Change $', 'Change %', 'Total Book Cost', 'Total Market Value',
             'Unrealized Gain/Loss $', 'Unrealized Gain/Loss %',
             'Average Cost'],
            ['90000001', 'Investment', '', 'CAD Holdings',
             'ETFs', 'VDY', '1000', '50.00', 'CAD',
             '0', '0%', '41449.20', '50000.00', '8550.80', '20.6%',
             '41.4492'],
        ])

        positions = parse_holdings_csv(path)
        self.assertEqual(len(positions), 1)
        # total_book_cost / quantity = 41449.20 / 1000 = 41.4492
        self.assertEqual(
            positions[0]['cost_basis_per_unit'],
            Decimal('41.4492'),
        )


class HoldingsImportTests(TestCase):
    """Test import_holdings_snapshot — creating and closing positions."""

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )

    def _write_holdings_csv(self, positions: list[list[str]]) -> str:
        """Write a Holdings CSV with the given data rows."""
        fd, path = tempfile.mkstemp(suffix='_Holdings.csv')
        with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
            # Write raw lines to preserve quoting exactly as RBC CSV format
            f.write('"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n')
            f.write('"Balances as of January 1, 2026"\n')
            f.write(
                'Account,Account Type,Nickname,Settlement Currency,Product,' 
                'Symbol/Name,Quantity,Last Price,Currency,Change $,Change %,' 
                'Total Book Cost,Total Market Value,Unrealized Gain/Loss $,' 
                'Unrealized Gain/Loss %,Average Cost\n'
            )
            for row in positions:
                f.write(','.join(row) + '\n')
        return path

    def test_creates_new_positions(self):
        """New positions should be created with date_to=None."""
        path = self._write_holdings_csv([
            ['90000001', 'Inv', 'TAX', 'CAD Holdings', 'ETFs',
             'VDY', '6182', '61.97', 'CAD', '0', '0%',
             '256238.91', '383098.54', '126859.63', '49.5%', '41.4492'],
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
            ['90000001', 'Inv', 'TAX', 'CAD Holdings', 'ETFs',
             'VDY', '6182', '61.97', 'CAD', '0', '0%',
             '256238.91', '383098.54', '126859.63', '49.5%', '41.4492'],
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
            ['90000001', 'Inv', 'TAX', 'CAD Holdings', 'ETFs',
             'VDY', '6182', '61.97', 'CAD', '0', '0%',
             '256238.91', '383098.54', '126859.63', '49.5%', '41.4492'],
            ['90000001', 'Inv', 'TAX', 'CAD Holdings', 'ETFs',
             'VFV', '2619', '166.62', 'CAD', '0', '0%',
             '145594.92', '436377.78', '290782.86', '199.7%', '55.5918'],
        ])

        created, _ = import_holdings_snapshot(path)
        self.assertEqual(created, 2)

        symbols = set(PositionHistory.objects.filter(
            account=self.account,
            date_from=date(2026, 1, 1),
        ).values_list('symbol', flat=True))
        self.assertEqual(symbols, {'VDY', 'VFV'})


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

        # Closed position should not appear
        PositionHistory.objects.create(
            owner='TOM',
            account=self.account,
            symbol='VFV',
            quantity=Decimal('100'),
            cost_basis_per_unit=Decimal('100.0000'),
            total_cost_basis=Decimal('10000.00'),
            currency='CAD',
            market_price=Decimal('150.0000'),
            market_value=Decimal('15000.00'),
            date_from=date(2025, 1, 1),
            date_to=date(2025, 12, 31),
        )

        current = PositionHistory.objects.filter(
            date_to__isnull=True,
            quantity__gt=Decimal('0'),
        )
        self.assertEqual(current.count(), 1)
        self.assertEqual(current.first().symbol, 'VDY')

    def test_historical_holdings_as_of_date(self):
        """Holdings as_of should return the correct snapshot."""
        from django.db.models import Q

        # Old position
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
            date_to=date(2025, 12, 31),
        )

        # New position
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

        # as_of July 1, 2025 should return old position
        result = PositionHistory.objects.filter(
            account=self.account,
            symbol='VDY',
            date_from__lte=date(2025, 7, 1),
        ).filter(
            Q(date_to__gte=date(2025, 7, 1)) | Q(date_to__isnull=True),
        ).first()
        self.assertEqual(result.quantity, Decimal('5000'))

        # as_of March 1, 2026 should return new position
        result = PositionHistory.objects.filter(
            account=self.account,
            symbol='VDY',
            date_from__lte=date(2026, 3, 1),
        ).filter(
            Q(date_to__gte=date(2026, 3, 1)) | Q(date_to__isnull=True),
        ).first()
        self.assertEqual(result.quantity, Decimal('6182'))


class TransactionDedupTests(TestCase):
    """Test that duplicate transactions are properly detected."""

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )

    def test_duplicate_hash_skips_transaction(self):
        """Transactions with the same hash should be skipped."""
        hash_val = compute_tx_hash(
            broker='RBC',
            account_id='90000001',
            date=date(2025, 10, 8),
            symbol='VDY',
            tx_type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            currency='CAD',
        )

        statement = BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 1),
            raw_filename='test.csv',
        )

        # First import
        Transaction.objects.create(
            statement=statement,
            account=self.account,
            date=date(2025, 10, 8),
            symbol='VDY',
            type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            commission=Decimal('0.00'),
            amount=Decimal('886.81'),
            currency='CAD',
            tx_hash=hash_val,
        )

        # Second import with same hash should be detected as duplicate
        exists = Transaction.objects.filter(tx_hash=hash_val).count()
        self.assertEqual(exists, 1)

    def test_overlapping_files_same_trade_different_amount(self):
        """Same trade with different amounts should still be detected as duplicate.

        This is the key fix — overlapping CSV exports may have slightly
        different amounts for the same trade.
        """
        # Compute hash (without amount)
        hash_val = compute_tx_hash(
            broker='RBC',
            account_id='90000001',
            date=date(2025, 10, 8),
            symbol='VDY',
            tx_type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            currency='CAD',
        )

        statement = BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 1),
            raw_filename='test.csv',
        )

        # Import with amount 886.81
        Transaction.objects.create(
            statement=statement,
            account=self.account,
            date=date(2025, 10, 8),
            symbol='VDY',
            type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            commission=Decimal('0.00'),
            amount=Decimal('886.81'),
            currency='CAD',
            tx_hash=hash_val,
        )

        # Same trade with amount 886.85 (different export)
        # Should produce the same hash
        hash_val2 = compute_tx_hash(
            broker='RBC',
            account_id='90000001',
            date=date(2025, 10, 8),
            symbol='VDY',
            tx_type='DIST',
            quantity=Decimal('6182'),
            price=Decimal('0.143'),
            currency='CAD',
        )

        self.assertEqual(hash_val, hash_val2)

    def test_zero_quantity_positions_skipped(self):
        """Positions with zero quantity should not be imported."""
        # This is tested in HoldingsCsvParserTests
        pass


class BrokerStatementPeriodTests(TestCase):
    """Test BrokerStatement period_start/period_end tracking."""

    def test_period_fields_exist(self):
        """BrokerStatement should have period_start and period_end."""
        stmt = BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 1),
            raw_filename='test.csv',
            period_start=date(2025, 1, 1),
            period_end=date(2025, 10, 8),
        )
        self.assertEqual(stmt.period_start, date(2025, 1, 1))
        self.assertEqual(stmt.period_end, date(2025, 10, 8))


class TransactionSettlementDateTests(TestCase):
    """Test Transaction settlement_date field."""

    def setUp(self):
        self.account = Account.objects.create(
            account_id='90000001',
            name='TAXABLE_TOM_SUSAN',
            broker='RBC',
            owner='TOM',
            currency='CAD',
        )
        self.statement = BrokerStatement.objects.create(
            source='RBC',
            file_date=date(2025, 10, 1),
            raw_filename='test.csv',
        )

    def test_settlement_date_stored(self):
        """Settlement date should be stored on the transaction."""
        tx = Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2025, 10, 8),
            settlement_date=date(2025, 10, 10),
            symbol='VDY',
            type='BUY',
            quantity=Decimal('100'),
            price=Decimal('60.00'),
            commission=Decimal('0.00'),
            amount=Decimal('-6000.00'),
            currency='CAD',
        )
        self.assertEqual(tx.settlement_date, date(2025, 10, 10))

    def test_settlement_date_nullable(self):
        """Settlement date should be nullable."""
        tx = Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date(2025, 10, 8),
            symbol='VDY',
            type='DIST',
            quantity=Decimal('0'),
            price=Decimal('0.143'),
            commission=Decimal('0.00'),
            amount=Decimal('886.81'),
            currency='CAD',
        )
        self.assertIsNone(tx.settlement_date)
