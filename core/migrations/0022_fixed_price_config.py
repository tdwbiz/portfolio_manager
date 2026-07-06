from django.db import migrations, models


def _seed_fixed_prices(apps, schema_editor):
    """Seed RBF2010 (CAD 10) and RBF2014 (USD 10)."""
    FixedPriceConfig = apps.get_model("core", "FixedPriceConfig")

    FixedPriceConfig.objects.get_or_create(
        symbol="RBF2010",
        defaults={
            "price_cad": 10.00,
            "price_usd": 0,
            "currency": "CAD",
        },
    )
    FixedPriceConfig.objects.get_or_create(
        symbol="RBF2014",
        defaults={
            "price_cad": 0,
            "price_usd": 10.00,
            "currency": "USD",
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_dividendschedule_alter_transaction_tx_hash_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="FixedPriceConfig",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("symbol", models.CharField(max_length=50, unique=True)),
                (
                    "price_cad",
                    models.DecimalField(decimal_places=6, default=0, max_digits=12),
                ),
                (
                    "price_usd",
                    models.DecimalField(decimal_places=6, default=0, max_digits=12),
                ),
                ("currency", models.CharField(default="CAD", max_length=3)),
                ("notes", models.TextField(blank=True, default="")),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "db_table": "core_fixed_price_config",
                "ordering": ["symbol"],
            },
        ),
        migrations.RunPython(
            code=_seed_fixed_prices,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
