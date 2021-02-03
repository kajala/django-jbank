import os
import subprocess
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
import jbank


class Command(SafeCommand):
    help = "Compiles xmlsec1-examples"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--clean", action="store_true")
        parser.add_argument("--clean-only", action="store_true")

    def do(self, *args, **options):
        package_path = os.path.dirname(jbank.__file__)
        xmlsec1_examples_path = os.path.join(package_path, "xmlsec1-examples")
        print("xmlsec1-examples @ {}".format(xmlsec1_examples_path))
        os.chdir(xmlsec1_examples_path)
        if options["clean"] or options["clean_only"]:
            subprocess.run(["make", "clean"], check=True)
        if not options["clean_only"]:
            subprocess.run(["make"], check=True)
