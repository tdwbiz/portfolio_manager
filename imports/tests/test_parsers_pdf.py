"""Tests for the RBC PDF parser (imports/parsers_pdf.py)."""

import os
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.core.management import call_command
from io import BytesIO

from core.models import Account, RbcMonthly, RbcMonthlyBalance, RbcMonthlyTransaction
from core.utils.account_fingerprint import fingerprint_account_number
from imports.parsers_pdf import parse_rbc_pdf, _parse_rbc_date


# ---------------------------------------------------------------------------
# Synthetic RBC PDF text fixtures
# ---------------------------------------------------------------------------

SAMPLE_RBC_PDF_TEXT = """
RRSP STATEMENT (CDN$)
SEPT 30 2025
Your Account Number: 900-00003-1-8
Date of Last Statement: AUG. 29 2025

--------------------------------------------------------------------------------
Asset Summary
--------------------------------------------------------------------------------
Cash                  $20.85          1.97 %
Mutual Funds          $1,036.34        98.03 %
Total                $1,057.19       100.00 %

--------------------------------------------------------------------------------
Income Summary
--------------------------------------------------------------------------------
                      This Month    Year-to-Date
Dividends               $0.00        $0.00
Interest                $0.00        $0.00
Total Income            $0.00        $0.00

--------------------------------------------------------------------------------
Cash Balance
--------------------------------------------------------------------------------
ACCOUNT TYPE            OPENING BALANCE AT AUG. 29   CLOSING BALANCE AT SEPT 30
Cash                    $20.85                         $20.85
Fixed Income            $0.00                          $0.00

--------------------------------------------------------------------------------
Contribution Summary
--------------------------------------------------------------------------------
PERSONAL CONTRIBUTION     First 60 days    Balance of Year
                          $0.00              $0.00

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.39185 CAD as of SEPT 30, 2025 )
--------------------------------------------------------------------------------
SYMBOL      SECURITY                  QUANTITY    MKT. PRICE    BOOK COST    MARKET VALUE
RBF2010     RBC INVESTMENT SAVINGS    103.634     10.000        1,036.34     $1,036.34

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
SEPT 02     INTEREST    RBC INVESTMENT SAVINGS ACCOUNT SR A (2010) AS OF 08/29/25 REINVEST @ $10.00   0.209                   $0.209
"""

MULTI_SECURITY_PDF_TEXT = """
RRSP STATEMENT (CDN$)
OCT 31 2025
Your Account Number: 123-45678-9-0
Date of Last Statement: SEPT 30 2025

--------------------------------------------------------------------------------
Asset Summary
--------------------------------------------------------------------------------
Cash                  $100.00          5.00 %
Mutual Funds          $1,500.00        75.00 %
Foreign Securities      $400.00        20.00 %
Total                $2,000.00       100.00 %

--------------------------------------------------------------------------------
Income Summary
--------------------------------------------------------------------------------
                      This Month    Year-to-Date
Dividends               $50.00       $500.00
Interest                $10.00        $100.00
Total Income            $60.00       $600.00

--------------------------------------------------------------------------------
Cash Balance
--------------------------------------------------------------------------------
ACCOUNT TYPE            OPENING BALANCE AT SEPT 30   CLOSING BALANCE AT OCT 31
Cash                    $100.00                        $100.00
Fixed Income            $0.00                          $0.00

--------------------------------------------------------------------------------
Contribution Summary
--------------------------------------------------------------------------------
PERSONAL CONTRIBUTION     First 60 days    Balance of Year
                          $500.00            $500.00

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.40000 CAD as of OCT 31, 2025 )
--------------------------------------------------------------------------------
SYMBOL      SECURITY                  QUANTITY    MKT. PRICE    BOOK COST    MARKET VALUE
RBF2010     RBC INVESTMENT SAVINGS    150.000     10.500        1,500.00     $1,575.00
VGRO        VANGUARD GROWTH ETF       10.000      50.000        500.00       $500.00
BDL.UN      BROOKFIELD DIVIDEND       5.000       80.000        400.00       $400.00

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
OCT 05      BUY         PURCHASE OF VGRO                                 50.00         10.00    $500.00
OCT 10      SELL        SALE OF BDL.UN                                   80.00          5.00                   $400.00
OCT 15      DIVIDEND    VGRO DIVIDEND                                     1.50         10.00                   $15.00
OCT 20      INTEREST    RBC INVESTMENT SAVINGS                            0.50                     $0.50
"""

MISSING_SECTIONS_PDF_TEXT = """
RRSP STATEMENT (CDN$)
NOV 30 2025
Your Account Number: 999-88877-6-5
Date of Last Statement: OCT 31 2025

--------------------------------------------------------------------------------
Asset Summary
--------------------------------------------------------------------------------
Cash                  $50.00          10.00 %
Mutual Funds          $450.00        90.00 %
Total                $500.00       100.00 %

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.38000 CAD as of NOV 30, 2025 )
--------------------------------------------------------------------------------
SYMBOL      SECURITY                  QUANTITY    MKT. PRICE    BOOK COST    MARKET VALUE
RBF2010     RBC INVESTMENT SAVINGS    45.000      10.000        450.00       $450.00

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
NOV 01      INTEREST    RBC INVESTMENT SAVINGS                            0.10                     $0.10
"""


class TestParseRbcDate(TestCase):
    """Test the _parse_rbc_date helper function."""

    def test_parse_sept_30(self):
        result = _parse_rbc_date("SEPT 30", 2025)
        self.assertEqual(result, date(2025, 9, 30))

    def test_parse_aug_dot_29(self):
        result = _parse_rbc_date("AUG. 29", 2025)
        self.assertEqual(result, date(2025, 8, 29))

    def test_parse_jan_1(self):
        result = _parse_rbc_date("JAN 01", 2025)
        self.assertEqual(result, date(2025, 1, 1))


class TestParseRbcPdf(TestCase):
    """Test the main parse_rbc_pdf function."""

    def test_parse_sample_pdf(self):
        """Test 1: Parse sample PDF and verify all fields match expected values."""
        result = parse_rbc_pdf(SAMPLE_RBC_PDF_TEXT)

        self.assertEqual(result['statement_date'], date(2025, 9, 30))
        self.assertEqual(result['last_statement_date'], date(2025, 8, 29))
        self.assertEqual(result['account_number'], "900-00003-1-8")
        self.assertEqual(result['fx_rate'], Decimal("1.39185"))

        # Check balances
        balances = result['balances']
        cash_bal = next((b for b in balances if b['account_type'] == 'Cash'), None)
        self.assertIsNotNone(cash_bal)
        self.assertEqual(cash_bal['market_value'], Decimal("20.85"))
        self.assertEqual(cash_bal['percentage'], Decimal("1.97"))

        mf_bal = next((b for b in balances if b['account_type'] == 'Mutual Funds'), None)
        self.assertIsNotNone(mf_bal)
        self.assertEqual(mf_bal['market_value'], Decimal("1036.34"))
        self.assertEqual(mf_bal['percentage'], Decimal("98.03"))

        # Check transactions
        transactions = result['transactions']
        self.assertGreater(len(transactions), 0)
        interest_tx = next(
            (t for t in transactions if t['activity_type'] == 'INTEREST'), None
        )
        self.assertIsNotNone(interest_tx)
        self.assertEqual(interest_tx['date'], date(2025, 9, 2))
        self.assertEqual(interest_tx['credit'], Decimal("0.209"))

        # Check income summary
        income = result['income_summary']
        self.assertEqual(income['dividends_this_month'], Decimal("0.00"))
        self.assertEqual(income['dividends_ytd'], Decimal("0.00"))
        self.assertEqual(income['interest_this_month'], Decimal("0.00"))
        self.assertEqual(income['interest_ytd'], Decimal("0.00"))
        self.assertEqual(income['total_this_month'], Decimal("0.00"))
        self.assertEqual(income['total_ytd'], Decimal("0.00"))

        # Check contribution summary
        contribution = result['contribution_summary']
        self.assertEqual(contribution['first_60_days'], Decimal("0.00"))
        self.assertEqual(contribution['balance_of_year'], Decimal("0.00"))

    def test_parse_multi_security_pdf(self):
        """Test 2: Parse synthetic PDF with multiple securities, buys, sells, dividends."""
        result = parse_rbc_pdf(MULTI_SECURITY_PDF_TEXT)

        self.assertEqual(result['statement_date'], date(2025, 10, 31))
        self.assertEqual(result['last_statement_date'], date(2025, 9, 30))
        self.assertEqual(result['account_number'], "123-45678-9-0")
        self.assertEqual(result['fx_rate'], Decimal("1.40000"))

        # Check balances
        balances = result['balances']
        self.assertGreater(len(balances), 0)
        cash_bal = next((b for b in balances if b['account_type'] == 'Cash'), None)
        self.assertIsNotNone(cash_bal)
        self.assertEqual(cash_bal['market_value'], Decimal("100.00"))

        # Check income summary
        income = result['income_summary']
        self.assertEqual(income['dividends_this_month'], Decimal("50.00"))
        self.assertEqual(income['dividends_ytd'], Decimal("500.00"))
        self.assertEqual(income['interest_this_month'], Decimal("10.00"))
        self.assertEqual(income['interest_ytd'], Decimal("100.00"))
        self.assertEqual(income['total_this_month'], Decimal("60.00"))
        self.assertEqual(income['total_ytd'], Decimal("600.00"))

        # Check contribution summary
        contribution = result['contribution_summary']
        self.assertEqual(contribution['first_60_days'], Decimal("500.00"))
        self.assertEqual(contribution['balance_of_year'], Decimal("500.00"))

        # Check transactions
        transactions = result['transactions']
        self.assertGreater(len(transactions), 0)
        buy_tx = next((t for t in transactions if t['activity_type'] == 'BUY'), None)
        self.assertIsNotNone(buy_tx)
        self.assertEqual(buy_tx['debit'], Decimal("500.00"))
        self.assertEqual(buy_tx['quantity'], Decimal("10.00"))

        sell_tx = next((t for t in transactions if t['activity_type'] == 'SELL'), None)
        self.assertIsNotNone(sell_tx)
        self.assertEqual(sell_tx['credit'], Decimal("400.00"))

        div_tx = next((t for t in transactions if t['activity_type'] == 'DIVIDEND'), None)
        self.assertIsNotNone(div_tx)
        self.assertEqual(div_tx['credit'], Decimal("15.00"))

    def test_missing_sections(self):
        """Test 3: Parse PDF text omitting Income Summary and Contribution Summary.
        Verify those sections return empty dicts without raising."""
        result = parse_rbc_pdf(MISSING_SECTIONS_PDF_TEXT)

        self.assertEqual(result['statement_date'], date(2025, 11, 30))
        self.assertEqual(result['last_statement_date'], date(2025, 10, 31))

        # Income summary should return defaults
        income = result['income_summary']
        self.assertEqual(income['dividends_this_month'], Decimal("0.00"))
        self.assertEqual(income['dividends_ytd'], Decimal("0.00"))
        self.assertEqual(income['interest_this_month'], Decimal("0.00"))
        self.assertEqual(income['interest_ytd'], Decimal("0.00"))
        self.assertEqual(income['total_this_month'], Decimal("0.00"))
        self.assertEqual(income['total_ytd'], Decimal("0.00"))

        # Contribution summary should return defaults
        contribution = result['contribution_summary']
        self.assertEqual(contribution['first_60_days'], Decimal("0.00"))
        self.assertEqual(contribution['balance_of_year'], Decimal("0.00"))

        # Balances should still parse
        self.assertGreater(len(result['balances']), 0)

        # Transactions should still parse
        self.assertGreater(len(result['transactions']), 0)

    def test_invalid_pdf(self):
        """Test 4: Pass a non-PDF file. Should handle gracefully without crashing."""
        fd, path = tempfile.mkstemp(suffix='.pdf')
        with os.fdopen(fd, 'wb') as f:
            f.write(b"This is not a valid PDF file, just plain text.")

        try:
            # Should not raise an error - parser should handle non-PDF files gracefully
            result = parse_rbc_pdf(path)
            # Result should have empty or minimal data since there's no valid content
            self.assertIsNotNone(result)
        finally:
            os.unlink(path)

    @staticmethod
    def _mock_vl_section():
        """One valid monthly VL parse section (CAD)."""
        return {
            'statement_date': date(2025, 9, 30),
            'last_statement_date': date(2025, 8, 29),
            'account_number': '900-00003-1-8',
            'currency': 'CAD',
            'fx_rate': Decimal('1.000000'),
            'begin_total_market_value': Decimal('1057.19'),
            'end_total_market_value': Decimal('1057.19'),
            'transactions': [],
            'balances': [
                {
                    'account_type': 'Cash',
                    'opening': Decimal('20.85'),
                    'closing': Decimal('20.85'),
                    'market_value': Decimal('20.85'),
                    'percentage': Decimal('1.97'),
                },
            ],
        }

    def test_management_command_dry_run(self):
        """Test 5: Run manage_inbox_import with --dry-run.
        Verify no DB writes occur."""
        account = Account.objects.create(
            name="Test Account",
            broker="RBC")

        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir = os.path.join(tmpdir, 'processed')
            os.makedirs(inbox_dir)
            # Filename matches the monthly heuristic so no VL call is needed.
            pdf_path = os.path.join(inbox_dir, 'Monthly Statement 2025-09.pdf')
            with open(pdf_path, 'w') as f:
                f.write(SAMPLE_RBC_PDF_TEXT)

            with patch(
                'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl'
            ) as mock_vl:
                mock_vl.return_value = [self._mock_vl_section()]
                with override_settings(
                    IMPORT_PROCESSED_DIR=processed_dir,
                    IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
                ):
                    call_command(
                        'manage_inbox_import', '--inbox-dir', inbox_dir, '--dry-run'
                    )

            # No RbcMonthly should have been created
            self.assertEqual(RbcMonthly.objects.count(), 0)

        account.delete()

    def test_duplicate_detection(self):
        """Test 6: Import the same PDF twice via manage_inbox_import.
        Verify the second import is skipped with a warning."""
        # Fingerprint of the number in _mock_vl_section; the raw number is
        # never stored on the Account row.
        account = Account.objects.create(
            name="Test Duplicate Account",
            broker="RBC",
            fingerprint=fingerprint_account_number("900-00003-1-8"),
            last_4="0003")

        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir = os.path.join(tmpdir, 'processed')
            os.makedirs(inbox_dir)
            pdf_name = 'Monthly Statement 2025-09.pdf'

            def _drop_pdf():
                with open(os.path.join(inbox_dir, pdf_name), 'w') as f:
                    f.write(SAMPLE_RBC_PDF_TEXT)

            with patch(
                'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl'
            ) as mock_vl:
                # Mock the VL parser to return valid parsed data
                mock_vl.return_value = [self._mock_vl_section()]
                with override_settings(
                    IMPORT_PROCESSED_DIR=processed_dir,
                    IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
                ):
                    # First import
                    _drop_pdf()
                    call_command('manage_inbox_import', '--inbox-dir', inbox_dir)
                    self.assertEqual(RbcMonthly.objects.count(), 1)

                    # Second import of the same PDF - should be skipped
                    _drop_pdf()
                    call_command('manage_inbox_import', '--inbox-dir', inbox_dir)
                    self.assertEqual(RbcMonthly.objects.count(), 1)

        account.delete()
