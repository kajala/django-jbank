# Generated by Django 2.2.3 on 2019-08-15 20:59

from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('jbank', '0033_auto_20190801_2121'),
    ]

    operations = [
        migrations.CreateModel(
            name='CurrencyExchangeSource',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=64, verbose_name='name')),
                ('created', models.DateTimeField(blank=True, db_index=True, default=django.utils.timezone.now, editable=False, verbose_name='created')),
            ],
            options={
                'verbose_name': 'currency exchange source',
                'verbose_name_plural': 'currency exchange sources',
            },
        ),
        migrations.AlterModelOptions(
            name='currencyexchange',
            options={'verbose_name': 'currency exchange', 'verbose_name_plural': 'currency exchanges'},
        ),
        migrations.AddField(
            model_name='currencyexchange',
            name='source',
            field=models.ForeignKey(blank=True, default=None, null=True, on_delete=django.db.models.deletion.PROTECT, to='jbank.CurrencyExchangeSource', verbose_name='currency exchange source'),
        ),
    ]