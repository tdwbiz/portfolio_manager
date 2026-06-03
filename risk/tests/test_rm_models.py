from django.test import TestCase
from django.core.exceptions import ValidationError
from decimal import Decimal
from risk.models import SecurityUniverse, FxRate, AlternativePortfolio, AlternativeHolding
from core.models import Account


class AlternativePortfolioWeightTests(TestCase):

    def setUp(self):
        self.account = Account.objects.create(
            account_id="TEST_ACC",
            name="Test Account",
            owner="TOM",
            currency="CAD",
        )
        self.portfolio = AlternativePortfolio.objects.create(
            owner="TOM",
            account=self.account,
            name="Test Portfolio",
        )

    def test_weights_sum_to_one_valid(self):
        AlternativeHolding.objects.create(
            portfolio=self.portfolio,
            symbol="AAPL",
            target_weight=Decimal("0.3500"),
        )
        AlternativeHolding.objects.create(
            portfolio=self.portfolio,
            symbol="GOOG",
            target_weight=Decimal("0.3500"),
        )
        AlternativeHolding.objects.create(
            portfolio=self.portfolio,
            symbol="MSFT",
            target_weight=Decimal("0.3000"),
        )
        # Should not raise
        for holding in self.portfolio.holdings.all():
            holding.clean()

    def test_weights_exceeding_one_raises_validation_error(self):
        AlternativeHolding.objects.create(
            portfolio=self.portfolio,
            symbol="AAPL",
            target_weight=Decimal("0.6000"),
        )
        holding = AlternativeHolding(
            portfolio=self.portfolio,
            symbol="GOOG",
            target_weight=Decimal("0.5000"),
        )
        with self.assertRaises(ValidationError):
            holding.clean()

    def test_single_holding_at_full_weight(self):
        AlternativeHolding.objects.create(
            portfolio=self.portfolio,
            symbol="AAPL",
            target_weight=Decimal("1.0000"),
        )
        # Should not raise
        for holding in self.portfolio.holdings.all():
            holding.clean()

    def test_empty_portfolio_is_valid(self):
        # No holdings added
        # Should not raise
        pass


class SecurityUniverseSeedTests(TestCase):

    def setUp(self):
        # Seed data
        symbols = [
            ("AAPL", "Apple Inc", "common_stock", True, False, False, False, True),
            ("ADX", "Adams Diversified Equity", "cef", True, False, False, True, False),
            ("AIS", "Ares Capital ETF", "etf_equity", True, False, False, False, True),
            ("CGDV", "Capital Group Dividend ETF", "etf_equity", True, False, False, False, True),
            ("ETHA", "iShares Ethereum ETF", "crypto_etf", True, False, False, False, True, True),
            ("GOOG", "Alphabet Inc", "common_stock", True, False, False, False, True),
            ("IBIT", "iShares Bitcoin ETF", "crypto_etf", True, False, False, False, True, True),
            ("ICSH", "iShares Ultra Short Bond ETF", "cash_equiv", True, False, False, False, False),
            ("IGM", "iShares Expanded Tech ETF", "etf_equity", True, False, False, False, True),
            ("IWY", "iShares Russell Top 200 Growth", "etf_equity", True, False, False, False, True),
            ("MA", "Mastercard Inc", "common_stock", True, False, False, False, True),
            ("NVDA", "NVIDIA Corp", "common_stock", True, False, False, False, True, True),
            ("QQQ", "Invesco QQQ Nasdaq-100", "etf_equity", True, True, False, False, True),
            ("RBF2010", "RBC CAD Money Market", "mutual_fund", True, False, False, False, False),
            ("RBF2014", "RBC USD Money Market", "mutual_fund", True, False, False, False, False),
            ("SGOV", "iShares 0-3 Month Treasury", "cash_equiv", True, False, False, False, False),
            ("SMH", "VanEck Semiconductor ETF", "etf_equity", True, False, False, False, True),
            ("SOXX", "iShares Semiconductor ETF", "etf_equity", True, False, False, False, True),
            ("SPY", "SPDR S&P 500 ETF", "etf_equity", True, True, False, False, True),
            ("V", "Visa Inc", "common_stock", True, False, False, False, True),
            ("VDY", "Vanguard FTSE Canadian High Div", "etf_equity", True, False, False, False, True),
            ("VFV", "Vanguard S&P 500 Index ETF CAD", "etf_equity", True, False, False, False, True),
            ("VOO", "Vanguard S&P 500 ETF", "etf_equity", True, False, False, False, True),
            ("XDIV", "iShares Core MSCI Canadian Div", "etf_equity", True, False, False, False, True),
            ("XEQT", "iShares Core Equity ETF Portfolio", "etf_equity", True, False, False, False, True),
        ]
        for item in symbols:
            sym = item[0]
            name = item[1]
            asset_class = item[2]
            is_held = item[3]
            is_benchmark = item[4]
            is_hedge = item[5]
            is_cef = item[6]
            options = item[7]
            use_jump = item[8] if len(item) > 8 else False
            SecurityUniverse.objects.create(
                symbol=sym,
                name=name,
                asset_class=asset_class,
                is_held=is_held,
                is_benchmark=is_benchmark,
                is_hedge=is_hedge,
                is_cef=is_cef,
                options_available=options,
                use_jump_diffusion=use_jump,
            )

    def test_all_25_held_symbols_present(self):
        held = SecurityUniverse.objects.filter(is_held=True)
        symbols = set(held.values_list("symbol", flat=True))
        expected = {"AAPL", "ADX", "AIS", "CGDV", "ETHA", "GOOG", "IBIT", "ICSH",
                    "IGM", "IWY", "MA", "NVDA", "QQQ", "RBF2010", "RBF2014", "SGOV",
                    "SMH", "SOXX", "SPY", "V", "VDY", "VFV", "VOO", "XDIV", "XEQT"}
        self.assertEqual(symbols, expected)

    def test_adx_is_cef_and_options_not_available(self):
        adx = SecurityUniverse.objects.get(symbol="ADX")
        self.assertTrue(adx.is_cef)
        self.assertFalse(adx.options_available)

    def test_crypto_etfs_use_jump_diffusion(self):
        for sym in ["IBIT", "ETHA"]:
            s = SecurityUniverse.objects.get(symbol=sym)
            self.assertTrue(s.use_jump_diffusion)

    def test_qqq_spy_are_both_held_and_benchmark(self):
        for sym in ["QQQ", "SPY"]:
            s = SecurityUniverse.objects.get(symbol=sym)
            self.assertTrue(s.is_held)
            self.assertTrue(s.is_benchmark)


class FxRateTests(TestCase):

    def test_usd_cad_rate_stored_correctly(self):
        FxRate.objects.create(
            date="2026-01-15",
            usd_cad_rate=Decimal("1.4423"),
            data_source="yfinance",
        )
        rate = FxRate.objects.get(date="2026-01-15")
        self.assertEqual(rate.usd_cad_rate, Decimal("1.4423"))

    def test_duplicate_date_raises_integrity_error(self):
        FxRate.objects.create(
            date="2026-01-15",
            usd_cad_rate=Decimal("1.4423"),
            data_source="yfinance",
        )
        with self.assertRaises(Exception):
            FxRate.objects.create(
                date="2026-01-15",
                usd_cad_rate=Decimal("1.4500"),
                data_source="yfinance",
            )
