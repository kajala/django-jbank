# Generated by Django 4.1.7 on 2023-08-26 23:48

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jbank", "0035_wsediconnection_agreement_identifier_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="referencepaymentrecord",
            old_name="manually_settled",
            new_name="marked_reconciled",
        ),
        migrations.RenameField(
            model_name="statementrecord",
            old_name="manually_settled",
            new_name="marked_reconciled",
        ),
    ]
