import django, os, sys, math
os.environ["DJANGO_SETTINGS_MODULE"] = "portfolio_manager.settings"
sys.path.insert(0, "C:/AI-DevTeam/projects/portfolio_manager")
django.setup()
import logging; logging.disable(logging.CRITICAL)

import pandas as pd
from decimal import Decimal
from datetime import datetime, date
from core.models import Transaction as TxModel
from core.utils import compute_portfolio_value_series, _load_prices_from_db

mv_series = compute_portfolio_value_series(freq='weekly', date_from='2026-01-01', date_to='2026-05-21')
labels = [item['date'] for item in mv_series]

first_tx = TxModel.objects.order_by('date').values_list('date', flat=True).first()
bench_df = _load_prices_from_db(['^GSPC'], first_tx, date.today())
bench_ps = bench_df['^GSPC'].dropna() if bench_df is not None else None

eq_txs = list(TxModel.objects.filter(type__in=['BUY','SELL']).exclude(symbol__contains=' ').order_by('date','id'))
ecost = Decimal('0')
ei = 0
eq_cost = []
for lbl in labels:
    ld = datetime.strptime(lbl[:10], '%Y-%m-%d').date()
    while ei < len(eq_txs) and eq_txs[ei].date <= ld:
        tx = eq_txs[ei]
        if tx.amount_cad:
            amt = abs(tx.amount_cad)
            ecost += amt if tx.type=='BUY' else -amt
        ei += 1
    eq_cost.append(float(ecost))

mv0 = float(mv_series[0]['market_value_cad'])
bp = mv0; pprice = None; pcost = 0.0; binit = False; bvals = []
for i, lbl in enumerate(labels):
    cc = eq_cost[i] if i < len(eq_cost) else pcost
    bprice = None
    if bench_ps is not None:
        raw = bench_ps.asof(pd.Timestamp(lbl[:10]))
        if raw is not None and not math.isnan(float(raw)) and float(raw) > 0:
            bprice = float(raw)
    if bprice is None:
        pcost = cc; bvals.append(None); continue
    if not binit:
        binit = True; pprice = bprice; pcost = cc; bvals.append(round(bp)); continue
    if pprice > 0: bp *= (bprice / pprice)
    bp += cc - pcost; pprice = bprice; pcost = cc; bvals.append(round(bp))

sys.stdout.write("Date          mv(portfolio)  bench(^GSPC)  eq_cost\n")
for i in range(len(labels)):
    mv = float(mv_series[i]['market_value_cad'])
    sys.stdout.write(f"{labels[i]}   {mv:12.0f}  {str(bvals[i]):>12}  {eq_cost[i]:12.0f}\n")
