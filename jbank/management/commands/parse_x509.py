import logging
from pprint import pprint
from django.core.management.base import CommandParser
from jbank.x509_helpers import get_x509_cert_from_file
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Parses x509 cert"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("pem", type=str)

    def do(self, *args, **options):
        cert = get_x509_cert_from_file(options["pem"])
        pprint(cert)
