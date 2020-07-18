import logging
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from jbank.models import WsEdiConnection, WsEdiSoapCall
from jbank.wspki import wspki_execute, process_wspki_response

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
    Executes WS-PKI command using direct bank connection.
    """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('--ws', type=int, default=1)
        parser.add_argument('--cmd', type=str, default='GetBankCertificate')
        parser.add_argument('--process-response', type=int)

    def do(self, *args, **options):
        if options['process_response']:
            soap_call = WsEdiSoapCall.objects.get(id=options['process_response'])
            assert isinstance(soap_call, WsEdiSoapCall)
            content = open(soap_call.debug_application_response_full_path, 'rb').read()
            process_wspki_response(content, soap_call)
            return

        ws = WsEdiConnection.objects.get(id=options['ws'])
        assert isinstance(ws, WsEdiConnection)
        if ws and not ws.enabled:
            logger.info('WS connection %s not enabled, exiting', ws)
            return

        cmd = options['cmd']
        wspki_execute(ws, command=cmd, verbose=True)
