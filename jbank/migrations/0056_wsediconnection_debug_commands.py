# Generated by Django 2.2.3 on 2019-12-03 00:48

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("jbank", "0055_auto_20191130_2011"),
    ]

    operations = [
        migrations.AddField(
            model_name="wsediconnection",
            name="debug_commands",
            field=models.TextField(blank=True, verbose_name="debug commands"),
        ),
    ]
