"""Fill CSV gaps from stored PDF reference rows (phase 4, step 14-0d).

``manage.py fill_from_pdf --account NAME [--from YYYY-MM] [--to YYYY-MM]``

Dry run by default: for every statement period of the account that FAILS the
monthly check inside the window, reports the cleaned-CSV slice rows that would
be removed and the mapped PDF rows that would be added, with the before/after
verdicts. A period is filled only if the monthly check passes after the
change; otherwise it is left unchanged and reported with the check's remaining
differences. Nothing is written to the database.

Output uses account names, dates, types, symbols and amounts only — never
account digits.

Plan: docs/plans/fill-from-pdf.md (Decisions: tdw, Oct 30).
"""

import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from core.models import Account
from core.utils.fill_from_pdf import plan_period
from core.utils.monthly_check import (
    check_statement,
    csv_coverage_bounds,
    group_statements,
    previous_groups,
)


def _parse_month(text, option):
    """'YYYY-MM' -> (first day of month, last day of month)."""
    try:
        parsed = datetime.strptime(text, "%Y-%m").date()
    except ValueError:
        raise CommandError(f"{option} must be YYYY-MM, got {text!r}")
    last_day = calendar.monthrange(parsed.year, parsed.month)[1]
    return parsed, date(parsed.year, parsed.month, last_day)


def _fmt_tx(tx):
    symbol = tx.symbol or "(no symbol)"
    return (
        f"{tx.date} {tx.type} {symbol} qty={tx.quantity} "
        f"amount={tx.amount} {tx.currency}"
    )


class Command(BaseCommand):
    help = (
        "Fill CSV gaps from stored PDF reference rows: per failing monthly-check "
        "period, remove the cleaned-CSV slice rows and add the mapped unmatched "
        "PDF rows. Dry run by default; a period is filled only if the monthly "
        "check passes after the change."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", required=True,
            help="Account name (e.g. TOM_TFSA); must match exactly one account.",
        )
        parser.add_argument(
            "--from", dest="date_from", default=None,
            help="First statement month to include (YYYY-MM, filter on statement_date).",
        )
        parser.add_argument(
            "--to", dest="date_to", default=None,
            help="Last statement month to include (YYYY-MM, filter on statement_date).",
        )

    def handle(self, *args, **options):
        accounts = Account.objects.filter(name=options["account"])
        if accounts.count() != 1:
            raise CommandError(
                f"--account {options['account']!r} must match exactly one account "
                f"(found {accounts.count()})"
            )
        account = accounts.first()

        month_start = month_end = None
        if options["date_from"]:
            month_start, _ = _parse_month(options["date_from"], "--from")
        if options["date_to"]:
            _, month_end = _parse_month(options["date_to"], "--to")

        # Group over ALL statements of the account (the window only filters
        # which periods are reported, so a period inside the window can still
        # chain to a previous one outside it).
        groups = group_statements(Account.objects.filter(pk=account.pk))
        prev_of = previous_groups(groups)
        bounds = csv_coverage_bounds()

        failing = []  # [(StatementGroup, StatementCheck)] inside the window
        for group in groups:
            if month_start and group.statement_date < month_start:
                continue
            if month_end and group.statement_date > month_end:
                continue
            check = check_statement(group, prev_of[id(group)], bounds)
            if any(cur.verdict == "FAIL" for cur in check.currencies):
                failing.append((group, check))

        window = (
            f"{options['date_from'] or '(start)'}..{options['date_to'] or '(end)'}"
        )
        self.stdout.write(
            f"fill_from_pdf dry run — account {account.name}, window {window}"
        )
        self.stdout.write(f"failing periods in window: {len(failing)}")

        would_fill = unchanged = 0
        for group, _before in failing:
            plan = plan_period(group, prev_of[id(group)], bounds, account)
            if self._report_period(plan):
                would_fill += 1
            else:
                unchanged += 1

        self.stdout.write(
            f"summary: {len(failing)} failing — would fill {would_fill}, "
            f"left unchanged {unchanged}"
        )

    # ------------------------------------------------------------------
    # Reporting (dry run)
    # ------------------------------------------------------------------

    @staticmethod
    def _verdict_line(check):
        parts = [f"[{cur.currency}] {cur.verdict}" for cur in check.currencies]
        kinds = check.failed_kinds
        suffix = f" ({', '.join(kinds)})" if kinds else ""
        return " ".join(parts) + suffix

    def _failure_lines(self, check):
        return [line for cur in check.currencies for line in cur.failures]

    def _report_period(self, plan):
        """Print the report for one failing period.

        Returns True when the dry-run simulation would fill the period
        (after-check passes), False when it is left unchanged.
        """
        out = self.stdout.write
        group = plan.group
        out(f"== {group.statement_date} ==")
        out(f"  verdict before: {self._verdict_line(plan.before)}")

        if plan.unmapped:
            for line in plan.unmapped:
                out(f"  STOPPED (unmapped activity): {line}")
            for line in self._failure_lines(plan.before):
                out(f"    {line}")
            out("  left unchanged")
            return False

        for tx in plan.slice_rows:
            out(f"  remove: {_fmt_tx(tx)}")
        for tx in plan.new_rows:
            out(f"  add:    {_fmt_tx(tx)}")
        if not plan.slice_rows and not plan.new_rows:
            out("  nothing to fill (no unmatched PDF rows)")

        after = plan.after
        out(f"  verdict after:  {self._verdict_line(after)}")
        if any(cur.verdict == "FAIL" for cur in after.currencies):
            for line in self._failure_lines(after):
                out(f"    {line}")
            out("  left unchanged (after-check still fails)")
            return False
        return True
