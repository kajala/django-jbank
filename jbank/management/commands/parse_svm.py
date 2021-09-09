import logging
import os
from pprint import pprint
from django.core.management.base import CommandParser
from django.db import transaction
from jbank.helpers import create_reference_payment_batch, get_or_create_bank_account, save_or_store_media
from jbank.files import list_dir_files
from jbank.models import ReferencePaymentBatch, ReferencePaymentBatchFile
from jbank.parsers import parse_filename_suffix
from jbank.svm import parse_svm_batches_from_file, SVM_STATEMENT_SUFFIXES
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses bank settlement .SVM (saapuvat viitemaksut) files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--delete-old", action="store_true")
        parser.add_argument("--auto-create-accounts", action="store_true")
        parser.add_argument("--resolve-original-filenames", action="store_true")
        parser.add_argument("--tag", type=str, default="")

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        files = list_dir_files(options["path"])
        # pprint(files)
        for filename in files:
            plain_filename = os.path.basename(filename)

            if parse_filename_suffix(plain_filename).upper() not in SVM_STATEMENT_SUFFIXES:
                print("Ignoring non-SVM file {}".format(filename))
                continue

            if options["resolve_original_filenames"]:
                found = ReferencePaymentBatchFile.objects.filter(referencepaymentbatch__name=plain_filename).first()
                if found and not found.original_filename:
                    assert isinstance(found, ReferencePaymentBatchFile)
                    found.original_filename = filename
                    found.save(update_fields=["original_filename"])
                    logger.info("Original SVM reference payment batch filename of %s resolved to %s", found, filename)

            if options["delete_old"]:
                ReferencePaymentBatch.objects.filter(name=plain_filename).delete()

            if options["test"]:
                batches = parse_svm_batches_from_file(filename)
                pprint(batches)
                continue

            if not ReferencePaymentBatch.objects.filter(name=plain_filename).first():
                print("Importing statement file {}".format(filename))

                batches = parse_svm_batches_from_file(filename)
                if options["verbose"]:
                    pprint(batches)

                with transaction.atomic():
                    file = ReferencePaymentBatchFile(original_filename=filename, tag=options["tag"])
                    file.save()
                    save_or_store_media(file.file, filename)
                    file.save()

                    for data in batches:
                        if options["auto_create_accounts"]:
                            for rec_data in data["records"]:
                                account_number = rec_data.get("account_number")
                                if account_number:
                                    get_or_create_bank_account(account_number)

                        create_reference_payment_batch(data, name=plain_filename, file=file)  # pytype: disable=not-callable

                    file.get_total_amount(force=True)
            else:
                print("Skipping reference payment file {}".format(filename))
