"""Tests for the import_csv management command (Activity CSVs).

Ported from the deleted auto_import command tests: RBC/inbox/FileImportLog/
duplicate-hash cases only (IB and --broker cases dropped — import_csv is
RBC-only, see docs/plans/import-paths.md decision Q1).
"""

import io
import os
import shutil
import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import FileImportLog, Transaction, Account, TransactionType
from core.utils.account_fingerprint import fingerprint_account_number
from imports.parsers import parse_rbc_csv


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

RBC_CSV_HEADER = (
    '"Activity Export as of January 15, 2024"\n'
    '"Exchange Rate: 1 USD = 1.3500 CAD"\n'
    '"Date","Activity","Symbol","Symbol Description","Quantity","Price",'
    '"Settlement Date","Account","Value","Currency","Description"\n'
)

RBC_ROW_A = (
    '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5","2800.00",'
    '"January 17, 2024","90000001","13990.05","USD","Buy GOOGL"\n'
)

RBC_ROW_B = (
    '"January 16, 2024","Sell","AAPL","Apple Inc.","2","200.00",'
    '"January 18, 2024","90000001","395.00","USD","Sell AAPL"\n'
)

RBC_CSV = RBC_CSV_HEADER + RBC_ROW_A


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
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001")

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

    def test_reimported_identical_file_reports_all_rows_duplicate(self) -> None:
        """Re-importing identical content is NOT skipped at file level
        (review fix 1): the row tx_hash decides duplicates, so every row is
        reported as a duplicate and a FileImportLog is still written."""
        # First import
        self._run()
        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(FileImportLog.objects.filter(status='success').count(), 1)

        # Write a file with identical content (original was moved to processed/)
        duplicate_file = os.path.join(self.inbox_dir, 'rbc_duplicate.csv')
        with open(duplicate_file, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(RBC_CSV)

        # Second import — the file is processed, not skipped by content hash
        stream = io.StringIO()
        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv', stdout=stream)
        out = stream.getvalue()

        # No new transactions — the row was matched by its tx_hash
        self.assertEqual(Transaction.objects.count(), 1)

        # A log for the re-imported file: 0 imported, 1 duplicate (partial)
        dup_log = FileImportLog.objects.get(filename='rbc_duplicate.csv')
        self.assertEqual(dup_log.records_imported, 0)
        self.assertEqual(dup_log.records_skipped, 1)
        self.assertEqual(dup_log.status, 'partial')

        # All rows reported as duplicates; file moved to processed/
        self.assertIn('Rows: 1 data = 0 imported + 1 duplicates'
                      ' + 0 practice + 0 rejected', out)
        self.assertIn('rbc_duplicate.csv', os.listdir(self.processed_dir))

    def test_reimported_row_found_as_duplicate_after_wrapping(self) -> None:
        """A row re-imported from a different file is found as a duplicate.

        The stored tx_hash is the wrapped (HMAC) value, so the second pass
        must wrap its computed hash the same way to match the first import.
        """
        # First import: only row A.
        self._run()
        self.assertEqual(Transaction.objects.count(), 1)
        stored_hash = Transaction.objects.get().tx_hash
        self.assertEqual(len(stored_hash), 64)

        # Second file: different content (so the file-content-hash shortcut
        # does not apply) containing row A again plus a new row B.
        second_file = os.path.join(self.inbox_dir, 'rbc_second.csv')
        with open(second_file, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(RBC_CSV_HEADER + RBC_ROW_A + RBC_ROW_B)

        self._run()

        # Row A was matched by its wrapped hash (no duplicate row), row B new.
        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(
            Transaction.objects.filter(tx_hash=stored_hash).count(), 1
        )
        second_log = FileImportLog.objects.get(filename='rbc_second.csv')
        self.assertEqual(second_log.records_imported, 1)
        self.assertEqual(second_log.records_skipped, 1)

    def test_transaction_notes_scrubbed_of_account_number(self) -> None:
        """Account numbers in the CSV description are scrubbed before store."""
        os.remove(self.rbc_file)  # only the notes file should be imported
        notes_file = os.path.join(self.inbox_dir, 'rbc_notes.csv')
        with open(notes_file, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(
                RBC_CSV_HEADER
                + '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5",'
                  '"2800.00","January 17, 2024","90000001","13990.05",'
                  '"USD","Buy GOOGL on 90000001"\n'
            )

        self._run()

        tx = Transaction.objects.get()
        self.assertEqual(tx.notes, 'Buy GOOGL on ACCOUNT_A')


# ---------------------------------------------------------------------------
# Step 7: both layouts, duplicates, seq, unknown types, near-duplicates,
# row accounting, atomic failure on mismatch, no account numbers in output.
# ---------------------------------------------------------------------------

class ImportCsvLayoutAndReportTests(TestCase):
    """Synthetic-CSV tests for the import_csv Activity path (plan step 7)."""

    OLD_HEADER = (
        '"Date","Activity","Symbol","Quantity","Price",'
        '"Settlement Date","Account","Value","Currency"\n'
    )

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.inbox_dir = os.path.join(self.temp_dir, 'inbox')
        self.processed_dir = os.path.join(self.temp_dir, 'processed')
        os.makedirs(self.inbox_dir)

        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, name: str, text: str) -> str:
        path = os.path.join(self.inbox_dir, name)
        with open(path, 'w', newline='', encoding='utf-8-sig') as f:
            f.write(text)
        return path

    def _run(self, **kwargs) -> tuple[str, str]:
        out, err = io.StringIO(), io.StringIO()
        with override_settings(
            IMPORT_INBOX_DIR=self.inbox_dir,
            IMPORT_PROCESSED_DIR=self.processed_dir,
        ):
            call_command('import_csv', stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    def test_old_layout_imported_with_dates(self) -> None:
        """Old 9-column file with 31-Dec-18 dates: Date + Settlement Date
        stored, row accounting printed."""
        self._write('old.csv',
                    self.OLD_HEADER
                    + '"31-Dec-18","Buy","VFV","10","50.00",'
                      '"02-Jan-19","90000001","-500.00","CAD"\n')

        out, _ = self._run()

        tx = Transaction.objects.get()
        self.assertEqual(tx.date, date(2018, 12, 31))
        self.assertEqual(tx.settlement_date, date(2019, 1, 2))
        self.assertIn('Rows: 1 data = 1 imported + 0 duplicates'
                      ' + 0 practice + 0 rejected', out)

    def test_overlapping_files_second_fully_duplicate(self) -> None:
        """Two overlapping exports of the same rows dedup by hash."""
        self._write('first.csv', RBC_CSV_HEADER + RBC_ROW_A + RBC_ROW_B)
        self._run()
        self.assertEqual(Transaction.objects.count(), 2)

        # Second export: same rows, slightly different amounts (overlap).
        self._write('second.csv',
                    RBC_CSV_HEADER
                    + '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5",'
                      '"2800.00","January 17, 2024","90000001","13990.04",'
                      '"USD","Buy GOOGL"\n'
                    + '"January 16, 2024","Sell","AAPL","Apple Inc.","2",'
                      '"200.00","January 18, 2024","90000001","395.01",'
                      '"USD","Sell AAPL"\n')
        out, _ = self._run()

        self.assertEqual(Transaction.objects.count(), 2)
        log = FileImportLog.objects.get(filename='second.csv')
        self.assertEqual(log.records_imported, 0)
        self.assertEqual(log.records_skipped, 2)
        self.assertIn('Rows: 2 data = 0 imported + 2 duplicates'
                      ' + 0 practice + 0 rejected', out)

    def test_identical_rows_in_one_file_both_imported(self) -> None:
        """Two identical rows in one file get seq 1 and 2 — both imported,
        different hashes."""
        self._write('twice.csv', RBC_CSV_HEADER + RBC_ROW_A + RBC_ROW_A)

        out, _ = self._run()

        self.assertEqual(Transaction.objects.count(), 2)
        hashes = set(Transaction.objects.values_list('tx_hash', flat=True))
        self.assertEqual(len(hashes), 2)
        self.assertIn('Rows: 2 data = 2 imported + 0 duplicates'
                      ' + 0 practice + 0 rejected', out)

    def test_unknown_activity_type_rejected_no_transactiontype(self) -> None:
        """Unknown activity type: row rejected, no new TransactionType,
        rejection reason in the output."""
        before = TransactionType.objects.count()
        self._write('mixed.csv',
                    RBC_CSV_HEADER + RBC_ROW_A
                    + '"January 15, 2024","Div Reinvest","RBF2010",'
                      '"RBF 2010","100","10.00","January 17, 2024",'
                      '"90000001","-1000.00","USD","REI"\n')

        out, _ = self._run()

        self.assertEqual(Transaction.objects.count(), 1)
        self.assertEqual(TransactionType.objects.count(), before)
        self.assertIn('rejected (1): Unknown activity type: Div Reinvest', out)
        self.assertIn('Rows: 2 data = 1 imported + 0 duplicates'
                      ' + 0 practice + 1 rejected', out)

    def test_near_duplicates_reported_in_dry_run(self) -> None:
        """Same account/date/symbol/value in two files, different quantity:
        reported as a near-duplicate in --dry-run; nothing written."""
        self._write('file_a.csv',
                    RBC_CSV_HEADER
                    + '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","5",'
                      '"2800.00","January 17, 2024","90000001","13990.05",'
                      '"USD","Buy GOOGL"\n')
        self._write('file_b.csv',
                    RBC_CSV_HEADER
                    + '"January 15, 2024","Buy","GOOGL","Alphabet Inc.","6",'
                      '"2800.00","January 17, 2024","90000001","13990.05",'
                      '"USD","Buy GOOGL"\n')

        out, _ = self._run(dry_run=True)

        self.assertIn('Near-duplicates across files (1):', out)
        self.assertIn('file_a.csv row 4 vs file_b.csv row 4', out)
        self.assertIn('quantity: 5 vs 6', out)
        # Dry run wrote nothing.
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(FileImportLog.objects.count(), 0)
        self.assertEqual(len(os.listdir(self.inbox_dir)), 2)

    def test_forced_row_count_mismatch_fails_file_atomically(self) -> None:
        """A row-count mismatch fails the whole file atomically (decision 4):
        nothing stored for it, FileImportLog status error with the counts,
        and the run continues with the next file."""
        self._write('bad.csv', RBC_CSV_HEADER + RBC_ROW_A)
        self._write('good.csv', RBC_CSV_HEADER + RBC_ROW_B)

        def fake_parse(path, rejections=None):
            trades, rate, accounting = parse_rbc_csv(path, rejections)
            if os.path.basename(path) == 'bad.csv':
                accounting['data_rows'] += 3   # force the mismatch
            return trades, rate, accounting

        with patch('imports.auto_import.parse_rbc_csv', side_effect=fake_parse):
            out, err = self._run()

        log = FileImportLog.objects.get(filename='bad.csv')
        self.assertEqual(log.status, 'error')
        self.assertIn('Row count mismatch in bad.csv', log.error_message)
        self.assertIn('data rows=4', log.error_message)
        # Nothing stored for the bad file; the good file was still imported.
        self.assertFalse(Transaction.objects.filter(symbol='GOOGL').exists())
        self.assertTrue(Transaction.objects.filter(symbol='AAPL').exists())
        errors_dir = os.path.join(self.processed_dir, 'errors')
        self.assertIn('bad.csv', os.listdir(errors_dir))

    def test_dry_run_counts_in_run_duplicates(self) -> None:
        """In --dry-run a tx_hash already seen in an earlier file of the
        same run is a duplicate, not only hashes in the DB (review fix 3)."""
        self._write('file_a.csv', RBC_CSV_HEADER + RBC_ROW_A)
        self._write('file_b.csv', RBC_CSV_HEADER + RBC_ROW_A + RBC_ROW_B)

        out, _ = self._run(dry_run=True)

        # file_a (first): row A would be imported. file_b (second): row A is
        # a duplicate of what the same run already saw, row B would import.
        self.assertIn('Rows: 1 data = 1 imported + 0 duplicates'
                      ' + 0 practice + 0 rejected', out)
        self.assertIn('Rows: 2 data = 1 imported + 1 duplicates'
                      ' + 0 practice + 0 rejected', out)
        # Nothing was written, so a second dry run sees the same picture.
        self.assertEqual(Transaction.objects.count(), 0)
        out2, _ = self._run(dry_run=True)
        self.assertIn('Rows: 2 data = 1 imported + 1 duplicates'
                      ' + 0 practice + 0 rejected', out2)

    def test_dry_run_row_count_mismatch_fails_file(self) -> None:
        """In --dry-run the same row-accounting check as the real run runs;
        a mismatch is reported as a failed file and the run continues
        (review fix 3)."""
        self._write('bad.csv', RBC_CSV_HEADER + RBC_ROW_A)
        self._write('good.csv', RBC_CSV_HEADER + RBC_ROW_B)

        def fake_parse(path, rejections=None):
            trades, rate, accounting = parse_rbc_csv(path, rejections)
            if os.path.basename(path) == 'bad.csv':
                accounting['data_rows'] += 3   # force the mismatch
            return trades, rate, accounting

        with patch('imports.auto_import.parse_rbc_csv', side_effect=fake_parse):
            out, err = self._run(dry_run=True)

        self.assertIn('FILE FAILED — nothing stored for bad.csv', err)
        self.assertIn('Row count mismatch in bad.csv', err)
        # The run continued with the next file.
        self.assertIn('Rows: 1 data = 1 imported + 0 duplicates'
                      ' + 0 practice + 0 rejected', out)
        # Dry run wrote nothing and moved no files.
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(FileImportLog.objects.count(), 0)
        self.assertEqual(sorted(os.listdir(self.inbox_dir)),
                         ['bad.csv', 'good.csv'])

    def test_no_account_number_in_command_output(self) -> None:
        """The Account column value never appears in the command output."""
        self._write('acct.csv', RBC_CSV_HEADER + RBC_ROW_A)

        # Dry-run output (breakdown + rejections) first — the file stays in
        # the inbox, so the dry run actually parses it.
        out2, err2 = self._run(dry_run=True)
        self.assertNotIn('90000001', out2)
        self.assertNotIn('90000001', err2)

        # Then the real import.
        out, err = self._run()
        self.assertNotIn('90000001', out)
        self.assertNotIn('90000001', err)


# ---------------------------------------------------------------------------
# Review fix 2: any exception (other than the accounting mismatch) fails the
# file — scrubbed reason, FileImportLog error row, no tracking-file rows,
# moved to errors/, run continues with the next file.
# ---------------------------------------------------------------------------

class ReviewFix2ExceptionFailsFileTests(TestCase):
    """A mid-import exception is treated like a failed file."""

    def setUp(self) -> None:
        # auto_import writes its tracking CSVs relative to the CWD — run
        # from a scratch directory so the real files are never touched.
        self._old_cwd = os.getcwd()
        self.temp_dir = tempfile.mkdtemp()
        os.chdir(self.temp_dir)
        self.inbox_dir = os.path.join(self.temp_dir, 'inbox')
        self.processed_dir = os.path.join(self.temp_dir, 'processed')
        os.makedirs(self.inbox_dir)

        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001")

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write(self, name: str, text: str) -> None:
        with open(os.path.join(self.inbox_dir, name), 'w',
                  newline='', encoding='utf-8-sig') as f:
            f.write(text)

    def test_exception_fails_file_and_run_continues(self) -> None:
        """Transaction creation raising fails the file (scrubbed reason,
        error log, no tracking rows, moved to errors/) and the next file is
        still imported."""
        self._write('bad.csv', RBC_CSV_HEADER + RBC_ROW_A)
        self._write('good.csv', RBC_CSV_HEADER + RBC_ROW_B)

        real_create = Transaction.objects.create

        def fake_create(*args, **kwargs):
            if kwargs.get('symbol') == 'GOOGL':
                raise RuntimeError('boom for 90000001')
            return real_create(*args, **kwargs)

        out, err = io.StringIO(), io.StringIO()
        with patch.object(Transaction.objects, 'create', side_effect=fake_create), \
             override_settings(
                 IMPORT_INBOX_DIR=self.inbox_dir,
                 IMPORT_PROCESSED_DIR=self.processed_dir):
            call_command('import_csv', stdout=out, stderr=err)

        # The bad file failed with a scrubbed reason — the account number in
        # the exception became the account name.
        log = FileImportLog.objects.get(filename='bad.csv')
        self.assertEqual(log.status, 'error')
        self.assertIn('boom', log.error_message)
        self.assertIn('ACCOUNT_A', log.error_message)
        self.assertNotIn('90000001', log.error_message)
        self.assertEqual((log.records_imported, log.records_skipped), (0, 0))

        # Nothing stored for the bad file; the run continued and imported
        # the good one.
        self.assertFalse(Transaction.objects.filter(symbol='GOOGL').exists())
        self.assertTrue(Transaction.objects.filter(symbol='AAPL').exists())

        # File moves: bad → errors/, good → processed/.
        errors_dir = os.path.join(self.processed_dir, 'errors')
        self.assertEqual(os.listdir(errors_dir), ['bad.csv'])
        self.assertIn('good.csv', os.listdir(self.processed_dir))

        # No rows of the failed file in the CSV_Activity_Imported tracking
        # file (the good file's row is there).
        tracking = os.path.join(
            self.temp_dir, 'docs', 'reconciliation', 'CSV_Activity_Imported.csv')
        with open(tracking, newline='', encoding='utf-8') as f:
            lines = [line for line in f.read().splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)   # header + the good file's row
        self.assertNotIn('bad.csv', lines[1])
        self.assertIn('AAPL', lines[1])

        # The command reported the failure and the final counts.
        self.assertIn('FILE FAILED — nothing stored for bad.csv',
                      err.getvalue())
        self.assertIn('Imported 1/2 files (1 errors)', out.getvalue())
