"""Tests for reverse statement generation."""

import datetime
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Account, AccountCurrency, BrokerStatement, Transaction
from core.utils.statements import (
    _month_start,
    _prev_month,
    reverse_generate_monthly_statements,
)


class UtilityFunctionTests(TestCase):
    """Tests for helper functions."""

    def test_month_start(self):
        """_month_start returns the first day of the month."""
        self.assertEqual(_month_start(date(2024, 6, 15)), date(2024, 6, 1))
        self.assertEqual(_month_start(date(2024, 1, 1)), date(2024, 1, 1))
        self.assertEqual(_month_start(date(2024, 12, 31)), date(2024, 12, 1))

    def test_prev_month(self):
        """_prev_month returns the first day of the previous month."""
        self.assertEqual(_prev_month(date(2024, 6, 15)), date(2024, 5, 1))
        self.assertEqual(_prev_month(date(2024, 1, 15)), date(2023, 12, 1))
        self.assertEqual(_prev_month(date(2024, 3, 1)), date(2024, 2, 1))


class ReverseStatementGenerationTests(TestCase):
    """Tests for the reverse_generate_monthly_statements function.

    CSV sign convention:
    - BUY = negative amount_cad (money out)
    - SELL = positive amount_cad (money in)
    - DIV/INT = positive amount_cad (income)
    - TAX/WDRAW = negative amount_cad (money out)
    - Commission is already included in amount_cad
    """

    def setUp(self):
        self.account = Account.objects.create(
            account_id='REV001',
            name='Reverse Test Account',
            broker='IB',
            category='TAXABLE',
        )
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='test_statement.csv',
        )

    def test_reverse_single_month_buy(self):
        """Generate a single month statement with a BUY in reverse."""
        # BUY in June 2024: amount_cad = -1509.99 (negative = money out)
        # commission=9.99 is already included in amount_cad
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='AAPL', type='BUY', quantity=10, price=Decimal('150'),
            amount=Decimal('1500'), currency='CAD', commission=Decimal('9.99'),
            amount_cad=Decimal('-1509.99'),
        )
        # Anchor at June 2024, known ending = 10000
        # net_flow = -1509.99 (one BUY)
        # reverse: beginning = ending - net_flow = 10000 - (-1509.99) = 11509.99
        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'CAD': Decimal('10000')},
        )
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.statement_month, date(2024, 6, 1))
        self.assertEqual(stmt.currency, 'CAD')
        self.assertEqual(stmt.ending_market_value, Decimal('10000'))
        self.assertEqual(stmt.beginning_market_value, Decimal('11509.99'))
        # total_buys stored as absolute magnitude
        self.assertEqual(stmt.total_buys, Decimal('1509.99'))

    def test_reverse_multiple_months(self):
        """Generate statements for multiple months going backwards."""
        # May 2024: BUY 505 CAD (negative)
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 5, 10),
            symbol='GOOGL', type='BUY', quantity=5, price=Decimal('100'),
            amount=Decimal('500'), currency='CAD', commission=Decimal('5'),
            amount_cad=Decimal('-505'),
        )
        # June 2024: BUY 1005 CAD (negative)
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 20),
            symbol='AAPL', type='BUY', quantity=5, price=Decimal('200'),
            amount=Decimal('1000'), currency='CAD', commission=Decimal('5'),
            amount_cad=Decimal('-1005'),
        )

        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'CAD': Decimal('12000')},
        )
        # Should generate 2 months: June and May
        self.assertEqual(len(stmts), 2)

        june_stmt = next(s for s in stmts if s.statement_month == date(2024, 6, 1))
        may_stmt = next(s for s in stmts if s.statement_month == date(2024, 5, 1))

        # June: net_flow = -1005, ending = 12000
        # beginning = 12000 - (-1005) = 13005
        self.assertEqual(june_stmt.ending_market_value, Decimal('12000'))
        self.assertEqual(june_stmt.beginning_market_value, Decimal('13005'))
        # May: ending = beginning of June = 13005
        # net_flow = -505, beginning = 13005 - (-505) = 13510
        self.assertEqual(may_stmt.ending_market_value, june_stmt.beginning_market_value)

    def test_reverse_with_sell_transaction(self):
        """Reverse generation handles SELL transactions correctly."""
        # SELL: amount_cad = +2000 (positive = money in)
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='AAPL', type='SELL', quantity=10, price=Decimal('200'),
            amount=Decimal('2000'), currency='CAD', commission=Decimal('9.99'),
            amount_cad=Decimal('2000'),
        )

        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'CAD': Decimal('10000')},
        )
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        # net_flow = +2000
        # beginning = 10000 - 2000 = 8000
        self.assertEqual(stmt.beginning_market_value, Decimal('8000'))
        self.assertEqual(stmt.ending_market_value, Decimal('10000'))
        self.assertEqual(stmt.total_sells, Decimal('2000'))

    def test_reverse_with_dividends(self):
        """Reverse generation correctly handles dividend income."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='TD', type='DIV', quantity=100, price=Decimal('0.55'),
            amount=Decimal('55'), currency='CAD', commission=Decimal('0'),
            amount_cad=Decimal('55'),
        )

        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'CAD': Decimal('10000')},
        )
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.total_dividends, Decimal('55'))
        # net_flow = +55, beginning = 10000 - 55 = 9945
        self.assertEqual(stmt.beginning_market_value, Decimal('9945'))

    def test_reverse_no_transactions(self):
        """Returns empty list when account has no transactions."""
        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'CAD': Decimal('10000')},
        )
        self.assertEqual(stmts, [])

    def test_reverse_no_ending_balances(self):
        """Returns empty list when no ending balances can be determined."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='AAPL', type='BUY', quantity=10, price=Decimal('100'),
            amount=Decimal('1000'), currency='CAD', commission=Decimal('5'),
            amount_cad=Decimal('-1005'),
        )
        # No ending_balances provided, no RBC data, no AccountCurrency
        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            None,
        )
        self.assertEqual(stmts, [])

    def test_reverse_auto_detect_from_account_currency(self):
        """Auto-detects ending balance from AccountCurrency when no RBC data."""
        AccountCurrency.objects.create(
            account=self.account, currency='CAD',
            starting_balance=Decimal('5000'),
            starting_balance_date=date(2020, 1, 1),
        )
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='AAPL', type='BUY', quantity=10, price=Decimal('100'),
            amount=Decimal('1000'), currency='CAD', commission=Decimal('5'),
            amount_cad=Decimal('-1005'),
        )

        # Auto-detection uses AccountCurrency.starting_balance as fallback
        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            None,
        )
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.currency, 'CAD')
        # ending = 5000 (from AccountCurrency.starting_balance)
        # net_flow = -1005, beginning = 5000 - (-1005) = 6005
        self.assertEqual(stmt.ending_market_value, Decimal('5000'))
        self.assertEqual(stmt.beginning_market_value, Decimal('6005'))

    def test_reverse_with_interest(self):
        """Reverse generation correctly handles interest income."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='CASH', type='INT', quantity=1, price=Decimal('25'),
            amount=Decimal('25'), currency='CAD', commission=Decimal('0'),
            amount_cad=Decimal('25'),
        )

        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'CAD': Decimal('10000')},
        )
        self.assertEqual(len(stmts), 1)
        stmt = stmts[0]
        self.assertEqual(stmt.total_interest, Decimal('25'))
        # net_flow = +25, beginning = 10000 - 25 = 9975
        self.assertEqual(stmt.beginning_market_value, Decimal('9975'))

    def test_reverse_multi_currency(self):
        """Reverse generation works with multiple currencies."""
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 15),
            symbol='AAPL', type='BUY', quantity=5, price=Decimal('180'),
            amount=Decimal('900'), currency='USD', commission=Decimal('2'),
            amount_cad=Decimal('-1200'),
        )
        Transaction.objects.create(
            statement=self.statement,
            account=self.account, date=date(2024, 6, 20),
            symbol='TD', type='BUY', quantity=10, price=Decimal('80'),
            amount=Decimal('800'), currency='CAD', commission=Decimal('5'),
            amount_cad=Decimal('-805'),
        )

        stmts = reverse_generate_monthly_statements(
            self.account,
            date(2024, 6, 1),
            {'USD': Decimal('5000'), 'CAD': Decimal('10000')},
        )
        self.assertEqual(len(stmts), 2)
        currencies = {s.currency for s in stmts}
        self.assertEqual(currencies, {'USD', 'CAD'})


class ReverseStatementViewTests(TestCase):
    """Tests for the reverse_statements_view."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        self.account = Account.objects.create(
            account_id='REVVIEW',
            name='Reverse View Account',
            broker='IB',
            category='TAXABLE',
        )
        AccountCurrency.objects.create(
            account=self.account, currency='CAD',
            starting_balance=Decimal('10000'),
            starting_balance_date=date(2020, 1, 1),
        )

    def test_reverse_statements_page_loads(self):
        """The reverse statements page loads successfully."""
        response = self.client.get(reverse('reverse_statements'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reverse Statement Generation')
        self.assertContains(response, 'Anchor Month')

    def test_reverse_statements_requires_account(self):
        """Submitting without an account shows an error."""
        response = self.client.post(reverse('reverse_statements'), {
            'account': '',
            'anchor_month': '2024-06',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Select account')

    def test_reverse_statements_invalid_account(self):
        """Submitting a non-existent account shows an error."""
        response = self.client.post(reverse('reverse_statements'), {
            'account': 'NONEXISTENT',
            'anchor_month': '2024-06',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not found')

    def test_reverse_statements_post_with_data(self):
        """Submitting valid data generates reverse statements."""
        stmt = BrokerStatement.objects.create(
            source='IB',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='test.csv',
        )
        Transaction.objects.create(
            statement=stmt,
            account=self.account, date=date(2024, 6, 15),
            symbol='AAPL', type='BUY', quantity=5, price=Decimal('200'),
            amount=Decimal('1000'), currency='CAD', commission=Decimal('5'),
            amount_cad=Decimal('-1005'),
        )
        response = self.client.post(reverse('reverse_statements'), {
            'account': 'REVVIEW',
            'anchor_month': '2024-06',
            'ending_cad': '10000',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Generated')
        self.assertContains(response, '10000.00')

    def test_reverse_statements_link_from_accounts_list(self):
        """The accounts list page links to reverse statements."""
        response = self.client.get(reverse('accounts_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Reverse Statements')
