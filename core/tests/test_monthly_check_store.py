"""Tests for monthly_check storage helpers (phase 4, step 14b).

Fake accounts ACCOUNT_A ... ACCOUNT_H per AGENTS.md. Plan:
docs/plans/monthly-check-store.md (Decisions: tdw, Oct 30).
"""

from datetime import date
from decimal import Decimal

from django.test import TestCase

from core.models import Account
from core.utils.monthly_check import CurrencyResult, StatementCheck
from core.utils.monthly_check_store import (
    exceptions_from_check,
    import_exceptions_from_parked,
)


def make_check(failures_by_currency):
    """A StatementCheck with the given {currency: [failure lines]}."""
    check = StatementCheck(
        account_name="ACCOUNT_A", statement_date=date(2025, 2, 28)
    )
    for currency in sorted(failures_by_currency):
        check.currencies.append(
            CurrencyResult(
                currency=currency,
                verdict="FAIL",
                failures=list(failures_by_currency[currency]),
            )
        )
    return check


class ExceptionsFromCheckTests(TestCase):
    def test_quantity_deduplicated_across_currencies(self):
        # check_statement attaches the pooled QUANTITY failures to every
        # currency; storage must keep exactly one, with currency "".
        qty = "QUANTITY: ACME: expected 60 (prev 100 + csv -40), got 50"
        check = make_check(
            {
                "CAD": [qty],
                "USD": ["CHAIN: no previous USD section on 2025-01-31 to chain from, but begin total is 500.00", qty],
            }
        )

        excs = exceptions_from_check(check)
        by_key = {(e["kind"], e["currency"], e["ref"]): e for e in excs}
        self.assertEqual(
            set(by_key), {("QUANTITY", "", "ACME"), ("CHAIN", "USD", "")}
        )
        qty_exc = by_key[("QUANTITY", "", "ACME")]
        # got - expected.
        self.assertEqual(qty_exc["difference"], Decimal("-10"))
        # Attached to two currencies, stored once: detail holds one line.
        self.assertEqual(qty_exc["detail"], qty)

    def test_cash_diff_parsed_and_unmatched_rows_kept(self):
        check = make_check(
            {
                "CAD": [
                    "CASH: expected 150.00 (start 100.00 + flows 50.00), got 125.00, diff -25.00",
                    "CASH: unmatched CSV row: 2025-02-15 DIV ACME 25.00",
                    "CASH: trade-date replay still fails (expected 150.00)",
                ]
            }
        )

        excs = exceptions_from_check(check)
        self.assertEqual(len(excs), 1)
        exc = excs[0]
        self.assertEqual((exc["kind"], exc["currency"], exc["ref"]),
                         ("CASH", "CAD", ""))
        self.assertEqual(exc["difference"], Decimal("-25.00"))
        # All grouped lines, unmatched row included (decision 1).
        self.assertEqual(
            exc["detail"].splitlines(),
            [
                "CASH: expected 150.00 (start 100.00 + flows 50.00), got 125.00, diff -25.00",
                "CASH: unmatched CSV row: 2025-02-15 DIV ACME 25.00",
                "CASH: trade-date replay still fails (expected 150.00)",
            ],
        )

    def test_chain_diff_parsed_and_absent_diff_is_none(self):
        check = make_check(
            {
                "CAD": [
                    "CHAIN: begin_total_market_value 390.00 != previous end_total_market_value 400.00 (diff -10.00)"
                ],
                "USD": [
                    "CHAIN: no previous USD section on 2025-01-31 to chain from, but begin total is 500.00"
                ],
            }
        )

        excs = exceptions_from_check(check)
        by_cur = {e["currency"]: e for e in excs}
        self.assertEqual(by_cur["CAD"]["difference"], Decimal("-10.00"))
        # No "(diff ...)" in the line: difference stays None (decision 5).
        self.assertIsNone(by_cur["USD"]["difference"])
        self.assertEqual(
            by_cur["USD"]["detail"],
            "CHAIN: no previous USD section on 2025-01-31 to chain from, but begin total is 500.00",
        )

    def test_unattributable_line_raises(self):
        check = make_check({"CAD": ["MYSTERY: something went wrong"]})
        with self.assertRaises(ValueError):
            exceptions_from_check(check)


class ImportExceptionsFromParkedTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC", last_4="0001"
        )

    def test_parked_entry_becomes_import_exception(self):
        parked = [
            {
                "filename": "2023-05-31_monthly_ACCOUNT_A_aabbccdd.pdf",
                "status": "error",
                "reason": "VL parse error: bad page",
                "imported_at": None,
            }
        ]
        excs, skipped = import_exceptions_from_parked(parked)
        self.assertEqual(skipped, [])
        self.assertEqual(
            excs,
            [
                {
                    "kind": "IMPORT",
                    "account_name": "ACCOUNT_A",
                    "statement_date": date(2023, 5, 31),
                    "currency": "",
                    "ref": "aabbccdd",
                    "difference": None,
                    "detail": "VL parse error: bad page",
                }
            ],
        )

    def test_unknown_account_and_bad_name_are_skipped(self):
        parked = [
            {
                "filename": "2023-05-31_monthly_NO_SUCH_ACCOUNT_aabbccdd.pdf",
                "status": "error",
                "reason": "r1",
                "imported_at": None,
            },
            {
                "filename": "2023_monthly_ACCOUNT_A_aabbccdd.pdf",
                "status": "error",
                "reason": "r2 (no full date)",
                "imported_at": None,
            },
        ]
        excs, skipped = import_exceptions_from_parked(parked)
        self.assertEqual(excs, [])
        self.assertEqual(len(skipped), 2)
