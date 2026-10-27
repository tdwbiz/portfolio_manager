"""Report duplicate RbcMonthly statement rows, and (with --apply) delete them.

Step 11 of the PDF audit found pairs of RbcMonthly rows with the same
account, statement_date and currency but different file_name (the same PDF
imported twice under two names). This command reports those groups; with
--apply it deletes the higher-id row of each pair that is fully identical.

Deletable test (all must hold): exactly 2 rows in the group, equal
begin_total_market_value, equal end_total_market_value, equal transaction
count, equal balance count, and holdings identical as a multiset of
(currency, security, symbol, quantity, book_cost, market_price, market_value).

The report prints account name, statement date, currency and row ids only —
never values, file names or any account digits.

Usage:
    python manage.py dedupe_monthly_statements          # report only
    python manage.py dedupe_monthly_statements --apply  # delete deletable pairs
"""

from collections import Counter

from django.core.management.base import BaseCommand
from django.db.models import Count

from core.models import RbcMonthly

HOLDING_FIELDS = (
    "currency",
    "security",
    "symbol",
    "quantity",
    "book_cost",
    "market_price",
    "market_value",
)


class Command(BaseCommand):
    help = (
        "Report duplicate RbcMonthly rows (same account, statement_date, "
        "currency); with --apply delete the higher-id row of each identical pair"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Delete the higher-id row of each deletable pair (default: report only)",
        )

    def handle(self, *args, **options):
        groups = (
            RbcMonthly.objects.values("account_id", "statement_date", "currency")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
            .order_by("account_id", "statement_date", "currency")
        )

        pairs_found = 0
        would_delete = 0
        kept = 0
        not_a_pair = 0

        for group in groups:
            rows = list(
                RbcMonthly.objects.filter(
                    account_id=group["account_id"],
                    statement_date=group["statement_date"],
                    currency=group["currency"],
                ).order_by("id")
            )
            account_name = rows[0].account.name
            ids = "|".join(str(row.id) for row in rows)

            if len(rows) != 2:
                not_a_pair += 1
                self.stdout.write(
                    f"{account_name} {group['statement_date']} "
                    f"{group['currency']} ids {ids} NOT-A-PAIR"
                )
                continue

            low, high = rows
            pairs_found += 1
            reasons = self.diff_reasons(low, high)
            if reasons:
                kept += 1
                self.stdout.write(
                    f"{account_name} {group['statement_date']} "
                    f"{group['currency']} ids {low.id}|{high.id} "
                    f"KEEP {','.join(reasons)}"
                )
            else:
                would_delete += 1
                self.stdout.write(
                    f"{account_name} {group['statement_date']} "
                    f"{group['currency']} ids {low.id}|{high.id} DELETE"
                )

        self.stdout.write(
            f"pairs found: {pairs_found}  would delete: {would_delete}  "
            f"kept: {kept}  not-a-pair: {not_a_pair}"
        )

    def diff_reasons(self, low, high):
        """Field names only, in fixed order; empty list means deletable."""
        reasons = []
        if low.begin_total_market_value != high.begin_total_market_value:
            reasons.append("begin_differs")
        if low.end_total_market_value != high.end_total_market_value:
            reasons.append("end_differs")
        if low.transactions.count() != high.transactions.count():
            reasons.append("tx_count_differs")
        if low.balances.count() != high.balances.count():
            reasons.append("bal_count_differs")
        if self.holdings_signature(low) != self.holdings_signature(high):
            reasons.append("holdings_differ")
        return reasons

    @staticmethod
    def holdings_signature(row):
        return Counter(
            tuple(getattr(h, field) for field in HOLDING_FIELDS)
            for h in row.holdings.all()
        )
