import logging
import os
from pprint import pprint
from django.core.management.base import CommandParser
from jbank.aeb43 import AEB43_STATEMENT_SUFFIXES, parse_aeb43_statements_from_file
from jbank.files import list_dir_files
from jbank.parsers import parse_filename_suffix
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses bank statement .AEB43 files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--test", action="store_true")

    def do(self, *args, **kwargs):
        files = list_dir_files(kwargs["path"])
        for filename in files:
            plain_filename = os.path.basename(filename)

            if parse_filename_suffix(plain_filename).upper() not in AEB43_STATEMENT_SUFFIXES:
                print("Ignoring non-AEB43 file {}".format(filename))
                continue

            batches = parse_aeb43_statements_from_file(filename)
            pprint(batches)
