# pylint: disable=c-extension-no-member
import logging
import sys
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from lxml import etree, objectify  # noqa  # type: ignore

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Validates XML files against XSD schema"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--xsd", type=str, required=True)
        parser.add_argument("files", type=str, nargs="+")

    def do(self, *args, **kwargs):  # noqa
        schema = etree.XMLSchema(file=kwargs["xsd"])
        failed = 0
        for filename in kwargs["files"]:
            with open(filename, "rb") as fp:
                content = fp.read()
                try:
                    parser = objectify.makeparser(schema=schema)
                    objectify.fromstring(content, parser)
                    print(f"{filename} OK")
                except Exception as exc:
                    print(f"{filename} failed to validate: {exc}")
                    failed += 1
        if failed:
            print("Exiting with 1")
            sys.exit(1)
