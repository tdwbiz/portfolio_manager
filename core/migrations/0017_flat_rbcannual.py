"""Migration 0017 — drop old 6-table RbcAnnual* hierarchy, create flat RbcAnnual.

Drops (from migration 0015):
  core_rbcannualcompensation
  core_rbcannualcharges
  core_rbcannualperformancereturn
  core_rbcannualperformance
  core_rbcannualbalances
  core_rbcannualreport

Creates:
  core_rbcannual  (one row per account × report_year × currency)
"""

from django.db import migrations, models
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_rename_pdf_to_rbcmonthly'),
    ]

    operations = [
        # --- Drop old normalised tables (children first) ---
        migrations.DeleteModel(name='RbcAnnualCompensation'),
        migrations.DeleteModel(name='RbcAnnualCharges'),
        migrations.DeleteModel(name='RbcAnnualPerformanceReturn'),
        migrations.DeleteModel(name='RbcAnnualPerformance'),
        migrations.DeleteModel(name='RbcAnnualBalances'),
        migrations.DeleteModel(name='RbcAnnualReport'),

        # --- Create flat RbcAnnual ---
        migrations.CreateModel(
            name='RbcAnnual',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('account', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='rbc_annual_records',
                    to='core.account',
                )),
                ('file_name', models.CharField(max_length=255)),
                ('report_year', models.IntegerField(help_text='Calendar year this report covers, e.g. 2017')),
                ('report_period_start', models.DateField(help_text='First day of period, e.g. 2017-01-01')),
                ('report_period_end', models.DateField(help_text='Last day of period, e.g. 2017-12-31')),
                ('account_number', models.CharField(blank=True, default='', max_length=50,
                                                    help_text='RBC account number from PDF, e.g. 900-00005')),
                ('account_type_code', models.CharField(blank=True, default='', max_length=10,
                                                       help_text='RRSP, RRIF, RIF, LIF, LIRA, TFSA, RESP')),
                ('currency', models.CharField(
                    choices=[('CAD', 'CAD'), ('USD', 'USD')], default='CAD', max_length=3,
                    help_text='Currency section this row describes')),
                ('parsed_at', models.DateTimeField(auto_now_add=True)),
                # Balances
                ('bal_beginning_value', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15,
                                                            help_text='Market value at start of period')),
                ('bal_deposits_in', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15,
                                                        help_text='Total deposits and transfers-in')),
                ('bal_withdrawals_out', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15,
                                                            help_text='Total withdrawals and transfers-out')),
                ('bal_net_return', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15,
                                                       help_text='Net investment return for the period')),
                ('bal_ending_value', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15,
                                                         help_text='Market value at end of period')),
                ('bal_amount_invested', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=15,
                                                            help_text='Beginning value + deposits - withdrawals')),
                # Performance
                ('perf_since_date', models.DateField(blank=True, null=True,
                                                     help_text='Reference date for since-inception return')),
                ('perf_1yr', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True,
                                                 help_text='Past 1 year return %')),
                ('perf_3yr', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True,
                                                 help_text='Past 3 year annualised return %')),
                ('perf_5yr', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True,
                                                 help_text='Past 5 year annualised return %')),
                ('perf_10yr', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True,
                                                  help_text='Past 10 year annualised return %')),
                ('perf_since', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True,
                                                   help_text='Return since perf_since_date %')),
                # Charges
                ('chg_operating', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                      help_text='Operating charges')),
                ('chg_commissions', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                        help_text='Commissions — Equities/options')),
                ('chg_total_transaction', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                              help_text='Total transaction charges subtotal')),
                ('chg_total_paid', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                       help_text='Total paid by you')),
                # Compensation
                ('comp_trailing', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                      help_text='Trailing commissions received by RBC')),
                ('comp_other', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                   help_text='Other third-party compensation')),
                ('comp_total', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12,
                                                   help_text='Total compensation from third parties')),
            ],
            options={
                'ordering': ['-report_year', 'account', 'currency'],
                'unique_together': {('account', 'report_year', 'currency')},
            },
        ),
    ]
