from django.core.management.base import BaseCommand
from django.db import models
from decimal import Decimal
from core.models import Transaction


class Command(BaseCommand):
    help = 'Audit and optionally repair amount_cad on Transaction records.'

    def add_arguments(self, parser):
        parser.add_argument('--fix', action='store_true', help='Apply repairs (default: dry-run)')
        parser.add_argument('--account', type=str, help='Filter by account_id (optional)')

    def handle(self, *args, **options):
        fix = options['fix']
        account_id = options['account']

        # Query transactions
        qs = Transaction.objects.all()
        if account_id:
            qs = qs.filter(account__account_id=account_id)

        suspicious = 0
        repairable = 0
        repaired = 0
        tx_details = []

        for tx in qs:
            if tx.amount != 0 and tx.amount_cad == 0:
                suspicious += 1
                if tx.exchange_rate_cad != 0:
                    repairable += 1
                    if fix:
                        tx.amount_cad = Decimal(round(tx.amount * tx.exchange_rate_cad, 2))
                        tx.save()
                        repaired += 1
                        tx_details.append(f"[REPAIRED] id={tx.id} account={tx.account.account_id if tx.account else ''} date={tx.date} symbol={tx.symbol} amount={tx.amount} {tx.currency} rate={tx.exchange_rate_cad} → set amount_cad={tx.amount_cad}")
                    else:
                        tx_details.append(f"[SUSPICIOUS] id={tx.id} account={tx.account.account_id if tx.account else ''} date={tx.date} symbol={tx.symbol} amount={tx.amount} {tx.currency} rate={tx.exchange_rate_cad} → would set amount_cad={round(tx.amount * tx.exchange_rate_cad, 2)}")

        # Output summary
        self.stdout.write(f"Total transactions scanned: {qs.count()}")
        self.stdout.write(f"Suspicious (amount_cad = 0, amount ≠ 0): {suspicious}")
        self.stdout.write(f"Repairable (have valid exchange_rate_cad): {repairable}")
        if fix:
            self.stdout.write(f"Repaired: {repaired}")

        # Output details
        for line in tx_details:
            self.stdout.write(line)