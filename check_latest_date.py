"""Check latest calc_date in risk tables."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
django.setup()

from risk.models import UrgencyScore, PortfolioRiskMetrics, RegimeState
from django.db.models import Max

# Latest urgency calc_date
latest_urgency = UrgencyScore.objects.aggregate(Max('calc_date'))['calc_date__max']
print(f"Latest UrgencyScore calc_date: {latest_urgency}")

# Latest portfolio metrics calc_date
latest_metrics = PortfolioRiskMetrics.objects.aggregate(Max('calc_date'))['calc_date__max']
print(f"Latest PortfolioRiskMetrics calc_date: {latest_metrics}")

# Latest regime calc_date
latest_regime = RegimeState.objects.aggregate(Max('calc_date'))['calc_date__max']
print(f"Latest RegimeState calc_date: {latest_regime}")
