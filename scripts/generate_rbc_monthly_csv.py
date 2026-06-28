#!/usr/bin/env python
"""
Generate RBC Monthly Statement CSV Report
===========================================
For each account, lists every month from 2020-01 to 2025-12.
Shows begin/end balances for statements that exist.
Shows "missing" for months without statements.

Output: Two CSV files (CAD and USD) with all accounts.

Usage:
    python scripts/generate_rbc_monthly_csv.py
"""

import os
import sys
import csv
from datetime import date, timedelta
from decimal import Decimal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()

from core.models import Account, RbcMonthly


def get_monthly_data():
    """Get all monthly statements grouped by account and currency."""
    accounts = Account.objects.filter(broker='RBC').order_by('account_id')
    
    # Structure: {account: {'CAD': [statements], 'USD': [statements]}}
    data = {}
    
    for account in accounts:
        statements = RbcMonthly.objects.filter(
            account=account,
            statement_date__gte=date(2020, 1, 1),
            statement_date__lt=date(2026, 1, 1)
        ).order_by('statement_date')
        
        data[account] = {
            'CAD': list(statements.filter(currency='CAD')),
            'USD': list(statements.filter(currency='USD'))
        }
    
    return data


def generate_monthly_rows(account, statements, currency):
    """Generate rows for all months, marking only truly missing ones.
    
    If stmt_A.end_balance == stmt_B.begin_balance (where stmt_B is a later statement),
    then the months between A and B are NOT missing - they just had no transactions.
    Only mark as 'missing' if we can't explain the gap with balance continuity.
    """
    rows = []
    
    # Sort statements by date
    statements.sort(key=lambda s: s.statement_date)
    
    # Build a lookup of existing statements by year-month
    existing = {}
    for stmt in statements:
        key = stmt.statement_date.strftime('%Y-%m')
        existing[key] = stmt
    
    # Find all explainable gaps: months between statements where balances match
    explainable_months = set()
    for i in range(len(statements)):
        for j in range(i + 1, len(statements)):
            stmt_a = statements[i]
            stmt_b = statements[j]
            
            end_bal_a = float(stmt_a.end_total_market_value) if stmt_a.end_total_market_value else None
            begin_bal_b = float(stmt_b.begin_total_market_value) if stmt_b.begin_total_market_value else None
            
            # If end balance of A matches begin balance of B, all months between are explainable
            if end_bal_a is not None and begin_bal_b is not None:
                # Check if balances match (within 0.01 tolerance for floating point)
                if abs(end_bal_a - begin_bal_b) < 0.01:
                    # Mark all months between A and B as explainable
                    current = stmt_a.statement_date
                    end = stmt_b.statement_date
                    while current < end:
                        if current.month == 12:
                            current = current.replace(year=current.year + 1, month=1, day=1)
                        else:
                            current = current.replace(month=current.month + 1, day=1)
                        if current < end:
                            explainable_months.add(current.strftime('%Y-%m'))
    
    # Generate all months from 2020-01 to 2025-12
    current = date(2020, 1, 1)
    end = date(2025, 12, 1)
    
    while current <= end:
        key = current.strftime('%Y-%m')
        year = current.year
        month_num = current.month
        month_name = current.strftime('%b')
        
        if key in existing:
            # This month has a statement
            stmt = existing[key]
            begin_bal = float(stmt.begin_total_market_value) if stmt.begin_total_market_value else ''
            end_bal = float(stmt.end_total_market_value) if stmt.end_total_market_value else ''
            rows.append({
                'account_id': account.account_id,
                'account_name': account.name,
                'currency': currency,
                'year': year,
                'month': month_name,
                'month_num': month_num,
                'begin_balance': begin_bal,
                'end_balance': end_bal
            })
        elif key in explainable_months:
            # This month is explainable (no transactions, balance carried forward)
            rows.append({
                'account_id': account.account_id,
                'account_name': account.name,
                'currency': currency,
                'year': year,
                'month': month_name,
                'month_num': month_num,
                'begin_balance': '',
                'end_balance': ''
            })
        else:
            # This month is truly missing
            rows.append({
                'account_id': account.account_id,
                'account_name': account.name,
                'currency': currency,
                'year': year,
                'month': month_name,
                'month_num': month_num,
                'begin_balance': '',
                'end_balance': 'missing'
            })
        
        # Move to next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)
    
    return rows


def write_csv(filepath, all_rows):
    """Write rows to CSV file."""
    fieldnames = ['account_id', 'account_name', 'currency', 'year', 'month', 'month_num', 'begin_balance', 'end_balance']
    
    with open(filepath, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)


def main():
    """Main entry point."""
    print("Fetching monthly statement data...")
    data = get_monthly_data()
    print(f"  Found {len(data)} RBC accounts")
    
    # Generate rows for CAD and USD separately
    cad_rows = []
    usd_rows = []
    
    for account, currencies in data.items():
        # CAD rows
        cad_rows.extend(generate_monthly_rows(account, currencies['CAD'], 'CAD'))
        
        # USD rows
        usd_rows.extend(generate_monthly_rows(account, currencies['USD'], 'USD'))
    
    # Sort by account_id, then year, then month
    cad_rows.sort(key=lambda x: (x['account_id'], x['year'], x['month_num']))
    usd_rows.sort(key=lambda x: (x['account_id'], x['year'], x['month_num']))
    
    # Write CSV files
    output_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs')
    os.makedirs(output_dir, exist_ok=True)
    
    cad_path = os.path.join(output_dir, 'rbc_monthly_cad.csv')
    usd_path = os.path.join(output_dir, 'rbc_monthly_usd.csv')
    
    write_csv(cad_path, cad_rows)
    write_csv(usd_path, usd_rows)
    
    # Summary
    cad_present = sum(1 for r in cad_rows if r['end_balance'] != 'missing' and r['end_balance'] != '')
    cad_missing = sum(1 for r in cad_rows if r['end_balance'] == 'missing')
    usd_present = sum(1 for r in usd_rows if r['end_balance'] != 'missing' and r['end_balance'] != '')
    usd_missing = sum(1 for r in usd_rows if r['end_balance'] == 'missing')
    
    print(f"\n✓ CAD Report generated: {cad_path}")
    print(f"  - Total rows: {len(cad_rows)}")
    print(f"  - Present: {cad_present}")
    print(f"  - Missing: {cad_missing}")
    
    print(f"\n✓ USD Report generated: {usd_path}")
    print(f"  - Total rows: {len(usd_rows)}")
    print(f"  - Present: {usd_present}")
    print(f"  - Missing: {usd_missing}")


if __name__ == '__main__':
    main()
