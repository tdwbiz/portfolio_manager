"""Monthly and annual statement generation from transaction data.

Provides functions to generate CoreMonthlyStatement and CoreAnnualReport
records from the master Transaction (CSV import) data.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.db.models.functions import ExtractYear

from core.models import (
    Account,
    CoreAnnualReport,
    CoreMonthlyStatement,
    RbcAnnual,
    RbcMonthly,
    Transaction,
)

logger = logging.getLogger(__name__)

# Transaction type groupings
BUY_TYPES = {"BUY", "DEP", "TFR-IN"}
SELL_TYPES = {"SELL", "WDR", "TFR-OUT"}
DIVIDEND_TYPES = {"DIV"}
INTEREST_TYPES = {"INT"}
COMMISSION_TYPES = {"COMM"}


def _month_start(d: date) -> date:
    """Return the first day of the month for a given date."""
    return d.replace(day=1)


def _month_end(d: date) -> date:
    """Return the last day of the month for a given date."""
    if d.month == 12:
        next_month = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_month = d.replace(month=d.month + 1, day=1)
    return next_month - timedelta(days=1)


def generate_monthly_statements(
    account: Account,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CoreMonthlyStatement]:
    """Generate monthly statements from transactions for the given account.

    For each month in the transaction range:
    1. Get all transactions for that month
    2. Sum buys, sells, dividends, interest, commissions
    3. Compute ending market value (cumulative from beginning value)
    4. Store or update in CoreMonthlyStatement

    Args:
        account: The Account to generate statements for.
        from_date: Optional start date (first transaction date to include).
        to_date: Optional end date (last transaction date to include).

    Returns:
        List of generated/updated CoreMonthlyStatement objects.
    """
    qs = Transaction.objects.filter(account=account)
    if from_date:
        qs = qs.filter(date__gte=from_date)
    if to_date:
        qs = qs.filter(date__lte=to_date)

    txs = qs.order_by("date", "currency")
    if not txs.exists():
        logger.info(f"No transactions for account {account} in range")
        return []

    # Group by (month_start, currency)
    from collections import defaultdict

    groups = defaultdict(list)
    for tx in txs:
        ms = _month_start(tx.date)
        groups[(ms, tx.currency)].append(tx)

    # Get all existing statements for this account for reference
    existing = CoreMonthlyStatement.objects.filter(account=account).order_by(
        "statement_month", "currency"
    )
    ending_values = {}  # (month_start, currency) -> ending_market_value
    for stmt in existing:
        ending_values[(stmt.statement_month, stmt.currency)] = stmt.ending_market_value

    generated = []
    sorted_keys = sorted(groups.keys())

    for (month_start, currency), month_txs in groups.items():
        # Accumulate signed sums per type (CSV sign convention: buys/wdrow/tax = negative, sells/div/dep = positive)
        total_buys = Decimal("0")
        total_sells = Decimal("0")
        total_dividends = Decimal("0")
        total_interest = Decimal("0")
        total_commission = Decimal("0")
        count = 0

        for tx in month_txs:
            count += 1
            amt = tx.amount_cad
            tx_type = tx.type

            if tx_type in BUY_TYPES:
                total_buys += amt  # negative in CSV
            elif tx_type in SELL_TYPES:
                total_sells += amt  # positive in CSV
            elif tx_type in DIVIDEND_TYPES:
                total_dividends += amt  # positive in CSV
            elif tx_type in INTEREST_TYPES:
                total_interest += amt  # positive in CSV
            elif tx_type in COMMISSION_TYPES:
                total_commission += amt  # negative in CSV

        # Net cash flow = sum of all signed amount_cad values
        # (buy/withdraw/tax are negative; sell/div/dep/interest are positive)
        # The commission is already included in amount_cad, so no need to add it again.
        net_flow = sum(tx.amount_cad for tx in month_txs)

        # Beginning MV: ending MV from previous month, or 0
        prev_month = month_start
        if month_start.month == 1:
            prev_month = month_start.replace(year=month_start.year - 1, month=12)
        else:
            prev_month = month_start.replace(month=month_start.month - 1)

        key = (prev_month, currency)
        beginning_mv = ending_values.get(key, Decimal("0"))

        # ending = beginning + net_flow
        # (net_flow is positive when money/income > money/out)
        ending_mv = beginning_mv + net_flow

        # Store absolute magnitudes for display (positive numbers)
        abs_buys = abs(total_buys)
        abs_commission = abs(total_commission)

        with transaction.atomic():
            stmt, created = CoreMonthlyStatement.objects.update_or_create(
                account=account,
                statement_month=month_start,
                currency=currency,
                defaults={
                    "beginning_market_value": beginning_mv,
                    "ending_market_value": ending_mv,
                    "total_buys": abs_buys,
                    "total_sells": total_sells,
                    "total_dividends": total_dividends,
                    "total_interest": total_interest,
                    "total_commission": abs_commission,
                    "net_flow": net_flow,
                    "total_transactions": count,
                },
            )

        ending_values[(month_start, currency)] = ending_mv
        generated.append(stmt)
        logger.info(
            f"Generated monthly statement: {account} {month_start} [{currency}] "
            f"begin={beginning_mv} end={ending_mv} buys={abs_buys} sells={total_sells} div={total_dividends} net={net_flow}"
        )

    return generated


def _prev_month(d: date) -> date:
    """Return the first day of the previous month."""
    if d.month == 1:
        return d.replace(year=d.year - 1, month=12, day=1)
    return d.replace(month=d.month - 1, day=1)


def reverse_generate_monthly_statements(
    account: Account,
    anchor_month: date,
    ending_balances: dict[str, Decimal] | None = None,
) -> list[CoreMonthlyStatement]:
    """Generate monthly statements going backwards from an anchor month.

    Uses known ending balances per currency at the anchor month and walks
    backwards through transaction data to compute beginning/ending values
    for each prior month.

    Args:
        account: The Account to generate statements for.
        anchor_month: First day of the anchor month (e.g. 2024-06-01).
            The ending balance at this month is provided via
            ending_balances. Statements are generated for the anchor
            month and all prior months that have transactions.
        ending_balances: Mapping of currency code to known ending balance
            at the anchor month end. E.g. {"CAD": Decimal("45000")}..
            If not provided, tries to use AccountCurrency.starting_balance
            for the earliest known currency or RbcMonthly data.

    Returns:
        List of generated/updated CoreMonthlyStatement objects.
    """
    from collections import defaultdict

    anchor_month_start = date(anchor_month.year, anchor_month.month, 1)

    # Get all transactions for this account
    txs = Transaction.objects.filter(account=account).order_by("date", "currency")
    if not txs.exists():
        logger.info(f"No transactions for account {account}")
        return []

    # Group by (month_start, currency)
    groups: dict[tuple[int, int, str], list[Transaction]] = defaultdict(list)
    all_months: set[tuple[int, int]] = set()
    all_currencies: set[str] = set()
    for tx in txs:
        ms = _month_start(tx.date)
        key = (ms.year, ms.month, tx.currency)
        groups[key].append(tx)
        all_months.add((ms.year, ms.month))
        all_currencies.add(tx.currency)

    if not all_months:
        return []

    min_year, min_month = min(all_months)
    max_year, max_month = max(all_months)

    # If ending_balances not provided, try to auto-detect
    if ending_balances is None:
        ending_balances = {}

        # Try RBC monthly data first (most reliable)
        from core.models import RbcMonthly

        for currency in all_currencies:
            rbc_latest = (
                RbcMonthly.objects.filter(account=account, currency=currency)
                .order_by("-statement_date")
                .first()
            )
            if rbc_latest and rbc_latest.end_total_market_value:
                ending_balances[currency] = rbc_latest.end_total_market_value

        # Fall back to AccountCurrency starting balance if no RBC data
        if not ending_balances:
            from core.models import AccountCurrency

            for ac in account.currencies.filter(currency__in=all_currencies):
                if ac.starting_balance and ac.starting_balance > 0:
                    ending_balances[ac.currency] = ac.starting_balance

    if not ending_balances:
        logger.warning(
            f"No ending balances found for account {account}. "
            f"Provide ending_balances or add RBC data."
        )
        return []

    # Determine the range of months to process
    # Walk backwards from anchor_month
    anchor_year, anchor_mon = anchor_month_start.year, anchor_month_start.month
    start_year, start_month = min_year, min_month

    # Build ordered list of months to process (backwards)
    months_to_process = []
    y, m = anchor_year, anchor_mon
    while (y, m) >= (start_year, start_month):
        months_to_process.append((y, m))
        if m == 1:
            y -= 1
            m = 12
        else:
            m -= 1

    generated = []
    # Track the ending MV for each currency as we walk backwards
    # At the anchor month, the ending balance is the known value.
    # The ending MV of month N-1 = beginning MV of month N.
    current_ending = dict(ending_balances)  # mutable copy

    for year, month in months_to_process:
        # For each currency that has transactions in this month
        for currency in sorted(all_currencies):
            if currency not in current_ending:
                # No known ending for this currency — skip
                continue

            key = (year, month, currency)
            month_txs = groups.get(key)

            if not month_txs:
                # No transactions this month for this currency.
                # If this is the anchor month and we have a known ending,
                # create a zero-activity statement.
                if (year == anchor_year and month == anchor_mon):
                    with transaction.atomic():
                        stmt, _ = CoreMonthlyStatement.objects.update_or_create(
                            account=account,
                            statement_month=date(year, month, 1),
                            currency=currency,
                            defaults={
                                "beginning_market_value": current_ending[currency],
                                "ending_market_value": current_ending[currency],
                                "total_buys": Decimal("0"),
                                "total_sells": Decimal("0"),
                                "total_dividends": Decimal("0"),
                                "total_interest": Decimal("0"),
                                "total_commission": Decimal("0"),
                                "net_flow": Decimal("0"),
                                "total_transactions": 0,
                                "ending_cash_balance": current_ending[currency],
                            },
                        )
                        generated.append(stmt)
                # For prior months with no transactions, the ending = beginning
                # and we carry the value forward. Set beginning = ending.
                beginning_mv = current_ending.get(currency)
                if beginning_mv is not None:
                    with transaction.atomic():
                        stmt, _ = CoreMonthlyStatement.objects.update_or_create(
                            account=account,
                            statement_month=date(year, month, 1),
                            currency=currency,
                            defaults={
                                "beginning_market_value": beginning_mv,
                                "ending_market_value": beginning_mv,
                                "total_buys": Decimal("0"),
                                "total_sells": Decimal("0"),
                                "total_dividends": Decimal("0"),
                                "total_interest": Decimal("0"),
                                "total_commission": Decimal("0"),
                                "net_flow": Decimal("0"),
                                "total_transactions": 0,
                                "ending_cash_balance": beginning_mv,
                            },
                        )
                        generated.append(stmt)
                    # ending for next iteration = beginning (no change)
                continue

            # Sum up signed transaction activities (CSV convention: buys=-, sells=+, div=+, int=+)
            total_buys = Decimal("0")
            total_sells = Decimal("0")
            total_dividends = Decimal("0")
            total_interest = Decimal("0")
            total_commission = Decimal("0")
            count = 0

            for tx in month_txs:
                count += 1
                amt = tx.amount_cad
                tx_type = tx.type

                if tx_type in BUY_TYPES:
                    total_buys += amt  # negative
                elif tx_type in SELL_TYPES:
                    total_sells += amt  # positive
                elif tx_type in DIVIDEND_TYPES:
                    total_dividends += amt  # positive
                elif tx_type in INTEREST_TYPES:
                    total_interest += amt  # positive
                elif tx_type in COMMISSION_TYPES:
                    total_commission += amt  # negative

            # Net cash flow = sum of all signed amount_cad values
            net_flow = sum(tx.amount_cad for tx in month_txs)

            # In reverse mode:
            # forward: ending = beginning + net_flow
            # reverse: beginning = ending - net_flow
            ending_mv = current_ending[currency]
            beginning_mv = ending_mv - net_flow

            # Store absolute magnitudes for display
            abs_buys = abs(total_buys)
            abs_commission = abs(total_commission)

            # Calculate ending cash balance for this month
            # Ending cash = beginning cash + net flow (all income and spending)
            # For reverse: ending = beginning + net flow (forward formula)
            # We need to track what happened this month to know ending cash
            # The simplest approach: ending cash = ending MV - position value
            # But we don't know position value per month, so we track cash separately
            # For now, use net flow as proxy for cash change
            ending_cash = current_ending.get(currency, Decimal("0")) + net_flow

            with transaction.atomic():
                stmt, created = CoreMonthlyStatement.objects.update_or_create(
                    account=account,
                    statement_month=date(year, month, 1),
                    currency=currency,
                    defaults={
                        "beginning_market_value": beginning_mv,
                        "ending_market_value": ending_mv,
                        "total_buys": abs_buys,
                        "total_sells": total_sells,
                        "total_dividends": total_dividends,
                        "total_interest": total_interest,
                        "total_commission": abs_commission,
                        "net_flow": net_flow,
                        "total_transactions": count,
                        "ending_cash_balance": ending_cash,
                    },
                )

            generated.append(stmt)
            logger.info(
                f"Reverse generated: {account} {year}-{month:02d} [{currency}] "
                f"begin={beginning_mv} end={ending_mv} cash={ending_cash}")

            # The beginning of THIS month = ending of PREVIOUS month
            # So for the next iteration (previous month), set current_ending
            current_ending[currency] = beginning_mv

    return generated


def batch_generate_all_statements(account: Account) -> tuple[list, list]:
    """Batch-generate monthly and annual statements anchored from RBC data.

    This is the primary entry point for generating all statements from scratch.
    It uses the latest RBC end_total_market_value per currency as the anchor,
    reverse-generates monthly statements backwards from that anchor, then
    generates annual reports from the monthly data.

    If no transactions exist, creates zero-activity placeholder statements
    for each month covered by RBC data.

    Args:
        account: The Account to generate statements for.

    Returns:
        Tuple of (list of monthly statements, list of annual reports).
    """
    from collections import defaultdict

    # Step 1: Get latest RBC end balances per currency for this account.
    # Use per-currency queries for database portability (distinct("currency")
    # is PostgreSQL-only; SQLite and SQL Server don't support it).
    currencies = (
        RbcMonthly.objects.filter(account=account)
        .values_list("currency", flat=True)
        .distinct()
    )
    rbc_latest = []
    for cur in currencies:
        latest = (
            RbcMonthly.objects.filter(account=account, currency=cur)
            .order_by("-statement_date")
            .first()
        )
        if latest:
            rbc_latest.append(latest)

    if not rbc_latest:
        logger.warning(
            f"No RBC data found for account {account}. "
            f"Cannot generate statements from RBC anchor."
        )
        return [], []

    # Build ending_balances dict from RBC data and find month range
    ending_balances = {}
    latest_month = None
    earliest_month = None
    for rbc in rbc_latest:
        ending_balances[rbc.currency] = rbc.end_total_market_value or Decimal("0")
        rbc_month_start = rbc.statement_date.replace(day=1)
        if latest_month is None or rbc.statement_date > latest_month:
            latest_month = rbc.statement_date
        if earliest_month is None or rbc_month_start < earliest_month:
            earliest_month = rbc_month_start

    if not ending_balances:
        logger.warning(
            f"No valid ending balances in RBC data for account {account}."
        )
        return [], []

    # Anchor at the latest RBC statement date
    anchor_month = latest_month.replace(day=1) if latest_month else date.today()

    logger.info(
        f"Batch generating statements for {account} anchored at {anchor_month} "
        f"with RBC balances: {ending_balances}"
    )

    # Step 2: Try reverse-generate from anchor
    monthly_stmts = reverse_generate_monthly_statements(
        account, anchor_month, ending_balances
    )

    # If no transactions exist, create placeholder statements for the RBC range
    if not monthly_stmts and earliest_month and ending_balances:
        logger.info(
            f"No transactions for {account} — creating placeholder statements "
            f"from {earliest_month} to {anchor_month}"
        )
        monthly_stmts = _create_placeholder_statements(
            account, earliest_month, anchor_month, ending_balances
        )

    logger.info(
        f"Batch: generated {len(monthly_stmts)} monthly statements for {account}"
    )

    # Step 3: Generate annual reports from monthly statements
    annual_reports = generate_annual_reports(account)
    logger.info(
        f"Batch: generated {len(annual_reports)} annual reports for {account}"
    )

    return monthly_stmts, annual_reports


def _create_placeholder_statements(
    account: Account,
    start_month: date,
    end_month: date,
    ending_balances: dict[str, Decimal],
) -> list[CoreMonthlyStatement]:
    """Create zero-activity placeholder monthly statements.

    Used when there are no transactions but we have RBC anchor data.
    Fills each month from start_month to end_month with a constant
    ending balance (no activity between months).

    Args:
        account: The Account to create statements for.
        start_month: First month to create (inclusive).
        end_month: Last month to create (inclusive).
        ending_balances: Known ending balances per currency at the end_month.

    Returns:
        List of created CoreMonthlyStatement objects.
    """
    generated = []

    # Walk forwards from start_month to end_month
    y, m = start_month.year, start_month.month
    while (y, m) <= (end_month.year, end_month.month):
        month_start = date(y, m, 1)
        for currency in sorted(ending_balances.keys()):
            balance = ending_balances[currency]
            with transaction.atomic():
                stmt, _ = CoreMonthlyStatement.objects.update_or_create(
                    account=account,
                    statement_month=month_start,
                    currency=currency,
                    defaults={
                        "beginning_market_value": balance,
                        "ending_market_value": balance,
                        "total_buys": Decimal("0"),
                        "total_sells": Decimal("0"),
                        "total_dividends": Decimal("0"),
                        "total_interest": Decimal("0"),
                        "total_commission": Decimal("0"),
                        "net_flow": Decimal("0"),
                        "total_transactions": 0,
                    },
                )
                generated.append(stmt)
        # Move to next month
        if m == 12:
            y += 1
            m = 1
        else:
            m += 1

    logger.info(
        f"Created {len(generated)} placeholder statements for {account} "
        f"from {start_month} to {end_month}"
    )
    return generated


def generate_annual_reports(
    account: Account,
    from_year: int | None = None,
    to_year: int | None = None,
) -> list[CoreAnnualReport]:
    """Generate annual reports from monthly statements for the given account.

    Aggregates CoreMonthlyStatement data into yearly summaries.
    If monthly statements don't exist for some months, falls back to raw transactions.

    Args:
        account: The Account to generate reports for.
        from_year: Optional start year.
        to_year: Optional end year.

    Returns:
        List of generated/updated CoreAnnualReport objects.
    """
    monthly_qs = CoreMonthlyStatement.objects.filter(account=account)

    # Use annotation to extract year (Django 5.x compatible, no .extra())
    from django.db.models.functions import ExtractYear

    year_months = (
        monthly_qs.annotate(year=ExtractYear("statement_month"))
        .values("year")
        .distinct()
        .order_by("year")
    )

    years = [int(r["year"]) for r in year_months]

    if from_year:
        years = [y for y in years if y >= from_year]
    if to_year:
        years = [y for y in years if y <= to_year]

    if not years:
        logger.info(f"No monthly statement data for account {account}")
        return []

    generated = []
    for year in years:
        monthly = (
            monthly_qs.filter(
                statement_month__year=year,
            )
            .values_list("statement_month", "currency", "currency")
            .order_by("currency", "statement_month")
        )

        # Aggregate by currency
        currency_totals = {}
        for row in monthly:
            cur = row[1]
            if cur not in currency_totals:
                currency_totals[cur] = {
                    "beginning_mv": Decimal("0"),
                    "ending_mv": Decimal("0"),
                    "buys": Decimal("0"),
                    "sells": Decimal("0"),
                    "dividends": Decimal("0"),
                    "interest": Decimal("0"),
                    "commission": Decimal("0"),
                    "transactions": 0,
                }

        # Get all monthly statements for this year
        year_monthly = monthly_qs.filter(statement_month__year=year)

        for cur in currency_totals:
            cur_monthly = year_monthly.filter(currency=cur).order_by("statement_month")
            if cur_monthly.exists():
                # Beginning = first month's beginning
                currency_totals[cur]["beginning_mv"] = (
                    cur_monthly.first().beginning_market_value
                )
                # Ending = last month's ending
                currency_totals[cur]["ending_mv"] = (
                    cur_monthly.last().ending_market_value
                )
                # Sums
                totals = cur_monthly.aggregate(
                    buys=Sum("total_buys"),
                    sells=Sum("total_sells"),
                    dividends=Sum("total_dividends"),
                    interest=Sum("total_interest"),
                    commission=Sum("total_commission"),
                    tx_count=Sum("total_transactions"),
                )
                currency_totals[cur]["buys"] = totals["buys"] or Decimal("0")
                currency_totals[cur]["sells"] = totals["sells"] or Decimal("0")
                currency_totals[cur]["dividends"] = totals["dividends"] or Decimal("0")
                currency_totals[cur]["interest"] = totals["interest"] or Decimal("0")
                currency_totals[cur]["commission"] = totals["commission"] or Decimal(
                    "0"
                )
                currency_totals[cur]["transactions"] = sum(
                    m.total_transactions for m in cur_monthly
                )

        for currency, totals in currency_totals.items():
            total_income = totals["dividends"] + totals["interest"]
            net_return = (totals["buys"] - totals["sells"]) + total_income
            amount_invested = totals["buys"] - totals["sells"]

            # Deposits = buys + dividends + interest
            deposits = totals["buys"] + totals["dividends"] + totals["interest"]
            # Withdrawals = sells
            withdrawals = totals["sells"]

            with transaction.atomic():
                report, created = CoreAnnualReport.objects.update_or_create(
                    account=account,
                    report_year=year,
                    currency=currency,
                    defaults={
                        "beginning_value": totals["beginning_mv"],
                        "ending_value": totals["ending_mv"],
                        "deposits": deposits,
                        "withdrawals": withdrawals,
                        "net_investment_return": net_return,
                        "amount_invested": amount_invested,
                        "total_buys": totals["buys"],
                        "total_sells": totals["sells"],
                        "total_income": total_income,
                        "total_commission": totals["commission"],
                        "total_transactions": totals["transactions"],
                    },
                )

            generated.append(report)
            logger.info(
                f"Generated annual report: {account} {year} [{currency}] "
                f"begin={totals['beginning_mv']} end={totals['ending_mv']} "
                f"deposits={deposits} withdrawals={withdrawals}"
            )

    return generated
