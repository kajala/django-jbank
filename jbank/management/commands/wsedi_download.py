import base64
import logging
import os
from django.core.management import CommandParser
from jbank.helpers import process_pain002_file_content
from jbank.wsedi import wsedi_get
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Download Finnish bank files. Assumes WS-EDI API parameter compatible HTTP REST API end-point.
        Uses project settings WSEDI_URL and WSEDI_TOKEN.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--overwrite', action='store_true')
        parser.add_argument('--file-type', type=str, help='E.g. TO, SVM, XP, NDCORPAYL, pain.002.001.03')
        parser.add_argument('--status', type=str, default='', help='E.g. DLD, NEW')
        parser.add_argument('--file-reference', type=str, help='Download single file based on file reference')
        parser.add_argument('--list-only', action='store_true')
        parser.add_argument('--process-pain002', action='store_true')

    def do(self, *args, **options):
        path = options['path']
        command = 'DownloadFileList'
        file_reference = options['file_reference']
        if file_reference:
            command = 'DownloadFile'
        status = options['status']
        file_type = options['file_type']
        if command == 'DownloadFileList' and not file_type:
            return print('--file-type required (e.g. TO, SVM, XP, NDCORPAYL, pain.002.001.03)')
        res = wsedi_get(command, file_type, status, file_reference, options['verbose'])
        if res.status_code >= 300:
            raise Exception("WS-EDI {} HTTP {}".format(command, res.status_code))
        data = res.json()
        """
            "FileDescriptors": {
                "FileDescriptor": [
                    {
                        "FileReference": "535283541",
                        "TargetId": "NONE",
                        "UserFilename": "STOL001.FMV80KT2.WEBSER.PS",
                        "ParentFileReference": "1218",
                        "FileType": "TO",
                        "FileTimestamp": "2017-12-18T20:33:09.362+02:00",
                        "Status": "DLD",
                        "LastDownloadTimestamp": "2017-12-19T12:36:34.490+02:00",
                        "ForwardedTimestamp": "2017-12-18T20:33:09.362+02:00",
                        "Deletable": "false",
                        "CustomerNumber": "06720106",
                        "Modifier": "06720106",
                        "ModifiedTimestamp": "2017-12-19T12:36:34.490+02:00",
                        "SourceId": "A",
                        "Environment": "PRODUCTION"
                    },
                    ...
        """
        if command == 'DownloadFileList':
            if 'FileDescriptors' in data and 'FileDescriptor' in data['FileDescriptors']:
                for fd in data['FileDescriptors']['FileDescriptor']:
                    file_reference = fd['FileReference']
                    file_type = fd['FileType']
                    file_basename = file_reference + '.' + file_type
                    file_path = os.path.join(path, file_basename)
                    if options['list_only']:
                        print('{file_reference} ({file_type}/{status}): {user_filename} ({timestamp})'.format(file_reference=file_reference, file_type=file_type, status=fd.get('Status'), user_filename=fd.get('UserFilename'), timestamp=fd.get('FileTimestamp')))
                        continue
                    if options['overwrite'] or not os.path.isfile(file_path):
                        command = 'DownloadFile'
                        res = wsedi_get(command, file_type, '', file_reference, options['verbose'])
                        if res.status_code >= 300:
                            raise Exception("WS-EDI {} HTTP {}".format(command, res.status_code))
                        file_data = res.json()
                        bcontent = base64.b64decode(file_data['Content'])
                        with open(file_path, 'wb') as fp:
                            fp.write(bcontent)
                        logger.info('Wrote file {}'.format(file_path))

                        # process selected files immediately
                        if options['process_pain002'] and file_type in ['XP', 'pain.002.001.03', 'NDCORPAYL']:
                            process_pain002_file_content(bcontent, file_path)
                    else:
                        print('Skipping old file {}'.format(file_path))
            else:
                print('Empty file list downloaded')
        elif command == 'DownloadFile':
            bcontent = base64.b64decode(data['Content'])
            file_path = os.path.join(path, file_reference)
            if options['overwrite'] or not os.path.isfile(file_path):
                with open(file_path, 'wb') as fp:
                    fp.write(bcontent)
                logger.info('Wrote file {}'.format(file_path))
            else:
                print('Skipping old file {}'.format(file_path))

