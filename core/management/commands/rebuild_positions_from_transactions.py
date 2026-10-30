"""PARKED (see docs/architecture.md §11)

Rebuild PositionHistory from Transaction activity.

Uses holdings snapshots to establish initial cost basis at the anchor date,
then replays ALL BUY/SELL transactions chronologically to build accurate
PositionHistory with proper date ranges and average cost basis.

Algorithm (per account + symbol):
1. Find the earliest holdings snapshot → anchor date + known quantity + known
   average cost basis
2. Replay ALL BUY/SELL transactions from anchor date forward:
   - BUY: qty += tx.quantity, avg_cost = weighted average of old + new
   - SELL: qty -= tx.quantity, avg_cost stays the same (FIFO/LIFO not needed,
     average cost basis stays constant on sell)
3. After each transaction, emit a new PositionHistory record with updated qty
   and avg cost
4. When qty hits 0, close the previous record (date_to = sell date)
5. Last record stays open (date_to IS NULL)

This gives you:
- Accurate date ranges (based on actual transaction dates, not sparse snapshots)
- Average cost basis (computed from transaction prices)
- Ability to query: "what was my ADX quantity and avg cost on 2024-06-15?"

Usage:
    python manage.py rebuild_positions_from_transactions [--dry-run] [--account ID]
"""

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import PositionHistory, Transaction, Account

logger = logging.getLogger(__name__)

QUANT_PREC = Decimal('0.0001')
COST_PREC = Decimal('0.0001')
TOTAL_PREC = Decimal('0.01')
PRICE_PREC = Decimal('0.0001')


class Command(BaseCommand):
    help = (
        "Rebuild PositionHistory from Transaction activity. "
        "Uses earliest holdings snapshot as anchor (qty + avg cost), "
        "then replays BUY/SELL transactions chronologically to build "
        "accurate date ranges and running average cost basis."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report changes without writing to the database.',
        )
        parser.add_argument(
            '--account',
            type=int,
            help='Rebuild for a specific account ID only.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        account_id = options['account']

        accounts = Account.objects.all()
        if account_id:
            accounts = accounts.filter(pk=account_id)
            if not accounts.exists():
                self.stderr.write(f"Account {account_id} not found.")
                return

        total = accounts.count()
        self.stdout.write(f"Processing {total} account(s)...\n")

        for i, account in enumerate(accounts, 1):
            self.stdout.write(
                f"--- {i}/{total}: {account.account_id} ({account.name}) ---"
            )
            self._rebuild_account(account, dry_run)
            self.stdout.write("")

        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — no changes written."))
        else:
            self.stdout.write(self.style.SUCCESS("Done."))

    # ------------------------------------------------------------------
    # Account-level
    # ------------------------------------------------------------------

    def _rebuild_account(self, account, dry_run):
        """Rebuild all PositionHistory for one account."""

        symbols = PositionHistory.objects.filter(
            account=account
        ).values_list('symbol', flat=True).distinct()

        if not symbols:
            self.stdout.write("  No symbols.")
            return

        all_new = []
        old_pks = []

        for symbol in symbols:
            result = self._rebuild_symbol(account, symbol)
            if result:
                all_new.extend(result['new'])
                old_pks.extend(result['old_pks'])

        if dry_run:
            self.stdout.write(
                f"  Would delete {len(old_pks)} old, create {len(all_new)} new."
            )
            for rec in all_new[:20]:
                status = "OPEN" if rec.date_to is None else f"closed {rec.date_to}"
                self.stdout.write(
                    f"    {rec.symbol} qty={rec.quantity} avg_cost={rec.cost_basis_per_unit} "
                    f"{status} from {rec.date_from}"
                )
        else:
            if old_pks:
                PositionHistory.objects.filter(pk__in=old_pks).delete()
                self.stdout.write(f"  Deleted {len(old_pks)} old records.")
            if all_new:
                PositionHistory.objects.bulk_create(all_new)
                self.stdout.write(f"  Created {len(all_new)} new records.")
            else:
                self.stdout.write("  No changes.")

    # ------------------------------------------------------------------
    # Per-symbol timeline
    # ------------------------------------------------------------------

    def _rebuild_symbol(self, account, symbol):
        """Rebuild PositionHistory for (account, symbol).

        Strategy:
        1. Find the earliest non-zero PositionHistory record → anchor with
           known quantity and cost basis
        2. Replay BUY/SELL transactions from anchor date forward
        3. Compute running quantity and weighted average cost after each trade
        4. Emit PositionHistory records for each quantity change
        5. Close position when qty hits 0

        Returns dict('new': [PositionHistory], 'old_pks': [int]) or None.
        """
        existing = list(
            PositionHistory.objects.filter(
                account=account, symbol=symbol
            ).order_by('date_from')
        )
        if not existing:
            return None

        old_pks = [r.pk for r in existing]

        # Find earliest non-zero record as anchor
        anchor = None
        for rec in existing:
            if rec.quantity > 0:
                anchor = rec
                break

        if anchor is None:
            return None

        # All BUY/SELL transactions from anchor date onward
        txs = list(
            Transaction.objects.filter(
                account=account,
                symbol=symbol,
                type__in=('BUY', 'SELL'),
                date__gte=anchor.date_from,
            ).order_by('date')
        )
        if not txs:
            return None

        # Replay transactions
        records = []
        running_qty = anchor.quantity
        running_avg_cost = anchor.cost_basis_per_unit

        # Emit anchor record
        total_cost = (running_qty * running_avg_cost).quantize(
            TOTAL_PREC, rounding=ROUND_HALF_UP
        )
        records.append(PositionHistory(
            owner=account.owner,
            account=account,
            symbol=symbol,
            quantity=running_qty.quantize(QUANT_PREC),
            cost_basis_per_unit=running_avg_cost.quantize(COST_PREC),
            total_cost_basis=total_cost,
            currency=anchor.currency,
            market_price=Decimal('0.0000'),
            market_value=Decimal('0.00'),
            date_from=anchor.date_from,
            date_to=None,
        ))

        for tx in txs:
            if tx.type == 'BUY':
                # New quantity = old qty + bought qty
                # New avg cost = (old_qty * old_cost + bought_qty * price) / new_qty
                old_total = running_qty * running_avg_cost
                new_purchase_total = tx.quantity * tx.price
                new_qty = running_qty + tx.quantity
                if new_qty > 0:
                    new_avg_cost = (old_total + new_purchase_total) / new_qty
                else:
                    new_avg_cost = running_avg_cost

            elif tx.type == 'SELL':
                # Avg cost stays the same on sell (FIFO not tracked,
                # average cost basis is preserved). Quantity is signed
                # (dry-run fix C) — a SELL of -10 reduces qty by 10.
                new_qty = running_qty - abs(tx.quantity)
                new_avg_cost = running_avg_cost
            else:
                continue

            if new_qty <= 0:
                # Position eliminated
                if records:
                    records[-1].date_to = tx.date
                break

            if new_qty != running_qty:
                # Quantity changed — close previous, start new record
                if records:
                    records[-1].date_to = tx.date

                new_total_cost = (new_qty * new_avg_cost).quantize(
                    TOTAL_PREC, rounding=ROUND_HALF_UP
                )
                records.append(PositionHistory(
                    owner=account.owner,
                    account=account,
                    symbol=symbol,
                    quantity=new_qty.quantize(QUANT_PREC),
                    cost_basis_per_unit=new_avg_cost.quantize(COST_PREC),
                    total_cost_basis=new_total_cost,
                    currency=anchor.currency,
                    market_price=Decimal('0.0000'),
                    market_value=Decimal('0.00'),
                    date_from=tx.date,
                    date_to=None,
                ))

            running_qty = new_qty
            running_avg_cost = new_avg_cost

        if not records:
            return None

        # Remove duplicate consecutive records with same quantity (same-day trades)
        records = self._deduplicate_consecutive(records)

        return {'new': records, 'old_pks': old_pks}

    def _deduplicate_consecutive(self, records):
        """Merge consecutive records with the same quantity and cost."""
        if len(records) <= 1:
            return records

        out = [records[0]]
        for rec in records[1:]:
            if rec.quantity == out[-1].quantity and rec.cost_basis_per_unit == out[-1].cost_basis_per_unit:
                # Same state — close previous at day before
                if rec.date_from > out[-1].date_from:
                    out[-1].date_to = rec.date_from - timedelta(days=1)
            else:
                out.append(rec)
        return out
