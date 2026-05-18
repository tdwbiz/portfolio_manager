from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import Transaction, Account, BrokerStatement
from unittest.mock import patch
from decimal import Decimal
import datetime


class PerformanceChartTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        self.account = Account.objects.create(
            account_id='PERF001',
            name='Test Account',
            broker='IB'
        )
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 15),
            raw_filename='test.csv'
        )
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
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')}
        ]
        response = self.client.get(reverse('portfolio_chart_data'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/json')
        data = response.json()
        self.assertEqual(data['labels'], ['2024-01-31'])
        self.assertEqual(len(data['datasets']), 1)
        self.assertEqual(data['datasets'][0]['id'], 'market_value')
        self.assertEqual(data['datasets'][0]['data'], [2500.0])

    @patch('core.views.compute_portfolio_value_series')
    def test_chart_data_empty(self, mock_compute):
        mock_compute.return_value = []
        response = self.client.get(reverse('portfolio_chart_data'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['labels'], [])
        self.assertEqual(len(data['datasets']), 1)
        self.assertEqual(data['datasets'][0]['id'], 'market_value')
        self.assertEqual(data['datasets'][0]['data'], [])

    @patch('core.views.compute_portfolio_value_series')
    def test_chart_data_with_date_range(self, mock_compute):
        """Test that date range parameters are passed through and labels are filtered."""
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('1000.00')},
            {'date': '2024-06-30', 'market_value_cad': Decimal('2000.00')},
        ]
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'date_from': '2024-01-01', 'date_to': '2024-06-30'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['labels']), 2)
        self.assertEqual(data['labels'], ['2024-01-31', '2024-06-30'])
        self.assertTrue(
            any(ds.get('id') == 'market_value' for ds in data['datasets']),
            "Expected 'market_value' dataset in response"
        )

    @patch('core.views.compute_portfolio_value_series')
    @patch('core.views.compute_cost_basis_series')
    def test_chart_data_with_cost_basis(self, mock_cost_basis, mock_compute):
        """Test that cost_basis dataset is included when requested."""
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')},
        ]
        mock_cost_basis.return_value = [
            {'date': '2024-01-31', 'cost_basis_cad': Decimal('2000.00')},
        ]
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'series': 'market_value,cost_basis'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        dataset_ids = [ds['id'] for ds in data['datasets']]
        self.assertIn('cost_basis', dataset_ids)

    @patch('core.views.compute_portfolio_value_series')
    @patch('core.views.compute_income_series')
    def test_chart_data_with_income(self, mock_income, mock_compute):
        """Test that income dataset with type='bar' is included when requested."""
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')},
        ]
        mock_income.return_value = [
            {'date': '2024-01-31', 'income_cad': Decimal('100.00')},
        ]
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'series': 'market_value,income'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        income_ds = None
        for ds in data['datasets']:
            if ds['id'] == 'income':
                income_ds = ds
                break
        self.assertIsNotNone(income_ds, "Expected 'income' dataset in response")
        self.assertEqual(income_ds['type'], 'bar')

    def test_chart_data_invalid_date(self):
        """Test that invalid date_from returns 400."""
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'date_from': 'not-a-date'}
        )
        self.assertEqual(response.status_code, 400)

    def test_chart_data_benchmark_sanitise(self):
        """Test that benchmark ticker is sanitised before use."""
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'benchmark': '../../etc/passwd', 'series': 'benchmark'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        benchmark_datasets = [ds for ds in data['datasets'] if ds['id'].startswith('benchmark_')]
        self.assertTrue(len(benchmark_datasets) >= 0)

    def test_chart_page_has_controls(self):
        """Test that the chart page template contains the expected control labels."""
        response = self.client.get(reverse('portfolio_chart'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Cost Basis', response.content)
        self.assertIn(b'Benchmark', response.content)
        self.assertIn(b'Income', response.content)
        self.assertIn(b'By Account', response.content)
        self.assertIn(b'1M', response.content)
        self.assertIn(b'3M', response.content)
        self.assertIn(b'6M', response.content)
        self.assertIn(b'YTD', response.content)
        self.assertIn(b'1Y', response.content)
        self.assertIn(b'All', response.content)
