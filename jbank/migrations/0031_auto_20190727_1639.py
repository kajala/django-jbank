# Generated by Django 2.2.3 on 2019-07-27 16:39

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jbank', '0030_auto_20190727_1633'),
    ]

    operations = [
        migrations.AlterField(
            model_name='statementrecord',
            name='record_code',
            field=models.CharField(blank=True, choices=[('700', 'Money Transfer (In/Out)'), ('701', 'Recurring Payment (In/Out)'), ('702', 'Bill Payment (Out)'), ('703', 'Payment Terminal Deposit (In)'), ('704', 'Bank Draft (In/Out)'), ('705', 'Reference Payments (In)'), ('706', 'Payment Service (Out)'), ('710', 'Deposit (In)'), ('720', 'Withdrawal (Out)'), ('721', 'Card Payment (Out)'), ('722', 'Check (Out)'), ('730', 'Bank Fees (Out)'), ('740', 'Interests Charged (Out)'), ('750', 'Interests Credited (In)'), ('760', 'Loan (Out)'), ('761', 'Loan Payment (Out)'), ('770', 'Foreign Transfer (In/Out)'), ('780', 'Zero Balancing (In/Out)'), ('781', 'Sweeping (In/Out)'), ('782', 'Topping (In/Out)')], db_index=True, max_length=4, verbose_name='record type'),
        ),
    ]
