"""
Process-wide Playwright singleton for the e2e suite.

Exactly one ``sync_playwright`` session runs per pytest process: it is started
lazily by :func:`get_playwright` and stopped by an ``atexit`` handler, so a
failing ``setUpClass``, a failing ``tearDownClass``, or a plain interpreter
exit can never leak the driver process.

:class:`PlaywrightLiveServerMixin` gives the ``StaticLiveServerTestCase`` classes a
shared Playwright plus a per-class browser and page; the conftest fixtures use
the same singleton, so fixture-based and class-based tests cannot run two
sessions.
"""
import atexit

from playwright.sync_api import sync_playwright

_pw = None


def get_playwright():
    """Return the process-wide Playwright instance, starting it on first use."""
    global _pw
    if _pw is None:
        _pw = sync_playwright().start()
        atexit.register(shutdown)
    return _pw


def shutdown():
    """Stop the shared Playwright session (idempotent; safe to call twice)."""
    global _pw
    if _pw is not None:
        try:
            _pw.stop()
        except Exception:
            pass
        _pw = None


class PlaywrightLiveServerMixin:
    """Mixin for StaticLiveServerTestCase: shared Playwright, per-class browser + page."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()  # LiveServerTestCase has started the server
        try:
            cls.playwright = get_playwright()  # shared; never stopped here
            cls.browser = cls.playwright.chromium.launch(headless=True)
            cls.page = cls.browser.new_page()
        except Exception:
            super().tearDownClass()  # setUpClass failed -> tearDownClass will not run
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.browser.close()
        finally:
            super().tearDownClass()  # the shared Playwright is stopped by atexit
