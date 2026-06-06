# Generated migration for M3-Spec-1: Instrument & Strategy Catalogue

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('risk', '0006_securitydriver_urgencyscore'),
    ]

    operations = [
        migrations.CreateModel(
            name='InstrumentType',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=30, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('mechanics_description', models.TextField()),
                ('use_cases_json', models.TextField()),
                ('cost_structure_json', models.TextField()),
                ('tax_treatment_json', models.TextField()),
                ('eligible_tfsa', models.BooleanField(default=True)),
                ('eligible_rrsp', models.BooleanField(default=True)),
                ('eligible_lif', models.BooleanField(default=True)),
                ('eligible_rrif', models.BooleanField(default=True)),
                ('eligible_taxable', models.BooleanField(default=True)),
                ('requires_options_availability', models.BooleanField(default=False)),
                ('wash_sale_applicable', models.BooleanField(default=False)),
                ('sort_order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'rm_instrument_type',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='StrategyTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=50, unique=True)),
                ('name', models.CharField(max_length=150)),
                ('description', models.TextField()),
                ('applicable_urgency_levels', models.CharField(max_length=50)),
                ('applicable_regime_quadrants', models.CharField(max_length=20)),
                ('applicable_asset_types', models.CharField(max_length=100)),
                ('applicable_account_categories', models.CharField(max_length=100)),
                ('min_unrealised_gain_usd', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('requires_options', models.BooleanField(default=False)),
                ('cef_eligible', models.BooleanField(default=True)),
                ('lif_rrif_eligible', models.BooleanField(default=True)),
                ('is_tax_triggering', models.BooleanField(default=False)),
                ('sort_order', models.IntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'rm_strategy_template',
                'ordering': ['sort_order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='StrategyInstrumentLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(max_length=50)),
                ('sort_order', models.IntegerField(default=0)),
                (
                    'instrument',
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name='strategy_links',
                        to='risk.instrumenttype',
                    ),
                ),
                (
                    'strategy',
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name='instrument_links',
                        to='risk.strategytemplate',
                    ),
                ),
            ],
            options={
                'db_table': 'rm_strategy_instrument_link',
                'unique_together': [('strategy', 'instrument')],
                'ordering': ['sort_order'],
            },
        ),
    ]
