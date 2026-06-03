from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, timedelta
from core.models import Account, Transaction, PositionHistory


class Command(BaseCommand):
    help = "Rebuild the position_history table from core_transaction records"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Skip confirmation prompt for truncating data",
        )

    def handle(self, *args, **options):
        if not options["force"]:
            confirm = input("This will truncate all position_history records. Continue? (yes/no): ")
            if confirm.lower() != "yes":
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        self.stdout.write("Truncating position_history...")
        PositionHistory.objects.all().delete()

        self.stdout.write("Loading transactions...")
        transactions = Transaction.objects.select_related("account").order_by(
            "account", "symbol", "date", "id"
        )

        # Group transactions by (account_id, symbol)
        groups = {}
        for tx in transactions:
            key = (tx.account_id, tx.symbol)
            if key not in groups:
                groups[key] = []
            groups[key].append(tx)

        records_created = 0
        symbols_processed = set()
        accounts_processed = set()

        for (account_id, symbol), txs in groups.items():
            account = Account.objects.get(id=account_id)
            owner = account.owner
            accounts_processed.add(account_id)
            symbols_processed.add(symbol)

            running_qty = Decimal("0")
            running_cost_basis = Decimal("0")
            current_record = None

            for tx in txs:
                if tx.type in ("BUY", "DIVIDEND", "SPECIAL_DIVIDEND"):
                    tx_cost = tx.price * tx.quantity + tx.commission
                    new_qty = tx.quantity
                    if running_qty > 0:
                        running_cost_basis = (running_cost_basis + tx_cost)
                        running_qty += new_qty
                    else:
                        running_cost_basis = tx_cost
                        running_qty = new_qty
                elif tx.type in ("SELL",):
                    tx_proceeds = (tx.price * tx.quantity) - tx.commission
                    if running_qty > 0:
                        cost_per_unit = running_cost_basis / running_qty
                        cost_of_sold = cost_per_unit * tx.quantity
                        running_cost_basis -= cost_of_sold
                        running_qty -= tx.quantity

                # Close current record if quantity changed
                if current_record and (
                    abs(current_record.quantity - running_qty) > Decimal("0.0001")
                    or current_record.quantity == Decimal("0")
                ):
                    current_record.date_to = tx.date - timedelta(days=1)
                    current_record.save()
                    records_created += 1

                # Open new record
                if running_qty > 0:
                    cost_basis_per_unit = running_cost_basis / running_qty if running_qty > 0 else Decimal("0")
                    current_record = PositionHistory(
                        owner=owner,
                        account_id=account_id,
                        symbol=symbol,
                        quantity=running_qty,
                        cost_basis_per_unit=cost_basis_per_unit,
                        total_cost_basis=running_cost_basis,
                        currency=tx.currency,
                        date_from=tx.date,
                        date_to=None,
                    )
                    current_record.save()
                    records_created += 1
                else:
                    current_record = None

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuild complete: {records_created} records created, "
                f"{len(symbols_processed)} symbols, {len(accounts_processed)} accounts."
            )
        )
