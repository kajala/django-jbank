import logging
from django.core.management import CommandParser
from jutil.command import SafeCommand

from jbank.models import WsEdiConnection
from jbank.wsedi import wsedi_execute

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
    Executes WS-EDI command using direct bank connection.
    """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('--ws', type=int, default=1)
        parser.add_argument('--cmd', type=str, default='DownloadFileList')

    def do(self, *args, **options):
        ws = WsEdiConnection.objects.get(id=options['ws'])
        cmd = options['cmd']
        wsedi_execute(ws, cmd, verbose=True)