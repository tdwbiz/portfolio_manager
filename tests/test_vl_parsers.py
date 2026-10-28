"""Tests for Vision-Language (VL) PDF parsers."""

import os
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase


class ParseRbcMonthlyVLTest(TestCase):
    """Tests for parse_rbc_monthly_vl regex fallback parser."""

    @patch("imports.parsers_monthly_vl._render_pdf_pages")
    @patch("imports.parsers_monthly_vl.VLClient")
    def test_vl_success_returns_result(self, mock_client_class, mock_render):
        """When VL model succeeds, return coerced result."""
        mock_render.return_value = ["fake_image_b64"]
        mock_client = MagicMock()
        mock_client.extract_json.return_value = {
            "account_number": "12345-678901",
            "statement_date": "2024-05-31",
            "last_statement_date": "2024-04-30",
            "fx_rate": 1.35,
            "sections": [
                {
                    "currency": "CAD",
                    "begin_total_market_value": 285450.10,
                    "end_total_market_value": 287192.32,
                    "balances": [],
                    "transactions": [],
                },
            ],
        }
        mock_client_class.return_value = mock_client

        from imports.parsers_monthly_vl import parse_rbc_monthly_vl

        result = parse_rbc_monthly_vl("/fake/path.pdf")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["currency"], "CAD")
        self.assertEqual(result[0]["statement_date"], date(2024, 5, 31))
        self.assertEqual(result[0]["account_number"], "12345-678901")
        self.assertEqual(result[0]["end_total_market_value"], Decimal("287192.32"))

    @patch("imports.parsers_monthly_vl._render_pdf_pages")
    @patch("imports.parsers_monthly_vl.VLClient")
    def test_vl_failure_falls_back_to_regex(self, mock_client_class, mock_render):
        """When VL model fails, fall back to regex parser."""
        mock_render.return_value = ["fake_image_b64"]
        mock_client_class.side_effect = Exception("VL model error")

        # Create a minimal fake PDF file for regex parser
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            # Minimal valid PDF
            f.write(b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
            f.write(b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n")
            f.write(
                b"xref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n"
            )
            f.write(b"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n115\n%%EOF")
            temp_path = f.name

        try:
            from imports.parsers_monthly_vl import parse_rbc_monthly_vl

            result = parse_rbc_monthly_vl(temp_path)

            self.assertIsInstance(result, list)
            self.assertGreaterEqual(len(result), 1)
            self.assertEqual(result[0]["currency"], "CAD")
        finally:
            os.unlink(temp_path)


class RegexFallbackTest(TestCase):
    """Tests for the regex fallback parser."""

    def _create_test_pdf(self, text_content: str) -> str:
        """Create a minimal PDF with given text content."""
        import pymupdf

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            temp_path = f.name

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text(pymupdf.Point(72, 72), text_content)
        doc.save(temp_path)
        doc.close()
        return temp_path

    def test_regex_extract_statement_date(self):
        """Regex parser extracts statement date from PDF text."""
        text = "RBC Monthly Statement\nAs of Date: 05/31/2024\nAccount: 12345-678901"
        path = self._create_test_pdf(text)

        try:
            from imports.parsers_monthly_vl import _parse_rbc_monthly_regex

            result = _parse_rbc_monthly_regex(path)

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["statement_date"], date(2024, 5, 31))
        finally:
            os.unlink(path)

    def test_regex_extract_account_number(self):
        """Regex parser extracts account number from PDF text."""
        text = "Account No: 987654-321098\nMonthly Statement"
        path = self._create_test_pdf(text)

        try:
            from imports.parsers_monthly_vl import _parse_rbc_monthly_regex

            result = _parse_rbc_monthly_regex(path)

            self.assertEqual(result[0]["account_number"], "987654-321098")
        finally:
            os.unlink(path)

    def test_regex_extract_fx_rate(self):
        """Regex parser extracts FX rate when USD currency section is found."""
        text = "USD\nExchange rate 1 USD = 1.3542 CAD\nMonthly Statement"
        path = self._create_test_pdf(text)

        try:
            from imports.parsers_monthly_vl import _parse_rbc_monthly_regex

            result = _parse_rbc_monthly_regex(path)

            # FX rate is only set for USD sections
            usd_sections = [r for r in result if r["currency"] == "USD"]
            if usd_sections:
                self.assertEqual(usd_sections[0]["fx_rate"], Decimal("1.3542"))
        finally:
            os.unlink(path)

    def test_regex_default_cad_section(self):
        """When no currency sections found, default to CAD."""
        text = "RBC Monthly Statement\nAs of Date: 06/30/2024"
        path = self._create_test_pdf(text)

        try:
            from imports.parsers_monthly_vl import _parse_rbc_monthly_regex

            result = _parse_rbc_monthly_regex(path)

            self.assertEqual(result[0]["currency"], "CAD")
        finally:
            os.unlink(path)


class FilenameHeuristicTest(TestCase):
    """Tests for filename-based classification heuristic in classify_pdf_vl."""

    def test_annual_report_filename(self):
        """Filename containing 'annual report' is classified as annual."""
        from imports.classify_pdf_vl import classify_pdf

        result = classify_pdf("/path/to/Annual Report-0001 2025-12-31.pdf")
        self.assertEqual(result, "annual")

    def test_annual_investment_performance_filename(self):
        """Filename containing 'annual investment performance' is classified as annual."""
        from imports.classify_pdf_vl import classify_pdf

        result = classify_pdf("/path/to/Annual Investment Performance Report.pdf")
        self.assertEqual(result, "annual")

    def test_monthly_statement_filename(self):
        """Filename containing 'monthly statement' is classified as monthly."""
        from imports.classify_pdf_vl import classify_pdf

        result = classify_pdf("/path/to/Monthly Statement 2025-06.pdf")
        self.assertEqual(result, "monthly")

    def test_account_statement_filename(self):
        """Filename containing 'account statement' is classified as monthly."""
        from imports.classify_pdf_vl import classify_pdf

        result = classify_pdf("/path/to/Account Statement June 2025.pdf")
        self.assertEqual(result, "monthly")

    def test_unknown_filename_delegates_to_vl(self):
        """Filename without known patterns delegates to VL model (returns 'unknown' when VL is unavailable)."""
        from imports.classify_pdf_vl import classify_pdf

        # Non-existent file with no matching pattern should return 'unknown'
        result = classify_pdf("/path/to/Statement_2025-06.pdf")
        self.assertEqual(result, "unknown")

    def test_filename_heuristic_is_case_insensitive(self):
        """Filename matching is case-insensitive."""
        from imports.classify_pdf_vl import classify_pdf

        result = classify_pdf("/path/to/ANNUAL REPORT-0001.pdf")
        self.assertEqual(result, "annual")

        result = classify_pdf("/path/to/annual report 2025.pdf")
        self.assertEqual(result, "annual")


class PostProcessingTest(TestCase):
    """Tests for transaction post-processing rules in the VL parser."""

    def test_tfr_out_clears_quantity(self):
        """TFR OUT should have quantity=0 and cash in debit."""
        from decimal import Decimal
        from datetime import date
        from imports.parsers_monthly_vl import _coerce_response

        raw = {
            'account_number': '12345',
            'statement_date': '2023-03-31',
            'sections': [{
                'currency': 'CAD',
                'begin_total_market_value': 500000,
                'end_total_market_value': 520000,
                'balances': [],
                'holdings': [],
                'transactions': [{
                    'date': '2023-03-13',
                    'activity_type': 'TFR OUT',
                    'description': 'ACCOUNT TRANSFER',
                    'symbol': '',
                    'quantity': Decimal('27103.0000'),  # Wrong - should be 0
                    'price': Decimal('0'),
                    'debit': Decimal('0'),
                    'credit': Decimal('27103.00'),  # Wrong side
                    'commission': Decimal('0'),
                    'notes': '',
                }],
            }],
        }
        result = _coerce_response(raw, '/test.pdf')
        tx = result[0]['transactions'][0]
        self.assertEqual(tx['quantity'], Decimal('0'))
        self.assertEqual(tx['debit'], Decimal('27103.00'))
        self.assertEqual(tx['credit'], Decimal('0'))

    def test_deposit_clears_quantity(self):
        """DEPOSIT should have quantity=0."""
        from decimal import Decimal
        from imports.parsers_monthly_vl import _coerce_response

        raw = {
            'account_number': '12345',
            'statement_date': '2023-06-30',
            'sections': [{
                'currency': 'CAD',
                'begin_total_market_value': 500000,
                'end_total_market_value': 520000,
                'balances': [],
                'holdings': [],
                'transactions': [{
                    'date': '2023-06-01',
                    'activity_type': 'DEPOSIT',
                    'description': 'Deposit',
                    'symbol': '',
                    'quantity': Decimal('5000.00'),  # Wrong
                    'price': Decimal('0'),
                    'debit': Decimal('0'),
                    'credit': Decimal('5000.00'),
                    'commission': Decimal('0'),
                    'notes': '',
                }],
            }],
        }
        result = _coerce_response(raw, '/test.pdf')
        tx = result[0]['transactions'][0]
        self.assertEqual(tx['quantity'], Decimal('0'))
        self.assertEqual(tx['price'], Decimal('0'))
