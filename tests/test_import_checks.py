"""Unit tests for core/utils/import_checks.py (import-checks plan, step 1).

Covers the three date rules (future, before 2000, 1–100 days after the
Date of Last Statement printed on the same PDF) and the content hash.
No real data: fixed dates and a FileImportLog row only — no accounts needed.
"""

import hashlib
import os
import tempfile
from datetime import date

from django.test import TestCase

from core.models import FileImportLog
from core.utils.import_checks import (
    content_hash_of,
    is_content_imported,
    statement_date_problems,
)
from core.utils.safe_names import hash8_of

TODAY = date(2026, 10, 27)


class ContentHashOfTests(TestCase):
    def test_sha256_of_file_bytes(self):
        payload = b"import-checks hash fixture\n" * 100
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(payload)
            path = fh.name
        try:
            self.assertEqual(content_hash_of(path), hashlib.sha256(payload).hexdigest())
            # Same digest family as hash8_of (its first 8 chars).
            self.assertEqual(content_hash_of(path)[:8], hash8_of(payload))
        finally:
            os.unlink(path)

    def test_accepts_path_objects(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
            fh.write(b"abc")
            path = fh.name
        try:
            from pathlib import Path

            self.assertEqual(
                content_hash_of(Path(path)), hashlib.sha256(b"abc").hexdigest()
            )
        finally:
            os.unlink(path)


class StatementDateProblemsTests(TestCase):
    def test_valid_date_without_last_known_is_ok(self):
        self.assertEqual(statement_date_problems(date(2025, 9, 30), today=TODAY), [])

    def test_future_date_fails(self):
        problems = statement_date_problems(date(2026, 10, 28), today=TODAY)
        self.assertEqual(problems, ["statement date in the future"])

    def test_today_is_not_future(self):
        self.assertEqual(statement_date_problems(TODAY, today=TODAY), [])

    def test_before_2000_fails(self):
        problems = statement_date_problems(date(1999, 12, 31), today=TODAY)
        self.assertEqual(problems, ["statement date before 2000-01-01"])

    def test_year_2000_boundary_is_ok(self):
        self.assertEqual(statement_date_problems(date(2000, 1, 1), today=TODAY), [])

    def test_missing_or_unreadable_date_reports_one_reason(self):
        self.assertEqual(
            statement_date_problems(None, today=TODAY),
            ["statement date missing or unreadable"],
        )
        self.assertEqual(
            statement_date_problems("SEPT 30 2025", today=TODAY),
            ["statement date missing or unreadable"],
        )

    def test_gap_rule_against_pdf_last_statement_date(self):
        last = date(2025, 8, 29)  # as printed on the PDF, not from the DB
        # gap of 1 and 100 days are OK
        self.assertEqual(
            statement_date_problems(last.fromordinal(last.toordinal() + 1),
                                    today=TODAY, last_known=last),
            [],
        )
        self.assertEqual(
            statement_date_problems(date(2025, 12, 7), today=TODAY, last_known=last),
            [],
        )
        # same day (gap 0) and gap of 101 days fail
        for bad in (date(2025, 8, 29), date(2025, 12, 8)):
            problems = statement_date_problems(bad, today=TODAY, last_known=last)
            self.assertEqual(
                problems,
                ["statement date not 1-100 days after the Date of Last Statement "
                 "on the same PDF"],
            )

    def test_no_gap_rule_without_last_known(self):
        # No Date of Last Statement on the PDF: no gap check at all.
        self.assertEqual(
            statement_date_problems(date(2025, 9, 30), today=TODAY, last_known=None),
            [],
        )

    def test_accepts_iso_strings(self):
        self.assertEqual(
            statement_date_problems("2025-09-30", today="2026-10-27",
                                    last_known="2025-08-29"),
            [],
        )


class IsContentImportedTests(TestCase):
    def test_success_log_counts(self):
        FileImportLog.objects.create(
            filename="a.pdf", broker="RBC", status="success", content_hash="h1"
        )
        self.assertTrue(is_content_imported("h1"))

    def test_ok_log_counts(self):
        FileImportLog.objects.create(
            filename="b.pdf", broker="RBC", status="ok", content_hash="h2"
        )
        self.assertTrue(is_content_imported("h2"))

    def test_error_log_does_not_count(self):
        # A failed import may be retried — it is not a duplicate.
        FileImportLog.objects.create(
            filename="c.pdf", broker="RBC", status="error", content_hash="h3"
        )
        self.assertFalse(is_content_imported("h3"))

    def test_unknown_hash_and_empty_hash(self):
        self.assertFalse(is_content_imported("nope"))
        self.assertFalse(is_content_imported(""))
