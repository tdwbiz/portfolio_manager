"""Forward statement generation (phase 4, step 14d).

``manage.py generate_statements [--account NAME] [--from YYYY-MM] [--to YYYY-MM] [--out FILE]``

Dry run by default: builds the account's statement rows forward from the
stored RBC PDF statements and CSV transactions (one generator —
``core.utils.statements.generate_forward_statements``) and prints a report:
per-account counts by source (REPLAY / PDF / GAP), PASS rows equal to the
PDF, rows priced from history, stale rows, and every PASS-vs-PDF mismatch.
Nothing is written to the database; ``--out FILE`` writes the same report
to a file.

Output contains account names, dates, currencies, symbols and amounts only
— never account digits.

Plan: docs/plans/statements-forward.md (Decisions: tdw, Oct 30).
"""

import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from core.models import Account
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
        "same report to a file."
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

        reports = [
            generate_forward_statements(account, from_date=from_date, to_date=to_date)[1]
            for account in accounts
        ]

        lines = self._render(reports)
        if options["out"]:
            with open(options["out"], "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            self.stdout.write(f"Report written to {options['out']}")
        for line in lines:
            self.stdout.write(line)

    def _render(self, reports):
        lines = ["Forward statement generation (dry run)", ""]
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
