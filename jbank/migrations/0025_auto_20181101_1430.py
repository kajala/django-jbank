# Generated by Django 2.1.2 on 2018-11-01 14:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jbank", "0024_auto_20180425_1704"),
    ]

    operations = [
        migrations.AddField(
            model_name="payout",
            name="reference",
            field=models.CharField(blank=True, default="", max_length=32, verbose_name="recipient reference"),
        ),
        migrations.AlterField(
            model_name="payout",
            name="messages",
            field=models.TextField(blank=True, default="", verbose_name="recipient messages"),
        ),
    ]
