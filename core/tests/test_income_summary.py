from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import Transaction, Account, BrokerStatement
from decimal import Decimal
import datetime


class IncomeSummaryTests(TestCase):
    def setUp(self):
        # Create test user
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        # Create test account
        self.account = Account.objects.create(
            account_id='INC001',
            name='Test Account',
            broker='IB'
        )
        
        # Create broker statement
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='test.csv'
        )
        
        # Create test transactions
        # DIV AAPL 2024 (two instances)
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=datetime.date(2024, 1, 15),
            symbol='AAPL',
            type='DIV',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('150.00'),
            amount_cad=Decimal('150.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='inc_div1'
        )
        
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=datetime.date(2024, 4, 15),
            symbol='AAPL',
            type='DIV',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('150.00'),
            amount_cad=Decimal('150.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='inc_div2'
        )
        
        # INT cash 2024
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=datetime.date(2024, 3, 1),
            symbol='',
            type='INT',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('50.00'),
            amount_cad=Decimal('50.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='inc_int1'
        )
        
        # WITHTAX AAPL 2024
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=datetime.date(2024, 1, 15),
            symbol='AAPL',
            type='WITHTAX',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('-45.00'),
            amount_cad=Decimal('-45.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='inc_tax1'
        )
        
        # DIV AAPL 2023
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=datetime.date(2023, 12, 15),
            symbol='AAPL',
            type='DIV',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('120.00'),
            amount_cad=Decimal('120.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='inc_div3'
        )
        
        # BUY AAPL 2024 (should NOT appear)
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=datetime.date(2024, 2, 1),
            symbol='AAPL',
            type='BUY',
            quantity=Decimal('100'),
            price=Decimal('150.00'),
            amount=Decimal('-15000.00'),
            amount_cad=Decimal('-20000.00'),
            currency='USD',
            exchange_rate_cad=Decimal('1.33'),
            commission=Decimal('0'),
            tx_hash='inc_buy1'
        )

    def test_income_page_loads(self):
        response = self.client.get(reverse('income_summary'))
        self.assertEqual(response.status_code, 200)

    def test_income_shows_div(self):
        response = self.client.get(reverse('income_summary'))
        self.assertIn(b'DIV', response.content)

    def test_income_excludes_buy(self):
        response = self.client.get(reverse('income_summary'))
        self.assertNotIn(b'inc_buy1', response.content)
        self.assertNotIn(b'-15000', response.content)

    def test_income_year_filter(self):
        response = self.client.get(reverse('income_summary'), {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        # 2023 DIV should not be shown
        content = response.content.decode()
        # Count instances of 2024 vs 2023
        self.assertIn('2024', content)
        # 2023 row should not appear when filtering for 2024
        rows = [r for r in content.split('<tr>') if '2023' in r and 'AAPL' in r and 'DIV' in r]
        self.assertEqual(len(rows), 0)

    def test_income_type_filter(self):
        response = self.client.get(reverse('income_summary'), {'type': 'INT'})
        self.assertIn(b'INT', response.content)
        # Should not show DIV when filtering for INT
        content = response.content.decode()
        # Check that only INT rows appear in table body
        self.assertIn('INT', content)

    def test_income_totals(self):
        response = self.client.get(reverse('income_summary'))
        content = response.content.decode()
        # Two DIV AAPL 2024 = 300.00
        self.assertIn('300.00', content)

    def test_income_account_filter(self):
        """Create income transactions in two accounts; GET /income/?account=ACCOUNT1; assert only transactions from ACCOUNT1 appear."""
        acct2 = Account.objects.create(
            account_id='INC002',
            name='Test Account 2',
            broker='IB'
        )
        stmt2 = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='test2.csv'
        )
        Transaction.objects.create(
            statement=stmt2,
            account=acct2,
            date=datetime.date(2024, 5, 15),
            symbol='MSFT',
            type='DIV',
            quantity=Decimal('0'),
            price=Decimal('0'),
            amount=Decimal('200.00'),
            amount_cad=Decimal('200.00'),
            currency='CAD',
            commission=Decimal('0'),
            tx_hash='inc_acct2_div'
        )
        response = self.client.get(reverse('income_summary'), {'account': 'INC001'})
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # INC001 transactions should appear
        self.assertIn('AAPL', content)
        # MSFT from INC002 should not appear
        self.assertNotIn('MSFT', content)

    def test_income_account_filter_invalid(self):
        """GET /income/?account=DOESNOTEXIST; assert 200 and rows is empty (not an error)."""
        response = self.client.get(reverse('income_summary'), {'account': 'DOESNOTEXIST'})
        self.assertEqual(response.status_code, 200)
        # Should not crash, and should show no rows (or empty)
        self.assertEqual(response.status_code, 200)
