# Generated by Django 3.1 on 2020-09-02 13:54

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0002_auto_20200817_2217"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="refund",
            options={"verbose_name": "incoming.payment.refund", "verbose_name_plural": "incoming.payment.refunds"},
        ),
    ]
