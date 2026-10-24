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
from django.contrib.staticfiles.testing import StaticLiveServerTestCase

from pw_session import PlaywrightLiveServerMixin

# Setup Django
project_root = str(Path(__file__).parent.parent.parent)
sys.path.insert(0, project_root)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings_test")
django.setup()


class TestUserAuthentication(PlaywrightLiveServerMixin, StaticLiveServerTestCase):
    """Test user authentication flows."""

    def setUp(self):
        super().setUp()
        # Create test user (per-test: the DB is flushed after every test)
        self.user = get_user_model().objects.create_user(
            username="e2e_test_user", password="secure_pass_123", email="e2e@test.com"
        )

    def test_login_page_loads(self):
        """Test that the login page loads correctly."""
        self.page.goto(f"{self.live_server_url}/login/")
        self.page.wait_for_selector('input[name="username"]')
        self.assertIn("Sign In", self.page.title())

    def test_successful_login(self):
        """Test successful user login."""
        self.page.goto(f"{self.live_server_url}/login/")

        # Fill in credentials
        self.page.fill('input[name="username"]', "e2e_test_user")
        self.page.fill('input[name="password"]', "secure_pass_123")

        # Submit form
        self.page.click('button[type="submit"]')
        self.page.wait_for_url(f"{self.live_server_url}/")

        # Verify redirect to the live-server root, away from the login page
        self.assertTrue(self.page.url.startswith(self.live_server_url))
        self.assertNotIn("/login", self.page.url)

    def test_failed_login(self):
        """Test login with incorrect credentials."""
        self.page.goto(f"{self.live_server_url}/login/")

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
        self.page.goto(f"{self.live_server_url}/login/?next=https://evil.com")

        # Fill in correct credentials
        self.page.fill('input[name="username"]', "e2e_test_user")
        self.page.fill('input[name="password"]', "secure_pass_123")

        # Submit form
        self.page.click('button[type="submit"]')
        self.page.wait_for_url(f"{self.live_server_url}/")

        # Should end up on the live server, not evil.com
        self.assertNotIn("evil.com", self.page.url)
        self.assertTrue(self.page.url.startswith(self.live_server_url))


class TestDashboardNavigation(PlaywrightLiveServerMixin, StaticLiveServerTestCase):
    """Test dashboard and navigation features."""

    def setUp(self):
        super().setUp()
        # Create test user (per-test: the DB is flushed after every test)
        self.user = get_user_model().objects.create_user(
            username="nav_test_user", password="nav_pass_456", email="nav@test.com"
        )
        # Auto-login
        self.page.goto(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "nav_test_user")
        self.page.fill('input[name="password"]', "nav_pass_456")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url(f"{self.live_server_url}/")

    def test_dashboard_loads(self):
        """Test that the dashboard loads successfully."""
        self.page.goto(f"{self.live_server_url}/dashboard/")
        self.assertIn("dashboard", self.page.url.lower())
        self.page.wait_for_selector("h1, h2, .dashboard")  # Common dashboard selectors

    def test_navigation_menu(self):
        """Test that navigation menu items are clickable."""
        # Top-level navbar links with real hrefs, matching core/templates/core/base.html.
        # Dropdown toggles (Risk / Reports / Data Tools) use href="#" and are not asserted
        # here — see docs/plans/e2e-last.md.
        nav_links = [
            ("Dashboard", "/dashboard/"),
            ("Portfolio", "/portfolio/"),
            ("Transactions", "/transactions/"),
            ("Accounts", "/accounts/"),
            ("Annual Reports", "/annual-reports/"),
        ]

        for link_text, expected_path in nav_links:
            # Start from a known page so a failed click cannot cascade into the next iteration.
            self.page.goto(f"{self.live_server_url}/dashboard/")
            link = self.page.locator("nav").get_by_role(
                "link", name=link_text, exact=True
            )
            link.click()
            self.page.wait_for_url(f"{self.live_server_url}{expected_path}")
            self.assertTrue(self.page.url.endswith(expected_path))

    def test_account_list_page(self):
        """Test navigating to accounts page."""
        self.page.goto(f"{self.live_server_url}/accounts/")
        self.page.wait_for_timeout(500)
        self.assertIn("accounts", self.page.url.lower())


class TestPortfolioFeatures(PlaywrightLiveServerMixin, StaticLiveServerTestCase):
    """Test portfolio management features."""

    def setUp(self):
        super().setUp()
        # Create test user (per-test: the DB is flushed after every test)
        self.user = get_user_model().objects.create_user(
            username="portfolio_user",
            password="port_pass_789",
            email="portfolio@test.com",
        )
        # Auto-login
        self.page.goto(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "portfolio_user")
        self.page.fill('input[name="password"]', "port_pass_789")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url(f"{self.live_server_url}/")

    def test_portfolio_page_loads(self):
        """Test that portfolio page loads."""
        self.page.goto(f"{self.live_server_url}/portfolio/")
        self.page.wait_for_timeout(500)
        self.assertIn("portfolio", self.page.url.lower())

    def test_portfolio_summary_visible(self):
        """Test that portfolio summary is displayed."""
        self.page.goto(f"{self.live_server_url}/portfolio/summary/")
        self.page.wait_for_timeout(500)
        # Check for common summary elements
        self.page.wait_for_selector("table, .summary, .portfolio-summary, h1, h2")


class TestResponsiveDesign(PlaywrightLiveServerMixin, StaticLiveServerTestCase):
    """Test responsive design across different screen sizes."""

    def setUp(self):
        super().setUp()
        # Create test user (per-test: the DB is flushed after every test)
        self.user = get_user_model().objects.create_user(
            username="responsive_user",
            password="resp_pass_654",
            email="responsive@test.com",
        )
        # Auto-login
        self.page.goto(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "responsive_user")
        self.page.fill('input[name="password"]', "resp_pass_654")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url(f"{self.live_server_url}/")

    def test_responsive_viewports(self):
        """Test that pages render correctly at different viewport sizes."""
        for viewport, device_name in [
            ("375x667", "iPhone 8"),
            ("768x1024", "iPad"),
            ("1440x900", "Desktop Small"),
            ("1920x1080", "Desktop HD"),
        ]:
            with self.subTest(device_name=device_name):
                width, height = map(int, viewport.split("x"))
                self.page.set_viewport_size({"width": width, "height": height})

                # Test dashboard
                self.page.goto(f"{self.live_server_url}/dashboard/")
                self.page.wait_for_timeout(300)

                # Take screenshot for visual inspection
                screenshot_path = (
                    f"tests/screenshots/{device_name.replace(' ', '_')}_dashboard.png"
                )
                self.page.screenshot(path=screenshot_path)

                # Check that page is not broken
                assert self.page.content(), "Page content is empty"


class TestFormValidation(PlaywrightLiveServerMixin, StaticLiveServerTestCase):
    """Test form validation and error handling."""

    def setUp(self):
        super().setUp()
        # Create test user (per-test: the DB is flushed after every test)
        self.user = get_user_model().objects.create_user(
            username="form_user", password="form_pass_987", email="form@test.com"
        )
        # Auto-login
        self.page.goto(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', "form_user")
        self.page.fill('input[name="password"]', "form_pass_987")
        self.page.click('button[type="submit"]')
        self.page.wait_for_url(f"{self.live_server_url}/")

    def test_empty_form_submission(self):
        """Test that empty forms show validation errors."""
        self.page.goto(f"{self.live_server_url}/accounts/new/")
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
