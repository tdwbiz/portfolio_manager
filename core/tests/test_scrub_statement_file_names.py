"""Tests for the scrub_statement_file_names command (phase 2 step 11, plan step 6)."""

import os
import tempfile
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from core.models import Account, RbcAnnual, RbcMonthly
from core.utils.account_fingerprint import fingerprint_account_number
from core.utils.safe_names import hash8_of, is_safe_file_name

FAKE_NUMBER = "90000001"
FAKE_NAME = "ACCOUNT_A"

MONTHLY_CONTENT = b"physical monthly statement content A"
ANNUAL_CONTENT = b"physical annual report content B"

OLD_MONTHLY_NAME = f"2025-09 Monthly {FAKE_NUMBER}.pdf"
OLD_ANNUAL_NAME = f"2024 Annual Report {FAKE_NUMBER}.pdf"
MISSING_MONTHLY_NAME = f"2023-05 Monthly {FAKE_NUMBER}.pdf"


class ScrubStatementFileNamesTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name=FAKE_NAME, broker="RBC",
            fingerprint=fingerprint_account_number(FAKE_NUMBER), last_4="0001",
        )

    def _make_env(self, tmpdir):
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

    def test_dry_run_prints_counts_only_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                out = self._run('--dry-run')

            # Counts only — no file names, no numbers.
            self.assertIn('RbcMonthly: to-rename=1 already-safe=0 missing-source=1', out)
            self.assertIn('RbcAnnual: to-rename=1 already-safe=0 missing-source=0', out)
            for secret in (FAKE_NUMBER, OLD_MONTHLY_NAME, OLD_ANNUAL_NAME, MISSING_MONTHLY_NAME):
                self.assertNotIn(secret, out)

            # Nothing was written.
            self.assertEqual(RbcMonthly.objects.get(file_name=OLD_MONTHLY_NAME).file_name, OLD_MONTHLY_NAME)
            self.assertEqual(RbcAnnual.objects.get().file_name, OLD_ANNUAL_NAME)

    def test_rows_are_rewritten_to_safe_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir)
            with override_settings(IMPORT_PROCESSED_DIR=processed_dir):
                out = self._run()

            h8_monthly = hash8_of(MONTHLY_CONTENT)
            h8_annual = hash8_of(ANNUAL_CONTENT)
            safe_monthly = f'2025-09-30_monthly_{FAKE_NAME}_{h8_monthly}.pdf'
            safe_annual = f'2024_annual_{FAKE_NAME}_{h8_annual}.pdf'

            self.assertTrue(is_safe_file_name(safe_monthly))
            self.assertTrue(is_safe_file_name(safe_annual))
            self.assertEqual(RbcMonthly.objects.filter(statement_date=date(2025, 9, 30)).get().file_name, safe_monthly)
            self.assertEqual(RbcAnnual.objects.get().file_name, safe_annual)

            # Missing source: skipped and counted, name untouched.
            self.assertEqual(
                RbcMonthly.objects.get(statement_date=date(2023, 5, 31)).file_name,
                MISSING_MONTHLY_NAME,
            )
            self.assertIn('RbcMonthly: to-rename=1 already-safe=0 missing-source=1', out)

            # DB only: the physical files were not renamed.
            self.assertTrue(os.path.exists(os.path.join(processed_dir, OLD_MONTHLY_NAME)))
            self.assertTrue(
                os.path.exists(os.path.join(processed_dir, 'errors', OLD_ANNUAL_NAME))
            )

    def test_second_run_is_a_noop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            processed_dir = self._make_env(tmpdir)
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
            self.assertIn('RbcMonthly: to-rename=0 already-safe=1 missing-source=1', out)
            self.assertIn('RbcAnnual: to-rename=0 already-safe=1 missing-source=0', out)
