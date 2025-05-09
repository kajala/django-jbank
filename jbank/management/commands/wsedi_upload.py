# pylint: disable=logging-format-interpolation,too-many-locals
import logging
import traceback
from datetime import timedelta
from django.core.management.base import CommandParser
from django.utils.timezone import now
from jutil.xml import xml_to_dict
from jbank.models import Payout, PayoutStatus, PAYOUT_ERROR, PAYOUT_WAITING_UPLOAD, PAYOUT_UPLOADED, WsEdiConnection, PAYOUT_PAID, PAYOUT_WAITING_BATCH_UPLOAD
from jbank.wsedi import wsedi_execute
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Upload Finnish bank pain.001 files
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--payout", type=int)
        parser.add_argument("--file-type", type=str, help="E.g. XL, NDCORPAYS, pain.001.001.03")
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--default-ws", type=int)
        parser.add_argument("--ws", type=int)
        parser.add_argument("--test", action="store_true")

    def do(self, *args, **options):  # noqa
        default_ws = WsEdiConnection.objects.get(id=options["default_ws"]) if options["default_ws"] else None
        assert default_ws is None or isinstance(default_ws, WsEdiConnection)
        file_type = options["file_type"]
        if not file_type:
            print("--file-type required (e.g. XL, NDCORPAYS, pain.001.001.03)")
            return
        test = options["test"]

        payouts = Payout.objects.all()
        if options["payout"]:
            payouts = Payout.objects.filter(id=options["payout"])
        else:
            payouts = payouts.filter(state=PAYOUT_WAITING_UPLOAD)
            if options["ws"]:
                payouts = payouts.filter(connection_id=options["ws"])

        for p in list(payouts.order_by("id").distinct()):
            assert isinstance(p, Payout)
            p.refresh_from_db()

            ws_connection = p.connection or default_ws
            if ws_connection is None:
                raise Exception(f"WS-connection not set for {p} and --default-ws is missing")

            response_code = ""
            response_text = ""
            try:
                if p.state != PAYOUT_WAITING_UPLOAD:
                    logger.info("Skipping %s since not in state PAYOUT_WAITING_UPLOAD", p)
                    continue
                if not ws_connection.enabled:
                    logger.info("WS connection %s not enabled, skipping payment %s", ws_connection, p)
                    continue

                # make sure file has not been uploaded already
                old = now() - timedelta(days=90)
                dup_states = [PAYOUT_WAITING_UPLOAD, PAYOUT_WAITING_BATCH_UPLOAD, PAYOUT_UPLOADED, PAYOUT_PAID]
                dup_pmt = Payout.objects.exclude(id=p.id).filter(created__gt=old, full_path=p.full_path, state__in=dup_states).first()
                if dup_pmt is not None:
                    assert isinstance(dup_pmt, Payout)
                    raise Exception(f"File {p.file_name} is duplicate of payment id={dup_pmt.id}")

                # upload file
                logger.info("Uploading payment id={} {} file {}".format(p.id, file_type, p.full_path))
                with open(p.full_path, "rt", encoding="utf-8") as fp:
                    file_content = fp.read()
                p.state = PAYOUT_UPLOADED
                p.save(update_fields=["state"])

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
                file_reference = ""
                if response_code != "00":
                    logger.error("WS-EDI file %s upload failed: %s (%s)", p.file_name, response_text, response_code)
                    raise Exception("Response code {} ({})".format(response_code, response_text))
                if "FileDescriptors" in data:
                    fds = data.get("FileDescriptors", {}).get("FileDescriptor", [])  # type: ignore
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

            except Exception as exc:
                logger.error("File upload failed (%s): %s", p.file_name, traceback.format_exc())
                short_err = f"File upload failed: {exc}"
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
