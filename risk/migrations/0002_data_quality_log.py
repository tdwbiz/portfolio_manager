# Generated migration for DataQualityLog model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("risk", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DataQualityLog",
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
                ("logged_at", models.DateTimeField(auto_now_add=True)),
                ("symbol", models.CharField(blank=True, max_length=20)),
                ("event_type", models.CharField(max_length=30)),
                ("severity", models.CharField(max_length=10)),
                ("description", models.TextField()),
                ("start_date", models.DateField(blank=True, null=True)),
                ("end_date", models.DateField(blank=True, null=True)),
                ("gap_days", models.IntegerField(blank=True, null=True)),
                ("resolved", models.BooleanField(default=False)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "rm_data_quality_log",
            },
        ),
        migrations.AddIndex(
            model_name="dataqualitylog",
            index=models.Index(
                fields=["symbol", "logged_at"],
                name="rm_data_quality_log_symbol_logged_at_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="dataqualitylog",
            index=models.Index(
                fields=["event_type", "severity"],
                name="rm_data_quality_log_event_type_severity_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="dataqualitylog",
            index=models.Index(
                fields=["resolved"],
                name="rm_data_quality_log_resolved_idx",
            ),
        ),
    ]
