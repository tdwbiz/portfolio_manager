"""Tests for AccountCurrency model and multi-currency support."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Account, AccountCurrency, Transaction
from core.forms import AccountForm, AccountCurrencyFormSet


class AccountCurrencyModelTests(TestCase):
    """Tests for the AccountCurrency model."""

    def setUp(self):
        self.account = Account.objects.create(
            name='Multi-Currency Test',
            broker='IB',
            category='TAXABLE')

    def test_create_cad_sub_account(self):
        """Can create a CAD sub-account."""
        cur = AccountCurrency.objects.create(
            account=self.account,
            currency='CAD',
            starting_balance=Decimal('10000.00'),
            starting_balance_date=date(2020, 1, 1),
        )
        self.assertEqual(cur.currency, 'CAD')
        self.assertEqual(cur.starting_balance, Decimal('10000.00'))
        self.assertEqual(str(cur), 'Multi-Currency Test (CAD)')

    def test_create_usd_sub_account(self):
        """Can create a USD sub-account."""
        cur = AccountCurrency.objects.create(
            account=self.account,
            currency='USD',
            starting_balance=Decimal('5000.00'),
        )
        self.assertEqual(cur.currency, 'USD')
        self.assertEqual(cur.starting_balance, Decimal('5000.00'))

    def test_unique_together_account_currency(self):
        """Cannot create duplicate (account, currency) pairs."""
        AccountCurrency.objects.create(
            account=self.account, currency='CAD',
        )
        with self.assertRaises(Exception):
            AccountCurrency.objects.create(
                account=self.account, currency='CAD',
            )

    def test_ordering_by_currency(self):
        """Sub-accounts are ordered by currency code."""
        AccountCurrency.objects.create(
            account=self.account, currency='USD', starting_balance=Decimal('100'),
        )
        AccountCurrency.objects.create(
            account=self.account, currency='CAD', starting_balance=Decimal('200'),
        )
        currencies = list(self.account.currencies.all())
        self.assertEqual(currencies[0].currency, 'CAD')
        self.assertEqual(currencies[1].currency, 'USD')

    def test_has_multi_currency_property(self):
        """has_multi_currency returns True when >1 sub-account exists."""
        self.assertFalse(self.account.has_multi_currency)

        AccountCurrency.objects.create(
            account=self.account, currency='CAD',
        )
        self.assertFalse(self.account.has_multi_currency)

        AccountCurrency.objects.create(
            account=self.account, currency='USD',
        )
        self.assertTrue(self.account.has_multi_currency)

    def test_currency_labels_property(self):
        """currency_labels returns list of currency codes."""
        AccountCurrency.objects.create(
            account=self.account, currency='CAD',
        )
        AccountCurrency.objects.create(
            account=self.account, currency='USD',
        )
        labels = self.account.currency_labels
        self.assertEqual(labels, ['CAD', 'USD'])


class AccountFormCurrencyTests(TestCase):
    """Tests for AccountForm without deprecated currency/starting_balance fields."""

    def test_account_form_excludes_deprecated_fields(self):
        """AccountForm should NOT include deprecated currency, starting_balance fields."""
        form = AccountForm()
        self.assertNotIn('currency', form.fields)
        self.assertNotIn('starting_balance', form.fields)
        self.assertNotIn('starting_balance_date', form.fields)

    def test_account_form_includes_required_fields(self):
        """AccountForm should include name, broker, category, owner."""
        form = AccountForm()
        self.assertNotIn('account_id', form.fields)
        self.assertIn('name', form.fields)
        self.assertIn('broker', form.fields)
        self.assertIn('category', form.fields)
        self.assertIn('owner', form.fields)


class AccountCurrencyFormSetTests(TestCase):
    """Tests for AccountCurrencyFormSet inline formset."""

    def test_formset_initially_empty(self):
        """Formset starts empty for an account with no currencies."""
        account = Account.objects.create(
            name='Empty', broker='IB')
        formset = AccountCurrencyFormSet(instance=account)
        self.assertEqual(len(formset.forms), 0)

    def test_formset_shows_existing_currencies(self):
        """Formset shows existing sub-accounts."""
        account = Account.objects.create(
            name='Has Currency', broker='IB')
        AccountCurrency.objects.create(
            account=account, currency='CAD', starting_balance=Decimal('1000'),
        )
        formset = AccountCurrencyFormSet(instance=account)
        self.assertEqual(len(formset.forms), 1)
        self.assertEqual(formset.forms[0].initial['currency'], 'CAD')


class AccountCurrencyViewTests(TestCase):
    """Tests for account views with AccountCurrency."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        self.account = Account.objects.create(
            name='View Test Account',
            broker='IB',
            category='TAXABLE')
        AccountCurrency.objects.create(
            account=self.account, currency='CAD',
            starting_balance=Decimal('10000.00'),
            starting_balance_date=date(2020, 1, 1),
        )
        AccountCurrency.objects.create(
            account=self.account, currency='USD',
            starting_balance=Decimal('5000.00'),
            starting_balance_date=date(2020, 1, 1),
        )

    def test_accounts_list_shows_sub_account_currencies(self):
        """Accounts list page shows currency badges from sub-accounts."""
        response = self.client.get(reverse('accounts_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAD')
        self.assertContains(response, 'USD')

    def test_account_detail_shows_sub_accounts(self):
        """Account detail page shows sub-account currency breakdown."""
        response = self.client.get(
            reverse('account_detail', kwargs={'pk': self.account.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sub-Account Currencies')
        self.assertContains(response, 'CAD')
        self.assertContains(response, 'USD')
        self.assertContains(response, '10000.00')
        self.assertContains(response, '5000.00')

    def test_account_detail_shows_multi_currency_badge(self):
        """Multi-currency accounts show a Multi-Currency badge."""
        response = self.client.get(
            reverse('account_detail', kwargs={'pk': self.account.pk})
        )
        self.assertContains(response, 'Multi-Currency')

    def test_account_detail_single_currency(self):
        """Single-currency accounts don't show Multi-Currency badge."""
        account = Account.objects.create(
            name='Single', broker='IB')
        AccountCurrency.objects.create(
            account=account, currency='USD', starting_balance=Decimal('100'),
        )
        response = self.client.get(
            reverse('account_detail', kwargs={'pk': account.pk})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Multi-Currency')

    def test_account_edit_shows_currency_formset(self):
        """Account edit page includes the currency formset."""
        response = self.client.get(
            reverse('account_edit', kwargs={'pk': self.account.pk})
        )
        self.assertEqual(response.status_code, 200)
        # The form should render with currency fields
        self.assertContains(response, 'starting_balance')

    def test_account_edit_post_updates_currencies(self):
        """Posting to account edit saves both account and currency changes."""
        response = self.client.post(
            reverse('account_edit', kwargs={'pk': self.account.pk}),
            {
                'account_id': 'VIEWACC',
                'name': 'Updated Name',
                'broker': 'IB',
                'category': 'TAXABLE',
                'owner': '',
                'currencies-TOTAL_FORMS': '2',
                'currencies-INITIAL_FORMS': '2',
                'currencies-MIN_NUM_FORMS': '0',
                'currencies-MAX_NUM_FORMS': '1000',
                'currencies-0-id': AccountCurrency.objects.get(
                    account=self.account, currency='CAD'
                ).pk,
                'currencies-0-currency': 'CAD',
                'currencies-0-starting_balance': '15000.00',
                'currencies-0-starting_balance_date': '2020-01-01',
                'currencies-1-id': AccountCurrency.objects.get(
                    account=self.account, currency='USD'
                ).pk,
                'currencies-1-currency': 'USD',
                'currencies-1-starting_balance': '7500.00',
                'currencies-1-starting_balance_date': '2020-01-01',
            },
        )
        self.assertEqual(response.status_code, 302)
        cad_cur = AccountCurrency.objects.get(account=self.account, currency='CAD')
        self.assertEqual(cad_cur.starting_balance, Decimal('15000.00'))
        usd_cur = AccountCurrency.objects.get(account=self.account, currency='USD')
        self.assertEqual(usd_cur.starting_balance, Decimal('7500.00'))
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'Updated Name')


class AccountCurrencyTemplateTests(TestCase):
    """Tests for template rendering with AccountCurrency."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

    def test_dashboard_shows_sub_account_currencies(self):
        """Dashboard account table shows currency badges from sub-accounts."""
        account = Account.objects.create(
            name='Dashboard Test', broker='IB')
        AccountCurrency.objects.create(
            account=account, currency='CAD', starting_balance=Decimal('1000'),
        )
        AccountCurrency.objects.create(
            account=account, currency='USD', starting_balance=Decimal('500'),
        )
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAD')
        self.assertContains(response, 'USD')

    def test_accounts_list_shows_multi_currency_account(self):
        """Accounts list page shows multiple currency badges."""
        account = Account.objects.create(
            name='List Test', broker='IB')
        AccountCurrency.objects.create(
            account=account, currency='CAD',
        )
        AccountCurrency.objects.create(
            account=account, currency='USD',
        )
        response = self.client.get(reverse('accounts_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CAD')
        self.assertContains(response, 'USD')


class AccountCurrencyDeleteTests(TestCase):
    """Tests for deleting AccountCurrency via formset."""

    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')
        self.account = Account.objects.create(
            name='Delete Test', broker='IB')
        self.cad_cur = AccountCurrency.objects.create(
            account=self.account, currency='CAD',
            starting_balance=Decimal('10000'),
        )
        self.usd_cur = AccountCurrency.objects.create(
            account=self.account, currency='USD',
            starting_balance=Decimal('5000'),
        )

    def test_delete_currency_via_formset(self):
        """Deleting a currency sub-account via edit form removes it."""
        response = self.client.post(
            reverse('account_edit', kwargs={'pk': self.account.pk}),
            {
                'account_id': 'DELACC',
                'name': 'Delete Test',
                'broker': 'IB',
                'category': 'TAXABLE',
                'owner': '',
                'currencies-TOTAL_FORMS': '2',
                'currencies-INITIAL_FORMS': '2',
                'currencies-MIN_NUM_FORMS': '0',
                'currencies-MAX_NUM_FORMS': '1000',
                'currencies-0-id': self.cad_cur.pk,
                'currencies-0-currency': 'CAD',
                'currencies-0-starting_balance': '10000',
                'currencies-0-starting_balance_date': '',
                # USD is marked for deletion (no _DELETE field = keep, but we omit it)
                'currencies-1-id': self.usd_cur.pk,
                'currencies-1-currency': 'USD',
                'currencies-1-starting_balance': '5000',
                'currencies-1-starting_balance_date': '',
                'currencies-1-DELETE': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            AccountCurrency.objects.filter(account=self.account, currency='USD').exists()
        )
        self.assertTrue(
            AccountCurrency.objects.filter(account=self.account, currency='CAD').exists()
        )
