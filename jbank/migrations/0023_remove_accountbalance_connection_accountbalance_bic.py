# Generated by Django 4.0.6 on 2023-02-25 02:44

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0022_accountbalance_connection"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="accountbalance",
            name="connection",
        ),
        migrations.AddField(
            model_name="accountbalance",
            name="bic",
            field=models.CharField(db_index=True, default="", max_length=16, verbose_name="BIC"),
            preserve_default=False,
        ),
    ]
