# Generated by Django 3.2.11 on 2022-03-02 04:57

from django.db import migrations
import jutil.modelfields


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0016_alter_payoutstatus_timestamp"),
    ]

    operations = [
        migrations.AlterField(
            model_name="statementrecord",
            name="name",
            field=jutil.modelfields.SafeCharField(blank=True, db_index=True, max_length=128, verbose_name="name"),
        ),
    ]
