"""
Pytest configuration for Playwright end-to-end tests.
Provides browser fixtures and Django test client setup.
"""
import os
import pytest
from playwright.sync_api import sync_playwright

# Set Django settings for tests
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')
os.environ.setdefault('DJANGO_SECRET_KEY', 'e2e-test-secret-key-not-for-production')
os.environ.setdefault('DEBUG', 'True')
os.environ.setdefault('ALLOWED_HOSTS', 'localhost,127.0.0.1')


@pytest.fixture(scope="session")
def browser_type():
    """Provide Playwright browser type for the session."""
    with sync_playwright() as p:
        yield p.chromium


@pytest.fixture(scope="session")
def browser(browser_type):
    """Provide a browser instance for the session."""
    with browser_type.launch(headless=True) as browser:
        yield browser


@pytest.fixture
def page(browser):
    """Provide a new page for each test."""
    ctx = browser.new_context(
        base_url="http://127.0.0.1:8000",
        viewport={"width": 1920, "height": 1080},
        java_script_enabled=True
    )
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture
def api_page(browser):
    """Provide a page fixture for API testing with network interception."""
    ctx = browser.new_context(
        base_url="http://127.0.0.1:8000",
        viewport={"width": 1920, "height": 1080}
    )
    page = ctx.new_page()
    yield page
    ctx.close()
