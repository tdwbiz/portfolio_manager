"""Tests for the scrub_statement_file_names command (phase 2 step 11, plan step 6).

Covers the keyed-hash fallback: when the source PDF is missing, ``hash8`` is
the first 8 hex chars of HMAC-SHA256(``ACCOUNT_FINGERPRINT_KEY``, stored file
name verbatim) instead of a content hash. Only the fake account
``90000001`` / ``ACCOUNT_A``; the key comes from ``settings_test``.
"""

import os
import tempfile
from datetime import date
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import Account, RbcAnnual, RbcMonthly
from core.utils.account_fingerprint import fingerprint_account_number, keyed_hash8
from core.utils.safe_names import hash8_of, is_safe_file_name

FAKE_NUMBER = "90000001"
FAKE_NAME = "ACCOUNT_A"

MONTHLY_CONTENT = b"physical monthly statement content A"
ANNUAL_CONTENT = b"physical annual report content B"

OLD_MONTHLY_NAME = f"2025-09 Monthly {FAKE_NUMBER}.pdf"
OLD_ANNUAL_NAME = f"2024 Annual Report {FAKE_NUMBER}.pdf"
MISSING_MONTHLY_NAME = f"2023-05 Monthly {FAKE_NUMBER}.pdf"
PAIR_NAME = f"2024-03 Monthly {FAKE_NUMBER}.pdf"


class ScrubStatementFileNamesTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC",
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0001",
        )

    def _make_env(self, tmpdir, pair=True, empty_name=False):
        """Create the physical files and DB rows; return (processed_dir, out)."""
        processed_dir = os.path.join(tmpdir, 'processed')
        errors_dir = os.path.join(processed_dir, 'errors')
        os.makedirs(errors_dir)
        with open(os.path.join(processed_dir, OLD_MONTHLY_NAME), 'wb') as f:
            f.write(MONTHLY_CONTENT)
        with open(os.path.join(errors_dir, OLD_ANNUAL_NAME), 'wb') as f:
            f.write(ANNUAL_CONTENT)

        RbcMonthly.objects.create(
            account=self.account, file_name=OLD_MONTHLY_NAME,
            statement_date=date(2025, 9, 30), currency='CAD',
        )
        RbcMonthly.objects.create(
            account=self.account, file_name=MISSING_MONTHLY_NAME,
            statement_date=date(2023, 5, 31), currency='CAD',
        )
        if pair:
            # CAD and USD sections of one PDF: same stored name, account and
            # date — both must end up with the identical safe name.
            for currency in ('CAD', 'USD'):
                RbcMonthly.objects.create(
                    account=self.account, file_name=PAIR_NAME,
                    statement_date=date(2024, 3, 31), currency=currency,
                )
        if empty_name:
            # file_name is NOT NULL on the model; '' exercises the same
            # empty-input path as a hypothetical NULL.
            RbcMonthly.objects.create(
                account=self.account, file_name='',
                statement_date=date(2022, 6, 30), currency='CAD',
            )
        RbcAnnual.objects.create(
            account=self.account, file_name=OLD_ANNUAL_NAME, report_year=2024,
            report_period_start=date(2024, 1, 1), report_period_end=date(2024, 12, 31),
            currency='CAD',
        )
        return processed_dir

    def _run(self, *args):
        out = StringIO()
        call_command('scrub_statement_file_names', *args, stdout=out)
        return out.getvalue()

    def test_missing_source_row_rewritten_with_keyed_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=False)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                out = self._run()

            expected = (
                f'2023-05-31_monthly_{FAKE_NAME}_'
                f'{keyed_hash8(MISSING_MONTHLY_NAME)}.pdf'
            )
            row = RbcMonthly.objects.get(statement_date=date(2023, 5, 31))
            self.assertEqual(row.file_name, expected)
            self.assertTrue(is_safe_file_name(row.file_name))
            self.assertIn('RbcMonthly: from-file=1 keyed=1 already-safe=0', out)

    def test_found_source_still_uses_content_hash_and_files_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=False)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                out = self._run()

            safe_monthly = (
                f'2025-09-30_monthly_{FAKE_NAME}_{hash8_of(MONTHLY_CONTENT)}.pdf'
            )
            safe_annual = f'2024_annual_{FAKE_NAME}_{hash8_of(ANNUAL_CONTENT)}.pdf'
            self.assertTrue(is_safe_file_name(safe_monthly))
            self.assertTrue(is_safe_file_name(safe_annual))
            self.assertEqual(
                RbcMonthly.objects.get(statement_date=date(2025, 9, 30)).file_name,
                safe_monthly,
            )
            self.assertEqual(RbcAnnual.objects.get().file_name, safe_annual)

            # DB only: the physical files were not renamed.
            self.assertTrue(os.path.exists(os.path.join(processed_dir, OLD_MONTHLY_NAME)))
            self.assertTrue(
                os.path.exists(os.path.join(processed_dir, 'errors', OLD_ANNUAL_NAME))
            )
            # The row without a source file is keyed; the found rows are content-hashed.
            self.assertIn('RbcMonthly: from-file=1 keyed=1 already-safe=0', out)
            self.assertIn('RbcAnnual: from-file=1 keyed=0 already-safe=0', out)

    def test_same_old_name_account_and_date_get_identical_new_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=True)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                self._run()

            rows = RbcMonthly.objects.filter(statement_date=date(2024, 3, 31))
            names = set(rows.values_list('file_name', flat=True))
            self.assertEqual(rows.count(), 2)
            self.assertEqual(len(names), 1)
            only_name = next(iter(names))
            expected = f'2024-03-31_monthly_{FAKE_NAME}_{keyed_hash8(PAIR_NAME)}.pdf'
            self.assertEqual(only_name, expected)
            self.assertTrue(is_safe_file_name(only_name))

    def test_every_produced_name_passes_is_safe_file_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=True, empty_name=True)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                self._run()

            for row in RbcMonthly.objects.all():
                self.assertTrue(is_safe_file_name(row.file_name), row.file_name)
            for row in RbcAnnual.objects.all():
                self.assertTrue(is_safe_file_name(row.file_name), row.file_name)

            # An empty file_name is keyed-hashed like any other name (empty
            # input) and ends up safe — not skipped.
            null_row = RbcMonthly.objects.get(statement_date=date(2022, 6, 30))
            self.assertEqual(
                null_row.file_name,
                f'2022-06-30_monthly_{FAKE_NAME}_{keyed_hash8("")}.pdf',
            )

    def test_dry_run_prints_counts_only_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=True)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                out = self._run('--dry-run')

            # Counts only — no file names, no numbers, no key.
            self.assertIn('[dry-run] RbcMonthly: from-file=1 keyed=3 already-safe=0', out)
            self.assertIn('[dry-run] RbcAnnual: from-file=1 keyed=0 already-safe=0', out)
            for secret in (
                FAKE_NUMBER, OLD_MONTHLY_NAME, OLD_ANNUAL_NAME,
                MISSING_MONTHLY_NAME, PAIR_NAME, settings.ACCOUNT_FINGERPRINT_KEY,
            ):
                self.assertNotIn(secret, out)

            # Nothing was written.
            self.assertEqual(
                RbcMonthly.objects.get(file_name=OLD_MONTHLY_NAME).file_name,
                OLD_MONTHLY_NAME,
            )
            self.assertEqual(RbcAnnual.objects.get().file_name, OLD_ANNUAL_NAME)

    def test_missing_or_short_key_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=False)
            for bad_key in ('', 'abcdefghij'):  # empty and 10-char key
                with override_settings(
                    IMPORT_PROCESSED_DIR=processed_dir,
                    ACCOUNT_FINGERPRINT_KEY=bad_key,
                ):
                    for args in (('--dry-run',), ()):
                        with self.assertRaises(CommandError):
                            self._run(*args)

            # No row changed after any attempt.
            self.assertEqual(
                RbcMonthly.objects.get(file_name=OLD_MONTHLY_NAME).file_name,
                OLD_MONTHLY_NAME,
            )
            self.assertEqual(
                RbcMonthly.objects.get(file_name=MISSING_MONTHLY_NAME).file_name,
                MISSING_MONTHLY_NAME,
            )
            self.assertEqual(RbcAnnual.objects.get().file_name, OLD_ANNUAL_NAME)

    def test_second_run_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir, pair=True)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                self._run()
                before = {
                    rm.pk: rm.file_name for rm in RbcMonthly.objects.all()
                } | {ra.pk: ra.file_name for ra in RbcAnnual.objects.all()}

                out = self._run('--dry-run')
                after = {
                    rm.pk: rm.file_name for rm in RbcMonthly.objects.all()
                } | {ra.pk: ra.file_name for ra in RbcAnnual.objects.all()}

            self.assertEqual(before, after)
            # Keyed names are already safe: a second run rewrites nothing.
            self.assertIn('[dry-run] RbcMonthly: from-file=0 keyed=0 already-safe=4', out)
            self.assertIn('[dry-run] RbcAnnual: from-file=0 keyed=0 already-safe=1', out)
