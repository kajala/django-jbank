# Generated by Django 2.2.3 on 2019-11-29 20:18

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0049_wsediconnection_sender_identifier"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="wsedisoapcall",
            name="payout",
        ),
    ]
