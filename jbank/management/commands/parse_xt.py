# pylint: disable=too-many-branches
import logging
import os
from pprint import pprint
from django.core.management.base import CommandParser
from django.db import transaction
from jbank.camt import (
    camt053_get_iban,
    camt053_create_statement,
    camt053_parse_statement_from_file,
    CAMT053_STATEMENT_SUFFIXES,
)
from jbank.helpers import get_or_create_bank_account, save_or_store_media
from jbank.files import list_dir_files
from jbank.models import Statement, StatementFile
from jbank.parsers import parse_filename_suffix
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses XML bank account statement (camt.053.001.02) files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--delete-old", action="store_true")
        parser.add_argument("--auto-create-accounts", action="store_true")
        parser.add_argument("--suffix", type=str)
        parser.add_argument("--resolve-original-filenames", action="store_true")
        parser.add_argument("--tag", type=str, default="")

    def do(self, *args, **options):
        files = list_dir_files(options["path"], options["suffix"])
        for filename in files:  # pylint: disable=too-many-nested-blocks
            plain_filename = os.path.basename(filename)

            if parse_filename_suffix(plain_filename).upper() not in CAMT053_STATEMENT_SUFFIXES:
                print("Ignoring non-CAMT53 file {}".format(filename))
                continue

            if options["resolve_original_filenames"]:
                found = StatementFile.objects.filter(statement__name=plain_filename).first()
                if found and not found.original_filename:
                    assert isinstance(found, StatementFile)
                    found.original_filename = filename
                    found.save(update_fields=["original_filename"])
                    logger.info("Original XML statement filename of %s resolved to %s", found, filename)

            if options["delete_old"]:
                Statement.objects.filter(name=plain_filename).delete()

            if options["test"]:
                statement = camt053_parse_statement_from_file(filename)
                pprint(statement)
                continue

            if not Statement.objects.filter(name=plain_filename).first():
                print("Importing statement file {}".format(plain_filename))

                statement = camt053_parse_statement_from_file(filename)
                if options["verbose"]:
                    pprint(statement)

                with transaction.atomic():
                    file = StatementFile(original_filename=filename, tag=options["tag"])
                    file.save()
                    save_or_store_media(file.file, filename)
                    file.save()

                    for data in [statement]:
                        if options["auto_create_accounts"]:
                            account_number = camt053_get_iban(data)
                            if account_number:
                                get_or_create_bank_account(account_number)

                        camt053_create_statement(data, name=plain_filename, file=file)  # pytype: disable=not-callable
            else:
                print("Skipping statement file {}".format(filename))
