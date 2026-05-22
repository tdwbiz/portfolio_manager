from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_update_branding_defaults'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalPrice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('symbol', models.CharField(max_length=20)),
                ('date', models.DateField()),
                ('close', models.DecimalField(decimal_places=6, max_digits=18)),
            ],
            options={
                'ordering': ['symbol', 'date'],
            },
        ),
        migrations.AddConstraint(
            model_name='historicalprice',
            constraint=models.UniqueConstraint(fields=['symbol', 'date'], name='unique_symbol_date'),
        ),
        migrations.AddIndex(
            model_name='historicalprice',
            index=models.Index(fields=['symbol', 'date'], name='core_histor_symbol_date_idx'),
        ),
    ]
