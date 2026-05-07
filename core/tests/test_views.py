from django.test import TestCase
from django.urls import reverse
from core.models import Transaction, FileImportLog, BrokerStatement, Account
from decimal import Decimal
from django.contrib.auth.models import User


class DashboardTests(TestCase):
    def setUp(self):
        self.statement = BrokerStatement.objects.create(source='IB', file_date='2023-01-01', raw_filename='test.csv')
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

    def test_dashboard_loads(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_shows_positions(self):
        Transaction.objects.create(
            statement=self.statement,
            date='2023-01-01',
            symbol='AAPL',
            type='BUY',
            quantity=10,
            price=Decimal('100.00'),
            commission=Decimal('0'),
            amount=Decimal('1000.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.3'),
            amount_cad=Decimal('1300.00'),
            tx_hash='dashboard_test_hash_001',
        )
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'AAPL')

    def test_dashboard_recent_imports(self):
        FileImportLog.objects.create(filename='test.csv', broker='IB', records_imported=10, status='success')
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'test.csv')


class TransactionListTests(TestCase):
    def setUp(self):
        self.statement_ib = BrokerStatement.objects.create(source='IB', file_date='2023-01-01', raw_filename='ib.csv')
        self.statement_rbc = BrokerStatement.objects.create(source='RBC', file_date='2023-01-01', raw_filename='rbc.csv')
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        for i in range(55):
            Transaction.objects.create(
                statement=self.statement_ib,
                date='2023-01-01',
                symbol='AAPL',
                type='BUY',
                quantity=1,
                price=Decimal('100.00'),
                commission=Decimal('0'),
                amount=Decimal('100.00'),
                currency='USD',
                exchange_rate_cad=Decimal('1.3'),
                amount_cad=Decimal('130.00'),
                tx_hash=f'txlist_loop_{i:04d}',
            )

    def test_transaction_list_loads(self):
        response = self.client.get(reverse('transaction_list'))
        self.assertEqual(response.status_code, 200)

    def test_transaction_list_filter_by_broker(self):
        Transaction.objects.create(
            statement=self.statement_rbc,
            date='2023-01-01',
            symbol='GOOG',
            type='BUY',
            quantity=1,
            price=Decimal('100.00'),
            commission=Decimal('0'),
            amount=Decimal('100.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.3'),
            amount_cad=Decimal('130.00'),
            tx_hash='broker_filter_rbc_goog',
        )
        response = self.client.get(reverse('transaction_list'), {'broker': 'IB'})
        self.assertContains(response, 'AAPL')
        self.assertNotContains(response, 'GOOG')

    def test_transaction_list_filter_by_symbol(self):
        response = self.client.get(reverse('transaction_list'), {'symbol': 'AAPL'})
        self.assertContains(response, 'AAPL')
        self.assertNotContains(response, 'GOOG')

    def test_transaction_list_pagination(self):
        response = self.client.get(reverse('transaction_list'))
        self.assertEqual(len(response.context['page_obj']), 50)

        response = self.client.get(reverse('transaction_list'), {'page': 2})
        self.assertEqual(len(response.context['page_obj']), 5)


class AccountsPageTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            account_id='TOM IB',
            name='Tom IB',
            broker='IB',
            currency='USD',
            starting_balance=Decimal('1000.00')
        )
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

    def test_accounts_page_loads(self):
        response = self.client.get(reverse('accounts_list'))
        self.assertEqual(response.status_code, 200)

    def test_accounts_page_shows_accounts(self):
        response = self.client.get(reverse('accounts_list'))
        self.assertContains(response, 'TOM IB')


class TransactionListFilterByAccountTests(TestCase):
    def setUp(self):
        self.statement = BrokerStatement.objects.create(source='IB', file_date='2023-01-01', raw_filename='test.csv')
        self.account = Account.objects.create(
            account_id='TOM IB',
            name='Tom IB',
            broker='IB'
        )
        self.transaction = Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date='2023-01-01',
            symbol='AAPL',
            type='BUY',
            quantity=10,
            price=Decimal('100.00'),
            commission=Decimal('0'),
            amount=Decimal('1000.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.3'),
            amount_cad=Decimal('1300.00'),
            tx_hash='acct_filter_test_hash_001',
        )
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

    def test_transaction_list_filter_by_account(self):
        response = self.client.get(reverse('transaction_list'), {'account': 'TOM IB'})
        self.assertContains(response, 'AAPL')

        response = self.client.get(reverse('transaction_list'), {'account': 'OTHER'})
