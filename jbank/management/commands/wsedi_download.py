# pylint: disable=logging-format-interpolation,too-many-locals,too-many-branches
import base64
import logging
import os
from django.core.management.base import CommandParser
from django.utils.timezone import now
from jutil.xml import xml_to_dict
from jbank.helpers import parse_start_and_end_date
from jbank.pain002 import process_pain002_file_content
from jbank.models import WsEdiConnection
from jbank.wsedi import wsedi_execute
from jutil.command import SafeCommand

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Download Finnish bank files
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("path", type=str)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--overwrite", action="store_true")
        parser.add_argument("--file-type", type=str, help="E.g. TO, SVM, XP, NDCORPAYL, pain.002.001.03")
        parser.add_argument("--status", type=str, default="", help="E.g. DLD, NEW")
        parser.add_argument("--file-reference", type=str, help="Download single file based on file reference")
        parser.add_argument("--list-only", action="store_true")
        parser.add_argument("--process-pain002", action="store_true")
        parser.add_argument("--start-date", type=str)
        parser.add_argument("--end-date", type=str)
        parser.add_argument("--ws", type=int)

    def do(self, *args, **options):  # noqa
        ws_qs = WsEdiConnection.objects.all()
        if options["ws"]:
            ws_qs = ws_qs.filter(id=options["ws"])
        if ws_qs.count() > 1:
            raise Exception("--ws required if multiple WS-EDI connections are available")
        ws = ws_qs.first()
        if ws is None:
            raise Exception("WS-EDI connection does not exist")
        assert isinstance(ws, WsEdiConnection)
        if not ws.enabled:
            logger.info("WS connection %s not enabled, exiting", ws)
            return

        start_date, end_date = parse_start_and_end_date(ZoneInfo("Europe/Helsinki"), **options)
        path = os.path.abspath(options["path"])
        command = "DownloadFileList"
        time_now = now()
        file_reference = options["file_reference"]
        if file_reference:
            command = "DownloadFile"
        status = options["status"]
        file_type = options["file_type"]
        verbose = options["verbose"]
        if command == "DownloadFileList" and not file_type:
            print("--file-type required (e.g. TO, SVM, XP, NDCORPAYL, pain.002.001.03)")
            return

        content = wsedi_execute(
            ws,
            command=command,
            file_type=file_type,
            status=status,
            start_date=start_date,
            end_date=end_date,
            file_reference=file_reference,
            verbose=verbose,
        )
        data = xml_to_dict(content, array_tags=["FileDescriptor"])

        if command == "DownloadFileList":
            if "FileDescriptors" in data and data["FileDescriptors"] is not None and "FileDescriptor" in data["FileDescriptors"]:
                for fd in data["FileDescriptors"]["FileDescriptor"]:
                    file_reference = fd["FileReference"]
                    file_type = fd["FileType"]
                    file_basename = file_reference + "." + file_type
                    file_path = os.path.join(path, file_basename)
                    if options["list_only"]:
                        print(
                            "{file_reference} ({file_type}/{status}): {user_filename} ({timestamp})".format(
                                file_reference=file_reference,
                                file_type=file_type,
                                status=fd.get("Status"),
                                user_filename=fd.get("UserFilename"),
                                timestamp=fd.get("FileTimestamp"),
                            )
                        )
                        continue
                    if options["overwrite"] or not os.path.isfile(file_path):
                        command = "DownloadFile"
                        content = wsedi_execute(
                            ws,
                            command=command,
                            file_type=file_type,
                            status="",
                            file_reference=file_reference,
                            verbose=options["verbose"],
                        )
                        file_data = xml_to_dict(content)
                        if "Content" not in file_data:
                            logger.error("WS-EDI {} Content block missing: {}".format(command, file_data))
                            raise Exception("WS-EDI {} Content block missing".format(command))
                        bcontent = base64.b64decode(file_data["Content"])
                        with open(file_path, "wb") as fp:
                            fp.write(bcontent)
                        logger.info("Wrote file {}".format(file_path))

                        # process selected files immediately
                        if options["process_pain002"] and file_type in ["XP", "pain.002.001.03", "NDCORPAYL"]:
                            process_pain002_file_content(bcontent, file_path, created=time_now)
                    else:
                        if verbose:
                            logger.info("Skipping old file %s", file_path)
            else:
                print("Empty file list downloaded")
        elif command == "DownloadFile":
            bcontent = base64.b64decode(data["Content"])
            file_path = os.path.join(path, file_reference)
            if options["overwrite"] or not os.path.isfile(file_path):
                with open(file_path, "wb") as fp:
                    fp.write(bcontent)
                logger.info("Wrote file {}".format(file_path))
            else:
                if options["verbose"]:
                    logger.info("Skipping old file %s", file_path)
