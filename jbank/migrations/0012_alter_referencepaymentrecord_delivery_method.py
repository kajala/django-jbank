# Generated by Django 3.2.3 on 2021-05-28 00:35

from django.db import migrations
import jutil.modelfields


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0011_referencepaymentbatch_cached_total_amount"),
    ]

    operations = [
        migrations.AlterField(
            model_name="referencepaymentrecord",
            name="delivery_method",
            field=jutil.modelfields.SafeCharField(
                blank=True,
                choices=[("", ""), ("A", "From Customer"), ("K", "From Bank Clerk"), ("J", "From Bank System")],
                db_index=True,
                max_length=1,
                verbose_name="delivery method",
            ),
        ),
    ]
