"""Reconciliation engine for CSV-generated vs RBC PDF data.

Compares CoreMonthlyStatement/CoreAnnualReport (CSV master) against
RbcMonthly/RbcAnnual (PDF import) to find matches and discrepancies.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum

from core.models import (
    Account,
    CoreAnnualReport,
    CoreMonthlyStatement,
    CoreReconciliation,
    RbcAnnual,
    RbcMonthly,
)

logger = logging.getLogger(__name__)

# Threshold for flagging discrepancies (anything above this is flagged)
DISCREPANCY_THRESHOLD = Decimal("1.00")


def _compare_decimals(a: Decimal | None, b: Decimal | None) -> tuple[bool, Decimal]:
    """Compare two Decimal values. Returns (is_match, absolute_difference)."""
    if a is None and b is None:
        return True, Decimal("0")
    if a is None or b is None:
        return False, Decimal("999999")
    diff = abs(a - b)
    return diff <= DISCREPANCY_THRESHOLD, diff


def reconcile_monthly(
    account: Account,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CoreReconciliation]:
    """Reconcile CSV-generated monthly statements against RBC PDF data.

    For each month that has a CoreMonthlyStatement, find the matching
    RbcMonthly statement and compare key values.

    Args:
        account: The Account to reconcile.
        from_date: Optional start date.
        to_date: Optional end date.

    Returns:
        List of CoreReconciliation records.
    """
    csv_statements = CoreMonthlyStatement.objects.filter(account=account)
    rbc_statements = RbcMonthly.objects.filter(account=account)

    if from_date:
        csv_statements = csv_statements.filter(statement_month__gte=from_date)
        rbc_statements = rbc_statements.filter(statement_date__gte=from_date)
    if to_date:
        csv_statements = csv_statements.filter(statement_month__lte=to_date)
        rbc_statements = rbc_statements.filter(statement_date__lte=to_date)

    # Build lookups by date and currency
    csv_lookup = {}
    for stmt in csv_statements:
        key = (stmt.statement_month, stmt.currency)
        csv_lookup[key] = stmt

    rbc_lookup = {}
    for rbc in rbc_statements:
        key = (rbc.statement_date, rbc.currency)
        rbc_lookup[key] = rbc

    # Collect all unique (date, currency) pairs from both sources
    all_keys = sorted(set(csv_lookup.keys()) | set(rbc_lookup.keys()))

    generated = []

    for period_date, currency in all_keys:
        stmt = csv_lookup.get((period_date, currency))
        rbc = rbc_lookup.get((period_date, currency))

        # CSV values (may be None if no CSV statement for this period)
        csv_begin = stmt.beginning_market_value if stmt else None
        csv_end = stmt.ending_market_value if stmt else None
        csv_buys = stmt.total_buys if stmt else None
        csv_sells = stmt.total_sells if stmt else None
        csv_income = (stmt.total_dividends + stmt.total_interest) if stmt else None
        csv_tx_count = stmt.total_transactions if stmt else None

        # RBC values (may be None if no RBC statement for this period)
        rbc_begin = rbc.begin_total_market_value if rbc else None
        rbc_end = rbc.end_total_market_value if rbc else None
        rbc_buys = None  # RBC monthly doesn't break out buys
        rbc_sells = None
        rbc_income = None
        rbc_tx_count = None

        # Overall match: both beginning and ending must match and both sources must exist
        is_matching = False
        if stmt and rbc:
            begin_match, begin_diff = _compare_decimals(csv_begin, rbc_begin)
            end_match, end_diff = _compare_decimals(csv_end, rbc_end)
            is_matching = begin_match and end_match

        # Total discrepancy
        _, begin_diff = _compare_decimals(csv_begin, rbc_begin)
        _, end_diff = _compare_decimals(csv_end, rbc_end)
        total_diff = begin_diff + end_diff

        notes_parts = []
        if not stmt:
            notes_parts.append("No CSV monthly statement for this period")
        if not rbc:
            notes_parts.append("No RBC statement for this period")
        if stmt and rbc:
            if not begin_match:
                notes_parts.append(
                    f"Beginning value differs: CSV={csv_begin}, RBC={rbc_begin}"
                )
            if not end_match:
                notes_parts.append(
                    f"Ending value differs: CSV={csv_end}, RBC={rbc_end}"
                )

        notes = "; ".join(notes_parts) if notes_parts else ""

        period_end = period_date + timedelta(days=31)

        with transaction.atomic():
            rec, created = CoreReconciliation.objects.update_or_create(
                account=account,
                reconciliation_type="monthly",
                period_start=period_date,
                period_end=period_end,
                defaults={
                    "csv_beginning_value": csv_begin,
                    "csv_ending_value": csv_end,
                    "csv_total_buys": csv_buys,
                    "csv_total_sells": csv_sells,
                    "csv_total_income": csv_income,
                    "csv_total_transactions": csv_tx_count,
                    "rbc_beginning_value": rbc_begin,
                    "rbc_ending_value": rbc_end,
                    "rbc_total_buys": rbc_buys,
                    "rbc_total_sells": rbc_sells,
                    "rbc_total_income": rbc_income,
                    "rbc_total_transactions": rbc_tx_count,
                    "matching": is_matching,
                    "discrepancy_amount": total_diff,
                    "discrepancy_notes": notes,
                    "reconciled_by": "auto",
                },
            )

        generated.append(rec)
        status = "MATCH" if is_matching else "DISCREPANCY"
        logger.info(
            f"Reconciled monthly: {account} {period_date} [{status}] "
            f"diff=${total_diff:.2f}"
        )

    return generated


def reconcile_annual(
    account: Account,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[CoreReconciliation]:
    """Reconcile CSV-generated annual reports against RBC PDF data.

    Args:
        account: The Account to reconcile.
        from_year: Optional start year.
        to_year: Optional end year.

    Returns:
        List of CoreReconciliation records.
    """
    csv_reports = CoreAnnualReport.objects.filter(account=account)
    rbc_reports = RbcAnnual.objects.filter(account=account)

    if from_year:
        csv_reports = csv_reports.filter(report_year__gte=from_year)
        rbc_reports = rbc_reports.filter(report_year__gte=from_year)
    if to_year:
        csv_reports = csv_reports.filter(report_year__lte=to_year)
        rbc_reports = rbc_reports.filter(report_year__lte=to_year)

    # Build lookups by year and currency
    csv_lookup = {}
    for report in csv_reports:
        key = (report.report_year, report.currency)
        csv_lookup[key] = report

    rbc_lookup = {}
    for rbc in rbc_reports:
        key = (rbc.report_year, rbc.currency)
        rbc_lookup[key] = rbc

    # Collect all unique (year, currency) pairs from both sources
    all_keys = sorted(set(csv_lookup.keys()) | set(rbc_lookup.keys()))

    generated = []

    for report_year, currency in all_keys:
        report = csv_lookup.get((report_year, currency))
        rbc = rbc_lookup.get((report_year, currency))

        # CSV values (may be None if no CSV report for this period)
        csv_begin = report.beginning_value if report else None
        csv_end = report.ending_value if report else None
        csv_buys = report.deposits if report else None
        csv_sells = report.withdrawals if report else None
        csv_income = report.total_income if report else None
        csv_tx_count = report.total_transactions if report else None

        # RBC values (may be None if no RBC report for this period)
        rbc_begin = rbc.bal_beginning_value if rbc else None
        rbc_end = rbc.bal_ending_value if rbc else None
        rbc_buys = rbc.bal_deposits_in if rbc else None
        rbc_sells = rbc.bal_withdrawals_out if rbc else None
        rbc_income = rbc.bal_net_return if rbc else None

        # Overall match: both sources must exist and values must agree
        is_matching = False
        if report and rbc:
            begin_match, begin_diff = _compare_decimals(csv_begin, rbc_begin)
            end_match, end_diff = _compare_decimals(csv_end, rbc_end)
            is_matching = begin_match and end_match
            _, buys_diff = _compare_decimals(csv_buys, rbc_buys)
            total_diff = begin_diff + end_diff + buys_diff
            _, sells_diff = _compare_decimals(csv_sells, rbc_sells)
            total_diff += sells_diff
            _, income_diff = _compare_decimals(csv_income, rbc_income)
            total_diff += income_diff
        else:
            _, begin_diff = _compare_decimals(csv_begin, rbc_begin)
            _, end_diff = _compare_decimals(csv_end, rbc_end)
            total_diff = begin_diff + end_diff

        notes_parts = []
        if not report:
            notes_parts.append("No CSV annual report for this period")
        if not rbc:
            notes_parts.append("No RBC annual report for this period")
        if report and rbc:
            if not begin_match:
                notes_parts.append(
                    f"Beginning value differs: CSV={csv_begin}, RBC={rbc_begin}"
                )
            if not end_match:
                notes_parts.append(
                    f"Ending value differs: CSV={csv_end}, RBC={rbc_end}"
                )

        notes = "; ".join(notes_parts) if notes_parts else ""

        period_start = date(report_year, 1, 1)
        period_end = date(report_year, 12, 31)

        with transaction.atomic():
            rec, created = CoreReconciliation.objects.update_or_create(
                account=account,
                reconciliation_type="annual",
                period_start=period_start,
                period_end=period_end,
                defaults={
                    "csv_beginning_value": csv_begin,
                    "csv_ending_value": csv_end,
                    "csv_total_buys": csv_buys,
                    "csv_total_sells": csv_sells,
                    "csv_total_income": csv_income,
                    "csv_total_transactions": csv_tx_count,
                    "rbc_beginning_value": rbc_begin,
                    "rbc_ending_value": rbc_end,
                    "rbc_total_buys": rbc_buys,
                    "rbc_total_sells": rbc_sells,
                    "rbc_total_income": rbc_income,
                    "rbc_total_transactions": None,
                    "matching": is_matching,
                    "discrepancy_amount": total_diff,
                    "discrepancy_notes": notes,
                    "reconciled_by": "auto",
                },
            )

        generated.append(rec)
        status = "MATCH" if is_matching else "DISCREPANCY"
        logger.info(
            f"Reconciled annual: {account} {report_year} [{status}] "
            f"diff=${total_diff:.2f}"
        )

    return generated


def reconcile_account(
    account: Account,
    period_type: str = "monthly",
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CoreReconciliation]:
    """Run full reconciliation for an account.

    Args:
        account: The Account to reconcile.
        period_type: 'monthly' or 'annual'.
        from_date: Optional start date (or year start for annual).
        to_date: Optional end date (or year end for annual).

    Returns:
        List of CoreReconciliation records.
    """
    if period_type == "annual":
        from_year = from_date.year if from_date else None
        to_year = to_date.year if to_date else None
        return reconcile_annual(account, from_year=from_year, to_year=to_year)
    else:
        return reconcile_monthly(account, from_date=from_date, to_date=to_date)
