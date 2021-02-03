from pprint import pprint
from django.core.management.base import CommandParser
from jbank.parsers import parse_tiliote_statements_from_file
from jutil.command import SafeCommand


class Command(SafeCommand):
    help = "Parses bank account statement .TO (tiliote) files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)

    def do(self, *args, **options):
        statements = parse_tiliote_statements_from_file(options["path"])
        pprint(statements)
