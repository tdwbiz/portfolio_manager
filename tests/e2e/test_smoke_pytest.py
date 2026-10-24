"""
Smoke tests - Quick sanity checks to verify the testing infrastructure works.
Run these first to ensure everything is set up correctly.
"""

import os
import sys
from pathlib import Path

import django
import pytest
from django.contrib.auth import get_user_model
from django.test import LiveServerTestCase

from pw_session import PlaywrightLiveServerMixin

# Setup Django
project_root = str(Path(__file__).parent.parent.parent)
sys.path.insert(0, project_root)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "portfolio_manager.settings_test")
django.setup()

# Note: This test file uses LiveServerTestCase with Playwright.
# It requires a live Django server. Run with: pytest tests/e2e/test_smoke_pytest.py -v -s


class TestInfrastructure(PlaywrightLiveServerMixin, LiveServerTestCase):
    """Test that the testing infrastructure is working."""

    def setUp(self):
        super().setUp()
        # Create test user (per-test: the DB is flushed after every test)
        self.user = get_user_model().objects.create_user(
            username="smoke_test_user",
            password="smoke_pass_123",
            email="smoke@test.com",
        )

    @pytest.mark.smoke
    def test_playwright_works(self):
        """Verify Playwright is installed and working."""
        self.page.goto(self.live_server_url)
        assert self.page.title() is not None
        print("✓ Playwright is working correctly")

    @pytest.mark.smoke
    def test_django_server_running(self):
        """Verify Django live server is running."""
        self.page.goto(f"{self.live_server_url}/login/")
        # Should not raise an exception
        assert self.page.content() is not None
        print("✓ Django live server is responding")

    @pytest.mark.smoke
    def test_login_page_accessible(self):
        """Verify login page is accessible."""
        self.page.goto(f"{self.live_server_url}/login/")
        self.page.wait_for_selector('input[name="username"]', timeout=5000)
        assert "Login" in self.page.title() or "login" in self.page.url.lower()
        print("✓ Login page is accessible")

    @pytest.mark.smoke
    def test_user_can_login(self):
        """Verify user can successfully log in."""
        self.page.goto(f"{self.live_server_url}/login/")

        # Fill credentials
        self.page.fill('input[name="username"]', "smoke_test_user")
        self.page.fill('input[name="password"]', "smoke_pass_123")

        # Submit
        self.page.click('button[type="submit"]')

        # Should redirect to the live-server root, away from /login/
        self.page.wait_for_url(f"{self.live_server_url}/")
        assert self.page.url.startswith(self.live_server_url)
        assert "/login" not in self.page.url
        print("✓ User authentication works")


# Run with: python -m pytest tests/e2e/test_smoke_pytest.py -v -s
if __name__ == "__main__":
    pytest.main(["tests/e2e/test_smoke_pytest.py", "-v", "-s"])
