import logging
import os
import subprocess
import traceback
from decimal import Decimal
from os.path import join
from django.conf import settings
from django.core.management import CommandParser
from django.db import transaction
from jbank.models import Payout, PAYOUT_ERROR, PAYOUT_WAITING_PROCESSING, PayoutStatus, PAYOUT_WAITING_UPLOAD
from jbank.sepa import Pain001, PAIN001_REMITTANCE_INFO_MSG, PAIN001_REMITTANCE_INFO_OCR_ISO, \
    PAIN001_REMITTANCE_INFO_OCR
from jutil.command import SafeCommand
from jutil.format import format_xml
from jutil.validators import iban_bic


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Generates pain.001.001.03 compatible SEPA payment files from pending Payout objects.
        By default generates files of Payouts in WAITING_PROCESSING state.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('dir', type=str)
        parser.add_argument('--payout', type=int)
        parser.add_argument('--verbose', action='store_true')

    def do(self, *args, **options):
        if options['verbose']:
            logger.info('Writing pain.001 files to {}'.format(options['dir']))

        payouts = Payout.objects.all()
        if options['payout']:
            payouts = Payout.objects.filter(id=options['payout'])
        else:
            payouts = payouts.filter(state=PAYOUT_WAITING_PROCESSING)

        for p in list(payouts):
            assert isinstance(p, Payout)
            try:
                if options['verbose']:
                    print(p)

                if not p.msg_id or not p.file_name:
                    p.generate_msg_id()
                    p.file_name = p.msg_id + '.XL'
                    p.save(update_fields=['msg_id', 'file_name'])

                pain001 = Pain001(p.msg_id, p.payer.name, p.payer.account_number, p.payer.bic, p.payer.org_id, p.payer.address_lines, p.payer.country_code)
                if p.messages:
                    remittance_info = p.messages
                    remittance_info_type = PAIN001_REMITTANCE_INFO_MSG
                else:
                    remittance_info = p.reference
                    remittance_info_type = PAIN001_REMITTANCE_INFO_OCR_ISO if remittance_info[:2] == 'RF' else PAIN001_REMITTANCE_INFO_OCR
                pain001.add_payment(p.id, p.recipient.name, p.recipient.account_number, p.recipient.bic, p.amount, remittance_info, remittance_info_type, p.due_date)

                p.full_path = full_path = os.path.join(options['dir'], p.file_name)
                if options['verbose']:
                    print(p, 'written to', full_path)
                pain001.render_to_file(full_path)
                logger.info('{} generated'.format(full_path))
                p.state = PAYOUT_WAITING_UPLOAD
                p.save(update_fields=['full_path', 'state'])

                PayoutStatus.objects.create(payout=p, file_name=p.file_name, msg_id=p.msg_id, status_reason="File generation OK")
            except Exception as e:
                short_err = 'File generation failed: ' + str(e)
                long_err = "File generation failed ({}): ".format(p.file_name) + traceback.format_exc()
                logger.error(long_err)
                p.state = PAYOUT_ERROR
                p.save(update_fields=['state'])
                PayoutStatus.objects.create(payout=p, file_name=p.file_name, msg_id=p.msg_id, status_reason=short_err[:255])
