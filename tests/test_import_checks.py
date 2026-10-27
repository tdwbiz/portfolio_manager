"""Unit tests for core/utils/import_checks.py (import-checks plan, step 1).

Covers the three date rules (future, before 2000, 1–100 days after the
Date of Last Statement printed on the same PDF) and the content hash.
No real data: fixed dates and a FileImportLog row only — no accounts needed.
"""

import hashlib
import os
import re
import tempfile
from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import FileImportLog
from core.utils.import_checks import (
    content_hash_of,
    is_content_imported,
    merge_and_reconcile,
    reconcile_section,
    statement_date_problems,
)
from core.utils.safe_names import hash8_of

TODAY = date(2026, 10, 27)


class ContentHashOfTests(TestCase):
    def test_sha256_of_file_bytes(self):
        payload = b"import-checks hash fixture\n" * 100
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(payload)
            path = fh.name
        try:
            self.assertEqual(content_hash_of(path), hashlib.sha256(payload).hexdigest())
            # Same digest family as hash8_of (its first 8 chars).
            self.assertEqual(content_hash_of(path)[:8], hash8_of(payload))
        finally:
            os.unlink(path)

    def test_accepts_path_objects(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(b"abc")
            path = fh.name
        try:
            from pathlib import Path

            self.assertEqual(
                content_hash_of(Path(path)), hashlib.sha256(b"abc").hexdigest()
            )
        finally:
            os.unlink(path)


class StatementDateProblemsTests(TestCase):
    def test_valid_date_without_last_known_is_ok(self):
        self.assertEqual(statement_date_problems(date(2025, 9, 30), today=TODAY), [])

    def test_future_date_fails(self):
        problems = statement_date_problems(date(2026, 10, 28), today=TODAY)
        self.assertEqual(problems, ["statement date in the future"])

    def test_today_is_not_future(self):
        self.assertEqual(statement_date_problems(TODAY, today=TODAY), [])

    def test_before_2000_fails(self):
        problems = statement_date_problems(date(1999, 12, 31), today=TODAY)
        self.assertEqual(problems, ["statement date before 2000-01-01"])

    def test_year_2000_boundary_is_ok(self):
        self.assertEqual(statement_date_problems(date(2000, 1, 1), today=TODAY), [])

    def test_missing_or_unreadable_date_reports_one_reason(self):
        self.assertEqual(
            statement_date_problems(None, today=TODAY),
            ["statement date missing or unreadable"],
        )
        self.assertEqual(
            statement_date_problems("SEPT 30 2025", today=TODAY),
            ["statement date missing or unreadable"],
        )

    def test_gap_rule_against_pdf_last_statement_date(self):
        last = date(2025, 8, 29)  # as printed on the PDF, not from the DB
        # gap of 1 and 100 days are OK
        self.assertEqual(
            statement_date_problems(last.fromordinal(last.toordinal() + 1),
                                    today=TODAY, last_known=last),
            [],
        )
        self.assertEqual(
            statement_date_problems(date(2025, 12, 7), today=TODAY, last_known=last),
            [],
        )
        # same day (gap 0) and gap of 101 days fail
        for bad in (date(2025, 8, 29), date(2025, 12, 8)):
            problems = statement_date_problems(bad, today=TODAY, last_known=last)
            self.assertEqual(
                problems,
                ["statement date not 1-100 days after the Date of Last Statement "
                 "on the same PDF"],
            )

    def test_no_gap_rule_without_last_known(self):
        # No Date of Last Statement on the PDF: no gap check at all.
        self.assertEqual(
            statement_date_problems(date(2025, 9, 30), today=TODAY, last_known=None),
            [],
        )

    def test_accepts_iso_strings(self):
        self.assertEqual(
            statement_date_problems("2025-09-30", today="2026-10-27",
                                    last_known="2025-08-29"),
            [],
        )


def _text_section(currency='CAD', opening='90.00', closing='100.00',
                  transactions=None, balances=None):
    """A synthetic currency section (text-parser shaped)."""
    if balances is None:
        balances = [{'account_type': 'Cash', 'opening': Decimal(opening),
                     'closing': Decimal(closing),
                     'market_value': Decimal(closing),
                     'percentage': Decimal('5.00')}]
    return {
        'statement_date': date(2025, 9, 30),
        'currency': currency,
        'account_number': '90000001',
        'balances': balances,
        'holdings': [],
        'transactions': transactions if transactions is not None else [],
    }


def _tx(debit='0.00', credit='0.00'):
    return {'date': date(2025, 9, 15), 'activity_type': 'DEPOSIT',
            'description': 'TEST', 'symbol': '',
            'quantity': Decimal('0'), 'price': Decimal('0.0000'),
            'debit': Decimal(debit), 'credit': Decimal(credit)}


class ReconcileSectionTests(TestCase):
    """The per-section cash gate (step 13c-b)."""

    def test_reconciles_with_transactions(self):
        # 90.00 + 10.00 credit - 5.00 debit == 95.00
        section = _text_section(opening='90.00', closing='95.00',
                                transactions=[_tx(credit='10.00'),
                                              _tx(debit='5.00')])
        self.assertEqual(reconcile_section(section), [])

    def test_off_by_one_cent_fails(self):
        section = _text_section(opening='90.00', closing='95.01',
                                transactions=[_tx(credit='10.00'),
                                              _tx(debit='5.00')])
        reasons = reconcile_section(section)
        self.assertEqual(len(reasons), 1)
        self.assertIn('CAD', reasons[0])
        for field in ('opening', 'credits', 'debits', 'closing'):
            self.assertIn(field, reasons[0])
        # No digits beyond the currency code.
        self.assertRegex(reasons[0], r'^CAD\b')
        self.assertEqual(re.findall(r'\d+', reasons[0]), [])

    def test_no_transactions_equal_cash_ok(self):
        self.assertEqual(
            reconcile_section(_text_section(opening='90.00', closing='90.00')),
            [],
        )

    def test_no_transactions_unequal_cash_fails(self):
        reasons = reconcile_section(_text_section(opening='90.00', closing='100.00'))
        self.assertEqual(len(reasons), 1)
        self.assertIn('CAD', reasons[0])

    def test_missing_cash_row_fails_naming_balances(self):
        section = _text_section(balances=[{'account_type': 'Common Shares',
                                           'opening': Decimal('1'),
                                           'closing': Decimal('1')}])
        reasons = reconcile_section(section)
        self.assertEqual(len(reasons), 1)
        self.assertIn('balances', reasons[0])
        self.assertIn('CAD', reasons[0])

    def test_multiple_cash_rows_are_summed(self):
        balances = [
            {'account_type': 'Cash', 'opening': Decimal('50.00'),
             'closing': Decimal('60.00')},
            {'account_type': 'cash', 'opening': Decimal('40.00'),
             'closing': Decimal('30.00')},  # case-insensitive match
        ]
        # Summed: 90 opening, 90 closing; no transactions -> reconciles.
        section = _text_section(balances=balances)
        self.assertEqual(reconcile_section(section), [])

    def test_non_cash_balance_rows_are_ignored(self):
        balances = _text_section(opening='90.00', closing='95.00')['balances'] + [
            {'account_type': 'Common Shares', 'opening': Decimal('1.00'),
             'closing': Decimal('2.00')},  # must not enter the cash sum
        ]
        section = _text_section(balances=balances,
                                transactions=[_tx(credit='5.00')])
        # Correct: 90 + 5 == 95. If the shares row leaked into the cash sum:
        # 91 + 5 != 92 and this would fail.
        self.assertEqual(reconcile_section(section), [])

    def test_reason_names_currency_not_digits(self):
        reasons = reconcile_section(
            _text_section(currency='usd', opening='1.00', closing='2.00'))
        self.assertEqual(len(reasons), 1)
        self.assertTrue(reasons[0].startswith('USD '))


class MergeAndReconcileTests(TestCase):
    """Currency matching + gate for the text path (step 13c-b)."""

    def test_reconciles_and_replaces_transactions(self):
        text = [_text_section()]
        vl = [{'currency': 'CAD', 'transactions': [_tx(credit='10.00')]}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertEqual(reasons, [])
        self.assertIsNotNone(merged)
        self.assertEqual(len(merged), 1)
        # The text section's own data is kept…
        self.assertEqual(merged[0]['balances'], text[0]['balances'])
        self.assertEqual(merged[0]['statement_date'], date(2025, 9, 30))
        # …and the transactions come from the VL section.
        self.assertEqual(merged[0]['transactions'], [_tx(credit='10.00')])
        # The text input is not mutated.
        self.assertEqual(text[0]['transactions'], [])

    def test_lowercase_currency_matched(self):
        text = [_text_section(currency='cad')]
        vl = [{'currency': 'CAD', 'transactions': [_tx(credit='10.00')]}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertEqual(reasons, [])
        self.assertIsNotNone(merged)

    def test_gate_failure_on_merged_section(self):
        # VL transactions do not bridge the text cash gap.
        text = [_text_section(opening='90.00', closing='100.00')]
        vl = [{'currency': 'CAD', 'transactions': []}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertIsNotNone(merged)
        self.assertEqual(len(reasons), 1)
        self.assertIn('CAD', reasons[0])

    def test_currency_missing_from_vl(self):
        text = [_text_section(), _text_section(currency='USD')]
        vl = [{'currency': 'CAD', 'transactions': []}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertIsNone(merged)
        self.assertEqual(reasons, ['currency USD missing from VL sections'])

    def test_currency_missing_from_text(self):
        text = [_text_section()]
        vl = [{'currency': 'CAD', 'transactions': []},
              {'currency': 'USD', 'transactions': []}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertIsNone(merged)
        self.assertEqual(reasons, ['currency USD missing from text sections'])

    def test_duplicated_currency_on_either_side(self):
        text = [_text_section(), _text_section()]
        vl = [{'currency': 'CAD', 'transactions': []}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertIsNone(merged)
        self.assertEqual(reasons, ['duplicated currency CAD in text sections'])

        text = [_text_section()]
        vl = [{'currency': 'CAD', 'transactions': []},
              {'currency': 'CAD', 'transactions': []}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertIsNone(merged)
        self.assertEqual(reasons, ['duplicated currency CAD in VL sections'])

    def test_multi_currency_matched_by_currency(self):
        text = [_text_section(), _text_section(currency='USD',
                                               opening='40.00', closing='50.00')]
        vl = [{'currency': 'USD', 'transactions': [_tx(credit='10.00')]},
              {'currency': 'CAD', 'transactions': [_tx(credit='10.00')]}]
        merged, reasons = merge_and_reconcile(text, vl)
        self.assertEqual(reasons, [])
        by_cur = {m['currency']: m for m in merged}
        self.assertEqual(by_cur['CAD']['transactions'], [_tx(credit='10.00')])
        self.assertEqual(by_cur['USD']['transactions'], [_tx(credit='10.00')])


class IsContentImportedTests(TestCase):
    def test_success_log_counts(self):
        FileImportLog.objects.create(
            filename="a.pdf", broker="RBC", status="success", content_hash="h1"
        )
        self.assertTrue(is_content_imported("h1"))

    def test_ok_log_counts(self):
        FileImportLog.objects.create(
            filename="b.pdf", broker="RBC", status="ok", content_hash="h2"
        )
        self.assertTrue(is_content_imported("h2"))

    def test_error_log_does_not_count(self):
        # A failed import may be retried — it is not a duplicate.
        FileImportLog.objects.create(
            filename="c.pdf", broker="RBC", status="error", content_hash="h3"
        )
        self.assertFalse(is_content_imported("h3"))

    def test_unknown_hash_and_empty_hash(self):
        self.assertFalse(is_content_imported("nope"))
        self.assertFalse(is_content_imported(""))
