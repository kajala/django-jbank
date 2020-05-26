import logging
from pprint import pprint
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from cryptography import x509
from cryptography.hazmat.backends import default_backend


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Parses x509 cert'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('pem', type=str)

    def do(self, *args, **options):
        pem_data = open(options['pem'], 'rb').read()
        cert = x509.load_pem_x509_certificate(pem_data, default_backend())
        pprint(cert)
