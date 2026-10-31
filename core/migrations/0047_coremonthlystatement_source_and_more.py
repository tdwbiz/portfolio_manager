# Hand-written (plan: docs/plans/statements-forward.md, step 3).
#
# statement_date is non-nullable but existing rows predate it: add it with a
# placeholder default, backfill the calendar month end of statement_month for
# every existing row, then drop the default.

import calendar
from datetime import date
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def backfill_statement_date(apps, schema_editor):
    Statement = apps.get_model("core", "CoreMonthlyStatement")
    rows = []
    for row in Statement.objects.iterator():
        last_day = calendar.monthrange(
            row.statement_month.year, row.statement_month.month
        )[1]
        rows.append(
            Statement(
                pk=row.pk,
                statement_date=row.statement_month.replace(day=last_day),
            )
        )
    if rows:
        Statement.objects.bulk_update(rows, ["statement_date"])


def revert_statement_date(apps, schema_editor):
    # The original per-row dates cannot be recovered; the column is dropped
    # by the reversed AddField.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0046_monthlycheckresult_monthlyexception'),
    ]

    operations = [
        migrations.AddField(
            model_name='coremonthlystatement',
            name='source',
            field=models.CharField(
                choices=[('REPLAY', 'REPLAY'), ('PDF', 'PDF'), ('GAP', 'GAP')],
                default='REPLAY',
                help_text='How the row was generated: REPLAY (previous PDF statement + CSV transactions), PDF (taken from the RBC statement as-is), GAP (no PDF statement, no transactions)',
                max_length=6,
            ),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='statement_date',
            field=models.DateField(
                default=date(1900, 1, 31),
                help_text='RBC statement date; calendar month end for gap and replay rows',
            ),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='total_deposits',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='total_withdrawals',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='total_transfers',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='total_income',
            field=models.DecimalField(
                decimal_places=2, default=Decimal('0.00'), max_digits=15,
                help_text='DIV + DIST + INT for the period (native currency)',
            ),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='total_fees',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='total_tax',
            field=models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15),
        ),
        migrations.AddField(
            model_name='coremonthlystatement',
            name='has_stale_prices',
            field=models.BooleanField(
                default=False,
                help_text='True when at least one position is priced from the last known PDF price (no PDF holding and no historical close available)',
            ),
        ),
        migrations.RunPython(backfill_statement_date, revert_statement_date),
        migrations.AlterField(
            model_name='coremonthlystatement',
            name='statement_date',
            field=models.DateField(
                help_text='RBC statement date; calendar month end for gap and replay rows'
            ),
        ),
        migrations.CreateModel(
            name='CoreMonthlyStatementPosition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('symbol', models.CharField(max_length=50)),
                ('quantity', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=12)),
                ('price', models.DecimalField(decimal_places=4, default=Decimal('0.0000'), max_digits=12)),
                ('price_source', models.CharField(
                    choices=[('PDF', 'PDF'), ('HISTORY', 'HISTORY'), ('LAST_PDF', 'LAST_PDF')],
                    default='PDF',
                    help_text='PDF (holding on this statement), HISTORY (latest close at or before the statement date), LAST_PDF (last known PDF price, row marked stale)',
                    max_length=8,
                )),
                ('value', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15)),
                ('statement', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='positions',
                    to='core.coremonthlystatement',
                )),
            ],
            options={
                'db_table': 'core_monthly_statement_position',
                'ordering': ['symbol'],
                'unique_together': {('statement', 'symbol')},
            },
        ),
    ]
