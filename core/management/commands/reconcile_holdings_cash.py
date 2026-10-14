"""Reconcile PositionHistory quantities and cash against RBC PDF data.

Generates a comprehensive markdown report comparing:
1. **Security quantities** — PositionHistory (replayed from CSV transactions)
   vs RBC PDF holdings (RbcMonthlyHolding)
2. **Cash balances** — Running cash from Transaction amount_cad aggregation
   vs RBC PDF cash balance (RbcMonthlyBalance where account_type='Cash')

Scope: 2026 only (or specify --year).

For each (account, statement_date, currency):
- List all securities in the RBC PDF holding with their quantities
- Match against current PositionHistory quantity on that date
- Show any discrepancies (missing, extra, quantity mismatch)
- Compare cash balance from transactions vs PDF

Usage:
    python manage.py reconcile_holdings_cash [--year 2026] [--account ID] [--output report.md]
"""

import logging
import re
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from core.models import (
    Account,
    PositionHistory,
    RbcMonthly,
    RbcMonthlyHolding,
    RbcMonthlyBalance,
    Transaction,
)

logger = logging.getLogger(__name__)

DISCREPANCY_THRESHOLD_QTY = Decimal("0.01")
DISCREPANCY_THRESHOLD_CASH = Decimal("1.00")

# Patterns that indicate a fake/corrupted symbol (not a real ticker)
FAKE_SYMBOL_PATTERNS = [
    re.compile(r'^\d+\.?\d*$'),        # Pure numbers
    re.compile(r'^\d+\.\d+$'),         # Decimal numbers
]


def _is_fake_symbol(symbol):
    """Check if a symbol looks like a real ticker (not a corrupted value)."""
    if not symbol:
        return True
    for pattern in FAKE_SYMBOL_PATTERNS:
        if pattern.match(symbol):
            return True
    return False


class Command(BaseCommand):
    help = "Reconcile PositionHistory quantities and cash against RBC PDF data."

    def add_arguments(self, parser):
        parser.add_argument(
            '--year',
            type=int,
            default=2026,
            help='Year to reconcile (default: 2026).',
        )
        parser.add_argument(
            '--account',
            type=int,
            help='Reconcile specific account ID only.',
        )
        parser.add_argument(
            '--output',
            type=str,
            default=None,
            help='Write report to file instead of stdout.',
        )

    def handle(self, *args, **options):
        year = options['year']
        account_id = options['account']
        output_file = options['output']

        accounts = Account.objects.all()
        if account_id:
            accounts = accounts.filter(pk=account_id)
            if not accounts.exists():
                self.stderr.write(f"Account {account_id} not found.")
                return

        report_lines = []
        report_lines.append(f"# Holdings & Cash Reconciliation Report — {year}")
        report_lines.append("")
        report_lines.append(f"**Accounts:** {accounts.count()}  |  "
                            f"**Date range:** {year}-01-01 to {year}-12-31")
        report_lines.append("")
        report_lines.append("> Note: Positions with numeric/corrupted symbols are excluded.")
        report_lines.append("> RBC PDF data anomalies (e.g., sudden jumps in share counts) are flagged.")
        report_lines.append("")

        total_accounts = accounts.count()
        for i, account in enumerate(accounts, 1):
            self.stdout.write(f"  [{i}/{total_accounts}] {account.account_id} ({account.name})...")
            lines = self._reconcile_account(account, year)
            report_lines.append("")
            report_lines.append("---")
            report_lines.append("")
            report_lines.extend(lines)

        report = "\n".join(report_lines)

        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(report)
            self.stdout.write(f"\nReport written to {output_file}")
        else:
            print(report)

    def _reconcile_account(self, account, year):
        """Generate reconciliation lines for one account."""
        lines = []
        lines.append(f"## {account.account_id} — {account.name} "
                     f"({account.owner}/{account.category})")
        lines.append("")

        # Get RBC statements for this account in the target year
        rbc_statements = RbcMonthly.objects.filter(
            account=account,
            statement_date__year=year,
        ).order_by('statement_date', 'currency')

        if not rbc_statements:
            lines.append("*No RBC PDF statements found for this year.*")
            return lines

        lines.append(f"**RBC statements found:** {rbc_statements.count()}")
        lines.append("")

        # Find earliest holdings snapshot date per symbol
        earliest_snapshots = {}
        for key in PositionHistory.objects.filter(
            account=account
        ).values_list('symbol', flat=True).distinct():
            pos = PositionHistory.objects.filter(
                account=account, symbol=key
            ).order_by('date_from').first()
            if pos:
                earliest_snapshots[key] = pos.date_from

        if earliest_snapshots:
            earliest_date = min(earliest_snapshots.values())
            latest_date = max(earliest_snapshots.values())
            lines.append(f"**PositionHistory anchor:** earliest snapshot = {earliest_date} "
                         f"(last snapshot = {latest_date})")
            lines.append(f"**Note:** Positions before the earliest snapshot may not have "
                         f"accurate starting quantities.")
            lines.append(f"**Symbols tracked:** {len(earliest_snapshots)}")
            lines.append("")

        # Group RBC statements by (date, currency)
        rbc_by_date = {}
        for stmt in rbc_statements:
            key = (stmt.statement_date, stmt.currency)
            rbc_by_date[key] = stmt

        total_qty_issues = 0
        total_cash_issues = 0

        for (stmt_date, currency), stmt in sorted(rbc_by_date.items()):
            lines.append(f"### {stmt_date.strftime('%Y-%m-%d')} — {currency}")
            lines.append("")

            # --- 1. HOLDINGS RECONCILIATION ---
            qty_issues = self._reconcile_holdings(lines, account, stmt_date, currency)
            total_qty_issues += qty_issues

            # --- 2. CASH RECONCILIATION ---
            cash_issues = self._reconcile_cash(lines, account, stmt_date, currency)
            total_cash_issues += cash_issues

            lines.append("")

        # Summary
        lines.append(f"**Summary for {account.account_id}:** "
                     f"{total_qty_issues} quantity discrepancy(ies), "
                     f"{total_cash_issues} cash discrepancy(ies)")
        lines.append("")

        return lines

    def _reconcile_holdings(self, lines, account, stmt_date, currency):
        """Reconcile security quantities. Returns count of discrepancies."""
        # Get RBC PDF holdings for this statement
        pdf_holdings = RbcMonthlyHolding.objects.filter(
            rbc_monthly__account=account,
            rbc_monthly__statement_date=stmt_date,
            rbc_monthly__currency=currency,
            quantity__gt=0,
        ).order_by('symbol')

        # Filter out corrupted entries
        pdf_holdings = [h for h in pdf_holdings if not _is_fake_symbol(h.symbol)]

        if not pdf_holdings:
            lines.append("| *No valid holdings in RBC PDF for this date/currency* |")
            lines.append("")
            return 0

        # Table header
        lines.append("| # | Ticker | RBC PDF Qty | Our Qty | Diff | Status |")
        lines.append("|---|--------|-------------|---------|------|--------|")

        qty_discrepancies = 0
        # Track last known quantities per symbol for anomaly detection
        last_qty = {}

        for holding in pdf_holdings:
            pdf_qty = holding.quantity
            our_qty = self._get_position_qty(account, holding.symbol, stmt_date, currency)

            # Anomaly detection: sudden jump >10x or drop >90%
            prev_qty = last_qty.get(holding.symbol)
            is_anomaly = False
            if prev_qty is not None and prev_qty > 0:
                ratio = float(pdf_qty / prev_qty) if prev_qty > 0 else 0
                if ratio > 10 or ratio < 0.1:
                    is_anomaly = True

            last_qty[holding.symbol] = pdf_qty

            if our_qty is None:
                # No position in our data
                diff = pdf_qty
                status = "❌ MISSING"
                if is_anomaly:
                    status = "⚠️ PDF ANOMALY"
                qty_discrepancies += 1
            else:
                diff = pdf_qty - our_qty
                if abs(diff) <= DISCREPANCY_THRESHOLD_QTY:
                    status = "✅"
                elif is_anomaly:
                    status = f"⚠️ PDF ANOMALY"
                    qty_discrepancies += 1
                else:
                    status = f"⚠️ diff={float(diff):.2f}"
                    qty_discrepancies += 1

            our_qty_str = (
                f"{float(our_qty):,.4f}"
                if our_qty is not None else "N/A"
            )
            lines.append(
                f"| — | **{holding.symbol}** | "
                f"{float(pdf_qty):,.4f} | "
                f"{our_qty_str} | "
                f"{float(diff):,.4f} | {status} |"
            )

        lines.append("")

        # Check for positions in our data that are NOT in RBC PDF
        pdf_symbols = {h.symbol for h in pdf_holdings}

        # Filter out fake symbols from our data
        valid_symbols = [s for s in pdf_symbols if not _is_fake_symbol(s)]

        our_positions = list(PositionHistory.objects.filter(
            account=account,
            date_from__lte=stmt_date,
        ).filter(
            date_to__gte=stmt_date,
            currency=currency,
            symbol__in=valid_symbols if valid_symbols else [],
        ).filter(
            date_to__isnull=True,
        ).exclude(symbol__in=pdf_symbols) if valid_symbols else [])

        extra_in_ours = 0
        for pos in our_positions:
            if pos.quantity > 0:
                lines.append(
                    f"| ⚠️ | **{pos.symbol}** (in our data, not in PDF) | "
                    f"N/A | {float(pos.quantity):,.4f} | — | "
                    f"Position exists in transactions but not in PDF snapshot |"
                )
                extra_in_ours += 1

        if extra_in_ours > 0:
            lines.append("")

        return qty_discrepancies

    def _get_position_qty(self, account, symbol, as_of_date, currency):
        """Get the position quantity for a symbol on a specific date."""
        pos = PositionHistory.objects.filter(
            account=account,
            symbol=symbol,
            currency=currency,
            date_from__lte=as_of_date,
        ).filter(
            date_to__gte=as_of_date,
        ).order_by('-date_from').first()

        if pos and pos.quantity > 0:
            return pos.quantity

        # Also check open positions (date_to IS NULL)
        pos = PositionHistory.objects.filter(
            account=account,
            symbol=symbol,
            currency=currency,
            date_from__lte=as_of_date,
            date_to__isnull=True,
        ).order_by('-date_from').first()

        if pos and pos.quantity > 0:
            return pos.quantity
        return None

    def _reconcile_cash(self, lines, account, stmt_date, currency):
        """Reconcile cash balance. Returns count of discrepancies."""
        # Get PDF cash balance
        cash_balance = RbcMonthlyBalance.objects.filter(
            rbc_monthly__account=account,
            rbc_monthly__statement_date=stmt_date,
            rbc_monthly__currency=currency,
            account_type__icontains='Cash',
        ).first()

        pdf_cash = cash_balance.closing_balance if cash_balance else None

        # Calculate running cash from transactions up to stmt_date
        our_cash = self._calculate_running_cash(account, currency, stmt_date)

        lines.append("| Item | RBC PDF | Our Data | Diff | Status |")
        lines.append("|------|---------|----------|------|--------|")

        if pdf_cash is None and our_cash is None:
            lines.append("| Cash | N/A | N/A | — | No cash data available |")
            lines.append("")
            return 0

        diff = (pdf_cash or Decimal('0')) - (our_cash or Decimal('0'))
        if abs(diff) <= DISCREPANCY_THRESHOLD_CASH:
            status = "✅"
            issues = 0
        else:
            status = f"⚠️ diff=${float(diff):,.2f}"
            issues = 1

        pdf_str = f"${float(pdf_cash):,.2f}" if pdf_cash is not None else "N/A"
        our_str = f"${float(our_cash):,.2f}" if our_cash is not None else "N/A"

        lines.append(
            f"| Cash Balance | {pdf_str} | {our_str} | "
            f"${float(diff):,.2f} | {status} |"
        )
        lines.append("")

        return issues

    def _calculate_running_cash(self, account, currency, as_of_date):
        """Calculate running cash balance from transactions up to a date.

        Cash affects:
        - BUY: negative (money out)
        - SELL: positive (money in)
        - DEP/CONTRIB: positive (money in)
        - WDRAW: negative (money out)
        - DIV/DIST/INT: positive (income)
        - TAX: negative (tax withheld)
        - FEE: negative (fees)
        """
        txs = Transaction.objects.filter(
            account=account,
            currency=currency,
            date__lte=as_of_date,
            type__in=(
                'BUY', 'SELL', 'DEP', 'WDRAW', 'DIV', 'DIST', 'INT',
                'TAX', 'FEE', 'TRANSFER', 'CONTRIB', 'DEPOSIT',
                'WITHDRAWAL', 'TFR OUT', 'WIRE TFR',
            ),
        )

        total = txs.aggregate(
            total=Sum('amount_cad', default=Decimal('0'))
        )['total']

        return total
