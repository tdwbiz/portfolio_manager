"""Quick gate check for M6-Spec-1 dashboard data."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()

from risk.models import UrgencyScore, PortfolioRiskMetrics, RegimeState
from django.db import connection

cursor = connection.cursor()

# Gate 1: Portfolio urgency score
cursor.execute("SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' AND entity_type='portfolio'")
print(f"Gate 1 - UrgencyScore portfolio: {cursor.fetchone()[0]} rows (expect 1)")

# Gate 2: Portfolio risk metrics
cursor.execute("SELECT COUNT(*) FROM rm_portfolio_risk_metrics WHERE calc_date='2026-06-02' AND entity_type='portfolio'")
print(f"Gate 2 - PortfolioRiskMetrics portfolio: {cursor.fetchone()[0]} rows (expect 1)")

# Gate 3: Account urgency scores
cursor.execute("SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' AND entity_type='account'")
print(f"Gate 3 - UrgencyScore account: {cursor.fetchone()[0]} rows (expect 11)")

# Gate 4: Regime state
cursor.execute("SELECT COUNT(*) FROM rm_regime_state WHERE calc_date='2026-06-02'")
print(f"Gate 4 - RegimeState: {cursor.fetchone()[0]} rows (expect 1)")

# Gate 5: Owner urgency scores
cursor.execute("SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' AND entity_type='owner'")
print(f"Gate 5 - UrgencyScore owner: {cursor.fetchone()[0]} rows (expect 2)")

# Gate 6: Security-in-account scores
cursor.execute("SELECT COUNT(*) FROM rm_urgency_score WHERE calc_date='2026-06-02' AND entity_type='security_in_account'")
print(f"Gate 6 - UrgencyScore security_in_account: {cursor.fetchone()[0]} rows")

# Gate 7: Market conditions
cursor.execute("SELECT COUNT(*) FROM rm_market_condition WHERE calc_date='2026-06-02'")
print(f"Gate 7 - MarketCondition: {cursor.fetchone()[0]} rows")

# Gate 8: Security risk metrics
cursor.execute("SELECT COUNT(*) FROM rm_security_risk_metrics WHERE calc_date='2026-06-02'")
print(f"Gate 8 - SecurityRiskMetrics: {cursor.fetchone()[0]} rows")
