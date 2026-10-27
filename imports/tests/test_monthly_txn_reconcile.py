"""Command-level tests for the 13c-b cash-reconciliation gate.

Pattern: ``ManageInboxImportTextFirstTests`` in
``imports/tests/test_manage_inbox_import.py`` — fake account 90000004 by
fingerprint, ``classify_pdf`` / ``parse_rbc_pdf_multi`` /
``parse_rbc_monthly_vl`` patched in the command module, synthetic section
dicts, ``override_settings`` for the dirs. SQLite only.

Covered:
- a reconciling text file stores holdings/balances from the text parser and
  transactions from the VL parser; the JSON log records the parsers and the
  per-section transaction count / reconciliation verdict;
- off by one cent → rejected ``reconcile_error``, nothing stored, the reason
  names fields and currency only;
- CAD + USD sections matched by currency, both reconcile → stored;
- a VL error on the text path → rejected, nothing stored;
- a currency section present on only one side (either direction) → rejected;
- a fallback file whose VL sections do not reconcile against their own cash
  → rejected (Q1, tdw Oct 27);
- ``--dry-run`` on a fully reconciling file: real verdict, nothing written.
"""

import io
import json
import os
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import (
    FileImportLog,
    RbcMonthly,
    RbcMonthlyBalance,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
)
from core.utils.account_fingerprint import fingerprint_account_number

# Fake account (90000001-90000008 are the reserved test numbers).
FAKE_NUMBER = "90000004"
FAKE_NAME = "ACCOUNT_D"

FAKE_PDF_CONTENT = b"%PDF-1.4\nfake statement content for 13c-b gate tests\n"


def _text_section(currency='CAD', opening='90.00', closing='100.00'):
    """A synthetic text-parser section that passes the self-check:

    one holding (5 x 280.00 = 1400.00) plus cash (``closing``) equals the
    end total; statement date 32 days after the last statement date.
    ``transactions`` is a placeholder — the command replaces it with the VL
    section's transactions.
    """
    return {
        'statement_date': date(2025, 9, 30),
        'last_statement_date': date(2025, 8, 29),
        'account_number': FAKE_NUMBER,
        'currency': currency,
        'fx_rate': Decimal('1.0000'),
        'begin_total_market_value': Decimal('1450.00'),
        'end_total_market_value': Decimal('1400.00') + Decimal(closing),
        'balances': [
            {'account_type': 'Cash', 'opening': Decimal(opening),
             'closing': Decimal(closing), 'market_value': Decimal(closing),
             'percentage': Decimal('5.00')},
        ],
        'holdings': [
            {'security': 'VANGUARD ETF', 'symbol': 'VFV',
             'quantity': Decimal('5'), 'book_cost': Decimal('1300.00'),
             'market_price': Decimal('280.000'),
             'market_value': Decimal('1400.00')},
        ],
        'transactions': [],
    }


def _vl_transaction(credit='10.00', debit='0.00'):
    return {'date': date(2025, 9, 15), 'activity_type': 'DEPOSIT',
            'description': 'TEST DEPOSIT', 'symbol': '',
            'quantity': Decimal('0'), 'price': Decimal('0.0000'),
            'debit': Decimal(debit), 'credit': Decimal(credit)}


def _vl_section(currency='CAD', credit='10.00'):
    """A VL section whose single credit bridges 90.00 -> 100.00."""
    return {'currency': currency,
            'transactions': [_vl_transaction(credit=credit)]}


class MonthlyTxnReconcileTests(TestCase):

    def setUp(self):
        from core.models import Account

        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC",
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0004",
        )

    def _run(self, tmpdir, text_sections, vl_sections=None, dry_run=False,
             vl_side_effect=None):
        inbox_dir = os.path.join(tmpdir, 'inbox')
        processed_dir = os.path.join(tmpdir, 'processed')
        os.makedirs(inbox_dir)
        with open(os.path.join(inbox_dir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf'),
                  'wb') as fh:
            fh.write(FAKE_PDF_CONTENT)

        args = ['--inbox-dir', inbox_dir]
        if dry_run:
            args.append('--dry-run')
        out, err = io.StringIO(), io.StringIO()
        with patch(
            'core.management.commands.manage_inbox_import.classify_pdf',
            return_value='monthly',
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_pdf_multi',
            return_value=text_sections,
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl',
            side_effect=vl_side_effect, return_value=vl_sections,
        ):
            with override_settings(
                IMPORT_PROCESSED_DIR=processed_dir,
                IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
            ):
                call_command('manage_inbox_import', *args, stdout=out, stderr=err)
        return processed_dir, out.getvalue(), err.getvalue()

    def _json_log(self, processed_dir, error=False):
        """The single JSON log of the run (pre-pass-1 rejections get the
        unattributable UNKNOWN_ACCOUNT name, so look it up rather than
        assuming the safe monthly name)."""
        json_dir = os.path.join(processed_dir, 'json_logs')
        names = [n for n in (os.listdir(json_dir) if os.path.isdir(json_dir) else [])
                 if n.endswith('.json')]
        self.assertEqual(len(names), 1, f'expected one JSON log, got {names}')
        name = names[0]
        self.assertEqual(name.endswith('_ERROR.json'), error)
        with open(os.path.join(json_dir, name), encoding='utf-8') as fh:
            return json.load(fh)

    def test_reconciling_file_stores_text_holdings_and_vl_transactions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(
                tmpdir, [_text_section()], vl_sections=[_vl_section()],
            )

            # Holdings, cash and totals come from the text parser…
            stmt = RbcMonthly.objects.get()
            self.assertEqual(stmt.currency, 'CAD')
            bal = RbcMonthlyBalance.objects.get()
            self.assertEqual(bal.account_type, 'Cash')
            self.assertEqual(bal.opening_balance, Decimal('90.00'))
            self.assertEqual(bal.closing_balance, Decimal('100.00'))
            holding = RbcMonthlyHolding.objects.get()
            self.assertEqual(holding.symbol, 'VFV')
            self.assertEqual(holding.quantity, Decimal('5'))
            # …the transaction comes from the VL parser.
            tx = RbcMonthlyTransaction.objects.get()
            self.assertEqual(tx.activity_type, 'DEPOSIT')
            self.assertEqual(tx.credit, Decimal('10.00'))
            self.assertEqual(tx.currency, 'CAD')

            payload = self._json_log(processed_dir)
            self.assertEqual(payload['verdict'], 'imported')
            self.assertEqual(payload['parser_holdings'], 'text')
            self.assertEqual(payload['parser_transactions'], 'vl')
            section = payload['sections'][0]
            self.assertEqual(section['txn_count'], 1)
            self.assertEqual(section['reconcile'], 'ok')

    def test_off_by_one_cent_rejected_with_field_names_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(
                tmpdir, [_text_section()],
                vl_sections=[_vl_section(credit='9.99')],
            )

            # Nothing from the file was stored…
            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(RbcMonthlyBalance.objects.count(), 0)
            self.assertEqual(RbcMonthlyHolding.objects.count(), 0)
            self.assertEqual(RbcMonthlyTransaction.objects.count(), 0)
            # …the file went to errors/…
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            # …and the reason names fields and currency only — no values.
            reason = 'CAD opening + credits - debits != closing'
            self.assertEqual(log.error_message, reason)
            self.assertIn(reason, err)

            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'reconcile_error')
            section = payload['sections'][0]
            self.assertEqual(section['txn_count'], 1)
            self.assertEqual(section['reconcile'], 'failed')

    def test_multi_currency_matched_by_currency(self):
        text = [_text_section('CAD'),
                _text_section('USD', opening='40.00', closing='50.00')]
        # VL in the opposite order: matching must be by currency, not index.
        vl = [_vl_section('USD'), _vl_section('CAD')]
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(tmpdir, text, vl_sections=vl)

            self.assertEqual(RbcMonthly.objects.count(), 2)
            for currency in ('CAD', 'USD'):
                txs = RbcMonthlyTransaction.objects.filter(currency=currency)
                self.assertEqual(txs.count(), 1)
            payload = self._json_log(processed_dir)
            self.assertEqual(payload['verdict'], 'imported')
            by_cur = {s['currency']: s for s in payload['sections']}
            self.assertEqual(by_cur['CAD']['txn_count'], 1)
            self.assertEqual(by_cur['USD']['txn_count'], 1)
            self.assertEqual(by_cur['CAD']['reconcile'], 'ok')
            self.assertEqual(by_cur['USD']['reconcile'], 'ok')

    def test_vl_error_on_text_path_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(
                tmpdir, [_text_section()],
                vl_side_effect=RuntimeError('boom'),
            )

            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn('vl_parser', log.error_message)
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'reconcile_error')

    def _assert_currency_mismatch_rejected(self, text, vl, expected_reason):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(tmpdir, text, vl_sections=vl)

            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn(expected_reason, log.error_message)
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'reconcile_error')

    def test_currency_missing_from_vl_rejected(self):
        text = [_text_section('CAD'), _text_section('USD', opening='40.00',
                                                        closing='50.00')]
        self._assert_currency_mismatch_rejected(
            text, [_vl_section('CAD')], 'currency USD missing from VL sections')

    def test_currency_missing_from_text_rejected(self):
        self._assert_currency_mismatch_rejected(
            [_text_section()],
            [_vl_section('CAD'), _vl_section('USD')],
            'currency USD missing from text sections')

    def test_fallback_file_that_does_not_reconcile_rejected(self):
        # Q1 (tdw Oct 27): the VL fallback path is gated too — on the VL
        # result's own cash rows and transactions.
        bad_text = _text_section()
        bad_text['end_total_market_value'] = Decimal('999.99')  # self-check fails
        vl_section = {
            'statement_date': date(2025, 9, 30),
            'last_statement_date': date(2025, 8, 29),
            'account_number': FAKE_NUMBER,
            'currency': 'CAD',
            'balances': [
                {'account_type': 'Cash', 'opening': Decimal('90.00'),
                 'closing': Decimal('100.00')},
            ],
            'transactions': [],  # 90.00 + 0 - 0 != 100.00
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, err = self._run(
                tmpdir, [bad_text], vl_sections=[vl_section],
            )

            self.assertIn('falling back to VL', out)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn('CAD opening + credits - debits != closing',
                          log.error_message)
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'reconcile_error')
            self.assertEqual(payload['parser_holdings'], 'vl')
            self.assertEqual(payload['parser_transactions'], 'vl')
            section = payload['sections'][0]
            self.assertEqual(section['txn_count'], 0)
            self.assertEqual(section['reconcile'], 'failed')

    def test_text_path_vl_returned_none_rejected_with_reason(self):
        # A VL result of None on the text path must reject with a reason —
        # never an empty one.
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, _err = self._run(
                tmpdir, [_text_section()], vl_sections=None,
            )

            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertEqual(log.error_message, 'vl_parser returned no sections')
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'reconcile_error')

    def test_text_path_vl_returned_empty_list_rejected_with_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, _err = self._run(
                tmpdir, [_text_section()], vl_sections=[],
            )

            self.assertEqual(RbcMonthly.objects.count(), 0)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertEqual(log.error_message, 'vl_parser returned no sections')

    def _raw_json_log(self, processed_dir):
        """The single JSON log of the run as raw text (for scrub checks)."""
        json_dir = os.path.join(processed_dir, 'json_logs')
        names = [n for n in (os.listdir(json_dir) if os.path.isdir(json_dir) else [])
                 if n.endswith('.json')]
        self.assertEqual(len(names), 1, f'expected one JSON log, got {names}')
        with open(os.path.join(json_dir, names[0]), encoding='utf-8') as fh:
            return fh.read()

    def _assert_vl_error_scrubbed(self, processed_dir, out, err):
        """The fake account number from the exception text must appear
        nowhere: stdout, stderr, FileImportLog or the JSON log."""
        log = FileImportLog.objects.get()
        self.assertEqual(log.status, 'error')
        raw_json = self._raw_json_log(processed_dir)
        for blob in (out, err, log.error_message, raw_json):
            self.assertNotIn(FAKE_NUMBER, blob)
        # The number was replaced by the account name.
        self.assertIn(FAKE_NAME, log.error_message)

    def test_vl_error_text_scrubbed_on_fallback_path(self):
        boom = RuntimeError(f'VL exploded for account {FAKE_NUMBER}')
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, err = self._run(
                tmpdir, [self._bad_self_check_text()], vl_side_effect=boom,
            )
            self._assert_vl_error_scrubbed(processed_dir, out, err)

    def test_vl_error_text_scrubbed_on_text_path(self):
        boom = RuntimeError(f'VL exploded for account {FAKE_NUMBER}')
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, err = self._run(
                tmpdir, [_text_section()], vl_side_effect=boom,
            )
            self._assert_vl_error_scrubbed(processed_dir, out, err)

    def _bad_self_check_text(self):
        """A text section that fails the self-check → VL fallback path."""
        bad_text = _text_section()
        bad_text['end_total_market_value'] = Decimal('999.99')
        return bad_text

    def test_fallback_vl_error_returns_full_tuple_and_moves_to_errors(self):
        # The fallback early return must be the full 11-value tuple with
        # verdict 'error': the file goes to errors/ and the run continues.
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, err = self._run(
                tmpdir, [self._bad_self_check_text()],
                vl_side_effect=RuntimeError('boom'),
            )

            self.assertIn('falling back to VL', out)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn('boom', log.error_message)
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'error')
            # The run continued to the summary.
            self.assertIn('Inbox Import Summary', out)

    def test_fallback_no_sections_returns_full_tuple_and_moves_to_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(
                tmpdir, [self._bad_self_check_text()], vl_sections=None,
            )

            self.assertIn('falling back to VL', out)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'error')
            self.assertIn('no sections', '; '.join(payload['error_messages']))
            # The run continued to the summary.
            self.assertIn('Inbox Import Summary', out)

    def test_fallback_vl_error_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir, out, err = self._run(
                tmpdir, [self._bad_self_check_text()],
                vl_side_effect=RuntimeError('boom'), dry_run=True,
            )

            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(FileImportLog.objects.count(), 0)
            json_dir = os.path.join(processed_dir, 'json_logs')
            self.assertEqual(
                os.listdir(json_dir) if os.path.isdir(json_dir) else [], [])
            # The file was not moved.
            self.assertEqual(len(os.listdir(inbox_dir)), 1)
            # The run continued to the summary.
            self.assertIn('Inbox Import Summary', out)

    def test_dry_run_reconciling_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir, out, _err = self._run(
                tmpdir, [_text_section()], vl_sections=[_vl_section()],
                dry_run=True,
            )

            self.assertIn('verdict=imported', out)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(FileImportLog.objects.count(), 0)
            json_dir = os.path.join(processed_dir, 'json_logs')
            self.assertEqual(os.listdir(json_dir) if os.path.isdir(json_dir) else [], [])
            # The file was not moved.
            self.assertEqual(len(os.listdir(inbox_dir)), 1)
