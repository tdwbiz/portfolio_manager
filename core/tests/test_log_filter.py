"""Tests for core/log_filter.py (phase 2 step 11, plan steps 2–3)."""

import logging
import sys
from unittest import mock

from django.test import TestCase, override_settings

from core.models import Account
from core.log_filter import ScrubFilter, fallback_scrub
from core.utils.account_fingerprint import fingerprint_account_number


def _make_record(msg, args=None):
    return logging.LogRecord("test", logging.INFO, __file__, 1, msg, args, None)


class ScrubFilterTests(TestCase):
    def setUp(self) -> None:
        ScrubFilter.reset_cache()
        self.account_a = Account.objects.create(
            name="ACCOUNT_A", broker="RBC",
            fingerprint=fingerprint_account_number("90000001"), last_4="0001",
        )
        self.account_b = Account.objects.create(
            name="ACCOUNT_B", broker="RBC",
            fingerprint=fingerprint_account_number("90000002"), last_4="0002",
        )

    def _run(self, msg, args=None):
        record = _make_record(msg, args)
        self.assertTrue(ScrubFilter().filter(record))  # must never raise
        return record.getMessage(), record.args

    def test_fingerprint_replaced_with_account_name(self):
        text, _ = self._run("imported statement for 90000001 ok")
        self.assertEqual(text, "imported statement for ACCOUNT_A ok")

    def test_dash_variant_replaced(self):
        text, _ = self._run("acct 900-00001-1-8 end")
        self.assertEqual(text, "acct ACCOUNT_A end")

    def test_second_account(self):
        text, _ = self._run("90000002 and 90000001")
        self.assertEqual(text, "ACCOUNT_B and ACCOUNT_A")

    def test_15_digit_run_becomes_bank_account(self):
        text, _ = self._run("wire to 123456789012345 done")
        self.assertEqual(text, "wire to BANK_ACCOUNT done")

    def test_dates_and_amounts_pass_through(self):
        original = "on 2026-10-21 bought for 1234.56, total 12345678.90"
        text, _ = self._run(original)
        self.assertEqual(text, original)

    def test_unknown_8_digit_run_untouched_when_map_available(self):
        text, _ = self._run("ref 42424242 end")
        self.assertEqual(text, "ref 42424242 end")

    def test_args_cleared_when_message_changed(self):
        record = _make_record("account %s", ("90000001",))
        ScrubFilter().filter(record)
        self.assertIsNone(record.args)
        self.assertEqual(record.getMessage(), "account ACCOUNT_A")

    def test_non_string_message_does_not_raise(self):
        record = _make_record(12345)
        self.assertTrue(ScrubFilter().filter(record))

    @override_settings(ACCOUNT_FINGERPRINT_KEY="")
    def test_fallback_with_blank_key(self):
        ScrubFilter.reset_cache()
        text, _ = self._run("acct 90000001 bank 123456789012345 end")
        self.assertEqual(text, "acct ACCOUNT bank BANK_ACCOUNT end")

    def test_fallback_with_no_db_rows(self):
        Account.objects.all().delete()
        ScrubFilter.reset_cache()
        text, _ = self._run("90000001 and 123456789012345")
        self.assertEqual(text, "ACCOUNT and BANK_ACCOUNT")

    def test_fallback_16_digit_run_becomes_account(self):
        Account.objects.all().delete()
        ScrubFilter.reset_cache()
        text, _ = self._run("id 1234567890123456 end")
        self.assertEqual(text, "id ACCOUNT end")

    def test_never_raises_on_unformattable_args(self):
        record = _make_record("%d", ("not-an-int",))
        self.assertTrue(ScrubFilter().filter(record))  # no exception
        self.assertEqual(record.msg, "%d")  # left untouched

    def test_exception_traceback_is_scrubbed(self):
        """logger.exception / exc_info tracebacks must be scrubbed too —
        handlers emit record.exc_text verbatim when it is pre-set."""
        record = _make_record("import failed for 90000001")
        try:
            raise ValueError("statement 90000001 missing")
        except ValueError:
            record.exc_info = sys.exc_info()
        self.assertTrue(ScrubFilter().filter(record))  # must never raise
        self.assertEqual(record.getMessage(), "import failed for ACCOUNT_A")
        self.assertIsNotNone(record.exc_text)
        self.assertNotIn("90000001", record.exc_text)
        self.assertIn("ACCOUNT_A", record.exc_text)  # the exception message line

    def test_stack_info_is_scrubbed(self):
        record = _make_record("with stack")
        record.stack_info = "File x.py, line 1\n    ref 90000001 end"
        self.assertTrue(ScrubFilter().filter(record))
        self.assertEqual(
            record.stack_info, "File x.py, line 1\n    ref ACCOUNT_A end"
        )


class FallbackScrubTests(TestCase):
    def test_15_digits_first_then_long_runs(self):
        self.assertEqual(
            fallback_scrub("a 123456789012345 b 87654321 c 1234 d"),
            "a BANK_ACCOUNT b ACCOUNT c 1234 d",
        )

    def test_dash_runs_and_short_runs_untouched(self):
        self.assertEqual(fallback_scrub("900-00001-1-8 and 123.45"), "900-00001-1-8 and 123.45")


class ScrubFilterReentryTests(TestCase):
    def setUp(self) -> None:
        ScrubFilter.reset_cache()

    def test_reentrant_record_gets_fallback_scrub_not_recursion(self):
        """A record logged while the name map is being built (e.g. SQL query
        logging) must get the fallback scrub, not trigger a second build."""

        def fake_build():
            inner = _make_record("select from account 90000001")
            self.assertTrue(ScrubFilter().filter(inner))  # must never raise
            self.assertEqual(inner.msg, "select from account ACCOUNT")
            return {}

        with mock.patch(
            "core.utils.account_fingerprint.build_fingerprint_name_map",
            side_effect=fake_build,
        ) as builder:
            outer = _make_record("hello")
            ScrubFilter().filter(outer)
            self.assertEqual(builder.call_count, 1)  # no recursive build


class ScrubFilterCacheTests(TestCase):
    def setUp(self) -> None:
        ScrubFilter.reset_cache()

    def test_name_map_cached_and_resettable(self):
        with mock.patch(
            "core.utils.account_fingerprint.build_fingerprint_name_map",
            side_effect=lambda: {"fp": "NAME"},
        ) as builder:
            ScrubFilter().filter(_make_record("hello"))
            ScrubFilter().filter(_make_record("hello again"))
            self.assertEqual(builder.call_count, 1)
            ScrubFilter.reset_cache()
            ScrubFilter().filter(_make_record("hello third"))
            self.assertEqual(builder.call_count, 2)
