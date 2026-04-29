"""
Migration 0006: Add tx_hash to Transaction.

Steps:
  1. Add tx_hash field (nullable, no constraint yet).
  2. RunPython: compute hashes for all existing rows; delete duplicate rows
     (keeping the one with the lowest pk per hash group).
  3. AlterField: make tx_hash unique + non-nullable.
"""

import hashlib
from decimal import Decimal

from django.db import migrations, models


def _hash(broker, account_id, date, symbol, tx_type, quantity, price, amount, currency):
    key = "|".join([
        broker or '',
        account_id or '',
        str(date),
        symbol or '',
        tx_type or '',
        f"{Decimal(str(quantity)):.4f}",
        f"{Decimal(str(price)):.4f}",
        f"{Decimal(str(amount)):.2f}",
        currency or '',
    ])
    return hashlib.sha256(key.encode()).hexdigest()


def populate_and_deduplicate(apps, schema_editor):
    Transaction = apps.get_model('core', 'Transaction')

    # Map hash → lowest pk seen so far; collect pks to delete.
    seen = {}        # hash → pk to keep
    to_delete = []   # pks to remove

    # Order by pk so we always keep the first-imported record.
    for tx in (
        Transaction.objects
        .select_related('statement', 'account')
        .order_by('id')
    ):
        broker     = tx.statement.source if tx.statement else ''
        account_id = tx.account.account_id if tx.account else ''
        h = _hash(
            broker, account_id, tx.date, tx.symbol or '',
            tx.type or '', tx.quantity, tx.price, tx.amount,
            tx.currency or '',
        )
        if h in seen:
            to_delete.append(tx.pk)
        else:
            seen[h] = tx.pk
            tx.tx_hash = h
            tx.save(update_fields=['tx_hash'])

    if to_delete:
        Transaction.objects.filter(pk__in=to_delete).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_transactiontype_and_transaction_notes'),
    ]

    operations = [
        # Step 1 — add the nullable field
        migrations.AddField(
            model_name='transaction',
            name='tx_hash',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        # Step 2 — populate hashes and remove duplicates
        migrations.RunPython(populate_and_deduplicate, migrations.RunPython.noop),
        # Step 3 — make it unique and non-nullable
        migrations.AlterField(
            model_name='transaction',
            name='tx_hash',
            field=models.CharField(max_length=64, unique=True),
        ),
    ]
