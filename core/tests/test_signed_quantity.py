"""Dry-run fix C: quantity is stored signed, exactly as the CSV gives it.

Every reader of Transaction.quantity that assumed a positive quantity must
give the same results for BUY and SELL — a SELL of -10 reduces a position
by 10 and leaves realised P&L unchanged. One test per changed reader.
"""

import importlib.util
import os
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, Client

from core.models import (
    Account,
    BrokerStatement,
    LedgerAccount,
    LedgerEntry,
    PositionHistory,
    Transaction,
)
from core.utils.portfolio import (
    compute_cost_basis_series,
    compute_positions,
    compute_portfolio_value_series,
    compute_realised_pnl,
    compute_series_by_account,
)


def _make_tx(account, statement, symbol, tx_type, quantity, price, amount_cad,
             date_str, tx_hash):
    return Transaction.objects.create(
        account=account,
        statement=statement,
        symbol=symbol,
        type=tx_type,
        quantity=quantity,
        price=price,
        commission=Decimal('0'),
        amount=amount_cad,
        currency='CAD',
        exchange_rate_cad=Decimal('1.0'),
        amount_cad=amount_cad,
        date=date_str,
        tx_hash=tx_hash,
    )


class SignedQuantityBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.account = Account.objects.create(name='ACCOUNT_A', broker='RBC')
        cls.statement = BrokerStatement.objects.create(
            source='RBC', file_date='2026-01-05', raw_filename='t.csv')


class ComputePortfolioValueSeriesTests(SignedQuantityBase):
    def test_sell_negative_reduces_position(self):
        """BUY +10, SELL -4 → 6 shares valued at the price (not 14)."""
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('200.00'), Decimal('-2000.00'), '2026-01-05', 'v1')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-4'),
                 Decimal('250.00'), Decimal('1000.00'), '2026-02-10', 'v2')

        idx = pd.date_range('2026-01-01', '2026-03-31', freq='D')
        prices_df = pd.DataFrame(
            {'AAPL': [100.0] * len(idx), 'USDCAD=X': [1.0] * len(idx)},
            index=idx)

        from unittest.mock import patch
        with patch('core.utils.portfolio.load_prices_from_db',
                   return_value=prices_df):
            results = compute_portfolio_value_series(
                date_from='2026-02-01', date_to='2026-03-31')

        self.assertTrue(results)
        # Every point after the SELL values 6 shares at 100 × fx 1.
        for point in results:
            self.assertEqual(point['market_value_cad'], Decimal('600'))


class ComputeRealisedPnlTests(SignedQuantityBase):
    def test_sell_negative_realised_pnl_unchanged(self):
        """A SELL of -10 sells 10 shares; FIFO P&L is the same as with a
        positive quantity (2500 proceeds − 2000 cost = 500)."""
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('200.00'), Decimal('-2000.00'), '2026-01-05', 'p1')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-10'),
                 Decimal('250.00'), Decimal('2500.00'), '2026-02-10', 'p2')

        trades = compute_realised_pnl(account_id='ACCOUNT_A')

        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade['qty_sold'], Decimal('10'))
        self.assertEqual(trade['sell_amount_cad'], Decimal('2500.00'))
        self.assertEqual(trade['cost_basis_fifo'], Decimal('2000.00'))
        self.assertEqual(trade['realised_pnl_fifo'], Decimal('500.00'))
        self.assertEqual(trade['realised_pnl_avg'], Decimal('500.00'))


class ComputeCostBasisSeriesTests(SignedQuantityBase):
    def test_sell_negative_reduces_cost_basis(self):
        """BUY +10 @200 then SELL -10 → cost basis goes 2000 → 0."""
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('200.00'), Decimal('-2000.00'), '2026-01-05', 'c1')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-10'),
                 Decimal('250.00'), Decimal('2500.00'), '2026-02-10', 'c2')

        results = compute_cost_basis_series(date_to='2026-03-31')

        by_date = {p['date']: p['cost_basis_cad'] for p in results}
        self.assertEqual(by_date.get('2026-01-31'), Decimal('2000.00'))
        self.assertEqual(by_date.get('2026-02-28'), Decimal('0.00'))


class ComputeSeriesByAccountTests(SignedQuantityBase):
    def setUp(self) -> None:
        super().setUp()
        cache.clear()

    def tearDown(self) -> None:
        cache.clear()
        super().tearDown()

    def test_sell_negative_reduces_position(self):
        """BUY +10, SELL -4 → 6 shares × 100 = 600 for the account."""
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('200.00'), Decimal('-2000.00'), '2026-01-05', 'a1')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-4'),
                 Decimal('250.00'), Decimal('1000.00'), '2026-02-10', 'a2')

        idx = pd.date_range('2026-01-01', '2026-03-31', freq='D')
        prices_df = pd.DataFrame(
            {'AAPL': [100.0] * len(idx), 'USDCAD=X': [1.0] * len(idx)},
            index=idx)

        from unittest.mock import patch
        with patch('core.utils.portfolio.yf.download', return_value=prices_df):
            results = compute_series_by_account(date_to='2026-03-31')

        series = results.get('ACCOUNT_A')
        self.assertTrue(series)
        # Before the SELL: 10 shares; after: 10 − 4 = 6 shares.
        self.assertEqual(series[0]['market_value_cad'], Decimal('1000'))
        self.assertEqual(series[-1]['market_value_cad'], Decimal('600'))


class ComputePositionsTests(SignedQuantityBase):
    def test_sell_negative_reduces_position(self):
        """BUY +10, SELL -4 → total_shares 6 (not 14)."""
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('200.00'), Decimal('-2000.00'), '2026-01-05', 'q1')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-4'),
                 Decimal('250.00'), Decimal('1000.00'), '2026-02-10', 'q2')

        positions = compute_positions()

        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['total_shares'], Decimal('6'))


class AccountDetailHoldingsTests(SignedQuantityBase):
    def test_sell_negative_reduces_shares(self):
        """The account detail page shows 6 shares after BUY +10, SELL -4."""
        from django.contrib.auth.models import User
        user = User.objects.create_user('signqty', password='x')
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('200.00'), Decimal('-2000.00'), '2026-01-05', 'd1')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-4'),
                 Decimal('250.00'), Decimal('1000.00'), '2026-02-10', 'd2')

        client = Client()
        client.force_login(user)
        response = client.get(f'/accounts/{self.account.pk}/')

        self.assertEqual(response.status_code, 200)
        holdings = {h['symbol']: h for h in response.context['active_holdings']}
        self.assertEqual(holdings['AAPL']['shares'], Decimal('6'))


class WriteLedgerTests(SignedQuantityBase):
    def test_sell_negative_credits_position_by_magnitude(self):
        """write_ledger: BUY +10 then SELL -10 → position back to 0 and the
        SELL's CREDIT entry carries quantity_change −10."""
        _make_tx(self.account, self.statement, 'AAPL', 'BUY', Decimal('10'),
                 Decimal('20.00'), Decimal('-200.00'), '2026-01-05', 'w1')
        sell = _make_tx(self.account, self.statement, 'AAPL', 'SELL',
                        Decimal('-10'), Decimal('25.00'), Decimal('250.00'),
                        '2026-01-15', 'w2')

        call_command('write_ledger', account='ACCOUNT_A', month='2026-01')

        position = LedgerAccount.objects.get(
            account=self.account, account_type='POSITION', symbol='AAPL')
        self.assertEqual(position.quantity, Decimal('0'))
        sell_entries = LedgerEntry.objects.filter(source_transaction=sell)
        self.assertEqual(sell_entries.count(), 2)   # cash + position
        sell_pos_entry = sell_entries.get(ledger_account=position)
        self.assertEqual(sell_pos_entry.entry_type, 'CREDIT')
        self.assertEqual(sell_pos_entry.quantity_change, Decimal('-10'))


class RebuildPositionsTests(SignedQuantityBase):
    def test_sell_negative_reduces_running_qty(self):
        """rebuild_positions: anchor 10 shares, SELL -3 → next record 7."""
        PositionHistory.objects.create(
            owner='', account=self.account, symbol='AAPL',
            quantity=Decimal('10'), cost_basis_per_unit=Decimal('20.0000'),
            total_cost_basis=Decimal('200.00'), currency='CAD',
            market_price=Decimal('0.0000'), market_value=Decimal('0.00'),
            date_from='2026-01-01')
        _make_tx(self.account, self.statement, 'AAPL', 'SELL', Decimal('-3'),
                 Decimal('25.00'), Decimal('75.00'), '2026-01-15', 'r1')

        from core.management.commands.rebuild_positions_from_transactions import (
            Command)
        result = Command()._rebuild_symbol(self.account, 'AAPL')

        self.assertIsNotNone(result)
        records = result['new']
        self.assertEqual(records[0].quantity, Decimal('10.0000'))
        self.assertEqual(records[-1].quantity, Decimal('7.0000'))


class QuantityReconClassifyTests(TestCase):
    """scripts/quantity_reconciliation_all.classify keeps its deltas with
    signed quantities (parked script, same results for BUY and SELL)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(os.path.dirname(__file__), '..', '..',
                            'scripts', 'quantity_reconciliation_all.py')
        spec = importlib.util.spec_from_file_location(
            'quantity_reconciliation_all', os.path.abspath(path))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _tx(self, **kwargs):
        base = dict(type='', quantity=Decimal('0'), notes='', symbol='')
        base.update(kwargs)
        return SimpleNamespace(**base)

    def test_buy_and_sell_deltas(self):
        delta, cat = self.mod.classify(
            self._tx(type='BUY', quantity=Decimal('10')))
        self.assertEqual((delta, cat), (Decimal('10'), 'BUY'))
        delta, cat = self.mod.classify(
            self._tx(type='SELL', quantity=Decimal('-10')))
        self.assertEqual((delta, cat), (Decimal('-10'), 'SELL'))

    def test_option_expiry_deltas(self):
        # Short call ('9…'): printed +qty. Long option: printed −qty.
        delta, cat = self.mod.classify(
            self._tx(type='REORGANIZATION', notes='EXP - OPTION EXPIRED',
                     symbol='9ABCDEF1', quantity=Decimal('5')))
        self.assertEqual((delta, cat), (Decimal('5'), 'OPT-EXP'))
        delta, cat = self.mod.classify(
            self._tx(type='REORGANIZATION', notes='EXP - OPTION EXPIRED',
                     symbol='8XYZPUT1', quantity=Decimal('-5')))
        self.assertEqual((delta, cat), (Decimal('-5'), 'LONG-OPT-EXP'))
