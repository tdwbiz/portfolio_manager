from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import Transaction, Account, BrokerStatement
from decimal import Decimal
import datetime


class TaxSummaryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

        self.account = Account.objects.create(
            name='Tax Account',
            broker='IB',
            category='TAXABLE'
        )

        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='tax_test.csv'
        )

        # 1 BUY and 1 SELL of AAPL in 2024 (produces realised gain)
        Transaction.objects.create(
            account=self.account,
            statement=self.statement,
            date=datetime.date(2024, 1, 10),
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('10'),
            price=Decimal('150.00'),
            amount=Decimal('-1500.00'),
            amount_cad=Decimal('-1500.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='tax_buy1'
        )

        Transaction.objects.create(
            account=self.account,
            statement=self.statement,
            date=datetime.date(2024, 6, 10),
            symbol='AAPL',
            type='SELL',
            quantity=Decimal('10'),
            price=Decimal('200.00'),
            amount=Decimal('2000.00'),
            amount_cad=Decimal('2000.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='tax_sell1'
        )

        # 1 DIV transaction in 2024
        Transaction.objects.create(
            account=self.account,
            statement=self.statement,
            date=datetime.date(2024, 3, 15),
            symbol='AAPL',
            type='DIV',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('50.00'),
            amount_cad=Decimal('50.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='tax_div1'
        )

        # 1 WITHTAX transaction in 2024
        Transaction.objects.create(
            account=self.account,
            statement=self.statement,
            date=datetime.date(2024, 3, 15),
            symbol='AAPL',
            type='WITHTAX',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('-15.00'),
            amount_cad=Decimal('-15.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='tax_withtax1'
        )

    def test_tax_summary_page_loads(self):
        response = self.client.get(reverse('tax_summary'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)

    def test_tax_summary_default_year(self):
        response = self.client.get(reverse('tax_summary'))
        self.assertEqual(response.status_code, 200)

    def test_tax_summary_shows_capital_gains(self):
        response = self.client.get(reverse('tax_summary'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Capital Gains', response.content)

    def test_tax_summary_shows_income_section(self):
        response = self.client.get(reverse('tax_summary'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Income', response.content)

    def test_tax_summary_shows_tax_section(self):
        response = self.client.get(reverse('tax_summary'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Withholding Tax', response.content)

    def test_total_return_on_portfolio_summary(self):
        response = self.client.get(reverse('portfolio_summary'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Total Return', response.content)
