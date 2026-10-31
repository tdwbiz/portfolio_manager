"""Read-only book-cost check for the taxable accounts (step 15d).

``manage.py book_cost_check [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE``

Replays each taxable account's transactions RBC-style (quantity and book
cost per security, native currency, no FX, no pooling across accounts)
from its OpeningPosition seed and compares with the book costs printed on
the stored RBC monthly PDFs. Strictly read-only: nothing is written to the
database; the only file written is ``--out`` (required). Default accounts:
every TAXABLE account with at least one stored RBC monthly statement.

Plan: docs/plans/book-cost-check.md (Decisions: tdw, Oct 31).
"""

from django.core.management.base import BaseCommand, CommandError

from core.utils import book_cost_check as bcc
from core.utils.taxable_events import month_bounds


class Command(BaseCommand):
    help = (
        "Read-only book-cost check for the taxable RBC accounts: replays "
        "each account's transactions from its opening-position seed and "
        "compares quantity and book cost with the stored RBC monthly PDFs. "
        "Only the --out file is written."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", default=None,
            help="Account name (e.g. TS_HUISGELD). Must be TAXABLE with at "
                 "least one stored RBC monthly statement. Default: all such "
                 "accounts.",
        )
        parser.add_argument(
            "--from", dest="date_from", default=None,
            help="First month whose statements are compared (YYYY-MM; "
                 "open-ended bound). The replay always starts from the seed.",
        )
        parser.add_argument(
            "--to", dest="date_to", default=None,
            help="Last month whose statements are compared (YYYY-MM; "
                 "open-ended bound).",
        )
        parser.add_argument(
            "--out", required=True,
            help="Path for the report file (required; the only file written).",
        )

    def handle(self, *args, **options):
        try:
            month_start, month_end = month_bounds(
                options["date_from"], options["date_to"]
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from None

        accounts = bcc.taxable_accounts_with_statements(options["account"])
        if not accounts:
            if options["account"]:
                raise CommandError(
                    f"no TAXABLE account with stored RBC monthly statements "
                    f"named {options['account']!r}"
                )
            raise CommandError(
                "no TAXABLE accounts with stored RBC monthly statements"
            )

        per_account = []
        for account in accounts:
            replay = bcc.replay_account(account)
            comparison, summaries = bcc.compare_account(
                account, replay, month_start, month_end
            )
            per_account.append((account.name, replay, comparison, summaries))

        lines = self._header(options, accounts)
        for name, replay, comparison, _ in per_account:
            lines.append(f"== {name} ==")
            lines.extend(replay.notes)
            lines.extend(comparison or ["  none"])
            if replay.flags:
                lines.append("  flagged rows:")
                lines.extend(replay.flags)
            lines.append("")
        lines.append("== SUMMARY ==")
        summary_lines = []
        for name, _, _, summaries in per_account:
            summary_lines.extend(bcc.summary_lines(name, summaries))
        lines.extend(summary_lines or ["  none"])
        lines.append("")
        lines.append(
            "== CANDIDATES (unexplained PDF book-cost changes) =="
        )
        candidates = bcc.candidates_lines(accounts, month_start, month_end)
        lines.extend(candidates or ["  none"])

        with open(options["out"], "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        self.stdout.write(
            f"Report written to {options['out']} ({len(accounts)} account(s))"
        )

    def _header(self, options, accounts):
        return [
            "book_cost_check report",
            f"filters: account={options['account'] or '(all taxable with statements)'} "
            f"from={options['date_from'] or '(none)'} "
            f"to={options['date_to'] or '(none)'}",
            f"accounts: {', '.join(account.name for account in accounts)}",
            "convention: book_cost 0.00 means 'not printed on the PDF' (skipped)",
            "",
        ]
