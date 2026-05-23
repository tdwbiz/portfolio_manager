# Generated migration for PDF models

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_historicalprice'),
    ]

    operations = [
        migrations.CreateModel(
            name='PdfStatement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file_name', models.CharField(max_length=255)),
                ('statement_date', models.DateField()),
                ('last_statement_date', models.DateField(blank=True, help_text='Date of previous statement', null=True)),
                ('account_number', models.CharField(blank=True, default='', help_text='RBC account number from PDF', max_length=50)),
                ('fx_rate', models.DecimalField(
                    decimal_places=6, default=Decimal('1.000000'),
                    help_text='USD to CAD exchange rate as of statement date',
                    max_digits=10,
                )),
                ('parsed_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='pdf_statements',
                    to='core.account',
                )),
            ],
            options={
                'ordering': ['-statement_date'],
            },
        ),
        migrations.CreateModel(
            name='PdfAccountBalance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account_type', models.CharField(
                    help_text='e.g. "Cash", "Mutual Funds", "Foreign Securities", "Preferred Shares", "Common Shares", "Fixed Income", "Other"',
                    max_length=50,
                )),
                ('opening_balance', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'),
                    help_text='Opening balance at last statement date',
                    max_digits=15,
                )),
                ('closing_balance', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'),
                    help_text='Closing balance at this statement date',
                    max_digits=15,
                )),
                ('market_value', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'),
                    help_text='Market value at this statement date',
                    max_digits=15,
                )),
                ('percentage', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'),
                    help_text='Percentage of total portfolio',
                    max_digits=5,
                )),
                ('pdf_statement', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='balances',
                    to='core.pdfstatement',
                )),
            ],
            options={
                'ordering': ['account_type'],
                'unique_together': {('pdf_statement', 'account_type')},
            },
        ),
        migrations.CreateModel(
            name='PdfTransaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('date', models.DateField()),
                ('activity_type', models.CharField(
                    help_text='e.g. "INTEREST", "BUY", "SELL", "DIVIDEND"',
                    max_length=50,
                )),
                ('description', models.TextField(
                    blank=True, default='',
                    help_text='Full activity description from PDF',
                )),
                ('symbol', models.CharField(blank=True, default='', max_length=50)),
                ('quantity', models.DecimalField(
                    decimal_places=4, default=Decimal('0.0000'), max_digits=12,
                )),
                ('price', models.DecimalField(
                    decimal_places=4, default=Decimal('0.0000'), max_digits=12,
                )),
                ('debit', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'),
                    help_text='Amount debited (outflow)',
                    max_digits=15,
                )),
                ('credit', models.DecimalField(
                    decimal_places=2, default=Decimal('0.00'),
                    help_text='Amount credited (inflow)',
                    max_digits=15,
                )),
                ('currency', models.CharField(default='CAD', max_length=3)),
                ('commission', models.DecimalField(
                    decimal_places=4, default=Decimal('0.00'), max_digits=12,
                )),
                ('notes', models.TextField(blank=True, default='')),
                ('pdf_statement', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='transactions',
                    to='core.pdfstatement',
                )),
            ],
            options={
                'ordering': ['date', 'id'],
            },
            bases=(models.Model,),
        ),
    ]
