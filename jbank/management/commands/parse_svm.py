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
from jbank.models import Statement, ReferencePaymentBatch, ReferencePaymentBatchFile
from jbank.parsers import parse_svm_batches_from_file
from jutil.command import SafeCommand
from django.utils.translation import ugettext as _


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Parses bank settlement .SVM (saapuvat viitemaksut) files'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--test', action='store_true')
        parser.add_argument('--delete-old', action='store_true')
        parser.add_argument('--auto-create-accounts', action='store_true')

    def do(self, *args, **options):
        files = list_dir_files(options['path'])
        # pprint(files)
        for filename in files:
            plain_filename = os.path.basename(filename)
            if options['delete_old']:
                ReferencePaymentBatch.objects.filter(name=plain_filename).delete()

            if options['test']:
                batches = parse_svm_batches_from_file(filename)
                pprint(batches)
                continue

            if not ReferencePaymentBatch.objects.filter(name=plain_filename).first():
                logger.info('Importing reference payment batch file {}'.format(plain_filename))

                batches = parse_svm_batches_from_file(filename)
                if options['verbose']:
                    pprint(batches)

                with transaction.atomic():
                    if not ReferencePaymentBatch.objects.filter(name=plain_filename).first():
                        file = ReferencePaymentBatchFile()
                        file.save()
                        with open(filename, 'rb') as fp:
                            file.file.save(plain_filename, File(fp))

                        for data in batches:
                            if options['auto_create_accounts']:
                                for rec_data in data['records']:
                                    account_number = rec_data.get('account_number')
                                    if account_number:
                                        get_or_create_bank_account(account_number)

                            create_reference_payment_batch(data, name=plain_filename, file=file)
            else:
                print('Skipping reference payment batch file {}'.format(filename))
