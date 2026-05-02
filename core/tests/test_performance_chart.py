import pytest
from django.test import TestCase, override_settings
from django.urls import reverse
from core.utils import compute_portfolio_value_series
from core.models import Transaction, Account
from unittest.mock import patch

@override_settings(USE_SQLITE_IN_TESTS=True)
class PerformanceChartTests(TestCase):
    @classmethod
def setUpTestData(cls):
        # Create test account
        cls.account = Account.objects.create(name='Test Account', account_id='test123')
        
        # Create test transaction
        cls.transaction = Transaction.objects.create(
            account=cls.account,
            symbol='AAPL',
            type='BUY',
            quantity=100,
            price=Decimal('150.00'),
            amount=Decimal('15000.00'),
            currency='USD',
            exchange_rate=Decimal('1.35'),
            amount_cad=Decimal('20250.00'),
            date='2024-01-15',
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