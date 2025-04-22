# pylint: disable=logging-format-interpolation,too-many-locals
import logging
import os.path
import shutil
import traceback
from typing import List
from django.core.management.base import CommandParser
from jutil.files import list_files
from jutil.xml import xml_to_dict
from jbank.models import WsEdiConnection, Payout, PAYOUT_UPLOADED, PAYOUT_ERROR, PayoutStatus, PAYOUT_WAITING_BATCH_UPLOAD
from jbank.wsedi import wsedi_execute
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Upload Finnish bank payments in a single pain.001 batch file"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("dir", type=str)
        parser.add_argument("--file-type", type=str, help="E.g. XL, NDCORPAYS, pain.001.001.03", required=True)
        parser.add_argument("--ws", type=int, required=True)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--archive-dir", type=str)
        parser.add_argument("--suffix", type=str, default="XL")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--test", action="store_true")

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        ws_connection = WsEdiConnection.objects.get(id=options["ws"])
        assert isinstance(ws_connection, WsEdiConnection)
        file_type = options["file_type"]
        suffix = options["suffix"]
        if not suffix.startswith("."):
            suffix = "." + suffix
        test = options["test"]

        for full_path in list_files(options["dir"], suffix):
            payout_list: List[Payout] = []
            response_code = ""
            response_text = ""
            base_name = os.path.basename(full_path)
            try:
                logger.info("Uploading %s", full_path)
                if options["archive_dir"]:
                    archive_dir = options["archive_dir"]
                else:
                    archive_dir = os.path.join(os.path.dirname(full_path), "archive")
                if not os.path.isdir(archive_dir):
                    os.mkdir(archive_dir)
                    logger.info("%s created", archive_dir)
                archive_full_path = os.path.join(archive_dir, base_name)

                # ensure we have payments waiting associated with the file
                payout_qs = Payout.objects.filter(state=PAYOUT_WAITING_BATCH_UPLOAD, full_path=full_path)
                payout_list: List[Payout] = list(payout_qs.order_by("id").distinct())
                if not payout_list and not options["force"]:
                    logger.warning("Skipping file %s because it is not used by any Payout objects, use --force to upload it anyway", full_path)
                    continue

                # mark payments as uploaded
                for p in payout_list:
                    p.state = PAYOUT_UPLOADED
                    p.save(update_fields=["state"])

                # archive uploaded file
                with open(full_path, "rt", encoding="utf-8") as fp:
                    file_content = fp.read()
                shutil.move(full_path, archive_full_path)
                logger.info("%s archived to %s", full_path, archive_full_path)

                # upload file
                logger.info("Executing WS-EDI command UploadFile (%s) %s", file_type, base_name)
                if not test:
                    content = wsedi_execute(
                        ws_connection,
                        "UploadFile",
                        file_content=file_content,
                        file_type=file_type,
                        verbose=options["verbose"],
                    )
                    data = xml_to_dict(content, array_tags=["FileDescriptor"])
                else:
                    data = {
                        "ResponseCode": "00",
                        "ResponseText": "Test OK, file not uploaded",
                    }

                # parse response
                response_code = data.get("ResponseCode", "")[:4]
                response_text = data.get("ResponseText", "")[:255]
                if response_code != "00":
                    logger.error("WS-EDI file %s upload failed: %s (%s)", base_name, response_text, response_code)
                    raise Exception("Response code {} ({})".format(response_code, response_text))
                file_reference = ""
                if "FileDescriptors" in data:
                    fds = data.get("FileDescriptors", {}).get("FileDescriptor", [])  # type: ignore
                    fd = {} if not fds else fds[0]
                    file_reference = fd.get("FileReference", "")
                for p in payout_list:
                    if file_reference:
                        p.file_reference = file_reference
                        p.save(update_fields=["file_reference"])
                    PayoutStatus.objects.create(
                        payout=p,
                        msg_id=p.msg_id,
                        file_name=p.file_name,
                        response_code=response_code,
                        response_text=response_text,
                        status_reason="File upload OK",
                    )
            except Exception as exc:
                logger.error("File upload failed (%s): %s", base_name, traceback.format_exc())
                short_err = f"File upload failed: {exc}"
                for p in payout_list:
                    p.state = PAYOUT_ERROR
                    p.save(update_fields=["state"])
                    PayoutStatus.objects.create(
                        payout=p,
                        group_status=PAYOUT_ERROR,
                        msg_id=p.msg_id,
                        file_name=p.file_name,
                        response_code=response_code,
                        response_text=response_text,
                        status_reason=short_err[:255],
                    )
