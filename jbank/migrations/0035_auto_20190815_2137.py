# Generated by Django 2.2.3 on 2019-08-15 21:37

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0034_auto_20190815_2059"),
    ]

    operations = [
        migrations.AlterField(
            model_name="currencyexchange",
            name="exchange_rate",
            field=models.DecimalField(blank=True, decimal_places=6, default=None, max_digits=12, null=True, verbose_name="exchange rate"),
        ),
    ]
