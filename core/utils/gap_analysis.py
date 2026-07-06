"""Gap analysis utilities for transaction data completeness."""

from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Max, Min

from core.models import (
    Account,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyTransaction,
    Transaction,
)


def find_longest_continuous_run(dates):
    """Find the longest run of consecutive dates in a sorted list.

    Args:
        dates: Sorted list of date objects.

    Returns:
        Tuple of (start_date, end_date) or None if no dates.
    """
    if not dates:
        return None

    best_start = dates[0]
    best_end = dates[0]
    cur_start = dates[0]
    cur_end = dates[0]

    for d in dates[1:]:
        if d == cur_end + timedelta(days=1):
            cur_end = d
        else:
            if (cur_end - cur_start) > (best_end - best_start):
                best_start, best_end = cur_start, cur_end
            cur_start, cur_end = d, d

    if (cur_end - cur_start) > (best_end - best_start):
        best_start, best_end = cur_start, cur_end

    return (best_start, best_end)


def count_gaps_in_range(start, end, dates):
    """Count the number of days missing between start and end given a set of dates.

    Args:
        start: Start date.
        end: End date.
        dates: Set of existing dates.

    Returns:
        Number of days missing (not in the set).
    """
    if not start or not end:
        return 0

    total_days = (end - start).days + 1
    existing_in_range = sum(1 for d in dates if start <= d <= end)
    return total_days - existing_in_range


def count_months_with_transactions(tx_dates):
    """Count distinct months that have at least one transaction."""
    if not tx_dates:
        return 0
    months = set()
    for d in tx_dates:
        months.add((d.year, d.month))
    return len(months)


def count_months_without_transactions(tx_dates, start_date, end_date):
    """Count months in range with zero transactions."""
    if not tx_dates or not start_date or not end_date:
        return 0

    # Get all months in the range
    all_months = set()
    current = start_date.replace(day=1)
    while current <= end_date:
        all_months.add((current.year, current.month))
        # Next month
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1, day=1)
        else:
            current = current.replace(month=current.month + 1, day=1)

    # Get months with transactions
    tx_months = set()
    for d in tx_dates:
        tx_months.add((d.year, d.month))

    return len(all_months - tx_months)


def account_gap_analysis(account_id):
    """Return completeness metrics for a single account."""
    try:
        account = Account.objects.get(pk=account_id)
    except Account.DoesNotExist:
        return None

    # Get all transaction dates
    tx_dates_list = list(
        Transaction.objects.filter(account_id=account.account_id)
        .values_list("date", flat=True)
        .distinct()
        .order_by("date")
    )
    tx_dates_set = set(tx_dates_list)

    if not tx_dates_list:
        return {
            "account_id": account.account_id,
            "account_name": account.name,
            "earliest_csv_date": None,
            "continuous_start": None,
            "continuous_end": None,
            "days_missing_continuous": 0,
            "total_months_with_tx": 0,
            "total_months_without_tx": 0,
            "earliest_rbc_statement": None,
            "earliest_rbc_transaction": None,
            "earliest_rbc_annual": None,
            "csv_coverage_pct": 0,
        }

    earliest_csv = tx_dates_list[0]
    latest_csv = tx_dates_list[-1]

    # Find longest continuous run
    cr = find_longest_continuous_run(tx_dates_list)
    continuous_start = cr[0] if cr else None
    continuous_end = cr[1] if cr else None

    # Count gaps
    days_missing = (
        count_gaps_in_range(continuous_start, continuous_end, tx_dates_set) if cr else 0
    )

    # Months with/without transactions
    months_with = count_months_with_transactions(tx_dates_list)
    months_without = (
        count_months_without_transactions(tx_dates_list, earliest_csv, latest_csv)
        if latest_csv > earliest_csv
        else 0
    )

    # Coverage percentage
    total_months = months_with + months_without
    coverage_pct = (months_with / total_months * 100) if total_months > 0 else 0

    # RBC dates
    earliest_rbc_stmt = RbcMonthly.objects.filter(
        account_id=account.account_id
    ).aggregate(Min("statement_date"))["statement_date__min"]

    earliest_rbc_tx = RbcMonthlyTransaction.objects.filter(
        rbc_monthly__account_id=account.account_id
    ).aggregate(Min("date"))["date__min"]

    earliest_rbc_annual = RbcAnnual.objects.filter(
        account_id=account.account_id
    ).aggregate(Min("report_year"))["report_year__min"]

    return {
        "account_id": account.account_id,
        "account_name": account.name,
        "earliest_csv_date": earliest_csv,
        "continuous_start": continuous_start,
        "continuous_end": continuous_end,
        "days_missing_continuous": days_missing,
        "total_months_with_tx": months_with,
        "total_months_without_tx": months_without,
        "earliest_rbc_statement": earliest_rbc_stmt,
        "earliest_rbc_transaction": earliest_rbc_tx,
        "earliest_rbc_annual": earliest_rbc_annual,
        "csv_coverage_pct": round(coverage_pct, 1),
    }


def all_accounts_gap_analysis():
    """Return gap analysis for all accounts."""
    results = []
    for account in Account.objects.all():
        result = account_gap_analysis(account.account_id)
        if result:
            results.append(result)
    return results
