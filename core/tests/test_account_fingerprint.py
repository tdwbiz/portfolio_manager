"""Tests for core/utils/account_fingerprint.py."""

from django.test import TestCase, override_settings

from core.models import Account
from core.utils.account_fingerprint import (
    fingerprint_account_number,
    last4,
    lookup_account,
    normalize_account_number,
    scrub_text,
    wrap_tx_hash,
)


class NormalizeAccountNumberTests(TestCase):
    def test_dashed_format(self):
        self.assertEqual(normalize_account_number("900-00003-1-8"), "90000003")

    def test_long_digits_truncated_to_8(self):
        self.assertEqual(normalize_account_number("9000000318"), "90000003")

    def test_canonical_8_digits(self):
        self.assertEqual(normalize_account_number("90000003"), "90000003")

    def test_spaces_and_punctuation_stripped(self):
        self.assertEqual(normalize_account_number(" 900 00003 / 1 "), "90000003")

    def test_short_digit_string_unchanged(self):
        self.assertEqual(normalize_account_number("12345"), "12345")

    def test_letters_upper_cased_and_kept(self):
        self.assertEqual(normalize_account_number("pracef8c2dae"), "PRACEF8C2DAE")

    def test_empty_string(self):
        self.assertEqual(normalize_account_number(""), "")


class FingerprintTests(TestCase):
    def test_deterministic(self):
        a = fingerprint_account_number("90000003")
        b = fingerprint_account_number("90000003")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)  # hex sha256

    def test_equivalent_formats_same_fingerprint(self):
        self.assertEqual(
            fingerprint_account_number("900-00003-1-8"),
            fingerprint_account_number("9000000318"),
        )

    def test_different_numbers_differ(self):
        self.assertNotEqual(
            fingerprint_account_number("90000001"),
            fingerprint_account_number("90000002"),
        )

    def test_wrong_key_differs(self):
        with_default_key = fingerprint_account_number("90000003")
        with override_settings(ACCOUNT_FINGERPRINT_KEY="another-test-key-abcdefghijklmnop"):
            with_other_key = fingerprint_account_number("90000003")
        self.assertNotEqual(with_default_key, with_other_key)

    @override_settings(ACCOUNT_FINGERPRINT_KEY="")
    def test_empty_key_raises(self):
        with self.assertRaises(ValueError):
            fingerprint_account_number("90000003")

    @override_settings(ACCOUNT_FINGERPRINT_KEY="too-short")
    def test_short_key_raises(self):
        with self.assertRaises(ValueError):
            fingerprint_account_number("90000003")


class Last4Tests(TestCase):
    def test_last4(self):
        self.assertEqual(last4("900-00003-1-8"), "0003")
        self.assertEqual(last4("90000001"), "0001")

    def test_short_number_gives_last_four_chars(self):
        self.assertEqual(last4("12345"), "2345")


class LookupAccountTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="ACCOUNT_C",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000003"),
            last_4="0003")

    def test_found_by_canonical_number(self):
        self.assertEqual(lookup_account("90000003"), self.account)

    def test_found_by_dashed_number(self):
        self.assertEqual(lookup_account("900-00003-1-8"), self.account)

    def test_unknown_number_returns_none(self):
        self.assertIsNone(lookup_account("90000099"))


class WrapTxHashTests(TestCase):
    RAW = "ab" * 32

    def test_deterministic_64_hex(self):
        a = wrap_tx_hash(self.RAW)
        b = wrap_tx_hash(self.RAW)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)

    def test_differs_from_raw_and_between_hashes(self):
        self.assertNotEqual(wrap_tx_hash(self.RAW), self.RAW)
        self.assertNotEqual(
            wrap_tx_hash(self.RAW), wrap_tx_hash("cd" * 32)
        )

    def test_wrong_key_differs(self):
        with_default_key = wrap_tx_hash(self.RAW)
        with override_settings(ACCOUNT_FINGERPRINT_KEY="another-test-key-abcdefghijklmnop"):
            with_other_key = wrap_tx_hash(self.RAW)
        self.assertNotEqual(with_default_key, with_other_key)

    @override_settings(ACCOUNT_FINGERPRINT_KEY="")
    def test_empty_key_raises(self):
        with self.assertRaises(ValueError):
            wrap_tx_hash(self.RAW)


class ScrubTextTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="ACCOUNT_C",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000003"),
            last_4="0003")
        self.ib = Account.objects.create(
            name="ACCOUNT_IB",
            broker="IB",
            fingerprint=fingerprint_account_number("U12345678"),
            last_4="5678")

    def test_dashed_number_replaced_with_name(self):
        cleaned, matched, replaced, unmatched = scrub_text(
            "Buy on 900-00003-1-8 today"
        )
        self.assertEqual(cleaned, "Buy on ACCOUNT_C today")
        self.assertEqual((matched, replaced, unmatched), (1, 1, 0))

    def test_long_digit_run_truncates_to_8_and_matches(self):
        cleaned, matched, replaced, unmatched = scrub_text("acct 9000000318 end")
        self.assertEqual(cleaned, "acct ACCOUNT_C end")
        self.assertEqual((matched, replaced, unmatched), (1, 1, 0))

    def test_ib_id_replaced(self):
        cleaned, matched, replaced, unmatched = scrub_text("IB acct U12345678 ok")
        self.assertEqual(cleaned, "IB acct ACCOUNT_IB ok")
        self.assertEqual((matched, replaced, unmatched), (1, 1, 0))

    def test_date_untouched(self):
        cleaned, matched, replaced, unmatched = scrub_text("settled 2026-10-21")
        self.assertEqual(cleaned, "settled 2026-10-21")
        self.assertEqual((matched, replaced, unmatched), (0, 0, 1))

    def test_large_amount_untouched(self):
        cleaned, matched, replaced, unmatched = scrub_text("amount 12345678.90")
        self.assertEqual(cleaned, "amount 12345678.90")
        self.assertEqual((matched, replaced, unmatched), (0, 0, 1))

    def test_number_followed_by_space_and_other_number(self):
        # Only the account-number part is replaced; the other number stays.
        cleaned, matched, replaced, unmatched = scrub_text(
            "acct 90000003 bal 12345678"
        )
        self.assertEqual(cleaned, "acct ACCOUNT_C bal 12345678")
        self.assertEqual((matched, replaced, unmatched), (1, 1, 1))

    def test_repeated_number_replaced_each_time(self):
        cleaned, matched, replaced, unmatched = scrub_text(
            "90000003 and 900-00003-1-8"
        )
        self.assertEqual(cleaned, "ACCOUNT_C and ACCOUNT_C")
        self.assertEqual((matched, replaced, unmatched), (2, 2, 0))

    def test_no_candidates(self):
        self.assertEqual(scrub_text("hello world"), ("hello world", 0, 0, 0))

    def test_empty_text(self):
        self.assertEqual(scrub_text(""), ("", 0, 0, 0))
