"""Read-only CSV-vs-RBC monthly check report (phase 4, step 14a).

``manage.py monthly_check [--account NAME] [--from YYYY-MM] [--to YYYY-MM] --out FILE``

Compares the CSV ``Transaction`` rows with the stored RBC monthly statements
and reports, per account / statement / currency, a verdict (FIRST / NO_CSV /
PASS / FAIL), plus an import-log section for errored PDFs. Nothing is written
to the database; the only file written is ``--out``.

Plan: docs/plans/monthly-check-report.md.
"""

from django.core.management.base import BaseCommand


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
        # Step 1 skeleton: no checks yet.
        self.stdout.write("0 statements checked")
        with open(options["out"], "w", encoding="utf-8") as f:
            f.write("monthly_check report\n\n0 statements checked\n")
