import logging
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jbank.models import WsEdiConnection, WsEdiSoapCall, PayoutParty
from jbank.wspki import wspki_execute, process_wspki_response
from jutil.format import format_xml_bytes

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
    Executes WS-PKI command using direct bank connection.
    """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--ws", type=int, default=1)
        parser.add_argument("--cmd", type=str, required=True)
        parser.add_argument("--payout-party-id", type=int, required=True)
        parser.add_argument("--process-response", type=int)

    def do(self, *args, **options):
        if options["process_response"]:
            soap_call = WsEdiSoapCall.objects.get(id=options["process_response"])
            assert isinstance(soap_call, WsEdiSoapCall)
            if not soap_call.debug_response_full_path:
                raise Exception("SOAP call response not available")
            content = open(soap_call.debug_response_full_path, "rb").read()
            process_wspki_response(content, soap_call)
            return

        ws = WsEdiConnection.objects.get(id=options["ws"])
        assert isinstance(ws, WsEdiConnection)
        if ws and not ws.enabled:
            logger.info("WS connection %s not enabled, exiting", ws)
            return

        cmd = options["cmd"]
        payout_party_id = options["payout_party_id"]
        payout_party = PayoutParty.objects.get(id=payout_party_id)
        assert isinstance(payout_party, PayoutParty)
        response = wspki_execute(ws, payout_party=payout_party, command=cmd, verbose=True)
        print(format_xml_bytes(response).decode())
