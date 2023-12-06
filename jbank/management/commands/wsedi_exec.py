import base64
import logging
from django.core.management.base import CommandParser
from jutil.format import get_media_full_path
from jutil.command import SafeCommand
from jbank.helpers import parse_start_and_end_date
from jbank.models import WsEdiConnection
from jbank.wsedi import wsedi_execute
from xml.etree import ElementTree

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Executes WS-EDI command using direct bank connection."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--ws", type=int, default=1)
        parser.add_argument("--cmd", type=str, default="DownloadFileList")
        parser.add_argument("--file-reference", type=str)
        parser.add_argument("--file-type", type=str)
        parser.add_argument("--start-date", type=str)
        parser.add_argument("--end-date", type=str)
        parser.add_argument("--status", type=str)

    def do(self, *args, **options):  # pylint: disable=too-many-locals
        ws = WsEdiConnection.objects.get(id=options["ws"])
        assert isinstance(ws, WsEdiConnection)
        if ws and not ws.enabled:
            logger.info("WS connection %s not enabled, exiting", ws)
            return

        start_date, end_date = parse_start_and_end_date(ZoneInfo("Europe/Helsinki"), **options)
        cmd = options["cmd"]
        file_reference = options["file_reference"] or ""
        file_type = options["file_type"] or ""
        status = options["status"] or ""
        response = wsedi_execute(
            ws,
            command=cmd,
            file_reference=file_reference,
            file_type=file_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
            verbose=True,
        )
        print(response)
        root_el = ElementTree.fromstring(response)
        content_el = root_el.find("{http://bxd.fi/xmldata/}Content")
        if content_el is not None:
            content_bytes = base64.b64decode(content_el.text)
            print(content_bytes.decode())
            if file_reference:
                full_path = get_media_full_path("downloads/" + file_reference + "." + file_type)
                with open(full_path, "wb") as fp:
                    fp.write(content_bytes)
                    print(full_path, "written")
