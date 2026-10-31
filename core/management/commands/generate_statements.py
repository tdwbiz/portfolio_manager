"""Forward statement generation (phase 4, step 14d).

``manage.py generate_statements [--account NAME] [--from YYYY-MM] [--to YYYY-MM] [--apply] [--out FILE]``

Dry run by default: builds the account's statement rows forward from the
stored RBC PDF statements and CSV transactions (one generator —
``core.utils.statements.generate_forward_statements``) and prints a report:
per-account counts by source (REPLAY / PDF / GAP), PASS rows equal to the
PDF, rows priced from history, stale rows, and every PASS-vs-PDF mismatch.
Nothing is written to the database; ``--out FILE`` writes the same report
to a file.

With ``--apply`` the built-in check runs first: if any PASS row's generated
ending_market_value differs from the PDF end_total_market_value by more than
a cent, the command exits non-zero without writing anything. Otherwise it
deletes the selected accounts' existing rows (within the --from/--to window
on statement_date, or all of them) together with their positions and writes
the generated rows in one transaction.

Output contains account names, dates, currencies, symbols and amounts only
— never account digits.

Plan: docs/plans/statements-forward.md (Decisions: tdw, Oct 30).
"""

import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from django.db import transaction

from core.models import (
    Account,
    CoreMonthlyStatement,
    CoreMonthlyStatementPosition,
)
from core.utils.statements import generate_forward_statements


def _parse_month(text, option):
    """'YYYY-MM' -> (first day of month, last day of month)."""
    try:
        parsed = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise CommandError(f"{option} must be YYYY-MM, got {text!r}")
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return parsed, date(parsed.year, parsed.month, last_day)


class Command(BaseCommand):
    help = (
        "Generate monthly statements forward from the stored RBC PDF "
        "statements and CSV transactions. Dry run by default: prints a "
        "report (counts by source, PASS rows equal to the PDF, history- "
        "priced and stale rows, PASS-vs-PDF mismatches); --out writes the "
        "same report to a file. --apply refuses on any PASS-vs-PDF "
        "mismatch; otherwise it replaces the selected accounts' rows (and "
        "positions) in one transaction."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", default=None,
            help="Account name (e.g. TOM_TFSA). Default: all accounts.",
        )
        parser.add_argument(
            "--from", dest="date_from", default=None,
            help="First statement month to include (YYYY-MM, filter on "
                 "statement_date; the chain is computed in full so rows at "
                 "the window edge still anchor correctly).",
        )
        parser.add_argument(
            "--to", dest="date_to", default=None,
            help="Last statement month to include (YYYY-MM, filter on "
                 "statement_date).",
        )
        parser.add_argument(
            "--out", default=None,
            help="Path for the full report file.",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Write: refuses (exit non-zero, nothing written) when any "
                 "PASS row's generated ending differs from the PDF "
                 "end_total_market_value by more than a cent; otherwise "
                 "replaces the selected accounts' rows and positions within "
                 "the --from/--to window (or all of them) in one "
                 "transaction.",
        )

    def handle(self, *args, **options):
        from_date = to_date = None
        if options["date_from"]:
            from_date, _ = _parse_month(options["date_from"], "--from")
        if options["date_to"]:
            _, to_date = _parse_month(options["date_to"], "--to")

        accounts = Account.objects.all().order_by("name")
        if options["account"]:
            accounts = accounts.filter(name=options["account"])
            if not accounts.exists():
                raise CommandError(f"Unknown account: {options['account']}")

        accounts = list(accounts)
        reports = [
            generate_forward_statements(account, from_date=from_date, to_date=to_date)[1]
            for account in accounts
        ]

        if options["apply"]:
            mismatches = [
                (report["account_name"], d, c, diff)
                for report in reports
                for (d, c, diff) in report["mismatches"]
            ]
            if mismatches:
                for name, d, c, diff in mismatches:
                    self.stderr.write(
                        f"PASS-vs-PDF mismatch {name} {d} [{c}]: "
                        f"generated ending differs by {diff}"
                    )
                raise CommandError(
                    f"--apply refused: {len(mismatches)} PASS row(s) do not "
                    f"equal the PDF end_total_market_value to the cent "
                    f"(nothing written)"
                )
            written = self._apply(accounts, reports, from_date, to_date)
            self.stdout.write(
                f"Applied: {written} row(s) written for "
                f"{len([r for r in reports if r['rows']])} account(s)"
            )

        lines = self._render(reports, applying=options["apply"])
        if options["out"]:
            with open(options["out"], "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self.stdout.write(f"Report written to {options['out']}")
        for line in lines:
            self.stdout.write(line)

    def _apply(self, accounts, reports, from_date, to_date):
        """Delete-and-replace the selected accounts' rows in one transaction."""
        written = 0
        with transaction.atomic():
            for account, report in zip(accounts, reports):
                qs = CoreMonthlyStatement.objects.filter(account=account)
                if from_date is not None:
                    qs = qs.filter(statement_date__gte=from_date)
                if to_date is not None:
                    qs = qs.filter(statement_date__lte=to_date)
                qs.delete()  # positions cascade
                for row in report["rows"]:
                    stmt = CoreMonthlyStatement.objects.create(
                        account=account,
                        statement_month=row.statement_month,
                        statement_date=row.statement_date,
                        currency=row.currency,
                        source=row.source,
                        beginning_market_value=row.beginning_market_value,
                        ending_market_value=row.ending_market_value,
                        total_buys=row.total_buys,
                        total_sells=row.total_sells,
                        total_dividends=row.total_dividends,
                        total_interest=row.total_interest,
                        total_commission=row.total_commission,
                        total_deposits=row.total_deposits,
                        total_withdrawals=row.total_withdrawals,
                        total_transfers=row.total_transfers,
                        total_income=row.total_income,
                        total_fees=row.total_fees,
                        total_tax=row.total_tax,
                        net_flow=row.net_flow,
                        total_transactions=row.total_transactions,
                        ending_cash_balance=row.ending_cash_balance,
                        has_stale_prices=row.has_stale_prices,
                    )
                    for p in row.positions:
                        CoreMonthlyStatementPosition.objects.create(
                            statement=stmt,
                            symbol=p.symbol,
                            quantity=p.quantity,
                            price=p.price,
                            price_source=p.price_source,
                            value=p.value,
                        )
                    written += 1
        return written

    def _render(self, reports, applying=False):
        mode = "apply" if applying else "dry run"
        lines = [f"Forward statement generation ({mode})", ""]
        for report in reports:
            counts = report["counts"]
            rows = report["rows"]
            lines.append(
                f"{report['account_name']}: {len(rows)} row(s) — "
                f"REPLAY {counts['REPLAY']}, PDF {counts['PDF']}, GAP {counts['GAP']}"
            )
            lines.append(
                f"  PASS rows equal to the PDF: {report['pass_match']}/{report['pass_rows']}"
            )
            lines.append(
                f"  rows priced from history: {report['history_priced_rows']}; "
                f"stale rows: {report['stale_rows']}"
            )
            for row in rows:
                lines.append(
                    f"  {row.statement_date} [{row.currency}] {row.source}: "
                    f"begin {row.beginning_market_value} end {row.ending_market_value} "
                    f"cash {row.ending_cash_balance} tx {row.total_transactions}"
                )
                for p in row.positions:
                    lines.append(
                        f"    {p.symbol}: {p.quantity} @ {p.price} "
                        f"({p.price_source}) = {p.value}"
                    )
            for stmt_date, currency, diff in report["mismatches"]:
                lines.append(
                    f"  MISMATCH {stmt_date} [{currency}]: generated ending "
                    f"differs from the PDF end_total_market_value by {diff}"
                )
            lines.append("")
        return lines
