from pprint import pprint
from django.core.management import CommandParser
from jbank.files import list_dir_files
from jbank.sepa import Pain002
from jutil.command import SafeCommand


class Command(SafeCommand):
    help = 'Parses pain.002 payment response .XP files'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--all', action='store_true')

    def do(self, *args, **options):
        files = list_dir_files(options['path'], '.XP')
        for f in files:
            print(f)
            with open(f, 'rb') as fp:
                p = Pain002(fp.read())
                print(p)
