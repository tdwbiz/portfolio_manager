"""Data migration: populate AccountCurrency from existing Transaction and RbcMonthly data.

For each (account, currency) pair that has transactions or RBC statements,
create an AccountCurrency record. Starting balance is set from the account-level
starting_balance if only one currency exists, or split proportionally if
the account has data in multiple currencies.
"""

from decimal import Decimal

from django.db import migrations


def populate_account_currencies(apps, schema_editor):
    Account = apps.get_model("core", "Account")
    AccountCurrency = apps.get_model("core", "AccountCurrency")
    Transaction = apps.get_model("core", "Transaction")
    RbcMonthly = apps.get_model("core", "RbcMonthly")

    for account in Account.objects.all():
        # Detect currencies from transactions
        tx_currencies = (
            Transaction.objects.filter(account=account)
            .values_list("currency", flat=True)
            .distinct()
        )
        # Also check RBC monthly statements
        rbc_currencies = (
            RbcMonthly.objects.filter(account=account)
            .values_list("currency", flat=True)
            .distinct()
        )

        all_currencies = set(tx_currencies) | set(rbc_currencies)

        if not all_currencies:
            # No data — create a default based on account.currency
            cur = account.currency if account.currency in ("CAD", "USD") else "CAD"
            AccountCurrency.objects.get_or_create(
                account=account,
                currency=cur,
                defaults={
                    "starting_balance": account.starting_balance,
                    "starting_balance_date": account.starting_balance_date,
                },
            )
            continue

        if len(all_currencies) == 1:
            # Single currency — use account-level starting balance
            cur = list(all_currencies)[0]
            AccountCurrency.objects.get_or_create(
                account=account,
                currency=cur,
                defaults={
                    "starting_balance": account.starting_balance,
                    "starting_balance_date": account.starting_balance_date,
                },
            )
        else:
            # Multi-currency — split starting balance proportionally
            # Use RBC beginning balances if available, otherwise split equally
            total_rbc_begin = Decimal("0")
            currency_begins = {}
            for cur in all_currencies:
                begin = (
                    RbcMonthly.objects.filter(account=account, currency=cur)
                    .order_by("statement_date")
                    .values_list("begin_total_market_value", flat=True)
                    .first()
                )
                if begin:
                    currency_begins[cur] = begin
                    total_rbc_begin += begin

            if total_rbc_begin > 0:
                # Proportional split based on RBC beginning balances
                for cur in all_currencies:
                    begin = currency_begins.get(cur, Decimal("0"))
                    proportion = (
                        begin / total_rbc_begin if total_rbc_begin else Decimal("0.5")
                    )
                    AccountCurrency.objects.get_or_create(
                        account=account,
                        currency=cur,
                        defaults={
                            "starting_balance": (account.starting_balance * proportion),
                            "starting_balance_date": account.starting_balance_date,
                        },
                    )
            else:
                # Equal split
                split = account.starting_balance / len(all_currencies)
                for cur in all_currencies:
                    AccountCurrency.objects.get_or_create(
                        account=account,
                        currency=cur,
                        defaults={
                            "starting_balance": split,
                            "starting_balance_date": account.starting_balance_date,
                        },
                    )

        # Update account.currency to reflect reality
        # Use 'MIX' for multi-currency (max_length=3 constraint on the column)
        # Must use raw SQL to avoid issues when the column was previously
        # wider (e.g. max_length=5) and has 'MIXED' stored. Django's save()
        # may trigger SQL Server truncation errors on existing rows.
        if len(all_currencies) > 1:
            new_currency = "MIX"
        else:
            new_currency = list(all_currencies)[0]
        Account.objects.filter(pk=account.pk).update(currency=new_currency)


def reverse_populate(apps, schema_editor):
    """Reverse: remove AccountCurrency records."""
    AccountCurrency = apps.get_model("core", "AccountCurrency")
    AccountCurrency.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_alter_account_category_alter_account_currency_and_more"),
    ]

    operations = [
        migrations.RunPython(populate_account_currencies, reverse_populate),
    ]
