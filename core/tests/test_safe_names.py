"""Tests for core/utils/safe_names.py (phase 2 step 11, plan step 1)."""

from datetime import date

from django.test import SimpleTestCase

from core.utils.safe_names import (
    ANNUAL,
    MONTHLY,
    UNKNOWN,
    hash8_of,
    is_safe_file_name,
    safe_file_name,
)


class Hash8OfTests(SimpleTestCase):
    def test_hash8_of_bytes_is_first_8_hex_of_sha256(self):
        import hashlib

        data = b"fake statement content 90000001"
        self.assertEqual(hash8_of(data), hashlib.sha256(data).hexdigest()[:8])

    def test_hash8_of_path_matches_bytes(self):
        import os

        from tempfile import NamedTemporaryFile

        with NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(b"some pdf bytes")
            path = fh.name
        try:
            self.assertEqual(hash8_of(path), hash8_of(b"some pdf bytes"))
        finally:
            os.unlink(path)

    def test_hash8_depends_on_content_not_name(self):
        # Same content under different (number-bearing) names → same hash8.
        self.assertEqual(hash8_of(b"abc"), hash8_of(b"abc"))
        self.assertNotEqual(hash8_of(b"abc"), hash8_of(b"abd"))


class SafeFileNameTests(SimpleTestCase):
    def test_monthly_pattern(self):
        name = safe_file_name(
            "RBC Monthly Statement 2026-10-31 -9999.pdf",
            "TOM_TFSA",
            MONTHLY,
            date=date(2026, 10, 31),
            hash8="a1b2c3d4",
        )
        self.assertEqual(name, "2026-10-31_monthly_TOM_TFSA_a1b2c3d4.pdf")

    def test_annual_pattern(self):
        name = safe_file_name(
            "RBC Annual 2026 -9999.pdf",
            "TOM_TFSA",
            ANNUAL,
            date=2026,
            hash8="a1b2c3d4",
        )
        self.assertEqual(name, "2026_annual_TOM_TFSA_a1b2c3d4.pdf")

    def test_extension_kept_from_original_name(self):
        name = safe_file_name("stmt.PDF", "TOM_TFSA", MONTHLY, date="2026-10-31", hash8="a1b2c3d4")
        self.assertTrue(name.endswith(".PDF"))

    def test_account_name_sanitised(self):
        name = safe_file_name("x.pdf", "SUSAN IB", MONTHLY, date="2026-10-31", hash8="a1b2c3d4")
        self.assertEqual(name, "2026-10-31_monthly_SUSAN_IB_a1b2c3d4.pdf")

    def test_deterministic_for_reimports(self):
        kwargs = dict(date=date(2026, 10, 31), hash8=hash8_of(b"content"))
        a = safe_file_name("one-90000001.pdf", "TOM_TFSA", MONTHLY, **kwargs)
        b = safe_file_name("two-90000001.pdf", "TOM_TFSA", MONTHLY, **kwargs)
        self.assertEqual(a, b)  # original name only contributes the extension

    def test_requires_hash8(self):
        with self.assertRaises(ValueError):
            safe_file_name("x.pdf", "TOM_TFSA", MONTHLY, date="2026-10-31")
        with self.assertRaises(ValueError):
            safe_file_name("x.pdf", "TOM_TFSA", MONTHLY, date="2026-10-31", hash8="xyz")

    def test_rejects_unknown_kind(self):
        with self.assertRaises(ValueError):
            safe_file_name("x.pdf", "TOM_TFSA", "quarterly", date="2026-10-31", hash8="a1b2c3d4")

    def test_monthly_requires_full_date(self):
        with self.assertRaises(ValueError):
            safe_file_name("x.pdf", "TOM_TFSA", MONTHLY, date=2026, hash8="a1b2c3d4")
        with self.assertRaises(ValueError):
            safe_file_name("x.pdf", "TOM_TFSA", ANNUAL, date="2026-10-31", hash8="a1b2c3d4")

    def test_unknown_kind_defaults_to_today(self):
        name = safe_file_name("x.pdf", "UNKNOWN_ACCOUNT", UNKNOWN, hash8="a1b2c3d4")
        self.assertTrue(is_safe_file_name(name))
        self.assertIn("_unknown_UNKNOWN_ACCOUNT_a1b2c3d4.pdf", name)


class IsSafeFileNameTests(SimpleTestCase):
    def test_round_trip_stays_safe(self):
        for name in (
            "2026-10-31_monthly_TOM_TFSA_a1b2c3d4.pdf",
            "2026_annual_SUSAN_IB_00ff00ff.pdf",
            "2026-10-31_unknown_UNKNOWN_ACCOUNT_deadbeef.pdf",
        ):
            self.assertTrue(is_safe_file_name(name))

    def test_original_number_bearing_names_are_not_safe(self):
        self.assertFalse(is_safe_file_name("RBC Monthly Statement 2026-10-31 -9999.pdf"))
        self.assertFalse(is_safe_file_name("annual_2026_TOM_TFSA.pdf"))  # no hash8
        self.assertFalse(is_safe_file_name("2026_monthly_TOM_TFSA_a1b2c3d4.pdf"))  # monthly needs full date
        self.assertFalse(is_safe_file_name("2026-10-31_annual_TOM_TFSA_a1b2c3d4.pdf"))  # annual needs year
        self.assertFalse(is_safe_file_name(""))
