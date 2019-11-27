import logging
from decimal import Decimal

from django.conf import settings
from django.core.management import CommandParser
from jacc.models import AccountType, Account
from jutil.command import SafeCommand
from jbank.helpers import make_msg_id
from jbank.models import Payout


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Makes test payment'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('--account-id', type=int)
        parser.add_argument('--auto-create-account', action='store_true')
        parser.add_argument('--payer-id', type=int, default=1)
        parser.add_argument('--recipient-id', type=int, default=2)
        parser.add_argument('--messages', type=str, default='test payment')
        parser.add_argument('--amount', type=Decimal, default=Decimal('1.23'))

    def do(self, *args, **options):
        acc = None
        if options['account_id']:
            acc = Account.objects.get(id=options['account_id'])
        if not acc and (settings.DEBUG or options['auto_create_account']):
            print('Auto-creating account type and account')
            acc_type, created = AccountType.objects.get_or_create(code=settings.ACCOUNT_BANK_ACCOUNT, name='pankkitili', is_asset=True)
            acc, created = Account.objects.get_or_create(type=acc_type, name='pankkitili')
        if not acc:
            return print('Define account with --account-id or use --auto-create-account')

        p = Payout(account=acc, payer_id=options['payer_id'], recipient_id=options['recipient_id'], messages=options['messages'], msg_id=make_msg_id(), amount=options['amount'])
        p.full_clean()
        p.save()
        print('{} created'.format(p))
