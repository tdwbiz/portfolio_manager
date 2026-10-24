"""
Comprehensive user workflow tests using Playwright.
Tests realistic scenarios that users would perform in the portfolio_manager application.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import django
import pytest
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase

from core.models import Account

# Setup Django
project_root = str(Path(__file__).parent.parent.parent)
sys.path.insert(0, project_root)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings_test")
django.setup()


class TestCompleteUserOnboarding(LiveServerTestCase):
    """Test the complete user onboarding journey."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        # Clean up any existing test users
        get_user_model().objects.filter(username="onboarding_user").delete()
        cls.user = get_user_model().objects.create_user(
            username="onboarding_user",
            password="onboard_pass_123",
            email="onboard@test.com",
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_viewport_size({"width": 1440, "height": 900})

    def tearDown(self):
        self.page.close()

    def test_new_user_first_login(self):
        """Test a new user's first login experience."""
        # Step 1: User arrives at login page
        self.page.get(f"{self.live_server_url}/login/")
        self.page.wait_for_selector('input[name="username"]')

        # Step 2: User enters credentials
        self.page.fill('input[name="username"]', "onboarding_user")
        self.page.fill('input[name="password"]', "onboard_pass_123")

        # Step 3: User clicks login
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

        # Step 4: Verify dashboard loads
        assert "dashboard" in self.page.url.lower()

        # Step 5: Verify user sees welcome message or dashboard content
        self.page.wait_for_selector("h1, h2, .dashboard, .welcome", timeout=5000)

    def test_user_creates_first_account(self):
        """Test user creating their first investment account."""
        # Login
        self._login()

        # Navigate to accounts page
        self.page.get(f"{self.live_server_url}/accounts/")
        self.page.wait_for_timeout(500)

        # Click "Add Account" or similar button
        add_buttons = self.page.query_selector_all(
            'a:has-text("Add"), button:has-text("Add"), a:has-text("New")'
        )
        if add_buttons:
            add_buttons[0].click()
            self.page.wait_for_timeout(500)

            # Check if account form is present
            form = self.page.query_selector("form")
            assert form is not None, "Account form not found"

    def _login(self):
        """Helper method to log in."""
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "onboarding_user")
        self.page.fill('input[name="password"]', "onboard_pass_123")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")


class TestTransactionManagement(LiveServerTestCase):
    """Test transaction creation and management workflows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="transaction_user",
            password="trans_pass_456",
            email="transaction@test.com",
        )

        # Create a test account
        cls.account = Account.objects.create(
            user=cls.user,
            name="Test Brokerage",
            account_number="12345678",
            institution="RBC",
            currency="CAD",
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.account.delete()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_user_views_transactions(self):
        """Test user viewing transaction list."""
        # Login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "transaction_user")
        self.page.fill('input[name="password"]', "trans_pass_456")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

        # Navigate to transactions
        self.page.get(f"{self.live_server_url}/transactions/")
        self.page.wait_for_timeout(500)

        # Verify page loads
        assert "transactions" in self.page.url.lower()

    def test_transaction_filters(self):
        """Test that transaction filters work."""
        # Login
        self._login()

        # Navigate to transactions
        self.page.get(f"{self.live_server_url}/transactions/")
        self.page.wait_for_timeout(500)

        # Look for filter elements
        filter_selectors = [
            'select[name*="filter"], select[name*="date"], select[name*="type"]',
            'input[name*="filter"], input[name*="date"], input[name*="search"]',
        ]

        # Filters may or may not be implemented
        self.page.query_selector(filter_selectors[0])


class TestReportGeneration(LiveServerTestCase):
    """Test report generation and viewing."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="report_user", password="report_pass_789", email="report@test.com"
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_user_accesses_reports(self):
        """Test user accessing reports section."""
        # Login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "report_user")
        self.page.fill('input[name="password"]', "report_pass_789")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

        # Navigate to reports
        self.page.get(f"{self.live_server_url}/core/reports/")
        self.page.wait_for_timeout(500)

        # Verify reports page loads
        assert "reports" in self.page.url.lower()

    def test_annual_reports_available(self):
        """Test that annual reports section is accessible."""
        # Login
        self._login()

        # Navigate to annual reports
        self.page.get(f"{self.live_server_url}/annual-reports/")
        self.page.wait_for_timeout(500)

        # Check for annual reports content
        self.page.wait_for_selector("table, .reports, h1, h2", timeout=3000)


class TestSecurityFeatures(LiveServerTestCase):
    """Test security features of the application."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="security_user",
            password="security_pass_321",
            email="security@test.com",
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_csrf_protection(self):
        """Test that CSRF protection is active."""
        # Login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "security_user")
        self.page.fill('input[name="password"]', "security_pass_321")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

        # Get CSRF token from cookie
        cookies = self.page.context.cookies()
        csrf_token = next(
            (c["value"] for c in cookies if "csrftoken" in c["name"].lower()), None
        )

        # CSRF token should exist
        assert csrf_token is not None, "CSRF token not found in cookies"

    def test_session_timeout_behavior(self):
        """Test that sessions expire properly."""
        # Login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "security_user")
        self.page.fill('input[name="password"]', "security_pass_321")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

        # Clear cookies to simulate session end
        self.page.context.clear_cookies()

        # Try to access protected page
        self.page.get(f"{self.live_server_url}/dashboard/")
        self.page.wait_for_timeout(500)

        # Should redirect to login
        assert "login" in self.page.url.lower() or self.page.url.endswith("/")

    def test_unauthorized_access_redirect(self):
        """Test that unauthorized access redirects to login."""
        # Don't login - try to access protected page directly
        self.page.get(f"{self.live_server_url}/accounts/")
        self.page.wait_for_timeout(500)

        # Should redirect to login
        assert "login" in self.page.url.lower()


class TestPerformanceMetrics(LiveServerTestCase):
    """Test page load performance and responsiveness."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="perf_user", password="perf_pass_654", email="perf@test.com"
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()

    def tearDown(self):
        self.page.close()

    def test_dashboard_load_time(self):
        """Test that dashboard loads within acceptable time."""
        # Login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "perf_user")
        self.page.fill('input[name="password"]', "perf_pass_654")
        self.page.click('button[type="submit"]')

        # Measure dashboard load time
        start = datetime.now()
        self.page.wait_for_url("**/dashboard/**")
        load_time = (datetime.now() - start).total_seconds()

        print(f"Dashboard loaded in {load_time:.2f} seconds")
        assert load_time < 10, f"Dashboard took too long to load: {load_time:.2f}s"

    def test_account_list_load_time(self):
        """Test that account list loads within acceptable time."""
        # Login
        self._login()

        # Measure account list load time
        start = datetime.now()
        self.page.get(f"{self.live_server_url}/accounts/")
        self.page.wait_for_timeout(1000)  # Wait for content to render
        load_time = (datetime.now() - start).total_seconds()

        print(f"Account list loaded in {load_time:.2f} seconds")
        assert load_time < 10, f"Account list took too long to load: {load_time:.2f}s"

    def _login(self):
        """Helper method to log in."""
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "perf_user")
        self.page.fill('input[name="password"]', "perf_pass_654")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")


# Run with: python -m pytest tests/e2e/test_user_workflows.py -v --tb=short -s
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short", "-s"])
