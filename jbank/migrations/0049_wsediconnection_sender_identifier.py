# Generated by Django 2.2.3 on 2019-11-29 01:24

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0048_wsediconnection_soap_endpoint"),
    ]

    operations = [
        migrations.AddField(
            model_name="wsediconnection",
            name="sender_identifier",
            field=models.CharField(default="", max_length=32, verbose_name="sender identifier"),
            preserve_default=False,
        ),
    ]
