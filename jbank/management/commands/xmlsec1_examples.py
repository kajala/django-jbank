import os
import subprocess
from django.core.management import CommandParser
from jutil.command import SafeCommand
import jbank


class Command(SafeCommand):
    help = 'Compiles xmlsec1-examples'

    def add_arguments(self, parser: CommandParser):
        pass

    def do(self, *args, **options):
        package_path = os.path.dirname(jbank.__file__)
        xmlsec1_examples_path = os.path.join(package_path, 'xmlsec1-examples')
        print('xmlsec1-examples @ {}'.format(xmlsec1_examples_path))
        os.chdir(xmlsec1_examples_path)
        subprocess.run(['make', 'clean'])
        subprocess.run(['make'])
