# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2017-11-02 03:38
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jacc", "0006_account_name"),
        ("jbank", "0002_auto_20171031_0356"),
    ]

    operations = [
        migrations.AddField(
            model_name="statement",
            name="account",
            field=models.ForeignKey(default=None, on_delete=django.db.models.deletion.CASCADE, related_name="+", to="jacc.Account"),
            preserve_default=False,
        ),
    ]
