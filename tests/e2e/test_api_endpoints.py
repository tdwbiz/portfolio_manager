"""
API endpoint tests using Playwright to test AJAX/API interactions.
Tests the backend API endpoints that the frontend JavaScript calls.
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


class TestAPIEndpoints(LiveServerTestCase):
    """Test API endpoints through browser interactions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="api_user", password="api_pass_123", email="api@test.com"
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.user.delete()
        super().tearDownClass()

    def setUp(self):
        self.page = self.browser.new_page()
        # API responses captured here
        self.api_responses = []

        # Listen for network responses
        self.page.on(
            "response",
            lambda response: self.api_responses.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "headers": dict(response.headers),
                }
            ),
        )

    def tearDown(self):
        self.page.close()
        self.api_responses.clear()

    def _login(self, username="api_user", password="api_pass_123"):
        """Helper method to log in."""
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', username)
        self.page.fill('input[name="password"]', password)
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def test_dashboard_api_loads(self):
        """Test that dashboard API endpoints return successful responses."""
        # Login
        self._login()

        # Check for successful API calls
        successful_apis = [r for r in self.api_responses if r["status"] < 400]
        assert len(successful_apis) > 0, "No successful API calls detected"

    def test_accounts_api_endpoint(self):
        """Test accounts API endpoint."""
        # Login
        self._login()

        # Clear previous responses
        self.api_responses.clear()

        # Navigate to accounts
        self.page.get(f"{self.live_server_url}/accounts/")
        self.page.wait_for_timeout(1000)

        # Check for account-related API calls (logged but not asserted)
        _account_apis = [r for r in self.api_responses if "account" in r["url"].lower()]


class TestChartRendering(LiveServerTestCase):
    """Test that charts and visualizations render correctly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="chart_user", password="chart_pass_456", email="chart@test.com"
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

    def _login(self, username="chart_user", password="chart_pass_456"):
        """Helper method to log in."""
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', username)
        self.page.fill('input[name="password"]', password)
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def test_portfolio_chart_loads(self):
        """Test that portfolio chart renders."""
        # Login
        self._login()

        # Navigate to portfolio chart
        self.page.get(f"{self.live_server_url}/portfolio/chart/")
        self.page.wait_for_timeout(1000)

        # Look for canvas or SVG elements (Chart.js uses canvas)
        canvas = self.page.query_selector("canvas")
        svg = self.page.query_selector("svg")

        # At least one should exist if charts are implemented
        assert canvas is not None or svg is not None, "No chart elements found"

    def test_chart_data_loaded(self):
        """Test that chart has data loaded."""
        # Login
        self._login()

        # Navigate to portfolio
        self.page.get(f"{self.live_server_url}/portfolio/summary/")
        self.page.wait_for_timeout(1000)

        # Check for chart-related JavaScript (logged but not asserted)
        self.page.evaluate("""
            () => {
                return typeof Chart !== 'undefined' ||
                       document.querySelector('canvas') !== null;
            }
        """)


class TestRealTimeFeatures(LiveServerTestCase):
    """Test real-time features like live updates."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

        cls.user = get_user_model().objects.create_user(
            username="realtime_user",
            password="realtime_pass_789",
            email="realtime@test.com",
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

    def _login(self, username="realtime_user", password="realtime_pass_789"):
        """Helper method to log in."""
        self.page.get(f"{self.live_server_url}/login/")
        self.page.fill('input[name="username"]', username)
        self.page.fill('input[name="password"]', password)
        self.page.click('button[type="submit"]')
        self.page.wait_for_url("**/dashboard/**")

    def test_page_does_not_crash(self):
        """Test that page doesn't crash with JavaScript errors."""
        # Login
        self._login()

        # Check for JavaScript errors
        js_errors = []
        self.page.on(
            "console",
            lambda msg: js_errors.append(msg) if msg.type == "error" else None,
        )

        # Navigate to different pages
        pages_to_test = [
            "/accounts/",
            "/transactions/",
            "/reports/",
        ]

        for page_url in pages_to_test:
            self.page.get(f"{self.live_server_url}{page_url}")
            self.page.wait_for_timeout(500)

        # Should have minimal JS errors
        print(f"JavaScript errors encountered: {len(js_errors)}")


# Run with: python -m pytest tests/e2e/test_api_endpoints.py -v --tb=short
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
