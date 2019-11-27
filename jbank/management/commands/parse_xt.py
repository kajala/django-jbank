import os
from copy import copy
from pathlib import Path
from pprint import pprint
from django.core.files import File
from django.core.management import CommandParser
from django.db import transaction
from jbank.camt import camt053_get_iban, camt053_create_statement, camt053_parse_statement_from_file
from jbank.helpers import get_or_create_bank_account
from jbank.files import list_dir_files
from jbank.models import Statement, StatementFile
from jutil.command import SafeCommand


class Command(SafeCommand):
    help = 'Parses XML bank account statement (camt.053.001.02) files'

    def add_arguments(self, parser: CommandParser):
        parser.add_argument('path', type=str)
        parser.add_argument('--verbose', action='store_true')
        parser.add_argument('--test', action='store_true')
        parser.add_argument('--delete-old', action='store_true')
        parser.add_argument('--auto-create-accounts', action='store_true')
        parser.add_argument('--suffix', type=str)

    def do(self, *args, **options):
        files = list_dir_files(options['path'], options['suffix'])
        for filename in files:
            plain_filename = os.path.basename(filename)
            if options['delete_old']:
                Statement.objects.filter(name=plain_filename).delete()

            if options['test']:
                statement = camt053_parse_statement_from_file(filename)
                pprint(statement)
                continue

            if not Statement.objects.filter(name=plain_filename).first():
                print('Importing statement file {}'.format(plain_filename))

                statement = camt053_parse_statement_from_file(filename)
                if options['verbose']:
                    pprint(statement)

                with transaction.atomic():
                    if not Statement.objects.filter(name=plain_filename).first():
                        file = StatementFile()
                        file.save()
                        with open(filename, 'rb') as fp:
                            file.file.save(plain_filename, File(fp))
                        for data in [statement]:
                            if options['auto_create_accounts']:
                                account_number = camt053_get_iban(data)
                                if account_number:
                                    get_or_create_bank_account(account_number)

                            camt053_create_statement(data, name=plain_filename, file=file)
            else:
                print('Skipping statement file {}'.format(filename))