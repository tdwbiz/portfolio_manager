from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from core.models import Account, BrokerStatement, Transaction

class PortfolioSummaryTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            account_id='PORT001', name='Portfolio Test', broker='IB', currency='USD',
            starting_balance=Decimal('0'),
        )
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date='2024-01-01',
            raw_filename='test.csv',
        )
        self.tx1 = Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-01-15', type='BUY', symbol='AAPL',
            quantity=Decimal('10'), price=Decimal('150.00'),
            commission=Decimal('0'), amount=Decimal('-1500.00'), currency='USD',
            amount_cad=Decimal('-2000.00'), exchange_rate_cad=Decimal('1.333'),
            tx_hash='port_test_aapl_buy',
        )
        self.tx2 = Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-02-01', type='BUY', symbol='VFV.TO',
            quantity=Decimal('5'), price=Decimal('100.00'),
            commission=Decimal('0'), amount=Decimal('-500.00'), currency='CAD',
            amount_cad=Decimal('-500.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='port_test_vfv_buy',
        )

    def test_portfolio_page_loads(self):
        with patch('core.views.fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertEqual(response.status_code, 200)

    def test_portfolio_shows_positions(self):
        with patch('core.views.fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertIn(b'AAPL', response.content)
            self.assertIn(b'VFV.TO', response.content)

    def test_portfolio_unrealised_pnl_calculation(self):
        with patch('core.views.fetch_last_prices', return_value={'AAPL': Decimal('180.00'), 'VFV.TO': Decimal('110.00'), 'USDCAD=X': Decimal('1.35')}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertIn(b'2430.00', response.content)

    def test_portfolio_missing_price_shows_dash(self):
        with patch('core.views.fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertIn(b'&mdash;', response.content)

    def test_portfolio_sold_out_position_excluded(self):
        self.tx_sell = Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-03-01', type='SELL', symbol='AAPL',
            quantity=Decimal('10'), price=Decimal('160.00'),
            commission=Decimal('0'), amount=Decimal('1600.00'), currency='USD',
            amount_cad=Decimal('2133.20'), exchange_rate_cad=Decimal('1.333'),
            tx_hash='port_test_aapl_sell',
        )
        with patch('core.views.fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertNotIn(b'AAPL', response.content)
