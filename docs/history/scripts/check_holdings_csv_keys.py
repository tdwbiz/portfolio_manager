"""Quick check: compare CSV unique keys vs DB PositionHistory keys.
Uses the actual parser from imports.parsers_holdings for accuracy."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
import logging
logging.getLogger('imports.parsers_holdings').setLevel(logging.ERROR)
django.setup()

from core.models import PositionHistory
from imports.parsers_holdings import parse_holdings_csv

processed_dir = 'processed'

csv_keys = set()
csv_positions = 0
csv_files = 0
csv_errors = 0

for fname in sorted(os.listdir(processed_dir)):
    if not fname.lower().startswith('holdings'):
        continue
    csv_files += 1
    fpath = os.path.join(processed_dir, fname)
    try:
        positions = parse_holdings_csv(fpath)
        csv_positions += len(positions)
        for p in positions:
            if p['snapshot_date']:
                csv_keys.add((p['account_id'], p['symbol'], p['snapshot_date'], p['currency']))
    except Exception as e:
        csv_errors += 1
        print(f"  Error parsing {fname}: {e}")

# --- DB ---
db_keys = set()
for ph in PositionHistory.objects.select_related('account').all():
    db_keys.add((ph.account.account_id, ph.symbol, ph.date_from, ph.currency))

missing = csv_keys - db_keys
extra = db_keys - csv_keys

print("=" * 60)
print("CSV vs DB KEY COMPARISON (using actual parser)")
print("=" * 60)
print(f"CSV files:           {csv_files}")
print(f"CSV parseable:       {csv_positions}")
print(f"CSV errors:          {csv_errors}")
print(f"CSV unique keys:     {len(csv_keys)}")
print(f"DB unique keys:      {len(db_keys)}")
print()
print(f"Missing from DB:     {len(missing)}")
print(f"Extra in DB:         {len(extra)}")

if missing:
    print(f"\nMissing from DB ({len(missing)}):")
    for m in sorted(missing):
        print(f"  {m}")
if extra:
    print(f"\nExtra in DB ({len(extra)}):")
    for e in sorted(extra):
        print(f"  {e}")

if not missing and not extra:
    print("\nAll CSV keys match DB keys. No rows missed.")
elif not missing:
    print(f"\nAll {len(csv_keys)} CSV keys are in DB. {len(extra)} extra DB keys (from prior imports or different sources).")
