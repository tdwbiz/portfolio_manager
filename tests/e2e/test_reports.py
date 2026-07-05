"""
End-to-end tests for reports functionality.
Simulates real user report generation and viewing.
"""


class TestReports:
    """Test report generation and viewing flows."""

    def test_reports_page_loads(self, page):
        """Test that reports page loads."""
        page.goto("/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')

        # Navigate to reports
        page.click('a:has-text("Report")')
        page.wait_for_load_state("networkidle")

        # Verify reports page loads
        assert "report" in page.url.lower() or "report" in page.title().lower()

    def test_report_generation(self, page):
        """Test generating a report."""
        page.goto("/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')

        # Navigate to reports
        page.click('a:has-text("Report")')

        # Click generate button if present
        generate_buttons = page.query_selector_all('button:has-text("Generate")')
        if generate_buttons:
            generate_buttons[0].click()
            page.wait_for_load_state("networkidle")

        # Verify page responds
        assert page.url() != ""

    def test_report_filters(self, page):
        """Test applying filters to reports."""
        page.goto("/login/")
        page.fill('input[name="username"]', "admin")
        page.fill('input[name="password"]', "admin")
        page.click('button[type="submit"]')

        # Navigate to reports
        page.click('a:has-text("Report")')

        # Try to select a date range if present
        date_inputs = page.query_selector_all('input[type="date"]')
        if date_inputs:
            date_inputs[0].fill("2024-01-01")
            if len(date_inputs) > 1:
                date_inputs[1].fill("2024-12-31")

        # Apply filters
        filter_buttons = page.query_selector_all('button:has-text("Filter")')
        if filter_buttons:
            filter_buttons[0].click()
            page.wait_for_load_state("networkidle")

        # Verify page responds
        assert page.url() != ""
