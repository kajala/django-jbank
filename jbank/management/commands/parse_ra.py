import logging
from pprint import pprint
from django.core.management.base import CommandParser
from jbank.services import create_account_balance
from jbank.parsers import parse_samlink_real_time_statement
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses Samlink RA file type"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("filename", type=str)
        parser.add_argument("--bic", type=str, default="")
        parser.add_argument("--pprint", action="store_true")
        parser.add_argument("--store", action="store_true")

    def do(self, *args, **kwargs):
        filename = kwargs["filename"]
        with open(filename, "rt", encoding="ISO-8859-1") as fp:
            content = fp.read()
            res = parse_samlink_real_time_statement(content)
            if kwargs["store"]:
                logger.info("%s created", create_account_balance(bic=kwargs["bic"], **res))
            pprint(res)
