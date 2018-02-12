from pprint import pprint
from django.core.management import CommandParser
from jbank.parsers import parse_svm_batches_from_file
from jutil.command import SafeCommand


class Command(SafeCommand):
    help = 'Parses bank reference payment .SVM (saapuvat viitemaksut) files'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)

    def do(self, *args, **options):
        batches = parse_svm_batches_from_file(options['path'])
        pprint(batches)
