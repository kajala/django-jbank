# Generated by Django 4.0.6 on 2023-02-24 08:58

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0020_accountbalance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="accountbalance",
            name="balance",
            field=models.DecimalField(decimal_places=2, max_digits=10, verbose_name="balance"),
        ),
    ]
