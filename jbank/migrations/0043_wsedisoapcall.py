# Generated by Django 2.2.3 on 2019-11-28 22:25

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('jbank', '0042_wsediconnection_bank_encryption_cert_file'),
    ]

    operations = [
        migrations.CreateModel(
            name='WsEdiSoapCall',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(blank=True, db_index=True, default=django.utils.timezone.now, verbose_name='timestamp')),
                ('request_identifier', models.CharField(blank=True, db_index=True, max_length=64, unique=True, verbose_name='request identifier')),
                ('command', models.CharField(blank=True, db_index=True, max_length=64, verbose_name='command')),
                ('created', models.DateTimeField(blank=True, db_index=True, default=django.utils.timezone.now, editable=False, verbose_name='created')),
                ('executed', models.DateTimeField(blank=True, db_index=True, default=None, editable=False, null=True, verbose_name='executed')),
                ('connection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='jbank.WsEdiConnection', verbose_name='WS-EDI connection')),
                ('payout', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='jbank.Payout', verbose_name='payout')),
            ],
        ),
    ]