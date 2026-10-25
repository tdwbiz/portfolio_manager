from django.core.management.base import BaseCommand
from decimal import Decimal
from risk.models import SecurityUniverse, SecurityIndexExposure, ScoringConfig
from core.models import Account


class Command(BaseCommand):
    help = "Seed the rm_security_universe, rm_security_index_exposure, and rm_scoring_config tables"

    def handle(self, *args, **options):
        self.stdout.write("Seeding rm_security_universe...")

        security_universe_data = [
            ("AAPL", "Apple Inc", "common_stock", True, False, False, False, True, "High UGL in IB_SUSAN"),
            ("ADX", "Adams Diversified Equity", "cef", True, False, False, True, False, "Closed-end fund; limited liquidity"),
            ("AIS", "Ares Capital ETF", "etf_equity", True, False, False, False, True, ""),
            ("CGDV", "Capital Group Dividend ETF", "etf_equity", True, False, False, False, True, ""),
            ("ETHA", "iShares Ethereum ETF", "crypto_etf", True, False, False, False, True, "use_jump_diffusion=True"),
            ("GOOG", "Alphabet Inc", "common_stock", True, False, False, False, True, ""),
            ("IBIT", "iShares Bitcoin ETF", "crypto_etf", True, False, False, False, True, "use_jump_diffusion=True"),
            ("ICSH", "iShares Ultra Short Bond ETF", "cash_equiv", True, False, False, False, False, "Near-cash; low risk"),
            ("IGM", "iShares Expanded Tech ETF", "etf_equity", True, False, False, False, True, ""),
            ("IWY", "iShares Russell Top 200 Growth", "etf_equity", True, False, False, False, True, ""),
            ("MA", "Mastercard Inc", "common_stock", True, False, False, False, True, ""),
            ("NVDA", "NVIDIA Corp", "common_stock", True, False, False, False, True, "use_jump_diffusion=True"),
            ("QQQ", "Invesco QQQ Nasdaq-100", "etf_equity", True, True, False, False, True, "Held AND benchmark"),
            ("RBF2010", "RBC CAD Money Market", "mutual_fund", True, False, False, False, False, "Cash equiv; primary_currency=CAD"),
            ("RBF2014", "RBC USD Money Market", "mutual_fund", True, False, False, False, False, "Cash equiv"),
            ("SGOV", "iShares 0-3 Month Treasury", "cash_equiv", True, False, False, False, False, "Near-cash risk-free proxy"),
            ("SMH", "VanEck Semiconductor ETF", "etf_equity", True, False, False, False, True, ""),
            ("SOXX", "iShares Semiconductor ETF", "etf_equity", True, False, False, False, True, ""),
            ("SPY", "SPDR S&P 500 ETF", "etf_equity", True, True, False, False, True, "Held AND benchmark"),
            ("V", "Visa Inc", "common_stock", True, False, False, False, True, ""),
            ("VDY", "Vanguard FTSE Canadian High Div", "etf_equity", True, False, False, False, True, "primary_currency=CAD; largest position"),
            ("VFV", "Vanguard S&P 500 Index ETF CAD", "etf_equity", True, False, False, False, True, "primary_currency=CAD; S&P 500 CAD-hedged"),
            ("VOO", "Vanguard S&P 500 ETF", "etf_equity", True, False, False, False, True, ""),
            ("XDIV", "iShares Core MSCI Canadian Div", "etf_equity", True, False, False, False, True, "primary_currency=CAD"),
            ("XEQT", "iShares Core Equity ETF Portfolio", "etf_equity", True, False, False, False, True, "primary_currency=CAD"),
            ("XIU.TO", "iShares S&P/TSX 60 ETF", "etf_equity", False, True, False, False, True, "Benchmark only; amibroker_symbol=XIU"),
            ("SOXS", "Direxion Semiconductor Bear 3x", "etf_equity", False, False, True, False, True, "Hedge for SOXX/SMH"),
            ("SOXL", "Direxion Semiconductor Bull 3x", "etf_equity", False, False, True, False, True, ""),
            ("PSQ", "ProShares Short QQQ", "etf_equity", False, False, True, False, True, "Hedge for QQQ"),
            ("SPXS", "Direxion S&P 500 Bear 3x", "etf_equity", False, False, True, False, True, "Hedge for SPY/VOO/VFV"),
        ]

        created = 0
        for sym, name, asset_class, is_held, is_benchmark, is_hedge, is_cef, options, notes in security_universe_data:
            defaults = {
                "name": name,
                "security_type": "etf" if "etf" in asset_class or asset_class == "cef" else asset_class,
                "asset_class": asset_class,
                "is_held": is_held,
                "is_benchmark": is_benchmark,
                "is_hedge": is_hedge,
                "is_cef": is_cef,
                "options_available": options,
                "notes": notes,
            }
            if sym in ("ETHA", "IBIT", "NVDA"):
                defaults["use_jump_diffusion"] = True
            if sym == "XIU.TO":
                defaults["amibroker_symbol"] = "XIU"
            if sym in ("VDY", "VFV", "XDIV", "XEQT", "RBF2010"):
                defaults["primary_currency"] = "CAD"
            if sym in ("RBF2014",):
                defaults["primary_currency"] = "USD"

            _, created_count = SecurityUniverse.objects.update_or_create(
                symbol=sym,
                defaults=defaults,
            )
            created += created_count

        self.stdout.write(self.style.SUCCESS(f"Seeded {created} security universe entries."))

        self.stdout.write("Seeding rm_security_index_exposure...")

        index_exposure_data = [
            ("VFV", "SP500", Decimal("1.0000"), ""),
            ("VOO", "SP500", Decimal("1.0000"), ""),
            ("SPY", "SP500", Decimal("1.0000"), ""),
            ("QQQ", "NASDAQ100", Decimal("1.0000"), ""),
            ("IGM", "NASDAQ100", Decimal("0.8000"), ""),
            ("IGM", "TECHNOLOGY", Decimal("0.2000"), ""),
            ("IWY", "SP500", Decimal("0.8500"), ""),
            ("IWY", "TECHNOLOGY", Decimal("0.1500"), ""),
            ("SOXX", "SEMICONDUCTOR", Decimal("1.0000"), ""),
            ("SMH", "SEMICONDUCTOR", Decimal("1.0000"), ""),
            ("VDY", "DIVIDEND_CA", Decimal("1.0000"), ""),
            ("VDY", "TSX", Decimal("0.9000"), ""),
            ("XDIV", "DIVIDEND_CA", Decimal("1.0000"), ""),
            ("XDIV", "TSX", Decimal("1.0000"), ""),
            ("XEQT", "SP500", Decimal("0.4500"), ""),
            ("XEQT", "TSX", Decimal("0.2500"), ""),
            ("XEQT", "BROAD_MARKET", Decimal("0.3000"), ""),
            ("CGDV", "DIVIDEND_US", Decimal("1.0000"), ""),
            ("CGDV", "SP500", Decimal("0.7000"), ""),
            ("AIS", "DIVIDEND_US", Decimal("0.8000"), ""),
            ("IBIT", "OTHER", Decimal("1.0000"), ""),
            ("ETHA", "OTHER", Decimal("1.0000"), ""),
            ("ADX", "SP500", Decimal("0.8500"), ""),
            ("AAPL", "TECHNOLOGY", Decimal("1.0000"), ""),
            ("NVDA", "SEMICONDUCTOR", Decimal("0.7000"), ""),
            ("NVDA", "TECHNOLOGY", Decimal("0.3000"), ""),
            ("MA", "OTHER", Decimal("1.0000"), ""),
            ("GOOG", "TECHNOLOGY", Decimal("0.8000"), ""),
            ("GOOG", "NASDAQ100", Decimal("0.2000"), ""),
            ("V", "OTHER", Decimal("1.0000"), ""),
        ]

        created_exposure = 0
        for symbol, index_name, weight, notes in index_exposure_data:
            _, created_count = SecurityIndexExposure.objects.update_or_create(
                symbol=symbol,
                index_name=index_name,
                defaults={"exposure_weight": weight, "notes": notes},
            )
            created_exposure += created_count

        self.stdout.write(self.style.SUCCESS(f"Seeded {created_exposure} security index exposure entries."))

        self.stdout.write("Seeding rm_scoring_config...")

        scoring_config_data = [
            ("weight_m1_metrics", "0.35", "Weight for M1 metrics in scoring"),
            ("weight_security_driver", "0.20", "Weight for security driver in scoring"),
            ("weight_regime", "0.25", "Weight for regime in scoring"),
            ("weight_market_condition", "0.20", "Weight for market condition in scoring"),
            ("threshold_low", "25", "Low threshold for scoring"),
            ("threshold_medium", "50", "Medium threshold for scoring"),
            ("threshold_high", "75", "High threshold for scoring"),
            ("var_portfolio_alert_pct", "5.0", "VaR portfolio alert percentage"),
            ("concentration_alert_pct", "20.0", "Concentration alert percentage"),
        ]

        created_config = 0
        for key, value, description in scoring_config_data:
            _, created_count = ScoringConfig.objects.update_or_create(
                config_key=key,
                defaults={"config_value": value, "description": description},
            )
            created_config += created_count

        self.stdout.write(self.style.SUCCESS(f"Seeded {created_config} scoring config entries."))

        self.stdout.write("Setting TAXABLE_CAR exclude_from_risk=True...")
        updated = Account.objects.filter(name="TS_CAR").update(exclude_from_risk=True)
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} TAXABLE_CAR account(s)."))

        self.stdout.write(self.style.SUCCESS("Seed complete."))
