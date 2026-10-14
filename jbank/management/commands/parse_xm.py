import logging
import os
from pprint import pformat
from django.core.management.base import CommandParser
from jbank.camt import CAMT054_FILE_SUFFIXES, camt054_parse_file, camt054_create_reference_payment_batch, camt054_parse_ntfctn_acct
from jbank.files import list_dir_files
from jbank.helpers import save_or_store_media, get_or_create_bank_account
from jbank.models import ReferencePaymentBatch, ReferencePaymentBatchFile
from jbank.parsers import parse_filename_suffix
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses camt.054.001.02 files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--delete-old", action="store_true")
        parser.add_argument("--auto-create-accounts", action="store_true")
        parser.add_argument("--tag", type=str, default="")

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        verbose = options["verbose"]
        files = list_dir_files(options["path"])
        for filename in files:
            plain_filename = os.path.basename(filename)

            if parse_filename_suffix(plain_filename).upper() not in CAMT054_FILE_SUFFIXES:
                print("Ignoring non-camt.054 file {}".format(filename))
                continue

            if options["delete_old"]:
                ReferencePaymentBatch.objects.filter(name=plain_filename).delete()

            if options["test"]:
                camt054_data = camt054_parse_file(filename)
                logger.debug("%s", pformat(camt054_data))
                continue

            if ReferencePaymentBatch.objects.filter(name=plain_filename).exists():
                if verbose:
                    logger.info("Skipping existing camt.054 since name %s", plain_filename)
                continue
            if ReferencePaymentBatchFile.objects.filter(original_filename=filename).exists():
                if verbose:
                    logger.info("Skipping existing camt.054 since original filename %s", filename)
                continue

            logger.info("Importing camt.054 %s", filename)
            camt054_data = camt054_parse_file(filename)
            if options["verbose"]:
                logger.debug("%s", pformat(camt054_data))

            file = ReferencePaymentBatchFile(original_filename=filename, tag=options["tag"])
            file.save()
            save_or_store_media(file.file, filename)
            file.save()

            for ntfctn in camt054_data["BkToCstmrDbtCdtNtfctn"]["Ntfctn"]:
                if options["auto_create_accounts"]:
                    account_number, currency = camt054_parse_ntfctn_acct(ntfctn)
                    if account_number:
                        get_or_create_bank_account(account_number, currency)

                camt054_create_reference_payment_batch(ntfctn, name=plain_filename, file=file)

            logger.info("Marking camt.054 %s processed", file)
            file.mark_processed()
