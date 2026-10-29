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

    def test_no_last_statement_date_uses_160_day_bound(self):
        """Without last_statement_date the lower bound is statement_date −
        160 days (plan vl-window-backdated; deliberately wider than the
        1–100-day statement_date_problems gap): Dec 31 2020 → Jul 24 2020,
        so a Jul 23 row is dropped and a Jul 25 row is kept."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw('2020-12-31', None,
                        [self._bought('2020-07-23'),
                         self._bought('2020-07-25')])
        section = _coerce_response(raw, '/test.pdf')[0]
        self.assertEqual(len(section['transactions']), 1)
        self.assertEqual(section['transactions'][0]['date'].isoformat(),
                         '2020-07-25')
        self.assertEqual(section['out_of_window_dropped'], 1)

    def test_backdated_rows_kept_and_gate_passes(self):
        """Plan vl-window-backdated: RBC prints back-dated rows inside a
        monthly statement (the Nov 30 2018 spin-off statement lists two
        OCT. 29 rows "AS OF 10/29/18"). Both survive the widened window
        (last_statement_date − 60 days); only the CASH-LIEU row is kept —
        the 0/0 spin-off DIST row is skipped by the existing zero-amount
        rule (tdw, Oct 29) — and the cash gate passes:
        698.97 + 10.96 = 709.93."""
        from core.utils.import_checks import cash_gate_details
        from imports.parsers_monthly_vl import _coerce_response

        raw = {
            'account_number': '12345',
            'statement_date': '2018-11-30',
            'last_statement_date': '2018-10-31',
            'sections': [{
                'currency': 'CAD',
                'begin_total_market_value': 1000.00,
                'end_total_market_value': 1000.00,
                'balances': [{'account_type': 'Cash', 'opening': 698.97,
                              'closing': 709.93, 'market_value': 698.97}],
                'holdings': [],
                'transactions': [
                    {'date': '2018-10-29', 'activity_type': 'DISTRIB',
                     'description': ('DISTRIB 20 SHS RESIDEO FROM HONEYWELL '
                                     'AS OF 10/29/18'),
                     'symbol': '', 'quantity': 20, 'price': 0,
                     'debit': 0, 'credit': 0,
                     'commission': 0, 'notes': ''},
                    {'date': '2018-10-29', 'activity_type': 'CASH-LIEU',
                     'description': ('CASH-LIEU FOR FRACTIONAL SHARE '
                                     'AS OF 10/29/18'),
                     'symbol': '', 'quantity': 0, 'price': 0,
                     'debit': 0, 'credit': 10.96,
                     'commission': 0, 'notes': ''},
                ],
            }],
        }
        section = _coerce_response(raw, '/test.pdf')[0]
        # Only the CASH-LIEU row is kept: the spin-off row labels DIST
        # (partial match) and has zero debit and credit, so it is skipped
        # by the existing zero-amount rule. Keeping zero-cash security rows
        # (spin-offs, DLR journals) is a later step.
        self.assertEqual(len(section['transactions']), 1)
        tx = section['transactions'][0]
        self.assertEqual(tx['activity_type'], 'OTHER')
        self.assertEqual(tx['credit'], Decimal('10.96'))
        self.assertEqual(tx['debit'], Decimal('0'))
        self.assertEqual(section['out_of_window_dropped'], 0)
        self.assertTrue(cash_gate_details(section)['ok'])


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


class InterestAndDeregistrationTest(TestCase):
    """Plan vl-interest-columns: amounts stay in their printed columns;
    interest is scoped to savings-fund rows; DEREGISTRATION credits are
    TRANSFER. Synthetic fixtures only."""

    @staticmethod
    def _raw(txs, opening, closing, currency='CAD'):
        return {
            'account_number': '12345',
            'statement_date': '2021-08-31',
            'last_statement_date': '2021-07-31',
            'sections': [{
                'currency': currency,
                'begin_total_market_value': 1000.00,
                'end_total_market_value': 1000.00,
                'balances': [{'account_type': 'Cash', 'opening': opening,
                              'closing': closing, 'market_value': closing}],
                'holdings': [],
                'transactions': txs,
            }],
        }

    def test_deregistration_credit_is_transfer_and_gate_passes(self):
        """A DEREGISTRATION FROM ACCOUNT row labelled WITHDRAWAL with the
        amount in CREDIT is coerced to TRANSFER, the credit is untouched,
        and the cash gate passes (opening + 4500.00 = closing)."""
        from core.utils.import_checks import cash_gate_details
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw([{
            'date': '2021-08-10', 'activity_type': 'WITHDRAWAL',
            'description': 'DEREGISTRATION FROM ACCOUNT 1234567',
            'symbol': '', 'quantity': 0, 'price': 0,
            'debit': 0, 'credit': 4500.00, 'commission': 0, 'notes': '',
        }], opening=100.00, closing=4600.00)
        section = _coerce_response(raw, '/test.pdf')[0]
        tx = section['transactions'][0]
        self.assertEqual(tx['activity_type'], 'TRANSFER')
        self.assertEqual(tx['credit'], Decimal('4500.00'))
        self.assertEqual(tx['debit'], Decimal('0'))
        gate = cash_gate_details(section)
        self.assertTrue(gate['ok'])
        self.assertEqual(gate['difference'], Decimal('0.00'))

    def test_int_fr_debit_is_interest_paid_and_gate_passes(self):
        """Cash-balance interest charged on a negative balance: raw type
        INTEREST with the amount in DEBIT becomes INTEREST_PAID, the debit
        is kept, and the cash gate passes (306.67 - 97.28 = 209.39)."""
        from core.utils.import_checks import cash_gate_details
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw([{
            'date': '2021-08-31', 'activity_type': 'INTEREST',
            'description': 'INT FR 07/22 THRU08/21 @20 3/4% BAL 306- '
                           'AVBAL 5,519',
            'symbol': '', 'quantity': 0, 'price': 0,
            'debit': 97.28, 'credit': 0, 'commission': 0, 'notes': '',
        }], opening=306.67, closing=209.39)
        section = _coerce_response(raw, '/test.pdf')[0]
        tx = section['transactions'][0]
        self.assertEqual(tx['activity_type'], 'INTEREST_PAID')
        self.assertEqual(tx['debit'], Decimal('97.28'))
        self.assertEqual(tx['credit'], Decimal('0'))
        gate = cash_gate_details(section)
        self.assertTrue(gate['ok'])
        self.assertEqual(gate['difference'], Decimal('0.00'))

    def test_int_fr_credit_stays_interest_received_and_gate_passes(self):
        """The same INT FR wording with the amount in CREDIT stays
        INTEREST_RECEIVED, the credit is kept, and the gate passes."""
        from core.utils.import_checks import cash_gate_details
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw([{
            'date': '2021-08-31', 'activity_type': 'INTEREST',
            'description': 'INT FR 07/22 THRU08/21 @20 3/4% BAL 5,519 '
                           'AVBAL 5,531',
            'symbol': '', 'quantity': 0, 'price': 0,
            'debit': 0, 'credit': 12.34, 'commission': 0, 'notes': '',
        }], opening=306.67, closing=319.01)
        section = _coerce_response(raw, '/test.pdf')[0]
        tx = section['transactions'][0]
        self.assertEqual(tx['activity_type'], 'INTEREST_RECEIVED')
        self.assertEqual(tx['credit'], Decimal('12.34'))
        self.assertEqual(tx['debit'], Decimal('0'))
        gate = cash_gate_details(section)
        self.assertTrue(gate['ok'])
        self.assertEqual(gate['difference'], Decimal('0.00'))

    def test_savings_fund_row_clears_price_and_amounts(self):
        """An INVESTMENT SAVINGS row with qty/price 10 and printed
        debit/credit stays INTEREST_RECEIVED with price, debit and credit
        cleared (net-zero reinvested shares)."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw([{
            'date': '2021-08-31', 'activity_type': 'INTEREST',
            'description': 'RBC INVESTMENT SAVINGS ACCOUNT SR A (2010) '
                           'AS OF 08/31/21 REINV@C$10.00',
            'symbol': 'RBF2010',
            'quantity': 2.067, 'price': 10,
            'debit': 20.67, 'credit': 20.67, 'commission': 0, 'notes': '',
        }], opening=100.00, closing=100.00)
        tx = _coerce_response(raw, '/test.pdf')[0]['transactions'][0]
        self.assertEqual(tx['activity_type'], 'INTEREST_RECEIVED')
        self.assertEqual(tx['quantity'], Decimal('2.067'))
        self.assertEqual(tx['price'], Decimal('0'))
        self.assertEqual(tx['debit'], Decimal('0'))
        self.assertEqual(tx['credit'], Decimal('0'))

    def test_bought_and_sold_keep_printed_columns(self):
        """A BOUGHT row with the amount in CREDIT and a SOLD row with the
        amount in DEBIT keep their printed columns untouched (the cash gate
        catches a column VL read wrongly)."""
        from imports.parsers_monthly_vl import _coerce_response

        raw = self._raw([
            {'date': '2021-08-05', 'activity_type': 'BOUGHT',
             'description': 'BOUGHT 100 SHS TST', 'symbol': 'TST',
             'quantity': 100, 'price': 10.00,
             'debit': 0, 'credit': 1000.00, 'commission': 0, 'notes': ''},
            {'date': '2021-08-20', 'activity_type': 'SOLD',
             'description': 'SOLD 50 SHS TST', 'symbol': 'TST',
             'quantity': 50, 'price': 9.00,
             'debit': 450.00, 'credit': 0, 'commission': 0, 'notes': ''},
        ], opening=100.00, closing=100.00)
        txs = _coerce_response(raw, '/test.pdf')[0]['transactions']
        bought, sold = txs
        self.assertEqual(bought['activity_type'], 'BOUGHT')
        self.assertEqual(bought['credit'], Decimal('1000.00'))
        self.assertEqual(bought['debit'], Decimal('0'))
        self.assertEqual(sold['activity_type'], 'SOLD')
        self.assertEqual(sold['debit'], Decimal('450.00'))
        self.assertEqual(sold['credit'], Decimal('0'))


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
