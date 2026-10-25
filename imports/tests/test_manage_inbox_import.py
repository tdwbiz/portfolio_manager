"""Tests for manage_inbox_import safe file names (phase 2 step 11, plan step 5).

The classifier and VL parser are mocked; a fake PDF with the fake account
number in its original name is imported and every produced artifact (moved
file name, JSON log name and payload, stored file_name rows) must be free of
the number's digit run.
"""

import os
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import FileImportLog, RbcAnnual, RbcMonthly
from core.utils.account_fingerprint import fingerprint_account_number
from core.utils.safe_names import hash8_of, is_safe_file_name

# Fake account (90000001-90000008 are the reserved test numbers).
FAKE_NUMBER = "90000004"
FAKE_NAME = "ACCOUNT_D"

FAKE_PDF_CONTENT = b"%PDF-1.4\nfake statement content for safe-name tests\n"


def _monthly_section():
    return {
        'statement_date': date(2025, 9, 30),
        'last_statement_date': date(2025, 8, 29),
        'account_number': FAKE_NUMBER,
        'currency': 'CAD',
        'fx_rate': Decimal('1.0000'),
        'begin_total_market_value': Decimal('100.00'),
        'end_total_market_value': Decimal('105.00'),
        'transactions': [],
        'balances': [],
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
