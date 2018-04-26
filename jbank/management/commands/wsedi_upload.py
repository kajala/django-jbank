import logging
import os
import traceback
import getpass
from os.path import basename
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.move import file_move_safe
from django.core.management import CommandParser
from jbank.files import list_dir_files
from jbank.models import Payout, PayoutStatus, PAYOUT_ERROR, PAYOUT_CANCELED, PAYOUT_WAITING_UPLOAD, PAYOUT_UPLOADED
from jbank.wsedi import wsedi_get, wsedi_upload_file
from jutil.command import SafeCommand
from jutil.email import send_email


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Upload Finnish bank files. Assumes WS-EDI API parameter compatible HTTP REST API end-point.
        Uses project settings WSEDI_URL and WSEDI_TOKEN.
        By default uploads files of Payouts in WAITING_UPLOAD state.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('--payout', type=int)
        parser.add_argument('--file-type', type=str, help='E.g. XL, NDCORPAYS, pain.001.001.03')
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--force', action='store_true')

    def do(self, *args, **options):
        file_type = options['file_type']
        if not file_type:
            return print('--file-type required (e.g. XL, NDCORPAYS, pain.001.001.03)')

        payouts = Payout.objects.all()
        if options['payout']:
            payouts = Payout.objects.filter(id=options['payout'])
        else:
            payouts = payouts.filter(state=PAYOUT_WAITING_UPLOAD)

        for p in list(payouts):
            assert isinstance(p, Payout)
            try:
                # upload file
                logger.info('Uploading payment id={} {} file {}'.format(p.id, file_type, p.full_path))
                with open(p.full_path, 'rt') as fp:
                    file_content = fp.read()
                p.state = PAYOUT_UPLOADED
                p.save(update_fields=['state'])
                res = wsedi_upload_file(file_content, file_type, p.file_name)
                logger.info('HTTP response {}'.format(res.status_code))
                logger.info(res.text)

                # parse response
                data = res.json()
                response_code = data.get('ResponseCode', '')[:4]
                response_text = data.get('ResponseText', '')[:255]
                fds = data.get("FileDescriptors", {}).get("FileDescriptor", [])
                fd = {} if len(fds) == 0 else fds[0]
                file_reference = fd.get('FileReference', '')
                if not file_reference:
                    raise Exception("FileReference missing from response")
                p.file_reference = file_reference
                p.save(update_fields=['file_reference'])
                PayoutStatus.objects.create(payout=p, msg_id=p.msg_id, file_name=p.file_name, response_code=response_code, response_text=response_text, status_reason='File upload OK')

            except Exception as e:
                long_err = "File upload failed ({}): ".format(p.full_path) + traceback.format_exc()
                logger.error(long_err)
                short_err = 'File upload failed: ' + str(e)
                p.state = PAYOUT_ERROR
                p.save(update_fields=['state'])
                PayoutStatus.objects.create(payout=p, msg_id=p.msg_id, file_name=p.file_name, response_code=response_code, response_text=response_text, status_reason=short_err[:255])
