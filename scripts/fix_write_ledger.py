# This script fixes write_ledger.py by writing the correct content
import os

content = '''import os
import sys
import django
import logging
from datetime import date, timedelta
from decimal import Decimal
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portfolio_manager.settings')
sys.path.insert(0, r'C:\\AI-DevTeam\\projects\\portfolio_manager')
django.setup()

from core.models import (
    Account, Transaction, LedgerAccount, LedgerEntry,
    MonthlyLedgerBalance, LedgerMonthClosure
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Write ledger entries from Transaction records'

    def add_arguments(self, parser):
        parser.add_argument('--account', type=str, help='Account ID')
        parser.add_argument('--month', type=str, help='Month YYYY-MM')
        parser.add_argument('--all-open', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        account_id = options['account']
        month_str = options['month']
        all_open = options['all_open']
        dry_run = options['dry_run']

        if not account_id:
            raise CommandError('--account is required')

        try:
            account = Account.objects.get(account_id=account_id)
        except Account.DoesNotExist:
            raise CommandError(f'Account {account_id} not found')

        if all_open:
            self.write_all_open_months(account, dry_run)
        elif month_str:
            try:
                month = date.fromisoformat(f'{month_str}-01')
            except ValueError:
                raise CommandError(f'Invalid month format: {month_str}. Use YYYY-MM')
            self.write_month(account, month, dry_run)
        else:
            raise CommandError('Either --month or --all-open is required')

    def write_all_open_months(self, account, dry_run):
        months = set()
        for tx in Transaction.objects.filter(account=account):
            month = tx.date.replace(day=1)
            months.add(month)
        months = sorted(months)
        open_months = []
        for month in months:
            is_closed = False
            for currency in ['CAD', 'USD']:
                closure = LedgerMonthClosure.objects.filter(
                    account=account, statement_month=month, currency=currency
                ).first()
                if closure and closure.is_closed:
                    is_closed = True
                    break
            if not is_closed:
                open_months.append(month)
        self.stdout.write(f'Found {len(open_months)} open months')
        for month in open_months:
            self.write_month(account, month, dry_run)

    def write_month(self, account, month, dry_run):
        self.stdout.write(f'\\nProcessing: {account.account_id} - {month.strftime("%Y-%m")}')
        for currency in ['CAD', 'USD']:
            closure = LedgerMonthClosure.objects.filter(
                account=account, statement_month=month, currency=currency
            ).first()
            if closure and closure.is_closed:
                self.stdout.write(f'  Month {month.strftime("%Y-%m")} {currency} is CLOSED')
                return
        month_end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        transactions = Transaction.objects.filter(
            account=account, date__gte=month, date__lte=month_end
        ).order_by('date')
        if not transactions.exists():
            self.stdout.write(f'  No transactions for {month.strftime("%Y-%m")}')
            return
        self.stdout.write(f'  Found {transactions.count()} transactions')
        if dry_run:
            self.stdout.write('  [DRY RUN] Would create ledger entries')
            return
        entries_created = 0
        with db_transaction.atomic():
            for tx in transactions:
                entries_created += self.create_ledger_entries(tx, month)
        self.stdout.write(f'  Created {entries_created} ledger entries')
        self.create_monthly_balance_snapshot(account, month)

    def create_ledger_entries(self, tx, statement_month):
        entries_created = 0
        tx_type = tx.type.upper()
        cash_ledger, _ = LedgerAccount.objects.get_or_create(
            account=tx.account, account_type='CASH',
            currency=tx.currency, symbol='',
            defaults={'quantity': Decimal('0'), 'cost_basis_total': Decimal('0')}
        )
        if tx_type in ['BUY', 'BOUGHT']:
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f'BUY {tx.symbol}'
            )
            entries_created += self._create_position_entry(
                tx, statement_month, 'DEBIT',
                tx.quantity, abs(tx.amount_cad), f'BUY {tx.symbol}'
            )
        elif tx_type in ['SELL', 'SOLD']:
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), f'SELL {tx.symbol}'
            )
            entries_created += self._create_position_entry(
                tx, statement_month, 'CREDIT',
                tx.quantity, abs(tx.amount_cad), f'SELL {tx.symbol}'
            )
        elif tx_type in ['DIV', 'DIVIDEND', 'DIST', 'INT', 'INTEREST_RECEIVED']:
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), f'{tx_type} {tx.symbol}'
            )
            entries_created += self._create_income_entry(
                tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f'{tx_type} {tx.symbol}'
            )
        elif tx_type in ['DEP', 'DEPOSIT', 'CONTRIB']:
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'DEBIT',
                abs(tx.amount_cad), tx_type
            )
        elif tx_type in ['WDRAW', 'WITHDRAWAL', 'TFR OUT', 'WIRE TFR']:
            entries_created += self._create_cash_entry(
                cash_ledger, tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), tx_type
            )
        elif tx_type == 'DIV REIN':
            entries_created += self._create_position_entry(
                tx, statement_month, 'DEBIT',
                tx.quantity, abs(tx.amount_cad), f'DIV REIN {tx.symbol}'
            )
            entries_created += self._create_income_entry(
                tx, statement_month, 'CREDIT',
                abs(tx.amount_cad), f'DIV REIN {tx.symbol}'
            )
        elif tx_type == 'SPLIT':
            entries_created += self._create_position_entry(
                tx, statement_month, 'DEBIT',
                tx.quantity, Decimal('0'), f'SPLIT {tx.symbol}'
            )
        else:
            logger.warning(f'Unknown transaction type: {tx_type} for tx {tx.id}')
        return entries_created

    def _create_cash_entry(self, cash_ledger, tx, statement_month, entry_type, amount, description):
        LedgerEntry.objects.create(
            ledger_account=cash_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=amount if entry_type == 'DEBIT' else -amount,
            quantity_change=Decimal('0'),
            cost_basis_change=Decimal('0'),
            source_transaction=tx,
            description=description,
            statement_month=statement_month,
            related_symbol=tx.symbol if tx.symbol else '',
        )
        cash_ledger.save()
        return 1

    def _create_position_entry(self, tx, statement_month, entry_type, quantity, cost_basis, description):
        position_ledger, created = LedgerAccount.objects.get_or_create(
            account=tx.account, account_type='POSITION',
            currency=tx.currency, symbol=tx.symbol,
            defaults={'quantity': Decimal('0'), 'cost_basis_total': Decimal('0')}
        )
        LedgerEntry.objects.create(
            ledger_account=position_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=Decimal('0'),
            quantity_change=quantity if entry_type == 'DEBIT' else -quantity,
            cost_basis_change=cost_basis if entry_type == 'DEBIT' else -cost_basis,
            source_transaction=tx,
            description=description,
            statement_month=statement_month,
        )
        if entry_type == 'DEBIT':
            position_ledger.quantity += quantity
            position_ledger.cost_basis_total += cost_basis
        else:
            position_ledger.quantity -= quantity
            position_ledger.cost_basis_total -= cost_basis
        if position_ledger.quantity > 0:
            position_ledger.cost_basis_per_unit = (
                position_ledger.cost_basis_total / position_ledger.quantity
            ).quantize(Decimal('0.0001'))
        else:
            position_ledger.cost_basis_per_unit = Decimal('0')
        position_ledger.save()
        return 1

    def _create_income_entry(self, tx, statement_month, entry_type, amount, description):
        income_type = tx.type.upper()
        if income_type in ['DIV', 'DIVIDEND', 'DIST']:
            income_type = 'DIVIDEND'
        elif income_type in ['INT', 'INTEREST_RECEIVED']:
            income_type = 'INTEREST'
        income_ledger, created = LedgerAccount.objects.get_or_create(
            account=tx.account, account_type='INCOME',
            currency=tx.currency, symbol=income_type,
            defaults={'quantity': Decimal('0'), 'cost_basis_total': Decimal('0')}
        )
        LedgerEntry.objects.create(
            ledger_account=income_ledger,
            entry_date=tx.date,
            entry_type=entry_type,
            cash_amount=amount if entry_type == 'CREDIT' else -amount,
            quantity_change=Decimal('0'),
            cost_basis_change=Decimal('0'),
            source_transaction=tx,
            description=description,
            statement_month=statement_month,
            related_symbol=tx.symbol if tx.symbol else '',
        )
        if entry_type == 'CREDIT':
            income_ledger.cost_basis_total += amount
        else:
            income_ledger.cost_basis_total -= amount
        income_ledger.save()
        return 1

    def create_monthly_balance_snapshot(self, account, month):
        ledger_accounts = LedgerAccount.objects.filter(account=account)
        for ledger in ledger_accounts:
            balance, _ = MonthlyLedgerBalance.objects.update_or_create(
                ledger_account=ledger,
                statement_month=month,
                defaults={
                    'quantity': ledger.quantity,
                    'cost_basis_total': ledger.cost_basis_total,
                    'cost_basis_per_unit': ledger.cost_basis_per_unit,
                }
            )
        self.stdout.write('  Created monthly balance snapshot')
'''

file_path = r'C:\AI-DevTeam\projects\portfolio_manager\core\management\commands\write_ledger.py'
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print(f'Fixed {file_path}')
