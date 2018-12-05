import logging
import os
from copy import copy
from pathlib import Path
from pprint import pprint
from django.conf import settings
from django.core.files import File
from django.core.management import CommandParser
from django.db import transaction
from django.utils import translation
from jacc.models import Account, AccountType
from jbank.helpers import create_statement, create_reference_payment_batch, get_or_create_bank_account
from jbank.files import list_dir_files
from jbank.models import Statement, ReferencePaymentBatch, ReferencePaymentBatchFile, ReferencePaymentRecord
from jbank.parsers import parse_svm_batches_from_file
from jutil.command import SafeCommand
from django.utils.translation import ugettext as _


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Re-parses old bank settlement .SVM (saapuvat viitemaksut) files. Used for adding missing fields.'

    def add_arguments(self, parser: CommandParser):
        pass

    def do(self, *args, **options):
        logger.info('Re-parsing SVM files to update fields')
        for file in ReferencePaymentBatchFile.objects.all().order_by('id'):
            assert isinstance(file, ReferencePaymentBatchFile)
            logger.info('Processing {} BEGIN'.format(file))
            batches = parse_svm_batches_from_file(file.full_path)
            for batch in batches:
                for e in batch['records']:
                    # check missing line_number
                    e2 = ReferencePaymentRecord.objects.filter(batch__file=file, line_number=0, record_type=e['record_type'], account_number=e['account_number'],
                                                               paid_date=e['paid_date'], archive_identifier=e['archive_identifier'],
                                                               remittance_info=e['remittance_info'], payer_name=e['payer_name'],
                                                               currency_identifier=e['currency_identifier'], name_source=e['name_source'],
                                                               correction_identifier=e['correction_identifier'],
                                                               delivery_method=e['delivery_method'], receipt_code=e['receipt_code']).first()
                    if e2:
                        e2.line_number = e['line_number']
                        e2.save()
                        logger.info('Updated {} line number to {}'.format(e2, e2.line_number))
            logger.info('Processing {} END'.format(file))
