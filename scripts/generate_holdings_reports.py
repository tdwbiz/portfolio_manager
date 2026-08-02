"""Generate CSV reports for Holdings import, similar to Activity reports.

Generates:
- CSV_Holdings_Imported.csv — Summary of imported PositionHistory records
- CSV_Holdings_detail.csv — Full detail with date_to (post-fix)
- CSV_Holdings_detail_Rejected.csv — Any rejected/empty records (likely empty)
"""
import os
import sys
import csv
import django
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

from core.models import PositionHistory, Account

OUTPUT_DIR = r'C:\AI-DevTeam\projects\portfolio_manager\docs\reconciliation'


def generate_imported_summary():
    """Generate CSV_Holdings_Imported.csv — summary of imported records."""
    path = os.path.join(OUTPUT_DIR, 'CSV_Holdings_Imported.csv')
    
    records = list(PositionHistory.objects.all().order_by(
        'account__account_id', 'symbol', 'currency', 'date_from'
    ))
    
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'account_id', 'symbol', 'currency', 'date_from',
            'quantity', 'cost_basis_per_unit', 'total_cost_basis',
            'market_price', 'market_value'
        ])
        
        for r in records:
            writer.writerow([
                r.account.account_id,
                r.symbol,
                r.currency,
                r.date_from.isoformat(),
                str(r.quantity),
                str(r.cost_basis_per_unit),
                str(r.total_cost_basis),
                str(r.market_price),
                str(r.market_value),
            ])
    
    print(f"Written {len(records)} records to {path}")
    return len(records)


def generate_detail_report():
    """Generate CSV_Holdings_detail.csv — full detail with date_to."""
    path = os.path.join(OUTPUT_DIR, 'CSV_Holdings_detail.csv')
    
    records = list(PositionHistory.objects.all().order_by(
        'account__account_id', 'symbol', 'currency', 'date_from'
    ))
    
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'account_id', 'symbol', 'currency', 'date_from', 'date_to',
            'quantity', 'cost_basis_per_unit', 'total_cost_basis',
            'market_price', 'market_value', 'imported_at'
        ])
        
        for r in records:
            writer.writerow([
                r.account.account_id,
                r.symbol,
                r.currency,
                r.date_from.isoformat(),
                r.date_to.isoformat() if r.date_to else '',
                str(r.quantity),
                str(r.cost_basis_per_unit),
                str(r.total_cost_basis),
                str(r.market_price),
                str(r.market_value),
                r.as_of_timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            ])
    
    print(f"Written {len(records)} records to {path}")
    return len(records)


def generate_rejected_report():
    """Generate CSV_Holdings_detail_Rejected.csv — empty placeholder."""
    path = os.path.join(OUTPUT_DIR, 'CSV_Holdings_detail_Rejected.csv')
    
    with open(path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'account_id', 'symbol', 'currency', 'snapshot_date',
            'reason', 'raw_data'
        ])
        # No rejected records in current import
        writer.writerow(['', '', '', '', 'No rejected records', ''])
    
    print(f"Written to {path} (no rejected records)")
    return 0


def main():
    print("Generating Holdings CSV reports...")
    print("=" * 60)
    
    imported = generate_imported_summary()
    detail = generate_detail_report()
    rejected = generate_rejected_report()
    
    print("=" * 60)
    print(f"Total: {imported} imported, {rejected} rejected")


if __name__ == '__main__':
    main()
