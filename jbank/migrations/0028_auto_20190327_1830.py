# Generated by Django 2.1.7 on 2019-03-27 18:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jbank', '0027_auto_20190304_1913'),
    ]

    operations = [
        migrations.AlterField(
            model_name='referencepaymentbatch',
            name='institution_identifier',
            field=models.CharField(blank=True, max_length=2, verbose_name='institution identifier'),
        ),
    ]