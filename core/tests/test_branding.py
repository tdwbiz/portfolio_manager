from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from core.models import BrandingConfig, Account


class BrandingMigrationTests(TestCase):
    def test_branding_config_loads(self):
        config = BrandingConfig.load()
        self.assertEqual(config.company_name, "TS Money")
        self.assertEqual(config.primary_color, "#1A3F8B")
        self.assertEqual(config.secondary_color, "#2D5FA0")
        self.assertEqual(config.success_color, "#2FA02A")
        self.assertEqual(config.danger_color, "#C0392B")
        self.assertEqual(config.navbar_bg_color, "#1A3F8B")
        self.assertEqual(config.navbar_text_color, "#FFFFFF")
        self.assertEqual(config.footer_bg_color, "#1A3F8B")
        self.assertTrue(config.show_logo)
        self.assertFalse(config.show_tagline)


class BannerPresenceTests(TestCase):
    def setUp(self):
        self.client = Client()
        User = get_user_model()
        self.user = User.objects.create_user("testuser", password="testpass")
        self.client.force_login(self.user)

    def _get(self, url_name):
        return self.client.get(reverse(url_name))

    def test_banner_on_dashboard(self):
        r = self._get("dashboard")
        self.assertContains(r, "site-banner")
        self.assertContains(r, "tsmoney_banner.jpg")

    def test_banner_on_portfolio(self):
        r = self._get("portfolio_summary")
        self.assertContains(r, "site-banner")

    def test_favicon_in_head(self):
        r = self._get("dashboard")
        self.assertContains(r, "tsmoney_icon.jpg")

    def test_tsmoney_css_linked(self):
        r = self._get("dashboard")
        self.assertContains(r, "tsmoney.css")

    def test_accounts_page_has_page_header(self):
        r = self._get("accounts_list")
        self.assertContains(r, "page-header")
        self.assertContains(r, "btn btn-primary")

    def test_transaction_list_has_page_header(self):
        r = self._get("transaction_list")
        self.assertContains(r, "page-header")

    def test_accounts_page_has_bootstrap_table(self):
        Account.objects.create(account_id="TEST01", broker="IB", currency="USD")
        r = self._get("accounts_list")
        self.assertContains(r, 'class="table')

    def test_transaction_list_has_filter_panel(self):
        r = self._get("transaction_list")
        self.assertContains(r, "filter-panel")

    def test_login_page_has_branding(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, "tsmoney_icon.jpg")
        self.assertContains(r, "TS Money")

    def test_footer_has_branding(self):
        r = self._get("dashboard")
        self.assertContains(r, "TS Money")
        self.assertContains(r, "invest like you mean it")
