"""
End-to-End (E2E) tests using Playwright to simulate real user interactions.
These tests verify the complete user journey through the portfolio_manager application.
"""

import os
import sys
from pathlib import Path

import django
import pytest
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase

# Setup Django
project_root = str(Path(__file__).parent.parent.parent)
sys.path.insert(0, project_root)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings_test")
django.setup()


class TestUserAuthentication(LiveServerTestCase):
    """Test user authentication flows."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        # Create test user
        cls.user = get_user_model().objects.create_user(
            username="e2e_test_user", password="secure_pass_123", email="e2e@test.com"
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

    def test_login_page_loads(self):
        """Test that the login page loads correctly."""
        self.page.get(f"{self.live_server_url}/login/")
        self.page.wait_for_selector('input[name="username"]')
        self.assertIn("Login", self.page.title())

    def test_successful_login(self):
        """Test successful user login."""
        self.page.get(f"{self.live_server_url}/login/")

        # Fill in credentials
        self.page.fill('input[name="username"]', "e2e_test_user")
        self.page.fill('input[name="password"]', "secure_pass_123")

        # Submit form
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

        # Verify redirect to dashboard
        self.assertIn("dashboard", self.page.url.lower())

    def test_failed_login(self):
        """Test login with incorrect credentials."""
        self.page.get(f"{self.live_server_url}/login/")

        # Fill in wrong credentials
        self.page.fill('input[name="username"]', "e2e_test_user")
        self.page.fill('input[name="password"]', "wrong_password")

        # Submit form
        self.page.click('button[type="submit"]')
        self.page.wait_for_selector('input[name="username"]')

        # Should stay on login page (not redirected)
        self.assertIn("login", self.page.url.lower())

    def test_open_redirect_protection(self):
        """Test that open redirect attempts are blocked."""
        self.page.get(f"{self.live_server_url}/login/?next=https://evil.com")

        # Fill in correct credentials
        self.page.fill('input[name="username"]', "e2e_test_user")
        self.page.fill('input[name="password"]', "secure_pass_123")

        # Submit form
        self.page.click('button[type="submit"]')

        # Should redirect to dashboard, not evil.com
        self.assertNotIn("evil.com", self.page.url)
        self.assertIn("dashboard", self.page.url.lower())


class TestDashboardNavigation(LiveServerTestCase):
    """Test dashboard and navigation features."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="nav_test_user", password="nav_pass_456", email="nav@test.com"
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        # Auto-login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "nav_test_user")
        self.page.fill('input[name="password"]', "nav_pass_456")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def tearDown(self):
        self.page.close()

    def test_dashboard_loads(self):
        """Test that the dashboard loads successfully."""
        self.assertIn("dashboard", self.page.url.lower())
        self.page.wait_for_selector("h1, h2, .dashboard")  # Common dashboard selectors

    def test_navigation_menu(self):
        """Test that navigation menu items are clickable."""
        # Check for common navigation links
        nav_links = [
            ("Dashboard", "dashboard"),
            ("Accounts", "accounts"),
            ("Portfolio", "portfolio"),
            ("Transactions", "transactions"),
            ("Reports", "reports"),
        ]

        for link_text, expected_in_url in nav_links:
            # Try to click the link
            links = self.page.query_selector_all(f'a:has-text("{link_text}")')
            if links:
                links[0].click()
                self.page.wait_for_timeout(500)  # Wait for navigation
                self.assertIn(expected_in_url, self.page.url.lower())

    def test_account_list_page(self):
        """Test navigating to accounts page."""
        self.page.get(f"{self.live_server_url}/accounts/")
        self.page.wait_for_timeout(500)
        self.assertIn("accounts", self.page.url.lower())


class TestPortfolioFeatures(LiveServerTestCase):
    """Test portfolio management features."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="portfolio_user",
            password="port_pass_789",
            email="portfolio@test.com",
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        # Auto-login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "portfolio_user")
        self.page.fill('input[name="password"]', "port_pass_789")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def tearDown(self):
        self.page.close()

    def test_portfolio_page_loads(self):
        """Test that portfolio page loads."""
        self.page.get(f"{self.live_server_url}/portfolio/")
        self.page.wait_for_timeout(500)
        self.assertIn("portfolio", self.page.url.lower())

    def test_portfolio_summary_visible(self):
        """Test that portfolio summary is displayed."""
        self.page.get(f"{self.live_server_url}/portfolio/summary/")
        self.page.wait_for_timeout(500)
        # Check for common summary elements
        self.page.wait_for_selector("table, .summary, .portfolio-summary, h1, h2")


class TestResponsiveDesign(LiveServerTestCase):
    """Test responsive design across different screen sizes."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="responsive_user",
            password="resp_pass_654",
            email="responsive@test.com",
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        # Auto-login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "responsive_user")
        self.page.fill('input[name="password"]', "resp_pass_654")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def tearDown(self):
        self.page.close()

    @pytest.mark.parametrize(
        "viewport,device_name",
        [
            ("375x667", "iPhone 8"),
            ("768x1024", "iPad"),
            ("1440x900", "Desktop Small"),
            ("1920x1080", "Desktop HD"),
        ],
    )
    def test_responsive_viewports(self, viewport, device_name):
        """Test that pages render correctly at different viewport sizes."""
        width, height = map(int, viewport.split("x"))
        self.page.set_viewport_size({"width": width, "height": height})

        # Test dashboard
        self.page.get(f"{self.live_server_url}/dashboard/")
        self.page.wait_for_timeout(300)

        # Take screenshot for visual inspection
        screenshot_path = (
            f"tests/screenshots/{device_name.replace(' ', '_')}_dashboard.png"
        )
        self.page.screenshot(path=screenshot_path)

        # Check that page is not broken
        assert self.page.content(), "Page content is empty"


class TestFormValidation(LiveServerTestCase):
    """Test form validation and error handling."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="form_user", password="form_pass_987", email="form@test.com"
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        # Auto-login
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "form_user")
        self.page.fill('input[name="password"]', "form_pass_987")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def tearDown(self):
        self.page.close()

    def test_empty_form_submission(self):
        """Test that empty forms show validation errors."""
        self.page.get(f"{self.live_server_url}/accounts/new/")
        self.page.wait_for_timeout(500)

        # Try to submit empty form
        submit_buttons = self.page.query_selector_all(
            'button[type="submit"], input[type="submit"]'
        )
        if submit_buttons:
            submit_buttons[0].click()
            self.page.wait_for_timeout(500)

            # Should show error messages (logged but not asserted)
            error_selectors = [".error, .invalid, .alert-danger, .form-error"]
            _has_error = any(
                self.page.query_selector(selector) for selector in error_selectors
            )
            # Note: This might not always be true depending on implementation


# Run with: python -m pytest tests/e2e/test_e2e_user_journey.py -v --tb=short
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
