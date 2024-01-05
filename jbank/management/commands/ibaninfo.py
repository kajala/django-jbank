from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jutil.validators import iban_bank_info


class Command(SafeCommand):
    help = "Returns BIC from IBAN account number"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("iban", type=str)

    def do(self, *args, **kwargs):
        print(iban_bank_info(kwargs["iban"]))
