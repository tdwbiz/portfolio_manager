"""Tests for dashboard dollar summary (D3), top movers widget (D6), and upcoming dividends (D7)."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core.models import Account, BrokerStatement, DividendSchedule, Transaction


class DashboardDollarSummaryTests(TestCase):
    """D3: Dashboard dollar summary — market value, unrealised P&L, YTD income."""

    def setUp(self):
        self.statement = BrokerStatement.objects.create(
            source="IB",
            file_date="2023-01-01",
            raw_filename="test.csv",
        )
        self.account = Account.objects.create(
            account_id="TEST-ACCT",
            name="Test Account",
            broker="IB",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )
        self.client.login(username="testuser", password="testpass123")

        # Create a BUY transaction so positions are non-empty
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date="2024-01-15",
            type="BUY",
            symbol="AAPL",
            quantity=10,
            price=Decimal("150.00"),
            commission=Decimal("0"),
            amount=Decimal("1500.00"),
            currency="USD",
            exchange_rate_cad=Decimal("1.333"),
            amount_cad=Decimal("2000.00"),
            tx_hash="d3_buy_aapl",
        )

    @patch("core.views_portfolio.fetch_last_prices")
    def test_dashboard_shows_dollar_summary(self, mock_fetch):
        """Dashboard page contains market value and P&L summary cards."""
        mock_fetch.return_value = {
            "AAPL": Decimal("180.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Market Value")
        self.assertContains(response, "Unrealised P&L")
        self.assertContains(response, "YTD Income")

    @patch("core.views_portfolio.fetch_last_prices")
    def test_dashboard_pnl_coloring_positive(self, mock_fetch):
        """Positive P&L is styled green (text-success)."""
        mock_fetch.return_value = {
            "AAPL": Decimal("180.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        # Price > cost -> positive P&L -> text-success
        self.assertContains(response, "text-success")

    @patch("core.views_portfolio.fetch_last_prices")
    def test_dashboard_pnl_coloring_negative(self, mock_fetch):
        """Negative P&L is styled red (text-danger)."""
        # Price below cost basis -> negative P&L
        mock_fetch.return_value = {
            "AAPL": Decimal("100.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        # Price < cost -> negative P&L -> text-danger
        self.assertContains(response, "text-danger")

    @patch("core.views_portfolio.fetch_last_prices")
    def test_dashboard_ytd_income(self, mock_fetch):
        """YTD income reflects current year dividend transactions."""
        mock_fetch.return_value = {
            "AAPL": Decimal("180.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        # Add a dividend transaction in the current year
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date=date.today().strftime("%Y-%m-%d"),
            type="DIV",
            symbol="AAPL",
            quantity=1,
            price=Decimal("1.00"),
            commission=Decimal("0"),
            amount=Decimal("1.00"),
            currency="USD",
            exchange_rate_cad=Decimal("1.35"),
            amount_cad=Decimal("1.35"),
            tx_hash="d3_ytd_div",
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        # Should show non-zero YTD income
        self.assertContains(response, "YTD Income")

    @patch("core.views_portfolio.fetch_last_prices")
    def test_dashboard_market_value_computed(self, mock_fetch):
        """Market value is computed from positions * prices."""
        mock_fetch.return_value = {
            "AAPL": Decimal("180.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        # 10 shares * $180 * 1.35 = $2430
        self.assertContains(response, "2430")


class DashboardTopMoversTests(TestCase):
    """D6: Top movers widget — gainers and losers on dashboard."""

    def setUp(self):
        self.statement = BrokerStatement.objects.create(
            source="IB",
            file_date="2023-01-01",
            raw_filename="test.csv",
        )
        self.account = Account.objects.create(
            account_id="TEST-ACCT",
            name="Test Account",
            broker="IB",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )
        self.client.login(username="testuser", password="testpass123")

        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date="2024-01-15",
            type="BUY",
            symbol="AAPL",
            quantity=10,
            price=Decimal("150.00"),
            commission=Decimal("0"),
            amount=Decimal("1500.00"),
            currency="USD",
            exchange_rate_cad=Decimal("1.333"),
            amount_cad=Decimal("2000.00"),
            tx_hash="d6_aapl",
        )

    @patch("core.views_portfolio.fetch_last_prices")
    @patch("core.views_portfolio.get_top_movers")
    def test_dashboard_shows_top_movers(self, mock_movers, mock_fetch):
        """Dashboard page renders top movers section when data is available."""
        mock_fetch.return_value = {
            "AAPL": Decimal("180.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        mock_movers.return_value = {
            "gainers": [{"symbol": "AAPL", "pct_change": Decimal("12.5")}],
            "losers": [{"symbol": "MSFT", "pct_change": Decimal("-3.2")}],
        }
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Top Gainers")
        self.assertContains(response, "Top Losers")

    @patch("core.views_portfolio.fetch_last_prices")
    @patch("core.views_portfolio.get_top_movers")
    def test_top_movers_hidden_when_empty(self, mock_movers, mock_fetch):
        """Top movers widget is hidden when no movers data."""
        mock_fetch.return_value = {}
        mock_movers.return_value = {"gainers": [], "losers": []}
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Top Gainers")

    def test_get_top_movers_returns_empty_with_no_positions(self):
        """get_top_movers returns empty lists when no positions provided."""
        from core.utils import get_top_movers

        result = get_top_movers([])
        self.assertEqual(result, {"gainers": [], "losers": []})


class UpcomingDividendsTests(TestCase):
    """D7: Upcoming dividends tracker widget."""

    def setUp(self):
        self.statement = BrokerStatement.objects.create(
            source="IB",
            file_date="2023-01-01",
            raw_filename="test.csv",
        )
        self.account = Account.objects.create(
            account_id="DIV-TEST",
            name="Dividend Test",
            broker="IB",
            currency="USD",
        )
        self.user = User.objects.create_user(
            username="testuser", password="testpass123"
        )
        self.client.login(username="testuser", password="testpass123")

        # Create holdings of 100 shares AAPL
        Transaction.objects.create(
            statement=self.statement,
            account=self.account,
            date="2024-01-15",
            type="BUY",
            symbol="AAPL",
            quantity=Decimal("100"),
            price=Decimal("150.00"),
            commission=Decimal("0"),
            amount=Decimal("15000.00"),
            currency="USD",
            exchange_rate_cad=Decimal("1.333"),
            amount_cad=Decimal("20000.00"),
            tx_hash="d7_aapl_buy",
        )

    @patch("core.views_portfolio.fetch_last_prices")
    def test_upcoming_dividends_shows_in_dashboard(self, mock_fetch):
        """Dashboard shows upcoming dividends when schedules exist."""
        mock_fetch.return_value = {
            "AAPL": Decimal("180.00"),
            "USDCAD=X": Decimal("1.35"),
        }
        # Create a dividend schedule 10 days from now
        DividendSchedule.objects.create(
            symbol="AAPL",
            pay_date=date.today() + timedelta(days=10),
            amount_per_share=Decimal("0.96"),
            currency="USD",
            exchange_rate_cad=Decimal("1.35"),
            type="DIV",
        )
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upcoming Dividends")
        self.assertContains(response, "AAPL")

    def test_upcoming_dividends_excludes_past_schedules(self):
        """Past dividend schedules are not shown."""
        # Create a past schedule
        DividendSchedule.objects.create(
            symbol="MSFT",
            pay_date=date.today() - timedelta(days=5),
            amount_per_share=Decimal("0.75"),
            type="DIV",
        )
        # Create a future schedule
        future_sched = DividendSchedule.objects.create(
            symbol="AAPL",
            pay_date=date.today() + timedelta(days=10),
            amount_per_share=Decimal("0.96"),
            type="DIV",
        )

        with patch(
            "core.views_portfolio.fetch_last_prices",
            return_value={
                "AAPL": Decimal("180.00"),
                "USDCAD=X": Decimal("1.35"),
            },
        ):
            response = self.client.get(reverse("dashboard"))

        self.assertContains(response, "AAPL")
        # Past MSFT schedule should not appear
        self.assertNotContains(response, "MSFT")
        future_sched.delete()

    def test_dividend_schedule_model_creation(self):
        """DividendSchedule model can be created and queried."""
        sched = DividendSchedule.objects.create(
            symbol="TEST",
            pay_date=date.today() + timedelta(days=5),
            amount_per_share=Decimal("1.00"),
            currency="USD",
            exchange_rate_cad=Decimal("1.35"),
            type="DIV",
        )
        self.assertEqual(sched.symbol, "TEST")
        self.assertEqual(sched.amount_cad_per_share(), Decimal("1.35"))
        s = str(sched)
        self.assertIn("TEST", s)
        self.assertIn("Dividend", s)
        sched.delete()

    def test_get_upcoming_dividends_returns_empty_when_no_schedules(self):
        """get_upcoming_dividends returns empty list when no schedules exist."""
        from core.utils import get_upcoming_dividends

        result = get_upcoming_dividends()
        self.assertEqual(result, [])
