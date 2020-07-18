import logging
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jbank.models import WsEdiConnection
from jbank.wspki import wspki_execute


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
    Executes WS-PKI command using direct bank connection.
    """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('--ws', type=int, default=1)
        parser.add_argument('--cmd', type=str, default='GetBankCertificate')

    def do(self, *args, **options):
        ws = WsEdiConnection.objects.get(id=options['ws'])
        assert isinstance(ws, WsEdiConnection)
        if ws and not ws.enabled:
            logger.info('WS connection %s not enabled, exiting', ws)
            return

        cmd = options['cmd']
        wspki_execute(ws, command=cmd, verbose=True)
