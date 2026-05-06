from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import Transaction, Account, BrokerStatement
from unittest.mock import patch
from decimal import Decimal
import datetime


class PerformanceChartTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        # Create test account
        self.account = Account.objects.create(
            account_id='PERF001',
            name='Test Account',
            broker='IB'
        )
        
        # Create broker statement
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 15),
            raw_filename='test.csv'
        )
        
        # Create test transaction
        self.transaction = Transaction.objects.create(
            account=self.account,
            statement=self.statement,
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('100'),
            price=Decimal('150.00'),
            amount=Decimal('-15000.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.35'),
            amount_cad=Decimal('-20250.00'),
            date=datetime.date(2024, 1, 15),
            tx_hash='perf_buy1',
            commission=Decimal('0')
        )

    def test_chart_page_loads(self):
        response = self.client.get(reverse('portfolio_chart'))
        self.assertEqual(response.status_code, 200)

    @patch('core.views.compute_portfolio_value_series')
    def test_chart_data_endpoint(self, mock_compute):
        # Mock return value
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')}
        ]
        
        response = self.client.get(reverse('portfolio_chart_data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        
        data = response.json()
        self.assertEqual(data['labels'], ['2024-01-31'])
        self.assertEqual(data['values'], [2500.0])

    @patch('core.views.compute_portfolio_value_series')
    def test_chart_data_empty(self, mock_compute):
        mock_compute.return_value = []
        
        response = self.client.get(reverse('portfolio_chart_data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'labels': [], 'values': []})