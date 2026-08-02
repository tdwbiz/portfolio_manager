"""Verify Holdings import: compare processed CSVs against PositionHistory DB.
Generates 3 CSV reports:
  CSV_Holdings_detail.csv       — all positions from CSVs + DB, with full fields
  CSV_Holdings_Imported.csv     — newly imported positions (from CSVs into DB)
  CSV_Holdings_detail_Rejected.csv — positions skipped/rejected during import

Usage:
    python scripts/verify_holdings_import.py
"""
import os
import sys
import csv
import re
import io
import logging
from decimal import Decimal
from datetime import datetime
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings_test')
import django
django.setup()

from core.models import PositionHistory, Account

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

processed_dir = 'processed'
output_dir = 'docs/reconciliation'
output_dir = Path(output_dir)
output_dir.mkdir(parents=True, exist_ok=True)

# --- Parse all Holdings CSVs from processed/ ---
csv_positions = []         # positions extracted from CSV files
csv_file_counts = Counter()  # file -> position count
csv_skipped_rows = Counter()  # file -> count of skipped rows
csv_errors = []            # files that errored during parsing

for fname in sorted(os.listdir(processed_dir)):
    if not fname.lower().startswith('holdings'):
        continue
    fpath = os.path.join(processed_dir, fname)
    file_positions = []
    file_skipped = 0
    try:
        # Try multiple encodings
        content = None
        for enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
            try:
                with open(fpath, 'r', encoding=enc) as f:
                    content = f.read()
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if content is None:
            logger.error(f"  Cannot decode {fname} with any encoding")
            csv_errors.append(fname)
            continue

        reader = csv.reader(io.StringIO(content))
        account_id = None
        snapshot_date = None
        found_header = False
        row_num = 0

        for row in reader:
            row_num += 1
            if not row or all(c.strip() == '' for c in row):
                continue

            # Extract account_id from first rows
            if not account_id and len(row) >= 3:
                m = re.search(r'Account:\s*(\d+)', ' '.join(row))
                if m:
                    account_id = m.group(1)

            # Extract snapshot date from header
            if not snapshot_date and len(row) >= 3:
                joined = ' '.join(row)
                m = re.search(r'([A-Za-z]+ \d{1,2},? \d{4})', joined)
                if m:
                    try:
                        snapshot_date = datetime.strptime(
                            m.group(1).replace(',', '').strip(),
                            '%b %d %Y'
                        ).date()
                    except Exception:
                        pass

            # Detect data header row
            if not found_header:
                first = row[0].strip('"')
                if first == 'Product' or (len(row) > 2 and row[1].strip('"') == 'Product'):
                    found_header = True
                    continue

            # Parse data rows
            cleaned = [cell.strip().strip('"') for cell in row]
            if len(cleaned) < 14:
                file_skipped += 1
                continue

            symbol = cleaned[2]
            if not symbol:
                file_skipped += 1
                continue

            try:
                qty = Decimal(cleaned[4])
            except Exception:
                file_skipped += 1
                continue

            if qty == 0:
                file_skipped += 1
                continue

            currency = cleaned[6] if len(cleaned) > 6 else 'CAD'

            def _to_decimal(val: str) -> Decimal:
                v = val.replace(',', '').strip()
                if v in ('', '-', '--', 'N/A'):
                    return Decimal('0')
                return Decimal(v)

            market_price = _to_decimal(cleaned[5])
            total_book_cost = _to_decimal(cleaned[10])
            total_market_value = _to_decimal(cleaned[11])
            average_cost = _to_decimal(cleaned[13])

            if qty > 0:
                cost_basis_per_unit = (total_book_cost / qty).quantize(
                    Decimal('0.0001')
                )
            else:
                cost_basis_per_unit = average_cost

            file_positions.append({
                'account_id': account_id or '',
                'symbol': symbol,
                'quantity': qty,
                'cost_basis_per_unit': cost_basis_per_unit,
                'total_cost_basis': total_book_cost,
                'market_price': market_price,
                'market_value': total_market_value,
                'currency': currency,
                'snapshot_date': snapshot_date,
                'source_file': fname,
                'row_num': row_num,
            })

        csv_file_counts[fname] = len(file_positions)
        csv_skipped_rows[fname] = file_skipped
        csv_positions.extend(file_positions)
    except Exception as e:
        logger.error(f"Error parsing {fname}: {e}")
        csv_errors.append(fname)

# --- Parse DB PositionHistory ---
db_positions = list(PositionHistory.objects.select_related('account').all())

# Build DB lookup by (account_id, symbol, date_from, currency)
db_lookup = {}
for ph in db_positions:
    key = (ph.account.account_id, ph.symbol, ph.date_from, ph.currency)
    db_lookup[key] = ph

# --- Classify positions ---
newly_imported = []    # positions from CSV that were imported into DB
csv_only = []          # positions from CSV not in DB
db_only = []           # positions in DB not from CSV
matching = []          # positions matching both CSV and DB
csv_rejected = []      # positions that should have been imported but weren't (errors)

csv_keys = set()
for p in csv_positions:
    if p['snapshot_date']:
        csv_keys.add((p['account_id'], p['symbol'], p['snapshot_date'], p['currency']))

for p in csv_positions:
    key = (p['account_id'], p['symbol'], p['snapshot_date'], p['currency'])
    if key in db_lookup:
        ph = db_lookup[key]
        # Compare values
        match = True
        reasons = []
        if ph.quantity != p['quantity']:
            match = False
            reasons.append(f"qty CSV={p['quantity']} DB={ph.quantity}")
        if ph.market_price != p['market_price']:
            match = False
            reasons.append(f"price CSV={p['market_price']} DB={ph.market_price}")
        if ph.total_cost_basis != p['total_cost_basis']:
            match = False
            reasons.append(f"cost CSV={p['total_cost_basis']} DB={ph.total_cost_basis}")
        if ph.total_market_value != p['market_value']:
            match = False
            reasons.append(f"mv CSV={p['market_value']} DB={ph.total_market_value}")
        if match:
            matching.append(p)
        else:
            csv_rejected.append({
                **p,
                'db_value': {
                    'quantity': ph.quantity,
                    'market_price': ph.market_price,
                    'total_cost_basis': ph.total_cost_basis,
                    'total_market_value': ph.total_market_value,
                },
                'reasons': '; '.join(reasons),
            })
    else:
        csv_only.append(p)

for ph in db_positions:
    key = (ph.account.account_id, ph.symbol, ph.date_from, ph.currency)
    if key not in csv_keys:
        db_only.append(ph)

# --- Summary ---
total_csv = len(csv_positions)
total_db = len(db_positions)

print("=" * 70)
print("HOLDINGS IMPORT VERIFICATION REPORT")
print("=" * 70)
print(f"\nProcessed CSV files:  {len(csv_file_counts)}")
print(f"Total CSV positions:  {total_csv}")
print(f"Total DB records:     {total_db}")
print(f"CSV skipped rows:     {sum(csv_skipped_rows.values())}")
print(f"CSV parse errors:     {len(csv_errors)}")
if csv_errors:
    print(f"  Error files: {', '.join(csv_errors)}")

print(f"\n--- Match Summary ---")
print(f"  Matching (CSV = DB):   {len(matching)}")
print(f"  CSV only (not in DB):  {len(csv_only)}")
print(f"  DB only (not in CSV):  {len(db_only)}")
print(f"  CSV/DB mismatch:       {len(csv_rejected)}")
print(f"  Newly imported:        {len(matching)}")

print(f"\n--- CSV Files ---")
for fname in sorted(csv_file_counts.keys()):
    count = csv_file_counts[fname]
    skipped = csv_skipped_rows.get(fname, 0)
    print(f"  {fname}: {count} positions ({skipped} skipped)")

if csv_rejected:
    print(f"\n--- CSV/DB Mismatches (first 20) ---")
    for r in csv_rejected[:20]:
        print(f"  {r['account_id']} {r['symbol']} {r['snapshot_date']} {r['currency']}")
        print(f"    CSV: qty={r['quantity']} price={r['market_price']} cost={r['total_cost_basis']} mv={r['market_value']}")
        print(f"    DB:  qty={r['db_value']['quantity']} price={r['db_value']['market_price']} cost={r['db_value']['total_cost_basis']} mv={r['db_value']['total_market_value']}")
        print(f"    Reasons: {r['reasons']}")

# --- Per-date breakdown ---
csv_dates = Counter(p['snapshot_date'] for p in csv_positions if p['snapshot_date'])
db_dates = Counter(ph.date_from for ph in db_positions)
print(f"\n--- Positions per Snapshot Date ---")
for d in sorted(set(list(csv_dates.keys()) + list(db_dates.keys()))):
    csv_count = csv_dates.get(d, 0)
    db_count = db_dates.get(d, 0)
    match = 'OK' if csv_count == db_count else f"MISMATCH"
    print(f"  {d}: CSV={csv_count} DB={db_count} [{match}]")

# --- Write CSV reports ---
print(f"\n--- Writing CSV Reports ---")

# 1. CSV_Holdings_detail.csv — all CSV positions with full details
detail_path = output_dir / 'CSV_Holdings_detail.csv'
with open(detail_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow([
        'Source_File', 'account_id', 'symbol', 'currency', 'snapshot_date',
        'quantity', 'cost_basis_per_unit', 'total_cost_basis',
        'market_price', 'market_value',
    ])
    for p in csv_positions:
        writer.writerow([
            p['source_file'],
            p['account_id'],
            p['symbol'],
            p['currency'],
            p['snapshot_date'].strftime('%Y-%m-%d') if p['snapshot_date'] else '',
            p['quantity'],
            p['cost_basis_per_unit'],
            p['total_cost_basis'],
            p['market_price'],
            p['market_value'],
        ])
print(f"  Written: {detail_path} ({len(csv_positions)} rows)")

# 2. CSV_Holdings_Imported.csv — matching positions (CSV imported into DB)
imported_path = output_dir / 'CSV_Holdings_Imported.csv'
with open(imported_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow([
        'Source_File', 'account_id', 'symbol', 'currency', 'snapshot_date',
        'quantity', 'cost_basis_per_unit', 'total_cost_basis',
        'market_price', 'market_value',
    ])
    for p in matching:
        writer.writerow([
            p['source_file'],
            p['account_id'],
            p['symbol'],
            p['currency'],
            p['snapshot_date'].strftime('%Y-%m-%d') if p['snapshot_date'] else '',
            p['quantity'],
            p['cost_basis_per_unit'],
            p['total_cost_basis'],
            p['market_price'],
            p['market_value'],
        ])
print(f"  Written: {imported_path} ({len(matching)} rows)")

# 3. CSV_Holdings_detail_Rejected.csv — mismatched positions
rejected_path = output_dir / 'CSV_Holdings_detail_Rejected.csv'
with open(rejected_path, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    writer.writerow([
        'Source_File', 'account_id', 'symbol', 'currency', 'snapshot_date',
        'quantity_csv', 'cost_basis_csv', 'market_price_csv', 'market_value_csv',
        'quantity_db', 'cost_basis_db', 'market_price_db', 'market_value_db',
        'reasons',
    ])
    for r in csv_rejected:
        writer.writerow([
            r['source_file'],
            r['account_id'],
            r['symbol'],
            r['currency'],
            r['snapshot_date'].strftime('%Y-%m-%d') if r['snapshot_date'] else '',
            r['quantity'],
            r['cost_basis_per_unit'],
            r['market_price'],
            r['market_value'],
            r['db_value']['quantity'],
            r['db_value']['total_cost_basis'],
            r['db_value']['market_price'],
            r['db_value']['total_market_value'],
            r['reasons'],
        ])
print(f"  Written: {rejected_path} ({len(csv_rejected)} rows)")

print("\n" + "=" * 70)
print("VERIFICATION COMPLETE")
print("=" * 70)
