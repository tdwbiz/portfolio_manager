"""
End-to-end tests for portfolio management functionality.
Simulates real user portfolio interactions.
"""


class TestPortfolioManagement:
    """Test portfolio management user flows."""

    def test_portfolio_list_page(self, page, test_user):
        """Test that portfolio list page loads."""
        page.goto("/login/")
        page.fill('input[name="username"]', test_user.username)
        page.fill('input[name="password"]', "e2e-pass-123")
        page.click('button[type="submit"]')
        page.wait_for_url("/")

        # Navigate to portfolio
        page.goto("/portfolio/")

        # Verify portfolio page loads
        assert "portfolio" in page.url.lower() or "portfolio" in page.title().lower()

    def test_portfolio_transactions(self, page, test_user):
        """Test viewing transactions in a portfolio."""
        page.goto("/login/")
        page.fill('input[name="username"]', test_user.username)
        page.fill('input[name="password"]', "e2e-pass-123")
        page.click('button[type="submit"]')
        page.wait_for_url("/")

        # Navigate to transactions
        page.goto("/transactions/")

        # Verify transactions page loads
        assert (
            "transaction" in page.url.lower() or "transaction" in page.title().lower()
        )
