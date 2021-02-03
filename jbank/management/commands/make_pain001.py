# pylint: disable=logging-format-interpolation,too-many-branches
import logging
import os
import traceback
from django.core.management.base import CommandParser
from jbank.models import (
    Payout,
    PAYOUT_ERROR,
    PAYOUT_WAITING_PROCESSING,
    PayoutStatus,
    PAYOUT_WAITING_UPLOAD,
    WsEdiConnection,
)
from jbank.sepa import (
    Pain001,
    PAIN001_REMITTANCE_INFO_MSG,
    PAIN001_REMITTANCE_INFO_OCR_ISO,
    PAIN001_REMITTANCE_INFO_OCR,
)
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Generates pain.001.001.03 compatible SEPA payment files from pending Payout objects.
        By default generates files of Payouts in WAITING_PROCESSING state.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("dir", type=str)
        parser.add_argument("--payout", type=int)
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--ws", type=int)
        parser.add_argument("--suffix", type=str, default="XL")

    def do(self, *args, **options):
        target_dir = options["dir"]
        if options["verbose"]:
            logger.info("Writing pain.001 files to {}".format(target_dir))

        payouts = Payout.objects.all()
        if options["payout"]:
            payouts = Payout.objects.filter(id=options["payout"])
        else:
            payouts = payouts.filter(state=PAYOUT_WAITING_PROCESSING)
        if options["ws"]:
            ws = WsEdiConnection.objects.get(id=options["ws"])
            assert isinstance(ws, WsEdiConnection)
            if ws and not ws.enabled:
                logger.info("WS connection %s not enabled, exiting", ws)
                return
            payouts = payouts.filter(connection=ws)

        for p in list(payouts):
            assert isinstance(p, Payout)
            try:
                if options["verbose"]:
                    print(p)

                if not p.msg_id:
                    p.generate_msg_id()
                if not p.file_name:
                    p.file_name = p.msg_id + "." + options["suffix"]
                    p.save(update_fields=["file_name"])

                pain001 = Pain001(
                    p.msg_id,
                    p.payer.name,
                    p.payer.account_number,
                    p.payer.bic,
                    p.payer.org_id,
                    p.payer.address_lines,
                    p.payer.country_code,
                )
                if p.messages:
                    remittance_info = p.messages
                    remittance_info_type = PAIN001_REMITTANCE_INFO_MSG
                else:
                    remittance_info = p.reference
                    remittance_info_type = (
                        PAIN001_REMITTANCE_INFO_OCR_ISO if remittance_info[:2] == "RF" else PAIN001_REMITTANCE_INFO_OCR
                    )
                pain001.add_payment(
                    p.msg_id,
                    p.recipient.name,
                    p.recipient.account_number,
                    p.recipient.bic,
                    p.amount,
                    remittance_info,
                    remittance_info_type,
                    p.due_date,
                )

                p.full_path = full_path = os.path.join(target_dir, p.file_name)
                if options["verbose"]:
                    print(p, "written to", full_path)
                pain001.render_to_file(full_path)
                logger.info("{} generated".format(full_path))
                p.state = PAYOUT_WAITING_UPLOAD
                p.save(update_fields=["full_path", "state"])

                PayoutStatus.objects.create(
                    payout=p, file_name=p.file_name, msg_id=p.msg_id, status_reason="File generation OK"
                )
            except Exception as e:
                short_err = "File generation failed: " + str(e)
                long_err = "File generation failed ({}): ".format(p.file_name) + traceback.format_exc()
                logger.error(long_err)
                p.state = PAYOUT_ERROR
                p.save(update_fields=["state"])
                PayoutStatus.objects.create(
                    payout=p,
                    group_status=PAYOUT_ERROR,
                    file_name=p.file_name,
                    msg_id=p.msg_id,
                    status_reason=short_err[:255],
                )
