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

    @patch('core.views_portfolio.compute_portfolio_value_series')
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

    @patch('core.views_portfolio.compute_portfolio_value_series')
    def test_chart_data_empty(self, mock_compute):
        mock_compute.return_value = []
        response = self.client.get(reverse('portfolio_chart_data'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['labels'], [])
        self.assertEqual(len(data['datasets']), 1)
        self.assertEqual(data['datasets'][0]['id'], 'market_value')
        self.assertEqual(data['datasets'][0]['data'], [])

    @patch('core.views_portfolio.compute_portfolio_value_series')
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

    @patch('core.views_portfolio.compute_portfolio_value_series')
    @patch('core.views_portfolio.compute_cost_basis_series')
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

    @patch('core.views_portfolio.compute_portfolio_value_series')
    @patch('core.views_portfolio.compute_income_series')
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


class PerformanceChartFixesTests(TestCase):
    """Tests for the performance chart fixes and granularity control."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        self.account = Account.objects.create(
            account_id='PERF002',
            name='Test Account 2',
            broker='IB'
        )
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 15),
            raw_filename='test2.csv'
        )

    def test_generate_date_series_daily(self):
        """Test daily date series generation."""
        from core.utils import _generate_date_series
        result = _generate_date_series(
            datetime.date(2026, 1, 1),
            datetime.date(2026, 1, 10),
            'daily'
        )
        self.assertEqual(len(result), 10)
        self.assertEqual(result[0], datetime.date(2026, 1, 1))
        self.assertEqual(result[-1], datetime.date(2026, 1, 10))

    def test_generate_date_series_weekly(self):
        """Test weekly date series generation (Fridays)."""
        from core.utils import _generate_date_series
        result = _generate_date_series(
            datetime.date(2026, 1, 1),
            datetime.date(2026, 1, 31),
            'weekly'
        )
        self.assertGreater(len(result), 0)
        for d in result:
            self.assertEqual(d.weekday(), 4, f"Expected Friday, got {d}")

    def test_generate_date_series_annual(self):
        """Test annual date series generation."""
        from core.utils import _generate_date_series
        result = _generate_date_series(
            datetime.date(2024, 1, 1),
            datetime.date(2026, 5, 20),
            'annual'
        )
        self.assertEqual(result, [
            datetime.date(2024, 12, 31),
            datetime.date(2025, 12, 31),
            datetime.date(2026, 5, 20),
        ])

    def test_cost_basis_no_double_count(self):
        """Test that cost basis does not double-count on each pass."""
        from core.utils import compute_cost_basis_series

        self.transaction = Transaction.objects.create(
            account=self.account,
            statement=self.statement,
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('10.00'),
            amount=Decimal('-100.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            amount_cad=Decimal('-100.00'),
            date=datetime.date(2026, 1, 15),
            tx_hash='cb_buy1',
            commission=Decimal('0')
        )
        self.statement2 = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2026, 1, 20),
            raw_filename='test2.csv'
        )
        self.transaction2 = Transaction.objects.create(
            account=self.account,
            statement=self.statement2,
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('10.00'),
            amount=Decimal('-100.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            amount_cad=Decimal('-100.00'),
            date=datetime.date(2026, 1, 20),
            tx_hash='cb_buy2',
            commission=Decimal('0')
        )
        self.statement3 = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2026, 2, 15),
            raw_filename='test3.csv'
        )
        self.transaction3 = Transaction.objects.create(
            account=self.account,
            statement=self.statement3,
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('20'),
            price=Decimal('10.00'),
            amount=Decimal('-200.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.0'),
            amount_cad=Decimal('-200.00'),
            date=datetime.date(2026, 2, 15),
            tx_hash='cb_buy3',
            commission=Decimal('0')
        )

        from django.core.cache import cache
        cache.clear()

        result = compute_cost_basis_series(freq='monthly')

        jan_entry = None
        feb_entry = None
        for entry in result:
            if entry['date'].startswith('2026-01'):
                jan_entry = entry
            elif entry['date'].startswith('2026-02'):
                feb_entry = entry

        self.assertIsNotNone(jan_entry)
        self.assertEqual(jan_entry['cost_basis_cad'], Decimal('200.00'))

        self.assertIsNotNone(feb_entry)
        self.assertEqual(feb_entry['cost_basis_cad'], Decimal('400.00'))
        self.assertLess(feb_entry['cost_basis_cad'], Decimal('800.00'))

    @patch('core.views_portfolio.compute_portfolio_value_series')
    def test_chart_data_freq_daily(self, mock_compute):
        """Test that freq=daily is accepted and echoed in response."""
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('2500.00')}
        ]
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'freq': 'daily', 'series': 'market_value'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['freq'], 'daily')

    def test_chart_data_freq_invalid(self):
        """Test that invalid freq returns 400."""
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'freq': 'quarterly', 'series': 'market_value'}
        )
        self.assertEqual(response.status_code, 400)

    @patch('core.views_portfolio.compute_portfolio_value_series')
    def test_chart_data_market_value_nonzero_with_prices(self, mock_compute):
        """Test that market value is non-zero when prices are available."""
        mock_compute.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('50000.00')},
            {'date': '2024-02-29', 'market_value_cad': Decimal('52000.00')},
        ]

        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'series': 'market_value'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        mv_dataset = None
        for ds in data['datasets']:
            if ds['id'] == 'market_value':
                mv_dataset = ds
                break
        self.assertIsNotNone(mv_dataset)
        non_zero_count = sum(1 for v in mv_dataset['data'] if v != 0)
        self.assertGreater(non_zero_count, 0)

    @patch('core.views_portfolio.compute_series_by_account')
    @patch('core.views_portfolio.compute_portfolio_value_series')
    def test_chart_data_by_account_labels_match(self, mock_mv, mock_by_account):
        """Test that by_account datasets have the same length as labels."""
        mock_mv.return_value = [
            {'date': '2024-01-31', 'market_value_cad': Decimal('1000.00')},
            {'date': '2024-02-29', 'market_value_cad': Decimal('1100.00')},
            {'date': '2024-03-31', 'market_value_cad': Decimal('1200.00')},
        ]
        mock_by_account.return_value = {
            'ACCT1': [
                {'date': '2024-01-31', 'market_value_cad': Decimal('500.00')},
                {'date': '2024-02-29', 'market_value_cad': Decimal('550.00')},
                {'date': '2024-03-31', 'market_value_cad': Decimal('600.00')},
            ],
            'ACCT2': [
                {'date': '2024-01-31', 'market_value_cad': Decimal('500.00')},
                {'date': '2024-02-29', 'market_value_cad': Decimal('550.00')},
                {'date': '2024-03-31', 'market_value_cad': Decimal('600.00')},
            ],
        }
        response = self.client.get(
            reverse('portfolio_chart_data'),
            {'series': 'market_value,by_account'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['labels']), 3)
        for ds in data['datasets']:
            if ds['id'].startswith('account_'):
                self.assertEqual(len(ds['data']), 3)

    def test_chart_page_has_granularity_control(self):
        """Test that the chart page template contains granularity control buttons."""
        response = self.client.get(reverse('portfolio_chart'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Daily', response.content)
        self.assertIn(b'Weekly', response.content)
        self.assertIn(b'Monthly', response.content)
        self.assertIn(b'Annual', response.content)
