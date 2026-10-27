"""End-to-end tests for the import checks (phase 3, step 13b review).

Synthetic statement text (the text-parser fixtures from
``imports.tests.test_parsers_pdf``) is written to a temp inbox as ``.pdf``
files and run through ``manage_inbox_import`` with only the VL classifier
mocked — the real text parser does the work. SQLite only, fake accounts
(90000001+).

Covered:
- the same monthly file twice: the second run is a duplicate, adds no rows;
- same account + date + currency with different content: conflict, nothing
  stored, file in errors/;
- a CAD + USD file where only USD conflicts: nothing stored (all-or-nothing);
- a stored row with an empty content_hash: conflict;
- a date failure: nothing stored, file in errors/;
- after deleting a file's rows the same file imports again (a FileImportLog
  row alone does not block re-import);
- a dry run shows the conflict verdict and writes nothing;
- the same Holdings CSV twice is a duplicate, and no FileImportLog filename
  or output line carries the fake account digits.
"""

import hashlib
import io
import os
import tempfile
from datetime import date, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import (
    Account,
    FileImportLog,
    PositionHistory,
    RbcMonthly,
)
from core.utils.account_fingerprint import fingerprint_account_number
from core.utils.safe_names import is_safe_file_name
from imports.tests.test_parsers_pdf import (
    CAD_ONLY_STATEMENT,
    MULTI_CURRENCY_STATEMENT,
)

# The statement fixtures carry '900-00006-1-8' → 90000006 (ACCOUNT_F).
FAKE_NUMBER = "90000006"
FAKE_NAME = "ACCOUNT_F"


def _future_dated_statement(today):
    """CAD_ONLY_STATEMENT with the statement date 60 days in the future and
    no *Date of Last Statement* line: the text self-check passes (no gap rule
    without a last date) but the command's future-date rule must reject it."""
    stmt = today + timedelta(days=60)
    return (
        CAD_ONLY_STATEMENT
        .replace('Order Execution Only SEPT 30',
                 f'Order Execution Only {stmt:%b} {stmt.day}')
        .replace('Cdn. Dollar Statement 2025',
                 f'Cdn. Dollar Statement {stmt.year}')
        .replace('Date of Last Statement: AUG. 29, 2025\n', '')
    )


class MonthlyImportChecksTests(TestCase):
    """manage_inbox_import on synthetic monthly statement text."""

    def setUp(self):
        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC", owner=FAKE_NAME,
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0006",
        )

    def _run(self, tmpdir, content, dry_run=False):
        inbox_dir = os.path.join(tmpdir, 'inbox')
        processed_dir = os.path.join(tmpdir, 'processed')
        os.makedirs(inbox_dir, exist_ok=True)
        pdf_path = os.path.join(
            inbox_dir, f'RBC Monthly Statement {FAKE_NUMBER}.pdf'
        )
        with open(pdf_path, 'w', encoding='utf-8') as fh:
            fh.write(content)

        out, err = io.StringIO(), io.StringIO()
        args = ['--inbox-dir', inbox_dir]
        if dry_run:
            args.append('--dry-run')
        with patch(
            'core.management.commands.manage_inbox_import.classify_pdf',
            return_value='monthly',
        ):
            with override_settings(
                IMPORT_PROCESSED_DIR=processed_dir,
                IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
            ):
                call_command('manage_inbox_import', *args, stdout=out, stderr=err)
        return processed_dir, out.getvalue(), err.getvalue()

    def _child_counts(self):
        from core.models import RbcMonthlyBalance, RbcMonthlyHolding

        return (RbcMonthlyBalance.objects.count(),
                RbcMonthlyHolding.objects.count())

    def test_same_monthly_file_twice_second_run_is_duplicate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _processed, out1, _ = self._run(tmpdir, CAD_ONLY_STATEMENT)
            self.assertIn('Imported monthly', out1)
            self.assertEqual(RbcMonthly.objects.count(), 1)
            first_hash = RbcMonthly.objects.get().content_hash
            children_before = self._child_counts()

            # Same content again (new file name): duplicate — no new rows.
            _processed, out2, _ = self._run(tmpdir, CAD_ONLY_STATEMENT)
            self.assertIn('Duplicate content', out2)
            self.assertEqual(RbcMonthly.objects.count(), 1)
            self.assertEqual(RbcMonthly.objects.get().content_hash, first_hash)
            self.assertEqual(self._child_counts(), children_before)
            self.assertEqual(
                FileImportLog.objects.filter(status='duplicate').count(), 1
            )

    def _stored_conflict_row(self, currency='CAD', content_hash='f' * 64):
        return RbcMonthly.objects.create(
            account=self.account,
            file_name=f'2025-09-30_monthly_{FAKE_NAME}_ffffffff.pdf',
            statement_date=date(2025, 9, 30),
            currency=currency,
            content_hash=content_hash,
        )

    def test_same_key_different_content_is_conflict(self):
        self._stored_conflict_row()
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(tmpdir, CAD_ONLY_STATEMENT)

            # Nothing from the file was stored…
            self.assertEqual(RbcMonthly.objects.count(), 1)
            self.assertEqual(
                RbcMonthly.objects.get().content_hash, 'f' * 64
            )
            self.assertEqual(self._child_counts(), (0, 0))
            # …the file went to errors/ under a safe name…
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            self.assertTrue(is_safe_file_name(moved[0]))
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn('Conflict', log.error_message)
            self.assertIn('Conflict', err)

    def test_multi_currency_only_usd_conflicts_stores_nothing(self):
        # Only the USD section conflicts — the CAD section must not be stored
        # either (all-or-nothing).
        self._stored_conflict_row(currency='USD', content_hash='e' * 64)
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(tmpdir, MULTI_CURRENCY_STATEMENT)

            self.assertEqual(RbcMonthly.objects.count(), 1)
            self.assertEqual(
                RbcMonthly.objects.filter(currency='CAD').count(), 0
            )
            self.assertEqual(self._child_counts(), (0, 0))
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            self.assertIn('Conflict', err)

    def test_stored_row_with_empty_content_hash_is_conflict(self):
        # A stored row without a content_hash counts as different content.
        self._stored_conflict_row(content_hash='')
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(tmpdir, CAD_ONLY_STATEMENT)

            self.assertEqual(RbcMonthly.objects.count(), 1)
            self.assertEqual(self._child_counts(), (0, 0))
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            self.assertIn('Conflict', err)

    def test_date_failure_stores_nothing_and_goes_to_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, _out, err = self._run(
                tmpdir, _future_dated_statement(timezone.now().date())
            )

            self.assertEqual(RbcMonthly.objects.count(), 0)
            self.assertEqual(self._child_counts(), (0, 0))
            moved = os.listdir(os.path.join(processed_dir, 'errors'))
            self.assertEqual(len(moved), 1)
            self.assertTrue(is_safe_file_name(moved[0]))
            log = FileImportLog.objects.get()
            self.assertEqual(log.status, 'error')
            self.assertIn('in the future', log.error_message)
            self.assertIn('in the future', err)

    def test_after_deleting_rows_same_file_imports_again(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _processed, out1, _ = self._run(tmpdir, CAD_ONLY_STATEMENT)
            self.assertEqual(RbcMonthly.objects.count(), 1)

            # Delete the file's rows; the FileImportLog row stays.
            RbcMonthly.objects.all().delete()
            self.assertTrue(
                FileImportLog.objects.filter(status='success').exists()
            )

            # The same content imports again — a log row alone is not a
            # duplicate for PDFs.
            _processed, out2, _ = self._run(tmpdir, CAD_ONLY_STATEMENT)
            self.assertIn('Imported monthly', out2)
            self.assertNotIn('Duplicate content', out2)
            self.assertEqual(RbcMonthly.objects.count(), 1)

    def test_dry_run_shows_conflict_verdict_and_writes_nothing(self):
        self._stored_conflict_row()
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir, out, _err = self._run(
                tmpdir, CAD_ONLY_STATEMENT, dry_run=True
            )

            # The real verdict is reported…
            self.assertIn('verdict=conflict', out)
            # …and nothing was written: no rows, no log, file not moved.
            self.assertEqual(RbcMonthly.objects.count(), 1)
            self.assertEqual(
                RbcMonthly.objects.get().content_hash, 'f' * 64
            )
            self.assertEqual(FileImportLog.objects.count(), 0)
            inbox_dir = os.path.join(tmpdir, 'inbox')
            self.assertEqual(len(os.listdir(inbox_dir)), 1)
            json_dir = os.path.join(processed_dir, 'json_logs')
            self.assertEqual(
                os.listdir(json_dir) if os.path.isdir(json_dir) else [], []
            )


class HoldingsImportChecksTests(TestCase):
    """import_csv --holdings: duplicate rule and safe names (no account
    digits in stored or printed file names)."""

    FAKE_NUMBER = "90000004"  # ACCOUNT_D — only in the file name
    CSV_BODY = (
        '"Holdings Export as of January 1, 2026 10:59:55 AM ET"\n'
        '"Balances as of January 1, 2026"\n'
        'Group,Product,Symbol,Name,Quantity,Last Price,Currency,'
        'Change $,Change %,Padding,Total Book Cost,Total Market Value,'
        'Padding,Average Cost\n'
        'Group,Common Shares,VDY,Vanguard ETF,1000,50.00,CAD,0,0%,X,'
        '41449.20,50000.00,X,41.4492\n'
    )

    def setUp(self):
        self.account = Account.objects.create(
            name='ACCOUNT_D', broker='RBC', owner='ACCOUNT_D',
            fingerprint=fingerprint_account_number(self.FAKE_NUMBER),
            last_4='0004',
        )

    def _write_csv(self, inbox_dir):
        path = os.path.join(
            inbox_dir, f'Holdings {self.FAKE_NUMBER} January 1, 2026.csv'
        )
        with open(path, 'w', newline='', encoding='utf-8-sig') as fh:
            fh.write(self.CSV_BODY)
        return path

    def _run(self, tmpdir):
        inbox_dir = os.path.join(tmpdir, 'inbox')
        processed_dir = os.path.join(tmpdir, 'processed')
        os.makedirs(inbox_dir, exist_ok=True)
        out, err = io.StringIO(), io.StringIO()
        with override_settings(
            IMPORT_INBOX_DIR=inbox_dir,
            IMPORT_PROCESSED_DIR=processed_dir,
            IMPORT_JSON_DIR=os.path.join(processed_dir, 'json_logs'),
        ):
            call_command('import_csv', '--holdings', stdout=out, stderr=err)
        return processed_dir, out.getvalue(), err.getvalue()

    def test_same_holdings_csv_twice_is_duplicate_without_account_digits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            inbox_dir = os.path.join(tmpdir, 'inbox')
            os.makedirs(inbox_dir)
            path = self._write_csv(inbox_dir)
            full_hash = hashlib.sha256(
                open(path, 'rb').read()
            ).hexdigest()
            h8 = full_hash[:8]

            processed_dir, out1, err1 = self._run(tmpdir)
            self.assertIn('verdict=imported', out1)
            safe_name = f'2026-01-01_holdings_{h8}.csv'
            self.assertIn(safe_name, out1)
            self.assertEqual(PositionHistory.objects.count(), 1)

            # Same content again (the first file was moved away): duplicate.
            self._write_csv(inbox_dir)
            _processed, out2, err2 = self._run(tmpdir)
            self.assertIn('Duplicate content', out2)
            self.assertEqual(PositionHistory.objects.count(), 1)
            self.assertEqual(
                FileImportLog.objects.filter(status='duplicate').count(), 1
            )

            # No FileImportLog filename or output line carries the account
            # digits from the original file name.
            for log in FileImportLog.objects.all():
                self.assertNotIn(self.FAKE_NUMBER, log.filename)
            for text in (out1, out2, err1, err2):
                for line in text.splitlines():
                    self.assertNotIn(self.FAKE_NUMBER, line)
