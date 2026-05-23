from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from core.models import BrandingConfig


class BrandingMigrationTests(TestCase):
    def test_branding_config_loads(self):
        config = BrandingConfig.load()
        self.assertEqual(config.company_name, 'TS Money')
        self.assertEqual(config.primary_color, '#1A3F8B')


class BannerPresenceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user('testuser', password='testpass')
        self.client.force_login(self.user)

    def _get(self, url_name):
        return self.client.get(reverse(url_name))

    def test_banner_on_dashboard(self):
        r = self._get('dashboard')
        self.assertContains(r, 'site-banner')
        self.assertContains(r, 'tsmoney_banner.jpg')

    def test_banner_on_portfolio(self):
        r = self._get('portfolio_summary')
        self.assertContains(r, 'site-banner')

    def test_favicon_in_head(self):
        r = self._get('dashboard')
        self.assertContains(r, 'tsmoney_icon.jpg')

    def test_tsmoney_css_linked(self):
        r = self._get('dashboard')
        self.assertContains(r, 'tsmoney.css')

    def test_accounts_page_has_page_header(self):
        r = self._get('accounts_list')
        self.assertContains(r, 'page-header')
        self.assertContains(r, 'btn btn-primary')

    def test_transaction_list_has_page_header(self):
        r = self._get('transaction_list')
        self.assertContains(r, 'page-header')
