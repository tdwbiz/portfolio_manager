"""Tests for the import_csv management command (Activity CSVs).

Ported from the deleted auto_import command tests: RBC/inbox/FileImportLog/
duplicate-hash cases only (IB and --broker cases dropped — import_csv is
RBC-only, see docs/plans/import-paths.md decision Q1).
"""

import os
import shutil
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import FileImportLog, Transaction, Account
from core.utils.account_fingerprint import fingerprint_account_number


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RBC_CSV = (
    '"Activity Export as of January 15, 2024"\n'
    '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
    '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
    '"Settlement Date","Account","Value","Currency","Description"\n'
    '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5","2800.00",'
    '"January 17, 2024","90000001","13990.05","USD","Buy GOOGL"\n'
)


class ImportCsvCommandTest(TestCase):
    """Tests for the import_csv management command."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.inbox_dir = os.path.join(self.temp_dir, 'inbox')
        self.processed_dir = os.path.join(self.temp_dir, 'processed')
        os.makedirs(self.inbox_dir)

        # Fake account 90000001 (ACCOUNT_A) with a stored fingerprint — the
        # importer resolves rows by fingerprint, never by the raw number.
        self.account = Account.objects.create(
            account_id="ACCOUNT_A", name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001",
        )

        # RBC file
        self.rbc_file = os.path.join(self.inbox_dir, 'rbc_test.csv')
        with open(self.rbc_file, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(RBC_CSV)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, **kwargs) -> None:
        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv', **kwargs)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_scans_inbox_and_imports(self) -> None:
        """import_csv scans inbox and imports the RBC file."""
        self._run()

        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(FileImportLog.objects.count(), 1)
        log = FileImportLog.objects.get()
        self.assertEqual(log.status, 'success')
        self.assertEqual(log.records_imported, 1)

    def test_files_moved_to_processed(self) -> None:
        """Processed CSV files are moved from inbox/ to processed/."""
        self._run()

        # inbox should be empty
        self.assertEqual(os.listdir(self.inbox_dir), [])

        # processed should have the file (same name, no timestamp suffix)
        self.assertIn('rbc_test.csv', os.listdir(self.processed_dir))

    def test_dry_run_no_db_writes_no_file_moves(self) -> None:
        """--dry-run parses files but makes no DB writes and moves no files."""
        self._run(dry_run=True)

        inbox_files = os.listdir(self.inbox_dir)
        self.assertEqual(len(inbox_files), 1)            # file still in inbox
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(FileImportLog.objects.count(), 0)

    def test_file_import_log_fields(self) -> None:
        """FileImportLog record has all required fields populated."""
        self._run()

        logs = FileImportLog.objects.all()
        self.assertEqual(logs.count(), 1)
        log = logs.get()
        self.assertEqual(log.broker, 'RBC')
        self.assertIsNotNone(log.imported_at)
        self.assertGreaterEqual(log.records_imported, 0)
        self.assertGreaterEqual(log.records_skipped, 0)
        self.assertIn(log.status, ('success', 'partial', 'error'))

    def test_error_handling_bad_file(self) -> None:
        """A file that cannot be decoded does not stop processing of other files."""
        bad_file = os.path.join(self.inbox_dir, 'bad_test.csv')
        with open(bad_file, 'wb') as f:
            f.write(b'\xff\xfe\x00bad data\x00')  # invalid UTF-8

        self._run()

        # The good file should still be imported
        self.assertEqual(Transaction.objects.count(), 1)

        # Bad file moved to processed/errors/
        errors_dir = os.path.join(self.processed_dir, 'errors')
        self.assertTrue(os.path.isdir(errors_dir))
        self.assertEqual(os.listdir(errors_dir), ['bad_test.csv'])

    def test_exchange_rate_stored_on_rbc_transactions(self) -> None:
        """RBC transactions have exchange_rate_cad and correct amount_cad."""
        self._run()

        rbc_tx = Transaction.objects.filter(statement__source='RBC').first()
        self.assertIsNotNone(rbc_tx)
        self.assertEqual(rbc_tx.exchange_rate_cad, Decimal('1.350000'))
        # amount_cad = 13990.05 * 1.35 = 18886.5675 → 18886.57
        expected_cad = (rbc_tx.amount * rbc_tx.exchange_rate_cad).quantize(Decimal('0.01'))
        self.assertEqual(rbc_tx.amount_cad, expected_cad)

    def test_duplicate_file_skipped_by_hash(self) -> None:
        """Importing the same file content twice skips the second import."""
        # First import
        self._run()
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(FileImportLog.objects.filter(status='success').count(), 1)

        # Write a duplicate file with identical content (original was moved to processed/)
        duplicate_file = os.path.join(self.inbox_dir, 'rbc_duplicate.csv')
        with open(duplicate_file, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(RBC_CSV)

        # Second import
        self._run()

        # Transaction count should not change
        self.assertEqual(Transaction.objects.count(), 1)

        # Only 1 success log (the duplicate was skipped)
        self.assertEqual(FileImportLog.objects.filter(status='success').count(), 1)

        # The duplicate file should not have a log
        self.assertFalse(FileImportLog.objects.filter(filename='rbc_duplicate.csv').exists())
