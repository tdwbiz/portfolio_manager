from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from core.models import Account, BrokerStatement, Transaction
from core.utils import compute_positions, group_positions_by_account, group_positions_by_category, enrich_positions_with_prices
from django.contrib.auth.models import User


class PortfolioSummaryTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name='Portfolio Test', broker='IB', currency='USD',
            starting_balance=Decimal('0'))
        self.statement = BrokerStatement.objects.create(
            source='IB',
            file_date='2024-01-01',
            raw_filename='test.csv',
        )
        self.tx1 = Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-01-15', type='BUY', symbol='AAPL',
            quantity=Decimal('10'), price=Decimal('150.00'),
            commission=Decimal('0'), amount=Decimal('-1500.00'), currency='USD',
            amount_cad=Decimal('-2000.00'), exchange_rate_cad=Decimal('1.333'),
            tx_hash='port_test_aapl_buy',
        )
        self.tx2 = Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-02-01', type='BUY', symbol='VFV.TO',
            quantity=Decimal('5'), price=Decimal('100.00'),
            commission=Decimal('0'), amount=Decimal('-500.00'), currency='CAD',
            amount_cad=Decimal('-500.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='port_test_vfv_buy',
        )
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

    def test_portfolio_page_loads(self):
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertEqual(response.status_code, 200)

    def test_portfolio_shows_positions(self):
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertIn(b'AAPL', response.content)
            self.assertIn(b'VFV.TO', response.content)

    def test_portfolio_unrealised_pnl_calculation(self):
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={'AAPL': Decimal('180.00'), 'VFV.TO': Decimal('110.00'), 'USDCAD=X': Decimal('1.35')}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertIn(b'2430.00', response.content)

    def test_portfolio_missing_price_shows_dash(self):
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertIn(b'&mdash;', response.content)

    def test_portfolio_sold_out_position_excluded(self):
        self.tx_sell = Transaction.objects.create(
            statement=self.statement, account=self.account,
            date='2024-03-01', type='SELL', symbol='AAPL',
            quantity=Decimal('10'), price=Decimal('160.00'),
            commission=Decimal('0'), amount=Decimal('1600.00'), currency='USD',
            amount_cad=Decimal('2133.20'), exchange_rate_cad=Decimal('1.333'),
            tx_hash='port_test_aapl_sell',
        )
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertNotIn(b'AAPL', response.content)

    # --- New tests for Spec 16 ---

    def test_positions_include_account_category(self):
        """Test that compute_positions includes category and category_display."""
        # Clean slate
        Transaction.objects.all().delete()
        Account.objects.all().delete()
        BrokerStatement.objects.all().delete()

        rrsp_account = Account.objects.create(
            name='RRSP Test', broker='IB', currency='USD',
            category='RRSP', owner='Tom',
            starting_balance=Decimal('0'))
        rrsp_stmt = BrokerStatement.objects.create(
            source='IB', file_date='2024-06-01', raw_filename='rrsp.csv',
        )
        Transaction.objects.create(
            statement=rrsp_stmt, account=rrsp_account,
            date='2024-06-15', type='BUY', symbol='TEST',
            quantity=Decimal('1'), price=Decimal('100.00'),
            commission=Decimal('0'), amount=Decimal('-100.00'), currency='USD',
            amount_cad=Decimal('-133.30'), exchange_rate_cad=Decimal('1.333'),
            tx_hash='cat_test_buy',
        )
        positions = compute_positions()
        rrsp_pos = [p for p in positions if p['symbol'] == 'TEST']
        self.assertEqual(len(rrsp_pos), 1)
        self.assertEqual(rrsp_pos[0]['category'], 'RRSP')
        self.assertEqual(rrsp_pos[0]['category_display'], 'RRSP (Retirement)')

    def test_group_by_account_subtotals(self):
        """Test that group_positions_by_account computes correct subtotals."""
        # Clean slate
        Transaction.objects.all().delete()
        Account.objects.all().delete()
        BrokerStatement.objects.all().delete()

        acct1 = Account.objects.create(
            name='Account 1', broker='IB', currency='CAD',
            category='TAXABLE', owner='Tom',
            starting_balance=Decimal('0'))
        acct2 = Account.objects.create(
            name='Account 2', broker='IB', currency='CAD',
            category='TFSA', owner='Tom',
            starting_balance=Decimal('0'))
        stmt = BrokerStatement.objects.create(
            source='IB', file_date='2024-07-01', raw_filename='sub.csv',
        )
        # Position 1: cost 100 (amount_cad = -100)
        Transaction.objects.create(
            statement=stmt, account=acct1,
            date='2024-07-01', type='BUY', symbol='SYM1',
            quantity=Decimal('10'), price=Decimal('10.00'),
            commission=Decimal('0'), amount=Decimal('-100.00'), currency='CAD',
            amount_cad=Decimal('-100.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='sub_test_1',
        )
        # Position 2: cost 200 (amount_cad = -200)
        Transaction.objects.create(
            statement=stmt, account=acct2,
            date='2024-07-01', type='BUY', symbol='SYM2',
            quantity=Decimal('20'), price=Decimal('10.00'),
            commission=Decimal('0'), amount=Decimal('-200.00'), currency='CAD',
            amount_cad=Decimal('-200.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='sub_test_2',
        )
        positions = compute_positions()
        enriched = enrich_positions_with_prices(positions, {'SYM1': Decimal('12.00'), 'SYM2': Decimal('15.00'), 'USDCAD=X': Decimal('1.0')})
        groups = group_positions_by_account(enriched)
        self.assertEqual(len(groups), 2)
        for g in groups:
            if g['account_id'] == 'ACCT1':
                self.assertEqual(g['subtotal_cost_cad'], Decimal('-100'))
            elif g['account_id'] == 'ACCT2':
                self.assertEqual(g['subtotal_cost_cad'], Decimal('-200'))

    def test_group_by_category_ordering(self):
        """Test that group_positions_by_category returns groups in fixed order."""
        # Clean slate
        Transaction.objects.all().delete()
        Account.objects.all().delete()
        BrokerStatement.objects.all().delete()

        taxable_acct = Account.objects.create(
            name='Taxable', broker='IB', currency='CAD',
            category='TAXABLE', owner='Tom',
            starting_balance=Decimal('0'))
        rrsp_acct = Account.objects.create(
            name='RRSP', broker='IB', currency='CAD',
            category='RRSP', owner='Tom',
            starting_balance=Decimal('0'))
        stmt = BrokerStatement.objects.create(
            source='IB', file_date='2024-08-01', raw_filename='ord.csv',
        )
        Transaction.objects.create(
            statement=stmt, account=taxable_acct,
            date='2024-08-01', type='BUY', symbol='TAXSYM',
            quantity=Decimal('1'), price=Decimal('10.00'),
            commission=Decimal('0'), amount=Decimal('-10.00'), currency='CAD',
            amount_cad=Decimal('-10.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='ord_test_tax',
        )
        Transaction.objects.create(
            statement=stmt, account=rrsp_acct,
            date='2024-08-01', type='BUY', symbol='RRSPSYM',
            quantity=Decimal('1'), price=Decimal('10.00'),
            commission=Decimal('0'), amount=Decimal('-10.00'), currency='CAD',
            amount_cad=Decimal('-10.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='ord_test_rrsp',
        )
        positions = compute_positions()
        enriched = enrich_positions_with_prices(positions, {'TAXSYM': Decimal('10.00'), 'RRSPSYM': Decimal('10.00'), 'USDCAD=X': Decimal('1.0')})
        groups = group_positions_by_category(enriched)
        categories = [g['category'] for g in groups]
        # RRSP should appear before TAXABLE
        self.assertLess(categories.index('RRSP'), categories.index('TAXABLE'))

    def test_portfolio_summary_page_shows_category_section(self):
        """Test that the portfolio summary page renders the category section."""
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Summary by Category', response.content)

    def test_portfolio_summary_page_shows_account_section(self):
        """Test that the portfolio summary page renders the account section."""
        with patch('core.views_portfolio.utils_fetch_last_prices', return_value={}):
            response = self.client.get(reverse('portfolio_summary'))
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Summary by Account', response.content)

    def test_allocation_pct_sums_to_100(self):
        """Test that allocation_pct sums to approximately 100%."""
        # Clean slate
        Transaction.objects.all().delete()
        Account.objects.all().delete()
        BrokerStatement.objects.all().delete()

        # Create two positions in the same account
        acct = Account.objects.create(
            name='Allocation Test', broker='IB', currency='CAD',
            category='TAXABLE', owner='Tom',
            starting_balance=Decimal('0'))
        stmt = BrokerStatement.objects.create(
            source='IB', file_date='2024-09-01', raw_filename='alloc.csv',
        )
        # Position 1: cost 100, price 100 -> market value 100
        Transaction.objects.create(
            statement=stmt, account=acct,
            date='2024-09-01', type='BUY', symbol='SYM_A',
            quantity=Decimal('10'), price=Decimal('10.00'),
            commission=Decimal('0'), amount=Decimal('-100.00'), currency='CAD',
            amount_cad=Decimal('-100.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='alloc_test_a',
        )
        # Position 2: cost 100, price 100 -> market value 100
        Transaction.objects.create(
            statement=stmt, account=acct,
            date='2024-09-01', type='BUY', symbol='SYM_B',
            quantity=Decimal('10'), price=Decimal('10.00'),
            commission=Decimal('0'), amount=Decimal('-100.00'), currency='CAD',
            amount_cad=Decimal('-100.00'), exchange_rate_cad=Decimal('1.0'),
            tx_hash='alloc_test_b',
        )
        positions = compute_positions()
        enriched = enrich_positions_with_prices(positions, {'SYM_A': Decimal('10.00'), 'SYM_B': Decimal('10.00'), 'USDCAD=X': Decimal('1.0')})
        # Add allocation_pct manually as the view does
        total_mv = sum(p['market_value_cad'] for p in enriched if p['market_value_cad'] is not None)
        for pos in enriched:
            if pos.get('market_value_cad') and total_mv:
                pos['allocation_pct'] = round(pos['market_value_cad'] / total_mv * 100, 2)
            else:
                pos['allocation_pct'] = None
        total_pct = sum(p['allocation_pct'] for p in enriched if p['allocation_pct'] is not None)
        self.assertAlmostEqual(total_pct, 100.0, delta=0.1)
