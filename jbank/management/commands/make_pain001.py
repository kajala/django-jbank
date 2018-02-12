import logging
import os
import subprocess
import traceback
from decimal import Decimal
from os.path import join
from django.conf import settings
from django.core.management import CommandParser
from django.db import transaction
from jbank.models import Payout, PAYOUT_ERROR, PAYOUT_WAITING, PayoutStatus
from jbank.sepa import Pain001
from jutil.command import SafeCommand
from jutil.format import format_xml
from jutil.validators import iban_bic


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Generates pain.001.001.03 compatible SEPA payment files from pending Payout objects.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('dir', type=str)
        parser.add_argument('--payout', type=int)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--new-msg-id', action='store_true')

    def do(self, *args, **options):
        if options['verbose']:
            logger.info('Writing pain.001 files to {}'.format(options['dir']))

        payouts = Payout.objects.filter(file_name='', state=PAYOUT_WAITING)
        if options['payout']:
            payouts = Payout.objects.filter(id=options['payout'])

        for p in payouts:
            assert isinstance(p, Payout)
            print(p)
            if not p.msg_id or options['new_msg_id']:
                p.generate_msg_id()
            p.file_name = p.msg_id + '.XL'

            try:
                pain001 = Pain001(p.msg_id, p.payer.name, p.payer.account_number, p.payer.bic, p.payer.org_id, p.payer.address_lines, p.payer.country_code)
                pain001.add_payment(p.id, p.recipient.name, p.recipient.account_number, p.recipient.bic, p.amount, p.messages, p.due_date)

                full_path = os.path.join(options['dir'], p.file_name)
                pain001.render_to_file(full_path)
                logger.info('{} generated'.format(full_path))

                PayoutStatus.objects.create(payout=p, file_name=p.file_name, msg_id=p.msg_id, status_reason="File generation OK")
            except Exception as e:
                short_err = 'File generation failed: ' + str(e)
                long_err = "File generation failed ({}): ".format(p.file_name) + traceback.format_exc()
                logger.error(long_err)
                p.state = PAYOUT_ERROR
                PayoutStatus.objects.create(payout=p, file_name=p.file_name, msg_id=p.msg_id, status_reason=short_err[:255])

            p.save()
