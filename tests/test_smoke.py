"""
Smoke tests - Quick sanity checks to verify the testing infrastructure works.
Run these first to ensure everything is set up correctly.
"""
import os
import sys
import pytest
import django
from pathlib import Path

# Setup Django
project_root = str(Path(__file__).parent.parent)
sys.path.insert(0, project_root)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')
django.setup()

from django.test import StaticLiveServerTestCase
from django.contrib.auth import get_user_model


class TestInfrastructure(StaticLiveServerTestCase):
    """Test that the testing infrastructure is working."""
    
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)
        
        # Create test user
        cls.user = get_user_model().objects.create_user(
            username='smoke_test_user',
            password='smoke_pass_123',
            email='smoke@test.com'
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
    
    @pytest.mark.smoke
    def test_playwright_works(self):
        """Verify Playwright is installed and working."""
        self.page.get(self.live_server_url)
        assert self.page.title() is not None
        print("✓ Playwright is working correctly")
    
    @pytest.mark.smoke
    def test_django_server_running(self):
        """Verify Django live server is running."""
        self.page.get(f'{self.live_server_url}/core/login/')
        # Should not raise an exception
        assert self.page.content() is not None
        print("✓ Django live server is responding")
    
    @pytest.mark.smoke
    def test_login_page_accessible(self):
        """Verify login page is accessible."""
        self.page.get(f'{self.live_server_url}/core/login/')
        self.page.wait_for_selector('input[name="username"]', timeout=5000)
        assert 'Login' in self.page.title() or 'login' in self.page.url.lower()
        print("✓ Login page is accessible")
    
    @pytest.mark.smoke
    def test_user_can_login(self):
        """Verify user can successfully log in."""
        self.page.get(f'{self.live_server_url}/core/login/')
        
        # Fill credentials
        self.page.fill('input[name="username"]', 'smoke_test_user')
        self.page.fill('input[name="password"]', 'smoke_pass_123')
        
        # Submit
        self.page.click('button[type="submit"]')
        
        # Should redirect to dashboard
        self.page.wait_for_url('**/dashboard/**', timeout=10000)
        assert 'dashboard' in self.page.url.lower()
        print("✓ User authentication works")


# Run with: python -m pytest tests/test_smoke.py -v -s
if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
