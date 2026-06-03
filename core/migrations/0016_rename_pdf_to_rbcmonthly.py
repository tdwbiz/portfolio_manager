"""Migration 0016 — rename Pdf* tables to RbcMonthly* and add total_market_value.

Renames:
  core_pdfstatement        → core_rbcmonthly
  core_pdfaccountbalance   → core_rbcmonthlybalance
  core_pdftransaction      → core_rbcmonthlytransaction

Also:
  Renames FKs and indexes to match new model names.
  Adds RbcMonthly.total_market_value (for cross-check vs annual).
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_rbcannualreport_rbcannualperformance_and_more'),
    ]

    operations = [
        # --- Rename tables ---
        migrations.RenameModel(
            old_name='PdfStatement',
            new_name='RbcMonthly',
        ),
        migrations.RenameModel(
            old_name='PdfAccountBalance',
            new_name='RbcMonthlyBalance',
        ),
        migrations.RenameModel(
            old_name='PdfTransaction',
            new_name='RbcMonthlyTransaction',
        ),

        # --- Drop index before renaming FK field (SQLite cannot handle index on renamed field) ---
        migrations.RemoveIndex(
            model_name='RbcMonthlyTransaction',
            name='core_pdftra_pdf_sta_2117c5_idx',
        ),

        # --- Rename FK fields on child tables ---
        migrations.RenameField(
            model_name='RbcMonthlyBalance',
            old_name='pdf_statement',
            new_name='rbc_monthly',
        ),
        migrations.RenameField(
            model_name='RbcMonthlyTransaction',
            old_name='pdf_statement',
            new_name='rbc_monthly',
        ),

        # --- Recreate index with new FK name ---
        migrations.AddIndex(
            model_name='RbcMonthlyTransaction',
            index=models.Index(
                fields=['rbc_monthly', 'date'], name='core_rbcmon_transact_rbc_mon_4f3a1e_idx'
            ),
        ),

        # --- Rename related_name on Account FK ---
        migrations.AlterField(
            model_name='rbcmonthly',
            name='account',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='rbc_monthly_statements',
                to='core.account',
            ),
        ),

        # --- Add total_market_value to RbcMonthly ---
        migrations.AddField(
            model_name='rbcmonthly',
            name='total_market_value',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Total portfolio market value from statement footer (cross-check vs annual)',
                max_digits=15,
            ),
        ),

        # --- Fix index on RbcMonthlyTransaction to use new FK name ---
        migrations.AlterField(
            model_name='rbcmonthlytransaction',
            name='rbc_monthly',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='transactions',
                to='core.rbcmonthly',
            ),
        ),

        # --- Fix FK on RbcMonthlyBalance ---
        migrations.AlterField(
            model_name='rbcmonthlybalance',
            name='rbc_monthly',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='balances',
                to='core.rbcmonthly',
            ),
        ),
    ]
