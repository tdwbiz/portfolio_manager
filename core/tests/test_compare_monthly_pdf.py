"""Tests for the compare_monthly_pdf read-only check (phase 3 step 13a-2).

Synthetic statement text only (fake account 90000001 / ACCOUNT_A, key from
settings_test); stored rows are built directly. Privacy rule: no fixture
amounts, quantities, prices or account digits may appear in the command's
output — the tests assert that.
"""

import re
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from core.models import (
    Account,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
)
from core.utils.account_fingerprint import fingerprint_account_number

# Synthetic July statement: parses cleanly and passes the step 12b self-check
# (holdings + cash == end total to the cent; dates within 100 days).
STATEMENT_TEXT = """
RRSP STATEMENT (CDN$)
JUL 31 2025
Your Account Number: 900-00001-1-8
Date of Last Statement: JUN. 30 2025

--------------------------------------------------------------------------------
Asset Summary
--------------------------------------------------------------------------------
Cash                  $10.00          1.00 %
Common Shares         $990.00         99.00 %
Total on JUN. 30      $800.00
Total on JUL 31       $1,000.00       100.00 %

--------------------------------------------------------------------------------
Cash Balance
--------------------------------------------------------------------------------
ACCOUNT TYPE            OPENING BALANCE AT JUN. 30   CLOSING BALANCE AT JUL 31
Cash                    $10.00                         $10.00

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.40000 CAD as of JUL 31, 2025 )
--------------------------------------------------------------------------------
SECURITY QUANTITY MKT. PRICE BOOK COST MARKET VALUE
SYMBOL SEGREGATED RATE
ALPHA FUND              AAA         10.000      50.000        500.00       $500.00
BETA FUND               BBB         9.800       50.000        490.00       $490.00

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
"""

STATEMENT_DATE = date(2025, 7, 31)
LAST_STATEMENT_DATE = date(2025, 6, 30)

# (symbol, quantity, book_cost, market_price, market_value) — matches the text.
DEFAULT_HOLDINGS = (
    ("AAA", "10.0000", "500.00", "50.0000", "500.00"),
    ("BBB", "9.8000", "490.00", "50.0000", "490.00"),
)

# Fixture values that must never leak into the output (privacy hard rule).
FORBIDDEN_STRINGS = (
    "10.00", "990.00", "800.00", "1,000.00", "1000.00",
    "500.00", "490.00", "50.000", "9.800", "10.000",
    "900-00001-1-8", "90000001",
)

# Every output line must match one of these safe patterns (field names,
# symbols, account name, file name, date, counts, verdicts — nothing else).
LINE_PATTERNS = (
    r"^SELF_CHECK OK \S+$",
    r"^SELF_CHECK \S+: .+$",
    r"^MISSING_FILE \S+$",
    r"^UNKNOWN_ACCOUNT \S+ (CAD|USD)$",
    r"^NO_STATEMENT_DATE \S+ (CAD|USD)$",
    r"^ROWS FOUND \d+ \S+ (CAD|USD)$",
    r"^\S+ [A-Z0-9_]+ (CAD|USD) \d{4}-\d{2}-\d{2} "
    r"(?:\[row \d+\] )?.+: (MATCH|DIFFER|ONLY_PDF|ONLY_DB)$",
    r"^\S+ [A-Z0-9_]+ (CAD|USD) \d{4}-\d{2}-\d{2} "
    r"(?:\[row \d+\] )?DUP_SYMBOLS .+ \d+ \d+$",
    r"^TOTALS \S+: fields compared \d+, MATCH \d+, DIFFER \d+, "
    r"ONLY_PDF \d+, ONLY_DB \d+$",
)


class CompareMonthlyPdfTestBase(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="ACCOUNT_A",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000001"),
        )
        self._temp_paths = []

    def tearDown(self):
        import os
        for path in self._temp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_text(self, text: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt")
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        self._temp_paths.append(path)
        return path

    def _run(self, text: str = STATEMENT_TEXT) -> list[str]:
        """Run the command on a temp file; return its stdout lines."""
        path = self._write_text(text)
        out = StringIO()
        call_command("compare_monthly_pdf", path, stdout=out)
        return [line for line in out.getvalue().splitlines() if line.strip()]

    def _make_row(
        self,
        file_name: str = "rbc-a.pdf",
        end_total: Decimal = Decimal("1000.00"),
        cash: Decimal = Decimal("10.00"),
        holdings=None,
    ) -> RbcMonthly:
        row = RbcMonthly.objects.create(
            account=self.account,
            file_name=file_name,
            statement_date=STATEMENT_DATE,
            currency="CAD",
            last_statement_date=LAST_STATEMENT_DATE,
            begin_total_market_value=Decimal("800.00"),
            end_total_market_value=end_total,
        )
        RbcMonthlyBalance.objects.create(
            rbc_monthly=row,
            currency="CAD",
            account_type="Cash",
            opening_balance=cash,
            closing_balance=cash,
            market_value=cash,
        )
        for sym, qty, cost, price, mv in (holdings or DEFAULT_HOLDINGS):
            RbcMonthlyHolding.objects.create(
                rbc_monthly=row,
                currency="CAD",
                security=sym.upper() + " FUND",
                symbol=sym,
                quantity=Decimal(qty),
                book_cost=Decimal(cost),
                market_price=Decimal(price),
                market_value=Decimal(mv),
            )
        return row

    @staticmethod
    def _verdict_lines(lines: list[str]) -> list[tuple[str, str]]:
        """(line, verdict) for every per-field line."""
        result = []
        for line in lines:
            match = re.search(r": (MATCH|DIFFER|ONLY_PDF|ONLY_DB)$", line)
            if match and "TOTALS" not in line:
                result.append((line, match.group(1)))
        return result

    def _assert_privacy(self, lines: list[str]) -> None:
        for line in lines:
            with self.subTest(line=line):
                self.assertTrue(
                    any(re.match(p, line) for p in LINE_PATTERNS),
                    f"line does not match a safe pattern: {line!r}",
                )
                self.assertIsNone(
                    re.search(r"\d+\.\d+", line),
                    f"a decimal value leaked into the output: {line!r}",
                )
        text = "\n".join(lines)
        for forbidden in FORBIDDEN_STRINGS:
            self.assertNotIn(forbidden, text)


class CompareMonthlyPdfAllMatchTest(CompareMonthlyPdfTestBase):
    def test_all_fields_and_holdings_equal(self):
        self._make_row()
        lines = self._run()

        self.assertIn("SELF_CHECK OK " + self._temp_paths[-1].rsplit("/", 1)[-1], lines)
        self.assertTrue(any(line.startswith("ROWS FOUND 1 ") for line in lines))

        verdicts = self._verdict_lines(lines)
        self.assertTrue(verdicts)
        for line, verdict in verdicts:
            with self.subTest(line=line):
                self.assertEqual(verdict, "MATCH")


class CompareMonthlyPdfTwoRowsTest(CompareMonthlyPdfTestBase):
    def test_each_stored_row_compared_and_labelled_by_id(self):
        row_a = self._make_row(file_name="rbc-a.pdf")
        row_b = self._make_row(file_name="rbc-b.pdf")
        lines = self._run()

        self.assertTrue(any(line.startswith("ROWS FOUND 2 ") for line in lines))
        label_a = f"[row {row_a.pk}]"
        label_b = f"[row {row_b.pk}]"
        for label in (label_a, label_b):
            labelled = [l for l in lines if label in l]
            self.assertEqual(len(labelled), 13, f"expected 13 field lines for {label}")
        # No file_name of a stored row may appear in the output.
        text = "\n".join(lines)
        self.assertNotIn("rbc-a.pdf", text)
        self.assertNotIn("rbc-b.pdf", text)


class CompareMonthlyPdfOneCentOffTest(CompareMonthlyPdfTestBase):
    def test_one_stored_value_off_by_one_cent(self):
        self._make_row(end_total=Decimal("1000.01"))
        lines = self._run()

        verdicts = self._verdict_lines(lines)
        differ = [l for l, v in verdicts if v == "DIFFER"]
        self.assertEqual(len(differ), 1)
        self.assertIn("end_total_market_value: DIFFER", differ[0])
        for line, verdict in verdicts:
            if "end_total_market_value" not in line:
                with self.subTest(line=line):
                    self.assertEqual(verdict, "MATCH")


class CompareMonthlyPdfNoStoredRowTest(CompareMonthlyPdfTestBase):
    def test_missing_stored_row_yields_only_pdf(self):
        lines = self._run()

        self.assertTrue(any(line.startswith("ROWS FOUND 0 ") for line in lines))
        self.assertNotIn("[row", "\n".join(lines))
        verdicts = self._verdict_lines(lines)
        self.assertTrue(verdicts)
        for line, verdict in verdicts:
            with self.subTest(line=line):
                self.assertEqual(verdict, "ONLY_PDF")


class CompareMonthlyPdfHoldingSidesTest(CompareMonthlyPdfTestBase):
    def test_holding_only_in_pdf_and_only_in_db(self):
        # Stored row has AAA and CCC; the PDF has AAA and BBB.
        self._make_row(holdings=[
            ("AAA", "10.0000", "500.00", "50.0000", "500.00"),
            ("CCC", "4.0000", "200.00", "50.0000", "200.00"),
        ])
        lines = self._run()
        text = "\n".join(lines)

        for field in ("quantity", "book_cost", "market_price", "market_value"):
            self.assertIn(f"holding BBB {field}: ONLY_PDF", text)
            self.assertIn(f"holding CCC {field}: ONLY_DB", text)
            self.assertIn(f"holding AAA {field}: MATCH", text)


class CompareMonthlyPdfReadOnlyTest(CompareMonthlyPdfTestBase):
    def test_row_counts_unchanged(self):
        self._make_row()
        before = {
            "Account": Account.objects.count(),
            "RbcMonthly": RbcMonthly.objects.count(),
            "RbcMonthlyBalance": RbcMonthlyBalance.objects.count(),
            "RbcMonthlyHolding": RbcMonthlyHolding.objects.count(),
        }

        self._run()

        after = {
            "Account": Account.objects.count(),
            "RbcMonthly": RbcMonthly.objects.count(),
            "RbcMonthlyBalance": RbcMonthlyBalance.objects.count(),
            "RbcMonthlyHolding": RbcMonthlyHolding.objects.count(),
        }
        self.assertEqual(before, after)


class CompareMonthlyPdfTotalsTest(CompareMonthlyPdfTestBase):
    def test_totals_line_counts_equal_printed_verdicts(self):
        # Mixed verdicts: one cent off + a holding only in the PDF.
        self._make_row(
            end_total=Decimal("1000.01"),
            holdings=[("AAA", "10.0000", "500.00", "50.0000", "500.00")],
        )
        lines = self._run()

        totals_lines = [l for l in lines if l.startswith("TOTALS ")]
        self.assertEqual(len(totals_lines), 1)
        match = re.match(
            r"^TOTALS \S+: fields compared (\d+), MATCH (\d+), DIFFER (\d+), "
            r"ONLY_PDF (\d+), ONLY_DB (\d+)$",
            totals_lines[0],
        )
        self.assertIsNotNone(match)
        compared, n_match, n_differ, n_only_pdf, n_only_db = (int(g) for g in match.groups())

        verdicts = [v for _, v in self._verdict_lines(lines)]
        self.assertEqual(compared, len(verdicts))
        self.assertEqual(n_match, verdicts.count("MATCH"))
        self.assertEqual(n_differ, verdicts.count("DIFFER"))
        self.assertEqual(n_only_pdf, verdicts.count("ONLY_PDF"))
        self.assertEqual(n_only_db, verdicts.count("ONLY_DB"))


class CompareMonthlyPdfPrivacyTest(CompareMonthlyPdfTestBase):
    def test_every_line_matches_a_safe_pattern(self):
        self._make_row(end_total=Decimal("1000.01"))
        lines = self._run()
        self._assert_privacy(lines)
