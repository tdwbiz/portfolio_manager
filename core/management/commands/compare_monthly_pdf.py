"""compare_monthly_pdf — read-only check of stored RbcMonthly rows vs a PDF.

Phase 3 step 13a-2 (docs/plans/compare-monthly-pdf.md). For each given
monthly statement file: parse it with the deterministic text parser
(``imports/parsers_pdf.py``), run its self-check, resolve the account by
fingerprint and compare the parsed fields against the stored ``RbcMonthly``
rows field by field. The command is strictly read-only — it performs no
database writes of any kind.

Output privacy (hard rule): lines carry only file names, account names,
currencies, statement dates, row ids, counts and verdicts — never amounts,
quantities, prices, account numbers or any account digits. The parsed
account number is used only for the fingerprint lookup and is never echoed.
"""

import os

from django.core.management.base import BaseCommand

from core.models import RbcMonthly
from core.utils.account_fingerprint import lookup_account
from imports.parsers_pdf import (
    _extract_text_from_pdf,
    parse_rbc_pdf_multi,
    self_check_sections,
)


class Command(BaseCommand):
    help = (
        "Read-only: parse monthly statement PDFs with the text parser and "
        "compare each field against the stored RbcMonthly rows. Never writes "
        "to the database."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "paths",
            nargs="+",
            type=str,
            help="Path(s) to monthly statement PDFs (or text files for tests).",
        )

    def handle(self, *args, **options):
        for path in options["paths"]:
            self._process_file(path)

    # ------------------------------------------------------------------
    # Per file
    # ------------------------------------------------------------------

    def _process_file(self, path: str) -> None:
        base = os.path.basename(path)
        if not os.path.exists(path):
            self.stdout.write(f"MISSING_FILE {base}")
            return

        with open(path, "rb") as f:
            is_pdf = f.read(4) == b"%PDF"

        if is_pdf:
            sections = parse_rbc_pdf_multi(path)
            full_text = _extract_text_from_pdf(path)
        else:
            # Plain text (test fixtures): pass the string to the parser.
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            sections = parse_rbc_pdf_multi(text)
            full_text = text

        reasons = self_check_sections(sections, full_text)
        if reasons:
            for reason in reasons:
                self.stdout.write(f"SELF_CHECK {base}: {reason}")
        else:
            self.stdout.write(f"SELF_CHECK OK {base}")

        for section in sections:
            self._process_section(base, section)

    # ------------------------------------------------------------------
    # Per currency section
    # ------------------------------------------------------------------

    def _process_section(self, base: str, section: dict) -> None:
        cur = str(section.get("currency") or "CAD").upper()

        try:
            account = lookup_account(section.get("account_number") or "")
        except ValueError as exc:
            # Fingerprint key missing — fail loudly at the point of use.
            self.stderr.write(f"ERROR {base} {cur}: {exc}")
            return
        if account is None:
            self.stdout.write(f"UNKNOWN_ACCOUNT {base} {cur}")
            return

        statement_date = section.get("statement_date")
        rows = []
        if statement_date is not None:
            rows = list(
                RbcMonthly.objects.filter(
                    account=account,
                    statement_date=statement_date,
                    currency=cur,
                ).order_by("id")
            )
        self.stdout.write(f"ROWS FOUND {len(rows)} {base} {cur}")

        # Comparison is added in step 2 of the plan.
        self._compare_section(base, section, account, rows)

    def _compare_section(
        self, base: str, section: dict, account, rows: list
    ) -> None:
        """Compare one parsed currency section against its stored rows.

        Stub for step 1; the field-by-field comparison lands in step 2.
        """
        return None
