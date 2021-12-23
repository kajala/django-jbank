# Generated by Django 3.2.9 on 2021-12-02 16:32

from django.db import migrations
from django.db.models import F


def migr0015(apps, schema):
    PayoutStatus = apps.get_model("jbank", "PayoutStatus")
    res = PayoutStatus.objects.all().update(timestamp=F("created"))
    print(res, "PayoutStatus objects updated to include timestamp")


class Migration(migrations.Migration):

    dependencies = [
        ("jbank", "0014_payoutstatus_timestamp"),
    ]

    operations = [migrations.RunPython(migr0015)]