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
from jbank.models import Payout, PayoutStatus, PAYOUT_ERROR
from jbank.wsedi import wsedi_get, wsedi_upload_file
from jutil.command import SafeCommand
from jutil.email import send_email

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Upload Finnish bank files. Assumes WS-EDI API parameter compatible HTTP REST API end-point.
        Uses project settings WSEDI_URL and WSEDI_TOKEN.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--file-type', type=str, help='E.g. XL, NDCORPAYS, pain.001.001.03')
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--retry-error', action='store_true')
        parser.add_argument('--ignore-uploaded', action='store_true')
        parser.add_argument('--move-to', type=str, help='Target directory for successfully uploaded files')

    def do(self, *args, **options):
        file_type = options['file_type']
        if not file_type:
            return print('--file-type required (e.g. XL, NDCORPAYS, pain.001.001.03)')

        files = list_dir_files(options['path'])
        for filename in files:
            filename_base = basename(filename)
            p = Payout.objects.filter(file_name=filename_base).first()
            assert p is None or isinstance(p, Payout)
            response_code = response_text = ''

            try:
                if not options['force']:
                    if p:
                        if p.state == PAYOUT_ERROR and not options['retry_error']:  # unless --retry-error
                            continue
                        if p.is_upload_done:
                            if options['ignore_uploaded']:
                                continue
                            raise ValidationError(_('File already uploaded') + ' ({})'.format(p.group_status))

                # upload file
                logger.info('Uploading {} file {}'.format(file_type, filename_base))
                with open(filename, 'rt') as fp:
                    file_content = fp.read()
                res = wsedi_upload_file(file_content, file_type, filename)
                logger.info('HTTP response {}'.format(res.status_code))
                logger.info(res.text)

                # parse response
                data = res.json()
                response_code = data.get('ResponseCode', '')[:4]
                response_text = data.get('ResponseText', '')[:255]
                fds = data.get("FileDescriptors", {}).get("FileDescriptor", [])
                fd = {} if len(fds) != 0 else fds[0]
                file_reference = fd.get('FileReference', '')
                if p:
                    p.file_reference = file_reference
                    p.save()
                    PayoutStatus.objects.create(payout=p, msg_id=p.msg_id, file_name=filename_base, response_code=response_code, response_text=response_text, status_reason='File upload OK')

                # move successful files to new directory (optional)
                if options['move_to']:
                    if response_code == '00':
                        dst = os.path.join(options['move_to'], filename_base)
                        logger.info('Moving {} to {}'.format(filename, dst))
                        file_move_safe(filename, dst, allow_overwrite=True)

            except Exception as e:
                long_err = "File upload failed ({}): ".format(filename) + traceback.format_exc()
                logger.error(long_err)
                if p:
                    short_err = 'File upload failed: ' + str(e)
                    p.state = PAYOUT_ERROR
                    PayoutStatus.objects.create(payout=p, msg_id=p.msg_id, file_name=filename_base, response_code=response_code, response_text=response_text, status_reason=short_err[:255])

            if p:
                p.save()
