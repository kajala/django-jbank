# Generated by Django 4.1.7 on 2023-02-24 08:50

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0019_alter_payout_state"),
    ]

    operations = [
        migrations.CreateModel(
            name="AccountBalance",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account_number", models.CharField(db_index=True, max_length=32, verbose_name="account number")),
                ("record_datetime", models.DateTimeField(db_index=True, verbose_name="record date")),
                ("balance", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="available balance")),
                ("available_balance", models.DecimalField(decimal_places=2, max_digits=10, verbose_name="available balance")),
                ("credit_limit", models.DecimalField(blank=True, decimal_places=2, default=None, max_digits=10, null=True, verbose_name="credit limit")),
                ("currency", models.CharField(db_index=True, default="EUR", max_length=3, verbose_name="currency")),
                ("created", models.DateTimeField(blank=True, db_index=True, default=django.utils.timezone.now, editable=False, verbose_name="created")),
            ],
            options={
                "verbose_name": "account balance",
                "verbose_name_plural": "account balances",
            },
        ),
    ]