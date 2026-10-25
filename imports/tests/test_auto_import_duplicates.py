"""Tests for the imports.duplicates CSV log line format (consultant review fix).

The duplicate detail line must keep exactly one value per column. When the
account number was replaced by the account name, the comma after it was lost
and the name and the date merged into a single field, shifting every later
column of ``docs/reconciliation/CSV_Duplicates.csv``.
"""

import os
import shutil
import tempfile

from django.test import TestCase

from core.models import Account
from core.utils.account_fingerprint import fingerprint_account_number
from imports.auto_import import auto_import


RBC_CSV_HEADER = (
    '"Activity Export as of January 15, 2024"\n'
    '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
    '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
    '"Settlement Date","Account","Value","Currency","Description"\n'
)

RBC_ROW_A = (
    '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5","2800.00",'
    '"January 17, 2024","90000001","13990.05","USD","Buy GOOGL"\n'
)

RBC_ROW_B = (
    '"January 16, 2024","Sell","AAPL","Apple Inc.","2","200.00",'
    '"January 18, 2024","90000001","395.00","USD","Sell AAPL"\n'
)


class DuplicateLogLineTests(TestCase):
    """The duplicate line in CSV_Duplicates.csv keeps its column layout."""

    def setUp(self) -> None:
        # auto_import writes its tracking CSVs to docs/reconciliation/ relative
        # to the CWD — run from a scratch directory so the real files are
        # never touched.
        self._old_cwd = os.getcwd()
        self.temp_dir = tempfile.mkdtemp()
        os.chdir(self.temp_dir)

        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001",
        )

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_csv(self, name: str, rows: list[str]) -> str:
        path = os.path.join(self.temp_dir, name)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            f.write(RBC_CSV_HEADER + "".join(rows))
        return path

    def test_duplicate_line_column_count_and_account_column(self) -> None:
        # First import brings ROW_A in. The second file repeats it at the same
        # row position (same tx_hash → duplicate) plus a new trade, so the
        # file-level content-hash dedupe does not skip it wholesale.
        first = self._write_csv("first.csv", [RBC_ROW_A])
        imported, skipped, _ = auto_import(first, "RBC")
        self.assertEqual((imported, skipped), (1, 0))

        second = self._write_csv("second.csv", [RBC_ROW_A, RBC_ROW_B])
        imported, skipped, _ = auto_import(second, "RBC")
        self.assertEqual((imported, skipped), (1, 1))

        dup_path = os.path.join(
            self.temp_dir, "docs", "reconciliation", "CSV_Duplicates.csv"
        )
        self.assertTrue(os.path.exists(dup_path))
        with open(dup_path, newline="", encoding="utf-8") as f:
            lines = [line for line in f.read().splitlines() if line.strip()]

        self.assertEqual(len(lines), 2)  # header + exactly one duplicate
        header, dup_line = lines[0], lines[1]
        self.assertEqual(len(header.split(",")), 11)

        fields = dup_line.split(",")
        # One value per column — the account name must not merge with the date.
        self.assertEqual(len(fields), 11)
        self.assertEqual(fields[0], "second.csv")
        self.assertTrue(fields[1].startswith("Line "))
        self.assertEqual(fields[2], "ACCOUNT_A")
