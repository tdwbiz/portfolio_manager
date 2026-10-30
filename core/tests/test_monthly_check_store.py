"""Tests for monthly_check storage helpers (phase 4, step 14b).

Fake accounts ACCOUNT_A ... ACCOUNT_H per AGENTS.md. Plan:
docs/plans/monthly-check-store.md (Decisions: tdw, Oct 30).
"""

import os
import tempfile
from datetime import date
from decimal import Decimal

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from core.models import (
    Account,
    BrokerStatement,
    FileImportLog,
    MonthlyCheckResult,
    MonthlyException,
    RbcMonthly,
    RbcMonthlyBalance,
    Transaction,
)
from core.utils.monthly_check import (
    CurrencyResult,
    StatementCheck,
    StatementGroup,
)
from core.utils.monthly_check_store import (
    exceptions_from_check,
    import_exceptions_from_parked,
    store_exceptions,
    store_results,
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


class StoreLogicTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC", last_4="0001"
        )
        self.d = date(2025, 2, 28)

    def make_group_check(self, verdict="FAIL"):
        group = StatementGroup(
            account_pk=self.account.pk,
            account_name="ACCOUNT_A",
            statement_date=self.d,
        )
        check = StatementCheck(account_name="ACCOUNT_A", statement_date=self.d)
        failures = (
            [
                "CASH: expected 100.00 (start 100.00 + flows 0.00), "
                "got 75.00, diff -25.00"
            ]
            if verdict == "FAIL"
            else []
        )
        check.currencies.append(
            CurrencyResult(currency="CAD", verdict=verdict, failures=failures)
        )
        return group, check

    def cash_exc(self, diff):
        difference = None if diff is None else Decimal(diff)
        return {
            "kind": "CASH",
            "currency": "CAD",
            "ref": "",
            "difference": difference,
            "detail": (
                f"CASH: expected 100.00 (start 100.00 + flows 0.00), got "
                f"75.00, diff {diff}"
                if diff is not None
                else "CASH: this statement has no Cash section for this currency"
            ),
        }

    def test_store_twice_gives_same_rows(self):
        group, check = self.make_group_check()
        store_results([(group, check)])
        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])
        first_seen = MonthlyException.objects.get().first_seen

        store_results([(group, check)])
        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])

        self.assertEqual(MonthlyCheckResult.objects.count(), 1)
        self.assertEqual(MonthlyException.objects.count(), 1)
        row = MonthlyException.objects.get()
        self.assertEqual(row.status, "open")
        # first_seen is set only on create; last_seen moves on every run.
        self.assertEqual(row.first_seen, first_seen)
        self.assertGreaterEqual(row.last_seen, first_seen)

    def test_disappeared_exception_becomes_resolved(self):
        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])

        created, updated, reopened, resolved = store_exceptions(
            self.account, self.d, []
        )

        self.assertEqual((created, updated, reopened, resolved), (0, 0, 0, 1))
        row = MonthlyException.objects.get()
        self.assertEqual(row.status, "resolved")
        self.assertIsNotNone(row.resolved_at)

    def test_accepted_same_difference_stays_accepted(self):
        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])
        row = MonthlyException.objects.get()
        row.status = "accepted"
        row.reason = "known CSV gap"
        row.accepted_at = timezone.now()
        row.save()

        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])

        row.refresh_from_db()
        self.assertEqual(row.status, "accepted")
        self.assertNotIn("reopened", row.detail)

    def test_accepted_changed_difference_reopens_with_note(self):
        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])
        row = MonthlyException.objects.get()
        row.status = "accepted"
        row.reason = "known CSV gap"
        row.accepted_at = timezone.now()
        row.save()

        store_exceptions(self.account, self.d, [self.cash_exc("-30.00")])

        row.refresh_from_db()
        self.assertEqual(row.status, "open")
        self.assertIsNone(row.resolved_at)
        # The stored difference is at 4 dp (decimal_places=4).
        self.assertIn(
            "difference changed from -25.0000 to -30.00", row.detail
        )

    def test_null_difference_never_auto_reopens(self):
        store_exceptions(self.account, self.d, [self.cash_exc(None)])
        row = MonthlyException.objects.get()
        self.assertIsNone(row.difference)
        row.status = "accepted"
        row.accepted_at = timezone.now()
        row.save()

        # Difference stays NULL on the re-run: still accepted.
        store_exceptions(self.account, self.d, [self.cash_exc(None)])
        row.refresh_from_db()
        self.assertEqual(row.status, "accepted")

    def test_resolved_exception_found_again_returns_to_open(self):
        store_exceptions(self.account, self.d, [self.cash_exc("-25.00")])
        store_exceptions(self.account, self.d, [])  # -> resolved
        created, updated, reopened, resolved = store_exceptions(
            self.account, self.d, [self.cash_exc("-25.00")]
        )
        self.assertEqual((created, updated, reopened, resolved), (0, 1, 0, 0))
        row = MonthlyException.objects.get()
        self.assertEqual(row.status, "open")
        self.assertIsNone(row.resolved_at)

    def test_store_results_stores_all_verdicts_and_upserts(self):
        group, check = self.make_group_check(verdict="PASS")
        store_results([(group, check)])
        # Re-run with a different verdict: same row, updated in place.
        group2, check2 = self.make_group_check(verdict="FAIL")
        store_results([(group2, check2)])

        self.assertEqual(MonthlyCheckResult.objects.count(), 1)
        result = MonthlyCheckResult.objects.get()
        self.assertEqual(result.verdict, "FAIL")
        self.assertIsNotNone(result.checked_at)


class StoreCommandTests(TestCase):
    """``manage.py monthly_check --store`` end to end (synthetic data)."""

    def setUp(self):
        self.out_path = os.path.join(tempfile.mkdtemp(), "report.txt")
        self.account = Account.objects.create(
            name="ACCOUNT_A", broker="RBC", last_4="0001"
        )
        # January (FIRST) and February (CASH FAIL: expected 125, got 100).
        jan = RbcMonthly.objects.create(
            account=self.account,
            file_name="2025-01-31_monthly_ACCOUNT_A_11111111.pdf",
            statement_date=date(2025, 1, 31),
            currency="CAD",
            last_statement_date=None,
            begin_total_market_value=Decimal("100"),
            end_total_market_value=Decimal("200"),
        )
        RbcMonthlyBalance.objects.create(
            rbc_monthly=jan, currency="CAD", account_type="Cash",
            opening_balance=Decimal("100"), closing_balance=Decimal("100"),
            market_value=Decimal("100"),
        )
        feb = RbcMonthly.objects.create(
            account=self.account,
            file_name="2025-02-28_monthly_ACCOUNT_A_22222222.pdf",
            statement_date=date(2025, 2, 28),
            currency="CAD",
            last_statement_date=date(2025, 1, 31),
            begin_total_market_value=Decimal("200"),
            end_total_market_value=Decimal("250"),
        )
        RbcMonthlyBalance.objects.create(
            rbc_monthly=feb, currency="CAD", account_type="Cash",
            opening_balance=Decimal("100"), closing_balance=Decimal("100"),
            market_value=Decimal("100"),
        )
        stmt = BrokerStatement.objects.create(
            source="RBC", file_date=date(2025, 2, 10), raw_filename="t.csv"
        )
        Transaction.objects.create(
            statement=stmt, account=self.account,
            date=date(2025, 2, 10), settlement_date=date(2025, 2, 12),
            symbol="", type="DEP", quantity=Decimal("0"),
            price=Decimal("0"), commission=Decimal("0"),
            amount=Decimal("25"), currency="CAD",
        )
        # A parked errored monthly PDF (never stored) and a conflict.
        FileImportLog.objects.create(
            filename="2024-11-30_monthly_ACCOUNT_A_deadbeef.pdf", broker="RBC",
            status="error", error_message="VL parse failed",
        )
        FileImportLog.objects.create(
            filename="2024-12-31_monthly_ACCOUNT_A_abcdef12.pdf", broker="RBC",
            status="error",
            error_message="conflict: second copy of a corrected statement",
        )

    def test_store_twice_gives_same_rows(self):
        call_command("monthly_check", "--store")
        cash = MonthlyException.objects.get(kind="CASH")
        first_seen = cash.first_seen

        call_command("monthly_check", "--store")

        self.assertEqual(MonthlyCheckResult.objects.count(), 2)  # FIRST + FAIL
        self.assertEqual(MonthlyException.objects.count(), 2)  # CASH + IMPORT
        cash.refresh_from_db()
        self.assertEqual(cash.first_seen, first_seen)

    def test_without_store_nothing_is_written(self):
        call_command("monthly_check", "--out", self.out_path)

        self.assertEqual(MonthlyCheckResult.objects.count(), 0)
        self.assertEqual(MonthlyException.objects.count(), 0)
        # The report file is still written, and report mode without --out
        # is refused.
        with open(self.out_path) as f:
            self.assertIn("monthly_check report", f.read())
        with self.assertRaises(CommandError):
            call_command("monthly_check")

    def test_import_exceptions_from_parked_not_conflicts(self):
        call_command("monthly_check", "--store")

        imp = MonthlyException.objects.get(kind="IMPORT")
        self.assertEqual(imp.ref, "deadbeef")
        self.assertEqual(imp.statement_date, date(2024, 11, 30))
        self.assertIn("VL parse failed", imp.detail)
        # The conflict (second copy) never becomes an exception.
        self.assertFalse(
            MonthlyException.objects.filter(ref="abcdef12").exists()
        )
