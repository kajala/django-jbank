# Generated by Django 2.2.8 on 2019-12-20 06:18

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jbank', '0056_wsediconnection_debug_commands'),
    ]

    operations = [
        migrations.AddField(
            model_name='wsediconnection',
            name='enabled',
            field=models.BooleanField(blank=True, default=True, verbose_name='enabled'),
        ),
    ]