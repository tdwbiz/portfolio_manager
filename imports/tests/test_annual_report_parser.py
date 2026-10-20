"""Tests for the RBC Annual Report parser (imports/parsers_annual.py)."""

import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.core.management import call_command
from io import BytesIO

from core.models import Account, RbcAnnual
from imports.parsers_annual import (
    parse_rbc_annual_report,
    _clean_rbc_text,
    _parse_full_date,
)


# ---------------------------------------------------------------------------
# Synthetic RBC Annual Report text fixtures (simulating cleaned PDF text)
# ---------------------------------------------------------------------------

SAMPLE_2259_6PAGE = """
Annual Investment Performance Report
E L S I E S M A R A I S R I F Account
Account 900- 00 0 05
from January 1, 2025 to December 31, 2025

Your Canadian dollar account

Change in Account Value
Beginning market value $80,000.00
Deposits and transfers-in $10,000.00
Withdrawals and transfers-out $5,000.00
Net investment return $15,000.34
Ending market value $100,000.34
Amount invested $85,000.00

Amount invested
Beginning value $80,000.00
Deposits and transfers-in $10,000.00
Withdrawals and transfers-out $5,000.00
Amount invested $85,000.00

Money-weighted rate of return
Past 1 year: 20.37%
Past 3 years: 15.93%
Past 5 years: 12.45%
Past 10 years: 10.22%
Since January 1, 2016: 11.87%

Your U.S. dollar account

Change in Account Value
Beginning market value $60,000.00
Deposits and transfers-in $8,000.00
Withdrawals and transfers-out $4,000.00
Net investment return $12,000.50
Ending market value $76,000.50
Amount invested $64,000.00

Money-weighted rate of return
Past 1 year: 19.50%
Past 3 years: 14.80%
Past 5 years: 11.90%
Past 10 years: 9.80%
Since January 1, 2016: 11.20%

Annual Charges and Compensation Report

Paid by you (CAD)

Operating Charges $0.00
Commissions - Equities/options $49.75
Total transaction charges $49.75
Total paid by you $49.75

Third-party compensation (CAD)

Trailing commission $6.42
Other compensation $2.00
Total compensation from third parties $8.42

Paid by you (USD)

Operating Charges $0.00
Commissions - Equities/options $35.00
Total transaction charges $35.00
Total paid by you $35.00

Third-party compensation (USD)

Trailing commission $4.50
Other compensation $1.50
Total compensation from third parties $6.00
"""

SAMPLE_4033_4PAGE = """
Annual Investment Performance Report
R O B E R T J O H N S O N L I F Account
Account 690- 84 0 33
from January 1, 2025 to December 31, 2025

Your Canadian dollar account

Change in Account Value
Beginning market value $150,000.00
Deposits and transfers-in $20,000.00
Withdrawals and transfers-out $10,000.00
Net investment return $45,722.34
Ending market value $205,722.34
Amount invested $160,000.00

Amount invested
Beginning value $150,000.00
Deposits and transfers-in $20,000.00
Withdrawals and transfers-out $10,000.00
Amount invested $160,000.00

Money-weighted rate of return
Past 1 year: 18.72%
Past 3 years: 14.50%
Past 5 years: 11.20%
Past 10 years: 9.50%
Since January 1, 2016: 10.80%

Annual Charges and Compensation Report

Paid by you (CAD)

Operating Charges $0.00
Commissions - Equities/options $30.00
Total transaction charges $30.00
Total paid by you $30.00

Third-party compensation (CAD)

Trailing commission $5.00
Other compensation $1.00
Total compensation from third parties $6.00
"""


class TestCleanRbcText(TestCase):
    """Test the _clean_rbc_text helper function."""

    def test_wide_spacing(self):
        """Test 1: Wide character spacing is collapsed to normal words."""
        wide = "A n n u a l   i n v e s t m e n t   p e r f o r m a n c e"
        cleaned = _clean_rbc_text(wide)
        self.assertEqual(cleaned, "Annual investment performance")

    def test_mixed_spacing(self):
        """Test 2: Mixed spacing normalizes to single spaces."""
        mixed = "Beginning   market   value    $80,000.00"
        cleaned = _clean_rbc_text(mixed)
        self.assertEqual(cleaned, "Beginning market value $80,000.00")

    def test_already_clean(self):
        """Test 3: Already-clean text stays clean."""
        clean = "This is already clean text."
        cleaned = _clean_rbc_text(clean)
        self.assertEqual(cleaned, "This is already clean text.")


class TestParseFullDate(TestCase):
    """Test the _parse_full_date helper function."""

    def test_january_1_2025(self):
        result = _parse_full_date("January 1, 2025", 2025)
        self.assertEqual(result, date(2025, 1, 1))

    def test_december_31_2025(self):
        result = _parse_full_date("December 31, 2025", 2025)
        self.assertEqual(result, date(2025, 12, 31))

    def test_invalid_month(self):
        with self.assertRaises(ValueError):
            _parse_full_date("InvalidMonth 1, 2025", 2025)


class TestParseAnnualReport6Page(TestCase):
    """Test parsing a 6-page annual report (CAD + USD)."""

    def test_parse_2259_6page(self):
        """Test 1: Parse 6-page report and verify all fields."""
        tmpdir = tempfile.mkdtemp()
        report_file = os.path.join(tmpdir, 'Annual Report-0005 2025-12-31.pdf')
        with open(report_file, 'w') as f:
            f.write(SAMPLE_2259_6PAGE)
        try:
            result = parse_rbc_annual_report(report_file)

            # Broker
            self.assertEqual(result['broker'], 'RBC')

            # Report year
            self.assertEqual(result['report_year'], 2025)

            # Account metadata
            self.assertEqual(result['account_number'], '900-00005')
            self.assertEqual(result['account_type_code'], 'RIF')

            # Period
            self.assertEqual(result['report_period_start'], date(2025, 1, 1))
            self.assertEqual(result['report_period_end'], date(2025, 12, 31))

            # CAD data (VL-compatible flat dict)
            cad = result['cad']
            self.assertIsNotNone(cad)
            self.assertEqual(cad['bal_beginning_value'], Decimal('80000.00'))
            self.assertEqual(cad['bal_deposits_in'], Decimal('10000.00'))
            self.assertEqual(cad['bal_withdrawals_out'], Decimal('5000.00'))
            self.assertEqual(cad['bal_net_return'], Decimal('15000.34'))
            self.assertEqual(cad['bal_ending_value'], Decimal('100000.34'))
            self.assertEqual(cad['bal_amount_invested'], Decimal('85000.00'))
            self.assertEqual(cad['chg_total_paid'], Decimal('49.75'))
            self.assertEqual(cad['comp_total'], Decimal('8.42'))
            self.assertEqual(cad['perf_1yr'], Decimal('20.37'))
            self.assertEqual(cad['perf_3yr'], Decimal('15.93'))

            # USD data
            usd = result['usd']
            self.assertIsNotNone(usd)
            self.assertEqual(usd['bal_ending_value'], Decimal('76000.50'))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestParseAnnualReport4Page(TestCase):
    """Test parsing a 4-page annual report (CAD only)."""

    def _write_pdf_text(self, text: str) -> str:
        fd, path = tempfile.mkstemp(suffix='.pdf')
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        return path

    def test_parse_4033_4page(self):
        """Test 2: Parse 4-page report and verify CAD-only data."""
        tmpdir = tempfile.mkdtemp()
        report_file = os.path.join(tmpdir, 'Annual Report-0008 2025-12-31.pdf')
        with open(report_file, 'w') as f:
            f.write(SAMPLE_4033_4PAGE)
        try:
            result = parse_rbc_annual_report(report_file)

            # Account metadata
            self.assertEqual(result['account_number'], '900-00008')
            self.assertEqual(result['account_type_code'], 'LIF')

            # CAD data (VL-compatible flat dict)
            cad = result['cad']
            self.assertIsNotNone(cad)
            self.assertEqual(cad['bal_ending_value'], Decimal('205722.34'))
            self.assertEqual(cad['perf_1yr'], Decimal('18.72'))

            # USD should be None for CAD-only report
            self.assertIsNone(result['usd'])

            # Charges: CAD total paid (sample data shows Total paid by you $30.00)
            self.assertEqual(cad['chg_total_paid'], Decimal('30.00'))
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestDuplicateAnnualReportSkipped(TestCase):
    """Test that duplicate annual reports are skipped."""

    @patch('imports.parsers_annual_vl.parse_rbc_annual_report_vl')
    def test_duplicate_annual_report_skipped(self, mock_vl):
        """Test 3: Import the same annual report twice.
        Verify the second import is skipped with a warning.

        We mock the VL parser to use the text parser so the test works
        with text files (not real PDFs).
        """
        from imports.parsers_annual import parse_rbc_annual_report

        def mock_parse(filepath):
            # The text parser also takes a file path — call it directly
            return parse_rbc_annual_report(filepath)

        mock_vl.side_effect = mock_parse

        account = Account.objects.create(
            account_id="900-00005",
            name="Test RIF Account",
            broker="RBC",
        )

        # Create a temp directory with an annual report file
        import tempfile
        tmpdir = tempfile.mkdtemp()
        report_file = os.path.join(tmpdir, 'Annual Report-0005 2025-12-31.pdf')
        with open(report_file, 'w') as f:
            f.write(SAMPLE_2259_6PAGE)
        try:
            # First import - creates CAD + USD records = 2 records
            call_command('manage_annual_report_import', tmpdir)
            self.assertEqual(RbcAnnual.objects.count(), 2)

            # Second import: the file was moved to processed/ by the first import,
            # so create a fresh copy to test duplicate detection
            report_file2 = os.path.join(tmpdir, 'Annual Report-0005 2025-12-31.pdf')
            with open(report_file2, 'w') as f:
                f.write(SAMPLE_2259_6PAGE)
            # Second import - should be skipped (both CAD and USD already exist)
            call_command('manage_annual_report_import', tmpdir)
            self.assertEqual(RbcAnnual.objects.count(), 2)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
            account.delete()


class TestAnnualReportView(TestCase):
    """Test the annual reports view."""

    def test_annual_report_page_shows_summary(self):
        """Test 4: GET /annual-reports/ should show report summaries."""
        from django.contrib.auth.models import User
        user = User.objects.create_user(username='testuser', password='testpass')
        self.client.login(username='testuser', password='testpass')

        account = Account.objects.create(
            account_id="900-00005",
            name="Test RIF Account",
            broker="RBC",
        )

        report = RbcAnnual.objects.create(
            account=account,
            file_name='Annual Report-0005 2025-12-31.pdf',
            report_year=2025,
            report_period_start=date(2025, 1, 1),
            report_period_end=date(2025, 12, 31),
            account_number='900-00005',
            account_type_code='RIF',
            currency='CAD',
        )

        response = self.client.get('/annual-reports/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'RBC Annual Reports')
        self.assertContains(response, '2025')

        report.delete()
        account.delete()
        user.delete()

