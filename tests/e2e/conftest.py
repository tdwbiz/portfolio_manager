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
def live_server(django_test_environment):
    """Session-scoped live server that serves static files.

    Shadows pytest-django's ``live_server`` fixture so the fixture-based
    browser tests get static assets the same way
    ``StaticLiveServerTestCase`` does: the WSGI app is wrapped in
    ``StaticFilesHandler`` (Django instantiates it around
    ``_MediaFilesHandler(WSGIHandler())`` inside the server thread).
    """
    from django.contrib.staticfiles.handlers import StaticFilesHandler
    from django.db import connections
    from django.test.testcases import LiveServerThread
    from django.test.utils import modify_settings

    addr = os.getenv("DJANGO_LIVE_TEST_SERVER_ADDRESS", "localhost")
    try:
        host, port = addr.split(":")
        port = int(port)
    except ValueError:
        host, port = addr, 0

    # Share in-memory sqlite connections with the server thread.
    connections_override = {}
    for conn in connections.all():
        if conn.vendor == "sqlite" and conn.is_in_memory_db():
            connections_override[conn.alias] = conn

    with modify_settings(ALLOWED_HOSTS={"append": host}):
        for conn in connections_override.values():
            conn.inc_thread_sharing()
        thread = LiveServerThread(
            host,
            StaticFilesHandler,
            connections_override=connections_override,
            port=port,
        )
        thread.daemon = True
        thread.start()
        thread.is_ready.wait()
        if thread.error:
            error = thread.error
            thread.terminate()
            for conn in connections_override.values():
                conn.dec_thread_sharing()
            raise error

        class _StaticLiveServer:
            @property
            def url(self):
                return "http://%s:%s" % (host, thread.port)

        yield _StaticLiveServer()

        thread.terminate()
        for conn in connections_override.values():
            conn.dec_thread_sharing()


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
