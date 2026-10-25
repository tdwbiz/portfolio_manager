"""Tests for core/utils/account_fingerprint.py."""

from django.test import TestCase, override_settings

from core.models import Account
from core.utils.account_fingerprint import (
    fingerprint_account_number,
    last4,
    lookup_account,
    normalize_account_number,
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
            account_id="ACCOUNT_C",
            name="ACCOUNT_C",
            broker="RBC",
            fingerprint=fingerprint_account_number("90000003"),
            last_4="0003",
        )

    def test_found_by_canonical_number(self):
        self.assertEqual(lookup_account("90000003"), self.account)

    def test_found_by_dashed_number(self):
        self.assertEqual(lookup_account("900-00003-1-8"), self.account)

    def test_unknown_number_returns_none(self):
        self.assertIsNone(lookup_account("90000099"))
