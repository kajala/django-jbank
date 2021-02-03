# Generated by Django 2.0 on 2017-12-26 10:13

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("jbank", "0009_auto_20171107_1847"),
    ]

    operations = [
        migrations.AlterField(
            model_name="statement",
            name="account",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="jacc.Account"),
        ),
    ]
