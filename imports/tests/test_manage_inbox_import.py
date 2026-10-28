"""Tests for manage_inbox_import safe file names (phase 2 step 11, plan step 5).

The classifier and VL parser are mocked; a fake PDF with the fake account
number in its original name is imported and every produced artifact (moved
file name, JSON log name and payload, stored file_name rows) must be free of
the number's digit run.
"""

import hashlib
import io
import os
import re
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import (
    FileImportLog,
    RbcAnnual,
    RbcMonthly,
    RbcMonthlyHolding,
    RbcMonthlyTransaction,
)
from core.utils.account_fingerprint import fingerprint_account_number
from core.utils.safe_names import hash8_of, is_safe_file_name

# Fake account (90000001-90000008 are the reserved test numbers).
FAKE_NUMBER = "90000004"
FAKE_NAME = "ACCOUNT_D"

FAKE_PDF_CONTENT = b"%PDF-1.4\nfake statement content for safe-name tests\n"


def _monthly_section():
    """A VL section that passes the 13c-b cash gate (90.00 opening +
    10.00 credit == 100.00 closing) and the 13d VL section checks: no
    holdings, so end total == cash closing (100.00)."""
    return {
        'statement_date': date(2025, 9, 30),
        'last_statement_date': date(2025, 8, 29),
        'account_number': FAKE_NUMBER,
        'currency': 'CAD',
        'fx_rate': Decimal('1.0000'),
        'begin_total_market_value': Decimal('100.00'),
        'end_total_market_value': Decimal('100.00'),
        'transactions': [
            {'date': date(2025, 9, 15), 'activity_type': 'DEPOSIT',
             'description': 'TEST DEPOSIT', 'symbol': '',
             'quantity': Decimal('0'), 'price': Decimal('0.0000'),
             'debit': Decimal('0.00'), 'credit': Decimal('10.00')},
        ],
        'balances': [
            {'account_type': 'Cash', 'opening': Decimal('90.00'),
             'closing': Decimal('100.00'), 'market_value': Decimal('100.00'),
             'percentage': Decimal('5.00')},
        ],
        'holdings': [],
        # The number repeated in a free-text field must not survive either.
        'raw_text': f'acct {FAKE_NUMBER} statement end',
    }


def _annual_data():
    return {
        'account_number': FAKE_NUMBER,
        'report_year': 2025,
        'report_period_start': date(2025, 1, 1),
        'report_period_end': date(2025, 12, 31),
        'account_type_code': '',
        # The number repeated in a free-text field must not survive either.
        'raw_text': f'Annual report for {FAKE_NUMBER} end',
        'cad': {
            'bal_beginning_value': Decimal('100.00'),
            'bal_ending_value': Decimal('120.00'),
        },
    }


def _assert_no_number_anywhere(processed_dir: str):
    """No produced file name or JSON payload may carry the fake number."""
    for root, _dirs, files in os.walk(processed_dir):
        for fname in files:
            assert FAKE_NUMBER not in fname, f"number leaked into file name {fname!r}"
            if fname.endswith('.json'):
                with open(os.path.join(root, fname), encoding='utf-8') as fh:
                    assert FAKE_NUMBER not in fh.read(), (
                        f"number leaked into JSON payload {fname!r}"
                    )


class ManageInboxImportSafeNameTests(TestCase):
    def setUp(self):
        from core.models import Account

        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC",
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0004",
        )

    def _run_command(self, tmpdir, pdf_name, doc_type, sections=None, annual_data=None):
        inbox_dir = os.path.join(tmpdir, 'inbox')
        processed_dir = os.path.join(tmpdir, 'processed')
        os.makedirs(inbox_dir)
        with open(os.path.join(inbox_dir, pdf_name), 'wb') as f:
            f.write(FAKE_PDF_CONTENT)

        with patch(
            'core.management.commands.manage_inbox_import.classify_pdf',
            return_value=doc_type,
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl',
            return_value=sections,
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_annual_report_vl',
            return_value=annual_data if annual_data is not None else {},
        ):
            with override_settings(
                IMPORT_PROCESSED_DIR=processed_dir,
                IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
            ):
                call_command('manage_inbox_import', '--inbox-dir', inbox_dir)
        return processed_dir

    def test_monthly_import_uses_safe_names_and_scrubbed_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._run_command(
                tmpdir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf', 'monthly',
                [_monthly_section()],
            )

            h8 = hash8_of(FAKE_PDF_CONTENT)
            safe_name = f'2025-09-30_monthly_{FAKE_NAME}_{h8}.pdf'
            self.assertTrue(is_safe_file_name(safe_name))

            # The PDF was moved under the safe name (original name is gone).
            self.assertTrue(os.path.exists(os.path.join(processed_dir, safe_name)))
            self.assertFalse(
                os.path.exists(os.path.join(processed_dir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf'))
            )

            # Stored rows carry the safe name.
            self.assertEqual(RbcMonthly.objects.get().file_name, safe_name)
            self.assertEqual(FileImportLog.objects.get().filename, safe_name)

            # JSON log: safe stem, payload scrubbed to the account name.
            json_path = os.path.join(
                processed_dir, 'json_logs', f'2025-09-30_monthly_{FAKE_NAME}_{h8}.json'
            )
            self.assertTrue(os.path.exists(json_path))
            with open(json_path, encoding='utf-8') as fh:
                payload = fh.read()
            self.assertNotIn(FAKE_NUMBER, payload)
            self.assertIn(FAKE_NAME, payload)

            _assert_no_number_anywhere(processed_dir)

    def test_unresolvable_monthly_gets_unknown_account_name(self):
        # No Account row matches the parsed number → UNKNOWN_ACCOUNT safe name.
        self.account.delete()
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._run_command(
                tmpdir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf', 'monthly',
                [_monthly_section()],
            )

            errors_dir = os.path.join(processed_dir, 'errors')
            moved = os.listdir(errors_dir)
            self.assertEqual(len(moved), 1)
            self.assertIn('UNKNOWN_ACCOUNT', moved[0])
            self.assertTrue(is_safe_file_name(moved[0]))
            self.assertEqual(RbcMonthly.objects.count(), 0)
            _assert_no_number_anywhere(processed_dir)

    def _run_monthly_without_usable_date(self, statement_date):
        section = _monthly_section()
        section['statement_date'] = statement_date
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._run_command(
                tmpdir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf', 'monthly',
                [section],
            )
            errors_dir = os.path.join(processed_dir, 'errors')
            moved = os.listdir(errors_dir)
            self.assertEqual(len(moved), 1)
            self.assertIn('UNKNOWN_ACCOUNT', moved[0])
            self.assertTrue(is_safe_file_name(moved[0]))
            self.assertEqual(RbcMonthly.objects.count(), 0)
            _assert_no_number_anywhere(processed_dir)

    def test_monthly_section_without_statement_date_uses_unknown_name(self):
        # No statement date: the run must not stop and the PDF gets the
        # unattributable UNKNOWN_ACCOUNT name (moved to errors/).
        self._run_monthly_without_usable_date(None)

    def test_monthly_section_with_malformed_statement_date_does_not_stop_run(self):
        # A truthy but unusable date used to make safe_file_name raise and
        # stop the whole run; it is now treated as unattributable too.
        self._run_monthly_without_usable_date('OCT 31, 2025')

    def test_annual_import_uses_safe_names_and_scrubbed_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._run_command(
                tmpdir, f'RBC Annual Report {FAKE_NUMBER}.pdf', 'annual',
                annual_data=_annual_data(),
            )

            h8 = hash8_of(FAKE_PDF_CONTENT)
            safe_name = f'2025_annual_{FAKE_NAME}_{h8}.pdf'
            self.assertTrue(is_safe_file_name(safe_name))

            # The PDF was moved under the safe name (original name is gone).
            self.assertTrue(os.path.exists(os.path.join(processed_dir, safe_name)))
            self.assertFalse(
                os.path.exists(os.path.join(processed_dir, f'RBC Annual Report {FAKE_NUMBER}.pdf'))
            )

            # Stored rows carry the safe name.
            self.assertEqual(RbcAnnual.objects.get().file_name, safe_name)
            self.assertEqual(FileImportLog.objects.get().filename, safe_name)

            # JSON log: safe stem, payload scrubbed (field and raw_text).
            json_path = os.path.join(
                processed_dir, 'json_logs', f'2025_annual_{FAKE_NAME}_{h8}.json'
            )
            self.assertTrue(os.path.exists(json_path))
            with open(json_path, encoding='utf-8') as fh:
                payload = fh.read()
            self.assertNotIn(FAKE_NUMBER, payload)
            self.assertIn(FAKE_NAME, payload)

            _assert_no_number_anywhere(processed_dir)

    def test_unknown_type_gets_unknown_account_error_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._run_command(
                tmpdir, f'Mystery Document {FAKE_NUMBER}.pdf', 'unknown', None,
            )

            h8 = hash8_of(FAKE_PDF_CONTENT)
            errors_dir = os.path.join(processed_dir, 'errors')
            moved = os.listdir(errors_dir)
            self.assertEqual(len(moved), 1)
            name = moved[0]
            self.assertIn('unknown_UNKNOWN_ACCOUNT', name)
            self.assertTrue(name.endswith(f'_{h8}.pdf'))
            self.assertTrue(is_safe_file_name(name))

            json_logs = os.path.join(processed_dir, 'json_logs')
            json_names = os.listdir(json_logs)
            self.assertEqual(len(json_names), 1)
            self.assertIn('_ERROR.json', json_names[0])
            self.assertEqual(FileImportLog.objects.get().status, 'error')
            _assert_no_number_anywhere(processed_dir)


class ManageInboxImportVerdictSummaryTests(TestCase):
    """End summary: files counted per verdict — one line per verdict
    (imported, duplicate, conflict, date_error, reconcile_error,
    vl_check_error, error) in normal and dry runs, existing lines kept."""

    def setUp(self):
        from core.models import Account

        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC",
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0004",
        )

    def _populate_inbox(self, inbox_dir):
        dup_content = b"%PDF-1.4\nfake duplicate content\n"
        with open(os.path.join(inbox_dir, f'A {FAKE_NUMBER}.pdf'), 'wb') as f:
            f.write(FAKE_PDF_CONTENT)  # -> imported (mocked VL section)
        with open(os.path.join(inbox_dir, f'B {FAKE_NUMBER}.pdf'), 'wb') as f:
            f.write(dup_content)       # -> duplicate (row pre-created below)
        with open(os.path.join(inbox_dir, f'C {FAKE_NUMBER}.pdf'), 'wb') as f:
            f.write(b"%PDF-1.4\nunknown content\n")  # -> error (unknown type)

        RbcMonthly.objects.create(
            account=self.account,
            file_name='2025-08-31_monthly_ACCOUNT_D_00000000.pdf',
            statement_date=date(2025, 8, 31),
            content_hash=hashlib.sha256(dup_content).hexdigest(),
        )

    def _run(self, inbox_dir, processed_dir, dry_run):
        args = ['--inbox-dir', inbox_dir]
        if dry_run:
            args.append('--dry-run')
        out = io.StringIO()
        with patch(
            'core.management.commands.manage_inbox_import.classify_pdf',
            side_effect=lambda p: {
                'A': 'monthly', 'B': 'monthly', 'C': 'unknown',
            }[os.path.basename(p)[0]],
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl',
            return_value=[_monthly_section()],
        ):
            with override_settings(
                IMPORT_PROCESSED_DIR=processed_dir,
                IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
            ):
                call_command('manage_inbox_import', *args, stdout=out)
        return out.getvalue()

    def _assert_summary(self, logged):
        summary = logged.split('=== Inbox Import Summary ===')[1]
        # The existing lines are kept…
        self.assertIn('PDFs found:', summary)
        self.assertIn('JSON logs:', summary)
        # …and there is one line per verdict with the right count.
        for verdict, expected in (
            ('imported', 1), ('duplicate', 1), ('conflict', 0),
            ('date_error', 0), ('reconcile_error', 0),
            ('vl_check_error', 0), ('error', 1),
        ):
            with self.subTest(verdict=verdict):
                self.assertIsNotNone(
                    re.search(rf'(?m)^\s+{verdict}:\s+{expected}\s*$', summary),
                    f'{verdict} line not found in the summary',
                )

    def test_summary_counts_files_per_verdict(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir = os.path.join(tmpdir, 'processed')
            os.makedirs(inbox_dir)
            self._populate_inbox(inbox_dir)

            # Dry run: the per-verdict lines are printed, nothing written…
            logged = self._run(inbox_dir, processed_dir, dry_run=True)
            self._assert_summary(logged)
            self.assertEqual(RbcMonthly.objects.count(), 1)  # only pre-created

            # …and a normal run reports the same per-verdict lines.
            logged = self._run(inbox_dir, processed_dir, dry_run=False)
            self._assert_summary(logged)
            self.assertEqual(RbcMonthly.objects.count(), 2)


class ManageInboxImportTextFirstTests(TestCase):
    """Step 12b step 5: text-first parsing with the VL fallback.

    Test 6 (command part): a self-check failure sends the file to VL and the
    log line names only fields/currencies. Test 7: the JSON log of a
    text-parsed file carries no account digits (the scrubber handles the text
    parser's payload, including its extra income_summary/contribution_summary
    keys).
    """

    def setUp(self):
        from core.models import Account

        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC",
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0004",
        )

    def _run_monthly_with_text(self, tmpdir, text_sections):
        inbox_dir = os.path.join(tmpdir, 'inbox')
        processed_dir = os.path.join(tmpdir, 'processed')
        os.makedirs(inbox_dir)
        with open(os.path.join(inbox_dir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf'), 'wb') as f:
            f.write(FAKE_PDF_CONTENT)

        out = io.StringIO()
        with patch(
            'core.management.commands.manage_inbox_import.classify_pdf',
            return_value='monthly',
        ), patch(
            'core.management.commands.manage_inbox_import.parse_rbc_pdf_multi',
            return_value=text_sections,
        ) as mock_text, patch(
            'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl',
            return_value=[_monthly_section()],
        ) as mock_vl:
            with override_settings(
                IMPORT_PROCESSED_DIR=processed_dir,
                IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
            ):
                call_command('manage_inbox_import', '--inbox-dir', inbox_dir, stdout=out)
        return processed_dir, out.getvalue(), mock_text, mock_vl

    def test_self_check_failure_falls_back_to_vl(self):
        # Text section fails the self-check (holdings + cash != end total) →
        # the file goes to VL and the log line names only fields/currencies.
        bad = _monthly_section()
        bad['end_total_market_value'] = Decimal('999.99')

        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, logged, mock_text, mock_vl = self._run_monthly_with_text(
                tmpdir, [bad],
            )

            mock_text.assert_called_once()
            mock_vl.assert_called_once()
            line = next(l for l in logged.splitlines() if 'falling back to VL' in l)
            self.assertIn('CAD holdings+cash != end_total_market_value', line)
            # The reason carries no values: no digit run at all.
            self.assertNotRegex(line, r'\d')
            # The import proceeded via the VL section.
            self.assertEqual(RbcMonthly.objects.count(), 1)
            json_path = os.path.join(
                processed_dir, 'json_logs',
                f'2025-09-30_monthly_{FAKE_NAME}_{hash8_of(FAKE_PDF_CONTENT)}.json',
            )
            with open(json_path, encoding='utf-8') as fh:
                payload = fh.read()
            self.assertIn('"parser_holdings": "vl"', payload)
            self.assertIn('"parser_transactions": "vl"', payload)
            _assert_no_number_anywhere(processed_dir)

    def test_text_parsed_file_json_log_has_no_account_digits(self):
        # The real text parser (no parser mocks) on a synthetic statement that
        # passes the self-check: imported via the text path, and the JSON log
        # must be free of the account's digits.
        from core.models import Account
        from imports.tests.test_parsers_pdf import CAD_ONLY_STATEMENT

        # 900-00006-1-8 normalises to 90000006 (ACCOUNT_F).
        Account.objects.create(
            name='ACCOUNT_F', broker='RBC',
            fingerprint=fingerprint_account_number('90000006'), last_4='0006',
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            processed_dir = os.path.join(tmpdir, 'processed')
            os.makedirs(inbox_dir)
            pdf_name = f'RBC Monthly Statement {FAKE_NUMBER}.pdf'
            with open(os.path.join(inbox_dir, pdf_name), 'w', encoding='utf-8') as f:
                f.write(CAD_ONLY_STATEMENT)

            out = io.StringIO()
            with patch(
                'core.management.commands.manage_inbox_import.classify_pdf',
                return_value='monthly',
            ), patch(
                # Step 13c-b: the text path always calls VL for
                # transactions; a mock that reconciles with the statement's
                # cash (90.00 opening + 10.00 credit == 100.00 closing).
                'core.management.commands.manage_inbox_import.parse_rbc_monthly_vl',
                return_value=[{
                    'currency': 'CAD',
                    'transactions': [
                        {'date': date(2025, 9, 15),
                         'activity_type': 'DEPOSIT',
                         'description': 'TEST DEPOSIT', 'symbol': '',
                         'quantity': Decimal('0'),
                         'price': Decimal('0.0000'),
                         'debit': Decimal('0.00'),
                         'credit': Decimal('10.00')},
                    ],
                }],
            ):
                with override_settings(
                    IMPORT_PROCESSED_DIR=processed_dir,
                    IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
                ):
                    call_command('manage_inbox_import', '--inbox-dir', inbox_dir, stdout=out)

            logged = out.getvalue()
            self.assertIn('Parser: text (self-check passed)', logged)

            # Imported via the text path: statement and holdings stored…
            self.assertEqual(RbcMonthly.objects.count(), 1)
            self.assertEqual(RbcMonthlyHolding.objects.count(), 2)
            # …and the one transaction comes from the VL mock (13c-b).
            self.assertEqual(RbcMonthlyTransaction.objects.count(), 1)

            h8 = hash8_of(CAD_ONLY_STATEMENT.encode('utf-8'))
            json_path = os.path.join(
                processed_dir, 'json_logs', f'2025-09-30_monthly_ACCOUNT_F_{h8}.json'
            )
            self.assertTrue(os.path.exists(json_path))
            with open(json_path, encoding='utf-8') as fh:
                payload = fh.read()
            self.assertNotIn('90000006', payload)
            self.assertNotIn('900-00006-1-8', payload)
            self.assertIn('ACCOUNT_F', payload)
            self.assertIn('"parser_holdings": "text"', payload)
            self.assertIn('"parser_transactions": "vl"', payload)
            self.assertIn('"self_check_reasons": []', payload)
