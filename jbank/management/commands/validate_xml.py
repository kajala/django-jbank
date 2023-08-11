import logging
from django.core.management.base import CommandParser
from jutil.command import SafeCommand
from lxml import etree, objectify  # noqa

logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Validates XML files against XSD schema"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--xsd", type=str, required=True)
        parser.add_argument("files", type=str, nargs="+")

    def do(self, *args, **kwargs):  # pylint: disable=too-many-locals,too-many-branches
        schema = etree.XMLSchema(file=kwargs["xsd"])
        for filename in kwargs["files"]:
            with open(filename, "rb") as fp:
                content = fp.read()
                try:
                    parser = objectify.makeparser(schema=schema)
                    objectify.fromstring(content, parser)
                    print(f"{filename} OK")
                except Exception as exc:
                    print(f"{filename} failed to validate: {exc}")
