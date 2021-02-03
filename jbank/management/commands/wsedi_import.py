# pylint: disable=too-many-locals,logging-format-interpolation,too-many-branches
import json
import logging
import os
import zipfile
from random import randint
from django.conf import settings
from django.core.management.base import CommandParser
from django.utils.timezone import now
from jutil.command import SafeCommand
from jbank.models import WsEdiConnection


logger = logging.getLogger(__name__)


class Command(SafeCommand):
    help = "Import WS-EDI connection"

    def add_arguments(self, parser: CommandParser):
        parser.add_argument("file", type=str)
        parser.add_argument("--verbose", action="store_true")

    def do(self, *args, **options):
        zf = zipfile.ZipFile(options["file"])
        ws_data = {}
        today = now()

        for filename in zf.namelist():
            assert isinstance(filename, str)
            if options["verbose"]:
                print("Importing {}".format(filename))
            if filename.endswith(".json"):
                content = zf.read(filename)
                ws_data = json.loads(content.decode())

        pem_suffix = "-import-{}-{}.pem".format(today.date().isoformat(), randint(100, 999))
        for filename in zf.namelist():
            assert isinstance(filename, str)
            if filename.endswith(".pem"):
                content = zf.read(filename)
                new_filename = filename[:-4] + pem_suffix
                new_path = "certs/{}".format(new_filename)
                with open(os.path.join(settings.MEDIA_ROOT, new_path), "wb") as fp:
                    fp.write(content)  # pytype: disable=not-callable
                repl = []
                for k, v in ws_data.items():
                    if k.endswith("_file") and os.path.basename(v) == filename:
                        repl.append((k, new_path))
                for k, v in repl:
                    ws_data[k] = v

        if not ws_data:
            print("Nothing to import!")
            return
        if "created" in ws_data:
            del ws_data["created"]
        ws = WsEdiConnection.objects.create(**ws_data)
        logger.info("WsEdiConnection id={} created".format(ws.id))
