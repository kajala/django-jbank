# pylint: disable=logging-format-interpolation,too-many-locals
import logging
import os.path
import shutil
from django.core.management.base import CommandParser
from django.utils.timezone import now
from django.utils.translation import gettext as _
from jutil.admin import admin_log
from jutil.files import list_files
from jutil.xml import xml_to_dict
from jbank.models import WsEdiConnection, Payout, PAYOUT_UPLOADED, PAYOUT_PAID
from jbank.wsedi import wsedi_execute
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Upload Finnish bank files"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("dir", type=str)
        parser.add_argument("--file-type", type=str, help="E.g. XL, NDCORPAYS, pain.001.001.03", required=True)
        parser.add_argument("--ws", type=int, required=True)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--archive-dir", type=str)
        parser.add_argument("--no-mark-paid", action="store_true")
        parser.add_argument("--suffix", type=str, default="XL")

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        ws_connection = WsEdiConnection.objects.get(id=options["ws"])
        assert isinstance(ws_connection, WsEdiConnection)
        file_type = options["file_type"]
        suffix = options["suffix"]
        if not suffix.startswith("."):
            suffix = "." + suffix

        for full_path in list_files(options["dir"], suffix):
            logger.info("Uploading %s", full_path)
            base_name = os.path.basename(full_path)
            if options["archive_dir"]:
                archive_dir = options["archive_dir"]
            else:
                archive_dir = os.path.join(os.path.dirname(full_path), "archive")
            if not os.path.isdir(archive_dir):
                os.mkdir(archive_dir)
                logger.info("%s created", archive_dir)
            archive_full_path = os.path.join(archive_dir, base_name)

            # archive uploaded file
            with open(full_path, "rt", encoding="utf-8") as fp:
                file_content = fp.read()
            shutil.move(full_path, archive_full_path)
            logger.info("%s archived to %s", full_path, archive_full_path)

            # upload file
            logger.info("Executing WS-EDI command UploadFile (%s)", file_type)
            content = wsedi_execute(
                ws_connection,
                "UploadFile",
                file_content=file_content,
                file_type=file_type,
                verbose=options["verbose"],
            )

            # parse response
            data = xml_to_dict(content, array_tags=["FileDescriptor"])
            response_code = data.get("ResponseCode", "")[:4]
            response_text = data.get("ResponseText", "")[:255]
            if response_code != "00":
                logger.error("WS-EDI file upload failed: %s (%s)", response_code, response_text)
                raise Exception(_("Response code {} ({})").format(response_code, response_text))

            # mark uploaded payments as paid
            if not options["no_mark_paid"]:
                payout_qs = Payout.objects.filter(state=PAYOUT_UPLOADED, full_path=full_path)
                for p in list(payout_qs.order_by("id").distinct()):
                    assert isinstance(p, Payout)
                    p.state = PAYOUT_PAID
                    p.last_modified = now()
                    p.save(update_fields=["state", "last_modified"])
                    admin_log([p], f"Uploaded to bank in {base_name} (response {response_code}) using WS connection {ws_connection}, marking payment as PAID")
