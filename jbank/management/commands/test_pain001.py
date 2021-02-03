import logging
import subprocess
from decimal import Decimal
from os.path import join
from django.conf import settings
from django.core.management.base import CommandParser
from jbank.sepa import Pain001
from jutil.command import SafeCommand
from jutil.format import format_xml, format_xml_bytes
from jutil.validators import iban_bic


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = """
        Generates pain.001.001.03 compatible SEPA payment file.
        """

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("--verbose", action="store_true")
        parser.add_argument("--validate", action="store_true")

    def do(self, *args, **options):
        debtor_acc = "FI4947300010416310"
        p = Pain001(
            "201802071211XJANITEST",
            "Kajala Group Oy",
            debtor_acc,
            iban_bic(debtor_acc),
            "020840699",
            ["Koukkukankareentie 29", "20320 Turku"],
            "FI",
        )
        creditor_acc = "FI8847304720017517"
        p.add_payment(
            "201802071339A0001", "Jani Kajala", creditor_acc, iban_bic(creditor_acc), Decimal("49.00"), "vuokratilitys"
        )
        xml_str = format_xml_bytes(p.render_to_bytes()).decode()
        print(xml_str)

        filename = "/tmp/pain001.xml"
        with open(filename, "wt") as fp:
            fp.write(xml_str)
            print(filename, "written")

        if options["validate"]:
            # /usr/bin/xmllint --format --pretty 1 --load-trace --debug --schema $1 $2
            res = subprocess.run(
                [
                    "/usr/bin/xmllint",
                    "--format",
                    "--pretty",
                    "1",
                    "--load-trace",
                    "--debug",
                    "--schema",
                    join(settings.BASE_DIR, "data/pain001/pain.001.001.03.xsd"),
                    filename,
                ]
            )
            if res.returncode == 0:
                print("OK")
            else:
                print("FAIL")
