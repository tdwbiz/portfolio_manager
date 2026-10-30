"""Tests for the csv_coverage management command (docs/plans/csv-coverage.md).

Synthetic RBC-format Activity CSVs are written to a temp folder; fake accounts
90000001/90000002 are created as Account rows with stored fingerprints (same
convention as imports/tests/test_import_csv_command.py). The command is
read-only: the assertions cover the report file written via --out.
"""

import os
import shutil
import tempfile
from datetime import date

from django.core.management import call_command
from django.test import TestCase

from core.models import Account, RbcMonthly, RbcMonthlyTransaction
from core.utils.account_fingerprint import fingerprint_account_number


RBC_HEADER = (
    '"Activity Export as of January 15, 2024"\n'
    '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
    '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
    '"Settlement Date","Account","Value","Currency","Description"\n'
)


def rbc_row(date_s, activity, symbol, qty, price, settlement, account,
            value, notes):
    """One quoted RBC Activity CSV data row."""
    return (f'"{date_s}","{activity}","{symbol}","{symbol} desc.","{qty}",'
            f'"{price}","{settlement}","{account}","{value}","USD","{notes}"\n')


class CsvCoverageCommandTest(TestCase):
    """csv_coverage audits a folder of Activity CSVs without DB writes."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.csv_dir = os.path.join(self.temp_dir, 'csvs')
        os.makedirs(self.csv_dir)
        self.out_path = os.path.join(self.temp_dir, 'report.txt')

        # Fake accounts with stored fingerprints (fixed test key in
        # settings_test — no .env needed).
        Account.objects.create(
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001")
        Account.objects.create(
            name="ACCOUNT_B", broker="RBC",
            fingerprint=fingerprint_account_number("90000002"), last_4="0002")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, name: str, rows: list[str]) -> str:
        path = os.path.join(self.csv_dir, name)
        with open(path, 'w', newline='', encoding='utf-8') as f:
            f.write(RBC_HEADER + ''.join(rows))
        return path

    def _run(self) -> str:
        call_command('csv_coverage', csv_dir=self.csv_dir, out=self.out_path)
        with open(self.out_path, encoding='utf-8') as f:
            return f.read()

    # ------------------------------------------------------------------
    # Overlaps
    # ------------------------------------------------------------------

    def test_overlap_range_and_identical_count(self) -> None:
        """Two files overlapping Jan 15..20 with identical rows in the range."""
        row_jan10 = rbc_row("January 10, 2024", "Buy", "GOOGL", "5",
                            "2800.00", "January 12, 2024", "90000001",
                            "13990.05", "Buy GOOGL")
        row_jan15 = rbc_row("January 15, 2024", "Sell", "AAPL", "2",
                            "200.00", "January 17, 2024", "90000001",
                            "395.00", "Sell AAPL")
        row_jan20 = rbc_row("January 20, 2024", "Buy", "MSFT", "3",
                            "400.00", "January 23, 2024", "90000001",
                            "1200.00", "Buy MSFT")
        row_jan25 = rbc_row("January 25, 2024", "Dividends", "T", "0",
                            "0.00", "January 29, 2024", "90000001",
                            "50.00", "DIV ON 10 SHS")
        self._write('a.csv', [row_jan10, row_jan15, row_jan20])
        self._write('b.csv', [row_jan15, row_jan20, row_jan25])

        report = self._run()

        line = next(l for l in report.splitlines() if 'a.csv x b.csv' in l)
        self.assertIn('2024-01-15 .. 2024-01-20', line)
        self.assertIn('2 identical rows', line)

    # ------------------------------------------------------------------
    # Gaps
    # ------------------------------------------------------------------

    def test_gap_over_31_days_names_files_on_each_side(self) -> None:
        """A 46-day gap is reported with the file on each side."""
        self._write('a.csv', [rbc_row(
            "January 15, 2024", "Buy", "GOOGL", "5", "2800.00",
            "January 17, 2024", "90000001", "13990.05", "Buy GOOGL")])
        self._write('b.csv', [rbc_row(
            "March 01, 2024", "Sell", "AAPL", "2", "200.00",
            "March 04, 2024", "90000001", "395.00", "Sell AAPL")])

        report = self._run()

        line = next(l for l in report.splitlines() if ' -> ' in l)
        self.assertIn('2024-01-15 (a.csv)', line)
        self.assertIn('2024-03-01 (b.csv)', line)
        self.assertIn('46 days', line)

    # ------------------------------------------------------------------
    # Unknown accounts / privacy
    # ------------------------------------------------------------------

    def test_unknown_account_counted_and_number_never_reported(self) -> None:
        """A row with no stored fingerprint is counted, never shown."""
        self._write('a.csv', [rbc_row(
            "January 15, 2024", "Buy", "GOOGL", "5", "2800.00",
            "January 17, 2024", "99999999", "13990.05", "Buy GOOGL")])

        report = self._run()

        self.assertIn('unknown-account rows: 1', report)
        self.assertNotIn('99999999', report)

    # ------------------------------------------------------------------
    # Monthly table
    # ------------------------------------------------------------------

    def test_repeated_row_within_one_file_kept_in_monthly_count(self) -> None:
        """Identical rows within one file are kept (multiplicity preserved)."""
        row = rbc_row("January 15, 2024", "Buy", "GOOGL", "5", "2800.00",
                      "January 17, 2024", "90000001", "13990.05", "Buy GOOGL")
        self._write('a.csv', [row, row])

        report = self._run()

        line = next(l for l in report.splitlines()
                    if l.strip().startswith('2024-01:'))
        self.assertIn('csv=2', line)

    def test_pdf_only_month_flagged(self) -> None:
        """A month with PDF rows but no CSV rows is flagged."""
        self._write('a.csv', [rbc_row(
            "January 15, 2024", "Buy", "GOOGL", "5", "2800.00",
            "January 17, 2024", "90000001", "13990.05", "Buy GOOGL")])

        account = Account.objects.get(name="ACCOUNT_A")
        stmt = RbcMonthly.objects.create(
            account=account, file_name="stmt_2024-03.pdf",
            statement_date=date(2024, 3, 31))
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2024, 3, 5), activity_type="DIVIDEND")
        RbcMonthlyTransaction.objects.create(
            rbc_monthly=stmt, date=date(2024, 3, 20), activity_type="DIST")

        report = self._run()

        line = next(l for l in report.splitlines()
                    if l.strip().startswith('2024-03:'))
        self.assertIn('csv=0', line)
        self.assertIn('pdf=2', line)
        self.assertIn('FLAGGED (PDF rows, no CSV rows)', line)
