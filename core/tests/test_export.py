from django.test import TestCase, Client
from django.urls import reverse
from django.core.management import call_command
from core.models import Transaction, Account, BrokerStatement
from decimal import Decimal
import datetime

class TransactionExportTests(TestCase):
    def setUp(self):
        # Create test data
        self.account = Account.objects.create(id='12345', name='Test Account')
        self.broker = BrokerStatement.objects.create(
            broker__name='Interactive Brokers',
            account=self.account
        )
        
        # Create transactions
        Transaction.objects.create(
            date=datetime.date(2024, 1, 1),
            symbol='AAPL',
            type='BUY',
            quantity=100,
            price=Decimal('150.00'),
            amount=Decimal('15000.00'),
            currency='USD',
            amount_cad=Decimal('20000.00'),
            exchange_rate=Decimal('1.33'),
            account=self.account,
            broker_statement=self.broker,
            notes='Test transaction 1'
        )
        
        Transaction.objects.create(
            date=datetime.date(2024, 1, 2),
            symbol='AAPL',
            type='SELL',
            quantity=50,
            price=Decimal('160.00'),
            amount=Decimal('8000.00'),
            currency='USD',
            amount_cad=Decimal('10640.00'),
            exchange_rate=Decimal('1.33'),
            account=self.account,
            broker_statement=self.broker,
            notes='Test transaction 2'
        )
        
        Transaction.objects.create(
            date=datetime.date(2024, 1, 3),
            symbol='MSFT',
            type='BUY',
            quantity=200,
            price=Decimal('300.00'),
            amount=Decimal('60000.00'),
            currency='USD',
            amount_cad=Decimal('80000.00'),
            exchange_rate=Decimal('1.33'),
            account=self.account,
            broker_statement=self.broker,
            notes='Test transaction 3'
        )

    def test_csv_export_status(self):
        client = Client()
        response = client.get(reverse('transaction_export_csv'))
        self.assertEqual(response.status_code, 200)

    def test_csv_content_type(self):
        client = Client()
        response = client.get(reverse('transaction_export_csv'))
        self.assertTrue(response.get('Content-Type').startswith('text/csv'))

    def test_csv_contains_header(self):
        client = Client()
        response = client.get(reverse('transaction_export_csv'))
        self.assertIn(b'Date', response.content)
        self.assertIn(b'Symbol', response.content)

    def test_csv_contains_data(self):
        client = Client()
        response = client.get(reverse('transaction_export_csv'))
        self.assertIn(b'AAPL', response.content)

    def test_csv_filter_by_symbol(self):
        client = Client()
        response = client.get(reverse('transaction_export_csv'), {'symbol': 'MSFT'})
        self.assertIn(b'MSFT', response.content)
        self.assertNotIn(b'AAPL', response.content)

    def test_excel_export_status(self):
        client = Client()
        response = client.get(reverse('transaction_export_excel'))
        self.assertEqual(response.status_code, 200)

    def test_excel_content_type(self):
        client = Client()
        response = client.get(reverse('transaction_export_excel'))
        self.assertIn('spreadsheetml', response.get('Content-Type'))