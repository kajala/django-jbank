import logging
from pprint import pprint
from django.core.management.base import CommandParser
from jbank.parsers import parse_nordea_balance_query
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses Nordea SALDO file type"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("filename", type=str)
        parser.add_argument("--pprint", action="store_true")

    def do(self, *args, **kwargs):
        filename = kwargs["filename"]
        with open(filename, "rt", encoding="ISO-8859-1") as fp:
            content = fp.read()
            res = parse_nordea_balance_query(content)
            pprint(res)
