"""Load or override opening positions (data only, no ACB calculation).

Snapshot mode (copy book cost from an RBC monthly statement):
    manage.py load_opening_positions --account NAME --statement YYYY-MM-DD [--apply] [--replace]

Override mode (change one existing row by hand):
    manage.py load_opening_positions --account NAME --set SYMBOL --currency CODE \
        --book-cost AMOUNT [--note TEXT] [--apply]

Both modes are dry runs unless --apply is given. Output shows the account
name, symbols, quantities and amounts only — never account digits.

Refusals (snapshot mode): unknown account; no RBC statement on that date;
existing OpeningPosition rows without --replace; any holding with an empty
symbol or book_cost == 0; any transaction settling on or before the
statement date (the guard applies with --replace too).

Refusals (override mode): --set/--currency/--book-cost must be given
together; --statement and --replace are not allowed with --set; no
existing OpeningPosition for that (account, symbol, currency) — this
command only overrides existing rows.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Account, OpeningPosition, RbcMonthly, Transaction


class Command(BaseCommand):
    help = "Load opening positions from an RBC statement or override one row (dry run by default)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--account",
            required=True,
            help="exact account name",
        )
        parser.add_argument(
            "--statement",
            help="statement date YYYY-MM-DD (snapshot mode)",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="delete the account's existing opening positions first (snapshot mode)",
        )
        parser.add_argument(
            "--set",
            dest="symbol",
            metavar="SYMBOL",
            help="override mode: symbol of the existing row to change",
        )
        parser.add_argument(
            "--currency",
            help="override mode: currency code of the existing row (required with --set)",
        )
        parser.add_argument(
            "--book-cost",
            dest="book_cost",
            metavar="AMOUNT",
            help="override mode: new book cost (required with --set)",
        )
        parser.add_argument(
            "--note",
            help="override mode: replace the row's note (optional with --set)",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="write the changes (default is a dry run)",
        )

    def handle(self, *args, **options):
        account = Account.objects.filter(name=options["account"]).first()
        if account is None:
            raise CommandError(
                f"No account named {options['account']!r}; nothing done."
            )

        if options["symbol"] is not None:
            self.handle_override(account, options)
        else:
            self.handle_snapshot(account, options)

    # ------------------------------------------------------------------
    # Snapshot mode
    # ------------------------------------------------------------------
    def handle_snapshot(self, account, options):
        if not options["statement"]:
            raise CommandError(
                "--statement YYYY-MM-DD is required in snapshot mode "
                "(or use --set for override mode)."
            )
        try:
            statement_date = date.fromisoformat(options["statement"])
        except ValueError:
            raise CommandError(
                f"--statement {options['statement']!r} is not a YYYY-MM-DD date."
            )

        statements = list(
            RbcMonthly.objects.filter(account=account, statement_date=statement_date)
        )
        if not statements:
            raise CommandError(
                f"No RBC monthly statement for {account.name} on "
                f"{statement_date.isoformat()}; nothing done."
            )

        existing = OpeningPosition.objects.filter(account=account).count()
        if existing and not options["replace"]:
            raise CommandError(
                f"{account.name} already has {existing} opening position row(s); "
                "re-run with --replace to delete and rewrite them."
            )

        holdings = [h for s in statements for h in s.holdings.all()]
        offenders = [h for h in holdings if not h.symbol or h.book_cost == 0]
        if offenders:
            lines = [
                f"  {h.symbol or '(no symbol)'} — {h.security} [{h.currency}]"
                for h in offenders
            ]
            raise CommandError(
                "Refusing: the following holdings have an empty symbol or a zero "
                "book cost and were not imported:\n" + "\n".join(lines)
            )

        early_txs = Transaction.objects.filter(
            account=account, settlement_date__lte=statement_date
        )
        if early_txs.exists():
            dates = sorted(
                early_txs.values_list("settlement_date", flat=True).distinct()
            )
            raise CommandError(
                f"Refusing: {early_txs.count()} transaction(s) for {account.name} "
                f"settle on or before {statement_date.isoformat()} "
                f"(dates: {', '.join(d.isoformat() for d in dates)}). "
                "An opening position must predate all transactions."
            )

        rows = [
            (h.symbol, h.security, h.currency, h.quantity, h.book_cost, h.rbc_monthly)
            for h in holdings
        ]
        listing = "\n".join(
            f"  {symbol} [{currency}] qty {quantity} book cost {book_cost}"
            for symbol, _name, currency, quantity, book_cost, _stmt in rows
        )

        if not options["apply"]:
            self.stdout.write(
                f"Dry run: would load {len(rows)} opening position(s) for "
                f"{account.name} from RBC statement {statement_date.isoformat()}:\n"
                f"{listing}"
            )
            return

        with transaction.atomic():
            if existing:
                OpeningPosition.objects.filter(account=account).delete()
            note = f"from RBC statement {statement_date.isoformat()}"
            OpeningPosition.objects.bulk_create(
                OpeningPosition(
                    account=account,
                    statement=stmt,
                    symbol=symbol,
                    security_name=security,
                    currency=currency,
                    quantity=quantity,
                    book_cost=book_cost,
                    source="RBC_BOOK_COST",
                    note=note,
                )
                for symbol, security, currency, quantity, book_cost, stmt in rows
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {len(rows)} opening position(s) for {account.name} "
                f"from RBC statement {statement_date.isoformat()}:\n{listing}"
            )
        )

    # ------------------------------------------------------------------
    # Override mode
    # ------------------------------------------------------------------
    def handle_override(self, account, options):
        if not options["currency"] or options["book_cost"] is None:
            raise CommandError(
                "--set requires --currency and --book-cost to be given together."
            )
        if options["statement"]:
            raise CommandError("--statement is not allowed with --set.")
        if options["replace"]:
            raise CommandError("--replace is not allowed with --set.")

        try:
            new_cost = Decimal(options["book_cost"])
        except InvalidOperation:
            raise CommandError(
                f"--book-cost {options['book_cost']!r} is not a number."
            )

        row = OpeningPosition.objects.filter(
            account=account, symbol=options["symbol"], currency=options["currency"]
        ).first()
        if row is None:
            raise CommandError(
                f"No opening position for {account.name} "
                f"{options['symbol']} [{options['currency']}]; this command only "
                "overrides existing rows. Nothing done."
            )

        if not options["apply"]:
            self.stdout.write(
                f"Dry run: would set book cost {new_cost} (source MANUAL) for "
                f"{account.name} {options['symbol']} [{options['currency']}]"
                + (" and replace its note" if options["note"] is not None else "")
                + "."
            )
            return

        row.book_cost = new_cost
        if options["note"] is not None:
            row.note = options["note"]
        row.source = "MANUAL"
        # The existing row's statement link is kept unchanged.
        row.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Set book cost {new_cost} (source MANUAL) for {account.name} "
                f"{options['symbol']} [{options['currency']}]"
            )
        )
