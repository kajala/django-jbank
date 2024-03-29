# Generated by Django 2.2.3 on 2019-11-27 01:30

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0035_auto_20190815_2137"),
    ]

    operations = [
        migrations.CreateModel(
            name="WsEdiConnection",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_id", models.CharField(max_length=64, verbose_name="customer id")),
                ("signing_cert", models.FileField(blank=True, upload_to="", verbose_name="signing cert")),
                (
                    "created",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        verbose_name="created",
                    ),
                ),
            ],
            options={
                "verbose_name": "WS-EDI connection",
                "verbose_name_plural": "WS-EDI connections",
            },
        ),
    ]
