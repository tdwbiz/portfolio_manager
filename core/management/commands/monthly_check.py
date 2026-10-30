"""Read-only CSV-vs-RBC monthly check report (phase 4, step 14a).

``manage.py monthly_check [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE``

Compares the CSV ``Transaction`` rows with the stored RBC monthly statements
and reports, per account / statement / currency, a verdict (FIRST / NO_CSV /
PASS / FAIL), plus an import-log section for errored PDFs. Nothing is written
to the database; the only file written is ``--out``.

Plan: docs/plans/monthly-check-report.md.
"""

import calendar
from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError

from core.models import Account
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


class Command(BaseCommand):
    help = (
        "Read-only CSV-vs-RBC report: per account/statement/currency verdict "
        "(FIRST / NO_CSV / PASS / FAIL) plus an import-log section. "
        "Writes nothing to the database; only the --out report file."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", default=None,
            help="Account name (e.g. TOM_TFSA). Default: all accounts.",
        )
        parser.add_argument(
            "--from", dest="date_from", default=None,
            help="First statement month to include (YYYY-MM, filter on statement_date).",
        )
        parser.add_argument(
            "--to", dest="date_to", default=None,
            help="Last statement month to include (YYYY-MM, filter on statement_date).",
        )
        parser.add_argument(
            "--out", required=True,
            help="Path for the full report file (required).",
        )

    def handle(self, *args, **options):
        month_start = month_end = None
        if options["date_from"]:
            month_start, _ = _parse_month(options["date_from"], "--from")
        if options["date_to"]:
            _, month_end = _parse_month(options["date_to"], "--to")

        accounts = Account.objects.all().order_by("name")
        if options["account"]:
            accounts = accounts.filter(name=options["account"])

        # Group over ALL statements of the selected accounts (the window only
        # filters which statements are reported, so a statement inside the
        # window can still chain to a previous one outside it).
        groups = group_statements(accounts)
        prev_of = previous_groups(groups)
        bounds = csv_coverage_bounds()

        results = []  # [(StatementGroup, StatementCheck)] inside the window
        for group in groups:
            if month_start and group.statement_date < month_start:
                continue
            if month_end and group.statement_date > month_end:
                continue
            results.append(
                (group, check_statement(group, prev_of[id(group)], bounds))
            )

        self._write_report(options["out"], options, bounds, results)
        self._print_summary(accounts, results)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _fmt_bounds(self, bounds):
        first, last = bounds
        return (f"first settlement {first}, last settlement {last}"
                if first is not None else "no Transactions with a settlement date")

    def _statement_lines(self, group, check):
        lines = []
        for cur in check.currencies:
            line = f"  {check.statement_date} [{cur.currency}] {cur.verdict}"
            lines.append(line)
            lines.extend(f"    {f}" for f in cur.failures)
        return lines

    def _write_report(self, out_path, options, bounds, results):
        lines = [
            "monthly_check report",
            f"filters: account={options['account'] or '(all)'} "
            f"from={options['date_from'] or '(none)'} to={options['date_to'] or '(none)'}",
            f"CSV coverage (global): {self._fmt_bounds(bounds)}",
            "",
        ]
        by_account = {}
        for group, check in results:
            by_account.setdefault(check.account_name, []).append((group, check))
        for account_name in sorted(by_account):
            lines.append(account_name)
            for group, check in by_account[account_name]:
                lines.extend(self._statement_lines(group, check))
            lines.append("")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")

    def _print_summary(self, accounts, results):
        per_account = {}
        for _group, check in results:
            entry = per_account.setdefault(
                check.account_name,
                {"FIRST": 0, "NO_CSV": 0, "PASS": 0, "FAIL": 0, "kinds": set()},
            )
            for cur in check.currencies:
                entry[cur.verdict] += 1
            entry["kinds"].update(check.failed_kinds)

        self.stdout.write(f"Accounts: {accounts.count()}")
        self.stdout.write(f"Statements checked: {len(results)}")
        totals = {"FIRST": 0, "NO_CSV": 0, "PASS": 0, "FAIL": 0}
        for account_name in sorted(per_account):
            entry = per_account[account_name]
            for verdict in totals:
                totals[verdict] += entry[verdict]
            kinds = (f" ({', '.join(sorted(entry['kinds']))})"
                     if entry["FAIL"] else "")
            self.stdout.write(
                f"  {account_name}: FIRST={entry['FIRST']} NO_CSV={entry['NO_CSV']} "
                f"PASS={entry['PASS']} FAIL={entry['FAIL']}{kinds}"
            )
        self.stdout.write(
            f"Totals: FIRST={totals['FIRST']} NO_CSV={totals['NO_CSV']} "
            f"PASS={totals['PASS']} FAIL={totals['FAIL']}"
        )
