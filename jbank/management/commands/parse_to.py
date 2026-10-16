import logging
import os
from pprint import pformat
from time import perf_counter

from django.core.management.base import CommandParser
from django.utils.timezone import now
from jbank.helpers import get_or_create_bank_account, save_or_store_media
from jbank.svm import create_statement
from jbank.files import list_dir_files
from jbank.models import Statement, StatementFile
from jbank.parsers import parse_filename_suffix
from jbank.tito import parse_tiliote_statements_from_file, TO_STATEMENT_SUFFIXES
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses bank account statement .TO (tiliote) files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--delete-old", action="store_true")
        parser.add_argument("--auto-create-accounts", action="store_true")
        parser.add_argument("--resolve-original-filenames", action="store_true")
        parser.add_argument("--tag", type=str, default="")

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        verbose = options["verbose"]
        files = list_dir_files(options["path"])
        for filename in files:
            plain_filename = os.path.basename(filename)

            if parse_filename_suffix(plain_filename).upper() not in TO_STATEMENT_SUFFIXES:
                if verbose:
                    logger.info("Ignoring non-TO file %s", filename)
                continue

            if options["resolve_original_filenames"]:
                found = StatementFile.objects.filter(statement__name=plain_filename).first()
                if found and not found.original_filename:
                    assert isinstance(found, StatementFile)
                    found.original_filename = filename
                    found.save(update_fields=["original_filename"])
                    logger.info("Original TO statement filename of %s resolved to %s", found, filename)

            if options["delete_old"]:
                Statement.objects.filter(name=plain_filename).delete()

            if options["test"]:
                statements = parse_tiliote_statements_from_file(filename)
                logger.info("%s", pformat(statements))
                continue

            if Statement.objects.filter(name=plain_filename).exists():
                if verbose:
                    logger.info("Skipping existing TO since name %s", plain_filename)
                continue
            if StatementFile.objects.filter(original_filename=filename).exists():
                if verbose:
                    logger.info("Skipping existing TO since original filename %s", filename)
                continue

            logger.info("Importing statement file %s", filename)
            statements = parse_tiliote_statements_from_file(filename)
            if verbose:
                logger.debug("%s", pformat(statements))

            time_now = now()
            file = StatementFile(original_filename=filename, tag=options["tag"], created=time_now)
            file.save()
            save_or_store_media(file.file, filename)
            file.save()

            for data in statements:
                if options["auto_create_accounts"]:
                    account_number = data.get("header", {}).get("account_number")
                    if account_number:
                        get_or_create_bank_account(account_number)

                timing_start = perf_counter()
                stm = create_statement(data, name=plain_filename, file=file, created=time_now)  # pytype: disable=not-callable
                logger.info("Statement %s created in %s", stm, perf_counter() - timing_start)
