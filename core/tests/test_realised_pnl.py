from django.test import TestCase
from decimal import Decimal
from core.models import Account, BrokerStatement, Transaction
from core.utils import compute_realised_pnl


class RealisedPnlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Create test account
        cls.account = Account.objects.create(
            account_id='RPNL001',
            name='Test Account',
            broker='IB',
        )

        # Create broker statement
        cls.broker_statement = BrokerStatement.objects.create(
            source='IB',
            file_date='2026-04-01',
            raw_filename='test_file.csv',
        )

        # Create transactions
        cls.buy1 = Transaction.objects.create(
            account=cls.account,
            statement=cls.broker_statement,
            symbol='AAPL',
            type='BUY',
            quantity=10,
            price=Decimal('200.00'),
            amount=Decimal('-2000.00'),
            amount_cad=Decimal('-2000.00'),
            date='2026-04-01',
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'),
            tx_hash='rpnl_buy1',
        )

        cls.buy2 = Transaction.objects.create(
            account=cls.account,
            statement=cls.broker_statement,
            symbol='AAPL',
            type='BUY',
            quantity=10,
            price=Decimal('220.00'),
            amount=Decimal('-2200.00'),
            amount_cad=Decimal('-2200.00'),
            date='2026-04-02',
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'),
            tx_hash='rpnl_buy2',
        )

        cls.sell = Transaction.objects.create(
            account=cls.account,
            statement=cls.broker_statement,
            symbol='AAPL',
            type='SELL',
            quantity=15,
            price=Decimal('220.00'),
            amount=Decimal('3300.00'),
            amount_cad=Decimal('3300.00'),
            date='2026-04-03',
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'),
            tx_hash='rpnl_sell1',
        )

        # Create second account for filter test
        cls.account2 = Account.objects.create(
            account_id='RPNL002',
            name='Test Account 2',
            broker='IB',
        )

    def test_fifo_cost_basis(self):
        trades = compute_realised_pnl(account_id='RPNL001')
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['cost_basis_fifo'], Decimal('3100.00'))

    def test_avg_cost_basis(self):
        trades = compute_realised_pnl(account_id='RPNL001')
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['cost_basis_avg'], Decimal('3150.00'))

    def test_realised_pnl_page_loads(self):
        response = self.client.get('/realised-pnl/')
        self.assertEqual(response.status_code, 200)

    def test_realised_pnl_shows_symbol(self):
        response = self.client.get('/realised-pnl/')
        self.assertIn(b'AAPL', response.content)

    def test_realised_pnl_account_filter(self):
        # Create a transaction for account2 without SELL
        Transaction.objects.create(
            account=self.account2,
            statement=self.broker_statement,
            symbol='AAPL',
            type='BUY',
            quantity=10,
            price=Decimal('200.00'),
            amount=Decimal('-2000.00'),
            amount_cad=Decimal('-2000.00'),
            date='2026-04-01',
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'),
            tx_hash='rpnl_buy3',
        )

        # Test with account filter - AAPL trade for RPNL001 is visible
        response = self.client.get('/realised-pnl/?account=RPNL001')
        self.assertIn(b'AAPL', response.content)
        # RPNL002 has no sells so no trade rows for it should appear in table data
        self.assertNotIn(b'<td>RPNL002</td>', response.content)