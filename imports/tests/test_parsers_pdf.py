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
from imports.parsers_pdf import (
    parse_rbc_pdf,
    parse_rbc_pdf_multi,
    self_check_sections,
    _parse_rbc_date,
)


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


# Step 12b test fixtures: statement date with month/day mid-line and the year
# on the next line (both layouts — see docs/audit/05-text-parser-check.md).
RECENT_LAYOUT_DATE_TEXT = """
RRSP Statement SEPT 30
2025
Your Account Number: 900-00001-1-8
Date of Last Statement: AUG. 29 2025
"""

OLD_LAYOUT_DATE_TEXT = """
Cdn. Dollar Statement MAR. 31
2020
Your Account Number: 900-00002-1-8
Date of Last Statement: FEB. 28 2020
"""


class TestStatementDateLayouts(TestCase):
    """Step 12b tests 1-2: statement date with month/day mid-line, year on next line."""

    def test_recent_layout_date_found(self):
        result = parse_rbc_pdf(RECENT_LAYOUT_DATE_TEXT)
        self.assertEqual(result['statement_date'], date(2025, 9, 30))
        # The "Date of Last Statement" line must not be taken as the statement date.
        self.assertEqual(result['last_statement_date'], date(2025, 8, 29))

    def test_old_layout_date_found(self):
        result = parse_rbc_pdf(OLD_LAYOUT_DATE_TEXT)
        self.assertEqual(result['statement_date'], date(2020, 3, 31))
        self.assertEqual(result['last_statement_date'], date(2020, 2, 28))

    def test_recent_layout_date_found_multi(self):
        sections = parse_rbc_pdf_multi(RECENT_LAYOUT_DATE_TEXT)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['statement_date'], date(2025, 9, 30))


# Step 12b test fixture: Asset Review holdings table in the real layout —
# two-line header (SECURITY … QUANTITY on the first line), right-anchored
# fixed columns, description words before the symbol, and a joined-word total
# line as in the old layout. Fake numbers only.
ASSET_REVIEW_PDF_TEXT = """
RRSP Statement OCT 31 2025
Your Account Number: 900-00005-1-8
Date of Last Statement: SEPT 30 2025

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.40000 CAD as of OCT 31, 2025 )
--------------------------------------------------------------------------------
SECURITY QUANTITY MKT. PRICE BOOK COST MARKET VALUE
SYMBOL SEGREGATED RATE
RBC INVESTMENT SAVINGS ACCOUNT SR A (2010)                RBF2010    150.000     10.500      1,470.00    $1,575.00
VANGUARD CANADA HIGH DIVIDEND ETF                        VFV         5.000       280.000     1,300.00    $1,400.00
TotalValueofsecurities                                                       2,770.00    $2,975.00

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
"""


class TestAssetReviewHoldings(TestCase):
    """Step 12b test 3: holdings from a synthetic Asset Review table."""

    def test_holdings_parsed_with_vl_shaped_keys(self):
        result = parse_rbc_pdf(ASSET_REVIEW_PDF_TEXT)
        holdings = result['holdings']
        self.assertEqual(len(holdings), 2)
        for h in holdings:
            self.assertEqual(
                set(h.keys()),
                {'security', 'symbol', 'quantity', 'book_cost',
                 'market_price', 'market_value'},
            )
        by_symbol = {h['symbol']: h for h in holdings}
        self.assertEqual(set(by_symbol), {'RBF2010', 'VFV'})

        rbf = by_symbol['RBF2010']
        self.assertEqual(rbf['security'], 'RBC INVESTMENT SAVINGS ACCOUNT SR A (2010)')
        self.assertEqual(rbf['quantity'], Decimal('150.000'))
        self.assertEqual(rbf['book_cost'], Decimal('1470.00'))
        self.assertEqual(rbf['market_price'], Decimal('10.500'))
        self.assertEqual(rbf['market_value'], Decimal('1575.00'))

        vfv = by_symbol['VFV']
        self.assertEqual(vfv['security'], 'VANGUARD CANADA HIGH DIVIDEND ETF')
        self.assertEqual(vfv['quantity'], Decimal('5.000'))
        self.assertEqual(vfv['book_cost'], Decimal('1300.00'))
        self.assertEqual(vfv['market_price'], Decimal('280.000'))
        self.assertEqual(vfv['market_value'], Decimal('1400.00'))

    def test_holdings_key_present_in_multi_entry_point(self):
        sections = parse_rbc_pdf_multi(ASSET_REVIEW_PDF_TEXT)
        self.assertEqual(len(sections), 1)
        self.assertIn('holdings', sections[0])
        self.assertEqual(len(sections[0]['holdings']), 2)

    def test_unreadable_book_cost_is_none_not_zero(self):
        # A holding row whose BOOK COST column is missing: the parser must not
        # invent a 0 — it returns None and the self-check (step 4) fails on it.
        text = ASSET_REVIEW_PDF_TEXT.replace(
            'VFV         5.000       280.000     1,300.00    $1,400.00',
            'VFV         5.000       280.000                 $1,400.00',
        )
        holdings = parse_rbc_pdf(text)['holdings']
        self.assertEqual(len(holdings), 2)
        # The shifted row is kept (its $ market value is still readable) but
        # with unreadable columns — never a placeholder 0.
        vfv = next(h for h in holdings if h['market_value'] == Decimal('1400.00'))
        self.assertIsNone(vfv['book_cost'])
        self.assertIsNone(vfv['quantity'])


# Plan vl-statement-rows fix 3a: Asset Review tables with option rows.
# The option row carries no separate symbol token — the security is the
# full CALL/PUT pattern and the fixed columns are QTY PRICE BOOK_COST $MV,
# a trailing '-' marking a negative (short position). Fake numbers only.
def _asset_review_text(rows: str) -> str:
    return (
        "RRSP Statement OCT 31 2025\n"
        "Your Account Number: 900-00007-1-8\n"
        "Date of Last Statement: SEPT 30 2025\n"
        "\n" + "-" * 80 + "\n"
        "Asset Review  ( Exchange rate 1USD = 1.40000 CAD as of OCT 31, 2025 )\n"
        + "-" * 80 + "\n"
        "SECURITY QUANTITY MKT. PRICE BOOK COST MARKET VALUE\n"
        "SYMBOL SEGREGATED RATE\n"
        + rows
        + "\n" + "-" * 80 + "\n"
        "Account Activity\n"
    )


class TestOptionHoldings(TestCase):
    """Plan vl-statement-rows fix 3a: trailing-minus values and the option
    four-token layout in _parse_asset_review (full pattern only, extra A)."""

    def test_option_row_parsed_as_negative(self):
        text = _asset_review_text(
            "CALL NVDA 07/30/21 210 4- 0.010 225.04- $4.00-\n"
        )
        holdings = parse_rbc_pdf(text)['holdings']
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        self.assertEqual(h['security'], 'CALL NVDA 07/30/21 210')
        self.assertEqual(h['symbol'], '')
        self.assertEqual(h['quantity'], Decimal('-4'))
        self.assertEqual(h['market_price'], Decimal('0.010'))
        self.assertEqual(h['book_cost'], Decimal('-225.04'))
        self.assertEqual(h['market_value'], Decimal('-4.00'))

    def test_option_row_glued_call_prefix(self):
        # The check-7 layout: CALL glued to the underlying, no space.
        text = _asset_review_text(
            "CALLNVDA 07/30/21 210 4- 0.010 225.04- $4.00-\n"
        )
        holdings = parse_rbc_pdf(text)['holdings']
        self.assertEqual(len(holdings), 1)
        h = holdings[0]
        self.assertEqual(h['security'], 'CALLNVDA 07/30/21 210')
        self.assertEqual(h['symbol'], '')
        self.assertEqual(h['quantity'], Decimal('-4'))
        self.assertEqual(h['market_value'], Decimal('-4.00'))

    def test_callaway_and_putnam_names_are_not_options(self):
        # Extra A: a description that merely starts with CALL or PUT is not
        # an option — without the expiry+strike pattern the row must fall
        # back to unreadable (None), never to the option four-token layout.
        text = _asset_review_text(
            "CALLAWAYGOLFINC 100 25.000 2,500.00 $2,500.00\n"
            "PUTNAMBALMUTFUND 50 12.000 600.00 $600.00\n"
        )
        holdings = parse_rbc_pdf(text)['holdings']
        self.assertEqual(len(holdings), 2)
        for h in holdings:
            self.assertIsNone(h['quantity'])
            self.assertIsNone(h['book_cost'])
            self.assertIsNone(h['market_price'])
            # The $ market value is still readable and stays positive.
            self.assertIsNotNone(h['market_value'])
            self.assertGreater(h['market_value'], 0)


# Step 12b test fixtures: full synthetic statements whose holdings + cash
# reconcile to the end total (fake numbers only). Layout mimics the real
# statements: date pair at the top, "Total on …" footer lines in the Asset
# Summary, right-anchored Asset Review rows.
CAD_ONLY_STATEMENT = """
Order Execution Only SEPT 30
Cdn. Dollar Statement 2025
Page 1 of 2
Your Account Number: 900-00006-1-8
Date of Last Statement: AUG. 29, 2025

--------------------------------------------------------------------------------
Asset Summary
--------------------------------------------------------------------------------
                      MARKET VALUE
Cash                  $100.00          5.00 %
Common Shares         $1,400.00        70.00 %
Mutual Funds          $500.00          25.00 %
Total on AUG. 29      $1,850.00
Total on SEPT 30      $2,000.00       100.00 %

--------------------------------------------------------------------------------
Cash Balance
--------------------------------------------------------------------------------
ACCOUNT TYPE            OPENING BALANCE AT AUG. 29   CLOSING BALANCE AT SEPT 30
Cash                    $90.00                         $100.00

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.40000 CAD as of SEPT 30, 2025 )
--------------------------------------------------------------------------------
SECURITY QUANTITY MKT. PRICE BOOK COST MARKET VALUE
SYMBOL SEGREGATED RATE
VANGUARD CANADA HIGH DIVIDEND ETF                        VFV         5.000       280.000     1,300.00    $1,400.00
VANGUARD FTSE ALL-WORLD EQUITY ETF                       VDY         10.000      50.000      480.00      $500.00
Total Value of securities                                                  1,780.00    $1,900.00

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
"""

# CAD + USD statement: the USD part starts at its header line (the split
# point) and repeats the page-header pair, as the real multi-currency PDFs do.
MULTI_CURRENCY_STATEMENT = CAD_ONLY_STATEMENT + """
Order Execution Only SEPT 30
U.S. Dollar Statement 2025
Order Execution Only SEPT 30
U.S. Dollar Statement 2025
Page 2 of 4

--------------------------------------------------------------------------------
Asset Summary
--------------------------------------------------------------------------------
                      MARKET VALUE
Cash                  $50.00           5.00 %
Common Shares         $950.00          95.00 %
Total on AUG. 29      $980.00
Total on SEPT 30      $1,000.00       100.00 %

--------------------------------------------------------------------------------
Cash Balance
--------------------------------------------------------------------------------
ACCOUNT TYPE            OPENING BALANCE AT AUG. 29   CLOSING BALANCE AT SEPT 30
Cash                    $40.00                         $50.00

--------------------------------------------------------------------------------
Asset Review  ( Exchange rate 1USD = 1.40000 CAD as of SEPT 30, 2025 )
--------------------------------------------------------------------------------
SECURITY QUANTITY MKT. PRICE BOOK COST MARKET VALUE
SYMBOL SEGREGATED RATE
SMALL CAP VALUE ETF                                      XDIV        10.000      95.000       900.00      $950.00
Total Value of securities                                                     900.00      $950.00

--------------------------------------------------------------------------------
Account Activity
--------------------------------------------------------------------------------
DATE        ACTIVITY    DESCRIPTION                                    PRICE/RATE    QUANTITY    DEBIT    CREDIT
"""


class TestPerCurrencySections(TestCase):
    """Step 12b tests 4-5: cash, totals and holdings per currency section."""

    @staticmethod
    def _cash_closing(section):
        return sum(
            (Decimal(b['market_value']) for b in section['balances']
             if b['account_type'].strip().lower() == 'cash'),
            Decimal('0'),
        )

    def test_cad_only_section_data(self):
        sections = parse_rbc_pdf_multi(CAD_ONLY_STATEMENT)
        self.assertEqual(len(sections), 1)
        sec = sections[0]
        self.assertEqual(sec['currency'], 'CAD')
        self.assertEqual(sec['statement_date'], date(2025, 9, 30))
        self.assertEqual(sec['end_total_market_value'], Decimal('2000.00'))
        self.assertEqual(sec['begin_total_market_value'], Decimal('1850.00'))
        self.assertEqual(self._cash_closing(sec), Decimal('100.00'))
        self.assertEqual(
            {h['symbol'] for h in sec['holdings']}, {'VFV', 'VDY'})
        # holdings + cash reconcile to the end total to the cent
        mv = sum((Decimal(h['market_value']) for h in sec['holdings']), Decimal('0'))
        self.assertEqual(mv + self._cash_closing(sec), Decimal('2000.00'))
        # Test 4: the self-check passes.
        self.assertEqual(self_check_sections(sections), [])

    def test_cad_and_usd_sections_each_own_data(self):
        sections = parse_rbc_pdf_multi(MULTI_CURRENCY_STATEMENT)
        self.assertEqual(len(sections), 2)
        by_cur = {s['currency']: s for s in sections}
        self.assertEqual(set(by_cur), {'CAD', 'USD'})

        cad, usd = by_cur['CAD'], by_cur['USD']
        # Both sections carry their own date, totals, cash and holdings —
        # taken from the section text, not the full text.
        for sec in (cad, usd):
            self.assertEqual(sec['statement_date'], date(2025, 9, 30))

        self.assertEqual(cad['end_total_market_value'], Decimal('2000.00'))
        self.assertEqual(self._cash_closing(cad), Decimal('100.00'))
        self.assertEqual({h['symbol'] for h in cad['holdings']}, {'VFV', 'VDY'})

        self.assertEqual(usd['end_total_market_value'], Decimal('1000.00'))
        self.assertEqual(self._cash_closing(usd), Decimal('50.00'))
        self.assertEqual({h['symbol'] for h in usd['holdings']}, {'XDIV'})

        mv = sum((Decimal(h['market_value']) for h in usd['holdings']), Decimal('0'))
        self.assertEqual(mv + self._cash_closing(usd), Decimal('1000.00'))
        # Test 5: the self-check passes per currency.
        self.assertEqual(self_check_sections(sections), [])


# Step 12b follow-up fixture (tdw, Oct 26): the same CAD + USD statement but
# with a USD header the splitter does not recognise, so everything stays in
# one section. The second Asset Summary's end total is set so that the
# combined parse still reconciles (first holdings table + last cash closing =
# last end total) — exactly the case that used to pass silently; only the
# currency-section count check sees it.
UNRECOGNISED_USD_HEADER_STATEMENT = MULTI_CURRENCY_STATEMENT.replace(
    'U.S. Dollar Statement', 'Foreign Currency Statement'
).replace(
    'Total on SEPT 30      $1,000.00       100.00 %',
    'Total on SEPT 30      $1,950.00       100.00 %',
)


class TestStatementDateYearSanity(TestCase):
    """Step 12b follow-up (tdw, Oct 26): whole 4-digit year, 2000..next year."""

    def test_last_four_digits_of_longer_number_do_not_count(self):
        # Two-line shape: the next line ends in a longer number; its last four
        # digits must not be taken as the year.
        text = """
Order Execution Only SEPT 30
Cdn. Dollar Statement No 12025
"""
        self.assertIsNone(parse_rbc_pdf(text)['statement_date'])

    def test_single_line_year_glued_to_digits_rejected(self):
        text = "RRSP Statement (CDN$)\nSEPT 30 12025\n"
        self.assertIsNone(parse_rbc_pdf(text)['statement_date'])

    def test_two_line_year_glued_to_digits_rejected(self):
        text = """
Order Execution Only SEPT 30
Cdn. Dollar Statement 12025
"""
        self.assertIsNone(parse_rbc_pdf(text)['statement_date'])

    def test_year_before_2000_rejected(self):
        text = "RRSP Statement (CDN$)\nSEPT 30 1999\n"
        self.assertIsNone(parse_rbc_pdf(text)['statement_date'])

    def test_year_beyond_next_year_rejected(self):
        text = "RRSP Statement (CDN$)\nSEPT 30 2099\n"
        self.assertIsNone(parse_rbc_pdf(text)['statement_date'])


class TestSelfCheckSections(TestCase):
    """Step 12b test 6 (helper part): self_check_sections reasons."""

    @staticmethod
    def _minimal_section(statement_date, last_statement_date):
        return [{
            'currency': 'CAD',
            'statement_date': statement_date,
            'last_statement_date': last_statement_date,
            'holdings': [],
            'balances': [{'account_type': 'Cash', 'market_value': Decimal('10.00')}],
            'end_total_market_value': Decimal('10.00'),
        }]

    def test_unsplit_currency_section_fails_on_count_mismatch(self):
        # The unrecognised USD header keeps everything in one (CAD) section and
        # the totals still reconcile — without full_text the self-check passes,
        # with it the count check returns a reason naming only the counts.
        sections = parse_rbc_pdf_multi(UNRECOGNISED_USD_HEADER_STATEMENT)
        self.assertEqual(len(sections), 1)
        self.assertEqual(self_check_sections(sections), [])
        reasons = self_check_sections(
            sections, full_text=UNRECOGNISED_USD_HEADER_STATEMENT,
        )
        self.assertEqual(reasons, ['currency sections: 1 parsed vs 2 in text'])

    def test_statement_date_before_last_statement_date_fails(self):
        sections = self._minimal_section(date(2025, 8, 29), date(2025, 9, 30))
        reasons = self_check_sections(sections)
        self.assertEqual(
            reasons,
            ['CAD statement_date not 1-100 days after last_statement_date'],
        )

    def test_statement_date_more_than_100_days_after_last_fails(self):
        # Sep 1 -> Dec 31 is 121 days.
        sections = self._minimal_section(date(2025, 12, 31), date(2025, 9, 1))
        reasons = self_check_sections(sections)
        self.assertEqual(
            reasons,
            ['CAD statement_date not 1-100 days after last_statement_date'],
        )

    def test_statement_date_within_100_days_after_last_passes(self):
        # Aug 29 -> Sep 30 is 32 days.
        sections = self._minimal_section(date(2025, 9, 30), date(2025, 8, 29))
        self.assertEqual(self_check_sections(sections), [])

    def test_date_missing(self):
        sections = [{
            'currency': 'CAD', 'statement_date': None, 'holdings': [],
            'balances': [{'account_type': 'Cash', 'market_value': Decimal('10.00')}],
            'end_total_market_value': Decimal('10.00'),
        }]
        reasons = self_check_sections(sections)
        self.assertEqual(reasons, ['CAD statement_date missing'])

    def test_holdings_cash_mismatch_by_one_cent(self):
        sections = parse_rbc_pdf_multi(CAD_ONLY_STATEMENT)
        sections[0]['end_total_market_value'] = Decimal('2000.01')
        reasons = self_check_sections(sections)
        self.assertEqual(reasons, ['CAD holdings+cash != end_total_market_value'])

    def test_unreadable_book_cost_fails(self):
        text = CAD_ONLY_STATEMENT.replace(
            'VFV         5.000       280.000     1,300.00    $1,400.00',
            'VFV         5.000       280.000                 $1,400.00',
        )
        reasons = self_check_sections(parse_rbc_pdf_multi(text))
        self.assertTrue(any('book_cost unreadable' in r for r in reasons), reasons)

    def test_qty_price_beyond_tolerance_fails(self):
        # 5 shares at 280.00 = 1400.00; move the market value by more than
        # 0.1 percent (1.40) without touching quantity or price.
        text = CAD_ONLY_STATEMENT.replace(
            'VFV         5.000       280.000     1,300.00    $1,400.00',
            'VFV         5.000       280.000     1,300.00    $1,402.00',
        )
        reasons = self_check_sections(parse_rbc_pdf_multi(text))
        self.assertTrue(
            any('quantity*market_price vs market_value beyond 0.1 percent' in r
                for r in reasons),
            reasons,
        )

    def test_qty_price_within_tolerance_passes(self):
        # 5 * 280.00 = 1400.00 vs market value 1401.40: exactly 0.1 percent
        # off — within tolerance, and cash still reconciles to the end total.
        sections = [{
            'currency': 'CAD', 'statement_date': date(2025, 9, 30),
            'holdings': [{'security': '', 'symbol': 'VFV',
                          'quantity': Decimal('5'),
                          'book_cost': Decimal('1300.00'),
                          'market_price': Decimal('280.000'),
                          'market_value': Decimal('1401.40')}],
            'balances': [{'account_type': 'Cash', 'market_value': Decimal('598.60')}],
            'end_total_market_value': Decimal('2000.00'),
        }]
        self.assertEqual(self_check_sections(sections), [])


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
        """One valid monthly VL parse section (CAD) that also passes the 13d
        VL section checks: no holdings, so begin/end totals equal the cash
        balance."""
        return {
            'statement_date': date(2025, 9, 30),
            'last_statement_date': date(2025, 8, 29),
            'account_number': '900-00003-1-8',
            'currency': 'CAD',
            'fx_rate': Decimal('1.000000'),
            'begin_total_market_value': Decimal('20.85'),
            'end_total_market_value': Decimal('20.85'),
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
