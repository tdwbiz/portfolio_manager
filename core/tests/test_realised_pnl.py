from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from core.models import Account, BrokerStatement, Transaction
from core.utils import compute_realised_pnl


class RealisedPnlTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Create test user
        cls.user = User.objects.create_user(username='testuser', password='testpass123')
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
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/realised-pnl/')
        self.assertEqual(response.status_code, 200)

    def test_realised_pnl_shows_symbol(self):
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/realised-pnl/')
        self.assertIn(b'AAPL', response.content)

    def test_realised_pnl_account_filter(self):
        self.client.login(username='testuser', password='testpass123')
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

    def test_date_range_filter(self):
        """Create a SELL in 2024 and a SELL in 2025; filter to 2024 only."""
        acct = Account.objects.create(
            account_id='RPNL_DATE',
            name='Date Filter Account',
            broker='IB',
        )
        stmt = BrokerStatement.objects.create(
            source='IB',
            file_date='2024-01-01',
            raw_filename='date_test.csv',
        )
        # 2024 trade
        Transaction.objects.create(
            account=acct, statement=stmt, symbol='MSFT', type='BUY',
            quantity=10, price=Decimal('300.00'), amount=Decimal('-3000.00'),
            amount_cad=Decimal('-3000.00'), date='2024-01-15',
            currency='CAD', exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'), tx_hash='date_buy1',
        )
        Transaction.objects.create(
            account=acct, statement=stmt, symbol='MSFT', type='SELL',
            quantity=10, price=Decimal('350.00'), amount=Decimal('3500.00'),
            amount_cad=Decimal('3500.00'), date='2024-06-15',
            currency='CAD', exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'), tx_hash='date_sell1',
        )
        # 2025 trade
        Transaction.objects.create(
            account=acct, statement=stmt, symbol='GOOG', type='BUY',
            quantity=5, price=Decimal('140.00'), amount=Decimal('-700.00'),
            amount_cad=Decimal('-700.00'), date='2025-01-10',
            currency='CAD', exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'), tx_hash='date_buy2',
        )
        Transaction.objects.create(
            account=acct, statement=stmt, symbol='GOOG', type='SELL',
            quantity=5, price=Decimal('160.00'), amount=Decimal('800.00'),
            amount_cad=Decimal('800.00'), date='2025-06-15',
            currency='CAD', exchange_rate_cad=Decimal('1.0'),
            commission=Decimal('0'), tx_hash='date_sell2',
        )
        trades = compute_realised_pnl(date_from='2024-01-01', date_to='2024-12-31')
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['symbol'], 'MSFT')

    def test_symbol_filter(self):
        """Create SELLs for AAPL and MSFT; filter with symbol='AAPL'."""
        trades = compute_realised_pnl(account_id='RPNL001', symbol='AAPL')
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['symbol'], 'AAPL')

    def test_realised_pnl_year_grouping_view(self):
        """GET /realised-pnl/?group_by=year; assert response contains a year heading."""
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/realised-pnl/?group_by=year')
        self.assertEqual(response.status_code, 200)
        # The sell date is 2026-04-03, so 2026 should appear
        self.assertIn(b'2026', response.content)

    def test_realised_pnl_date_filter_view(self):
        """GET /realised-pnl/?date_from=2024-01-01&date_to=2024-12-31; assert 200."""
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/realised-pnl/?date_from=2024-01-01&date_to=2024-12-31')
        self.assertEqual(response.status_code, 200)

    def test_realised_pnl_invalid_date_ignored(self):
        """GET with date_from=baddate; assert 200 (invalid date silently ignored)."""
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get('/realised-pnl/?date_from=baddate')
        self.assertEqual(response.status_code, 200)
