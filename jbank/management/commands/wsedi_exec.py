import logging
import pytz
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jbank.helpers import parse_start_and_end_date
from jbank.models import WsEdiConnection
from jbank.wsedi import wsedi_execute


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
    Executes WS-EDI command using direct bank connection.
    """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--ws", type=int, default=1)
        parser.add_argument("--cmd", type=str, default="DownloadFileList")
        parser.add_argument("--file-reference", type=str)
        parser.add_argument("--file-type", type=str)
        parser.add_argument("--start-date", type=str)
        parser.add_argument("--end-date", type=str)

    def do(self, *args, **options):
        ws = WsEdiConnection.objects.get(id=options["ws"])
        assert isinstance(ws, WsEdiConnection)
        if ws and not ws.enabled:
            logger.info("WS connection %s not enabled, exiting", ws)
            return

        start_date, end_date = parse_start_and_end_date(pytz.timezone("Europe/Helsinki"), **options)
        cmd = options["cmd"]
        file_reference = options["file_reference"]
        file_type = options["file_type"]
        wsedi_execute(
            ws,
            command=cmd,
            file_reference=file_reference,
            file_type=file_type,
            start_date=start_date,
            end_date=end_date,
            verbose=True,
        )
