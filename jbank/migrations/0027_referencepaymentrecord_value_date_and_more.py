# Generated by Django 4.1.7 on 2023-03-27 04:39

from django.db import migrations, models
import jutil.modelfields


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0026_referencepaymentbatch_identifier_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="referencepaymentrecord",
            name="value_date",
            field=models.DateField(blank=True, db_index=True, default=None, null=True, verbose_name="value date"),
        ),
        migrations.AlterField(
            model_name="referencepaymentrecord",
            name="record_type",
            field=jutil.modelfields.SafeCharField(blank=True, default="", max_length=4, verbose_name="record type"),
        ),
    ]
