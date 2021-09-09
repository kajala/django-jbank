# pylint: disable=logging-format-interpolation,too-many-locals
import logging
import traceback
from django.core.management.base import CommandParser
from jutil.xml import xml_to_dict
from jbank.models import Payout, PayoutStatus, PAYOUT_ERROR, PAYOUT_WAITING_UPLOAD, PAYOUT_UPLOADED, WsEdiConnection
from jbank.wsedi import wsedi_upload_file, wsedi_execute
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Upload Finnish bank files
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--payout", type=int)
        parser.add_argument("--file-type", type=str, help="E.g. XL, NDCORPAYS, pain.001.001.03")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--default-ws", type=int)
        parser.add_argument("--ws", type=int)

    def do(self, *args, **options):  # pylint: disable=too-many-branches
        default_ws = WsEdiConnection.objects.get(id=options["default_ws"]) if options["default_ws"] else None
        assert default_ws is None or isinstance(default_ws, WsEdiConnection)
        file_type = options["file_type"]
        if not file_type:
            print("--file-type required (e.g. XL, NDCORPAYS, pain.001.001.03)")
            return

        payouts = Payout.objects.all()
        if options["payout"]:
            payouts = Payout.objects.filter(id=options["payout"])
        else:
            payouts = payouts.filter(state=PAYOUT_WAITING_UPLOAD)
            if options["ws"]:
                payouts = payouts.filter(connection_id=options["ws"])

        for p in list(payouts):
            assert isinstance(p, Payout)
            p.refresh_from_db()
            ws_connection = p.connection or default_ws

            if p.state != PAYOUT_WAITING_UPLOAD:
                logger.info("Skipping {} since not in state PAYOUT_WAITING_UPLOAD".format(p))
                continue
            if ws_connection and not ws_connection.enabled:
                logger.info("WS connection %s not enabled, skipping payment %s", ws_connection, p)
                continue

            response_code = ""
            response_text = ""
            try:
                # upload file
                logger.info("Uploading payment id={} {} file {}".format(p.id, file_type, p.full_path))
                with open(p.full_path, "rt", encoding="utf-8") as fp:
                    file_content = fp.read()
                p.state = PAYOUT_UPLOADED
                p.save(update_fields=["state"])
                if ws_connection:
                    content = wsedi_execute(
                        ws_connection,
                        "UploadFile",
                        file_content=file_content,
                        file_type=file_type,
                        verbose=options["verbose"],
                    )
                    data = xml_to_dict(content, array_tags=["FileDescriptor"])
                else:
                    res = wsedi_upload_file(
                        file_content=file_content,
                        file_type=file_type,
                        file_name=p.file_name,
                        verbose=options["verbose"],
                    )
                    logger.info("HTTP response {}".format(res.status_code))
                    logger.info(res.text)
                    data = res.json()

                # parse response
                response_code = data.get("ResponseCode", "")[:4]
                response_text = data.get("ResponseText", "")[:255]
                if response_code != "00":
                    msg = "WS-EDI file {} upload failed: {} ({})".format(p.file_name, response_text, response_code)
                    logger.error(msg)
                    raise Exception("Response code {} ({})".format(response_code, response_text))
                if "FileDescriptors" in data:
                    fds = data.get("FileDescriptors", {}).get("FileDescriptor", [])
                    fd = {} if not fds else fds[0]
                    file_reference = fd.get("FileReference", "")
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

            except Exception as e:
                long_err = "File upload failed ({}): ".format(p.file_name) + traceback.format_exc()
                logger.error(long_err)
                short_err = "File upload failed: " + str(e)
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
