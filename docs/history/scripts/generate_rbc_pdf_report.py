#!/usr/bin/env python
"""
Generate RBC PDF Coverage Report (Markdown)
============================================
Analyzes all RBC accounts and identifies missing PDF statements
from 2020-01-01 to 2026-01-01.

Usage:
    python scripts/generate_rbc_pdf_report.py

Output:
    docs/rbc_pdf_coverage_report.md
"""

import os
import sys
from datetime import date, timedelta
from decimal import Decimal

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
import django
django.setup()

from core.models import Account, RbcMonthly, RbcAnnual


def _has_gap_in_ranges(ranges):
    """Check if continuous ranges have gaps (>45 days between consecutive ranges)."""
    for i in range(1, len(ranges)):
        if ranges[i]['start'] - ranges[i-1]['end'] > timedelta(days=45):
            return True
    return False


def _get_gap_details(ranges):
    """Get details of gaps between consecutive ranges."""
    gaps = []
    for i in range(1, len(ranges)):
        gap_days = (ranges[i]['start'] - ranges[i-1]['end']).days
        if gap_days > 45:
            gaps.append({
                'prev_end': ranges[i-1]['end'],
                'curr_start': ranges[i]['start'],
                'days': gap_days
            })
    return gaps


def get_monthly_coverage():
    """Get monthly statement coverage per account.
    
    Uses balance continuity checking instead of fixed monthly expectations.
    If stmt1.end_total_market_value == stmt2.begin_total_market_value (where
    stmt2 is the next statement for the same account/currency), then no
    statements are missing between them - the balance carried forward correctly.
    """
    accounts = Account.objects.filter(broker='RBC').order_by('account_id')
    
    results = []
    for account in accounts:
        statements = RbcMonthly.objects.filter(
            account=account,
            statement_date__gte=date(2020, 1, 1),
            statement_date__lt=date(2026, 1, 1)
        ).order_by('statement_date')
        
        # Group statements by currency
        from collections import defaultdict
        cad_stmts = []
        usd_stmts = []
        cad_dates = set()
        usd_dates = set()
        
        for stmt in statements:
            year_month = stmt.statement_date.strftime('%Y-%m')
            if stmt.currency == 'CAD':
                cad_stmts.append(stmt)
                cad_dates.add(year_month)
            elif stmt.currency == 'USD':
                usd_stmts.append(stmt)
                usd_dates.add(year_month)
        
        # Check balance continuity for CAD
        cad_continuous_ranges = []
        if cad_stmts:
            current_range = {
                'start': cad_stmts[0].statement_date,
                'end': cad_stmts[0].statement_date,
                'continuous': True,
                'gaps': []
            }
            
            for i in range(1, len(cad_stmts)):
                prev_stmt = cad_stmts[i-1]
                curr_stmt = cad_stmts[i]
                
                # Check if end balance of previous matches begin balance of current
                prev_end = prev_stmt.end_total_market_value or Decimal('0.00')
                curr_begin = curr_stmt.begin_total_market_value or Decimal('0.00')
                
                if prev_end == curr_begin:
                    # Balance matches - continuous
                    current_range['end'] = curr_stmt.statement_date
                else:
                    # Balance doesn't match - gap detected
                    cad_continuous_ranges.append(current_range)
                    current_range = {
                        'start': curr_stmt.statement_date,
                        'end': curr_stmt.statement_date,
                        'continuous': True,
                        'gaps': [f"{prev_stmt.statement_date} to {curr_stmt.statement_date}"]
                    }
            
            cad_continuous_ranges.append(current_range)
        
        # Check balance continuity for USD
        usd_continuous_ranges = []
        if usd_stmts:
            current_range = {
                'start': usd_stmts[0].statement_date,
                'end': usd_stmts[0].statement_date,
                'continuous': True,
                'gaps': []
            }
            
            for i in range(1, len(usd_stmts)):
                prev_stmt = usd_stmts[i-1]
                curr_stmt = usd_stmts[i]
                
                prev_end = prev_stmt.end_total_market_value or Decimal('0.00')
                curr_begin = curr_stmt.begin_total_market_value or Decimal('0.00')
                
                if prev_end == curr_begin:
                    current_range['end'] = curr_stmt.statement_date
                else:
                    usd_continuous_ranges.append(current_range)
                    current_range = {
                        'start': curr_stmt.statement_date,
                        'end': curr_stmt.statement_date,
                        'continuous': True,
                        'gaps': [f"{prev_stmt.statement_date} to {curr_stmt.statement_date}"]
                    }
            
            usd_continuous_ranges.append(current_range)
        
        # Calculate coverage based on date ranges (not fixed monthly expectations)
        start_date = date(2020, 1, 1)
        end_date = date(2025, 12, 31)
        
        # For CAD
        if cad_stmts:
            cad_actual_start = min(s.statement_date for s in cad_stmts)
            cad_actual_end = max(s.statement_date for s in cad_stmts)
            # Count expected months in actual range
            cad_expected_months = 0
            current = cad_actual_start
            while current <= cad_actual_end:
                cad_expected_months += 1
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1, day=1)
                else:
                    current = current.replace(month=current.month + 1, day=1)
            cad_coverage_pct = len(cad_dates) / cad_expected_months * 100 if cad_expected_months > 0 else 0
        else:
            cad_actual_start = None
            cad_actual_end = None
            cad_expected_months = 0
            cad_coverage_pct = 0
        
        # For USD
        if usd_stmts:
            usd_actual_start = min(s.statement_date for s in usd_stmts)
            usd_actual_end = max(s.statement_date for s in usd_stmts)
            usd_expected_months = 0
            current = usd_actual_start
            while current <= usd_actual_end:
                usd_expected_months += 1
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1, day=1)
                else:
                    current = current.replace(month=current.month + 1, day=1)
            usd_coverage_pct = len(usd_dates) / usd_expected_months * 100 if usd_expected_months > 0 else 0
        else:
            usd_actual_start = None
            usd_actual_end = None
            usd_expected_months = 0
            usd_coverage_pct = 0
        
        results.append({
            'account': account,
            'cad_stmts': cad_stmts,
            'usd_stmts': usd_stmts,
            'cad_dates': sorted(cad_dates),
            'usd_dates': sorted(usd_dates),
            'cad_continuous_ranges': cad_continuous_ranges,
            'usd_continuous_ranges': usd_continuous_ranges,
            'cad_count': len(cad_dates),
            'usd_count': len(usd_dates),
            'cad_expected_months': cad_expected_months,
            'usd_expected_months': usd_expected_months,
            'cad_coverage_pct': cad_coverage_pct,
            'usd_coverage_pct': usd_coverage_pct,
            'cad_actual_start': cad_actual_start,
            'cad_actual_end': cad_actual_end,
            'usd_actual_start': usd_actual_start,
            'usd_actual_end': usd_actual_end,
        })
    
    return results


def get_annual_coverage():
    """Get annual report coverage per account."""
    accounts = Account.objects.filter(broker='RBC').order_by('account_id')
    
    results = []
    for account in accounts:
        annuals = RbcAnnual.objects.filter(
            account=account,
            report_year__gte=2020,
            report_year__lt=2026
        ).order_by('report_year')
        
        # Expected years
        expected_years = list(range(2020, 2026))
        
        # Get existing years per currency
        cad_years = set()
        usd_years = set()
        for annual in annuals:
            if annual.currency == 'CAD':
                cad_years.add(annual.report_year)
            elif annual.currency == 'USD':
                usd_years.add(annual.report_year)
        
        # Find missing years per currency
        cad_missing = [y for y in expected_years if y not in cad_years]
        usd_missing = [y for y in expected_years if y not in usd_years]
        
        results.append({
            'account': account,
            'cad_years': sorted(cad_years),
            'usd_years': sorted(usd_years),
            'cad_missing': cad_missing,
            'usd_missing': usd_missing,
            'cad_count': len(cad_years),
            'usd_count': len(usd_years),
            'cad_missing_count': len(cad_missing),
            'usd_missing_count': len(usd_missing),
        })
    
    return results


def generate_markdown_report(monthly_data, annual_data):
    """Generate markdown report."""
    
    lines = []
    lines.append("# RBC PDF Coverage Report\n")
    lines.append("**Period:** 2020-01-01 to 2025-12-31\n")
    lines.append("**Generated:** " + date.today().strftime("%Y-%m-%d") + "\n")
    lines.append("\n---\n")
    lines.append("\n")
    
   # Summary
    total_accounts = len(monthly_data)
    total_cad_months = sum(m['cad_count'] for m in monthly_data)
    total_usd_months = sum(m['usd_count'] for m in monthly_data)
    total_cad_gaps = sum(1 for m in monthly_data if _has_gap_in_ranges(m.get('cad_continuous_ranges', [])))
    total_usd_gaps = sum(1 for m in monthly_data if _has_gap_in_ranges(m.get('usd_continuous_ranges', [])))
    
    accounts_with_cad = sum(1 for m in monthly_data if m['cad_count'] > 0)
    accounts_with_usd = sum(1 for m in monthly_data if m['usd_count'] > 0)
    
    lines.append("## Summary\n")
    lines.append(f"- **Total RBC Accounts:** {total_accounts}\n")
    lines.append(f"- **Accounts with CAD Statements:** {accounts_with_cad}\n")
    lines.append(f"- **Total CAD Statements Found:** {total_cad_months}\n")
    lines.append(f"- **Accounts with Balance Gaps (CAD):** {total_cad_gaps}\n")
    lines.append(f"- **Accounts with USD Statements:** {accounts_with_usd}\n")
    lines.append(f"- **Total USD Statements Found:** {total_usd_months}\n")
    lines.append(f"- **Accounts with Balance Gaps (USD):** {total_usd_gaps}\n")
    lines.append(f"\n**Note:** Balance gaps detected when end balance of one statement ≠ begin balance of next statement\n")
    lines.append("\n---\n")
    lines.append("\n")
    
    # Annual Summary
    total_cad_years = sum(a['cad_count'] for a in annual_data)
    total_usd_years = sum(a['usd_count'] for a in annual_data)
    total_cad_missing_years = sum(a['cad_missing_count'] for a in annual_data)
    total_usd_missing_years = sum(a['usd_missing_count'] for a in annual_data)
    expected_years = 6  # 2020-2025
    total_expected_cad_years = total_accounts * expected_years
    total_expected_usd_years = total_accounts * expected_years
    
    lines.append("## Annual Reports Summary\n")
    lines.append(f"- **Expected Annual Reports (CAD):** {total_expected_cad_years}\n")
    lines.append(f"- **Found (CAD):** {total_cad_years} ({total_cad_years/total_expected_cad_years*100:.1f}%)\n")
    lines.append(f"- **Missing (CAD):** {total_cad_missing_years} ({total_cad_missing_years/total_expected_cad_years*100:.1f}%)\n")
    lines.append(f"- **Expected Annual Reports (USD):** {total_expected_usd_years}\n")
    lines.append(f"- **Found (USD):** {total_usd_years} ({total_usd_years/total_expected_usd_years*100:.1f}%)\n")
    lines.append(f"- **Missing (USD):** {total_usd_missing_years} ({total_usd_missing_years/total_expected_usd_years*100:.1f}%)\n")
    lines.append("\n---\n")
    lines.append("\n")
    
    # Monthly Coverage Details
    lines.append("## Monthly Statement Coverage\n")
    lines.append("\n")
    
    for data in monthly_data:
        account = data['account']
        lines.append(f"### {account.account_id} - {account.name}\n")
        lines.append(f"**Type:** {account.category} | **Currency:** {account.currency}\n")
        lines.append("\n")
        
        # CAD Coverage
        lines.append("**CAD Statements:**\n")
        lines.append(f"- **Found:** {data['cad_count']} statements\n")
        
        if data['cad_continuous_ranges']:
            lines.append(f"- **Continuous Ranges:**\n")
            for rng in data['cad_continuous_ranges']:
                lines.append(f"  - {rng['start'].strftime('%Y-%m-%d')} to {rng['end'].strftime('%Y-%m-%d')}\n")
            
            # Check for gaps
            if _has_gap_in_ranges(data['cad_continuous_ranges']):
                lines.append(f"- **Balance Gaps Detected:** Yes ⚠️\n")
                # Show gap details
                gap_details = _get_gap_details(data['cad_continuous_ranges'])
                for gap in gap_details:
                    lines.append(f"  - Gap: {gap['prev_end'].strftime('%Y-%m-%d')} → {gap['curr_start'].strftime('%Y-%m-%d')} ({gap['days']} days)\n")
            else:
                lines.append(f"- **Balance Gaps Detected:** None ✓\n")
        else:
            lines.append(f"- **No CAD statements found**\n")
        
        lines.append("\n")
        
        # USD Coverage (if applicable)
        if data['usd_count'] > 0:
            lines.append("**USD Statements:**\n")
            lines.append(f"- **Found:** {data['usd_count']} statements\n")
            
            if data['usd_continuous_ranges']:
                lines.append(f"- **Continuous Ranges:**\n")
                for rng in data['usd_continuous_ranges']:
                    lines.append(f"  - {rng['start'].strftime('%Y-%m-%d')} to {rng['end'].strftime('%Y-%m-%d')}\n")
                
                # Check for gaps
                if _has_gap_in_ranges(data['usd_continuous_ranges']):
                    lines.append(f"- **Balance Gaps Detected:** Yes ⚠️\n")
                    gap_details = _get_gap_details(data['usd_continuous_ranges'])
                    for gap in gap_details:
                        lines.append(f"  - Gap: {gap['prev_end'].strftime('%Y-%m-%d')} → {gap['curr_start'].strftime('%Y-%m-%d')} ({gap['days']} days)\n")
                else:
                    lines.append(f"- **Balance Gaps Detected:** None ✓\n")
            else:
                lines.append(f"- **No USD statements found** (account may not have USD investments)\n")
            
            lines.append("\n")
        
        lines.append("\n")
    
    lines.append("\n---\n")
    lines.append("\n")
    
    # Annual Report Coverage Details
    lines.append("## Annual Report Coverage\n")
    lines.append("\n")
    
    for data in annual_data:
        account = data['account']
        lines.append(f"### {account.account_id} - {account.name}\n")
        lines.append(f"**Type:** {account.category}\n")
        lines.append("\n")
        
        # CAD Coverage
        lines.append("**CAD Reports:**\n")
        lines.append(f"- **Found:** {data['cad_count']} | **Missing:** {data['cad_missing_count']} | **Coverage:** {data['cad_count']/expected_years*100:.1f}%\n")
        
        if data['cad_years']:
            lines.append(f"- **Years:** {', '.join(str(y) for y in data['cad_years'])}\n")
        
        if data['cad_missing']:
            lines.append(f"- **Missing Years:** {', '.join(str(y) for y in data['cad_missing'])}\n")
        else:
            lines.append("- **Missing:** None ✓\n")
        
        lines.append("\n")
        
        # USD Coverage (if applicable)
        if data['usd_count'] > 0 or data['usd_missing_count'] > 0:
            lines.append("**USD Reports:**\n")
            lines.append(f"- **Found:** {data['usd_count']} | **Missing:** {data['usd_missing_count']} | **Coverage:** {data['usd_count']/expected_years*100:.1f}%\n")
            
            if data['usd_years']:
                lines.append(f"- **Years:** {', '.join(str(y) for y in data['usd_years'])}\n")
            
            if data['usd_missing']:
                lines.append(f"- **Missing Years:** {', '.join(str(y) for y in data['usd_missing'])}\n")
            else:
                lines.append("- **Missing:** None ✓\n")
            
            lines.append("\n")
        
        lines.append("\n")
    
    lines.append("\n---\n")
    lines.append("\n")
    
    # Balance Gaps Quick Reference
    lines.append("## Balance Gaps Quick Reference\n")
    lines.append("\n")
    lines.append("Accounts with balance continuity gaps (top 10):\n")
    lines.append("\n")
    
    # Sort monthly by number of gaps (accounts with gaps first)
    def count_gaps(data):
        cad_gaps = sum(1 for r in data.get('cad_continuous_ranges', []) if _has_gap_in_ranges([r]) if len(data.get('cad_continuous_ranges', [])) > 1)
        usd_gaps = sum(1 for r in data.get('usd_continuous_ranges', []) if _has_gap_in_ranges([r]) if len(data.get('usd_continuous_ranges', [])) > 1)
        # Simpler: just count ranges with gaps
        cad_has_gaps = 1 if _has_gap_in_ranges(data.get('cad_continuous_ranges', [])) else 0
        usd_has_gaps = 1 if _has_gap_in_ranges(data.get('usd_continuous_ranges', [])) else 0
        return cad_has_gaps + usd_has_gaps
    
    monthly_sorted = sorted(monthly_data, key=lambda x: count_gaps(x), reverse=True)
    annual_sorted = sorted(annual_data, key=lambda x: x['cad_missing_count'] + x['usd_missing_count'], reverse=True)
    
    lines.append("### Monthly Statements - Accounts with Balance Gaps\n")
    lines.append("\n")
    lines.append("| Account | Name | CAD Statements | USD Statements | CAD Gaps | USD Gaps |\n")
    lines.append("|---------|------|----------------|----------------|----------|----------|\n")
    
    for data in monthly_sorted[:10]:
        account = data['account']
        cad_gaps = "Yes" if _has_gap_in_ranges(data.get('cad_continuous_ranges', [])) else "No"
        usd_gaps = "Yes" if _has_gap_in_ranges(data.get('usd_continuous_ranges', [])) else "No"
        lines.append(f"| {account.account_id} | {account.name[:30]} | {data['cad_count']} | {data['usd_count']} | {cad_gaps} | {usd_gaps} |\n")
    
    lines.append("\n")
    lines.append("\n")
    
    lines.append("### Annual Reports - Top 10 Missing\n")
    lines.append("\n")
    lines.append("| Account | Name | CAD Missing | USD Missing | Total Missing | Coverage |\n")
    lines.append("|---------|------|-------------|-------------|---------------|----------|\n")
    
    for data in annual_sorted[:10]:
        account = data['account']
        total_missing = data['cad_missing_count'] + data['usd_missing_count']
        coverage = (data['cad_count'] + data['usd_count']) / (expected_years * 2) * 100
        lines.append(f"| {account.account_id} | {account.name[:30]} | {data['cad_missing_count']} | {data['usd_missing_count']} | {total_missing} | {coverage:.1f}% |\n")
    
    lines.append("\n")
    lines.append("\n---\n")
    lines.append("\n")
    lines.append("*Report generated automatically by generate_rbc_pdf_report.py*\n")
    
    return '\n'.join(lines)


def main():
    """Main entry point."""
    print("Fetching monthly statement coverage...")
    monthly_data = get_monthly_coverage()
    print(f"  Found {len(monthly_data)} RBC accounts with monthly data")
    
    print("Fetching annual report coverage...")
    annual_data = get_annual_coverage()
    print(f"  Found {len(annual_data)} RBC accounts with annual data")
    
    print("Generating markdown report...")
    report = generate_markdown_report(monthly_data, annual_data)
    
    output_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'rbc_pdf_coverage_report.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✓ Report generated: {output_path}")
    print(f"  - {len(monthly_data)} accounts analyzed (monthly)")
    print(f"  - {len(annual_data)} accounts analyzed (annual)")


if __name__ == '__main__':
    main()
