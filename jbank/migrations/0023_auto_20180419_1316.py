# Generated by Django 2.0.2 on 2018-04-19 13:16

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0022_auto_20180411_1814"),
    ]

    operations = [
        migrations.AlterField(
            model_name="payout",
            name="messages",
            field=models.TextField(verbose_name="recipient messages"),
        ),
    ]
