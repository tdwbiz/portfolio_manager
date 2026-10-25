"""Tests for batch statement generation from RBC anchor."""

import datetime
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import (
    Account,
    AccountCurrency,
    BrokerStatement,
    CoreAnnualReport,
    CoreMonthlyStatement,
    RbcMonthly,
    Transaction,
)
from core.utils.statements import batch_generate_all_statements


class BatchGenerationTests(TestCase):
    """Tests for batch_generate_all_statements from RBC data."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Batch Test Account',
            broker='RBC',
            category='TAXABLE')
        self.statement = BrokerStatement.objects.create(
            source='RBC',
            file_date=datetime.date(2024, 1, 1),
            raw_filename='test_statement.csv',
        )
        # Create AccountCurrency sub-accounts
        AccountCurrency.objects.create(
            account=self.account,
            currency='CAD',
            starting_balance=Decimal('10000'),
            starting_balance_date=date(2023, 1, 1),
        )
        AccountCurrency.objects.create(
            account=self.account,
            currency='USD',
            starting_balance=Decimal('5000'),
            starting_balance_date=date(2023, 1, 1),
        )

    def test_batch_no_rbc_data_returns_empty(self):
        """Batch generation returns empty lists when no RBC data exists."""
        monthly, annual = batch_generate_all_statements(self.account)
        self.assertEqual(monthly, [])
        self.assertEqual(annual, [])

    def test_batch_with_rbc_data_generates_monthly(self):
        """Batch generation creates monthly statements from RBC anchor."""
        # Create RBC data with known ending balance
        RbcMonthly.objects.create(
            account=self.account,
            account_number='TEST-001',
            statement_date=date(2024, 6, 30),
            currency='CAD',
            begin_total_market_value=Decimal('45000'),
            end_total_market_value=Decimal('50000'),
            file_name='rbc_2024_06.pdf',
        )

        monthly, annual = batch_generate_all_statements(self.account)

        # Should have at least one monthly statement (the anchor month)
        self.assertGreater(len(monthly), 0)
        cad_stmts = [s for s in monthly if s.currency == 'CAD']
        self.assertGreater(len(cad_stmts), 0)

        # The latest statement should have ending = RBC end_total_market_value
        latest = max(cad_stmts, key=lambda s: s.statement_month)
        self.assertEqual(latest.ending_market_value, Decimal('50000'))

    def test_batch_with_multi_currency_rbc(self):
        """Batch generation handles multiple currencies from RBC."""
        RbcMonthly.objects.create(
            account=self.account,
            account_number='TEST-001',
            statement_date=date(2024, 6, 30),
            currency='CAD',
            begin_total_market_value=Decimal('45000'),
            end_total_market_value=Decimal('50000'),
            file_name='rbc_2024_06.pdf',
        )
        RbcMonthly.objects.create(
            account=self.account,
            account_number='TEST-001',
            statement_date=date(2024, 6, 30),
            currency='USD',
            begin_total_market_value=Decimal('20000'),
            end_total_market_value=Decimal('25000'),
            file_name='rbc_2024_06.pdf',
        )

        monthly, annual = batch_generate_all_statements(self.account)

        currencies = {s.currency for s in monthly}
        self.assertEqual(currencies, {'CAD', 'USD'})

        # Check CAD ending
        cad_stmts = [s for s in monthly if s.currency == 'CAD']
        latest_cad = max(cad_stmts, key=lambda s: s.statement_month)
        self.assertEqual(latest_cad.ending_market_value, Decimal('50000'))

        # Check USD ending
        usd_stmts = [s for s in monthly if s.currency == 'USD']
        latest_usd = max(usd_stmts, key=lambda s: s.statement_month)
        self.assertEqual(latest_usd.ending_market_value, Decimal('25000'))

    def test_batch_generates_annual_from_monthly(self):
        """Batch generation also creates annual reports from monthly data."""
        # Create RBC data across multiple months in the same year
        for month in range(1, 7):
            RbcMonthly.objects.create(
                account=self.account,
                account_number='TEST-001',
                statement_date=date(2024, month, 28),
                currency='CAD',
                begin_total_market_value=Decimal('40000') + (month * Decimal('1000')),
                end_total_market_value=Decimal('41000') + (month * Decimal('1000')),
                file_name=f'rbc_2024_{month:02d}.pdf',
            )

        monthly, annual = batch_generate_all_statements(self.account)

        # Annual reports should be generated from monthly statements
        self.assertGreater(len(annual), 0)
        annual_2024 = [r for r in annual if r.report_year == 2024]
        self.assertGreater(len(annual_2024), 0)


class BatchStatementsViewTests(TestCase):
    """Tests for the updated monthly/annual statements views."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

        self.account = Account.objects.create(
            name='View Test Account',
            broker='RBC',
            category='TAXABLE')
        AccountCurrency.objects.create(
            account=self.account,
            currency='CAD',
            starting_balance=Decimal('10000'),
            starting_balance_date=date(2023, 1, 1),
        )

    def test_monthly_statements_view_shows_existing(self):
        """Monthly statements view shows existing statements without regenerating."""
        # Create some existing statements
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 6, 1),
            currency='CAD',
            beginning_market_value=Decimal('45000'),
            ending_market_value=Decimal('50000'),
        )

        response = self.client.get(
            reverse('generate_monthly_statements'),
            {'account': str(self.account.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'existing monthly statements')
        self.assertContains(response, '45000')

    def test_monthly_statements_view_regenerate(self):
        """Monthly statements view regenerates when regenerate=1."""
        response = self.client.get(
            reverse('generate_monthly_statements'),
            {'account': str(self.account.pk), 'regenerate': '1'},
        )
        self.assertEqual(response.status_code, 200)
        # Should not crash - even with no transactions, it should show a message
        self.assertContains(response, 'View Test Account')

    def test_monthly_statements_view_batch(self):
        """Monthly statements view uses batch when batch=1."""
        # Create RBC data for batch to use
        RbcMonthly.objects.create(
            account=self.account,
            account_number='TEST-001',
            statement_date=date(2024, 6, 30),
            currency='CAD',
            begin_total_market_value=Decimal('45000'),
            end_total_market_value=Decimal('50000'),
            file_name='rbc_2024_06.pdf',
        )

        response = self.client.get(
            reverse('generate_monthly_statements'),
            {'account': str(self.account.pk), 'batch': '1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Batch-generated')
        self.assertContains(response, '50000')

    def test_annual_reports_view_shows_existing(self):
        """Annual reports view shows existing reports without regenerating."""
        CoreAnnualReport.objects.create(
            account=self.account,
            report_year=2024,
            currency='CAD',
            beginning_value=Decimal('40000'),
            ending_value=Decimal('50000'),
        )

        response = self.client.get(
            reverse('generate_annual_reports'),
            {'account': str(self.account.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'existing annual reports')
        self.assertContains(response, '40000')

    def test_annual_reports_view_regenerate(self):
        """Annual reports view regenerates when regenerate=1."""
        # First create monthly statements
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 1, 1),
            currency='CAD',
            beginning_market_value=Decimal('40000'),
            ending_market_value=Decimal('41000'),
        )
        CoreMonthlyStatement.objects.create(
            account=self.account,
            statement_month=date(2024, 2, 1),
            currency='CAD',
            beginning_market_value=Decimal('41000'),
            ending_market_value=Decimal('42000'),
        )

        response = self.client.get(
            reverse('generate_annual_reports'),
            {'account': str(self.account.pk), 'regenerate': '1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Regenerated')

    def test_monthly_statements_no_account(self):
        """View handles missing account gracefully."""
        response = self.client.get(
            reverse('generate_monthly_statements'),
            {'account': '999999'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not found')
