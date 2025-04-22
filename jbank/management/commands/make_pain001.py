import logging
import os
import traceback
from typing import Optional
from django.core.management.base import CommandParser
from django.template import Template, Context
from django.utils import translation
from django.utils.timezone import now

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

try:
    import zoneinfo  # noqa
except ImportError:
    from backports import zoneinfo  # type: ignore  # noqa
from zoneinfo import ZoneInfo

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
        parser.add_argument("--xml-declaration", action="store_true")
        parser.add_argument("--template-file", type=str)
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--generate-msg-id", action="store_true")
        parser.add_argument("--generate-end-to-end-id", action="store_true")
        parser.add_argument("--tz", type=str, default="Europe/Helsinki")

    def do(self, *args, **kwargs):  # noqa
        target_dir = kwargs["dir"]
        if kwargs["verbose"]:
            logger.info("Writing pain.001 files to %s", target_dir)

        payouts = Payout.objects.all()
        if kwargs["payout"]:
            payouts = payouts.filter(id=kwargs["payout"])
        else:
            payouts = payouts.filter(state=PAYOUT_WAITING_PROCESSING)
        if kwargs["ws"]:
            ws = WsEdiConnection.objects.get(id=kwargs["ws"])
            assert isinstance(ws, WsEdiConnection)
            if ws and not ws.enabled:
                logger.info("WS connection %s not enabled, exiting", ws)
                return
            payouts = payouts.filter(connection=ws)

        pain001_template: Optional[Template] = None
        if kwargs["template_file"]:
            with open(kwargs["template_file"], "rt", encoding="UTF-8") as fp:
                pain001_template = Template(fp.read())

        for p in list(payouts.order_by("id").distinct()):
            assert isinstance(p, Payout)
            try:
                if p.due_date is None:
                    p.due_date = now().astimezone(ZoneInfo(kwargs["tz"])).date()
                    p.save(update_fields=["due_date"])
                if kwargs["verbose"]:
                    logger.info("%s", p)
                if p.state != PAYOUT_WAITING_PROCESSING and not kwargs["force"]:
                    logger.warning("Skipping %s since payment state %s", p, p.state_name)
                    continue

                if not p.end_to_end_id or kwargs["generate_end_to_end_id"]:
                    p.generate_end_to_end_id(commit=False)
                if not p.msg_id or kwargs["generate_msg_id"]:
                    p.generate_msg_id(commit=False)
                if not p.file_name:
                    p.file_name = p.msg_id + "." + kwargs["suffix"]
                    p.save(update_fields=["file_name"])
                p.full_path = os.path.join(target_dir, p.file_name)

                if pain001_template is None:
                    pain001 = Pain001(
                        p.msg_id,
                        p.payer.name,
                        p.payer.account_number,
                        p.payer.bic,
                        p.payer.org_id,
                        p.payer.address_lines,
                        p.payer.country_code,
                    )
                    if kwargs["tz"]:
                        pain001.tz_str = kwargs["tz"]
                    if kwargs["xml_declaration"]:
                        pain001.xml_declaration = kwargs["xml_declaration"]
                    if p.messages:
                        remittance_info = p.messages
                        remittance_info_type = PAIN001_REMITTANCE_INFO_MSG
                    else:
                        remittance_info = p.reference
                        remittance_info_type = PAIN001_REMITTANCE_INFO_OCR_ISO if remittance_info[:2] == "RF" else PAIN001_REMITTANCE_INFO_OCR
                    pain001.add_payment(
                        p.msg_id,
                        p.recipient.name,
                        p.recipient.account_number,
                        p.recipient.bic,
                        p.amount,
                        remittance_info,
                        remittance_info_type,
                        p.due_date,
                        p.end_to_end_id,
                    )
                    pain001.render_to_file(p.full_path)
                else:
                    with translation.override("en_US"):
                        content = pain001_template.render(Context({"p": p}))
                        with open(p.full_path, "wt", encoding="UTF-8") as fp:
                            fp.write(content)

                logger.info("%s written", p.full_path)
                p.state = PAYOUT_WAITING_UPLOAD
                p.save()
                PayoutStatus.objects.create(payout=p, file_name=p.file_name, msg_id=p.msg_id, status_reason="File generation OK")
            except Exception as exc:
                short_err = "File generation failed: " + str(exc)
                logger.error("File generation failed (%s): %s", p.file_name, traceback.format_exc())
                p.state = PAYOUT_ERROR
                p.save()
                PayoutStatus.objects.create(
                    payout=p,
                    group_status=PAYOUT_ERROR,
                    file_name=p.file_name,
                    msg_id=p.msg_id,
                    status_reason=short_err[:255],
                )
