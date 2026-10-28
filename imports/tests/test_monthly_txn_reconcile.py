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
from core.utils.import_checks import (
    reconcile_section,
    self_check_vl_sections,
)
from imports.llm_client import VLParseError
from imports.parsers_monthly_vl import _coerce_response

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

    def _run_two_files(self, tmpdir, dry_run=False):
        """Two inbox PDFs (different content); classify_pdf raises on the
        first and classifies the second as monthly. Returns
        (inbox_dir, processed_dir, out, err)."""
        inbox_dir = os.path.join(tmpdir, 'inbox')
        processed_dir = os.path.join(tmpdir, 'processed')
        os.makedirs(inbox_dir)
        for label, content in (('A', b'%PDF-1.4\nfirst file content\n'),
                               ('B', b'%PDF-1.4\nsecond file content\n')):
            with open(os.path.join(inbox_dir,
                                   f'RBC Monthly Statement {FAKE_NUMBER} {label}.pdf'),
                      'wb') as fh:
                fh.write(content)

        args = ['--inbox-dir', inbox_dir]
        if dry_run:
            args.append('--dry-run')
        out, err = io.StringIO(), io.StringIO()
        with patch(
            'core.management.commands.manage_inbox_import.classify_pdf',
            side_effect=[RuntimeError('classifier blew up'), 'monthly'],
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_pdf_multi',
            return_value=[_text_section()],
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl',
            return_value=[_vl_section()],
        ):
            with override_settings(
                IMPORT_PROCESSED_DIR=processed_dir,
                IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
            ):
                call_command('manage_inbox_import', *args, stdout=out, stderr=err)
        return inbox_dir, processed_dir, out.getvalue(), err.getvalue()

    def test_unexpected_error_on_first_file_does_not_stop_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _inbox_dir, processed_dir, out, _err = self._run_two_files(tmpdir)

            # The first file was parked in errors/ with a FileImportLog row…
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            logs = list(FileImportLog.objects.all())
            self.assertEqual([l.status for l in sorted(logs, key=lambda l: l.pk)],
                             ['error', 'success'])
            # …the second file imported normally…
            self.assertEqual(RbcMonthly.objects.count(), 1)
            # …and the run reached the summary with exactly one error.
            self.assertIn('Inbox Import Summary', out)
            self.assertRegex(out, r'Errors:\s+1\b')

    def test_unexpected_error_dry_run_reports_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir, processed_dir, out, _err = self._run_two_files(
                tmpdir, dry_run=True,
            )

            self.assertIn('verdict=error', out)
            self.assertIn('Inbox Import Summary', out)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(FileImportLog.objects.count(), 0)
            json_dir = os.path.join(processed_dir, 'json_logs')
            self.assertEqual(os.listdir(json_dir) if os.path.isdir(json_dir) else [], [])
            # Neither file was moved.
            self.assertEqual(len(os.listdir(inbox_dir)), 2)

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

    # ── Step 13d: vl_check_error, gate printing, no regex fallback ──────────

    def _fallback_vl_section(self, end_total='1600.00', book_cost='1300.00'):
        """A VL section for the fallback path that passes the cash gate
        (90 + 10 - 0 == 100) but whose holdings do not add up to the end
        total (1400 + 100 != 1600) unless the fixture is fixed."""
        return {
            'statement_date': date(2025, 9, 30),
            'last_statement_date': date(2025, 8, 29),
            'account_number': FAKE_NUMBER,
            'currency': 'CAD',
            'begin_total_market_value': Decimal('500.00'),
            'end_total_market_value': Decimal(end_total),
            'balances': [
                {'account_type': 'Cash', 'opening': Decimal('90.00'),
                 'closing': Decimal('100.00'), 'market_value': Decimal('100.00')},
            ],
            'holdings': [
                {'security': 'VANGUARD ETF', 'symbol': 'VFV',
                 'quantity': Decimal('5'), 'book_cost': Decimal(book_cost),
                 'market_price': Decimal('280.000'),
                 'market_value': Decimal('1400.00')},
            ],
            'transactions': [_vl_transaction(credit='10.00')],
        }

    def test_fallback_vl_totals_do_not_add_up_dry_run_verdict_vl_check_error(self):
        # The text self-check fails (forces the fallback); the VL section
        # passes the cash gate but holdings + cash != end total.
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir, out, _err = self._run(
                tmpdir, [self._bad_self_check_text()],
                vl_sections=[self._fallback_vl_section()], dry_run=True,
            )

            # The real verdict and the reason are printed…
            self.assertIn('verdict=vl_check_error', out)
            self.assertIn('[DRY-RUN] CAD holdings+cash != end_total_market_value',
                          out)
            # …and nothing was written: no rows, no log, file not moved.
            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(FileImportLog.objects.count(), 0)
            json_dir = os.path.join(processed_dir, 'json_logs')
            self.assertEqual(os.listdir(json_dir) if os.path.isdir(json_dir) else [], [])
            self.assertEqual(len(os.listdir(inbox_dir)), 1)

    def test_fallback_vl_unreadable_book_cost_dry_run_verdict_vl_check_error(self):
        # The VL coercion turns a missing book cost into 0; the check must
        # treat it as unreadable (Q4).
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(
                tmpdir, [self._bad_self_check_text()],
                vl_sections=[self._fallback_vl_section(book_cost='0')],
                dry_run=True,
            )

            self.assertIn('verdict=vl_check_error', out)
            self.assertIn('[DRY-RUN] CAD VFV book_cost unreadable', out)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(FileImportLog.objects.count(), 0)

    def test_fallback_vl_totals_do_not_add_up_stores_nothing(self):
        # Non-dry-run: the file goes to errors/ with a FileImportLog row and
        # the JSON log carries the verdict and the scrubbed reasons.
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(
                tmpdir, [self._bad_self_check_text()],
                vl_sections=[self._fallback_vl_section()],
            )

            self.assertEqual(RbcMonthly.objects.count(), 0)
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn('CAD holdings+cash != end_total_market_value',
                          log.error_message)
            self.assertIn('CAD holdings+cash != end_total_market_value', err)
            payload = self._json_log(processed_dir, error=True)
            self.assertEqual(payload['verdict'], 'vl_check_error')

    def test_fallback_vl_parser_raises_dry_run_verdict_error_no_regex_fallback(self):
        # There is no regex fallback anymore: the parser function must not
        # even exist, and a VL failure gives verdict error with a reason.
        import imports.parsers_monthly_vl as pmv

        self.assertFalse(hasattr(pmv, '_parse_rbc_monthly_regex'))

        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir, out, err = self._run(
                tmpdir, [self._bad_self_check_text()],
                vl_side_effect=VLParseError('VL model failed to return valid JSON'),
                dry_run=True,
            )

            self.assertIn('Parse error', err)
            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(FileImportLog.objects.count(), 0)
            json_dir = os.path.join(processed_dir, 'json_logs')
            self.assertEqual(os.listdir(json_dir) if os.path.isdir(json_dir) else [], [])
            self.assertEqual(len(os.listdir(inbox_dir)), 1)

    def test_dry_run_prints_gate_line_for_every_currency_section(self):
        text = [_text_section('CAD'),
                _text_section('USD', opening='40.00', closing='50.00')]
        vl = [_vl_section('USD'), _vl_section('CAD')]
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(
                tmpdir, text, vl_sections=vl, dry_run=True,
            )

            # One gate line per currency section, in the run's output.
            self.assertIn(
                '[CAD] cash gate: opening 90.00 credits 10.00 debits 0 '
                'closing 100.00 diff 0.00 PASS', out)
            self.assertIn(
                '[USD] cash gate: opening 40.00 credits 10.00 debits 0 '
                'closing 50.00 diff 0.00 PASS', out)

    def test_dry_run_prints_failed_gate_line_and_reasons(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(
                tmpdir, [_text_section()],
                vl_sections=[_vl_section(credit='9.99')], dry_run=True,
            )

            # The gate line shows FAIL with the difference…
            self.assertIn(
                '[CAD] cash gate: opening 90.00 credits 9.99 debits 0 '
                'closing 100.00 diff 0.01 FAIL', out)
            # …and every failed check's reason is printed in dry run too.
            self.assertIn('[DRY-RUN] CAD opening + credits - debits != closing',
                          out)
            self.assertIn('verdict=reconcile_error', out)


class DivReinPairTests(TestCase):
    """Step 13d, goal 2: a reinvested dividend is two PDF rows — DIV REIN
    (debit, whole shares bought) + a credit row whose ACTIVITY column may be
    empty with CASH DIV / DIST only in the description. Both amounts are kept
    and the section reconciles."""

    def _raw_pair(self, credit_desc='CASH DIV ON 1000 SHS VDY'):
        return {
            'account_number': FAKE_NUMBER,
            'statement_date': '2025-09-30',
            'sections': [{
                'currency': 'CAD',
                'begin_total_market_value': 0,
                'end_total_market_value': 0,
                'balances': [
                    {'account_type': 'Cash', 'opening': 90.00, 'closing': 100.00,
                     'market_value': 100.00, 'percentage': 5},
                ],
                'holdings': [],
                'transactions': [
                    {'date': '2025-09-15', 'activity_type': 'DIV REIN',
                     'description': 'REINV@C$1.00 ON 10 SHS VDY',
                     'symbol': 'VDY', 'quantity': 10, 'price': 1.00,
                     'debit': 10.00, 'credit': 0, 'commission': 0, 'notes': ''},
                    {'date': '2025-09-15', 'activity_type': '',
                     'description': credit_desc, 'symbol': 'VDY',
                     'quantity': 0, 'price': 0.01,
                     'debit': 0, 'credit': 20.00, 'commission': 0, 'notes': ''},
                ],
            }],
        }

    def test_div_rein_pair_keeps_amounts_and_reconciles(self):
        sections = _coerce_response(self._raw_pair(), '/fake/div_rein.pdf')
        txs = {tx['activity_type']: tx for tx in sections[0]['transactions']}

        self.assertIn('DIV REIN', txs)
        self.assertIn('DIVIDEND', txs)
        # The DIV REIN row keeps its printed debit (no clearing)…
        self.assertEqual(txs['DIV REIN']['debit'], Decimal('10.00'))
        self.assertEqual(txs['DIV REIN']['credit'], Decimal('0'))
        # …the empty-activity CASH DIV row is a credit row…
        self.assertEqual(txs['DIVIDEND']['credit'], Decimal('20.00'))
        self.assertEqual(txs['DIVIDEND']['debit'], Decimal('0'))
        # …and the section reconciles: 90 + 20 - 10 == 100.
        self.assertEqual(reconcile_section(sections[0]), [])

    def test_div_rein_pair_with_dist_wording(self):
        sections = _coerce_response(
            self._raw_pair(credit_desc='VDY DIST ON 1035 SHS'),
            '/fake/div_rein_dist.pdf',
        )
        txs = {tx['activity_type']: tx for tx in sections[0]['transactions']}

        self.assertIn('DIST', txs)
        self.assertEqual(txs['DIST']['credit'], Decimal('20.00'))
        self.assertEqual(reconcile_section(sections[0]), [])

    def test_empty_activity_security_name_dividend_is_other(self):
        # tdw decision (Extra 3): an empty activity type matches only income
        # wording — a security name containing DIVIDEND comes out OTHER,
        # never DIVIDEND.
        raw = self._raw_pair()
        raw['sections'][0]['transactions'] = [
            {'date': '2025-09-15', 'activity_type': '',
             'description': 'DIVIDEND FUND OF CANADA LTD', 'symbol': 'VCD',
             'quantity': 0, 'price': 0, 'debit': 0, 'credit': 0,
             'commission': 0, 'notes': ''},
        ]
        sections = _coerce_response(raw, '/fake/other.pdf')

        txs = sections[0]['transactions']
        self.assertEqual(len(txs), 1)
        self.assertEqual(txs[0]['activity_type'], 'OTHER')


class VlSectionCheckTests(TestCase):
    """Unit tests for self_check_vl_sections (step 13d, Q4).

    There is no begin-total check: VL's balances often list only cash for a
    USD section, so a begin total far above the balance sum is normal there
    (the begin total is checked against the previous month's end in step 14).
    """

    def _section(self, end_total='1500.00'):
        return {
            'currency': 'CAD',
            'statement_date': date(2025, 9, 30),
            'last_statement_date': date(2025, 8, 29),
            # Far above the 1500.00 balance sum: must NOT be flagged.
            'begin_total_market_value': Decimal('99999.00'),
            'end_total_market_value': Decimal(end_total),
            'balances': [
                {'account_type': 'Cash', 'opening': Decimal('90.00'),
                 'closing': Decimal('100.00'), 'market_value': Decimal('100.00')},
                {'account_type': 'Common Shares', 'market_value': Decimal('1400.00')},
            ],
            'holdings': [
                {'security': 'VANGUARD ETF', 'symbol': 'VFV',
                 'quantity': Decimal('5'), 'book_cost': Decimal('1300.00'),
                 'market_price': Decimal('280.000'),
                 'market_value': Decimal('1400.00')},
            ],
            'transactions': [],
        }

    def test_consistent_section_has_no_reasons(self):
        # end total 1500 = holdings 1400 + cash 100; the begin total is
        # not checked (see class docstring).
        reasons = self_check_vl_sections(
            [self._section()], today=date(2026, 10, 27),
        )
        self.assertEqual(reasons, [])
