"""Tests for Vision-Language (VL) PDF parsers."""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from imports.llm_client import VLParseError


class ParseRbcMonthlyVLTest(TestCase):
    """Tests for parse_rbc_monthly_vl (hard error on VL failure — no fallback)."""

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
    def test_vl_failure_raises_without_regex_fallback(self, mock_client_class,
                                                      mock_render):
        """When the VL model fails, parse_rbc_monthly_vl re-raises — there is
        no regex fallback (plan vl-fallback-checks, step 4)."""
        mock_render.return_value = ["fake_image_b64"]
        mock_client_class.side_effect = VLParseError("VL model error")

        from imports.parsers_monthly_vl import parse_rbc_monthly_vl

        with self.assertRaises(VLParseError):
            parse_rbc_monthly_vl("/fake/path.pdf")


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


class OutOfWindowDropTest(TestCase):
    """Plan vl-statement-rows fix 1: transactions dated outside the
    statement's own window (future-settlement trades, misread years) are
    dropped; only the count is kept — never dates or descriptions."""

    @staticmethod
    def _raw(statement_date, last_statement_date, txs):
        return {
            'account_number': '12345',
            'statement_date': statement_date,
            'last_statement_date': last_statement_date,
            'sections': [{
                'currency': 'CAD',
                'begin_total_market_value': 1000.00,
                'end_total_market_value': 1000.00,
                'balances': [{'account_type': 'Cash', 'opening': 50.00,
                              'closing': 50.00, 'market_value': 50.00}],
                'holdings': [],
                'transactions': txs,
            }],
        }

    @staticmethod
    def _bought(tx_date):
        return {
            'date': tx_date, 'activity_type': 'BOUGHT',
            'description': 'FUTURE SETTLEMENT TRADE', 'symbol': 'TST',
            'quantity': 16, 'price': 51.380, 'debit': 832.03, 'credit': 0,
            'commission': 0, 'notes': '',
        }

    def test_future_settlement_row_dropped_and_gate_passes(self):
        """A DEC. 02 BOUGHT on a Nov 30 2020 statement (last Oct 31) is
        dropped, the count is 1 and the cash gate passes."""
        from core.utils.import_checks import cash_gate_details
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw('2020-11-30', '2020-10-31',
                        [self._bought('2020-12-02')])
        section = _coerce_response(raw, '/test.pdf')[0]
        self.assertEqual(section['transactions'], [])
        self.assertEqual(section['out_of_window_dropped'], 1)
        self.assertTrue(cash_gate_details(section)['ok'])

    def test_same_year_january_misread_on_december_statement_dropped(self):
        """A Jan 02 row read as the same year on a Dec 31 statement is
        before the window start and dropped."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw('2020-12-31', '2020-11-30',
                        [self._bought('2020-01-02')])
        section = _coerce_response(raw, '/test.pdf')[0]
        self.assertEqual(section['transactions'], [])
        self.assertEqual(section['out_of_window_dropped'], 1)

    def test_in_window_row_kept(self):
        """A row inside the window is kept and the count stays 0."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw('2020-11-30', '2020-10-31',
                        [self._bought('2020-11-15')])
        section = _coerce_response(raw, '/test.pdf')[0]
        self.assertEqual(len(section['transactions']), 1)
        self.assertEqual(section['out_of_window_dropped'], 0)

    def test_no_last_statement_date_uses_100_day_bound(self):
        """Without last_statement_date the lower bound is statement_date −
        100 days (Q1): Dec 31 → Sep 2, so a Sep 01 row is dropped and an
        Oct 01 row is kept."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw('2020-12-31', None,
                        [self._bought('2020-09-01'),
                         self._bought('2020-10-01')])
        section = _coerce_response(raw, '/test.pdf')[0]
        self.assertEqual(len(section['transactions']), 1)
        self.assertEqual(section['transactions'][0]['date'].isoformat(),
                         '2020-10-01')
        self.assertEqual(section['out_of_window_dropped'], 1)


class NrtWithholdingTest(TestCase):
    """Plan vl-statement-rows fix 2: a DIVIDEND row can carry the gross
    amount as CREDIT and the withheld tax (NRT) as DEBIT on the same row.
    Both amounts must survive coercion, and the cash gate nets them."""

    def test_dividend_keeps_gross_credit_and_nrt_debit(self):
        from core.utils.import_checks import cash_gate_details
        from imports.parsers_monthly_vl import _coerce_response

        raw = {
            'account_number': '12345',
            'statement_date': '2021-03-31',
            'last_statement_date': '2021-02-28',
            'sections': [{
                'currency': 'USD',
                'begin_total_market_value': 1000.00,
                'end_total_market_value': 1055.66,
                'balances': [{'account_type': 'Cash', 'opening': 100.00,
                              'closing': 155.66, 'market_value': 155.66}],
                'holdings': [],
                'transactions': [{
                    'date': '2021-03-15', 'activity_type': 'DIVIDEND',
                    'description': 'US FUND DIVIDEND NRT', 'symbol': 'USF',
                    'quantity': 0, 'price': 0,
                    'debit': 9.82, 'credit': 65.48,
                    'commission': 0, 'notes': '',
                }],
            }],
        }
        section = _coerce_response(raw, '/test.pdf')[0]
        tx = section['transactions'][0]
        # No zeroing or swapping: gross credit and NRT debit both survive.
        self.assertEqual(tx['activity_type'], 'DIVIDEND')
        self.assertEqual(tx['credit'], Decimal('65.48'))
        self.assertEqual(tx['debit'], Decimal('9.82'))
        # The cash gate nets them: 100.00 + 65.48 - 9.82 = 155.66.
        gate = cash_gate_details(section)
        self.assertTrue(gate['ok'])
        self.assertEqual(gate['difference'], Decimal('0.00'))


class UsSavingsLabelTest(TestCase):
    """Plan vl-statement-rows fix 4: US sections print
    RBC U.S INVESTMENT SAVINGS — the savings rule matches
    INVESTMENT SAVINGS so US savings interest stays INTEREST_RECEIVED."""

    def test_us_savings_interest_row_is_interest_received(self):
        from imports.parsers_monthly_vl import normalize_activity_type

        result = normalize_activity_type(
            'INTEREST',
            'RBC U.S INVESTMENT SAVINGS ACCOUNT SR A (2014) AS OF 03/31/21 '
            'REINVEST @ $10.00',
        )
        self.assertEqual(result, 'INTEREST_RECEIVED')

    def test_us_savings_coercion_clears_amounts_not_div_rein(self):
        """A US savings row whose description mentions reinvestment stays
        INTEREST_RECEIVED (not DIV REIN) with debit/credit cleared."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = {
            'account_number': '12345',
            'statement_date': '2021-03-31',
            'last_statement_date': '2021-02-28',
            'sections': [{
                'currency': 'USD',
                'begin_total_market_value': 1000.00,
                'end_total_market_value': 1000.00,
                'balances': [],
                'holdings': [],
                'transactions': [{
                    'date': '2021-03-31', 'activity_type': 'INTEREST',
                    'description': 'RBC U.S INVESTMENT SAVINGS ACCOUNT '
                                   'SR A (2014) AS OF 03/31/21 REINVEST @ $10.00',
                    'symbol': 'RBF2014',
                    'quantity': 2.067, 'price': 10,
                    'debit': 0, 'credit': 0,
                    'commission': 0, 'notes': '',
                }],
            }],
        }
        tx = _coerce_response(raw, '/test.pdf')[0]['transactions'][0]
        self.assertEqual(tx['activity_type'], 'INTEREST_RECEIVED')
        self.assertEqual(tx['debit'], Decimal('0'))
        self.assertEqual(tx['credit'], Decimal('0'))

    def test_bought_of_us_savings_keeps_bought_and_debit(self):
        """Extra D: a real purchase of the US savings fund keeps BOUGHT and
        its debit — the widened match must not recode it."""
        from imports.parsers_monthly_vl import (
            _coerce_response, normalize_activity_type,
        )

        self.assertEqual(
            normalize_activity_type('BOUGHT', 'RBC U.S INVESTMENT SAVINGS'),
            'BOUGHT',
        )
        raw = {
            'account_number': '12345',
            'statement_date': '2021-03-31',
            'last_statement_date': '2021-02-28',
            'sections': [{
                'currency': 'USD',
                'begin_total_market_value': 1000.00,
                'end_total_market_value': 1000.00,
                'balances': [],
                'holdings': [],
                'transactions': [{
                    'date': '2021-03-15', 'activity_type': 'BOUGHT',
                    'description': 'RBC U.S INVESTMENT SAVINGS',
                    'symbol': 'RBF2014',
                    'quantity': 100, 'price': 10,
                    'debit': 1000.00, 'credit': 0,
                    'commission': 0, 'notes': '',
                }],
            }],
        }
        tx = _coerce_response(raw, '/test.pdf')[0]['transactions'][0]
        self.assertEqual(tx['activity_type'], 'BOUGHT')
        self.assertEqual(tx['debit'], Decimal('1000.00'))
        self.assertEqual(tx['credit'], Decimal('0'))


class PostProcessingTest(TestCase):
    """Tests for transaction post-processing rules in the VL parser."""

    def test_tfr_out_clears_quantity(self):
        """TFR OUT should have quantity=0; the printed amounts stay in their
        columns (plan vl-interest-columns: no more credit->debit move — a
        misread column is caught by the cash gate)."""
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
                    'credit': Decimal('27103.00'),
                    'commission': Decimal('0'),
                    'notes': '',
                }],
            }],
        }
        result = _coerce_response(raw, '/test.pdf')
        tx = result[0]['transactions'][0]
        self.assertEqual(tx['quantity'], Decimal('0'))
        # Amounts untouched: the credit stays in the credit column.
        self.assertEqual(tx['debit'], Decimal('0'))
        self.assertEqual(tx['credit'], Decimal('27103.00'))

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
