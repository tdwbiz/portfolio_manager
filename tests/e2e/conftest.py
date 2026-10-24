"""
Pytest configuration for Playwright end-to-end tests.
Provides browser fixtures and Django test client setup.
"""
import os
import pytest

from pw_session import get_playwright

# Set Django settings for tests
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')
os.environ.setdefault('DJANGO_SECRET_KEY', 'e2e-test-secret-key-not-for-production')
os.environ.setdefault('DEBUG', 'True')
os.environ.setdefault('ALLOWED_HOSTS', 'localhost,127.0.0.1')
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')


@pytest.fixture(scope="session")
def browser_type():
    """Provide Playwright browser type for the session (shared singleton)."""
    yield get_playwright().chromium


@pytest.fixture(scope="session")
def browser(browser_type):
    """Provide a browser instance for the session."""
    with browser_type.launch(headless=True) as browser:
        yield browser


@pytest.fixture
def page(browser, live_server):
    """Provide a new page for each test against the pytest-django live server."""
    ctx = browser.new_context(
        base_url=live_server.url,
        viewport={"width": 1920, "height": 1080},
        java_script_enabled=True
    )
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture
def api_page(browser, live_server):
    """Provide a page fixture for API testing with network interception."""
    ctx = browser.new_context(
        base_url=live_server.url,
        viewport={"width": 1920, "height": 1080}
    )
    page = ctx.new_page()
    yield page
    ctx.close()


@pytest.fixture
def test_user(django_user_model):
    """Create a dedicated user for the conftest-based login tests."""
    return django_user_model.objects.create_user(
        username="e2euser", password="e2e-pass-123"
    )
