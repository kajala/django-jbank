import logging
from os.path import basename
from django.core.management import CommandParser
from jbank.files import list_dir_files
from jbank.helpers import process_pain002_file_content
from jbank.models import Payout, PayoutStatus, PAYOUT_PAID
from jbank.sepa import Pain002
from jutil.command import SafeCommand


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = 'Parses pain.002 payment response .XP files and updates Payout status'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--test', action='store_true')
        parser.add_argument('--verbose', action='store_true')

    def do(self, *args, **options):
        files = list_dir_files(options['path'], '.XP')
        for f in files:
            if PayoutStatus.objects.is_file_processed(f):
                if options['verbose']:
                    print('Skipping processed payment status file', f)
                continue
            if options['verbose']:
                print('Importing payment status file', f)
            with open(f, 'rb') as fp:
                process_pain002_file_content(fp.read(), f)
