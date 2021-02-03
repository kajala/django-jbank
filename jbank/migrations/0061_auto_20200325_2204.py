# Generated by Django 3.0.4 on 2020-03-25 22:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jbank", "0060_auto_20200325_1906"),
    ]

    operations = [
        migrations.AddField(
            model_name="referencepaymentbatchfile",
            name="tag",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="tag"),
        ),
        migrations.AddField(
            model_name="statementfile",
            name="tag",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64, verbose_name="tag"),
        ),
    ]
