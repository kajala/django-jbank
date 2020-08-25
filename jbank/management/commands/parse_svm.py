#pylint: disable=too-many-branches,logging-format-interpolation
import logging
import os
from pprint import pprint
from django.core.files import File
from django.core.management.base import CommandParser
from django.db import transaction
from jbank.helpers import create_reference_payment_batch, get_or_create_bank_account
from jbank.files import list_dir_files
from jbank.models import ReferencePaymentBatch, ReferencePaymentBatchFile
from jbank.parsers import parse_svm_batches_from_file
from jutil.command import SafeCommand
from jutil.format import strip_media_root, is_media_full_path

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Parses bank settlement .SVM (saapuvat viitemaksut) files'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--test', action='store_true')
        parser.add_argument('--delete-old', action='store_true')
        parser.add_argument('--auto-create-accounts', action='store_true')
        parser.add_argument('--resolve-original-filenames', action='store_true')
        parser.add_argument('--tag', type=str, default='')

    def do(self, *args, **options):
        files = list_dir_files(options['path'])
        # pprint(files)
        for filename in files:
            plain_filename = os.path.basename(filename)

            if options['resolve_original_filenames']:
                found = ReferencePaymentBatchFile.objects.filter(referencepaymentbatch__name=plain_filename).first()
                if found and not found.original_filename:
                    assert isinstance(found, ReferencePaymentBatchFile)
                    found.original_filename = filename
                    found.save(update_fields=['original_filename'])
                    logger.info('Original SVM reference payment batch filename of %s resolved to %s', found, filename)

            if options['delete_old']:
                ReferencePaymentBatch.objects.filter(name=plain_filename).delete()

            if options['test']:
                batches = parse_svm_batches_from_file(filename)
                pprint(batches)
                continue

            if not ReferencePaymentBatch.objects.filter(name=plain_filename).first():
                print('Importing statement file {}'.format(filename))

                batches = parse_svm_batches_from_file(filename)
                if options['verbose']:
                    pprint(batches)

                with transaction.atomic():
                    if not ReferencePaymentBatch.objects.filter(name=plain_filename).first():
                        file = ReferencePaymentBatchFile(original_filename=filename, tag=options['tag'])
                        file.save()

                        if is_media_full_path(filename):
                            file.file.name = strip_media_root(filename)  # type: ignore
                            file.save()
                        else:
                            with open(filename, 'rb') as fp:
                                file.file.save(plain_filename, File(fp))

                        for data in batches:
                            if options['auto_create_accounts']:
                                for rec_data in data['records']:
                                    account_number = rec_data.get('account_number')
                                    if account_number:
                                        get_or_create_bank_account(account_number)

                            create_reference_payment_batch(data, name=plain_filename, file=file)  # pytype: disable=not-callable
            else:
                print('Skipping reference payment file {}'.format(filename))
