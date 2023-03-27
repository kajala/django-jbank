# -*- coding: utf-8 -*-
# Generated by Django 1.11 on 2017-11-02 23:51
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0006_auto_20171102_2341"),
    ]

    operations = [
        migrations.AddField(
            model_name="statement",
            name="statement_file",
            field=models.ForeignKey(
                blank=True,
                default=None,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="jbank.StatementFile",
            ),
        ),
        migrations.AlterField(
            model_name="statementfile",
            name="errors",
            field=models.TextField(blank=True, default="", max_length=4086, verbose_name="errors"),
        ),
    ]
