"""Tests for RBC and IB CSV parsers."""

import csv
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from core.models import Account, TransactionType
from core.utils.account_fingerprint import fingerprint_account_number
from imports.parsers import parse_rbc_csv, parse_ib_csv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(rows: list[list[str]]) -> str:
    """Write CSV rows to a temp file and return the path.

    Uses csv.writer so fields containing commas (dates, amounts) are quoted,
    matching the real RBC/IB exports.
    """
    fd, path = tempfile.mkstemp(suffix='.csv')
    with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerows(rows)
    return path


def _write_text(text: str) -> str:
    """Write raw CSV text to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix='.csv')
    with os.fdopen(fd, 'w', newline='', encoding='utf-8-sig') as f:
        f.write(text)
    return path


# ---------------------------------------------------------------------------
# RBC CSV Parser
# ---------------------------------------------------------------------------

class RbcCsvParserTests(TestCase):
    """Test RBC CSV parsing."""

    def setUp(self) -> None:
        # Fake account 90000001 (ACCOUNT_A) with a stored fingerprint — the
        # parser resolves rows by fingerprint, never by the raw number.
        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001")

    def test_parses_buy_with_settlement_date(self):
        """BUY with settlement date should be parsed."""
        path = _write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.20', 'CAD'],
        ])

        trades, rate, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade['type'], 'BUY')
        self.assertEqual(trade['symbol'], 'VFV')
        self.assertEqual(trade['quantity'], Decimal('10'))
        self.assertEqual(trade['price'], Decimal('166.62'))
        self.assertEqual(trade['amount'], Decimal('-1666.20'))
        self.assertEqual(trade['settlement_date'], date(2025, 10, 10))

    def test_parses_dividend_distribution(self):
        """DIST dividend should be parsed."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Distribution', 'VDY', 'Vanguard ETF',
             '6182', '0.143', 'October 10, 2025', '90000001',
             '886.81', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'DIST')
        self.assertEqual(trades[0]['amount'], Decimal('886.81'))

    def test_parses_dividend_reinvestment(self):
        """Real RBC reinvestments are DIV rows with qty > 0 and negative value."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency', 'Notes'],
            ['October 08, 2025', 'Dividends', 'VFV', 'Vanguard S&P 500',
             '17', '49.56', 'October 10, 2025', '90000001',
             '-842.52', 'CAD', 'REI - REINV@C$49.56'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'DIV')
        self.assertEqual(trades[0]['quantity'], Decimal('17'))
        self.assertEqual(trades[0]['price'], Decimal('49.56'))
        # REI: negative value = cash outflow into shares
        self.assertEqual(trades[0]['amount'], Decimal('-842.52'))
        self.assertIn('REI', trades[0]['notes'])

    def test_parses_interest_received(self):
        """INTEREST_RECEIVED should be parsed."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 01, 2025', 'Interest', 'RBF2010', 'RBF 2010',
             '2.003', '0', 'October 03, 2025', '90000001',
             '0', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'INT')
        self.assertEqual(trades[0]['quantity'], Decimal('2.003'))
        # Interest: no cash flow — amount = 0
        self.assertEqual(trades[0]['amount'], Decimal('0'))

    def test_parses_withdrawal(self):
        """WDRAW withdrawal should be parsed."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Withdrawal', '', 'Withdrawal',
             '0', '0', 'October 10, 2025', '90000001',
             '-500.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'WDRAW')
        self.assertEqual(trades[0]['amount'], Decimal('-500.00'))

    def test_parses_deposit(self):
        """DEP deposit should be parsed."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Deposits & Contributions', '', 'Contribution',
             '0', '0', 'October 10, 2025', '90000001',
             '2000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'DEP')
        self.assertEqual(trades[0]['amount'], Decimal('2000.00'))

    def test_parses_fees_and_taxes(self):
        """FEE and TAX should be parsed."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Fees', '', 'Management Fee',
             '0', '0', 'October 10, 2025', '90000001',
             '-15.00', 'CAD'],
            ['October 08, 2025', 'Taxes', '', 'Tax Withholding',
             '0', '0', 'October 10, 2025', '90000001',
             '-50.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]['type'], 'FEE')
        self.assertEqual(trades[1]['type'], 'TAX')

    def test_parses_negative_amount_as_outflow(self):
        """Negative amount should indicate money out."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '100', '166.62', 'October 10, 2025', '90000001',
             '-16662.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(trades[0]['amount'], Decimal('-16662.00'))
        self.assertTrue(trades[0]['amount'] < 0)

    def test_parses_commission_calculated_from_gross_amount(self):
        """Commission should be calculated as |gross - |amount||."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1702.80', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        gross = Decimal('10') * Decimal('166.62')
        # Commission = |gross - |amount|| = |1666.20 - 1702.80| = 36.60
        self.assertAlmostEqual(trades[0]['commission'], Decimal('36.60'), places=2)

    def test_parses_exchange_rate(self):
        """Exchange rate should be extracted from header."""
        path = _write_csv([
            ['Exchange Rate: 1 USD = 1.3720 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
        ])

        _, rate, _ = parse_rbc_csv(path)
        self.assertEqual(rate, Decimal('1.3720'))

    def test_exchange_rate_default_one_if_not_found(self):
        """Default exchange rate should be 1.0 when not found."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard',
             '10', '100.00', '', '90000001',
             '-1000.00', 'CAD'],
        ])

        _, rate, _ = parse_rbc_csv(path)
        self.assertEqual(rate, Decimal('1.0'))

    def test_skips_practice_accounts(self):
        """Practice accounts should be skipped."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'AAPL', 'Apple',
             '10', '150.00', 'October 10, 2025', 'PRACEF8C2DAE',
             '-1500.00', 'USD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_parses_multiple_trades_in_order(self):
        """Multiple trades should be parsed in file order."""
        path = _write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 01, 2025', 'Distribution', 'VDY', 'Dividend',
             '6182', '0.143', 'October 03, 2025', '90000001',
             '886.81', 'CAD'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.20', 'CAD'],
            ['October 15, 2025', 'Dividends', 'RBF2010', 'RBF 2010',
             '100', '10.00', 'October 17, 2025', '90000001',
             '1000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 3)
        self.assertEqual(trades[0]['symbol'], 'VDY')
        self.assertEqual(trades[1]['symbol'], 'VFV')
        self.assertEqual(trades[2]['symbol'], 'RBF2010')

    def test_date_formats(self):
        """Various date formats should be parsed correctly."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['Oct 08, 2025', 'Buy', 'VFV', 'Test',
             '10', '100.00', 'Oct 10, 2025', '90000001',
             '-1000.00', 'CAD'],
            ['October 08, 2025', 'Buy', 'VDY', 'Test',
             '100', '0.143', 'October 10, 2025', '90000001',
             '14.30', 'CAD'],
            ['08-Oct-25', 'Buy', 'VFV', 'Test',
             '5', '100.00', '10-Oct-25', '90000001',
             '-500.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 3)
        self.assertEqual(trades[0]['date'], date(2025, 10, 8))
        self.assertEqual(trades[1]['date'], date(2025, 10, 8))
        self.assertEqual(trades[2]['date'], date(2025, 10, 8))

    def test_date_parse_error_skipped(self):
        """Invalid date should be logged as warning and skipped."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['NOT A DATE', 'Buy', 'VFV', 'Test',
             '10', '100.00', '', '90000001',
             '-1000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_insufficient_columns_skipped(self):
        """Rows with fewer than 9 columns should be skipped."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV',
             '-1000.00', 'CAD'],  # Only 5 columns
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_empty_activity_skipped(self):
        """Rows with empty activity should be skipped."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', '', 'VFV', 'Test',
             '10', '100.00', '', '90000001',
             '-1000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_numeric_parse_error_skipped(self):
        """Rows with invalid numeric fields should be skipped."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Test',
             'invalid', '100.00', '', '90000001',
             '-1000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_comma_in_amount_stripped(self):
        """Amounts with commas should be handled correctly."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Test',
             '100', '100.00', 'October 10, 2025', '90000001',
             '-100,000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['amount'], Decimal('-100000.00'))

    def test_dash_in_amount_treated_as_zero(self):
        """Dash/dash amounts should be treated as zero."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Test',
             '100', '100.00', 'October 10, 2025', '90000001',
             '--', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['amount'], Decimal('0'))

    def test_unknown_account_number_rejected(self):
        """A row whose number has no stored fingerprint is rejected."""
        path = _write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000099',
             '-1666.20', 'CAD'],
        ])

        rejections = []
        trades, _rate, _acctg = parse_rbc_csv(path, rejections)
        self.assertEqual(len(trades), 0)
        self.assertEqual(len(rejections), 1)
        self.assertIn('Unknown account', rejections[0]['Reason Rejected'])

    def test_empty_file_returns_no_trades(self):
        """Empty file should return no trades."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_unexpected_header_not_treated_as_data(self):
        """A row starting with 'Account' should not be treated as data."""
        path = _write_csv([
            ['Exchange Rate: 1 USD = 1.37 CAD'],
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['Account', 'Something', 'VFV', 'Test',
             '10', '100.00', '', '90000001',
             '-1000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 0)

    def test_activity_type_normalisation(self):
        """Activity type should be normalised to canonical code."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'buy', 'VFV', 'Test',
             '10', '100.00', 'October 10, 2025', '90000001',
             '-1000.00', 'CAD'],
        ])

        trades, _, _ = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'BUY')

    def test_old_nine_column_layout(self):
        """Old 9-column layout (2017–2019) with 31-Dec-18 dates parses."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Quantity', 'Price',
             'Settlement Date', 'Account', 'Value', 'Currency'],
            ['31-Dec-18', 'Buy', 'VFV', '10', '50.00', '02-Jan-19',
             '90000001', '-500.00', 'CAD'],
        ])

        trades, _, accounting = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade['date'], date(2018, 12, 31))
        self.assertEqual(trade['settlement_date'], date(2019, 1, 2))
        self.assertEqual(trade['type'], 'BUY')
        self.assertEqual(accounting['data_rows'], 1)
        self.assertEqual(accounting['imported'], 1)

    def test_new_eleven_column_layout(self):
        """New 11-column layout (Symbol Description + Description) parses."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency', 'Description'],
            ['December 31, 2018', 'Dividends', 'VDY', 'Vanguard Dividend ETF',
             '1000', '0.15', 'January 02, 2019', '90000001',
             '150.00', 'CAD', 'DIV ON 1000 SHS'],
        ])

        trades, _, accounting = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade['date'], date(2018, 12, 31))
        self.assertEqual(trade['settlement_date'], date(2019, 1, 2))
        self.assertEqual(trade['type'], 'DIV')
        self.assertEqual(trade['notes'], 'DIV ON 1000 SHS')
        self.assertEqual(accounting['data_rows'], 1)
        self.assertEqual(accounting['imported'], 1)

    def test_corporate_action_activity_types_map_to_existing_codes(self):
        """Reorganization / Return of Capital / Adjustment map to the codes
        the old auto-create produced (dry-run fix B); notes are kept."""
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency', 'Description'],
            ['October 08, 2025', 'Reorganization', '9XYZABC1', 'Short call',
             '100', '0', 'October 10, 2025', '90000001',
             '0.00', 'CAD', 'EXP - OPTION EXPIRED'],
            ['October 08, 2025', 'Reorganization', 'VFV', 'Vanguard S&P 500',
             '300', '0', 'October 10, 2025', '90000001',
             '0.00', 'CAD', 'DIS - STK SPLIT 4:1'],
            ['October 08, 2025', 'Reorganization', 'VFV', 'Vanguard S&P 500',
             '', '0', 'October 10, 2025', '90000001',
             '1.37', 'CAD', 'CIL - CASH IN LIEU'],
            ['October 08, 2025', 'Reorganization', 'OLDSTK', 'Old security',
             '-100', '0', 'October 10, 2025', '90000001',
             '0.00', 'CAD', 'MER - MERGER'],
            ['October 08, 2025', 'Return of Capital', 'VDY', 'Vanguard ETF',
             '', '0', 'October 10, 2025', '90000001',
             '0.00', 'CAD', 'ROC - BOOK COST ADJ 12.34'],
            ['October 08, 2025', 'Adjustment', 'RBF2010', 'RBF 2010',
             '-2.5', '0', 'October 10, 2025', '90000001',
             '0.00', 'CAD', 'ADJDEL'],
        ])

        rejections = []
        trades, _, accounting = parse_rbc_csv(path, rejections)
        self.assertEqual(len(trades), 6)
        self.assertEqual(rejections, [])
        self.assertEqual(accounting['rejected'], {})
        codes = [t['type'] for t in trades]
        self.assertEqual(codes[:4], ['REORGANIZATION'] * 4)
        self.assertEqual(codes[4], 'RETURN_OF_CAPITAL')
        self.assertEqual(codes[5], 'ADJUSTMENT')
        # Empty quantities → 0; descriptions kept in notes.
        self.assertEqual(trades[0]['quantity'], Decimal('100'))
        self.assertEqual(trades[2]['quantity'], Decimal('0'))
        self.assertEqual(trades[4]['quantity'], Decimal('0'))
        self.assertIn('STK SPLIT', trades[1]['notes'])
        self.assertIn('BOOK COST ADJ', trades[4]['notes'])
        self.assertEqual(trades[5]['notes'], 'ADJDEL')

    def test_footer_counted_separately_eleven_column(self):
        """The RBC disclaimer footer (first cell 'Disclaimer', 7 one-cell
        lines in the 11-column layout) is counted as footer — never data
        rows or rejections (dry-run fix A)."""
        footer = [['Disclaimer']] * 7
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency', 'Description'],
            ['October 08, 2025', 'Buy', 'VFV', 'Vanguard S&P 500',
             '10', '166.62', 'October 10, 2025', '90000001',
             '-1666.20', 'CAD', 'Buy VFV'],
        ] + footer)

        rejections = []
        trades, _, accounting = parse_rbc_csv(path, rejections)
        self.assertEqual(len(trades), 1)
        self.assertEqual(accounting['data_rows'], 1)
        self.assertEqual(accounting['imported'], 1)
        self.assertEqual(accounting['footer'], 7)
        self.assertEqual(accounting['rejected'], {})
        self.assertEqual(rejections, [])

    def test_footer_counted_separately_nine_column(self):
        """The 9-column layout footer starts at 'Disclaimers' (9 lines of
        10 cells) and is counted as footer (dry-run fix A)."""
        footer = [['Disclaimers'] + [''] * 9]
        footer += [[f'Footer line {i}'] + [''] * 9 for i in range(8)]
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Quantity', 'Price',
             'Settlement Date', 'Account', 'Value', 'Currency'],
            ['31-Dec-18', 'Buy', 'VFV', '10', '50.00', '02-Jan-19',
             '90000001', '-500.00', 'CAD'],
        ] + footer)

        trades, _, accounting = parse_rbc_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(accounting['data_rows'], 1)
        self.assertEqual(accounting['imported'], 1)
        self.assertEqual(accounting['footer'], 9)
        self.assertEqual(accounting['rejected'], {})

    def test_unknown_activity_type_rejects_row(self):
        """An activity not in _RBC_TYPE_MAP rejects the row and creates no
        TransactionType (plan step 4)."""
        before = TransactionType.objects.count()
        path = _write_csv([
            ['Date', 'Activity', 'Symbol', 'Symbol Description',
             'Quantity', 'Price', 'Settlement Date', 'Account',
             'Value', 'Currency'],
            ['October 08, 2025', 'Div Reinvest', 'RBF2010', 'RBF 2010',
             '100', '10.00', 'October 10, 2025', '90000001',
             '-1000.00', 'CAD'],
        ])

        rejections = []
        trades, _, accounting = parse_rbc_csv(path, rejections)
        self.assertEqual(len(trades), 0)
        self.assertEqual(
            accounting['rejected'].get('Unknown activity type: Div Reinvest'), 1)
        self.assertEqual(accounting['data_rows'], 1)
        self.assertEqual(
            rejections[0]['Reason Rejected'], 'Unknown activity type: Div Reinvest')
        self.assertEqual(TransactionType.objects.count(), before)


# ---------------------------------------------------------------------------
# IB CSV Parser
# ---------------------------------------------------------------------------

class IbCsvParserTests(TestCase):
    """Test IB CSV parsing."""

    def test_parses_transaction_history_format(self):
        """Transaction History format should be parsed."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,U1234567,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'BUY')
        self.assertEqual(trades[0]['symbol'], 'AAPL')
        self.assertEqual(trades[0]['quantity'], Decimal('10'))
        self.assertEqual(trades[0]['price'], Decimal('150.25'))
        self.assertEqual(trades[0]['amount'], Decimal('1502.50'))

    def test_parses_activity_statement_format(self):
        """Activity Statement format should be parsed."""
        content = (
            "Trades\n"
            "Date/Time,Symbol,Buy/Sell,Quantity,T. Price,Currency,Net Amount,Account\n"
            "2024-01-15 10:30:00,AAPL,Buy,10,150.25,USD,1502.50,U1234567\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'BUY')
        self.assertEqual(trades[0]['symbol'], 'AAPL')

    def test_dividend_imported(self):
        """Dividend transactions should be imported."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,U1234567,Dividend paid,Dividend,MSFT,"
            "10,3.00,USD,30.00,0,30.00\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'DIV')

    def test_fee_imported(self):
        """Fee transactions should be imported."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,U1234567,Commission fee,FEE,AAPL,"
            "10,1.00,USD,10.00,0,10.00\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'FEE')

    def test_transfer_imported(self):
        """Transfer transactions should be imported."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,U1234567,Wire transfer,TRANSFER,,,"
            "0,0,0,USD,1000.00\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['type'], 'TRANSFER')

    def test_practice_account_skipped(self):
        """Practice accounts should be skipped."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,PRACEF8C2DAE,Test,Buy,AAPL,"
            "10,100.00,USD,1000.00,0,1000.00\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 0)

    def test_empty_file_returns_no_trades(self):
        """Empty file should return no trades."""
        path = _write_text(
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
        )

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 0)

    def test_insufficient_columns_skipped(self):
        """Rows with fewer than 13 columns should be skipped."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,U1234567,Test\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 0)

    def test_multiple_transactions_imported(self):
        """Multiple transactions should all be imported."""
        content = (
            "Transaction History,Header,Date,Account,Description,Type,Symbol,"
            "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
            "Transaction History,Data,2024-01-15,U1234567,AAPL stock,Buy,AAPL,"
            "10,150.25,USD,1502.50,1.00,1503.50\n"
            "Transaction History,Data,2024-01-16,U1234567,MSFT stock,Sell,MSFT,"
            "5,300.00,USD,1500.00,0.50,1499.50\n"
        )
        path = _write_text(content)

        trades = parse_ib_csv(path)
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0]['type'], 'BUY')
        self.assertEqual(trades[1]['type'], 'SELL')
