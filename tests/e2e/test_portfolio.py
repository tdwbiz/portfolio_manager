"""
End-to-end tests for portfolio management functionality.
Simulates real user portfolio interactions.
"""
import pytest


class TestPortfolioManagement:
    """Test portfolio management user flows."""

    def test_portfolio_list_page(self, page):
        """Test that portfolio list page loads."""
        page.goto("/login/")
        page.fill('input[name="username"]', 'admin')
        page.fill('input[name="password"]', 'admin')
        page.click('button[type="submit"]')
        page.wait_for_url("**/portfolio/**")
        
        # Verify portfolio page loads
        assert "portfolio" in page.url.lower() or "portfolio" in page.title().lower()

    def test_portfolio_detail_page(self, page):
        """Test viewing a specific portfolio."""
        page.goto("/login/")
        page.fill('input[name="username"]', 'admin')
        page.fill('input[name="password"]', 'admin')
        page.click('button[type="submit"]')
        
        # Click on first portfolio
        page.click('a:has-text("Portfolio")')
        page.wait_for_load_state("networkidle")
        
        # Verify portfolio details load
        assert page.url() != ""

    def test_portfolio_transactions(self, page):
        """Test viewing transactions in a portfolio."""
        page.goto("/login/")
        page.fill('input[name="username"]', 'admin')
        page.fill('input[name="password"]', 'admin')
        page.click('button[type="submit"]')
        
        # Navigate to transactions
        page.click('a:has-text("Transaction")')
        page.wait_for_load_state("networkidle")
        
        # Verify transactions page loads
        assert "transaction" in page.url.lower() or "transaction" in page.title().lower()
