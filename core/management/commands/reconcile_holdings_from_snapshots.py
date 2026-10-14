"""Reconcile holdings and cash against known snapshots.

Walks forward from the first holdings snapshot for each account, replaying
all BUY/SELL transactions to produce monthly quantity snapshots and cash
tracking. Compares against RBC PDF holdings where available.

Usage:
    python manage.py reconcile_holdings_from_snapshots [--account ID] [--year 2026] [--output file.md]
"""

import logging
import os
from calendar import monthrange
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import Q

from core.models import (
    Account,
    PositionHistory,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    Transaction,
)

logger = logging.getLogger(__name__)


def _is_real_ticker(symbol):
    """Filter out CUSIPs and numeric symbols.

    CUSIPs can be 7 chars (stored in DB) or 9 chars (full format).
    """
    if not symbol:
        return False
    s = symbol.strip()
    # CUSIPs: 7 or 9 char alphanumeric starting with digit
    if len(s) in (7, 9) and s[0].isdigit():
        return False
    # Pure numeric (with optional dots/commas from corruption)
    if s.replace(".", "").replace(",", "").replace(" ", "").isdigit():
        return False
    # Options chains like "SPY 033121 400 C" — skip
    if " " in s and len(s) > 15:
        return False
    return True


class Command(BaseCommand):
    help = (
        "Reconcile holdings and cash from snapshots. Walks forward from "
        "first holdings snapshot, replays BUY/SELL transactions, "
        "produces monthly snapshots and compares against RBC PDF."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            type=int,
            help="Specific account ID (default: all).",
        )
        parser.add_argument(
            "--year",
            type=int,
            help="Year for report output (default: all years).",
        )
        parser.add_argument(
            "--output",
            type=str,
            help="Output file path (default: prints to stdout).",
        )

    def handle(self, *args, **options):
        account_id = options["account"]
        year_filter = options["year"]
        output_path = options["output"]

        accounts = Account.objects.all()
        if account_id:
            accounts = accounts.filter(pk=account_id)

        all_lines = []
        for account in accounts:
            all_lines.extend(self._process_account(account, year_filter))

        report = "\n".join(all_lines)

        if output_path:
            os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report)
            self.stdout.write(self.style.SUCCESS(f"Report written to {output_path}"))
        else:
            # Print summary only to stdout, full report to file if path given
            self.stdout.write(report[:3000])

    def _process_account(self, account, year_filter):
        """Process one account and return report lines."""
        acct_id = account.pk
        lines = []
        lines.append(f"# {account.name} ({acct_id}) — Holdings & Cash Reconciliation")
        lines.append("")

        # ---- STEP 1: Find first holdings snapshot as anchor ----
        earliest_ph = PositionHistory.objects.filter(
            account=account
        ).order_by("date_from").first()

        if not earliest_ph:
            lines.append("## No holdings data found for this account.")
            lines.append("")
            return lines

        # Filter out corrupted symbols for the anchor
        anchor_date = earliest_ph.date_from
        starting_positions = {}
        corrupted_count = 0
        for ph in PositionHistory.objects.filter(
            account=account, date_from=anchor_date
        ).order_by("symbol"):
            if not _is_real_ticker(ph.symbol):
                corrupted_count += 1
                continue
            if ph.quantity > 0:
                starting_positions[(ph.symbol, ph.currency)] = {
                    "qty": ph.quantity,
                    "cost": ph.cost_basis_per_unit,
                }

        if corrupted_count:
            lines.append(
                f"**Note:** Skipped {corrupted_count} corrupted records (numeric/CUSIP symbols)."
            )

        # Build flat starting positions dict (symbol, currency) -> qty (Decimal)
        start_qty = {}
        start_cost = {}
        for (sym, curr), info in starting_positions.items():
            start_qty[(sym, curr)] = info["qty"]
            start_cost[(sym, curr)] = info["cost"]

        lines.append(f"Anchor date: {anchor_date} (earliest holdings snapshot)")
        lines.append(f"Starting positions: {len(start_qty)} symbols")
        lines.append("")

        # Starting positions table
        lines.append("## Starting Positions")
        lines.append("")
        lines.append("| Ticker | Currency | Qty | Avg Cost |")
        lines.append("|--------|----------|-----|----------|")
        for (sym, curr) in sorted(start_qty.keys()):
            lines.append(
                f"| {sym} | {curr} | {start_qty[(sym, curr)]:,.4f} | {start_cost[(sym, curr)]} |"
            )

        # ---- STEP 2: Get cash starting balance from Dec 2025 PDF ----
        lines.append("")
        lines.append("## Starting Cash (Dec 2025 PDF)")
        lines.append("")
        cash_start = {}
        for curr in ["CAD", "USD"]:
            cb = (
                RbcMonthlyBalance.objects.filter(
                    rbc_monthly__account=account,
                    rbc_monthly__statement_date=date(2025, 12, 31),
                    currency=curr,
                    account_type__icontains="Cash",
                )
                .values_list("closing_balance", flat=True)
                .first()
            )
            cash_start[curr] = cb if cb else Decimal("0")
            lines.append(f"| {curr} | ${float(cash_start[curr]):,.2f} |")

        lines.append("")

        # ---- STEP 3: Determine date range ----
        last_tx = Transaction.objects.filter(
            account=account
        ).order_by("-date").first()
        end_date = last_tx.date if last_tx else date.today()

        if year_filter:
            start_check = date(year_filter, 1, 1)
        else:
            start_check = anchor_date

        # Get all months with transactions
        all_txs_cash = Transaction.objects.filter(
            account=account,
            date__gte=anchor_date,
            date__lte=end_date,
        ).order_by("date")

        all_month_keys = sorted(
            set((tx.date.year, tx.date.month) for tx in all_txs_cash)
        )
        month_end_dates = {}
        for y, m in all_month_keys:
            month_end_dates[(y, m)] = date(y, m, monthrange(y, m)[1])

        # ---- STEP 4: Replay transactions month-by-month ----
        lines.append("## Monthly Position Snapshots")
        lines.append("")
        lines.append(
            "(Replaying all BUY/SELL transactions from anchor date up to each month-end)"
        )
        lines.append("")

        # Collect all (symbol, currency) pairs from transactions in 2026
        all_sym_curr = set()
        tx_query = Transaction.objects.filter(
            account=account,
            date__gte=date(2026, 1, 1),
            date__lte=end_date,
            type__in=("BUY", "SELL"),
        )
        if year_filter:
            tx_query = tx_query.filter(
                date__gte=date(year_filter, 1, 1),
                date__lte=date(year_filter, 12, 31),
            )
        for sym, curr in tx_query.values_list("symbol", "currency").distinct():
            if _is_real_ticker(sym):
                all_sym_curr.add((sym, curr))
        # Also add starting positions
        for key in start_qty:
            if _is_real_ticker(key[0]):
                all_sym_curr.add(key)

        sym_curr_list = sorted(all_sym_curr, key=lambda x: (x[0], x[1]))

        # Process each month
        monthly_data = {}  # (year, month) -> {positions: {...}, cash: {curr: dec}}

        for month_key in sorted(month_end_dates.keys()):
            if year_filter and month_key[0] != year_filter:
                continue

            y, m = month_key
            month_end = month_end_dates[month_key]

            # Replay all BUY/SELL up to this month end
            qty_replay = dict(start_qty)
            for tx in Transaction.objects.filter(
                account=account,
                date__lte=month_end,
                date__gte=anchor_date,
                type__in=("BUY", "SELL"),
            ).order_by("date"):
                if not _is_real_ticker(tx.symbol):
                    continue
                if tx.currency not in ("CAD", "USD"):
                    continue
                key = (tx.symbol, tx.currency)
                if key in qty_replay:
                    delta = tx.quantity if tx.type == "BUY" else -tx.quantity
                    qty_replay[key] += delta
                elif tx.type == "BUY":
                    qty_replay[key] = tx.quantity

            # Replay cash from start of report year up to this month end
            # (cash_start is from Dec 2025 PDF, so only replay 2026+ transactions)
            report_year_start = date(year_filter, 1, 1) if year_filter else anchor_date
            cash_replay = dict(cash_start)
            for tx in Transaction.objects.filter(
                account=account,
                date__lte=month_end,
                date__gte=report_year_start,
            ).order_by("date"):
                if tx.currency in cash_replay:
                    cash_replay[tx.currency] += tx.amount_cad

            monthly_data[month_key] = {
                "positions": {
                    k: v for k, v in qty_replay.items() if v > 0
                },
                "cash_CAD": cash_replay.get("CAD", Decimal("0")),
                "cash_USD": cash_replay.get("USD", Decimal("0")),
            }

        # Build positions table
        if sym_curr_list:
            header = "| Month | " + " | ".join(f"{s} ({c})" for s, c in sym_curr_list) + " |"
            sep = "|-------|" + "|".join(["------"] * len(sym_curr_list)) + "|"
            lines.append(header)
            lines.append(sep)

            for month_key in sorted(monthly_data.keys()):
                year, month = month_key
                snap = monthly_data[month_key]
                row = f"| {year}-{month:02d} |"
                for sym, curr in sym_curr_list:
                    qty = snap["positions"].get((sym, curr), 0)
                    if qty and qty > 0:
                        row += f" {qty:,.0f} |"
                    else:
                        row += " — |"
                lines.append(row)
        else:
            lines.append("*No positions found.*")

        # Cash summary table
        lines.append("")
        lines.append("## Monthly Cash Summary")
        lines.append("")
        lines.append("| Month | Cash CAD | Cash USD |")
        lines.append("|-------|----------|----------|")
        for month_key in sorted(monthly_data.keys()):
            year, month = month_key
            snap = monthly_data[month_key]
            lines.append(
                f"| {year}-{month:02d} "
                f"| ${float(snap['cash_CAD']):,.2f} "
                f"| ${float(snap['cash_USD']):,.2f} |"
            )

        # ---- STEP 5: Compare against RBC PDF holdings ----
        lines.append("")
        lines.append("## Comparison with RBC PDF Holdings")
        lines.append("")

        # Get the latest RBC holdings for comparison
        latest_rbc = RbcMonthlyHolding.objects.filter(
            rbc_monthly__account=account
        ).order_by("-rbc_monthly__statement_date")

        # Find the most recent RBC statement per currency
        rbc_comparison = {}
        for curr in ["CAD", "USD"]:
            latest_stmt = (
                RbcMonthly.objects.filter(
                    account=account,
                    statement_date__lte=end_date,
                )
                .order_by("-statement_date")
                .first()
            )
            if latest_stmt is None:
                continue
            if latest_stmt:
                holdings = RbcMonthlyHolding.objects.filter(
                    rbc_monthly=latest_stmt,
                    currency=curr,
                ).values_list("symbol", "quantity")

                # Compare with computed positions at month end
                computed = monthly_data.get((latest_stmt.statement_date.year, latest_stmt.statement_date.month), {}).get("positions", {})

                if holdings:
                    rbc_comparison[curr] = {
                        "statement_date": latest_stmt.statement_date,
                        "holdings": dict(holdings),
                        "computed": {s: q for (s, c), q in computed.items() if c == curr and _is_real_ticker(s)},
                    }

        if rbc_comparison:
            for curr, data in rbc_comparison.items():
                lines.append(f"### {curr} — vs {data['statement_date']} PDF")
                lines.append("")
                lines.append("| Ticker | PDF Qty | Computed Qty | Match? |")
                lines.append("|--------|---------|-------------|--------|")

                all_tickers = set(list(data["computed"].keys()) + list(data["holdings"].keys()))
                for sym in sorted(all_tickers):
                    pdf_qty = data["holdings"].get(sym, "N/A")
                    comp_qty = data["computed"].get(sym, "N/A")

                    if pdf_qty == "N/A" and comp_qty == "N/A":
                        continue
                    if pdf_qty == "N/A":
                        match = "EXTRA in computed"
                    elif comp_qty == "N/A":
                        match = "EXTRA in PDF"
                    elif str(pdf_qty) == str(comp_qty):
                        match = "OK"
                    else:
                        match = f"MISMATCH (PDF={pdf_qty}, computed={comp_qty})"

                    lines.append(f"| {sym} | {pdf_qty} | {comp_qty} | {match} |")
                lines.append("")
        else:
            lines.append("*No RBC PDF holdings available for comparison.*")
            lines.append("")

        # Final positions
        final_month = max(monthly_data.keys()) if monthly_data else None
        if final_month:
            snap = monthly_data[final_month]
            lines.append("## Final Positions")
            lines.append("")
            lines.append("| Ticker | Currency | Qty |")
            lines.append("|--------|----------|-----|")
            for (sym, curr) in sorted(snap["positions"].keys()):
                qty = snap["positions"][(sym, curr)]
                if qty > 0 and _is_real_ticker(sym):
                    lines.append(f"| {sym} | {curr} | {qty:,.4f} |")
            lines.append("")
            lines.append(f"**Final Cash CAD:** ${float(snap['cash_CAD']):,.2f}")
            lines.append(f"**Final Cash USD:** ${float(snap['cash_USD']):,.2f}")
            lines.append("")

        return lines
