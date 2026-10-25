from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.core.management import call_command
from core.models import Transaction, Account, BrokerStatement
from decimal import Decimal
import datetime

class TransactionExportTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        # Create test data
        self.account = Account.objects.create(
            account_id='EXP001',
            name='Test Account',
            broker='IB'
        )
        self.broker = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='test_statement.csv'
        )
        
        # Create transactions
        Transaction.objects.create(
            statement=self.broker,
            account=self.account,
            date=datetime.date(2024, 1, 1),
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('100'),
            price=Decimal('150.00'),
            amount=Decimal('-15000.00'),
            currency='USD',
            amount_cad=Decimal('-20000.00'),
            exchange_rate_cad=Decimal('1.33'),
            commission=Decimal('0'),
            notes='Test transaction 1',
            tx_hash='exp_tx1'
        )
        
        Transaction.objects.create(
            statement=self.broker,
            account=self.account,
            date=datetime.date(2024, 1, 2),
            symbol='AAPL',
            type='SELL',
            quantity=Decimal('50'),
            price=Decimal('160.00'),
            amount=Decimal('8000.00'),
            currency='USD',
            amount_cad=Decimal('10640.00'),
            exchange_rate_cad=Decimal('1.33'),
            commission=Decimal('0'),
            notes='Test transaction 2',
            tx_hash='exp_tx2'
        )
        
        Transaction.objects.create(
            statement=self.broker,
            account=self.account,
            date=datetime.date(2024, 1, 3),
            symbol='MSFT',
            type='BUY',
            quantity=Decimal('200'),
            price=Decimal('300.00'),
            amount=Decimal('-60000.00'),
            currency='USD',
            amount_cad=Decimal('-80000.00'),
            exchange_rate_cad=Decimal('1.33'),
            commission=Decimal('0'),
            notes='Test transaction 3',
            tx_hash='exp_tx3'
        )

    def test_csv_export_status(self):
        response = self.client.get(reverse('transaction_export_csv'))
        self.assertEqual(response.status_code, 200)

    def test_csv_content_type(self):
        response = self.client.get(reverse('transaction_export_csv'))
        self.assertTrue(response.get('Content-Type').startswith('text/csv'))

    def test_csv_contains_header(self):
        response = self.client.get(reverse('transaction_export_csv'))
        self.assertIn(b'Date', response.content)
        self.assertIn(b'Symbol', response.content)

    def test_csv_contains_data(self):
        response = self.client.get(reverse('transaction_export_csv'))
        self.assertIn(b'AAPL', response.content)

    def test_csv_filter_by_symbol(self):
        response = self.client.get(reverse('transaction_export_csv'), {'symbol': 'MSFT'})
        self.assertIn(b'MSFT', response.content)
        self.assertNotIn(b'AAPL', response.content)

    def test_excel_export_status(self):
        response = self.client.get(reverse('transaction_export_excel'))
        self.assertEqual(response.status_code, 200)

    def test_excel_content_type(self):
        response = self.client.get(reverse('transaction_export_excel'))
        self.assertIn('spreadsheetml', response.get('Content-Type'))

    def test_csv_shows_account_name_not_number(self):
        response = self.client.get(reverse('transaction_export_csv'))
        self.assertIn(b'Test Account', response.content)
        self.assertNotIn(b'EXP001', response.content)

    def test_excel_shows_account_name_not_number(self):
        import io
        import zipfile
        response = self.client.get(reverse('transaction_export_excel'))
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        xml = b''.join(zf.read(n) for n in zf.namelist() if n.endswith('.xml'))
        self.assertIn(b'Test Account', xml)
        self.assertNotIn(b'EXP001', xml)

    def test_csv_filter_by_account_pk(self):
        # ?account= carries Account.pk
        other = Account.objects.create(
            account_id='EXPACC2', name='Other Account', broker='IB'
        )
        Transaction.objects.create(
            statement=self.broker,
            account=other,
            date=datetime.date(2024, 1, 4),
            symbol='GOOG',
            type='BUY',
            quantity=Decimal('1'),
            price=Decimal('10.00'),
            amount=Decimal('-10.00'),
            currency='USD',
            amount_cad=Decimal('-13.30'),
            exchange_rate_cad=Decimal('1.33'),
            commission=Decimal('0'),
            notes='other account tx',
            tx_hash='exp_tx_other',
        )
        response = self.client.get(
            reverse('transaction_export_csv'), {'account': str(self.account.pk)}
        )
        self.assertIn(b'AAPL', response.content)
        self.assertNotIn(b'GOOG', response.content)