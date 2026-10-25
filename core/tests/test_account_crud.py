from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import Account, AccountCurrency
from core.forms import AccountForm


class AccountCRUDTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.client.login(username='testuser', password='testpass123')

        self.account = Account.objects.create(
            account_id='TESTACC',
            name='Test Account',
            broker='IB',
            currency='USD',
            starting_balance=Decimal('1000.00'),
        )

    def test_account_list_loads(self):
        response = self.client.get(reverse('accounts_list'))
        self.assertEqual(response.status_code, 200)

    def test_account_create_get(self):
        response = self.client.get(reverse('account_create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add Account")

    def test_account_create_post_valid(self):
        response = self.client.post(reverse('account_create'), {
            'account_id': 'NEWACC',
            'name': 'New Account',
            'broker': 'IB',
            'category': 'TAXABLE',
            'owner': '',
        })
        # Redirects to account_edit page to set up currencies
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Account.objects.filter(account_id='NEWACC').exists())

    def test_account_create_post_invalid(self):
        response = self.client.post(reverse('account_create'), {
            'account_id': 'NEWBACC',
            'name': '',
            'broker': 'IB',
            'category': 'TAXABLE',
            'owner': '',
            'currency': 'USD',
            'starting_balance': '500.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(account_id='NEWBACC').exists())

    # ── account_id must not contain digits (decision 3) ────────────────

    def test_account_create_post_rejects_digit_account_id(self):
        """Posting an account_id containing a digit is rejected."""
        response = self.client.post(reverse('account_create'), {
            'account_id': '90000001',
            'name': 'Number Account',
            'broker': 'IB',
            'category': 'TAXABLE',
            'owner': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(account_id='90000001').exists())
        self.assertIn('account_id', response.context['form'].errors)

    def test_account_form_rejects_mixed_digit_account_id(self):
        """An account_id mixing letters and digits is rejected."""
        form = AccountForm(data={
            'account_id': 'ACC2',
            'name': 'Mixed',
            'broker': 'IB',
            'category': 'TAXABLE',
        })
        self.assertFalse(form.is_valid())
        self.assertIn('account_id', form.errors)

    def test_account_form_accepts_letter_only_account_id(self):
        """A letter-only account_id passes validation."""
        form = AccountForm(data={
            'account_id': 'TOMTFSA',
            'name': 'Letters',
            'broker': 'IB',
            'category': 'TAXABLE',
        })
        self.assertTrue(form.is_valid(), form.errors)

    def test_account_edit_get(self):
        response = self.client.get(reverse('account_edit', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.account.name)

    def test_account_edit_post_valid(self):
        # Create a currency sub-account for the formset
        AccountCurrency.objects.create(
            account=self.account, currency='USD',
            starting_balance=Decimal('1000.00'),
        )
        response = self.client.post(reverse('account_edit', kwargs={'pk': self.account.pk}), {
            'account_id': self.account.account_id,
            'name': 'Updated Name',
            'broker': 'IB',
            'category': self.account.category,
            'owner': self.account.owner,
            'currencies-TOTAL_FORMS': '1',
            'currencies-INITIAL_FORMS': '1',
            'currencies-MIN_NUM_FORMS': '0',
            'currencies-MAX_NUM_FORMS': '1000',
            'currencies-0-id': AccountCurrency.objects.get(account=self.account, currency='USD').pk,
            'currencies-0-currency': 'USD',
            'currencies-0-starting_balance': '1000.00',
            'currencies-0-starting_balance_date': '',
        })
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'Updated Name')

    def test_account_delete_post(self):
        response = self.client.post(reverse('account_delete', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(account_id=self.account.account_id).exists())

    def test_account_delete_get_not_allowed(self):
        response = self.client.get(reverse('account_delete', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 405)

    # ── New tests for category & owner fields ─────────────────────────────

    def test_account_default_category(self):
        """When category is not specified, it defaults to 'TAXABLE'."""
        account = Account.objects.create(
            account_id='DEFAULT_CAT',
            name='Default Category Account',
            broker='IB',
        )
        self.assertEqual(account.category, 'TAXABLE')

    def test_account_category_choices(self):
        """Each CATEGORY_CHOICES value returns the correct human-readable label."""
        for code, label in Account.CATEGORY_CHOICES:
            account = Account.objects.create(
                account_id=f'CAT_{code}',
                name=f'Category {code}',
                broker='IB',
                category=code,
            )
            self.assertEqual(account.get_category_display(), label)

    def test_account_owner_optional(self):
        """An account can be created without an owner; owner defaults to ''."""
        account = Account.objects.create(
            account_id='NO_OWNER',
            name='No Owner Account',
            broker='IB',
        )
        self.assertEqual(account.owner, '')
        # Verify it saves without error (already done above, but be explicit)
        account.save()
        account.refresh_from_db()
        self.assertEqual(account.owner, '')

    def test_account_form_includes_category_owner(self):
        """AccountForm should include 'category' and 'owner' in its fields."""
        form = AccountForm()
        self.assertIn('category', form.fields)
        self.assertIn('owner', form.fields)

    def test_accounts_list_shows_category(self):
        """The accounts list page should display the category label."""
        account = Account.objects.create(
            account_id='RRSP001',
            name='RRSP Account',
            broker='IB',
            category='RRSP',
        )
        response = self.client.get(reverse('accounts_list'))
        self.assertContains(response, 'RRSP (Retirement)')
