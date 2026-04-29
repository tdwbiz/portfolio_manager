from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from core.models import Account

class AccountCRUDTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            account_id='TEST001',
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
            'account_id': 'NEW001',
            'name': 'New Account',
            'broker': 'IB',
            'currency': 'USD',
            'starting_balance': '500.00',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Account.objects.filter(account_id='NEW001').exists())

    def test_account_create_post_invalid(self):
        response = self.client.post(reverse('account_create'), {
            'account_id': 'NEW002',
            'name': '',
            'broker': 'IB',
            'currency': 'USD',
            'starting_balance': '500.00',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Account.objects.filter(account_id='NEW002').exists())

    def test_account_edit_get(self):
        response = self.client.get(reverse('account_edit', kwargs={'account_id': self.account.account_id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.account.name)

    def test_account_edit_post_valid(self):
        response = self.client.post(reverse('account_edit', kwargs={'account_id': self.account.account_id}), {
            'account_id': self.account.account_id,
            'name': 'Updated Name',
            'broker': 'IB',
            'currency': 'USD',
            'starting_balance': '1000.00',
        })
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'Updated Name')

    def test_account_delete_post(self):
        response = self.client.post(reverse('account_delete', kwargs={'account_id': self.account.account_id}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(account_id=self.account.account_id).exists())

    def test_account_delete_get_not_allowed(self):
        response = self.client.get(reverse('account_delete', kwargs={'account_id': self.account.account_id}))
        self.assertEqual(response.status_code, 405)
