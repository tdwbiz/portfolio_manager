"""Tests for the auto_import management command."""

import os
import shutil
import tempfile
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import BrokerStatement, FileImportLog, Transaction


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

IB_CSV = (
    "Transaction History,Header,Date,Account,Description,Type,Symbol,"
    "Quantity,Price,Currency,Gross Amount,Commission,Net Amount\n"
    "Transaction History,Data,2024-01-15,U1234567,AAPL stock,Buy,AAPL,"
    "10,150.25,USD,1502.50,1.00,1503.50\n"
)

RBC_CSV = (
    '"Activity Export as of January 15, 2024"\n'
    '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
    '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
    '"Settlement Date","Account","Value","Currency","Description"\n'
    '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5","2800.00",'
    '"January 17, 2024","123456","13990.05","USD","Buy GOOGL"\n'
)


class AutoImportCommandTest(TestCase):
    """Tests for the auto_import management command."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.inbox_dir = os.path.join(self.temp_dir, 'inbox')
        self.processed_dir = os.path.join(self.temp_dir, 'processed')
        os.makedirs(self.inbox_dir)

        # IB file
        self.ib_file = os.path.join(self.inbox_dir, 'ib_test.csv')
        with open(self.ib_file, 'w', newline='', encoding='utf-8') as f:
            f.write(IB_CSV)

        # RBC file
        self.rbc_file = os.path.join(self.inbox_dir, 'rbc_test.csv')
        with open(self.rbc_file, 'w', newline='', encoding='utf-8') as f:
            f.write(RBC_CSV)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, **kwargs) -> None:
        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('auto_import', **kwargs)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_scans_inbox_and_imports(self) -> None:
        """auto_import scans inbox, imports both IB and RBC files."""
        self._run()

        self.assertEqual(Transaction.objects.count(), 2)  # 1 IB + 1 RBC
        self.assertEqual(FileImportLog.objects.count(), 2)
        for log in FileImportLog.objects.all():
            self.assertEqual(log.status, 'success')
            self.assertEqual(log.records_imported, 1)

    def test_files_moved_to_processed(self) -> None:
        """Processed CSV files are moved from inbox/ to processed/ with timestamp."""
        self._run()

        # inbox should be empty
        self.assertEqual(os.listdir(self.inbox_dir), [])

        # processed should have 2 files with timestamp suffixes
        processed_files = os.listdir(self.processed_dir)
        self.assertEqual(len(processed_files), 2)
        for fname in processed_files:
            self.assertIn('_', fname)  # timestamp separator present

    def test_dry_run_no_db_writes_no_file_moves(self) -> None:
        """--dry-run parses files but makes no DB writes and moves no files."""
        self._run(dry_run=True)

        inbox_files = os.listdir(self.inbox_dir)
        self.assertEqual(len(inbox_files), 2)            # files still in inbox
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(FileImportLog.objects.count(), 0)

    def test_broker_auto_detection(self) -> None:
        """Broker is detected from file content, not filename."""
        self._run()

        ib_log = FileImportLog.objects.get(filename='ib_test.csv')
        self.assertEqual(ib_log.broker, 'IB')

        rbc_log = FileImportLog.objects.get(filename='rbc_test.csv')
        self.assertEqual(rbc_log.broker, 'RBC')

    def test_broker_override(self) -> None:
        """--broker flag overrides auto-detection for all files."""
        self._run(broker='IB')

        for log in FileImportLog.objects.all():
            self.assertEqual(log.broker, 'IB')

    def test_file_import_log_fields(self) -> None:
        """FileImportLog records have all required fields populated."""
        self._run()

        logs = FileImportLog.objects.all()
        self.assertEqual(logs.count(), 2)
        for log in logs:
            self.assertIn(log.broker, ('IB', 'RBC'))
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

        # The two good files should still be imported
        self.assertEqual(Transaction.objects.count(), 2)

        # Error log created for the bad file
        error_logs = FileImportLog.objects.filter(status='error')
        self.assertEqual(error_logs.count(), 1)
        self.assertEqual(error_logs.first().filename, 'bad_test.csv')

        # Bad file moved to processed/errors/
        errors_dir = os.path.join(self.processed_dir, 'errors')
        self.assertTrue(os.path.isdir(errors_dir))
        self.assertEqual(len(os.listdir(errors_dir)), 1)

    def test_exchange_rate_stored_on_rbc_transactions(self) -> None:
        """RBC transactions have exchange_rate_cad and correct amount_cad."""
        self._run()

        rbc_tx = Transaction.objects.filter(statement__source='RBC').first()
        self.assertIsNotNone(rbc_tx)
        self.assertEqual(rbc_tx.exchange_rate_cad, Decimal('1.350000'))
        # amount_cad = 13990.05 * 1.35 = 18886.5675 → 18886.57
        expected_cad = (rbc_tx.amount * rbc_tx.exchange_rate_cad).quantize(Decimal('0.01'))
        self.assertEqual(rbc_tx.amount_cad, expected_cad)

    def test_ib_transactions_have_default_exchange_rate(self) -> None:
        """IB transactions default to exchange_rate_cad=1.0 and amount_cad=amount."""
        self._run()

        ib_tx = Transaction.objects.filter(statement__source='IB').first()
        self.assertIsNotNone(ib_tx)
        self.assertEqual(ib_tx.exchange_rate_cad, Decimal('1.000000'))
        self.assertEqual(ib_tx.amount_cad, ib_tx.amount)
