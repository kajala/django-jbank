import logging
import os
from typing import Optional
from datetime import date
from django.core.management import CommandParser
from jacc.helpers import sum_queryset
from django.utils.dateparse import parse_date
from jbank.helpers import make_msg_id
from jbank.models import Payout, PayoutParty, WsEdiConnection, PAYOUT_WAITING_BATCH_UPLOAD
from jbank.sepa import Pain001, PAIN001_REMITTANCE_INFO_MSG, PAIN001_REMITTANCE_INFO_OCR_ISO, PAIN001_REMITTANCE_INFO_OCR
from jutil.command import SafeCommand

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Sends all payouts on specified state to bank via WS-channel as a single payout"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("dir", type=str)
        parser.add_argument("--test", action="store_true")
        parser.add_argument("--state", type=str, default="B")
        parser.add_argument("--due-date", type=str)
        parser.add_argument("--xml-declaration", action="store_true")
        parser.add_argument("--ws", type=int, required=True)
        parser.add_argument("--file-id-as-pmt-id", action="store_true")
        parser.add_argument("--generate-msg-id", action="store_true")
        parser.add_argument("--generate-end-to-end-id", action="store_true")

    def do(self, *args, **kwargs):  # pylint: disable=too-many-locals
        target_dir = kwargs["dir"]
        payout_qs = Payout.objects.all().filter(state=kwargs["state"])

        if kwargs["ws"]:
            ws = WsEdiConnection.objects.get(id=kwargs["ws"])
            assert isinstance(ws, WsEdiConnection)
            if ws and not ws.enabled:
                logger.info("WS connection %s not enabled, exiting", ws)
                return
            payout_qs = payout_qs.filter(connection=ws)

        first = payout_qs.first()
        if first is None:
            print("(nothing to do)")
            return
        assert isinstance(first, Payout)
        pp = first.payer
        assert isinstance(pp, PayoutParty)
        payout_qs = payout_qs.filter(payer=pp)
        logger.info("Processing %s payouts (%s EUR)", payout_qs.count(), sum_queryset(payout_qs))

        due_date: Optional[date] = None
        if kwargs["due_date"]:
            due_date = parse_date(kwargs["due_date"])

        file_id = make_msg_id()
        file_name = f"B{file_id}.XL"
        full_path = os.path.join(target_dir, file_name)
        pain001 = Pain001(
            file_id,
            pp.name,
            pp.account_number,
            pp.bic,
            pp.org_id,
            pp.address_lines,
            pp.country_code,
        )
        if kwargs["xml_declaration"]:
            pain001.xml_declaration = kwargs["xml_declaration"]
        for p in list(payout_qs.order_by("id").distinct()):
            assert isinstance(p, Payout)
            if p.messages:
                remittance_info = p.messages
                remittance_info_type = PAIN001_REMITTANCE_INFO_MSG
            else:
                remittance_info = p.reference
                remittance_info_type = PAIN001_REMITTANCE_INFO_OCR_ISO if remittance_info[:2] == "RF" else PAIN001_REMITTANCE_INFO_OCR
            if not p.end_to_end_id or kwargs["generate_end_to_end_id"]:
                p.generate_end_to_end_id(commit=False)
            if not p.msg_id or kwargs["generate_msg_id"]:
                p.generate_msg_id(commit=False)
            if kwargs["file_id_as_pmt_id"]:
                p.msg_id = file_id
            pain001.add_payment(
                p.msg_id,
                p.recipient.name,
                p.recipient.account_number,
                p.recipient.bic,
                p.amount,
                remittance_info,
                remittance_info_type,
                due_date or p.due_date,
                p.end_to_end_id,
            )
            p.state = PAYOUT_WAITING_BATCH_UPLOAD
            p.file_name = file_name
            p.full_path = full_path
            p.save()

        pain001.render_to_file(full_path)
        logger.info("%s written", full_path)
