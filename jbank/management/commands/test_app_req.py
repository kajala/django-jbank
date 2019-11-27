import logging
import os
from decimal import Decimal

from django.conf import settings
from django.core.management import CommandParser
from jacc.models import AccountType, Account
from jutil.command import SafeCommand
from jbank.helpers import make_msg_id, validate_xml
from jbank.models import Payout, WsEdiConnection
import jbank


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Makes test application request'

    def add_arguments(self, parser: CommandParser):
        default_xsd = os.path.join(os.path.dirname(jbank.__file__), 'templates/jbank/application_request.xsd')
        parser.add_argument('--ws', type=int, default=1)
        parser.add_argument('--xsd', type=str, default=default_xsd)
        parser.add_argument('--command', type=str, default='DownloadFileList')
        parser.add_argument('--file', type=str)

    def do(self, *args, **options):
        ws = WsEdiConnection.objects.get(id=options['ws'])
        if options['file']:
            content = open(options['file'], 'rb').read()
        else:
            content = ws.get_application_request(options['command']).encode()
        validate_xml(content, options['xsd'])
