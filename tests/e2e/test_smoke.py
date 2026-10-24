"""
Comprehensive smoke tests for the portfolio_manager application.
Quick health checks to verify the application is running and responsive.
"""

import time


class TestSmoke:
    """Basic smoke tests for application health."""

    def test_home_page_loads(self, page):
        """Test that the home page loads."""
        page.goto("/")
        assert page.title() != ""

    def test_health_check(self, page):
        """Test health check endpoint if available."""
        resp = page.goto("/health/")
        assert resp.status == 200

    def test_static_assets_load(self, page):
        """Test that static assets are accessible."""
        page.goto("/")
        # Check if CSS and JS load
        css_links = page.query_selector_all('link[rel="stylesheet"]')
        js_scripts = page.query_selector_all("script[src]")
        # At least some assets should be present
        assert len(css_links) + len(js_scripts) > 0

    def test_response_time(self, page):
        """Test that the homepage loads within acceptable time."""
        start_time = time.time()
        page.goto("/")
        load_time = (time.time() - start_time) * 1000  # Convert to milliseconds
        assert load_time < 5000, f"Page took too long to load: {load_time:.2f}ms"
        print(f"Homepage loaded in {load_time:.2f}ms")

    def test_favicon(self, page):
        """Test that favicon is accessible."""
        resp = page.goto("/favicon.ico")
        # Favicon should either load (200) or not exist (404), but not error
        assert resp.status in [200, 404]

    def test_no_console_errors(self, page):
        """Test that there are no JavaScript console errors on homepage."""
        errors = []
        page.on(
            "console", lambda msg: errors.append(msg) if msg.type == "error" else None
        )
        page.goto("/")
        assert len(errors) == 0, f"Console errors detected: {[e.text for e in errors]}"
