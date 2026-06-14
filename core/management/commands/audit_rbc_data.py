"""RBC Data Audit & Reconciliation Command.

Comprehensive audit program to verify data integrity after RBC PDF imports.
Runs 10 checks to detect issues early and enable cleanup before data corrupts
reports/analytics.

Usage:
    python manage.py audit_rbc_data
    python manage.py audit_rbc_data --verbose
    python manage.py audit_rbc_data --json > audit_report.json
    python manage.py audit_rbc_data --fix  # auto-fix reversible issues
    python manage.py audit_rbc_data --md  # generate markdown report
    python manage.py audit_rbc_data --md --output-dir processed\audit
    python manage.py audit_rbc_data --start-year 2025  # only audit from 2025 onwards
"""
import json
import logging
import sys
import warnings
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Suppress WeasyPrint and other noisy warnings before Django imports
warnings.filterwarnings('ignore')
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', category=PendingDeprecationWarning)

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Sum, Count, Q, F, Exists, OuterRef
from django.utils import timezone

from core.models import (
    Account,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyTransaction,
)

logger = logging.getLogger(__name__)

# Tolerance constants
BALANCE_TOLERANCE = Decimal('0.02')  # $0.02 rounding tolerance
ANNUAL_TOLERANCE_PCT = Decimal('0.01')  # 1% tolerance for annual vs monthly
ANNUAL_TOLERANCE_ABS = Decimal('10.00')  # $10.00 absolute tolerance
FX_RATE_MIN = Decimal('0.60')  # Minimum reasonable CAD/USD rate
FX_RATE_MAX = Decimal('1.50')  # Maximum reasonable CAD/USD rate
FX_RATE_USD_NORMAL_MIN = Decimal('1.20')  # USD statements should have fx_rate > 1.20
GAP_WARNING_DAYS = 45  # Days before flagging as warning
GAP_ERROR_DAYS = 90  # Days before flagging as error
TREND_CHANGE_PCT = Decimal('30')  # % change threshold for market value trends


class AuditResult:
    """Stores audit findings."""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.info = []
        self.ok_count = 0
        self.checks_run = 0
        self.checks_failed = 0
    
    def add_error(self, check_name, message, details=None):
        self.errors.append({
            'check': check_name,
            'severity': 'ERROR',
            'message': message,
            'details': details or {},
        })
    
    def add_warning(self, check_name, message, details=None):
        self.warnings.append({
            'check': check_name,
            'severity': 'WARNING',
            'message': message,
            'details': details or {},
        })
    
    def add_info(self, check_name, message, details=None):
        self.info.append({
            'check': check_name,
            'severity': 'INFO',
            'message': message,
            'details': details or {},
        })
    
    def add_ok(self, check_name, message):
        self.ok_count += 1
        self.info.append({
            'check': check_name,
            'severity': 'OK',
            'message': message,
        })
    
    @property
    def has_errors(self):
        return len(self.errors) > 0
    
    @property
    def has_warnings(self):
        return len(self.warnings) > 0
    
    def exit_code(self):
        if self.has_errors:
            return 2
        elif self.has_warnings:
            return 1
        return 0


class TerminalPrinter:
    """Formats and prints audit results to terminal."""
    
    SEVERITY_COLORS = {
        'ERROR': '\x1b[31;1m',    # Red bold
        'WARNING': '\x1b[33;1m',  # Yellow bold
        'INFO': '\x1b[36;1m',     # Cyan
        'OK': '\x1b[32;1m',       # Green bold
    }
    RESET = '\x1b[0m'
    
    def __init__(self, verbose=False):
        self.verbose = verbose
    
    def print(self, text, severity=None):
        if severity and severity in self.SEVERITY_COLORS:
            color = self.SEVERITY_COLORS[severity]
            print(f'{color}{text}{self.RESET}')
        else:
            print(text)
    
    def print_header(self, title):
        print('\n' + '=' * 80)
        self.print(title, 'INFO')
        print('=' * 80)
    
    def print_section(self, title):
        print(f'\n[{title}]')
    
    def print_check_result(self, check_name, result_type, message, details=None):
        prefix = f'  [{result_type}] {check_name}:'
        if result_type == 'ERROR':
            self.print(prefix, 'ERROR')
        elif result_type == 'WARNING':
            self.print(prefix, 'WARNING')
        elif result_type == 'OK':
            self.print(prefix, 'OK')
        else:
            self.print(prefix)
        
        if details and self.verbose:
            for key, value in details.items():
                self.print(f'    → {key}: {value}')


class RBCDataAuditor:
    """Main audit engine that runs all checks."""
    
    def __init__(self, verbose=False, fix=False, start_year=None):
        self.result = AuditResult()
        self.printer = TerminalPrinter(verbose=verbose)
        self.fix = fix
        self.start_year = start_year
        self.start_date = None
        if start_year is not None:
            self.start_date = datetime(start_year, 1, 1)
    
    def _filter_by_start_date(self, queryset, date_field='statement_date'):
        """Filter queryset to only include records from start_date onwards.
        
        Args:
            queryset: Django QuerySet to filter
            date_field: Field name for date filtering (default: statement_date)
        
        Returns:
            Filtered QuerySet, or original if no start_year specified
        """
        if self.start_date is None:
            return queryset
        return queryset.filter(**{f'{date_field}__gte': self.start_date})
    
    def run(self):
        """Run all audit checks."""
        self.printer.print_header('RBC DATA AUDIT REPORT')
        self.printer.print(f'Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        
        # Show start year filter if specified
        if self.start_year is not None:
            self.printer.print(f'Start Year: {self.start_year} (only records from {self.start_date.strftime("%Y-%m-%d")} onwards)')
        
        # Print summary stats
        self._print_summary_stats()
        
        # Run all checks
        self._check_1_statement_continuity()
        self._check_2_balance_sum_reconciliation()
        self._check_3_annual_vs_monthly()
        self._check_4_currency_validation()
        self._check_5_exchange_rate_validation()
        self._check_6_account_matching()
        self._check_7_transaction_integrity()
        self._check_8_orphaned_records()
        self._check_9_market_value_trends()
        self._check_10_data_completeness()
        
        # Print final report
        self._print_final_report()
        
        return self.result


    def _print_summary_stats(self):
        """Print database statistics."""
        monthly_qs = self._filter_by_start_date(RbcMonthly.objects.all())
        balance_qs = self._filter_by_start_date(
            RbcMonthlyBalance.objects.select_related('rbc_monthly'),
            date_field='rbc_monthly__statement_date'
        )
        tx_qs = self._filter_by_start_date(
            RbcMonthlyTransaction.objects.select_related('rbc_monthly'),
            date_field='rbc_monthly__statement_date'
        )
        
        stats = {
            'RbcMonthly records': monthly_qs.count(),
            'RbcMonthlyBalance records': balance_qs.count(),
            'RbcMonthlyTransaction records': tx_qs.count(),
            'RbcAnnual records': RbcAnnual.objects.count(),
            'RBC accounts': Account.objects.filter(broker='RBC').count(),
        }
        
        self.printer.print('\nDATABASE STATISTICS')
        self.printer.print('-' * 40)
        for key, value in stats.items():
            self.printer.print(f'  {key:30s}: {value:>10,}')
    
    def _check_1_statement_continuity(self):
        """CHECK 1: Statement continuity and temporal gaps."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 1: Statement Continuity')
        
        # Get all (account, currency) pairs filtered by start_date
        accounts_currencies = self._filter_by_start_date(
            RbcMonthly.objects.all()
        ).values_list(
            'account_id', 'currency'
        ).distinct()
        
        gap_errors = 0
        gap_warnings = 0
        continuous_ok = 0
        
        for account_id, currency in accounts_currencies:
            # REVERSE CHRONOLOGICAL: Latest dates first for prioritized fixing
            statements = self._filter_by_start_date(
                RbcMonthly.objects.filter(
                    account_id=account_id,
                    currency=currency
                ).order_by('-statement_date')
            )
            
            if statements.count() < 2:
                continue
            
            stmt_list = list(statements)
            account = stmt_list[0].account
            
            # Iterate in reverse chronological order (skip first, pair with next)
            for i in range(len(stmt_list) - 1):
                curr_stmt = stmt_list[i]       # More recent
                prev_stmt = stmt_list[i + 1]   # Less recent (previous in time)
                
                days_gap = (curr_stmt.statement_date - prev_stmt.statement_date).days
                
                # Check for overlapping dates
                if days_gap < 0:
                    self.result.add_error(
                        'continuity',
                        f'{account.account_id} ({currency}): Overlapping dates detected',
                        {
                            'prev_date': str(prev_stmt.statement_date),
                            'curr_date': str(curr_stmt.statement_date),
                            'prev_file': prev_stmt.file_name,
                            'curr_file': curr_stmt.file_name,
                        }
                    )
                    gap_errors += 1
                    continue
                
                # Check for large gaps
                if days_gap > GAP_ERROR_DAYS:
                    missing_months = self._count_missing_months(
                        prev_stmt.statement_date, curr_stmt.statement_date
                    )
                    self.result.add_error(
                        'continuity',
                        f'{account.account_id} ({currency}): Gap of {days_gap} days between '
                        f'{prev_stmt.statement_date} and {curr_stmt.statement_date}',
                        {
                            'gap_days': days_gap,
                            'missing_months': missing_months,
                            'prev_end_total': str(prev_stmt.end_total_market_value),
                            'curr_begin_total': str(curr_stmt.begin_total_market_value),
                        }
                    )
                    gap_errors += 1
                
                elif days_gap > GAP_WARNING_DAYS:
                    missing_months = self._count_missing_months(
                        prev_stmt.statement_date, curr_stmt.statement_date
                    )
                    self.result.add_warning(
                        'continuity',
                        f'{account.account_id} ({currency}): Gap of {days_gap} days between '
                        f'{prev_stmt.statement_date} and {curr_stmt.statement_date}',
                        {
                            'gap_days': days_gap,
                            'missing_months': missing_months,
                        }
                    )
                    gap_warnings += 1
                
                # Check begin/end total matching (within tolerance)
                if days_gap <= 65:  # Only check if gap is <= ~2 months
                    discrepancy = abs(
                        curr_stmt.begin_total_market_value - prev_stmt.end_total_market_value
                    )
                    if discrepancy > BALANCE_TOLERANCE:
                        self.result.add_warning(
                            'continuity',
                            f'{account.account_id} ({currency}): Begin/end mismatch between '
                            f'{prev_stmt.statement_date} and {curr_stmt.statement_date}',
                            {
                                'prev_end_total': str(prev_stmt.end_total_market_value),
                                'curr_begin_total': str(curr_stmt.begin_total_market_value),
                                'discrepancy': str(discrepancy),
                            }
                        )
                        gap_warnings += 1
            
            if gap_errors == 0 and gap_warnings == 0:
                continuous_ok += 1
        
        if gap_errors == 0 and gap_warnings == 0:
            self.result.add_ok('continuity', f'All {continuous_ok} account/currency pairs continuous')
        else:
            self.result.add_info('continuity', 
                f'Found {gap_errors} errors, {gap_warnings} warnings, {continuous_ok} OK')
    
    def _count_missing_months(self, start_date, end_date):
        """Count missing months between two dates."""
        months = []
        current = start_date
        while current < end_date:
            # Move to next month
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)
            months.append(current.strftime('%b %Y'))
        return months
    
    def _check_2_balance_sum_reconciliation(self):
        """CHECK 2: Balance sum matches end_total_market_value."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 2: Balance Sum Reconciliation')
        
        # Use SQL for performance — REVERSE CHRONOLOGICAL: latest dates first
        with connection.cursor() as cursor:
            sql = """
                SELECT 
                    rm.id,
                    rm.account_id,
                    a.account_id as acc_display,
                    rm.statement_date,
                    rm.currency,
                    rm.end_total_market_value,
                    COALESCE(SUM(rmb.market_value), 0) as sum_balances
                FROM core_rbcmonthly rm
                JOIN core_account a ON rm.account_id = a.id
                LEFT JOIN core_rbcmonthlybalance rmb ON rm.id = rmb.rbc_monthly_id
            """
            where_clauses = []
            params = []
            
            if self.start_date:
                where_clauses.append('rm.statement_date >= %s')
                params.append(self.start_date)
            
            if where_clauses:
                sql += ' WHERE ' + ' AND '.join(where_clauses)
            
            sql += """
                GROUP BY rm.id, rm.account_id, a.account_id, rm.statement_date,
                         rm.currency, rm.end_total_market_value
                HAVING ABS(COALESCE(SUM(rmb.market_value), 0) - rm.end_total_market_value) > %s
                ORDER BY rm.statement_date DESC
            """
            params.append(str(BALANCE_TOLERANCE))
            
            cursor.execute(sql, params)
            mismatches = cursor.fetchall()
        
        if not mismatches:
            self.result.add_ok('balance_sum', 'All balance sums match end_total_market_value')
            return
        
        error_count = 0
        warning_count = 0
        
        for row in mismatches:
            rm_id, account_id, acc_display, stmt_date, currency, end_total, sum_bal = row
            discrepancy = abs(sum_bal - end_total)
            
            # Determine severity
            if end_total == 0 and sum_bal > 0:
                # Import bug — end_total not saved
                self.result.add_error(
                    'balance_sum',
                    f'RbcMonthly ID={rm_id} ({acc_display}, {currency}, {stmt_date}): '
                    f'end_total=0.00 but balances sum to ${sum_bal:,.2f}',
                    {
                        'end_total': str(end_total),
                        'sum_balances': str(sum_bal),
                        'discrepancy': str(discrepancy),
                        'note': 'IMPORT BUG — begin/end totals not being saved',
                    }
                )
                error_count += 1
            elif discrepancy > Decimal('100'):
                self.result.add_error(
                    'balance_sum',
                    f'RbcMonthly ID={rm_id} ({acc_display}, {currency}, {stmt_date}): '
                    f'discrepancy ${discrepancy:,.2f}',
                    {
                        'end_total': str(end_total),
                        'sum_balances': str(sum_bal),
                        'discrepancy': str(discrepancy),
                    }
                )
                error_count += 1
            else:
                self.result.add_warning(
                    'balance_sum',
                    f'RbcMonthly ID={rm_id} ({acc_display}, {currency}, {stmt_date}): '
                    f'discrepancy ${discrepancy:,.2f}',
                    {
                        'end_total': str(end_total),
                        'sum_balances': str(sum_bal),
                        'discrepancy': str(discrepancy),
                    }
                )
                warning_count += 1
        
        self.result.add_info('balance_sum',
            f'Checked all records: {error_count} errors, {warning_count} warnings')
    
    def _check_3_annual_vs_monthly(self):
        """CHECK 3: Annual vs monthly cross-check."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 3: Annual vs Monthly Cross-Check')
        
        # REVERSE CHRONOLOGICAL: latest years first
        annual_records = RbcAnnual.objects.select_related('account').order_by('-report_year')
        
        # Filter annual records by start_year if specified
        if self.start_year is not None:
            annual_records = annual_records.filter(report_year__gte=self.start_year)
        
        discrepancies = 0
        matches = 0
        
        for annual in annual_records:
            account = annual.account
            
            # Check ending value vs Dec monthly
            dec_monthly = RbcMonthly.objects.filter(
                account=account,
                currency=annual.currency,
                statement_date__year=annual.report_year,
                statement_date__month=12,
            ).order_by('-statement_date').first()
            
            if dec_monthly:
                ending_discrepancy = abs(
                    annual.bal_ending_value - dec_monthly.end_total_market_value
                )
                tolerance = max(
                    annual.bal_ending_value * ANNUAL_TOLERANCE_PCT,
                    ANNUAL_TOLERANCE_ABS,
                )
                
                if ending_discrepancy > tolerance:
                    self.result.add_error(
                        'annual_monthly',
                        f'{account.account_id} Annual {annual.report_year} ({annual.currency}): '
                        f'ending_value discrepancy ${ending_discrepancy:,.2f}',
                        {
                            'annual_ending': str(annual.bal_ending_value),
                            'monthly_ending': str(dec_monthly.end_total_market_value),
                            'discrepancy': str(ending_discrepancy),
                            'tolerance': str(tolerance),
                        }
                    )
                    discrepancies += 1
                else:
                    matches += 1
            
            # Check beginning value vs Jan monthly
            jan_monthly = RbcMonthly.objects.filter(
                account=account,
                currency=annual.currency,
                statement_date__year=annual.report_year,
                statement_date__month=1,
            ).order_by('statement_date').first()
            
            if jan_monthly:
                begin_discrepancy = abs(
                    annual.bal_beginning_value - jan_monthly.begin_total_market_value
                )
                tolerance = max(
                    annual.bal_beginning_value * ANNUAL_TOLERANCE_PCT,
                    ANNUAL_TOLERANCE_ABS,
                )
                
                if begin_discrepancy > tolerance:
                    self.result.add_warning(
                        'annual_monthly',
                        f'{account.account_id} Annual {annual.report_year} ({annual.currency}): '
                        f'beginning_value discrepancy ${begin_discrepancy:,.2f}',
                        {
                            'annual_beginning': str(annual.bal_beginning_value),
                            'monthly_beginning': str(jan_monthly.begin_total_market_value),
                            'discrepancy': str(begin_discrepancy),
                        }
                    )
                    discrepancies += 1
                else:
                    matches += 1
        
        if discrepancies == 0:
            self.result.add_ok('annual_monthly', f'All {matches} annual/monthly comparisons match')
        else:
            self.result.add_info('annual_monthly',
                f'Found {discrepancies} discrepancies, {matches} matches')
    
    def _check_4_currency_validation(self):
        """CHECK 4: Currency field validation."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 4: Currency Validation')
        
        # Check RbcMonthly currency (filtered by start_date)
        monthly_qs = self._filter_by_start_date(RbcMonthly.objects.all())
        invalid_monthly = monthly_qs.exclude(
            currency__in=['CAD', 'USD']
        ).count()
        
        if invalid_monthly > 0:
            self.result.add_error(
                'currency',
                f'{invalid_monthly} RbcMonthly records with invalid currency',
            )
        
        # Check RbcMonthlyBalance currency matches parent
        with connection.cursor() as cursor:
            if self.start_date:
                cursor.execute(
                    "SELECT COUNT(*) FROM core_rbcmonthlybalance rmb "
                    "JOIN core_rbcmonthly rm ON rmb.rbc_monthly_id = rm.id "
                    "WHERE rmb.currency != rm.currency AND rm.statement_date >= %s",
                    [self.start_date]
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM core_rbcmonthlybalance rmb "
                    "JOIN core_rbcmonthly rm ON rmb.rbc_monthly_id = rm.id "
                    "WHERE rmb.currency != rm.currency"
                )
            balance_mismatches = cursor.fetchone()[0]
        
        if balance_mismatches > 0:
            self.result.add_error(
                'currency',
                f'{balance_mismatches} RbcMonthlyBalance records with currency mismatch',
            )
        
        # Check RbcMonthlyTransaction currency matches parent
        with connection.cursor() as cursor:
            if self.start_date:
                cursor.execute(
                    "SELECT COUNT(*) FROM core_rbcmonthlytransaction rmt "
                    "JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id "
                    "WHERE rmt.currency != rm.currency AND rm.statement_date >= %s",
                    [self.start_date]
                )
            else:
                cursor.execute(
                    "SELECT COUNT(*) FROM core_rbcmonthlytransaction rmt "
                    "JOIN core_rbcmonthly rm ON rmt.rbc_monthly_id = rm.id "
                    "WHERE rmt.currency != rm.currency"
                )
            tx_mismatches = cursor.fetchone()[0]
        
        if tx_mismatches > 0:
            self.result.add_error(
                'currency',
                f'{tx_mismatches} RbcMonthlyTransaction records with currency mismatch',
            )
        
        if invalid_monthly == 0 and balance_mismatches == 0 and tx_mismatches == 0:
            self.result.add_ok('currency', 'All currencies valid and consistent')
    
    def _check_5_exchange_rate_validation(self):
        """CHECK 5: Exchange rate validation."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 5: Exchange Rate Validation')
        
        # Check USD statements with fx_rate = 1.000000 — REVERSE CHRONOLOGICAL
        bad_fx = self._filter_by_start_date(
            RbcMonthly.objects.filter(
                currency='USD',
                fx_rate=Decimal('1.000000'),
            ).select_related('account').order_by('-statement_date')
        )
        
        error_count = 0
        for stmt in bad_fx:
            self.result.add_error(
                'exchange_rate',
                f'{stmt.account.account_id} USD {stmt.statement_date}: fx_rate=1.000000 '
                f'(should be ~1.20-1.50 for USD)',
                {
                    'account': stmt.account.account_id,
                    'date': str(stmt.statement_date),
                    'fx_rate': str(stmt.fx_rate),
                }
            )
            error_count += 1
        
        # Check fx_rate out of reasonable range — REVERSE CHRONOLOGICAL
        out_of_range = self._filter_by_start_date(
            RbcMonthly.objects.filter(
                Q(fx_rate__lt=FX_RATE_MIN) | Q(fx_rate__gt=FX_RATE_MAX)
            ).select_related('account').order_by('-statement_date')
        )
        
        for stmt in out_of_range:
            self.result.add_warning(
                'exchange_rate',
                f'{stmt.account.account_id} {stmt.currency} {stmt.statement_date}: '
                f'fx_rate={stmt.fx_rate} (out of range {FX_RATE_MIN}-{FX_RATE_MAX})',
            )
        
        if error_count == 0 and out_of_range.count() == 0:
            self.result.add_ok('exchange_rate', 'All exchange rates within reasonable range')
        else:
            self.result.add_info('exchange_rate',
                f'Found {error_count} errors, {out_of_range.count()} warnings')
    
    def _check_6_account_matching(self):
        """CHECK 6: Account matching & duplicates."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 6: Account Matching & Duplicates')
        
        # Find duplicate accounts (same normalized ID)
        rbc_accounts = Account.objects.filter(broker='RBC')
        
        # Group by first 8 digits
        normalized_groups = {}
        for acc in rbc_accounts:
            digits = ''.join(c for c in acc.account_id if c.isdigit())
            normalized = digits[:8] if len(digits) >= 8 else digits
            
            if normalized not in normalized_groups:
                normalized_groups[normalized] = []
            normalized_groups[normalized].append(acc)
        
        duplicate_groups = {k: v for k, v in normalized_groups.items() if len(v) > 1}
        
        if duplicate_groups:
            for normalized, accounts in duplicate_groups.items():
                account_ids = [a.account_id for a in accounts]
                self.result.add_error(
                    'account_matching',
                    f'Duplicate accounts normalize to same ID {normalized}: {", ".join(account_ids)}',
                    {
                        'normalized_id': normalized,
                        'accounts': account_ids,
                    }
                )
        
        # Check for auto-created accounts
        auto_created = Account.objects.filter(
            broker='RBC',
            name__startswith='Auto-created',
        )
        
        if auto_created.exists():
            for acc in auto_created:
                self.result.add_warning(
                    'account_matching',
                    f'Auto-created account: {acc.account_id} ({acc.name})',
                )
        
        if not duplicate_groups and not auto_created.exists():
            self.result.add_ok('account_matching', 'All accounts unique and properly configured')
    
    def _check_7_transaction_integrity(self):
        """CHECK 7: Transaction data integrity."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 7: Transaction Integrity')
        
        # Filter transactions by start_date
        tx_qs = self._filter_by_start_date(
            RbcMonthlyTransaction.objects.select_related('rbc_monthly'),
            date_field='rbc_monthly__statement_date'
        )
        
        # Check for negative quantities
        neg_qty = tx_qs.filter(quantity__lt=0).count()
        if neg_qty > 0:
            self.result.add_error(
                'transaction_integrity',
                f'{neg_qty} transactions with negative quantity',
            )
        
        # Check for negative prices
        neg_price = RbcMonthlyTransaction.objects.filter(price__lt=0).count()
        if neg_price > 0:
            self.result.add_error(
                'transaction_integrity',
                f'{neg_price} transactions with negative price',
            )
        
        # Check for negative commissions
        neg_comm = RbcMonthlyTransaction.objects.filter(commission__lt=0).count()
        if neg_comm > 0:
            self.result.add_error(
                'transaction_integrity',
                f'{neg_comm} transactions with negative commission',
            )
        
        # Check for empty descriptions
        empty_desc = RbcMonthlyTransaction.objects.filter(
            description__exact=''
        ).count()
        if empty_desc > 0:
            self.result.add_warning(
                'transaction_integrity',
                f'{empty_desc} transactions with empty description',
            )
        
        if neg_qty == 0 and neg_price == 0 and neg_comm == 0 and empty_desc == 0:
            self.result.add_ok('transaction_integrity', 'All transactions valid')
    
    def _check_8_orphaned_records(self):
        """CHECK 8: Orphaned records."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 8: Orphaned Records')
        
        with connection.cursor() as cursor:
            # Orphaned balances
            cursor.execute("""
                SELECT COUNT(*) 
                FROM core_rbcmonthlybalance 
                WHERE rbc_monthly_id NOT IN (SELECT id FROM core_rbcmonthly)
            """)
            orphan_balances = cursor.fetchone()[0]
            
            # Orphaned transactions
            cursor.execute("""
                SELECT COUNT(*) 
                FROM core_rbcmonthlytransaction 
                WHERE rbc_monthly_id NOT IN (SELECT id FROM core_rbcmonthly)
            """)
            orphan_transactions = cursor.fetchone()[0]
            
            # Orphaned RbcMonthly (no account)
            cursor.execute("""
                SELECT COUNT(*) 
                FROM core_rbcmonthly 
                WHERE account_id NOT IN (SELECT id FROM core_account)
            """)
            orphan_monthly = cursor.fetchone()[0]
        
        if orphan_balances > 0:
            self.result.add_error(
                'orphaned_records',
                f'{orphan_balances} orphaned RbcMonthlyBalance records',
            )
        
        if orphan_transactions > 0:
            self.result.add_error(
                'orphaned_records',
                f'{orphan_transactions} orphaned RbcMonthlyTransaction records',
            )
        
        if orphan_monthly > 0:
            self.result.add_error(
                'orphaned_records',
                f'{orphan_monthly} orphaned RbcMonthly records',
            )
        
        if orphan_balances == 0 and orphan_transactions == 0 and orphan_monthly == 0:
            self.result.add_ok('orphaned_records', 'No orphaned records')
    
    def _check_9_market_value_trends(self):
        """CHECK 9: Market value trend analysis."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 9: Market Value Trend Analysis')
        
        accounts_currencies = RbcMonthly.objects.values_list(
            'account_id', 'currency'
        ).distinct()
        
        anomalies = 0
        
        for account_id, currency in accounts_currencies:
            # REVERSE CHRONOLOGICAL: latest dates first
            statements = RbcMonthly.objects.filter(
                account_id=account_id,
                currency=currency
            ).order_by('-statement_date')
            
            if statements.count() < 2:
                continue
            
            stmt_list = list(statements)
            account = stmt_list[0].account
            
            # Iterate in reverse chronological order (skip first, pair with next)
            for i in range(len(stmt_list) - 1):
                curr_mv = stmt_list[i].end_total_market_value     # More recent
                prev_mv = stmt_list[i + 1].end_total_market_value  # Less recent (previous in time)
                
                if prev_mv == 0:
                    continue
                
                # Calculate percentage change
                change_pct = abs(curr_mv - prev_mv) / prev_mv * 100
                
                # Check for negative values
                if curr_mv < 0:
                    self.result.add_error(
                        'market_trends',
                        f'{account.account_id} ({currency}) {stmt_list[i].statement_date}: '
                        f'negative market value ${curr_mv:,.2f}',
                    )
                    anomalies += 1
                
                # Check for extreme changes
                elif change_pct > 50:
                    self.result.add_error(
                        'market_trends',
                        f'{account.account_id} ({currency}) {stmt_list[i].statement_date}: '
                        f'market value changed {change_pct:.1f}% (from ${prev_mv:,.2f} to ${curr_mv:,.2f})',
                        {
                            'prev_value': str(prev_mv),
                            'curr_value': str(curr_mv),
                            'change_pct': str(round(change_pct, 1)),
                        }
                    )
                    anomalies += 1
                
                elif change_pct > TREND_CHANGE_PCT:
                    self.result.add_warning(
                        'market_trends',
                        f'{account.account_id} ({currency}) {stmt_list[i].statement_date}: '
                        f'market value changed {change_pct:.1f}% (unusual)',
                        {
                            'prev_value': str(prev_mv),
                            'curr_value': str(curr_mv),
                            'change_pct': str(round(change_pct, 1)),
                        }
                    )
                    anomalies += 1
        
        if anomalies == 0:
            self.result.add_ok('market_trends', 'All market value changes within normal range')
        else:
            self.result.add_info('market_trends', f'Found {anomalies} anomalies')
    
    def _check_10_data_completeness(self):
        """CHECK 10: Data completeness."""
        self.result.checks_run += 1
        self.printer.print_section('CHECK 10: Data Completeness')
        
        # Check RbcMonthly required fields
        missing_date = RbcMonthly.objects.filter(statement_date__isnull=True).count()
        missing_account = RbcMonthly.objects.filter(account__isnull=True).count()
        
        if missing_date > 0:
            self.result.add_error(
                'completeness',
                f'{missing_date} RbcMonthly records missing statement_date',
            )
        
        if missing_account > 0:
            self.result.add_error(
                'completeness',
                f'{missing_account} RbcMonthly records missing account',
            )
        
        # Check RbcAnnual required fields
        missing_year = RbcAnnual.objects.filter(report_year__isnull=True).count()
        
        if missing_year > 0:
            self.result.add_error(
                'completeness',
                f'{missing_year} RbcAnnual records missing report_year',
            )
        
        if missing_date == 0 and missing_account == 0 and missing_year == 0:
            self.result.add_ok('completeness', 'All required fields populated')
    
    def _print_final_report(self):
        """Print final audit summary."""
        self.printer.print_header('AUDIT SUMMARY')
        
        self.printer.print(f'\nChecks Run:           {self.result.checks_run}')
        self.printer.print(f'Errors Found:        {len(self.result.errors)}')
        self.printer.print(f'Warnings Found:      {len(self.result.warnings)}')
        self.printer.print(f'Info Messages:       {len(self.result.info)}')
        
        # Show error/warning breakdown by check type
        if self.result.errors or self.result.warnings:
            self.printer.print('\nBREAKDOWN BY CHECK TYPE')
            self.printer.print('-' * 40)
            
            # Group by check type
            error_counts = {}
            warning_counts = {}
            
            for error in self.result.errors:
                check_name = error['check']
                error_counts[check_name] = error_counts.get(check_name, 0) + 1
            
            for warning in self.result.warnings:
                check_name = warning['check']
                warning_counts[check_name] = warning_counts.get(check_name, 0) + 1
            
            # Print errors by type
            if error_counts:
                self.printer.print('\n  ERRORS:')
                for check_name, count in sorted(error_counts.items()):
                    self.printer.print(f'    {check_name:30s}: {count:>6,}')
            
            # Print warnings by type
            if warning_counts:
                self.printer.print('\n  WARNINGS:')
                for check_name, count in sorted(warning_counts.items()):
                    self.printer.print(f'    {check_name:30s}: {count:>6,}')
            
            self.printer.print(f'\n  NOTE: See markdown report for full details of all errors and warnings.')
        
        self.printer.print('\n' + '=' * 80)
        if self.result.has_errors:
            self.printer.print(
                f'AUDIT COMPLETE — {len(self.result.errors)} ERRORS, {len(self.result.warnings)} WARNINGS',
                'ERROR'
            )
        elif self.result.has_warnings:
            self.printer.print(
                f'AUDIT COMPLETE — {len(self.result.warnings)} WARNINGS',
                'WARNING'
            )
        else:
            self.printer.print('AUDIT COMPLETE — ALL CHECKS PASSED ✓', 'OK')
        self.printer.print('=' * 80)


class Command(BaseCommand):
    help = 'Audit RBC monthly data for integrity issues after imports.'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output with all fields.',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            dest='output_json',
            help='Output audit report in JSON format.',
        )
        parser.add_argument(
            '--md',
            action='store_true',
            dest='output_md',
            help='Generate markdown report file.',
        )
        parser.add_argument(
            '--output-dir',
            type=str,
            default='processed/audit',
            help='Directory for markdown/JSON reports (default: processed/audit).',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Auto-fix reversible issues (exchange rates, orphans).',
        )
        parser.add_argument(
            '--start-year',
            type=int,
            default=None,
            help='Only audit records from this year onwards (e.g., 2025). If not specified, audits all records.',
        )
    
    def handle(self, *args, **options):
        # Suppress WeasyPrint warnings
        import warnings
        warnings.filterwarnings('ignore', module='weasyprint')
        
        verbose = options['verbose']
        output_json = options['output_json']
        output_md = options['output_md']
        output_dir = options['output_dir']
        fix = options['fix']
        start_year = options['start_year']
        
        auditor = RBCDataAuditor(verbose=verbose, fix=fix, start_year=start_year)
        result = auditor.run()
        
        # Markdown report
        if output_md:
            report_path = self._generate_markdown_report(result, output_dir, start_year)
            print(f'\nMarkdown report saved to: {report_path}')
        
        # JSON output
        if output_json:
            json_output = {
                'audit_timestamp': datetime.now().isoformat(),
                'summary': {
                    'checks_run': result.checks_run,
                    'errors': len(result.errors),
                    'warnings': len(result.warnings),
                    'info': len(result.info),
                },
                'errors': result.errors,
                'warnings': result.warnings,
            }
            print('\n' + json.dumps(json_output, indent=2, default=str))
        
        sys.exit(result.exit_code())
    
    def _generate_markdown_report(self, result, output_dir, start_year=None):
        """Generate a markdown audit report file."""
        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Generate filename with timestamp and optional start-year
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        if start_year is not None:
            filename = f'RBC_pdf_import_audit_since_{start_year}_{timestamp}.md'
        else:
            filename = f'RBC_pdf_import_audit_{timestamp}.md'
        filepath = output_path / filename
        
        # Build markdown content
        lines = []
        lines.append('# RBC PDF Import Audit Report')
        lines.append('')
        lines.append(f'**Generated**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
        lines.append(f'**Database**: PortfolioDEV')
        if start_year is not None:
            lines.append(f'**Period**: Records from {start_year}-01-01 onwards')
        lines.append('')
        lines.append('---')
        lines.append('')
        
        # Summary
        lines.append('## Summary')
        lines.append('')
        lines.append(f'| Metric | Value |')
        lines.append(f'|--------|-------|')
        lines.append(f'| Checks Run | {result.checks_run} |')
        lines.append(f'| Errors | {len(result.errors)} |')
        lines.append(f'| Warnings | {len(result.warnings)} |')
        lines.append(f'| Passed | {result.ok_count} |')
        lines.append('')
        
        # Status
        if result.has_errors:
            lines.append(f'**Status**: ❌ **FAILED** — {len(result.errors)} errors found')
        elif result.has_warnings:
            lines.append(f'**Status**: ⚠️ **WARNINGS** — {len(result.warnings)} warnings found')
        else:
            lines.append(f'**Status**: ✅ **PASSED** — All checks passed')
        lines.append('')
        lines.append('---')
        lines.append('')
        
        # Error/Warning Breakdown
        lines.append('## Breakdown by Check Type')
        lines.append('')
        lines.append('| Check Type | Errors | Warnings |')
        lines.append('|------------|--------|----------|')
        
        # Group by check type
        error_counts = {}
        warning_counts = {}
        
        for error in result.errors:
            check_name = error['check']
            error_counts[check_name] = error_counts.get(check_name, 0) + 1
        
        for warning in result.warnings:
            check_name = warning['check']
            warning_counts[check_name] = warning_counts.get(check_name, 0) + 1
        
        # Get all check types
        all_checks = set(list(error_counts.keys()) + list(warning_counts.keys()))
        
        for check_name in sorted(all_checks):
            errors = error_counts.get(check_name, 0)
            warnings = warning_counts.get(check_name, 0)
            lines.append(f'| {check_name} | {errors} | {warnings} |')
        
        lines.append('')
        lines.append('---')
        lines.append('')
        
        # Database Statistics
        lines.append('## Database Statistics')
        lines.append('')
        lines.append(f'| Table | Count |')
        lines.append(f'|-------|-------|')
        lines.append(f'| RbcMonthly | {RbcMonthly.objects.count():,} |')
        lines.append(f'| RbcMonthlyBalance | {RbcMonthlyBalance.objects.count():,} |')
        lines.append(f'| RbcMonthlyTransaction | {RbcMonthlyTransaction.objects.count():,} |')
        lines.append(f'| RbcAnnual | {RbcAnnual.objects.count():,} |')
        lines.append(f'| RBC Accounts | {Account.objects.filter(broker="RBC").count()} |')
        lines.append('')
        lines.append('---')
        lines.append('')
        
        # Errors
        if result.errors:
            lines.append('## Errors')
            lines.append('')
            lines.append(f'**{len(result.errors)} errors found**')
            lines.append('')
            
            # Group by check
            errors_by_check = {}
            for error in result.errors:
                check = error['check']
                if check not in errors_by_check:
                    errors_by_check[check] = []
                errors_by_check[check].append(error)
            
            check_names = {
                'continuity': '1. Statement Continuity',
                'balance_sum': '2. Balance Sum Reconciliation',
                'annual_monthly': '3. Annual vs Monthly Cross-Check',
                'currency': '4. Currency Validation',
                'exchange_rate': '5. Exchange Rate Validation',
                'account_matching': '6. Account Matching & Duplicates',
                'transaction_integrity': '7. Transaction Integrity',
                'orphaned_records': '8. Orphaned Records',
                'market_trends': '9. Market Value Trend Analysis',
                'completeness': '10. Data Completeness',
            }
            
            for check_key in ['continuity', 'balance_sum', 'annual_monthly', 'currency',
                              'exchange_rate', 'account_matching', 'transaction_integrity',
                              'orphaned_records', 'market_trends', 'completeness']:
                if check_key in errors_by_check:
                    lines.append(f'### {check_names.get(check_key, check_key)}')
                    lines.append('')
                    for i, error in enumerate(errors_by_check[check_key][:20], 1):  # Limit to 20 per check
                        lines.append(f'{i}. {error["message"]}')
                        if error.get('details'):
                            for key, value in error['details'].items():
                                lines.append(f'   - **{key}**: `{value}`')
                    if len(errors_by_check[check_key]) > 20:
                        lines.append(f'   ... and {len(errors_by_check[check_key]) - 20} more')
                    lines.append('')
        
        # Warnings
        if result.warnings:
            lines.append('## Warnings')
            lines.append('')
            lines.append(f'**{len(result.warnings)} warnings found**')
            lines.append('')
            
            # Group by check
            warnings_by_check = {}
            for warning in result.warnings:
                check = warning['check']
                if check not in warnings_by_check:
                    warnings_by_check[check] = []
                warnings_by_check[check].append(warning)
            
            check_names = {
                'continuity': '1. Statement Continuity',
                'balance_sum': '2. Balance Sum Reconciliation',
                'annual_monthly': '3. Annual vs Monthly Cross-Check',
                'currency': '4. Currency Validation',
                'exchange_rate': '5. Exchange Rate Validation',
                'account_matching': '6. Account Matching & Duplicates',
                'transaction_integrity': '7. Transaction Integrity',
                'orphaned_records': '8. Orphaned Records',
                'market_trends': '9. Market Value Trend Analysis',
                'completeness': '10. Data Completeness',
            }
            
            for check_key in ['continuity', 'balance_sum', 'annual_monthly', 'currency',
                              'exchange_rate', 'account_matching', 'transaction_integrity',
                              'orphaned_records', 'market_trends', 'completeness']:
                if check_key in warnings_by_check:
                    lines.append(f'### {check_names.get(check_key, check_key)}')
                    lines.append('')
                    for i, warning in enumerate(warnings_by_check[check_key][:20], 1):  # Limit to 20 per check
                        lines.append(f'{i}. {warning["message"]}')
                        if warning.get('details'):
                            for key, value in warning['details'].items():
                                lines.append(f'   - **{key}**: `{value}`')
                    if len(warnings_by_check[check_key]) > 20:
                        lines.append(f'   ... and {len(warnings_by_check[check_key]) - 20} more')
                    lines.append('')
        
        # Passed checks
        if result.ok_count > 0:
            lines.append('## Passed Checks')
            lines.append('')
            lines.append(f'**{result.ok_count} checks passed**')
            lines.append('')
            for info in result.info:
                if info['severity'] == 'OK':
                    lines.append(f'- ✅ {info["message"]}')
            lines.append('')
        
        # Recommendations
        lines.append('---')
        lines.append('')
        lines.append('## Recommendations')
        lines.append('')
        if result.has_errors:
            lines.append('### Immediate Actions Required')
            lines.append('')
            if any(e['check'] == 'exchange_rate' for e in result.errors):
                lines.append('- **Exchange Rates**: USD statements with fx_rate=1.000000 need correction. Check parser configuration.')
            if any(e['check'] == 'balance_sum' for e in result.errors):
                lines.append('- **Balance Sums**: Reconcile balance discrepancies. Some statements may have missing balance records.')
            if any(e['check'] == 'continuity' for e in result.errors):
                lines.append('- **Continuity**: Review statement gaps. Missing statements may need to be re-imported.')
            if any(e['check'] == 'account_matching' for e in result.errors):
                lines.append('- **Accounts**: Resolve duplicate accounts. Merge or delete duplicate account records.')
        
        lines.append('')
        lines.append('### Preventive Measures')
        lines.append('')
        lines.append('- Run `audit_rbc_data` after every import to catch issues early')
        lines.append('- Review and fix parser issues before re-importing')
        lines.append('- Maintain statement continuity to ensure accurate reporting')
        lines.append('- Keep exchange rates up to date for USD statements')
        lines.append('')
        lines.append('---')
        lines.append('')
        lines.append(f'*Report generated by `python manage.py audit_rbc_data --md`*')
        
        # Write file
        content = '\n'.join(lines)
        filepath.write_text(content, encoding='utf-8')
        
        return str(filepath)
