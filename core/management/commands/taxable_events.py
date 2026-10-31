"""Read-only report on the taxable RBC accounts (step 15a).

``manage.py taxable_events [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE``

Gathers the facts for the "known gaps in the taxable RBC accounts"
(architecture §12, step 15): non-plain transactions, RBF interest,
currencies, DLR rows, cross-account symbols, book-cost anomalies and stored
monthly-check verdicts. Strictly read-only: nothing is written to the
database; the only file written is ``--out`` (required). Default accounts:
every TAXABLE account with at least one Transaction.

Plan: docs/plans/taxable-events.md (Decisions: tdw, Oct 31).
"""

from django.core.management.base import BaseCommand, CommandError

from core.utils import taxable_events as te


class Command(BaseCommand):
    help = (
        "Read-only report on the taxable RBC accounts: non-plain "
        "transactions, RBF interest, currencies, DLR rows, cross-account "
        "symbols, book-cost anomalies. Only the --out file is written."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--account", default=None,
            help="Account name (e.g. TS_HUISGELD). Must be TAXABLE with at "
                 "least one transaction. Default: all such accounts.",
        )
        parser.add_argument(
            "--from", dest="date_from", default=None,
            help="First month to include (YYYY-MM; open-ended bound).",
        )
        parser.add_argument(
            "--to", dest="date_to", default=None,
            help="Last month to include (YYYY-MM; open-ended bound).",
        )
        parser.add_argument(
            "--out", required=True,
            help="Path for the report file (required; the only file written).",
        )

    def handle(self, *args, **options):
        try:
            month_start, month_end = te.month_bounds(
                options["date_from"], options["date_to"]
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from None

        accounts = te.taxable_accounts(options["account"])
        if not accounts:
            if options["account"]:
                raise CommandError(
                    f"no TAXABLE account with transactions named "
                    f"{options['account']!r}"
                )
            raise CommandError("no TAXABLE accounts with transactions")

        lines = self._header(options, accounts)
        for title, body in self._sections(accounts, month_start, month_end):
            lines.append(f"== {title} ==")
            lines.extend(body if body else ["  none"])
            lines.append("")
        with open(options["out"], "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")
        self.stdout.write(
            f"Report written to {options['out']} ({len(accounts)} account(s))"
        )

    def _header(self, options, accounts):
        return [
            "taxable_events report",
            f"filters: account={options['account'] or '(all taxable)'} "
            f"from={options['date_from'] or '(none)'} "
            f"to={options['date_to'] or '(none)'}",
            f"accounts: {', '.join(account.name for account in accounts)}",
            "convention: book_cost 0.00 means 'not printed on the PDF' "
            "(missing, decision 3)",
            "",
        ]

    def _sections(self, accounts, month_start, month_end):
        return [
            (
                "1. Non-plain transactions",
                te.section_1_non_plain(accounts, month_start, month_end),
            ),
            (
                "2. RBF interest (CSV rows + January PDF reference rows)",
                te.section_2_rbf_interest(accounts, month_start, month_end),
            ),
            (
                "3. Currencies (counts per currency/type; USD FX presence)",
                te.section_3_currencies(accounts, month_start, month_end),
            ),
            ("4. DLR rows", te.section_4_dlr(accounts, month_start, month_end)),
            (
                "5. Symbols held in >= 2 accounts in the same month",
                te.section_5_cross_account_symbols(
                    accounts, month_start, month_end
                ),
            ),
            (
                f"6. {te.SECTION6_ACCOUNT} {te.SECTION6_MONTH} statement holdings",
                te.section_6_target_statement(),
            ),
            (
                f"7. {te.SECTION7_ACCOUNT} first stored statement holdings",
                te.section_7_first_statement(),
            ),
            (
                "8. Unexplained PDF book-cost changes",
                te.section_8_book_cost_changes(accounts, month_start, month_end),
            ),
            (
                f"9. Stored monthly-check verdicts "
                f"({te.SECTION9_ACCOUNT} {', '.join(te.SECTION9_MONTHS)})",
                te.section_9_stored_verdicts(),
            ),
        ]
