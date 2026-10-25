"""Write ledger entries from Transaction records.

This command creates LedgerEntry records from core_transaction data.
It implements double-entry bookkeeping where each transaction creates
1-2 ledger entries (cash and/or position).

Usage:
    python manage.py write_ledger --account 90000001 --month 2024-01
    python manage.py write_ledger --account 90000001 --all-open
    python manage.py write_ledger --all-open          # all accounts
    python manage.py write_ledger --month 2024-01     # all accounts
"""
import logging
import os
import sys

import django
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\AI-DevTeam\projects\portfolio_manager')
django.setup()

from datetime import date, timedelta
from decimal import Decimal

from core.models import Account, LedgerAccount, LedgerEntry, LedgerMonthClosure, MonthlyLedgerBalance, Transaction

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Write ledger entries from Transaction records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--account',
            type=str,
            help='Account ID to process (e.g., 90000001). Omit to process all accounts.',
        )
        parser.add_argument(
            '--month',
            type=str,
            help='Month to process in YYYY-MM format (e.g., 2024-01)',
        )
        parser.add_argument(
            '--all-open',
            action='store_true',
            help='Process all open (non-closed) months for the account(s)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making changes',
        )

    def handle(self, *args, **options):
        account_id = options['account']
        month_str = options['month']
        all_open = options['all_open']
        dry_run = options['dry_run']

        if not all_open and not month_str:
            raise CommandError('Either --month or --all-open is required')

        # Determine which accounts to process
        if account_id:
            try:
                account = Account.objects.get(name=account_id)
            except Account.DoesNotExist:
                raise CommandError(f'Account {account_id} not found')
            accounts = [account]
        else:
            accounts = Account.objects.all()
            if not accounts.exists():
                raise CommandError('No accounts found')
            self.stdout.write(f"Processing all {accounts.count()} accounts\n")

        if all_open:
            for account in accounts:
                self.write_all_open_months(account, dry_run)
        else:
            try:
                month = date.fromisoformat(f'{month_str}-01')
            except ValueError:
                raise CommandError(f'Invalid month format: {month_str}. Use YYYY-MM')
            for account in accounts:
                self.write_month(account, month, dry_run)

    def write_all_open_months(self, account, dry_run):
        """Write ledger for all open (non-closed) months."""
        months = set()
        for tx in Transaction.objects.filter(account=account):
            month = tx.date.replace(day=1)
            months.add(month)

        months = sorted(months)

        open_months = []
        for month in months:
            for currency in ['CAD', 'USD']:
                closure = LedgerMonthClosure.objects.filter(
                    account=account,
                    statement_month=month,
                    currency=currency
                ).first()

                if not closure or not closure.is_closed:
                    open_months.append(month)
                    break

        self.stdout.write(f"Found {len(open_months)} open months to process")

        for month in open_months:
            self.write_month(account, month, dry_run)

    def write_month(self, account, month, dry_run):
        """Write ledger entries for a specific month."""
        self.stdout.write(f"\n{'='*70}")
        self.stdout.write(f"Processing: {account.name} - {month.strftime('%Y-%m')}")
        self.stdout.write(f"{'='*70}")

        # Check if month is closed
        for currency in ['CAD', 'USD']:
            closure = LedgerMonthClosure.objects.filter(
                account=account,
                statement_month=month,
                currency=currency
            ).first()

            if closure and closure.is_closed:
                self.stdout.write(f"  \u26a0 Month {month.strftime('%Y-%m')} {currency} is CLOSED - skipping")
                return

        # Get all transactions for this month
        month_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

        transactions = Transaction.objects.filter(
            account=account,
            date__gte=month,
            date__lte=month_end
        ).order_by('date')

        if not transactions.exists():
            self.stdout.write(f"  No transactions found for {month.strftime('%Y-%m')}")
            return

        self.stdout.write(f"  Found {transactions.count()} transactions")

        if dry_run:
            self.stdout.write('  [DRY RUN] Would create ledger entries')
            return

        # Process transactions
        entries_created = 0
        with transaction.atomic():
            for tx in transactions:
                entries_created += self.create_ledger_entries(tx, month)

        self.stdout.write(f"  \u2713 Created {entries_created} ledger entries")

        # Create monthly balance snapshot
        self.create_monthly_balance_snapshot(account, month)

    def create_ledger_entries(self, tx, statement_month):
        """Create ledger entries for a single transaction.

        Returns number of entries created.
        """
        entries_created = 0

        # Skip if entries already exist (idempotent re-run)
        if LedgerEntry.objects.filter(source_transaction=tx).exists():
            return 0

        tx_type = tx.type.upper()

        # Get or create cash ledger account
        cash_ledger, _ = LedgerAccount.objects.get_or_create(
            account=tx.account,
            account_type='CASH',
            currency=tx.currency,
            symbol='',
            defaults={
                'quantity': Decimal(0),
                'cost_basis_total': Decimal(0),
                'account_name': tx.account.name,
                'ledger_name': f"{tx.account.name}-{tx.currency}-CASH",
            }
        )

        # Handle different transaction types
        if tx_type in ['BUY', 'BOUGHT']:
            # BUY: CREDIT cash (out) + DEBIT position (in)
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"BUY {tx.symbol}"
            )
            entries_created += self._create_position_entry(
                tx, statement_month, 'DEBIT',
                tx.quantity, abs(tx.amount_cad), f"BUY {tx.symbol}"
            )

        elif tx_type in ['SELL', 'SOLD']:
            # SELL: DEBIT cash (in) + CREDIT position (out)
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), f"SELL {tx.symbol}"
            )
            entries_created += self._create_position_entry(
                tx, statement_month, 'CREDIT',
                tx.quantity, abs(tx.amount_cad), f"SELL {tx.symbol}"
            )

        elif tx_type in ['DIV', 'DIVIDEND', 'DIST', 'INT', 'INTEREST_RECEIVED', 'RETURN_OF_CAPITAL', 'WITHTAX']:
            # Income: DEBIT cash (in) + CREDIT income (revenue)
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), f"{tx_type} {tx.symbol}"
            )
            entries_created += self._create_income_entry(
                tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"{tx_type} {tx.symbol or ''}"
            )

        elif tx_type == 'TAX':
            # Tax withheld: CREDIT cash (out) + DEBIT tax expense
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"TAX {tx.notes[:80] if tx.notes else tx.symbol}"
            )
            # Create a tax expense ledger account
            tax_ledger, _ = LedgerAccount.objects.get_or_create(
                account=tx.account,
                account_type='TAX',
                currency=tx.currency,
                defaults={
                    'quantity': Decimal(0),
                    'cost_basis_total': Decimal(0),
                        'account_name': tx.account.name,
                    'ledger_name': f"{tx.account.name}-{tx.currency}-TAX",
                }
            )
            LedgerEntry.objects.create(
                ledger_account=tax_ledger,
                entry_date=tx.date,
                entry_type='DEBIT',
                cash_amount=abs(tx.amount_cad),
                quantity_change=Decimal(0),
                cost_basis_change=Decimal(0),
                source_transaction=tx,
                description=f"TAX {tx.notes[:80] if tx.notes else 'Tax Withheld'}",
                statement_month=statement_month,
                related_symbol=tx.symbol if tx.symbol else '',
            )
            tax_ledger.cost_basis_total += abs(tx.amount_cad)
            tax_ledger.save()

        elif tx_type in ['DEP', 'DEPOSIT', 'CONTRIB']:
            # Deposit: DEBIT cash (in)
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), f"{tx_type}"
            )

        elif tx_type in ['WDRAW', 'WITHDRAWAL', 'TFR OUT', 'WIRE TFR', 'TRANSFER']:
            # Withdrawal/transfer out: CREDIT cash (out)
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"{tx_type}"
            )

        elif tx_type == 'DIV REIN':
            # Dividend reinvestment: DEBIT position + CREDIT income (dividend used)
            entries_created += self._create_position_entry(
                tx, statement_month, 'DEBIT',
                tx.quantity, abs(tx.amount_cad), f"DIV REIN {tx.symbol}"
            )
            entries_created += self._create_income_entry(
                tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"DIV REIN {tx.symbol}"
            )

        elif tx_type == 'SPLIT':
            # Stock split: DEBIT position (quantity up, cost per share down)
            entries_created += self._create_position_entry(
                tx, statement_month, 'DEBIT',
                tx.quantity, Decimal(0), f"SPLIT {tx.symbol}"
            )

        elif tx_type in ['OTHER_FEE', 'FEE']:
            # Fee: cash out
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"{tx_type}"
            )

        elif tx_type in ['CREDIT_INTEREST', 'INTEREST_RECEIVED']:
            # Interest earned: DEBIT cash + CREDIT income
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), f"{tx_type}"
            )
            entries_created += self._create_income_entry(
                tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"{tx_type}"
            )

        elif tx_type in ['DEBIT_INTEREST', 'INTEREST_PAID']:
            # Interest paid: cash out
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f"{tx_type}"
            )

        elif tx_type == 'OTHER':
            # Other — treat as cash adjustment based on sign
            amount = abs(tx.amount_cad)
            desc = f"OTHER {tx.symbol}" if tx.symbol else ""
            if not desc and tx.notes:
                desc = f"OTHER {tx.notes[:40]}"
            elif not desc:
                desc = "OTHER"
            if amount > 0:
                entries_created += self._create_cash_entry(
                    cash_ledger, tx, statement_month, 'CREDIT',
                    amount, desc
                )
            else:
                entries_created += self._create_cash_entry(
                    cash_ledger, tx, statement_month, 'DEBIT',
                    amount, desc
                )

        elif tx_type == 'REORGANIZATION':
            # Corporate reorg has two parts:
            #   DIS (in-kind): qty > 0, amount = 0  → new share count after reorg
            #   CIL (cash in lieu): qty = 0, amount > 0 → fractional share paid in cash
            if tx.quantity > Decimal(0):
                # DIS: adjust position to new total quantity
                entries_created += self._create_reorg_entry(tx, statement_month)
            elif tx.amount_cad != Decimal(0):
                # CIL: cash in lieu of fractional shares → income
                entries_created += self._create_cash_entry(
                    cash_ledger, tx, statement_month, 'DEBIT',
                    abs(tx.amount_cad), f"CIL {tx.symbol}"
                )
                entries_created += self._create_income_entry(
                    tx, statement_month, 'CREDIT',
                    abs(tx.amount_cad), f"CIL {tx.symbol}"
                )
            else:
                logger.info(f"Skipped REORGANIZATION (no qty, no cash): tx {tx.id}")

        # ---- silent-skip types (bookkeeping, no cash effect) ----
        elif tx_type in ['ADJUSTMENT', 'ADJUST', 'RTC', 'FOREX_TRADE_COMPONEN', 'FOREX_TRADE_COMPONENT']:
            logger.info(f"Skipped: {tx_type} for tx {tx.id}")

        elif tx_type in ['N_A', '0', '0_00', '10_00', '5_00',
                         '1_USD___1_4187CAD', 'TRADES_THIS_MONTH']:
            logger.info(f"Skipped placeholder: {tx_type} for tx {tx.id}")

        # ---- catch-all for any stray type ----
        else:
            logger.warning(
                f"Unknown transaction type '{tx_type}' for tx {tx.id} — treating as cash adjustment"
            )
            amount = abs(tx.amount_cad)
            if tx_type in ['WDRAW', 'WITHDRAWAL', 'TFR OUT', 'WIRE TFR', 'TRANSFER']:
                entries_created += self._create_cash_entry(
                    cash_ledger, tx, statement_month, 'CREDIT',
                    amount, f"{tx_type}"
                )
            else:
                entries_created += self._create_cash_entry(
                    cash_ledger, tx, statement_month, 'DEBIT' if amount <= 0 else 'CREDIT',
                    amount, f"{tx_type}"
                )

        return entries_created

    def _create_cash_entry(self, cash_ledger, tx, statement_month, entry_type, amount, description):
        """Create a cash ledger entry and update ledger account balance."""
        LedgerEntry.objects.create(
            ledger_account=cash_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=amount if entry_type == 'DEBIT' else -amount,
            quantity_change=Decimal(0),
            cost_basis_change=Decimal(0),
            source_transaction=tx,
            description=description,
            statement_month=statement_month,
            related_symbol=tx.symbol if tx.symbol else '',
        )

        cash_ledger.save()
        return 1

    def _create_position_entry(self, tx, statement_month, entry_type, quantity, cost_basis, description):
        """Create a position ledger entry and update ledger account balance."""
        position_ledger, _ = LedgerAccount.objects.get_or_create(
            account=tx.account,
            account_type='POSITION',
            currency=tx.currency,
            symbol=tx.symbol,
            defaults={
                'quantity': Decimal(0),
                'cost_basis_total': Decimal(0),
                'account_name': tx.account.name,
                'ledger_name': f"{tx.account.name}-{tx.currency}-POSITION-{tx.symbol}",
            }
        )

        LedgerEntry.objects.create(
            ledger_account=position_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=Decimal(0),
            quantity_change=quantity if entry_type == 'DEBIT' else -quantity,
            cost_basis_change=cost_basis if entry_type == 'DEBIT' else -cost_basis,
            source_transaction=tx,
            description=description,
            statement_month=statement_month,
        )

        # Update ledger account balance
        if entry_type == 'DEBIT':
            position_ledger.quantity += quantity
            position_ledger.cost_basis_total += cost_basis
        else:
            position_ledger.quantity -= quantity
            position_ledger.cost_basis_total -= cost_basis

        # Recalculate average cost per unit
        if position_ledger.quantity > 0:
            position_ledger.cost_basis_per_unit = (
                position_ledger.cost_basis_total / position_ledger.quantity
            ).quantize(Decimal('0.0001'))
        else:
            position_ledger.cost_basis_per_unit = Decimal(0)

        position_ledger.save()
        return 1

    def _create_income_entry(self, tx, statement_month, entry_type, amount, description):
        """Create an income ledger entry (for dividends, interest, etc.)."""
        income_type = tx.type.upper()
        if income_type in ['DIV', 'DIVIDEND', 'DIST']:
            income_type = 'DIVIDEND'
        elif income_type in ['INT', 'INTEREST_RECEIVED']:
            income_type = 'INTEREST'
        elif income_type == 'RETURN_OF_CAPITAL':
            income_type = 'RETURN_OF_CAPITAL'

        income_ledger, _ = LedgerAccount.objects.get_or_create(
            account=tx.account,
            account_type='INCOME',
            currency=tx.currency,
            symbol=income_type,
            defaults={
                'quantity': Decimal(0),
                'cost_basis_total': Decimal(0),
                'account_name': tx.account.name,
                'ledger_name': f"{tx.account.name}-{tx.currency}-INCOME-{income_type}",
            }
        )

        LedgerEntry.objects.create(
            ledger_account=income_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=amount if entry_type == 'CREDIT' else -amount,
            quantity_change=Decimal(0),
            cost_basis_change=Decimal(0),
            source_transaction=tx,
            description=description,
            statement_month=statement_month,
            related_symbol=tx.symbol,
        )

        # Update ledger account balance (income is cumulative)
        if entry_type == 'CREDIT':
            income_ledger.cost_basis_total += amount
        else:
            income_ledger.cost_basis_total -= amount

        income_ledger.save()
        return 1

    def _create_reorg_entry(self, tx, statement_month):
        """Handle REORGANIZATION DIS: adjust position quantity to new total.
        
        tx.quantity = new total shares after reorg (not a delta).
        No cash effect — just a share count adjustment.
        Cost basis is preserved.
        """
        position_ledger, _ = LedgerAccount.objects.get_or_create(
            account=tx.account,
            account_type='POSITION',
            currency=tx.currency,
            symbol=tx.symbol,
            defaults={
                'quantity': Decimal(0),
                'cost_basis_total': Decimal(0),
                'account_name': tx.account.name,
                'ledger_name': f"{tx.account.name}-{tx.currency}-POSITION-{tx.symbol}",
            }
        )

        old_qty = position_ledger.quantity
        new_qty = tx.quantity

        # Distinguish: stock split (new total) vs stock dividend (increment)
        # tx.notes is always None — use ratio heuristic instead:
        #   split: new_qty / old_qty is close to a round number (2,3,4,10,20,30,40)
        #   dividend: small increment relative to old_qty
        COMMON_SPLITS = (2, 3, 4, 5, 10, 15, 20, 30, 40, 50)
        is_split = False
        abs_old = abs(old_qty)
        if abs_old > 0 and new_qty > 0:
            ratio = new_qty / abs_old
            for s in COMMON_SPLITS:
                if abs(ratio - s) < 0.2:
                    is_split = True
                    break
        # fallback: if notes text contains "SPLIT" (RBC CSV captures it)
        if not is_split and tx.notes:
            if 'SPLIT' in tx.notes.upper():
                is_split = True
            elif 'DIV ON' in tx.notes.upper():
                is_split = False  # explicitly a stock dividend
            elif 'CIL' in tx.notes.upper():
                is_split = False  # cash in lieu
            elif 'EXP' in tx.notes.upper():
                is_split = False  # option expiration
            elif 'DIV REIN' in tx.notes.upper():
                is_split = False
            elif 'TFR' in tx.notes.upper():
                is_split = False

        if is_split:
            # Stock split: tx.quantity = new total share count
            qty_change = new_qty - old_qty
            label = f"STK SPLIT {tx.symbol} ({old_qty:.0f} -> {new_qty:.0f})"
        else:
            # Stock dividend: tx.quantity = additional shares received
            qty_change = new_qty
            new_qty = old_qty + qty_change
            label = f"STK DIV {tx.symbol} ({old_qty:.0f} + {qty_change:.0f} = {new_qty:.0f})"

        # Preserve cost basis — reorg doesn't change total cost
        cb_change = Decimal(0)

        if qty_change == Decimal(0):
            logger.info(f"Skipped REORGANIZATION (no qty change): {tx.symbol} tx {tx.id}")
            return 0

        entry_type = 'DEBIT' if qty_change > 0 else 'CREDIT'

        LedgerEntry.objects.create(
            ledger_account=position_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=Decimal(0),
            quantity_change=qty_change,
            cost_basis_change=cb_change,
            source_transaction=tx,
            description=label,
            statement_month=statement_month,
        )

        # Update ledger account
        position_ledger.quantity = new_qty
        if position_ledger.quantity > 0:
            position_ledger.cost_basis_per_unit = (
                position_ledger.cost_basis_total / position_ledger.quantity
            ).quantize(Decimal('0.0001'))
        else:
            position_ledger.cost_basis_per_unit = Decimal(0)
        position_ledger.save()

        logger.info(f"REORGANIZATION {tx.symbol}: {old_qty:.0f} -> {new_qty:.0f} shares ({'split' if is_split else 'div'})")
        return 1

    def create_monthly_balance_snapshot(self, account, month):
        """Create MonthlyLedgerBalance snapshot for all ledger accounts."""
        ledger_accounts = LedgerAccount.objects.filter(account=account)

        for ledger in ledger_accounts:
            _, _ = MonthlyLedgerBalance.objects.update_or_create(
                ledger_account=ledger,
                statement_month=month,
                defaults={
                    'quantity': ledger.quantity,
                    'cost_basis_total': ledger.cost_basis_total,
                    'cost_basis_per_unit': ledger.cost_basis_per_unit,
                }
            )

        self.stdout.write('  Created monthly balance snapshot')
